# -*- coding: utf-8 -*-
"""Phase51 C 图片测试战役（用户授权：图片数千张可用，每批换样式多角度测试）。

目标 1（为明日视频修复备料）：s05_c03 / s04_c04 首帧重建——4 个变体 × 8 seeds：
  V1 锁定描述直出 / V2 负向转正向重述 / V3 世界状态参考锚定（同片尾帧）/ V4 构图语言变换
目标 2（一致性门原型测试）：全部候选经视觉模型（agnes-2.5-flash，text 桶）做
  「描述锁定核对」（码头/沙滩/陆地/帆船检测），与执行侧目检对照——验证门Prototype。

纪律：系统同一 AgnesClient（限流+配额）；请求摘要+结果落 _p51_imgtest/ledger.jsonl；
视觉筛选结果落 screen.jsonl；产出 contact-sheet 供执行侧复核。
"""
import asyncio
import base64
import io
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = Path("_p51_imgtest")
BASE.mkdir(exist_ok=True)
LEDGER = BASE / "ledger.jsonl"
SCREEN = BASE / "screen.jsonl"

# ---- 锁定描述（P50_C LOCKED_STORYBOARD，节选关键约束）----
DESC_503 = ("大远景。机位在地面高度贴近海面。海面与天空占画面九成。一艘很小很小的"
            "机动渔船（只占画面宽度十分之一）在画面中左偏，朝远处地平线缓慢驶去，"
            "身后拖着一条细长白色尾迹向右后延伸。船上有两个小小黑色人影。远处海平线"
            "处几盏暖色港口灯光。写实电影质感，暮色。")
DESC_504 = ("暗礁浅水区远景：水下怪物的触须在浅水处受阻，拍打浅水但够不到船，"
            "被迫留在后方。一艘深蓝色木质渔船擦过两块礁石后冲出浅水区，向开阔海面"
            "驶去。写实电影质感，暮色。")

VARIANTS = {
    # s05_c03 四变体（每批换样式——用户要求的多角度）
    "s05c03_V1_直出": {"prompt": DESC_503, "refs": []},
    "s05c03_V2_正向重述": {"prompt": (
        "远离一切陆地的开阔大洋中央，暮色。海面与天空占据画面的绝大部分，"
        "一艘极小的机动渔船在海平面附近航行，拖出细长白尾迹，船上两个小小人影。"
        "海平线处几点暖色灯火（只是远处灯光，不是码头）。画面里除了这艘船和海水"
        "天空没有任何其他物体。写实电影质感。"), "refs": []},
    "s05c03_V3_参考锚定": {"prompt": (
        "延续这张画面的世界状态（同一片海、同一艘深蓝色木质渔船、同一暮色与远处"
        "灯火）：镜头大幅拉远到海面高度的极远景，小船朝地平线缓慢驶去，尾迹细长，"
        "船上两个小小人影。画面只有海、天空、小船、灯火。写实电影质感。"),
        "refs": ["ref_s05c02_end.png"]},
    "s05c03_V4_鸟瞰构图": {"prompt": (
        "高空航拍俯瞰，暮色：广阔无边的深蓝色海面占满整个画面，一艘极小的渔船"
        "（不到画面宽度十分之一）在海面留下细长的白色 V 形尾迹。地平线处有一串"
        "暖色小灯点。画面里只有海水和这艘船。写实电影质感，航拍摄影。"), "refs": []},
    # s04_c04 四变体
    "s04c04_V1_直出": {"prompt": DESC_504, "refs": []},
    "s04c04_V2_船尾回望": {"prompt": (
        "从渔船船尾向回望的视角，暮色写实电影：船刚刚冲出一片礁石浅水区，"
        "礁石间水很浅、白浪翻滚；远处浅水中，巨型触须海怪的部分触须在浅水里"
        "拍打挣扎——水太浅，它庞大的身躯进不来，只能留在后面。深蓝色渔船的"
        "船尾占画面下缘。画面里只有船、礁石、海水、怪物触须。"), "refs": []},
    "s04c04_V3_参考锚定": {"prompt": (
        "延续这张画面的世界状态（同一片礁石浅水区、同一艘深蓝色木质渔船、"
        "同一暮色）：镜头切换到船尾方向回望——远处浅水区里，巨型触须海怪的"
        "触须在浅水中拍打受阻（水太浅进不来），深蓝色渔船正驶向开阔海面。"
        "画面里只有船、礁石、海水、怪物。写实电影质感。"),
        "refs": ["ref_s04c03_end.png"]},
    "s04c04_V4_水下视角": {"prompt": (
        "半水面视角（一半水下一半水上），暮色写实电影：水面下清晰可见巨型触须"
        "搁浅在浅色沙洲礁盘上、无法前进，触须在浅水中拍打；水面上方，一艘深蓝色"
        "木质渔船正远离这片浅水区驶向开阔深海。画面里只有海水、触须、礁盘、渔船。"),
        "refs": []},
}
SEEDS = [11, 23, 37, 51, 66, 79, 88, 97]


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
        os.environ.setdefault("SENSENOVA_API_KEY", env.get("SENSENOVA_API_KEY", ""))
    deps._APP_CFG = cfg
    RateLimiter.get(cfg.get("agnes", {}).get("rpm"))
    quota = QuotaLedger.get_default(Path("cache/quota.json"), limits=cfg.get("quota"))
    return AgnesClient(agnes_cfg, quota=quota)


