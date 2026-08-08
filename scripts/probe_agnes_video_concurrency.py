"""Agnes Video V2.0 并发 / Outstanding Task 能力实测探针（手工执行的真实联网探针，非 pytest）。

回答的问题（提示词.md）：
  Agnes Video V2.0 在 Free / Default 1 RPM 条件下，是否允许「前一个视频还未完成时，
  到达下一个 RPM 窗口后继续提交新的视频任务」，以及这样做是否具有真实吞吐价值。
  结果决定未来 Autopilot 是继续完全串行，还是升级为「限速提交 + 多任务同时 poll」。

🔴 硬约束（提示词.md §三 / §八 / §十三）：
  - 不修改正式产品逻辑（JobQueue / RateLimiter / Autopilot / Shot handler / 前端 / AgnesClient 行为）。
  - 复用：config 加载、AgnesClient 的 HTTP client（auth/proxy/base_url）、snap_num_frames、RateLimiter。
  - 不使用「submit 后 poll 到结束再返回」的串行封装作为唯一测试方式。
  - Probe 内自带 query_video_status(video_id)：单次 GET /agnesapi，不阻塞、不重试。
  - 所有 POST /v1/videos 之间走项目现有 RateLimiter video 桶（1 RPM * 0.85 safety ≈ 70.6s 间隔），绝不违反 1 RPM。
  - 不通过多个 API Key 绕限制；不硬编码 API Key；日志禁止输出完整 Key。
  - B 被拒时记录第一次原始 HTTP 响应，不自动重试掩盖。

用法（项目根目录）：
  python scripts/probe_agnes_video_concurrency.py
报告输出：
  output/probes/agnes_video_concurrency_<timestamp>.json
  output/probes/agnes_video_concurrency_<timestamp>.md
"""
import asyncio
import os
import sys
import json
import time
from pathlib import Path

# ---- Windows 控制台 UTF-8（与 test_agnes_video_probe.py 一致）----
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))  # 让 scripts/ 下脚本能 import 项目根的 src/

# ---- 加载 .env（AGNES_API_KEY / IMGBB_API_KEY），不硬编码 ----
env_path = BASE / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import yaml
import httpx
from src.core.rate_limiter import RateLimiter
from src.core.quota import QuotaLedger
from src.api.agnes_client import AgnesClient, snap_num_frames

# ============================================================================
# 配置（全部从 config/config.yaml 读取，模型名不硬编码）
# ============================================================================
cfg = yaml.safe_load((BASE / "config" / "config.yaml").read_text(encoding="utf-8"))
agnes_cfg = dict(cfg["agnes"])
if "${AGNES_API_KEY}" in str(agnes_cfg.get("api_key", "")):
    agnes_cfg["api_key"] = os.environ.get("AGNES_API_KEY", "")

# 进程级单例初始化（与 probe_content_moderation.py 一致；用 probe 专属 quota 文件，不污染生产账本）
RateLimiter.get(agnes_cfg.get("rpm"))
QuotaLedger.get_default(BASE / "cache" / "quota_probe.json", limits=cfg.get("quota"))
client = AgnesClient(agnes_cfg)

VIDEO_MODEL = agnes_cfg["models"]["video"]            # agnes-video-v2.0（从配置读，不硬编码）
FRAME_RATE = int(agnes_cfg["video"]["frame_rate"])    # 24
WIDTH, HEIGHT = 1280, 704                             # 生产 16:9 直提交（FACTS §3.4.1 实测无归一化）
POLL_INTERVAL = 5                                     # 官方建议 5s 轮询（FACTS §3.5）
# video 桶 rpm=1 * 0.85 safety -> 令牌补充需 60/0.85 ≈ 70.6s（即安全 submit 间隔）
VIDEO_RPM = float(agnes_cfg.get("rpm", {}).get("video", 1))
SUBMIT_INTERVAL_S = 60.0 / max(VIDEO_RPM * 0.85, 0.1)

OUTPUT_DIR = BASE / "output" / "probes"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
START_TS = time.time()
TS_TAG = time.strftime("%Y%m%d_%H%M%S", time.localtime(START_TS))
REPORT_JSON = OUTPUT_DIR / f"agnes_video_concurrency_{TS_TAG}.json"
REPORT_MD = OUTPUT_DIR / f"agnes_video_concurrency_{TS_TAG}.md"

VIDEO_PROMPT = "The mechanical sphere slowly rotates in place, subtle stable motion, static camera."
# 避开内容审核：不用 "pure/solid white background" 组合（CLAUDE.md 实测触发种族过滤）
IMG_PROMPT = ("a red mechanical sphere on a gray workbench, clean studio background, "
              "product photography, soft even lighting, no people")


def log(msg):
    print(msg, flush=True)


def mask_key(k):
    if not k:
        return "(empty)"
    return f"{k[:6]}...{k[-4:]} (len={len(k)})"


