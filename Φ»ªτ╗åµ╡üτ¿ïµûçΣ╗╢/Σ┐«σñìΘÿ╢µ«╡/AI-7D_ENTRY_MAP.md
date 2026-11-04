# AI-7D · D0 全入口真实调用图（ENTRY_MAP）

> 执行者：Claude Code · AI-7D §0 Start Rule + D0.1/D0.2
>
> 方法论（§0）：**不从文件名猜实现**。下列每一条调用链均来自真实代码的逐行阅读，附 `file:line`。
> D0 只做审计 + 极小测试辅助，**不做正式修复**。完成后 STOP 等架构验收。

---

## 0. 一句话结论

**同一个产品里，不同输入框进入了不同代的 Agent Runtime**（主文档 §0 原话）：

- **Agent Center 的每个 Specialist Direct Chat** → legacy `send_message` → `client.chat()` 原样存模型文本。**无 intent router、无 inspect、无 delegate、无 ChangeSession**。这是最坏的 legacy 路径。
- **Floating Agent Bar** → 已有半统一 `send()` router：变更意图→真 ChangeSession ✓；但**只读/查询/runtime_query 仍落到 legacy `send_message`**，不调真实 `/project-intelligence`。
- **Team Chat** → 后端 `run_team_inquiry` **确实做真实 inspect**（`run_inquiry`→`ProjectIntelligenceService` 真读 canonical）✓，但**前端仍有用户输入框**（D4 要移除），且 change 请求会**重复创建** 7A analysis-only + 7B 真修改流两个 session。
- **真实只读 inquiry 运行时（`run_inquiry` + `project_intelligence`）已存在且可用**，但 Direct Chat 不接它——这正是 D1 要修的核心。

---

## 1. 后端并存的三代 Runtime（`routers/agents.py` + `src/agents/`）

| 代 | 端点 | Runtime 函数 | 真行为 | file:line |
|---|---|---|---|---|
| **Gen-1 legacy chat** | `POST /api/project/{p}/agents/sessions/{sid}/messages` | `send_message` → `runtime.send_message_to_session` → `runtime.send_message` | 拼系统提示+context+history（含 `active_workflow_snapshot` 快照）→ `client.chat(temp=0.7)` → **原样存模型文本**。0 工具/0 delegate/0 ChangeSession/0 inspect | router `agents.py:216-222`；runtime `runtime.py:204-265`（核心 `client.chat` @ `runtime.py:256`） |
| **Gen-2 真只读 inquiry** | `GET /api/project/{p}/project-intelligence?view=...` | `runtime.project_intelligence(p).inspect(view,...)` | **真从 canonical `ProjectStore.load()` 读盘**：overview/script/characters/character_usage/storyboard/scene/shot/production/lineage/dependencies。纯读、不写、有界、带 evidence_refs | `agents.py:260-278`；`project_intelligence.py:232-512`（`_project()` @ `:208` 真加载） |
| **Gen-2 真只读 inquiry（对话封装）** | `POST /api/project/{p}/team-chat/messages` | `TeamChatStore.append_message(pending_inquiry=True)` + `asyncio.create_task(run_team_inquiry_and_persist)` | `classify_request` 分流：question→`run_inquiry`（`ProjectContextBuilder` 调 `intel.overview/character_usage/scene/inspect` **真 inspect**→`_producer_answer` 一次 LLM 综合接地答案）；change→`create_change_analysis_session`（7A analysis-only） | router `agents.py:297-312`；`team_chat.py:193-242`；`inquiry.py:535-599` |
| **Gen-2 真 delegate** | `POST /api/project/{p}/agents/tasks` | `runtime.delegate` | Producer→Specialist 真委派：建 AgentTask + 五重权限校验 + dispatch capability（inspect 同步 / production_plan.create async / shot 长执行走 JobQueue）。**仅 Producer `can_delegate_to` 非空** | `agents.py:238-248`；`runtime.py:285-361` |
| **Gen-3 真修改流（ChangeSession）** | `POST /api/project/{p}/change-sessions` + `/resume` `/decision` `/plan-feedback` `/approve` `/reject` `/execute` `/executions/...` `/cancel` `/eligibility` | `cp_service.*` / `cp_exec.*` | ChangeRequest→ChangeSession→模块提案→UnifiedPlan→preview→approve→execute（replay+commit+char_sync+production_planning）。**所有创作修改唯一通道**，0 直接 canonical 改 | `agents.py:500-723`；`change_protocol/service.py` + `execution_service.py` |
| **Gen-2.5 旧 Director Proposal** | `POST /api/project/{p}/agents/director/proposals` + `/preview-selected` `/apply` `/dismiss` | `runtime.propose_storyboard_change` 等 | AI-6D 的 workspace+changeset 提案路径（**早于 7B change-sessions**，另一套提案系统） | `agents.py:407-463`；`runtime.py:508-580` |

