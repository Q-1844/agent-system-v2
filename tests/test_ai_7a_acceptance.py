"""AI-7A §89~§97 + §101 总验收（7A-5）。

每阶段已有 focused Gate；本文件只做**跨切面总验收**——把分散在各阶段 Gate 的硬不变量
放在一起，再加 §97 大项目上下文有界测试（catch context explosion）。§98 浏览器手动验收
见文件末注释清单（无法在本环境跑浏览器）。

Gate 覆盖：§97 大项目有界检视（不把整项目塞进一次 run）/ §91 跨模块变更硬不变量聚合
（execution_ready False、0 ChangeOperation、0 Workspace、0 Plan 修改、0 job、全只读 task、
canonical 不变、0 真 Agnes 生成调用）。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor, VideoVersion
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.inquiry import run_inquiry, classify_request, ProjectContextBuilder
from src.agents.team_chat import TeamChatStore, TeamMessage, run_team_inquiry_and_persist
from src.agents.change_analysis import (create_change_analysis_session,
                                          continue_change_analysis, ChangeAnalysisStore)
from src.agents.project_intelligence import is_read_only_capability


def _rt(tmp_path, **kw):
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path, **kw)


class ScriptedClient:
    def __init__(self, producer="answer", consult=None, synthesis=None):
        self.producer = producer
        self.consult = consult or json.dumps(
            {"conclusion": "ok", "affected_entities": [], "downstream_facts": ["镜头"],
             "blockers": [], "user_decisions": [], "evidence": []})
        self.synthesis = synthesis or json.dumps({
            "direct_changes": [{"module": "character", "owner_agent": "character_designer",
                                "summary": "改", "reason": "user_requested"}],
            "downstream_changes": [{"module": "storyboard", "owner_agent": "director",
                                    "summary": "镜头", "reason": "downstream_impact"}],
            "unchanged": [{"module": "script", "entity_id": "s01"}],
            "user_decisions_needed": ["是否保留旧版"]})
        self.chat_calls = 0

    async def chat(self, messages, **kw):
        self.chat_calls += 1
        u = messages[-1]["content"] if messages else ""
        if "咨询问题" in u:
            return self.consult
        if "咨询结果" in u or "综合" in u:
            return self.synthesis
        return self.producer


# ============ §97 大项目上下文有界（catch context explosion）============
def _big_proj(n_scenes=30, shots_per=10):
    """30 场景 × 10 镜头 = 300 镜头。Xiaoyu 在 s05/s15/s25（各 10 镜头）；其 c01 已有视频。"""
    chars = [Character(name="Xiaoyu", description_en="a girl"),
             Character(name="Aran", description_en="a hero")]
    scenes = []
    for i in range(1, n_scenes + 1):
        sid = f"s{i:02d}"
        xy = i in (5, 15, 25)
        shot_chars = ["Xiaoyu"] if xy else (["Aran"] if i % 2 == 0 else [])
        shots = []
        for j in range(1, shots_per + 1):
            sh = Shot(shot_id=f"{sid}_c{j:02d}", scene_id=sid, index=j,
                      description=f"shot {j}", characters=shot_chars, target_duration_s=4)
            if xy and j == 1:
                sh.final_video = "v1"
                sh.video_versions = [VideoVersion(
                    version_id="v1", status="done", video_id="t", local="/v.mp4",
                    url="http://v", created_at=0.0)]
            shots.append(sh)
        scenes.append(Scene(scene_id=sid, summary=f"scene {i}",
                            characters=shot_chars, shots=shots))
    return Project(name="big", style=StyleAnchor(), characters=chars,
                   scenes=scenes, story_text="once upon a time")


def test_large_project_context_bounded(tmp_path):
    """§97：300 镜头项目问「Xiaoyu 在哪些场景、哪些没完成视频」→ 增量有界检视，
    Producer 不得在单次 run 收到全部 300 镜头定义。"""
    ProjectStore(tmp_path).save(_big_proj())
    rt = _rt(tmp_path, client=ScriptedClient(producer="Xiaoyu 在 s05/s15/s25"))
    before = (tmp_path / "output" / "project.json").read_bytes()

    intel = rt.project_intelligence("big")
    classification = classify_request(
        "Xiaoyu 出现在哪些场景？哪些场景还没完成视频？", intel._project())
    assert classification["character_refs"] == ["Xiaoyu"]

    pack = ProjectContextBuilder(intel).build_for_producer(
        request="Xiaoyu 出现在哪些场景？哪些场景还没完成视频？",
        classification=classification)
    # 有界：含 character_usage:Xiaoyu（仅 3 场景）+ storyboard 索引；**不含** 300 镜头逐条全文
    assert "character_usage:Xiaoyu" in pack.module_context
    cu = pack.module_context["character_usage:Xiaoyu"]
    assert cu["scene_count"] == 3 and cu["shot_count"] == 30
    assert cu["first_scene"] == "s05" and cu["last_scene"] == "s25"
    # 关键：pack 里不应出现 300 条镜头的逐条全文（description/prompt_en 等）
    pack_json = json.dumps(pack.to_dict(), ensure_ascii=False)
    assert pack_json.count("_c0") < 40   # 仅 Xiaoyu 3 场景的 shot 出现，非全 300
    assert len(pack_json) < 40000       # 无 whole-project 巨型 prompt（§97）

    # run_inquiry 问题路径：返回答案 + evidence_refs，canonical 不变、无 workspace
    out = asyncio.run(run_inquiry(rt, "big",
                                   "Xiaoyu 出现在哪些场景？哪些场景还没完成视频？",
                                   client=ScriptedClient(producer="s05 s15 s25")))
    assert out["kind"] == "question"
    assert out["evidence_refs"]
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not (tmp_path / "workspaces").exists()


# ============ §91 跨模块变更硬不变量聚合 ============
def test_cross_module_change_hard_invariants(tmp_path):
    """§91：跨模块变更分析 → execution_ready False、0 ChangeOperation、0 Workspace、
    0 Plan 修改、0 image/video Job、0 Agnes 生成调用、canonical 不变。"""
    ProjectStore(tmp_path).save(Project(
        name="demo", style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="a girl"),
                   Character(name="Aran", description_en="a hero")],
        scenes=[Scene(scene_id="s01", summary="meet", characters=["Xiaoyu", "Aran"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="see", characters=["Xiaoyu"],
                                  target_duration_s=4)])],
        story_text="story"))
    rt = _rt(tmp_path, client=ScriptedClient())
    before = (tmp_path / "output" / "project.json").read_bytes()

    sess = asyncio.run(create_change_analysis_session(
        rt, "demo", "把 Xiaoyu 的头发改长并修改结局", background=False))
    sess = asyncio.run(continue_change_analysis(rt, "demo", sess["session_id"]))

    assert sess["status"] == "ready"
    draft = sess["plan_draft"]
    # §37/§38
    assert draft["execution_ready"] is False
    assert "change_operations" not in draft
    for item in draft["direct_changes"] + draft["downstream_changes"]:
        for bad in ("op", "op_type", "update_scene", "update_shot",
                    "insert_shot", "delete_shot", "after", "before"):
            assert bad not in item
    # §91：0 Workspace / 0 Plan 修改 / 0 job / 0 真 Agnes 生成
    assert not (tmp_path / "workspaces").exists()                     # 0 Workspace
    assert not (tmp_path / "output" / "ai" / "production_plans.json").exists()  # 0 Plan 修改
    assert not (tmp_path / "cache" / "jobs.json").exists()            # 0 job（未碰 JobQueue）
    assert (tmp_path / "output" / "project.json").read_bytes() == before  # canonical 不变
    # 全只读 inspect cap（无 mutation workflow）
    from src.agents.tasks import AgentTaskStore
    for t in AgentTaskStore(tmp_path, "demo").list():
        assert is_read_only_capability(t.capability_id)


# ============ §56/§78 跨子系统项目隔离聚合 ============
def test_cross_subsystem_project_isolation(tmp_path):
    """team-chat + change-analysis 都按项目隔离：A 的 thread/session 不在 B。"""
    for n in ("A", "B"):
        ProjectStore(tmp_path / n).save(Project(
            name=n, style=StyleAnchor(),
            characters=[Character(name="Xiaoyu")],
            scenes=[Scene(scene_id="s01", summary="x", characters=["Xiaoyu"],
                          shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                      description="x", characters=["Xiaoyu"])])],
            story_text=""))
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path / name,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=ScriptedClient())
    # team chat
    sa = TeamChatStore(tmp_path / "A", "A")
    sb = TeamChatStore(tmp_path / "B", "B")
    sa.get_or_create_thread()
    sa.append_message(TeamMessage(message_type="user", text="A-only"))
    assert all(m["text"] != "A-only" for m in sb.list_messages())
    # change analysis
    sess_a = asyncio.run(create_change_analysis_session(rt, "A", "把头发改长", background=False))
    assert ChangeAnalysisStore(tmp_path / "B", "B").get_session(sess_a["session_id"]) is None


# §98 手动浏览器验收清单（无法在本环境跑浏览器，供人工回归）：
# 1. 打开项目 → /agents 页 → 点左侧「Team Chat」
# 2. 问项目级问题（如「Xiaoyu 出现在哪些场景？」）→ 见 Producer 回复
# 3. 提跨模块变更（如「把 Xiaoyu 头发改长并修改结局」）→ 见 analysis-only Plan 卡
#    （Requested / Related / Unchanged / Needs decision），无 Apply/Execute 按钮
# 4. 刷新浏览器 → Team Thread 恢复（消息持久）
# 5. 中途改 canonical → 重新分析 → 旧会话 stale（§49）
# 6. 后端重启 → blocked 会话可点「恢复分析」（§55/§57）
