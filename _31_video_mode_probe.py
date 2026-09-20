# -*- coding: utf-8 -*-
"""31.md 视频模式探针——回答用户假设：
「不设首帧、只生成视频、把人物和背景作为参考加进去，会不会更自然？」

同一镜（s17_c03）、同一 prompt（既有 i2v 视频的编译结果原文）、同参数，三种条件：
  A i2v      （已有：_31_integration_e2e/mitigate/s17_c03_video_auto.mp4）
  B t2v      纯文本（无任何图）——client mode="t2v"
  C multi    多图参考指导（人物 look 公网 URL + 场景 SIV 公网 URL，无首帧）——
             client mode="multi"（FACTS §3.2D 形态，客户端已实现但从未真机验证；
             400/失败也是有效答案，如实记录）

探针直调 client（不经 handler——handler 不暴露 t2v/multi，preflight 也拦无首帧）。
产物落 _31_integration_e2e/mitigate/s17_c03_probe_*.mp4 + MANIFEST，Gallery 重建成对比行。
"""
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import _31_e2e_driver as d


async def probe():
    d.load_env()
    client = d.setup_clients()
    d.bind_pm(d.GOLDEN)
    from src.core.store import ProjectStore
    from src.shot_binding import ShotBindingService
    from src.resources.store import ResourceStore

    shot_id = "s17_c03"
    store = ProjectStore(d.GOLDEN)
    shot = store.load().get_shot(shot_id)
    # 既有 i2v 视频的编译 prompt（同窗公平对比）
    manifest = json.loads((d.E2E / "MANIFEST.json").read_text(encoding="utf-8"))
    base = next(e for e in manifest if e.get("shot_id") == shot_id
                and e.get("variant") == "video_auto")
    prompt = base["prompt"]
    nf, fr = shot.video.num_frames, shot.video.frame_rate
    print(f"probe {shot_id}: num_frames={nf} fr={fr} prompt={len(prompt)} chars", flush=True)

    # multi 参考公网 URL：每角色 look hero + resolved SIV
    rdoc = ResourceStore(d.GOLDEN, d.GOLDEN.name).load()
    svc = ShotBindingService(d.GOLDEN)
    validity = svc.read_validity(shot_id)
    siv_id = (validity.get("resolved_dependencies") or {}).get("scene_instance_view_id")
    from src.scene_system.store import SceneSystemStore
    sdoc = SceneSystemStore(d.GOLDEN, d.GOLDEN.name).load()
    siv_asset = rdoc.reference_assets[
        sdoc.scene_instance_views[siv_id].reference_asset_id]
    ref_urls = [siv_asset.url]
    from test_shot_binding_s3b1 import LOOK  # noqa: F401（仅示意，下面按 binding 取）
    b = svc.read_binding(shot_id)["binding"]
    for cb in b["character_bindings"]:
        look = rdoc.looks[cb["look_id"]]
        hero = look.approved_reference_asset_ids[0]
        ref_urls.insert(0, rdoc.reference_assets[hero].url)   # 人物在前
    ref_urls = [u for u in ref_urls if u]
    print("multi refs:", [u[:60] for u in ref_urls], flush=True)

    entries = []

    async def run_one(tag, **kw):
        sink = []
        emit = lambda *a, **k: d._emit(sink, *a, **k)
        try:
            created = await client.create_video(
                prompt, num_frames=nf, frame_rate=fr, width=1280, height=704, **kw)
            vid = created.get("video_id") or created.get("task_id")
            print(f"  [{tag}] submitted {vid}", flush=True)
            res = await client.poll_video(vid, interval=5, on_progress=(
                lambda pct, st, waited: print(f"  [{tag}] {st} {pct}% {int(waited)}s",
                                              flush=True)))
            dest = d.E2E / "mitigate" / f"{shot_id}_probe_{tag}.mp4"
            await client.download(res["url"], dest)
            print(f"  [{tag}] done → {dest.name} ({dest.stat().st_size} bytes)", flush=True)
            entries.append({
                "group": "mitigate", "shot_id": shot_id, "variant": f"{tag}_probe",
                "frame": "video", "file": str(dest), "provider_url": res["url"],
                "generation_basis": {"probe": tag, "prompt_chars": len(prompt),
                                     "num_frames": nf, "refs": kw.get("image_urls", [])},
                "prompt": prompt, "params": kw,
            })
        except Exception as e:   # noqa: BLE001 —— 探针失败也是答案（如实记录）
            print(f"  [{tag}] FAILED: {type(e).__name__}: {str(e)[:200]}", flush=True)
            entries.append({"group": "mitigate", "shot_id": shot_id,
                            "variant": f"{tag}_probe", "frame": "video",
                            "error": f"{type(e).__name__}: {str(e)[:300]}", "params": kw})
        await d._gap()

    await run_one("t2v", mode="t2v")
    await run_one("multi", mode="multi", image_urls=ref_urls)
    d._manifest_append(entries)
    print("probe done", flush=True)


if __name__ == "__main__":
    asyncio.run(probe())
