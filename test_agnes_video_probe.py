"""
Agnes 视频 API 分层诊断脚本（只读）
诊断顺序：
  A. 连通性: GET base_url
  B. 最小 POST /v1/videos (只 model+prompt) -> 看返回的错误提示缺什么参数
  C. 完整 POST 格式A: extra_body.image=[data_url]  (当前 agnes_client.py 的写法)
  D. 完整 POST 格式B: image=[data_url] 顶层       (openai SDK extra_body 展开后的等价写法)
  E. 哪个返回 200/video_id，就轮询 GET /agnesapi
每步打印完整返回；连接级错误重试 2 次。
"""

import os, sys, base64, json, time
from pathlib import Path

os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).parent
env_path = BASE / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import yaml, httpx

config = yaml.safe_load((BASE / "config" / "config.yaml").read_text(encoding="utf-8"))
agnes = config["agnes"]
base_url = agnes["base_url"].rstrip("/")
api_key = os.environ.get("AGNES_API_KEY", "")
video_model = agnes["models"]["video"]
num_frames = 121  # 阶段5：旧 agnes.settings 块已删，新代码用 snap_num_frames（默认 121）
frame_rate = agnes["video"]["frame_rate"]

print(f"[INFO] base_url={base_url}")
print(f"[INFO] api_key={api_key[:12]}... (len={len(api_key)})")
print(f"[INFO] model={video_model} num_frames={num_frames} frame_rate={frame_rate}")
print("=" * 70)

headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def post_with_retry(url, payload, retries=3, timeout=120):
    last = None
    for i in range(retries):
        try:
            with httpx.Client(timeout=timeout) as c:
                r = c.post(url, headers=headers, json=payload)
            return r
        except Exception as e:
            last = e
            print(f"    [retry {i+1}/{retries}] {type(e).__name__}: {e}")
            time.sleep(3)
    return last


def show_resp(label, r):
    if isinstance(r, Exception):
        print(f"{label}: 请求异常 {type(r).__name__}: {r}")
        return None
    print(f"{label}: HTTP {r.status_code}")
    try:
        j = r.json()
        print(f"{label}: body =\n{json.dumps(j, ensure_ascii=False, indent=2)}")
        return j
    except Exception:
        print(f"{label}: body(text) =\n{r.text[:1500]}")
        return None


# ===== A. 连通性 =====
print("[A] 连通性 GET base_url ...")
try:
    with httpx.Client(timeout=20) as c:
        r = c.get(base_url)
    print(f"[A] HTTP {r.status_code}, body[:200]={r.text[:200]!r}")
except Exception as e:
    print(f"[A] 连通性异常: {type(e).__name__}: {e}")

# ===== B. 最小请求（诊断参数要求）=====
print("\n[B] 最小 POST /v1/videos (model+prompt) ...")
r = post_with_retry(f"{base_url}/v1/videos", {"model": video_model, "prompt": "a cat walking"}, retries=2)
jB = show_resp("[B]", r)

# ===== 准备测试图 data_url =====
img_path = BASE / "projects" / "我的动画短片" / "output" / "storyboards" / "scene_01.png"
if not img_path.exists():
    cand = list((BASE / "projects" / "我的动画短片" / "output" / "characters").glob("*.png"))
    if cand:
        img_path = cand[0]
img_b64 = base64.b64encode(img_path.read_bytes()).decode()
data_url = f"data:image/png;base64,{img_b64}"
print(f"\n[INFO] 测试图={img_path.name}, b64len={len(img_b64)}")

prompt = "gentle wind blowing hair and cloak, subtle camera push-in, cinematic"

# ===== C. 格式A: extra_body 嵌套（当前代码写法）=====
print("\n[C] 格式A: extra_body.image=[data_url], extra_body.mode=ti2vid ...")
payloadA = {
    "model": video_model,
    "prompt": prompt,
    "extra_body": {"image": [data_url], "mode": "ti2vid"},
    "num_frames": num_frames,
    "frame_rate": frame_rate,
}
r = post_with_retry(f"{base_url}/v1/videos", payloadA, retries=3)
jC = show_resp("[C]", r)

