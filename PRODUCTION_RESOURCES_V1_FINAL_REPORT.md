# Production Resources V1 — Final Report（Phase 1C5：CharacterReferenceComposition 收口与冻结）

> 阶段：**Phase 1C5 — Final Composition Closure & Freeze**（Resources V1 最后一轮功能建设）
> 分支：`feat/resources-v1-phase1c1`（自 `6cf1634` 起 11 commits + 本轮 8 commits，均未 push）
> 前置报告：Phase 1A 审计 + 修复阶段2/ 下七份报告（1B / 1C1 / 1C1.1 / 1C1.2 / 1C2 / 1C3 / 1C4）
> 本轮执行文档：`详细流程文件/修复阶段/3.md`（T1-T40 / §1-§24 逐条对应）
> 纪律：Composition 是 **Reference（approved visual composition plate）**，不是故事事实；
> Scene / Location / Scene State / spatial continuity 全部留给 Scene System（§19 禁令全程遵守）。

---

## 1. V1 最终 ontology

```
Shared Production Library（shared_library/resources.json，schema v2，版本化母版）
        │  显式 publish（copy-on-publish + version+1）/ import（copy-on-import + lineage）
        ▼
Project Production Resources（projects/{p}/output/resources.json，schema v2）
        │
        ├── Character（legacy canonical，name 即身份，零改动；三视图 = identity refs）
        ├── Clothing（clothing_id）
        ├── CharacterLook（look_id；FK→Character.name、FK→Clothing；candidates + approved refs）
        ├── Prop（prop_id；candidates + approved refs【1C5 补齐 review/approve 语义】）
        ├── CharacterReferenceComposition（comp_id；FK→Character.name + CharacterLook）
        │       └── CompositionPropBinding[]（FK→Prop；relation/anchor/visual_state_note/required_visibility）
        └── ReferenceAsset（ref_id；全文档唯一一份，所有实体只存 id 引用）
```

## 2. Character / Clothing / Look / Prop / Composition 关系

- **Look** 是"某角色某套外观"的可复用参考（1C1 生成、1C2 审批）。
- **Prop** 是独立道具资源；1C5 之前只有 CRUD+绑图，**审批语义缺失**（本轮 §7 最小闭环补齐）。
- **Composition** = `Character + Approved CharacterLook + 1~3 Prop + 明确视觉关系` 的
  提前批准组合参考。生成 Composition 候选时，输入**只**使用 Look 的 approved references
  与每个 Prop 的 approved references——审批链（Look approve ← 1C2；Prop approve ← 本轮）
  是 Composition 可生成的**前置硬条件**。

## 3. resource vs story-state 边界

| 维度 | Resources（本轮） | Story State（未来 Scene System） |
|---|---|---|
| "Alice 右手拿木剑" | 这张 approved plate 上的视觉呈现 | 谁在第几个 Scene/Shot 拿什么 |
| relation/anchor | 有限 enum（视觉关系） | 世界坐标 / blocking / screen direction |
| visual_state_note | 这张图应有的状态（sheathed） | 掉落/交接/损坏时间线 |
| 存储位置 | resources.json / shared_library | scene/shot canonical（未实现） |

代码层强制：Composition dataclass 无任何 timeline/scene/shot 字段；§19 禁令清单
（Prop holder timeline / ShotReferencePack / Director 自动绑定 / inventory / 物理模拟…）
全部未实现。

## 4. Composition schema（`src/resources/models.py`）

```python
CharacterReferenceComposition:
  composition_id        # comp_<uuid12>，稳定身份
  scope                 # project | shared
  label                 # 显示名（rename 不变 id）
  character_name        # FK → Character.name（身份，不可改）
  look_id               # FK → CharacterLook（必须 approved，create 时硬校验）
  prop_bindings         # List[CompositionPropBinding]（1~3）
  pose_note             # 姿态备注
  composition_description
  reference_asset_ids / approved_reference_asset_ids / cover_asset_id
  generation_status     # none/pending/generating/ready/failed（独立维度）
  approval_status       # unreviewed/approved/rejected（独立维度）
  origin                # ResourceOrigin（shared lineage）
  created_at / updated_at
```

