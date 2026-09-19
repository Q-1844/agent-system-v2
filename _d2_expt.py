# -*- coding: utf-8 -*-
"""Phase D2 §5.2 — 紧景别无参考实验（Mode C'）：CU/ECU + context_only + 0 人形参考。

假设：全身白底 look 立绘作为 i2i 参考时把构图锚定在「全身+环境」——去掉人形参考
（identity 走 prompt 文本），紧景别遵从应显著改善。对照 = 同 prompt 常规生成。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PROJ = ROOT / "projects" / "Golden_Full_Production_V1"
OUT = ROOT / "_phase_d2_ab"

SHOTS = ["s09_c03", "s17_c03", "s18_c06", "s11_c03"]
VARIANTS = 2


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        import os
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def main() -> int:
    t0 = time.time()
    load_env()
    from src.core.store import ProjectStore
    from src.core.prompt_builder import build_first_frame_prompt
    from src.shot_binding import ShotBindingService
    from src.generation.image_adapter import (
        _gate_ready_pack, filter_first_frame_references, _apply_reference_budget,
        _resolve_media_loop, _style_instruction_from_pack)
    from src.resources.store import ResourceStore
    from src.pipeline.controller import PipelineController

    ctrl = PipelineController(str(ROOT / "config" / "config.yaml"))
    client = ctrl.agnes_client
    project = ProjectStore(PROJ).load()
    rdoc = ResourceStore(PROJ, PROJ.name).load()

    recs = []
    for sid in SHOTS:
        shot = project.get_shot(sid)
        svc = ShotBindingService(PROJ, PROJ.name)
        pack = _gate_ready_pack(svc, sid)
        eligible, _excl = filter_first_frame_references(pack.get("references") or [])
        dropped = []
        kept, mode = _apply_reference_budget(eligible, shot=shot,
                                             scene_reference_mode="context_only",
                                             dropped=dropped)
        # Mode C'：全部参考都不发（人形与场景）——identity 走 prompt 文本
        from src.core.prompt_builder import build_first_frame_prompt as _b
        creative = _b(project, shot, style_suffix="")
        ctx = _production_context_text(pack, rdoc)
        style = _style_instruction_from_pack(pack)
        prompt = f"{creative}, {ctx} {style}".strip()
        for v in range(1, VARIANTS + 1):
            dest = OUT / sid / f"noref_c{v}.png"
            res = await client.generate_image(prompt=prompt, image_refs=None,
                                              size_tier="2K",
                                              ratio=project.style.aspect_ratio or "16:9")
            url, b64 = res.get("url"), res.get("b64")
            if not url and not b64:
                raise RuntimeError("no url/b64")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if b64:
                import base64 as _b64
                dest.write_bytes(_b64.b64decode(b64))
            else:
                await client.download(url, dest)
            recs.append({"shot_id": sid, "variant": v, "file": str(dest.relative_to(ROOT)),
                         "sha256": sha256_of(dest), "n_refs": 0, "prompt": prompt})
            print(f"  done {sid} noref_c{v}", flush=True)
            await asyncio.sleep(45)

    (OUT / "noref_runs.json").write_text(json.dumps(recs, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    print(f"elapsed={time.time()-t0:.0f}s candidates={len(recs)}")
    return 0


def _production_context_text(pack, rdoc):
    from src.generation.image_adapter import _production_context_text as _p
    return _p(pack, rdoc, "first")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
