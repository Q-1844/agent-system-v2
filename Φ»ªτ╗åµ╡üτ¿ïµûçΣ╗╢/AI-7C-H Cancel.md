# AI-7C-H Cancel / Restart Hotfix 执行文档

## 一、任务定位

本任务只修复 AI-7C-H 独立验收发现的一个 blocker：

**用户在已有 ChangeExecution 正在 post-commit 执行时选择“停止当前任务并重新规划”，旧执行可能被后台陈旧状态重新写回 running，并继续启动 ProductionRun，导致用户已经放弃的旧计划继续生产，同时新的计划被旧 ProductionRun 阻塞。**

这是 AI-7C-H 的局部 hotfix。

不要开始 AI-7D。

不要重新设计 Agent 系统。

不要重新审计或重构整个执行框架。

先阅读真实代码确认下面描述与当前 HEAD 一致，再实施；如果具体函数名已经变化，以当前代码为准。

---

# 二、先确认真实问题

重点阅读：

* `src/agents/change_protocol/execution_service.py`
* `src/agents/change_protocol/execution_models.py`
* ChangeExecution 的 store / persistence / lock 实现
* `src/store/changeSession.ts`
* `src/store/agentUi.ts`
* ProjectProductionRun / ProjectProductionManager 当前启动与取消逻辑
* 当前 JobQueue 的取消与恢复能力
* AI-7C-H 与 AI-7C-S 中已有 execution/restart/cancel 测试

重点确认当前是否仍存在：

```text
run_character_sync
或
run_production_planning

加载一次 ex
↓
执行较长任务
↓
使用旧的 ex 对象
ex.status = "running"
save_execution(ex)
```

以及：

```text
cancel_execution
↓
status = cancelled
cancel_requested = true
↓
后台旧 ex 随后 save
↓
cancelled 被覆盖回 running
```

同时确认当前：

```text
ChangeExecution
→ ProjectProductionRun
```

是否已经存在稳定、可持久化的关联 ID。

如果已经有，直接复用。

如果没有，而且无法可靠通过现有 origin / execution_id 找到准确的子 Run，可以给 ChangeExecution 增加**一个最小的 production_run_id / owned_run_id 之类持久字段**。

不要建立新的 Run registry。

---

# 三、本次采用的取消语义

## 1. cancellation 是请求，不是瞬间完成

用户点击停止后：

```text
running
↓
cancel_requested = true
↓
正在停止
↓
确认不会再产生新工作
↓
cancelled
```

不要在还有真实子任务运行时，让 UI 提前宣称：

```text
已取消
```

如果当前状态枚举不值得增加 `cancelling`，可以继续使用：

```text
status = running
cancel_requested = true
```

作为持久化语义。

前端把这个组合显示成：

```text
正在停止…
```

即可。

不要为了这一项再扩展复杂状态机。

---

# 四、最重要的不变量：取消不能复活

从本 hotfix 完成以后：

> 一旦 `cancel_requested=true`，普通 post-commit 代码不得再推进新的执行阶段。

> 一旦 Execution 已进入 `cancelled`，任何后台持有的旧 Python 对象都不得把它重新保存为 `running`。

不要只采用：

```text
reload
↓
if cancelled
↓
继续
↓
save old object
```

这种方式。

因为：

```text
reload running
↓
此时用户 cancel
↓
后台继续 save old running
```

仍然存在竞态。

---

# 五、使用最小的 guarded execution mutation

优先复用项目已经存在的 execution/session lock。

不要持锁等待 Agnes、Planner 或视频生成这种长网络请求。

锁只保护很短的持久状态修改。

建议形成一个很小的统一 helper，名字自行根据工程风格决定，例如：

```text
mutate_execution_guarded(...)
update_execution_state(...)
save_execution_if_active(...)
```

核心语义：

```text
获取 execution 对应的短锁
↓
从磁盘重新 load 最新 Execution
↓
如果已经 terminal：
    不允许旧任务把它改回 active
↓
如果 cancel_requested：
    不允许普通执行代码推进 stage / status
↓
只修改本次真正需要的字段
↓
atomic save
↓
释放锁
```

