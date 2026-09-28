"""AgnesClient 单元测试（阶段5③-c）：MockTransport 拦截，钉死真实踩过的坑。

坑1（poll URL fallback）：completed 时 remixed_from_video_id 为 null，URL 从 url 字段取
    —— .cn 直生成场景（FACTS §3.6 勘误，原写 remixed_from_video_id 是 mp4 URL 仅对 remix 成立）
坑2（response_format 位置）：t2i/i2i 都把 response_format 放 extra_body，绝不放顶层（FACTS §2.2 坑1）
坑3（num_frames 8n+1）：create_video 把 num_frames 经 snap_num_frames 夹成 8n+1 再进请求体
§5.4 i2i 双位置 fallback：image 放 extra_body 返 400 -> 自动改放顶层，_img_pos 缓存
§5.4 poll 进度：前 N 次 in_progress -> completed

用 asyncio.run 驱动（不引 pytest-asyncio 依赖）；依赖 client fixture 仅用于确保
RateLimiter/QuotaLedger 进程单例已由 startup 初始化（make_agnes 会把 limiter/quota 替成 no-op）。
"""
import asyncio

from tests.mock_agnes import MockAgnes, make_agnes


# ---------- 坑2：response_format 只在 extra_body ----------
def test_response_format_in_extra_body(client):
    """t2i 与 i2i 都把 response_format 放 extra_body，不在顶层（AGNES_API_FACTS §2.2 坑1）"""
    # t2i
    mock = MockAgnes()
    agnes = make_agnes(mock)
    asyncio.run(agnes.generate_image(prompt="a cat", size_tier="1K", ratio="16:9"))
    body = mock.bodies("POST", "/v1/images/generations")[-1]
    assert "response_format" not in body                       # 不在顶层
    assert body["extra_body"]["response_format"] == "url"      # 在 extra_body

    # i2i
    mock2 = MockAgnes()
    a2 = make_agnes(mock2)
    asyncio.run(a2.generate_image(prompt="a cat", image_refs=["https://x/a.png"]))
    body2 = mock2.bodies("POST", "/v1/images/generations")[-1]
    assert "response_format" not in body2
    assert body2["extra_body"]["response_format"] == "url"


# ---------- §5.4：i2i 双位置 fallback ----------
def test_i2i_image_position_fallback(client):
    """i2i image 放 extra_body 返 400 -> 自动改放顶层，成功且 _img_pos 缓存为 top"""
    mock = MockAgnes(i2i_extra_400=True)   # extra_body.image 返 400
    agnes = make_agnes(mock)
    res = asyncio.run(agnes.generate_image(prompt="x", image_refs=["https://x/a.png"]))
    assert res["url"] == mock.image_url
    bodies = mock.bodies("POST", "/v1/images/generations")
    assert len(bodies) == 2                                # 两次请求
    assert "image" in bodies[0].get("extra_body", {})      # 第 1 次 extra
    assert "image" in bodies[1]                            # 第 2 次顶层
    assert "image" not in bodies[1].get("extra_body", {})
    assert agnes._img_pos == "top"                         # 位置缓存


# ---------- 坑3（历史）：num_frames 8n+1 只属 v2.0 legacy——v2.0 删除后 2.5 契约不带帧参数 ----------
def test_create_video_default_25_contract(client):
    """不传 model：config 默认（TEST_CONFIG=agnes-video-2.5-flash）→ 2.5 契约 body
    （mode/seconds 字符串）；num_frames/width/height 不再进入请求体
    （snap_num_frames 纯函数覆盖见 test_core）。"""
    mock = MockAgnes()
    agnes = make_agnes(mock)
    asyncio.run(agnes.create_video("prompt", mode="t2v", num_frames=120,
                                   frame_rate=24, width=1280, height=704))
    body = mock.bodies("POST", "/v1/videos")[-1]
    assert body["model"] == "agnes-video-2.5-flash"
    assert body["mode"] == "text" and body["seconds"] == "5"
    for forbidden in ("num_frames", "width", "height", "frame_rate"):
        assert forbidden not in body, forbidden


