# Phase51-E V-A2：rear 突破追加组（V-A 最弱项 2/8——措辞强化+路线变体，用掉剩余额度）
# 8 条×8s=64s：i2v 强化 4 条（首帧锚定+显式"背面成为画面主体"）+ t2v 英文强化 4 条
import asyncio, json, sys, io, time, os
sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

LEDGER = Path("cache/p50_timing_ledger.jsonl")
FIRST_URL = "https://Q-1844.github.io/agnes-frame-host/objects/b9e9b194ea01d003.png"

IDENT_ZH = ("草原石屋：石灰岩石墙刷白灰泥，正面正中一扇竖纹木板门，门两侧各一扇小方窗，"
            "一侧山墙有高出屋顶的石砌烟囱，屋前南侧有低木栅栏，四周开阔草原，写实电影风格")
REAR_I2V = [
    ("VA2_i2v_rearA_s{seed}",
     "摄影机平稳地绕到房子的正后方并停住，最终画面完全是房子的背面：一面干净的白石灰后墙占据画面主体，"
     "后墙上没有任何门和窗，正面和木门完全看不见，只能看到后墙、屋顶背面轮廓和周边草地。{ident}"),
    ("VA2_i2v_rearB_s{seed}",
     "镜头水平横移越过房子侧面后继续移动，直到从房子背后拍摄：画面主体=无门窗的白灰后墙，"
     "木门和正面彻底离开画面，视频结尾停留在背面视角。{ident}"),
]
REAR_T2V = [
    ("VA2_t2v_rearA_s{seed}",
     "static shot from directly behind the cottage: the REAR ELEVATION fills the frame as the "
     "main subject, a blank whitewashed plaster rear wall with no door and no windows, the front "
     "facade completely out of frame. {ident}"),
    ("VA2_t2v_rearB_s{seed}",
     "the video begins facing the rear wall of the cottage from behind: blank plaster back wall "
     "as the main subject, gable roof edge and chimney visible, grassland around, no door, no "
     "windows, never showing the front. {ident}"),
]

CONDS = []
for tpl, prompt in REAR_I2V:
    for seed in (11, 51):
        CONDS.append({"tag": tpl.format(seed=seed), "route": "i2v", "seed": seed,
                      "prompt": prompt.format(ident=IDENT_ZH)})
for tpl, prompt in REAR_T2V:
    for seed in (11, 51):
        ident_en = ("stone cottage: limestone walls whitewashed, single plank door on front, "
                    "two small windows, stone chimney at side gable, wooden fence, open grassland")
        CONDS.append({"tag": tpl.format(seed=seed), "route": "t2v", "seed": seed,
                      "prompt": prompt.format(ident=ident_en)})


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
    results = []

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
                err = f"{type(e).__name__}: {str(e)[:160]}"
            dt = round(time.monotonic() - t0, 1)
            rec = {**cnd, "ok": ok2, "err": err, "url": url, "elapsed_s": dt}
            results.append(rec)
            with LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"event": "va2_rear_push", "utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "project": "P51_VA",
                    "shot_id": cnd["tag"], "ok": ok2, "error": err, "url": url[:160],
                    "elapsed_s": dt, "seconds": 8}, ensure_ascii=False) + "\n")
            print(cnd["tag"], ok2, dt, err[:60], flush=True)

    await asyncio.gather(*(run_one(x) for x in CONDS))
    Path("_p51_va2_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"DONE {sum(1 for r in results if r['ok'])}/{len(results)}")


if __name__ == "__main__":
    asyncio.run(main())
