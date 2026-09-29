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
| Look+尾帧双参考同发 | prev_tail_look 哨兵（§4.5 接线） | P1 探针一次命中+画风向 Look 回正（_video25_probe/p1.*） | 已验证（探针级；默认化待用户裁决） |
| 融合首帧起步（路径 c） | 合成→目检→i2v | expB 09-27 v2.0 样本 | 仅接通（2.5 待真机） |
| 硬首帧跨空间 | 场景切换探针 _probe_scene_transition.py | task_oRWiMOll 8s 实证 | 已验证（探针级） |
| 首尾关键帧桥（keyframes） | bridge_service.py:232 | **P2 探针一次命中：双帧受理+双端钉住（_video25_probe/kf.*）；client 已放行** | 已验证（探针级；真镜样本待盲测） |
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

### 4.6 39 项 AV-sync 回归重判 ✅ 环境性失败修复

逐项判定（42.md 要求）：39 项失败**全部同根因**——fixture 依赖
`projects/AGNES_GoldenPath_H/output/videos/s01_c01.mp4`（真实成片，该项目
09-24 清理删除）→ `shutil.copy` FileNotFoundError（亲测复现）。
- 与本阶段的关系：这些测试覆盖 **A2 外挂 lip-sync 路径**（已搁置留档的
  fallback primitive），不在本轮封板的 2.5 视频生产路径上；但其失败被
  Phase 41 以「零净增」挂账掩盖，未逐项判定环境条件（42.md 点名的诚实缺口）。
- **处置**：常驻合成夹具 `tests/fixtures/a2_authoritative_video.mp4`
  （ffmpeg testsrc 121 帧=5042ms 探测时长一致+h264+aac 音轨——原片探测特征
  逐项对齐）替换已删项目依赖；REAL_MP4 常量改指仓库内夹具。
- **结果**：三文件 55/55 全绿；最终全量回归 **2508 passed / 0 failed**
  （对比 Phase 41 基线 2458 passed / 40 failed——首次完全收口，含此前
  挂账的全部 39 项；1 项 cancel_race 间歇 flake 本轮亦绿）。
- 1 项波动测试（test_ai_7ch_cancel_race）：预存间歇单跑即绿（39F 在档），
  本轮全量亦通过；与本阶段无关，维持原状。

## §5 方法边界定向真机测试（42.md §四）

### P2：keyframes 双帧探针（2026-09-29）✅ 桥解锁

- **裁决问题**：2.5-flash 是否接受 keyframe 模式 `first_frame`+`last_frame` 双帧
  （官方契约字段，client 此前「未验证勿发」拒绝 → 桥结构性不可达，§2.4-①）。
- **结果**：候选 0 一次命中——双帧 200 受理，69s 出片 5s 720P（task_o55qqlL…，
  证据 `_video25_probe/kf.*`）。帧目检：**首帧与输入端点逐像素级对齐（硬边界）、
  尾帧构图钉住（开门望塔）、中间平滑过渡动作**。
- **处置**：client 放行 keyframes 双帧（映射官方字段，数量守卫=恰好 2 张公网
  URL）；测试锚点由「拒绝」反转为「接受+边界」；capability-facts 三行更新。
- **纪律保留**：桥仍只在【两端点合格且值得保留】时使用（skill 决策入口唯一，
  端点画错不插桥）；探针端点本身取自偏差组成片——桥钉死端点，端点错则错误
  被钉住（visual 判定=双端钉住机制成立，与端点质量无关）。

### P1：Look+尾帧双参考 relay 探针（2026-09-29）✅ 传播层修复验证

- **设置**：s02_c03（偏差链第一消费镜）哨兵 `prev_tail_look`（仅内存，canonical
  不落盘——探针不冒充正式决策）；请求经生产编译器
  （build_bound_video_input→compile）与正式路径同源。
