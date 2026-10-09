# Phase51 A 批报告｜生产系统效率与失败恢复

执行日期：2026-10-10。执行书：`测试2/phase51/PHASE51_A_RUNTIME_EFFICIENCY.md`（总控 `PHASE51_00_EXECUTION_GUIDE.md`）。
执行侧：Claude Code。代码基线：子仓 `animation-storyboard` 分支 `feat/agent-v2-phase-d-golden-run` 起点 `cbf775c`（Phase50 B FINAL 落档，764 个未跟踪/脏文件为历史过程档，未触碰）。
Python：`D:\Delta Force\python.exe`。证据主源：`projects/P50_B` 全部 6 次 run 记录+事件日志、`cache/p50_timing_ledger.jsonl`（1020 事件）、`PHASE50_A/B_REPORT.md`（按总控 §3 要求作为线索入口，全部关键数字用原始 run/账本数据重算核验，未直接采信报告标签）。

## 0. 状态总览

| 项 | 状态 | 摘要 |
| --- | --- | --- |
| A1 最小基线 | ✅ | §1；P50_B 六 run 重建时间轴+损耗精算（§2） |
| A2① 承认有效成果（require_new 重试判死） | ✅ 修复+验证 | §3.1；P50 实证最大损耗（~2h）根因修复，11 项新测试含原样重放 |
| A2② 持久化提交与恢复 | ✅ 核验已有机制 | §3.2；无新缺陷实例，不重复建设 |
| A2③ 就绪未推进（视频阶段空转 2765s） | ✅ 机制修复+安全网 | §3.3；提交纪律接线+服务端在途等待（零 LLM） |
| A2④ 轮询与文本限流竞争 | ✅ 修复+验证 | §3.4；poll_video 自适应退避（5s→20s，量降 4-6 倍） |
| A2⑤ 重复 I/O / 上下文 | ◐ 核验 | §3.5；缓存机制已在，未发现计时证明的可观损耗，未动 |
| A2⑥ reject 语义与局部返工 | ✅ 修复+验证 | §3.6；驳回停止语义保持，新增显式修复路径（continue_run rejected） |
| A3 定向验证 | ✅ | §4；新增 11 项（test_p51_a_efficiency）+73 项工作流套件+全量回归 |
| A4 目标 | ◐ 机制就绪，数字由 C 判定 | §5；60%/30% 目标的口径与可测性 |

**A 放行结论**：主要损耗均有逐段证据；最关键缺陷（完成判定语义）有修复与定向验证；正式入口能跑通并恢复（测试实证 continue/reject/repair/在途等待）；无新引入关键正确性失败（全量回归与基线一致，§4.2）。全片 30% 目标由 C 判定。

## 1. 基线与证据入口

- **注册表**：六员 producer/script_writer/character_designer/scene_designer/director/assembly（`src/agents/registry.py` SPEC_AGENT_IDS + SPECIALISTS；P50 A 报告 `/workflow/catalog` toolCount 24/16/20/22/32/18 实测在档，本轮未改动注册）。
- **Skill**：`full-story-relay-calibration` v1.4.0 + `keyframes-bridge-repair` v1.4.0（builtin，本轮零改动——Skill 修订归 B 批）。
- **配置指纹**：`config/config.yaml`（text=agnes-3.0-flash / image=agnes-image-2.1-flash / video=agnes-video-2.5-flash；rpm.text=20, video=1；AGNES_VIDEO_INFLIGHT / AGNES_VIDEO_RPM 两个 env 开关默认=1，P50 探针后生产段=2）。Thinking 全链保持开启（本轮所有改动不触及 thinking 参数）。
- **AGENTS.md**：当前环境不存在（仓库与父目录均无）。按总控 §3「历史文件不在执行环境时不假装读过」处理，以 `CLAUDE.md` + 注册表 + 代码实读替代。
- **计时口径**：runs_index 的时间戳实为本地时间（UTC+8）误标 Z 后缀（与 ledger 真 UTC 互证：run6 createdAt 19:11 本地 = 11:11 UTC = ledger epoch 1791544297）。本报告全部时间已换算为本地时间。

