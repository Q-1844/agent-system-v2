# tests/test_producer_planning_skill.py
"""PR A：producer-project-planning Skill 包注册与接线诚实性。

Skill 以 Phase49 candidate 身位于 src/agents/skill_system/candidate/：
- 不进入 builtin/ 黄金集，legacy 数量断言零改动；
- 缺省 Registry（生产 runtime）会扫描 candidate/，交互式 Producer 可见；
- PR B1 完成无值守接线后转正 builtin/ 并同步更新 legacy 清单断言。

覆盖执行指令 v2 §7 PR A 必测（skill 部分）：
- manifest 通过 SkillPackage.from_manifest_file 校验，source="builtin" 合法；
- SkillRegistry 注册成功，producer catalog 可见、未绑定 agent 不可见；
- activation_hint 对四类核心任务命中；
- 既有无关文本不误命中（含 legacy 回归：bridge skill 的 producer hint 不受影响）；
- skill.load 可激活 + capability_check 诚实（unknown/ok/missing 三态）；
- role_resource 加载；
- **无值守接线诚实断言**（T12 纪律）：PR A 不宣称无值守链已接线——
  tools/full_project_cloud_acceptance.py 与 workflow yml 中不得出现本 skill id。
  （PR B1 接线后删除本测试。）
"""
from pathlib import Path

from src.agents.skill_system.models import SkillPackage
from src.agents.skill_system.registry import SkillRegistry
from src.agents.skill_system.runtime import SkillRuntime

REPO = Path(__file__).resolve().parent.parent
BUILTIN = REPO / "src" / "agents" / "skill_system" / "builtin"
CANDIDATE = REPO / "src" / "agents" / "skill_system" / "candidate"
SKILL_DIR = CANDIDATE / "producer-project-planning"
SKILL_ID = "producer-project-planning"

_REQUIRED_CAPS = {"project.inspect", "project.quota", "script_writer.delegate"}


def _rt() -> SkillRuntime:
    """隔离构造：只扫 candidate（本 skill 独占视图）。"""
    return SkillRuntime(SkillRegistry(source_dirs=[CANDIDATE]))


def _rt_full() -> SkillRuntime:
    """builtin + candidate 全集（等价缺省扫描，用于 legacy 共存回归）。"""
    return SkillRuntime(SkillRegistry(source_dirs=[BUILTIN, CANDIDATE]))


# ============ manifest 与注册 ============

def test_manifest_loads_and_validates():
    pkg = SkillPackage.from_manifest_file(SKILL_DIR / "skill.json")
    assert pkg.skill_id == SKILL_ID
    assert pkg.source == "builtin"          # 合法枚举（models.SKILL_SOURCES）
    assert pkg.status == "active"
    b = pkg.binding_for("producer") if hasattr(pkg, "binding_for") else None
    # 兼容不同版本 API：直接查 bindings
    if b is None:
        b = next(x for x in pkg.bindings if x.agent_id == "producer")
    assert b.role == "coordinator"
    assert b.role_resource == "references/producer.md"


def test_registry_registers_and_catalog_visible():
    rt = _rt()
    assert rt._registry.get(SKILL_ID) is not None
    catalog = rt.catalog_block("producer")
    assert SKILL_ID in catalog
    # 未绑定 agent 不可见（director 无本 skill 绑定）
    assert SKILL_ID not in rt.catalog_block("director")
    assert SKILL_ID not in rt.catalog_block("script_writer")


# ============ activation_hint：四类核心任务 ============

def test_hint_create_with_duration():
    hint = _rt().activation_hint(
        "producer", "拍一个热气球穿越云海的视频，大概两分钟")
    assert hint is not None and SKILL_ID in hint


def test_hint_create_without_duration():
    hint = _rt().activation_hint("producer", "做个卫星环绕地球的视频")
    assert hint is not None and SKILL_ID in hint


def test_hint_continue_and_add_assets():
    rt = _rt()
    assert SKILL_ID in rt.activation_hint("producer", "继续这个项目，把没做完的做完成")
    assert SKILL_ID in rt.activation_hint("producer", "把没做完的素材补齐")
    assert SKILL_ID in rt.activation_hint("producer", "给这条片子拍一个新续集")


# ============ 误命中防护（producer 视图） ============

def test_no_hint_on_irrelevant_texts():
    rt = _rt()
    for text in ("给这个角色换一套冬装", "把 s03 第2镜改成近景",
                 "把剧本正文压缩到一半", "读一下项目配额"):
        assert rt.activation_hint("producer", text) is None, text


def test_legacy_bridge_hint_unaffected():
    """legacy 回归：bridge 任务的 producer hint 不被本 skill 干扰（全集视图）。"""
    hint = _rt_full().activation_hint("producer", "镜头链断裂需要插桥修复")
    assert hint is not None and "keyframes-bridge-repair" in hint


# ============ skill.load 与能力对账诚实性 ============

def test_skill_load_with_capability_ok(tmp_path):
    out = _rt().load("producer", SKILL_ID, tmp_path, agent_tools=_REQUIRED_CAPS)
    assert out["ok"] is True
    assert out["capability_check"]["status"] == "ok"


def test_skill_load_capability_unknown_when_tools_unknown(tmp_path):
    out = _rt().load("producer", SKILL_ID, tmp_path, agent_tools=None)
    assert out["ok"] is True
    assert out["capability_check"]["status"] == "unknown"


def test_skill_load_capability_missing_is_honest(tmp_path):
    out = _rt().load("producer", SKILL_ID, tmp_path,
                     agent_tools={"project.inspect"})  # 缺其余两个
    assert out["ok"] is True  # 加载成功（只读知识）
    assert out["capability_check"]["status"] == "missing"
    assert set(out["capability_check"]["missing"]) == {
        "project.quota", "script_writer.delegate"}


def test_skill_load_role_resource_and_md(tmp_path):
    out = _rt().load("producer", SKILL_ID, tmp_path, agent_tools=None)
    assert "执行纪律" in out["skill_md"]
    assert out["role_instructions"] and "coordinator" in out["role_instructions"]
    assert "duration-planning" in (out["skill_md"] or "")


def test_read_resource_duration_planning(tmp_path):
    out = _rt().read_resource("producer", SKILL_ID,
                               "references/duration-planning.md", tmp_path)
    assert out["ok"] is True
    assert "SceneBudget" in out["content"]


# 接线诚实断言（T12）已于 PR B1（Stage 0 真接线）后移除：
# full_project_cloud_acceptance.py 已真实 import producer_planning_service，
# "skill 存在但不生效"的守卫使命完成。媒体生产接线（director_note 注入）仍属 PR B2。
