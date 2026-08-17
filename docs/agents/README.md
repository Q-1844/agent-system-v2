# AGNES Agent System

> 八个固定 Agent 的操作契约与协作协议入口。本文档是**运行时真相的角色化说明**，
> 不是新框架设计。运行时授权以 `AgentRegistry` / `CapabilityRegistry` 为准。

本目录建立于 **AI-6E.0 — Agent Role Contracts & Collaboration Protocol** 阶段。
该阶段**仅做文档与架构对齐**，不实现新能力、不改运行时行为、不重写 Agent 框架。
目的是在更多 Specialist 获得真实执行能力之前，为现有八个 Agent 建立持久、显式的操作契约。

---

## 1. 架构

```text
Producer Supervisor
        │
        ├── Script Writer
        ├── Character Designer
        ├── Director
        ├── Production Planner
        ├── Shot Producer
        ├── Continuity Reviewer
        └── Editor
```

这是**恰好八个固定角色类型**。本阶段不新增角色。稳定 ID 进入持久化数据，勿改：

```text
producer              script_writer         character_designer
director              production_planner    shot_producer
continuity_reviewer   editor
```

定义见 `src/agents/registry.py::_build_agents`。

---

## 2. 核心操作模型

```text
Agent        = 决策 / 推理层
Capability   = 受控的语义动作面（Agent 的"手"）
Workflow/Service = 确定性执行
Project / Workspace = 共享结构化状态
AgentTask    = Agent 间协调记录（≠ JobQueue 的图片/视频/Production 执行任务）
JobQueue / Dependency / Checkpoint = 执行规则
```

一句话：

```text
Agent decides.   Capability authorizes.   Service / Workflow executes.   Artifact carries durable results.
```

**不得用 LLM 判断取代确定性 Workflow 行为**（如 Shot 依赖、Checkpoint、JobQueue 调度）。
**不得引入新 Agent 框架**（LangGraph / CrewAI / AutoGen / MCP / 向量库 / RAG / reflection loop）。

---

## 3. 核心不变量（红线）

1. **Agent 不直写 canonical project。** 任何结构性修改须经 Workspace / ChangeSet。
2. **LLM 只产创作意图；before / revision / signature / stable shot_id / changeset_id / workspace_id 全部由确定性服务端代码产出。** 这是 AI-6D Director Proposal 已验证的参考模式（见 `director_proposal.py`）。
3. **`allowed_capabilities` 是真实授权检查的一环，不是展示元数据。** `CapabilityRegistry.can_execute` 做五重校验（见下）。
4. **一层委派：Producer → Specialist 允许；Specialist → Specialist 禁止**（v1，所有 Specialist 的 `can_delegate_to=[]`）。
5. **运行时数据不塞进 canonical `project.json`**（AgentSession / AgentTask / Proposal / Settings 各自原子落盘）。
6. **Agent 调用失败不影响已有 Workflow**（adapter 失败 / chat 未配置均为软失败）。

---

## 4. 运行时真相 vs 角色文档

显式区分：

- `AgentRegistry` / `CapabilityRegistry` 是**可执行授权真相**（`src/agents/registry.py`、`src/agents/capabilities.py`）。
- 本目录角色文档描述**预期操作契约**。
- 文档**绝不**声称运行时未授予的权限。若文档与可执行权限出现分歧，以代码为准并**显式记录 mismatch**，而非静默发明行为（见各角色文件 §28 + 本目录一致性审计记录）。

### 权限校验（`CapabilityRegistry.can_execute`，五重）

```text
agent 存在  AND  capability 存在  AND
capability_id ∈ AgentDefinition.allowed_capabilities  AND
agent_id ∈ CapabilityDefinition.allowed_agents  AND
operation ∈ CapabilityDefinition.operations
```

`enabled/disabled` 由 `AgentRuntime` 在执行前**单独**检查（不在 `can_execute` 内）。

---

## 5. Capability 现状（implemented vs reserved）

`src/agents/capabilities.py::build_default_capabilities()` 注册 17 个能力。

**已实现**（8 个）：

