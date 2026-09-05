# Producer V1 Phase 2 — Intelligent Coordination · 实现报告

> 阶段：把 Producer 从「能真实调用专业 Agent 的操作员」升级为「根据项目状态与执行结果
> 自主协调多个专业 Agent 的智能制片人」。
> 主体 = Producer prompt/上下文工程 + 极少量最小状态与管道修补 + 诚实的行为验收。
> 基线：`feat/producer-v1-bridge`（Phase 1 已提交于 `e4e274e`/`2ea64fb`/`334622d`）。
> Phase 2 增量提交：`e93e6ce`（prompt+最小代码 C）/ `d25eafe`（mock 测试）/ 本报告。

## ① Phase 1 baseline 确认

Phase 1（Specialist Bridge Core）已在 `feat/producer-v1-bridge` 提交，工作区干净（仅未跟踪
scratch 文件）。Phase 1 交付：`turn_registry.py`（中性活跃 turn 注册表）/ `producer.py`
（PRODUCER_SYSTEM 极简版 + 专用轻量镜像 loop `producer_turn` + `_delegate_specialist` 泛化
委派 + `_bridge_cb` 锁/emit 接线 + 结构化 Task Result + workflow/bridge_messages 持久化）/
`runtime.py` producer project-scope 重路由（门控 function-calling client）/ `routers/agents.py`
producer 流式分支（`_writes` 空集）/ `test_producer_bridge.py` §10 A-L 共 11 测试。

**Phase 2 起步前 grep 确认**：`producer.py` 无 `MAX_DELEGATIONS`/`TOTAL_`/`WALL_CLOCK`/
budget/cap——无硬上限可删（§5.2）；未调 `log_context_telemetry`（需补，§5.4）；
`_wrap_task_result` 的 `verified` 含 `or status=="completed"` 兜底（非纯 receipts 派生，§5.5
允许微调）。

## ② Phase 2 修改范围（文件级）

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `src/agents/producer.py` | 改 | **主体**。工作 A：重写 `PRODUCER_SYSTEM`（覆盖 §3 全部协调原则）；工作 B：重写三个 delegate 工具描述（ownership/适用/不适用/反模式）；工作 C 最小代码：C3 workflow 加 `last_coordination_summary`（Runtime 派生）；C4 `producer_turn` 补 `log_context_telemetry`；C5 `verified` 改为只从 receipts 派生。 |
| `tests/test_producer_bridge.py` | 改 | R1（replan 重复委派）/ R3（事实层级 verified 从 receipts）/ P1（prompt 弱断言）；R5 grep 扩展（`MAX_DELEGATIONS`）。 |
| `PRODUCER_V1_PHASE2_REPORT.md` | 新建 | 本报告。 |

**红线遵守**：三个 Specialist 文件 diff = 零业务改动（未 touch）；subagent/governance/OCC/
truth gates/locks/SSE/window/legacy 路径全未动。跨轮 workflow 恢复机制有意不做（per-turn
语义保持，session 历史 + bridge_messages.jsonl 已提供连续性——有意设计，非缺陷）。

## ③ PRODUCER_SYSTEM 核心原则清单

重写后的 `PRODUCER_SYSTEM` 覆盖 §3 全部原则（措辞自组织）：

1. **身份与权限**：动画项目 Producer/总制片协调者；完成项目级目标；方法=调用真实专业 Agent；
   读事实自己来，专业判断问 Specialist，专业修改交 owner；永不直接改 canonical。
2. **硬边界**：物理保证（工具面无写/生成工具），非提示词约束。
3. **决策循环**：理解目标→观察现状→决定下一步→委派→读真实 Task Result→评估影响→重新规划
   →完成检查→继续/结束/问用户；**每个 Specialist 返回后不机械执行原计划，回到原始目标重新判断**。
4. **目标锚定（3 例防"有工具就全用"）**：s06 分镜节奏→纯 Director；改剧情+同步分镜→SW 完成≠
   任务完成；全流程优化≠三个 Agent 各调一遍。
5. **Observe 纪律**：默认读轻量地图（inspect/list）；定位才局部 read；Specialist 完成先看 Task
   Result；明确单域可直接委派无需大量前置读。
6. **领域路由表（ownership）**：SW=Story/剧本/动机/对白/Scene叙事；CD=Character/外貌/服装/
   视觉身份/三视图；Director=Shot/分镜/构图/景别/运镜/分镜图/视频。
