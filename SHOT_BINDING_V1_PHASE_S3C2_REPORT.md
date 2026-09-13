# Shot Production Binding V1 — Phase S3C.2 实施报告

> 阶段：S3C.2 = Last-frame / Video Generation Adapter + Generation Path Integration
> （执行指令 `详细流程文件/修复阶段/12.md`）。
> 结论先行：**S3C.2 PASS，Ready for Agent Integration / Visual Golden Path**。
> 真实 video / last-frame 生成路径已按双路径消费同一套 ShotReferencePack；
> boundary-frame provenance（generation_basis freshness）落地并 fail closed；
> 冻结区 0 business diff（仅 generation 接线必需最小改动 + 1 项已申报 deviation）。
> 回归：binding 五套件 218 全绿；分块全量 1524 passed / 12 failed 全∈冻结 baseline
> （runtime_control×11 + not_configured×1，逐条归因同构）——零新回归。

---

## 1. Baseline

- branch `feat/scene-system-v1-s1`，起始 commit `f2a793f`（S3C.1），tracked 工作区零修改
- 开工核验（§0）：binding 四套件 + prompt_builders + continuity + request_rules
  **186 passed** → baseline reproducible
- 分块回归基线 = S3C.1 报告口径（1524 passed / 12 failed 全∈冻结 baseline 34 条清单）

## 2. Existing Last-frame Generation Flow（真实调用链）

```
HTTP POST /api/project/{p}/shot/{id}/last-frame（routers/shots.py:815）
  / orchestrator split（routers/shots.py:277）
  / Director Visual（src/agents/director_visual.py:246 "last"→"last_frame"）
→ JobQueue worker → routers/shots.py::_handler_last_frame
   1) link_mode != "keyframes" → RuntimeError（仅 keyframes 需要尾帧）
   2) _await_first_frame_url（180s 有界等待 effective 首帧公网 URL）
   3) prompt = build_last_frame_prompt（StyleAnchor 后缀注入点）
   4) image_refs = [已选首帧 URL] + _shot_char_refs（首帧始终第一张）
   5) AgnesClient.generate_image（i2i，单候选）
   6) download → frames/{shot_id}_last.png → ImageBatch(kind=last_frame,
      based_on_first_frame) + selected_last_frame + 旧 last_frame.mark_done
   7) split_next 桥接拷贝给后继首帧
```

S3C.2 改动点：step 3-5 前插入双路径分支（BOUND → `build_bound_last_frame_input`；
UNBOUND → 原代码逐字节不变）；批次写入 `generation_basis`（仅 BOUND）。

## 3. Existing Video Generation Flow（真实调用链）

```
HTTP POST /video（routers/shots.py:1293）/ autopilot（src/ai/autopilot.py:832）
/ Director Visual（director_visual.py:246 "video"）——三入口全部汇入
deps.JOB_HANDLERS["video"] = _handler_video（routers/shots.py:1104）
   1) 首帧 local 有 / url 空（v1 迁移数据）→ imgbb 重托管（inherit 除外）
   2) ratio→w/h（16:9 → 1280x704 实测提交）；target_duration_s / num_frames /
      默认 3s → snap_num_frames（8n+1 ≤441 @720p）写回 shot.video
   3) eff_ff_url = _effective_first_frame_url
      （inherit_previous → 来源镜头 video_end_frame.url；否则本镜已选首帧 url）
   4) _video_preflight（locked / num_frames / eff_ff_ready / keyframes 尾帧
      failed / quota）→ 不过即 raise，不发起 API
   5) mode = "keyframes" if link_mode=="keyframes" else "i2v"
   6) keyframes：等待 last_frame.url_ready（180s）
   7) prompt = build_video_prompt(shot, mode)【无 StyleAnchor/无风格后缀——继承输入帧】
   8) 恢复路径：video_id 存在且 generating → 续轮询（不重提交）
   9) 重提交：kwargs={num_frames, frame_rate, width, height, seed, negative_prompt}
      + i2v: image_url=首帧 ｜ keyframes: image_urls=[首帧, 尾帧]（v2.0 extra_body.image）
      + video_model/duration_s（2.5 契约）
      → create_video → 立即落库 video_id → poll_video(5s) → download →
      VideoVersion + final_video + video.mark_done → ffmpeg 提取 video_end_frame
```

