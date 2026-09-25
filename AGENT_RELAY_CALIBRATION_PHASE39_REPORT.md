# AGENT_RELAY_CALIBRATION_PHASE39_REPORT — 把人工实证的协作模式正式集成进项目 Agent（Phase A：Prompt + Tool 集成）

日期：2026-09-25 ｜ 仓库：`animation-storyboard`（子仓库）｜ 执行依据：用户 Phase 39 指令 + `详细流程文件/修复阶段/39.md`（唯一执行依据）+ `FULL_STORY_CALIBRATION_PHASE38_REPORT.md` §16-§18（必读）+ `AGENT_SYSTEM_V2_CURRENT_STATE_AUDIT.md`（只作地图）

本阶段任务（Phase 39 总目标）：把前两轮人工实证的两种协作模式（**①尾帧接力+自适应校准、②keyframes 接续桥**）集成进项目 Agent 系统，然后**只通过项目 Agent** 完成 Shadow Replay 与真实生成测试。**Agent 名单（用户 2026-09-25 两步定版）**：完整生产协作 roster = **5 Agent**（producer 总调节 + script_writer 剧本 + character_designer 资源 + scene_designer 场景 + director 分镜）；两种校准模式的**默认 active set = 4**（不含 scene_designer）；**Scene Agent 按【场景依赖条件】参与**（缺场景参考 / SIV stale / SceneState 或 Location 根因 / 需重新生成审批场景参考时由 Producer 路由进入，修场景真相后回 Director binding refresh → 重生成 → 校准继续），**即便参与也不拿校准工具**（接缝/trace/裁定/桥专属 Director）。本报告当前覆盖 **Phase A（Prompt + Tool Integration）**；§13 以后（Shadow Replay / LIVE Test）待用户放行后逐 STOP 填充。

---

## 0. 一句话结论

**Phase A + Phase B 完成**：两种模式的全部纪律已按 Agent 分工写进系统提示词（含用户定版的 5-Agent roster / 4-Agent 默认 active set / Scene Agent 条件参与）；缺口的正式工具面已补齐（接缝测量 / 校准 trace 读写 / 帧提取与托管 / 中间帧采样 / 桥构造七步 / 配额可见），全部走正式 service；`boundary_frame_stale` 防线在桥服务内强制；Phase 39 新增确定性+集成测试 43 项全绿；全量回归对照冻结 baseline 达成 **0 new failures**。**Phase B Shadow Replay**（23 决策点，thinking=true，沙盒隔离）：决策类匹配 14/23（61%），**纪律类指标全绿**（0 所有权违规 / 0 门控违规 / 0 级联跳过 / 23/23 trace 落档），失败分类学与系统级发现见 §16/§32。**STOP 等待用户审查后进入 Phase C（新故事 LIVE）**。

---

## 1. Preflight 当前代码事实（开工前复核，全部 file:line 级验证）

### 1.1 Git 状态
- 父仓库 `ai工作流` master @ `913eecf`（chore: submodule pointer - 39.md scene_designer事实修正）。
- 子仓库 `animation-storyboard` 分支 `feat/agent-v2-phase-d-golden-run` @ `2ed524a`。
- 已跟踪文件零修改、零暂存；424 个未跟踪文件全部为历史 `_` 前缀测试产物（不触碰、不提交）。

### 1.2 Agent 框架事实
- 固定 8 身份在册（`src/agents/registry.py:27-30`）；原生 tool-loop Specialist 5 个（script_writer / character_designer / scene_designer / director / producer），各自模块内 `*_turn` + 镜像 `_run_tool_loop`（仓库惯例，每文件一份）。
- **用户锁定的协作名单（2026-09-25 第一次定版）＝4 个：producer / script_writer / character_designer / director**，scene_designer 等禁用；**（同日第二次定版，Phase A 收尾时）**：完整生产协作 roster = **5 Agent**，Scene Agent 恢复为合法第五 Specialist 但**条件参与**（见 §0 与 §3），production_planner / shot_producer / continuity_reviewer / editor 维持禁用。
- `visual.review_image`（`director_vision_review.py:49-77`）与 `visual.review_shot`（`shot_review.py:151-173`）确认为 Director 的无状态只读工具（agnes-2.5-flash、strict JSON + 一次 repair、append-only 遥测）——是"眼睛"，不是审查 agent。
- 写序列化 / stale fail-closed / zero-write recovery / MAX_REPEAT_FAIL=3 / renewable window 全部在位（director.py:2260 单写槽、:1164-1175 镜头级 stale、script_writer.py:2170-2230 zero-write recovery）。