# ============================================================================
# 核心：raw submit（单次 POST，不重试，捕获原始响应）+ 单次非阻塞 query
# ============================================================================
async def raw_post_video(body, tag="submit"):
    """单次 POST /v1/videos，不重试。调用方负责事先 limiter.acquire('video')。
    返回原始响应记录（含 http_status / body_text / video_id / status / seconds / size）。
    🔴 不重试：B 被拒时能看到第一次原始响应（提示词.md §六/§九 情况3：不掩盖第一次行为）。"""
    t0 = time.monotonic()
    c = await client.client()
    try:
        r = await c.post("/v1/videos", json=body,
                         timeout=httpx.Timeout(connect=30, read=180, write=60, pool=30))
    except Exception as e:
        dt = time.monotonic() - t0
        log(f"[{tag}] POST 异常 {dt:.1f}s: {type(e).__name__}: {e}")
        return {"ok": False, "http_status": None,
                "error": f"{type(e).__name__}: {e}",
                "submit_at": t0, "submit_dt": round(dt, 2),
                "video_id": None, "task_id": None, "status": None,
                "seconds": None, "size": None, "created_at": None,
                "body_text": "", "json": None}
    dt = time.monotonic() - t0
    try:
        j = r.json()
    except Exception:
        j = None
    vid = (j or {}).get("video_id") or (j or {}).get("id")
    rec = {"ok": r.status_code < 400, "http_status": r.status_code,
           "body_text": r.text[:1500], "json": j,
           "video_id": vid, "task_id": (j or {}).get("task_id"),
           "status": (j or {}).get("status"),
           "seconds": (j or {}).get("seconds"),
           "size": (j or {}).get("size"),
           "created_at": (j or {}).get("created_at"),
           "submit_at": t0, "submit_dt": round(dt, 2), "error": None}
    log(f"[{tag}] HTTP {r.status_code} {dt:.1f}s video_id={vid} "
        f"status={rec['status']} seconds={rec['seconds']} size={rec['size']}")
    if not rec["ok"]:
        log(f"[{tag}] ❌ 错误响应体（不重试，记录第一次）: {r.text[:800]}")
    return rec


async def query_video_status(video_id):
    """单次 GET /agnesapi，不阻塞、不重试（提示词.md §三：Probe 内专门的 query_video_status）。
    返回 {ok, http_status, status, progress, seconds, size, url, error, raw}。"""
    c = await client.client()
    try:
        r = await c.get("/agnesapi",
                        params={"video_id": video_id, "model_name": VIDEO_MODEL},
                        timeout=httpx.Timeout(connect=15, read=60, write=15, pool=15))
    except Exception as e:
        return {"ok": False, "http_status": None,
                "error": f"{type(e).__name__}: {e}", "status": None, "progress": None,
                "seconds": None, "size": None, "url": None, "raw": None}
    try:
        j = r.json()
    except Exception:
        return {"ok": False, "http_status": r.status_code,
                "error": f"non-JSON: {r.text[:300]}", "status": None, "progress": None,
                "seconds": None, "size": None, "url": None, "raw": None}
    # URL fallback 链与正式 poll_video 一致（FACTS §3.6 勘误）
    url = j.get("remixed_from_video_id") or j.get("url") or j.get("video_url")
    return {"ok": r.status_code < 400, "http_status": r.status_code,
            "status": j.get("status"), "progress": j.get("progress"),
            "seconds": j.get("seconds"), "size": j.get("size"), "url": url,
            "error": (j.get("error") if r.status_code >= 400 else None), "raw": j}


async def query_one(video_id):
    """经 text 限流桶的单次查询（与正式 poll_video 走 text 桶一致，FACTS §3.5；
    轮询不占 video 桶——否则第二个任务永远提交不了）。多任务并发查询时自动按 text RPM 排队。"""
    await client.limiter.acquire("text")
    return await query_video_status(video_id)


# ============================================================================
# state 工具
# ============================================================================
def new_state(t0, resp):
    return {"video_id": resp.get("video_id"), "t0": t0, "submit_at": t0,
            "status": resp.get("status"), "progress": 0,
            "first_in_progress_at": None, "completed_at": None, "failed_at": None,
            "url": None, "seconds": None, "size": None, "error": None,
            "history": []}


def update_state(state, q):
    now = time.monotonic()
    st = q.get("status")
    if st:
        state["status"] = st
        state["progress"] = q.get("progress")
        if st == "in_progress" and state.get("first_in_progress_at") is None:
            state["first_in_progress_at"] = now
        if st == "completed":
            state["completed_at"] = now
            state["url"] = q.get("url")
            state["seconds"] = q.get("seconds")
            state["size"] = q.get("size")
        if st == "failed":
            state["failed_at"] = now
            state["error"] = q.get("error") or str(q.get("raw"))[:300]
    state["history"].append({"t_rel": round(now - state["t0"], 1),
                             "http": q.get("http_status"),
                             "status": st, "progress": q.get("progress")})


def is_active(status):
    """任务是否仍 outstanding（已提交未完成）。
    实测 Agnes 视频状态机：create 响应 status=queued，首次 poll 可能是 pending -> in_progress -> completed/failed。
    任何非 completed/failed 都算活跃（含 queued/pending/in_progress 及未知），避免漏判 pending。"""
    return status not in ("completed", "failed")


