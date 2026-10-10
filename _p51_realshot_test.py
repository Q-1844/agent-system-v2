# -*- coding: utf-8 -*-
"""Phase51 C 复杂镜头首帧测试（用户授权）：真实克苏鲁追击 20 镜 × 身份策略对照。

问题：复杂叙事镜头（怪物揭示/触须拍击/擦礁/抛索/风暴）的首帧，
      无参考+详细身份锁定（N-lock）会不会比现行双 Look 路径（D-ref）更好？
度量：贴图感评分 + 人数 + 描述锁定核对 + 并排 contact-sheet（N vs D vs 现行 approved 首帧）。
每镜 2 策略 × 2 seeds = 80 张。固定身份描述块=身份测试 N2 版（逐字相同）。"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path("_p51_realshot")
IMG = BASE / "images"
LEDGER = BASE / "ledger.jsonl"
SCREEN = BASE / "screen.jsonl"
IMG.mkdir(parents=True, exist_ok=True)

REF_CAPTAIN = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_aF380vb3j2t9Y0f1ozdDKzDjL7d9XSNT/output.png"
REF_MATE = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_8idQeQTKxQCk9eyWKh5D2hWTmWwtyDP1/output.png"
REF_BOAT = "https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_Y7K4sqXAL6RWaNdBxMmZwGe18QNhwcNH/output.png"

ID_DETAIL = (
    "两名男性船员，外貌严格如下——船长：约55岁，灰白色短发，短胡茬，"
    "方脸、深目、鼻梁高挺，穿灰蓝色防水工装外套（做旧磨损、肘部补丁）、深色裤子；"
    "大副：约28岁，深棕色蓬松短发，无胡须，瘦长脸、浓眉，穿军绿色工装衬衫内白T恤，"
    "左臂有深色纹身，深蓝牛仔裤。两人面孔差异明显：一个年长一个年轻，绝不混淆。")
POP = "画面中恰好出现描述里的人物，绝不增加其他人，绝不缺少描述中应在场的人。"


def load_locked():
    d = json.load(open("_p50_c_package/LOCKED_STORYBOARD.json", encoding="utf-8"))
    return {s["shot_id"]: s for s in d["shots"]}


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


def build_jobs():
    locked = load_locked()
    jobs = []
    for sid, s in sorted(locked.items()):
        desc = s.get("description") or ""
        chars = s.get("characters_expected") or []
        has_people = any("人" in c for c in chars) or "人影" in desc or "船员" in desc
        for strat in ("N_lock", "D_ref"):
            for seed in (11, 37):
                if strat == "N_lock":
                    ident = (ID_DETAIL + " " + POP + " ") if has_people else ""
                    prompt = f"{desc} {ident}写实电影质感，35mm 胶片感。"
                    refs = []
                else:
                    ident = ""
                    prompt = f"{desc} 写实电影质感，35mm 胶片感。"
                    refs = []
                    if "船长" in desc or "大副" in desc:
                        refs += [REF_CAPTAIN, REF_MATE]
                    elif has_people:
                        refs += [REF_CAPTAIN, REF_MATE]
                    if "渔船" in desc or "船" in desc:
                        refs += [REF_BOAT]
                jobs.append({"shot": sid, "strat": strat, "seed": seed,
                             "prompt": prompt, "refs": refs, "has_people": has_people})
    return jobs


async def gen(c):
    from deps import get_rehost_host
    host = get_rehost_host()
    jobs = build_jobs()
    queue = [j for j in jobs
             if not (IMG / f"{j['shot']}_{j['strat']}_s{j['seed']}.png").exists()]
    print(f"jobs: {len(jobs)} | queue: {len(queue)}", flush=True)

    async def worker(wid):
        while queue:
            j = queue.pop(0)
            dest = IMG / f"{j['shot']}_{j['strat']}_s{j['seed']}.png"
            if dest.exists():
                continue
            t0 = time.monotonic()
            try:
                refs = None
                if j["refs"]:
                    # 参考已是 provider 公网 URL——直接用（github_pages rehost 今日
                    # 不稳，provider 抓取 rehost 链接超时 504；原链已实证可抓取）
                    refs = list(j["refs"])
                r = await c.generate_image(j["prompt"], image_refs=refs,
                                           size_tier="1K", ratio="16:9", seed=j["seed"])
                url = r.get("url") or ""
                if url:
                    await c.download(url, dest)
                ok = dest.exists() and dest.stat().st_size > 1000
                _log(LEDGER, {**{k: j[k] for k in ("shot", "strat", "seed")}, "ok": ok,
                              "elapsed_s": round(time.monotonic() - t0, 1)})
            except Exception as e:
                _log(LEDGER, {**{k: j[k] for k in ("shot", "strat", "seed")}, "ok": False,
                              "error": f"{type(e).__name__}: {str(e)[:110]}"})
                await asyncio.sleep(2)

    await asyncio.gather(*[worker(i) for i in range(3)])
    print("generation done", flush=True)


SCREEN_PROMPT = (
    "你是首帧质量审查员。看这张图：\n"
    "1. 贴图感评分（1-5）：1=人物完全融入场景；3=轻微合成痕迹；5=明显贴图。\n"
    "2. 人数：画面里清晰可见的人物数（0/1/2/3+）。\n"
    "3. 硬错误：分身/多余的人/肢体异常（有/无）。\n"
    "4. 叙事要素命中：图中是否包含【怪物或其触须】【礁石】【渔船】中的哪些？\n"
    "只输出 JSON：{\"sticker\":1-5,\"people\":n,\"hard_error\":bool,"
    "\"has_monster\":bool,\"has_reef\":bool,\"has_boat\":bool}。")


async def screen(c):
    done = set()
    if SCREEN.exists():
        for l in SCREEN.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(l)
                if d.get("sticker") is not None:
                    done.add(d.get("image"))
            except Exception:
                pass
    todo = [f for f in sorted(IMG.glob("*.png")) if f.name not in done]
    print(f"screening: {len(todo)} todo / {len(done)} done", flush=True)
    for f in todo:
        import base64
        b64 = base64.b64encode(f.read_bytes()).decode()
        try:
            r = await c.chat_completion_vision(
                [{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": SCREEN_PROMPT}]}],
                model="agnes-2.5-flash", thinking=True, max_tokens=3000)
            text = r.get("content") or ""
            m = re.search(r"\{[^}]+\}", text, re.DOTALL)
            v = json.loads(m.group()) if m else {}
            _log(SCREEN, {"image": f.name, **v})
        except Exception as e:
            _log(SCREEN, {"image": f.name, "error": str(e)[:90]})
        await asyncio.sleep(0.5)
    print("screening done", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "gen"
    c = _client()
    if mode == "gen":
        asyncio.new_event_loop().run_until_complete(gen(c))
    elif mode == "screen":
        asyncio.new_event_loop().run_until_complete(screen(c))
