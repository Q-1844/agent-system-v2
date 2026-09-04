# Producer V1 Phase 1 — Specialist Bridge Core · 实现报告

> 阶段：Producer 从「只读项目问答器 + 手工委派表单」升级为能自主调用 SW/CD/Director
> 真实 `*_turn`、并据上一步结果自然衔接下一步的项目级总操作 Agent。
> 优先级：可靠真实调用 > 自然衔接 > 长时间执行 > 可观测 > 高级智能规划。
> 基线：`feat/producer-v1-bridge`（自 `aa6ed2f` 切出）。

## 1. 改了什么

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `src/agents/turn_registry.py` | 新建 | 中性活跃 turn 注册表（`_ACTIVE`/`key`/`acquire`/`pop`/`get`）。router 与 Producer Bridge 共用同一进程内 dict，防止用户直开 Specialist 与 Bridge 委派并发驱动同一 session。仿 `change_protocol/locks.py` 先例。 |
| `src/agents/producer.py` | 新建 | PRODUCER_SYSTEM / 工具 schema / 专用轻量镜像 loop `producer_turn` / `_delegate_specialist`（三个共用一个泛化实现）/ `_bridge_cb`（锁与 `*.changed` 接线）/ workflow + bridge 消息持久化。 |
| `src/agents/runtime.py` | 改 | `unified_turn` 加 producer project-scope 重路由（镜像 SW/CD），**并门控于 function-calling client**（`chat_completion`）：真 Agnes/ScriptedClient 才走 producer tool-loop；纯 chat client（D1/D3 冻结契约 FakeClient）与未配置 client 继续走 inquiry/conversation/runtime_control 意图分流。 |
| `routers/agents.py` | 改 | ① `_ACTIVE_TURN_EVENTS`/`_turn_key` 改为 `turn_registry` 的别名（同一 dict/函数，行为逐字节等价，既有测试零改动）；② `agent_turn_stream` 加 producer 分支（`_writes` 空集——Producer 自身不锁资源，锁由 Bridge 内 Specialist 接线负责）。 |
| `src/views/agents/index.vue` | 改 | §8 最小切流：Agent Center `STREAMED_AGENTS` 加 `producer`（多 Agent 委派分钟级，非流式 HTTP 会超时）+ Producer 三域任一锁即提示。 |
| `src/agents/models.py` | 不动 | `AgentTask.parent_task_id` 已存在（`:350`，已序列化），无需补。 |
| `tests/test_producer_bridge.py` | 新建 | §10 A-L 全 11 测试（全 mock 不联网）。 |
| `docs/agents/producer_v1_phase1.md` | 新建 | 本报告。 |

**不碰**：三个 Specialist 业务逻辑、`subagent/`、session_governance、agnes_client、director_visual/JobQueue、OCC/truth gates/locks 语义、legacy delegate 端点与 capability adapters（全部保留）。

## 2. 架构（保持用户控制权）

```
User → Producer session → producer tool-loop（专用轻量镜像 loop）
  ├─ 只读 canonical（复用既有 read executors：project.inspect/story.*/character.*/shot.*）
  ├─ script_writer.delegate / character_designer.delegate / director.delegate
  │    └→ 真实 Specialist session（get_or_create_conversation）+ 真实 *_turn + 真实 tool-loop
  │    └→ _bridge_cb 复制 router _cb 的锁/emit 接线 + 转发事件到 Producer SSE
  │    └→ 结构化 Task Result（receipts 派生 changed_refs，绝不采信 LLM 自述）
  └─ 据 Task Result 判断下一步 → … → 向用户汇报
```
Producer 始终持有用户对话控制权；Specialist turn 在 Bridge 内嵌套执行，不接管对话。

## 3. Authority 硬边界（物理保证）

Producer 工具面（`_build_producer_tools`）= `project.inspect` + 7 个只读 executor（直接 import SW/CD/Director 的 `_execute_*`）+ 3 个 delegate 工具。**根本不存在** story/character/shot 写工具与图/视频生成工具——`PRODUCER_WRITE_OPERATIONS = set()`。`test_H` 显式断言。Story→SW / Character→CD / Shot→Director 所有权由工具面缺失直接保证。

## 4. 关键设计决策

- **为何不复用 `director._run_tool_loop` 的 `tools_whitelist` 模式**（写进 `producer.py` 头注释）：该模式 zero-read gate 照常上膛，而 Producer 汇总 Task Result 时必然引用 shot_id/scene_id（事实来源是 Runtime 包装的 Task Result 而非本轮读调用）→ 必然误触发 nudge。故 Producer 用自己的轻量镜像 loop：保留 renewable window / cancel / SSE / trace / receipts(delegate 回执) / telemetry；**去掉**写域真相闸门 / zero-read-zero-write nudge / 搜索预算 / 批量降级。窗口 1024，self-check nudge 镜像（每文件一份）。**无任何总步数/总时长上限**（不设总续期次数/总步数/总墙钟硬上限）。
- **锁与 `*.changed` 事件在 Bridge 的 `_bridge_cb` 里复制**（§5.3-⑤b）：直接调 `*_turn` 而不复制接线时前端所有页面都看不到锁与内容变化。`_bridge_cb` 逐条照抄 `routers/agents.py` 的 `_cb` 语义（`tool.started`+name∈writes→`bus.lock`；`tool.completed`+name∈writes+ok→`emit_changed`），并转发 Specialist 全部事件到 Producer SSE。
- **envelope 渲染 `user_intent` 在首位**：`script_writer_turn` 内部 `capture_brief_if_absent` 会捕获首条 ≥10 字符用户消息为 brief，envelope 以用户原话开头保证 brief 锚定用户话语（已知可接受行为）。
- **Task Result 的 `verified`/`changed_refs` 只从真实 receipts 派生**，绝不采信 LLM 自述。
- **`agent_turn_stream` 的 producer 分支 `_writes` 传空集**：Producer 自身不锁资源（锁由 Bridge 内 Specialist 接线负责），避免双重锁/错域。

