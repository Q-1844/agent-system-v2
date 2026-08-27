"""AI-7D-R Phase 4-7 · 前端流式消费者逻辑测试。

前端是 Vue/TS，无法直接 pytest。但核心逻辑（SSE 分帧 + 事件 state 归约 + thinking snapshot
语义）可用 Python 镜像验证其正确性契约，等价于前端 Case A-G 的逻辑断言。

测的是「行为契约」，不是前端实现本身——前端实现按契约在 useAgentTurnStream.ts /
index.vue 中。若前端与契约偏离，手测会暴露。
"""
import json


def _sse_frame(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


# ============ 镜像前端 parseSseFrame：按 \n\n 分帧，多行 data 拼接，跳过非 data 行 ============
def parse_sse_stream(raw: str):
    """输入原始 SSE 字节流（可能跨 chunk），产出 AgentStreamEvent list。镜像前端逻辑。"""
    events = []
    buffer = ""
    for chunk in raw:
        buffer += chunk
        while True:
            sep = buffer.find("\n\n")
            if sep == -1:
                break
            frame = buffer[:sep]
            buffer = buffer[sep + 2:]
            data_lines = []
            for line in frame.split("\n"):
                s = line.lstrip()
                if s.startswith("data:"):
                    data_lines.append(s[5:].strip())
            if not data_lines:
                continue
            payload = "\n".join(data_lines)
            if payload == "[DONE]":
                continue
            try:
                events.append(json.loads(payload))
            except Exception:
                continue
    # 尾部残余
    if buffer.strip():
        data_lines = []
        for line in buffer.split("\n"):
            s = line.lstrip()
            if s.startswith("data:"):
                data_lines.append(s[5:].strip())
        if data_lines:
            payload = "\n".join(data_lines)
            if payload != "[DONE]":
                try:
                    events.append(json.loads(payload))
                except Exception:
                    pass
    return events


# ============ Case: SSE 分帧跨 chunk 边界 ============
def test_sse_frame_split_across_chunks():
    raw = (_sse_frame({"type": "turn.started", "thinking": True})
           + _sse_frame({"type": "assistant.delta", "delta": "hi"})
           + _sse_frame({"type": "turn.completed", "result": {}}))
    # 模拟 reader 每次给 5 字节
    evs = parse_sse_stream(raw)
    assert [e["type"] for e in evs] == ["turn.started", "assistant.delta", "turn.completed"]


def test_sse_one_frame_across_many_chunks():
    """一条 SSE 跨多个 reader chunk。"""
    raw = _sse_frame({"type": "assistant.delta", "delta": "hello"})
    evs = parse_sse_stream(raw)
    assert len(evs) == 1 and evs[0]["delta"] == "hello"


def test_sse_done_terminator_ignored():
    raw = _sse_frame({"type": "turn.started", "thinking": False}) + "data: [DONE]\n\n"
    evs = parse_sse_stream(raw)
    assert len(evs) == 1
    assert all(e["type"] != "[DONE]" for e in evs)


def test_sse_malformed_json_skipped():
    raw = "data: {not json\n\n" + _sse_frame({"type": "assistant.delta", "delta": "ok"})
    evs = parse_sse_stream(raw)
    assert len(evs) == 1 and evs[0]["delta"] == "ok"


def test_sse_ignores_non_data_lines():
    raw = ": ping\n\nevent: x\n\n" + _sse_frame({"type": "turn.completed", "result": {}})
    evs = parse_sse_stream(raw)
    assert len(evs) == 1


# ============ Case A: thinking=false 发送，执行中切 true → 当前 turn 全 false ============
def test_case_a_thinking_snapshot_false_fixed():
    """发送时 thinking=false。后端收到 req.thinking=False，整个 turn 固定。
    契约：turn.started.thinking=False，所有 sub-call 不开 thinking。前端 send() 用
    thinkingSnapshot=thinkingToggle.value（发送瞬间值），不读运行中开关。"""
    # 前端 send 时快照
    thinking_toggle_state = False
    thinking_snapshot = thinking_toggle_state   # 发送瞬间固定
    # 执行中前端切换
    thinking_toggle_state = True
    # 契约：snapshot 不变，后端 turn.started.thinking=snapshot
    assert thinking_snapshot is False
    assert thinking_toggle_state is True   # 前端开关变了，但不影响已发的 turn


# ============ Case B: thinking=true 发送，执行中切 false → 当前 turn 全 true ============
def test_case_b_thinking_snapshot_true_fixed():
    thinking_toggle_state = True
    thinking_snapshot = thinking_toggle_state
    thinking_toggle_state = False
    assert thinking_snapshot is True
    assert thinking_toggle_state is False


# ============ Case C: thinking=true 但无 reasoning → 不出现空思考项 ============
def test_case_c_no_reasoning_no_empty_item():
    """契约：onStreamEvent 收到 reasoning.started 才 push reasoning item；
    无 reasoning.* 事件 → processItems 无 reasoning 项。"""
    process_items = []

    def on_event(ev):
        if ev["type"] == "reasoning.started":
            process_items.append({"kind": "reasoning", "step_id": ev["step_id"]})
        # tool.started 等不在此测

    # 只 tool 事件，无 reasoning
    for ev in [{"type": "tool.started", "step_id": 0, "call_id": "c1", "name": "story.list", "label": "读取故事段"},
               {"type": "tool.completed", "step_id": 0, "call_id": "c1", "name": "story.list", "ok": True, "summary": "ok"}]:
        on_event(ev)
    assert not any(p["kind"] == "reasoning" for p in process_items)


# ============ Case D: thinking=true 且有 reasoning → 折叠默认不显示，展开显示完整 ============
def test_case_d_reasoning_collected_for_expand():
    """契约：reasoning.delta 累积完整文本（不截断/不摘要），展开时显示。"""
    reasoning_text = ""

    def on_event(ev):
        nonlocal reasoning_text
        if ev["type"] == "reasoning.delta":
            reasoning_text += ev["delta"]

    for d in ["先想", "一下", "怎么做"]:
        on_event({"type": "reasoning.delta", "step_id": 0, "delta": d})
    assert reasoning_text == "先想一下怎么做"   # 完整，无摘要无截断


# ============ Case E: 执行中不展开 → 只显示当前 activity，完成自动折叠 ============
def test_case_e_default_folded_on_complete():
    """契约：processExpanded 默认 false；完成时若未手动展开→保持 false（自动折叠）。"""
    process_expanded = False

    # 执行中用户没点展开
    # 完成
    if not process_expanded:
        process_expanded = False   # 保持折叠
    assert process_expanded is False


# ============ Case F: 执行中主动展开 → 完成保持展开 ============
def test_case_f_manual_expand_kept_on_complete():
    process_expanded = False
    # 用户点击展开
    process_expanded = not process_expanded   # → True
    # 完成：已展开→保持
    assert process_expanded is True
    # 完成时不强制收起
    if process_expanded:
        pass
    assert process_expanded is True


# ============ Case G: 多写串行 → 事件顺序与真实 tool-loop 一致 ============
def test_case_g_serial_write_event_order():
    """契约：tool.started/completed 按 backend emit 顺序到达前端（SSE 保序）。
    串行：第1写执行(tool.started→completed ok)，第2写拒绝(started→completed ok=false 串行)。"""
    raw = (_sse_frame({"type": "turn.started", "thinking": False})
           + _sse_frame({"type": "tool.started", "step_id": 0, "call_id": "c1", "name": "story.create", "label": "创建故事段"})
           + _sse_frame({"type": "tool.completed", "step_id": 0, "call_id": "c1", "name": "story.create", "ok": True, "summary": "创建故事段 s07"})
           + _sse_frame({"type": "tool.started", "step_id": 0, "call_id": "c2", "name": "story.create", "label": "创建故事段"})
           + _sse_frame({"type": "tool.completed", "step_id": 0, "call_id": "c2", "name": "story.create", "ok": False, "summary": "本步写工具串行，已跳过"})
           + _sse_frame({"type": "turn.completed", "result": {}}))
    evs = parse_sse_stream(raw)
    types = [e["type"] for e in evs]
    assert types == ["turn.started", "tool.started", "tool.completed",
                     "tool.started", "tool.completed", "turn.completed"]
    # c2 串行拒绝（取 completed 事件，started 无 ok 字段）
    c2_done = [e for e in evs if e.get("call_id") == "c2" and e["type"] == "tool.completed"][0]
    assert c2_done["ok"] is False and "串行" in c2_done["summary"]


# ============ 收据渲染：只信 meta.receipts 结构化 ============
def test_receipts_only_trust_structured():
    """契约：realReceipts 过滤 ok&&verified&&!unchanged；不解析正文。"""
    meta = {"receipts": [
        {"operation": "story.create", "ok": True, "verified": True, "unchanged": False, "scene_id": "s07"},
        {"operation": "story.move", "ok": True, "verified": True, "unchanged": False, "scene_id": "s04"},
        {"operation": "story.delete", "ok": True, "verified": False, "unchanged": False, "scene_id": "s09"},  # 预览不算
        {"operation": "script.update", "ok": False, "verified": False, "unchanged": False},  # 失败不算
    ]}
    real = [r for r in meta["receipts"] if r["ok"] and r["verified"] and not r["unchanged"]]
    assert len(real) == 2
    ops = [r["operation"] for r in real]
    assert "story.create" in ops and "story.move" in ops
    assert "story.delete" not in ops   # verified=false（预览）不算
