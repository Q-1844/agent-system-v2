# src/agents/subagent/registry.py
# SceneAgentRegistry（Part 2 §7 落盘形态）+ Main Director 全局决策记忆（§8，Errata E3）。
#
# 落盘布局（全部 atomic_write_json + 顶层 version 字段）：
#   output/agents/scene_agents/
#     registry.json               索引：per-scene status / 计数 / 惰性 spawn/retire
#     memory/{scene_id}.json      MemoryEntry（retire 后随身份归档，不删）
#     delegations/{delegation_id}.json   生命周期状态机 + commit journal
#     proposals/{proposal_id}.json       proposal store（含 superseded 标记）
#     global_memory.json          MD 全局决策条目（scene_id=null）
#
# 硬约束：
#   - 一切跨 delegation 持久物必须带可检测前提（premises 指纹）；rejected/stale
#     永不入库（Errata E2：入库时点 = commit 成功后，从实际 commit 的有效端态提取）；
#   - 生命周期检测全部惰性 + 确定性（派发/commit/turn 收尾 sweep 三节点；
#     无守护进程、SW 零感知）；
#   - volatile 依赖（border/adjacency，Errata E4）不落 memory，只留 proposal store。
#
# 建议阈值（Part 2 §11，标注依据不拍死）：
#   MEMORY_INJECTION_CAP K=12 / PER_SHOT_RETENTION M=3 / SCENE_NOTES_RETENTION=4 /
#   GLOBAL_MEMORY_CAP K_g=8 / SPAWN_PRESSURE_THRESHOLD=6 / HARD_DELEGATION_CAP=6。

from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.core.atomic import atomic_write_json
from src.core.models import Scene
from src.core.store import ProjectStore

from . import fingerprints as fp

log = logging.getLogger("agents.subagent.registry")

REGISTRY_VERSION = 1
MEMORY_VERSION = 1
DELEGATION_VERSION = 1
PROPOSAL_VERSION = 1
GLOBAL_MEMORY_VERSION = 1

# ---- 阈值（Part 2 §11 建议值）----
MEMORY_INJECTION_CAP = 12        # K：单次注入条目上限
PER_SHOT_RETENTION = 3           # M：同镜保留最新 3 条 active
SCENE_NOTES_RETENTION = 4        # 段级 notes 保留条数
GLOBAL_MEMORY_CAP = 8            # K_g：全局决策注入上限
SPAWN_PRESSURE_THRESHOLD = 6     # 段级结构写 ≥6 → pressure 信号成立
HARD_DELEGATION_CAP = 6          # 全来源累计 delegation 硬上限
STRUCTURAL_WRITE_OPS = {"shot.create", "shot.create_many",
                        "shot.delete", "shot.move"}

# 进程内存活 delegation 句柄（单段单飞检查；单进程语义：重启即无存活实例）
_ACTIVE_DELEGATIONS: Dict[str, str] = {}
# 进程内 turn 预算（本 turn MD 发起的 delegation <3；director_turn 边界重置）
_TURN_DELEGATION_COUNT = 0


def new_delegation_id() -> str:
    return "dlg_" + secrets.token_hex(6)


def new_proposal_id() -> str:
    return "prop_" + secrets.token_hex(6)


def new_entry_id() -> str:
    return "ment_" + secrets.token_hex(6)


def active_handle(scene_id: str) -> Optional[str]:
    """该段当前在飞的 delegation_id（无则 None）。"""
    return _ACTIVE_DELEGATIONS.get(scene_id)


def acquire_handle(scene_id: str, delegation_id: str) -> bool:
    """单段单飞登记（已在飞 → False）。"""
    if scene_id in _ACTIVE_DELEGATIONS:
        return False
    _ACTIVE_DELEGATIONS[scene_id] = delegation_id
    return True


def release_handle(scene_id: str, delegation_id: str) -> None:
    if _ACTIVE_DELEGATIONS.get(scene_id) == delegation_id:
        del _ACTIVE_DELEGATIONS[scene_id]


def reset_turn_budget() -> None:
    global _TURN_DELEGATION_COUNT
    _TURN_DELEGATION_COUNT = 0


