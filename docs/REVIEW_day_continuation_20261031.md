# 跨日自主制片代码审查、修复与真实验收交接

本次审查以 A 仓 `chore/migration-readiness` 的 `7f9db2b1d933719ed838d303ac5d064f7f709bff`（已合并 PR #36）为基线。首先阅读了 C 仓 `docs/HANDOFF_day_continuation_20261031.md`，随后核查最新源码、原始 Actions artifact 与 B1 检查点。没有启动任何真实视频生产，没有修改运行中 r46 的 B/B1/D 数据。

## 1. 事故证据与独立结论

- nonce：`film6m-r46-20261030`；原 FullRun：`frun_c92bef6d14`。
- hop7：A 仓 run `41160317704`；artifact `13026678246`；ZIP 163,818,793 字节；SHA256 `0e2cf511f1428381698acc322da7d6ec0c36cff1feee23b0e670130d9d98e0ac`，下载时已核对 GitHub artifact digest。
- 原始 `jobs.json`：35 个完成的视频 Job、19 个完成的首帧 Job、17 个完成的尾帧 Job，以及 2 个失败的视频 Job。
- 两个失败 Job 都属于 `s04_c03`：`2386a5d61158`、`70dc5e158d36`；都有 `submission_intent_id`，都明确记录 `provider_submit_state=rejected`，都没有 `provider_task_id`，Job attempts 都是 **1**。
- 最新只读 B1 观察已经指向 hop8 `41163103629`，仍保存 35 个 ACCEPTED 回执、307 秒；`quota_resume_after` 为空。
- hop8 日志明确出现两次 `video retry blocked pending provider reconciliation ... reason=paid_submission_evidence`，之后重放前一天的错误。

**真正未闭合的闸门是 `JobQueue.retry()`。** PR #36 只允许 Scene 执行器尝试重排，真实队列仍把“登记过意图”当成已受理证据。原测试替换了 `retry()`，因此无法发现这个断点。

错误中的“重试 5 次”来自 `AgnesClient._request()` 的单次 HTTP 重试循环，并不是持久化的 Job attempts=5。审查没有发现客户端跨进程缓存五次重试后直接抛旧异常的机制；旧错误重放发生在失败 Job 被复用且没有真正重新入队时。

## 2. 实施的修复

| 环节 | 原问题 | 修复后的行为 |
|---|---|---|
| HTTP 分类 | 每日额度拒收也连续请求五次；普通 `Too Many Requests` 被当成每日额度 | `DailyQuotaExhausted` 单次拒收即返回；其它 worker 在取得限流槽后复检额度，避免迟到 POST；普通 RPM 429 有界退避，解析秒数及 HTTP-date 的 Retry-After；余额/字符数限制不误判每日额度 |
| 配额时间 | 只凭当前日期猜下一次刷新 | 优先解析供应商错误中实际刷新日期，加十分钟余量；没有日期时使用北京 00:10；记录 UTC 闸门 |
| 原生队列 | 意图字段永久阻止明确拒收重提 | 有明确 `rejected/not_sent` 事实才允许重排；已受理/unknown/submitting/游离回执优先阻止新 POST；旧意图及错误归档到 `submission_history` |
| 本地预检 | 本地配额提前拦截绕过 B1 账本，无法进入跨日等待；帧数换算多出小数误拦最后几秒 | 预检直接记录未发出与额度等待；按与实际提交一致的整秒数校验额度 |
| 提交账本 | 拒收意图直接删除，无法审计完整生命周期 | 新增 `submission_intents`，使用唯一 intent_id、递增 ordinal，保留拒收/未发出历史；`video_attempts` 继续保存累计付费事实，兼容旧检查点 |
| 已受理后的落账失败 | 后续存储 429 可能被误当供应商拒收 | 已知 ACCEPTED 优先，绝不因后续 CAS 错误删除付费回执；冻结失去确认的写入方 |
| 轮询 | 429/5xx 耗尽后可能将已受理视频标成永久失败 | 保留任务号，标技术暂歇；原 Job 续轮询；不创建另一 stage key 争夺同一回执 |
| 已完成原 Job 重放 | 镜头已完成，但原 Job 重排仍可能再次 POST | 按原任务号确认已有选版和文件后幂等结束；不重提、不覆盖选版 |
| 供应商最终失败 | 已扣费但没有成片，回执一直像未收货 | 明确终态使用 `VideoGenerationFailed`，意图 FAILED；付费记录保留成本并记 `provider_outcome=FAILED`，不要求不存在的成片文件 |
| 重试预算 | 额度/技术等待与内容失败共用局部失败路径 | 额度不消耗视频内容重试；技术计数独立记 `video_technical`；旧 Job/Shot attempts 不作为跨日熔断上限 |
| Producer 修订 | 基础设施故障可能进入 C2 剧情修订 | 额度等待、轮询暂歇、短限流、未发出、提交不明不触发剧情修订 |
| 检查点 | 额度暂停仍像普通失败；直接声称排水完成 | 显式 `WAITING_QUOTA`，带日历闸门；实际检查各层控制器、Job 和已受理素材后才标 `drain_complete` |
| 跨 hop 初始化 | 恢复/绑定账本前启动 worker，或重复 startup 后更换队列对象 | 依赖初始化可延迟生产恢复；先恢复文件并载入队列/配额，绑定 CAS 客户端，再启动 worker 和续轮询 |
| 已受理未落镜头回执 | 有 B1 回执却尚未写入镜头，恢复要求所有视频已经下载 | 用 ACCEPTED 回执、唯一原意图、输入签名和选版校验修复任务号；验证原签名后重绑新 runner 的路径签名并保留原证明；只重新排入原 Job 续轮询 |
| 巡检与证据 | 只扫描 B1 前 100 个分支；A 派发缺少冷却时间；C1 自定义项目名未必进入 artifact | 分页扫描全部分支；成功派发记录时间；上传实际项目目录；artifact 保留期增至 30 天 |
| 巡检观测 | 新等待相位被报未知/普通失败 | `watch_full_project.py` 返回 `WAITING_QUOTA` / `QUOTA_READY`，退出码 6 表示等待/待巡检接棒 |

