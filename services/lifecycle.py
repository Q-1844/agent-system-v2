"""启动期生命周期：崩溃恢复 + 孤儿 job 判定（PHASE_5 §5.2，从 api_server 抽出以瘦身）。

recover_pending_videos：重启后对 video.status==generating 且有 video_id 的镜头先 poll 一次，
不盲目重提交（省 60s + 不重扣配额）。job_target_exists：清理孤儿 job 用。
"""
from typing import Optional

import deps
from deps import logger, projects_dir, JOB_HANDLERS


async def recover_pending_videos():
    """🔴 崩溃恢复（PHASE_3 §3.4）：重启后对 video.status==generating 且有 video_id
    的镜头先 poll_video 查一次，不盲目重提交（省 60s + 不重扣配额）。
    已完成 -> 下载入库；仍在跑/失效 -> 标 queued 并入队由 video handler 续轮询。"""
    try:
        if deps.agnes_client is None:
            return
        from src.core.store import ProjectStore
        client = deps.agnes_client
        for pdir in projects_dir.iterdir():
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
                if sh.video.status != "generating" or not sh.video.video_id:
                    continue
                vid = sh.video.video_id
                logger.info(f"恢复检查: {p.name}/{sh.shot_id} video_id={vid}")
                try:
                    res = await client.poll_video(vid, interval=5, max_wait=60)
                    dest = str(pdir / "output" / "videos" / f"{sh.shot_id}.mp4")
                    await client.download(res["url"], dest)
                    store.update_shot(sh.shot_id, lambda s, d=dest, r=res: (
                        s.video.mark_done(d, r["url"]),
                        setattr(s.video, "seconds_returned", r["seconds"]),
                        setattr(s.video, "size_returned", r["size"]),
                    ))
                    logger.info(f"恢复完成: {sh.shot_id}")
                except Exception as e:
                    logger.warning(f"恢复 {sh.shot_id} 未完成（{e}），重新入队续轮询")
                    store.update_shot(sh.shot_id,
                                      lambda s: setattr(s.video, "status", "queued"))
                    if "video" in JOB_HANDLERS and deps.job_queue is not None:
                        deps.job_queue.submit(p.name, "video", sh.shot_id)
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
