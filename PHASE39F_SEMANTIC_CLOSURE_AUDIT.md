# PHASE39F — Slot / Input Provenance / Skill Semantic Closure 全面诊断报告

对应任务：`详细流程文件/修复阶段/41.md`（Phase 39F）。
诊断日期：2026-09-26。执行：ZCode（Phase 39F 诊断轮）。

---

## 0. 总结论（TL;DR）

**六者闭环（系统事实层 / UI 槽位 / 历史 canonical / 下一次 generation dispatch / Skill 决策 / trace）总体成立，rerun 无语义污染**：

1. **首帧槽同步（39E `_sync_first_frame_slot`）是纯显示事实同步**——不改 `link_mode`/`opening_source`/`video_reference_asset_id`，下一次 dispatch 的 mode 由 `shot.link_mode` 决定、参考由 Binding/relay 源重新解析，槽内图片**不能**让 multi 偷偷变 i2v、也**不能**自动成为 hard frame 0（Case A/B/C 全部 PASS，provider 请求级截获验证）。
2. **发现并修复 1 个 CONFIRMED BUG（生产全域污染）**：`Shot.from_dict` 的 legacy 迁移把「键存在但为空」的 `video_versions` 也当 legacy 文档迁移——视频在途保存（submit 落 video_id、versions 尚空）后的任何 reload 都会把在途 video 伪造成 `status=generating` 的**幽灵 `legacy_video` 版本**坐进版本史 index 0（input_visuals=[]、永不更新）。P40A/P39C/P39D/Fable×2/Golden×2/AGNES验收 等 8 个生产项目全部中招。同一窗口还使 `final_video=None` 的显式清除被推断复活（freshness 读模型谎报 current）。两处均改为「键缺失才迁移/推断」。
3. **`input_visuals` = provider 最终 dispatch 事实**：7 种 dispatch 形态（multi 参考直生×2 / multi relay / i2v 硬首帧 / i2v inherit 接力 / keyframes 双边界 / legacy unbound i2v / recover）在 provider 最后一层截获对账，URL/槽位/顺序/semantic_role 逐项一致；provenance 不完整（空）时推导为 None，不产生假 mode mismatch。
4. **relay freshness 诚实**：A 更新到 V2 后，B 的历史 VideoVersion provenance immutable、freshness 读模型如实 `stale`、i2v inherit 链 `continuity_status=stale`；重生成 B 才记录 V2。
5. **插曲制系统基座完整**（1 个/2 个插曲、正式镜身份、bind/relay/trace/级联、inherit 接力链插入保护 `dependency_block`）。
6. **最小修复**：`pregen_check_unavailable` 非阻断诊断（fail-open 不再静默）；Skill 文本 4 处定版（组软语义 / trace 收尾自检 / seam 唯一权威口径 / bridge 负面约束）；首帧大图本体最小语义角标（琥珀/蓝/绿）+ provenance strip 头 + 「人物参考」标签。
7. **P40A fixture**：s03 SIV 已按正式流程重生成（真实 API）——新空板候选通过 8 维 Vision 审查（含鹤候选被正确判 uncertain），approve saga 完成资产换绑（`ref_e9732a7fe99d`，verified=true）。s02_c01/s03_c01 旧 i2v 配置按任务要求**保留原状**作为下轮 Agent Canary 的 Skill Canary 测试点。
8. **回归**：本轮新增 24 项测试全绿；全量与 baseline 对比：39 个 av_sync 旧失败逐条一致 + 1 个满载 flaky（cancel_race，隔离 3/3 通过）+ 1 个 skill 锚点字符串碰撞（语义保留、已对齐，修复后全绿）——无真实新增失败。

---

## A. Verdict Matrix

