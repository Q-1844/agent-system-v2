# AGENT_SYSTEM_V2_RESOURCE_AGENT_PHASE_B_REPORT.md

**Agent System V2 — Phase B：Resource Agent Expansion & Production Resources Integration 最终报告**

**日期**: 2026-09-18 · **规格**: 详细流程文件/修复阶段/26.md · **分支**: `feat/agent-v2-resource-agent`
**事实基线**: `AGENT_SYSTEM_V2_CURRENT_STATE_AUDIT.md`（Phase A，只读审计）+ 真实代码
**测试基线**: 收档点全量 2029 collected → 本阶段最终 **2093 passed / 0 failed**（+64 新用例，含 2 个 pre-existing baseline failure 修复）

---

## 1. final Resource Agent identity

用户可见身份 = **Resource Agent（资源设计师 / Production Resource Designer）**，role = `角色 · 造型 · 道具 · 生产资源`。系统提示整体重写（character_designer.py `CHARACTER_DESIGNER_SYSTEM`）：身份、六个核心概念（§11.1-11.5 全覆盖）、审批分档纪律、cast 检查纪律、资源准备依赖链工作法。

## 2. internal agent_id compatibility

内部 agent_id 恒为 **`character_designer`**（§0.1 裁决执行，零迁移）。兼容面逐一验证：
- registry `SPECIALISTS` 不变；session（agent_id JSON 字段）旧数据直接可用（R3 测试 + 真机同 session 跨 Phase B 升级续用）；
- `character_designer.delegate` 工具名保留，描述升级为「委派 Resource Agent（兼容工具名，内部历史 id character_designer）」；
- Production Room / bridge_messages / workflows 历史数据零迁移正常显示（roomIdentity.ts 按 agent_id 映射，映射键不变）。

## 3. final ownership

Resource Agent 拥有：Character（name/description_en + views，project.json）、Clothing、CharacterLook、Prop、ReferenceAsset、CharacterReferenceComposition（resources.json）、Shared Library publish/import。**无第二套 canonical**——全部写路径走 `ResourceWriteService` / `SharedLibraryService` / 既有 review/approval service。

## 4. canonical boundaries

不写（工具面物理缺失 + WRITE_TOOL_DOMAIN 双域白名单）：Story/Script、Shot、Scene System 全域、Binding、**ProjectVisualProfile**（§0.4：只读工具 `project.visual_profile.read`，无任何 mutation 工具——R8 断言）。§0.5 Scene System 未纳入（Location/Instance/State/View 零工具）。

## 5. ProjectVisualProfile integration

- **唯一编译点复用**（§3.2）：agent 三视图 handler 的全部 prompt 经 `append_style_instruction`（与 HTTP `_apply_project_style` 同一编译函数）；unspecified/读取失败 → 宽容降级 = 与 HTTP 行为一致，不造第三种行为（B2 测试逐字节断言）。
- **签名统一**：`character_appearance_sig` 升级三元（description_en + art_style + visual_profile_fingerprint）；agent 与 change-protocol 两条提交路径同式（B3）——profile 变更 ⇒ 旧三视图判定陈旧必须重生成。
- **§3.3 双风格真相消除**：system 注入最小 profile 摘要（render_family/方向/标签/负面约束），全文按需工具读取；legacy `context.py build_context` 属 legacy capability 路径（native tool-loop 不经过），未动。
- 真机证据：Look 生成 prompt 含编译点风格指令（`Visual production style: ...`）。

## 6. final tool list

基础面 16 + shot.list/shot.read（Director 过滤复用）= **18**（§5 目标 14~18 上限内）：

