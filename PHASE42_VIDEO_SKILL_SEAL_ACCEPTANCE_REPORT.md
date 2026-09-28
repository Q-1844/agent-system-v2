# PHASE 42 — 视频 Skill 底层封板验收 报告

日期：2026-09-28 起。执行依据：`详细流程文件/修复阶段/42.md`（用户）。
执行：Claude Code（核验+修复+测试）+ 系统 Agent（后续盲测）。
线索文档：`PHASE41_VIDEO25_MIGRATION_AND_FULL_REDO_REPORT.md`（其结论按待核验记录处理）。

状态标记：〔已核验〕=仓库/回执可复现；〔仅报告陈述〕=只有 Phase 41 文字、无独立证据；
〔待视觉验证〕=需看帧/人评。

---

## §0 基线整备（先于一切核验）

工作区发现 09-27「修复阶段3」会话遗留的**未提交已验证改动**（该会话报告
`PHASE3_SKILL_REVISION_DELIVERY.md` 声明留待审查；Phase 41 在其之上提交并全量回归）：

- 编译层守卫：ffsync 槽位语义标记（slot_semantics=multi_reference_only）+
  `first_frame_slot_not_hard_boundary` truth gate + 跨段接力可见性
  （prev_tail_cross_segment_relay 诊断+进指纹）+ 末帧版本化文件名 +
  continuity_tail 持久 provenance。
- Skill v1.1.0：起步/再锚定决策入口、触发词扩充、bridge 决策入口唯一。
- 测试：test_phase3_relay_guards.py ×3 + 5 文件锚点更新。

处置：本轮 77 项定向测试复测全绿后落提交 **c3bf7da**；档案补交（P40A 审计/
39E/expB/交接报告+执行文件 4-34/37/40-42+P40A 驱动器另存）**41b0ffe**。
39C 驱动器被某会话原地改写为 P40A 变体——已恢复原状（_40_e2e_driver.py 独立存档）。

## §1 Phase 41 报告核验结果

### 1.1 可由仓库/回执复现（全部命中）〔已核验〕

| 报告声明 | 核验证据 |
|---|---|
| config 唯一模型 2.5-flash、video_25 键删除 | config/config.yaml:11；DEFAULT_VIDEO_MODEL 常量 agnes_client.py:30 |
| 非 2.5 显式拒绝 / negative_prompt 非空拒绝 / multi→reference images 1-5 | agnes_client.py:607-614, 568-585；agnes_adapter.py:110-131 |
| keyframes 双帧 2.5 显式拒绝（未验证勿发） | agnes_client.py:563-566；test_agnes.py:166 |
| director_visual 枚举收敛 | director_visual.py:263 `allowed={"agnes-video-2.5-flash"}` |
| 路由落库 model_name | routers/shots.py:1546-1548 + 提交即落库 :1667-1674 |
| 2.5 reference 双图探针回执 | _video25_probe/ref.*（submit/accepted_body/final/mp4/帧图）+ models.json |
| 修复阶段产物 | _41_redo/（result_2.json、events、recover_videos.py、task_full_redo.txt、after_*.json）；_41_repair/（result_1.json、events_1.jsonl、task_bridge_s03c03.txt） |
| trace run 落档（attempt 归档 a1-a4） | projects/雨灯归航/output/experiments/calibration_runs/cal_20260928_202352_1adf/links/{s03_c03,a1-a4}.json |
| AGNES_API_FACTS §3.0.1 | 详细流程文件/AGNES_API_FACTS.md:159 |
| capability-facts 六状态表 | skill 包 references/capability-facts.md |
| 测试锚定（v2.0 拒绝等 4 项） | tests/test_agnes.py:166,180,195,227 |
| 13 镜 2.5 重生成 | 13/13 final_video model=agnes-video-2.5-flash（project.json 逐镜回执） |

### 1.2 与报告叙述不符（本轮新发现，回执为准）

1. **「解除 relay+独立重生」未发生**（§6 叙述 vs trace+回执）：修复版 vv_7339c1d5
   的 input_visuals = relay_start_boundary ← s03_c02 尾帧 vv_a9ab4df5——relay
   全程保留。agent 自己的同步 trace（selected_action）写的是「文本强化+
   negative_prompt 排除楼梯→被 2.5 客户端拒绝→清空重试」；「清 source_shot_id」
   对实际请求无影响（vref=prev_tail 残留时按展平序回退上一镜，接力源不变）。
   **空间修复的真实机制=强化 description 压过语义参考**（2.5 reference 不锁首帧）。
   Phase 41 §6 方法表「解除 relay 独立重生」列**从未被真实执行**，不能作为已验证修法。
