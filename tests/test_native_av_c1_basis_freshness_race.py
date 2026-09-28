# tests/test_native_av_c1_basis_freshness_race.py
# Native AV C1-R：VideoVersion generation_basis（21.md §10-§12）
# + NativeAVFreshness（§14）+ No Auto Regeneration（§15）+ Race guard（§16）。
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

import deps
from src.core.models import NativeAVSettings, VideoVersion
from src.core.store import ProjectStore as PS
from src.core.write_lock import _clear_for_test
from src.generation.native_av_pack import (
    NATIVE_AV_DEPENDENCY_KEYS,
    NativeAVDependencyChangedError,
    compute_current_dependency_keys,
    native_av_freshness,
    native_av_pre_commit_check,
    native_av_pre_dispatch_check,
)
from routers.shots import _handler_video, shot_video_freshness
from test_shot_binding_s3b1 import make_pdir               # noqa: E402
from test_shot_binding_s3c2 import (                        # noqa: E402
    FakeMediaClient,
    _load_project,
    _seed_first_frame,
    _set_video_ready,
)

LANG = "Mandarin Chinese"


class _StubPM:
    def __init__(self, pdir):
        self._pdir = pdir

    def get_project_dir(self, name):
        return self._pdir


def _set_project_language(pdir, lang=LANG):
    p = PS(pdir).load()
    p.native_av = NativeAVSettings(spoken_language=lang)
    PS(pdir).save(p)


def _set_dialogue(pdir, dialogue, shot_id="s01_c01", characters=None):
    def mut(s):
        s.dialogue = dialogue
        if characters:
            s.characters = characters
    PS(pdir).update_shot(shot_id, mut)


def _run_video(pdir, shot_id="s01_c01", params=None, fake=None):
    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=pdir.name, shot_id=shot_id,
                          params=params or {}, kind="video")
    asyncio.run(_handler_video(job, emit))


@pytest.fixture()
def venv(tmp_path, monkeypatch):
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    fake = FakeMediaClient()
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    yield pdir, fake
    _clear_for_test()


def _generate(pdir, fake, dialogue="我们现在出发。", characters=None):
    """标准生成路径：seed 首帧 → 设语言/台词 → 跑 handler → 返回 (shot, basis)。"""
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_project_language(pdir)
    _set_dialogue(pdir, dialogue, characters=characters)
    _run_video(pdir, fake=fake)
    project = _load_project(pdir)
    shot = project.get_shot("s01_c01")
    version = next(vv for vv in shot.video_versions if vv.version_id == shot.final_video)
    return shot, version


# ============ 21.md §10 VideoVersion.generation_basis ============

def test_video_version_records_generation_basis(venv):
    pdir, fake = venv
    _shot, version = _generate(pdir, fake)
    b = version.generation_basis
    assert b["schema"] == "native_av_generation_basis_v1"
    assert b["dialogue_fingerprint"]
    assert b["spoken_language"] == LANG
    assert "music_policy" not in b, "撤回键不得残留（21.md §20/§21）"
    assert b["provider_mode"] == "i2v"
    from src.api.agnes_client import DEFAULT_VIDEO_MODEL
    assert b["model"] == DEFAULT_VIDEO_MODEL, (
        "2026-09-28：不传 video_model → 解析为唯一视频模型（2.5-flash）并落库")
    assert b["prompt_compiler_schema"] == "native_av_prompt_v3"


def test_video_version_legacy_loads_with_empty_basis():
    """21.md §10：旧 VideoVersion JSON（无 generation_basis）→ {}，legacy 加载不破坏。"""
    old = {"version_id": "vv_legacy01", "status": "done", "video_id": "vid_x"}
    vv = VideoVersion(**{k: v for k, v in old.items()
                         if k in VideoVersion.__dataclass_fields__})
    assert vv.generation_basis == {}


def test_recover_attached_version_is_untracked(venv, monkeypatch):
    """recover 路径 attach 的版本 basis={}（原始编译输入不可考——诚实 untracked）。"""
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_project_language(pdir)
    _set_dialogue(pdir, "我们现在出发。")
    # 伪造「已提交未完成」：video_id 存在且 generating → handler 走 recover 分支
    def mut(s):
        s.video.video_id = "vid_recover0001"
        s.video.status = "generating"
    PS(pdir).update_shot("s01_c01", mut)
    _run_video(pdir, fake=fake)
    project = _load_project(pdir)
    shot = project.get_shot("s01_c01")
    version = next(vv for vv in shot.video_versions
                   if vv.version_id == shot.final_video)
    assert version.generation_basis == {}
    r = native_av_freshness(pdir, project, shot)
    assert r["status"] == "untracked_legacy"


# ============ 21.md §14 freshness 逐键 ============

def _freshness(pdir, shot):
    project = _load_project(pdir)
    return native_av_freshness(pdir, project, shot)


