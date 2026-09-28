# PHASE39E 修改审查报告 —— 首帧槽语义 / 插曲制 / 系统与 Skill 分工

日期：2026-09-26 ｜ 范围：用户三轮反馈定版后的全部系统与 skill 修改 ｜ 供用户逐项审查
commits：`ba7215a`(预检+负面约束) → `a954750`(姿态词表) → `89ab534`/`2076e9b`(schema 补口) → `b1f2a81`(角色语义定版) → `542ca38`(input_visuals+前端标记+插曲制定稿) → `349418e`(裁定完整性警告) → `3cadffc` → `336a788`(同步规则收紧)

---

## 0. 一句话

你的"槽位模型"（槽内=生成该视频实际用的首尾帧或参考图，参考要标记，人物图不填）已由系统侧（如实记录+显示+首帧槽同步）与 skill 侧（写清保障效果的做法，不做系统强制）分工实现；插曲制取代了"major 一律重试"的旧决策模型。全部修改经确定性测试 + 两次全量回归（2425 passed / 39 挂账失败，零新增）。

## 1. 定版依据 —— 你的原话 → 实现映射

| 你的要求（原话要点） | 实现落点 |
|---|---|
| "如果有参考图而且它又是首帧或者尾帧，那么它应该填入槽内" | 首帧槽/尾帧槽照常显示（i2v/keyframes 路径不变）；`input_visuals` 记 hard_start/hard_end_boundary |
| "如果参考图只做视频参考并不是首尾帧，也要填入槽内并做一个很小的标记（比如换一下框颜色）" | `input_visuals` 记 reference_only；前端版本条缩略图**琥珀框**+"参考"角标；接力=蓝框"首·接力"；硬边界=绿框 |
| "槽内只能填最重要的那个参考图，如果是人物图的话就不用填" | multi 单参考槽只装一张关键图；首帧槽同步跳过 character_look（人物图由 Binding 携带） |
| "参考图不填入系统中就看不到了" | 每个视频版本持久化 `input_visuals`（生成时刻由 adapter 权威构造，API 全量透出） |
| "第二个视频的首帧槽应该填入第一个视频的尾帧" | 生成完成后 `_sync_first_frame_slot` 自动把首帧槽同步为实际开场输入（见 §2.3） |
| "如果下个视频的首帧不是上个视频的尾帧，那么就是在新的组内；新组槽填新参考图或首帧图" | skill §0.3 组约定（轻量、不强制）；跨组首镜不设 prev_tail，按组首镜处理 |
| "尾帧偏离小→下一镜加动作拉回；偏离大→加情节（几镜、可长）拉回；剧本与段不变" | skill §4 三档裁定改版：major 按根因分两路——镜内失败→单次锚定重试；尾态偏离→**插曲制**（不重试） |
| "组之间不要做严格系统要求" | 系统零强制：同步/显示只如实记录；分组核对只在 skill 指导层（Producer 编排时按约定核对） |

## 2. 系统侧修改（六项）

### 2.1 `input_visuals` 权威记录（`542ca38`）
- 每个 VideoVersion 持久化 `input_visuals: [{slot, semantic_role, url, source_type, source_shot_id, …}]`——**生成该视频实际消费的输入图清单**。
- 构造时机：视频 handler 内、以 adapter 的 semantic_roles 为权威（BOUND 路径）；legacy/恢复路径尽力标注（诚实留空）。
- 四种 semantic_role：`hard_start_boundary`（绿）/ `hard_end_boundary`（绿）/ `relay_start_boundary`（蓝）/ `reference_only`（琥珀）。
- API 全量透出；这是"槽内是什么"的权威事实源，Agent 汇报不再能覆盖它。

### 2.2 前端标记（`542ca38`，`Workbench.vue`）
- 分镜工作台版本条：每个视频下显示输入缩略图排，**琥珀框=参考**、蓝框=接力边界、绿框=硬边界，角标文字"参考 / 首·接力 / 尾帧 / 首帧"。
- 旧数据无 input_visuals 的诚实留空（不伪造标记）。