## 2. P50_B 真实时间轴重建与损耗精算（A1 交付）

### 2.1 六 run 时间轴（attempt 级，本地时间）

| run | 节点链 | 墙钟 | 结局 |
| --- | --- | --- | --- |
| W09 (run1) 15:36-16:20 | producer(3m)→sw(1.7m)→cd att1(6m, 建 4 角色)→**cd att2(24m, 建 4 Look+2 服装+8 参考图)** | 44m | **cd 判死**：att2 建了 Look 但 character spec 要求本 attempt 新写 → 节点 failed → run 死 |
| W09r2 (run2) 16:20-16:39 | producer(2.1m)→sw(1.1m)→cd(7.5m 复用核验) | 19m | 资源审定**驳回**（大副西装+渔船 SIV）→ 9 下游节点停止 → run 终态 rejected |
| W09r3 (run3) 16:39-16:42 | producer(1.25m)→**sw(1.1m)** | 2.3m | **sw 判死**：故事已在项目内（run1 产物），domain 命中要求新写入 → failed |
| W09r4 (run4) 16:43-18:05 | producer→sw→cd→sc(33m 真实场景生产)→**dir_board att1(5m, 真实建 20 镜+5 场景后上游 400 死亡)**→**att2(3.3m, 20 镜已在→0 新写入→判死)** | 82m | run 死 |
| W09r5 (run5) 18:12-18:54 | producer(0.7m)→sw(0.3m)→cd(2.5m)→sc(5.2m 复用核验)→dir_board(26.4m 真实首帧) | 42m | 停靠分镜审定 waiting_human，被弃用 |
| W09r6 (run6) 19:11-22:49 | producer(0.75m)→sw(0.9m)→cd(5m)→sc(1.4m)→board(6.3m)→**dir_video(2.72h)**→asm(4m) | 218m | **COMPLETED**（成片 56.56s） |

每次重启都是**新建 definition**（W09→r2→…→r6 六份，逐轮把判死节点手工改 `allow_verified_reuse`，见 §3.1 证据）。

### 2.2 按损耗分钟排序的根因表

| # | 根因 | 证据（原始数据） | 损耗（分钟） | 归类 |
| --- | --- | --- | --- | --- |
| 1 | **require_new 按"本 attempt 新写入"判定，重试 attempt 延续既有成果被判死** | run1 cd att2：changed_refs 实证 4 Look+2 服装+8 参考图为真实回执写入，仍判"新写入 0 个"；run4 board att2 同型（20 镜实证在 changed_refs）→ 连锁 4 次重启+6 份 definition | ~123（纯损耗：r1 att2 23.9 + r2 重复派发 10.7 + r3 2.3 + r4 att2 3.3 + r5 复用轮 8.7 + r6 前置复用轮 14.3 + 失败 run 操作/间隙 ~60） | 可消除 |
| 2 | **视频阶段委派间隙空闲** | 账本：墙钟 8166s，在途覆盖并集仅 5401s，空档 2765s（12 段 80-857s）；n_dir_video 回合 9785s / 134 工具调用——导演"提交→等结算→再委派"回合节拍 | ~46 | 可消除 |
| 3 | **轮询与文本调用竞争 text 桶** | poll_video 固定 5s×2 在途=24 req/min vs text 桶有效预算 17（20×0.85）；P50 探针实测 text 桶 429×10（重试吸收，视频提交零 429） | 损耗为 429 重试延迟+文本排队（未单独计时），量级小于 #1/#2 | 可消除 |
| 4 | 审核代行间隙（r4 17.6m、r6 20.7m、r5 6.4m） | attempt 表起止间隙 | ~45 | T_human_blocked（协作开销，B 批机械审核自动化杠杆，本轮不动） |
| 5 | run4 n_sc 33m 模型边界探索 / run5 board 26m 真实首帧 | attempt 摘要+产物复用实证（r6 全部复用） | 0（真实生产工作） | 不可消除 |

