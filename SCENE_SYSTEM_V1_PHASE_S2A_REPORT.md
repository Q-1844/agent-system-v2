# Scene System V1 — Phase S2A（Location Visual Identity + Approved View Bank）报告

分支 `feat/scene-system-v1-s1`（延续 S1，8 commits：integrity hardening → schema v2 →
basis/prompt → generation → review → approval → HTTP+master → docs）。
S1 基线复现验证通过（§1 preflight：S1 25 tests + 资源八套件 144 + Agent 关键回归全绿）。
**不接 Agent、不改 Director/Sub-Agent/Shot；不生成 SceneState 剧情状态图（§29/§30 全程遵守）。**

验收线（§27）：真机 E2E——同一 Location 拿到 **2 个不同方向的真实 approved Views** ✅
（E2E r2 PASS，145s；r1 保留为诚实失败证据，见 §16）。

---

## 1. S1 integrity preflight（§6）

审计结论：A/B/C（anchor 被 view/layout/scene_state 引用时删除）service 已 fail closed
（`anchor_in_use`）但**缺测试**；D（Prop 被 Resources 删除后 read）是**真实缺口**；E 文档需对齐。

- D 实装：`read_scene_instance`/`list_scene_instances` 增加 prop 引用诚实标注——
  `missing_props`（可证明缺失）/ `props_lookup_unavailable`（resources.json 损坏时诚实
  不确定；不 crash、不假装存在、不静默清除 SceneState 数据）。
- E：router state 端点 docstring 明确——字符串字段 partial；`anchor_states/prop_placements`
  **整 dict 替换**（未传不动，OCC 保障并发）。
- 新测试 `tests/test_scene_system_s2a_integrity.py`（6 tests）：A/B/C fail closed + 磁盘
  零污染 + used_by 归因；D 两态标注；E 整替换语义证明。
- 单独 commit（`2789526`）后才进视觉生成（按 §6 指令）。

## 2. Schema migration decision（§7）

审计：LocationView 需新增 `generation_status`/`active_generation_id`（`reference_asset_id`/
`approval_status` S1 已有）。新 runtime 写入这些字段后，旧 S1 runtime 的 known-field 过滤
会 load→save **静默丢弃**它们 → **必须 bump schema_version 1→2**（拒绝为省 bump 接受
silent field loss）。

惰性迁移契约（同 Resources 1C5 已证明原则）：
- v1 文件可读（新字段默认值，in-memory 规范化为 v2）；纯 read 零写回（T3 以字节证明）；
- 第一次真实 mutation 才落盘 v2（幂等非破坏，T4）；
- 旧 S1 runtime（只支持 v1）面对 v2 **fail closed**（T6 以收窄支持表模拟证明）；
- ≥3 继续 fail closed（T5）。

## 3. LocationView visual state（§8）

`generation_status`（none/pending/generating/ready/failed，对齐 Resources）与
`approval_status`（unreviewed/approved/rejected）**两维度分离**：生成成功 ≠ approved；
review pass ≠ 自动 approved（T26/T30 双向证明）。
视觉状态四字段（generation_status/active_generation_id/reference_asset_id/approval_status）
为**协议管理字段**——`update_view` 可 patch 面收紧为视角定义（label/camera_zone/
look_direction/visible_anchor_ids），不允许手工 patch 绕过审批证据链（T8-schema 证明）。
`active_generation_id` 指向当前 committed generation（诊断与审批的 candidate 锚点）。

## 4. GenerationBasis（§9）

`compute_location_visual_basis`（`src/scene_system/visual_basis.py`）：纯函数式事实快照——
Location 身份快照 / 全部相关 Anchors 快照（含 relations）/ SetLayout 快照 / View 定义快照
（camera_zone/look_direction/visible_anchor_ids/label）/ 实际选中 approved 参考（含内容
sha256）/ prompt / model{provider,model_id,size_tier,ratio} / seed / candidate_count /
reference_selection_mode + explicit_ids（重算复现用）。

## 5. Location visual basis fingerprint（§9 核心）