def summarize(label, num_frames, resp, state):
    return {
        "label": label,
        "num_frames": num_frames,
        "requested_duration_s": round(num_frames / FRAME_RATE, 2),
        "video_id": resp.get("video_id"),
        "task_id": resp.get("task_id"),
        "submit_http": resp.get("http_status"),
        "submit_status": resp.get("status"),
        "submit_seconds": resp.get("seconds"),      # 服务端回读（FACTS §3.6）
        "submit_size": resp.get("size"),
        "created_at": resp.get("created_at"),
        "first_in_progress_at_rel": (round(state["first_in_progress_at"] - state["t0"], 1)
                                     if state.get("first_in_progress_at") else None),
        "completed_at_rel": (round(state["completed_at"] - state["t0"], 1)
                             if state.get("completed_at") else None),
        "wall_time_s": (round(state["completed_at"] - state["t0"], 1)
                        if state.get("completed_at") else None),
        "final_status": state.get("status"),
        "final_url": state.get("url"),
        "final_seconds": state.get("seconds"),
        "final_size": state.get("size"),
        "error": state.get("error"),
    }


def build_body(image_url, num_frames):
    nf = snap_num_frames(num_frames, WIDTH, HEIGHT)  # 8n+1 + ≤max_frames_for(size) 校验（复用正式逻辑）
    return {"model": VIDEO_MODEL, "prompt": VIDEO_PROMPT,
            "image": image_url,                   # 顶层字符串（i2v 单图，FACTS §3.2 形态B）
            "num_frames": nf, "frame_rate": FRAME_RATE,
            "width": WIDTH, "height": HEIGHT}


# ============================================================================
# 轮询：单任务 / 多任务并发
# ============================================================================
async def poll_single_to_done(label, state, max_wait=900):
    """单任务轮询到 completed/failed（每 POLL_INTERVAL 秒一次，经 text 桶）。"""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        q = await query_one(state["video_id"])
        update_state(state, q)
        log(f"  [{label}] t={time.monotonic()-state['t0']:.0f}s "
            f"http={q.get('http_status')} status={state['status']} progress={state.get('progress')}")
        if state.get("completed_at") or state.get("failed_at"):
            return
        await asyncio.sleep(POLL_INTERVAL)
    log(f"  [{label}] ⚠️ 超时 {max_wait}s 未完成")


async def concurrent_poll(states, t0, labels, max_wait=1200):
    """多任务并发轮询：每个 tick 依次查询所有未完成任务（每个经 text 桶，自动按 text RPM 排队）。
    返回 (timeline, max_outstanding, simultaneous_in_progress)。"""
    timeline = []
    max_outstanding = 0
    simultaneous_in_progress = False
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        now = time.monotonic()
        snap = {"t_rel": round(now - t0, 1)}
        active = 0
        inprog = 0
        for label in labels:
            s = states[label]
            if not (s.get("completed_at") or s.get("failed_at")):
                q = await query_one(s["video_id"])
                update_state(s, q)
            st = s.get("status")
            snap[label] = {"status": st, "progress": s.get("progress")}
            if is_active(st):
                active += 1
            if st == "in_progress":
                inprog += 1
        timeline.append(snap)
        if active > max_outstanding:
            max_outstanding = active
        if inprog >= 2:
            simultaneous_in_progress = True
        log(f"  [poll] t={snap['t_rel']:.0f}s " + " ".join(
            f"{l}={snap[l]['status']}/{snap[l].get('progress')}" for l in labels))
        if all(states[l].get("completed_at") or states[l].get("failed_at") for l in labels):
            break
        await asyncio.sleep(POLL_INTERVAL)
    return timeline, max_outstanding, simultaneous_in_progress


async def wait_next_window(active_states, t_ref, max_poll=150):
    """等下一个 video 限流窗口（~70.6s），同时每 POLL_INTERVAL 秒轮询 active_states 里的活跃任务。
    窗口打开（video 令牌已消耗）后返回 monotonic 时刻。调用方随后立即 raw_post_video（不再 acquire）。
    active_states: dict label->state。"""
    async def _win():
        await client.limiter.acquire("video")
        return time.monotonic()

    win_task = asyncio.create_task(_win())
    while not win_task.done():
        for label, s in list(active_states.items()):
            if s.get("completed_at") or s.get("failed_at"):
                continue
            q = await query_one(s["video_id"])
            update_state(s, q)
        log(f"  [pre-window poll] t={time.monotonic()-t_ref:.0f}s " + " ".join(
            f"{l}={s['status']}" for l, s in active_states.items()))
        if win_task.done():
            break
        await asyncio.wait([win_task], timeout=POLL_INTERVAL)
    return await win_task


# ============================================================================
# 测试图：Agnes Image 2.1 生成 -> 公网 URL（FACTS §3.6：Agnes 图 URL 可直送 video 接口）
# ============================================================================
async def make_test_image():
    log(f"[test-image] 用 Agnes Image 2.1 生成测试图：{IMG_PROMPT}")
    res = await client.generate_image(prompt=IMG_PROMPT, size_tier="1K", ratio="16:9")
    url = res.get("url")
    if not url:
        raise RuntimeError(f"测试图生成无 url: {res}")
    log(f"[test-image] url={url}")
    local = OUTPUT_DIR / f"test_image_{TS_TAG}.png"
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            local.write_bytes(r.content)
        log(f"[test-image] saved -> {local.name}")
    except Exception as e:
        log(f"[test-image] 下载保存失败（不影响测试）: {e}")
    return url, local