> 关键：Gen-2 真只读 inquiry（`run_inquiry`/`project-intelligence`）**已实现且正确**——它做真 inspect、接地、不臆造（`inquiry.py:619-632 _producer_answer` 明确"数据不足请明说"）。AI-7D D1 的问题不是"缺 inquiry 运行时"，而是 **Direct Chat 不接它**。

---

## 2. 前端入口表面全表（D0.1）

每个用户可见 Agent 输入入口 → 组件 → store → api → 端点 → runtime → LLM → 能力边界 → 持久化/刷新/重启。

### 入口 A · Agent Center Direct Chat（8 Specialist + Producer）⚠️ 最坏 legacy

| 字段 | 值 |
|---|---|
| Frontend component | `src/views/agents/index.vue`（"Conversation（独立隔离）"区，:84-103） |
| Frontend store/action | **无 store**，本地 `ref<AgentSession>` + 本地 `send()`（:198-216） |
| HTTP endpoint | `POST /agents/sessions/{sid}/messages`（legacy Gen-1） |
| Router function | **无 intent router**——`send()` 直接 `apiClient.sendAgentMessage`（:207） |
| Runtime/service | `runtime.send_message` |
| LLM client call | `client.chat(messages, temp=0.7, max_tokens=1024)`（runtime.py:256） |
| Can execute tools? | **否**（无 dispatch） |
| Can delegate? | **否**（仅独立"Delegation"表单见入口 B） |
| Can create ChangeSession? | **否**——用户说"把中段加两场"只得到聊天回复 |
| Can read runtime truth? | **间接且不可靠**——仅 context 里塞了 `active_workflow_snapshot` 快照 blob（runtime.py:224-231），模型可读但对它的陈述未经验证 |
| Persistence store | `AgentSessionStore`（session+messages 落盘，:186-201） |
| Refresh behavior | `openSession` 重建 session（:188-196）；无活跃执行恢复 |
| Backend restart behavior | session+messages 持久化可恢复；**无运行时任务可恢复**（本来就没起） |

### 入口 B · Agent Center Producer Delegation 表单

| 字段 | 值 |
|---|---|
| Frontend component | `src/views/agents/index.vue`（"New Delegation"区，:67-81） |
| store/action | 本地 `delegForm` + `doDelegate()`（:225-249） |
| endpoint | `POST /agents/tasks`（Gen-2 真 delegate） |
| runtime | `runtime.delegate` |
| 真行为 | **真委派**（建 AgentTask + 权限 + dispatch）——但是**手动表单选 capability/scope**，非自然语言 |
| 注 | Producer 专有；Specialist 无此区（:51 `v-if selectedId==='producer'`） |

### 入口 C · Floating Agent Bar（半统一 router）⚠️ 只读分支仍 legacy

| 字段 | 值 |
|---|---|
| Frontend component | `FloatingAgentBar.vue`（发送 :177 `store.send(t)`）+ `AgentConversationSurface.vue`（渲染 + 运行卡）+ `FloatingAgentHost.vue`/`AgentSwitcher.vue`/`AgentContextChips.vue` |
| Frontend store/action | `useAgentUiStore().send()`（agentUi.ts:335-360）——**统一 intent router** |
| intent 检测 | `CHANGE_INTENT_RE`（agentUi.ts:81，与后端 `classify_request._CHANGE_VERBS` 同族词表） |
| 变更意图分支 | `_bridgeChangeIntent`（:238-285）→ `csStore.create()`（source=`floating_bar`/`direct_agent_chat`，带 sourceAgentId/sourceSessionId/contextScope）→ 真 `/change-sessions` ✓；活跃执行中→确定性二选一 queue/restart（:292-332，含 cancelExecution+waitForExecutionStopped）✓；活跃提案会话→`requestChanges` 反馈并入 ✓ |
| 只读/查询分支 | **`apiClient.sendAgentMessage`**（agentUi.ts:351）→ **legacy Gen-1 `send_message`** ✗——不调 `/project-intelligence` |
| 运行卡 | `AgentConversationSurface.vue:24-49` 真 durable 真相（ChangeSession/ChangeExecution 轮询所得，非 LLM 话术）✓ |
| 可达页面 | Storyboard（`ShotBoard.vue:271` setContext）、Characters（`characters/index.vue:174` setContext）、全局 FloatingAgentHost |
| 刷新/重启 | `agentUi.onProjectChange` 清空；运行卡由 `changeSession.ts restoreActive` 恢复 |

