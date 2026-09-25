# SKILL_SYSTEM_V1_REPORT

依据：`详细流程文件/修复阶段/40.md`。设计文档：`详细流程文件/修复阶段/SKILL_SYSTEM_V1_DESIGN.md`。
分支：`feat/agent-v2-phase-d-golden-run`；开工 HEAD `fb0411b` → 完成 HEAD `55e512f`（8 个小步 commit）。

## 1. Before architecture

```
Agent System Prompt（巨型单体，identity + workflow 混在一起，每轮全量下发）
  ↓
原生 function-calling tool loop（5 个 Agent 各自镜像实现）
  ↓
工具面（Director 28 / SW 13 / CD 17 / Scene 10 / Producer 只读+delegate）
  ↓
Production System（OCC / truth gate / approval / binding / generation）
```

问题（40.md §一实测复述）：Agent 拥有工具，但**不稳定地知道面对复杂生产任务时该用什么方法、按什么顺序、何时检查/修复/委派/停止**。两个已实证的协作模式（尾帧接力校准 / keyframes 桥）的工作方法被摊平写进 **5 个 Agent 的 System Prompt 常驻段落**（Director L329-350、Producer L155-162、Script Writer L332-336、Character Designer L252-255、Scene Designer L144-151），每轮全量下发，且不存在任何 skill / capability-directory / prompt-module / workflow-registry / plugin / dynamic-context-loader 机制（全库复核结论）。

## 2. Final architecture

```
Runtime Invariants（OCC / truth gate / ownership / approval / Clean Review——零改动）
        ↓
Agent Base System（L0：身份 / 权限 / 真相纪律 + Skill Policy——prompt 已做减法）
        ↓
Skill Discovery（L1 catalog 常驻 system 尾部 + turn 入口确定性 trigger hint 注入
                 user 消息【不落盘】+ skill.list 工具）
        ↓
Skill Runtime（L2：skill.load 只读工具 → SKILL.md + 本 Agent role 指令 + capability
               对账 + authority notice；L3：skill.read_resource 按需加载）
        ↓
Skill Package（canonical 唯一；SKILL.md 共享工作流 + references/<agent>.md 角色指令）
        ↓
Tools / Compound Tools（现有工具面零改动——机械时序仍在 bridge_service 等代码层）
        ↓
Production System（零改动）
```

Progressive Disclosure（40.md §七）落地形态：

| 层 | 内容 | 载体 | 成本 |
|---|---|---|---|
| L0 | Skill Policy（发现→加载纪律，3 条） | 各 `_build_system` 尾部 catalog_block | ~5 行 |
| L1 | 技能目录元数据（id/版本/名称/适用/触发线索/优先级） | 同上，registry mtime 缓存渲染 | ~10 行/技能 |
| L2 | SKILL.md 主体 + 本 Agent role 专属指令 | `skill.load` 工具返回 | 按需 |
| L3 | 其余 references（其他角色指令/失败案例等） | `skill.read_resource` 工具 | 按需 |

Discovery 三通道（廉价、确定、可追踪）：① L1 catalog 常驻；② turn 入口对 user content 做 manifest `triggers` 关键词匹配，命中则把 `[系统 skill 提示]` 拼进发给 LLM 的 user 消息（session 落盘仍只有用户原文）；③ loop 中随时 `skill.list`。

## 3. Modified files

新增：
- `src/agents/skill_system/{__init__,models,registry,runtime,tools}.py`
- `src/agents/skill_system/builtin/full-story-relay-calibration/{skill.json,SKILL.md,references/{producer,director,script_writer,character_designer,scene_designer}.md}`
- `src/agents/skill_system/builtin/keyframes-bridge-repair/{skill.json,SKILL.md,references/{director,producer}.md}`
- `tests/test_skill_system.py`（Phase A 13 项）、`tests/test_skill_discovery.py`（Phase B 7 项）、`tests/test_skill_turn.py`（Phase C 3 项）
- `_40_skill_replay.py`（Phase D 真实 API replay 脚本）
- `详细流程文件/修复阶段/SKILL_SYSTEM_V1_DESIGN.md`

