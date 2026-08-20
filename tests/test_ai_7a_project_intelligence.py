"""AI-7A §6~§18 Project Intelligence —— 只读语义检视 + 只读硬闸 focused Gate（7A-1）。

engine 级：直接驱动 ProjectIntelligenceService + 注入 plans_reader / task_store，
不依赖 TestClient / 真 Agnes。每个 case 用 tmp_path 独立项目，互不污染（匹配
tests/test_agents.py 风格）。

Gate 覆盖：只读能力分类 / 显式硬闸 / Producer 结构性不可 mutation（§96）/ 各视图
正确性 / 有界响应（§9）/ lineage（§18）/ dependencies（§48）/ history 诚实不造假
（§11/§80）/ canonical 字节不变（§43）/ index 缓存可重建（§13）。
"""
import pytest

from src.core.models import (Project, Scene, Shot, Character, StyleAnchor,
                              ImageBatch, Candidate, VideoVersion)
from src.core.store import ProjectStore
from src.agents.project_intelligence import (
    ProjectIntelligenceService, READ_ONLY_CAPABILITIES, MUTATION_CAPABILITIES,
    is_read_only_capability, assert_read_only_capability, invalidate_index_cache)
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.registry import Registries
from src.agents.tasks import AgentTaskStore, AgentTask
from src.ai.production_planner import SceneProductionPlan, shot_structure_snapshot


# ============ 辅助 ============
def _big_proj():
    """4 场景 / 4 镜头 / 3 角色：Aran(s01,s02)、Xiaoyu(s02,s03)、Mechanical Dog(未上场)、s04 空场景。"""
    chars = [Character(name="Aran", description_en="a hero"),
             Character(name="Xiaoyu", description_en="a girl"),
             Character(name="Mechanical Dog", description_en="a robot dog")]
    s1 = Scene(scene_id="s01", summary="Aran wakes", characters=["Aran"],
               shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                           description="Aran opens eyes", characters=["Aran"],
                           target_duration_s=5)])
    s2 = Scene(scene_id="s02", summary="Aran meets Xiaoyu", characters=["Aran", "Xiaoyu"],
               shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1,
                           description="Aran sees Xiaoyu", characters=["Aran", "Xiaoyu"],
                           target_duration_s=4),
                      Shot(shot_id="s02_c02", scene_id="s02", index=2,
                           description="They talk", characters=["Aran", "Xiaoyu"],
                           target_duration_s=6)])
    s3 = Scene(scene_id="s03", summary="Xiaoyu runs", characters=["Xiaoyu"],
               shots=[Shot(shot_id="s03_c01", scene_id="s03", index=1,
                           description="Xiaoyu sprints", characters=["Xiaoyu"],
                           target_duration_s=3)])
    s4 = Scene(scene_id="s04", summary="empty scene", characters=[], shots=[])
    return Project(name="demo", style=StyleAnchor(), characters=chars,
                   scenes=[s1, s2, s3, s4], story_text="Once upon a time...")


def _apply_assets(p: Project):
    """s02_c01：选中首帧 c1（batch b1）；s03_c01：最终视频版本 v1。"""
    s02_c01 = p.get_shot("s02_c01")
    s02_c01.first_frame_batches = [ImageBatch(
        batch_id="b1", kind="first_frame", status="done",
        candidates=[Candidate(candidate_id="c1", local="/ff.png",
                              url="http://ff", status="done", created_at=0.0)])]
    s02_c01.selected_first_frame = "c1"
    s03_c01 = p.get_shot("s03_c01")
    s03_c01.video_versions = [VideoVersion(
        version_id="v1", status="done", video_id="task_xx", local="/v.mp4",
        url="http://v", first_frame_candidate_id="c1", created_at=0.0)]
    s03_c01.final_video = "v1"


def _plan_dict(scene: Scene, status: str, stale: bool) -> dict:
    snap = shot_structure_snapshot(scene)
    return SceneProductionPlan(
        plan_id=f"plan_{scene.scene_id}", project_name="demo", scene_id=scene.scene_id,
        scene_summary_snapshot="STALE_DIFFERENT" if stale else (scene.summary or ""),
        scene_characters_snapshot=list(scene.characters),
        shot_structure_snapshot=snap, status=status, shots=[]).model_dump()


