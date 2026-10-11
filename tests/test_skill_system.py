# tests/test_skill_system.py
"""Skill System V1（40.md §十五）Phase A — Architecture / Unit。

覆盖：registry / package validation / binding / per-agent filtering / priority /
authority / version / enable-disable / invalid package fail closed /
capability dependency / progressive loading / trace / user-extension trust boundary。
"""
import json
import time
from pathlib import Path

import pytest

from src.agents.skill_system.models import (
    AgentSkillBinding, SkillPackage, SkillValidationError,
)
from src.agents.skill_system.registry import SkillRegistry
from src.agents.skill_system.runtime import SkillRuntime

BUILTIN = Path(__file__).resolve().parent.parent / "src" / "agents" / \
    "skill_system" / "builtin"


def _registry() -> SkillRegistry:
    return SkillRegistry(source_dirs=[BUILTIN])


def _write_pkg(root: Path, dir_name: str, manifest: dict,
               skill_md: str = "# 测试技能\n\n内容。" * 5,
               references: dict = None) -> Path:
    """测试工具：在临时目录构造一个 skill 包。"""
    pkg = root / dir_name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "skill.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (pkg / "SKILL.md").write_text(skill_md, encoding="utf-8")
    for rel, text in (references or {}).items():
        rp = pkg / rel
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(text, encoding="utf-8")
    return pkg


def _manifest(**over) -> dict:
    base = {
        "skill_id": "test-skill", "version": "1.1.0", "name": "测试技能",
        "description": "测试用技能", "category": "workflow",
        "source": "builtin_core", "status": "active",
        "triggers": ["测试"], "applicability": "测试情境",
        "required_capabilities": {}, "depends_on": [],
        "bindings": [{"agent_id": "director", "role": "executor",
                      "routing_priority": 50, "authority_tier": "builtin_core",
                      "activation_mode": "hinted"}],
    }
    base.update(over)
    return base


# ============ Registry / 加载 ============

def test_builtin_registry_loads_two_golden_skills():
    r = _registry()
    skills = r.skills()
    assert set(skills) == {"full-story-relay-calibration", "keyframes-bridge-repair"}
    assert r.registry_errors() == {}
    pkg = skills["full-story-relay-calibration"]
    assert pkg.version == "1.6.0" and pkg.category == "workflow"
    assert pkg.source == "builtin_core"


def test_registry_mtime_cache_and_reload(tmp_path):
    _write_pkg(tmp_path, "s1", _manifest())
    r = SkillRegistry(source_dirs=[tmp_path])
    assert set(r.skills()) == {"test-skill"}
    # 新增包后自动发现（mtime 签名变化）
    time.sleep(0.02)
    _write_pkg(tmp_path, "s2", _manifest(skill_id="second-skill"))
    assert set(r.skills()) == {"test-skill", "second-skill"}


# ============ Package validation（fail closed）============

def test_invalid_manifest_fail_closed(tmp_path):
    # skill_id 非法
    _write_pkg(tmp_path, "bad1", _manifest(skill_id="BAD_ID"))
    # version 非法
    _write_pkg(tmp_path, "bad2", _manifest(skill_id="ok-skill", version="v1"))
    # category 非法
    _write_pkg(tmp_path, "bad3", _manifest(skill_id="ok2", category="unknown"))
    # bindings 为空
    _write_pkg(tmp_path, "bad4", _manifest(skill_id="ok3", bindings=[]))
    # binding agent 非法
    _write_pkg(tmp_path, "bad5", _manifest(
        skill_id="ok4",
        bindings=[{"agent_id": "not_an_agent", "role": "executor"}]))
    # routing_priority 越界
    _write_pkg(tmp_path, "bad6", _manifest(
        skill_id="ok5",
        bindings=[{"agent_id": "director", "role": "executor",
                   "routing_priority": 101}]))
    # 合法对照
    _write_pkg(tmp_path, "good", _manifest(skill_id="good-skill"))
    r = SkillRegistry(source_dirs=[tmp_path])
    assert set(r.skills()) == {"good-skill"}          # 仅合法包注册
    errs = r.registry_errors()
    assert len(errs) == 6                              # 6 个坏包全部 fail closed
    assert all(isinstance(v, str) and v for v in errs.values())


