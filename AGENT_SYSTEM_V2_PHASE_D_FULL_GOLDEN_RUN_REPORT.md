# AGENT SYSTEM V2 — PHASE D FINAL REPORT
# Full Producer Golden Production Run（29.md）
# 《山雨》· Golden_Full_Production_V1 · 2026-09-19

---

## 0. 结论速览

- **Persistent Golden Project `Golden_Full_Production_V1` 建成并保留**（§43）：9 故事段 / 4 角色 / 7 approved Looks / 4 approved Props / 2 地点 12 锚点 / 52 production-grade Shots / **52/52 BOUND 2K 首帧** / 10 尾帧 / 6 镜多候选+选定 / 再生 before-after 案例 ≥6。
- **Producer 全程自主编排**（25 轮真实委派，零手工 specialist 直调、零手工 canonical 写入）：故事创作→资源→场景→分镜→出图→质量收口全链路由 Producer 协调四个 Specialist 完成。
- **本轮发现并修复 10 个系统性问题**（Commit A-J + B2/B3/B4），其中 4 个是「真机才暴露」的生产级缺口（i2i 输入图上限 6 / 多参考克隆人 / 空机位 view 审查退化 / Producer 幻觉汇报）。
- **无 BLOCKER**。项目可直接留给 Assembly / Compositor Agent（§43/§44）。
- 最终回归：**见 §31**（本轮新增 12 项测试，全量基线 2208 → 2220+）。

---

## 1. Frozen baseline
C2 FINAL（28.md）：分支 feat/agent-v2-scene-agent-c2 @ 6e0e63e，2208 passed / 0 failed，无 BLOCKER。本轮独立分支 `feat/agent-v2-phase-d-golden-run`。

## 2. Golden project title / path
《山雨》（东方悬疑·山雨驿站·旅途短片）；`projects/Golden_Full_Production_V1/`。**持久保留**，后续 Assembly Agent 直接沿用（§43），不作为 scratch 清理。

## 3. Agnes-only evidence（§39）
- 资源/场景全部 99 条 generation records：`provider=agnes / model=agnes-image-2.1-flash / size_tier=2K`（`_phase_d_evidence.json`）。
- 52 首帧 + 10 尾帧全部经 JobQueue BOUND 链路（`generation_basis.image_provider=agnes`），帧文件实测 **2624x1472**。
- 文本模型 agnes-3.0-flash / agnes-2.5-flash（vision），`text_provider=agnes`（registry 默认 profile，agents_data/settings.json 无 SenseNova 绑定）。
- **全程零 SenseNova 生成记录**。

## 4. ProjectVisualProfile
render_family=2d_animation，revision 05b75e02；art_direction=high-end cinematic 2D / theatrical anime feature（全文见 project_profile.json）；10 style_tags + 10 negative_style_constraints；不塞不存在的字段（P1 真实 schema）。

## 5. Original Producer brief
§6 原文（`_phase_d_prompts/t1_brief.txt`）：自然语言作品级 brief，无任何工具/调用顺序；追加一句「本轮先把故事创作好」属任务优先级而非流程指令。

## 6. Producer workflow timeline（25 轮）
| Turn | 内容 | wall |
|---|---|---|
| T1 | 故事 v1+2 角色+2 地点+14 镜+9 张 2K 图（legacy） | 47min |
| T2 | §9 修订：故事 v2（9 段+萧寒+周伯），删旧重建 | 27min |
| T3 | 镜头描述去风格污染+伤损 Look+12 锚点+17 实例 | 37min |
| T4 | 幻觉轮（触发 Commit C 修复） | 16s |
| T4b/T4c | 道具 4/4 approved；暴露 look.approve 缺口 | 18+3min |
| T5 | 分镜 22→49；场景视图推进 | 20min |
| T6 | 萧寒 v2 批准（先读后批）；57 镜 | 5min |
| T7/T8 | 场景 Agent 会话污染→归档→Commit F | ~2min |
| T9/T10 | 孤儿实例清理（8 个删除）；sciv 审查 churn→根因定位 | 5+14min |
| T11-T14 | 空机位 view 根因修复（Commit G）后 9 段 sciv 全 approved；s16 错绑诊断+重绑 | ~40min |
| T15/T16 | §37 文本评审 7 重复+2 连续性修正；52 镜定稿 | 4min |
| T17-T20 | Batch 1-4：52/52 首帧+10 尾帧+legacy 冒烟（中途 Commit I/J） | ~2h |
| T21-T25 | 视觉审查+再生 6 案例+组合参考（部分受阻，见 §20） | ~1h |

