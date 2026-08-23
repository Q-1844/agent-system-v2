# AI-7C-H_CANCEL_HOTFIX_REPORT.md

> AI-7C-H Cancel / Restart Hotfix 执行报告（承接 AI-7C-H_FINAL_HANDOVER.md 之后，
> 依据《详细流程文件/AI-7C-H Cancel.md》实施）。
> 分支 `refactor/shot-pipeline`，基线 HEAD `bd6f98a`（hotfix 改动在工作区，未提交）。

## 1. 原 blocker 的真实根因

旧代码存在四条互相叠加的缺陷链：

1. **stale 整对象覆盖**：`run_character_sync` / `run_production_planning` 在进入时
   加载一次 `ex`，执行长任务（Agnes 生成 / Planner 调用）几分钟后用这个旧
   Python 对象 `ex.status = "running"; store.save_execution(ex)` 整对象落盘——
   把用户已取消的 `cancelled` 状态覆盖回 `running`（取消复活）。
2. **取消不传播**：`cancel_execution` 对 committed 执行直接置终态 `cancelled`，
   但 E1 已拥有的 ProjectProductionRun 不被取消，继续生产并阻塞新计划
   （`PROJECT_PRODUCTION_ACTIVE`）。
3. **run-start 竞态**：最终 cancel 检查、`start_run()` 创建 R1、把 `run_id` 关联回
   E1 三步无锁保护，与 `cancel_execution()` 并发时可能产生「R1 已启动但 E1 不知道
   R1 是谁」的孤立 run。
4. **重启复活旧计划**：`recover_executions()` 把 `cancel_requested=true` 的执行当
   普通 active execution 续跑（重新 kickoff post-commit）；前端 restart 则
   cancel 与新建会话几乎同时发出，S2 撞上旧 R1 的 `_active_production` 兜底。

## 2. 最终 cancellation 状态语义

- 取消是**请求**不是瞬间完成：committed 执行取消后持久态为
  `status=running + cancel_requested=true`，前端显示「正在停止…」。
- 收敛条件（三者同时满足才进入终态 `cancelled`）：
  1. 无其他在飞 post-commit 任务（`_INFLIGHT_POSTCOMMIT` 记账）；
  2. owned ProductionRun 不再活跃（精确 `production_run_id` 关联，非猜测）；
  3. 状态翻转在 `project_write_lock` 短临界区内 reload-latest 完成。
- 提交前取消 → 立即终态 `cancelled`（canonical 未动）。
- 已提交取消不回滚已落盘的创作变更与资产（§38：取消=停止继续生产，非 undo）。
- 「已取消」只来自后端持久终态，LLM 文本不能决定。

## 3. 如何防止 stale Execution 覆盖取消状态

新增最小 guarded mutation helper（`mutate_execution_guarded`）：

```text
获取 per-project project_write_lock（复用既有锁）
→ 从磁盘 reload 最新 Execution
→ 已 terminal：拒绝一切普通写入（旧对象不得复活）
→ cancel_requested 且 policy=advance：拒绝（不得推进 stage/status/新工作）
→ cancel_requested 且 policy=record：允许（已开始最小单元的真实完成记录）
→ 只修改 mutator 声明的字段 → atomic save → 释放锁
```

全部会与取消并发的 post-commit 持久化路径已迁移：char sync 完成记录、
char→planning 推进、planning 逐 Scene checkpoint、planning→run 推进、
run_id 关联、blocked 落盘、completion/cancellation。长生命周期函数不再持有
几分钟后整对象覆盖的旧引用。

## 4. 使用的锁与保护范围

- **`project_write_lock(project)`**（既有 per-project 锁，未新建锁）：保护
  ①`cancel_execution` 状态翻转；②所有 `mutate_execution_guarded` 临界区；
  ③§七 run-start 临界区（最终 cancel 检查 + `starter(manifest)` 创建 R1 +
  `production_run_id` 关联落盘——全部本地磁盘操作，是本 hotfix 唯一允许在锁内
  创建 run 的例外，保证 cancel 与 run-start 二选一互斥）。
