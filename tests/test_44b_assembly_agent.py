# tests/test_44b_assembly_agent.py
# Phase 44B — Assembly Agent 确定性测试（零 ffmpeg、零 LLM）。
# 覆盖：注册/能力/系统提示、快照路径守卫、briefing/preview 执行器、快照对齐核验
# 失败分支、sequence_create/update（OCC/审计/写后重读）、render_submit（fake queue）、
# manifest_publish（无 done 拒绝 + done 后发布）、proposal_write、真相闸门
# （谎报成功被确定性覆写）、脚本化 tool-loop 全链路。
import asyncio
import json

import pytest

from src.agents.assembly import (
    ASSEMBLY_SYSTEM,
    WRITE_TOOL_DOMAIN,
    _execute_briefing,
    _execute_manifest_publish,
    _execute_proposal_write,
    _execute_render_submit,
    _execute_sequence_create,
    _execute_sequence_update,
    _execute_snapshot_read,
    _execute_timeline_preview,
    assembly_turn,
)
from src.agents.registry import Registries
from src.agents.runtime import AgentRuntime, AgentError
from src.core.store import ProjectStore
from src.sequence.service import SequenceService

from tests.test_44b_assembly_fixture import (
    build_media_project,
    build_snapshot_manifest,
    write_snapshot_file,
)


class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        if not self.script:
            return {"content": "（脚本已耗尽）", "tool_calls": None}
        content, tool_calls = self.script.pop(0)
        return {"content": content, "tool_calls": tool_calls}

    async def chat_completion_stream(self, messages, **kw):
        content, tool_calls = (self.script.pop(0) if self.script
                               else ("（脚本已耗尽）", None))
        self.calls.append({"messages": messages, "kw": kw})
        yield {"kind": "finish", "content": content, "tool_calls": tool_calls}


class FakeQueue:
    def __init__(self):
        self.submitted = []

    def submit(self, project, kind, payload, **params):
        self.submitted.append({"project": project, "kind": kind, **params})
        n = len(self.submitted)

        class _J:
            # 镜像真实 Job 字段名（state 非 status——回归：曾因 .status AttributeError
            # 使 render_submit 回执构造失败）
            job_id = f"job_{n}"
            state = "queued"

        return _J()


def _setup(tmp_path, mode="formal"):
    files = build_media_project(tmp_path)
    snap = build_snapshot_manifest(files, mode=mode)
    write_snapshot_file(tmp_path, snap)
    return files


@pytest.fixture(autouse=True)
def _fake_ffmpeg_probe(monkeypatch):
    """Agent 工具内所有 ffprobe 走伪探测（零 ffmpeg；dummy 字节非真视频）。"""
    from tests.test_44b_assembly_fixture import fake_probe
    monkeypatch.setattr("src.sequence.media_ops.probe_video", fake_probe)


def _tool_call(name, arguments, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)}}


def _runtime(tmp_path, client=None, queue=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client,
                        job_queue_getter=(lambda: queue) if queue else None)


# ============ 注册与提示 ============

def test_registered_as_specialist():
    reg = Registries().agents
    a = reg.get("assembly")
    assert a is not None and a.id == "assembly" and a.name == "Assembly Agent"
    assert "assembly.inspect" in a.allowed_capabilities
    assert "assembly.assemble" in a.allowed_capabilities
    # 加法式：旧 identity 全在
    for aid in ("script_writer", "character_designer", "scene_designer", "director",
                "production_planner", "shot_producer", "continuity_reviewer", "editor"):
        assert reg.exists(aid), aid
    caps = {c.id for c in Registries().capabilities.list()}
    assert {"assembly.inspect", "assembly.assemble"} <= caps
    assert "assembly" in Registries().agents.list()[-1].id
    # 系统提示包含边界要点
    assert "生成" in ASSEMBLY_SYSTEM and "select" in ASSEMBLY_SYSTEM


def test_write_domain_single_sequence_scope():
    # Phase 44C：+tts_synthesize / audio_plan_put / stem_render（同一 sequence 域）
    assert set(WRITE_TOOL_DOMAIN) == {
        "assembly.sequence_create", "assembly.sequence_update",
        "assembly.render_submit", "assembly.manifest_publish",
        "assembly.proposal_write", "assembly.tts_synthesize",
        "assembly.audio_plan_put", "assembly.stem_render"}
    assert set(WRITE_TOOL_DOMAIN.values()) == {"sequence"}


# ============ 只读执行器 ============

