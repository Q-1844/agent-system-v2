# Producer V1 Phase 3 — Production Room / Group Chat · 实现报告

> 阶段：把后台已经真实存在的 `User → Producer → Specialist → Producer → …` 多 Agent
> 协作过程，做成**真实、持久、实时、可刷新恢复、可调试的 Production Room**。
> 基线：`feat/producer-v1-bridge`（Phase 2 已冻结于 `0f0256b`/`44e40b5`/`ef9cc79`）。
> 本报告所有"证据"均为真实执行产物；判定从证据，不从文本自述。

---

## 1. Phase 2 frozen baseline（未重设计确认）

Phase 2 冻结基线=「PRODUCER_SYSTEM 智能协调 prompt + 三 delegate 描述 + 最小代码 C
（last_coordination_summary / telemetry / verified 纯 receipts 派生）+ RE1-RE5 执行性纠偏」。
Phase 3 **零改动**以下冻结面：

- `PRODUCER_SYSTEM` prompt 与三个 delegate 描述（`src/agents/producer.py` 一行未改，仅被只读引用）；
- 三个 Specialist 业务逻辑 / Scene Sub-Agent / DiffEngine / Clean Review / Session
  Governance / OCC / truth gate / renewable window —— `git diff` 证明 Phase 3 三个功能
  commit 的 backend 改动只有：**新增** `src/agents/production_room.py`（只读投影）、
  **新增** `tests/test_production_room.py`、`routers/agents.py` **+18 行**（一个只读 GET
  路由），producer.py 与三个 Specialist 文件 **零 diff**；
- Producer 智能协调核心（routing/skip/handoff/replan/完成判断/执行性纠偏）全部原样，
  真机 E2E 中自然复现了 skip（CD 未参与）与 A→A replan（Director 二次委派），协调行为无回退。

## 2. 原 Producer UI 结构（Phase 3 前）

- Agent Center 为单页多面板：`src/views/agents/index.vue` 左侧 8 个 Agent 卡 + 右侧聊天面板；
- Producer 面板 = 旧 AI-6B 手动委派表单（Delegated Tasks 表 + New Delegation）+ 与其它
  Agent 相同的通用聊天区（Phase 1 已把 producer 加入 `/turn/stream` 流式管线）；
- Specialist 的 delegate 事件仅以「执行过程折叠条」里的 tool 条目出现，没有：agent 身份、
  roster、委派/结果卡片、verified 徽标、刷新后可恢复的协作历史；
- `bridge_messages.jsonl` 只写不读，前端零接入。

## 3. Production Room 最终架构

```text
                          ┌────────────────────────────────────────────┐
  持久事实（唯一来源）        │  Production Room（只读投影 View，零写入）      │
  ─────────────────        │  ─────────────────────────────             │
  Producer session 消息 ──▶│ backend: src/agents/production_room.py      │
  bridge_messages.jsonl ──▶│   build_room_projection(rt, project)        │
  AgentTask(*.delegate) ──▶│ GET /api/project/{p}/agents/producer/room   │
  workflows.json        ──▶│   → {session_id, turn_active, workflow,     │
  turn_registry(进程内) ──▶│      participants, messages[]}              │
                          └──────────────┬─────────────────────────────┘
                                         │ snapshot（stable id / 确定顺序）
  Producer /turn/stream SSE ─▶ frontend useProductionRoomStore
  （delegation.started/completed 里程碑 +        │  snapshot + live merge（稳定 ID 同键去重）
   带 agent 标记的 Specialist 工具事件）          ▼
                              ProductionRoom.vue（roster + 群聊 timeline + composer）
```

- 定位：Producer 页面升级为 Production Room（§七），Agent Center 导航与其余 Agent 面板
  不变；Script Writer / Character Designer / Director 的 Direct Chat 原样保留（E2E 验证）；
