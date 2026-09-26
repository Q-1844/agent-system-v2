# 全片尾帧接力连续性校准（Full-Story Relay Calibration）

## 目的（Purpose）

按尾帧接力（multi relay / inherit_previous）模式，把整条故事镜头链逐镜生成视频，
并在每一步用确定性测量 + 视觉证据 + 三档裁定保证跨镜头连续性（动作/时空/身份/叙事
无缝衔接），直到全链完成或真实 blocker 上报。已实证（Phase 37A：9 镜 11 生成；
Phase 38：9 镜 12 生成；Phase 39：Agent 集成）。

## 适用性（Applicability）

- 用户要求"整条故事生成视频 / 全片连续性校准 / 逐镜生成并检查连续性"；
- Producer 编排全片生产进入视频阶段；
- 任何以 multi relay 逐镜推进、需要接缝质量保证的生产任务。

不适用：单镜独立生成（无接力）、纯静态分镜图任务、纯审查讨论（走 Clean Review）。

## 参与者与角色（Participants）

完整生产协作 roster 是 5 个 Agent；本校准的**默认 active set 是 4 个**——
Scene Agent 按【场景依赖条件】参与，不是每轮必跑：

| Agent | 角色 | 职责 |
|---|---|---|
| Producer | coordinator | 编排节奏；none/minor 自动推进；major 立即停下升级用户（绝不代批重试）；配额感知（project.quota）；按段落向用户汇报检查点 |
| Director | primary_executor | 逐镜执行链路 owner：提交生成→等末帧→接缝测量→Vision 证据→综合判断→三档裁定→trace 记录；级联检查；接缝修复决策（retry/bridge）；场景根因上报 |
| Script Writer | contributor | 预编剧冻结（六要素）；minor 吸收设计；major 时设计 anchor patch（无 Shot 写权，patch 交 Director 落库） |
| Resource Agent (character_designer) | conditional | 链上身份一致性：approved Look 是生产身份真相，链根 identity reference 是链上身份锚；检查与建议，不静默改 production Look |
| Scene Agent (scene_designer) | conditional | 场景根因修场景真相（SIV stale/SceneState·Location 身份不符/缺场景参考）；不参与接缝测量/裁定/桥 |

## 总体工作流（Overall Workflow）

```
[Script Writer] 预编剧冻结（六要素：description/motion/expected_tail/
                duration_s/opening intent/negative constraints）
        ↓
[Director] 逐镜循环（禁止跳步）：
   1. 输入角色分类：每个视频的关键图作参考或作首尾帧皆可（系统都允许，
      作参考优先——不钉死画面，效果更好）。REFERENCE_ONLY（参考，带标记）/
      HARD_START_BOUNDARY（首帧槽）/ HARD_END_BOUNDARY（尾帧槽）/
      RELAY_START_BOUNDARY（接续：上镜真实尾帧进首帧槽）——按角色套口径：
      reference-only 姿态命中只记 pose_reference_mismatch_soft 诊断（不阻断
      不要求补参考不改故事）；硬首帧姿态冲突才阻断处置；接力由生成后接缝
      校验；负面约束写进 negative_prompt 字段
   2. 提交生成 shot.generate_videos（multi 显式 link_mode=multi，
      尾帧接力=video_reference_asset_id=="prev_tail"）
   3. 等末帧就绪（extract 自动托管）
   4. 接缝测量 calibration.seam_measure(shot_id)
   5. Vision 证据（尾帧 11 维评估 / visual.review_shot）——证据不是裁定
   6. 综合判断（叙事/情绪/状态/机位/负面约束 + MAE 经验区间）
   7. 三档裁定 → none / minor / major
   8. trace 落档 calibration.trace_upsert（裁定枚举字段非空——裁定是验收对象）；
      落档后 calibration.trace_read 复读本镜 link，确认裁定字段确实存在
        ↓
   none  → 按预编剧继续（自动推进）
   minor → accept+adapt：预编剧首句自然吸收，零文字修改零重试，自动继续
   major（大偏离）→ 按根因回正（剧本与故事段不变）：
           镜内失败=单次锚定重试（SW patch → Director 落库 → 完整复查）；
           尾态偏离=插曲方案（一或多镜、时长可长，从偏离尾帧接力逐步回正，
           自主推进）。仅真实 blocker（重试仍败/插曲无法回正/配额不足）
           停下等用户。
   级联 → 重生成链中任一镜必须声明 cascade risk 并检查下游接缝；
          下游断缝按语义选择顺延重生成（配额允许）或插桥
          （→ 关联技能 keyframes-bridge-repair）。
   场景根因 → scene_production_prerequisite 上报 → Producer 路由 Scene Agent
             修场景真相 → binding refresh → 重生成 → 校准继续。
        ↓
[全链完成] 检查点统计（none/minor/major 计数 + 当前接缝值）向用户汇报。
```

## 共享术语与关键数据（Shared Terminology）

