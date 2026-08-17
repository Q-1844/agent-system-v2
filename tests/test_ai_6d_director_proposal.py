"""AI-6D Director Proposal Runtime 测试（§24 focused verification）。

engine 级 + 编排级：直接驱动 director_proposal 模块函数（generate_proposal /
preview_selected / apply_proposal）+ AgentRuntime 权限门。不真调 Agnes——
注入 FakeAgnes（async chat 返回 canned JSON），对齐 tests/mock_agnes 思路。
每个 case 用 tmp_path 独立项目。
"""
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, Character
from src.core.store import ProjectStore
from src.revision.workspace import WorkspaceStore
from src.revision.changeset import ChangeSetError
from src.revision.models import creative_source_signature, OpType as OT
from src.agents.proposal_store import ProposalStore
from src.agents.director_proposal import (
    DirectorProposalItem, ProposalAction, ProposalScope, ProposalStatus,
    ProposalValidationError, ProposalGenerationError,
    parse_and_validate, compile_items, generate_proposal, preview_selected,
    apply_proposal, proposal_to_api, build_director_context,
)
from src.agents.runtime import AgentRuntime, AgentError


# ============ 辅助 ============
def _proj():
    """3-shot canonical 项目（对齐 test_workspaces._proj）：s01_c01/c02/c03，角色 Alice。"""
    return Project(name="t", characters=[Character(name="Alice", description_en="a woman")],
                   scenes=[Scene(scene_id="s01", summary="Alice sees rain", characters=["Alice"],
                                  shots=[
                                      Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                           description="old", target_duration_s=8, characters=["Alice"]),
                                      Shot(shot_id="s01_c02", scene_id="s01", index=2,
                                           description="b", target_duration_s=5, characters=["Alice"]),
                                      Shot(shot_id="s01_c03", scene_id="s01", index=3,
                                           description="c", target_duration_s=5, characters=["Alice"]),
                                  ])])