2. **「两组同源 Look」推断被证伪**（§7）：偏差组根帧（s02_c02）的编译回执
   （ff_cb6f94a5）显示 Look 被 `human_reference_drop` 丢弃、场景图被
   `scene_reference_context_only` 丢弃，仅发道具图——白三角斑来自 description
   文本（s02_c02 描述原文含「左脸颊的白色三角毛纹」），不来自 Look。详见 §3。
3. **§5 执行细节小偏差**：报告称中断轮 12:12 起，批次回执时间 12:02（±时钟口径，
   不影响结论）；「重生成首帧 29.5s→重生成视频 209.9s」对应 ff_c1905be6（20:41）
   与 vv_7339c1d5（20:44），期间还有一次未在报告出现的视频提交 vv_400bc423（20:39，
   同 turn 内 negative_prompt 被拒前的尝试）。

### 1.3 仅报告陈述、无独立证据（如实标注）

- 「84 工具调用全 ok、3 次 trace_upsert 失败」——events 流无 args，调用级成败无法
  逐条复现（result_2.json 收据在档）。
- 「12:12 中断轮 agent 显式传 human_reference_mode=drop 还是依赖 auto 默认」——
  事件流无参数、turn 注册表不落盘，**不可判定**（auto 解析结果与显式传参同值）。
  修复设计按「两侧都须安全」处理。
- 39 AV-sync 失败的环境条件判定（Phase 41 仅转述 39F 基线）→ 本轮 §5.6 重判。

### 1.4 待视觉验证

- 修复版 s03_c03 的 1 处 suspected 跳变（late_middle→ending）待用户目检。
- 13 镜逐镜人评（本轮仅用户定性反馈在档）。

## §2 工具与方法盘点矩阵（42.md §一）

### 2.1 工具面（系统 Agent 实际可调）

| 工具 | 位置 | Agent | 状态 |
|---|---|---|---|
| shot.generate_videos | director_visual.py:638(schema)/:567(exec) | director | 已验证（13/13 真机） |
| shot.generate_images | director_visual.py:578 | director | 已验证（含 reference_policy 门槛 :283-320） |
| shot.production.bind/inspect/refresh | director.py:827/807/881 | director | 已验证（Phase C2 E2E） |
| shot.select_frame | director.py:653/:1596 | director | 已验证 |
| shot.bridge_create | director_bridge.py:40 | director | **2.5 下结构性不可达**（见 §2.4-①） |
| calibration.seam_measure | director_calibration.py:32 | director | 已验证（MAE 128 灰度，continuity_service.py:190） |
| calibration.trace_read/trace_upsert | director_calibration.py:89/:152 | director | 已验证（修复会话 5 次落档） |
| **calibration run 创建** | **无工具**（唯一入口 REST experiments.py:50） | — | **不可用**（缺陷①，§5.1 修复） |
| video.extract_frame / visual.sample_frames | director_frames.py:32/:103 | director | 已验证 |
| visual.review_image/review_shot | director_vision_review.py:49 / shot_review.py:151 | director | 已验证 |
| skill.list/load/read_resource | skill_system/tools.py:33/45/65 | 5 Agent 全挂 | 已验证 |
| script_writer / character_designer / scene_designer / producer 工具面 | script_writer.py:732-741 / character_designer.py:764-793 / scene_designer.py:1754-1761 / producer.py:256-335 | 各自 | Phase B/C2 验证在档 |

### 2.2 请求链路（2.5-flash）

director 工具 → director_visual.py:567→:206→:374-385（JobQueue submit）
→ job_queue.py:117-125（cache/jobs.json 原子持久化；video 串行 worker）
→ routers/shots.py:1463 _handler_video → video_adapter.py:287 build_bound_video_input
→ agnes_adapter.py:88 compile_agnes_video_call → agnes_client.py:587 create_video
→ 轮询 poll_video:635-683（URL fallback :674-676）→ VideoVersion 落库 :1712-1739。

请求体（2.5）：model/mode（text|keyframe|reference）/prompt/seconds(str 4-12)/
image|images/seed；num_frames 等仅签名兼容不进 body；negative_prompt 拒绝。

### 2.3 生命周期机制

- **task id 持久化**：提交成功立刻 store.update_shot（video_id/task_id/status/
  attempts/model，routers/shots.py:1667-1674）+ jobs.json 原子写。残留风险窗口=
  create_video 响应已达 provider 但响应/落库前进程被杀（不可消除，无 provider 端
  幂等键；Phase 41 c02 损失 8s 即此窗口）。
