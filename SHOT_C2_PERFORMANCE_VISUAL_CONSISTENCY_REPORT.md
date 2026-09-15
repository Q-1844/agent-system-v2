# SHOT C2 — PERFORMANCE & VISUAL CONSISTENCY REPORT（22.md）

日期：2026-09-15
规格：`详细流程文件/修复阶段/22.md`（C2 — Shot Performance & Visual Consistency Foundation）
上游：Native AV C1（21.md）FINAL PASS，C1 全部冻结。

---

## 1. C1 是否保持完全冻结？（§28 Q1）

**是。** Dialogue/Speaker Control、spoken language truth、pack dialogue semantics、
generation_basis 机制、freshness、race guard、BOUND/UNBOUND routing、A1/A2、
Voice Reference、audio architecture——零改动。C2 只在两处「接线」C1 已建好的机制：
prompt schema v2→v3（§15 允许）与 basis 依赖键增补（§23 要求的正确 STALE 语义），
freshness/race guard 机制本身未动（既有 C1 测试全过）。

## 2-4. Action intent 在哪里表达 / 此前在哪里丢失 / C2 如何保持 ordering（Q2/§6/§7）

### 审计结论（§5，代码证据）

**Action facts 的 canonical 位置**：`Shot.action_sequence`（"动作发生顺序（专业）"）、
`Shot.motion`、`motion_pace`、`environment_motion`、`opening_state`、`end_state`、
BOUND 侧另有 `BindingAction.start_state/end_state/note`。

**§5.B 丢失/弱化清单（审计实证）**：

| 事实 | canonical/pack 状态 | C2 前的 prompt 命运 |
|---|---|---|
| `action_sequence` | Shot 字段存在 | 进 prompt 但仅作 `action sequence: {seq}` 裸前缀——时序语义弱；**C1 样本 C 中该字段为空**（分镜顺序根本没写进 canonical——失败主因） |
| `BindingAction.note` | 已投影进 pack（reference_pack.py:157-164） | **`_production_context_text` 完全丢弃** |
| direct PropBinding（relation/anchor/`visual_state_note`） | 已投影进 pack（reference_pack.py:512-557） | **`_production_context_text` 完全丢弃**（只编译 composition covered_props） |
| `covered_props[].visual_state_note` | 已投影进 pack（reference_pack.py:470-473） | **丢弃**（只编译 relation+anchor） |

**C2 的 ordering 保持（§7）**：`_video_prompt_core`（i2v 与 keyframes 两模式）把
`action sequence: {seq}` 升级为 **`The actions must unfold in this exact order: {seq}`**
——canonical 文本 verbatim 保留，只加强时序框架；顺序标记（1. 2. 3. / 先…再…）由
Director 写进 canonical。真机实证（C/v1 prompt）：四步梯架动作顺序逐字在位。

**§8 不发明动作**：seq 缺失 → 不出现任何顺序文本（测试
`test_no_invented_action_when_sequence_absent`）；compiler 各区只含 canonical 事实
（测试 `test_no_invented_action_from_compiler`）。配套 Director 数据卫生：字段指引
补「多步动作务必写 action_sequence 并带明确顺序标记」。

## 5. 是否增加 continuity schema？为什么？（Q5/§10）

**没有。** §10 要求「只有代码审计证明现有字段无法表达时才能增加」。审计证明相反：
`PropBinding.visual_state_note` 与 `BindingAction.note` 都是**已存在的生产 intent
自由文本字段**，语义完全覆盖观察到的失败（手枪朝向），问题只在编译丢弃——纯 §5.B
丢失类。修 compiler 即可，加字段才是重复建设。报告据此不新增 `continuity_constraints`。

## 6. 当前 prop continuity 如何表达？（Q6）

BOUND 链三层（全部既有 canonical）：
1. **composition covered_props**：`{character} {relation} {prop} ({anchor})` + C2 起
   `visual_state_note` → `Keep this consistent throughout the shot: {note}.`
