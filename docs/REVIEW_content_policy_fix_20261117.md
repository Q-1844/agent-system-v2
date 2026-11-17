# 内容政策驳回三层防停摆（CP-3）— 交接文档

- **日期**: 2026-11-17
- **提交链**: fb8d181（机械剥离 v1）→ 1266ba9（参数化门控 v2）→ 0d40113（agent 反馈修订，用户拍板终稿）
- **触发事件**: anime750s-20261116a run 336/337/338 三跳 12 发首帧候选全部 content_policy_violation 拒死，两次 gateless 静默停摆各 4h+

**后续接线修复**：原 CP-3 只完成了低层第三批改写，没有接通 Producer 的自主修复。
新建影片也曾被 C2-only 入口排除。现已将首帧、尾帧和视频的实际服务端驳回接入现有
Producer 协作修订账本；再次驳回继续形成新反馈并修订。修订决策暂不可用时保存为
可自动唤醒的待办。详见 `REVIEW_provider_feedback_producer_repair_20261117.md`。

## 一、根因证据链（勿凭记忆，对账用）

1. **死法不是额度墙**：job 缓存实证（run 336 artifact `cache/jobs.json`）
   - `a780bb6934c2` = s03_c09 first_frame **批 1**（variants=2），error=`Agnes API 错误 400 ... "code":"content_policy_violation"`
   - `50dc974fdcbb` = **批 2**，同样 400 拒；两批七秒内全灭（400 是即拒，无生成耗时）
   - run 337（2 发）与 run 338（4 发）重复同形——三跳累计 12 拒
   - 额度墙是另一套长相：429/DailyQuotaExhausted → 检查点落 `quota_resume_after` 闸门 → 唤醒链接续。337 的墙（gate=2026-11-16T16:10Z）与 00:14 的自动接续都正常工作
2. **内容源头**：s03_c09 描述内嵌台词原文（"对不起我不是故意撞你的"），脱离剧情上下文触发供应商内容过滤
3. **静默放大器**：内容死 `terminal_status=None` 不在 `{paused, quota_exhausted}` 可自动恢复集 → 唤醒链按设计解除武装（`no matching resumable checkpoint`）→ 无人知晓

## 二、修复设计（用户拍板，三轮迭代定稿）

**核心原则**：服务端内容驳回 = 审核意见（与自有审片打回同类）；不同平台限制
不一样，绝不写死「哪句话不能用」的规则；不做任何提示词预限制（服务端已把关，
客户端不自我审查）。

| 层 | 机制 | 文件 |
|---|---|---|
| agent 修订 | 批 1/2 原提示词直提；两批全被 content_policy 拒 → 退避 30s 走批 3，**把服务端驳回原文（错误信息）带给 handler**，由文本模型按反馈动态改写提示词（保留画面语义、避开被拒内容），改写失败降级为原样重试 | `src/ai/autopilot.py`（批三带 `content_policy_feedback`）、`routers/shots.py`（收口调 `revise_prompt_with_feedback`）、`src/ai/content_policy_revision.py`（新模块：反馈→LLM 改写） |
| 红警 | patrol 发现 gateless FAILED（phase=FAILED 且 terminal_status 不在可恢复集）→ 隔离 AWAITING_HUMAN + `main()` 返回 1 点亮 Actions 红警（只响一次；人工 dispatch 在 AWAITING_HUMAN 下放行，有测试先例） | `tools/resume_patrol.py` |

**被否决的中间方案（留档防复走弯路）**：曾实现「图像提示词机械剥离引语对白」
（fb8d181 无条件剥离 → 1266ba9 参数化门控），用户两轮纠正后全部撤除：
1. 第一轮：NDC 生成语音对话，台词是提示词合法语义输入，不能剥
2. 第二轮：平台限制各不相同，固定规则必错；驳回反馈要交给 agent 动态调整；
   服务端已做限制，客户端无需任何预限制

## 三、防回退测试（红了先怀疑自己，别当冗余删）

- `tests/test_content_policy_revision.py`：服务端驳回原文必须进入修订指令（agent 按真实反馈调整）；LLM 失败降级原文；空回复/超长回复降级护栏；无反馈不调模型（不预审查原则）
- `tests/test_prompt_builders.py`：**默认批次台词原文保留**（任何预过滤都是回退，用户拍板的核心锚点）
- `tests/test_autopilot.py`：CP 全拒必须打满批 3（stage_key `:3` + 退避 30s + 批 3 带 `content_policy_feedback`、批 1/2 不带）；非 CP 失败必须保持两批；批 3 过审正常恢复
- `tests/test_resume_dispatch.py`：gateless FAILED → `awaiting_human_alarm`；额度墙绝不误报；main() 红警退出码

## 四、后续 agent 注意事项

1. **删重试/保底前先读本文档**——CP-3 与 quota wakeup 是两套互补机制：一个管「审核驳回」，一个管「额度墙」。看到批次三以为是冗余重试删掉的话，下一个内容死镜头会回到静默停摆
2. 修订必须由 agent 按服务端反馈动态完成——**不要**退回固定规则剥离（两版已被用户否决，理由见上）
3. `content_policy_violation` 在 job error 文本里做子串匹配——供应商若改错误码形态，同步改 `_do_first_frame`/`_do_last_frame` 两处判断
4. 视频内容驳回现已接入 Producer 修复，但必须证明明确拒收或供应商任务已确定失败。
   UNKNOWN、在途任务、额度和网络故障仍使用原对账/等待路径，不能用剧情修订绕过。