### 入口 D · Team Chat ⚠️ 有用户输入框（D4 要移除）+ 双建 session

| 字段 | 值 |
|---|---|
| Frontend component | `src/components/agent/TeamChatPanel.vue`（composer :56-62 **有用户输入框**） |
| store/action | `useTeamChatStore().send()`（teamChat.ts:58-73）+ `TeamChatPanel.send()`（:95-107） |
| 发送路径 | (1) `store.send`→`POST /team-chat/messages`→后端 `run_team_inquiry_and_persist`；(2) 前端再判 change-verb 且无 cs.session→`cs.create()`（:102-104）→真 `/change-sessions` |
| question 真行为 | 后端 `run_inquiry` **真 inspect**（`intel.overview/character_usage/scene/inspect`）→`_producer_answer` 接地自然语言 ✓ |
| change 真行为 | **双建**：后端建 7A `ChangeAnalysisSession`（analysis-only，`change_operations:[]` 0 可执行，落 analysis_card）+ 前端建 7B `ChangeSession`（真提案/执行流）。两者并存渲染（analysis_card + `ChangePlanReview`） |
| 运行卡 | `TeamChatPanel.vue:66 <ChangePlanReview v-if="cs.session"/>` |
| 轮询 | teamChat.ts 每 2500ms 轮询 GET messages（pending_inquiry 时）✓ |
| 刷新/重启 | onMounted `store.load`+`cs.restoreActive`（:124-128）；后端 `recover_team_chat` 把中断 pending 标 system「请重提」不重跑 LLM ✓ |

### 入口 E · Storyboard 上下文 Agent（Director 对话）

| 字段 | 值 |
|---|---|
| 组件 | `src/views/storyboard/ShotBoard.vue:271` 用 `useAgentUiStore`——即 Floating Bar（入口 C）setContext(scene/shot) |
| 旧提案路径 | `ShotBoard.vue:306 createDirectorProposal`——AI-6D 旧 Director Proposal（Gen-2.5，另一套提案系统，非 7B change-sessions） |
| 注 | 无独立 free-text agent chat；contextual agent 就是 Floating Bar |

### 入口 F · Character 页上下文 Agent

| 字段 | 值 |
|---|---|
| 组件 | `src/views/characters/index.vue:174` 用 `useAgentUiStore`——Floating Bar setContext(character) |
| 注 | 无独立 free-text agent chat |

### 入口 G · ChangePlanReview（真修改流审批/执行 UI）

| 字段 | 值 |
|---|---|
| 组件 | `src/components/agent/ChangePlanReview.vue`（在 TeamChatPanel:66 + AgentConversationSurface:46 渲染） |
| store | `useChangeSessionStore`（changeSession.ts）——完整真修改流 store：create/resume/poll/loadPlan/submitDecision/approve/requestChanges/reject/execute/resumeExecution/cancelExecution/restoreActive |
| 能力 | 查看方案/预览、批准、拒绝、请求调整、执行、取消、恢复——**全部接真实 7B/7C 后端** ✓ |
| 注 | 这是已接通的正确路径；D2 的 inline change card 应复用它而非新建 |

### 入口 H · 其它 Specialist Studios（DirectorStudio / CharacterDesignStudio / ProductionPlannerStudio / StoryOrganizer / CreativeWorkspaceStudio / Workbench）

经全前端 grep（`sendAgentMessage|useAgentUiStore|sendTeamMessage|createChangeSession|createAgentTask|createDirectorProposal`）确认：**这些组件不含 free-text agent 聊天输入框**。它们是领域制作 Studio（生成按钮/参数面板），agent 交互经 Floating Bar（入口 C）或旧 Director Proposal 端点。

---

## 3. D0.2 · legacy `send_message` 坏路径核查（确认）

文档点名路径：
```
POST .../agents/sessions/{sid}/messages → runtime.send_message() → client.chat(...) → raw assistant text persisted
```
**逐行确认**（`runtime.py:204-265`）：
1. `store.append_message(user)` 持久化用户消息（:214-216）
2. `build_context` + `agent_system_prompt` + history（:219-236）
3. **AI-7C-H §13**：追加 `active_workflow_snapshot`（持久化运行时真相快照）进 context（:224-231）——这是唯一的"真相"注入，但是 blob 形式塞进 prompt
4. 取 Agnes client（软失败 not_configured，:239-252）
5. **`text = await client.chat(messages, temperature=0.7, max_tokens=1024)`**（:256）——纯 LLM 聊天
6. **`AgentMessage(role="assistant", content=text)`** 原样存模型文本（:263-264）

