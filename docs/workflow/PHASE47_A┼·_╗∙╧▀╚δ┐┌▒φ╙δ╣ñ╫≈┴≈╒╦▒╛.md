# Phase 47 A批｜基线核实、入口表与工作流完成账本（初版）

> 日期：2026-10-04 · 分支 `feat/agent-v2-phase-d-golden-run` · HEAD `ddb4258`
> Phase 46 接入提交 `86187d3` 的直接子提交是 `ddb4258`（Phase 46 最终报告文档，无代码变更）。
> 本文件是 PHASE47 §3 的 A 批交付：实际执行基线、入口表（§3.2 第一张表）、工作流完成账本
> （§3.2 第二张表，初版随批推进更新）、明确阻塞清单。

## 1. 实际执行基线

| 项 | 结果 |
| --- | --- |
| 子模块 HEAD | `ddb4258`（`docs(workflow): Phase 46 最终报告`），父提交 `86187d3` = Phase 46 接入提交——**HEAD 已前进一个文档提交，与接入基线同源，无需回退** |
| 分支 | `feat/agent-v2-phase-d-golden-run`（未漂移） |
| 工作区 diff | 仅 `components.d.ts` 自动生成变化 + 大量历史未入库测试工件（沿项目 `_31_*`/`_44*` 惯例，不属本轮） |
| 项目指引 | 无 `AGENTS.md`（真实核实），以 `CLAUDE.md` 为准；后端 FastAPI:8000 / 前端 Vite:7777 |
| 文本模型 | `config/config.yaml` `agnes.models.text: agnes-3.0-flash`（六员全部经此；Director Vision 的 `agnes-2.5-flash` 不动） |
| 引擎现状 | `src/workflow/`（models/store/catalog/validate/plan/adapter/executor）+ `routers/workflow.py`；`tests/test_p46_workflow_engine.py` **本轮实测 18/18 PASS（18.4s）** |
| 后端进程 | 启动中（PID 3700，`/api/project/{p}/workflow/catalog` 真实响应） |
| 持久化格局 | 无数据库保持：`projects/{p}/output/workflows/{definitions,runs,events.jsonl,runs_index.json}` 原子写 |
| 模型思考 | 工作流派发固定 `thinking=True`（run 快照固化）；agnes 出站 `chat_template_kwargs.enable_thinking=true` |
| 正式 Skill | `full-story-relay-calibration` v1.3.1、`keyframes-bridge-repair` v1.3.1（builtin_core/active，后者依赖前者）；assembly 不可绑定（`VALID_AGENT_IDS` 名册事实） |

## 2. 入口表（§3.2 第一张表）

### 2.1 六个 Agent

| Agent | 正式入口 | 工作流适配 | 授权工具面（目录端点实测） | 写域（锁） | 成功判据（现状） |
| --- | --- | --- | --- | --- | --- |
| producer | `producer_turn` | 直调 + `WF_DELEGATE_GUARD`（allowedDelegates=[]） | 24（含 5 个 delegate schema，工作流中被服务端拦截） | agent_task | turn 完成 + 无越权委派（无写域要求） |
| script_writer | `script_writer_turn` 经 `_delegate_specialist` | requester=workflow + workflow_ref | 16 | story | `scenes`/`story` 域 ≥1 条 ok+verified 回执 |
| character_designer | 同上 | 同上 | 20 | characters+资源域（双域） | `characters/looks/props/clothings/compositions/reference_assets` ≥1 |
| scene_designer | 同上 | 同上 | 22 | scene_system | `locations/location_views/scene_instances/scene_instance_views` ≥1 |
| director | `director_turn`（原生 tool-loop） | 经 `_delegate_specialist` | 32 | storyboard | `shots` ≥1 |
| assembly | `assembly_turn` | 经 `_delegate_specialist` | 18 | sequence | `sequences` ≥1 |

适配实现：`src/workflow/adapter.py dispatch_node`（五员走 `_delegate_specialist` 带 workflow_ref；producer 直调 + 守卫）；收货校验 `adapter.verify_completion`；交接包 `adapter.build_handoff`。

### 2.2 两个正式 Skill

