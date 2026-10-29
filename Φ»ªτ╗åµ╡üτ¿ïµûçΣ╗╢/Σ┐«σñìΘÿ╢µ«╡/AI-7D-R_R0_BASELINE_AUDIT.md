# AI-7D-R · R0 基线审计（Baseline & Dependency Audit）

> 阶段：R0（§51）——只读审计，不改代码。
> 审计基线：submodule HEAD `10f0d44`（AI-7C-H 收口）+ 工作区未提交的 D0–D3 改动。
> 审计日期：2026-08-24。审计方式：4 路只读 subagent 并行，全部结论以 `file:line` 落证，互相交叉验证。
> 结论一句话：**确定性内核完好且硬（mode 闸 / can_execute / applyable=False / 原子提交 / stale 检测 / JobQueue / 重启恢复全部是代码强制，LLM 无法绕过）；真正坏掉的是 Conversation↔ChangeSession 的边界、变更路由、UI 真值刷新、跨 agent 共享记忆，以及一个能绕过内核的 AI-6D 旧 apply 旁路。**

---

## 0. 仓库基线

- 外层仓库把 `animation-storyboard` 记为 submodule（gitlink），记录在 `bd6f98a`；内层实际 HEAD 已前进到 `10f0d44`（AI-7C-H cancel/restart 收口）——外层显示 `M animation-storyboard` 源于此。
- 内层工作区有一批未提交改动，即 D0–D3 产物：
  - 后端：`routers/agents.py`、`src/agents/{inquiry,models,registry,runtime}.py`、`src/agents/change_protocol/{models,service,store}.py`
  - 前端：`src/api/index.ts`、`src/components/agent/{ChangePlanReview,TeamChatPanel}.vue`、`src/store/{agentUi,changeSession}.ts`、`src/views/agents/index.vue`
- **无任何 V2 重复系统**（§1 禁用清单 `AgentRuntimeV2/ConversationV2/ChangeSystemV2/ProjectStoreV2/WorkflowEngineV2` 全库 0 命中）。
- 但存在 4 处“准重复”需在 R 阶段收敛：① AI-6D Director Proposal（可 apply 到 canonical）与 7B/7C ChangeSession 双提案系统；② 7A change_analysis 与 7B change-sessions 双分析系统；③ 同一条变更/创作意图正则散落 4 处（`agentUi.ts:81`、`index.vue:173`、`inquiry.py:75-79`、`changeSession.ts:420`）；④ 死代码（TeamChatPanel `send()`、`sendAgentMessage` 客户端方法零调用方）。

---

## 1. Agent 入口地图（§51 交付物）

| 入口表面 | 前端调用点 | 后端路径 | 现状 |
|---|---|---|---|
| Agent Center 直聊（Producer + 8 specialist） | `views/agents/index.vue:250-279` `send()` | 变更正则→`bridgeFromDirectChat`→`POST /change-sessions`；其余→`POST /agents/sessions/{sid}/turn` | **已统一（D1/D2）**，但变更路由在前端正则 |
| Floating Agent Bar（全局） | `components/FloatingAgentBar.vue:177`→`store/agentUi.ts:263-290` | 同上（change-regex 桥接 / `apiClient.agentTurn`） | **已统一（D1/D2）**，挂在 `layouts/MainLayout.vue:100` |
| 角色上下文 agent | `views/characters/index.vue:244` `setContext({type:'character'})` | 走 Floating Bar；character 上下文降级为 project scope（`agentUi.ts:45`） | 已统一 |
| 分镜上下文 agent（Director） | `views/storyboard/ShotBoard.vue:481-494` `setAgentContext('director',…)` | 走 Floating Bar | 已统一 |
| 分镜显式 Director 提案 | `ShotBoard.vue:306` `createDirectorProposal` | `POST /agents/director/proposals`→`runtime.propose_storyboard_change` | **旧 Gen-2.5 旁路，可 apply 到 canonical（须退役）** |
| 剧本上下文 agent | **不存在** | — | 仅靠全局 Floating Bar 覆盖（R2 须确保被统一 turn 覆盖，目前传递性地覆盖到） |
| Producer 直聊 | Agent Center 选 producer，同 `send()` | `/turn` | 已统一 |
| Producer 委派表单 | `index.vue:288-312` `doDelegate` | `POST /agents/tasks`→`runtime.delegate` | 已统一（真正的 agent→agent 委派记录） |
| Team Chat | `components/agent/TeamChatPanel.vue` | `GET /team-chat/thread`+轮询；后端 `run_team_inquiry_and_persist` | 输入框已删（D4 部分），后端 `POST /team-chat/messages` 仍活；变更仍建 7A 会话 |
| ChangePlanReview（审查+执行 UI） | `TeamChatPanel.vue:62` + Agent Center modal `index.vue:127-140` | `store/changeSession.ts`→`/change-sessions/*`+`/executions/*` | 已统一；blocked/proposal 阶段可拒绝 |

**与 §12 的关键差距**：后端 `/turn` 从不判定 `creative_change`——`classify_turn_intent` 把变更意图降级为 `conversation`（`inquiry.py:827-829`，注释“D2 bridges to ChangeSession”），变更拦截发生在**前端正则**且两份正则不一致（前端 `CHANGE_INTENT_RE` 是后端 `_CHANGE_VERBS` 的超集；“把中间的对白压缩一点”两边都匹配不上 → 变成盲聊）。R2 必须把变更语义裁决收进后端统一 Turn Runtime。

### 1.1 四可见 agent 映射（§3）

注册表 `src/agents/registry.py:31-103`（8 个固定 agent）：

| 现有 agent | 映射 | 依据 |
|---|---|---|
| `producer` | **Producer（可见）** | 唯一有 `can_delegate_to`（:41）；但当前**仅有 `project.inspect`，零创作写权限** |
| `script_writer` | **Script Writer（可见）** | `story.*` 能力 |
| `character_designer` | **Character Designer（可见）** | `character.*` 能力 |
| `director` | **Director（可见）** | `storyboard.*` |
| `production_planner` | **隐藏** | 生产是确定性下游，非创作模块（§4） |
| `shot_producer` | **隐藏** | 确定性执行层 |
| `continuity_reviewer` | **隐藏**（§3 的 Reviewer） | 可选 QA |
| `editor` | **隐藏** | §3 明示 |

