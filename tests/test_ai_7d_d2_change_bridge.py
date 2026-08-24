"""AI-7D D2 · Direct Chat → ChangeSession 桥接后端测试。

测 client_request_id 幂等（create_session 同 id 返回同 session，双击/retry/F5 不造 dup）。
前端 bridgeFromDirectChat（TS）由 vite build + 用户手测覆盖；这里测后端幂等契约。

复用 tests/test_agents.py 的 _proj/_save_proj/_runtime 模式 + cp_service.create_session。
"""
import asyncio

from src.core.store import ProjectStore
from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.agents.runtime import AgentRuntime
from src.agents.change_protocol import service as cp_service


def _proj():
    return Project(
        name="demo", style=StyleAnchor(),
        characters=[Character(name="LinXia", description_en="a young woman")],
        scenes=[Scene(scene_id="s01", summary="rain", characters=["LinXia"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="look up at rain", characters=["LinXia"],
                                  target_duration_s=5)])])


def _save_proj(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _runtime(tmp_path, **kw):
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path, **kw)


def _run(coro):
    return asyncio.run(coro)


# ============ 1. client_request_id 幂等：同 id 再调返回同 session ============
def test_create_session_idempotent_same_client_request_id(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    r1 = cp_service.create_session(rt, "demo", "把中段拓展成中篇", source="direct_agent_chat",
                                    source_agent_id="script_writer", client_request_id="creq_abc")
    assert r1["session"]["status"] == "routing"
    sid1 = r1["session"]["session_id"]
    assert r1["request"]["client_request_id"] == "creq_abc"
    assert "idempotent" not in r1   # 首次创建非幂等返回

    # 同 client_request_id 再调 → 返回同 session，idempotent=True，不造新
    r2 = cp_service.create_session(rt, "demo", "把中段拓展成中篇", source="direct_agent_chat",
                                    source_agent_id="script_writer", client_request_id="creq_abc")
    assert r2["session"]["session_id"] == sid1
    assert r2.get("idempotent") is True


# ============ 2. 不同 client_request_id → 不同 session ============
def test_create_session_distinct_client_request_id(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    r1 = cp_service.create_session(rt, "demo", "改结局", client_request_id="creq_1")
    r2 = cp_service.create_session(rt, "demo", "改中段", client_request_id="creq_2")
    assert r1["session"]["session_id"] != r2["session"]["session_id"]
    assert r1["request"]["client_request_id"] == "creq_1"
    assert r2["request"]["client_request_id"] == "creq_2"


# ============ 3. 无 client_request_id → 正常创建（不查重）============
def test_create_session_no_client_request_id(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    r1 = cp_service.create_session(rt, "demo", "改剧本")
    r2 = cp_service.create_session(rt, "demo", "改剧本")
    assert r1["session"]["session_id"] != r2["session"]["session_id"]
    assert r1["request"]["client_request_id"] is None


# ============ 4. origin metadata 持久化（source/source_agent_id/source_session_id）============
def test_create_session_origin_metadata_persisted(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    r = cp_service.create_session(rt, "demo", "拓展剧本", source="direct_agent_chat",
                                 source_agent_id="script_writer", source_session_id="sess_xx",
                                 client_request_id="creq_org")
    req = r["request"]
    assert req["source"] == "direct_agent_chat"
    assert req["source_agent_id"] == "script_writer"
    assert req["source_session_id"] == "sess_xx"
    assert req["client_request_id"] == "creq_org"
    # context_scope → entity_refs
    r2 = cp_service.create_session(rt, "demo", "改 s01", source="direct_agent_chat",
                                   context_scope={"scene_id": "s01"}, client_request_id="creq_scope")
    assert any(e["entity_id"] == "s01" for e in r2["request"]["entity_refs"])
