import json
import urllib.request

# Test projects API
try:
    resp = urllib.request.urlopen("http://localhost:8000/api/projects")
    data = json.loads(resp.read())
    print("=== Projects ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
except Exception as e:
    print(f"Projects API error: {e}")

# Test scenes API
try:
    import urllib.parse
    project = urllib.parse.quote("我的动画短片")
    resp = urllib.request.urlopen(f"http://localhost:8000/api/projects/{project}/scenes")
    data = json.loads(resp.read())
    print("\n=== Scenes ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
except Exception as e:
    print(f"Scenes API error: {e}")

# Test characters API
try:
    resp = urllib.request.urlopen(f"http://localhost:8000/api/projects/{project}/characters")
    data = json.loads(resp.read())
    print("\n=== Characters ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
except Exception as e:
    print(f"Characters API error: {e}")

# Test current task
try:
    resp = urllib.request.urlopen("http://localhost:8000/api/task/current")
    data = json.loads(resp.read())
    print("\n=== Current Task ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
except Exception as e:
    print(f"Task API error: {e}")