S3C.2 改动点：**仅在重提交分支**插入双路径（BOUND → `build_bound_video_input`；
UNBOUND → 原代码逐字节不变）；恢复/续轮询路径不做 Binding gate（无新生成输入，
轮询不是 generation dispatch，不阻断在途视频的结果回收）。

## 4. Provider Capability Audit（真实 API，来自 src/api/agnes_client.py:529-645）

| Capability | Supported? | Actual API |
|---|---|---|
| text-to-video | Yes（handler 不使用） | v2.0 mode='t2v'；2.5 mode='text' |
| image-to-video | Yes | v2.0 mode='i2v' 顶层 `body.image`（公网 URL 字符串）；2.5 mode='keyframe' 同形 |
| first-frame | Yes | i2v `image_url` |
| last-frame（单独模式） | **No** | 无 last-frame-only 模式 |
| first+last keyframes | **Yes，仅 v2.0** | mode='keyframes' `extra_body.image=[first,last]`（≥2 张）；2.5 显式拒绝（AgnesError："双帧/多图契约未验证"） |
| arbitrary image refs | **No** | i2v/keyframes 无通用参考槽；'multi' 模式存在但未验证、handler 不用——**不把 Pack references 当成 provider 已消费** |
| negative_prompt | Yes，仅 v2.0（非空才传） | 顶层参数；2.5 契约静默丢弃 |
| seed | Yes，仅 v2.0（None 不传） | 顶层参数；2.5 契约静默丢弃 |
| duration | Yes | v2.0：num_frames(8n+1)/frame_rate；2.5：`seconds` 字符串（4-12） |
| aspect ratio | Yes | v2.0：width/height；2.5：不接受（400） |

Pack semantic truth（references/角色/场景/动作）与 provider can physically express
的区分落在两处：boundary frames（视觉真相，由 Pack 生成）+ prompt 生产上下文
（文本真相）；provider 无槽的维度以 diagnostics 如实报告，不伪造消费。

## 5. Boundary-frame Provenance Audit（本报告最重要新增章节）

**审计结论 = Case C（已存 artifact 无 basis/provenance）。**

审计对象：`ImageBatch`（first_frame_batches / last_frame_batches，project.json 持久层）。
现有字段：prompt_snapshot / char_ref_snapshot / origin_run_id / based_on_first_frame /
candidates(local+url)。**没有任何** binding revision、document revision、resolved
dependency fingerprints、resolved SIV、visual profile fingerprint、reference IDs 或
generation input basis。

因此 S3C.1 锁定的命题在此**不成立**：

```text
Binding 现在 READY ≠ 磁盘上的旧 first/last frame fresh
```

12.md §1 的 T1/T2/T3 场景真实存在：Binding revision 3 生成首帧 A → Alice Look
changed → binding stale → explicit refresh → revision 4 / READY，**但 A 仍在盘上**；
此时若 video 直接消费 A（仅因当前 READY），就是把旧生产真相当成 current evidence。

**最小正确修复（deviation D1，已申报 §24）**：`ImageBatch` 新增可选字段
`generation_basis: Dict[str, Any] = {}`（generation-result metadata）：

- BOUND 首帧/尾帧 handler 在生成时把 Adapter 返回的 Pack basis（binding_revision /
  binding_document_revision / binding_readiness / dependency_fingerprints /
  resolved_scene_instance_view_id / visual_profile_fingerprint）记入批次；
- 消费方（尾帧生成 = 构图基础首帧；video = first/last boundary frame）在 provider
  dispatch 前比对（`boundary_stale_detail`）；
- **不是**新的 production canonical / Binding 替代品 / Shot creative truth 改写 /
  Resources-Scene-Profile 反写——只描述"这个生成结果基于什么产生"；
- deterministic / inspectable（纯 dict 快照）/ 不自动 refresh。

比对键（有意最小化）：`binding_revision` + `resolved_scene_instance_view_id`。

- 不比 `binding_document_revision`：它覆盖整个 bindings 文档——**别的 shot refresh
  会误伤本镜 fresh frame**（违反 actual-dependencies-only）；
- 依赖内容变化 → validity NOT_READY（gate 已挡）→ 唯一出路显式 refresh →
  binding_revision 翻动——两键经传递覆盖全部依赖维度。

**J3 legacy artifact 行为 = 如实 fail closed**：`generation_basis` 缺失（legacy /
binding 前生成，含 S3C.1 期生成的帧）→ `boundary_frame_stale(artifact_basis_missing)`，
provider 调用数 0；**绝不静默认定 fresh**；绝不 auto regenerate——用户显式重新生成
boundary frame（BOUND 路径生成即记录 basis）后 video 才放行。

