# Phase50 A 批报告｜最小修复、并发探针与耗时归因

执行日期：2026-10-09。执行书：`测试1/PHASE50_A_CONCURRENCY_AND_MINIMAL_REPAIR.md`（总控 `测试1/PHASE50_00_DECISIONS_AND_ROUTE.md`）。
执行侧：Claude（本轮同时承担 Zcode 部分，按用户指令）。
Python：`D:\Delta Force\python.exe`。后端 FastAPI :8000。免费主链（Token Plan）：文本 agnes-3.0-flash / 图像 agnes-image-2.1-flash / 视频 agnes-video-2.5-flash / Director 视觉审查 agnes-2.5-flash（显式）。

## 0. 状态总览

| 项 | 状态 | 摘要 |
| --- | --- | --- |
| A0 真实基线 | ✅ FINAL | §1；六员注册/模型/配额/Skill 哈希实测 |
| A0 四条历史线索核查 | ✅ FINAL | §2；两条已修（hint 抑制），两条有机制+过程兜底 |
| A1 串行来源定位 | ✅ FINAL | §3；七问逐项回答 |
| A2 限流与在途分离 | ✅ FINAL | §4；两个独立 env 开关，默认零变化 |
| A3 渐进探针 C1/C2 | ✅ PASS | §5；在途重叠 370.2s，双双完成，视频提交零拒绝 |
| A4 依赖调度 | ✅ FINAL | §6；B 批调度计划 |
| A5 计时账本 | ✅ FINAL | §7；ledger 落地 + 事件实测 |
| 定向验证 | ✅ FINAL | §8；回归 213+38 项通过（中断处见 §8 注） |

A 通过：本轮能可靠制作（B 正在运行验证）、计时真实、并发边界有证据。C3 未单独跑（按执行书复用 B 独立任务，见 §5.3）。

## 1. A0：真实基线

- 代码：子仓 `animation-storyboard` 分支 `feat/agent-v2-phase-d-golden-run`，本轮起点 `e05f8cf`（Phase49 FINAL 落档），A 批修复后 `164425d`+。父仓指针同步。
- 六员 Agent 运行中后端 `/api/project/P50_B/workflow/catalog` 实证注册：producer / script_writer / character_designer / scene_designer / director / assembly（toolCount 24/16/20/22/32/18）。
- Skill 三包并列 active：`full-story-relay-calibration` v1.4.0（9 文件，整包 sha256:94faa9d9a2c6…）、`keyframes-bridge-repair` v1.4.0（4 文件，98bafad9ffb2…）、候选 `agnes-production-craft` v0.9.0（10 文件，a3a8e40b2a6d…，Phase49 人评双败不转正）。哈希方法：整包目录级 sha256（文件名字节序拼接），与 P49 单文件口径不同，仅本轮相对比较用。
- 配额（执行日 2026-10-09 真机）：video 0/500s、images 0/4000、text 0/7200（全日新鲜）。key：`.env` 单一 AGNES_API_KEY（Token Plan 类型）。
- 模型契约沿用 Phase49 A1 真机探针结论（thinking 出站/视频 8s 真时长/无视频 thinking 字段）；文本模型 2026-10-02 用户指令切 agnes-3.0-flash（切换时已做 /v1/models 在列 + function-calling 探针，config.yaml:9 注释在档）。

## 2. A0：四条历史线索核查（证据→处理）

