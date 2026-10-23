# tools/relstore/project_registry.py
# G4-A：六份活跃项目管理状态机（本地隔离，FakeCAS，零远端）。
#
# 设计依据：G4A-CONSENSUS-20261022-R2 §3-§5。
# 核心纪律：
# - 排名按全局单调 activity_seq（B1 权威序号），绝不由本地墙钟/浏览/心跳决定；
# - 离线活动 = 带 operation_id 的待同步意图，不是已确认权威结果；
# - 超额仅标记（MARKED），资料不移动不删除；本批 residency 永不进 EVICTED；
# - 归档中源项目变化 → SUPERSEDED，旧回执不得成为删除新原件的许可；
# - 所有事件 operation_id 幂等；CAS 冲突败者候选保留，不静默覆盖。
from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

# ---- 业务状态枚举 ----
PROD_IN_PROGRESS = "IN_PROGRESS"
PROD_PAUSED = "PAUSED"
PROD_COMPLETE = "COMPLETE"

RES_RESIDENT = "RESIDENT"
RES_EVICTED = "EVICTED"  # G4-A 不可达

SLOT_ACTIVE = "ACTIVE"
SLOT_MARKED = "MARKED"

ARC_NONE = "NONE"
ARC_PLANNED = "PLANNED"
ARC_SNAPSHOTTING = "SNAPSHOTTING"
ARC_UPLOADING = "UPLOADING"
ARC_VERIFIED_BYTES = "VERIFIED_BYTES"
ARC_VERIFIED_RESTORABLE = "VERIFIED_RESTORABLE"
ARC_FAILED = "FAILED"
ARC_SUPERSEDED = "SUPERSEDED"

SLOT_CAPACITY = 6  # 六份活跃名额（逻辑，非物理硬限）

# 有效制作活动（触发重排）；§3 明示
EFFECTIVE_ACTIVITY_KINDS = frozenset({
    "save_shot", "save_storyboard", "save_scene", "save_character",
    "generate_frame", "generate_video", "generate_audio",
    "compose_sequence", "save_project_state", "resource_saved",
    "save_input",  # 保存剧本输入
})
# 非有效活动（不刷新排名）；§3 明示
NON_EFFECTIVE_KINDS = frozenset({
    "open", "browse", "preview", "heartbeat", "save_failed", "api_retry",
})

RETRIEVE_REASSESS_DAYS = 3.0
DAY_SECONDS = 86400.0


@dataclass
class ProjectRecord:
    project_id: str
    alias: str
    project_revision: int = 0
    snapshot_fingerprint: str | None = None
    last_activity_seq: int = 0
    last_activity_kind: str = ""
    last_activity_at: float = 0.0  # 墙钟，仅信息性，不参与排名
    production_status: str = PROD_IN_PROGRESS
    residency: str = RES_RESIDENT
    slot_status: str = SLOT_ACTIVE
    archive_status: str = ARC_NONE
    archive_ref: dict | None = None
    retrieved_at: float | None = None
    active_operation_id: str | None = None
    open_jobs: int = 0
    marked_at_seq: int | None = None
    retrieved_reassess_at: float | None = None
    pending_sync_intents: list = field(default_factory=list)
    missing_required: list = field(default_factory=list)  # 依赖缺口（影响 full/partial）

    def is_effective_activity_candidate(self) -> bool:
        return self.production_status in (PROD_IN_PROGRESS, PROD_PAUSED)


@dataclass
class RegistryState:
    registry_revision: int = 1
    activity_seq: int = 0
    projects: dict = field(default_factory=dict)  # project_id -> ProjectRecord
    archive_queue: list = field(default_factory=list)  # project_id 有序
    operation_dedup: dict = field(default_factory=dict)  # op_id -> {kind, result, at}

    def all_resident(self) -> list:
        return [p for p in self.projects.values() if p.residency == RES_RESIDENT]

    def active_slot_holders(self) -> list:
        """当前 ACTIVE slot 占用者，按 activity_seq 降序。"""
        return sorted([p for p in self.all_resident() if p.slot_status == SLOT_ACTIVE],
                      key=lambda r: -r.last_activity_seq)

    def marked(self) -> list:
        return [p for p in self.all_resident() if p.slot_status == SLOT_MARKED]


