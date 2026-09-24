# _37_fable/_37_review_server.py — 37A 战役人评服务：静态托管 _37_fable/ + FEEDBACK.json。
# GET  /CHECKPOINT.html   检查点页（每镜视频 + 末帧 + 预期尾帧 + Vision 评估）
# POST /api/feedback      {"fb": {"N1": {"level": "none|minor|major", "action": "...", "note": "..."}}}
# 用法: python _37_review_server.py [port]  （默认 8781；脱离式启动由外部负责）
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FEEDBACK = ROOT / "FEEDBACK.json"


def _save(data: dict):
    tmp = FEEDBACK.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, FEEDBACK)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def do_POST(self):
        if self.path != "/api/feedback":
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        fb = body.get("fb") or {}
        cur = {}
        if FEEDBACK.exists():
            try:
                cur = json.loads(FEEDBACK.read_text(encoding="utf-8"))
            except Exception:
                cur = {}
        cur.update(fb)
        _save(cur)
        data = json.dumps({"ok": True, "saved": len(fb)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8781
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
