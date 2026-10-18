# Phase 39E：trace 落档时两条 skill 绑定软提示的确定性测试（用户定版实现）。
# 1) mode 对账：record 声称的 generation_mode vs input_visuals 权威推导（t3 谎报教训）。
# 2) 组内 reference-only 意图提醒：段内非首镜+上镜已有视频+无正当理由 → 提醒；
#    有意独立镜（continuity_type=intentional_cut / reference_only_note）不再提醒；
#    组首镜 / 上镜无视频 → 天然豁免。两条都绝不阻断落档。
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, StyleAnchor, VideoVersion
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.director_calibration import _execute_trace_upsert
from src.experiments import calibration_trace as ct

REF_IV = [{"slot": "reference", "semantic_role": "reference_only", "url": "https://h/r.jpg"}]
RELAY_IV = [{"slot": "first", "semantic_role": "relay_start_boundary",
             "url": "https://h/r.jpg", "source_type": "prev_tail_relay"}]


def _proj():
    def mk(sid, idx, ivs):
        sh = Shot(shot_id=f"s01_c0{idx}", scene_id="s01", index=idx,
                  description="x", final_video=f"vv_{idx}",
                  link_mode="multi")
        sh.video_versions.append(VideoVersion(version_id=f"vv_{idx}",
                                              local=f"v{idx}.mp4",
                                              status="done",
                                              input_visuals=list(ivs)))
        return sh
    return Project(name="p39e", style=StyleAnchor(),
                   scenes=[Scene(scene_id="s01", shots=[
                       mk("s01_c01", 1, REF_IV),
                       mk("s01_c02", 2, RELAY_IV),
                       mk("s01_c03", 3, REF_IV)])])


@pytest.fixture()
def env(tmp_path):
    pdir = tmp_path / "proj"
    pdir.mkdir()
    ProjectStore(pdir).save(_proj())
    run = ct.create_run(pdir, story_id="p39e")
    rt = AgentRuntime(project_dir_resolver=lambda name: pdir,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=None)
    return pdir, rt, run["run_id"]


def _upsert(rt, project, run_id, key, record):
    return asyncio.run(_execute_trace_upsert(
        rt, project, json.dumps({"run_id": run_id, "trace_key": key,
                                 "record": record}, ensure_ascii=False),
        None, None))


# ============ mode 对账 ============
def test_mode_mismatch_warning_fires(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c01",
                  {"generation_mode": "multi_relay", "human_deviation_level": "minor"})
    assert out["ok"] is True   # 警告不阻断落档
    assert "mode_mismatch_warning" in out
    assert "multi_reference" in out["mode_mismatch_warning"]
    assert ct.get_link(pdir, run, "s01_c01")["human_deviation_level"] == "minor"


def test_mode_match_no_warning(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c01",
                  {"generation_mode": "multi_reference"})
    assert "mode_mismatch_warning" not in out


def test_no_mode_claim_no_warning(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c01", {"human_deviation_level": "none"})
    assert "mode_mismatch_warning" not in out


# ============ 组内 reference-only 意图提醒 ============
def test_relay_intent_hint_fires_mid_group(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c03", {"human_deviation_level": "minor"})
    assert "relay_intent_hint" in out
    assert "intentional_cut" in out["relay_intent_hint"]
    assert "prev_tail" in out["relay_intent_hint"]


def test_relay_intent_hint_suppressed_by_justification(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c03",
                  {"human_deviation_level": "minor",
                   "continuity_type": "intentional_cut"})
    assert "relay_intent_hint" not in out
    out2 = _upsert(rt, pdir.name, run, "s01_c03",
                   {"reference_only_note": "有意独立镜：云层空镜过渡"})
    assert "relay_intent_hint" not in out2


def test_relay_intent_hint_group_first_exempt(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c01",
                  {"human_deviation_level": "minor"})
    assert "relay_intent_hint" not in out   # 组首镜 reference-only 是约定


def test_relay_intent_hint_relay_shot_exempt(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c02",
                  {"human_deviation_level": "minor"})
    assert "relay_intent_hint" not in out   # 接力镜本就不是 reference-only


def test_relay_intent_hint_prev_not_ready_exempt(env):
    pdir, rt, run = env
    store = ProjectStore(pdir)
    proj = store.load()
    proj.get_shot("s01_c02").final_video = None   # 上镜无视频 → 无从接力
    store.save(proj)
    out = _upsert(rt, pdir.name, run, "s01_c03",
                  {"human_deviation_level": "minor"})
    assert "relay_intent_hint" not in out


def test_trace_key_not_a_shot_silent(env):
    """trace_key 非镜头 id（如 38 的 M-key）→ 两条提示静默跳过，绝不报错。"""
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "M1",
                  {"generation_mode": "multi_relay", "human_deviation_level": "minor"})
    assert out["ok"] is True
    assert "mode_mismatch_warning" not in out
    assert "relay_intent_hint" not in out


# ============ skill 锚点（补丁内容钉住）============
def test_skill_anchor_interlude_postcheck():
    p = __import__("pathlib").Path(
        "src/agents/skill_system/builtin/full-story-relay-calibration/references/director.md")
    t = p.read_text(encoding="utf-8")
    assert "插曲逐镜落档后同样执行接缝复查" in t
    assert "relay_intent_hint" in t
    assert "mode 对账" in t


def test_skill_anchor_bridge_cross_reference():
    p = __import__("pathlib").Path(
        "src/agents/skill_system/builtin/keyframes-bridge-repair/references/director.md")
    t = p.read_text(encoding="utf-8")
    assert "与插曲的分工" in t
    assert "full-story-relay-calibration" in t


# ============ 裁定完整性警告（39C/39E 两次实证后的牙齿）============
def test_adjudication_incomplete_warning_fires(env):
    """视频已生成但 record 无裁定字段 → 警告（不阻断落档）。"""
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c02",
                  {"tail_to_first_mae": 2.8})   # 有视频、有证据、无裁定
    assert out["ok"] is True
    assert "adjudication_incomplete_warning" in out
    assert "human_deviation_level" in out["adjudication_incomplete_warning"]


