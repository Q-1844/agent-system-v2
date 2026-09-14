# tests/test_voice_a1_profile.py
# Audio V1 Phase A1 §28：CharacterVoiceProfile canonical（15.md §4/§5/§6/§45）。
# 全部 Fake（不联网）：store 行为 / OCC / approve 门 / fail closed schema。
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.models import Character, Project, Scene, Shot
from src.core.store import ProjectStore
from src.audio.models import (
    VOICE_APPROVAL_APPROVED,
    VOICE_APPROVAL_DRAFT,
    CharacterVoiceProfile,
    VoiceSchemaError,
    parse_voice_profile,
)
from src.audio.service import VoiceService
from src.audio.store import VoiceProfileStore


def make_project(tmp_path: Path) -> Path:
    p = Project(name="voice_a1_profile",
                characters=[Character(name="小明"), Character(name="小红")])
    p.scenes.append(Scene(scene_id="s01", characters=["小明"], shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1,
             characters=["小明"], dialogue="我们现在出发。")]))
    ProjectStore(tmp_path).save(p)
    return tmp_path


def make_svc(tmp_path: Path) -> VoiceService:
    return VoiceService(make_project(tmp_path))


# ============ create ============

def test_create_ok_draft_and_revision(tmp_path):
    svc = make_svc(tmp_path)
    r = svc.create_profile("小明", "male-qn-qingse", label="主男声")
    assert r["ok"] is True
    prof = r["voice_profile"]
    assert prof["character_name"] == "小明"
    assert prof["provider"] == "minimax"
    assert prof["default_model"] == "speech-2.8-turbo"     # 15.md §0 正式默认
    assert prof["approval_status"] == VOICE_APPROVAL_DRAFT
    assert prof["source_kind"] == "system"
    assert len(r["revision"]) == 8                          # sha256[:8] 指纹族


