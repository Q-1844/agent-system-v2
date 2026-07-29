"""补全「我的动画短片」parsed_script 的 image_path/audio_path（文件存在但未记录），重新合成验证音频。"""
import httpx, time, json, subprocess
from pathlib import Path

BASE = "http://localhost:8000"
PROJ = "我的动画短片"

# 1. 补全 parsed_script
p = Path(f"projects/{PROJ}/output/parsed_script.json")
d = json.loads(p.read_text(encoding="utf-8"))
print("=== 补全前 ===")
for s in d["scenes"]:
    print(f"  scene {s['id']}: img={s.get('image_path')}, aud={s.get('audio_path')}")

for s in d["scenes"]:
    sid = s["id"]
    img = Path(f"projects/{PROJ}/output/storyboards/scene_{sid:02d}.png")
    if img.exists():
        s["image_path"] = str(img)
    aud = Path(f"projects/{PROJ}/output/audio/dialog_{sid:02d}.mp3")
    if aud.exists():
        s["audio_path"] = str(aud)
    s["status"] = "completed"
p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

print("\n=== 补全后 ===")
for s in d["scenes"]:
    print(f"  scene {s['id']}: img={bool(s.get('image_path'))}, aud={bool(s.get('audio_path'))}")

# 2. 重新合成
print("\n=== 触发合成 ===")
r = httpx.post(f"{BASE}/api/project/{PROJ}/compose",
               json={"scene_duration": 4.0, "width": 1280, "height": 720, "fps": 24}, timeout=30)
print(f"  HTTP {r.status_code}: {r.json()}")
task_id = r.json()["task_id"]

print("\n=== 轮询 ===")
for i in range(90):
    time.sleep(2)
    s = httpx.get(f"{BASE}/api/task/{task_id}", timeout=10).json()
    print(f"  [{i:02d}] status={s['status']} msg={s.get('message','')}")
    if s["status"] in ("completed", "failed"):
        print(f"  result: {s.get('result')}")
        break

# 3. 检查 final.mp4（含音频流）
print("\n=== final.mp4 流信息 ===")
fp = Path(f"projects/{PROJ}/output/final.mp4")
print(f"  大小: {fp.stat().st_size/1024/1024:.2f} MB")
probe = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,duration", "-of", "default=noprint_wrappers=1", str(fp)],
    capture_output=True, text=True)
print(probe.stdout)
dur = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(fp)],
    capture_output=True, text=True)
print(f"  总时长: {dur.stdout.strip()} 秒")
