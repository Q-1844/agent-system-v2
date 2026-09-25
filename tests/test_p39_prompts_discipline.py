# Phase 39（39.md §23 测试重点 1/2/3/4/12/13）：提示词纪律集成测试。
# 校准协作的行为约束必须写在提示词里（工具是眼睛，裁定在提示词纪律下运行）。
# 断言用「锚定关键词」而非全文快照——提示词可演进，纪律不可丢失。
from src.agents.character_designer import (
    CHARACTER_DESIGNER_SYSTEM, _build_system as _cd_system)
from src.agents.director import DIRECTOR_SYSTEM
from src.agents.producer import PRODUCER_SYSTEM
from src.agents.registry import _AGENT_SYSTEM_PROMPTS, SPEC_AGENT_IDS
from src.agents.script_writer import SCRIPT_WRITER_SYSTEM


# ============ Director：两种模式的执行主体 ============
def test_director_prompt_relay_calibration_discipline():
    for kw in (
        "tail_to_first_mae", "end_to_end_mae",            # 接缝指标分开（坑 13）
        "1.7-2.4", "26-60", "90+",                        # 三档基线（经验区间）
        "MAE 不是 none/minor/major 分类器",                # §10：MAE 非分类器
        "vision_vs_director_diff",                        # Vision 分歧保存
        "50%+", "unexpected_elements",                    # Vision 已知盲区
        "accept+adapt",                                   # minor 不重试
        "smirk/play-bow",                                 # 强先验吸引子不重试判据
        "calibration.seam_measure", "calibration.trace_read",
        "calibration.trace_upsert", "archive_existing",
        "boundary_frame_stale",                           # trace enum / attempt 归档
        "cascade",                                        # 级联纪律
        "vision_assessment", "root_cause", "retry_or_adapt",  # trace 必存字段
    ):
        assert kw in DIRECTOR_SYSTEM, f"Director 提示词缺少纪律锚点: {kw}"


def test_director_prompt_major_user_gate():
    """major 用户闸门（39.md §11）：Director 不得自行重试，升级 Producer/用户。"""
    assert "你【不得】自行重试" in DIRECTOR_SYSTEM
    assert "单次" in DIRECTOR_SYSTEM                      # 单次锚定重试
    assert "全身端立在土路中央" in DIRECTOR_SYSTEM          # 锚定杠杆实证例句
    assert "重试方差" in DIRECTOR_SYSTEM and "儿童" in DIRECTOR_SYSTEM  # 重试后重查负面约束实证
    assert "不得第二次自行重试" in DIRECTOR_SYSTEM


def test_director_prompt_bridge_discipline():
    """桥纪律（39.md §15-§20）：定位/触发/构造/验收。"""
    for kw in (
        "双端钉死", "keyframes 桥",                        # 桥定位
        "shot.bridge_create", "10s",                      # 构造工具 + 时长下限
        "transition_description",                         # 中间节拍
        "intentional_cut",                                # 有意硬切不算 failure
        "visual.sample_frames",                           # 中间帧逐段检查
        "级联影响=0", "不重生成邻镜",                       # 桥对邻镜原则
        "calibration.seam_measure",                       # 双侧 seam 验收
    ):
        assert kw in DIRECTOR_SYSTEM, f"Director 提示词缺少桥纪律锚点: {kw}"
    assert "接缝修复的标准手段" in DIRECTOR_SYSTEM          # 桥 ≠ retry 的另一种叫法


def test_director_prompt_no_robot_vision_trust():
    """Vision 是证据不是裁定（39.md §13）。"""
    assert "Vision 只是证据不是裁定" in DIRECTOR_SYSTEM
    assert "不要机械服从" in DIRECTOR_SYSTEM


# ============ Producer：编排与用户闸门 ============
def test_producer_prompt_calibration_orchestration():
    for kw in (
        "连续性校准编排",
        "自动继续",                                       # none/minor 自动推进
        "立即停下等用户",                                  # major STOP/WAIT USER
        "为什么判 major", "Vision 说了什么", "当前 seam 数据",   # 报告要素
        "cascade",                                        # cascade 风险报告
        "预计新增视频消耗",                                # 配额代价
        "绝不", "代替用户批准",                            # 不得代批
        "project.quota", "写死",                          # 配额实时读数，不硬编码
    ):
        assert kw in PRODUCER_SYSTEM, f"Producer 提示词缺少编排锚点: {kw}"


# ============ Script Writer：预编剧冻结 + ownership ============
def test_script_writer_prompt_freeze_and_ownership():
    for kw in (
        "预编剧冻结",
        "expected_tail", "duration", "negative constraints",  # 冻结六要素（部分）
        "不根据生成结果偷偷改写",                            # 冻结定义
        "自然承接上一镜有限的位置/姿态/端点偏差",            # minor 吸收设计
        "anchor patch",                                    # major 锚定 patch
        "你没有镜头写工具",                                 # ownership：不越 shot 写权
        "全身端立在土路中央",                               # 收束句尾态实证例句
    ):
        assert kw in SCRIPT_WRITER_SYSTEM, f"Script Writer 提示词缺少锚点: {kw}"


# ============ Character Designer：身份一致性 ============
def test_character_designer_prompt_identity_discipline():
    for kw in (
        "链上身份一致性",
        "identity drift",
        "检查与建议，不自动修改",                           # 默认检查/建议
        "binding 快照失效",                                # 静默改 identity 的代价
        "独立的资源修复任务",                               # 升级路径
    ):
        assert kw in CHARACTER_DESIGNER_SYSTEM, f"CD 提示词缺少锚点: {kw}"


# ============ 名单纪律（39.md §2：只允许四 Agent）============
def test_registry_roster_unchanged_four_agent_collaboration():
    """协作名单锁定 producer/script_writer/character_designer/director；
    其余身份仍在册（identity 勿改）但本阶段禁用——没有任何新身份被加入。"""
    assert SPEC_AGENT_IDS == ["producer"] + [
        "script_writer", "character_designer", "scene_designer", "director",
        "production_planner", "shot_producer", "continuity_reviewer", "editor"]


def test_chat_prompt_stubs_mention_calibration_not_required():
    """chat 桩提示词是简短身份行，校准纪律在原生 tool-loop 模块提示词里；
    桩保持不变（避免 chat 路径与 tool-loop 路径双源漂移）。"""
    assert "calibration" not in _AGENT_SYSTEM_PROMPTS.get("director", "")
    assert "bridge" not in _AGENT_SYSTEM_PROMPTS.get("director", "")
