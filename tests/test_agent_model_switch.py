# -*- coding: utf-8 -*-
"""Agent 模型切换（2026-08-29）测试：默认 Agnes + 滑动开关绑定 SenseNova。

覆盖：
1. ModelProfile.text_provider 字段序列化往返（旧持久化档案无该字段 -> None -> 按 agnes）
2. 默认档案：text_default = Agnes（默认）、text_sensenova_68fl = SenseNova 6.8 Flash Lite；
   任何档案都不引用 sensenova-u1-fast（接口暂不可调用，不提供 UI 档案，配置保留）
3. runtime._resolve_client_or_none(agent_id)：
   - 默认（未绑定）-> Agnes 共享客户端（原有行为不变）
   - 绑定 text_sensenova_68fl -> SenseNova 文本客户端（下一次 turn 起生效）
   - 切回 text_default -> Agnes
   - 注入 client（测试用）优先级最高，不受档案影响
   - SenseNova getter 返回 None（未配置/未就绪）-> 回落 Agnes
   - 其它 Agent（director）不受开关影响
"""
import pytest

from src.agents.models import ModelProfile
from src.agents.runtime import AgentRuntime


class _Fake:
    """可区分的假客户端（只验证 runtime 返回哪一个引用）。"""


@pytest.fixture()
def rt(tmp_path):
    return AgentRuntime(
        agnes_getter=lambda: "AGNES_CLIENT",
        sensenova_getter=lambda: "SENSENOVA_CLIENT",
        settings_path=tmp_path / "settings.json",
    )


# ============ 1. ModelProfile.text_provider ============
def test_model_profile_text_provider_roundtrip():
    p = ModelProfile(id="x", name="x", reasoning_model="m", text_provider="sensenova")
    d = p.to_dict()
    assert d["text_provider"] == "sensenova"
    assert ModelProfile.from_dict(d).text_provider == "sensenova"
    # 旧持久化档案（无该字段）-> None -> 运行时按 agnes
    old = ModelProfile.from_dict({"id": "legacy", "name": "legacy"})
    assert old.text_provider is None


# ============ 2. 默认档案 ============
def test_default_profiles_agnes_first_and_sensenova_optional(rt):
    ids = {p.id: p for p in rt.profiles.list()}
    assert ids["text_default"].text_provider == "agnes"
    # 2026-10-02 用户指令：全 Agent 文本模型切 agnes-3.0-flash（config.agnes.models.text）。
    # 档案的 reasoning_model 是 config 的镜像；这里断言「跟随配置且必须是 agnes 系」，
    # 不再钉死具体版本号（避免每次模型升级都改测试）。
    from deps import _APP_CFG
    expected_text = ((_APP_CFG.get("agnes") or {}).get("models") or {}).get("text")
    assert expected_text and expected_text.startswith("agnes-")
    assert ids["text_default"].reasoning_model == expected_text
    sn = ids["text_sensenova_68fl"]
    assert sn.text_provider == "sensenova"
    assert sn.reasoning_model == "sensenova-6.8-flash-lite"
    # u1-fast 不提供 UI 档案（接口暂不可调用）
    assert not any("u1" in (p.reasoning_model or "") for p in ids.values())


# ============ 3. runtime 按 Agent 绑定档案路由 ============
def test_resolve_default_is_agnes_without_binding(rt):
    assert rt._agent_text_provider("script_writer") == "agnes"
    assert rt._resolve_client_or_none(agent_id="script_writer") == "AGNES_CLIENT"
    # 不传 agent_id 的调用点（其它 agent/聊天）行为不变
    assert rt._resolve_client_or_none() == "AGNES_CLIENT"


def test_resolve_follows_binding_to_sensenova_and_back(rt):
    rt.update_agent_setting("script_writer", profile_id="text_sensenova_68fl")
    assert rt._agent_text_provider("script_writer") == "sensenova"
    assert rt._resolve_client_or_none(agent_id="script_writer") == "SENSENOVA_CLIENT"

    # 切回 Agnes
    rt.update_agent_setting("script_writer", profile_id="text_default")
    assert rt._resolve_client_or_none(agent_id="script_writer") == "AGNES_CLIENT"


def test_resolve_binding_persists_across_runtime_instances(tmp_path):
    rt1 = AgentRuntime(agnes_getter=lambda: "AGNES_CLIENT",
                       sensenova_getter=lambda: "SENSENOVA_CLIENT",
                       settings_path=tmp_path / "settings.json")
    rt1.update_agent_setting("script_writer", profile_id="text_sensenova_68fl")
    # 新实例（模拟重启/下一次 turn 的解析）读到同一绑定
    rt2 = AgentRuntime(agnes_getter=lambda: "AGNES_CLIENT",
                       sensenova_getter=lambda: "SENSENOVA_CLIENT",
                       settings_path=tmp_path / "settings.json")
    assert rt2._resolve_client_or_none(agent_id="script_writer") == "SENSENOVA_CLIENT"


def test_injected_client_wins_over_binding(tmp_path):
    rt = AgentRuntime(client="INJECTED", agnes_getter=lambda: "AGNES_CLIENT",
                      sensenova_getter=lambda: "SENSENOVA_CLIENT",
                      settings_path=tmp_path / "settings.json")
    rt.update_agent_setting("script_writer", profile_id="text_sensenova_68fl")
    assert rt._resolve_client_or_none(agent_id="script_writer") == "INJECTED"


def test_sensenova_unavailable_falls_back_to_agnes(rt, tmp_path):
    rt2 = AgentRuntime(agnes_getter=lambda: "AGNES_CLIENT",
                       sensenova_getter=lambda: None,
                       settings_path=tmp_path / "settings.json")
    rt2.update_agent_setting("script_writer", profile_id="text_sensenova_68fl")
    assert rt2._resolve_client_or_none(agent_id="script_writer") == "AGNES_CLIENT"


def test_other_agents_unaffected_by_switch(rt):
    rt.update_agent_setting("script_writer", profile_id="text_sensenova_68fl")
    # director 未绑定 SenseNova -> 仍走 Agnes 共享客户端
    assert rt._resolve_client_or_none(agent_id="director") == "AGNES_CLIENT"


def test_unknown_profile_binding_rejected(rt):
    from src.agents.runtime import AgentError
    with pytest.raises(AgentError) as ei:
        rt.update_agent_setting("script_writer", profile_id="text_sensenova_u1fast")
    assert ei.value.code == "unknown_model_profile"
