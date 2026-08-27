"""AI-7D-R · 共享级联删除服务 src.storyboard.delete_service 单元测试。

不联网、不调 LLM、不依赖 deps（execute 里的 job_queue best-effort 在测试环境静默）。
验证级联范围、产物清理、计划处理、index 重排、悬空指针、护栏、说明文本确定性。
"""
import json
from pathlib import Path

import pytest

from src.core.models import (Project, Scene, Shot, Character, StyleAnchor,
                              VideoAsset, AudioAsset, ImageAsset, Asset,
                              ImageBatch, Candidate, VideoVersion)
from src.core.store import ProjectStore
from src.ai.production_planner import (SceneProductionPlan, PlannedShot,
                                        read_plans, write_plans, is_plan_stale)
from src.storyboard.delete_service import (preview_delete, execute_delete,
                                            describe_delete_preview, DeleteError)


def _asset(local):
    return Asset(local=local, status="done")


def _make_shot(shot_id, scene_id, index, *, desc="", chars=None, video_local=None,
               source_shot_id=None):
    sh = Shot(shot_id=shot_id, scene_id=scene_id, index=index,
              description=desc or f"镜头{shot_id}", characters=chars or [])
    if video_local:
        sh.video = VideoAsset(local=video_local, status="done")
        sh.audio = AudioAsset(local=video_local.replace(".mp4", ".wav"), status="done")
        sh.video_end_frame = _asset(video_local.replace(".mp4", "_end.png"))
        # 旧字段候选
        sh.first_frame = ImageAsset(candidates=[_asset(video_local.replace(".mp4", "_ff.png"))],
                                    status="done")
        # 新批次
        sh.first_frame_batches = [ImageBatch(
            batch_id=f"b_{shot_id}", kind="first_frame", status="done",
            candidates=[Candidate(candidate_id=f"c_{shot_id}",
                                  local=video_local.replace(".mp4", "_ff_batch.png"),
                                  status="done")])]
        sh.video_versions = [VideoVersion(
            version_id=f"vv_{shot_id}", status="done",
            local=video_local.replace(".mp4", "_v2.mp4"))]
    if source_shot_id:
        sh.source_shot_id = source_shot_id
    return sh


