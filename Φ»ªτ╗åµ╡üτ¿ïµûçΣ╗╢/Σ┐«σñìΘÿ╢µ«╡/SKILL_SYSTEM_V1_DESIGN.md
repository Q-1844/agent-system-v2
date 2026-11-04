# Skill System V1 — Current State / Gap Map 与设计（40.md 执行文档）

依据：`详细流程文件/修复阶段/40.md`。本文是调查结论 + 实现设计，代码事实优先于旧报告。

## 一、Current State（2026-09-25 代码复核）

### 1. Agent 实现现状

- 正式 Agent：`src/agents/registry.py` 定义 9 个 Agent；**真实 tool-loop 主力是 5 个**：
  Producer（`producer.py`）+ 4 个 Specialist（`director.py` / `script_writer.py` / `character_designer.py` / `scene_designer.py`），每个文件自包含 system prompt + 工具 schema + executors + 镜像 `_run_tool_loop` + `*_turn` 入口。
- 委派：Producer `_delegate_specialist`（producer.py:706）→ Specialist `*_turn`，envelope 渲染为 Specialist 的 user 消息。
- 上下文组装（以 director_turn 为例，director.py:2801-2808）：`system = _build_system(proj, pdir) + memory` → `messages = [system] + history(过滤) + [user content]` → `_run_tool_loop`。
- trace：loop 内 `state["trace"]`（step/tool/target/result），turn 收口进 assistant meta（producer.py:1289-1295）→ `AgentMessage.meta` 落盘 session JSON。
- 校准专用 trace：`calibration.trace_read/upsert` → `src/experiments/calibration_trace.py`。
- **不存在任何 skill / capability-directory / prompt-module / workflow-registry / plugin / dynamic-context-loader 机制**（全库复核）。最接近的是 `subagent/tools.py` 的 `mount_subagent_tools()` 门控挂载与 Director 的模块化工具面（calibration/bridge/frames 各自独立文件 + TOOL_EXECUTORS 合并）。

### 2. 两个实证协作模式（Golden Skill 迁移素材，来源已复核）

模式一：**尾帧接力连续性 + 自适应校准**（Phase 37A/38/39 实证）。
当前散落在 5 个 Agent System Prompt：

| Agent | 文件 | 段落 | 行 |
|---|---|---|---|
| Director | `director.py` | 【连续性校准：尾帧接力与接缝裁定】 | L329-340 |
| Director | `director.py` | 【keyframes 接续桥：接缝修复的标准手段】 | L342-350 |
| Producer | `producer.py` | 【连续性校准编排（Phase 39）】 | L155-162 |
| Script Writer | `script_writer.py` | 【预编剧冻结与 minor 吸收（连续性校准协作）】 | L332-336 |
| Character Designer | `character_designer.py` | 【链上身份一致性（校准协作）】 | L252-255 |
| Scene Designer | `scene_designer.py` | 【校准协作中的上游场景 owner（条件参与）】 | L144-151 |

模式二：**keyframes 桥修复**（38 §16/§18 + Phase 39 `bridge_service.py` 正式化）——已包含在上表 Director 桥段，且有独立机械本体（`shot.bridge_create` → `src/storyboard/bridge_service.py:build_keyframes_bridge` 七步事务）。

### 3. 已具备的机械能力（不重复实现，Skill 只教用法）

- `calibration.seam_measure` / `calibration.trace_read` / `calibration.trace_upsert`（director_calibration.py）
- `video.extract_frame` / `visual.sample_frames`（director_frames.py）
- `shot.bridge_create`（director_bridge.py）
- `shot.production.inspect/bind/refresh`（director_production.py）
- `shot.generate_images/videos`（director_visual.py）
- `visual.review_image/review_shot`（director_vision_review.py / shot_review.py）
- `project.quota`（producer.py）
- OCC / 锁 / revision / approval / receipt / truth gate / Clean Review 路由全部在位。

### 4. Gap Map

| # | Gap | 40.md 对应 |
|---|---|---|
| G1 | 无 canonical Skill Registry / Package / Binding 模型 | §三/§四 |
| G2 | 无 Progressive Disclosure：workflow 知识全部摊平在 5 个 system prompt 常驻每轮 | §七 |
| G3 | routing（多容易被选）与 authority（冲突时谁权威）未分离，甚至不存在 | §五 |
| G4 | Agent 对"何时用什么方法"完全依赖 prompt 记忆，无确定性 discovery | §一/§七 |
| G5 | Skill 加载无 trace，无法对比 Prompt-baseline vs Skill-based 行为 | §十三 |
| G6 | 用户上传 Skill 无 schema/信任边界（未来兼容需预留） | §十四 |

