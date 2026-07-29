# -*- coding: utf-8 -*-
import urllib.request
import json
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

API_KEY = "sk-jC7GlV9A7qxcFvPqRPqeowwE6NUaS1U4RXUm3pcQu9TipTDq"
BASE_URL = "https://apihub.agnes-ai.cn"

print("Testing Text API with 60s timeout...")

try:
    data = json.dumps({
        "model": "agnes-2.0-flash",
        "messages": [{"role": "user", "content": "Say hello in one word"}],
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

    resp = urllib.request.urlopen(req, timeout=60)
    result = json.loads(resp.read())
    print(f"Status: {resp.status}")
    print(f"Reply: {result['choices'][0]['message']['content']}")
    print("[PASS]")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    print(f"[FAIL] HTTP Error: {e.code}")
    print(f"Response: {body}")
except Exception as e:
    print(f"[FAIL] {e}")
