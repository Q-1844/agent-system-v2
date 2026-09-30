# tests/test_native_av_b1_prompt_integration.py
# Native AV B1 — prompt integration + feature gate（17.md §10-§12/§14）。
#
# Commit B 范围：
#   - flag OFF（默认）→ 当前生成请求逐字节等价（dialogue 不进 prompt）；
#   - flag ON → 两条路径（UNBOUND legacy handler + BOUND adapter）block 追加在
#     现有 visual prompt 之后、原文原样；
#   - request shape 不变（无 audios / audio_url / reference mode）；
#   - fail closed → provider 调用数 = 0；
#   - VideoVersion.prompt_snapshot 记录完整编译 prompt（§13：零 schema 改动的证据位）。
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

import deps
from src.core.prompt_builder import build_video_prompt
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.generation.dialogue_control import DialogueControlError
from src.generation.video_adapter import build_bound_video_input
from routers.shots import _handler_video, shot_video
from test_shot_binding_s3b1 import make_pdir, run          # noqa: E402
from test_shot_binding_s3b2 import _bind, _build_scene      # noqa: E402
from test_shot_binding_s3c2 import (                        # noqa: E402
    FakeMediaClient,
    _bind_default,
    _current_basis,
    _load_project,
    _seed_first_frame,
    _seed_last_frame,
    _set_video_ready,
)
from src.shot_binding import ShotBindingService

LANG = "Mandarin Chinese"

# §11/§14：request shape 不变——BoundVideoRequest 字段集合硬断言
#（C1 §22：+generation_basis——Native AV 派生 basis，仍 ephemeral 不落盘字段）
BOUND_KEYS = {"shot_id", "prompt", "provider_mode", "first_frame", "last_frame",
              "video_reference",  # 34.md §13：multi 单语义参考槽（i2v/keyframes 下为空串）
              "identity_reference",  # 42.md 缺陷⑤：Look+尾帧双参考（prev_tail_look 哨兵；None=未启用）
              "scene_reference",  # 43.md §二：场景实例视图第三参考（prev_tail_look_scene 哨兵；None=未启用）
              "used_reference_ids", "dropped_optional", "diagnostics",
              "semantic_roles", "basis", "generation_basis"}
FAKE_VIDEO_KEYS = {"prompt", "mode", "image_url", "image_urls", "num_frames",
                   "frame_rate", "width", "height", "seed", "negative_prompt",
                   "model", "duration_s"}


class _StubPM:
    def __init__(self, pdir):
        self._pdir = pdir

    def get_project_dir(self, name):
        return self._pdir


def _make_venv(tmp_path, monkeypatch, *, with_scene):
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    ids = None
    if with_scene:
        (pdir / "output" / "scene_system.json").unlink()
        ids = run(_build_scene(pdir))
    fake = FakeMediaClient()
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    return pdir, ids, ShotBindingService(pdir), fake


@pytest.fixture()
def venv(tmp_path, monkeypatch):
    pdir, _ids, _svc, fake = _make_venv(tmp_path, monkeypatch, with_scene=False)
    yield pdir, fake
    _clear_for_test()


@pytest.fixture()
def bound_venv(tmp_path, monkeypatch):
    pdir, ids, svc, fake = _make_venv(tmp_path, monkeypatch, with_scene=True)
    yield pdir, ids, svc, fake
    _clear_for_test()


def _set_dialogue(pdir, shot_id="s01_c01", dialogue="", characters=None):
    def mut(s):
        if dialogue:
            s.dialogue = dialogue
        if characters:
            s.characters = characters
    ProjectStore(pdir).update_shot(shot_id, mut)


def _run_video(pdir, shot_id="s01_c01", params=None):
    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=pdir.name, shot_id=shot_id,
                          params=params or {}, kind="video")
    asyncio.run(_handler_video(job, emit))


NDC_ON = {"native_dialogue_control": True, "native_dialogue_language": LANG}


# ============ Legacy / UNBOUND handler（§14 regression）============

def test_flag_off_dialogue_stays_out_of_prompt(venv):
    """C1 18.md §4：显式 native_dialogue_control=False = debug/compat override
    → dialogue 不进 prompt（visual-only 行为逐字节不变）。"""
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    _run_video(pdir, params={"native_dialogue_control": False})
    assert len(fake.video_calls) == 1
    call = fake.video_calls[0]
    project = _load_project(pdir)
    assert call["prompt"] == build_video_prompt(project.get_shot("s01_c01"), "i2v")
    assert "我们现在出发" not in call["prompt"]
    assert "spoken dialogue" not in call["prompt"]


