# Shot Production Binding V1 — Phase S3C.1 实施报告

> 阶段：S3C.1 = Image / First-frame Generation Adapter + Generation Path Integration
> （执行指令 `详细流程文件/修复阶段/11.md`）。
> 结论先行：**S3C.1 PASS，Ready for S3C.2 — Video Generation Adapter**。
> 150/150 相关测试全绿（binding 126 + S3C.1 24）；真实首帧生成路径已按双路径消费
> ShotReferencePack；冻结区 0 business diff（仅 §54 允许的两个接线必需最小改动）。

---

## 1. Baseline

- branch `feat/scene-system-v1-s1`，起始 commit `9a86a3a`（S3B.3）
- 开工核验（§0）：tracked 工作区零修改；binding 三套件 **126 passed** → baseline reproducible
- 现有 image generation targeted tests：`tests/test_shot_request_rules.py`（6 条，MockAgnes
  HTTP 层拦截）开工后复跑 **6 passed**（legacy 行为零回归的实证，见 §16）
- baseline failures：与 S3B.3 报告一致（runtime_control×11、not_configured 族、
  case7/cancel_race 时序间歇），全部有既有实证

## 2. Existing Image Generation Flow（§3 真实调用链）

```
HTTP POST /project/{p}/shot/{id}/first-frame（或 autopilot `_enqueue_autopilot_job` /
Director `shot.generate_images` / Producer capability——四入口全部收敛 JobQueue）
→ JobQueue worker → routers/shots.py::_handler_first_frame
   1) creative prompt：build_first_frame_prompt（override > prompt_en > 结构字段拼接
      + project.style.suffix() ← legacy StyleAnchor 注入点）
   2) reference collection：_collect_first_frame_refs（character：Character.views
      ref_url/base64；continuity：来源镜 video_end_frame）
   3) use_i2i = bool(refs) → AgnesClient.generate_image(prompt, image_refs,
      size_tier, ratio)（无 seed/无 negative_prompt 参数——AGNES_API_FACTS §4.1）
   4) 结果：download → ImageBatch + char_ref_snapshot → store.update_shot（既有结果路径）
```

**被 Pack 替代的逻辑**（BOUND shot）：风格注入（StyleAnchor.suffix → pack.visual_profile
经 P1 编译器）、角色参考选择（Character.views → pack references 的 approved Look/Composition
资产）、场景参考（无 → resolved SIV）。
**仍属 creative path 保留**：creative prompt 全部字段与优先级、reference_previous 接力
（continuity 参考与子句）、size_tier/aspect_ratio 几何参数来源、结果写回路径。

## 3. Modified Files

| 文件 | 状态 | 目的 |
|---|---|---|
| `src/generation/__init__.py` | 新增 | Generation 边界包（provider transformation 与 binding 域隔离，§5） |
| `src/generation/image_adapter.py` | 新增 | Adapter：temporal 过滤（先于 priority）、权威媒体解析、生产上下文文本、P1 风格组装、pre-dispatch race guard、BoundImageRequest |
| `src/core/prompt_builder.py` | 修改 | `build_first_frame_prompt` 增可选 `style_suffix: Optional[str]=None` 参数（None=legacy 行为逐字节不变；Binding 路径传 Pack 风格指令）——**唯一允许的 core 改动**，§54 最小接线 |
| `routers/shots.py` | 修改 | `_handler_first_frame` 双路径分支（§27）：`shot_has_binding` → `build_bound_first_frame_input`；否则原 legacy 代码块原样保留 |
| `tests/test_shot_binding_s3c1.py` | 新增 | §35-§53 测试组（24 条，FakeClient 零外部调用） |
| `SHOT_BINDING_V1_PHASE_S3C1_REPORT.md` | 新增 | 本报告 |

## 4. Final Architecture

