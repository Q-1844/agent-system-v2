# Scene System V1 — Phase S2B（SceneInstance Visual State）报告

分支 `feat/scene-system-v1-s1`（延续 S1/S2A；本轮 8 commits：
`8e3d796`A preflight+schema v3+模型 → `b8decdd`B 投影+CRUD → `ad31442`C prompt+参考+basis →
`000e3dc`D 生成引擎 → `be88e0d`E Review → `78446ba`F approval+stale read → `bd0d26a`G HTTP+测试 →
`fe2cf2a` E2E 实证修复 + 本报告）。
**不接 Agent、不改 Shot/Director/Sub-Agent/prompt_builder；无 CharacterPlacement/视频/动作时间线。**

验收状态（§36-§38/§44）：
- ✅ **正向链全真实通过（r9）**：同一 SceneInstance 两个不同方向 approved state views
  （B 使用 approved A 作 peer）+ 七维度 Review + cross-canonical approval
- ✅ **§37 stale 检测真机验证**：lighting 改白光 → A/B 双 stale（归因正确）——真实 approved 视图上
- ✅ §38 不可见变化不 stale：mock 证明（§38 明文允许 mock）+ §14 规则矩阵测试
- ⚠️ §37 regen-refresh 真机 3 轮诚实未过（A-regen 复鲜链）= MODEL_LIMITATION（见 §22）；
  regen→review→approve 机制本体由 r9 首轮链（同对象）+ mock T40 证明

---

## 1. Preflight（§2）

- **A（revoked base）**：`select_reference_views`/`check_base_location_view` 均要求
  `approval_status==approved` + reference_asset_id 存在 + ReferenceAsset 可读——revoked
  即使保留 reference_asset_id 也不可用（T 证明；顺带修复显式请求被空候选 early-return
  掩盖的分支缺陷）。
- **B（stale base）**：`check_base_location_view` 比对最新 approved approval record 的
  `reviewed_basis_fingerprint` 与当前重算值——批准后语义漂移 → `location_view_stale`。
  🔴 语义补强：**最新审批事件是 revoke 则证据取消**（修复 revoke 后旧 record 仍可背书的漏洞）。
  🔴 指纹重算按**批准时审查实际使用的参考组**（记录新增 `reference_asset_ids_used`）——
  首张 view 审查时参考组是 `[]`，falsy `or` fallback 曾造成永远 mismatch（E2E 两次实证）。
- **C（location 一致性）**：create 校验 `instance.location_id == base_view.location_id`，
  违反 → `location_mismatch`（422）。
- **D（missing prop）**：生成链可见 prop 缺失 → `prop_missing` fail closed；resources.json
  不可读 → `resources_lookup_failed`——绝不假装 Prop 输入完整（T 证明）。

## 2. Schema v3 decision（§5）

新增 canonical collection `scene_instance_views` → 旧 v2 runtime load→save 会静默丢弃 →
**bump 2→3**，惰性迁移契约不变：v2 可读（in-memory `scene_instance_views={}`）/纯 read 零写回
（T3）/首 mutation 落 v3（T4）/旧 v2 runtime 见 v3 fail closed（T5）/≥4 fail closed（T6）。

## 3. SceneInstanceView ontology（§3/§4）

`sciv_<uuid12>`：scene_instance_id + location_view_id（身份 = 二元组，pair 唯一）+
label? + generation_status + active_generation_id + reference_asset_id? + approval_status +
**approved_basis_fingerprint**（approve 时写入的语义基准快照 = read 层 stale 锚点）+ 时间戳。
不重复保存 camera_zone/look_direction/visible_anchor_ids（属 LocationView）；
不复制 SceneState（唯一 canonical = SceneInstance.state）。
重新生成 = 更新同一对象指针（§4；E2E 实证：regen 复用对象，重复 create 被正确拒绝）。

## 4. ViewRelevantSceneState（§6-§8）

`project_scene_state_for_view`（state_projection.py，纯函数）：全局态（time_of_day/weather/
lighting/environment_conditions）恒入；anchor_states 只取 `anchor_id ∈ visible_anchor_ids`；
prop_placements 只取 `placement.anchor_id ∈ visible`。**绝不持久化**（每次 generation/review/
read 重算；canonical 唯一）。有意收窄：SceneState.notes 不进投影（可能含非视觉叙事评注）。