修改（Agent 接线 + prompt 减法）：
- `src/agents/director.py`：+skill 工具面（28→31）、catalog 注入、hint 注入、meta.skills、Clean Review 白名单允许 skill.*；**删除**【连续性校准】【keyframes 接续桥】两段（-23 行 prompt）
- `src/agents/producer.py`：+skill 工具面（13→16）、catalog/hint/meta.skills；**删除**【连续性校准编排】（-9 行 prompt）
- `src/agents/script_writer.py`：+skill 工具面（13→16）、catalog/hint/meta.skills；**删除**【预编剧冻结与 minor 吸收】（-6 行 prompt）
- `src/agents/character_designer.py`：+skill 工具面（17→20）、catalog/hint/meta.skills；**删除**【链上身份一致性】（-5 行 prompt）
- `src/agents/scene_designer.py`：+skill 工具面（10→13）、catalog/hint/meta.skills；**删除**【校准协作中的上游场景 owner】（-9 行 prompt）
- `tests/test_p39_prompts_discipline.py`：语义反转（workflow 锚点断言从"在 prompt 中"改为"在 skill 包中 + 不在 prompt 中"；身份/权限/真相纪律锚点保留在 prompt 断言）
- `tests/test_c1_director_production.py` / `test_resources_phase1b.py` / `test_ai_7d_r_script_writer_native.py` / `test_character_designer_agent.py` / `test_scene_designer_agent.py`：工具集合/数量锚定 +skill.*（5 处预期更新）

未动（冻结区，40.md §十八）：Production Resources / Scene System / Shot Binding / Generation Adapter / bridge_service / continuity_service / calibration_trace / 全部既有工具本体 / 前端 / provider。

## 4. Skill schema（`skill.json` manifest）

```jsonc
{
  "skill_id": "full-story-relay-calibration",   // [a-z0-9-]{2,64}
  "version": "1.0.0",                            // x.y.z
  "name": "全片尾帧接力连续性校准",
  "description": "一句话",
  "category": "workflow",                        // workflow/domain/situation/review_recovery（V1 少量稳定类别）
  "source": "builtin_core",                      // builtin_core/builtin/user（来源≠category）
  "status": "active",                            // active/disabled（包级开关）
  "applicability": "适用情境一句话（进 L1 catalog）",
  "triggers": ["校准", "连续性", ...],            // 确定性 discovery 关键词
  "required_capabilities": {"director": ["calibration.seam_measure", ...]},  // 声明，加载时对账
  "depends_on": [],                              // 关联技能
  "bindings": [ /* AgentSkillBinding，见 §5 */ ]
}
```

主体是 `SKILL.md`（Markdown 自然语言工作方法）——不过度 schema 化生产知识（40.md §四）。校验 fail closed：非法 id/版本/枚举、缺 SKILL.md、显式声明的 role_resource 缺失、重复 skill_id → 不注册、不可见、不可加载，错误入 `registry_errors()`。

## 5. Binding model（AgentSkillBinding）

```jsonc
{"agent_id": "director", "enabled": true,
 "role": "primary_executor",        // coordinator/primary_executor/contributor/conditional/executor
 "routing_priority": 100,           // 0-100：仅 skill selection/ranking（多容易被选）
 "authority_tier": "builtin_core",  // builtin_core/builtin/user：仅指令冲突解析
 "activation_mode": "hinted",       // hinted（trigger 命中给确定性提示）/manual
 "role_resource": null}             // 缺省派生 references/<agent_id>.md（可选存在）
```

核心语义（40.md §四/§五）：
- **重要度是 Agent×Skill 的关系属性**：同一 calibration 技能，director=primary_executor/100，producer=coordinator/80，script_writer=contributor/60，character/scene=conditional/40。
- **routing_priority 与 authority_tier 分离**：用户上传 skill 可以有高 routing_priority（被主动选择）但 authority_tier=user 永远最低——`authority_notice` 在每次 load 返回中声明不得覆盖 system prompt / ownership / 用户显式指令 / canonical truth / OCC / 审批 / truth gate。
- Binding V1 内嵌 manifest（唯一 canonical package；user-upload 未来在 registry 层加 binding override，见 §12 Deviations D2）。

## 6. Loading lifecycle

