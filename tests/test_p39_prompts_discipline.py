# Phase 39（39.md §23）纪律锚定测试 —— 40.md Skill System V1 语义更新：
# 校准/桥协作的 workflow 知识已从各 Agent System Prompt 迁移为 Golden Skill
# （skill.load 按需加载）。本文件现在锚定两条边界：
#   ① workflow 知识【不在】prompt 中（40.md §八 减法；Agent 身份/权限/真相纪律保留）；
#   ② 同样的纪律【完整存在于】skill 包中（迁移 1:1，锚定关键词不丢失）。
# 断言用「锚定关键词」而非全文快照——skill 内容可演进，纪律不可丢失。
from pathlib import Path

from src.agents.character_designer import CHARACTER_DESIGNER_SYSTEM
from src.agents.director import DIRECTOR_SYSTEM
from src.agents.producer import PRODUCER_SYSTEM
from src.agents.registry import _AGENT_SYSTEM_PROMPTS, SPEC_AGENT_IDS
from src.agents.script_writer import SCRIPT_WRITER_SYSTEM
from src.agents.scene_designer import SCENE_DESIGNER_SYSTEM

_SKILL_DIR = (Path(__file__).resolve().parent.parent / "src" / "agents" /
              "skill_system" / "builtin")


def _norm(text: str) -> str:
    """空白归一化：锚定关键词按语义内容比较，不被 markdown 换行排版断开。"""
    return " ".join(text.split())


def _skill_text(skill_id: str, resource: str = None) -> str:
    pkg = _SKILL_DIR / skill_id
    text = (pkg / "SKILL.md").read_text(encoding="utf-8")
    if resource:
        text += "\n" + (pkg / resource).read_text(encoding="utf-8")
    return _norm(text)


def _calibration_all() -> str:
    """calibration 技能全部文本（SKILL.md + 全部 role resources）。"""
    pkg = _SKILL_DIR / "full-story-relay-calibration"
    parts = [(pkg / "SKILL.md").read_text(encoding="utf-8")]
    for md in sorted((pkg / "references").glob("*.md")):
        parts.append(md.read_text(encoding="utf-8"))
    return _norm("\n".join(parts))


# ============ Director：校准 workflow 已迁移到 skill（prompt 减法 + skill 完整）============
def test_director_calibration_knowledge_in_skill_not_prompt():
    workflow_kws = (
        "tail_to_first_mae", "end_to_end_mae",            # 接缝指标分开（坑 13）
        "1.7-2.4", "26-60", "90+",                        # 三档基线（经验区间）
        "MAE 不是 none/minor/major 分类器",                # §10：MAE 非分类器
        "vision_vs_director_diff",                        # Vision 分歧保存
        "50%+", "unexpected_elements",                    # Vision 已知盲区
        "accept+adapt",                                   # minor 不重试
        "smirk/play-bow",                                 # 强先验吸引子不重试判据
        "calibration.seam_measure", "calibration.trace_read",
        "calibration.trace_upsert", "archive_existing",
        "cascade",                                        # 级联纪律
        "vision_assessment", "root_cause", "retry_or_adapt",  # trace 必存字段
        "scene_production_prerequisite",                  # 场景侧根因路由
        "三档裁定",                                        # 裁定模型
    )
    # ① prompt 中不再有 workflow 教学（40.md §八：从 System Prompt 删除重复 workflow）
    for kw in workflow_kws:
        assert kw not in _norm(DIRECTOR_SYSTEM), f"workflow 锚点应迁移出 prompt: {kw}"
    # ② 全部纪律在 calibration skill 包中完整存在
    cal = _calibration_all()
    for kw in workflow_kws:
        assert kw in cal, f"calibration skill 缺少纪律锚点: {kw}"


def test_director_major_user_gate_in_skill():
    """major 回正制度（P39D 用户定版）：镜内失败=单次锚定重试；尾态偏离=插曲；
    真实 blocker 才升级用户。旧"一律停下等用户批准重试"已被插曲制替代。"""
    cal = _calibration_all()
    assert "镜内失败" in cal and "单次】锚定重试" in cal      # 镜内失败 → 单次重试
    assert "尾态偏离" in cal and "插曲方案" in cal            # 尾态偏离 → 插曲（不重试）
    assert "剧本与故事段" in cal                              # 剧本/段不变
    assert "重试方差" in cal and "儿童" in cal                # 重试后重查负面约束实证
    assert "不得第二次自行重试" in cal
    # 减法验证：workflow 闸门教学不在 prompt（major 行为由 skill 承载）
    assert "全身端立在土路中央" not in _norm(DIRECTOR_SYSTEM)