- 角色（§八）：👤 User / 🎛 Producer / ✍ Script Writer / 🎨 Character Designer / 🎥 Director
  （`roomIdentity.ts` 纯视觉映射，不承载状态语义）。Director 内部 Scene Agent 未展示——
  其无可识别的独立事件（Scene Sub-Agent 无独立 task/bridge 记录），为显示它需改 Specialist
  核心，违反 §五/§八，进 NON-BLOCKER。

## 4. Authoritative sources（红线自证）

| 事实 | 来源 | Production Room 行为 |
| --- | --- | --- |
| Project canonical | Story/Character/Shot 存储 | **零写入**（投影甚至不读 canonical——不需要） |
| Agent 执行事实 | AgentTask / Task Result | 只读 `tasks/*.json`（capability 以 `.delegate` 结尾的 Bridge 委派） |
| P↔S 协作事实 | bridge_messages.jsonl | 只读（附 `_seq` 行号派生，见 §7） |
| Producer 对话 | sessions/{sid}.json | 只读 user/assistant 消息 |
| workflow | workflows.json | 只读（current = running 优先，否则 created_at 最新） |
| 进程内活跃 | turn_registry | 只读判定 working（不伪造） |

- 无新数据库 / 无新持久文件 / 无 projection cache：**每次 GET 从盘上事实重建**（§十五
  "删掉 cache 可重建"——根本没有 cache）；
- 测试 `test_projection_is_readonly_no_side_files` 断言投影调用前后项目文件树逐字节不变；
- UI 历史 ≠ Agent context（§四十九）：Room 数据只进浏览器，Producer 下一轮 LLM 调用仍是
  `PRODUCER_SYSTEM + session history + 本轮消息`（producer.py 原逻辑），无任何把 Room
  消息灌回 context 的路径；Specialist context 隔离不变（envelope 五段 + 各自读 canonical）。

## 5. Projection 实现（production_room.py）

- `_load_delegate_tasks`：`tasks/*.json` → requester=producer 且 capability 以 `.delegate`
  结尾（旧 AI-6B 手动 inspect 委派**不进**时间线，B9b 断言）；
- `_read_bridge_rows`：JSONL 逐行解析（坏行跳过 + warning），附 `_seq`=行号；
- `build_room_projection`：session 消息 + 每 task 的 dispatch/result 消息 + 孤儿 bridge 行
  （task 文件缺失仍显示，数据在即显示）→ 统一排序输出；
- envelope 内容源：bridge P→S 行（objective/situation/constraints/scope）优先；**委派进行中
  尚无 bridge 行时回退 AgentTask.instruction**（dispatch 时已落盘 → 刷新恢复的关键事实链）；
- 状态判定（§九不伪造）：
  - `running` 仅当 AgentTask=running **且** turn_registry 活跃；
  - running 但 registry 空（后端重启）→ `interrupted`（已中断）；
  - 已结算 → structured_result.status 原样透传（completed/stopped/failed/not_configured），
    stopped → roster `cancelled`；
  - verified / changed_refs 只认 receipts 派生的 Task Result，绝不采信 Specialist 自述
    （真机证据：SW 第二轮自述"检查完毕无需修改"→ verified=false 正确显示，Producer 未假成功）。

## 6. Message schema（快照单条消息）

```jsonc
// delegation（Producer → Specialist）
{ "id": "task:task_x:dispatch", "kind": "delegation", "timestamp": 1788…,
  "task_id": "task_x", "workflow_id": "wf_x", "sender": "producer", "recipient": "director",
  "content": "<objective>", "situation": "…", "constraints": ["…"], "scope": {"scene_ids": [...]},
  "status": "running|completed|stopped|failed|interrupted|not_configured", "live": true,
  "parent_task_id": "task_prev", "capability_id": "director.delegate",
  "session_id": "sess_…", "task_created_at": …, "task_updated_at": … }

// specialist_result（Specialist → Producer）
{ "id": "task:task_x:result", "kind": "specialist_result", …, "sender": "director",
  "content": "<Task Result summary>", "full_reply": "<assistant_message（≤6000，超长带 truncated 标记）>",
  "status": "completed", "verified": true,
  "changed_refs": {"scenes": ["s03","s04"], "shots": ["s03_c03",…], "characters": []},
  "warnings": [], "n_tool_calls": 18 }

// user / producer 消息：id = session message_id；producer 附真实终态 status
```

