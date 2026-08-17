# Director

> **金标准参考角色**。Director 是 AGNES 首个完整验证的 Specialist 执行能力（AI-6D）。
> 未来 Specialist（如 Production Planner）接通真实执行能力时，应参照此契约与
> `src/agents/director_proposal.py` 的"语义意图层 + 确定性编译器 + Workspace/ChangeSet 边界"模式。

## 1. Stable identity

```text
id        = director
name      = Director
role      = 导演
profile   = text_default
```

定义于 `src/agents/registry.py::_build_agents`。ID 稳定，勿改。

## 2. Mission

把场景 / 镜头创意意图翻译为可 review 的 Storyboard 决策。

## 3. Owns

```text
shot structure
shot description
storyboard pacing
insert / update / delete / reorder intent
```

## 4. Explicitly does NOT own

```text
production strategy
asset generation
final quality acceptance
final composition
```

## 5. Accepted task types

```text
storyboard.inspect          读取场景分镜结构（DEMO）
storyboard.propose_change   提出分镜修改提案（KEEP/UPDATE/INSERT/DELETE/REORDER）
                            ——必须经 Workspace/ChangeSet（红线），AI-6D 已接通（async）
```

## 6. Tasks it must reject or redirect

```text
production strategy      → production_planner
asset / video generation → shot_producer
quality review           → continuity_reviewer
story / 续写              → script_writer
character design         → character_designer
composition / export     → editor
```

## 7. Scope

`scope_type = scene`（整段分镜）或 `shot`（聚焦选中镜头 + 邻接）。
Director 的语义动作**只限当前故事段内，不得跨故事段**（`parse_and_validate` 校验）。

## 8. Required context

`director_proposal.py::build_director_context`（与 chat 的 `build_context("director",...)` 互补）：

```text
project / style（art_style / palette / aspect_ratio / era）
scope（type / id / scene_id）
scene（scene_id / title / summary / characters / shot_order）
shots（每镜：shot_id / index / description / characters / target_duration_s / status /
       first_frame_status / video_status / has_final_video）   不含 base64/URL/批次/签名
shot scope 额外：selected_shot + neighbors(prev/next)
```

有用且最小——不 dump 整个项目。

## 9. Read permissions

```text
read_scopes = ["scenes", "characters", "shots", "style"]
```

## 10. Write / execution permissions

```text
allowed_capabilities = ["storyboard.inspect", "storyboard.propose_change"]
write_scopes         = ["storyboard_proposal"]   创意提案层，非 canonical 直写
can_delegate_to      = []
```

`storyboard.propose_change` 的 `operation = execute`，但**执行 = 产出语义提案 + 编译 + 预览**，
**不是**直接写 canonical。

## 11. Current implemented capabilities

| Capability | Op | 状态 | 说明 |
|---|---|---|---|
| `storyboard.inspect` | inspect | implemented | 读场景分镜结构（DEMO），返回 shots[] + issues |
| `storyboard.propose_change` | execute | **implemented（AI-6D async）** | 经 `AgentRuntime.propose_storyboard_change`：ctx→Agnes→parse+validate→workspace→compile→preview→persist |

> `storyboard.propose_change` 标 `implemented=True`，但**不在** sync `dispatch` 的 `_ADAPTERS` 中——
> 它走单独的 async 编排路径（`director_proposal.py::generate_proposal`）。

## 12. Reserved future capabilities

无 Director 专属预留能力。完整 Variant Engine 不在本阶段（见 §28）。

## 13. Inputs

```text
scope_type   shot | scene
scope_id     shot_id | scene_id
instruction  自然语言修改意图（如"在镜头 02 后插入一个反应镜头"）
```

## 14. Structured outputs / artifacts

### 语义模型（`director_proposal.py`）