| Skill | ID/版本 | 内容 hash 来源 | 引用文件 | 允许绑定 | 实际加载位置 |
| --- | --- | --- | --- | --- | --- |
| 尾帧接力校准 | `full-story-relay-calibration` v1.3.1 | `_skill_content_hash`（manifest+SKILL.md+references/*.md 全文件字节） | SKILL.md + references/{producer,script_writer,character_designer,scene_designer,director,production-recipes,capability-facts}.md | VALID_AGENT_IDS 内 9 成员（producer/script_writer/character_designer/scene_designer/director 等；不含 assembly） | Specialist turn 内 `skill.load`（持久会话 meta.skills 留证据；`adapter.last_skill_evidence` 提取） |
| keyframes 接续桥 | `keyframes-bridge-repair` v1.3.1 | 同上 | SKILL.md + references/{director,producer}.md | 同上（director 可绑；依赖 relay-calibration） | 同上 |

### 2.3 用户决定入口（真实认证方式）

| 决定 | 真实入口 | 认证 |
| --- | --- | --- |
| 资源批准（prop/composition/look reference） | `POST /api/project/{p}/props/{id}/approve`、`/compositions/{id}/approve`、`/looks/{id}/review/human` 等（`routers/resources.py`） | 服务端写 approved 指针；审批前强制 review pass 证据 |
| 图像候选选择（角色视图/分镜图） | `routers/characters.py`、`routers/shots.py`（selected_index / selected_first_frame 等 REST 入口） | 用户专用 REST 入口，非 Agent 可达 |
| 视频选片 | shot 级视频候选选择入口（`routers/shots.py`） | 同上 |
| render 选择与 final | `POST /sequences/{sid}/renders/select`、`final_render`（`routers/sequences.py:254`） | 同上（Phase 45 用户定版语义 freshness=current） |

### 2.4 长任务

| 机制 | 位置 | 语义 |
| --- | --- | --- |
| 任务 ID 落盘 | AgentTaskStore（`src/agents/tasks.py`，workflow_ref 关联） | AgentTask 持久化 + structured_result 结算 |
| provider ID/查询 | JobQueue（`src/core/job_queue.py`）+ 各 provider client | 崩溃恢复 `recover_pending_videos` 续轮询不重提交 |
| 结算对账 | settlement（`src/agents/settlement.py`） | 真长任务收货；45 批 interrupt 诚实结算 |
| 恢复扫描 | `routers/workflow.recover_workflows`（startup） | 在途 attempt 按 workflow_ref 对账：adopted / needs_reconciliation |

### 2.5 工作流接口

| 接口 | 语义 |
| --- | --- |
| `GET /workflow/catalog` | 动态组件目录 |
| `GET/PUT /workflow/definitions/{wid}` | 定义读取/保存（revision 冲突 409 + contentHash） |
| `POST .../validate` / `.../compile` | 校验/编译（无副作用） |
| `POST .../runs` | 启动（幂等键 + preflight + 启动快照） |
| `GET /workflow/runs` / `.../runs/{rid}` / `.../events` | 查询 |
| `POST .../reviews/{rid}/decision` | 审核决策（幂等/对象指纹 409） |
| `POST .../continue` / `.../stop` | 显式继续/停止 |
| startup `recover_workflows()` | 恢复扫描 |

## 3. 工作流完成账本（§3.2 第二张表；初版）

状态口径：未实现 / 仅定义 / 接口已通 / 真实生产通过 / 等待用户决定 / 有明确限制 / 失败待修 / 预期阻塞通过（不计正常成功）。

| ID | 执行链 | 定义位置 | 配置项 | 前置条件 | 代码状态 | 真机 run | 产物 | 用户决定 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| W00 | producer 计划→审核 | 待 C 批建模板 | objective/审核 | 项目已建 | 引擎能力已通（P46 探针 producer PASS） | P46: run_56c0cd9c2d29 | 持久化计划（会话内） | 无 | 接口已通；模板待建 |
| W01 | 编剧→审核（可前插 producer） | 待建 | 新写/修订范围 | 项目已建 | 引擎已通（P46 探针 PASS + 双审核切片） | P46: run_d9591d01697c、P46_F_DualReview: run_5606b7226160 | s11/s12 新故事 | 无（切片为工程验收） | 接口已通；复用语义待 B 批 |
| W02 | 角色资源→审核 | 待建 | 角色/Look 目标 | 无（新项目可） | **失败待修**：P46 探针 reference_required 阻塞（预期阻塞，非接线缺陷）；B 批修正常路径 | P46: run_87245713b4b0（诚实阻塞） | 无图像 | 无 | B 批 §4.1 |
| W03 | 场景设计→审核 | 待建 | 故事段/地点/时段 | 故事存在 | 引擎已通（探针 PASS） | P46: run_28f5da00ac58 | loc_2ec0b9888969 + 场景事实 | 无 | 接口已通；场景视图图像待 C 批验证 |
| W04 | 导演分镜→审核 | 待建 | 故事/资源引用 | 故事+资源 | 引擎已通（探针 PASS 建 s11/s12+c01） | P46: run_5ded96663eb2 | shots | 无 | 接口已通；分镜图候选待 C 批 |
| W05 | 导演视频→审核（绑尾帧 Skill） | 待建 | 批准首帧/素材 | 首帧批准 | 接口已通；真实视频生产待 C/D 批 | 无 | 无 | 无 | 待真实视频 |
| W06 | 导演诊断修复→审核 | 待建 | 问题视频/根帧 | 已有视频 | 待建 | 无 | 无 | 无 | 未实现（模板待建） |
| W07 | 导演端点桥接→审核（绑 keyframes Skill） | 待建 | 两合格端点 | 两端点合格 | 待建 | 无 | 无 | 无 | 未实现（模板待建） |
| W08 | 装配→审核 | 待建 | 已选视频/音频政策 | 用户已选视频 | **失败待修**：assembly 无精确素材交接语义（B 批 §4.7） | P46: run_a7e2a77c7162（诚实阻塞） | 无 | 无 | B 批 |
| W09 | 六员完整生产双审核 | 待建 | 新故事目标 | 全前置 | 部分成员已通；整链未跑 | 无 | 无 | 无 | C/D 批 |

## 4. 明确阻塞清单（A 批出口）

1. **character_designer 正常路径未通过**：P46 探针目标是"新建项目建角色"但 agent 陷入 `resource.prop.register` 的 `reference_required`（诚实拒绝，无参考图不写 canonical）→ 本 attempt 无资源域写入 → 节点失败。**不是 OCC/接线缺陷，是节点目标与工具约束的错配 + 完成校验未按节点目标细分**。B 批 §4.1 处理：区分四种情况（道具缺参考≠角色缺图），角色/Look/视图有真实图像生成工具（`character.generate_views` 走 JobQueue、`resource.look.prepare`）。
2. **assembly 缺精确素材交接**：完成校验只看 `sequences` 域写入，不校验所采用视频版本来自交接包指定范围（"找最新视频"风险）。B 批 §4.7。
3. **复用语义未实现**：`allow_verified_reuse` 不存在；节点重跑只核验旧素材会诚实失败。B 批 §4.3。
4. **审核只有 approve/reject**：无"批准并选用"路径，审核通过不产生 selected 指针；两步用户操作（选片+审核）无对账记录。B 批 §4.6。
5. **节点完成校验只看域命中**：无"目标产物逐项核对"（如分镜图候选齐备、视频文件可播放）。B 批 §4.2。
6. **模板机制不存在**：W00-W09 无模板落地路径，无"从模板新建"。C 批。
7. **交接包不含 disposition/contentHash/reviewState**：现有 artifactRefs 只有 kind+id。B 批 §4.4。

## 5. 旧 Phase 46 执行书映射

两份旧执行书真实存在：`详细流程文件/新阶段2/PHASE46_01_SIX_AGENT_WORKFLOW_INTEGRATION.md`（A-F 批接入书）与 `PHASE46_02_WORKFLOW_REAL_TEST_AND_ACCEPTANCE.md`（真实测试与验收书，含 M/C/A/H/R/U/E 组与 §9 完整生产 E01-E06、§7 恢复 R 组）。82 情景全覆盖计划见其 §5/§7/§9；P46 报告 §5 记录已执行 18 引擎用例 + 探针 + UI + 双审核切片。**本轮 PHASE47 §9 的 48 情景为自包含编号（P47 01-48），不沿用旧编号语义**；旧 E02-E06/R07/R08/R15 的对应验证并入本轮 P47 35-42（恢复组）与 C/D/F 批真机项，对应关系在 `PHASE47_TEST_EVIDENCE_INDEX.md` 建立。
