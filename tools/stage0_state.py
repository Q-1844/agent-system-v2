# tools/stage0_state.py
"""Stage 0 身份与蓝图在 B1 仓的 CAS 记录（PR B1，方案 A：单文件原子内嵌）。

文件：e2e/<nonce>/producer-stage0.json（与 full-project-production.json 同分支
test/e2e-<nonce>，经 task_index(filename=...) 复用 GitHubContentsIndex 的
read/write_cas——单 blob 乐观并发 = 写入即原子，蓝图与身份不分离存储，
从机制上排除"检查点已绑定、蓝图不存在/被篡改"的中间态。

身份语义（P0-2 三元组，恢复语义的唯一权威）：
- task_spec_sha256：任务身份（bootstrap 时钉死；同 nonce 换任务 → 拒绝）
- plan_fingerprint：蓝图身份（freeze 时写入；缺失 = 未冻结，允许重规划）
- 执行 digest（request_sha256）：物化后绑定 ProductionCheckpoint（tools 层）

崩溃窗口（GPT 契约 §4.3）：
- 冻结前崩溃：stage0 无 plan_fingerprint → 重新规划（text_calls_used 不回退）
- 冻结后、checkpoint 绑定前崩溃：恢复原蓝图重物化，不再调 Script Writer
- 绑定后崩溃：ProductionCheckpoint 域（既有机制）
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

SCHEMA_VERSION = 2
STAGE0_FILENAME = "producer-stage0.json"
MODE = "auto"

STAGE_CREATED = "CREATED"
STAGE_PLANNING = "PLANNING"
STAGE_FROZEN = "FROZEN"


class Stage0Conflict(RuntimeError):
    """同 nonce 的任务身份或模式与 B1 权威记录不一致（fail closed）。"""


class Stage0Corrupt(RuntimeError):
    """记录存在但自校验失败（蓝图指纹与内容不符等）——拒绝使用，要求人工对账。"""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))


class Stage0Record:
    """producer-stage0.json 的 CAS 封装。index 由 task_index(...) 构造。"""

    def __init__(self, index, nonce: str):
        self.index = index
        self.nonce = nonce
        self.current = index.read()
        self.doc: Dict[str, Any] = {} if self.current is None else self.current["doc"]

    # ---------- 读 ----------

    def exists(self) -> bool:
        return self.current is not None

    @property
    def mode(self) -> Optional[str]:
        return self.doc.get("mode")

    @property
    def plan_fingerprint(self) -> Optional[str]:
        return self.doc.get("plan_fingerprint")

    @property
    def blueprint(self) -> Optional[Dict[str, Any]]:
        return self.doc.get("blueprint")

    @property
    def text_calls_used(self) -> int:
        return int(self.doc.get("text_calls_used") or 0)

    def verify(self) -> None:
        """自校验（恢复路径必过）：结构 + 蓝图指纹与内容一致（T28 fail-closed）。"""
        d = self.doc
        if not isinstance(d, dict) or d.get("schema_version") != SCHEMA_VERSION \
                or d.get("nonce") != self.nonce or d.get("mode") != MODE:
            raise Stage0Corrupt(f"stage0 记录结构非法: nonce={self.nonce}")
        if d.get("status") == STAGE_FROZEN:
            fp = d.get("plan_fingerprint")
            bp = d.get("blueprint")
            if not isinstance(fp, str) or not isinstance(bp, dict):
                raise Stage0Corrupt("FROZEN 记录缺少蓝图或指纹")
            if hashlib.sha256(_canonical(bp).encode()).hexdigest() != fp:
                raise Stage0Corrupt("蓝图内容与 plan_fingerprint 不符（可能被篡改/损坏）")

    # ---------- 写（全部经 write_cas，单文件原子） ----------

    def _save(self, **fields) -> None:
        candidate = dict(self.doc, **fields, schema_version=SCHEMA_VERSION,
                         nonce=self.nonce)
        result = self.index.write_cas(
            candidate, None if self.current is None else self.current["blob_sha"],
            message=f"stage0 {self.nonce}: {candidate.get('status')}")
        if not result.get("ok") or not result.get("new_sha"):
            raise Stage0Conflict("stage0 CAS 写入未确认（并发或冲突）")
        self.doc = candidate
        self.current = {"doc": candidate, "blob_sha": result["new_sha"]}

    def bootstrap(self, task_spec) -> None:
        """登记任务身份。已存在且身份不符 → 拒绝（T25）。"""
        digest = task_spec.sha256()
        if self.current is not None:
            self.verify()
            if self.doc.get("task_spec_sha256") != digest:
                raise Stage0Conflict(
                    f"nonce 已绑定不同任务（B1: {self.doc.get('task_spec_sha256', '')[:12]}"
                    f"… ≠ 本次 {digest[:12]}…）；拒绝覆盖，需人工对账或新 nonce")
            return
        self._save(status=STAGE_CREATED, mode=MODE,
                   task_spec_sha256=digest,
                   task_spec=task_spec.to_dict(),
                   text_calls_used=0)

    def mark_planning(self) -> None:
        if self.doc.get("status") == STAGE_CREATED:
            self._save(status=STAGE_PLANNING)

    def reserve_text_call(self) -> None:
        """P0-4：网络请求前 CAS 预留一格文本额度（只增不减）。"""
        self._save(text_calls_used=self.text_calls_used + 1)

    def reserve(self) -> None:
        """Stage0Ledger 接口（src.ai.producer_planning_service）——同 reserve_text_call。"""
        self.reserve_text_call()

    def freeze(self, blueprint: Dict[str, Any]) -> str:
        """冻结蓝图（单次原子写：内容+指纹一起落账）。返回 plan_fingerprint。

        已冻结 → 拒绝重规划（恢复语义由调用方按"必须恢复原蓝图"执行）。
        """
        if self.plan_fingerprint is not None:
            raise Stage0Conflict(
                "蓝图已冻结；如需变更内容请走人工对账，不得自动重规划")
        fingerprint = hashlib.sha256(_canonical(blueprint).encode()).hexdigest()
        self._save(status=STAGE_FROZEN, blueprint=blueprint,
                   plan_fingerprint=fingerprint)
        return fingerprint
