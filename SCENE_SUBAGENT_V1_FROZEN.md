# Scene Sub-Agent V1 · 冻结记录

> 2026-09-03 建立。Director Scene Sub-Agent V1 经最终冻结验收，判定 **PASS WITH NON-BLOCKERS**，正式冻结。
> **冻结后，除非真实生产使用出现 canonical corruption / 越权写 / 核心 proposal 链路失效等明确问题，否则不继续主动修改 Scene Sub-Agent V1。**
> 建立在 [[V1_FROZEN]]（2026-09-01，submodule `e3bc902`）之上：`e3bc902` +16 commits（Sub-Agent 本体 7 阶段 + 治理四件套 + 架构文档）→ `5d19b7b`（四项 Runtime 增强 Replay 验收）→ 三个字符串化兼容修复 → `0d752a1`（本冻结点）。

## 冻结点
| 项 | 值 |
|----|----|
| submodule branch/HEAD | `design/subagent-v1` @ `0d752a1` |
| 父仓库 | `master`（pointer 待更新，见末尾）|
| 模型/provider | `agnes-2.5-flash` @ `https://api.agnes-ai.cn`，`text_provider=agnes` |
| `subagent_enabled` | 原=absent(false)；测试期置 true（每 turn 读盘，无需重启）；**验收后已恢复 absent** |
| subagent 测试 | **92 passed**（diff_engine 40 + instance/tools/governance/registry/bitforbit/fingerprints），0 回归 |
| tag | `subagent-v1-frozen-20260903`（见末尾创建步骤；未 push）|

## 三个字符串化兼容修复（逐字段消灭真机证据失败点）
同一模型行为根因：agnes-2.5-flash 在大参数（proposal.submit 是 Director 体系参数最大的工具，全中文）+ 中文引号时，把嵌套数组/对象都序列化成 JSON 字符串塞进参数（SW 的 `_loose_json` 早已记载同行为）。三个 normalize helper 同模式：dict 直通浅拷贝 / 非空 str→`json.loads` 且类型必须匹配 / None·空串→空 / 非法 JSON 或类型不符→结构化 DiffError。**不放宽任何后续校验（E1–E6 / basis 指纹防伪 / border constraints / DiffEngine）**。

| commit | 字段 | helper | 错误码 | 咽喉点 |
|--------|------|--------|--------|--------|
| `9f51bd5` | `end_state`（数组）| `normalize_end_state` | `bad_end_state` | normalize_end_state 入口（str 且 `[` 开头→json.loads）|
| `3cce547` | `removed_shot_rationales`（dict）| `normalize_removed_rationales` | `bad_end_state` | validate_end_state 消费处 + instance.submit 落盘前 + run_diff_commit commit 边界三处全过 helper |
| `0d752a1` | `basis`（对象）| `normalize_basis` | `basis_fabricated` | instance._submit:254 basis 段包进 try/except DiffError + run_diff_commit:337 basis 取值+stale 检查整体进 try/except |

## 最终冻结验收（2026-09-03，中等规模真机）
**2 个独立 fresh 项目 / 4 场景（s02·s04·s06·s07·s08）/ 8 次 delegation / 5 类任务**：

### 链路稳定性
- **12 次 proposal.submit：9 成功 / 3 结构化 bounce / 0 tool.failed**
- 3 bounce 全是 `undeclared_deletion`（模型漏列 shot 未给理由，E6 正常拦截后纠正重交，可恢复）
- **零字符串化崩溃**：无 `end_state 必须是有序数组` / 无 `removed_shot_rationales {` / 无 `'str' object has no attribute 'get'` / 无 basis 裸异常

### 完整链路真机走通 3 次
`scene.delegate → Scene Agent 读 canonical → proposal.submit → MD proposal.read → reconcile → proposal.commit → DiffEngine → canonical`：
1. **s02 T1**（整场 redesign）：delete c03 + create c05/c06 + reorder，DiffEngine `shot.delete s03_c05` verified，canonical 4→5 镜
2. **s08 Turn A**（整场 redesign）：5 镜 update，DiffEngine verified
3. **s08 Turn B**（重复 delegation）：再次 update，DiffEngine verified

### 重复 delegation + decision memory（§8 关键 PASS）
s08 二次 delegation（A→commit→B→commit），Turn B 新 SA instance：
- **basis 指纹 `1d2d0774` ≠ Turn A `a26c9aa0`** + shot revision 不同（A=5c99100a / B=a167fe07）→**读了 A commit 后的最新 canonical，非 stale**
- 17 次真实读取，end_state 引用全为当前真实 6 shot，**零引用已删/不存在 shot**
- 产出独立新 proposal（非照搬 A），**无 stale memory 自我强化**

