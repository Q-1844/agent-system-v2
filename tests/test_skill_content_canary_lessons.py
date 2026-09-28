"""Skill 包内容回归锚定——P39C F1-F6 教训方法化（§3.2）+ P39D reference-role
语义修正（用户 2026-09-26 定版：reference ≠ first frame）。

锚点对应：
  1. 输入角色决策表：REFERENCE_ONLY / HARD_START_BOUNDARY / RELAY_START_BOUNDARY
     分口径（P39D 修正——reference-only 姿态命中只记 soft 诊断，绝不阻断）
  2. bind 前场景参考↔冻结核对（F1，语义真值轴，照常执行）
  3. 负面约束进 negative_prompt 生成字段（约束生成结果，不约束参考资产）
  4. 结构纪律：段=地点/叙事单元、段内多镜（F5，6×1 实证）
  5. 方法完成度收口：裁定枚举字段非空才算交付（Phase D replay 实证）
  6. 像素接缝≠内容接缝（F3）进失败模型
  7. MINOR accept+adapt / major 等用户 / intentional cut 不强制 relay
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


# ---- 1. 输入角色决策表（P39D 修正核心）----
def test_reference_role_decision_table_in_director():
    d = _t(_RELAY_DIR)
    assert "REFERENCE_ONLY" in d and "HARD_START_BOUNDARY" in d
    assert "HARD_END_BOUNDARY" in d and "RELAY_START_BOUNDARY" in d
    assert "pose_reference_mismatch_soft" in d
    # reference-only 的四个"不得"
    assert "不得阻断" in d
    assert "不得要求补参考" in d or "不得要求补动作匹配参考" in d or "不得把" in d
    assert "不得升级 major" in d or "升级 major" in d
    assert "不得请用户改故事" in d or "改写 Shot 开场" in d or "不得改写故事" in d
    # 硬边界口径
    assert "必须满足 opening state" in d
    assert "换边界帧 / 换 mode" in d
    # relay 口径
    assert "不做姿态关键词预判" in d or "不做姿态预判" in d
    # 可选建议语义（不预先要求）
    assert "可选质量策略建议" in d or "可选建议" in d


def test_negative_constraint_binds_result_not_reference():
    d = _t(_RELAY_DIR)
    assert "约束的是【生成结果】，不是参考资产本身" in d


def test_first_shot_action_by_narrative_in_script_writer():
    s = _t(_RELAY_SW)
    assert "REFERENCE_ONLY" in s
    assert "身份库不是姿态库" in s
    assert "不迁就参考姿态" in s or "不因" in s


def test_producer_no_user_gate_for_soft_reference_risk():
    p = _t(_RELAY_PROD)
    assert "不上报" in p
    assert "组首镜的图作参考" in p or "带标记" in p


# ---- 插曲制度与组约定（P39D 用户定版）----
def test_interlude_doctrine():
    d = _t(_RELAY_DIR)
    assert "插曲方案" in d
    assert "不重试" in d
    assert "剧本与故事段" in d
    s = _t(_RELAY_SW)
    assert "插曲设计" in s
    assert "实际尾帧" in s
    k = _t(_RELAY_SKILL)
    assert "插曲（interlude）" in k


def test_group_convention_light():
    """组约定轻量锚点（39F 软语义定版：组真实性由是否承接上一条真实尾帧决定）。"""
    k = " ".join(_t(_RELAY_SKILL).split())   # 归一化换行（文本锚点跨行友好）
    assert "组（group，约定）" in k
    assert "不存 group canonical、不强制" in k          # 系统不做强制（语义保留）
    assert "由镜头间是否承接上一条真实 `video_end_frame` 决定" in k
    assert "跨段 + 显式接力" in k
    # Phase 3：组首起步改为显式决策（端点质量选路），原句退役
    assert "新组首镜显式决定是否跨组延续" in k


def test_slot_semantics_every_video():
    """槽位语义适用每个视频：参考或首尾帧皆可、参考优先、参考必标记。"""
    d = _t(_RELAY_DIR)
    assert "作参考或作首尾帧都可以，系统都允许" in d
    assert "作参考优先" in d
    assert "semantic_role=reference_only" in d


# ---- 2. 场景参考 ↔ 冻结 description 一致性（F1，保留）----
def test_freeze_vs_siv_consistency_method():
    d = _t(_RELAY_DIR)
    assert "场景 ↔ 冻结一致性" in d or "场景参考 ↔ 冻结 description 一致性" in d
    assert "production.inspect" in d
    assert "逐项对照" in d


# ---- 3. 负面约束进生成字段 ----
def test_negative_prompt_write_path_method():
    d = _t(_RELAY_DIR)
    assert "shot.negative_prompt" in d
    assert "唯一正式负面约束通路" in d
    assert "只写进 trace" in d
    s = _t(_RELAY_SW)
    assert "negative_prompt" in s


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


# ---- 7. 裁定与 relay 语义（Case D/E/F 对应）----
def test_verdict_only_on_actual_tail():
    d = _t(_RELAY_DIR)
    assert "NONE/MINOR/MAJOR 只对【实际生成的尾帧】裁定" in d


def test_intentional_cut_no_forced_relay():
    d = _t(_RELAY_DIR)
    assert "intentional cinematic cut 不强制接续" in d
    k = _t(_RELAY_SKILL)
    assert "先判 continuity intent" in k


def test_workflow_diagram_has_role_classification_step():
    k = _t(_RELAY_SKILL)
    assert "输入角色分类" in k
    assert "pose_reference_mismatch_soft" in k
    assert "negative_prompt" in k
