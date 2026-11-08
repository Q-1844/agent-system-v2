# HANDOFF: ink 归档收官 + 额度墙压缩模拟（2026-11-08）

> 给后续 agent（Codex/GPT/未来的我）：本文档记录 2026-11-07 夜～11-08 晨的
> 修复链与当晚压缩模拟实验。**动了哪些红线文件、为什么动、测试锁在哪**，
> 一目了然。测试红了先怀疑自己。

## 一、ink-diffusion-20m-20261105 正式收官（已验证）

- **根因链**（跳 3 run 42335593731）：2.09GB `project.tar` POST 201 回执成功，
  但 GitHub 端 finalize **永久卡 state=starter**（19h 仍 starter，资产 404）→
  归档验收 240s 宽限耗尽 → 误判 archive_failed。素材全程安全（artifact 2.09GB
  + final.mp4 347MB uploaded）。
- **修复三面**（6f9c13d / 51ba199 / 0bc0a41）：
  1. `upload_asset` POST 201 后**必 GET 复核 state=uploaded**（回执自称
     uploaded 也不信）；300s 轮询超时 → ReceiptLost fail-loud。
  2. starter 僵尸**全面拒用**：422 幂等 reconcile 与回执丢失恢复路径均要求
     state=uploaded；同名同 sha 也不行（starter 永远 404）。
  3. 2GB POST 超时按体积放宽（30s + 每 2MB 加 1s，900s 封顶）——2GB 用默认
     30s **必然** ReadTimeout，客户端掐断后服务端留半成品（僵尸成因）。
- **posthoc 重建模式**（42391322297 成功收官）：`--rebuild-from`（e2e-evidence
  artifact 恢复项目目录重打包，2092421120 字节与原 tar 逐字节同尺寸）+
  资产名带 GITHUB_RUN_ID 后缀（防 422 撞同名僵尸死锁）；artifact 下载走
  B1_ARCHIVE_TOKEN + REST API（download-artifact@v4 的 GITHUB_TOKEN 跨 run
  下载被拒，42384281634 实锤）。最终：restore full/882 文件零坏、树指纹一致、
  片长 1213.205s、registry completed + B1 COMPLETE + report 落账。release
  437329843 上 3 份 rebuilt 全 uploaded，原僵尸 starter 留作故障证据。

## 二、额度墙压缩模拟（2026-11-08 凌晨实测，run 85 → 87 → 88）

用户拍板：真实跨天接续每天只能测一次；压缩模拟走全真实链路
（本地账本 96s 档触墙 → wakeup 20min 环境定时器 → 闸门 → 派发接续）。

**实测时间线（全 UTC，+8=北京）：**
- 17:00:40 run 85 派发（3 分钟故事片「修鞋匠与旧舞鞋」，auto 规划，96s 模拟档）
- 17:38:18 hop1 触墙：12 镜 ACCEPTED=96s，WAITING_QUOTA/quota_exhausted，干净排水
- 17:38:20 wakeup 自动创建（workflow_run 事件 2 秒）
- 17:38:37 wake job 进入 environment wait timer（20 分钟非计费等待）
- 17:52:47 N84（被取消的 15 分钟片）检查点关闭（见边界发现 1）
- 17:53:52 N85 闸门压缩 16:10→17:57:52（b1-test-ops，+4 分钟）
- 17:58:38 定时器准点放行（20 分 01 秒）；17:58:51 接续 run 87 派发（触墙后 20 分 33 秒）
- 17:59:48 run 87 完成排水跳职责（恢复态里残留 hop1 触墙时的配额错误任务）
- 18:00+ run 88（42399015379）A 机制立即接管，干净续产：13、14 镜连续 ACCEPTED

**发现的边界 bug（记录在案）：**
1. 取消于规划期的 run 不可死链恢复：run 86 恢复 run 84 时
   FileNotFoundError（快照缺 summary.json，取消时 acceptor 未及写）。
   处置：b1-test-ops close 关闭 N84（RUNNING→FAILED/failed 防巡检无限重派）。
2. 触墙瞬间的恢复态残留需要一跳排水：hop1 第 13 镜提交被墙拒后，任务以
   配额错误形态持久化；直接接续的 run 87 只负责排掉它（paused+drained），
   A 机制立即派下一跳干净续产——与 ink 跳 3 两段式接续同构，属设计内行为。
