# Director Sub-Agent Architecture V1 · Part 3：实现报告

> 分支 `design/subagent-v1`。基线：Part 1（`5edc0e1` + Errata `1c200be`）+
> Part 2 rev2（`18bd415` + `972efb9` pre-detach）+ 接力可写性（`0f106c6`）+
> spike（`937603c`，14 场景全 PASS）。
> 本轮：实现 → mock 测试 → 全量回归 → 报告。设计已冻结，未重新设计；
> 实现偏差全部记录于 §3 DEVIATIONS。

## 1. 实现摘要

七阶段分项 commit（各自可回滚）：

| 阶段 | commit    | 模块                                                      | 内容                                                                                       |
| -- | --------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 1  | `3d9d221` | `src/agents/subagent/fingerprints.py`                   | per-scene 派生指纹（Part 2 §4.2 正式定义；E5 复用关系；basis 三元组）                                       |
| 2  | `3c51758` | `src/agents/subagent/diff_engine.py`                    | DiffEngine：阶段 C 复查 → pre-detach → delete → create → reorder → update → 终态断言；错误分类学；D4 可重算 |
| 3  | `1c06f02` | `src/agents/subagent/registry.py`                       | SceneAgentRegistry + memory + delegations + proposals + global\_memory；三节点惰性检测           |
| 4  | `15a5277` | `src/agents/subagent/tools.py` + director.py 接线         | MD 侧 4 工具 + 挂载门控（bit-for-bit）+ 资格闸门四检查                                                   |
| 5  | `40ca621` | `src/agents/subagent/instance.py` + `_run_tool_loop` 扩展 | 一次性实例执行链 + proposal.submit（E1–E6）+ extra\_executors / wall\_clock\_s                     |
| 6  | `a994d2e` | director.py 收尾 + session\_governance.py                 | 治理扁平化计数（勿双计）/ reground 条件扩展 / Clean Review 联动                                            |
| 7  | `f7f633d` | tests + registry 零痕迹修正                                  | bit-for-bit 抽查 / 上下文经济 / resume e2e                                                      |

**一句话**：`subagent_enabled`（settings.json 顶层键，默认 false）开启后，
Main Director 获得 `scene.delegate / proposal.read / proposal.commit /
proposal.reject` 四工具；Scene Agent 一次性实例读 canonical 后经
`proposal.submit` 提交端态提案；MD 裁决后 DiffEngine（零 LLM）把端态确定性
转换为既有 shot 原语串行提交；decision memory（accepted-only，带前提指纹）
跨 delegation 持久；一切生命周期检测惰性 + 确定性。关闭开关 = 主链路
bit-for-bit（快照测试锁定）。

## 2. 模块映射（设计 → 实现）

| Part 2 设计对象           | 实现位置                                                                        | 备注                                                                                                 |
| --------------------- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| §2 DelegationContract | `tools._execute_scene_delegate`（闸门+构造）+ `registry.save_delegation`          | contract 字段 = delegation record（basis 三元组 / task\_brief / origin）                                  |
| §2.3 BorderPack       | `instance._render_border_pack`                                              | window\_k=2、total\_shots、null 语义显式                                                                 |
| §3 实例执行链              | `instance.run_scene_agent_instance` + `build_instance_instruction`          | §3.2 模板全段落地（注入测试锁定）                                                                                |
| §4 ProposalReturn     | `instance._make_submit_executor`（E1–E6）+ `registry.save_proposal`           | 端态 chain:bool 表达学；防御性解析（平铺字段收进 fields）                                                             |
| §5 ReconcileDecision  | `tools._execute_proposal_commit / _reject`                                  | accept/rebase/reject/partial；decision\_rationale 必填；resume 显式                                      |
| §6 DiffEngine         | `diff_engine.run_diff_commit`                                               | rev2 拓扑序（pre-detach 先行）；planned/executed 区分；journal\_cb 三时点                                        |
| §7 Registry           | `registry.SubagentStore`                                                    | 五类落盘物全部 atomic + version；三节点惰性检测；单段单飞句柄                                                            |
| §8 全局决策记忆             | `registry.append_global_memory` + `session_governance.reground_session` 条件段 | 锚定 reconcile 工件；空 = seed 逐字节一致                                                                     |
| §10 治理集成              | `director.director_turn` 收尾扁平化                                              | 双计数器恰好一次；Clean Review 联动自动成立                                                                       |
| §11 阈值                | `registry` 顶部常量 + `instance` / `tools` 常量                                   | K=12 / M=3 / notes=4 / K\_g=8 / pressure=6 / 硬上限=6 / window\_k=2 / steps=32 / wall=600s / items=24 |

