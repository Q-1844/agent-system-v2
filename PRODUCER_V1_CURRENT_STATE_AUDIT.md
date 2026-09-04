# Producer V1 · 现状审计报告（CURRENT_STATE_AUDIT）

> 范围：对当前项目中已经存在但效果不理想的 **Producer Agent** 做完整现状审计。
> 红线：本轮**只调查、分析、出报告，不修改任何生产代码**。不重构 Producer、不实现群聊、不实现新 Orchestrator。
> 方法：所有结构结论来自真实代码逐行阅读，附 `file:line`。不从名字推测。
> 执行：Claude Code（anchor + 前端/能力/上下文自读）+ 三个并行 fork 子审计（identity/entry、specialist invocation、cross-cutting）。
> 时间：2026-09-04。基线：父 `d8e1f17`（branch `master`），submodule `animation-storyboard` 指针有未提交改动（未触碰）。

---

## 1. Executive Summary

**Producer 是 AI-6B 时代的「Producer Supervisor」协调者**（`src/agents/registry.py:33-41`），定义上只持有一项只读能力 `project.inspect`，`write_scopes=["agent_task"]`，`can_delegate_to=全部 7 个 Specialist`。它的 system prompt（`registry.py:297-303` + 真话纪律后缀 `:344-361`）要求它「理解需求、判断找哪个 Agent、创建 AgentTask 委派、汇总结果」，但文档（`docs/agents/roles/producer.md §28`）同时写明：**autonomous Producer（自动分解 + 自动委派链）未实现且本阶段不做**。

**核心发现：Producer 与三个冻结的真实 Specialist tool-loop（`script_writer_turn` / `character_designer_turn` / `director_turn`）在结构上是断开的。** Producer 的 Direct Chat（`unified_turn`，`runtime.py:347`）对 Producer 退化为只读 `run_inquiry`（`runtime.py:438`）；Producer 唯一能触达 Specialist 的路径是**手动委派表单** → `POST /agents/tasks` → `AgentRuntime.delegate()`（`runtime.py:691`）→ 同步 capability adapter（`capabilities.py:_ADAPTERS`），而 `_ADAPTERS` 只含 7 个 `*.inspect` 只读快照，所有写能力（`story.edit`/`character.design`/`storyboard.propose_change`…）`implemented=False` → 返回 `NOT_IMPLEMENTED`。真实写 canonical 的 tool-loop 只能由**用户直开某 Specialist 会话**经 `/turn`（`routers/agents.py:229`）与 `/turn/stream`（`:282`）触发，且 **Producer 被明确拒绝使用 `/turn/stream`**（`routers/agents.py:321-323`）。Producer 自己没有 tool-loop（全仓无 `producer_turn` 函数）。

**因此当用户对 Producer 说「请把这个项目全流程优化一下」时**，`classify_turn_intent`（`inquiry.py:810`）把「优化」判为 `conversation`（不在 `_CHANGE_VERBS` `:75-79`、不命中 `_PROJECT_FACT_RE` `:739-742`）→ 走盲聊 `_produce_chat_reply`（`runtime.py:432`）→ 在真话纪律约束下回一段「请告诉我具体想优化哪部分 / 我可以协调 SW/CD/Director…」的建议性文本，**不读项目、不委派、不执行**。零真实推进。

**最关键缺失**：**「Producer → 真实 Specialist turn 的调用与衔接层」完全不存在**——既没有调用路径，也没有结构化任务/结果 schema 与跨步移交状态。Producer 既没有绕过 Specialist（canonical 所有权边界干净，见 §12），也没有真正触达它们（结构断开，见 §5-7）。它目前只是一个**只读项目问答器 + 手表单委派器**。

---

## 2. Current Producer Architecture

八个固定 Agent（`registry.py:24-28`）：`producer` + 7 Specialist（`script_writer / character_designer / director / production_planner / shot_producer / continuity_reviewer / editor`）。Producer 居顶，`can_delegate_to` 全部 Specialist（单向，Specialist→Specialist 禁止，`registry.py:166-168`）。

运行时层并存**四代 + 一套新 tool-loop**（`routers/agents.py` + `src/agents/`）：

| 代 | 端点 | 真行为 | Producer 是否用 |
|---|---|---|---|
| Gen-1 legacy chat | `POST .../agents/sessions/{sid}/messages` (`send_message`) | `client.chat()` 原样存模型文本，0 工具/0 委派 | Producer Direct Chat 的**旧**路径（AI-7D-D0 判为最坏 legacy，已逐步退役） |
| Gen-2 真只读 inquiry | `/project-intelligence` + `run_inquiry` | 真 canonical 读盘 + 接地 NL 回答 | ✅ Producer Direct Chat 现走此（`unified_turn` `runtime.py:438`） |
| Gen-2 真 delegate | `POST /agents/tasks` → `runtime.delegate` | 建 AgentTask + 五重权限 + sync capability dispatch | ✅ Producer 唯一 Specialist 触达路径（**仅 inspect**） |
| Gen-3 ChangeSession | `/change-sessions/*`（7B/7C） | 真修改流：提案→预览→批准→执行 | ❌ Producer 不驱动；用户/Floating Bar 直驱 |
| Gen-2.5 旧 Director Proposal | `POST .../agents/director/proposals` | AI-6D 语义提案→ChangeSet→Workspace | ❌ Producer 从不调用；独立 UI 端点 |
| **新 tool-loop Specialist** | `/turn` + `/turn/stream` | `script_writer_turn`/`character_designer_turn`/`director_turn` 真写 canonical（OCC/真值闸门/批量工具/SSE/可再生窗口） | ❌ **仅用户直开 Specialist 会话可触发；Producer 无路径、且 `/turn/stream` 对 Producer permission_denied** |