def test_flag_on_single_char_block_appended(venv):
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    _run_video(pdir, params=dict(NDC_ON))
    project = _load_project(pdir)
    shot = project.get_shot("s01_c01")
    call = fake.video_calls[0]
    assert call["prompt"].startswith(build_video_prompt(shot, "i2v")), \
        "visual prompt 原有内容不丢（§14）"
    assert "1. Alice says: 我们现在出发。" in call["prompt"]
    assert "Mandarin Chinese" in call["prompt"]
    # §13：VideoVersion.prompt_snapshot 记录完整编译 prompt（零 schema 改动的证据位）
    assert shot.video_versions[-1].prompt_snapshot == call["prompt"]


def test_flag_on_multi_char_inline_prefix_and_order(venv):
    pdir, fake = venv
    _seed_first_frame(pdir, shot_id="s01_c02")
    _set_video_ready(pdir, shot_id="s01_c02")
    _set_dialogue(pdir, shot_id="s01_c02",
                  dialogue="Alice：Open the door!\nGhost：Never.",
                  characters=["Alice", "Ghost"])
    _run_video(pdir, shot_id="s01_c02", params=dict(NDC_ON))
    prompt = fake.video_calls[0]["prompt"]
    assert "1. Alice says: Open the door!" in prompt
    assert "2. Ghost replies: Never." in prompt
    assert "Do not swap the speakers" in prompt


def test_flag_on_silent_character_instruction(venv):
    pdir, fake = venv
    _seed_first_frame(pdir, shot_id="s01_c02")
    _set_video_ready(pdir, shot_id="s01_c02")
    _set_dialogue(pdir, shot_id="s01_c02", dialogue="Alice：Wait.",
                  characters=["Alice", "Ghost"])
    _run_video(pdir, shot_id="s01_c02", params=dict(NDC_ON))
    prompt = fake.video_calls[0]["prompt"]
    assert "1. Alice says: Wait." in prompt
    assert "Ghost remains completely silent" in prompt


def test_flag_on_ambiguous_fail_closed_zero_provider_calls(venv):
    pdir, fake = venv
    _seed_first_frame(pdir, shot_id="s01_c02")
    _set_video_ready(pdir, shot_id="s01_c02")
    _set_dialogue(pdir, shot_id="s01_c02", dialogue="我不会屈服！",
                  characters=["Alice", "Ghost"])
    with pytest.raises(DialogueControlError) as ei:
        _run_video(pdir, shot_id="s01_c02", params=dict(NDC_ON))
    assert ei.value.code == "ambiguous_speaker"
    assert fake.video_calls == [], "fail closed → provider 调用数 = 0"


def test_flag_on_without_language_fail_closed(venv):
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    with pytest.raises(DialogueControlError) as ei:
        _run_video(pdir, params={"native_dialogue_control": True})
    assert ei.value.code == "language_missing"
    assert fake.video_calls == []


def test_flag_off_keyframes_behavior_unchanged(venv):
    """§14：first/last/keyframe behavior 不变（flag off；keyframes 双帧照旧）。"""
    pdir, fake = venv
    _seed_first_frame(pdir, shot_id="s01_c02")
    _seed_last_frame(pdir, shot_id="s01_c02")
    _set_video_ready(pdir, shot_id="s01_c02", link_mode="keyframes")
    _set_dialogue(pdir, shot_id="s01_c02", dialogue="台词。")
    _run_video(pdir, shot_id="s01_c02", params={"native_dialogue_control": False})
    call = fake.video_calls[0]
    project = _load_project(pdir)
    assert call["mode"] == "keyframes"
    assert call["prompt"] == build_video_prompt(project.get_shot("s01_c02"),
                                                "keyframes")
    assert call["image_urls"] is not None and len(call["image_urls"]) == 2


def test_request_shape_unchanged_no_audio_slots(venv):
    """§11/§14：不出现 audios / audio_url / reference mode；prompt 是唯一差异。"""
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    _run_video(pdir, params={"native_dialogue_control": False})   # A：显式 override off
    _run_video(pdir, params=dict(NDC_ON))              # B：flag on
    off, on = fake.video_calls[0], fake.video_calls[1]
    assert set(off.keys()) == FAKE_VIDEO_KEYS
    assert set(on.keys()) == FAKE_VIDEO_KEYS
    for k in FAKE_VIDEO_KEYS - {"prompt"}:
        assert off[k] == on[k], f"非 prompt 字段必须逐字节等价: {k}"
    blob = str(on).lower()
    assert "audios" not in blob and "audio_url" not in blob
    assert "reference mode" not in blob and '"mode": "reference"' not in blob


