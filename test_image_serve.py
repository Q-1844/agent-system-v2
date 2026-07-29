# -*- coding: utf-8 -*-
import urllib.request
import urllib.parse
import json
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE = "http://localhost:8000"

# Test 1: API health
print("=" * 50)
print("TEST 1: Backend Health")
print("=" * 50)
try:
    resp = urllib.request.urlopen(f"{BASE}/api/health", timeout=5)
    data = json.loads(resp.read())
    print(f"Status: {data}")
except Exception as e:
    print(f"[FAIL] {e}")

# Test 2: Scenes API
print()
print("=" * 50)
print("TEST 2: Scenes API")
print("=" * 50)
try:
    project = urllib.parse.quote("我的动画短片")
    resp = urllib.request.urlopen(f"{BASE}/api/projects/{project}/scenes", timeout=10)
    data = json.loads(resp.read())
    for s in data.get("scenes", []):
        img = s.get("image_path", "NONE")
        print(f"  Scene {s['id']}: status={s.get('status')}, image={img}")
except Exception as e:
    print(f"[FAIL] {e}")

# Test 3: Image endpoint
print()
print("=" * 50)
print("TEST 3: Image Endpoint")
print("=" * 50)
try:
    project = urllib.parse.quote("我的动画短片")
    url = f"{BASE}/api/image/{project}/storyboards/scene_01.png"
    resp = urllib.request.urlopen(url, timeout=10)
    print(f"Status: {resp.status}")
    print(f"Content-Type: {resp.headers.get('Content-Type')}")
    print(f"Size: {len(resp.read())} bytes")
    print("[PASS] Image accessible!")
except urllib.error.HTTPError as e:
    print(f"[FAIL] HTTP {e.code}: {e.reason}")
except Exception as e:
    print(f"[FAIL] {e}")

# Test 4: Static file mount
print()
print("=" * 50)
print("TEST 4: Static File Mount")
print("=" * 50)
try:
    project = urllib.parse.quote("我的动画短片")
    url = f"{BASE}/projects/{project}/output/storyboards/scene_01.png"
    resp = urllib.request.urlopen(url, timeout=10)
    print(f"Status: {resp.status}")
    print(f"Size: {len(resp.read())} bytes")
    print("[PASS] Static file accessible!")
except urllib.error.HTTPError as e:
    print(f"[FAIL] HTTP {e.code}: {e.reason}")
except Exception as e:
    print(f"[FAIL] {e}")

print()
print("=" * 50)
print("DONE")
print("=" * 50)