**差距**：没有 `visible` 概念。当前只有 `enabled` 开关，`enabled=false` 会**硬阻断所有执行**（`runtime.py:127-128,194-195,289-290`），包括 Producer 内部委派。§3 要“隐藏但内部仍可调用”，需新增与 `enabled` 解耦的 `visible` 标志。两个切换器都无过滤地渲染全部 8 个（`AgentSwitcher.vue:7-18`、`index.vue:22-31`）。

---

## 2. 四生命周期地图（§51 交付物，§7 强制纠正）

| 生命周期 | 存储（file:line） | ID / 状态机 | 持久化？ | 区分/混淆 |
|---|---|---|---|---|
| **Conversation（直聊）** | `AgentSession`+`AgentMessage`：`models.py:216-249,186-211`；`sessions.py:25` 存 `output/agents/sessions/{sid}.json` | `sess_*`；按 `(agent_id,scope_type,scope_id)` 复用（`sessions.py:43-55`）；**无状态字段、无状态机**，仅 append-only `messages[]` | 是（原子 JSON，F5/重启存活） | 结构上独立，但**语义上与 ChangeSession 混淆**（见 §3） |
| **Agent Work / Run** | `AgentTask`：`models.py:287-368`、`tasks.py:25`（`output/agents/tasks/`）；提案运行=`ModuleProposal`；team inquiry=异步任务落 team thread | `task_*`：pending→running→completed/failed/cancelled（`models.py:19-24`）；`ModuleProposal`：pending/running/completed/failed/superseded | 是 | 独立（`execution_ref` 关联 Job/Run，`models.py:317`） |
| **ChangeSession** | `change_protocol/models.py:85-124`；`store.py:34` 存 `change_protocol/sessions/{cs_id}.json` | `cs_*`；14 态（`models.py:20-25`）；终态 `approved_for_execution/rejected/stale/failed/cancelled`（`models.py:26`）——**`blocked` 不是终态** | 是 | 对象独立，但**充当变更意图的事实上的对话**（§7 病根） |
| **ChangeExecution** | `execution_models.py:187-256`；`store.py:195` 存 `executions/{eid}.json` | pending/running/waiting_user/blocked/failed/cancelled/completed（`execution_models.py:18-22`）；11 阶段（:26-30） | 是 | 独立且划分正确（执行期间 session 保持 `approved_for_execution`；前端 `shouldPoll` 分离，`changeSession.ts:62-72`） |

**关键缺失**：没有 Turn 对象、没有 conversation 级 ID。`unified_turn` 在 `runtime.py:389` 生成 `turn_id` 但**从不持久化**。Conversation→ChangeSession 只有单向元数据 `ChangeRequest.source_session_id`（`models.py:53`），ChangeSession 生命周期事件**从不回写** AgentSession 转录（桥接回复是前端合成的本地数组，`changeSession.ts:422-424,446-448,462-464`，Floating Bar 上 F5 即丢）。

---

## 3. 会话/消息持久化现状（§9/§10.1）

- **持久化转录确实存在**：`AgentSession.messages[]`（`AgentMessage{role,content,status,created_at,meta}`），按 `(agent,scope)` 存，F5/重启存活；`meta` 带 `intent/evidence_refs/events/linked_change_session_id`（D1，`models.py:190-199`）。近期历史会自动进上下文（`runtime.py:226-228`，inquiry 路径经 `history` 参数 `inquiry.py:642-645,904-907`）。
- **但不是 §9 要的“Conversation”**：没有凌驾于 session 之上的 `conversation_id`，没有跨 agent 连续性，超出原始历史之外无记忆；Floating Bar 切 agent/上下文会清空本地 `messages`（`agentUi.ts:185-219`，服务端历史仍在）。
- **变更轮次绕过转录**：前端 `CHANGE_INTENT_RE` 在 `/turn` 之前拦截（`agentUi.ts:267-270`、`index.vue:261-267`），用户原文只存进 `ChangeRequest.original_user_request`（不可变，`models.py:58`），或塞进 `plan.user_feedback_message_id`（一个**名叫 `*_message_id` 实际存反馈原文**的字段，`service.py:760`）。漏过正则的变更话术被后端降级为盲聊。
- **§10.1 缺的一等字段**：`message_id / conversation_id / agent_id / linked_work_ids / linked_change_session_id / linked_execution_id` 未成为 message 一等字段（目前只在 unified_turn 的 assistant 消息 meta 里零星带 `linked_change_session_id`）。

---

## 4. 三域能力地图（§4/§5/§30）

存在**两套重叠的能力注册**：
1. `src/agents/capabilities.py`——运行时 `CapabilityDefinition` 目录（id/owner/allowed_agents/operations/risk/implemented），由 `CapabilityRegistry.can_execute`（`registry.py:178`，五重检查）闸控。
2. `src/agents/change_protocol/`——7B/7C 语义层（`ModuleProposalItem.action`→编译为 `ChangeOperation`），由 `AgentExecutionMode`（`modes.py`）闸控。

`AgentOp`：`INSPECT/EXECUTE/RETRY/CANCEL/PROPOSE`（`models.py:33-38`）。`AgentExecutionMode`：`READ_ONLY/PROPOSAL_ONLY/APPROVED_PLAN_EXECUTION`（`modes.py:20-28`）。硬闸：`READ_ONLY` 仅 `inspect`；`PROPOSAL_ONLY` 仅 `inspect,propose`；`APPROVED_PLAN_EXECUTION` 仅 `inspect`（执行引擎本身是非 agent 代码路径，`modes.py:33-34`）。

### 4.1 各域能力（摘要）