## 5. binding schema

```python
CompositionPropBinding:
  prop_id               # FK → Prop
  relation              # 有限 enum: held|carried|worn|attached|near
  anchor                # 有限 enum: right_hand|left_hand|back|waist|neck|beside_character
  visual_state_note     # 如 "sword sheathed"（只描述这张图，无时间线）
  required_visibility   # required|optional（review 维度 required_prop_visibility 的依据）
```

不做万能 free JSON（执行文档 §4）；非法 enum 值在 create/update 时 `invalid_binding` 拒绝。

## 6. Generation basis（Composition 专属，`generation.py::compute_composition_basis`）

- 记录：composition_id / character_name + 身份指纹 + 描述快照 / **naked look_revision**
  （§8 明确要求"Look 更新 → stale"；look 不被 composition 流改动，无自触发）/
  look approved refs（ids + 内容 sha256）/ 每 prop：**prop_revision** + relation + anchor +
  visual_state_note + required_visibility + approved ref ids + 内容 sha256 / pose_note /
  composition_description / selected_input_refs（含 role）+ input_budget / prompt /
  model{provider,model_id,size_tier,ratio} / seed / candidate_count / basis_fingerprint。
- **语义指纹口径**：`basis_fingerprint = sha256(canonical_json(basis))[:8]`；**不含**
  label / 状态机字段 / 时间戳（display 改名等无害写不误判 stale，沿用 1C1 敏感度设计）；
  `started_at` 在指纹计算之后追加（重算比对不受影响）。生成结束同参数重算，指纹不同 →
  `stale_generation_basis` 拒绝 attach，候选图留诊断目录，composition 状态诚实还原。

## 7. Reference selection（§12 确定性最小选择 + 硬预算）

优先级：① Look approved hero（cover ∈ approved → cover，否则 approved 第一个）
→ ② 每 binding 的 Prop approved hero（同规则，binding 顺序）
→ ③ 额外 Look angle ≤1 张（**仅当 bindings ≥ 2** 且存在第二个 approved look ref——
多道具组合注意力分散风险更高 → 双 Look 锚定）。
硬预算 `MAX_COMPOSITION_INPUT_REFS = 6`（实际最大 1+3+1=5，上限为防御值）；超预算先截 ③。
Selection（role/asset_id/url/local/sha256）全量记录进 `selected_input_refs`。

## 8. single-flight

与 1C1.1 同构：阶段 1 短锁内、entry OCC 之后检查 `composition.generation_status == generating`
→ `generation_in_progress`（不调模型、不写 record、不改 canonical）。检查与 generating 翻转
在同一把 `project_write_lock` 临界区内串行化，无 TOCTOU。**粒度 = same composition_id**
（T11 实证不同 composition 并发生成互不阻塞，无全局锁）。

## 9. recovery

- **cancel cleanup**：provider await 中 CancelledError → `_fail_close`（诚实终态 failed +
  record error=cancelled + 已到手候选留诊断目录）→ **re-raise 永不吞取消**（T16）。
- **startup orphan sweep**：`recovery.py` 扩展扫描 compositions——孤儿 generating → failed +
  record（`composition_id` 字段、合成 `gen_orphan_*` id、basis=None 不臆造）；幂等（无孤儿
  零写入，T18）；schema 损坏 fail closed 不阻塞其它项目。api_server startup 钩子零改动
  （`recover_all_projects` 内部自动覆盖）。

## 10. Visual Review（`composition_review.py`，§15 六维度）

| 维度 | 来源 |
|---|---|
| character_identity | 模型直判（normalize） |
| look_consistency | 模型直判 |
| prop_fidelity | **派生**：逐 binding `object_correct` 聚合 |
| prop_binding_correctness | **派生**：逐 binding `holder_correct` + `anchor_correct` 聚合 |
| required_prop_visibility | **派生**：required binding 的 `visibility != visible` → fail |
| visual_defects | 模型直判 |

