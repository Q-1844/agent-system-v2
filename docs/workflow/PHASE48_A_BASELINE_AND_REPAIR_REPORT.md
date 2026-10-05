# Phase48 A批｜基线、证据收口与最小修复报告

> 日期：2026-10-06 · 分支 `feat/agent-v2-phase-d-golden-run`
> 执行书：`docs/workflow/PHASE48_A_BASELINE_AND_MINIMAL_REPAIR.md`
> 执行主体：Zcode / 执行 Agent（真实仓库 + 真机演练）
> 本批不生成新故事、不替用户批准旧片；六员体系与顺序工作流引擎全部保留。

## 1. 结论总览

| 项 | 结果 |
| --- | --- |
| 前置基线与冻结 | 完成：`PHASE48_A_FROZEN_BASELINE.json`（全字段来自实际读取） |
| A01 四轮 run 核对 | 完成：D/F1/F2/F3 六员全 success、停靠成片审定；render 全部在盘 |
| A02 目录/索引修正 | 完成（W09 旧 run、P47 16/21-26/42/44-46；原始版本 git 可追溯） |
| A03 量化收口 | 完成：evidence index §5 + `PHASE48_A_CANDIDATE_LEDGER.json`；首次合格率如实标"不可重算" |
| A04 P47 26/37/39 缺口 | 26/37 已由 A11/A10 直接演练闭合；39 保持等价路径等级（专门演练仍未单独执行，如实保留） |
| A05 W06/W07 复用核对 | 完成：C3 修复/桥接真实生成在案；发现的"旧产物绕过"缺口已按 A08 修复 |
| A06 停止标签 | 修复+测试：用户停止落 cancelled（不再一律 failed）；真实失败仍 failed |
| A07 资源发布文件校验 | 修复+测试：双路径基准解析 + 逐视图具名失败 + 失败零写共享库 |
| A08 修复目标钉定 | 修复+测试：`excludeVersionIds` + W06/W07 默认 require_new |
| A09 场景目标/单一驱动者 | 模板目标点名资产类型；全项目无活动 run/遗留 task；互斥机制在位 |
| A10 provider ID 恢复演练 | 真机直接演练通过：硬杀后同 ID 收货、零重提交、真实媒体+hash |
| A11 Skill 快照演练 | 直接演练通过：旧 run 冻结原 hash、新 run 读新内容、F3 零漂移 |
| A12 受影响回归 | 新增 12 用例全过 + 关键词扫描 213 过 + 全量回归见 §9 |

## 2. 前置检查与基线（执行书 §2）

1. **HEAD/工作树**：HEAD `e52d615a481ccc07acf09754f4a0692ed918c807`（晚于 Phase47 冻结
   `d985783`，其间仅 2 个文档/探针入库提交——合法后续，不回退）。分支
   `feat/agent-v2-phase-d-golden-run` 未漂移；工作树仅 `components.d.ts` 自动生成变化 +
   620 项历史未入库测试工件（沿项目 `_31_*`/`_44*` 惯例）。
2. **text_model 规范化**：`agnes-3.0-flash`（config/config.yaml，hash 见基线 JSON）；
   Vision 独立配置 `agnes-2.5-flash`（director_vision_review.py）未动。
   **出站 thinking 抓包核验**（`_p48_a_outbound_thinking_probe.py`）：出站请求体实际含
   `chat_template_kwargs.enable_thinking=true`，响应含 `reasoning_content`，
   URL=https://api.agnes-ai.cn/v1/chat/completions——不是配置推定。
3. **provider/模型/参数**（凭最新成功回执）：video=agnes-video-2.5-flash（t2v/i2v/
   keyframes/reference；4-12s；720P；实测 1280×704@24fps）；image=agnes-image-2.1-flash
   （1K，16:9→1312×736）；TTS 生产实测 minimax/speech-2.8-turbo（config 另有 mimo 入口，
   并记）；限流/配额按 config。回执证据：D/F 批 40 条视频版本 + A10 演练。