# ---------------- 事件 / 操作 ----------------

@dataclass
class Operation:
    operation_id: str
    kind: str
    project_id: str
    payload: dict = field(default_factory=dict)
    at: float = 0.0


def new_operation_id() -> str:
    return "op_" + uuid.uuid4().hex[:12]


def _now() -> float:
    return time.time()


# ---------------- FakeCAS（B1 单文件原子提交模拟）----------------

class CasConflict(Exception):
    """CAS 冲突：败者候选保留，不静默覆盖。"""
    def __init__(self, msg, *, expected, actual, candidate):
        super().__init__(msg)
        self.expected = expected
        self.actual = actual
        self.candidate = candidate


class RegistryPoisoned(RuntimeError):
    """CAS 冲突后本实例内存与权威已分叉；须丢弃实例并从权威 CAS 重载重判。
    写与读均 fail-closed：读取结果不得被前台误认为权威已确认的排名。"""


class RegistryOutcomeUnknown(RuntimeError):
    """提交结果未知（超时/回执丢失）：操作可能已或未落权威。
    须先 GET 权威状态按 operation_id 对账；对账前不得显示为成功、
    不得在旧实例继续写。与明确的 CAS 409 冲突是不同异常。"""


class FakeCAS:
    """模拟 B1 Contents API 单文件 CAS。
    - exists=False 时两写手同时 create：一胜一 CasConflict（G4-A 假设；真实 GitHub 行为 G4-B 待实测）。
    - exists=True 时带 expected_revision 更新：不符即 CasConflict。
    全进程内存，零 socket。
    """

    def __init__(self):
        self._doc = None  # 当前权威文档（None=文件不存在）
        self._revision = 0  # 0 = 不存在；>0 = 存在的 CAS 版本号
        self.history = []

    def read(self) -> dict | None:
        if self._doc is None:
            return None
        return {"doc": self._doc, "revision": self._revision}

    def commit(self, new_doc: dict, expected_revision: int | None,
               *, operation_id: str) -> dict:
        """expected_revision=None 表示文件必须尚不存在（首次创建）。"""
        if self._doc is None:
            # 文件不存在
            if expected_revision is not None:
                raise CasConflict("文件不存在但传了 expected_revision（语义错误）",
                                  expected=expected_revision, actual=None,
                                  candidate=new_doc)
            self._doc = new_doc
            self._revision = 1
            self.history.append(("create", operation_id, 1))
            return {"ok": True, "http": 201, "revision": 1, "winner": operation_id}
        # 文件存在：必须 expected_revision == 当前
        if expected_revision != self._revision:
            raise CasConflict(
                f"CAS 冲突：expected revision {expected_revision} != current {self._revision}",
                expected=expected_revision, actual=self._revision,
                candidate=new_doc)
        self._doc = new_doc
        self._revision += 1
        self.history.append(("update", operation_id, self._revision))
        return {"ok": True, "http": 200, "revision": self._revision,
                "winner": operation_id}


# ---------------- 状态机：核心操作 ----------------

