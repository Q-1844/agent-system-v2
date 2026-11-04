# AI-7C-S / S5 — Trae GLM-5.3 Independent Final Audit & Adversarial Acceptance

## 0. 你的身份与任务

你现在不是 AI-7C-S 的实现者。

你是一个**全新的、独立的最终审查者**。

当前仓库已经由另一套 Coding Agent 完成：

* AI-7A
* AI-7B
* AI-7C
* AI-7C-S
* AI-7C-S / S5 最终稳定性收口

实现者最终报告结论为：

> **AI-7C-S COMPLETE — PASS**

你的任务不是重复实现 AI-7C，也不是继续开发 AI-7D。

你的唯一目标是：

> **独立验证这个 PASS 是否真实成立，并尝试主动推翻它。**

不要把已有 completion report 当作事实来源。

它们只是“实现者提出的声明”。

真正的事实来源优先级是：

1. 当前 Git 历史和代码
2. 当前磁盘持久化数据
3. 当前真实后端行为
4. 当前真实前端行为
5. 浏览器实际可观察结果
6. 真实 Agnes 调用结果
7. 自动测试结果
8. 最后才是实现者报告

如果报告与代码或运行结果冲突：

> **以代码和运行事实为准。**

---

# 1. 当前阶段边界

必须先钉死：

```text
AI-7A       COMPLETE
AI-7B       COMPLETE
AI-7C       COMPLETE
AI-7C-S     COMPLETE / claimed PASS
AI-7D       NOT STARTED
```

你绝对不能：

* 开始 AI-7D
* 实现 Reviewer Agent
* 实现自动质量判断
* 增加新的创意循环
* 重构 AI-7C 整体架构
* 把本审计变成新功能开发

本轮结束必须 STOP。

---

# 2. 当前实现者声称的提交链

当前分支：

```text
refactor/shot-pipeline
```

据实现者报告，本轮 S5 相关提交为：

```text
8b64e6b
S5-2a
soak B：快速 resume 无 duplicate
soak C：项目隔离
soak E：失败 → blocked 一致性

a1dbfaa
S5-2b
live A：真实 Agnes Character sync 中途重启
job_id 跨重启复用
恰好 3 次调用
不重复生成

S5-2c
Character regeneration 保留旧 Shot 资产

d36d3b4
S5-3
Playwright browser
B1 polling 多 tick 不冻结
B2 remount / restoreActive 恢复
B5 completed 不重执行

96ddcc4
S5-4
AI-7C-S_COMPLETION_REPORT.md
AI-7C_FINAL_HANDOVER.md
```

此前实现者还声明已有：

```text
327b580
02a02d2
a35dfef
144171f
```

属于 S1-S4 / S5-1。

不要因为 commit message 写了这些内容就认为已经证明。

你必须检查真实 diff 和行为。

---

# 3. 实现者最终声明

需要逐条挑战以下声明。

## HIGH

```text
H1
execution polling 不再第一 tick 后冻结

证据声明：
C / Browser / B1
```

```text
H2
执行过程中刷新或组件重挂载后，
ChangeExecution 能恢复并继续

证据声明：
C / Browser / B2
```

```text
H3
真实 Character synchronization production path 已经真正运行

证据声明：
D / Live Agnes
job_id = 964eba120ca2
跨后端重启复用
共 3 次 Agnes Character view 调用
所有 required views：
done + url + local
无重复生成
```

## MEDIUM / LOW

```text
M1
公开 execute 路径真正接通
character-sync + production planning
```

```text
M2
ProductionExecutionManifest
对 bounded change 保持 bounded
不会整项目重算
```

```text
M3
resume / restart 不重复 Character generation
```

```text
M4
preflight scope 正确
```

```text
M5
PROJECT_CHANGED_AFTER_APPROVAL
或等价 stale blocked 状态
前端显示重新提案 / 重新规划
不再误导用户 Resume
```

```text
L1
Character sync production handler
真实注册于 routers/agents.py 等 production startup 路径
```

```text
L4
manifest 可在恢复时重算 / reconciliation
```

```text
L6
前端真实消费 authoritative eligibility / blocker
```

你的任务是尝试找到这些声明里的漏洞。

---

# 4. 审计纪律：默认只读

这是一个**独立审查任务，不是修复任务**。