## 二、Final Architecture（设计定稿）

```
Runtime Invariants（OCC/truth gate/ownership/审批——全部不动）
        ↓
Agent Base System（L0：身份/权限/真相纪律 + Skill Policy——每 Agent system prompt）
        ↓
Skill Discovery（L1：catalog 常驻 system 尾部 + 确定性 trigger hint 注入 user 消息）
        ↓
Skill Runtime（L2/L3：skill.load / skill.read_resource 工具 + capability 声明 + trace）
        ↓
Skill Package（SKILL.md + references/<agent>.md，文件系统 markdown）
        ↓
Tools / Compound Tools（现有工具面，零改动）
        ↓
Production System（零改动）
```

### 新增代码

```
src/agents/skill_system/
  __init__.py
  models.py      # SkillPackage / AgentSkillBinding + manifest 校验（fail closed）
  registry.py    # SkillRegistry：扫描 builtin 包、校验、binding、per-agent view
  runtime.py     # SkillRuntime：catalog 渲染 / trigger hint / load / resource / trace
  tools.py       # skill.list / skill.load / skill.read_resource schema + executor 工厂
  builtin/
    full-story-relay-calibration/
      skill.json          # manifest（含 bindings）
      SKILL.md            # 共享工作流
      references/producer.md / director.md / script_writer.md /
                 character_designer.md / scene_designer.md
    keyframes-bridge-repair/
      skill.json
      SKILL.md
      references/director.md
```

### 关键设计决策

1. **Skill 加载走工具面（skill.load 为只读工具）**——复用现有 tool loop：加载天然进 trace/events、天然可观察、无需新 plumbing；工具面物理保证 Skill 无法授予新权限（硬约束 §十一自动成立）。
2. **L1 catalog 注入点 = 各 `_build_system` 尾部**（随 `_refresh_system` 每轮刷新，确定性缓存渲染）；**L0 Skill Policy 一段通用文字**随 catalog 注入。
3. **确定性 discovery hint**：turn 入口对 user content 做 manifest `triggers` 关键词匹配，命中则在 messages 的 user 消息前拼一行 `[系统 skill 提示]`（**不落盘**——session 中仍只存原 content）。廉价、确定、可 trace（hint 注入记入 skill trace）。
4. **routing_priority（Agent×Skill 关系，选择排序）与 authority_tier（builtin_core > builtin > user，冲突解析）双字段分离**（§五）。
5. **两个 Golden Skill**：`full-story-relay-calibration`（模式一）与 `keyframes-bridge-repair`（模式二，`depends_on` 关联）。理由：桥有独立适用面（级联断缝/用户直接要过渡镜头）、独立触发词、内容自成体系；拆开后各自更短、加载更准。二者共享术语，桥 skill 的 SKILL.md 引用校准 skill 的裁定上下文。
6. **capability 声明**：manifest `required_capabilities[agent_id]` 在 load 时与该 Agent 真实工具面对账，missing 时返回结构化 `capability_status="missing"` + fail/delegate 指令（fail-closed 教学，物理工具面仍是真权限层）。
7. **Skill trace 双层**：(a) `SkillRuntime` 追加 JSONL → `projects/{p}/output/agents/skills/trace.jsonl`（activation/hint/load/resource/结果）；(b) turn 收口时 collect 本 turn skill 使用记录进 assistant `meta.skills`（与既有 trace/receipts 同层）。
8. **Binding 内嵌 manifest**（`skill.json` 的 `bindings` 数组）：同一 canonical package，不同 Agent 通过 binding 得到 role/priority/authority/activation；V1 不做独立 binding 存储，user-upload 未来在 registry 层加 binding override 即可。
9. **System Prompt 减法顺序**：先建 runtime + skill 内容（1:1 保留纪律原文）→ 接线 → 测试绿 → 再删除 5 个 prompt 中已迁移段落（同一轮 commit 链内最后一步）。

### Deviations（对 40.md 措辞的工程调整）

- D1：40.md §三示例目录 `skills/<id>/SKILL.md` 调整为 `src/agents/skill_system/builtin/<id>/`（skill 是 Agent 子系统的一部分，与 subagent/ 平级，符合现有包结构；文件名仍为 SKILL.md）。
- D2：`AgentSkillBinding` 不做独立 registry 文件，V1 内嵌 manifest `bindings`（§四语义完整：agent_id/enabled/role/routing_priority/authority_tier/activation_mode/role_resource 全部表达；避免 V1 引入尚无消费者的第二存储）。
- D3：不新增 `skill.discover` 独立 API——discovery 由 L1 catalog（system 注入）+ `skill.list` 工具 + turn 入口确定性 hint 三者覆盖，均为现有通道。
