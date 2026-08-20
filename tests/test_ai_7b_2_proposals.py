# tests/test_ai_7b_2_proposals.py
# AI-7B-2 Script / Character / Storyboard 提案运行时 focused Gate（§16-§21/§25/§103）。
#
# 不联网（ScriptedProposalClient 按 system prompt 角色标记分派 JSON）。每 case 独立 tmp_path。
# 覆盖：① Script 语义提案（§17，无 before/revision/op）② Character 外貌→description_en only
# （§20，不碰 views/assets）+ 改名不支持（§19，decision 或 action 拒）③ Storyboard Team 路径
# 不建 Workspace（§21）④ generate_semantic_proposal 不建 Workspace（§21 抽取）⑤ resume 跑首轮
# 提案并行 + 0 mutation ⑥ 部分失败→blocked（§103），独立结算（§25）。
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.change_protocol import service as cps
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import ChangeRequest
from src.agents.change_protocol.script_proposal import run_script_proposal
from src.agents.change_protocol.character_proposal import run_character_proposal
from src.agents.change_protocol.storyboard_proposal import run_storyboard_proposal
from src.agents.director_proposal import generate_semantic_proposal


def _proj(name="demo"):
    return Project(
        name=name, style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="short dark hair, gray coat"),
                    Character(name="Aran", description_en="a hero")],
        scenes=[Scene(scene_id="s01", summary="meet at the gate", characters=["Xiaoyu", "Aran"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="see the gate", characters=["Xiaoyu"],
                                  target_duration_s=4)])],
        story_text="Xiaoyu meets Aran at the gate.")