1. 进程启动首次访问 → `SkillRegistry` 扫描 `skill_system/builtin/` → manifest 校验（fail closed）→ mtime 签名缓存。
2. 每 turn：`_build_system` 尾部渲染 catalog_block（L0 policy + L1 目录，确定性缓存，随 `_refresh_system` 每轮刷新）。
3. turn 入口：`activation_hint(agent_id, content, pdir)` 关键词匹配 → 命中 hinted 技能则拼提示进 user 消息（不落盘）+ 记 hint 事件。
4. Agent 判断相关 → `skill.load(skill_id)`（只读，不占写槽，可并行）→ 返回 SKILL.md + role 指令 + capability 对账 + authority notice + 剩余资源清单 → 记 load 事件。
5. 需要更多 → `skill.read_resource(skill_id, resource)`（白名单防路径穿越）→ 记 resource 事件。
6. turn 收口：`collect_and_reset(agent_id)` → assistant `meta.skills`（与 receipts/trace 同层）；全部事件 append `projects/{p}/output/agents/skills/trace.jsonl`。

失败模式（全部结构化、fail closed、不 raise）：`unknown_skill` / `skill_disabled` / `skill_not_visible` / `unknown_resource`；capability 对账 `missing` 时返回 fail/delegate 指引（知识可读、执行受限——物理工具面才是真权限层）。

## 7. Authority model

`authority_tier ∈ {builtin_core > builtin > user}`，只用于指令冲突解析，与 routing 无关。不变量（结构性成立，非 prompt 自觉）：

- **Skill 无法授予权限**：`skill.*` 是只读工具，加载返回纯文本；真实权限层是各 Agent 的物理工具面（代码决定，skill 无法改变）。
- **Skill 无法绕过 invariant**：OCC / revision / approval / truth gate / Clean Review 路由 / 写串行 / zero-read gate 全部在 loop 层，skill 文本不经过这些通道。Phase D 实测：skill 加载后 Agent 零写假成功仍被 truth gate 纠偏（invariant 未被削弱）。
- **skill.* 不计入 canonical 读域**：不加入 `DIRECTOR_READ_TOOLS`（zero-read gate 不被 skill 调用绕过）；但 Clean Review 白名单 `REVIEW_READ_TOOLS` 允许 skill.*（审查者可读方法知识）。
- capability 声明是**诚实教学层**：`required_capabilities[agent_id]` 加载时对账，missing → 明确 fail/delegate（40.md §十一）。

## 8. Agent integration（5 个 tool-loop Agent 统一 3 处小改 + turn 入口 1 处）

每个 Agent 文件：`import skill 模块` → 工具列表 `*SKILL_TOOLS` → `TOOL_EXECUTORS` 合并 `make_skill_executors(agent_id)`（闭包绑定 agent_id——现有 executor 签名无 agent 维度）→ `_build_system` 尾部 `catalog_block` → turn 入口 hint 拼接 → meta `skills` 字段。Producer 走 `_build_producer_tools()` 同样模式。与 `director_calibration.py` 的模块化挂载模式一致，无新框架、无第三方依赖。

子系统交互复核：Scene SubAgent（`_scene_agent_tools()` 从 DIRECTOR_READ_TOOLS 派生）不含 skill.*（一次性设计实例不走 skill）；`mount_subagent_tools()` 门控不受影响；registry 名册不变；禁用身份（planner/shot_producer/continuity_reviewer/editor）view 为空、零绑定。

## 9. Golden Skill structure（第一批 = 两个实证协作模式，40.md §九）

**`full-story-relay-calibration` v1.0.0**（模式一：尾帧接力连续性 + 自适应校准）
- `SKILL.md`：目的/适用性/参与者表（5 Agent 角色）/总体工作流（预编剧冻结→逐镜链路→三档裁定→trace→none·minor·major·级联·场景根因）/共享术语（expected_tail、tail_to_first_mae vs end_to_end_mae、MAE 经验区间、时长配方、链根 identity reference、cascade、检查点节奏）/决策模型（三档 + 窄锚定）/完成标准/失败模型（Vision 盲区、M5-a4 重试方差、特写吸引子、回滚手术、Shadow Replay 纪律）。
- `references/`：producer.md（编排：active set=4、场景根因路由、major 完整报告要素、配额感知、检查点节奏）、director.md（七步链路、三档判据、级联纪律、trace 必存字段、边界处理）、script_writer.md（六要素冻结、minor 吸收设计、anchor patch）、character_designer.md（approved Look 身份真相、检查与建议不自动改）、scene_designer.md（条件参与、修场景真相、绝不迁就单镜画面）。

