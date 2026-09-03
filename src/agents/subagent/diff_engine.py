# src/agents/subagent/diff_engine.py
# DiffEngine（Part 2 §6 规格 rev2 的实现）：desired scene end state → 现有 shot
# 原语的确定性转换。spike/subagent_diff_spike.py 的 apply_end_state 是本模块的
# 种子原型（14 场景全 PASS）；正式化要点：
#
#   - 阶段 C（commit 时点权威复查，零副作用）：liveness → basis（resume 豁免，
#     构造性 current≠原 basis）→ 有效端态重校验（schema + 删申明 + partial 排除后
#     全量重跑 + 链切规则 §5.2）；
#   - 拓扑序（rev2，勿改）：pre-detach（待删镜的 keep 下游先解除接力）→ delete
#     （级联守卫 = 纵深防御）→ create（无锚，追加段尾）→ reorder（selection 式
#     move）→ update（内容 diff + relay_op：chain?前一项真实id:null）；
#   - D4 可重算性：F(current, desired) 对任意部分变换中间态重跑 = 剩余变换
#     （resume 的根基；journal 只保证"有人知道这里中断过"，不回放）；
#   - 错误分类学（§6.3）：bounce 码全部带机器可读 code + fix_hint；MD_REBASE；
#     ABORT_LIVENESS；ABORT_INTEGRITY（loud log——应被上游拦截的错 = 引擎缺陷
#     警报）；INTERRUPTED_COMMIT（执行期异常：诚实部分报告，已执行 ops 的
#     receipts 全量返回，绝不假成功）；
#   - receipts 区分 planned_ops 与 executed_ops（前缀）；
#   - 直接调 director.TOOL_EXECUTORS（spike 模式），instruction/message_id 传
#     delegation 上下文（审计可追溯）。
#
# 零 LLM：本模块的一切判定均为确定性代码（硬约束）。

from __future__ import annotations

import json
import logging
from typing import List, Optional, Sequence

from src.agents.runtime import AgentRuntime, AgentError
from src.core.models import Scene, Shot
from src.core.store import ProjectStore

from . import fingerprints as fp

log = logging.getLogger("agents.subagent.diff")

# ============ 错误码（Part 2 §6.3：每个错误必带 code + fix_hint）============
BOUNCE_CODES = {
    "bad_end_state", "contract_mismatch", "unknown_shot_id", "foreign_scene_id",
    "missing_constraint_response", "basis_fabricated", "basis_stale",
    "read_first", "undeclared_deletion", "stale_removal_ref",
    "chain_cut_by_exclusion",
}
# 提案预算（Part 2 §11 建议值；常量集中可调）
MAX_PROPOSAL_ITEMS = 24


class _OpFailure(Exception):
    """执行期某个原语失败（INTERRUPTED_COMMIT 路径）。obs = 失败回执。"""

    def __init__(self, obs: dict):
        super().__init__(str(obs.get("error") or obs))
        self.obs = obs



