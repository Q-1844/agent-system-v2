# daycarry12m 第五道闸：显式风险授权、收尾和原任务接续

## 事故与修复边界

先读 C 仓 `docs/HANDOFF_day_continuation_20261031.md`，并以 C issues [#1](https://github.com/Q-1844/agent-system-v2-archive/issues/1)、[#2](https://github.com/Q-1844/agent-system-v2-archive/issues/2) 和最新源码为准。PR #41–43 已处理 pausing 恢复白名单和 FullProject.resume 的暂停标志；本次保留这些修复。

本次核验的原任务是 `daycarry12m-20261101`，FullRun `frun_372385c158`，源 Actions run `41331753136`，source SHA `4c9221c773a1b1099fef9c8f098b5a9f3452d2be`。它已有 **63 个 ACCEPTED、498 秒**；第 64 个提交 `s05_c03` 为 8 秒，Job `6427f45421e3`、native intent `69b137b3f60649289d2752a076666f61`，running/SUBMITTING 且无供应商任务号。**无任务号不能证明 POST 没有到达供应商。**

下载并核验的 artifact：ID `13093985653`，399751950 bytes，SHA256 `4c12f13f1b334120423d864d101c03daba1d459b04f4bbff06c39ce3152bf4d7`。原检查点 `STAGE0_BOUND/paused`，是先前失败恢复提前重绑留下的状态。

## 本次行为

1. **先排水。** driver 在 FullRun 普通 paused 时也停止新提交，向仍运行的 production 子级传暂停信号，等待在途 POST/轮询/落盘。已经 paused 的 FullRun 保持 stopped，不重置成没有 loop 可收尾的 PAUSING。排水期限到仍有未知回执，明确保留 INTERRUPTED，不宣称 drain_complete。
2. **先对账、再消费授权。** stopped snapshot 中能找到任务号时，补回 ACCEPTED，继续原任务轮询；明确 not_sent/rejected 且没有相反证据时关闭拒收意图，不占视频预算。任务号、artifact、镜头归属/输入/选择冲突不能靠授权绕过。
3. **UNKNOWN 默认继续阻断。** workflow 新增 `confirm_uncertain_submissions`（boolean，默认 false）；显式 true 必须同时提供 resume_run_id。用户已接受本任务可能重复扣费后，恢复校验生成绑定 nonce/request digest 的持久策略，记录操作者、授权 Actions run、时间、源 run/SHA/artifact hash、原 intent 和 Job。
4. **授权不是免费拒收。** 旧记录改为 `RESUBMIT_AUTHORIZED`，`provider_outcome=UNKNOWN`、`possible_charge_seconds=8`；原意图保留在历史和财务账本，8 秒仍计入累计授权预算。确认任务数/影片验收只统计真正 ACCEPTED 的回执。新提交记录 `resubmission_of`，单次授权在 WAL 写入前预留；明确未发送/拒收才释放预留，新的 UNKNOWN 不得在本进程盲目重发。
5. **走原生 Job/handler。** 对账只为确切 native Job 写 `confirm_uncertain_resubmit`；队列归档旧 dispatch 和 intent 后编译新请求。VideoVersion 保留 `previous_submit_uncertain=True`。已完成镜头、已选版本和文件不被重新制作。
6. **跨 hop 一致。** 通过全部 snapshot 校验后，将新 ledger/policy 和 RESTORED owner 一次 CAS 保存。恢复失败保持旧 phase/owner，防止巡检失去接棒对象。已有 STAGE0_BOUND 仅在真实 FullRun、非空已付费记录、paused/quota_exhausted 终态等条件同时满足时允许巡检接棒。
7. **后续 hop 继承同一 nonce 的策略。** 无需每天再次手工放行；默认 false 表示“不新增授权”，不会撤销已经落账的本任务策略。其它任务没有该策略仍 fail-loud。readonly monitor、后续恢复、局部修订与最终合成都识别有审计记录的历史未知成本，不能把它当作视频文件或真实回执。

供应商只支持按已知任务号查询的路径继续复用；本次没有声称能凭时间/提示词查询无任务号请求，也没有伪造供应商回执。

## 提交前测试

- 新增 `tests/test_uncertain_submission_recovery.py`：34 项。覆盖默认阻断、显式授权、跨 hop 策略、正向回执优先、已拒收/未发送、重复或篡改回执、输入变化/取消、真实 JobQueue + 原生 handler 一次重提、旧片 hash 不变、普通暂停时 POST 排水、暂停子级、后续技术错误和巡检。
- 关联回归 263 项通过（新增 monitor 用例后由全量回归再次覆盖）。
- 原 399.75 MB artifact 离线重放：未授权恢复在复制项目之前拒绝；授权后 63 个视频的任务号、版本号、文件 SHA256 全部不变；保留 498 秒已确认 + 8 秒未知成本，仅授权原 Job。原检查点未修改，无付费 API 请求。
- 全量回归结果和真实接续 run 在下方补录；真实跨日最终验收仍需 GLM 持续执行。

## GLM 真实验收顺序

1. 读取 B1 `test/e2e-daycarry12m-20261101` 分支 `e2e/daycarry12m-20261101/full-project-production.json`，确认最新 owner 对应 Actions 已结束且没有同 nonce 活跃 run。使用最新 owner，不能固定使用已过期 hop。
2. 首次从 `41331753136` 续跳时，dispatch A 的 `video-autoproduction-e2e.yml`，ref 为合并后的 `chore/migration-readiness`，参数：

   ```json
   {
     "acceptance_scope": "full-project-3min",
     "target_duration_seconds": "auto",
     "task_nonce": "daycarry12m-20261101",
     "resume_run_id": "41331753136",
     "media_inflight": "2",
     "driver_timeout_minutes": "330",
     "confirm_uncertain_submissions": "true",
     "finalize_only": "false"
   }
   ```

   这是原片约十二分钟目标，已经超过 500 秒要求；不要新建 nonce、改 frozen 创作要求或拷贝旧项目覆盖进度。
3. 观察 RESTORED：新 owner/source SHA 正确，原 63 个 ACCEPTED 保持顺序和 ID，原 8 秒成为带授权的 UNKNOWN 成本，新 native Job 提交生成新 intent。新 ACCEPTED 超过 63，不能只看到 RESTORED 就宣布通过。
4. 观察实际生产；按 C 记录约每 30 分钟查看一次即可。任何一次人工连续真实验证不超过 50 分钟，不需整夜轮询。若暂停，先检查明确错误、drain_complete、原生 jobs/收据，不能只靠 Actions 成败。
5. 额度用尽时应有 WAITING_QUOTA / quota_exhausted、有效 quota_resume_after、拒收历史和 drain_complete=true；当天不能不断 POST 或耗尽内容重试次数。过闸后巡检应从最新 artifact 自动接棒，同 nonce、相同 FullRun 和已完成作品保留，发起新的有效请求。
6. GitHub cron 可能延迟。过闸后无新 owner，先手动 dispatch `video-patrol.yml`（默认分支）；读 patrol 派发理由。需要手动驱动兜底时使用当时最新 resume_run_id 和同 nonce/auto。持久授权已在 B1，无需再次 true。
7. 终验检查完整成片、所有镜头选择、本地 SHA、ffprobe 时长和全解码、B/B1 归档 registry、full restorable_level。RESUBMIT_AUTHORIZED 的 8 秒仅为未知可能成本，不可计入真实影片时长。
