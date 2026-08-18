# Production Planner

> **AI-6E 下一阶段实现的目标角色**。本契约 + 末尾「AI-6E implementation readiness」节
> 将成为下一编码阶段的契约。AI-6E.0 **不实现** `production_plan.create`。

## 1. Stable identity

```text
id        = production_planner
name      = Production Planner
role      = 规划
profile   = text_default
```

定义于 `src/agents/registry.py::_build_agents`。ID 稳定，勿改。

## 2. Mission

把一个**已决定**的 Shot 转化为具体、合法、可执行的 Production Plan。

Planner 回答：

> **这个 Shot 应该怎么制作？**

Planner **不**回答：

> **这个 Shot 创意上应该是什么？**（属 Director）

## 3. Owns

```text
production strategy
continuity strategy
opening-source strategy
ending strategy
reference-source decisions
prompt / generation strategy
production-plan fields
```

## 4. Explicitly does NOT own

```text
Storyboard creative redesign   （属 Director）
Character identity              （属 Character Designer）
actual asset generation         （属 Shot Producer）
video acceptance / rejection     （属 Continuity Reviewer / 用户）
final composition               （属 Editor）
```

## 5. Accepted task types

```text
production_plan.inspect   读取场景制作计划与镜头策略（已实现）
production_plan.create    为镜头决定制作策略（AI-6E 已实现，async delegation）
shot.inspect              读取镜头字段（AI-6E §4 修复 mismatch，can_execute=True）
```

## 6. Tasks it must reject or redirect

```text
Storyboard 重设计          → director
角色身份 / 参考图           → character_designer
真正的图片/视频生成         → shot_producer
质量审查 / retry            → continuity_reviewer
合成 / 导出                → editor
```

若 Planner 发现 Shot 本身创意无效或欠定，应返回**结构化 blocker** 而非静默重设计 Shot。
概念上的上游 owner 是 Director。但 Specialist→Specialist 委派**未实现**，故记录为 escalation 关系（见 §21）。

## 7. Scope

**scene 级**。`production_plan.inspect` 与现有 Planner 业务均按 `scene_id` 读取 / 生成计划
（`plans_reader(project, scene_id)` → `production_plans.json` by scene_id）。
一个 `SceneProductionPlan` 含该 scene 全部 `PlannedShot[]`。

## 8. Required context

`build_context("production_planner", ...)`：

```text
project name
scope
（scene scope）各镜头: shot_id / description / opening_source / link_mode
plans_reader → 制作计划: 有/无 + status
```

注意：context 经 `project_loader` 直接读 `scene.shots`，**不**依赖 `shot.inspect` capability。

## 9. Read permissions

```text
read_scopes = ["shots", "assets", "continuity", "production_state"]
```

## 10. Write / execution permissions

```text
allowed_capabilities = ["production_plan.inspect", "production_plan.create", "shot.inspect"]
write_scopes         = ["production_plan"]
can_delegate_to      = []
```

`production_plan.create` 的 `operation = execute`，但**执行 = 产出 Production Plan**（未来执行意图），
**不是**生成资产 / 视频。

## 11. Current implemented capabilities

| Capability | Op | 状态 | 说明 |
|---|---|---|---|
| `production_plan.inspect` | inspect | implemented | 读场景制作计划（scene 级），返回 plan 状态 + 各 shot opening_source/link_mode |
| `production_plan.create` | execute | implemented | 为镜头决定制作策略（AI-6E：经 AgentRuntime delegate async 分支包装中性 `plan_production_business` + 落盘） |
| `shot.inspect` | inspect | implemented | AI-6E §4 已修复 mismatch：production_planner 加入 shot.inspect.allowed_agents，can_execute=True（owner 仍 shot_producer） |

## 12. Reserved future capabilities

| Capability | Op | 状态 | 备注 |
|---|---|---|---|
| （无） | — | — | `production_plan.create` 已于 AI-6E 实现并移入 §11。未来预留：shot 执行 / review 执行 / composition.compose 在各自角色契约。 |

## 13. Inputs

```text
scope_type = scene
scope_id   = scene_id
instruction（可选）制作策略偏好（直接对话）
```

## 14. Structured outputs / artifacts

`production_plan.inspect` 返回：

```text
status         completed（无计划时 has_plan=False）/ not_configured（plans_reader 未注入）
summary        "场景 {scene_id} 制作计划：状态 {status}，N 个镜头策略。"
artifact_refs  [{type:"shot", id}, ...]
data           {scene_id, has_plan, plan_status, shots:[{shot_id, opening_source, link_mode}]}
```

