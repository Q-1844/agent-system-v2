# HANDOFF: 同镜同输入重复付费悬案——生产期活闸 + 恢复期自动作废（2026-11-14）

## 一句话

同镜头同输入的已受理提交对两道防重闸（`find_by_origin` 与 pre-POST pending 闸）都隐形，
瞬态轮询死亡会触发 autopilot 阶段重试造成**第二次付费 POST**；而该悬案在恢复期即使
同镜已有可用收据也一律要求人工授权，无人值守链整夜卡死。本次三层修复终结这两个洞。

## 实锤证据链（anime750s-20261113a run 329，2026-11-13）

1. **06:04:16Z** job `5eed1f32`（stage_key `...s02_c11:video:1`）POST → 受理
   `task_hpGEVkjZ`（账本 ordinal 20 = ACCEPTED，已付费 8s）
2. **06:05:15Z** 供应商 401「无效的令牌」瞬断 → job `:1` 轮询死亡（state=failed），
   **视频在供应商侧存活但无人认领**
3. **06:05:22Z** autopilot 阶段重试 → job `9a7c3717`（stage_key `...:video:2`）
   —— `:N` 递增使 `find_by_origin` 复用失明；pre-POST pending 闸只查
   SUBMITTING/UNKNOWN，**对 ACCEPTED 隐形** → 同镜同输入**第二次付费 POST**
4. 第二笔回执被同一瞬态打断 → 账本 ordinal 21 = UNKNOWN（无收据）
5. 06:21:42Z 第 24 序号镜头撞 429 日限（干净拒绝）→ run 329 墙停
6. 11-14 00:13/01:38/06:53 wakeup 三次自动接续（run 330/331/332）全部被
   UNKNOWN 纪律 fail-loud 拦下（1 分钟内死，`confirm_uncertain_submissions`
   缺失），片子卡死 19 小时
7. 09:40 操作员授权（run 333）→ 恢复期 `reconcile_accepted_work` **收养了
   ordinal 20 的原视频、零重拍**（证明恢复期收养是好的，缺的是生产期闸）

## 修复三层（commit a693f56）

### 1. 生产期活闸（tools/full_project_cloud_acceptance.py `create_video`）

同镜头存在【同 immutable_input_digest、state=ACCEPTED、有 video_id、对应 intent
未 FAILED】的旧尝试 → 新 POST 直接 `VideoSubmissionNotSent` fail-loud
（`accepted-but-unclaimed prior submission for <shot> (task <id>); adopt/reconcile
before any new POST`）。收养走恢复期 `reconcile_accepted_work`。

**放行面**（不要当成冗余删掉）：digest 不同（修订重拍）放行；兄弟 intent 已
FAILED（供应商判死，`poll_video` 的 `VideoGenerationFailed` 路径标记）放行——
否则镜头会永久卡死无法重拍。

### 2. 恢复期自动作废（tools/submission_reconciliation.py）

UNKNOWN 悬案若同镜已有【带收据的 ACCEPTED】→ 自动 `SUPERSEDED`：保留意图与
`possible_charge_seconds` 成本记录，不授权任何重发。**拒绝花钱永远是安全默认，
不属于操作员决策面**——run 330/331/332 那种整夜等人工的形态由此终结。

### 3. 状态机（src/core/submission_recovery.py + src/core/provider_errors.py）

- `SUPERSEDED` 常量 + `superseded_attempt()` 谓词（须有
  `superseded_by_attempt_id` + `possible_charge_seconds`）
- `validate_attempts`：SUPERSEDED 必须对上同镜已确认 ACCEPTED 兄弟，悬空即拒
- `validate_intent_history`：SUPERSEDED 意图与账本尝试成对校验、不得私带收据
- 对账器 kept 列表加入 SUPERSEDED（二次对账幂等）

## 防回退锁（测试红了先怀疑自己改坏了语义，再怀疑测试）

`tests/test_uncertain_submission_recovery.py` 尾部四个用例：
- `test_unknown_with_sibling_accepted_receipt_auto_supersedes_without_operator`
  （自动作废 + 幂等）
- `test_superseded_without_confirmed_sibling_is_reconciliation_debt`（悬空拒绝）
- `test_accepted_but_unclaimed_same_input_blocks_new_paid_post`（活闸拦截）
- `test_live_gate_allows_different_input_and_provably_dead_videos`
  （修订/判死放行——删掉这条测试去「修复」拦截过宽，必先读上面放行面说明）

CI：mock-regression 223 + pipeline-smoke + runtime-regression 全绿（a693f56）。

## 后续注意

- 修复对 run 333（进行中）不生效（其 runner 已 checkout f87ceff），下一跳接续起效
- 若未来出现「活闸拦截 → 阶段重试耗尽 → run 失败」的形态：这是**设计行为**——
  恢复期收养兜底（run 333 已实证），不要把活闸改回静默放行
- `possible_unconfirmed_seconds` 只统计 RESUBMIT_AUTHORIZED；SUPERSEDED 的可能
  成本在尝试级 `possible_charge_seconds` 留痕（不进聚合授权风险口径）
