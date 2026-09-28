# -*- coding: utf-8 -*-
"""42.md §四 P4：错误端点「真解除 relay+独立重生」实证（2026-09-29）。

背景：Phase 41 报告把「解除 relay+强化描述+独立重生」列为 s03_c03 修复所用的
方法，但回执审计（42.md §1.2-1）证明 relay 实际未解除（修复=文本强化压过
语义参考）。本探针把该方法真实执行一遍，闭合端点三修法矩阵：
  修法 1 文本强化+保留 relay：Phase 41 实证（空间修复成功）。
  修法 2 真解除 relay+独立重生：本探针——无尾帧参考，独立生成首帧（强化
        description）+ i2v 硬首帧。
  修法 3 硬首帧+空间转移：Phase 41 探针实证（task_oRWiMOll）。
纪律：canonical 不落盘（vref 清除仅内存对象）；探针样本直接可看，正式采用走
系统 Agent。费用：1 图 + 1 视频（5s）。
证据：_video25_probe/p4.*。
"""
import sys, os, json, asyncio, subprocess
sys.path.insert(0, ".")
import copy as _copy
import httpx

from _probe_video25 import _load_cfg, submit, poll, _dump

PROJECT, SHOT = "雨灯归航", "s03_c03"


async def main():
    from src.core.store import ProjectStore
    from src.generation.image_adapter import build_bound_first_frame_input
    from src.generation.agnes_adapter import (
        compile_agnes_image_call, compile_agnes_video_call)
    from src.generation.video_adapter import build_bound_video_input

    pdir = os.path.join("projects", PROJECT)
    store = ProjectStore(pdir)
    project = store.load()
    shot = project.get_shot(SHOT)
    print(f"shot={SHOT} link_mode={shot.link_mode} vref={shot.video_reference_asset_id} "
          f"opening={shot.opening_source}")

    # ---- 步骤 1：真解除 relay（内存）+ 独立重生成首帧（强化 description）----
    shot2 = _copy.copy(shot)
    shot2.video_reference_asset_id = None
    shot2.opening_source = "independent"   # 不继承上镜末帧——独立起步
    bound = build_bound_first_frame_input(
        pdir, project, shot2,
        scene_reference_mode="full", human_reference_mode="keep")
    gen = bound.to_generation_request(generation_type="first_frame",
                                      size_tier="1K", ratio="16:9")
    icall = compile_agnes_image_call(gen)
    _dump("p4_ff.compiled.json", {
        "opening_source": "independent", "vref": None,
        "human_mode": bound.human_reference_mode,
        "scene_mode": bound.scene_reference_mode,
        "n_refs": len(icall.image_refs or []),
        "used_reference_ids": bound.used_reference_ids})
    base, key, models = _load_cfg()
    body = {"model": "agnes-image-2.1-flash", "prompt": icall.prompt,
            "size": "1312x736", "extra_body": {"response_format": "url"}}
    if icall.image_refs:
        body["extra_body"]["image"] = icall.image_refs
    async with httpx.AsyncClient(base_url=base,
                                 headers={"Authorization": f"Bearer {key}"},
                                 timeout=httpx.Timeout(300, connect=30)) as c:
        r = await c.post("/v1/images/generations", json=body)
        print(f"  [ff] images -> {r.status_code}")
        r.raise_for_status()
        ff_url = r.json()["data"][0]["url"]
    print("独立首帧:", ff_url)

    # ---- 步骤 2：i2v（硬首帧=独立首帧，无尾帧参考）----
    # 注：不消费 canonical 首帧槽（其现值=relay 同步记录，Phase 3 truth gate
    # first_frame_slot_not_hard_boundary 已在探针中实战命中拦截——守卫有效）。
    # 「独立重生」语义=用刚生成的独立首帧直接作 i2v 硬首帧。
    from src.core.prompt_builder import build_video_prompt
    from src.generation.request import GenerationRequest, ReferenceInput
    vprompt = build_video_prompt(shot, "i2v")
    greq = GenerationRequest(
        generation_type="video", shot_id=SHOT, prompt=vprompt,
        reference_inputs=[ReferenceInput(
            source_type="boundary_frame", semantic_role="first_slot",
            temporal_scope="start", media=ff_url)],
        first_frame=ff_url, model="agnes-video-2.5-flash",
        provenance={"provider_mode": "i2v"})
    vcall = compile_agnes_video_call(greq)
    slots = vcall.slots()
    _dump("p4_video.compiled.json", {
        "mode": "i2v", "first_frame": ff_url,
        "prompt_head": vcall.prompt[:200],
        "note": "真解除 relay：无尾帧参考；首帧=独立生成（强化 description+完整锚定）"})
    vbody = {"model": "agnes-video-2.5-flash", "mode": "keyframe",
             "prompt": vcall.prompt, "seconds": "5", "image": ff_url, "seed": 42}
    ok, data = await submit(base, key, vbody, "p4")
    if not ok:
        print("P4 submit rejected")
        return
    vid = data.get("video_id") or data.get("task_id")
    await poll(base, key, vid, "agnes-video-2.5-flash", "p4")
    fin = json.load(open("_video25_probe/p4.final.json", encoding="utf-8"))
    url = fin.get("url")
    print("final url:", url)
    if url:
        mp4 = os.path.join("_video25_probe", "p4.mp4")
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            rr = await c.get(url)
            rr.raise_for_status()
            with open(mp4, "wb") as f:
                f.write(rr.content)
        for tag, ss in (("first", "0.2"), ("mid", "2.5"), ("last", "4.8")):
            out = os.path.join("_video25_probe", f"p4_frame_{tag}.jpg")
            subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", mp4,
                            "-frames:v", "1", "-q:v", "3", out],
                           capture_output=True, text=True)
    print("P4 probe done")


if __name__ == "__main__":
    asyncio.run(main())
