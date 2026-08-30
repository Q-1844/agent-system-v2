# -*- coding: utf-8 -*-
"""Web Search（第一版 Tavily Hub）测试。全部离线（ScriptedClient + FakeProvider）。

验证：
  - Provider（tavily_hub_client）：双层包装解包、压缩/截断、429/5xx/timeout/网络错/空结果/
    未配置 key/非 JSON 响应 → 全部结构化返回，绝不抛异常。
  - Runtime 预算：每 turn 最多 3 次真实搜索；第 4 次被 Runtime 拒绝（不调 Provider），
    拒绝以结构化 obs（search_budget_exhausted）喂回，Agent 可继续收尾。
  - meta.search_sources：仅 title+url 去重落盘；snippet/正文不进 meta/trace（不跨轮污染）。
  - 错误路径：Provider 失败落为结构化 tool message，turn 正常收尾不崩；web.search 不记账本
    （receipts 为空，不影响「本轮改动」）。
  - schema：web.search 在 SCRIPT_WRITER_TOOLS 且仅 query 单参数。
"""
import asyncio
import json

import httpx
import pytest

from src.core.models import Project, Scene, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents import script_writer as sw
from src.agents.script_writer import script_writer_turn, SCRIPT_WRITER_TOOLS
from src.api import tavily_hub_client as thc


# ============ 基础设施（与 test_ai_7d_r_script_writer_native 同款最小环境） ============

def _proj():
    return Project(
        name="demo", style=StyleAnchor(), story_text="小雨走进雨里。",
        characters=[Character(name="LinXia", description_en="a young woman")],
        scenes=[Scene(scene_id="s01", summary="rain", characters=["LinXia"], shots=[])])


def _runtime(tmp_path, client=None):
    ProjectStore(tmp_path).save(_proj())
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


def _run(coro):
    return asyncio.run(coro)


