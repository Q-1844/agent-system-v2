"""Director Visual Production（2026-08-31）· shot.generate_images / shot.generate_videos 测试。

engine 级：真实 JOB_HANDLERS（routers/shots.py）+ 真实 JobQueue（worker 已 start）+
FakeAgnes/FakeRehost/FakePM（不联网）。覆盖任务书 smoke 清单中的 mock 项：

  1. 单 Shot 生成首帧：canonical 批次落盘（origin marker）+ 文件存在 + 自动选中；
  2. 批量（含预检拦截）：keyframes 尾帧被拦截、未知镜头 not_found、成功项验证通过；
  3. 锁定镜头默认跳过（不提交、不烧额度），force 可越过；
  4. 视频预检：首帧未就绪 → 结构化拦截不提交；首帧就绪 → 生成成功 + VideoVersion 落盘；
  5. 协作取消：已完成项保留（ok+verified），未完成项标 cancelled；
  6. variants=2：两个候选、选中第一张；
  7. 回执渲染 _op_human / trace target；
  8. director_turn 全链路：loop 收 generate 回执 → meta.generated_assets 供前端渲染。

🔴 每个测试只有【一个】asyncio.run：JobQueue worker 任务绑定在其事件循环上，
   跨 asyncio.run 复用队列会拿到已停摆的 worker（job 永远 queued）。
"""
import asyncio
import json
from pathlib import Path

import pytest

import deps
from src.core.models import (Project, Scene, Shot, Character, StyleAnchor, Asset,
                             ImageAsset, ShotStatus)
from src.core.store import ProjectStore
from src.core.job_queue import JobQueue
from src.agents.runtime import AgentRuntime, AgentError

# import 即触发 handler 可用性（测试自建 handlers dict，直接引用函数）
from routers.shots import _handler_first_frame, _handler_last_frame, _handler_video

from src.agents.director_visual import (
    _execute_shot_generate_images, _execute_shot_generate_videos,
)


PROJECT = "_test_dir_visual"


# ============ Fakes ============
class _FakeQuota:
    def can(self, kind, amount=1):
        return (True, "")

    def consume(self, kind, amount=1):
        pass


class FakeAgnes:
    """generate_image/create_video/poll_video/download 全 fake；download 真实写盘
    （canonical 验证要求文件存在）。generate_image 可注入慢速（分项时序控制）。"""

    def __init__(self):
        self.calls = []
        self.quota = _FakeQuota()
        self.gen_hook = None   # async (call_index) -> None，测试注入 sleep

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.calls.append("gen_image")
        if self.gen_hook is not None:
            await self.gen_hook(len(self.calls))
        return {"url": "https://fake.agnes/images/frame.png", "b64": None}

    async def download(self, url, dest):
        self.calls.append("download")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"FAKE_IMAGE")
        return str(dest)

    async def create_video(self, prompt, *, mode="i2v", image_url=None, image_urls=None,
                           num_frames=121, frame_rate=24, width=1312, height=736,
                           seed=None, negative_prompt="",
                           model=None, duration_s=None):
        self.calls.append("create_video")
        self.last_create_kwargs = dict(mode=mode, image_url=image_url,
                                       image_urls=image_urls, num_frames=num_frames,
                                       model=model, duration_s=duration_s)
        return {"video_id": "vid_fake_1", "task_id": "t_1"}

    async def poll_video(self, video_id, *, interval=5.0, max_wait=3600,
                         on_progress=None, max_consecutive_failures=5,
                         model_name=None):
        self.calls.append("poll")
        self.last_poll_model = model_name
        if on_progress:
            await on_progress(100, "completed", 0)
        return {"url": "https://fake.agnes/videos/fake.mp4",
                "seconds": 3.0, "size": "1280x704"}


class FakeRehost:
    async def upload(self, path):
        return "https://fake.imgur/rehost.png"


class _FakePM:
    def __init__(self, pdir):
        self._pdir = pdir
        self.base_dir = pdir

    def get_project_dir(self, name):
        return self._pdir


# ============ 装置 ============
def _shot(sid, idx=1, desc="机甲启动", chars=("阿派",), dur=5.0, link_mode="i2v"):
    s = Shot(shot_id=sid, scene_id=sid.rsplit("_c", 1)[0], index=idx,
             description=desc, characters=list(chars), target_duration_s=dur)
    s.link_mode = link_mode
    return s


