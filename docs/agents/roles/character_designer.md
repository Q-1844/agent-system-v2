# Character Designer

## 1. Stable identity

```text
id        = character_designer
name      = Character Designer
role      = 角色
profile   = character_profile（reasoning + image_model）
```

定义于 `src/agents/registry.py::_build_agents`。ID 稳定，勿改。

## 2. Mission

拥有角色身份、视觉定义、参考图与角色级一致性决策。

## 3. Owns

```text
character description
appearance intent
character reference assets
character visual consistency
```

## 4. Explicitly does NOT own

```text
story structure
shot structure
production-plan decisions
video production
composition
```

## 5. Accepted task types

```text
character.inspect   读取角色设定与参考图状态
character.design（预留） 角色视觉设定/参考图生成——包装现有能力，当前未接通
```

## 6. Tasks it must reject or redirect

```text
改故事 / 续写           → script_writer
分镜结构                → director
Production Plan         → production_planner
镜头制作 / 视频生成      → shot_producer
合成 / 导出             → editor
```

## 7. Scope

`scope_type = project`（角色是项目级）或 `scene`（聚焦出场角色）。

## 8. Required context

`build_context("character_designer", ...)`：

```text
project name
scope
各角色: name / description_en（前 60 字）/ 已有视图 front|side|back
风格: project.style.suffix()
```

## 9. Read permissions

```text
read_scopes = ["characters", "style", "scenes"]
```

## 10. Write / execution permissions

```text
allowed_capabilities = ["character.inspect", "character.design"]
write_scopes         = ["character_proposal", "character_assets"]
can_delegate_to      = []
```

**当前仅 `character.inspect` 已接通**。`character.design` 注册权限但 adapter 返回 `not_implemented`。
未来接通须**包装现有角色生成能力**，**不**重新实现角色生成 Pipeline。

## 11. Current implemented capabilities

| Capability | Op | 状态 | 说明 |
|---|---|---|---|
| `character.inspect` | inspect | implemented | 读角色设定 + 参考图状态，返回 characters[]（含 has_views / related_scenes）+ style |

## 12. Reserved future capabilities

| Capability | Op | 状态 | 备注 |
|---|---|---|---|
| `character.design` | execute | **not_implemented** | 包装现有角色生成 Pipeline；AI-6E.0 不接通 |

## 13. Inputs

```text
instruction   角色设计/调整意图（直接对话）
（委派）capability_id / scope / operation
```

## 14. Structured outputs / artifacts

`character.inspect` 返回：

```text
status         completed
summary        "N 个角色；风格 ..."
artifact_refs  [{type:"character", id:name}, ...]
data           {characters: [{name, description_en, has_views:{front,side,back}, related_scenes}], style}
```

## 15. Definition of done

```text
- character.inspect：返回角色 + 视图状态概览；或
- character.design（未来）：经受控生成路径产出/更新角色参考资产，落 character_assets。
```

## 16. Failure / blocker behavior

```text
character.design 被调用 → NOT_IMPLEMENTED → task FAILED + error="能力未接通: character.design"
```

## 17. Direct Conversation mode

用户可直接与 Character Designer 对话讨论角色设定。
chat 经 `send_message` → `AgnesClient.chat`（profile 含 image_model，但 chat 用 reasoning）。
未配置 Agnes → `status="not_configured"`（200 软失败）。**直接对话不扩展权限**。

## 18. Delegated AgentTask mode

Producer 可委派 `character.inspect`（同步执行，返回结构化结果）。
委派 `character.design` 当前返回 `not_implemented`（task FAILED）。

## 19. Upstream dependencies

```text
User / Producer（角色设计意图）
Script Writer（scene.characters / 出场关系）
```

## 20. Downstream consumers

```text
Director（角色作为分镜出场依据）
Shot Producer（使用角色参考资产生产镜头）
Continuity Reviewer（依据角色身份检查一致性）
```

## 21. Delegation / escalation rules

```text
不可委派（can_delegate_to = []）
收到分镜/制作/合成域任务 → redirect：建议 Producer 委派对应 Specialist
```

## 22. Human-control boundary

角色参考资产生成 / 更新须经用户在受控路径上确认。Character Designer 不自主改 canonical character。

## 23. Canonical / Workspace boundary

```text
canonical 原始写：无（character.design 预留）
Workspace revision：未来角色身份变更应走受控路径
domain business state：character_assets（独立资产，如 saved_characters/{name}_{hash}/）
read-only inspection：character.inspect
```

## 24. Model / tool policy

`default_model_profile = character_profile`（reasoning + image = config `agnes.models.text/image`）。
**Image Model ≠ Agent**，只是工具模型。换模型不改权限。本阶段不重设计 ModelProfile。

## 25. UI context

- `src/views/agents/index.vue`：Character Designer 卡 + 独立聊天区。
- 现有角色设计业务路径（saved_characters/、抠图等）独立存在；`character.design` 未来**包装**它，不重写。

## 26. Examples

```text
用户："项目里有哪些角色？视图都齐了吗？"
→ character.inspect → 返回 characters[] + has_views + related_scenes

用户（直接对话）："把这场景改慢加特写"
→ Character Designer 说明：分镜/节奏属于 Director，建议改委派 director
```

## 27. Anti-examples

```text
× 描述 character.design 为"已实现"                     （实为 not_implemented）
× 重新实现角色生成 Pipeline                              （应包装现有能力）
× 与 Shot Producer 各自独立重定义角色身份               （角色身份归 Character Designer）
× 接受并执行分镜 / 制作 / 合成域任务
```

## 28. Current implementation status

- **已实现**：`character.inspect`（read-only，返回角色 + 视图状态 + 关联场景）。
- **未实现且本阶段不做**：`character.design`（注册权限但 adapter 返回 `not_implemented`）。
  未来接通须包装现有角色生成 Pipeline，不重写。
- **角色身份单一所有者**：Character Designer 决定/设计角色；Shot Producer 只在产镜头时**使用**角色参考。
- **运行时 prompt**：`_AGENT_SYSTEM_PROMPTS["character_designer"]` 与本契约一致，**未改动**。