async def rehost_via_imgbb(local_path):
    """imgbb 重新托管（实测唯一可用 ImageHost，上传原字节 100% 保真）。
    仅当 Agnes 测试图 URL 被 video 接口拒绝时作为 fallback。"""
    from src.core.image_host import ImgbbHost
    key = os.environ.get("IMGBB_API_KEY", "")
    host = ImgbbHost(key, 0, cache_path=str(BASE / "cache" / "imgbb_cache_probe.json"))
    url = await host.upload(local_path)
    log(f"[imgbb rehost] {local_path.name} -> {url}")
    return url


# ============================================================================
# Section 五：时长测试
# ============================================================================
async def run_duration_case(label, num_frames, image_url):
    body = build_body(image_url, num_frames)
    log(f"\n[Duration {label}] num_frames={num_frames} (~{num_frames/FRAME_RATE:.1f}s)")
    await client.limiter.acquire("video")          # 安全 RPM（首个立即，后续 ~70.6s）
    resp = await raw_post_video(body, f"dur-{label}")
    if not resp["ok"] or not resp["video_id"]:
        return {"label": label, "num_frames": num_frames, "submit": resp, "error": "submit failed"}
    state = new_state(resp["submit_at"], resp)
    await poll_single_to_done(label, state)
    return summarize(label, num_frames, resp, state)


# ============================================================================
# Section 六：Outstanding 2-task Test
# ============================================================================
async def run_two_task(image_url):
    body = build_body(image_url, 441)  # 最大帧数 -> 最长生成时间，优先让 A 持续超过一个 RPM 周期
    log("\n[2-task] submit A (441 frames)...")
    await client.limiter.acquire("video")
    resp_a = await raw_post_video(body, "submit-A")
    if not resp_a["ok"] or not resp_a["video_id"]:
        return {"conclusive": False, "error": "A submit failed", "a_submit": resp_a}
    state_a = new_state(resp_a["submit_at"], resp_a)
    log(f"[2-task] A video_id={resp_a['video_id']} status={resp_a['status']}")

    # 等 RPM 窗口（~70.6s）同时持续 poll A
    t_open = await wait_next_window({"A": state_a}, resp_a["submit_at"])
    a_status_at_b = state_a["status"]
    a_active = is_active(a_status_at_b)
    a_to_b_gap = t_open - resp_a["submit_at"]
    log(f"[2-task] RPM 窗口打开（A 提交后 {a_to_b_gap:.0f}s）。A 当前 status={a_status_at_b} active={a_active}")
    result = {
        "a_to_b_gap_s": round(a_to_b_gap, 1),
        "a_status_at_b_submit": a_status_at_b,
        "a_active_when_b_submitted": a_active,
    }

    if not a_active:
        # 提示词.md §六：A 已完成 -> inconclusive，不强行制造并发（不违反 1 RPM）
        log("[2-task] INCONCLUSIVE: A 在下一个合法提交窗口前已完成。不强行制造并发。")
        result["conclusive"] = False
        result["b_submit_succeeded"] = None
        result["a"] = summarize("A", 441, resp_a, state_a)
        return result

    # A 仍活跃 -> 提交 B（video 令牌已在 wait_next_window 消耗，立即 POST）
    resp_b = await raw_post_video(body, "submit-B")
    result["b_submit_http_status"] = resp_b["http_status"]
    result["b_submit_ok"] = resp_b["ok"]
    result["b_submit_body_excerpt"] = (resp_b.get("body_text") or "")[:600]
    if not resp_b["ok"] or not resp_b["video_id"]:
        log(f"[2-task] B 提交被拒 HTTP {resp_b['http_status']}。记录第一次响应，不重试。")
        result["conclusive"] = True
        result["b_submit_succeeded"] = False
        result["simultaneous_outstanding_observed"] = False
        result["simultaneous_in_progress_observed"] = False
        result["both_completed"] = False
        await poll_single_to_done("A", state_a)
        result["a"] = summarize("A", 441, resp_a, state_a)
        result["b"] = None
        return result
    state_b = new_state(resp_b["submit_at"], resp_b)
    log(f"[2-task] B video_id={resp_b['video_id']} status={resp_b['status']}（A 仍 {state_a['status']}）")

    # 同时 poll A / B
    timeline, max_out, sim_inprog = await concurrent_poll(
        {"A": state_a, "B": state_b}, resp_b["submit_at"], ["A", "B"], max_wait=900)
    result["conclusive"] = True
    result["b_submit_succeeded"] = True
    result["simultaneous_outstanding_observed"] = max_out >= 2
    result["simultaneous_in_progress_observed"] = sim_inprog
    result["max_outstanding_during_poll"] = max_out
    result["both_completed"] = bool(state_a.get("completed_at")) and bool(state_b.get("completed_at"))
    result["timeline_tail"] = timeline[-12:]
    result["a"] = summarize("A", 441, resp_a, state_a)
    result["b"] = summarize("B", 441, resp_b, state_b)
    return result


