# AGENT SYSTEM V2 — PHASE C2 FINAL REPORT
# Scene Agent V1 — Scene Production Preparation（28.md）

> 收口日期：2026-09-19
> 分支：`feat/agent-v2-scene-agent-c2`（基于 C1 FINAL `d059c39`）
> 结论先行：**Scene Agent 已成为可被 Director 消费的场景生产部门**。§29-§33 真机 E2E 全部通过，无 BLOCKER。建议进入 Phase D（Full Producer Production Workflow）——按既定协议，**完成后停止，勿自动开始**。

---

## 1. C1 frozen baseline

- 分支 `feat/agent-v2-director-production-c1` HEAD `d059c39`（C1 FINAL report commit）；
- Preflight 复跑：C1 新增测试 + Producer bridge + Production Room = 98 passed；
  Scene System S1/S2A/S2B 核心 = 53 passed；Resources 关键回归 = 63 passed；
- 工作树干净（仅 untracked scratch），冻结基线可复现。

## 2. Scene Agent identity

- 内部稳定 id：`scene_designer`（不用 `scene_agent`，避免与 Director Scene Sub-Agent 混淆，§0.1）；
- 用户可见名：**Scene Agent**（Scene Production Designer / 场景设计师），role 显示「地点 · 空间 · 场景状态 · 场景参考」；
- 第四个正式 Specialist：Producer / Script Writer / Resource Agent / **Scene Agent** / Director；
- 独立 ModelProfile：`scene_profile`；`/api/agents` 列出 9 个 identity（加法式扩展）。

## 3. Scene Sub-Agent distinction

- Director 内部 Scene Sub-Agent **零改动**：继续做单故事段导演/分镜创作提案，仍折叠在 Director 域；
- Production Room 顶层 roster 只显示 Scene Agent（production_room.py `_SPECIALISTS` 四元组），Scene Sub-Agent 不重复显示（§26）；
- 冻结断言（t26/t47）演进为白名单：`production_context.py`（只读层）/ `scene_designer.py`（唯一写入者）/ `project_events.py` / `producer.py`（锁域常量）；其余 Agent 源仍禁止 scene_system 引用。

## 4. Ownership

- Scene Agent **独占** Scene System 写权：Location / SpatialAnchor / SetLayout / LocationView / SceneInstance / SceneState / SceneInstanceView；
- 全部写入经 SceneSystemService / SceneVisualService 正式链路（OCC + 文件锁 + 落盘重读验证）；零直接写 JSON、零第二套 canonical；
- 边界由工具面物理保证：Story / Character / Clothing / Look / Prop / Shot / Binding / ProjectVisualProfile 无任何写工具。

## 5. Canonical boundaries

| 域 | Owner | Scene Agent 权限 |
|---|---|---|
| Story/剧本 | Script Writer | 只读（story.list/read/script.read） |
| Character/Look/Clothing/Prop/Composition | Resource Agent | 只读（resource.inventory/read，§0.6） |
| **Scene System** | **Scene Agent** | **读写（scene.* 工具）** |
| Shot/Storyboard | Director | 只读（shot.list/read） |
| Binding | Director | 不碰（经 Director 的 binding 读取 Scene 生产上下文） |
| ProjectVisualProfile | 用户 | 只读（project.visual_profile.read） |

## 6. Final tool surface（17 个，§6 目标 14~18 内）

读（10）：story.list / story.read / script.read / project.visual_profile.read / shot.list / shot.read / web.search / **scene.inventory** / **scene.production.inspect** / resource.inventory + resource.read（§0.6 只读定位 Prop id——合计 10 个只读通道）
写（7）：scene.location.upsert / scene.anchor.upsert / scene.layout.set / scene.view.prepare / scene.instance.prepare / scene.state.update / scene.state_view.prepare
- 写工具单写串行 + 回执账本 + scene_system 锁域（WRITE_TOOL_DOMAIN 单一权威映射）；
- view/state_view 标注 `_long_task`（流式进度 + 协作取消）。

## 7. VisualProfile integration

