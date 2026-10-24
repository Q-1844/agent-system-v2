# GLM 5.3 本地值守交接

本轮分工：Codex 修复代码、运行回归和提交修复；本地 GLM 5.3 负责长时间值守、保存证据及终态复核。监控程序只访问 GitHub GET 接口，绝不重跑 workflow、取消运行或调用 Agnes。需要 Python 3.10+，监控本身只用标准库。

## 当前任务身份

- 仓库：Q-1844/agent-system-v2，默认分支 chore/migration-readiness。
- 运行：[40055563112](https://github.com/Q-1844/agent-system-v2/actions/runs/40055563112)。启动：洛杉矶 2026-10-23 19:46:23（UTC 10-24 02:46:23）。
- **正在运行的代码固定为 c3afe255bec6bccdb52aa8ad3e2bf98ab14db9c2**。后续默认分支的修复不会热更新这个 runner；不要把默认分支最新 SHA 当成当前运行 SHA。
- nonce：film3m-20261023-rescue5；FullRun：frun_80b1bd5120；ProjectRun：prun_4ddff9a28f；首段 SceneRun：run_4029a80181。
- 当前运行从 40053946925 的快照恢复；更早原运行 40002008268。原两个视频和第三镜尾帧已复用，不得新建 nonce 清零重做。
- 原规划 5 故事段、20 镜头、约 200 秒；最终移动视频流和容器均必须 >=180 秒。视频提交累计预算 240 秒，包含前面运行已用的 20 秒。
- B1 检查点：agent-system-v2-rundata-1，分支 test/e2e-film3m-20261023-rescue5，文件 e2e/film3m-20261023-rescue5/full-project-production.json。
- 2026-10-23 20:40 洛杉矶复核为 RUNNING、9 个 ACCEPTED 任务、19 次图片、31 次文本；这是历史快照，启动监控后以 latest.json 为准。ACCEPTED 只证明服务端受理，不能称为镜头已完成或累计成片时长。

## 启动

在本地更新仓库后，从仓库根目录执行。凭据需要 A 仓 Actions:read 和 B1 Contents:read；不需要 Agnes Key。沿用本地已保存的 GitHub 凭据，值不写入文件、配置、聊天或日志。

PowerShell（已安装并登录 gh；若 GH_TOKEN 已在环境中，跳过第一行）：

```powershell
$env:GH_TOKEN = gh auth token
python tools/watch_full_project.py --config tools/watch_film3m_20261024.json --output evidence/film3m-40055563112 --interval 60 --timeout-minutes 180 --collect-on-stop
$watchExit = $LASTEXITCODE
Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
Write-Output "watch exit=$watchExit"
```

Bash：

```bash
export GH_TOKEN="$(gh auth token)"
python tools/watch_full_project.py --config tools/watch_film3m_20261024.json --output evidence/film3m-40055563112 --interval 60 --timeout-minutes 180 --collect-on-stop
watch_exit=$?
unset GH_TOKEN
printf 'watch exit=%s\n' "$watch_exit"
```

只查看一次时加 `--once`。单次 ACTIVE 返回 10 是正常结果。无本地 gh 会话时，可使用已配置的 GH_TOKEN 或 GITHUB_TOKEN 环境变量。请勿在这里重新登录或贴出凭据；由本地环境管理凭据。

每次运行使用独立输出目录，不要同时运行两个实例写同一目录。目录带 run/source/nonce 绑定，不能混入其他任务。默认单个下载上限 1 GiB，必要时可显式提高 --max-download-mib（最高 4096）。

## 输出与退出

| 退出码 | 含义及后续动作 |
| --- | --- |
| 10 | --once 时仍活动或等待当前运行认领检查点；继续观察 |
| 20 | Actions 成功、B1 COMPLETE，仍须本地独立验收；不是“成片已验收” |
| 2 | 运行失败；保存证据，分析失败阶段后处理 |
| 3 | 读取失败（连续三次即退出）；核对网络/只读权限，不能认定项目不存在 |
| 4 | 身份不符、未知提交、心跳超过 5 分钟、运行中断或证据校验失败；停止自动动作，先对账 |
| 5 | 本地监控窗口结束；不会取消云端任务，也不代表任务失败 |

- latest.json：当前观察，包括 Actions run 与 B1 原始检查点。
- snapshot-last-good.json：最后一次成功读取并完成身份核验的观察；读取错误不会覆盖它。
- timeline.jsonl：时间、状态、累计提交秒数和调用计数。
- 终态时自动收集 actions-logs.zip、e2e-evidence.zip、evidence.json；ZIP 按 GitHub SHA256 对账，下载失败保留旧文件，临时文件删除。不自动解压或执行任何归档内容。
- 成功终态额外核对归档 summary 身份、>=180 秒报告、20 镜头来源、不同 SHA 及 B1 Registry 的 VERIFIED_RESTORABLE 引用，保存 summary.json、registry.json。
- collection-error.json：取证未完成，保留已经下载的日志/快照，修复读取或下载后可再次 --once --collect-on-stop；禁止因此重跑生产。

## 终态验收与失败处理

1. Actions success + B1 COMPLETE 仅为远端报告成功。核对本地 summary.json 的 media、source_videos、saved 及 registry.json，保存审计结果，不能仅看 Actions 绿灯。
2. 用 B 仓 Release 的 final_mp4_asset_id 下载 final.mp4，SHA256 必须等于 archive_ref.final_mp4_sha256；使用 ffprobe 核对一个非封面视频流与容器均 >=180 秒，ffmpeg -v error -xerror 完整解码。Python 环境已装仓库依赖时，可调用 tools.full_project_cloud_acceptance.validate_final_media()。不能循环、静帧或补音频凑时长。
3. source_videos 必须对应原 20 镜头、唯一视频 ID 与不同源文件 SHA；核对项目中 final_video/VideoVersion 和 composition manifest。需要从快照提取文件时使用现有受限恢复工具，勿直接批量解压陌生 ZIP。
4. 下载 project.tar，核对 archive_ref.sha256，使用现有 tools.relstore.restore_campaign 在空目录恢复；要求 full 且 missing_required=[]。核对 B1 Registry 的 project_id、archive_ref 与对应恢复指纹。
5. 失败时先看 actions-logs.zip 和 summary/error，保存快照，确认每个 ACCEPTED ID 在 provider 的状态及本地文件。UNKNOWN / SUBMITTING 长期未确认 / 已受理但缺文件都不能重新 POST。对账后才能使用现有显式 resume_run_id 入口，必须复用 nonce、原生 IDs、已完成素材和累计预算。不得直接 gh run rerun 或新开测试影片。
6. 当前 runner 有原 150 分钟驱动限制和 180 分钟 Actions 上限。新补丁的“有限暂停收尾”只影响后续运行；当前旧 runner 若在上限结束且 B1 仍 RUNNING，按 INTERRUPTED_RECONCILE 处理。不要把未落盘视频任务当作失败后重生成。
7. 将实际终态、运行 ID、源码 SHA、媒体时长、SHA、归档/恢复证据同步到 C 仓 feat/codex-handoff-20261023。三分钟验收未完成前，Producer 新能力、预览页等继续后置。

C 仓入口：[CODEX_START_HERE.md](https://github.com/Q-1844/agent-system-v2-archive/blob/feat/codex-handoff-20261023/CODEX_START_HERE.md)。用户已经批准整顿、代码修复、合入及此次真实测试；不需要重新询问同一授权。
