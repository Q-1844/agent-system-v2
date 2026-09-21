# -*- coding: utf-8 -*-
"""33.md Animal Character Creative Calibration 驱动。

子命令:
  run [--limit N] [--only F001,F002]   串行执行（75s 间隔，断点续跑，孤儿恢复，900s 看门狗）
  status                               打印完成/失败统计

设计（_33_fox_calibration/FACTS.md）:
  - 12 条核心样本: Shot A/B/C × R0(t2v 无参考) / R1(白底板) / R2(中性身份图) / R3(电影感身份图)
  - R1-R3 prompt = §六基础 prompt + 风格行（身份只来自参考图）
  - R0 prompt    = §六基础 prompt + 狐狸设定 + 场景事实 + 风格行（production facts 文本编译）
  - seed 每 Shot 配对（A/B/C 各一，同 Shot 的 R0-R3 共用）→ paired comparison
  - 参考: 本地 png → imgbb 公网托管（sha1 缓存）→ multi 单图（v2.0 ti2vid 上限 1 张）
  - 直调 client 层，不进 handler；全字段记录 EXPERIMENTS.jsonl
"""
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _33_make_refs import FOX_DESIGN, STYLE_LINE, REFS  # noqa: E402

E33 = ROOT / "_33_fox_calibration"
VIDS = E33 / "videos"
FRAMES = E33 / "frames"
RECORDS = E33 / "EXPERIMENTS.jsonl"
HOSTED = E33 / "refs_hosted.json"
for d in (VIDS, FRAMES):
    d.mkdir(parents=True, exist_ok=True)

INTERVAL = 75.0
NF, FR, W, H = 121, 24, 1280, 704
PER_EXP_TIMEOUT = 900

SCENE_FACTS = ("A quiet vineyard: rows of grapevines with hanging clusters of "
               "purple grapes, a dirt path running between the rows, soft warm daylight.")

BASE_PROMPTS = {
    "A": ("The fox notices something above and slowly raises its gaze. Its ears make "
          "a subtle natural movement and its expression becomes quietly attentive. "
          "The camera remains in a gentle cinematic close-up with very slight natural movement."),
    "B": ("The fox walks naturally along the vineyard path at an unhurried pace. Its body "
          "and tail move with natural four-legged locomotion. The camera tracks smoothly "
          "alongside the fox at a low cinematic angle."),
    "C": ("The fox crouches briefly, then makes one natural upward leap toward the hanging "
          "grapes. It does not reach them and lands back on the ground, regaining its "
          "balance. The camera follows the motion smoothly without an abrupt cut."),
}

REF_FORMATS = ["R0", "R1", "R2", "R3"]
REF_NAMES = {"R1": "R1_plate", "R2": "R2_neutral", "R3": "R3_cinematic"}


def compile_prompt(shot, ref_format):
    base = BASE_PROMPTS[shot]
    if ref_format == "R0":
        return f"{base} {FOX_DESIGN} {SCENE_FACTS} {STYLE_LINE}"
    return f"{base} {STYLE_LINE}"


def _seed_for(shot):
    return int(hashlib.sha1(shot.encode()).hexdigest()[:8], 16) % 10 ** 8


def build_plan():
    rows = []
    idx = 1
    for shot in ("A", "B", "C"):
        for rf in REF_FORMATS:
            rows.append({
                "exp_id": f"F{idx:03d}", "shot": shot, "ref_format": rf,
                "tag": f"{shot}_{rf}", "prompt": compile_prompt(shot, rf),
                "prompt_chars": len(compile_prompt(shot, rf)),
                "seed": _seed_for(shot),
                "num_frames": NF, "frame_rate": FR, "width": W, "height": H,
            })
            idx += 1
    plan_file = E33 / "EXPERIMENT_PLAN.jsonl"
    plan_file.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                         encoding="utf-8")
    print(f"plan written: {len(rows)} -> {plan_file.name}")
    return rows


def _load_plan():
    plan_file = E33 / "EXPERIMENT_PLAN.jsonl"
    return [json.loads(l) for l in plan_file.read_text(encoding="utf-8").splitlines() if l.strip()]


async def _rehost(path):
    import deps
    import _31_e2e_driver as _d31
    _d31.load_env()
    if getattr(deps, "_APP_CFG", None) is None:
        import yaml
        deps._APP_CFG = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    host = deps.get_rehost_host()
    url = await host.upload(str(path))
    print(f"  rehosted {Path(path).name} -> {url[:60]}", flush=True)
    return url


