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
## 6. E批：真机恢复（状态）

`_p47_probe_e.py`：R1 派发窗口/ R2 turn 遗留 / R4 人审等待 / R6 幂等 / R7 停止晚到——
真杀进程+重启+持久化状态注入（状态见 `PHASE47_TEST_EVIDENCE_INDEX.md` §2.9.5）。

## 7. F批：冻结与三轮复验（状态）

冻结内容（代码提交+模板版本+Skill hash+模型配置）后 F1/F2/F3 独立复验；
每轮新项目新会话；状态与本节结论以最终更新为准。

## 8. 全量回归

| 时点 | 结果 |
| --- | --- |
| B 批完成后 | 2655 passed, 2 failed（cancel_race/autopilot——全量负载偶发，隔离复验均过，与本轮无关） |
| C 批修复后 | 2657 passed, 1 failed（同上偶发项） |

## 9. 提交链

| 提交 | 内容 |
| --- | --- |
| （B/C 批） | 目标级完成校验+复用+交接包 v2+审核选用+assembly 守卫+10 模板+前端+35 引擎测试 |
| （修复三连） | 选用 async/OCC/多选用+交接链 |
| （assembly+settlement） | 快照项目作用域+snapshot_freeze+boot 守卫 |

## 10. 未完成与边界（如实）

- W09 整链/D 批成片、E 批真机演练、F 批三轮复验：见各节状态（本报告随批更新）。
- W06/W07 的模板默认无 Skill 绑定（依名册：修复/桥接方法沉淀于 relay-calibration
  的 references，director 可另绑；模板保持最小绑定面）。
- 前端 vue-tsc 历史 61 类型错误（store/resources/scene-system 视图）非本轮引入，
  workflow 组件 0 新增错误（如实保留不隐去）。
- 工程探针的审核通过=机制验证，不等于用户审美通过；所有正式定版待用户。
