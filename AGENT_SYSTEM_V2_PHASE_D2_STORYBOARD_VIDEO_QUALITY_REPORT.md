# AGENT SYSTEM V2 — PHASE D2 REPORT（进行中）
# Storyboard Visual Quality & Full Shot Video Production（30.md）
# 《山雨》· Golden_Full_Production_V1 · 2026-09-19 起

> 状态：**执行中**。本文档按 30.md 各节完成度增量构建；全部真实调用 Agnes only。
> 完成后按 §49 补齐 41 节 + §50 的 26 问。完成前不代表 PASS。

---

## 1. D baseline verdict revision（30.md 开头）

Phase D（29.md）验收结论修订：

- Engineering Pipeline = **PASS**（维持）
- Production Infrastructure = **PASS**（维持）
- Storyboard Visual / Directing Quality = **FAIL / NEEDS HARDENING**（用户人工检查判定）

本阶段（D2）在既有持久项目 `Golden_Full_Production_V1` 上继续：不重建项目、不重写故事、
不重建角色/场景体系；全部真实调用 Agnes（image/text/vision/video），禁 SenseNova、禁 fallback。

## 2. Before baseline freeze（30.md §2）

- 工具：`_d2_baseline.py`（scratch）；输出 `_phase_d2_baseline/baseline_index.json` +
  `BEFORE_BASELINE.md`（已提交入库，作为 D2 全程对照真相）。
- 覆盖：52 镜逐镜 creative intent 字段 / selected first & last frame（candidate_id +
  batch + **sha256**）/ 全部历史候选（差图全部在档，禁止删除）/ binding 概要 /
  generation_basis / 视频状态。
- 快照计数：52 shots / 47 bindings / 9 approved sciv / **0 缺失 selected 文件**。
- 计划中的任何差图删除/覆盖都会被哈希检出；Before vs After 逐镜比较以本索引为基准。

## 3. Reference behavior 全量审计（30.md §3 —— 修改代码前完成）

工具：`_d2_pack_audit.py`（对 52 镜只读编译 ShotReferencePack，不调 provider）；
明细 `_phase_d2_audit/pack_ref_inventory.json` + `PACK_REF_AUDIT.md`。

### 3.1 十问答案（真实代码 + 真实数据）

**Q1 SceneInstanceView 图片是否每个 BOUND Shot 都发送？**
是。`src/shot_binding/reference_pack.py:357-361`：只要 resolved SIV 存在即无条件追加
`purpose="scene_context" / priority="primary" / temporal_scope="throughout"`。
实测 **47/47** READY 镜全部携带场景图。`throughout` 使其对首帧/尾帧均 eligible。

**Q2 它与 Character Look reference 的地位？**
Pack 语义序 scene→characters→props（§34），场景图恒为 **image_refs[0]**；priority 与
角色 look hero 同为 primary。Provider 侧无任何地位差异——见 Q3。

**Q3 provider 是否支持 reference role / weight / strength / crop / mask / background-only？**
**全不支持**。`src/api/agnes_client.py:473-496`：`image_refs` 为扁平列表（公网 URL 或
data URI），无 slot/role/weight 字段；`src/generation/image_adapter.py` 头部 §14 冻结
结论一致（无 negative_prompt、无 seed、无 slot 概念）。真机硬约束：输入图 ≤6（Phase D
Commit I 400 实证），超限由 `_cap_provider_input_images` 确定性裁剪。

**Q4 若不支持不得虚构。** → 30.md §4 的 **Mode C（SELECTIVE SCENE REFERENCE）不可安全
实现，按规格跳过并如实记录**（见 §5）。

**Q5 close-up / ECU / OTS / insert 是否仍收到完整 SceneInstanceView？**
是。pack/adapter 全链路 **无任何 shot_size 感知**。实测：16/16 CU、3/3 ECU、2/2 MCU
首帧发送序列均以 SCENE 开头（如 s17_c02 ECU = `SCENE→IDENT→IDENT→IDENT→LOOK`）。

