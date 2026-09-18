# AGENT_SYSTEM_V2_DIRECTOR_PRODUCTION_C1_REPORT.md

**Agent System V2 — Phase C1：Director Production Awareness & Shot Binding 最终报告**

**日期**: 2026-09-18 · **规格**: 详细流程文件/修复阶段/27.md · **分支**: `feat/agent-v2-director-production-c1`
**Phase B 冻结基线**: 87e3847（2093 passed / 0 failed）
**本阶段测试**: 全量 **2142+ passed / 0 failed**（+~50 新用例）· 真机 E2E §20-§24 全部通过

---

## 1. Phase B frozen baseline

Preflight 复现：Phase B 六个测试文件 + Director + Binding S3B1/S3B2 = 193 passed；全量基线 2093 passed。分支 `feat/agent-v2-director-production-c1` 自 87e3847 建立。

## 2. Director unchanged creative responsibilities

零重写：镜头拆分/景别/构图/运镜/action/dialogue/节奏/连续性/Shot canonical/首尾帧/视频生成/视觉审查/Scene Sub-Agent 委托全部原样。`DIRECTOR_SYSTEM` 创作核心段落未动；`test_director_agent.py` 全量通过（C4）。

## 3. new production concepts

Director 系统提示新增【生产检查与绑定】纪律段：Character≠CharacterLook（身份 vs 生产造型）、Story Scene≠Location≠SceneInstance≠SceneInstanceView（四层链，Sciv 才是 BOUND 消费的场景参考）、Binding=生产意图非故事事实、readiness 工作流（inspect→fix→bind→READY→generate）、ownership（缺 Look/场景视图=报告 prerequisite 不自己建；cast 不全=自己修）。

## 4. final tool surface

17 既有 + 4 新增 = **21**（§18 上限内）：`project.visual_profile.read`、`shot.production.inspect`（只读）、`shot.production.bind`/`shot.production.refresh`（写，进 DIRECTOR_WRITE_OPERATIONS=storyboard 锁域）。未加任何 resource.*/scene.*/底层 binding API 平铺工具。

## 5. production.inspect schema

单工具聚合（§6）：shot 信息+内容指纹（`_shot_token`）/ compact VisualProfile / cast 相关 approved Looks（§6.3 只返回本镜 cast，含 look_id/label/refs/usable）/ 道具线索（§6.4 仅 binding 已引用或 `prop_requirement_unknown`，零 NLP 猜测）/ 场景生产上下文（§6.5 `scene_instance_missing`/`scene_view_missing` 显式码 + approved Sciv 清单含 camera_zone/look_direction）/ binding 状态（§6.6 复用 ShotBindingService.read_validity——UNBOUND/READY/NOT_READY+issues+resolved_dependencies，零 readiness 重实现）/ cast_consistency / prerequisite_warnings。

## 6. cast consistency

§7.1 deterministic：项目 canonical 已建立角色名在镜头文本字段（description/dialogue/prompt_en/first_frame_prompt_override/action_sequence/important_details/motion）明确出现、但不在 shot.characters → `missing_from_cast` 结构化 warning。不猜未建立人物（C17）。Director 用既有 shot.update 自修（§7.2，C18）。

## 7. binding ownership decision

§1.1 冻结裁决执行：ShotProductionBinding 是 Shot 的 production companion，生产意图 ownership 归 Director；Resource Agent / Producer / Scene Sub-Agent 零 binding 写路径。经 ShotBindingService 操作，零直写 JSON。

## 8. bind implementation

`shot.production.bind`：UNBOUND→create；已有→replace（必须显式 expected_binding_revision，来自 inspect）；scene_id 由 canonical Shot 推导（防错绑）；payload 直通 service——cast 等集/Look approved/Sciv/profile 校验全复用既有 fail-closed（§8.2 零弱化）；receipt 带 binding_revision/readiness/issues/resolved_dependencies + changed_resources=[{kind:"binding", id:shot_id}]（§8.3）。

## 9. refresh semantics