# ============ BOUND adapter（§10/§12）============

def test_bound_flag_off_identical(bound_venv):
    pdir, ids, svc, fake = bound_venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    project = _load_project(pdir)
    shot = project.get_shot("s01_c01")
    req = build_bound_video_input(pdir, project, shot,
                                  native_dialogue_control=False)   # 显式 override off
    assert "我们现在出发" not in req.prompt
    assert "spoken dialogue" not in req.prompt
    assert set(req.to_dict().keys()) == BOUND_KEYS


def test_bound_flag_on_block_at_tail_with_diagnostic(bound_venv):
    pdir, ids, svc, fake = bound_venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    project = _load_project(pdir)
    shot = project.get_shot("s01_c01")
    off = build_bound_video_input(pdir, project, shot, native_dialogue_control=False)
    on = build_bound_video_input(pdir, project, shot,
                                 native_dialogue_control=True,
                                 native_dialogue_language=LANG)
    assert on.prompt.startswith(off.prompt), "block 追加在完整 visual prompt（含 ctx/style）之后"
    assert "1. Alice says: 我们现在出发。" in on.prompt
    assert set(on.to_dict().keys()) == BOUND_KEYS
    diag = [d for d in on.diagnostics if d["reason"] == "native_dialogue_control_applied"]
    assert diag and diag[0]["detail"]["line_count"] == 1
    assert "native_dialogue_control_applied" not in [d["reason"] for d in off.diagnostics]


def test_bound_flag_on_no_dialogue_no_block(bound_venv):
    pdir, ids, svc, fake = bound_venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    project = _load_project(pdir)
    shot = project.get_shot("s01_c01")
    off = build_bound_video_input(pdir, project, shot, native_dialogue_control=False)
    on = build_bound_video_input(pdir, project, shot,
                                 native_dialogue_control=True,
                                 native_dialogue_language=LANG)
    assert on.prompt == off.prompt, "§14：无 dialogue → 行为不变"
    diag = [d for d in on.diagnostics if d["reason"] == "native_dialogue_control_applied"]
    assert diag and diag[0]["detail"]["resolution"] == "no_dialogue"


def test_bound_flag_on_speaker_not_in_characters_fail_closed(bound_venv):
    pdir, ids, svc, fake = bound_venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="Ghost：Boo!")   # Ghost ∉ Shot.characters=["Alice"]
    project = _load_project(pdir)
    shot = project.get_shot("s01_c01")
    with pytest.raises(DialogueControlError) as ei:
        build_bound_video_input(pdir, project, shot,
                                native_dialogue_control=True,
                                native_dialogue_language=LANG)
    assert ei.value.code == "speaker_not_in_characters"


def test_bound_handler_flag_on_dialogue_reaches_provider(bound_venv):
    """handler BOUND 路径把 ndc/ndc_lang 透传 adapter → provider prompt 含 block。"""
    pdir, ids, svc, fake = bound_venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    _run_video(pdir, params=dict(NDC_ON))
    assert len(fake.video_calls) == 1
    prompt = fake.video_calls[0]["prompt"]
    assert "1. Alice says: 我们现在出发。" in prompt
    assert "Mandarin Chinese" in prompt


def test_bound_handler_flag_on_ambiguous_fail_closed_zero_calls(bound_venv):
    # BOUND 路径 fail closed：adapter 抛出 → 零 provider 调用（不回落 legacy）
    pdir, ids, svc, fake = bound_venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="Ghost：Boo!")   # Ghost ∉ ["Alice"]
    with pytest.raises(DialogueControlError) as ei:
        _run_video(pdir, params=dict(NDC_ON))
    assert ei.value.code == "speaker_not_in_characters"
    assert fake.video_calls == []


# ============ 端点 gate 参数（C1 18.md §4：None=自动，False=override）============

def test_endpoint_exposes_feature_gate_params():
    import inspect
    sig = inspect.signature(shot_video)
    assert "native_dialogue_control" in sig.parameters
    assert sig.parameters["native_dialogue_control"].default is None
    assert "native_dialogue_language" in sig.parameters
    assert sig.parameters["native_dialogue_language"].default is None
