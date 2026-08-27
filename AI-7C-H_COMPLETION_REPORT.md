# AI-7C-H 完成报告 — Truthful Agent Autonomy & Post-Freeze Production Hardening

日期：2026-08-23 03:00（Asia/Shanghai）
执行 agent：GLM-5.3（接续被截断的前一 agent，从 H2/H3 前端验证点继续）

---

## 1. 基线（Baseline）

- 分支：`refactor/shot-pipeline`
- 起点 HEAD：`96ddcc4`（AI-7C-S PASS）
- 起点工作树：上一 agent 已提交 `cd17535`（后端 F-1/K1/F-2 + normalize_module + H3 后端部分），前端 H2/H3 改动未提交（已在本轮验证后提交为 `636c7ae`）
- 起点 dirty WIP：
  - 上一 agent 的 normalize_module WIP（`change_analysis.py`/`inquiry.py`）——**已按方案 A 正式化**（cd17535，全部模块名消费点归一）
  - 用户自有改动（未触碰、未提交）：`saved_characters/` 两目录删除、`详细流程文件/提示词.md`、多个 audit log/py（AI-7C-S 遗留工具）

## 2. 本阶段全部提交

| commit | 内容 |
|---|---|
| `cd17535`（前 agent） | 后端 F-1/K1/F-2 + normalize_module 正式化 + H3 后端（context_scope/分类合并/真话提示词/工作流快照） |
| `636c7ae` | H2/H3 前端：K3 项目身份持久化、Floating Bar 写意图桥接、running 派生真相、§12 运行卡、§15 冲突二选一 |
| `8d3e24c` | H4 确定性测试 9 项（F-1/K1/F-2/Event B）+ **修复 K1 legacy 路径 bug**（无 appearance_sig 的旧调用误清空 done_views → 恢复 done-skip） |
| `c10bcea` | 双角色崩溃重启 soak（§17 必备场景） |
| `bd6f98a` | H3 澄清加固（真实 Golden Path 暴露的 4 个缺陷修复，见 §11） |

## 3. 变更的生产文件

后端：`src/revision/models.py`、`src/agents/character_views_handler.py`、`src/agents/change_protocol/execution_service.py`、`src/agents/change_protocol/service.py`、`src/agents/change_protocol/character_proposal.py`、`src/agents/change_analysis.py`、`src/agents/inquiry.py`、`src/agents/registry.py`、`src/agents/runtime.py`、`src/agents/team_chat.py`、`routers/agents.py`
前端：`src/api/index.ts`、`src/store/index.ts`、`src/store/agentUi.ts`、`src/store/changeSession.ts`、`src/layouts/MainLayout.vue`、`src/views/characters/index.vue`、`src/components/agent/{AgentConversationSurface,FloatingAgentBar,FloatingAgentHost,TeamChatPanel}.vue`
测试：`tests/test_ai_7ch_h4.py`（新增，10 测试）

## 4. F-1 根因与修复

**根因**：`creative_source_signature`（`src/revision/models.py`）把 Character `views` 的 keys 计入创作语义签名。character sync 的合法生产写回改变 views → 重启 re-entry 比对签名不符 → 误报 `PROJECT_CHANGED_AFTER_APPROVAL`。
**修复**：从签名剔除 Character.views（生产写回的派生态）。真实外部创作突变（description_en 变化）仍改变签名。
**证明**：`test_f1_views_writeback_keeps_signature_external_mutation_changes`（写回不变 + 真突变仍变）+ 双角色 soak（重启后 `status != "blocked"`、commit 仍 committed）+ 真实 Golden Path Event B（重启续跑零 stale 误报）。

## 5. K1 根因与修复

**根因**：`handler_character_views` 旧幂等规则"done views 无条件跳过"——外观修订后旧 done 三视图是陈旧输入，导致 0 重生成、下游用旧外观。
**修复**：`appearance_sig = sha256(description_en + art_style)`。sig 一致（resume 同一生成）→ 跳过 done views；sig 不同/无记录（新批准外观）→ **全部真实重生成**；无 sig（legacy 调用）→ 保持旧 done-skip（本轮修复的边界 bug：初版实现会误清空）。新视图落盘为版本化文件 `{view}_{sig8}_seg.png`（历史资产不删）。
**真实验收**（live Agnes，§19）：赤焰守卫 done 三视图（旧 sig f69d964a）→ 批准新外观 → **真实重生成 3 张**（新 Agnes url + 新版本化文件 f326d472 + views_meta 更新）→ 旧文件保留 → **下游 s01_c01 的 first_frame `char_ref_snapshot.url` 与新 front view url 完全一致**（新参考被真实消费）。

## 6. K3 修复

