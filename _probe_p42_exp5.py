# -*- coding: utf-8 -*-
"""42.md EXP5：完美视频验证轮（2026-09-30，用户裁定「需要知道能否做出人物、
道具、场景都完全没问题的视频」）。

两个针对性测试：
1. s02_c03 多候选（链 4 配方不变，种子 7/11/23——加上已有 seed42 共 4 候选）：
   足量候选中挑「人物+道具+场景」零瑕疵样本 → 证明可能性。
2. s01_c06 凭空甲板修复验证（种子 42 动作不变、时长 5s→4s）：
   验证「动作-时长-场景匹配」判据——缩短时长是否消除场景凭空延伸。
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
OUT = "_video25_probe/exp5"
LOOK_URL = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_kRdI2rkUUrTWOXSGRZqTjneUVWAqKSVd/output.png"
PIC_CLAUSE = ("。参考图说明：<Picture 1> 是上一镜头的结尾画面（开场位置与状态须与它"
              "衔接）；<Picture 2> 是该角色的设定参考图（角色的画风、五官、毛色与"
              "服装必须与它保持一致）。")


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
    for lbl, ss in (("b", "0.3"), ("m1", "1.4"), ("m2", "2.5"), ("m3", "3.6"), ("e", "4.6")):
        subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", mp4, "-frames:v", "1",
                        "-q:v", "3", mp4.replace(".mp4", f"_f{lbl}.jpg")],
                       capture_output=True, text=True)
    _dump(label + ".receipt.json", {"label": label, "seed": seed, "url": fin.get("url")})
    print(f"  {label} done")


async def main():
    import httpx
    os.makedirs(OUT, exist_ok=True)
    from src.core.store import ProjectStore
    from src.core.prompt_builder import build_video_prompt
    base, key, _ = _load_cfg()
    pdir = os.path.join("projects", PROJECT)
    project = ProjectStore(pdir).load()

    # ---- 1. s02_c03 多候选（链 4 根帧链路的尾帧接力；种子 7/11/23）----
    # 尾帧源=链 4 的 s02_c02 视频（seed42 根帧 crop_s42）
    shot3 = project.get_shot("s02_c03")
    prompt3 = build_video_prompt(shot3, "multi").rstrip() + PIC_CLAUSE
    tail_src = "_video25_probe/exp3/chain4_crop_s42_s02c02_tail.jpg"
    assert os.path.exists(tail_src), "链4 尾帧缺失"
    tail_url = await rehost(tail_src)
    for seed in (7, 11, 23):
        mp4 = os.path.join(OUT, f"s02c03_seed{seed}.mp4")
        if os.path.exists(mp4):
            continue
        vid = await gen(base, key, "reference", prompt3, seed,
                        images=[tail_url, LOOK_URL], label=f"exp5_s02c03_s{seed}")
        if vid:
            await finish(base, key, vid, mp4, f"exp5_s02c03_s{seed}", seed)

    # ---- 2. s01_c06 时长匹配修复验证（种子 42 动作不变，5s→4s）----
    shot6 = project.get_shot("s01_c06")
    prompt6 = build_video_prompt(shot6, "multi").rstrip() + PIC_CLAUSE
    # 尾帧源=链 A seed42 的 s01_c05 尾帧
    tail6_src = "_video25_probe/exp4/chainA_s42_s01_c05_tail.jpg"
    assert os.path.exists(tail6_src), "链A c05 尾帧缺失"
    tail6 = await rehost(tail6_src)
    mp4 = os.path.join(OUT, "s01c06_4s_seed42.mp4")
    if not os.path.exists(mp4):
        vid = await gen(base, key, "reference", prompt6, 42, seconds="4",
                        images=[tail6, LOOK_URL], label="exp5_s01c06_4s")
        if vid:
            await finish(base, key, vid, mp4, "exp5_s01c06_4s", 42)
    print("EXP5 done")


if __name__ == "__main__":
    asyncio.run(main())
