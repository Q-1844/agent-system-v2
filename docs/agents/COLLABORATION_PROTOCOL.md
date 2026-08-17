# AGNES Agent Collaboration Protocol

> AGNES 专属协作协议。**不是**通用 multi-agent 论文。
> 本协议描述八个固定 Agent 之间如何通过**持久结构化状态**协作，而非复制聊天原文。

---

## 1. 概念链

```text
User / Producer
        ↓
Script Writer / Character Designer
        ↓ structured project state（故事 / 角色）

Director
        ↓
Storyboard / Director Proposal（Workspace / ChangeSet）

Production Planner
        ↓
Production Plan（per scene，未来执行意图）

Shot Producer
        ↓
Assets / Production Run（JobQueue 执行）

Continuity Reviewer
        ↓
Review Report / Retry Proposal（只审查 + 提案）

Editor
        ↓
Composition / Final Export
```

**并非每个项目都必须调用每个角色。** 这不是刚性强制瀑布。Producer 按需选择 Specialist。

---

## 2. 当前可执行委派边界

**当前可执行规则**：

```text
Producer → Specialist        允许（producer.can_delegate_to = 全部 7 specialist）
Specialist → Specialist      禁止（所有 specialist.can_delegate_to = []）
```

`AgentRuntime.delegate` 在 `can_delegate(requester, target)` 处挡住 Specialist→Specialist：
只有 producer 的 `can_delegate_to` 非空。

### 概念上的上下游（仅文档，非可执行委派链）

以下关系**仅作为上下游语义**记录，**不是**已实现的委派链：

```text
Director    → Production Planner   （Director 决定 Shot 创意，Planner 决定如何制作）
Planner     → Shot Producer        （Planner 出 Plan，Shot Producer 执行）
Reviewer    → Shot Producer        （Reviewer 出 retry 提案，Shot Producer 执行 retry）
```

**禁止**实现递归委派、自动任务链、Specialist→Specialist 自动调用。这些属于后续显式阶段。

---

## 3. Artifact-first 交接

**核心规则**：Agent 之间通过**持久结构化状态**交接，**不**通过复制长聊天原文。

所有 Agent 共享的是结构化 artifact：

```text
Project          Workspace         ChangeSet         AgentTask
Storyboard       ProductionPlan    ProductionRun     Asset
ReviewResult     Composition state
```

**不是**：

```text
Producer 的全部对话 + Director 的全部对话 + Character 的全部对话
```

例如：Character Designer 得到 `Character LinXia, appearance_version=4`，
Director 之后应**读取结构化 Project / Artifact**，而非收到 Character Agent 二十分钟聊天原文。

### Specialist 返回结构化结果

Specialist 委派完成后返回 `AgentStructuredResult`（`src/agents/models.py`）：

```text
status                 completed | not_configured | not_implemented | failed
summary                一句话结论
artifact_refs          [{type, id}, ...]   指向持久 artifact
warnings               [str, ...]
suggested_next_actions [str, ...]
data                   demo / 载荷字段（如 scene_id / issues）
```

Producer 读取结构化结果即可理解决策，无需解析另一位 Agent 的长篇聊天。

**本阶段不发明新的持久化格式**——复用现有 `AgentStructuredResult` / `AgentTask` / `DirectorProposal` 等结构。

---

## 4. 任务接受契约

每个角色收到**域外任务**时，必须**拒绝或重定向**，不得机会主义地执行另一位 Specialist 的工作。

概念语义（§17）：

```text
complete              任务在本角色域内并完成
blocked               本可执行但被前置条件阻塞（如缺资产 / 缺 Plan）
needs_upstream_change 需要上游（如 Director）先改 Shot 创意
needs_specialist      属于另一位 Specialist 的域
not_supported         能力本身未实现 / 不存在
```

### 运行时映射（不新增 enum）

**本阶段不新增运行时 enum。** 复用现有结构化状态，语义映射如下：

| 概念语义 | 运行时承载 | 备注 |
|---|---|---|
| complete | `TaskStatus.COMPLETED` + `ResultStatus.COMPLETED` | 已有 |
| not_supported | `ResultStatus.NOT_IMPLEMENTED` → task `FAILED` + `error="能力未接通"` | 预留能力经 `delegate` 派发时如此结算 |
| not_configured（软失败） | `ResultStatus.NOT_CONFIGURED` | chat 未配置 Agnes |
| blocked / needs_upstream_change / needs_specialist | task `FAILED` + `error`/`warnings` 文本描述 blocker | **运行时无专用 enum**，当前以 FAILED + 描述承载 |

