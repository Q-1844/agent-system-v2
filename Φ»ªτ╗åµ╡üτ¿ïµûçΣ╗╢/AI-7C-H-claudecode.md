你现在是 AI-7C-H 的独立验收 Agent。

本轮不是继续开发，也不是帮助前一个 Agent证明它做对了。
你的任务是主动尝试推翻 AI-7C-H 的 PASS。

重要规则：

1. 先不要阅读 AI-7C-H_COMPLETION_REPORT.md 和 AI-7C-H_FINAL_HANDOVER.md。
2. 先独立阅读 AI-7C-H 的原始实施/验收规范，以及当前仓库代码。
3. 不修改任何生产代码。
4. 不顺手修 bug。
5. 如果发现问题，只报告：
   - 复现方式
   - 实际结果
   - 预期结果
   - 涉及代码位置
   - 严重程度
   - 是否足以推翻 AI-7C-H PASS
6. 不以“测试已经存在”为证明，关键不变量需要阅读真实实现。
7. 不要为了覆盖率写大量新测试，本轮重点是对抗性验收。

首先确认：

- 当前分支与 HEAD
- AI-7C-H 涉及的 commits
- working tree 是否存在用户自己的未提交内容
- 不得修改或提交这些用户内容

然后重点尝试推翻以下事实：

A. F-1 / stale 语义
- Character.views 的合法生产写回不会导致 PROJECT_CHANGED_AFTER_APPROVAL。
- description_en 等真实创作变化仍然会触发 stale。
- 检查 creative_source_signature 是否错误忽略了真正应该参与签名的创作字段。
- 检查 autopilot 的另一套 signature 是否被意外改变。

B. K1 角色外观重生成
- appearance_sig 相同时恢复不会重复生成。
- appearance_sig 变化时旧 done views 不会被错误 skip。
- legacy 无 sig 路径仍然保持旧的 done-skip。
- 新三视图生成后，下游 first frame 真正读取新角色参考，而不是只更新 project.json。
- 检查中文角色 safe filename / views_meta 是否可能造成读取旧目录的问题。

C. F-2 planner checkpoint
- scene 级 approved checkpoint 在 planner 成功后立即持久化。
- 在第二个 scene 中途失败或重启后，第一个 scene 的 planner 不会再次调用。
- 不存在内存状态看似防重、进程重启后实际重复的问题。

D. Event B / crash restart
重点审查：
- char-sync 中途硬重启
- JobQueue running → queued 恢复
- 同一 job_id 是否真的复用
- _INFLIGHT_POSTCOMMIT 是否可能产生双 kickoff
- 已完成角色是否可能被重新生成
- blocked / terminal / pre-commit execution 是否可能被误 kickoff
- 是否可能创建第二个 execution

如果成本允许，使用 mock/deterministic 环境重现双角色：
A 已完成，B 正执行 → crash → restart。
不要默认重新跑一次完整 live Agnes Golden Path，除非前面发现可疑行为。

E. K3 真正 F5
使用真实浏览器 hard refresh，不是 Vue route 切换：
- 当前项目身份仍然恢复正确
- identityReady 前不能恢复错误项目的 ChangeSession
- 无效/删除项目的 localStorage 值不会形成循环
- session/execution 真相必须继续来自后端，而不是 localStorage

F. Agent 写意图
从 Floating Bar / Specialist 输入自然语言创作修改：
- 必须进入 durable ChangeSession
- “换回 / 恢复到 / 外观 / 短发 / 装甲”等真实请求不应误落普通聊天
- 页面已有 scene/character scope 时不能再次问“哪个场景/哪个角色”
- 没有 scope、文本也没有指向时，应确认当前行为与已记录限制一致
- 普通只读问题不能误创建 ChangeSession

G. Runtime Truth
重点寻找是否存在任何路径：
LLM 文本 → 直接让 UI 显示 running/completed。

运行中、完成、计数、blocker、阶段必须来自：
ChangeSession / ChangeExecution / manifest / JobQueue 等持久化或确定性状态。

特别检查：
- sending
- cs.isActive
- cs.executionActive
- restoreActive
- running card
- timeline
- 刷新后恢复

H. 一次批准后的自动推进
确认一次批准以后：
revalidate
→ workspace replay
→ canonical commit
→ character_sync
→ production_planning
→ targeted production
→ compose

后续不应为了普通内部步骤继续要求用户确认。

但真正的 ambiguity、方案批准、活跃 execution 上的新变更冲突仍应等待用户。

I. §15 活跃执行中的新变更
确认不是静默修改已批准计划，而是明确两种选择：
- 排队等当前执行完成
- 停止当前执行并重新规划

检查两种选择是否真的有后端语义，不只是 UI 文案。

J. bounded regeneration / project isolation
重点尝试：
- s01 被修改，s02 不应因为 convenience 被重新生产
- 项目 A / B 同名角色
- A 的 session/execution/job 不得污染 B
- 页面刷新与项目恢复不能把旧项目状态显示到新项目

K. 已知限制不能伪装成已解决
确认下面这些仍被诚实记录，而没有半修状态：
- 创作历史版本不可知
- char-sync 单角色 all-or-nothing
- 中文角色 safe filename 目录问题
- 小视口 Floating Bar 遮挡
- 无 scope 的模糊变更 blocked 文案
- blocked session 被视为 alive
- jobs.json 无限增长
- update_shot 非原子读改写
- 明文 API key

关于 API key：
- 可以确认是否仍为历史暴露值
- 绝对不要在输出中打印 key 内容
- 只报告“需要轮换”

完成上述独立检查后，再阅读：

- AI-7C-H_COMPLETION_REPORT.md
- AI-7C-H_FINAL_HANDOVER.md

逐项对账，专门寻找：
1. 报告声称验证但实际没有证据的项目
2. 报告与最终代码不一致的项目
3. completion report 与 final handover 相互矛盾的地方
4. 已知限制是否遗漏
5. PASS Criteria 是否存在任何一项实际上未满足

特别检查：
Golden Path 项目最终 ChangeSession 状态与报告描述是否一致，
解释为什么 handover 中记录的 session 最终状态不会削弱已完成执行和恢复证据。

最终只给我一个结论：

PASS
或
BLOCKED

如果 PASS：
列出你实际试图攻击但没能推翻的关键不变量，
以及仍然接受的 known limitations。

如果 BLOCKED：
只列出真正足以阻止进入 AI-7D 的问题，
不要把小 UI 瑕疵或已明确接受的限制升级成 blocker。

不要修改代码。
不要提交 commit。
不要开始 AI-7D。
