# -*- coding: utf-8 -*-
"""Phase 45.4 — _45/review 专用评审 HTTP 服务。

- /            → REVIEW_45.html
- /media/<仓库根相对路径> → 媒体文件（带 HTTP Range 206，进度条可拖动——44C+ 实测教训）
- 其余路径按 _45/review 下静态文件；404 → 兜底回 REVIEW_45.html（44C+ 先例）。
用法：python _4545_review_server.py [port]   （默认 8787）
"""
import os
import re
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

BASE = Path(__file__).resolve().parent
REVIEW = BASE / "_45" / "review"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8787


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(REVIEW), **kw)

    # ---- 静音访问日志（保留错误）----
    def log_message(self, fmt, *args):
        if " 404 " not in fmt % args and " 206 " not in fmt % args:
            return

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.path = "/REVIEW_45.html"
        m = re.match(r"^/media/(.+)$", self.path.split("?")[0])
        if m:
            from urllib.parse import unquote
            rel = unquote(m.group(1)).replace("/", os.sep)
            target = (BASE / rel).resolve()
            if not str(target).startswith(str(BASE.resolve())) or not target.is_file():
                self.send_error(404)
                return
            return self._send_with_range(target)
        if not (REVIEW / self.path.lstrip("/")).is_file():
            self.send_response(302)
            self.send_header("Location", "/REVIEW_45.html")
            self.end_headers()
            return
        return super().do_GET()

    def _send_with_range(self, p: Path):
        size = p.stat().st_size
        rng = self.headers.get("Range")
        ctype = ("video/mp4" if p.suffix == ".mp4"
                 else "audio/wav" if p.suffix == ".wav"
                 else "application/octet-stream")
        start, end = 0, size - 1
        code = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                elif m.group(1):
                    end = size - 1
                code = 206
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with p.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionAbortedError):
                    return
                remaining -= len(chunk)


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"review server: http://127.0.0.1:{PORT}/REVIEW_45.html")
    srv.serve_forever()