**Q6 同一 Character 最多发送多少张 reference？**
每角色 pack 内最多 **4 张人形参考** = 1 张 approved Look + 3 张 legacy 身份视图
（front/side/back，`reference_pack.py:417-426` 无条件追加）。三/四人镜头 pack 总 refs
12~17 张（s15_c01=17）。

**Q7 front/side/back 是否可能同时进入一个 Shot？**
是。单角色镜头 5 refs ≤ cap 6 → **cap 永不触发，4 张人形全发**；多角色镜头 cap 触发后
保留 1 look/角色（`LOOK×2~4`），identity 被裁。两种模式都以多张人形进入 provider。

**Q8 Commit J 反克隆策略具体如何选择？**
`src/generation/image_adapter.py:382-436 _cap_provider_input_images`：仅当 eligible > 6
时触发；场景/composition 恒保；每角色保第一张 character_look；单角色镜头允许
identity 多视角/extras 填充剩余槽位（C2 §33 时代假设「单人无克隆歧义」）。**本质是
超限缓解，不是人形参考预算**——≤6 时任何多张人形照发。Phase D s17_c02 克隆案发镜
（ECU）审计复现 = `SCENE→IDENT→IDENT→IDENT→LOOK` 4 张人形 + 1 张全院外景。

**Q9 SceneInstanceView 本身是否可能包含人物？**
已批 9 张 sciv 逐张目检（contact sheet `_phase_d2_audit/sciv_contact_sheet.png`）：
**全部无人**，人物污染路径排除。但发现更严重的问题——见 3.2 R1。

**Q10 Sciv image 在 prompt 中被描述成什么角色？**
**没有明确角色**。`_production_context_text`（image_adapter.py:191-255）仅输出文本行
`Scene: {location_name} (time, weather, lighting…)`；场景**图片**本身进入 image_refs
时无任何语义声明——模型只能按 i2i 默认行为理解第 1 张参考图＝「目标构图」。

### 3.2 根因链（代码 + 数据证据）

- **R1 场景图构图锚定（主根因）**：每个 BOUND 镜头的 refs[0] 都是一张「正面全景」
  sciv。更严重：**标注「内景大堂正面」（19 镜引用）与「内景柴房正面」（10 镜引用）的
  approved sciv，图片实为外景庭院正面全景**（`_phase_d2_audit/sciv_check_*.png` 目检）。
  即全片 47 镜——内景/外景、CU/ECU——refs[0] 实际都是同一构图的庭院大图。
  这同时解释「景别遵从偏宽」与「每张都是完整场景+中等人物」。
- **R2 人形参考过载（克隆/幻觉路人根因）**：单角色 4 张人形（1 look+3 identity）；
  多角色 2~4 张 look。Phase D 已知案发镜 s17_c02（两沈夜）/s15_c01（5+ 路人）
  与本审计的结构完全吻合。Commit J 只在 >6 时起作用。
- **R3 legacy 身份视图混入 V2 链路**：front/side/back 是 V1 时代产物，经
  `legacy_character_references` 兼容适配进入 V2 BOUND 生成，违反「权威 owner」精神
  （V2 人形真相 = approved Look ref）。
- **R4 5 镜 UNBOUND**：s14_c04 / s15_c02 / s16_c03 / s16_c08 / s18_c01 当前无
  Visual Production Binding（Phase D 52 帧均经 BOUND 链生成，现状与 §24 记录不一致，
  待 D2 重收口时补 binding）。

### 3.3 首帧实际发送序列总表（cap 后）

见 `_phase_d2_audit/PACK_REF_AUDIT.md`（按 shot_size 分组全表）。汇总：

| 指标 | 值 |
|---|---|
| READY 且编译成功 | 47/52 |
| 场景参考存在且位置=refs[0] | 47/47 |
| cap(6) 触发 | 30/47 |
| 首帧发送 ≥2 张人形 | 47/47 |
| 首帧发送 ≥4 张人形 | 18/47 |

