# AI-7D-R · R1 报告 — Conversation + Basic Memory Foundation

> 阶段：R1（§53）。上一阶段：R0 基线审计（`AI-7D-R_R0_BASELINE_AUDIT.md`）已 PASS。
> 检查点 commit：`efea462`（D0–D3 + R0 审计，作为 R1 前的精确回滚/对照边界）。
> 验证：后端 12 个 R1 新测试 + D1/D2/D3 + 7B-3 共 47 测试通过；全量后端回归 559 passed、1 个有意的契约更新；前端 vue-tsc 在所有 R1 改动文件 0 错误（剩余错误均为 CLAUDE.md 记录的既有问题）。
> 按 §53/§89：**STOP，等你手测验收后才进 R2。**

---

## 1. 本阶段改了什么（files changed）

**新增（3）**
- `src/agents/work_memory.py` —— §10.2 共享工作记忆（项目级 append-only JSON，`output/agents/work_memory.json`，有界保留最近 200 条）。
- `src/agents/preferences.py` —— §10.3 显式偏好记忆（`output/agents/preferences.json`），含保守的偏好触发判定 `looks_like_preference`。
- `tests/test_ai_7d_r1_conversation_memory.py` —— 12 个 R1 测试。

**后端修改（8）**
- `src/agents/models.py` —— `AgentMessage` 增加一等字段 `message_id/conversation_id/agent_id/linked_work_ids/linked_change_session_id/linked_execution_id`（旧持久化 from_dict 向后兼容，从 meta 提升旧 linked 值）；`AgentSession` 增加 `conversation_id`。
- `src/agents/sessions.py` —— 新增 `get_or_create_conversation(agent_id)`：按 `(project, agent)` 返回长存 project 级会话（迁移最近旧 project 会话并回填消息 id，不丢历史）。
- `src/agents/runtime.py` —— `get_or_create_session` 对 project scope 走长存 conversation；新增 `append_session_message`、`work_memory/preference_store`、`_memory_supplement`（把工作记忆+偏好注入对话/inquiry 上下文）、`record_work`、`_maybe_capture_preference`（unified_turn 持久化 user 消息时保守捕获偏好）。
- `src/agents/inquiry.py` —— Producer/Specialist 答案构建时注入工作记忆+偏好片段。
- `src/agents/change_protocol/models.py` —— 新增终态 `no_action` 与集合 `SESSION_SETTLED`（approved/rejected/stale/failed/cancelled/no_action/blocked）；`ChangeSession` 增加 `latest_feedback`、`no_action_reason`。
- `src/agents/change_protocol/service.py` —— **根因 #1 核心修复**（见 §4）；新增 `_terminate_no_action`。
- `src/agents/change_protocol/script_proposal.py` —— 删除“0-item 时伪造 `剧本无需改动` A/B 决策”，改为诚实空提案，由 service 终态化为 no_action。
- `src/agents/change_protocol/execution_service.py` —— canonical 提交成功后 best-effort 写一条共享工作记忆（让其它 agent 知道工作真发生过，修根因 #4 的数据来源）。

**前端修改（4）**
- `src/store/changeSession.ts` —— **前端拱心石修复**：新增 `SESSION_SETTLED/SESSION_OPEN_FOR_FEEDBACK`、`noAction/noActionReason/settled`；重写 `bridgeFromDirectChat`（落定会话不再捕获新变更，自动新建基于最新 canonical 的会话；开放方案才并入反馈，后端判 SETTLED/STALE 时自动回退新建）；新增 `_startFreshSession`；`loadExecution` 在执行终态后释放会话并消费排队请求（修“队列从不消费”）；`restoreActive` 只恢复构建中/待审/待决/待执行/执行中的会话，不再领养 blocked/no_action/已完成；导出新状态。
- `src/store/agentUi.ts` —— Direct Chat 会话改为按 `(project, agent)` 长存（切换页面 context/agent 不再清空历史；context 仅作 per-turn 变更范围元数据）；watch `no_action` 把诚实结论补进对话流。
- `src/views/agents/index.vue` —— Agent Center 默认 project 级长存会话（不再硬编码 `scene/s01` 碎片化历史）；no_action 不显示 chip/不弹审查卡，异步到达时补诚实回复；状态文案加“无需改动”。
- `src/components/agent/TeamChatPanel.vue` —— no_action/落定会话不再渲染 ChangePlanReview。

**测试契约更新（1）**
- `tests/test_ai_7b_3_analysis.py` —— `test_coverage_failure_blocked`（断言旧的“空提案→blocked/PLAN_REQUEST_NOT_COVERED”，正是 §10 禁止的伪造 A/B 行为）改为 `test_coverage_empty_terminates_no_action`，断言诚实的 no_action 终态。