**前端**：`STREAMED_AGENTS = {script_writer, character_designer, director}`（`src/views/agents/index.vue:345`）。Producer **不在其中** → 无思考开关、无 `/turn/stream`、无流式执行过程条。Producer 聊天经 `apiClient.agentTurn`（统一 `/turn`，对 Producer = `run_inquiry` 只读，`agentUi.ts:286` / `index.vue:627`）。Producer 卡独有「Delegated Tasks」表 + 「New Delegation」表单（`index.vue:60-90`）。

**架构图（现状，非理想）**：

```
用户 ──Direct Chat──> Producer(unified_turn=run_inquiry 只读问答) ──手动表单──> delegate() ──sync inspect adapter──> 只读快照
                                                                                     （写能力 NOT_IMPLEMENTED）
用户 ──直开 Specialist 会话──> /turn · /turn/stream ──> 真实 tool-loop（SW/CD/Director）──> 真写 canonical
                                  ↑ Producer 无此路径、被 /turn/stream 拒绝
```

---

## 3. Producer Prompt / Runtime

**System prompt**（`registry.py:297-303`）原文要点：
- 身份：`Producer Supervisor`，AGNES 项目总协调者。
- 职责：理解用户项目级要求、阅读项目状态、判断应找哪个专业 Agent、创建 AgentTask 委派、汇总结果。
- 可以：查看项目概览（`project.inspect`）、把任务委派给 7 个 Specialist。
- 不能：直接执行专业领域操作（改分镜/生成图片视频）。
- 输出要简明结构化：给出结论与建议下一步。

**真话纪律后缀**（`agent_system_prompt()` `registry.py:344-361`，所有 Agent 共享）：禁止在对话中声称已委派/已创建任务/已执行修改/已应用变更——这些只能由系统后台真实发生（产生真实 AgentTask/ChangeSession 记录）。未看到真实记录即未发生。

**Runtime**：Producer Direct Chat 经 `unified_turn`（`runtime.py:347`）。对 `agent_id=="producer"` 分支（`runtime.py:438`）调 `run_inquiry(force_kind="question")` → 只读 inspect + 一次 LLM 综合接地回答。**不是 tool-loop**：Producer 在自己的 turn 内没有任何工具可调、不能决定调用 Specialist、不能创建 AgentTask（委派只能从前端表单走 HTTP）。全仓 grep 无 `producer_turn` 函数。

**模型**：`default_model_profile="producer_profile"`（`registry.py:41`）。`producer_profile`（`registry.py:128`）= `reasoning_model=_agnes_models.get("text")`（`:119`）= config `agnes.models.text` = **Agnes 文本模型**，与 `text_default` 同源。前端模型滑动开关（Agnes/SenseNova）**仅 Script Writer 暴露**（`index.vue:227` `v-if="selectedId==='script_writer'"`），Producer 无模型切换 UI。

**独立性 / 持久会话**：Producer 有独立 `AgentSession`，按 `(agent_id, scope_type, scope_id)` 隔离（`models.py:249-264`）。AI-7D-R §9 起 Direct Conversation 长存于 project 级（稳定 conversation_id），切页面不重建、不清空历史（`agentUi.ts:169-187` `ensureSession`）。session+messages 落盘可恢复（`AgentSessionStore`）。`openSession` 重载（`index.vue:569-591`）。

---

## 4. Current Tools

- **`allowed_capabilities = ["project.inspect"]`**（`registry.py:37`）——唯一直接可执行能力，**read-only**。
- `project.inspect` adapter `_project_inspect`（`capabilities.py:153-164`）返回：scene/shot/角色数 + 风格 suffix + `artifact_refs[{type:"scene",id}]` + `suggested_next_actions=["查看各场景分镜结构","检查制作计划"]` + `data{project, scene_count, shot_count, char_count}`。亚毫秒本地读，不调 Agnes。
- **Producer 不在任何 domain-inspect 的 `allowed_agents` 中**（`capabilities.py:41-42` 注释：`can_execute(producer, storyboard.inspect)==False`，必须 delegate）。`project.inspect` 的 `allowed_agents=["producer"]` only（`:68`）。
- **`write_scopes = ["agent_task"]`**（`registry.py:39`）——Producer 的"写"仅限创建 `AgentTask` 记录。
- **委派不是 Producer turn 内的工具**：Producer 的 LLM turn 内没有任何 `delegate_to_specialist` 工具。委派只能从前端表单（`index.vue:60-90` `doDelegate` → `apiClient.createAgentTask` → `POST /agents/tasks`）触发。即 Producer 模型**不能在对话中自主决定调用 Specialist**——它只能产出自然语言文本，由用户去点表单。
- **build_context("producer")**（`context.py:36-38`）注入：project name、scope、`场景数/镜头数/角色数`、`可委派 Specialist: script_writer/character_designer/director/production_planner/shot_producer/continuity_reviewer/editor`。极简，无具体故事/角色/分镜内容。

