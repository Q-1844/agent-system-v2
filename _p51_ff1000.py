# -*- coding: utf-8 -*-
"""Phase51 C 首帧千张测试战役（用户授权：视频额度已尽，图片可测数千张）。

核心课题：**人物贴图感**（用户定版的大问题——P50 验收"首帧贴图感→i2v 人物僵死"）。
多因子矩阵：参考策略(5) × 景别(3) × 光线(2) × 融合提示词(2) = 60 条件 × 17 seeds ≈ 1020 张。
轮转出图（round-robin：相邻两张必属不同条件——满足"每几十张换样式"）。

度量：
  1) 贴图感评分：视觉模型逐张打分（1=完全融入…5=明显贴图）+ 受光一致性 + 人数核对
  2) 生成效率：逐张 elapsed_s → 均值/p95/有效 RPM/成功率（分条件+总体）
产物：_p51_ff1000/{ledger.jsonl, screen.jsonl, images/<cond>/, sheets/}
可恢复：已存在的图片跳过；STOP 文件出现则优雅停。
"""
import asyncio
import base64
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path("_p51_ff1000")
IMG = BASE / "images"
SHEETS = BASE / "sheets"
LEDGER = BASE / "ledger.jsonl"
SCREEN = BASE / "screen.jsonl"
for p in (IMG, SHEETS):
    p.mkdir(parents=True, exist_ok=True)

REF_CAPTAIN = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_aF380vb3j2t9Y0f1ozdDKzDjL7d9XSNT/output.png"
REF_MATE = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_8idQeQTKxQCk9eyWKh5D2hWTmWwtyDP1/output.png"
REF_BOAT = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_Y7K4sqXAL6RWaNdBxMmZwGe18QNhwcNH/output.png"

# ---- 因子定义 ----
REF_STRATS = {
    "双Look": {"refs": [REF_CAPTAIN, REF_MATE],
               "id_text": ""},
    "单Look船长": {"refs": [REF_CAPTAIN],
                 "id_text": "船上两人：年长的船长（灰蓝色工装衬衫、短胡茬、深色裤子）与年轻的大副（军绿色衬衫、白T恤、手臂纹身）。"},
    "单Look大副": {"refs": [REF_MATE],
                 "id_text": "船上两人：年轻的大副（军绿色衬衫、白T恤、手臂纹身）与年长的船长（灰蓝色工装衬衫、短胡茬、深色裤子）。"},
    "双Look加场景": {"refs": [REF_CAPTAIN, REF_MATE, REF_BOAT],
                  "id_text": ""},
    "无参考": {"refs": [],
             "id_text": "船上两人：年长的船长约55岁，灰色短发与短胡茬，穿灰蓝色防水工装外套（做旧磨损）、深色裤子；年轻的大副约28岁，深棕短发，穿军绿色工装衬衫内白T恤、手臂有纹身。两人面孔必须不同且各自保持一致。"},
}
SIZES = {
    "MCU": ("中近景（胸口以上充满画面，人物占画面高度一半以上）",),
    "MS": ("中景（膝盖以上，甲板环境可见）",),
    "WS": ("远景（人物全身较小，船体与海面占画面主体）",),
}
LIGHTS = {
    "夕阳逆光": "太阳低垂在人物身后的海面上，人物处于半剪影与轮廓光状态，暖金色反光洒满海面",
    "柔和顺光": "太阳在画面侧后方，人物面部受光柔和均匀，天空呈淡橙粉色",
}
FUSIONS = {
    "基线": "",
    "强化融合": "整幅画面必须是同一张照片拍摄的：人物受光方向与场景光源完全一致，"
             "皮肤与衣物的色温随环境光变化，人物边缘与背景自然过渡并带空气透视，"
             "地面与脚部有接触阴影；绝无合成感、绝无贴图感、绝无边缘光晕。",
}
BASE_SCENE = "黄昏，开阔海面上的一艘深蓝色木质渔船的甲板。甲板有磨损的木板、盘起的缆绳、木质舵轮。"