### reconcile 分布
- accept(commit)：3（s02 T1、s08 A、s08 B）
- no-action（MD read proposal 后未 commit）：1（s07 T4，读 3 个 proposal 后 honest_fallback）
- reject/rebase/partial：0（本轮模型自然只产生 accept 与 no-action）
- MD 每次都真实 read proposal 后才决定（未盲 commit）

### canonical 完整性（全项目只读审计）
两项目均：**零 duplicate / 零 dangling/无效 relay / 零 index gap / 零无效 char 引用 / 零跨场景错写**。proposal 目标与 commit 结果一致；被拒/aborted delegation 未产生错误 canonical 改动（MD fallback 是独立 MD-direct 路径，非越权）。

### 所有权与安全
- Scene Agent **物理无 canonical 写权限**（白名单 `SCENE_AGENT_TOOLS = DIRECTOR_READ_TOOLS ∪ {proposal.submit}`，无写工具）
- SA 误调 MD 侧工具（proposal.read/commit）被 read_only 闸门正确拒绝，可恢复
- 只有 Main Director 才能 proposal.read/reconcile/commit
- OCC / basis 指纹校验正常；revision 推进正常

## NON-BLOCKER Backlog（进 backlog，不阻塞冻结）
均为模型行为 / graceful 恢复，非 runtime/安全缺陷：

| # | 问题 | 场景 | 类别 | 处置 |
|---|------|------|------|------|
| N1 | SA 偶发早退不提交（3 读后 aborted no_proposal）| s04 T2 | 模型行为（delegation 偏保守）| MD fallback update_many，graceful |
| N2 | MD 误路由到 Clean Review（`is_review_request` 误中"连续性"等词）| s06 T3 | 模型行为/路由 | 0 delegation，0 修改 |
| N3 | MD 读 proposal 后不 commit（honest_fallback）| s07 T4 | 模型行为 | 零写，graceful |
| N4 | `HARD_DELEGATION_CAP=6` 在中等规模测试偏紧 | T5 | 设计参数（非 bug）| 长会话场景可考虑调高 |
| N5 | 多次无害读取（T4 58 读、A/B 17 读）| 多处 | 模型行为 | 不影响 canonical |
| N6 | latency 85-200s/turn | 全局 | 模型/IO | 可接受 |
| N7 | 3 个可恢复 bounce（undeclared_deletion）| s02/s07 | 模型行为 | E6 正常工作，自愈 |
| N8（既有，非本轮）| `get_brief` fallback 取首条 user message 当 brief | 长run P5 | runtime | 独立处理 |
| N9（既有，非本轮）| truth-gate 不防 false-failure 叙述 | 长run P4 | runtime | 独立处理 |

## 证据索引（均保留）
- 冻结验收事件流：`_stress/logs/Subagent_Freeze_20260903_director_fz_t{1..5}_*.events.jsonl` + `Subagent_Freeze2_20260903_director_fz2_{tA,tB}_*.events.jsonl`
- canonical 快照：`_stress/snapshots/Subagent_Freeze_20260903_BEFORE.json`（51 镜/指纹 22057af4）
- delegation/proposal：`projects/Subagent_Freeze_20260903/output/agents/scene_agents/{delegations,proposals}/`（6 dlg + 多 prop）+ `projects/Subagent_Freeze2_20260903/.../`（s08 A/B 2 dlg committed + 2 prop）
- SA 实例会话：`projects/Subagent_Freeze*/output/agents/sessions/sess_*.json`（scope_type=scene_agent）
- 前序 smoke 证据（end_state/rationales/basis 逐字段确认）：`_stress/logs/Subagent_Smoke*_*.events.jsonl` + `_stress/longrun_20260903_1203_FINAL_REPORT.md`
- 架构文档：`docs/agents/director_subagent_v1_part{1,2,3}_*.md`（本体论/协议/实现）

## 冻结后纪律
1. **不主动修改**：除非真实生产出现 canonical corruption / 越权写 / 核心 proposal 链路失效等**明确问题**。
2. **NON-BLOCKER 不在本基线修**：N1–N9 进入 backlog，独立分支/后续版本处理。
3. **subagent_enabled 默认 absent**：生产环境如需启用 Sub-Agent，显式置 true（每 turn 读盘，无需重启）；不启用则 bit-for-bit 与无 Sub-Agent 基线一致。
4. **父仓库 pointer**：本冻结点 `0d752a1` 需在父仓库 `ai工作流` 更新 submodule pointer 并提交（见下）。

## 父仓库 pointer 更新（待执行）
```
cd <父仓库>/animation-storyboard  # 已在 0d752a1
cd ..                              # 回父仓库
git add animation-storyboard       # 记录 submodule pointer
git commit -m "chore: submodule pointer - Scene Sub-Agent V1 frozen (animation-storyboard 0d752a1)"
git tag subagent-v1-frozen-20260903   # 可选 tag
```