**inherit_previous relay 物料**：BOUND shot 开场 = 来源镜头实际视频末帧（creative
relay 机制，Phase 6A）——非本镜 Binding 产物、无 basis 可比 → `boundary_relay_no_binding_basis`
diagnostic 不阻断（与 S3C.1 continuity 不做 basis 检查同口径）；其新鲜度由既有接力
机制（continuity_status / source_version）管辖。

## 6. Modified Files

| 文件 | 状态 | 目的 |
|---|---|---|
| `src/generation/video_adapter.py` | 新增 | Video Adapter：validity gate → compile → boundary selection truth + freshness → prompt（creative motion + Pack 生产上下文 video 口径 + P1 风格）→ pre-dispatch guard → BoundVideoRequest |
| `src/generation/image_adapter.py` | 修改 | 泛化：`filter_frame_references(refs, temporal_target)`（first/last 双口径，temporal 先于 priority）、`_production_context_text` 增显式 temporal_target 参数（first 逐字节不变 / last / video）、boundary freshness 助手（selected_boundary_batch / boundary_stale_detail）、共享内核 `_gate_ready_pack` / `_resolve_media_loop`、新增 `build_bound_last_frame_input` |
| `src/core/prompt_builder.py` | 修改 | `build_last_frame_prompt` 增可选 `style_suffix`（None=legacy 逐字节不变）；`build_video_prompt` 增 `style_suffix=""`（默认不拼后缀=legacy 逐字节不变；Binding 路径显式传 Pack 上下文+风格）——§13 显式注入 seam，无第二套 Shot interpreter |
| `src/core/models.py` | 修改 | `ImageBatch` 增可选 `generation_basis` 字段（deviation D1，§5） |
| `routers/shots.py` | 修改 | `_handler_first_frame` / `_handler_last_frame` / `_handler_video` 双路径接线 + BOUND 批次记录 generation_basis（§21 双路径） |
| `src/generation/__init__.py` | 修改 | 导出新 adapter 入口 |
| `tests/test_shot_binding_s3c2.py` | 新增 | §26-§42 测试组（32 条，Fake provider 零外部调用） |
| `SHOT_BINDING_V1_PHASE_S3C2_REPORT.md` | 新增 | 本报告 |

## 7. Final Architecture

```
Shot（creative intent：description/motion/link_mode/opening_source/action 文案）
 +
ShotProductionBinding（canonical production intent）
 → ShotBindingValidity（READY / NOT_READY / UNBOUND）
 → ShotReferencePack（COMPILED，含 basis 指纹）
      │
      ├─→ Image Adapter（S3C.1）                ├─→ Image Adapter（S3C.2 last 口径）
      │   temporal: start/throughout/static     │   temporal: end/throughout/static
      │   prompt=start_state only               │   prompt=end_state only
      │   refs=Pack eligible                    │   refs=[首帧(构图基础)]+Pack eligible
      │                                         │   +首帧 freshness gate
      │                                         │
      └───────────────┬─────────────────────────┘
                      ▼
        Image Provider（generate_image）
                      ▼
        ImageBatch（既有结果路径 + generation_basis 落盘）
                      │
                      ▼
        Video Adapter（S3C.2，src/generation/video_adapter.py）
          boundary selection truth（selected 指针 → owning batch，绝不 glob）
          boundary freshness（generation_basis vs 当前 Pack basis；缺失/不一致→fail closed）
          prompt = build_video_prompt(creative motion)
                   + Pack 生产上下文（video 口径：start→end motion target）
                   + P1 风格指令（VisualProfile 编译器）
          pre-dispatch race guard
                      ▼
        Video Provider（create_video：i2v 首帧槽 / v2.0 keyframes 双帧槽）
                      ▼
        既有结果路径（poll/download/VideoVersion/final_video/video_end_frame 零改动）
```

Video Adapter **不**重新选择 Look/Prop/SIV/Action（§2 禁令）；Pack 保持 model-neutral
（provider mapping 未反向进入 Binding/Pack——src/shot_binding/ 本轮零改动）。

## 8. Video Adapter Contract

输入：`project_dir, project, shot`（+ handler 已就绪的 boundary 帧）。
输出：`BoundVideoRequest`（ephemeral dataclass，§24）：

