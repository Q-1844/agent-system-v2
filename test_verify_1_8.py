"""验证 1.8 ffmpeg 最终合成。用「我的动画短片」的 3 张分镜图 + 2 段音频合成 final.mp4。"""
import httpx, time, json
from pathlib import Path

BASE = "http://localhost:8000"
PROJ = "我的动画短片"

print("=== 触发合成 ===")
r = httpx.post(f"{BASE}/api/project/{PROJ}/compose",
               json={"scene_duration": 4.0, "width": 1280, "height": 720, "fps": 24},
               timeout=30)
print(f"  HTTP {r.status_code}: {json.dumps(r.json(), ensure_ascii=False)}")
if r.status_code != 200:
    raise SystemExit(1)
task_id = r.json()["task_id"]

print("\n=== 轮询任务 ===")
for i in range(90):
    time.sleep(2)
    s = httpx.get(f"{BASE}/api/task/{task_id}", timeout=10).json()
    print(f"  [{i:02d}] status={s['status']} msg={s.get('message','')}")
    if s["status"] in ("completed", "failed"):
        print(f"  result: {s.get('result')}")
        break

print("\n=== 检查 final.mp4 ===")
fp = Path(f"projects/{PROJ}/output/final.mp4")
if fp.exists():
    size_mb = fp.stat().st_size / 1024 / 1024
    print(f"  ✅ final.mp4 已生成: {fp}")
    print(f"     大小: {size_mb:.2f} MB")
    # ffprobe 看时长
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(fp)],
        capture_output=True, text=True)
    print(f"     时长: {probe.stdout.strip()} 秒")
else:
    print(f"  ❌ final.mp4 未生成")