- scene.view.prepare / scene.state_view.prepare 全部经 SceneVisualService 生成路径——profile-aware fail closed（unspecified → project_visual_profile_required 拒绝生成）；
- 审查含 `project_style_consistency` hard 维度（P1 口径）；
- system prompt 注入 profile 紧凑摘要；Agent 无 profile mutation 工具。

## 8. Scene System lock

- 新锁域 `RESOURCE_SCENE_SYSTEM = "scene_system"`（§5）+ `scene_system.changed` 事件；
- 流式端点与 Producer Bridge 均按 WRITE_TOOL_DOMAIN 逐工具挂锁；finally 释放；
- 前端：`sceneSystemLocked` → Scene Agent 聊天输入锁定提示「Scene Agent 正在准备场景系统」；
- Director 可同时做纯 Shot 工作不被误锁（不同域）。

## 9. production.inspect

- 输入 story_scene_id → 剧情输入 / Scene System 现状（instance/location/anchors/layout/state/views/scivs）/ VisualProfile 紧凑摘要 / 资源依赖核验（prop 存在性+批准态）/ 生产状态判定；
- 状态阶梯（复用 service stale 读模型，零重实现）：NO_LOCATION → INSTANCE_MISSING → LOCATION_INCOMPLETE → BASE_VIEW_MISSING → STATE_INCOMPLETE → SCIV_MISSING → SCIV_STALE → READY；
- 真机验证：s01/s02 推进全程由该工具导航，最终双双 READY。

## 10-12. Location / SpatialAnchor / SetLayout

- scene.location.upsert：创建（document OCC）/更新（location revision OCC，name 可选）；schema 明示「地点长期身份 vs 某一幕的样子」；rename 不变 location_id（S25/S30）；
- scene.anchor.upsert：结构性锚点导向（schema 明示 east gate/west chimney 优于装饰性小污点）；relations 经 service fail-closed 校验（S28 非法 relation 拒绝）；
- scene.layout.set：一 location 至多一个 layout（已有→需 layout revision 更新）；schema 明示语义方位 hint 非物理 CAD、禁止毫米级假精度（S29）。

## 13. LocationView

- scene.view.prepare（§11 高层工具）：建/改 view 定义 → generate（一次正式生成尝试）→ vision review → approve_if_pass 且真实 pass 自动批准；
- 一次调用 = 一次生成尝试；系统无静默重试；
- 机位策略写入 system prompt（§12）：先 hero，按生产需要补视角，不凑 8 视角。

## 14. Camera review hardening（Work A，§2）

- 新共享模块 `src/scene_system/camera_review.py`：
  - `camera_zone_group`：词元语义组（overhead/side/rear/front；compass 自由串不误判、countryside/hilltop 子串陷阱不命中）；
  - `camera_expectation_block`：审查 prompt 机位语义块——相机位置先于且独立于 anchor 可见性、feature checklist 假视角=反证（所有 anchor 同框正面图 = side/rear/overhead 声明的 FAIL 证据）、overhead=真俯视（无地平线无天空）；
  - `derive_camera_view_consistency`：确定性推导——observed 与 requested 矛盾→fail；无证据/uncertain→pass 封顶 uncertain（fail closed）；observed=other 且 requested=overhead→fail；
- LocationView + SceneInstanceView 两处审查同步加固（schema additive 加 camera_observed_zone/camera_evidence）；
- §2.4 诚实原则落实：假 overhead（正面 hero 图）真机被判 needs_fix 不放行；rear/overhead 未获批准。

## 15. SceneInstance

- scene.instance.prepare：one-active-instance 校验零弱化——instance_already_active 时返回现有 ids + 复用指引，绝不静默建第二个（S42）；
- story_scene/location 存在性校验（S40/S41）；绑定快照 location_revision_at_bind（S43 映射稳定）。

## 16. SceneState

- scene.state.update：patch 语义（字符串字段 + anchor_states + prop_placements 整 dict 替换）；
- anchor 跨 location 校验拒绝（S45）；prompt 纪律：「剧情只写夜晚→不无理由改成暴雨」。

## 17. Prop prerequisite（§15）