既有代码改动面（全部条件化，默认路径不变）：

- `director.py`：`_build_system` 尾部条件追加（`_SUBAGENT_SYSTEM_SUFFIX` 默认
  None）；`director_turn` 挂载点 + 收尾扁平化；`proposal.commit` 旗标分支；
  `_run_tool_loop` 的 `extra_executors / wall_clock_s`（默认 None 行为不变）；
  receipts\_meta 增 `executed_ops / proposal_id` 字段。

- `session_governance.py`：`reground_session` 携带物条件追加（空 = 逐字节一致）。

- `structure_service.py` / OCC / SW / CD / SSE：**零改动**（接力可写性已在
  `0f106c6` 落地，本轮直接消费）。

## 3. DEVIATIONS（偏差清单；均非架构变更）

| #   | 规格位置                                    | 偏差                                                                                                                                                                                                                                                                                         | 理由与影响                                                                                 |
| --- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| D-1 | Part 2 §4.4-E4（basis 防伪）                | `scene_slice_fingerprint` 是派生值、不出现在任何工具返回——防伪由**组件**承担：`basis.scene_content_token` 必须 ∈ 实例 `story.read` 真实返回的 revision 集合，`basis.shots_structure_token` 必须 ∈ `shot.list` 返回集合；`scene_slice` 本身做 == 当前现算（防过期）。                                                                              | E4 的语义（"basis ∈ 实例真实读到的指纹集合"）以组件等价实现；净效果更强——实例必须同时真实读过段内容与镜头结构。                       |
| D-2 | Part 2 §4.4 校验顺序                        | E5（read\_first）先于 E4（basis 防伪）执行。                                                                                                                                                                                                                                                          | 没读过时 read\_first 是更准确的错误（否则报 fabricated 误导修复方向）；§4.4 表格编号是列举不是强制执行顺序。                 |
| D-3 | Part 2 §2.2 `locked_border_constraints` | **复核后已补齐（rev2）**：`scene.delegate` 增加可选 `locked_border_constraints` 参数（≤4 条，确定性校验 applies\_to/statement，自动编号 bc\_1..），注入包渲染编号约束，submit 以 delegation 约束上膛 E3（require\_responses=bool(ids)），proposal 持久化 `constraint_ids` 供 commit 路径复验。测试 3 项（注入渲染 / E3 bounce / 持久化+复验）。无约束时注入保持占位（既有行为不变）。 | 已不再是留白；Part 1 设计对象 E（Border Ownership）的锁定半边完整落地。                                      |
| D-4 | proposal.commit 的收尾重读                   | 挂载后 `proposal.commit` ∈ DIRECTOR\_WRITE\_OPERATIONS → MD 回复含数字总结时可能触发既有"写后重读" nudge（一次 shot.list）。未按视觉生成工具先例豁免。                                                                                                                                                                            | DiffEngine 终态断言强于单镜重读，但 MD 的**口头总结**仍应以真实状态为准——保留 nudge 是诚实收尾，无害且低频。                  |
| D-5 | Part 2 §7.2 registry 计数落盘               | `add_structural_writes` 只在确实更新了已有 scene 记录时落盘——从未 spawn 过 Scene Agent 的项目（含开关关闭的全部正常创作流）不创建 `scene_agents/` 目录。                                                                                                                                                                            | bit-for-bit 的落盘面延伸（无 subagent 活动的项目零痕迹）；spawn 过的项目压力计数跨开关状态连续（设计内：压力是真实历史，与当时是否委派无关）。 |
| D-6 | Part 2 §0.1 Model A（同步 delegation）      | 实例预算 wall\_clock\_s=600 内同步完成；同 turn 多段 delegation 串行执行。                                                                                                                                                                                                                                   | 设计内（P2-8：V1 串行，并发执行留给后续版本，协议不变）。                                                      |

## 4. 测试结果

### 4.1 新增测试（全部 mock / ScriptedClient，不依赖真机 API）