def _setup(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    ws_store = WorkspaceStore(tmp_path, "t", lambda: ProjectStore(tmp_path).load())
    pstore = ProposalStore(tmp_path, "t")
    return ws_store, pstore, (lambda: ProjectStore(tmp_path).load())


VALID_JSON = json.dumps({
    "title": "Pacing", "summary": "tighten the scene",
    "items": [
        {"action": "update_shot", "shot_id": "s01_c02", "description": "new b",
         "target_duration_s": 3, "reason": "tighter"},
        {"action": "insert_shot", "scene_id": "s01", "after_shot_id": "s01_c02",
         "description": "reaction", "characters": ["Alice"],
         "target_duration_s": 2, "reason": "beat"},
        {"action": "delete_shot", "shot_id": "s01_c03", "reason": "cut"},
    ]})


class FakeAgnes:
    """async chat；按顺序返回 canned 字符串。callable 项可基于 messages 动态生成。"""

    def __init__(self, responses):
        self._responses = list(responses) if isinstance(responses, (list, tuple)) else [responses]
        self.calls = []

    async def chat(self, messages, *, temperature=0.7, max_tokens=2048,
                   json_mode=False, thinking=False):
        self.calls.append(messages)
        if not self._responses:
            raise RuntimeError("FakeAgnes: no more responses")
        r = self._responses.pop(0)
        return r(messages) if callable(r) else r


def _run(coro):
    return asyncio.run(coro)


# ============ 编译器（§7/§13）============
def test_compile_update_before_from_working(tmp_path):
    ws_store, _ps, loader = _setup(tmp_path)
    ws = ws_store.create("exp")
    working = ws_store.load_working_project(ws.workspace_id)
    items = [DirectorProposalItem(item_id="i1", action=ProposalAction.UPDATE_SHOT.value,
                                   scene_id="s01", shot_id="s01_c02",
                                   description="new b", target_duration_s=3)]
    ops = compile_items(items, ws_store, ws, working)
    assert len(ops) == 1 and ops[0].op_type == OT.UPDATE_SHOT.value
    # before 来自 working（=canonical 快照），不是 LLM
    assert ops[0].description.before == "b"
    assert ops[0].description.after == "new b"
    assert ops[0].target_duration_s.before == 5
    assert ops[0].target_duration_s.after == 3


def test_compile_insert_after_emits_insert_plus_reorder(tmp_path):
    """§7：位置 insert -> INSERT + REORDER（无 delete 时自然顺序不满足位置）。"""
    ws_store, _ps, _ = _setup(tmp_path)
    ws = ws_store.create("exp")
    working = ws_store.load_working_project(ws.workspace_id)
    items = [DirectorProposalItem(item_id="i1", action=ProposalAction.INSERT_SHOT.value,
                                   scene_id="s01", after_shot_id="s01_c02",
                                   description="reaction", characters=["Alice"],
                                   target_duration_s=2)]
    ops = compile_items(items, ws_store, ws, working)
    kinds = [o.op_type for o in ops]
    assert OT.INSERT_SHOT.value in kinds and OT.REORDER_SHOTS.value in kinds
    ins = next(o for o in ops if o.op_type == OT.INSERT_SHOT.value)
    assert ins.new_shot.shot_id == "s01_c04"   # 服务端分配稳定 id
    reo = next(o for o in ops if o.op_type == OT.REORDER_SHOTS.value)
    assert reo.ordered_shot_ids == ["s01_c01", "s01_c02", "s01_c04", "s01_c03"]


def test_compile_delete(tmp_path):
    ws_store, _ps, _ = _setup(tmp_path)
    ws = ws_store.create("exp")
    working = ws_store.load_working_project(ws.workspace_id)
    ops = compile_items([DirectorProposalItem(
        item_id="i1", action=ProposalAction.DELETE_SHOT.value,
        scene_id="s01", shot_id="s01_c03")], ws_store, ws, working)
    assert len(ops) == 1 and ops[0].op_type == OT.DELETE_SHOT.value and ops[0].shot_id == "s01_c03"


def test_compile_reorder(tmp_path):
    ws_store, _ps, _ = _setup(tmp_path)
    ws = ws_store.create("exp")
    working = ws_store.load_working_project(ws.workspace_id)
    ops = compile_items([DirectorProposalItem(
        item_id="i1", action=ProposalAction.REORDER_SHOT.value,
        scene_id="s01", shot_id="s01_c03", after_shot_id="s01_c01")], ws_store, ws, working)
    assert len(ops) == 1 and ops[0].op_type == OT.REORDER_SHOTS.value
    assert ops[0].ordered_shot_ids == ["s01_c01", "s01_c03", "s01_c02"]


def test_compile_subset_fewer_ops(tmp_path):
    """§13：子集重编译只含被选 item 的 op，且 insert 分配新 id（不回收计数器）。"""
    ws_store, _ps, _ = _setup(tmp_path)
    ws = ws_store.create("exp")
    working = ws_store.load_working_project(ws.workspace_id)
    intent = parse_and_validate(VALID_JSON, _proj(), "scene", "s01")
    full = compile_items(intent.items, ws_store, ws, working)
    full_ins_id = next(o.new_shot.shot_id for o in full if o.op_type == OT.INSERT_SHOT.value)
    # 只选 update + insert（去掉 delete）
    sel = [it for it in intent.items if it.action != ProposalAction.DELETE_SHOT.value]
    sub = compile_items(sel, ws_store, ws, working)
    sub_ins_id = next(o.new_shot.shot_id for o in sub if o.op_type == OT.INSERT_SHOT.value)
    assert len(sub) <= len(full)
    assert sub_ins_id != full_ins_id   # 新分配 id（计数器只增）


# ============ 解析 + 校验（§9）============
def test_parse_malformed_rejected(tmp_path):
    with pytest.raises(ProposalValidationError):
        parse_and_validate("not json {{{", _proj(), "scene", "s01")


def test_parse_out_of_scope_shot_rejected(tmp_path):
    bad = json.dumps({"items": [{"action": "update_shot", "shot_id": "s99_c01",
                                  "description": "x"}]})
    with pytest.raises(ProposalValidationError, match="不在故事段内|shot_id"):
        parse_and_validate(bad, _proj(), "scene", "s01")


def test_parse_cross_scene_rejected(tmp_path):
    bad = json.dumps({"items": [{"action": "insert_shot", "scene_id": "s99",
                                  "description": "x"}]})
    with pytest.raises(ProposalValidationError, match="跨故事段"):
        parse_and_validate(bad, _proj(), "scene", "s01")


def test_parse_chars_not_in_cast_filtered(tmp_path):
    """非法角色被过滤（安全：不让非法角色进 changeset）。"""
    raw = json.dumps({"items": [{"action": "update_shot", "shot_id": "s01_c02",
                                  "characters": ["Alice", "Bob"]}]})
    intent = parse_and_validate(raw, _proj(), "scene", "s01")
    assert intent.items[0].characters == ["Alice"]   # Bob 被滤掉


def test_parse_invented_insert_id_ignored(tmp_path):
    """§2/§9：LLM 不得发明 insert shot_id；即便给也忽略，服务端 allocate。"""
    raw = json.dumps({"items": [{"action": "insert_shot", "scene_id": "s01",
                                  "shot_id": "FAKE_ID", "description": "x"}]})
    intent = parse_and_validate(raw, _proj(), "scene", "s01")
    assert intent.items[0].shot_id is None   # 被清空，编译时 allocate


def test_parse_contradictory_delete_update_rejected(tmp_path):
    bad = json.dumps({"items": [
        {"action": "delete_shot", "shot_id": "s01_c02"},
        {"action": "update_shot", "shot_id": "s01_c02", "description": "x"}]})
    with pytest.raises(ProposalValidationError, match="矛盾"):
        parse_and_validate(bad, _proj(), "scene", "s01")


def test_parse_after_deleted_anchor_rejected(tmp_path):
    bad = json.dumps({"items": [
        {"action": "delete_shot", "shot_id": "s01_c02"},
        {"action": "insert_shot", "scene_id": "s01", "after_shot_id": "s01_c02",
         "description": "x"}]})
    with pytest.raises(ProposalValidationError, match="位置锚点.*被.*删除|矛盾"):
        parse_and_validate(bad, _proj(), "scene", "s01")


def test_parse_empty_rejected(tmp_path):
    with pytest.raises(ProposalValidationError, match="为空"):
        parse_and_validate(json.dumps({"items": []}), _proj(), "scene", "s01")


def test_parse_server_assigns_item_ids(tmp_path):
    """§6：item_id 服务端生成，LLM 不发明。"""
    intent = parse_and_validate(VALID_JSON, _proj(), "scene", "s01")
    ids = [it.item_id for it in intent.items]
    assert all(i.startswith("item_") for i in ids)
    assert len(ids) == len(set(ids))   # 唯一


# ============ 权限门（§3）============
def _runtime(tmp_path, fake=None):
    return AgentRuntime(client=fake, settings_path=tmp_path / "s.json",
                        project_dir_resolver=lambda name: tmp_path)


def test_permission_non_director_rejected(tmp_path):
    """§24：unauthorized Agent 不能执行 storyboard.propose_change。"""
    rt = _runtime(tmp_path)
    with pytest.raises(AgentError) as ei:
        asyncio.run(rt.propose_storyboard_change("t", "editor", "scene", "s01", "x"))
    assert ei.value.code == "permission_denied"


def test_permission_director_execute_allowed_by_registry(tmp_path):
    rt = _runtime(tmp_path)
    assert rt.capabilities.can_execute("director", "storyboard.propose_change", "execute")
    assert not rt.capabilities.can_execute("director", "storyboard.propose_change", "inspect")


# ============ 编排 E2E（§11/§24）============
def test_generate_proposal_valid_creates_workspace_and_preview(tmp_path):
    ws_store, pstore, loader = _setup(tmp_path)
    canon_sig = creative_source_signature(loader().to_dict())
    proposal, pv = _run(generate_proposal(FakeAgnes([VALID_JSON]), loader, ws_store, pstore,
                                          "scene", "s01", "tighten pacing"))
    assert proposal.workspace_id and proposal.full_preview_changeset_id == pv["changeset_id"]
    assert proposal.status == ProposalStatus.CREATED.value
    assert len(proposal.items) == 3
    assert pv["changeset_id"].startswith("cs_")
    # workspace 已创建
    ws = ws_store.load(proposal.workspace_id)
    assert ws.name.startswith("Director · s01 ·")
    # 🔴 canonical 未变（§21）——preview 不写 canonical
    assert creative_source_signature(loader().to_dict()) == canon_sig
    # changeset 持久化可加载
    assert ws_store.load_changeset(proposal.workspace_id, pv["changeset_id"])


def test_generate_proposal_invalid_after_repair_no_workspace(tmp_path):
    """§11/§24：malformed 输出（repair 后仍非法）不能创建 workspace、不能动 canonical。"""
    ws_store, pstore, loader = _setup(tmp_path)
    canon_sig = creative_source_signature(loader().to_dict())
    bad = "not json {{{"
    with pytest.raises(ProposalGenerationError):
        _run(generate_proposal(FakeAgnes([bad, bad]), loader, ws_store, pstore,
                               "scene", "s01", "x"))
    # 没有留垃圾 workspace
    assert ws_store.list() == []
    assert creative_source_signature(loader().to_dict()) == canon_sig


def test_generate_proposal_repair_succeeds(tmp_path):
    """第一次非法、repair 后合法 -> 成功。"""
    ws_store, pstore, loader = _setup(tmp_path)
    fake = FakeAgnes(["not json {{{", VALID_JSON])
    proposal, _pv = _run(generate_proposal(fake, loader, ws_store, pstore,
                                            "scene", "s01", "x"))
    assert proposal.full_preview_changeset_id
    assert len(fake.calls) == 2   # 第一次非法 -> 触发一次 repair


def test_preview_selected_creates_new_changeset(tmp_path):
    """§13：子集再预览 -> 全新 changeset_id（≠ full）；diff 反映子集。"""
    ws_store, pstore, loader = _setup(tmp_path)
    proposal, full_pv = _run(generate_proposal(FakeAgnes([VALID_JSON]), loader, ws_store, pstore,
                                                "scene", "s01", "x"))
    selected = [it.item_id for it in proposal.items
                if it.action != ProposalAction.DELETE_SHOT.value]   # update + insert
    api = preview_selected(proposal, selected, ws_store, pstore)
    assert api["changeset"]["changeset_id"] != full_pv["changeset_id"]
    assert api["status"] == ProposalStatus.SELECTED_PREVIEWED.value
    assert api["changeset"]["changeset_id"] == proposal.selected_preview_changeset_id


def test_apply_canonical_unchanged(tmp_path):
    """§21 定义不变量：propose → preview-selected → apply 后 working 变、canonical 不变。"""
    ws_store, pstore, loader = _setup(tmp_path)
    canon_sig = creative_source_signature(loader().to_dict())
    proposal, _pv = _run(generate_proposal(FakeAgnes([VALID_JSON]), loader, ws_store, pstore,
                                           "scene", "s01", "x"))
    selected = [it.item_id for it in proposal.items
                if it.action != ProposalAction.DELETE_SHOT.value]
    api = preview_selected(proposal, selected, ws_store, pstore)
    cid = api["changeset"]["changeset_id"]
    res = apply_proposal(proposal, cid, ws_store, pstore)
    assert res["applied"] is True
    assert proposal.status == ProposalStatus.APPLIED.value
    # working 变了（c02 description -> new b）
    wp = ws_store.load_working_project(proposal.workspace_id)
    assert wp.get_shot("s01_c02").description == "new b"
    # 🔴 canonical 签名不变
    assert creative_source_signature(loader().to_dict()) == canon_sig


def test_stale_workspace_revision_conflict(tmp_path):
    """§17/§24：workspace revision 变 -> apply 冲突而非静默应用。"""
    ws_store, pstore, loader = _setup(tmp_path)
    proposal, _pv = _run(generate_proposal(FakeAgnes([VALID_JSON]), loader, ws_store, pstore,
                                           "scene", "s01", "x"))
    cid = proposal.full_preview_changeset_id
    # 外部改动 workspace revision
    ws = ws_store.load(proposal.workspace_id)
    ws.revision += 1
    ws_store.save_workspace(ws)
    with pytest.raises(ChangeSetError) as ei:
        apply_proposal(proposal, cid, ws_store, pstore)
    assert ei.value.code in ("WORKSPACE_CHANGED", "STALE", "CONFLICT")


def test_preview_selected_stale_returns_stale_dict(tmp_path):
    ws_store, pstore, loader = _setup(tmp_path)
    proposal, _pv = _run(generate_proposal(FakeAgnes([VALID_JSON]), loader, ws_store, pstore,
                                           "scene", "s01", "x"))
    ws = ws_store.load(proposal.workspace_id)
    ws.revision += 1
    ws_store.save_workspace(ws)
    api = preview_selected(proposal, [it.item_id for it in proposal.items], ws_store, pstore)
    assert api.get("stale") is True


def test_refresh_restore_via_runtime(tmp_path):
    """§16/§24：刷新恢复——持久化 Proposal 可经 runtime.get_director_proposal 重建。"""
    ws_store, pstore, loader = _setup(tmp_path)
    proposal, _pv = _run(generate_proposal(FakeAgnes([VALID_JSON]), loader, ws_store, pstore,
                                           "scene", "s01", "x"))
    rt = _runtime(tmp_path)
    restored = rt.get_director_proposal("t", proposal.proposal_id)
    assert restored is not None
    assert restored["proposal_id"] == proposal.proposal_id
    assert len(restored["items"]) == 3
    assert restored["changeset"]["changeset_id"] == proposal.full_preview_changeset_id
    assert "base_is_stale" in restored


def test_list_proposals_by_scope(tmp_path):
    ws_store, pstore, loader = _setup(tmp_path)
    _run(generate_proposal(FakeAgnes([VALID_JSON]), loader, ws_store, pstore,
                           "scene", "s01", "x"))
    rt = _runtime(tmp_path)
    lst = rt.list_director_proposals("t", "scene", "s01")
    assert len(lst) == 1 and lst[0]["scope_id"] == "s01"


# ============ canonical 字节级不变量（§21 加严）============
def test_canonical_project_json_bytes_unchanged(tmp_path):
    """§21：全流程后 canonical project.json 字节不变。"""
    ws_store, pstore, loader = _setup(tmp_path)
    from pathlib import Path
    canon_path = tmp_path / "output" / "project.json"
    before = canon_path.read_bytes()
    proposal, _pv = _run(generate_proposal(FakeAgnes([VALID_JSON]), loader, ws_store, pstore,
                                           "scene", "s01", "x"))
    selected = [it.item_id for it in proposal.items
                if it.action != ProposalAction.DELETE_SHOT.value]
    api = preview_selected(proposal, selected, ws_store, pstore)
    apply_proposal(proposal, api["changeset"]["changeset_id"], ws_store, pstore)
    assert canon_path.read_bytes() == before


# ============ 上下文（§5）============
def test_build_context_excludes_assets_and_other_scenes(tmp_path):
    """§5：上下文不含 base64/URL/签名/其它 scene，只含创作字段+生成态。"""
    ProjectStore(tmp_path).save(_proj())
    ctx = build_director_context(_proj(), "shot", "s01_c02")
    blob = json.dumps(ctx, ensure_ascii=False)
    assert "signature" not in blob and "workspace" not in blob
    assert ctx["scope"]["scene_id"] == "s01"
    assert ctx["selected_shot"] == "s01_c02"
    assert ctx["neighbors"]["prev"] == "s01_c01" and ctx["neighbors"]["next"] == "s01_c03"
    # shots 只含创作字段+生成态
    for sh in ctx["shots"]:
        assert set(sh.keys()) <= {"shot_id", "index", "description", "characters",
                                   "target_duration_s", "status",
                                   "first_frame_status", "video_status", "has_final_video"}