**`keyframes-bridge-repair` v1.0.0**（模式二，depends_on 前者）
- `SKILL.md`：三模式边界控制力（multi 钉开场/i2v 钉首帧/桥双端钉死）、桥=接缝修复标准手段（retry/accept 非同类替代）、触发判据 + 窄锚定双端证据规则、工作流（七步事务由后端强制）、决策模型、完成标准（双侧 MAE + 帧检落 trace）、失败模型（5s 无 morph/两点一线/seed 后守卫/桥尾帧先验/MAE-only 误判）。
- `references/`：director.md（取证→构造→验收→对冲细节）、producer.md（repair 决策中的桥、cascade=0 价值、10s+ 配额成本）。

形态决策（40.md §九）：采用**两个相互关联的 workflow skills**而非"一技能+资源"——桥有独立适用面（级联断缝/用户直接要过渡镜头）、独立触发词、独立内容体系；拆开后 L2 加载更精准（普通校准轮不加载桥的构造细节）。依赖关系用 `depends_on` 表达。

内容来源：40.md §九要求"从现有规格、当前 Prompt 和真实 production code 中确认准确边界，不凭本文重新发明"——全部纪律 1:1 取自当前 5 个 Agent prompt 段落 + Phase 37A/38/39 报告实证事实（时长配方、MAE 基线、M4/M5/M5B 事故教训、39B 窄锚定），由 `test_p39_prompts_discipline.py` 锚定关键词双向验证（prompt 中消失 + skill 中存在）。

## 10. Trace examples（真实落盘，`projects/_40_skill_replay/output/agents/skills/trace.jsonl`）

```jsonl
{"event":"hint","agent_id":"director","ok":true,"detail":{"hinted":["full-story-relay-calibration"],"catalog":["full-story-relay-calibration","keyframes-bridge-repair"]}}
{"event":"load","agent_id":"director","skill_id":"full-story-relay-calibration","version":"1.0.0","ok":true,"detail":{"role":"primary_executor","loaded":["SKILL.md","references\\director.md"],"capability":{"status":"ok","missing":[]},"task":"对整条故事做连续性校准"}}
```

可回答 40.md §十三全部问题：Agent 看到了哪些 metadata（detail.catalog）/ 为什么被选择（hint 事件 hinted + trigger 匹配）/ 哪个 skill/version（load 事件）/ 什么 role（detail.role）/ 加载了哪些 resources（detail.loaded）/ capability 状态 / 任务摘要 / 成功失败（ok + fail 事件含 code）。assistant `meta.skills` 同层收口。不持久化模型私有推理。

## 11. Tests（40.md §十五 Phase A–D）

- **Phase A（13 项，`test_skill_system.py`）**：registry 加载/mtime 缓存/reload；invalid manifest 6 类 fail closed（坏 id/版本/枚举/空 bindings/非法 agent/priority 越界）+ 缺 SKILL.md + 声明资源缺失 + 重复 id；per-agent filtering + routing 排序 + role 差异 + routing/authority 字段分离；enable/disable（包级 + binding 级）；user-extension 默认最低 tier + authority_notice 边界；progressive loading（L1 无主体/L2 含主体+role/L3 按需/role 不串）；load fail-closed 4 变体 + 路径穿越防护；capability 对账（ok/missing/unknown + fail/delegate 指引）；结构化 trace（hint/load/resource/load_fail 事件序列 + 字段完整）；turn collect/reset + 跨 agent 隔离。
- **Phase B（7 项，`test_skill_discovery.py`）**：6 类无关任务零 hint；校准任务命中正确 skill；桥任务命中桥 skill；多技能命中按 routing_priority 排序；hint 尊重 agent view（scene 无 bridge binding 不 hint / producer 有）；catalog per-agent 内容；role-specific resource 5 agent 各自命中；选择理由留痕（hint/load 事件）。
- **Phase C（3 项，`test_skill_turn.py`）**：fake client 驱动**正式入口 director_turn**——skill.load 在真实 loop 执行；hint 注入 LLM 上下文但**不落盘**（session 只有原文）；meta.skills=[hint,load]；trace.jsonl 持久化；无关任务零注入零事件；加载未知技能结构化失败喂回模型（不 raise）。
- **Phase D（真实 API，`_40_skill_replay.py`，agnes-2.5-flash 文本，零视频消耗）**：任务"对整条故事做连续性校准"，正式入口 director_turn，两组对照（下表）。
- **纪律锚定（11 项，`test_p39_prompts_discipline.py`）**：workflow 知识不在 prompt + 完整在 skill 包 + 身份/权限/真相纪律保留在 prompt + 名册与 skill binding 一致性 + 禁用身份零绑定。

