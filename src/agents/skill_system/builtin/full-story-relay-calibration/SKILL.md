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
   1. 输入角色分类（起步路径先过「起步/再锚定决策入口」：按叙事关系→端点
      质量→槽位/能力选路径 a-f，能力事实读 references/capability-facts.md）：
      每个视频的关键图作参考或作首尾帧皆可（系统都允许，
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

## 起步/再锚定决策入口（每镜适用——不只组首；本 Skill 唯一路径决策点）

每镜生成前按【叙事关系 → 上游端点质量 → 可用槽位/能力】顺序决策，逐镜读取：
批准 Look、场景/SIV、Shot 意图（description/motion/expected_tail）、上游真实
已选视频版本（final_video 与其来源版本）、上游真实尾帧、依赖版本
（binding readiness / generation_basis）。**不是只有组首做身份检查**；不用
「接了几镜、组号、参考数组位置」替代判断。

**上游端点质量分级**：合格（身份特征与动作可读且正确——特征以该角色 approved
Look 为准，如丹顶鹤=黑颈/红冠/黑次级飞羽及腿）；身份不可读（远景小主体等，
环境/动作仍可用）；已画错（身份/状态明确错误）；版本过期（引用的
end_source_version ≠ 上游当前 final_video）。

**路径表**（逐路径能力六状态见 `references/capability-facts.md`，生成前必读）：

- **a. 真实尾帧作视频首帧硬边界**（inherit_previous → i2v/keyframes 首槽）：
  端点画面合格且动作确需继承、编译器支持时选；这是起点约束，仍须检查实际
  第 0 帧与中段。
- **b. 裸尾帧作单图语义参考**（multi → 2.5 reference，单图 prev_tail）：不锁
  首帧、无独立 Look——端点身份不可读/身份风险高时避免；🔴 不是硬接，汇报与
  trace 不得称 hard boundary / relay_start_boundary 硬边界。
- **c. 融合首帧起步**：Look＋指定真实尾帧（必要时加批准场景图）先经图片模型
  合成新首帧，审合成图的身份/空间/动作起点，再用受支持的首帧模式
  （i2v/keyframes）制视频。适用：远景身份不可读、下一镜转中近景、上镜环境/
  动作仍有用。这是保留语境的重新构图/正常切镜，不是逐像素尾帧接续；
  s02_c02（2026-09-27 expB）为正例，**不升级成所有镜头的默认值**。
- **d. Look 主导的独立首帧/新镜头**：新故事段、正常切镜、换场景、上镜端点
  已画错；组内必要时也能重建，须写清剧情代价。
- **e. Look＋尾帧双参考接力**（relay 镜身份/画风再锚定）：Binding 层已接线
  （canonical 哨兵 `video_reference_asset_id="prev_tail_look"`，agent 经
  shot.update 显式设置；纯 relay `prev_tail` 行为不变）。请求=新尾帧（Picture 1，
  衔接动作/空间）＋本镜 hero Look（Picture 2，再锚定身份/画风），prompt 自动附
  `<Picture N>` 指称段。真机已验证（探针+端到端链，见 capability-facts）；
  **已知权衡：Look 锚定与尾帧锚定竞争，端点帧里已冻结的偏差特征（如斑纹侧别）
  可能随尾帧胜出——所以根帧有错先修根帧，双参考只接力合格端点**。解析不到
  hero Look=结构化拒绝（fail closed），不静默降级。
- **f. 桥接修复**：仅成片接缝审查后考虑，需两个端点自身合格且值得保留、确有
  叙事/视觉断裂；普通电影切镜不插桥，错误端点先修（→ 关联技能
  keyframes-bridge-repair）。

**组内再锚定触发器**（任一命中即评估；目标是让批准身份真正进入图片或视频
请求，而非机械占据"第一张参考图"）：远景转中近景；实际编译请求丢失 Look
（sent/used/dropped refs 证据）；数镜接力后身份漂移累积。

**Phase48 受控对照结论（路径选择的实证权重；2026-10-06，4 条件 × 4 法 × 3
重复 = 48 格，45 生成 + 3 正确拒绝；证据：PHASE48_C_REPORT.md /
`_p48_c_evidence.json`）**——路径 a-f 的选择按镜型取权重，不预设任何方法最优：

- **同动作继续、端点合格且同机位**：a（真实尾帧硬边界）/ c（融合重建）/
  d（Look 主导独立首帧）受控等价（A 条件 action_ok 12/12）——按制作意图选，
  不为"连续性"强行接力；正常切镜不是缺陷。