`basis_fingerprint = sha256(canonical_json(basis))[:8]`。
🔴 Location 自己的 revision 不覆盖 Anchor/Layout/View——指纹必须覆盖全部视觉语义输入。
故意不含：时间戳、状态机字段（generation_status/approval_status——无害写不误判）、
裸 location_revision（同 Resources 1C5 教训）。
生成结束（阶段3）对当前 canonical **同参数重算**：参考按原模式（auto/explicit）对当前
状态重选——approved 参考图变化也触发 stale。测试证明四类变化全部触发
`stale_generation_basis`：anchor（T20）/ layout（T21）/ view 定义（T22）/ location 身份（T19）
+ 指纹覆盖单测（basis 套件）。

## 6. Prompt builder（§11/§12）

`src/scene_system/location_prompt.py::build_location_view_prompt`——确定性（同输入逐字节
同输出，T14；无时间/随机/LLM）。输出目标 = clean reusable environment reference：
same location identity / architectural consistency / stable materials / stable landmark
positions / requested camera zone / look direction / required anchors visible。
明确中性约束（no people/props/weather effects/damage/action；neutral even illumination）——
本轮是 Base Location identity，**不加** SceneState/天气/损坏/人物（§29）。
SetLayout 编译为语义方位短语（`north_up` 才解释南北；`x/y → west/central/east ×
north/central/south area`），anchor.relations 编译为 "X opposite Y"；
prompt 内自述 "semantic orientation only, not a 3D projection"（§12：hint 非 3D renderer）。

## 7. Reference selection strategy（§10）

- 第一张 approved view 之前：无参考，纯 identity+anchors+layout+prompt（T11）。
- 之后：优先 1 张最相关 approved LocationView（可见 anchor 交集降序 → view_id 升序）；
  仅当目标 view 的 required anchors 未被第一张覆盖时补第 2 张（对未覆盖贡献最大者）。
  绝不把所有 approved views 无脑全发。
- 双路：显式 `reference_view_ids`（逐个校验 approved/同 location/可用）或 auto；
  选择模式 + 结果全量记录进 basis。
- 同 location 有 approved 候选但 resources.json 不可读 → `resources_lookup_failed`
  fail closed（不静默退化为无参考生成——那会产出与已批准视觉身份脱节的图）。

## 8. Reference budget（§10/§13）

`MAX_VIEW_REFERENCE_REFS = 2` 硬上限（最少但足够）；显式超预算 → `reference_budget_exceeded`
（T13）；auto 模式结构性 ≤2。

## 9. Lock / single-flight（§13/§14）

三阶段协议（沿用 Resources 已验证模式）：
- 阶段1 短锁：view OCC（expected_revision）+ per-view single-flight + 参考选择 + basis
  快照 → `generation_status=generating`；
- 阶段2 **无锁**：`AgnesClient.generate_image`（复用现有 client，不复制 HTTP contract；
  T15 以锁探针证明 provider 等待期间 project lock 可被他人获取——引擎绝不持锁等模型）；
- 阶段3 短锁：重算 basis 指纹 → commit（ready + active_generation_id）或 stale 拒绝
  （状态诚实还原为 prev，候选留诊断目录）。

**single-flight 粒度 = same view_id**：飞行态检查**先于** entry OCC（飞行态是状态检查——
并发第二请求无论携带什么 revision 都得 `generation_in_progress`（§14 明确语义），不调
provider、不写假 record；T16）；不同 view 可并发——同 location 也不全局串行（T17，
两次 provider 调用证明）。

## 10. Recovery（§15）

- cancel cleanup（内联）：CancelledError → 尽力写诚实终态（failed + record error=cancelled）
  → **re-raise 永不吞取消**（T23）。
- startup orphan sweep（`src/scene_system/recovery.py`，api_server startup 挂载，与
  resources recovery 并列）：凡 `generation_status==generating` 必为上次进程遗留 → failed +
  `orphaned_by_process_restart` record（合成 gen_orphan_* id，basis=None 不臆造）。
  一次性确定性扫描；无 daemon、无轮询；幂等（T24/T25）。HTTP `POST recovery/run` 供运维。
- provider 失败诚实（T18/T18b）：status=failed + 真实错误进 record；无图不伪造候选。

## 11. Candidate storage（§16/§17）