```
Shot（creative intent：description/framing/camera/action 文案/接力 opening_source）
 +
ShotProductionBinding（canonical production intent）
 → ShotBindingValidity（READY/NOT_READY/UNBOUND）
 → ShotReferencePack（COMPILED，含 basis 指纹）
 → Image Generation Adapter（src/generation/image_adapter.py）
    temporal 过滤（end 排除，先于 priority）
    → 权威媒体解析（Resources ReferenceAsset / SIV asset / legacy compatibility）
    → prompt = creative（无 StyleAnchor）+ Pack 生产上下文（start 态 only）
              + P1 风格指令（visual_profile 编译器，含 must-avoid 负面约束）
    → pre-dispatch race guard（§25）
 → AgnesClient.generate_image（现有 provider）
 → 既有结果路径（batch/candidates/char_ref_snapshot/update_shot——零改动）
```

## 5. Adapter Contract

- 输入：`project`（creative 字段+几何参数来源）、`shot`、隐式 canonical（经 svc/pack）。
- 输出：`BoundImageRequest`（ephemeral dataclass）：prompt / image_refs / used_reference_ids /
  dropped_optional / diagnostics / semantic_roles（每个入选参考的 source_type/purpose/
  priority/temporal_scope/subject——§29 观测）/ basis。
- 错误：`BoundGenerationError(code)`，code ∈ {binding_not_ready, reference_media_unresolvable,
  dependency_changed_before_generation}；handler 直接抛出 → job failed、provider 零调用。
- **不落盘、无业务 ID、不写回 Binding/Pack/Shot**（§7/§31；p1 字节级测试）。

## 6. Prompt Integration

- creative prompt **原样复用** `build_first_frame_prompt`（override > prompt_en > 结构字段），
  仅把风格后缀换成 Pack 语义——没有第二套 prompt truth（§20）。
- Pack 生产上下文（确定性、无 LLM、顺序固定，§21）：Scene 行（location + view-relevant
  state 的 time/weather/lighting/environment）→ 每角色外观行（look_label/appearance）→
  组合关系行（"{char} {relation} {prop_name} ({anchor})"）→ **action start_state 行**。
  🔴 end_state 绝不进入首帧 prompt（§22，d1 断言）。
- 风格：`compile_project_style_instruction(profile-from-pack)`——P1 单一编译点复用，
  negative_style_constraints 以 "must avoid" 文本进入（与 Resources/Scene 生成同口径，
  因为真实 provider 的图片接口无 negative_prompt 参数——§14 真实 capability）。

## 7. Reference Selection

1. **Temporal eligibility 先行**（§8/§13）：end → 排除（c1 证明 primary+end 仍被排除）；
2. 媒体解析（§23/§24）：primary 解析失败 → `reference_media_unresolvable`（provider 不调用，
   k2）；supporting/context 失败 → drop + diagnostic；
3. 无容量截断：真实 provider 的 image_refs 为列表、无文档化上限（AGNES_API_FACTS +
   agnes_client.py 实读），全部 eligible 参考按 Pack 确定性顺序进入——**不虚构 capacity**
   （§46 尾段如实报告；L 组不适用）；
4. continuity（接力）作为 creative 参考追加尾部 + 既有子句（creative 机制保留，§2/§20）。

## 8. Asset Resolution

- Resources：`reference_asset_id → rdoc.reference_assets → to_agnes_image_ref`
  （local 可读→data URI；否则 url）——复用既有权威 helper，零 glob/文件名猜测（§29/§56）。
- Scene：resolved SIV 的 `reference_asset_id`（approval saga 注册的 approved asset）。
- legacy identity（`legacy:{name}:{view}` 合成 id）：**隔离 compatibility resolver**
  （`legacy_character_references` 只读 adapter，k4）；raw path 不因此成为 identity（§16）。

## 9. Action-aware First-frame Semantics