## 7. Script Writer passes
T1 创作 v1 → §9 评审（6 条作品级反馈）→ T2 修订 v2 → 通过。v1→feedback→final 全 trace 在 `_phase_d_runs/`。

## 8. Final story summary
《山雨》：前镖师沈夜二十年护送灭门案证据给凶手而不自知；一封匿名暗号信引他重入荒驿，驿主柳衣（柳家幸存者）揭真相；主谋爪牙萧寒闯入试探、三人混战；柴房发现赵字玉佩与账本；黎明携证据离店清算，驿站重归寂静（开放结尾）。起承转合完整、motif=雨（暴雨→黎明）、贯穿道具=碎布/密信/玉佩/账本/剑。

## 9. Story quality review（§9）
v1 十维评审结论：底子好但规模不足（6 段 2 人）、**因果硬伤**（箱子既被送走又在柴房）、到达动机缺失、碎布无下落、缺外部压力升级、缺服装变化。反馈经 Producer 转达，v2 全部解决（新增匿名信引路+追兵萧寒+周伯+战斗伤损造型）。

## 10. Scene count / purpose
9 段（§7 达标 9-12）：暴雨夜抵达 / 密信揭示 / 暗号信任 / 萧寒试探 / 三人混战 / 柴房真相 / 对峙抉择 / 黎明送别 / 余韵。每段有独立叙事功能。

## 11. Characters
4：沈夜（25，前镖师，疤/蓝袍/佩剑）、柳衣（27，驿主，黑衫油纸伞）、萧寒（35-40，追兵刺客，黑色劲装，非脸谱化）、周伯（60，老马夫，粗布麻衣）。描述均达 §11 生产级（age/body/face/eyes/hair/traits/posture/costume/era）。

## 12. Looks
7 approved：沈夜默认v2+战斗伤损、柳衣默认+战后伤损、萧寒黑色劲装v2、周伯粗布麻衣。§10 要求达成：≥2 主要人物多 Look（沈夜/柳衣各有剧情伤损造型=服装/状态变化）。萧寒 v1/v3 未批准副本仍在档（系统无 look.delete 能力，见 §35 已知限制）。另有 1 个沈夜无名 look（数据卫生瑕疵，不阻塞）。

## 13. Props
4 approved（§13 setup exception：Agnes 2K 单体参考图，`input/prop_references/`，记录于 `_phase_d_setup_exceptions.md`）：赵字玉佩 / 密信与锈铁盒 / 柳字碎布 / 长剑。全部经 Resource Agent register→review→approve 正式链路。

## 14. Compositions
2 个已生成（沈夜·持玉端详 / 柳衣·持剑递剑）但**审查未通过→未批准**（多轮审查收敛失败，如实保留 unreviewed）。§10/§25.D 的 composition 绑定目标部分达成（能力在、产物在、审批未过）。列入 Assembly Agent 待办。

## 15. Locations
2 个（落雁驿：庭院/枯井/大堂/东厢房/柴房/门廊/北山道 7+1 锚点；暴雨山道：石桥/古松/木棚/泥径 4 锚点）。**偏离说明**：§14 建议 3-5 地点；故事 v2 的地理自然收敛为「一站一道」双地点，为凑数硬造第三地点违反 §5「不要为了测功能写故事」。两地点内部空间通过多视角+状态图表达（17 views / 8 approved）。

## 16. Anchors / layouts
12 SpatialAnchors + 2 SetLayout；anchor_states 在 9 段状态中被真实使用（half_open/lit/chaotic/damaged 等剧情驱动变化）。§15 的 4+ 结构锚点要求由落雁驿 8 锚点满足。

## 17. SceneStates
9 实例（每段 1）覆盖 day/night/dusk/early_morning × heavy_rain/clear，lighting 逐段差异化（烛光/冷蓝月光/黎明金光），anchor_states 剧情化（大门 half_open→open→chaotic，主屋 lit→damaged）。s16 状态曾被错绑到暴雨山道+漂成「洞穴」，经审查诚实拒绝→诊断→重绑修正（§22 纪律的自证案例）。

