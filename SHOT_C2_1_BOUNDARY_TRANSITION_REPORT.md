# SHOT C2.1 — BOUNDARY TRANSITION FEASIBILITY REPORT（23.md）

日期：2026-09-15
规格：`详细流程文件/修复阶段/23.md`（C2.1 — Boundary Transition Feasibility）
上游：C2 人评收口 = §26 情况 B（C 样本明显净提升保持；E 样本窄跟进）。
真机只测 Sample E（23.md §15）；C / B1 / C1 样本零重跑。

---

## 1-3. First Frame State / Last Frame State / State Delta（§4 人工审计）

审计对象：C2 轮选定边界帧（首 `ff_0dd85b45_1`、尾 `lf_091f74c1_1`）+ canonical +
C2 编译 prompt。人工目检 + canonical 对照，无 CV。

### First Frame State（`s01_c01_first_v1.png`）

- 人物位置：Alice 站在写字台右侧偏前，面朝桌面/窗户（画面左侧）
- 姿态：**右手向前下方伸出、手掌张开悬停在桌面地图上方**；左臂自然下垂
- 枪：古董燧发枪**平躺在桌面地图上**
- 枪由谁持有：无人（桌面道具）
- 枪口大致朝向：**指向画面左侧（绿台灯方向）**，枪柄朝右（Alice 一侧）
- 哪只手靠近枪：右手（悬停在枪上方偏右）
- 表情：平静微笑

### Last Frame State（`s01_c01_last.png`）

- 人物位置：与首帧几乎相同
- 姿态：**右手前伸握住枪柄**，枪被提离桌面少许；左臂下垂
- 枪：被 Alice 右手握持
- 枪口大致朝向：**仍指向画面左侧（台灯方向）**，略微上抬
- 表情：平静微笑

### State Delta

```text
枪 ownership:    桌面（无人持有） → Alice 右手
枪 orientation:  平躺、枪口朝左 → 握持、枪口仍朝左（略抬起）
角色 pose:       右手悬停张开 → 握持；身体其余基本不变
表情:            平静微笑 → 平静微笑
```

## 4. 原 action_sequence 缺什么？（§5）

最短合理路径（first → last 真正需要发生的）：

```text
1. 右手从地图上方下沉到枪柄
2. 手指合拢握住枪柄
3. 将枪笔直提离桌面（枪口方向保持朝左不变）
4. 持稳
```

对照原 canonical：`action_sequence` **为空**；`motion` 是一句话 blob（"reaches out
… picks up … lifting it firmly"，无中间步骤）；`BindingAction` 只有 start/end 态 +
C2 的 Do-not 式 note（"muzzle must not randomly flip"——否定式约束，已证无效）。
**→ 全部必要 transition 都没有被表达。**

### 审计的两个关键发现（改变问题定性）

1. **两帧之间的枪械过渡物理上已是「最小过渡」**：枪口方向两帧一致朝左，
   State Delta 里**没有 180° 翻转需求**。用户看到的「枪头变枪尾」发生在
   **video 插值过程**，不是 boundary 帧强加的几何变化。
2. **表情问题的根源在 boundary 帧自身**：首尾帧里 Alice 都是平静微笑——
   `tense/high` 表演意图只存在于 prompt 文本，**视觉锚点与之矛盾**（模型在两个
   平静帧之间插值）。这直接解释 C2 评审「expression 没变化」。

## 5-7. Test A / B / C 结果（§6-§8）

三组单变量对照（§10 每次只改一个变量；证据 `_c21_e2e/evidence/testX/v1,v2`）：

| 测试 | 唯一变量 | prompt 证据 | freshness | 机器验证 |
|---|---|---|---|---|
| **A** Boundary Correction | **只换尾帧图像**（corrected：握持延续首帧、枪口保持朝台灯方向不旋转、身体不变、表情收敛 tense） | 与 C2 baseline **逐字节一致**（文本零改动） | 2/2 current | 2/2 PASS |
| **B** Explicit Transition | 原首尾帧不动；只把四步中间动作写进 `action_sequence`（下沉→合拢→直提不旋转→持稳） | exact-order 区新增四步（creative 指纹变化） | 2/2 current | 2/2 PASS |
| **C** First-frame-only | 去掉尾帧约束（keyframes→i2v）；action_sequence 恢复空 | i2v 形态（无尾帧 conditioning） | 2/2 current | 2/2 PASS |

