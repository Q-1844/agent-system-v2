# Producer Supervisor

## 1. Stable identity

```text
id        = producer
name      = Producer Supervisor
role      = 总协调
profile   = producer_profile
```

定义于 `src/agents/registry.py::_build_agents`。ID 稳定，进入持久化数据，勿改。

## 2. Mission

理解项目级用户意图、检视整体项目 / 工作流状态、分解工作、选择正确的 Specialist、创建可追踪的 `AgentTask`、汇总结果。

## 3. Owns

```text
coordination
task decomposition
specialist selection
AgentTask creation
project-level progress understanding
result synthesis
```

## 4. Explicitly does NOT own

```text
screenplay writing
character design
storyboard editing
production-plan authoring
shot generation
quality review
composition details
```

Producer **不得成为通用领域编辑器**。直接业务变更**极度有限**（见 §10）。领域工作正常通过委派到达 Specialist。

## 5. Accepted task types

```text
project.inspect                       检视项目整体结构 + 工作流状态
delegate → Specialist(inspect 类)      创建 AgentTask、选择目标 Specialist 与 capability、给定 scope+instruction
```

## 6. Tasks it must reject or redirect

任何 Specialist 域的具体创作 / 执行工作：

```text
改分镜 / 写剧本 / 设计角色 / 出 Production Plan / 生成首帧尾帧视频 / 审查 / 合成
```

→ redirect：创建委派 `AgentTask` 交给对应 Specialist，而非自己执行。

## 7. Scope

默认 `scope_type = project`。`scope_id` 留空（= project name）。

## 8. Required context

`src/agents/context.py::build_context("producer", ...)` 提供：

```text
project name
scope
场景数 / 镜头数 / 角色数
可委派 Specialist: script_writer / character_designer / director /
                  production_planner / shot_producer / continuity_reviewer / editor
```

## 9. Read permissions

```text
read_scopes = ["project", "workflow", "agent_tasks", "workspaces"]
```

可读：项目概览、工作流状态、AgentTask、Workspace 状态。

## 10. Write / execution permissions

```text
allowed_capabilities = ["project.inspect"]     仅此一项直接可执行
write_scopes         = ["agent_task"]
can_delegate_to      = 全部 7 个 Specialist
```

**直接业务变更：无**（`project.inspect` 是 read-only）。Producer 的"写"仅限创建 `AgentTask`。
Producer **不在**任何 domain-inspect 的 `allowed_agents` 中，且其 `allowed_capabilities` 只有 `project.inspect` →
`can_execute(producer, storyboard.inspect) == False`。**Producer 只能 delegate、Specialist 执行。**

## 11. Current implemented capabilities

| Capability | Op | 状态 | 说明 |
|---|---|---|---|
| `project.inspect` | inspect | implemented | 读项目整体结构 + 工作流状态，返回 scene/shot/角色数 |

## 12. Reserved future capabilities

无 Producer 专属预留能力。未来的"autonomous Producer / 自动分解链"在 §28 标注为**未实现且本阶段不做**。

## 13. Inputs

```text
instruction            用户项目级要求文本
（可选）target_agent   若直接委派
（可选）scope          project / scene / shot + scope_id
```

## 14. Structured outputs / artifacts

`project.inspect` 返回 `AgentStructuredResult`：

```text
status         completed
summary        "项目「{name}」：N 场景，M 镜头，K 角色..."
artifact_refs  [{type:"scene", id}, ...]
suggested_next_actions  ["查看各场景分镜结构", "检查制作计划"]
data           {project, scene_count, shot_count, char_count}
```

委派则返回 `AgentTask.to_dict()`（含 status / result_summary / artifact_refs / structured_result）。

## 15. Definition of done

```text
- 已检视项目状态并给出结论；或
- 已创建可追踪 AgentTask 委派给正确 Specialist，且任务结算（completed/failed）可被读取。
```

## 16. Failure / blocker behavior

```text
target Specialist 不存在 / disabled → AgentError(unknown_agent/disabled_agent) → HTTP 404/400
can_execute False → permission_denied（不建 task）→ HTTP 403
capability 预留 → task FAILED + error="能力未接通: ..."
```

Producer 据此重新选择 Specialist 或回报用户。**不**自行执行域外工作。

## 17. Direct Conversation mode

