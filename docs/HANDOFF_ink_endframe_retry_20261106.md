# ink-diffusion 方案 C（末帧托管瞬态重试）交接：根因、修复与审查红线

> 写于 2026-11-06（北京）。面向 Codex / 任何后续审查 `video_end` 重试逻辑的 agent。
> **本文档是前置阅读；不得把重试代码当作"冗余复杂度"删除或简化。**

## 背景：这段代码修的是什么

- 生产片：`ink-diffusion-20m-20261105`（FullRun `frun_9b6951965f`），跳 2 = Actions run `42064787121`。
- 2026-11-06 01:54（北京）：68 镜全部 ACCEPTED（569s）后，run 死在 `s03_c18` 的 `video_end` 阶段——`RuntimeError: 视频末帧提取失败，下游镜头依赖它`。
- 根因证据（e2e-evidence artifact 内 project.json 的 asset 原文）：
  - 视频本身 ACCEPTED；ffmpeg 末帧提取成功（`s03_c18_video_end_vv_184d71085555.png` 本地存在）；
  - github_pages 图床 PUT 字节成功（内容寻址，`objects/b7d11da0eb0e2cb9.jpg` URL 已建）；
  - 唯一失败点：**Pages 构建未在 poll_timeout=600s 内就绪** → `PagesNotReadyError`。asset error 原文即「末帧托管失败: github_pages 上传成功但 Pages 未在 600.0s 内就绪（URL 已建，可稍后重试轮询）」。
- 定性：纯基础设施瞬态（Pages 构建队列慢）。错误串自带自救路径，但当时 `_do_video_end` 无任何重试，单次失败直接 `_fail_shot` → 整个 run FAILED——跨 2 个额度日的 68 镜产出险些作废（靠恢复链救回）。

## 修复（两笔提交，CI 全量回归均绿）

1. `24e53e8` 方案 C：`_do_video_end` 在下游依赖时重试 3 次 × 120s 退避；无下游依赖维持单次（host 按需跳过，仅 warning）。重试复用 upload 的内容寻址幂等（字节已 PUT，重试=再问一次，零付费调用）。硬失败消息内嵌 `last_detail` 真实根因，不再黑盒。
2. `67cee64` 终极保底（用户 2026-11-06 拍板）：3 次仍失败 → 先落盘 warning → `asyncio.sleep(1200)` → 最后一次尝试。总窗口约 24 分钟，覆盖 Pages 构建队列排满/服务降级长尾。仍不开才 fail-loud（带根因）。仅下游依赖时启用。
- 实战验证：09:53 恢复跳 `42146803043` 同项目续产，末帧链顺利通过（s03_c18 后连拍至 s03_c27+），当日 12:24 干净落 WAITING_QUOTA（106 镜 / 889s，零失败）。

## 🔴 审查红线（不可协商）

1. **3 次重试 + 20 分钟终试是刻意设计、用户拍板，不是技术债。** 理由：末帧重试不占供应商额度（sleep 不提交付费任务）；单次失败的代价是整条跨日 run，等待的代价接近零——风险完全不对称。删除重试=回到 11-06 凌晨的暴毙模式。
2. **结构测试是防回退锁**：`tests/test_day_continuation.py` 中
   - `test_video_end_hosting_transient_gets_three_retries_when_downstream_needs_it`
   - `test_video_end_final_backoff_is_twenty_minutes_after_three_failures`
   - `test_video_end_failure_detail_reaches_the_hard_fail_message`

   这些断言直接扫源码结构（`3 if downstream_needs else 1`、`asyncio.sleep(1200)`、`末帧终试异常` 等）。**你的改动若让它们变红：先怀疑自己改了不该改的，而不是先改测试。**
3. 代码内 `🔴` 注释（带日期、根因、「用户拍板」标记）是历史记录不是 TODO，不得清理。
4. 无下游依赖镜头的单次路径同样是刻意的（图床按需跳过，C2 修复 2026-10-27：Pages 构建 soft limit 10 次/小时），不得"统一"成全量托管。

## 建议的审查范围（真正值得看的）

- `_do_video_end` 内 `asyncio.sleep(1200)` 对 cancel/pause 的响应性（run 被请求暂停时长睡是否应中断提前退出）。
- 20 分钟长睡与 `driver_timeout_minutes`（默认 330min）保险丝的相互作用（单镜最长 24 分钟不会单独引爆，但多镜累计最坏情况值得估算）。
- 终试前 `_save_shot_state` 与并发 pause 是否有竞态。
- 以上均属"保持行为不变的精修"；**任何行为变更必须单开 PR 并附上下文讨论，不得顺手改**。

## 当前生产状态（本文写作时）

- nonce `ink-diffusion-20m-20261105`：106 镜 / 889s 全 ACCEPTED，跳 2 已 WAITING_QUOTA（2026-11-06T04:24:48Z），drain 干净。
- 北京 11-07 00:10 额度刷新后跳 3 预计自动续跑收尾（余量约 311s）。恢复跳派发规则：原 nonce + `resume_run_id` 引用**最新一跳**。
