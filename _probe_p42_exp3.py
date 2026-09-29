# -*- coding: utf-8 -*-
"""42.md EXP3：crop 主配方深测 + s01_c04 补道具绑定 + crop 链视频（2026-09-29）。

用户 EXP2 评审裁定（FEEDBACK.json 全量在档）：
- crop（头肩裁剪 Look）= 跨两镜最稳配方（「很好」「人物大小合理基本看不出来」）
- keep 族=可爱但身材失真；keep_fullscene=风格最正但构图锚死风险（灵活性担忧）
- 新发现：s01_c04 每张图的灯都不一样 → 查证属实（Binding 无 prop 绑定）
- 遗留：链 2 是否继续生成看人物还会不会再变

本脚本三件事：
1. s01_c04 补绑铜灯 prop（正式 ShotBindingService，状态注记按镜描述）。
2. 图像矩阵：s02_c02 crop×5 新种子；s01_c04 crop×5 新种子；s01_c04
   crop+prop×2 种子（与 exp2 无 prop 版直答灯一致性问题）。
3. 视频：链 4（crop_s42 根帧→i2v→双参考接力 s02_c03）；链 6（链 2 根帧
   keep_fullscene_s42 换种子重生成——回答「人物还会不会再变」）。
canonical 变更=仅 s01_c04 的 prop 绑定（用户发现问题的正式修复）；
视频/图像样本均为探针产物，正式选片走系统 Agent。
"""
import sys, os, json, asyncio, subprocess
sys.path.insert(0, ".")
import copy as _copy
import httpx

