# 死亡恢复专项测试 Round 1——硬终止快照修复（2026-11-11 凌晨）

## 背景

real1000s（125 镜/1001.7s）完整收官、三对账通过后，按用户批准的专项测试计划，
于 00:48（北京）派发测试片 `killrecovery-20261111a`（200s 固定档/串行/全新 nonce），
目标：人为制造「账面干净 + native=producing」的硬死亡（run 315 同形态），
验证 11-09 修复（§四十七崩溃恢复）的活体链路。

## 意外时间线（全部为服务端硬证据）

| UTC | 事件 |
|---|---|
| 16:48:43 | run 321 派发（ref=chore/migration-readiness, d6e71cf） |
| 16:50-16:53 | 规划完成（16 文本 + 6 图片调用），进入 production |
| 16:53:48 | 第 1 镜视频提交（10s, keyframe 模式） |
| ~16:56 | 第 1 镜 ACCEPTED（检查点最终态实录） |
| **16:56:47** | **run 321 被 @Q-1844 身份取消**（job 注解原文 "The run was canceled by @Q-1844"） |
| 16:56:55 | wakeup 471 被 workflow_run 完成事件触发（20 分钟计时） |
| 17:17:25 | wakeup 471 自动派发恢复跳 run 322 → 40 秒内 FileNotFoundError |
| 17:47:38 | 我方手动派发 run 323 → 同样 FileNotFoundError（错误相同，双重实证） |

**取消者身份（已查明：本 agent 自己，2026-11-11 02:15 更正初版误判）**：
- 初版误判为"未知第三方"（排除法基于错误前提：以为取消时刻本地会话在
  sleep 540 中）。复盘铁证推翻：`/tmp/run321.log` mtime=00:57:28，距派发
  仅 8.75 分钟；期间 12+ 工具调用与 4 条 sleep(540/570/560/480) 命令不可能
  真实执行 36 分钟——**网关频死（502/504）导致持久会话反复回收、sleep 被截断，
  本 agent 主观时间线被压缩约 40 分钟**
- 真实时间线：00:48:43 派发 → 00:50-00:55 六次轮询（数据其实新鲜：视频生成期
  计数合法静止 + 1-3 分钟正常 API 延迟，被误读为"冻结挂死"）→ ~00:56:35 本
  agent 基于误诊发起取消（202=run 当时活着，完全吻合）→ 00:56:47 取消生效
- **教训（守候纪律强化）：长守候实际等待时长必须用系统时钟对表（date -u /
  Time 工具），主观累计不可信；kill 类动作前必须双证（job 日志时间戳 + 系统
  时钟），单一"冻结"读数不构成取消依据**

## 发现一（更正）：不存在 30-50 分钟 API 滞后

初版"API 视图滞后 30-50 分钟"结论错误——那是主观时间压缩的衍生产物。实际
API 延迟 1-3 分钟（正常范围）。活性诊断纪律仍然成立：**运行中活性以 job 日志
为准；且诊断前先对系统时钟，排除自身时间感知失真。**

## 发现二：硬终止快照没有 summary.json（本修复）

### 根因
`restore_snapshot` 硬读 `e2e_run/{nonce}/summary.json`。该文件只在驱动的
**异常退出路径**写出（GitHub 500 等进程内异常 → finally 落盘）；Actions 取消/
SIGKILL 直接终止进程，异常路径不执行 → 快照永远没有 summary.json → 恢复死锁。
（run 84/86 为同族历史案例，当时未修根因。）

### 修复（2766a8c）
- summary.json 缺失时以 checkpoint 为权威：身份字段（nonce/github_run_id/source_sha）
  与 checkpoint 校验冗余，缺失即跳过，只拒绝"写了但写错"
- 视频路径重定基不受影响：真实生产快照为**相对路径**（`projects/<pname>/...`，
  run 321 工件实证），走既有的 `relative_root` 默认根；仅绝对路径快照依赖
  summary 的 project_dir（此类快照来自异常退出，本就有 summary）
- 防回退测试：`test_hard_kill_snapshot_without_summary_restores_from_checkpoint`
  （相对路径 + 无 summary 的硬终止现场恢复 3 个已付费视频）

### 边界与不修的部分
- 绝对路径快照 + 无 summary 的组合不存在于真实生产（如上），不做投机支持
- wakeup 对 cancelled 源自动接力（run 322）为**自洽设计**：cancel 只杀进程不关任务，
  任务由检查点状态驱动；要真停任务需用 close 工具关闭

## 发现三：硬终止僵尸 job 撞排队闸（run 324/325/326 三连拒，本修复）

summary 修复（2766a8c）过后，恢复链推进到最终闸「snapshot contains unrelated
or unresolved queued work」再次死锁。三跳实证：run 324（wakeup 473 自动接力）、
run 325（patrol 91 死链兜底）、run 326（手动）——**恢复自动化三层全部到场、
全部撞同一堵墙**。

### 根因
取消瞬间的 jobs.json 含已认领未提交的僵尸 job（run 321 的 s02_c01：
state=running，params.video_pre_submit_failure=last_frame_url——预提交阶段就
失败、卡在重试循环中被取消，从未到达供应商）。restore 的最终闸只放行
done/failed/对账恢复集，僵尸 running → 拒绝。

### 修复（4dc3320）
无 provider_task_id/video_id 且不在对账恢复集的 job = **可证实从未发出付费
请求**（意图先于请求落盘的契约 + C2 纪律「确知未提交 → 允许且仅允许一次提交」
的快照级应用）→ 标记 failed 弃置：惰性（队列不再执行）、留痕
（hard_kill_abandoned=true）、不与恢复侧 FullRun 的新 job 双重提交，镜头由
恢复侧恰好一次重驱动。**带供应商凭证者维持 fail-loud 拒绝不误伤**（C3b
UNKNOWN 家族继续走对账路径）。

### 设计确认（为什么僵尸不能以 queued 入新账本）
恢复侧驱动的生产循环会为未完成镜头创建新 job；若僵尸以可执行状态入账本，
新旧两个 job 并发 = 同镜头双重提交风险——最终闸守的就是这个，修的是「可证
安全者放行」而非「放松闸门」。

## 后续

- [x] **Round 1 全链闭环（run 327，2026-11-11 08:01 派发 → 单跳收片）**：
      过闸 → restore 双修复 → §四十七三级恢复 → 僵尸镜头 s02_c01 恰好一次重驱动
      （C2 语义）→ 20/20 全受理零重滚、202.9s、首镜付费视频复用零损失 →
      归档三对账过（release 441143439 双资产 / registry COMPLETE / B1 检查点）。
      期间 wakeup(324) 与 patrol(325) 自动接力到场（均撞僵尸闸墙，实证恢复
      自动化三层齐备）。一次跳收片，无额外 hop。
- [x] 取消者身份已查明：本 agent 误诊取消（见上，教训入档）
- [ ] Round 2（提交中死亡/脏账面形态）视用户决定——UNKNOWN 对账路径已有
      历史活证（run 315→318 的 s03_c01 族），边际价值低于 Round 1
