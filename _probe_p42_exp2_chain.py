# -*- coding: utf-8 -*-
"""42.md EXP2-B：从根重建视频链（2026-09-29，用户反馈「首帧人物就不对」驱动）。

用户裁定：P1 探针首帧继承了偏差尾帧，人物从第一帧就不对——对照不成立。
本探针从根修起：用 EXP2 图像矩阵的最优根帧 → 重生成 s02_c02 视频 → 提取新
尾帧+托管 → 以「新尾帧+Look 双参考」重生成 s02_c03——得到首帧人物即正确的
完整链样本，与现役偏差链端到端对照。

三链（EXP2 初筛：keep_fullscene 两臂画风/身份最佳、keep_comp 构图条款臂）：
  chain_fullscene_s7   根帧=keep_fullscene seed42 之中更优 s7
  chain_fullscene_s42  根帧=keep_fullscene s42
  chain_keep_comp_s42  根帧=keep_comp s42（构图条款）
每链 2 条视频（5s+5s）。canonical 不落盘（探针样本，正式采用走系统 Agent）。
输出：_video25_probe/exp2/chain_*。
"""
import sys, os, json, asyncio, subprocess
sys.path.insert(0, ".")
import copy as _copy
import httpx


def _load_env():
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(env_file):
        for line in open(env_file, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
from _probe_video25 import _load_cfg, submit, poll, _dump

PROJECT = "雨灯归航"
OUT = "_video25_probe/exp2"
LOOK_URL = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_kRdI2rkUUrTWOXSGRZqTjneUVWAqKSVd/output.png"
CHAINS = [
    ("chain_fullscene_s7", "s02_c02_keep_fullscene_s7"),
    ("chain_fullscene_s42", "s02_c02_keep_fullscene_s42"),
    ("chain_keep_comp_s42", "s02_c02_keep_comp_s42"),
]


def extract_tail(mp4: str, out_jpg: str):
    rc = subprocess.run(["ffmpeg", "-y", "-sseof", "-0.2", "-i", mp4,
                         "-frames:v", "1", "-q:v", "3", out_jpg],
                        capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr[-300:]
    return out_jpg


async def rehost(path: str) -> str:
    from src.core.image_host import build_image_host
    import yaml
    cfg = yaml.safe_load(open("config/config.yaml", encoding="utf-8"))
    ih = json.loads(json.dumps(cfg.get("image_host") or {}))  # deep copy
    # config 里 token 是 ${VAR} 占位符（生产由 controller 工厂做 env 替换）——
    # 探针侧手动替换（与 controller.py 同语义）
    for sect in (ih.get("github_pages") or {}, ih.get("imgbb") or {}):
        for k, v in list(sect.items()):
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                sect[k] = os.environ.get(v[2:-1], "")
    host = build_image_host(ih)
    return await host.upload(path)


async def download(url: str, dest: str):
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)


async def main():
    from src.core.store import ProjectStore
    from src.core.prompt_builder import build_video_prompt
    receipts = json.load(open(os.path.join(OUT, "exp2_receipts.json"),
                              encoding="utf-8"))
    url_by_name = {r["name"]: r["url"] for r in receipts if "url" in r}
    pdir = os.path.join("projects", PROJECT)
    project = ProjectStore(pdir).load()
    shot2 = project.get_shot("s02_c02")
    shot3 = project.get_shot("s02_c03")
    prompt2 = build_video_prompt(shot2, "i2v")
    prompt3 = build_video_prompt(shot3, "multi")
    base, key, _ = _load_cfg()

    for tag, root_name in CHAINS:
        root_url = url_by_name[root_name]
        print(f"===== {tag} root={root_name}")
        # 步骤 1：s02_c02 视频（i2v，硬首帧=修好的根帧）
        body1 = {"model": "agnes-video-2.5-flash", "mode": "keyframe",
                 "prompt": prompt2, "seconds": "5", "image": root_url, "seed": 42}
        ok, data = await submit(base, key, body1, tag + "_c02")
        if not ok:
            continue
        vid1 = data.get("video_id") or data.get("task_id")
        await poll(base, key, vid1, "agnes-video-2.5-flash", tag + "_c02")
        fin = json.load(open(f"_video25_probe/{tag}_c02.final.json",
                             encoding="utf-8"))
        mp4 = os.path.join(OUT, f"{tag}_s02c02.mp4")
        await download(fin["url"], mp4)
        print(f"    s02_c02 video saved {mp4}")
        # 步骤 2：提尾帧+托管
        tail_jpg = os.path.join(OUT, f"{tag}_s02c02_tail.jpg")
        extract_tail(mp4, tail_jpg)
        tail_url = await rehost(tail_jpg)
        print(f"    tail rehosted: {tail_url}")
        # 步骤 3：s02_c03 视频（reference 双参考=新尾帧+Look）
        p3 = prompt3.rstrip() + (
            "。参考图说明：<Picture 1> 是上一镜头的结尾画面（开场位置与状态须与它"
            "衔接）；<Picture 2> 是该角色的设定参考图（角色的画风、五官、毛色与"
            "服装必须与它保持一致）。")
        body2 = {"model": "agnes-video-2.5-flash", "mode": "reference",
                 "prompt": p3, "seconds": "5",
                 "images": [tail_url, LOOK_URL], "seed": 42}
        ok2, data2 = await submit(base, key, body2, tag + "_c03")
        if not ok2:
            continue
        vid2 = data2.get("video_id") or data2.get("task_id")
        await poll(base, key, vid2, "agnes-video-2.5-flash", tag + "_c03")
        fin2 = json.load(open(f"_video25_probe/{tag}_c03.final.json",
                              encoding="utf-8"))
        mp42 = os.path.join(OUT, f"{tag}_s02c03.mp4")
        await download(fin2["url"], mp42)
        print(f"    s02_c03 video saved {mp42}")
        for lbl, ss in (("b", "0.3"), ("m", "2.5"), ("e", "4.6")):
            subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", mp42,
                            "-frames:v", "1", "-q:v", 3 if False else "3",
                            os.path.join(OUT, f"{tag}_s02c03_f{lbl}.jpg")],
                           capture_output=True, text=True)
        _dump(f"{tag}.receipt.json", {
            "tag": tag, "root_image": root_name, "root_url": root_url,
            "s02c02_video": fin.get("url"), "tail_url": tail_url,
            "s02c03_video": fin2.get("url"),
            "note": "探针链：canonical 未动；根帧修复+双参考接力"})
    print("EXP2-B chains done")


if __name__ == "__main__":
    asyncio.run(main())