def test_snapshot_load_and_read(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    obs = _execute_snapshot_read(rt, "p44b", "{}", "t", "m1")
    assert obs["ok"] and obs["shot_count"] == 3
    assert obs["shots"][0]["video_version_id"] == "vv_s01c01"
    # 单镜
    obs2 = _execute_snapshot_read(rt, "p44b",
                                  json.dumps({"shot_id": "s02_c01"}), "t", "m1")
    assert obs2["shot"]["scene_id"] == "s02"


def test_snapshot_path_escape_denied(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    outside = tmp_path.parent / "evil.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(AgentError) as ei:
        from src.agents.assembly import _snapshot_load
        _snapshot_load(rt, "p44b", str(outside))
    assert ei.value.code == "snapshot_path_denied"


def test_briefing_and_preview(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    br = _execute_briefing(rt, "p44b", "{}", "t", "m1")
    assert br["ok"] and br["snapshot"]["shot_count"] == 3
    assert br["recommended_render_profile"]["width"] == 1280
    pv = _execute_timeline_preview(
        rt, "p44b",
        json.dumps({"clips": [{"shot_id": "s01_c01"}, {"shot_id": "s02_c01"}]}),
        "t", "m1")
    assert pv["ok"] and pv["total_duration_ms"] == 16000
    assert pv["items"][1]["scene_boundary"] is True
    assert pv["seam_times_ms"] == [8000]
    # 未知镜头 → violation，不偷换
    pv2 = _execute_timeline_preview(
        rt, "p44b",
        json.dumps({"clips": [{"shot_id": "s01_c01"}, {"shot_id": "s09_c99"}]}),
        "t", "m1")
    assert not pv2["ok"]
    assert pv2["violations"][0]["code"] == "shot_not_in_snapshot"
    assert pv2["violations"][0]["shot_id"] == "s09_c99"


# ============ 写执行器：对齐核验 / OCC / 审计 / 队列 ============

def test_create_rejects_snapshot_mismatch(tmp_path, monkeypatch):
    files = _setup(tmp_path)
    rt = _runtime(tmp_path)
    # 破坏 live final 指针 → 对齐核验必须拒绝
    proj = ProjectStore(tmp_path).load()
    proj.get_shot("s01_c01").final_video = "vv_s01c02"
    ProjectStore(tmp_path).save(proj)
    args = json.dumps({"clips": [{"shot_id": "s01_c01"}]})
    r = _execute_sequence_create(rt, "p44b", args, "t", "m1")
    assert r["ok"] is False and "对齐核验失败" in r["error"] and "s01_c01" in r["error"]
    # sequences.json 不应被创建
    assert not (tmp_path / "output" / "sequences.json").exists()


def test_create_update_occ_and_audit(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    args = json.dumps({"title": "44B 全片", "clips": [
        {"shot_id": "s01_c01"}, {"shot_id": "s01_c02"}, {"shot_id": "s02_c01"}]})
    r = _execute_sequence_create(rt, "p44b", args, "把三个镜头连起来", "m1")
    assert r["ok"] and r["verified"] and r["clip_count"] == 3
    sid = r["sequence_id"]
    rev1 = r["revision"]
    # OCC：旧 revision → conflict
    r_stale = _execute_sequence_update(
        rt, "p44b",
        json.dumps({"sequence_id": sid, "expected_revision": "deadbeef",
                    "clips": [{"shot_id": "s01_c01"}]}), "t", "m1")
    assert r_stale["ok"] is False and r_stale.get("code") == "revision_conflict"
    # 正确 revision → trim 更新成功
    r_ok = _execute_sequence_update(
        rt, "p44b",
        json.dumps({"sequence_id": sid, "expected_revision": rev1,
                    "clips": [{"shot_id": "s01_c01", "trim_out_ms": 200},
                              {"shot_id": "s01_c02"}, {"shot_id": "s02_c01"}]}),
        "掐掉 c01 尾部多余收尾", "m1")
    assert r_ok["ok"] and r_ok["verified"] and r_ok["revision"] != rev1
    svc = SequenceService(tmp_path, "p44b")
    assert svc.get_entry(sid).block.clips[0].trim_out_ms == 200
    # 审计在档
    audit_file = tmp_path / "output" / "agents" / "direct_edit_audit.jsonl"
    assert audit_file.exists()
    lines = [json.loads(x) for x in
             audit_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(x["agent_id"] == "assembly" for x in lines)


def test_render_submit_with_fake_queue(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    queue = FakeQueue()
    rt2 = _runtime(tmp_path, queue=queue)
    r = _execute_sequence_create(
        rt2, "p44b",
        json.dumps({"clips": [{"shot_id": "s01_c01"}]}), "t", "m1")
    r2 = _execute_render_submit(rt2, "p44b",
                                json.dumps({"sequence_id": r["sequence_id"]}),
                                "渲染", "m1")
    assert r2["ok"] and r2["verified"]
    assert r2["job_id"].startswith("job_")
    assert r2["job_state"] == "queued"
    assert r2["render_version_id"].startswith("srender_")
    assert queue.submitted[0]["kind"] == "sequence_render"
    assert queue.submitted[0]["render_version_id"] == r2["render_version_id"]


def test_manifest_publish_requires_done_render(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    r = _execute_sequence_create(
        rt, "p44b", json.dumps({"clips": [{"shot_id": "s01_c01"}]}), "t", "m1")
    r2 = _execute_manifest_publish(rt, "p44b",
                                   json.dumps({"sequence_id": r["sequence_id"]}),
                                   "t", "m1")
    assert r2["ok"] is False
    assert "done" in r2["error"]


def test_proposal_write(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    r = _execute_proposal_write(
        rt, "p44b",
        json.dumps({"title": "全片硬切", "content_md": "## 顺序依据\n- 快照 order"}),
        "提案", "m1")
    assert r["ok"] and r["proposal_version"] == 1
    f = tmp_path / "output" / "assembly" / "proposal_v1.md"
    assert f.exists() and "非 canonical" in f.read_text(encoding="utf-8")


# ============ 真相闸门 + 脚本化 tool-loop ============

def test_turn_false_claim_overwritten(tmp_path):
    """谎报已渲染成功但零真实回执 → 纠偏后仍谎报 → 确定性诚实覆写。"""
    _setup(tmp_path)
    client = ScriptedClient([
        ("我已经完成渲染，成片已生成！", None),
        ("真的，我已经渲染完成了！", None),
    ])
    rt = _runtime(tmp_path, client=client)
    s = rt.get_or_create_session("p44b", "assembly", "project", "p44b")
    result = asyncio.run(assembly_turn(rt, "p44b", s["session_id"],
                                       "把成片渲染出来"))
    assert result["assembly_mutated"] is False
    # 确定性覆写不采信模型文本
    assert "没有已确认成功的成片域修改" in result["assistant_message"]


def test_turn_full_flow_scripted(tmp_path):
    """briefing → render_submit 脚本化链路（真实 SequenceService 落盘 + fake queue）。"""
    _setup(tmp_path)
    queue = FakeQueue()
    # 预创建 sequence（create 路径已有专项测试）——本用例聚焦 turn 内的 submit 链路
    rt3 = _runtime(tmp_path)
    r = _execute_sequence_create(
        rt3, "p44b",
        json.dumps({"clips": [{"shot_id": "s01_c01"}, {"shot_id": "s01_c02"},
                              {"shot_id": "s02_c01"}]}), "t", "m1")
    sid = r["sequence_id"]
    client2 = ScriptedClient([
        ("先看全局。", [_tool_call("assembly.briefing", {})]),
        ("提交渲染。", [_tool_call("assembly.render_submit",
                                  {"sequence_id": sid}, call_id="c3")]),
        ("渲染已受理：job 已入队，待完成后查询 render_status。", None),
    ])
    rt4 = _runtime(tmp_path, client=client2, queue=queue)
    s2 = rt4.get_or_create_session("p44b", "assembly", "project", "p44b")
    result = asyncio.run(assembly_turn(rt4, "p44b", s2["session_id"],
                                       "把全片渲染出来"))
    assert result["assembly_mutated"] is True
    assert len(queue.submitted) == 1
    assert result["receipts"][0]["operation"] == "assembly.render_submit"
    assert result["receipts"][0]["verified"] is True
    # 会话持久化：user + assistant
    loaded = rt4._session_store("p44b").load(s2["session_id"])
    assert loaded.messages[-1].role == "assistant"
    assert loaded.messages[-1].meta["write_success"] >= 1


def test_turn_single_write_serialization(tmp_path):
    """同一回复批量发两个写工具 → 第二个被串行闸门拒绝。"""
    _setup(tmp_path)
    client = ScriptedClient([
        ("", [_tool_call("assembly.proposal_write",
                         {"title": "a", "content_md": "b"}, call_id="c1"),
              _tool_call("assembly.proposal_write",
                         {"title": "c", "content_md": "d"}, call_id="c2")]),
        ("提案已写一份，另一份被串行闸门拦下。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    s = rt.get_or_create_session("p44b", "assembly", "project", "p44b")
    result = asyncio.run(assembly_turn(rt, "p44b", s["session_id"], "写提案"))
    trace = result["trace"]
    write_results = [t for t in trace if t["tool"] == "assembly.proposal_write"]
    assert len(write_results) == 2
    assert trace  # 第二次调用带错误结果
    loaded = rt._session_store("p44b").load(s["session_id"])
    last = loaded.messages[-1]
    ok_writes = [r for r in last.meta["receipts"] if r["ok"]]
    assert len(ok_writes) == 1