不要再让长生命周期函数在几分钟后：

```python
store.save_execution(old_ex)
```

整对象覆盖当前持久状态。

本 hotfix 不要求把所有历史 execution 代码全部迁移到 helper。

至少把会与 cancellation 并发冲突的 post-commit 路径迁移：

* character sync 完成记录
* character → planning stage transition
* planning 的逐 Scene checkpoint
* planning → ProductionRun transition
* run_id 记录
* execution completion / cancellation

如果检查发现还有同类的直接 stale save，会影响本取消路径，可以一起做最小修正。

不要借此进行全项目 store 重构。

---

# 六、让 `cancel_requested` 真正参与执行

在 post-commit 的自然工作单元边界检查取消。

至少：

```text
character_sync：
每个角色开始前
每个角色完成后
进入下一角色前
进入 planning 前
```

```text
production_planning：
每个 Scene 开始前
每个 Scene 完成后
进入下一 Scene 前
启动 ProductionRun 前
```

如果取消发生时一个 Agnes 外部请求已经真正提交：

第一版允许这个**已经开始的最小单元**自然结束，除非现有 JobQueue 已经提供可靠取消能力，可以安全复用。

但结束以后：

```text
不得启动下一个角色
不得规划下一个 Scene
不得进入下一 stage
不得启动新的 ProductionRun
```

已经生成成功并安全落盘的资产不需要为了“取消”而删除。

取消代表：

> 停止继续生产。

不是：

> 回滚已经真实发生的所有副作用。

---

# 七、关闭 ProductionRun 启动竞态

这是本次必须特别处理的一处。

可能存在：

```text
后台检查：没有 cancel
↓
用户 cancel
↓
后台 start_run()
```

因此：

```text
最终 cancel 检查
+
创建 ProjectProductionRun
+
把 run_id 关联回 ChangeExecution
```

这一小段必须和 `cancel_execution()` 使用兼容的同一把短锁，或者实现等价的原子语义。

目标必须满足二选一：

### 情况 A

cancel 先取得锁：

```text
cancel_requested = true
↓
run starter 随后取得锁
↓
看到 cancel_requested
↓
不创建 ProductionRun
```

### 情况 B

run starter 先取得锁：

```text
创建 R1
↓
立即持久记录 R1 属于 E1
↓
释放锁
↓
cancel 随后取得锁
↓
能够准确找到 R1
↓
向 R1 传播取消
```

绝不能出现：

```text
R1 已启动
但 ChangeExecution 不知道 R1 是谁
```

---

# 八、取消必须传播到已经存在的子 ProductionRun

如果 Execution 已经拥有 ProjectProductionRun：

```text
E1
└─ R1
```

用户取消 E1 时，要调用项目**现有的** ProjectRun cancel 语义取消准确的 R1。

不要重新写一套 ProductionRun cancellation。

不要通过：

```text
找当前项目任意 active run
```

来猜。

必须通过明确的 owner/origin/run_id 关联确定目标。

如果现有 ProjectRun cancel 本身是 cooperative：

允许：

```text
R1 cancelling
```

持续一段时间。

ChangeExecution 在 R1 仍会继续产生工作时也不要提前显示最终 `cancelled`。

只有当：

```text
Execution 自身不再推进
+
它拥有的 ProductionRun 已停止或确认不会继续产生新任务
```

以后，Execution 才进入真正 `cancelled`。

---

# 九、backend restart 时也要尊重取消

检查当前：

```text
recover_executions()
```

或对应启动恢复入口。

如果加载到：

```text
cancel_requested = true
```

不要按普通 active execution 恢复：

```text
character_sync
production_planning
```

继续执行。

恢复逻辑应该进入“完成取消”路径：

* 不重新启动新的角色同步；
* 不重新启动新的 Planner Scene；
* 如果存在 owned ProductionRun 且仍 active，继续请求/恢复其 cancellation；
* 当没有会继续运行的子生产后，将 ChangeExecution 收敛到 `cancelled`。

