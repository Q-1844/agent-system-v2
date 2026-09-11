# src/resources/recovery.py
# Production Resources V1 — Phase 1C1.2 Generation Recovery（startup orphan sweep）。
#
# 两层恢复设计：
#   1. cancel cleanup（write_service 内联）：任务被取消/进程关闭时尽力写终态
#      （failed/cancelled + 诚实 record），然后 re-raise——永不吞 CancelledError。
#   2. startup orphan sweep（本模块）：进程死亡（kill/断电/崩溃）留下的孤儿
#      `generation_status="generating"` 在 api_server 启动时清理。
#
# 🔴 判定无歧义的依据：startup 时刻进程内**必然没有 in-flight generation**
#    （JobQueue/请求尚未开始处理）——凡 generating 必为上次进程遗留。
# 🔴 一次性确定性扫描：无 timeout daemon、无 background worker、无轮询。
# 🔴 schema 损坏 fail closed：ResourceStore.load() 抛 ResourceSchemaError 时不静默重置，
#    上抛由调用方决定（api_server startup 捕获记 warning，不阻塞启动）。
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import generation_records
from .models import GenerationStatus
from .store import ResourceStore

ORPHAN_ERROR = "orphaned_by_process_restart"


async def recover_orphaned_generations(project_dir, project_name: Optional[str] = None) -> dict:
    """单项目孤儿清理：generating → failed + 诚实 record（无 generation 身份可考，
    用合成 gen_orphan_* id 标记，basis=None 不臆造）。幂等：无孤儿时零写入。
    Phase 1C5 扩展：composition.generating 孤儿进入同一恢复体系（§10），
    record 以 composition_id 字段区分（look 字段缺席）。"""
    pdir = Path(project_dir)
    pname = project_name or pdir.name
    store = ResourceStore(pdir, pname)
    receipt: Dict[str, Any] = {
        "ok": True, "operation": "resources.recover_orphaned_generations",
        "project": pname, "scanned": 0, "recovered": [], "error": None,
    }
    if not store.exists():
        return receipt
    async with store.write_lock():
        doc = store.load()   # 损坏 → ResourceSchemaError 上抛（不静默重置）
        receipt["scanned"] = len(doc.looks) + len(doc.compositions)
        look_orphans = [lk for lk in doc.looks.values()
                        if lk.generation_status == GenerationStatus.GENERATING.value]
        comp_orphans = [c for c in doc.compositions.values()
                        if c.generation_status == GenerationStatus.GENERATING.value]
        if not look_orphans and not comp_orphans:
            return receipt
        recovered_looks: List[str] = []
        recovered_comps: List[str] = []
        for lk in look_orphans:
            lk.generation_status = GenerationStatus.FAILED.value
            generation_records.append_record(pdir, {
                "generation_id": f"gen_orphan_{uuid.uuid4().hex[:8]}",
                "operation": "resources.recover_orphan",
                "look_id": lk.look_id,
                "basis": None, "prompt": None, "model": None, "seed": None,
                "seeds_used": None, "candidate_asset_ids": [], "candidate_files": [],
                "candidate_urls": [],
                "started_at": None, "completed_at": time.time(),
                "status": "failed", "error": ORPHAN_ERROR,
                "detail": "进程重启发现孤儿 generating 状态（上次生成未达终态即进程消失），已置为 failed；"
                          "原 generation 身份不可考，候选图若已落盘留在 generation 诊断目录",
                "verified": False, "resource_document_revision_after": None,
            })
            recovered_looks.append(lk.look_id)
        for c in comp_orphans:
            c.generation_status = GenerationStatus.FAILED.value
            generation_records.append_record(pdir, {
                "generation_id": f"gen_orphan_{uuid.uuid4().hex[:8]}",
                "operation": "resources.recover_orphan",
                "composition_id": c.composition_id,
                "basis": None, "prompt": None, "model": None, "seed": None,
                "seeds_used": None, "candidate_asset_ids": [], "candidate_files": [],
                "candidate_urls": [],
                "started_at": None, "completed_at": time.time(),
                "status": "failed", "error": ORPHAN_ERROR,
                "detail": "进程重启发现孤儿 generating 的 Composition（同 look 恢复语义），已置为 failed",
                "verified": False, "resource_document_revision_after": None,
            })
            recovered_comps.append(c.composition_id)
        store.save(doc)
        # 写后重读验证（truth gate 同口径）
        reloaded = store.load()
        receipt["verified"] = (
            all(reloaded.looks[lid].generation_status == GenerationStatus.FAILED.value
                for lid in recovered_looks)
            and all(reloaded.compositions[cid].generation_status == GenerationStatus.FAILED.value
                    for cid in recovered_comps))
        receipt["recovered"] = recovered_looks + recovered_comps
        receipt["recovered_looks"] = recovered_looks
        receipt["recovered_compositions"] = recovered_comps
    return receipt


async def recover_all_projects(project_manager) -> dict:
    """全项目孤儿清理（api_server startup 用）。单项目失败不阻塞其它项目/启动。"""
    summary: Dict[str, Any] = {
        "ok": True, "operation": "resources.recover_all_projects",
        "projects": {}, "total_recovered": 0, "errors": {},
    }
    for name in project_manager.list_projects():
        try:
            pdir = project_manager.get_project_dir(name)
            r = await recover_orphaned_generations(pdir, name)
            summary["projects"][name] = {"recovered": len(r.get("recovered") or []),
                                         "scanned": r.get("scanned", 0)}
            summary["total_recovered"] += len(r.get("recovered") or [])
        except Exception as e:   # 单项目失败（损坏/IO）不阻塞启动，记录继续
            summary["ok"] = False
            summary["errors"][name] = f"{type(e).__name__}: {e}"
    return summary