- prop_not_found → 结构化 `resource_prerequisite`：{code, story_scene_id, missing_props:[{prop_id, requirement{anchor_id,state,note}, why, target_field}], required_capability: resource_agent}；
- turn 级 prerequisite_warnings 聚合供 Producer 路由；canonical 零写入、零伪造引用（S47）；
- §0.6 配套：resource.inventory/read 只读工具让 Agent 能把剧情物品名映射到真实 prop id（真机发现的缺口，Commit H2 补齐）。

## 18. SceneInstanceView

- scene.state_view.prepare：base approved fail-closed（含审批证据漂移校验）→ generate（SceneState+props+profile 由 service 编译，Agent 不拼 provider request）→ vision review → approve_if_pass 自动批准；
- (instance, base view) 对已存在 Sciv 时返回现有 id + 复用指引（稳定身份）；
- basis 指纹含 instance revision——SceneState 真改变 → guard 新 basis 放行（S53/S54）。

## 19. Approval policy（§17）

- 与 Resource Agent 完全一致：production-ready → approve_if_pass；探索 → review_only；review_only 恒不批准（S34）；
- 无 revoke 工具（用户 UI 显式操作）；审查 pass ≠ 自动批准（显式 mutation 才算）。

## 20. Retry guard（§18/§11）

- C1 的 per-resource guard 抽取为共享 `src/agents/review_retry_guard.py`（Resource/Scene Agent 共用，不复制两套）；
- key=稳定 view/sciv id；basis=语义输入指纹（camera_zone/anchors/label/instance revision 等）；
- 同 basis 连续 3 次 needs_fix → `review_retry_exhausted`（零生成零审查）；换 basis 放行；pass 清零；
- 更新路径 guard 在 OCC update 之前（被拦截不消耗 revision）；换 seed 不算 basis 变化（工具面本就不暴露 seed）。

## 21. Receipts / OCC

- 全部写回执：ok/verified/stale/unchanged/revision/document_revision_after/changed_resources（真实 verified 派生）；
- service 的 stale_revision 错误码规范化为 stale 布尔（与 Resource Agent 契约一致）；
- receipts_meta 透传 detail/code（Producer 可见失败根因，Commit H1）。

## 22. changed_refs（§22）

- 新增四键：`locations` / `location_views` / `scene_instances` / `scene_instance_views`；
- anchor/layout 变更归入 locations、SceneState 归入 scene_instances（不为 child entity 加键）；
- 只从真实 receipts 的 changed_resources 派生；`_empty_changed_refs` 全键骨架；artifact_refs 类型映射同步；旧 TaskResult 无新键容错（S61）。

## 23. Producer integration（§23/§25）

- `scene_designer.delegate` 第四 delegate（schema 含 owns/适用/不适用/要点四段）；`_delegate_specialist` 分支（单写域锁按工具逐域挂）；
- PRODUCER_SYSTEM 四专业分工（§24）：Script Writer=故事 / Resource Agent=角色与生产资源 / Scene Agent=地点、场景状态和场景视觉参考 / Director=分镜、镜头和 Shot binding；Producer canonical writes = 0；
- 前置路由（§25，不硬编码 pipeline）：scene_production_prerequisite→Scene Agent；Scene Agent 的 resource_prerequisite→Resource Agent；visual_profile_unspecified→如实报告用户；cast_incomplete→Director 自修。

## 24. Production Room/UI（§26/§27）

- Room roster 四 Specialist（scene_designer 🏞️ 地点·空间·场景状态·场景参考）；历史参与者不变；changedRefsText 显示场景域键；
- Agents 页 `STREAMED_AGENTS` + sceneSystemLocked；Scene Agent 支持 direct chat / Producer delegation / SSE streaming / tool timeline / session persistence / work memory / resource lock display。

## 25. Mock tests

- `tests/test_scene_designer_agent.py`：55 测试（S1-S65 覆盖清单全部落地；S21/S22 stale 用 monkeypatch read 模型、S36/S37/S55 覆盖机位/anchor fail closed）；
- `tests/test_scene_camera_review.py`：9 测试（Work A）；
- 回归面：test_agents（registry 演进为 9 identity 加法式断言）/ test_resource_agent_identity / test_production_room / test_producer_bridge / test_producer_changed_refs_v2 / C1 director production 全过。

## 26. Standalone real Scene Agent E2E（§29）