| 域 | 读 | 写（提案） | 写（直接执行） |
|---|---|---|---|
| Script | `story.inspect`（已接线 :167） | `story.propose_change`（真实 LLM，`script_proposal.py:168`，0 apply）；动作 `update_story_text/update_scene/insert_scene/delete_scene/reorder_scenes` | `story.edit` **桩**（`implemented=False`，永不写） |
| Character | `character.inspect`（:180） | `character.propose_change`（`character_proposal.py`；rename→`needs_user_decision`） | `character.design` 桩；`character.views.generate` **真实**（AI-7C，经 JobQueue，由 `execution_service.run_character_sync:996` 执行，**非 agent 直接派发**） |
| Storyboard | `storyboard.inspect`（:200） | `storyboard.propose_change`（PROPOSE→change_protocol）；动作 `update/insert/delete/reorder_shot` | `storyboard.propose_change` 的 **EXECUTE 操作 = AI-6D 旧旁路**（`runtime.py:821`，**可直接 apply 到 canonical**） |
| 生产（非创作域） | `production_plan/shot/composition.inspect` | — | `production_plan.create`、`shot.first_frame/last_frame/video/audio` **真实**（JobQueue）；`composition.compose` 桩 |

### 4.2 关键结论

- **change_protocol 路径硬闸完好**：`PlanPreviewArtifact.applyable` 在构造与 `from_dict` 都**强制 False**（`models.py:416,436`），revalidation 拒绝任何 `applyable is not False`（`execution_service.py:142-143`）。提案运行时只产语义 item；`assert_mode_allowed(PROPOSAL_ONLY, PROPOSE)` 是代码闸（`proposal_helpers.py:84`）。`commit_canonical`（`execution_service.py:493`）只在 `PlanApproval`+完整 revalidation 后经 AI-7C execute 端点可达。
- **但 change_protocol 之外存在真实副作用能力**：`production_plan.create`、`shot.*`、`character.views.generate` 都是 `implemented=True`，经 `runtime.delegate`/异步分支可达；外加 **AI-6D Director 旁路可 apply canonical**（第二个无防护变更路径）。
- **Producer 今天是纯路由器**：`allowed_capabilities=["project.inspect"]`、`write_scopes=["agent_task"]`（`registry.py:37-39`），不在任何域能力的 `allowed_agents` 里，系统提示词明示不能执行域操作（:288）。**这与 §5 目标（Producer 有三域写权限、不只是路由器）直接矛盾——授予 Producer 创作写是 R3 的核心能力扩张。**

---

## 5. 工作流清单（§20/§49）

| # | 工作流/控制器 | 位置 | 有序步骤 |
|---|---|---|---|
| W1 | 变更提案管线 | `service.py:573→281→416→519` | classify→并行三 specialist 提案→impact/conflicts→(Director 二轮)→compile→validate→preview |
| W2 | AI-7C 批准计划执行 | `execution_service.py:181→298→493→996→1086` | revalidate→workspace replay→commit canonical→char-sync→production planning→targeted run |
| W3 | ProjectProductionManager | `project_autopilot.py:407→628→851` | preflight→逐 scene 串行→compose |
| W4 | SceneAutopilotManager | `autopilot.py:126,208` | activate→first_frame→[last_frame]→video（每 shot） |
| W5 | FullProjectManager | `full_project_autopilot.py:680→736→1254` | inspect→逐 scene（Director draft→apply→Planner→approve→READY）→移交生产 |
| W6 | StoryWorkflow | `story_workflow.py:187-328` | LLM 故事→scenes→characters 草稿（创作分解，有状态机） |
| W7 | ProductionPlannerWorkflow | `production_planner.py:133,281,322` | LLM 出策略 + **确定性 sanitize/staleness 校验** |
| W8 | JobQueue | `job_queue.py:55` | 单视频 worker（1 RPM）+轻并发 3+watchdog+重启续轮询 |
| W9 | AI-7A ChangeAnalysis | `change_analysis.py:322` | classify→只读咨询→综合；`execution_ready` 恒 False |
| — | `src/pipeline/controller.py` | `:28-34` | **不是工作流**（阶段 5 重构后只剩配置/客户端工厂，忽略） |

---

## 6. 硬依赖分类（§49 强制审计）

### 6.1 HARD_DEPENDENCY（必须保留为内核，不许降级为“建议”）

- mode 闸 `assert_mode_allowed`（`modes.py:46`）
- `can_execute` 五重检查（`registry.py:178`）
- `applyable=False` 强制（`models.py:416,436`）
- 执行幂等（`execution_service.py:198`）+ operation digest（:49）
- 执行前 stale 检测（:228）+ 锁内再 stale（:572）
- `_revalidate_for_execution` 10 项检查（:105-177）
- 原子 canonical 提交 + COMMIT_INTENT/DONE write-ahead（:557-635）
- 提交对账 Cases A/B/C（:461-490，重启恢复不靠猜）
- 提交前 active-production preflight（:541）
- `_assert_agnes_prerequisites`（:975，未提交+manifest+解冻前 0 Agnes 调用）
- `compute_production_manifest` 确定性 op→asset 映射（:653，资产血缘/有界重生范围）
- 逐 character/scene 幂等跳过（:1032,1138，重启不重复生成）
- all-plan-before-production 闸（:1164）
- `start_run` 单活+冻结快照（`project_autopilot.py:427,441,453`）
- `preflight_project`/`preflight_scene_autopilot`（:266 / `autopilot.py:208`）
- 场景间 `_validate_source_for_scene`（:761）
- `dependency_ready`（`autopilot.py:137`，i2v 需要源 final_video+尾帧）
- 视频单 worker+1 RPM（`job_queue.py:65,202`）
- `recover_pending_videos`/`recover_executions`（`execution_service.py:1483`）
- 锁定 shot 保护（`plan_validator.py:132` / `autopilot.py:198`）

### 6.2 CREATIVE_HEURISTIC（应降级为 Agent 自决 / Playbook）

- 首轮 specialist 按 `classify_request` 关键词路由（`inquiry.py:268,274`；`service.py:297`）——“扩写”不必召唤 Director
- 脚本带 scene_id 就**自动**拉 Director 二轮（`service.py:390-402,466`）——盲拉，未按用户意图把关
- impact 的 “RECOMMENDED” 事实（`impact.py:122,126`，FRAME/VIDEO_REGENERATION_RECOMMENDED）——是“可能需要”，由 Agent 决定是否刷新
- `story_workflow` 故事→scenes→characters 分解（开放创作，Playbook）
- ProductionPlanner LLM 策略（LLM 提案，内核 sanitize 合法性）——LLM=Playbook/Agent，sanitize=Kernel
- `continuity_reviewer` 评审提案（可选 QA，Playbook）
- Full-project Director→Planner 自动序列（创作步骤降级 Playbook，检查点保留内核）

