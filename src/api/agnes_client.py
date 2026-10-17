# src/api/agnes_client.py  （阶段 1 整文件重写）
# 重写要点（AGNES_API_FACTS 为唯一真相源）：
#   1. 共享 httpx 连接池
#   2. 全错误码指数退避：408/429/500/502/503/504/520/522/524
#   3. 图生图 image 双位置 fallback（extra_body -> 顶层），成功位置进程内缓存
#   4. response_format 只放 extra_body
#   5. num_frames 8n+1 夹取 + ≤441 校验
#   6. 视频轮询 5s，回读 seconds/size
#   7. 接入 RateLimiter + QuotaLedger
#   8. size 用档位 + ratio
# 另：阶段5 垫片已全部清理--垫片1/2（generate_image legacy size= + data 字段）随③-e 删
# （角色接口改 size_tier/ratio + result["url"]），垫片3（understand_text）随③-b 删，
# 垫片4/5（generate_video / edge_tts.synthesize）早已删。
from __future__ import annotations
import asyncio, json, logging, os, random, time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from ..utils.logger import setup_logger
from ..core.rate_limiter import RateLimiter
from ..core.quota import QuotaLedger

log = setup_logger("agnes_client")

RETRYABLE = {408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def _parse_retry_after(r) -> Optional[float]:
    """429 响应的 Retry-After 解析（秒；HTTP-date 形态不支持——Agnes 未实证使用，
    不可解析=回落指数退避，不猜）。"""
    try:
        v = r.headers.get("Retry-After") or r.headers.get("retry-after")
        if not v:
            return None
        return float(str(v).strip())
    except Exception:   # noqa: BLE001
        return None

# 唯一视频模型（2026-09-28：v2.0 官方下线+用户指令删除；config 缺失/测试 fake
# 无 models 时的确定兜底——dispatch 与 freshness 重算投影必须同源）。
DEFAULT_VIDEO_MODEL = "agnes-video-2.5-flash"

# AGNES_API_FACTS §2.3
# 2K 扩展（Phase D §2 最小接线，2026-09-19 真机探针 _phase_d_2k_probe）：
# 1:1/16:9/9:16 为既有档；3:4/4:3/21:9 来自 AGNES_API_FACTS 官方 2K 表；
# 2:3/3:2 官方表未列但真机探针 PASS（请求 1664x2496 → 实际 1664x2496，无服务端归一化），
# 3:2 按同族对称补齐——角色三视图/Look/Composition 竖图链路（ratio="2:3"）由此可在 2K 档透传。
SIZE_TIERS = {
    "1K": {"1:1": "1024x1024", "3:4": "864x1152", "4:3": "1152x864",
           "16:9": "1312x736", "9:16": "736x1312", "2:3": "832x1248",
           "3:2": "1248x832", "21:9": "1568x672"},
    "2K": {"1:1": "2048x2048", "16:9": "2624x1472", "9:16": "1472x2624",
           "2:3": "1664x2496", "3:2": "2496x1664", "3:4": "1728x2304",
           "4:3": "2304x1728", "21:9": "3136x1344"},
}

def resolve_size(tier: str, ratio: str) -> str:
    return SIZE_TIERS.get(tier, SIZE_TIERS["1K"]).get(ratio, "1312x736")

# AGNES_API_FACTS §3.4：num_frames ≤ 441 且满足 8n+1。
# 441 是全局绝对上限；按分辨率还有更低的实际上限（视频生成算力随像素数增长）。
# 下方 max_frames_for 的分档值参考 lcy362/agnes-video-generator 实测（来自 Agnes 400 报错），
# ⚠️ 待本项目 Clash 起来后实测回填 AGNES_API_FACTS.md 确认。
ABS_MAX_FRAMES = 441
def max_frames_for(width: int, height: int) -> int:
    """该分辨率下允许的最大 num_frames。分辨率越高，允许帧数越少。
    实测（.cn，2026-07-29）：720p 档（含 1312x736、1280x720）接受 441 帧（=全局上限）。
    1080p / 480p 待实测，暂取参考值（lcy362）169 / 961。"""
    long_side = max(width or 0, height or 0)
    if long_side >= 1920:
        return 169    # 1080p 档（待实测）
    if long_side >= 854:
        return 441    # 720p 档（实测，= 全局上限）
    return 961        # 480p 档（待实测）

def snap_num_frames(target_frames: int,
                    width: int | None = None, height: int | None = None) -> int:
    """夹到最近的 8n+1。若给定 size，再夹到 max_frames_for(size) 与 441 的较小者。
    不给 size 时仅用全局上限 441（向后兼容 snap_num_frames(500)==441 这类断言）。"""
    n = round((target_frames - 1) / 8)
    v = 8 * int(n) + 1
    cap = ABS_MAX_FRAMES
    if width and height:
        cap = min(cap, max_frames_for(width, height))
    return max(81, min(cap, v))


# ---------- OpenAI 兼容 SSE 流式解析（Agnes agnes-2.5-flash 实测格式） ----------
# 纯 async 解析器：输入 async line iterable（httpx r.aiter_lines() 或测试用假流），
# 产出 provider 事件 dict：
#   {"kind":"content_delta","delta":str}           —— delta.content，逐块给 UI
#   {"kind":"reasoning_delta","delta":str}         —— delta.reasoning_content（DeepSeek/Agnes 风格）
#                                                     或 delta.reasoning（SenseNova 实测字段），
#                                                     统一转成 reasoning_delta，业务层无感
#   {"kind":"finish","reason":"stop"|"tool_calls","content":str|None,
#    "tool_calls":list|None,"usage":dict|None}     —— 终结，聚合完整消息（与非流式 chat_completion
#                                                    返回形状一致，供 tool-loop 执行）
# tool_calls 按 index 内部聚合（id/name 只首片，arguments 顺序拼接），【不】暴露原始
# argument delta（§六：聚合完整后再交给 tool-loop）。
async def _parse_sse_events(alines):
    content_buf: List[str] = []
    tool_aggs: Dict[int, Dict[str, str]] = {}
    tool_pending_emitted: set = set()   # 已发过 tool_call_pending 的 index
    finish_reason: Optional[str] = None
    usage: Optional[dict] = None
    async for line in alines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            continue
        try:
            ch = json.loads(payload)
        except Exception:
            # 畸形/半截 JSON 行：跳过，不中断整条流
            continue
        if ch.get("usage"):
            usage = ch["usage"]
        for choice in ch.get("choices") or []:
            fr = choice.get("finish_reason")
            if fr:
                finish_reason = fr
            d = choice.get("delta") or {}
            # delta.content 空串 "" 不当 delta（首块 role 标记常带 content:""）
            if d.get("content"):
                content_buf.append(d["content"])
                yield {"kind": "content_delta", "delta": d["content"]}
            # reasoning 字段容错：Agnes 用 reasoning_content，SenseNova 6.8 实测用 reasoning
            # （2026-08-29 probe results/03_flash_stream_scan.json delta_keys={'reasoning':70}）。
            # 在解析器层统一，不改下游事件契约。
            rc = d.get("reasoning_content") or d.get("reasoning")
            if rc:
                yield {"kind": "reasoning_delta", "delta": rc}
            for frag in d.get("tool_calls") or []:
                idx = frag.get("index", 0)
                a = tool_aggs.setdefault(idx, {"id": "", "name": "", "args": ""})
                if frag.get("id"):
                    a["id"] += frag["id"]
                fn = frag.get("function") or {}
                if fn.get("name"):
                    had_name = bool(a["name"])
                    a["name"] += fn["name"]
                    # 工具名一出现就提前通知（参数还在流式生成中）：大参数 tool_call
                    # 的 arguments 生成可能耗时几十秒，期间前端不应只显示"思考中"。
                    if not had_name and a["name"] and idx not in tool_pending_emitted:
                        tool_pending_emitted.add(idx)
                        yield {"kind": "tool_call_pending", "index": idx, "name": a["name"]}
                if fn.get("arguments"):
                    a["args"] += fn["arguments"]
    final_content = "".join(content_buf) if content_buf else None
    final_tool_calls = None
    if tool_aggs:
        final_tool_calls = [
            {"id": a["id"] or f"call_stream_{i}", "type": "function",
             "function": {"name": a["name"], "arguments": a["args"]}}
            for i, a in sorted(tool_aggs.items())
        ]
    yield {"kind": "finish", "reason": finish_reason or "stop",
           "content": final_content, "tool_calls": final_tool_calls, "usage": usage}


# ---------- 流式看门狗（keepalive 免疫，2026-08-31） ----------
# 真机实测：上游偶发长时间只发 keepalive 字节（注释/空行），httpx read-timeout 被字节
# 到达无限重置 → chat_completion_stream 永久挂死 → 整个 agent turn 挂死。
# read-timeout 只能防「完全无字节」，防不了「有字节但无有效数据行」。
# 修复语义：只有真实 SSE data: 行才重置不活跃计时；注释/空行（keepalive）不重置；
# 超过 inactivity 秒没有 data 行（含 __anext__ 永久挂住的情形）→ 抛 AgnesError，
# 让 turn 干净失败而不是无限等待。360s 与 httpx read=360 对齐：正常「无字节」路径
# 行为不变（两者同时刻触发），只是补上「keepalive 空转」这个洞。
async def _watched_lines(alines, inactivity: float = 360.0):
    it = alines.__aiter__()
    deadline = time.monotonic() + inactivity
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgnesError(
                f"流式响应 {int(inactivity)}s 无有效数据行（仅 keepalive/静默），判定上游挂死")
        try:
            line = await asyncio.wait_for(it.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            return
        if line.startswith("data:"):
            deadline = time.monotonic() + inactivity
        yield line


class AgnesError(Exception):
    def __init__(self, msg, status=None, body=None):
        super().__init__(msg); self.status, self.body = status, body


class PollTransientError(AgnesError):
    """轮询的暂时性失败（网络/超时/5xx 耗尽）。调用方不应标 video.status=failed，
    应保留 video_id 让 retry/recover 续轮询，避免重提交丢掉已生成的视频。
    与「服务端明确返回 failed / video_id 404」的永久失败区分。"""


def _resolve_hotswap_key(configured_key: str) -> str:
    """SenseNova key 热切换：读切换按钮写入的 key 文件，存在且非空则优先于配置 key。
    路径：环境变量 SENSENOVA_KEY_FILE 优先，否则 ~/.sensenova/active_key.txt。
    文件不存在/为空/读失败 -> 用配置 key（.env 的 SENSENOVA_API_KEY），行为不变。
    每次请求现读（文件极小，相对 LLM 延迟可忽略）——切换即生效，无需重启后端。"""
    kf = os.environ.get("SENSENOVA_KEY_FILE") or str(
        Path.home() / ".sensenova" / "active_key.txt")
    try:
        v = Path(kf).read_text(encoding="utf-8").strip()
        if v:
            return v
    except OSError:
        pass
    return configured_key


class AgnesClient:
    def __init__(self, config: dict, quota: QuotaLedger | None = None,
                 transport: Optional[httpx.BaseTransport] = None,
                 text_endpoint: Optional[dict] = None):
        self.cfg      = config
        self.base_url = config["base_url"].rstrip("/")
        self.api_key  = config["api_key"]
        self.models   = config["models"]
        self.proxy    = config.get("proxy") or None
        self.limiter  = RateLimiter.get(config.get("rpm"))
        # 共享进程级配额单例：api_server 启动时会先用 config 初始化它，此处复用
        self.quota    = quota or QuotaLedger.get_default()
        self._client: Optional[httpx.AsyncClient] = None
        self._img_pos: Optional[str] = None      # "extra" | "top"，双位置探测结果缓存
        # 测试注入用（阶段5③-c）：传 httpx.MockTransport 拦截所有请求；默认 None 走真实网络。
        # 生产路径（api_server.startup 构造 AgnesClient）不传 transport，初始化行为不变。
        # 传了 transport 时 httpx 忽略 proxy（MockTransport 不走网络）。
        self.transport = transport
        # SenseNova 接入（2026-08-29）：文本调用可独立于主 endpoint。text_endpoint 非 None 时
        # chat/chat_completion/chat_completion_stream 走该 OpenAI-compatible endpoint
        # （{base_url, api_key, model, provider}，由 controller 按 config.text_provider 注入），
        # 图片/视频仍走主 endpoint。None = 文本与主 endpoint 相同（纯 Agnes，原有行为零变化）。
        self._text_ep = dict(text_endpoint) if text_endpoint else None
        self._text_client: Optional[httpx.AsyncClient] = None

    # ---------- 文本 endpoint 解析 ----------
    def _text_cfg(self):
        """文本请求的 (base_url, api_key, model, provider)。
        text_endpoint 注入时（SenseNova）支持 key 文件热切换（见 _resolve_hotswap_key）。"""
        if not self._text_ep:
            return self.base_url, self.api_key, self.models["text"], "agnes"
        ep = self._text_ep
        return ep["base_url"], _resolve_hotswap_key(ep["api_key"]), \
            ep["model"], ep.get("provider", "sensenova")

    @staticmethod
    def _thinking_body(thinking: bool, provider: str) -> Dict[str, Any]:
        """provider 层 thinking 参数适配（业务层只传 bool，语义与 Agnes 一致）。
        agnes:     True -> chat_template_kwargs.enable_thinking（原样）；False -> 不传（原样）。
        sensenova: 实测默认开启思考（2026-08-29 probe results/20_off_default.json），
                   必须显式开关。thinking.type 合法值 enabled/disabled/adaptive
                   （probe 400 报错原文给出）。True -> enabled；False -> disabled。"""
        if provider == "sensenova":
            return {"thinking": {"type": "enabled" if thinking else "disabled"}}
        if thinking:
            return {"chat_template_kwargs": {"enable_thinking": True}}
        return {}

    # ---------- 连接池 ----------
    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                timeout=httpx.Timeout(connect=30, read=360, write=60, pool=30),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                proxy=self.proxy, follow_redirects=True,
                transport=self.transport)
        return self._client

    async def _text_http(self) -> httpx.AsyncClient:
        """文本 endpoint 专用连接池（base_url 与主 endpoint 不同时用）。
        Authorization 不烘进 client——key 文件热切换要求每次请求现算（见 _request）。"""
        base_url, _, _, _ = self._text_cfg()
        if self._text_client is None or self._text_client.is_closed:
            self._text_client = httpx.AsyncClient(
                base_url=base_url,
                headers={"Content-Type": "application/json"},
                timeout=httpx.Timeout(connect=30, read=360, write=60, pool=30),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                proxy=self.proxy, follow_redirects=True,
                transport=self.transport)
        return self._text_client

    async def aclose(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        if self._text_client and not self._text_client.is_closed:
            await self._text_client.aclose()

    # ---------- 带退避的请求 ----------
    async def _request(self, method: str, url: str, bucket: str,
                       max_attempts: int = 5, *, text_ep: bool = False,
                       **kw) -> httpx.Response:
        label = "SenseNova" if text_ep else "Agnes"
        last = None
        for attempt in range(max_attempts):
            retry_after_s = None
            async with self.limiter.slot(bucket):
                try:
                    c = await (self._text_http() if text_ep else self.client())
                    if text_ep:
                        # key 每次请求现算（热切换），不能烘进连接池 client 的默认头
                        _, key, _, _ = self._text_cfg()
                        kw["headers"] = {**(kw.get("headers") or {}),
                                         "Authorization": f"Bearer {key}"}
                    r = await c.request(method, url, **kw)
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last = e
                    log.warning("[%s] 网络异常 %s（第 %d/%d 次）", bucket, e,
                                attempt + 1, max_attempts)
                else:
                    if r.status_code < 400:
                        return r
                    if r.status_code not in RETRYABLE:
                        raise AgnesError(
                            f"{label} API 错误 {r.status_code}: {r.text[:400]}",
                            r.status_code, r.text)
                    last = AgnesError(f"{r.status_code}: {r.text[:200]}", r.status_code, r.text)
                    if r.status_code == 429:
                        retry_after_s = _parse_retry_after(r)
                    log.warning("[%s] 可重试错误 %d（第 %d/%d 次）", bucket,
                                r.status_code, attempt + 1, max_attempts)
            # 退避在限流槽之外做，避免占着令牌睡觉。
            # Phase D 校准：基值 3→6（429 风暴下 10 并发各自短退避会互相顶住服务端
            # 速率窗口；更长退避让窗口排空，总耐心 6+12+24+48+60≈150s）。
            # Phase56 A4：服务端 Retry-After 优先（真实限流窗口），无头/不可解析
            # 时回落既有指数退避；有界（≤120s），不因并发调大制造重试风暴。
            backoff = min(60, (2 ** attempt) * 6) + random.uniform(0, 2)
            if retry_after_s is not None:
                backoff = min(120, max(1.0, retry_after_s))
                log.info("[%s] 429 Retry-After=%ss（服务端窗口优先）", bucket, backoff)
            await asyncio.sleep(backoff)
        # 保留末次错误的 status/body：上层（如 producer loop 的瞬态恢复）需要区分
        # 「可重试错误耗尽」（429/5xx，值得再等一轮）与「配置/配额类永久失败」。
        raise AgnesError(f"重试 {max_attempts} 次仍失败: {last}",
                         status=getattr(last, "status", None),
                         body=getattr(last, "body", None))

    # ---------- 文本 ----------
    async def chat(self, messages: List[dict], *, temperature=0.7,
                   max_tokens=2048, thinking=False, json_mode=False) -> str:
        if self.quota:
            ok, why = self.quota.can("text_calls", 1)
            if not ok: raise AgnesError(why)
        _, _, model, provider = self._text_cfg()
        log.info("[text] provider=%s model=%s thinking=%s", provider, model, bool(thinking))
        body: Dict[str, Any] = {"model": model, "messages": messages,
                                "temperature": temperature, "max_tokens": max_tokens}
        body.update(self._thinking_body(thinking, provider))
        r = await self._request("POST", "/v1/chat/completions", "text",
                                text_ep=bool(self._text_ep), json=body)
        if self.quota: self.quota.consume("text_calls", 1)
        text = r.json()["choices"][0]["message"]["content"]
        if json_mode:
            text = text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"): text = text[4:]
            return text.strip()
        return text

    async def chat_completion(self, messages: List[dict], *, temperature=0.7,
                              max_tokens=2048, thinking=False,
                              tools: Optional[List[dict]] = None,
                              tool_choice: Optional[Any] = None) -> dict:
        """OpenAI 兼容的 chat completion（支持原生 function-calling）。

        返回 message dict：{"content": str|None, "tool_calls": list|None, "role": "assistant"}。
        tool_calls 元素为 {"id","type":"function","function":{"name","arguments"(str)}}，
        与 OpenAI Chat Completions 格式一致。tools 为标准 OpenAI tools 数组。
        不做 response_format/json_mode 处理（agent 工具调用走结构化 tool_calls，不需要）。"""
        if self.quota:
            ok, why = self.quota.can("text_calls", 1)
            if not ok: raise AgnesError(why)
        _, _, model, provider = self._text_cfg()
        log.info("[text] provider=%s model=%s thinking=%s", provider, model, bool(thinking))
        body: Dict[str, Any] = {"model": model, "messages": messages,
                                "temperature": temperature, "max_tokens": max_tokens}
        body.update(self._thinking_body(thinking, provider))
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        r = await self._request("POST", "/v1/chat/completions", "text",
                                text_ep=bool(self._text_ep), json=body)
        if self.quota: self.quota.consume("text_calls", 1)
        msg = r.json()["choices"][0]["message"]
        # Phase55 D：usage 透传（此前被丢弃）——会话级 token 预算监控的数据基础
        #（P54-B1 实证 467k input 撞 524k 上限；监控需真实 usage 而非字符近似）。
        # provider 不回 usage 时键缺省（诚实缺失，不做估算冒充）。
        _usage = r.json().get("usage") or None
        return {
            "content": msg.get("content"),
            "tool_calls": msg.get("tool_calls"),
            "role": msg.get("role", "assistant"),
            **({"usage": _usage} if _usage else {}),
        }

    async def chat_completion_vision(self, messages: List[dict], *, model: str,
                                     temperature=0.2, max_tokens=1600,
                                     thinking: bool = True) -> dict:
        """Vision 请求（Visual Review Tool V1，2026-09-06 probe 定案）。

        与 chat_completion 的三点差异：
          1) model 显式传入（agnes-2.5-flash），不走 _text_cfg——text_endpoint
             路由到 SenseNova 时文本客户端不能承载视觉请求；
          2) 恒走主 endpoint（text_ep=False）——视觉能力只在 Agnes 主端点；
          3) 返回 usage（prompt/completion/total + prompt_tokens_details.image_tokens），
             供工具遥测（文档 §二十一）；provider 事实：图片输入按 image_tokens 计量。
        messages[].content 允许数组形态（text / image_url）；image_url 支持
        公网 URL 与 base64 data URI（probe C3：data URI 5/5 成功；imgbb 公网
        URL 恒 500"loading IMAGE data"——勿用 imgbb 承载审查图）。
        复用 _request（限流/重试/退避/配额），不新造 retry。
        Phase49：视觉审查调用也开 thinking（真机探针 2026-10-07 实证：视觉端点
        接受 chat_template_kwargs.enable_thinking 且回包含 reasoning_content）；
        thinking=False 不传字段（回归旧行为）。"""
        if self.quota:
            ok, why = self.quota.can("text_calls", 1)
            if not ok: raise AgnesError(why)
        body: Dict[str, Any] = {"model": model, "messages": messages,
                                "temperature": temperature, "max_tokens": max_tokens}
        if thinking:
            body.update(self._thinking_body(True, "agnes"))
        r = await self._request("POST", "/v1/chat/completions", "text",
                                text_ep=False, json=body)
        if self.quota: self.quota.consume("text_calls", 1)
        data = r.json()
        return {
            "content": data["choices"][0]["message"].get("content"),
            "usage": data.get("usage"),
            "model": data.get("model"),
        }

    async def chat_completion_stream(self, messages: List[dict], *, temperature=0.7,
                                      max_tokens=2048, thinking=False,
                                      tools: Optional[List[dict]] = None,
                                      tool_choice: Optional[Any] = None):
        """OpenAI 兼容 streaming chat completion（原生 function-calling + reasoning）。

        异步生成 provider 事件 dict（与 _parse_sse_events 相同）：
          {"kind":"content_delta","delta":str}
          {"kind":"reasoning_delta","delta":str}
          {"kind":"finish","reason":"stop"|"tool_calls","content":str|None,
           "tool_calls":list|None,"usage":dict|None}
        tool_calls 内部按 index 聚合（id/name 只首片，arguments 顺序拼接），finish 时给
        完整 list——与非流式 chat_completion 返回形状一致，供 tool-loop 执行；不暴露原始
        argument delta（§六）。不影响非流式 chat_completion。"""
        if self.quota:
            ok, why = self.quota.can("text_calls", 1)
            if not ok: raise AgnesError(why)
        base_url, key, model, provider = self._text_cfg()
        label = "SenseNova" if self._text_ep else "Agnes"
        log.info("[text] provider=%s model=%s thinking=%s", provider, model, bool(thinking))
        body: Dict[str, Any] = {"model": model, "messages": messages,
                                "temperature": temperature, "max_tokens": max_tokens,
                                "stream": True}
        body.update(self._thinking_body(thinking, provider))
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        headers = {"Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        # 🔴 read=360 对齐非流式 _request（360）：流式 turn 的大生成（10 段 x300 字
        # update_many 等大参数 tool_call）服务端可能长时间不发首块/响应头
        # （实测 httpx.ReadTimeout str 为空 -> "[执行失败: ]" 黑盒，2026-08-28）。
        # 120s 对"等响应头"不够。流式 chunk 间隔实际很短，360s 是安全上限。
        # SenseNova 实测（2026-08-29 probe round2/3）：tool calling 偶发 500
        # "engine is not available temporarily"（挂 60~190s 后返回，非 stream 特有）。
        # 500 在 RETRYABLE 集合内、非流式 _request 本就自动重试；流式在【尚未产出任何
        # 事件】时对可重试状态做有限重试（3 次尝试），不改变成功路径行为。
        # 收口 §1B（2026-09-06）：重试扩展到纯 Agnes 路径——此前 stream_attempts 仅在
        # SenseNova text_endpoint 时为 3，Agnes 主路径流式 429/500/520 直接抛（真机：
        # producer turn 以 [执行失败] 收尾）。重试只发生在状态码检查点（尚未产出任何
        # 事件），无重复推送风险；auth/参数类（非 RETRYABLE）仍然立即失败。
        # 42.md §二缺陷③（Phase 41 实证：单次瞬时 500 终结 88 分钟 turn）加固两点：
        # a) stream_attempts 3→5 对齐非流式 _request 的耐心；退避对齐其量级
        #    （6/12/24/48，cap 45s）——上游 5xx 风暴窗口实测可持续 60-190s。
        # b) 中途网络异常（ReadTimeout/TransportError）在【零事件已产出】时同样
        #    重试（此前直接抛出终结 turn）；已产出事件后不重试——重发会造成
        #    重复 delta/tool_call，交由 turn 层（driver 双形态重试/用户重发）处理。
        stream_attempts = 5
        for attempt in range(stream_attempts):
            emitted = False
            try:
                async with httpx.AsyncClient(
                        base_url=base_url, headers=headers,
                        timeout=httpx.Timeout(connect=30, read=360, write=30, pool=30),
                        proxy=self.proxy, follow_redirects=True,
                        transport=self.transport) as c:
                    async with c.stream("POST", "/v1/chat/completions", json=body) as r:
                        if r.status_code != 200:
                            body_txt = (await r.aread()).decode("utf-8", "replace")[:400]
                            if (attempt + 1 < stream_attempts
                                    and r.status_code in RETRYABLE):
                                log.warning("[%s] stream 可重试错误 %d（第 %d/%d 次）",
                                            "text", r.status_code, attempt + 1,
                                            stream_attempts)
                                backoff = min(45, (2 ** attempt) * 6) + random.uniform(0, 2)
                                await asyncio.sleep(backoff)
                                continue
                            raise AgnesError(f"{label} stream 错误 {r.status_code}: {body_txt}",
                                             r.status_code, body_txt)
                        async for ev in _parse_sse_events(_watched_lines(r.aiter_lines())):
                            emitted = True
                            yield ev
                        break   # 流正常走完（或已在产出事件后结束），不再重试
            except (httpx.TimeoutException, httpx.TransportError) as e:
                # 零事件 → 安全重试（与状态码检查点同一纪律）；已产出事件 →
                # 消费方已收到部分流，客户端层重发必重复，原样抛给 turn 层。
                if emitted or attempt + 1 >= stream_attempts:
                    raise
                log.warning("[text] stream 中途网络异常（零事件，重试 %d/%d）: %s",
                            attempt + 1, stream_attempts, str(e)[:120])
                backoff = min(45, (2 ** attempt) * 6) + random.uniform(0, 2)
                await asyncio.sleep(backoff)
                continue
        if self.quota: self.quota.consume("text_calls", 1)

    # ---------- 图片 ----------
    async def generate_image(self, prompt: str, *,
                             image_refs: Optional[List[str]] = None,
                             size_tier: str = "1K", ratio: str = "16:9",
                             seed: Optional[int] = None,
                             want_b64: bool = False) -> dict:
        """返回 {"url": str|None, "b64": str|None}。
        image_refs 支持公网 URL 或 data:image/png;base64,..."""
        if self.quota:
            ok, why = self.quota.can("images", 1)
            if not ok: raise AgnesError(why)

        bucket = f"image_{size_tier.lower()}"
        size = resolve_size(size_tier, ratio)
        is_i2i = bool(image_refs)

        def build(pos: str) -> dict:
            b: Dict[str, Any] = {"model": self.models["image"],
                                 "prompt": prompt, "size": size}
            eb: Dict[str, Any] = {}
            # 🔴 response_format 只能在 extra_body（AGNES_API_FACTS §2.2 坑1）
            if is_i2i:
                eb["response_format"] = "b64_json" if want_b64 else "url"
                if pos == "extra": eb["image"] = image_refs
                else:              b["image"]  = image_refs
            else:
                if want_b64: b["return_base64"] = True
                else:        eb["response_format"] = "url"
            if seed is not None: b["seed"] = seed
            if eb: b["extra_body"] = eb
            return b

        positions = ["extra", "top"] if is_i2i else ["extra"]
        if self._img_pos in positions:
            positions = [self._img_pos] + [p for p in positions if p != self._img_pos]

        last_err = None
        for pos in positions:
            try:
                r = await self._request("POST", "/v1/images/generations",
                                        bucket, json=build(pos))
            except AgnesError as e:
                # 只有 400 才试另一个位置——其它错误换位置也没用
                if e.status == 400 and pos != positions[-1]:
                    log.warning("图生图 image 放在 %s 返回 400，尝试另一位置", pos)
                    last_err = e; continue
                raise
            if is_i2i:
                self._img_pos = pos
                log.info("图生图 image 位置确定为: %s", pos)
            if self.quota: self.quota.consume("images", 1)
            d = r.json()["data"][0]
            return {"url": d.get("url"), "b64": d.get("b64_json")}
        raise last_err or AgnesError("图片生成失败")

    # ---------- 视频 ----------
    # 视频模型唯一契约（2026-09-28：v2.0 官方 2026-09-25 23:59 UTC+8 下线 +
    # 用户指令从系统删除；agnes-video-2.5-flash 为唯一视频模型）：
    #   2.5 契约（2026-09-06 实测 _video25_probe/ + 2026-09-28 官方文档核读）：
    #     mode 必填（text/keyframe/reference）+ seconds 字符串（"4"-"12"，默认 "5"）
    #     + 可选 size（720P/1080P/1K/2K，默认 720P）/ aspect_ratio（默认 16:9）/
    #     seed（integer，可复现）。
    #     【不接受】duration/resolution/num_frames/width/height/fps/quality（400）。
    #     mode=keyframe：首帧图（顶层 `image` 公网 URL，2026-09-06 实测出片；
    #     官方文档记 `first_frame` 字段——顶层 image 为已验证兼容形态）。
    #     mode=reference：`images` 公网 URL 数组（flash 上限 5 张，单图<15MB；参考
    #     不锁首帧 may recompose）；prompt 可用 <Picture N> 指名用途。
    #     轮询 endpoint/参数不变（/agnesapi?video_id=&model_name=），URL 仍在 url 字段。
    # model 参数 None = config 默认（agnes.models.video = agnes-video-2.5-flash）。
    def _video_model_id(self, model: Optional[str]) -> str:
        return model or self.models["video"]

    @staticmethod
    def _is_video_25(model_id: str) -> bool:
        return (model_id or "").startswith("agnes-video-2.5")

    @staticmethod
    def _video25_mode(legacy_mode: str) -> str:
        """legacy 模式 → 2.5 契约 mode。已实测：t2v→text、i2v→keyframe（顶层 image
        公网 URL，2026-09-06 各生成成功）。multi→reference（2026-09-28 官方文档核读
        + 真机探针回执：images 数组，参考不锁首帧）。keyframes→keyframe 双帧：
        官方 first_frame/last_frame 字段（2026-09-29 真机探针 _probe_video25_keyframes.py
        一次命中：双帧 200 受理→出片，首尾帧均被钉住——桥路径解锁，42.md §四 P2）。"""
        if legacy_mode == "t2v":
            return "text"
        if legacy_mode == "i2v":
            return "keyframe"
        if legacy_mode == "multi":
            return "reference"
        if legacy_mode == "keyframes":
            return "keyframe"
        raise AgnesError(f"2.5 契约不支持 {legacy_mode} 模式")

    @staticmethod
    def _video25_image(legacy_mode: str,
                       image_url: Optional[str],
                       image_urls: Optional[List[str]]) -> Dict[str, Any]:
        if legacy_mode in ("t2v",):
            return {}
        if legacy_mode == "i2v":
            if not image_url:
                raise AgnesError("2.5 契约 keyframe 模式必须提供公网 image URL")
            return {"image": image_url}          # 顶层字符串（2026-09-06 实测）
        if legacy_mode == "keyframes":
            # 42.md §四 P2 探针实证（2026-09-29）：官方 first_frame/last_frame
            # 双帧字段受理且两端均被钉住（首帧逐像素级对齐、尾帧构图一致）。
            urls = [u for u in (image_urls or []) if u]
            if len(urls) != 2:
                raise AgnesError(
                    "2.5 契约 keyframes 双帧需要恰好 first+last 两个公网 URL")
            for u in urls:
                if str(u).startswith("data:"):
                    raise AgnesError("视频接口图片必须公网 URL")
            return {"first_frame": urls[0], "last_frame": urls[1]}
        if legacy_mode == "multi":
            urls = [u for u in (image_urls or []) if u]
            if not (1 <= len(urls) <= 5):
                raise AgnesError(
                    f"2.5-flash reference 模式需要 1-5 张公网参考图（flash 上限 5），"
                    f"得到 {len(urls)}")
            return {"images": urls}              # 官方 reference 字段（2026-09-28 文档核读）
        raise AgnesError(f"2.5 契约暂不支持 {legacy_mode} 模式的图片参数")

    async def create_video(self, prompt: str, *,
                           mode: str = "i2v",
                           image_url: Optional[str] = None,
                           image_urls: Optional[List[str]] = None,
                           num_frames: int = 121, frame_rate: int = 24,
                           width: int = 1312, height: int = 736,
                           seed: Optional[int] = None,
                           negative_prompt: str = "",
                           model: Optional[str] = None,
                           duration_s: Optional[int] = None) -> dict:
        """提交视频任务。mode: 't2v' | 'i2v' | 'keyframes' | 'multi'
        2026-09-28 起唯一支持 agnes-video-2.5 系列（v2.0 官方 2026-09-25 23:59 下线
        + 用户指令从系统删除——非 2.5 模型 id 显式拒绝）。契约映射：
        t2v→text / i2v→keyframe（顶层 image）/ multi→reference（images 数组 1-5，
        flash 上限）/ keyframes→keyframe 双帧（官方 first_frame/last_frame 字段，
        2026-09-29 P2 探针实证两端钉住——桥路径解锁）。
        duration_s: 时长（秒，4-12，str 传输，默认 5）。num_frames/frame_rate/width/
        height 仅签名兼容，不再进入请求体；seed 按官方文档发送（integer）；
        negative_prompt 非空显式拒绝（2.5 契约无此字段，绝不静默丢参）。
        返回创建响应（含 video_id / task_id / seconds / size）。"""
        model_id = self._video_model_id(model)
        if not self._is_video_25(model_id):
            raise AgnesError(
                f"视频模型 {model_id} 已删除：仅支持 agnes-video-2.5 系列"
                "（v2.0 已于 2026-09-25 官方下线，2026-09-28 按用户指令从系统移除）")
        if negative_prompt:
            raise AgnesError(
                "2.5 契约无 negative_prompt 字段（官方参数表 2026-09-28 核读）——"
                "绝不静默丢参：请把负面约束并入 prompt 文本后重试")
        seconds_25 = max(4, min(12, int(duration_s or 5)))
        # Phase49 T17：模式互斥出站前拦截。2.5 契约 text/keyframe/reference
        # 三模式互斥；与模式不符的图片参数一律显式拒绝，绝不静默忽略——
        # 静默吞参数会让调用方误以为参考图已生效（B 批 T17 情景）。
        _extra_imgs: List[str] = [u for u in (image_urls or []) if u]
        if mode == "t2v" and (image_url or _extra_imgs):
            raise AgnesError("t2v→text 模式不接受任何图片参数"
                             f"（收到 image_url={bool(image_url)}，"
                             f"image_urls={len(_extra_imgs)} 张）——keyframe/"
                             "reference 输入请改用对应 mode")
        if mode == "i2v" and _extra_imgs:
            raise AgnesError("i2v→keyframe 单帧模式不接受 image_urls"
                             f"（收到 {len(_extra_imgs)} 张）——多图参考请用"
                             " mode=multi；两端钉住请用 mode=keyframes")
        if mode in ("keyframes", "multi") and image_url:
            raise AgnesError(f"{mode} 模式不接受单帧 image_url（顶层 image 是"
                             " i2v 专用）——keyframes 用 image_urls 两帧；"
                             "multi 用 image_urls 1-5 张")
        body: Dict[str, Any] = {"model": model_id, "prompt": prompt,
                                "mode": self._video25_mode(mode),
                                "seconds": str(seconds_25)}
        img = self._video25_image(mode, image_url, image_urls)
        if img:
            body.update(img)
        if seed is not None:
            body["seed"] = int(seed)     # 官方参数（2026-09-28 文档核读）：seed integer
        if self.quota:
            ok, why = self.quota.can("video_seconds", seconds_25)
            if not ok: raise AgnesError(why)
        r = await self._request("POST", "/v1/videos", "video", json=body)
        if self.quota: self.quota.consume("video_seconds", seconds_25)
        data = r.json()
        log.info("视频任务已提交（2.5 契约）model=%s mode=%s seconds=%s id=%s",
                 model_id, body.get("mode"), seconds_25,
                 data.get("video_id") or data.get("task_id"))
        return data

    async def poll_video(self, video_id: str, *,
                         interval: float = 5.0, max_wait: float = 3600,
                         on_progress=None, max_consecutive_failures: int = 5,
                         model_name: Optional[str] = None,
                         slow_interval: float = 20.0, slow_after_s: float = 60.0) -> dict:
        """轮询直到 completed / failed。官方建议间隔 5s（AGNES_API_FACTS §3.5）。
        model_name：提交 2.5 系模型时必须传同一模型名（None = config 默认，legacy 行为）。
        注意：轮询走 text 桶，不占视频桶——否则第二个任务永远提交不了。

        Phase51 A 自适应退避（P50 实证：视频真实完成分布 150-753s/条，固定 5s
        轮询在 2 任务并发时达 24 req/min，超过 text 桶有效预算（20×0.85≈17），
        与文本调用竞争产生 429）。前 slow_after_s 秒保持 interval（捕捉快速完成），
        之后放慢到 slow_interval：完成检测延迟 ≤20s（相对分钟级生成时长可忽略），
        轮询量降 4-6 倍，给正常文本调用让出额度。429 仍由 _request 重试吸收。"""
        waited = 0.0
        consecutive = 0
        while waited < max_wait:
            step = interval if waited < slow_after_s else max(slow_interval, interval)
            await asyncio.sleep(step); waited += step
            try:
                r = await self._request("GET", "/agnesapi", "text",
                                        params={"video_id": video_id,
                                                "model_name": self._video_model_id(model_name)},
                                        max_attempts=3)
            except AgnesError as e:
                if e.status is None:
                    # HTTP 层错误（网络/5xx/429 耗尽重试）-> 计数重试，不立刻放弃
                    consecutive += 1
                    log.warning("poll_video %s 网络错误（连续 %d/%d）: %s",
                                video_id[:16], consecutive, max_consecutive_failures, e)
                    if consecutive >= max_consecutive_failures:
                        raise PollTransientError(
                            f"轮询连续 {max_consecutive_failures} 次网络失败: {e}")
                    continue
                # 有 status（4xx 等，_request 已判定不可重试）-> 永久失败，立刻抛
                if e.status == 404:
                    raise AgnesError(f"video_id 不存在: {video_id}", 404)
                raise
            consecutive = 0   # 成功，重置
            d = r.json()
            st = d.get("status")
            if on_progress:
                await _maybe_await(on_progress(d.get("progress", 0), st, waited))
            if st == "completed":
                # 🔴 URL 取值 fallback：remixed_from_video_id（remix 任务）→ url → video_url。
                # 实测 .cn 节点直生成（t2v/i2v）任务 remixed_from_video_id 为 None，URL 在 url 字段。
                # （FACTS §3.6 原写"remixed_from_video_id 就是 mp4 URL"仅对 remix 任务成立，已勘误。）
                url = (d.get("remixed_from_video_id")
                       or d.get("url")
                       or d.get("video_url"))
                if not url: raise AgnesError(f"任务完成但没有视频 URL: {d}")
                return {"url": url,
                        "seconds": float(d.get("seconds") or 0),   # 🔴 回读
                        "size": d.get("size"), "raw": d}
            if st == "failed":
                raise AgnesError(f"视频生成失败: {d.get('error')}")
        raise PollTransientError(f"视频生成超时（{max_wait}s），video_id={video_id}")

    async def download(self, url: str, dest) -> str:
        from pathlib import Path
        dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
        # 资产 URL（platform-outputs.agnes-ai.space）是预签名 URL，
        # 带 Authorization 头反而 401（实测），用无认证 client 下载。
        async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=30, read=600, write=60, pool=30),
                follow_redirects=True, proxy=self.proxy,
                transport=self.transport) as cli:
            async with cli.stream("GET", url) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 16):
                        f.write(chunk)
        return str(dest)

    # 阶段5 垫片已全部清理（③-e 清垫片1/2、③-b 清垫片3、更早清垫片4/5）。
    # generate_image 现只接受 size_tier/ratio，返回 {url, b64}（无 data 字段）。


async def _maybe_await(x):
    if asyncio.iscoroutine(x): return await x
    return x