7. **动态路由与 Skip**：任意顺序+回头；**知道不调用谁也是智能**（示例：SW 改 s05/s06 动机未动
   人物外貌→skip CD）；禁止为"全流程"机械跑三 Agent。
8. **下游影响思考方向**（思考方向非机械规则）：Story变→考虑 Scene实际变化/Character/分镜/
   stale资产；Character变→考虑用该角色的 Shot/三视图/stale资产；Shot变→考虑 stale图视频。
9. **事实层级**：authoritative=status/verified/changed_refs/warnings/Runtime证据/canonical重读；
   参考=summary/assistant_message/建议；**Specialist说"已修改完成"≠ verified truth**。
10. **自然衔接（envelope）**：situation=发生了什么+为什么现在找你；示例 SW→Director envelope；
    **不复制 canonical 全文**（4000字→压缩几行）。
11. **Replanning 允许回头**：A→B→A 合法；示例 Director→SW→Director；已调用过≠不能再调用。
12. **Specialist 建议只是输入不是命令**。
13. **完成判断两方向**：①某 Specialist completed ≠ 用户任务 completed（回到原始目标检查）；
    ②"优化一下"≠无限完美（无明确价值的修改→停止）。
14. **用户询问门槛**：完整目标→自动继续不逐步请示；仅五类问用户；**问=正常结束 turn 在最终
    消息给问题，无 pause/resume API**。
15. **失败协调**：failed≠workflow 失败；不假装成功也不一失败就放弃。
16. **最终汇报结构**：调用了谁/为什么/做了什么/跳过了谁/变化/残留 blocker。

## ④ delegate 描述要点

每个 `*.delegate` description 含：**owns**（ownership 域）/ **适用** / **不适用**（指向其他
owner）/ **要点**（"对方会自己读最新 canonical——situation 写发生了什么+为什么找它，不要把
canonical 全文复制进去"）。保持条目式非散文。P1 弱断言确认三个描述各含 ownership 关键词
（SW: 剧本/动机；CD: 外貌/服装；Director: 镜头/构图）+ 不适用域 + canonical 反模式提示。

## ⑤ Observe/Decide/Delegate/Evaluate/Replan/Finish 的实现位置（prompt 承载）

Phase 2 的协调智能**全部住在 `PRODUCER_SYSTEM` prompt 里**（由 Agnes 的判断执行），未写任何
新 Runtime 决策逻辑：
- **Observe**：【Observe 纪律】节 + 只读工具面（project.inspect/story.*/character.*/shot.*）。
- **Decide**：【决策循环】+【目标锚定】+【领域路由表】+【动态路由与Skip】。
- **Delegate**：三个 `*.delegate` 工具描述 + envelope 五段（objective/situation/constraints/
  scope/user_intent，user_intent 首位锚定——Phase 1 既有，未动）。
- **Evaluate**：【事实层级】+ Task Result（receipts 派生 verified/changed_refs/warnings）回喂
  Producer loop 作为 tool result。
- **Replan**：【Replanning 允许回头】+【下游影响思考方向】。
- **Finish**：【完成判断两方向】+【最终汇报结构】+【用户询问门槛】。

## ⑥ 读取策略

prompt 明确：默认读轻量地图（project.inspect / story.list / character.list / shot.list）；
定位问题才局部 read（story.read / script.read / character.read / shot.read）；Specialist 完成
后先看 Task Result，只有判断下游影响时才局部重读 canonical；明确单域任务可直接委派无需大量
前置读。真机 Case 1 Producer 读 shot.list/story.read/shot.read 后委派 Director（略多于最小，
NON-BLOCKER）；Case 2 Producer 读 story.read 后委派 SW；Case 3 T4 退化为自读自报（NON-BLOCKER）。

## ⑦ 动态路由与 skip

真机证据：
- **Case 1 单域**：只委派 Director（纯镜头域），跳过 SW/CD，最终消息明确"Story 与 Character
  无变动，跳过其它 Agent"。
- **Case 2 跨域+skip CD**：SW→Director，跳过 CD（"用户明确要求角色外貌和服装不要改，且 SW 未
  将角色设定纳入"）。
- **Case 3**：只委派 Director（偏保守，未检查 story/character——NON-BLOCKER）。

## ⑧ 下游影响判断

