# -*- coding: utf-8 -*-
"""42.md §四 P1：Look+尾帧双参考 relay 真机探针（2026-09-29）。

裁决问题：relay 镜双参考（上镜尾帧=衔接 + hero Look=身份/画风再锚定）能否让
偏差链镜头画风向 Look 回正、同时保持空间连续？——对照基准=同镜现役
plain-relay 版本（偏差组风格，42.md §3 审计）。

纪律：
- canonical 不落盘：哨兵 prev_tail_look 只在内存对象上设置（探针不冒充
  正式决策/不伪造选片历史）；若视觉验收通过，正式采用走系统 Agent（盲测/用户批准）。
- 请求经生产编译器（build_bound_video_input → to_generation_request →
  compile_agnes_video_call），与正式路径同源——验证的是真实请求形状。
- 证据落 _video25_probe/p1.*（compiled request + submit/final + 帧）。
费用：5s（Token Plan 500s/天）。
"""
import sys, os, json, asyncio, subprocess
sys.path.insert(0, ".")
import copy as _copy
import httpx

from _probe_video25 import _load_cfg, submit, poll, _dump

PROJECT = "雨灯归航"
SHOT = "s02_c03"          # 偏差链第一消费镜（现役=plain relay，偏差组风格）


def extract_frames(mp4: str, label: str):
    for tag, ss in (("first", "0.2"), ("early_middle", "1.5"), ("mid", "2.5"),
                    ("late_middle", "3.6"), ("last", "4.8")):
        out = os.path.join("_video25_probe", f"{label}_frame_{tag}.jpg")
        rc = subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", mp4,
                             "-frames:v", "1", "-q:v", "3", out],
                            capture_output=True, text=True)
        print(f"    frame {tag}: rc={rc.returncode}")


async def main():
    from src.core.store import ProjectStore
    from src.generation.video_adapter import (
        build_bound_video_input, VIDEO_REFERENCE_RELAY_IDENTITY)
    from src.generation.agnes_adapter import compile_agnes_video_call

    pdir = os.path.join("projects", PROJECT)
    store = ProjectStore(pdir)
    project = store.load()
    shot = project.get_shot(SHOT)
    assert shot is not None and shot.link_mode == "multi"
    print(f"shot={SHOT} vref={shot.video_reference_asset_id} "
          f"source={shot.source_shot_id}")

    # 哨兵只在内存设置（canonical 不落盘）
    shot.video_reference_asset_id = VIDEO_REFERENCE_RELAY_IDENTITY

    req = build_bound_video_input(pdir, project, shot)
    print("provider_mode:", req.provider_mode)
    print("video_reference(尾帧):", req.video_reference)
    print("identity_reference(Look):", json.dumps(req.identity_reference, ensure_ascii=False))
    print("used_reference_ids:", req.used_reference_ids)
    reasons = [d["reason"] for d in req.diagnostics]
    print("diagnostics:", reasons)
    assert "relay_identity_reference_applied" in reasons
    assert "<Picture 1>" in req.prompt and "<Picture 2>" in req.prompt

    gen = req.to_generation_request(model="agnes-video-2.5-flash")
    call = compile_agnes_video_call(gen)
    slots = call.slots()
    print("compiled slots:", {k: v for k, v in slots.items() if k != "prompt"})
    _dump("p1.compiled.json", {
        "shot_id": SHOT, "sentinel": VIDEO_REFERENCE_RELAY_IDENTITY,
        "video_reference": req.video_reference,
        "identity_reference": req.identity_reference,
        "used_reference_ids": req.used_reference_ids,
        "slots": {k: v for k, v in slots.items() if k != "prompt"},
        "prompt": call.prompt,
        "note": "探针：canonical 未改（哨兵仅内存）；请求经生产编译器"})

    base, key, models = _load_cfg()
    body = {"model": "agnes-video-2.5-flash", "mode": "reference",
            "prompt": call.prompt, "seconds": "5",
            "images": slots["image_urls"], "seed": 42}
    ok, data = await submit(base, key, body, "p1")
    if not ok:
        print("SUBMIT REJECTED — 双参考请求形状被拒，见 p1.error.json")
        return
    vid = data.get("video_id") or data.get("task_id")
    final = await poll(base, key, vid, "agnes-video-2.5-flash", "p1")
    # poll 落 _video25_probe/p1.final.json
    fin = json.load(open("_video25_probe/p1.final.json", encoding="utf-8"))
    url = fin.get("url")
    print("final url:", url)
    if url:
        mp4 = os.path.join("_video25_probe", "p1.mp4")
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            with open(mp4, "wb") as f:
                f.write(r.content)
        extract_frames(mp4, "p1")
    print("P1 probe done")


if __name__ == "__main__":
    asyncio.run(main())