| # | 工具 | 类型 |
|---|---|---|
| 1-6 | character.list/read/create/update/delete/generate_views | 既有保留（§6 零重包装） |
| 7 | project.visual_profile.read | 只读（§3.1） |
| 8 | resource.inventory | 只读（§7.2，filter enum，紧凑摘要+document_revision） |
| 9 | resource.read | 只读（§7.3，kind enum，高信号详情） |
| 10 | resource.clothing.upsert | 写（§8.1） |
| 11 | resource.look.prepare | 写+生成+审查+批准（§8.2） |
| 12 | resource.prop.register | 写+审查+批准（§8.3） |
| 13 | resource.composition.prepare | 写+生成+审查+批准（§8.4） |
| 14 | resource.library.publish | 写（共享库，§9） |
| 15 | resource.library.import | 写（共享库，§9） |
| 16 | web.search | 既有 |
| 17-18 | shot.list / shot.read | 只读（§12，schema 从 Director 过滤复用，执行器 lazy 复用防环） |

反模式全部规避：无第二套 character 工具、无万能 do_everything、无底层细碎 API（approve/revoke/register_reference_asset 裸接口均未直接暴露）。

## 7. 每个 tool 的 read/write/generate/review/approve 权限

| 工具 | read | write canonical | generate | review | approve |
|---|---|---|---|---|---|
| character.* | ✅ | name/description_en | generate_views ✅ | ❌（人评） | ❌ |
| visual_profile.read | ✅ | ❌ | — | — | — |
| inventory/read | ✅ | ❌ | — | — | — |
| clothing.upsert | ✅ | ✅ | ❌ | — | — |
| look.prepare | ✅ | ✅ | ✅（经 service 三阶段） | ✅（触发正式 vision review） | ✅（仅 approve_if_pass 且真实 pass；review_only 永不） |
| prop.register | ✅ | ✅ | ❌（系统无 Prop 生成端点，R31 断言零 provider 调用） | ✅ | 同上分档 |
| composition.prepare | ✅ | ✅ | ✅ | ✅（六维度） | 同上 |
| library.publish/import | ✅ | ✅（copy 语义） | ❌ | — | — |
| shot.list/read | ✅ | ❌ | ❌ | ❌ | ❌ |

**Revoke：零工具**（§10.1 高风险用户决策保留给 UI）。

## 8. ResourceWriteService reuse

100% 复用：create/update_clothing、create/update_look、generate_look_candidates、approve_look_reference、create/update_prop、register/attach_prop_reference、review_prop_reference、approve_prop_reference、create/update_composition、generate_composition_candidates、review_composition_candidates、approve_composition_candidate、register_reference_asset、SharedLibraryService.publish/publish_character/import_resource。**工具执行器零直写 JSON、零直连 provider**（§19；prop 流程 `gen_calls == []` 断言）。

## 9. approval_mode 设计

显式 enum `review_only | approve_if_pass`（无隐藏默认自动批准）。系统提示 §10 裁决话术映射：production-ready/完成后供分镜 → approve_if_pass；先设计看看/生成候选/不要定稿 → review_only。物理分层保持：Generation ≠ Review（证据，review_records.jsonl）≠ Approval（显式 mutation，独立 receipt，可 revoke）。服务端确定性推导压过模型自报 overall（needs_fix ⇒ 绝不 approve——R19/R28 真机复证）。

## 10. OCC

`expected_resources_revision` 显式透传：create 对照 document_revision、update 对照目标资源 revision（svc 判定 stale，工具不无声读新）；链内后续阶段（generate→approve）用上一阶段真实回执的 revision 衔接；生成阶段 3 的 basis 重算 fail-closed 原样保留。发布前源状态校验（防过期快照发布）。

## 11. receipts

`_base_receipt` 统一骨架 + 写后 svc 重读验证（verified）+ changed_resources 只从 ok+verified 的真实 service 回执聚合（去重保序）。分层语义：`ok`=本次尝试是否有真实 canonical 变更（部分成功=True，与 generate_views 同语义），generation/review/approval 各阶段结论独立字段——「Look 已建但生成失败」同时可见。stale → 结构化 stale receipt（expected/current revision + 当前内容）。

## 12. changed_refs extension