---

## 2. Conversation 持久化设计

- 一个可见 Agent 在一个项目里有**一个长存 Direct Conversation**（`conversation_id = conv_*`，稳定）。会话文件仍是 `output/agents/sessions/{session_id}.json`，复用既有 `AgentSessionStore` 与原子写。
- 页面选中的 Scene/Shot/Character 是 **per-turn 上下文元数据**（用于 ChangeRequest.entity_refs / 变更范围定位），不再是会话身份键——切页面/F5/重开都不丢历史（§9）。
- 消息有服务端生成的 `message_id`，并带 `linked_change_session_id / linked_execution_id / linked_work_ids`；变更事件通过 `append_session_message` 写回**耐久转录**（不再只在前端合成、F5 即丢）。
- 旧会话自动迁移：最近一条同 agent 的 project 级旧会话被赋予 conversation_id 并回填消息 id，历史保留。

**Conversation 与 ChangeSession 的区别**：Conversation 长存、append-only、是上下文；ChangeSession 是一次有始有终的变更事务，落定（approved/blocked/no_action/…）后成为不可变历史，不再锁定对话、不再是后续编辑的隐式目标。

---

## 3. 基础记忆设计

- **对话记忆（§10.1）**：即上面的长存转录 + linked ids；近期历史原本就注入上下文，现已按 conversation 连续。
- **共享工作记忆（§10.2）**：`WorkMemoryStore`，结构化条目（type=task/change/collaboration/decision/completion/blocker；summary/participants/references/affected_domains/entities/status）。写入点：变更 no_action 时、canonical 提交完成时（execution_service）。R4 接入真实 CollaborationTask 后会更丰富。**不是原始聊天**，不存隐藏推理。
- **偏好记忆（§10.3）**：`PreferenceStore`，仅在消息命中保守长期信号（“以后/默认/总是/不要自动…”等）且非问句时记录；一次性请求不记。偏好是建议性，渲染时明确标注“当前指令与项目真值优先”。
- 真值优先级（§11）在上下文装配中体现：canonical facts 在 system prompt 前部，工作记忆/偏好作为附加章节且声明从属于 canonical 与当前指令。没有引入向量库/RAG/人格学习。

---

## 4. 根因 #1 是怎么修的（bridgeFromDirectChat / sessionAlive / 落定 / blocked）

后端：
- 新增 `SESSION_SETTLED`（含 `approved_for_execution` 与 `blocked`）。`request_changes` 对**已落定/blocked** 会话直接抛 `CHANGE_SESSION_SETTLED`——新变更必须新建会话，杜绝“灌进旧会话→PLAN_SOURCE_STALE→吞消息”。
- 对**仍开放**（构建中/待审/待决）的方案，`request_changes` 现在：① 把旧 completed 提案标 `superseded`（`_has_completed_proposal` 不再跳过 → 下一轮 resume **真重跑 agent**）；② 反馈写入 `session.latest_feedback`，并拼入分类与各 proposal LLM 的请求文本——反馈真正到达 agent（修 D2 缺陷 a）。`original_user_request` 仍不可变，但反馈不再被它挡住。
- 0-item 提案：所有 specialist 都无可执行项且无真实待决问题 → 新终态 `no_action`（带诚实原因，写回来源对话+工作记忆），**不再伪造 A/B 方案**（§10，修 D2 缺陷 c）。character 的改名等真实歧义仍用合法 `user_decisions_needed`，保留。

前端（拱心石）：
- `bridgeFromDirectChat` 把“会话是否活着”从旧的“非 rejected/stale/failed/cancelled 即活”改为：**只有开放构建/审查中的会话才接收反馈**；approved+执行已完成、blocked、no_action 等一律走 `_startFreshSession`（清旧引用→基于最新 canonical 新建）。`requestChanges` 若收到 SETTLED/STALE 也自动回退新建。
- 执行终态后 `loadExecution` 自动释放会话引用，并消费之前排队的变更请求（修“queued changes 静默腐烂”）。
- `restoreActive`（F5/重开）只恢复在途/待执行/执行中的会话；blocked、no_action、已完成的 approved 不再被重新领养来捕获新消息（修 D2 缺陷 b 的 F5 复活）。
- 结果：**A→完成→B→完成→C 三次连续修改无需 Cancel、不复用旧 plan、不撞 PLAN_SOURCE_STALE**（§54 场景）。

---

## 5. 第二次/第三次变更如何基于最新 canonical

`create_session` 本就捕获 `_current_signature()` 作为 base 快照。R1 让前端在旧会话落定后**总是**新建会话，因此 B/C 的 base 自然是执行提交后的最新 canonical 签名与内容；旧 S1 的快照不会被复用。后端 stale 检测保留不动（仍是安全闸），但正常连续变更不再触发它。