_load_env_done = False
def _load_env():
    global _load_env_done
    if _load_env_done:
        return
    for line in open(".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    _load_env_done = True

_load_env()
from _probe_video25 import _load_cfg, submit, poll, _dump

PROJECT = "雨灯归航"
OUT = "_video25_probe/exp3"
LOOK_URL = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_kRdI2rkUUrTWOXSGRZqTjneUVWAqKSVd/output.png"
NEW_SEEDS = [1, 2, 3, 5, 11]


def extract_tail(mp4, out_jpg):
    rc = subprocess.run(["ffmpeg", "-y", "-sseof", "-0.2", "-i", mp4,
                         "-frames:v", "1", "-q:v", "3", out_jpg],
                        capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr[-300:]


async def rehost(path):
    from src.core.image_host import build_image_host
    import yaml
    cfg = yaml.safe_load(open("config/config.yaml", encoding="utf-8"))
    ih = json.loads(json.dumps(cfg.get("image_host") or {}))
    for sect in (ih.get("github_pages") or {}, ih.get("imgbb") or {}):
        for k, v in list(sect.items()):
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                sect[k] = os.environ.get(v[2:-1], "")
    host = build_image_host(ih)
    return await host.upload(path)


async def download(url, dest):
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)


def bind_prop_to_s01_c04():
    """s01_c04 补铜灯 prop 绑定（正式 ShotBindingService；用户发现问题的修复）。"""
    from src.shot_binding import ShotBindingService
    from src.core.store import ProjectStore
    pdir = os.path.join("projects", PROJECT)
    svc = ShotBindingService(pdir)
    v = svc.read_validity("s01_c04")
    cur = svc.get_binding_document("s01_c04") if hasattr(svc, "get_binding_document") else None
    if cur is None:
        # 结构探测 fallback：从绑定仓库直接读
        store = json.load(open(os.path.join(
            pdir, "output", "shot_production_bindings.json"), encoding="utf-8"))
        bs = store["bindings"]
        cur = bs.get("s01_c04") if isinstance(bs, dict) else next(
            (b for b in bs if b.get("shot_id") == "s01_c04"), None)
    assert cur, "s01_c04 binding doc not found"
    if any(p.get("prop_id") == "prop_fb1bd0b18fb1"
           for p in (cur.get("prop_bindings") or [])):
        print("s01_c04 已有 prop 绑定，跳过")
        return False
    payload = {
        "shot_id": "s01_c04",
        "scene_binding": cur.get("scene_binding"),
        "character_bindings": cur.get("character_bindings"),
        "prop_bindings": [{
            "prop_id": "prop_fb1bd0b18fb1",
            "relation": "near",
            "anchor": "held_by_character",
            "visual_state_note": ("BRASS storm lantern held up close in both paws "
                                  "for inspection, glass chimney with a visible "
                                  "crack, dried mud streaks on the brass body, "
                                  "unlit")},
        ],
        "production_notes": "EXP3：用户发现灯不一致 → 补道具锚定（42.md §三 道具样式层）",
    }
    r = svc.create_binding(payload)
    print("create_binding:", r.get("ok"), json.dumps(r, ensure_ascii=False)[:200])
    return r.get("ok")


async def gen_images():
    from src.core.store import ProjectStore
    from src.generation.image_adapter import build_bound_first_frame_input
    from src.generation.agnes_adapter import compile_agnes_image_call
    pdir = os.path.join("projects", PROJECT)
    project = ProjectStore(pdir).load()
    base, key, _ = _load_cfg()
    os.makedirs(OUT, exist_ok=True)
    jobs = []
    for seed in NEW_SEEDS:
        jobs.append(("s02_c02", "crop", seed, False))
        jobs.append(("s01_c04", "crop", seed, False))
    for seed in (42, 7):
        jobs.append(("s01_c04", "crop", seed, True))   # prop 已绑定后重生成
    receipts = []
    async with httpx.AsyncClient(base_url=base,
                                 headers={"Authorization": f"Bearer {key}"},
                                 timeout=httpx.Timeout(300, connect=30,
                                                       read=300)) as c:
        for sid, arm, seed, after_prop in jobs:
            shot = ProjectStore(pdir).load().get_shot(sid)
            bound = build_bound_first_frame_input(
                pdir, project, shot, scene_reference_mode="context_only",
                human_reference_mode="crop")
            gen = bound.to_generation_request(generation_type="first_frame",
                                              size_tier="1K", ratio="16:9")
            call = compile_agnes_image_call(gen)
            name = f"{sid}_{arm}{'_prop' if after_prop else ''}_s{seed}"
            out_png = os.path.join(OUT, name + ".png")
            if os.path.exists(out_png):
                continue
            body = {"model": "agnes-image-2.1-flash", "prompt": bound.prompt,
                    "size": "1312x736", "seed": seed,
                    "extra_body": {"response_format": "url"}}
            if call.image_refs:
                body["extra_body"]["image"] = call.image_refs
            r = await c.post("/v1/images/generations", json=body)
            print(f"  [{name}] -> {r.status_code} (refs={len(call.image_refs or [])})")
            if r.status_code != 200:
                receipts.append({"name": name, "error": r.text[:200]})
                continue
            url = r.json()["data"][0]["url"]
            rr = await c.get(url)
            rr.raise_for_status()
            with open(out_png, "wb") as f:
                f.write(rr.content)
            receipts.append({"name": name, "shot": sid, "arm": "crop",
                             "seed": seed, "after_prop": after_prop,
                             "used_reference_ids": bound.used_reference_ids,
                             "url": url})
    with open(os.path.join(OUT, "exp3_receipts.json"), "w", encoding="utf-8") as f:
        json.dump(receipts, f, ensure_ascii=False, indent=1)


async def gen_chain(tag, root_url, seed):
    """根帧 → s02_c02 i2v 视频 → 尾帧托管 → s02_c03 双参考接力。"""
    from src.core.store import ProjectStore
    from src.core.prompt_builder import build_video_prompt
    pdir = os.path.join("projects", PROJECT)
    project = ProjectStore(pdir).load()
    shot2 = project.get_shot("s02_c02")
    shot3 = project.get_shot("s02_c03")
    prompt2 = build_video_prompt(shot2, "i2v")
    prompt3 = build_video_prompt(shot3, "multi").rstrip() + (
        "。参考图说明：<Picture 1> 是上一镜头的结尾画面（开场位置与状态须与它"
        "衔接）；<Picture 2> 是该角色的设定参考图（角色的画风、五官、毛色与"
        "服装必须与它保持一致）。")
    base, key, _ = _load_cfg()
    body1 = {"model": "agnes-video-2.5-flash", "mode": "keyframe",
             "prompt": prompt2, "seconds": "5", "image": root_url, "seed": seed}
    ok, data = await submit(base, key, body1, tag + "_c02")
    if not ok:
        return
    vid1 = data.get("video_id") or data.get("task_id")
    await poll(base, key, vid1, "agnes-video-2.5-flash", tag + "_c02")
    fin = json.load(open(f"_video25_probe/{tag}_c02.final.json", encoding="utf-8"))
    mp4 = os.path.join(OUT, f"{tag}_s02c02.mp4")
    await download(fin["url"], mp4)
    tail_jpg = os.path.join(OUT, f"{tag}_s02c02_tail.jpg")
    extract_tail(mp4, tail_jpg)
    tail_url = await rehost(tail_jpg)
    body2 = {"model": "agnes-video-2.5-flash", "mode": "reference",
             "prompt": prompt3, "seconds": "5",
             "images": [tail_url, LOOK_URL], "seed": seed}
    ok2, data2 = await submit(base, key, body2, tag + "_c03")
    if not ok2:
        await asyncio.sleep(20)
        ok2, data2 = await submit(base, key, body2, tag + "_c03")
    if not ok2:
        _dump(f"{tag}.error.json", data2)
        return
    vid2 = data2.get("video_id") or data2.get("task_id")
    await poll(base, key, vid2, "agnes-video-2.5-flash", tag + "_c03")
    fin2 = json.load(open(f"_video25_probe/{tag}_c03.final.json", encoding="utf-8"))
    mp42 = os.path.join(OUT, f"{tag}_s02c03.mp4")
    await download(fin2["url"], mp42)
    for lbl, ss in (("b", "0.3"), ("m", "2.5"), ("e", "4.6")):
        subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", mp42, "-frames:v", "1",
                        "-q:v", "3", os.path.join(OUT, f"{tag}_s02c03_f{lbl}.jpg")],
                       capture_output=True, text=True)
    _dump(f"{tag}.receipt.json", {"tag": tag, "root_url": root_url,
                                  "seed": seed, "s02c02": fin.get("url"),
                                  "tail_url": tail_url, "s02c03": fin2.get("url")})
    print(f"chain {tag} done")


async def main():
    os.makedirs(OUT, exist_ok=True)
    print("== 1. bind prop to s01_c04")
    bind_prop_to_s01_c04()
    print("== 2. image matrix")
    await gen_images()
    print("== 3. chains")
    recs = {r["name"]: r["url"] for r in
            json.load(open(os.path.join(OUT, "exp3_receipts.json"),
                           encoding="utf-8")) if "url" in r}
    # 链 4：crop_s42 根帧（exp2 图，用户评「很好」）
    e2 = {r["name"]: r["url"] for r in
          json.load(open("_video25_probe/exp2/exp2_receipts.json",
                         encoding="utf-8")) if "url" in r}
    await gen_chain("chain4_crop_s42", e2["s02_c02_crop_s42"], 42)
    # 链 6：链 2 根帧（keep_fullscene_s42）换种子 7 —— 回答「人物还会不会再变」
    await gen_chain("chain6_fullscene_s42_s7", e2["s02_c02_keep_fullscene_s42"], 7)
    print("EXP3 done")


if __name__ == "__main__":
    asyncio.run(main())