Case 2 证据：SW 改 s03/s04 故事（changed_refs scenes=[s03,s04]）→ Producer 判断下游分镜受影响
→ 委派 Director 同步 s03/s04 镜头（changed_refs shots=[s03_c01,c02,s04_c01,c02]）。Director
的 envelope situation 引用 SW 变化（"Script Writer 已调整剧本正文…s01_c01 的景别偏松"）。
Director 自己读【最新】story.read（在 SW 写之后）——非 stale canonical。

## ⑨ replan

- **mock R1**（管道层）：Director→SW→Director 三连，turn_registry acquire/pop/acquire 同 specialist
  复用正确（第二次不 409）、bridge_messages 六条、task parent 链 t2.parent=t1/t3.parent=t2。
- **真机**：Case 3 T3 对同一 Director 重复委派 5 次（A→A→A，合法 replan 同 agent 回头）；
  T3→T4 策略切换（重复委派→停止委派自读自报）。replan 管道与策略切换均成立。

## ⑩ 完成判断

- Case 1/2：目标达成后正常停止，最终汇报含六要素。
- Case 3：T3 重复委派 5 次后停止（不无限）；T4 识别"无明确价值的新修改"后停止自报——符合
  "不无限优化"原则。但 Case 3 未能驱动 Specialist **实际修改**（见 ⑳ NON-BLOCKER N3）。

## ⑪ 询问门槛

prompt：【用户询问门槛】五类才问；"问=正常结束 turn 在最终消息给问题，无 pause API"。
真机三案例 Producer 均未中途请示用户（用户给了完整目标→自动继续）。无 pause/resume 代码。

## ⑫ 失败行为

prompt：【失败协调】failed≠workflow 失败。真机 Case 3：Director 多次返回 verified=False
（completed 但零真实写入）——Producer **未假装成功**（Task Result 诚实 verified=False，最终
汇报"发现的新问题需要优化"未声称已修复）。但 Producer 未能切换策略打破无效循环（NON-BLOCKER N3）。

## ⑬ workflow 状态变化（含 last_coordination_summary 决定）

**决定：加 `last_coordination_summary`，Runtime 派生**（§5.3 二选一，选加）。
`ProducerWorkflowStore.settle` 增 `coordination_summary` 参数；`producer_turn` finally 从
`receipts` 拼接 `"agent:status-summary首80字 | ..."`（非模型文本/非 CoT）。真机 Case 1：
`"director:completed-（分镜整体优化完成…）"`；Case 2：`"script_writer:completed-…"`。
workflow per-turn 生命周期不变（turn 结束置终态）；跨轮恢复有意不做。

## ⑭ context 纪律

新增 `log_context_telemetry` 调用（镜像 director_turn：`tel_history_chars`/`tel_system_chars`/
`tel_tool_io_chars`，best-effort 静默）。每 producer turn 落盘一行
`output/agents/telemetry/context_size.jsonl`。

## ⑮ 长执行检查（grep 证据）

```
producer.py 无 MAX_DELEGATIONS / MAX_DELEGATIONS_TOTAL / TOTAL_MAX_STEPS /
            TOTAL_WALL_CLOCK / MAX_WINDOW_RENEWALS
```
- `DEFAULT_MAX_TOOL_STEPS=1024`（renewable window，仅有的步数常量）
- 无总续期次数 / 无总步数 / 无总墙钟 / 无总委派次数上限
- cancel 有效（Phase 1 既有，未动）
- 真机 Case 3 T3 委派 Director 5 次未被任何上限拦截——证明无总上限

## ⑯ mock 测试结果

`tests/test_producer_bridge.py`：**14/14 绿**（11 Phase1 + R1 + R3 + P1）。
- R1 replan 重复委派（Director→SW→Director）：registry 复用/bridge 6 条/task parent 链 ✓
- R3 事实层级：SW 文本声称"已修改完成"但 script.update 过期指纹→ok=False；Task Result
  `verified=False`、`changed_refs` 空、canonical 未变、warnings 含失败写 ✓
- P1 prompt 弱断言：PRODUCER_SYSTEM 含完成判断/询问门槛/事实层级/skip/replan/失败协调关键词；
  三 delegate 描述含 ownership+不适用域+canonical 反模式 ✓