对照基线：C2 baseline（`_c2_perf_e2e/evidence/E/v1`，即人评「枪头变枪尾」的样本组）。
Test D（写实风对照）**未执行**——按 §9 仅在 A/B/C 无法判断原因时进行；是否触发由
人评结论决定。

**效果判断 = 待人评（:8914）**——机器只验证流/时长/静音（§11 机器不判动作物理性）。
评审页每条视频按 §11 七项判（枪械结构/枪口连续/动作物理合理/人物完整/identity/
expression/overall vs C2 baseline）+ audio sanity。

## 8. 是否执行 realistic control？（§9 / Q8）

未执行（预留）。触发条件：人评后 A/B/C 均无法定位原因。

## 9. identity drift 与 boundary frames 是否有关？（Q9）

观察事实：C2 轮 v1/v3 出现 identity 漂移而 baseline 稳定（n=2）。本轮已知 confound：
C2 variants 与 baseline 的**尾帧输入不同**（binding note 变更 → boundary 显式重生成）。
本轮 Test A/B/C 各 2 variants 在受控单变量下生成，人评的 identity 判据将给出更干净的
归因数据。若 identity 漂移在 A/B/C 中持续出现而不依赖特定尾帧 → 倾向 provider 随机
边界；若只在特定测试组出现 → 与该变量相关。**结论待人评数据。**

## 10-12. 定性 / 系统能力 / freeze 建议（Q10/Q11/Q12）

- **Q10（boundary planning / transition planning / style boundary / provider
  boundary）**：待 §11 人评七项数据按 §12 决策树定性。
- **Q11（是否需要新增系统能力）**：审计表明**当前 canonical 已能表达全部必要事实**
  （action_sequence 表达中间动作；尾帧 prompt override 表达边界修正；i2v/keyframes
  模式选择表达约束策略）——**无新增 schema 需求**（§2 禁令自然满足）。若人评指向
  「boundary planning rule」，其落地是 Director/创作规约 + 既有字段，不是新系统。
- **Q12（Shot Production Backend V1 freeze）**：按 §14，C2.1 完成后无论结论为何，
  原则上 STOP further Shot-system expansion → 建议 freeze。最终以人评后用户裁定为准。

## 13. 最重要的候选 Production Principle（§13，待人评证实后正式记录）

> **Boundary frames are temporal states, not merely good-looking images.**
> 首帧和尾帧必须共同描述一个在当前 Shot 时长内可以合理发生的运动过程——
> 包括 character pose / prop ownership / prop orientation / spatial relationship /
> action count / shot duration，以及（本轮审计新增发现）**与 performance_intent
> 一致的表情状态**。

## 附：实验记录

- 单变量审计：Test A 的 video prompt 与 C2 baseline **逐字节一致**（差异只在尾帧
  图像）；Test B 的 creative 指纹变化仅来自 action_sequence；Test C 仅 link_mode。
  ——实验解释力成立（§10）。
- 状态机：`_c21_e2e/setup_states.py`（每态幂等、实验后 `restore_baseline()` 已恢复
  C2 baseline 状态——诊断不落生产）。
- corrected 尾帧（`lf_b993a841_1`）经生产 `_handler_last_frame` BOUND 路径生成，
  人工核验：枪口朝向与首帧一致、握持几何自然、身体不变（表情仍偏平静——
  该帧的 tense 修正未完全生效，如实记录）。
- 6 条视频全部经生产 `_handler_video` 全链（VideoVersion/generation_basis/freshness
  /final_video），freshness 6/6 current、机器验证 6/6 PASS、音量正常。
- 配额消耗：6 视频 + 1 尾帧图。零 auto regeneration。

