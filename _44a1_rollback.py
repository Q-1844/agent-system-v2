# -*- coding: utf-8 -*-
"""Phase 44A1：两笔正式回拨（用户最新裁定 FEEDBACK_repair.json repair43_v3 层）：
- s01_c04: vv_d90ff290（三轮未点亮对照，生成即 final 前移）→ vv_80dc90a5（一轮候选）
- s02_c02: vv_1c4b7001（二轮 seed42）→ vv_4bdf6b08（一轮候选）

🔴 与 Phase 43 流程的差异（44A 任务书 §A3：不以磁盘同名文件顶替）：
VideoVersion.local 覆盖写——共享路径里装的是该镜最新代字节。回拨目标版本必须
先按 provider URL 下载到版本后缀的受管副本、校验 ffprobe 可读，再改 vv.local，
最后走 set_final_video_business（指针+video 槽同步+末帧重提，重提读到的才是
回拨目标自己的字节）。不覆盖共享路径（保留被撤下版本的磁盘字节）。
"""
import asyncio
import hashlib
import io
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PROJ = "雨灯归航"
ROLLBACKS = [
    ("s01_c04", "vv_80dc90a5"),
    ("s02_c02", "vv_4bdf6b08"),
]
OUTDIR = ROOT / "_44a"
OUTDIR.mkdir(exist_ok=True)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def fetch(url: str, dst: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "phase44a/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        return dst.stat().st_size > 0
    except Exception as e:
        print(f"  download failed: {e}")
        return False


def ffprobe(p: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(p)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0:
        return {"error": (r.stderr or "")[-300:]}
    d = json.loads(r.stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {"duration": float(d.get("format", {}).get("duration") or 0),
            "video_codec": v.get("codec_name"), "size": v.get("width", 0), "height": v.get("height", 0),
            "fps": v.get("avg_frame_rate") or v.get("r_frame_rate"),
            "has_audio": bool(a), "audio_codec": a.get("codec_name")}


def shot_versions(p):
    for sc in p.get("scenes", []):
        for sh in sc.get("shots", []):
            yield sh.get("shot_id"), sh


async def main():
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                import os
                os.environ.setdefault(k.strip(), v.strip())

    import deps
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(ROOT / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from routers.shots import set_final_video_business
    from src.core.store import ProjectStore
    pdir = deps.project_manager.get_project_dir(PROJ).resolve()
    store = ProjectStore(pdir)

    # ---- BEFORE 快照（回拨证据基线）----
    raw_before = json.loads((pdir / "output" / "project.json").read_text(encoding="utf-8"))
    (OUTDIR / "a1_before_project.json").write_text(
        json.dumps(raw_before, ensure_ascii=False, indent=1), encoding="utf-8")

    audit = {}
    for sid, vid in ROLLBACKS:
        print(f"==== {sid} -> {vid}")
        proj = store.load()
        shot = proj.get_shot(sid)
        vv = next((v for v in (shot.video_versions or []) if v.version_id == vid), None)
        if vv is None:
            raise SystemExit(f"{sid} 版本 {vid} 不存在——中止（不执行任何回拨）")
        print(f"  url: {str(vv.url)[:100]}")
        dst = pdir / "output" / "videos" / f"{sid}__{vid}.mp4"
        if not dst.exists() or dst.stat().st_size == 0:
            ok = fetch(vv.url, dst)
            if not ok:
                raise SystemExit(f"{sid} {vid} 媒体下载失败——按 44A §A3 阻断正式快照，不回拨")
        else:
            print("  (受管副本已存在，复用)")
        digest = sha256(dst)
        probe = ffprobe(dst)
        print(f"  managed copy: {dst.name} | sha256={digest[:16]}… | probe={probe}")
        if probe.get("error") or not probe.get("duration"):
            raise SystemExit(f"{sid} {vid} ffprobe 失败——阻断")
        rel = str(dst.relative_to(ROOT)).replace("/", "\\")
        # 更新 vv.local -> 受管副本（OCC）
        store.update_shot(sid, lambda s, v=vid, r=rel: setattr(
            next(x for x in s.video_versions if x.version_id == v), "local", r))
        # 正式回拨（指针 + video 槽同步 + 末帧重提）
        r = await set_final_video_business(store, pdir, sid, vid)
        print(f"  set_final ok={r.get('ok', True)} final={r.get('final_video')}")
        audit[sid] = {"version": vid, "managed_copy": str(dst.relative_to(ROOT)),
                      "sha256": digest, "probe": probe, "source_url": vv.url}

    # ---- AFTER 复核 ----
    (OUTDIR / "a1_media_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")
    raw_after = json.loads((pdir / "output" / "project.json").read_text(encoding="utf-8"))
    (OUTDIR / "a1_after_project.json").write_text(
        json.dumps(raw_after, ensure_ascii=False, indent=1), encoding="utf-8")
    proj = store.load()
    print("==== 复核")
    for sid, vid in ROLLBACKS:
        sh = proj.get_shot(sid)
        ef = sh.video_end_frame
        print(f"{sid}: final={sh.final_video} | video.local={sh.video.local} "
              f"| end_src_ver={sh.video_end_source_version} | end_ready={ef.url_ready} "
              f"| end_url={str(ef.url or '')[:80]}")
    # 全镜 final 表
    for sc_i, sc in enumerate(proj.scenes or []):
        for sh in (sc.shots or []):
            print(f"  {sh.shot_id}: final={sh.final_video}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