`src/core/provider_errors.py` 是客户端、队列、Scene 执行器和 tools 共用的标准库策略模块，避免继续维护多套不同的额度关键词和安全重排规则。

## 3. 状态与恢复约束

意图状态链：`SUBMITTING → ACCEPTED → COMPLETED / FAILED`；明确未受理走 `REJECTED / NOT_SENT`；受理结果无法证明走 `UNKNOWN`。明确拒收退出付费列表但保留历史；供应商最终失败仍然保留已消费秒数。下一次安全重提使用新 intent_id，仍沿用原 nonce、项目、FullRun、SceneRun 和阶段归属。

以下事实的优先级高于错误字符串：已知供应商任务号、游离回执、unknown/submitting/accepted。不能因为错误里出现了 429 或没有任务号就自动重发。

额度等待必须有合法 `quota_resume_after`。工作流显式恢复入口、原生 Job 重试、HTTP handler 入口和巡检均尊重等待时间。已过期的额度字段在验证并恢复新 hop 后清除。

旧版检查点无需清空重做：旧 ACCEPTED 列表继续有效；历史 429 拒收通过真实错误形态迁移；已有完成视频和选版不重建。

**保留的必要边界：** POST 结果确实未知且没有任何可靠任务号时，当前已核实的 Agnes 契约没有提供可验证的客户端幂等/按意图查单能力。本修复保留 UNKNOWN 并停止该意图自动重发；不声称能无条件消除所有人工对账。CAS 写入确认丢失也不能通过刷新预期 SHA 强行接管权威账本。

## 4. 离线验证证据

运行环境：Python 3.12，按 `requirements-ci.txt` 安装依赖，系统 ffmpeg/ffprobe。测试均使用本地 fake 或 httpx MockTransport；没有发起真实 Agnes 生成。

- 新增 `tests/test_day_continuation.py`：真实 JobQueue + 原生视频 handler + Scene `_do_video` 的两天恢复链；每日 429 单次请求；短限流重试；GET 429/503/504 续轮询；供应商回执落镜头前崩溃；已完成 Job 幂等；本地配额等待；剩余整秒额度；拒收/未知/已接受优先级；存储 429 不回滚付费；CAS 快照不可变；启动顺序；跨分支分页；命名项目 artifact；巡检观测；基础设施不改剧情。
- 原始 r46 hop7 `jobs.json` 的独立离线回放：两个失败 Job 经真实 `JobQueue.retry()` 均进入 queued；旧意图进入历史、旧错误清除；35 个 done 视频 Job 内容保持不变；生成请求数为 **0**。
- 定向关联回归覆盖 Stage0、Scene/Project/FullRun、C2 修订、按天巡检、下载与 artifact 恢复、CAS 提交闸门和归档前排水。
- 最终全量结果见下方验证记录。

复现命令：

```bash
python -m pytest -q tests/test_day_continuation.py
python -m pytest -q
```

## 5. GLM：先完成 r46

