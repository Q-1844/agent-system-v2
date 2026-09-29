# -*- coding: utf-8 -*-
"""42.md EXP4：定版配方批量试产（2026-09-29，用户裁定「基本可以确定，再一轮针对测试多生成」）。

定版配方（EXP2/3 验收）：crop 根帧（头肩裁剪 Look）→ i2v 硬首帧 → 新尾帧 →
【新尾帧+Look 双参考】接力下一镜；s01_c04 已补铜灯 prop 绑定。

批量内容：
- 链 A（s01 偏差链全量）：s01_c04（crop_prop_s7 根，2 种子）→ s01_c05 →
  s01_c06——每步双参考接力，共 6 条视频。
- 链 B（s02 链补量）：s02_c02 定版候选根 s1 / s5 各一条链（s02_c02+s02_c03），
  共 4 条视频——与链 4（s42 根）并排供用户终选。
canonical 不动（探针试产；正式选片走系统 Agent+用户）。
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
OUT = "_video25_probe/exp4"
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


async def gen_video(base, key, mode, prompt, seed, image=None, images=None, label=""):
    body = {"model": "agnes-video-2.5-flash", "mode": mode,
            "prompt": prompt, "seconds": "5", "seed": seed}
    if image:
        body["image"] = image
    if images:
        body["images"] = images
    for attempt in range(3):
        ok, data = await submit(base, key, body, label)
        if ok:
            return data.get("video_id") or data.get("task_id")
        await asyncio.sleep(25)
    return None


async def relay_step(base, key, project, prev_shot_id, prev_mp4, seed, tag):
    """提上镜尾帧+托管 → 双参考接力生成下一镜视频 → 下载。返回 mp4 路径。"""
    from src.core.store import ProjectStore
    from src.core.prompt_builder import build_video_prompt
    nxt_id = {"s01_c04": "s01_c05", "s01_c05": "s01_c06", "s02_c02": "s02_c03"}[prev_shot_id]
    project = ProjectStore(os.path.join("projects", PROJECT)).load()
    nxt = project.get_shot(nxt_id)
    prompt = build_video_prompt(nxt, "multi").rstrip() + PIC_CLAUSE
    tail_jpg = os.path.join(OUT, f"{tag}_{prev_shot_id}_tail.jpg")
    extract_tail(prev_mp4, tail_jpg)
    tail_url = await rehost(tail_jpg)
    vid = await gen_video(base, key, "reference", prompt, seed,
                          images=[tail_url, LOOK_URL], label=tag + "_" + nxt_id)
    if not vid:
        return None
    await poll(base, key, vid, "agnes-video-2.5-flash", tag + "_" + nxt_id)
    fin = json.load(open(f"_video25_probe/{tag}_{nxt_id}.final.json", encoding="utf-8"))
    mp4 = os.path.join(OUT, f"{tag}_{nxt_id}.mp4")
    await download(fin["url"], mp4)
    for lbl, ss in (("b", "0.3"), ("m", "2.5"), ("e", "4.6")):
        subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", mp4, "-frames:v", "1",
                        "-q:v", "3", os.path.join(OUT, f"{tag}_{nxt_id}_f{lbl}.jpg")],
                       capture_output=True, text=True)
    _dump(f"{tag}_{nxt_id}.receipt.json", {"tag": tag, "shot": nxt_id,
                                           "tail_url": tail_url, "url": fin.get("url")})
    print(f"  {tag}: {nxt_id} done -> {mp4}")
    return mp4


async def main():
    import httpx
    os.makedirs(OUT, exist_ok=True)
    from src.core.store import ProjectStore
    from src.core.prompt_builder import build_video_prompt
    base, key, _ = _load_cfg()
    pdir = os.path.join("projects", PROJECT)
    project = ProjectStore(pdir).load()
    receipts = json.load(open("_video25_probe/exp3/exp3_receipts.json", encoding="utf-8"))
    url_by_name = {r["name"]: r["url"] for r in receipts if "url" in r}
    e2 = {r["name"]: r["url"] for r in
          json.load(open("_video25_probe/exp2/exp2_receipts.json", encoding="utf-8"))
          if "url" in r}

    # ---- 链 A：s01_c04（crop_prop_s7 根）→ c05 → c06，种子 42/7 ----
    for seed in (42, 7):
        tag = f"chainA_s{seed}"
        root_url = url_by_name["s01_c04_crop_prop_s7"]
        shot = project.get_shot("s01_c04")
        prompt = build_video_prompt(shot, "i2v")
        vid = await gen_video(base, key, "keyframe", prompt, seed,
                              image=root_url, label=tag + "_s01_c04")
        if not vid:
            continue
        await poll(base, key, vid, "agnes-video-2.5-flash", tag + "_s01_c04")
        fin = json.load(open(f"_video25_probe/{tag}_s01_c04.final.json", encoding="utf-8"))
        mp4 = os.path.join(OUT, f"{tag}_s01_c04.mp4")
        await download(fin["url"], mp4)
        print(f"  {tag}: s01_c04 done")
        m1 = await relay_step(base, key, project, "s01_c04", mp4, seed, tag)
        if m1:
            await relay_step(base, key, project, "s01_c05", m1, seed, tag)

    # ---- 链 B：s02_c02 定版候选根 s1 / s5 → s02_c03（种子 42） ----
    for root_name in ("s02_c02_crop_s1", "s02_c02_crop_s5"):
        tag = f"chainB_{root_name.split('_')[-1]}"
        root_url = e2[root_name]
        shot = project.get_shot("s02_c02")
        prompt = build_video_prompt(shot, "i2v")
        vid = await gen_video(base, key, "keyframe", prompt, 42,
                              image=root_url, label=tag + "_s02_c02")
        if not vid:
            continue
        await poll(base, key, vid, "agnes-video-2.5-flash", tag + "_s02_c02")
        fin = json.load(open(f"_video25_probe/{tag}_s02_c02.final.json", encoding="utf-8"))
        mp4 = os.path.join(OUT, f"{tag}_s02_c02.mp4")
        await download(fin["url"], mp4)
        print(f"  {tag}: s02_c02 done")
        await relay_step(base, key, project, "s02_c02", mp4, 42, tag)
    print("EXP4 done")


if __name__ == "__main__":
    asyncio.run(main())
