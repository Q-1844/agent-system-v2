# src/workflow/store.py
# 工作流持久化（PHASE46_01 §2「逻辑持久化至少覆盖定义/运行/事件」）。
# 布局（无数据库，全部 atomic_write_json / append jsonl，跨重启可读）：
#   projects/{p}/output/workflows/
#     definitions/{workflow_id}.json        # 已提交定义（服务端 revision 递增，冲突保护）
#     runs/{run_id}.json                    # run 完整状态快照（节点/attempt/审核/交接）
#     runs/{run_id}/events.jsonl            # 追加事件（单调 seq，断线观察恢复游标）
#     runs_index.json                       # run 轻索引（id→状态/时间），list 不解析全部 run
#
# 事务边界：run 状态变更 = 读改写单个 run JSON（原子替换）。事件 append 与 run 保存
# 不强求同事务——观察端以 run JSON 为准，事件仅辅助投影（§7.1 允许现状的等价实现：
# 状态先落盘，事件 best-effort 追加，重启对账以 run 为准）。
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from src.core.atomic import atomic_write_json
from . import models as M


def workflows_root(project_dir: Path) -> Path:
    return project_dir / "output" / "workflows"


def _definitions_dir(project_dir: Path) -> Path:
    return workflows_root(project_dir) / "definitions"


def _runs_dir(project_dir: Path) -> Path:
    return workflows_root(project_dir) / "runs"


