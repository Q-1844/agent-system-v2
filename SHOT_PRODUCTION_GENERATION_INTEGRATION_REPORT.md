# SHOT PRODUCTION GENERATION INTEGRATION REPORT（31.md）

> 阶段：Phase A（Audit）+ Phase B（Design）完成；Phase C-G 待推进（本文档随阶段推进增量更新）。
> 执行约束：Agnes only（禁 SenseNova 真实生成 / 禁 fallback / 禁 CodeBuddy）；冻结区不动；Truth Gate 不降级。

---

## 1. Baseline / Commit

- 分支 `feat/agent-v2-phase-d-golden_run` 实际名 `feat/agent-v2-phase-d-golden-run`
- 起点 HEAD：`70fb256`（D2 收尾）→ 本阶段首个 commit `41495a7`（D2 遗留接线修复，见 §7.0）
- 工作树状态：审计开始时 `routers/shots.py` 有未提交改动（human_reference_mode job 参数透传，D2 会话遗留）；
  s3c1+s3c2+D2 预算子集 **127 passed** 后作为独立 commit 提交（`41495a7`）
- 全量回归基线：tests/ 静态清点 **2237 个测试 / 176 文件**；最近有记录的全量绿为 C2 期 2208 passed
  （D/D2 增量后未再记录全量数字）；本阶段 Phase G 需跑全量并记录实际数
- 生产项目：`Golden_Full_Production_V1`（52 镜全 BOUND，2K 首帧）——**不被任何 pytest 引用**（只被根目录 driver 使用），持久保留

## 2. 前提校正（先调查的第一产出）

31.md 前提「`ShotReferencePack` → 【尚未真正进入 generation provider】」**已过时**。当前真实代码
（逐行验证，非历史报告）：

- 首/尾帧：`routers/shots.py:561-576 / 664-678` BOUND 路由 → `src/generation/image_adapter.py`
  `build_bound_first_frame_input`(:612) / `build_bound_last_frame_input`(:694)——validity gate →
  Pack 编译 → temporal 过滤（先于 priority）→ D2 预算（scene/human mode）→ cap 6 反克隆 →
  权威媒体解析 → prompt 编译（creative + 生产上下文 + P1 风格）→ pre-dispatch race guard。
  该链自 S3C.1/S3C.2 建成，经 C2（22.md 连续性编译）/C1（18.md 路由谓词）/D（29.md cap+反克隆）/
  D2（30.md scene/human mode + 景别前置）加固。
- 视频：`src/generation/video_adapter.py` `build_bound_video_input`(:122)——boundary frames
  selection truth + freshness fail closed；provider 能力诚实映射（i2v 只有 first 槽；keyframes
  first+last；无任意 reference 槽，`pack_references_not_provider_consumed` 显式 diagnostic :220）；
  Native AV 编译 + **pre-dispatch + pre-commit 双守卫**（native_av_pack.py:417/:438）。
- UNBOUND：legacy 行为逐字节保留（路由谓词 `shot_has_visual_production_binding` :128；
  dialogue-only binding 不触发 BOUND）。
- NOT_READY / stale / 撤销：全部 fail closed（`BoundGenerationError`，绝不回落 legacy）。
- 触发路径唯一汇合点 = 三个 JOB_HANDLERS（§3.1 矩阵）；Director/autopilot/Producer 均提交同 kind job。

**结论：31.md §0「如果已经有部分 integration，不要重复实现；审计现状后从真实缺口继续」适用——
本轮从 §6 真实缺口继续，不重建既有链。**

## 3. Generation Path Audit（Phase A）

### 3.1 触发路径汇合矩阵（全部经 JobQueue dispatch：src/core/job_queue.py:245-247）

