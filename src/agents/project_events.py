"""项目级实时事件总线（AI-7D-R）。

进程内、per-project 发布订阅：Agent 工具落盘 / 资源锁定等业务事件推给所有打开该
项目的前端连接（SSE）。目的——Agent 在后台跑长任务时，用户切到其它模块也能：
  1) 实时看到 Agent 对故事段/剧本的修改（story.changed → 相关页面自动刷新）；
  2) 对正在被 Agent 修改的资源做只读锁定（resource.locked → 编辑器只读，
     unlocked → 恢复编辑），避免两边同时写互相覆盖。

设计：
- 单例（get_project_bus()）。publish 同步、非阻塞：慢消费者丢最旧事件腾位置，
  绝不让 Agent 的写入路径被某个卡住的前端连接拖住（与 JobQueue 订阅同思想）。
- 资源锁状态保存在总线内（locks[project][resource] = meta）；新订阅者连接时先
  收到锁快照（replay 当前已锁资源），与 jobs/stream 的 snapshot 语义一致——
  打开页面时 turn 可能已在跑。
- 事件（都带 project/at）：
    resource.locked    {resource, by, session_id}   Agent 开始修改某资源
    resource.unlocked  {resource, by, session_id}   Agent 完成/失败/停止
    story.changed      {by, session_id, operation}  故事段/剧本正文已落盘

第一版资源只有 RESOURCE_STORY（剧本域：story_text + scenes）。角色/分镜等后续
Agent 接入时新增资源名即可，总线不关心资源语义。
"""
import asyncio
import time
from typing import Optional

# 资源名
RESOURCE_STORY = "story"   # 剧本域：story_text（正文）+ scenes（故事段）
RESOURCE_CHARACTER = "character"   # 角色域：project.json 的 characters（Character Designer 写入域）
RESOURCE_STORYBOARD = "storyboard"   # 分镜域：scenes[].shots[]（Director 写入域）
RESOURCE_RESOURCES = "resources"   # 生产资源域：resources.json（Look/Clothing/Prop/Composition/
                                   # ReferenceAsset——Agent System V2 Phase B，Resource Agent 写入域）
RESOURCE_SEQUENCE = "sequence"   # 成片装配域：sequences.json + output/assembly/（Phase 44B，
                                         # Assembly Agent 写入域：SequenceBlock/manifest/提案）
RESOURCE_SCENE_SYSTEM = "scene_system"   # 场景系统域：scene_system.json（Location/SpatialAnchor/
                                         # SetLayout/LocationView/SceneInstance/SceneState/
                                         # SceneInstanceView——Agent System V2 Phase C2，
                                         # Scene Agent 写入域）

_MAX_QUEUE = 256


class ProjectEventBus:
    def __init__(self):
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._locks: dict[str, dict[str, dict]] = {}   # project -> resource -> meta

    # ---------- 订阅 ----------
    def subscribe(self, project: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subs.setdefault(project, []).append(q)
        return q

    def unsubscribe(self, project: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(project)
        if subs and q in subs:
            subs.remove(q)

    def snapshot(self, project: str) -> list[dict]:
        """当前锁快照：新订阅者连接时 replay（已锁资源先发 resource.locked）。"""
        return [dict(meta, type="resource.locked", resource=res)
                for res, meta in self._locks.get(project, {}).items()]

    # ---------- 发布 ----------
    def publish(self, project: str, event: dict) -> None:
        ev = dict(event)
        ev.setdefault("at", time.time())
        ev["project"] = project
        for q in list(self._subs.get(project, ())):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # 慢消费者：丢最旧腾位置（保最新状态），绝不阻塞 Agent 写入
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except Exception:
                    pass

    def emit_story_changed(self, project: str, *, by: str = "",
                           session_id: str = "", operation: str = "") -> None:
        self.publish(project, {"type": "story.changed", "by": by,
                               "session_id": session_id, "operation": operation})

    def emit_characters_changed(self, project: str, *, by: str = "",
                                session_id: str = "", operation: str = "") -> None:
        """角色域内容已落盘（Character Designer 写路径发布；角色页订阅静默刷新）。"""
        self.publish(project, {"type": "character.changed", "by": by,
                               "session_id": session_id, "operation": operation})

    def emit_storyboard_changed(self, project: str, *, by: str = "",
                                session_id: str = "", operation: str = "") -> None:
        """分镜域内容已落盘（Director 写路径发布；分镜页订阅静默刷新）。"""
        self.publish(project, {"type": "storyboard.changed", "by": by,
                               "session_id": session_id, "operation": operation})

    # ---------- 资源锁 ----------
    def lock(self, project: str, resource: str, *, by: str = "",
             session_id: str = "") -> bool:
        """锁定资源。返回 True=本次新锁定（已发事件）；False=已锁（幂等，不重复发）。"""
        locks = self._locks.setdefault(project, {})
        if resource in locks:
            return False
        meta = {"by": by, "session_id": session_id}
        locks[resource] = dict(meta)
        self.publish(project, {"type": "resource.locked", "resource": resource, **meta})
        return True

    def unlock(self, project: str, resource: str) -> bool:
        """释放资源。返回 True=此前是锁定的（已发事件）；False=本来就没锁。"""
        locks = self._locks.get(project)
        if not locks or resource not in locks:
            return False
        meta = locks.pop(resource)
        self.publish(project, {"type": "resource.unlocked", "resource": resource,
                               "by": meta.get("by", ""),
                               "session_id": meta.get("session_id", "")})
        return True

    def is_locked(self, project: str, resource: Optional[str] = None) -> bool:
        locks = self._locks.get(project, {})
        if resource is None:
            return bool(locks)
        return resource in locks


def emit_changed(bus, project: str, resource: str, *, by: str = "",
                 session_id: str = "", operation: str = "") -> None:
    """按资源名分发内容变更事件（Agent 流式端点用；资源 → 事件类型映射的唯一入口）。
    story → story.changed；character → character.changed；storyboard → storyboard.changed；
    resources → resources.changed；scene_system → scene_system.changed；
    未知资源发通用 resource.changed。"""
    type_by_resource = {RESOURCE_STORY: "story.changed",
                        RESOURCE_CHARACTER: "character.changed",
                        RESOURCE_STORYBOARD: "storyboard.changed",
                        RESOURCE_RESOURCES: "resources.changed",
                        RESOURCE_SCENE_SYSTEM: "scene_system.changed",
                        RESOURCE_SEQUENCE: "sequence.changed"}
    bus.publish(project, {"type": type_by_resource.get(resource, "resource.changed"),
                          "resource": resource, "by": by,
                          "session_id": session_id, "operation": operation})


_bus: Optional[ProjectEventBus] = None


def get_project_bus() -> ProjectEventBus:
    """进程级单例（deps 里 router 直接用；测试可调 reset_project_bus 清态）。"""
    global _bus
    if _bus is None:
        _bus = ProjectEventBus()
    return _bus


def reset_project_bus() -> None:
    """测试用：重置单例（清掉订阅/锁状态）。"""
    global _bus
    _bus = None