3. GitHub workflow_dispatch API：number 输入必须传字符串（JSON 数字 → 422）。
4. B1 仓写入必须走 B1_ARCHIVE_TOKEN（本地 GH_TOKEN 对 rundata-1 只读）。

**工具沉淀（492a628，CI 绿）：**
- `DAILY_VIDEO_SECONDS` env 旋钮 + workflow 输入 `daily_video_seconds`
  （默认 500 恒定=真实口径；账本管线：full_project_cloud_acceptance:1678
  QuotaLedger limits，跨跳经 artifact 的 cache/quota.json 持久 used 值）。
- `tools/b1_test_ops.py` + `.github/workflows/b1-test-ops.yml`：闸门压缩
  （仅 WAITING_QUOTA+quota_exhausted 可压）与卡死关闭（仅卡死相位）两操作，
  守卫测试×6+接线锁×2，CI sparse-checkout 已含 yml。
- **生产闸门永远是北京次日 00:10**（quota_gate.RESET_MARGIN_MINUTES），
  压缩仅限本工具显式操作，勿用于真实生产派发。

## 三、踩坑记录（新）

1. **CI sparse-checkout 环境差异**：runtime-regression（ubuntu）不带
   `.github/workflows/`，读 yml 的接线锁测试在那必红（本地有 yml 所以绿）。
   已把 posthoc-complete.yml / b1-test-ops.yml 加进两个 job 的 sparse 清单。
2. **前台 sleep 被网关截断、时间感被扭曲**（误取消 run 84 的教训）：
   一切以 date 和 API 时间戳为准；守候类操作用后台脚本+日志落盘。
3. **POST 超时 ≠ 失败**（干跑 #2 的 982b9d3b156a 资产后来转 uploaded）：
   网络黑窗回执丢失后服务端可能已完成——恢复路径 GET 确认 state 才是
   唯一真相，既不重 POST 也不轻信失败。

## 三点五、热循环事件与修复（2026-11-08 晨，bb1702a）

用户质疑「接续根本无法实现」触发全面取证，结论：**接续核心无恙，坏的是暂停分类→派发闸门的接线**。

- **现象**：run 85→304（215 跳空烧，18:18→03:20 UTC），每跳 12 秒即暂停、
  每分钟一跳、每跳上传 108MB artifact。
- **根因**（artifact jobs.json 取证）：96s 模拟墙在 s05_c04 留下 job 级
  daily_quota 失败（not_sent + quota_resume_after=16:10Z）。恢复跳回放该
  失败 → autopilot wait_for_quota → JobQueue.retry 因闸门未开正确拒绝重试
  → 但驱动 paused 分支【无条件立即派发下一跳】→ 死循环。期间其余镜头
  14→22 正常产出（绕开毒镜头），只剩毒镜头时纯空转。
- **修复**（bb1702a）：`job_level_quota_gate()` 扫毒任务签名；paused 分支
  有闸门 → WAITING_QUOTA 落账（auto_resume_next_hop 见闸门自动拒派，
  wakeup 链按日历接管）。测试：签名检测×2 + 结构锁×1（闸门检查必须
  先于派发）。run 307 实证：落账 WAITING_QUOTA+16:10Z ✓ 新错误路径 ✓
  wakeup 10 秒武装 ✓。
- **止血手段**（复用）：取消 in-flight run 掐断其退出路径派发；409=已
  收尾派了后继需连追。checkpoint=FAILED/paused 时 wakeup/patrol 自动停
  （不在 RESUMABLE_PHASES）——唯 in-driver 派发无视 phase。
- **今晚 00:10 北京**：wakeup 自然唤醒 → s05_c04 重试 → 收片归档（同时
  即真实跨天接续测试）。
- **守候盲区教训**：昨夜守候只盯预期信号（WAITING_QUOTA），没盯频率
  异常——215 跳空转一夜无人报警。后续长守候必须带频率哨兵（如：同
  nonce 连续 N 跳零 ACCEPTED 即报警/熔断）。

## 四、明日待办

- [ ] 晨间确认：run 88 完成收片归档终态（B1 COMPLETE / registry / release）
- [ ] 派发 15 分钟故事长片（真实额度墙，天然跨天测试；替代被关闭的 N84）
- [ ] 收紧审片 skill（用户既定方向：打回-修复闭环实测）
- [ ] C 仓 archive 补本文档副本