def _log(rec):
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def gen_batch(c):
    # 参考图先 rehost
    ref_urls = {}
    for name in ("ref_s05c02_end.png", "ref_s04c03_end.png"):
        local = str(BASE / name)
        url = await (await _rehost()).upload(local)
        ref_urls[name] = url
        _log({"tag": name, "step": "rehost", "url": url[:120]})
    total = 0
    for vname, cfg in VARIANTS.items():
        refs = [ref_urls[r] for r in cfg["refs"]]
        for seed in SEEDS:
            tag = f"{vname}_s{seed}"
            t0 = time.monotonic()
            try:
                r = await c.generate_image(cfg["prompt"], image_refs=refs or None,
                                           size_tier="1K", ratio="16:9", seed=seed)
                url = r.get("url") or ""
                local = ""
                if url:
                    dest = BASE / f"{tag}.png"
                    urllib.request.urlretrieve(url, dest)
                    local = str(dest)
                    total += 1
                _log({"tag": tag, "ok": True, "url": url[:140], "local": local,
                      "elapsed_s": round(time.monotonic() - t0, 1)})
            except Exception as e:
                _log({"tag": tag, "ok": False,
                      "error": f"{type(e).__name__}: {str(e)[:140]}"})
            await asyncio.sleep(0.5)
    print("generated:", total)


_rehost_host = None
async def _rehost():
    global _rehost_host
    if _rehost_host is None:
        from deps import get_rehost_host
        _rehost_host = get_rehost_host()
    return _rehost_host


# ---- 一致性门原型：视觉模型描述锁定核对（text 桶）----
SCREEN_PROMPT = (
    "仔细看这张图片，只回答以下检查项（每项回答 有/无/不确定）：\n"
    "1. 码头/栈桥/木质平台（人物可站立的人造地面）\n"
    "2. 沙滩或沙洲\n"
    "3. 绿色植被覆盖的陆地或山丘\n"
    "4. 除画面主船以外的其他船只（帆船/轮船）\n"
    "5. 建筑物\n"
    "最后用一行 JSON 总结：{\"dock\":..,\"beach\":..,\"vegetation\":..,"
    "\"other_ships\":..,\"buildings\":..}，值为 true/false/uncertain。")

FORBIDDEN = {"dock", "beach", "vegetation", "other_ships", "buildings"}


async def screen_all(c):
    """对全部候选做描述锁定核对（视觉模型 agnes-2.5-flash，thinking 开启）。"""
    results = []
    imgs = sorted(BASE.glob("s05c03_*.png")) + sorted(BASE.glob("s04c04_*.png"))
    for f in imgs:
        b64 = base64.b64encode(f.read_bytes()).decode()
        try:
            r = await c.chat_completion_vision(
                [{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": SCREEN_PROMPT}]}],
                model="agnes-2.5-flash", thinking=True)
            text = (r.get("choices") or [{}])[0].get("message", {}).get("content", "")
            m = re.search(r"\{[^}]+\}", text)
            verdict = json.loads(m.group()) if m else {}
            bad = [k for k in FORBIDDEN if verdict.get(k) is True]
            results.append({"image": f.name, "verdict": verdict, "bad": bad,
                            "raw": text[:200]})
            with open(SCREEN, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(results[-1], ensure_ascii=False) + "\n")
        except Exception as e:
            results.append({"image": f.name, "error": str(e)[:120]})
            with open(SCREEN, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(results[-1], ensure_ascii=False) + "\n")
    n_bad = sum(1 for r in results if r.get("bad"))
    print(f"screened {len(results)}: flagged {n_bad}")


if __name__ == "__main__":
    c = _client()
    if "gen" in sys.argv:
        asyncio.new_event_loop().run_until_complete(gen_batch(c))
    if "screen" in sys.argv:
        asyncio.new_event_loop().run_until_complete(screen_all(c))