与规范差异（自行修正说明）：
- `user_intent` 不在 bridge 持久行里（Phase 1 只落盘 objective/situation/constraints/scope），
  任务详情面板按真实存在字段展示，**不虚构**；
- bridge 行 `constraints` 真实样例存在 string 形态（Phase 2 落盘数据证实）——投影归一化为
  list（`_norm_constraints`，B8b 断言）；
- `full_reply` 后端 6000 字截断 + truncated 标记（防超长快照；完整内容仍在 Specialist
  session，可经 Direct Chat 查看）。

## 7. Stable ID / 稳定顺序

- **ID**：user/producer 消息复用 `message_id`；委派/结果用 `task:{task_id}:dispatch|result`
  ——task_id 每委派唯一（replan 同 agent 重复委派也是新 task），live（SSE 合成）与
  snapshot（投影）天然同键，去重**不依赖 content 相等**（§十六）。未给 bridge 行加写时
  字段：行身份由 (task_id, sender) 唯一确定 + `_seq` 行号兜底——零写入的向后兼容方案
  （Phase 2 真实落盘 8 行数据验证）；
- **顺序**：`(timestamp, kind 序 user<delegation<result<producer, id)`；全部 timestamp 为
  落盘事实（time.time() 单调时钟）。B2/B3（两次投影 + 全新 Runtime 重建）、E2E 20 次
  GET 重载、真机浏览器刷新——顺序均一致。

## 8. Snapshot / Hydration（刷新恢复）

页面打开 → `ensureSession`（get_or_create producer 长存会话）→ `GET room` 全量快照 → 渲染。
刷新（含 Specialist 执行中刷新）后：已完成历史全部恢复；执行中委派由 AgentTask(running) +
registry 活跃投影为 working；乐观本地 echo 由 content+±5s 时间窗回收（真实消息去重始终走
稳定 ID，此仅为本地 placeholder 清理），turn 结束 refresh 无条件清空。

## 9. SSE Live Merge

- 复用现有 Producer `/turn/stream`（Phase 1 已转发 Specialist 事件并带 `agent` 字段），
  **未新增第三条 SSE 通道**；
- `agentTurn` store 增加 `onTurnEvent` 订阅 tap（监听器异常隔离，主流程零风险）；Room store：
  - `tool.progress(phase=delegation.started)` → live 委派卡（id=`task:{tid}:dispatch`）+
    roster 立即 working（真机截图：Director 委派卡实时"工作中"）；
  - Specialist `tool.started/completed/failed`（带 agent）→ 该委派卡「执行详情」折叠条
    （按 call_id upsert）；
  - `delegation.completed` → settle 过渡态 + 立即 refresh（此刻 bridge 行/Task Result 已
    落盘，结果卡以稳定 ID 合并，无重复）；
  - `turn.completed/failed` → 终态 refresh + 停轮询判定；
  - **不**消费 Specialist reasoning/assistant.delta（§十三：不直播私有思维链；活动文案仅用
    既有中文 label）。

## 10. 轮询对账（SSE 不是持久事实，§二十/§四十八）

SSE 断开（刷新/切页/半开连接）不丢事实：turn 后台续跑 + 持久层承担真相。Room store 在
`turn 活跃 || workflow running || 存在 running/live 消息` 时以 3s 轮询 snapshot 兜底
（后端重启残留 running 的场景以 100 次 stale 轮询上限自动停）。稳态零轮询；活跃期每 3s
一次只读 GET，不重读 canonical。真机证据：浏览器刷新断流后，Director 完成结果与 Producer
最终汇报均在数秒内经轮询自动出现。

## 11. Agent roster / status

