"""2.5-flash reference 模式真机探针（2026-09-28，Phase 41 前置核验）。

目的：官方文档核读后（wiki.agnes-ai.com/en/docs/agnes-video-25-flash），真机验证
multi→reference 映射的请求形状：顶层 `images` 数组（≤5）+ seed integer。
双图（Look+上镜尾帧形态）优先——这是生产 relay 的目标形态。
拒绝请求不耗配额（flash 校验在任务创建前）；被接受的 1 条 5s 任务耗 5s/500s。

证据落 _video25_probe/ref.*（submit/error/final/head + 帧提取）。
"""
import sys, os, json, asyncio, subprocess
sys.path.insert(0, ".")
from _probe_video25 import _load_cfg, submit, poll, download_head, _dump

# 双图：ibb.co（v25d 已验证公网可读）+ agnes cos 生产分镜图（s01_c01 首帧，公网）
IMG1 = "https://i.ibb.co/fGH0wF93/8f6ccfdbfd0e.png"
IMG2 = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_uytmu7w5At5j49l2X4wzn0xoEMUaaOg8/output.png"

PROMPT = ("Use <Picture 1> as the character appearance reference and <Picture 2> as "
          "the scene continuation reference. The character walks slowly along a "
          "lantern-lit rainy street at dusk, smooth camera tracking, flat 2D "
          "storybook style, keep appearance consistent with <Picture 1>.")


async def extract_frames(url: str, label: str):
    """下载 mp4 后 ffmpeg 抽首/中/尾帧（身份/构图目检用）。"""
    try:
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
    except Exception as e:
        print(f"    extract fail: {e}")


async def main():
    base, key, models = _load_cfg()
    candidates = [
        # 0: 官方形状——images 数组双图 + seed（文档核读，直接命中概率最高）
        {"model": "agnes-video-2.5-flash", "prompt": PROMPT, "mode": "reference",
         "seconds": "5", "images": [IMG1, IMG2], "seed": 42},
        # 1: 兼容猜测——image 数组
        {"model": "agnes-video-2.5-flash", "prompt": PROMPT, "mode": "reference",
         "seconds": "5", "image": [IMG1, IMG2]},
        # 2: 官方形状单图（若双图被拒，区分是形状错还是数量错）
        {"model": "agnes-video-2.5-flash", "prompt": PROMPT, "mode": "reference",
         "seconds": "5", "images": [IMG1]},
    ]
    vid = None
    for i, body in enumerate(candidates):
        if i > 0:
            print("  ... 等 70s（1 RPM 视频桶）...")
            await asyncio.sleep(70)
        print(f"attempt {i}: {json.dumps({k: (v if k != 'prompt' else '...')
                                        for k, v in body.items()}, ensure_ascii=False)}")
        ok, data = await submit(base, key, body, f"ref.a{i}")
        if ok:
            vid = data.get("video_id") or data.get("task_id") or data.get("id")
            _dump("ref.accepted_body.json", body)
            print(f"  ACCEPTED: {sorted(body.keys())}")
            break
    if not vid:
        print("!! reference 模式全部候选被拒（error json 已落盘，勿在生产使用）")
        return 1
    st, final, url = await poll(base, key, vid, "agnes-video-2.5-flash", "ref")
    if st == "completed" and url:
        await download_head(url, "ref")
        await extract_frames(url, "ref")
        print("== 2.5-flash reference 模式确认（双图形状 + seed）+ 生成成功 ==")
        return 0
    print(f"== 未完成 st={st} url={url} ==")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
