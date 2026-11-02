# daycarry20m 跨日唤醒缺口与完成事件定时接续

## 真实故障

任务 `daycarry20m-20261102`，原 run `41379443191`，FullRun `frun_6dec578ae4`。本轮检查时：

- B1 WAITING_QUOTA / quota_exhausted，drain_complete=true；34 条 ACCEPTED、278 秒，未决收据为空。
- quota_resume_after=`2026-11-02T16:10:00Z`；GitHub 服务端本次检查时间 `2026-11-02T17:31:45Z`，已过闸 81 分钟，仍无下一跳。
- 最后一个 cron patrol 为 `41425178958`，在 `10:00Z` 运行。日志显示当时正确判断 gate closed；之后没有新巡检。RESUME_DISPATCH_TOKEN 已配置，本次不是 token 缺失。
- 所以额度识别、停止、意图账本和保存均正常；无人值守唤醒仍然只依赖不保证频率的 GitHub schedule，设计上留下空档。

原 artifact ID 13116077738，194540377 bytes，SHA256 `646f7168f3a0a32769a53d2ffd61e111932ab2100e7a69bd59c51a94407b5014` 已下载验证。离线恢复成功，34 段素材 / 278 秒复用；原版本和 SHA 基线在同目录 evidence 文件中。没有使用离线测试调用付费接口。

## 新机制

`video-quota-wakeup.yml` 同时接受 production workflow 的 `workflow_run: completed` 事件和显式 dispatch。

1. resolve 读取已结束生产 run 的仓库、workflow path、source SHA，并从 B1 找到属于该 run 的可恢复任务。已 COMPLETE、已换 owner、不可恢复或无有效 gate 的任务不登记。
2. wake job 进入 `producer-quota-wakeup` 环境，等待规则为 **20 分钟**。等待发生在 GitHub 环境保护阶段，不启动 runner 来 sleep，也不消耗付费视频请求。
3. 到点重新读权威 B1。闸门未开就再登记一个 20 分钟票据；闸门已开则复用原有 patrol 的 liveness、宽限、近期派发去重和显式恢复入口。
4. 生产接棒保留同 nonce、原 run/artifact 来源、auto 档位和冻结并行策略；恢复仍由既有原生 Job/收据/版本/预算校验控制。定时检查与镜头 attempts、制作 hop 分开，不重置历史。
5. B1 `quota_wakeup` 记录 ARMED/FIRED、来源 run、唤醒 run 和时间。写回只用 CAS；已换 owner 不覆盖，审计 CAS 失败不能成为重复派发的理由。
6. GitHub cron patrol 保留兜底。短控制任务或 GitHub 平台仍可能失败/排队，不能承诺某一分钟准点；不再以 cron 为唯一唤醒入口。

为防 fork 自动创建无保护规则的环境而形成快速自派发循环，wake 还核验真实 alarm run 的仓库、workflow path 和最小经过时间，未观察到等待则 fail closed。`smoke_test=true` 只使用 1 分钟测试环境；仍检查供应商 quota gate，任何再次登记一律转回标准 20 分钟环境。

## GitHub 配置和凭据

本仓已通过 Environments API 创建：

- `producer-quota-wakeup`：wait_timer=20，无人工 reviewer。
- `producer-quota-wakeup-smoke`：wait_timer=1，无人工 reviewer，仅验证。

新仓/迁移时必须用仓库管理权限配置上述保护规则，仅创建同名 environment 不够。wait timer 是 GitHub 的持久环境配置，代码中的延迟检查可发现缺失规则。

新控制 workflow 只需要 repo 的 `GITHUB_TOKEN`（contents read / actions write）和 B1_ARCHIVE_TOKEN，不加载 Agnes/图床凭据。原生产和巡检优先使用 RESUME_DISPATCH_TOKEN，缺失时支持原生 GITHUB_TOKEN。

官方资料已核对：

- [Triggering a workflow](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-when-your-workflow-runs/triggering-a-workflow)：workflow_dispatch/repository_dispatch 是 GITHUB_TOKEN 防递归限制的例外。此前代码中声称 GITHUB_TOKEN 一概不能派发的注释不准确。
- [Deployments and environments](https://docs.github.com/en/actions/reference/deployments-and-environments)：环境等待计时不计为 billable runner time；公开仓支持该规则。

## 验证与接棒

本地新增测试覆盖事件识别、源身份/SHA、quota 跨天、关闭时仅再登记定时器、到点走实际 patrol、原回执不变、源已更换、近期派发去重、网络/上传尚未稳定的延迟检查、CAS 竞争/回执丢失、计时规则缺失及原生 token fallback。

本地全量回归 **3869 passed，0 skipped / failed**，449.85 秒，P55 fixture SHA256 已核验；23 项既有弃用 warning。新增 32 项控制测试，关联回归 171 项通过。全量后仅补齐无凭据测试对 GITHUB_TOKEN 的隔离，并用注入假令牌的测试环境复验。

本次遗留任务在新 observer 部署前已经停止，GitHub 不会重放过去的 completed 事件，因此通过一次 smoke dispatch 登记现有任务：

```json
{
  "ref": "chore/migration-readiness",
  "inputs": {
    "task_nonce": "daycarry20m-20261102",
    "source_run_id": "41379443191",
    "timer_hops": "0",
    "smoke_test": "true"
  }
}
```

这是迁移后的一次唤醒验证；后续 producer 结束事件会自行登记。不能将这次手工登记冒充原先已经全自动接续。

GLM 后续先查 B1 最新 owner 和 `quota_wakeup` 的 alarm run；正常的 environment waiting 不等于失败。过 gate 后检查新恢复 run 的 RESTORED/RUNNING，原 34 个 accepted ID、版本和 SHA 保留，以及新增有效回执。然后继续观察下一次额度等待时 completed 事件是否自动登记标准环境票据。长期制作和最终成片仍按原 C 文档验收，不需每分钟盯守，也不让 Codex 亲自持续验证超过 50 分钟。