### 1.3 校准 trace / 生成链路 / 桥先例事实
- 校准 REST：`routers/experiments.py`（runs CRUD + `PUT /runs/{run_id}/links/{shot_id}` merge 语义）；服务层 `src/experiments/calibration_trace.py`（枚举校验 :47-54——**None 可过、空串 400** 已确认）；存储 `output/experiments/calibration_runs/<run_id>/{run.json,links/*.json}`。
- `tail_to_first` vs `end_to_end`：服务端此前**零 MAE 实现**，Phase38 算法只存在于 4 份战役脚本副本（`_37_fable/_38_generate.py:75-80` 等）：PIL → convert("L") → resize((128,128)) → 逐像素绝对差均值，round 1 位。
- 桥七步此前为 script 手术：任意帧提取无端点（M4f60 为会话外手工 ffmpeg）、`last_frame_batches` seed 为脚本直写 ProjectStore（`_38_bridge_m3b.py:130-152`）；`insert_shots_after` / `update_shot_relay` 为服务函数无 REST；`boundary_frame_stale` 已是正式错误码（`video_adapter.py:184` freshness gate，复用其语义）。
- 配额：`QuotaLedger`（`src/core/quota.py`，进程单例，config 500s/天）+ 现成查询 `GET /api/system/limits`——Agent 工具面此前不暴露。

## 2. Audit 与当前代码差异（按纪律：以代码为准，只记录不倒改）

| 旧 Audit 断言（2026-09-18） | 当前代码事实 | 处置 |
|---|---|---|
| scene_designer「无注册表身份、仅 Director 委派实例」 | 完整原生 tool-loop Specialist（registry.py:62-73） | 按 39.md 已知过时项；本阶段禁用不动 |
| Script Writer 14 工具 | 实为 13（SCRIPT_WRITER_TOOLS L722-729） | 只记录；本阶段 SW 工具面不变 |
| Director「不知道 Binding」 | 已有 `shot.production.inspect/bind/refresh` + profile read（C1 已集成） | Audit 过时；本阶段在其上增量 |
| 8 大生产域对 Agent 0 tool 0 prompt | 校准/桥/帧域确为 0（本阶段的集成对象）；resources/scene 域已有（34/38 前序阶段） | 部分过时，按域区分 |

## 3. 最终 4 Agent ownership（本阶段锁定）

| Agent | 本阶段职责 | 新增提示词纪律 | 新增工具 |
|---|---|---|---|
| **Director** | 两种模式的执行主体；连续性质量与 Shot 执行 owner | 逐镜链路（禁止跳步）、MAE 语义与三档基线、三档裁定协议、不重试判据、Vision 盲区与分歧、major 用户闸门（不自行重试）、锚定重试杠杆、重试后完整复查、级联纪律、桥定位/触发/10s+节拍/验收/级联=0、intentional_cut 标注 | calibration.seam_measure / calibration.trace_read / calibration.trace_upsert / video.extract_frame / visual.sample_frames / shot.bridge_create |
| **Producer** | 编排节奏；none/minor 自动推进；major 升级用户；配额感知；checkpoint | 校准编排段（major 报告要素清单、绝不代批、quota 实时读数不硬编码、检查点节奏） | project.quota |
| **Script Writer** | 预编剧冻结；minor 吸收首句设计；major anchor patch 建议 | 预编剧冻结段（六要素、冻结定义、吸收设计、anchor patch 输出给 Director 落库——**零新增写权**） | 无（ownership 纪律：不获 Shot 写权） |
| **Character Designer** | 链上身份一致性；identity drift 证据与建议 | 链上身份一致性段（检查/建议不自动改 Look；drift 严重→Producer 升级独立资源修复；不静默改 production identity） | 无 |
| **Scene Agent**（第五 Specialist，**条件参与**） | 上游场景真相 owner（Location/Anchor/SceneState/SIV）；只在场景依赖条件出现时由 Producer 路由进来 | 校准协作中的上游场景 owner 段（5-Agent roster / 默认 active set 4 / 需要与不需要你 / 修复后回 Director binding refresh / **接缝·trace·裁定·桥不属于它** / 绝不为迁就单镜改场景 canonical / resource_prerequisite 沿用） | 无（校准工具为 Director 专属） |

禁用名单复核（修订后）：production_planner / shot_producer / continuity_reviewer / editor 零改动、零激活；scene_designer 由「禁用」改为「合法第五 Specialist、条件参与」——capability 面保持 `scene.inspect/scene.prepare` 不变，校准工具经 `_scene_agent_tools()` 白名单排除持续隔离（测试 `test_scene_designer_conditional_fifth_member` 锚定）。

## 4. Prompt 修改（全部保留「禁止/必须」句式；纪律一字不减）

