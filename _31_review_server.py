# -*- coding: utf-8 -*-
"""31.md 人评服务：静态托管 _31_integration_e2e/ + FEEDBACK.json 落盘。

GET  /                      → GALLERY.html 等静态文件
GET  /api/feedback          → 当前 FEEDBACK.json（无则 {}）
POST /api/feedback          → body={"key": "<cell_key>", "state": "ok|best|bad|"}
                              或 {"note_key": "<shot_id>", "note": "..."}
                              合并写入 _31_integration_e2e/FEEDBACK.json（原子写）

用法：python _31_review_server.py [port]   （默认 8777；先 kill 旧实例再启动）
"""
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
E2E = ROOT / "_31_integration_e2e"
FEEDBACK = E2E / "FEEDBACK.json"


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
        super().__init__(*a, directory=str(E2E), **kw)

    def log_message(self, fmt, *args):   # 静默静态日志
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
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 400)
            return
        data = _load()
        if "key" in payload:
            key = str(payload["key"])
            state = str(payload.get("state", ""))
            if state not in ("", "ok", "best", "bad"):
                self._send_json({"ok": False, "error": "bad state"}, 400)
                return
            entry = data.setdefault("selections", {})
            if state:
                entry[key] = {"state": state, "at": payload.get("at", 0)}
            else:
                entry.pop(key, None)
        elif "note_key" in payload:
            notes = data.setdefault("notes", {})
            nk = str(payload["note_key"])
            if str(payload.get("note", "")):
                notes[nk] = str(payload["note"])
            else:
                notes.pop(nk, None)
        else:
            self._send_json({"ok": False, "error": "need key|note_key"}, 400)
            return
        data["_updated_at"] = payload.get("at", 0)
        _save(data)
        self._send_json({"ok": True})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"31 review server on http://127.0.0.1:{port}/GALLERY.html", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
