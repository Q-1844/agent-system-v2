# AI-6B · Multi-Agent Foundation

> AGNES 的 8-Agent Runtime 骨架。**不是**全自动 Agent 系统——建立简单、清晰、可扩展的
> 基础框架，先把骨架做正确（spec §39）。封板即停。

## 1. AI-6B 实际实现了什么

在现有稳定的 Production 系统 / AI Workflow / AI-6A Workspace·ChangeSet·Revision 基础上，
新增 `src/agents/` 包（7 文件）+ `routers/agents.py` + 前端 Agent Center，建立：

- 固定 8 个 Agent 的注册、职责边界、权限矩阵；
- Capability Registry，权限检查真实生效（`allowed_capabilities` 纳入校验）；
- 独立隔离的 AgentSession（按 agent+scope 隔离，JSON 持久化）；
- 可追踪的 AgentTask（Producer delegation 走正式 task，同步执行 inspect）；
- ModelProfile 可配置并持久化；
- 一层 delegation（Producer→Specialist 允许；Specialist→Specialist 禁止）；
- read-only inspect 能力接通现有 service（不重写、不写 canonical）；edit/execute 能力注册权限但 adapter 返回 `not_implemented`（预留）；
- 前端 Agent Center（8 卡 + 独立聊天 + Producer 委派）+ Settings「Agents & Models」。

**未做**：autonomous Producer、递归 delegation、reflection/debate、memory/RAG/vector DB、
MCP、LangGraph/CrewAI/AutoGen、Redis/Celery、异步分布式任务、Director Variant 完整功能、
自动 review/retry loop、模型自动选择、重写 Production/JobQueue/Planner/Autopilot/Workspace。

## 2. 八个 Agent 的实际 ID（稳定，勿改）

```
producer              Producer Supervisor   项目总协调者；只 direct project.inspect，其余 delegate
script_writer         Script Writer         故事/场景/剧情
character_designer    Character Designer    角色/视觉/参考图
director              Director              分镜结构（修改须经 Workspace）
production_planner    Production Planner    制作策略（只规划不执行生成）
shot_producer         Shot Producer         按 Plan 执行首/尾/视频（不同 Capability）
continuity_reviewer   Continuity Reviewer   连续性/retry 提案（只审查+提案）
editor                Editor                版本/合成/导出
```

定义见 `src/agents/registry.py::_build_agents`。

## 3. Capability Registry

`src/agents/capabilities.py::build_default_capabilities()` 注册 17 个能力。

**已接通（implemented=True，read-only inspect）**：
`project.inspect`、`story.inspect`、`character.inspect`、`storyboard.inspect`(DEMO)、
`production_plan.inspect`、`shot.inspect`、`composition.inspect`。
这些 adapter 经 `ProjectStore`（`projects/{p}/output/project.json`）与注入的 `plans_reader`
（读 `production_plans.json`）读取，返回 `AgentStructuredResult`，**不调 Agnes、不写项目**。

**预留（implemented=False，注册权限但 adapter 返回 `not_implemented`）**：
`story.edit`、`character.design`、`storyboard.propose_change`（须经 Workspace/ChangeSet，§18 红线）、
`production_plan.create`、`shot.first_frame`、`shot.last_frame`、`shot.video`、
`review.shot`、`review.scene`、`composition.compose`。

## 4. Agent 权限矩阵（§34 基线）

`CapabilityRegistry.can_execute(agent_id, capability_id, operation)` **五重校验**（§修正1）：
agent 存在 + capability 存在 + `capability_id ∈ AgentDefinition.allowed_capabilities` +
`agent_id ∈ CapabilityDefinition.allowed_agents` + `operation ∈ CapabilityDefinition.operations`。
`allowed_capabilities` 是真实授权检查的一环，非展示元数据。enabled/disabled 由
`AgentRuntime` 在执行前单独检查（不在 `can_execute` 内）。

| Agent | 直接可执行（allowed_capabilities） | can_delegate_to |
|---|---|---|
| producer | `project.inspect` | 全部 7 specialist |
| script_writer | `story.inspect`, `story.edit` | — |
| character_designer | `character.inspect`, `character.design` | — |
| director | `storyboard.inspect`, `storyboard.propose_change` | — |
| production_planner | `production_plan.inspect`, `production_plan.create`, `shot.inspect` | — |
| shot_producer | `shot.inspect`, `production_plan.inspect`, `shot.first_frame`, `shot.last_frame`, `shot.video` | — |
| continuity_reviewer | `storyboard.inspect`, `shot.inspect`, `production_plan.inspect`, `review.shot`, `review.scene` | — |
| editor | `composition.inspect`, `storyboard.inspect`, `shot.inspect`, `composition.compose` | — |

关键边界：Producer **不在**任何 domain-inspect 的 `allowed_agents` 中，且 Producer 的
`allowed_capabilities` 只有 `project.inspect` → `can_execute(producer, storyboard.inspect)==False`，
**Producer 只能 delegate、Specialist 执行**。