- **逐 binding 检查**（§15 核心）：不能只查"图里有剑和罗盘"——要查"是 Wooden Sword 本身
  （对照其 approved reference）、由正确人物呈现、在 right_hand"。
- **多图输入**：IMAGE 1 = candidate，其后 approved look refs + 逐 prop approved refs
  （图序确定性，prompt 逐一标注；真机 4 图一次通过）。
- **overall 确定性推导**：任何 fail → needs_fix；全 pass → pass；其余 → uncertain。
  模型自报 overall 只留档 `model_overall`。
- **fail closed**：预期 binding 在回复中缺失 / 字段不可解析 → 该子判定按 **fail**
  处理（"回复里没有"本身就是 missing-prop 缺陷信号）；审查证据进 `review_records.jsonl`
  （operation=composition.review_candidates，composition_id 流），canonical 零写入。

## 11. Approval（§16）

- `approve_composition_candidate`：asset ∈ 候选集 → **门槛：最新审查证据 overall=pass**
  （无证据 / needs_fix / uncertain 全拒，HTTP 409 `review_not_passed`）→ 追加 approved +
  approval_status=approved + cover（set_cover 语义同 Look）→ save 后重读 verified。
- `revoke_composition_approval`：移除 approved；cover 清空；空则回 unreviewed；候选保留。
- **review pass ≠ 自动 approved**（生成 commit 后 approved 恒空，T19）；**无 pass 证据不能
  approve**（双向硬门槛）。Prop 侧同构（`approve_prop_reference`，§7）。

## 12. Shared publish/import（§17）

- **publish_composition**：显式依赖链——Character / Look / Look 的 Clothing / 全部绑定 Prop
  必须已发布，缺哪个报哪个（`dependency_not_published` + missing_dependencies 清单，
  不隐式捆绑，与 look→clothing 哲学一致）；共享条目 FK 全量 remap 到 shared id；候选媒体
  copy-on-publish（asset_map 复用，republish 同 shared id version+1）；approved/cover/
  generation_status 按发布快照 remap。
- **import**：目标项目须有同名 Character（`character_not_found`）；自动捆绑 look（及其
  clothing）+ 各绑定 prop，**origin 查重**（已有指向同一共享条目的副本 → FK 直连不重复
  导入）；全 id remap（look_id/prop_ids/候选 refs/approved/cover）；origin+
  shared_version+imported_at 完整 lineage；隔离由 copy-on-import 构造保证（T32/T33 字节级）。
- **🔧 顺带修复 1C3 既有缺口**：publish/import 此前不 remap `approved_reference_asset_ids`
  /cover（Look/Clothing/Prop 同样受影响）。1C3 时无害，但 Composition 生成直接消费
  look.approved refs——缺口从无害变为 load-bearing，本轮全部补齐（发布侧 remap + 导入侧
  remap + approved 清空回 unreviewed 不留悬空审批）。

## 13. UI（§18 最小）

`/resources` 页（未重写页面）：Character/Look tab 新增 **Approved Compositions** 区——
创建组合（选 approved Look + 1~3 道具，每道具 relation/anchor 下拉 + 状态备注）、
生成候选、视觉审查、批准/撤销、发布到共享库（依赖未发布时按 character→clothing→prop→look
优先级引导链式发布）；候选卡显示**六维度徽标 + 逐 binding 明细（对象/持有/位置/可见性）+
审查摘要**，不只红绿灯。Prop tab 新增参考图审查徽标 + 审查/批准/撤销按钮。

## 14. mock tests（`tests/test_resources_phase1c5.py`，53 条全绿）

