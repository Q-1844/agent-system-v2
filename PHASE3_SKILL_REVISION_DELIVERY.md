# 第三阶段交付：视频连续生产 Skill 修订与验收（2026-09-27）

执行依据：`详细流程文件/修复阶段3/PHASE3_VIDEO_SKILL_REVISION_EXECUTION.md`。
边界遵守：零付费调用（无任何图片/视频生成）；未续旧桥接；未重做 s03_c01；未把 expB 候选提升进原项目 canonical；未迁移默认模型（config.yaml 未动）。改动全部在工作树，未提交 commit（diff 可审查：`git diff` 共 15 文件 +256/-48，另加 2 个新文件：本报告、tests/test_phase3_relay_guards.py）。

---

## 一、冲突盘点（报告 vs 仓库，逐条标状态）

| 项 | 状态 | 关键证据 |
|---|---|---|
| A. multi+prev_tail 按全片展平序取上一镜；组首会接上一组末镜 | **已证实** | video_adapter.resolve_multi_relay_source（展平循环）；P40A s02_c01（组首）实际接力源=s01_c02（input_visuals 实测） |
| B. multi 语义参考同步进 first_frame 槽+mark_done 后改 i2v 会误消费；末帧同路径重提取覆盖旧 local 字节 | **已证实**（其中"BOUND 路径误消费"此前被 freshness gate 的 artifact_basis_missing 意外拦住，属碰巧而非设计） | routers/shots.py:_sync_first_frame_slot / _extract_video_end_frame（固定路径）；P40A s02_c02 ffsync 候选 local 字节已被重生成覆盖（URL≠local 字节实测）；39F Case-C 测试即此场景 |
| C. reference_previous 同场景 gate vs multi 跨段接力矛盾 | **已证实** | routers/shots.py continuity_status:469-470（同场景强制）vs resolve_multi_relay_source（跨段常态） |
| D. "已生成首帧"≠"已输入视频"（s03_c01 合格静帧未被 multi 消费） | **已证实** | P40A s03_c01 input_visuals=[relay ← s02_c02 旧尾帧]；ff_7bbf9e12_1 从未进 provider 请求 |
| E. 融合图请求的参考构成 | **已证实（含缺口，已补）** | expB 批次 ff_f38c1865：sent=[scene_instance_view, character_look]（SIV+Look 已实际发送），ref_layout=continuity_tail 证明尾帧追加；但**持久 receipt 此前不含尾帧身份**（used_reference_ids 漏临时尾帧）→ 本阶段补 `generation_basis.continuity_tail`（含 source_shot_id/end_source_version/url） |
| F. vv_6e26ffbc 仅在 expB；原项目 canonical 未动 | **已证实** | 原项目 project.json：final_video=vv_11e01881、opening_source=independent、link_mode=multi（本轮复核）；s03_c01 仍引用 s02_c02 旧版 vv_e716ddfe 尾帧（过期依赖未变）。用户"效果非常不错"记为反馈，未伪造选片 |
| G. v2.0 官方下线公告 vs 近期成功调用并存 | **已证实（并存事实）** | 官方 wiki："已于 2026-09-25 23:59:59（UTC+8）正式下线"（2026-09-27 核读）；同日本仓库 v2.0 真实出片（vv_6e26ffbc，URL 路径 agnes-video-v2.0）。持续可用性=**未确认**；2.5 reference 官方支持 ≤8 图/与 keyframe 互斥/不锁首帧（wiki，2026-09-27），本地编译**未实现** |

## 二、Skill 修订（关键 diff：原句→新句→原因）

权威文件：`full-story-relay-calibration/SKILL.md`（v1.1.0）、`references/director.md`、**新增** `references/capability-facts.md`；`keyframes-bridge-repair/SKILL.md`（v1.1.0）。

