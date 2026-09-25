# Phase 39（39.md §6.4/§15-§20）：bridge_service 确定性测试。
# 覆盖：时长 10s 下限拒绝 / 中间节拍拒绝 / UNBOUND 全链 happy path（插镜位次、
# relay、keyframes 字段、seed、提交）/ 幂等 / 前置缺失 fail closed /
# seed 后 refresh → boundary_frame_stale（fail closed，绝不重新 seed）。
import asyncio
import json
from pathlib import Path

import pytest

from src.core.models import Asset, Project, Scene, Shot, ShotStatus, StyleAnchor, VideoVersion
from src.core.store import ProjectStore
from src.storyboard import bridge_service as bs


def _make_video(path: Path, color: str = "red", seconds: float = 1.0) -> Path:
    import subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={color}:size=64x48:rate=8:duration={seconds}",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    return path


def _jpg(path: Path) -> Path:
    from PIL import Image
    Image.new("RGB", (64, 48), (30, 180, 40)).save(path, "JPEG", quality=95)
    return path


def _proj(videos_dir: Path) -> Project:
    """锚点 c01（尾帧就绪）+ 后继 c02（有最终视频）。UNBOUND（无 binding）。"""
    v1 = _make_video(videos_dir / "s01_c01.mp4")
    v2 = _make_video(videos_dir / "s01_c02.mp4")
    tail = _jpg(videos_dir / "s01_c01_end.jpg")
    c01 = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="A",
               characters=["狐狸"],
               final_video="vv_a", video_end_frame=Asset(local=str(tail), url="https://h/tail.jpg",
                                                         status=ShotStatus.DONE.value),
               video_end_source_version="vv_a")
    c01.video_versions.append(VideoVersion(version_id="vv_a", local=str(v1),
                                           status=ShotStatus.DONE.value))
    c02 = Shot(shot_id="s01_c02", scene_id="s01", index=2, description="B",
               characters=["狐狸"], final_video="vv_b")
    c02.video_versions.append(VideoVersion(version_id="vv_b", local=str(v2),
                                           status=ShotStatus.DONE.value))
    return Project(name="p39bridge", style=StyleAnchor(),
                   scenes=[Scene(scene_id="s01", shots=[c01, c02])])


class FakeHost:
    def __init__(self):
        self.uploaded = []

    async def upload(self, local):
        self.uploaded.append(local)
        return f"https://host/{Path(local).name}"


class FakeJob:
    def __init__(self, job_id, params):
        self.job_id = job_id
        self.params = params
        self.state = "queued"
        self.error = None
        self.shot_id = params.get("shot_id")


class FakeQueue:
    def __init__(self):
        self.submitted = []
        self.jobs = {}

    def submit(self, project, kind, shot_id, **extra):
        job = FakeJob(f"job_{len(self.submitted) + 1}",
                      {"project": project, "kind": kind, "shot_id": shot_id, **extra})
        self.submitted.append((project, kind, shot_id, extra))
        self.jobs[job.job_id] = job
        return job

    def get(self, job_id):
        return self.jobs.get(job_id)

    def cancel(self, job_id, force=False):
        return True


@pytest.fixture()
def env(tmp_path, monkeypatch):
    pdir = tmp_path / "proj"
    pdir.mkdir()
    ProjectStore(pdir).save(_proj(pdir / "output" / "videos"))
    return pdir, FakeHost(), FakeQueue()


BEATS = "低头→嗅沿瓜皮→绕半步→鼻贴瓜皮→鼻尖悬停"


# ============ 确定性验收前移（39.md 测试重点 10/11）============
def test_reject_duration_below_10s(env):
    with pytest.raises(bs.BridgeError) as ei:
        bs.validate_bridge_request(5.0, BEATS)
    assert ei.value.code == "bridge_duration_below_minimum"


def test_reject_transition_without_beats(env):
    with pytest.raises(bs.BridgeError) as ei:
        bs.validate_bridge_request(10.0, "from A to B")
    assert ei.value.code == "bridge_transition_beats_missing"
    # 实证桥描述（M3B 用 - 分隔，M5B 用 →）都应通过
    bs.validate_bridge_request(10.0, BEATS)
    bs.validate_bridge_request(10.0,
                               "狐狸从雨林边缘加速冲刺-金色光爆-世界展开为明亮村庄-落到村口土路")