| 线索 | 核查证据（本轮亲证） | 处理 |
| --- | --- | --- |
| 执行侧曾补交视频 | `projects/P49_D_NEW/output/project.json`：18 条 video_versions 中 **9 条无 origin_run_id（=REST 补交）、9 条有（Agent 委派）**，与 P49 报告"9 条补齐"吻合 | 提交/收货分离与重试事实反馈机制已在（adapter 重试提示+settlement）；P50 起任何执行侧补交记 `manual_intervention` 账本事件，不计 Agent 自主 |
| Skill 曾被触发词误加载 | P49 W05 run_b5e7134993cc（报告 §4.2 在档）：hint 按任务文本 triggers 匹配，与节点绑定无关 | **已修**：`activation_hint` 检测 envelope 显式绑定标记「本节点已绑定正式 Skill」→ 抑制 trigger 提示（`src/agents/skill_system/runtime.py`；确定性判定+hint_suppressed 落档）；测试 3 项（`tests/test_p50_a_minrepairs.py`） |
| 首帧合格但视频分身 | P49 D s04_c01 抽帧实证 2 个小满（用户验收未通过在档）；首帧层 hard gate（duplicate_identity/unexpected_people）不覆盖视频生成阶段 | 本轮以**过程审查**兜底（B4：逐镜视频抽帧审查→硬错误阻止装配选用）；视频层自动 hard gate 保持 backlog（不因本轮扩大范围） |
| 已提交任务被当成新任务 | 代码核验：recover 续轮询不重提交（shots.py 恢复分支）、execution_ref reattach（shot_producer_capability §6）、提交结果不明标注 previous_submit_uncertain（42.md 缺陷④） | 机制已在；本轮探针未出现该情景（无新增实例） |

## 3. A1：串行来源定位（七问逐项）

关键结论：**串行不是服务端限制，是客户端两处保守设计**——①JobQueue 视频 worker 并发=1（`src/core/job_queue.py`，AI-4B 时代"1 RPM 硬约束"下刻意串行，`详细流程文件/提示词.md` 在档：「虽然 Agnes Probe 已证明视频 outstanding 并发可用，本阶段仍然采用严格逐 Shot 串行」——为连续性验证优先，此后未再评估）；②RateLimiter video 桶 RPM=1（`src/core/rate_limiter.py`）。

1. **POST 返回后任务标识立即持久化？** 是。提交成功立刻落库 video_id（`routers/shots.py:1726-1732`），崩溃重启续轮询不重提交。
2. **工具是否只有等到完成才返回？** 否。Agent 路径：AgentTask(shot.video) → execute_shot_production 提交 Job → HTTP 立即返回 RUNNING（`runtime.py _delegate_long_running`），settlement bridge 轮询收货。REST 路径：`POST /shot/{id}/video` 入队即返回 job_id。**但 handler 内部**（提交→轮询→下载）单 Job 占一个 worker 直至终态——worker 数=1 时即整链串行点。
3. **同一导演是否因等待停止规划？** 不会。委派返回 RUNNING 后导演可在同一 turn 继续提交后续镜头（P49 D 批实证 9 条同 turn 委派）；节点完成判定等 outputs，不阻塞其它节点规划。
4. **候选写入是否被过大项目锁串行化？** 无全局长锁。ProjectStore.update_shot 每次调用 fresh load + 同步写（事件循环内天然串行原子）；write_lock.py 仅用于 canonical creative commit 短临界区。并发 handler 对不同 shot 写回安全（每次 reload）。
5. **Producer 与画布执行器重复委派？** 有幂等闸：execution_ref reattach（非终态不重提交）+ find_by_origin（Autopilot 重启用）。
6. **受理/排队/生成/下载是否混为一个状态？** 部分改善过：staged emit（recover/submit/poll/download）+ 看门狗 + 提交结果不明可见化（42.md ④）。仍无 provider 侧 started/completed 时间（本轮不编造，见 §5.2）。
7. **轮询查询标识？** 正确：`poll_video(video_id, model_name=提交时同一模型)`（`agnes_client.py`，2.5 契约）。轮询走 **text 桶**（不占视频桶）——本轮 C1/C2 实测 text 桶 429（重试吸收），视频提交零 429（§5）。

## 4. A2：限流与在途分离（实现）

两个独立开关（默认均零变化；env 未设时行为与修复前逐字节一致）：

| 维度 | 开关 | 默认 | 本轮设置 | 依据 |
| --- | --- | --- | --- | --- |
| 提交频率（RPM） | `AGNES_VIDEO_RPM` | 1（config.yaml rpm.video:1） | **2** | Token Plan FAQ 官方参考值 video=5 RPM（2026-06-28 页）；官方值是参考值未真机证实可持续，探针取保守 2；env 优先于 config 合并（修复项，否则 config 覆盖 env） |
| 在途任务数 | `AGNES_VIDEO_INFLIGHT` | 1 | **2** | 真机探针在档（提示词.md）：Agnes 允许 ≥3 outstanding / 约 2 路并行；本轮 C2 实证复证 |