| 触发源 | first_frame | last_frame | video |
|---|---|---|---|
| HTTP（shots.py:760/:908/:1498；orchestrate :1892-1899；jobs retry） | `_handler_first_frame`(:533) | `_handler_last_frame`(:639) | `_handler_video`(:1185) |
| Director tool（director_visual.py:311-321） | 同 handler（job.params 可带 scene/human_reference_mode；origin_run_id=dgv_*） | 同 | 同（可带 video_model） |
| autopilot（src/ai/autopilot.py:751/:788/:823） | 同（origin_run_id=run_id + autopilot_stage_key；后接 choose_*_business） | 同 | 同（+ set_final_video_business） |
| Producer/AgentTask（shot_producer_capability.py:353-366） | 同（real kind；origin_run_id=task_id；提交前多 Plan preflight） | 同 | 同 |
| 启动恢复（services/lifecycle.py:54-55） | — | — | 同（续轮询或重提交） |

- Director 生成工具 `shot.generate_images`/`shot.generate_videos`（director_visual.py:495/:555）
  **不直接调用 adapter/provider**，提交与 HTTP 相同 kind；`reference_policy=bound_required` 只是
  Director 侧提交前额外门槛（:267-304）。
- 全仓 shot 帧的 `generate_image(` 调用点只有 :594/:682 两处；shot 视频 `create_video(` 只有 :1353。
  其余 generate_image 调用属其它资产域（角色三视图/场景视图/资源），与 shot 帧无关。
- `visual.review_shot`（src/agents/shot_review.py:481 执行器，`review_shot_core` :384 可独立复用）
  只读；`shot.select_frame`（director.py:1527）与 REST choose 同语义。

### 3.2 First Frame（§2.A 逐项）

| 31.md 问题 | 现状（file:line） |
|---|---|
| prompt 来源 | BOUND：`build_first_frame_prompt(style_suffix="")` + Pack 生产上下文 `_production_context_text` + P1 风格指令（image_adapter.py:672-680）；UNBOUND：legacy builder + `_legacy_vp_style_suffix`（shots.py:575-576） |
| style 来源 | BOUND：pack.visual_profile → `compile_project_style_instruction`（唯一编译点，:281-293）；**无 StyleAnchor 回落** |
| character refs | BOUND：pack references（每角色 1 look hero；legacy identity 视图被 D2 预算排除；composition plate 独立 ReferenceItem）；UNBOUND：`_collect_first_frame_refs`（shots.py:406） |
| continuity refs | reference_previous → 来源镜头视频末帧（image_adapter.py:655-670，append 尾部 + continuity clause）；inherit_previous 走视频 relay（不进图片 refs） |
| reference 结构 | `image_refs: List[str]`（公网 URL 或 data URI）；语义清单在 `semantic_roles`（:600-606） |
| provider 数量上限 | 无文档上限；**真机 cap=6**（400 invalid_request，Golden Run Batch 1 证据）→ `MAX_PROVIDER_INPUT_IMAGES=6`（:402） |
| reference 顺序语义 | provider 未文档化；adapter 保持 Pack 确定性序（scene→look→composition→prop，reference_pack.py:355 起） |
| i2i / multi-image | 支持（`image` 字段数组，agnes_client.py:493-496；URL/data URI 均可） |
| candidates 落盘 | frames dir + `Candidate(local,url)` + `ImageBatch` append；`selected_first_frame` 自动指新批次第一候选（shots.py:601-635） |
| ImageBatch provenance | prompt_snapshot ✅ / char_ref_snapshot（legacy）✅ / origin_run_id ✅ / generation_basis = PackBasis + image_provider + scene_reference_mode + sent_ref_sequence（:614-624）——**缺口见 §6 G2** |
| char_ref_snapshot 产生 | handler 内 `project.get_character(name).ref_url()`（:612-613；legacy 快照，非 BOUND 参考真相） |
| 汇合 | ✅（§3.1 矩阵） |

### 3.3 Last Frame（§2.B 逐项）

- opening/first frame 作为 reference：✅ 首帧恒第一张构图基础（image_adapter.py:762），预留
  `reserved_slots=1` 进 cap 预算（:749-750）。
- continuity reference：reference_previous 由首帧生成侧承担；尾帧只认已选首帧。
- Character/Scene/Prop reference 缺失：不缺——BOUND 尾帧经同一 Pack（temporal 镜像规则
  end/throughout/static 进、start 排除 :154-157）。
