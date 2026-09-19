# -*- coding: utf-8 -*-
"""Phase D2 (30.md §4/§5) — Scene Reference A/B harness。

16 代表镜头 × {Mode A: full, Mode B: context_only} × 2 候选，全部走真实
build_bound_first_frame_input（同一 pack / 同一 creative intent，唯一变量 = 场景图
是否进入 conditioning），Agnes 2K 真机生成，产物 + 请求元数据落 _phase_d2_ab/。

纪律：
- 不写 project canonical（候选不进 shot.first_frame_batches——A/B 是实验，不是正式候选）；
- 不直接改 provider prompt（prompt 全部出自 adapter 真实组装）；
- reference budget（D2 新策略）对 A/B 两模式同等生效；
- Mode A = full（场景图 refs[0]），Mode B = context_only（场景语义仍在 prompt）。
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

SHOTS = [
    # (shot_id, category rationale)
    ("s09_c01", "EWS/high-angle 单人"),
    ("s13_c01", "EWS 3 人"),
    ("s14_c01", "WS 2 人"),
    ("s18_c08", "WS 3 人"),
    ("s09_c02", "MS 单人（下马入院）"),
    ("s10_c01", "MS 3 人"),
    ("s15_c01", "MS 4 人（幻觉路人案发镜）"),
    ("s13_c03", "MS 单人"),
    ("s09_c07", "MCU"),
    ("s14_c05", "MCU low-angle 对白"),
    ("s10_c02", "CU+OTS（全片唯一显式 OTS）"),
    ("s17_c03", "CU 单人对白"),
    ("s16_c02", "CU 2 人"),
    ("s17_c02", "ECU overhead（克隆案发镜）"),
    ("s18_c06", "ECU 道具细节"),
    ("s11_c03", "CU 反应对白"),
]
VARIANTS = 2
CONCURRENCY = 3          # 尊重会员档 RPM 16 配置（Commit B4），不过度并发


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def main() -> int:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    # .env → os.environ（api_server 由 dotenv 负责；harness 手工加载，APIS 走 ${AGNES_API_KEY} 替换）
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                import os
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    from src.core.store import ProjectStore
    from src.core.models import OpeningSource  # noqa: F401
    from src.generation.image_adapter import build_bound_first_frame_input
    from src.pipeline.controller import PipelineController

    ctrl = PipelineController(str(ROOT / "config" / "config.yaml"))
    client = ctrl.agnes_client
    project = ProjectStore(PROJ).load()
    style = project.style
    size_tier = style.size_tier or "2K"
    ratio = style.aspect_ratio or "16:9"

    runs = []
    tasks = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async def gen_one(shot_id: str, mode: str, variant: int):
        shot = project.get_shot(shot_id)
        req = build_bound_first_frame_input(PROJ, project, shot,
                                            scene_reference_mode=mode)
        dest = OUT / shot_id / f"{mode}_c{variant}.png"
        label = f"{shot_id}/{mode}/c{variant}"
        async with sem:
            res = await client.generate_image(prompt=req.prompt,
                                              image_refs=req.image_refs,
                                              size_tier=size_tier, ratio=ratio)
        url, b64 = res.get("url"), res.get("b64")
        if not url and not b64:
            raise RuntimeError(f"{label}: 无 url/b64")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if b64:
            import base64 as _b64
            dest.write_bytes(_b64.b64decode(b64))
        if url:
            await client.download(url, dest)
        rec = {
            "shot_id": shot_id, "mode": mode, "variant": variant,
            "file": str(dest.relative_to(ROOT)), "sha256": sha256_of(dest),
            "scene_reference_mode": req.scene_reference_mode,
            "n_refs": len(req.image_refs),
            "sent_sequence": [r.get("source_type") for r in
                              (req.semantic_roles or [])],
            "used_reference_ids": req.used_reference_ids,
            "dropped": req.dropped_optional,
            "prompt": req.prompt,
            "basis": req.basis, "provider_url": url,
        }
        print(f"  done {label} refs={rec['n_refs']} seq={rec['sent_sequence']}")
        return rec

    # 先编译全部请求（fail fast：任何镜编译失败立即结构化报错，不烧一半额度）
    preflight = []
    for shot_id, why in SHOTS:
        shot = project.get_shot(shot_id)
        for mode in ("full", "context_only"):
            req = build_bound_first_frame_input(PROJ, project, shot,
                                                scene_reference_mode=mode)
            preflight.append({"shot_id": shot_id, "mode": mode,
                              "n_refs": len(req.image_refs),
                              "sent_sequence": [r.get("source_type")
                                                for r in req.semantic_roles]})
    print("preflight:")
    for p in preflight:
        print(f"  {p['shot_id']} {p['mode']}: refs={p['n_refs']} {p['sent_sequence']}")

    for shot_id, why in SHOTS:
        for mode in ("full", "context_only"):
            for v in range(1, VARIANTS + 1):
                tasks.append(gen_one(shot_id, mode, v))

    print(f"generating {len(tasks)} images ({size_tier}, {ratio}) ...")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok, failed = [], []
    for r in results:
        (ok if isinstance(r, dict) else failed).append(r)
    for f in failed:
        print("FAILED:", repr(f))

    runs = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "size_tier": size_tier, "ratio": ratio,
            "shots": [{"shot_id": s, "rationale": w} for s, w in SHOTS],
            "preflight": preflight,
            "candidates": ok, "failures": [repr(f) for f in failed]}
    (OUT / "ab_runs.json").write_text(json.dumps(runs, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(f"ok={len(ok)} failed={len(failed)} elapsed={time.time()-t0:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