默认禁止修改生产代码。

如果发现问题：

```text
不要立刻修
不要为了让测试通过修改实现
不要改变测试再宣布 PASS
```

先记录：

```text
Finding ID
severity
reproduction
expected
actual
root cause
affected files/functions
proof
suggested minimum repair boundary
```

除非用户之后明确要求你修复，否则只提交审计报告。

允许：

* 运行已有测试
* 启动/关闭开发服务
* 使用浏览器
* 使用 Playwright
* 使用 curl / HTTP 客户端
* 创建 disposable test project
* 创建临时测试脚本
* 创建临时日志或 trace
* 写最终审计报告

临时内容不要提交进 Git。

不要修改用户已有 WIP。

---

# 5. Git 安全与审计隔离

开始执行：

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git log --oneline --decorate -20
```

确认：

* 当前 HEAD
* 当前分支
* 上述提交是否存在
* 是否存在用户未提交内容

已知实现者报告存在未提交、明确排除在 AI-7C-S 外的内容，例如可能包括：

```text
src/agents/inquiry.py
src/agents/change_analysis.py
提示词.md
saved_characters
components.d.ts
```

其中 `normalize_module` 方向的 WIP 被实现者明确声明未进入 AI-7C-S commit。

不要删除这些文件。

禁止：

```text
git reset --hard
git clean -fd
git checkout -- .
git restore .
强制覆盖工作区
```

如果需要“绝对干净的 HEAD”验证：

优先创建临时 detached Git worktree，或者使用其他不会影响当前工作区的隔离方式。

不要为了审查把用户 WIP 清掉。

---

# 6. 首先审阅两份最终文档

找到并完整阅读：

```text
AI-7C-S_COMPLETION_REPORT.md
AI-7C_FINAL_HANDOVER.md
```

不要只摘要。

建立一张 claim ledger：

```text
Claim
报告中的证据
对应 commit
对应 production code
对应 automated test
对应 browser proof
对应 persisted evidence
是否可独立复现
Verdict
```

Verdict 只能是：

```text
VERIFIED
PARTIAL
UNVERIFIED
CONTRADICTED
```

不要使用“看起来合理”。

---

# 7. Commit-by-Commit 对抗审查

重点检查：

```text
8b64e6b
a1dbfaa
d36d3b4
96ddcc4
```

并回看必要的 S1-S4 / S5-1 提交。

针对每个提交回答：

1. 生产代码到底改了什么？
2. 测试到底证明了什么？
3. 是否存在“测试自己搭了一个现实中不存在的路径”？
4. 是否绕过 public endpoint？
5. 是否注入了 production 中不会出现的 fake generator？
6. 是否人为提前写入了恢复时才会存在的 persisted state？
7. 是否只让断言变绿，却没有修用户路径？
8. 是否有 branch 在测试中可达、production 中不可达？
9. 是否有 production branch 已经变成 dead code？
10. 是否存在测试 fixture 与真实 startup graph 不一致？

重点寻找：

```text
test-only fix
mock leakage
fake generator leakage
dead branch
unreachable recovery path
production registry mismatch
public endpoint bypass
hard-coded successful state
fake persisted evidence
```

---

# 8. 静态生命周期审查：H1 / H2

不要只运行现有测试。

重新阅读完整 frontend lifecycle。

至少跟踪：

```text
ChangeSession
UnifiedChangePlan
PlanApproval
ChangeExecution
ProductionRun
Job
```

检查：

```text
approved_for_execution
executing
waiting_user
blocked
failed
completed
cancelled
```

在前端到底分别意味着什么。

特别检查：

```text
src/store/changeSession.ts
ChangePlanReview.vue
restoreActive()
polling start/stop
execution polling
session polling
eligibility
blocked reason
```

主动寻找这种逻辑矛盾：

```text
proposal terminal
=>
被错误解释成
整个执行流程 terminal
```

必须确认：

```text
Proposal lifecycle
!=
Execution lifecycle
```

而不是通过一两个 status 特判偶然修好。

---

# 9. Browser Gate：必须用真实浏览器重新执行

Trae 有浏览器 / Preview 能力的话，必须使用。

不要只相信 Playwright 旧日志。

最终浏览器必须连接：

```text
真实本地前端
+
真实本地 FastAPI 后端
```

不能把整个后端 route mock 掉后宣布 Browser PASS。

## B1 — 多 polling tick

从 approved Plan 开始：

```text
Execute
→ execution begins
→ 等待多个真实 polling interval
→ UI 状态持续变化
```

确认：

* 第一次 tick 后没有 freeze
* 第二、第三以及后续 tick 仍在请求
* execution state 继续刷新
* console 没有 silent exception
* network 没有停止请求
* 页面状态与后端 GET execution 一致

不要使用“页面一直显示生成中”作为充分证明。

要证明状态真的持续更新。

---

## B2 — 真正刷新页面

执行进行中：

```text
F5 / reload
```

而不是只 Vue component remount。

如果当前产品已知：

```text
projectName 不跨 full-page reload 持久
```

请把两个问题分开审计：

### 产品已知限制

```text
projectName persistence
```

### AI-7C-S 必须成立的不变量

```text
当 execution context 可恢复时，
restoreActive / execution restoration 本身不能错误过滤 approved session。
```

如果 full reload 因 projectName 的已知限制无法完成完整恢复：

* 不要错误判定 H2 自动 FAIL
* 但必须说明真正阻断点
* 再使用 in-app remount / route remount 独立验证 H2 的 execution lifecycle

同时判断这个已知限制是否已经严重到影响 AI-7C 的核心产品承诺。

如果是，提升为 blocker；
如果不是，保留为 known limitation。

---

## B3 — approved 但尚未执行

```text
Plan approved
→ 不点击 Execute
→ 离开组件
→ 返回
```

必须仍然看到：

```text
Approved Plan
Execute Plan
```

不得：

* 自动执行
* 丢失 approval
* 错误显示 completed
* 错误隐藏 Execute

---

## B4 — stale blocked

构造：

```text
approve Plan
→ 改变 canonical creative truth
→ Execute
```

必须：

```text
blocked / PROJECT_CHANGED_AFTER_APPROVAL
```

前端不能显示正常的：

```text
Resume
Try to resume
```

正确行为应该指导：

```text
重新生成 / 更新 Change Plan
→ Review
→ Approve
```

---

## B5 — completed 不重执行

```text
execution completed
→ 重挂载 / 返回页面
→ 恢复 completed state
```

必须：

* 不创建新 ChangeExecution
* 不重新 creative commit
* 不重新 Character sync
* 不重新 production
* 不增加新的 Job
* 不改变已完成的 execution_id

---

# 10. 浏览器测试方法

优先使用：

* 用户可见 role / label / text locator
* web-first assertions
* network observation
* console observation
* backend state cross-check

避免：

```text
大量固定 sleep
只看 CSS class
只看 Pinia 内部变量
只看 screenshot 颜色
```

当失败或出现 flaky：

保留：

* Playwright trace
* console log
* network log
* execution_id
* project
* session ID
* timestamp

成功路径不需要为追求“重型感”对所有 case 永久开启完整 trace。

失败证据要足够复盘。

---

# 11. Public Path Trace：M1 / L1

从用户真正触发的 public execute endpoint 开始。

完整跟踪：

```text
frontend Execute
↓
public POST execute
↓
ChangeExecution
↓
revalidation
↓
exact approved-operation replay
↓
canonical commit
↓
ProductionExecutionManifest
↓
Character synchronization
↓
Production planning
↓
scoped ProductionRun
↓
existing JobQueue / Producer
```

不能从某个内部：

```text
execution_service._helper()
```

开始，然后说 public path 已连接。

必须证明：

### affected_production

需要 Character sync 时：

```text
public execute
→ 真正进入 Character sync
```

需要 planning 时：

```text
public execute
→ 真正进入 Planner
```

### creative_only

必须：

```text
0 Agnes generation
0 ProductionRun
```

但 creative changes 正确提交，production consequences 正确报告。

---

# 12. Character Sync 注册审查

独立确认：

```text
谁注册 Character-sync handler？
什么时候注册？
真实 application startup 是否执行注册？
public runtime 是否拿到同一个 registry？
```

实现者声称：

```text
routers/agents.py:66
```

有 production registration。

不要相信行号。

重新追踪 startup dependency graph。

尝试证明：

```text
server startup
→ handler registry
→ character-sync kind
→ production handler
```

如果测试手工 instantiate handler：

那不算 production registration 证据。

---

# 13. Character Sync 真路径：H3

这是本轮最关键的一项之一。

检查实现者声明：

```text
job_id = 964eba120ca2
```

不要因为这个字符串存在日志中就判定 PASS。

如果对应 durable artifacts 仍存在，验证：

* execution record
* Job record
* Character state
* views
* local files
* URL
* timestamps
* generator lineage

确认真实 path：

```text
public AI-7C execution
→ Character sync stage
→ registered production handler
→ real Character view generator
→ existing Character page generation workflow
→ Agnes transport
→ persisted Character views
```

必须特别检查：

```text
_real_character_view_generator
```

或当前生产等价实现。

不能在真正需要证明的层上替换成：

```text
_fake_view_generator
```

---

# 14. Live Agnes Gate

如果当前环境仍配置有效 Agnes credentials：

执行一个**最小、针对性的真实 Character-sync live acceptance**。

不要无意义生成大量视频。

推荐：

```text
1 个 disposable project
1 个 Character
1 个 appearance-only approved change
最小 affected scope
```

通过正常 AI-7C public execute 路径。

证明：

```text
approved appearance change
→ execute affected_production
→ canonical creative commit
→ manifest requires Character refresh
→ real production handler
→ real Character workflow
→ real Agnes image request
→ persisted views
→ execution checkpoint
→ downstream planning consumes synchronized truth
```

不得打印真实 API Key。

不得把凭证写入报告。

如果当前凭证不可用：

把这一项记为：

```text
NOT RE-RUN
```

然后审查此前 D-layer 持久化证据是否足够可信。

不要伪造 live PASS。

---

# 15. Restart / Resume 重型审查

这是本次 Trae 审计最值得投入时间的地方之一。

不要只重启一次。

创建 disposable project / execution，覆盖以下时间窗口。

## R1 — Character sync 正在执行时重启

观察并记录：

```text
execution_id
job_id
character
required views
已完成 views
未完成 views
```

重启后：

* same execution_id
* same logical job
* completed views 不重新生成
* missing views 继续
* 不多调用 Agnes
* state 最终一致

---

## R2 — Character sync 很快完成后立即 resume

这是实现者声称的 soak B。

重复多轮：

```text
complete
→ immediate resume
→ resume again
```

验证：

```text
0 duplicate logical generation
0 extra durable completion
```

---

## R3 — planning 创建前重启

验证：

* manifest 不丢
* Character sync 已完成状态不丢
* planner 正确创建一次

---

## R4 — planner 已创建 / stage marker 未完全推进时重启

验证：

* planner 不重复创建不一致副本
* durable state reconcile 正确
* execution 能继续

---

## R5 — completed execution 后反复 restore/resume

至少多轮：

```text
restore
restore
restore
```

不能再次产生 side effect。

---

# 16. Soak：不要只“跑久”，要攻击幂等性

允许做长时间 / 重型测试，但目标是寻找重复工作和生命周期竞态，不是刷测试次数。

建议对 FakeAgnes / mocked-network 的 production-code-path scenario 做多轮 soak。

例如：

```text
20–50 个 execution cycles
```

根据机器性能自行调整。

随机插入：

* route remount
* frontend re-entry
* execution GET polling
* duplicate resume
* backend restart
* very fast completion
* injected technical failure
* recovery
* second recovery

关键 invariants：

```text
same logical work
→ at most once durable side effect
```

统计：

* executions
* jobs
* generator calls
* planner creations
* manifest versions/recomputations
* ProductionRuns
* Character view writes

只要计数异常，就深入调查。

不要因为最终 state 看起来对就忽略中间 duplicate side effect。

---

# 17. Project isolation

重新挑战 S5-2a C。

至少两个项目：

```text
Project A
Project B
```

尽量包含相同：

* Character name
* Scene / Shot 形态
* 类似 execution

验证：

* A execution 永远不更新 B
* B resume 永远不读取 A manifest
* Character views 不串
* job/project key 不串
* current project state 不作为 late async callback 的错误目标
* durable paths 不共享错误目录

尤其检查：

```text
project + character
project + execution
```

相关 key 是否真的隔离。

---

# 18. Failure → blocked 一致性

重新挑战 soak E。

主动让 Character generation 或 production planning 出现可控失败。

验证三个层：

```text
persisted backend state
API response
frontend display
```

必须一致。

不能：

```text
backend blocked
frontend still running
```

也不能：

```text
backend permanently failed
frontend一直显示可 Resume
```

已知设计限制为：

```text
Character sync permanent failure
→ blocked
→ 无自动 retry
→ 需要重新 plan
```

确认代码真的如此。

不要擅自“优化”成自动重试。

---

# 19. Manifest 对抗审查

重点检查：

```text
ProductionExecutionManifest
```

它必须是 execution artifact。

不能变成第二 creative truth source。

重新验证：

## bounded change

在较大 fixture / project 中：

```text
只改变 1 个 Scene / 少数 Shots
```

确认：

* 不全项目 regenerate
* required dependent scope 没漏
* unrelated generated assets 不丢
* existing ProductionRun state 不被覆盖

## update_scene

针对真实 operation：

回答：

```text
creative change 是什么？
manifest action 为什么存在？
目标 Scene / Shot 是谁？
哪些 asset stale？
哪些仍有效？
```

不要接受“有 action 就算对”。

---

# 20. Manifest reconstruction / L4

模拟 durable crash window。

例如：

```text
canonical commit 成功
manifest 未完整持久化
```

或：

```text
manifest 已持久化
stage marker 未推进
```

恢复后必须：

```text
reuse durable truth
OR
deterministically recompute
OR
block on real ambiguity
```

不能：

* blind repeat generation
* trust stale in-memory manifest
* silently skip work
* expand to whole project

检查 manifest 能否从：

```text
approved operations
+
committed canonical truth
+
execution state
```

重新推导。

---

# 21. Character regeneration 不得破坏旧 Shot 资产

重新挑战 S5-2c D。

构造：

```text
已有 Shot
已有 first-frame / last-frame / video history
```

然后 Character appearance 更新并 sync。

确认：

* Character current views 更新
* 历史 Shot assets 保留
* 历史 VideoVersion 保留
* 不偷偷删除旧成果
* downstream affected-production 只创建新的需要结果
* history lineage 可解释

不要把“旧资产没有显示在 UI”误当成“文件没删除”。

检查真实 persisted model / files。

---

# 22. 已知限制的独立判断

实现者主动报告 4 个限制。

不要自动当作 blocker，也不要自动接受。

逐项判断严重度。

## K1 — done_views 粗粒度 v1

声明：

```text
Character 描述变化后
如果 view 已经 done
update_character 不清 views
则不会自动重新生成
```

判断：

* 是否真的是当前已知设计语义
* 是否与 AI-7C-S 目标矛盾
* 是否可能让 appearance-only change 错误地跳过必需 sync

这里要特别认真。

如果 appearance change 按 manifest 明确要求刷新，但 `done_views` 又错误阻止刷新：

这不是“小限制”，而可能是 blocker。

不要被“by-design v1”四个字说服。

---

## K2 — permanent failure → blocked

确认：

```text
无自动 retry
重新 plan 才能继续
```

判断是否与 UI 文案一致。

---

## K3 — projectName 不跨 full reload

判断：

* 只是已有项目导航限制
  还是
* 实际破坏 AI-7C refresh recovery 承诺

必须给出明确结论。

---

## K4 — normalize_module WIP 未提交

检查：

```text
src/agents/inquiry.py
src/agents/change_analysis.py
```

或真实 WIP。

回答：

1. AI-7C / 7C-S 是否真的不依赖这些未提交修改？
2. 从 clean HEAD 执行 AI-7C-S 是否仍成立？
3. WIP 是否只是未来 AI-7A analysis normalization？
4. handover 是否错误依赖了 WIP 行为？

如果 clean HEAD 失去 AI-7C-S 必需能力：

这是 blocker。

否则保持 deferred。

不要擅自提交 WIP。

---

# 23. Clean-HEAD 验证

因为当前工作区可能有未提交 WIP，

尽可能使用独立 worktree 对当前 HEAD 做一次最重要的 clean validation。

至少验证：

* 后端关键 AI-7C / 7C-S tests
* frontend build
* public API startup
* browser smoke
* Character handler registration

目标是证明：

> PASS 来自 committed repository，而不是当前未提交 WIP。

---

# 24. Regression 数量不是判据

实现者声明：

```text
498 passed
+ 1 pre-existing flaky
0 new regressions
```

你要验证，但不要把 `498` 当核心审计目标。

特别检查所谓 pre-existing flake：

```text
test_case7
AI-5A timing
```

做：

* 定位失败
* 重跑
* 查看该测试与本轮代码依赖
* 必要时执行多次

结论只能是：

```text
PRE-EXISTING FLAKE
NEW REGRESSION
UNKNOWN
```

如果无法证明是 pre-existing：

不要自动接受实现者解释。

---

# 25. Typecheck / build

实现者声明：

```text
typecheck
0 新错误