`_derive_changed_refs` 扩为 **八键**：scenes/shots/characters（原三键完全兼容）+ clothings/looks/props/reference_assets/compositions。派生只认真实回执：`changed_resources=[{kind,id}]`（mutation 工具聚合）+ 单数 id 字段兼容 + candidate_asset_ids；`character` kind 映射 characters（共享库角色导入）。早期失败回执 `_empty_changed_refs()` 全键骨架；AgentTask.artifact_refs 扩展资源 type；receipts_meta 透传资源字段。**未增加 shared_resources 键**（发布零改项目 canonical，导入按真实 kind 归键）。

## 13. resource locks

`RESOURCE_RESOURCES="resources"` + `resources.changed` 事件（project_events.py）。**唯一权威映射表 `WRITE_TOOL_DOMAIN`**（写工具→锁域）：character.* → character 域、resource.* → resources 域。routers/agents.py 流式端点与 Producer Bridge `_cb` 改为按域 lock/emit/finally 逐域 unlock（异常/停止/断开全路径释放）。前端：projectEvents store 增 resourcesVersion/resourcesLocked；生产资源页识别 character+resources 双锁（只读横幅/新建按钮禁用/resources.changed 静默刷新/topbar 状态）。域级锁——一个 Look 生成不锁整个项目（§15）。

## 14. Producer integration

PRODUCER_SYSTEM 三分功升级（Resource Agent = Character identity + 生产资源全套，production-ready 一条龙为招牌任务）；delegate 描述含 owns/适用/不适用 + approval_mode 传话要点；`_bridge_cb` 按域锁；`_SPECIALIST_RESOURCE` 不变（CD 桥级 fallback 域）。无第四 Specialist。真机 §24：委派 → 真实 turn → TaskResult verified=True + changed_refs.compositions 非空 + scenes/shots 恒空——Producer 未因无 scene/shot 键而误判未执行。

## 15. Production Room / UI

roomIdentity.ts：`character_designer` → name 'Resource Agent' / 🧩 / '角色 · 造型 · 道具 · 生产资源'（映射键内部 id 不变，历史 session/桥消息正常渲染）。agentTurn.ts delegate label 更新。registry.py name/role 升级（API 下发）。R56 静态断言。

## 16. prompt-reference conflict handling

§14 轻量预检 `_look_reference_conflicts`：6 组高信号规则（face hidden/mask/silhouette/faceless/fully covered/hood shadow），0 LLM。命中 → `reference_conflict` 结构化拦截（零写入 + 冲突信号清单 + 四步处置引导 + `conflict_acknowledged` 用户确认后放行）。真机 Case B：拦截→agent 结构化报告冲突并给出选项 A/B，零盲目重试；Case A：确认后 6 次生成全 needs_fix，agent 如实报告「模型无法同时满足遮面与身份参考」的系统性矛盾，**未假批准**（Golden Run night_cloak 教训的 Agent 化闭环）。

## 17. Prop review fix

§4 判卷口径重写（schema 零改动）：判卷对象=道具本体（材质/造型语言/纹理/线条/时代感/渲染介质）；素底/白底/透明底/简洁背景+均匀布光是预期呈现形式，本身不得 fail；真介质错配仍 hard fail。真机实证：坏参考图被正确 needs_fix（"shows colored pencils instead of a Bronze Lantern"）——审查拒稿能力正常。解除 Golden Run §六.10 P1 遗留。

## 18. mock tests

新增 6 文件 64 用例：test_producer_changed_refs_v2（A1-A8）、test_resource_agent_visual_profile（B1-B5=R5-R8）、test_resource_agent_prop_review_semantics（C1/R27/R28）、test_resource_agent_read_tools（D1-D6=R9-R13/R57）、test_resource_agent_mutations（R14-R24/§14/R25-R38/R44-R48/R58-R59+复用护栏×3）、test_resource_agent_shared_library（R39-R43）、test_resource_agent_identity（G1-G6/R3/R56）。全量回归 **2093 passed 0 failed**。

## 19. real Resource Agent E2E（§22）