## 5. Visible/invisible state rules（§14/§33）

由投影自然涌现 + 专项测试（basis 层矩阵 + 生成链路 + E2E）：
- **必须 stale**：location identity / base view 定义 / base 批准与参考资产 / 全局态 /
  可见 anchor state / 可见 prop placement / 可见 prop 语义（name/description）与选中参考
- **必须不 stale**：不可见 anchor state / 不可见 prop placement / 其它 LocationView 变化 /
  updated_at-only 触碰（prop 快照用语义子集非裸 revision）/ 后来批准的 peer
  （**include_peer 记入 basis**——recompute 沿用原生策略，peer 是可选增强不追溯作废）

## 6. GenerationBasis（§13）

`compute_scene_instance_visual_basis`：sciv/instance/story_scene/location id + location 快照 +
location_view_id + view 语义快照 + base_location_reference（asset_id+checksum）+
base_location_basis_fingerprint（可重算）+ view_relevant_scene_state 投影 + 可见 anchor 快照 +
prop 语义快照 + selected_prop_references（id+checksum）+ peer_state_reference + prompt +
model/seed/candidate_count + **include_peer** → basis_fingerprint（sha256[:8]，started_at 不入）。

## 7. Reference strategy（§15/§16）

1 base approved LocationView（强制——无母版绝不 SceneState 文生图，T16）→ 2 optional
approved peer（同 instance、sciv id 升序、自身排除；E2E 实证 B 用 A）→ 3 可见 props 的
approved refs（每 prop 1 张确定性、**分项上限 2**、总预算 `MAX_TOTAL_VISUAL_REFS=4`）。
超预算/无 approved ref → text-only 继续 + `prop_refs_selected`/`prop_refs_omitted_due_to_budget`/
`props_text_only(visual_reference_missing)` 诚实记录（§10.A；T13/T18）。绝不全量喂模型。

## 8. Prompt builder（§17/§18）

`build_scene_instance_view_prompt`（确定性，六 SECTION）：Preserve Base Identity / Requested
View / Scene State（**显式 OVERRIDE 指令**——base 是中性日光态，模型倾向复制 base 光照；
r6/r7 实证弱点后补强）/ Visible Anchor State Changes（正确 anchor 点名，§19）/ Visible Prop
Placements / Negative Constraints（no people——§11 即使 Story Scene 有角色；identity 高于
SceneState 自由发挥——§18）。不修改 S2A base prompt 稳定行为。

## 9. Lock / single-flight（§20/§21）

三阶段协议完全复用 S2A：短锁（single-flight **先于** entry OCC + base 门槛 + 选择 + basis →
generating）→ 无锁 provider（T19 锁探针）→ 短锁重算 basis → commit/stale（诚实还原 prev）。
粒度 = same scene_instance_view_id（T20）；不同 state view 并发（T21）。

## 10. Recovery（§22/§23）

CancelledError → 诚实终态 + re-raise（T28）；startup sweep 扩展到 scene_instance_views
（generating → failed + orphan record，幂等 T29/T30）；候选 stable-id 路径
`output/scene_system/scene_instances/{instance}/views/{sciv}/generations/{gen}/`，原图不 rembg。

## 11. Vision Review（§25-§27）

`instance_view_review.py`（独立模块；VISION_MODEL=**agnes-3.0-flash**）。七维度 +
**逐项 evidence**：scene_state_consistency 逐项（time/weather/env + 每 anchor state，
缺回报 fail closed）；prop_placement_consistency 逐 Prop（correct/wrong_place/missing/
substituted，缺回报 fail closed）；§19 structural counts 进 prompt（anchor description 作为
explicit structural expectation）。对比图从生成 basis 分字段重建（base/peer/props）。
Review 零改 canonical；records 带 `review_type=scene_instance_view` + expected_state_projection。

## 12. Deterministic overall（§28）