def _svc_with_plans(tmp_path):
    """service + 真项目落盘 + 注入 plans_reader（s02 approved / s03 stale）+ running task。"""
    proj = _big_proj()
    _apply_assets(proj)
    ProjectStore(tmp_path).save(proj)
    plans = {"s02": _plan_dict(proj.scenes[1], "approved", stale=False),
             "s03": _plan_dict(proj.scenes[2], "draft", stale=True)}
    reader = lambda project, scene_id: plans.get(scene_id)   # noqa: E731
    # 一个 running AgentTask 挂在 s02_c01（shot.video）
    store = AgentTaskStore(tmp_path, "demo")
    store.create(AgentTask(task_id="", project="demo", requester_agent="producer",
                          target_agent="shot_producer", capability_id="shot.video",
                          scope_type="shot", scope_id="s02_c01", operation="execute",
                          status="running"))
    svc = ProjectIntelligenceService(
        project_name="demo", project_loader=lambda: ProjectStore(tmp_path).load(),
        plans_reader=reader, task_store_getter=lambda: AgentTaskStore(tmp_path, "demo"))
    return svc


def _runtime(tmp_path, **kw):
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path, **kw)


@pytest.fixture(autouse=True)
def _clear_index_cache():
    invalidate_index_cache()
    yield
    invalidate_index_cache()


# ============ 1. 只读能力分类 ============
def test_read_only_capability_classification():
    for cid in ("project.inspect", "story.inspect", "character.inspect",
                "storyboard.inspect", "production_plan.inspect", "shot.inspect",
                "composition.inspect", "review.shot", "review.scene"):
        assert is_read_only_capability(cid) is True, f"{cid} 应为只读"
    for cid in ("story.edit", "character.design", "storyboard.propose_change",
                "production_plan.create", "shot.first_frame", "shot.last_frame",
                "shot.video", "composition.compose"):
        assert is_read_only_capability(cid) is False, f"{cid} 应为 mutation"
    # 两集合不交
    assert READ_ONLY_CAPABILITIES & MUTATION_CAPABILITIES == set()


# ============ 2. 显式硬闸（§43/§96）============
def test_assert_read_only_blocks_mutation():
    for cid in ("shot.video", "storyboard.propose_change", "production_plan.create",
                "character.design", "composition.compose"):
        with pytest.raises(AgentError) as ei:
            assert_read_only_capability(cid)
        assert ei.value.code == "permission_denied"
    # 只读能力放行
    assert_read_only_capability("project.inspect")   # 不抛
    assert_read_only_capability("shot.inspect")


# ============ 3. Producer 结构性不可 mutation（§96 天然闸）============
def test_producer_cannot_execute_mutation():
    r = Registries()
    for cid in ("shot.video", "shot.first_frame", "shot.last_frame",
                "storyboard.propose_change", "production_plan.create",
                "story.edit", "character.design", "composition.compose"):
        assert r.capabilities.can_execute("producer", cid, "execute") is False
    # producer 唯一可执行的是 project.inspect
    assert r.capabilities.can_execute("producer", "project.inspect", "inspect") is True


# ============ 4. overview 计数 + 签名 ============
def test_overview_counts_and_signature(tmp_path):
    svc = _svc_with_plans(tmp_path)
    res = svc.overview()
    d = res.data
    assert d["scenes"] == 4 and d["shots"] == 4 and d["characters"] == 3
    assert d["story_present"] is True
    assert d["storyboard"]["empty_scenes"] == ["s04"]
    assert d["production"]["approved_plans"] == 1
    assert d["production"]["stale_plans"] == 1
    assert d["production"]["no_plan_scenes"] == 2
    assert d["production"]["shots_with_first_frame"] == 1   # s02_c01
    assert d["production"]["shots_with_video"] == 1          # s03_c01
    assert res.snapshot_signature and len(res.snapshot_signature) == 64  # sha256
    assert any(r.entity_id == "demo" for r in res.evidence_refs)


# ============ 5. character_usage（§16 关键能力）============
def test_character_usage(tmp_path):
    svc = _svc_with_plans(tmp_path)
    xy = svc.character_usage("Xiaoyu")
    assert xy.data["found"] is True
    assert xy.data["scene_count"] == 2 and xy.data["shot_count"] == 3
    assert xy.data["first_scene"] == "s02" and xy.data["last_scene"] == "s03"
    ar = svc.character_usage("Aran")
    assert ar.data["scene_count"] == 2 and ar.data["shot_count"] == 3
    assert ar.data["first_scene"] == "s01"
    # 在册但未上场
    dog = svc.character_usage("Mechanical Dog")
    assert dog.data["found"] is True
    assert dog.data["scene_count"] == 0 and dog.data["shot_count"] == 0
    assert dog.data["first_scene"] is None
    # 不存在
    ghost = svc.character_usage("Ghost")
    assert ghost.data["found"] is False


