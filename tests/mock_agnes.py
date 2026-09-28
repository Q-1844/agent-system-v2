"""Mock Agnes 后端：httpx.MockTransport 拦截 AgnesClient 所有请求（阶段5③-c，PHASE_5 §5.4）。

让 AgnesClient 单元测试不真调 Agnes（1 RPM 会让测试跑一小时）。拦截点：
  POST /v1/images/generations  -> 图片生成（含 i2i 双位置 fallback：extra 返 400 -> top 返 200）
  POST /v1/videos              -> 视频提交（回 video_id）
  GET  /agnesapi                -> 视频轮询（前 N 次 in_progress，之后 completed）
  GET  <任意 url>               -> 资产下载（占位字节）

记录所有请求供断言（response_format 放置位置、num_frames 8n+1、image 双位置）。
"""
import json

import httpx


# AgnesClient 构造时会绑 RateLimiter / QuotaLedger 进程单例；测试里替成 no-op stub，
# 避免真限流睡觉（image 桶容量 1）或真配额消耗污染 session 级单例（影响其它测试）。
class _NoLimiter:
    def slot(self, bucket):
        class _Slot:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
        return _Slot()


class _NoQuota:
    def can(self, *a, **kw):
        return True, ""

    def consume(self, *a, **kw):
        pass


# 最小 config（AgnesClient.__init__ 读 base_url/api_key/models/proxy/rpm）
TEST_CONFIG = {
    "base_url": "https://mock.agnes",
    "api_key": "test-key",
    "models": {"text": "mock-text", "image": "mock-image",
               "video": "agnes-video-2.5-flash"},   # 2026-09-28：唯一视频模型（v2.0 已删除）
    "proxy": None,
    "rpm": None,
}


class MockAgnes:
    """拦截 AgnesClient 请求。requests 按到达顺序记录所有 httpx.Request。"""

    def __init__(self, *,
                 image_url="https://mock.agnes/img/out.png",
                 video_url="https://mock.agnes/video/out.mp4",
                 video_id="task_mock123",
                 poll_in_progress=2,
                 i2i_extra_400=False,
                 stream_429_times=0,
                 stream_fail_mode=None,
                 stream_fail_times=0):
        self.image_url = image_url
        self.video_url = video_url
        self.video_id = video_id
        self.poll_in_progress = poll_in_progress
        # True = i2i image 放 extra_body 时返 400，触发 AgnesClient fallback 到顶层
        self.i2i_extra_400 = i2i_extra_400
        # 前 N 次 /v1/chat/completions 返 429（流式重试路径测试）
        self.stream_429_times = stream_429_times
        # 42.md 缺陷③：流式中途断流——"pre"=未产出任何事件即断（零事件，可安全
        # 重试）；"mid"=产出 1 个事件后断（已推送，客户端层不得重试）。前
        # stream_fail_times 次 chat 请求断流。
        self.stream_fail_mode = stream_fail_mode
        self.stream_fail_times = stream_fail_times
        self.requests: list[httpx.Request] = []
        self._poll_count = 0
        self._chat_count = 0
        self.transport = httpx.MockTransport(self._handler)

    # ---------- 路由 ----------
    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method, path = request.method, request.url.path
        if method == "POST" and path == "/v1/images/generations":
            return self._images(request)
        if method == "POST" and path == "/v1/videos":
            return self._videos(request)
        if method == "GET" and path == "/agnesapi":
            return self._poll(request)
        if method == "POST" and path == "/v1/chat/completions":
            return self._chat(request)
        if method == "GET":
            # 资产下载（image_url / video_url，任意 host）
            return httpx.Response(200, content=b"mock-bytes")
        return httpx.Response(404, text=f"unmocked {method} {path}")

    def _chat(self, request: httpx.Request) -> httpx.Response:
        self._chat_count += 1
        if self._chat_count <= self.stream_429_times:
            return httpx.Response(429, text='{"error":{"message":"rate limited"}}')
        if self.stream_fail_mode and self._chat_count <= self.stream_fail_times:
            async def _broken_stream():
                if self.stream_fail_mode == "mid":
                    part = ('data: {"choices":[{"delta":{"role":"assistant",'
                            '"content":"部分"}}]}\n\n').encode("utf-8")
                    yield part
                raise httpx.ReadTimeout("mid-stream cut")
            return httpx.Response(200, content=_broken_stream())
        sse = ('data: {"choices":[{"delta":{"role":"assistant","content":"你好"}}]}\n\n'
               'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
               "data: [DONE]\n\n")
        return httpx.Response(200, text=sse)

    def _images(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        eb = body.get("extra_body") or {}
        is_i2i_extra = "image" in eb            # i2i 且 image 放 extra_body（pos="extra"）
        if is_i2i_extra and self.i2i_extra_400:
            return httpx.Response(
                400, text='{"error":{"message":"image in extra_body not accepted"}}')
        return httpx.Response(
            200, json={"data": [{"url": self.image_url, "b64_json": None}]})

    def _videos(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "video_id": self.video_id, "task_id": self.video_id,
            "seconds": 3.4, "size": "1280x704",
        })

    def _poll(self, request: httpx.Request) -> httpx.Response:
        self._poll_count += 1
        if self._poll_count <= self.poll_in_progress:
            return httpx.Response(200, json={"status": "in_progress", "progress": 50})
        # 🔴 .cn 直生成场景：remixed_from_video_id 为 null，URL 在 url 字段
        # （FACTS §3.6 勘误：原写 remixed_from_video_id 是 mp4 URL 仅对 remix 任务成立）
        return httpx.Response(200, json={
            "status": "completed", "progress": 100,
            "remixed_from_video_id": None,
            "url": self.video_url, "seconds": 3.4, "size": "1280x704",
        })

    # ---------- 断言 helper ----------
    def find(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def bodies(self, method: str, path: str) -> list[dict]:
        return [json.loads(r.content) for r in self.find(method, path) if r.content]


def make_agnes(mock: "MockAgnes"):
    """构造绑 MockTransport 的 AgnesClient（limiter/quota 替成 no-op，不碰真单例）。"""
    from src.api.agnes_client import AgnesClient
    c = AgnesClient(TEST_CONFIG, transport=mock.transport)
    c.limiter = _NoLimiter()
    c.quota = _NoQuota()
    return c