- `prompt` / `provider_mode`（"i2v"|"keyframes"，沿用 handler 既有 link_mode 映射）/ `first_frame` / `last_frame`（仅 keyframes）
- `used_reference_ids`（实际消费的 boundary candidate ids）
- `dropped_optional` / `diagnostics`（capability 诚实报告：end_state_semantic_target_only、pack_references_not_provider_consumed、boundary_frame_fresh、boundary_relay_no_binding_basis）/ `semantic_roles`（slot→temporal_semantics 映射观测）/ `basis`
- 无 invented 参数槽：seed/negative_prompt 属 Shot creative/config 字段，由 handler 既有 kwargs 传递（Adapter contract 不含）
- 不落盘、无业务 ID、不写回 Binding/Pack/Shot/Resources/Scene/Profile（p1/o1 字节级测试）

错误：`BoundGenerationError(code)`，S3C.2 新增 `boundary_frame_stale`
（detail.reason ∈ artifact_basis_missing / artifact_basis_mismatch /
selected_boundary_artifact_not_found 场景归入 reference_media_unresolvable）；
其余沿用 S3C.1 口径（binding_not_ready / reference_media_unresolvable /
dependency_changed_before_generation）。绝不伪装 provider 500。

## 9. Temporal Mapping

| | first（S3C.1 锁定） | video（S3C.2） | last（S3C.2 锁定） |
|---|---|---|---|
| start | **INCLUDE** | motion 起点（prompt start_state） | **EXCLUDE**（primary 也不例外，c1） |
| throughout | INCLUDE | prompt 上下文 | INCLUDE |
| static | INCLUDE | prompt 上下文 | INCLUDE |
| end | **EXCLUDE**（primary 也不例外） | motion 终点（prompt end_state） | **INCLUDE** |

temporal eligibility **先于 priority**（`filter_frame_references` 单次遍历保序；
S3C.1 c1 + S3C.2 c1 双向反例测试）。

## 10. pick_up

Pack：持物组合 temporal=end（§23 硬规则）、Look=start（primary）、prop identity=
throughout、SIV=throughout。

- **FIRST**（S3C.1 已锁定，本轮 cross-domain 复验）：持物组合排除；prompt 只含
  "Action start state: right hand empty"，end_state 绝不出现；
- **LAST**：持物组合（end）作为 end-state evidence 进入；Look(start) 排除；prompt
  只含 "Action end state: right hand holding the pistol"，start_state 绝不出现（c2）；
- **VIDEO i2v**（真实 provider 无 last-frame 槽）：first slot = start-compatible
  首帧；end state 仅作 motion target 文本（prompt 双态），diagnostic
  `end_state_semantic_target_only` 如实报告——**不宣称 visual last-frame
  conditioning，不把 end ref 塞进 first/current 槽**（d1）；
- **VIDEO keyframes（v2.0 真实支持）**：image_urls=[start-compatible, end-compatible]
  精确入槽（d2）。

## 11. put_down

Pack：持物组合 temporal=start（primary）、Look=end（supporting）。

- FIRST：持物组合进入（start-state evidence）；Look(end) 排除（e2，复验 S3C.1）；
- LAST：start-only 持物组合**排除**（绝不作为 end current-state reference）；
  Look(end) 进入；prompt 只含 end_state "empty hand"（e1）；
- VIDEO：prompt 同时表达 start（holding pistol）→ end（empty hand）motion target（e2）。

## 12. hand_off

Pack：subject 组合=start（primary）、recipient 组合=end。

- FIRST：start 组合进、end-only 组合排除（f1）；
- LAST：end 组合进、start 组合排除（f1——与 first 镜像）；
- VIDEO：prompt 同时表达双 holder 语义（"pistol held by Alice" → "pistol held by
  Bob"）（f1）；
- **歧义 case**（Compiler 已因 action_target_unspecified BLOCK）：first/last/video
  三个 Adapter 全部 `binding_not_ready`、provider 调用数 0，**不通过 prompt NLP
  自行修复/重解释**（f2）。

## 13. static / custom

- static：组合 temporal=static 在 first 与 last **同时 eligible**（G1）；不为首尾
  概念强行制造两个不同状态；同输入重复构造请求稳定（G1）。
- custom：Pack 给什么 temporal 消费什么（classify_temporal → static）；
  `action_temporal_unclassified` 继续 diagnostic 不升级 blocking（S3B.3/S3C.1 决策
  延续，未升级 Action schema，无 NLP timeline 推导）。