| 文件                              | 项数     | 覆盖                                                                                                                                                      |
| ------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| test\_subagent\_fingerprints.py | 9      | 确定性 / 内容与接力敏感 / 段级作用域 / 与 `_scene_token`、`_shots_token` 同源一致 / 白名单漂移哨兵                                                                                  |
| test\_subagent\_diff\_engine.py | 25     | spike S1–S11 语义全覆盖（S4b/S9b 转 schema 不可表达）/ pre-detach（S11 + receipt 可见）/ partial 链切 / basis stale / D4 resume 幂等收敛 / 约束回应 / planned-executed 区分 / 防御性解析 |
| test\_subagent\_registry.py     | 9      | E2/E4/E6 入库规则 / per-shot 粒度隔离与 SW 改段整体失效 / 注入上限 / sweep retire+孤儿 / 单段单飞 / 压力聚合 / 全局记忆 / 原子+version                                                     |
| test\_subagent\_tools\_gate.py  | 10     | 默认关闭 bit-for-bit 快照 / mount-unmount 往返幂等完整还原 / 只增不改 / 闸门七条拒绝路径                                                                                          |
| test\_subagent\_instance.py     | 13     | e2e 交错（delegate→读+submit→commit：canonical/先建后锚/E2 入库/E3/状态机）/ reject 不入库 / E1–E6 bounce / supersede / 注入模板完整性                                           |
| test\_subagent\_governance.py   | 5      | 扁平化恰好一次（双计哨兵）/ Clean Review 联动 / reground 空=逐字节、非空=含出处 / 实例遥测独立行                                                                                        |
| test\_subagent\_bitforbit.py    | 3      | 关闭开关创作流 bit-for-bit / 上下文经济（全文不进主会话）/ resume e2e                                                                                                        |
| **合计**                          | **74** | **全绿**                                                                                                                                                  |

### 4.2 指定既有测试零回归

`test_director_agent.py`（21）/ `test_relay_editing.py`（8）/
`test_clean_review.py`（8）/ `test_zero_read_gate.py` /
`test_session_regrounding.py` / `test_context_telemetry.py` ——
**50 项全绿，零回归**。

### 4.3 全量回归

`python -m pytest tests/ -q --tb=no`（2026-09-03，45:56）：
**23 failed / 1077 passed / 1 skipped**。

- **数量对账**：上轮（四项治理增强后）995 passed / 23 failed；+ `0f106c6`
  relay 8 项 + 本轮 subagent 74 项 = 1077 passed；failed 数 23 持平。

- **失败名单比对**：23 个失败**全部**在 V1\_FROZEN.md 噪声清单内，**零新增
  失败名**——not\_configured 单例污染 ×3、test\_shot\_request\_rules ×5、
  test\_interface ×2、test\_full\_project\_autopilot ×7、test\_project\_autopilot ×6。

- **按判定规则单跑确认**：

  - not\_configured ×3 单跑 3 passed；test\_shot\_request\_rules 单跑 6 passed；
    test\_interface 单跑 3 passed → 基础设施噪声，非 regression；

  - autopilot e2e（full 7 + project 6）单跑仍失败（V1\_FROZEN 记录时为单跑
    全过）——**在基线 commit** **`972efb9`（本轮全部实现之前）上对照复跑，结果
    逐数字一致**（7 failed/6 passed 与 6 failed/6 passed），确证为当前环境下
    的既有 e2e 时序 flake（等待超时类：status=producing/running, error=None），
    **非本轮引入**。完整日志留存 `_full_regression_subagent.log`（未入库）。

## 5. 已知问题与后续

1. ~~\*\*D-3（locked constraints 未暴露）\*\*是 V1 功能面上最主要的留白~~
   ——复核（原窗口终审）时已补齐：工具参数 + 注入渲染 + E3 双时点上膛 +
   proposal 持久化 constraint\_ids，附 3 项测试。
2. **真机验证未做**（本轮全部 mock）：上真机后用 `_stress/stress_driver.py`
   同法压测 30+ 轮长程域（Part 1 §12-3 的遗留风险项）；Scene Agent 实例的
   真实 LLM 行为（提案质量、bounce 后自愈率）需真机观测。
3. **spike 文件保留**（`spike/subagent_diff_spike.py`，非生产代码），其 14
   场景语义已被正式测试覆盖。
4. **P2 清单全部沿用**（Part 2 §12 的 P2-1\~P2-9 在实现中未新增恶化项）；
   单进程判死假设（P2-7）依现状成立。