`participants`：Producer + 三 Specialist。状态源：AgentTask + turn_registry + session 最后
一条 assistant 消息 status；无 task 时 `not_called: true`（显示「本轮未参与」）。**不猜测
skip 理由**（§二十七）。并发就绪：roster/消息按 task_id 索引（非 activeAgent:string），
B8 双 running task 同时 working 断言；真机截图 roster 同屏表达 Producer 工作中 + Director
工作中。

## 12-14. 展示层

- **委派卡**（§二十二）：默认 objective 一行卡（🎛 Producer → ✍ Script Writer）+ 状态徽标；
  「任务详情」展开 situation/constraints/scope/task_id/workflow/上游 task（parent 链）；
  live 时另有「执行详情」折叠条（工具事件不进主线气泡，§十二）；
- **结果卡**（§二十三/§二十四）：默认 Task Result summary，保留 Specialist 自己的声音
  （真机：Director 的"我如实汇报：上一轮成功创建了 7 个镜头……"原样呈现，未被改写成
  Producer 话术）；「查看完整回复」展开 assistant_message；底部事实徽标 `✓ Verified` /
  `Scenes: … · Shots: … · 角色: …` / `N 次工具调用`；verified=false 完成态显示
  `○ 无 verified canonical 修改`（真机两次出现，均未显示绿色成功）；failed 显示 `⚠ 失败`
  + warnings；`■ 已停止（已完成的修改保留）`；
- **Producer 失败**（§三十四）：`[执行失败: …]` 以 failed 状态诚实显示（真机发生一次，
  见 §20）；不新增 retry 系统（遵守不扩范围）；
- **Workflow 可视化**（§二十六）：顶栏轻量 chip（goal 悬浮 + 状态点 running/completed/
  failed/stopped），roster 即 per-agent 状态条；无 DAG/BPMN；
- **调试信息**（§二十五）：结果卡「调试信息」折叠 task_id/workflow/parent/execution_ref/
  session_id/时间戳——直接定位一次委派与 canonical 变化；
- 滚动复用 `useStickToBottom`（贴底跟随/查看历史不强拉）；正文沿用仓库「不引入
  v-html/markdown 解析」纪律（pre-wrap），见 §25 NB4；小屏 roster 可折叠、卡片占满（§三十九）。

## 15. Context isolation 证明

Room 数据只进浏览器；Producer LLM 调用消息序列 = system + session history + 本轮 user
（producer.py:869-895 原样）；Specialist 收到的仍是 render_envelope 五段文本（E2E events
抓盘确认 delegation.started → Specialist 自己 story.list/script.read/shot.list）。群聊 UI
对 Runtime 不可见——**群聊 UI ≠ 共享 context**（§四最高红线）满足。

## 16. Concurrent-ready 证明

- 后端 B8：两个不同 agent 的 running task + 两个 registry 槽位 → 投影同时表达两个 working；
- 前端 `liveDelegations` 为 Map（task_id 键），timeline computed 支持多条 live 委派并存；
- 未实现并行调度（遵守 §十/§五十八：不主动制造并行）。

## 17. Direct Chat 兼容

左侧 Agent 列表与 Specialist 面板零改动；真机浏览器点击 Script Writer 卡验证：scope 选择
器、切换会话、「Conversation（独立隔离）」、思考开关、输入框全部原样。Team Chat / Floating
Bar / 测试弹窗不受影响。Specialist 完整工具时间线仍在各自 Direct Chat。

## 18. Backend tests（tests/test_production_room.py，12 项全绿）

| 用例 | 覆盖 |
| --- | --- |
| B1 | 完整链路投影顺序 + 字段（sender/recipient/objective/situation/verified/changed_refs/parent 链） |
| B2+B3 | 两次投影 / 全新 Runtime 重建：ID+顺序+时间戳完全一致；ID 唯一 |
| B4 | 清 registry（模拟重启）后重建：已完成历史一致、无假 running |
| B5 | verified=false（stale 失败写）→ result.verified=false + warnings + roster verified=false |
| B6 | Specialist 异常 → failed 状态 + 异常信息诚实进正文 |
| B7 | cancel → result stopped / dispatch stopped / producer stopped / roster cancelled / workflow stopped |
| B8 | concurrent-ready（双 running task 同时 working）+ constraints string 容错 |
| B9 | 空项目不崩（session=None 全 idle）；旧 producer session 正常显示聊天；旧手动委派不进时间线 |
| HTTP | GET 端点 200/404 语义 + 两次 GET 稳定 + 投影后 bridge 行数不变 |
| 只读红线 | 投影调用前后项目文件树逐字节不变 |