| 执行文档 | 测试 | 结果 |
|---|---|---|
| §5 schema | v1 惰性迁移 ×4（纯读零写回/首写升级/round-trip/未知 fail closed）+ shared v1 ×1 | ✅ |
| T1 | create composition（2 props）| ✅ |
| T2 | missing character → character_not_found | ✅ |
| T3 | non-approved look → look_not_approved；角色不匹配 → look_character_mismatch；不存在 → look_not_found | ✅ |
| T4 | missing prop → prop_not_found | ✅ |
| T5 | prop 无 approved ref：create 放行、**generate 拒绝** prop_reference_not_approved（状态未动） | ✅ |
| T6/T7 | 1/2 props 合法；4 个/0 个 → invalid_prop_count | ✅ |
| T8 | duplicate prop binding / 非法 relation / 非法 anchor → 拒 | ✅ |
| T9 | rename id 不变 | ✅ |
| T10 | same-composition single-flight（B 不进 provider、canonical 不被触碰） | ✅ |
| T11 | different compositions 并发 | ✅ |
| T12 | 生成期间不持写锁（lock spy） | ✅ |
| T13 | 生成中改 Look → stale_basis 拒绝 commit + 状态还原 + 可重试 | ✅ |
| T14 | 生成中改 Prop → stale | ✅ |
| T15 | 生成中改 binding → stale | ✅ |
| T16 | cancel cleanup（re-raise + failed + record + 可重入） | ✅ |
| T17/T18 | startup orphan sweep（compositions）+ 幂等零写入 | ✅ |
| T19 | 生成 commit 不自动 approve | ✅ |
| T20-T24 | 六维度逐项（identity fail/look concern/prop fidelity/holder+anchor/missing prop） | ✅ |
| T25 | overall 确定性推导（纯函数直证，缺失≠pass） | ✅ |
| T26-T28 | approve 三态门槛 → pass 批准 → revoke 全语义 | ✅ |
| T29 | publish 全依赖链 + 媒体复制 + republish version+1 | ✅ |
| T29b | dependency_not_published（缺失清单四类齐全） | ✅ |
| T30/T31 | import + remap 全链（look/props/refs/approved/cover/origin/状态快照） | ✅ |
| T30b | 缺 Character 拒绝 + import OCC stale | ✅ |
| T32/T33 | A/B 隔离 + shared 字节级不变 + C 新导入 + 查重复用 | ✅ |
| T34 | 重启持久化（全新实例读回全关系） | ✅ |
| T35 | canonical/record 无 base64 | ✅ |
| T36 | project.json 字节不变（composition 全链路） | ✅ |
| 附加 | 预算选择（roles/顺序/上限）、prompt 语义与确定性、basis 字段、delete_prop 引用保护、detach 清理 approved、多图契约（4 图 + IMAGE 标注） | ✅ |

既有白名单契约测试同步扩展（+14 端点：3 prop 审批 + 3 comp CRUD/generate + 3 comp 审批
+ …）；Phase 1B-1C4 共 91 条测试逐条未动、全绿。

## 15. real E2E（§20，driver `_e2e_resources_1c5.py` + `_e2e_resources_1c5_shared.py`，throwaway 项目保留作证据）

隔离项目 `__res_1c5_e2e`（Alice = saved_characters/火焰机甲 真实三视图 PNG；
coat_ref 真实服装参考）+ `__res_1c5_e2e_c`（导入验证）：

| 步骤 | 真实结果 |
|---|---|
| Character Alice | 三视图 PNG 经 ProjectStore 正常路径就位 ✅ |
| Look "Alice · Black Coat" | 真实生成 2 候选（4 data URI 多参考 i2i）→ 真实 vision 审查 **双候选 pass**（一次通过）→ approve ✅ |
| Prop Wooden Sword | 真实 t2i 参考图 → 真实审查 **pass**（fidelity/clarity/defects 全 pass，一次通过）→ approve ✅ |
| Prop Brass Compass | 同上，一次通过 → approve ✅ |
| **Composition A（双道具）** | 真实生成 2 候选 → 真实六维度审查：**第一轮第一个候选 overall=pass，六维度全 pass**；逐 binding："Wooden Sword correctly held in right_hand"、"Brass Compass correctly held in left_hand; open lid, visible"——**右手/左手 anchor 全部正确，无需重试** ✅ |
| Composition B（1 prop） | 第 1 轮 uncertain/needs_fix（审查如实指出剑柄细节与身份问题）→ 第 2 轮出 pass 候选 → **approve 成功**（正链证明：generate → review PASS → approve）✅ |
| Shared publish | 全依赖链（character→clothing→look→props→composition）真实执行；approve 后 republish v2，共享条目 approved/状态快照正确 ✅ |
| import + remap | 全新项目 C 导入 character + composition：**T31 remap 全链 9/9 PASS**（composition/look/props/candidate refs/approved/cover/origin/状态快照/捆绑 look approved refs 全部解析）✅ |