## 5. ModelProfile

`src/agents/registry.py::ModelProfileRegistry`。默认 4 个 profile（模型 id 从 `config.yaml`
`agnes.models` 派生，保证与配置一致）：`text_default`、`producer_profile`、`character_profile`、
`shot_producer_profile`。`KNOWN_MODEL_IDS` = config 中 agnes 文本/图片/视频模型 id 集合；
`validate()` 拒绝引用未知模型 id 的 profile。

Agent → ModelProfile 映射：`resolve_for_agent` = settings 覆盖优先，否则 AgentDefinition.default。
**Image/Video Model ≠ Agent**，只是 Agent 可用的工具模型。API Key 继续走环境配置，前端不展示 secret。

## 6. AgentSession

`src/agents/sessions.py::AgentSessionStore`，按项目落盘
`projects/{p}/output/agents/sessions/{session_id}.json`（元数据 + messages 历史）。
隔离：`get_or_create(agent_id, scope_type, scope_id)` 按 `(agent_id, scope_type, scope_id)`
复用 session_id，不同 Agent 的聊天历史互不混（§9）。简单 JSON persistence，无向量库/RAG/摘要。
`AgentMessage.status` 承载 `not_configured`/`not_implemented` 等结构化状态。

## 7. AgentTask

`src/agents/tasks.py::AgentTaskStore`，按项目落盘
`projects/{p}/output/agents/tasks/{task_id}.json`。状态：pending→running→completed/failed/cancelled。
`AgentTask` ≠ `JobQueue`（§10）：前者是 AI 任务协调记录，后者是图片/视频/Production 执行任务。
不复杂 DAG、不 distributed engine（§24）。

### 8. Producer delegation 流程（§11，同步）

`AgentRuntime.delegate(project, requester, target, scope_type, scope_id, instruction, capability_id, operation)`：
1. requester 解析；`target ∈ requester.can_delegate_to` 否则 `permission_denied`（**specialist→specialist 在此挡住**——只有 producer 的 `can_delegate_to` 非空）；
2. target 解析（unknown_agent 404）；enabled 检查（disabled_agent 400）；
3. capability 解析（unknown_capability 404）；
4. `can_execute` 五重校验，False → `permission_denied`（**不建 task**）；
5. task pending → running（persist）；target 的 session；
6. `CapabilityContext`（注入 `project_loader` + `plans_reader`）→ `dispatch(cap, ctx)`；
7. 结算：completed（result_summary/artifact_refs/structured_result）或 failed（error）；persist；返回。

全程同步——inspect 是亚毫秒本地读，v1 可接受（§10 明令不做 distributed engine）。

## 9. Persistence 文件位置

| 数据 | 位置 | 持久化 |
|---|---|---|
| Agent 模型设置（全局） | `agents_data/settings.json` | `atomic_write_json`（gitignored） |
| AgentSession + 消息（按项目） | `projects/{p}/output/agents/sessions/{sid}.json` | `atomic_write_json` |
| AgentTask（按项目） | `projects/{p}/output/agents/tasks/{tid}.json` | `atomic_write_json` |

均走 `src/core/atomic.atomic_write_json`（单一来源，AI-6A §十四）。**不塞进 canonical `project.json`**（§26）。
服务重启后 settings/session/task 均不丢。

## 10. 新增 API（`routers/agents.py`）

```
GET  /api/agents                                  8 agent + resolved profile + enabled
GET  /api/agents/profiles                         ModelProfile 列表（Settings 下拉）
GET  /api/agents/{agent_id}/capabilities          该 agent 可用能力 + can_execute
GET  /api/agents/settings                        全局 agent 设置
PUT  /api/agents/settings                        {agent_id, model_profile_id?, enabled?}
GET  /api/project/{p}/agents/sessions            列 session
POST /api/project/{p}/agents/sessions            {agent_id, scope_type, scope_id} -> get_or_create
GET  /api/project/{p}/agents/sessions/{sid}     读消息历史
POST /api/project/{p}/agents/sessions/{sid}/messages  {content} -> assistant AgentMessage
GET  /api/project/{p}/agents/tasks               ?requester_agent=&target_agent=&status=
POST /api/project/{p}/agents/tasks               delegation body -> 同步 -> AgentTask
GET  /api/project/{p}/agents/tasks/{tid}
```

Producer delegation 走正式 `POST .../tasks`（可追踪，§27），不在聊天 endpoint 内偷偷建后台任务。
chat 未配置 Agnes **不是** HTTP 错误——`send_message` 软失败返回 `status="not_configured"` 的
assistant message（200），messages endpoint 正常返回（§修正3）。HTTP 错误仅用于校验类
（unknown/disabled/permission/unknown profile/invalid model）。

