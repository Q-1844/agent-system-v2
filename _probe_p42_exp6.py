# -*- coding: utf-8 -*-
"""42.md EXP6：动作延伸修甲板 + 修灯视频多候选（2026-09-30，用户裁定）。

1. s01_c06 凭空甲板修复 v2（用户解法）：时长不动（5s），**动作文本延伸**——
   人物跑到码头尽头后加后续动作（停步转身面向灯塔举灯），让动作填满时长，
   场景不再需要凭空延伸。2 种子。
2. s02_c02 修灯视频多候选：定版根帧 crop_s42（链 4 同根），种子 7/11/23，
   足量候选挑「人物+灯（样式/亮灭状态）」零瑕疵——接续 EXP4 遗留的
   「人物无瑕疵与道具完美不同镜达成」问题。
canonical 不动；正式选片走系统 Agent+用户。
"""
import sys, os, json, asyncio, subprocess
sys.path.insert(0, ".")

def _load_env():
    for line in open(".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
_load_env()
from _probe_video25 import _load_cfg, submit, poll, _dump

PROJECT = "雨灯归航"
OUT = "_video25_probe/exp6"
LOOK_URL = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_kRdI2rkUUrTWOXSGRZqTjneUVWAqKSVd/output.png"
PIC_CLAUSE = ("。参考图说明：<Picture 1> 是上一镜头的结尾画面（开场位置与状态须与它"
              "衔接）；<Picture 2> 是该角色的设定参考图（角色的画风、五官、毛色与"
              "服装必须与它保持一致）。")
# 动作延伸条款（用户解法：场景跑不完 → 给人物加后续动作填满时长）
ACTION_EXTEND = (" After reaching the end of the wooden dock, she stops, turns "
                 "around to face the lighthouse, and holds the lantern up "
                 "slightly, watching its light — no new ground appears, the "
                 "dock ends there.")


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
    import httpx
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)


async def gen(base, key, mode, prompt, seed, seconds="5", image=None, images=None, label=""):
    body = {"model": "agnes-video-2.5-flash", "mode": mode,
            "prompt": prompt, "seconds": seconds, "seed": seed}
    if image: body["image"] = image
    if images: body["images"] = images
    for attempt in range(3):
        ok, data = await submit(base, key, body, label)
        if ok:
            return data.get("video_id") or data.get("task_id")
        await asyncio.sleep(25)
    return None


async def finish(base, key, vid, mp4, label, seed):
    await poll(base, key, vid, "agnes-video-2.5-flash", label)
    fin = json.load(open(f"_video25_probe/{label}.final.json", encoding="utf-8"))
    await download(fin["url"], mp4)
    # 5s 视频帧位：b0.3/m1 1.4/m2 2.5/m3 3.6/e 4.6（4s 视频需另调，本轮无）
    for lbl, ss in (("b", "0.3"), ("m1", "1.4"), ("m2", "2.5"), ("m3", "3.6"), ("e", "4.6")):
        subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", mp4, "-frames:v", "1",
                        "-q:v", "3", mp4.replace(".mp4", f"_f{lbl}.jpg")],
                       capture_output=True, text=True)
    _dump(label + ".receipt.json", {"label": label, "seed": seed, "url": fin.get("url")})
    print(f"  {label} done")


async def main():
    os.makedirs(OUT, exist_ok=True)
    from src.core.store import ProjectStore
    from src.core.prompt_builder import build_video_prompt
    base, key, _ = _load_cfg()
    project = ProjectStore(os.path.join("projects", PROJECT)).load()
    e2 = {r["name"]: r["url"] for r in json.load(
        open("_video25_probe/exp2/exp2_receipts.json", encoding="utf-8")) if "url" in r}

    # ---- 1. s01_c06 动作延伸版（5s，种子 42/7）----
    shot6 = project.get_shot("s01_c06")
    prompt6 = build_video_prompt(shot6, "multi").rstrip() + PIC_CLAUSE + ACTION_EXTEND
    tail6_src = "_video25_probe/exp4/chainA_s42_s01_c05_tail_re.jpg"
    tail_src = tail6_src if os.path.exists(tail6_src) else "_video25_probe/exp4/chainA_s42_s01_c05_tail.jpg"
    tail6 = await rehost(tail_src)
    for seed in (42, 7):
        mp4 = os.path.join(OUT, f"s01c06_ext_seed{seed}.mp4")
        if os.path.exists(mp4):
            continue
        vid = await gen(base, key, "reference", prompt6, seed,
                        images=[tail6, LOOK_URL], label=f"exp6_s01c06_ext_s{seed}")
        if vid:
            await finish(base, key, vid, mp4, f"exp6_s01c06_ext_s{seed}", seed)

    # ---- 2. s02_c02 修灯视频多候选（crop_s42 根，种子 7/11/23）----
    shot2 = project.get_shot("s02_c02")
    prompt2 = build_video_prompt(shot2, "i2v")
    root_url = e2["s02_c02_crop_s42"]
    for seed in (7, 11, 23):
        mp4 = os.path.join(OUT, f"s02c02_seed{seed}.mp4")
        if os.path.exists(mp4):
            continue
        vid = await gen(base, key, "keyframe", prompt2, seed,
                        image=root_url, label=f"exp6_s02c02_s{seed}")
        if vid:
            await finish(base, key, vid, mp4, f"exp6_s02c02_s{seed}", seed)
    print("EXP6 done")


if __name__ == "__main__":
    asyncio.run(main())