## 12. Regressions

- 定向回归全绿：`test_p39_agent_tools` + `test_director_agent` + `test_agents` + `test_agent_model_switch` + `test_resource_agent_*` + `test_subagent_*` + `test_resources_phase1b` + `test_ai_7d_r_script_writer_native` + `test_character_designer_agent` + `test_scene_designer_agent` = **302 项 passed**（含本轮新增 37 项 skill 测试）。
- 全量 `pytest tests/` 基线对比（`git worktree` 于 2c431bf 隔离目录）：基线与当前均存在 **54 项环境污染失败**（`test_av_sync_a2_*` 39 项 ffmpeg 产物缺失、`test_full_project_autopilot` 10+1+1+1 项 FileExistsError、`test_ai_7ch_cancel_race` 2 项 Task pending）——根因为**当前工作目录有一个 19:36 启动的运行中后端进程（PID 31636，监听 8000）持有 `projects/` 文件句柄**，测试 `shutil.rmtree` 无法清理。worktree 独立目录中这些测试基线全过。与本轮改动无关（skill 不碰 AV/生成/change_protocol 通道）。
- 预期锚定更新 5 处（工具集合 +skill.*），1 处工具数量锚定（Director 28→31），已在 commit `5f29e41` 说明。

## 13. Phase D 对照证据（Prompt-less discovery 双通道验证）

| 指标 | skill 模式（hint+catalog） | baseline 模式（no-hint，仅 catalog） |
|---|---|---|
| correct skill activation | ✅ 第一步即 `skill.load("full-story-relay-calibration")` | ✅ 自主 `skill.list` → `skill.load` 同一技能 |
| false activation | ❌ 0 次误加载 | ❌ 0 次 |
| 工具选择受 skill 影响 | `calibration.trace_read` 被调用（校准方法引导） | 未到 trace 阶段即发现 prerequisite blocker |
| Agent 收尾行为 | 只读汇报后声称推进 → **truth gate 纠偏**（invariant 正常工作） | 如实报告 blocker（角色未建/缺字段/缺场景参考）→ 合法收尾 |
| hint 泄漏入 session | ❌ | — |
| 时长 / LLM 调用 | 34.7s / 4 次 | 13.1s / 3 次 |

结论：discovery 双通道都成立（hint 直达 / catalog 自主发现）；skill 加载后工具选择受方法引导；**既有 invariant（truth gate）未被 Skill 削弱**。hint 组 Agent 因 hint 的"开始执行"语义在当前项目状态（无视频/无角色）下更早进入执行姿态而被闸门纠偏——属于任务-项目状态匹配问题，见 §14。

## 14. Known limitations

1. **hint 触发是关键词匹配**（确定性、廉价），不是语义分类器：任务文本不含 trigger 词时依赖 catalog 自主发现（Phase D 已证明可行但多 1-2 步）；trigger 词表需随真实 miss/false 数据迭代（trace 已具备度量基础）。
2. **执行决策层仍是 LLM 自主**：skill 提供方法，但"何时从只读评估进入 trace 落档/预编剧检查点"的决策在当前任务-项目状态下出现了一次被 truth gate 纠偏的样本（Phase D hint 组）。skill 内容（尤其"校准任务在缺 prerequisite 时先报 blocker"的表述）有下一轮优化空间——这正是 Skill 系统的目的：改方法不再改 Agent。
3. **Producer/SW/CD/Scene 的 replay 未做**（本轮 Phase D 只跑了 Director 正式入口；Producer 委派链的真实 E2E 留给下一轮，需要更接近真实生产状态的项目 fixture）。
4. **user-upload ingestion 只有 schema/trust 边界**（source=user + authority_tier=user + fail closed 校验路径已测试），无 UI、无第二 source 目录扫描开关。
5. **Phase E（真实视频 E2E）未做**——按 40.md §十五顺序，需用户确认前四层后放行真实生成资源。
6. `skill.load` 返回全文一次性注入（无分段/摘要模式）；技能变大后可在 runtime 加 L2 摘要模式，schema 已预留 `applicability`。

