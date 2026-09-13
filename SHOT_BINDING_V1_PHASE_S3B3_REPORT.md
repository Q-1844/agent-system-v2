# Shot Production Binding V1 — Phase S3B.3 实施报告

> 阶段：S3B.3 = ShotReferencePack Compiler（执行指令 `详细流程文件/修复阶段/10.md`）。
> 结论先行：**S3B.3 PASS，Ready for Generation Adapter / Generation Path Integration**。
> 126/126 binding 测试全绿（S3B.1 35 + S3B.2 50 + S3B.3 41），冻结区 0 business diff，
> 分块全量回归【见 §15】失败全部逐条归因，零新回归。

---

## 1. Baseline

- branch `feat/scene-system-v1-s1`，起始 commit `7689b95`（S3B.2）
- 开工核验（§0）：tracked 工作区零修改；S3B.1+S3B.2 targeted tests 复跑 **85 passed**
  → **S3B.2 baseline reproducible**；冻结区无未解释修改
- baseline failures：与 S3B.2 报告一致（runtime_control×11、not_configured 族、
  case7_source_changed、cancel_race×2 间歇），全部有既有实证

## 2. Modified Files

| 文件 | 状态 | 目的 |
|---|---|---|
| `src/shot_binding/reference_pack.py` | 新增 | Pack 数据模型（ShotReferencePack/PackBasis/ProfileProjection/SceneProjection/CharacterPackEntry/PropPackEntry/ActionProjection/ReferenceItem，全部 ephemeral dataclass 只有 to_dict）+ 确定性编译器 `build_pack` + approved 参考确定性选择 `select_approved_refs` + temporal 分类 `classify_temporal` |
| `src/shot_binding/service.py` | 修改 | 新增 `compile_reference_pack`（Gate + 一致性守卫 + receipt）与 `_collect_pack_material`（canonical 实体只读收集；独立方法 = 确定性 race 测试的 patch seam）；import PackBlockedError |
| `src/shot_binding/__init__.py` | 修改 | 导出 Pack 模型族 |
| `tests/test_shot_binding_s3b3.py` | 新增 | §45-§65 测试组 A-T + 跨域集成（41 测试） |
| `SHOT_BINDING_V1_PHASE_S3B3_REPORT.md` | 新增 | 本报告 |

冻结区逐目录 `git diff HEAD` = 0（§68 清单：agents/ai/core/resources/scene_system/visual_profile/pipeline/storyboard/routers/frontend——§72 报告末复核）。production 代码全部集中在 `src/shot_binding/*`。

## 3. Final Architecture

```
Shot（creative intent，零改动）
 ↓
ShotProductionBinding（canonical intent，shot_production_bindings.json）
 ↓
ShotBindingValidity（S3B.2 派生 read model——READY/NOT_READY/UNBOUND + 结构化 issues）
 ↓  ← Compile Gate：只有 READY 可进入（Pack 不发明第二套 READY 判断，§3/§5）
ShotReferencePack Compiler（reference_pack.build_pack，确定性纯变换）
 ↓
ShotReferencePack（derived / ephemeral / read-only / 不落盘 / 无 ID）
 ↓
[FUTURE] Generation Adapter → Image / Video Provider（不在本阶段）
```

service = 域加载 + validity gate + 一致性守卫；compiler = 确定性语义投影（§4 职责边界）。

## 4. Pack Data Model

- `pack_version=1`；`shot_id`/`scene_id`；`basis`（binding_revision/binding_document_revision/binding_readiness/dependency_fingerprints 快照/resolved_scene_instance_view_id/visual_profile_fingerprint——§7 provenance，只服务 reproducibility/audit/race-detection/未来 cache key，不形成第二真相）；`visual_profile`（语义字段+指纹，无 prompt）；`scene`（instance/view/SIV 身份 + location 语义 + Scene System 自有 view_relevant_scene_state 投影 + SIV 的 reference_asset_id）；`characters[]`（cast 序）；`props[]`（direct bindings 序）；`action`（V1 原样）；`references[]`（ReferenceItem）；`diagnostics[]`（非阻断）。
- **为什么 ephemeral**：Pack 是"当前 READY binding 在当前 canonical 下的确定性投影"——任何 canonical 变化都应即时反映，持久化会制造第二真相源（§6/§36）。无 ID（身份=shot_id+basis 指纹）；无 store（禁止 shot_reference_packs.json，测试 b2/b3 钉住）。
- ReferenceItem（§13）：source_type（scene_instance_view/character_identity/character_look/composition/prop）/ source_id / reference_asset_id / subject_character / prop_id / purpose（scene_context/character_identity/character_appearance/prop_identity/spatial_relation/action_state）/ priority（primary/supporting/context）/ temporal_scope（start/throughout/end/static）/ relation/anchor/semantic_note。**无 raw path、无 provider 字段**。