# ============ 6. 有界响应（§9）============
def test_bounded_character_usage(tmp_path):
    svc = _svc_with_plans(tmp_path)
    res = svc.character_usage("Aran", limit=1)
    assert len(res.data["scenes"]) == 1
    assert res.truncated is True
    assert res.next_cursor == "1"
    assert res.data["scene_count"] == 2   # 总数仍准确


def test_storyboard_index_bounded(tmp_path):
    svc = _svc_with_plans(tmp_path)
    res = svc.storyboard_index(limit=2)
    assert len(res.data["scenes"]) == 2
    assert res.truncated is True
    # 索引行不含镜头明细（§15）
    assert "shots" not in res.data["scenes"][0]


# ============ 7. scene 返回镜头明细（§15）============
def test_scene_returns_shots(tmp_path):
    svc = _svc_with_plans(tmp_path)
    s2 = svc.scene("s02")
    assert s2.data["scene_id"] == "s02"
    assert len(s2.data["shots"]) == 2
    sh0 = s2.data["shots"][0]
    assert sh0["shot_id"] == "s02_c01"
    assert set(sh0.keys()) == {"shot_id", "description", "characters", "status",
                                "target_duration_s", "has_first_frame", "has_video"}
    assert sh0["has_first_frame"] is True
    # 空场景
    s4 = svc.scene("s04")
    assert s4.data["shots"] == []
    # 未知场景
    assert svc.scene("sXX").data["found"] is False


# ============ 8. shot 明细 ============
def test_shot_detail(tmp_path):
    svc = _svc_with_plans(tmp_path)
    res = svc.shot("s02_c01")
    d = res.data
    assert d["shot_id"] == "s02_c01" and d["scene_id"] == "s02"
    assert d["selected_first_frame"] == "c1"
    assert d["first_frame_batch_count"] == 1
    assert d["video_version_count"] == 0
    assert svc.shot("sXX").data["found"] is False


# ============ 9. production 状态（§17）============
def test_production_states(tmp_path):
    svc = _svc_with_plans(tmp_path)
    d = svc.production().data
    states = {r["scene_id"]: r["plan_state"] for r in d["per_scene"]}
    assert states == {"s01": "none", "s02": "approved", "s03": "stale", "s04": "none"}
    assert d["approved_plans"] == 1 and d["stale_plans"] == 1
    assert d["running_tasks"] and d["running_tasks"][0]["capability_id"] == "shot.video"


# ============ 10. lineage（§18，用确切 id 不猜）============
def test_lineage(tmp_path):
    svc = _svc_with_plans(tmp_path)
    # s02_c01：选中首帧 c1、无最终视频、1 个 running task
    l2 = svc.lineage("s02_c01").data
    assert l2["selected_first_frame"]["candidate_id"] == "c1"
    assert l2["selected_first_frame"]["batch_id"] == "b1"
    assert l2["final_video_version"] is None
    assert len(l2["related_agent_tasks"]) == 1
    assert l2["related_agent_tasks"][0]["execution_ref"] is None  # inspect-style task 无 durable ref
    # s03_c01：最终视频 v1、无选中首帧、无 task
    l3 = svc.lineage("s03_c01").data
    assert l3["selected_first_frame"] is None
    assert l3["final_video_version"]["version_id"] == "v1"
    assert l3["final_video_version"]["video_id"] == "task_xx"
    assert l3["related_agent_tasks"] == []
    assert svc.lineage("sXX").data["found"] is False