## 15. Deviations（对 40.md 措辞的工程调整，语义不变）

- **D1**：skill 包目录为 `src/agents/skill_system/builtin/<id>/`（与 `subagent/` 平级的 Agent 子系统包结构），非 40.md 示例的 `skills/<id>/`；文件名保持 SKILL.md。
- **D2**：`AgentSkillBinding` 内嵌 manifest `bindings` 数组，无独立 binding 存储——§四全部字段（agent_id/enabled/role/routing_priority/authority_tier/activation_mode/role_resource）完整表达；V1 没有第二消费者，user-upload 未来在 registry 层加 binding override 即可，schema 已预留。
- **D3**：不新增独立 `skill.discover` API——discovery 由 L1 catalog + `skill.list` + turn 入口确定性 hint 三通道覆盖（全部复用现有通道，零新 plumbing）。
- **D4**：skill 加载复用**只读工具面**（skill.load 是 tool）而非独立注入通道——加载天然进 trace/events/SSE、天然可观察、天然受 read-only 语义保护；这是与现有架构最一致的 progressive disclosure 实现。
- **不变量全部保持**：Runtime Invariants 零改动；Agent 身份/ownership 稳定；canonical truth 来源不变；无新第三方框架。

## 16. Next recommended experiment

1. **Producer 委派链 replay**：构造有角色/场景/预编剧的完整项目 fixture，走 `producer_turn` 真实入口，验证校准编排中 Producer 的 skill 加载与多 Agent handoff（含 scene_production_prerequisite 条件路由）。
2. **Skill 内容迭代 A/B**：针对 §14-2 的纠偏样本，迭代 `references/director.md`（"缺 prerequisite 时先报 blocker 再进入链路"的表述），用同一 replay 任务对比闸门触发率——验证"改方法不改 Agent"的迭代闭环。
3. **Trigger 词表度量**：收集真实用户任务文本跑 `activation_hint` 离线评估（correct/false/missed activation 计数），迭代 manifest triggers。
4. **Phase E 放行评审**：前四层证据齐备后，由用户决定用哪个已实证案例做少量真实视频 E2E（建议复用 37_fable  fixture），并把 token/latency/生成消耗纳入 §16 对照指标。

## 17. Commit 链（小步，可回滚）

| commit | 内容 |
|---|---|
| `9bc1ae9` | 上一轮遗留 thinking 透传入库（保持 diff 干净） |
| `2c431bf` | 调查结论与设计定稿 |
| `c96c9fd` | domain model + registry + runtime + 两个 Golden Skill 包 |
| `c3cde54` | 5 个 Agent 接线（catalog/工具面/hint/meta.skills） |
| `5f29e41` | 回归锚定更新 + 环境污染基线证明 |
| `18d8069` | Phase A/B/C 测试 26 项 + 2 个实现 bug 修复 |
| `da05574` | System Prompt 减法 + 纪律锚定测试语义反转 |
| `55e512f` | Phase D 真实 API replay 脚本 |

完成标准对照（40.md §二十 1-15）：✅ 架构 / ✅ 调查 / ✅ Registry / ✅ Binding·View / ✅ progressive disclosure / ✅ routing·authority 分离 / ✅ capability·permission 边界 / ✅ 结构化 trace / ✅ Base prompt 接入通用 Skill policy / ✅ 两个实证模式 Golden Skill 迁移 / ✅ workflow 不再重复塞 prompt / ✅ targeted tests / ✅ regression / ✅ Agent 正式入口 replay / ✅ Prompt-less baseline 与 Skill-based 行为差异报告（§13）。

**STOP**——按 40.md 要求，不继续开发第二批 Creative / Situation Skills；下一步由用户根据实际测试结果决定 Skill 内容优化方向。