def test_freshness_current_after_generation(venv):
    pdir, _fake = venv
    _shot, _version = _generate(pdir, _fake if False else _fake)
    r = _freshness(pdir, _shot)
    assert r["status"] == "current", r


def test_freshness_stale_on_dialogue_change(venv):
    pdir, fake = venv
    shot, _v = _generate(pdir, fake)
    _set_dialogue(pdir, "台词已改。")
    r = _freshness(pdir, _load_project(pdir).get_shot("s01_c01"))
    assert r["status"] == "stale"
    assert "dialogue_fingerprint" in r["detail"]["mismatched_keys"]
    assert "speaker_map_fingerprint" not in r["detail"]["mismatched_keys"], \
        "speaker 结构未变 → 该键不误报（actual dependencies only）"


def test_freshness_stale_on_speaker_map_change(venv):
    """speaker 结构变化（内联前缀互换）→ speaker_map_fingerprint stale
    （dialogue_fingerprint 同时变，但两键各自成立）。"""
    pdir, fake = venv
    shot, _v = _generate(pdir, fake,
                         dialogue="Alice：开场词。\nGhost：回应词。",
                         characters=["Alice", "Ghost"])
    _set_dialogue(pdir, "Ghost：回应词。\nAlice：开场词。",
                  characters=["Alice", "Ghost"])
    r = _freshness(pdir, _load_project(pdir).get_shot("s01_c01"))
    assert r["status"] == "stale"
    assert "speaker_map_fingerprint" in r["detail"]["mismatched_keys"]


def test_freshness_stale_on_language_change(venv):
    pdir, fake = venv
    shot, _v = _generate(pdir, fake)
    _set_project_language(pdir, "Cantonese Chinese")
    r = _freshness(pdir, _load_project(pdir).get_shot("s01_c01"))
    assert r["status"] == "stale"
    assert "spoken_language" in r["detail"]["mismatched_keys"]


def test_freshness_stale_on_first_frame_reselect(venv):
    """UNBOUND visual basis = boundary frame 身份——换选首帧 → visual stale。"""
    pdir, fake = venv
    shot, _v = _generate(pdir, fake)
    PS(pdir).update_shot("s01_c01",
                         lambda s: setattr(s, "selected_first_frame", "cand_other"))
    r = _freshness(pdir, _load_project(pdir).get_shot("s01_c01"))
    assert r["status"] == "stale"
    assert "visual_basis_fingerprint" in r["detail"]["mismatched_keys"]


def test_freshness_stale_on_model_change(venv):
    """21.md §14/§22：model 变 → STALE（provider 事实 ∈ 对比集——换模型后
    旧 final 不再对应当前生成事实，必须诚实暴露）。"""
    pdir, fake = venv
    shot, version = _generate(pdir, fake)
    from src.api.agnes_client import DEFAULT_VIDEO_MODEL
    assert version.generation_basis["model"] == DEFAULT_VIDEO_MODEL
    PS(pdir).update_shot(
        "s01_c01",
        lambda s: s.video_versions[-1].generation_basis.update({"model": "m2"}))
    r = _freshness(pdir, _load_project(pdir).get_shot("s01_c01"))
    assert r["status"] == "stale"
    assert "model" in r["detail"]["mismatched_keys"]


def test_freshness_stale_on_provider_options_change(venv):
    """21.md §14/§22：provider options（seed/尺寸/帧数…）变 → STALE。"""
    pdir, fake = venv
    shot, _v = _generate(pdir, fake)
    PS(pdir).update_shot(
        "s01_c01",
        lambda s: s.video_versions[-1].generation_basis.update(
            {"provider_options_fingerprint": "deadbeef"}))
    r = _freshness(pdir, _load_project(pdir).get_shot("s01_c01"))
    assert r["status"] == "stale"
    assert "provider_options_fingerprint" in r["detail"]["mismatched_keys"]


def test_freshness_current_default_projection_matches_dispatch(venv):
    """21.md §14 读模型口径：默认 dispatch（UI 不传 video_model）落库的
    provider 事实 == 无请求上下文时的默认投影 → CURRENT 不误报。"""
    pdir, fake = venv
    shot, version = _generate(pdir, fake)
    b = version.generation_basis
    keys = compute_current_dependency_keys(pdir, _load_project(pdir), shot)
    for k in ("provider", "model", "provider_mode", "provider_options_fingerprint"):
        assert b[k] == keys[k], f"默认投影与 dispatch 不一致: {k}"


def test_freshness_unrelated_change_stays_current(venv):
    """21.md §11：无关变更（note 文本/另一镜）→ CURRENT（actual dependencies only）。"""
    pdir, fake = venv
    shot, _v = _generate(pdir, fake)
    PS(pdir).update_shot("s01_c01", lambda s: setattr(s, "note", "无关备注"))
    PS(pdir).update_shot("s01_c02", lambda s: setattr(s, "dialogue", "别的镜头台词"))
    r = _freshness(pdir, _load_project(pdir).get_shot("s01_c01"))
    assert r["status"] == "current", r