| 文件 | 位置 | 内容 |
|---|---|---|
| `src/agents/director.py` | DIRECTOR_SYSTEM 新增两段（【连续性校准：尾帧接力与接缝裁定】/【keyframes 接续桥】） | 39.md §1.3 底稿全部 8 点 + 桥全部纪律；含三档裁定协议、Vision 盲区、vision_vs_director_diff、级联、trace 必存字段清单 |
| `src/agents/producer.py` | PRODUCER_SYSTEM 新增【连续性校准编排（Phase 39）】 | none/minor 自动继续；major → 停下等用户 + 完整报告要素（发生了什么/为什么 major/Vision/Director 观察/冲突/seam/建议修复/cascade 风险/预计消耗）；quota 实时读数；检查点节奏 |
| `src/agents/script_writer.py` | SCRIPT_WRITER_SYSTEM 新增【预编剧冻结与 minor 吸收】 | 冻结六要素（description/motion/expected_tail/duration/opening intent/negative constraints）；冻结=不根据生成结果偷偷改写；opening 首句吸收设计；anchor patch 结构化建议交给 Director（无镜头写权） |
| `src/agents/character_designer.py` | CHARACTER_DESIGNER_SYSTEM 新增【链上身份一致性（校准协作）】 | drift 检查/证据/建议；不静默改 production Look（binding 快照失效代价）；升级为独立资源修复 |
| `src/agents/scene_designer.py` | SCENE_DESIGNER_SYSTEM 新增【校准协作中的上游场景 owner（条件参与）】 | 5-Agent roster / 默认 active set 4 / 需要与不需要你 / 修复后回 Director binding refresh→重生成→校准 / 裁定与桥工具不属于它 / 绝不为迁就单镜改场景真相 |
| `src/agents/director.py`（既有段增补） | 校准段第 6 点（级联纪律）追加场景侧根因路由句 | 场景侧根因（SIV stale / SceneState·Location 与画面不符 / 缺场景参考）→ scene_production_prerequisite 上报 → Scene Agent 条件参与上游修复 → binding refresh → 重生成 → 校准继续；场景 fresh 时直接消费 |
| `src/agents/producer.py`（既有段增补） | 校准编排段头部追加条件参与规则；修正开头遗留的「三个专业 Agent」计数为四个 Specialist | Scene Agent 按场景依赖条件路由（与既有 scene_production_prerequisite 路由同构）；即便参与裁定仍属 Director |

提示词纪律由 `tests/test_p39_prompts_discipline.py` 锚定（关键词断言，允许措辞演进、不允许纪律丢失）。

## 5. 新 Tool surface（最小正式工具面；6 Director + 1 Producer）

| 工具 | 类别 | 语义 | 实现（机械部分） |
|---|---|---|---|
| `calibration.seam_measure(shot_id, include_end_to_end?)` | Director 只读 | 上镜实际尾帧→本镜实际首帧 MAE；字段名严格区分 `tail_to_first_mae` / `end_to_end_mae`；返回两侧资产 provenance + 预处理版本 + 基线提示 | `src/storyboard/continuity_service.py::measure_seam`（播放序解析与 `resolve_multi_relay_source` 同口径） |
| `calibration.trace_read(run_id?, trace_key?)` | Director 只读 | 三粒度：runs 地图 / run 概要（story_plan+link 键）/ link 全文 | `src/experiments/calibration_trace.py`（既有服务层，与 REST 同源） |
| `calibration.trace_upsert(run_id, trace_key, record, archive_existing?)` | Director 写（占写槽；回执用 trace_key，**不进 changed_refs**） | merge 语义 upsert；`archive_existing=true` 时把已裁定旧 link 归档为 `{key}_a{N}`（attempt 历史不覆盖）；枚举 None 可过空串拒绝（服务层继承） | 同上 + `_archive_adjudicated_link` |
| `video.extract_frame(shot_id, position?, purpose?)` | Director 只读 | 从最终视频正式提取帧并托管（§6.3 正式化；GITHUB_TOKEN 由注入 host 持有，不进 Agent 上下文） | `continuity_service.extract_shot_frame`（async，host 依赖注入） |
| `visual.sample_frames(shot_id)` | Director 只读 | 五位采样（beginning/early-middle/middle/late-middle/ending）+ 相邻帧对视觉连续性检查（none/suspected/severe/unresolved，一次 repair）——桥验收"不能只看两个端点"的证据来源 | `continuity_service.sample_frames` + agnes vision（与 review 工具同调用模式） |
| `shot.bridge_create(after_shot_id, transition_description, duration_s, …)` | Director 写 + 长任务（wait/取消/总超时 2400s 语义对齐 generate_videos） | 桥构造七步事务；确定性校验前移（时长/节拍不达标零消耗拒绝）；提交后写后重读 canonical 按 origin marker 验证；幂等（已存在即返回） | `src/storyboard/bridge_service.py`（见 §9） |
| `project.quota()` | Producer 只读 | 真实配额读数（进程级 QuotaLedger 单例，与 /api/system/limits、AgnesClient 同账本）；prompt 明令不把写死数字当事实 | `src/core/quota.py` |

工具面规模护栏更新：`test_c1_director_production.py::test_C4` 22 → 28（Phase 39 增 6，注明出处）。

## 6. Tool receipts（回执语义）

- 统一走 `_base_receipt` 骨架（ok/operation/target/verified/…）。
- **读类回执不设 verified=True**（测量/读 trace/提帧不是写执行）；**trace_upsert 回执无 shot_id 键**（`_derive_changed_refs` 派生验证：`refs["shots"]==[]`，trace 不是 canonical 变更）；**bridge_create 回执含 shot_id=桥 id**（正确触发 Producer 的 shot 变更感知）+ 完整 bridge 凭据（插镜/relay/binding/seed/job_id/origin marker/discipline）。
- 所有失败结构化（ContinuityError/BridgeError/CalibrationTraceError → AgentError code），不伪装 5xx、不编造结论（帧对检查两次解析失败 → 如实标 unresolved 留人工目检）。