- **恢复**：startup 挂 recover_pending_videos（api_server.py:174；services/
  lifecycle.py:12-57）：generating+有 video_id → poll 60s → 完成入库/否则重排队续
  轮询（handler :1570-1580 有 video_id 不重提交）；PollTransientError 保留 video_id
  （:1685-1689）。**缺口**：generating+无 video_id（提交结果不明）被 recover 跳过
  （lifecycle.py:32）→ 永久滞留 generating，无可见性（§5.4 修复）。
- **选片/下游**：set-final（:1833-1875，选后重提末帧）；changed_refs 从真实
  receipts 派生（producer.py:498-551）；binding 失效走 validity read model +
  shot.production.refresh；video freshness 读模型（:1766-1784）。
- **尾帧/接缝**：_extract_video_end_frame（:490-533，版本化文件名）；seam_measure=
  128 灰度 MAE（continuity_service.py:38-50,190-246）。
- **trace**：<project>/output/experiments/calibration_runs/<run_id>/{run.json,
  links/*.json}；upsert 对不存在 run 抛 not_found（calibration_trace.py:126）。

### 2.4 盘点发现的结构性缺口

① **keyframes 双帧在 2.5 契约下不可达**：adapter/handler 仍构造双槽
（agnes_adapter.py:132-139、routers/shots.py:1643-1644），client 显式拒绝
（:563-566）→ 所有 link_mode=keyframes 镜头（含 shot.bridge_create 建的桥，
bridge_service.py:232 固定 keyframes）提交必然 AgnesError。桥修复能力与 2.5 契约
断层；官方契约明确 keyframe 模式支持 first_frame/last_frame（至少其一，FACTS:170）
——双帧形态是否被服务端接受**待探针**（§6 探针 P2）。
② 进程内 driver（_41_redo_run.py/_40_canary_run.py）不挂 recover（§5.2 修复）。
③ run 创建无工具面（§5.1 修复）。
④ t2v 仅 client 契约层存在，无生产路径（如实边界，不修）。
⑤ recover 对无 model 字段的旧数据用 config 默认轮询（仅 41 之前历史数据风险）。

### 2.5 方法盘点（42.md §一清单 → 逐项状态）

| 方法 | 生产入口 | 实证 | 状态 |
|---|---|---|---|
| 独立起镜（Look 直生 multi） | s01_c01/s02_c01 | 2.5 真机 13/13 内 | **已验证**（贴定义组） |
| 正常切镜（intentional cut） | s03_c03 修复（强化描述压过 relay） | 空间修复实证；**relay 实际未解除** | 已验证（文本强化版）|
| 真实尾帧作硬首帧（inherit_previous i2v） | 路径 a | Phase 3 行为验收+编译层；2.5 真机样本缺 | 仅接通（待真机） |
| 裸尾帧 relay（multi prev_tail 单参考） | s02_c03→s03_c03 | 13 镜内 8 镜 | 已验证（含漂移副作用，见 §3） |
| Look+尾帧双参考同发 | 无 Binding 接线 | client/编译层探针 ref.* 一次命中 | 仅接通（§5.5 接线） |
| 融合首帧起步（路径 c） | 合成→目检→i2v | expB 09-27 v2.0 样本 | 仅接通（2.5 待真机） |
| 硬首帧跨空间 | 场景切换探针 _probe_scene_transition.py | task_oRWiMOll 8s 实证 | 已验证（探针级） |
| 首尾关键帧桥（keyframes） | bridge_service.py:232 | 2.5 无真机样本；client 拒绝 | **不可用**（待探针 P2 裁决） |
| 错误端点独立重生（真解除 relay） | — | **从未真实执行** | 仅有规则（§6 探针） |
| 镜内锚定重试 | Phase 40 镜内 major→锚定重试 | 40.md PASS WITH LIMITATION | 已验证（预算受限） |
| 尾态偏离插曲加镜 | 37A《雨林奇遇》MINOR 吸收 | 37A 9/9 | 已验证（2.5 待复证） |
| 改正式选片下游级联 | set-final+changed_refs | Phase 40 负面约束 PASS | 已验证（机制面） |

## §3 画风漂移审计（42.md §三）——根因定论

### 3.1 逐镜回执矩阵（13 镜全量，project.json 回执为准）

| 镜 | 视频参考（实际发送） | 首帧批次模式 | 分类 |
|---|---|---|---|
| s01_c01 | character_look（仅） | —（multi 直生） | 锚定 ✓ |
| s01_c02/c03 | relay←前镜 | — | 继承 s01_c01 |
| s01_c04 | ff_77ffc303_1（i2v） | context_only+**drop**，仅发构图图 | **偏差链根 B** |
| s01_c05/c06 | relay←s01_c04 | — | 继承偏差 |
| s02_c01 | character_look（仅） | — | 锚定 ✓（贴定义组） |
| s02_c02 | ff_cb6f94a5_1（i2v） | context_only+**drop**，仅发道具图 | **偏差链根 A** |
| s02_c03 | relay←s02_c02 | — | 继承偏差 |
| s03_c01 | relay←s02_c03（**跨段**） | — | 继承偏差 |
| s03_c02 | relay←s03_c01 | — | 继承偏差 |
| s03_c03 | relay←s03_c02（修复=文本强化，relay 未解除） | 修复期首帧批 full+keep（未被视频消费） | 继承偏差（空间已修） |
| s03_c04 | scene_instance_view（仅，EWS） | — | 无 Look（远景身份不可见，用户未标记） |

### 3.2 根因链（回执+帧目检双证据）

1. **机制**：D2 auto 规则（image_adapter.py:517-573）——`medium close-up` 命中
   _AUTO_CONTEXT_ONLY_SIZE_KEYS → scene=context_only → **human=drop（Look 不进
   provider）**。s01_c04/s02_c02 均为中近景 → 双双丢 Look → 模型按 description
   文本重造角色（画风/兜帽/五官全部自由发挥）。
2. **冻结**：i2v 视频继承重造形象 → 尾帧定型。
3. **传播**：relay 链以「上镜尾帧=唯一视觉参考」逐镜传播偏差风格
   （s02_c02→s02_c03→s03_c01→s03_c02→s03_c03）。
4. **视觉定性**〔已核验，帧图 _tmp/p42_audit/〕：Look=水彩/兜帽向下/黑眼/左颊
   白三角；s02_c02 首帧=厚涂赛璐璐/兜帽向上/白斑异侧；s03_c03 修复版=偏差组风格
   且出现**蓝眼**（relay 链内累积新漂移）+空间正确（圆形灯室✓）。
5. **Phase 41 推断修正**：「共享参考链或场景图风格传染」二选一 → 实为
   **「特写丢 Look 重造 + relay 传播」**；「同源 Look」证伪（§1.2-2）。

### 3.3 用户验收反馈对齐（验收依据保留）

- 「2.5 整体漂移比 v2.0 少」→ 保留为模型层结论。
- 「s03_c03 原版不可接受」→ 对应修复前版本（楼梯间挂灯），空间修复后待用户目检
  （画风问题未修，见 §3.4）。
- 「固定好人物、素材和场景（含画风与状态），按剧情可随意发挥」→ 修复设计目标。

### 3.4 修复方向（按证据定层，§5-§6 实施）

- **传播层**：Binding 双参考接线（Look+尾帧同发）→ relay 镜每镜再锚定身份/画风
  （§5.5+§6 探针 P1 对照）。
- **根生成层**：特写丢 Look 的补偿——既有 crop 档（面部/头肩裁切参考，代码在档
  待 A/B）或融合首帧路径 c；auto→crop 翻转须先小范围对照（§6 探针 P3）。
- **决策层**：Skill 决策入口补「特写镜身份锚定检查」（drop 不得静默，§7）。
- 12:02 中断轮 agent 是否显式传 drop 不可判定（§1.3）→ 两侧同时加固。

## §4 底层修复（42.md §二）——实施记录

### 4.1 run 创建工具面（缺陷①）✅

`calibration.run_create` 进 Director 正式工具面（src/agents/director_calibration.py）：
与 REST `POST /calibration/runs` 同源服务层（calibration_trace.create_run），
story_id 必填；归入 DIRECTOR_WRITE_OPERATIONS（同 trace_upsert 口径：非 canonical
状态 mutation，占写槽，回执 run_id 不进 changed_refs）。skill 两包
required_capabilities 同步；director.md §7 补「不得以无 run 可写为由跳过落档」。
测试：test_p39_agent_tools.py（注册/写集合/执行→落盘→trace_upsert 闭环/缺参拒绝）+
test_c1_director_production.py 工具面锚定 31→32。

### 4.2 driver 恢复钩子（缺陷②）✅

`lifecycle.recover_pending_videos(queue=, client=, projects_root=, handlers=)`
参数化（默认 deps 全局，生产行为不变）；进程内驱动器 `_41_redo_run.py`/
`_40_canary_run.py` 挂钩（隔离队列传自身 queue）。测试：隔离队列 re-enqueue
（poll 异常→queued+job 进驱动器队列）/ 不重 create_video。

### 4.3 上游瞬时 5xx 分阶段处理（缺陷③）✅

三阶段口径：
- **提交前失败**：client 内层重试 5 次（RETRYABLE 408/429/5xx 全覆盖+网络异常）
  → 耗尽 AgnesError → handler 标 failed → retry 重提交（见 4.4 不明标注）。
- **已提交后轮询失败**：PollTransientError 保留 video_id+generating → recover/
  续轮询不重提交（既有机制，本轮测试锚定）。
- **合成回复失败**（Phase 41 缺口）：client 流式路径加固——stream_attempts 3→5
  （对齐非流式耐心）、退避对齐（6/12/24/48 cap 45s）、**零事件中途网络异常
  （ReadTimeout/TransportError）纳入重试**（此前直接抛出终结 turn）；已产出
  事件后不重试（重发=重复 delta/tool_call），维持 turn 层处理（driver 双形态
  重试 e2ab989 + 生产 REST 用户重发语义）。
测试：test_agnes.py（429 耗尽 5 次/零事件断流重试成功/已推送事件断流不重发）。

### 4.4 提交结果不明与重复提交防护（缺陷④）✅

- **可见化**：recover 对 generating 且无 video_id（提交结果不明，Phase 41 c02
  损失 8s 的窗口）显式告警、不自动处置（lifecycle.py）。
- **重提交诚实标注**：handler fresh-submit 前发现结果不明状态 → emit warn +
  VideoVersion 新字段 `previous_submit_uncertain=True` 落库——重复生成可能性
  随版本持久，验收方/消费方可见（42.md：不得直接重发并声称不会重复生成）。
- **残留窗口说明**：create_video 响应已达 provider 但响应/落库前进程被杀——
  无 provider 幂等键，理论不可归零；现行为=滞留 generating 可见 + 下次提交
  带标注（不静默、不冒充全新提交）。
测试：recover 跳过结果不明任务不轮询不入队 / handler 标注字段落库。

### 4.5 Binding 双参考接线（缺陷⑤，Look+尾帧）✅

**canonical 哨兵设计**（非请求级 flag——freshness 读模型无请求上下文，canonical
才能 build/重算两侧对称）：`shot.video_reference_asset_id = "prev_tail_look"`
（新哨兵，agent 经 shot.update 显式设置；纯 relay `prev_tail` 行为逐字节不变）。

- video_adapter：`resolve_multi_relay_source` 返回 identity 标志（进 relay 指纹，
  两侧同键）；`_resolve_relay_identity_reference` 确定性解析本镜 hero Look
  （priority=primary character_look，与 _select_video_reference 同规则）——
  解析不到公网 media = `relay_identity_reference_unresolvable` 结构化拒绝
  （opt-in 语义下静默跳过=谎报已锚定，绝不允许）。
- BoundVideoRequest.identity_reference → to_generation_request 发两个
  ReferenceInput（尾帧 video_reference 在前=Picture 1，Look character_look 在
  后=Picture 2）→ agnes_adapter multi 收集两种来源（1-5 校验不变）→ prompt
  自动附加 `<Picture 1/2>` 指称段（仅双参考路径，探针实证 provider 语义采用
  占位符；不触碰单参考/硬边界路径与指纹）。
- receipt：used_ids/semantic_roles/input_visuals 全落（reference_asset_id/
  subject_character 随版本持久）。
- **测试**：双参考 build→投影→编译→指纹对称 4 项 + 指纹差异（prev_tail vs
  prev_tail_look）+ 无 Look fail closed + 纯函数哨兵语义；p39f 直生单 Look 回归
  （input_visuals URL 判别修复）。
- 状态：**仅接通+机制测试**——视觉效果未验证（42.md：接口成功≠可设默认），
  待 §6 P1 真机对照后才可讨论默认化；capability-facts 已记「未验证」。

### 4.6 39 项 AV-sync 回归重判

（全量回归运行中，回填）

## §5 方法边界定向真机测试（42.md §四）

（待 §4 完成后执行；P1 双参考对照 / P2 keyframes 双帧探针 / P3 特写 crop 对照 /
P4 真解除 relay 对照 / P5 系统 Agent 真镜硬首帧跨空间）

## §6 Skill 沉淀、冻结与盲测（42.md §五）

（待 §5 完成后执行）

## §7 缺陷与风险清单（滚动）

1. keyframes 桥 2.5 断层（§2.4-①）——探针 P2 裁决前，桥不得用于生产。
2. relay 链画风传播（§3）——双参考接线+验证前，多镜 relay 链应视为画风漂移高险。
3. 提交结果不明窗口（§2.3）——无 provider 幂等键，理论上不可归零，只能可见化。
4. 进程内 driver 队列隔离导致 recover 失效（§5.2 修复中）。

## §8 封板结论

（待 §4-§6 完成后填写）