- R5 grep 扩展：test_I 增 `MAX_DELEGATIONS`/`MAX_DELEGATIONS_TOTAL` 断言 ✓
- R2（skip=test_G）/R4（envelope=test_D）/R6（cancel=test_J/ownership=test_H/持久化=test_K）复跑 ✓

## ⑰ 三案例真机结果（九项观察 × 3 + 证据引用）

前置：后端进程启动晚于最后 commit（22:35 startup complete，commit 后）；全新安全测试项目
`__producer_phase2_smoke`（4 场景/6 镜头/2 角色，受控种子）；先跑 read-only smoke 确认桥+prompt
加载（T0：project.inspect/story.list/character.list→结构化摘要，零委派零写，14.5s）。
三案例共用同一长存 Producer session（project-scope 按 agent_id 去重——设计如此，realistic）。

### Case 1（单域）T1：「故事和角色保持不变，只检查并优化目前分镜的镜头节奏和镜头语言…」

| 观察 | 证据 |
| --- | --- |
| 1. Producer 自己读项目 | T1 trace: shot.list, story.read×3, shot.read×6（inspect 在 T0） |
| 2. 路由正确 | bridge_messages: producer→director（receiver=director）|
| 3. 真实调用 | director session user_msg=1, n_tool_calls=30, receipts 存在 |
| 4. 读到真实返回 | receipt: director verified=**true**, changed_refs shots=[s01_c01,c02,s02_c01,c02,s03_c01,c02,s04_c01,c02]+scenes=[s04]；最终汇报引用变化 |
| 5. situation 传递 | N/A（单委派无第二条 envelope）|
| 6. Specialist 自己读 canonical | director trace: story.list/character.list/shot.list/story.read/shot.read |
| 7. 自主推进/回头 | 1 委派，正常推进停止 |
| 8. 正常停止 | 汇报：调用了 Director/做了什么/跳过 SW+CD/变化（6→8 镜）/无 blocker |
| 9. 零越权 | Producer trace 无写工具名；canonical 变更只在 director receipts |

**Case 1 判定：PASS。** 单域路由正确 + 跳过 SW/CD 有理由 + verified=true 真实变更 + ownership 干净。

### Case 2（跨域+跳过）T2：「后半段剧情节奏和人物动机有问题，先处理故事层再同步受影响分镜…角色外貌和服装不要改」

| 观察 | 证据 |
| --- | --- |
| 1. Producer 自己读项目 | T2 trace: story.read×4, shot.list×2 |
| 2. 路由正确 | bridge: producer→script_writer, producer→director |
| 3. 真实调用 | SW session user_msg=1 n_tools=9；Director n_tools=14 |
| 4. 读到真实返回 | SW receipt verified=true changed scenes=[s03,s04]；Director envelope situation 引用 SW 结果 |
| 5. situation 传递 | 第2条 director.delegate situation="Script Writer 已调整剧本正文…s01_c01 的景别偏松，重新检查后收紧" |
| 6. Specialist 自己读 canonical | SW: story.list/script.read/story.read；Director: shot.read + **story.read（在 SW 写之后，读最新）** |
| 7. 自主推进/回头 | SW→Director；+1 serial_delegate 拒绝（Director+SW 同步委派，Phase1 串行闸门正确拦截）|
| 8. 正常停止 | 汇报：SW 改 s03/s04 动机/ Director 同步 4 镜/ 跳过 CD（用户要求不改外貌服装）|
| 9. 零越权 | Producer trace 无写工具；SW 改 story/Director 改 shots 各归 owner |

**Case 2 判定：PASS。** 跨域 SW→Director 自然 handoff + 跳过 CD 有理由 + 下游影响判断（SW story→Director shot 同步 s03/s04）+ Director 读最新 canonical 非 stale + serial 闸门工作。

### Case 3（开放项目级）T3+T4：「请把当前项目整体检查和优化一下。你自己判断需要找哪些专业 Agent…不要强行调用全部 Agent」

