# Phase51-E V-A3：收尾补测（2 条 V-A2 失败重试 + 6 条成功项扩样 seed，64s→488s）
import asyncio, json, sys, io, time, os
sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

LEDGER = Path("cache/p50_timing_ledger.jsonl")
FIRST_URL = "https://Q-1844.github.io/agnes-frame-host/objects/b9e9b194ea01d003.png"
IDENT_ZH = ("草原石屋：石灰岩石墙刷白灰泥，正面正中一扇竖纹木板门，门两侧各一扇小方窗，"
            "一侧山墙有高出屋顶的石砌烟囱，屋前南侧有低木栅栏，四周开阔草原，写实电影风格")
IDENT_EN = ("stone cottage: limestone walls whitewashed, single plank door on front, "
            "two small windows, stone chimney at side gable, wooden fence, open grassland")

CONDS = [
    {"tag": "VA2_i2v_rearA_s11_retry", "route": "i2v", "seed": 11,
     "prompt": "摄影机平稳地绕到房子的正后方并停住，最终画面完全是房子的背面：一面干净的白石灰后墙占据画面主体，"
               "后墙上没有任何门和窗，正面和木门完全看不见，只能看到后墙、屋顶背面轮廓和周边草地。" + IDENT_ZH},
    {"tag": "VA2_i2v_rearB_s51_retry", "route": "i2v", "seed": 51,
     "prompt": "镜头水平横移越过房子侧面后继续移动，直到从房子背后拍摄：画面主体=无门窗的白灰后墙，"
               "木门和正面彻底离开画面，视频结尾停留在背面视角。" + IDENT_ZH},
    {"tag": "VA3_i2v_orbit_en_s37", "route": "i2v", "seed": 37,
     "prompt": "the camera starts facing the cottage front, then orbits steadily to the right, "
               "turning continuously until the SIDE ELEVATION of the house becomes the main subject "
               "and the front facade rotates out of frame, revealing the full side wall and the stone chimney. " + IDENT_EN},
    {"tag": "VA3_i2v_orbit_en_s79", "route": "i2v", "seed": 79,
     "prompt": "the camera starts facing the cottage front, then orbits steadily to the right, "
               "turning continuously until the SIDE ELEVATION of the house becomes the main subject "
               "and the front facade rotates out of frame, revealing the full side wall and the stone chimney. " + IDENT_EN},
    {"tag": "VA3_t2v_overhead_en_s37", "route": "t2v", "seed": 37,
     "prompt": "the camera rises straight up, the viewing angle getting steeper, ending in a "
               "TOP-DOWN overhead shot directly above the cottage, looking vertically down at "
               "the rooftop, chimney top and the surrounding grass. " + IDENT_EN},
    {"tag": "VA3_t2v_overhead_en_s79", "route": "t2v", "seed": 79,
     "prompt": "the camera rises straight up, the viewing angle getting steeper, ending in a "
               "TOP-DOWN overhead shot directly above the cottage, looking vertically down at "
               "the rooftop, chimney top and the surrounding grass. " + IDENT_EN},
    {"tag": "VA3_i2v_overhead_zh_s37", "route": "i2v", "seed": 37,
     "prompt": "镜头垂直升到房子正上方，俯视角度越来越陡，最终变成从正上方垂直向下看屋顶的画面"
               "（屋顶、烟囱顶部、周边草地从上方可见）。" + IDENT_ZH},
    {"tag": "VA3_i2v_overhead_zh_s79", "route": "i2v", "seed": 79,
     "prompt": "镜头垂直升到房子正上方，俯视角度越来越陡，最终变成从正上方垂直向下看屋顶的画面"
               "（屋顶、烟囱顶部、周边草地从上方可见）。" + IDENT_ZH},
]


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
        os.environ.setdefault("GITHUB_TOKEN", env.get("GITHUB_TOKEN", ""))
    deps._APP_CFG = cfg
    RateLimiter.get(cfg.get("agnes", {}).get("rpm"))
    from src.core.quota import QuotaLedger
    QuotaLedger.get_default(Path("cache/quota.json"), limits=cfg.get("quota"))
    return AgnesClient(agnes_cfg, quota=None)


async def main():
    c = _client()
    sem = asyncio.Semaphore(2)

    async def run_one(cnd):
        async with sem:
            t0 = time.monotonic()
            ok2, err, url = False, "", ""
            try:
                kw = dict(mode=cnd["route"], duration_s=8, seed=cnd["seed"])
                if cnd["route"] == "i2v":
                    kw["image_url"] = FIRST_URL
                sub = await c.create_video(cnd["prompt"], **kw)
                vid = sub.get("video_id") or sub.get("task_id")
                poll = await c.poll_video(vid, model_name=c.models["video"], max_wait=1500)
                url = poll.get("url") or ""
                ok2 = bool(url)
                if not ok2:
                    err = "no_url"
            except Exception as e:
                err = f"{type(e).__name__}: {str(e)[:150]}"
            dt = round(time.monotonic() - t0, 1)
            with LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"event": "va3_final", "utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "project": "P51_VA",
                    "shot_id": cnd["tag"], "ok": ok2, "error": err, "url": url[:160],
                    "elapsed_s": dt, "seconds": 8}, ensure_ascii=False) + "\n")
            print(cnd["tag"], ok2, dt, err[:60], flush=True)

    await asyncio.gather(*(run_one(x) for x in CONDS))
    print("VA3 done")


if __name__ == "__main__":
    asyncio.run(main())