`production_plan.create`（未来）应产出 `SceneProductionPlan`（见 §AI-6E readiness）。

## 15. Definition of done

```text
- production_plan.inspect：返回计划状态 + 各 shot 策略；或
- production_plan.create（未来）：产出合法 SceneProductionPlan（AI 只 opening_source/link_mode/reason，
  其余代码 derive），落 production_plans.json，status=draft 待用户 approve。
```

## 16. Failure / blocker behavior

```text
production_plan.create 当前 → NOT_IMPLEMENTED → task FAILED + error="能力未接通: production_plan.create"
（未来）scene 无 shot → ValueError("请先创建镜头或使用 AI 设计分镜")
（未来）Agnes 不可用 → not_configured / agnes_failed
（未来）AI 输出无法解析 → 一次 repair；仍失败 → invalid_output
若 Shot 创意本身无效 → 结构化 blocker（needs_upstream_change 语义，当前折叠进 FAILED + warnings）
```

## 17. Direct Conversation mode

用户可直接与 Planner 对话讨论制作策略。
chat 经 `send_message` → `AgnesClient.chat`。未配置 Agnes → `status="not_configured"`（200 软失败）。
**直接对话不扩展权限**：用户直接说"删除镜头 04"，Planner 应说明 Storyboard 结构属于 Director（§19）。

## 18. Delegated AgentTask mode

Producer 可委派 `production_plan.inspect`（同步执行）。
委派 `production_plan.create` 当前返回 `not_implemented`（task FAILED）。

## 19. Upstream dependencies

```text
User / Producer（制作策略意图）
Director（Shot 创意——已决定的 Shot 是 Planner 的输入）
Character Designer（角色参考，影响 reference 策略）
```

若 Shot 创意欠定 → 结构化 blocker，概念上 escalate 给 Director（Specialist→Specialist 未实现，见 §21）。

## 20. Downstream consumers

```text
Shot Producer（读 approved Plan 执行首/尾/视频）
Continuity Reviewer（读 Plan 作为审查依据）
用户（approve 制作计划）
```

## 21. Delegation / escalation rules

```text
不可委派（can_delegate_to = []）
Specialist → Specialist 未实现：Director → Planner / Planner → Shot Producer 仅文档为概念链
若 Shot 创意无效 → 返回 needs_upstream_change 语义 blocker（当前折叠进 FAILED + warnings），
  概念上游 owner = Director，但不自动委派回 Director
收到分镜/制作执行/合成域任务 → redirect：建议 Producer 委派对应 Specialist
```

## 22. Human-control boundary

Production Plan 产出后 `status=draft`，须经用户 `approve`（`approve_plan_business`）才 `status=approved`。
**approve 不写 shot.opening_source**——正式值由 AI-4B Executor 执行时应用。Planner 不自主 approve、不自主执行。

## 23. Canonical / Workspace boundary

```text
canonical 原始写：无
domain business state：production_plans.json（projects/{p}/output/ai/，by scene_id）
                     ——独立文件，不塞进 project.json
Workspace revision：无（Plan 不是 Storyboard 修订，是制作意图）
read-only inspection：production_plan.inspect
```

`production_plan.create`（未来）落地到 `production_plans.json`，**不**写 canonical Shot 字段。

## 24. Model / tool policy

`default_model_profile = text_default`（reasoning = config `agnes.models.text`）。无 image/video 模型
（Planner 不生成资产）。`ProductionPlannerWorkflow` 用 `json_mode=True`，`temperature=0.5`，`max_tokens=2048`。
换模型不改权限。本阶段不重设计 ModelProfile。

## 25. UI context

- `src/views/agents/index.vue`：Production Planner 卡 + 独立聊天区（scope: scene）。
- 现有 Production Planner 业务路径（`routers/production_planner.py` 5 端点 + ProductionPlannerStudio 前端 +
  FullProjectManager Autopilot 共享 `plan_production_business`/`approve_plan_business`）独立存在。
- AI-6E `production_plan.create` 应**包装**这些共享 helper，**不**重写 Workflow、**不**复制 system prompt。

## 26. Examples

```text
用户："看看场景 s03 的制作计划"
→ production_plan.inspect(scope=scene:s03) → 返回 plan_status + 各 shot opening/link

用户："为场景 s03 生成制作计划"
→（未来）production_plan.create → SceneProductionPlan(draft) → 用户在 Production Planner UI approve
（✗ 不生成首帧/视频；✗ 不改 Shot 创意）
```