**§49 结论**：硬依赖与创作启发式的边界清晰，R5 把 W5/W6/W7 的创作部分转为 Playbook，检查点/闸保留内核。没有发现“把硬依赖错误降级为建议”的现有代码。

---

## 7. 记忆/持久化清单（§10/§51）

| Store | file:line | 存什么 | 作用域 | 持久化 | 判定 |
|---|---|---|---|---|---|
| ProjectStore | `core/store.py:102` | canonical `output/project.json` | project | 是 | 已有（真值源） |
| AgentSessionStore | `agents/sessions.py:19` | 直聊会话+完整转录 | project+agent+scope | 是 | **= 对话记忆实质存在，schema 不全** |
| AgentTaskStore | `agents/tasks.py:19` | 委派/咨询记录 | project | 是 | 已有，但**从不注入 agent 上下文** |
| TeamChatStore | `agents/team_chat.py:117` | 每项目一个 team thread | project | 是 | 已有（旧 Producer Q&A） |
| ChangeProtocolStore | `change_protocol/store.py:28` | requests/sessions/proposals/plans/previews/decisions/executions+审计快照 | project | 是 | 已有 |
| ChangeAnalysisStore | `change_analysis.py:153` | 7A 只读分析会话 | project | 是 | 已有（旧旁路） |
| ProposalStore（AI-6D） | `proposal_store.py:24` | Director 分镜提案 | project | 是 | 已有（旧旁路，待退役） |
| WorkspaceStore | `revision/workspace.py:24` | 创作修订 workspace+changeset | project | 是 | 已有 |
| 生产计划 | `routers/production_planner.py:13` | `output/ai/production_plans.json` | project | 是 | 已有 |
| JobQueue | `core/job_queue.py:62` | 生成 job | global | 是 | 已有 |
| AgentSettingsStore | `registry.py:228` | agent enable+model profile | global | 是 | 已有 |
| 角色库 | `routers/characters.py:466` | 全局角色库 | global | 是 | 已有 |
| 前端 localStorage | `store/index.ts:20` | 项目 identity+浮窗位置 | browser | 是 | UI-only |

### 7.1 §10 三层记忆判定

- **§10.1 对话记忆——实质存在，schema 不全**（见 §3）。
- **§10.2 共享工作记忆——完全缺失**。全库 grep `WorkMemory|work_memory|shared_memory` = 0 命中。最接近的 AgentTaskStore 有持久化但从不被任何 agent 上下文读取；`active_workflow_snapshot` 只含**非终态**会话/执行（`service.py:160-207`），已完成的协作不可见。
- **§10.3 显式偏好记忆——完全缺失**。grep `MemoryPreference|preference|偏好`（src/）= 0 命中。

### 7.2 上下文构建现状（§11/§39）

当前组装 = `静态系统提示词 + canonical 事实 + 自身 session 历史 + 活动工作流快照`（`context.py:16-81` 仅静态 canonical；inquiry `build_for_specialist/producer` `inquiry.py:301-376` 加 overview+scope+active workflows）。**没有偏好层、没有共享工作记忆层可排序**——§11 的真值优先级目前无法表达，要等 R1 把层建出来。

---

## 8. Team Chat 路径（§27）

- **前端输入框已移除**（未提交 D4）：`TeamChatPanel.vue:56-58` 换成 AI-only 提示。死代码残留：`send()`（:91-103）、`draft`（:79）、`placeholder`（:82）、`.composer` 样式（:156-159）；空态文案仍说“向 Producer 提项目级问题或变更请求”（:14）。
- **后端仍收用户消息**：`POST /api/project/{p}/team-chat/messages`（`routers/agents.py:305-320`）持久化用户 TeamMessage 并 spawn `run_team_inquiry_and_persist`；前端 `apiClient.sendTeamMessage`（`index.ts:1777`）与 `store/teamChat.ts:58` 仍在，只是面板不可达。
- **渲染内容**：持久化 TeamMessages（轮询）+ 活动 ChangeSession 时的 ChangePlanReview。写入者穷举：用户消息（:316-317）和 `run_team_inquiry_and_persist` 结果（Producer 问答“agent”消息 :233-234、变更 `analysis_card` :209-213、系统状态/失败 :215-220,182-185）。
- **没有真实 agent→agent 内容**：`runtime.delegate`（`runtime.py:598-674`）和 specialist 咨询（`inquiry.py:467-537`）**从不写 TeamChatStore**——没有委派/发现/交接/阻塞项被发布。也**没有伪造多 agent 对话**（好基线）。
- **每条消息的耐久引用：部分**。analysis_card/system 带 `source_change_session_id`（`team_chat.py:81-83,209-220`）；Producer 问答“agent”消息**无**源引用（:233-234）。
- **CollaborationTask 全库 0 命中**（§25 全新建设）。
- 另：Team Chat 变更消息仍**双建 7A+7B**（`team_chat.py:201-207` 建 7A；前端 `TeamChatPanel.vue:95-100` 建 7B，除非 cs.session 已存在）——两条分歧的变更管线都活着。

---

## 9. 运行时自检能力（§35–§38）

**已有（窄）**：
- `runtime_query` 意图（`inquiry.py:732-735` 正则 + `classify_turn_intent:809-842`）→ 把 `active_workflow_snapshot` 注入上下文包（`runtime.py:310-318`，`inquiry.py:566-568,887-888`），仅支撑“现在做到哪了/完成了吗”，且**只对非终态会话/执行**（`service.py:160-207`）。
- D3 `runtime_control`（`runtime.py:421-585`）：自然语言 stop/resume/approve/reject/replan→真实后端动作，回复基于真实后置状态。
- 静态系统提示词 + 真值纪律后缀（`registry.py:331-348`，明示不许声称未记录的动作）。