- **`session_lock`**（既有）：HTTP 层 cancel 端点串行化。
- 锁从不跨 Agnes / Planner / 视频生成等长网络请求等待（除上述全本地的 run-start
  临界区）。

## 5. `cancel_requested` 的检查边界

| 边界 | 行为 |
|---|---|
| `commit_canonical` 锁内 reload | 终态/cancel_requested → 不提交 |
| `run_character_sync` 进入前 | return `cancelling` |
| 每个角色开始前 | reload-latest，取消则 break（已开始单元自然收尾） |
| 进入 planning 前 | guarded advance 被拒 → return |
| `run_production_planning` 进入前 / 每 Scene 开始前 | return `cancelling` |
| all-plan gate | blocked 写入经 advance guard 被拒（取消优先，不误报受阻） |
| §七 run-start 临界区最终检查 | 情况 A：不创建 run |
| `_maybe_kickoff_postcommit` | 不派发新 post-commit 工作 |
| `_run_postcommit_to_completion` 每步前 | 复查，取消则跳过 |
| `resume_execution` | `cancelling`，驱动收敛而非推进 |
| `recover_executions`（§九） | 进入完成取消路径，不续跑 |
| GET execution 读取路径 | 惰性收敛（前端轮询驱动「正在停止→已取消」推进） |
| postcommit 失败落 blocked | 取消优先不误写 |

## 6. Execution 与 ProductionRun 的精确关联

直接复用既有 `ChangeExecution.production_run_id` 持久字段（AI-7C §59 遗产），
未新建 registry。hotfix 的关键改动是把「创建 R1 + `run_id` 关联落盘」放进
`project_write_lock` 临界区（§七）：run starter 先得锁则 R1 创建后关联**先落盘
再释放锁**，后续取得锁的 cancel 必然能通过 `production_run_id` 精确找到 R1；
绝不通过「找当前项目任意 active run」猜测。

## 7. 已启动 ProductionRun 如何被取消

`_propagate_cancel_to_owned_run` → 复用既有
`ProjectProductionManager.request_cancel`（§二十八 cooperative 语义：已提交的
Agnes job 允许完成并保存，Scene 间立即停；已生成资产保留不回滚）。未重写
ProductionRun cancellation。传播幂等，由四个驱动点触发：
`cancel_execution` 收尾、stop watcher（`_spawn_stop_watcher`，2s 轮询 / 24h 兜底
上限）、GET 读取惰性收敛、backend restart 恢复链。

## 8. 为什么没有引入新的工作流框架

项目已具备 `durable ChangeExecution` / `ProjectProductionRun` / `JobQueue` /
atomic persistence / `project_write_lock` / restart recovery 全部所需原语；
本 hotfix 只是把既有组件的 cancellation 语义正确连接（§十二边界）。新增后端代码
仅一个 helper + 收敛/传播路径，改动集中在 execution service / router /
restart 前端 / 少量测试（§十六），未触碰 Temporal/Celery/事件总线/Event
Sourcing/全局 revision 等任何大件，也未开始 AI-7D。

## 9. restart（backend）时如何处理正在取消的 Execution

`recover_executions()` 对 `cancel_requested=true` 非终态执行：

- owned run 仍活跃 → 异步继续请求/恢复其 cancellation + stop watcher
  （无运行 loop 时由 GET 惰性收敛兜底）；
- 无活跃子生产 → 直接收敛 `cancelled`；
- **绝不**按普通 active execution 重新 kickoff post-commit（旧计划不复活）。

配合既有 `ProjectProductionManager.recover_runs()`（§三十/§三十一）把
`cancel_requested` 的 run 在重启时转为 `cancelled`，形成完整闭环：
「点停止 → 后端崩溃 → 重启 → 旧计划仍死」。

## 10. restart（前端「停止并重新规划」）为什么现在会等待

`resolveChangeChoice('restart')`（agentUi.ts）新语义：

```text
POST cancel E1 → UI「正在停止当前任务…」
→ waitForExecutionStopped(240s)：持续 GET 后端权威 Execution/ProductionRun 状态
→ 确认旧执行安全 cancelled（终态）
→ 才创建新 ChangeSession S2 → 重新处理用户的新要求
```