---

## 5. Script Writer Invocation Path

**唯一 Producer 可触达路径**：`POST /agents/tasks`（手动表单）→ `AgentRuntime.delegate()`（`runtime.py:691`）→ `dispatch_capability`（`capabilities.py:306`）→ `_ADAPTERS["story.inspect"]`（`capabilities.py:297`，`_story_inspect` `:167-177`，同步返回 `story_text_head[:2000]` + 场景列表快照）。

- `story.edit`（`capabilities.py:73`）/`story.propose_change`（`:80`）`implemented=False` → 不在 `_ADAPTERS` → `dispatch` 返回 `NOT_IMPLEMENTED`（`capabilities.py:310-312`）→ task `FAILED`（`runtime.py:757-759`）。
- `instruction` 字段**不被 inspect adapter 消费**（adapter 只吃 `ctx`，`capabilities.py:153-164`），所以表单里写的指令在 inspect 路径上基本被忽略。

**真实 SW tool-loop**：`script_writer_turn`（`script_writer.py:1911`），写 `story_text`/`scenes`，持 `story` 资源锁，有 OCC 指纹/真值闸门/批量工具/续写恢复/web.search。**仅**由用户直开 SW 会话经 `/turn`（`unified_turn` `runtime.py:372` 对 SW project-scope 特殊分发）与 `/turn/stream`（`routers/agents.py:305-320`）触发，或经测试弹窗 `/agents/script-writer-test/turn`（`routers/agents.py:974`）。**Producer 无任何代码路径调用 `script_writer_turn`。**

**分类：(b) 同步 capability adapter**。SW **不**保持完整身份（真实 Runner 未被触达）。

---

## 6. Character Designer Invocation Path

**唯一路径**：`POST /agents/tasks` → `delegate()` → `_ADAPTERS["character.inspect"]`（`capabilities.py:298`，`_character_inspect` `:180-197`，返回角色列表 + `has_views` + `related_scenes` 快照）。

- `character.design`（`:85`）/`character.propose_change`（`:92`）`implemented=False` → `NOT_IMPLEMENTED` → `FAILED`。

**真实 CD tool-loop**：`character_designer_turn`（`character_designer.py:1479`），写 `characters`，持 `character` 锁，可 `character.create/update/delete/generate_views`。仅用户直开 CD 会话经 `/turn`（`runtime.py:378` 特殊分发）与 `/turn/stream` 触发。**Producer 无路径调用 `character_designer_turn`。**

**分类：(b) 同步 capability adapter**。CD 不保持完整身份。

---

## 7. Director Invocation Path

**唯一路径**：`POST /agents/tasks` → `delegate()` → `_ADAPTERS["storyboard.inspect"]`（`capabilities.py:299`，`_storyboard_inspect` `:200-229`，DEMO：查某场景的镜头列表 + 覆盖检查 issues，sync）。

- `storyboard.propose_change` 在 capability 定义里 `implemented=True`（`capabilities.py:103`），但**不在 `_ADAPTERS`**（`:97-98` 注释：经 `AgentRuntime.propose_storyboard_change` async，不经 sync dispatch）→ `dispatch` 仍返回 `NOT_IMPLEMENTED`。

**第二条独立路径（Producer 从不调用）**：`POST .../agents/director/proposals`（`routers/agents.py:643`）→ `propose_storyboard_change`（`runtime.py:936`）→ `director_proposal.py::generate_proposal`（旧 AI-6D 语义提案→ChangeSet→Workspace，apply 走 `cs_engine.apply`+`workspace_lock`+用户 approve，`routers/agents.py:681-691`）。是 Director 域 UI 端点，**仍非新 `director.py` tool-loop**。

**真实 Director tool-loop**：`director_turn`（`director.py:2321`），写 `scenes[].shots`，持 `storyboard` 锁，含批量 `shot.create_many/update_many/delete_many/insert_many`、`move`、`generate_images/videos`，返回 `receipts/trace/search_sources`（`director.py:2326`）。Director 在 `unified_turn` **未**被特殊分发（`/turn` 上的 Director 落到只读 `run_specialist_read_answer`，`runtime.py:460`）；真实写只能经 `/turn/stream`（`routers/agents.py:305-320`）或测试弹窗 `/agents/director-test/turn`（`:1010`，用 `director_min.director_turn` 精简存根 `director_min.py:605`）。**`/turn/stream` 对 Producer `permission_denied`**（`routers/agents.py:321-323`：「流式 turn 仅支持 Script Writer / Character Designer / Director 会话」）。Producer 无路径调用 `director_turn`。