`shot.production.refresh`：显式快照刷新（§9）。依赖 invalid（审批撤销/未批准/profile unspecified）→ fail closed 返回真实原因（真机 §23 补充轮+mock C33/C35/C36）；stale-but-valid（fingerprint 落后）→ refresh 后 READY（真机 §23 主轮：外部改 Look→NOT_READY→refresh→READY→生成成功）。**注意**：27.md §9 写「Sciv stale → refresh fail」，实际复用的 S3B.2 service 语义是「SIV stale-but-valid 可 refresh，validity 诚实重报 stale_dependency，Scene 侧修复+再 refresh 才 READY」——按 §6.6「复用现有语义不弱化」执行，见 deviations。

## 10. VisualProfile awareness

Director `project.visual_profile.read`（与 Resource Agent 同 schema，共享 `production_context.read_visual_profile_projection`）+ system 一行摘要（render_family/方向/标签/负面约束）。只读；生产推理以 profile 为真相；legacy StyleAnchor 系统保留未删（§5）。

## 11. reference_policy

`shot.generate_images` 增可选 enum `legacy_allowed|bound_required`（默认=旧行为，§11.1 旧项目兼容）。bound_required：UNBOUND → 提交前拒绝 `binding_required`（零额度消耗）；NOT_READY → `binding_not_ready` + 结构化 issues；READY → 放行 BOUND（§11.3）。prompt 纪律（§11.4）：production-ready/用批准资源/保持一致/正式生成 → 必须 bound_required；明确允许草稿才 legacy。

## 12. legacy compatibility

UNBOUND+默认 → legacy 行为逐字节保留（C37 端到端：job 照常提交）；未传 reference_policy 的旧调用方行为不变（C42）；`test_director_agent.py` 全量通过。

## 13. Resource prerequisite behavior

缺 approved Look → inspect 的 prerequisite_warnings（code=resource_prerequisite, required_capability=resource_agent）→ turn 级聚合 → Producer TaskResult.warnings（§15「真实 tool result 派生，不信模型自述」）→ 真机 §21：Director inspect 后**零生成、零 legacy 回落、零创建资源**，如实报告 Bob 缺 approved Look（该消息曾被 zero-write 闸门误覆写——已修复，见 §16/commit b2d69c7）。

## 14. Scene prerequisite behavior

缺 SceneInstance/approved Sciv → `scene_production_prerequisite` warning → Producer 按 §16 如实报告 blocker，不路由给 Resource Agent（C53 prompt 断言 + 真机 prerequisite 上报）。SCENE_PREPARATION_REQUIRED 留给 Phase C2（§25）。

## 15. changed_refs.bindings

九键（八键+bindings）；binding id=shot_id（canonical 键，§10）。派生经 changed_resources kind=binding（bind/refresh receipt）——**修复了一个派生 bug**：Phase B map 加 "character"→characters 后，`**` 解包覆盖 legacy characters 键（A1/A5/A8/B 回归捕获，改为合并）。`_wrap_task_result` 并入 turn 级 prerequisite_warnings。旧 TaskResult 兼容（C51）。

## 16. Resource review retry guard

Work A（27.md §3.1）：per-turn 内存态 `(project, message_id, resource_key, basis_sig)` → 连续 needs_fix 计数；≥3 后同 basis 调用返回 `review_retry_exhausted`（零生成零审查）。basis=语义输入指纹（appearance/clothing 或参考图来源或 bindings+描述）——改输入=新 basis；pass 清零（C58）；新 turn 隔离（C56）；**无 canonical 失败计数器**（C57）。Phase B Case A 的 6 连烧图被结构性收口。

## 17. receipts / truth discipline

零重写 Runtime：inspect=只读（不进 receipts、不占写槽）；bind/refresh=写（单写串行+storyboard 锁+finally 释放）。**真机 hardening ×2**：①zero-write 闸门误覆写「前置阻断如实报告」→ prerequisite_blocked 豁免（commit b2d69c7）；②模型零工具调用幻觉声称「刷新/生成成功 job_id xxx」穿透 claims 正则 → 生产域措辞+✅/job_id 形态入正则+span 内否定豁免（commit 927fd43，四态验证）。

## 18. mock tests

`tests/test_c1_review_retry_guard.py`（C54-C58+prop guard）+ `tests/test_c1_director_production.py`（C1-C53 主体，复用 S3B 真实跨域夹具）。全量 2142+ passed 0 failed（含 Director/Binding S3B/S3C/Producer/Room/Scene System 回归）。

