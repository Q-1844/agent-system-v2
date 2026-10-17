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
#   - 租约文件=协调状态，不是 canonical；损坏按无租约处理（fail-open 到
#     turn_registry 同会话互斥的既有防线，不阻断生产）。
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Optional

DEFAULT_TTL_S = 1800   # 30min：覆盖单 turn 上限（窗口续期多次的情形由 renew 维持）


def _lease_path(pdir: Path) -> Path:
    return Path(pdir) / "output" / "agents" / "master_lease.json"


def _load(pdir: Path) -> Optional[dict]:
    p = _lease_path(pdir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save(pdir: Path, doc: dict) -> None:
    p = _lease_path(pdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def acquire(pdir: Path, session_id: str, *, ttl_s: int = DEFAULT_TTL_S,
            now: Optional[float] = None) -> dict:
    """获取/续期主控租约。返回 {ok, generation, holder, reason}。
    - 无租约/租约过期 → 接管（沿用原 generation+1？接管=新代次）；
    - 同 holder → 续期（generation 不变）；
    - 他人未过期 → 拒绝（ok=False，reason 带 holder/expires_at）。"""
    now = time.time() if now is None else now
    cur = _load(pdir)
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


def transfer(pdir: Path, from_session: str, to_session: str, *,
             ttl_s: int = DEFAULT_TTL_S, now: Optional[float] = None) -> dict:
    """接力移交：generation+1、holder 换新会话。仅当前 holder 可移交。"""
    now = time.time() if now is None else now
    cur = _load(pdir)
    if cur is None or cur.get("holder") != from_session:
        return {"ok": False, "reason": "当前会话不持有主控租约（移交失败，不静默）"}
    doc = {"holder": to_session, "generation": int(cur.get("generation") or 0) + 1,
           "acquired_at": now, "expires_at": now + ttl_s,
           "previous_holder": from_session}
    _save(pdir, doc)
    return {"ok": True, **doc}


def verify(pdir: Path, session_id: str, *, now: Optional[float] = None) -> dict:
    """执行点检查：session 是否为当前未过期 holder。损坏文件按无租约处理
    （fail-open，返回 verified=True + note）。"""
    now = time.time() if now is None else now
    cur = _load(pdir)
    if cur is None:
        return {"verified": True, "note": "无租约文件（fail-open；同会话互斥由 "
                                          "turn_registry 承担）"}
    if cur.get("holder") == session_id and (cur.get("expires_at") or 0) >= now:
        return {"verified": True, "generation": cur.get("generation")}
    return {"verified": False,
            "holder": cur.get("holder"), "generation": cur.get("generation"),
            "reason": "主控租约已转移/过期——本会话不再是主控写入者"}