**分类：(b) 同步 capability adapter**（Producer 唯一可达路径）。Director 不保持完整身份。

---

## 8. Task Input Schema

**`DelegateReq`**（`routers/agents.py:148-155`）：

```python
class DelegateReq(BaseModel):
    requester_agent: str = "producer"
    target_agent: str
    scope_type: str = "scene"
    scope_id: str = ""
    instruction: str = ""          # ← 裸自由文本
    capability_id: str
    operation: str = "inspect"
```

**`AgentTask`** 持久化（`models.py:328-362`）：`task_id, project, requester_agent, target_agent, capability_id, instruction, scope_type, scope_id, session_id, operation, parent_task_id, status, result_summary, artifact_refs, structured_result, execution_ref, warnings, suggested_next_actions, error, ...`。

**丰富度评估**：**只有裸 instruction 字符串 + scope + capability_id + operation**。**没有** `objective` / `situation` / `constraints` / `previous_result` / `handoff` / `context` 字段。`parent_task_id` 字段存在（`models.py:350`）但 `delegate()` 构造 task 时**从不设置**（`runtime.py:718-724`），`_create_consultation_task` 也不设（`inquiry.py:386-391`），全仓 `parent_task_id=` 仅出现在 `from_dict` 反序列化（`models.py:397`）——**链式字段是遗留空字段，无逻辑消费它**。`instruction` 在 inspect 路径上甚至不被 adapter 消费。

**结论**：当前任务输入**不足以让 Producer「像人一样做好衔接」**。无法承载「现在为什么找你 / 当前发生了什么 / 用户真正希望解决什么」。未来期望的 `{objective, situation, constraints}` 结构完全缺失。

---

## 9. Agent Result Schema

两条**不互通**的返回形态，Producer 只能看到较差的那条：

1. **`delegate()` 路径（Producer 唯一可见）** → `AgentTask.to_dict()`（`runtime.py:767`），其 `structured_result` 为 `AgentStructuredResult`（`models.py:294-311`）：
   ```
   status, summary, artifact_refs[{type,id}], warnings[], suggested_next_actions[], data{}
   ```
   - 写能力 → `status=NOT_IMPLEMENTED` → task `FAILED`。
   - inspect 能力 → `data` 带浅层 canonical 快照（如 `_story_inspect` 返回 `story_text_head`+场景；`_storyboard_inspect` 返回某场景镜头）。

2. **新 tool-loop 路径（Producer 永远收不到）** → `AgentTurnResult`（`runtime.py:481-491`）：`{turn_id, session_id, agent_id, intent, assistant_message, events, linked_task_ids, linked_change_session_id, evidence_refs, message}`，Director 循环额外返回 `receipts/trace/search_sources`（`director.py:2326`）。**该形态支持「做了什么/是否成功/影响了什么/下一步」——但 Producer 从不接收它**，只回给 Direct Chat 调用者。

另有 **`ConsultationResult`**（`inquiry.py:182-205`）：`status/summary/owner_module/owner_agent/direct_implications/downstream_facts/evidence_refs/user_decisions_needed`，结算进路径②的 AgentTask（`inquiry.py:394-403`）。

**是否支撑 Producer 推理下一步**：`AgentStructuredResult` 有 `suggested_next_actions` + `artifact_refs`，原则上 Producer 可读。**实际不行**：写能力返回 `NOT_IMPLEMENTED`，无有意义的 `suggested_next_actions`；且无代码把 `suggested_next_actions` 作为下一 task 输入消费。

---

## 10. Cross-Agent Handoff Today

**结论：不存在任何自动移交机制。每次委派相互独立。**

- `DelegateReq` 无 `previous_result/situation/handoff/context` 字段（`routers/agents.py:148`）。
- `AgentTask.parent_task_id`（`models.py:350`）是唯一链接字段，但**从未被赋值**（`runtime.py:718` / `inquiry.py:386`）。
- `suggested_next_actions`（`models.py:301`）是 **Specialist 返回侧**字段，**无代码把它作为下一个 task 的输入消费**——仅存档。
- 路径② `_producer_synthesize_analysis`（`inquiry.py:657`）确实把多 Specialist 咨询结果聚合成一个 analysis-only 计划，但产出 `execution_ready=False` + 0 `ChangeOperation`（`inquiry.py:727`）——**不是真实链式执行，只是只读分析**。
- 路径③（真实 Specialist turn）的 `AgentTurnResult` 根本不进 Producer 的 AgentTask。

**即**：SW 完成后，下一个对 Director 的委派**不会自动携带**「s05 动机已改、角色未变、请复查 s05 分镜」。必须由 Producer（或用户）手工读上一 task 的 `structured_result` 再手写新 instruction——而**代码里没有任何东西做这件事**。当前完全是 `call Agent A → done → call Agent B → done`，中间无信息流动。