说明：#1 的 ~123min 采用"纯重复派发/verify 轮+操作开销"口径；B 报告的"~2h 重启损耗"口径=失败 run 全墙钟（145min）扣其中真实工作（~60min）≈85min，两口径在此互相校准。上表 #1 各分量均为 run 记录 attempt 级实测，非推算。

## 3. 修复（证据→改动→验证）

### 3.1 A2① 跨 attempt 真实写入累计（require_new 重试判死）

**证据**（§2.1/#1 完整）：完成判定链 `adapter.verify_completion` → `outputs.verify_node_outputs` 原按 `changed_refs`（本 attempt 收货）命中；重试 attempt 的合理行为是延续+补缺（run1 cd att2 的 24 分钟真实图像工作、run4 board att1 的 20 镜结构），一律判死→run 终态→被迫新建 definition 全链重跑。P50 报告 §3.1 单例怀疑，本轮以两组 attempt 级 changed_refs 实证为**系统性缺陷**。

**改动**（最小实现，诚实性底线不变）：
- `src/workflow/executor.py`：新增 `_prior_changed_refs()`——汇总本 run 本节点此前 attempts 的 changed_refs（全部来自服务端留档的真实工具回执摘要 `_result_digest`）；`_run_agent_node` 收货校验与 `_reconcile_attempt` 收养校验均传入。
- `src/workflow/adapter.py` `verify_completion(prior_changed_refs=)`：域命中底线与目标级判定按「本 attempt ∪ 此前 attempts」累计。
- `src/workflow/outputs.py` `verify_node_outputs(prior_changed=)`：命中实体标 `writeSource: current|prior`（逐条可溯），交接包带此标注；verdict reason 注明 prior 计入数。
- **诚实性不放宽**：只承认本节点任务回执覆盖的写入（同 run 同节点）；跨 run 复用仍走 `allow_verified_reuse` 显式语义；项目里更早的/别的节点的旧素材不因此合格（A-T02 实证）。

**验证**：A-T01（run1 cd 场景原样重放：att1 建 s01→判缺项→continue→att2 只补 s02→**通过**，交接包 `writeSource` 标注 s01=prior/s02=current）；A-T02（非本节点/不存在实体不冒充）；A-T03（零写入零 prior 维持原判死——回归保护）。

### 3.2 A2② 持久化提交与恢复（核验，无改动）

机制已在且本轮未发现新缺陷实例：提交成功立即落 video_id（`routers/shots.py:1726-1732`）、崩溃重启续轮询不重提交（`recover_pending_videos`）、execution_ref reattach、`previous_submit_uncertain` 标注、workflow_ref 找回收养（`_reconcile_attempt`）。P50 B 全程 20 条视频零 REST 补交。不重复建设。

### 3.3 A2③ 就绪未推进→及时推进（视频阶段空转）

**证据**：§2.2/#2。根因不是底座缺并发（P50 A 已开 2 在途），而是**导演回合节拍**：导演在回合内等结算再委派下一批（134 工具调用/2.72h），2765s 空档吃回并发收益（P50 净提速仅 2%）。

**改动**（两件套，缺一不可）：
1. **提交纪律接线**（`adapter.build_envelope`）：对 completion 含 `video_version` 的 director 节点追加约束——独立镜头立即连续提交（本地队列按在途上限自动排队/RPM 自动节流），仅接力依赖镜头等上游端点；服务端会在回合结束后等待在途结算并重判，不会因提前结束误判。
2. **服务端在途等待安全网**（`executor._await_inflight_videos` + `_run_agent_node` 重判）：完成判定缺项但本 attempt 已提交镜头的视频 job 仍在本地队列（queued/running）时，服务端 `asyncio.sleep` 轮询等待终态（**零 LLM 回合**；与 JobQueue worker 同事件循环）后重判。上限 2h+停顿守卫 25min（P50 实测单条 150-753s，不因短时无进展杀正常 job）+取消事件即时响应；在途 job 全部终态后重判——视频失败仍诚实 failed（A-T08）。