def content_hash(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


# ============ 定义（草稿提交后的服务端权威副本）============
class DefinitionStore:
    """服务端定义存取。revision 从 1 起单调递增；PUT 带预期 revision，冲突报 409 语义。"""

    def __init__(self, project_dir: Path, project: str):
        self._dir = _definitions_dir(project_dir)
        self._project = project

    def _path(self, workflow_id: str) -> Path:
        return self._dir / f"{workflow_id}.json"

    def load(self, workflow_id: str) -> Optional[dict]:
        p = self._path(workflow_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_ids(self) -> List[str]:
        if not self._dir.is_dir():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json"))

    def save(self, workflow_id: str, definition: dict, expected_revision: Optional[int]) -> dict:
        """保存定义。expected_revision=None 表示首次创建（已存在则冲突）。
        返回保存后的定义（含新 revision/updated_at）。revision 冲突抛 RevisionConflict。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        cur = self.load(workflow_id)
        if expected_revision is None:
            if cur is not None:
                raise RevisionConflict(cur.get("revision"), "定义已存在（需带当前 revision 保存）")
            base = cur
            new_rev = 1
        else:
            if cur is None:
                raise RevisionConflict(None, "定义不存在，无法按预期 revision 更新")
            if int(expected_revision) != int(cur.get("revision") or 0):
                raise RevisionConflict(cur.get("revision"), "revision 冲突：他人已保存更新版本")
            base = cur
            new_rev = int(cur.get("revision") or 0) + 1
        doc = dict(definition or {})
        doc["schemaVersion"] = M.DEF_SCHEMA_VERSION
        doc["projectId"] = self._project
        doc["workflowId"] = workflow_id
        doc["revision"] = new_rev
        doc["updatedAt"] = _now_iso()
        if base and not doc.get("createdAt"):
            doc["createdAt"] = base.get("createdAt") or _now_iso()
        if not doc.get("createdAt"):
            doc["createdAt"] = _now_iso()
        doc["contentHash"] = content_hash({k: v for k, v in doc.items()
                                           if k not in ("contentHash", "revision", "updatedAt")})
        atomic_write_json(self._path(workflow_id), doc)
        return doc


class RevisionConflict(Exception):
    def __init__(self, current: Optional[int], message: str):
        self.current = current
        super().__init__(message)


# ============ Run ============
class RunStore:
    def __init__(self, project_dir: Path, project: str):
        self._dir = _runs_dir(project_dir)
        self._project = project

    def _path(self, run_id: str) -> Path:
        return self._dir / f"{run_id}.json"

    def load(self, run_id: str) -> Optional[dict]:
        p = self._path(run_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def create(self, run: dict) -> dict:
        self._dir.mkdir(parents=True, exist_ok=True)
        run = dict(run)
        run.setdefault("createdAt", _now_iso())
        run["updatedAt"] = _now_iso()
        atomic_write_json(self._path(run["runId"]), run)
        self._index_upsert(run)
        return run

    def save(self, run: dict) -> dict:
        run = dict(run)
        run["updatedAt"] = _now_iso()
        atomic_write_json(self._path(run["runId"]), run)
        self._index_upsert(run)
        return run

    def update(self, run_id: str, fn) -> Optional[dict]:
        """读-改-写（fn(run) 原地修改）。run 不存在返回 None。"""
        run = self.load(run_id)
        if run is None:
            return None
        fn(run)
        return self.save(run)

    def list(self, status: Optional[str] = None) -> List[dict]:
        """轻索引 list（不展开 run 全量）。"""
        idx = self._index()
        rows = list(idx.values())
        rows.sort(key=lambda r: r.get("createdAt") or "", reverse=True)
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return rows

    def active_run_ids(self) -> List[str]:
        idx = self._index()
        return [rid for rid, r in idx.items()
                if r.get("status") not in M.RUN_TERMINAL]

    # ---- 索引 ----
    def _index_path(self) -> Path:
        return self._dir / "runs_index.json"

    def _index(self) -> Dict[str, dict]:
        p = self._index_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _index_upsert(self, run: dict) -> None:
        p = self._index_path()
        idx = self._index()
        idx[run["runId"]] = {
            "runId": run["runId"], "workflowId": run.get("workflowId"),
            "status": run.get("status"),
            "createdAt": run.get("createdAt"), "updatedAt": run.get("updatedAt"),
            "name": (run.get("snapshot", {}).get("definition", {}) or {}).get("name"),
            "idempotencyKey": run.get("idempotencyKey"),
            "startParamsHash": run.get("startParamsHash"),
        }
        atomic_write_json(p, idx)


# ============ 事件 ============
class EventLog:
    """run 事件（append-only jsonl，单调 seq）。seq 从 1 起；断线观察按 after=seq 补齐。"""

    def __init__(self, project_dir: Path, run_id: str):
        self._dir = _runs_dir(project_dir) / run_id
        self._path = self._dir / "events.jsonl"

    def append(self, run_id: str, kind: str, *, node_id: Optional[str] = None,
               attempt_id: Optional[str] = None, **data) -> dict:
        self._dir.mkdir(parents=True, exist_ok=True)
        seq = self.next_seq()
        ev = {"seq": seq, "ts": _now_iso(), "runId": run_id, "kind": kind,
              "nodeId": node_id, "attemptId": attempt_id}
        ev.update(data)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return ev

    def next_seq(self) -> int:
        if not self._path.exists():
            return 1
        last = 0
        try:
            with self._path.open("rb") as f:
                for line in _tail_lines(f, 1):
                    try:
                        last = int(json.loads(line).get("seq") or 0)
                    except Exception:
                        pass
        except Exception:
            pass
        return last + 1

    def read(self, after: int = 0, limit: int = 500) -> List[dict]:
        if not self._path.exists():
            return []
        out: List[dict] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if int(ev.get("seq") or 0) > after:
                    out.append(ev)
                if len(out) >= limit:
                    break
        return out


def _tail_lines(f, n: int):
    """取文件最后 n 行（事件 seq 推导用；文件按行 append）。"""
    f.seek(0, 2)
    size = f.tell()
    if size == 0:
        return []
    block = 4096
    data = b""
    pos = size
    while pos > 0 and data.count(b"\n") <= n:
        step = min(block, pos)
        pos -= step
        f.seek(pos)
        data = f.read(step) + data
    lines = [ln for ln in data.splitlines() if ln.strip()]
    return lines[-n:]


def _now_iso() -> str:
    # Phase52 修复：原实现用 time.localtime() 却拼 "Z"（UTC 后缀），把本地时间误标成
    # UTC（+08:00 差 8 小时），与 timing_ledger 的真 UTC（+00:00）口径冲突，污染跨源
    # 时间线对账。改为真 UTC，保持 "Z" 后缀与既有数据形态一致。历史事件（修复前落盘）
    # 为 local-as-Z，报告里单独注明 8 小时偏移。
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + \
        f".{int(time.time()*1000)%1000:03d}Z"
