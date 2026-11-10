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

**取消者身份排查（排除法）**：
- 非本 agent：取消时刻本地会话在 bash sleep 中；无任何存活后台脚本（哨兵全被会话回收）；watchdog 类脚本只读不取消
- 非仓库内任何工作流：16:56:47 全仓仅 run 321 自身活跃；代码库无任何 cancel API 调用路径（rg 实证）
- 非用户（用户明确否认）
- 409 复测实证：对已完结 run 发 cancel 返回 409，而当时返回 202——本环境 API 视图整晚滞后
  30-50 分钟（见下节），202 来自滞后视图的误判
- **结论：Q-1844 令牌的另一个持有者（身份未明，风险点——能杀在产影片，建议用户排查
  Codex 侧/其他会话/自动化是否持有 PAT 并在 00:56 有动作）**

## 发现一：本环境 GitHub API 视图滞后（诊断纪律修正）

当晚 API 读数（contents + commits 双端点）滞后真实状态 30-50 分钟，导致：
1. 「B1 心跳冻结」误诊（实际心跳每 31 秒正常落账至 16:57:10，取消后仍有一笔收尾写入）
2. cancel 端点返回 202 而非 409（滞后视图误判 run 仍活跃）

**纪律（后续 agent 必须遵守）：运行中的活性诊断以 job 日志为准；API 读数只能作参考。
连续「冻结」读数先怀疑 API 视图陈旧，再怀疑系统挂死。**

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

## 后续

- [ ] CI 绿后派发 run 324（resume_run_id=42858356342）走完整恢复链：过闸 →
      三级崩溃恢复 → 从第 2 镜续产 → 收片归档
- [ ] 取消者身份由用户侧排查（Codex/其他会话/PAT 持有者）
- [ ] Round 2（提交中死亡形态）视 Round 1 结果决定
