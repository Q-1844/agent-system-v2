# Phase 46｜六 Agent 工作流前后端对接报告

> 日期：2026-10-04 · 分支 `feat/agent-v2-phase-d-golden-run` · 接入基线 `87d7ed2` → 本阶段提交 `86187d3`
> 执行书：PHASE46_01（A—F 批）+ PHASE46_02（真实测试与验收）。本文按 §13.3 结构如实记录：**已完成的验证**与**尚未完成的验证**分开写，不预填通过数字、不把切片包装成整链验收。

## 1. 基线与范围

- HEAD 核验：接入起点 `87d7ed2`（与前端交付报告基线一致，无偏移）；无 `AGENTS.md`（以 CLAUDE.md 为准）。
- 范围：三条服务职责（定义与校验／顺序执行与任务适配／查询与前端投影）；三类组件（Agent／Skill 只绑定／人工审核）；单条连通顺序链；通过→继续、驳回→停止。未加条件分支／并行／返工循环／子工作流。
- 持久化：无数据库格局保持——`projects/{p}/output/workflows/`（definitions／runs／events.jsonl／runs_index.json），全部 atomic 写。
- 复用底座：AgentRegistry／AgentTaskStore／turn_registry 互斥／ProjectEventBus 锁与 changed 事件／JobQueue／OCC 指纹／truth gates／session governance——未重建任何执行机制。

## 2. 六员真实入口与产物对应

见 [PHASE46_A批_六员对应表与基线核验.md](PHASE46_A批_六员对应表与基线核验.md)（同目录）。要点：

| Agent | 入口 | 完成校验域（changed_refs 真实键） | 入口探针结果 |
| --- | --- | --- | --- |
| producer | `producer_turn` + WF_DELEGATE_GUARD 守卫 | 计划节点（无写域要求；越权委派被服务端拦截） | PASS |
| script_writer | `script_writer_turn` 经 `_delegate_specialist` | `scenes` / `story` | PASS |
| character_designer | 同上 | `characters/looks/props/clothings/compositions/reference_assets` | 诚实阻塞（OCC reference_required，agent 未完成即如实报告） |
| scene_designer | 同上 | `locations/location_views/scene_instances/scene_instance_views` | PASS |
| director | 同上（原生 tool-loop，保持流式端点能力不变） | `shots` | PASS（真实创建 s11/s12 + 两镜清单） |
| assembly | 同上 | `sequences` | 诚实阻塞（无可用成片素材，合法前置条件阻塞） |

动态目录 `GET /api/project/{p}/workflow/catalog`：六员工具面（24/16/20/22/32/18）、两正式 Skill v1.3.1（含 contentHash）、assembly 不可绑定（VALID_AGENT_IDS 名册事实）、旧 capability-dispatch Agent 不展示。

## 3. 契约 / API / 状态 / 审核 / 恢复实现

| 语义 | 实现 |
| --- | --- |
| 定义保存 | `PUT /workflow/definitions/{wid}`：服务端 revision 单调递增 + `expectedRevision` 冲突 409 + contentHash；草稿可带错保存、启动必挡 |
| 校验 | `POST .../validate`（无副作用）：结构十条（§4.3），环/分叉/汇合/孤立/Skill 端口/绑定名册全部独立重查 |
| 编译 | `POST .../compile`：确定性拓扑（忽略坐标与边界），产出目标/上游/Skill/审核对象/期望产物域；不给 LLM 解释顺序的权限 |
| 启动 | `POST .../runs`：幂等键（同键同参复用 / 同键异参 409）+ preflight（缺目标/禁用 Agent 具体缺项）+ 启动快照（冻结定义+Agent 基线+Skill hash+模型+thinking=True+canonical 指纹） |
| 状态机 | 节点 pending/dispatching/running/waiting_human/needs_reconciliation/failed/stopped/success；run 由节点推导（waiting_human/needs_reconciliation 不合成 running） |
| 交接 | attempt 先落盘（R03 窗口）→ AgentTask 带 `workflow_ref`（R04 correlation）→ 成功后交接包（taskRefs/artifactRefs/摘要/警告）→ 逐节点 canonical before/after 指纹账本 |
| 审核 | 持久化申请（对象=上游 attempt 产物引用+指纹）→ 幂等决策（同决策幂等/异决策拒绝/对象变更 409）→ 通过驱动下一节点唯一启动，驳回停止下游 |
| 恢复 | startup `recover_workflows`：在途 attempt 按 workflow_ref 对账——已结算收养继续、运行中→needs_reconciliation（不盲重发）、审核等待保持等待；已完成节点不重做 |
| 前端投影 | 运行面板（run 状态/定义版本/模型+思考/节点链/错误/任务引用/待审卡片）；轮询 2.5s + 终态停轮；刷新后从 runs 列表恢复观察 |