def _save_proj(tmp_path, shots):
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事",
                   characters=[Character(name="阿派", description_en="a mecha pilot")],
                   scenes=[Scene(scene_id="s01", summary="出发", characters=["阿派"],
                                 shots=list(shots))])
    ProjectStore(tmp_path).save(proj)
    return proj


def _mark_first_frame_ready(tmp_path, shot_id, url="https://fake.agnes/first.png"):
    """给镜头造一个「已生成且已选」的首帧（video 前置：effective 首帧 URL 就绪）。"""
    store = ProjectStore(tmp_path)

    def mut(s):
        s.first_frame = ImageAsset(
            candidates=[Asset(local=str(tmp_path / "output" / "frames" / f"{shot_id}_first_v1.png"),
                              url=url, status=ShotStatus.DONE.value)],
            selected_index=0, status=ShotStatus.DONE.value)
    store.update_shot(shot_id, mut)


def _real_handlers():
    return {"first_frame": _handler_first_frame, "last_frame": _handler_last_frame,
            "video": _handler_video}


@pytest.fixture
def visual_env(tmp_path):
    """deps 单例换 fake + 真 JobQueue（未 start，测试内随事件循环 start）+ runtime。"""
    agnes = FakeAgnes()
    saved = (deps.agnes_client, deps._rehost_host, deps.project_manager)
    deps.agnes_client = agnes
    deps._rehost_host = FakeRehost()
    deps.project_manager = _FakePM(tmp_path)
    q = JobQueue(tmp_path / "jobs.json", _real_handlers())
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      job_queue_getter=lambda: q, agnes_getter=lambda: agnes,
                      client=None)
    try:
        yield rt, q, agnes, tmp_path
    finally:
        deps.agnes_client, deps._rehost_host, deps.project_manager = saved


# ============ 1. 单 Shot 生成首帧 ============
def test_generate_images_single_first_frame(visual_env):
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01")])

    async def scenario():
        await q.start()
        return await _execute_shot_generate_images(
            rt, PROJECT, json.dumps({"targets": [{"shot_id": "s01_c01", "frame": "first"}]}),
            "instr", "m1")

    obs = asyncio.run(scenario())
    assert obs["ok"] and obs["verified"] and obs["done"] == 1
    r = obs["results"][0]
    assert r["ok"] and r["verified"] and r["status"] == "done"
    assert r["local"] and Path(r["local"]).exists()
    # canonical：批次按 origin marker 落盘 + 自动选中第一候选 + 旧字段同步
    proj = ProjectStore(tmp_path).load()
    shot = proj.get_shot("s01_c01")
    assert len(shot.first_frame_batches) == 1
    assert shot.first_frame_batches[0].origin_run_id == r["origin_run_id"]
    assert shot.first_frame_batches[0].candidates[0].local == r["local"]
    assert shot.selected_first_frame == shot.first_frame_batches[0].candidates[0].candidate_id
    assert shot.first_frame.candidates and shot.first_frame.candidates[0].url
    # 字符参考快照（角色一致性进入 canonical）
    assert "阿派" in shot.first_frame_batches[0].char_ref_snapshot


# ============ 2. 批量 + 预检拦截（部分成功语义） ============
def test_generate_images_batch_with_precheck_blocks(visual_env):
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01"), _shot("s01_c02", idx=2, link_mode="i2v")])

    async def scenario():
        await q.start()
        return await _execute_shot_generate_images(
            rt, PROJECT,
            json.dumps({"targets": [
                {"shot_id": "s01_c01", "frame": "first"},
                {"shot_id": "s01_c02", "frame": "last"},    # i2v 不需要尾帧 → 拦截
                {"shot_id": "s09_c01", "frame": "first"},   # 不存在 → 拦截
            ]}, ensure_ascii=False),
            "instr", "m1")

    obs = asyncio.run(scenario())
    assert obs["ok"] and obs["verified"] and obs["done"] == 1 and obs["count"] == 3
    by = {(r["shot_id"], r["frame"]): r for r in obs["results"]}
    assert by[("s01_c01", "first")]["ok"] and by[("s01_c01", "first")]["local"]
    assert not by[("s01_c02", "last")]["ok"] and "keyframes" in by[("s01_c02", "last")]["error"]
    assert by[("s09_c01", "first")]["status"] == "not_found"
    # 只有 1 个 job 真正提交（拦截项不烧额度）
    assert agnes.calls.count("gen_image") == 1