---

## 4. Shot-level Scene Reference Policy 实现（30.md §6-§8）— 已实现，默认已按 A/B 证据翻转为 auto

Commit 89b1233 / c6f9685：

- `scene_reference_mode ∈ {full, context_only, auto}`（`src/generation/image_adapter.py`）：
  full=场景图 conditioning（Phase D 行为）；context_only=场景图不进 image_refs、场景语义
  仍经 `_production_context_text` 的 Scene 行进 prompt（30.md §4 Mode B 的真实实现——
  BOUND 保持、不 fallback legacy）；auto=确定性导演映射（EWS/WS→full；MCU/CU/ECU/OTS/
  insert/MS→context_only 起草版，A/B 后校准）。
- **`DEFAULT_SCENE_REFERENCE_MODE = "full"`（零回归）**——翻转默认必须以 A/B 证据为前提
  （30.md §4-§6 的顺序纪律）。
- 人形参考预算（§8/§9）：`_apply_reference_budget` —— legacy identity 视图一律不进
  provider；每角色仅保 pack 序第一张 approved look；裁剪逐条记录 `human_reference_budget`；
  cap(6) 保留为最终护栏。任何角色不再出现「1 look + 3 身份视图」的人形过载。
- 透传链：Director 工具 `shot.generate_images(scene_reference_mode=…)` → job.params →
  `_handler_first_frame` / `_handler_last_frame` → adapter（request.scene_reference_mode
  落 generation 观测）。

## 5. Scene Reference A/B（30.md §4-§5）— 完成，判定见 5.1

- 设计：16 代表镜（EWS×2/WS×2/MS×4/MCU×2/CU×4 含全片唯一显式 OTS s10_c02/ECU×2）×
  {Mode A=full, Mode B=context_only} × 2 候选，全部经真实 `build_bound_first_frame_input`
  （同一 pack、同一 creative intent、同一新预算，唯一变量=场景图是否 conditioning），
  Agnes 2K 真机生成。Before 组 = baseline 既有 selected（旧参考行为）作第三对照。
- 评测：每张候选 + Before 共 80 张经 `review_shot_core`（真实 Agnes Vision，14 维度），
  汇总 overall / 维度失败率按组对比；产出 `_phase_d2_ab/AB_EVAL.json` +
  `AB_GALLERY.html`（三组并排）供人工终审。
- Mode C（SELECTIVE SCENE REFERENCE）：**跳过**——Agnes image API 无 crop/role/weight/
  mask/background-only 能力（§3.1 Q3/Q4），按 30.md §4「不支持不要伪造」如实记录。
- 状态：真机生成中（会员档限速波动，429 自愈重试；结果出来后补 §5.1 判定）。

### 5.1 A/B 结果与判定（真机 64 候选 + 16 Before，全部 Agnes 2K + Agnes Vision 14 维审查）

数据：`_phase_d2_ab/AB_EVAL.json`（80 条审查全落盘）、`AB_GALLERY.html`（并排对比页）、
`_phase_d2_ab/ab_runs.json`（每候选真实 prompt/refs/sequence/ basis）。

**三组 overall（deterministic derive_overall）**：

| 组 | n | pass | needs_fix | uncertain |
|---|---|---|---|---|
| Before（Phase D 旧参考行为） | 16 | 0 | 16 | 0 |
| A = full（新预算+场景图） | 32 | 4 | 27 | 1 |
| B = context_only（新预算+无场景图） | 32 | 1 | 31 | 0 |

**关键发现（推翻/修正起草假设）**：