4. **两个正式 Skill 完整 SHA-256**（此前只有 24 位截短展示值）：
   - `full-story-relay-calibration` v1.3.1 内容 hash 全值
     `sha256:4c529461af9e20ada9b8b80b02224bc6989bf99dbdfe5af1d1aa9cd836becda3`
     （9 文件逐文件 hash 见基线 JSON；前 24 位与 Phase47 冻结值及 F3 运行快照一致 → 冻结后零漂移）
   - `keyframes-bridge-repair` v1.3.1
     `sha256:eb23f971f4cadc1bd4968b23da36fd1df4e3b3f5c3c286dcca31e55896162924`（同上一致）
5. **模板/注册**：`p47_templates_v1`；六员 registryDigest（真实运行后端 catalog 端点）：
   producer 61913bd2… / script_writer 4e99956f… / character_designer fb18cdd6… /
   scene_designer 42673f11… / director 9c6e02b7… / assembly 3b46fc0f…（工具面 24/16/20/22/32/18）。
   冻结后 F1-F3 变化：仅 2 个文档提交，引擎/Skill/模板零变更。
6. **持久化与备份**：无数据库——`projects/{p}/output/**`（原子写）+ `cache/jobs.json` +
   `shared_library/resources.json`；无独立备份目录（如实记录；代码历史由 git 承担）。

## 3. A01：D/F1/F2/F3 逐项核对（direct_runtime）

| run | 项目 | 节点状态 | 审核状态 | render |
| --- | --- | --- | --- | --- |
| run_9cc683f2ce6c（D） | 风后邮站 | 14 节点六员全 success | 6 decided approve + n_rv_final **pending** | srender_ea44c7a9 done 6.49MB；final_render=None |
| run_181eff42300e（F1） | 风铃山道 | 同上 | 同上 | srender_cf03b1ff done 6.65MB；final_render=已选 |
| run_13e741c1c934（F2） | 夜邮刺猬 | 同上 | 同上 | srender_c57fb8be done 6.05MB；final_render=已选 |
| run_fb5e2c27eac8（F3） | 山雀夜航 | 同上（含真实中断+对账续跑） | 同上 | srender_8fb05fe6 done 7.74MB；final_render=已选 |

- 主报告"四部可审未定版"在写报告时准确；本轮核对发现 F1/F2/F3 的**序列层**
  final_render 已于 10-05 16:56—17:00 经用户 REST 入口（`/renders/select`，Agent 无此
  工具）选中，晚于主报告——如实入账（P47 42 行），**run 层成片审定四轮仍 pending**，
  用户审美验收未发生，不写 PASS。
- D 批首驱动 run_22b54d2261b9（n_dir_board 诚实失败）保留为失败历史，目录已修正。

## 4. A02/A03：证据与状态统一

- **目录**：W09 行修正（原误指旧 run"进行中"）；W03/W06/W07/W08 行随 A08/A09 更新并注明。
- **证据索引**：P47 16（assembly 成片）→ PASS；21 → NOT_EXECUTED（四轮导演自主
  independent，接力专项未发生，不拿整链成功冒充接力质量）；22 → PASS（链级硬切）；
  23/24 → PASS（C3 W06/W07 真实生成）；25 → NOT_EXECUTED（负例夹具未跑，机制在位）；
  26 → PASS（A11 演练）；37 → PASS（A10 直接演练）；40 补注 A06 已修；42 → PASS
  （工程链，媒体待用户）；44 → PASS；45 → PASS（当时纪律+注明 B 批修订）；46 → PARTIAL
  （混合规格未专项测）。每行标注证据类别（direct_runtime/mechanism_test/equivalent_scenario）。
- **量化章节（evidence index §5）**：24 镜 / 40 视频版本候选 / 24 已选 final /
  4 render 全 done；**首次合格率不计**（逐版本 review 明细未随版本持久化，联合分母
  不可重算——不从成片数倒推）；attempt 级 outputsVerdict 可重算（账本内全量）。
