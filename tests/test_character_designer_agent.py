"""Character Designer Agent 测试（2026-08-30，原生 function-calling tool-loop）。

不联网（ScriptedClient）。对齐需求验收场景 A–L：
  A 普通聊天：零写工具、canonical 不变。
  B 根据故事创建角色：story.list → character.list → character.create → verified 回执落盘。
  C 局部修改：character.read → character.update → 仅 description_en 变，name/views/其它角色不动。
  D 整体重设计：仍走 update（身份/引用稳定）。
  E 讨论后执行：第一轮讨论零写；用户确认后第二轮真实 update。
  F 剧情事实保护（工具面）：工具集中没有任何 story/script 写工具——Agent 无能力改剧情。
  G 多角色：逐个 create 全部落盘；同回复多发写工具 → 只执行第 1 个。
  H Web Search：web.search → create；search_sources 进 meta；重复 query 拒绝；预算拦截。
  I 不该搜索的修改轮：trace 无 web.search（机制存在性由 H 的预算/去重覆盖）。
  J 假成功：明确写入请求 + 零写尝试 → zero-write recovery nudge 注入；仍谎称 → 确定性兜底覆写。
  K OCC stale：过期 expected_revision → 拒绝写入 + 返回最新内容；不落盘、无审计。
  L 资源锁：character 资源 lock/unlock + character.changed 事件分发（总线层）。
  另：删除（含三视图文件与设计草稿清理）、重名拒绝、未知字段拒绝、会话权限、
      unified_turn 端到端路由、协作停止。
"""
import asyncio
import json
import pathlib

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.character_designer import (
    character_designer_turn, CHARACTER_DESIGNER_TOOLS, TOOL_EXECUTORS,
    CHARACTER_WRITE_OPERATIONS, _characters_token, _character_token,
    _tool_surface,
)
from src.agents.script_writer import WRITE_OPERATIONS
from src.agents.revision_audit import DirectEditAuditStore
from src.agents.project_events import (RESOURCE_CHARACTER, RESOURCE_STORY,
                                       get_project_bus, reset_project_bus, emit_changed)


# ============ fixtures / helpers ============
def _proj():
    return Project(
        name="demo", style=StyleAnchor(), story_text="爷爷林海70岁，退休教师。小雨在雨夜遇到他。",
        characters=[Character(name="LinHai", description_en="a 70-year-old retired teacher with grey hair, warm smile")],
        scenes=[Scene(scene_id="s01", summary="雨夜爷爷救了小雨", characters=["LinHai", "XiaoYu"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="rainy night", characters=["LinHai"],
                                  target_duration_s=5)])])


