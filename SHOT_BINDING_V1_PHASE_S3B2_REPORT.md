# Shot Production Binding V1 — Phase S3B.2 实施报告

> 阶段：S3B.2 = Dependency Validity + Freshness + Explicit Refresh（执行指令 `详细流程文件/修复阶段/8.md`）。
> 结论先行：**S3B.2 PASS，Ready for S3B.3**。85/85 binding 测试全绿（S3B.1 35 + S3B.2 50），
> 冻结区 0 business diff，分块全量回归 1523 passed / 13 failed / 1 skipped，
> 13 条失败全部 ∈ 冻结 baseline 34 条清单（逐条实证），零新回归。

---

## 1. Baseline

- branch `feat/scene-system-v1-s1`，起始 commit `b8d3afd`（S3B.1）
- 开工核验（§0）：tracked 工作区零修改；S3B.1 targeted tests 复跑 **35 passed** →
  **S3B.1 baseline reproducible**；冻结区无未解释修改
- pre-existing failures（S3B.1 期分块回归实测）：13-15 条/轮（runtime_control×11、
  not_configured 族、case7_source_changed、cancel_race×2 间歇），全部有 baseline/单跑实证

## 2. Modified Files

| 文件 | 状态 | 目的 |
|---|---|---|
| `src/shot_binding/validity.py` | 新增 | 依赖解析（pinned/unpinned SIV）+ 指纹比对 + Derived Validity read model + refresh 共享纯逻辑；`compute_fingerprints`（自 service 移入，create/replace/refresh 三路共享，S3B.1 行为逐字节不变） |
| `src/shot_binding/service.py` | 修改 | ①`_compute_fingerprints`/`_has_usable_approved` 委托 validity（去重，行为不变）；②新增 `read_validity`（纯读）；③新增 `refresh_binding`/`refresh_binding_async`（唯一新写行为，复用 S3B.1 store/OCC/锁/原子写）；④`_issue_text`（issue→错误文本，保留 stale_reasons） |
| `tests/test_shot_binding_s3b2.py` | 新增 | §34 A-K 66 项 + §41 并发 + §42 原子性 + §44 跨域集成（50 测试） |
| `SHOT_BINDING_V1_PHASE_S3B2_REPORT.md` | 新增 | 本报告 |

无其它文件改动（`git diff HEAD` 逐目录 0：agents/ai/core/resources/scene_system/visual_profile/pipeline/storyboard/routers/services）。

## 3. Final Validity Architecture

```
ShotProductionBinding（Canonical Intent，不落状态——无 status/valid 字段）
   + 当前 project.json / resources.json / scene_system.json / project_profile.json
        ↓ validity.compute_binding_validity（纯函数派生，不落盘、无时间戳）
ShotBindingValidity read model
   { shot_id, binding_state: unbound|bound, legacy: bool(仅 unbound 元数据),
     readiness: READY|NOT_READY|UNBOUND, binding_revision, document_revision,
     resolved_dependencies: { scene_instance_view_id },
     dependency_checks: [ {dependency_type, dependency_id, reason, detail} ],
     issues: [ 非 ok 检查 ] }
```

**Binding 是 Intent，Validity 是 Projection；Read 永不 refresh；只有 Explicit Refresh 接受新 basis。**

## 4. Dependency Resolution（逐类）

