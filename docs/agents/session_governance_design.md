# 会话治理四项 Runtime 增强 · 设计说明

> 分支 `feat/stress-runtime-hardening`（自冻结基线 `e3bc902` 切出）。
> 依据：`_stress/STRESS_REPORT.md` §8（失败案例）/ §12（退化阶段）/ §14（架构建议）。
> 分项 commit：`d27b516`（共享原语）→ `86e2ce0`（gate）→ `abdc066`（clean review）→
> `e451847`（re-grounding）→ `731e8b0`（telemetry），各自可独立回滚。

## 0. 共享基础设施

三项增强共用同一套原语（`src/agents/session_governance.py`，~370 行），
**"calibrated session spawn" = 新建会话 + canonical 状态头 + 用户 brief 注入**：

| 原语 | 用途 |
| --- | --- |
| `GovernanceStore` | 结构写计数器（Clean Review 与 Re-grounding 共用计数源） |
| `BriefStore` / `capture_brief_if_absent` | 用户创作约束（原始 brief）捕获与读取 |
| `build_state_header` | 校准状态头（canonical + work_memory 结构化事实） |
| `create_ephemeral`（sessions.py 附加方法） | 一次性 review session spawn |
| `reground_session` | 校准会话重置（同 sid 原地重置 + 历史归档） |
| `is_read_required_reply` / `validate_cited_ids` | zero-read gate 检测特征 / ID 勘误 |
| `log_context_telemetry` | 上下文规模遥测（改动四） |

`sessions.py` 仅附加 `create_ephemeral` / `save` 两个方法，`get_or_create_conversation`
的长存会话语义（runtime.py L241 project scope 一律返回长存会话）未动。

## 1. 触发计数口径（Clean Review 与 Re-grounding 共用）

- **计数对象**：`shot.create` / `shot.create_many` / `shot.delete` / `shot.move`
  （**不含 update/update_many** —— 内容修改不改变镜头集合，不制造"记忆里有、
  canonical 里没有/位置变了"的结构性脱节）。
- **计数单位**：按**成功调用次数**计（`create_many` 一批算 1 次）。只计
  `ok=true && verified=true && unchanged=false` 的回执——失败/stale/幂等调用没有
  实际改动 canonical，不产生 staleness。选择"按调用次数"而非"按影响镜头数"：
  更简单、可直接对照 events 流里的工具调用数；实验证据（E1+E2+E2b ≈ 13 次结构
  操作后出现退化）本身就是调用次数口径。
- **两个计数器、一个增量源**：`structural_since_review`（Clean Review 用，触发后
  清零）与 `structural_since_reground`（Re-grounding 用，触发后两个都清零）。若
  Clean Review 也清零 reground 计数，16 阈值将永远无法到达。
- **阈值**：`CLEAN_REVIEW_THRESHOLD = 8`（先于实验观察到的退化点 ~13）；
  `REGROUND_THRESHOLD = 16`（两次 Clean Review 量级）。均为 `session_governance.py`
  顶部配置常量。检查顺序：Re-grounding 优先于 Clean Review。
- **落盘**：`output/agents/session_governance.json`（原子写，turn 收尾记入）。
- **触发 A 的执行时机**：turn 成功收尾后（`status is None`），主链路写已全部真实
  落盘；STOPPED/FAILED 轮只记数不触发动作（计数保留，下一成功轮补触发）。

## 2. Zero-read Gate：read-required 检测的具体特征清单

命中条件 = **本 turn 零次读工具调用**（`DIRECTOR_READ_TOOLS` 内的调用，无论成败）
**且** 回复出现以下任一信号：

1. **引用 ≥2 个具体 shot_id**（正则 `(?<![A-Za-z0-9_])s\d{1,3}_c\d{1,3}(?![A-Za-z0-9_])`，
   去重计数）——"逐镜细节表"特征。**scene_id 单独出现不计**：探讨轮常自然复述
   用户提到的段号（"s02 这段怎么样"），误伤风险高；F2 的幻觉表格引用的是 shot_id。
2. **读取声称**（正则，带 6 字符否定窗口保护，"建议你逐镜检查"不算）：
   - `逐镜(精读|读了一遍|读过|核对|检查了|过)` / `逐段(…)` 同构；
   - `(完整|全部|所有)镜头(已经?|都)?(读|看|核对|检查)(了|过|一遍)`；
   - `一个一个(地)?(读|看|核对|检查)(了|过)` / `逐一(读|核对|检查|看过)(了|过)`；
   - `已(经)?(核对|读了一遍|全部读|读完|通读|精读)`；
   - `以(工具|真实返回|工具返回|读取结果|本次工具)(的)?(结果|内容|数据|为准)?为准`。