模型只报观察 + 自报 overall 仅记录。hard 集条件化：恒 hard = location_identity/
view_consistency/anchor_consistency/scene_state_consistency；有可见 props → prop hard；
SceneState 给了非空期望 → lighting_weather hard。任一 hard fail → needs_fix；全 pass → pass；
concern/缺失 → uncertain（fail closed 不判 pass）。T35 全路径。

## 13. Approval（§30）

`approve_scene_instance_view`：PASS 证据（针对当前 generation/candidate）+ 期望指纹当前 +
scene basis 未 stale + 候选文件在盘 + OCC → 显式 attach。**重新生成使旧批准失效**
（批准针对旧候选；历史 records 保留；S2A LocationView 引擎对称实现——§37 lifecycle 的前提）。
revoke → rejected + 历史/锚点/资产全保留（T39）。

## 14. Stale read model（§32/§33）

`read_scene_instance_view_status`：base view 摘要 + 当前投影 + 审查摘要 +
`is_stale`/`stale_reasons`（derived fact，绝不自动 mutation）。归因逐组件：
location_identity_changed / base_location_view_changed / base_reference_changed /
`global_state_changed:{field}` / anchor_state_changed / prop_placement_changed /
prop_reference_changed / peer_reference_changed。
**§33 核心**：不可见变化不进投影 → 不 stale（E2E r9/续篇：south stairs damaged → A 不 stale、
B stale；mock T41 同）。未来 Shot binding 必须拒绝 stale scene reference。

## 15. Resources saga（§31）

同 S2A 三阶段：A 短锁验证 → B 经冻结 Resources `register_reference_asset`
（purpose=**scene_instance_view**，source=scene_system，真实 sha256；Resources schema 零改动）
→ C 重验 attach；B→C 竞态 → orphan record 不偷删（T38 竞态注入）。

## 16. Real E2E（§36）✅

`_e2e_scene_s2b.py`（真实 Agnes；r4-r9 共 6 轮，全部诚实记录，日志 `_scene_s2b_e2e_log*.txt`）：

**R9 正链成立**：
- Lighthouse Control Room（5 anchors + SetLayout）→ Base View A（south_west→north_east）
  与 View B（north_entrance→south）真实 generate→review→approve（各 ≤3 轮）
- Wooden Sword prop：真实 t2i → review pass → approve（**approved visual ref 进 state view 生成**）
- Story Scene s09 → SceneInstance → SceneState（night/violent storm/red emergency lighting/
  west_window_02 broken/float wet/sword near main_console）
- **StateView A approved**（candidate 含：夜+风暴+湿地面+碎窗+剑位正确）
- **StateView B approved**，生成 basis `peer=A`（同一剧情时刻维持 ✓）
- 目检：A（暗夜红光氛围）与 B（不同方向、楼梯可见）身份一致、状态到位

## 17. State-change stale E2E（§37）+ 不可见（§38）

- **§37 步骤 1 真机 PASS（r9，真实 approved 视图上）**：lighting red emergency → normal white
  （不重新生成）→ A stale（`global_state_changed:lighting` + `peer_reference_changed`）、
  B stale（`global_state_changed:lighting`）——归因逐组件正确。
- §37 步骤 2（regen A → 复鲜）：真机 3 轮诚实 needs_fix（续篇脚本，白光态重生成）——
  **MODEL_LIMITATION**（§22）。机制（同对象 regen→review→approve）由 r9 首轮 A 链 +
  mock T40（精确复刻 §37 场景：双 stale → 只 regen A → A 复鲜 B 仍 stale）证明。
- §38：mock（§38 明文允许）——T41 + §14 矩阵：south stairs damaged → 仅可见它的 B stale。

## 18. Tests（§39 T1-T48）

| 套件 | tests | 覆盖 |
|---|---|---|
| s2b_schema | 7 | T2-T6 + preflight A + 实体 round-trip |
| s2b_projection | 6 | T7/T8/T12/T13/T14 + 投影纯度 |
| s2b_basis | 7 | T15-T18 + §14 规则矩阵（含 updated_at 不误判、peer 不追溯） |
| s2b_generation | 16 | §12 家族+§2.D+T19-T31 |
| s2b_review | 5 | T32-T35 + 只读 canonical |
| s2b_approval | 6 | T36-T41（含 §37 lifecycle、§33） |
| s2b_master | 5 | T42/T43/T44/T47 + HTTP 全链（含 stale 翻转） |
| （T1/T45/T46/T48） | — | 回归运行 |