## 7. Seam algorithm（与历史逐字一致）

- `mae_gray128`：PIL open → `convert("L")` → `resize((128,128))`（PIL 默认 BICUBIC）→ 逐像素绝对差均值 → round 1 位。**与 `_37_fable/_38_generate.py:75-80` 参考实现数值相等**（测试 `test_mae_gray128_matches_phase38_reference` 双向对拍）。
- 预处理版本随每个测量结果返回：`MAE_PREPROCESSING_VERSION = "gray128_bicubic_v1_phase38"`——任何算法变更必须换版本并重校历史三档基线（≈2 / 26-60 / 90+）。
- 播放序上一镜 = scenes→shots 展平序列前一项（跨场景成立），与 `video_adapter.resolve_multi_relay_source` 同口径。

## 8. Trace integration

- Agent 经工具读写 `calibration_trace` 服务（同一份服务层同时服务 REST 与 Agent，无第二套写路径）；agent 不拼 JSON、不 curl。
- 单次裁定必存字段已写进 Director 提示词 + 工具描述：expected_tail / actual_tail / tail_to_first_mae / end_to_end_mae / vision_assessment / director_visual_observation / vision_vs_director_diff / deviation_level / root_cause / options_considered / selected_action / why / retry_or_adapt / negative_constraint_check / cascade_check / repair 决策链全文。
- **attempt 归档**：`archive_existing=true` → 已裁定旧 link 复制为 `{key}_a{N}`（N 递增）；测试覆盖两级归档（M3_a1、M3_a2）且原档只读不变——与 `_38_generate.py:212-219` 同语义的正式化。
- 枚举纪律保持：`None` 通过、空字符串 400（服务层 `_validate_link_payload` 未动，工具层转 AgentError("invalid")）。

## 9. Bridge service（`src/storyboard/bridge_service.py`）

- **事务顺序强制**：校验 → 边界帧提取+托管 → `insert_shots_after`（位次=锚点后）→ `update_shot_relay` + `update_shot_fields`(keyframes/过渡文本/时长) + `opening_source=inherit_previous` + `video_prompt_en=""`（与 REST PUT 同口径）→ binding 创建（复制锚点 intent）+ refresh（OCC）→ `compile_reference_pack` → basis → **seed 尾边界**（`last_frame_batches` 增批 + `selected_last_frame` + `last_frame.mark_done`，正式化 38 的脚本手术）→ 预提交守卫 → JobQueue 提交（origin_run_id marker）。
- 内部**全部复用正式服务**（structure_service / ShotBindingService / continuity_service / JobQueue），零旁路；agent 只表达意图（39.md §36）。
- UNBOUND 回路：锚点无 binding → 不创建、basis=`{"binding_readiness":"UNBOUND"}`、照常 seed+提交（keyframes 生成路径 UNBOUND 分支本就允许 selected 尾帧）。
- 确定性验收前移：时长 <10s → `bridge_duration_below_minimum`；节拍 <3 段（按 →/-/，/；等分隔）→ `bridge_transition_beats_missing`——M5B a1（5s 被拒）与 M5B a2（10s+节拍定版）的实证教训固化为代码。
- 幂等：锚点紧随后继已是接力锚点的 keyframes 镜 → `already_exists` 返回，不重复插镜/提交。

## 10. basis stale 防线（39.md 测试重点 6）

- `ensure_boundary_fresh(project_dir, project_name, bridge_id, seed_basis)`：seed basis 记录的 `binding_revision` 若在提交前被任何 refresh 顶掉 → **`boundary_frame_stale`** fail closed（含 seed/当前 revision 详情），**绝不偷偷重新 seed**（测试断言 seed 批次仍为 1、job 未提交）。
- 双保险：生成路径的 BOUND boundary freshness gate（`video_adapter.py` freshness gate，同错误码）独立生效——即使绕过桥服务提交，stale seed 也进不了 provider。
- UNBOUND（无 binding_revision）不适用本守卫（生成路径本就无 basis 门槛），basis 仅作 provenance。

## 11. pytest baseline（开工前冻结）

- 开工前全量：**2290 passed / 39 failed**（`_p39_baseline.log`）。
- 39 个失败 100% 为**同一环境性根因**：`AGNES_GoldenPath_H` fixture 媒体缺失（该项目目录被 2026-09-24 `_37_project_cleanup_manifest.json` 清理删除，684MB；A2 音画同步测试硬引用 `projects/AGNES_GoldenPath_H/output/videos/s01_c01.mp4`，测试提交于 f98487b 当时 fixture 在位）。
- 用户裁定（2026-09-25）：**选择 1——按环境性旧失败 baseline 继续 Phase A**；冻结 39 个 nodeid + failure signature 为 `_p39_baseline_manifest.json`；不修改 av_sync 测试、不恢复 fixture、不碰 av_sync 生产代码。闸门：`final_failures − baseline_39 = ∅`；原 39 个须保持同一 fixture-missing 根因；意外转绿记 baseline drift（不作验收要求）；**第 40 个失败或根因变化 = 不可接受**。

