"""Clean Review（独立干净会话的连续性审查）mock 测试（不联网）。

对应 V1 压力测试验收用例：
  - F4 复现：用户触发审查 → review session 必须真实读取（对照工具调用数）、
    报告落盘 output/agents/reviews/、只报告不修改；
  - 只读模式：写工具调用被运行时拒绝，canonical 零改动；
  - 审查者零读取 → zero-read gate 同样拦截（审查者也必须真实读取）；
  - 触发计数：结构写操作累计 ≥8 → 自动 Clean Review 一次，计数重新起算；
  - 一次性会话：两次触发各建各的 review session，不复用。
"""
import asyncio
import json

from tests.test_director_agent import (
    _save_proj, _runtime, _run, _tool_call, _shots_rev, ScriptedClient,
)
from src.agents import session_governance
from src.core.store import ProjectStore


def _director():
    from src.agents.director import director_turn
    return director_turn


# ============ 触发意图识别（白名单，宁缺勿滥）===========
def test_is_review_request_whitelist():
    assert session_governance.is_review_request("帮我全片检查一下连续性")
    assert session_governance.is_review_request("从头过一遍，看看有没有问题")
    assert session_governance.is_review_request("做一次整体审查")
    assert session_governance.is_review_request("逐镜检查一遍")
    # 单点核对/探讨不触发（主会话 + zero-read gate 胜任）
    assert not session_governance.is_review_request("检查一下第2镜有没有问题")
    assert not session_governance.is_review_request("把第1镜改成近景")
    assert not session_governance.is_review_request("你觉得这段节奏怎么样")


