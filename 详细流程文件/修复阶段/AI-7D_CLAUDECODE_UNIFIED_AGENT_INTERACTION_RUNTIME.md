# AI-7D — Claude Code Execution Document
## Unified Agent Interaction Runtime · Frontend/Backend Integration First

> 执行者：Claude Code
>
> 目标：**先把所有 Agent 前端入口和现有真实后端统一接通。**
>
> 重要：本阶段不是“聊天 Prompt 美化”；不是重做 7A/7B/7C；不是增加 Reviewer。
>
> 每个 Checkpoint 完成后必须 STOP，让用户亲自发消息验收。只有用户明确 PASS 才继续。

---

# 0. Start Rule

先读：

- 当前 git status / branch / HEAD；
- 项目内最新 AI-7A / 7B / 7C / 7C-H handover 或 completion 文档（如果存在）；
- `CLAUDE.md` / repo instructions；
- 所有 Agent runtime / router / frontend store / Agent Center / Team Chat 相关实现；
- 当前 tests / e2e scripts。

不要从历史文件名猜实现。

你必须先从**真实代码**画出调用图，再改代码。

---

# D0 — Full Interaction Entry Audit

## D0.1 找出每一个用户可见 Agent 输入入口

至少扫描：

- Agent Center / AI Team
  - Producer Direct Chat
  - Script Writer
  - Character Designer
  - Director
  - Production Planner
  - Shot Producer
  - Continuity Reviewer
  - Editor
- Floating Agent Bar
- Storyboard contextual agent / Director conversation
- Character page contextual agent（如存在）
- Team Chat / Group Chat
- 任何复用 `sendAgentMessage`, `send_message`, `messages` endpoint 的组件
- 任何旧的 Agent Session 页面/组件

对每个入口记录：

```text
Frontend component
Frontend store/action
HTTP endpoint
Router function
Runtime/service
LLM client call
Can execute tools?
Can delegate?
Can create ChangeSession?
Can read runtime truth?
Persistence store
Refresh behavior
Backend restart behavior
```

输出一张 `AI-7D_ENTRY_MAP.md`。

## D0.2 特别核查 legacy path

定位当前类似：

```text
POST .../agents/sessions/{sid}/messages
→ runtime.send_message()
→ client.chat(...)
→ raw assistant text persisted
```

的路径。

确认：

- 是否只有某几个入口使用；
- 是否所有 Specialist 都受影响；
- prompt 是否声明了模型实际无法执行的 capability；
- 是否存在 tool-call JSON 被当作普通文本保存；
- 是否有“状态”从模型文本而不是 backend object 派生。

## D0.3 不改代码就先做一次浏览器复现

至少复现：

1. Producer：
   `现在剧本有多少场？先不要修改。`
2. Producer：
   `剧本内容太少了，请继续添加剧本内容。`
3. Script Writer：
   `先看看当前中段内容，再告诉我哪里最薄弱。`

记录：

- Network request；
- session id；
- backend durable object；
- UI 最终文本；
- 是否出现 JSON；
- 是否真的发生 inspect / ChangeSession。

## D0 Gate

D0 只允许极小的日志/测试辅助代码，不做正式修复。

完成后 STOP，给用户：

- 入口图；
- 至少一个真实坏路径证据；
- 你准备修改的最小架构边界。

`STOP — waiting for manual/architecture acceptance.`

---

# D1 — Unified Direct Chat Turn Runtime: Read / Conversation

## D1.1 核心原则

不要简单在 `send_message()` 里加：

```python
if looks_like_change:
    create_changesession()
```

AI-7D 需要一个明确的统一 Turn Runtime。

概念上：

```text
Direct Chat user turn
        ↓
UnifiedAgentTurnRuntime
        ↓
TurnIntent
 ┌─────────────┬─────────────┬─────────────┬──────────────┐
 conversation    inquiry        change        runtime_query
```

如果现有项目已经有合适的 `agentUi` intent/router，复用它，不要重复造。

## D1.2 TurnIntent

使用结构化结果，不解析模型自由文本中的伪 JSON。

建议至少：

```text
conversation
project_inquiry
creative_change
runtime_query
runtime_control
```

每个结果要带：