- reference budget 冲突：已处理（cap-1 预留）。
- 构图基础 freshness：`boundary_stale_detail` fail closed（:347-369；basis 缺失=artifact_basis_missing）。
- 尾帧 handler 落盘：generation_basis = PackBasis + image_provider（shots.py:706-707）——**连 D2 的
  scene_reference_mode/sent_ref_sequence 都没记（G2 的一部分）**。

### 3.4 Video（§2.C 逐项）

provider 真实支持（agnes_client.py + AGNES_API_FACTS §3）：

| 能力 | 支持 | 证据 |
|---|---|---|
| first frame（i2v） | ✅ `image_url`（公网 URL；**视频接口无 data URI**） | FACTS §3.3 |
| last frame | ✅ 仅 keyframes 模式 `image_urls=[first,last]` | video_adapter.py:14 |
| 任意 reference/character ref | ❌ API 有多图形态但未验证、代码拒绝（"暂不支持"） | FACTS :153-155；video_adapter.py:16-18 |
| video reference / image sequence | ❌ | 同上 |
| 2.5-flash | ✅ mode/duration_s 新契约（轮询须同 model_name） | shots.py:1261-1277/:1371 |

- Pack references 不直接进 provider（诚实 diagnostic :220-224）；生产真相经 boundary frames（由
  Pack 生成、freshness 管控）+ prompt 生产上下文进入——**与 31.md §3「不要把所有图片扔给模型」在
  视频侧天然成立**。
- ReferencePack 接入不会造成 reference budget 冲突（视频无图片预算）。

### 3.5 Race / OCC / Lock（§13 对照）

- 三阶段契约：短读（store.load）→ 释放锁调 provider（无锁跨 provider 窗口 ✅）→ 短写
  （store.update_shot = fresh load→mutate→原子 save，store.py:138-146）。
- pre-dispatch guard：图片（image_adapter.py:298-315）+ 视频（native_av + `_pre_dispatch_guard`）。
- **post-provider commit guard：视频有（native_av_pre_commit_check :1389-1396），图片无（G1）**。
- 域内先例：resources/scene_system 生成链已有同型 post-guard（`recompute_basis_still_holds` →
  `stale_generation_basis` 拒绝 attach；tests T19/T20 覆盖）——图片 handler 补齐时照此口径。

## 4. Provider Capability Audit（图片侧汇总）

- `generate_image(prompt, image_refs, size_tier, ratio, seed, want_b64)`（agnes_client.py:473-507）；
  i2i 时 `image` 数组放顶层或 extra_body（双位置 fallback）；返回 {url,b64}。
- 无 negative_prompt 参数（负面约束经 P1 风格指令 "must avoid" 文本）；图片 seed 通道在签名中存在
  但 handler 不传（AGNES_API_FACTS §2.1：图片接口不支持 seed）。
- reference cap 真机=6；URL 不过期（GCS 公开读）；429 纪律：串行 + 75s 间隔（并发 3 触发风暴）；
  image_2k RPM=16（0981ea3 校准）。
- 内容审核红线：拒 "pure/solid white background" 组合（CLAUDE.md）——裁剪参考的 prompt 语义需避开。

## 5. 消费面与旁路审计（附录结论）

- approved 判定：look/prop `approval_status` + `approved_reference_asset_ids`（resources/models.py:168-171/:234-237）；
  SIV `approval_status` + `approved_basis_fingerprint`（scene_system/models.py:408-409）；hero 选择
  `select_approved_refs`（cover 优先，reference_pack.py:224-243）。
- 两套 provenance 体系关系：域级 `generation_records.jsonl`（资源/场景「怎么生成」）→ approve 固化
  `approved_basis_fingerprint` → validity freshness → Pack basis → `ImageBatch/VideoVersion.generation_basis`
  （shot 产物「基于什么」）。传递链闭合，无需 whole-project hash（31.md §13 禁止项遵守）。