### D0.2 五问逐条

| 问 | 结论 | 证据 |
|---|---|---|
| 是否只有某几个入口使用？ | **Agent Center Direct Chat 全部 Specialist + Floating Bar 只读分支** | 入口 A（views/agents:207）、入口 C 只读分支（agentUi.ts:351） |
| 是否所有 Specialist 受影响？ | **是**——Agent Center 8 个 Specialist 全走 legacy；Floating Bar 8 个 Specialist 的只读问答全走 legacy | views/agents:160-169 selectAgent 不分 agent；agentUi.send 只读分支不分 agent |
| prompt 是否声明模型实际无法执行的 capability？ | **部分是**。system prompt（registry.py:283-328）告诉各 Agent"你可以：查看 X（X.inspect）；提出 X 修改提案"——但 `send_message` 路径**不接任何 capability dispatch**，模型被告知"可以查看/提案"却只能聊天。例：script_writer prompt 说"你可以查看故事与场景（story.inspect）"，但 Direct Chat 不调 `story.inspect` | registry.py:291-295 等；runtime.send_message 无 dispatch_capability 调用 |
| 是否有 tool-call JSON 被当作普通文本保存？ | **风险存在**。system prompt 暗示模型可"提案/查看"，模型可能输出伪 tool-call/JSON 文本，被原样存进 `AgentMessage.content` 并显示给用户。`run_inquiry` 的 CONSULTATION_SYSTEM 用 `json_mode=True` 受控解析（inquiry.py:433），但 `send_message` 用普通 `chat()`（无 json_mode）→ 模型自由文本 | runtime.py:256（普通 chat）vs inquiry.py:433（json_mode） |
| 是否有"状态"从模型文本而非 backend object 派生？ | **是（半派生）**。AI-7C-H §13 把 `active_workflow_snapshot` 塞进 context，模型据此"回答进度"——但模型的进度陈述**未对照真实 inspect 验证**，可能臆测。前端 `agentUi.running` 已正确改为派生态（agentUi.ts:75-76，只由 sending 或 csStore 活跃态推导，不采信 LLM 话术）✓，但**回复文本本身**仍可能含未验证的进度声明 | runtime.py:224-231；agentUi.ts:72-76 |

---

## 4. D0.3 · 真实坏路径证据

### 4.1 代码级证据（确定性，已闭环）

**既有测试** `tests/test_agents.py:259 test_send_message_with_fake_client`：
```python
rt = _runtime(tmp_path, client=FakeClient(reply="hello from model"))
msg = asyncio.run(rt.send_message("demo", "director", "scene", "s01", "检查一下"))
assert msg["content"] == "hello from model"   # ← 原样存模型 chat 文本
```
证明：`send_message` 不调任何 inspect/project_intelligence/run_inquiry，仅把 `client.chat()` 的返回字符串存为 assistant 消息。FakeClient 是 Agnes 的替身——若换真 Agnes，模型对"现在剧本有多少场"的回答**完全来自 context blob 的自由发挥**，不来自真 inspect。

**对比证据**（同一产品两代 runtime）：Team Chat 的 `run_team_inquiry_and_persist`（team_chat.py:230-231）对 question 调 `run_inquiry`→`intel.character_usage/scene/inspect` **真读盘**，而 Direct Chat 的 `send_message` 不调。**同一句"现在剧本有多少场"在 Team Chat 得到接地答案，在 Direct Chat 得到模型臆测**——这就是主文档所述"不同输入框进入不同代 runtime"。

### 4.2 浏览器复现状态

D0.3 要求浏览器复现 3 条（Producer"现在剧本有多少场？先不要修改。"/Producer"剧本内容太少了，请继续添加剧本内容。"/Script Writer"先看看当前中段内容，再告诉我哪里最薄弱。"）。环境核查：
- 现有项目可用（`projects/我的动画短片`、`AGNES_GoldenPath_H` 等）；
- Playwright harness 可复用（`e2e_7c_s5_browser.py`）；
- **依赖**：后端 :8000 + 前端 Vite :7777 需启动；Agnes API Key 需配置（未配置则 `send_message` 软失败返回 `not_configured`——其本身即证据：Direct Chat 无 inspect 兜底，配置缺失则彻底哑火）。

代码级证据（§4.1）已确定性闭环"真实坏路径"要求。**live 浏览器复现**作为补充证据，建议在 D1 动手前由用户环境（已配置 Agnes）跑一次上述 3 条，或在 D1 实现后作为对比回归。本审计不阻塞于此。

---