def test_director_bridge_knowledge_in_skill():
    """桥纪律（39.md §15-§20）：定位/触发/构造/验收 → keyframes-bridge-repair。"""
    bridge_kws = (
        "双端钉死", "keyframes 桥",                        # 桥定位
        "shot.bridge_create", "10s",                      # 构造工具 + 时长下限
        "transition_description",                         # 中间节拍
        "intentional_cut",                                # 有意硬切不算 failure
        "visual.sample_frames",                           # 中间帧逐段检查
        "级联影响=0", "不重生成邻镜",                       # 桥对邻镜原则
        "calibration.seam_measure",                       # 双侧 seam 验收
        "接缝修复的标准手段",                               # 桥 ≠ retry 的另一种叫法
        "boundary_frame_stale",                           # seed 后守卫
        "bridge_frame_check",                             # 帧检证据落 trace
    )
    for kw in bridge_kws:
        assert kw not in _norm(DIRECTOR_SYSTEM), f"桥 workflow 锚点应迁移出 prompt: {kw}"
    bridge = _skill_text("keyframes-bridge-repair", "references/director.md")
    for kw in bridge_kws:
        assert kw in bridge, f"bridge skill 缺少纪律锚点: {kw}"


def test_vision_is_evidence_not_verdict_in_skill():
    """Vision 是证据不是裁定（39.md §13）。"""
    cal = _calibration_all()
    assert "Vision 只是证据不是裁定" in cal
    assert "不要机械服从" in cal
    assert "Vision 只是证据不是裁定" not in _norm(DIRECTOR_SYSTEM)


# ============ Producer：编排与用户闸门 → references/producer.md ============
def test_producer_orchestration_in_skill_not_prompt():
    kws = (
        "默认 active set 是 4",                            # 条件参与：4-Agent 默认集
        "按【场景依赖条件】参与",                           # Scene Agent 条件参与
        "none/minor",                                     # 自动推进档位（校准专属词）
        "插曲编排",                                        # 尾态大偏离 → 插曲（P39D）
        "插曲无法回正",                                    # 升级用户的边界（P39D）
        "最终统计必须读 trace 实况",                        # 汇报一致性（P39D t7 实证）
        "cascade",                                        # cascade 风险报告
        "project.quota", "写死",                          # 配额实时读数，不硬编码
    )
    for kw in kws:
        assert kw not in _norm(PRODUCER_SYSTEM), f"编排 workflow 锚点应迁移出 prompt: {kw}"
        assert kw in _skill_text("full-story-relay-calibration",
                                 "references/producer.md"), \
            f"producer.md 缺少编排锚点: {kw}"


# ============ Script Writer：预编剧冻结 + ownership → references/script_writer.md ============
def test_script_writer_freeze_in_skill_not_prompt():
    kws = (
        "预编剧冻结",
        "expected_tail", "duration", "negative constraints",  # 冻结六要素（部分）
        "不根据生成结果偷偷改写",                            # 冻结定义
        "自然承接上一镜有限的位置/姿态/端点偏差",            # minor 吸收设计
        "anchor patch",                                    # major 锚定 patch
        "你没有镜头写工具",                                 # ownership：不越 shot 写权
        "全身端立在土路中央",                               # 收束句尾态实证例句
    )
    for kw in kws:
        assert kw not in _norm(SCRIPT_WRITER_SYSTEM), \
            f"预编剧 workflow 锚点应迁移出 prompt: {kw}"
        assert kw in _skill_text("full-story-relay-calibration",
                                 "references/script_writer.md"), \
            f"script_writer.md 缺少锚点: {kw}"


# ============ Character Designer：身份一致性 → references/character_designer.md ============
def test_character_identity_discipline_in_skill_not_prompt():
    kws = (
        "链上身份一致性",
        "identity drift",
        "检查与建议，不自动修改",                           # 默认检查/建议
        "binding 快照失效",                                # 静默改 identity 的代价
        "独立的资源修复任务",                               # 升级路径
    )
    for kw in kws:
        assert kw not in _norm(CHARACTER_DESIGNER_SYSTEM), \
            f"身份一致性 workflow 锚点应迁移出 prompt: {kw}"
        assert kw in _skill_text("full-story-relay-calibration",
                                 "references/character_designer.md"), \
            f"character_designer.md 缺少锚点: {kw}"