- **实际请求**（_video25_probe/p1.compiled.json）：images=[s02_c02 尾帧, hero Look
  ref_92ad3512a47f] + `<Picture 1/2>` 指称段 + seed=42，5s 720P 出片
  （task_VxgTFsmE…，199s）。
- **视觉判定**（帧图 _video25_probe/p1_frame_*.jpg，对照现役偏差版）：
  - 首帧：接力语义保留（同场景同道具衔接）✓；画风首段仍被尾帧拖拽（偏差
    风格已冻结在端点帧里）。
  - 中/尾帧：**画风向 Look 水彩质感明显回正**（背景柔化、质感渐变），空间
    叙事正确（修理间→开门→夜色灯塔），身份特征（橙外套/蓝围巾/白斑）保持。
  - 遗留：白斑侧别与 Look 不一致（跟随尾帧侧别）——Look 锚定与尾帧锚定
    竞争，尾帧侧别胜出。
- **结论**：双参考=有效的传播层修复（沿链逐镜回正方向验证成功，接口/请求/
  素材指代/跨帧身份全链闭环）；**根帧修复仍需重生成 s02_c02 首帧**（根层，
  探针 P3/修复阶段）。是否默认化=§7 Skill 沉淀时按用户看片裁决（探针样本
  `_video25_probe/p1.mp4` 可直接观看）。

### P3：特写身份锚定三臂对照（2026-09-29）✅ crop 证据成立

- **设置**：s02_c02（medium close-up，偏差链根）同一 binding 三臂对照——
  A=drop（现役根帧，零费用）/ B=crop（头肩派生+scene context_only）/
  C=keep（全幅 Look+scene context_only）；各 1 次图片生成（_video25_probe/p3_*）。
- **判定**：
  - A（drop）：构图 MCU ✓；身份/画风**完全重造**（赛璐璐/兜帽向上/白斑错侧）
    ——偏差根复证。
  - B（crop）：构图 MCU ✓（D2 关切满足）；画风部分回正（质感变柔）；白斑
    仍在但侧别错、兜帽向上。
  - C（keep）：身份保真最高（白斑侧别✓/兜帽向下✓/水彩✓）；但构图被拉宽
    （全身立绘锚定构图——D2 原始批评复证）。
- **结论**：auto→crop 翻转有 A/B 证据支持（相对 drop 纯收益：构图不变、身份
  变好）；keep 在特写确实锚死构图。翻转落地=§6 Skill 沉淀时按用户裁决执行。

### P4：真解除 relay+独立重生（2026-09-29）✅ 三修法矩阵闭合

- **设置**：s03_c03 内存中清 vref+independent 起步，独立生成首帧（完整锚定
  scene full+Look keep+强化 description）→ i2v 硬首帧。canonical 未动。
- **意外收获**：Phase 3 truth gate `first_frame_slot_not_hard_boundary` 在
  真实数据上首次实战命中（探针试图消费 relay 同步槽被拦）——守卫有效性
  获得真机级证据。
- **判定**：画风/身份三探针最优（水彩/白斑正确侧/兜帽向下）；**但空间错**
  （室外平台+螺旋梯+玻璃穹顶，未进灯室）——无尾帧参考钉住空间时自由度过大；
  且 s03 通用场景视图无灯室内部先验（**素材层缺口：需经 Scene Agent 正式入口
  新增「灯室内部」SIV**——用户已授权新增场景走正式入口）。
- **端点三修法实证矩阵**（42.md §四「比较适用条件」交付）：

| 修法 | 空间 | 身份/画风 | 实证 |
|---|---|---|---|
| 文本强化+保留 relay | ✓ | ✗（继承偏差） | Phase 41 修复会话（41.md） |
| **双参考（relay+Look）** | ✓ | 中气回正 | P1（本轮）——**综合最优** |
| 真解除+独立重生 | ✗（自由度过大） | ✓（最佳） | P4（本轮） |
| 硬首帧+空间转移 | ✓ | 随首帧 | Phase 41 探针（41.md §7） |

