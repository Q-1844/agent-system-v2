"""Scene Agent 实例执行链测试（mock，ScriptedClient 不联网）。

覆盖：e2e 交错模式（MD delegate → 实例读+submit → MD commit → canonical 变化）/
submit E1–E6 校验路径（bounce 码 + 修复指引）/ supersede / 实例白名单物理无
写工具 / 提案落盘与 delegation 状态机。
"""
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents import director as D
from src.agents.subagent import tools as T
from src.agents.subagent import registry as reg
from src.agents.subagent import instance as I
from src.agents.subagent import fingerprints as fp


def make_proj(dirpath):
    s01 = [Shot(shot_id="s01_c01", scene_id="s01", index=1, description="外景",
                characters=["林晚星"]),
           Shot(shot_id="s01_c02", scene_id="s01", index=2, description="走廊",
                source_shot_id="s01_c01", opening_source="inherit_previous")]
    proj = Project(
        name="demo", style=StyleAnchor(), story_text="正文。",
        characters=[Character(name="林晚星", description_en="a keeper")],
        scenes=[Scene(scene_id="s01", summary="段一", characters=["林晚星"], shots=s01),
                Scene(scene_id="s02", summary="段二", characters=[], shots=[])],
        next_scene_seq=3)
    ProjectStore(dirpath).save(proj)
    return proj


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


def _tc(name, arguments, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)}}


def rt_for(path, client=None, enabled=True):
    sp = path / "agents_data" / "settings.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps({"subagent_enabled": enabled}), encoding="utf-8")
    return AgentRuntime(project_dir_resolver=lambda name: path,
                        settings_path=sp, client=client)


@pytest.fixture(autouse=True)
def _restore():
    yield
    T.unmount_subagent_tools()
    reg.reset_turn_budget()


def _run(coro):
    return asyncio.run(coro)


def _only_proposal_id(tmp_path):
    root = tmp_path / "output" / "agents" / "scene_agents" / "proposals"
    files = sorted(root.glob("prop_*.json"))
    assert files, "proposal store 为空"
    return json.loads(files[-1].read_text(encoding="utf-8"))["proposal_id"]