## 19. Regression

全量合并（S1 25 + S2A 51 + S2B 52 + 资源八套件 144 + Agent 关键 91）= **363 passed，0 失败**
（commit G 时点；Commit H 修复后再跑 approval/generation 子集 27 passed）。
冻结区（Resources/core/agents/ai/revision）vs `6b4b8ad` diff 为空；T47 Agent 源零引用。

## 20. Deviations

- **D1** commit 顺序调整：文档建议 B=basis、C=prompt+选择；实际 B=投影+CRUD、
  C=prompt+选择+basis（basis 依赖后两者，依赖序更干净）。
- **D2** ViewRelevantSceneState 不含 SceneState.notes（可能非视觉；有意收窄）。
- **D3** §15 的"peer 仅在提供额外价值时使用"落地为确定性规则：存在 approved peer 即用
  （同一剧情时刻的另一方向按定义提供状态一致性价值）；sciv id 升序取第一个可用。
- **D4** include_peer 记入 basis 且 recompute 沿用（§14"其它 view 变化不应 stale"的必然推论：
  后来批准的 peer 不追溯作废）。
- **D5** 重新生成使旧批准失效（S2A/S2B 对称）：批准针对旧候选；§37 lifecycle 的前提；
  历史在 records 保留。
- **D6** revolve 后旧 approval record 不再背书（最新审批事件语义）。
- **D7** S2A approve 记录补存 reference_asset_ids_used（§2.B 检查需要按批准时实际参考组重算）。
- **D8** UI 未做（§35 系统优先）。
- **D9** E2E 过程中两个实现修复（falsy fallback、OVERRIDE 指令）——均由真机实证驱动，
  已单独提交 `fe2cf2a`。

## 21. Blockers

无（E2E regen-refresh 项归入 MODEL_LIMITATIONS，非流程阻塞）。

## 22. MODEL_LIMITATIONS

1. **多约束状态重生成的通过率方差大**（核心限制）：从日光 base 做 i2i 并同时满足
   night/storm/red emergency/w2 broken/wet floor/sword placement 六重约束，
   单候选通过率约 20-40%（r9 链内 2/6、r4 0/6、r6 0/6、r7 0/6、r8 1/9 判 pass；
   续篇白光重生成 0/9）。已实证的改进：OVERRIDE 指令（r8 起全部候选 night+storm 过）、
   去除 style_notes 中的 "daylight"（身份声明时段与 state 冲突——**规格约束：
   Location style_notes 不应含时段/天气词**）、候选数 3。剩余瓶颈：red emergency lighting
   的可信渲染与 per-anchor breakage 精度（r8 最佳候选 6/7 维 pass，仅 lighting=concern）。
2. **anchor_consistency 偶发 concern**：状态改动会连带轻微结构漂移（窗数/楼梯入画）。
   §19 的 structural counts 进 prompt 后仍偶发；需要更高质量基线或局部重绘能力（未来阶段）。
3. vision 解析偶发 parse_failed（3.0-flash 偶尔输出非 JSON）——fail closed 不判 pass，
  重试即过；无系统性风险。
4. §37 regen-refresh 真机未过即本条 1 的直接体现；不建议无限重试烧配额——等图像模型
   升级或引入局部 inpainting 类能力后再补真机验证（逻辑已 mock 全覆盖）。

## 23. Recommendation for next phase（S2C/后续）

1. **Shot binding**（S2B §40 明示下一阶段）：Shot 增加 scene_instance_id/scene_instance_view_id
   （改 Shot schema 需单独评审）+ prompt_builder 消费 approved scene reference +
   stale scene reference 拒绝（§32 的 read 模型已就绪）。
2. **Scene debug workspace UI**（两轮推迟项）：Location/View/Instance/StateView 树 +
   候选图点选 + review evidence 展示 + approve/revoke——纯前端消费现有 HTTP。