1. 采用本次修复提交。若用工作流验证分支，应给 `gh workflow run` 传该分支；无人值守巡检使用 A 仓默认分支，因此最终跨日验收前必须使默认分支包含修复。
2. 先只读 B1 的 `test/e2e-film6m-r46-20261030` 分支检查点，核对仍是同一个 nonce / request_sha256 / FullRun，保存 35 个已受理视频 ID、版本 ID、文件哈希和 307 秒基线。**使用那一刻最新的 `github_run_id`**。本次审查观察到的是 `41163103629`，不要再硬编码 hop7。
3. 保留 `target_duration_seconds=auto`、`task_nonce=film6m-r46-20261030`、`resume_run_id=<最新权威 run>`、`media_inflight=2`；不要创建新 nonce，不要用 `gh run rerun`。
4. 观察 `s04_c03` 原 Job 真正重新进入 worker，出现新的供应商请求/任务号；旧拒收意图归档。已完成 35 镜不再 POST，原哈希、版本和选版不变。旧两个 stage key 可能留下未被再次执行的历史失败 Job，这不能被误算为新的内容失败。
5. 如果当天仍无额度，应一次明确拒收即进入 WAITING_QUOTA，并完成在途排水；不能连续五次请求、立刻自派发或改写剧情。
6. 补拍完成后检查合成清单、最终移动视频时长和全量解码；确认 B/B1 归档 `VERIFIED_RESTORABLE`、`restorable_level=full`，实际恢复归档后再核对源视频及成片。

示例（以实际修复分支/默认分支和最新检查点为准）：

```bash
gh workflow run video-autoproduction-e2e.yml --repo Q-1844/agent-system-v2 \
  --ref <包含修复的分支> \
  -f acceptance_scope=full-project-3min \
  -f target_duration_seconds=auto \
  -f task_nonce=film6m-r46-20261030 \
  -f resume_run_id=<最新权威github_run_id> \
  -f media_inflight=2
```

## 6. GLM：真正无人值守的多日终验

- 新建一个只提交一次的 ≥10 分钟创作任务，目标超过一天可用视频秒数；保存冻结的创作要求、nonce、计划指纹、项目/FullRun/SceneRun 身份与并行档位。
- 让额度自然耗尽，不人工删除检查点/意图/Job，不修改账本伪造配额。每天额度恢复时间以供应商回执为准。
- 每次明确拒收后核查：WAITING_QUOTA + quota_exhausted + 合法 UTC 闸门；未受理请求不记付费；无 UNKNOWN 幽灵；额度等待不消耗内容 attempts；已有受理任务完成收货后才写 drain_complete。
- 闸门关闭期间，多次巡检不得派发；北京零点刷新并过余量后，由 `video-patrol` 自动派发同一 nonce 的下一跳。现有 cron 为每小时 7/27/47 分，不是报告中的固定 09:00；GitHub 排队可能延迟。
- 本次只读 API 已确认 `video-patrol` 为 active；PAT 对仓库 Secrets 清单返回 403，因此未声称已核验 Secrets。确认 Actions 允许计划工作流，`RESUME_DISPATCH_TOKEN`、`B1_ARCHIVE_TOKEN`、`FRAME_HOST_TOKEN`、`AGNES_API_KEY` 均有效；PAT 需有本仓 Actions 派发权限。没有这些真实运行条件，离线测试不能证明巡检已实际启动。
- 在一条已受理镜头尚未完成时，注入 GET 网络/429/5xx 或重启 runner：下一跳必须沿同一任务号续轮询；供应商 POST 总数不增加。若故意丢掉 POST 回执且无法证明任务号，应看到 UNKNOWN 隔离，禁止自动重复提交。
- 跨至少两次额度刷新、数次 hop，比较原已完成视频 ID/文件 SHA256/选版；检查 C2 局部修订只影响授权镜头，额度和技术错误不触发剧情修订。
- 全片完成必须同时满足：所有必需镜头完备、合成清单无重复补时、成片满足创作目标且全量可解码、归档完整可恢复。另由 GLM 做人物/动作/连续性/音画的实际质量检查，不能用单元测试替代观影验收。

观察当前等待与收片结果时，保留每跳 source_sha、artifact SHA256、请求 ID、intent_id、供应商任务号、版本 ID 和文件哈希。观察器退出码 6 是预期的额度等待信号，不能当普通制作失败。它仍是单 run 观察器；跨 hop 应读取 B1 新归属后为新 run 建立新的观测绑定，不能修改旧绑定冒充同一次运行。

## 最终验证记录

已完成串行关联回归：746 passed / 10 skipped；随后从 B 仓取得 36,116,480 字节 P55 真实归档，核对官方 SHA256 `f18a80b3a2b60b4ed762ef9aec70edb85f9124147dd8cdc1f7f38779dd2b75ff`，归档兼容及路径重绑回归 15 passed，覆盖先前缺样本跳过的用例。

新 runner 绝对路径改变后的受理任务签名回归及相关恢复检查：84 passed。最终完整回归（含上述真实归档样本）：**3751 passed，零 skipped，零 failed，432.39 秒**；新增跨日/故障注入测试 **36 项**。全部代码均为该次回归开始前的最终版本，随后仅更新本交接文档。测试有 23 条既有依赖弃用警告。未运行真实长片生产，真实多日闭环与观影验收仍由 GLM 执行。