**诚实记录**：主 driver 首轮 8 项检查 7/8——唯一 FAIL（T31）根因是 **driver 流程缺陷**
（发布 Composition A 前漏了 approve，发布快照如实记录 unreviewed → 导入方 approved 为空），
**产品代码零缺陷**；补跑脚本以既有真实 pass 证据执行显式 approve → republish → 新项目
导入 → 9/9 全过。无任何数据伪造。

## 16. regression

| 套件 | 结果 |
|---|---|
| 资源八套件（1B/1C1/1C1.1/1C2/1C3/1C4/1C5） | **144 passed**（1C5 新 53 + 既有 91 零改动） |
| 关键 Agent 套件 14 文件（Character Designer/Director×3/Producer×2/Sub-Agent×3/Script Writer×3 等） | **223 passed + 1 skipped**；2 例 `test_not_configured_soft_fail` 为基线已知套件内自扰（1C1.1 报告 §6 同族），单跑复绿 ✅ |
| 全量 pytest（T37） | **34 failed / 1309 passed / 1 skipped（45:31）**——总收集数 1344 = 上一冻结点 1222 + 本轮起累计新增 122（逐数吻合，收集完整）；失败数与 1B 冻结点基线（34）**完全一致**，失败族全部为已知家族（d3 端口族 ×11 / autopilot 族 / shot_request_rules ×5 / interface ×2 / not_configured 自扰族）；可疑文件 11 个 51 条单跑复绿 + `test_case7_source_changed_blocked` 为已记录 AI-5A 既有 timing flake（基线 stash 亦失败）→ **零新增回归** |
| vite build（T39） | ✓ 16.51s |
| vue-tsc（T40） | 本轮触碰文件（views/resources、api/index.ts）**零错误**；src 侧 6 个既有债（1C4 已记录）+ node_modules 声明问题，均非本轮引入 |

## 17. MODEL_LIMITATIONS

**无**。两道具组合（右手木剑 + 左手罗盘）在第一轮生成即通过六维度逐 binding 审查
（§20 预留的 3 次重生成额度未用完，未触发 MODEL_LIMITATION 判定）。
观察（非限制）：单道具 Composition B 第 1 轮出现过 uncertain/needs_fix（审查者如实指出
剑柄细节偏差与单候选身份漂移）——审查波动性与 1C2 报告记录一致，人工 approve 终审
架构（证据 + 显式批准）正是为此设计。

## 18. known issues

1. **孤儿 generating 的同进程 UI 提示**：sweep 在 startup 一次性执行，运行中无 daemon
   轮询（设计使然，§10 明令不加）；运行中若进程被 kill，下次启动恢复。
2. **Composition 无 delete API**：与 Look 无 delete 同口径（V1 冻结面最小化）；错误绑定
   可通过 update_composition 替换 prop_bindings 纠正。
3. **Character delete 不级联 Composition**：沿用 legacy Character 域既有债（1B 报告 §15
   #2）；Composition 生成前重验证会 fail closed（character/look/prop 缺失即拒），不会
   产生坏产物。
4. **导入不查重复导入 composition 本体**：同一条目可多次导入产生多份独立副本（与 1C3
   look/prop 导入同语义，copy-on-import 下合法；依赖侧已有查重）。
