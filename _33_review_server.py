# -*- coding: utf-8 -*-
"""33.md 人评服务：静态托管 _33_fox_calibration/ + FEEDBACK.json 落盘。

GET  /              → GALLERY.html 等静态文件
GET  /api/feedback  → 当前 FEEDBACK.json（无则 {}）
POST /api/feedback  → body={"fb": { "<shot>_<ref>": {"state": "...", "note": "..."} }}
                      合并写入 _33_fox_calibration/FEEDBACK.json（原子写）

用法：python _33_review_server.py [port]   （默认 8779；33 与 32 评审服务并存不同端口）
"""
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
E33 = ROOT / "_33_fox_calibration"
FEEDBACK = E33 / "FEEDBACK.json"


def _load():
    if FEEDBACK.exists():
        try:
            return json.loads(FEEDBACK.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict):
    tmp = FEEDBACK.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, FEEDBACK)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(E33), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/feedback":
            self._send_json(_load())
            return
        super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] == "/api/feedback":
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                fb = body.get("fb") or {}
                data = _load()
                data.setdefault("fb", {}).update(fb)
                data["_updated_at"] = int(__import__("time").time())
                _save(data)
                self._send_json({"ok": True, "keys": len(data["fb"])})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)[:200]}, 400)
            return
        self.send_error(404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8779
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