---

## 11. "全流程优化一下" Current Behavior Walkthrough（纯分析，未执行）

用户对**活的 Producer Direct Chat** 说「请把这个项目全流程优化一下」：

1. `unified_turn`（`runtime.py:347`），`agent_id=="producer"`。
2. `classify_turn_intent`（`inquiry.py:810`）：「优化」**不在** `_CHANGE_VERBS`（`inquiry.py:75-79`：改/修改/换成/变成/删除/调整/替换/重写/续写…）；「全流程优化一下」也不命中 `_PROJECT_FACT_RE`（`inquiry.py:739-742`：剧本/故事/场/角色/镜头/分镜…）→ `_is_trivial_chatter=True`（`:745`）→ **intent="conversation"**。
3. 走 `_produce_chat_reply`（`runtime.py:432`）→ 盲聊。Producer 在 system prompt + 真话纪律约束下（`registry.py:354-361`：不得声称已委派/已执行），大概率回复「请告诉我具体想优化哪部分 / 我可以帮你协调 SW、CD、Director…」——**不读项目、不委派、不执行**。
4. 即便分类成 `project_inquiry` → `run_inquiry force_kind="question"`（`runtime.py:443`）→ 只读 inspect 概览 + Producer LLM 自然语言回答。仍**不触发任何真实 Specialist turn**。
5. 即便分类成 change → D1 **降级为 conversation**（`inquiry.py:828-830`：D1 change→conversation，D2 才接 ChangeSession）；ChangeSession 是独立提案 UI 流，非 Producer 驱动链式执行。

**理想 SW→CD→Director 链的第一个断裂点**：**Producer 的 `unified_turn` 根本没有「调用真实 Specialist tool-loop」的代码路径**。在到达「SW 做完→带给 Director→Director 做完」这种衔接问题之前，**第一步「Producer 让 SW 真正跑一次」就不存在**。`delegate()` 表单能建只读 inspect task，但 SW 的 `story.edit`/`story.propose_change` 未接通；`/turn/stream` 真实 SW turn 只能由用户直开 SW 会话触发，Producer 无权/无路径调它。

**结果**：当前 Producer 对「全流程优化」的响应 ≈ **一段建议性对话，零真实推进**。这正是「Producer 没做好」的根因。

---

## 12. Canonical Ownership Audit

**结论：Producer 严格不越权，无任何 canonical 直写、无 Agent Bridge 旁路。这是当前实现最干净的一面。**

- Producer `allowed_capabilities=["project.inspect"]`（`registry.py:37`），read-only。`capabilities.py:68` 确认 `project.inspect` `allowed_agents=["producer"]` only。
- Producer **不在**任何 story/character/shot 写能力的 `allowed_agents` 里：`story.edit`→`["script_writer"]`（`capabilities.py:73`）、`character.design`→`["character_designer"]`（`:85`）、`storyboard.propose_change`→`["director"]`（`:103`）、`shot.*`→`["shot_producer"]`（`:123-132`）。`can_execute(producer, storyboard.inspect)==False`（`capabilities.py:42`）。
- `delegate()` 校验的是 **target** Agent 的权限（`runtime.py:709`），Producer 仅 requester，自身无写能力。
- `run_inquiry`/`consult_specialist` 路径硬闸 `assert_read_only_capability(cap)`（`inquiry.py:481`）——mutation cap 直接拒。
- 全仓**无** `AgentBridge`/`agent_bridge`/`class.*Bridge`。
- Producer-facing 路由（`routers/agents.py:462-560`：tasks/project-intelligence/team-chat/change-analysis）无直接 `store.update_shot`/`ProjectStore` 写调用。Director proposal 的 apply 写 canonical，但走 `cs_engine.apply` + `workspace_lock` + 用户 approve（`routers/agents.py:681-691`），是 Director 域路径，非 Producer 直写。
- `write_scopes=["agent_task"]`——Producer 的"写"仅限创建 AgentTask。

**Producer 未变成第四个 canonical writer。** Story→SW / Character→CD / Shot→Director 所有权边界完整保留。

---

## 13. Session / Context Behavior

- Producer 有独立 `AgentSession`，按 `(agent_id, scope_type, scope_id)` 隔离（`models.py:249-264`）。AI-7D-R §9：Direct Conversation 长存于 project 级（稳定 conversation_id），页面 context（scene/shot/character）是 per-turn 元数据，不再作为会话身份键——切页面不重建、不清空历史（`agentUi.ts:169-187`）。
- session+messages 落盘可恢复（`AgentSessionStore`）。`openSession` 重载（`index.vue:569-591`）。
- `build_context("producer")`（`context.py:36-38`）注入：project name、scope、场景/镜头/角色数、可委派 Specialist 列表。极简。
- **Producer turn 基本无状态**：`unified_turn`（`runtime.py:347-491`）每次从 `session.messages` + `build_context` + `active_workflow_snapshot` 重新组装上下文——**无跨 turn 的「进行到第几步/上一个 Agent 做了什么」记忆**（除会话历史文本外）。
- `WorkMemoryStore`（`runtime.py:124-146`，`record_work`/`render_for_context`）：**项目级共享**工作记忆（所有 Agent 共享，非 Producer 专有），best-effort，limit=6 条，非结构化文本笔记。
- `PreferenceStore`（`runtime.py:127`）：项目级显式偏好，非 Producer 工作流状态。