scratch 项目 `PhaseB_ResourceAgent_E2E`（2d_animation profile、清末山驿故事、真机灯笼参考图）。自然语言目标（26.md 原文，无工具顺序提示）→ 真 Agnes tool-loop **49 分钟 78 次工具调用**：story.list/read ×3 → visual_profile.read → shot.list/read ×3 → character.read/list → generate_views（林晚/陈叔三视图）→ clothing.upsert → look.prepare（16 次，2 个 Look **生成→审查 PASS→自动批准**）→ prop.register → composition.prepare。web.search 自主使用 1 次。全程 SSE trace + session 持久化 + generation/review/approval JSONL 证据（driver `_phaseb_e2e.py`，结果 `_phaseb_e2e_results.json`）。

## 20. real Producer→Resource Agent E2E（§24）

Producer 真实委派（§24 原文 prompt）→ `character_designer.delegate` → 真实 Resource Agent turn（会话历史延续）→ TaskResult **status=completed / verified=True / changed_refs.compositions=[comp_...]** → bridge_messages.jsonl 2 行落盘 → workflows.json 终态。scenes/shots 恒空且 Producer 正确接受——资源键语义被正确消费。

## 21. regressions

全量 2093 passed / 0 failed。过程中修复 2 个 **pre-existing baseline failures**（收档点即失败，单跑归因非 Phase B 引入）：s3c1/s3c2 的 legacy 断言未随 image 轮 `_legacy_vp_style_suffix` seam 更新——期望值改为与生产路径一致。有意 schema 扩展导致的旧全键断言更新 8 处（changed_refs 八键、工具面全等 ×2）。

## 22. deviations

1. **§7.2 inventory 的 stale/readiness 信息**：未含 per-resource style-compat（成本考量，resource.read 已带 `latest_generation_style`）。
2. **§9 destination library**：仅 default 库（系统现状），参数校验 fail-closed。
3. **receipt ok 部分成功语义**：look/prop/composition prepare 在「本体已建但生成/审查失败」时 ok=True（镜像 generate_views 部分成功语义）——以 generation/review/approval 独立字段如实呈现， Producer 侧靠 changed_resources 精确计数。
4. **Commit H1（计划外）**：复用护栏（见 23）。
5. ** shot.list/read 惰性并入** `_tool_surface()`（director.py 模块级 import CD 执行器，模块级反向 import 成环——turn 时构建，director 不可用降级基础面）。

## 23. blockers

**无 BLOCKER。** 真机发现并当场修复一项系统性病理（Commit H1）：审查失败后 Agent 反复重建同名资源（3 同名 prop/4 同名 composition）而非复用重审——部分成功 ok=True 不计入 repeat-failure 护栏 + LLM 偏好新建。修复=三 create 路径同名护栏（非空同名 → 结构化拒绝 + existing id 引导），Case A 复验同 look_id 零重建。**残余风险（已知限制，非 blocker）**：审查 needs_fix 后的多次再生成（Case A 6 次）不计入重复失败护栏——建议后续以 per-resource review-failure 计数器收口（Phase C 议题）。

## 24. known limitations

1. 审查失败重试烧图无独立护栏（上述）；2. Prop 参考图质量依赖用户/外部输入（无生成端点=系统设计，Case B/A 的灯笼参考即 setup 阶段误用 character-view 模板生成的坏图——被 review 正确拦截恰为审查能力实证）；3. composition 生成依赖 prop approved（caseB 项目中 composition 全部止步 create，行为正确）；4. inventory 不分页（资源量大时需按 filter 拆分）；5. Agnes 免费档 500 风暴拉长真机 turn（client 重试吸收，非 Phase B 引入）。

## 25. file/commit map

| Commit | 内容 |
|---|---|
| b4e84dd | 收档：image 验收轮 + UI 重构 + Phase A 报告（preflight） |
| 0568144 | pre-existing baseline failure 修复（s3c1/s3c2 legacy 断言） |
| 5694cd4 | **A** changed_refs 八键扩展 + artifact_refs + 透传通道 |
| a8945b6 | **B** profile 对齐：handler 接编译点 + 三元签名 + visual_profile.read |
| 182ac53 | **C** Prop review 判卷口径 |
| fd24e0b | **D** inventory/read + shot 读工具 + resources 锁域 + 前端双锁 |
| 1f0f644 | **E** clothing/look/prop/composition mutation 工具 + §14 预检 |
| 243c243 | **F** library.publish/import |
| 40f9163 | **G** Resource Agent 身份重写 + Producer 桥 + Room UI |
| 2cfa62d | **H1** 资源复用护栏（真机矫正） |
| （本提交） | **H2/H3** E2E driver + 结果 + 本报告 |