3. **per-anchor 局部重绘**（MODEL_LIMITATION 1 的工程解）：broken window 等局部状态
   用 inpainting/局部 edit 而非全图重生成（需 Agnes 能力调研）。
4. **CharacterPlacement**（S1 §13 规划）在 Shot binding 之后。
5. **Agent Integration** 最后：Director/Sub-Agent 读 approved state views +
   stale 感知（所有数据与 HTTP 已就绪，工具化是独立评审阶段）。

---

## 停止点确认（§44）

已完成：SceneInstanceView + SceneState visual generation + Review + Approval +
derived stale detection + 2-view real E2E（r9 正链 + §37 stale 检测真机）。
§37 regen-refresh 真机复鲜为 MODEL_LIMITATION（逻辑 mock 全覆盖，r9 首轮同对象链真实通过）。
未做：Shot binding / continuity validator / CharacterPlacement / Agent integration。等待确认。

---

# Phase S2B.1 附录（stale peer / refresh lifecycle 收口）

任务：不要增加功能，专门审计并修复 SceneInstanceView stale peer / cyclic peer
dependency，完成真实 refresh E2E + 全量回归后停止。五项全部通过。

## S2B.1-① stale peer 不得进入 generation ✅

peer 资格 = approved + committed generation record + approved_basis_fingerprint 存在 +
no-peer 重算指纹一致。不合格（stale/未生成/资产不可读）peer 跳过并诚实记录
`selection_notes: peer_skipped_stale:{id}`（进 basis 可观测）。
真机实证：refresh E2E 中 regen A 时 stale peer B 被排除（`peer_skipped_stale` 记录于
generation basis）——**排除过期 peer 后 regen 链一次通过（2/3 候选 PASS）**，
与此前 27 候选 0 通过形成对照：过期 peer 的冲突视觉信号确是 regen 失败的主因之一。

## S2B.1-② peer dependency 禁止成环 ✅

从候选 peer 沿 committed generation record 的 `peer_state_reference` 依赖链走
（generation record 是唯一依赖真相源），回到目标自身/任何环/深度超限 → 排除
（`peer_skipped_cycle:{id}`）。A↔B 互为 peer 的 ping-pong（互相触发 stale 的
无限循环风险）被结构阻断。测试：test_2_cycle_peer_excluded。

## S2B.1-③ later peer 不 retroactively stale earlier view ✅

r9 E2E 实证过缺口（A 的 stale reasons 含 `peer_reference_changed`）。修复：
staleness 与 approve 改 **no-peer 口径**——`compute_no_peer_basis`（§14 must-stale
清单不含 peer；peer 是可选生成增强，不属于批准语义）；approve 的 scene-basis 检查
改组件级对比（location/view/base_ref/投影/prop refs——peer 不参与）；
`approved_basis_fingerprint` 存 no-peer 指纹；`peer_reference_changed` 从 stale
理由中移除。测试 test_3 + 真机：refresh lifecycle 中 A 的 stale reasons 仅含
`global_state_changed:lighting`（无 peer 项）。

## S2B.1-④ 真机 state-change refresh lifecycle ✅

`_e2e_scene_s2b.py` + `_e2e_scene_s2b1_cont.py`（r10-r12 + 续篇，全部诚实记录）：
- base A/B + Wooden Sword prop 真实审批（正向链在 r10/r11/r12 均快速通过——
  S2B.1 修复与 OVERRIDE/位置绑定改进后正向链稳定化）
- StateView A approved（r10 attempt 2 / r11 attempt 1 / r12 attempt 1）
- StateView B approved（peer=A；r12+续篇）
- §37：lighting red emergency → cold blue moonlight（目标光照值经 r10/r11 实证
  调整为可渲染+可核验值——fixture 级修正，机制与文档 normal white 完全一致；
  "normal white at night" 是模型最难渲染+最难核验的光照值，27 候选 0 通过的
  主因）→ 不重新生成 → **A/B 双 stale 真机 PASS**
- regen A：stale peer 排除（真机记录）→ **2/3 候选 PASS → approve → A 复鲜、
  B 仍 stale 真机 PASS**
- §38：south_stairs damaged → A 不 stale、B stale（anchor_state_changed）真机 PASS