- **expected_tail**：预编剧冻结的"本镜结束时画面应收在什么状态"（校准元数据）。
- **tail_to_first_mae**：上镜实际尾帧→本镜实际首帧的接缝测量值（128 灰度
  BICUBIC 逐像素绝对差均值，版本 `gray128_bicubic_v1_phase38`）——**接续性指标**。
  🔴 **唯一权威口径（39F 定版）**：生产流程的 seam 数值（Skill 判断 / Producer
  汇报 / trace / 最终报告）一律读 `calibration.seam_measure`（测量本体
  `src/storyboard/continuity_service.py`，确定性零 LLM）。禁止自己另写 ffmpeg
  抽帧参数、重新实现 resize/灰度/MAE、或用另一套脚本算一个数再来裁定；人工
  可做独立研究复测，但复测值不得写成 production trace 的 authoritative seam
  value。seam MAE 是证据，不直接决定 minor/major——叙事状态仍由视觉/故事判断。
- **end_to_end_mae**：本镜端点构图漂移——**另一个量，禁止与 tail_to_first_mae
  混写**（38 §17.3 教训：旧 mae_prev 量的是构图漂移不是接续性）。
- **MAE 经验区间（参照不是分类器）**：同场景正常接力 ≈2（近零带 1.7-2.4）；
  跨场景/构图切换 26-60；剧变构图 90+。超档即异常必须查明；但 MAE 不是
  none/minor/major 分类器——两个本应连续的镜头 MAE 只有 26、端点状态明显
  断裂也要修。
- **预编剧冻结**：生成开始后不根据生成结果偷偷改写已冻结计划；minor 的吸收
  靠预先设计，不靠事后改文本。
- **时长配方（实证两轮 20 attempts 零截断）**：静态/反应镜 5s；动态镜 8s；
  高运动/复杂交互 10s。
- **链根 identity reference**：链上身份锚（与 approved Look 三视图对照）。
- **cascade（级联）**：relay 在生成时刻快照上镜尾帧，上游重生成不自动传播；
  重生成链中任何已有下游镜头的镜，必须先声明 cascade risk，新尾帧就绪后
  必须检查下游接缝。
- **relay 适用性（先判 continuity intent）**：intentional cinematic cut
  （构图切换/insert/POV/reaction/跨场景切换）不强制 prev_tail——该镜用自身
  参考直生（REFERENCE_ONLY），trace 记 continuity_type=intentional_cut。
- **组（group，约定）**：Story segment / Scene 只可作为默认分组提示；组的
  真实连续性语义由镜头间是否承接上一条真实 `video_end_frame` 决定（系统不存
  group canonical、不强制）。因此：同段 + 接力 → 同组；同段 + intentional_cut
  → 可开新组；跨段 + 显式接力 → 技术上仍是连续组；跨段 + 新参考/新首帧 →
  新组。组内非首镜默认尾帧接力（上一镜真实尾帧进本镜首帧槽）；组首镜的图
  作参考优先（带标记）或作首帧；新组首镜用新参考图/新首帧图，不用上组尾帧
  作边界（上组尾帧需运镜衔接时作参考，带标记）。
- **插曲（interlude）**：尾态大偏离时插入的正式过渡镜（一或多镜、时长可比
  常规更长），从偏离尾帧接力、逐镜把故事拉回剧本轨道。剧本与故事段不变；
  插曲有完整六要素小冻结、正常接力与 trace，purpose=interlude，自主推进。
- **检查点节奏**：长故事按段落向用户汇报 none/minor/major 计数 + 当前接缝值
  ——汇报的计数与接缝值必须读取 calibration.trace_read 的 trace 事实，
  不转述 Director 的口头总结（39F 定版）。

## 决策模型（三档裁定）

裁定者是 **Director**（综合 seam 数据 + Vision 证据 + 叙事意图判断），
Producer 按档执行编排策略：

- **none**：实际尾帧与预期尾帧足够一致、叙事情绪正确 → 记 trace，按预编剧继续。
- **minor**：叙事/情绪命中但机位/端点/朝向/姿态阶段略偏 → **accept+adapt**，
  按预编剧继续，下一镜预编剧首句自然吸收。🔴 禁止为 minor 消耗重试/视频额度。
  已知不重试判据（实证）：机位偏但叙事命中（机位控制权在新镜 prompt+binding）；
  动作端点未落尾帧（下一镜吸收）；接触精度极限（多体接触）；强先验吸引子
  单独出现（smirk/play-bow，retry 无效）；道具链继承（读作叙事元素）。
  **窄决策锚定（39B 实证）**：非关键表情偏差（smirk/嘴角弧度/眼神）【单独】
  出现、且叙事/动作端点/地理/负面约束全部成立时 = minor accept+adapt——不得
  仅凭表情偏差升级为 retry；升级 major 必须有叙事/可见性/地理/负面约束级的
  独立依据。