def _touch(path: Path, content=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


@pytest.fixture
def proj_fixture(tmp_path):
    """s01: c01(独立,有视频) ← c02(接力c01,有视频) ← c03(接力c02,无视频)。
    s02: c01(独立,无视频)。角色 LinXia。"""
    out = tmp_path / "output"
    f1 = _touch(tmp_path / "output" / "s01_c01.mp4")
    f2 = _touch(tmp_path / "output" / "s01_c02.mp4")
    s01 = Scene(scene_id="s01", summary="雨中", characters=["LinXia"], shots=[
        _make_shot("s01_c01", "s01", 1, video_local=f1),
        _make_shot("s01_c02", "s01", 2, video_local=f2, source_shot_id="s01_c01"),
        _make_shot("s01_c03", "s01", 3, source_shot_id="s01_c02"),
    ])
    s02 = Scene(scene_id="s02", summary="雨后", characters=["LinXia"], shots=[
        _make_shot("s02_c01", "s02", 1)])
    proj = Project(name="demo", style=StyleAnchor(),
                   story_text="雨", characters=[Character(name="LinXia", description_en="x")],
                   scenes=[s01, s02])
    ProjectStore(tmp_path).save(proj)
    # 两个段都建制作计划
    plans = {}
    for sid, sc in (("s01", s01), ("s02", s02)):
        plans[sid] = SceneProductionPlan(
            plan_id=f"plan_{sid}", project_name="demo", scene_id=sid,
            scene_summary_snapshot=sc.summary, scene_characters_snapshot=list(sc.characters),
            shot_structure_snapshot=[{"shot_id": s.shot_id, "description": s.description,
                                       "characters": list(s.characters),
                                       "target_duration_s": s.target_duration_s}
                                      for s in sc.shots],
            shots=[PlannedShot(shot_id=s.shot_id, opening_source="independent",
                               link_mode="i2v", reason="",
                               generate_first_frame=True, generate_last_frame=False,
                               requires_previous_video_end=False) for s in sc.shots])
    write_plans(tmp_path, plans)
    return tmp_path, proj


# ---------- 1. 删段级联 ----------
def test_delete_scene_cascades_shots_assets_plan(proj_fixture):
    tmp_path, proj = proj_fixture
    res = execute_delete(tmp_path, proj, "scene", "s01")
    # 内存结构
    ids = [s.scene_id for s in proj.scenes]
    assert "s01" not in ids and "s02" in ids
    assert all(s.scene_id != "s01" for s in proj.all_shots())
    # 计划：s01 删，s02 留
    plans = read_plans(tmp_path)
    assert "s01" not in plans and "s02" in plans
    assert res["plan_removed"] is True
    # 产物文件删了（c01/c02 各有 6 个文件，c03 无）
    assert res["removed_files"] > 0
    for f in ["s01_c01.mp4", "s01_c02.mp4", "s01_c01_ff.png", "s01_c02_ff_batch.png",
              "s01_c01_v2.mp4"]:
        assert not (tmp_path / "output" / f).exists(), f
    # s02 没动
    assert (tmp_path / "output" / "s02_c01").parent.exists()
    # 角色未删
    assert any(c.name == "LinXia" for c in proj.characters)
    deleted = res["deleted"]
    assert deleted["scenes"] == ["s01"] and set(deleted["shots"]) == {"s01_c01", "s01_c02", "s01_c03"}


# ---------- 2. 删镜头只级联下游 ----------
def test_delete_shot_cascades_downstream_only(proj_fixture):
    tmp_path, proj = proj_fixture
    res = execute_delete(tmp_path, proj, "shot", "s01_c02")
    remaining = {s.shot_id for s in proj.all_shots()}
    # c02 删，c03（接力 c02）删；c01 上游留；s02_c01 留
    assert remaining == {"s01_c01", "s02_c01"}
    assert set(res["deleted"]["shots"]) == {"s01_c02", "s01_c03"}
    # 计划文件保留（s01 的 plan 自动 stale）
    plans = read_plans(tmp_path)
    assert "s01" in plans and "s02" in plans
    assert res["plan_removed"] is False
    s01 = next(s for s in proj.scenes if s.scene_id == "s01")
    assert is_plan_stale(plans["s01"], s01)   # 镜头数量已变化
    # 上游 c01 产物保留
    assert (tmp_path / "output" / "s01_c01.mp4").exists()


# ---------- 3. preview 纯读不写 ----------
def test_preview_is_pure_no_mutation(proj_fixture):
    tmp_path, proj = proj_fixture
    before = (tmp_path / "output" / "project.json").read_bytes()
    plan_before = (tmp_path / "output" / "ai" / "production_plans.json").read_bytes()
    prev = preview_delete(proj, "scene", "s01")
    assert prev["found"] is True and set(prev["will_delete"]["shots"]) == {
        "s01_c01", "s01_c02", "s01_c03"}
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert (tmp_path / "output" / "ai" / "production_plans.json").read_bytes() == plan_before
    assert (tmp_path / "output" / "s01_c01.mp4").exists()


# ---------- 4. 不存在实体 ----------
def test_unknown_entity(proj_fixture):
    tmp_path, proj = proj_fixture
    prev = preview_delete(proj, "scene", "s99")
    assert prev["found"] is False
    with pytest.raises(DeleteError) as ei:
        execute_delete(tmp_path, proj, "shot", "s99_c01")
    assert ei.value.code == "not_found"


# ---------- 5. 幸存 index 重排、shot_id 不变 ----------
def test_reindex_survivors_stable_ids(proj_fixture):
    tmp_path, proj = proj_fixture
    execute_delete(tmp_path, proj, "shot", "s01_c02")
    s01 = next(s for s in proj.scenes if s.scene_id == "s01")
    assert [sh.shot_id for sh in s01.shots] == ["s01_c01"]   # id 不变
    assert s01.shots[0].index == 1                            # index 连续


# ---------- 6. 悬空 source_shot_id / split_next 清空 ----------
def test_dangling_source_pointer_cleared(tmp_path):
    """构造 A、B、D：D.source_shot_id=B（D 不在 B 的下游链，因为 B 不接力任何人，
    但 D 指向 B）。删 B 时 D 也作为下游被删（反向扫描）。这里改为构造独立段中一个镜头
    指向将被删者，验证指针清空——用 split_next 更直接。"""
    s = Scene(scene_id="s01", summary="x", shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1, split_next="s01_c02"),
        Shot(shot_id="s01_c02", scene_id="s01", index=2),
    ])
    proj = Project(name="d", style=StyleAnchor(), scenes=[s])
    ProjectStore(tmp_path).save(proj)
    execute_delete(tmp_path, proj, "shot", "s01_c02")
    c01 = proj.scenes[0].shots[0]
    assert c01.split_next is None


# ---------- 7. 角色 unsupported ----------
def test_character_unsupported(proj_fixture):
    tmp_path, proj = proj_fixture
    prev = preview_delete(proj, "character", "LinXia")
    assert prev["supported"] is False
    with pytest.raises(DeleteError) as ei:
        execute_delete(tmp_path, proj, "character", "LinXia")
    assert ei.value.code == "unsupported"


# ---------- 8. 护栏：越界文件不删 ----------
def test_guardrail_blocks_outside_project(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = _touch(outside / "secret.mp4")
    s = Scene(scene_id="s01", summary="x", shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1,
             video=VideoAsset(local=secret, status="done"))])
    proj = Project(name="d", style=StyleAnchor(), scenes=[s])
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    ProjectStore(proj_dir).save(proj)
    res = execute_delete(proj_dir, proj, "scene", "s01")
    assert Path(secret).exists()          # 没被删
    assert res["removed_files"] == 0


# ---------- 9. 说明文本确定性 + 含确认句 ----------
def test_preview_text_deterministic(proj_fixture):
    _, proj = proj_fixture
    t1 = describe_delete_preview(preview_delete(proj, "scene", "s01"))
    t2 = describe_delete_preview(preview_delete(proj, "scene", "s01"))
    assert t1 == t2
    assert "确定删除吗" in t1
    tshot = describe_delete_preview(preview_delete(proj, "shot", "s01_c02"))
    assert "确定删除吗" in tshot
    assert "s01_c03" in tshot   # 下游镜头被列出