这样：

```text
用户点停止
↓
backend 此时崩溃
↓
重启
```

也不能让旧计划复活。

---

# 十、修改“停止当前任务并重新规划”的前端语义

检查：

```text
resolveChangeChoice('restart')
```

当前如果类似：

```text
await cancelExecution()
createNewSession()
```

或者 cancel 与 create 基本同时启动，需要修改。

第一版采用最稳逻辑：

```text
用户选择：
停止当前任务并重新规划
↓
POST cancel E1
↓
UI：
正在停止当前任务…
↓
持续读取后端权威 Execution / ProductionRun 状态
↓
确认旧执行已经进入安全 cancelled 状态
↓
再创建新 ChangeSession S2
↓
重新处理用户的新要求
```

不要在旧执行仍 stopping 时提前告诉用户：

```text
正在重新规划
```

也不要先创建 S2，最后让 S2 撞上：

```text
PROJECT_PRODUCTION_ACTIVE
```

正常 restart 路径应主动等待旧运行安全结束。

已有 `_active_production` 检查继续保留。

它仍然是安全兜底，不要删除。

但正常用户 restart 不应该靠撞到这个 blocker 才知道旧任务没停。

---

# 十一、UI 真相

运行卡至少区分：

```text
执行中
正在停止…
已取消
```

其中“正在停止”来自真实：

```text
cancel_requested=true
且 execution / owned run 尚未完全停止
```

“已取消”只能来自后端持久状态。

LLM 文本不能决定这些状态。

不要因为这个 hotfix 重做整个 Agent UI。

只补当前已有运行卡需要的状态映射和文案。

---

# 十二、不要扩大成新的基础设施项目

明确不要做：

* Temporal / Celery / Durable Functions 等第三方工作流迁移
* 新事件总线
* 通用 Workflow Engine
* Event Sourcing
* CQRS
* 全局 execution revision 系统
* 全局 transaction framework
* 重写 JobQueue
* 重写 ProductionRun
* 重写 ChangeSession
* 重写 ChangeExecution
* AI-7D
* Reviewer Agent
* 无关 UI 重构

当前项目已经有：

```text
durable ChangeExecution
ProjectProductionRun
JobQueue
atomic persistence
locks
restart recovery
```

本次只把已有组件的 cancellation 语义正确连接起来。

---

# 十三、必须攻击的几个竞态

测试不要追求数量，重点证明真实 blocker 消失。

## Case 1 — stale object resurrection

构造确定性 barrier：

```text
后台已读取 running Execution
↓
暂停在保存之前
↓
另一协程执行 cancel
↓
确认磁盘 cancel_requested
↓
释放旧后台继续
```

最终：

```text
Execution 不能恢复成普通 running
```

旧写入必须被 guarded mutation 拒绝或合并。

这是最关键测试。

---

## Case 2 — cancel during character_sync

至少两个角色。

```text
Character A 完成
Character B 正准备/正在执行
↓
cancel
```

期望：

* 已完成 A 不需要回滚；
* 不产生后续新的角色工作；
* Execution 不复活；
* 不进入 planning；
* backend restart 后也不继续旧计划。

如果 B 的外部调用取消不了，可以允许当前已开始的 B 单元结束，但结束后必须停止。

---

## Case 3 — cancel during planning

至少两个 Scene。

在 Scene 1 完成、Scene 2 尚未开始或正在开始时取消。

期望：

* 不再规划新的 Scene；
* 不进入 run starter；
* 不创建 ProductionRun；
* Execution 最终 cancelled。

---

## Case 4 — cancel vs ProductionRun creation race

强制制造：

```text
最终 cancel check
↔
start_run
```

并发。

无论顺序如何：

* 要么 R1 根本不创建；
* 要么 R1 创建后能够被 cancel 精确找到并停止。

不存在孤立 R1。

---

## Case 5 — ProductionRun 已经 active

