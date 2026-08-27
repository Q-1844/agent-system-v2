"""scripts/probe_stream_agnes.py -- AI-7D-R UI 阶段第 0 步：Agnes agnes-2.5-flash
streaming capability probe（一次性探针，不修改任何正式业务代码）。

镜像 src/api/agnes_client.py::chat_completion 的请求格式（同 base_url / Bearer auth /
model / temperature / max_tokens / tools / tool_choice），仅增加 stream=true。

组 1  纯文本，body 与 chat_completion 完全一致 + stream=true（不加任何新参数）
组 1b 纯文本 + stream_options={"include_usage": true}（仅探测 usage 通道是否支持）
组 2  最简 function tool（get_weather），观察 tool_calls 分片与聚合
组 3  enable_thinking=true（chat_template_kwargs，代码已有该参数位；不改正式配置）
      3a thinking + 纯文本 -> reasoning 字段名 / 顺序 / TTFT
      3b thinking + tool   -> reasoning 与 tool_calls 的顺序
      3c 3b 的 assistant 回传（带/不带 reasoning 字段各一次）-> API 接受性

输出：脱敏（绝不打印 API key）的原始 SSE chunk 样本 + 字段统计 + TTFT/总时长。
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent          # animation-storyboard/


def _load_base_url_and_model():
    """与生产一致：读 config.yaml 的 agnes.base_url / agnes.models.text。"""
    txt = (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
    try:
        import yaml
        ag = yaml.safe_load(txt)["agnes"]
        return ag["base_url"], ag["models"]["text"]
    except Exception:
        # fallback：手动剥注释
        base = model = None
        for line in txt.splitlines():
            s = line.split("#", 1)[0].strip()
            if s.startswith("base_url:") and base is None:
                base = s.split(":", 1)[1].strip().strip('"').strip("'")
            if s.startswith("text:") and model is None:
                model = s.split(":", 1)[1].strip().strip('"').strip("'")
        return base, model


def _load_key():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("AGNES_API_KEY") and "=" in s:
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("AGNES_API_KEY")


BASE_URL, MODEL = _load_base_url_and_model()
KEY = _load_key()
if not KEY:
    raise SystemExit("AGNES_API_KEY 未找到（.env / 环境变量）")

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市的当前天气",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    },
}]

REASONISH = ("reason", "think", "analysis", "reflect", "chain")


def _trunc(s: str, n: int = 240) -> str:
    return s if len(s) <= n else s[:n] + f"…(+{len(s)-n} chars)"


async def stream_once(body: dict, label: str, show_head: int = 14, show_tail: int = 6) -> dict:
    headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    raw_lines: list[str] = []
    ttft = None
    status = ct = None
    async with httpx.AsyncClient(
            base_url=BASE_URL, headers=headers,
            timeout=httpx.Timeout(connect=30, read=120, write=30, pool=30)) as c:
        async with c.stream("POST", "/v1/chat/completions", json=body) as r:
            status, ct = r.status_code, r.headers.get("content-type")
            if status != 200:
                txt = (await r.aread()).decode("utf-8", "replace")[:400]
                print(f"\n===== {label} =====")
                print(f"[request] POST {BASE_URL}/v1/chat/completions  stream={body.get('stream')}")
                print(f"[FAIL] HTTP {status}  Content-Type: {ct}")
                print(f"[body] {txt}")
                return {"ok": False}
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                raw_lines.append(line)
                if ttft is None and line.startswith("data:"):
                    ttft = time.perf_counter() - t0
    total = time.perf_counter() - t0

    # ---- 解析 ----
    json_chunks, events, done_seen, unparsable = [], [], False, []
    for line in raw_lines:
        if line.startswith(":") or line.startswith("event:"):
            events.append(line)
            continue
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                done_seen = True
                continue
            try:
                json_chunks.append(json.loads(payload))
            except Exception:
                unparsable.append(payload)

    delta_keys: set = set()
    chunk_keys: set = set()
    finish_reasons: set = set()
    usage = None
    content_parts: list[str] = []
    tool_aggs: dict[int, dict] = {}
    reasonish_hits: list[str] = []
    for ch in json_chunks:
        chunk_keys.update(ch.keys())
        u = ch.get("usage")
        if u:
            usage = u
        for choice in ch.get("choices") or []:
            fr = choice.get("finish_reason")
            if fr:
                finish_reasons.add(fr)
            d = choice.get("delta") or {}
            delta_keys.update(d.keys())
            if d.get("content"):
                content_parts.append(d["content"])
            for frag in d.get("tool_calls") or []:
                idx = frag.get("index", 0)
                a = tool_aggs.setdefault(idx, {"id": "", "name": "", "args": ""})
                if frag.get("id"):
                    a["id"] += frag["id"]
                fn = frag.get("function") or {}
                if fn.get("name"):
                    a["name"] += fn["name"]
                if fn.get("arguments"):
                    a["args"] += fn["arguments"]

    # 原始行里搜 reasoning 类字段名（不猜：直接对原始 payload 找）
    for line in raw_lines:
        if line.startswith("data:"):
            pl = line[5:].strip()
            for kw in REASONISH:
                if f'"{kw}' in pl.lower() or f"{kw}\":" in pl.lower():
                    for m in json_chunks:
                        pass
            # 直接在 json chunk 里找含这些子串的键（含嵌套 delta）
    def _walk(o, hits):
        if isinstance(o, dict):
            for k, v in o.items():
                if any(kw in k.lower() for kw in REASONISH):
                    hits.add(k)
                _walk(v, hits)
        elif isinstance(o, list):
            for x in o:
                _walk(x, hits)
    reasonish_keys: set = set()
    for ch in json_chunks:
        _walk(ch, reasonish_keys)

    # ---- 输出 ----
    print(f"\n===== {label} =====")
    print(f"[request] POST {BASE_URL}/v1/chat/completions  model={MODEL} stream={body.get('stream')}"
          + (f"  +chat_template_kwargs={body.get('chat_template_kwargs')}" if body.get("chat_template_kwargs") else "")
          + (f"  +tools=[{', '.join(t['function']['name'] for t in body['tools'])}]" if body.get("tools") else "")
          + (f"  +stream_options={body.get('stream_options')}" if body.get("stream_options") else ""))
    print(f"[response] HTTP {status}  Content-Type: {ct}")
    print(f"[timing] TTFT(首个data行)={ttft and round(ttft,2)}s  总时长={round(total,2)}s  SSE行数={len(raw_lines)}")
    print(f"[raw head {show_head}]")
    for line in raw_lines[:show_head]:
        print("  " + _trunc(line))
    if len(raw_lines) > show_head + show_tail:
        print(f"[raw tail {show_tail}]")
        for line in raw_lines[-show_tail:]:
            print("  " + _trunc(line))
    print(f"[解析] json_chunks={len(json_chunks)}  [DONE]={done_seen}  event行={events or '无'}  "
          f"unparsable={len(unparsable)}")
    print(f"[chunk顶层键] {sorted(chunk_keys)}")
    print(f"[delta键] {sorted(delta_keys)}")
    print(f"[finish_reason] {finish_reasons or '未见'}")
    print(f"[usage] {json.dumps(usage, ensure_ascii=False) if usage else '未见'}")
    print(f"[reasoning类字段(全chunk递归搜)] {sorted(reasonish_keys) or '未见'}")
    if content_parts:
        print(f"[delta.content 聚合] {_trunc(''.join(content_parts), 200)}")
    if tool_aggs:
        for idx, a in sorted(tool_aggs.items()):
            print(f"[tool_calls[{idx}] 聚合] id={a['id'] or '(未在此流出现)'}  name={a['name']}  "
                  f"arguments={a['args'] or '(空)'}")
            print(f"    分片说明: 见上方 raw chunks 中 tool_calls 片段")
    return {"ok": True, "json_chunks": json_chunks, "raw_lines": raw_lines,
            "content": "".join(content_parts), "tool_aggs": tool_aggs,
            "ttft": ttft, "total": total}


async def main():
    print(f"probe target: {BASE_URL}  model: {MODEL}  (key 已加载, 不打印)")

    # ---- 组 1: 纯文本, 只加 stream=true ----
    body1 = {"model": MODEL, "messages": [
        {"role": "user", "content": "从 1 数到 5，只输出数字，用空格分隔。"}],
        "temperature": 0.7, "max_tokens": 2048, "stream": True}
    r1 = await stream_once(body1, "组1 · 纯文本 · 仅 stream=true")

    # ---- 组 1b: + stream_options include_usage ----
    body1b = dict(body1)
    body1b["stream_options"] = {"include_usage": True}
    await stream_once(body1b, "组1b · 纯文本 · stream_options.include_usage")

    # ---- 组 2: 最简 function tool ----
    body2 = {"model": MODEL, "messages": [
        {"role": "user", "content": "北京现在天气怎么样？请调用工具查询。"}],
        "temperature": 0.7, "max_tokens": 2048,
        "tools": TOOLS, "tool_choice": "auto", "stream": True}
    r2 = await stream_once(body2, "组2 · function tool call")

    # ---- 组 3a: thinking + 纯文本 ----
    body3a = {"model": MODEL, "messages": [
        {"role": "user", "content": "计算 17*23 并告诉我结果。"}],
        "temperature": 0.7, "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": True}, "stream": True}
    await stream_once(body3a, "组3a · enable_thinking + 纯文本")

    # ---- 组 3b: thinking + tool ----
    body3b = {"model": MODEL, "messages": [
        {"role": "user", "content": "北京现在天气怎么样？请调用工具查询。"}],
        "temperature": 0.7, "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": True},
        "tools": TOOLS, "tool_choice": "auto", "stream": True}
    r3b = await stream_once(body3b, "组3b · enable_thinking + tool call")

    # ---- 组 3c: 回传历史（带/不带 reasoning 字段）----
    if r2.get("ok") and r2.get("tool_aggs"):
        a = list(r2["tool_aggs"].values())[0]
        assistant_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": a["id"] or "call_probe_1", "type": "function",
                            "function": {"name": a["name"], "arguments": a["args"]}}],
        }
        base_hist = [
            {"role": "user", "content": "北京现在天气怎么样？请调用工具查询。"},
            assistant_msg,
            {"role": "tool", "tool_call_id": a["id"] or "call_probe_1",
             "content": json.dumps({"city": "北京", "weather": "晴, 31度"}, ensure_ascii=False)},
        ]
        # c-1: 不带 reasoning 字段（当前 script_writer.py 的做法）
        body_c1 = {"model": MODEL, "messages": base_hist + [
            {"role": "user", "content": "谢谢。请用一句话告诉我北京多少度。"}],
            "temperature": 0.7, "max_tokens": 2048, "stream": True}
        await stream_once(body_c1, "组3c-1 · 回传历史【不带】reasoning 字段")
        # c-2: 带 reasoning_content 字段（若 3b 出现了 reasoning 字段名则用真名）
        rk = "reasoning_content"
        # 从 3b 找真实 reasoning 字段名
        if r3b.get("ok"):
            for ch in r3b.get("json_chunks", []):
                for choice in ch.get("choices") or []:
                    d = choice.get("delta") or {}
                    for k in d:
                        if any(kw in k.lower() for kw in REASONISH):
                            rk = k
                            break
        assistant_with_reason = dict(assistant_msg)
        assistant_with_reason[rk] = "(probe 注入的假 reasoning 文本，测试字段接受性)"
        body_c2 = {"model": MODEL, "messages": [
            base_hist[0], assistant_with_reason, base_hist[2],
            {"role": "user", "content": "谢谢。请用一句话告诉我北京多少度。"}],
            "temperature": 0.7, "max_tokens": 2048, "stream": True}
        await stream_once(body_c2, f"组3c-2 · 回传历史【带】{rk} 字段")

    print("\n===== PROBE 完成 =====")


if __name__ == "__main__":
    asyncio.run(main())