### 待测项

- P5：系统 Agent 真镜选择+验证（含硬首帧跨空间由 agent 自主选择）→ 归入
  §6 盲测。

## §6 用户验收反馈与扩展测试（EXP2，2026-09-29）

### 6.1 用户预览页反馈（Round 2，原文要点）

1. **P1 双参考对照不成立**：「本视频的首帧的人物都不对，仔细看人物的眼睛就会
   发现风格不对了。综合看数据不够，应该继续做对比。」——执行侧复核**成立**：
   探针首帧继承偏差尾帧，人物（眼型/风格）从第一帧即偏差，「中后段回正」
   幅度不足以裁决。→ 打法修正：从根重建链（先修根帧再接力），让首帧即正确。
2. **桥的定位**：「桥接主要在修复阶段被选，普通首次生成一般没有必要。」——
   方法论定版：桥=修复阶段工具（写进 Skill）。
3. **P3 三臂各有缺点，数据不够**：「B 图其实可以，但人物还是有点宽，应该衬托
   出人物小巧可爱才对」「为啥构图被拉宽了」——验收判据新增：**人物小巧可爱**；
   生图额度充足（4000/天，用户确认放手用），做大样本图像矩阵。
4. **场景缺口处置**：授权新增灯室内部；方法论定版——「当生成视频时发现需要
   新场景，可以停下来去生成新场景再继续，注意场景切换的镜头跟随」。
5. **§五暂缓**：「数据太少了……至少补充 2-3 组数据再让我来选，可以换一镜多做
   测试并做预览页让我审查。」

### 6.2 EXP2-A：特写锚定图像矩阵（20 张新图，已完成）

两镜（s02_c02 + 换一镜 s01_c04，均为偏差根）× 五臂（crop / keep / keep+构图
条款 / crop+构图条款 / keep+场景图同发）× 两种子（42/7）。
证据：`_video25_probe/exp2/`（图 + exp2_receipts.json）。

**执行侧初筛信号**（供用户审阅参考，非结论）：
- **keep_fullscene 臂（场景图同发）在两镜上均把画风收敛回水彩、白斑回正确
  侧、兜帽回向下**——场景实例视图本身是风格锚（此前 context_only 把它丢了）。
  代价：构图偏宽（全身+环境尺度，D2 原始批评在「场景图」上同样成立）。
- 构图条款臂（keep_comp）确实让人物变小/留白变多（小巧可爱方向 ✓）。
- crop 臂构图保持但白斑侧别仍错（与 drop 同侧）——侧别错误非 crop 引入。
- 「为啥构图被拉宽」的直接答案：参考集同时含全身立绘+场景图时，模型锚到
  「全身+环境」尺度（D2 发现的复现）；仅 Look（context_only）时锚到人物
  大特写。

### 6.3 EXP2-B：从根重建视频链（进行中）

三链 × 2 视频：修好的根帧 → s02_c02 视频（i2v）→ 提新尾帧+托管 → s02_c03
（reference 双参考=新尾帧+Look）。与现役偏差链端到端对照，回答「首帧人物即
正确」后的链路观感。

### 6.4 用户验收判据更新（滚动）

- 人物形象：**小巧可爱**（比例圆润、不撑满画面）
- 画风：水彩（与批准 Look/贴定义组一致）
- 身份标志：白三角斑**侧别正确**（Look 同侧）、兜帽向下、蓝围巾
- 桥=修复阶段专用；缺场景=停下补场景再继续（注意切换镜头跟随）

### 6.5 EXP2 评审结果（用户 Edge 本地存储恢复，逐字在档）