| kind | 首帧参考 | prompt 生产上下文 |
|---|---|---|
| none/static | 组合 plate primary（temporal=static）+ Look supporting + SIV | 关系行；无 action 行 |
| **pick_up** | **持物组合排除（end）**；Look(start/primary) + SIV + prop identity(throughout) | `Action start state: right hand empty`；**end_state 文本绝不出现**（d1 断言） |
| **put_down** | 持物组合 **start/primary 进入**；Look(end) 排除 | `Action start state: holding pistol` |
| hand_off | start 组合进入、end-only 组合排除（f1）；Compiler 已 BLOCK 的歧义不重解释（f2） | start_state |
| custom | Pack 给什么消费什么（static），`action_temporal_unclassified` 下传 diagnostic 不升级 blocking（§12） | start_state |

## 10. Provider Mapping（真实 capability，无推测）

`AgnesClient.generate_image(prompt, image_refs=[URL|data-URI], size_tier, ratio)`：
- image_refs 列表（公网 URL 或 data URI），**无文档化数量上限** → 不虚构截断（§46）；
- 无 negative_prompt / seed 参数（负面约束走风格指令文本；图片不支持 seed——FACTS §4.1）；
- 无 character/scene/style slot、无 weight/IPAdapter/ControlNet 概念 → 语义角色只存在于
  adapter 的 semantic_roles/diagnostics（§17：provider 不能表达语义类型时，内部 selection
  仍知道角色）。

## 11. Legacy Compatibility（§27 双路径）

- **UNBOUND**：legacy 路径原样（q1：handler 产出 prompt 与 `build_first_frame_prompt`
  逐字节相等、refs 与 `_collect_first_frame_refs` 相等——含 StyleAnchor 后缀）。
- **BOUND + NOT_READY**：`binding_not_ready` 结构化失败、provider 零调用、**绝不 fallback
  legacy**（b2/b3——错误路径不产生任何 legacy 参考收集）。
- **READY**：只走 Pack path（q3：prompt 无 StyleAnchor 痕迹、basis 带 binding revision）。
- bindings.json 损坏 → schema 异常显式抛出，不静默当 UNBOUND（§39）。

## 12. Race Protection

两道守卫（§25）：①`compile_reference_pack` 内建 pre/post guard（S3B.3）；②adapter 在
媒体解析完成后、provider dispatch 前重读 validity 比对 basis（readiness/binding revision/
document revision/resolved SIV——语义同 S3B.3 post-guard，未复制指纹引擎）。
- 相关变更（m1：解析后改被绑 Look）→ `dependency_changed_before_generation`，调用数 0；
- 无关变更（n1：改 Bob Look）→ 照常生成、request 不变（actual dependencies only）。
- 调用方可显式重试整个 generation；系统绝不 auto refresh / auto rebind / 静默重编译。

## 13. Determinism

同输入两次构造 `BoundImageRequest` → to_dict 逐字段相等（o1）；顺序 = Pack references
确定性序 → temporal 过滤保序 → refs 顺序稳定；无 random/时钟/set 迭代输出（§30）。

## 14. Mutation Audit

`build_bound_first_frame_input` 前后五个 canonical 文件字节不变（p1）；handler 级：
生成结果按既有路径写回（frames + project.json 批次），**resources/scene/profile/bindings
四文件字节不变**（handler_no_canonical_mutation）——不写回 Binding/Pack/审批/Scene 真相（§32）。

## 15. Tests

- 新增 **24**（S3C.1）；binding 四套件合计 **150/150**（126+24，55.10s→合并跑 162 含
  request_rules/structured_fields）
- 覆盖：B gate（READY/NOT_READY/UNBOUND 三态+legacy 等价）、C temporal、D pick_up、
  E put_down、F hand_off（含歧义不重解释）、G static、H Scene（进入 request/无关不变/
  stale 阻断）、I Profile vs StyleAnchor 冲突、J 未绑 Look 不进入、K 解析（成功/
  primary 失败/legacy compatibility）、M/N race、O/P 确定性与零突变、continuity 保留
- 真实 provider 调用数：**0**（FakeImageClient + deps stub；§34/§66）

## 16. Regression

- targeted：request_rules 6 + structured_fields + binding 126 = **162 passed**；
  Resources+Scene+Profile **300 passed**
