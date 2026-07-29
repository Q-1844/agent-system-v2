"""验证 1.7 SSE 实时进度推送。用 compose 任务（不依赖 Agnes/Mimo）触发，订阅 SSE 流。"""
import httpx, json

BASE = "http://localhost:8000"
PROJ = "我的动画短片"

# 触发 compose
print("=== 触发 compose 任务 ===")
r = httpx.post(f"{BASE}/api/project/{PROJ}/compose",
               json={"scene_duration": 3.0, "width": 960, "height": 540, "fps": 24}, timeout=30)
print(f"  HTTP {r.status_code}: {r.json()}")
task_id = r.json()["task_id"]

# 订阅 SSE
print(f"\n=== 订阅 SSE /api/task/{task_id}/stream ===")
import time
start = time.time()
events = 0
with httpx.stream("GET", f"{BASE}/api/task/{task_id}/stream", timeout=60) as resp:
    print(f"  响应 Content-Type: {resp.headers.get('content-type')}")
    for line in resp.iter_lines():
        if line and line.startswith("data:"):
            data = json.loads(line[5:].strip())
            elapsed = time.time() - start
            events += 1
            print(f"  [+{elapsed:.1f}s] event#{events}: status={data.get('status')} "
                  f"msg={data.get('message','')[:50]} progress={data.get('progress')}")
            if data.get("status") in ("completed", "failed"):
                break

print(f"\n共收到 {events} 个 SSE 事件，用时 {time.time()-start:.1f}s")