**缺失（§37/§38 差距，已证实）**：
- 无 agent **最近动作/最近完成工作/最近失败/最近能力调用**（状态/耗时/错误）——AgentTask 记录存在、可经 API 列出（`routers/agents.py:234-264`），但**无任何代码路径注入 agent 上下文**（`context.py:16-81` 仅静态 canonical；ProjectIntelligenceService 只用 task_store 算 `history` 计数，`project_intelligence.py:522-543`）。
- 无**最近协作**/“我为什么咨询 X”的回忆；活动工作流快照之外无 pending decision；无“当前已知限制”数据。
- 提示词里**无动态能力/权限快照**（§38）：能力注册与 `can_execute` 存在（`runtime.py:155-162`，`registry.py`）但从不渲染进任何系统提示词；`agent_system_prompt` 完全静态（`registry.py:340`）。

---

## 10. 四个根因（§51 明确要求，全部实锤到机制级）

### 根因 #1 —— 已完成的 ChangeSession 阻塞后续对话（§7/§8/§82）

**单一拱心石**：`bridgeFromDirectChat`（`changeSession.ts:441-450`）。`sessionAlive` = 会话存在且状态**不在** `['rejected','stale','failed','cancelled']`。于是 `approved_for_execution`（提案终态、执行已完成）、`blocked`、`ready_for_review`、`awaiting_user_decision` 都算“活着”，每个新变更意图都被路由到旧会话的 `requestChanges(text)`（:445），而新建会话分支（:451-460）在这些状态下**不可达**。

**§8 序列完整复现**：执行完成→canonical 提交（`execution_service.py:493+`）、创作签名变化（`revision/models.py:420-430`）→会话停在 `approved_for_execution`→用户“再扩写一点”→`sessionAlive=true`→`requestChanges`→后端 stale 守卫（`service.py:742-746`）发现 `cur_sig != source_snapshot_signature`→会话标 `stale` + `AgentError("PLAN_SOURCE_STALE")`→HTTP 409（`routers/agents.py:94`）→前端吞成错误提示（`changeSession.ts:341-343`），**这条消息被白吞**。且执行完成后的审查卡无释放按钮（`canReject` 排除 approved，`ChangePlanReview.vue:254-256`；done 后 cancel 禁用 :177）——唯一“释放”是献祭一条消息给 stale 错误或手动拒绝。

**三个 D2 缺陷机制**：
- **(a) feedback 不重调 agent**：`request_changes`（`service.py:735-766`）只做“作废旧计划+建空草稿 v+1（反馈原文塞进名不副实的 `user_feedback_message_id` :760）+状态回 routing :764”，**从不调 LLM**；随后 resume→`_run_first_round_proposals`（:281）→ `:297-299` **跳过所有已有首轮提案的 agent**（`_has_completed_proposal:239-244`）→0 协程→`_analyze_and_compile:416` 用**同一批旧提案**重编 v+2（分类用不可变的 `original_user_request:290-291`）。反馈从未被任何 agent 看见。
- **(b) blocked 不释放**：`blocked` 既不在后端 `SESSION_TERMINAL`（`models.py:26`）也不在前端任何集合（`changeSession.ts:16-29`）——**僵尸态**：轮询停了（`shouldPoll:66-72`）却每次 F5 被 `restoreActive:372` 重新领养，toolbar chip+审查弹窗反复弹（`index.vue:8-11,167-171`），快照里永远挂为活动工作流（`service.py:197-203`），新变更继续灌入并重新导出同样 blocked（如“无 completed 提案可编译” :436-441）。有手动“拒绝”逃生口（`ChangePlanReview.vue:19,254-256`），但无自动释放；语音“停止”对 blocked 只回“尚未进入执行…如要放弃可以告诉我拒绝方案”（`runtime.py:484-489`）。
- **(c) 模糊请求→0-item**：两条路。(c1) 无模块关键词的变更默认 `modules=["director"]`（`inquiry.py:279-280`），但 director 需要 scene_refs（`service.py:306-310`）→0 提案→blocked“无 completed 提案可编译”。(c2) LLM 返回空 items 也被标 `completed`（`script_proposal.py:215-218` 等，提示词明确允许空 items :44）：有 summary→validator 报 `PLAN_REQUEST_NOT_COVERED`（`plan_validator.py:53-55`）→blocked；无 summary→合成“剧本无需改动”阻塞决策（`script_proposal.py:218`）→**前端伪造“方案 A/B”选项**（`ChangePlanReview.vue:278-285`，因根本没有决策列表端点）→`plan.user_decision_ids` 非空→空计划通过校验→空 preview→`ready_for_review` 让用户批准一个 0-item 方案。

**“新变更基于最新 canonical”机制本来就在**：`create_session` 建时快照 `_current_signature`（`service.py:100,119,122`）+`client_request_id` 幂等（:94-99），只是被 `sessionAlive` 挡住走不到。“排队”选项 `_queuedChangeRequests` 入队后**无任何消费者**（只在 :487,504 push，:407 reset），静默腐烂。

### 根因 #2 —— canonical 更新后页面不刷新，要 F5（§33/§82）

后端持久化正确（原子提交 `execution_service.py:568-614`，:614 `pstore.save(projected)`；角色同步 :1048-1056）。**前端从不失效/重取**：
1. `changeSession.ts` 轮询执行（`poll:159-179`、`loadExecution:213-223`），到终态 `shouldPoll():66-72` 返回 false 后**轮询直接停**——无重取、无事件、无回调。
2. `ChangePlanReview.vue:161-163` 只渲染“创作变更已提交…执行完成。”文本，不调任何东西。
3. 项目 store（`store/index.ts`）**没有 canonical 域状态、没有失效 API**——只有 `loadProjectCharacters`（:357-379，仅角色列表）；`loadProject`（:346-353）只设名+清缓存。
4. 各域视图只在 mount/切项目时拉数据（Script `script-editor/index.vue:385`+watch :379；Storyboard `ShotBoard.vue:632-641`；Characters `characters/index.vue:370-389`）。唯一刷新是用户主动操作的 `emit('refresh')`。
5. 无事件总线（grep `mitt|eventBus|emitter` = 0 命中），无 SSE/WebSocket。

**断链点**：`changeSession.ts` 终态转换与任何域重取之间**完全没有调用**；也没有可失效的共享 canonical 缓存。R3 必须补“后端持久化→前端按 affected domain 失效+重取 canonical”。