def _save_proj(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _runtime(tmp_path, client=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


def _run(coro):
    return asyncio.run(coro)


def _tool_call(name, arguments, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


def _chars_rev(tmp_path):
    return _characters_token(ProjectStore(tmp_path).load().characters)


def _char_rev(tmp_path, name):
    return _character_token(ProjectStore(tmp_path).load().get_character(name))


class ScriptedClient:
    """按调用顺序返回脚本化的 chat_completion 响应。每条是 (content, tool_calls)。
    tool_calls 列表项若为 callable（无参），则在【该次 LLM 调用时】才构建——
    用于引用「写入后才会变化」的乐观并发指纹（如 expected_revision）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    @staticmethod
    def _resolve(tool_calls):
        if not tool_calls:
            return tool_calls
        return [tc() if callable(tc) else tc for tc in tool_calls]

    async def chat_completion(self, messages, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        if not self.script:
            return {"content": "（脚本已耗尽）", "tool_calls": None}
        content, tool_calls = self.script.pop(0)
        return {"content": content, "tool_calls": self._resolve(tool_calls)}

    async def chat_completion_stream(self, messages, **kw):
        """流式路径（/turn/stream 端点传 event_cb 时走这里）：模拟真实事件序列
        content_delta* → tool_call_pending* → finish。"""
        self.calls.append({"messages": messages, "kw": kw, "stream": True})
        if not self.script:
            yield {"kind": "finish", "content": "（脚本已耗尽）", "tool_calls": None}
            return
        content, tool_calls = self.script.pop(0)
        tool_calls = self._resolve(tool_calls)
        if content and not tool_calls:
            yield {"kind": "content_delta", "delta": content}
        if tool_calls:
            for tc in tool_calls:
                yield {"kind": "tool_call_pending", "name": tc["function"]["name"]}
        yield {"kind": "finish", "content": content, "tool_calls": tool_calls}

    async def chat(self, messages, **kw):
        return self.script and self.script.pop(0)[0] or ""


def _session(rt, agent_id="character_designer"):
    return rt.get_or_create_session("demo", agent_id, "project", "demo")["session_id"]


# ============ A 普通聊天：零写、canonical 不变 ============
def test_chat_no_mutation(tmp_path):
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    client = ScriptedClient([("我觉得 LinHai 目前的设定挺扎实的：退休教师身份给了他温和的气质……", None)])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "你觉得女主目前的人设怎么样？不要修改"))
    assert res["intent"] == "conversation"
    assert res["created"] is False and res["updated"] is False and res["deleted"] is False
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert len(client.calls) == 1
    assert client.calls[0]["kw"]["tools"] == _tool_surface()


# ============ B 根据故事创建角色 ============
def test_create_from_story(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("story.list", {})]),
        (None, [_tool_call("character.list", {})]),
        (None, [_tool_call("character.create",
                           {"name": "XiaoYu", "description_en": "a 16-year-old high school girl with black ponytail, blue raincoat",
                            "expected_revision": _chars_rev(tmp_path)}, call_id="call_3")]),
        ("已根据故事创建角色小雨。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "根据当前故事设计女主角"))
    assert res["intent"] == "character_create"
    p = ProjectStore(tmp_path).load()
    c = p.get_character("XiaoYu")
    assert c is not None and "raincoat" in c.description_en
    # 原有角色未受影响
    assert p.get_character("LinHai") is not None
    # 审计 + 工作记忆
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert audit and audit[-1]["agent_id"] == "character_designer"
    wm = rt.work_memory("demo").recent()
    assert any("角色" in e["summary"] for e in wm)
    # 回执 verified + meta 带 character_name
    assert res["receipts"][0]["ok"] and res["receipts"][0]["verified"]
    assert res["receipts"][0]["character_name"] == "XiaoYu"


# ============ C 局部修改：仅 description_en 变 ============
def test_partial_update_only_description(tmp_path):
    _save_proj(tmp_path)
    old_desc = ProjectStore(tmp_path).load().get_character("LinHai").description_en
    new_desc = "a 70-year-old retired teacher with grey short hair, warm smile, brown vest"
    client = ScriptedClient([
        (None, [_tool_call("character.read", {"character_name": "LinHai"})]),
        (None, [_tool_call("character.update",
                           {"character_name": "LinHai", "expected_revision": _char_rev(tmp_path, "LinHai"),
                            "description_en": new_desc}, call_id="call_2")]),
        ("已把林海的发型改为短发，其余设定未动。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "把男主头发改成短发，其他设定不要动"))
    assert res["intent"] == "character_update"
    p = ProjectStore(tmp_path).load()
    c = p.get_character("LinHai")
    assert c.description_en == new_desc
    assert c.name == "LinHai"                      # 身份不变
    assert c.views == {}                            # 图片资产未被触碰
    assert len(p.characters) == 1                   # 未产生新角色（不是 delete+recreate）
    # 回执只有 description_en 一个变更字段
    assert res["receipts"][0]["changed"] == ["description_en"]


# ============ D 整体重设计仍走 update ============
def test_full_redesign_keeps_identity(tmp_path):
    _save_proj(tmp_path)
    new_desc = "an imposing 70-year-old retired teacher, sharp grey eyes, dark mandarin-collar coat"
    client = ScriptedClient([
        (None, [_tool_call("character.read", {"character_name": "LinHai"})]),
        (None, [_tool_call("character.update",
                           {"character_name": "LinHai", "expected_revision": _char_rev(tmp_path, "LinHai"),
                            "description_en": new_desc}, call_id="call_2")]),
        ("已重新设计林海，压迫感更强，年龄身份不变。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "这个角色太普通，重新设计得更有压迫感，但年龄和身份保持不变"))
    assert res["updated"] is True
    p = ProjectStore(tmp_path).load()
    assert p.get_character("LinHai").description_en == new_desc
    assert len(p.characters) == 1


# ============ E 讨论后执行（跨轮）============
def test_discuss_then_execute(tmp_path):
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    rt = _runtime(tmp_path, None)
    sid = _session(rt)

    # 轮 1：讨论（零写）
    client1 = ScriptedClient([("加金丝眼镜是个不错的想法，能强化他的教师身份……要不要我加上？", None)])
    rt._client = client1
    res1 = _run(character_designer_turn(rt, "demo", sid, "你觉得给他加眼镜怎么样？"))
    assert res1["intent"] == "conversation"
    assert (tmp_path / "output" / "project.json").read_bytes() == before

    # 轮 2：用户确认 → 真实 update
    new_desc = ProjectStore(tmp_path).load().get_character("LinHai").description_en + ", round golden glasses"
    client2 = ScriptedClient([
        (None, [_tool_call("character.read", {"character_name": "LinHai"}, call_id="c1")]),
        (None, [_tool_call("character.update",
                           {"character_name": "LinHai", "expected_revision": _char_rev(tmp_path, "LinHai"),
                            "description_en": new_desc}, call_id="c2")]),
        ("已为林海加上金丝眼镜。", None),
    ])
    rt._client = client2
    res2 = _run(character_designer_turn(rt, "demo", sid, "可以，就加上"))
    assert res2["intent"] == "character_update"
    assert "golden glasses" in ProjectStore(tmp_path).load().get_character("LinHai").description_en


# ============ F 工具面：没有任何 story/script 写工具 ============
def test_no_story_write_capability(tmp_path):
    _save_proj(tmp_path)
    tool_names = {t["function"]["name"] for t in CHARACTER_DESIGNER_TOOLS}
    # 工具集只有角色写工具；story/script 工具一个都不出现在工具定义里
    # （Phase B 最终面：+ profile/inventory/read 只读 + clothing/look/prop/composition
    #   mutation 工具；shot.list/shot.read 经 _tool_surface() 从 Director 过滤并入）
    # 40.md Skill System V1：+skill.list / skill.load / skill.read_resource（只读）
    assert tool_names == {"character.list", "character.read", "character.create",
                          "character.update", "character.delete",
                          "character.generate_views", "web.search",
                          "project.visual_profile.read",
                          "resource.inventory", "resource.read",
                          "resource.clothing.upsert", "resource.look.prepare",
                          "resource.look.approve",
                          "resource.prop.register", "resource.composition.prepare",
                          "resource.library.publish", "resource.library.import",
                          "skill.list", "skill.load", "skill.read_resource"}
    surface_names = {t["function"]["name"] for t in _tool_surface()}
    assert {"shot.list", "shot.read"} <= surface_names
    # 执行器表也只暴露只读的 story/script 执行器，绝无写执行器
    assert "story.create" not in TOOL_EXECUTORS and "story.update" not in TOOL_EXECUTORS
    assert "story.delete" not in TOOL_EXECUTORS and "script.update" not in TOOL_EXECUTORS
    for t in ("story.list", "story.read", "script.read"):
        assert t in TOOL_EXECUTORS
    # 角色写工具集合与 Script Writer 写工具集合完全不相交
    assert CHARACTER_WRITE_OPERATIONS & WRITE_OPERATIONS == set()


# ============ G 多角色：逐个 create + 单写串行 ============
def test_multi_character_sequential_writes(tmp_path):
    _save_proj(tmp_path)
    # 同一回复里发 2 个写工具 → 只执行第 1 个，第 2 个被拒（单写串行）。
    # 每次成功写入后角色指纹都会变化 → 后续步骤的 expected_revision 用 lambda 延迟求值。
    client = ScriptedClient([
        (None, [_tool_call("character.create",
                           {"name": "XiaoYu", "description_en": "a high school girl", "expected_revision": _chars_rev(tmp_path)},
                           call_id="c1"),
                lambda: _tool_call("character.create",
                                   {"name": "Shadow", "description_en": "a tall figure in black", "expected_revision": _chars_rev(tmp_path)},
                                   call_id="c2")]),
        (None, [lambda: _tool_call("character.create",
                                   {"name": "Shadow", "description_en": "a tall figure in black", "expected_revision": _chars_rev(tmp_path)},
                                   call_id="c3")]),
        (None, [lambda: _tool_call("character.create",
                                   {"name": "OldWang", "description_en": "a shop owner", "expected_revision": _chars_rev(tmp_path)},
                                   call_id="c4")]),
        ("已创建三个角色。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "根据故事设计女主、反派和老王"))
    p = ProjectStore(tmp_path).load()
    for name in ("XiaoYu", "Shadow", "OldWang"):
        assert p.get_character(name) is not None
    assert len(p.characters) == 4   # 1 既有 + 3 新建，无重复创建
    # c2 被串行拒绝：trace 里有失败记录
    rejected = [t for t in res["trace"] if t["tool"] == "character.create" and not t["ok"]]
    assert any("一个一个来" in (t["error"] or "") for t in rejected)


# ============ H Web Search → create；重复 query 拒绝；search_sources 进 meta ============
def test_web_search_then_create(tmp_path, monkeypatch):
    _save_proj(tmp_path)
    from src.agents import script_writer as sw

    async def fake_search(self, query, depth="basic"):
        if query in getattr(fake_search, "seen", set()):
            return {"ok": True, "n_results": 0, "results": [], "sources": []}
        getattr(fake_search, "seen", set()).add(query)
        return {"ok": True, "n_results": 1,
                "results": [{"title": "1930年代上海服饰", "url": "https://example.com/qipao",
                             "content": "旗袍与西式大衣并存……", "score": 0.9}],
                "sources": [{"title": "1930年代上海服饰", "url": "https://example.com/qipao"}]}
    monkeypatch.setattr(sw.TavilyHubClient, "search", fake_search)
    monkeypatch.setattr(sw, "_WEB_SEARCH_CLIENT", sw.TavilyHubClient(api_key="test"))

    client = ScriptedClient([
        (None, [_tool_call("web.search", {"query": "1930年代上海 女记者 服装"}, call_id="c1")]),
        (None, [_tool_call("web.search", {"query": "1930年代上海 女记者 服装"}, call_id="c2")]),  # 重复 → 拒
        (None, [_tool_call("character.create",
                           {"name": "SuWan", "description_en": "a 1930s shanghai female reporter, qipao under western coat",
                            "expected_revision": _chars_rev(tmp_path)}, call_id="c3")]),
        ("参考真实史料设计了苏婉。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "设计一个1930年代上海女记者，查一下当时真实服装再设计"))
    assert res["created"] is True
    assert ProjectStore(tmp_path).load().get_character("SuWan") is not None
    # 重复 query 被拒（预算未被消耗：c2 失败但 c1 成功真实计数）
    dup = [t for t in res["trace"] if t["tool"] == "web.search" and not t["ok"]]
    assert any(t.get("error") and "相同" in t["error"] for t in dup)
    # search_sources 进 meta（仅 title+url）
    assert res["search_sources"] == [{"title": "1930年代上海服饰", "url": "https://example.com/qipao"}]


def test_search_budget_exhausted(tmp_path, monkeypatch):
    """每 user turn 最多 3 次真实搜索，超限 Runtime 硬拦截。"""
    _save_proj(tmp_path)
    from src.agents import script_writer as sw

    async def fake_search(self, query, depth="basic"):
        return {"ok": True, "n_results": 1,
                "results": [{"title": "t", "url": "u", "content": "c", "score": 0.5}],
                "sources": [{"title": "t", "url": "u"}]}
    monkeypatch.setattr(sw.TavilyHubClient, "search", fake_search)

    calls = {"n": 0}

    def _tc(i):
        calls["n"] += 1
        return _tool_call("web.search", {"query": f"查询{i}"}, call_id=f"c{i}")

    client = ScriptedClient([
        (None, [_tool_call("web.search", {"query": "q1"}, call_id="c1"),
                _tool_call("web.search", {"query": "q2"}, call_id="c2"),
                _tool_call("web.search", {"query": "q3"}, call_id="c3")]),
        (None, [_tool_call("web.search", {"query": "q4"}, call_id="c4")]),   # 第 4 次 → 预算拒绝
        ("资料已足够，基于已有信息继续。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "查四个方向的资料"))
    exhausted = [t for t in res["trace"] if t["tool"] == "web.search" and not t["ok"]]
    assert any("上限" in (t.get("error") or "") for t in exhausted)


# ============ I 不该搜索的修改轮：trace 无 web.search ============
def test_no_search_for_simple_edit(tmp_path):
    _save_proj(tmp_path)
    new_desc = "a 70-year-old retired teacher with grey buzz cut, warm smile"
    client = ScriptedClient([
        (None, [_tool_call("character.read", {"character_name": "LinHai"})]),
        (None, [_tool_call("character.update",
                           {"character_name": "LinHai", "expected_revision": _char_rev(tmp_path, "LinHai"),
                            "description_en": new_desc}, call_id="c2")]),
        ("已把发型改为寸头。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "把她头发改短一点"))
    assert all(t["tool"] != "web.search" for t in res["trace"])


# ============ J 假成功：zero-write recovery + 确定性兜底 ============
def test_fake_success_recovered_then_honest_fallback(tmp_path):
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    # 轮1：零写尝试 + 假成功文本 → zero-write recovery nudge；轮2：仍假成功 → 纠偏；轮3：仍假成功 → 兜底覆写
    client = ScriptedClient([
        ("好的，我已经把林海的头发改成短发了！", None),
        ("我已经完成修改。", None),
        ("已经修改完成了。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "把男主头发改成短发"))
    # canonical 未被改动
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    # zero-write recovery nudge 注入到第 2 次调用的 messages
    nudge_seen = any("还没有发起任何写工具" in m.get("content", "")
                     for m in client.calls[1]["messages"])
    assert nudge_seen
    # 最终回复是确定性兜底（不信模型自述）
    assert "没有真正执行修改" in res["assistant_message"]
    assert res["intent"] == "conversation"   # 零成功写 → 不猜变更类别


def test_zero_write_recovery_then_real_write(tmp_path):
    _save_proj(tmp_path)
    # 轮1 零写 + 非假成功措辞 → nudge；轮2 真正调用工具完成
    new_desc = "a 70-year-old retired teacher with grey short hair"
    client = ScriptedClient([
        ("好的，我来为林海重新设计发型。", None),
        (None, [_tool_call("character.read", {"character_name": "LinHai"}, call_id="c1")]),
        (None, [_tool_call("character.update",
                           {"character_name": "LinHai", "expected_revision": _char_rev(tmp_path, "LinHai"),
                            "description_en": new_desc}, call_id="c2")]),
        ("已改为短发。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "把男主头发改成短发"))
    assert ProjectStore(tmp_path).load().get_character("LinHai").description_en == new_desc
    assert res["updated"] is True


# ============ K OCC stale ============
def test_update_stale_rejected(tmp_path):
    _save_proj(tmp_path)
    stale_rev = "00000000"
    client = ScriptedClient([
        (None, [_tool_call("character.update",
                           {"character_name": "LinHai", "expected_revision": stale_rev,
                            "description_en": "hacked"}, call_id="c1")]),
        ("好的，我用最新内容重新判断。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    before_audit = DirectEditAuditStore(tmp_path, "demo").recent()
    res = _run(character_designer_turn(rt, "demo", _session(rt), "把男主改成另一个样子"))
    assert res["receipts"][0]["stale"] is True and res["receipts"][0]["ok"] is False
    assert res["receipts"][0]["current_revision"] == _char_rev(tmp_path, "LinHai")
    # 描述未被覆盖；无新增审计
    assert "hacked" not in ProjectStore(tmp_path).load().get_character("LinHai").description_en
    assert DirectEditAuditStore(tmp_path, "demo").recent() == before_audit


def test_create_stale_returns_current_list(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("character.create",
                           {"name": "Ghost", "description_en": "a ghost", "expected_revision": "badbad00"},
                           call_id="c1")]),
        ("用最新列表重新判断。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "再加一个角色"))
    r = res["receipts"][0]
    assert r["stale"] is True
    assert r["current_characters"][0]["name"] == "LinHai"
    assert ProjectStore(tmp_path).load().get_character("Ghost") is None


# ============ 删除：canonical + 三视图文件 + 设计草稿清理 ============
def test_delete_cleans_files_and_draft(tmp_path):
    _save_proj(tmp_path)
    # 造三视图文件 + 设计草稿（镜像 router 落盘位置）
    view_dir = tmp_path / "output" / "characters"
    view_dir.mkdir(parents=True, exist_ok=True)
    for f in ("LinHai_front.png", "LinHai_front_seg.png", "LinHai_side.png"):
        (view_dir / f).write_bytes(b"png")
    ai_dir = tmp_path / "output" / "ai"
    ai_dir.mkdir(parents=True, exist_ok=True)
    (ai_dir / "character_designs.json").write_text(
        json.dumps({"LinHai": {"draft": {"generation_prompt": "x"}}, "Other": {"draft": {}}}, ensure_ascii=False),
        encoding="utf-8")

    client = ScriptedClient([
        (None, [_tool_call("character.read", {"character_name": "LinHai"}, call_id="c1")]),
        (None, [_tool_call("character.delete", {"character_name": "LinHai"}, call_id="c2")]),
        (None, [_tool_call("character.list", {}, call_id="c3")]),
        ("已删除林海；注意 s01 故事段仍引用这个名字。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "删除林海这个角色"))
    assert res["deleted"] is True
    p = ProjectStore(tmp_path).load()
    assert p.get_character("LinHai") is None and len(p.characters) == 0
    assert not (view_dir / "LinHai_front.png").exists()
    assert not (view_dir / "LinHai_front_seg.png").exists()
    designs = json.loads((ai_dir / "character_designs.json").read_text(encoding="utf-8"))
    assert "LinHai" not in designs and "Other" in designs
    r = res["receipts"][0]
    assert r["ok"] and r["verified"] and r["removed_files"] == 3 and r["draft_removed"] is True


# ============ 参数校验 / 边界 ============
def test_create_duplicate_name_rejected(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("character.create",
                           {"name": "LinHai", "description_en": "dup", "expected_revision": _chars_rev(tmp_path)},
                           call_id="c1")]),
        ("明白，改用 character.update。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "创建林海"))
    assert res["receipts"][0]["ok"] is False
    assert "已存在" in (res["receipts"][0]["error"] or "")
    assert ProjectStore(tmp_path).load().get_character("LinHai").description_en != "dup"


def test_update_unknown_field_rejected(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("character.update",
                           {"character_name": "LinHai", "expected_revision": _char_rev(tmp_path, "LinHai"),
                            "age": "20"}, call_id="c1")]),
        ("明白，只能改 description_en。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "把年龄改一下"))
    assert res["receipts"][0]["ok"] is False
    assert "不允许修改字段" in (res["receipts"][0]["error"] or "")


def test_update_noop_unchanged(tmp_path):
    _save_proj(tmp_path)
    cur = ProjectStore(tmp_path).load().get_character("LinHai").description_en
    client = ScriptedClient([
        (None, [_tool_call("character.read", {"character_name": "LinHai"}, call_id="c1")]),
        (None, [_tool_call("character.update",
                           {"character_name": "LinHai", "expected_revision": _char_rev(tmp_path, "LinHai"),
                            "description_en": cur}, call_id="c2")]),
        ("内容一致，未变化。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "把描述改成现在这样"))
    assert res["receipts"][0]["unchanged"] is True and res["receipts"][0]["ok"] is True


def test_read_not_found(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("character.read", {"character_name": "Nobody"}, call_id="c1")]),
        ("该角色不存在。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "读取角色 Nobody"))
    assert res["trace"][0]["result"] == "error"


def test_wrong_agent_session_rejected(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    try:
        _run(character_designer_turn(rt, "demo", sid, "你好"))
        assert False, "should raise"
    except AgentError as e:
        assert e.code == "permission_denied"


def test_disabled_agent_rejected(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session(rt)   # 先建会话（此时未禁用）
    rt.settings.set_agent_enabled("character_designer", False)
    try:
        _run(character_designer_turn(rt, "demo", sid, "你好"))
        assert False, "should raise"
    except AgentError as e:
        assert e.code == "disabled_agent"


# ============ unified_turn 端到端路由 ============
def test_unified_turn_routes_to_character_runner(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([("我可以帮你设计角色，先聊聊想法吧。", None)])
    rt = _runtime(tmp_path, client=client)
    sid = _session(rt)
    res = _run(rt.unified_turn("demo", sid, "你觉得这个角色怎么样？"))
    # 走了新 runner：meta 带 receipts/trace 结构（D1 inquiry 路径无此 meta 形状）
    assert res["message"]["meta"]["intent"] == "conversation"
    assert "trace" in res["message"]["meta"]
    assert res.get("agent_id") == "character_designer"


# ============ 协作式停止 ============
def test_cancel_event_stops_turn(tmp_path):
    _save_proj(tmp_path)
    import threading
    client = ScriptedClient([("我开始处理……", None)])
    rt = _runtime(tmp_path, client=client)
    cancel = asyncio.Event()
    cancel.set()   # 预置取消：步骤边界立即停
    res = _run(character_designer_turn(rt, "demo", _session(rt), "设计女主",
                                       cancel_event=cancel))
    assert res["message"]["status"] == "stopped"
    assert "任务已停止" in res["assistant_message"]


# ============ L 资源锁总线（角色域）============
def test_bus_character_lock_and_changed():
    reset_project_bus()
    bus = get_project_bus()
    assert bus.lock("p", RESOURCE_CHARACTER, by="character_designer", session_id="s1") is True
    assert bus.is_locked("p", RESOURCE_CHARACTER)
    assert not bus.is_locked("p", RESOURCE_STORY)   # story 域不受影响
    snap = bus.snapshot("p")
    assert snap and snap[0]["resource"] == "character"
    sub = bus.subscribe("p")
    emit_changed(bus, "p", RESOURCE_CHARACTER, by="character_designer",
                 session_id="s1", operation="character.create")
    ev = _run(asyncio.wait_for(sub.get(), timeout=2))
    assert ev["type"] == "character.changed" and ev["operation"] == "character.create"
    assert bus.unlock("p", RESOURCE_CHARACTER) is True
    ev2 = _run(asyncio.wait_for(sub.get(), timeout=2))
    assert ev2["type"] == "resource.unlocked" and ev2["resource"] == "character"
    reset_project_bus()


def test_emit_changed_story_resource_maps_to_story_changed():
    reset_project_bus()
    bus = get_project_bus()
    sub = bus.subscribe("p")
    emit_changed(bus, "p", RESOURCE_STORY, by="script_writer", operation="script.update")
    ev = _run(asyncio.wait_for(sub.get(), timeout=2))
    assert ev["type"] == "story.changed"
    reset_project_bus()


# ============ 流式端点 wiring：写后必须释放角色锁 + 正常收尾 ============
def test_stream_endpoint_releases_character_lock(tmp_path, monkeypatch):
    """回归钉子（2026-08-30 真机发现）：/turn/stream 的 finally 若引用分支局部 import 名
    （RESOURCE_STORY），character_designer 分支会 NameError → 锁泄漏 + SSE 不收尾。
    端到端断言：turn.completed 后收到 resource.unlocked(character)，总线锁清空。"""
    import routers.agents as agents_router
    from fastapi.testclient import TestClient
    import api_server

    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [lambda: _tool_call("character.update",
                                   {"character_name": "LinHai",
                                    "expected_revision": _char_rev(tmp_path, "LinHai"),
                                    "description_en": "a 70-year-old retired teacher with grey hair and round glasses"},
                                   call_id="c1")]),
        ("已加上圆框眼镜。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = _session(rt)
    monkeypatch.setattr(agents_router, "get_agent_runtime", lambda: rt)

    # 资源锁/变更事件走【项目总线】（不进 turn SSE 流）——spy publish 记录（append 线程安全）
    bus = agents_router.get_project_bus()
    seen = []
    _orig_publish = bus.publish

    def _spy_publish(project, event):
        if project == "demo":
            seen.append(event)
        _orig_publish(project, event)
    bus.publish = _spy_publish

    with TestClient(api_server.app) as tc:
        events = []
        with tc.stream("POST", f"/api/project/demo/agents/sessions/{sid}/turn/stream",
                       json={"content": "给他加上眼镜", "thinking": False}) as resp:
            assert resp.status_code == 200
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    for line in frame.split("\n"):
                        line = line.strip()
                        if line.startswith("data:"):
                            try:
                                events.append(json.loads(line[5:].strip()))
                            except Exception:
                                pass
        types = [e.get("type") for e in events]
        assert "turn.completed" in types, types
        # 锁生命周期（总线）：写工具启动 → resource.locked(character)；
        # 写成功 → character.changed；finally → resource.unlocked(character)
        bus_types = [(e.get("type"), e.get("resource")) for e in seen]
        assert ("resource.locked", "character") in bus_types, bus_types
        assert ("character.changed", "character") in bus_types, bus_types
        assert ("resource.unlocked", "character") in bus_types, bus_types
        # 总线无残留锁；SSE 正常收尾（连接结束 = SENTINEL 已发）
        assert bus.snapshot("demo") == []
        # canonical 真实落盘
        assert "glasses" in ProjectStore(tmp_path).load().get_character("LinHai").description_en


# ============ 2026-08-30 真机 R2 回归：「改为」假成功漏网 + 「让…更…」recovery 句式 ============
def test_false_claim_with_gaiwei_is_caught(tmp_path):
    """「已将X改为Y」是完成态声称（R2 真机漏网案例）：真相闸门必须纠正；
    纠偏后模型真调工具 → 正常落盘。"""
    _save_proj(tmp_path)
    new_desc = ("28-year-old male fisherman, lean muscular build, "
                "sharp piercing dark eyes, wearing a faded blue work jacket")
    client = ScriptedClient([
        ("已将林远的瞳孔颜色改为深黑色，眼神更锐利。", None),   # 假成功：零写 + 完成态声称
        ("已将林远的瞳孔颜色改为深黑色，眼神更锐利。", None),   # recovery 后仍谎称 → 真相闸门纠正
        (None, [_tool_call("character.read", {"character_name": "LinHai"}, call_id="c1")]),
        (None, [lambda: _tool_call("character.update",
                                   {"character_name": "LinHai",
                                    "expected_revision": _char_rev(tmp_path, "LinHai"),
                                    "description_en": new_desc}, call_id="c2")]),
        ("已把眼神改得更锐利。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "让他的眼神更锐利一些"))
    # 完整恢复链：①zero-write recovery 上膛（「让…更…」祈使句识别）
    assert any("还没有发起任何写工具" in m.get("content", "")
               for m in client.calls[1]["messages"])
    # ②模型仍谎称「已将…改为」→ 真相闸门纠正（动词表含 改为）
    assert any("根本没有发起对应的工具调用" in m.get("content", "")
               for m in client.calls[2]["messages"])
    # ③纠偏后模型真写：canonical 落盘
    assert "sharp" in ProjectStore(tmp_path).load().get_character("LinHai").description_en
    assert res["updated"] is True


def test_rangeng_imperative_triggers_zero_write_recovery(tmp_path):
    """「让X更Y一些。」（无问号）是明确修改请求：零写尝试时 zero-write recovery 必须上膛。"""
    _save_proj(tmp_path)
    new_desc = ("28-year-old male fisherman, lean muscular build, "
                "sharp piercing dark eyes, wearing a faded blue work jacket")
    client = ScriptedClient([
        ("好的，我来调整他的眼神。", None),   # 零写 + 无完成态声称 → recovery（而非纠偏）
        (None, [_tool_call("character.read", {"character_name": "LinHai"}, call_id="c1")]),
        (None, [lambda: _tool_call("character.update",
                                   {"character_name": "LinHai",
                                    "expected_revision": _char_rev(tmp_path, "LinHai"),
                                    "description_en": new_desc}, call_id="c2")]),
        ("已把眼神改得更锐利。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "让他的眼神更锐利一些"))
    assert any("还没有发起任何写工具" in m.get("content", "")
               for m in client.calls[1]["messages"])
    assert "sharp" in ProjectStore(tmp_path).load().get_character("LinHai").description_en


def test_gaiwei_false_claim_honest_fallback(tmp_path):
    """「改为」假成功在纠偏额度用尽后被确定性兜底覆写（不让谎话到达用户）。"""
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    client = ScriptedClient([
        ("已将林远的瞳孔颜色改为深黑色。", None),
        ("已将林远的瞳孔颜色改为深黑色。", None),
        ("已将林远的瞳孔颜色改为深黑色。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "把他的瞳孔颜色改成深黑色"))
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert "没有真正执行修改" in res["assistant_message"]


# ============ character.generate_views（三视图生成，2026-08-30）============
def _fake_handler_factory(tmp_path, *, fail_names=(), delay=0.0, emit_log=None):
    """构造假 handler：写三视图文件 + canonical views（模拟 handler_character_views 行为）。"""
    from src.core.models import Asset

    async def fake_handler(job, emit):
        name = (job.params or {}).get("character_name")
        if emit_log is not None:
            emit_log.append((name, "front", 10))
        await emit("front", f"{name} front 生成中", 10)
        if delay:
            await asyncio.sleep(delay)
        if name in fail_names:
            raise RuntimeError(f"mock 生成失败: {name}")
        p = ProjectStore(tmp_path).load()
        c = p.get_character(name)
        views = {}
        vdir = tmp_path / "output" / "characters"
        vdir.mkdir(parents=True, exist_ok=True)
        for v in ("front", "side", "back"):
            f = vdir / f"{name}_{v}.png"
            f.write_bytes(b"png")
            views[v] = Asset(local=str(f), status="done")
        c.views = views
        ProjectStore(tmp_path).save(p)
    return fake_handler


def _mk_views_args(names):
    return json.dumps({"character_names": names}, ensure_ascii=False)


def test_generate_views_happy_path(tmp_path, monkeypatch):
    from src.agents import character_designer as cd
    _save_proj(tmp_path)
    emit_log = []
    monkeypatch.setattr(cd, "handler_character_views", _fake_handler_factory(tmp_path, emit_log=emit_log))
    rt = _runtime(tmp_path)
    res = _run(cd._execute_character_generate_views(
        rt, "demo", _mk_views_args(["LinHai"]), "生成三视图", None,
        event_cb=lambda e: None))
    assert res["ok"] and res["verified"]
    assert res["elapsed_s"] is not None
    assert len(res["generated"]) == 1
    assert set(res["generated"][0]["views"].keys()) == {"front", "side", "back"}
    for v, local in res["generated"][0]["views"].items():
        assert pathlib.Path(local).exists()
    # canonical 真实落盘
    c = ProjectStore(tmp_path).load().get_character("LinHai")
    assert c.views["front"].status == "done"
    # audit + 工作记忆
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert audit and audit[-1]["field"] == "character_views"
    wm = rt.work_memory("demo").recent()
    assert any("三视图" in e["summary"] for e in wm)


def test_generate_views_progress_events(tmp_path, monkeypatch):
    from src.agents import character_designer as cd
    _save_proj(tmp_path)
    monkeypatch.setattr(cd, "handler_character_views", _fake_handler_factory(tmp_path))
    events = []
    rt = _runtime(tmp_path)
    _run(cd._execute_character_generate_views(
        rt, "demo", _mk_views_args(["LinHai"]), "生成三视图", None,
        event_cb=events.append))
    kinds = [e.get("type") for e in events]
    assert "views.progress" in kinds
    p = next(e for e in events if e.get("type") == "views.progress")
    assert p["character_name"] == "LinHai" and "view" in p and "pct" in p


def test_generate_views_partial_failure(tmp_path, monkeypatch):
    from src.agents import character_designer as cd
    p = Project(name="demo", style=StyleAnchor(), story_text="s",
                characters=[Character(name="LinHai", description_en="a man"),
                            Character(name="XiaoYu", description_en="a girl")])
    ProjectStore(tmp_path).save(p)
    monkeypatch.setattr(cd, "handler_character_views",
                        _fake_handler_factory(tmp_path, fail_names={"XiaoYu"}))
    rt = _runtime(tmp_path)
    res = _run(cd._execute_character_generate_views(
        rt, "demo", _mk_views_args(["LinHai", "XiaoYu"]), "生成三视图", None))
    # 部分成功：ok=True 但 verified=False（未全部完成）
    assert res["ok"] is True and res["verified"] is False
    assert len(res["generated"]) == 1 and res["generated"][0]["character_name"] == "LinHai"
    failed = [r for r in res["results"] if not r["ok"]]
    assert failed and "mock 生成失败" in failed[0]["error"]


def test_generate_views_total_timeout_keeps_partial(tmp_path, monkeypatch):
    from src.agents import character_designer as cd
    _save_proj(tmp_path)
    # 单角色慢生成（1.5s），把总超时调小 → 未完成标记 timeout；per-char timeout 调小让慢任务超时
    async def slow_handler(job, emit):
        await asyncio.sleep(10)   # 覆盖 wait 轮询粒度，确保命中总超时而非自然完成
    monkeypatch.setattr(cd, "handler_character_views", slow_handler)
    rt = _runtime(tmp_path)
    old_total = cd.VIEWS_TOTAL_TIMEOUT_S
    cd.VIEWS_TOTAL_TIMEOUT_S = 0.3
    try:
        res = _run(cd._execute_character_generate_views(
            rt, "demo", _mk_views_args(["LinHai"]), "生成三视图", None))
    finally:
        cd.VIEWS_TOTAL_TIMEOUT_S = old_total
    assert res["ok"] is False and res["verified"] is False
    r = res["results"][0]
    assert r["ok"] is False and r.get("timeout") is True
    # canonical 未被改动（无部分写入）
    assert ProjectStore(tmp_path).load().get_character("LinHai").views == {}


def test_generate_views_cancel(tmp_path, monkeypatch):
    from src.agents import character_designer as cd
    _save_proj(tmp_path)
    async def slow_handler(job, emit):
        await asyncio.sleep(30)   # 会被 cancel
    monkeypatch.setattr(cd, "handler_character_views", slow_handler)
    rt = _runtime(tmp_path)
    cancel = asyncio.Event()
    cancel.set()   # 预置取消
    res = _run(cd._execute_character_generate_views(
        rt, "demo", _mk_views_args(["LinHai"]), "生成三视图", None, cancel_event=cancel))
    assert res["ok"] is False and res.get("cancelled") is True
    assert ProjectStore(tmp_path).load().get_character("LinHai").views == {}


def test_generate_views_param_validation(tmp_path, monkeypatch):
    from src.agents import character_designer as cd
    _save_proj(tmp_path)
    monkeypatch.setattr(cd, "handler_character_views", _fake_handler_factory(tmp_path))
    rt = _runtime(tmp_path)
    # >3 个角色
    try:
        _run(cd._execute_character_generate_views(
            rt, "demo", _mk_views_args(["A", "B", "C", "D"]), "x", None))
        assert False
    except AgentError as e:
        assert "3 个角色的三视图" in e.message
    # 重复
    try:
        _run(cd._execute_character_generate_views(
            rt, "demo", _mk_views_args(["LinHai", "LinHai"]), "x", None))
        assert False
    except AgentError as e:
        assert "重复" in e.message
    # 不存在 → 预检失败，不启动生成
    try:
        _run(cd._execute_character_generate_views(
            rt, "demo", _mk_views_args(["Nobody"]), "x", None))
        assert False
    except AgentError as e:
        assert e.code == "not_found"


def test_generate_views_in_write_slot(tmp_path, monkeypatch):
    """generate_views 占写槽：同回复里它之后的写工具被串行拒绝。"""
    from src.agents import character_designer as cd
    _save_proj(tmp_path)
    monkeypatch.setattr(cd, "handler_character_views", _fake_handler_factory(tmp_path))
    new_desc = "a 70-year-old retired teacher with grey hair"
    client = ScriptedClient([
        (None, [lambda: _tool_call("character.generate_views",
                                   {"character_names": ["LinHai"]}, call_id="c1"),
                _tool_call("character.update",
                           {"character_name": "LinHai",
                            "expected_revision": _char_rev(tmp_path, "LinHai"),
                            "description_en": new_desc}, call_id="c2")]),
        ("三视图已生成。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    res = _run(character_designer_turn(rt, "demo", _session(rt), "生成三视图并改描述"))
    assert res["views_generated"] is True
    rejected = [t for t in res["trace"] if t["tool"] == "character.update" and not t["ok"]]
    assert any("一个一个来" in (t["error"] or "") for t in rejected)
    # meta.generated_views 透传路径
    gv = res["message"]["meta"]["generated_views"]
    assert len(gv) == 3 and all(g["character_name"] == "LinHai" for g in gv)


# ============ Renewable window 镜像：续期后正常完成（三 Agent 统一）============
def test_window_renewal_then_complete(tmp_path):
    """窗口用满 → 注入 self-check nudge → 续期 → 继续读工具 → 纯文本正常结束。
    与 tests/test_long_execution.test_window_renewal_then_complete（director 侧）
    对齐，验证 Character Designer 的 _run_tool_loop 同一 renewable window 机制。"""
    _save_proj(tmp_path)
    from src.agents.character_designer import _run_tool_loop as cd_loop
    client = ScriptedClient([
        (None, [_tool_call("character.list", {})]),    # step 0
        (None, [_tool_call("character.list", {})]),    # step 1
        (None, [_tool_call("character.list", {})]),    # step 2
        # 窗口用满（max_steps=3）→ 续期注入 nudge → 第 4 次调用看到 nudge
        (None, [_tool_call("character.list", {})]),    # step 3（续期后）
        ("当前状态已确认，无需进一步操作。", None),       # 纯文本 → 正常结束
    ])
    rt = _runtime(tmp_path, client=client)
    instruction = "看看当前角色的设计情况如何？"   # 含「如何」→ chat_turn，绕过写域闸门
    messages = [{"role": "system", "content": "你是角色设计师。"},
                {"role": "user", "content": instruction}]
    res = _run(cd_loop(rt, client, "demo", messages, instruction, None, max_steps=3))
    assert len(client.calls) == 5
    nudge_msgs = [m for m in client.calls[3]["messages"]
                  if m.get("role") == "user"
                  and "【系统提示·执行窗口】" in (m.get("content") or "")]
    assert len(nudge_msgs) == 1
    assert res["window_renewals"] == 1
    assert res["text"] == "当前状态已确认，无需进一步操作。"
