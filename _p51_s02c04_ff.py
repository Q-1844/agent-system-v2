# Phase51-E：s02_c04 首帧重建（发现级：s02_c04 首帧与分镜严重不符——帆船时代错误
# +海怪巨大具象立起，分镜要求"水下暗影"。i2v 忠实锚定→C-T03 视频层帆船瑕疵）。
# 配方：§11 N_lock（无参考+详细身份锁定，复杂叙事镜头实证贴图感 1.57 最优）+§9.2
# 无参考组。6 seeds → 一致性门+目检 → 选 1 张经 E1 同款脚本切 selected。
import asyncio, json, sys, io, time
sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

BASE = Path("_p51_s02c04_ff")
BASE.mkdir(exist_ok=True)
LEDGER = Path("cache/p50_timing_ledger.jsonl")

PROMPT = (
    "极端大远景，写实电影风格，机位贴近海面。黄昏光线变暗，乌云密布。"
    "一艘很小的深蓝色木质渔船（占画面宽度不足八分之一）在画面下方中央全速航行，"
    "白色尾迹向右后方拉长；甲板上无人可见（远景不可读）。"
    "更远处的海面下方一道巨大的暗色水下阴影正缓慢上浮，只有朦胧的轮廓和隐约的触须凸起可辨，"
    "阴影所到之处水色变黑；阴影与船之间隔着一片开阔水面。"
    "画面中只有这一艘渔船：没有帆船，没有其他任何船只，没有陆地，没有岛屿，没有码头，没有礁石出露。"
    "海面有细碎波浪，天空阴沉。"
)

SEEDS = [11, 23, 37, 51, 66, 79]


def _client():
    import yaml
    import deps
    from src.api.agnes_client import AgnesClient
    from src.core.rate_limiter import RateLimiter
    cfg = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))
    agnes_cfg = dict(cfg.get("agnes") or {})
    if "${AGNES_API_KEY}" in str(agnes_cfg.get("api_key") or ""):
        env = {}
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        agnes_cfg["api_key"] = env.get("AGNES_API_KEY", "")
    deps._APP_CFG = cfg
    RateLimiter.get(cfg.get("agnes", {}).get("rpm"))
    from src.core.quota import QuotaLedger
    quota = QuotaLedger.get_default(Path("cache/quota.json"), limits=cfg.get("quota"))
    return AgnesClient(agnes_cfg, quota=quota)


async def main():
    c = _client()
    sem = asyncio.Semaphore(3)
    out = []

    async def one(seed):
        async with sem:
            t0 = time.monotonic()
            try:
                r = await c.generate_image(PROMPT, size_tier="1K", ratio="16:9", seed=seed)
                url, b64 = r.get("url"), r.get("b64")
                local = BASE / f"s02c04_nlock_s{seed}.png"
                if b64:
                    import base64
                    local.write_bytes(base64.b64decode(b64))
                elif url:
                    import httpx
                    async with httpx.AsyncClient(timeout=60) as hc:
                        rr = await hc.get(url)
                        local.write_bytes(rr.content)
                rec = {"tag": f"s02c04_nlock_s{seed}", "ok": bool(url or b64),
                       "url": (url or "")[:150], "local": str(local),
                       "elapsed_s": round(time.monotonic() - t0, 1)}
            except Exception as e:
                rec = {"tag": f"s02c04_nlock_s{seed}", "ok": False,
                       "err": f"{type(e).__name__}: {str(e)[:140]}",
                       "elapsed_s": round(time.monotonic() - t0, 1)}
            out.append(rec)
            with LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"event": "s02c04_ff_rebuild", "utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "project": "P50_B",
                    "shot_id": "s02_c04", **rec}, ensure_ascii=False) + "\n")
            print(json.dumps(rec, ensure_ascii=False)[:180])

    await asyncio.gather(*(one(s) for s in SEEDS))


if __name__ == "__main__":
    asyncio.run(main())