pre-existing：
unplugin / webpack type dependency
store/index.ts ×4
2 components
```

以及：

```text
npx vite build
PASS
30.34s
```

重新执行当前仓库真实命令。

目的不是修 pre-existing issue。

确认：

* AI-7C-S 改动没有增加新 type error
* production build 能完成
* browser 审查使用的是当前 build/source

不要为了让 typecheck 全绿顺手修 unrelated files。

---

# 26. Git hygiene

检查 7C-S 提交是否混入：

* API Key
* credentials
* real secret
* generated images/videos
* disposable projects
* browser trace
* temporary logs
* dist
* saved_characters
* unrequested prompt docs
* debug prints
* temporary feature flags

特别检查 git history，而不仅仅当前工作区。

如果历史 commit 中出现 secret：

立即标记 CRITICAL，但不要在报告中打印 secret 内容。

---

# 27. 独立对抗式代码审查

你必须专门主动搜索以下 9 类问题：

```text
1. test-only fix
2. dead / unreachable branch
3. lifecycle contradiction
4. fake generator leakage
5. production handler 未注册
6. test 绕过 public endpoint
7. recovery 仅在 refresh 前有效
8. restart 导致重复 external work
9. manifest 无法由 durable truth 重建
```

此外主动增加：

```text
10. late async callback 写错 project
11. completed state 被重新执行
12. blocked state 与 UI 不一致
13. current selection 与 historical asset 混淆
14. uncommitted WIP 隐式参与 production
15. broad scope regeneration
```

---

# 28. 如果 Trae 支持多个 Agent

可以使用，但只做独立只读审查。

不要多个 Agent 同时改代码。

建议最多拆成：

### Reviewer A — Frontend / Browser

负责：

* H1
* H2
* M5
* L6
* B1–B5
* console/network

### Reviewer B — Runtime / Recovery

负责：

* M1
* M2
* M3
* M4
* L1
* L4
* manifest
* restart
* idempotency

### Reviewer C — Evidence / Git / Handover

负责：

* commit diff
* clean HEAD
* report claim verification
* WIP dependency
* git hygiene
* regression claims

三者先独立完成。

主 GLM-5.3 再合并。

不要让后一个 reviewer 先读前一个结论，以减少确认偏差。

如果 Trae 的多 Agent 实现不适合隔离：

单 Agent 顺序完成即可。

---

# 29. Evidence Classification

重要结论必须标证明层。

统一使用：

```text
A
deterministic unit / protocol proof

