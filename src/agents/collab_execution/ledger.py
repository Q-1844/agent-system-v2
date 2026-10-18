# R2 B1：协调账本（本机 SQLite，标准库 sqlite3）。
# 设计要点（03 文档 §B1）：
# - 全局一份本机账本；记录均带 project_instance_id 与 run_id。
# - 任务、候选、审核回执、接纳、事件有唯一键与状态版本（revision）。
# - 显式事务（BEGIN IMMEDIATE / COMMIT / ROLLBACK），busy 超时；写串行化锁避免
#   busy 风暴；不持数据库事务等待模型或 Provider（所有方法都是纯本地快速操作）。
# - 状态推进用条件更新（expected status/revision），重复事件不能倒退状态。
# - 正式 Project JSON 与现有 JobQueue 均保留；账本不另造 Provider worker。
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# 状态枚举（03 文档推荐状态；三维度分开：生成完 ≠ 审核过 ≠ 已接纳）
EXECUTION_STATES = ("pending", "running", "done", "failed", "cancelled", "unknown")
REVIEW_STATES = ("not_started", "running", "pass", "fail", "uncertain", "incomplete", "stale")
ACCEPTANCE_STATES = ("pending", "committing", "accepted", "rejected", "stale", "cancelled")
TASK_STATES = ("waiting_dependency", "ready", "queued", "running", "blocked_decision",
               "done", "failed", "cancelled")
