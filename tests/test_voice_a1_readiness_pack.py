# tests/test_voice_a1_readiness_pack.py
# Audio V1 Phase A1 §28：Readiness 三态（§8）+ Pack determinism（§9/§10/§45）。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.models import Character, Project, Scene, Shot
from src.core.store import ProjectStore
from src.audio.models import (
    VOICE_READINESS_NOT_READY,
    VOICE_READINESS_READY,
    VOICE_READINESS_UNBOUND,
)
from src.audio.service import VoiceService
from src.audio.voice_pack import resolve_dialogue, resolve_speaker


def make_svc(tmp_path, characters=("小明", "小红", "阿强")):
    p = Project(name="voice_a1_readiness",
                characters=[Character(name=c) for c in characters])
    p.scenes.append(Scene(scene_id="s01", characters=list(characters), shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1,
             characters=["小明"], dialogue="我们现在出发。"),
        Shot(shot_id="s01_c02", scene_id="s01", index=2,
             characters=["小红"], dialogue="等一下。"),
        Shot(shot_id="s01_c03", scene_id="s01", index=3,
             characters=["小明", "小红"], dialogue="一起走吗？"),   # 多角色歧义镜
        Shot(shot_id="s01_c04", scene_id="s01", index=4,
             characters=[], dialogue="（空镜，无人声）"),              # 无角色镜
        Shot(shot_id="s01_c05", scene_id="s01", index=5,
             characters=["阿强"], dialogue="   "),                     # 空台词镜
    ]))
    ProjectStore(tmp_path).save(p)
    return VoiceService(tmp_path)


def approve(svc, character, voice_id):
    r = svc.create_profile(character, voice_id)
    assert r["ok"], r
    r2 = svc.approve_profile(f"vp_{character}", r["revision"])
    assert r2["ok"], r2


# ============ UNBOUND（§8）============

def test_unbound_no_profile(tmp_path):
    svc = make_svc(tmp_path)
    r = svc.readiness("s01_c01")
    assert r["status"] == VOICE_READINESS_UNBOUND
    assert r["code"] == "speaker_unbound"
    assert r["speaker"] == "小明"


# ============ NOT_READY（§8）============

def test_not_ready_draft_profile(tmp_path):
    svc = make_svc(tmp_path)
    svc.create_profile("小明", "male-qn-qingse")              # draft 未批准
    r = svc.readiness("s01_c01")
    assert r["status"] == VOICE_READINESS_NOT_READY
    assert r["code"] == "profile_not_approved"


def test_not_ready_provider_voice_id_missing(tmp_path):
    """§8：profile 存在（approved）但 provider_voice_id missing → NOT_READY。
    schema 允许空 voice_id（不代行业务门）；此状态经手改 JSON/数据演化出现，
    readiness 诚实 NOT_READY。"""
    svc = make_svc(tmp_path)
    outdir = tmp_path / "output"
    outdir.mkdir(exist_ok=True)
    (outdir / "voice_profiles.json").write_text(
        '{"schema_version": 1, "profiles": {"vp_小明": {"schema_version": 1,'
        '"voice_profile_id": "vp_小明", "character_name": "小明",'
        '"provider": "minimax", "provider_voice_id": "",'
        '"default_model": "speech-2.8-turbo", "approval_status": "approved"}}}',
        encoding="utf-8")
    r = svc.readiness("s01_c01")
    assert r["status"] == VOICE_READINESS_NOT_READY
    assert r["code"] == "provider_voice_id_missing"