def test_adjudication_warning_not_fired_when_verdict_present(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c02",
                  {"human_deviation_level": "minor", "human_action": "continue"})
    assert "adjudication_incomplete_warning" not in out


def test_adjudication_warning_not_fired_without_video(env):
    """trace_key 非镜头（无视频概念）→ 不触发。"""
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "M1", {"tail_to_first_mae": 2.0})
    assert "adjudication_incomplete_warning" not in out


# ============ 39E 锚定（Phase 3 修订：槽位语义区分 + 融合首帧路径）============
def test_skill_anchor_relay_first_frame_rules():
    """Phase 3 修订原锚点更新：39E 的「接力镜不得生成分镜首帧/组首镜默认参考
    直生」被 SKILL.md「起步/再锚定决策入口」的端点质量选路取代——裸 multi
    接力镜的槽由系统同步（multi_reference_only 标记），融合首帧路径（c）允许
    且要求为接续镜生成合成首帧；改 i2v 由 first_frame_slot_not_hard_boundary
    拦截（编译层 truth gate）。"""
    p = __import__("pathlib").Path(
        "src/agents/skill_system/builtin/full-story-relay-calibration/references/director.md")
    t = p.read_text(encoding="utf-8")
    assert "slot_semantics=multi_reference_only" in t
    assert "first_frame_slot_not_hard_boundary" in t
    assert "融合首帧起步" in t
    assert "不得在 trace/汇报中称硬接" in t
    sk = __import__("pathlib").Path(
        "src/agents/skill_system/builtin/full-story-relay-calibration/SKILL.md")
    s = sk.read_text(encoding="utf-8")
    assert "起步/再锚定决策入口" in s
    assert "融合首帧起步" in s
    assert "capability-facts.md" in s


def test_first_frame_slot_sync_by_role(tmp_path):
    """泛化首帧槽同步：接力边界→[接力边界]；尾帧作参考→[参考]；人物图/硬首帧→不同步。"""
    import asyncio
    from src.core.models import (Project, Scene, Shot, StyleAnchor, Asset,
                                 ShotStatus, VideoVersion)
    from src.core.store import ProjectStore
    from routers.shots import _sync_first_frame_slot
    pdir = tmp_path / "proj"
    pdir.mkdir()
    c01 = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="A",
               final_video="vv_a",
               video_end_frame=Asset(local="e.jpg", url="https://h/tail.jpg",
                                     status=ShotStatus.DONE.value))
    c02 = Shot(shot_id="s01_c02", scene_id="s01", index=2, description="B",
               final_video="vv_x")
    ProjectStore(pdir).save(Project(name="p", style=StyleAnchor(),
                                    scenes=[Scene(scene_id="s01", shots=[c01, c02])]))
    store = ProjectStore(pdir)

    def mk_version(ivs):
        return VideoVersion(version_id="vv_x", local="v.mp4", status="done",
                            input_visuals=list(ivs))

    # 1) 接力边界 → 同步 + [接力边界] 标记 + 解析上镜尾帧 local
    vv = mk_version([{"slot": "first", "semantic_role": "relay_start_boundary",
                      "url": "https://h/tail.jpg", "source_shot_id": "s01_c01"}])
    asyncio.run(_sync_first_frame_slot(store, "s01_c02", vv, "vv_x", None))
    sh = store.load().get_shot("s01_c02")
    assert sh.first_frame.status == "done"
    assert sh.first_frame.selected.url == "https://h/tail.jpg"
    assert sh.first_frame.selected.local == "e.jpg"   # 解析了上镜尾帧本地路径
    assert "[接力边界]" in sh.first_frame_batches[-1].prompt_snapshot
    # 分镜历史保留（追加不是覆盖）
    assert len(sh.first_frame_batches) >= 1

    # 2) 尾帧作参考（reference_only + prev_tail source）→ 同步 + [参考] 标记
    vv.input_visuals = [{"slot": "reference", "semantic_role": "reference_only",
                         "url": "https://h/tail2.jpg",
                         "source_type": "prev_tail_reference",
                         "source_shot_id": "s01_c01"}]
    vv.version_id = "vv_y"
    store.update_shot("s01_c02", lambda shot: setattr(shot, "final_video", "vv_y"))
    asyncio.run(_sync_first_frame_slot(store, "s01_c02", vv, "vv_y", None))
    sh = store.load().get_shot("s01_c02")
    assert "[参考]" in sh.first_frame_batches[-1].prompt_snapshot
    assert sh.first_frame.selected.url == "https://h/tail2.jpg"

    # 3) 人物图参考 → 不同步（槽内不变；人物图由 Binding 携带）
    before = sh.selected_first_frame
    vv.input_visuals = [{"slot": "reference", "semantic_role": "reference_only",
                         "url": "https://h/hero.jpg", "source_type": "character_look"}]
    asyncio.run(_sync_first_frame_slot(store, "s01_c02", vv, "vv_z", None))
    assert store.load().get_shot("s01_c02").selected_first_frame == before

    # 4) 硬首帧 → 不同步（槽内已是实际输入）
    vv.input_visuals = [{"slot": "first", "semantic_role": "hard_start_boundary",
                         "url": "https://h/own.jpg"}]
    asyncio.run(_sync_first_frame_slot(store, "s01_c02", vv, "vv_w", None))
    assert store.load().get_shot("s01_c02").selected_first_frame == before