## 27. Anti-examples

```text
× 描述 production_plan.create 为"已实现"               （实为 not_implemented，AI-6E 待做）
× 重新实现 Production Planner Workflow                  （应包装 src/ai/production_planner.py）
× 让 LLM 决定 source_shot_id / generate_first_frame 等执行字段  （须代码 derive）
× 写 shot.opening_source 到 canonical                    （approve 不写，Executor 执行时才应用）
× 自主重设计 Shot 创意                                  （属 Director）
× 接受并执行分镜 / 视频生成 / 合成域任务
```

## 28. Current implementation status

- **已实现**：`production_plan.inspect`（read-only，scene 级，经注入 `plans_reader` 读 `production_plans.json`）。
- **已实现**（AI-6E）：`production_plan.create`——`implemented=True`，经 AgentRuntime delegate 的 async 执行分支
  （`_execute_production_plan_create` → 中性 `plan_production_business` + 落 `production_plans.json`），
  不经 sync `_ADAPTERS` dispatch（同 Director `storyboard.propose_change` 模式）。
- **权限 mismatch 已修复**（AI-6E §4）：AI-6E.0 记录的 mismatch（`production_planner.allowed_capabilities` 含 `shot.inspect`
  但 `shot.inspect.allowed_agents` 不含 planner → can_execute=False）已按 §4 方案 (a) 修复——把 production_planner
  加入 `shot.inspect.allowed_agents`（owner 仍 `shot_producer` 不变）。现 `can_execute(production_planner, shot.inspect, inspect) == True`。
  Planner 仍经 `build_context` 的 `project_loader` 直接读 `scene.shots`（不依赖 shot.inspect 执行），但权限声明现已自洽。
- **运行时 prompt**：`_AGENT_SYSTEM_PROMPTS["production_planner"]` 与本契约一致，**未改动**（§23）。

---

## AI-6E implementation readiness

本节是下一编码阶段（AI-6E — Production Planner Runtime，接通 `production_plan.create`）的契约。
**AI-6E.0 不实现，仅记录。**

### 1. 现有 Planner 实现 / service / workflow（应复用，不重写）

```text
src/ai/production_planner.py
  ProductionPlannerWorkflow.plan_scene(project, scene)   AI 原始规划（overview + per-shot opening_source/link_mode/reason）
  sanitize_plan(scene, ai_result, run_seed, project_name) 确定性 sanitizer，derive 全部执行字段
  rederive_from_edits(scene, plan, edits)                用户编辑后重建 PlannedShot[]
  is_plan_stale(plan, scene) -> Optional[str]            stale 检测（三快照）
  new_run_seed() / choose_candidate(...)                 seeded_random 候选选择（AI-4A 不调，AI-4B Executor 用）
  SceneProductionPlan / PlannedShot（pydantic 模型）

routers/production_planner.py
  plan_production_business(project, scene, project_name, client)  共享 AI 规划 helper
  approve_plan_business(plan, scene)                              共享 approve helper
  5 端点：plan / get / save(PUT) / delete / approve
```

`production_plan.create` 应包装 `plan_production_business`（+ sanitize + 落盘），**不**复制 system prompt、**不**重写 Workflow。

### 2. 现有 `production_plan.inspect`

`implemented=True`，scene 级 scope。经 composition root 注入的 `plans_reader(project, scene_id)`
→ `_read_plans` → `SceneProductionPlan.model_dump()`。返回 plan status + 各 shot opening_source/link_mode。
`plans_reader` 未注入时返回 `not_configured`。

### 3. 当前预留 `production_plan.create`

`implemented=False`，`allowed_agents=[production_planner]`，`operation=[execute]`，`risk=high`。
经 sync `delegate` 派发时 `dispatch` 返回 `not_implemented` → task FAILED。AI-6E 应将其升级为 async
（类似 Director 的 `propose_storyboard_change` 路径，因需调 Agnes）。

### 4. 预期 scope

**scene 级**：输入一个已决定的 Scene（含 Shot[]），输出该 scene 的 `SceneProductionPlan`（含全部 `PlannedShot[]`）。
与现有 Planner 业务一致（plan 按 scene_id 存）。

### 5. 预期结构化输入

