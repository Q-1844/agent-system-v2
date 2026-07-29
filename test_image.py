import urllib.request
import urllib.parse

# Test image access
project = urllib.parse.quote("我的动画短片")
url = f"http://localhost:8000/projects/{project}/output/storyboards/scene_01.png"

try:
    r = urllib.request.urlopen(url)
    print(f"Status: {r.status}")
    print(f"Content-Type: {r.headers.get('Content-Type')}")
    print(f"Size: {len(r.read())} bytes")
    print("Image accessible: YES")
except Exception as e:
    print(f"Error: {e}")

# Test character image
url2 = f"http://localhost:8000/projects/{project}/output/characters/%E5%B0%8F%E6%98%9F_front.png"
try:
    r2 = urllib.request.urlopen(url2)
    print(f"\nCharacter image status: {r2.status}")
    print(f"Size: {len(r2.read())} bytes")
except Exception as e:
    print(f"\nCharacter image error: {e}")

# Test scenes API
url3 = f"http://localhost:8000/api/projects/{project}/scenes"
try:
    r3 = urllib.request.urlopen(url3)
    import json
    data = json.loads(r3.read())
    print(f"\nScenes API response:")
    for s in data.get("scenes", []):
        print(f"  Scene {s['id']}: image_path={s.get('image_path', 'NONE')}")
except Exception as e:
    print(f"\nScenes API error: {e}")