# ============ F4 复现：触发 B → 独立只读审查会话真实读取 ============
def test_user_triggered_review_reads_canonical(tmp_path):
    _save_proj(tmp_path)
    report = ("问题清单：\n1. s01_c01 与 s01_c02 衔接正常；未发现重复镜头。\n总体结论：结构完整。")
    client = ScriptedClient([
        (None, [_tool_call("story.list", {})]),
        (None, [_tool_call("shot.list", {})]),
        (None, [_tool_call("shot.read", {"shot_id": "s01_c01"})]),
        (None, [_tool_call("shot.read", {"shot_id": "s01_c02"})]),
        (report, None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, "帮我全片检查一下连续性，重点看重复镜头"))
    # 审查由独立会话真实执行（工具调用数可对照 events 流口径）
    assert res["intent"] == "continuity_review"
    assert res["assistant_message"].startswith("【连续性审查报告】")
    assert res["receipts"] == [] and res["created"] is False
    # 审查会话真实读取了 4 次（story.list/shot.list/shot.read×2）
    assert len(res["events"]) == 1 and res["events"][0]["tool_calls"] == 4
    # 只读模式：client 只被告知读工具 schema（白名单内，无任何写工具）
    from src.agents.director import REVIEW_READ_TOOLS
    for c in client.calls:
        names = {t["function"]["name"] for t in c["kw"]["tools"]}
        assert names <= REVIEW_READ_TOOLS
    # canonical 零改动（只报告不修改）
    proj = ProjectStore(tmp_path).load()
    assert len(proj.scenes[0].shots) == 2 and len(proj.scenes[1].shots) == 0
    # 报告落盘（带触发原因）
    reviews = list((tmp_path / "output" / "agents" / "reviews").glob("review_*.json"))
    assert len(reviews) == 1
    data = json.loads(reviews[0].read_text(encoding="utf-8"))
    assert data["trigger"] == "user_request"
    assert data["tool_calls"] == 4 and report in data["report"]
    # review session 是新建的一次性会话（≠ 主会话）
    assert data["review_session_id"] != sid
    review_sess = rt.get_session("demo", data["review_session_id"])
    assert review_sess is not None and review_sess["scope_type"] == "review"
    # 主会话收到报告（user + assistant 两条）
    main = rt.get_session("demo", sid)
    assert main["messages"][-1]["role"] == "assistant"
    assert "【连续性审查报告】" in main["messages"][-1]["content"]


def test_review_session_one_off_not_reused(tmp_path):
    """每次触发新建一次性 review session，不复用（复用会自己积累 staleness）。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=None)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    ids = []
    for _ in range(2):
        client = ScriptedClient([
            (None, [_tool_call("shot.list", {})]),
            ("审查完成，无重大问题。", None),
        ])
        rt._client = client
        _run(_director()(rt, "demo", sid, "做一次全片检查"))
        reviews = json.loads(
            sorted((tmp_path / "output" / "agents" / "reviews").glob("review_*.json"))[-1]
            .read_text(encoding="utf-8"))
        ids.append(reviews["review_session_id"])
    rt._client = None
    assert ids[0] != ids[1]


# ============ 只读模式：写工具被运行时拒绝 ============
def test_review_write_tool_rejected(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.list", {})]),
        (None, [_tool_call("shot.delete", {"shot_id": "s01_c01"})]),
        ("已核对当前分镜，报告完成。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, "全片检查一下连续性"))
    # 写调用被拒绝：canonical 零改动
    proj = ProjectStore(tmp_path).load()
    assert len(proj.scenes[0].shots) == 2
    # 拒绝原因真实返回给模型（工具消息里可见）
    tool_msgs = [m for m in client.calls[-1]["messages"] if m.get("role") == "tool"]
    assert any("只读审查会话" in (m.get("content") or "") for m in tool_msgs)
    assert res["intent"] == "continuity_review"


# ============ 审查者零读取同样被 zero-read gate 拦截 ============
def test_reviewer_zero_read_gated(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        ("我已逐镜核对 s01_c01 与 s01_c02，无问题。", None),   # 零读取全幻觉
        (None, [_tool_call("shot.list", {})]),                 # nudge 后真实读取
        ("经真实读取：s01 共 2 镜，衔接正常。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, "全片检查一下连续性"))
    assert len(client.calls) == 3
    nudge = [m for m in client.calls[-1]["messages"] if m.get("role") == "user"
             and "零次读工具调用" in (m.get("content") or "")]
    assert len(nudge) == 1
    assert "经真实读取" in res["assistant_message"]


# ============ 触发计数用例：结构写累计 ≥8 → 自动 Clean Review ============
def test_governance_counter_semantics(tmp_path):
    """计数口径：create/create_many/delete/move 按成功调用次数计；update 类、
    unchanged、失败调用不计。"""
    receipts = [
        {"operation": "shot.create", "ok": True, "verified": True, "unchanged": False},
        {"operation": "shot.create_many", "ok": True, "verified": True, "unchanged": False},
        {"operation": "shot.update_many", "ok": True, "verified": True, "unchanged": False},
        {"operation": "shot.update", "ok": True, "verified": True, "unchanged": False},
        {"operation": "shot.delete", "ok": True, "verified": True, "unchanged": False},
        {"operation": "shot.move", "ok": True, "verified": True, "unchanged": True},
        {"operation": "shot.create", "ok": False, "verified": False},
    ]
    assert session_governance.GovernanceStore.count_structural_writes(receipts) == 3
    g = session_governance.GovernanceStore(tmp_path, "demo")
    for _ in range(3):
        g.record_structural_writes(receipts)   # 9 次
    st = g.snapshot()
    assert st["structural_since_review"] == 9 and st["structural_since_reground"] == 9
    g.reset_since_review()
    st = g.snapshot()
    assert st["structural_since_review"] == 0 and st["structural_since_reground"] == 9


def test_trigger_a_auto_review_at_threshold(tmp_path):
    """E1+E2b 量级的操作序列（预置 7 次 + 本轮 1 次成功结构写 = 8）→ 自动审查一次，
    since_review 计数清零、since_reground 继续累计。"""
    _save_proj(tmp_path)
    gov_path = tmp_path / "output" / "agents" / "session_governance.json"
    gov_path.parent.mkdir(parents=True, exist_ok=True)
    gov_path.write_text(json.dumps({"structural_since_review": 7,
                                    "structural_since_reground": 7}),
                        encoding="utf-8")
    rev = _shots_rev(tmp_path)
    client = ScriptedClient([
        # 主会话：补 1 个镜头（1 次结构写）
        (None, [_tool_call("shot.list", {"scene_id": "s02"})]),
        (None, [_tool_call("shot.create",
                           {"scene_id": "s02", "description": "风铃特写，浅景深",
                            "expected_shots_revision": rev})]),
        ("已创建镜头 s02_c01。", None),
        # 触发 A → 独立审查会话
        (None, [_tool_call("story.list", {})]),
        (None, [_tool_call("shot.list", {})]),
        ("全片审查完成：s01/s02 无重复与错位。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, "在段尾补一个风铃特写"))
    assert res["created"] is True                       # 主链路正常执行
    assert "【自动连续性审查】" in res["assistant_message"]
    assert any(e["kind"] == "continuity_review"
               and e["trigger"] == "structural_threshold" for e in res["events"])
    reviews = list((tmp_path / "output" / "agents" / "reviews").glob("review_*.json"))
    assert len(reviews) == 1
    assert json.loads(reviews[0].read_text(encoding="utf-8"))["trigger"] == "structural_threshold"
    st = json.loads(gov_path.read_text(encoding="utf-8"))
    assert st["structural_since_review"] == 0           # 审查后重新起算
    assert st["structural_since_reground"] == 8         # re-ground 计数继续累计
    # 主链路写真实生效 + 审查只读未动 canonical
    assert len(ProjectStore(tmp_path).load().scenes[1].shots) == 1


def test_below_threshold_no_auto_review(tmp_path):
    """阈值之下的结构写不触发自动审查（主链路行为与基线一致）。"""
    _save_proj(tmp_path)
    rev = _shots_rev(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.list", {"scene_id": "s02"})]),
        (None, [_tool_call("shot.create",
                           {"scene_id": "s02", "description": "风铃特写，浅景深",
                            "expected_shots_revision": rev})]),
        ("已创建镜头 s02_c01。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, "在段尾补一个风铃特写"))
    assert res["created"] is True
    assert "【自动连续性审查】" not in res["assistant_message"]
    assert not (tmp_path / "output" / "agents" / "reviews").exists()
    st = session_governance.GovernanceStore(tmp_path, "demo").snapshot()
    assert st["structural_since_review"] == 1
