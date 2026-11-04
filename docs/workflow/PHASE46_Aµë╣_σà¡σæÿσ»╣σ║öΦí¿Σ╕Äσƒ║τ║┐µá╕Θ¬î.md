# Phase 46 A批｜六员真实入口对应表与基线核验

> 日期：2026-10-04 · 基线 commit `87d7ed2`（= 前端交付报告基线，未发生偏移）· 分支 `feat/agent-v2-phase-d-golden-run`
> 本文档是 PHASE46_01 §3 的 A 批交付：六员对应表、动态组件目录、入口探针与思考模式探针结果。

## 1. 基线核验

| 项 | 结果 |
| --- | --- |
| HEAD | `87d7ed2`（与报告基线一致；保留他人未提交工作，未做 reset） |
| 项目指引 | 无 `AGENTS.md`（与前端依据表结论一致），以 `CLAUDE.md` 为准 |
| 文本模型 | `config/config.yaml` `agnes.models.text: agnes-3.0-flash`（六员全部经此解析，真机目录端点实证六员 modelProfile 均为 agnes-3.0-flash） |
| Director Vision | `director_vision_review.py` 显式 `VISION_MODEL=agnes-2.5-flash`，不在文本切换范围（保留） |
| 正式 Skill | `full-story-relay-calibration` v1.3.1、`keyframes-bridge-repair` v1.3.1（builtin_core/active；后者依赖前者） |
| Skill 绑定名册 | `skill_system/models.py VALID_AGENT_IDS` 含 9 个（不含 assembly）——assembly 不可绑 Skill |
| 持久化格局 | 无数据库；project.json + 各域 JSON/JSONL 原子写 + 各域 OCC 指纹 + 进程内软锁 |

## 2. 六员对应表（全部经真机探针核实）

| 项 | Producer | 编剧 Script Writer | 角色资源 Resource Agent | 场景 Scene Agent | 导演 Director | 装配 Assembly |
| --- | --- | --- | --- | --- | --- | --- |
| 真实 ID | `producer` | `script_writer` | `character_designer` | `scene_designer` | `director` | `assembly` |
| 展示名 | Producer Supervisor | Script Writer | Resource Agent | Scene Agent | Director | Assembly Agent |
| 注册 | `src/agents/registry.py:38-46` | `:47-54` | `:55-63` | `:64-75` | `:76-83` | `:123-132` |
| 正式入口（turn） | `producer.py:1213 producer_turn` | `script_writer.py:1926 script_writer_turn` | `character_designer.py:3037 character_designer_turn` | `scene_designer.py:2177 scene_designer_turn` | `director.py:2748 director_turn` | `assembly.py:2076 assembly_turn` |
| 入口接线 | `runtime.unified_turn` project-scope 分发（`runtime.py:374-407`）；director 无 unified 分支、走流式端点（`routers/agents.py:285-468`） | 同左 | 同左 | 同左 | 流式端点 | 同左 |
| 写域（锁） | 无（委派经 Bridge 复制） | story（`WRITE_OPERATIONS`） | character + 资源域（`WRITE_TOOL_DOMAIN` 双域） | scene_system（单写域） | storyboard（`DIRECTOR_WRITE_OPERATIONS`） | sequence（`WRITE_TOOL_DOMAIN`） |
| changed_refs 域（真机实证） | —（计划节点） | `scenes` / `story`（script.update 整篇写入，Phase 46 补） | `characters/looks/props/clothings/compositions/reference_assets` | `locations/location_views/scene_instances/scene_instance_views` | `scenes/shots` | `sequences` |
| 核心工具数（目录端点实测） | 24（含 5 个 delegate） | 16 | 20 | 22 | 32 | 18 |
| Skill 绑定 | 可（P80/P40） | 可（P60） | 可（P40） | 可（P40） | 可（P100/P90） | **不可**（VALID_AGENT_IDS 不含） |
| 模型 profile | producer_profile→agnes-3.0-flash | text_default | character_profile | scene_profile | text_default | text_default |
| 完成校验（Phase 46 契约） | 计划节点：turn 完成 + 无越权委派 | ≥1 条已验证回执落在 scenes/story | 同左（资源域） | 同左（场景四键） | 同左（shots） | 同左（sequences） |
| 恢复能力 | turn 进程内；Job 长任务经 settlement 对账 | 同左 | 同左 | 同左 | 生成任务 JobQueue + 帧/视频 Job 恢复 | render Job + render_status 深审计 |
| 探针结果 | **PASS**（计划完成，越权委派被服务端守卫拦截面待 F 批验证） | **PASS**（首轮 changed_refs 派生缺口已修：`story` 键 + `results[].scene_id`；复验通过） | 首轮 agent 诚实报 `reference_required` 未写入（OCC 语义，非接线缺陷）；F 批以更准确目标复测 | **PASS**（首轮期望域映射笔误已修：scene_system→四真实键；复验通过） | **PASS**（真实创建 s11/s12 + s11_c01/s12_c01，回执 verified） | 诚实阻塞（无可用成片素材；BLOCKED_OK，符合"合法前置条件阻塞"口径） |

说明：