- 旁路：无（shot 生成参考选择全部经 Binding→Pack；adapter 内 rdoc 直读仅为权威媒体解析 owner）。
  唯一记录点：`shot_review.py:262-271` 审查比对图取 `approved[0]` 忽略 cover——与 pack hero 规则
  不一致（review 用途非生成用途；本轮不改，记 §26 known limitations）。

## 6. 真实缺口清单（Phase A 结论）

| # | 缺口 | 31.md 条款 | 严重度 |
|---|---|---|---|
| G1 | 图片 handler 无 post-provider commit guard：provider 窗口内 binding/Look/SIV/profile 变化后，旧 basis 候选仍会被 commit（视频侧已有，图片侧缺） | §13 阶段3 | 高（Truth Gate 级） |
| G2 | 图片 provenance 不完整：used_reference_ids / semantic_roles / dropped(含理由) / human_reference_mode / size_tier / ratio / checksum 均未持久化；尾帧连 scene mode 都没记 | §12 | 中（可审计性） |
| G3 | 无 provider-neutral GenerationRequest 层：BoundImageRequest 为 adapter 内部产物，handler 直接以 Agnes 形态调 client；未来 provider A/B 需要 seam | §10/§20 | 中（架构价值） |
| G4 | 缓解实验未做：裁剪人形参考（identity 无构图锚）、人群约束、参考顺序——D2 只试过 full/context_only/无参考三档 | §19/§23（本轮真正目标） | 目标级 |
| G5 | （记录）shot_review 比对图忽略 cover 的不一致 | §15 旁注 | 低 |

## 7. Phase B — 最小实施设计

### 7.0 Commit 0（已提交）：D2 遗留接线

`41495a7`：handler 透传 job.params.human_reference_mode（Director :315-318 已在传参，handler 此前
静默忽略——承重修复，s3c1/s3c2/D2 预算 127 passed 验证）。

### 7.1 架构（目标形态）

```
ShotProductionBinding → ShotBindingValidity → ShotReferencePack     （冻结，不动）
        ↓
Bound*Adapter（image_adapter / video_adapter，既有；G4 预算扩展在此）
        ↓ to_generation_request()
GenerationRequest（新，provider-neutral：prompt/negative_prompt/reference_inputs[
    ReferenceInput{asset_id, semantic_role, priority, temporal_scope, source_type,
    subject_character, prop_id, media, derivation?}]/first_frame/last_frame/
    ratio/size_tier/seed/model/diagnostics/provenance）
        ↓ compile_agnes_image_call / compile_agnes_video_call（新，唯一 Agnes 形态知识点）
AgnesClient.generate_image / create_video
```

- ReferencePack 保持 model-neutral（不进 provider 字段——现状已成立）。
- `media` 解析仍走权威 owner（ReferenceAsset→Resources→URL/data URI，image_adapter §23 既有实现，
  不搬家）；derivation 字段承载裁剪派生记录（G4）。
- BoundImageRequest/BoundVideoRequest **保留**（既有测试面），新增 `to_generation_request()` 投影；
  handler 改经 GenerationRequest→agnes compile→client，行为逐字节不变。
- UNBOUND 路径零改动（31.md §8：不为统一破坏老项目）。

### 7.2 Commit 计划（每步测试后提交）

| Commit | 内容 | 关键测试 |
|---|---|---|
| C1 | `src/generation/request.py`：GenerationRequest/ReferenceInput schema + `to_generation_request()` + `src/generation/agnes_adapter.py` 编译器；首/尾/视频 handler 改经 seam（行为等价） | seam 单测（确定性/无伪造槽位/UNBOUND 不经此）+ s3c1/s3c2 全绿 |
| C2 | G1：`_post_generation_guard`（image_adapter）+ 首/尾帧 handler commit 前复验；不匹配 → 结构化拒绝、文件保留为 diagnostic、**不 commit 批次** | race 单测（生成窗口改 Look/SIV → 0 commit；无关变更不误伤）；域 T19 口径对齐 |
| C3 | G2：ImageBatch.generation_basis additive 补全（used_reference_ids/semantic_roles/dropped_optional/human_reference_mode/size_tier/ratio/checksum[可用时]；尾帧补 scene mode+sent sequence） | provenance 内容断言；s3c2 test_j5 兼容 |
| C4 | G4 基建：`src/generation/identity_crop.py`（rembg→subject bbox→确定性 bust/face 裁剪→data URI；缓存按 (asset_id,checksum,method)；失败→诚实降级+diagnostic）+ `human_reference_mode="crop"`（auto 映射暂不变，显式供实验）+ derivation 进 provenance | 裁剪确定性/降级/预算交互单测 |
| E/F | 真机 E2E（§8 计划）→ 按证据决定 auto 映射是否 drop→crop + 人群约束（**证据门控，不预设**） | 真机矩阵 + review_shot 评测 + 人评页 |