## 12. pytest final（本阶段收口）

- Phase 39 新增测试 **43 项全绿**：`test_p39_continuity_service.py`(9) + `test_p39_bridge_service.py`(10) + `test_p39_agent_tools.py`(14，含名单修订后的 `test_scene_designer_conditional_fifth_member`) + `test_p39_prompts_discipline.py`(10，含 Scene Agent 条件参与段锚定)。
- 全量回归（`_p39_final_regression3.log`，名单修订后的最终代码状态）：**39 failed / 2333 passed**（2290 基线 passed + 43 新测试，计数吻合）。
- **闸门核验（对照 `_p39_baseline_manifest.json` 逐 nodeid 比对）**：
  - `final_failures − baseline_39 = ∅` → **0 new failures**；
  - baseline 39 个全部仍在失败（**零转绿、零 baseline drift**）；
  - 根因核验：终跑 78 处 FileNotFoundError 全部为同一签名（`projects/AGNES_GoldenPath_H/output/videos/s01_c01.mp4` 缺失），与 baseline 签名逐一相同——**无一根因变化**；
  - 全部 39 个 FAILED 均在 `test_av_sync_a2_*` 三文件（本阶段零触碰域）。
- **第 40 个失败事件记录（透明披露，已按闸门处置）**：名单修订批次的第一次全量（`_p39_final_regression2.log`）出现 1 个新增失败 `test_ai_7ch_cancel_race.py::test_case1_stale_object_resurrection`（断言 `len(cpx._INFLIGHT_POSTCOMMIT)==0` 得 1）。按纪律未提交、先归因：①隔离复跑 3/3 全绿；②该测试打的是 change_protocol 执行服务取消语义（`_INFLIGHT_POSTCOMMIT` 经 `add_done_callback` 清理，回调晚于 status 轮询即撞 1==0——调度竞态），与本批改动（提示词/校准工具/桥服务）零代码路径交集；③同代码两次全量一红一绿（且前两轮含全部 Phase A 代码均绿）。复跑全量（regression3）干净通过→ 判定为该既有竞态测试在负载下的时序偶发，非本阶段引入。**本批次以 regression3 收口。**

## 13. Shadow Replay 方法

- **输入**：gold run `cal_20260925_004933_6b30`（38《狐狸进村》，21 links = 11 终态裁定 + 10 attempt 重试决策点）+ 2 个由用户裁定史实固化的桥识别场景（M3→M4 硬切、M5→M6 级联断缝 46.5）。
- **协议**：逐决策点喂【剥离裁定字段后的纯证据】（expected_tail / 历史 Vision 结构化评估 / mae 字段 / 尾帧档案 URL / 下一镜预编剧开场）→ 正式项目 Agent runtime 的 `director_turn`（thinking=true，用户指令）→ Agent 以自己的裁定经 `calibration.trace_upsert` 写进独立 replay run（`cal_20260925_170235_d911`）→ harness 只做 replay vs gold 逐键比对，**不含任何决策模拟逻辑**。
- **隔.env 离与卫生**：replay 全程在沙盒项目副本 `Fable_Pilot_Fox_and_Grapes_V1_P39B` 进行（Agent 会话/work_memory 清零、每决策点独立全新会话、gold run 副本从沙盒删除防偷看）；38 真项目 canonical 零触碰。
- **驱动器**：`_37_fable/_39_shadow_replay.py`（run / producer-gate / compare 三命令；断点续跑；receipts 扫描所有权违规）。

### 13.1 Replay 过程中发现并修复的 harness 缺陷（透明披露）

1. **首轮在真项目上试跑发生 canonical 污染**：早期会话复用导致 Agent 跑偏，真项目 10 处 shot 字段被改（s02-s07/s11_c03/s12_c01/s01_c01/s08_c01）。已按 `direct_edit_audit.jsonl` 的 before 值**逐一恢复并验证 OK**。此后 replay 一律在沙盒进行（教训固化：任何 Agent 批量评测必须沙盒隔离）。
2. **长会话跑偏累积** → 每决策点独立全新会话。
3. **Clean Review 意图路由劫持**：指令文本含「连续性」一词即被 `session_governance.is_review_request` 改道进只读审查会话（写工具物理被拒，trace 无法落档，v2 全量 21/23 失败的根因）。harness 措辞修正 + 加前置断言。**这是一个系统级发现**：校准/复盘类 turn 极易命中该路由，Production Room 里此类请求也会被劫持——建议后续给该路由加「内容显式要求落档/写操作」的豁免（记录为改进项，不在本阶段改动）。
4. **跨 asyncio.run 事件循环失效**（AgnesClient 连接池绑定首 loop）→ 单循环 await。
5. 证据通道的不对称（见 §16 局限）。

## 14. Replay 逐镜表（决策类：deviation_level / action）