- 同类 key 池：`.env` 单 key，无多 key 绕池。
- 「提交结果不明」处理：沿用 previous_submit_uncertain 标注（不盲重发）。
- 不新增非法模式组合：T17 互斥拦截在站（create_video 出站前显式拒绝矛盾参数）。
- 429/降频纪律：client 指数退避在站；本轮视频提交零 429（§5）。

## 5. A3：渐进探针 C1/C2（真机，正式入口）

### 5.1 设计与执行

- 夹具项目 `P50_Probe`：两个独立镜头（sc01=黎明渔港/has 人物入画、sc02=空旷海面海鸟；i2v、各 10s、互不读取生成结果）。首帧 t2i 各 1 候选（photorealistic 实拍质感，人工目检通过）。
- C1：正式 REST 入口提交 sc01_c01 视频；18s 后（C1 在途）C2 提交 sc02_c01。
- 后端进程 env：AGNES_VIDEO_INFLIGHT=2、AGNES_VIDEO_RPM=2。

### 5.2 结果（`_p50_probe_c12_result.json` + `cache/p50_timing_ledger.jsonl`）

| 指标 | C1 (sc01_c01) | C2 (sc02_c01) |
| --- | --- | --- |
| video_id | task_EJbH…KbsK | task_FZLF…HnGk |
| ready→submitted | 2.3s | 19.0s（含 18s 间隔 + RPM 节流） |
| submitted→completed（远端等待，合并口径） | 434.0s | 370.2s |
| completed→downloaded | 1.9s | 2.1s |
| seconds_returned / ffprobe 实测 | 10.0 / 10.12s | 10.0 / 10.12s |
| 终态 | done | done |

- **在途重叠 370.2s**（两任务 [submitted, completed] 区间交）。两任务均受理、均完成、回执各自正确写入、互不撤销——§8「两个不同 shot 同时在途」验证通过。
- **视频提交零 429**；text 桶 429 共 10 次（轮询共享 text 池+重试吸收）——A1 Q7 的"轮询占相同 RPM 池"疑问**实证为是**。
- 配额：本探针消耗 20s（video 20/500）。

### 5.3 结论范围（如实）

- C2 证明：客户端可保持 2 个在途、服务端受理并完成两者。**服务端是否真并行推理未知**（provider 无 started/completed 字段）——按执行书要求写"服务端实际并行未知"，在途重叠仅为客户端观测。
- 单任务 vs 共享速度未做严格对照（C2 提交时 C1 已在途；且服务负载未知）——不声称吞吐倍数。参考在档探针（AI-4B 期）：10s 单任务约 124s；本轮共享下 370-434s，差异方向与"约 2 路并行分摊"一致，但**不构成严格结论**。
- C3（三条在途）：按执行书"优先复用 B 中已批准独立任务"，随 B 批视频阶段自然发生（B 的独立镜头在途数达 3 时记录区间），不另烧配额专测。

## 6. A4：依赖决定能否并行（B 批调度计划）

| 关系 | 调度 |
| --- | --- |
| 角色/船/怪物参考资产 | 文字定义固定后独立制作（资源 Agent 自主） |
| 共享批准资产、互不依赖视频的镜头 | 并行候选生成（在途上限=本轮实测配置 2；B 中出现 3 独立在途时记录为 C3 观测） |
| 依赖上一镜实际保留端点的接力镜 | 等上游完成审查+端点确认后启动 |
| 正常切镜 | 冻结剧情状态下独立构图 |
| 上游未过审/素材未批准 | 下游暂不启动 |
| 旧任务结果不明 | 先恢复核对，不投同意图重复任务 |
| 合格旧片段 | 完成判定承认合法复用 |

导演自主决定逐镜方法（合法模式菜单）；执行侧不预写逐镜答案。