```text
intent
confidence
target_agent/module
requires_project_context
candidate_scope
```

不要让 LLM 直接返回任意函数名当执行协议。

## D1.3 conversation

真正普通聊天：

> “你觉得这个故事现在最大的问题是什么？”

可以由当前 Specialist 正常回复。

但如果回答依赖项目事实，必须先进入真实 project context / inspect。

## D1.4 project_inquiry

接入现有 AI-7A read/inquiry 能力。

要求：

- 真读取 Project / Scene / Shot / Character / Plan；
- 需要 Specialist 时产生真实 delegation / AgentTask；
- 工具结果重新进入模型上下文；
- 最终得到自然语言 reply；
- UI 永远不显示 tool-call JSON；
- 工具失败要自然且真实地说明失败。

### 关键约束

```text
model says "I should inspect"
≠
inspect happened
```

只有真实 backend tool/task 记录存在，UI 才能表示“已查看/正在查看”。

## D1.5 Direct Chat response contract

建议统一为：

```python
AgentTurnResult:
    turn_id
    session_id
    agent_id
    intent
    assistant_message
    events[]
    linked_task_ids[]
    linked_change_session_id?
    evidence_refs[]
```

`assistant_message` 是用户看到的自然语言。

`events` 是真实后台事件，不是让模型编的叙述。

## D1.6 自然语言最小要求（只做基础，不做 Trae 深度优化）

禁止用户看到：

- function/tool JSON；
- capability id；
- Python repr；
- raw exception stack；
- “请去另一个输入框重新输入”。

允许短暂自然过渡：

> “我先看一下现有剧本结构。”

但只有在**本 turn 真的随后执行了 inspect**时才能说。

完成后必须有第二段真实结果：

> “现在共有 7 场，中段主要集中在第 3–4 场，篇幅明显比开头短……”

不要停在“我去看一下”。

## D1.7 Compatibility

旧的 `/messages` API 如果已有外部依赖，可以：

- 保留 endpoint；
- 内部迁移到 Unified Turn Runtime；
- 或明确 deprecated wrapper。

不得保留两个行为不同的 Direct Chat runtime。

## D1 Automated Browser Gate

真实 Vite + 真实 FastAPI。

至少：

### Case D1-A
Producer：
`现在剧本有多少场？先不要修改。`

断言：

- 没有 ChangeSession；
- 有真实 project inquiry/inspect；
- UI 最终为自然语言；
- 没有 JSON/tool syntax。

### Case D1-B
Script Writer：
`先读一下现在的剧本，中段为什么显得短？先别改。`

断言：

- current agent 保持 Script Writer；
- 有真实 script context；
- 回复引用实际项目内容；
- 不自动修改。

### Case D1-C
普通交流：
`如果只从叙事节奏看，你更担心开头还是中段？`

断言：

- 没有不必要的 ChangeSession；
- 如果需要事实，会 inspect；
- 最终只显示自然 reply。

## D1 User Manual Gate

服务保持运行。

让用户亲自发送：

1. `现在剧本有多少场？先别改。`
2. `你觉得目前中段最大的问题是什么？`
3. `刚才你为什么这么判断？`

要求三轮之间有上下文承接。

然后 STOP。

---

# D2 — Direct Chat Creative Change → Real ChangeSession

## D2.1 核心行为

用户在任意 Specialist Direct Chat 说修改需求，不得：

- 直接改 canonical；
- 只聊天；
- 让用户切去 Floating Bar；
- 让用户切 Team Chat；
- 输出假 tool call。

必须：

```text
Direct Chat
↓
creative_change
↓
existing ChangeRequest / ChangeSession
↓
current Specialist as origin
↓
Producer coordination if needed
↓
Module Proposal(s)
↓
Unified Plan / Preview
↓
user decision
↓
existing AI-7C execution
```

## D2.2 Origin metadata

真实 ChangeSession 必须记录：

```text
source_surface = direct_chat
origin_agent_id
origin_session_id
origin_turn_id
user_request
project
scope
```

这允许：

- Direct Chat inline 恢复；
- Team Chat 展示真实协作；
- 刷新后找到同一个请求；
- 不重复创建。

## D2.3 同模块 vs 跨模块

### 同模块例

