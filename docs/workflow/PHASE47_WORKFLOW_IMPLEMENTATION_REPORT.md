# Phase 47｜六 Agent 与现有 Skill 工作流落地与验收报告

> 日期：2026-10-05（跨 10-04/10-05 真机会话）· 分支 `feat/agent-v2-phase-d-golden-run`
> 基线：`ddb4258`（Phase 46 最终报告）→ 本阶段提交链见 §9
> 执行书：`详细流程文件/新阶段2/PHASE47_EXISTING_WORKFLOWS_IMPLEMENTATION_AND_ACCEPTANCE.md`
> 配套：`PHASE47_A批_基线入口表与工作流账本.md`（A 批）· `PHASE47_WORKFLOW_CATALOG.md`（C 批目录）· `PHASE47_TEST_EVIDENCE_INDEX.md`（48 情景证据索引）

## 1. 结论总览（四项分开，不互相替代）

| 验收项 | 结论 |
| --- | --- |
| ①六员正常职责生产 | **六员全部有真实职责内成功证据**（producer 计划/script_writer 新写与复用/character_designer 三视图+Look/scene_designer 地点+视图/director 分镜+视频+桥接+修复/assembly Sequence+render）。character_designer 与 assembly 的 Phase 46 阻塞已通过正常生产解决 |
| ②十类工作流 W00-W09 | **九类有真实运行证据**（W00-W08 全部 completed）；W09=D 批六员整链（见 §5 进行状态） |
| ③两个正式 Skill | relay-calibration：W05 真实 load（快照 hash+skillEvidence）+ 组内连续动作/帧检/trace 落档；keyframes-bridge-repair：W07 真实桥接生成。两 Skill 均有版本+内容 hash+请求轨迹 |
| ④六员整链新媒体与成片 | D 批《风后邮站》——真实图像/视频/旁白/音乐混音/可审成片（状态见 §5，未完成项如实列出） |
| ⑤恢复 | E 批真机演练（§6） |
| ⑥三轮复验 | F 批（§7） |

## 2. B批：交接语义与完成判定（已实现+真机验证）

实现位置：`src/workflow/outputs.py`（目标级完成校验）、`selection.py`（批准并选用）、
`adapter.py`（交接包 v2 + envelope 冻结输入）、`executor.py`（冻结/选用/守卫）。

| 语义 | 实现 | 真机证据 |
| --- | --- | --- |
| §4.1 character_designer 正常路径 | 目标与工具约束错配的最小引导（缺参考道具列缺项，不阻塞主目标）；profile 为图像生成合法前置（正式建项目 API 自带） | B 探针 S1：三视图 54.2s 真实生成+Look 参考图；C 探针 W02 模板链 completed |
| §4.2 目标级完成校验 | completion.outputs 按 kind 读真实项目事实（存在/图像/审批/可播放/渲染）；缺项诚实失败不推进下游 | B 探针 S0（scene created+hash）；W04 withImage 曾误报→定位 `_file_ok` 路径基准 bug→修复后 outputs_verified |
| §4.3 allow_verified_reuse | 服务端独立核验（归属/存在/hash/文件/审批）；reusedRefs 与 changedRefs 分开；spec 级 allowReuse 混合 | B 探针 S3（故事复用）+S5（复用合格 Look+补缺新 Look）；S4 负例（冒充被拒） |
| §4.4 交接包 v2 | workflowRef/scope/outputs(disposition/contentHash/reviewState)/reusedRefs/missingRequirements | 全部真机 run 的 handoff 字段（results JSON） |
| §4.5 逐节点输入冻结 | 派发前 freeze_node_inputs：上游 outputs 重读核对 hash/fileOk；stale 如实记录 | 单测 staleness 检测；真机 envelope 含冻结清单 |
| §4.6 审核与选用分清 | 审核通过=确认（不动指针）；「通过并选用」=用户卡片显式动作→服务端走既有用户入口同层服务；两步独立落账；失败幂等重试；选用产物进入交接链；内容快照防无关变更 | B2 S1b（Look 批准落盘）；浏览器选片（指针真实变更）；C 探针 W05/W08（video_version/sequence_render 选用） |
| §4.7 assembly 素材交接 | 派发前守卫（无 video_version 交接 fail-fast）；装配 agent 快照对齐门（不偷换素材）双保险 | C3 W08 首跑守卫拦截跨项目快照+不存在的版本 pin（诚实阻塞+诊断提案）；r3/r4 真实走通 |

