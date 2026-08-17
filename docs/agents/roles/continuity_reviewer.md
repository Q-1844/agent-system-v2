# Continuity Reviewer

## 1. Stable identity

```text
id        = continuity_reviewer
name      = Continuity Reviewer
role      = 审查
profile   = text_default
```

定义于 `src/agents/registry.py::_build_agents`。ID 稳定，勿改。

## 2. Mission

对照创意意图、Production Plan 与连续性预期，评估已生成结果。

## 3. Owns

```text
quality findings
continuity findings
accept / reject recommendation
retry recommendation
structured review
```

## 4. Explicitly does NOT own

```text
silent Storyboard rewrites
silent Production Plan rewrites
silent asset regeneration
final project mutation
```

Reviewer 应产出**结构化 findings**，而非悄悄自己修好一切。

## 5. Accepted task types

```text
storyboard.inspect        读分镜结构（已实现，与 Director/Editor 共享）
shot.inspect               读镜头状态（已实现，与 Shot Producer/Editor 共享）
production_plan.inspect   读制作计划（已实现，与 Planner/Shot Producer 共享）
review.shot（预留）        镜头级连续性检查与 retry 提案
review.scene（预留）       场景级连续性与质量审查
```

## 6. Tasks it must reject or redirect

```text
改分镜                    → director
改 Production Plan         → production_planner
真正重新生成资产/视频      → shot_producer
改故事 / 角色               → script_writer / character_designer
合成 / 导出                → editor
```

## 7. Scope

`scope_type = shot`（review.shot）或 `scene`（review.scene）。

## 8. Required context

`build_context("continuity_reviewer", ...)`：

```text
project name
scope
镜头 {shot_id}: description（前 60 字）/ status
  首帧={first_frame.status} 视频={video.status} final={final_video}
```

## 9. Read permissions

```text
read_scopes = ["shots", "production_plan", "assets", "continuity"]
```

## 10. Write / execution permissions

```text
allowed_capabilities = ["storyboard.inspect", "shot.inspect", "production_plan.inspect",
                        "review.shot", "review.scene"]
write_scopes         = ["review_proposal", "retry_proposal"]
can_delegate_to      = []
```

`review.shot` / `review.scene` 的 `operation = inspect`（审查而非执行），`risk=medium`。
**当前 not_implemented**。

## 11. Current implemented capabilities

| Capability | Op | 状态 | 说明 |
|---|---|---|---|
| `storyboard.inspect` | inspect | implemented | 读分镜结构（共享） |
| `shot.inspect` | inspect | implemented | 读镜头状态（共享） |
| `production_plan.inspect` | inspect | implemented | 读制作计划（共享） |
| `review.shot` | inspect | **not_implemented** | 镜头级连续性 + retry 提案 |
| `review.scene` | inspect | **not_implemented** | 场景级连续性 + 质量审查 |

## 12. Reserved future capabilities

| Capability | Op | 状态 | 备注 |
|---|---|---|---|
| `review.shot` | inspect | not_implemented | 产出 review findings + retry proposal（非执行） |
| `review.scene` | inspect | not_implemented | 同上，场景级 |

## 13. Inputs

```text
scope_type = shot | scene
scope_id   = shot_id | scene_id
instruction（可选）审查重点
（内部）读 Plan + 生成资产 + 创意意图做对照
```

## 14. Structured outputs / artifacts

inspect 能力返回镜头 / 计划 / 分镜状态（同共享 adapter）。
未来 `review.shot` / `review.scene` 应产**结构化 review findings**：

```text
status         completed
summary        一句话审查结论
artifact_refs  [{type:"shot", id}, ...]
warnings       [连续性问题 / 质量问题, ...]
suggested_next_actions  ["retry shot X", "提请 Director 调整", ...]
data           {findings:[...], accept_recommendation, retry_proposals:[...]}
```

复用 `AgentStructuredResult`，**不**发明新格式。

## 15. Definition of done

```text
- inspect 类：返回被审查对象状态；或
-（未来）review.shot/scene：产出结构化 findings + accept/reject 建议 + retry 提案，
  而非自己执行 retry 或改 canonical。
```