**验证**：A-T07（在途→等待→结算→重判通过，节点成功）；A-T08（在途→失败→诚实 failed）；A-T11（纪律约束只出现在视频节点，分镜节点不误加）。真实导演行为改变（是否提前结束回合）由 C 批生产实测——本轮不宣称空闲已消除，只证明机制就绪且不引入误判。

### 3.4 A2④ 轮询与文本限流竞争

**证据**：`poll_video` 固定 5s（官方建议值被当成全程轮询间隔）；视频真实完成分布 150-753s/条（账本实测 min/max），2 在途=24 req/min 超 text 桶预算 17；P50 探针 10× 429（重试吸收，未丢任务，但文本调用排队）。

**改动**（`src/api/agnes_client.py poll_video`）：前 60s 保持 5s（捕捉快速完成，覆盖实测最短 124-150s），之后退避到 20s——完成检测延迟 ≤20s（相对分钟级生成可忽略），轮询量降 4-6 倍，给正常文本调用让出额度。429 仍由 `_request` 退避重试吸收；单 job 单查询者不变（JobQueue handler 自轮询，结算桥只读本地态）。

**验证**：A-T09（步进序列断言：12×5s→20s 稳态，无回跳）。

### 3.5 A2⑤ 重复 I/O / 上下文（核验，未动）

图床 rehost 按文件哈希缓存（imgbb/github_pages，P50 B 无重复上传证据）；canonical 为按需 fresh load 非"每回合全量塞入"；无计时证据表明重复转码/抽帧占比可观。按 A2⑤"只在计时证明占比可观时处理"——本轮不动，无发现即如实记录。

### 3.6 A2⑥ reject 语义与局部返工

**证据**：run2 驳回→run 终态 rejected→9 节点停止→唯一出路=新建 definition 重跑（W09r3-r6 四轮，每轮重付 producer/sw/cd 派发轮）。`decide_review` reject 分支：审核节点 STOPPED+下游 PENDING→STOPPED；`continue_run` 拒绝 rejected（"已停止请另起新 run"）。

**改动**（驳回停止语义保持，补显式修复路径）：
- `executor.continue_run`：rejected run 走新增 `_repair_rejected_run()`——目标=被驳回审核的 targetNodeId 节点（node_id 显式指定时必须一致，不得跳过未过审上游）；该节点重开 attempt（repairNote=驳回记录注入 envelope 修复提示：定点修复、保留合格部分、不做无差别重做）；下游 STOPPED 节点复位 PENDING（旧 attempt 从未派发，无历史损失）；cursor 回退到目标；事件日志 `repair_after_reject` 落账。
- **不自动返工**：drive 对终态 run 的拒绝守卫保持（Phase48 E）——修复只能显式调用；审核重新打开需再次人工决策（A-T04 实证 decided 记录保留为历史、新 pending 审核、通过后下游照常）。
- 与 A2① 组合：修复 attempt 按跨 attempt 累计判定——驳回前已合格的部分（如 run1 的 4 角色）不必重做。

**验证**：A-T04（驳回→显式修复→envelope 含驳回记录→重派→重审→通过→下游推进→COMPLETED 全闭环）；A-T05（目标约束：node_id=n_asm 拒绝）；A-T06（stopped/completed 仍拒绝——语义未松动）。

## 4. 定向验证与回归

### 4.1 新增测试（`tests/test_p51_a_efficiency.py`，11 项全绿）

| 用例 | 对应执行书 | 结果 |
| --- | --- | --- |
| A-T01 跨 attempt 计入（run1 cd 场景重放） | A3 表 A-T01 | ✅ |
| A-T02 旧素材/不存在实体不冒充 | A3 表 A-T02 | ✅ |
| A-T03 零写入零 prior 维持原判死 | 回归保护 | ✅ |
| A-T04 驳回→显式修复→重审→下游推进全闭环 | A3 表 A-T07 | ✅ |
| A-T05 修复目标约束（不得跳过未过审上游） | A2⑥ | ✅ |
| A-T06 stopped/completed 仍拒绝 | 语义保持 | ✅ |
| A-T07 在途视频等待→重判通过 | A3 表 A-T08 | ✅ |
| A-T08 在途视频失败→诚实 failed | A2③ | ✅ |
| A-T09 poll 退避步进序列 | A3 表 A-T05（量维度） | ✅ |
| A-T10 envelope 修复提示注入 | A2⑥ | ✅ |
| A-T11 视频提交纪律只加视频节点 | A2③ | ✅ |