## 4. 文本模型思考模式核验（M 批）

- Provider 层 `chat_template_kwargs.enable_thinking`（既有适配）；工作流派发固定 `thinking=True` 且固化于 run 快照；attempt 记录 thinking 字段。
- M02：`agentTurn` 端点 + `unified_turn` 透传 thinking；前端助手解析固定 true。
- 出站证据：`agnes_client` 文本请求日志 `[text] provider model thinking=…`。**本轮后端会话 117 次真实文本请求全部 thinking=True（唯一 "False" 匹配是旧堆栈回显，非请求日志）**；涵盖六员 turn、工具回填、重试、UI run。
- 未暴露 reasoning usage 时如实记录"已请求开启、内部用量未暴露"（不虚构 token 数）。

## 5. 测试总表（已执行轮次；完整 82 情景覆盖计划见测试书，未全部执行）

| 组 | 已执行 | 结果 |
| --- | --- | --- |
| 引擎单测（零 LLM） | `tests/test_p46_workflow_engine.py` 18 用例：校验负例（环/分叉/汇合/孤立/未知 Agent/Skill→审核/缺目标）、编译、revision 冲突、幂等键、happy 链、双审核、驳回停止、停止、对账（no_task/adopted/needs_reconciliation）、守卫、envelope | 18/18 PASS |
| 全量回归 | `pytest tests/ -q` | **2641 passed, 0 failed**（首轮暴露 routers/workflow.py 的 AgentError 导入错，修复后全绿） |
| 入口探针 M/A（3 轮真机） | `_p46_probe_entries.py`：六员各一条单节点链经正式 API 全链 | 第 1 轮暴露 3 缺陷（§6）；第 3 轮：producer/script_writer/scene_designer/director PASS，character_designer/assembly 诚实阻塞（BLOCKED_OK） |
| UI 真机（browser） | 搭链（助手 6 指令）→ 填目标/审核说明/审核者 → 后端保存 → 运行 → 节点实时状态 → 真实完成 → 等待审核 → 通过 → 完成；刷新恢复观察；失败诚实呈现；冻结定义语义（旧 run 用 r8，新 run 用 r11） | 全部通过；截图 `_tmp/wf46_accept/10_run_completed_with_review.png` |
| 双审核 F 切片（真机，新项目） | `_p46_fbatch_dual_review.py`：P46_F_DualReview 全新项目，编剧真实新写两段（s01/s02）→ 一审过 → 二审独立等待 → 二审过 → completed；双意见存档；17 事件 | PASS |
| 思考覆盖 | 本会话全部真实文本请求 | 117/117 thinking=True |

## 6. 缺陷、根因、修复与定向回归（全部真实轮次记录）

| bug | 第一处偏离 | 根因 | 修复 | 定向回归 |
| --- | --- | --- | --- | --- |
| B1 | 探针1：script_writer 实际创建 s11/s12 但节点判"无产物" | `script_writer_turn` 返回的是会话 meta 白名单投影 `receipts_meta`（剥掉 created/results 富字段）→ `_derive_changed_refs` 拿不到批量工具产物 id | turn 返回新增 `receipts_raw`（原始回执），`_wrap_task_result` 优先取用；`_derive_changed_refs` 补 `story` 域（script.update 整篇写入）与 `results[].scene_id` | `test_producer_bridge/_changed_refs_v2/_production_room/_p46/…` 105 绿（含骨架断言加法更新） |
| B2 | 探针1：scene_designer 期望域 `scene_system` vs 实际 `locations` | 完成校验域映射笔误（未对照 `_RESOURCE_KIND_TO_REF`） | `AGENT_EXPECTED_DOMAINS` 对齐真实键 | 引擎测试 |
| B3 | 探针1：character_designer 期望域 `clothing`（单数） | 同上 | 改 `clothings` | 引擎测试 |
| B4 | 回归 177 ERROR | `routers/workflow.py` 从 `src.agents.models` 导入 `AgentError`（实际在 runtime） | 改从 `runtime` 导入 | 全量回归 2641 绿 |
| B5 | 决策/继续在无事件循环上下文崩溃风险 | executor 在 `decide_review/continue_run` 内 `ensure_future` | 驱动职责上移到 async 路由层（状态落地与驱动分离） | 引擎测试 |
| B6 | 同幂等键重复建 run | runs_index 行未存幂等键 | 索引补 `idempotencyKey/startParamsHash`；参数摘要改为对定义全文 hash | 引擎测试 |
| B7 | UI 复用场景：agent 发现故事已存在而拒绝重复写入 → 节点 failed | 这是**诚实行为**（不是 bug）：v1 完成校验要求"本 attempt 有新写入"，复用语义（§5.3 标为复用）未实现 | 保持严格（不把读旧素材伪装成新产物）；用户以"必须真实写入"目标重跑即通过；复用标记列为未完成项 | UI 真机两轮 |

