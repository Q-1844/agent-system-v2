# tests/test_prompt_freshness_37.py
# 37.md §1.1 — prompt 语义签名 / 单一可靠 mutation 路径 / multi 生成前自动重建。
#
# 背景（36.md §1.2.3 缺口，v1g 实证）：update_shot_fields / REST PUT 改字段后
# *_prompt_en 不自动失效重建 → 视频用旧剧本（s03 prompt_snapshot 仍是旧葡萄园
# motion 文本）。修复=三道闸：
#   A. 签名引擎（prompt_builder.prompt_semantic_sig / video_prompt_freshness）
#   B. REST PUT 与 update_shot_fields 同口径 invalidate + receipt + 自动签名
#   C. multi 生成前自动重建（_ensure_multi_video_prompt）+ adapter fail-closed 兜底
# 全程零真实外部调用（chat 用 stub，provider 不触）。
import asyncio
import hashlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import deps
from src.core.models import Shot
from src.core.prompt_builder import (
    build_video_prompt,
    prompt_semantic_sig,
    video_prompt_freshness,
)
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.storyboard.structure_service import update_shot_fields

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import make_pdir  # noqa: E402
from test_shot_binding_s3b2 import _bind, _build_scene  # noqa: E402
from test_shot_binding_s3c2 import (  # noqa: E402
    FakeMediaClient, _load_project, _load_shot, _set_video_ready,
    _StubPM,
)
from src.shot_binding import ShotBindingService  # noqa: E402


def _shot(**kw):
    s = Shot(shot_id="s01_c01", scene_id="s01", index=1, **kw)
    return s


# ============ A. 签名引擎 ============

def test_sig_stable_and_sensitive():
    """同字段同签名；字段变化 → 签名变化。"""
    a = _shot(motion="walk", environment_motion="wind")
    b = _shot(motion="walk", environment_motion="wind")
    assert prompt_semantic_sig(a, "video") == prompt_semantic_sig(b, "video")
    assert prompt_semantic_sig(a, "video") != ""
    c = _shot(motion="run", environment_motion="wind")
    assert prompt_semantic_sig(c, "video") != prompt_semantic_sig(a, "video")


def test_sig_description_in_video_not_last():
    """multi 缺口教训：description 进 video 签名；不影响 last 阶段签名。"""
    a = _shot(description="雨林奔跑")
    b = _shot(description="葡萄园漫步")
    assert prompt_semantic_sig(a, "video") != prompt_semantic_sig(b, "video")
    assert prompt_semantic_sig(a, "last") == prompt_semantic_sig(b, "last")


def test_sig_list_order_insensitive():
    """list 字段顺序无关（顺序无关改写不误判失效）。"""
    a = _shot(video_preserve_elements=["a", "b"])
    b = _shot(video_preserve_elements=["b", "a"])
    assert prompt_semantic_sig(a, "video") == prompt_semantic_sig(b, "video")


def test_sig_duration_and_relay_in_video():
    """时长 / 接力来源变化 → video 签名变化（它们决定视频语义）。"""
    a = _shot(target_duration_s=5.0)
    b = _shot(target_duration_s=8.0)
    assert prompt_semantic_sig(a, "video") != prompt_semantic_sig(b, "video")
    c = _shot(source_shot_id="s00_c01")
    assert prompt_semantic_sig(c, "video") != prompt_semantic_sig(_shot(), "video")


def test_freshness_all_states():
    """override/fresh/stale/untracked/missing 五态判定。"""
    s = _shot(description="x")
    assert video_prompt_freshness(s)["state"] == "missing"
    s.video_prompt_en = "old text"
    assert video_prompt_freshness(s)["state"] == "untracked"
    s.video_prompt_sig = prompt_semantic_sig(s, "video")
    assert video_prompt_freshness(s)["state"] == "fresh"
    s.description = "y"
    st = video_prompt_freshness(s)
    assert st["state"] == "stale" and st["recorded"] != st["sig"]
    s.video_prompt_override = "manual"
    assert video_prompt_freshness(s)["state"] == "override"


def test_update_shot_fields_clears_sig_with_en():
    """invalidation 清 en 的同时清签名（不留脏 sig）。"""
    p = SimpleNamespace()  # update_shot_fields 只需 project.get_shot
    s = _shot(motion="old", video_prompt_en="old en",
              video_prompt_sig="deadbeefdeadbeef")
    p.get_shot = lambda sid: s
    r = update_shot_fields(p, "s01_c01", {"motion": "new"})
    assert r["prompts_invalidated"]["video"] == "cleared"
    assert s.video_prompt_en == "" and s.video_prompt_sig == ""


# ============ B. REST PUT receipt / 自动签名 ============

PROJECT = "_test_37_freshness"


