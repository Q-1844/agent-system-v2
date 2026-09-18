# AGENT_SYSTEM_V2_CURRENT_STATE_AUDIT.md

**Agent System V2 — Phase A：现状审计与职责映射（Current-State Audit & Responsibility Mapping）**

**日期**: 2026-09-18
**性质**: 只读调查报告。本轮**零生产代码修改、零 prompt 修改、零 tool 新增**。全部结论以当前代码为准（文件:行号可溯），不以历史文档推测。
**输入**: 现有代码（src/agents/**、src/ai/**、src/resources/**、src/scene_system/**、src/shot_binding/**、src/visual_profile/**、src/sequence/**、src/generation/**、routers/**、前端 src/views|components|store）+ Golden Run（2026-09-18，33 镜真机验收）真实证据 + 测试全量收集（2029 passed）。
**调查边界**: 5 个在册 Agent（Script Writer / Character Designer / Director / Scene Sub-Agent / Producer）+ 注册表中另 4 个固定身份（production_planner / shot_producer / continuity_reviewer / editor）。

---

## 1. Executive Summary

1. **当前 Agent 系统是一个「project.json 时代」的架构。** 8 个固定 Agent 身份（registry.py:23 "固定 8 Agent 稳定 ID（进入持久化数据，勿改）"）、工具面、资源锁（story/character/storyboard 三把）、changed_refs（{scenes, shots, characters} 三键）全部围绕 project.json 的三个 canonical 域构建。
2. **此后进入生产后端的 8 大能力域，对全部 Agent 完全不可见。** ProjectVisualProfile、Production Resources（Look/Clothing/Prop/ReferenceAsset/Composition/SharedLibrary）、Scene System（Location→View→Instance→InstanceView）、ShotProductionBinding/ReferencePack——Agent 侧 **0 tool、0 prompt 提及、0 上下文注入**（grep 全证实）。Director 触发图片生成时会"暗中"消费这些能力（Job handler 内部走 BOUND/visual profile），但 Director 不知道它们存在，也无法查询/补救。
3. **Character Designer 是当前最窄的 Specialist：恰好 7 个工具**，写权限仅 project.json 中 Character 的 `name/description_en` 两个字段 + 三视图生成。它在「角色设定」意义上完整，但在今天的系统里，一个角色的"生产视觉身份"实际上是 **approved Look + ReferenceAsset 链**——CD 完全不知道这条链的存在。
4. **"Character Designer → Resource Agent" 有真实必要性，但 R2（Production Resource owner）是自然边界，R4（一切视觉物）会导致 ownership 膨胀**——Location/SceneInstance 的归属是 Scene System 与叙事域的交界问题，不宜并入。
5. **改名（identity migration）风险真实但可控**：identity 以 JSON 字段值持久化（sessions/tasks/change_protocol/work_memory/bridge_messages，约 55 个持久化文件含该字符串），无 URL/文件名耦合；Option A（内部 id 不变、仅改 UI 显示）成本最低且零迁移风险。
6. **两个已被证实的现状缺口**：① agent 的 `character.generate_views` 路径绕过 ProjectVisualProfile（用 legacy `project.style.art_style`，character_views_handler.py:113）；② 全部 Agent 的风格认知来自 legacy StyleAnchor，与真实生成消费的 visual profile 脱节——Agent 会基于一个已经不再决定生成结果的"风格"做推理。
7. **Assembly/Compositor 的底层能力已具 70%+**：Sequence Assembly（S1/S2 冻结）提供 canonical + 渲染 + 音频混音；缺口是 BGM/SFX/ambience/narration 的**生成**、转场、细粒度 timeline 编辑。
8. **Golden Run 暴露的 4 个问题中，1 个是 Agent 职责缺失（cast 校验无人负责）、1 个是 prompt-reference 冲突（CD 域）、2 个是模型/系统设计问题**——不应全部归给 Agent。

---

## 2. Current Agent Map（当前 Agent 真实地图）

### 2.1 Agent 注册表全貌（src/agents/registry.py）

固定 8 身份（`SPECIALISTS` 7 + producer；registry.py:23-25）：

| Agent id | 名称 | 角色 | allowed_capabilities（真实授权） | 现状 |
|---|---|---|---|---|
| producer | Producer Supervisor | 总协调 | project.inspect | **活跃**（含 Production Room / 桥） |
| script_writer | Script Writer | 剧本 | story.inspect / story.edit / story.propose_change | **活跃**（native tool-loop，14 工具） |
| character_designer | Character Designer | 角色 | character.inspect / character.design / character.propose_change / character.views.generate | **活跃**（native tool-loop，7 工具） |
| director | Director | 导演 | storyboard.inspect / storyboard.propose_change | **活跃**（native tool-loop，15+3 工具） |
| production_planner | Production Planner | 规划 | production_plan.inspect/create, shot.inspect | 半活跃（AI-6E 接通 plan.create） |
| shot_producer | Shot Producer | 制作 | shot.first_frame/last_frame/video/audio | 半活跃（real pipeline 已接通，见 §2.4） |
| continuity_reviewer | Continuity Reviewer | 审查 | review.shot / review.scene | **未激活**（capabilities implemented=False） |
| editor | Editor | 剪辑 | composition.inspect / composition.compose | **未激活**（compose capability=False） |

### 2.2 五个在册 Agent 的 20 项属性表

下表全部经代码核实（文件:行号）。

| # | 属性 | Script Writer | Character Designer | Director | Scene Sub-Agent | Producer |
|---|---|---|---|---|---|---|
| 1 | identity/registry key | `script_writer` | `character_designer` | `director` | **无注册表身份**（Director 的 per-scene delegation 实例） | `producer` |
| 2 | system prompt | registry.py:306-310 + script_writer.py 内 `SCRIPT_WRITER_SYSTEM`（详细版） | registry.py:311-313 + character_designer.py:169 `CHARACTER_DESIGNER_SYSTEM`（详细版） | registry.py:314-317 + director.py 内详细 prompt | instance.py:149 `build_instance_instruction`（每段动态构造） | producer.py:86 `PRODUCER_SYSTEM`（详细版） |
| 3 | turn entry | routers/agents.py turn/stream → runtime 按 session.agent_id 分发 → script_writer turn | character_designer.py:1479 `character_designer_turn` | director.py:2373 `director_turn` | director 发起 `scene.delegate` → subagent/instance.py:354 `run_scene_agent_instance` | producer.py:899 `producer_turn` |
| 4 | session 创建 | sessions.py:49 `get_or_create(agent_id,...)`（同 agent+scope 复用；持久化 output/agents/sessions/sess_*.json，agent_id 为 JSON 字段） | 同左 | 同左 | 无独立 session（delegation 句柄 + 提案落盘 output/agents/scene_agents/） | 同左（另 production_room 投影） |
| 5 | tools（LLM 可见） | 14 个：script.update/read, story.create/list/read/move/update/delete/create_many/update_many/delete_many/insert_many, web.search | **7 个**：character.list/read/create/update/delete/generate_views + web.search | 15 个：shot.create/create_many/update/update_many/delete/move/list/read, story.list/read, script.read, character.list/read, web.search；**另** shot.generate_images/generate_videos（director_visual.py:421/459）、visual.review_image（director_vision_review.py:50）、subagent_enabled 时 scene.delegate（subagent/tools.py:39） | DIRECTOR_READ_TOOLS ∪ {proposal.submit}；**物理无写工具**（instance.py:17 硬约束） | project.inspect + 3 个 delegate 工具（producer.py:75） |
| 6 | read tools | story/script 域 | character/story/script | shot/story/script/character | 全只读 | project.inspect + specialist 结果 |
| 7 | write tools（canonical） | story_text/scenes 全套（含批量） | **仅 character.create/update/delete**（写 name/description_en 两字段）+ generate_views（写三视图资产） | shot.* 全套（经 Workspace/ChangeSet 语义，写 scenes[].shots[]） | **无**（只产 proposal） | **无（物理保证）** producer.py:71 |
| 8 | canonical ownership | story_text + scenes 叙事字段 | Project.characters（name/description_en）+ Character.views 资产 | scenes[].shots[] | 无（提案制） | 无 |
| 9 | generation tools | 无 | character.generate_views（JobQueue kind=`character_views`，chain 生成 front t2i→side/back i2i→rembg，版本化 sig 文件；character_views_handler.py） | shot.generate_images / shot.generate_videos（**真实 JobQueue** kinds first_frame/last_frame/video，轮询+写后验证 director_visual.py:193-260） | 无 | 无（委派） |
| 10 | review tools | 无 | 无 | visual.review_image（单图视觉审查） | 无（提案自审） | 无（读 Task Result 的 verified/changed_refs） |
| 11 | Web Search | ✅（执行器+预算标准化，MAX_WEB_SEARCH） | ✅（复用 SW 执行器 import 共享） | ✅ | ✅（继承 DIRECTOR_READ_TOOLS） | ❌ |
| 12 | resource lock | RESOURCE_STORY（project_events.py:28） | RESOURCE_CHARACTER（:29；generate_views 期间角色页只读） | RESOURCE_STORYBOARD（:30） | 共用 Director 的 storyboard 锁域 | 无写锁（PRODUCER_WRITE_OPERATIONS=空，producer.py:71） |
| 13 | OCC / revision | 内容指纹 token（角色/场景/正文）+ expected_revision | _characters_token/_character_token（sha256 前 8 位，仅 name/description_en 参与；character_designer.py:99-125） | shot 域指纹（workspace/ChangeSet 校验） | premises 指纹（subagent/fingerprints.py） | 不适用（不写） |
| 14 | receipts | _base_receipt 统一骨架 + 写后无条件重读 canonical 验证 | 同左（receipts 是唯一权威执行事实） | 同左 + origin marker 写后精确对账 | proposal/journal 落盘 | Task Result（verified/changed_refs 只从 receipts 派生，producer.py:375-403） |
| 15 | truth gate / zero-write recovery | _claims_success 正则 + zero-write recovery + MAX_REPEAT_FAIL=3 + timeout recovery（全套共享基础设施） | 同左（镜像实现，MAX_CORRECTIVE_ROUNDS=1） | 同左 | proposal 必须引用真实 trace（seg_reads 审计，instance.py:245-286） | 【真话纪律】（registry.py 追加）+ 执行性纠偏机制（producer.py:131） |
| 16 | session governance | session_governance.py（get_brief 重锚定 / zero-read gate / clean review）+ work_memory（per-agent work_memory.json） | 同左 | 同左 | delegation 生命周期状态机 + commit journal | 同左 + Production Room 投影 |
| 17 | Producer delegation 入口 | script_writer.delegate | character_designer.delegate | director.delegate | （由 Director 委派，Producer 不感知） | —（自己是入口） |
| 18 | SSE / Production Room | /turn/stream 流式（STREAMED_AGENTS） | 同左 | 同左 | 桥消息经 proposal.read 折叠显示 | GET /agents/producer/room 投影 + bridge_messages.jsonl |
| 19 | UI 显示名称 | "Script Writer"（roomIdentity.ts） | "Character Designer"（roomIdentity.ts:17，emoji 🎨 role="角色设计 · 视觉身份"） | "Director" | （并入 Director 视图） | "Producer" |
| 20 | 测试覆盖 | ~220 tests（20 文件） | 37+16+17（agent/design/long+partial） | 106（7 文件，含 visual/vision/bridge/proposal） | 92（7 文件） | 113（7 文件） |

### 2.3 真实调用图（User → … → Canonical）

```
User
 ├─→ Direct Chat /turn/stream (routers/agents.py:285)
 │     └─ AgentRuntime.dispatch(agent_id)  ──┬─→ script_writer_turn ──→ tool-loop ──→ ProjectStore(project.json)[story/scenes]
 │                                           ├─→ character_designer_turn ─→ tool-loop ─┬→ ProjectStore[characters]
 │                                           │                                          └→ JobQueue(character_views)→ chain 生成→ output/characters/
 │                                           ├─→ director_turn ─→ tool-loop ─┬→ ProjectStore[shots]
 │                                           │                                ├→ JobQueue(first_frame/last_frame/video)→ routers/shots handlers
 │                                           │                                 │   └─→ [BOUND? binding pack / legacy prompt_builder] → Agnes → ImageBatch/VideoVersion
 │                                           │                                └→ scene.delegate → run_scene_agent_instance(只读+proposal) → MD proposal.commit
 │                                           └─→ producer_turn ─→ *.delegate → AgentTask(持久化) → specialist turn → Task Result{verified,changed_refs} → 桥/Room
 ├─→ Production Room (GET /agents/producer/room → build_room_projection ← bridge_messages.jsonl + AgentTaskStore)
 └─→ 专业域 HTTP（Agent 无感知）：
       routers/resources.py(42) ── ResourceWriteService ──→ output/resources.json（Look/Prop/Composition/ReferenceAsset/SharedLibrary 三阶段）
       routers/scene_system.py(45) ── SceneSystem/VisualService ──→ output/scene_system.json（Location/View/Instance/State/Sciv 三阶段）
       routers/shot_binding.py(7) ── ShotBindingService ──→ output/shot_production_bindings.json
       routers/projects.py visual-profile PUT ── VisualProfileService ──→ output/project_profile.json
       routers/sequences.py(11) ── SequenceService ──→ output/sequences.json + sequence_audio_plans.json
```

**关键事实：右侧整块「专业域 HTTP」没有一条边来自 Agent tool-loop——Agent 与它唯一的接触点是在 Job handler 内部被动的、不可见的消费。**

### 2.4 shot_producer 的真实/假双路径（易误解处）

`execute_shot_production`（shot_producer_capability.py:211）有 real/fake 双通道：生产单例 `get_agent_runtime()` 置 `real_shot_pipeline=True`（runtime.py:1033）→ 真实 JOB_HANDLERS + Plan preflight + origin traceability + settlement 对账；fake kinds（agent_first_frame 等）仅测试用（settlement.py:49）。AI-6F 真实管线已接通。

---

## 3. Character Designer Current Reality（重点审计对象）

### 3.A 当前 Character Designer 到底能做什么？

逐项判定（**区分 SYSTEM CAPABILITY EXISTS 与 AGENT TOOL EXPOSED**）：

| 能力 | System capability | Agent tool exposed | 证据 / 说明 |
|---|---|---|---|
| 创建 Character | ✅ | ✅ character.create | 写 name/description_en；重名拒（character_designer.py:284-308） |
| 修改 Character | ✅ | ✅ character.update | 仅 description_en 字段级 OCC（_character_token） |
| 删除 Character | ✅ | ✅ character.delete | 连带删三视图文件+AI 设计草稿（:127-150） |
| 读取 Story | ✅ | ✅ story.list/read（复用 SW 执行器） | 参考资料，非写入对象 |
| 读取 Script | ✅ | ✅ script.read | 同上 |
| Web Search | ✅ | ✅ web.search | 预算内（每轮 3 次） |
| 生成角色三视图 | ✅ | ✅ character.generate_views | chain 生成（front t2i→side/back i2i→rembg）；**⚠️ 该 agent 路径不消费 ProjectVisualProfile**（character_views_handler.py:113 用 legacy art_style）——与 HTTP generate-chain（已接 profile，2026-09-18 修复）不一致 |
| regenerate view | ✅（HTTP /api/character/regenerate-view） | ❌ | 仅 HTTP |
| generate chain（HTTP 独立） | ✅ | （内部同链） | agent 的 generate_views 是同一业务逻辑的 JobQueue 包装 |
| generate sheet 设定图 | ✅（HTTP） | ❌ | |
| review（视觉审查） | ✅（Look/Prop/Composition 域的 vision review） | ❌ **CD 无任何 review 工具**（CD 自己的三视图生成也无审查环节） | |
| approve | ✅（Resources approve 链） | ❌ | |
| 操作 CharacterLook | ✅（service 完整三阶段） | ❌ **0 工具** | |
| 操作 Clothing | ✅（create/attach refs） | ❌ | |
| 操作 Prop | ✅（create/attach/review/approve；无生成端点=系统设计如此） | ❌ | |
| 操作 ReferenceAsset | ✅（register/attach） | ❌ | |
| 操作 CharacterReferenceComposition | ✅（create/generate/review/approve） | ❌ | |
| 读 Shared Library | ✅（SharedLibraryService.read） | ❌ | |
| publish/import Shared Library | ✅（publish/publish_character/import_resource，copy-on-import+身份重映射） | ❌ | |
| 理解 ProjectVisualProfile | — | ❌ **零感知**（prompt/context/工具三层面都不存在；context.py:47 喂的是 legacy `project.style.suffix()`） | |

### 3.B 结构性观察

1. **CD 的写域被有意收窄为「创作身份两字段」**（文件头注释明确：三视图图片资产、分镜、AI 草稿均不写——图片资产除外，generate_views 例外）。这与"角色身份文本是后续一切视觉资产的种子"的设计一致。
2. **CD 与 Production Resources 的关系 = 零**。但下游真实依赖是：Character.views（legacy）→ Look 的 identity ref（generation.py:114 select_character_identity_refs 读的就是 legacy views！）→ approved Look → BOUND binding。即 **CD 今天生产的产品（三视图）其实是 Resources 域的原料**——这个隐式耦合没有任何 Agent 层的显式表达。
3. capability `character.design`（registry 授权存在）= implemented False——"角色设计执行"在 capability 层是占位。
4. **变更影响通路断裂**：CD 改 description_en 后，Resources 域的 Look（其 basis 指纹含 character_identity_fingerprint=name+description_en，generation.py:155）会 stale——但没有任何机制通知 Agent/Producer"角色身份变了，3 个 Look + 2 个 binding 需要刷新"。目前 stale 是被动暴露（生成时 fail）。

---

## 4. Production Resources Current Reality

代码：src/resources/{models,store,service,write_service,generation,look_review,composition_review,prop_review,shared_library}.py；HTTP：routers/resources.py（42 端点）；canonical：output/resources.json。

| 实体 | canonical/ownership | 写路径 | 生成能力 | review/approve | 关键语义 |
|---|---|---|---|---|---|
| Character（叙事身份） | **project.json**（不在 resources.json） | ProjectStore；CD agent 可写 name/description_en | — | — | Resources 通过 `select_character_identity_refs` **只读引用** legacy Character.views（generation.py:114-125）作为 Look 的 identity ref |
| Character.views（三视图资产） | project.json `characters[].views` + output/characters/*.png | CD agent generate_views / HTTP character 端点 / bind 端点 | ✅ | 无审查（人评） | legacy 资产；同时是 Look identity ref 与 legacy 分镜 ref 的双重来源 |
| Clothing | resources.json | ResourceWriteService.create_clothing/attach_clothing_reference | ❌ 无独立生图（作为 Look 的 i2i ref 参与） | 无独立 approve（经 Look 审查间接） | schema：description/design_traits/materials/colors |
| CharacterLook | resources.json | create_look / generate_look_candidates / approve_look_reference | ✅ i2i（identity ref=legacy views，**fail-closed missing_character_reference**） | ✅ 三阶段（vision review→显式 approve）+ 可 revoke | basis 指纹含 character_identity_fingerprint + clothing revision + visual_profile_fingerprint；stale→拒绝复用 |
| Prop | resources.json | create_prop / attach_prop_reference / approve_prop_reference | ❌ **无生成端点**（系统当前不存在；参考图直生+导入） | ✅（attach→review→approve） | 已知口径问题：素底设定图 vs project_style_consistency 维度（Golden Run §六.10 上轮报告） |
| ReferenceAsset | resources.json `reference_assets{}` | register_reference_asset（local/url 二选一，sha256 校验）/ generation commit 自动注册 | — | — | 全域统一资产登记表；lineage=source 字段（generated/manual/direct_t2i/import）；approved 状态属宿主实体（look/prop/composition 各自 approve） |
| CharacterReferenceComposition | resources.json | create_composition（Look 已 approved 才可建）/ generate / approve | ✅（look refs + prop refs 按 budget 选择） | ✅ 三阶段 | binding 语义：relation 枚举 attached/carried/held/near/worn + anchor；**它是 production reference（角色+道具合成参考图），不是 story fact** |
| Shared Library | 全局 agents_data/shared（default_shared_dir） | publish / publish_character / import_resource | — | — | copy-on-import（新 id、媒体复制、origin 记录）；look 导入自动捆绑 clothing；character 导入要求项目内无同名。项目隔离 ✅ |

**哪些 Resources mutation 已有成熟 service、可安全暴露给未来 Agent？**
全部写操作都经 ResourceWriteService（短锁 OCC + 写后重读验证 + verified receipt + 三类 JSONL 记录 + per-look single-flight）——**语义上已 Agent-ready**。最成熟：look 创建/生成/审查/批准、prop 链、composition 链、clothing 创建、reference 注册。

**哪些暂不适合直接暴露给 LLM tool-loop？**
- `revoke-approval` 类（破坏性，需要人类决策语境）；
- Shared Library import（跨项目副作用 + 同名校验复杂，宜高层封装）；
- 直接 register_reference_asset（裸 asset 注册语义抽象层次低，应包在"导入道具/服装参考"高层操作里）；
- 审查口径有已知问题的维度（prop style）——暴露前先修判卷口径（上轮报告 §六.10 P1 遗留）。

---

## 5. Scene System Current Reality（与 Resources 的边界调查）

代码：src/scene_system/{models,store,service,visual_service,visual_basis,instance_view_basis,location_prompt,view_review,instance_view_review}.py；HTTP：routers/scene_system.py（45 端点）；canonical：output/scene_system.json。

实体与成熟度（全部经 Golden Run 实弹）：

| 实体 | 说明 | 生成 | review/approve | stale 机制 |
|---|---|---|---|---|
| Location | 身份描述（非对称特征）+ style_notes | — | — | — |
| SpatialAnchor | 门/窗/楼梯/地标/区域（AnchorKind 枚举 9 种）+ relations | — | — | — |
| SetLayout | 每 Location 至多一个；anchor 归一化平面坐标 hint（models.py:174-176 "只帮 Agent/continuity 理解相对方位"） | 无 | 无 | **schema/service 在、生产链未消费**（Golden Run 未用） |
| LocationView | camera_zone + look_direction + visible_anchor_ids；overhead 有 nadir 正语义锚特判（location_prompt.py） | ✅ hero 先批准→后续机位 cross-view 引用 approved hero（i2i） | ✅ 三阶段 | basis 指纹（location/anchor/profile）变化→stale |
| SceneInstance | story_scene_id ↔ Location 绑定（一场一实例）；绑定时刻快照 location_revision_at_bind | — | — | — |
| SceneState | time_of_day/weather/lighting/environment_conditions/notes + anchor_states（T15 校验）+ prop_placements（T16 校验 prop 存在于 Resources——**唯一的 Resources↔Scene 跨域写校验**） | — | — | patch 即翻新 revision |
| SceneInstanceView | (instance, base view) 身份；BOUND 硬门槛 | ✅（base approved 强制 + 状态编译） | ✅ 三阶段 | instance state 变→stale（"stale but currently valid"可 refresh） |

跨 canonical saga：approve 时经 Resources 注册 ReferenceAsset 再回写 view（visual_service.py:1067-1082 三阶段，orphan 不偷删）——与 Resources 的耦合是**资产登记**，不是 ownership 混合。

### 5.1 Location 应该被 Resource Agent 管理吗？（分层讨论）

- **Location identity（位置本体+anchors+layout）**：它是**空间叙事事实**（story world 的地理），不是"视觉资产"。创建动机来自剧本（s02 在驿站）而非资产需求。归 Resource Agent 会让它拥有叙事事实的写权——与 Script Writer/Director 的域重叠。**倾向非 R2 职责**。
- **Location visual references（LocationView 多机位图）**：这是**纯生产资产**（approved reference 图），语义上与 Look/Composition 同类（generation→review→approve→ReferenceAsset 登记）。**这部分天然像 Resources**。
- **Story Scene**：剧本事实，Script Writer/叙事域，**确定不属于**。
- **SceneInstance**：**叙事-生产的绑定事实**（哪场戏发生在哪）——它把 story_scene_id 映射到 Location 并声明时态。它服务于分镜生产（BOUND），但字段是叙事语义（黄昏/暴雨）。**归属最模糊**，候选：Director（分镜生产的准备）/ Scene Sub-Agent / 独立 Scene Agent / Resource Agent（若定义"一切生产准备"）。
- **SceneState**：同 Instance，且 anchor_states/prop_placements 同时引用 Location anchor 与 Resources prop——**跨域粘合点**。

**明确警示**：前端 IA 中 `characters` 页已并入「生产资源」页（router/index.ts:24-26），scene-system 是独立页——但 **UI information architecture ≠ canonical ownership**（用户指令明确要求区分）。当前前端「生产资源」页含 characters tab 只说明 UI 组织，不构成 Resource Agent 拥有 Character canonical 的论据（Character canonical 在 project.json，属叙事身份域）。

### 5.2 SceneInstance / SceneState 谁负责？（只列事实与耦合，不拍板）

当前事实：创建/patch 全是 HTTP/服务层（Golden Run 用测试脚本驱动）；**无任何 Agent 知道它**。耦合：① SceneState.prop_placements 引用 Resources prop（T16 fail-closed）；② SceneInstanceView 是 ShotBinding 的 hard dependency；③ story_scene_id 引用 project.json 的 scenes。因此它同时依赖三个域。候选方案权衡表见 §21 Open Questions。

### 5.3 场景参考生产准备链是谁的"准备工作"？

链：Location → LocationView（4 机位）→ SceneInstance（+State）→ SceneInstanceView（approved）→ 进 binding/pack。
- **Resource Agent prepares asset**：优点=与 Look/Prop 同构（生成→审→批）；缺点=Instance/State 的叙事语义（哪场戏/什么天气）超出资源语义，且创建 Instance 需要 story_scene_id 知识（跨到叙事域）。
- **Director requests production scene**：优点=消费需求方发起（缺什么补什么，目标明确）；缺点=Director prompt/tool 面大幅膨胀，创作职责被生产流程稀释。
- **Scene Agent manages scene production**：优点=已有 Scene Sub-Agent 基础设施（per-scene delegation/memory/proposal）；缺点=当前 Sub-Agent 是分镜创作提案者（无写权、无生成工具），转型是能力重构而非扩展。
当前架构摩擦点：**没有现成的"生产准备协调者"**——Golden Run 里这一整块由人（用户）+脚本驱动，Agent 层完全缺位。

---

## 6. ProjectVisualProfile

canonical：output/project_profile.json（VisualProfileStore，atomic+锁）；唯一编译点 src/visual_profile/compiler.py。**全部调用点见附录调查（§6.1 摘要）**：

| 消费方 | 消费方式 | 状态 |
|---|---|---|
| Look / Composition 生成 | fail-closed（未设置拒绝生成）+ prompt 后缀 + basis 指纹 | ✅ |
| Prop review | project_style_consistency 维度判卷期望 | ✅（仅审查；Prop 无生成） |
| LocationView / SceneInstanceView 生成+审查+批准 | fail-closed + 指纹进 approve 期望 | ✅ |
| Shot first/last frame（legacy） | `_legacy_vp_style_suffix` seam（routers/shots.py:389-403，2026-09-18 接入） | ✅ |
| Shot（BOUND） | validity 硬依赖（指纹不符→NOT_READY）+ pack ProfileProjection → adapter prompt | ✅ |
| Character HTTP 端点（chain/view/sheet/regen） | `_apply_project_style`（characters.py:88-103，2026-09-18 接入） | ✅ |
| **Agent character.generate_views** | **❌ 绕过**（character_views_handler.py:113 用 legacy art_style） | **缺口** |
| Script Writer / Character Designer / Director 的 prompt/context | **❌ 零提及**（agents 全目录 grep 0 命中；context.py 只喂 legacy StyleAnchor） | **缺口** |
| 任何 agent tool 读/写 profile | **❌ 不存在** | |

谁创建/修改：**仅用户**（项目创建必选 render_family；PUT /visual-profile 带 OCC；UI 在 project-picker 风格对话框）。无任何自动编译/无 Agent 触达。
未来 Resource Agent 是否应拥有 profile mutation 权限：本轮仅分析——倾向**不**（它是项目级风格宪法，改动影响全部 stale 指纹，属人类决策；Agent 可提"修改建议"经人类确认）。留待 Phase B。

---

## 7. Shot Production Binding 与 Reference Pack

代码：src/shot_binding/{models,store,service,validity,reference_pack}.py + src/generation/{image_adapter,video_adapter,native_av_pack}.py；HTTP：routers/shot_binding.py（7 端点）；canonical：output/shot_production_bindings.json。

机制（Golden Run 实弹验证）：binding intent（character_bindings[character_name+look_id] + scene_binding[instance+view]）→ bind 时快照 dependency_fingerprints → validity read model（READY/NOT_READY/UNBOUND+结构化 issues）→ READY→Pack（look refs+profile projection+sciv 编译）→ image/video adapter 编译 prompt+refs → first/last frame 真实生成。cast 校验：binding 角色集合必须 == shot.characters（service.py:632-640，7.md S3B.1）。stale→refresh（唯一快照更新操作，fail-closed 不掩盖 invalid）。NOT_READY→**BLOCK，绝不回落 legacy**（image_adapter.py:51）。

### A. 今天 Director Agent 是否知道这些概念？
**完全不知道。** director.py 2645 行中 binding/look/scene_instance/resources 零提及；precheck（director_visual.py:128-164）只查 shot 存在/锁定/link_mode/首帧就绪。

### B. Director 创建 Shot 后，谁建立 binding？
**没有谁。** 创建 binding 的现实路径只有：HTTP 端点 / Golden Run 等测试脚本 / 未来前端 Workbench。Agent 侧无创建工具、无提示、无意识。Golden Run 中 33 个 binding 全部由执行脚本调 ShotBindingService 建立。

### C. Shot 缺 Look/Prop/Instance/Sciv 时，现有 Director 会怎样？
分两种：① Director 用 shot.generate_images 提交 → 若该 shot **有** binding 但 NOT_READY → Job handler 走 BOUND → adapter 抛 BLOCKED → **Job failed**，Director 收到失败回执但**错误信息里看不出缺哪个 production truth**（除非人去读 validity issues）；② shot **无** binding → 静默走 legacy 链（可能穿错服装/无场景参考，Golden Run s07 实证）——**Director 无法区分这两种情况**。

### D. Director 能自主发现 NOT_READY / 知道缺什么 / 自己补前置吗？
**都不能。** ① 无 readiness 查询工具；② 失败回执不携带 validity issues 结构（blocked 明细在 binding read model 里，Agent 不可达）；③ 就算知道了，**补前置所需的全部动作**（生成 Look/建 Instance/生成 Sciv/生成视图/refresh binding）**没有任何一个是 Director 的工具**。结论：Director 今天在 BOUND 世界里只能"提交→碰壁→失败"，或无意识地落到 legacy。

### E. 多人镜头 cast 如何进入 binding？Golden Run 克隆问题的系统根因
链条：创建数据 `shot.characters` 只写主角（`_golden_create.py` 对 over-shoulder/对手戏只列 1 人）→ cast 校验强制 `binding 角色集合 == shot.characters` → binding 只含主角 Look → 第二角色无 approved identity ref 进 pack → prompt 文本提及时模型即兴/克隆主角变体（Golden Run s06 实证，尾帧 i2i 忠实继承错误）。
**系统级根因**：cast 完整性没有任何 owner——Script Writer 写 shot.characters（创建期）、Director 改分镜、binding service 只做"一致性"校验而不做"完整性"校验（它无法知道谁应该在场）。这是一个**跨域职责真空**：需要在 §十四 归责。

---

## 8. Script Writer 是否需要变化

当前事实：14 工具全部 story/script 域；canonical=story_text+scenes 叙事字段；read_scopes=[story, scenes, characters]；**与 Scene System 零耦合**（不创建 Instance、不知道 Location）；**与 Production Resources 零耦合**；对 characters 只有读（character 域属 CD）。
它创建的 scenes[].characters 字段与 scene 字段（location/time 天气等创作字段）今天恰是 SceneInstance/SceneState 的叙事原料——但这只是数据上的"上游"，无代码耦合。
**结论：维持 Story creative truth owner，不扩。** 无明确理由让它知道生产资源；它的产出（story+scenes+cast）正是 Resource Agent/Scene 准备的输入清单，这个"交接"应由 Producer 路由或新的准备 Agent 承担，而非 SW 自己做 production planning。

## 9. Director：哪些不该变，哪些必须补

**不该变（创作核心）**：shot.create/update/delete/move 全套（经 Workspace 语义）、分镜结构/景别/构图/movement/action/dialogue/rhythm/continuity——prompt 红线（"永不直接覆盖 canonical shots 数组"）与 ChangeSet 流程保持。
**必须补的事实（当前零认知）**：ProjectVisualProfile（风格真相）、binding readiness（生成前查询）、NOT_READY issues 结构、Look/Sciv 等 approved 依赖的存在、cast 完整性。
**Creative Director vs Production Director 是否同一个 Agent**：当前代码里"生产动作"已经以两种形态渗入 Director——① shot.generate_images/videos（真实生成，无生产知识）；② scene.delegate（把分镜设计外包给 Sub-Agent）。两个候选路线：单 Agent 双工具面（prompt 膨胀、context 大） vs 拆分（Director 提"生产意图"→Producer 路由给准备/执行 Agent）。本轮只列权衡：**Director 的生成工具已经存在且真实**（这是既成事实），新增的应是"生产事实查询"类只读工具（readiness/missing-prereq），而非把资源/场景生成塞给 Director。

## 10. Resource Agent 候选职责 R1-R4（分析，不拍板）

| 候选 | 覆盖 | 支持证据 | 反对证据 |
|---|---|---|---|
| **R1** Character-only（+Look/Clothing） | Character、views、Look、Clothing | 与 CD 现有域连续、迁移最小 | Prop/Composition/SharedLibrary 同属 resources.json 同一 canonical 与同一写服务——拆开反而制造两个 Agent 共写一个 JSON 的锁竞争；Look 审查天然牵涉 Composition |
| **R2** Production Resource owner | R1 + Prop + ReferenceAsset + Composition + SharedLibrary | **与 canonical 边界完全重合**（resources.json 全部实体 + 一个写服务 + 一套三阶段协议）；tool 面可分层收敛；Golden Run 全链已被验证可机械化（脚本可跑=Agent 可学） | 仍是 6 实体+共享库，tool 面需克制设计（§15）；ProjectVisualProfile mutation 不宜包含 |
| **R3** = R2 + Location/LocationView | + 场景视觉资产 | LocationView 与 Look 在"生成→审→批→ReferenceAsset"上同构；机位/状态视图是"资产" | Location identity/anchors 是空间叙事事实；SceneInstance 依赖 story_scene_id（叙事域）；把 Location 拆成"事实归 A、视图归 B"会在同一 Location 上制造双 owner（identity 改动→视图全 stale 的联动谁负责？） |
| **R4** = R3 + SceneInstance/SceneState（一切视觉） | + 时态/锚点状态/prop placements | 单一"视觉真相"负责人，Producer 路由最简 | **ownership 过度膨胀**：① 吞并叙事语义（黄昏/暴雨是剧情事实）；② 与 Director 的分镜准备、Scene Sub-Agent 的场景知识三重重叠；③ tool 面 30+、context 爆炸；④ anchor_states/prop_placements 是跨域粘合，放任何一个 Agent 都制造对另一域的写依赖；⑤ Producer 对"角色变了还是场景变了"的路由分辨率下降 |

coupling/context/ownership/routing 四维对比详表见 §21 前的正文（本表为摘要）。**R4 的本质问题：它不是"资源 Agent"，而是"除剧本和分镜外的一切 Agent"——职责定义退化为排除法，违反 clear ownership 原则。**

## 11. Agent identity / rename migration（只调查）

引用全景（探索清查，111 处测试引用 + 55 个持久化文件）：

| 类别 | 位置 | 持久化？ |
|---|---|---|
| registry 常量 | registry.py:23（注释明示"勿改"）、:51；runtime.py:51；capabilities.py 4 处授权 | 否 |
| 系统提示 | registry.py:311、character_designer.py:169（两份 inline） | 否 |
| session 持久化 | sessions.py:49/64/100（agent_id 为 JSON 字段）；**现存 17 个 session 文件含该值**（projects/*/output/agents/sessions/） | **是** |
| Producer 委派 | producer.py:75 `character_designer.delegate`（工具名内嵌 id！）、:81 资源锁映射、:235 schema、:547 分发 | 桥消息/任务文件持久化（bridge_messages.jsonl、task_*.json×9） |
| change_protocol | character_proposal.py:143、service.py:279-280 | plans/proposals/executions 21 文件 |
| Production Room | production_room.py:37 `_SPECIALISTS` | 投影实时 |
| UI 显示 | **roomIdentity.ts:17 单一映射**（name/emoji/color/role）+ agentUi.ts:267 DIRECT_EDIT_AGENTS + agentTurn.ts:227 + agents/index.vue 5 处 | dist 构建物（可再生） |
| 测试 | 25 文件 111 处（test_producer_bridge 最重） | 否 |
| 其它 | inquiry.py/context.py/project_events.py；src/ai/character_designer.py（**模块名本身**）；docs/agents/roles/*.md | work_memory.json×4、direct_edit_audit.jsonl×4 |

**缓解因素**：无 URL 路径、无文件名嵌入 identity（只有 JSON 字段值）→ 迁移=字段重写，不是文件改名。

| 选项 | 内容 | 迁移成本 | 风险 |
|---|---|---|---|
| **A 内部 id 不变，UI 显示 Resource Agent** | roomIdentity.ts 改 name/role；prompt 人设改 | **极低**（~3 文件） | 展示与 identity 语义漂移（旧 id 名不副实）；delegate 工具描述仍是旧名（LLM 可见） |
| **B 正式 migration → resource_designer** | 全部代码引用+持久化字段双写/迁移脚本+25 测试文件 | 高（一次性迁移脚本 + 回归） | 旧项目 sessions/tasks/bridge 里的旧 id 需读取兼容（别名解析）；registry 明文警告勿改 |
| **C 新 Agent + 旧 identity 兼容 alias** | 新增 resource_designer，character_designer 变 alias/退役 | 中 | 双身份并存期路由歧义；Production Room 参与者重复；session 复用逻辑（按 agent_id 匹配）会分裂对话历史 |

## 12. Producer 影响调查

- **delegate 工具名**：`character_designer.delegate` 内嵌 id（producer.py:75）——LLM 可见工具名 + 持久化于桥消息。
- **specialist registry / 路由**：producer.py:81 `_SPECIALIST_RESOURCE`（resource lock 映射，若新 Agent 需新锁语义）；:547-550 分发分支；inquiry.py:38 语义路由表（"character"→character_designer）。
- **PRODUCER_SYSTEM 硬编码三分工**（"三个专业 Agent"、按域路由示例全部写死 SW/CD/Director）——新增第四 Specialist 需改 prompt（本轮禁改，仅标记）。
- **TaskResult.changed_refs：确认硬编码三键**。`_derive_changed_refs`（producer.py:375-403）只扫 receipts 的 scene_id/shot_id/character_name 三字段 + created/deleted/results 同名字段；返回 `{scenes, shots, characters}`。Producer prompt（:112）也按此教学（"changed_refs 为空即未改动"）。**若未来修改 looks/props/locations/scene_instances/bindings：现有 schema 不足**——Resource Agent 改了 3 个 Look + 1 个 Prop 后 changed_refs 恒为空 → Producer 误判"未改动"→ 触发执行性纠偏死循环风险（producer.py:131 的纠偏逻辑以 changed_refs 为准）。**本轮不扩 schema，只报告**。
- Production Room roster：`_SPECIALISTS` 三元组 + 参与者由 target_agent 推导——第四成员需登记。
- workflow persistence / 旧 bridge_messages：receiver/sender 为字符串 id——rename 需读兼容（§11）。

## 13. Assembly / Compositor（工作名）capability inventory（只盘点）

| 能力 | 状态 | 关键实现 |
|---|---|---|
| Shot 视频（i2v/keyframes/multi） | **IMPLEMENTED** | routers/shots.py _handler_video；AgnesClient.create_video；serial 1 RPM；VideoVersion+final_video |
| First/last frame（生成/choose/rehost） | **IMPLEMENTED** | routers/shots.py:740-882；多批次+选择指针 |
| Video versions 管理 | **IMPLEMENTED** | shot.video_versions + set-final + freshness（native_av_pack） |
| 对白配音 TTS | **PARTIAL（双栈）** | 生产栈=Edge-TTS（audio job，可选阶段）；新 A1 栈（MiniMax voice service）**service 层无 HTTP**；A2 lip-sync 同为 service-only |
| Native AV（模型原生声画） | **IMPLEMENTED** | native_av.spoken_language + av_prompt_compiler v3 + dialogue_control fail-closed |
| BGM 生成 | **ABSENT**（AudioPlan 有 music 层位但只引用既有文件；Music generation=NOT IMPLEMENTED，S2 已声明） |
| SFX | **ABSENT**（role=sfx 被 schema 显式拒绝） |
| Ambience | **PARTIAL**（层位+导入文件；无生成） |
| 音频混音 | **IMPLEMENTED** | AudioMixPlan（sequence_audio_plans.json）→ ffmpeg filter graph；基础层=原生镜头音频强制混入 |
| 字幕 | **PARTIAL** | SRT 仅源自 Edge-TTS word timestamps；**Native AV 镜头无字幕源**；不在 Sequence 渲染路径 |
| Sequence 装配 | **IMPLEMENTED** | sequences.json canonical + 3-phase render + 版本 select + ffprobe 真值 + hard-cut concat（S1/S2 冻结） |
| Timeline 编辑/trim/重排 | **PARTIAL** | clip 级 trim+整表替换排序；无逐 clip move/insert/split 端点（设计上明确拒绝做全 Editor API） |
| 转场 | **ABSENT**（仅 hard cut；transition_description 只进 keyframes prompt） |
| 最终导出 | **IMPLEMENTED（三路并存）** | legacy compose（final.mp4+字幕+旁白）/ manifest autopilot compose（保原生音频）/ Sequence render |
| 旁白生成 | **PARTIAL**（narration 混音层位有；**生成入口无**——A1 耦合 Shot.dialogue） |

**结论**：底层大半就绪（Sequence/Mix/Video versions 可直接复用）；**缺口=音频生成类（BGM/SFX/ambience/narration）、转场、细粒度 timeline、native-AV 字幕**。三个并存的 final-export 路径需要 Phase B 前先做收敛决策。

## 14. Golden Run 暴露问题的责任归属分析

| Case | 事实 | 归责分析 |
|---|---|---|
| **1 cast 不完整→克隆** | 创建数据 shot.characters 只写主角→cast 校验强制 binding 等集→第二角色无 ref→模型即兴克隆 | **跨域职责真空，非单点**：① Script Writer/创建端写 cast 不全（创作事实无人复核）；② binding service 只做等集校验、不做完整性判断（它无法知道谁该在场——这需要剧本/分镜语义）；③ 无任何 Agent 在"分镜定稿→生产前"检查 cast↔prompt 一致性。**最自然的负责者：生产准备阶段的 Agent（未来 Resource/Scene 准备职能）+ Producer 的完成判断**；Director 是第二防线（它写 shot.prompt_en，能看出"prompt 提及黑衣客但 cast 无此角色"）。**不是模型问题的部分**：模型忠实执行了"没有 ref 的文本提及"。 |
| **2 night_cloak 遮面矛盾** | identity ref（chain front 脸可见）+ 文本 "face hidden" 冲突→i2i 跟随参考→3 轮全败 | **Character/Resource 域职责**：① CD 生成 chain 视图时无"遮面版本"选项（系统缺口，§3/§四.4 行动项）；② 未来 Resource Agent 若拥有 Look 生成，应在 generate 前做 **prompt-reference 一致性预检**（描述含"hidden/blind/silhouette"而 ref 脸可见→警告/换策略）；③ 审查链正确拒稿（无问题）。 |
| **3 sciv_s07 状态不满足→NOT_READY** | "雨后无雨"4 轮不可生成（模型边界）→ s07 BOUND 全阻塞 | **决策归属（谁决定 regenerate/换参考/回退/问用户）当前不存在**——这是「生产准备协调者」缺口（§5.3）。合理归属：准备 Agent 给出**结构化选项**（再试 seed / 改状态措辞提案 / 该场回退 legacy 的代价说明），**Producer 或用户拍板**；fail-closed 本身（系统拒绝静默降级）是对的。 |
| **4 机位分离不足但过审** | 4 机位构图收敛；审查 view_consistency 实判"锚点可见" | **三三四**：审查维度设计问题（系统——review prompt 把机位判成了锚点可见性，§四.2）+ cross-view 参考机制的设计代价（系统——identity 锚定 vs camera 释放未解耦）+ 模型几何推理弱（模型）。**Agent 侧当前无从谈起**（无人被授权判定"机位是否达标"），若未来 Resource/Scene Agent 拥有机位生成，需为它配真正的机位判定 review，否则 Agent 会被假 PASS 误导。 |

## 15. 工具面调查：不要暴露全部 CRUD

**规模实测**：直接映射=**约 104 个候选工具**（resources 42 + scene_system 45 + shot_binding 7 + visual_profile 2 + characters 生产域 8 ≈ 104；不含 shots/sequences）。当前最大的 Specialist（Script Writer）14 个工具——**104 不可行**（LLM 工具选择退化 + prompt 爆炸 + 回执上下文淹没）。

**分层备选（taxonomy，非最终设计）**：

- **T-A 高层任务型（推荐起点）**：按"生产准备意图"封装三阶段链，每链 1 工具（内部走完整 generate→review→approve 协议，回执带 asset_id/review 结论/失败原因）。示例族：
  - `resource.look.prepare(character, label, appearance, clothing?)`（create+generate+review 循环，review 不 pass 时如实返回拒因）
  - `resource.prop.register_with_reference(prop, image?)`
  - `resource.composition.prepare(character, look, prop_bindings[])`
  - `scene.view.prepare(location, zone, look, visible_anchors[])` / `scene.state_view.prepare(instance, base_view)`
  - `binding.readiness.report(shot_id)` / `binding.refresh(shot_id)`
  - 约 **10-14 个工具**即可覆盖 R2+R3 的 90% 操作；approval 是否自动见 §17。
- **T-B 域级 read + 窄写**：read 工具按域（resource.read(kind,id) / scene.read）+ 写工具只暴露低风险 mutation（create/update 文本字段），生成/审批仍走高层任务型。折中，工具数 ~20。
- **反模式（明确排除）**：① 全 CRUD 平铺（104）；② 万能 `resource.do_everything(...)`（参数不可验证、回执无法定位、违背 receipts 语义）；③ 把 OCC revision 藏进高层工具（Agent 必须仍显式传 expected_revision——这是系统可靠性原则，不能为省事吞掉）。

## 16. Canonical Ownership Matrix

| Canonical | 存储文件 | Current writer(s) | Current readers | Current Agent owner | Future candidate owner | System-only? | 跨域依赖 |
|---|---|---|---|---|---|---|---|
| Story（story_text+scenes 叙事） | project.json | SW agent 工具 / HTTP script 端点 | 全体 agent 读、SceneInstance 引用、binding 引用 | **Script Writer** | 不变 | 否 | →Scene(Instance) 叙事输入 |
| Character（name/description_en） | project.json | CD agent / HTTP | Look identity 指纹、binding cast、全部生成 ref | **Character Designer** | R1/R2（同一 Agent 扩展） | 否 | →Look/Binding |
| Character.views（三视图） | project.json + output/characters/ | CD generate_views / HTTP / bind | Look identity ref、legacy 分镜 ref | **Character Designer** | R1/R2 | 否 | →Resources(identity ref) |
| Clothing | resources.json | ResourceWriteService（HTTP） | Look 生成（ref+指纹） | 无 Agent | **R2** | 否 | →Look |
| CharacterLook | resources.json | 同上 + Golden 脚本 | Composition 输入、Binding character_bindings、validity 指纹 | 无 Agent | **R2** | 否 | →Character/Clothing/Profile/Binding |
| Prop | resources.json | 同上 | Composition、SceneState.prop_placements（T16）、Binding prop_bindings | 无 Agent | **R2** | 否 | →Composition/SceneState |
| ReferenceAsset | resources.json | register + 各域 approve saga 自动注册 | Look/Comp/View 生成 refs、Approved 资产登记 | 无 Agent | **R2（登记表随域走）** | 部分（跨域 saga 注册=系统行为） | 全域 |
| CharacterReferenceComposition | resources.json | 同上 | 生产参考（Look 的补充） | 无 Agent | **R2** | 否 | →Look/Prop |
| Shared Library | agents_data/shared | SharedLibraryService（HTTP） | import 写回 resources | 无 Agent | **R2** | 否 | 跨项目 |
| ProjectVisualProfile | project_profile.json | **仅用户**（创建必选+PUT OCC） | Look/Comp/Prop review/LocationView/Sciv/BOUND pack/legacy seams | **无（零 Agent 感知）** | 只读：R2+Director；mutation：**人类**（Agent 可提案） | **是（建议保持）** | 全域指纹 |
| Location | scene_system.json | SceneSystemService（HTTP/脚本） | View/Instance/审查 | 无 Agent | R3 讨论（identity 倾向 Scene 域） | 否 | →Scene |
| SpatialAnchor / SetLayout | scene_system.json | 同上 | prompt 编译/审查/layout hint | 无 Agent | 同上 | 否 | →View |
| LocationView | scene_system.json(+RA 注册) | SceneVisualService | cross-view 参考、Sciv base、Binding location_view_id | 无 Agent | R3 讨论（视图资产倾向 R2/R3） | 否 | →Anchor/Profile |
| SceneInstance | scene_system.json | SceneSystemService | Sciv、Binding scene_binding、validity | 无 Agent | **待裁决**（§5.2） | 否 | →Story Scene/Location |
| SceneState | 同上（内嵌 instance） | patch_scene_state | Sciv 状态编译、freshness | 无 Agent | **待裁决** | 否 | →Anchor/Prop（跨域 T15/T16） |
| SceneInstanceView | scene_system.json(+RA) | SceneVisualService | **Binding 硬门槛**、shot pack | 无 Agent | 待裁决（跟随 Instance） | 否 | →Profile/RA/Binding |
| Shot | project.json | Director agent / HTTP / 脚本 | binding cast 校验、pack、生成、sequence 读 | **Director** | 不变 | 否 | →全部生产域（作为绑定目标） |
| ShotProductionBinding | shot_production_bindings.json | ShotBindingService（HTTP/脚本） | image/video adapter、validity、pack | 无 Agent | 待裁决（Director 读；写=准备 Agent 或 system） | 部分（refresh 语义系统性） | →Look/Sciv/Profile/Shot |
| ImageAsset（帧/候选/视图产物） | project.json batches + output/frames|scene_system|resources | 各生成 job | choose/review/sequence 间接 | 无（系统产） | **系统** | — |
| Video | project.json video_versions + output/videos/ | _handler_video | sequence 读 final_video、freshness | 无（shot_producer capability 可触发） | **系统** | 是 | →Sequence |
| Audio | output/audio/（Edge-TTS）+ dialogue_audio.json（A1）+ sequence_audio_plans.json | audio job / VoiceService（service-only） | compose/sequence mix | 无 | **系统**（生成缺口见 §13） | 是 | →Sequence |
| Sequence | sequences.json | SequenceService（HTTP） | 渲染/前端 | 无 | 未来 Assembly Agent 只读消费 | 是（写服务收敛） | →Shot.video |

## 17. Read / Write / Generate / Review 权限矩阵（未来视角建议，供裁决）

对 R2 候选（Resource Agent）逐域建议 + 依据：

| 域 | READ | WRITE CANONICAL | GENERATE ASSET | REVIEW/APPROVE |
|---|---|---|---|---|
| Character | ✅ | ✅（现状延伸） | ✅ generate_views（先修 profile 缺口） | ❌（人评三视图） |
| Clothing/Look/Prop/Composition | ✅ | ✅ create/update 文本 | ✅（系统三阶段内自动） | **review 可自动（vision 判卷），approve 分档**：生产推进流（如 Golden Run 式批量）可"pass 即 approve+留撤销痕"；创意定稿流留给人类——系统 approve 语义本就是显式 mutation+可 revoke，具备两档实现的全部基础（write_service approve 均独立 receipt） |
| ProjectVisualProfile | ✅（只读指纹+内容） | ❌ 建议人类独占 | — | — |
| Location/View/Instance/Sciv | 待 R3 裁决 | 待裁决 | 若归入则同 Look 模式 | 同上 |
| Binding | ✅ readiness/issues | 仅 `refresh`（低风险语义）+ 创建建议留给 Director/系统流 | — | — |
| Shot | ✅（读 cast/prompt 做 cast 校验） | ❌（Director 域） | ❌ | — |

关键原则重申（§十八）：generate 自动 ≠ approve 自动；review 结论是**证据**（review_records.jsonl），approve 是**显式 mutation**——两层在现有系统已物理分离，Agent 设计不应合并它们。

## 18. 现有可靠性原则的适配检查

逐条对照（无违反，列出未来设计的约束落点）：
- canonical 是事实 / Agent memory 不是：work_memory 仅注入上下文，从未参与生成依据 ✅ 保持。
- OCC/revision：高层任务型工具必须透传 expected_revision（§15 反模式③）。
- verified receipts：TaskResult.changed_refs 现状不足以表达新域（§12）——Phase B 需扩 receipts 派生规则（先扩 receipts 扫描字段，changed_refs 键集可加 look_ids/prop_ids/location_ids/binding_shot_ids——属 schema 变更，本轮不动）。
- fail closed：BOUND/visual profile/missing ref 的 fail-closed 是 Agent 化的**前提资产**（Agent 能收到确定性错误）✅。
- Producer 不直接写 Specialist canonical / Specialist 走真实 service：新 Agent 沿用 capability→service adapter 模式（capabilities.py §6 不变量）即可，无需新机制。
- 不复制第二套 canonical：R2 的 resources.json 单一写服务天然满足；**警惕** SceneState/anchor 若归 Resource Agent，绝不允许它绕过 SceneSystemService 直写 scene_system.json。

## 19. 向后兼容风险清单

1. **identity rename**（§11）：持久化 JSON 字段值 55 文件 + delegate 工具名 + 25 测试文件。
2. **changed_refs 扩键**：旧 workflow/Room 投影对新键的容错（production_room.py:245 已做 isinstance 容错，风险低但需回归）。
3. **新 Specialist 进入 roster**：PRODUCER_SYSTEM 硬编码"三个专业 Agent"的 prompt 改动会影响既有路由行为（regression 面=producer 全部测试 113）。
4. **Session 复用键**：若 Option B/C，(agent_id, scope) 复用逻辑会割裂旧对话历史（sessions.py:49-58）。
5. **profile 缺口修复**：agent generate_views 接入 profile 后，旧项目（profile unspecified）走 fail-closed 还是 legacy 降级——需与 HTTP 端点行为对齐（_apply_project_style 是宽容降级，生成域是 fail-closed——**两种口径并存**，Phase B 需统一裁决）。
6. **fake shot handlers**：agent_* kinds 仍注册于 routers/agents.py:62（注释"AI-6F 接通真实生成后移除"——真实管线已接，fake 未拆）——清理属低风险兼容性收尾。

## 20. Blockers（进入 Phase B 前的已知阻塞/前置）

1. B1：changed_refs/receipts 派生规则不覆盖新域（§12）——Resource Agent 的一切 Producer 协作都建立在此之上。
2. B2：ProjectVisualProfile 的 Agent 认知为零 + agent generate_views 绕过 profile（§6 两缺口）——Resource Agent 若先于修复上线，会延续"双风格真相"。
3. B3：prop review 风格维度判卷口径（上轮遗留 P1）——若 R2 拿到 prop 链，口径问题会被自动化放大。
4. B4：机位判定 review 缺失（§14 Case 4）——场景视图自动化前需要真判机位的审查。
5. B5：三个并存 final-export 路径（§13）——Assembly Agent 设计前需收敛决策。
6. B6：cast 完整性 owner 缺位（§7.E）——绑定链自动化的前置。

## 21. Open Design Questions（留给 Phase B 裁决）

1. SceneInstance/SceneState 归属：Director 准备 / Scene Agent / R2 扩展 / system-only+HTTP？候选对比见 §5.2。
2. Location identity 与 LocationView 是否拆分双 owner（R3 的核心难题）。
3. Director 单体双面 vs 拆 Production Director（§9）。
4. approve 的两档策略（生产流 pass-即-approve+revoke 痕迹 vs 定稿流人批）按项目/按阶段配置还是全局固定（§17）。
5. identity Option A/B/C（§11）。
6. Resource Agent 与 Script Writer 的"上游清单"交接：由 Producer 路由（SW 完成→Producer 触发资源准备）还是 Resource Agent 主动读 story 变更（事件驱动）？当前 project_events.py 有 character.changed 等事件基建。
7. profile 双口径（宽容降级 vs fail-closed）统一（§19.5）。

## 22. Recommended Phase B Design Topics（建议议题，非决定）

1. **Resource Agent capability 契约**（R2 起步）：工具面 T-A 分层（§15）、四类权限（§17）、envelope/TaskResult 扩展、与 change_protocol 的关系（Look/Prop 的修改是否也要语义提案通道，还是沿用直写+OCC——CD 现状是直写）。
2. **Production readiness 查询与补前置流程**（打通 §7.D）：Director/Producer 可读 readiness+issues；补前置委派给 Resource/Scene 准备职能。
3. **SceneInstance/SceneState 归属专题**（§21.1）——建议单独一轮设计。
4. **Director Production Integration**：只读事实注入（profile 摘要/readiness/cast 校验）先行，生成权不扩。
5. **Agent VisualProfile 认知**（B2 修复）：context 注入 profile 摘要 + generate_views 接 profile。
6. **Assembly Agent 预研**（待 B5 收敛后）。

---

## 二十、最终问题逐条回答

**1. Character Designer 当前真实职责是什么？**
角色**叙事身份**的唯一 Agent owner：创建/修改/删除 project.json 中 Character 的 name/description_en（字段级 OCC），并生成/更新三视图（chain+rembg，JobQueue durable）；读 story/script 作创作上下文、可 web search；对下游（Look/Binding/生成 ref）产出的资产**零认知、零工具**。

**2. 它为什么已经不足以覆盖当前系统？**
因为"角色的生产视觉身份"已从三视图演进为 Look+ReferenceAsset 链（Resources 域），"场景的生产视觉身份"演进为 LocationView/Sciv 链（Scene 域），"镜头的生产依据"演进为 Binding/Pack——这三条链是 Golden Run 实证的生产主干，而 CD（以及所有 Agent）对它们 0 工具/0 认知；CD 的产物（三视图）如今只是 Resources 域的原料（identity ref），且其 agent 生成路径还绕过了 profile（风格真相）。

**3. "Resource Agent" 是否有真实必要性？**
有，依据三点：① 生产主干（Golden Run 19+33 次三阶段操作）目前 100% 由人+脚本驱动，无 Agent 化路径，是最大的人工瓶颈；② resources.json 六实体共享一个 canonical、一个写服务、一套三阶段协议——天然是一个所有权单元；③ Golden Run Case 1/2/3 的归责都落在"生产准备无人负责"这一空位上。

**4. 哪些 Resources 对象最自然归 Resource Agent？**
Clothing、CharacterLook、Prop、ReferenceAsset、CharacterReferenceComposition、Shared Library（publish/import）——与 resources.json 边界完全重合（R2）；Character 本体延续现有 CD 职责（R2 含 R1）。

**5. Location 应该归 Resource Agent 吗？**
**不建议整体归入。** Location identity/anchors/SetLayout 是空间叙事事实（与剧本/分镜同源），归 R 会给资源 Agent 叙事写权；LocationView/Sciv 等**视觉参考资产**与 Look 同构、可归（R3 的合理内核）。即：倾向"视图资产归资源职能，空间事实归场景/叙事侧"，或维持 Scene System 独立准备职能——需 Phase B 裁决。

**6. SceneInstance / SceneState 应该归 Resource Agent 吗？**
**倾向否（R4 的主要反证）**：它们是"哪场戏发生在哪+什么时态"的叙事-生产绑定事实，含 anchor_states/prop_placements 跨域写校验；归 R2 会让它拥有剧情语义写权并与 Director/Sub-Agent 重叠。更自然的候选是 Director 的生产准备流或场景准备职能（§5.2 只列权衡）。

**7. ProjectVisualProfile 谁应该拥有？**
**人类拥有 mutation**（现状即是，且 fail-closed 全域依赖它）；Agent（R2+Director）应获得**只读认知**（当前为零）；Agent 提案修改经人类确认。不建议任何 Agent 直接 mutation。

**8. Resource Agent 是否应该拥有 generation 权限？**
**应该**（这是它存在的核心价值），但必须走现有 service 三阶段（generate→vision review→显式 approve 通道），durable、receipts、不直连模型——与 character.generate_views/shot.generate_images 同一模式。

**9. Resource Agent 是否应该拥有 review / approval 权限？**
review：可自动（vision review 本就是系统判卷，Agent 触发即可，结论是证据）。approval：**分档**——生产推进流可"pass 即 approve（可 revoke，痕迹完整）"，创意定稿流保留人工显式批准；系统 approve 语义（显式 mutation+独立 receipt+可撤销）已具备双档基础（§17）。不建议无条件自动 approve 一刀切。

**10. Director 当前最缺哪些 production capabilities？**
① binding readiness/NOT_READY issues 的**只读查询**（现在提交即盲）；② 生产依赖存在性的认知（Look/Sciv/profile 摘要注入）；③ cast↔prompt 一致性的校验视野；④（可选）refresh binding。**不需要**：资源/场景的生成权（交给准备职能）。

**11. Script Writer 是否应该保持基本不变？**
**是**。零耦合证据充分；保持 Story creative truth owner，不扩 production planning 职责。

**12. Producer Bridge 要改哪些地方？**
最小集：① delegate 工具名/描述与 _SPECIALIST_RESOURCE 映射（新增成员或改名）；② PRODUCER_SYSTEM 的三分工叙述与路由示例；③ inquiry.py 语义路由表；④ production_room.py `_SPECIALISTS` roster；⑤ roomIdentity.ts 显示映射；⑥ **changed_refs 派生扩展**（前置阻塞 B1）；⑦ 若改名：§11 全表。

**13. 是否应该保留 character_designer 作为兼容 identity？**
**倾向 Option A**（内部 id 不变、UI/人设显示演进为 Resource Agent）：持久化面 55 文件、零 URL 耦合但 registry 明文"勿改"、25 测试文件引用——Option A 以~3 文件成本达成全部业务目标；B/C 的收益（名义一致性）不抵迁移与历史割裂风险。最终由设计方裁决。

**14. 当前系统是否已具备未来 Assembly/Compositor Agent 所需的大部分底层能力？**
**是（约 70%）**：Sequence canonical+渲染+版本选择、AudioMixPlan、Video versions+freshness、first/last frame 链全部 IMPLEMENTED 且服务收敛（只读消费 Shot.final_video 的设计正是 Agent-friendly）。缺口=音频**生成**（BGM/SFX/ambience/narration）、转场、细粒度 timeline、native-AV 字幕、三条 final-export 路径的收敛。

**15. 下一阶段最应该先设计 Resource Agent，还是 Director Production Integration？**
**先 Resource Agent（R2 范围）**：它是 changed_refs/receipts 扩展、四类权限模型、三阶段 Agent 化模式的**第一块试验田**，且 Golden Run 已给出完整机械化脚本可作对照；Director Integration 依赖 readiness 查询等接口，其中大半由 Resource/准备职能提供——顺序上后者自然跟进。两议题有共享前置（B1/B2），可并行设计但 Resource Agent 先落地。

**16. 有没有任何事实表明我们不应该把 Character Designer 演进成 Resource Agent？**
有**三个**需要正视的事实（均不构成否决，但构成边界条件）：① Character canonical 在 project.json（叙事身份域），Resources 只是视觉资产域——R2 必须保持"角色身份文本"与"视觉资产"的分层（现 CD 的字段级 OCC 正是这个分层的体现），不能让资源 Agent 无差别改写叙事身份；② Golden Run 证明 Agnes 模型边界（机位/去雨/遮面）不被 Agent 化解决——Resource Agent 的价值是**流程自动化与归责**，不是画质本身（画质主线=2K 档与模型侧，勿混为一谈）；③ changed_refs/能力授权/prompt 三处硬编码的"三分工"意味着引入第四 Specialist 的回归面集中在 Producer 域（113 tests）——是可控成本，但必须作为 Phase B 的一等公民任务，而非附带改动。

---

**报告位置**：`animation-storyboard/AGENT_SYSTEM_V2_CURRENT_STATE_AUDIT.md`（本报告）· 调查证据：代码 file:line 均在文内 · Golden Run 证据 `_acc_evidence.json` + `GOLDEN_RUN_REPORT.md` · 测试基线 2029 collected