## 14. Scene / Look / Composition / Prop mapping

真实 provider i2v/keyframes **无 arbitrary reference 槽**（§4 capability table），
故生产真相进入 video request 的真实途径只有两条：

1. **boundary frames**（视觉）：first/last 帧本身由 Pack 参考生成（S3C.1/S3C.2
   last 口径 adapter → image provider）——Look/Composition/Prop/SIV 视觉真相
   经此物理进入视频 conditioning；
2. **prompt 生产上下文**（文本）：Scene 行（location + view-relevant state）、
   角色外观行（look_label/appearance）、组合关系行（"{char} {relation} {prop}
   ({anchor})"）、action start/end 态行——确定性拼装（H1 断言 "Scene: lab" 真实
   进入 video prompt）。

diagnostic `pack_references_not_provider_consumed` 常驻（H1），杜绝"Pack references
被 provider 消费"的虚假声明。修改无关 LocationView → video request 不变（H2）；
bound SIV stale → provider 不调用（H3）。

## 15. Visual Profile

- Binding-aware video / last-frame prompt 风格 = pack.visual_profile 经 P1 单一
  编译点（`compile_project_style_instruction`）；冲突 legacy StyleAnchor 完全忽略
  （i1：prompt 含 P1 输出 "2D animated image"、不含 "legacy oil painting"）；
- legacy video prompt 本就不拼 StyleAnchor（继承输入帧）——不存在 StyleAnchor
  fallback 需要 seam 抑制；`build_video_prompt` seam 用于**正向注入** P1 风格与
  Pack 上下文（§13）；
- UNBOUND 两条路径 prompt 逐字节不变（q1/q2）。

## 16. I2V / Keyframe Mapping（只写 provider 真实能力）

- **i2v**（默认路径）：`image_url` = start-compatible boundary frame（selection
  truth 解析）；**无 last-frame 能力**——end_state 为 semantic motion target only
  （§8/§22），request 中无伪造 last-frame/image_urls 字段（k1）；
- **keyframes（仅 v2.0）**：`extra_body.image=[first, last]` 严格 first slot ←
  start-compatible、last slot ← end-compatible（d2/q2）；2.5 契约显式拒绝
  keyframes 模式（真实代码 AgnesError）， adapter 不做 2.5 双帧映射——模型选择
  属 job params（creative/config），不在 Adapter 域；
- 两组模式真实存在 → L 组双向测试（d1/d2/q2/k1），无 mock 虚构 capability。

## 17. Artifact Freshness

- 消费前验证（§17）：`selected_{first,last}_frame` → owning batch →
  `generation_basis` vs 当前 Pack basis；不一致 → `boundary_frame_stale`、
  provider 调用数 0（J1/J2）；
- 缺失 provenance → fail closed（J3，§5 详述）；
- **传递新鲜度**：尾帧生成的构图基础=首帧——首帧 stale 则尾帧 fail closed（J4），
  保证尾帧落盘的 generation_basis 有意义；
- **绝不**：发现 stale → 自动重新生成 → 自动开始 video（12.md §17 明令）；
  explicit regeneration only（用户重新跑 first/last frame 生成即记录新 basis）；
- cross-domain Step 8：refresh 后旧 artifact 不自动认定 fresh（binding_revision
  已翻动 → boundary_frame_stale）。

## 18. Race Protection

S3C.1 两道守卫延伸到 video（§18 全窗覆盖）：

1. `compile_reference_pack` 内建 pre/post guard（S3B.3）；
2. Video Adapter 在 boundary 解析、prompt 组装完成后、provider dispatch 前
   `_pre_dispatch_guard`（readiness / binding revision / resolved SIV——语义同
   S3B.3 post-guard，未复制指纹引擎）；
- 相关变更（m1：build 窗口内改被绑 Look）→ `dependency_changed_before_generation`、
  provider 调用数 0；
- 无关变更（n1：改 Bob Look）→ 照常生成、request 逐字段不变（actual dependencies
  only，非全项目大锁）。

## 19. Determinism

同输入两次构造 `BoundVideoRequest` → to_dict 逐字段相等（o1）；last-frame 同
（G1）。顺序 = Pack references 确定性序 → temporal 过滤保序；无 random/时钟/set
迭代输出；runtime UUID 等仅存在于 provider dispatch 之后的字段不在 Adapter 输出内。

## 20. Mutation Audit