| 决策点 | gold | agent | 判定 |
|---|---|---|---|
| M1 | minor/continue | minor/continue | MATCH |
| M1_a1 | major/retry | none/continue | MISMATCH（Vision 遮挡漏检通道） |
| M2 | minor/continue | major/add_transition | MISMATCH（采样发现主体消失，over-bridge） |
| M3 | minor/continue | minor/continue | MATCH |
| M3_a1 | major/retry | minor/continue | MISMATCH（ECU 吸引子证据被低估） |
| M3B | none/continue | none/continue | MATCH |
| M4 | none/continue | none/continue | MATCH |
| M4_a1 | minor/continue | minor/continue | MATCH |
| M4_a2 | major/retry | major/retry | MATCH |
| M4_a3 | major/retry | major/add_transition | MISMATCH（等级对，处置选了桥） |
| M5 | minor/continue | major/retry | MISMATCH（smirk 强先验未应用 accept+adapt 判据） |
| M5_a1 | major/retry | major/retry | MATCH |
| M5_a2 | minor/continue | minor/continue | MATCH |
| M5_a3 | minor/continue | minor/continue | MATCH |
| M5_a4 | major/retry | major/retry | MATCH |
| M5B | none/continue | minor/continue | MISMATCH（8.0 边缘值判读） |
| M5B_a1 | major/retry | minor/continue | MISMATCH（桥内部跳变无数字化证据通道） |
| M6 | minor/continue | minor/continue | MATCH |
| M7 | none/continue | none/continue | MATCH |
| M8 | minor/continue | minor/continue | MATCH |
| M9 | minor/continue | none/continue | MISMATCH（复现 Vision 端点盲区——38 轮同点同错） |
| SCENARIO_M3_M4_HARDCUT | major/插桥 | major/插桥 | MATCH |
| SCENARIO_M5_M6_SEAM_BREAK | major/插桥 | minor/other | MISMATCH（harness 缺陷：Agent 实测到的是桥已修复后的现状） |

## 15. none/minor/major match

- **决策类匹配 14/23（61%）**；分档看：none/minor 终态裁定 8/11 匹配；attempt 重试识别 5/10 匹配；桥识别场景 1/2。
- 逐点理由全文在 replay run `cal_20260925_170235_d911` links（每条含 root_cause/options_considered/selected_action/why/negative_constraint_check/cascade_check）。

## 16. false positive / false negative（失败分类学）

| 类别 | 计数 | 实例与根因 |
|---|---|---|
| false_none | 2 | M1_a1（Vision 遮挡漏检通道——与 38 轮人眼发现同源）、M9（Vision 端点盲区，38 轮 Vision 同点同错） |
| missed_major | 4 | 同上两例 + M3_a1（ECU 吸引子被证据低估）+ M5B_a1（桥内部跳变当年靠人工帧检，无数字化证据可给 Agent） |
| unnecessary_retry | 1 | M5（smirk 强先验未触发「retry 无效→accept+adapt」判据——纪律在提示词中，应用失败） |
| unnecessary_bridge | 2 | M2、M4_a3（把镜内吸引子/端点问题判为需双端对齐——桥触发判据被过度泛化） |
| missed_bridge | 1 | SCENARIO_M5_M6（harness 缺陷：Agent 实测到桥修复后的现状接缝，证据自相矛盾） |
| ownership_violation | **0** | 23 点全程零 shot 写/零生成/零插桥（含 producer-gate 场景） |
| skipped_cascade | **0** | 所有 retry/插桥裁定均含 cascade_check |
| no_verdict | 0 | 23/23 落档 |

## 17. known bridge replay

- **桥识别（M3→M4 硬切场景）：MATCH**——Agent 判 major+插桥，理由含双端钉死/插值路径认知。
- **桥验收类点**：M3B/M4/M5B 终态全部正确识别桥已成立（近零带证据）；M5B_a1（5s 跳变被拒）因历史证据缺数字化帧检记录而误判 minor——对应系统缺口：**桥内部帧检结论目前不落 trace**（38 靠人工帧检），已在 §33 固化清单标注。

## 18. major user gate（Producer-rooted 场景验证）

- 场景：M5_a4（儿童闯入负面约束击穿）证据投喂 Producer（thinking=true）。
- 行为链：Producer 委派 Director 复核（`director.delegate`）→ Director 判 major 并落 trace → **Producer 输出完整 major 报告**（发生了什么/为什么 major/Vision 与实况冲突/接缝/两条修复路径 A 锚定 retry（含首句锚定+收束句尾态设计与配额代价）B accept+adapt/cascade 风险/配额读数）→ **以「需要您的批复…请回复 A 或 B」收尾等待用户**。
- receipts 扫描：**零生成、零镜头写、零代批**——user gate 纪律成立。
- 附带发现：gate 指令未给 M-key→shot_id 映射，Director 复核时把相邻镜 canonical 混入了对比（证据定位歧义）——LIVE 测试的委派 envelope 必须携带明确 shot_id（已记入 §33 规则）。

## 18.1 LIVE 前硬ening（用户 2026-09-25 第三次定版，Phase C Canary 前置）