---

## 14. Sync / Parallel Capability

**结论：底层锁/会话基础设施对「不同域并行」基本就绪，但缺一个 Producer 级 fan-out 驱动器；真实 Specialist 执行的并行从未被 Producer 触发。**

- `delegate()` 是 `async` 但单次 await（`runtime.py:691-767`）：inspect 同步 dispatch；`production_plan.create` await 真实 IO；shot 长执行提交 durable Job 后立即返回 running（`:736-741`）。**不 fan-out 多 Specialist。**
- 唯一并行 = `consult_specialists_parallel`（`inquiry.py:497`）用 `asyncio.gather`——但是**只读 LLM 咨询**，非真实 Specialist 执行。
- **同会话互斥**：`/turn/stream` 以 `f"{project}::{sid}"` 为 key，命中 `_ACTIVE_TURN_EVENTS` 则 **409**（`routers/agents.py:331-334`）。同一 Specialist 会话不可并发；**不同 Specialist 会话可并发**。
- **会话隔离**：长存 `(project, agent_id)` Conversation（`models.py:249-264`），不同 Agent 不同会话 → **无会话冲突**。
- **资源锁粒度 = per-RESOURCE，三把独立锁**：`RESOURCE_STORY` / `RESOURCE_CHARACTER` / `RESOURCE_STORYBOARD`（`routers/agents.py:307,313,318`）。SW 锁 story、CD 锁 character、Director 锁 storyboard。**两个 Agent 写不同 canonical 域技术上不会锁冲突**。
- `/events` SSE 项目级、支持多订阅（`routers/agents.py:416-446`）；`/turn/stream` 每会话一条。多 active turn 跨会话允许（key 不同）。
- **缺口**：(1) 无 Producer 级并行驱动器把多个真实 Specialist turn 同时发出去；(2) 同 Specialist 会话串行；(3) 跨域 canonical 写入的原子性/一致性（跨域事务）无机制——但 v1 串行下不会暴露。

**未来并行就绪度**：锁层 ✅ / 会话层 ✅ / SSE ✅ / Producer 驱动器 ❌（需新建，本轮不实现）。

---

## 15. SSE / UI / Future Group-Chat Observability

**已有结构化/持久化事件**：

- **`AgentTask` 落盘**（`projects/{p}/output/agents/tasks/*.json`）：含 `instruction`（出向任务文本）、`structured_result`（回向结果）、`status`、`result_summary`、`artifact_refs`、`suggested_next_actions`、`warnings`。→ **未来群聊最直接的结构化源**——出向 objective 与回向 summary 都在里面。
- **`ConsultationResult`**（`inquiry.py:182-205`）：`status/summary/owner_module/owner_agent/direct_implications/downstream_facts/evidence_refs/user_decisions_needed`，结算进 AgentTask（`inquiry.py:394-403`）。
- **`/turn/stream` SSE**：`turn.started → reasoning.*/tool.*/assistant.delta → turn.completed/failed`（`routers/agents.py:398-405`）。**细粒度事件仅在有观看连接时入队，客户端断开即丢**（`:358-366`）。
- **`/events` 项目 SSE**：`snapshot/resource.locked/resource.unlocked/story.changed/character.changed/storyboard.changed`（`:416-446`）。**始终发总线**，后台跑也发。
- **`meta.trace`/`meta.reasonings`** 持久化在 assistant 消息，刷新可重建折叠条（`runtime.py:475-478`，AI-7D-R hydrate）。

**群聊三要素现状**：

| 群聊需要 | 现状 |
|---|---|
| Producer→Agent 任务文本（objective/situation） | ✅ 持久化为 `AgentTask.instruction`（自由文本 str，**非**结构化 `{objective,situation,constraints}`） |
| Agent→Producer 结果摘要 | ✅ 持久化为 `AgentTask.structured_result`/`result_summary`（但**仅路径①②有**；路径③真实 turn 的结果不进 Producer 的 AgentTask） |
| 每 Agent 实时状态（working/completed/failed/waiting） | ⚠️ 部分：终态在 `AgentTask.status`（pending/running/completed/failed）；**实时"working"只在 Specialist 自己的 `/turn/stream`**，Producer 不订阅 → "Producer waiting" 无事件 |