## 结论

```text
SHOT C2.1
EXPERIMENTS COMPLETE（A/B/C 单变量对照 6/6 生成 + §4 审计完成）
HUMAN FINAL REVIEW PENDING（:8914）
```

STOP——等待人评按 §12 决策树定性，然后按 §14 建议 freeze。

## 14. 人评收口（2026-09-15 用户裁定）

评审数据：`_c21_e2e/results/C2_1_REVIEW_FEEDBACK.json`（91 条点击 + 6 短评）。

### 结果：三个测试全部未能解决枪械问题

| | 枪械结构 | 枪口连续性 | identity | 人物完整 | expression | overall |
|---|---|---|---|---|---|---|
| C2 baseline | 变形/morph | 瞬时翻转 | 稳定 | 完整 | 可接受 | 相当/略好 |
| **Test A**（corrected 尾帧）A1/A2 | **变形/morph** | **瞬时翻转** | 稳定 | 完整 | 可接受 | 相当/略好 |
| **Test B**（显式中间动作）B1/B2 | **变形/morph** | **瞬时翻转** | 稳定 | 完整 | 可接受 | 略好 |
| **Test C**（去尾帧约束）C1/C2 | **变形/morph** | **瞬时翻转** | 稳定 | 完整 | 可接受 | 明显更好/略好 |

短评：A「枪还是不对」；B「枪问题还有」；C「枪还是变形了」。

### 决定性归因（§12 决策树）

- **Test C 关键**：**完全去掉尾帧约束后枪依然翻转/变形**——排除了「尾帧约束导致
  interpolation morph」假说；
- **Test A 关键**：corrected 尾帧把几何过渡做到最小（枪口两帧同向）仍翻转——排除了
  「boundary 帧几何不一致」假说；
- **Test B 关键**：显式四步中间动作（正向时序描述）仍翻转——排除了「transition
  under-specified」假说（至此 prompt 侧所有表达手段用尽：几何一致边界帧、
  positive 时序步骤、Do-not 约束均无效）。

**§12 最终定性 = provider capability boundary**：

> **Agnes prop-interaction provider capability boundary**——刚性道具（手枪）在
> 人物手部交互过渡中的几何/朝向保持，超出当前 provider 在该风格下的稳定执行能力。

处置（按 §12/§21）：**接受 variant regeneration + selection 作为当前生产策略**
（VideoVersion 多版本 + 挑选已是生产既有能力）；不做音乐分离式的高阶修复、
不建追踪系统、不为绕过它污染 canonical、停止 prompt tuning。

附带结论：
- **identity 漂移排除系统性**：本轮 6/6 全部「稳定」（C2 轮 n=2 的漂移信号 =
  provider 随机性，非 C2 变更引入的回退）；
- **人物完整/expression 全部可接受**；audio sanity 6/6 正常（dialogue 红线无回退）；
- Test C 组 overall 被评「明显更好」×1——i2v 路线的整体观感不差，但**未解决目标
  问题**，按 §8 不改变生产路由默认。

## 15. 最终结论（§14）

```text
SHOT C2.1 — FINAL
PRIMARY CAUSE = Agnes prop-interaction provider capability boundary
生产策略 = variant regeneration + selection（既有能力，零新增系统）
```

**按 §14 正式建议：SHOT PRODUCTION BACKEND V1 FREEZE。**

- C1（生产正确性）+ C2（C 样本动作顺序控制，明显净提升）+ C2.1（provider 边界
  定界）三轮证据齐备；系统表达面完整（§4 审计证明 canonical 可表达全部必要事实），
  剩余问题属 provider 执行边界，属「系统拥有表达能力但模型无法稳定执行」——
  按规格原文，这与「系统根本表达不了」是两回事，不阻塞封板。
- 本报告即 Shot-system expansion 的 STOP 点：除非出现新的明确 system-level
  missing capability，不再扩 Shot backend。
- 下一阶段候选：**Sequence Assembly Foundation**（跨 Shot 组装）——待用户规格，
  不自动开始。