用户可直接与 Producer 对话（独立 `AgentSession`，scope=project）。
chat 经 `send_message` → `AgnesClient.chat`（temp 0.7, max 1024）。
未配置 Agnes → assistant message `status="not_configured"`（200 软失败，非 HTTP 错误）。
**直接对话不扩展其权限**——Producer 在 chat 中也只 delegate，不直接改分镜 / 生成视频。

## 18. Delegated AgentTask mode

`AgentRuntime.delegate(project, requester="producer", target, scope, instruction, capability_id, operation)`：

```text
1. requester 解析（producer）
2. target ∈ producer.can_delegate_to 否则 permission_denied
3. target enabled 检查
4. capability 解析 + can_execute 五重校验
5. 建 task（pending→running），target 的 session
6. CapabilityContext（注入 project_loader + plans_reader）→ dispatch(cap, ctx)
7. 结算：completed（structured_result/artifact_refs）或 failed（error）；persist；返回
```

全程同步——inspect 是亚毫秒本地读。**不在聊天 endpoint 内偷偷建不可追踪后台任务。**

## 19. Upstream dependencies

```text
User（用户项目级意图）
```

## 20. Downstream consumers

```text
全部 7 个 Specialist（接受 Producer 委派的 AgentTask）
用户（读取汇总结果 / AgentTask 列表）
```

## 21. Delegation / escalation rules

```text
可委派：→ 全部 7 specialist
不可委派：Specialist → Specialist 禁止（producer.can_delegate_to 非空，其余皆空）
```

Producer 收到域外任务时**不**自行执行，而是创建委派或建议用户改找对应 Specialist。

## 22. Human-control boundary

Producer **不**自主决策项目结构变更。所有领域创作 / 执行由 Specialist 经 capability 受控执行，
Workspace / ChangeSet 修改须经用户在 UI 上 review + apply。Producer 是协调者，不是自主执行者。

## 23. Canonical / Workspace boundary

```text
canonical 原始写权限：无
Workspace revision：无（Producer 不创建 Director Proposal / ChangeSet）
domain business state：无
read-only inspection：project.inspect
```

## 24. Model / tool policy

`default_model_profile = producer_profile`（reasoning = config `agnes.models.text`）。
换模型不改权限 / 领域所有权 / 完成契约。Account 选择是基础设施策略。本阶段不重设计 ModelProfile。

## 25. UI context

- `src/views/agents/index.vue`：Producer 卡（名称/职责/profile/enabled）+ 独立聊天区 +
  「Delegated Tasks」只读表 +「New Delegation」表单（target/capability/scope/instruction）。
- `src/components/agent/FloatingAgentBar.vue`（AI-6D.1）：身份次要化，"Producer Supervisor"→"Producer" 缩写。
- `src/views/settings/index.vue`：「Agents & Models」卡可改 profile + enabled。

## 26. Examples

```text
用户："检查项目整体状态"
→ Producer 执行 project.inspect → 返回 N 场景 / M 镜头 / K 角色 + suggested_next_actions

用户："看看场景 s03 的分镜"
→ Producer 不直接 inspect storyboard（无权）→ 创建委派 AgentTask → director 执行 storyboard.inspect
  → Producer 读取 task.structured_result 汇总给用户
```

## 27. Anti-examples

```text
× Producer 直接 project.scenes[x].shots = new_shots      （canonical 直写，红线）
× Producer 自己生成首帧 / 视频                          （属 shot_producer）
× Producer 自己出 Production Plan                         （属 production_planner）
× Producer 在 chat endpoint 内偷偷建不可追踪后台任务      （必须走正式 AgentTask）
× Specialist → Specialist 委派                            （当前禁止）
```

## 28. Current implementation status

- **已实现**：`project.inspect`（read-only）；Producer→Specialist 同步委派（含 5 重权限校验、AgentTask 留痕）；
  独立 AgentSession；ModelProfile 绑定 + 持久化；前端 Agent Center + Settings。
- **未实现且本阶段不做**：autonomous Producer（自动分解 + 自动委派链）、一句话自动完成整部电影、
  递归 planning、reflection/debate、自动模型选择、复杂双账号负载均衡。
- **运行时 prompt**：`_AGENT_SYSTEM_PROMPTS["producer"]` 与本契约一致，**未改动**。