def test_generate_images_locked_shot_skipped_unless_force(visual_env):
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01")])
    ProjectStore(tmp_path).update_shot("s01_c01", lambda s: setattr(s, "locked", True))

    async def scenario():
        await q.start()
        obs = await _execute_shot_generate_images(
            rt, PROJECT, json.dumps({"targets": [{"shot_id": "s01_c01"}]}), "instr", "m1")
        n_calls_after_lock = list(agnes.calls)
        obs2 = await _execute_shot_generate_images(
            rt, PROJECT, json.dumps({"targets": [{"shot_id": "s01_c01"}], "force": True}),
            "instr", "m1")
        return obs, obs2, n_calls_after_lock

    obs, obs2, calls_after_lock = asyncio.run(scenario())
    assert not obs["ok"] and obs["done"] == 0
    assert obs["results"][0]["status"] == "locked"
    assert "gen_image" not in calls_after_lock    # 未提交，未烧额度
    assert obs2["ok"] and obs2["done"] == 1       # force 越过锁定


def test_generate_images_last_frame_requires_ready_first(visual_env):
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="keyframes")])

    async def scenario():
        await q.start()
        return await _execute_shot_generate_images(
            rt, PROJECT, json.dumps({"targets": [{"shot_id": "s01_c01", "frame": "last"}]}),
            "instr", "m1")

    obs = asyncio.run(scenario())
    assert not obs["ok"] and obs["results"][0]["status"] == "blocked"
    assert "首帧未就绪" in obs["results"][0]["error"]
    assert agnes.calls.count("gen_image") == 0


# ============ 3. 视频工具 ============
def test_generate_videos_precheck_requires_first_frame(visual_env):
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01")])

    async def scenario():
        await q.start()
        return await _execute_shot_generate_videos(
            rt, PROJECT, json.dumps({"shot_ids": ["s01_c01"]}), "instr", "m1")

    obs = asyncio.run(scenario())
    assert not obs["ok"] and obs["results"][0]["status"] == "blocked"
    assert "开场画面未就绪" in obs["results"][0]["error"]
    assert "create_video" not in agnes.calls


def test_generate_videos_ok(visual_env):
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01")])
    _mark_first_frame_ready(tmp_path, "s01_c01")

    async def scenario():
        await q.start()
        return await _execute_shot_generate_videos(
            rt, PROJECT,
            json.dumps({"shot_ids": ["s01_c01"],
                        "reference_policy": "legacy_allowed"}), "instr", "m1")

    obs = asyncio.run(scenario())
    assert obs["ok"] and obs["verified"] and obs["done"] == 1
    r = obs["results"][0]
    assert r["ok"] and r["local"] and Path(r["local"]).exists()
    assert r.get("seconds") == 3.0
    proj = ProjectStore(tmp_path).load()
    shot = proj.get_shot("s01_c01")
    # 按 origin marker 精确匹配（管线可能附带 legacy_video 迁移批次，属既有行为）
    mine = [vv for vv in shot.video_versions if vv.origin_run_id == r["origin_run_id"]]
    assert len(mine) == 1
    assert shot.final_video == mine[0].version_id
    assert mine[0].local == r["local"]
    # Phase55 A：统一输入依据清单随版本落档（legacy 显式标注场景未进视觉输入）
    im = mine[0].generation_basis.get("input_manifest")
    assert im and im["schema"] == "input_manifest_v1"
    assert im["reference_consumption"]["state"] == "absent"
    assert im["method"]["mode"] == "i2v"