`output/scene_system/locations/{location_id}/views/{view_id}/generations/{generation_id}/
candidate_NN.png`——stable id 定位，rename 不搬目录；**保存原始生成图，绝不 rembg**。
🔴 候选不进 Resources（§17）：unapproved candidate 只是 Scene 侧文件 + generation record
（T37：ready 阶段 resources.json 零条目）；只有 Review PASS + 显式 Approval 才注册。

## 12. Vision Review（§21/§22）

`src/scene_system/view_review.py`——真 Vision（`AgnesClient.chat_completion_vision`，
VISION_MODEL=agnes-2.5-flash 与 Resources review 同源；复用中性 `_extract_json/
_normalize_dim` helper；不 import 任何 Agent 模块，绝不让 Director Agent 判断）。

六维度：location_identity / **anchor_visibility（逐 anchor）** / anchor_consistency /
layout_consistency / view_consistency / visual_defects。不加 SceneState 维度（§29）。
对比策略（§22）：第一张 view = candidate vs 规格说明；后续 = candidate + 该候选生成时
使用的 1~2 张 approved references（来自 generation basis，与生成同一母版）+ 规格说明。
prompt 明确判据："是不是同一个地点"而非"两张图像不像"——材质亮度可随视角变；
门数量/窗型/主要 Landmark/布局关系不能漂移。
对比参考不可用时跳过该图（诚实记录 ref_ids_used）；review 零改 canonical（§24，
revision/approval 不动——专项测试证明）。

## 13. Deterministic overall（§23）

模型只给各维度观察证据 + 逐 anchor 明细；**overall 由系统代码推导**，模型自报仅记录
`model_overall` 不采信（T28 证明 fail+model_pass → needs_fix）。
- 关键维度（§23 四维度）任一 fail → needs_fix；
- `anchor_visibility`：预期 anchor 回报缺失/missing → fail；partially → uncertain；
  **有意加入 hard 集**（视角定义的核心承诺；报告 §19 deviations D7 说明）；
- 六维度全 pass → pass；concern/uncertain/维度缺失 → uncertain（fail closed 不判 pass；
  T28c/T28d/T28e）。
证据 append-only 到 `output/scene_system/review_records.jsonl`（§24 全字段，无 base64；
每候选独立一次 vision 调用）。

## 14. Approval（§25）

`approve_location_view`：只有当前 candidate 有**针对当前期望指纹**的最新有效 PASS review
才能 approve（T29 双向：无 pass / 非 pass 都拒绝；审查后语义变化 → `stale_approval_basis`
，T33a，Resources 零新增）。approve 是显式 mutation（T30：pass 不自动 approve）。
成功 = attach `reference_asset_id` + `approval_status=approved` + 重读验证（T31）。
`revoke_view_approval`：approval_status → rejected；**历史不删**——generation/review/
approval records 与 ReferenceAsset 全保留（reference_asset_id 保留为历史事实；T34）。
approval 事件流：`output/scene_system/approval_records.jsonl`
（approved/revoked/orphaned_reference_asset/aborted）。

## 15. Cross-canonical Resources protocol（§19）

scene_system.json 与 resources.json 是两个 canonical，**不假装 atomic transaction**。
Saga 三阶段：
- **A**（scene 短锁）：候选 committed + PASS 证据 + 期望指纹当前 + 文件在盘 + scene OCC；
- **B**（无 scene 锁）：经**冻结** Resources service `register_reference_asset`
  （source=scene_system / purpose=location_view / 真实 sha256；Resources OCC
  `expected_resources_document_revision` 独立传入；不改 Resources 任何 schema/语义，§18）；
  注册失败 → aborted 记录，无 orphan；
- **C**（scene 短锁）：重读 scene view，revision + active_generation 未变 → attach；
  已变 → **绝不 attach**，已注册 ReferenceAsset 记 `orphaned_reference_asset`
  （不偷删，approval_records 留 GC 线索）——不能为了避免 orphan 牺牲 canonical 正确性。
  T33b 以 B→C 竞态注入（saga 中途改 view）证明 orphan 路径。