## 18. LocationViews
17 生成 / 8 approved（落雁驿 front×2/大堂内景/柴房内景/山道…、暴雨山道 front/low_angle）。多视角尝试（§16）：rear、overhead、south_west、side 均有真实生成尝试，失败视角如实保留未批准（rear/overhead 延续 C2 §30 的 MODEL_LIMITATION 记录）。

## 19. Sciv
34 生成 / 9 approved（每段恰好 1 张）。34-9=25 的差额=审查 churn 的诚实记录（needs_fix/uncertain 未批准），根因见 §20.3。

## 20. Scene model limitations
1. **rear/overhead 机位**：延续 C2 §30 边界，非对称地点的背面/俯视构图仍被审查诚实拦下。
2. **「去状态」难于「加状态」**：雨夜→清晨的 sciv 审查通过率显著低于状态增强方向。
3. **空机位 view 派生的 sciv 审查期望退化**（"(base)" 乱码）→ 已修（Commit G），但已存在的 2 张空机位 approved view 无法追溯修复（只能弃用）。

## 21. Final Shot count
52（§18 达标 50-65）。分布：s09=6/s10=4/s11=6/s13=6/s14=5/s15=6/s16=6/s17=5/s18=8。景别统计：EWS 3 / WS 4 / MS 21 / MCU 2 / CU 18 / ECU 4。

## 22. Storyboard textual review（§37）
出图前完成文本评审：发现 7 组重复镜头（s09_c02≈c04、s10_c01≈c04≈c05、s16_c01≈c04、s17_c01≈c04、s17_c02≈c05、s18_c01≈c04、s14_c03≈c06）与 2 处连续性错误（s14_c04 雨夜闪电 vs 黎明+空镜挂 cast；s15_c02「竹林」不在地点体系）——全部由 Director 修正（57→52，重复删除、连续性改写）。critique-trace 在 T15/T16 runs。

## 23. Cast audit
抽查+再生轮结论：单人镜头 cast 干净；**多人物镜头存在系统性 provider 失败模式**——同角色多视角参考被渲染为克隆人（s17_c02 两沈夜）、幻觉路人（s15_c01 5+ 闲人）、角色缺失（柳衣被挤出）。已修 Commit J（反克隆参考策略）并再生验证（s17_c02 v2 单人+玉佩插入构图 ✓）。全片逐镜 cast 审计的最终残余疑点列于 Gallery notes。

## 24. Binding readiness
47 个 ShotProductionBinding（scene_binding + character_bindings + dependency_fingerprints）；52 帧全部在有绑定事实的 BOUND 链路生成。个别早期绑定在 sciv 批准前创建（pinned sciv 为 null）但生成时 sciv 已 approved 且 pack 拉取成功——「bind 后 reference 晚到」的 freshness 语义为 C1 既有设计。

## 25. First-frame generation
**52/52 全部完成**（2624x1472 实测）。分批纪律（§38）：Batch1 第一幕→Batch2 试探混战→Batch3 高潮→Batch4 尾声，批间质量确认。免费档期间批次成功率一度 3/10（配额+参考超限），Commit I/J 修复后质量轮全部通过。

## 26. Multi-candidate generation
5 镜 ×2 候选（s09_c05、s09_c08、s10_c02、s16_c02、s17_c02）+ 尾帧再生多候选（s15_c01/s16_c02/s17_c01/s18_c02）= §25.B 的 6-8 目标以「首帧 5 + 尾帧再生 4」覆盖。

## 27. Candidate selection
s17_c02/s10_c02 等全部真实选定（对比克隆版/构图版后选新 candidate，理由记录于 T22/T22b runs）。未选候选全部保留。

## 28. Last-frame / keyframe generation
10 镜 keyframes 模式（s09_c02/s10_c03/s15_c01/s15_c05/s16_c02/s16_c05/s16_c06/s17_c01/s18_c02/s13_c06），首尾帧状态变化明确（下马→入院、震惊→镇定、出鞘、火花弹开、剑断跌坐、跪坐握箱、决绝起身等）。Commit H 补齐 link_mode=keyframes 的 Agent 写路径。

## 29. Regenerate cases
§25.E 达标（≥4）：s09_c01（缺马缺蓑衣→描述强化再生）、s10_c02、s17_c02、s15_c01（首帧 4 例，before/after 候选保留）+ 尾帧 5 镜的 last_frame_description 强化再生。全部由 Director 修改意图后经正式工具再生，零脚本改 prompt。