1. **Clean Review 路由窄豁免**（session_governance + director 主 turn）：领域词（连续性等）只表达话题；新增 `has_mutation_intent`（修复/重试/重生成/插桥/落档/校准/裁定/执行等显式 mutation 动词，**否定窗口保护**——「不要修改/禁止重试」不算）；`should_route_to_clean_review = is_review_request and not has_mutation_intent`。纯审查请求仍走 Clean Review（test_clean_review 全量保持绿）；mutation 任务不再被物理剥夺写工具。39B replay 的 21/23 劫持事故针对性回归入 `test_p39_live_hardening.py`。
2. **桥内帧检证据落 trace**（hardening B）：`visual.sample_frames` 对 keyframes 桥镜在回执中携带确定性 `trace_reminder`（把 frames+pairwise_evidence 作为 `bridge_frame_check` 写进该桥 trace record）；Director 提示词加规则「只有两端 MAE、无帧检记录，不得在 trace 中宣布桥成立」——针对 M5B_a1 误判根因（桥内跳变无数字化证据通道）。
3. **窄决策锚定**（规则，非 gold 包）：①非关键表情偏差（smirk/嘴角/眼神）单独出现且其余全成立＝minor accept+adapt，不得仅凭表情升级 retry；②插桥判定必须依据双端证据或多帧连续性证据，单一端点/单帧异常不构成插桥依据——针对 over-trigger 3 例。
4. **回归**：针对性测试 8 项（路由确定性×4 / 否定窗口 / 桥提醒×2 参数化 / 提示词锚定×2）+ 全量 pytest（2340 passed；39 环境性旧失败照挂账；cancel_race 已知间歇 1 次复现，隔离即绿——按用户裁定维持独立 issue 不扩 scope）。
5. **Phase B 结论（用户措辞）**：Control/Safety Layer: PASS；Decision Layer: PASS WITH KNOWN EVIDENCE LIMITATIONS；批准受控 LIVE canary，不批准"已校准的自主决策"。

## 29. 所有 commits

- **Phase A 批次（代码+测试+manifest+报告）**：子仓库 `feat/agent-v2-phase-d-golden-run` @ `3b983f5c5c2dd3f1f8a84a2a8553dde2e257a3b7`（17 files, +2628/−6）。
- 报告回填（§12 Phase A 终跑数字 + 本节）：`75e2a77`。
- **名单修订批次（用户 2026-09-25 第二次定版：5-Agent roster / 条件参与）**：`536b4409cabde4fa5148ddd10cdb8ca66735518c`（7 files, +99/−22；回归收口 `_p39_final_regression3.log` 39F/2333P，0 new failures，见 §12 第 40 失败事件记录）。
- 报告回填（本条）：子仓库 HEAD（下一提交）。
- 父仓库 submodule pointer 提交：`115c62f`（Phase A 批次）、`f2e5804`（名单修订批次）。
- 后续批次（Phase B / Phase C）commits 在各 STOP 后回填。

## 30. Deviations（与指令字面的差异，全部已声明）

1. **Baseline 处置**：39.md §4 要求 baseline 失败即 STOP。已 STOP 并报告；用户裁定改为「环境性旧失败 baseline manifest 闸门」继续（见 §11）——闸门强度不低于原纪律（0 new failures + 根因核验 + manifest 冻结）。
2. **`test_c1_director_production.py::test_C4` 工具面护栏** 22→28：护栏本义是「工具面变更必须有意识确认」；本变更有意识且逐工具注明出处（39.md §6），非降低判据。
3. **无 prompt rebuild 独立工具**：39.md §6 未将其列入必须补齐清单；multi 提交路径已有 `_ensure_multi_video_prompt` 自动重建（stale→重建→fail closed），遵循"最小正式工具面"原则（§5）未再加工具。
4. **`shot.bridge_create` 为同步长任务**（构造+提交+等待+写后验证一体的工具语义），与 `shot.generate_videos` 等长任务工具同型；服务层（构造/seed/守卫）与工具层（等待/取消）分离，均可确定性测试。
5. **Agent 名单修订（用户 2026-09-25 第二次定版，Phase A 收尾时）**：39.md 原文「可协作仅 4 Agent、scene_designer 禁用」由用户修订为——**完整生产协作 roster 5 Agent；两种校准模式默认 active set 4；Scene Agent 条件参与**（缺场景参考 / SIV stale / SceneState·Location 根因 / 需重新生成审批场景参考时 Producer 路由进入；修复后回 Director binding refresh→重生成→校准继续；**即便参与也不拿接缝/trace/裁定/桥工具**）。落点：Scene Agent 提示词条件参与段、Director 校准段场景侧根因句、Producer 编排段条件路由规则、`instance.py` 排除注释同步、名单锚定测试改版（`test_scene_designer_conditional_fifth_member`）。production_planner / shot_producer / continuity_reviewer / editor 禁用不变。
6. **Phase B replay 事件（§13.1 全记录）**：首轮试跑在真项目发生 canonical 污染 → 已按审计 before 值恢复并验证；此后 replay 一律沙盒隔离。Clean Review 意图路由劫持校准类 turn（v2 全量失败根因）→ harness 措辞修正 + 前置断言，路由豁免改进项记录在案（本阶段不改运行时路由）。

## 31. Blockers

