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
   1. 提交生成 shot.generate_videos（multi 显式 link_mode=multi，
      尾帧接力=video_reference_asset_id=="prev_tail"）
   2. 等末帧就绪（extract 自动托管）
   3. 接缝测量 calibration.seam_measure(shot_id)
   4. Vision 证据（尾帧 11 维评估 / visual.review_shot）——证据不是裁定
   5. 综合判断（叙事/情绪/状态/机位/负面约束 + MAE 经验区间）
   6. 三档裁定 → none / minor / major
   7. trace 落档 calibration.trace_upsert（裁定是验收对象）
        ↓
   none  → 按预编剧继续（自动推进）
   minor → accept+adapt：预编剧首句自然吸收，零文字修改零重试，自动继续
   major → 🔴 立即停下：Producer 向用户完整报告并等待；用户批准 retry_generation
           后 → Script Writer 设计 anchor patch → Director 执行【单次】锚定重试
           → 完整复查；仍 major → 再次升级用户，不得第二次自行重试。
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
- **检查点节奏**：长故事按段落向用户汇报 none/minor/major 计数 + 当前接缝值。

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
- **major**：叙事/情绪与剧本相反、地理丢失、主体不可见或严重遮挡、关键状态
  无法自然进入下一镜、严重 unexpected element → **立即停下等用户**。
  Producer 输出完整报告（发生了什么/为什么 major/Vision 与 Director 观察及
  冲突/当前 seam/建议修复 retry 或 bridge/预计 cascade 风险/预计新增视频消耗/
  配额实时读数）——**绝不代替用户批准重试**。

## 完成标准（Completion Criteria）

- 全链每镜都有 trace link（expected_tail/actual_tail/tail_to_first_mae/
  end_to_end_mae/vision_assessment/director_visual_observation/
  vision_vs_director_diff/deviation_level/root_cause/options_considered/
  selected_action/why/retry_or_adapt/negative_constraint_check/cascade_check
  + repair 决策链全文）；
- 每条链路裁定明确（none/minor/major）；major 有用户决策记录或停在升级点；
- 重试/重生成的旧 attempt 用 archive_existing=true 归档（历史不覆盖）；
- 下游接缝全部复查（级联无遗漏）；
- 检查点统计已按段落向用户汇报。

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