def test_freshness_missing_without_final(venv):
    pdir, fake = venv
    shot, _v = _generate(pdir, fake)
    PS(pdir).update_shot("s01_c01", lambda s: setattr(s, "final_video", None))
    r = _freshness(pdir, _load_project(pdir).get_shot("s01_c01"))
    assert r["status"] == "missing"


def test_freshness_is_pure_read_no_provider_call(venv):
    """21.md §15 No Auto Regeneration：freshness 是纯读模型——零 provider 调用、
    零 canonical 写入。"""
    pdir, fake = venv
    shot, _v = _generate(pdir, fake)
    calls_before = (len(fake.video_calls), len(fake.image_calls))
    before = (pdir / "output" / "project.json").read_bytes()
    for _ in range(3):
        _freshness(pdir, shot)
    assert (len(fake.video_calls), len(fake.image_calls)) == calls_before
    assert (pdir / "output" / "project.json").read_bytes() == before


# ============ 21.md §16 race guard ============

def test_pre_dispatch_rejects_after_dialogue_change(venv):
    pdir, fake = venv
    _shot, version = _generate(pdir, fake)
    basis = version.generation_basis
    _set_dialogue(pdir, "台词在 dispatch 前被改。")
    project = _load_project(pdir)
    with pytest.raises(NativeAVDependencyChangedError) as ei:
        native_av_pre_dispatch_check(pdir, project,
                                     project.get_shot("s01_c01"), basis)
    assert ei.value.code == "native_dependency_changed_before_generation"


def test_pre_dispatch_rejects_provider_options_drift(venv):
    """21.md §14/§16：请求 options 与 pack basis 不一致（同窗错位）→
    dispatch 前拒绝（零 provider 调用）。"""
    pdir, fake = venv
    _shot, version = _generate(pdir, fake)
    basis = version.generation_basis
    project = _load_project(pdir)
    with pytest.raises(NativeAVDependencyChangedError) as ei:
        native_av_pre_dispatch_check(
            pdir, project, project.get_shot("s01_c01"), basis,
            provider_options={"mode": "i2v", "model": "m2",
                              "num_frames": 81, "frame_rate": 16,
                              "width": 1280, "height": 704,
                              "seed": None, "negative_prompt": None})
    assert "model" in ei.value.detail["mismatched_keys"]


def test_pre_commit_rejects_after_dialogue_change(venv):
    pdir, fake = venv
    _shot, version = _generate(pdir, fake)
    basis = version.generation_basis
    _set_dialogue(pdir, "台词在生成窗口内被改。")
    project = _load_project(pdir)
    with pytest.raises(NativeAVDependencyChangedError) as ei:
        native_av_pre_commit_check(pdir, project,
                                   project.get_shot("s01_c01"), basis)
    assert ei.value.code == "native_dependency_changed_before_commit"


def _racing_fake(fake, pdir, mutate):
    """poll_video 成功返回前执行 mutate（模拟生成窗口内 canonical 变化）。"""
    original_poll = fake.poll_video

    async def poll(video_id, **kw):
        res = await original_poll(video_id, **kw)
        mutate()
        return res
    fake.poll_video = poll
    return fake


def test_handler_race_dialogue_changed_mid_generation_not_attached(venv):
    """§27 handler 级：provider 生成窗口内 dialogue 被改 → 拒绝 attach——
    不建 VideoVersion、不设 final_video、旧 final 保持不变。"""
    pdir, fake = venv
    # 第一次生成（干净）建立基线
    shot, version = _generate(pdir, fake)
    baseline_final = shot.final_video
    baseline_versions = len(shot.video_versions)
    # 第二次生成，poll 返回前改 dialogue
    _set_dialogue(pdir, "新台词第二段生成")
    PS(pdir).update_shot("s01_c01",
                         lambda s: (setattr(s.video, "video_id", None),
                                    setattr(s.video, "status", "pending")))

    def mutate():
        _set_dialogue(pdir, "生成窗口内又被改！")

    _racing_fake(fake, pdir, mutate)
    with pytest.raises(NativeAVDependencyChangedError):
        _run_video(pdir, fake=fake)
    shot2 = _load_project(pdir).get_shot("s01_c01")
    assert shot2.final_video == baseline_final, "旧 final 不被覆盖"
    assert len(shot2.video_versions) == baseline_versions, "不建新 VideoVersion"


# ============ 21.md §14 读端点 ============

def test_freshness_endpoint(venv):
    pdir, fake = venv
    shot, _v = _generate(pdir, fake)
    r = asyncio.run(shot_video_freshness(pdir.name, "s01_c01"))
    assert r["status"] == "current"
    _set_dialogue(pdir, "台词又变了。")
    r2 = asyncio.run(shot_video_freshness(pdir.name, "s01_c01"))
    assert r2["status"] == "stale"
    assert r2["detail"]["mismatched_keys"]