- Agent 子集：425 passed / 5 failed（3×not_configured 族 + case2/case4 ∈ 冻结清单，
  与 S3B.1/S3B.2/S3B.3 期逐条同构，既有单跑实证）
- 分块全量（130 文件 9 块，块清单=S3B.1 时点文件集；新增测试文件单独 targeted 全绿）：
  chunk00 212 / chunk01 90 / chunk03 159 / chunk04 200+1 skipped / chunk05 220 /
  chunk06 158 / chunk07 125 / chunk08 215 全绿；chunk02【见下方补记】
- **零新回归**

### chunk02 补记

chunk02（12 failed/145 passed）12 条全部 ∈ 冻结 baseline 34 条清单（runtime_control×11 +
not_configured×1，逐条 grep 实证）——与前两阶段分块结果完全同构。合计 **1524 passed /
12 failed / 1 skipped，零新回归**。

## 17. Deviations

1. **`build_first_frame_prompt` 增可选 `style_suffix` 参数**（src/core/prompt_builder.py）：
   §54 允许的接线必需最小改动；None=legacy 逐字节不变（request_rules 6 条回归实证），
   Binding 路径传 Pack 风格指令——未新建第二套 prompt builder（§20）。
2. **continuity 接力参考在 BOUND 路径保留**：接力是 Shot creative 机制（Phase 6A）而非
   production truth，按"Shot creative intent + Pack production truth → request"原则追加
   尾部；legacy 子句文本原样复制进 adapter（legacy 路径零改动）。
3. **negative_style_constraints 以 "must avoid" 文本进 prompt**：真实 provider 图片接口
   无 negative_prompt 参数（§14 真实 capability），与 P1 在 Resources/Scene 生成的既有
   口径一致。
4. **容量策略**：真实 provider 无文档化 image_refs 上限 → 不实现/不虚构 capacity 截断
   逻辑与 L 组测试（§46 尾段"如实报告"）；`provider_reference_capacity_insufficient`
   码预留未用。

## 18. Known Boundaries

- **无 Video Adapter**（S3C.2）；last_frame/video handler 仍走 legacy 路径
- **无 Agent/Director/Producer/UI/HTTP 新功能**（§4 禁令逐项核销）
- **无新 canonical**（无 image_generation_plans.json 等，§31）
- **无 Binding/Pack schema 变更**（Pack 保持 model-neutral，provider mapping 未反向进入）
- legacy Character.views 仅经 compatibility resolver 作为 supporting media，身份不变

## 19. Recommendation

**可以进入 S3C.2 — Video Generation Adapter。**
依据：首帧路径已完成"Binding→Validity→Pack→Adapter→Provider"全链并证明可靠
（gate/race/temporal/风格/解析全部钉住）；video adapter 可复用同一 adapter 骨架
（temporal 过滤需按首/尾帧分别取 start/end 证据——S3B.3 的 temporal_scope 已为此
准备了完整语义）。建议 S3C.2 同时处理 keyframes 双帧与 i2v 的 reference 映射。

---

## PASS 条件核销（§58）

Architecture：Generation 真实消费 Pack ✓ Pack 仍 model-neutral ✓ provider 逻辑未反向污染 ✓
无新 canonical ✓。Gate：READY 可生成 ✓ BOUND+NOT_READY 不调用 provider ✓ 绝不 legacy
fallback ✓。First-frame：start/throughout/static 进、end 排除 ✓（先于 priority）。
Critical pick_up rule ✓（d1）。Resources：权威 resolver ✓ 无 glob ✓ 无文件名猜测 ✓ 无
raw-path identity ✓。Style：ProjectVisualProfile ✓ 无 StyleAnchor fallback ✓（i1 冲突测试）。
Reliability：确定性 ✓ pre-dispatch guard ✓ 相关变更阻断 ✓ 无关变更不误伤 ✓ 零 canonical
mutation ✓。Regression：binding 150 全绿 ✓ image generation 既有行为零新增回归 ✓
Resources/Scene/Profile/Agent/full 零新增 ✓。