### 7.3 G4 缓解实验设计（本轮真正要回答的问题）

D2 墙：紧景别残余=景别差一档（CU 意图→MCU/MS）+ 偶发幻觉人群；根因=全身白底立绘的构图锚定。
已试三档：full（构图被立绘锚死）/ context_only（场景图退场）/ drop（人形参考全退，identity 只剩文本）。

**新杠杆（D2 未试过）**：
1. **crop 模式**：紧景别发「look hero 资产的确定性头肩裁剪」而非全身立绘/而非全退——identity
   信号保留、构图锚解除。假设：景别距离进一步收窄且 identity 不劣于 drop。
2. **人群约束**（证据门控）：单人镜（cast=1）确定性追加 population 排除语义——针对偶发幻觉人群
   （含宽景 s09_c01 EWS 渲染 9 人案例）。
3. 顺序实验（仅在 1/2 不足时）：MS+full 组 look-first vs scene-first。

**实验矩阵**（真机，Agnes 2K，串行+75s 纪律）：
- 架构验证（31.md §14 Case A/B/C）：最小 fixture 项目（显式 profile+1 角色+approved look+approved
  LocationView/SIV+1 镜 READY binding）×3 案例——验证生产事实真实进入 provider（§23 验收标准）。
- 缓解 A/B：Golden_Full_Production_V1 代表集紧景别失败镜（s13_c01/s14_c01/s17_c02/s17_c03 等
  D2 矩阵成员）× 变体 {auto(drop)=对照 / auto+crop / auto+crop+人群约束} × 2 候选；
  `review_shot_core` 14 维自动矩阵 + `_phase_d2_ab` 同款 Gallery 人评页；**人评是最终口径，
  自动矩阵只做初筛**（31.md §15：图片本身是证据）。

### 7.4 冻结边界确认（§17）

不改：Producer/Script Writer/Character Designer/Scene Sub-Agent/Resources ontology/Scene System
ontology/VisualProfile ontology/ShotProductionBinding schema/ShotReferencePack 语义；不给 Shot/Binding/
Pack 塞路径或 provider 字段；不自动 refresh/approve；不 silent fallback。四真相文件字节不变测试
（test_shot_binding_s3c1.py:557-567）持续作为护栏。

### 7.5 风险

- OCC/signature：generation_basis 是 plain Dict 字段（models.py:190），additive key 不破坏序列化；
  四真相文件不含 ImageBatch（零突变测试不受影响）。
- crop 依赖 rembg：确定性（同输入同输出），失败降级路径不阻断生成（记 diagnostic）。
- 真机成本：串行+75s；实验矩阵规模受控（≤~40 calls/批）；差图只从 provider URL 重下（D2 教训）。

## 8. Real E2E（Phase E）— 待执行

（计划见 §7.3；执行后填充：case 结果、产物路径、review 矩阵、失败分类）

## 9-27. （Phase C-G 完成后逐节填充：modified files / 架构图终稿 / mapping / 规则 / 集成细节 /
provenance / OCC / 兼容性 / E2E / 证据 / 失败与修复 / LIMITATION / 测试结果 / 冻结区 diff /
known limitations / next phase 建议）

---

*Phase A+B 结论：无隐藏架构冲突——既有链已覆盖 31.md 大部分要求；真实缺口 G1/G2/G3/G4 全部
落在 src/generation/ 与三个 handler 内，不触冻结区。按 §21 顺序进入 Phase C。*
