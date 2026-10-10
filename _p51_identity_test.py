# -*- coding: utf-8 -*-
"""Phase51 C 跨镜身份一致性测试（用户核心问题：无参考能否保持人物一致性）。

设计：模拟 6 镜小序列（景别/光线/动作变化=生产实际），同一份固定文本身份描述
逐镜生成；对照组=双 Look 现行路径。然后用视觉模型做跨图配对一致性评分：
  - 同角色跨镜对（如 s02 船长特写 vs s06 船长特写）：面部一致性 1-5
  - 文本忠实度：图中人物是否符合固定描述
策略：
  N1 无参考+简短身份描述
  N2 无参考+详细结构化身份描述（逐字相同的固定描述块）
  N3 = N2 + 「与之前画面相同的两个人」锚定句
  D1 双 Look 现行路径（对照组）
每策略 6 镜 × 6 seeds = 36 张；四策略共 144 张（图片额度充足）。
"""
import asyncio
import base64
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path("_p51_identity")
IMG = BASE / "images"
LEDGER = BASE / "ledger.jsonl"
PAIRS = BASE / "pairs.jsonl"
for p in (IMG,):
    p.mkdir(parents=True, exist_ok=True)

REF_CAPTAIN = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_aF380vb3j2t9Y0f1ozdDKzDjL7d9XSNT/output.png"
REF_MATE = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_8idQeQTKxQCk9eyWKh5D2hWTmWwtyDP1/output.png"

# ---- 固定文本身份描述（跨镜逐字相同=「固定素材」）----
ID_SHORT = "年长的船长与年轻的大副在渔船甲板上。"
ID_DETAIL = (
    "两名男性船员，外貌严格如下——船长：约55岁，灰白色短发，短胡茬，"
    "方脸、深目、鼻梁高挺，穿灰蓝色防水工装外套（做旧磨损、肘部补丁）、深色裤子；"
    "大副：约28岁，深棕色蓬松短发，无胡须，瘦长脸、浓眉，穿军绿色工装衬衫内白T恤，"
    "左臂有深色纹身，深蓝牛仔裤。两人面孔差异明显：一个年长一个年轻，绝不混淆。")
ANCHOR = "画面中的两个人与同一故事里其他画面中的两人是同一个人（同一张脸、同一套衣服、同一艘船）。"

SHOTS = [
    ("sh1", "WS·黄昏·远景", "黄昏开阔海面，深蓝色木质渔船全速行驶的侧面远景，甲板上两个小小人影（船长在舵位、大副在船首），白色尾迹，暖橙天空"),
    ("sh2", "MCU·暮色·船长特写", "中近景：船长（年长者）在舵轮前双手扶舵，抬头望向远方，表情警觉，额头有汗；甲板背景虚化"),
    ("sh3", "MCU·暮色·大副特写", "中近景：大副（年轻人）在前甲板直起身回望船长方向，表情紧张，手里抓着缆绳；海面背景"),
    ("sh4", "MS·夜·双人配合", "中景：夜间，船长操舵同时大副在旁递工具，两人配合调整航向，驾驶舱灯光照亮面部"),
    ("sh5", "WS·风暴·全速逃离", "远景：风暴云下的渔船侧面全速行驶，浪花飞溅，甲板上两个小人影在忙碌"),
    ("sh6", "MCU·夜·船长决断", "中近景：船长在舵轮前猛打舵，侧脸被驾驶舱灯光照亮，眼神决断；与此前同一人同一服装"),
]

STRATS = {
    "N1_简短描述": {"refs": [], "id": ID_SHORT, "anchor": ""},
    "N2_详细锁定": {"refs": [], "id": ID_DETAIL, "anchor": ""},
    "N3_详细加锚定": {"refs": [], "id": ID_DETAIL, "anchor": ANCHOR},
    "D1_双Look现行": {"refs": [REF_CAPTAIN, REF_MATE], "id": "", "anchor": ""},
}


def build_jobs():
    jobs = []
    for sname, scfg in STRATS.items():
        for shot_id, shot_label, shot_text in SHOTS:
            for seed in (11, 23, 37, 51, 66, 79):
                people = (scfg["id"] + " " + scfg["anchor"]).strip()
                prompt = (f"{shot_text}。{people} 写实电影质感，35mm 胶片感。")
                jobs.append({"strat": sname, "shot": shot_id, "shot_label": shot_label,
                             "seed": seed, "prompt": prompt, "refs": scfg["refs"]})
    return jobs


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


