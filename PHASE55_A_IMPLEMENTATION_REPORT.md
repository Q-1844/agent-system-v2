# PHASE55 A 批实施报告——核验依据表与统一契约（2026-10-17）

任务书：`测试5/PHASE55_SYSTEM_INTEGRATION_AND_PARALLEL_EXECUTION.md`（用户授权工程实施）。
分支：`feat/phase55-collab-parallel`（基于 phase52/efficiency = Phase54 全部提交 51abc2e）。
本报告是 A 批交付物：§1 核验依据表（真实路径核验，非照抄 Phase54 报告）、§2 报告数字对账、§3 统一契约实施、§4 定向验证。

## 1. 核验依据表（最短版）

所有路径相对仓库根 `animation-storyboard/`。五路并行核验（六员/grant、参考链/生成契约、调度/恢复、会话/上下文、审核/账本），关键结论：

### 1.1 真实入口与六员/Skill/grant

- **Agent 清单**：`src/agents/registry.py:27-33` = producer + 9 Specialist（SW/CD/SD/Director/Assembly 五个原生 tool-loop + production_planner/shot_producer/continuity_reviewer/editor 走 capability 通道）。「六员链」= producer + 前五个。只有 producer 有 `can_delegate_to`。
- **Turn 链路**：`routers/agents.py` POST /turn(:232)、/turn/stream(:289)、grant 三端点(:1133/:1148/:1159) → `src/agents/runtime.py:349` `unified_turn` 按 agent_id 分流到各模块 `*_turn`（producer.py:1275 `producer_turn`）。
- **Scoped grant**：`src/agents/grant.py`——5 个 family（:19-25，= Specialist 模块工具面）、`granted_tool_face`(:133) 合并、TTL/revoke、`grants_audit.jsonl` 全程审计。producer 挂钩点 = producer.py:1367-1417（工具面合并+审计包裹）。
- **Skill 系统**：`src/agents/skill_system/`——目录常驻+按需 `skill.load`（非整篇注入）；工作流启动快照已钉 skillVersions（`src/workflow/plan.py:119`）。
- **run 快照**：`workflow_snapshot_v1`（`src/workflow/plan.py:116-147`）= 仓库唯一成熟 run 级配置快照（definition 全文+agent 基线+skill 版本+模型+toolPolicy+canonical 基线）——协作模式注册表快照直接复用此范式。
- **声明式工作流引擎**：`src/workflow/`（executor/store/models/selection/outputs/repair_scope）+ W00-W09 模板（`templates.py`，W09=六员完整生产 14 节点）。producer 自由 turn 的 workflows.json 无快照。
- **AGENTS.md 不存在**；CLAUDE.md 为阶段5 旧版（仅供服务启动命令参考）。

### 1.2 BOUND 与 Producer 参考差异（§3 最高优先核验）

两条路径**共用同一个 video handler**（`routers/shots.py:1533` `_handler_video`），真正分叉点 = `routers/shots.py:1699` `shot_has_visual_production_binding`：

| 环节 | BOUND（六员链） | Producer 两条入口 |
| --- | --- | --- |
| sciv 场景图 | `link_mode=multi`+哨兵 `video_reference_asset_id=prev_tail_look_scene` → 第三参考槽（`src/generation/video_adapter.py:528,:378`）；i2v 经首帧间接 | **完全缺席**（唯一视觉输入=首帧；场景只可能间接进首帧） |
| 角色 Look | relay 哨兵第二槽 / multi 直生选 hero（:342,:298） | 缺席 |
| prop | pack→prompt 生产上下文 | 仅创作文本 |
| 人数子句 | `apply_population_clause`（handler:1611 默认开） | 同一 handler，同样生效 |
| 绑定门槛 | `_gate_ready_pack` READY 门 fail-closed | 无绑定检查 |
| 字段校验 | 编译层槽位互斥（agnes_adapter.py:88-146） | kwargs 直通 client 才抛错（后置） |
| provenance | VideoVersion.generation_basis 全键 + input_visuals 三参考语义 | UNBOUND basis=首帧指针单条 |