### 根因 #3 —— Script-only 修改可能进入生产（§23/§34/§82）

**变更→影响→生产扩张链**：
```
create/resume_session → classify_request(关键词选模块) → _run_first_round_proposals(并行三 LLM)
→ [自动] _director_round2_target(脚本带 scene_id 就拉 Director) → compute_proposal_impact(下游 ImpactFacts)
→ compile_change_plan(直接项+impact 下游项) → build_preview(仅直接项→ChangeOperations)
→ [用户批准] execute_change_plan(默认 production_mode="affected_production", routers/agents.py:639)
→ commit_canonical → compute_production_manifest(execution_service.py:653)
→ run_production_planning(:1086) → _real_run_starter → ProjectProductionManager.start_run(:1233) ← 生产启动
```
**生产启动唯一决策点**：`execution_service.py:1233`，仅当 `manifest.planner_scene_ids` 非空（:1117）才启动；为空走 0 scene 无 run（:1118-1126）。manifest 纯由**批准的 ChangeOperation**推导（非 impact “RECOMMENDED”），op 规则（:697-735）：
- `update_story_text` → **无任何分支** → 无 shot action、无 scene、无 planner scene。
- `update_scene(summary/characters)` → 该 scene 每个 shot 标 first_frame+video 重生（:726-735）。
- `update_shot` → first_frame+video 重生（:706-716）。
- `update_character(description_en)` → 所有引用 shot 重生（:717-725）。

**纯 `update_story_text` 今天确实进不了生产**（三层隔离：`impact.py:60-63`、`revision/impact.py:135-137`、`execution_service.py:697-735` 都无分支）。**但 §34 仍然 OPEN，真正缺陷是边界由 LLM 输出形状决定、且生产默认 opt-out**：
1. 默认 `production_mode="affected_production"`（`routers/agents.py:639`）——每个批准计划默认带生产，除非调用方显式传 `creative_only`。没有“散文扩写→creative_only”的语义闸。
2. **Script Writer LLM 在 `update_story_text` 与 `update_scene`/`insert_scene` 间自由选择**——两者在提示词里是同等合法动作（`script_proposal.py:35-39`）。“扩写故事/把中间心理变化写详细”的转述可被发成 `update_scene(new_summary=…)`，这一个 op 就无条件给该 scene **所有 shot 标 first_frame+video 重生**（:726-735），无“同步进成片吗”确认。
3. **盲自动 Director 二轮**（`service.py:390-402`，:466-470 调用）：任何带 `scene_id`/`after_scene_id` 的脚本提案项都自动跑 Director 分镜提案，无用户把关；Director 项编译为 `update_shot` 等→重生。脚本 `update_scene` 因此结构性地拖入 Storyboard+生产。
4. **关键词模块路由**（`inquiry.py:80-94`）：“场景/节奏”→director，“镜头/分镜”→storyboard/production。脚本请求碰巧提到场景/镜头就可能在首轮把 Director/shot_producer 拉进 `modules`。
5. **并行旧 AI-6D apply 旁路**：`storyboard.propose_change` 仍带 EXECUTE 操作（`capabilities.py:101-103`）→`runtime.propose_storyboard_change:821`，可在 change_protocol manifest 纪律之外直接 apply canonical/production——变更到达 canonical 的第二条无防护路径。

**机制总结**：纯 story_text 散文安全；但没有任何东西*强制*散文式故事请求保持 `update_story_text`。LLM 可发 `update_scene`，系统自动拉 Director，默认 `affected_production` manifest 确定性地重生该 scene 的帧/视频。启动决策（:1233）按操作形状与模式把关，而非按“用户是否要求同步进成片”。

### 根因 #4 —— Direct Agent 会话丢失共享工作上下文（§40/§82）

- 直聊会话按 `(agent_id,scope_type,scope_id)` 故意隔离（`sessions.py:43-55`），每个 specialist 只看自己消息。
- Producer 委派脚本工作时，`runtime.delegate`（`runtime.py:598-674`）建持久 AgentTask（:625-632），甚至 get/create 了**目标 specialist 的 session**（:620-621）——但**一条消息也不写**、不链任何 specialist 之后能看到的东西。ChangeSession 提案期间，模块提案由直接生成器（`{script,character,storyboard}_proposal.py`）产出，根本不走委派。
- specialist 上下文构建器从不读 AgentTask/ChangeSession/委派历史：`context.py:16-81`=canonical+静态角色行；`run_specialist_read_answer` 包（`inquiry.py:864-918`，`build_for_specialist:345-376`）=overview+module view+scope+`active_workflows`（仅非终态）。已完成的委派/执行不可见（终态项被 `active_workflow_snapshot` 过滤，`service.py:183-203`）。
- 无 CollaborationTask、无共享工作记忆（grep 均 0）。

**结果**：Producer 委派并完成脚本工作后，用户打开 Script Writer，Script Writer 只能看到（已经变了的）canonical 文本——对“工作发生过、谁请求的、已完成”**零感知**。今天唯一的跨 agent 共享上下文 = canonical 真值 + 在途工作流快照。

---

## 11. Dependency Guard 现状（§22）

两个确定性校验器已存在，但都还不是 §22 要的“能力前 `DEPENDENCY_NOT_READY + required/blocked_step` 事实返回”：
- `change_protocol/dependency.py:18 build_dependency_facts`：返回结构化 `DependencyFact`（`fact_id/relation/source/target/summary`），纯确定性（无 LLM），**显式跳过 story**（:29）。只描述*结构边*，不表达*运行时就绪*（“角色参考图未生成”“源视频未完成”）；作为事实喂进计划，不是闸门。
- `change_protocol/plan_validator.py:47 validate`：返回 `PlanValidationIssue`（`code/message/severity`），全确定性，含 `PLAN_REQUEST_NOT_COVERED/OWNER_MISMATCH/ENTITY_NOT_FOUND/REFERENCE_CONFLICT/UNRESOLVED_CONFLICT/USER_DECISION_REQUIRED/STALE/LOCKED_SCOPE_REQUIRES_USER_DECISION/DEPENDENCY_CYCLE`。`has_errors` 在 `service.py:497-507` 硬阻断，error 计划无法进 validated/preview/approval。**LLM 无法绕过**。
- 运行时就绪闸（执行期，硬）：`_assert_agnes_prerequisites`（:975-993，未满足抛 `PRODUCTION_PREREQUISITE_FAILED`，0 Agnes 调用）、`dependency_ready`（`autopilot.py:137-149`）、preflight、all-plan 闸。