## 3. C批：W00-W09 模板落地

`src/workflow/templates.py`（10 模板，真实 binding 边）+ 模板 API + 前端「从模板新建」。
**W00-W08 九类全部真实运行通过**（目录与 run 索引见 `PHASE47_WORKFLOW_CATALOG.md`）。

关键真机发现与修复（全部按第一处偏离定位）：

1. `SceneSystemDocument.location_views` → 实际字段 `views`（outputs.py）。
2. `_file_ok` 路径基准：项目内 local 路径存在仓库根相对形态 → 双基准解析。
3. 装配快照跨项目污染（新项目读 44A 全局快照）→ `_snapshot_load` 项目作用域 + `assembly.snapshot_freeze` 新增。
4. settlement 对账竞态（本 boot 活任务被 2s 轮询误判"重启中断"）→ boot 守卫。
5. 审核选用资源批准需 OCC revision + 事件循环内 await（`perform_selection_async`）。
6. `SaveDefinitionReq.definition` 可选化（缺省=已保存定义，与文档语义一致）。

## 4. 前端最小修改（§10）

从模板新建弹层（10 模板+参数+新身份）· completion 产物要求编辑器 · 审核 selection 配置 ·
审核卡片候选单选+通过并选用 · 运行面板输出/复用/缺项显示 · 诚实错误呈现。
浏览器走查（IAB，截图存证）：创建→运行观察→运行中刷新恢复→审核→选片→指针真实变更。

## 5. D批：新项目《风后邮站》六员真实生产——**W09 整链真实走通**

W09 模板链（14 节点 7 审核）· 终态 run `run_9cc683f2ce6c`（definition rev7）·
thinking=True · agnes-3.0-flash · **全部六员节点 success，停靠于成片审定（用户定版待处理）**。

执行链（全部真实模型/图像/视频）：producer 计划 ✓ → 编剧三段故事 ✓（复用核验跨 run 保持诚实）
→ 故事审核 ✓ → 角色三视图+Look 图像 ✓ → 资源审核+批准选用 ✓ → 场景+环境视图 ✓
→ 场景审核+街角 SceneInstanceView 审查/批准选用 ✓（新增 scene_view 选用类型：机器审查
needs_fix→按反馈重生成→pass→approve）→ 分镜 6 镜+绑定+图像 ✓ → 分镜审核+首帧选用 ✓
→ 导演视频 6 条真实生成（relay Skill 绑定+逐镜生成）✓ → 视频一审 ✓ → 视频二审+多镜
选用（items×6）✓ → 装配（快照冻结→Sequence 6 clips→6 句 TTS 旁白落盘→音乐→混音渲染）✓
→ **成片审定=保留给用户**。

