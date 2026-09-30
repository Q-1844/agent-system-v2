# -*- coding: utf-8 -*-
"""Phase 43 §一步——Skill v1.2 沉淀锚定（42.md §7 判据的 Skill 化 + 系统默认修正）。

锚点对应（43.md 要求）：
  1. SKILL.md 决策入口=简短条目（不堆十二条原文）；判据按适用条件/选择动作/
     禁用条件/失败修复方向组织
  2. 方法细节与反例进 references/production-recipes.md；项目镜头 ID/种子/评语
     不进 Skill 包（留在项目实验档案）
  3. capability-facts 更新：双参考实证状态 + auto→crop 系统默认 + diagnostics 落档
  4. 桥技能：2.5 双帧真机状态 + 修复阶段工具定位
  5. skill.json v1.2.0 经真实注册表可加载
  6. 系统层守卫：resolve_human_reference_mode auto→crop（仅景别驱动）——
     行为测试在 test_scene_designer_agent.py / test_generation_provenance.py
"""
from pathlib import Path

_BUILTIN = (Path(__file__).resolve().parents[1] / "src" / "agents"
            / "skill_system" / "builtin")

_RELAY = "full-story-relay-calibration"
_BRIDGE = "keyframes-bridge-repair"


def _t(rel: str) -> str:
    return (_BUILTIN / rel).read_text(encoding="utf-8")


# ---- 1. SKILL.md 决策入口 ----

def test_p42_criteria_section_in_skill_md():
    s = _t(f"{_RELAY}/SKILL.md")
    assert "定版生产判据" in s
    # 判据 1：特写身份锚定（含 crop 默认与例外）
    assert "特写/中近景身份锚定检查" in s
    assert "auto 已默认 crop" in s
    assert "human_reference_mode" in s and "crop" in s
    # 判据 2：道具绑定（禁用=文本硬写）
    assert "道具入画绑定检查" in s and "状态注记" in s
    # 判据 3：根帧先修再接力 + 同链配方一致
    assert "根帧先修，再接力" in s
    assert "逐镜传播" in s
    assert "两个" in s and "形象" in s
    # 判据 4：修法按空间/身份选择（不是万能修法）
    assert "修法按问题类型选择" in s
    assert "真解除 relay 独立重生" in s or "真解除 + 独立重生" in s or "真解除 relay + 独立重生" in s
    assert "Scene Agent 正式入口" in s
    # 判据 5：动作-时长-场景
    assert "动作-时长-场景容量一起估" in s
    assert "凭空延伸" in s and "如实记录" in s
    # 判据 6：候选不是模型保证 + 用户终审
    assert "不是模型保证" in s or "模型保证" in s
    assert "最终判断" in s


def test_path_e_dual_reference_wired():
    s = _t(f"{_RELAY}/SKILL.md")
    # 路径 e 重写：已接线哨兵 + fail closed + 根帧先修权衡
    assert "prev_tail_look" in s
    assert "结构化拒绝" in s and "fail closed" in s
    assert "根帧有错先修根帧" in s
    assert "未接通" not in s.split("e. Look＋尾帧双参考接力")[1].split("**f.")[0] if "e. Look＋尾帧双参考接力" in s else True


def test_duration_capacity_coupled():
    s = _t(f"{_RELAY}/SKILL.md")
    assert "时长不是自由参数" in s
    assert "凭空延伸" in s
    assert "减秒数 / 增加后续动作 / 并用" in s


# ---- 2. 方法细节在 references，项目细节不进 Skill 包 ----

def test_production_recipes_reference_exists():
    r = _t(f"{_RELAY}/references/production-recipes.md")
    assert "证据边界" in r
    assert "不是模型保证" in r
    # 端点修复矩阵四行
    for kw in ("强化 description", "硬首帧转移", "双参考接力", "真解除 relay"):
        assert kw in r
    # 已知权衡与反例
    assert "根帧有错先修根帧" in r
    assert "裸尾帧单参考接力" in r
    assert "identity_crop_derivation_failed" in r
    # 项目细节不进 Skill 包（43.md：镜头 ID/种子/评语留在项目实验档案）
    assert "s02_c02" not in r and "s01_c04" not in r
    assert "seed 42" not in r and "seed=42" not in r
    assert "vv_" not in r


def test_capability_facts_updated():
    c = _t(f"{_RELAY}/references/capability-facts.md")
    # 双参考行：不再「未验证」——P1 服务端成功+视觉验证（含权衡）
    assert "未验证（待 42.md §四 P1 对照）" not in c
    assert "task_VxgTFsmE" in c
    assert "Look 与尾帧锚定竞争" in c
    # 新守卫：auto→crop 默认翻转 + diagnostics 落档
    assert "resolve_human_reference_mode" in c and "auto→**crop**" in c
    assert "OTS/insert/detail" in c and "drop" in c
    assert "generation_basis.diagnostics" in c


# ---- 4. 桥技能 ----

def test_bridge_skill_p25_status_and_positioning():
    b = _t(f"{_BRIDGE}/SKILL.md")
    assert "2026-09-29" in b and "2.5" in b
    assert "真机探针验证" in b
    assert "修复阶段工具" in b
    assert "普通首次生成一般没有必要插桥" in b
    assert "端点质量与桥无关" in b


# ---- 5. 注册表加载 v1.2.0 ----

def test_registry_loads_v120():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.agents.skill_system.registry import SkillRegistry
    reg = SkillRegistry()
    errs = reg.registry_errors()
    assert not errs, f"注册错误: {errs}"
    relay = reg.skills().get(_RELAY)
    bridge = reg.skills().get(_BRIDGE)
    assert relay is not None and relay.version == "1.2.0"
    assert bridge is not None and bridge.version == "1.2.0"