**动作**：注入一次 turn-local recovery nudge（`_ZERO_READ_RECOVERY_NUDGE`，镜像
zero-write recovery 设计：只一次、不循环），要求真正调读工具核对后再作答。
放在写域假成功纠偏**之后**——同时命中两者时先走既有写域纠偏，基线行为不变。

**ID 存在性校验（防敷衍，无条件作用于所有最终回复）**：回复中出现的全部
shot_id / scene_id 逐一对照当前 canonical；引用不存在的 ID → 追加确定性
【系统勘误】块（列出不存在者，不改写模型原文、不静默）。豁免：本轮真实删除的
ID（含级联下游）——"已删除 s05_c03"是合法陈述。

**误伤防线**：纯探讨轮（零工具、不引用镜头、无声称）不触发；gate 有
`real_changes` 排除（有真实写操作的轮走写域闸门）；nudge 只一次。

## 3. Clean Review

- **触发 A**：结构写累计 ≥8（自动）。**触发 B**：用户消息命中审查意图白名单
  （`连续性|连贯性|全片(检查|审查|过一遍|梳理|复查)|从头(过|读|看|检查|梳理|审|复盘)一?遍|
  整体(审查|检查|梳理|复查)|全面(审查|检查|复查)|逐镜(检查|审查|核对|梳理)|逐段(…)|
  做一?次(全片|整体|全面)?(审查|检查|复查)`），宁缺勿滥——"检查一下第2镜"这类
  单点核对不触发（主会话 + zero-read gate 胜任），识别不了走正常对话不算失败。
- **执行**：`run_clean_review`——每次触发新建一次性 review session
  （`create_ephemeral`，`scope_type="review"`，不复用、不进长存会话复用键、
  用完即弃）。Reviewer = 现有 Director agent 定义，**只读双保险**：client 只被
  告知读工具 schema（`tools_whitelist`）+ 运行时拒绝白名单外调用。只读模式下
  写域真相闸门不上膛（审查报告天然含"建议删除：…"措辞，会被误判为假成功声明），
  zero-read gate 照常生效。
- **上下文注入**：① 审查 preamble（规则：必须用读工具自主读 canonical、只报告
  不修改、重点检查项清单、报告格式）；② 用户原始 brief/关键约束；③ 状态头。
- **只报告不修改**：报告（问题清单+建议）追加到本轮回复返回主会话/用户；
  报告 JSON（含 trigger / trace / 工具调用数 / 时间戳）落盘
  `output/agents/reviews/review_*.json`；审查会话文件留存供审计。
- **延迟代价说明**：触发 A 的审查在 turn 收尾同步执行（真实模型约 30~60s，
  F4 实测 38s）；主回复已流式送达，审查报告随 turn.completed 返回并落盘。

## 4. Session Re-grounding

- **触发**：`structural_since_reground ≥ 16`（turn 成功收尾后检查，优先于
  Clean Review）。**触发后两个计数器都清零**。
- **动作**：`reground_session`——**同 session_id 原地重置**（前端无感，继续在
  主会话干活）：旧历史整体归档到 `output/agents/sessions/archive/{sid}_preground_
  {ts}.json`（保留证据，不删除、不摘要）。
- **携带物（严格限定）**：① 系统生成状态头（见 §5）；② 用户原始 brief；
  ③ 最近一轮用户意图（一句话，截 200 字）。**绝不含旧会话聊天内容、绝不做旧
  对话摘要**——否则等于自己制造 F2。
- **UX 不静默**：向用户输出明确提示（"已根据当前项目状态开启校准会话"+归档
  路径），并作为事件 `session_reground` 记入 meta.events。

## 5. 状态头的生成源与格式

**生成源（只有两个，均为结构化事实）**：

1. **canonical**（`ProjectStore.load()`）：各故事段 `scene_id`/摘要/镜头数/
   全部 shot_id 列表；角色 canonical 名单（name + description_en 摘要）。
2. **work_memory**（`WorkMemoryStore.recent(limit=8, domains=["storyboard"])`）：
   最近 8 条 storyboard 域结构化工作记忆（由真实回执生成的"谁做了什么"事实）。

**格式**（注入为新会话首条 user 消息，前缀【校准会话开启】说明）：

```
【校准会话开启】系统已根据当前项目状态重置本会话（旧对话已归档…）。
# 项目状态头（系统生成：canonical + 结构化工作记忆；不是对话回忆，当前状态以此为准）
## 故事段与镜头（当前真实状态）
- s01「林小雨走进咖啡馆，心情低落」2 镜：s01_c01, s01_c02
- s02「窗外下起了雨…」0 镜：（无镜头）
## 角色 canonical（权威名单）
- 林小雨：a young woman, red coat
## 最近结构/内容变更（共享工作记忆，结构化事实）
- Director 修改了镜头 s01_c01（description）
# 用户创作约束（原始 brief，创作与修改时必须遵守）
<brief 原文>
# 最近一轮用户意图
<一句话>
```