- Adapter 级：`build_bound_video_input` / `build_bound_last_frame_input` 前后五个
  canonical 文件（project.json / resources.json / scene_system.json /
  project_profile.json / shot_production_bindings.json）**字节不变**（p1）；
- handler 级：video 生成结果按既有路径写回（videos + project.json VideoVersion），
  resources/scene/profile/bindings 四文件字节不变（p2）；首/尾帧批次写回沿用既有
  ImageBatch 机制（新增 generation_basis 字段随批次落盘——D1 申报范围内）。

## 21. Legacy Compatibility（§41 硬测试）

- **UNBOUND**：video（b4/q2：prompt == build_video_prompt、image_url/image_urls ==
  legacy 计算、seed/negative_prompt 直传）与 last-frame（q1：prompt ==
  build_last_frame_prompt、refs == [ff_url]+char_refs）**逐字节保持**；
- **BOUND + NOT_READY**：`binding_not_ready` 结构化失败、provider 调用数 0、
  **绝不 fallback legacy**（b2/b3/q3——错误路径不产生任何 legacy 参考收集）；
- **READY**：只走 Pack path（q4/i1：无 StyleAnchor 痕迹、basis 带 binding revision）；
- bindings store 损坏 → ShotBindingSchemaError 显式抛出（S3C.1 §39 语义延续，
  `shot_has_binding` 不静默当 UNBOUND）。

## 22. Tests

- 新增 **32**（tests/test_shot_binding_s3c2.py）：B gate×4、C temporal×2、
  D pick_up×2、E put_down×2、F hand_off×2、G static×1、H Scene×3、I Profile×1、
  J provenance×5、K capability×1、M/N race×2、O/P determinism+mutation×3、
  Q legacy×4、cross-domain 9 步全链×1；
- binding 五套件 + prompt_builders + continuity + request_rules 合并：
  **218 passed**（126 S3B + 24 S3C.1 + 32 S3C.2 + 36 既有）；
- 真实 provider 调用数：**0**（FakeMediaClient：generate_image / create_video /
  poll_video / download 全记录零外发）。

## 23. Regression

- S3C.2 targeted 32 / S3C.1 24 / S3B.1-3 126 全绿；
- Resources + Scene System + ProjectVisualProfile：chunk05-08 全绿（220/158/125/215）；
- Agent 子集：chunk00-04（含 6f real pipeline / 6f shot producer 等视频链既有测试
  ——video handler 改动的直接回归面——212/90/159/200+1s 全绿）；
- 分块全量（130 文件 9 块，块清单=S3B.1 时点文件集；S3B.1-3/S3C.1/S3C.2 新增测试
  文件已单独 targeted 全绿）：

| chunk | 结果 | 与 S3C.1 基线 |
|---|---|---|
| chunk00（agents/6f real pipeline 等） | 212 passed | 同构 |
| chunk01 | 90 passed | 同构 |
| chunk02 | 145 passed / **12 failed** | 同构（见下方逐条归因） |
| chunk03 | 159 passed | 同构 |
| chunk04（api/autopilot 等） | 200 passed + 1 skipped | 同构 |
| chunk05（resources 等） | 220 passed | 同构 |
| chunk06（scene 等） | 158 passed | 同构 |
| chunk07 | 125 passed | 同构 |
| chunk08 | 215 passed | 同构 |

合计 **1524 passed / 12 failed / 1 skipped**——与 S3B.1/S3B.2/S3B.3/S3C.1 四期
分块结果完全同构。

chunk02 的 12 条失败逐条归因（逐条 grep 实证，全 ∈ 冻结 baseline 34 条清单）：

- `test_ai_7d_d3_runtime_control.py` × 11（test_control_stop_active_execution /
  resume_execution / resume_session / approve_ready / approve_not_ready /
  approve_already_running / reject / replan / no_active_target /
  stop_no_execution_but_session / persists_meta）——既有 runtime_control 基线失败，
  与本轮改动无关（该文件不触及 generation/shots/binding 域）；
- `test_ai_7d_r_director_min.py::test_not_configured_soft_fail` × 1——既有
  not_configured 族（单跑即过的既有 flake，历轮报告同条目）。

**零新回归**。

## 24. Deviations

