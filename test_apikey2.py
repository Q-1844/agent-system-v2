# -*- coding: utf-8 -*-
import urllib.request
import json
import sys
import io

# 修复Windows终端编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

API_KEY = "sk-jC7GlV9A7qxcFvPqRPqeowwE6NUaS1U4RXUm3pcQu9TipTDq"
BASE_URL = "https://apihub.agnes-ai.cn"

# 测试1: 文本理解接口
print("=" * 50)
print("TEST 1: Text API (agnes-2.0-flash)")
print("=" * 50)

try:
    data = json.dumps({
        "model": "agnes-2.0-flash",
        "messages": [{"role": "user", "content": "Hello, say OK"}],
        "temperature": 0.7,
        "max_tokens": 10,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE_URL}/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )

    resp = urllib.request.urlopen(req, timeout=15)
    result = json.loads(resp.read())
    print(f"Status: {resp.status}")
    print(f"Reply: {result['choices'][0]['message']['content']}")
    print("[PASS] Text API works!")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    print(f"[FAIL] HTTP Error: {e.code}")
    print(f"Response: {body}")
except Exception as e:
    print(f"[FAIL] Request failed: {e}")

# 测试2: 图片生成接口
print()
print("=" * 50)
print("TEST 2: Image API (agnes-image-2.1-flash)")
print("=" * 50)

try:
    data = json.dumps({
        "model": "agnes-image-2.1-flash",
        "prompt": "a simple red circle on white background",
        "size": "512x512",
        "extra_body": {"response_format": "url"},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE_URL}/v1/images/generations",
        data=data,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )

    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    print(f"Status: {resp.status}")
    if "data" in result and len(result["data"]) > 0:
        url = result["data"][0].get("url", "No URL")
        print(f"Image URL: {url[:100]}...")
    print("[PASS] Image API works!")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    print(f"[FAIL] HTTP Error: {e.code}")
    print(f"Response: {body}")
except Exception as e:
    print(f"[FAIL] Request failed: {e}")

print()
print("=" * 50)
print("DONE")
print("=" * 50)