| Capability | Owner | Operations | 风险 | 接通方式 |
|---|---|---|---|---|
| `project.inspect` | producer | inspect | low | sync adapter |
| `story.inspect` | script_writer | inspect | low | sync adapter |
| `character.inspect` | character_designer | inspect | low | sync adapter |
| `storyboard.inspect` | director | inspect | low | sync adapter（DEMO） |
| `storyboard.propose_change` | director | execute | high | **async**（AI-6D，经 `AgentRuntime.propose_storyboard_change`，不经 sync dispatch） |
| `production_plan.inspect` | production_planner | inspect | low | sync adapter |
| `shot.inspect` | shot_producer | inspect | low | sync adapter |
| `composition.inspect` | editor | inspect | low | sync adapter |

**预留 / not_implemented**（9 个，注册权限但 adapter 返回 `not_implemented`）：

| Capability | Owner | Operations | 风险 | 备注 |
|---|---|---|---|---|
| `story.edit` | script_writer | execute | high | 须经安全业务接口或 Workspace |
| `character.design` | character_designer | execute | high | 包装现有角色生成能力 |
| `production_plan.create` | production_planner | execute | high | **AI-6E 下一阶段实现** |
| `shot.first_frame` | shot_producer | execute,retry,cancel | high | |
| `shot.last_frame` | shot_producer | execute,retry,cancel | high | |
| `shot.video` | shot_producer | execute,retry,cancel | high | |
| `review.shot` | continuity_reviewer | inspect | medium | |
| `review.scene` | continuity_reviewer | inspect | medium | |
| `composition.compose` | editor | execute | high | 包装现有 Compose/Export |

> 注：`AI_6B_MULTI_AGENT_FOUNDATION.md` §3 曾把 `storyboard.propose_change` 列为预留；
> **AI-6D 已将其升级为 implemented**（async 路径）。以当前代码为准。

预留能力经 `delegate()` 派发时，`dispatch` 返回 `NOT_IMPLEMENTED`，
`AgentRuntime` 将 task 置为 `FAILED` 并写 `error="能力未接通: ..."`，**不建副作用、不动 canonical**。

---

## 6. 对话 vs 委派任务

区分两种使用方式：

```text
Direct Conversation   =  用户 ↔ Specialist 直接对话（AgentSession 隔离）
Delegated AgentTask    =  Producer → Specialist 结构化委派（走 AgentTask，可追踪）
```

二者可能使用同一角色与领域知识，但**委派必须经 AgentTask 留痕**。
直接对话**不扩展** Specialist 的运行时权限——角色边界在两种模式下同等适用（见 `COLLABORATION_PROTOCOL.md` §Direct Conversation）。

---

## 7. 文档结构

```text
docs/agents/
    README.md                      （本文件）
    COLLABORATION_PROTOCOL.md      协作协议：概念链、artifact-first、任务接受、委派边界
    roles/
        producer.md
        script_writer.md
        character_designer.md
        director.md                （AI-6D gold-standard 参考）
        production_planner.md       （含 §AI-6E implementation readiness）
        shot_producer.md
        continuity_reviewer.md
        editor.md
```

每个角色文件统一使用 28 节模板（见任一 `roles/*.md`）。它们是**操作契约**，不是人格传记。

---

## 8. 参考实现

- **Director Proposal Runtime**（`src/agents/director_proposal.py` + `proposal_store.py`）是
  首个完整验证的 Specialist 执行能力，作为未来 Specialist 集成的**金标准参考**：
  语义意图层 + 确定性编译器 + Workspace/ChangeSet 边界 + 三重签名 apply。
- **Production Planner**（`src/ai/production_planner.py` + `routers/production_planner.py`）是
  现有确定性 Workflow，AI-6E 将以 `production_plan.create` 能力**包装**它而非重写（见 `roles/production_planner.md` §AI-6E readiness）。

---

## 9. 运行时入口

- **AgentRuntime**：`src/agents/runtime.py`（薄层，单例 `get_agent_runtime()`）。
- **API 路由**：`routers/agents.py`（composition root，唯一 import `routers.production_planner._read_plans` 注入 `plans_reader`）。
- **持久化**：`agents_data/settings.json`（全局）、`projects/{p}/output/agents/{sessions,tasks,proposals}/*.json`（按项目）。