async def hosted_ref_urls():
    """R1/R2/R3 → 公网 URL。优先 Agnes provider 自带 URL（GCS 公开读，
    32.md 实证视频服务可直接抓取同域 look URL）；imgbb 仅兜底
    （2026-09-21 实测 imgbb 503 维护中，且本轮 provider URL 全部可用）。"""
    out = {}
    for rf, name in REF_NAMES.items():
        prov = json.loads((REFS / f"{name}.provenance.json").read_text(encoding="utf-8"))
        url = prov.get("provider_url")
        assert url, f"{name} 无 provider URL 且 imgbb 兜底不可用（503 维护中）"
        sha = prov.get("sha256") or hashlib.sha256((REFS / f"{name}.png").read_bytes()).hexdigest()
        out[rf] = {"name": name, "url": url, "sha256": sha,
                   "source": "agnes provider url (cos-platform-outputs, public-read)"}
    return out


def _frames_and_metrics(mp4, exp_id, duration):
    ts = [0.1, 0.5, 1.0, duration * .25, duration * .5, duration * .75, max(0.05, duration - 0.15)]
    paths = []
    for i, t in enumerate(ts):
        p = FRAMES / f"{exp_id}_f{i}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(mp4),
                        "-frames:v", "1", str(p)], check=True, timeout=120)
        paths.append(p)
    try:
        from PIL import Image, ImageChops
        def diff(a, b):
            ia = Image.open(a).convert("L").resize((256, 144))
            ib = Image.open(b).convert("L").resize((256, 144))
            h = ImageChops.difference(ia, ib).histogram()
            return sum(i * v for i, v in enumerate(h)) / (256 * 144)
        early = diff(paths[0], paths[2]); mid = diff(paths[4], paths[5])
        ratio = round(early / mid, 3) if mid > 0.01 else None
    except Exception as e:
        print(f"  metric err {e}")
        early = mid = ratio = None
    sheet = FRAMES / f"{exp_id}_sheet.jpg"
    try:
        from PIL import Image
        imgs = [Image.open(p).resize((320, 180)) for p in paths]
        grid = Image.new("RGB", (320 * 4, 180 * 2), "black")
        for i, im in enumerate(imgs):
            grid.paste(im, ((i % 4) * 320, (i // 4) * 180))
        grid.save(sheet, quality=80)
    except Exception as e:
        sheet = None
        print(f"  sheet err {e}")
    return {"early_diff": early, "mid_diff": mid, "early_over_mid": ratio}, sheet


def _append(rec):
    with open(RECORDS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def _poll_finish(client, r, rec):
    res = await client.poll_video(rec["video_id"], interval=5,
                                  on_progress=lambda pct, st, w:
                                  print(f"  [{r['exp_id']}] {st} {pct}% {int(w)}s", flush=True))
    dest = VIDS / f"{r['exp_id']}_{r['shot']}_{r['tag'].split('_')[1]}.mp4"
    await client.download(res["url"], dest)
    rec.update({"status": "done", "provider_url": res["url"], "local_mp4": str(dest),
                "bytes": dest.stat().st_size, "completed_at": time.time()})
    pr = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                         "-show_format", "-show_streams", str(dest)],
                        capture_output=True, text=True, timeout=120)
    try:
        finfo = json.loads(pr.stdout)
        vst = next(s for s in finfo["streams"] if s["codec_type"] == "video")
        rec["ffprobe"] = {"duration": finfo["format"].get("duration"),
                          "size": f"{vst.get('width')}x{vst.get('height')}"}
        metrics, sheet = _frames_and_metrics(dest, r["exp_id"], float(finfo["format"]["duration"]))
        rec["motion_proxy"] = metrics
        rec["contact_sheet"] = str(sheet) if sheet else None
    except Exception as e:
        rec["ffprobe_error"] = str(e)[:200]
    _append(rec)
    print(f"[{r['exp_id']}] {r['tag']} DONE {rec['bytes']}B", flush=True)


async def _run_row(r, client, ref_urls):
    rec = {"exp_id": r["exp_id"], "shot": r["shot"], "ref_format": r["ref_format"],
           "tag": r["tag"], "prompt": r["prompt"], "prompt_chars": r["prompt_chars"],
           "seed": r["seed"], "num_frames": r["num_frames"], "frame_rate": r["frame_rate"],
           "width": r["width"], "height": r["height"], "model": "agnes-video-v2.0",
           "submitted_at": time.time()}
    kw = {"num_frames": r["num_frames"], "frame_rate": r["frame_rate"],
          "width": r["width"], "height": r["height"], "seed": r["seed"]}
    if r["ref_format"] == "R0":
        kw["mode"] = "t2v"
        rec["refs"] = []
    else:
        ent = ref_urls[r["ref_format"]]
        kw.update(mode="multi", image_urls=[ent["url"]])
        rec["refs"] = [{"role": ent["name"], "url": ent["url"], "sha256": ent["sha256"],
                        "source": ent["source"]}]
    rec["request_kwargs"] = {k: (v if k != "image_urls" else [u[:60] + "..." for u in v])
                             for k, v in kw.items()}
    try:
        created = await client.create_video(r["prompt"], **kw)
        vid = created.get("video_id") or created.get("task_id")
        rec["video_id"] = vid
        rec["status"] = "submitted"
        _append(rec)
        print(f"[{r['exp_id']}] {r['tag']} submitted {str(vid)[:30]}", flush=True)
        await _poll_finish(client, r, rec)
    except Exception as e:
        rec["status"] = "error"
        rec["error"] = str(e)[:500]
        _append(rec)
        print(f"[{r['exp_id']}] {r['tag']} ERROR {str(e)[:200]}", flush=True)


async def run(limit=None, only=None):
    from src.core.quota import QuotaLedger
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    # 必须在 AgnesClient 创建前初始化单例（32.md 教训：否则落到 300 保守默认）
    QuotaLedger.get_default(ROOT / "cache" / "quota.json", limits=cfg.get("quota"))
    from _31_e2e_driver import load_env, setup_clients
    load_env()
    client = setup_clients()

    rows = _load_plan()
    if only:
        keep = {x.strip() for x in only.split(",")}
        rows = [r for r in rows if r["exp_id"] in keep]
    latest = {}
    if RECORDS.exists():
        for line in RECORDS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                latest[rec["exp_id"]] = rec
    # 孤儿恢复：上次中断留下 submitted 的任务先轮询收尾，不重复提交
    for r in rows:
        rec = latest.get(r["exp_id"])
        if rec and rec.get("status") == "submitted" and rec.get("video_id"):
            print(f"[{r['exp_id']}] orphan recovery: polling {rec['video_id'][:30]}", flush=True)
            try:
                await asyncio.wait_for(_poll_finish(client, r, rec), timeout=PER_EXP_TIMEOUT)
            except Exception as e:
                rec.update({"status": "error", "error": f"orphan poll: {str(e)[:300]}"})
                _append(rec)
                latest[r["exp_id"]] = rec
    todo = [r for r in rows
            if (not latest.get(r["exp_id"])
                or latest[r["exp_id"]].get("status") in ("error",))]
    if limit:
        todo = todo[:int(limit)]
    print(f"plan={len(rows)} todo={len(todo)}", flush=True)
    if not todo:
        return
    ref_urls = await hosted_ref_urls()
    for i, r in enumerate(todo):
        if i:
            await asyncio.sleep(INTERVAL)
        try:
            await asyncio.wait_for(_run_row(r, client, ref_urls), timeout=PER_EXP_TIMEOUT)
        except asyncio.TimeoutError:
            _append({"exp_id": r["exp_id"], "status": "error",
                     "error": f"watchdog timeout {PER_EXP_TIMEOUT}s"})
            print(f"[{r['exp_id']}] WATCHDOG TIMEOUT", flush=True)


def status():
    latest = {}
    if RECORDS.exists():
        for line in RECORDS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("status") in ("done", "error") or rec["exp_id"] not in latest:
                    latest[rec["exp_id"]] = rec
    from collections import Counter
    print("终态:", dict(Counter(r.get("status") for r in latest.values())), "/", len(_load_plan()))
    for eid in sorted(latest):
        r = latest[eid]
        print(f"{eid}|{r.get('tag')}|{r.get('status')}|mp4={bool(r.get('local_mp4'))}")


if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[1] == "status":
        status()
    elif sys.argv[1:] and sys.argv[1] == "plan":
        build_plan()
    else:
        limit = only = None
        args = sys.argv[2:]
        if "--limit" in args:
            limit = args[args.index("--limit") + 1]
        if "--only" in args:
            only = args[args.index("--only") + 1]
        asyncio.run(run(limit=limit, only=only))