- **Producer 视频入口**：`src/agents/director_visual.py:602` `_execute_shot_generate_videos`（视频分支无 reference_policy/scene_reference_mode 参数）+ `src/agents/shot_producer_capability.py:211` `execute_shot_production`（preflight 只查首帧就绪+锁定，不查参考/绑定）。
- **negative_prompt M5 事故链**（复现确认 `backend_p54.log:163-285`）：storyboard 层 `shot.update` 写入 → handler :1747 无条件 kwargs 直通 → client :649 提交时抛错 = **无提交前编译校验**。`provider_options`（:1642）把 negative_prompt 计入 basis 指纹。
- **模型能力层**：无声明式 capability registry；能力事实散在 `agnes_client.py:557-622`（2.5 契约硬编码）、`agnes_adapter.py:9-16`、Skill `capability-facts.md`。
- **人数子句**：`src/generation/image_adapter.py:773` `_population_constraint_clause`（确定性计数）；首帧层 legacy 路径 :674-686 不加（BOUND 分支才传参）。

### 1.3 调度状态与恢复

- **JobQueue**（`src/core/job_queue.py`）：5 状态；video worker 默认 1（**`AGNES_VIDEO_INFLIGHT` 环境变量已可调**，:212-217）；轻任务并发 3；`cache/jobs.json` 原子落盘；启动恢复 `recover_pending_videos`（`services/lifecycle.py:12-74`）先查原任务再续轮询，不盲目重提交。
- **provider ID 即时落库**：`shots.py:1777-1784` 提交成功立刻写 video_id/task_id（崩溃可续轮询）。
- **🔴 watchdog 缺陷（M5 事故路径）**：`job_queue.py:319-342` 墙钟 600s 无进度 → FAILED+cancel；机器休眠被误判卡死；FAILED→retry 走 `_handler_video` else 分支**重新 create_video**（:1655-1657 注释自证）→ 重复生成。**watchdog 本身不补交，补交是 FAILED 后的 retry 路径**。
- **提交结果不明**：`shots.py:1670-1686` 已诚实标注 `previous_submit_uncertain`；**无逻辑请求 ID/幂等键**，无法向 provider 对账。
- **收货幂等缺失**：完成即无条件 append VideoVersion（:1858-1864），无按 video_id 去重。429 **不解析 Retry-After**（`agnes_client.py:284-323` 固定指数退避）。
- **额度**：QuotaLedger 3 桶（video_seconds/images/text_calls，`src/core/quota.py`）；RateLimiter 6 桶（`src/core/rate_limiter.py:35-42`）——**额度桶与限流组不对应**，无跨项目共享问题（单进程全局单例）。
- **OCC 现状**：`ProjectStore.update_shot` = 整文件原子写、**无 CAS/owner**（`src/core/write_lock.py:4-5` 自证）；并发纪律靠 per-project asyncio.Lock（canonical commit 短临界区）+ ChangeSession 锁 + workflow selection 双回执。AgentTask `execution_ref`（`src/agents/models.py:334`）已支持重启 reattach 不重提交。

### 1.4 上下文计量与会话

- **token 计量不存在**：非流式 usage 被丢弃（`agnes_client.py:373-378`）；流式 finish 事件已带 usage（:147-148）但 tool-loop 只取 content/tool_calls；producer max_tokens 恒 65536（producer.py:1088）；唯一度量=字符数遥测。
- **P54-B1 524 事故精确定位**：`backend_p54.log:788`「maximum context length of 524288 tokens… 467222 input + 65536 completion」——**467k=失败请求的 input tokens，524k=provider 上下文硬上限（input+completion）**。任务书 §1「不同计量对象」之疑已解。
- **无压缩/裁剪**：history 全量（producer.py:1304）、工具回执整包进 messages（:1267）；`reground_session`（`session_governance.py:336`）= 现成的「归档+状态头重建」接力骨架（按结构写次数触发，不按 token）。
- **turn 互斥**：`turn_registry.py:30` 进程内 dict（重启即清，不构成跨重启主控租约）。
- **会话接力**：代码库无 handoff schema（人工 split 仅见 backend_p54.log:788-800）。