5. **审查者 run-to-run 波动**（1C2 已记录）：temperature=0.2 下 uncertain↔pass 波动存在；
   门槛取"最近一次证据"+ 人工终审，不为稳定而加手段。

## 19. final file map（本轮新增/修改）

```
src/resources/
  models.py               # +CharacterReferenceComposition/CompositionPropBinding/enum×3 + schema v2
  store.py                # +get_composition/list_compositions
  service.py              # +list/read_composition + read_prop/look latest_reviews
  write_service.py        # +Composition CRUD/生成/审批 + Prop 审批 + detach 扩展 + delete_prop 保护
  generation.py           # +composition basis/selection 预算/save_composition_candidate_files
  composition_prompt.py   # 新增：确定性组合 prompt builder
  prop_review.py          # 新增：Prop 参考三维度审查 + 证据
  composition_review.py   # 新增：Composition 六维度审查 + 逐 binding + 多图
  recovery.py             # +compositions 孤儿 sweep
  generation_records.py   # +prop_id/composition_id 过滤
  shared_library.py       # +compositions 段（schema v2）+ publish/import + approved remap 修复
routers/resources.py      # +17 端点（prop 审批×3 + comp×8 + 错误码）+ composition GET×2
api_server.py             # 零改动（recovery 自动覆盖、静态挂载已有）
src/views/resources/index.vue  # +Compositions 区 + Prop 审查批准 UI
src/api/index.ts               # +40 资源域方法（1C4 29 + 本轮 11）
tests/test_resources_phase1c5.py  # 新增 53 条
tests/test_resources_phase1b.py   # 白名单契约 +14 端点（唯一改动）
```

## 20. commits（feat/resources-v1-phase1c1，均未 push）

| # | commit | 内容 |
|---|---|---|
| A | `783e503` | Composition typed models + resources.json schema v2 惰性迁移 |
| B | `89a52ec` | Prop Reference Review + Approval 最小闭环（§7） |
| C | `a90555f` | Composition CRUD + 三阶段生成核心 + single-flight + recovery |
| D | `38994fd` | Composition 六维度 Visual Review + Approval |
| E | `fb05851` | Shared publish/import + approved remap 修复（1C3 缺口） |
| F | `b2f5705` | 最小 Composition UI + Prop 参考审查/批准 |
| G | （本次） | 真机 E2E（driver 为 scratch 不入库；证据项目 `projects/__res_1c5_e2e*` 保留） |
| H | （本次） | 本报告 + FREEZE 文档 + .gitignore 补 shared_library/（运行时数据，与 projects/ 同惯例）+ 执行文档 3.md 入库 |

每个 commit 自带当块测试且验证通过后提交（commit-per-verified-step 惯例）。

## 21. V1 frozen surface（§22）

**Production Resources V1 冻结范围**：
Character（legacy）· Clothing · CharacterLook · Prop · ReferenceAsset ·
**CharacterReferenceComposition** · Generation · Generation Recovery · Visual Review ·
Approval · Shared Library · Resources UI。

schema/API 冻结点：resources.json & shared_library schema_version=2；REST 端点全集 =
1B 9 GET + 1C1 写 9 + 1C2 审批 3 + 1C3 共享 5 + 1C4 Prop 5 + 1C5 新增 17（见
`routers/resources.py` 与白名单契约测试）。

## 22. explicit non-goals（§19/§22，永久排除在 Resources V1 之外）

Prop holder timeline / Shot holder state / Scene State / Location / Spatial Anchor /
Set Layout / screen direction / character blocking / ShotReferencePack / Director 自动
绑定 / Scene Agent integration / Expression / Performance Reference / Prop damage
timeline / inventory system / 物理模拟——全部属于 Scene System V1 或更高层。
"Wooden Sword held by Alice" 只是这张 approved plate 的视觉呈现，**不是** Prop 永久 ownership。

---

**本轮停止。Production Resources V1 功能建设正式收口，等待用户确认后打 frozen tag。**
（不开始 Scene System；不修改 Scene 数据模型；不碰 Director / Scene Sub-Agent；不自行 push。）