**缺口（R3/R5）**：没有一个能力前的统一 Dependency Guard 在*规划/决策期*把 `DEPENDENCY_NOT_READY`（带 `required`/`blocked_step`）返回给 Agent，让它决定如何满足前置条件。今天就绪只在执行后期以 `AgentError`/blocked execution 暴露。校验器是计划期+结构性的，生产闸是执行期+抛错的；LLM 都绕不过，但也无法在计划中干净地观察并满足缺失前置——正是 §22 要建的。

---

## 12. D0–D3 实现台账（§1，复用/改造/退役）

| 特性 | 现状（file:line） | 能用？ | 与 R 冲突？ | 处置 |
|---|---|---|---|---|
| `unified_turn` | `runtime.py:271-398`（intent 分支：runtime_control/conversation/producer inquiry/specialist read answer；持久 user+assistant 带 meta） | 是（10 D1 测试） | 部分：从不返回 creative_change（前端预桥接）；regex 意图 vs §13 语义分类 | **复用+改造**（R2：变更路由进后端；语义裁决） |
| `/turn` 端点 | `routers/agents.py:221-230` | 是 | 否 | 复用 |
| `/messages` 旧端点 | `agents.py:211-218`→废弃包装→`unified_turn`（`runtime.py:400-404`） | 是（兼容） | 否 | 最后退役 |
| 旧 `send_message` | `runtime.py:252-268`；聊天逻辑抽到 `_produce_chat_reply:204-250` | 是 | 否 | 复用（conversation 分支） |
| `agentUi.send` | `agentUi.ts:263-290`（正则变更桥+agentTurn） | 是 | 变更正则重复（agentUi.ts:81 vs index.vue:173）；路由在客户端 | **改造**（单一来源；后端吸收路由后收缩） |
| `run_inquiry` | `inquiry.py:541-612`（+force_kind/workflow_state/history） | 是 | 否 | 复用 |
| `run_specialist_read_answer` | `inquiry.py:864-918` | 是 | 否 | 复用 |
| `classify_turn_intent` | `inquiry.py:809-842`（+`_classify_control:790-806`，正则集合 732-787） | 是 | **是——§13 禁关键词硬编码**，当前 4 份正则散落 3+ 处 | **改造**（R2 上下文内语义裁决） |
| ChangeSession 桥（D2） | `changeSession.ts:411-507`；后端幂等 `client_request_id`+`context_scope`（`service.py:77-128`） | **部分**（3 手测缺陷，见根因#1） | 客户端桥接 Conversation↔ChangeSession；§7 要运行时层分离 | **改造**（R1/R2：新变更新建会话、终态释放、反馈真重调） |
| runtime_control（D3） | `runtime.py:447-585`+锁；inquiry.py:767-806；NL 命令→真实动作→grounded 回复 | 12 测试是；手测 gate 未过 | 控制匹配是 regex+guard（同 §13 关切） | 复用；R2 折进语义轮次 |
| AgentSession/AgentMessage | `models.py:188-245`（meta:intent/evidence/events） | 是 | 按 `(agent,scope)` 键，切作用域重建；非 §9 长存 Conversation | **改造**（R1 在其上加 conversation 对象+link 字段；保留 store） |
| 会话持久化 | `sessions.py:19-75`，JSON 原子写，重启/F5 存活 | 是 | §10.1 要更富 schema | 复用+扩展 |
| ChangeSession 恢复 | `changeSession.ts:365-392 restoreActive`；启动恢复 `api_server.py:218-240` | 是 | 否（但 restoreActive 会领养 blocked/approved 僵尸——根因#1） | 复用+修正终态过滤 |
| 项目 identity 持久化 | `store/index.ts:18-59`；`MainLayout.vue:134` 恢复；`TeamChatPanel.vue:122` 等待 | 是 | 否 | 复用 |
| Team Chat 后端 | `team_chat.py:193-242`（真实 inspect 问答），recovery :245-258，启动 `api_server.py:185-191` | 是 | **是**：变更分支仍建 7A（:203-220）；§27 要 AI-only 协作 feed | **改造**（R4：绑真实 agent-agent 事件；移除 7A 分支） |
| 7A ChangeAnalysisSession | `change_analysis.py`+`routers/agents.py:342-385` | 是 | 与 7B 对用户可见变更流冗余 | **退役/降级**（分析层可喂 R 阶段内部规划） |
| AI-6D Director Proposal | `agents.py:415-471`，`runtime.py:821-893`，`ShotBoard.vue:306` | 是 | **是**：可 apply canonical 的并行提案系统，违反“ChangeSession 唯一变更通道” | **§49 审计后退役** |
| Producer 委派 `/agents/tasks` | `agents.py:246-256`→`runtime.delegate:598-674`（五重权限检查，JobQueue 长跑） | 是 | 否（契合 §5/§6；R4 可放宽强制委派官僚） | 复用 |
| 共享工作记忆 | **未实现** | — | §10.2 本阶段要求 | R1 新建 |
| 偏好记忆 | **未实现** | — | §10.3 要求 | R1 新建 |
| 运行时自检 | `active_workflow_snapshot`（`service.py:160`）+project-intelligence views | 部分（只读真值存在，无 §35 自检面） | 差距非冲突 | R7 扩展 |

**测试覆盖备注**：D1=10 测试，D2=4 测试（仅建会话幂等/来源，**3 个桥缺陷零覆盖**），D3=12 测试。据记忆 548 回归曾全绿，但 D3 手测 gate 与浏览器证明未完成。

---

## 13. 旧 D0–D6 → 新 R0–R8 映射（§1 不盲目续旧计划）

R 文档**取代**旧 D0–D6 轨道（旧文档 `AI-7D_CLAUDECODE_UNIFIED_AGENT_INTERACTION_RUNTIME.md` 909 行）。R 是超集+架构纠偏，不是推倒重来：

