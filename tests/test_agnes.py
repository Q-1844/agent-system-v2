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


# ---------- 坑3：num_frames 8n+1 进请求体 ----------
def test_create_video_num_frames_8n1(client):
    """create_video 把 num_frames 夹成 8n+1 再发：120->121（8*15+1），500->441（720p 档上限）"""
    mock = MockAgnes()
    agnes = make_agnes(mock)
    asyncio.run(agnes.create_video("prompt", mode="t2v", num_frames=120,
                                   frame_rate=24, width=1280, height=704))
    assert mock.bodies("POST", "/v1/videos")[-1]["num_frames"] == 121

    mock2 = MockAgnes()
    a2 = make_agnes(mock2)
    asyncio.run(a2.create_video("prompt", mode="t2v", num_frames=500,
                                frame_rate=24, width=1280, height=704))
    assert mock2.bodies("POST", "/v1/videos")[-1]["num_frames"] == 441   # 720p=441


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