def _tool_call(name, arguments, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


class ScriptedClient:
    """按调用顺序返回脚本化的 chat_completion 响应。每条是 (content, tool_calls)。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        if not self.script:
            return {"content": "（脚本已耗尽）", "tool_calls": None}
        content, tool_calls = self.script.pop(0)
        return {"content": content, "tool_calls": tool_calls}


class FakeSearchProvider:
    """替身 TavilyHubClient：记录查询、按脚本返回结构化结果。"""

    def __init__(self, script=None, default=None):
        self.script = list(script or [])
        self.default = default or {
            "ok": True, "n_results": 1,
            "results": [{"title": "急诊抢救流程", "url": "https://example.com/a",
                         "content": "心肺复苏步骤……", "score": 0.9}],
            "sources": [{"title": "急诊抢救流程", "url": "https://example.com/a"}],
        }
        self.queries = []

    async def search(self, query, depth="basic"):
        self.queries.append(query)
        if self.script:
            return self.script.pop(0)
        return dict(self.default)


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(sw, "_WEB_SEARCH_CLIENT", provider)


def _tool_messages(client: ScriptedClient):
    """所有 LLM 调用里见过的 role=tool 消息（obs JSON）。"""
    out = []
    seen = {}
    for c in client.calls:
        for m in c["messages"]:
            if m.get("role") == "tool":
                # 按 tool_call_id 去重：每次 LLM 调用的 messages 都累积包含之前的 tool 消息
                seen[m.get("tool_call_id") or m.get("content", "")[:32]] = m
    return list(seen.values())


# ============ Provider 单元测试（monkeypatch httpx） ============

class _FakeResponse:
    def __init__(self, status_code=200, body=None, bad_json=False):
        self.status_code = status_code
        self._body = body
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


class _FakeAsyncClient:
    """替身 httpx.AsyncClient：post 返回预设响应或抛预设异常。"""
    response = None
    exc = None
    last_json = None
    last_headers = None

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeAsyncClient.last_json = json
        _FakeAsyncClient.last_headers = dict(headers or {})
        if _FakeAsyncClient.exc is not None:
            raise _FakeAsyncClient.exc
        return _FakeAsyncClient.response


def _provider():
    return thc.TavilyHubClient(api_key="test-key", timeout_s=5)


def _wrap_tavily(results):
    """Hub 双层包装形状。"""
    return {"code": 0, "message": "success",
            "data": {"data": {"query": "q", "results": results}}}


@pytest.fixture()
def fake_http(monkeypatch):
    _FakeAsyncClient.response = None
    _FakeAsyncClient.exc = None
    _FakeAsyncClient.last_json = None
    monkeypatch.setattr(thc.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


def test_provider_unwraps_and_compresses(fake_http):
    fake_http.response = _FakeResponse(200, body=_wrap_tavily([
        {"title": "t" * 300, "url": "https://e.com/1", "content": "c" * 900, "score": 0.7},
    ]))
    res = _run(_provider().search("查询"))
    assert res["ok"] and res["n_results"] == 1
    r = res["results"][0]
    assert len(r["title"]) == 200 and len(r["content"]) == 600   # 截断生效
    assert res["sources"] == [{"title": "t" * 200, "url": "https://e.com/1"}]
    # Provider 参数由 Runtime 固定
    sent = fake_http.last_json
    assert sent["max_results"] == 3 and sent["include_raw_content"] is False \
        and sent["include_answer"] is False and sent["include_images"] is False
    # 鉴权头带 Bearer key
    assert fake_http.last_headers["Authorization"] == "Bearer test-key"


def test_provider_single_layer_ok(fake_http):
    fake_http.response = _FakeResponse(200, body={"results": [
        {"title": "t", "url": "https://e.com/2", "content": "c"}]})
    res = _run(_provider().search("查询"))
    assert res["ok"] and res["n_results"] == 1


def test_provider_error_mapping(fake_http):
    fake_http.response = _FakeResponse(429, body={})
    assert _run(_provider().search("q"))["code"] == "rate_limited"
    fake_http.response = _FakeResponse(502, body={})
    assert _run(_provider().search("q"))["code"] == "upstream_error"
    fake_http.response = _FakeResponse(200, body=None, bad_json=True)
    assert _run(_provider().search("q"))["code"] == "upstream_error"
    fake_http.response = _FakeResponse(200, body={"data": {"data": "unexpected"}})
    assert _run(_provider().search("q"))["code"] == "upstream_error"
    fake_http.exc = httpx.TimeoutException("read timeout")
    assert _run(_provider().search("q"))["code"] == "timeout"
    fake_http.exc = httpx.ConnectError("conn refused")
    assert _run(_provider().search("q"))["code"] == "network_error"


def test_provider_empty_results_is_ok(fake_http):
    fake_http.response = _FakeResponse(200, body=_wrap_tavily([]))
    res = _run(_provider().search("生僻查询"))
    assert res["ok"] and res["n_results"] == 0 and res["results"] == []


def test_provider_hub_quota_error(fake_http):
    """Hub 业务层错误（HTTP 200 + 非 0 code）：42901 → quota_exhausted，透出原始 message。"""
    fake_http.response = _FakeResponse(200, body={
        "code": 42901, "message": "Credit 不足，请等待恢复或升级套餐",
        "data": None, "timestamp": 1788062727400})
    res = _run(_provider().search("查询"))
    assert res["ok"] is False and res["code"] == "quota_exhausted"
    assert "Credit 不足" in res["error"]


def test_provider_missing_key(monkeypatch):
    monkeypatch.delenv("TAVILY_HUB_API_KEY", raising=False)
    res = _run(thc.TavilyHubClient(api_key="").search("q"))
    assert res["ok"] is False and res["code"] == "not_configured"


# ============ Runtime 集成（ScriptedClient + FakeSearchProvider） ============

def test_web_search_schema_registered():
    names = [t["function"]["name"] for t in SCRIPT_WRITER_TOOLS]
    assert "web.search" in names
    schema = next(t for t in SCRIPT_WRITER_TOOLS if t["function"]["name"] == "web.search")
    props = schema["function"]["parameters"]["properties"]
    assert set(props) == {"query"}
    assert schema["function"]["parameters"]["required"] == ["query"]


def test_budget_hard_limit_three_per_turn(tmp_path, monkeypatch):
    """第 4 次搜索被 Runtime 拒绝（不调 Provider）；Agent 拿到结构化拒绝后正常收尾。"""
    client = ScriptedClient([
        (None, [_tool_call("web.search", {"query": "急诊抢救流程"}, "c1")]),
        (None, [_tool_call("web.search", {"query": "急诊室分级分诊"}, "c2")]),
        (None, [_tool_call("web.search", {"query": "心肺复苏用药"}, "c3")]),
        (None, [_tool_call("web.search", {"query": "第四次不该发生"}, "c4")]),
        ("搜到的急诊抢救资料要点：先评估环境，再胸外按压。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    provider = FakeSearchProvider()
    _patch_provider(monkeypatch, provider)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "查一下真实急诊室抢救流程的资料"))
    assert provider.queries == ["急诊抢救流程", "急诊室分级分诊", "心肺复苏用药"]   # 第 4 次未调 Provider
    refused = [m for m in _tool_messages(client)
               if "search_budget_exhausted" in (m.get("content") or "")]
    assert len(refused) == 1
    obs = json.loads(refused[0]["content"])
    assert obs["ok"] is False and obs["code"] == "search_budget_exhausted"
    assert res["assistant_message"].startswith("搜到的急诊抢救资料要点")


def test_sources_into_meta_and_not_into_trace(tmp_path, monkeypatch):
    """sources 仅 title+url 落盘 meta（去重）；snippet 不进 meta/trace。"""
    client = ScriptedClient([
        (None, [_tool_call("web.search", {"query": "1940年代上海 生活"}, "c1")]),
        (None, [_tool_call("web.search", {"query": "上海租界 百货公司"}, "c2")]),
        ("资料要点如下：1940年代的上海街景细节很丰富。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    src = {"title": "遭遇经济战", "url": "https://hist.example/x"}
    provider = FakeSearchProvider(default={
        "ok": True, "n_results": 1,
        "results": [{"title": src["title"], "url": src["url"],
                     "content": "网页正文片段……不应跨轮持久化", "score": 0.8}],
        "sources": [src]})
    _patch_provider(monkeypatch, provider)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "写一个1940年代上海街头场景，要真实"))
    meta = res["message"]["meta"]
    assert meta["search_sources"] == [src]   # 两次搜索同源去重 → 1 条
    assert all("content" not in s and "snippet" not in s for s in meta["search_sources"])
    ws_trace = [t for t in meta["trace"] if t["tool"] == "web.search"]
    assert len(ws_trace) == 2
    assert all(t["result"] == "ok" and "不应跨轮持久化" not in json.dumps(t) for t in ws_trace)
    assert ws_trace[0]["target"] == "1940年代上海 生活"
    assert res["search_sources"] == [src]
    assert res["receipts"] == []   # web.search 是只读工具，不进写账本


def test_provider_error_structured_no_crash(tmp_path, monkeypatch):
    """timeout → 结构化 obs 喂回，turn 正常收尾；Agent 换查询第二次成功。"""
    client = ScriptedClient([
        (None, [_tool_call("web.search", {"query": "庭审流程"}, "c1")]),
        (None, [_tool_call("web.search", {"query": "中国法庭 庭审顺序"}, "c2")]),
        ("第一次搜索超时，换词后搜到了资料，要点如下。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    provider = FakeSearchProvider(script=[
        {"ok": False, "code": "timeout", "error": "搜索超时（>15s）"},
    ])
    _patch_provider(monkeypatch, provider)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "查一下真实法庭流程然后写一段"))
    assert res["assistant_message"].startswith("第一次搜索超时")
    errs = [t for t in res["message"]["meta"]["trace"]
            if t["tool"] == "web.search" and t["result"] == "error"]
    assert len(errs) == 1 and "超时" in (errs[0]["error"] or "")
    assert res["message"]["meta"]["search_sources"]   # 第二次成功带来源
    assert res["receipts"] == []


def test_zero_results_note(tmp_path, monkeypatch):
    """零结果是正常返回：ok obs 带 note 提示，Agent 可换词或继续。"""
    client = ScriptedClient([
        (None, [_tool_call("web.search", {"query": "生僻到没有结果的词"}, "c1")]),
        ("没有搜到资料，我基于已有知识完成了。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    provider = FakeSearchProvider(default={"ok": True, "n_results": 0,
                                           "results": [], "sources": []})
    _patch_provider(monkeypatch, provider)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "搜一下这个冷门习俗"))
    tool_msgs = [json.loads(m["content"]) for m in _tool_messages(client)]
    zero = [o for o in tool_msgs if o.get("operation") == "web.search"]
    assert zero and zero[0]["ok"] and zero[0]["n_results"] == 0 and "note" in zero[0]
    assert res["message"]["meta"]["search_sources"] == []


def test_bad_arguments_and_empty_query(tmp_path, monkeypatch):
    client = ScriptedClient([
        (None, [_tool_call("web.search", {"query": ""}, "c1")]),
        (None, [_tool_call("web.search", {"query": "x" * 201}, "c2")]),
        ("参数有误，已提示用户。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    provider = FakeSearchProvider()
    _patch_provider(monkeypatch, provider)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    _run(script_writer_turn(rt, "demo", sid, "搜索"))
    obs = [json.loads(m["content"]) for m in _tool_messages(client)]
    bads = [o for o in obs if o.get("operation") == "web.search" and not o["ok"]]
    assert len(bads) == 2
    assert all(o["code"] == "bad_arguments" for o in bads)
    assert provider.queries == []   # 参数非法不触达 Provider


# ============ 搜索谎报收口（真相闸门扩展） ============

def test_search_claim_regex():
    f = sw._claims_search_success
    # 谎报（肯定式声明）
    assert f("四次 web.search 结果汇报：①成功 ②额度耗尽")
    assert f("我已经搜索到了3条资料")
    assert f("搜索完成，找到 3 个参考来源")
    assert f("已发起搜索，结果如下：…")
    # 诚实表述（否定/失败/无声明）
    assert not f("没有搜索到相关资料，基于已有知识完成了创作")
    assert not f("搜索因额度不足未能完成，改用已有知识继续")
    assert not f("本轮没有发起搜索")
    assert not f("搜索失败：超时。我用已有知识写好了这一段")
    assert not f("你觉得这个角色怎么样？")


def test_search_false_claim_gate(tmp_path, monkeypatch):
    """模型零搜索调用却编造搜索汇报 → 闸门纠正 nudge 喂回 → 模型真调 web.search。"""
    client = ScriptedClient([
        # 第一轮：编造搜索汇报（无任何 tool_calls）
        ("四次搜索汇报：①搜索成功，找到 3 条资料；②搜索成功，找到 3 条资料。", None),
        # 被纠正后：真调 web.search
        (None, [_tool_call("web.search", {"query": "唐朝长安城布局"}, "c1")]),
        ("如实汇报：本轮只有第1次搜索成功（其余受每轮上限影响未执行）。", None),
    ])
    provider = FakeSearchProvider()
    _patch_provider(monkeypatch, provider)
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "连续搜四次唐朝资料"))
    nudges = [m for c in client.calls for m in c["messages"]
              if m.get("role") == "user" and "web.search 工具调用" in (m.get("content") or "")]
    assert nudges, "纠正 nudge 未触发"
    assert provider.queries == ["唐朝长安城布局"]   # 纠正后真调了 Provider
    assert res["assistant_message"].startswith("如实汇报")


def test_search_claim_no_false_positive(tmp_path, monkeypatch):
    """真实搜索成功后的正常汇报不触发闸门；聊天轮谎报不上膛。"""
    # 1) 有真实搜索 → 不触发
    client = ScriptedClient([
        (None, [_tool_call("web.search", {"query": "唐代服饰"}, "c1")]),
        ("我搜索到了3条资料，可以基于这些资料来写。", None),
    ])
    _patch_provider(monkeypatch, FakeSearchProvider())
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "查一下唐代服饰"))
    assert res["assistant_message"].startswith("我搜索到")
    # 2) 聊天轮（无编辑请求）谎报 → 不上膛、不纠正
    client2 = ScriptedClient([
        ("刚才那次搜索成功了，找到 3 条资料。", None),
    ])
    rt2 = _runtime(tmp_path, client=client2)
    sid2 = rt2.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res2 = _run(script_writer_turn(rt2, "demo", sid2, "你觉得刚才搜到的资料怎么样？先不要改东西"))
    assert res2["assistant_message"].startswith("刚才那次搜索成功")


# ============ 同 turn 重复 query 拦截（不扣预算） ============

def test_duplicate_query_refused_without_budget(tmp_path, monkeypatch):
    """完全相同/标准化相同的 query：第二次起直接拒绝（duplicate_query），
    不调 Provider、不扣 3 次预算——后续不同 query 仍可正常搜索。"""
    client = ScriptedClient([
        (None, [_tool_call("web.search", {"query": "唐朝服饰"}, "c1")]),
        (None, [_tool_call("web.search", {"query": "唐朝服饰"}, "c2")]),
        (None, [_tool_call("web.search", {"query": "  唐朝   服饰 "}, "c3")]),   # 标准化后相同
        (None, [_tool_call("web.search", {"query": "唐朝饮食文化"}, "c4")]),
        ("如实汇总：第1、4次成功，重复的两次被系统拒绝。", None),
    ])
    provider = FakeSearchProvider()
    _patch_provider(monkeypatch, provider)
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "多搜几条唐朝资料"))
    assert provider.queries == ["唐朝服饰", "唐朝饮食文化"]   # 重复的不触达 Provider
    dups = [json.loads(m["content"]) for m in _tool_messages(client)
            if '"duplicate_query"' in (m.get("content") or "")]
    assert len(dups) == 2
    assert all(d["ok"] is False for d in dups)
    assert res["assistant_message"].startswith("如实汇总")