关键文件：src/agents/character_designer.py（+约 1100 行：8 工具+预检+护栏+prompt 重写）、src/agents/producer.py（changed_refs/锁域/桥/prompt）、src/agents/character_views_handler.py、src/agents/project_events.py、src/agents/registry.py、src/agents/inquiry.py、src/agents/change_protocol/execution_service.py、routers/agents.py、src/resources/prop_review.py、src/store/projectEvents.ts、src/components/agent/roomIdentity.ts、src/views/resources/index.vue、src/store/agentTurn.ts、tests/test_resource_agent_*.py ×6 + 既有断言更新 ×6。

---

## 最终问题逐条回答（26.md §28）

**1. Character Designer 是否已经真正升级为 Resource Agent？**
是。身份（prompt/registry/UI）、工具面（7→18）、所有权（Character+Resources 六实体+共享库）、概念体系（Look/Prop/Composition/profile 宪法）四位一体升级；内部 id 兼容不变。

**2. 它是否能独立完成 Character → Views → Look → Review → Approval？**
是，真机实证：两个 Look 全链自主完成（生成→审查 PASS→approve_if_pass 自动批准），approved 状态落盘 canonical。

**3. 它是否能管理 Clothing？**
是（upsert create/update 双路径 + OCC stale 拒绝，R14-R16 + 真机）。

**4. 它是否能管理有参考图的 Prop？**
是（三来源参考注册/attach→review→分档批准；无参考诚实 reference_required；R31 零 provider 直调）。

**5. 它是否能创建 Character + Look + Prop Composition？**
是（前置校验 fail-closed + 六维度审查 + 分档批准；R32-R38；真机 composition.prepare 全链）。

**6. 它是否理解 ProjectVisualProfile？**
是（只读工具+system 摘要+生成消费唯一编译点+三元签名；宪法纪律写入 prompt——可读可遵守不可改）。

**7. 它是否仍然无法越权修改 Story/Shot/Scene？**
是（工具面物理缺失 + R44-R48 断言 + WRITE_TOOL_DOMAIN 双域白名单；真机 Producer 委派 scenes/shots 恒空）。

**8. Producer 是否能正确委派并识别 Resource mutations？**
是（§24 真机：verified=True、changed_refs.compositions 非空被正确消费，未因无 scene/shot 键误判；八键派生 A1-A8 单测）。

**9. 老 character_designer session / workflow 是否兼容？**
是（R3 + G 系列；真机同 session 跨升级续用；桥消息/workflows/Room 历史零迁移）。

**10. 是否存在 BLOCKER？**
无。一项真机系统性病理（重建同名资源）已在 Commit H1 修复并有回归护栏；残余「审查失败重试烧图」限制已记录（Phase C 议题）。

**11. 是否可以进入下一阶段 Director Production Integration？**
**可以。** Resource Agent 侧的 changed_refs/readiness 前置（B1）、profile 认知（B2）、prop 口径（B3）已就位；Producer→Resource Agent→canonical→receipts→Room 全链真机贯通。建议 Phase C 议题：Director 的 readiness 只读查询、cast 完整性归责（§13 纪律已由 Resource Agent 侧报告闭环）、审查失败重试护栏、（可选）Masked identity view 能力（Case A 暴露的模型边界需要新的身份参考类型才能根治）。

**报告位置**：`animation-storyboard/AGENT_SYSTEM_V2_RESOURCE_AGENT_PHASE_B_REPORT.md` · 分支 `feat/agent-v2-resource-agent` · 真机证据 `_phaseb_e2e_results.json` + `projects/PhaseB_ResourceAgent_E2E/output/`（generation/review/approval JSONL + bridge_messages.jsonl）· 测试 2093 passed