```text
scope_type = scene
scope_id   = scene_id
instruction（可选）制作偏好
（内部）project_loader -> Project；scene = resolve(scene_id)
（前置）scene.shots 非空，否则 ValueError("请先创建镜头或使用 AI 设计分镜")
```

### 6. 预期结构化 ProductionPlan 输出

`SceneProductionPlan`（`src/ai/production_planner.py`）：

```text
plan_id, project_name, scene_id
scene_summary_snapshot, scene_characters_snapshot, shot_structure_snapshot   stale 检测用
run_seed（new_run_seed，稳定 int）
overview
shots: [PlannedShot]
  shot_id
  opening_source          AI 决定（independent / reference_previous / inherit_previous）
  link_mode               AI 决定（i2v / keyframes）
  source_shot_id          代码 derive（紧邻上一镜）
  generate_first_frame    代码 derive（opening != inherit_previous）
  generate_last_frame     代码 derive（link == keyframes）
  generate_video          代码 derive（True）
  requires_previous_video_end  代码 derive（reference/inherit）
  auto_select_policy      代码 derive（seeded_random）
  reason                  AI 决定
status: draft | approved（is_stale 派生不持久化）
created_at, approved_at
```

**核心**：AI 只产 `opening_source` / `link_mode` / `reason`；其余 100% 代码 derive（§九/§十/§十三）。

### 7. canonical 写边界

Plan 落 `projects/{p}/output/ai/production_plans.json`（by scene_id），**不**写 canonical `project.json`。
`approve` 只改 `plan.status=approved`，**不**写 `shot.opening_source` / `link_mode` / `source_shot_id`
（§六：plan 是未来执行意图，正式值由 AI-4B Executor 执行时应用）。AI-6E 必须保持此不变量。

### 8. stale-plan 行为

`is_plan_stale`（三快照比对：`scene_summary_snapshot` / `scene_characters_snapshot` / `shot_structure_snapshot`）。
stale 触发：scene.summary / scene.characters / Shot 增删/顺序 / Shot.description / Shot.characters / target_duration_s（>0.5s）变化。
**不触发**：生成资产状态 / video_end_frame / Character.views / JobQueue / 候选变化。
stale → `rederive_from_edits` 不刷新 snapshot（反映创建时结构）；调用方拦截 stale → 须重新生成。
`rederive_from_edits` 保留 `run_seed` / snapshot，但 `approved` → `draft`（编辑后需重新确认）。

### 9. 与现有 Production Planner UI / workflow 交互

现有 5 端点（plan/get/save/delete/approve）+ ProductionPlannerStudio 前端 + FullProjectManager（Autopilot）
共享 `plan_production_business` / `approve_plan_business`（§六十四）。
AI-6E `production_plan.create` 应**包装** `plan_production_business`，与现有路径**共用** sanitize / derive / stale，
**不**建并行实现。前端可新增 Agent 路径触发同一 helper，或复用现有 UI。

### 10. 与 Shot Producer 的关系

```text
Planner = 决定怎么制作（Production Plan）
Shot Producer = 执行怎么制作（读 approved Plan，执行 shot.first_frame / last_frame / video）
```

Plan 是**未来执行意图**；`shot.opening_source` 等正式值由 AI-4B Executor（Shot Producer 执行侧）在执行当前 Shot 时应用，
Planner/approve **不**写。若 Plan invalid/stale/missing，Shot Producer 返回结构化 blocker，**不**即兴重设计。

### 11. 必须保持确定性

```text
source_shot_id / requires_previous_video_end / generate_first_frame / generate_last_frame /
generate_video / auto_select_policy   全部代码 derive，不让模型负责
sanitize_plan 的 sanitizer 规则（第一镜强制 independent / 无效值回退 / 漏 Shot 安全默认）
is_plan_stale 的三快照比对
new_run_seed / choose_candidate 的 seeded_random（可复现）
```

### 12. 需要人工 review 的环节

```text
AI 原始输出 → sanitize_plan（代码）→ draft Plan → 用户在 UI 编辑（PUT rederive）→ 用户 approve（status=approved）
```

approve 是人工门。AI 输出必须经 sanitize，不得直落 plan。

### 13. AI-6E 必须不实现

```text
shot.first_frame / shot.last_frame / shot.video（属 Shot Producer，AI-6E.0 不动）
canonical 写（shot.opening_source 等由 Executor 执行时应用）
Specialist → Specialist 委派 / 自动链
autonomous Planner / 递归 planning
新修订模型 / canonical merge / make-official
```