1. **SKILL.md 新增「起步/再锚定决策入口」（唯一路径决策点）**：每镜按叙事关系→上游端点质量→可用槽位/能力决策（原规则只约束组首/接力镜）；端点四分级（合格/身份不可读/已画错/版本过期）；路径 a-f 全表（a=尾帧硬边界、b=裸尾帧语义参考（🔴不得称硬接）、c=融合首帧起步（合成→目检→i2v，s02_c02 正例但不升默认）、d=Look 主导独立首帧、e=未来路径（2.5 reference 等，禁塞 v2.0 单图槽）、f=桥（端点合格才修缝））；组内再锚定触发器；新组首镜显式跨组决策（prev_tail 残留不静默，有 prev_tail_cross_segment_relay 诊断）；first_frame 槽硬边界≠multi 语义参考（multi_reference_only 标记 + first_frame_slot_not_hard_boundary 拦截）；上游重选→下游待复核（不机械全重做、不沿用过期引用）；生成后视觉门槛（先查真实编译请求再看帧；身份合格+剧情尾态偏离→owner 裁定，不得悄悄改 expected_tail）；五类状态不混淆（实验候选/积极反馈/正式选定/故事事实调整/下游批准）。原因：路径决策此前散落且与 provider 事实冲突（执行文档 §二 1-8 逐项落点）。
2. **SKILL.md 组约定条改写**：原句"组内非首镜默认尾帧接力（上一镜真实尾帧进本镜首帧槽）；组首镜的图作参考优先…新组首镜用新参考图/新首帧图"→新句"组内怎么接、组首怎么起步，一律走决策入口按端点质量选路径…注意 v2.0 multi 接力的 provider 事实是单图语义参考（不锁首帧），「尾帧进首帧槽」是生成后系统如实槽位同步，不是 provider 级硬边界"。原因：默认接力在远景端点（s02_c02 实证）注定身份漂移；原句把槽位显示误当 provider 能力。
3. **references/director.md 槽位语义改写**：原句"组内接续：上一镜真实尾帧进本镜首帧槽——这是边界不是参考；接力镜不得生成分镜首帧；组首镜默认参考直生"→新句"multi+prev_tail 是单图语义参考…真正硬首帧只有 inherit_previous 与融合首帧起步两条；裸尾帧接力不得在 trace/汇报中称硬接；改 i2v 前必须重新确认消费的首帧（first_frame_slot_not_hard_boundary 拦截）；上游重选后下游旧 end_source_version 先复核再沿用"。原因：原句与 provider 事实相反，且禁止生成分镜首帧堵死了路径 c；39E 锚点测试已同步更新（tests/test_p39e_trace_hints.py，注明修订依据）。
4. **keyframes-bridge-repair/SKILL.md 加决策入口唯一段**：桥只承接两端点合格且值得保留后的接缝修复；端点画错→回连续生产 Skill 修端点，不插桥。原因：消除竞争规则（执行文档 §边界）。
5. **skill.json**：两包 v1.1.0；relay 触发词补"身份漂移/再锚定/组首/尾帧接续/融合首帧/起步决策"。原因：runtime 为子串匹配，原触发词无法命中"组内身份漂移/新组首镜"类真实任务（确定性验证见 §四）。

能力事实与判断分离：六状态能力表放 `references/capability-facts.md`（带日期与来源），SKILL.md 只写何时读它。

## 三、最小代码守卫（编译层/truth gate 失败，非文本警告）+ 测试

| 守卫 | 位置 | 行为 | 测试 |
|---|---|---|---|
| ffsync 槽位语义标记 | routers/shots.py `_sync_first_frame_slot` | 同步批次写 `generation_basis.slot_semantics=multi_reference_only`（含来源版本）；历史无标记批次仍被 artifact_basis_missing fail closed | test_phase3_relay_guards.py ×3（marker 拦截/正常批次放行/legacy 仍拦） |
| 槽位语义 gate | src/generation/video_adapter.py `_resolve_boundary_frame` | i2v/keyframes 消费带标记槽 → `first_frame_slot_not_hard_boundary` 结构化拒绝（先于 freshness），hint 指明 inherit_previous/重生成两条正路 | 同上 |
| 跨段接力可见性 | src/generation/video_adapter.py `resolve_multi_relay_source` + `build_bound_video_input` + src/generation/native_av_pack.py 重算侧 | 返回 `cross_segment` → 诊断 `prev_tail_cross_segment_relay` + 进 visual basis 指纹（两侧对称）；**不阻断**——跨段显式接力合法（39 Canary 实证），硬失败会误伤；决策由 Skill 入口承担（偏差已在报告中说明） | test：跨段 True/同段 False |
| 末帧版本化文件名 | routers/shots.py `_extract_video_end_frame` | `{shot_id}_video_end_{version_id}.jpg`——重提取不再覆盖旧字节（消费方全走 `video_end_frame.local`，grep 证实无硬编码路径依赖） | test：版本名落盘、旧哨兵字节不变（本地 ffmpeg 生成测试视频，零费用） |
| continuity_tail 持久 provenance | src/generation/image_adapter.py `BoundImageRequest.continuity_ref` + routers/shots.py 批次 basis | 持久 receipt 补上临时尾帧身份（来源镜头/来源版本/URL）——补 §一.E 缺口 | test：provenance 映射 + 编译层发送顺序不变 |

回归结果：**201 passed / 0 failed**（skill 全家+s3c1/s3c2+multi_34+provenance+seam+input_visuals+p39e/p39f/p39 系；含 3 处锚点随语义更新：39E 首帧规则句、组约定句、两包版本号；1 处指纹对称性修复 native_av_pack.py）。编译 dry-run 断言（零调用）：multi 恰好 1 参考（既有 test_multi_compile_requires_exactly_one_reference）、v2.0 双图 400 口径（X005）、2.5 非 t2v/i2v 拒绝——全部通过。

## 四、九例行为验收（干净上下文盲测，18 次：旧/新各 9；只读 fixture）