def _rt(tmp_path, client=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


class ScriptedProposalClient:
    """按 system prompt 角色标记分派 JSON。顺序 Script Writer→Character Agent→Director
    （Script Writer prompt 提到 'Character Agent'，故必须先判 Script Writer）。fail_module
    模拟指定模块 Agnes 失败（§103 部分失败）。"""

    def __init__(self, script=None, character=None, director=None, fail_module=None):
        self.script = script or {"title": "剧本修改", "summary": "改结局",
            "items": [{"action": "update_scene", "scene_id": "s01",
                       "new_summary": "Aran leaves the city alone", "reason": "ending change"}]}
        self.character = character or {"title": "角色修改", "summary": "改外观",
            "items": [{"action": "update_character", "name": "Xiaoyu",
                       "description_en": "long black hair, gray coat", "reason": "appearance"}],
            "user_decisions_needed": []}
        self.director = director or {"title": "分镜修改", "summary": "加快节奏",
            "items": [{"action": "update_shot", "shot_id": "s01_c01",
                       "description": "fast tense shot", "characters": ["Xiaoyu"],
                       "target_duration_s": 3, "reason": "faster"}]}
        self.fail_module = fail_module
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        sys = messages[0]["content"] if messages else ""
        if "Script Writer" in sys:
            module = "script"; payload = self.script
        elif "Character Agent" in sys:
            module = "character"; payload = self.character
        elif "Director" in sys:
            module = "storyboard"; payload = self.director
        else:
            return json.dumps({"items": []}, ensure_ascii=False)
        if self.fail_module == module:
            raise Exception(f"simulated agnes failure for {module}")
        return json.dumps(payload, ensure_ascii=False)


def _make_cr(rt, project, request_text):
    out = cps.create_session(rt, project, request_text)
    return out["session"]["session_id"], ChangeRequest.from_dict(out["request"])


# ============ ① Script 语义提案（§17）============
def test_script_proposal_semantic_no_mutation(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient()
    rt = _rt(tmp_path, client=client)
    sid, cr = _make_cr(rt, "demo", "修改结局，让 Aran 离开城市")
    before = (tmp_path / "output" / "project.json").read_bytes()
    mp = asyncio.run(run_script_proposal(rt, "demo", sid, cr, cr.source_snapshot_signature, client=client))
    assert mp.status == "completed" and mp.module == "script"
    assert len(mp.items) >= 1
    item = mp.items[0]
    assert item["action"] == "update_scene"
    assert item["item_id"].startswith("item_")          # §14 服务端分配
    assert item["intent"]["scene_id"] == "s01"
    # §17：语义意图无 before/revision/op_type/ChangeOperation
    for bad in ("before", "after", "revision", "op_type", "operations", "changeset_id"):
        assert bad not in item["intent"]
    # AgentTask 持久化 + 结算
    tasks = rt._task_store("demo").list(target_agent="script_writer")
    assert any(t.capability_id == "story.propose_change" and t.operation == "propose"
               and t.status == "completed" for t in tasks)
    # 0 mutation
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not (tmp_path / "workspaces").exists()


# ============ ② Character 外貌→description_en only（§20）============
def test_character_appearance_description_only(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient()
    rt = _rt(tmp_path, client=client)
    sid, cr = _make_cr(rt, "demo", "把 Xiaoyu 的头发改长")
    before = (tmp_path / "output" / "project.json").read_bytes()
    mp = asyncio.run(run_character_proposal(rt, "demo", sid, cr, cr.source_snapshot_signature, client=client))
    assert mp.status == "completed" and mp.module == "character"
    item = mp.items[0]
    assert item["action"] == "update_character"
    assert item["intent"]["name"] == "Xiaoyu"
    assert item["intent"]["description_en"] == "long black hair, gray coat"
    # §20：外貌变更只改 description_en；intent 绝不含 views/assets/frames/video
    for bad in ("views", "first_frame", "last_frame", "video", "audio", "assets"):
        assert bad not in item["intent"]
    assert (tmp_path / "output" / "project.json").read_bytes() == before


# ============ ②b Character 改名不支持（§19）============
def test_character_rename_unsupported_decision(tmp_path):
    """§19：改名请求 → LLM 返回 user_decisions_needed（不发明 rename 动作）。"""
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient(character={
        "title": "改名", "summary": "改名不支持", "items": [],
        "user_decisions_needed": ["改名不支持（name 为身份），请确认是否保留旧名仅改描述，或新建角色"]})
    rt = _rt(tmp_path, client=client)
    sid, cr = _make_cr(rt, "demo", "把 Xiaoyu 改名成 Yue")
    mp = asyncio.run(run_character_proposal(rt, "demo", sid, cr, cr.source_snapshot_signature, client=client))
    assert mp.status == "completed"
    assert mp.items == []
    assert mp.user_decisions_needed and "改名" in mp.user_decisions_needed[0]


def test_character_rename_action_rejected(tmp_path):
    """§19：LLM 误发明 rename_character 动作 → 解析拒 → 一次 repair 仍拒 → failed proposal。"""
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient(character={
        "title": "rename", "summary": "rename",
        "items": [{"action": "rename_character", "name": "Xiaoyu", "new_name": "Yue", "reason": "rename"}]})
    rt = _rt(tmp_path, client=client)
    sid, cr = _make_cr(rt, "demo", "把 Xiaoyu 改名成 Yue")
    mp = asyncio.run(run_character_proposal(rt, "demo", sid, cr, cr.source_snapshot_signature, client=client))
    assert mp.status == "failed"          # rename 非法 action，repair 仍非法
    assert mp.blockers


# ============ ③ Storyboard Team 路径不建 Workspace（§21）============
def test_storyboard_team_no_workspace(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient()
    rt = _rt(tmp_path, client=client)
    sid, cr = _make_cr(rt, "demo", "让场景 s01 的分镜节奏更快")
    before = (tmp_path / "output" / "project.json").read_bytes()
    mp = asyncio.run(run_storyboard_proposal(rt, "demo", sid, cr, cr.source_snapshot_signature,
                                              "scene", "s01", client=client))
    assert mp.status == "completed" and mp.module == "storyboard"
    assert len(mp.items) >= 1
    assert mp.items[0]["action"] == "update_shot"
    assert mp.items[0]["intent"]["shot_id"] == "s01_c01"
    # §21：Team 路径绝不建 Director Workspace / 不落 DirectorProposal
    assert not (tmp_path / "workspaces").exists()
    assert (tmp_path / "output" / "project.json").read_bytes() == before


# ============ ④ generate_semantic_proposal 不建 Workspace（§21 抽取）============
def test_generate_semantic_proposal_no_workspace(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient()
    rt = _rt(tmp_path, client=client)
    before = (tmp_path / "output" / "project.json").read_bytes()
    intent = asyncio.run(generate_semantic_proposal(
        client, rt._project_loader("demo"), "scene", "s01", "让 s01 更快"))
    assert intent.items and intent.items[0].action in ("update_shot", "insert_shot",
                                                          "delete_shot", "reorder_shot")
    # §21：语义生成不建 workspace / 不 persist DirectorProposal / 不写 canonical
    assert not (tmp_path / "workspaces").exists()
    assert (tmp_path / "output" / "project.json").read_bytes() == before


# ============ ⑤ resume 跑首轮提案并行 + 0 mutation（§24/§25）============
def test_resume_runs_first_round_no_mutation(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient()
    rt = _rt(tmp_path, client=client)
    sid, _cr = _make_cr(rt, "demo", "把 Xiaoyu 头发改长并修改结局")  # character + script, 无 scene→Director 延后
    before = (tmp_path / "output" / "project.json").read_bytes()
    s = asyncio.run(cps.resume_session(rt, "demo", sid))
    assert s["status"] == "analyzing_dependencies"   # 7B-3 接通下一阶段
    assert len(s["module_proposal_ids"]) >= 2
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    props = store.list_proposals(sid)
    assert {p.module for p in props} >= {"script", "character"}
    assert all(p.status == "completed" for p in props)
    # 0 mutation：canonical / Workspace / Production Plan 全未碰
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not (tmp_path / "workspaces").exists()
    assert not (tmp_path / "output" / "ai" / "production_plans.json").exists()


# ============ ⑥ 部分失败→blocked，独立结算（§103/§25）============
def test_resume_partial_failure_blocked(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient(fail_module="character")   # 仅 character 失败
    rt = _rt(tmp_path, client=client)
    sid, _cr = _make_cr(rt, "demo", "把 Xiaoyu 头发改长并修改结局")
    s = asyncio.run(cps.resume_session(rt, "demo", sid))
    assert s["status"] == "blocked"                    # §103
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    statuses = {p.module: p.status for p in store.list_proposals(sid)}
    assert statuses.get("script") == "completed"       # §25：独立结算，一个失败不拖垮其他
    assert statuses.get("character") == "failed"


# ============ ⑦ 幂等：resume 不重跑已完成 round-1 提案（§25/§87）============
def test_resume_idempotent_no_rerun(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient()
    rt = _rt(tmp_path, client=client)
    sid, _cr = _make_cr(rt, "demo", "把 Xiaoyu 头发改长并修改结局")
    asyncio.run(cps.resume_session(rt, "demo", sid))   # round 1 → analyzing_dependencies
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    r1_script = [p for p in store.list_proposals(sid) if p.module == "script"]
    r1_char = [p for p in store.list_proposals(sid) if p.module == "character"]
    # 再次 resume → 编译（7B-3）；可能触发 Director round 2（合法），但不重跑 script/character round 1
    s2 = asyncio.run(cps.resume_session(rt, "demo", sid))
    assert s2["status"] == "validating_plan"
    r2_script = [p for p in store.list_proposals(sid) if p.module == "script"]
    r2_char = [p for p in store.list_proposals(sid) if p.module == "character"]
    assert len(r2_script) == len(r1_script)            # §25：已完成 round-1 不重跑
    assert len(r2_char) == len(r1_char)