## 5. Compile Gate

- UNBOUND → `BLOCKED(binding_unbound)`：不猜 binding、不用 legacy generation files、不用 StyleAnchor、不从 Shot 文本猜资源（§5 Case A）。
- NOT_READY → `BLOCKED`：S3B.2 结构化 issues 原样转发（a2/a3 测试）；不自动 refresh、无 compile→refresh→compile。
- READY → 编译；硬 Gate（a4）。
- canonical 损坏（坏 JSON/未知 schema）→ store 显式异常，不伪装 BLOCKED（§39）。

## 6. Visual Profile Projection

只投影语义字段（render_family/art_direction/style_tags/negative_style_constraints + visual_profile_fingerprint）。**零 prompt 转换**（e3 断言序列化输出无任何 prompt 字样）、**零 StyleAnchor 回落**（e4 断言无 art_style/palette/size_tier 键）、unspecified 在 compile race 中 fail closed（§8）。

## 7. Scene Projection

使用 S3B.2 已解析的 SIV（pinned 精确取用 d2 / unpinned 复用解析 d3）；scene 语义 = Scene System 自有 `view_relevant_scene_state` 投影（`read_scene_instance_view_status` 消费，**零重实现**，D6）；不可见状态变化不改变 Pack scene 语义（D7）；scene 视觉参考 = resolved SIV 的 `reference_asset_id`（approval saga 注册的 approved asset，§10）——不读图片 bytes、不存路径。

## 8. Character / Look / Composition

- 角色顺序 = `Shot.characters`（creative canonical order，§32）；只投影当前 cast（f3/f4：Bob 不进入 Alice 的 Pack）。
- 无 Composition：Look approved refs = **primary** character_appearance（f1）；hero=cover 优先否则第一个可解析项，其余角度 supporting。
- 有 Composition（静态）：composition plate = **primary** spatial_relation（temporal=static，g1/g2）；Look 降 supporting；covered_props 元数据（prop_id/relation/anchor/note）入 characters[]（g2）。
- Character identity refs（legacy Character.views 经只读 adapter）= supporting character_identity（f2）——仅在存在时投影。
- Composition plate 的 approved 参考同样走确定性 hero 选择；approved id 失配 → fail closed。

## 9. Prop Semantics

- **direct prop**：props[] 条目含 relation/anchor/visual_state_note（Resources 词表原样，h2）+ approved 身份参考（primary prop_identity，h1）。
- **composition-covered prop**：关系真相属 Composition（characters[].covered_props）；其 standalone 图只可作 prop_identity **supporting** 且 ReferenceItem 不带 relation/anchor（g3，§19"Composition owns relation"）。
- **action prop standalone**：prop_identity supporting throughout（§42 示例口径）。
- 防御重查（compile 时再跑）：composition×direct 冲突（g4）+ 多重有效关系（m1）。

## 10. Action-aware Temporal Semantics

| kind | Composition plate | Look | action prop standalone |
|---|---|---|---|
| none/static | primary / spatial_relation / **static**（§22.1） | supporting / static | — |
| **pick_up** | **supporting / action_state / end**（§23 硬规则：持物 plate 绝不能当 start primary） | **primary / character_appearance / start** | supporting / prop_identity / throughout |
| **put_down** | **primary / action_state / start**（§24） | supporting / end | 同上 |
| hand_off | 恰两组合（其一属 subject）→ subject=start primary、other=end supporting（§25 唯一映射）；否则 BLOCK | throughout supporting | supporting / throughout |
| custom | supporting / static + `action_temporal_unclassified` diagnostic（§26 不解析自然语言） | supporting / static | 同 none/static |