方法：同一中性 fixture（`_tmp/phase3_eval/fixtures.md`），旧 Skill=git HEAD 文本、新 Skill=修订版，各置独立目录；每例独立子代理自行读技能后输出决策 JSON；不发生成。**局限披露**：fixture 文件九例同文（agent 读全文），例 I 文本含"合成新首帧"字样——旧规则 agent 虽可见仍未选融合路径（差异仍成立），但严格双盲被削弱，记录在案。

| 例 | 旧规则决策 | 新规则决策 | 断言判定 |
|---|---|---|---|
| A 合格尾帧继续 | multi 接力，但称"接力**硬首帧**边界"（语义错标） | 路径 a：inherit_previous 作 i2v 硬首帧（六状态全绿），弃 b（不锁首帧+身份漂移负例） | 新✓ 旧⚠️ |
| B 远景身份不可读 | Look 单参考 multi 直生，尾帧不进任何槽（丢语境、无首帧保证） | **路径 c 融合首帧起步**（合成→目检→i2v），引用 X005/marker gate/下线核验 | 新✓（必须项命中）旧⚠️ |
| C 端点已画错 | 弃端点，Look 参考 multi 直生（无第 0 帧保证） | 路径 d：Look 生成首帧+目检+**i2v 硬首帧**（满足"第 0 帧正确"），上游修复独立上报 | 新✓ 旧⚠️ |
| D 新组正常切镜 | intentional_cut，Look/SIV 直生 | 同左（+跨组显式决策、dry-run 核对无 prev_tail） | 双✓ |
| E 新组需接续+prev_tail 残留 | 接力，称"RELAY_START_BOUNDARY 蓝框边界不是参考"（语义错标），无跨段审议 | 显式跨组延续：cross_segment 诊断知情+dry-run 核对源镜，如实标"单语义参考非硬接" | 新✓ 旧⚠️ |
| F multi 同步槽改 i2v | **违规**：直接消费 cand_ffsync_77 作硬首帧 i2v（旧码层仅靠碰巧的 artifact_basis_missing 拦截） | 知道 marker gate 会拦，改走 inherit_previous 重锚（合法硬首帧），并核对下游级联 | 新✓ 旧✗ |
| G 上游改选 | 先测量帧检再裁定，不自动传播 | 同左（+过期引用不沿用、待复核标记、不机械重做） | 双✓ |
| H 双缝对照 | 缝1桥（用户批准）、缝2记 intentional_cut 不修 | 同左（+能力核验引用事实表） | 双✓ |
| I 融合候选+尾态冲突 | **违规**：直接 promote 进 canonical+自主插曲推进（无正式选片即改 canonical） | 「视觉合格、剧情尾态偏离」交剧情 owner；口头好评≠选片；接受后才循 canonical 留痕更新 expected_tail，禁静默改写 | 新✓ 旧✗ |

**对照结论**：旧规则 2 次实质违规（F 消费语义参考作硬帧、I 无选片即改 canonical）+3 次语义错标/能力缺口（A/E 硬边界误标、B/C 无首帧保证）；新规则 0 违规，9 例路径全部符合行为断言（B 识别再锚定、D 独立起步、I 保留尾态冲突、未调用未实现多图、未接错误端点、未桥普通切镜）。触发验证（确定性，非 LLM）：「组内身份漂移」「新组首镜怎么起步」「尾帧接续」→ 命中 relay Skill；「插桥」→ 命中桥 Skill。

## 五、项目事实状态（只报告，不晋升）

- 原项目 Crane_Lake_P40A：s02_c02 final=vv_11e01881（裸尾帧接力，身份漂移未修复）；s03_c01 final=vv_f4112813，其接力引用 s02_c02 旧版 vv_e716ddfe 尾帧（**过期依赖**）；s02_c02→s03_c01 接缝 34.4（40 Canary 记录）；s03_c01 尾态=合格首帧从未被视频消费。
- expB 克隆：融合首帧候选链（ff_f38c1865 → vv_6e26ffbc）在案；已知瑕疵=结尾提前触水（expected_tail=空中减速）+末帧颈前偏粉；用户积极反馈已记录，**未正式选片、未晋升**。

## 六、下一阶段（第四阶段）前置条件与阻断项

1. **模型可用性核验（任何付费生成前）**：v2.0 官方已公告下线但 2026-09-27 仍实测出片——先小样核验账户/入口/模型再批量；若迁移 2.5：先实现 `mode=reference`（官方 ≤8 图、与 keyframe 互斥、不锁首帧）+ 实际请求抓取 + 小样视觉验证，三关齐备才启用；不得把官方文档当本地已开放。
2. 未解决代码缺口（阻断项）：无。本轮三风险均有编译层守卫+测试；2.5 迁移为独立任务（非阻断，按上条三关验收）。
3. 验收条件（第四阶段）：s02_c02 依新 Skill 决策入口重判（用户选定路径后走合法选片）；s03_c01 起步基于选定版本+组间叙事重决策；尾态冲突按「视觉合格、剧情尾态偏离」由用户裁定，不静默改写。

**本轮到此停止，不进入第四阶段。**