2. **direct PropBinding**（此前整体未编译）：C2 起同格式编译（含 note）。
3. **BindingAction**：start_state/end_state（原有）+ C2 起 note →
   `Important continuity: {note}.`

**全部仅 temporal_target="video"**（连续性是镜内过程约束；first/last 帧 prompt
逐字节不变——测试 `test_continuity_absent_for_first_last_targets`）。
真机实证（E/v1 prompt）：既有 fixture 中原本就存在却一直被丢弃的
`visual_state_note`（"gripped firmly in her right hand at chest height"）首次进入
prompt——§5.B 丢失类的真机铁证。

## 7-8. 是否增加 performance_intent？为什么？表情如何进 prompt？（Q7/Q8/§12-§14）

**增加了最小 `PerformanceIntent`（emotion / intensity / note）**，挂 `Shot`。
审计依据（§5.C）：系统此前**不存在任何**表演/情绪字段——`important_details`
（情绪氛围）不进视频 prompt，`opening_state` 是首帧静态语义，BOUND binding 无表演
槽位，UNBOUND 无 binding。挂 Shot 而非 binding：表演是导演创作意图（与 motion 同
类），且 UNBOUND 镜没有 binding——挂 binding 会把多数镜排除在外。
🔴 不建 EmotionVersion / FacialAnimationTrack / PerformanceTimeline / Emotion Agent。

**编译形态（§14 统一表演指令，非孤立关键词）**：
```text
Unified performance direction (emotional state: tense; intensity: high): the facial
expression, body action, and spoken dialogue are one continuous, coherent performance
— keep them at this level throughout the shot.
```
位于 visual prompt 之后、dialogue 区之前；可选 note → `Performance note: {note}`。
**空/缺省 intent → 输出逐字节等于既有语义**（§23 测试锁定）。

写路径已接：UpdateShotRequest（`performance_intent` dict，`{}`=清空）、
structure_service 白名单 + 视频字段失效映射。Director LLM 工具 schema（
shot.create_many 参数说明）**未扩**——留给未来 Agent 集成期（机制性接线，报告如实记录）。

## 9. Prompt Compiler V3 的实际变化（Q9/§15）

`native_av_prompt_v2 → native_av_prompt_v3`。V3 输出结构：
Visual identity（原样）+ ordered action（时序框架）+ 统一表演指令（新增）+
non-spoken 指示 + B1 verified dialogue/speaker/language（逐字节保留）。
🔴 无 AudioEventIntent / Music policy / Voice Reference（§15 禁令）。
旧版本经既有 freshness 机制诚实 STALE（`prompt_compiler_schema` 键）。

**basis 依赖键增补（actual dependencies 补齐，§23）**：
- `performance_intent_fingerprint`：表演意图变更 = 生成语义变更；
- `video_prompt_creative_fingerprint`：视频 prompt 实际消费的创作文本字段
  （motion/action_sequence/pace/env/cam/transition/preserve/characters/overrides/link_mode）
  的指纹——**C1 只跟踪 dialogue 侧文本；C2 的动作顺序成为正式能力后，不补这个键
  则 action_sequence 变更后旧 final 会谎报 CURRENT**。无关字段（note/description
  等不进视频 prompt 者）不哈希——无关变更不误报。

## 10. 每一条规则对应哪个真实失败？（Q10/§16）

| Compiler 规则 | Observed failure（C1 人评） | System fact | Transformation |
|---|---|---|---|
| 动作时序框架 | C：人物没有绕楼梯走 | 分镜顺序既不在 canonical 也不在 prompt（action_sequence 空 + 裸前缀弱表达） | exact-order 框架 + Director 指引补 canonical |
| 连续性编译（note/visual_state_note/direct props） | E：手枪方向拿起后变化 | 事实在 pack 中但被 `_production_context_text` 丢弃 | video target 编译三条连续性通道 |
| 统一表演指令 | E：应该紧张但表情不够紧张 | 系统无表演意图字段；important_details 不进视频 prompt | PerformanceIntent + Unified performance direction |