scratch 项目 `__c2_scene_e2e`（3 故事段同一地点不同状态 + 真实 stylized profile），自然语言目标（不给工具顺序），真机 turn 286.5s：

| 验证 | 结果 |
|---|---|
| 同一地点不重复创建（locations=1） | PASS |
| Story canonical 零修改（指纹 388a70361cc2 恒定） | PASS |
| Resources 零越权（props/looks/clothings/compositions 零变更） | PASS |
| 2 approved LocationView + 参考图真落盘 2/2 | PASS |
| 两个 SceneInstance + 2 approved SceneInstanceView | PASS |
| Director 共享只读层可见（s01/s02 均 READY） | PASS |

**8/8 PASS**。Agent 全程自主：inspect 导航 → 建地点/锚点/layout → hero view（审查 pass 自动批准）→ 绑定两故事段 → 各自状态 → 各自 Sciv approved。

## 27. Multi-view real E2E（§30）

- 非对称地点多机位请求（hero/side/rear/overhead），turn 345.2s；
- 结果：front + side×2 = **approved**（有真实 pass 审查证据）；rear + overhead = **needs_fix 未批准**；
- 诚实性 PASS：所有 approved 都有真实 pass 证据；失败视角零批准；
- **MODEL_LIMITATION 记录**：Agnes 在该地点的 rear/overhead 机位被 vision 审查判定实际构图仍偏正面（feature checklist 假视角）——审查正确拦下，模型能力边界如实上报（§2.4/§30 目标达成：不是 4/4 成功，而是诚实区分成功与失败）。

## 28. State-change real E2E（§31）

- A（晴朗→暴雨）：s01 状态 day/heavy_rain 更新成功，stale Sciv 重新生成；
- B（暴雨→雨后无雨）：s02 状态 day/clear 更新成功——「去状态」本次模型可处理（Golden Run 的「加易去难」边界在本例未触发，但重生成中有一次 needs_fix 被诚实拦下后重试成功）；
- Story canonical 全程零修改；两次 turn 63.2s / 171.9s。

## 29. Missing-prop E2E（§32）

- Scene Agent：inspect → **resource_prerequisite 结构化上报**（copper_compass 缺失 + target_field）→ 零 Prop 越权写入 → 零伪造引用 → turn 级 prerequisite_warnings 供路由：全 PASS；
- Producer 路由 smoke：Producer → Resource Agent（登记 prop；首张参考图被我方 seed 为写实产品照，审查**诚实判 needs_fix**——风格与项目 stylized gouache 冲突）→ 提供手绘风格新参考图 → Resource Agent 审查 pass 批准 → Scene Agent 用真实 prop id 摆放 + s03 Sciv 推进到 READY；
- 闭环 PASS：s03 state 含 prop_6e4fb41bf965、s03 对 Director 可见（READY）、Story 零修改。

## 30. Scene Agent → Director joint E2E（§33）

scratch 项目 `__c2_scene_dir_e2e`：Story（1 段 1 镜）+ Character + approved Look（Resource Agent 真机准备），**无 Scene System**。单条 Producer 目标（286s）：

「把第一段的正式场景准备好，然后让导演检查该镜头的生产准备、把已批准的造型和场景参考绑定到 s01_c01，并按 bound_required 正式生成关键镜头首帧。不要修改故事。」

**九项验证（14 断言）全 PASS**：

| # | 验证 | 结果 |
|---|---|---|
| ① | Producer 零直接写（回执仅 scene_designer.delegate + director.delegate） | PASS |
| ② | Scene Agent 只写 scene 域（changed_refs 仅 scene 键非空） | PASS |
| ③ | Director 只写 Shot/Binding 域 | PASS |
| ④ | Resource canonical 零越权（props/compositions/characters=0，looks=setup 产物） | PASS |
| ⑤ | generation pack 含场景参考（binding scene_binding 含 pinned Sciv sciv_3fed0eb2e462） | PASS |
| ⑥ | binding_state=bound + readiness=READY（无 legacy fallback） | PASS |
| ⑦ | changed_refs 含 scene 四键 + shots + bindings | PASS |
| ⑧ | BOUND 首帧真生成（1 batch）+ Room 正确显示两 Specialist | PASS |
| ⑨ | bridge task parent 链：task_a5b48c66d66b(scene) → task_d68efa809d76(director) | PASS |
| ⑩ | Story canonical 剧情字段零修改 | PASS |