Frozen Resources 边界（§18）：只调用其现有稳定 service；未改 schema/entity/ontology；
无需 BLOCKER。

## 16. Real E2E（§27）✅

真机两轮（driver `_e2e_scene_s2a.py`，scratch；真实 AgnesClient 经 PipelineController）：

**R1（project `__scene_s2a_e2e`，保留为诚实失败证据）**：View A 一次 PASS approved；
View B 三次全部被 Review 诚实 needs_fix（`anchor_visibility` fail：south_stairs 不可见）。
根因 = **我的锚点规格错误**（"穿地板舱口向下的楼梯"任何角度都画不出来），非管线/模型。
零篡改：三次 needs_fix 全部如实拒绝。

**R2（project `__scene_s2a_e2e_r2`，规格修正后重跑）— PASS（145s）**：
- Lighthouse Control Room：Location + 5 anchors（north_door/main_console/west_window_01/
  west_window_02/south_stairs）+ relations + SetLayout（north_up + placements）；
- View A「South-west to North-east」：attempt 1 生成 2 候选 → review PASS → approve
  → `ref_a9825e0db35f`（目检：环形控制室/黄铜仪表操作台围绕中央灯柱/铆钉铁门舷窗/
  拱窗/石墙木地板，无人物中性光；楼梯在南侧镜头后 correctly 不可见）；
- View B「North entrance to South」：生成 basis `refs=['view_7829076fbac2']`（= approved
  View A 作母版，§27 要求 ✓）→ attempt 1 review PASS → approve → `ref_8ab91372e4dd`
  （目检：楼梯梯段可见、地点身份与 A 一致）；
- 终态：同 Location **2 个不同方向 approved views**（{south, north_east}）+ Resources 2 个
  scene_system ReferenceAsset + 候选原始图在盘未 rembg + gen/review/approval records
  完整（2/2/2）。
**S2A 正链成立。** MODEL_LIMITATION 见 §21。

## 17. Tests（§28 T1-T39 全覆盖）

| 套件 | tests | 覆盖 |
|---|---|---|
| test_scene_system_s2a_integrity.py | 6 | §6 A-E（T6/T7 + anchor-state 删除 + missing_prop + patch 语义） |
| test_scene_system_s2a_schema.py | 8 | T2-T5 + 旧 runtime v2 fail closed + 视觉状态进 revision + patch 收紧 |
| test_scene_system_s2a_basis.py | 5 | T11/T12/T13/T14 + 指纹覆盖四类变化 |
| test_scene_system_s2a_generation.py | 12 | T15-T26（锁外 provider/单飞/并发/诚实失败/T19-T22 stale 全家/取消/恢复/幂等/不自动 approve） |
| test_scene_system_s2a_review.py | 8 | T27/T28a-e + 只读 canonical + no_candidates 零 record |
| test_scene_system_s2a_approval.py | 6 | T29-T34 + B→C 竞态 orphan 注入 |
| test_scene_system_s2a_master.py | 6 | T8/T35/T36/T37 + HTTP 全链 status mapping |
| （T1/T9/T10/T38/T39） | — | T1=S1 套件保持绿（回归运行）；T9/T10=S1 T8/T9；T38/T39=回归运行 |

## 18. Regression

| 套件 | 结果 |
|---|---|
| 全量合并运行（S1 25 + S2A 七套件 51 + 资源八套件 144 + Agent 关键 92） | **312 passed，0 失败**（commit 7-8 时点） |
| 资源冻结八套件（T38） | 144 passed = 冻结基线一致 |
| Agent 关键回归（T39）：agents + ai_7d_r story/batch + subagent_instance/tools_gate | 全绿零新增 |
| 真机 E2E | R2 PASS（§16） |

## 19. Deviations（有意偏离/强化，均已记录）

- **D1**（§6）`missing_prop` 采用 read 双标注（`missing_props` + `props_lookup_unavailable`）：
  resources 损坏时"诚实不确定"优于静默二值。
- **D2**（§14）single-flight 检查先于 entry OCC：飞行态是状态检查——保证 §14 明确语义
  （并发第二请求 = generation_in_progress，而非误导性 stale_revision）。
