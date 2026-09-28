# -*- coding: utf-8 -*-
"""2.5-flash keyframe 双帧（首+尾）真机探针（2026-09-29，42.md §四 P2）。

裁决问题：2.5-flash 是否接受 keyframe 模式的 first_frame+last_frame 双帧形态？
——官方文档（FACTS §3.0.1/169-170）：keyframe 模式图片字段为 first_frame/last_frame
（至少其一）；reference 模式与 first_frame/last_frame 互斥。本仓库 client 层把
keyframes 双帧显式拒绝（"未验证勿发"，_video25_mode）→ 桥路径（bridge_service
固定 keyframes 双帧）在 2.5 下结构性不可达。本探针直接回答 provider 事实：
接受 → 解锁桥路径（client 放行双帧）；拒绝 → 桥在 2.5 时代诚实列为不可用。

素材：真实生产端点帧（《雨灯归航》s02_c02 尾帧 → s02_c03 尾帧——正是桥修复
的典型接缝两端，agnes-frame-host 公网）。
费用：候选 0 命中即 5s（Token Plan 500s/天）；拒绝不耗配额。
证据落 _video25_probe/kf.*（submit/error/final + 帧提取）。
"""
import sys, os, json, asyncio, subprocess, time
sys.path.insert(0, ".")
from _probe_video25 import _load_cfg, submit, poll, _dump

FIRST_FRAME = "https://Q-1844.github.io/agnes-frame-host/objects/cfbb88d3e12a5233.jpg"
LAST_FRAME = "https://Q-1844.github.io/agnes-frame-host/objects/470e69a81e1a836d.jpg"

PROMPT = ("Smooth continuous single-shot transition: the otter opens the heavy wooden "
          "door and steps out of the workshop into the deep blue night, the camera "
          "follows her; the shot ends exactly at the final frame composition. Flat 2D "
          "storybook watercolor style, keep the character appearance consistent "
          "throughout.")


async def extract_frames(url: str, label: str):
    import httpx
    mp4 = os.path.join("_video25_probe", f"{label}.mp4")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        with open(mp4, "wb") as f:
            f.write(r.content)
    print(f"    mp4 saved: {mp4} ({os.path.getsize(mp4)} bytes)")
    for tag, ss in (("first", "0.2"), ("mid", "2.5"), ("last", "4.8")):
        out = os.path.join("_video25_probe", f"{label}_frame_{tag}.jpg")
        rc = subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", mp4,
                             "-frames:v", "1", "-q:v", "3", out],
                            capture_output=True, text=True)
        print(f"    frame {tag}: rc={rc.returncode} -> {out}")


async def main():
    base, key, models = _load_cfg()
    candidates = [
        # 0: 官方字段双帧（桥形态——裁决目标）
        {"model": "agnes-video-2.5-flash", "prompt": PROMPT, "mode": "keyframe",
         "seconds": "5", "first_frame": FIRST_FRAME, "last_frame": LAST_FRAME,
         "seed": 42},
        # 1: 官方字段单 first_frame（区分「字段名错」vs「双帧被拒」）
        {"model": "agnes-video-2.5-flash", "prompt": PROMPT, "mode": "keyframe",
         "seconds": "5", "first_frame": FIRST_FRAME, "seed": 42},
    ]
    vid = None
    for i, body in enumerate(candidates):
        label = f"kf_c{i}"
        print(f"== candidate {i}: {sorted(body.keys())}")
        ok, data = await submit(base, key, body, label)
        if ok:
            vid = data.get("video_id") or data.get("task_id") or data.get("id")
            _dump(f"{label}.accepted_body.json", body)
            break
        await asyncio.sleep(3)   # RPM 礼貌间隔
    if not vid:
        print("ALL CANDIDATES REJECTED — 2.5 keyframe 双帧=不可用（桥诚实列为限制）")
        return
    print(f"accepted: {vid} — poll...")
    final = await poll(base, key, vid, "agnes-video-2.5-flash", "kf")
    url = None
    if isinstance(final, dict):
        url = final.get("url") or final.get("video_url")
    print("final url:", url)
    if url:
        await extract_frames(url, "kf")
    print("P2 probe done")


if __name__ == "__main__":
    asyncio.run(main())
