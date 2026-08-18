# src/agents/fake_shot_handlers.py
# AI-6F.0 §7：Shot Producer 长执行能力的 **deterministic fake** handler。
#
# 🔴 红线（§7/§21）：本文件 **绝不** 调 Agnes、**绝不** 写真实资产、**绝不** 走
# routers/shots.py 的 first_frame/video handler 逻辑。它只为证明 durable lifecycle 语义
# （§4/§6/§10/§22）：AgentTask 提交 fake long-running Job → HTTP 先返回 → Job 在 JobQueue
# 独立运行 → backend restart → 同一 AgentTask reattach 同一 Job → 不重复 submit →
# Job 终态 → AgentTask 正确结算。
#
# 设计为「drop-in 可替换」：未来 AI-6F 接通真实生成时，把 capability→kind 映射指向真实
# JOB_HANDLERS["first_frame"]/["video"]（routers/shots.py），AgentTask 侧 execution_ref /
# settlement bridge / 幂等逻辑不变（§17 final path）。
#
# Handler 契约对齐 JobQueue：`async def(job, emit) -> None`，emit(stage, msg, pct=None)。
# 不在全局 JOB_HANDLERS 注册（无副作用）——由 composition root（routers/agents.py）注册，
# 测试直接注入 handlers dict（与 routers/shots.py 注册真实 handler 同模式）。
from __future__ import annotations
import asyncio

# capability_id -> fake job kind（与真实 JobKind 字符串 **不** 冲突：真实为
# "first_frame"/"last_frame"/"video"，本处前缀 "agent_"，JobQueue._route 视为轻任务走 light_q）
FAKE_KIND_FOR_CAPABILITY = {
    "shot.first_frame": "agent_first_frame",
    "shot.last_frame": "agent_last_frame",
    "shot.video": "agent_video",
}

# fake handler 阶段名（对齐 §8 确定性 production phase 术语：queued/first_frame/last_frame/
# video/review/completed）。execution_ref.phase 存 capability 派生的 phase。
PHASE_FOR_CAPABILITY = {
    "shot.first_frame": "first_frame",
    "shot.last_frame": "last_frame",
    "shot.video": "video",
}


async def _fake_shot_handler(job, emit):
    """通用 deterministic fake handler。行为由 job.params 控制（测试可注入）：
      _fake_outcome  : "done"（默认）/ "failed"
      _fake_steps    : emit 步数（默认 3）
      _fake_step_sleep: 每步 sleep 秒（默认 0.05，足以让 delegate 在 Job 完成前返回 §18-3）
    不调 Agnes、不写 shot 资产；JobQueue 在 handler 正常返回后置 job.state=done。
    🔴 emit 是 async——必须 `await emit(...)`，否则 progress/_save/_broadcast 不生效。
    """
    params = getattr(job, "params", {}) or {}
    outcome = params.get("_fake_outcome", "done")
    steps = int(params.get("_fake_steps", 3))
    sleep = float(params.get("_fake_step_sleep", 0.05))
    kind = getattr(job, "kind", "agent_shot")

    for i in range(1, steps + 1):
        await asyncio.sleep(sleep)
        pct = int(100 * i / steps)
        await emit(f"step{i}", f"fake {kind} 进度 {i}/{steps}", pct)

    if outcome == "failed":
        raise RuntimeError("fake shot generation failure（AI-6F.0 测试用）")
    # outcome == "done"：正常返回 -> JobQueue._run_job 置 done / progress 100


def fake_handler_for(kind: str):
    """按 kind 返回 fake handler（composition root / 测试用）。三种 kind 同一实现，区别仅在
    job.params 与 kind 字段，行为一致。"""
    if kind not in FAKE_KIND_FOR_CAPABILITY.values():
        raise ValueError(f"未知 fake shot kind: {kind}")
    return _fake_shot_handler


def all_fake_handlers() -> dict:
    """{kind: handler} 全量，供 composition root 注册进 JOB_HANDLERS。"""
    return {kind: _fake_shot_handler for kind in FAKE_KIND_FOR_CAPABILITY.values()}