- **主报告**：删除过时的重复"## 7"小节；§9 占位提交链替换为真实 hash
  （3b2fe68/0fe62c8/20a4b9e/acdca31/692896c/7cc570d/d985783）。

## 5. A05：W06/W07 verified reuse 核对结论

- **W06**（run_7f30add8a220）：objective 钉定问题版本 vv_2855ff25（suspected=1 纸张
  过渡压缩）；修复候选 vv_47dd2ea1 于 attempt 窗口内真实生成（task_GLJnCL07rxfu2yz，
  15:03:40）；原版保留、指针未动 → **满足修复目标，非任意旧视频顶替**。
- **W07**（run_10e21ba7f1cb）：双端 s01_c02/s02_c01，桥接候选 vv_53d832b2 真实生成 +
  bridge Skill 真实 load → 成立。
- **发现的真实缺口（→A08 修复）**：①`changed_refs` 为 shot 级——同镜头的问题版本本身
  也被计为"本 attempt created"（W06 handoff 曾把问题版本与新修复候选都记 created，账面
  不精确）；②W06/W07 默认 `allow_verified_reuse` 下，任意可播放旧视频即可满足完成校验。
  两者都可能让"有视频、verified=true"顶替修复目标。

## 6. 修复与验证（A06-A09 代码变更）

### A06 运行中停止的标签
- `src/agents/producer.py` `_settle`：turn 结果 status=stopped → AgentTask 落
  **CANCELLED**（仓库既有 cancellation 语义；stop reason 保留于 error/warning）；
  真实失败仍 FAILED。
- `src/workflow/executor.py` `_reconcile_attempt`：task=cancelled → attempt/node 落
  **stopped**（错误含停止来源，run 状态 stopped，不翻 success、不派发下游）；
  task=failed → 原样 failed。历史失败记录零改写。
- 测试：`tests/test_p48_a_repairs.py::test_reconcile_cancelled_task_yields_stopped_node /
  test_reconcile_failed_task_yields_failed_node / test_delegate_stopped_turn_settles_cancelled`。

### A07 资源发布文件校验（`src/resources/shared_library.py`）
- 缺口复现：真实角色视图为**仓库根相对**路径（`projects\{p}\output\characters\…`），
  原 `publish_character` 只按项目目录拼接 → 文件误判不存在（合法发布被误拒）；且部分
  视图缺失时被静默跳过、无具名回执。
- 最小修复：双基准解析（项目相对/仓库根相对，绝对直判）；逐视图校验（存在+非空+图片
  后缀），任何缺失/不可读/URL-only 视图 → **阻塞发布并逐项具名**
  （error=character_view_files_unreadable + failed_views[{view,code,detail}] +
  publishable_views），共享库零改动；全部合格才走原 copy-on-publish 流程。
  URL-only 视图如实具名 `url_only_no_local_file`（不判为不存在）。
- Look 复用路径（`publish`/`import`）零改动。权限/OCC/revision/ownership 全保留。
- 测试：4 项（双基准可发布 / 缺失具名阻塞+零写 / URL-only 具名 / 合法路径与幂等 republish 保持）。

### A08 修复目标钉定（`src/workflow/outputs.py` + `src/workflow/templates.py`）
- 新 spec 字段 `excludeVersionIds`（validate fail-closed：非空字符串数组）：
  被排除的问题版本在 created 与 reused 两路都不计入，缺项原因具名排除清单。
- W06/W07 模板默认 `require_new`（修复/桥接必须本节点新产出——与 C3 真实生产行为
  一致）；W06 新参数 `problemVersionId` 自动写入排除清单；两模板均支持显式
  `excludeVersionIds`。**不全局关闭 reuse**：definition 级仍可显式改
  allow_verified_reuse（服务端逐项核验不变），合格旧产物继续可复用（有测试证明）。