- **跨景别（远景→中近景）**：a 会把构图钉死在上游远景尺度（B 条件 a 的
  action_ok 1/3：近景计划从远景尾帧难以表达）——优先 c/d/e；本镜首帧必须
  按目标景别重建或新构图。
- **大机位/场景构图变化**：双端 keyframes 钉死不稳（C 条件 0/3，含分身伪影；
  且计划结束帧图像本身伪影率 2/12）——单端锚定（a 硬边界+新构图 prompt、
  或 c 重建）更稳；桥只按 f 的窄判据进。
- **上游尾帧身份已画错**：路径决策前先做身份检查（Look vs 尾帧 Vision 比对
  即可起步）；不合格端点**拒绝作硬边界**（受控 3/3 正确拒绝），走 d/c 重建
  （Look 主导压制错误身份，受控 9/9 身份恢复）。
- **双帧/keyframes 类请求的结束帧必须先审图**（伪影率实证 ~17%）：Vision
  伪影检查不过的结束帧不得进请求（→ 关联技能 keyframes-bridge-repair 失败
  模型 6）。

**新组首镜显式决定是否跨组延续**：`vref=prev_tail` 残留会按全片顺序自动接
上一组末镜——与本镜意图不符时先清理/显式配置并 dry-run 核对（系统对跨段
接力有 `prev_tail_cross_segment_relay` 诊断，不静默但也不阻断）。跨场景使用
reference_previous 若被同场景 gate 拒绝，选合法替代路径（c/d 或显式跨段接力）
或报能力缺口，不绕 truth gate。

**first_frame 槽语义（硬边界 ≠ multi 语义参考）**：i2v/keyframes 实际消费的
首帧是硬边界；multi 接力同步进槽的图带 `slot_semantics=multi_reference_only`
标记，改 i2v 会被 `first_frame_slot_not_hard_boundary` 拦下——先重新确认本镜
应消费的首帧（inherit_previous 或重新生成，如路径 c）。上游重选新版本后，
下游旧 end_source_version 标记待复核：比较下游真实输入与新上游状态，合格镜
可保留，过期引用不得沿用，不机械全重做。

**生成后视觉门槛**：先查真实编译请求（sent/used/dropped refs、input_visuals、
model/mode、来源版本），再看首/中/尾帧与与前镜剪辑：身份（approved 特征
逐项）、身体、场景、动作、实际尾态。工具成功≠视觉合格。身份合格但剧情尾态
偏离（如提前触水）＝「视觉合格、剧情尾态偏离」——由剧情/镜头 owner 评估
收束重试或接受新尾态；只有接受后才循 canonical 流程更新预期与下游起点，
🔴 不得悄悄把 expected_tail 改成已发生的动作。

**状态不得混淆**：实验候选、用户积极反馈、正式版本选定、故事事实调整、
下游批准是五个不同事件，各走各的记录与批准。

### 定版生产判据（Phase 42 沉淀，生成前过一遍；方法细节与反例见
`references/production-recipes.md`，能力事实见 capability-facts）

1. **特写/中近景身份锚定检查**（适用：shot_size 命中 CU/ECU/MCU 家族的身份
   敏感镜）。动作：生成前核对**真实编译请求**（receipt 的 sent/used/dropped
   refs）确认 Look 进了请求——系统 auto 已默认 crop 档（头肩派生，2026-09-30
   定版）；被 drop 必有 reason（如角度驱动 OTS/insert/detail），此时 identity
   只由 prompt 文本承载。禁用：特写镜显式 keep（全身立绘会锚死构图）——除非
   用户明确要求。失败修复方向：成片身份/画风偏差 → 显式 human_reference_mode
   =crop 重生成根帧；构图已被拉宽 → 去掉场景图同发或加构图条款。
2. **道具入画绑定检查**（适用：画面中出现的剧情道具）。动作：Binding 必须
   绑 prop（带视觉状态注记，如「未点亮/裂纹玻璃罩」），缺 → 先走正式 Binding
   入口补齐再生产。禁用：不绑 prop 靠 prompt 文本硬写道具样式。失败修复方向：
   道具样式漂移 → 补绑定+状态注记后重生成，不在 prompt 堆形容词。
3. **根帧先修，再接力**（适用：任何接力链）。动作：链上出现偏差时先定位根帧
   （第一个 Look 未进请求/参考错误的镜）并重生成根帧，再沿链用真实尾帧
   （动作/空间）+Look（身份/画风）双参考推进。**端点有两层：根帧与尾帧——
   尾帧偏差同样沿链传播且换种子无效**（43.md 实证：根帧侧别✓但 i2v 过程中
   标志特征漂移，尾帧镜像→下游全部候选镜像）；接力前核对真实尾帧，端点
   （根帧或尾帧）有错先修端点或换合格端点，不硬接力。禁用：同一连续链内
   混用人物配方（一批视频两个形象）——确需切换时记录原因并检查相邻镜观感。