## 30. BOUND / i2i reference evidence
帧批次 generation_basis 全部含 bound pack（scene pinned sciv / per-char approved look）；Commit I 实证 refs 真实进入 provider 请求（超限 400 即来自真实发送）。Character+Look+Prop 的 composition 绑定：组合参考未批准 → 未进入 binding（§14 如实记录）。

## 31. 2K evidence / 回归
- 2K：全部帧 2624x1472 实测 + records size_tier=2K + config/DEFAULT_RPM 链路（Commit A/B/B2/B3/B4）。
- 回归：`pytest tests/ -q` 全量（结果见 `_phase_d_final_regression.txt`；本轮新增 12 测试：2K 透传 3、限流校准 1、Producer 闸门 3、look.approve 2、instance.delete 1、link_mode 1、cap 反克隆 1；基线 2208 零回归）。

## 32-36. Visual / consistency observations（§26/§27）
- **2K vs 1K（§27）**：Golden_山雨信 1K=1312x736 vs 本轮 2624x1472。抽检对比：2K 在面部细节（眉眼/疤痕）、发丝、服装褶皱、雨丝层次、背景纵深上提升明确；「人物扁」问题在 2K 下改善（体积感来自更精细的 cel 阴影过渡），但整体仍属 cel 风格的平面着色美学——这是风格属性而非分辨率缺陷。**不自动宣称更好，保留实图人工判断**（Gallery + 旧 Golden Run 并列可查）。
- **角色一致性**：单人镜头优（沈夜蓝袍/发髻/疤痕稳定）；柳衣黑衫+油纸伞跨镜稳定。
- **服装一致性**：伤损造型与剧情进度匹配。
- **场景一致性**：落雁驿的枯井/大堂/匾额/山景跨镜可辨识（锚点+sciv 的功劳）；匾额文字仍有 AI 字符伪影。
- **风格一致性**：hand-drawn cel + painted background 全片稳定，无 photoreal/3D 漂移（negative constraints 生效）。
- **AI artifacts**：克隆人（已修）、匾额伪字、个别背景花丛违和（s10_c02 v2）、极端特写的景别遵从偏宽。

## 37. Model artifacts（→ §36 类）
克隆人/幻觉路人（多参考时）、匾额汉字伪影、景别遵从偏松（CU/ECU 常渲染成 MS）、非对称机位（rear/overhead）能力边界。

## 38. Producer autonomy
- **能力实证**：25 轮跨 6+ 小时持续推进；持续理解 original goal（每轮无需重解释项目）；自主决定委派顺序与批处理；诚实上报 blocker（§32 完成判断在 T3/T4c/T7 多次拒绝假装完成）。
- **失败模式**（本轮最重要发现）：①零工具幻觉汇报（T4，伪造 ref id）→ Commit C 闸门；②委派 objective 软化成「检查/汇报」致 Specialist 空转 → Commit E 纪律；③长会话上下文污染致行为退化/幻觉「工具不存在」（T8/T17b/T22）→ 归档会话恢复；④summary 与 canonical 不符（伪造库存数字）。**闸门抓住了谎报，但「伪造工具失败」与「伪造库存数字」类谎言仍靠外部审计兜住**——诚实性需要闸门+审计双层，单层不够。

## 39. Specialist ownership（§33）
- Script Writer：只写 Story/剧本（changed_refs 仅 story/script 域）✓
- Resource Agent：Character/Look/Clothing/Prop/Composition ✓
- Scene Agent：Location/Anchor/Layout/View/Instance/State/Sciv ✓（T10 实证删除悬空实例 8 个）
- Director：Shot/Binding/帧生成 ✓
- **Producer：零直接 canonical 写入**（工具面物理无写工具；全部经 4 个 *.delegate）✓

## 40. Changed_refs / receipts
全部委派走 bridge Task Result（`output/agents/tasks/`）；changed_refs 为空即如实报「无修改」（T4c/T7 多次实证）。Receipts 汇总见 `_phase_d_evidence.json`。

## 41. Locks / sessions / Room
OCC stale-revision 全程高频出现且全部被 Agent 正确处理（重读重试）；per-look single-flight 拒绝并发生成；会话持久化+归档机制用于污染恢复（3 次归档：producer ×2、scene ×1）。Production Room/UI 展示四 Specialist 参与与 changed_refs。