def test_create_persists_to_voice_profiles_json(tmp_path):
    svc = make_svc(tmp_path)
    svc.create_profile("小明", "male-qn-qingse")
    raw = json.loads((tmp_path / "output" / "voice_profiles.json")
                     .read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert "vp_小明" in raw["profiles"]
    assert raw["profiles"]["vp_小明"]["provider_voice_id"] == "male-qn-qingse"


def test_create_invalid_character(tmp_path):
    svc = make_svc(tmp_path)
    r = svc.create_profile("不存在角色", "male-qn-qingse")
    assert r["ok"] is False and r["error"] == "invalid_character"


def test_create_duplicate_profile_rejected(tmp_path):
    svc = make_svc(tmp_path)
    assert svc.create_profile("小明", "male-qn-qingse")["ok"] is True
    r = svc.create_profile("小明", "female-shaonv")
    assert r["ok"] is False and r["error"] == "profile_exists"


def test_create_validation_error_bad_speed(tmp_path):
    svc = make_svc(tmp_path)
    r = svc.create_profile("小明", "male-qn-qingse", speed=5.0)   # 越界 [0.5, 2.0]
    assert r["ok"] is False and r["error"] == "validation_error"


# ============ get / list ============

def test_get_and_list(tmp_path):
    svc = make_svc(tmp_path)
    svc.create_profile("小明", "male-qn-qingse")
    svc.create_profile("小红", "female-shaonv")
    assert svc.get_profile("vp_小明").provider_voice_id == "male-qn-qingse"
    assert svc.get_profile("vp_不存在") is None
    assert svc.get_profile_by_character("小红").voice_profile_id == "vp_小红"
    assert set(svc.list_profiles()) == {"vp_小明", "vp_小红"}


# ============ update（OCC）============

def test_update_ok_changes_revision(tmp_path):
    svc = make_svc(tmp_path)
    rev1 = svc.create_profile("小明", "male-qn-qingse")["revision"]
    r = svc.update_profile("vp_小明", rev1, speed=1.2, label="快语速")
    assert r["ok"] is True
    assert r["voice_profile"]["speed"] == 1.2
    assert r["revision"] != rev1                            # 语义变化 → revision 变


def test_update_stale_revision_rejected_no_silent_retry(tmp_path):
    svc = make_svc(tmp_path)
    rev1 = svc.create_profile("小明", "male-qn-qingse")["revision"]
    svc.update_profile("vp_小明", rev1, speed=1.2)
    r = svc.update_profile("vp_小明", rev1, speed=1.5)      # rev1 已过期
    assert r["ok"] is False and r["error"] == "stale_revision"
    assert svc.get_profile("vp_小明").speed == 1.2          # 拒绝且未写入


def test_update_label_does_not_change_revision(tmp_path):
    """label 是 display 字段（§7）：不改声音语义 → revision 不变 → 不引发 STALE。"""
    svc = make_svc(tmp_path)
    rev1 = svc.create_profile("小明", "male-qn-qingse")["revision"]
    r = svc.update_profile("vp_小明", rev1, label="改名不改声")
    assert r["ok"] is True and r["revision"] == rev1


def test_update_to_empty_voice_id_allowed_but_not_ready(tmp_path):
    """schema 不代行业务门：approved profile 可被 update 成空 voice_id（用户自改），
    但 readiness 诚实回落 NOT_READY（§8 provider_voice_id missing）。"""
    svc = make_svc(tmp_path)
    rev1 = svc.create_profile("小明", "male-qn-qingse")["revision"]
    svc.approve_profile("vp_小明", rev1)
    cur = svc.get_profile("vp_小明").revision()
    r = svc.update_profile("vp_小明", cur, provider_voice_id="")
    assert r["ok"] is True
    assert svc.readiness("s01_c01")["code"] == "provider_voice_id_missing"


def test_update_not_found(tmp_path):
    svc = make_svc(tmp_path)
    r = svc.update_profile("vp_幽灵", "", speed=1.0)
    assert r["ok"] is False and r["error"] == "not_found"


# ============ approve（§4 approval 门）============

def test_approve_gate_missing_voice_id(tmp_path):
    svc = make_svc(tmp_path)
    rev = svc.create_profile("小明", "")["revision"]        # draft 允许占位
    r = svc.approve_profile("vp_小明", rev)
    assert r["ok"] is False and r["error"] == "cannot_approve"
    assert svc.get_profile("vp_小明").approval_status == VOICE_APPROVAL_DRAFT


def test_approve_ok(tmp_path):
    svc = make_svc(tmp_path)
    rev = svc.create_profile("小明", "male-qn-qingse")["revision"]
    r = svc.approve_profile("vp_小明", rev)
    assert r["ok"] is True
    assert r["voice_profile"]["approval_status"] == VOICE_APPROVAL_APPROVED


def test_approve_stale_revision(tmp_path):
    svc = make_svc(tmp_path)
    svc.create_profile("小明", "male-qn-qingse")
    r = svc.approve_profile("vp_小明", "deadbeef")
    assert r["ok"] is False and r["error"] == "stale_revision"


# ============ store fail closed（visual_profile 同口径）============

def test_store_load_or_none_zero_side_effect(tmp_path):
    """纯读绝不创建文件（unspecified → None）。"""
    store = VoiceProfileStore(make_project(tmp_path))
    assert store.exists() is False
    assert store.load_or_none() is None
    assert store.path.exists() is False
    assert store.load_profiles() == {}


def test_store_bad_json_fail_closed(tmp_path):
    pdir = make_project(tmp_path)
    pdir.joinpath("output").mkdir(exist_ok=True)
    pdir.joinpath("output", "voice_profiles.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(VoiceSchemaError):
        VoiceProfileStore(pdir).load_or_none()


def test_store_unknown_schema_fail_closed(tmp_path):
    pdir = make_project(tmp_path)
    pdir.joinpath("output").mkdir(exist_ok=True)
    pdir.joinpath("output", "voice_profiles.json").write_text(
        json.dumps({"schema_version": 99, "profiles": {}}), encoding="utf-8")
    with pytest.raises(VoiceSchemaError):
        VoiceProfileStore(pdir).load_or_none()


def test_parse_voice_profile_missing_identity_fail_closed(tmp_path):
    with pytest.raises(VoiceSchemaError):
        parse_voice_profile({"schema_version": 1, "character_name": "小明"})


def test_profile_source_kind_invalid(tmp_path):
    with pytest.raises(VoiceSchemaError):
        CharacterVoiceProfile(voice_profile_id="vp_x", character_name="小明",
                              source_kind="magic")


def test_designed_cloned_are_legal_enum_values(tmp_path):
    """§4：designed/cloned 是合法枚举占位（不实现 API，但 schema 允许）。"""
    for kind in ("designed", "cloned"):
        p = CharacterVoiceProfile(voice_profile_id="vp_x", character_name="小明",
                                  source_kind=kind)
        assert p.source_kind == kind


# ============ 指纹纪律（actual-dependencies-only 的基础）============

def test_revision_covers_voice_semantics_only(tmp_path):
    a = CharacterVoiceProfile(voice_profile_id="vp_x", character_name="小明",
                              provider_voice_id="male-qn-qingse", speed=1.0)
    b = CharacterVoiceProfile(voice_profile_id="vp_x", character_name="小明",
                              provider_voice_id="male-qn-qingse", speed=1.3)
    c = CharacterVoiceProfile(voice_profile_id="vp_y", character_name="小红",
                              provider_voice_id="male-qn-qingse", speed=1.0)
    assert a.revision() != b.revision()       # 声音语义变 → revision 变 → STALE
    assert a.revision() == c.revision()       # 不同角色同声音参数 → 同指纹
                                              # （§22：无关角色绝不 false-stale 的前提）