# ============================================================================
# Section 七：Outstanding 3-task Test（仅 2-task 真正成功后继续）
# ============================================================================
async def run_three_task(image_url):
    body = build_body(image_url, 441)
    log("\n[3-task] submit A...")
    await client.limiter.acquire("video")
    resp_a = await raw_post_video(body, "submit-A")
    if not resp_a["ok"] or not resp_a["video_id"]:
        return {"conclusive": False, "error": "A submit failed", "a_submit": resp_a}
    state_a = new_state(resp_a["submit_at"], resp_a)
    log(f"[3-task] A video_id={resp_a['video_id']}")

    t_open1 = await wait_next_window({"A": state_a}, resp_a["submit_at"])
    a_active = is_active(state_a["status"])
    result = {"a_active_when_b_submitted": a_active,
              "a_status_at_b_submit": state_a["status"],
              "a_to_b_gap_s": round(t_open1 - resp_a["submit_at"], 1)}
    if not a_active:
        log("[3-task] A 在 B 窗口前完成 -> 无法构造 3 outstanding，停止。")
        result["conclusive"] = False
        result["reason"] = "A finished before B window"
        await poll_single_to_done("A", state_a)
        result["a"] = summarize("A", 441, resp_a, state_a)
        return result
    resp_b = await raw_post_video(body, "submit-B")
    result["b_submit_http_status"] = resp_b["http_status"]
    result["b_submit_ok"] = resp_b["ok"]
    if not resp_b["ok"] or not resp_b["video_id"]:
        log(f"[3-task] B 被拒 HTTP {resp_b['http_status']}。停止。")
        result["b_submit_succeeded"] = False
        result["conclusive"] = True
        await poll_single_to_done("A", state_a)
        result["a"] = summarize("A", 441, resp_a, state_a)
        return result
    state_b = new_state(resp_b["submit_at"], resp_b)
    log(f"[3-task] B video_id={resp_b['video_id']}")

    t_open2 = await wait_next_window({"A": state_a, "B": state_b}, resp_b["submit_at"])
    any_active = (is_active(state_a["status"]) or is_active(state_b["status"]))
    result["b_to_c_gap_s"] = round(t_open2 - resp_b["submit_at"], 1)
    result["a_status_at_c_submit"] = state_a["status"]
    result["b_status_at_c_submit"] = state_b["status"]
    result["any_active_when_c_submitted"] = any_active
    if not any_active:
        log("[3-task] A&B 在 C 窗口前均完成 -> 无法构造 3 outstanding。")
        result["c_submit_succeeded"] = None
        result["conclusive"] = False
        result["reason"] = "A&B finished before C window"
        await concurrent_poll({"A": state_a, "B": state_b}, resp_b["submit_at"],
                              ["A", "B"], max_wait=300)
        result["a"] = summarize("A", 441, resp_a, state_a)
        result["b"] = summarize("B", 441, resp_b, state_b)
        return result
    resp_c = await raw_post_video(body, "submit-C")
    result["c_submit_http_status"] = resp_c["http_status"]
    result["c_submit_ok"] = resp_c["ok"]
    if not resp_c["ok"] or not resp_c["video_id"]:
        log(f"[3-task] C 被拒 HTTP {resp_c['http_status']}。停止。")
        result["c_submit_succeeded"] = False
        result["conclusive"] = True
        await concurrent_poll({"A": state_a, "B": state_b}, resp_b["submit_at"],
                              ["A", "B"], max_wait=600)
        result["a"] = summarize("A", 441, resp_a, state_a)
        result["b"] = summarize("B", 441, resp_b, state_b)
        return result
    state_c = new_state(resp_c["submit_at"], resp_c)
    log(f"[3-task] C video_id={resp_c['video_id']}")

    timeline, max_out, sim_inprog = await concurrent_poll(
        {"A": state_a, "B": state_b, "C": state_c}, resp_c["submit_at"],
        ["A", "B", "C"], max_wait=1500)
    result["c_submit_succeeded"] = True
    result["conclusive"] = True
    result["max_outstanding_during_poll"] = max_out
    result["simultaneous_in_progress_observed"] = sim_inprog
    result["all_completed"] = all(s.get("completed_at") for s in (state_a, state_b, state_c))
    result["timeline_tail"] = timeline[-15:]
    result["a"] = summarize("A", 441, resp_a, state_a)
    result["b"] = summarize("B", 441, resp_b, state_b)
    result["c"] = summarize("C", 441, resp_c, state_c)
    return result