# ============ Scene Designer：条件参与上游 owner → references/scene_designer.md ============
def test_scene_conditional_role_in_skill_not_prompt():
    kws = (
        "默认 active set 是 4",
        "按【场景依赖条件】加入",                          # 条件参与（校准专属）
        "SIV stale",
        "scene_production_prerequisite",
        "binding refresh 不是你的工具",
        "接缝测量、校准 trace、三档裁定、接缝桥都【不属于你】",
        "绝不修改场景 canonical 去迁就某一镜的画面",
        # 注意：resource_prerequisite 同时是 Scene 身份级纪律词（不做 Prop 的
        # 边界，保留在 prompt【不做混淆的铁律】段），不作减法锚点。
    )
    for kw in kws:
        assert kw not in _norm(SCENE_DESIGNER_SYSTEM), \
            f"条件参与 workflow 锚点应迁移出 prompt: {kw}"
        assert kw in _skill_text("full-story-relay-calibration",
                                 "references/scene_designer.md"), \
            f"scene_designer.md 缺少锚点: {kw}"


# ============ 永久身份/权限/真相纪律仍在 prompt（减法边界：这些不迁移）============
def test_permanent_identity_and_truth_discipline_kept_in_prompt():
    """40.md §八：Agent Base System 保留 Role/Ownership/Truth discipline/
    Tool usage fundamentals——减法只删 workflow，不删身份与硬约束。"""
    for kw in ("权限边界（硬约束）", "你【只】写镜头 canonical",
               "接力（inherit_previous/接力镜头）是【动作与时空的连续】",
               "不要编造 shot_id", "版本指纹"):
        assert kw in DIRECTOR_SYSTEM, f"Director 身份/纪律锚点丢失: {kw}"
    for kw in ("【身份与权限】", "你永远不直接修改任何 canonical",
               "【硬边界（物理保证，不是提示词约束）】"):
        assert kw in PRODUCER_SYSTEM, f"Producer 身份/纪律锚点丢失: {kw}"
    for kw in ("不创建或修改镜头", "🔴 最重要的铁律（违反即失败）"):
        assert kw in SCRIPT_WRITER_SYSTEM, f"SW 身份/纪律锚点丢失: {kw}"
    for kw in ("Resource Agent", "生产视觉资源"):
        assert kw in CHARACTER_DESIGNER_SYSTEM, f"CD 身份锚点丢失: {kw}"
    for kw in ("Scene Agent", "你【只写你的域】"):
        assert kw in SCENE_DESIGNER_SYSTEM, f"Scene 身份锚点丢失: {kw}"


# ============ 名单纪律（用户 2026-09-25 定版：5-Agent roster + 条件参与）============
def test_registry_roster_five_agent_conditional():
    """完整生产协作 roster = 5 Agent；两种校准模式默认 active set = 4；
    scene_designer 条件参与（合法可路由，永不拿校准工具——行为锚定见
    test_p39_agent_tools.py::test_scene_designer_conditional_fifth_member）。
    registry 固定 8 身份不变（identity 勿改纪律）。Skill 名册（40.md）：
    calibration 绑 5 Agent、bridge 只绑 director/producer——与 roster 语义一致。"""
    # Phase 44B：assembly 加法式加入（成片装配 Specialist，无任何 Skill 绑定）
    assert SPEC_AGENT_IDS == ["producer"] + [
        "script_writer", "character_designer", "scene_designer", "director",
        "production_planner", "shot_producer", "continuity_reviewer", "editor",
        "assembly"]
    from src.agents.skill_system.registry import get_skill_registry
    r = get_skill_registry()
    cal = r.get("full-story-relay-calibration")
    assert {b.agent_id for b in cal.bindings} == {
        "producer", "script_writer", "character_designer",
        "scene_designer", "director"}
    bridge = r.get("keyframes-bridge-repair")
    assert {b.agent_id for b in bridge.bindings} == {"director", "producer"}
    # 禁用身份纪律延续：四个非活跃身份无任何 skill 绑定；assembly（44B 新增）同样零绑定
    for disabled in ("production_planner", "shot_producer",
                     "continuity_reviewer", "editor", "assembly"):
        assert r.view_for(disabled) == []


def test_chat_prompt_stubs_mention_calibration_not_required():
    """chat 桩提示词是简短身份行，校准纪律在原生 tool-loop 模块提示词/skill 里；
    桩保持不变（避免 chat 路径与 tool-loop 路径双源漂移）。"""
    assert "calibration" not in _AGENT_SYSTEM_PROMPTS.get("director", "")
    assert "bridge" not in _AGENT_SYSTEM_PROMPTS.get("director", "")
