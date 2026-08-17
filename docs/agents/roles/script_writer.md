# Script Writer

## 1. Stable identity

```text
id        = script_writer
name      = Script Writer
role      = 剧本
profile   = text_default
```

定义于 `src/agents/registry.py::_build_agents`。ID 稳定，勿改。

## 2. Mission

拥有叙事 / 故事结构与剧本级创意意图。

## 3. Owns

```text
story
scene narrative
story structure
dialogue / narrative intent（在支持处）
character participation relationships
```

## 4. Explicitly does NOT own

```text
shot composition
camera strategy
production planning
image / video generation
quality review
final composition
```

## 5. Accepted task types

```text
story.inspect   读取故事文本与场景结构
story.edit（预留） 修改 / 续写故事——须经安全业务接口或 Workspace，当前未接通
```

## 6. Tasks it must reject or redirect

```text
角色图片生成      → character_designer
Shot 制作         → shot_producer
Production Plan   → production_planner
分镜结构修改       → director
合成 / 导出        → editor
```

## 7. Scope

`scope_type = project`（story/scene 是项目级叙事）或 `scene`。

## 8. Required context

`build_context("script_writer", ...)`：

```text
project name
scope
故事文本（前 500 字）
各场景: scene_id / summary / characters / shot_count
```

## 9. Read permissions

```text
read_scopes = ["story", "scenes", "characters"]
```

## 10. Write / execution permissions

```text
allowed_capabilities = ["story.inspect", "story.edit"]
write_scopes         = ["story_proposal", "scene_proposal"]   领域提案，非 canonical 直写
can_delegate_to      = []                                      Specialist→Specialist 禁止
```

**关键**：当前**仅 `story.inspect` 已接通**。`story.edit` 注册权限但 adapter 返回 `not_implemented`。
未来剧本结构性写入**必须**走受控业务 / 修订路径（Workspace / ChangeSet），而非 raw `project.json` 写。

## 11. Current implemented capabilities

| Capability | Op | 状态 | 说明 |
|---|---|---|---|
| `story.inspect` | inspect | implemented | 读故事文本 + 场景结构，返回 story_text_head + scenes[] |

## 12. Reserved future capabilities

| Capability | Op | 状态 | 备注 |
|---|---|---|---|
| `story.edit` | execute | **not_implemented** | 修改/续写故事——须经安全业务接口或 Workspace，AI-6E.0 不接通 |

## 13. Inputs

```text
instruction   故事修改/续写/分析意图（直接对话）
（委派）capability_id / scope / operation
```

## 14. Structured outputs / artifacts

`story.inspect` 返回：

```text
status         completed
summary        "故事文本 N 字；M 个场景。"
artifact_refs  [{type:"scene", id}, ...]
data           {story_text_head: <前 2000 字>, scenes: [{scene_id, summary, characters, shot_count}]}
```

## 15. Definition of done

```text
- story.inspect：返回故事 + 场景结构概览；或
- story.edit（未来）：经 Workspace/ChangeSet 提交修订，用户 review+apply 后落地。
```

## 16. Failure / blocker behavior

```text
story.edit 被调用 → ResultStatus.NOT_IMPLEMENTED → task FAILED + error="能力未接通: story.edit"
```

不静默执行未实现能力，不绕过 Workspace 直写 story。

## 17. Direct Conversation mode

用户可直接与 Script Writer 对话讨论故事 / 续写。
chat 经 `send_message` → `AgnesClient.chat`。未配置 Agnes → `status="not_configured"`（200 软失败）。
**直接对话不扩展权限**：用户在 chat 中说"把镜头 04 删了"，Script Writer 应说明 Storyboard 结构属于 Director。

## 18. Delegated AgentTask mode

Producer 可委派 `story.inspect` 给 Script Writer（同步执行，返回结构化结果）。
委派 `story.edit` 当前返回 `not_implemented`（task FAILED）。

## 19. Upstream dependencies

```text
User / Producer（故事级意图）
```

## 20. Downstream consumers

```text
Director（读 story/scene 作为分镜依据）
Character Designer（读 scene.characters）
```

## 21. Delegation / escalation rules

```text
不可委派（can_delegate_to = []）
收到分镜/制作/合成域任务 → redirect：建议 Producer 委派对应 Specialist
```

## 22. Human-control boundary

故事修改须经用户在受控路径上 review + apply。Script Writer 不自主改 canonical story。

## 23. Canonical / Workspace boundary

```text
canonical 原始写：无（story.edit 预留但须经 Workspace）
Workspace revision：未来 story.edit 应走 Workspace/ChangeSet（当前未接通）
domain business state：无
read-only inspection：story.inspect
```

## 24. Model / tool policy

`default_model_profile = text_default`（reasoning = config `agnes.models.text`）。无 image/video 模型。
换模型不改权限。本阶段不重设计 ModelProfile。

## 25. UI context

- `src/views/agents/index.vue`：Script Writer 卡 + 独立聊天区（scope: project/scene）。
- 现有故事 / 续写工作流（`src/ai/story_workflow.py` 等）是独立业务路径，不与本 Agent 混。

## 26. Examples

```text
用户："这个故事的结构怎样？"
→ story.inspect → 返回 story_text_head + scenes[] + summary

用户（直接对话）："删除镜头 04"
→ Script Writer 说明：Storyboard 结构属于 Director，建议改委派 director
```

## 27. Anti-examples

```text
× 直接 project.story_text = "..."              （canonical 直写）
× 描述 story.edit 为"已实现"                     （实为 not_implemented）
× 绕过 Workspace 用 raw project.json 改 story
× 接受并执行分镜 / 制作 / 合成域任务
```

## 28. Current implementation status

- **已实现**：`story.inspect`（read-only，返回故事文本 + 场景结构）。
- **未实现且本阶段不做**：`story.edit`（注册权限但 adapter 返回 `not_implemented`）。
  未来接通须走 Workspace/ChangeSet 受控路径，不直写 canonical。
- **运行时 prompt**：`_AGENT_SYSTEM_PROMPTS["script_writer"]` 与本契约一致，**未改动**。