| 旧 D | 状态 | 新 R 承接 |
|---|---|---|
| D0 入口审计 | 已做（`AI-7D_ENTRY_MAP.md`，D1/D2 后已过时） | **R0**（范围更大：+依赖审计+4 根因+台账） |
| D1 统一 Turn（读/对话） | 已做未提交 | R2（语义轮次）+ R1（会话基础） |
| D2 变更→ChangeSession | 已做但有 3 缺陷 | R1（Conversation≠ChangeSession 纠正）+ R2 |
| D3 运行时查询/控制 | 已做未手测 | R2（语义 pending-decision）+ R7（自检） |
| D4 AI-only Team Chat | 前端输入框已删 | R4（真实协作+AI-only） |
| D5 持久化/记忆审计 | 未做 | R1（三层记忆，含共享工作+偏好） |
| D6 集成收口 | 未做 | R8（真浏览器+10 黄金交互） |
| —（旧 D 无） | — | **R3** Producer 跨域写+权限+真值刷新 |
| —（旧 D 无） | — | **R5** 自主工作计划/进度账本/Playbook |
| —（旧 D 无） | — | **R6** Director 场景子 agent |

**判断**：不新建 V2，复用 D0–D3 已落地的统一 turn/会话持久化/ChangeSession/执行内核，在其上做架构纠偏与能力扩张。旧 D2 桥不是“打补丁”对象，而是被 R1/R2 的“Conversation≠ChangeSession、每轮新建 ChangeSession、终态释放、反馈真重调”结构性替换。

---

## 14. 对后续阶段的设计要点提示（非 R0 决定，供 R1+ 参考）

1. **R1 核心**：引入 `conversation_id`（凌驾 agent/scope session 之上，每项目每可见 agent 一个长存直聊会话）；message 加一等 `linked_*` 字段；建共享工作记忆 + 偏好记忆两个轻量 store；**修正根因#1**——已完成/blocked ChangeSession 必须终态化或对新变更“不可见”，新变更一律新建会话基于最新 canonical；`request_changes` 必须真重调 agent（反馈进 LLM 上下文，不跳过已有提案）；删 `_queuedChangeRequests` 死队列或接上消费者。
2. **R2 核心**：把变更/审批/控制语义裁决全部收进后端统一 Turn Runtime，删除前端 4 处正则；pending-decision 语义解析（approve/revise/reject/question/new_intent）；批准后自动续执行（§15）；工具调用是运行时操作不是助手散文（§31）。
3. **R3 核心**：授予 Producer 三域受控写权限（仍走 capability+revision+审计，**不绕内核**）；各 specialist 自有域写；**修根因#2**（执行终态→按 affected domain 失效+重取 canonical，可加轻量事件总线或 store 失效 API）；**修根因#3**（散文式故事请求强制 creative-only / 不自动拉 Director 二轮 / production 默认须按用户意图而非 LLM 操作形状）；新增 `visible` 与 `enabled` 解耦。
   - **注意一个新写路径**：§6/§16 的 Level-1“安全直接动作”（如“把最后一句改成我们回家吧”）要求 Agent 直接执行、用户只见“已更新·撤销”，但今天所有创作写都走重型 ChangeSession 提案→批准→执行。R3 需建一条**轻量直接编辑路径**：仍经受控 capability + 原子 revision/checkpoint + 审计（§2/§16），但跳过提案/审批仪式。这是有意新增的能力，须谨慎确保不绕内核。
4. **R4 核心**：CollaborationTask 耐久协议；agent→agent 真实请求/结构化结果；共享工作上下文喂入 specialist（**修根因#4**）；Team Chat 渲染真实协作、禁用/退役后端用户消息端点；移除 7A 变更分支。
5. **R5 核心**：AutonomousWork/WorkStep/Progress Ledger；自适应重规划；依赖边+无进展检测；把 W5/W6/W7 创作部分转 Playbook，硬依赖留内核；建 §22 能力前 Dependency Guard（返回 DEPENDENCY_NOT_READY 事实）；退役 AI-6D 旁路。
6. **R6**：Director 临时 Scene worker（scoped，声明 scene_id/read/write/parent work_id，不可改别的 Scene/Character/全局故事）。
7. **R7**：运行时自检能力（最近动作/协作/阻塞/能力快照，从真实记录来，不暴露隐藏推理/密钥）。
8. **R8**：真前端+真后端+真项目+真直聊的浏览器验收；10 个黄金交互；30+ 转述评测；项目隔离；重启测试。

---

## 15. R0 结论与待确认事项

**R0 交付完成（§51 全部 9 张地图 + 4 根因），未改任何代码。**

确定性内核（§2 冻结清单）经审计**完好且代码强制**，无需重写：canonical/revision/ChangeSet/ChangeSession/ChangeExecution/manifest/impact/ProductionRun/JobQueue/检查点恢复/原子提交/项目隔离/幂等/stale 检测全部在位，LLM 无法绕过。本阶段风险集中在“内核之上的 agent 交互层”，与 R 文档“最大灵活性 above 确定性内核”的定位一致。

**请用户确认（§52 R0 Manual Gate）**：
1. 上述架构地图是否准确反映你对现状的认知？有无遗漏的入口/工作流/旁路？
2. 确认 R 文档**取代**旧 D0–D6 轨道（旧 D2 桥不打补丁，由 R1/R2 结构性替换）——这与你“和 GPT 重新对齐后大改”的方向一致吗？
3. **AI-6D Director Proposal 旁路**（分镜页“创建导演提案”按钮，可直接 apply canonical）计划在 R5 §49 审计后退役、统一走 ChangeSession——是否同意？还是该按钮的 UX 需要保留（那就把它接到 ChangeSession 通道而非删掉）？
4. R3 将新增一条“Level-1 安全直接编辑”轻量写路径（跳过提案/审批，但仍经 capability+原子 revision+审计）。这是 §6/§16 明确要求的，我按此设计——如你对“哪些操作算 Level-1 安全可直接改”有边界偏好（例如角色外观修改是否一律要审批），请告知。

确认后进入 R1（会话+基础记忆基础 + 修根因#1），R1 自动测试后 STOP 等你手测（§53/§54）。
