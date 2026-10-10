# Phase51-E V-A：场景多视角生视频测试（图片测试→视频测试，用户 2026-10-11 指令）
# 背景：2026-09-17 图片层结论——Agnes 图片相机方位角控制完全失效（侧/背视 10+ 次
#       6 措辞族 100% 正面泄漏）、正俯视打折（~60°）。本测试回答：视频层的相机
#       运动依从是否更好？（视频有运动维度，且 i2v 有"从正面图出发的运动重绘"）
# 矩阵：2 路线(t2v/i2v) × 3 指令(orbit/rear/overhead) × 2 措辞(中文/英文电影术语)
#       × 2 seeds = 24 条 + 对照(i2v 无运动) 2 条 = 26 条 × 8s = 208s
# i2v 首帧：草原石屋唯一 approved 视图（远景·南面草原 ref_4167a5228c13）经
#       get_rehost_host rehost（与生产同路径）。
# 并发/限流与生产一致（2 并发/RPM 桶）；quota 每次提交前重读（跨进程安全，E run
#       同时在消耗同一 500s/日桶）；结果落 cache/p50_timing_ledger.jsonl。
import asyncio, json, sys, io, time, os
sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

STONE_LOCAL = Path("projects_archive_20261007/AGNES_完整验收_A/output/scene_system/"
                   "locations/loc_60f30c46eebf/views/view_42edf2426aa0/"
                   "generations/gen_493bf1cc2482/candidate_03.png")
LEDGER = Path("cache/p50_timing_ledger.jsonl")
OUT = Path("_p51_va_results.json")

IDENTITY_ZH = ("草原石屋：石灰岩石墙刷白灰泥，正面正中一扇竖纹木板门，"
               "门两侧各一扇小方窗，一侧山墙有高出屋顶的石砌烟囱，屋前南侧有低木栅栏，"
               "四周开阔草原，写实电影风格")
IDENTITY_EN = ("stone cottage: limestone walls with whitewashed plaster, a single "
               "vertical-plank wooden door centered on the front wall, two small square "
               "windows flanking the door, a tall stone chimney rising at one side gable, "
               "low wooden fence on the south approach, open grassland, photorealistic")

MOVES = (
    ("orbit",
     "镜头从房子正面开始，平稳地向右环绕移动，持续转动，直到房子的整面侧墙变成画面主体（正面逐渐转出画面），完整展示侧墙和石砌烟囱",
     "the camera starts facing the cottage front, then orbits steadily to the right, "
     "turning continuously until the SIDE ELEVATION of the house becomes the main "
     "subject and the front facade rotates out of frame, revealing the full side wall "
     "and the stone chimney"),
    ("rear",
     "镜头向前推进并绕过房子的一角，转到房子背后，画面主体变成房子的背面白墙（背面没有门和窗），房子的正面完全离开画面",
     "the camera moves forward past the corner of the cottage and turns around to "
     "frame its REAR WALL as the main subject (blank plaster, no door no windows), "
     "the front facade leaves the frame completely"),
    ("overhead",
     "镜头垂直升到房子正上方，俯视角度越来越陡，最终变成从正上方垂直向下看屋顶的画面（屋顶、烟囱顶部、周边草地从上方可见）",
     "the camera rises straight up, the viewing angle getting steeper, ending in a "
     "TOP-DOWN overhead shot directly above the cottage, looking vertically down at "
     "the rooftop, chimney top and the surrounding grass"),
)

COND = []
for route in ("t2v", "i2v"):
    for move, zh, en in MOVES:
        for lang, tpl, ident in (("zh", zh, IDENTITY_ZH), ("en", en, IDENTITY_EN)):
            for seed in (11, 51):
                COND.append({
                    "tag": f"VA_{route}_{move}_{lang}_s{seed}",
                    "route": route, "move": move, "lang": lang, "seed": seed,
                    "prompt": f"{tpl}。{ident}" if lang == "zh" else f"{tpl}. {ident}",
                })
for seed in (11, 51):
    COND.append({"tag": f"VA_i2v_static_s{seed}", "route": "i2v", "move": "static",
                 "lang": "zh", "seed": seed,
                 "prompt": f"镜头固定不动，房子前草原微风吹动草叶，光线缓慢流动，云影掠过。{IDENTITY_ZH}"})


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
    return AgnesClient(agnes_cfg, quota=None)   # quota 手动跨进程检查（见下）


def _quota_check(seconds: float) -> tuple[bool, str]:
    """跨进程安全的额度检查：每次提交前重读 quota.json（服务进程每次 consume 落盘）。"""
    from src.core.quota import QuotaLedger
    q = QuotaLedger(Path("cache/quota.json"), limits=None)  # 每次新建=重读文件
    return q.can("video_seconds", seconds)


def _log(rec):
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False)[:220])


async def main():
    import deps
    from src.api.agnes_client import AgnesClient
    c = _client()
    # 1) i2v 首帧 rehost
    from deps import get_rehost_host
    first_url = await get_rehost_host().upload(str(STONE_LOCAL))
    _log({"tag": "VA_first_frame", "step": "rehost", "ok": bool(first_url),
          "url": (first_url or "")[:160]})
    if not first_url:
        print("rehost 失败，i2v 组降级为 t2v 前先停止")
        return

    sem = asyncio.Semaphore(2)
    results = []
    total_budget = 232.0   # 本脚本视频秒数上限（208 + 重试余量），服务进程另跑 E run
    spent = {"s": 0.0}

    async def run_one(cnd):
        if spent["s"] + 8 > total_budget:
            results.append({**cnd, "ok": False, "err": "budget_excluded", "url": "", "elapsed_s": 0})
            return
        ok, why = _quota_check(8)
        if not ok:
            results.append({**cnd, "ok": False, "err": f"quota: {why}", "url": "", "elapsed_s": 0})
            return
        async with sem:
            t0 = time.monotonic()
            ok2, err, url = False, "", ""
            try:
                kw = dict(mode=cnd["route"], duration_s=8, seed=cnd["seed"])
                if cnd["route"] == "i2v":
                    kw["image_url"] = first_url
                sub = await c.create_video(cnd["prompt"], **kw)
                vid = sub.get("video_id") or sub.get("task_id")
                spent["s"] += 8
                poll = await c.poll_video(vid, model_name=c.models["video"],
                                          max_wait=1500)
                url = poll.get("url") or ""
                ok2 = bool(url)
                if not ok2:
                    err = "no_url"
            except Exception as e:
                err = f"{type(e).__name__}: {str(e)[:160]}"
            dt = round(time.monotonic() - t0, 1)
            rec = {**cnd, "ok": ok2, "err": err, "url": url, "elapsed_s": dt}
            results.append(rec)
            _log({"event": "va_multiview_video", "utc": time.strftime(
                "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "project": "P51_VA",
                "shot_id": cnd["tag"], "ok": ok2, "error": err, "url": url[:160],
                "elapsed_s": dt, "seconds": 8})

    await asyncio.gather(*(run_one(x) for x in COND))
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    nok = sum(1 for r in results if r["ok"])
    print(f"DONE {nok}/{len(results)} ok")


if __name__ == "__main__":
    asyncio.run(main())