## 6. 用户 brief 获取方案（最简可行）

项目**首个 ≥10 字符的用户消息**（三 agent turn 均调用
`capture_brief_if_absent`，best-effort）被记为创作约束 brief，落盘
`output/agents/creative_brief.json`。正常创作流（SW 先接收故事 brief）里这就是
用户交给 SW 的原始 brief；短消息（"你好"/"开始吧"）不会被误捕。已存在的项目
可手工编辑该文件补录。Clean Review 与 Re-grounding 注入同一份。

## 7. Compaction 遥测（仅观察）

每 turn 追加一行 JSONL 到 `output/agents/telemetry/context_size.jsonl`：
`{ts, project, agent, session, turn_id, history_chars, system_chars,
tool_io_chars}`。tool_io_chars = 本轮 tool 消息内容 + assistant tool_calls
参数长度之和（估算口径，够看趋势）。审查会话独立成行（session 为一次性
review id、turn_id 带 `review_` 前缀）。Director 已接线；SW/CD 后续一轮
各加一行调用即可（helper agent 无关）。**不做压缩本体**（实验否决）。

## 8. 禁改清单遵守情况

- Agent 权限边界 / tool schema / OCC 指纹 / 批量上限 / SSE 事件协议 / canonical
  数据结构：零改动（`_run_tool_loop` 新参数 `tools_whitelist` 默认 None，既有
  调用行为 bit-for-bit 不变）。
- 既有 zero-write recovery：行为不变（zero-read gate 排在其后，且互不干扰）。
- review / re-grounding 均为新增只读或会话文件级路径；主链路在阈值未达时
  行为与基线一致（`test_below_threshold_no_auto_review` /
  `test_no_reground_below_threshold` 回归）。
- `_stress/` 证据文件零改动。

## 9. 验收用例 ↔ 测试映射

| 验收标准 | 测试 |
| --- | --- |
| 1. F2 复现（gate 命中 + nudge 一次 + 勘误块） | `test_zero_read_gate.py::test_f2_zero_read_gate_nudge_then_real_read` / `test_f2_errata_when_final_reply_cites_nonexistent_ids` |
| 2. 误伤用例（探讨轮不触发） | `test_zero_read_gate.py::test_chat_turn_no_zero_read_nudge` |
| 3. F4 复现（真实读取、报告落盘、只读） | `test_clean_review.py::test_user_triggered_review_reads_canonical` / `test_review_write_tool_rejected` / `test_reviewer_zero_read_gated` |
| 4. 触发计数（≥8 审查 / ≥16 重置 / 清零） | `test_clean_review.py::test_trigger_a_auto_review_at_threshold` / `test_governance_counter_semantics` / `test_session_regrounding.py::test_reground_at_threshold` |
| 5. 主链路零回归 | 全量测试（对照 V1_FROZEN.md 噪声清单）+ `test_director_agent.py` 21 项全绿 |
| 6. mock 测试不依赖真机 API | 四个新测试文件全部 ScriptedClient，不联网 |

**说明**：F4 复现用例中"报告发现 s05_c11/c05 重复与 s08_c07 空间混乱"属真实
模型能力，mock 测试只能验证接线（审查会话真实调用了读工具、报告落盘、只读
约束生效）；模型侧该能力已由 F4 真机实验本身证明，上真机后可用
`_stress/stress_driver.py` 同法复验。

## 10. 全量回归结果（2026-09-02，44m23s）

`pytest tests/ -q`：**995 passed / 23 failed / 1 skipped**（基线 968/26；新增
21 项治理测试全绿）。23 个失败**全部**在 V1_FROZEN.md 已知噪声清单内，无新增
失败名：

- not_configured 单例污染 ×3（director_min / script_writer_min / script_writer_native）——单独运行通过（已复验 director_min 单跑 37 passed）；
- test_shot_request_rules ×5、test_interface ×2——全量长跑资源噪声（已复验 shot_request_rules 单跑全过）；
- test_full_project_autopilot ×7、test_project_autopilot ×6（含 case7）——
  已知 e2e/timing flake（case7 为 V1_FROZEN 记录的"stash baseline 仍失败"既有 flake）。

按 V1_FROZEN 判定规则：清单内失败名单跑通过（或属记录在案的既有 flake）→
基础设施噪声，**非 regression**。完整日志留存于仓库根 `_full_regression.log`
（未入库）。
