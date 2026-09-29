# -*- coding: utf-8 -*-
"""42.md 评审服务：静态托管 _video25_probe/ + FEEDBACK.json 落盘（33 模式复用）。

GET  /                → EXP2_REVIEW.html 等静态文件
GET  /api/feedback    → 当前 FEEDBACK.json（无则 {}）
POST /api/feedback    → body={"fb": {...}}  合并写入 _video25_probe/FEEDBACK.json（原子写）

用法：python _42_review_server.py [port]   （默认 8780）
"""
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
E42 = ROOT / "_video25_probe"
FEEDBACK = E42 / "FEEDBACK.json"


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
        super().__init__(*a, directory=str(E42), **kw)

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
        if self.path.split("?")[0] != "/api/feedback":
            self._send_json({"ok": False, "error": "not_found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            fb = body.get("fb")
            if not isinstance(fb, dict):
                self._send_json({"ok": False, "error": "bad_body"}, 400)
                return
            cur = _load()
            cur.update(fb)
            _save(cur)
            self._send_json({"ok": True, "keys": len(cur)})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)[:200]}, 500)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8780
    print(f"42 review server: http://127.0.0.1:{port}/EXP2_REVIEW.html "
          f"→ {FEEDBACK}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