复用 `test_producer_bridge.py` 的 ScriptedClient 双循环夹具（真实 producer_turn → 真实
Specialist turn → 真实持久化 → 只读投影）。`test_producer_bridge.py` 既有 20 项回归全绿。

## 19. Frontend tests

仓库**无前端单测基建**（package.json 无 test script / 无 vitest）。按规范 §四十一"已有
基础才补"，不为本阶段引入新测试框架。替代验证：`vue-tsc`（新增 4 文件零错误；全仓库
6 个既有基线错误均在未触碰文件）+ vite dev 编译 + 真机浏览器 E2E（§20-23）。

## 20. Real E2E（真机 + 真浏览器 + 真 Agnes）

环境：独立启动 backend(:8000) + vite dev(:7777)；全新安全项目 `__producer_phase3_room` /
`__producer_phase3_stop`（各 4 场景/6 镜头/2 角色，不动正式项目）；真实 Agnes。

**Turn 1（HTTP 驱动，`gp_phase3_room.py casea`）**：
- Producer 观察（project.inspect/story.list/script.read）→ `delegation.started` → 委派 SW
  （objective="修复 s03/s04 后半段节奏偏快、人物动机不清晰…"）；
- **中途刷新模拟**：SW 执行中独立 HTTP 连接 GET room → `caseA.midrefresh.room.json`：
  messages=`[user, delegation(running)]`，participants=`producer:working, script_writer:working` ✓；
- SW completed verified=True（Scenes: s03, s04）；随后 Producer 读不存在的 `s04_c01`
  触发**冻结代码的既有异常路径**（read 工具异常未被 Producer loop 捕获 → turn 以
  `[执行失败: AgentError: 镜头不存在: s04_c01]` 收尾，Phase 2 已知家族，属 §三十四"正确
  显示失败"而非 Phase 3 任务）——Room 全程诚实：producer failed / roster failed /
  workflow failed，SW 已完成的 verified 结果完整保留；
- 20 次重载顺序稳定 ✓；盘账一致 ✓。

**Turn 2（同项目第二 turn，真机浏览器全程在线观看）**：
- 浏览器实时看到：user 气泡 → Producer→SW 委派卡"已完成" → SW 结果卡
  `○ 无 verified canonical 修改 · 4 次工具调用`（SW 判断正文无需改，verified=false 自然
  发生且正确显示）→ **Producer→Director 委派卡实时"工作中"**（截图
  `browser_live_director_working.png`）；
- **§44 BLOCKER·执行中刷新浏览器**：F5 后历史完整恢复（两轮全部卡片 + verified 徽标），
  无重复气泡；SSE 已断，Director 的完成结果（✓ Verified · Scenes: s03, s04 ·
  Shots: s03_c03, s03_c04, s04_c01–c05 · 18 次工具调用）与 Producer 最终汇报经 3s 轮询
  自动补上——"历史不消失 / active task 恢复 / 后续消息继续出现 / 不重复"四项全过；
- **真实 A→A replan 可见**：Director 汇报"s03_c01/c02 未更新，需要我继续吗"→ Producer
  再次委派 Director（更新旧镜头）→ 第二张 Director 结果卡 verified=True；
- 最终：workflow completed、12 条消息、roster 全员终态（CD 本轮未参与）、
  `browser_final_completed.png`、20x 稳定、盘账一致。

**Stop 案例（`__producer_phase3_stop`）**：Specialist 执行中（41.5s）POST cancel → SW 步骤
边界停止（51.3s delegation.completed）→ turn status=stopped → Room：delegation/result/
producer 全部 `[stopped]`（"■ 已停止（已完成的修改保留）"）、roster producer+script_writer
=cancelled、workflow stopped、has_running=False、无假完成（`stop.final.room.json`）。