| # | 问题 | 代码事实 | 测试 | 结果 | 分类 | 是否修改 | commit |
|---|---|---|---|---|---|---|---|
| 1 | 39E 首帧槽同步是否污染下一次生成（Case A multi/reference-only rerun） | sync 只写 first_frame 批次/指针（shots.py `_sync_first_frame_slot`）；mode 由 `shot.link_mode` 决定；multi 参考每次从 Binding/relay 源重解析（video_adapter.py `build_bound_video_input`） | `test_case_a1_character_look_reference_rerun_keeps_multi`、`test_case_a2_scene_reference_synced_to_slot_rerun_keeps_multi`（第二次 provider 请求截获） | **PASS**——rerun 仍 multi、无 `image_url` 硬槽、参考 URL 不变、历史 immutable | PASS | 否 | — |
| 2 | Case B relay boundary rerun | multi+`prev_tail` 的开场=影片顺序上一镜真实 `video_end_frame` 重解析；不消费槽 | `test_case_b_multi_relay_rerun_keeps_relay_semantics` | **PASS**——`image_urls==[上镜尾帧]`、semantic_role 仍 relay_start_boundary、未生成新分镜首帧 | PASS | 否 | — |
| 3 | Case C reference-only 进槽后 rerun 变 hard boundary？ | i2v 无 sync 入口（hard_start 早退）；multi 不消费槽；mode 切换后同步图无 provenance → fail closed `artifact_basis_missing` | `test_case_c_synced_reference_never_becomes_hard_frame_on_mode_switch`（零 provider 调用）、`test_sync_slot_does_not_touch_semantics_config` | **PASS**——display state 无法借道变成 generation boundary | PASS | 否 | — |
| 4 | `input_visuals` 是否 = provider 最终 dispatch | handler 从 `compile_agnes_video_call` 槽位构造 kwargs；`_build_input_visuals` 以 adapter semantic_roles 为权威合并同源 URL | `test_p2_*` 7 形态对账（planned ↔ payload ↔ persisted 三方） | **PASS**——URL/槽位/顺序/semantic_role 逐项一致 | PASS | 否 | — |
| 5 | **视频在途 reload 伪造幽灵 `legacy_video` 版本** | `Shot.from_dict`：`vv_raw` 空即走 `_legacy_video_version(d["video"])`——submit 落库（versions=[] + generating + video_id）后的 reload 伪造幽灵版本坐进 index 0（P40A 实证：`legacy_video` status=generating、input_visuals=[]） | `test_p2_recover_path_honest_provenance_and_no_phantom_version`、P1-P3 全部版本序断言 | **CONFIRMED BUG**（生产全域：8 项目 project.json 均含幽灵条目） | CONFIRMED BUG | **是（最小）**：legacy 迁移仅对「无 video_versions 键」的真 legacy 文档 | `5d50840` |
| 6 | `final_video=None` 显式清除被推断复活 | 同窗口：`d.get("final_video") is None` → 推断回 `versions[0]`；freshness 读模型对「无 final」镜谎报 current（`test_freshness_missing_without_final` 回归实证——旧断言靠幽灵版本撞对） | 该既有测试在修复后恢复本义通过 | **CONFIRMED BUG**（同族） | CONFIRMED BUG | **是（最小）**：推断仅对「无 final_video 键」 | `5d50840` |
| 7 | relay source_version freshness（P3） | multi relay：relay 锚进 Native AV visual 指纹（end_source_version）；i2v inherit：`continuity_status` source_version 比对 | `test_p3_relay_freshness_and_historical_immutability`、`test_p3_i2v_inherit_relay_stale_verdict` | **PASS**——历史 provenance immutable、stale 如实、重生成才记 V2 | PASS | 否 | — |
| 8 | 槽位 UI 与 provenance UI 语义（§4） | 39E 已有版本条 provenance strip（琥珀/蓝/绿）；**首帧大图本体无语义角标**（DESIGN GAP）；CharacterLook 条目标签「参考」易误读 | 浏览器实测 + 截图（_p39f_ui_evidence/ 5 张） | DESIGN GAP | DESIGN GAP | **是（最小）**：首帧大图角标（参考=琥珀/接力=蓝/硬=绿，只依事实显示）+ strip 头「实际输入」+「人物参考」标签 | `c634e8d` |
| 9 | Skill 组定义「段即组/段=组」绝对措辞（§5） | full-story-relay-calibration SKILL.md / references/director.md / references/producer.md 三处 | `test_p5_group_definition_is_soft_semantics`（锚点） | DESIGN GAP | DESIGN GAP | **是**：改为软语义四情形矩阵（同段+接力/同段+cut/跨段+接力/跨段+新参考），系统层零改动 | `c6140d5` |
| 10 | trace 完全缺失盲区（§6） | 视频完成无任何系统级 trace 检查（routers/shots.py 0 处 trace）；adjudication 警告只在 Agent 主动 trace_upsert 时出现 | 设计约束=不加全局 hook；Skill-local 收尾自检条款 + 锚点 `test_p6_skill_local_trace_closure`；普通路径零噪音（既有 p39e 测试 + 本轮 `test_p10_*` 正常路径断言） | 按任务口径补 Skill-local 收尾自检 | DESIGN GAP（按 §6 优先 Skill-local 处理） | **是（Skill 文本）**：本镜收尾前 trace_read 复核、缺失即补裁档；Producer 汇报必须读 trace 事实 | `c6140d5` |
| 11 | seam 口径唯一权威（§7） | 测量本体 continuity_service.py（gray128_bicubic_v1_phase38）；Skill 未明文禁止自算 | 锚点 `test_p7_seam_single_authority` | DESIGN GAP（文本层） | DESIGN GAP | **是（Skill 文本）**：唯一权威口径=calibration.seam_measure；禁止自写 ffmpeg/自实现 MAE/另一套脚本裁定；人工复测不得写成 authoritative；MAE 是证据不直接定档 | `c6140d5` |
| 12 | 插曲制结构完整性（§8） | 系统无 interlude 实体；插入走 structure_service（id 不回收、index 重排、依赖保护） | `test_p39f_interlude_structure.py` 3 项（1 插曲全链 / 2 连续插曲 / inherit 插入保护） | **PASS**——原文不改写、正式镜 bind/relay/trace 正常、影片顺序接力自动穿过插曲、旧版本 immutable+stale、dependency_block 守卫 | PASS | 否（测试随 `5d50840`） |
| 13 | Bridge 负面约束（§9） | skill.json applicability 已有「且需要连续过渡」限定；正文缺显式负面清单 | 锚点 `test_p9_bridge_negative_constraints` | DESIGN GAP（文本层） | DESIGN GAP | **是（Skill 文本）**：Bridge 前提=双端必须保留+业务必须连续；intentional cut/new group/deliberate location jump/montage cut 默认不插桥 | `c6140d5` |
| 14 | pregen fail-open 完全静默（§10） | `director_visual.py`：`except Exception: sem=None` → 放行且零诊断，静默冒充「无冲突」 | `test_p10_pregen_check_unavailable_diagnostic`（异常路径带诊断+正常路径零噪音） | DESIGN GAP | DESIGN GAP | **是（最小）**：`pregen_check_unavailable` 非阻断诊断（回执字段+分项标记+note），语义=「本轮未完成确定性预检」≠「已证明无冲突」；hard_start 冲突阻断与 reference_only soft 口径不变 | `5d50840` |
| 15 | mode_mismatch 对不完整 provenance 的行为（§2 尾） | `_derive_mode_from_input_visuals([])`→None；warning 仅在 `claimed and actual and !=` 时发 | `test_mode_mismatch_not_fired_on_incomplete_provenance` | **PASS** | PASS | 否 | — |
| 16 | P40A s03 SIV 空板纪律（§11a） | sciv_96882e7dc2f4 旧参考含站立鹤（problems_log Step1 记录「大」） | 正式流程重生成（真实 API gen_2e592259eea3）+ 8 维 Vision 审查 + 亲自目检 + approve | **已修复**（新资产 ref_e9732a7fe99d verified；审查器本轮正确拦截含鹤候选） | fixture defect（已修复） | 是（fixture 数据，projects/ 不入 git） | — |
| 17 | P40A s02_c01/s03_c01 旧 i2v 配置（§11b） | 组首镜 i2v + 残留 vref=prev_tail 死配置（i2v 不消费） | 按任务要求**不人工修复**——保留作下轮新对话 Agent Canary 的 Skill Canary 测试点 | 待 Canary（下轮） | EXPECTED（保留观察） | 否 | — |