- 已知边界（如实）：`changed_refs` shot 级语义未改（版本级"本 attempt 新建"判定需
  派发前版本快照，超出 A 批最小修复）——以 excludeVersionIds 钉定修复目标为缓解，
  修复是否真正解决问题仍由人工审定判定。
- 测试：4 项（validate 正负例 / 排除+合法复用 / require_new 拒绝零写入任意旧视频 /
  模板默认与参数钉定）。

### A09 场景目标与单一驱动者
- W03/W09 场景节点目标点名资产类型：具体场景状态（时段/天气/门已开等）→
  `scene.state_view.prepare` 生成 **SceneInstanceView** 并绑定正确 SceneInstance；
  仅补地点资料 → LocationView；"不为达标一律生成新图"（F2 工具偏差的模板层修正）。
- 单一驱动者核查（真实现状）：全部项目无未终态 run、无运行超 1h 的遗留 AgentTask；
  `turn_registry.acquire` 会话互斥与 `engine.drive` 重复 worker 拒绝在位；F2 僵尸 run
  接管为既有机制（Phase47 报告）。未发现需要新代码的冲突路径。

## 7. A10：provider ID 后真实恢复演练（direct_runtime，一次低风险）

- 隔离：`_p48_a10_root/P48_A_Recovery`（独立项目，1 镜）；不触碰用户项目/正在运行的
  8000 后端。
- 进程 A（`_p48_a10_stepA_submit.py`，PID 36940）：真实提交 t2v 4s 720P（本演练唯一
  provider 生成请求，provider job `task_SMMqkUvC0F6Qa6BNX6NjThqCgbYPhaNx`）→
  与真实 handler 同落点持久化（shot.video.status=generating + video_id + model）→
  磁盘重读核验 → **taskkill //F 硬杀**。
- 进程 B（`_p48_a10_stepB_recover.py`）：生产同函数 `lifecycle.recover_pending_videos`
  （参数化窗口，42.md §二缺陷②）+ 隔离 JobQueue + 防重提交哨兵（包装 create_video 计数）。
- 结果：恢复检查命中持久化 video_id → 首轮 60s 窗口未完成→诚实重新入队续轮询（本地
  队列再入队 1 次，非 provider 提交）→ 同一 job 轮询完成 → 真实媒体落盘
  （s01_c01.mp4，1369434B，sha256=1fa2e153…74cc2a5，4.0s/720P）。
  **provider create_video 调用数=0；同一 video_id；wall 193.7s**。
- 证据：`_p48_a10_stepA_evidence.json` / `_p48_a10_stepB_evidence.json`。

## 8. A11：半途改 Skill 的快照验证（direct，隔离副本）

- 方式：SkillRegistry 注入临时 source_dirs（仓库支持的测试注入点，**正式 builtin 零
  写入**）；真实 validate/compile/snapshot/RunStore 持久化；内容更新=编辑隔离包文件+
  版本号（仓库支持的方式）。
- 结果：run1 冻结 `sha256:c2ce9e6bec6761c8f921ee46` → 内容更新（v1.4.0→1.4.1）→
  run2 快照 `sha256:c65b8443234d9d815e0f33c9`（≠旧、=当前包真实 hash）→ run1 从磁盘
  重读**仍为原 hash**（历史不改写）。语义注记：快照 version=定义边请求版本（定义层
  冻结）；contentHash=实际包内容快照。
- 生产交叉核对：F3 运行快照 relay hash == 当前正式包 hash（冻结后零漂移）。
- 证据：`_p48_a11_drill_evidence.json`。→ P47 26 内容变化场景闭合。

## 9. A12：回归与收口