def build_conditions():
    """60 条件（参考5 × 景别3 × 光线2 × 融合2）。"""
    conds = {}
    for rname, rcfg in REF_STRATS.items():
        for sname, (ssize,) in SIZES.items():
            for lname, ltext in LIGHTS.items():
                for fname, ftext in FUSIONS.items():
                    key = f"{rname}|{sname}|{lname}|{fname}"
                    if rname == "无参考":
                        people = rcfg["id_text"]
                    else:
                        people = "画面中恰好两名男性船员：船长与大副（外观严格与参考图一致）。"
                    size_text = {
                        "MCU": "中近景：船长在舵轮前双手扶舵占画面一侧，大副在另一侧弯腰整理缆绳，两人上半身充满画面",
                        "MS": "中景：船长站在船尾舵轮前，大副在船中部弯腰整理缆绳，两人全身膝上可见，甲板环境清楚",
                        "WS": "远景：从船尾后上方看，整艘船的后半部与两名船员的小全身像，海面与天空占画面大部分",
                    }[sname]
                    prompt = (f"{BASE_SCENE}{ssize}。{people}{size_text}。{ltext}。"
                              f"{ftext} 写实电影质感，35mm 胶片感。")
                    conds[key] = {"prompt": prompt, "refs": rcfg["refs"]}
    return conds


CONDITIONS = build_conditions()
SEEDS_PER_COND = 17


def _client():
    import yaml
    import deps
    import os
    from src.api.agnes_client import AgnesClient
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    cfg = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))
    agnes_cfg = dict(cfg.get("agnes") or {})
    env = {}
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    if "${AGNES_API_KEY}" in str(agnes_cfg.get("api_key") or ""):
        agnes_cfg["api_key"] = env.get("AGNES_API_KEY", "")
        os.environ.setdefault("GITHUB_TOKEN", env.get("GITHUB_TOKEN", ""))
    deps._APP_CFG = cfg
    RateLimiter.get(cfg.get("agnes", {}).get("rpm"))
    quota = QuotaLedger.get_default(Path("cache/quota.json"), limits=cfg.get("quota"))
    return AgnesClient(agnes_cfg, quota=quota)


