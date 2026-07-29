"""验证 1.1（full->back 兼容）+ 1.2（bind/update 接口），不依赖 Agnes。用临时项目避免污染。"""
import httpx, base64, json, shutil
from pathlib import Path

BASE = "http://localhost:8000"
os_environ = None

# A. full -> back 兼容（只读「我的动画短片」）
print("=" * 60)
print("A. full->back 兼容验证（GET /api/projects/我的动画短片/characters）")
r = httpx.get(f"{BASE}/api/projects/我的动画短片/characters", timeout=30)
print(f"  HTTP {r.status_code}")
for c in r.json().get("characters", []):
    ri = c.get("reference_images", {})
    print(f"  角色 {c['name']}: reference_images keys = {list(ri.keys())}")

# B. bind 接口（临时项目 _test_bind_verify）
print("\n" + "=" * 60)
print("B. bind 接口验证（临时项目 _test_bind_verify）")
TEST = "_test_bind_verify"
httpx.post(f"{BASE}/api/projects/create", params={"name": TEST}, timeout=30)

img = Path("projects/我的动画短片/output/storyboards/scene_01.png")
b64 = base64.b64encode(img.read_bytes()).decode()

r = httpx.post(f"{BASE}/api/project/{TEST}/character/bind", json={
    "name": "测试角色",
    "description": "a test character",
    "views": {"front": {"raw": b64, "seg": b64}, "side": {"raw": b64}, "back": {"raw": b64}},
}, timeout=60)
print(f"  POST bind: HTTP {r.status_code}")
print(f"  response: {json.dumps(r.json(), ensure_ascii=False)}")

char_dir = Path(f"projects/{TEST}/output/characters")
print(f"  落盘文件: {sorted(f.name for f in char_dir.glob('*.png'))}")

script = json.loads(Path(f"projects/{TEST}/output/parsed_script.json").read_text(encoding="utf-8"))
chars = [(c["name"], list(c.get("reference_images", {}).keys())) for c in script.get("characters", [])]
print(f"  parsed_script 角色: {chars}")

# C. update 接口（改名 + 改描述）
print("\n" + "=" * 60)
print("C. update 接口验证（改名「测试角色」->「改名后角色」）")
r = httpx.put(f"{BASE}/api/project/{TEST}/character/测试角色",
              json={"new_name": "改名后角色", "description": "updated desc"}, timeout=30)
print(f"  PUT update: HTTP {r.status_code} {json.dumps(r.json(), ensure_ascii=False)}")
print(f"  改名后文件: {sorted(f.name for f in char_dir.glob('*.png'))}")
script2 = json.loads(Path(f"projects/{TEST}/output/parsed_script.json").read_text(encoding="utf-8"))
print(f"  改名后 parsed_script 角色: {[(c['name'], list(c.get('reference_images',{}).keys())) for c in script2.get('characters',[])]}")

# 清理
shutil.rmtree(f"projects/{TEST}", ignore_errors=True)
print(f"\n[CLEANUP] 临时项目 {TEST} 已删除")
print("=" * 60)
print("验证完成")