## 19. Director positive real E2E（§20）

`PhaseC1_Director_E2E`（2d_animation、场景链真实生成/批准、Alice+Bob approved Looks、无预建 binding）。自然语言任务（27.md 原文，无工具顺序）→ Director 自主：story/character/shot reads → profile.read → **inspect×6 → shot.update（cast/字段修正）→ bind×6（3 镜）→ 3 binding 全 READY → generate_images（bound_required）→ 真实 Agnes BOUND 生成 3/3 首帧成功**。零 Resource/Scene 写入。首轮实证并修复：setup 假 PNG 被 Agnes 400 拒收（换真实 PNG）。

## 20. missing-resource E2E（§21）

Bob 无 approved Look → Director：profile.read → inspect（resource_prerequisite）→ **零生成零回落零创建资源**，如实报告「Bob 缺 approved Look，需要 resource_agent 能力」（修复闸门覆写后消息完整保留）。

## 21. cast-mismatch E2E（§22）

description 提到 Bob 但 cast=[Alice] → inspect 发现 missing_from_cast → **Director shot.update 自修 [Alice,Bob]** → bind 两角色 → BOUND 生成成功。Golden Run s06 克隆缺陷在 Agent 层被系统性堵住。

## 22. stale-binding E2E（§23）

三态闭环：①外部改 Look（fingerprint 落后，stale-but-valid）→ NOT_READY → refresh → READY → 生成成功；②撤销审批（invalid）→ refresh fail closed（mock C33/C36+真机补充轮 Director 如实停止）；③真机幻觉轮：模型零工具调用声称成功 → **真话闸门补盲后已堵**（commit 927fd43）。

## 23. Producer→Resource Agent→Director E2E（§24）

`PhaseC1_Joint_E2E`（Bob/陈叔缺 Look、无 binding）。两轮 Producer 对话后全链完成：
- Producer→RA：创建 林晚/陈叔 characters+views → look.prepare 真机生成→审查→**approved**（3 approved Looks 落盘）
- Producer→SW：故事段 characters 数据修正（合法域）
- Producer→Director：inspect×2 → bind（一次 location_view_missing 失败后重试成功）→ **binding READY → BOUND 首帧生成落盘**（后端日志实证 `build_bound_first_frame_input` 执行）
- Producer 全程零 direct writes；changed_refs：RA=characters/looks、SW=scenes(叙事字段)、Director=shots+bindings；prerequisite warnings 进 TaskResult；AgentTask parent chain + bridge_messages 成立。

## 24. regression

全量 **2142 passed / 0 failed**。过程中更新 2 个冻结断言（T26/T47：scene_system 引用边界从「零引用」演进为「只读经 production_context」，27.md §6.5 授权）+ changed_refs 九键骨架断言 ×8。

## 25. deviations

1. **§9 vs service 语义**：SIV stale-but-valid → refresh 成功（spec 写 refresh fail）——按 §6.6「复用 ShotBindingService 既有语义」执行；refresh 后 validity 诚实重报 stale_dependency。invalid 依赖（撤销/未批准/profile 缺失）refresh 确实 fail（C33/C35/C36）。
2. **Commits B-F 合并**（9bdee9f）：代码结构上 production_context/director_production/director 三文件交织，按 27.md §28 精神合并提交，测试同行。
3. **§24 花费两轮 Producer 对话**：首轮 RA 只到 views 未到 approved Look、Director 末段 legacy 生成；第二轮在 Producer 持有上下文后全链闭合。第一轮的 prerequisite warnings 与 failed bind 回执全部如实入档（这正是多轮协调的真实形态）。
4. **setup 数据**：E2E 的 approved Looks/场景链由 setup 脚本经真实 service/fake-provider 产出（27.md §20 授权）；E2E 首轮发现 fake PNG 被 Agnes 拒收 → 改真实 PNG。
5. shot.generate_videos 未加 reference_policy（§11.2「必要时」——视频消费已选首帧，首帧侧门槛已闭环）。

## 26. blockers

**无 BLOCKER。**

## 27. known limitations