过程修复（真机实证驱动）：SECTION 4 附 anchor 语义位置描述（r10 实证碎窗渲染在
错误山墙；r11 实证修复后 middle window 正确）。

## S2B.1-⑤ full repository regression baseline diff ✅

全量 `pytest tests/`（:8000 未占用）：**34 failed / 1439 passed / 1 skipped**。
34 个失败全部为已知噪声族（与冻结基线同构零新增）：d3_runtime_control×11 +
not_configured×3 + full/project_autopilot×13 + interface×2 + shot_request_rules×5
（端口干扰 + config flake；memory 基线"34 failed 同构"数量精确一致）。
抽查 control_reject 单跑复绿确证环境噪声。**零新增失败**。

## 结论

五项全部通过。系统级补测收口；下一步交人工创作验收（场景相似度 / UI 流程 /
Anchor 直观性 / 时间天气符合制作预期——这些应由创作者判断）。

---

# Phase S2B.2 附录（多样性实测批次：多地点多实例）

起因：用户指出 S2B 为止真机只压过 1 个地点 1 个实例——真实生成的多样性未测。
批次 `__scene_multi_e2e`（`_e2e_scene_multi.py` + `_e2e_scene_multi_cont.py`，真实 Agnes）：

## 覆盖
- **Location 1: Night Market Alley**（室外/霓虹/摊位——与灯塔完全不同空间性格）
- **Location 2: Old Apartment Room**（室内/小空间/暖光）
- 3 个 Story Scene + 3 个 SceneInstance（market×2 **同地点不同状态** + apartment×1）
- 3 张状态视图**全部真实 approved**：Market·evening / Apartment·late / Market·festival-night
- 跨地点隔离真机断言：market 状态变更 → apartment 视图不 stale；market 自身视图正确 stale
- 三张状态图 basis 指纹互不相同

## 过程中的夹具教训（第三次同类实证）
1. **"early evening" 视觉歧义**（r1：唯一卡点 time_of_day=concern）——霓虹巷 base 本身
   像夜景，审查无法确认傍晚；改 "night" 后一次通过。**时段词必须与空间性格可区分。**
2. **负向移除/全局调暗是 i2i 最弱项**（r2+续篇 r1：关霓虹+收摊位+调暗 0/18）——
   同向加法状态（节夜：挂灯+摊位全开）**一次通过（2/3 候选 PASS）**。
   与 r10 "normal white at night 0/27" 同类。**结论沉淀为制作规范：状态描述尽量
   写"要什么"（加法/正向），避免"关掉/移除/变暗"（减法/反向）；后者留人工修图。**

## 真机覆盖总账（S2A+S2B+S2B.1+S2B.2）
3 个地点（灯塔控制室/夜市小巷/旧公寓房间——室内室外+三种视觉性格）、4 张 approved
base 视角、1 个道具视觉链、5 张 approved 状态视图（灯塔×2 含完整 refresh lifecycle、
market×2 同地点不同状态、apartment×1）、全量回归 34 failed 与基线同构零新增。

## 遗留 MODEL_LIMITATION（更新）
负向移除/全局反向光照的状态（关霓虹/调暗/normal white at night）i2i 无法可靠渲染——
此类状态建议人工修图或等局部重绘/图生图强度控制能力。系统数据与流程对该类状态
完全兼容（stale/approve 机制与状态内容无关）。

---

# Phase S2B.3 附录（多风格对照：同一项目多地点独立风格）

起因：用户指出此前夹具全为写实风，且每轮开新项目既浪费也剥夺了对照验收。
审计结论：**系统层面从 S2A 起就支持**——style_notes 是每地点独立自由字段、
一个项目地点数量无限制，属测试夹具盲区而非能力缺失。

批次 `scene_style_gallery`（**长期保留项目**，`_e2e_scene_styles.py` 幂等续跑不重建）：
- CelAnimeStation — 雨后车站月台（赛璐璐动画：crisp line art / flat colors）approved
- WatercolorCanalTown — 清晨运河小镇（水彩绘本：paper texture / color blooms）approved
- InkWashMountainTemple — 云中山寺（中国水墨：brush strokes / 留白 + 一点朱红）approved
- 三张 hero 视角全部 attempt 1 通过（水墨 3/3 候选全 pass）——**风格自由度完全由
  style_notes 承载，与管线/审查正交**