**链裁决（qa3）**：选择=链 2（fullscene 种子42）可接受。逐链评语（原文）：
「1 的人物形象是最符合原人物形象的，视频生成的稍微有一点问题，但是它是最符合
原来的人物长相。2 的视频动作很合理，唯一的缺陷是人物的眼睛比原人物大了一点，
其它的身材等都没问题，我认为某些情况也可以用。3 人物宽一点，但是看起来非常
合理，并且胖胖的很可爱。所以我认为这三种都是可以用的，如果要求可爱胖胖画风，
那么可以用 3，2 的话少用，其次用 1。当然我不知道我们用不用针对 2 继续生成
试试人物还会不会再变。」

**总体（qa4）**：「可以将我选出来的一些进一步深入测试比较好，或者你可以先
总结，目前不着急，还是要慢慢针对性测试，越往后同一种我挑中生成方式就要有
更多的数据来测试。」

**逐图评语（部分恢复）**：s01_c04_crop_comp_s42「可以说完全合理」；
s02_c02_keep_fullscene_s7「很合理，但是这种…」（截断）；另一图「比旁边这个
差一点，因为脸稍微宽」。其余逐图评语因浏览器存储分块未能完整恢复——后续
预览页增加「下载反馈文件」按钮（一键落盘，不再依赖剪贴板）。

**初步结论（待用户确认）**：
1. **三链全部可用**——从根重建（修根帧→i2v→双参考接力）的方法得到验收。
2. 风格偏好排序：可爱胖胖画风=链 3 根帧（keep_comp）> 链 2（fullscene s42）
   少用 > 链 1（fullscene s7）——注意这是链级排序，A/B 区逐图裁决以用户
   评语为准。
3. 执行方式：先总结、慢慢针对性测试、每种被选中的配方要有更多数据。

### 6.6 EXP2 完整评审（服务器落盘版，逐图 22 条全量在档）

**选择汇总**：s02_c02 选中 6（crop×2 / crop_comp×2 / keep_fullscene×2，
keep 族与 drop 未选）；s01_c04 选中 7（crop×2 / keep×2 / keep_comp×2 /
crop_comp_s42，keep_fullscene 未选）。

**逐图关键评语**：
- **crop 族（两镜一致好评）**：s02_c02「人物大小合理，变宽了一点点基本看不
  出来，很好」「很好，白斑位置不好」；crop_comp_s7「很好」；s01_c04「很好」
  ×2、crop_comp_s42「可以说完全合理」→ **crop（头肩裁剪 Look）= 跨镜最稳
  配方，31.md §19 悬案的用户实证落地**。
- keep 族（全幅 Look）评语一致：「身体胖一点，脸不宽，很可爱很合理，虽然可能
  与人物原本身材不太一致」——可爱但失真；s02_c02 侧更严：「人物变胖，形象
  不是原本的那种可爱」。
- keep_fullscene：s01_c04「很明显它的灵活性不够，不能用」；s02_c02 选中但
  「模样最符合人物…怕生成视频后灵活性有欠缺，再切换方法会导致一批视频中有
  两个不同形象，建议生成视频测试」→ **场景图同发=风格最正但构图锚死风险**
  （D2 原始批评被用户亲证）+ **新方法论判据：同批/同链必须统一配方，混用
  =两种形象**。
- drop 现状：「人物胖和宽」（偏差根实锤收尾）。

**用户发现的新问题（逐图评语）**：「每个人物提的灯都不一样，那么灯应该没有
用参考图吧」→ **执行侧查证属实**：s01_c04 的编译参考集=[Look+构图图]
（keep_fullscene 多一张场景图），**无灯道具参考**（对比 s02_c02 有绑
prop_fb1bd0b18fb1 铜灯）——s01_c04 的 Binding 缺 prop 绑定，灯全靠文本。
修复=补道具绑定（42.md §三 道具样式锚定层的正式落地动作）。

**遗留问题（用户提出）**：链 2（fullscene s42）是否继续生成看人物还会不会变。

### 6.7 下轮深测计划（EXP3，按用户裁定执行）