- 240s 窗口覆盖真实生产收尾（Agnes view 生成约 1-3 分钟/角色；30s 会把 §十五
  期望的「新 ChangeSession 自动开始」退化成暂存回退，故上调并保留超时回退）。
- 超时回退不丢请求：请求暂存并明确告知用户，稍后重试。
- 不提前宣称「正在重新规划」；`_active_production` 兜底保留。
- `cancelExecution` 后 execution 仍 active 时继续轮询（stopping 期间 UI 持续读
  后端真相）；`create()` 解绑旧 execution，避免残留显示。

## 11. 六个关键竞态测试结果（tests/test_ai_7ch_cancel_race.py，全过）

| Case | 测试 | 结果 |
|---|---|---|
| 1 stale object resurrection（确定性 barrier：后台持旧对象暂停→cancel→释放） | `test_case1_stale_object_resurrection` | PASS：B 单元自然收尾记录，不进 planning，最终 cancelled 不复活 |
| 2 cancel during character_sync | `test_case2_cancel_between_chars_no_new_work` | PASS：A 保留、B 不启动、kickoff 拒绝、不进 planning |
| 3 cancel during planning | `test_case3_cancel_during_planning_no_run` | PASS：s02 已开始单元可收尾，不进 run starter、不建 run |
| 4 cancel vs run-start race | `test_case4a_cancel_before_run_start_no_run` / `test_case4b_run_start_then_cancel_propagates` | PASS：情况 A 根本不创建 run；情况 B run_id 关联落盘后 cancel 精确找到 R1 并传播 |
| 5 ProductionRun 已 active 时 restart | `test_case5_active_run_cancel_then_new_session_unblocked` | PASS：精确 cancel R1 → E1 cancelled → `_active_production` 为空 |
| 6 stopping 中 backend restart | `test_case6a_restart_during_stopping_no_run` / `test_case6b_restart_during_stopping_active_run` | PASS：不复活、不续跑、继续收敛、最终允许新计划 |
| 附 resume 尊重取消 | `test_resume_cancelling_returns_no_new_work` | PASS |

回归：`test_ai_7ch_cancel_race.py`(9) + `test_ai_7c_6_cancel.py`(5) + AI-7C
系列（1/2/3/4/4b/5/5b/7/H4/S2/S3/S4 共 82）+ `test_api.py`/`test_ai_7c_s5_endpoint.py`(11)
= **107 passed**；typecheck 仅 6 个预存在错误（AI-7C-S 基线一致，均不在 hotfix
文件）；`vite build` 通过。`test_project_autopilot.py` 13 failed 为预存在环境问题
（缺视频生成二进制 `[WinError 2]`，经 stash 干净 HEAD 复跑同样失败，与 hotfix
无关，按 §十四不修无关旧错误）。

## 12. 浏览器实际结果（真实前端 vite preview + 真实 FastAPI + 真实 Agnes）

**场景 A（§十五 主场景）**——项目 `__cancel_gp_a`，E1 `exec_bb15fd40139f` 真实
运行全链（char sync 双角色真实 Agnes 三视图生成 → planning 双 Scene 真实规划 →
真实 ProductionRun `prun_4163f189bc` 活跃）：

```text
FloatingBar 提交「把AudA的头发换成蓝色」
→ 二选一 UI 出现 → 点「停止当前任务并重新规划」
→ 消息「正在停止当前任务…」+ 运行卡「正在停止…」（均真实出现）
→ 后端真相 running + cancel_requested（未提前宣称已取消）
→ owned run prun_4163f189bc: running → cancelled（Gate 3 精确传播）
→ E1: (running,True) → (cancelled,True)，15s 复查不复活（Gate 1）
→ stage 冻结 production_prepare、planning/角色记录冻结、无新 run（Gate 2）
→ 新会话 cs_215e06115a0f 自动创建，status=validating_plan，
   活跃 run=[]（未被已放弃的旧 R1 阻塞，Gate 4）
```

