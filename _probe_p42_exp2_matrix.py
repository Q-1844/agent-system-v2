# -*- coding: utf-8 -*-
"""42.md EXP2：特写锚定大样本图像矩阵（2026-09-29，用户反馈驱动）。

用户裁定：P1/P3 数据不足以裁决（「首帧人物就不对」「各有缺点」「B 图人物还是
有点宽，应衬托小巧可爱」）——图像额度充足（4000/天），换一镜、多臂、多种子
做对比，预览页供用户挑选。

两镜（两个偏差根，均为 medium close-up）：
  s02_c02（修理间拧灯）、s01_c04（河面灯影特写）
五臂（drop 现状图已有，不重生成）：
  crop            头肩裁剪 Look + scene context_only
  keep            全幅 Look + scene context_only
  keep_comp       keep + 小巧可爱构图条款（候选修复层=描述模板）
  crop_comp       crop + 构图条款
  keep_fullscene  keep + scene full（场景图同发——测场景参考对形象/构图的影响）
种子：42 / 7（方差可见）。
输出：_video25_probe/exp2/*.png + exp2_receipts.jsonl。
"""
import sys, os, json, asyncio
sys.path.insert(0, ".")
import copy as _copy
import httpx

from _probe_video25 import _load_cfg, _dump

PROJECT = "雨灯归航"
SHOTS = ["s02_c02", "s01_c04"]
SEEDS = [42, 7]
COMP_CLAUSE = (" Keep the character's small cute rounded proportions exactly as in "
               "the character reference; leave breathing room around the character, "
               "do not zoom in until she fills the frame.")
OUT = "_video25_probe/exp2"
ARMS = {
    "crop":           {"scene": "context_only", "human": "crop",  "clause": False},
    "keep":           {"scene": "context_only", "human": "keep",  "clause": False},
    "keep_comp":      {"scene": "context_only", "human": "keep",  "clause": True},
    "crop_comp":      {"scene": "context_only", "human": "crop",  "clause": True},
    "keep_fullscene": {"scene": "full",         "human": "keep",  "clause": False},
}


async def main():
    os.makedirs(OUT, exist_ok=True)
    from src.core.store import ProjectStore
    from src.generation.image_adapter import build_bound_first_frame_input
    from src.generation.agnes_adapter import compile_agnes_image_call

    pdir = os.path.join("projects", PROJECT)
    project = ProjectStore(pdir).load()
    base, key, _models = _load_cfg()
    receipts = []
    total = 0
    async with httpx.AsyncClient(base_url=base,
                                 headers={"Authorization": f"Bearer {key}"},
                                 timeout=httpx.Timeout(300, connect=30,
                                                       read=300)) as c:
        for sid in SHOTS:
            shot0 = project.get_shot(sid)
            print(f"===== {sid} size={shot0.shot_size!r}")
            for arm, cfg in ARMS.items():
                bound = build_bound_first_frame_input(
                    pdir, project, shot0,
                    scene_reference_mode=cfg["scene"],
                    human_reference_mode=cfg["human"])
                prompt = bound.prompt + (COMP_CLAUSE if cfg["clause"] else "")
                for seed in SEEDS:
                    name = f"{sid}_{arm}_s{seed}"
                    out_png = os.path.join(OUT, name + ".png")
                    if os.path.exists(out_png):
                        print(f"  skip {name} (exists)")
                        continue
                    gen = bound.to_generation_request(
                        generation_type="first_frame", size_tier="1K",
                        ratio="16:9")
                    # 用带条款的 prompt 覆盖（条款=候选修复层，探针变量）
                    call = compile_agnes_image_call(gen)
                    body = {"model": "agnes-image-2.1-flash",
                            "prompt": prompt, "size": "1312x736",
                            "seed": seed,
                            "extra_body": {"response_format": "url"}}
                    if call.image_refs:
                        body["extra_body"]["image"] = call.image_refs
                    r = await c.post("/v1/images/generations", json=body)
                    print(f"  [{name}] -> {r.status_code} "
                          f"(refs={len(call.image_refs or [])})")
                    if r.status_code != 200:
                        receipts.append({"name": name, "error": r.text[:300]})
                        continue
                    url = r.json()["data"][0]["url"]
                    rr = await c.get(url)
                    rr.raise_for_status()
                    with open(out_png, "wb") as f:
                        f.write(rr.content)
                    total += 1
                    receipts.append({
                        "name": name, "shot": sid, "arm": arm, "seed": seed,
                        "scene": cfg["scene"], "human": cfg["human"],
                        "clause": cfg["clause"],
                        "human_resolved": bound.human_reference_mode,
                        "n_refs": len(call.image_refs or []),
                        "used_reference_ids": bound.used_reference_ids,
                        "url": url})
    with open(os.path.join(OUT, "exp2_receipts.json"), "w", encoding="utf-8") as f:
        json.dump(receipts, f, ensure_ascii=False, indent=1)
    print(f"DONE: {total} images generated → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
