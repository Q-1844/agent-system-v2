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

（以下章节随执行推进追加：§4 A/B、§5 policy、§6 review_shot、§7 Director、
§8 52 镜重收口、§9 视频、§10 Gallery/Reel、§11 回归、§12 结论）