4. **修法按问题类型选择**（空间 vs 身份/画风，端点修复矩阵——细节与实测
   证据见 production-recipes）：仅空间错 → 文本强化+保留 relay 或硬首帧转移；
   身份/画风漂移（空间对）→ 双参考接力逐镜回正；身份/画风要求最高且允许
   重构图 → 真解除 relay 独立重生（空间自由度过大，须先有场景先验——缺场景
   先停下走 Scene Agent 正式入口补场景，注意场景切换镜头跟随）。
5. **动作-时长-场景容量一起估**（适用：动态镜）。动作：按镜时长估动作容量，
   快速动作（奔跑等）少加多余动作（抢速度感、易不自然）。失败修复方向：成片
   场景被凭空延伸 → 缩短时长 / 增加合理的后续动作填满时长 / 两者并用；成片
   凭空发明未建立的场景元素（门/凳等）→ 场景先验进请求（三参考哨兵）+文本
   钉死规格，且**元素出现方式二选一**（44A 终选沉淀，细节见 production-recipes）：
   **镜头跟随·盲区揭露**（默认求稳——首帧保持接力连续性、场景零变动，运动
   指向首帧盲区，镜头跟随平移，元素在新显露的空间出现——未知区域可创建，
   接缝保持接力带）；**融合首帧·全景**（用户要求全景或剧本需要建立镜时——
   合成首帧把元素与人物状态一并成立，元素置于人物身后背景为基准构图，代价
   =场景切换入缝）。**约束要留动作空间**：规格钉死过强会吃掉剧情动作（43.md 实证：
   门规格钉死后开门动作消失）——修完元素必须复查动作仍在。修复不完全的
   情形如实记录。动作描述密度：1s 分拍时间戳实证可用；0.5s 细分拍无可感知
   收益（用户终审），不必更细。
6. **候选与裁决**（适用：正式成片）。动作：同配方足量候选供逐镜挑选（跨种子
   方差真实存在——某种子出多人/瑕疵是数据点不是废配方）。禁用：把某次
   「N 选 M 全优」写成模型保证；跳过用户对人物/道具/场景/动作/整体观感的
   最终判断。

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
  高运动/复杂交互 10s。**时长不是自由参数——与动作量、场景容量一起估**：
  时长超过场景/动作容量 → 模型凭空延伸场景（新地址凭空出现让人物继续跑）；
  时长内动作不足 → 移速不自然。修复=减秒数 / 增加后续动作 / 并用（判据 5）。
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
  新组。**组内怎么接、组首怎么起步，一律走上方「起步/再锚定决策入口」按
  端点质量选路径（接力默认值只适用于端点合格的场景；远景身份不可读优先
  融合首帧路径 c，已画错端点禁止接）**。注意 multi 接力（2.5-flash reference）
  的 provider 事实是语义参考（不锁首帧，参考图上限 5 张）——「上一镜真实尾帧
  进本镜首帧槽」是生成后系统的如实槽位同步，不是 provider 级硬边界。
- **插曲（interlude）**：尾态大偏离时插入的正式过渡镜（一或多镜、时长可比
  常规更长），从偏离尾帧接力、逐镜把故事拉回剧本轨道。剧本与故事段不变；
  插曲有完整六要素小冻结、正常接力与 trace，purpose=interlude，自主推进。
- **检查点节奏**：长故事按段落向用户汇报 none/minor/major 计数 + 当前接缝值
  ——汇报的计数与接缝值必须读取 calibration.trace_read 的 trace 事实，
  不转述 Director 的口头总结（39F 定版）。

## 审核三查判据（协作流程 v2，2026-10-11 用户定版）

**审核（自动或人工）只负责三件事，面不能宽**：①人物模样/身份有没有出错；
②环境跨镜有没有不一致；③有没有大的其它不合理（人数爆炸、叙事断裂、衔接失败）。
**资产「长成了什么样子」不归审核管**——像海怪的样貌只要没变就可以，不非要控制
它的样貌；固定资产不变、全流程没有变、衔接性完好，结果就是完好的。分镜文本是
拍摄蓝图，不是逐帧验收标准（判据细节与错杀反例见 production-recipes §8）。
小瑕疵（背景元素一闪类）记录不拦。同批混用配方始终禁止。

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