# ============ 视频模型选择（2026-09-06 新契约接入）============
def test_generate_videos_with_25_model_threads_model_through_chain(visual_env):
    """video_model=agnes-video-2.5-flash：create_video 收到 model+duration_s（4-12s），
    poll 用同一 model_name，模型落库 shot.video.model 与 VideoVersion.model。"""
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01")])
    _mark_first_frame_ready(tmp_path, "s01_c01")

    async def scenario():
        await q.start()
        return await _execute_shot_generate_videos(
            rt, PROJECT,
            json.dumps({"shot_ids": ["s01_c01"],
                        "video_model": "agnes-video-2.5-flash",
                        "reference_policy": "legacy_allowed"}),
            "instr", "m1")

    obs = asyncio.run(scenario())
    assert obs["ok"] and obs["verified"] and obs["done"] == 1
    assert agnes.last_create_kwargs["model"] == "agnes-video-2.5-flash"
    assert 4 <= agnes.last_create_kwargs["duration_s"] <= 12
    assert agnes.last_poll_model == "agnes-video-2.5-flash"
    proj = ProjectStore(tmp_path).load()
    shot = proj.get_shot("s01_c01")
    assert shot.video.model == "agnes-video-2.5-flash"
    mine = [vv for vv in shot.video_versions if vv.origin_run_id == obs["results"][0]["origin_run_id"]]
    assert len(mine) == 1 and mine[0].model == "agnes-video-2.5-flash"


def test_generate_videos_default_model_resolved_to_25_flash(visual_env):
    """不传 video_model：解析为唯一视频模型 agnes-video-2.5-flash（2026-09-28 v2.0
    已删除）——create/poll/落库全链同一 model_name（恢复续轮询正确性）。"""
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01")])
    _mark_first_frame_ready(tmp_path, "s01_c01")

    async def scenario():
        await q.start()
        return await _execute_shot_generate_videos(
            rt, PROJECT,
            json.dumps({"shot_ids": ["s01_c01"],
                        "reference_policy": "legacy_allowed"}), "instr", "m1")

    obs = asyncio.run(scenario())
    assert obs["ok"] and obs["verified"]
    assert agnes.last_create_kwargs["model"] == "agnes-video-2.5-flash"
    assert agnes.last_poll_model == "agnes-video-2.5-flash"
    proj = ProjectStore(tmp_path).load()
    assert proj.get_shot("s01_c01").video.model == "agnes-video-2.5-flash"


def test_generate_videos_unknown_model_rejected(visual_env):
    """未知模型 → AgentError bad_arguments，不提交任务。"""
    import pytest
    from src.agents.runtime import AgentError
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01")])
    _mark_first_frame_ready(tmp_path, "s01_c01")

    async def scenario():
        await q.start()
        return await _execute_shot_generate_videos(
            rt, PROJECT,
            json.dumps({"shot_ids": ["s01_c01"], "video_model": "agnes-video-9.9"}),
            "instr", "m1")

    with pytest.raises(AgentError) as ei:
        asyncio.run(scenario())
    assert "未知视频模型" in str(ei.value)
    assert agnes.calls == []


# ============ 4. 协作取消：已完成保留、未完成标 cancelled ============
def test_generate_images_cancel_preserves_done(visual_env):
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01"), _shot("s01_c02", idx=2)])
    # 第 1 次生成秒完；其后每次慢 30s（远超取消等待窗口）→ 确定的「1 done + 1 running」
    agnes.gen_hook = None

    async def gen_hook(call_index):
        if call_index > 1:
            await asyncio.sleep(30)
    agnes.gen_hook = gen_hook

    async def scenario():
        await q.start()
        cancel_event = asyncio.Event()
        task = asyncio.ensure_future(_execute_shot_generate_images(
            rt, PROJECT,
            json.dumps({"targets": [{"shot_id": "s01_c01"}, {"shot_id": "s01_c02"}]}),
            "instr", "m1", event_cb=None, cancel_event=cancel_event))
        await asyncio.sleep(1.5)     # 第 1 张完成、第 2 张仍在慢生成
        cancel_event.set()
        return await task

    obs = asyncio.run(scenario())
    by = {r["shot_id"]: r for r in obs["results"]}
    assert by["s01_c01"]["ok"] and by["s01_c01"]["verified"] and by["s01_c01"]["local"]
    assert by["s01_c02"]["status"] == "cancelled" and not by["s01_c02"]["ok"]
    assert obs["cancelled"] is True and obs["ok"] is True and obs["done"] == 1
    # 已完成资产保留在 canonical；未完成的没有批次
    proj = ProjectStore(tmp_path).load()
    assert len(proj.get_shot("s01_c01").first_frame_batches) == 1
    assert len(proj.get_shot("s01_c02").first_frame_batches) == 0