无 Timeline / 无状态机 / 无 LLM（l1-l3）。

## 11. Direct Prop Subject Policy

**没有修改 PropBinding schema**（§16/§69）：`character_name` 不在 canonical 字段中（测试 test_binding_schema_unchanged 钉住 + `PropBinding.__dataclass_fields__` 断言）。subject 是 **pack 级派生**：
- Rule 1：`action.prop_id == prop_id && action.subject_character` → subject（h3）
- Rule 6：单角色 cast + character-local 关系 → subject=该角色（确定性解析；h1/h2 隐含 + 本报告记录）。"character-local"判定依据：当前 Resources `CompositionAnchor` 词表（right_hand/left_hand/back/waist/neck/beside_character）**全部**为 character-local，无 scene/world 关系值——因此 Rule 3 在现词表下永不适用（如实记录，不猜词表）。
- Rule 4：多角色 + 无 explicit subject → `BLOCKED prop_subject_ambiguous`（h4/h5，detail 带 cast，绝不猜）
- 空 cast + direct prop → 同样 BLOCKED（词表无法表达 scene/world 放置——§75 limitation 记录，h6）
- 全部解析结果只存在于 Pack，不写回 binding。

## 12. Consistency / Race Protection

采用 **pre/post validity guard**（§37 推荐逻辑）：
1. `pre = read_validity()`；非 READY → BLOCKED（转发 issues）
2. `_collect_pack_material` 收集 canonical 实体（独立方法 = race 测试的确定性 patch seam）
3. `build_pack` 纯变换（中途任何解析失败 → PackBlockedError，§38 不交半成品）
4. `post = read_validity()`；比较 readiness + binding_revision + document_revision + resolved_dependencies（resolved SIV）——validity 每次全量重算依赖状态，故该比较覆盖"任何被绑依赖在编译期间变化"（Look/Prop/Composition/SIV/Profile/结构漂移）
5. 不一致 → `BLOCKED dependency_changed_during_compile` + post issues（q1/s1/t1 证明）；无关变更不影响（r1：Bob Look 变 → 仍 COMPILED 且 Pack 逐字节相同）

不引入粗粒度全局锁（§37 尾段）；canonical 损坏显式异常（§39）。

## 13. Determinism

- 无 UUID、无 `compiled_at`、无时钟/random（c1-c4：连续 compile dict 与序列化逐字节一致）
- 顺序全部确定性：characters=Shot.characters 序（§32）、props=binding.prop_bindings 序（§33）、references=§34 语义序（scene → 每角色 identity/look/composition → action prop → direct props）、look/comp approved refs=canonical 存储序（hero=cover 优先）
- 无 set 迭代进入输出路径；只读、零副作用

## 14. Mutation Audit

compile 前后五个 canonical 文件（project.json / resources.json / scene_system.json / project_profile.json / shot_production_bindings.json）**字节级不变**（b1/b4，含重复 compile）；无 shot_reference_packs.json / reference_pack/ 目录产生（b2/b3）；binding revision/updated_at/指纹不变（a5/a6）；不 pin SIV（d4）。

## 15. Tests

- **新增 41**（S3B.3）；binding 三阶段合计 **126/126 passed**（57.92s）
- targeted：Resources+Scene+Profile **300 passed**；Agent 子集 425 passed / 5 failed（3×not_configured 族 + case2/case4 ∈ 冻结 baseline 清单——与 S3B.1/S3B.2 期逐条同构，单跑实证见 S3B.1 报告）
- 分块全量回归：【见下方"全量回归数字"节】失败逐条与 `_pvp_failures_baseline.txt` 对照，零新回归

### 全量回归数字