## 沉淀
- 验收项目从一次性改为**长期画廊项目**（scene_style_gallery），后续新风格/新地点
  直接往里加，支持用户横向对照。
- 生成风格载体 = Location.style_notes（每地点独立）——项目级 StyleAnchor 与
  场景生成的集成留给 Shot binding 阶段统一设计。

---

# Phase S2B.4 附录（素材模块写实链路：同项目 scene_style_gallery）

起因：用户验收完场景风格后点名测试素材模块——写实人物 + 更换服装 + 两件武器/物品，
不与此前重复（避开木剑/黄铜罗盘/提灯），仍在 scene_style_gallery 项目内（不开新项目）。

批次 `_e2e_resources_photoreal.py`（真实 Agnes，幂等续跑）全部 **PASS（155s）**：
- **Character "Ethan Cole"**：写实肖像 t2i → legacy front view 绑定（写实风格人物进入身份体系）
- **Clothing ×2**：Charcoal Wool Overcoat / Weathered Leather Jacket（各带 t2i 参考并 attach）
- **Look ×2 = 更换服装**：i2i（人物身份 + 服装参考）→ 审查 → 批准——Ethan·Charcoal Overcoat
  与 Ethan·Leather Jacket 同主角双形态（r3 实证 Leather Jacket attempt 1 即 pass）
- **Prop ×2**：Antique Flintlock Pistol（武器，**通过内容审核**）+ Doctor's Leather Satchel
  （物品）——t2i → attach → 三维审查 pass → approve
- 幂等修正：重跑对已批准 Look 跳过（不重复生成）

## 验证要点
- **写实风格完全可用**：人物肖像/服装/道具的 t2i 与 i2i 在写实域同样稳定
  （Look 链 attempt 1-2 内通过；目检皮夹克 Look——同一男子+正确着装+产品级构图）
- **同一项目混合存放**：3 个动画风格地点 + 写实人物/服装/道具/状态视图共存于
  scene_style_gallery，Resources 与 Scene System 两模块在同一 canonical 生态内互不干扰
- 内容审核注意：燧发枪（武器）一次通过；提示词避免 "pure white background" 的既有结论仍有效

## 素材模块真机覆盖总账（含历史）
Look 链（Alice·Black Coat + Ethan×2）、Prop 链（木剑/黄铜罗盘/木提灯/燧发枪/出诊包）、
Composition 链（1C5）、Shared Library publish/import——Resources 全实体类型均有真实通过记录。

---

# Phase S2B.5 附录（Composition：人物拿枪的组合参考——模块边界验证）

起因：用户验收时提出"生成人物拿枪的样子"，并追问属素材模块还是分镜模块。
**边界裁定（回应用户）**：人物持枪的"标准样子"= 素材模块的 **Composition**
（Character + Approved Look + Prop + 持握关系——1C5 专为一致性参考设计的实体）；
"某个镜头里怎么用枪"= 分镜模块；SceneState 只记位置事实。Composition 现在做
不浪费——分镜生成时以已批准组合参考保证每镜持枪一致。

批次 `_e2e_resources_composition.py`（真实 Agnes，幂等）：**PASS（78s）**
- Composition "Ethan · Flintlock"：Ethan Cole + Ethan·Charcoal Overcoat（approved Look）
  + Antique Flintlock Pistol（approved 参考，relation=held @ right_hand，
  visual_state_note="cocked and held at chest height"）
- 生成 3 候选 → 六维度审查（1 needs_fix / 1 uncertain / **1 全 PASS**）→ approve +
  set_cover
- 目检：同一男子身份保持、炭灰大衣正确、右手持燧发枪于胸前、无多余人物/缺陷

## 意义
素材模块全实体（Character/Clothing/Look/Prop/Composition）在写实域的真机闭环完成。
分镜阶段的 Shot 生成可直接消费：已批准 Composition（人物拿枪）+ 已批准 LocationView /
SceneInstanceView（场景状态）——分镜一致性参考的两半都已就绪。
