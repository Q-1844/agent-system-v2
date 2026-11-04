# AI-7C-S / S5 最终稳定性收口

继续当前 AI-7C-S，不开始 7D，也不要扩展任何新产品功能。

当前已知事实：

* pytest：99 passed，另有 1 个既有 flake `test_case7`，不是本轮新回归。
* typecheck：0 个本轮新错误。
* 后端可干净启动，L1 handler registration 与 execution 改动未破坏启动。
* D1 Live Character Sync 已 PASS：
  `character change → execute → commit → production character_views handler → real Agnes → front/side/back → rembg → Character.views done/url/local → production_planning`
* M4 `scenes_to_run` preflight 漏查已经修复。
* 当前最明确的剩余 gap：已有 S2 主要证明了直接函数路径，还没有完整证明公开 HTTP endpoint wiring。
* 这一阶段的目标不是增加测试数量，而是证明真实用户运行路径、恢复路径和幂等性足够可靠。

请先查看当前 git 状态和最新实现，以磁盘代码为准。已有正确实现不要重新设计。

## S5-1：公开 HTTP Endpoint 集成

补一个小而真实的 B 级集成层。

必须从实际 FastAPI endpoint 进入，而不是直接调用 execution_service 内部函数。

至少证明实际公开链路：

`POST execute`
→ approved plan revalidation
→ semantic execution
→ commit
→ execution 创建/推进

以及：

`POST resume`
→ 正确返回/进入 kicked_off 或当前真实协议对应状态
→ 不创建重复 execution

随后通过真实 GET/poll endpoint 观察 execution stage 至少推进多个状态，而不是只验证一次函数返回。

Agnes 在这一层允许 mock，但只在 Agnes/network client 边界 mock。不要把 execution service、router 或 production wiring 本身 fake 掉。

这一部分尤其检查刚修过的 `scenes_to_run` preflight 确实经过 HTTP 路径生效。

如果发现 endpoint 与内部函数行为不同，以真实 public API 行为为准修复。

## S5-2：D2 Live Soak

完成后运行针对真实恢复和竞态的 soak。

重点场景：

### A. Character Sync 中途重启

发起一个会进入 Character visual sync 的真实 execution。

在 Character sync 已经开始、但整个 execution 尚未完成时：

* 记录 execution/run/job/character 当前状态；
* 重启后端；
* 恢复系统；
* Resume 或让现有 recovery 机制继续；
* 确认最终正常完成。

最重要的不变量：

**同一个已经成功提交或完成的 Agnes Character generation 不能因为 restart/resume 被重复执行。**

请用调用记录、job identity、生成资产或现有持久化信息证明，而不是只看最终页面“完成”。

不需要为了压力测试故意大量消耗 Agnes；一个最小角色案例足够。

### B. Execute / Resume 快速重复操作

模拟用户快速点击：

* Execute 两次；
* Execute 后马上 Resume；
* Resume 连续触发；
* polling 与按钮操作碰撞。

最终只能有一个合法 execution 流继续。

不能出现：

* 两个 ProductionRun；
* 两份 Character generation；
* 同一 semantic operation commit 两次；
* UI 一份、服务器另一份状态。

### C. 项目隔离

建立项目 A / B，允许存在相同角色名。

确认：

* A execution 只能写 A；
* B execution 只能写 B；
* Character views 不串；
* affected production scope 不串；
* resume/recovery 后也不能因为角色同名串项目。

### D. 角色重新生成

角色已有旧三视图和已有生产资产后，修改外观并重新走 Character sync。

确认：

* 角色当前 views 更新为新版本；
* 旧首帧、旧视频、旧 generation history 不被偷偷删除或覆盖；
* 后续新 production 使用新角色参考；
* 已完成旧结果保持可追溯。

### E. 失败 / 重试一致性

选择一个可控失败点做数轮：

成功 → 失败 → retry/resume → 成功。

最后检查：

* project canonical state；
* change session；
* execution；
* production run；
* jobs；
* character views；
* affected scope

彼此一致。

不要为了“soak”机械循环几十次；目标是攻击恢复边界和幂等性。

## S5-3：真实浏览器 E2E

优先安装并使用 Playwright；如果项目已经有浏览器 E2E 基础则直接复用。

浏览器必须连接：

* 真实本地前端；
* 真实本地 FastAPI。

不要把整套后端用 route mock 替代。

重点覆盖 5 个用户行为：

1. Execute 后经过多个 polling tick，进度仍继续推进，不在第一个 tick 后冻结。
2. execution 正在运行时刷新浏览器，页面恢复当前 execution，而不是变成“什么都没有”。
3. 用户快速重复 Execute / Resume，不产生第二条执行链。
4. stale / blocked execution 显示正确恢复动作，不把不可 Resume 的状态伪装成“继续执行”。
5. execution completed 后刷新，仍显示真实完成状态，不重新执行。

优先用用户真正能看到和点击的元素验证，不依赖 Pinia 内部变量或 CSS class 证明成功。

如果当前 Claude Code 环境因为权限、浏览器安装或 `EPERM` 无法运行 Playwright：

* 不要为了环境问题改产品代码；
* 记录准确 blocker、命令和错误；
* 把浏览器验证标记为 `BLOCKED BY ENVIRONMENT`；
* 保留一个清晰的 Playwright case / manual case 清单，供 Trae 或另一个有浏览器环境的 reviewer 执行。

## S5-4：收口

以上完成后：

* 跑一次现有全回归；
* typecheck / build 做现有项目正常检查；
* 不为了清掉与本轮无关的历史 flake 或旧 TS 错误扩大范围；
* 检查 git diff，确保没有调试代码、真实 API Key、临时项目或生成资产进入提交。

然后生成一份：

`AI-7C-S_COMPLETION_REPORT.md`

报告重点不要只写 passed 数量，要明确分层说明：

* direct function proof；
* HTTP endpoint proof；
* browser proof；
* restart/recovery proof；
* mocked Agnes proof；
* real Agnes Character sync proof。

最后给出明确结论，只允许：

`AI-7C-S PASS`

或

`AI-7C-S INCOMPLETE / BLOCKED`

不能因为 pytest 很多就把缺失的 browser / HTTP / recovery 证据当成通过。

如果 PASS，再生成：

`AI-7C_FINAL_HANDOVER.md`

只记录稳定事实：

* 7C 最终用户路径；
* ChangeSession 与 ChangeExecution 生命周期；
* Execute / Resume / refresh 行为；
* canonical commit；
* Character / Audio sync；
* affected-production planning；
* scoped ProductionRun；
* restart recovery；
* idempotency；
* project isolation；
* 真实 Agnes Character path；
* public HTTP / browser path；
* 已知限制；
* 7D 可以依赖的不变量。

完成 7C Final Handover 后停止。

**不要开始 7D。**