---

## B. Provider Payload Evidence（final provider request ↔ persisted input_visuals）

截获层：`FakeMediaClient.create_video`（provider 最后一层；记录 mode/image_url/image_urls 全部实参）。
对账函数：`_assert_multi_reference_dispatch` / `_assert_hard_boundary_dispatch` / `_assert_planned_matches_dispatch`
（tests/test_p39f_semantic_closure.py——同时比对 adapter planned → payload → persisted 三方）。

| 模式 | provider 实收（截获） | persisted input_visuals | 一致性 |
|---|---|---|---|
| multi / reference-only（character_look） | `mode=multi, image_urls=[http://example/alice.png], image_url=None` | `[{slot:reference, semantic_role:reference_only, url:同, source_type:character_look}]` | ✅ |
| multi / reference-only（scene_instance_view） | `mode=multi, image_urls=[http://example/sciv.png]` | `[{slot:reference, semantic_role:reference_only, url:同, source_type:scene_instance_view}]` | ✅ |
| multi relay（prev_tail） | `mode=multi, image_urls=[http://example/s01_tail_v1.png]` | `[{slot:first, semantic_role:relay_start_boundary, url:同, source_shot_id:s01_c02, end_source_version:vv_a1}]` | ✅ |
| i2v hard_start（BOUND） | `mode=i2v, image_url=http://example/ff.png` | `[{slot:first, semantic_role:hard_start_boundary, url:同, candidate_id/batch_id 同 adapter}]` | ✅ |
| i2v inherit relay（硬接力） | `mode=i2v, image_url=http://example/s01_tail_v1.png` | `[{slot:first, semantic_role:hard_start_boundary, url:同}]`（槽位同步不发生——槽内已是实际输入） | ✅ |
| keyframes 硬首+硬尾（BOUND） | `mode=keyframes, image_urls=[ff, lf]`（顺序一致） | `[{slot:first,hard_start_boundary},{slot:last,hard_end_boundary}]` | ✅ |
| legacy unbound i2v | `mode=i2v, image_url=http://example/ff.png` | `[{slot:first, semantic_role:hard_start_boundary, url:同}]`（best-effort 标注实际发送 URL） | ✅ |
| recover/续轮询 | 不重提交（provider 调用数不变） | 旧版本原样；recover 新版本诚实空缺省（models.py 文档化「[] = 旧数据/未记录」） | ✅（无伪造） |