def bump_turn_budget() -> int:
    global _TURN_DELEGATION_COUNT
    _TURN_DELEGATION_COUNT += 1
    return _TURN_DELEGATION_COUNT


def turn_budget_used() -> int:
    return _TURN_DELEGATION_COUNT


# ============ MemoryEntry 构造（Errata E2/E4/E6）============
def make_memory_entries(scene_id: Optional[str], delegation_id: str,
                        proposal_id: str, basis_fp: str,
                        effective_end_state: List[dict],
                        design_notes: str,
                        scene: Scene, created_at: Optional[float] = None) -> List[dict]:
    """从【实际 commit 的有效端态】提取 decision memory 条目（入库规则 E2）。

    条目两类：per-shot rationale（premises = 该镜当前 token + 段内容 token +
    声明的 character 依赖）与段级 design_notes（premises = 全段）。
    volatile 判定（E4）：依赖声明含 border/adjacency → 该条目 volatile=True，
    不落 memory 文件（调用侧只留 proposal store）。"""
    from src.agents.director import _shot_token
    ts = created_at or time.time()
    entries: List[dict] = []

    def _premises_shots(shot_ids: List[str]) -> List[dict]:
        out = []
        for sid in shot_ids:
            sh = scene and next((x for x in (scene.shots or [])
                                 if x.shot_id == sid), None)
            if sh is not None:
                out.append({"shot_id": sid, "token": _shot_token(sh)})
        return out

    def _chars_of(deps) -> List[str]:
        return sorted({d.get("ref") for d in (deps or [])
                       if isinstance(d, dict)
                       and d.get("kind") == "character_canonical" and d.get("ref")})

    def _volatile_of(deps) -> bool:
        return any(isinstance(d, dict) and d.get("kind") in ("border", "adjacency")
                   for d in (deps or []))

    for it in effective_end_state:
        rationale = str(it.get("rationale") or "").strip()
        if not rationale:
            continue
        sid = it.get("shot_id")
        if not sid:
            continue   # 新建镜的 id 已回填；防御
        entries.append({
            "entry_id": new_entry_id(),
            "scene_id": scene_id,
            "text": f"[{sid}] {rationale}",
            "provenance": {"delegation_id": delegation_id,
                           "proposal_id": proposal_id,
                           "basis_fingerprint": basis_fp},
            "premises": {
                "shots": _premises_shots([sid]),
                "scene_content_token": fp.scene_content_token(scene),
                "characters": _chars_of(it.get("dependencies")),
            },
            "volatile": _volatile_of(it.get("dependencies")),
            "status": "active",
            "created_at": ts,
        })
    notes = str(design_notes or "").strip()
    if notes:
        entries.append({
            "entry_id": new_entry_id(),
            "scene_id": scene_id,
            "text": f"[段级设计说明] {notes}",
            "provenance": {"delegation_id": delegation_id,
                           "proposal_id": proposal_id,
                           "basis_fingerprint": basis_fp},
            "premises": {
                "shots": _premises_shots([sh.shot_id for sh in (scene.shots or [])]),
                "scene_content_token": fp.scene_content_token(scene),
                "characters": [],
            },
            "volatile": False,   # 段级 notes 的 volatile 由上层按 proposal 依赖判定
            "status": "active",
            "created_at": ts,
        })
    return entries