localStorage 持久化选中项目身份（`agnes:project:selected:v1`，仅身份/导航态）；`MainLayout` 挂载即恢复（对照后端项目列表，无效/已删项目回退默认，无崩溃循环）；`TeamChatPanel`/`FloatingAgentHost` 的后端真相恢复 `await identityReady`（保证恢复正确项目的会话）。session/execution 真相仍只在后端。
**证明**：Golden Path 全程多次硬刷新（F5 + 后端重启后的重连）后项目恒为 AGNES_GoldenPath_H、会话/执行状态从后端重建。

## 7. F-2 修复

`run_production_planning` 每 scene plan 实际 approved 后**立即**持久化 checkpoint 进 `ex.planning`；重启 re-entry 用 done_records 跳过已完成 scene（不重跑 planner LLM），不依赖内存 set。
**证明**：`test_f2_per_scene_checkpoint_no_duplicate_planner_on_reentry`——2 scene、s02 崩溃、重入后 **s01 planner 恰好 1 次调用**（call-count 证明），s02 续跑，targeted run 覆盖两 scene，planning 记录无重复。

## 8. normalize_module 处置（方案 A）

上一 agent 的 WIP（LLM 模块名别名归一，如 characters→character）**正确且必需**——已在全部模块名消费点正式化：`_normalize_analysis`（change_analysis）、`_wrap_plan_draft`、`ReadOnlyPlanValidator`。已随 cd17535 提交，本阶段 Golden Path 的自然语言前半链路依赖它运行。

## 9. 新的 Agent 交互路由

Floating Bar / Specialist 直聊统一意图路由（`agentUi.send`）：
- 变更意图（CHANGE_INTENT_RE，与后端 `_CHANGE_VERBS` 同族、双语）→ `_bridgeChangeIntent` → durable ChangeSession（source=`floating_bar`/`direct_agent_chat`，携带页面 contextScope）
- 只读问题 → 普通 Agent 会话
- 活跃执行 + 新变更（§15）→ 确定性二选一卡片（排队等完成后处理 / 停止当前并重新规划），不静默改已批准计划
- 后端分类：`classify_request`（词表）+ `_classification_with_scope`（scope 合并；模块补充**只由 scope 驱动**——名字出现≠改角色设定，回归语义保持）

## 10. 运行真相与 LLM 话术分离（Layer A/B）

- `running` 为**派生态**：`sending || cs.isActive || cs.executionActive`——LLM 话术永不进入判定
- §12 运行卡（AgentConversationSurface）：阶段标签映射自持久化 `ChangeSession.status` / `ChangeExecution.stage`；真实计数仅在有确定分母时显示（角色参考图 N/M、制作计划 N/M 来自 execution manifest）；引用真实 session id；blocker 显示真实失败边界
- 全部 Agent 系统提示词追加真话纪律后缀；`active_workflow_snapshot`（只读持久化状态快照）注入 Producer 上下文——"现在做到哪了"只能引用 Layer B
- Event C（§32）验证：Golden Path 全程 UI 状态均来自 durable 对象，未出现 LLM 幻觉"已开始/已完成"

## 11. 实现的澄清规则（及真实 Golden Path 暴露并修复的 4 个缺陷）

本轮**真实浏览器 + live Agnes** 验收暴露并修复（bd6f98a）：
1. **外观变更词缺口**：character 关键词不含"短发/装甲/造型"等——"把赤焰守卫改成银色短发、黑色战斗装甲"被误路由到 script（update_character 缺失 → K1 链断）。词表补强（18+ 外观词）。
2. **"换回"不是变更动词**："换回之前那个造型"落入普通聊天，永远到不了 ChangeSession。前后端词表补"换回/恢复到/恢复成"。
3. **character 提案丢弃 scope**：`run_character_proposal` 内部重新纯文本分类，丢失 entity_refs 的角色指向 → LLM 反问"哪个角色"（§9 违规）。已合并 entity_refs + 提示词注入选中角色。
4. **角色页无 context 接线**：characters 页选中角色不设置 Agent context。已接线（selectChar → setContext）。

已实现的规则：页面上下文自动解析指向（不反问 Which Scene/Character/Shot）；material 歧义 → 单个合并 decision request；变更请求的模块归属自动协调（不问用户选 Agent）；一次批准 → 下游生产自动推进（不再逐段确认）。