# ============ e2e：交错模式（任务书阶段 7 指定形态） ============
def test_e2e_delegate_submit_then_commit(tmp_path):
    make_proj(tmp_path)
    basis = fp.scene_slice_fingerprint(ProjectStore(tmp_path).load().scenes[0])
    end_state = [
        {"shot_id": "s01_c01", "chain": False,
         "fields": {"description": "外景（改：黄昏）"}, "rationale": "黄昏更贴情绪",
         "dependencies": [{"kind": "scene_content", "ref": "current"}]},
        {"shot_id": "s01_c02", "chain": True, "fields": {},
         "rationale": "保持接力走廊",
         "dependencies": [{"kind": "shot", "ref": "s01_c01"}]},
        {"shot_id": None, "chain": True,
         "fields": {"description": "新结尾：灯塔亮起"},
         "rationale": "收束段情绪", "dependencies": []},
    ]
    client = ScriptedClient([
        # ---- MD turn 1：发起委派 ----
        (None, [_tc("scene.delegate", {
            "scene_id": "s01", "task_brief": "重做本段，强化黄昏氛围",
            "signal": {"kind": "declared", "evidence": "单段大规模重设计"}})]),
        # ---- 实例（同一 client 队列，天然按时间序交错）----
        (None, [_tc("story.read", {"scene_id": "s01"})]),
        (None, [_tc("shot.list", {"scene_id": "s01"})]),
        (None, [_tc("proposal.submit", {
            "basis": {"scene_slice_fingerprint": basis},
            "end_state": end_state,
            "removed_shot_rationales": {},
            "constraint_responses": [],
            "border_change_requests": [],
            "design_notes": "以黄昏收束本段",
            "read_evidence": {"n_read_calls": 2}})]),
        ("提案完成：3 镜端态已提交。", None),
        # ---- MD turn 1 收尾：转述摘要 ----
        ("s01 设计实例已产出提案，等待我裁决。", None),
    ])
    rt = rt_for(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(D.director_turn(rt, "demo", sid, "把 s01 整段重做，强化黄昏氛围"))
    assert "提案" in res["assistant_message"]
    pid = _only_proposal_id(tmp_path)
    # delegation 状态 proposed；proposal 落盘带端态与理由
    st = reg.SubagentStore(tmp_path, "demo")
    d = st.list_delegations()[0]
    assert d["status"] == "proposed" and d["proposal_id"] == pid
    # 实例调用时只见白名单工具（物理无写工具）
    inst_calls = client.calls[1:5]   # 实例的 4 次调用
    for c in inst_calls:
        names = {t["function"]["name"] for t in c["kw"]["tools"]}
        assert names <= I._scene_agent_tools()
        assert not (names & {"shot.create", "shot.update", "shot.delete",
                             "shot.move", "shot.create_many"})
    # ---- MD turn 2：commit ----
    client.script = [
        (None, [_tc("proposal.commit", {"proposal_id": pid,
                                        "decision_rationale": "端态符合全片节奏，采纳"})]),
        ("已通过提案改写 s01。", None),
    ]
    res2 = _run(D.director_turn(rt, "demo", sid, "采纳这份提案"))
    assert res2["intent"] == "shot_create"      # 旗标分支（proposal.commit 汇总）
    proj = ProjectStore(tmp_path).load()
    scene = proj.scenes[0]
    descs = [sh.description for sh in scene.shots]
    assert descs[0].endswith("（改：黄昏）")
    assert len(descs) == 3
    assert scene.shots[2].source_shot_id == scene.shots[1].shot_id   # 新镜先建后锚
    # E2：决策记忆入库（commit 成功后）
    mem = st.load_memory("s01")
    assert any("黄昏更贴情绪" in e["text"] for e in mem)
    assert any("以黄昏收束本段" in e["text"] for e in mem)
    # 全局决策记忆（E3）
    assert any("采纳" in e["text"] for e in st.load_global_memory())
    # delegation → committed
    assert st.list_delegations()[0]["status"] == "committed"


def test_e2e_reject_no_memory_entry(tmp_path):
    """E2：rejected 的 rationale 不入段记忆（只留 proposal store）。"""
    make_proj(tmp_path)
    basis = fp.scene_slice_fingerprint(ProjectStore(tmp_path).load().scenes[0])
    end_state = [
        {"shot_id": "s01_c01", "chain": False, "fields": {}, "rationale": "理由X"},
        {"shot_id": "s01_c02", "chain": True, "fields": {}}]
    client = ScriptedClient([
        (None, [_tc("scene.delegate", {
            "scene_id": "s01", "task_brief": "重做",
            "signal": {"kind": "declared", "evidence": "大改"}})]),
        (None, [_tc("story.read", {"scene_id": "s01"})]),
        (None, [_tc("shot.list", {"scene_id": "s01"})]),
        (None, [_tc("proposal.submit", {
            "basis": {"scene_slice_fingerprint": basis},
            "end_state": end_state, "removed_shot_rationales": {},
            "design_notes": "被否决的设计说明"})]),
        ("提案已提交。", None),
        ("等待裁决。", None),
    ])
    rt = rt_for(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(D.director_turn(rt, "demo", sid, "重做 s01"))
    pid = _only_proposal_id(tmp_path)
    client.script = [
        (None, [_tc("proposal.reject", {
            "proposal_id": pid, "kind": "rejected",
            "reasons": ["节奏不符"], "decision_rationale": "全片节奏不合适"})]),
        ("已否决。", None),
    ]
    _run(D.director_turn(rt, "demo", sid, "否决它"))
    st = reg.SubagentStore(tmp_path, "demo")
    assert st.load_memory("s01") == []          # rejected 不入库
    d = st.list_delegations()[0]
    assert d["status"] == "rejected"
    # canonical 零改动
    assert len(ProjectStore(tmp_path).load().scenes[0].shots) == 2


# ============ submit 校验路径（E1–E6 bounce 码） ============
def _submit_direct(tmp_path, submit_args, *, prelude=None, fresh=True):
    """直接构造实例上下文调 submit 闭包（绕过 LLM，校验路径单测）。
    prelude：额外喂进 trace 的（工具名, obs）序列——模拟实例已真实读取。
    fresh=False：不重建项目（供已手工改过 canonical 的用例用）。"""
    if fresh:
        make_proj(tmp_path)
    rt = rt_for(tmp_path, client=None)
    proj = ProjectStore(tmp_path).load()
    scene = proj.scenes[0]
    basis = fp.compute_scene_basis(scene, proj.scenes)
    delegation = {"delegation_id": "dlg_t", "scene_id": "s01",
                  "task_brief": "t", "basis": basis}
    state: dict = {"trace": []}
    for tool, obs in (prelude or []):
        state["trace"].append({"step": 0, "tool": tool, "target": "s01",
                               "result": "ok", "revision": obs, "ok": True})
    executor = I._make_submit_executor(rt, "demo", delegation, state)
    return executor(rt, "demo", json.dumps(submit_args, ensure_ascii=False), "t", "m")


def _basis_fp(tmp_path):
    return fp.scene_slice_fingerprint(ProjectStore(tmp_path).load().scenes[0])


GOOD_END = [
    {"shot_id": "s01_c01", "chain": False, "fields": {}},
    {"shot_id": "s01_c02", "chain": True, "fields": {}}]


def _reads(tmp_path):
    """标准预读：story.read（scene token）+ shot.list（结构 token）。"""
    proj = ProjectStore(tmp_path).load()
    scene = proj.scenes[0]
    return [("story.read", fp.scene_content_token(scene)),
            ("shot.list", fp.shots_structure_token(proj.scenes))]


def test_submit_ok_and_store(tmp_path):
    make_proj(tmp_path)
    r = _submit_direct(tmp_path, {
        "basis": {"scene_slice_fingerprint": _basis_fp(tmp_path)},
        "end_state": GOOD_END, "removed_shot_rationales": {},
        "design_notes": "ok"}, prelude=_reads(tmp_path))
    assert r["ok"], r
    assert r["proposal_id"].startswith("prop_")
    assert r["summary"]["shots"]["before"] == 2


def test_submit_read_first(tmp_path):
    """E5：无任何读取记录 → read_first（F2 机制在提案域的拦截）。"""
    make_proj(tmp_path)
    r = _submit_direct(tmp_path, {
        "basis": {"scene_slice_fingerprint": _basis_fp(tmp_path)},
        "end_state": GOOD_END, "removed_shot_rationales": {}},
        prelude=[])
    assert not r["ok"] and r["code"] == "read_first"
    assert r["fix_hint"]


def test_submit_basis_fabricated_no_story_read(tmp_path):
    """E4：basis 内容组件不在真实读取记录中（没 story.read）→ fabricated。"""
    make_proj(tmp_path)
    proj = ProjectStore(tmp_path).load()
    r = _submit_direct(tmp_path, {
        "basis": {"scene_slice_fingerprint": _basis_fp(tmp_path)},
        "end_state": GOOD_END, "removed_shot_rationales": {}},
        prelude=[("shot.list", fp.shots_structure_token(proj.scenes))])
    assert not r["ok"] and r["code"] == "basis_fabricated"
    assert "story.read" in r["fix_hint"]


def test_submit_basis_fabricated_no_shot_list(tmp_path):
    make_proj(tmp_path)
    proj = ProjectStore(tmp_path).load()
    scene = proj.scenes[0]
    # 有段级读（shot.read 满足 E5）但没 shot.list → 组件防伪触发
    r = _submit_direct(tmp_path, {
        "basis": {"scene_slice_fingerprint": _basis_fp(tmp_path)},
        "end_state": GOOD_END, "removed_shot_rationales": {}},
        prelude=[("story.read", fp.scene_content_token(scene)),
                 ("shot.read", "anytoken")])
    assert not r["ok"] and r["code"] == "basis_fabricated"
    assert "shot.list" in r["fix_hint"]


def test_submit_basis_fabricated_wrong_value(tmp_path):
    """E4：basis 值与校准基准不符（编造）→ fabricated。"""
    make_proj(tmp_path)
    r = _submit_direct(tmp_path, {
        "basis": {"scene_slice_fingerprint": "deadbeef"},
        "end_state": GOOD_END, "removed_shot_rationales": {}},
        prelude=_reads(tmp_path))
    assert not r["ok"] and r["code"] == "basis_fabricated"


def test_submit_basis_stale(tmp_path):
    """E4：飞行期间段被改 → 当前现算 ≠ 基准 → stale（提示重读重交）。"""
    make_proj(tmp_path)
    # 先固定 delegation 的校准基准（原始状态），再改 canonical
    proj = ProjectStore(tmp_path).load()
    scene0 = proj.scenes[0]
    contract_basis = fp.compute_scene_basis(scene0, proj.scenes)
    proj.get_shot("s01_c01").description = "中途被改"
    ProjectStore(tmp_path).save(proj)
    # 实例重读过（trace 有段级读），提交当前现算的新指纹
    rt = rt_for(tmp_path, client=None)
    delegation = {"delegation_id": "dlg_t", "scene_id": "s01",
                  "task_brief": "t", "basis": contract_basis}
    state = {"trace": [{"step": 0, "tool": "shot.list", "target": "s01",
                        "result": "ok", "revision": "whatever", "ok": True}]}
    executor = I._make_submit_executor(rt, "demo", delegation, state)
    cur = fp.scene_slice_fingerprint(ProjectStore(tmp_path).load().scenes[0])
    r = executor(rt, "demo", json.dumps({
        "basis": {"scene_slice_fingerprint": cur},
        "end_state": GOOD_END, "removed_shot_rationales": {}},
        ensure_ascii=False), "t", "m")
    assert not r["ok"] and r["code"] == "basis_stale"
    assert "重新读取" in r["fix_hint"]


def test_submit_contract_mismatch(tmp_path):
    make_proj(tmp_path)
    r = _submit_direct(tmp_path, {
        "scene_id": "s02",   # 不是本实例的段
        "basis": {"scene_slice_fingerprint": _basis_fp(tmp_path)},
        "end_state": GOOD_END, "removed_shot_rationales": {}},
        prelude=_reads(tmp_path))
    assert not r["ok"] and r["code"] == "contract_mismatch"


def test_submit_undeclared_deletion(tmp_path):
    make_proj(tmp_path)
    r = _submit_direct(tmp_path, {
        "basis": {"scene_slice_fingerprint": _basis_fp(tmp_path)},
        "end_state": [{"shot_id": "s01_c01", "chain": False, "fields": {}}],
        "removed_shot_rationales": {}},   # c02 缺席无理由
        prelude=_reads(tmp_path))
    assert not r["ok"] and r["code"] == "undeclared_deletion"
    assert "s01_c02" in r["error"]


def test_submit_bad_end_state(tmp_path):
    make_proj(tmp_path)
    r = _submit_direct(tmp_path, {
        "basis": {"scene_slice_fingerprint": _basis_fp(tmp_path)},
        "end_state": [{"shot_id": "s01_c01", "chain": True, "fields": {}},
                      {"shot_id": "s01_c02", "chain": True, "fields": {}}],
        "removed_shot_rationales": {}},
        prelude=_reads(tmp_path))
    assert not r["ok"] and r["code"] == "bad_end_state"


def test_submit_supersede(tmp_path):
    """多次提交合法：后者 supersede 前者（同一实例闭包共享 delegation 状态）。"""
    make_proj(tmp_path)
    rt = rt_for(tmp_path, client=None)
    proj = ProjectStore(tmp_path).load()
    delegation = {"delegation_id": "dlg_t", "scene_id": "s01",
                  "task_brief": "t",
                  "basis": fp.compute_scene_basis(proj.scenes[0], proj.scenes)}
    reads = [("story.read", fp.scene_content_token(proj.scenes[0])),
             ("shot.list", fp.shots_structure_token(proj.scenes))]
    state = {"trace": [{"step": 0, "tool": t, "target": "s01", "result": "ok",
                        "revision": v, "ok": True} for t, v in reads]}
    executor = I._make_submit_executor(rt, "demo", delegation, state)
    basis = _basis_fp(tmp_path)
    r1 = executor(rt, "demo", json.dumps({
        "basis": {"scene_slice_fingerprint": basis},
        "end_state": GOOD_END, "removed_shot_rationales": {},
        "design_notes": "v1"}, ensure_ascii=False), "t", "m")
    assert r1["ok"], r1
    r2 = executor(rt, "demo", json.dumps({
        "basis": {"scene_slice_fingerprint": basis},
        "end_state": GOOD_END, "removed_shot_rationales": {},
        "design_notes": "v2"}, ensure_ascii=False), "t", "m")
    assert r2["ok"] and r2["proposal_id"] != r1["proposal_id"]
    st = reg.SubagentStore(tmp_path, "demo")
    old = st.load_proposal(r1["proposal_id"])
    assert old["superseded_by"] == r2["proposal_id"]
    d = st.load_delegation("dlg_t")
    assert d["proposal_id"] == r2["proposal_id"]


def test_instance_injection_contains_required_sections(tmp_path):
    """注入包含全部 §3.2 模板段（状态头/邻接/brief/记忆/任务书/格式）。"""
    make_proj(tmp_path)
    rt = rt_for(tmp_path, client=None)
    proj = ProjectStore(tmp_path).load()
    delegation = {"delegation_id": "dlg_i", "scene_id": "s01",
                  "task_brief": "测试任务书内容",
                  "basis": fp.compute_scene_basis(proj.scenes[0], proj.scenes)}
    text = I.build_instance_instruction(rt, "demo", delegation, proj.scenes[0])
    assert "Scene Agent 任务开启" in text
    assert "段状态头" in text and "s01_c01" in text
    assert "接力 s01_c01" in text           # 接力边可见
    assert "邻接与边界" in text
    assert "上游段：无（本段是第一段）" in text
    assert "下游段" in text and "s02" in text       # s01 的下游是 s02（空段）
    assert "测试任务书内容" in text
    assert "proposal.submit" in text
    assert "先验设计决策" in text
    assert "本段指纹" in text and fp.scene_slice_fingerprint(proj.scenes[0]) in text


# ============ locked_border_constraints（D-3 补齐：Part 2 §2.3/§4.4-E3）============
def _submit_with_locks(tmp_path, submit_args, locks, *, prelude=None):
    """带锁定边界约束的 submit 直调（复用 _submit_direct 的夹具语义）。
    submit_args 里的 basis 占位符 "__FP__" 会被替换为项目建立后的真实指纹
    （参数求值先于 make_proj，故不能在调用点现算）。"""
    make_proj(tmp_path)
    rt = rt_for(tmp_path, client=None)
    proj = ProjectStore(tmp_path).load()
    scene = proj.scenes[0]
    submit_args = dict(submit_args)
    if submit_args.get("basis", {}).get("scene_slice_fingerprint") == "__FP__":
        submit_args["basis"] = {"scene_slice_fingerprint":
                                fp.scene_slice_fingerprint(scene)}
    delegation = {"delegation_id": "dlg_lc", "scene_id": "s01",
                  "task_brief": "t", "basis": fp.compute_scene_basis(scene, proj.scenes),
                  "locked_border_constraints": locks}
    state: dict = {"trace": []}
    for tool, obs in (prelude if prelude is not None else _reads(tmp_path)):
        state["trace"].append({"step": 0, "tool": tool, "target": "s01",
                               "result": "ok", "revision": obs, "ok": True})
    executor = I._make_submit_executor(rt, "demo", delegation, state)
    return executor(rt, "demo", json.dumps(submit_args, ensure_ascii=False), "t", "m"), rt


def test_locked_constraints_rendered_in_instruction(tmp_path):
    """注入包渲染锁定约束（带编号与中英方向标注）。"""
    make_proj(tmp_path)
    rt = rt_for(tmp_path, client=None)
    proj = ProjectStore(tmp_path).load()
    delegation = {"delegation_id": "dlg_i2", "scene_id": "s01",
                  "task_brief": "t", "basis": fp.compute_scene_basis(proj.scenes[0], proj.scenes),
                  "locked_border_constraints": [
                      {"constraint_id": "bc_1", "applies_to": "exit",
                       "statement": "出口必须停在暴雨未歇"}]}
    text = I.build_instance_instruction(rt, "demo", delegation, proj.scenes[0])
    assert "bc_1" in text and "出口必须停在暴雨未歇" in text
    assert "constraint_responses" in text          # 回应义务明示
    # 无约束时保持占位（bit-for-bit 于既有行为）
    text2 = I.build_instance_instruction(
        rt, "demo", {k: v for k, v in delegation.items()
                     if k != "locked_border_constraints"}, proj.scenes[0])
    assert "无结构化锁定约束" in text2


def test_submit_missing_constraint_response_bounced(tmp_path):
    """E3 上膛：有锁定约束但 constraint_responses 缺回应 → bounce 带修复指引。"""
    locks = [{"constraint_id": "bc_1", "applies_to": "exit", "statement": "出口停在暴雨"}]
    r, _ = _submit_with_locks(tmp_path, {
        "basis": {"scene_slice_fingerprint": "__FP__"},
        "end_state": GOOD_END, "removed_shot_rationales": {},
        "constraint_responses": []}, locks)
    assert not r["ok"] and r["code"] == "missing_constraint_response"
    assert "bc_1" in r["error"] and r["fix_hint"]


def test_submit_with_constraint_response_ok_and_persisted(tmp_path):
    """回应齐全 → 提交成功，proposal 持久化 constraint_ids（commit 路径复验依据）。"""
    locks = [{"constraint_id": "bc_1", "applies_to": "exit", "statement": "出口停在暴雨"},
             {"constraint_id": "bc_2", "applies_to": "entry", "statement": "承接上段夜戏"}]
    r, rt = _submit_with_locks(tmp_path, {
        "basis": {"scene_slice_fingerprint": "__FP__"},
        "end_state": GOOD_END, "removed_shot_rationales": {},
        "constraint_responses": [
            {"constraint_id": "bc_1", "response": "末镜保持雨势未减弱"},
            {"constraint_id": "bc_2", "response": "首镜延续夜戏光线"}]}, locks)
    assert r["ok"], r
    from src.agents.subagent import registry as _reg
    store = _reg.SubagentStore(rt._resolve_project_dir("demo"), "demo")
    p = store.load_proposal(r["proposal_id"])
    assert p["constraint_ids"] == ["bc_1", "bc_2"]
    assert len(p["constraint_responses"]) == 2
    # commit 路径复验：validate_end_state 以 proposal.constraint_ids 上膛（E3 第二时点）
    from src.agents.subagent.diff_engine import normalize_end_state, validate_end_state
    validate_end_state(normalize_end_state(p["end_state"]),
                       ProjectStore(tmp_path).load().scenes[0],
                       constraint_ids=p["constraint_ids"],
                       constraint_responses=p["constraint_responses"])
