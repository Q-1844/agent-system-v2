# -*- coding: utf-8 -*-
"""Phase 44A 媒体取证（只读 + c06 受管修复）：
1) 75056b51d6c77c06 尾帧归属：与 1c4b7001 / 0e7fe39f 的 URL 视频逐帧提取末帧
   （与 _extract_video_end_frame 同参数）做字节比对——验证覆盖写危害是否把
   0e7fe39f 的尾帧错标成了 1c4b7001 的末帧（进而成为第四轮 c03 接力源）。
2) s01_c06 final=vv_5ad9192d 非最新代（共享文件=6d3b05db 字节）——按回拨同
   流程下载受管副本→改 vv.local→set-final 重提末帧（指针不变，仅修媒体真相）。
3) 全 13 镜 final：URL 下载 → sha256 与登记 local 比对 → 可用性表（冻结输入）。
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
OUTDIR = ROOT / "_44a"
OUTDIR.mkdir(exist_ok=True)
WORK = OUTDIR / "media_audit"
WORK.mkdir(exist_ok=True)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def fetch(url: str, dst: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "phase44a/1.0"})
        with urllib.request.urlopen(req, timeout=90) as r, open(dst, "wb") as f:
            while True:
                c = r.read(1 << 20)
                if not c:
                    break
                f.write(c)
        return dst.stat().st_size > 0
    except Exception as e:
        print(f"  fetch fail {url[:70]}: {e}")
        return False


def extract_tail(video: Path, dst_png: Path) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-0.2", "-i", str(video),
         "-frames:v", "1", "-q:v", "2", str(dst_png)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    return r.returncode == 0 and dst_png.exists()


def to_jpg_q88(p: Path) -> Path:
    from PIL import Image
    jpg = p.with_suffix(".jpg")
    Image.open(p).convert("RGB").save(jpg, "JPEG", quality=88)
    return jpg


async def main():
    import os
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    raw = json.loads((ROOT / "projects" / PROJ / "output" / "project.json")
                     .read_text(encoding="utf-8"))
    shots = {}
    for sc in raw.get("scenes", []):
        for sh in sc.get("shots", []):
            shots[sh["shot_id"]] = sh

    report = {"tail_forensics": {}, "finals_media": {}}

    # ---- 1) 尾帧归属取证 ----
    obj = WORK / "75056b51d6c77c06.jpg"
    if fetch("https://Q-1844.github.io/agnes-frame-host/objects/75056b51d6c77c06.jpg", obj):
        h_obj = sha256(obj)
        print(f"object 75056b51d6 sha256={h_obj[:16]}…")
        report["tail_forensics"]["object"] = h_obj
        for vid in ("vv_1c4b7001", "vv_0e7fe39f"):
            vv = next(v for v in shots["s02_c02"]["video_versions"]
                      if v["version_id"] == vid)
            mp4 = WORK / f"s02_c02__{vid}.mp4"
            if not mp4.exists():
                fetch(vv["url"], mp4)
            png = WORK / f"tail_{vid}.png"
            if extract_tail(mp4, png):
                jpg = to_jpg_q88(png)
                h = sha256(jpg)
                same = (h == h_obj)
                print(f"  tail({vid}) sha256={h[:16]}… == object? {same}")
                report["tail_forensics"][vid] = {"tail_sha256": h, "matches_object": same}

    # ---- 2) s01_c06 受管修复 ----
    import deps
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(ROOT / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from routers.shots import set_final_video_business
    from src.core.store import ProjectStore
    pdir = deps.project_manager.get_project_dir(PROJ).resolve()
    store = ProjectStore(pdir)
    proj = store.load()
    sh = proj.get_shot("s01_c06")
    vv = next(v for v in sh.video_versions if v.version_id == "vv_5ad9192d")
    dst = pdir / "output" / "videos" / "s01_c06__vv_5ad9192d.mp4"
    if not dst.exists():
        if not fetch(vv.url, dst):
            raise SystemExit("s01_c06 vv_5ad9192d 下载失败——阻断")
    digest = sha256(dst)
    shared = ROOT / "projects" / "雨灯归航" / "output" / "videos" / "s01_c06.mp4"
    h_shared = sha256(shared)
    print(f"s01_c06: managed={digest[:16]}… shared(6d3b05db bytes)={h_shared[:16]}… "
          f"same={digest == h_shared}")
    report["finals_media"]["s01_c06"] = {
        "version": "vv_5ad9192d", "managed_copy": str(dst.relative_to(ROOT)),
        "sha256": digest, "shared_file_sha256": h_shared,
        "managed_matches_shared": digest == h_shared, "source_url": vv.url}
    rel = str(dst.relative_to(ROOT)).replace("/", "\\")
    store.update_shot("s01_c06", lambda s: setattr(
        next(x for x in s.video_versions if x.version_id == "vv_5ad9192d"),
        "local", rel))
    r = await set_final_video_business(store, pdir, "s01_c06", "vv_5ad9192d")
    proj = store.load()
    sh = proj.get_shot("s01_c06")
    print(f"s01_c06 repaired: final={sh.final_video} end_src={sh.video_end_source_version} "
          f"end_url={str(sh.video_end_frame.url or '')[:80]}")

    # ---- 3) 全 13 镜 final 媒体核验 ----
    raw = json.loads((pdir / "output" / "project.json").read_text(encoding="utf-8"))
    shots = {sh["shot_id"]: sh for sc in raw.get("scenes", [])
             for sh in sc.get("shots", [])}
    for sid in sorted(shots, key=lambda x: (x[:3], x)):
        sh = shots[sid]
        fv = sh["final_video"]
        vv = next(v for v in sh["video_versions"] if v["version_id"] == fv)
        entry = {"version": fv, "source_url": vv.get("url"), "local": vv.get("local")}
        mp4 = WORK / f"{sid}__{fv}.mp4"
        if not mp4.exists():
            ok = fetch(vv.get("url") or "", mp4)
            entry["url_fetch_ok"] = ok
            if not ok:
                print(f"{sid}: ⚠ URL 取不到——按 44A §A3 阻断正式快照")
                entry["sha256"] = None
                report["finals_media"][sid] = entry
                continue
        entry["sha256"] = sha256(mp4)
        local_path = ROOT / vv.get("local")
        entry["local_exists"] = local_path.exists()
        entry["local_sha256"] = sha256(local_path) if local_path.exists() else None
        entry["local_is_same_bytes"] = (entry["sha256"] == entry["local_sha256"])
        flag = "OK" if entry["local_is_same_bytes"] else "⚠ local≠URL"
        print(f"{sid} {fv}: url_sha={entry['sha256'][:12]}… local_sha="
              f"{(entry['local_sha256'] or '')[:12]}… {flag}")
        report["finals_media"][sid] = entry

    (OUTDIR / "a1_media_forensics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved -> _44a/a1_media_forensics.json")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