def _log(f, rec):
    with open(f, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")


def build_queue():
    """轮转队列：pass0 = 每条件第 1 个 seed（相邻不同条件）……直到 pass16。"""
    q = []
    conds = list(CONDITIONS.keys())
    for seed_idx in range(SEEDS_PER_COND):
        for key in conds:
            q.append((key, seed_idx))
    return q


async def gen_worker(wid, c, queue, stop_flag):
    while queue and not stop_flag.exists():
        key, seed_idx = queue.pop(0)
        cond = CONDITIONS[key]
        safe = key.replace("|", "_").replace(" ", "")
        fname = f"{safe}__s{seed_idx}.png"
        dest = IMG / fname
        if dest.exists():
            continue
        # seed 限 [-1,999]（AGNES_API_FACTS）——确定性映射进范围内
        seed = (seed_idx * 7 + abs(hash(key)) % 5) % 1000
        t0 = time.monotonic()
        try:
            r = await c.generate_image(cond["prompt"], image_refs=cond["refs"] or None,
                                       size_tier="1K", ratio="16:9", seed=seed)
            url = r.get("url") or ""
            if url:
                dl_ok = False
                for attempt in range(3):
                    try:
                        # 🔴 必须用异步 download——同步 urlretrieve 会阻塞事件循环，
                        # 把 3 worker 退化成串行（首轮实测 3.1/min vs 理论 ~11/min）
                        await c.download(url, dest)
                        dl_ok = dest.exists() and dest.stat().st_size > 1000
                        break
                    except Exception as de:
                        await asyncio.sleep(3 * (attempt + 1))
                ok = dl_ok
            else:
                ok = False
            _log(LEDGER, {"cond": key, "seed_idx": seed_idx, "seed": seed,
                          "ok": ok, "url": url[:120], "local": str(dest) if ok else "",
                          "elapsed_s": round(time.monotonic() - t0, 1),
                          "worker": wid, "ts": time.time()})
        except Exception as e:
            _log(LEDGER, {"cond": key, "seed_idx": seed_idx, "seed": seed, "ok": False,
                          "error": f"{type(e).__name__}: {str(e)[:120]}",
                          "elapsed_s": round(time.monotonic() - t0, 1),
                          "worker": wid, "ts": time.time()})
            await asyncio.sleep(2)


async def main_gen(n_workers=3):
    c = _client()
    queue = build_queue()
    print(f"queue: {len(queue)} items, {len(CONDITIONS)} conditions", flush=True)
    stop = BASE / "STOP"
    if stop.exists():
        stop.unlink()
    # 3 并发 worker 共享队列（image_1k 17 RPM 上限内：3 worker × ~10s/张 ≈ 18 RPM
    # 会被限流器节流到 17——并发收益来自延迟重叠）
    await asyncio.gather(*[gen_worker(i, c, queue, stop) for i in range(n_workers)])
    print("generation done, remaining:", len(queue), flush=True)




# ---- 贴图感视觉评分（核心质量度量；text 桶）----
SCREEN_PROMPT = (
    "你是合成感（贴图感）审查员。看这张图里的人物与场景：\n"
    "1. 贴图感评分（1-5）：1=人物完全融入场景（像同一张照片一次拍成）；"
    "3=可察觉轻微合成痕迹；5=明显贴图（人物像剪贴上去的，边缘光晕/受光方向错误/"
    "清晰度突变/透视不匹配）。\n"
    "2. 受光一致性（1-5）：人物受光方向与场景光源是否一致。\n"
    "3. 人数：画面里有几个人（0/1/2/3+）。\n"
    "4. 硬错误：分身/多余的人/肢体异常（有/无）。\n"
    "只输出 JSON：{\"sticker\": 1-5, \"light\": 1-5, \"people\": n, "
    "\"hard_error\": true/false}，无其他文字。")


async def screen_worker(wid, c, done_set, stop_flag):
    from src.api.agnes_client import AgnesError
    while not stop_flag.exists():
        todo = None
        for f in sorted(IMG.glob("*.png")):
            if f.name not in done_set:
                todo = f
                break
        if todo is None:
            return
        b64 = base64.b64encode(todo.read_bytes()).decode()
        try:
            r = await c.chat_completion_vision(
                [{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": SCREEN_PROMPT}]}],
                model="agnes-2.5-flash", thinking=True, max_tokens=3000)
            # chat_completion_vision 返回顶层 content（非 choices 结构）；
            # thinking 占满 max_tokens 时 content 可能为空——空=不入 done（重试）
            text = r.get("content") or ""
            m = re.search(r"\{[^}]+\}", text, re.DOTALL)
            v = json.loads(m.group()) if m else {}
            if v.get("sticker") is None:
                await asyncio.sleep(1)
                continue   # 空解析：不加 done，下轮重试
            done_set.add(todo.name)
            _log(SCREEN, {"image": todo.name, "sticker": v.get("sticker"),
                          "light": v.get("light"), "people": v.get("people"),
                          "hard_error": v.get("hard_error"), "raw": text[:160]})
        except AgnesError as e:
            _log(SCREEN, {"image": todo.name, "error": str(e)[:100]})
            await asyncio.sleep(5)
        except Exception as e:
            _log(SCREEN, {"image": todo.name, "error": f"{type(e).__name__}: {str(e)[:80]}"})
            await asyncio.sleep(2)


async def main_screen(n_workers=2):
    c = _client()
    done = set()
    if SCREEN.exists():
        for l in SCREEN.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(l)
                if d.get("sticker") is not None:
                    done.add(d.get("image"))
            except Exception:
                pass
    stop = BASE / "STOP"
    if stop.exists():
        stop.unlink()
    print(f"screening: {len(done)} already done", flush=True)
    await asyncio.gather(*[screen_worker(i, c, done, stop) for i in range(n_workers)])
    print("screening done", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "gen"
    if mode == "gen":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        asyncio.new_event_loop().run_until_complete(main_gen(n))
    elif mode == "screen":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
        asyncio.new_event_loop().run_until_complete(main_screen(n))