## 21. refresh-mid-run 结果

见 §20 Turn 1（HTTP 独立连接）与 Turn 2（真实浏览器 F5）：**BLOCKER 级验收通过**。

## 22. Stop 结果

见 §20 Stop 案例：stop 真实生效、active 状态停止、锁释放（unlocked 事件 + 对账）、UI 无假
完成、持久化后状态正确。**通过**。

## 23. Regression 结果

- 全量 `pytest tests/`（:8000 已确认空闲）：**34 failed / 1123 passed / 1 skipped**——与
  Phase 2 基线**完全同构**（同 34 个、同文件族：runtime_control×11 + autopilot×14 +
  interface×2 + shot_request_rules×5 + case7 timing flake + not_configured×3——均为套件
  内部端口自干扰与基线已知噪声，Phase 2 报告 §⑲ 已分类）；**零新增失败**；
- 端口干扰族 + 新增测试单跑复绿：`test_ai_7d_d3_runtime_control + test_interface +
  test_shot_request_rules + test_production_room + test_producer_bridge = 54 passed`；
- producer 影响面：test_producer_bridge 20/20 绿； Specialist 零改动；
- 前端：vue-tsc 错误数 6（全部既有）；Direct Chat 兼容真机验证（§17）。

## 24. BLOCKER

**无。** 逐条核对 §五十六：context 未合并（投影只读）/ 无虚假消息（全部来自持久事实，
E2E 盘账一致）/ SSE 断开历史不丢（轮询兜底 + 刷新恢复实测）/ refresh 零重复（稳定 ID）/ 
failed 显示 failed（§20 两例）/ verified=false 未显示成功（§20 SW 第二轮）/ 无第二套
authoritative state（只读 + 文件树不变测试）/ Producer Bridge 未改坏（bridge 20 测试绿 +
真机三案例）/ Direct Chat 未破坏（真机验证）/ canonical ownership 不变（Producer 零写工具
+ 投影零写入）/ cancel 后无 working/completed（Stop 案例）/ 跨 Agent 顺序刷新不漂移
（20x + 真机刷新）。

## 25. NON-BLOCKER（backlog）

- NB1 Scene Agent 不在 Room 中展示（需 Specialist 事件支持，违反 §五不为此改核心）；
- NB2 长会话快照无分页（当前量级安全；历史极长后可加 `limit` 参数，端点已预留实现空间）；
- NB3 Producer 读不存在 shot 时 turn 以执行失败收尾（冻结核心既有行为；§三十四只要求
  正确显示——已做到；真正的 retry/容错属后续阶段）；
- NB4 正文为 pre-wrap 纯文本，未渲染 markdown（仓库既有"不引入 v-html/markdown"纪律，
  引入渲染器+XSS 防护是产品级决策，留 backlog）；
- NB5 断流重连后 live 工具事件不重放（已有 3s 轮询兜底至秒级延迟；事件级 replay 需要
  协议扩展）；
- NB6 avatar/动画视觉可进一步打磨（§五十七）。

## 26. Screenshots / evidence 路径

- 驱动脚本 `gp_phase3_room.py`（seed/casea/stop/room 四命令；未提交，gp_* scratch 惯例）
- `_phase3_evidence/caseA.events.jsonl`（Turn 1 全程 SSE 帧抓盘）
- `_phase3_evidence/caseA.midrefresh.room.json`（SW 执行中"刷新"快照：running 状态）
- `_phase3_evidence/caseA.final.room.json`（两轮 12 消息终态）
- `_phase3_evidence/stop.final.room.json`（Stop 案例终态）
- `_phase3_evidence/browser_live_director_working.png`（真机浏览器：Director 执行中实时
  roster + live 委派卡 + verified=false 结果卡）
- `_phase3_evidence/browser_final_completed.png`（真机浏览器：workflow 完成终态 + Producer
  最终汇报）