## 5. 测试（§10 A-L，全 11 通过）

`tests/test_producer_bridge.py`：单 `ScriptedClient` 交错喂 Producer（`chat_completion`）与 Specialist（`chat_completion_stream`，因 Bridge 传 `event_cb=_bridge_cb`）双循环。
- A/B/C：真实委派 SW/CD/Director —— Specialist session 文件存在+agent_id 正确+首条 user=envelope；canonical 被 Specialist 真实 executor 改动；Producer receipts 只有 delegate 回执。
- D：envelope 五段齐全（user_intent/objective/situation/constraints/scope），user_intent 在 objective 前。
- E（在 C）：Task Result 的 status/summary/changed_refs/warnings，changed_refs 与真实 receipts 一致。
- F：SW→Director 链 —— Director envelope situation 含 SW 结果摘要；Director 自己调读工具（Producer 不转述全文）；workflow 两 task `parent_task_id` 链；bridge_messages.jsonl 四条。
- G：跳过 —— 读完直接委派 Director，只有一次委派（无固定顺序）。
- H：ownership —— Producer 工具名集合无任何写/生成工具；`PRODUCER_WRITE_OPERATIONS` 空。
- I：窗口 —— 注入 window=3，3 步工具后恰好一条【系统提示·执行窗口】nudge，`window_renewals==1`；grep 断言无总上限常量。
- J：cancel —— 第 2 次调用 set cancel → turn STOPPED 诚实收尾；cancel 透传（Specialist Task Result status=stopped）。
- K：持久化 —— 重载 workflows.json / bridge_messages.jsonl / AgentTaskStore，链完整。
- L：锁接线 —— SW delegate 期间 `RESOURCE_STORY` 锁/`story.changed`/解锁全发总线。

## 6. 红线遵守

- 不做：群聊 UI / @Agent / Production Room / 复杂 DAG planner / dependency engine / loop detector / progress scoring / Producer Sub-Agent / Skill / 多模型路由 / 并行 scheduler（V1 串行委派，架构不堵死未来并行）/ Visual Review Agent / 大规模 legacy cleanup。
- 不破坏：三个冻结 Specialist / Scene Sub-Agent / Clean Review / Session Governance / OCC / truth gates / zero-read-zero-write / resource locks / DiffEngine / Web Search / renewable window / visual pipelines / SSE 协议 / cancellation / receipts（grep diff 确认三个 Specialist 文件零业务改动）。
- 不设 Producer 总步数/总时长上限。
- Specialist 失败 ≠ workflow 失败：Producer 收真实 failed Task Result 后自行判断，绝不假装成功。

## 7. 状态

- mock 测试（`tests/test_producer_bridge.py` §10 A-L）：**11/11 通过**。
- 影响面回归（按改动 blast radius 全部 standalone 跑，~595 例）：
  - D1/D2/D3 unified_turn + 8 个 producer bridge + stream_turn/front_stream/project_events/r1_memory：79 passed
  - 核心冻结套件（test_agents/director/sw-native/CD/turn-background/story-update/web-search/subagent bitforbit+gate）：180 passed / 1 skipped
  - batch split/tools/cross-memory/false-success/min：48 passed
  - long-execution + 全部 subagent（instance/registry/diff-engine/fingerprints/governance）：通过
  - 7d_r tail（fact-strip/failure-observability/serial/story-delete/batch-delete-insert 等）+ tail api/runtime 套件（api/zero-write-recovery/clean-review/telemetry/partial-success/relay/story-continuation/regrounding/zero-read/workbench/interface/sensenova/prompt-builders/shot-rules）：125+109 passed
  - **零新增失败**。全量套件内的失败均为基线已知噪声：`not_configured` 族（conftest 加载真 .env 时失败、**单跑即过**，已逐个验证）+ autopilot e2e 时序 flake（`test_project_autopilot`/`full_project_autopilot` 零引用 producer/stream/unified_turn，且 `import api_server` 干净、18/19 通过）。
  - 修复记录：首版 producer 无条件重路由曾拦截 D1/D3 冻结路径（FakeClient 只有 `.chat()`），已用 **function-calling client 门控**修复（`2ea64fb`），D1/D2/D3 复绿。
- 后端导入：`import api_server` 干净，FastAPI app 构建通过，`producer_turn` 可调，registry 别名同一 dict。
- 前端：Agent Center Producer 已切 `/turn/stream`（`334622d`）；Vite HMR 无需重启后端即可生效（.vue）。
- 真机 smoke：待后端重启（加载 Python 新代码）+ 新测试项目 + 真 Agnes（§11 九问）。这是用户验收闸。

## 8. 提交

分支 `feat/producer-v1-bridge`：
- `dc5aa7b` docs(producer): 现状审计（Phase 1 输入基线）
- `e4e274e` feat(producer): Specialist Bridge Core（turn_registry + producer.py + runtime + routers + 测试 + 本文档）
- `2ea64fb` fix(producer): 门控 tool-loop 于 function-calling client（D1/D3 冻结路由不破）
- `334622d` feat(producer-ui): Agent Center Producer 切 /turn/stream
