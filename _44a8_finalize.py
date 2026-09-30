# -*- coding: utf-8 -*-
"""Phase 44A 收尾：s02_c03 正式定版（用户终选 vv_49e4bd19）。

流程（44A 受管副本纪律——目标版本非该镜最新代，共享文件里是其它候选字节）：
  1) provider URL 下载 → 版本后缀受管副本 → sha256 + ffprobe（音轨实测）；
  2) 改 vv.local → set_final_video_business（指针+video 槽同步+从正确字节重提末帧）；
  3) 复核全 13 镜 final 表。
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
TARGET = "s02_c03"
FINAL = "vv_49e4bd19"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


async def main() -> int:
    import os
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
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
    proj = store.load()
    sh = proj.get_shot(TARGET)
    vv = next(v for v in sh.video_versions if v.version_id == FINAL)
    dst = pdir / "output" / "videos" / f"{TARGET}__{FINAL}.mp4"
    if not dst.exists():
        req = urllib.request.Request(vv.url, headers={"User-Agent": "phase44a/1.0"})
        with urllib.request.urlopen(req, timeout=90) as r, open(dst, "wb") as f:
            while True:
                c = r.read(1 << 20)
                if not c:
                    break
                f.write(c)
    digest = sha256(dst)
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_streams", "-show_format", str(dst)],
                       capture_output=True, encoding="utf-8", errors="replace")
    d = json.loads(r.stdout or "{}")
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
    print(f"managed copy: {dst.name} sha256={digest[:16]}… dur="
          f"{d.get('format', {}).get('duration')} audio={bool(a)} ({a.get('codec_name')})")
    rel = str(dst.relative_to(ROOT)).replace("/", "\\")
    store.update_shot(TARGET, lambda s, r_=rel: setattr(
        next(x for x in s.video_versions if x.version_id == FINAL), "local", r_))
    res = await set_final_video_business(store, pdir, TARGET, FINAL)
    print("set_final:", res)
    proj = store.load()
    sh = proj.get_shot(TARGET)
    ef = sh.video_end_frame
    print(f"{TARGET}: final={sh.final_video} | video.local={sh.video.local} "
          f"| end_src={sh.video_end_source_version} | end_ready={ef.url_ready} "
          f"| end_url={str(ef.url or '')[:80]}")
    print("==== 全 13 镜 final 表")
    for sc in proj.scenes or []:
        for s in (sc.shots or []):
            print(f"  {s.shot_id}: {s.final_video}")
    (ROOT / "_44a" / "a4_setfinal_49e4bd19.json").write_text(json.dumps({
        "shot": TARGET, "final": FINAL, "managed_copy": str(dst.relative_to(ROOT)),
        "sha256": digest, "probe_duration": d.get("format", {}).get("duration"),
        "audio": {"present": bool(a), "codec": a.get("codec_name"),
                  "note": "用户听审判定甲-2 无背景音；dB 见报告（选片以听审为准）"},
        "end_frame": {"local": ef.local, "url": ef.url,
                      "source_version": sh.video_end_source_version}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