# 终态：终态只进不出（与 collab_modes.save_run 终态保护同一不变量）
_TASK_TERMINAL = ("done", "failed", "cancelled")
_PROMOTION_TERMINAL = ("accepted", "rejected")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    project_instance_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    shot_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    input_signature TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    status_revision INTEGER NOT NULL DEFAULT 1,
    dispatch_key TEXT UNIQUE,
    job_id TEXT,
    ready_at REAL,
    enqueued_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    project_instance_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    task_id TEXT,
    job_id TEXT,
    shot_id TEXT NOT NULL,
    parent_candidate_id TEXT,
    artifact_path TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    provider_task_id TEXT,
    model TEXT,
    base_input_signature TEXT NOT NULL DEFAULT '',
    base_final_video TEXT,
    execution_state TEXT NOT NULL DEFAULT 'done',
    review_state TEXT NOT NULL DEFAULT 'not_started',
    acceptance_state TEXT NOT NULL DEFAULT 'pending',
    status_revision INTEGER NOT NULL DEFAULT 1,
    invalid_reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS review_receipts (
    receipt_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    input_baseline TEXT NOT NULL DEFAULT '',
    policy_version TEXT NOT NULL DEFAULT '',
    overall TEXT NOT NULL,
    hard_errors_json TEXT NOT NULL DEFAULT '[]',
    evidence_path TEXT,
    reviewed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS promotions (
    promotion_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    project_instance_id TEXT NOT NULL,
    run_id TEXT,
    shot_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    state TEXT NOT NULL,
    state_revision INTEGER NOT NULL DEFAULT 1,
    intent_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    promotion_id TEXT,
    candidate_id TEXT,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    delivered INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_shot ON candidates(project_instance_id, shot_id);
CREATE INDEX IF NOT EXISTS idx_promotions_candidate ON promotions(candidate_id);
CREATE INDEX IF NOT EXISTS idx_events_pending ON events(delivered);
"""


def _now() -> float:
    return time.time()


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class LedgerError(RuntimeError):
    """账本约束违反（唯一键冲突/状态条件不满足/记录不存在）。"""


class CollabLedger:
    """线程安全的本机协调账本。连接按线程持有；写操作经进程内锁串行化，
    配合 SQLite busy_timeout，双保险避免写竞争。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        with self._tx() as cur:
            cur.executescript(_SCHEMA)

    # ---- 连接与事务 ----
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    class _Tx:
        def __init__(self, ledger: "CollabLedger"):
            self.ledger = ledger
            self.conn: Optional[sqlite3.Connection] = None

        def __enter__(self) -> sqlite3.Cursor:
            self.ledger._write_lock.acquire()
            self.conn = self.ledger._conn()
            self.conn.execute("BEGIN IMMEDIATE")
            return self.conn.cursor()

        def __exit__(self, exc_type, exc, tb):
            try:
                if exc_type is None:
                    self.conn.commit()
                else:
                    self.conn.rollback()
            finally:
                self.ledger._write_lock.release()
            return False

    def _tx(self) -> "CollabLedger._Tx":
        return CollabLedger._Tx(self)

    # ---- 任务（确定性派发的节点） ----
    def register_task(self, *, task_id: str, project_instance_id: str, run_id: str,
                      kind: str, shot_id: Optional[str] = None,
                      payload: Optional[dict] = None,
                      depends_on: Optional[List[dict]] = None,
                      input_signature: str = "") -> dict:
        """登记依赖节点。depends_on 项形如 {"shot_id": ..., "any_accepted_of_run": ...}。
        无依赖条件 → ready；有依赖 → waiting_dependency（由调度器推进）。"""
        deps = list(depends_on or [])
        status = "waiting_dependency" if deps else "ready"
        row = {"task_id": task_id, "project_instance_id": project_instance_id,
               "run_id": run_id, "kind": kind, "shot_id": shot_id,
               "payload_json": json.dumps(payload or {}, ensure_ascii=False),
               "depends_on_json": json.dumps(deps, ensure_ascii=False),
               "input_signature": input_signature, "status": status,
               "ready_at": _now() if status == "ready" else None,
               "created_at": _now(), "updated_at": _now()}
        try:
            with self._tx() as cur:
                cols = ", ".join(row.keys())
                marks = ", ".join("?" for _ in row)
                cur.execute(f"INSERT INTO tasks ({cols}) VALUES ({marks})", list(row.values()))
        except sqlite3.IntegrityError as e:
            raise LedgerError(f"task register conflict: {task_id} ({e})") from e
        return self.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> Optional[dict]:
        row = self._conn().execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._task_from_row(row) if row else None

    def list_tasks(self, project_instance_id: Optional[str] = None,
                   run_id: Optional[str] = None) -> List[dict]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        args: List[Any] = []
        if project_instance_id:
            sql += " AND project_instance_id=?"
            args.append(project_instance_id)
        if run_id:
            sql += " AND run_id=?"
            args.append(run_id)
        rows = self._conn().execute(sql + " ORDER BY created_at", args).fetchall()
        return [self._task_from_row(r) for r in rows]

    def transition_task(self, task_id: str, *, to_status: str,
                        from_statuses: Optional[Iterable[str]] = None,
                        expect_revision: Optional[int] = None,
                        set_dispatch_key: Optional[str] = None,
                        set_job_id: Optional[str] = None) -> dict:
        """条件状态推进（CAS）：仅当当前状态在 from_statuses（缺省=不限）且 revision
        匹配（缺省=不限）时更新。终态（done/failed/cancelled）粘性——任何再推进
        都拒绝（重复事件不能倒退状态）。返回更新后记录；条件不满足抛 LedgerError。"""
        froms = list(from_statuses) if from_statuses is not None else None
        with self._tx() as cur:
            row = cur.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise LedgerError(f"task not found: {task_id}")
            if row["status"] in _TASK_TERMINAL:
                raise LedgerError(
                    f"task {task_id} is terminal ({row['status']}); no further transition")
            if froms is not None and row["status"] not in froms:
                raise LedgerError(
                    f"task {task_id} status {row['status']} not in {froms}")
            if expect_revision is not None and row["status_revision"] != expect_revision:
                raise LedgerError(
                    f"task {task_id} revision {row['status_revision']} != {expect_revision}")
            sets = ["status=?", "status_revision=status_revision+1", "updated_at=?"]
            args: List[Any] = [to_status, _now()]
            if to_status == "ready" and not row["ready_at"]:
                sets.append("ready_at=?")
                args.append(_now())
            if to_status == "queued":
                sets.append("enqueued_at=?")
                args.append(_now())
            if set_dispatch_key is not None:
                sets.append("dispatch_key=?")
                args.append(set_dispatch_key)
            if set_job_id is not None:
                sets.append("job_id=?")
                args.append(set_job_id)
            args.append(task_id)
            cur.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE task_id=?", args)
        out = self.get_task(task_id)
        assert out is not None
        return out

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["payload"] = json.loads(d.pop("payload_json"))
        d["depends_on"] = json.loads(d.pop("depends_on_json"))
        return d

    # ---- 候选 ----
    def register_candidate(self, *, candidate_id: Optional[str] = None,
                           project_instance_id: str, run_id: str,
                           task_id: Optional[str] = None, job_id: Optional[str] = None,
                           shot_id: str, parent_candidate_id: Optional[str] = None,
                           artifact_path: str, artifact_sha256: str,
                           provider_task_id: Optional[str] = None,
                           model: Optional[str] = None,
                           base_input_signature: str = "",
                           base_final_video: Optional[str] = None) -> dict:
        cid = candidate_id or _uid("cand")
        row = {"candidate_id": cid, "project_instance_id": project_instance_id,
               "run_id": run_id, "task_id": task_id, "job_id": job_id,
               "shot_id": shot_id, "parent_candidate_id": parent_candidate_id,
               "artifact_path": artifact_path, "artifact_sha256": artifact_sha256,
               "provider_task_id": provider_task_id, "model": model,
               "base_input_signature": base_input_signature,
               "base_final_video": base_final_video,
               "created_at": _now(), "updated_at": _now()}
        try:
            with self._tx() as cur:
                cols = ", ".join(row.keys())
                marks = ", ".join("?" for _ in row)
                cur.execute(f"INSERT INTO candidates ({cols}) VALUES ({marks})",
                            list(row.values()))
        except sqlite3.IntegrityError as e:
            raise LedgerError(f"candidate register conflict: {cid} ({e})") from e
        return self.get_candidate(cid)  # type: ignore[return-value]

    def get_candidate(self, candidate_id: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        return dict(row) if row else None

    def list_candidates(self, project_instance_id: Optional[str] = None,
                        shot_id: Optional[str] = None,
                        acceptance_state: Optional[str] = None) -> List[dict]:
        sql = "SELECT * FROM candidates WHERE 1=1"
        args: List[Any] = []
        if project_instance_id:
            sql += " AND project_instance_id=?"
            args.append(project_instance_id)
        if shot_id:
            sql += " AND shot_id=?"
            args.append(shot_id)
        if acceptance_state:
            sql += " AND acceptance_state=?"
            args.append(acceptance_state)
        return [dict(r) for r in self._conn().execute(
            sql + " ORDER BY created_at", args).fetchall()]

    _UNSET = object()

    def set_candidate_states(self, candidate_id: str, *,
                             execution_state: Any = _UNSET,
                             review_state: Any = _UNSET,
                             acceptance_state: Any = _UNSET,
                             invalid_reason: Any = _UNSET,
                             from_acceptance: Optional[Iterable[str]] = None) -> dict:
        """三维度状态分开推进（acceptance 支持条件 from_acceptance）。
        各可选字段用哨兵区分「未提供（不动）」与「显式清空/置值」。"""
        sets: List[str] = ["status_revision=status_revision+1", "updated_at=?"]
        args: List[Any] = [_now()]
        for col, val in (("execution_state", execution_state),
                         ("review_state", review_state),
                         ("acceptance_state", acceptance_state),
                         ("invalid_reason", invalid_reason)):
            if val is not CollabLedger._UNSET:
                sets.append(f"{col}=?")
                args.append(val)
        args.append(candidate_id)
        with self._tx() as cur:
            row = cur.execute("SELECT * FROM candidates WHERE candidate_id=?",
                              (candidate_id,)).fetchone()
            if row is None:
                raise LedgerError(f"candidate not found: {candidate_id}")
            if from_acceptance is not None and row["acceptance_state"] not in set(from_acceptance):
                raise LedgerError(
                    f"candidate {candidate_id} acceptance {row['acceptance_state']} "
                    f"not in {list(from_acceptance)}")
            cur.execute(f"UPDATE candidates SET {', '.join(sets)} WHERE candidate_id=?", args)
        return self.get_candidate(candidate_id)  # type: ignore[return-value]

    # ---- 审核回执 ----
    def attach_review(self, *, candidate_id: str, version_id: str,
                      content_sha256: str, overall: str,
                      input_baseline: str = "", policy_version: str = "",
                      hard_errors: Optional[List[str]] = None,
                      evidence_path: Optional[str] = None) -> dict:
        """审核回执落账并把候选 review_state 置为对应值。overall 必须是
        pass/fail/uncertain/incomplete 之一（stale 由失效机制写入，不由回执写入）。"""
        if overall not in ("pass", "fail", "uncertain", "incomplete"):
            raise LedgerError(f"invalid review overall: {overall}")
        rid = _uid("rcpt")
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO review_receipts (receipt_id, candidate_id, version_id,"
                " content_sha256, input_baseline, policy_version, overall,"
                " hard_errors_json, evidence_path, reviewed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, candidate_id, version_id, content_sha256, input_baseline,
                 policy_version, overall, json.dumps(hard_errors or []),
                 evidence_path, _now()))
            cur.execute(
                "UPDATE candidates SET review_state=?, status_revision=status_revision+1,"
                " updated_at=? WHERE candidate_id=?", (overall, _now(), candidate_id))
        return self.get_candidate(candidate_id)  # type: ignore[return-value]

    def latest_review(self, candidate_id: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM review_receipts WHERE candidate_id=?"
            " ORDER BY reviewed_at DESC LIMIT 1", (candidate_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["hard_errors"] = json.loads(d.pop("hard_errors_json"))
        return d

    # ---- 接纳（promotion） ----
    def begin_promotion(self, *, candidate_id: str, project_instance_id: str,
                        run_id: Optional[str], shot_id: str, version_id: str,
                        intent: Optional[dict] = None) -> dict:
        """接纳意图持久化（幂等）：存在未终态 promotion → 返回它（同一逻辑接纳
        继续）；已 accepted → 返回既有结果（调用方据此短路）；rejected → 允许
        重新发起（候选被保留供重新判断，B04 恢复选版后重试的合法路径）。"""
        with self._tx() as cur:
            row = cur.execute(
                "SELECT * FROM promotions WHERE candidate_id=?"
                " ORDER BY created_at DESC LIMIT 1", (candidate_id,)).fetchone()
            if row is not None and row["state"] != "rejected":
                pid = row["promotion_id"]
            else:
                pid = _uid("promo")
                cur.execute(
                    "INSERT INTO promotions (promotion_id, candidate_id,"
                    " project_instance_id, run_id, shot_id, version_id, state,"
                    " intent_json, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (pid, candidate_id, project_instance_id, run_id, shot_id, version_id,
                     "intent", json.dumps(intent or {}, ensure_ascii=False),
                     _now(), _now()))
        return self.get_promotion(pid)  # type: ignore[return-value]

    def get_promotion(self, promotion_id: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM promotions WHERE promotion_id=?", (promotion_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        intent_raw = d.pop("intent_json", None)
        result_raw = d.pop("result_json", None)
        d["intent"] = json.loads(intent_raw) if intent_raw else {}
        d["result"] = json.loads(result_raw) if result_raw else None
        return d

    def transition_promotion(self, promotion_id: str, *, to_state: str,
                             from_states: Optional[Iterable[str]] = None,
                             result: Optional[dict] = None) -> dict:
        froms = list(from_states) if from_states is not None else None
        with self._tx() as cur:
            row = cur.execute("SELECT * FROM promotions WHERE promotion_id=?",
                              (promotion_id,)).fetchone()
            if row is None:
                raise LedgerError(f"promotion not found: {promotion_id}")
            if froms is not None and row["state"] not in froms:
                raise LedgerError(
                    f"promotion {promotion_id} state {row['state']} not in {froms}")
            sets = ["state=?", "state_revision=state_revision+1", "updated_at=?"]
            args: List[Any] = [to_state, _now()]
            if result is not None:
                sets.append("result_json=?")
                args.append(json.dumps(result, ensure_ascii=False))
            args.append(promotion_id)
            cur.execute(f"UPDATE promotions SET {', '.join(sets)} WHERE promotion_id=?",
                        args)
        return self.get_promotion(promotion_id)  # type: ignore[return-value]

    def list_promotions(self, state: Optional[str] = None) -> List[dict]:
        if state:
            rows = self._conn().execute(
                "SELECT * FROM promotions WHERE state=? ORDER BY created_at",
                (state,)).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM promotions ORDER BY created_at").fetchall()
        out = []
        for row in rows:
            d = dict(row)
            intent_raw = d.pop("intent_json", None)
            result_raw = d.pop("result_json", None)
            d["intent"] = json.loads(intent_raw) if intent_raw else {}
            d["result"] = json.loads(result_raw) if result_raw else None
            out.append(d)
        return out

    # ---- 事件 ----
    def enqueue_event(self, *, kind: str, promotion_id: Optional[str] = None,
                      candidate_id: Optional[str] = None,
                      payload: Optional[dict] = None) -> str:
        eid = _uid("evt")
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO events (event_id, promotion_id, candidate_id, kind,"
                " payload_json, delivered, created_at) VALUES (?,?,?,?,?,0,?)",
                (eid, promotion_id, candidate_id, kind,
                 json.dumps(payload or {}, ensure_ascii=False), _now()))
        return eid

    def pending_events(self) -> List[dict]:
        rows = self._conn().execute(
            "SELECT * FROM events WHERE delivered=0 ORDER BY created_at").fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["payload"] = json.loads(d.pop("payload_json"))
            out.append(d)
        return out

    def mark_events_delivered(self, event_ids: List[str]) -> int:
        if not event_ids:
            return 0
        with self._tx() as cur:
            marks = ", ".join("?" for _ in event_ids)
            cur.execute(f"UPDATE events SET delivered=1 WHERE event_id IN ({marks})",
                        event_ids)
        return len(event_ids)