### Router import 边界（§修正2）

`src/agents/*` **绝不** import `routers/*`。production-plan 读取经显式注入路径：
`routers/agents.py`（composition root，唯一 import `routers.production_planner._read_plans`）
→ 包装成 `plans_reader` callable → `AgentRuntime.set_plans_reader()` →
`CapabilityContext.plans_reader` → `production_plan.inspect` adapter 调用。无 DI 框架。

## 11. 新增前端入口

- **Agent Center**（`src/views/agents/index.vue`，路由 `/agents`，导航「AI 团队」）：
  8 张 agent 卡（名称/职责/ModelProfile/enabled）；点击进入该 agent 独立聊天区
  （消息列表/输入/发送；当前 project；scope 选择 project/scene/shot + scope_id）。
  Producer 卡额外：「Delegated Tasks」只读表 +「New Delegation」表单（target/capability/scope/instruction）。
- **Settings「Agents & Models」卡**（`src/views/settings/index.vue`）：8 行，每行 profile
  下拉 + Enabled 开关，改动即存（`PUT /api/agents/settings`），刷新仍在。
- API client（`src/api/index.ts`）：8 个 interface + 12 个 `ApiClient` 方法。
- 导航（`src/layouts/MainLayout.vue`）+ 路由（`src/router/index.ts`）各加一项。

## 12. 测试结果

`tests/test_agents.py` 22 个测试全过（engine 级 `tmp_path` + 直接类 + 注入 FakeClient/假 getter）：
8 agent 注册、registry 完整性（allowed_capabilities/owner/allowed_agents 均存在、8 ID 唯一+匹配 spec）、
can_execute allow/deny（含 producer+storyboard.inspect=False 须 delegate）、session 隔离、
get_or_create 复用、task persistence、Producer→Director `storyboard.inspect` delegation、
permission denied（不建 task）、unknown capability、disabled agent、ModelProfile persistence、
unknown model profile、unknown model config、send_message not_configured（软失败）+ FakeClient、
restart recovery、reserved capability not_implemented、canonical 不变。

**全量回归**：`pytest tests/ -q` → **237 passed**（215 AI-6A 基线 + 22 新增），现有
Project/Storyboard/Production Planner/Production Run/Workspace·ChangeSet/JobQueue/Compose 未被破坏。

**真实 HTTP E2E**（TestClient against `_p4_accept`）：
Producer→Director `storyboard.inspect`(scene s03) → task=completed，summary「场景 s03…含 1 个镜头：['s03_c01']」，
artifact_refs 含 shot；`production_plan.inspect` 经注入 plans_reader 优雅返回；权限错误 403。

## 13. 当前尚未实现的能力（预留，§37/§修正：不写成已完成）

- 所有 edit/execute 能力（story.edit / character.design / storyboard.propose_change /
  production_plan.create / shot.first_frame·last_frame·video / review.shot·scene / composition.compose）
  仅注册权限，adapter 返回 `not_implemented`。
- Director Storyboard Variant（KEEP/UPDATE/INSERT/DELETE/REORDER 经 Workspace/ChangeSet）只建权限边界，未实现 Variant Engine。
- chat 用 AgnesClient.chat（共享单例）；未做复杂模型路由/动态 benchmark/成本优化。
- delegation 同步执行（无后台 worker、无 recover_runs）；异步 distributed execution 未做。
- Agent 间不共享全部聊天历史（§9 已遵守）；无长期记忆/RAG/摘要。

## 14. 下一阶段推荐入口（AI-6C）

1. 接通 Director `storyboard.propose_change`：Director Proposal → AI-6A Semantic ChangeSet
   → Validation → Impact → Workspace（复用 `src/revision/*`，§2.4/§35）。
2. 接通 Production Planner `production_plan.create`（包装 `src/ai/production_planner`）与
   Shot Producer `shot.first_frame/last_frame/video`（包装现有 JobQueue/Production，不重写）。
3. 异步 delegation（`asyncio.create_task` + running 状态 + 轮询）——当 capability 调 Agnes/JobQueue 时启用。
4. 开放 Specialist→Specialist 受控链（带防环/防踢皮球/上下文上限）。
5. 复杂模型路由 / 双账号负载均衡 / 模型自动选择。

---

## 文件清单

**新增**：`src/agents/{__init__,models,capabilities,registry,context,sessions,tasks,runtime}.py`、
`routers/agents.py`、`tests/test_agents.py`、`src/views/agents/index.vue`、`AI_6B_MULTI_AGENT_FOUNDATION.md`。

**修改**（最小改动，纯加法）：`api_server.py`（+2 行 import/include）、
`src/api/index.ts`（+interface/+方法）、`src/router/index.ts`（+1 route）、
`src/layouts/MainLayout.vue`（+1 nav item）、`src/views/settings/index.vue`（+1 card）、`.gitignore`（+`agents_data/`）。