## 7. A5：计时账本（落地与实测）

- 新观测件 `src/core/timing_ledger.py`：JSONL 追加（cache/p50_timing_ledger.jsonl），事件 `video_ready / video_submitted / video_completed / video_failed / video_downloaded`，UTC ISO + epoch + 同机单调钟；**fail-open**（写失败绝不影响生成主链，测试覆盖）。
- 挂点：`_handler_video`（routers/shots.py，4 处，观测件不改变行为）。
- 缺失字段如实处理：provider_started_at/provider_completed_at 服务端不提供 → 远端等待合并报告（submitted→completed），不拆"排队/推理"。
- 墙钟/累计/人工等待口径：按执行书 §7（区间并集算墙钟、不把重叠时长相加、人工等待单列）。B 批报告按此出数。

## 8. 定向验证与回归

- 新增回归：`tests/test_p50_a_minrepairs.py` 10 项（hint 抑制 3 / RPM env 3 / inflight env 2 / ledger 2）全绿。
- 相关既有套件：skill_system/discovery/turn + job_queue_errors 30 项全绿；AI-6F 真实管线 28 项全绿（含 video handler 全链）。
- 全量回归：`tests/` 213 passed 后在 `test_ai_6f_real_pipeline` 一处**因本轮代码缺陷**（video_ready 计时点引用未定义变量）停下——**已修复并复证 28/28**；修复后全量重跑见 §8.1。
- 行为不变性证据：env 未设时 video RPM=1（/api/system/limits rpm=0.85×1 实测）+ inflight=1（测试断言）。

### 8.1 全量回归（基线对照，三轮）

| 轮 | 分支 | 结果 |
| --- | --- | --- |
| 基线 e05f8cf（Phase49 FINAL） | 修复前 | 76 failed / 2399 passed / 205 errors（两次复跑一致） |
| Phase50 A（含计时挂点初版） | 修复后 | 110 failed / 2375 passed / 205 errors——**+34 failed 为本轮缺陷**：ledger 挂点 `job.job_id` 假定 Job 形状，测试用 SimpleNamespace fake job 触发 AttributeError 级联（native_av/c1_director/scene 系） |
| Phase50 A（getattr 容错修复后） | 最终 | **76 failed / 2409 passed / 205 errors——与基线完全一致（+10 为本轮新增测试）；零回归** |

预存失败/错误清单与基线相同（s3b2 SCIV review 'uncertain' 级联 + test_api 两项 404 等，均非本轮引入）。日志：`_p50_baseline_regression2.log`（基线失败名单）、`_p50_cur_regression3.log`（最终）。

## 9. 遗留与未知

1. 视频生成阶段自动 hard gate（分身/人数）未实现——本轮以过程审查兜底，列为后续 backlog。
2. 服务端推理并行性无字段证实——只有客户端在途证据。
3. RPM=2 + 2 在途的无 429 结论样本量 n=1（本时段）；B 批继续观测。
4. C3 未单独执行（复用 B 独立任务，随 B 报告出数）。
5. 轮询密度与 text 桶竞争：2 任务并发轮询（各 12 RPM）超 text 桶 20 RPM（0.85 系数后 17）——本轮重试吸收，B 批若恶化考虑轮询间隔自适应（backlog，不本轮改）。

## 10. 修复提交清单

| commit | 内容 |
| --- | --- |
| （A批主提交） | AGNES_VIDEO_INFLIGHT 视频 worker 数（默认 1）/ AGNES_VIDEO_RPM 视频桶限流 env（默认 1）/ p50_timing_ledger 计时账本（fail-open 观测件）/ skill hint 显式绑定轮次抑制（W05 缺陷）+ 回归 10 项 |
| fix: video_ready 后移 | 计时点引用未定义变量致 6F 管线测试挂——修复并复证 |
| fix: env 优先级 | AGNES_VIDEO_RPM 提到 config rpm 合并之后（config video:1 此前覆盖 env） |
| test: C1/C2 探针 | 脚本+结果 JSON 在档 |

依据：S1/S2 及版本边界见总控文件。