def test_missing_skill_md_fail_closed(tmp_path):
    pkg = tmp_path / "nomd"
    pkg.mkdir(parents=True)
    (pkg / "skill.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    r = SkillRegistry(source_dirs=[tmp_path])
    assert r.skills() == {}
    assert "nomd" in r.registry_errors()


def test_declared_role_resource_must_exist(tmp_path):
    _write_pkg(tmp_path, "s1", _manifest(
        bindings=[{"agent_id": "director", "role": "executor",
                   "role_resource": "references/ghost.md"}]))
    r = SkillRegistry(source_dirs=[tmp_path])
    assert r.skills() == {}
    assert "s1" in r.registry_errors()


def test_duplicate_skill_id_fail_closed(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_pkg(a, "pkg_a", _manifest(skill_id="dup"))
    _write_pkg(b, "pkg_b", _manifest(skill_id="dup"))
    r = SkillRegistry(source_dirs=[a, b])
    assert len(r.skills()) == 1          # 先注册者胜出，后到者拒绝
    assert "dup" in r.registry_errors()


def test_version_and_id_format():
    with pytest.raises(SkillValidationError):
        AgentSkillBinding.from_dict(
            {"agent_id": "director", "routing_priority": -1}, "s")


# ============ Binding / per-agent filtering / priority / authority ============

def test_per_agent_view_filtering_and_priority():
    r = _registry()
    d_view = [p.skill_id for p, _ in r.view_for("director")]
    assert d_view == ["full-story-relay-calibration", "keyframes-bridge-repair"]
    p_view = [p.skill_id for p, _ in r.view_for("producer")]
    assert p_view == ["full-story-relay-calibration", "keyframes-bridge-repair"]
    # routing_priority 排序：director 100/90，producer 80/40
    assert [b.routing_priority for _, b in r.view_for("director")] == [100, 90]
    assert [b.routing_priority for _, b in r.view_for("producer")] == [80, 40]
    # 其他 agent 只看到 calibration（bridge 未绑定）
    for a in ("script_writer", "character_designer", "scene_designer"):
        assert [p.skill_id for p, _ in r.view_for(a)] == \
            ["full-story-relay-calibration"]
    # 无绑定 agent 视图空
    assert r.view_for("editor") == []
    # 同 skill 不同 agent 的 role 不同（重要度是 Agent×Skill 关系属性）
    b_d = r.binding_for("director", "full-story-relay-calibration")
    b_p = r.binding_for("producer", "full-story-relay-calibration")
    b_s = r.binding_for("scene_designer", "full-story-relay-calibration")
    assert (b_d.role, b_p.role, b_s.role) == \
        ("primary_executor", "coordinator", "conditional")
    # routing_priority（选择）与 authority_tier（冲突解析）是两个独立字段
    assert b_d.routing_priority != b_p.routing_priority
    assert b_d.authority_tier == b_p.authority_tier == "builtin_core"


def test_enable_disable(tmp_path):
    _write_pkg(tmp_path, "on", _manifest(skill_id="on-skill"))
    _write_pkg(tmp_path, "off", _manifest(skill_id="off-skill", status="disabled"))
    _write_pkg(tmp_path, "nobind", _manifest(
        skill_id="nobind-skill",
        bindings=[{"agent_id": "director", "role": "executor", "enabled": False}]))
    r = SkillRegistry(source_dirs=[tmp_path])
    assert r.binding_for("director", "on-skill") is not None
    assert r.binding_for("director", "off-skill") is None     # 包级 disabled
    assert r.binding_for("director", "nobind-skill") is None  # binding disabled
    view = [p.skill_id for p, _ in r.view_for("director")]
    assert view == ["on-skill"]


def test_user_extension_default_lowest_trust(tmp_path):
    """40.md §十四：用户上传 skill 未来兼容——source=user 默认最低 tier；
    V1 只验证 schema 边界与加载语义（不做 UI）。"""
    _write_pkg(tmp_path, "u1", _manifest(
        skill_id="user-skill", source="user",
        bindings=[{"agent_id": "director", "role": "contributor",
                   "authority_tier": "user", "routing_priority": 95}]))
    r = SkillRegistry(source_dirs=[tmp_path])
    b = r.binding_for("director", "user-skill")
    assert b is not None and b.authority_tier == "user"
    rt = SkillRuntime(r)
    out = rt.load("director", "user-skill", agent_tools=None)
    assert out["ok"] and out["authority_tier"] == "user"
    # 用户 skill 可被高 routing_priority 主动选择，但 authority_notice 声明
    # 不得覆盖 system/ownership/OCC/审批/truth gate（§五边界）
    assert "不授予任何新权限" in out["authority_notice"]
    assert "truth gate" in out["authority_notice"]


# ============ Progressive Disclosure（L1/L2/L3）============

def test_progressive_loading_levels():
    rt = SkillRuntime(_registry())
    # L1：catalog 只含少量 metadata，不含 SKILL.md 主体
    catalog = rt.catalog_block("director")
    assert "【你的技能目录】" in catalog
    assert "full-story-relay-calibration" in catalog
    assert "## 决策模型" not in catalog          # L2 主体不在 L1
    # L2：load 返回 SKILL.md + 本 agent role 指令；其余资源仅列名
    out = rt.load("director", "full-story-relay-calibration", agent_tools=None)
    assert out["ok"] and "## 决策模型" in out["skill_md"]
    assert "primary_executor" in out["role_instructions"]
    assert "references/producer.md" in out["available_resources"]
    # 不默认加载其他 agent 的 role 文件
    assert "producer.md" not in (out["role_instructions"] or "")
    # L3：按需读取
    res = rt.read_resource("director", "full-story-relay-calibration",
                           "references/producer.md")
    assert res["ok"] and "coordinator" in res["content"]
    # role resource 随 agent 切换（同一个 canonical package 不复制）
    out_sw = rt.load("script_writer", "full-story-relay-calibration",
                     agent_tools=None)
    assert "预编剧冻结" in out_sw["role_instructions"]
    assert "primary_executor" not in (out_sw["role_instructions"] or "")


def test_load_fail_closed_variants():
    rt = SkillRuntime(_registry())
    assert rt.load("director", "nope", agent_tools=None)["code"] == "unknown_skill"
    out = rt.load("scene_designer", "keyframes-bridge-repair", agent_tools=None)
    assert not out["ok"] and out["code"] == "skill_not_visible"
    # 路径穿越防护
    out2 = rt.read_resource("director", "keyframes-bridge-repair", "../SKILL.md")
    assert not out2["ok"] and out2["code"] == "unknown_resource"


def test_capability_dependency_check(tmp_path):
    rt = SkillRuntime(_registry())
    # director 真实工具面：required 全部在位
    from src.agents.skill_system.tools import agent_tool_names
    out = rt.load("director", "full-story-relay-calibration",
                  agent_tools=agent_tool_names("director"))
    cap = out["capability_check"]
    assert cap["status"] == "ok" and cap["missing"] == []
    # 构造声称需要越权工具的包：对账 missing + fail/delegate 指引
    _write_pkg(tmp_path, "greedy", _manifest(
        skill_id="greedy-skill",
        required_capabilities={"scene_designer": ["calibration.seam_measure",
                                                  "shot.update"]},
        bindings=[{"agent_id": "scene_designer", "role": "executor"}]))
    rt2 = SkillRuntime(SkillRegistry(source_dirs=[tmp_path]))
    out2 = rt2.load("scene_designer", "greedy-skill",
                    agent_tools=agent_tool_names("scene_designer"))
    cap2 = out2["capability_check"]
    assert cap2["status"] == "missing"
    assert "calibration.seam_measure" in cap2["missing"]
    assert "fail 或委派" in out2["note"]
    # 无法确定工具面时 unknown（物理工具面仍是真权限层）
    out3 = rt.load("director", "full-story-relay-calibration", agent_tools=None)
    assert out3["capability_check"]["status"] == "unknown"


# ============ Trace（40.md §十三）============

def test_structured_trace_jsonl(tmp_path):
    rt = SkillRuntime(_registry())
    rt.activation_hint("director", "对整条故事做连续性校准", tmp_path)
    rt.load("director", "full-story-relay-calibration", tmp_path,
            agent_tools=None, task_snippet="校准任务")
    rt.read_resource("director", "keyframes-bridge-repair",
                     "references/director.md", tmp_path)
    rt.load("scene_designer", "keyframes-bridge-repair", tmp_path)  # 失败事件
    tj = tmp_path / "output" / "agents" / "skills" / "trace.jsonl"
    assert tj.is_file()
    events = [json.loads(l) for l in tj.read_text(encoding="utf-8").splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds == ["hint", "load", "resource", "load_fail"]
    load_ev = events[1]
    assert load_ev["skill_id"] == "full-story-relay-calibration"
    assert load_ev["version"] == "1.6.0"
    assert load_ev["agent_id"] == "director"
    assert load_ev["detail"]["role"] == "primary_executor"
    assert load_ev["detail"]["capability"]["status"] == "unknown"
    # 可回答：看到了哪些 metadata（catalog）/ 为什么被选择（hinted）/
    #   哪个版本 / 什么 role / 哪些 resources / 成功失败
    assert "catalog" in events[0]["detail"]
    assert events[0]["detail"]["hinted"] == ["full-story-relay-calibration"]
    fail_ev = events[3]
    assert fail_ev["ok"] is False and fail_ev["detail"]["code"] == "skill_not_visible"


def test_turn_collect_and_reset(tmp_path):
    rt = SkillRuntime(_registry())
    rt.activation_hint("director", "校准", tmp_path)
    rt.load("director", "keyframes-bridge-repair", tmp_path, agent_tools=None)
    recs = rt.collect_and_reset("director")
    assert [r["event"] for r in recs] == ["hint", "load"]
    assert rt.collect_and_reset("director") == []     # 已清空
    # 跨 agent 隔离
    rt.load("producer", "keyframes-bridge-repair", tmp_path, agent_tools=None)
    assert len(rt.collect_and_reset("producer")) == 1
    assert rt.collect_and_reset("director") == []