无（Phase B 前置齐备：工具面 + 提示词 + trace 双落档能力 + 历史 run `cal_20260925_004933_6b30` 未被触碰）。

## 32. 新发现的 failure taxonomy（Phase B replay 实证）

1. **证据通道上限**：Agent 裁定质量 ≤ 证据通道质量——Vision 历史漏检（遮挡/端点/桥内帧检）会 1:1 复现为 Agent 的 missed_major（4 例）；人工「第二双眼睛」目前没有 Agent 等价物。
2. **过度触发倾向**：对镜内吸引子/端点问题，Agent 倾向升级为 major 并选择 retry/桥（unnecessary_retry 1 例、unnecessary_bridge 2 例）——「强先验吸引子 retry 无效」与「桥需要双端对齐」两条判据的应用需要更多 few-shot 锚定。
3. **Clean Review 意图路由的误劫持面**：校准/复盘/接缝类请求极易命中 `is_review_request`（「连续性」一词即触发），被改道为无写权限的审查会话。
4. **委派 envelope 的证据定位歧义**：不给 shot_id 映射时，Director 复核会拿相邻镜 canonical 对比。
5. **历史 attempt 的当前链测量混淆**：attempt 决策点上实测「现状接缝」（定版后）会与 attempt 当时证据冲突，harness 必须声明证据有效性边界。

## 33. 已稳定固化的规则（本阶段代码化清单）

- 桥时长 10s 下限（服务拒绝更短）——38 §18.4 用户指令 → `BRIDGE_MIN_DURATION_S`。
- 桥 transition_description 中间节拍 ≥3 段（两点一线拒绝）——M5B a2 实证 → `count_transition_beats`。
- 接缝指标 tail→first 与 end→end 分字段——CHECKPOINT_38 增补教训 → 连续性服务字段命名 + 提示词红线。
- seed 后零 refresh → `boundary_frame_stale` fail closed——38 桥时序坑 → 桥服务守卫（生成路径 gate 之外的第二道防线）。
- MAE 算法版本化（`gray128_bicubic_v1_phase38`）——历史基线只在同算法下有效。
- attempt 归档不覆盖——37A/38 双轮实践 → trace_upsert `archive_existing`。
- minor 不重试 / major 用户闸门 / 单次锚定重试 / 重试后重查负面约束——两轮实证 → Director/Producer 提示词纪律段。
- **5-Agent roster + 4-Agent 默认 active set + Scene Agent 条件参与**（用户 2026-09-25 定版）——三处提示词 + `_scene_agent_tools()` 白名单排除 + 名单锚定测试。
- **Agent 批量评测必须沙盒隔离**（Phase B 事故教训）——replay 沙盒项目 + 每决策点独立会话 + gold 防偷看。
- **thinking 随 Producer turn 快照透传**（用户指令）——`_delegate_specialist(thinking=...)`，不再硬编码 False。
- Phase B 新增待固化候选（≥2 次证据后再定）：桥内部帧检结论应落 trace（M5B_a1 误判根因）；校准类指令应显式携带 shot_id 映射；`is_review_request` 需要写意图豁免。

## 34. 仍需人工的规则

- major 的最终裁定（retry_generation 批准权）——保留用户。
- 桥验收的最终目检（帧文件路径随 `visual.sample_frames` 回执供人审）——Agent 证据 + 用户裁定。
- 项目视觉定义（ProjectVisualProfile）mutation——仍人类独占。
- 生产 Look 的实际修改（校准循环中只检查/建议）——独立资源修复任务走用户可见流程。

## 35. 与人工脚本阶段的等价性结论（Phase A+B 范围）

- **机制层等价已实证**：Shadow Replay 全程 23/23 决策点走通「证据→工具核实（seam_measure/trace_read/采样）→三档裁定→trace 落档」正式链路，0 次脚本手术、0 次 REST 直改。
- **纪律层等价成立**：minor 不重试（4/5 应用）、major 用户闸门（1/1，Producer 完整报告+等待批复）、零所有权违规、零级联跳过、attempt 归档与 trace 双落档全部成立。
- **决策层部分等价**：61% 决策类匹配；missed_major 全部可归因于证据通道（Vision 历史盲区 1:1 复现），过度触发（retry/桥）源于两条判据的应用粒度——对应改进项已入 §33 清单（few-shot 锚定 / 桥内帧检落 trace / 路由豁免）。
- **结果质量**：待 Phase C LIVE 由用户裁定。

---

## STOP（Phase A 边界）

按 39.md §35：Prompt + Tool integration + full regression 已完成 → **STOP**。未接入备用 API / Provider Router / 前端面板 / Agent 架构重排；未激活禁用身份；未改 Scene/Binding 核心数据模型；未触碰 37A/38 trace 与先例脚本（`_37_fable/` 零改动）。等待用户审查后进入 Phase B（Shadow Replay）。

## 19-28. LIVE story / 逐镜结果 / seam 表 / retry 表 / negative-constraint re-check / cascade / bridge 表 / identity / quota / provenance —— 待 Phase C（Canary 2 镜 → 放行 3-6 镜）
