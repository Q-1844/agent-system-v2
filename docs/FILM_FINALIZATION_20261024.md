# 三分钟影片收尾与制作时间窗口

本次仅收尾原任务，原 nonce=film3m-20261023-rescue5，resume_run_id=40055563112，原 Full/Project/Scene IDs 与累计预算保留。最终收尾使用 workflow 的 finalize_only=true：先校验所有选中版本、视频回执、本地完整解码，再将已提交完成但未结束登记的队列记录对账为 done，原生控制器安全置 paused 后接续。没有视频文件、选择不匹配、未知回执、取消或其他未完成任务都会拒绝恢复，不凭文件名猜版本，也不创建新 VideoVersion。

该模式禁止 Agnes chat/generate_image/create_video 三类新生成调用；只允许既有素材的末帧结束登记、原生合成、>=180秒实测与完整解码、B Release保存及B1 Registry/项目恢复核验。已用200秒仍计入240秒预算。

## 运行窗口

用户确认：3.2分钟从零制作通常4.5–5小时；已有素材、1分钟片和不同模型应分别评估。本次18个新镜头的串行提交跨度约143分钟，150分钟固定保险丝不能作为完整从零制作的默认值。

- Actions job timeout：360分钟。
- driver_timeout_minutes：默认330，可显式选30–330整数分钟；运行前校验，至少留30分钟外层余量。
- 到达driver时限时先停止新生成，沿用最多15分钟的在途素材收尾保护，再保存可恢复检查点或INTERRUPTED对账状态。
- AGNES_VIDEO_INFLIGHT=1保持；240秒视频提交预算保持；不增加并行、不清零nonce。
- 当前收尾建议driver_timeout_minutes=60（仅合成归档，无新生成）；正常3.2分钟从零制作使用默认330。

这补足单次制作的时间窗口；每日自动排产需要真实任务来源、任务去重及每日额度规则，不应通过每天创建新demo或新nonce实现。较大的排产改动后续交本地GLM按真实项目需求实施，先保留现有任务入口。

## 本地GLM执行

从C仓最新入口读取收尾运行ID和源码，复制tools/watch_film3m_20261024.json为该运行的新配置，修改run_id、resume_from_run_id=40055563112、expected_source_sha；nonce、request_sha256、FullRun和前两个历史video IDs保持。

```bash
python tools/watch_full_project.py --config <新配置.json> --output evidence/<运行ID> --interval 60 --timeout-minutes 420 --collect-on-stop
```

只需观察一次时加--once。终态后按docs/GLM53_LONG_FILM_HANDOFF.md执行独立媒体与归档恢复核验。若收尾失败，先查素材/合成/归档证据，不得重新生成20段视频。Codex专注代码修复，长时间值守继续由本地GLM承担。