> Scene Agent 正式成为可被 Director 消费的场景生产部门。

## 31. Regression

- 最终全量：`pytest tests/ -q`——**2208 passed / 0 failed**（C1 基线 2142 + 本轮新增 66，零回归）；
- 中途曾现 3 failed（t26/t47/C52）——均为冻结断言需按 §22/§23/§25/§26 演进，已随 Commit H1 演进并复跑通过；
- s2b1 四项测试在部分选择运行时失败 = **预存问题**（跨测试文件 import 的选择依赖；C1 基线 d059c39 同样失败，整目录运行通过），非本轮回归；
- 前端改动（STREAMED_AGENTS/roomIdentity/projectEvents store）为纯增量，无构建破坏（esbuild 通过）。

## 32. Deviations

1. **tools 面多 2 个只读工具**（resource.inventory/read，§0.6 授权范围内）：真机发现 Agent 无法把剧情物品名映射到真实 prop id——补最小只读定位能力；总量 17 仍在 §6 区间；
2. **registry 断言演进**：test_agents「固定 8」→ 9 + 旧 8 全在（§4 明确授权的加法式扩展语义）；
3. **t26/t47/C52 冻结断言演进**：C1 时代「场景无人可写」的措辞由 C2 事实取代（白名单 +producer.py；C52 改断言 Scene Agent 路由）；被禁 API 清单对 production_context 仍然生效；
4. **commit 计划微调**：§37 的 H 拆为 H（集成）+ H1（空参守卫+断言演进）+ H2（§0.6 工具+闸门加固）——均为真机 E2E 发现的结构性修复，符合「按代码耦合适当合并/拆分」精神。

## 33. Blockers

无。

## 34. MODEL_LIMITATIONS

1. **rear/overhead 机位**（§30）：Agnes 图片生成在非对称庭院地点上，声明 rear/overhead 的图实际构图仍偏正面 hero——vision 审查（camera correctness 加固后）正确判 needs_fix；有限尝试未通过，未批准，Director 不会消费假视角。后续可尝试：更多结构性锚点辅助空间定位 / cross-view 参考策略调优 / 模型升级。
2. **写工具空参调用**（§29 首轮）：Agnes 文本模型偶发把 scene.instance.prepare 的 arguments 发成空对象——已加 empty_arguments 结构化错误（指明参数载体+示例），复跑中模型自愈（该问题在 Producer 观察中也曾出现，属文本模型 function-calling 稳定性范畴）。
3. **零工具调用纯叙述**（§32 首轮）：指令含「不要修改…」否定约束时被 _CHAT_TURN_RE 误判聊天轮 → 闸门解除武装 → 模型直接幻觉执行结果。已修（_SCENE_EXPLICIT_ACTION_RE 武装加固）+ 污染会话归档后闭环重跑成功。该 regex 为全 Specialist 共享，Script Writer/CD/Director 同款风险留档（不属本轮改动范围）。

## 35. Known limitations

- scene.inventory 的 stale 概况对每个 approved Sciv 重算 basis 指纹（文档极大时有成本；当前规模无感）；
- Sciv 审查的 `has_pass_review_evidence` 只认 candidate_index=0 的 pass 记录（S2B 既有口径，未改）；
- Production Room 对 Scene Sub-Agent 不展示（按 §26 设计），其活动需进 Director 域查看；
- 前端未为 Scene Agent 加专用 Workbench 视图（Direct Chat + Room 已覆盖 §27 要求项）。

## 36. Commits（10 个）