- 「探针结果」为 2026-10-04 两轮真机探针（`_p46_probe_entries.py`，经正式工作流 API 全链：PUT 定义 → start → 派发 → 真实 turn（thinking=true）→ 收货校验 → 交接包）。第一轮发现 3 处收货校验缺口并当轮修复（见 §4）；F 批按配套测试书继续完整六员制作验证。
- 场景若作为独立卡片运行，仍经 `_delegate_specialist`（保留既有 turn_registry 互斥、session governance、锁接线），未绕过任何父任务约束。

## 3. 动态组件目录（A批 §3.2 交付）

端点：`GET /api/project/{p}/workflow/catalog`（`routers/workflow.py`；来源 `src/workflow/catalog.py`）。

真机响应摘要（项目：雨灯归航）：

- agents=6（producer 24 工具 / script_writer 16 / character_designer 20 / scene_designer 22 / director 32 / assembly 18），全部 enabled、全部 agnes-3.0-flash；
- skills=2（v1.3.1 active，含 contentHash 与 bindings 名册）；
- `assembly.skillBindable=false`（后端名册事实，不是前端假设）；
- `registryDigest`：系统提示+权限+工具面摘要（Agent 注册内容变化即变，供启动快照基线）；
- `expectedArtifactDomains`：完成校验的真实域（§2 表）。

旧 capability-dispatch Agent（production_planner/shot_producer/continuity_reviewer/editor）不进目录（与前端「旧 Agent 不进组件库」一致）。

## 4. 第一轮探针发现并修复的缺陷（真实记录）

| # | 现象 | 根因 | 修复 | 定向回归 |
| --- | --- | --- | --- | --- |
| B1 | script_writer 节点：turn 实际创建 s11/s12（项目事实已写入、回执 verified）但工作流判「无产物」失败 | `producer._derive_changed_refs` 不认 `script.update`（整篇故事写入无 scene_id）与 `story.update_many.results[].scene_id` | 新增 `story` 域（script.update→story_text）+ results[].scene_id 扫描；`AGENT_EXPECTED_DOMAINS` 补 story；交接包/`_settle` artifact_refs 同步 | `test_producer_bridge/_changed_refs_v2/_production_room/_p46` 74 全绿（含 story 键骨架断言更新） |
| B2 | scene_designer 节点：实际产 `locations` 域，期望域写成 `scene_system` | 完成校验域映射笔误（未对照 `_RESOURCE_KIND_TO_REF`） | 期望域改为 locations/location_views/scene_instances/scene_instance_views | 同上 |
| B3 | character_designer 期望域含 `clothing`（单数） | 同上（真实键 `clothings`） | 已改 | 同上 |

## 5. 思考模式探针（A批 §6 交付）

- Provider 层适配既有：`agnes_client._thinking_body`（Agnes→`chat_template_kwargs.enable_thinking`；SenseNova→`thinking.type`）。
- 工作流派发固定 `thinking=True`（`run.snapshot.thinking` 固化于启动快照；attempt 记录 `thinking` 字段）。
- 助手 Producer 解析（M02）：`agentTurn` 端点新增 `thinking` 请求字段，`unified_turn` 透传五员 tool-loop；前端助手固定传 true。
- 出站证据：`agnes_client` 三处文本请求日志升级为 `[text] provider model thinking=True/False`（backend_runtime.log 可逐请求核对）。
- 真机核验：探针 6 run 的 `snapshot.thinking=true`、attempt.thinking=true；backend_runtime.log 中探针期间文本请求全部 `thinking=True`（复验记录见 F 批测试报告）。

## 6. Producer 派发守卫（C批前置，已落地）

- `producer.WF_DELEGATE_GUARD`（ContextVar）：工作流上下文中 Producer 节点的 `*.delegate` 在 `_delegate_specialist` 入口做服务端拦截（不建任务、不进 turn，回执 `workflow_scope_denied`）——非提示词约束。
- 非 workflow 上下文（普通聊天/普通委派）行为不变（guard 默认 None）。
- 单测：`test_producer_guard_blocks_out_of_scope_delegate`（守卫拒绝 + 零 AgentTask 创建）。

## 7. 后端新增/改动清单（A-C 批累计）

| 文件 | 内容 |
| --- | --- |
| `src/workflow/{__init__,models,store,catalog,validate,plan,adapter,executor}.py` | 新增：定义/校验/编译/快照/六员适配/顺序执行引擎 |
| `routers/workflow.py` | 新增：目录/保存(revision 冲突)/校验/编译/启动(幂等)/run 查询/事件/审核决策/继续/停止 + 启动恢复 `recover_workflows` |
| `api_server.py` | 注册 workflow router + startup 恢复挂接 |
| `src/agents/models.py` | AgentTask 加法扩展 `workflow_ref`（运行关联/correlation） |
| `src/agents/producer.py` | `WF_DELEGATE_GUARD` 服务端守卫；`_delegate_specialist` 参数化（requester_agent/workflow_ref）；`_derive_changed_refs` 补 story 域与 results[].scene_id |
| `src/agents/runtime.py` + `routers/agents.py` | `unified_turn`/`agentTurn` 透传 thinking |
| `src/api/agnes_client.py` | 文本请求日志带 thinking 出站状态 |
| 测试 | `tests/test_p46_workflow_engine.py`（18 用例）；既有 changed_refs 骨架断言加法更新 |