# ============ 5. variants 与参数校验 ============
def test_generate_images_variants_two_candidates(visual_env):
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01")])

    async def scenario():
        await q.start()
        return await _execute_shot_generate_images(
            rt, PROJECT, json.dumps({"targets": [{"shot_id": "s01_c01"}], "variants": 2}),
            "instr", "m1")

    obs = asyncio.run(scenario())
    assert obs["ok"] and obs["variants"] == 2
    assert agnes.calls.count("gen_image") == 2
    proj = ProjectStore(tmp_path).load()
    shot = proj.get_shot("s01_c01")
    assert len(shot.first_frame_batches[-1].candidates) == 2
    assert shot.selected_first_frame == shot.first_frame_batches[-1].candidates[0].candidate_id


def test_generate_images_bad_arguments(visual_env):
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01")])

    async def scenario():
        await q.start()
        with pytest.raises(AgentError):
            await _execute_shot_generate_images(rt, PROJECT, json.dumps({"targets": []}), "u", "m")
        with pytest.raises(AgentError):
            await _execute_shot_generate_images(
                rt, PROJECT,
                json.dumps({"targets": [{"shot_id": "s01_c01"}, {"shot_id": "s01_c01"}]}),
                "u", "m")
        with pytest.raises(AgentError):
            await _execute_shot_generate_videos(rt, PROJECT, json.dumps({"shot_ids": []}), "u", "m")

    asyncio.run(scenario())


# ============ 6. 回执渲染 / trace ============
def test_op_human_rendering():
    from src.agents.director import _op_human, _trace_entry
    receipt_ok = {"operation": "shot.generate_images", "ok": True, "count": 2,
                  "results": [{"shot_id": "s01_c01", "frame": "first", "ok": True},
                              {"shot_id": "s01_c02", "frame": "first", "ok": False,
                               "error": "x"}]}
    assert "1/2" in _op_human(receipt_ok) and "s01_c01首帧" in _op_human(receipt_ok)
    receipt_vid = {"operation": "shot.generate_videos", "ok": True, "count": 1,
                   "results": [{"shot_id": "s01_c01", "frame": "video", "ok": True}]}
    assert "生成视频" in _op_human(receipt_vid)
    entry = _trace_entry(1, "shot.generate_images", "{}",
                         {"operation": "shot.generate_images", "ok": True,
                          "target": "s01_c01,s01_c02"})
    assert entry["target"] == "s01_c01,s01_c02" and entry["ok"]


# ============ 7. director_turn 全链路：meta.generated_assets ============
def test_director_turn_meta_generated_assets(visual_env):
    from src.agents.director import director_turn
    rt, q, agnes, tmp_path = visual_env
    _save_proj(tmp_path, [_shot("s01_c01")])

    class ScriptedClient:
        def __init__(self):
            self.calls = 0

        async def chat_completion(self, messages, **kw):
            self.calls += 1
            if self.calls == 1:
                return {"content": None, "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "shot.generate_images",
                                  "arguments": json.dumps(
                                      {"targets": [{"shot_id": "s01_c01"}]},
                                      ensure_ascii=False)}}]}
            return {"content": "已为 1 个镜头生成图片。", "tool_calls": None}

    rt._client = ScriptedClient()

    async def scenario():
        await q.start()
        sid = rt.get_or_create_session(PROJECT, "director", "project", PROJECT)["session_id"]
        return await director_turn(rt, PROJECT, sid, "给 s01_c01 生成首帧")

    res = asyncio.run(scenario())
    meta = res["message"]["meta"]
    assert meta["generated_assets"], meta
    ga = meta["generated_assets"][0]
    assert ga["shot_id"] == "s01_c01" and ga["kind"] == "first"
    assert Path(ga["path"]).exists()
    # 回执进入 meta.receipts（前端「本轮改动」渲染依赖）
    gen_receipts = [r for r in meta["receipts"] if r["operation"] == "shot.generate_images"]
    assert gen_receipts and gen_receipts[0]["ok"] and gen_receipts[0]["verified"]
    # trace 记录了该工具（刷新后折叠条重建依赖）
    assert any(t["tool"] == "shot.generate_images" for t in meta["trace"])