def _build_project(pname):
    from src.core.models import Project, Scene, StyleAnchor
    pm = deps.project_manager
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    sh = _shot(description="清晨土路", motion="fox walks", link_mode="multi")
    proj = Project(name=pname, style=StyleAnchor(aspect_ratio="16:9", size_tier="1K"),
                   scenes=[Scene(scene_id="s01", shots=[sh])])
    ProjectStore(pdir).save(proj)
    return pdir


@pytest.fixture
def proj37(client):
    _build_project(PROJECT)
    yield PROJECT
    shutil.rmtree(deps.project_manager.get_project_dir(PROJECT), ignore_errors=True)


def test_put_creative_change_receipt_and_sig(client, proj37):
    """PUT 改创作字段 → prompts_invalidated receipt；写 en 无 sig → 服务端算签名。"""
    c = client
    r = c.put(f"/api/project/{proj37}/shot/s01_c01",
              json={"motion": "fox runs fast", "video_prompt_en": "Fox runs."})
    assert r.status_code == 200, r.text
    body = r.json()
    # motion ∈ _VIDEO_FIELDS：新写的 video_prompt_en 不应被同一请求清掉（先失效后写入）
    assert body["prompts_invalidated"] == {}, body
    sigs = body.get("prompt_sigs_recorded") or {}
    assert sigs.get("video"), "video_prompt_en 写入应记录签名"
    assert sigs["video"] == prompt_semantic_sig(
        ProjectStore(deps.project_manager.get_project_dir(proj37)).load()
        .get_shot("s01_c01"), "video")


def test_put_field_change_invalidates_existing_en(client, proj37):
    """已有 en+sig 时再改字段 → 旧 en 失效（receipt cleared）。"""
    pdir = deps.project_manager.get_project_dir(proj37)
    c = client
    r = c.put(f"/api/project/{proj37}/shot/s01_c01",
              json={"motion": "walk", "video_prompt_en": "Walk.", "video_prompt_sig": "x"})
    assert r.status_code == 200
    r2 = c.put(f"/api/project/{proj37}/shot/s01_c01", json={"motion": "sprint"})
    body = r2.json()
    assert body["prompts_invalidated"].get("video") == "cleared"
    s = ProjectStore(pdir).load().get_shot("s01_c01")
    assert s.video_prompt_en == "" and s.video_prompt_sig == ""


def test_put_description_change_marks_video_sig_stale(client, proj37):
    """改 description（校准载体）→ video en 签名过期——multi 消费端可判 stale。"""
    pdir = deps.project_manager.get_project_dir(proj37)
    c = client
    r = c.put(f"/api/project/{proj37}/shot/s01_c01",
              json={"description": "新校准语义", "video_prompt_en": "Text.",
                    "video_prompt_sig": prompt_semantic_sig(
                        ProjectStore(pdir).load().get_shot("s01_c01"), "video")})
    assert r.status_code == 200
    s = ProjectStore(pdir).load().get_shot("s01_c01")
    assert video_prompt_freshness(s)["state"] == "stale"


# ============ C. rebuild 端点 / 自动重建 / adapter gate ============

class _StubChatClient:
    """只实现 chat 的 Agnes 替身（rebuild 走文本桶，不触 provider 视频接口）。"""

    def __init__(self, reply="Fox trots along the rainforest trail."):
        self.reply = reply
        self.chat_calls = []

    async def chat(self, messages, **kw):
        self.chat_calls.append(messages)
        return self.reply


@pytest.fixture
def venv37(tmp_path, monkeypatch):
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = asyncio.run(_build_scene(pdir))
    svc = ShotBindingService(pdir)
    fake = FakeMediaClient()
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    yield pdir, ids, svc, fake
    _clear_for_test()


def test_adapter_gate_multi_missing_en_with_desc_fails_closed(venv37):
    """multi + description 非空 + 无 en → fail closed（video_prompt_stale）。"""
    from src.generation.video_adapter import BoundGenerationError, build_bound_video_input
    pdir, ids, svc, fake = venv37
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")

    def mut(s):
        s.description = "狐狸沿雨林小径奔跑"   # multi 编译不带 description
    ProjectStore(pdir).update_shot("s01_c01", mut)
    with pytest.raises(BoundGenerationError) as ei:
        build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert ei.value.code == "video_prompt_stale"


def test_adapter_gate_multi_stale_en_ignored_not_shadowing(venv37):
    """multi + stale en → 旧 en 不 shadow：按字段重编译 + diagnostic。"""
    from src.generation.video_adapter import build_bound_video_input
    pdir, ids, svc, fake = venv37
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")

    def mut(s):
        s.video_prompt_en = "Stale grape text."
        s.video_prompt_sig = "0000000000000000"
    ProjectStore(pdir).update_shot("s01_c01", mut)
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    reasons = [d["reason"] for d in req.diagnostics]
    assert "video_prompt_en_stale_ignored" in reasons
    assert "Stale grape text." not in req.prompt