## 7. 冻结后新会话生产与稳定性（**部分完成，如实说明**）

已完成：新项目双审核切片（§5）在冻结基线 `86187d3` 后独立跑通。**尚未执行**：测试书 §9 的完整 E01（六员带真实图片/视频/旁白/成片的整链）、E02-E06、三轮稳定性轮次。这些是本阶段验收的剩余门槛，不能由上述切片替代。

## 8. 视频、声音、成片与用户决策状态

- 本阶段**未产生新视频/成片素材**（入口探针与双审核切片均为文本/结构域真实写入，未提交图片/视频生成）。
- 未触碰任何既有项目的 selected／final 指针；`select_render`／`final_render` 等用户专用入口未变。
- pending 用户决策：无（尚无可审成片材料）。

## 9. 未完成能力、限制与明确边界

1. **F 批完整验收未完成**：六员新项目整链（含导演真实视频候选、装配成片）、E02-E06、三轮稳定性——剩余门槛。
2. 复用语义未实现：节点重跑时"仅核验既有素材"会诚实失败（§6 B7）。
3. 前端助手已知限制保持原样（按数量删除/清空已填内容，未影响本接入，未修）。
4. `waiting_external` 状态预留未启用（v1 turn 内部轮询视频）；晚到结果对已终局 run 的隔离路径仅由单测覆盖（R15 真机演练未做）。
5. 事件流为轮询 + events.jsonl 游标（§8.3 允许），未做 SSE 推送。
6. 恢复范围声明：run/节点/审核状态可跨重启恢复；在途 in-process turn 不可跨进程恢复（诚实 needs_reconciliation + 显式重试）；JobQueue 侧长任务维持既有 settlement 对账。

## 10. 结论（四项分开，不互相替代）

- **接入是否可用**：可用。保存/校验/编译/启动/状态/审核/停止/恢复/幂等/守卫已实现并有单测+真机证据；浏览器可完成搭建→运行→观察→审核→完成全流程。
- **六员生产是否通过**：部分通过。四员有"一次真实职责内新产物"证据（producer 计划／script_writer 两轮新故事／scene_designer 地点事实／director 镜头清单+场景）；character_designer 与 assembly 为诚实阻塞（未伪造）。**完整六员协作整链（含视频/成片）未验证。**
- **恢复是否通过**：机制通过（单测对账三态 + UI 刷新恢复 + 启动恢复扫描）；真机进程杀断演练（R07/R08 级）未做。
- **用户画面/音频验收**：不适用——本阶段无可审媒体，未请求用户决策。

## 11. 提交链与证据索引

| 项 | 位置 |
| --- | --- |
| 接入提交 | `86187d3`（26 文件，+3698；src/workflow 包、routers/workflow.py、前端三文件+类型、agnes 底座最小扩展、18 测试用例） |
| A 批对应表 | `docs/workflow/PHASE46_A批_六员对应表与基线核验.md` |
| 引擎测试 | `tests/test_p46_workflow_engine.py` |
| 真机探针脚本（工作区，沿 `_31_*/_44*` 惯例未入库） | `_p46_probe_entries.py`（+`_p46_probe_entries_result.json`）、`_p46_fbatch_dual_review.py`（+`_p46_fbatch_dual_review_result.json`）、`_p46_repro_sw_changed.py` |
| UI 截图 | `_tmp/wf46_accept/10_run_completed_with_review.png` |
| 贯通 run 索引 | P46_Probe：run_5d434036b695(producer)/run_af6f33273b44→第3轮 sw PASS/…/run_c8af274bc350(director)；P46_F_DualReview：run_5606b7226160（双审核完成） |
| thinking 证据 | `backend_runtime.log`（本轮会话 117 条 thinking=True 请求日志） |
