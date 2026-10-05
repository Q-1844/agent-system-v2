# _p48_a_outbound_thinking_probe.py — Phase48 A批：文本出站 thinking 真实核验。
# 通过标准：出站请求体实际含 chat_template_kwargs.enable_thinking=true（抓包级证据，
# 不是配置推定）；响应含 reasoning_content（思考真实生效）；模型=agnes-3.0-flash。
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")

_env_path = Path(".env")
if _env_path.is_file():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

import httpx

captured: list = []

_orig_send = httpx.AsyncClient.send


async def _send(self, request, **kw):
    if "chat/completions" in str(request.url):
        rec = {"url": str(request.url)}
        try:
            body = json.loads(request.content.decode("utf-8"))
            rec.update({
                "model": body.get("model"),
                "thinking_kwargs": body.get("chat_template_kwargs"),
                "messages_n": len(body.get("messages") or []),
                "stream": body.get("stream"),
            })
        except Exception as e:
            rec["capture_error"] = str(e)
        resp = await _orig_send(self, request, **kw)
        try:
            rj = json.loads(resp.read().decode("utf-8"))
            msg = ((rj.get("choices") or [{}])[0].get("message") or {})
            rec["response_model"] = rj.get("model")
            rec["has_reasoning_content"] = bool(msg.get("reasoning_content"))
            rec["reasoning_head"] = (msg.get("reasoning_content") or "")[:120]
            rec["content_head"] = (msg.get("content") or "")[:120]
        except Exception as e:
            rec["response_capture_error"] = str(e)
        captured.append(rec)
        return resp
    return await _orig_send(self, request, **kw)


httpx.AsyncClient.send = _send


async def main():
    from src.pipeline.controller import PipelineController
    ctrl = PipelineController()
    client = ctrl.agnes_client
    msg = await client.chat_completion(
        [{"role": "user", "content": "用一句话回答：1+1 等于几？先在心里算一遍再答。"}],
        thinking=True, max_tokens=512)
    return msg


if __name__ == "__main__":
    msg = asyncio.run(main())
    result = {
        "probe": "p48_a_outbound_thinking",
        "captured_outbound_requests": captured,
        "returned_role": msg.get("role"),
        "returned_content_head": (msg.get("content") or "")[:120],
    }
    Path("_p48_a_outbound_thinking_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