### 2.3 首帧槽如实同步（`3cadffc` → `336a788` 收紧，`routers/shots.py::_sync_first_frame_slot`）
视频完成后按 `input_visuals` 角色分四类处理：
| 该视频实际开场输入 | 首帧槽行为 |
|---|---|
| relay_start_boundary（组内接续：上镜尾帧进槽） | 同步为上镜尾帧（解析本地路径），批次标记 **[接力边界]** |
| reference_only 且 source=上镜尾帧（尾帧作参考） | 同步为尾帧，标记 **[参考]**（琥珀语义） |
| reference_only 且 source=人物图（character_look） | **不同步**（人物图由 Binding 携带，不进槽） |
| hard_start_boundary | 不同步（槽内已是实际输入） |
- 幂等（槽内已是该图则跳过）；追加批次保留分镜图历史；同步异常不影响视频成功。
- P40A s01_c02 已回填：首帧槽现在显示 s01_c01 的尾帧（你反馈的问题当场修正）。
- **同步触发范围（你 09-26 定版）**：只在"接续"或"上镜尾帧作参考"两种情况同步；人物图不填；硬首帧本就对齐。

### 2.4 trace 落档的三条软提示（`349418e` + `336a788`，skill 绑定设计）
trace_upsert 是校准 skill 的专属收尾动作——**不跑这个 skill 就不会有 trace，因此提示天然与 skill 绑定**（回应你"没用这个 skill 不该有警告"）：
1. **mode 对账**：record 声称的 generation_mode vs input_visuals 权威推导不一致 → `mode_mismatch_warning`（39D t3 谎报实证）。
2. **组内 reference-only 意图提醒**：段内非首镜+上镜已有视频+reference_only 开场+无正当理由 → `relay_intent_hint`；record 写 `continuity_type=intentional_cut` 或 `reference_only_note` 后**不再提醒**（不拖慢有意的特殊处理）。
3. **裁定完整性警告**：视频已生成但 record 缺 human_deviation_level → `adjudication_incomplete_warning`（39C/39E 两次实证：prose 收口条款未改变行为，此处给牙齿）。
- 三条都是软提示：**绝不阻断落档、绝不改写 Agent 的 record**；正当理由记录后即静默。

### 2.5 生成前确定性预检（`ba7215a` → `b1f2a81` 按你定版角色化）
- `src/agents/pregen_checks.py`：按四类输入角色分检查口径——
  - **reference_only**：姿态词表命中（站姿参考×位移动作）→ **soft 诊断** `pose_reference_mismatch_soft`：绝不阻断/不要求补参考/不升级 major/不请用户改故事；steering 走 prompt+negative_prompt 字段（约束生成结果，不约束参考资产）。
  - **hard_start_boundary**：站姿参考直接用作首帧 × 位移动作开场 = boundary conflict（**阻断级**：换边界帧/换 mode/如实报告）。
  - **relay_\***：不做姿态预判——连续性由生成后接缝测量校验。
- 词表含 滑翔/振翅/展翅/掠水（P39D 首镜"云端滑翔"必须命中）。
- 预检自身失败 = 无冲突（预检绝不阻断生成）。

### 2.6 schema 与字段通路补口（`89ab534`/`2076e9b`/`ba7215a`）
- `shot.create/update/update_many` schema 补 `link_mode`、`video_reference_asset_id`、`negative_prompt`——39D 实证：工具面缺字段时 Agent 只能拒写或落 note。
- `negative_prompt` 进两层白名单（独立 API 参数通路已存在，此前 Agent 写不了）。

## 3. Skill 侧修改（`full-story-relay-calibration` + `keyframes-bridge-repair`）

### 3.1 输入角色决策表（director.md §0，b1f2a81 定版）
四角色识别+分口径检查表（见 §2.5），外加七条槽位语义纪律——核心三条：
- 每个视频的关键图**作参考或首尾帧皆可，参考优先**（不钉死画面、发挥空间大）；
- 组内接续=上镜真实尾帧进首帧槽，**是边界不是参考**；
- 组约定轻量：段=组、新组首镜新参考、系统不做强制。

### 3.2 决策模型改版：插曲制（§4，`542ca38`）
- **major 按根因分两路，目的都是把故事拉回剧本，剧本与故事段不变**：
  - 镜内失败（没演成自己 description 的任务，如触水拍成沉水）→ **单次锚定重试**（anchor patch：首句环境锚定+收束句尾态；含 P39D s02_c02 实证——剧本字面写"沉水"模型就忠实沉水，措辞 bug 在剧本层修）；仍失败→升级用户。
  - 尾态偏离（镜演了任务但收在偏离位）→ **不重试**（重掷骰子），**插曲方案**：一或多个插曲镜（时长可比常规更长），从偏离尾帧接力、逐镜拉回；插曲是正式镜（insert 进段、正常接力与 trace、purpose=interlude），Producer 编排自主推进。
  - 升级用户只剩真实 blocker：重试仍失败、插曲无法回正（链已坏）、配额不足、证据管线异常。