# ============================================================================
# 结论分类（提示词.md §十一）+ 依赖分析（§十二）
# ============================================================================
def classify(report):
    two = (report.get("concurrency_tests") or {}).get("two_task") or {}
    three = (report.get("concurrency_tests") or {}).get("three_task") or {}
    dur = report.get("duration_tests") or []
    gen18 = [d["wall_time_s"] for d in dur
             if d.get("label") == "18s" and d.get("wall_time_s") is not None]
    max_gen18 = max(gen18) if gen18 else None
    avg_gen18 = (sum(gen18) / len(gen18)) if gen18 else None

    a_active = two.get("a_active_when_b_submitted")
    b_ok = two.get("b_submit_succeeded")
    both_done = two.get("both_completed")
    sim_out = two.get("simultaneous_outstanding_observed")

    # Result A：保持完全串行
    if a_active is True and b_ok is False:
        return "KEEP_SERIAL_VIDEO_EXECUTOR", [
            "A 仍活跃时提交 B 被拒绝 -> Agnes 不支持 outstanding video task"]
    if a_active is True and b_ok is True and both_done is False:
        return "KEEP_SERIAL_VIDEO_EXECUTOR", [
            "B 可提交但多 outstanding 不稳定（出现 failed/未完成）"]

    # Result C：支持稳定多 outstanding 且存在明显吞吐收益
    if (a_active is True and b_ok is True and both_done is True and sim_out is True
            and max_gen18 is not None and max_gen18 > SUBMIT_INTERVAL_S):
        reasons = [f"A 活跃时 B 成功提交且均完成；18s(441帧) 视频最长生成 {max_gen18:.0f}s "
                   f"> 安全间隔 {SUBMIT_INTERVAL_S:.0f}s -> 重叠真实存在、有吞吐收益"]
        if three and three.get("c_submit_succeeded") and three.get("all_completed"):
            reasons.append("3-task 亦全部成功提交并完成 -> 多 outstanding 稳定")
        return "OUTSTANDING_SUPPORTED_USEFUL", reasons

    # Result B：API 支持 outstanding，但暂时没有明显收益
    if a_active is True and b_ok is True and both_done is True:
        g = f"~{avg_gen18:.0f}s" if avg_gen18 else "?"
        return "OUTSTANDING_SUPPORTED_LOW_VALUE", [
            f"B 可提交且完成，但 18s(441帧) 视频生成 {g} 不显著超过安全间隔 "
            f"{SUBMIT_INTERVAL_S:.0f}s -> 大多数时间不会重叠，吞吐收益小"]
    if a_active is False:
        return "OUTSTANDING_SUPPORTED_LOW_VALUE", [
            f"即使在 441 帧（最大合理帧数）下，A 也在下一个 RPM 窗口（~{SUBMIT_INTERVAL_S:.0f}s）前完成 -> "
            "视频生成过快，outstanding 无重叠机会，实际收益极小"]

    return "INCONCLUSIVE", ["数据不足，无法分类"]


DEPENDENCY_ANALYSIS = {
    "phase_6a_link_modes": {
        "independent": "无需上一镜视频末帧，Shot 之间相互独立 -> outstanding 优化的唯一潜在受益者",
        "reference_previous": "需上一镜 video_end_frame 作为 continuity reference -> 硬 dependency edge",
        "inherit_previous": "需上一镜 video_end_frame 作为 effective first frame -> 硬 dependency edge",
    },
    "dependency_chain": ("previous video completed -> extract video_end_frame -> "
                         "host URL ready -> next shot frame generation"),
    "implication": ("reference_previous / inherit_previous 形成硬 dependency edge，即使 Agnes 支持并发"
                    "也不能提前执行。未来 outstanding 优化只能作用于 dependency graph 中相互独立的 Shot"
                    "（即 independent 链），不能作用于有 continuity 依赖的 Shot 链。"),
}