def test_adapter_gate_multi_fresh_en_used(venv37):
    """multi + fresh en → en 正常进入 prompt + freshness diagnostic。"""
    from src.generation.video_adapter import build_bound_video_input
    pdir, ids, svc, fake = venv37
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")

    def mut(s):
        s.video_prompt_en = "Fresh fox run text."
        s.video_prompt_sig = prompt_semantic_sig(s, "video")
    ProjectStore(pdir).update_shot("s01_c01", mut)
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert "Fresh fox run text." in req.prompt
    reasons = [d["reason"] for d in req.diagnostics]
    assert "video_prompt_freshness" in reasons


def test_legacy_i2v_prompt_priority_unchanged():
    """legacy i2v：fresh en 优先于字段编译；untracked en 保持既有行为（en 胜出）。"""
    s = _shot(motion="field motion", video_prompt_en="Legacy en text.")
    assert build_video_prompt(s, "i2v") == "Legacy en text."
    s2 = _shot(motion="field motion")
    assert "Animate this image: field motion" in build_video_prompt(s2, "i2v")


def test_ensure_multi_video_prompt_rebuild_and_fresh_skip(venv37, monkeypatch):
    """_ensure_multi_video_prompt：fresh 不调 chat；missing+desc 自动重建并落签名。"""
    from routers.shots import _ensure_multi_video_prompt
    pdir, ids, svc, fake = venv37
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")

    async def _emit(stage, msg, pct=None):
        pass

    # missing + 无 description → 字段编译已覆盖，不重建
    rec = asyncio.run(
        _ensure_multi_video_prompt(pdir.name, "s01_c01", _emit))
    assert rec["rebuilt"] is False and rec["freshness_before"] == "missing"

    # missing + description 非空 → 自动重建（stub chat）
    ProjectStore(pdir).update_shot(
        "s01_c01", lambda s: setattr(s, "description", "狐狸奔跑校准语义"))
    stub = _StubChatClient()
    monkeypatch.setattr(deps, "agnes_client", stub)
    rec = asyncio.run(
        _ensure_multi_video_prompt(pdir.name, "s01_c01", _emit))
    assert rec["rebuilt"] is True and rec["freshness_before"] == "missing"
    assert len(stub.chat_calls) == 1
    s = _load_shot(pdir)
    assert s.video_prompt_en == stub.reply
    assert s.video_prompt_sig == prompt_semantic_sig(s, "video")
    assert rec["prompt_hash"] == hashlib.sha256(stub.reply.encode()).hexdigest()[:16]
    # 再跑一次 → fresh，不再调 chat
    rec = asyncio.run(
        _ensure_multi_video_prompt(pdir.name, "s01_c01", _emit))
    assert rec["rebuilt"] is False and rec["freshness_before"] == "fresh"
    assert len(stub.chat_calls) == 1


def test_ensure_multi_video_prompt_empty_reply_fail_closed(venv37, monkeypatch):
    """自动重建返回空文本 → fail closed（RuntimeError），绝不带旧语义提交。"""
    from routers.shots import _ensure_multi_video_prompt
    pdir, ids, svc, fake = venv37
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")
    ProjectStore(pdir).update_shot(
        "s01_c01", lambda s: setattr(s, "description", "校准语义"))

    async def _emit(stage, msg, pct=None):
        pass
    monkeypatch.setattr(deps, "agnes_client", _StubChatClient(reply=""))
    with pytest.raises(RuntimeError):
        asyncio.run(
            _ensure_multi_video_prompt(pdir.name, "s01_c01", _emit))


def test_prompt_rebuild_endpoint_receipt(client, proj37, monkeypatch):
    """POST /prompt/rebuild：first+video 重建落库 + receipt hash/sig。"""
    stub = _StubChatClient(reply="Rebuilt prompt text.")
    monkeypatch.setattr(deps, "agnes_client", stub)
    r = client.post(f"/api/project/{proj37}/shot/s01_c01/prompt/rebuild", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["stages"].keys()) == {"first", "video"}
    for st, res in body["stages"].items():
        assert res["ok"] is True, res
        assert res["prompt"] == stub.reply
        assert res["prompt_hash"] == hashlib.sha256(
            stub.reply.encode()).hexdigest()[:16]
    s = ProjectStore(deps.project_manager.get_project_dir(proj37)).load().get_shot("s01_c01")
    assert s.prompt_en == stub.reply and s.video_prompt_en == stub.reply
    assert s.first_frame_prompt_sig and s.video_prompt_sig


def test_prompt_rebuild_endpoint_invalid_stage(client, proj37):
    r = client.post(f"/api/project/{proj37}/shot/s01_c01/prompt/rebuild",
                    json={"stages": ["bogus"]})
    assert r.status_code == 400