1. **「场景图锚定构图」的假设被二值数据否证、但被逐镜目检部分证实**：
   - 分层统计：CU 族 shot_size fail A 16/16 vs B 16/16——有无场景图都偏宽；
   - **但逐镜目检（s17_c02 内景 ECU vs 外景庭院 plate）**：A 组 = 完整庭院 WS
     （plate 构图直接获胜）；B 组 = 玉佩+账本近景插入构图（大幅改善）。
     场景图主导构图**真实存在、但只在「镜头意图空间≠plate 空间」时爆发**（内景镜
     绑外景 plate 是 Golden Project 的系统性现状，见 §3.2 R1）。
   - pass/fail 二值统计掩盖了「差多远」：B 的失败多为「MS 而非 CU」（近了一步），
     A 的失败多为「完整庭院 WS」（完全跑偏）。景别阶梯距离（EWS↔WS↔MS↔MCU↔CU↔ECU）
     实测：MS 组 A 0.67 vs B 1.60（A 更好）；CU 族两者均 >3（都没解决）。
2. **人形参考预算立竿见影（§8/§9 证据）**：duplicate_identity fail Before 5/16 →
   A 组 2/32；unexpected_people 8/16 → 3/32。克隆/路人在新预算下大幅收敛。
3. **场景图对宽/中景有益**：scene_identity fail B 14/32 vs A 3/32；prop_fidelity
   B 15/32 vs A 8/32。去掉场景图伤害地点一致性与道具保真。
4. **残余主根因 = prompt 结构**：景别指令（"extreme close-up, overhead"）被埋在
   角色描述墙之后（`build_first_frame_prompt` 把 `_char_identity` 放最前）——i2i
   对头部权重高，景别指令被参考图构图淹没。CU→WS 在 A/B 两组都系统性存在。

**判定（§5 两问）**：

- *Scene reference 是否提高地点一致性但损害构图自由？* —— 场景图确实提高地点一致性
  （scene_identity 3 vs 14 fail）与道具保真；对构图自由的损害是**条件性的**：仅当
  「镜头意图空间≠plate 空间」时发生构图主导（s17_c02 实证），同空间宽/中景无损害。