# ---------- 坑1：remixed_from_video_id null -> url fallback ----------
def test_poll_url_fallback_remixed_null(client):
    """completed 时 remixed_from_video_id=null，URL 从 url 字段取（.cn 直生成场景）"""
    mock = MockAgnes(poll_in_progress=0)   # 第 1 次就 completed
    agnes = make_agnes(mock)
    res = asyncio.run(agnes.poll_video("task_mock123", interval=0, max_wait=5))
    assert res["url"] == mock.video_url                    # 来自 url 字段（remixed 为 null）
    assert res["seconds"] == 3.4
    assert res["size"] == "1280x704"


# ---------- §5.4：poll in_progress -> completed ----------
def test_poll_progression(client):
    """前 2 次 in_progress，第 3 次 completed"""
    mock = MockAgnes(poll_in_progress=2)
    agnes = make_agnes(mock)
    res = asyncio.run(agnes.poll_video("task_mock123", interval=0, max_wait=5))
    assert res["url"] == mock.video_url
    assert len(mock.find("GET", "/agnesapi")) == 3         # 3 次轮询


# ---------- 2026-09-06 收口 §1B：流式 429/5xx 重试（扩展到 Agnes 主路径） ----------
def test_stream_retry_on_429_agnes(client, monkeypatch):
    """纯 Agnes 流式路径 429 → 退避重试一次 → 200 正常出事件（此前 stream_attempts
    仅 SenseNova 为 3，Agnes 直接抛）。"""
    import src.api.agnes_client as ac

    async def _no_sleep(s):
        pass
    monkeypatch.setattr(ac.asyncio, "sleep", _no_sleep)
    mock = MockAgnes(stream_429_times=1)
    agnes = make_agnes(mock)

    async def run():
        evs = []
        async for ev in agnes.chat_completion_stream([{"role": "user", "content": "hi"}]):
            evs.append(ev)
        return evs

    evs = asyncio.run(run())
    assert any(e["kind"] == "finish" for e in evs)
    assert len(mock.find("POST", "/v1/chat/completions")) == 2   # 1 次 429 + 1 次成功


def test_stream_429_exhaustion_raises(client, monkeypatch):
    """持续 429 → 5 次尝试后抛 AgnesError（保留 status=429）。
    42.md 缺陷③：stream_attempts 3→5 对齐非流式 _request 耐心。"""
    import pytest
    from src.api.agnes_client import AgnesError
    import src.api.agnes_client as ac

    async def _no_sleep(s):
        pass
    monkeypatch.setattr(ac.asyncio, "sleep", _no_sleep)
    mock = MockAgnes(stream_429_times=99)
    agnes = make_agnes(mock)

    async def run():
        async for _ in agnes.chat_completion_stream([{"role": "user", "content": "hi"}]):
            pass

    with pytest.raises(AgnesError) as ei:
        asyncio.run(run())
    assert ei.value.status == 429
    assert len(mock.find("POST", "/v1/chat/completions")) == 5


def test_stream_pre_event_network_error_retried(client, monkeypatch):
    """42.md 缺陷③：零事件中途断流（ReadTimeout）→ 客户端层安全重试 → 成功出事件。
    （此前中途网络异常直接抛出终结 turn——Phase 41 单次 500 终结 88 分钟 turn 的
    客户端层缺口。）"""
    import src.api.agnes_client as ac

    async def _no_sleep(s):
        pass
    monkeypatch.setattr(ac.asyncio, "sleep", _no_sleep)
    mock = MockAgnes(stream_fail_mode="pre", stream_fail_times=1)
    agnes = make_agnes(mock)

    async def run():
        evs = []
        async for ev in agnes.chat_completion_stream([{"role": "user", "content": "hi"}]):
            evs.append(ev)
        return evs

    evs = asyncio.run(run())
    assert any(e["kind"] == "finish" for e in evs)
    assert len(mock.find("POST", "/v1/chat/completions")) == 2   # 1 次断流 + 1 次成功


def test_stream_post_event_network_error_not_retried(client, monkeypatch):
    """42.md 缺陷③：已产出事件后断流 → 客户端层【不】重试（重发会重复
    delta/tool_call），原样抛给 turn 层处理。"""
    import pytest
    import httpx
    import src.api.agnes_client as ac

    async def _no_sleep(s):
        pass
    monkeypatch.setattr(ac.asyncio, "sleep", _no_sleep)
    mock = MockAgnes(stream_fail_mode="mid", stream_fail_times=1)
    agnes = make_agnes(mock)

    async def run():
        got = []
        async for ev in agnes.chat_completion_stream([{"role": "user", "content": "hi"}]):
            got.append(ev)

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(run())
    assert len(mock.find("POST", "/v1/chat/completions")) == 1, "已推送事件后不得重发"