> **记录的运行时 gap**：`blocked` / `needs_upstream_change` / `needs_specialist` 当前只能折叠进 `FAILED`。
> 这三个语义在 AI-6E 及以后接通真实执行能力时，可能需要专用结构化状态（如 retry 提案、上游变更请求）。
> **AI-6E.0 不实现**——仅记录语义与 gap。

---

## 5. Artifact-first vs Direct Conversation

### Direct Conversation（用户 ↔ Specialist）

每个 Specialist 都可被用户直接访问（独立 `AgentSession`，按 `(agent_id, scope_type, scope_id)` 隔离）。

**直接对话不扩展运行时权限。** 角色边界在直接对话与委派任务两种模式下**同等适用**。

示例（§19）：

- 用户直接对 Production Planner 说"删除镜头 04"：Planner 应说明 Storyboard 结构属于 Director，而非假装拥有该操作。
- 用户直接对 Shot Producer 说"把这场景改慢并加特写"：Shot Producer 不得静默重设计 Storyboard。

### Delegated AgentTask（Producer → Specialist）

Producer 通过 `POST /api/project/{p}/agents/tasks` 创建正式 `AgentTask`，同步执行 inspect capability，
返回结构化结果。**不在聊天 endpoint 内偷偷建不可追踪后台任务**。

两种模式可能用同一角色与领域知识，但委派必须经 `AgentTask` 留痕。

---

## 6. Canonical / Creative Workspace 边界

每个角色文档必须显式声明其变更边界。统一区分四层：

```text
1. read-only inspection        只读检视（所有 inspect 能力）
2. domain business state        领域业务状态（如 Production Plan 独立文件）
3. Creative Workspace revision  创意修订（Workspace / ChangeSet，AI-6A）
4. canonical project            正式项目（project.json）
```

**规则**：

- **没有任何 Agent 仅凭文档获得 canonical 原始写权限。**
- Director 的 Workspace / ChangeSet 路径是参考模式（`director_proposal.py`）：
  `Director → 语义 proposal → ChangeOperation/ChangeSet → Preview/Impact → Workspace →（apply）`。
  apply 复用 `cs_engine.apply`（三重校验 revision + before_signature + after_signature）。
- **禁止**实现 canonical merge / "make official" / 新修订模型。这些不在本阶段。

---

## 7. 任务接受：redirect 而非越权

当任务落在另一位 Specialist 域内，当前可执行行为：

- 委派任务：返回 `FAILED` + `warnings` 描述应委派给谁（Producer 据此重新选择 Specialist）。
- 直接对话：在 chat 回复中说明该操作属于哪个角色，建议用户改委派或改找对应 Specialist。

**不得**：悄悄执行另一位 Specialist 的领域工作（如 Planner 改分镜、Shot Producer 改 Storyboard）。

---

## 8. Model policy 次于 role policy

`ModelProfile`（`src/agents/registry.py::ModelProfileRegistry`）**不定义**角色权限。

更换推理模型**不得**改变：

```text
Agent 权限 / 领域所有权 / 写边界 / 委派权限 / 完成契约
```

一个更强的模型仍是同一个角色。

- **Image / Video Model ≠ Agent**，只是 Agent 可用的工具模型。
- **Account 选择是基础设施策略，不是角色身份。** API Key 走环境配置，前端不展示 secret。
- **本阶段不重设计 ModelProfile。**

默认 4 个 profile（模型 id 从 `config.yaml` `agnes.models` 派生）：
`text_default` / `producer_profile` / `character_profile` / `shot_producer_profile`。
`resolve_for_agent` = settings 覆盖优先，否则 `AgentDefinition.default_model_profile`。

---

## 9. 运行时 prompt 保持简洁

现有 Agent system prompt（`src/agents/registry.py::_AGENT_SYSTEM_PROMPTS`）已短而稳定。

**不**把本目录角色文档整篇塞进运行时 system prompt。运行时 prompt 应保持高信号、简洁：
`identity / mission / owns / does-not-own / scope / tool-write-boundary / output-expectation`。

仅当现有 system prompt 与新契约**实质矛盾**时才最小修正。若无必要，代码不动。
（AI-6E.0 一致性审计结论：现有 prompt 与契约一致，**未改动**——见各角色 §24 + 最终报告。）