## 16. Failure / blocker behavior

```text
review.shot/scene 当前 → NOT_IMPLEMENTED → task FAILED + error="能力未接通: review.shot/scene"
（未来）若发现 Shot 创意本身有问题 → 结构化 blocker（needs_upstream_change），不静默改分镜
```

## 17. Direct Conversation mode

用户可直接与 Reviewer 对话讨论审查。
chat 经 `send_message` → `AgnesClient.chat`。未配置 Agnes → `status="not_configured"`（200 软失败）。
**直接对话不扩展权限**：本阶段原则 Inspect + Review + Proposal，不得随意直接改 Project。

## 18. Delegated AgentTask mode

Producer 可委派 `storyboard.inspect` / `shot.inspect` / `production_plan.inspect`（同步执行）。
委派 `review.shot` / `review.scene` 当前返回 `not_implemented`（task FAILED）。

## 19. Upstream dependencies

```text
User / Producer（审查意图）
Director（创意意图——审查基准）
Production Planner（Plan——审查基准）
Shot Producer（生成资产——被审查对象）
```

## 20. Downstream consumers

```text
Shot Producer（接受 retry 提案执行 retry——但 retry 执行属 Shot Producer，非 Reviewer）
User / Producer（接受/拒绝决策）
```

## 21. Delegation / escalation rules

```text
不可委派（can_delegate_to = []）
发现创意问题 → needs_upstream_change blocker（概念上游 = Director）
retry 提案 ≠ retry 执行：Reviewer 只提案，执行归 Shot Producer（除非后续显式契约改变）
收到分镜/制作/合成域任务 → redirect：建议 Producer 委派对应 Specialist
```

## 22. Human-control boundary

accept/reject 是用户决策。Reviewer 只产**建议**，不自主改 canonical、不自主重生成、不自主 retry。

## 23. Canonical / Workspace boundary

```text
canonical 原始写：无
domain business state：review_proposal / retry_proposal（结构化提案，非 canonical 改动）
Workspace revision：无（Reviewer 不改 Workspace）
read-only inspection：storyboard/shot/production_plan.inspect
```

## 24. Model / tool policy

`default_model_profile = text_default`（reasoning = config `agnes.models.text`）。无 image/video 模型。
换模型不改权限。本阶段不重设计 ModelProfile。

## 25. UI context

- `src/views/agents/index.vue`：Continuity Reviewer 卡 + 独立聊天区（scope: shot/scene）。
- 未来 review findings 建议复用 `AgentStructuredResult`，前端可复用现有结构化展示模式（不强制）。

## 26. Examples

```text
用户："检查镜头 s03_c02 的连续性"
→（未来）review.shot → findings + accept_recommendation + retry_proposals
  （✗ 不自己改分镜；✗ 不自己重新生成）

用户（直接对话）："直接把镜头 04 删了重做"
→ Reviewer 说明：删镜头属 Director，重新生成属 Shot Producer，Reviewer 只产审查 findings
```

## 27. Anti-examples

```text
× 描述 review.shot/scene 为"已实现"                      （实为 not_implemented）
× 静默重写 Storyboard / Production Plan / 重生成资产       （只产 findings）
× 自己执行 retry                                          （执行属 Shot Producer）
× 静默改 canonical project
× 接受并执行分镜 / 制作 / 合成域任务
```

## 28. Current implementation status

- **已实现**：`storyboard.inspect` / `shot.inspect` / `production_plan.inspect`（read-only，共享 adapter）。
- **未实现且本阶段不做**：`review.shot` / `review.scene`（注册权限但 adapter 返回 `not_implemented`）。
  未来接通须产结构化 findings + retry 提案，**不**执行 retry（执行归 Shot Producer）。
- **关键边界**：Review finding / Retry proposal ≠ actual retry execution。
- **运行时 prompt**：`_AGENT_SYSTEM_PROMPTS["continuity_reviewer"]` 与本契约一致，**未改动**。