| 套件 | 结果 |
| --- | --- |
| 新增 `tests/test_p48_a_repairs.py` | 12 passed |
| 受影响组合（p46 引擎/p47 语义/共享库） | 41 passed |
| 关键词扫描（producer/delegate/workflow/shared/settle/recover/stop） | 213 passed |
| 全量 `pytest tests/ -q` | **2669 passed, 1 failed**（1274.76s）——失败项
  `test_ai_7ch_cancel_race::test_case1_stale_object_resurrection` 为 Phase47 起已知全量
  负载偶发项，隔离复验 12/12 passed，与本轮修复无关（本轮未触碰 cancel 路径的既有机制，
  仅新增 cancelled≠failed 的语义分支）；上次全量失败的 autopilot 偶发项本轮通过 |

## 10. 交付物清单

| 文件 | 内容 |
| --- | --- |
| 本报告 | 改动、无需改动项、直接证据与未完成边界 |
| `docs/workflow/PHASE48_A_FROZEN_BASELINE.json` | A 批冻结基线（完整 hash） |
| `docs/workflow/PHASE48_A_CANDIDATE_LEDGER.json` | 可重算候选账本（缺记录标 unknown） |
| `docs/workflow/PHASE47_TEST_EVIDENCE_INDEX.md`（更新） | A02/A03/A04 修正+§5 量化 |
| `docs/workflow/PHASE47_WORKFLOW_CATALOG.md`（更新） | W09 行修正+W06/W07/W03/W09 契约更新 |
| `docs/workflow/PHASE47_WORKFLOW_IMPLEMENTATION_REPORT.md`（更新） | 重复 §7 删除+§9 真实提交链 |
| 代码 diff | producer.py / executor.py / shared_library.py / outputs.py / templates.py |
| `tests/test_p48_a_repairs.py` | 12 项修复回归 |
| `_p48_a_outbound_thinking_probe.py` + result | 出站 thinking 抓包核验 |
| `_p48_a10_stepA_submit.py` / `_p48_a10_stepB_recover.py` + evidence×2 | A10 直接演练 |
| `_p48_a11_skill_snapshot_drill.py` + evidence | A11 演练 |
| `_p48_a_catalog.json` / `_p48_a_build_ledger.py` | 基线取证与账本构建脚本 |

## 11. 未完成边界与既有问题（如实）

1. **用户关口未动**：四部成片的 run 层成片审定（n_rv_final）四轮 pending；用户审美
   验收未发生。F1/F2/F3 序列层 final_render 为用户 10-05 REST 选中，如实入账。
2. **首次合格率不可重算**（历史记录粒度所致）——已建账本，后续 B-E 批新实验按账本
   字段完整记录，可避免再次出现。
3. **P47 39 派发窗口**：保持"等价路径"证据等级，专门直接演练未做（机制与 R1 同源）。
4. **P47 21/25**：接力质量专项与桥接负例夹具未执行（C 批将按实验设计覆盖）。
5. **shot 级 changed_refs 语义**：版本级"本 attempt 新建"判定未改（见 §6-A08 边界）。
6. **既有历史问题单列**：全量回归中的 2 个偶发项（cancel_race/autopilot——Phase47 起
   即为全量负载偶发，隔离复验过）；vue-tsc 历史 61 类型错误；均与本轮修复无关。
7. **隔离演练项目**：`_p48_a10_root`（含 1 条真实 4s 视频）与 `_p48_a11_root` 留存为
   演练证据，不属生产项目。

## 12. 退出条件核对（执行书 §6）

| 条件 | 状态 |
| --- | --- |
| 资源缺文件不能发布 | ✅（A07：缺失/不可读/URL-only 具名阻塞，零写共享库） |
| 用户停止不可被晚到结果翻转 | ✅（既有 R7 机制 + A06 语义修正；stopped 不翻 success） |
| 修复目标不可由无关旧产物完成 | ✅（A08：require_new 默认 + excludeVersionIds；合法复用保留） |
| 来源和快照可追溯 | ✅（A11：run 冻结 hash 不改写；A01/账本全量溯源） |

**A 批出口：满足。可进入 B 批（原生音效保留与装配政策）。**
按用户指示，A 批完成后暂停，等待后续批次执行书。