Script Writer：
`把中段再加两场，但不要改变结局。`

可以由 Script Writer 作为 origin，进入真实 change flow。

### 跨模块例

Character Designer：
`把火焰机甲改成蓝色涂装，并同步受影响镜头。`

必须自动升级协调：

```text
Character Designer
→ Producer / dependency analysis
→ Character + Director/Storyboard
→ Unified Plan
```

用户不管理 Agent 队伍。

## D2.4 Inline Change Card

Direct Chat 中显示一个真实卡片：

```text
正在整理修改方案
来源：Script Writer
状态：分析影响 / 等待方案 / 等待你确认 / 执行中...
```

卡片状态只来自 ChangeSession/Execution。

允许：

- 查看方案；
- 批准；
- 拒绝；
- 打开详情。

“要求调整方案”最好通过当前 Direct Chat 自然语言继续说：

> “第二场不要加新角色。”

这应该关联到同一 change context / feedback flow，不应要求用户去 Team Chat 输入。

## D2.5 幂等

用户双击发送、前端 retry、F5：

同一个 `client_request_id / turn_id` 不得创建重复 ChangeSession。

## D2 Automated Browser Gate

### D2-A Producer
`剧本内容太少了，请继续添加剧本内容。`

断言：

- 真 ChangeSession；
- 真 Script Writer proposal path；
- UI 不出现 JSON；
- 不要求去 Floating Bar；
- inline card 指向真实 session id。

### D2-B Script Writer
`中段再增加两场，但结局保持不变。`

断言 origin_agent = Script Writer。

### D2-C Character Designer cross-module
`把火焰机甲改成蓝色，并同步所有受影响镜头。`

断言：

- 真实 dependency analysis；
- 至少 Character + Storyboard/Director 相关模块被纳入；
- 不扩大到无关场景；
- Team collaboration events 有真实来源（D4 前可先后端记录）。

## D2 User Manual Gate

让用户亲自发送：

1. Producer：`剧本内容太少了，请继续添加剧本内容。`
2. Script Writer：`中间再加两场，先给我方案，不要马上改。`
3. Character Designer：`把火焰机甲改成蓝色，并同步受影响镜头。`

STOP。

---

# D3 — Runtime Query / Control Truth

## D3.1 runtime_query

例如：

- `刚才那个修改做到哪了？`
- `现在是在生成角色图还是做分镜？`
- `还有几个镜头没完成？`

必须读取真实：

```text
ChangeSession
ChangeExecution
ProductionRun
JobQueue
```

再由 Agent 用自然语言表达。

严禁模型根据聊天记录猜。

## D3.2 runtime_control

至少支持与现有后端已经稳定的控制语义对齐：

- 停止当前任务；
- 重新规划；
- 恢复可恢复任务；
- 批准 / 拒绝（如现有 UI/协议允许自然语言入口）。

模型只负责理解意图。

真正执行必须调用现有 backend action。

## D3.3 运行状态消息

自然语言可以是：

> “角色参考图已经同步完成，现在正在为受影响的两个场景重新准备制作计划。”

但其数据必须来自真实 stage。

不显示虚假百分比。

## D3 Automated Browser Gate

- 执行中问进度；
- F5 后问同一问题；
- backend restart 后问同一问题；
- 请求停止后确认旧任务不复活；
- 再次问“刚才那个任务呢？”必须回答真实 cancelled/completed/blocked。

## D3 User Manual Gate

用户执行一条真实 change，然后连续问：

1. `现在做到哪了？`
2. `刚才实际完成了什么？`
3. `如果我现在停止，会停掉什么？`

如用户选择测试停止，再验证停止后的真实状态。

STOP。

---

# D4 — AI-only Team Chat / Coordination Room

## D4.1 UI 改造

Team Chat：

- 删除/隐藏 user composer；
- 不能通过 Enter / API 从 Team Chat 发送 user message；
- 顶部明确说明：
  `AI 制作团队协作记录 · 与某位 Agent 沟通请打开其私聊`
- 每个 Agent message 可点击打开 Direct Chat；
- 允许 filter：全部 / 当前任务 / 仅异常（可选，非必须）。

## D4.2 不是“假群聊”

禁止为了好看生成一段假的：