### 4.2 全量回归（基线对照）

全量 `tests/`（2701 项）：`_p51_full_regression.log`。首轮 77 failed / 2419 passed / 205 errors——`--lf` 重跑提取 FAILED 名单与基线**逐项对比：零新增零消失**（76 项完全一致；首轮多出的 1 项为已知 timing flake，重跑通过）。205 errors 与基线同数同源（预存：s3b2 SCIV review 'uncertain' 级联等，P50 A 报告 §8.1 在档）。**结论：零回归**（+11 = 本轮新增测试）。

### 4.3 兼容性改动点（涉及既有调用面的 3 处）

1. `_reconcile_attempt` 转 async（收养校验需在途等待）——调用点 3 处同步更新：executor 内部 await、`routers/workflow.py recover_workflows` 转 async（api_server startup await）、P48 两处测试 asyncio.run。
2. `verify_completion` / `verify_node_outputs` / `poll_video` 均为**新增 keyword 参数**（默认值=原行为），既有调用面零破坏。
3. `continue_run` 新增 rejected 分支——failed/stopped/completed 分支逐字节未动。

## 5. 目标口径（A4）与 C 批可测性

- **就绪后推进 p95≤30s**：本轮消除的判死/重启属"已就绪工作不被拒绝"；派发延迟本身（decision→drive）机制上为事件循环即时（`routers/workflow.py` continue/decide 后立即 engine.drive）。C 批以真实 run 的 attempt 起止间隙出数。
- **无理由停顿 >2min**：视频在途等待现在是**有记录的服务端等待**（job 级看门狗 10min+等待停顿守卫 25min）；审核等待=T_human_blocked 单列。C 批按 ledger 逐段归因。
- **T_avoidable −60% / T_system 中位数 −30%**：机制修复对应的可消除分量=§2.2 #1（~123min）+#2（~46min）+#3；基线口径已固定（本报告 §2 重建的 P50_B 同负荷时间轴）。真实数字由 C 批三轮同负荷运行交付；若导演行为未按纪律改变（仍回合内等结算），#2 分量将如实计入"目标未达到"。

## 6. 未知项与 B 批可用入口

1. **导演是否会按新纪律批量提交**：机制就绪（约束+安全网），行为改变需 C 批真实运行证实——这是 #2 损耗消除的先决条件。
2. **429 计数基线**：P50 B 的 text 桶 429 次数未在账本/日志中单独落账（只有探针的 10 次）——C 批建议在 ledger 增补 429 事件（未本轮实现，避免扩大改动面）。
3. **B 批可用入口**（均真实可用，测试实证）：`POST .../workflow/runs/{id}/continue`（failed 重试 / **rejected 显式修复**）→ 自动 drive；审核决策 `decide_review`（approve/reject 语义不变）；`n_dir_video` 提前结束回合不再误判失败（在途等待）；`writeSource` 标注进入交接包 outputs（下游输入冻结可见）。
4. **遗留**：视频阶段自动 hard gate（分身/人数）仍为 backlog（P50 A §9.1）；模板 `videoCount` 默认 6 与真实镜数脱节（P50 n_dir_video spec=minCount 6 vs 20 镜——靠二审兜住，C 批冻结定义时须显式钉定镜数）；轮询退避的 429 实测改善量待 C 批观测。

## 7. 修复提交清单

| commit | 内容 |
| --- | --- |
| `f98b654` | A 批主修复：跨 attempt 写入累计（executor/adapter/outputs）+ 驳回显式修复路径（continue_run/_repair_rejected_run + envelope 修复提示）+ 在途视频服务端等待与重判（_await_inflight_videos）+ 视频提交纪律接线（build_envelope）+ poll_video 自适应退避 + recover_workflows async 化 + 新增测试 11 项 + P48 测试适配 |
