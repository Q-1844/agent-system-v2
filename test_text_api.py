# -*- coding: utf-8 -*-
import urllib.request
import json
import sys
import io

import os as _os
import re as _re


def _api_key(name="AGNES_API_KEY"):
    """Key 只从环境变量 / 项目根 .env 读取，禁止硬编码进源码。"""
    if _os.environ.get(name):
        return _os.environ[name]
    _p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".env")
    for _line in open(_p, encoding="utf-8"):
        _m = _re.match(rf"\s*{name}\s*=\s*(\S+)", _line)
        if _m:
            return _m.group(1)
    raise SystemExit(f"{name} 未配置（.env 或环境变量），禁止硬编码进源码")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

API_KEY = _api_key("AGNES_API_KEY")
BASE_URL = "https://apihub.agnes-ai.cn"

print("Testing Text API with 60s timeout...")

try:
    data = json.dumps({
        "model": "agnes-2.5-flash",
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
