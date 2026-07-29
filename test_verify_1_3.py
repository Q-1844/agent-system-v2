"""验证 1.3 剧本保存+解析接口（不依赖 Agnes，enhance=False）。用临时项目避免污染。"""
import httpx, json, shutil
from pathlib import Path

BASE = "http://localhost:8000"
TEST = "_test_script"

# 创建临时项目
httpx.post(f"{BASE}/api/projects/create", params={"name": TEST}, timeout=30)

yaml = '''project: "_test_script"
style: "anime"
size: "1152x768"

characters:
  - name: "测试员"
    description: "a test hero with red cape"

scenes:
  - id: 1
    description: "测试员站在山顶"
    characters: ["测试员"]
    camera: "wide shot"
    lighting: "daylight"
    motion: "standing"
    dialogue: "你好"
  - id: 2
    description: "测试员转身离开"
    characters: ["测试员"]
    camera: "medium shot"
    lighting: "sunset"
    motion: "turning around"
'''

print("=== 保存剧本 ===")
r = httpx.post(f"{BASE}/api/project/{TEST}/script",
               json={"script_content": yaml, "enhance": False}, timeout=60)
print(f"  HTTP {r.status_code}: {json.dumps(r.json(), ensure_ascii=False)}")

print("\n=== 验证 parsed_script.json ===")
ps = json.loads(Path(f"projects/{TEST}/output/parsed_script.json").read_text(encoding="utf-8"))
print(f"  project: {ps['project']}")
print(f"  characters: {[c['name'] for c in ps['characters']]}")
print(f"  scenes: {[(s['id'], s['description'], s.get('dialogue')) for s in ps['scenes']]}")

print("\n=== 验证 YAML 文件已保存 ===")
yaml_file = Path(f"projects/{TEST}/input/{TEST}.yaml")
print(f"  {yaml_file} exists: {yaml_file.exists()}")
print(f"  前 60 字: {yaml_file.read_text(encoding='utf-8')[:60]!r}")

# 清理
shutil.rmtree(f"projects/{TEST}", ignore_errors=True)
print(f"\n[CLEANUP] 临时项目 {TEST} 已删除")