### 1.5 审核与账本

- **审核卡**：run 文档 `reviews[]`（`src/workflow/executor.py:736-759`），指纹 409 防篡改，refresh 端点实际在 **`routers/workflow.py:471`**（非 Phase54 报告所称 routers/agents.py）。
- **一票否决已代码化**：`src/agents/shot_review.py:55-58` `SHOT_REVIEW_HARD_DIMS`（duplicate_identity/unexpected_people/missing_character 等 6 项硬维）+ `derive_overall`(:178) 确定性推导，模型不许翻案。
- **抽帧工具**：五位采样已存在（`src/storyboard/continuity_service.py:25` SAMPLE_POSITIONS + `src/agents/director_frames.py:103` `visual.sample_frames`）——审核流水化可直接组合「采样+shot_review 单帧 14 维」。
- **修复守卫**：repairRequireNew（executor.py:1212-1220）+ prior credit 封堵（`src/workflow/outputs.py:429-440`）+ REPAIR_LIMIT=3 豁免卡（:1133）——Phase54 A2/A3 全部在位。
- **账本**：EventLog append-only（`src/workflow/store.py:190`）、grants_audit.jsonl、QuotaLedger、`cache/p50_timing_ledger.jsonl`——无统一 per-run 统计 API，耗时需从 attempts+events 推导。

## 2. 报告数字对账（任务书 §1 表逐项）

| 项目 | 复算结论 |
| --- | --- |
| 两天 391 vs 491 | **差额 ~100s 主因=B3（P54_M4 84s 实测文件时长）未计入 10-16 的 90s + M5/M6 跨日**。按视频文件实测：10-15=P53_M 94.0+P54_M2 94.6；10-16=P54_M3 91.1+P54_M4 84.0；10-17（凌晨跨日）=P54_M5 104.5+P54_M6 81.2。报告的「10-16: 90s」漏计 B3；「491」把 10-17 凌晨的 M5/M6 部分计入。**结论：漏行+跨日兼有，非汇总方法错误**。 billed（按提交秒数计费，含失败重试）≥ 文件时长（只计成功落盘），故 402/500（10-17 账本）>185.7（10-17 文件）为正常口径差。 |
| 467k/524k | 同一对象：524288=provider 上下文硬上限，467222=溢出请求 input tokens（+65536 completion 预留超限）。见 §1.4。 |
| 速度排序 1.74<2.87<4.73<5.4 | 排序本身与各样本墙钟一致（观察性）；任务书已定「不据此删模式」，本轮以同口径对照为准。 |
| 零回归口径 | Phase54 用失败子集 stash 对照=预存环境失败一致；本轮回归按「定向+全量+失败集合与基线对比」口径执行。 |
| watchdog「作废+自动补交」 | 精确机制=watchdog 只作废（FAILED+cancel），补交发生在 FAILED 后的 retry 重新提交（§1.3）。 |

## 3. 统一契约实施（本轮代码改动）

### 3.1 设计原则

- **不重建事实底座**：manifest 挂在既有 `VideoVersion.generation_basis` / `ImageBatch.generation_basis` / `job.params` 上， additive；不新增项目事实存储。
- **等价策略**：Agent 层生产视频提交统一要求 BOUND 参考链（基线等价）；REST/legacy 兼容路径保留（旧项目/UI 手动流程），但 UNBOUND 视频在 manifest 中显式标注 `scene_reference_consumed` 状态，不静默。
- **M5 教训编译化**：negative_prompt 项目层意图 → 提交前编译为正文约束（记录进 basis）；未知字段/互斥槽位/不合法时长 → **入队前**明确报错（不再排队后逐个失败）。
- **A2/A3 守卫保留**：repairRequireNew、refresh 语义不动。

### 3.2 改动清单