| 依赖 | 结构检查（复用 S3B.1 错误码） | freshness（S3B.2 新增） |
|---|---|---|
| Shot | 存在（否则 orphan_binding）；scene_id 匹配（scene_mismatch）；cast 集合相等（cast_mismatch） | — |
| Character | 存在（character_missing） | `character_identity_fingerprint`（name+description_en；views 不算身份，§10）vs 快照 → fingerprint_mismatch |
| CharacterLook | 存在 / character 匹配 / 有可用 approved ref（look_missing / look_character_mismatch / look_not_approved——approved id 必须真实指向存在的 ReferenceAsset；revoke=approved 集清空落入同码） | `look_revision` vs 快照 |
| Composition | 存在 / character 匹配 / look_id 匹配 / approved | `composition_revision` vs 快照 |
| Direct Prop | 存在 / 有可用 approved ref | `prop_revision` vs 快照 |
| Composition×Prop 冲突 | binding_conflict（validity 每次重查——Resources canonical 后续可变，§13） | — |
| SceneInstance | 存在 / active / story_scene_id==Shot.scene_id（missing/not_active/story_scene_mismatch） | 🔴 不比对 scene_instance 指纹（见 §13 deviation；快照字段保留为 provenance） |
| LocationView | 存在 / 属于 instance 的 location | — |
| SceneInstanceView | 见 §7 pinned/unpinned | 见 §5 |
| ProjectVisualProfile | specified（否则 project_visual_profile_unspecified）；不可读 → visual_profile_unreadable（§32 fail closed） | `visual_profile_fingerprint` vs 快照 |

检查顺序固定、实体按 binding 数组序、一次读收集全部 issues（不短路）→ 确定性（§33）。

## 5. Fingerprint Semantics

- **快照**：bind/refresh 时刻一次性写入 `dependency_fingerprints`（写侧动作）。
- **当前值**：validity read 每次从各域既有 helper 重算——`character_identity_fingerprint`（resources/generation.py）/ `look_revision` / `prop_revision` / `composition_revision`（resources/models.py）/ `visual_profile_fingerprint`（visual_profile/models.py）/ SIV `approved_basis_fingerprint`（scene 实体字段）/ `scene_instance_revision`（provenance）。**零新指纹算法、零 whole-document revision**（§9）。
- **比对**：仅对 binding 实际引用的实体比对 → Bob 的 Look / 未引用的 Prop / 未使用的 LocationView 变化在结构上进不了检查集 → 无 false stale（§K 62-66 测试证明）。
- **SIV freshness**：消费 Scene System 自有 verdict（`SceneVisualService.read_scene_instance_view_status` → is_stale/stale_reasons，view-relevant 投影由 Scene System 负责，binding 零重实现）；approved_basis_fingerprint 比对捕捉"重新生成+重新批准"；approved 无指纹 → fingerprint_unavailable（§32 fail closed）。

## 6. READY / NOT_READY / UNBOUND

- **UNBOUND**：无 binding 条目。不报错、不创建文件、不猜 binding；`legacy` 元数据（unbound 且 Shot 有生成产物）只作展示，不构成第四种 readiness（§4/§26）。
- **READY**：全部依赖检查 ok。
- **NOT_READY**：任一 issue。reason 词表 = S3B.1 create 码（orphan_binding/scene_mismatch/cast_mismatch/character_missing/look_*/composition_*/prop_*/scene_*/location_view_*/scene_instance_view_*）∪ {fingerprint_mismatch, stale_dependency, project_visual_profile_unspecified, scene_instance_view_ambiguous, fingerprint_unavailable, binding_conflict, project_unreadable, resources_unreadable, scene_system_unreadable, visual_profile_unreadable}。结构化 issue（dependency_type/dependency_id/reason/detail），不是一句话（§5）。
- 损坏 canonical（坏 JSON/未知 schema）→ store 显式异常，不伪装成业务 NOT_READY（§32，测试证明）。

## 7. Pinned / Unpinned SIV

- **pinned**：精确使用该实体（belongs-to instance / matches view / approved 全查）；快照恒与当前 `approved_basis_fingerprint` 比对；**绝不偷换**（§15 Case A）。
- **unpinned**：按 (scene_instance_id, location_view_id) 对解析——复用 Scene System S2B §4 身份规则（一对至多一实体），**不在 binding 层发明选择策略**：0 实体 → scene_instance_view_missing；实体未 approved → scene_instance_view_not_approved；多实体（数据异常）→ scene_instance_view_ambiguous fail closed（§15 Case B）。
- **解析结果只进 `resolved_dependencies`，绝不写回 `pinned_scene_instance_view_id`**（§16，字节级测试证明）。
- 快照语义：S3B.1 unpinned 快照=None=「未记录 basis」→ 跳过指纹比对（不伪造 mismatch）；显式 refresh 后记录解析锚点，此后参与比对（§24）。