# ---------- 2026-09-06：视频模型双契约（v2.0 legacy / 2.5-flash 新契约） ----------
def test_create_video_25_contract_body(client):
    """2.5-flash 新契约：model/mode=text/seconds 字符串；不带 num_frames/width/height
    （实测 400 报错原文：duration/resolution is not allowed，num_frames forbidden，
    seconds 必须 string——_video25_probe/）。"""
    mock = MockAgnes()
    agnes = make_agnes(mock)
    asyncio.run(agnes.create_video("prompt", mode="t2v", num_frames=121,
                                   frame_rate=24, width=1280, height=704,
                                   model="agnes-video-2.5-flash", duration_s=5))
    body = mock.bodies("POST", "/v1/videos")[-1]
    assert body["model"] == "agnes-video-2.5-flash"
    assert body["mode"] == "text"
    assert body["seconds"] == "5"
    for forbidden in ("num_frames", "width", "height", "duration", "resolution",
                      "frame_rate"):
        assert forbidden not in body, forbidden


def test_create_video_25_i2v_keyframe_image_top_level(client):
    """2.5 i2v → mode=keyframe + 顶层 image 公网 URL（实测生成成功）。"""
    mock = MockAgnes()
    agnes = make_agnes(mock)
    asyncio.run(agnes.create_video("prompt", mode="i2v", image_url="https://x/f.png",
                                   model="agnes-video-2.5-flash", duration_s=6))
    body = mock.bodies("POST", "/v1/videos")[-1]
    assert body["mode"] == "keyframe"
    assert body["image"] == "https://x/f.png"
    assert body["seconds"] == "6"


def test_create_video_25_keyframes_dual_frame_accepted(client):
    """42.md §四 P2（2026-09-29 真机探针 _probe_video25_keyframes.py 一次命中）：
    2.5 keyframe 模式官方 first_frame/last_frame 双帧字段受理且两端均被钉住——
    client 放行 keyframes 双帧，映射为官方字段（桥路径解锁）。
    历史锚点（2.5 上线时「双帧未验证勿发」拒绝）由探针证据退役。"""
    mock = MockAgnes()
    agnes = make_agnes(mock)
    asyncio.run(agnes.create_video("prompt", mode="keyframes",
                                   image_urls=["https://x/a.png", "https://x/b.png"],
                                   model="agnes-video-2.5-flash"))
    body = mock.bodies("POST", "/v1/videos")[-1]
    assert body["mode"] == "keyframe"
    assert body["first_frame"] == "https://x/a.png"
    assert body["last_frame"] == "https://x/b.png"
    # 数量守卫：缺 last / 多帧 → 结构化拒绝（不静默裁剪）
    import pytest
    from src.api.agnes_client import AgnesError
    with pytest.raises(AgnesError):
        asyncio.run(agnes.create_video("prompt", mode="keyframes",
                                       image_urls=["https://x/a.png"],
                                       model="agnes-video-2.5-flash"))
    with pytest.raises(AgnesError):
        asyncio.run(agnes.create_video("prompt", mode="keyframes",
                                       image_urls=["https://x/a.png", "https://x/b.png",
                                                   "https://x/c.png"],
                                       model="agnes-video-2.5-flash"))


def test_video_v20_model_rejected(client):
    """2026-09-28：v2.0 已删除（官方 2026-09-25 下线 + 用户指令）——显式指定即拒绝，
    绝不发请求。"""
    import pytest
    from src.api.agnes_client import AgnesError
    mock = MockAgnes()
    agnes = make_agnes(mock)
    with pytest.raises(AgnesError) as ei:
        asyncio.run(agnes.create_video("prompt", mode="i2v",
                                       image_url="https://x/f.png",
                                       model="agnes-video-v2.0"))
    assert "已删除" in str(ei.value)
    assert mock.find("POST", "/v1/videos") == []