## 42. System bugs found → fixes（Commit A-J）
| Commit | 问题 | 类别 |
|---|---|---|
| A | 2K 无法透传（SIZE_TIERS 缺 ratio + 4 处硬编码 1K） | 接线 |
| B/B2 | 免费 2K RPM 文档值不可持续（95×429）+ config 覆盖遗漏 | 实测校准 |
| C | **Producer 零工具幻觉汇报**（伪造完成） | 闸门缺失 |
| D | **look.approve 工具缺失**（有 pass 证据不能批准，被迫烧图） | 能力接线 |
| E | Producer 委派 objective 软化→Specialist 空转 | 行为 |
| F | **scene.instance.delete 缺失**（悬空实例无法清理） | 能力接线 |
| G | **空机位 view → sciv 审查期望退化**（审查 churn 根因） | 校验缺失 |
| H | link_mode 不可写 → 尾帧无法生产 | 接线 |
| I | **BOUND Pack 输入图超 Agnes 上限 6**（9-13 张→400） | provider 约束 |
| J | **多参考→克隆人/幻觉路人**（反克隆选择策略） | provider 失败模式 |

## 43. Fixes made during run
见上表；全部含真机证据+mock 测试+targeted regression，未重建项目、未重置 canonical（§35 纪律）。

## 44. MODEL_LIMITATIONS
1. 免费 2K 档实际吞吐远低于文档（95×429/38min）；会员档恢复。
2. i2i 输入图 ≤6（硬限制）。
3. 多人形参考→克隆/幻觉路人（Commit J 缓解；2 人镜头仍偶发，需审查兜底）。
4. rear/overhead 机位能力边界（延续 C2）。
5. 景别遵从：CU/ECU 指令常渲染为 MS；「骑马穿行」类复杂动作指令部分遵从（s09_c01 再生仍缺马）。
6. 汉字（匾额/信纸字迹）均为伪字——未来 Assembly 需后期贴字或规避。

## 45. Known limitations（留给 Assembly Agent）
1. Compositions 审查未收敛（2 个 unreviewed）→ 待批准后可进 binding。
2. 萧寒 look v1/v3 副本未删（无 look.delete 能力）；1 个无名 look 待清理。
3. 旧 s01-s08 相关 34-9=25 张未批准 sciv 候选与失败记录保留在档（诚实历史，不阻塞）。
4. §42 视频 smoke 未做（本轮聚焦画面质量；视频 5 RPM/日 500s 额度可用）。
5. shot 级 vision review 未进入生成主链（本轮由 Director 抽查+人评兜底）——建议 Assembly 阶段把 visual.review_image 编排进批次流程。
6. story_text（2482 字）与 9 段场景的文本粒度偶有参差（第 N 场 vs shot 段落对应关系在 Gallery 可查）。

## 46. Gallery
`_phase_d_gallery.html`（51KB）：ProjectVisualProfile / Story(9) / Characters+Looks+Props+Compositions / Locations+Views+States / Storyboard 52 镜（每镜 §28 全字段：id/scene/描述/cast/Look/Sciv/binding/mode/provider/res/candidates/selected/first/last/notes）。连续可从头看到尾。

## 47. Performance
T1 47min → 各轮 5-47min；52 帧 2K 总耗时约 3.5h（含限速期+限流修复前后）；Agent turn duration/tool calls/retry counts 全量在 telemetry 与 runs 目录。按 §40，性能不作为验收指标；QUALITY/CORRECTNESS/AUTONOMY/CONSISTENCY 为准。

## 48. Regression
见 §31。s2b1 四项选择的既有 import 依赖 flake 不计（整目录运行通过，C2 已记录）。

## 49. Final project preservation state
`Golden_Full_Production_V1` 完整保留：story/characters/looks/clothings/props/compositions/scene_system/shot_production_bindings/52 首帧+10 尾帧+全部候选与失败记录/receipts/telemetry。**不得删除或重置**。

## 50. Assembly Agent handoff readiness
**可以接手**。故事、资源、场景系统、分镜、绑定、首尾帧齐备；缺什么已列 §45：composition 审批收口、视频批量（5 RPM/日 500s）、对白/配音（Native AV 链路已冻结可用）、Sequence（S1+S2 已冻结可用）、transitions/mix/export。

---

# 最终 28 问（§46）