```text
DirectorProposal
  proposal_id, project, agent_id="director", scope_type, scope_id, instruction
  title, summary
  items: [DirectorProposalItem]
  workspace_id, base_workspace_revision
  full_preview_changeset_id, selected_preview_changeset_id, applied_changeset_id
  full_preview, selected_preview         含 diff/impact/warnings（持久化以刷新恢复）
  status: created | selected_previewed | applied | dismissed | stale

DirectorProposalItem
  item_id（服务端生成，LLM 不发明）
  action: update_shot | insert_shot | delete_shot | reorder_shot
  scene_id, shot_id（insert 无，服务端 allocate）
  description, characters, target_duration_s（新值）
  after_shot_id（insert/reorder 位置锚点）
  reason
```

### 前端 currentProposal 形态（`proposal_to_api`）

```text
proposal 全字段 + base_is_stale + changeset{changeset_id, status, diff, impact, warnings, base_workspace_revision, after_signature}
```

`base_is_stale` 动态判断（不持久化）。

## 15. Definition of done

```text
- storyboard.inspect：返回分镜结构 + issues；或
- storyboard.propose_change：产出合法 Proposal（含 full_preview），用户可选择性 preview 子集 → apply 选中 ChangeSet。
  apply 复用 cs_engine.apply（三重校验：revision + before_signature + after_signature）。
```

## 16. Failure / blocker behavior

```text
Agnes 不可用        → ProposalGenerationError("not_configured") → HTTP 503
Agnes 调用失败       → "agnes_failed" → HTTP 502
LLM 输出解析/校验失败 → 一次 repair；仍失败 → "invalid_output" → HTTP 422
scope 不存在         → "bad_scope" → HTTP 404
编译/预览失败        → 删除刚建的 workspace（不留垃圾）→ ChangeSetError code 透传 → HTTP 400/409
workspace revision 不匹配 → STALE → HTTP 409（preview-selected 时返回 stale）
```

**失败不留 workspace、不动 canonical。**

## 17. Direct Conversation mode

用户可直接与 Director 对话讨论分镜。
chat 经 `send_message` → `AgnesClient.chat`（temp 0.7, max 1024）。
未配置 Agnes → `status="not_configured"`（200 软失败）。
**直接对话不扩展权限**：用户直接说"删镜头 04"也可触发 `storyboard.propose_change`，但仍走 Workspace/ChangeSet，
**不**直写 canonical。

## 18. Delegated AgentTask mode

Producer 可委派 `storyboard.inspect`（同步执行）。`storyboard.propose_change` 当前**不**经 `delegate()` 同步 dispatch
（它是 async + 需 Agnes + Workspace），而是经专用端点 `POST /api/project/{p}/agents/director/proposals`。
两端点权限均经 `_check_director_permission`（复用 Capability Registry，不绕过）。

## 19. Upstream dependencies

```text
User / Producer（分镜意图）
Script Writer（scene / story）
Character Designer（characters）
```

## 20. Downstream consumers

```text
Production Planner（读分镜结构作为出 Plan 的依据）
Continuity Reviewer（依据分镜创意审查）
Editor（镜头顺序作为合成依据）
```

## 21. Delegation / escalation rules

```text
不可委派（can_delegate_to = []）
若 Director 发现 Shot 创意本身无效/欠定 → 返回结构化 blocker，而非静默重设计
收到制作/合成域任务 → redirect：建议 Producer 委派对应 Specialist
```

## 22. Human-control boundary

Director Proposal 必须**用户在 UI 上 review** diff/impact/warnings，选择 item 子集再 preview，最后 apply 选中 ChangeSet。
Director 不自主 apply、不自主删镜头。

## 23. Canonical / Workspace boundary

**红线（`_AGENT_SYSTEM_PROMPTS["director"]` 与 `DIRECTOR_PROPOSAL_SYSTEM` 双重声明）**：

```text
Director → 语义 proposal → existing ChangeOperation / ChangeSet → Preview / Impact → Creative Workspace →（apply）
Director ✗ 直接覆盖 canonical project 的 shots 数组
```