adapter 之后的链路无过滤/drop/URL 替换/顺序改变（编译层对非法输入是**结构化拒绝**而非静默丢弃——`multi_reference_slot_invalid` 等，既有 34.md 测试覆盖）。

---

## C. Rerun Pollution Evidence（不能含糊的回答）

> **slot sync 后重新生成，是否会改变 generation mode / hard boundary semantics？——不会。**

证据链（全部 provider 请求级截获，非 UI/JSON 推断）：

1. **mode 不变**：`_sync_first_frame_slot` 写面 = `first_frame_batches` + `selected_first_frame` + 旧 `first_frame` 字段（`test_sync_slot_does_not_touch_semantics_config`：link_mode/opening_source/vref 三字段 rerun 前后逐项相等）。
2. **Case A**（multi 参考已同步进槽 `[参考]`）→ 不清槽 rerun：第二次请求 `mode=multi, image_urls=[场景参考 URL], image_url=None`——**槽中存在图片没有让 mode 变 i2v，也没有成为 hard frame 0**。
3. **Case B**（multi relay 已同步 `[接力边界]`）→ rerun：第二次请求 `image_urls==[上镜真实尾帧]`（由 relay 语义重解析，非读槽），`semantic_role` 仍 `relay_start_boundary`；首帧批次数不变（未生成新分镜首帧覆盖 relay）。
4. **Case C 收尾**（最坏情形：slot 已有参考图后 mode 被显式切到 i2v）：同步图无生成 provenance → `build_bound_video_input` fail closed（`artifact_basis_missing`），**零 provider 调用**——display state 在任何路径下都无法借道变成 generation boundary。
5. 人物图（character_look）根本不进首帧槽（39E 纪律，`test_case_a1` 断言槽未被写入）。