**关键回答**：Producer 出向任务说明与 Specialist 回向结果**已经可以作为结构化事件原样保存**（AgentTask 即是）——但**仅限路径①②（只读 inspect/咨询）**。**路径③（真实 Specialist 创作 turn）的出/回向根本不进 Producer 的 AgentTask**，因为 Producer 不触发它。所以一旦未来让 Producer 真驱动 Specialist，需新建「把 Specialist turn 的 objective 与 result 落为结构化事件」的桥——目前没有。

---

## 16. Producer Persistent State

**结论：Producer 没有任何多步 workflow 记忆。每个 Producer turn 基本无状态地从项目快照重推导。**

- 无 `ProducerWorkflow`/`ProducerPlan`/`current_goal`/`active_agent`/`completion_state` 数据结构。
- 只有**逐条 ad-hoc `AgentTask`**（`tasks/*.json`），彼此无 parent 链接（见 §10），不构成"工作流"。
- `WorkMemoryStore`（`runtime.py:124-146`）：项目级共享，非 Producer 专有，limit=6 文本笔记。
- `PreferenceStore`（`runtime.py:127`）：项目级偏好。
- **三个 autopilot 文件**（`src/ai/autopilot.py`=`SceneAutopilotManager`、`project_autopilot.py`=`ProjectProductionManager`、`full_project_autopilot.py`=`FullProjectManager`）是**确定性生产执行 Controller**（scene→shot→video→compose，JobQueue 驱动），明确"不是开放式 Agent"，**不调用 SW/CD/Director Agent**，与 Producer Supervisor 是不同关注点。不重叠、不可复用为 Producer 协调层。
- `unified_turn`（`runtime.py:347-491`）每次重新组装上下文——**无跨 turn 步进记忆**。

---

## 17. P0 / P1 / P2 Problems

| 级别 | 问题 | 证据 |
|---|---|---|
| **P0** | Producer 在自己 turn 内**无路径触发任一 Specialist 的真实创作 tool-loop**。三条路径互不相通；真实写 canonical 的 `/turn/stream` 不被 Producer 调用，且对 Producer `permission_denied`。 | `runtime.py:438`(producer→run_inquiry 只读) / `routers/agents.py:305-323`(真实 turn 仅用户直开 + Producer 被拒) / `capabilities.py:73-92`(写能力 not_implemented) |
| **P0** | **无跨 Agent 移交机制**：`instruction` 纯文本、`parent_task_id` 遗留空字段、`suggested_next_actions` 仅返回不消费。 | `models.py:345,350,301` / `runtime.py:718-724` / `inquiry.py:386-391` |
| **P1** | Producer **无多步 workflow 持久状态**（无 goal/active agent/completion），每 turn 无状态重推导。 | `runtime.py:347-491` / §16 |
| **P1** | 任务输入**非结构化**（无 objective/situation/constraints），不足以"像人一样衔接"；且 inspect 路径忽略 instruction。 | `models.py:345` / `capabilities.py:153-164` / §8 |
| **P2** | 真实 Specialist turn 的出向 objective 与回向 result **不进 Producer 的结构化事件**（AgentTask 只覆盖路径①②只读）。未来群聊需新建事件桥。 | §15 |
| **P2** | 无 Producer 级并行 fan-out 驱动器（底层锁/会话已就绪，缺驱动层）。 | §14 |

**七个具体问题逐条回答**：

1. **当前 Producer 能不能真正调用三个成熟专业 Agent？** 不能。只能调同步 inspect adapter（写能力 `NOT_IMPLEMENTED`）；真实 tool-loop 不可达。
2. **是否会绕过它们？** 不会绕过（canonical 所有权干净，§12）；但也**触达不了**。Producer 既不越权、也调不动——卡在中间。
3. **是否能正确串联多个 Agent？** 不能。无移交机制、无 parent_task_id 逻辑、无 suggested_next_actions 消费。
4. **是否能把上一步情况告诉下一 Agent？** 不能。instruction 是裸文本，无 situation/previous_result 字段；inspect 路径连 instruction 都忽略。
5. **是否能根据 Agent 结果动态决定下一步？** 原则上可以（AgentStructuredResult 有 suggested_next_actions），实际上不行（写能力返回 NOT_IMPLEMENTED；无代码消费 suggested_next_actions 作下一 task 输入）。
6. **是否能持续运行完整 workflow？** 不能。无 workflow 状态、无链式、每 turn 无状态。
7. **当前为什么会让人感觉"Producer 没做好"？** 根因是**结构性断开**。Producer 是只读项目问答器 + 手表单委派器。用户说"全流程优化"得到一段建议性对话，零真实推进。「调用与衔接层」完全不存在。

---

## 18. What Can Be Reused