# ============ UNBOUND happy path（七步全链）============
def test_bridge_build_unbound_happy_path(env):
    pdir, host, q = env
    out = asyncio.run(bs.build_keyframes_bridge(
        pdir, "p39bridge", after_shot_id="s01_c01", transition_description=BEATS,
        duration_s=10.0, host=host, job_queue=q))

    assert out["ok"] and out["already_exists"] is False
    assert out["operation"] == "bridge.create"
    proj = ProjectStore(pdir).load()
    bridge = proj.get_shot(out["bridge_shot_id"])
    assert bridge is not None
    # 步骤 3：位次=锚点之后、后继之前；index 连续
    ids = [sh.shot_id for sh in proj.scenes[0].shots]
    assert ids == ["s01_c01", bridge.shot_id, "s01_c02"]
    assert [sh.index for sh in proj.scenes[0].shots] == [1, 2, 3]
    # 步骤 4：relay + keyframes 字段（与 REST PUT 同口径）
    assert bridge.source_shot_id == "s01_c01"
    assert bridge.opening_source == "inherit_previous"
    assert bridge.link_mode == "keyframes"
    assert bridge.transition_description == BEATS
    assert bridge.target_duration_s == 10.0
    assert bridge.video_prompt_en == ""
    assert bridge.characters == ["狐狸"]
    # 步骤 2：尾槽边界=后继镜实际首帧（托管）
    assert len(host.uploaded) == 1
    assert out["boundary"]["last_slot"]["url"].startswith("https://host/")
    assert out["boundary"]["last_slot"]["source_shot_id"] == "s01_c02"
    assert out["boundary"]["first_slot"]["source_shot_id"] == "s01_c01"
    # 步骤 5：UNBOUND（锚点无 binding → 不创建）
    assert out["binding"] == {"created": False, "refreshed": False,
                              "binding_revision": None}
    # 步骤 6：seed（批次 + 指针 + mark_done）
    assert len(bridge.last_frame_batches) == 1
    batch = bridge.last_frame_batches[0]
    assert batch.kind == "last_frame" and batch.status == "ready"
    assert batch.generation_basis == {"binding_readiness": "UNBOUND"}
    assert bridge.selected_last_frame == batch.candidates[0].candidate_id
    assert bridge.last_frame.status == "done"
    assert bridge.last_frame.selected.url == out["seed"]["url"]
    # 步骤 7：提交一次，origin marker 在 receipt
    assert len(q.submitted) == 1
    assert q.submitted[0][1] == "video" and q.submitted[0][2] == bridge.shot_id
    assert q.submitted[0][3]["origin_run_id"] == out["origin_run_id"]
    assert out["transition_beats"] == 5
    assert "boundary_frame_stale" in out["discipline"]["stale_guard"]


def test_bridge_build_idempotent(env):
    pdir, host, q = env
    first = asyncio.run(bs.build_keyframes_bridge(
        pdir, "p39bridge", after_shot_id="s01_c01", transition_description=BEATS,
        duration_s=10.0, host=host, job_queue=q))
    n_jobs = len(q.submitted)
    second = asyncio.run(bs.build_keyframes_bridge(
        pdir, "p39bridge", after_shot_id="s01_c01", transition_description=BEATS,
        duration_s=10.0, host=host, job_queue=q))
    assert second["already_exists"] is True
    assert second["bridge_shot_id"] == first["bridge_shot_id"]
    assert len(q.submitted) == n_jobs   # 幂等：不重复提交
    proj = ProjectStore(pdir).load()
    assert len([sh for sh in proj.scenes[0].shots]) == 3   # 不重复插镜


# ============ 前置缺失 fail closed ============
def test_bridge_anchor_tail_not_ready(env, tmp_path):
    pdir, host, q = env
    proj = ProjectStore(pdir).load()
    proj.get_shot("s01_c01").video_end_frame = Asset(status=ShotStatus.PENDING.value)
    ProjectStore(pdir).save(proj)
    with pytest.raises(bs.BridgeError) as ei:
        asyncio.run(bs.build_keyframes_bridge(
            pdir, "p39bridge", after_shot_id="s01_c01", transition_description=BEATS,
            duration_s=10.0, host=host, job_queue=q))
    assert ei.value.code == "anchor_tail_not_ready"


def test_bridge_next_shot_video_missing(env):
    pdir, host, q = env
    store = ProjectStore(pdir)
    proj = store.load()
    proj.get_shot("s01_c02").final_video = None
    proj.get_shot("s01_c02").video.local = None
    store.save(proj)
    with pytest.raises(bs.BridgeError) as ei:
        asyncio.run(bs.build_keyframes_bridge(
            pdir, "p39bridge", after_shot_id="s01_c01", transition_description=BEATS,
            duration_s=10.0, host=host, job_queue=q))
    assert ei.value.code == "next_shot_video_missing"


def test_bridge_no_successor(env, tmp_path):
    pdir, host, q = env
    store = ProjectStore(pdir)
    proj = store.load()
    proj.scenes[0].shots = [proj.get_shot("s01_c01")]
    store.save(proj)
    with pytest.raises(bs.BridgeError) as ei:
        asyncio.run(bs.build_keyframes_bridge(
            pdir, "p39bridge", after_shot_id="s01_c01", transition_description=BEATS,
            duration_s=10.0, host=host, job_queue=q))
    assert ei.value.code == "bridge_end_boundary_missing"