---

## D. Relay Freshness Evidence

| 步骤 | 事实 | 断言 |
|---|---|---|
| A 生成 V1 | A.final_video=vv_a1, end=TAIL1 | — |
| B relay V1 生成 | B 的 VideoVersion `input_visuals[0].end_source_version=vv_a1` | `test_p3_...` 前半 |
| A 重生成 V2 并 selected | A.final_video=vv_a2, end=TAIL2 | — |
| 不重生成 B | B 历史 VideoVersion input_visuals **逐字节不变**（仍 vv_a1/TAIL1）；`native_av_freshness(B)=stale`，mismatched_keys 含 `visual_basis_fingerprint`；UI 不伪装（final 仍指 B1） | freshness 读模型只读不写（§26 No Auto Regeneration） |
| 重生成 B | 新 VideoVersion 记 `end_source_version=vv_a2, url=TAIL2`；第二次 provider 请求 `image_urls==[TAIL2]`；**V1 版本原样保留** | 历史版本 immutable provenance |
| i2v inherit 链 | `continuity_status(B)`：更新前 `ready` → 更新后 `stale`（「上一镜头已更新」） | `test_p3_i2v_inherit_relay_stale_verdict` |

---

## E. UI Evidence（截图在 `_p39f_ui_evidence/`，全部肉眼可辨）

| 语义 | 大槽位角标 | provenance strip（版本条） | 截图 |
|---|---|---|---|
| hard start（i2v） | **绿色**角标「首帧」（右上） | 绿框条目 + tag「首帧」+ title「硬首帧边界」 | s01_c01_hard_start_green_badge.png / s01_c01_strip_hard.png |
| relay（multi prev_tail） | **蓝色**角标「接力」 | 蓝框条目 + tag「首·接力」+ title「接力边界（上一镜真实尾帧），来源 s01_c01」 | s01_c02_relay_blue_badge.png / s01_c02_strip_relay_blue.png |
| reference-only（character_look） | **琥珀**角标「参考」 | 琥珀框条目 + tag「**人物参考**」+ title「人物参考（provenance——非首帧槽）」 | s02_c02_character_ref_amber.png |
| provenance 头 | — | strip 头「**实际输入**」+ 悬浮说明「这是 provenance（该版本实际消费的全部输入事实），不是首帧槽」 | 同上 |

- 角标**只依事实显示**（inherit→接力；最终版本 input_visuals 与选中首帧候选 URL 匹配→该角色；无事实不显示），不发明状态。
- CharacterLook 缩略图在「实际输入」provenance 区内、以「人物参考」标签明确非首帧槽（§4 要求）。
- 验证方式：vite dev + 真实浏览器（P40A 复制夹具构造琥珀实例，验证后已删除）。

---

## F. Skill-only Evidence

| 项 | 证据 |
|---|---|
| 普通操作不加载 Skill → 零警告污染 | ① 三条 trace 警告只作为 `trace_upsert` 回执送达，而 trace_upsert 是校准 skill 专属收尾动作（director_calibration.py:214-217 设计注释 + best-effort `except: hints={}`）；② 既有测试：未声称 mode 不触发、intentional_cut/reference_only_note/组首镜/接力镜/上镜无视频全豁免（test_p39e_trace_hints）；③ 本轮 `test_p10_...` 正常路径零噪音断言。未使用 skill 的普通 Director 操作不会调 trace_upsert → 零警告、零干扰。 |
| 加载 Skill + 正常流程 → 自动完成 trace | Skill 文本已定版**收尾自检**（SKILL.md 完成标准 + references/director.md §7：本镜收尾前 trace_read 复核，裁定字段缺失/trace 完全不存在 → 立即补做）。锚点测试 `test_p6_skill_local_trace_closure`。**行为验证留给下轮新对话 Agent Canary**（本任务 §12 收尾明确：本轮 STOP，不跑完整故事）。 |
| 加载 Skill + intentional cut → 不误催 relay | 既有豁免测试（relay_intent_hint 对 intentional_cut/reference_only_note 豁免）+ 本轮组软语义措辞消除了「段内必须接力」的错误压力。 |
| 尾态 major → interlude | 决策模型 b) 路径（SKILL.md）；系统基座本轮实测（P8：插曲正式镜身份/接力/trace/级联/插入保护全 PASS）。实跑留给 Canary。 |
| 镜内失败 major → single anchored retry | 决策模型 a) 路径（SKILL.md「【单次】锚定重试」）；插曲测试同时验证「不偷偷把尾态偏离改判成镜内失败重试 A」的结构面（插入成功且 A 的历史版本未动）。 |