**探针结果**（§33，live）：
- Probe 1 ✓：scene scope 下"让这一段更紧张一点" → 方案自动作用于 s01（insert_shot s01 + update_shot s01_c01），零反问
- Probe 2 ✓（修复后）：选中赤焰守卫 →"把这个角色的盔甲换成黑色" → 直接 `update_character 赤焰守卫`，0 个 decision（修复前反问"哪个角色"）
- Probe 3 ✓（形式）：真歧义 → 单个合并 decision request（两个选项），会话安全暂停；**限制**：决策内容是"请提供描述"而非历史版本二选一——系统无创作历史版本存储（见 §26）
- Probe 4 ✓：Golden Path 主请求即跨模块（character+script+storyboard 自动咨询+Director round2），用户全程未指定任何 Agent

## 12. 无需用户确认自动继续的行为

项目内已授权边界内的全部低风险步骤：检视项目/历史、路由、咨询 Specialist、依赖分析、方案编译/校验/预览、影响计算、恢复持久化状态、轮询 Job、**批准后的完整下游生产链**（char-sync → planning → 受影响 shot 帧视频 → compose——Event B 后端重启后**自动 kickoff 续跑**，无需用户重新确认）。

## 13. 仍需用户输入的情形

§8 的 5 类：实质创作歧义（Probe 3）、必需信息不可推导、Specialist 冲突、超出已批准范围的后果性动作、外部阻塞（凭证/服务不可用）。以及：方案批准（一次有意义的批准）、活跃执行 + 新变更的冲突选择（§15 二选一）。

## 14. ChangeSession → ChangeExecution UI 状态映射

提案阶段（`ChangeSession.status`）：routing→理解你的要求；consulting→协调相关专业 Agent；analyzing_dependencies→分析影响；synthesizing_plan→整理修改方案；validating_plan→校验方案；building_preview→准备预览；ready_for_review→等待你确认；awaiting_user_decision→需要你确认。
执行阶段（`ChangeExecution.stage`）：revalidate→重验批准；workspace_create→建执行工作区；creative_replay→暂存创作变更；workspace_verify→校验预览一致；canonical_commit→原子提交创作变更；character_sync→同步角色参考图；production_planning→准备制作计划；production_prepare→冻结制作输入；production→生成受影响素材；compose→合成成片；done→完成。文本仅是展示，后端状态是真相。

## 15. 重启行为（Event B，真实）

Golden Path 中在 char-sync 进行中（front 完成、side 生成中）硬杀后端 → 重启：
- JobQueue `_load` 将 running 的 character_views job 重置 queued 并 re-route，**同一 job_id 复用**（2053e7c0e7fe，attempts 1→2，无重复提交）
- `recover_executions` 自动 kickoff post-commit 活跃执行（无需用户 resume）
- 续跑完成 char-sync → planning → targeted production run → compose → final.mp4
- 零 `PROJECT_CHANGED_AFTER_APPROVAL` 误报（F-1）、无第二 execution
- 已知语义：单 char 三视图写回是 all-or-nothing per char——mid-flight 重启会重做该 char 的 3 张（继承自 AI-7C-S 的已知限制，mock soak 已证明 per-char 幂等）

## 16. F5 行为（Event A，真实）

production 运行中整页刷新：项目身份恢复（K3）→ ChangeSession/Execution 真相从后端重建 → 轮询自动续接 → 运行卡/timeline 显示 durable 阶段。后端重启场景下前端轮询自动重连（executions 请求恢复 200）。

## 17. 新增确定性测试（tests/test_ai_7ch_h4.py，10 项）

- F-1：views 写回签名不变 + 外部真突变仍变
- K1：appearance_sig 确定性/敏感性；handler 矩阵（resume 同 sig 跳过 0 生成 / 新 sig 全重生成 3 次+版本化文件+views_meta / 无 meta+sig 重生成 / legacy 无 sig 保持 done-skip）
- F-2：per-scene checkpoint（s01 planner 恰 1 次 + s02 续跑 + targeted run）
- Event B：recover 只 kickoff post-commit 活跃执行（blocked/终态/pre-commit 不 kickoff）；in-flight guard 防双派发
- §17 双角色 soak：A 完成、B 进行中硬停 → 重启 → **A 不重生成（phase B 恰 3 次全属 B）**、B 完成、无 stale 误报、commit 保持、无第二 execution

## 18-20. HTTP 集成 / 浏览器 / live Agnes 结果

- **确定性+集成**：全量 **509 passed**（含全部 AI-7A/7B/7C/7C-S 回归）
- **浏览器 E2E**（真实 vite 前端 + 真实 uvicorn 后端 + Playwright 类自动化）：项目选择→Floating Bar 自然语言→运行卡 durable 阶段流转→方案审查（批准此方案）→执行计划→timeline→F5→后端重启→完成
- **live Agnes**（§34-D 最小集）：seed 三视图 3 次图像生成；Golden Path 真实重生成 3 次新三视图；s01_c01 新 first_frame batch（新参考消费）+ 新视频生成 + compose 出 **final.mp4（2.19MB，2026-08-23 02:16）**

