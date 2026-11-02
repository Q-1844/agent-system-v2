# 成片独立验收与下一轮跨日生产

## 已完成影片：daycarry12m-20261101

源 Actions [41337749271](https://github.com/Q-1844/agent-system-v2/actions/runs/41337749271) 已 success，B1 COMPLETE，error=null。独立下载 B 仓 Release 430826907 的两份资产并核对 GitHub digest 和检查点 SHA：

- final.mp4：138961755 bytes，SHA256 `909f9a9fcc7c17b29dad8da996537aa028e804fdaa394967310ff46c0b53178f`。
- project.tar：967536640 bytes，SHA256 `4c7f37ff7f0ef84eb3ef0e549fd9a8096fcd49b6b15c4e2267b07b999d6e8057`。
- 独立 ffprobe：视频 719.711995 秒，容器 719.733333 秒，H.264 / 1280×720，含音频；ffmpeg 全量解码通过。
- 归档独立安全恢复：600 文件逐项核验，full，hash_bad / missing / missing_required 全为空。
- FullRun、ProjectRun、6 个场景均完成；90 镜全部有 done 版本并选中。原 63 镜的任务号、版本和本地 SHA 与既有基线逐项一致，且仍在成片选版中。
- 90 条 ACCEPTED 的请求秒数为 714 秒。原未知提交仍保留 8 秒可能成本，不计为成片素材或回执。视频实测时长与请求秒数分开记录。

这证明原片接续后最终成片并归档成功；此前有人工修复和显式派发，不能据此声称全自动跨日唤醒已验收。

## 本轮发现并修复

1. **归档注册表漏记制作完成。** 旧片 checkpoint COMPLETE，但 registry.production_status 仍 IN_PROGRESS。新增 guarded `Registry.mark_completed`：仅针对同一 fully verified archive、原 activity_seq/revision、无在途或同步意图的项目写 COMPLETE；不删除、迁移或重排素材。driver 在完整成片 QA 和归档恢复验证后调用。另提供 `reconcile-completed-film.yml` 为旧完成任务回填；两次 CAS 之间中断可幂等重跑，拒绝覆盖后续新活动。
2. **长片 artifact 大小与恢复能力不匹配。** 实际成功 run 的 artifact 达 3856636565 bytes，重复携带 package、redownload 和 restore-check。上传中排除这三类验证中间产物，保留真实 projects/cache、摘要及 JSON 验证报告。恢复改为流式下载到临时文件、SHA/长度核验后流式解包，避免整包/整段视频占用内存。ZIP 磁盘上限 8 GiB、展开上限 16 GiB；危险路径/符号链接/嵌套仲裁等原守卫保留。明确超限仍拒绝，不能无限使用 runner 磁盘。
3. **新片风险授权不能静默忽略。** 原 `confirm_uncertain_submissions` 只有恢复路径消费，新任务 true 不会形成持久策略。现在新任务显式 true 时，在原生 workers 启动前按 nonce/request digest、操作者、Actions run 落账未来风险策略；未授权仍默认 fail-loud，旧提交的实际重提继续由 stopped-snapshot 对账控制。

新增 39 项针对性测试，包括真实打包/重下载/恢复 + registry 完成事件、CAS 竞争和两写中断、旧回执和新活动保护、fresh policy、流式读/解包、大小/digest/展开限额。全量回归结果见 PR 和 C 仓运行交接；GitHub CI 未完成时不得宣称通过。

## 下一部片的测试方法

采用新 nonce、auto 模式、约 20 分钟的新故事；目标显著超过每日视频额度，不复用旧片凑时长。保留 Producer 对剧作、镜头数量、镜头长度及专家协作的自主权。启用 2 路媒体和明确的同任务风险授权，driver 330 分钟保险丝。

GLM 按 C 仓最新交接的 nonce/run 接棒：

1. 首次记录 task-spec / frozen blueprint / plan fingerprint、目标时长和本任务风险 policy。不要把旧片的 owner 或 nonce 用在新片上。
2. 额度不足时核对 WAITING_QUOTA、quota_exhausted、有效 quota_resume_after、drain_complete=true；拒收意图应终结，已接受视频必须保存。额度等待不算剧情失败。
3. 闸门打开后巡检从最新已停止 run 的 artifact 接棒，相同 nonce/request/FullRun。旧 accepted IDs、镜头选版、文件 SHA 保留，新请求有新的有效回执，次数不因跨 hop 耗尽。
4. GitHub cron 是 best-effort，不能保证在零点准时。过闸后无新 owner，先读 patrol 的派发理由；必要时按 C 的顺序手动 dispatch patrol，再考虑最新 resume_run_id + 原 nonce + auto 兜底。手动兜底不算全自动唤醒通过。
5. 已有任务授权下新的 UNKNOWN 可停机后交给巡检恢复对账；有 task ID 优先回收原任务。不能在活进程盲重发，不能把旧 UNKNOWN 的可能成本冒充新素材。
6. 约每 30 分钟查看一次即可；Codex 提交并检查启动后不连续等待生产，任何人工单次真实验证不超过 50 分钟。
7. 如用只读 watcher 收集较大 artifact，显式设 `--max-download-mib 8192`，避免其默认 1 GiB 审计下载额度造成误判；生产恢复已使用流式 8 GiB/16 GiB 上限。
8. 最终检查成片全解码、项目恢复完整性、registry COMPLETE、B1 COMPLETE，以及已完成镜头 hash 和版本；不能只凭 Actions success 或 RESTORED 宣布全链成功。