1. **crop 主配方多种子深测**：两镜 crop 臂各 +5 种子 → 用户挑最终根帧。
2. **crop 根帧视频链**（新链 4）：crop 根 → i2v → 双参考接力 s02_c03，
   与链 1/2（fullscene 根）对照——顺带验证「keep_fullscene 视频灵活性」担忧。
3. **s01_c04 补铜灯 prop 绑定**后重测 crop 臂——直答「灯不一致」问题。
4. 链 2 追加样本（回答「人物还会不会再变」）。

### 6.8 EXP3 评审结果（服务器落盘全量在档）——三项定版

1. **道具锚定层验证成功**：「稳定了，灯基本一致」（形状一致，仅裂纹差异）——
   prop 绑定进 Binding 后道具样式稳定。**Skill 判据：道具入画必须绑 prop
   （带状态注记）**。
2. **链 4（crop 根帧端到端）最优**：「两条都可用，4 的效果最好……人物的跑步
   状态最好，画面感最好」。**用户动作判据（新）：跑步等快速动作镜头要少让
   人物做多余动作——多余动作会抢速度感，容易不自然。**
3. **crop 主配方确认**：s02_c02 选中 s1/s3/s5/s11（**s2 出现两个人物**——
   种子方差数据点，记入配方风险）；s01_c04 选中 s1/s3/s5/s7/s42+prop_s7。
4. 用户总评：「基本可以确定了，可以再做一轮针对测试，多生成一些东西。」

### 6.9 EXP4（进行中）：定版配方批量试产

- 链 A：s01_c04（crop_prop_s7 根，种子 42/7）→ s01_c05 → s01_c06——定版
  配方铺满 s01 偏差链（6 条视频）。
- 链 B：s02_c02 定版候选根 s1 / s5 → s02_c03（4 条视频）——与链 4（s42 根）
  并排供用户终选根帧。

### 6.10 EXP4 评审结果（服务器落盘全量在档）

1. **s02_c03 回正「达标，可正式替换」**：「画风和身份没有什么问题，模样基本
   都一致」——定版配方产出正式可替换成片（里程碑）。
2. **新判据（动作-时长-场景匹配）**：链 A 种子 42 的 c06「凭空出现一个甲板」
   （时长太长→场景凭空延伸让人物继续跑）；种子 7 移速降低（时长内动作不足
   →不自然）。用户原话：「动作和时长、场景要匹配，否则时长太长就会凭空出现
   新地址让人物跑，时长太短可能会出现人物跑速过快导致的不自然。」种子 42 更
   优（「剪辑软件稍微修一下，效果就变得极好了」）。
3. **s02 根帧终选**：选 s42（链 4），但记录张力——s1 根「人物效果比较好没有
   发现瑕疵」但灯有一些问题；s5 根「嘴角稍微有点问题，但灯的样貌处理极好，
   开门换手动作也展示出来了」→ **人物无瑕疵与道具完美尚未在同一镜同时达成**。
4. 用户总评：「需要针对上述情况继续针对测试。我们需要知道究竟能否做出人物、
   道具、场景都完全没问题的视频。」

### 6.11 EXP5（进行中）：完美视频验证轮

- s02_c03 多候选：链 4 配方不变，种子 7/11/23（+已有 42 共 4 候选）——
  足量候选中挑「人物+道具+场景」零瑕疵样本，直答可能性问题。
- s01_c06 凭空甲板修复验证：动作不变、时长 5s→4s——验证动作-时长-场景
  匹配判据。

### 6.14 EXP6 评审结果（用户文字反馈）——配方验证收官

1. **动作延伸修甲板：有效但不完全**——「没有解决甲板问题，但新生成的甲板变短
   了，并且人物感觉更丰富了」。**定版：作为 Skill 修复阶段方法之一**（场景
   跑不完→增加人物后续动作填满时长）；与减秒数**可并用**（「两者有时也要一起
   用」）。
2. **灯问题结案**：「灯的问题其实基本不存在，只是之前应该是特殊情况，暂时不用
   继续测试」。
