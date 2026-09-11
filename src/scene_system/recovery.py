# src/scene_system/recovery.py
# Scene System V1 — Phase S2A §15：startup orphan sweep（进程崩溃恢复）。
#
# 两层恢复设计（完全沿用 Resources 已验证模式）：
#   1. cancel cleanup（visual_service 内联）：取消/进程关闭时尽力写诚实终态
#      （failed + record），然后 re-raise——永不吞 CancelledError。
#   2. startup orphan sweep（本模块）：进程死亡（kill/断电/崩溃）留下的孤儿
#      LocationView.generation_status == "generating" 在 api_server 启动时清理。
#
# 🔴 判定无歧义的依据：startup 时刻进程内**必然没有 in-flight generation**
#    （JobQueue/请求尚未开始处理）——凡 generating 必为上次进程遗留。
# 🔴 一次性确定性扫描：无 daemon、无 background worker、无轮询。
# 🔴 幂等（T25）：无孤儿时零写入。schema 损坏 fail closed：上抛由调用方决定
#    （api_server startup 捕获记 warning，不阻塞启动）。
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import generation_records
from .generation_records import generation_records_path
from .models import GenerationStatus
from .store import SceneSystemStore

ORPHAN_ERROR = "orphaned_by_process_restart"


async def recover_orphaned_view_generations(project_dir,
                                            project_name: Optional[str] = None) -> dict:
    """单项目孤儿清理：LocationView 与 SceneInstanceView 的 generating → failed +
    诚实 record（无 generation 身份可考时用合成 gen_orphan_* id，basis=None 不臆造）。
    幂等：无孤儿时零写入。S2B 扩展：scene_instance_views 进入同一恢复体系（§22）。"""
    pdir = Path(project_dir)
    pname = project_name or pdir.name
    store = SceneSystemStore(pdir, pname)
    receipt: Dict[str, Any] = {
        "ok": True, "operation": "scene.recover_orphaned_view_generations",
        "project": pname, "scanned": 0, "recovered": [], "error": None,
    }
    if not store.exists():
        return receipt
    async with store.write_lock():
        doc = store.load()   # 损坏 → SceneSystemSchemaError 上抛（不静默重置）
        receipt["scanned"] = len(doc.views) + len(doc.scene_instance_views)
        view_orphans = [v for v in doc.views.values()
                        if v.generation_status == GenerationStatus.GENERATING.value]
        sciv_orphans = [v for v in doc.scene_instance_views.values()
                        if v.generation_status == GenerationStatus.GENERATING.value]
        if not view_orphans and not sciv_orphans:
            return receipt
        recovered: List[str] = []
        for v in view_orphans:
            v.generation_status = GenerationStatus.FAILED.value
            generation_records.append_record(pdir, {
                "generation_id": f"gen_orphan_{uuid.uuid4().hex[:8]}",
                "operation": "scene.recover_orphan",
                "location_id": v.location_id,
                "view_id": v.view_id,
                "basis": None, "prompt": None, "model": None, "seed": None,
                "seeds_used": None, "selected_reference_ids": [],
                "candidate_files": [], "candidate_urls": [],
                "started_at": None, "completed_at": time.time(),
                "status": "failed", "error": ORPHAN_ERROR,
                "detail": "进程重启发现孤儿 generating 状态的 LocationView（上次生成未达终态"
                          "即进程消失），已置为 failed；原 generation 身份不可考，"
                          "候选图若已落盘留在 generation 诊断目录",
                "verified": False, "revision_after": None,
            }, path=generation_records_path(pdir))
            recovered.append(v.view_id)
        for v in sciv_orphans:
            v.generation_status = GenerationStatus.FAILED.value
            generation_records.append_record(pdir, {
                "generation_id": f"gen_orphan_{uuid.uuid4().hex[:8]}",
                "operation": "scene.recover_orphan",
                "scene_instance_view_id": v.scene_instance_view_id,
                "scene_instance_id": v.scene_instance_id,
                "location_view_id": v.location_view_id,
                "basis": None, "prompt": None, "model": None, "seed": None,
                "seeds_used": None, "selected_reference_ids": [],
                "candidate_files": [], "candidate_urls": [],
                "started_at": None, "completed_at": time.time(),
                "status": "failed", "error": ORPHAN_ERROR,
                "detail": "进程重启发现孤儿 generating 状态的 SceneInstanceView（S2B §22，"
                          "同 LocationView 恢复语义），已置为 failed",
                "verified": False, "revision_after": None,
            }, path=generation_records_path(pdir))
            recovered.append(v.scene_instance_view_id)
        store.save(doc)
        # 写后重读验证（truth gate 同口径）
        reloaded = store.load()
        receipt["verified"] = (
            all(reloaded.views[vid].generation_status == GenerationStatus.FAILED.value
                for vid in [v.view_id for v in view_orphans])
            and all(reloaded.scene_instance_views[vid].generation_status
                    == GenerationStatus.FAILED.value
                    for vid in [v.scene_instance_view_id for v in sciv_orphans]))
        receipt["recovered"] = recovered
        receipt["recovered_location_views"] = [v.view_id for v in view_orphans]
        receipt["recovered_scene_instance_views"] = [v.scene_instance_view_id
                                                     for v in sciv_orphans]
    return receipt


async def recover_all_projects(project_manager) -> dict:
    """全项目孤儿清理（api_server startup 用）。单项目失败不阻塞其它项目/启动。"""
    summary: Dict[str, Any] = {
        "ok": True, "operation": "scene.recover_all_projects",
        "projects": {}, "total_recovered": 0, "errors": {},
    }
    for name in project_manager.list_projects():
        try:
            pdir = project_manager.get_project_dir(name)
            r = await recover_orphaned_view_generations(pdir, name)
            summary["projects"][name] = {"recovered": len(r.get("recovered") or []),
                                         "scanned": r.get("scanned", 0)}
            summary["total_recovered"] += len(r.get("recovered") or [])
        except Exception as e:   # 单项目失败（损坏/IO）不阻塞启动，记录继续
            summary["ok"] = False
            summary["errors"][name] = f"{type(e).__name__}: {e}"
    return summary