1. **`ImageBatch.generation_basis` 字段（src/core/models.py）**——§1 Case C 的
   generation-result metadata 最小修复：
   - 为什么 video correctness 离不开它：video 消费持久化 boundary frame 作视觉
     conditioning；无 provenance 则"Binding READY"会被错误等价于"旧帧 fresh"，
     stale 帧静默毒化视频（§5 T1/T2/T3 场景）；
   - 为什么它只是 artifact provenance 而非第二套 canonical：纯 dict 快照、只读、
     不参与 OCC/revision、不写回 Binding/Pack/Resources/Scene/Profile、不自动
     refresh——它描述"生成结果基于什么产生"，生产真相仍唯一属于 Binding→Validity→Pack；
   - backward compatibility：可选字段默认 {}；旧 runtime 读新数据经 known-field
     过滤忽略（不 crash；若旧 runtime 续写则 basis 丢失 → 新 runtime fail closed，
     方向安全）；新 runtime 读旧数据 → basis 缺失 → fail closed（J3）；
   - legacy artifact 行为：如实 fail closed（`artifact_basis_missing`），显式
     重新生成后放行——绝不静默认定 fresh。
2. **`build_last_frame_prompt` / `build_video_prompt` 增可选 seam 参数**
   （src/core/prompt_builder.py）：§13 允许的接线必需最小改动；None/"" = legacy
   逐字节不变（q1/q2/b4 回归实证）；未新建第二套 prompt builder。
3. **BOUND video gate 不覆盖恢复/续轮询路径**：恢复路径无新生成输入（不重提交），
   轮询非 generation dispatch——gate 在该路径会阻断在途视频的结果回收（video 白做）
   且无正确性收益；B 组 gate 测试均针对重提交路径。
4. **boundary freshness 比对键 = binding_revision + resolved SIV**（不含
   document_revision）：document_revision 覆盖全文档，别的 shot refresh 会永久
   误伤本镜 fresh frame；依赖维度经 validity NOT_READY → refresh → binding_revision
   传递覆盖（§5 详述）。

## 25. Known Boundaries

- 无 Agent integration（Director/Producer/autopilot 行为不变，仅经既有 JobQueue
  handler 汇流点自动获得双路径）
- 无 UI / Production Room
- 无 Dialogue / Voice / TTS / lip-sync / soundtrack
- 无 Timeline V2 / ActionGraph / multi-action engine / AnimationStateMachine
- 无 auto regenerate / auto refresh / auto rebind / auto approve
- 无 universal multi-provider abstraction / Kling/Runway/Veo 虚构 adapter
- 无新 canonical generation truth（无 generation-plan 文件；Pack 不持久化）
- 2.5 契约 keyframes 双帧不可用（provider 显式拒绝，真实能力如此）

## 26. Recommendation

**Generation Adapter layer complete? — YES。**

- Shot→Binding→Validity→Pack→Adapter→Provider 全链在真实 image（S3C.1）与真实
  video/last-frame（S3C.2）路径均成立；temporal 语义（first/last/video 三口径）、
  boundary provenance、race guard、双路径兼容全部钉住并有测试；
- **Ready for Agent Integration / Visual Golden Path**：Director/autopilot 入口
  无需改动即经 JobQueue handler 汇流点获得 Binding-aware 生成；下一阶段可在
  Agent 域暴露 bind/refresh/生成编排语义，或先走 Visual Golden Path 全链真机验收。

---

## PASS 条件核销（12.md §48）

Architecture：video 真实路径消费 Pack ✓ Pack model-neutral（shot_binding 域零改动）✓
provider 逻辑未反向污染 ✓ 无新 canonical generation truth ✓。Gate：READY 可生成 ✓
BOUND+NOT_READY provider 调用=0 ✓ 绝不 legacy fallback ✓。Temporal correctness：
first=start/throughout/static、last=end/throughout/static、temporal 先于 priority
（双向反例）✓。Actions：pick_up/put_down 双边界不冲突 ✓ hand_off 不猜歧义 ✓
custom 不 NLP 推导 ✓。Provider truthfulness：只用真实能力 ✓ 无 invented last-frame ✓
无 invented reference 槽 ✓。Artifact correctness：freshness/provenance 有明确政策 ✓
stale 帧不静默当 current ✓ 不 auto regenerate ✓。Reliability：确定性 ✓ pre-dispatch
guard ✓ 相关变更阻断 ✓ 无关变更不误伤 ✓ 零 canonical mutation ✓。Regression：
S3C.1 全绿 ✓ S3B 全绿 ✓ image/video 无新回归 ✓ Resources/Scene/Profile/Agent/full
零新增 ✓。