- **major（大偏离）**：actual tail 与剧本预期偏离大、关键状态无法自然进入
  下一镜 → 按根因两路回正（剧本与故事段不变）：
  a) 镜内失败（没演成自己的任务）→ 单次锚定重试（SW patch → Director 落库
  重试 → 完整复查）；仍失败升级用户。
  b) 尾态偏离（镜合格但收在偏离位置）→ **插曲方案**：插入一或多个插曲镜
  （时长可更长）从偏离尾帧接力逐步回正——不重试、不硬切；插曲自主推进，
  检查点向用户汇报。真实 blocker（重试仍失败/插曲无法回正/配额不足）才
  停下等用户。

## 完成标准（Completion Criteria）

- 全链每镜都有 trace link（expected_tail/actual_tail/tail_to_first_mae/
  end_to_end_mae/vision_assessment/director_visual_observation/
  vision_vs_director_diff/deviation_level/root_cause/options_considered/
  selected_action/why/retry_or_adapt/negative_constraint_check/cascade_check
  + repair 决策链全文）；
- 每条链路裁定明确（none/minor/major）；major 有用户决策记录或停在升级点；
- **裁定枚举字段非空**：缺裁定字段的落档视为未完成（Phase D replay 实证：
  加载了方法 ≠ 执行完方法，zero-write 纠偏话语不是交付物）；
- 重试/重生成的旧 attempt 用 archive_existing=true 归档（历史不覆盖）；
- 下游接缝全部复查（级联无遗漏）；
- 检查点统计已按段落向用户汇报；
- **收尾自检（Skill-local，39F 定版——系统无任何 hook 兜底，这是唯一发现点）**：
  本镜即将交棒/收尾前，calibration.trace_read 复读本镜 link：裁定枚举字段
  缺失 → 立即补做裁定并 trace_upsert；视频已生成完成但本镜 trace 完全不存在
  → 同样立即补做 review/adjudication/trace，再交 Producer。未使用本 Skill 的
  普通 Director 操作不适用本条（零干扰）。

## 失败模型与事故教训（Failure Model）

1. **Vision 盲区（Vision 是证据不是裁定）**：情绪/地理误报 none；主体被遮挡
   50%+ 漏检；端点盲区；unexpected elements 漏检（空场景闯入人物/动物）；
   桥内跳变。Vision 与 Director 判断冲突时记入 vision_vs_director_diff，
   不机械服从。
2. **重试方差事故（M5-a4）**：重试后空村出现 5-6 个儿童击穿"空无一人"负面
   约束，Vision 完全漏检——**重试后必须完整复查**：重新接缝 → Vision →
   负面约束重查 → unexpected elements → identity → 环境 → 叙事状态 →
   实际尾帧；不能只看原问题是否消失。
3. **特写吸引子连击**：桥/近景尾帧是强机位先验 → 下镜需要尾态尺寸锚定
   （远景首句 + 全身收束句）对冲，否则连续特写（M4 a2/a3 实证，a4 破解）。
4. **回滚手术（M5-a4 实证）**：major 事故镜回滚 = mp4+末帧 jpg 备份恢复 +
   Asset.url 指回上一 attempt；trace 记拒档 attempt 为 major，不覆盖历史。
5. **Shadow Replay 纪律**：Agent 批量评测必须沙盒隔离、每决策点独立全新
   会话；委派 envelope 必须携带明确 shot_id 映射；纯审查意图走 Clean Review，
   有显式 mutation 意图（校准/落档/修复/重试/插桥）走正常 tool-loop。
6. **P39C Canary 实证（2026-09-25，亲眼看图归档 manual_review_20260925）+ P39D
   语义修正**：
   a) **输入角色误判是 39C/39D 两轮的核心教训**——39C s01 的真实缺陷是
   steering 缺位（负面约束没进 negative_prompt 字段）与冻结↔SIV 无人核对，
   不是"站姿参考必须先补"。把 REFERENCE_ONLY 的 Look 参考当硬首帧边界，
   会导致生成前错误阻断/错误要求补飞行参考/错误请用户改故事（39D 已纠正：
   参考姿态命中只记 pose_reference_mismatch_soft，生成后按 actual tail 裁定；
   仅反复实证 pose dominance 后才把补动作参考作为可选建议）；
   b) **冻结↔场景参考冲突无人核对**——SIV 含森林湖泊 vs 冻结"纯天空"，
   生成前无任何一道检查比对两者——bind 前逐项对照（语义真值轴，照常执行）；
   c) **像素接缝≠内容接缝**——接力镜钉在漂移尾帧上，MAE 照样低位，但语义链
   已断——接缝测量之外必须核对 expected_tail↔实际尾帧的语义一致性；
   d) **结构纪律**——段=地点/叙事单元、段内多镜（建立-发展-收束），禁止
   N 段×每段 1 镜的碎片结构（6×1 实证：段失去叙事容量）；
   e) **负面约束进字段**——冻结负面约束没进 negative_prompt 字段 = 生成端
   拿不到，只写 trace 无效。