## 8. Explicit Refresh

- **能 refresh**：stale-but-currently-valid 的依赖（Look 内容合法更新、Composition 内容更新仍 approved、SIV 被重新生成/重新批准、Profile 变更）。刷新 = 重新走 S3B.1 结构校验管线（对当前 canonical）+ current-usability（全 FK/审批可用 + SIV 可解析 + approved + 指纹可得 + Profile specified）→ 重算全部当前指纹 → 原子写快照。
- **不能 refresh（fail closed 拒绝清单，§22）**：orphan / scene_mismatch / cast_mismatch / binding_conflict / 各类 missing / revoked / unapproved / composition not approved / instance inactive / pinned SIV missing / profile unspecified 或不可读。**SIV stale 不在拒绝清单**（stale-but-valid 正是 refresh 的适用场景），但 refresh 后 validity 诚实重报 stale_dependency——绝不假 READY（Scene 侧修复 + 再次 refresh 才 READY，测试 i44/integration 证明全链）。
- **不改变 Intent**（§23）：payload 直接取自现有 binding 实体（结构性保证），只写 `dependency_fingerprints` + `updated_at`；unpinned 保持 unpinned，仅把解析锚点记入快照（§24，测试 j54/j63）。
- **OCC/原子性**（§21/§41/§42）：project_write_lock 锁内 check-then-write；expected_binding_revision 必带、stale 拒绝；并发双 refresh 恰一成功；任何失败零写入（字节级测试）。

## 9. Orphan / Legacy

- orphan：NOT_READY + orphan_binding；不 delete/GC/归档/修改；refresh orphan 拒绝（§25，测试 b9/b10）。
- legacy/unbound：UNBOUND 不变，无任何自动猜建（§26）；StyleAnchor 不回归，bound 路径唯一风格真相源 = ProjectVisualProfile（§27）。

## 10. Cross-domain False Stale（测试证据）

§K62-66 专项组 + 对应独立测试全绿：
- 无关 CharacterLook（Bob 改 label/描述/加 approved ref）→ READY（c17/k62）
- 无关 Composition（comp_noappr00001 改动）→ READY（k63）
- 无关 Prop（PROP_NOAPPR 改名/加 tags）→ READY（d21/k64）
- 无关 LocationView（view_b label 改）→ READY（h42/k65）
- **SIV 不可见的 Scene state 变化**（a_win 只对 view_b 可见）→ READY（h41/k66）——证明 binding 消费 Scene System view-relevant 投影而非粗粒度 SceneState/实例指纹
- 无关 SIV 的生成/批准/stale → READY（g39）
- 无关项目元数据（story_text/shot.note）→ READY（i46）

## 11. Frozen Boundaries

逐目录 `git diff HEAD` = 0：`src/agents`（Director/Producer/Script Writer/Character Designer/Sub-Agent）、`src/ai`、`src/core`（Shot schema/project.json/prompt_builder）、`src/resources`、`src/scene_system`、`src/visual_profile`、`src/pipeline`、`src/storyboard`、`routers`、`services`。无 router、无 UI、无 Agent 集成、无生成接线。schema_version 保持 1（validity 不落盘；refresh 只写既有字段，§43）。

## 12. Tests