**场景 B（§十五 第二场景）**——项目 `__cancel_gp_b`，E1 stopping
（cancel_requested + owned R1 running+cancel_requested）→ 真实 kill backend →
重启：恢复链收敛 E1 `cancelled`（stage 未推进）、R1 `cancelled`、0 新 job
（旧生产链未复活）；页面恢复显示「已取消。已提交的创作变更保留（§38）。」，
与持久状态一致、无 blocker。

四个 Gate 全部在真实浏览器环境中验证成立。

## 13. 修改的生产文件

| 文件 | 内容 |
|---|---|
| `src/agents/change_protocol/execution_service.py` | guarded mutation helper、取消收敛/传播/stop watcher、各边界 cancel 检查、§七 run-start 临界区、recover 完成取消路径、cancel_execution async 化 |
| `routers/agents.py` | GET execution 惰性收敛；cancel 端点 await |
| `src/store/changeSession.ts` | `executionStopping` 派生态、`waitForExecutionStopped`、取消后继续轮询、create 解绑旧 execution |
| `src/store/agentUi.ts` | restart 等待旧执行真正停止（240s 窗口 + 暂存回退） |
| `src/components/agent/AgentConversationSurface.vue` | 执行中/正在停止…/已取消 三态标签（后端持久真相） |
| `src/components/agent/ChangePlanReview.vue` | 正在停止/已取消状态条；stopping 时禁用取消按钮 |
| `src/components/agent/FloatingAgentBar.vue` | running 点 tooltip「正在停止当前任务…」 |
| `tests/test_ai_7c_6_cancel.py` | cancel 调用 async 化适配 |
| `tests/test_ai_7ch_cancel_race.py` | 新增：六个竞态 + resume 共 9 测试 |

（一次性验收工具未提交：`cancel_gp_seed.py` / `cancel_gp_audit.py`，与
`gp_seed.py` 同类，标注勿提交。）

## 14. 仍未解决、会影响本语义的限制

1. **paused owned run + Execution 取消**：`request_cancel` 对 paused run 只置
   `cancel_requested`，无 loop 驱动其到终态（`recover_runs` 不扫描 paused）→
   Execution 可能停留在「正在停止…」直至下一次 restart 后仍不收敛
   （paused+cancel_requested 重启后仍 paused）。属预存在 ProjectRun 语义
   （§二十六/§二十八/§三十），按 §十二边界本 hotfix 未扩改。建议后续阶段：
   `request_cancel` 将 paused run 直接转 cancelled，或 `recover_runs` 扫描
   paused+cancel_requested。
2. **240s 等待窗口**：极端长收尾（>4 分钟单单元）仍触发暂存回退（请求不丢，
   需用户重新提交）。
3. **视频生成二进制缺失**（`[WinError 2]`，预存在环境问题）：影响 autopilot
   全量测试 13 个及真实 run 的 video 阶段；本次验收取消均发生在 video 之前，
   未受影响。
4. F5 后 projectName 复位默认（K3 已知限制，重选项目即可）；
   jobs.json 增长等 AI-7C-S 已知限制不变。

---

## 四 Gate 结论

- **Gate 1**：旧 Execution 永远不能复活 —— PASS（Case 1 测试 + 浏览器 15s 复查 +
  重启后 terminal 跳过恢复）。
- **Gate 2**：停止后不再启动新 Character / Planner Scene / ProductionRun /
  后续 production —— PASS（Case 2/3/4a + 浏览器 stage/记录冻结、无新 run、
  重启 0 新 job）。
- **Gate 3**：取消传播到准确的子 Run —— PASS（Case 4b/5/6b + 浏览器
  prun_4163f189bc running→cancelled 精确传播轨迹）。
- **Gate 4**：旧运行安全停止后新修改可重新规划执行 —— PASS（Case 5 + 浏览器
  S2 cs_215e06115a0f 自动创建并推进 validating_plan，活跃 run=[]）。

```text
AI-7C-H CANCEL HOTFIX: PASS
```

```text
STOP.
```

（未开始 AI-7D。）
