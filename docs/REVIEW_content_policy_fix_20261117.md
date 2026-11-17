# 内容政策驳回三层防停摆（CP-3）— 交接文档

- **日期**: 2026-11-17
- **提交**: fb8d181 → 1266ba9（用户拍板修正为「审核式换措辞」设计）
- **触发事件**: anime750s-20261116a run 336/337/338 三跳 12 发首帧候选全部 content_policy_violation 拒死，两次 gateless 静默停摆各 4h+

## 一、根因证据链（勿凭记忆，对账用）

1. **死法不是额度墙**：job 缓存实证（run 336 artifact `cache/jobs.json`）
   - `a780bb6934c2` = s03_c09 first_frame **批 1**（variants=2），error=`Agnes API 错误 400 ... "code":"content_policy_violation"`
   - `50dc974fdcbb` = **批 2**，同样 400 拒；两批七秒内全灭（400 是即拒，无生成耗时）
   - run 337（jobs 2 发）与 run 338（4 发）重复同形——三跳累计 12 拒
   - 额度墙是另一套长相：429/DailyQuotaExhausted → 检查点落 `quota_resume_after` 闸门 → 唤醒链接续。337 的墙（gate=2026-11-16T16:10Z）与 00:14 的自动接续都正常工作
2. **内容源头**：s03_c09 描述内嵌台词原文（"对不起我不是故意撞你的"），脱离剧情上下文触发供应商内容过滤
3. **静默放大器**：内容死 `terminal_status=None` 不在 `{paused, quota_exhausted}` 可自动恢复集 → 唤醒链按设计解除武装（`no matching resumable checkpoint`）→ 无人知晓

## 二、修复设计（用户拍板：驳回=审核意见，重试+换措辞）

**设计原则**：台词是 NDC（语音对话生成）的合法语义输入，正常批次必须保留原文；
内容驳回与自有审片打回同类 → 处理方式是「重试 + 换措辞」，不是永久剥除。

| 层 | 机制 | 文件 |
|---|---|---|
| 批次三 | 批 1/2 原提示词；全被内容驳回 → 退避 30s 走批 3，enqueue 带 `content_policy_retry=True`。其它失败类别维持两批 fail-loud | `src/ai/autopilot.py` `_do_first_frame`/`_do_last_frame` |
| 换措辞 | handler 读 `content_policy_retry` → builder `strip_speech=True` → 剥离引语对白（引号+包裹全角括号整体删除，画面语义保留）| `routers/shots.py` 两 handler、`src/generation/image_adapter.py` BOUND/legacy 四路径、`src/core/prompt_builder.py` |
| 红警 | patrol 发现 gateless FAILED（phase=FAILED 且 terminal_status 不在可恢复集）→ 隔离 AWAITING_HUMAN + `main()` 返回 1 点亮 Actions 红警（只响一次；人工 dispatch 在 AWAITING_HUMAN 下放行，有测试先例） | `tools/resume_patrol.py` |

## 三、防回退测试（红了先怀疑自己，别当冗余删）

- `tests/test_prompt_builders.py`：**默认批次台词保留**（误伤即红，用户拍板的核心锚点）；重试批剥离；视频 prompt 永远保留对白；边界（空括号/未配对引号/纯文本）
- `tests/test_autopilot.py`：CP 全拒必须打满批 3（stage_key `:3` + 退避 30s + 批 3 带 flag、批 1/2 不带）；非 CP 失败必须保持两批；批 3 过审正常恢复
- `tests/test_resume_dispatch.py`：gateless FAILED → `awaiting_human_alarm`；额度墙绝不误报；main() 红警退出码

## 四、后续 agent 注意事项

1. **删重试/保底前先读本文档**——CP-3 与 quota wakeup 是两套互补机制：一个管「审核驳回」，一个管「额度墙」。看到批次三以为是冗余重试删掉的话，下一个内容死镜头会回到静默停摆
2. 视频 prompt 永远不剥对白（NDC 语义）；只有图像侧的**重试批**剥
3. `content_policy_violation` 在 job error 文本里做子串匹配——供应商若改错误码形态，同步改 `_do_first_frame`/`_do_last_frame` 两处判断
4. 若未来视频提交侧也出现内容驳回（NDC 语音内容触发过滤），属新故障类，勿并入本机制（视频路径有自己的 Uncertain/对账体系）