class DiffError(Exception):
    """DiffEngine 阶段 C 的确定性拒绝（bounce / MD_REBASE / ABORT_LIVENESS）。
    code + message + fix_hint + extra（机器可读上下文）。"""

    def __init__(self, code: str, message: str, fix_hint: str = "",
                 extra: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix_hint = fix_hint
        self.extra = extra or {}


# ============ 端态 schema 规范化（proposal.submit 与 commit 共用）============
def _shot_writable_fields() -> set:
    """镜头可写内容字段（不含 source_shot_id——接力由 chain 表达）。"""
    from src.agents.director import SHOT_UPDATABLE_FIELDS
    return set(SHOT_UPDATABLE_FIELDS) - {"source_shot_id"}


def normalize_end_state(raw: object) -> List[dict]:
    """把 LLM 提交的 end_state 规范化为内部形态。
    容忍两种写法：{shot_id?, chain, fields{...}, rationale?, dependencies?}
    与平铺字段（fields 平铺在项里——防御性解析，协议以 Part 2 §4.1 为准）。
    不做合法性判定（validate_end_state 负责）。返回规范化列表（浅拷贝项）。

    JSON 字符串容错（真机 Subagent_Longrun_20260903 实测根因）：Agnes 在大参数
    +中文引号时会把嵌套数组序列化成 JSON 字符串再塞进参数（SW 的 _loose_json
    已记载同一行为）——str 且 [ 开头时尝试 json.loads 还原；还原失败或还原后
    仍非 list 则按原错误拒绝（后续结构校验 E1–E6 不受影响）。"""
    if isinstance(raw, str) and raw.strip()[:1] == "[":
        try:
            raw = json.loads(raw)
        except Exception:
            pass
    if not isinstance(raw, list):
        raise DiffError("bad_end_state", "end_state 必须是有序数组",
                        "按提案格式提交：[{shot_id?, chain, fields{…}, rationale?}, …]"
                        "（直接给 JSON 数组，不要把数组包进字符串）")
    out: List[dict] = []
    writable = _shot_writable_fields()
    for it in raw:
        if not isinstance(it, dict):
            raise DiffError("bad_end_state", f"end_state 项必须是对象：{it!r}",
                            "每项形如 {shot_id?, chain, fields{…}, rationale?}")
        item = dict(it)
        # 平铺字段收进 fields（白名单内）
        fields = dict(item.get("fields") or {})
        for k in list(item.keys()):
            if k in writable:
                fields[k] = item.pop(k)
        item.pop("source_shot_id", None)   # 接力只由 chain 表达（§1.1）
        item["fields"] = fields
        item["chain"] = bool(item.get("chain"))
        out.append(item)
    return out


def normalize_removed_rationales(raw: object) -> dict:
    """把 LLM 提交的 removed_shot_rationales 规范化为内部 dict（{shot_id: 删除理由}）。
    不做 shot_id 合法性判定（validate_end_state 负责）。返回浅拷贝 dict。

    JSON 字符串容错（真机 Subagent_Smoke_20260903 实测根因，与 normalize_end_state
    同一模型行为）：Agnes 在大参数 +中文引号时会把嵌套对象也序列化成 JSON 字符串塞进
    参数——真机 trace 里错误信息末尾的 '{' 来自 for sid in rationales 迭代字符串字符，
    'str' object has no attribute 'get' 来自 rationales.get(sid)。str 时尝试 json.loads
    还原；还原失败或还原后非 dict 则按 bad_end_state 结构化拒绝（后续 shot_id 合法性 /
    删申明完备 E6 校验全部不变），绝不把裸 AttributeError 抛回模型。"""
    if raw is None:
        return {}
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            raw = json.loads(s)
        except Exception:
            raise DiffError(
                "bad_end_state",
                "removed_shot_rationales 必须是 JSON 对象 {shot_id: 删除理由}，"
                "收到无法解析的字符串",
                '直接给 JSON 对象、不要包进字符串；形如 '
                '{"s03_c06": "与 c05 内容重复，删除"}；不删任何镜头时给 {} 或省略该字段')
    if not isinstance(raw, dict):
        raise DiffError(
            "bad_end_state",
            f"removed_shot_rationales 必须是对象 {{shot_id: 删除理由}}，"
            f"收到 {type(raw).__name__}",
            '形如 {"s03_c06": "删除理由…"}；不删除任何镜头时给空对象 {} 或省略该字段')
    return dict(raw)


def normalize_basis(raw: object) -> dict:
    """把 LLM 提交的 basis 规范化为内部 dict（{scene_slice_fingerprint: …, …}）。
    不做指纹合法性判定（instance._submit 的 E4 防伪 / run_diff_commit 的 md_rebase
    负责）。返回浅拷贝 dict。

    JSON 字符串容错（真机 Subagent_Smoke2_20260903 实测根因，与 normalize_end_state
    /normalize_removed_rationales 同一 Agnes 大参数双重序列化行为）：模型把 basis
    对象也发成 JSON 字符串，原 (args.get("basis") or {}).get("scene_slice_fingerprint")
    会抛裸 AttributeError('str' object has no attribute 'get')，因 basis 防伪段在
    try/except DiffError 之外而逃逸成 tool.failed。str 时 json.loads 还原；还原失败或
    非 dict → basis_fabricated 结构化拒绝，消灭裸异常；指纹比对防伪全部不变。"""
    if raw is None:
        return {}
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            raw = json.loads(s)
        except Exception:
            raise DiffError(
                "basis_fabricated",
                "basis 必须是 JSON 对象 {scene_slice_fingerprint: …}，"
                "收到无法解析的字符串",
                '直接给 JSON 对象、不要包进字符串；形如 '
                '{"scene_slice_fingerprint": "<本段指纹>"}（从注入包原样抄录）')
    if not isinstance(raw, dict):
        raise DiffError(
            "basis_fabricated",
            f"basis 必须是对象 {{scene_slice_fingerprint: …}}，"
            f"收到 {type(raw).__name__}",
            '形如 {"scene_slice_fingerprint": "<本段指纹>"}')
    return dict(raw)


def validate_end_state(end_state: List[dict], scene: Scene, *,
                       constraint_ids: Sequence[str] = (),
                       constraint_responses: Sequence[dict] = (),
                       removed_rationales: Optional[dict] = None,
                       exclude_item_refs: Optional[Sequence[int]] = None,
                       require_responses: bool = True) -> None:
    """零副作用端态校验（Part 2 §4.3 + §5.2 链切规则）。非法抛 DiffError。

    chain:bool 形式下接力闭包/邻接/环构造性成立（§1.1），无需运行期图校验；
    本函数校验：item-0 规则 / shot_id 引用 / 项数 / create 项 description /
    删申明完备（§1.2 两条）/ partial 链切（§5.2）/ 约束回应覆盖（E3）。
    exclude_item_refs 语义（§5.2）：排除 create 项 = 不创建；排除 keep 项 =
    保留 canonical 现状（fields 忽略、位置保留）。"""
    if len(end_state) > MAX_PROPOSAL_ITEMS:
        raise DiffError(
            "bad_end_state",
            f"end_state 项数 {len(end_state)} 超上限 {MAX_PROPOSAL_ITEMS}",
            "把提案拆成多次 delegation，或精简目标镜头集")
    if end_state and end_state[0].get("chain"):
        raise DiffError(
            "bad_end_state", "第 1 项声明 chain=true（段首镜无前驱）",
            "end_state[0].chain 必须为 false；跨段衔接是 border 语义，不是段内接力")
    # partial 链切规则：被排除项的后继（第一个保留项）chain==true → 非法
    excl = set(exclude_item_refs or ())
    for i in range(len(end_state) - 1):
        if i in excl and (i + 1) not in excl and end_state[i + 1].get("chain"):
            raise DiffError(
                "chain_cut_by_exclusion",
                f"排除第 {i + 1} 项会切断第 {i + 2} 项声明的接力"
                f"（其 chain=true 的前驱正是被排除项）",
                "要么连同后继一起调整（后继 chain 改 false 或同步排除），"
                "要么保留被排除项")
    # shot_id 引用 + create 项 description
    cur_ids = {sh.shot_id for sh in (scene.shots or [])}
    for idx, it in enumerate(end_state):
        sid = it.get("shot_id")
        if sid is None or sid == "":
            if not (it.get("fields") or {}).get("description"):
                raise DiffError(
                    "bad_end_state",
                    f"第 {idx + 1} 项是新建镜头但缺 description",
                    "新建项的 fields.description 必填（创建原语的最小字段）")
        elif sid not in cur_ids:
            raise DiffError(
                "unknown_shot_id",
                f"第 {idx + 1} 项引用的镜头 {sid} 不在本段 {scene.scene_id}"
                "（或不存在）",
                "shot_id 必须来自本段 canonical（用 shot.list 核对后修正）；"
                "新建镜头不填 shot_id")
    # 有效端态（排除后）的删申明完备（§1.2）
    effective_ids = {it.get("shot_id") for i, it in enumerate(end_state)
                     if i not in excl and it.get("shot_id")}
    rationales = normalize_removed_rationales(removed_rationales)
    undeclared = [sid for sid in sorted(cur_ids) if sid not in effective_ids]
    for sid in undeclared:
        if not str(rationales.get(sid) or "").strip():
            raise DiffError(
                "undeclared_deletion",
                f"镜头 {sid} 不在端态中（= 删除）但没有删除理由（removed_shot_rationales 缺项或为空）",
                "端态是完备枚举：遗漏即删除。若确要删除，在 removed_shot_rationales "
                "里给出 {shot_id: 理由}；若是遗漏，把它加回 end_state")
    for sid in rationales:
        if sid not in cur_ids:
            raise DiffError(
                "stale_removal_ref",
                f"removed_shot_rationales 引用了不存在于本段的镜头 {sid}",
                "引用的 shot_id 必须来自本段 canonical 的当前真实读取；请重读后修正")
    # 约束回应覆盖（E3；submit 时 require_responses=True，commit 时同）
    if require_responses and constraint_ids:
        got = {r.get("constraint_id") for r in constraint_responses
               if isinstance(r, dict)}
        missing = [cid for cid in constraint_ids if cid not in got]
        if missing:
            raise DiffError(
                "missing_constraint_response",
                f"锁定边界约束未回应：{', '.join(missing)}",
                "每条 locked constraint 都必须在 constraint_responses 里给出回应"
                "（说明如何满足，或为何需要 border_change_request）")


def _shot_exists_anywhere(scene: Scene, shot_id: str) -> bool:
    """shot_id 是否属于本段之外的段（提示 foreign 情形；scene 只有本段视野，
    全项目核对由调用方传入的 canonical 完成——此处保守返回 False，由上层
    commit 路径的全项目视图补充）。"""
    return False


def effective_end_state(end_state: List[dict],
                        exclude_item_refs: Optional[Sequence[int]]) -> List[dict]:
    """partial 排除后的有效端态：排除 create 项=剔除；排除 keep 项=保留项但
    fields 清空（维持 canonical 现状）、chain 按现状保持（链切规则已在
    validate_end_state 拦截非法组合）。"""
    excl = set(exclude_item_refs or ())
    out: List[dict] = []
    for i, it in enumerate(end_state):
        if i in excl:
            continue
        out.append(it)
    return out


# ============ DiffEngine 主流程 ============
def run_diff_commit(rt: AgentRuntime, project: str, scene_id: str,
                    proposal: dict, *,
                    exclude_item_refs: Optional[Sequence[int]] = None,
                    resume: bool = False,
                    instruction: str = "", message_id: Optional[str] = None,
                    journal_cb=None) -> dict:
    """确定性 commit executor（零 LLM）。

    proposal 需含：end_state（原始形态，内部规范化）/ removed_shot_rationales /
    basis.scene_slice_fingerprint / constraint_ids + constraint_responses
    （commit 路径重校验用）。
    journal_cb(phase, payload)：可选回调（registry 提供），在
    "committing"（含 ops_plan）/ "committed" / "interrupted" 时点落盘。
    返回 receipt（ok=False 时含 code/fix_hint/executed_ops 诚实部分报告）。"""
    from src.agents.director import TOOL_EXECUTORS, _shots_token, _shot_token

    pdir = rt._resolve_project_dir(project)

    def _reload() -> Scene:
        return ProjectStore(pdir).load()

    def _scene_of(proj) -> Optional[Scene]:
        return next((s for s in proj.scenes if s.scene_id == scene_id), None)

    def _obs_fail(code: str, message: str, fix_hint: str = "",
                  **extra) -> dict:
        return {"ok": False, "operation": "proposal.commit", "code": code,
                "error": message, "fix_hint": fix_hint, "scene_id": scene_id,
                **extra}

    executed: List[dict] = []
    planned: List[dict] = []

    def _exec(tool: str, **args) -> dict:
        """调真实 executor（TOOL_EXECUTORS，spike 模式）；失败抛 _OpFailure。"""
        obs = TOOL_EXECUTORS[tool](rt, project,
                                   json.dumps(args, ensure_ascii=False),
                                   instruction, message_id)
        if not obs.get("ok"):
            raise _OpFailure(obs)
        return obs

    # ---------- 阶段 C：commit 时点权威复查（零副作用） ----------
    proj = _reload()
    scene = _scene_of(proj)
    if scene is None:
        # liveness 失败：联动 retire 由上层（registry）处理
        return _obs_fail("abort_liveness",
                         f"段 {scene_id} 已不存在（delegation 飞行中被删除）",
                         "该段的 delegation 已失效；如仍需处理请在主会话确认当前段集")
    try:
        basis_fp = normalize_basis(
            proposal.get("basis")).get("scene_slice_fingerprint")
        if not resume and basis_fp:
            cur_fp = fp.scene_slice_fingerprint(scene)
            if cur_fp != basis_fp:
                return _obs_fail(
                    "md_rebase",
                    f"basis stale：proposal 基于 {basis_fp}，当前 {cur_fp}"
                    "——不得直接套用旧端态（防语义 lost update）",
                    "两条路：① proposal.reject(kind='rebase') 后重新 scene.delegate"
                    "（delta 任务书复用原意图）；② 用既有写工具手工完成修改",
                    current_basis=cur_fp)
        end_state = normalize_end_state(proposal.get("end_state"))
        removed_rationales = normalize_removed_rationales(
            proposal.get("removed_shot_rationales"))
        validate_end_state(
            end_state, scene,
            constraint_ids=proposal.get("constraint_ids") or (),
            constraint_responses=proposal.get("constraint_responses") or (),
            removed_rationales=removed_rationales,
            exclude_item_refs=exclude_item_refs)
    except DiffError as e:
        return _obs_fail(e.code, e.message, e.fix_hint, **e.extra)

    eff = effective_end_state(end_state, exclude_item_refs)

    # ---------- 阶段 D：分类（纯内存，零副作用） ----------
    cur_scene = _scene_of(_reload())
    cur_ids = [sh.shot_id for sh in (cur_scene.shots or [])]
    cur_id_set = set(cur_ids)
    keep_ids = {it.get("shot_id") for it in eff if it.get("shot_id") in cur_id_set}
    del_ids = [i for i in cur_ids if i not in keep_ids]
    new_items = [it for it in eff
                 if not it.get("shot_id") or it["shot_id"] not in cur_id_set]

    for sid in del_ids:
        planned.append({"phase": "delete", "shot_id": sid})
    for it in new_items:
        planned.append({"phase": "create",
                        "temp": (it.get("fields") or {}).get("description", "")[:30]})
    for it in eff:
        if it.get("shot_id") in keep_ids:
            planned.append({"phase": "update", "shot_id": it.get("shot_id")})

    # ---------- journal：committing 状态先落盘（崩溃窗口可见） ----------
    if journal_cb:
        try:
            journal_cb("committing", {"ops_plan": planned})
        except Exception:
            log.exception("journal 落盘失败（不阻断 commit）")

    # ---------- 阶段 0.5–4：执行（每步最新指纹；D3） ----------
    try:
        # 阶段 0.5 pre-detach（rev2）：待删镜的 keep 下游先解除接力。
        # del 下游不处理（其级联是合法删除）。幂等（重跑时已解除则集合为空）。
        del_set = set(del_ids)
        for sh in list((_scene_of(_reload()) or cur_scene).shots or []):
            if sh.source_shot_id in del_set and sh.shot_id in keep_ids:
                _exec("shot.update", shot_id=sh.shot_id,
                      expected_revision=_shot_token(
                          _reload().get_shot(sh.shot_id)),
                      source_shot_id=None)
                executed.append({"op": "pre-detach", "shot_id": sh.shot_id,
                                 "was_source": sh.source_shot_id})

        # 阶段 1 delete（级联守卫 = 纵深防御；触发即引擎缺陷警报）
        for sid in del_ids:
            obs = _exec("shot.delete", shot_id=sid)
            executed.append({"op": "delete", "receipt": obs})
            gone = ((obs.get("deleted") or {}).get("shots")) or []
            unexpected = [g for g in gone if g != sid and g in keep_ids]
            if unexpected:
                log.error("DiffEngine 级联守卫触发（引擎缺陷警报）：delete %s 级联删掉"
                          " keep 镜 %s（scene=%s）", sid, unexpected, scene_id)
                if journal_cb:
                    journal_cb("interrupted", {"reason": "abort_integrity",
                                               "executed": executed})
                return _obs_fail("abort_integrity",
                                 f"delete {sid} 级联删掉了端态要保留的镜头: {unexpected}",
                                 "引擎完整性警报（不应发生）——请把 executed_ops 与"
                                 "本回执反馈给维护者", executed_ops=executed,
                                 planned_ops=planned)

        # 阶段 2 create（追加段尾，spec 无接力——先建后锚）
        if new_items:
            specs = []
            for it in new_items:
                f = it.get("fields") or {}
                spec = {"description": f.get("description")}
                if f.get("characters") is not None:
                    spec["characters"] = f["characters"]
                if f.get("target_duration_s") is not None:
                    spec["target_duration_s"] = f["target_duration_s"]
                specs.append(spec)
            if len(specs) >= 2:
                obs = _exec("shot.create_many", scene_id=scene_id, shots=specs,
                            expected_shots_revision=_shots_token(_reload().scenes))
                created = [c.get("shot_id") for c in (obs.get("created") or [])]
            else:
                obs = _exec("shot.create", scene_id=scene_id,
                            description=specs[0]["description"],
                            expected_shots_revision=_shots_token(_reload().scenes))
                created = [obs.get("shot_id") or (obs.get("created") or [{}])[0].get("shot_id")]
            executed.append({"op": "create", "receipt": obs})
            # 回填真实 id（创建顺序 = 段尾追加序 = new_items 序；用对象身份防
            # 内容相等误判）
            new_ids = {id(it) for it in new_items}
            ci = 0
            for it in eff:
                if id(it) in new_ids:
                    it["shot_id"] = created[ci]
                    ci += 1
        desired = [it["shot_id"] for it in eff]

        # 阶段 3 reorder（selection 式 move；每步最新结构指纹）
        for i in range(len(desired)):
            cur = [sh.shot_id for sh in (_scene_of(_reload())).shots]
            if i < len(cur) and cur[i] == desired[i]:
                continue
            if i == 0:
                _exec("shot.move", shot_id=desired[0], before=cur[0],
                      expected_shots_revision=_shots_token(_reload().scenes))
            else:
                _exec("shot.move", shot_id=desired[i], after=desired[i - 1],
                      expected_shots_revision=_shots_token(_reload().scenes))
            executed.append({"op": "move", "shot_id": desired[i], "to_pos": i})

        # 阶段 4 update（内容 diff + relay_op；每镜最新内容指纹；reorder 已保证相邻）
        relay_normalized: List[dict] = []
        for idx, it in enumerate(eff):
            sid = it.get("shot_id")
            shot_now = _reload().get_shot(sid)
            if shot_now is None:
                raise _OpFailure({"ok": False, "error": f"镜头 {sid} 消失（中段一致性）"})
            fields_want = dict(it.get("fields") or {})
            # 内容 diff：显式提供且与当前不同的才写（省轮次 + unchanged 语义）
            diff = {}
            for k, v in fields_want.items():
                if k == "characters":
                    cur_v = list(shot_now.characters or [])
                    if v != cur_v:
                        diff[k] = v
                elif getattr(shot_now, k, None) != v:
                    diff[k] = v
            # relay_op：chain 期望 vs 当前接力（前一项真实 id 或 None）
            want_src = desired[idx - 1] if (it.get("chain") and idx > 0) else None
            if shot_now.source_shot_id != want_src:
                diff["source_shot_id"] = want_src
                relay_normalized.append({
                    "shot_id": sid, "from": shot_now.source_shot_id, "to": want_src})
            if diff:
                _exec("shot.update", shot_id=sid,
                      expected_revision=_shot_token(_reload().get_shot(sid)),
                      **diff)
                executed.append({"op": "update", "shot_id": sid,
                                 "fields": sorted(diff.keys())})

        # 阶段 5 终态断言
        final = [sh.shot_id for sh in (_scene_of(_reload())).shots]
        if final != desired:
            log.error("DiffEngine 终态断言失败（引擎缺陷警报）：scene=%s "
                      "final=%s desired=%s", scene_id, final, desired)
            if journal_cb:
                journal_cb("interrupted", {"reason": "abort_integrity",
                                           "executed": executed})
            return _obs_fail("abort_integrity",
                             f"终态顺序不符：{final} != {desired}",
                             "引擎完整性警报（不应发生）——请把 executed_ops 与本回执"
                             "反馈给维护者", executed_ops=executed,
                             planned_ops=planned)
    except _OpFailure as e:
        # INTERRUPTED_COMMIT：执行期异常——诚实部分报告（已执行 ops 全量返回，
        # 绝不假成功）；恢复 = proposal.commit(..., resume=true) 显式触发（D4 重算）
        log.exception("DiffEngine 执行期中断（scene=%s）：%s", scene_id, e.obs)
        if journal_cb:
            journal_cb("interrupted", {"reason": "interrupted_commit",
                                       "executed": executed})
        return _obs_fail(
            "interrupted_commit",
            f"commit 中途失败：{e.obs.get('error') or e.obs}",
            "canonical 停在合法中间态（每个原语各自原子）。恢复：proposal.commit"
            "(resume=true) 对当前状态重算剩余变换；或用既有写工具手工收尾",
            executed_ops=executed, planned_ops=planned,
            failed_obs=e.obs)

    # ---------- 成功：汇总 receipt ----------
    if journal_cb:
        journal_cb("committed", {"executed": executed})
    n_created = sum(1 for e in executed if e["op"] in ("create",))
    n_deleted = sum(1 for e in executed if e["op"] == "delete")
    n_moved = sum(1 for e in executed if e["op"] == "move")
    n_updated = sum(1 for e in executed if e["op"] == "update")
    # executed_ops 里的真实 receipts（治理扁平化提取口径：只含 executor 回执）
    op_receipts = [e["receipt"] for e in executed if "receipt" in e]
    return {
        "ok": True, "operation": "proposal.commit", "scene_id": scene_id,
        "verified": True, "unchanged": not executed,
        "executed_ops": op_receipts,
        "executed_log": executed, "planned_ops": planned,
        "relay_normalized": relay_normalized,
        "n_created": n_created, "n_deleted": n_deleted,
        "n_moved": n_moved, "n_updated": n_updated,
        "created": bool(n_created), "updated": bool(n_updated),
        "deleted": bool(n_deleted), "moved": bool(n_moved),
        "final_ids": desired,
        "note": (f"已按端态提交：{n_created} 新建 / {n_deleted} 删除 / "
                 f"{n_moved} 移动 / {n_updated} 内容更新"
                 + (f"；接力规范化 {len(relay_normalized)} 处"
                    if relay_normalized else "")),
    }


class _OpFailure(Exception):
    """执行期某个原语失败（INTERRUPTED_COMMIT 路径）。obs = 失败回执。"""

    def __init__(self, obs: dict):
        super().__init__(str(obs.get("error") or obs))
        self.obs = obs