- **D3**（§7）`update_view` patch 面收紧：视觉状态四字段协议管理（S1 允许 patch
  reference_asset_id/approval_status——S2A 起会绕过审批证据链，故收紧）。
- **D4**（§21）review 对比参考取"该候选生成时 basis 记录的母版"而非重选：与生成同一
  母版最忠实于"是不是生成时对标的那个地点"；且 expectations 指纹含 ref_ids_used，
  approval 时自动重验。
- **D5**（§19）approve 需要双 OCC 期望值（scene view revision + resources document
  revision）：两 canonical 各自诚实 OCC，不做跨文件伪事务。
- **D6**（§25）revoke 落 `rejected`（而非 unreviewed）：显式人工否决语义；
  reference_asset_id 保留为历史事实。
- **D7**（§23）`anchor_visibility` fail 加入 overall hard 集（§23 列了四关键维度）：
  required anchors 不可见的图不能成为该视角的批准参考——视角定义的核心承诺。
- **D8** UI 未做（§26 系统优先；最小 debug 页留 S2B）。

## 20. Blockers

无。

## 21. MODEL_LIMITATIONS

- **R1 south_stairs 规格教训**（非模型限制，记录为规格设计约束）：锚点描述为
  "descending through a floor hatch"（穿地板舱口）时，图像模型三次尝试都无法呈现
  （结构上不可见）→ **anchor 的 description 必须描述可见视觉特征**（"open arched
  opening + full flight of steps clearly visible" 后一次通过）。该约束已由 E2E 实证，
  建议写入未来 Agent 写入锚点时的规范。
- 同一房间多视角下窗数量偶有漂移（规格 3 扇西窗，个别候选画 4 扇）；review 以
  "same location" 判据整体评估未阻断。若未来需要严格数量一致性，建议在 anchor
  description 中显式写数量并在 review prompt 中强调（当前已写数量，模型服从度有限）。
- R2 中两视角均一次 PASS，未触发 3 次重试上限；免费档 Agnes 无 429 发生（418s/145s
  两轮共 10 次图片生成 + 10 次 vision 调用）。

## 22. Recommendation for S2B

1. **最小 Location debug/workspace UI**（§26 推迟项）：Location/Anchor/Layout/View 树 +
   候选图点选 + review 结果展示 + approve/revoke 按钮；纯前端 + 现有 HTTP API。
2. **SceneInstance Visual State（§29 下一阶段）**：SceneState（天气/时间/损坏/道具）
   编译进 prompt 的状态图层 + 与 base view 的 i2i 组合 + `scene_state_consistency`/
   `lighting_weather_consistency` review 维度。
3. **Shot binding（S1 报告 §20.3 延续）**：Shot.scene_instance_id / view binding 字段
   （改 Shot schema 属独立阶段，需单独评审）。
4. **orphan ReferenceAsset GC**（§19 遗留）：低频清扫无引用的 scene_system 资产。
5. **Agent Integration 仍后置**：LocationView approved 后，Director/Sub-Agent 读工具
   （scene.view.list 含 approved 参考指针）才有真实价值——建议 S2B 之后单独立阶段。

---

## Commits（本轮 8 个）

| Commit | 内容 |
|---|---|
| `2789526` | S1 integrity hardening（§6：missing_prop 标注 + A/B/C 测试 + patch 语义文档） |
| `f4af665` | schema v2 惰性迁移 + LocationView 视觉状态字段 + patch 收紧 |
| `4d33a69` | GenerationBasis + 视觉语义指纹 + prompt builder + 参考确定性选择 |
| `178c03b` | 生成引擎三阶段 + per-view single-flight + records + recovery + api_server 挂载 |
| `2999352` | Vision Review 六维度 + 确定性 overall + review_records |
| `9051e0a` | Approval saga（cross-canonical）+ revoke + approval_records |
| `e898618` | HTTP 视觉协议端点 + T1-T39 主套件 |
| （本 commit） | 本报告 |

## 停止点确认（§32）

已完成：LocationView Generation + Review + Approval + 2-view real E2E（R2 PASS）。
未做（按停止条件）：SceneState Visuals / Shot binding / CharacterPlacement /
Continuity engine / Agent integration。到此停止，等确认。