| 观察 | 证据 |
| --- | --- |
| 1. Producer 自己读项目 | T3 trace 全 director.delegate（未前置读，靠 T1/T2 历史——NON-BLOCKER）；T4 trace: story.list/character.list/shot.list/shot.read×7 |
| 2. 路由正确 | 只委派 Director（偏保守，未检查 story/character——NON-BLOCKER N1）|
| 3. 真实调用 | T3: 5 次 director.delegate；T4: 0 次（退化自读）|
| 4. 读到真实返回 | T3 receipts 全 verified=**False**（Director 审查报告未写）；Producer 据 verified=False 升级指令（"执行实际修改不是只读报告"→"write操作"）|
| 5. situation 传递 | 6 条升级 objective/situation（从"检查"→"执行实际修改"）|
| 6. Specialist 自己读 canonical | director session(7756340ff) trace 有 reads；但部分委派 n_tool_calls=0（Director 从会话记忆答"审查报告"而非本轮重读——NON-BLOCKER N2）|
| 7. 自主推进/回头 | T3: Director 重复委派 5 次（A→A→A replan）；T4: 策略切换（停止委派，自读自报）|
| 8. 正常停止 | T3 5 次后停止；T4 识别"无明确价值新修改"后停止诊断报告——未无限优化 |
| 9. 零越权 | Producer trace 无写工具；Case 3 零 canonical 变更（全 verified=False）|

**Case 3 判定：NON-BLOCKER（N1/N2/N3）。** 协调对开放"整体检查优化"无效（Director 审查-not-
写入 + Producer 重复 re-delegate T3 / 退化自读 T4），但**有界、零越权、零假成功、 receipts
诚实 verified=False**。

## ⑱ 质量指标评价（基于真机证据，非模型自述）

| 指标 | 评价 | 证据 |
| --- | --- | --- |
| Routing Precision | **强** | Case 1 单域 Director-only；Case 2 SW→Director；Case 3 偏保守（仅 Director） |
| Unnecessary Delegations | **强** | Case 1/2 均有理由跳过（SW/CD），无机械全调用 |
| Handoff Quality | **强** | Case 2 SW→Director situation 引用上游变化；Director 读最新 canonical |
| Canonical Grounding | **中强** | Case 1/2 Specialist 自读 canonical；Case 3 部分 Director 从记忆答（N2） |
| Replanning | **中** | R1 mock 管道✓；真机 Case 3 T3 A→A 重复委派、T4 策略切换；但无真机 A→B→A 自然回头 |
| Completion Accuracy | **中** | Case 1/2 准确；Case 3 未驱动实际修改即停（N3） |
| Ownership | **强** | 三案例 Producer trace 零写工具；canonical 变更只在 specialist receipts |
| Long-run Readiness | **强** | 1024 renewable window + 无总上限；Case 3 T3 5 次委派未被上限拦 |

## ⑲ 全量回归

- **targeted 回归**（producer 影响面）：test_agents + test_director_agent + sw_native +
  director_min + character_designer + subagent_bitforbit + subagent_tools_gate + long_execution
  + producer_bridge = **192 passed / 1 skipped / 2 known-noise**（`not_configured_soft_fail`
  ×2，单跑即过，基线已知噪声）。
- **全量 `pytest tests/`**：34 failed / 1105 passed / 1 skipped（45min）。**零新增失败可归因
  Phase 2 改动**——34 failures 全部分类如下：
  - **后端 :8000 端口干扰**（真机验收时后端在跑，全量套件里启动 test server / hit :8000 的
    用例被冲突）：`test_ai_7d_d3_runtime_control`×11、`test_interface`×2、
    `test_shot_request_rules`×6、多数 `test_*_autopilot`×15。**停后端后单跑全部复绿**
    （test_ai_7d_d3_runtime_control+test_interface+test_shot_request_rules = 22 passed；
    test_case1_e2e_complete_project 单跑过）。
  - **基线已知噪声**（`V1_FROZEN.md`/memory 记录）：`not_configured` 族×3（单跑即过）+
    `test_case7_source_changed_blocked`（预存 AI-5A timing flake，stash baseline 仍失败）。
  - 上述用例均不引用 producer.py / unified_turn（grep 确认），与 Phase 2 改动无关。
  - **注意**：跑全量套件前必须停掉 :8000 后端，否则端口干扰制造假失败。

## ⑳ BLOCKER / NON-BLOCKER 清单

**无 BLOCKER。**

**NON-BLOCKER（进 backlog 不阻塞）：**
- **N1 路由偏保守（Case 3）**：开放"整体检查"任务下 Producer 只委派 Director，未考虑检查
  story/character。属路由偏保守，非越权。
- **N2 Director 审查-not-写入（Case 3）**：Director（真 Agnes）对"检查"类指令倾向返回只读
  "审查报告"+询问"是否修改"而非实际写入；部分委派 n_tool_calls=0（从会话记忆答）。Director
  侧行为，Producer prompt 无法直接矫正。