- **插曲后复查**（本轮补）：插曲逐镜落档后同样执行接缝复查与 trace 完整复查；插曲尾帧与其后已生成镜断裂时按级联纪律处置（顺延重生成或插桥）。

### 3.3 组首镜与接力镜的生成纪律（§0，本轮你反馈后新增）
- 🔴 **接力镜不得生成分镜首帧**——首帧槽由系统同步为上镜尾帧；为接力镜画分镜图=浪费额度+制造"槽内图≠实际首帧"假象（39E 你指出的问题）。
- 🔴 **组首镜默认参考直生**（multi+身份参考，38 链根 walk_side 先例）——发挥空间大；i2v 硬首帧仅用户明确要求钉死开场时使用。

### 3.4 与桥的分工（keyframes-bridge-repair/references/director.md）
- 显式互指：**镜内失败（重试可解）与单端尾态偏离（插曲回正）都不走桥**；只有"两端都必须对齐"的断缝（跨 Location、大姿态反转、级联下游断裂）才是桥的领地。
- 桥纪律不变：双端证据才插桥、10s 起步、中间节拍、seed 后零 refresh（boundary_frame_stale）、bridge_frame_check 落档、桥不重生成邻镜。

### 3.5 其他角色指令同步
- Script Writer：预编剧冻结六要素+negative constraints 要能落到生成字段；插曲/吸收句设计。
- Producer：**最终统计必须读 trace 实况**（39D t7 假汇报教训）；none/minor 自动推进、major 完整报告要素、quota 实时读数；组约定编排核对。
- Character Designer：链上身份一致性（多姿态参考建议的提出时机——仅 provider 实证 pose dominance 后）。
- Scene Designer：条件参与（缺场景参考/SIV stale/场景侧根因时进场）。

## 4. 系统与 Skill 的分工原则（你的定版，全部修改遵循）

> **系统**：如实记录（input_visuals/trace）+ 如实显示（框色标记/首帧槽同步）+ 确定性预检与警告——不限制任何视频用哪种用法，不做组分组的强约束。
> **Skill**：写清"怎么做效果好"——参考优先、组内接续、组约定、插曲制、豁免条件——Agent 按 skill 加载方法自主决策，系统只提供事实与牙齿。

## 5. 验证

- 新增测试：input_visuals 构造/round-trip 7 项；预检角色化 20 项；skill 内容锚定 8 项；trace 软提示 14 项（mode 对账/意图提醒/豁免/裁定完整性）；同步规则 4 角色行为 + 锚定 2 项。
- 定向回归：133 项（视频域+skill 域）全绿；201 项（39E 批）全绿。
- 全量 pytest（两次）：**2425 passed / 39 failed**——39 全部为 baseline 挂账（av_sync fixture 缺失），**零新增、零转绿**。
- LIVE 亲验（Phase E Canary，P40A s01 组 2 镜）：接力物理成立（我复测 MAE 4.1/Agent 2.8，同为低位）、无镜内硬切、身份正确、内容符合冻结；**但** trace 裁定字段空置复发→已加警告牙齿；Agent 汇报数字自相矛盾（2.8 报为区间内）→ 记录待观察。

## 6. 遗留与待你确认

1. **[大] s03 SIV 场景参考含一只站立鹤**（空板纪律违反，审查器未拦）——s03 生成前必须重生成该 SIV。已记 `_40_e2e/problems_log.md`。
2. **[中] 组首镜 i2v 残留**：P40A 组首镜（s02_c01/s03_c01）冻结为 i2v+残留 vref=prev_tail（i2v 不消费）。skill 已改"组首镜默认参考直生"——下轮生成时 Director 应自行改配；若它不改，属 skill 执行问题再议。
3. **[低] 接缝测量口径差**：我复测 4.1 vs Agent 2.8（末帧提取参数不同）——建议统一以 `calibration.seam_measure` 服务端口径为唯一标准。
4. **[低] 首帧槽本体的框色**：参考/边界的标记当前在版本条缩略图（琥珀/蓝/绿）+ 批次 snapshot 文本；首帧槽大图本体是否也加框色属前端小改，等你定。
5. **[低] s01_c01 是否按新规则重生成**（multi+参考，替代 i2v 硬首帧）——画面已合格，纯流程修正，由你定。

## 7. STOP

以上为全部系统与 skill 修改的审查材料。等你审查反馈后：遗留项按你裁定处理，Phase E 继续（s02 组实测 → s03 SIV 修复 → s03 组实测），每步照旧亲验+记录。