## 21. Golden Path 精确轨迹（项目 AGNES_GoldenPath_H）

用户消息（Floating Bar，零内部 ID）："把赤焰守卫改成银色短发、黑色战斗装甲，并把它第一次进入机库的那一段做得更有压迫感。其他剧情和人物不要动。"
→ 意图路由（CHANGE_INTENT_RE）→ durable ChangeSession `cs_53547262df7f`（source=floating_bar）→ 运行卡 durable 阶段流转（协调→分析→整理→校验→等待你确认）→ 方案 4 ops（update_story_text + update_scene s01 + **update_character 赤焰守卫** + update_shot s01_c01；s02/角色名列入"保持不变"）→ 一次批准 → 执行计划（affected_production）→ 原子提交 → **Event B：char-sync 中（front done、side 生成中）硬杀后端 → 重启自动续跑** → 新三视图（f326d472）→ planner（真 LLM）→ targeted run `prun_e739cf8b0d`（仅 s01_c01）→ **Event A：F5** → 新 first_frame（char_ref=新 front url；prompt 含 "silver-haired...black combat armor"）→ 新视频（vv_e26fa515）→ compose → **final.mp4**
注：首次尝试的方案缺 update_character（分类词表缺陷），经"请求修改"反馈 + 词表修复后新会话产出完整方案——该缺陷修复已提交（bd6f98a）。

## 22. final.mp4 证据

`projects/AGNES_GoldenPath_H/output/final.mp4`（2,193,293 字节，2026-08-23 02:16:38）。s01_c01.mp4 重生成（02:16:23）；s02_c01.mp4 保持 seed 旧视频（20:52:59）——**无扩大化重生成**。

## 23-24. 回归 / typecheck / build

- pytest 全量：**509 passed**（预存在的 2 个 flaky 已查明为环境噪音：主目录 cache/jobs.json 积累 3320 条遗留 job 使 JobQueue 全量序列化变慢 5 倍，触发预存在的 `update_shot` 读-改-写竞态窗口；备份至 `cache/jobs.json.bak-7ch` 后清理，3 轮全过。**同一代码 + 同一用户数据在干净 worktree 全过**，证明非 cd17535 回归）
- typecheck：仅 6 个预存在错误（与 AI-7C-S 基线逐条一致，见 typecheck_audit.log）；本阶段零新增
- vite build：通过（36s）
- git worktree 基线对照验证已执行、worktree 已清理

## 25. 提交清单

见 §2（cd17535 / 636c7ae / 8d3e24c / c10bcea / bd6f98a）。

## 26. 未解决限制（诚实清单）

1. **创作历史版本不可知**：canonical 不存 description 的历史版本——"换回之前那个造型"只能问用户要描述，无法列出候选版本（需要新的历史存储，超出本阶段）
2. **char-sync 单角色 all-or-nothing**：mid-flight 崩溃重做该角色全部 3 view（per-view 持久化留待后续）
3. **中文名角色目录经 `_safe_filename` 落在 `_` 目录**（产品既有行为）：K1 版本化文件在 `characters/_/` 下，历史视图目录可能分裂（本 Golden Path 的旧 seed 文件在原名目录）
4. **UI 遮挡（小视口）**：viewport 较小时 Floating Agent Bar 遮挡内容底部按钮（"请求修改"/"Director 对话"坐标点击不生效，程序化点击正常）——真实用户可遇到
5. **变更请求无 scope 且无文本指向时**（如脱离页面上下文发"让这一段更紧张一点"）：director 无 scene_refs → 0 提案 → blocked("无 completed 提案可编译")，错误信息不友好（应给出"请选择场景或指明故事段"类引导）
6. **前端 blocked 会话被视为 alive**：新变更请求会 requestChanges 并入 blocked 会话而非开新会话（本次探针期间用 reject 规避）
7. **jobs.json 无限增长 + 每次全量序列化**（运维债，预存在；已备份并清理一次）
8. **update_shot 读-改-写非原子**（load 内 ffprobe 窗口内可被并发写覆盖——预存在架构弱点，测试环境噪音的根源）
9. 安全（§39）：`start.py` 仍含明文 AGNES/MIMO key（与旧历史暴露值相同）——**建议轮换**；未自动改写历史

## 27. 结论

**AI-7C-H：PASS**（§41 全部 PASS 条件满足；§26 限制均不属本阶段范围或已有记录与证明）。

按 §42 STOP：未开始 AI-7D，未实现 Reviewer Agent，未扩展无关改进。后端（127.0.0.1:8000）与前端（localhost:7779）保持运行中，供手动验收；Golden Path 项目 `AGNES_GoldenPath_H` 与全部证据文件保留。