- `_phase3_full_regression.log`（全量回归）

## 27. Commits（4 个，均不 push）

1. `18ee6e8` feat(producer): Phase 3 backend — Production Room 只读投影（stable ID/确定
   顺序/真实状态）+ GET /agents/producer/room（含 12 项 B1-B9 测试）
2. `0b450e4` feat(producer): Phase 3 frontend — Production Room 群聊式协作视图（roster/
   委派卡/结果卡/实时合并/刷新恢复）
3. `aaea0da` fix(producer): Phase 3 E2E 修复——SCSS 块注释中 `.st-*/` 提前终止注释
   （真机浏览器 E2E 抓获）
4. 本报告（`PRODUCER_V1_PHASE3_REPORT.md`）

scratch 未提交：`gp_phase3_room.py`、`_phase3_evidence/`、`_phase3_full_regression.log`
（符合 §五十二）。

## 28. 最终判定

**判定：PASS（无 BLOCKER）。**

§五十五 PASS 标准逐条：

| 维度 | 结果 |
| --- | --- |
| Architecture | Room=纯只读投影（文件树不变测试）；不成为 canonical；不共享 context；Producer 唯一 orchestrator ✓ |
| Messages | User / Producer / Producer→Specialist / Specialist→Producer 四类真实可见（12 消息终态快照）✓ |
| Realtime | delegation started/completed、Specialist 工具事件实时上屏（真机截图）✓ |
| Persistence | 执行中刷新完整恢复 + 轮询补齐终态（§21）✓ |
| Truth | verified=false / failed / stopped 全部诚实显示（三例真机）✓ |
| Debuggability | task/workflow/parent/execution_ref/session/时间戳折叠可查；盘账一致 ✓ |
| Agent Status | roster 多 Agent 状态同屏（B8 + 真机 Producer working + Director working）✓ |
| Compatibility | Specialist Direct Chat 原样（真机验证）；Phase 1/2 能力零回退 ✓ |
| Ownership | Producer 零 canonical mutation；投影零写入 ✓ |
| Regression | 全量 34 failed 与基线同构同数，单跑复绿，零新增 ✓ |

> **Production Room 是否已经能够把 Producer 与专业 Agent 的真实协作，以实时、持久、可信、
> 可调试的群聊形式呈现给用户，同时完全不破坏各 Agent 的上下文隔离与 canonical ownership？**
>
> **是。** 真机证据链：用户消息、Producer 委派（含完整 envelope）、Specialist 结果（保留
> 专业声音 + verified/changed_refs 事实徽标）、Producer 汇报全部实时可见；执行中刷新浏览器
> 历史不丢、状态恢复、终态自动补齐；verified=false、执行失败、用户停止三类非成功终态全部
> 诚实呈现且刷新后持久正确；每张卡可下钻到 task/workflow/parent/execution；而 Producer
> 与 Specialist 的 LLM context、canonical 写权限与 Phase 1/2 完全一致——Room 只是那套
> 真实事实的一面镜子。

---

## 六十 · 路线判断：Producer V1 — COMPLETE（建议冻结）

Phase 1（真实 Specialist Bridge）+ Phase 2（Intelligent Coordination）+ Phase 3（Production
Room）均 PASS。建议冻结 Producer V1，基于完整真机使用体验再选下一方向。按本轮真实证据
推荐优先级：

1. **Director N2 独立修复**（证据最强：真机两次出现 review-only / verified=false + 
   Phase 2 Case 3 五次只读审查——Production Room 已让该问题肉眼可见，修复收益直接）；
2. **Producer read-tool 容错**（本轮 Turn 1 真机暴露：read 工具异常击穿 Producer loop 
   使整轮 failed，与 429/500 同族；小改动、高鲁棒性收益——建议随 N2 一起做）；
3. 真正 parallel delegation / @Agent / Visual Review Agent / Skill system / 多模型路由：
   等 Producer 真机长跑一段时间积累需求后再决策，不提前投入。