async def gen(c):
    from deps import get_rehost_host
    host = get_rehost_host()
    jobs = build_jobs()
    print(f"jobs: {len(jobs)}", flush=True)
    # 参考图直接用原 provider 公网 URL（Agnes 服务端可直取；rehost 反而引入
    # github_pages 422 瞬时故障面——run 实证两次）
    ref_urls = {r: r for r in {rr for j in jobs for rr in j["refs"]}}
    queue = [j for j in jobs if not (IMG / f"{j['strat']}_{j['shot']}_s{j['seed']}.png").exists()]
    print(f"queue after resume-skip: {len(queue)}", flush=True)
    stop = BASE / "STOP"

    async def worker(wid):
        while queue and not stop.exists():
            j = queue.pop(0)
            fname = f"{j['strat']}_{j['shot']}_s{j['seed']}.png"
            dest = IMG / fname
            if dest.exists():
                continue
            t0 = time.monotonic()
            try:
                refs = [ref_urls[r] for r in j["refs"]] or None
                r = await c.generate_image(j["prompt"], image_refs=refs,
                                           size_tier="1K", ratio="16:9", seed=j["seed"])
                url = r.get("url") or ""
                if url:
                    await c.download(url, dest)
                ok = dest.exists() and dest.stat().st_size > 1000
                _log(LEDGER, {**{k: j[k] for k in ("strat", "shot", "seed")}, "ok": ok,
                              "local": str(dest) if ok else "", "url": url[:100],
                              "elapsed_s": round(time.monotonic() - t0, 1), "worker": wid})
            except Exception as e:
                _log(LEDGER, {**{k: j[k] for k in ("strat", "shot", "seed")}, "ok": False,
                              "error": f"{type(e).__name__}: {str(e)[:110]}"})
                await asyncio.sleep(2)

    await asyncio.gather(*[worker(i) for i in range(3)])
    print("generation done", flush=True)


# ---- 跨镜配对一致性评分（视觉模型，thinking 开启）----
PAIR_PROMPT = (
    "两张图里各有一名男性船员。判断：这两张图里的「船长」（年长者）或「大副」"
    "（年轻人）是否为同一个人？只依据面部特征（脸型/五官/胡须/发型/年龄）与服装，"
    "不考虑画质与角度差异。\n只输出 JSON：{\"same_person\": true/false, "
    "\"face_similarity\": 1-5, \"clothing_same\": true/false}，无其他文字。")


async def pairs(c):
    """同角色跨镜对评分：每策略内，sh2(船长特写) vs sh6(船长特写)、sh3 vs sh4 的大副。"""
    imgs = {}
    for f in sorted(IMG.glob("*.png")):
        m = re.match(r"(.+)_(sh\d)_s\d+\.png", f.name)
        if m:
            imgs[(m.group(1), m.group(2))] = f
    checks = []
    for strat in STRATS:
        if (strat, "sh2") in imgs and (strat, "sh6") in imgs:
            checks.append((strat, "船长跨镜(sh2 vs sh6)", imgs[(strat, "sh2")], imgs[(strat, "sh6")]))
        if (strat, "sh3") in imgs and (strat, "sh4") in imgs:
            checks.append((strat, "大副跨镜(sh3 vs sh4)", imgs[(strat, "sh3")], imgs[(strat, "sh4")]))
    for strat, label, fa, fb in checks:
        b64a = base64.b64encode(fa.read_bytes()).decode()
        b64b = base64.b64encode(fb.read_bytes()).decode()
        got = False
        for attempt in range(6):
            try:
                r = await c.chat_completion_vision(
                    [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64a}"}},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64b}"}},
                        {"type": "text", "text": PAIR_PROMPT}]}],
                    model="agnes-2.5-flash", thinking=True, max_tokens=3000)
                text = r.get("content") or ""
                m = re.search(r"\{[^}]+\}", text, re.DOTALL)
                v = json.loads(m.group()) if m else {}
                if not v:
                    raise ValueError("empty content")
                _log(PAIRS, {"strat": strat, "pair": label, "a": fa.name, "b": fb.name,
                             **v, "raw": text[:120]})
                print(f"{strat} {label}: same={v.get('same_person')} face={v.get('face_similarity')}", flush=True)
                got = True
                break
            except Exception as e:
                wait = 60 * (attempt + 1)
                print(f"  {strat} {label} attempt{attempt+1} failed ({str(e)[:60]}) wait {wait}s", flush=True)
                await asyncio.sleep(wait)
        if not got:
            _log(PAIRS, {"strat": strat, "pair": label, "a": fa.name, "b": fb.name,
                         "error": "6 attempts exhausted"})
        await asyncio.sleep(15)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "gen"
    c = _client()
    if mode == "gen":
        asyncio.new_event_loop().run_until_complete(gen(c))
    elif mode == "pairs":
        asyncio.new_event_loop().run_until_complete(pairs(c))