无一条规则无来源；无 Do-not 堆叠（§16 正向优先）。

## 11. 哪些规则尝试后被撤回？（Q11）

无。本轮没有「先实现后撤回」的规则（与 C1-R 的撤回形成对照——那次的教训直接塑造
了本轮「先审计后实现」的路径）。continuity_constraints 字段是**设计阶段**被审计
否决的（见 §5），未进入实现。

## 12-13. Sample C / E 是否改善？（Q12/Q13/§17-§20）

**待用户评审判断**——本报告提供事实面：

### 评审材料（`_c2_perf_e2e/evidence/`，6 条真机视频 + C1 轮 baseline）

| 样本 | baseline | C2 variants | freshness | 机器验证 | 音量 |
|---|---|---|---|---|---|
| C（UNBOUND，梯架动作顺序） | C1 轮 v（旧 compiler × 空 action_sequence） | v1/v2/v3（新 compiler × 补写顺序的 canonical） | 3/3 current | 3/3 PASS | -27.2~-29.1dB |
| E（BOUND，表情+手枪连续性） | C1 轮 v（无表演意图/连续性编译） | v1/v2/v3（tense/high + 手枪朝向连续性编译） | 3/3 current | 3/3 PASS | -24.7~-26.2dB |

- canonical 变更记录：C 补 `action_sequence`（四步顺序）；E 补 `performance_intent
  (tense/high)` + `BindingAction.note`（手枪朝向）→ binding_revision 翻动 →
  boundary 帧 stale → **显式重生成**（`regen_bound_frames.py`，fail-closed 语义的
  正确处理，绝不 auto refresh）。
- E/v1 prompt 实证三类新编译全部在位（表演指令 / Important continuity /
  Keep this consistent——后者来自 fixture 中原本被丢弃的 visual_state_note）。
- 判读口径（§20）：不看 3/3 完美，看正确 variant 是否更容易得到。

## 14. Audio 是否无明显 regression？（Q14）

机器面：6 variants 音量 -24.7~-29.1dB、audio 流/时长/container 全过； dialogue/
speaker/language 红线区逐字节保留（v3 只前置表演区）。人面：评审页每条 variant 有
sanity 判据「Dialogue/audio 无明显 regression」（§25，不做完整音频打分）。

## 15. Visual V1 / C1 是否无 regression？（Q15）

**全量回归 1946 passed，0 failed**（C1 1928 + C2 新增 18）。含 C1 全部
basis/freshness/race 测试、Visual V1、Shot Binding、B1、routing、stage directions。

## 16. 剩余问题属于 system gap 还是 provider boundary？（Q16）

审计后系统侧已知 gap 清零（§5.B 丢失类全部修复，§5.C 缺失类补齐最小表达）。
生成随机性导致的偶发不服从（§20/§21）属 **provider boundary**——处置路径 =
variant regeneration + selection（本轮 6 variants 即此机制的运用），不为绕过它
污染 canonical。评审后若某类仍经常失败，按 §21 记录 provider capability boundary。

## 17-18. 是否建议 Shot Production Backend V1 freeze？是否可进入 Sequence Assembly？（Q17/Q18）

**待 C2 人评结论（§26 三分支）**：
- A（C/E 都明显净提升）→ C2 PASS → Shot Production 封板成立 → 下一模块
  Sequence Assembly Foundation（独立阶段，经用户批准后另立规格）；
- B（一边明显一边没有）→ C2.1 narrow follow-up（只针对失败类别，不重开 C2）；
- C（无净收益）→ 撤回无效规则 + 记录 provider capability boundary（系统表达能力
  与 provider 执行能力是两回事，封板仍可考虑）。

---

## 附：实施与验证记录

**提交**：`a135cba`（C2 实现——§5 审计驱动的编译链升级 + 最小 canonical + 写路径 +
Director 数据卫生）、`5e438b9`（§23 targeted suite 18 项）。

