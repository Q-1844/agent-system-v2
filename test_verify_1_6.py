"""验证 1.6 音频生成（调 Mimo TTS 生成 scene_02 对白音频）。Mimo 是国内服务，直连。"""
import httpx, time, json
from pathlib import Path

BASE = "http://localhost:8000"
PROJ = "我的动画短片"

print("=== 触发单场景音频生成 (scene 2) ===")
r = httpx.post(f"{BASE}/api/project/{PROJ}/audio/2/generate",
               json={"voice": "default", "speed": 1.0}, timeout=30)
print(f"  HTTP {r.status_code}: {json.dumps(r.json(), ensure_ascii=False)}")
if r.status_code != 200:
    raise SystemExit(1)
task_id = r.json()["task_id"]

print("\n=== 轮询 ===")
for i in range(60):
    time.sleep(2)
    s = httpx.get(f"{BASE}/api/task/{task_id}", timeout=10).json()
    print(f"  [{i:02d}] status={s['status']} msg={s.get('message','')}")
    if s["status"] in ("completed", "failed"):
        break

print("\n=== 检查结果 ===")
ap = Path(f"projects/{PROJ}/output/audio/dialog_02.mp3")
print(f"  dialog_02.mp3: exists={ap.exists()}, size={ap.stat().st_size if ap.exists() else 0} bytes")
ps = json.loads(Path(f"projects/{PROJ}/output/parsed_script.json").read_text(encoding="utf-8"))
s2 = next((s for s in ps["scenes"] if s["id"] == 2), None)
print(f"  scene 2 audio_path: {s2.get('audio_path') if s2 else 'scene not found'}")
sp = Path(f"projects/{PROJ}/output/subtitles/scene_02.srt")
print(f"  scene_02.srt: exists={sp.exists()}")
if sp.exists():
    print(f"  srt 内容: {sp.read_text(encoding='utf-8')[:80]!r}")