1. reference_policy 依赖模型纪律选择（prompt 约束）；系统层只在 bound_required 时拦截，legacy_allowed 恒放行。2. inspect 的 cast 检查只匹配 canonical 名字的确定性出现（近似称呼不报——设计如此）。3. Producer 跨 RA/SW/Director 的长协调仍需多轮用户推动（§16 最小更新，Phase D 全流程协调议题）。4. refresh 语义与 spec 文字的 Sciv 分歧（deviation 1）。5. claims 正则仍是措辞驱动的启发式（已补生产域盲区，无法穷尽）。

## 28. commits

| Commit | 内容 |
|---|---|
| adcbd2f | **A** per-resource review failure guard（C54-C58） |
| 9bdee9f | **B-F** 共享只读层 + profile 感知 + inspect/cast + bind/refresh + changed_refs.bindings + reference_policy + 结构化回执 + prompt/Producer 最小更新 |
| 59ea808 | 冻结断言演进（T26/T47）+ E2E 驱动 |
| b2d69c7 | prerequisite 贯通 turn→TaskResult + 闸门豁免（§21 真机发现） |
| 927fd43 | 真话闸门补盲区——生产域幻觉措辞（§23 真机发现） |
| （本提交） | **G/H** 报告 + E2E 结果 |

---

## 最终问题逐条回答（27.md §29）

**1. Director 是否仍然是 Creative Shot owner？** 是——创作核心零改动，test_director_agent 全量通过；新增的是生产感知层。
**2. Director 是否真正理解 ProjectVisualProfile？** 是——只读工具（同 schema 共享实现）+ system 摘要 + prompt 纪律；无 mutation 工具（C2）。
**3. Director 是否能在生成前判断 Shot production readiness？** 是——inspect 聚合 readiness/issues/cast/资源/场景（复用 service validity），真机 §20 inspect×6 先于生成。
**4. Golden Run 的 cast omission 是否已被系统性拦截？** 是——deterministic cast_consistency + §22 真机自修闭环（发现→shot.update→bind 双角色→生成）。
**5. Director 是否能选择 approved Look？** 是——inspect 只列 cast 相关 approved Looks，bind 消费（§24 真机绑定的 look 进入 pack）。
**6. Director 是否能选择 approved SceneInstanceView？** 是——inspect 列 approved Sciv（camera_zone/direction），bind 消费（真机 §20/§24 绑定含 pinned sciv）。
**7. Director 是否能创建/替换/refresh ShotProductionBinding？** 是——bind（create/replace+OCC）、refresh（stale-but-valid→READY；invalid→fail closed）。
**8. bound_required 是否杜绝正式生产中的 silent legacy fallback？** 是——UNBOUND/NOT_READY 提交前拦截（C38/C39 + 真机 §21 零回落）；READY 才放行（C40 + §20 3/3）。
**9. Director 是否仍然无法越权创建 Resource？** 是——工具面物理缺失（C43）+ 真机零 Resource 写入。
**10. Director 是否仍然无法越权修改 Scene System？** 是——同上（C45 + 真机零 Scene 写入；只读经 production_context）。
**11. Producer 是否能完成 Resource Agent → Director 的真实协调？** 是——§24 两轮对话完成 RA 备 Look→SW 修正→Director bind READY→BOUND 生成；Producer 零直接写入；prerequisite 经 warnings 路由。
**12. 是否存在 BLOCKER？** 无。真机发现的三处问题（setup 假图、闸门覆写如实报告、生产域幻觉措辞）均已修复并有回归锚定。
**13. 下一阶段？** 当前 scene preparation 以 setup 预置即可支撑 Director 侧闭环，但 **Scene preparation 仍无 Agent 写路径**（§25 SCENE_PREPARATION_REQUIRED）——建议进入 **Phase C2 — Scene Production Preparation**（把 Location→View→Instance→State→Sciv 的准备职能 Agent 化），完成后再进 Phase D 全流程协调。

**§29 STOP**：本阶段收口，不自行进入 C2 或 D。

**报告位置**：`animation-storyboard/AGENT_SYSTEM_V2_DIRECTOR_PRODUCTION_C1_REPORT.md` · 分支 `feat/agent-v2-director-production-c1` · 真机证据 `_phasec1_e2e_results.json` + 各 E2E 项目 `output/` · 测试全量 2142 passed