def test_create_video_25_multi_reference_images(client):
    """2.5 multi → mode=reference + 顶层 images 数组（官方字段，2026-09-28 文档核读；
    flash 上限 5）。"""
    mock = MockAgnes()
    agnes = make_agnes(mock)
    urls = ["https://x/look.png", "https://x/prev_tail.png"]
    asyncio.run(agnes.create_video("prompt", mode="multi", image_urls=urls,
                                   model="agnes-video-2.5-flash", duration_s=5))
    body = mock.bodies("POST", "/v1/videos")[-1]
    assert body["mode"] == "reference"
    assert body["images"] == urls
    assert body["seconds"] == "5"
    assert "image" not in body


def test_create_video_25_multi_ref_count_bounds(client):
    """reference 参考图 0 张或 >5 张 → 拒绝（flash images 上限 5），不发请求。"""
    import pytest
    from src.api.agnes_client import AgnesError
    mock = MockAgnes()
    agnes = make_agnes(mock)
    with pytest.raises(AgnesError):
        asyncio.run(agnes.create_video("prompt", mode="multi", image_urls=[],
                                       model="agnes-video-2.5-flash"))
    with pytest.raises(AgnesError):
        asyncio.run(agnes.create_video(
            "prompt", mode="multi",
            image_urls=[f"https://x/{i}.png" for i in range(6)],
            model="agnes-video-2.5-flash"))
    assert mock.find("POST", "/v1/videos") == []


def test_create_video_25_negative_prompt_rejected(client):
    """2.5 契约无 negative_prompt 字段（官方参数表 2026-09-28 核读）——非空显式拒绝
    （绝不静默丢参）。"""
    import pytest
    from src.api.agnes_client import AgnesError
    mock = MockAgnes()
    agnes = make_agnes(mock)
    with pytest.raises(AgnesError) as ei:
        asyncio.run(agnes.create_video("prompt", mode="t2v", negative_prompt="blurry",
                                       model="agnes-video-2.5-flash"))
    assert "negative_prompt" in str(ei.value)
    assert mock.find("POST", "/v1/videos") == []


def test_poll_video_carries_model_name(client):
    """2.5 任务轮询必须带提交时同一 model_name（否则查不到任务）。"""
    mock = MockAgnes(poll_in_progress=0)
    agnes = make_agnes(mock)
    asyncio.run(agnes.poll_video("task_25", interval=0, max_wait=5,
                                 model_name="agnes-video-2.5-flash"))
    req = mock.find("GET", "/agnesapi")[-1]
    assert req.url.params["model_name"] == "agnes-video-2.5-flash"
    assert req.url.params["video_id"] == "task_25"


def test_video_model_field_roundtrip(client):
    """VideoAsset/VideoVersion 的 model 可选字段：旧 JSON（无 model）加载不炸、新字段可写。"""
    from src.core.models import VideoAsset, VideoVersion
    old = VideoAsset.from_dict({"video_id": "v1", "num_frames": 121}) if hasattr(VideoAsset, "from_dict") else None
    if old is not None:
        assert old.model is None
    vv = VideoVersion(version_id="vv_1", model="agnes-video-2.5-flash")
    assert vv.model == "agnes-video-2.5-flash"


# ---------- Phase D §2：2K 档全 ratio 扩展（真机探针 _phase_d_2k_probe 佐证）----------
def test_resolve_size_2k_extended_ratios(client):
    """2K 档覆盖全部既有 ratio：官方表（1:1/3:4/4:3/16:9/9:16/21:9）
    + 真机探针 PASS 的 2:3（角色三视图竖图）与按族对称的 3:2。
    未知 tier 仍回落 1K（既有语义不变）。"""
    from src.api.agnes_client import resolve_size
    assert resolve_size("2K", "1:1") == "2048x2048"
    assert resolve_size("2K", "16:9") == "2624x1472"
    assert resolve_size("2K", "9:16") == "1472x2624"
    assert resolve_size("2K", "2:3") == "1664x2496"
    assert resolve_size("2K", "3:2") == "2496x1664"
    assert resolve_size("2K", "3:4") == "1728x2304"
    assert resolve_size("2K", "4:3") == "2304x1728"
    assert resolve_size("2K", "21:9") == "3136x1344"
    assert resolve_size("9K", "16:9") == "1312x736"