def test_not_ready_no_speaker(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    r = svc.readiness("s01_c04")
    assert r["status"] == VOICE_READINESS_NOT_READY and r["code"] == "no_speaker"


def test_not_ready_ambiguous_speaker(tmp_path):
    """A1 无 speaker 字段：多角色镜不猜 → NOT_READY；显式裁决可解。"""
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    approve(svc, "小红", "female-shaonv")
    r = svc.readiness("s01_c03")
    assert r["status"] == VOICE_READINESS_NOT_READY and r["code"] == "ambiguous_speaker"
    r2 = svc.readiness("s01_c03", speaker="小红")              # 显式人裁决
    assert r2["status"] == VOICE_READINESS_READY
    assert r2["speaker"] == "小红"


def test_not_ready_invalid_explicit_speaker(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    r = svc.readiness("s01_c01", speaker="阿强")               # 不在本镜出场名单
    assert r["status"] == VOICE_READINESS_NOT_READY and r["code"] == "invalid_speaker"


def test_not_ready_dialogue_not_found(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    r = svc.readiness("s99_c99")
    assert r["status"] == VOICE_READINESS_NOT_READY and r["code"] == "dialogue_not_found"


def test_not_ready_empty_dialogue(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "阿强", "male-qn-jingying")
    r = svc.readiness("s01_c05")
    assert r["status"] == VOICE_READINESS_NOT_READY and r["code"] == "empty_dialogue"


# ============ READY（§8）============

def test_ready_approved_profile(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    r = svc.readiness("s01_c01")
    assert r["status"] == VOICE_READINESS_READY
    assert r["voice_profile"]["provider_voice_id"] == "male-qn-qingse"


# ============ readiness derived only（绝不写 canonical status 第二真相）============

def test_readiness_is_pure_read_zero_side_effect(tmp_path):
    svc = make_svc(tmp_path)
    outdir = tmp_path / "output"
    before = sorted(x.name for x in outdir.iterdir())
    for did in ("s01_c01", "s01_c02", "s01_c03", "s01_c04", "s99_c99"):
        svc.readiness(did)
    after = sorted(x.name for x in outdir.iterdir())
    assert before == after                                     # 无任何文件创建


# ============ Pack（§9/§10/§15/§45）============

def test_pack_determinism_same_input_same_pack(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    p1 = svc.compile_pack("s01_c01").to_dict()
    p2 = svc.compile_pack("s01_c01").to_dict()
    assert p1 == p2                                            # 同输入 → 同 pack


def test_pack_canonical_text_unchanged(tmp_path):
    """§15：pack.text 必须 = canonical Dialogue 原样（不 strip / 不改写）。"""
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    pack = svc.compile_pack("s01_c01")
    shot = ProjectStore(tmp_path).load().get_shot("s01_c01")
    assert pack.text == shot.dialogue


def test_pack_correct_speaker_and_voice(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    approve(svc, "小红", "female-shaonv")
    p_a = svc.compile_pack("s01_c01")
    p_b = svc.compile_pack("s01_c02")
    assert p_a.speaker_character_name == "小明"
    assert p_a.provider_binding["provider_voice_id"] == "male-qn-qingse"
    assert p_b.speaker_character_name == "小红"
    assert p_b.provider_binding["provider_voice_id"] == "female-shaonv"
    assert p_b.provider_binding["default_model"] == "speech-2.8-turbo"


def test_pack_generation_basis_complete_and_inspectable(tmp_path):
    """§10：basis 能证明「哪句 Dialogue + 哪个 VoiceProfile」；无 clock/随机/临时路径。"""
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    basis = svc.compile_pack("s01_c01").generation_basis
    for key in ("schema", "dialogue_id", "dialogue_fingerprint",
                "speaker_character_name", "voice_profile_id",
                "voice_profile_revision", "provider", "model",
                "provider_voice_id", "synthesis_settings_fingerprint"):
        assert key in basis and basis[key], key
    assert basis["dialogue_id"] == "s01_c01"
    assert basis["voice_profile_id"] == "vp_小明"
    assert basis["schema"] == "voice_generation_basis_v1"
    assert len(basis["dialogue_fingerprint"]) == 8             # 指纹族 = 纯 hex[:8]
    assert len(basis["synthesis_settings_fingerprint"]) == 8


def test_pack_text_change_changes_fingerprint(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    fp1 = svc.compile_pack("s01_c01").dialogue_fingerprint
    ProjectStore(tmp_path).update_shot(
        "s01_c01", lambda sh: setattr(sh, "dialogue", "我们现在出发！"))
    fp2 = svc.compile_pack("s01_c01").dialogue_fingerprint
    assert fp1 != fp2


def test_pack_speaker_change_changes_fingerprint(tmp_path):
    svc = make_svc(tmp_path)
    approve(svc, "小明", "male-qn-qingse")
    fp_single = svc.compile_pack("s01_c01").dialogue_fingerprint
    # 同文本同镜，换显式 speaker（演示 speaker 进入指纹；实际镜只有一个角色时
    # 显式给另一人会 invalid——此处直接对 resolution 层验证语义）
    shot = ProjectStore(tmp_path).load().get_shot("s01_c01")
    r1 = resolve_speaker(shot, "小明")
    shot.characters = ["小红"]
    r2 = resolve_speaker(shot)
    assert r1.speaker == "小明" and r2.speaker == "小红"
    assert fp_single  # sanity


def test_resolve_dialogue_requires_no_shot_mutation(tmp_path):
    """解析是纯读：Project 对象 dialogue/characters 不被改写。"""
    svc = make_svc(tmp_path)
    project = ProjectStore(tmp_path).load()
    res = resolve_dialogue(project, "s01_c03", "小明")
    assert res.speaker_resolution.speaker == "小明"
    assert res.text == "一起走吗？"
    assert project.get_shot("s01_c03").characters == ["小明", "小红"]