# ============================================================================
# 报告落盘（增量 + 最终）
# ============================================================================
def save_report(report):
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(report):
    two = (report.get("concurrency_tests") or {}).get("two_task") or {}
    three = (report.get("concurrency_tests") or {}).get("three_task") or {}
    dur = report.get("duration_tests") or []
    env = report.get("environment") or {}
    lines = []
    lines.append("# Agnes Video V2.0 并发 / Outstanding 能力实测报告\n")
    lines.append(f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report.get('generated_at', START_TS)))}")
    lines.append(f"- base_url: `{env.get('base_url')}`")
    lines.append(f"- model: `{env.get('model')}`  | api_key: `{env.get('api_key_masked')}`")
    lines.append(f"- frame_rate: {env.get('frame_rate')} | size: {env.get('default_size')} | "
                 f"安全 submit 间隔: ~{env.get('submit_interval_s')}s | poll: {env.get('poll_interval_s')}s\n")

    lines.append("## 一、时长测试（5s/10s/18s 各 ≥2 次）\n")
    lines.append("| case | num_frames | 请求时长 | submit_status | wall_time_s | final_status |")
    lines.append("|---|---:|---:|---|---:|---|")
    for d in dur:
        lines.append(f"| {d.get('label')} | {d.get('num_frames')} | {d.get('requested_duration_s')}s | "
                     f"{d.get('submit_status')} | {d.get('wall_time_s')} | {d.get('final_status')} |")
    gen18 = [d["wall_time_s"] for d in dur if d.get("label") == "18s" and d.get("wall_time_s") is not None]
    if gen18:
        lines.append(f"\n**18s(441帧) 生成耗时**: {gen18} -> max={max(gen18):.0f}s, avg={sum(gen18)/len(gen18):.0f}s "
                     f"（安全间隔 ~{SUBMIT_INTERVAL_S:.0f}s）\n")

    lines.append("## 二、Outstanding 2-task Test\n")
    lines.append(f"- conclusive: `{two.get('conclusive')}`")
    lines.append(f"- A→B 间隔: {two.get('a_to_b_gap_s')}s")
    lines.append(f"- A 提交 B 时仍活跃: `{two.get('a_active_when_b_submitted')}` (status={two.get('a_status_at_b_submit')})")
    lines.append(f"- B 提交成功: `{two.get('b_submit_succeeded')}` (http={two.get('b_submit_http_status')})")
    lines.append(f"- 观察到同时 outstanding: `{two.get('simultaneous_outstanding_observed')}`")
    lines.append(f"- 观察到同时 in_progress: `{two.get('simultaneous_in_progress_observed')}`")
    lines.append(f"- A/B 均完成: `{two.get('both_completed')}`")
    if two.get("a"):
        lines.append(f"  - A wall_time: {two['a'].get('wall_time_s')}s, B wall_time: "
                     f"{two['b'].get('wall_time_s') if two.get('b') else 'N/A'}s\n")

    if three:
        lines.append("## 三、Outstanding 3-task Test\n")
        lines.append(f"- skipped: `{three.get('skipped', False)}` | conclusive: `{three.get('conclusive')}`")
        if three.get("c_submit_succeeded") is not None:
            lines.append(f"- C 提交成功: `{three.get('c_submit_succeeded')}` (http={three.get('c_submit_http_status')})")
            lines.append(f"- poll 期间最大 outstanding: {three.get('max_outstanding_during_poll')}")
            lines.append(f"- 同时 in_progress: `{three.get('simultaneous_in_progress_observed')}`")
            lines.append(f"- A/B/C 均完成: `{three.get('all_completed')}`\n")

    lines.append(f"## 四、最大稳定 outstanding 数\n\n**{report.get('max_simultaneous_outstanding_observed')}**\n")

    errs = report.get("errors") or []
    lines.append(f"## 五、错误记录（429/503/failed 等）\n\n共 {len(errs)} 条")
    for e in errs:
        lines.append(f"- {e}")
    lines.append("")

    lines.append("## 六、分类结论\n")
    lines.append(f"### `{report.get('conclusion')}`\n")
    for r in report.get("conclusion_reasons") or []:
        lines.append(f"- {r}")
    lines.append("")

    lines.append("## 七、依赖分析（提示词.md §十二）\n")
    lines.append(f"- **依赖链**: {DEPENDENCY_ANALYSIS['dependency_chain']}")
    lines.append(f"- **implication**: {DEPENDENCY_ANALYSIS['implication']}\n")

    lines.append("## 八、对未来 Autopilot 的建议\n")
    conc = report.get("conclusion")
    if conc == "OUTSTANDING_SUPPORTED_USEFUL":
        lines.append("- 未来可考虑：`rate-limited submitter + independent pollers`（限速提交 + 独立轮询）。")
        lines.append("- 仅对 dependency graph 中相互独立的 Shot（independent 链）启用并发；"
                     "reference_previous/inherit_previous 链仍必须串行。")
        lines.append("- 🔴 本任务不修改正式产品架构，仅记录结论。")
    elif conc == "OUTSTANDING_SUPPORTED_LOW_VALUE":
        lines.append("- 正式产品仍建议保持串行（视频生成通常 < 安全间隔，重叠机会少，收益小）。")
        lines.append("- 若未来 Agnes 提速或视频变长导致生成时间稳定 > 间隔，再重新评估。")
    else:  # KEEP_SERIAL_VIDEO_EXECUTOR / INCONCLUSIVE
        lines.append("- 正式产品保持完全串行（KEEP_SERIAL_VIDEO_EXECUTOR）。")
        lines.append("- 不引入 outstanding 并发。")
    lines.append("")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