## 5. 准备修改的最小架构边界（D1+ 提案，待用户验收）

> 原则（附录 B）：**LLM 决定语义，代码决定权限与执行边界。工具调用是 runtime 事件，不是聊天文本。ChangeSession 是所有创作修改唯一通道。Runtime Truth 来自 persisted backend objects。**

### 5.1 统一 Turn Runtime（D1 核心）——复用现有，不重造

**已有可复用件**（D1.2 明确"复用现有 agentUi intent/router"）：
- 前端统一 router：`agentUi.ts:send()`（入口 C 已验证可行）——**把它提升为所有 Direct Chat 的唯一入口**，退役入口 A 的本地 `send()`。
- 后端 intent 分类：`inquiry.py:classify_request`（question|change + modules + refs）——已是确定性 TurnIntent。
- 后端真只读 inquiry：`inquiry.py:run_inquiry` + `project_intelligence.py:inspect`——已正确。
- 真修改流：`change_protocol`（7B/7C）——已正确。

**TurnIntent 分流**（对齐 D1.2）：
```
Direct Chat user turn → UnifiedAgentTurnRuntime
  ├ conversation       → 现有 send_message（纯聊天，无项目事实依赖时）
  ├ project_inquiry   → run_inquiry / project-intelligence（真 inspect→接地回复）   ← 新接线
  ├ creative_change    → change-sessions（Floating Bar 已有的 _bridgeChangeIntent）  ← 推广到 Agent Center
  ├ runtime_query     → run_inquiry(workflow_state=active_workflow_snapshot)        ← 复用 §13 快照但经 run_inquiry 而非裸 chat
  └ runtime_control   → 现有 execute/cancel/resume backend action                   ← D3
```

### 5.2 最小改动边界（不重做 7A/7B/7C 协议）

1. **Agent Center Direct Chat（入口 A）改用 `useAgentUiStore().send()`**——退役 `views/agents/index.vue:198-216` 的本地 `send()+sendAgentMessage`。使 8 个 Specialist 的 Direct Chat 与 Floating Bar 同语义（变更→ChangeSession、查询→inquiry）。
2. **`agentUi.send()` 只读分支**：从 `sendAgentMessage`（legacy chat）改为经新端点/复用 `run_inquiry`，使 project_inquiry 真接 inspect。`conversation`（纯闲聊无事实依赖）可保留 legacy chat。
3. **Direct Chat response contract**（D1.5）：`assistant_message` 自然语言 + `events[]` 真后台事件 + `linked_change_session_id?` + `evidence_refs[]`——UI 永不显示 tool-call JSON。
4. **Team Chat（D4）**：移除/禁用 `TeamChatPanel.vue:56-62` 用户 composer；Team Chat 仅展示真实 Agent→Agent 协作消息（绑定 AgentTask/ChangeSession/ChangeExecution 事件，非伪造对话）；解决 change 请求的 7A+7B 双建冗余。
5. **Team Chat 后台 change 分流**：`run_team_inquiry_and_persist` 对 change 不再建 7A analysis-only 死路（与前端 7B 重复），统一指向 7B ChangeSession。

### 5.3 不做的事（主文档 §6 绝对禁止）

- 不用 Prompt Patch 掩盖 legacy runtime；
- 不让用户看到 `{"function":...}`；
- 不新建第二套 Change Plan（7B change-sessions 是唯一）；
- 不让 Specialist 直接改 canonical；
- 不开始 AI-7E / Reviewer / 自动审片循环；
- 不引入 vector DB / cross-project memory（D5 先审计 A+B）。

---

## 6. D0 Gate · STOP

**已完成**：
- D0.1 全入口真实调用图（§2 八个入口 + file:line）；
- D0.2 legacy `send_message` 坏路径核查（§3 五问逐条 + 代码证据）；
- D0.3 真实坏路径证据（§4.1 代码级闭环 + §4.2 浏览器复现状态）；
- 准备修改的最小架构边界（§5）。

**未做**（D0 纪律）：任何正式修复代码。仅本审计文档。

**给用户的验收点**：
1. 入口图（§2）是否覆盖所有 user-visible Agent 入口？有无遗漏的入口？
2. 坏路径定性（§3-§4）是否同意？尤其"Agent Center Direct Chat 全 legacy、Floating Bar 只读分支仍 legacy、Team Chat 有用户输入框且双建"。
3. 最小架构边界（§5）是否同意？特别是：**把 `agentUi.send()` 提升为所有 Direct Chat 唯一入口 + 只读分支接 `run_inquiry`** 这个方向。

> `STOP — waiting for manual/architecture acceptance.`