class Registry:
    """六份活跃项目管理状态机。所有写操作经 Registry.commit(op) → 状态转换。

    G4-A：本地内存态 + FakeCAS 持久化模拟。不触网、不删数据。
    """

    def __init__(self, cas: FakeCAS | None = None, *, now: Callable = _now):
        self.cas = cas or FakeCAS()
        self.now = now
        self.state = RegistryState()
        self._poisoned: str | None = None  # None | "conflict" | "outcome_unknown"
        self._load_or_init()

    def _poison(self, reason: str):
        self._poisoned = reason

    def _guard_reads(self):
        """毒化实例读也 fail-closed：内存排名可能已分叉，不得冒充权威。"""
        if self._poisoned == "outcome_unknown":
            raise RegistryOutcomeUnknown(
                "实例提交结果未知（超时/回执丢失），内存状态不可作为权威读取；"
                "须先 GET 权威按 operation_id 对账")
        if self._poisoned == "conflict":
            raise RegistryPoisoned(
                "实例在 CAS 冲突后内存与权威分叉，读取结果不可作为权威排名；"
                "须丢弃本实例并从权威 CAS 重新加载")
        # 事后取证可经 self.state 原始字段直接访问（绕过本守卫，仅限诊断）

    def _load_or_init(self):
        cur = self.cas.read()
        if cur is not None:
            self.state = from_doc(cur["doc"])
            self.state.registry_revision = cur["revision"]  # CAS revision 权威
            self._cas_revision = cur["revision"]
        else:
            self.state = RegistryState()
            self._cas_revision = None  # 文件不存在：首次 create 用 None

    # ---- 持久化（CAS）----
    def to_doc(self) -> dict:
        import dataclasses, json
        return {
            "registry_revision": self.state.registry_revision,
            "activity_seq": self.state.activity_seq,
            "projects": {pid: dataclasses.asdict(r) for pid, r in self.state.projects.items()},
            "archive_queue": list(self.state.archive_queue),
            # 必须 deep copy：dedup value（{kind,...,result}）与 result dict 会以
            # 引用返回给调用方/被实例侧持有；浅拷贝下提交后对嵌套对象的任何
            # 修改会"渗透"进已提交的权威快照（内存 CAS 正确性隐患，B2.0a 实测）。
            "operation_dedup": copy.deepcopy(self.state.operation_dedup),
        }

    def _persist(self) -> int:
        """把当前 state 经 CAS 原子提交。返回新 revision；冲突抛 CasConflict。
        候选文档在提交前构造 registry_revision=expected+1（原始 JSON 与真实 CAS
        版本一致，不依赖加载器回填修正）。"""
        candidate = self.to_doc()
        candidate["registry_revision"] = (self._cas_revision or 0) + 1
        try:
            result = self.cas.commit(candidate, self._cas_revision,
                                     operation_id="registry")
        except CasConflict:
            self._poison("conflict")
            raise
        except Exception as exc:
            self._poison("outcome_unknown")
            raise RegistryOutcomeUnknown(
                "CAS 提交结果未知（非冲突异常：超时/回执丢失等）；"
                "操作可能已或未落权威，须先 GET 按 operation_id 对账，"
                "不得当作成功") from exc
        if result["ok"]:
            self._cas_revision = result["revision"]
            self.state.registry_revision = result["revision"]
        return result["revision"]

    def _persist_with_expected(self, expected_revision: int | None) -> dict:
        """显式 expected（用于双写手测试模拟读取后并发提交）。"""
        candidate = self.to_doc()
        candidate["registry_revision"] = (expected_revision or 0) + 1
        try:
            return self.cas.commit(candidate, expected_revision,
                                   operation_id="registry")
        except CasConflict:
            self._poison("conflict")
            raise
        except Exception as exc:
            self._poison("outcome_unknown")
            raise RegistryOutcomeUnknown(
                "CAS 提交结果未知（非冲突异常：超时/回执丢失等）；"
                "操作可能已或未落权威，须先 GET 按 operation_id 对账，"
                "不得当作成功") from exc

    # ---- 读取（纯查询，毒化实例 fail-closed）----
    def ranking(self) -> list:
        """按 activity_seq 降序的 resident 项目（ACTIVE + MARKED 都在）。"""
        self._guard_reads()
        return sorted(self.all_resident(), key=lambda r: -r.last_activity_seq)

    def all_resident(self) -> list:
        self._guard_reads()
        return self.state.all_resident()

    def active_slot_holders(self) -> list:
        self._guard_reads()
        return self.state.active_slot_holders()

    def marked(self) -> list:
        self._guard_reads()
        return self.state.marked()

    def get(self, project_id: str) -> ProjectRecord | None:
        self._guard_reads()
        return self.state.projects.get(project_id)

    # ---- 操作：创建项目 ----
    def create_project(self, *, project_id: str, alias: str,
                        operation_id: str | None = None) -> ProjectRecord:
        op = self._dedupe(operation_id, "create_project",
                          project_id, {"alias": alias})
        if op is not None:
            return self.get(project_id)
        if project_id in self.state.projects:
            raise ValueError(f"project_id 已存在: {project_id}")
        self.state.activity_seq += 1
        rec = ProjectRecord(
            project_id=project_id, alias=alias,
            last_activity_seq=self.state.activity_seq,
            last_activity_kind="create",
            last_activity_at=self.now())
        self.state.projects[project_id] = rec
        self._rebalance_slots()
        self._record_op(operation_id or new_operation_id(), "create_project",
                        project_id, {"ok": True})
        self._persist()
        return rec

    # ---- 操作：记录活动 ----
    def record_activity(self, *, project_id: str, kind: str,
                        operation_id: str | None = None,
                        project_revision: int | None = None,
                        snapshot_fingerprint: str | None = None,
                        offline_intent: bool = False) -> dict:
        """记录一次活动。返回 {effective, ranked, reason}。
        - 非有效活动（preview/heartbeat 等）→ 不刷新排名；
        - 有效活动 → activity_seq+1，更新 last_activity_seq，重排；
        - offline_intent=True → 进 pending_sync_intents，不动权威 activity_seq（离线未确认）；
        - 保存失败（save_failed）→ 不刷新，且不产生待同步意图。
        """
        op = self._dedupe(operation_id, "record_activity",
                          project_id, {"kind": kind})
        if op is not None:
            return op["result"]
        rec = self.get(project_id)
        if rec is None:
            raise KeyError(project_id)
        if kind in NON_EFFECTIVE_KINDS:
            # 非有效活动（preview/heartbeat 等）零效果：不入 dedup、不持久化。
            # 每次持久化在 B1 上是一次真实 PUT + 一个 Git commit；重放这类
            # 操作永远安全（无状态变化），故幂等无需 dedup 表背书。
            return {"effective": False, "ranked": False,
                    "reason": "non-effective", "persisted": False}
        if kind not in EFFECTIVE_ACTIVITY_KINDS:
            raise ValueError(f"未知活动 kind: {kind}（须在 EFFECTIVE/NON_EFFECTIVE 之一）")
        if offline_intent:
            # 离线待同步：记意图，不动权威排名
            intent = {"operation_id": operation_id or new_operation_id(),
                       "kind": kind, "at": self.now(),
                       "project_revision": project_revision,
                       "snapshot_fingerprint": snapshot_fingerprint}
            rec.pending_sync_intents.append(intent)
            self._record_op(intent["operation_id"], "record_activity",
                            project_id, {"effective": False, "ranked": False,
                              "reason": "offline pending sync intent"})
            self._persist()
            return {"effective": False, "ranked": False,
                    "reason": "offline pending (not confirmed authority)",
                    "intent_id": intent["operation_id"]}
        # 有效活动：刷新权威排名
        self.state.activity_seq += 1
        rec.last_activity_seq = self.state.activity_seq
        rec.last_activity_kind = kind
        rec.last_activity_at = self.now()
        if project_revision is not None:
            rec.project_revision = project_revision
        if snapshot_fingerprint is not None:
            rec.snapshot_fingerprint = snapshot_fingerprint
        # 标记项目有效活动 → 可能重返前六 → 取消标记
        was_marked = rec.slot_status == SLOT_MARKED
        self._rebalance_slots()
        still_marked = rec.slot_status == SLOT_MARKED
        result = {"effective": True, "ranked": True,
                  "activity_seq": rec.last_activity_seq,
                  "unmarked": was_marked and not still_marked}
        self._record_op(operation_id or new_operation_id(), "record_activity",
                        project_id, result)
        self._persist()
        return result

    # ---- 名额再平衡 ----
    def _rebalance_slots(self):
        """按 activity_seq 降序：前 SLOT_CAPACITY 名 ACTIVE，其余 MARKED。
        MARKED 项目获有效活动后 activity_seq 升高 → 直接置换最低 ACTIVE。"""
        resident = self.all_resident()
        ranked = sorted(resident, key=lambda r: -r.last_activity_seq)
        for i, r in enumerate(ranked):
            if i < SLOT_CAPACITY:
                if r.slot_status != SLOT_ACTIVE:
                    r.slot_status = SLOT_ACTIVE
                    r.marked_at_seq = None
            else:
                if r.slot_status != SLOT_MARKED:
                    r.slot_status = SLOT_MARKED
                    if r.marked_at_seq is None:
                        r.marked_at_seq = self.state.activity_seq

    # ---- 归档意图 ----
    def plan_archive(self, *, project_id: str,
                      operation_id: str | None = None) -> dict:
        op = self._dedupe(operation_id, "plan_archive", project_id, {})
        if op is not None:
            return op["result"]
        rec = self.get(project_id)
        if rec is None:
            raise KeyError(project_id)
        if rec.archive_status not in (ARC_NONE, ARC_FAILED, ARC_SUPERSEDED):
            raise ValueError(f"归档进行中，不可重新 plan: {rec.archive_status}")
        rec.archive_status = ARC_PLANNED
        if project_id not in self.state.archive_queue:
            self.state.archive_queue.append(project_id)
        self._record_op(operation_id or new_operation_id(), "plan_archive",
                        project_id, {"ok": True, "archive_status": ARC_PLANNED})
        self._persist()
        return {"ok": True, "archive_status": ARC_PLANNED}

    def begin_snapshot(self, *, project_id: str, snapshot_fingerprint: str,
                       operation_id: str | None = None) -> dict:
        op = self._dedupe(operation_id, "begin_snapshot", project_id,
                          {"snapshot_fingerprint": snapshot_fingerprint})
        if op is not None:
            return op["result"]
        rec = self.get(project_id)
        if rec is None:
            raise KeyError(project_id)
        if rec.archive_status != ARC_PLANNED:
            raise ValueError(f"须先 PLANNED: {rec.archive_status}")
        rec.archive_status = ARC_SNAPSHOTTING
        rec.snapshot_fingerprint = snapshot_fingerprint
        self._record_op(operation_id or new_operation_id(), "begin_snapshot",
                        project_id, {"ok": True})
        self._persist()
        return {"ok": True}

    def supersede_archive(self, *, project_id: str, new_fingerprint: str,
                          operation_id: str | None = None) -> dict:
        """归档中源项目变化 → SUPERSEDED，绝不产生删除许可。"""
        op = self._dedupe(operation_id, "supersede_archive", project_id,
                          {"new_fingerprint": new_fingerprint})
        if op is not None:
            return op["result"]
        rec = self.get(project_id)
        if rec is None:
            raise KeyError(project_id)
        if rec.archive_status not in (ARC_SNAPSHOTTING, ARC_UPLOADING,
                                      ARC_VERIFIED_BYTES):
            raise ValueError(f"不在归档进行态: {rec.archive_status}")
        rec.archive_status = ARC_SUPERSEDED
        rec.snapshot_fingerprint = new_fingerprint
        self._record_op(operation_id or new_operation_id(), "supersede_archive",
                        project_id, {"ok": True, "superseded": True,
                                     "delete_permit": False})
        self._persist()
        return {"ok": True, "superseded": True, "delete_permit": False}

    def set_verified_restorable(self, *, project_id: str, archive_ref: dict,
                                restorable_level: str,
                                operation_id: str | None = None) -> dict:
        op = self._dedupe(operation_id, "set_verified_restorable", project_id,
                          {"archive_ref": archive_ref})
        if op is not None:
            return op["result"]
        rec = self.get(project_id)
        if rec is None:
            raise KeyError(project_id)
        # R-008 修正（B2.3a）：服务端前置检查，不再只依赖调用方判断。
        # 回执仅对「归档进行中且指纹与权威快照一致」的快照有效；
        # SUPERSEDED/无有效快照/指纹不符一律 fail-closed。
        # 拒绝路径在任何 mutation 之前，不改 archive_status/archive_ref/
        # registry_revision/排名，不产生 CAS PUT，也不毒化实例。
        if rec.archive_status not in (ARC_SNAPSHOTTING, ARC_UPLOADING,
                                      ARC_VERIFIED_BYTES):
            raise ValueError(
                f"R-008: 回执仅对归档进行中的快照有效，当前 {rec.archive_status}")
        if rec.snapshot_fingerprint is None:
            raise ValueError("R-008: 无有效快照指纹（snapshot_fingerprint 为空）")
        if archive_ref.get("tree_fingerprint") != rec.snapshot_fingerprint:
            raise ValueError(
                "R-008: 回执指纹与权威快照指纹不符 "
                f"({archive_ref.get('tree_fingerprint')!r} != "
                f"{rec.snapshot_fingerprint!r})")
        rec.archive_status = ARC_VERIFIED_RESTORABLE
        rec.archive_ref = archive_ref
        rec.missing_required = (archive_ref.get("missing_required") or
                                 ([] if restorable_level == "full" else
                                  ["unknown"]))
        # full 必须依赖齐全；partial 不得标 full
        self._record_op(operation_id or new_operation_id(),
                        "set_verified_restorable", project_id,
                        {"ok": True, "level": restorable_level,
                         "evict_permitted": False})  # G4-A 永不释放
        self._persist()
        return {"ok": True, "level": restorable_level, "evict_permitted": False}

    # ---- 取回 ----
    def retrieve(self, *, project_id: str, archive_ref: dict,
                 operation_id: str | None = None) -> dict:
        """从 B 取回旧项目。先验证完整恢复（调用方负责），再入列。
        retrieved_reassess_at = now + 3 天（仅重新评估触发器，不自动删）。
        """
        op = self._dedupe(operation_id, "retrieve", project_id,
                          {"archive_ref": archive_ref})
        if op is not None:
            return op["result"]
        rec = self.get(project_id)
        if rec is None:
            raise KeyError(project_id)
        rec.archive_ref = archive_ref
        rec.retrieved_at = self.now()
        rec.retrieved_reassess_at = self.now() + RETRIEVE_REASSESS_DAYS * DAY_SECONDS
        rec.residency = RES_RESIDENT
        rec.archive_status = ARC_NONE  # 取回后归档状态清零（重新可归档）
        # 取回占用一个活跃名额 → 可能触发标记
        self.state.activity_seq += 1
        rec.last_activity_seq = self.state.activity_seq
        rec.last_activity_kind = "retrieve"
        rec.last_activity_at = self.now()
        self._rebalance_slots()
        self._record_op(operation_id or new_operation_id(), "retrieve",
                        project_id, {"ok": True,
                                      "reassess_at": rec.retrieved_reassess_at,
                                      "auto_delete": False})
        self._persist()
        return {"ok": True, "reassess_at": rec.retrieved_reassess_at,
                "auto_delete": False}

    def reassess_retrieved(self, *, project_id: str, now: float | None = None,
                           operation_id: str | None = None) -> dict:
        """取回三天重新评估（G4-A.1 P0-1 修正）：
        - ACTIVE（仍在 top-6）→ 仅清计时器，不进归档队列（3 天规则不旁路六份）；
        - MARKED（已跌出 top-6）→ 可进归档候选（不删）；
        - 有有效活动 → 延后；
        - 永不自动物理删除。"""
        op = self._dedupe(operation_id, "reassess_retrieved", project_id, {})
        if op is not None:
            return op["result"]
        rec = self.get(project_id)
        if rec is None:
            raise KeyError(project_id)
        if rec.retrieved_reassess_at is None:
            return {"ok": False, "reason": "not retrieved"}
        n = self.now() if now is None else now
        if n < rec.retrieved_reassess_at:
            return {"ok": True, "reassess": False, "reason": "not due"}
        # 到期
        op_id = operation_id or new_operation_id()
        rec.retrieved_reassess_at = None  # 清计时器（无论后续动作）
        if rec.last_activity_kind != "retrieve":
            # 有有效活动 → 延后
            rec.retrieved_reassess_at = n + RETRIEVE_REASSESS_DAYS * DAY_SECONDS
            result = {"ok": True, "reassess": True, "action": "postpone",
                      "auto_delete": False}
            self._record_op(op_id, "reassess_retrieved", project_id, result)
            self._persist()
            return result
        # 无后续有效活动
        if rec.slot_status == SLOT_ACTIVE:
            # P0-1 修正：仍在 top-6 → 不进归档队列，仅清计时器
            result = {"ok": True, "reassess": True,
                      "action": "no_archive_still_active",
                      "auto_delete": False, "slot_status": "ACTIVE"}
            self._record_op(op_id, "reassess_retrieved", project_id, result)
            self._persist()
            return result
        # MARKED（已跌出 top-6）→ 可进归档候选
        self.plan_archive(project_id=project_id)
        result = {"ok": True, "reassess": True, "action": "plan_archive",
                  "auto_delete": False, "slot_status": "MARKED"}
        self._record_op(op_id, "reassess_retrieved", project_id, result)
        self._persist()
        return result

    # ---- open_jobs / 释放保护 ----
    def set_open_jobs(self, *, project_id: str, count: int,
                      operation_id: str | None = None) -> dict:
        op = self._dedupe(operation_id, "set_open_jobs", project_id, {"count": count})
        if op is not None:
            return op["result"]
        rec = self.get(project_id)
        if rec is None:
            raise KeyError(project_id)
        rec.open_jobs = count
        self._record_op(operation_id or new_operation_id(), "set_open_jobs",
                        project_id, {"ok": True, "open_jobs": count})
        self._persist()
        return {"ok": True, "open_jobs": count}

    def attempt_evict(self, *, project_id: str,
                      operation_id: str | None = None) -> dict:
        """G4-A：释放尝试永远被拒（EVICTED 不可达）。"""
        rec = self.get(project_id)
        if rec is None:
            raise KeyError(project_id)
        reasons = []
        if rec.archive_status != ARC_VERIFIED_RESTORABLE:
            reasons.append(f"archive_status={rec.archive_status} 非 VERIFIED_RESTORABLE")
        if rec.open_jobs > 0:
            reasons.append(f"open_jobs={rec.open_jobs} > 0")
        if rec.missing_required:
            reasons.append(f"missing_required={rec.missing_required}")
        if rec.residency != RES_RESIDENT:
            reasons.append("not resident")
        # 即使全过，G4-A 仍拒绝（未做末次单写者 CAS 原子确认）
        reasons.append("G4-A: evict 禁用（未做末次整树指纹+云端 CAS 原子确认）")
        self._record_op(operation_id or new_operation_id(), "attempt_evict",
                        project_id, {"permitted": False, "reasons": reasons})
        return {"permitted": False, "reasons": reasons}

    # ---- operation 幂等 ----
    def _dedupe(self, operation_id, kind, project_id, payload) -> dict | None:
        if self._poisoned == "outcome_unknown":
            raise RegistryOutcomeUnknown(
                "实例存在结果未知的提交（超时/回执丢失），GET 权威按 operation_id "
                "对账前不得继续写；须丢弃本实例并从权威 CAS 重新加载")
        if self._poisoned == "conflict":
            raise RegistryPoisoned(
                "实例在 CAS 冲突后内存与权威分叉（activity_seq/dedup/排名已变动"
                "且未获权威确认）；须丢弃本实例并从权威 CAS 重新加载后重判")
        if operation_id is None:
            return None
        if operation_id in self.state.operation_dedup:
            return self.state.operation_dedup[operation_id]
        return None

    def _record_op(self, operation_id, kind, project_id, result):
        self.state.operation_dedup[operation_id] = {
            "kind": kind, "project_id": project_id,
            "result": result, "at": self.now()}


def from_doc(doc: dict) -> RegistryState:
    import dataclasses
    st = RegistryState(
        registry_revision=doc.get("registry_revision", 1),
        activity_seq=doc.get("activity_seq", 0),
        archive_queue=list(doc.get("archive_queue", [])),
        # 必须 deep copy：来源可能是内存 CAS 的权威文档本身；直接引用会让
        # pending_sync_intents/missing_required/archive_ref/嵌套 dedup value
        # 与权威共享，实例侧修改会反向污染权威（B2.0a 实测）。
        operation_dedup=copy.deepcopy(doc.get("operation_dedup", {})))
    for pid, r in doc.get("projects", {}).items():
        st.projects[pid] = ProjectRecord(**copy.deepcopy(r))
    return st