# ============================================================================
# main
# ============================================================================
async def main():
    log("=" * 72)
    log("Agnes Video V2.0 并发 / Outstanding Task 能力实测探针")
    log("=" * 72)
    log(f"base_url  = {agnes_cfg['base_url']}")
    log(f"model     = {VIDEO_MODEL}")
    log(f"api_key   = {mask_key(client.api_key)}   <- 来源: AGNES_API_KEY env (.env)，未硬编码")
    log(f"frame_rate= {FRAME_RATE}, size={WIDTH}x{HEIGHT}")
    log(f"video RPM = {VIDEO_RPM} (safety 0.85) -> 安全 submit 间隔 ≈ {SUBMIT_INTERVAL_S:.1f}s")
    log(f"poll      = {POLL_INTERVAL}s (官方建议，走 text 桶，不占 video 桶)")
    log(f"report    = {REPORT_JSON}")
    log("=" * 72)

    report = {
        "generated_at": START_TS,
        "environment": {
            "base_url": agnes_cfg["base_url"],
            "model": VIDEO_MODEL,
            "api_key_masked": mask_key(client.api_key),
            "api_key_source": "AGNES_API_KEY env (.env)",
            "frame_rate": FRAME_RATE,
            "default_size": f"{WIDTH}x{HEIGHT}",
            "submit_interval_s": round(SUBMIT_INTERVAL_S, 1),
            "poll_interval_s": POLL_INTERVAL,
            "rate_limiter": "video bucket rpm=1 * 0.85 safety (token refill ~70.6s)",
            "num_frames_rule": "8n+1, ≤441 (720p tier); 5s=121, 10s=241, 18s=441 @24fps",
        },
        "test_image": {},
        "duration_tests": [],
        "concurrency_tests": {},
        "max_simultaneous_outstanding_observed": 0,
        "errors": [],
        "conclusion": None,
        "conclusion_reasons": [],
        "dependency_analysis": DEPENDENCY_ANALYSIS,
    }
    save_report(report)

    # ---- 0. 测试图 ----
    try:
        image_url, local_img = await make_test_image()
        report["test_image"] = {"agnes_url": image_url, "local_path": str(local_img)}
        save_report(report)
    except Exception as e:
        report["errors"].append(f"test_image_failed: {type(e).__name__}: {e}")
        log(f"[FATAL] 测试图生成失败: {e}")
        report["conclusion"] = "INCONCLUSIVE"
        report["conclusion_reasons"] = ["测试图生成失败，无法继续"]
        save_report(report)
        return report

    # ---- 验证测试图 URL 可用于 i2v（同时作为 5s 时长测试 #1）----
    log("\n" + "#" * 72)
    log("# Section 五：时长测试（5s x2 / 10s x2 / 18s x2）")
    log("#" * 72)
    log("\n[validate] 用 5s i2v 验证测试图 URL 可用性（兼作 5s#1）...")
    body5 = build_body(image_url, 121)
    await client.limiter.acquire("video")
    v = await raw_post_video(body5, "dur-5s#1")
    if not (v["ok"] and v["video_id"]):
        log(f"[validate] Agnes 测试图 URL 提交失败 http={v['http_status']}，尝试 imgbb 重新托管...")
        try:
            image_url = await rehost_via_imgbb(local_img)
            report["test_image"]["imgbb_fallback_url"] = image_url
            save_report(report)
            body5 = build_body(image_url, 121)
            await client.limiter.acquire("video")
            v = await raw_post_video(body5, "dur-5s#1")
        except Exception as e:
            report["errors"].append(f"imgbb_fallback_failed: {e}")
            log(f"[FATAL] imgbb 重新托管失败: {e}")
            report["conclusion"] = "INCONCLUSIVE"
            save_report(report)
            return report
    if not (v["ok"] and v["video_id"]):
        report["errors"].append(f"i2v submit failed even after imgbb: {v.get('body_text','')[:300]}")
        report["conclusion"] = "INCONCLUSIVE"
        save_report(report)
        return report
    state = new_state(v["submit_at"], v)
    await poll_single_to_done("5s#1", state)
    report["duration_tests"].append(summarize("5s", 121, v, state))
    save_report(report)

    # ---- 5s#2 / 10s x2 / 18s x2 ----
    for label, nf in [("5s", 121), ("10s", 241), ("10s", 241), ("18s", 441), ("18s", 441)]:
        try:
            r = await run_duration_case(label, nf, image_url)
            report["duration_tests"].append(r)
            save_report(report)
        except Exception as e:
            report["errors"].append(f"duration {label} nf={nf}: {type(e).__name__}: {e}")
            log(f"[ERROR] duration {label}: {e}")

    # 时长汇总
    log("\n[duration summary]")
    for lab in ("5s", "10s", "18s"):
        wt = [d["wall_time_s"] for d in report["duration_tests"]
              if d.get("label") == lab and d.get("wall_time_s") is not None]
        if wt:
            log(f"  {lab}: wall_times={wt} avg={sum(wt)/len(wt):.1f}s max={max(wt):.0f}s")

    # ---- Section 六：2-task ----
    log("\n" + "#" * 72)
    log("# Section 六：Outstanding 2-task Test (441 frames)")
    log("#" * 72)
    try:
        two = await run_two_task(image_url)
        report["concurrency_tests"]["two_task"] = two
        if two.get("conclusive") and two.get("max_outstanding_during_poll"):
            report["max_simultaneous_outstanding_observed"] = max(
                report["max_simultaneous_outstanding_observed"],
                two["max_outstanding_during_poll"])
        save_report(report)
    except Exception as e:
        report["errors"].append(f"two_task: {type(e).__name__}: {e}")
        log(f"[ERROR] two_task: {e}")

    # ---- Section 七：3-task（仅 2-task 真正成功后）----
    two_ok = (report["concurrency_tests"].get("two_task", {}).get("conclusive")
              and report["concurrency_tests"]["two_task"].get("b_submit_succeeded")
              and report["concurrency_tests"]["two_task"].get("both_completed"))
    if two_ok:
        log("\n" + "#" * 72)
        log("# Section 七：Outstanding 3-task Test (441 frames)")
        log("#" * 72)
        try:
            three = await run_three_task(image_url)
            report["concurrency_tests"]["three_task"] = three
            if three.get("max_outstanding_during_poll"):
                report["max_simultaneous_outstanding_observed"] = max(
                    report["max_simultaneous_outstanding_observed"],
                    three["max_outstanding_during_poll"])
            save_report(report)
        except Exception as e:
            report["errors"].append(f"three_task: {type(e).__name__}: {e}")
            log(f"[ERROR] three_task: {e}")
    else:
        log("\n[skip] 3-task 测试：2-task 未成功（inconclusive/被拒/未全部完成），跳过以不无意义消耗。")
        report["concurrency_tests"]["three_task"] = {
            "skipped": True, "reason": "2-task not conclusive/successful"}

    # ---- 结论 ----
    conc, reasons = classify(report)
    report["conclusion"] = conc
    report["conclusion_reasons"] = reasons
    save_report(report)
    write_markdown(report)

    await client.aclose()
    log("\n" + "=" * 72)
    log(f"[DONE] 结论: {conc}")
    for r in reasons:
        log(f"  - {r}")
    log(f"[DONE] JSON 报告: {REPORT_JSON}")
    log(f"[DONE] MD   报告: {REPORT_MD}")
    log("=" * 72)
    return report


if __name__ == "__main__":
    asyncio.run(main())
