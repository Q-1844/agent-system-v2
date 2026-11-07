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
- **posthoc 重建模式**：`--rebuild-from`（e2e-evidence artifact 恢复项目目录
  重打包）+ 资产名带 GITHUB_RUN_ID 后缀（防 422 撞同名僵尸死锁）；
  artifact 下载走 B1_ARCHIVE_TOKEN + REST API（download-artifact@v4 的
  GITHUB_TOKEN 跨 run 下载被拒，42384281634 实锤）。
- **收官证据**（posthoc run 42391322297 success）：重打包 2092421120 字节
  （与原 tar 同尺寸）→ `project-rebuilt-1649070199b6-42391322297.tar`
  uploaded + 回读 sha 自证 → restore level=full / 882 文件零坏 → 树指纹一致
  → **片长 1213.205s** → registry completed + B1 phase=COMPLETE + report。
  Release 437329843 保留原始 starter 僵尸 asset 作为故障证据（勿删）。

## 二、测试锁（防回退，红了先怀疑自己）

- `tests/relstore/test_relstore_07_http_adapter.py`：starter 锁三件套
  （201 回执不信任 / 轮询至 uploaded / 永久 starter fail-loud）+ 422 僵尸
  拒用 + 恢复路径僵尸拒用 + 超时后确认复用 + **结构锁**。
- `tests/relstore/test_posthoc_rebuild.py`：posthoc 重建模式结构锁 ×4。
- `tests/relstore/test_relstore_08_index_snapshot_full.py`：mock 修正为真实
  GitHub Accept 语义（JSON→200 元数据 / octet-stream→302 跳转）。
- `tests/test_daily_video_seconds_knob.py`：额度旋钮结构锁（默认 500 恒定
  / env 生效 / 空串=未设置 / workflow 接线）。

## 三、🔴 测试旋钮：DAILY_VIDEO_SECONDS（aee070a）

- **用途（用户拍板 2026-11-08）**：压缩模拟「按天额度耗尽→20min 定时器→
  唤醒接续」，替代每天只能一次的真实跨天实测。
- **机制全走真实代码**：`QuotaLedger.can` 预检失败 → AgnesClient raise
  `DailyQuotaExhausted` → `classify_quota_rejection` → `pause_for_quota`
  （闸门=北京次日 00:10）→ `quota_wakeup` 环境 20min 定时器复查 → 开门即
  派发 `resume_run_id` 接续。
- **纪律**：默认 500=真实供应商口径（2026-11-03 TA 核实），**生产派发不得
  传小值**；仅链路测试用。
- workflow 输入：`daily_video_seconds`（number, default 500）→ env
  `DAILY_VIDEO_SECONDS`。

## 四、当晚压缩模拟（run 85 / 42394136215）

- 派发 01:00:40 北京：三分钟人物故事短片（修鞋匠与旧舞鞋；auto 模式），
  `daily_video_seconds=96`（12 镜 ×8s 触墙）。
- **闸门压缩操作**：触墙后 CAS 改 B1 检查点 `quota_resume_after` =
  now+15min（`/home/z/gate_edit.sh`，只改这一个字段，其余不动）——让
  wakeup 链的 20 分钟环境定时器第一次复查就撞上开门。**这是测试域操作**，
  生产闸门永远是「北京次日 00:10」，勿在真实生产中改。
- 观察点（watchdog 自动记录 `/home/z/watchdog.log`）：
  1. B1 首写 nonce（付费请求前落盘）
  2. 12 镜后本地预检触墙 → WAITING_QUOTA + 干净排水 + run failure
  3. wakeup 链 20min 定时器 → 闸门开 → 派发接续 run（resume_run_id）
  4. 接续 hop 复用已冻结镜头 → 续产（96+~100s < 真实 500s）→ 完成收片归档
- **次日天然测试**：另派 15 分钟长片（用真实额度墙），明晨 00:10 天然唤醒。

## 五、踩坑记录（新）

1. **CI sparse-checkout 环境差异**：runtime-regression（ubuntu）不带
   `.github/workflows/`，读 yml 的接线锁测试在那必红（本地有 yml 所以绿）。
   已把 posthoc-complete.yml 加进两个 job 的 sparse 清单。
2. **前台 sleep 会被网关 504 截断**（墙钟不推进，时间感被扭曲）——守候
   类操作必须 nohup 后台脚本 + 日志落盘 + 信号文件；前台只做快速查询。
   （误判代价：run 84 规划 13 分钟被误取消——无 B1 无素材损失。）
3. **POST 超时 ≠ 失败**（干跑 #2 的 982b9d3b156a 资产后来转 uploaded）：
   网络黑窗回执丢失后，服务端可能已完成——所以恢复路径 GET 确认 state
   才是唯一真相，既不重 POST 也不轻信失败。

## 六、明日待办

- [ ] 晨间确认：run 85 接续→完成→归档终态（B1 COMPLETE / registry /
      release 资产 uploaded + finalize 复核实战首秀）
- [ ] 派发 15 分钟故事长片（真实额度墙，天然跨天测试）
- [ ] 收紧审片 skill（用户既定方向：打回-修复闭环实测）
- [ ] C 仓 archive 补本文档副本