# ===== D. 格式B: image/mode 顶层 =====
print("\n[D] 格式B: image=[data_url] 顶层, mode 顶层 ...")
payloadB = {
    "model": video_model,
    "prompt": prompt,
    "image": [data_url],
    "mode": "ti2vid",
    "num_frames": num_frames,
    "frame_rate": frame_rate,
}
r = post_with_retry(f"{base_url}/v1/videos", payloadB, retries=3)
jD = show_resp("[D]", r)

# ===== E. 选一个成功的轮询 =====
chosen = None
chosen_label = None
for label, j in (("[C]", jC), ("[D]", jD)):
    if isinstance(j, dict):
        # 200 且有 video_id 视为成功
        vid = j.get("video_id") or j.get("id") or (j.get("data", {}) or {}).get("video_id") or (j.get("data", {}) or {}).get("id")
        if vid:
            chosen = vid
            chosen_label = label
            break

if not chosen:
    print("\n[E] 所有格式均未返回 video_id。请根据上面 [B][C][D] 的错误信息调整请求格式。")
    # 额外尝试：格式E image 不用 list
    print("\n[E'] 格式E: image=data_url(字符串非list) 顶层 ...")
    payloadE = {
        "model": video_model, "prompt": prompt, "image": data_url, "mode": "ti2vid",
        "num_frames": num_frames, "frame_rate": frame_rate,
    }
    r = post_with_retry(f"{base_url}/v1/videos", payloadE, retries=2)
    jE = show_resp("[E']", r)
    if isinstance(jE, dict):
        vid = jE.get("video_id") or jE.get("id") or (jE.get("data", {}) or {}).get("video_id")
        if vid:
            chosen = vid
            chosen_label = "[E']"

if not chosen:
    print("\n[END] 未能创建视频任务，探测终止。")
    sys.exit(0)

print(f"\n{'='*70}\n[E] 用 {chosen_label} 的 video_id={chosen} 轮询 GET /agnesapi ...")
final_json = None
with httpx.Client(timeout=60) as c:
    for i in range(72):  # 约 6 分钟
        time.sleep(5)
        try:
            r = c.get(f"{base_url}/agnesapi", headers=headers, params={"video_id": chosen})
        except Exception as e:
            print(f"[POLL {i+1:02d}] 异常: {type(e).__name__}: {e}")
            continue
        try:
            jj = r.json()
        except Exception:
            print(f"[POLL {i+1:02d}] HTTP {r.status_code} 非 JSON: {r.text[:200]}")
            continue
        status = jj.get("status", "(none)")
        if i == 0 or status in ("completed", "failed") or (i + 1) % 6 == 0:
            print(f"[POLL {i+1:02d}] HTTP {r.status_code} status={status}\n{json.dumps(jj, ensure_ascii=False, indent=2)}")
        else:
            print(f"[POLL {i+1:02d}] HTTP {r.status_code} status={status}")
        if status == "completed":
            final_json = jj
            print("\n[DONE] 完成！顶层字段=", list(jj.keys()))
            for k, v in jj.items():
                if isinstance(v, str) and ("http" in v.lower() or ".mp4" in v.lower() or "url" in k.lower()):
                    print(f"   >>> {k} = {v}")
                elif isinstance(v, dict):
                    for k2, v2 in v.items():
                        if isinstance(v2, str) and ("http" in v2.lower() or ".mp4" in v2.lower() or "url" in k2.lower()):
                            print(f"   >>> {k}.{k2} = {v2}")
            break
        if status == "failed":
            final_json = jj
            print("\n[FAIL] 失败，见上。")
            break

if final_json is None:
    print("\n[TIMEOUT] 未在限定时间内完成。")
print("\n[END] 探测结束。")
