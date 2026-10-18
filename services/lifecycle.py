"""启动期生命周期：崩溃恢复 + 孤儿 job 判定（PHASE_5 §5.2，从 api_server 抽出以瘦身）。

recover_pending_videos：重启后对 video.status==generating 且有 video_id 的镜头先 poll 一次，
不盲目重提交（省 60s + 不重扣配额）。job_target_exists：清理孤儿 job 用。
"""
from typing import Optional

import deps
from deps import logger, projects_dir, JOB_HANDLERS


async def recover_pending_videos(queue=None, client=None, projects_root=None,
                                 handlers=None):
    """🔴 崩溃恢复（PHASE_3 §3.4）：重启后对 video.status==generating 且有 video_id
    的镜头先 poll_video 查一次，不盲目重提交（省 60s + 不重扣配额）。
    已完成 -> 下载入库；仍在跑/失效 -> 标 queued 并入队由 video handler 续轮询。

    42.md §二缺陷②：参数化——生产 startup 用 deps 全局（默认不变）；进程内
    测试驱动器（隔离队列文件）传入自己的 queue/handlers 即可获得同等恢复钩子，
    不再出现「驱动器进程被杀后在途视频无人收货」（Phase 41 实证缺口）。"""
    try:
        from src.core.store import ProjectStore
        client = client or deps.agnes_client
        handlers = handlers if handlers is not None else JOB_HANDLERS
        queue = queue if queue is not None else deps.job_queue
        root = projects_root or projects_dir
        if client is None:
            return
        for pdir in root.iterdir():
            if not pdir.is_dir():
                continue
            if not (pdir / "output" / "project.json").exists():
                continue
            try:
                store = ProjectStore(pdir)
                p = store.load()
            except Exception:
                continue
            for sh in p.all_shots():
                if sh.video.status != "generating":
                    continue
                if not sh.video.video_id:
                    # 42.md §二缺陷④：提交结果不明（响应未及落库）——无幂等键
                    # 不可查询，不得静默重提交。显式告警留在原状态，由下一次
                    # 显式重提交时标注 previous_submit_uncertain（handler 侧）。
                    logger.warning(
                        f"提交结果不明: {p.name}/{sh.shot_id} generating 且无 "
                        "video_id（响应未及落库）——不自动处置；重提交将标注 "
                        "previous_submit_uncertain")
                    continue
                vid = sh.video.video_id
                # 2.5 新契约任务必须用提交时同一 model_name 轮询（旧数据无 model 字段
                # → None = config 默认，legacy 行为不变）
                sh_model = getattr(sh.video, "model", None)
                # Production recovery reuses the original queue job and handler. This
                # preserves origin/basis/version receipt and never overwrites legacy paths.
                if queue is not None and "video" in handlers and hasattr(queue, "jobs"):
                    # Match the Provider task exactly. A newer queued job without
                    # an ID must not borrow an older task's result or provenance.
                    matches = [j for j in queue.jobs.values()
                               if j.project == p.name and j.shot_id == sh.shot_id
                               and j.kind == "video" and j.params.get("provider_task_id") == vid]
                    original = max(matches, key=lambda j: j.created_at) if matches else None
                    if original is not None:
                        if (original.state == "cancelled" or
                                original.params.get("unattached_artifact") or
                                original.params.get("unattached_provider_receipt")):
                            logger.warning("保留待核对/已取消结果，不自动重新挂接: %s/%s (%s)",
                                           p.name, sh.shot_id, vid)
                            continue
                        live = getattr(queue, "_running_tasks", {}).get(original.job_id)
                        if original.state in ("running", "failed") and (live is None or live.done()):
                            original.state = "queued"
                            original.finished_at = None
                            original.error = None
                            queue._save()
                            queue._route(original.job_id, "video")
                        elif original.state == "done":
                            logger.warning("任务已结算但镜头仍 generating，需核对: %s/%s", p.name, sh.shot_id)
                    else:
                        queue.submit(p.name, "video", sh.shot_id, provider_task_id=vid,
                                     recovered=True, video_model=sh_model)
                    continue
                logger.info(f"恢复检查: {p.name}/{sh.shot_id} video_id={vid} model={sh_model or 'default'}")
                try:
                    res = await client.poll_video(vid, interval=5, max_wait=60,
                                                  model_name=sh_model)
                    from src.core.models import VideoVersion
                    import uuid
                    version_id = f"vv_{uuid.uuid4().hex[:12]}"
                    dest = str(pdir / "output" / "videos" / f"{sh.shot_id}_{version_id}.mp4")
                    await client.download(res["url"], dest)
                    vv = VideoVersion(version_id=version_id, video_id=vid, status="done",
                                      local=dest, url=res["url"], seconds_returned=res["seconds"],
                                      size_returned=res["size"], model=sh_model)
                    def settle(current):
                        if current.video.video_id != vid or current.final_video != sh.final_video:
                            raise RuntimeError("recovery task/selection changed; artifact retained unattached")
                        existing = next((v for v in current.video_versions if v.video_id == vid), None)
                        if existing is not None:
                            return
                        current.video_versions.append(vv)
                        current.final_video = vv.version_id
                        current.video.mark_done(dest, res["url"])
                        current.video.seconds_returned = res["seconds"]
                        current.video.size_returned = res["size"]
                    store.update_shot(sh.shot_id, settle)
                    logger.info(f"恢复完成: {sh.shot_id}")
                except Exception as e:
                    logger.warning(f"恢复 {sh.shot_id} 未完成（{e}），重新入队续轮询")
                    still_current = {}
                    def mark_pending(current):
                        if current.video.video_id == vid and current.final_video == sh.final_video:
                            current.video.status = "queued"
                            still_current["yes"] = True
                    store.update_shot(sh.shot_id, mark_pending)
                    if still_current and "video" in handlers and queue is not None:
                        queue.submit(p.name, "video", sh.shot_id)
    except Exception as e:
        logger.error(f"recover_pending_videos 失败: {e}")


def job_target_exists(project: Optional[str], shot_id: Optional[str]) -> bool:
    """判断 job 的 target 是否还存在（清理孤儿 job 用）。project/shot 任一不存在 -> False。
    无 project 的 job（如全局 compose）不算孤儿；controller 未就绪时不误清。"""
    if not project:
        return True
    if deps.project_manager is None:
        return True
    try:
        pdir = deps.project_manager.get_project_dir(project)
    except Exception:
        return False
    if not (pdir / "output" / "project.json").exists():
        return False
    if shot_id:
        from src.core.store import ProjectStore
        try:
            return ProjectStore(pdir).load().get_shot(shot_id) is not None
        except Exception:
            return False
    return True