- canonical 原始写：无。
- Workspace revision：是。Director Proposal 创建 Director Workspace（`WorkspaceStore.create`），
  compile 成 `ChangeOperation[]`，`cs_engine.preview` 产出预览，`cs_engine.apply` 落地工作区（非 canonical）。
- **不**实现 canonical merge / "make official"。

### 不变量（§2/§21）

```text
LLM 只产创作意图。
before / revision / signature / stable shot_id / changeset_id / workspace_id 全部由确定性服务端代码产出。
无 Agent 代码直写 canonical。
```

- `before` 由编译器从 working project 读（`compile_items`），**绝不取自 LLM**。
- `insert` 的 stable shot_id 由 `ws_store.allocate_shot_id` 分配，LLM 不得发明。
- `item_id` = `item_` + `secrets.token_hex(4)`，服务端生成。

## 24. Model / tool policy

`default_model_profile = text_default`（reasoning = config `agnes.models.text`）。
换模型不改权限 / 域所有权 / 完成契约。`temperature=0.3`（proposal）/ `0.2`（repair），`json_mode=True`，`max_tokens=2048`。
本阶段不重设计 ModelProfile。

## 25. UI context

- `src/views/agents/index.vue`：Director 卡 + 独立聊天区。
- `src/components/agent/`（AI-6D/D.1）：FloatingAgent（Director toolbar 紧贴 Shot）+ Proposal overlay
  （AI 修订层 + 状态内聚 dirty/stale/previewing/applying/error + 渐进 diff/impact/warnings + 卡片人类可读 headline + 明显选择 + review bar 决策点）。
- 端点：`POST .../agents/director/proposals`（create）、`GET .../{proposal_id}`（刷新恢复）、
  `GET .../proposals`（list by scope）、`POST .../preview-selected`、`POST .../apply`、`POST .../dismiss`。
  apply / preview-selected 复用 `workspace_lock`。

## 26. Examples

```text
用户："在镜头 02 后插入一个反应镜头"
→ propose_storyboard_change(scope=scene:s03, instruction)
→ Proposal(items=[insert_shot(after=02, desc=..., chars=[...], dur=2)])
→ full_preview（INSERT + REORDER 的 diff/impact/warnings）
→ 用户选该 item → preview-selected（全新 ChangeSet）
→ 用户 apply → cs_engine.apply（三重签名校验）→ workspace 落地

用户："把镜头 04 删了"
→ Proposal(items=[delete_shot(shot_id=04)]) → 同上 Workspace 路径
（✗ 不直接 project.scenes[x].shots = ...）
```

## 27. Anti-examples

```text
× Director 直接覆盖 canonical shots 数组                  （红线，§23）
× LLM 输出 before / shot_id / changeset_id / signature   （须服务端确定性产出）
× 跨故事段插入 / 修改                                    （parse_and_validate 拒绝）
× 自主 apply / 自主删镜头而不经用户 review
× 把 storyboard.propose_change 描述为"已实现"但实为 not_implemented  （实为 implemented，AI-6D）
× 接受并执行制作 / 合成域任务
```

## 28. Current implementation status

- **已实现**（AI-6D）：`storyboard.propose_change` 完整链路——
  语义意图层（`DirectorProposalItem`，四动作）+ 确定性编译器（`compile_items`，纯函数，可对任意子集重跑）+
  `parse_and_validate`（跨段/矛盾/锚点校验，一次 repair）+ Workspace/ChangeSet 边界（preview/apply/dismiss）+
  三重签名 apply + 持久化 Proposal（刷新恢复）+ stale 检测（`base_workspace_revision`）。
- **已实现**（AI-6B）：`storyboard.inspect`（DEMO，read-only）。
- **未实现且本阶段不做**：完整 Director Storyboard Variant Engine（多 Variant 并存 / Variant 选择 / canonical merge）。
- **运行时 prompt**：`_AGENT_SYSTEM_PROMPTS["director"]`（含 🔴 canonical 保护）与 `DIRECTOR_PROPOSAL_SYSTEM`
  均与本契约一致，**未改动**。