# ============ 存储主类 ============
class SubagentStore:
    """项目级 subagent 持久物统一入口（canonical 永远是真源；本存储是缓存 +
    记忆载体，任何不一致以 sweep 对账 canonical 收场）。"""

    def __init__(self, project_dir, project_name: str):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self.root = self.project_dir / "output" / "agents" / "scene_agents"

    # ---------- registry.json ----------
    def _registry_path(self) -> Path:
        return self.root / "registry.json"

    def _load_registry(self) -> dict:
        p = self._registry_path()
        if not p.exists():
            return {"version": REGISTRY_VERSION, "scenes": {},
                    "total_delegations": 0}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "scenes" not in data:
                return {"version": REGISTRY_VERSION, "scenes": {},
                        "total_delegations": 0}
            data.setdefault("version", REGISTRY_VERSION)
            data.setdefault("total_delegations", 0)
            return data
        except Exception:
            return {"version": REGISTRY_VERSION, "scenes": {},
                    "total_delegations": 0}

    def _save_registry(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._registry_path(), data)

    def ensure_scene(self, scene_id: str) -> dict:
        """惰性 spawn：首次 delegation 时创建身份记录（不预建）。"""
        data = self._load_registry()
        rec = data["scenes"].get(scene_id)
        if rec is None:
            rec = {"status": "active", "first_delegation_at": time.time(),
                   "last_delegation_at": time.time(), "delegation_count": 0,
                   "accepted_proposals": 0,
                   "structural_since_delegation": 0,
                   "active_memory_entries": 0, "quarantined_count": 0}
            data["scenes"][scene_id] = rec
            self._save_registry(data)
        return rec

    def scene_record(self, scene_id: str) -> Optional[dict]:
        return self._load_registry()["scenes"].get(scene_id)

    def retire_scene(self, scene_id: str) -> bool:
        """retire（identity 归档不删；scene_id 按 canonical 分配规则不复用，
        无身份碰撞）。返回是否发生了状态迁移。"""
        data = self._load_registry()
        rec = data["scenes"].get(scene_id)
        if rec is None or rec.get("status") == "retired":
            return False
        rec["status"] = "retired"
        rec["retired_at"] = time.time()
        self._save_registry(data)
        return True

    def update_scene_record(self, scene_id: str, **fields) -> None:
        data = self._load_registry()
        rec = data["scenes"].get(scene_id)
        if rec is None:
            return
        rec.update(fields)
        self._save_registry(data)

    def bump_delegation_count(self, scene_id: str) -> int:
        data = self._load_registry()
        rec = data["scenes"].get(scene_id)
        if rec is None:
            return 0
        rec["delegation_count"] = int(rec.get("delegation_count", 0)) + 1
        rec["last_delegation_at"] = time.time()
        data["total_delegations"] = int(data.get("total_delegations", 0)) + 1
        rec["structural_since_delegation"] = 0   # 新 delegation 起算
        self._save_registry(data)
        return data["total_delegations"]

    def add_structural_writes(self, receipts: Sequence[dict]) -> None:
        """按回执 scene_id 聚合累计 structural_since_delegation（turn 收尾 /
        commit 后调用；只计成功非幂等结构写）。只在确实更新了已有 scene 记录时
        落盘——无 subagent 活动（从未 spawn）的项目零痕迹（不创建目录）。"""
        per_scene: Dict[str, int] = {}
        for r in receipts or []:
            if not (r.get("ok") and r.get("verified") and not r.get("unchanged")):
                continue
            if r.get("operation") not in STRUCTURAL_WRITE_OPS:
                continue
            sid = r.get("scene_id")
            if sid:
                per_scene[sid] = per_scene.get(sid, 0) + 1
        if not per_scene:
            return
        data = self._load_registry()
        updated = False
        for sid, n in per_scene.items():
            rec = data["scenes"].get(sid)
            if rec is not None:
                rec["structural_since_delegation"] = \
                    int(rec.get("structural_since_delegation", 0)) + n
                updated = True
        if updated:
            self._save_registry(data)

    def pressure_ready(self, scene_id: str) -> bool:
        rec = self.scene_record(scene_id)
        return bool(rec and int(rec.get("structural_since_delegation", 0))
                    >= SPAWN_PRESSURE_THRESHOLD)

    # ---------- memory/{scene_id}.json ----------
    def _memory_path(self, scene_id: str) -> Path:
        return self.root / "memory" / f"{scene_id}.json"

    def load_memory(self, scene_id: str) -> List[dict]:
        p = self._memory_path(scene_id)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            entries = data.get("entries") if isinstance(data, dict) else None
            return [e for e in (entries or []) if isinstance(e, dict)]
        except Exception:
            return []

    def _save_memory(self, scene_id: str, entries: List[dict]) -> None:
        d = self.root / "memory"
        d.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._memory_path(scene_id),
                          {"version": MEMORY_VERSION, "entries": entries})

    def append_memory(self, scene_id: str, entries: List[dict]) -> None:
        """入库（E2：仅 commit 成功后调用；volatile 条目由调用侧过滤）。"""
        durable = [e for e in entries if not e.get("volatile")]
        if not durable:
            return
        existing = self.load_memory(scene_id)
        self._save_memory(scene_id, existing + durable)
        self.update_scene_record(
            scene_id, active_memory_entries=len(existing) + len(durable))

    def sweep_memory(self, scene: Scene) -> dict:
        """惰性前提清扫（派发节点）：per-shot token + scene_content_token 比对，
        不匹配 → quarantined（保留供审计，注入头明示计数）。幂等。"""
        from src.agents.director import _shot_token
        cur_tokens = {sh.shot_id: _shot_token(sh) for sh in (scene.shots or [])}
        cur_content = fp.scene_content_token(scene)
        entries = self.load_memory(scene.scene_id)
        n_quarantined = 0
        for e in entries:
            if e.get("status") != "active":
                continue
            prem = e.get("premises") or {}
            bad = (prem.get("scene_content_token") != cur_content
                   or any(t.get("token") != cur_tokens.get(t.get("shot_id"))
                          for t in (prem.get("shots") or [])))
            if bad:
                e["status"] = "quarantined"
                e["quarantined_at"] = time.time()
                n_quarantined += 1
        if n_quarantined:
            self._save_memory(scene.scene_id, entries)
            rec = self.scene_record(scene.scene_id) or {}
            self.update_scene_record(
                scene.scene_id,
                active_memory_entries=sum(1 for e in entries
                                          if e.get("status") == "active"),
                quarantined_count=int(rec.get("quarantined_count", 0)) + n_quarantined)
        active = [e for e in entries if e.get("status") == "active"]
        return {"active": active, "quarantined_count": n_quarantined,
                "total": len(entries)}

    def render_memory_for_injection(self, scene: Scene) -> tuple:
        """注入选择（Part 2 §7.1）：K=12 总量 / per-shot M=3 / 段级 notes 4。
        返回 (渲染文本, active_count, quarantined_total)。"""
        swept = self.sweep_memory(scene)
        active = swept["active"]
        # 分类：per-shot（text 以 [shot_id] 开头）与段级
        per_shot: Dict[str, List[dict]] = {}
        notes: List[dict] = []
        for e in active:
            text = str(e.get("text") or "")
            sid = text.startswith("[") and text[1:text.find("]")]
            if isinstance(sid, str) and sid and f"[{sid}]" in text and "_c" in sid:
                per_shot.setdefault(sid, []).append(e)
            else:
                notes.append(e)
        selected: List[dict] = []
        for sid, lst in per_shot.items():
            selected.extend(lst[-PER_SHOT_RETENTION:])   # 最新 M 条
        selected.extend(notes[-SCENE_NOTES_RETENTION:])  # 段级最新 4 条
        selected.sort(key=lambda e: e.get("created_at") or 0)
        selected = selected[-MEMORY_INJECTION_CAP:]      # 总量 K
        if not selected:
            return "", 0, swept["quarantined_count"]
        lines = []
        for e in selected:
            prov = e.get("provenance") or {}
            lines.append(f"- {e.get('text')}（出处：{prov.get('delegation_id')}"
                         f" / 前提指纹 {prov.get('basis_fingerprint')}）")
        return "\n".join(lines), len(selected), swept["quarantined_count"]

    # ---------- delegations/{delegation_id}.json ----------
    def _delegation_path(self, delegation_id: str) -> Path:
        return self.root / "delegations" / f"{delegation_id}.json"

    def save_delegation(self, record: dict) -> None:
        d = self.root / "delegations"
        d.mkdir(parents=True, exist_ok=True)
        record.setdefault("version", DELEGATION_VERSION)
        record["updated_at"] = time.time()
        atomic_write_json(self._delegation_path(record["delegation_id"]), record)

    def load_delegation(self, delegation_id: str) -> Optional[dict]:
        p = self._delegation_path(delegation_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_delegations(self) -> List[dict]:
        d = self.root / "delegations"
        if not d.exists():
            return []
        out = []
        for f in sorted(d.glob("dlg_*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out

    def sweep(self, canonical_scene_ids: Sequence[str]) -> dict:
        """turn 收尾惰性检测节点③（确定性，零 LLM）：
        ① registry scenes − canonical → retire（SW story.delete 的检测点）；
        ② 孤儿 delegation 清算：dispatched 且无进程内存活句柄且无 proposal
          → aborted（单进程语义：重启后无任何实例存活）。"""
        cano = set(canonical_scene_ids)
        data = self._load_registry()
        retired = []
        for sid, rec in data["scenes"].items():
            if sid not in cano and rec.get("status") == "active":
                rec["status"] = "retired"
                rec["retired_at"] = time.time()
                retired.append(sid)
        if retired:
            self._save_registry(data)
        aborted = []
        for d in self.list_delegations():
            if (d.get("status") == "dispatched"
                    and active_handle(d.get("scene_id")) != d.get("delegation_id")
                    and not d.get("proposal_id")):
                d["status"] = "aborted"
                d["aborted_reason"] = "orphan_sweep"
                self.save_delegation(d)
                aborted.append(d["delegation_id"])
        return {"retired": retired, "aborted": aborted}

    # ---------- proposals/{proposal_id}.json ----------
    def _proposal_path(self, proposal_id: str) -> Path:
        return self.root / "proposals" / f"{proposal_id}.json"

    def save_proposal(self, record: dict) -> None:
        d = self.root / "proposals"
        d.mkdir(parents=True, exist_ok=True)
        record.setdefault("version", PROPOSAL_VERSION)
        record["saved_at"] = time.time()
        atomic_write_json(self._proposal_path(record["proposal_id"]), record)

    def load_proposal(self, proposal_id: str) -> Optional[dict]:
        p = self._proposal_path(proposal_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ---------- global_memory.json（MD 全局决策记忆，Errata E3）----------
    def _global_memory_path(self) -> Path:
        return self.root / "global_memory.json"

    def load_global_memory(self) -> List[dict]:
        p = self._global_memory_path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return [e for e in (data.get("entries") or [])
                    if isinstance(e, dict)]
        except Exception:
            return []

    def append_global_memory(self, entry: dict) -> None:
        """全局决策条目（来源锚定 reconcile 工件：decision_rationale / bcr 裁决；
        刻意不做自由格式工具——防 F2 复制）。premises 可空 = standing decision，
        注入上限 K_g=8 兜底增长。"""
        entries = self.load_global_memory()
        entries.append({"entry_id": new_entry_id(), "scene_id": None,
                        "created_at": time.time(), **entry})
        # 上限滚动（最旧淘汰；schema version 保留）
        entries = entries[-(GLOBAL_MEMORY_CAP * 4):]   # 存储上限放宽（注入再截 K_g）
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._global_memory_path(),
                          {"version": GLOBAL_MEMORY_VERSION, "entries": entries})

    def render_global_memory_for_injection(self) -> str:
        """reground 携带物扩展（§8.2）：非空才返回文本段（空 = seed 逐字节一致）。"""
        entries = self.load_global_memory()
        if not entries:
            return ""
        picked = entries[-GLOBAL_MEMORY_CAP:]
        lines = []
        for e in picked:
            prov = e.get("provenance") or {}
            suffix = (f"（出处：{prov.get('proposal_id')}）"
                      if prov.get("proposal_id") else "")
            lines.append(f"- {e.get('text')}{suffix}")
        return ("# 全局导演决策（advisory，带出处；事实以状态头为准）\n"
                + "\n".join(lines))


def make_global_entry(text: str, *, proposal_id: Optional[str] = None,
                      kind: str = "reconcile_decision") -> dict:
    """全局决策条目构造（§8.1：锚定真实决策工件）。"""
    return {"text": text, "kind": kind,
            "provenance": {"proposal_id": proposal_id,
                           "created_via": "proposal.commit/reject"}}