| # | 文件 | 内容 |
| --- | --- | --- |
| A1 | `src/generation/video_contract.py`（新） | 统一视频提交契约：`validate_video_submission()`（时长 4-12、multi 需 binding、未知参数拒绝、negative_prompt 编译）+ `compile_negative_prompt()`（项目层意图→正文约束+basis 记录）+ `scene_reference_consumed()` 判定（provider_reference/via_first_frame/absent）。 |
| A2 | `src/agents/director_visual.py` | `shot.generate_videos` 视频分支：提交前走统一契约闸（含 BOUND 要求+negative_prompt 编译+时长校验），非法请求入队前明确拒绝。 |
| A3 | `src/agents/shot_producer_capability.py` | `execute_shot_production` 视频分支：同一契约闸（角色调用差异修复的核心——Producer 与 Director 同一合法请求路径）。 |
| A4 | `routers/shots.py` | `_video_preflight` 增补契约校验；handler 内 negative_prompt 编译消费；REST 提交端点入队前校验。 |
| A5 | `src/generation/input_manifest.py`（新） | 统一输入依据清单 builder：故事/镜头版本、方法、资产与版本、参考用途、上游已选版本与端点、人数规则、adapter 请求模式；落 `job.params.input_manifest` + `VideoVersion.generation_basis.input_manifest`。 |

（实施细节与测试结果见 §4，随实施回填。）

## 4. 定向验证与回归

### 4.1 新增定向测试（27 项全过）

| 文件 | 项数 | 覆盖 |
| --- | --- | --- |
| `tests/test_p55_video_contract.py` | 19 | 编译校验（时长/multi 需绑定/BOUND 闸/未知参数）、negative_prompt 编译、参考策略三级解析、场景参考消费判定（provider_reference/via_first_frame/absent）、输入依据清单（BOUND 资产清单 vs UNBOUND 诚实 None）、director 工具默认闸+项目级 legacy 放行+时长越界入队前拒绝、REST 旧端点（multi 无绑定 422/legacy i2v 放行+manifest/时长 422） |
| `tests/test_p55_collab_modes.py` | 8 | 注册表五预设+组合校验、run 快照+参考策略落盘+grant 入档+步骤账本、预铺并行编排（CD/SC→producer）、W09 真实实例化+接线、取消+终态幂等、REST 端点（list 可用性/400/404/get/cancel） |
| `tests/test_p55_recovery.py` | 6 | watchdog 休眠保护（provider task 在途不作废+计数留痕）、延长上限如实 FAILED、无 task ID 维持原行为、task ID 随 job 落档（真实 handler 链路）、收货幂等（同 video_id 双次完成只落一个版本且指针不悬空）、并发档位（config=3 生效/缺省 1 不变） |

### 4.2 既有测试适配（语义变更显式化，共 11 处）

- `test_director_visual.py`：legacy i2v 视频路径 3 测试显式传 `reference_policy=legacy_allowed`（时长夹具 3.0→5.0 合同化）；`test_generate_videos_ok` 增补 input_manifest 断言。
- `test_p51_b_population_wiring.py`：人数纪律接线 2 处显式 legacy（测试对象=人数子句非参考链）；夹具时长合同化。
- `test_ai_6f_real_pipeline.py`：`_save_proj` 统一声明项目级 legacy 参考策略（文件测生命周期非参考契约），7 个视频生命周期测试恢复。
- p39d 7 项失败经 stash 对照确认为 **HEAD 预存**（SIV review fake 'uncertain'），非本轮引入。

### 4.3 全量回归

- 命令：`python -m pytest tests/ -q`（完整日志 `_p55_full_regression.log`）。
- 对比基线：`_54_regression.log`（81 failed + 205 errors，预存环境依赖=DNS 日基线同量级）。
- 结果见 §5.2（回填）。

## 5. 吞吐与额度利用

- 今日（10-17）额度已被 Phase54 收尾消耗：video 402/500s（M5+M6 跨日提交，文件实测 10-17=185.7s，billed≥文件为正常口径差）、images 135、text 811——**今日真机媒体测试窗口仅剩 ~98s**；A/B/C 批全部为无媒体工程验证，不占用。
- 后端进程（10-16 09:34 启动）仍在运行，改动后须重启验证（本轮所有验证均为 pytest 进程内，未动运行中服务）。

---

# 附：批次 B——协作模式注册表与可用入口（已落地）

## B.1 设计与实现