---

## 6. 如何阻止 0-item 伪造计划

- `script_proposal` 不再合成“剧本无需改动”决策；空 items 就是诚实的 completed 空提案。
- service 在所有提案都空且无 answered decision 时终态化为 `no_action`（`_terminate_no_action`），记录原因、写回对话、写工作记忆。
- 前端不渲染审查卡/不弹弹窗，而是自然显示“我核对了当前项目：……无需产生实际修改。”
- plan_validator 的 `PLAN_REQUEST_NOT_COVERED` 保留作为深度防御（若有非空计划绕过），但不再是用户可见的阻塞/A/B 来源。

---

## 7. 测试结果

- R1 新测试 `tests/test_ai_7d_r1_conversation_memory.py`：**12 passed**（长存会话稳定性、消息 id/持久化、跨“重启”存活、工作记忆增查/项目隔离、偏好保守捕获/一次性不记、落定/blocked 拒绝反馈、0-item→no_action、开放反馈真 supersede+回 routing、连续变更新建会话不撞 stale）。
- 联合运行 R1 + D1/D2/D3 + 7B-3：**47 passed**。
- 全量后端 `pytest tests/`：**559 passed, 1 failed**。那 1 个 failed 是本次**有意的契约更新**（旧的空提案→blocked 断言已改名/改断言）；该测试在我改名后已单独跑通 8/8——全量跑时它还是旧名是因为全量任务在我改名前就启动了。
- 前端：`vue-tsc --noEmit` 在所有 R1 改动文件 **0 错误**；`npm run build` 成功。剩余 TS 错误全是 CLAUDE.md 已记录的既有问题（`store/index.ts` 4 个、`CreativeWorkspaceStudio.vue`、`ShotCard.vue`、node_modules 类型声明），非本阶段引入。

---

## 8. 已知限制 / 明确留给后续阶段（不偷跑）

按 §14，以下**有意不在 R1 做**：
- **根因 #2（页面 canonical 刷新/F5）未修**——改完后 Script/Character/Storyboard 页面仍需刷新才看到新值；这是 R3 的事件/失效工作。R1 修的是对话/变更流本身。
- 语义 pending-decision（approve/revise/reject/question/new_intent 的完整语义裁决）在 **R2**；R1 只保证“连续新变更”与“对开放方案的反馈真重跑”。
- 真实多 agent CollaborationTask、AI-only Team Chat 改造在 **R4**；Team Chat 仍有旧的用户消息后端端点（前端已无输入框）。
- Producer 跨域写权限、Level-1 直接编辑路径、AI-6D 旁路统一在 **R2/R3/R5**（AI-6D 按你的决定保留入口、执行权统一，记录为锁定需求；当前未在 R1 触碰，避免提前大改）。
- 自主工作计划/进度账本/Playbook、Director 场景子 agent、运行时自检在 R5/R6/R7。
- D2 测试此前只覆盖幂等；R1 新增了生命周期/反馈/no_action 覆盖，但 ChangeSession 的完整浏览器多轮验收留 R8。

---

## 9. 手测指令（§54，请你验证）

> 后端需重启（改了 `src/agents/*`、`routers` 未改但 agent 代码改了）；前端 Vite 热更新即可。
> 建议用一个有剧本/角色/分镜的项目，对 Producer 或 Script Writer 直聊：

1. `请把剧本稍微写长一点。` → 出现方案卡（真实提案）；批准并等执行完成（或先测 creative_only 类请求）。
2. 完成后直接发：`还是短，再补一点，但别加战斗。` → **不应**出现 PLAN_SOURCE_STALE/报错/要求 Cancel；应自动开一个新的变更会话，基于当前版本。
3. 再发：`刚才新加的最后一段换成角色之间的对话。` → 同一会话连续，理解“刚才新加的最后一段”指代。
4. F5 后重开同一个 Agent → 历史消息还在；刚完成的旧变更不会弹审查卡、不会挡住新消息。
5. 发一条模糊/无需改动的请求（如对已经满足的内容说“这段不用动”或含糊的“随便看看”）→ 如确无改动，应诚实显示“无需改动”，**不**出现 A/B 假方案。
6. 若执行中发新变更 → 仍出现“等当前完成 / 停止重做”二选一（这是有意保留的安全门）。

请重点看：**连续三次修改是否顺畅、有没有 stale/取消、上下文是否连续、F5 后是否还认得你**。

发现任何不顺，请告诉我是实现 bug、架构问题、还是预期限制——我会先修好当前 Gate 再继续（§81）。