3. 用户指令：详细整理记录交 GPT 判断下一阶段。

---

## §7 定版配方与方法判据清单（Skill 沉淀素材，全部有用户验收实证）

### 7.1 定版配方（v-final，EXP2-EXP6 验收）

**根帧层**：特写/中近景镜 → crop 档（头肩裁剪 Look 派生参考，image_adapter
既有 identity_crop）+ scene context_only；**道具入画必须绑 prop**（带状态
注记，如「裂纹玻璃罩/泥水/未点亮」）。
**视频层**：i2v（根帧硬首帧）→ 完成后提真实尾帧+托管 → 下一镜
**reference 双参考**（新尾帧=Picture 1 衔接 + hero Look=Picture 2 身份/画风
再锚定，prompt 自动附 <Picture N> 指称）逐镜推进。
**候选策略**：同配方足量候选挑「人物+道具+场景」零瑕疵（EXP5 实证 4 取 3）。
**风格抢救**（非常规）：场景图同发（keep_fullscene）=画风/白斑/兜帽全回正的
最强锚，但构图锚死风险——仅用于偏差抢救，非常规默认。

### 7.2 方法判据清单（写进 Skill 的用户定版规则）

| # | 判据 | 来源 |
|---|---|---|
| 1 | 特写镜身份锚定用 crop，不用 drop（构图身份双优） | EXP2/3 用户逐图评审 |
| 2 | relay 链每镜双参考再锚定（阻断画风沿链传播） | P1+链 4 用户验收 |
| 3 | 道具入画必须绑 prop+状态注记（否则样式漂移） | 用户发现灯不一致→EXP3 验证 |
| 4 | 同批/同链统一配方——混用=一批视频两个形象 | 用户 EXP2 提出 |
| 5 | 动作-时长-场景匹配：时长太长→场景凭空延伸；太短→移速不自然 | 用户 EXP4 提出 |
| 6 | 场景跑不完的修复：①增加人物后续动作填满时长（甲板变短+人物更丰富）②减秒数——两者可并用 | 用户 EXP5/6 裁定 |
| 7 | 跑步等快速动作镜头少加多余动作（否则速度感不自然） | 用户 EXP3 提出 |
| 8 | 桥（keyframes 双帧）=修复阶段工具，首次生成不插桥；端点画错不插桥 | 用户 EXP2 裁定 |
| 9 | 缺场景→停下走 Scene Agent 正式入口补场景再继续（注意切换镜头跟随） | 用户 EXP2 裁定+授权灯室内部 SIV |
| 10 | 完美视频产出=定版配方+足量候选挑零瑕疵（不追求单种子必中） | EXP5 实证 4 取 3 |
| 11 | 错误端点三修法矩阵：文本强化+relay=空间✓画风✗；双参考=空间✓画风回正（综合最优）；真解除+独立重生=画风✓空间✗（需场景先验）；硬首帧转移=探针✓ | §5 探针矩阵 |
| 12 | 校准 run 由 agent 自主创建（calibration.run_create），不得以无 run 为由跳过 trace | 42.md §二修复 |

### 7.3 系统侧已落地的底层修复（§4，回归 2508/0 全绿）

①calibration.run_create 工具面（trace 闭环自主）②recover 参数化+进程内
驱动器挂钩 ③流式重试 3→5+零事件网络异常重试 ④提交结果不明→
previous_submit_uncertain 诚实落库 ⑤Binding 双参考接线（prev_tail_look 哨兵，
canonical 事实，freshness 两侧对称，无 Look fail closed）。另有：Phase 3
守卫真机实战命中（first_frame_slot_not_hard_boundary 拦截 relay 槽被 i2v
消费）、39 项 AV-sync 环境性失败清零（合成夹具）、keyframes 双帧解锁
（桥路径恢复）、A2 权威视频夹具常驻化。

## §8 封板结论（按 42.md 五条件逐项）