- **`src/agents/collab_modes.py`**（新）：五预设注册表（①六员=W09 模板 / ②Producer 主执行 / ③预铺+主执行 / ④主执行+上交 / ⑤组合）、组合校验（未知模式/缺 story_goal/非法预铺角色/非实验 legacy 策略拒绝）、run 协调账本（`output/agents/collab_runs/`，引用 canonical 不替代）、快照（模式全文+解析参数+Skill 版本+agent 基线+sha256）、执行编排（预铺并行 gather→scoped grant→producer 全载荷单 turn，全部复用 unified_turn/grant/session，零生成代码复制）。
- **REST**（`routers/agents.py` 追加）：`GET /collab-modes`（含可用性与具体缺因）、`POST /collab-runs`、`GET /collab-runs[/{id}]`、`POST /collab-runs/{id}/cancel`（置位 turn_registry 取消事件+状态落档）。
- **目标模板**源自 Phase54 B1/B3 实证载荷（工作方法/Skill 加载/七步目标/上交纪律），参数化 story_goal。
- **参考策略接线**：模式 run 启动写项目级 `reference_policy.json`（source=mode_run）——video_contract 三级解析（显式参数 > 活动 run 策略 > bound_required 基线）使 producer 模式与六员链共享 BOUND 基线等价。

## B.2 边界与后续

- ⑤组合的独立终审步（review_policy=independent_final_review）在批次 D 审核流水化时接入执行器。
- 并发档位（concurrency.media_inflight）由批次 C 的 config 档位承接，run 快照已记录。
- 前端入口在批次 E（任务书 §9）接入 agents 视图。

---

# 附：批次 C——持久恢复与有界并发（已落地）

## C.1 M5 事故路径修复（任务书 §6 核验实证）

- **事故精确定位**（backend_p54_day2.log:2248）：休眠 3.45h → `[watchdog] job ... running 12423s 无 progress 变化，判定卡死 -> failed` → 恢复后重新提交（10:44-11:11 连续 8 个新 task id）——**有 provider task ID 也未先查询原任务**。
- **修复**（`src/core/job_queue.py` `_watchdog`）：已知 provider task ID 的视频 job（handler 提交成功即落 `job.params.provider_task_id`）不再被看门狗作废——改为延长观察窗（刷新 last_progress_at+`watchdog_extensions` 计数留痕，上限 48 次≈8h 超限如实 FAILED）；唤醒后轮询循环自动恢复，真实网络失败仍由 PollTransientError（连续 5 次）诚实上报。无 task ID 的 job 维持原行为（FAILED 后重试带 previous_submit_uncertain 诚实标注）。

## C.2 收货幂等与并发档位

- **收货幂等**（`routers/shots.py`）：同一 provider video_id 只正式收货一次（重复通知/双路径完成不落重复版本），指针指向实际保留版本（不悬空），重复收货发 warn 留痕。
- **并发档位**：`config.yaml` 新增 `concurrency.video_inflight: 1`（默认=串行基线不变）→ `JobQueue(video_worker_count=...)`；`AGNES_VIDEO_INFLIGHT` env 保持最高优先逃生口；提交频率仍由 `rpm.video` 桶独立约束（限流与在途两维度不混同）。逐级试 2/3 按任务书 §11.2 主试验推进（批次 E 真机）。

## C.3 已知诚实边界（不改，记录在案）

- 提交结果不明（generating 无 video_id）：Agnes 无幂等键/按请求查询端点——维持 Phase42 语义（告警+重提交标注 previous_submit_uncertain），无端到端 exactly-once 承诺。
- `ProjectStore.update_shot` 无 CAS（write_lock.py 自证 last-writer-wins）；并发纪律靠 per-project asyncio.Lock + workflow selection 双回执 + repair_scope 硬闸——批次 E 并发主试验若出现写冲突再评估加锁面。

---

# 附：批次 D——会话接力与审核流水化（已落地）

## D.1 Token 预算监控（数据基础补齐）