**测试（§23 逐项）**：action order preserved（i2v+keyframes，verbatim）/ no invented
action（seq 缺失 + compiler 无动作动词）/ continuity 三通道确定性编译且仅 video
target（first/last 逐字节不变）/ 无关 prop 不入 prompt / performance intent 确定性
+ 空 intent 逐字节零改动 / 无台词+intent 组合 / BOUND-UNBOUND 语义区逐字节一致 /
schema v3 → 诚实 STALE / performance_intent 与 action_sequence 变更 → 依赖键变 /
无关 canonical 变化（note/description）不误报 / **全量回归 1946 passed**。

**真机 E2E（§17-§19 专项）**：只测 C/E（用户裁定「后续 E2E 只对效果差的样本专门
测试」）；baseline 复用 C1 轮证据；variants ×3 ×2 样本 = 6 条生产 handler 全链视频；
boundary 帧显式重生成 2 张图（binding 变更的诚实路径）；全程零 auto regeneration。

**E2E 评审页**：`"D:/Delta Force/python.exe" _c2_perf_review_server.py` →
**http://localhost:8913/**（§25 判据：C 动作路径/顺序/人物完整/整体；E 表情/道具
连续性/身份/BOUND continuity/整体；每条 variant 一个 audio sanity）。
反馈落盘 `_c2_perf_e2e/results/C2_REVIEW_FEEDBACK.json`。

## 结论

```text
SHOT C2
IMPLEMENTATION PASS（审计驱动 + 全量回归零失败 + 专项 E2E 6/6 生成）
HUMAN FINAL REVIEW PENDING（:8913）
```

STOP——等待用户按 §26 三分支裁决。不自动进入 Sequence Assembly。

## 8. 人评收口（2026-09-15 用户裁定）

评审数据：`_c2_perf_e2e/results/C2_REVIEW_FEEDBACK.json`（36 条点击 + 5 短评）。

### Sample C（动作顺序）——**明显净提升** ✅

| | baseline | C2 v1 | C2 v3 |
|---|---|---|---|
| action_path | 正确 | 正确 | 正确 |
| action_order | 部分正确 | 部分正确 | **正确** |
| character_integrity | **有问题**（崩坏） | 完整 | 完整 |
| overall | 相当 | **明显更好** | **明显更好** |

短评：baseline「错误很多，嘴巴基本没动…男生只是个手并且崩坏」→ v1「无崩坏。很好」→
v3「**基本完美**…说的音准也没问题」。C 侧控制面成立：动作顺序编译 + canonical 补写
后，人物完整性与整体画面显著改善。

### Sample E（表情 + 手枪连续性）——**无净提升** ⚠️

| | baseline | C2 v1 | C2 v3 |
|---|---|---|---|
| expression | 没变化 | 略好 | 没变化 |
| prop_continuity | 没变化 | 没变化 | 略好 |
| identity | **稳定** | **漂移** | **漂移** |
| overall | 相当 | 略好 | 相当 |

短评：v1「手枪在拿到一刹那枪头变枪尾，嘴巴有一点点漂移」；v3「**枪的问题依然存在**」。
连续性指令编译正确（prompt 实证在位），但 provider 未稳定执行——与 §21 预警一致。
另出现 identity 漂移信号（baseline 稳定 → v1/v3 漂移；n=2 无法区分 provider 随机
与系统性回退；baseline 与 variants 的 boundary 帧输入也不同——binding 变更后显式
重生成）。

### §26 三分支映射 → **情况 B**

「其中一个改善明显（C），另一个没有（E）」→ 按规格只做 **C2.1 narrow follow-up
（E 侧：prop continuity / expression）**，不重开整个 C2；C 侧成果保持。
执行纪律按 §21：少量有针对性尝试后仍经常失败 → 记录 **provider capability
boundary**，以 variant regeneration + selection 处置，不为绕过模型限制污染 canonical。

C2.1 的具体规格待用户给出（或委托起草），不自动开始。