| 条件 | 状态 |
|---|---|
| 1. 工具入口/长任务恢复/trace/候选版本/下游失效可复现证据；中断不失踪不重复 | **满足**（§4 五缺陷修复+回归 2508/0+探针/真机回执） |
| 2. 每条拟用于生产的方法有合法请求+真实 Agent 选择记录+回执+视觉验收 | **部分**：方法全部有真机回执+用户视觉验收；「Agent 自主选择记录」待盲测（本轮方法验证为执行侧探针，canonical 未动） |
| 3. 画风/角色状态/道具/场景锚定跨镜实测；关键偏差有归因与可观看改善 | **满足**（§3 根因+§6 定版配方+偏差镜回正达标可正式替换+道具锚定验证） |
| 4. 冻结版本全新会话盲测（含非本项目迁移任务） | **未做**（用户裁定：配方验证齐+正式修复后再进入） |
| 5. 回归无未解释关键失败 | **满足**（2508/0；1 项预存间歇 flake 单跑即绿） |

**结论：不可封板。** 剩余阻塞项（全部已收敛、无未知风险）：
① 正式修复轮——按定版配方重做《雨灯归航》偏差镜（s02_c03 已达标待正式替换；
  s02_c02/s01_c04/c05/c06 候选样本在档待正式化）；② 灯室内部 SIV（Scene
  Agent 正式入口，已授权）；③ Skill v1.2 沉淀（§7.2 十二条判据写进 skill 包）；
④ 冻结+全新会话盲测（42.md §五：含角色再锚定/换场/接续/正常切镜/失败恢复+
  非本项目迁移任务）。

## §9 交 GPT 的下一阶段决策点（自足简报）

**背景**：《雨灯归航》13 镜已 2.5-flash 全量重测（Phase 41）；画风偏差根因
已定论并修复验证（D2 auto 规则特写丢 Look→文本重造→relay 传播；修复=crop
根帧+道具绑定+双参考接力，§7.1 定版配方）；底层五缺陷修复+回归全绿；用户
经 EXP2-EXP6 五轮逐图/逐链评审完成配方验收。评审机制=定版评审页
（REVIEW.html+manifest.json 每轮换数据+FEEDBACK.json 自动落盘）。

**已定版**：§7.1 配方 + §7.2 十二条判据（全部有用户实证）。
**在档资产**：偏差链候选样本（exp2-6 目录：图 40+张、视频 20+条，全部带
回执与用户评语）；灯室内部 SIV 授权未执行；系统侧修复已提交
（c3bf7da→20d825e 共 11 commits）。

**请 GPT 判断/规划**：
1. 正式修复轮的执行方式——系统 Agent 全新会话按定版配方重做偏差镜
   （Agent 自主选择方法=补齐封板条件 2 的选择记录）vs 执行侧按配方直接
   正式化（快但 Agent 选择记录缺失）。若走 Agent：任务书如何写（给配方
   还是给判据？给判据更接近真实能力测量）。
2. Skill v1.2 沉淀的粒度——§7.2 十二条判据全量进 skill 包 vs 精选进
   SKILL.md 决策入口+其余进 capability-facts/references。哪几条是
   Skill 层（决策规则）vs 系统层（代码守卫）？例如判据 1（crop 翻转）
   是否直接改 image_adapter 默认值（系统层）还是留在 Skill（决策层）。
3. 冻结与盲测的时机与规模——正式修复+Skill 沉淀后立即盲测，还是先做
   灯室内部 SIV+《雨灯归航》全片正式化再盲测？盲测任务规模（42.md 建议
   含角色再锚定/换场/接续/正常切镜/失败恢复+非本项目迁移任务）。
4. 灯室内部 SIV 的生成时机（正式修复前 vs 盲测中由 Agent 走正式入口——
   后者同时验证判据 9）。
5. 13 镜重测的 trace 补档是否纳入正式修复轮（run_create 工具已就位，
   可由 Agent 补档——须注明事后补档与原始证据）。