- **N3 Producer 重复 re-delegate + 退化自读（Case 3）**：T3 对返回 verified=False 的 Director
  重复委派 5 次（升级指令）未能驱动写入；T4 退化为自读自报。**prompt 的【失败协调】未显式覆盖
  "Specialist completed 但 verified=False"**——可在 backlog 加一句"连续 verified=False 时
  给一次性足够具体的实际修改指令，否则转向其他 Agent 或停止并如实汇报'未能让 Specialist
  实际修改'"。有界无假成功，故 NON-BLOCKER。
- **N4 前置读略多（Case 1）**：Producer 委派 Director 前读了 6 个 shot.read（略超最小，用于
  定位问题）。NON-BLOCKER。
- **N5 真机无 A→B→A 自然 replan**：replan 管道由 R1 mock 证明；真机三案例未出现 Director→SW→
  Director 式自然回头（Case 2 的 2nd SW 被 serial 闸门拦截；Case 3 为 A→A）。
- **N6 situation 偶有啰嗦**：Case 3 升级 objective 偏长。

## ㉑ 最终判定

**判定：PASS WITH NON-BLOCKERS。**

§10 PASS 标准（合取）逐条：
- 单域路由正确（Case 1）✓
- ≥1 条多 Agent 自然 handoff（Case 2 SW→Director）✓
- 能跳过（Case 1 skip SW/CD、Case 2 skip CD，均有理由）✓
- 结果影响下一步（Case 2 SW result→Director sync；Case 3 verified=False→升级/策略切换）✓
- 能 replan（R1 mock 管道 + 真机 A→A 重复委派/策略切换）✓
- 不过早结束（三案例均充分尝试）✓
- 不无限优化（Case 3 T3 5 次后停、T4 识别无价值修改后停）✓
- ownership 干净（三案例 Producer 零写工具）✓
- 证据真实（receipts 派生 verified/changed_refs，未采信自述）✓
- 无人为总上限（grep + Case 3 委派 5 次未拦）✓

无 BLOCKER（未直接改 canonical / 未固定机械 SW→CD→Director / 结果能改变下一步 / 无过早结束 /
handoff 未丢关键 / 未大面积信自述 / 无 stale canonical 错误协调（Case 2 Director 读最新）/
无新增 hard cap / 能稳定调用真实 Specialist）。

---

### 最终回答

> **当前 Producer 是否已经从"可靠的 Agent 操作员"升级为"能够根据项目状态和执行结果自主协调
> 多个专业 Agent 的智能制片人"？**

**是。** Case 1 与 Case 2 的真机证据表明 Producer 已能：据项目状态判断该找谁（单域只找
Director；跨域 SW→Director）、有理由地跳过不需要的 Agent（Story/Character 无关→skip CD）、
据上一步真实结果衔接下一步（SW 改 s03/s04 → Director 读最新 canonical 同步受影响分镜）、
诚实区分"真完成"与"空声称"（receipts 派生 verified，Case 3 未假成功）。它不再是"操作员"，
而是会做路由/跳过/下游影响/handoff 判断的协调者。

**但（诚实保留）**：Case 3 暴露协调鲁棒性不足——对开放"整体检查优化"，Director 倾向审查-not-
写入、Producer 重复 re-delegate 后退化为自读自报，未能驱动实际修改。此为 NON-BLOCKER（有界、
零越权、零假成功），进 backlog（N3：prompt【失败协调】补"连续 verified=False"处置 + N2 Director
检查任务写入倾向），不阻塞 V1 冻结，留 Phase 3 / 后续迭代收口。

**PASS 则停止扩展核心逻辑。** Phase 3（Production Room / Group Chat）本轮不做。

---

## ㉒ 极小范围收口修正（N3 收口，2026-09-06）

针对 Case 3 NON-BLOCKER N3（Specialist completed 但 verified=false 时 Producer 重复相似
delegation）做了一次**极小范围**收口。本轮不是 Phase 3；未实现群聊、未重构 Bridge、未改三个
Specialist 核心架构。提交：`0f0256b`（prompt+delegate 描述）/ `44e40b5`（mock RE1-RE5+P2）。

