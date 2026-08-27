# scripts/probe_native_tools.py
# 独立探针：验证 Agnes agnes-2.5-flash 是否支持 OpenAI 原生 function-calling。
# 用法：python scripts/probe_native_tools.py
# 不修改任何项目代码，只发一个最小 tools 请求并打印结论。
import asyncio
import os
import sys
from pathlib import Path

# 让脚本能 import 项目模块（复用 .env 加载 + config 解析）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import yaml


def _load_config():
    base = Path(__file__).resolve().parent.parent
    # 复用 api_server.py 的 .env 加载逻辑（极简版）
    env_path = base / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k and v and not os.environ.get(k):
                    os.environ[k] = v
    cfg = yaml.safe_load((base / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    agnes = cfg.get("agnes") or {}
    api_key = os.environ.get("AGNES_API_KEY", "")
    if "${AGNES_API_KEY}" in str(agnes.get("api_key", "")):
        api_key = api_key or os.environ.get("AGNES_API_KEY", "")
    else:
        api_key = agnes.get("api_key") or api_key
    return agnes, api_key


async def probe():
    agnes, api_key = _load_config()
    base_url = (agnes.get("base_url") or "https://apihub.agnes-ai.cn").rstrip("/")
    model = (agnes.get("models") or {}).get("text") or "agnes-2.5-flash"
    proxy = agnes.get("proxy") or None

    if not api_key:
        print("RESULT: FAIL — 未找到 AGNES_API_KEY（检查 .env）")
        return

    print(f"端点: {base_url}/v1/chat/completions")
    print(f"模型: {model}")
    print(f"代理: {proxy or '（直连）'}")
    print("-" * 60)

    # 最小工具：一个带类型的函数，让模型"把当前城市天气改成晴"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个助手。需要修改剧本时，调用 update_script 工具。"},
            {"role": "user", "content": "请把剧本正文改成：你好，世界。"},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "update_script",
                "description": "用新的完整剧本正文覆盖当前剧本。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "new_story_text": {
                            "type": "string",
                            "description": "修改后的完整剧本正文。",
                        }
                    },
                    "required": ["new_story_text"],
                },
            },
        }],
        "tool_choice": "auto",
        "temperature": 0.3,
        "max_tokens": 2048,
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=60, follow_redirects=True) as c:
            r = await c.post(f"{base_url}/v1/chat/completions", json=payload, headers=headers)
    except Exception as e:
        print(f"RESULT: FAIL — 网络错误: {e}")
        return

    print(f"HTTP {r.status_code}")
    if r.status_code >= 400:
        print(f"响应体（前 800 字）: {r.text[:800]}")
        print("\n结论：原生 tools 不被接受（返回 4xx）。→ 回退 JSON 解析方案。")
        return

    data = r.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    content = msg.get("content")
    tool_calls = msg.get("tool_calls")

    print("-" * 60)
    print(f"content: {repr(content)[:200]}")
    print(f"tool_calls: {tool_calls}")
    print("-" * 60)

    if tool_calls:
        tc = tool_calls[0]
        fn = tc.get("function", {})
        print(f"✅ 原生 function-calling 可用！")
        print(f"   工具名: {fn.get('name')}")
        print(f"   参数: {fn.get('arguments')}")
        print("   → runner 走原生 tool_calls 循环。")
    else:
        print("⚠️ 请求成功但模型没有返回 tool_calls（只返回了 content）。")
        print("   可能原因：模型未遵循 tools 指令 / 端点忽略了 tools 字段。")
        print("   需要再测一次或回退 JSON 方案。")


if __name__ == "__main__":
    asyncio.run(probe())