- **usage 透传**：`agnes_client.chat_completion` 非流式返回体补 `usage`（此前被丢弃——P54-B1 的 467k input 只能从报错日志反推）；流式 finish 事件本就带 usage，producer tool-loop 两侧均落账。
- **`src/agents/context_budget.py`**（新）：`UsageLedger`（per-session append-only JSONL `output/agents/usage/{sid}.jsonl`，真实 usage 锚点+成本口径）+ `estimate_request_tokens`（字符保守估算+输出预留 vs provider 上限——阈值全部 config 可覆盖：`context.limit_tokens/output_reserve_tokens/handoff_ratio/chars_per_token`，不硬编码 467k/524k）。

## D.2 自动接力与主控租约

- **`src/agents/handoff.py`**（新）：交接包（canonical 索引/在途 AgentTask 与媒体 job 含 provider task ID/待审卡/活跃 grant/租约状态/目标摘录/下一操作提示——**索引非真相**，新会话须回读 canonical）+ `relay_producer_session`（交接包落盘→新 producer 会话（`create_fresh_conversation`，干净上下文+新 conversation_id）→租约移交→确定性种子消息）。
- **`src/agents/master_lease.py`**（新）：per-project 主控租约（holder+generation+TTL；同会话续期/他人未过期拒绝/过期接管/transfer 代次+1）；执行点检查挂在被授权工具与 delegate 包装器（producer.py `_audit_wrap`+delegate executor）——**接力后旧会话晚返回的写尝试在工具层被拒**（lease_denied 结构化回执）；租约文件损坏 fail-open（同会话互斥仍由 turn_registry 承担）。
- **producer 接线**：turn 开始 acquire 租约（拒绝=诚实报错）；tool-loop 每步前预算检查——接近阈值停止发起新推理→交接包落盘→turn 以接力说明收尾（在途媒体由 JobQueue 管理，不重发）。
- **collab run 执行器**：producer turn 返回 handoff 时自动 relay+续跑（上限 3 次，超限如实完成并留 `result_handoff_pending`）——Phase54 人工 session-split 成为机制。
- **REST**：`POST /agents/sessions/{sid}/relay`（手动接力）。
- run 记录含 `usage_totals`（成本口径，与上下文长度分开）。

## D.3 审核流水化

- **`src/agents/review_pipeline.py`**（新）：`run_independent_review`——复用既有 `review_shot_core`（单帧 14 维+硬维一票否决已代码化）与 `extract_shot_frame_local` 机械抽帧，固定抽帧位（first/middle/end）批量审已完成视频；结果落 `output/agents/review_pipeline/{batch_id}.json`（抽帧位/判断/证据保存）；独立性诚实标注（fresh 无状态 vision 调用≠完全独立盲审——同一 vision 模型）。不新增业务角色。
- **run 接线**：review_policy=independent_final_review（⑤组合预设）在 producer turn 完成后自动执行独立终审步，硬错误镜清单落 run 记录（修复轮按任务书 §8 保留 repairRequireNew 语义，由用户/下一 run 决策）。

## D.4 新增定向测试（8 项，全过）

`tests/test_p55_handoff_review.py`：预算估算阈值+config 覆盖、UsageLedger 落账/锚点、租约全生命周期（含损坏 fail-open）、交接包字段完备+relay 新会话+租约移交+旧会话写拒、producer 低阈值强制交接（零新推理断言）、审核流水编排（假 vision+硬错误推导+报告落盘）。

---

# 附：批次 E——产品入口（前端部分已落地）

## E.1 前端（§9 产品入口）

- **`src/components/agent/CollabRunPanel.vue`**（新）：内嵌 Production Room——五预设选择（含用途说明+可用性+不可用具体缺因）、story_goal 输入、启动 run（POST /collab-runs）、run 步骤状态实时轮询（GET /collab-runs/{id}，5s）、停止、近期 runs 历史；快照哈希/步骤 detail 折叠在高级开关（普通制作流程只展示有助决策的信息）。
- **`src/api/index.ts`**：collabModes/collabRunStart/collabRuns/collabRunGet/collabRunCancel 五方法。
- vue-tsc 类型检查通过；不新增画布节点类型（沿用 Production Room 组件）。

