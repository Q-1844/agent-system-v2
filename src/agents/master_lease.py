# src/agents/master_lease.py
# Phase55 D（任务书 §7）：主控写入租约——可核验的会话代次机制。
#
# 解决：会话接力后旧会话晚返回再次主控写入（双主写入）。Phase54 人工
# session-split 期间此风险由「旧会话已死」的巧合规避——本模块把它变成机制。
#
# 语义：
#   - 一个项目至多一个主控 producer 会话（holder）+ 代次（generation，单调递增）；
#   - turn 开始 acquire（同 holder 续期；他人持有时：过期可接管，未过期拒绝——
#     诚实报错，不静默抢锁）；handoff 时 transfer 给新会话（generation+1）；
#   - 执行点检查（producer 被授权工具 + delegate 前校验 holder==当前会话）：
#     旧会话晚返回的写尝试在工具层被拒（读工具不拦——读无害）。
#   - 租约文件=协调状态，不是 canonical。Phase56 A3：损坏不再 fail-open 放行——
#     损坏租约=权限未核明，acquire/verify 一律拒绝新写入（fail-closed），
#     恢复读/查询走只读 diagnose()，重建须显式 recover（确认接管语义后重置）。
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Optional
from functools import wraps
from src.core.file_lock import file_transaction

DEFAULT_TTL_S = 1800   # 30min：覆盖单 turn 上限（窗口续期多次的情形由 renew 维持）

_CORRUPT = {"corrupt": True}   # 损坏标记（区别于「无租约」= None）


def _lease_path(pdir: Path) -> Path:
    return Path(pdir) / "output" / "agents" / "master_lease.json"


def _serialized(fn):
    @wraps(fn)
    def locked(pdir, *args, **kwargs):
        with file_transaction(_lease_path(pdir).with_suffix(".lock")):
            return fn(pdir, *args, **kwargs)
    return locked


def _load(pdir: Path) -> Optional[dict]:
    p = _lease_path(pdir)
    if not p.exists():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if (not isinstance(doc, dict) or not isinstance(doc.get("holder"), str)
                or not doc["holder"] or type(doc.get("generation")) is not int
                or doc["generation"] < 1
                or not isinstance(doc.get("expires_at"), (int, float))):
            return dict(_CORRUPT)
        return doc
    except Exception:
        # Phase56 A3：损坏≠无租约。返回损坏标记——调用方按「权限未核明」拒写，
        # 不 fail-open（否则损坏时任何会话都能接管=双主写入敞口）。
        return dict(_CORRUPT)