- **canonical 所有权边界**（§12，最干净的部分）——保持 Producer 永不直写 canonical 的红线。
- **per-resource 锁**（story/character/storyboard）+ 会话隔离 + 项目事件总线 `/events`——并行基础设施已就绪。
- **`AgentTask` 持久化结构 + `AgentStructuredResult`/`ConsultationResult` schema**——结构化任务/结果的底座，可扩展而非重建。
- **`unified_turn` 的意图分类**（`classify_turn_intent`）、**真话纪律 prompt 纪律**、**SSE 基建**（`/turn/stream` 事件词汇 `turn.started/reasoning.*/tool.*/assistant.delta/turn.completed`、`/events` 总线）。
- **三个冻结 tool-loop Specialist 本身**（`script_writer_turn`/`character_designer_turn`/`director_turn`）完整且生产可用——只差一个 Producer 侧「调用 + 衔接」层来驱动它们。
- **`AgentTurnResult` schema**（`runtime.py:481-491`：turn_id/intent/assistant_message/events/linked_task_ids/linked_change_session_id/evidence_refs）已建模「Agent 做了什么/事件/关联 id/证据」——可复用为结果契约。
- **`DelegateReq`/`AgentTask` 可扩展**（加 `objective/situation/constraints/previous_result`；填充 `parent_task_id`）而非重建。
- **资源锁粒度**已支持跨域并行；`/turn/stream` SSE 事件词汇可复用为群聊可观测性。

---

## 19. What Should Probably Be Replaced

- **「Producer = 只读问答 + 手表单委派」模型**。Producer 需要自己的 tool-loop（一个 `producer_turn`），内含「委派/衔接工具」能调用真实 Specialist tool-loop 并收回结构化结果。
- **以 sync-capability-dispatch 委派作为 Specialist 调用主机制**——它只能 inspect；应被「真实 Specialist turn 调用」取代（inspect adapter 可保留为只读捷径）。
- **裸字符串 instruction 任务 schema** → 结构化 `{objective, situation, constraints, previous_result, ...}`。
- **无状态 Producer turn** → 需 workflow/plan 状态（current goal / active agent / previous result / completion）。
- **「Specialist turn 结果不回 Producer」缺口** → 需事件/结果桥，让 Producer 消费 `AgentTurnResult`。
- **注**：不替换三个 Specialist（冻结可复用）；不替换 canonical 所有权；不替换锁/SSE 基建。

---

## 20. Questions That Must Be Decided Before Producer V1 Redesign

1. **调用机制**：Producer 通过 Specialist **既有 `/turn`/tool-loop** 复用 `script_writer_turn` 等作为子 turn 来驱动，还是新建一个内部调用 API？即新层是「Producer tool-loop 里调 Specialist turn」还是「围绕既有 turn 的 orchestrator service」？
2. **Producer 形态**：Producer V1 应是新的 `producer_turn`（与三 Specialist 对称、同 Runner 模式），还是不同的编排构造？
3. **任务输入 schema**：采纳结构化 `{objective, situation, constraints, previous_result}`？丰富度到哪？
4. **结果消费**：Producer 消费 Specialist 的完整 `AgentTurnResult`（receipts/trace/events）还是蒸馏摘要？Producer 自己重读 canonical 还是信任结果？
5. **移交语义**：Producer 给下一 Specialist 带简短 situation，还是每 Specialist 自读 canonical（artifact-first，`COLLABORATION_PROTOCOL.md §3`）？文档倾向后者——Producer 只传 objective+situation+constraints。
6. **串行/并行**：确认 Producer V1 默认串行 SW→CD→Director，仅对已证独立的任务并行？还是 v1 永远串行？
7. **持久 workflow 状态**：Producer V1 需要持久 `ProducerWorkflow/Plan` 对象（跨刷新/重启）吗，还是会话历史 + AgentTask 链对 v1 够用？
8. **群聊事件**：新层是否从一开始就发结构化 AgentTask/AgentTurnResult 事件（让群聊 UI 成为后续消费者），还是推迟？
9. **自主 vs 人审**：`producer.md §22` 说 Producer 不自主决策结构变更、变更须经 Specialist capability + 用户 review/apply；但三个新 Specialist（`script_writer_turn` 等）**直接写 canonical**（OCC/真值闸门，无用户 review，AI-7D-R `DIRECT_EDIT_AGENTS`）。需调和：Producer 驱动一个直接写的 Specialist 时，还有用户批准闸吗？还是 Producer 直接 fire-and-forget Specialist turn？
10. **"全流程优化"范围**：Producer V1 真要尝试端到端自主优化，还是只把一个用户请求协调到正确的 1-2 个 Specialist？

---

## 最终结论（一句话）

> **现有 Producer 距离「代替用户在三个专业 Agent 之间自然协调工作」，差的最关键一环是「Producer → 真实 Specialist turn 的调用与衔接层」完全不存在**——既没有调用路径（Producer 自己无 tool-loop、`delegate()` 只走只读 inspect adapter、`/turn/stream` 对 Producer `permission_denied`），也没有结构化任务/结果 schema（裸 instruction + 遗留空 `parent_task_id` + `suggested_next_actions` 不消费）与跨步移交状态（每 turn 无状态重推导）。Producer 目前只是一个**只读项目问答器 + 手表单委派器**：它既没有绕过三个 Specialist（canonical 所有权边界干净），也没有真正触达它们（结构断开）。