分块（130 文件 9 块，沿用 S3B.1 方法；后台整跑会被系统低内存终止——S3B.1 报告 §6 有完整记录。**注意：块清单是 S3B.1 时点生成的文件集，不含 S3B.2/S3B.3 新增的两个测试文件——两者已单独 targeted 全绿 126/126**）：
- chunk00: 212 passed；chunk01: 90 passed；chunk02: 12 failed/145 passed（全 BASELINE：runtime_control×11+not_configured×1，逐条 grep 实证）；chunk03: 159 passed；chunk04: 200 passed/1 skipped；chunk05: 220 passed；chunk06: 158 passed；chunk07: 125 passed；chunk08: 215 passed
- **合计 1524 passed / 12 failed / 1 skipped——12 条全部 ∈ 冻结 baseline 34 条清单，本轮零非 baseline 失败**（case7/cancel_race 时序 flake 本轮未触发；单跑实证见 S3B.1/S3B.2 报告）

## 16. Deviations

1. **identity refs（legacy Character.views）进入 Pack 为 supporting**：§12 允许（"如存在，可作为 supporting evidence"），采用只读 adapter `legacy_character_references`（合成 id `legacy:{name}:{view}`，非持久化、无路径身份）。若用户不希望 legacy views 进 Pack，删除该投影段即可（单点、无依赖）。
2. **空 cast + direct prop → BLOCKED prop_subject_ambiguous**：§17 Rule 3 前提是"vocabulary 能明确区分 scene/world 关系"，而当前 `CompositionAnchor` 词表全为 character-local——按"不自行猜 vocabulary"原则 fail closed，作为 §75 limitation 记录（h6）。解除方式=未来 Resources 词表扩展（属 Resources 域决策，不在本阶段）。
3. **hand_off 三方及以上歧义 reason 用 `action_semantics_ambiguous`**（K3），两组合但无 subject 归属也用同一码；`action_target_unspecified` 专用于"组合证据不足（<2 组合）"（K2）。三者均为 §25 允许的等价明确错误码。
4. **非阻断 diagnostics 两条**：`action_state_without_composition_evidence`（action prop 无组合 plate）、`action_temporal_unclassified`（custom）。只出现在 Pack.diagnostics，不影响 status。

## 17. Known Boundaries

- **无 Generation Adapter / prompt builder / provider 映射**（§14/§40/§41：Pack 无 weight/slot/model/seed/prompt——o1 测试钉住）
- **无 Agent / Director / Producer / UI / HTTP 集成**（§2.5）
- **PropBinding canonical 未扩**（§16/§69——多角色 direct prop 场景目前 fail closed，解除需 Resources 词表或 Binding schema 议案，均需用户批准）
- Pack 不负责 provider ordering / media bytes 解析（未来 adapter 通过 reference_asset_id / source_id 向 Resources / Scene System resolve）
- Composition nested revoke policy 仍属 Resources（消费其公开事实）

## 18. Recommendation

**可以进入下一阶段：Generation Adapter / Generation Path Integration。**
依据：Pack 已把"一镜生成需要哪些 production truths、各自语义角色与 temporal 归属"收敛为确定性 model-neutral 输出；validity gate + race guard 保证任何消费点拿到的都是 READY 且一致的投影。建议下一阶段先做 Image Adapter（first_frame 消费 references 中 primary/throughout 项 + temporal=start 项），Video Adapter 随后。

---

## PASS 条件核销（§74）

Architecture：derived/ephemeral ✓ 无 Pack store ✓ 无 canonical ID ✓ 只 READY 编译 ✓ 不重复 validity 真相 ✓。
Correctness：Profile/resolved SIV/Character/Look/Composition/Prop/Action/temporal 全部有断言 ✓。
Critical Action Rule：pick_up 持物 Composition ≠ start primary（i1 硬测试）✓。
Conflict Safety：composition/direct 冲突 ✓ 多重有效关系 ✓ prop subject 歧义 ✓ hand_off 歧义 ✓ 全 fail closed。
Reliability：确定性 ✓ 零 mutation ✓ 无 auto refresh ✓ 无隐藏文件 ✓ race 无 mixed-state ✓。
Boundary：无 provider adapter/prompt builder/generation call/Agent/UI/schema migration ✓。
Regression：S3B.1/S3B.2 126 全绿 ✓ Resources/Scene/Profile/Agent/full 零新回归 ✓。