**改动（全部仅 `producer.py` prompt/描述 + 测试，零 Runtime 新增）**：
- `PRODUCER_SYSTEM` 增【未实际修改的处理——区分 review 与 execution】节：①verified=false
  不一律失败——review 意图（检查/分析/评估/给建议）verified=false 是正常结果；execution 意图
  （优化/修改/修复）不能只完成"检查"就当目标完成；②execution 第一次 completed+verified=false
  → 不判失败也不原样重复，对同一 Specialist 做一次【执行性纠偏】重新委派（situation 以
  "[执行性纠偏]"开头，写明上轮无 verified 修改、要求重读 canonical、确有真实问题直接用写工具
  执行、无值得修改问题则明说无需修改不乱改）；③第二次仍 verified=false → 不机械重复，三选一
  replan（根因在他域→换 Agent）/接受 no-change/诚实报告"未能让 Specialist 产生实际修改"；
  ④禁止为 verified=true 强迫无价值修改；已发过一次[执行性纠偏]不发第二次。
- 三 delegate 描述（尤其 Director）微调：review+modify 任务发现真实问题应用写工具直接执行，
  不要只返回审查报告；无值得修改问题明确说明无需修改。**不改 Director system prompt（§13）。**
- 智能仍住 Agnes，由其读本 turn 历史自查"是否已发过[执行性纠偏]"；未建 retry engine/counter/
  loop detector（§14 禁令遵守）。

**mock（RE1-RE5 + P2，共 20/20 绿）**：RE1 review verified=false 正常接受；RE2 第一次失败写→
[执行性纠偏]→第二次成功写（同 Specialist 二次无 409、parent 链、纠偏标记透传、2nd verified=true）；
RE3 连续两次失败写不第三次机械调用同 Specialist（canonical 未变、消息诚实）；RE4 Specialist
明确"无值得修改"→接受 no-change 不强写；RE5 Director 两次未写且指 Story 根因→Producer replan
到 Script Writer；P2 prompt 弱断言。

**真机 Case 3 replay（§16，全新安全项目 `__producer_phase2d_r3`，干净 r3 运行）**：
Producer 读项目（inspect/list/read）后委派 SW（completed/verified=true，剧本 138→211 字）→
CD（completed/verified=true，两角色三视图生成）→ **Director #1（completed/verified=false/n_tools=0，
只读审查）→ Producer 发一次 [执行性纠偏]**（bridge_messages 第 4 条 situation 以"[执行性纠偏]"
开头，objective 改为"基于上述审查报告，**实际修改所有镜头**。具体操作：1)…2)…3)…"）→
**Director #2 仍 completed/verified=false/n_tools=0** → **Producer 停止，未发第 3 次 Director**。
最终汇报诚实："Director 未能执行实际修改…连续两次委派后均只返回审查报告（verified=false，
changed_refs 全空）"，未声称分镜已优化，未强制 Director 乱改。

**§17 验收逐条**：最多一次 clarification retry（恰 1 次 [执行性纠偏]）✓ / 之后正确 finish（诚实
blocker 报告，§9-C）✓ / 无机械重复（仅 2 次 Director 非旧版 5 次）✓ / 无强制乱改 ✓ / 无假成功 ✓。

**判定：N3 RESOLVED → Producer V1 Phase 2 正式冻结。**
- **N3 resolved**（收口目标达成）。
- **N2 仍 NON-BLOCKER backlog**：即便 Producer 给出明确"[执行性纠偏]…实际修改…具体操作 1/2/3"，
  Director（真 Agnes）第 2 轮仍 n_tool_calls=0（连重读都没做）未执行写入——这是 Director 侧
  review-only 倾向（§13 明确本轮不改 Director）。证据已充分，作为未来独立 Director 任务候选。
- N1（路由偏保守）/N4（前置读略多）/N5（真机无 A→B→A 自然 replan，但 RE5 mock 管道已证）/
  N6（situation 偶啰嗦）继续 backlog。
- 附注：replay 另两次（r1 malformed-JSON 400 / r2 免费档 429 rate limit）为 provider 容量/格式
  瞬态错误，非代码问题；干净 r3 运行给出决定性证据。另观察到 Producer 自身 LLM 调用遇 429/500
  会以 `[执行失败]` 收尾（producer loop 仅 catch ReadTimeout）——非本轮目标，记 backlog。

**Producer 智能协调核心开发到此停止。下一阶段 Phase 3（Production Room / Group Chat）。**