def _save(pdir: Path, doc: dict) -> None:
    p = _lease_path(pdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


@_serialized
def acquire(pdir: Path, session_id: str, *, ttl_s: int = DEFAULT_TTL_S,
            now: Optional[float] = None) -> dict:
    """获取/续期主控租约。返回 {ok, generation, holder, reason}。
    - 无租约/租约过期 → 接管（接管=新代次）；
    - 同 holder → 续期（generation 不变）；
    - 他人未过期 → 拒绝（ok=False，reason 带 holder/expires_at）；
    - 租约损坏（Phase56 A3）→ 拒绝（权限未核明不接管——恢复/重建走 diagnose/
      recover 独立路径，防损坏期间双主推进）。"""
    now = time.time() if now is None else now
    cur = _load(pdir)
    if cur is not None and cur.get("corrupt"):
        return {"ok": False, "corrupt": True,
                "reason": ("主控租约文件损坏——权限未核明，拒绝新的主控写入与接管；"
                           "读/查询可用 master_lease.diagnose()，确认无活动主控后"
                           "用 recover() 显式重建")}
    if cur is None or (cur.get("expires_at") or 0) < now:
        gen = int((cur or {}).get("generation") or 0) + 1
        doc = {"holder": session_id, "generation": gen,
               "acquired_at": now, "expires_at": now + ttl_s}
        _save(pdir, doc)
        return {"ok": True, **doc,
                "reason": ("接管（原租约已过期）" if cur else "首次获取")}
    if cur.get("holder") == session_id:
        cur["expires_at"] = now + ttl_s
        _save(pdir, cur)
        return {"ok": True, **cur, "reason": "续期"}
    return {"ok": False, "generation": cur.get("generation"),
            "holder": cur.get("holder"), "expires_at": cur.get("expires_at"),
            "reason": "另一会话持有主控租约（未过期）——先 relay 或等待过期"}


@_serialized
def transfer(pdir: Path, from_session: str, to_session: str, *,
             ttl_s: int = DEFAULT_TTL_S, now: Optional[float] = None) -> dict:
    """接力移交：generation+1、holder 换新会话。仅当前 holder 可移交。
    损坏租约不可移交（Phase56 A3：权限未核明——走 recover 重建后重新接力）。"""
    now = time.time() if now is None else now
    cur = _load(pdir)
    if cur is not None and cur.get("corrupt"):
        return {"ok": False, "corrupt": True,
                "reason": "主控租约文件损坏——移交被拒绝（recover() 重建后再接力）"}
    if cur is None or cur.get("holder") != from_session:
        return {"ok": False, "reason": "当前会话不持有主控租约（移交失败，不静默）"}
    doc = {"holder": to_session, "generation": int(cur.get("generation") or 0) + 1,
           "acquired_at": now, "expires_at": now + ttl_s,
           "previous_holder": from_session}
    _save(pdir, doc)
    return {"ok": True, **doc}


@_serialized
def verify(pdir: Path, session_id: str, *, now: Optional[float] = None,
           expected_generation: Optional[int] = None) -> dict:
    """执行点检查：session 是否仍为主控写入者。
    语义（Phase55 冒烟实证修正）：holder 匹配即通过——【自身租约过期≠失去主控】
    （长 turn 超过 TTL 是常态；过期只意味着他人可以接管，而接管会改变 holder/
    推 generation，届时本会话自然验不过）。过期只 gate 他人 acquire。
    损坏文件（Phase56 A3）：fail-closed——无法核明 holder 时拒绝一切新写入
    （verified=False），恢复走 diagnose()/recover()。"""
    now = time.time() if now is None else now
    cur = _load(pdir)
    if cur is None:
        return {"verified": False, "reason": "主控租约缺失；必须重新获取后才能写入"}
    if cur.get("corrupt"):
        return {"verified": False, "corrupt": True,
                "reason": ("主控租约文件损坏——holder 无法核明，拒绝新写入"
                           "（fail-closed；用 diagnose() 查看、确认后 recover() 重建）")}
    if expected_generation is not None and cur.get("generation") != expected_generation:
        return {"verified": False, "reason": "主控租约代次已变化", "generation": cur.get("generation")}
    if cur.get("holder") == session_id:
        return {"verified": True, "generation": cur.get("generation"),
                **({"note": "租约已过期但无接管者——holder 连续性保持"}
                   if (cur.get("expires_at") or 0) < now else {})}
    return {"verified": False,
            "holder": cur.get("holder"), "generation": cur.get("generation"),
            "reason": (f"主控租约已被会话 {cur.get('holder')} 持有"
                       f"（generation={cur.get('generation')}）——本会话不再是"
                       "主控写入者（经 relay 移交或被接管）")}


def diagnose(pdir: Path) -> dict:
    """只读诊断（恢复读/查询独立路径——不写、不接管、不修改任何状态）。"""
    p = _lease_path(pdir)
    cur = _load(pdir)
    if cur is None:
        return {"state": "absent", "path": str(p)}
    if cur.get("corrupt"):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:   # noqa: BLE001
            raw = f"<unreadable: {e}>"
        return {"state": "corrupt", "path": str(p),
                "raw_excerpt": raw[:400],
                "note": "损坏租约——写路径已 fail-closed；确认无活动主控后 recover() 重建"}
    return {"state": "ok", "path": str(p),
            "holder": cur.get("holder"), "generation": cur.get("generation"),
            "acquired_at": cur.get("acquired_at"), "expires_at": cur.get("expires_at"),
            "expired": (cur.get("expires_at") or 0) < time.time()}


@_serialized
def recover(pdir: Path, session_id: str, *, ttl_s: int = DEFAULT_TTL_S,
            now: Optional[float] = None) -> dict:
    """显式重建（损坏租约的唯一恢复路径）：generation 从损坏前最后已知值之上
    续推不可考——按「新代次」重建（generation+1 语义无从谈起，取 1 重新起算，
    并记录 rebuilt_from=corrupt 审计标记）。任何会话都可调用：损坏期间没有
    合法 holder 在执行（写路径已被 verify fail-closed 拦截），不存在双主。
    仅当租约确实损坏时允许；健康租约不走此路径（防绕过正常接管语义）。"""
    now = time.time() if now is None else now
    cur = _load(pdir)
    if not (cur is not None and cur.get("corrupt")):
        return {"ok": False,
                "reason": ("租约未损坏（state=" + diagnose(pdir)["state"] + "）——"
                           "重建仅用于损坏恢复，正常接管请走 acquire/transfer")}
    doc = {"holder": session_id, "generation": 1,
           "acquired_at": now, "expires_at": now + ttl_s,
           "rebuilt_from": "corrupt"}
    _save(pdir, doc)
    return {"ok": True, **doc, "reason": "损坏租约已显式重建（审计标记 rebuilt_from=corrupt）"}