- **新增 50**（S3B.2）+ S3B.1 35 = 85 全绿（34.35s）
- §34 66 项全覆盖（部分合并在单测试函数内）；§41 并发 / §42 原子性 / §44 跨域集成（真实 Scene System 服务链：revoke→regen→review→approve，FakeClient/FakeVision 零外部模型，§40 合规）
- targeted 回归：Resources+Scene+Profile 300 passed；Agent 子集 425 passed/5 failed（与开工前逐条同构：3 not_configured 族 + case2/case4 ∈ baseline 清单，S3B.1 期已单跑实证）
- 分块全量：**1523 passed / 13 failed / 1 skipped**；13 条全部 ∈ `_pvp_failures_baseline.txt`（runtime_control×11 + not_configured×1 + case7_source_changed×1）；上一轮出现过的 cancel_race×2 时序 flake 本轮未触发（其单跑 12/12 实证记录在 S3B.1 报告）

## 13. Deviations（对 S3A / 8.md 指令的明确偏离）

1. **scene_instance 指纹不参与 freshness 比对**（8.md §18 要求的修正）：S3B.1 快照用整实例 revision（含整个 SceneState），粒度粗于 view-relevant 投影——若比对，"SIV 不可见的状态变化"会 false stale（违反 §K66）。裁决：快照字段保留（schema v1 不动、bind/refresh 照写，provenance），比对职责移交给 resolved SIV 的 Scene System verdict。这是对 S3A §19 表 scene_instance 行的修正。
2. **§44 草图的 "Profile 变 → refresh → READY" 不可直接成立**：P1 已把 profile 指纹嵌进 SIV basis——profile 变更使 Scene System 判 SIV stale（Scene verdict，§17 要求消费）。诚实链 = refresh 接受新 profile basis → validity 仍 NOT_READY（stale，不掩盖）→ Scene 侧 regen+reapprove → binding 再 refresh → READY。集成测试按诚实链断言。
3. **refresh 不因 SIV stale 而拒绝**：§22 拒绝清单未列 SIV stale，"stale but currently valid" 正是 refresh 的语义；refresh 后 validity 诚实重报（测试 f33/j_refresh_siv_reapproved 证明"refresh 成功但绝不假 READY"）。
4. 测试夹具勘误（非产品代码问题）：`patch_scene_state` 的 anchor_states 为整段替换语义，"不可见变化"测试须保留可见锚状态（h41/g39/k66 注释注明）。

## 14. Known Boundaries

- Composition nested revoke policy（内部 Look 被 revoke 时 Composition 是否连带降级）仍属 Resources 政策，本层只消费公开事实（e26 测试钉住边界）
- ShotReferencePack Compiler 未实现（S3B.3）；validity 的 resolved_dependencies 只含 id，无图片/无 prompt 片段（§28）
- Agent Integration / 生成接线 / HTTP router / UI / Human Production View 未实现（§29/§37）
- 无 auto refresh / auto regenerate / auto approve / auto rebind / orphan GC（§37）
- Resources 无 approved-basis 锚点（P1 §22.3），Look/Prop 的"更细 stale verdict"不存在，本层不发明（§11.5 即可用性检查 = approved 且真实可解析）

## 15. Recommendation

**可以进入 S3B.3 — ShotReferencePack Compiler。** validity 的 resolved_dependencies（尤其 resolved SIV id）与确定性 check 结构已是 pack 编译的输入契约；唯一待用户裁决的遗留议案（S3B.1 报告附录 A）：direct prop_bindings 的可选 character_name 字段（pack 需要把直接道具关联到持物角色）。

---

## PASS 条件逐项核销（§49）

UNBOUND/READY/NOT_READY 正确 ✓；actual dependency freshness 正确 ✓；no whole-project false stale ✓（§K 组）；Look/Prop/Composition/Scene/SIV/Profile stale 检测 ✓；Profile unspecified → NOT_READY ✓；unpinned 确定性/fail-closed ✓；read 零 mutation（字节级）✓；refresh 显式唯一 ✓；OCC 安全 ✓；原子 ✓；不改 Intent ✓；拒绝 invalid ✓；orphan 保留 ✓；legacy 不自动迁移 ✓；S3B.1 回归零 ✓；Resources/Scene/Agent 回归零新 ✓；Shot schema/project.json/Resources/Scene 语义不变 ✓；no Pack/Generation/Agent Integration/API/UI ✓。