---

## G. Regression

- **本轮新增**：tests/test_p39f_semantic_closure.py（21 项）+ tests/test_p39f_interlude_structure.py（3 项）= **24 项全绿**；test_skill_content_canary_lessons.py 组约定锚点随软语义措辞同步更新（语义保留：「不存 group canonical、不强制」）。
- **Targeted**（binding/video/multi/native-av/pregen/skill 锚点/agnes/director_visual 等 13 个文件）：修复后 293 passed, 0 failed；skill 锚定 4 套件 49 passed。
- **全量**（`_p39f_final_regression.log`）：`41 failed, 2446 passed, EXIT=1`，与 baseline（`_p39f_regression2.log`：39 failed / 2425 passed）逐条对比：
  - **39 个 av_sync 旧失败逐条一致**（test_av_sync_a2_pack_freshness ×16 / test_av_sync_a2_adapter ×12 / test_av_sync_a2_readiness_timing ×11）——与本轮改动无关（改动面：core/models.py from_dict、director_visual.py 预检诊断、Skill 文本、Workbench.vue、新测试文件，均有 targeted 覆盖且全绿）；
  - **+1 flaky**：test_ai_7ch_cancel_race.py::test_case1_stale_object_resurrection——满载下偶发（隔离复跑 3/3 通过，1-2s vs 套件内 34s；barrier 竞态测试；本轮改动不触及 change-protocol 路径）；
  - **+1 锚点碰撞**：test_skill_content_canary_lessons.py::test_group_convention_light——「段=组→软语义」措辞更新后旧锚点字符串失配（**语义未降级**：组「约定、不强制」仍被断言，锚点已对齐新措辞，修复后全绿）；
  - 无其他新增失败，无旧失败被误算为本轮修复。
- 历史遗留：根目录 `_37_fable/_39c_canary_driver.py` 存在非本轮工作区改动，未纳入本轮提交。

---

## H. 修复纪律自查（§13）

- 未新增全局 Group canonical、未强制 relay/multi、未禁 i2v、未自动重生成/refresh binding/插 bridge/改 Story；
- 未把 Skill 规则复制进 Runtime（pregen 诊断只是「预检自身不可用」的事实上报，不是业务规则）；未把 Runtime 规则复制进 Skill；
- 未为 UI 制造第二份 production truth（角标只读最终版本 input_visuals 事实）；
- from_dict 两处修复是最小窗口修正（键缺失语义），未加新状态机。

## 全量回归

```
41 failed, 2446 passed in 1634.35s（_p39f_final_regression.log）
= baseline av_sync ×39（逐条一致）
+ 1 flaky（cancel_race，隔离复跑 3/3 通过）
+ 1 skill 锚点字符串碰撞（已按语义保留原则对齐新措辞，修复后全绿）
EXIT=1（与 baseline 一致）
```

## 提交清单

| commit | 内容 |
|---|---|
| `c6140d5` | Skill 定版（组软语义/收尾自检/seam 唯一口径/bridge 负面约束 + canary 锚点同步） |
| `5d50840` | core/models.py 幽灵版本/推断复活修复 + director_visual.py pregen 诊断 + 语义闭环/插曲结构测试 24 项 |
| `c634e8d` | Workbench 首帧大图语义角标 + provenance 头/人物参考标签 + UI 证据截图 |

## 下一步（按 41.md 收尾指令）

本轮 STOP。不跑完整故事、不进 Phase E。语义闭环无污染已证明 → 下一步为**新对话 Agent Canary**（重点：s02_c01/s03_c01 组首镜重配、插曲制实跑、Skill 收尾自检实跑）。