B
integrated production-code-path proof
外部 Agnes 只允许在网络边界 mock

C
real browser E2E

D
live Agnes

E
independent manual/static review
```

例如：

```text
H1 VERIFIED
proof: C + E
```

不能：

```text
H3 VERIFIED
proof: A
```

因为 H3 需要真实 production/runtime 路径和 live evidence。

---

# 30. 不要隐藏 flaky

对于重复测试：

记录：

```text
first-run pass
first-run fail / retry pass
repeat failure
```

如果一个 case：

```text
第一次失败
retry 才通过
```

不要直接写 PASS。

写：

```text
FLAKY
```

然后寻找原因。

浏览器失败时保留 trace。

不要通过增加 retries 把 flaky 洗成绿色。

---

# 31. 长时间测试停止条件

不需要为了“重型”无限运行。

以下任一成立即可停止某组 soak：

```text
发现稳定可复现 blocker
```

或者：

```text
达到预设循环量
且 durable invariants 全部稳定
```

或者：

```text
继续执行只能重复相同证明，
信息增益接近 0
```

把计算资源花在高风险状态窗口，而不是无意义重复 happy path。

---

# 32. Final Verdict 规则

最终只能给出以下之一：

## CLEAR

只有当：

```text
所有 HIGH 独立成立
关键 M/L 不存在 blocker
public runtime 真连接
browser lifecycle 真成立
restart/idempotency 真成立
Character sync 真路径有足够证明
clean committed HEAD 不依赖 WIP
```

才能：

```text
AI-7C-S / S5
INDEPENDENT REVIEW: CLEAR
```

---

## CLEAR WITH NON-BLOCKING LIMITATIONS

允许存在：

* 明确已记录的 v1 限制
* 不影响 7C-S 核心承诺的 pre-existing issue
* 不属于 AI-7C-S 的未来优化

但必须明确说明为什么 non-blocking。

---

## BLOCKED

任何以下情况应 BLOCK：

* H1/H2 任一无法真实复现为已修复
* real Character sync 证据不成立
* production handler 实际未注册
* public execute 绕过 required stage
* restart 会 duplicate external generation
* bounded change 会 broad regeneration
* clean HEAD 依赖未提交 WIP 才能工作
* stale execution 仍允许危险 resume
* manifest recovery 会跳过必需工作
* current project / other project 发生状态污染

---

# 33. 最终报告结构

输出一份：

```text
AI-7C-S / S5 — TRAE INDEPENDENT AUDIT
```

至少包含：

## 1. Baseline

```text
branch
HEAD
git status
reviewed commits
environment
frontend/backend ports
GLM model used
```

## 2. Report claim verification

逐条：

```text
Claim
Evidence
Verdict
```

## 3. H1

* code path
* browser reproduction
* multi-tick proof
* verdict
* proof layer

## 4. H2

* restore path
* F5/remount distinction
* verdict
* proof layer

## 5. H3

* registration
* production generator
* real workflow reuse
* live evidence
* restart result
* verdict
* proof layer

## 6. M1–M5 / L1 / L4 / L6

逐条结论。

## 7. Public endpoint trace

完整写：

```text
Execute
→ ...
→ Production
```

## 8. Restart / soak matrix

列出：

```text
case
iterations
restart window
expected
actual
duplicate count
verdict
```

## 9. Browser matrix

B1–B5。

## 10. Live Agnes

写：

* 是否重跑
* 请求种类
* 不泄露 secret
* logical calls
* job id
* restart
* resulting views
* persistence

## 11. Project isolation

A/B 结果。

## 12. Manifest

bounded scope + reconstruction。

## 13. Known limitations review

逐项判断：

```text
ACCEPTED NON-BLOCKER
PROMOTE TO BLOCKER
NEEDS FOLLOW-UP
```

## 14. Clean HEAD / WIP dependency

必须明确回答。

## 15. Regression

* backend
* browser
* typecheck
* build
* flaky

## 16. Git hygiene

结果。

## 17. Adversarial findings

即使没有 blocker，也列出你主动检查的 15 类风险。

## 18. New findings

按：

```text
CRITICAL
HIGH
MEDIUM
LOW
INFO
```

列出。

每个包含：

```text
reproduction
evidence
root cause
impact
minimum repair boundary
```

## 19. Final decision

只能：

```text
CLEAR
```

或：

```text
CLEAR WITH NON-BLOCKING LIMITATIONS
```

或：

```text
BLOCKED
```

## 20. AI-7C-S freeze recommendation

如果 CLEAR：

明确写：

```text
AI-7C-S may be frozen.
Do not reopen without a concrete regression.
```

然后：

```text
STOP.
Do not begin AI-7D.
```

---

# 34. 最重要的审计态度

不要试图证明实现者是对的。

也不要为了体现审查价值故意找问题。

你的任务是：

> **用尽可能独立的事实，尝试推翻 AI-7C-S 的 PASS；如果经过静态审查、真实浏览器、restart/soak、public path、clean HEAD 和必要的 live Agnes 之后仍然推翻不了，再给 CLEAR。**

一次测试通过不是证明。

一个 commit message 不是证明。

一份 completion report 不是证明。

一个 fake generator 不是 live proof。

一个内部 helper 能运行，不代表 public product path 已连接。

最终判断必须建立在真实运行事实之上。

本轮结束：

```text
STOP
DO NOT BEGIN AI-7D
```