```text
Producer: 我们开始吧
Director: 好的
Script Writer: 收到
```

Team Chat 里的每条消息必须绑定真实协作事件。

建议数据：

```python
AgentCollaborationMessage:
    message_id
    project_name
    thread_id
    sender_agent_id
    recipient_agent_ids[]
    message_kind  # request / finding / question / result / warning / handoff
    text
    source_task_id?
    source_change_session_id?
    source_execution_id?
    evidence_refs[]
    created_at
```

## D4.3 “真实对话”与“结构化协议”同时存在

后台可以继续使用 structured result。

但应让实际的 request/result 形成可读协作消息：

Producer → Script Writer：
> “用户希望扩写中段，但保留现有结局。请先检查当前节奏，提出只影响剧本结构的方案。”

Script Writer → Producer：
> “中段目前只有两场，冲突升级太快。我建议插入一场调查和一场失败尝试；不会改结局。”

如果随后 dependency analysis 发现镜头影响：

Producer → Director：
> “剧本方案会新增两场。请确认分镜需要新增哪些镜头，现有镜头哪些可以保持。”

这些不是 Chain-of-Thought，而是**真实工作交流**。

## D4.4 不把 raw Direct Chat 复制进去

用户对 Script Writer 说的原话只保留在 private session。

Team Chat 只显示必要的团队任务表达。

## D4.5 上下文预算

Team Chat transcript 是“用户可见记录”，不是每个 Agent 的完整 Prompt。

Specialist 输入继续使用 bounded Context Pack：

- 当前 request；
- relevant project state；
- relevant prior result summaries；
- dependency facts；
- exact question；
- references。

不要把整个群聊无限拼接。

## D4.6 Decision UX

用户可以在 Team Chat 观察。

若出现需要用户决定：

- 可以显示 Review Card；
- `Approve / Reject` 可以是按钮；
- “补充说明/调整” → 打开 Producer Direct Chat，并自动带上当前 session context。

Team Chat 自身不出现用户文本输入框。

## D4 Automated Browser Gate

触发跨模块 change：

`把火焰机甲改成蓝色，并同步受影响镜头。`

断言：

- Team Chat 自动出现真实 Agent 协作；
- message 顺序与 persisted task/session 一致；
- F5 后仍在；
- 用户不能在 Team Chat 发消息；
- 点击 Director/Script Writer 可跳到对应 Direct Chat；
- 不展示 hidden reasoning / raw JSON；
- 不把用户私聊全文复制进去。

## D4 User Manual Gate

用户只需要：

1. 在一个 Specialist 私聊发跨模块请求；
2. 打开 Team Chat；
3. 观察 Agent 是否像真正团队一样交流；
4. 点击其中一个 Agent 回到私聊继续追问。

STOP。

---

# D5 — Conversation Persistence & Memory Audit

## D5.1 先证明已有记忆层

不要直接新增 vector DB。

列出现有：

- AgentSession / messages；
- TeamThread/messages；
- AgentTask；
- ChangeSession；
- ChangeExecution；
- project-scoped context summary/ref；
- frontend restore logic。

给出：

```text
What survives F5?
What survives backend restart?
What is per-agent?
What is per-project?
What is current truth?
What is merely conversation history?
```

## D5.2 Direct Chat continuity

同一个 session：

User:
`我希望中段更长，但结局不要动。`

稍后：
`那就按刚才的方向，不过不要增加新角色。`

Agent 必须理解“刚才的方向”。

F5 / remount 后仍然成立。

## D5.3 长历史压缩

如果当前实现直接把完整 session 发给模型，先测长会话。

只有确认出现 context 膨胀/截断问题时，才允许增加：

```text
ConversationMemorySummary
```

建议字段：

```text
project_name
agent_id
session_id
summary_revision
stable_user_decisions[]
open_threads[]
important_project_refs[]
source_message_ids[]
updated_at
```

必须满足：

- project scoped；
- provenance；
- 可重建；
- summary 不覆盖 project truth；
- 后端项目当前事实冲突时，以 backend 为准；
- 不记录模型未验证推测。

## D5.4 不做的 Memory

本阶段禁止：