def test_bridge_anchor_not_found(env):
    pdir, host, q = env
    with pytest.raises(bs.BridgeError) as ei:
        asyncio.run(bs.build_keyframes_bridge(
            pdir, "p39bridge", after_shot_id="s01_c99", transition_description=BEATS,
            duration_s=10.0, host=host, job_queue=q))
    assert ei.value.code == "shot_not_found"


# ============ BOUND 路径：seed 后零 refresh 防线（39.md 测试重点 6）============
class _BindingScript:
    """模拟 BOUND 环境：锚点有 binding；create/refresh/compile 可用；
    post-seed 的 read_binding 可切换成「被 refresh 过的新 revision」。"""

    def __init__(self, seed_rev="rev2"):
        self.anchor_binding = {"binding": {"scene_binding": None,
                                           "character_bindings": [],
                                           "prop_bindings": []},
                               "binding_revision": "revA"}
        self.seed_rev = seed_rev
        self.post_seed_rev = seed_rev   # 改成别的值 = 模拟 seed 后被 refresh
        self.refresh_calls = []

    def patch(self, monkeypatch):
        from src.shot_binding.service import ShotBindingService
        script = self

        def read_binding(self, shot_id):
            if shot_id == "s01_c01":
                import json as _json
                return (_json.loads(_json.dumps(script.anchor_binding))
                        if script.anchor_binding else None)
            # 桥（动态 id）：当前返回 revision 由 script.current_bridge_rev 控制
            return {"binding_revision": script.current_bridge_rev}

        script.current_bridge_rev = "revB"   # create/refresh 阶段
        self._created = False

        async def create_binding_async(self, payload):
            script._created = True
            return {"ok": True, "operation": "shot_binding.create"}

        async def refresh_binding_async(self, shot_id, *, expected_binding_revision):
            script.refresh_calls.append((shot_id, expected_binding_revision))
            script.current_bridge_rev = script.seed_rev   # 模拟 refresh 持久化
            return {"ok": True, "binding_revision": script.seed_rev}

        def compile_reference_pack(self, shot_id):
            return {"status": "COMPILED",
                    "pack": {"basis": {"binding_revision": script.seed_rev,
                                       "binding_readiness": "READY"}}}

        monkeypatch.setattr(ShotBindingService, "read_binding", read_binding)
        monkeypatch.setattr(ShotBindingService, "create_binding_async",
                            create_binding_async)
        monkeypatch.setattr(ShotBindingService, "refresh_binding_async",
                            refresh_binding_async)
        monkeypatch.setattr(ShotBindingService, "compile_reference_pack",
                            compile_reference_pack)


def test_bound_path_stale_after_post_seed_refresh(env, monkeypatch):
    """seed 之后 binding 被第三方 refresh → 提交前守卫 boundary_frame_stale，
    fail closed，绝不偷偷重新 seed。"""
    pdir, host, q = env
    script = _BindingScript()
    script.patch(monkeypatch)

    # 拦截 seed：记录 seed 后把 post_seed_rev 改成新值（模拟 seed 与提交之间被 refresh）
    orig_ensure = bs.ensure_boundary_fresh

    def _sabotage_then_ensure(pdir_, name, shot_id, basis):
        script.current_bridge_rev = "revZ"   # 模拟 seed 后第三方 refresh 顶掉 basis
        return orig_ensure(pdir_, name, shot_id, basis)

    monkeypatch.setattr(bs, "ensure_boundary_fresh", _sabotage_then_ensure)

    with pytest.raises(bs.BridgeError) as ei:
        asyncio.run(bs.build_keyframes_bridge(
            pdir, "p39bridge", after_shot_id="s01_c01", transition_description=BEATS,
            duration_s=10.0, host=host, job_queue=q))
    assert ei.value.code == "boundary_frame_stale"
    assert ei.value.detail["seed_binding_revision"] == "rev2"
    assert ei.value.detail["current_binding_revision"] == "revZ"
    # 未提交（fail closed 在 submit 之前）
    assert len(q.submitted) == 0
    # 绝不重新 seed：桥的 last_frame_batches 恰好 1 批
    proj = ProjectStore(pdir).load()
    bridge = next(sh for sh in proj.scenes[0].shots
                  if sh.source_shot_id == "s01_c01" and sh.link_mode == "keyframes")
    assert len(bridge.last_frame_batches) == 1


def test_bound_path_fresh_submits(env, monkeypatch):
    """BOUND happy path：create→refresh→compile→seed→守卫通过→提交。"""
    pdir, host, q = env
    script = _BindingScript()
    script.patch(monkeypatch)
    out = asyncio.run(bs.build_keyframes_bridge(
        pdir, "p39bridge", after_shot_id="s01_c01", transition_description=BEATS,
        duration_s=10.0, host=host, job_queue=q))
    assert out["ok"] and out["binding"]["created"] and out["binding"]["refreshed"]
    assert out["binding"]["binding_revision"] == "rev2"
    assert out["seed"]["generation_basis"]["binding_revision"] == "rev2"
    assert len(q.submitted) == 1