## E.2 真机验证（进行中）

### E.2.1 接线冒烟 ② producer_lead（2026-10-17 完成，真实全流程）

- **入口**：产品 REST（POST /api/project/{p}/collab-runs，与前端 CollabRunPanel 同一端点）；项目 P55_SMOKE2B（微型 2 镜故事《雨夜递伞》，segment_count=两/duration_hint=8-12 秒——模板段数/时长参数化以适配冒烟额度）。
- **过程**：三次运行接力完成（run_eb818c54da 断网中断图像生成→诚实 blocker；run_67314129eb 租约 TTL 缺陷暴露→修复；run_51744a53ce 续跑完成）。canonical 状态跨 run 接续（run2/run3 复用 run1 的剧本/角色/三视图/Look）。
- **成片**：`projects/P55_SMOKE2B/output/sequences/seq_163beda6/srender_f5abfec2.mp4`——12.2s（计划 12.175s 误差 21ms），1280×720，native 音轨；结构=s01_c02（店内隔门 5s）+s02_c01（递伞收尾）；trace 裁定字段落档、收尾自检通过。**待用户看片**。
- **消耗**：video 402→415（+13s），images 176，text 1052；run3 墙钟 ~68min（含 Look/场景视图审批迭代——微型片资产阶段占比高于预期，记入后续效率观察）。
- **机制验证**：契约闸（BOUND 基线等价——producer 实际执行了 shot.production.bind）、scoped grant、run 快照、步骤账本、跨 run canonical 接续、诚实 blocker/汇报（两次中断均如实上报零伪造）。

### E.2.2 冒烟暴露缺陷与修复（已落地）

- **主控租约 TTL 缺陷（run_67314129eb 实证）**：lease TTL 30min < 长 turn 时长（39min）→ turn 自身租约过期 → 自身工具被拒（denial 消息误报"已转移"）。**修复**：verify 语义改为 holder 匹配即通过（自身过期≠失去主控——过期只 gate 他人 acquire，接管会推 generation 届时自然验不过）+ 消息如实化；回归测试 `test_lease_expired_holder_continuity`。修复后 run_51744a53ce 全程 68min 无自身拒绝（生产验证）。
- **模板参数化**：segment_count/duration_hint 占位符（replace 烘焙 grant_line，不动运行期占位符）。

### E.2.3 用户看片判定（2026-10-17）：**不通过——原因=衔接**

- 用户原话：「我认为不能通过的原因就是因为它的衔接，虽然说看着还行，但是如果将片加长，场景衔接性一定会变差。」（页面 POST 因 review 目录缺失写盘失败——bug 已修复；口头评价为准，已落 FEEDBACK_P55.json）
- **执行侧帧验亲证**（seam_s01_tail/seam_s02_head.jpg，测试5/review/）：
  - **一致性成立项**：门店物理身份跨镜锚定成功（绿色胶带条/空调外机/橱窗结构两镜可对上——LocationView+sciv 锚定有效）；店员黄雨衣/顾客灰风衣与 approved Look 一致。
  - **衔接弱判定成立项**：①两镜 `opening_source=independent`——**没有建立尾帧接续链**（六员链基线配方=接续链优先，intentional cut 须有依据）；两镜各自独立首帧，只靠同 Location 两张不同 sciv 间接锚定。②s01 场景参考 mode=**context_only**——场景图未作为视觉输入进首帧（仅语义上下文），比六员链的 full 弱一档。③空间/状态跳变：s01 尾=顾客门内侧微笑特写→s02 头=街对面大全景两人在雨中递伞（动作已进行中）——无终态/初态对齐。
  - 用户「加长一定变差」假设与本证据方向一致：每镜独立首帧的漂移随镜数复合放大（P54 §10.2 场景漂移根因的同型）。
- **契约缺口定性（本轮最重要产出）**：Phase55 契约强制了 **bind（场景绑定）** 但未强制 **接续链策略**（六员链基线的另一半）——`reference_policy=bound_required` 下 producer 合法地选择了 independent+intentional cut，且 context_only 被计为「已消费」。**修复方向=reference_policy v2（baseline_equiv_chain）**：同场景相邻镜默认要求接续链（inherit_previous 或 multi+relay 哨兵），intentional cut 须显式声明依据并落 manifest；scene_reference_consumed 基线只认 provider_reference 或 via_first_frame(full)，context_only=显式豁免标注。随中长片测试实施与验证。

