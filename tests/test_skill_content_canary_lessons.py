"""Skill 包内容回归锚定——P39C Canary F1-F6 教训方法化（§3.2）。

每个锚点都是一次实证事故的方法化改写（"改方法不改 Agent"）；skill 文本
回退（丢锚点/丢段）即红。锚点与 HANDOFF_NEXT_SESSION.md §3.2 一一对应：
  1. 首镜/直生镜参考姿态匹配（F2，38 walk_side 先例）
  2. bind 前场景参考↔冻结核对（F1）
  3. 负面约束进 negative_prompt 生成字段（F 负面约束没进字段）
  4. 结构纪律：段=地点/叙事单元、段内多镜（F5，6×1 实证）
  5. 方法完成度收口：裁定枚举字段非空才算交付（Phase D replay 实证）
  6. 像素接缝≠内容接缝（F3）进失败模型
"""
from pathlib import Path

_BUILTIN = (Path(__file__).resolve().parents[1] / "src" / "agents"
            / "skill_system" / "builtin")

_RELAY_SKILL = "full-story-relay-calibration/SKILL.md"
_RELAY_DIR = "full-story-relay-calibration/references/director.md"
_RELAY_SW = "full-story-relay-calibration/references/script_writer.md"
_RELAY_PROD = "full-story-relay-calibration/references/producer.md"


def _t(rel: str) -> str:
    return (_BUILTIN / rel).read_text(encoding="utf-8")


# ---- 1. 参考姿态 ↔ 镜头动作匹配（F2）----
def test_pose_reference_conflict_method_in_director():
    d = _t(_RELAY_DIR)
    assert "参考姿态 ↔ 镜头动作匹配" in d
    assert "prompt-reference 姿态冲突" in d
    assert "pose_reference_conflict" in d
    assert "video_reference_asset_id" in d
    assert "不带冲突提交" in d
    assert "38 walk_side 先例" in d


def test_first_shot_pose_matching_in_script_writer():
    s = _t(_RELAY_SW)
    assert "prompt-reference 姿态冲突" in s
    assert "多姿态 Look" in s


# ---- 2. 场景参考 ↔ 冻结 description 一致性（F1）----
def test_freeze_vs_siv_consistency_method():
    d = _t(_RELAY_DIR)
    assert "场景参考 ↔ 冻结 description 一致性" in d
    assert "production.inspect" in d
    assert "逐项对照" in d
    assert "不带冲突生成" in d


# ---- 3. 负面约束进生成字段 ----
def test_negative_prompt_write_path_method():
    d = _t(_RELAY_DIR)
    assert "shot.negative_prompt" in d
    assert "唯一正式负面约束通路" in d
    assert "只写进 trace" in d
    s = _t(_RELAY_SW)
    assert "shot.negative_prompt" in s
    assert "只留在策划文本里生成端拿不到" in s


# ---- 4. 结构纪律（F5）----
def test_structure_discipline():
    s = _t(_RELAY_SW)
    assert "段 = 一个地点/叙事单元" in s
    assert "碎片结构" in s
    p = _t(_RELAY_PROD)
    assert "碎片结构" in p
    assert "打回 Script Writer 重组" in p
    k = _t(_RELAY_SKILL)
    assert "段=地点/叙事单元、段内多镜" in k


# ---- 5. 方法完成度收口（Phase D replay 实证）----
def test_method_completion_closure():
    d = _t(_RELAY_DIR)
    assert "方法完成度收口" in d
    assert "加载了方法 ≠ 执行完方法" in d
    assert "缺裁定字段的落档视为本轮未完成" in d
    k = _t(_RELAY_SKILL)
    assert "缺裁定字段的落档视为未完成" in k
    assert "zero-write 纠偏话语不是交付物" in k


# ---- 6. 像素接缝≠内容接缝（F3）进失败模型 ----
def test_pixel_seam_vs_content_seam_in_failure_model():
    k = _t(_RELAY_SKILL)
    assert "像素接缝≠内容接缝" in k
    assert "expected_tail↔实际尾帧" in k


# ---- 工作流图：预检进入 Director 逐镜循环 ----
def test_workflow_diagram_has_precheck_step():
    k = _t(_RELAY_SKILL)
    assert "提交前双预检" in k
    assert "negative_prompt" in k
