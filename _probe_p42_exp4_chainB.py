# -*- coding: utf-8 -*-
"""EXP4 链 B 补跑：chainB_s1 用已提交任务（不重提交）+ chainB_s5 完整跑。"""
import sys, os, json, asyncio, subprocess
sys.path.insert(0, ".")
from _probe_p42_exp4 import relay_step, gen_video, _load_env, extract_tail
_load_env()
from _probe_video25 import _load_cfg, poll, submit, _dump
import httpx

PROJECT = "雨灯归航"
OUT = "_video25_probe/exp4"
ALREADY = {"chainB_s1_s02_c02": "task_3KcUIzZjn4NI920X2GCb12MATiby89RJ"}


async def download(url, dest):
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)


async def main():
    base, key, _ = _load_cfg()
    from src.core.store import ProjectStore
    project = ProjectStore(os.path.join("projects", PROJECT)).load()

    # ---- chainB_s1：已提交任务续收 ----
    tag = "chainB_s1"
    vid = ALREADY[tag + "_s02_c02"]
    await poll(base, key, vid, "agnes-video-2.5-flash", tag + "_s02_c02")
    fin = json.load(open(f"_video25_probe/{tag}_s02_c02.final.json", encoding="utf-8"))
    mp4 = os.path.join(OUT, f"{tag}_s02_c02.mp4")
    await download(fin["url"], mp4)
    print(tag, "s02_c02 recovered ->", mp4)
    m1 = await relay_step(base, key, project, "s02_c02", mp4, 42, tag)
    print(tag, "complete" if m1 else "relay failed")

    # ---- chainB_s5：完整跑 ----
    tag = "chainB_s5"
    e3 = {r["name"]: r["url"] for r in json.load(
        open("_video25_probe/exp3/exp3_receipts.json", encoding="utf-8")) if "url" in r}
    from src.core.prompt_builder import build_video_prompt
    shot = project.get_shot("s02_c02")
    prompt = build_video_prompt(shot, "i2v")
    vid = await gen_video(base, key, "keyframe", prompt, 42,
                          image=e3["s02_c02_crop_s5"], label=tag + "_s02_c02")
    if vid:
        await poll(base, key, vid, "agnes-video-2.5-flash", tag + "_s02_c02")
        fin = json.load(open(f"_video25_probe/{tag}_s02_c02.final.json", encoding="utf-8"))
        mp4 = os.path.join(OUT, f"{tag}_s02_c02.mp4")
        await download(fin["url"], mp4)
        print(tag, "s02_c02 done")
        m1 = await relay_step(base, key, project, "s02_c02", mp4, 42, tag)
        print(tag, "complete" if m1 else "relay failed")


if __name__ == "__main__":
    asyncio.run(main())