### E.2.4 后续真机（待额度/后续会话）

- **中长片压力测试升为决定性验收**（用户假设的直接检验）：《雨夜递伞》扩写为 2-3min（12-16 镜），同 ② 模式+reference_policy v2（接续链强制），额度刷新日执行（500s 全新；2-3min 源素材需求约 150-250s）。目标：检验「加长后场景衔接性是否变差」与 v2 修复是否钉住。
- ①六员链冒烟（W09 最小 6 镜）；③④⑤冒烟；并发主试验 1/2/3 档；恢复实战。
- 观察项：微型片资产阶段（Look/场景视图审批迭代）占墙钟比例显著——与 §12 指标「就绪到提交延迟」对齐后量化。

---

# 阶段收口（2026-10-17 当日）

## 完成度对照任务书（§10 批次 × §11 验收）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| A 核验与统一契约 | ✅ 完成 | 依据表 §1、对账 §2、video_contract/input_manifest 落地 |
| B 预设注册与可用入口 | ✅ 完成 | 五预设+REST+CollabRunPanel；REST 级测试全过 |
| C 有界并发与持久恢复（机制） | ✅ 完成 | watchdog 修复/收货幂等/并发档位；定向 6 测试 |
| D 会话接力与审核流水化（机制） | ✅ 完成 | usage/交接包/租约/自动 relay/审核流水；定向 9 测试 |
| E 前端配置 | ✅ 完成（面板） | CollabRunPanel+API；vue-tsc 通过 |
| 接线冒烟 | ◐ 部分 | ② 真机全流程完成（机制 PASS）；①③④⑤ 待额度 |
| 用户看片 | ✅ 已判定 | ② 冒烟片**不通过（原因=衔接）**→ 契约缺口定性（bind 已强制/接续链未强制）→ reference_policy v2 方向 |
| 并发主试验 1/2/3 档 | ⏳ 待额度 | 需全新 500s 日；机制已备（config video_inflight） |
| 恢复实战演练 | ⏳ 待额度 | 机制+定向测试齐；真机中断演练待任务在途时做 |
| 中长片压力测试 | ⏳ 待额度+故事批 | **升为决定性验收**（用户「加长衔接变差」假设的直接检验） |

## 结论

本阶段任务书 §10 的**工程实施批次（A-E）全部执行完毕**并提交；真机验证完成首个预设（②）冒烟闭环（启动→生产→看片→判定→缺口定性→修复方向），其结论已反哺契约设计。按任务书 §11.4 口径：**工程侧可验收项基本满足**（模式可选择且真实执行/输入链一致/持久恢复与接力机制+定向验证/正常入口可用/回归范围清楚/失败与救援完整记录）；**媒体消耗类验证（并发主试验/恢复实战/中长片）按设计跨配额日执行**。

## 下一会话开工清单（按优先序）

1. 实施 reference_policy v2（baseline_equiv_chain）+ 定向测试（接续链强制/intentional cut 声明/context_only 豁免标注）
2. 《雨夜递伞》长片故事扩写（待用户批准草案）→ 中长片压力测试（检验「加长衔接变差」+ v2 有效性）
3. ①六员链冒烟 + ③④⑤冒烟
4. 并发主试验 1/2/3 档 + 恢复实战演练
5. 预存失败专项（SIV review 'uncertain' flake 44 项等）另立清单

## 提交索引

- 子仓 `feat/phase55-collab-parallel`：30f62e2（A-E 批）→ c3bba9f（冒烟+租约修复）→ 5d25f6c（看片判定落档）
- 父仓 master：7de8497 → 381880d → 36226cc
- 运行面：后端 8000（已载新代码）、看片页 8791（FEEDBACK_P55.json 已落用户判定）
- 当日额度终值：video 415/500、images 176/4000、text 1052/7200