```text
E1
→ R1 active
↓
用户 restart
```

期望：

```text
cancel E1
↓
cancel 精确 R1
↓
等待 R1 安全停止
↓
E1 cancelled
↓
创建 S2
```

S2 不应该再因为被放弃的 R1 而得到：

```text
PROJECT_PRODUCTION_ACTIVE
```

---

## Case 6 — stopping 中 backend restart

```text
cancel_requested=true
↓
backend restart
```

重启后：

* 不恢复旧生产链继续跑；
* 不复活 Execution；
* 继续收敛 cancellation；
* 最终允许新计划创建。

---

# 十四、回归边界

完成后重点跑：

* 本 hotfix 新测试；
* 原 AI-7C-H cancellation / active execution 相关测试；
* execution recovery；
* Character sync；
* production planning；
* ProjectProductionRun cancel；
* Agent restart choice 前端测试。

不要求为了获得“全绿数字”去修无关旧错误。

如果目标测试通过，再做一次现有相关 regression 即可。

不要把主要时间消耗在无关测试覆盖率上。

---

# 十五、浏览器验收

使用真实前端 + 真实 FastAPI。

不要求为了这个 hotfix 大量消耗 Agnes quota。

最重要的浏览器场景：

```text
已有 E1 正在真实运行
↓
用户提交一个新的修改
↓
选择：
“停止当前任务并重新规划”
↓
UI 显示：
“正在停止当前任务…”
↓
旧 Execution / owned ProductionRun 实际停止
↓
旧 Execution 没有再次变回 running
↓
新 ChangeSession 自动开始
↓
新的方案可以继续，而不是被旧 R1 阻塞
```

至少再执行一次：

```text
正在停止
↓
backend restart
↓
页面恢复
```

确认 UI 与持久状态一致。

---

# 十六、代码质量原则

这次优先选择：

```text
少量明确 helper
+
已有锁
+
已有持久化状态
+
已有 child cancel
```

而不是更多抽象。

如果发现为了实现该 hotfix 必须修改十几个完全无关模块，请先停下来重新检查设计。

正常情况下，本 hotfix 应主要集中于：

```text
execution service
execution state/store
ProjectRun 关联/取消
restart choice frontend
少量 targeted tests
```

---

# 十七、完成条件

这次只有四个用户级 Gate。

### Gate 1

用户请求停止之后：

**旧 Execution 永远不能复活。**

### Gate 2

停止请求之后：

**旧计划不能再启动新的 Character、Planner Scene、ProductionRun 或后续 production。**

已经启动且无法立即中止的最小外部单元可以自然收尾，但不能继续扩展工作。

### Gate 3

如果旧 Execution 已经拥有 ProductionRun：

**取消必须传播到准确的子 Run。**

### Gate 4

旧运行安全停止以后：

**新的用户修改能够重新规划和执行，不会被已经放弃的旧运行阻塞。**

以上四项同时成立，本 hotfix 才算完成。

---

# 十八、完成后输出

请输出一份简洁的：

```text
AI-7C-H_CANCEL_HOTFIX_REPORT.md
```

说明：

1. 原 blocker 的真实根因；
2. 最终 cancellation 状态语义；
3. 如何防止 stale Execution 覆盖取消状态；
4. 使用了什么锁，锁保护哪些短操作；
5. `cancel_requested` 在哪些边界被检查；
6. Execution 与 ProductionRun 如何建立精确关联；
7. 已启动 ProductionRun 如何被取消；
8. 为什么没有引入新的工作流框架；
9. restart 时如何处理正在取消的 Execution；
10. `restart` 前端为什么现在会等待旧执行真正停止；
11. 六个关键竞态测试结果；
12. 浏览器实际结果；
13. 修改的生产文件；
14. 是否存在仍未解决、会影响本语义的限制。

如果四个 Gate 全部通过：

```text
AI-7C-H CANCEL HOTFIX: PASS
```

然后：

```text
STOP.
```

不要开始 AI-7D。