| Commit | 内容 |
|---|---|
| `473b348` | A：View/Sciv 机位审查加固——camera correctness（§2） |
| `2745393` | B：scene_designer identity + runtime + registry + lock（§0.1/§3/§4/§5） |
| `22b12e6` | C：scene.inventory + scene.production.inspect（§8/§9） |
| `c47ce41` | D：location/anchor/layout 三 typed 写工具（§10） |
| `4a06640` | E：scene.view.prepare + 共享 review retry guard（§11/§12/§17/§18） |
| `e4d05a4` | F：scene.instance.prepare + scene.state.update（§13/§14/§15） |
| `3e3cb4e` | G：scene.state_view.prepare（§16） |
| `8247a7d` | H：Producer 第四 delegate + changed_refs + Production Room/UI（§22-§27） |
| `463401a` | H1：空参写调用守卫 + 冻结断言演进（真机发现） |
| `51f75e6` | H2：§0.6 资源只读工具 + 真相闸门武装加固（真机发现） |

真机 E2E 证据（scratch，不入库）：`_c2_scene_e2e.py` / `_c2_scene_director_e2e.py` / `_c2_scene_e2e_results.json` / `_c2_scene_director_e2e_results.json` / `_c2_s*_log*.txt`。

---

# 最终 18 问（§38）

1. **Scene Agent 是否已经成为独立 Specialist？** 是。第四个正式 Specialist（scene_designer），registry/runtime/dispatch/sessions/stream/inquiry/Production Room/Producer delegate/前端 identity 全链接入，独立 system prompt 与模型档案。
2. **是否与 Director Scene Sub-Agent 完全分离？** 是。Scene Sub-Agent 零改动、不升格；两者不共写 canonical；Room 只显示 Scene Agent 顶层参与。
3. **是否独占 Scene System production canonical 写权？** 是。七个实体全部经 SceneSystemService/SceneVisualService 写入；t26/t47 白名单断言锁定唯一写入者地位。
4. **是否能从 Story Scene 创建/复用 Location？** 是。§29 真机：3 故事段同一地点全程复用 1 个 Location，无重复创建。
5. **是否能管理 SpatialAnchor / SetLayout？** 是。4 锚点 + layout 真机创建；relations/layout 校验零弱化（S27-S29）。
6. **是否能生成、Review、Approve LocationView？** 是。§29 两个 approved views；一次调用=一次生成尝试；approve 走正式 cross-canonical saga。
7. **camera review 是否真正检查机位，而非只检查 anchor？** 是。Work A：observed vs requested 确定性推导 + feature checklist 假视角=反证；§30 真机实证 rear/overhead 被诚实拦下。
8. **是否能创建 SceneInstance？** 是。§29 两实例 + §33 一实例；one-active 校验零弱化、重复绑定被拒并返回现有实例。
9. **是否能正确管理 SceneState？** 是。§31 状态双向变更（晴→暴雨 / 暴雨→无雨）真机成功，anchor/prop 校验生效。
10. **是否能引用真实 Prop placement 而不越权创建 Prop？** 是。§32 闭环：resource_prerequisite → Resource Agent 批准 → 用真实 prop id 摆放；零创建零伪造。
11. **是否能生成、Review、Approve SceneInstanceView？** 是。§29 两个 approved Sciv；§33 Director 绑定的 pinned Sciv approved。
12. **是否理解 ProjectVisualProfile？** 是。prompt 注入摘要 + 工具读取全文；生成走 profile-aware fail-closed 链路；无 mutation 工具。
13. **是否仍无法越权修改 Story/Resources/Shot/Binding？** 是。工具面物理缺失（S48 断言锁定；§33 ②③④ changed_refs 域归属验证）。
14. **Producer 是否能正确委派 Scene Agent？** 是。第四 delegate 真机三经委派（§32/§33），锁域/事件/Task Result 全链生效。
15. **Director 是否能直接消费 Scene Agent 的 approved Sciv？** 是。shot.production.inspect 可见 → bind scene_binding（pinned Sciv）→ readiness READY。
16. **Scene Agent → Director → BOUND generation 是否真机成功？** 是。§33：Producer 单目标 296s 完成 全链，BOUND 首帧真生成，无 legacy fallback。
17. **是否存在 BLOCKER？** 无。
18. **是否可以进入 Phase D — Full Producer Production Workflow？** 可以。四 Specialist（Story/Resource/Scene/Director）全部具备真机验证的生产能力与 prerequisite 路由；建议 D 阶段规格由用户拟定后另行启动。

---

**§38 完成声明**：本轮到此停止。不自动开始 Phase D。
