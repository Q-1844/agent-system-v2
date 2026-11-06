# ink-diffusion 跨日完成审核与 Pages 发布恢复

## 结论

本次审核不能宣告整片接续完全成功。任务 `ink-diffusion-20m-20261105` 已完成跨日恢复并新增视频，但 run [42252251047](https://github.com/Q-1844/agent-system-v2/actions/runs/42252251047) 在北京时间 2026-11-07 01:31 因尾帧图片输入不可下载而失败，尚未合成、归档。

- 同一 FullRun：`frun_9b6951965f`；同一计划指纹 `88ccdffb831b7437a37add75e7c05b95b5757a7e97d5c8303c705a57f199da20`。
- 冻结计划 145 镜、1192 秒；已完成视频从 106 镜 / 889 秒增加到 114 镜 / 953 秒。这里的秒数是请求素材时长，不是成片时长。
- 114 条 ACCEPTED，均有完成视频和选版；115 条意图中 114 COMPLETED、1 REJECTED，无 SUBMITTING/UNKNOWN。
- 上一跳的 106 个视频与当前 artifact 的任务 ID、版本、SHA256 逐项一致。
- 当前检查点 FAILED / failed，无未来额度闸门；不是额度等待，巡检按既有规则不会自动重启普通真实失败。

## 证据与根因

1. run80 的 `e2e-evidence`：artifact `13524649308`，832684988 字节，SHA256 `1a99fc2831cce24ad1a04e0c18c6a53b277364cf7648e39359582a1ab7ed693f`，本次完整下载校验一致。
2. `s04_c26`、`s05_c05` 都是 inherit_previous；其首帧取自 `s04_c25`、`s05_c04` 的真实视频末帧。JPEG 已在 artifact 中，URL 也写入 `video_end_frame`，但公网实际返回 404。
3. 对象 `06b6023117e7199c.jpg`、`392ce53d36aefe50.jpg` 已在 D 仓 main。D 仓 [42260516611](https://github.com/Q-1844/agnes-frame-host/actions/runs/42260516611) 构建成功上传 Pages artifact，但 deploy 在 600000ms 超时；前一构建被新构建取消。
4. `GithubPagesHost.upload()` 的 GET-first 命中分支直接返回 URL，跳过 `_wait_ready()`。因此首次上传超时后的重试虽然保留，实际上只证明 Git 仓库有字节，便把未发布 URL 标为可用。
5. Agnes 图片接口报 `400: media URL could not be downloaded`；两次 batch 都复用了同一个未发布 URL，无法自救，最终 FullRun FAILED。

## 修复边界

- Contents 命中只省略 PUT；首次上传、幂等命中、409/422 冲突核对成功都须等 Pages 200 才返回。
- 图片生成优先使用所选资产的本地原字节 data URI，保持顺序和参考身份；没有本地文件仍保留原 URL。只改变请求传输，不修改选版、URL、receipt 逻辑输入摘要，不把 base64 写进账本。
- 对恢复快照中已经保存的 Pages URL，新视频 POST 前再次检查发布状态。检查在提交意图登记之前；失败可在未产生任何付费证据时重试。等待结束后，原提交 guard 仍检查输入/选版是否变化。
- 视频仍使用公网 URL；不将 data URI 送进视频接口。已有 provider_task_id 的恢复路径不增加图片检查、不发新 POST。
- 保留 `_do_video_end` 的三次重试、120s 退避、1200s 终试等待及结构测试。本次修的是重试里误判成功的托管层，没有删除等待策略。

## 验证

- 新增 9 项回归：幂等命中但 Pages 404、上传超时后重试仍拒绝未发布 URL、恢复 URL 检查、独立/继承两类本地字节输入、图片 receipt 重放、视频发布检查前零提交、只有未提交时可重试。
- 真实 artifact 经 `restore_snapshot` 恢复，复用全部 114 个视频。用原生尾帧 handler 对两个失败镜头做本地供应商替身回放，两者成功，114 个视频的任务 ID、版本、SHA256 全部不变；该回放真实付费请求为零。
- 全量回归结果与部署、续跳回执在下方记录。

## 接续与最终验收

继续原 nonce，`resume_run_id` 必须引用 `42252251047`，`target_duration_seconds=auto`、`media_inflight=2`。不得重开 nonce 或重拍已完成视频。先确认 D 仓失效部署已恢复、两张既有 JPEG URL 可访问，再使用修复后的代码续跳。

完成判据仍是：145 镜完整选版、FullRun / ProjectRun / SceneRuns 全部完成、最终 MP4 全量解码正常、归档回读 hash 及 restore 检查通过、B1 COMPLETE 和注册表 COMPLETE 一致。恢复成功或新增回执均不能替代上述完成验收。

## 本轮验证结果

- 本地全量：3894 项中 3884 passed、10 skipped、0 failed / errors，476.987 秒。10 项跳过仅因 P55 真实外部样本尚未下载。
- 随后从 B 仓取回 36116480 字节 P55 样本，SHA256 `f18a80b3a2b60b4ed762ef9aec70edb85f9124147dd8cdc1f7f38779dd2b75ff` 核验一致；两个样本测试文件补跑 15 passed、0 skipped / failed，覆盖全量跳过的 10 项。不能把第一次全量描述成零跳过。
- D 仓 failed-job retry 获受理但长期 queued、无新 job；随后显式请求 Pages build，最终 built。两个原失效 URL 已独立 GET=200、image/jpeg，下载字节与 artifact 中的两张 JPEG 逐字节一致。
- 修复 PR：[A #51](https://github.com/Q-1844/agent-system-v2/pull/51)。后续合并、真实续跳和接棒证据见 C 仓 `docs/HANDOFF_ink_publication_recovery_20261107.md`。