§6.3 真实媒体核验（ffprobe/磁盘实证）：
- 角色三视图 front/side/back 可读；Look approved×2（图像文件在盘）。
- 6 镜分镜图可读；每镜 3 个可读视频版本（跨 run 生产历史完整保留）。
- 旁白 6 句 TTS 真实落盘（output/audio_imports/narration/*.wav + .raw.mp3）。
- 成片 `seq_0bbaaa6d/srender_ea44c7a9.mp4`：**h264 1280×704 + aac，36.7 秒**（简报
  30~60 秒目标内），6 clips 顺序=故事三段。
- `final_render=None`：用户定版待处理（不代办）。

D 批期间真机定位并修复（第一处偏离逐层）：
1. 分镜图 withImage 误报 → `_file_ok` 路径基准（repo 根相对）。
2. W09 恢复轮 require_new 死锁 → 恢复语义=改 allow_verified_reuse 新 run（§7.3 复用合格产物）。
3. 街角缺 SceneInstanceView（D 目标夹具缺口）→ 场景节点补视图（needs_fix 重生成循环真实走通）。
4. `scene_view` 选用类型新增（审查→实体级 OCC approve）。
5. shot 内容 hash 去掉 n_video_versions（下游进度≠分镜内容变更）。
6. 多选用 items 落交接包逐项展开（修占位符 bug）。
## 6. E批：真机恢复——**完成（语义全部验证）**

`_p47_probe_e.py` + `_p47_probe_e_r2.py`（真实杀断后端进程 + 持久化状态注入）：

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| R1 派发前窗口 | PASS | 派发意图无任务关联→诚实 needs_reconciliation→旧 attempt 保留→显式 continue 新 attempt 真实执行 |
| R2 turn 遗留 | PASS | run_31a0879f270c：运行态遗留 task→needs_reconciliation（错误含 task id）→continue 续跑完成；首轮暴露注入夹具绕过 runs_index→夹具修复后复验 |
| R4 人审等待 | PASS | review/材料/意见跨重启原样保留；通过后下游**恰好一次**派发（attempts=1） |
| R6 重复启动 | PASS | 同幂等键双启动→同 run（reused）；重复决策 alreadyDecided |
| R7 停止晚到 | PASS（带边界） | 停止后终态保持、下游不派发、指针零变更；边界：运行中停止的 turn 以 failed 标签落账（不破坏机制保证，如实记录） |

真实排障修复：探针 launcher 管道挂起（DEVNULL）、注入索引维护（save_run 同步 runs_index）。

## 7. F批：冻结与三轮独立复验——**完成**

冻结基线：`docs/workflow/PHASE47_FROZEN_BASELINE.json`（HEAD `d985783` + Skill hash
relay=4c529461…/bridge=eb23f971… + agnes-3.0-flash + p47_templates_v1）。

| 轮 | 项目/故事 | 结果 | 媒体核验 |
| --- | --- | --- | --- |
| F1 | 风铃山道《山铃回声》 | **run_181eff42300e 六员全 success**，停靠成片审定（用户定版待处理） | 6 帧 + 6 视频 + render done（seq_80326f20/srender_cf03b1ff 落盘）+ 6 句 TTS |
| F2 | 夜邮刺猬《夜邮灯塔》 | **run_13e741c1c934 六员全 success**，停靠成片审定 | 6 帧 + 6 视频 + render done（srender_c57fb8be）+ 6 句 TTS |
| F3 | 山雀夜航《山雀夜航》（**复用轮**） | **run_fb5e2c27eac8 六员全 success**（含真实中断），停靠成片审定 | 6 帧 + 8 视频 + render done（srender_8fb05fe6）+ 7 句 TTS |

F3 复用与中断（§8.2 要求逐项）：
- 合法既有资源复用：F1 已批准 Look 经共享库发布（look_7118f703805e）→ F3 角色
  节点 `resource.library.import` 导入；**复用溯源写入 canonical**（look.origin=
  shared_library/shared_resource_id）——不把复用冒充新建。
- 真实中断：装配节点运行中硬杀后端→重启→诚实 needs_reconciliation（错误含
  确切 task id）→显式 continue→装配重试成功。
- 逐轮独立：每轮新项目/新会话/新故事（F3 复用资产如实标注）；不拼凑不同基线片段。

F 轮过程真实记录（不从报告删除）：F2 场景 agent 两轮只建 LocationView（工具选择偏差）
→ 目标点名 `scene.state_view.prepare` 后第三次正确；F2 首驱动被杀留下僵尸 run 与后续
run 竞态（接管完成）；F3 首轮视频 4/6 诚实失败→复用续做。每轮第一候选可用率等量化
口径见 evidence index §5（Phase48 A批已按真实文件补齐该节）。

## 8. 全量回归

| 时点 | 结果 |
| --- | --- |
| B 批完成后 | 2655 passed, 2 failed（cancel_race/autopilot——全量负载偶发，隔离复验均过，与本轮无关） |
| C 批修复后 | 2657 passed, 1 failed（同上偶发项） |

## 9. 提交链

> Phase48 A批修正：原占位行替换为真实提交 hash（git log ddb4258..d985783）。

| 提交 | 内容 |
| --- | --- |
| `3b2fe68` | （B/C 批）目标级完成校验+复用+交接包 v2+审核选用+assembly 守卫+10 模板+前端+34 引擎测试 |
| `0fe62c8` | （修复三连）选用 async/OCC/多选用+交接链 |
| `20a4b9e` | （assembly+settlement）快照项目作用域+snapshot_freeze+boot 守卫 |
| `acdca31` | （docs）A批基线表/工作流目录/证据索引/主报告（D 批结果） |
| `692896c` | （test）真机探针脚本入库 |
| `7cc570d` | （test）E 批真机恢复演练 R1/R2/R4/R6/R7 |
| `d985783` | （chore）F 批冻结前收尾 → 冻结基线 `ab03293` |

## 10. 逐项回答（执行书 §12 主报告必答）

1. **六员正常职责生产**：全部通过。producer（计划+守卫）/script_writer（新写+复用核验）/
   character_designer（三视图+Look 图像+共享库导入复用）/scene_designer（地点+实例视图+
   needs_fix 重生成循环）/director（分镜图+视频+桥接+修复）/assembly（快照冻结+Sequence+
   TTS+混音渲染）——各有真实职责内产物与回执。
2. **十类工作流**：W00-W09 **全部正式可用**（模板创建→真机 run→真实产物）；
   无"仅定义"项。W06/W07 在隔离夹具项目验证（C3），W09 经 D 批+F 批四轮全链验证。
3. **两个 Skill**：均实际加载并完成任务（relay：W05/W09 视频链快照 hash+load 事件+
   trace 落档；bridge：W07 真实桥接生成+双端验收）。
4. **六员整链**：D 批《风后邮站》+ F1《山铃回声》+ F2《夜邮灯塔》+ F3《山雀夜航》
   四轮全链——新媒体（图像/视频/TTS/音乐混音）与可审成片全部落盘；
   **未完成的用户决定**：四部成片的 final 定版（成片审定关卡全部保留给用户）。
5. **恢复**：自动完成=对账状态判定/审核等待保持/已完成节点不重做/幂等；
   需显式动作=needs_reconciliation 后的页面「继续」（错误含确切 task id）；
   运行中停止的 turn 以 failed 标签落账（边界如实记录）。
6. **三轮复验**：全部执行（§7）；冻结基线 d985783；失败样本与过程记录保留（F2 场景
   工具偏差两轮、僵尸 run 竞态、F3 视频 4/6）。
7. **剩余阻塞**：无工程阻塞。**待用户**：四部成片的审美接受与 final 定版；
   P47-42 的媒体待评不写 PASS。以后拓展：并行/条件分支/自动返工（按本轮真实
   重复需求排序：场景视图质量的自动重生成循环最高频）。

## 11. 未完成与边界（如实）

- 四部成片可审未定版——工程验收通过，**媒体与定版待用户评审**。
- 运行中停止的节点标签语义（failed vs stopped）——机制保证不破坏，语义修正留待后续。
- `resource.library.publish_character` 视图文件校验路径缺口（Look 复用路径不受影响，
  如实记录）。
- 前端 vue-tsc 历史 61 类型错误（非本轮引入）；workflow 组件 0 新增。
- 48 情景矩阵中 P47-42 的媒体验收=工程核验通过、用户审美待评。

## 12. 交付物索引

| 文件 | 内容 |
| --- | --- |
| 本报告 | 主报告与分别判定的完成结论 |
| `PHASE47_A批_基线入口表与工作流账本.md` | 基线/入口表/账本初版 |
| `PHASE47_WORKFLOW_CATALOG.md` | W00-W09 定义来源/参数/前置/run 索引 |
| `PHASE47_TEST_EVIDENCE_INDEX.md` | 48 情景结果+环境事件+run 索引 |
| `PHASE47_FROZEN_BASELINE.json` | F 批冻结基线 |
| `_p47_probe_*.py`（已入库） | 全部真机探针/驱动/恢复演练脚本 |
| `projects/{风后邮站,风铃山道,夜邮刺猬,山雀夜航,P47_*}` | 真实媒体资产与 run 记录 |

## 13. 用户反馈与后续指引（2026-10-05，验收后第一轮用户意见）

> 用户看过四部成片与测试视频后提出三点；**本轮不改代码**，以下如实记录并给出
> 交给后续专门 agent 的落实依据。

### 13.1 成片音频政策——用户判断成立（附实测）

- 用户观察：所有测试视频都没有背景音乐 → agnes 视频模型变强了，成片可以
  **直接原视频加旁白，不用静音**。
- 本轮实测佐证：抽查源视频音轨（aac 存在）综合响度 **-52.9 LUFS**
  （广播目标 -16~-24 LUFS；数字静音通常 ≤-70）——近似无声：无音乐、无人声，
  仅极低底噪。用户判断与实测一致。
- 落实建议（交后续 agent）：成片混音可改为「原视频轨直混旁白」；注意 -52.9
  非数字纯静音，直混时可保留现有 `render_profile.audio=silent`（等效）或做一次
  响度对齐/低切，避免底噪在旁白间隙可闻。本轮 4 部成片按当时纪律仍为静音政策，
  效果与直混基本等价。

### 13.2 接续策略——本轮实证与用户调研一致

- 用户观察：成片衔接尚可，但大多没做接续；外部调研显示多数视频模型接续很差；
  思路=找本模型最合适的思路，不强推接续或首尾帧生成。
- 本轮实证（与用户判断一致）：
  - D/F 四轮全部视频生产中，导演均自主判定 **i2v independent**
    （`source_shot_id` 全 null，无接力链、无重生成级联）；镜头间换场判定为
    intentional hard cut，桥接 Skill 明确不触发（「双端证据」门槛未满足）。
  - 接续类生成仅在 W07 夹具场景真实触发一次（keyframes 桥，双端钉死）。
  - 独立 i2v + 硬切的 6 镜链在四轮中全部产出合格成片，衔接由装配侧保证。
- 生产策略定调（记录）：**不强推接力/首尾帧**；relay 的真实收益（身份/构图
  连续性）与成本（需上游 approved 帧、锚定误差风险）留待后续对本模型专项实测，
  以实测数据决定是否/何时启用，而非作为默认纪律。

### 13.3 执行时长归因（28h20m 拆解）

视频本身不长，总时长构成大致如下：

| 构成 | 量级 | 说明 |
| --- | --- | --- |
| 真实生成时间 | ~8-10h | 视频每条 2-5 min（JobQueue 串行 1 RPM）× 四轮 ~40 条；图像每张 30-60s；TTS/混音渲染 |
| 诚实失败驱动的重跑 | ~8h | **确实是"边纠错边执行"**——这正是验收纪律的设计：首轮真实暴露缺陷（快照作用域/路径基准/场景视图缺口/settlement 竞态等 7 项）→ 按第一处偏离定位修复 → 复验。D 批 5 次 run、F2 4 次 run 多由此来 |
| 审核停靠等待 | ~2h | 7 道审核 × 4 轮的 waiting_human 停靠与驱动轮询间隔 |
| 工程项 | ~3h | 全量回归 3 次（每次 ~20min）、35+17 引擎测试、文档、浏览器走查 |
| 驱动与观测开销 | 其余 | 探针轮询间隔、监控 sleep |

- 熟练后基准：**一轮干净六员链约 1.5-2 小时**（F1 实测 ~3h 含一次驱动断点；
  缺陷修复后的 F3 ~2h 且含真实中断演练）。首轮的纠错成本是前置投入——
  后续 agent 沿用本轮修复后的代码与模板，不再重复支付。
- 用户指示：**本轮代码不再改动**，音频政策简化与接续专项实测交后续专门 agent。