# ============ 11. dependencies（§48，只加仓库能证明的边）============
def test_dependencies(tmp_path):
    svc = _svc_with_plans(tmp_path)
    # 角色 → scene/shot
    dch = svc.dependencies("character", "Xiaoyu").data
    rels = [e["relation"] for e in dch["edges"]]
    assert rels.count("character_used_by_scene") == 2   # s02, s03
    assert rels.count("character_used_by_shot") == 3    # s02_c01, s02_c02, s03_c01
    # 场景 → shot + plan 覆盖
    dsc = svc.dependencies("scene", "s02").data
    srels = [e["relation"] for e in dsc["edges"]]
    assert srels.count("scene_contains_shot") == 2
    assert srels.count("shot_covered_by_production_plan") == 1
    # 镜头 → video + plan 覆盖
    dsh = svc.dependencies("shot", "s03_c01").data
    shrels = [e["relation"] for e in dsh["edges"]]
    assert "video_generated_from_shot" in shrels
    assert "shot_covered_by_production_plan" in shrels


# ============ 12. history 诚实不造假（§11/§80）============
def test_history_honest(tmp_path):
    svc = _svc_with_plans(tmp_path)
    h = svc.history().data
    avail = h["history_available"]
    assert avail["script_pocket"] is False
    assert avail["character_pocket"] is False
    assert avail["project_checkpoint"] is False
    assert avail["video_version_history"] is True
    assert avail["image_batch_history"] is True
    assert avail["agent_task_history"] is True
    assert h["counts"]["video_versions"] == 1
    assert h["counts"]["image_batches"] == 1   # s02_c01 一个首帧 batch
    assert h["counts"]["agent_tasks"] == 1
    # 无 "Script Revision S17" 之类伪造字段
    assert "script_revisions" not in h


# ============ 13. canonical 字节不变（§43/§18 红线）============
def test_canonical_unchanged(tmp_path):
    svc = _svc_with_plans(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    # 跑遍所有视图
    for view, kw in [("overview", {}), ("script", {}), ("characters", {}),
                     ("character_usage", {"character_id": "Xiaoyu"}),
                     ("storyboard", {}), ("scene", {"scene_id": "s02"}),
                     ("shot", {"shot_id": "s02_c01"}), ("production", {}),
                     ("lineage", {"shot_id": "s02_c01"}),
                     ("dependencies", {"entity_id": "character", "scene_id": "Xiaoyu"}),
                     ("history", {})]:
        svc.inspect(view, **kw)
    after = (tmp_path / "output" / "project.json").read_bytes()
    assert before == after, "canonical project.json 被只读检视修改（违反 §43）"


# ============ 14. index 缓存可重建（§13，仅内存、不落盘）============
def test_index_cache_and_rebuild(tmp_path):
    svc = _svc_with_plans(tmp_path)
    idx1 = svc.build_index()
    idx2 = svc.build_index()
    assert idx1 is idx2   # 签名未变 → 同一缓存实例
    assert idx1.scene_order == ["s01", "s02", "s03", "s04"]
    assert idx1.shot_to_characters["s02_c01"] == ["Aran", "Xiaoyu"]
    assert idx1.production_state_by_scene["s02"] == "approved"
    assert idx1.production_state_by_scene["s03"] == "stale"
    # 项目变更 → 签名变 → 重建
    p = ProjectStore(tmp_path).load()
    p.scenes.append(Scene(scene_id="s05", summary="new", characters=["Aran"],
                          shots=[Shot(shot_id="s05_c01", scene_id="s05", index=1,
                                      description="new", characters=["Aran"])]))
    ProjectStore(tmp_path).save(p)
    idx3 = svc.build_index()
    assert idx3 is not idx1
    assert "s05" in idx3.scene_order and "s05" not in idx1.scene_order
    # 不落盘：output/agents 下无 index 文件
    agents_dir = tmp_path / "output" / "agents"
    if agents_dir.exists():
        assert not any("index" in f.name.lower() for f in agents_dir.rglob("*.json"))


# ============ 15. runtime 接线 + 未知视图 ============
def test_runtime_wiring_and_unknown_view(tmp_path):
    proj = _big_proj()
    _apply_assets(proj)
    ProjectStore(tmp_path).save(proj)
    rt = _runtime(tmp_path)   # plans_reader 未注入 → 全 none
    svc = rt.project_intelligence("demo")
    res = svc.overview()
    assert res.data["scenes"] == 4
    assert all(r["plan_state"] == "none" for r in svc.production().data["per_scene"])
    # 未知视图干净返回
    bad = svc.inspect("nonsense")
    assert bad.data["error"] == "unknown_view"
    # canonical 不变
    before = (tmp_path / "output" / "project.json").read_bytes()
    svc.overview()
    assert (tmp_path / "output" / "project.json").read_bytes() == before
