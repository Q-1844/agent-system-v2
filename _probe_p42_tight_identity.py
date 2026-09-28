# -*- coding: utf-8 -*-
"""42.md §四 P3：特写镜身份锚定三臂对照（2026-09-29）。

背景（42.md §3 根因）：D2 auto 规则把 medium close-up 的 Look 静默 drop
（identity 走文本）→ s01_c04/s02_c02 首帧纯文本重造 → 偏差链根。31.md §19
悬案：auto→crop 翻转待 A/B 证据。

三臂（同一镜 s02_c02，同一 binding，同一 prompt 编译口径）：
  A（drop，现状）：12:02 批次根帧——已存在（ff_cb6f94a5_1），零费用。
  B（crop）：human_reference_mode=crop（Look 头肩派生 + scene context_only）。
  C（keep）：human_reference_mode=keep（全幅 Look + scene context_only）。
判定：身份/画风对 Look 的保真 vs 构图景别（D2 关切=参考锚死构图）。
费用：2 次图片生成（图片侧，非视频配额）。
证据：_video25_probe/p3_b.* / p3_c.*（compiled + 图 + provenance）。
"""
import sys, os, json, asyncio, subprocess
sys.path.insert(0, ".")

from _probe_video25 import _load_cfg, _dump


async def main():
    from src.core.store import ProjectStore
    from src.generation.image_adapter import build_bound_first_frame_input
    from src.generation.agnes_adapter import compile_agnes_image_call

    PROJECT, SHOT = "雨灯归航", "s02_c02"
    pdir = os.path.join("projects", PROJECT)
    store = ProjectStore(pdir)
    project = store.load()
    shot = project.get_shot(SHOT)
    assert shot is not None
    print(f"shot={SHOT} size={shot.shot_size!r}")

    base, key, models = _load_cfg()
    models["image"] = "agnes-image-2.1-flash"   # config/config.yaml models.image
    import httpx
    headers = {"Authorization": f"Bearer {key}"}

    async def gen(label, human_mode):
        bound = build_bound_first_frame_input(
            pdir, project, shot,
            scene_reference_mode="context_only",
            human_reference_mode=human_mode)
        req = bound.to_generation_request(generation_type="first_frame",
                                          size_tier="1K", ratio="16:9")
        call = compile_agnes_image_call(req)
        body = {"model": models["image"], "prompt": call.prompt,
                "size": "1312x736", "extra_body": {"response_format": "url"}}
        if call.image_refs:
            body["extra_body"]["image"] = call.image_refs
        _dump(f"{label}.compiled.json", {
            "human_mode_requested": human_mode,
            "human_mode_resolved": bound.human_reference_mode,
            "scene_mode_resolved": bound.scene_reference_mode,
            "ref_layout": bound.ref_layout,
            "used_reference_ids": bound.used_reference_ids,
            "dropped_optional": [
                {"source_id": d.get("source_id"), "reason": d.get("reason")}
                for d in bound.dropped_optional],
            "n_refs": len(call.image_refs or [])})
        async with httpx.AsyncClient(base_url=base, headers=headers,
                                     timeout=httpx.Timeout(300, connect=30)) as c:
            r = await c.post("/v1/images/generations", json=body)
            print(f"  [{label}] images -> {r.status_code}")
            r.raise_for_status()
            data = r.json()
        url = data["data"][0]["url"]
        img = os.path.join("_video25_probe", f"{label}.png")
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            rr = await c.get(url)
            rr.raise_for_status()
            with open(img, "wb") as f:
                f.write(rr.content)
        print(f"    saved {img}")
        return img

    for label, mode in (("p3_b", "crop"), ("p3_c", "keep")):
        await gen(label, mode)
    print("P3 probe done（A 臂=现役根帧 ff_cb6f94a5_1，零费用）")


if __name__ == "__main__":
    asyncio.run(main())