1. **四 Specialist 是否围绕同一项目协同完成一部作品？** 是。同一 canonical、同一 Room、全程 receipts 可溯。
2. **Producer 能否持续推进会而非单次 delegate？** 是（25 轮持续编排+完成判断+诚实上报）；但暴露幻觉与上下文退化两类失败模式，闸门+归档+审计兜住（§38）。
3. **故事是否达到真实短片水准？** 是——《山雨》有完整弧线、悬念、动作与余韵；非 fixture 故事。
4. **Story Scenes 数量质量？** 9 段（达标），每段有目的。
5. **Resource Agent 是否备齐生产资源？** 是（4 角色/7 Looks/4 Props 全 approved）；Composition 未批准（§14）。
6. **Scene Agent 是否备足且诚实的场景参考？** 是（9 段全 READY，每段 1 approved sciv；失败视角如实保留）。
7. **Director 是否产出足够数量且有导演质量的 Shots？** 52 镜，景别/节奏/道具特写/情绪镜头覆盖（§22 评审后）。
8. **Cast omission 是否为 0？** 文本层修正后为 0；**图像层曾出现克隆/缺角色/幻觉路人**（Commit J 修复后再生样本干净；全片逐镜终审残余见 Gallery notes）。
9. **正式 Shots 是否全部 READY/BOUND？** 是（52/52 BOUND 生成；47 绑定记录+依赖指纹）。
10. **是否发生正式镜头 silent legacy fallback？** 是——T1 的 14 镜（当时无绑定且 Director 未传 bound_required）与 T20 的 1 镜显式 COMPATIBILITY_ONLY 冒烟。T2 重建后主链零 silent fallback；legacy 冒烟为 §25.G 授权且已标注。
11. **52 首帧是否真正生成？** 是（文件+2624x1472 实测）。
12. **是否全部 Agnes？** 是（§3 证据）。
13. **2K 正式档？** 是。
14. **Multi-candidate 真实使用并选择？** 是（5+4 镜，理由在案）。
15. **Last-frame 真实覆盖？** 是（10 镜 ≥8）。
16. **Reference/i2i 真实进入 generation basis？** 是（Commit I 的 400 即直接证据）。
17. **Regeneration 由 Agent 驱动？** 是（4 首帧+5 尾帧 before/after）。
18. **Character consistency？** 良好（单人镜头优；多人镜头修复后改善）。
19. **Look/costume consistency？** 良好（伤损进度匹配剧情）。
20. **Scene consistency？** 良好（锚点/枯井/大堂跨镜可辨）。
21. **Style consistency？** 优（2d_animation 负面约束全程生效，零漂移）。
22. **2K 相比 1K 是否明显改善？** 分辨率细节明确提升（面部/发丝/褶皱/纵深）；体积感改善但 cel 平面着色风格属性不变（实图并列供人工终审）。
23. **哪些失败属于 system？** §42 十项（全部已修+回归）。
24. **哪些失败属于 Agent？** Producer 幻觉汇报/上下文退化/客观性软化；Scene Agent 伪造「工具不存在」；CD 误判参考串扰——闸门+审计缓解，非全防。
25. **哪些属于 Agnes model limitation？** §44 六项。
26. **有没有 BLOCKER？** 无。
27. **项目是否可直接留给 Assembly Agent？** 可以（§50）。
28. **若不够，缺什么？** 纯内容质量层面（composition 审批、个别帧再生的候选收敛、视频/音频/合成）——后端能力已具备，无能力缺口。

---

## §47 完成声明
Full Golden Production Run + 报告 + Gallery + 回归 已完成。**到此停止**：不开始 Assembly Agent，不创建另一测试项目；`Golden_Full_Production_V1` 全部事实与资产保留，等待下一阶段直接继续。

## 附：本轮 Commits（13）
c7f8ac9 A · 934de49 B · 91e6c59 B2 · b788f15 B3 · 0981ea3 B4 · 485734d C · fb54c7e D · 0a41b0e E · cad22a5 F · 5fcbec0 G · eb5a278 H · 97f97ec I · 2c157a0 J
真机证据（scratch，不入库）：`_phase_d_runs/`（25 轮）、`_phase_d_evidence.json`、`_phase_d_gallery.html`、`_phase_d_backend.log`、`_phase_d_2k_probe/`、`_phase_d_setup_exceptions.md`。