- cross-project memory；
- vector DB；
- embedding store；
- 自动学习人格；
- 把所有历史聊天永久塞入 prompt；
- Agent 自行改写其岗位规则。

## D5 Automated Gate

- 10+ turn direct conversation；
- F5；
- route away/remount；
- backend restart；
- project switch A→B→A；
- 不串项目；
- 当前任务状态仍真；
- “我们刚才说到哪里？”回答与真实历史一致。

## D5 User Manual Gate

用户与同一 Agent 连聊至少 6–10 轮，然后：

1. F5；
2. 切到别的 Agent 再切回；
3. 问 `我们刚才说到哪里？`
4. 问 `我之前明确说过哪些东西不要改？`

STOP。

---

# D6 — Full Integration Closure

## D6.1 Full entry matrix

重新运行 D0 入口图。

每个 user-visible input 必须标成：

```text
UNIFIED
```

不得残留：

```text
legacy raw-send_message behavior
```

若保留旧 endpoint，只能是 wrapper，语义必须统一。

## D6.2 Golden Paths

至少跑：

### GP1 — Read
Producer → read current story → answer.

### GP2 — Specialist change
Script Writer private → change request → plan → user approval → execution.

### GP3 — Cross-module
Character Designer private → character change → Team Chat real collaboration → bounded downstream impact.

### GP4 — Runtime truth
Execution中 → ask progress → F5 → ask again.

### GP5 — Cancel/restart
执行中 stop → safe cancellation → new request after safe stop.

### GP6 — Memory
multi-turn → F5/backend restart → continue conversation.

## D6.3 Browser requirements

必须使用真实前端 + 真实 FastAPI。

自动化优先 Playwright / 项目现有 E2E。

对于 LLM：

- deterministic wiring tests 可以在模型 client boundary mock；
- 不得 mock frontend routes / fake ChangeSession；
- 如果环境允许，至少做一轮 live model browser acceptance；
- 若 live model 无法跑，明确标为 `NOT PROVEN`，交给用户手测，不得写 PASS。

## D6.4 Regression

至少：

- existing Agent tests；
- AI-7A / 7B / 7C / 7C-H focused regressions；
- frontend production build；
- existing browser tests；
- new AI-7D tests。

不要修无关历史 warning。

## D6.5 Final report

写：

`AI-7D_CLAUDECODE_COMPLETION_REPORT.md`

必须包含：

1. baseline；
2. entry map before/after；
3. unified turn runtime；
4. Direct Chat；
5. Inquiry；
6. ChangeSession bridge；
7. Runtime truth；
8. Team Chat AI-only；
9. persistence/memory；
10. frontend routes/stores；
11. browser evidence；
12. user manual gates；
13. tests；
14. known limitations；
15. explicit statement:
   - no fake tool JSON
   - no user messages in Team Chat
   - no second change-plan system
   - no canonical direct mutation
   - no AI-7E work

完成后：

`STOP — Claude Code integration phase complete; waiting for user acceptance before Trae quality phase.`

---

# Appendix A — User-visible wording baseline

这些只是基线，不是最终人格润色。

### Before real inquiry
可说：
> “我先看一下当前剧本结构。”

前提：本 turn 后台真的启动/完成 inspect。

### After inquiry
> “我看过了。现在中段主要集中在第 3、4 场，冲突升级很快，但缺少一次失败或反转，所以体感会短。”

### Change request accepted
> “可以。我会先按‘扩写中段、保留结局’整理一个修改方案，不会直接改正式项目。方案出来后我就在这里给你看。”

前提：已经创建真实 ChangeSession。

### Runtime query
> “方案已经批准，现在角色参考同步完成，正在准备受影响场景的制作计划。”

前提：完全来自 persisted runtime state。

### Failure
> “剧本读取失败了，所以我现在不能可靠判断中段结构。你可以重试；我不会假装已经看过。”

---

# Appendix B — 关键实现思想

- **LLM 决定语义，代码决定权限与执行边界。**
- **工具调用是 runtime 事件，不是聊天文本。**
- **ChangeSession 是所有创作修改唯一通道。**
- **Runtime Truth 来自 persisted backend objects。**
- **Team Chat 展示真实协作，不展示隐藏推理。**
- **Session history ≠ project truth ≠ learned long-term memory。**