- *哪些 Shot 类型应发送 scene image？* —— EWS/WS/**MS→full**；MCU/CU/ECU/OTS/
  insert→**context_only**（避免 plate 冲突），场景语义仍走 prompt。MS 从起草的
  context_only 修正为 full（A/B 数据：景别距离与身份一致性都支持 full）。

**据此落地的两项修复**（commit 本次）：

1. `DEFAULT_SCENE_REFERENCE_MODE: full → auto`（30.md §6 要求翻转须以证据为前提——
   已满足），auto 映射定稿：EWS/WS/MS→full；MCU/CU/ECU/OTS/insert→context_only；
   未识别景别→full。
2. `_size_directive`（prompt_builder）：首帧 prompt 结构性重排——景别/机位/构图指令
   前置为 `[SHOT SIZE: ...]` 头部（近景族附「画面紧裁、只留局部环境、不要完整地点」
   排除语义），角色描述墙退居其后。这是 CU→WS 残余问题的对症修复（第 3 杠杆）。

Mode C 按规格跳过（provider 无 crop/role/weight 能力，§3.1 Q3/Q4）。

---

---

## 6. Shot-aware Vision Review（30.md §10-§13）— 已建成

- `src/agents/shot_review.py`：`visual.review_shot`——输入=Shot creative intent/cast/
  景别/机位/构图/细节/对白/Binding 期望 Look/场景上下文/待审图（+≤2 张 approved Look
  参考图）；输出=observed 块（people_count/people_notes/实际景别/实际机位语义/场景
  可见度）+ **14 固定维度**（§11 的 12 硬维度 + scene_identity/overall_quality 软维度）
  + director_feedback（可执行导演反馈）+ summary。
- **Deterministic overall（§12）**：`derive_overall` 代码推导——duplicate_identity /
  unexpected_people / missing_character / shot_size_correctness / framing 重大错配 /
  anatomy 严重伪影任一 fail → overall=needs_fix，模型整体印象无权翻案；无 fail 有
  uncertain → uncertain；否则 pass。
- scene_scope_correctness 口径（§11.9）写入评审 prompt：近景只见局部环境（一面墙/雨/
  门框）完全可 pass，绝不因「没画全整个场景」而 fail——只评局部与地点的一致性。
- 复用：`review_shot_core` 不依赖 AgentRuntime——A/B 评测、52 镜全量审查、fixture
  验证走同一条代码路径；telemetry 落 `output/agents/director/shot_reviews.jsonl`。
- 已知差图 fixture（§13）：`_phase_d2_fixtures/`——差图本地文件已被批次覆盖（候选文件名
  跨批复用 `{shot}_first_v1.png`），**从不过期的 provider URL 重下载**并哈希存证：
  F1=s17_c02 克隆+ECU→WS 双证图；F2=s15_c01 五个幻觉路人+柳衣/周伯缺失；
  F3=s15_c01 武器复制+服装漂移。真实 Agnes Vision verdict 确认后写入 registry 供
  derive_overall 单测（真机确认在 52 镜审查轮执行）。

## 7. Director 导演纪律强化（30.md §14-§17 / §36）— 已写入

`DIRECTOR_SYSTEM` 新增「导演核心原则」块：你是在决定观众此刻看什么、不看什么；场景已由
Scene System 建立，不逐镜重复展示；远景立空间 / 中景人物互动 / 近景特写「有意排除信息」
（背景只剩局部墙体/灯光/雨/门框，场景完整≠场景完整入镜）/ OTS 的证据是遮挡与焦点而非
「两人都在画面里」/ insert 锁道具局部；**接力（inherit_previous）是动作与时空的连续，
不是构图复制——cut 的自由永远保留**。8 问推理纪律（叙事目的/注意力/藏露/占比/场景露出/
景别选择/前后镜关系）作为 reasoning discipline，不加机械 checklist、不加 canonical 字段。

## 8. 视频生产能力审计（30.md §26，真实代码）

来源：`src/generation/video_adapter.py` / `src/api/agnes_client.py:529-645` /
`routers/shots.py _handler_video` / `src/core/models.OpeningSource`。

| Mode | 真实支持 | 生产路径暴露 | 备注 |
|---|---|---|---|
| i2v（first-frame video） | ✅ | ✅ 默认 | v2.0 顶层 image URL；2.5 契约 mode='keyframe' 同形；**只有 first frame 视觉槽**，end_state 仅语义文本（adapter §8 诚实诊断） |
| keyframes（first+last 双帧） | ✅ | ✅ link_mode=keyframes | **仅 v2.0**（extra_body.image=[first,last]≥2）；2.5 显式拒绝；首尾同图有 diagnostic |
| t2v | client 可发 | ❌ handler 不暴露 | mode 映射只有 i2v/keyframes |
| multi（多图） | client stub | ❌ 未验证、handler 不用 | adapter 头部明确「未验证且 handler 不用」→ 不测（30.md §26 不发明不存在的功能） |
| Native AV（对白控制） | ✅ | ✅ dialogue 镜默认开 | BOUND/UNBOUND 共用唯一 NativeAVGenerationPack→Compiler；language fail closed；speaker fail closed；generation_basis 参与 freshness |
| agnes-video-2.5-flash（新契约） | ✅ | ✅ video_model 参数 | mode 必填 text/keyframe + seconds 4-12s 720P；**keyframes 双帧在 2.5 被拒** |
| opening_source=independent | ✅ | ✅ | 本镜 selected 首帧开场 |
| opening_source=reference_previous | ✅ | ✅ | 来源镜头末帧作参考图生成新首帧候选（image_adapter continuity clause） |
| opening_source=inherit_previous | ✅ | ✅ | 开场=来源镜头**实际视频末帧**（video_end_frame.url），relay 物料免 basis 检查 |
| video_end_frame relay | ✅ | ✅ | 视频完成自动 ffmpeg 提取；video_end_source_version 记录 provenance；下游 source_version 变化=stale 可检 |
| boundary freshness | ✅ | ✅ | video 消费 boundary 前比对 generation_basis（binding_revision+resolved SIV），stale→fail closed 零 provider 调用 |
| 配额 | — | — | client quota `video_seconds=300`（config）+ 视频 1 RPM 串行 worker；平台档位额度以真机实测为准（§44 遗留：约 5 RPM/日 500s） |

**结论**：本轮真实可测的生产模式 = i2v / keyframes / Native AV / inherit_previous relay /
reference_previous / 2.5-flash(单帧)；与 30.md §29-§39 要求的覆盖面完全吻合（t2v/multi
非生产暴露，如实记录不虚构）。

---

（以下章节随执行推进追加：A/B 判定、52 镜重收口、视频生产、Gallery/Reel、回归、结论）

### 5.2 补充实验：紧景别无参考（Mode C'，D2 新证据）

A/B 后仍有一堵墙：CU 族在 A/B 两组都渲染为 WS。第四个假设——**全身白底 look 立绘把
构图锚定在「全身+环境」**。对照实验（4 紧景别镜 × 2 候选，context_only + **0 人形
参考**，identity 由 prompt 文本承载，Agnes 2K）：

| 指标 | A/B（有 look 参考） | 无参考实验 |
|---|---|---|
| observed 景别 | WS/EWS 为主 | **MCU/MS**（距离收窄一档） |
| observed 人数 | 5~11（s10_c02 曾 11 人） | **1~2** |
| vision pass | 0/16（CU 族） | 1/8（其余仅剩 1 档差距） |

证据文件：`_phase_d2_ab/noref_runs.json` / `NOREF_EVAL.json`；目检样张
`_phase_d2_ab/s17_c03/noref_c1.png`（真近景：头肩占框、雨夜、单人、identity 保持）。

**落地**：`human_reference_mode ∈ {keep, drop, auto}`（默认 auto）——scene 解析为
context_only（紧景别家族）时自动 drop 全部人形参考，identity 走 prompt 文本；
显式 keep/drop 可覆盖（commit dbce0ec）。

## 9. 52 镜重收口进展（30.md §18-§24）— 进行中

- Before 全量矩阵（§18）：PASS 1 / NEEDS_REFRAME 24 / NEEDS_CAST_FIX 23 /
  NEEDS_REVIEW 3 / ERROR 1（`_phase_d2_audit/REVIEW52_MATRIX.md`）。
- 代表集闭环由 Director 真实执行（Round 1-3，会话治理：2 次归档重校准）：
  Director 用 shot.update 改意图、shot.generate_images（bound_required + auto 策略）、
  visual.review_shot 逐候选审查、shot.select_frame 选定。
- **真机 bug 修复**：visual.review_shot 执行器缺 ProjectStore 导入（harness 路径
  不经过执行器所以单测未暴露）→ 修复 + 执行器接线测试（commit 前后两条）。
- ImageBatch.generation_basis 补记 scene_reference_mode + 发送序列（观测缺口）。
- **结果与剩墙**：s16_c02 达成 pass+选定；其余代表镜在 3 次策略升级尝试
  （意图重写/context_only/无参考）后仍不能全过 hard gate——残余失败集中在
  shot_size_correctness（CU 意图渲染为 MS/MCU，距离收窄但未达）与偶发
  unexpected_people。按 §24 规则记 MODEL_LIMITATION，证据链完整：
  这是 Agnes 对本题材的景别遵从能力边界（Phase D §44.5 的延续与量化）。
- 待用户决策点：§48 的「CU/ECU actually close」标准在本 provider 下的可达性——
  (a) 接受 MODEL_LIMITATION 按现状进入视频生产（需放宽 §25 门槛）；
  (b) 人工复核定稿口径（人眼可能接受 MCU 级渲染）；(c) 更换/增加 provider 重试
  紧景别（超出本阶段 Agnes-only 约束，需用户批准）。

