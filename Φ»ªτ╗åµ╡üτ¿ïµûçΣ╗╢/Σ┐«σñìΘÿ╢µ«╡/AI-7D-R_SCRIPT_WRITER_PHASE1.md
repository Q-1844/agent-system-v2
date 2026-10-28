# AI-7D-R · Script Writer Phase 1 — 原生 tool-loop + 乐观并发锁

> 范围：AI-7D-R 测试阶段（4-agent 简化方向）下的 Script Writer 单 agent 改造。
> 阶段定位：Phase 1 = ONE agent（Script Writer）+ 一个写工具 `script.update`，只改 `story_text`。
> 上一节点：R1 报告（`AI-7D-R_R1_REPORT.md`）已 PASS。本阶段是 R1 之上的「拆掉 change_protocol 长链、换成 OpenAI-Agents-SDK 风格 Runner tool-loop」的第一步落地。
> 按 §89：**STOP，等用户手测验收后才进 Phase 2（story.create 故事段）。**

---

## 0. 一句话结论

- Phase 1 的「读→改→写」tool-loop **已落地并被用户手测确认**：「测试成功了，成功修改了剧本内容」。
- 乐观并发锁（optimistic concurrency）**已加上并测过**：`script.update` 调 LLM 时看到的 `story_text` 与写入前的 canonical `story_text` 之间，原本**无任何 revision/updated_at/hash 比对**；现已补一层极薄的内容指纹比对，stale 即拒绝写入并把最新版交还 Agent 重新判断。
- 自动化测试：`test_ai_7d_r_script_writer_native.py` **9 passed**（8 原有 + 1 新增 stale 拒绝）；D1/D2/D3/R1 回归 39 passed；`7d_r` 全量 49 passed。**0 回归。**
- 这层是「工具自身的正确性保护」，不是恢复 change_protocol 协议仪式。

---

## 1. 为什么做这个（用户原话精简）

> 「script.update 调用 LLM 时看到的 story_text，和真正写入 canonical 前的当前 story_text，是否有 revision / updated_at / hash 比对？如果没有，我建议加一个非常薄的 optimistic concurrency check。」
>
> 「这不是恢复 change_protocol，只是工具自身的正确性保护。旧系统历史也说明，stale protection、模块边界、确定性依赖检查、长任务幂等性这些底层原则是真的有价值；复杂的是它们外面那层协议仪式。」

目标流（用户给的 verbatim 意图）：

```
LLM 读取 version 12
→ 用户期间改成 version 13
→ script.update(version=12)
→ 拒绝写入
→ 把最新版返回 Agent
→ Agent 自己重新判断
```

---

## 2. 查证结果：原本没有任何比对

读 `src/core/models.py` + `src/core/store.py`：

- `Project.story_text: str = ""`（`models.py:423`）——「项目级完整故事（只存显，不解析/不拆分/不生成）」。
- `Project.schema_version: int = 2`（`models.py:429`）——是 v1→v2 迁移版本号，**不是编辑计数器**，不能当 OCC 令牌。
- `Project.updated_at: float`（`models.py:431`）——**项目级**时间戳，任何字段（场景/镜头/角色）改动都会刷新它，字段级无关，无法用作 story_text 专属 stale 判定。
- `ProjectStore.save()`（`store.py:134-136`）：`project.updated_at = time.time(); _atomic_write_json(...)`。**只 bump updated_at，无 revision 计数、无内容 hash。**

旧 `_execute_script_update`（改之前）：load canonical → `old = proj.story_text` → **只判断 `if new_text == old`（no-op）** → 直接 `proj.story_text = new_text; pstore.save(proj)`。**LLM 看到的版本与写入前的 canonical 之间，零比对。** stale 覆盖风险真实存在。

---

## 3. 设计：内容指纹令牌（字段级、零迁移）

**决策：用 `sha256(story_text)[:8]` 作为版本令牌，而非全局自增计数。**

| 维度 | 选择 | 理由 |
|------|------|------|
| 令牌来源 | 内容哈希 `sha256(story_text)[:8]` | 内容变→指纹变；无关字段（场景/镜头/角色）改动不影响 → 尊重「模块边界」 |
| 持久化 | **不持久化**，由 `story_text` 实时计算 | 无需改 Project 模型 / 无迁移 / 无新字段；最薄 |
| 字段级作用域 | 仅 `story_text` | 场景/镜头编辑不会触发 script 域的 stale 误拒 |
| 兜底 | 哈希覆盖任何写路径 | 即使某路径忘了 bump 计数器，内容变就被抓到（计数器方案做不到） |
| 用户认可 | 是 | 用户明确把「hash」列为可接受的版本令牌 |

并发流（落地后）：

```
system 提示注入 story_text + 版本指纹 R0
→ LLM 读到 R0，决定改 → script.update(new_text, expected_revision=R0)
→ 写入前重算 canonical 当前指纹 R_cur
→ R0 != R_cur → stale：拒绝写入（不落盘、无审计）
→ observation 返回 {stale:true, current_revision:R_cur, current_head/tail, old_len}
→ system 无条件刷新为最新正文 + 最新指纹
→ Agent 基于最新状态重新判断（重试 / 放弃 / 换法）
```

---

## 4. 改了什么（files changed）

### `src/agents/script_writer.py`（重写，核心）

1. **新增 `_story_token(text)`**：`sha256((text or "").encode("utf-8")).hexdigest()[:8]`。空串也有稳定指纹。
2. **新增 `_build_system(story_text)`**：注入正文 + 指纹。关键细节——**空正文显示「（空）」但指纹按真实空串算**（不能拿显示串算指纹，否则空正文与「（空）」字面的正文指纹对不上）。
3. **`SCRIPT_WRITER_SYSTEM` 提示**：末行改为 `当前剧本正文（版本指纹 {revision}）：\n{story_text}`；新增指令——`expected_revision` 必须原样回传、`stale=true` 时基于返回的最新版重新判断再用新令牌调用。
4. **`SCRIPT_UPDATE_TOOL` schema**：新增 required 属性 `expected_revision`（string，8 位十六进制）；`"required": ["new_story_text", "expected_revision"]`。
5. **`_execute_script_update` 并发校验**：参数校验顺序 = 解析 JSON → `new_story_text` 非空 → `expected_revision` 非空 → load canonical → `current_rev = _story_token(old)` → **`if expected_rev != current_rev: return stale obs（不写）`** → 否则 no-op 判断 → 否则原子写 + audit + record_work → 返回 `{ok, unchanged, old_len, new_len, revision:new_rev, head, tail}`。
6. **`script_writer_turn`**：`system = _build_system(proj.story_text or "")`。
7. **`_run_tool_loop` 无条件刷新 system**：从原来的「仅 `last_edit_info is not None` 时刷新」改为**每轮工具调用后无条件刷新**。理由——成功改写 / stale 拒绝 / no-op / 错误喂回，四种情况都需要 Agent 基于最新 canonical + 最新指纹继续判断。stale 拒绝时尤其关键：必须刷新才能让 Agent 下一轮看到最新指纹。

### `tests/test_ai_7d_r_script_writer_native.py`（更新 + 新增）

- import `_story_token`；新增 helper `_rev(tmp_path)` = 当前 canonical story_text 指纹。
- 给所有走「成功路径」的 `script.update` 调用补 `expected_revision: _rev(tmp_path)`：test 1（单步编辑）、test 3（错误喂回里的 good call）、test 5（未变更）、test 8（unified_turn 端到端）。
  - test 3 的空正文 call 也补了 `expected_revision`（new_text 空校验在 expected_rev 校验之前，仍抛 `edit_empty`，不受影响；补上是为对 check 顺序变更鲁棒）。
- **新增 `test_stale_rejection_no_write_returns_latest`**：传错误令牌 `"deadbeef"` → 断言 `edited is False`、canonical 未变、**无审计记录**、stale obs 喂回且 `current_revision == _story_token(original)`、2 次 LLM 调用（stale→刷新→最终回复）。

---

## 5. 三层薄约束（对齐 Agents SDK tool guardrail，不替 Agent 规划）

```
工具前：权限（仅 script_writer）+ 参数（非空 new_story_text + 非空 expected_revision）+ 目标（项目存在）
工具执行：乐观并发校验 → 原子写 canonical project.json + 修改前快照/audit
工具后：返回真实 observation（成功/stale/错误），让 Agent 自己判断是否继续
```

- story_text 无下游生产依赖（Level-1 可逆，§16），故 script.update 可直接原子写 canonical，不必经 ChangeSession/revision 提案链。
- 修改前快照 + `DirectEditAuditStore`，出错可恢复。
- 工具错误抛 `AgentError`，由 runner 转成 tool 错误 observation 喂回，**不静默假成功**。

---

## 6. 验证

```
test_ai_7d_r_script_writer_native.py     9 passed   (含 1 新增 stale)
test_ai_7d_d1_unified_turn.py            ✓ (D1 回归)
test_ai_7d_d2_change_bridge.py           ✓ (D2 回归)
test_ai_7d_d3_runtime_control.py        ✓ (D3 回归)
test_ai_7d_r1_conversation_memory.py     ✓ (R1 回归)
test_ai_7d_r_director_min.py             ✓
test_ai_7d_r_script_writer_min.py         ✓ (legacy)
---------------------------------------- ---------
7d_r + D1/D2/D3/R1 合计                  49 + 39 ... 全绿，0 回归
```

**用户手测**：在真实前端 + 后端用 Script Writer 改剧本，剧本内容成功修改。Phase 1 read→edit→write loop 端到端打通。

---

## 7. 前端路由修复（Phase 1 能跑通的前提，上一段会话已落地）

之前 Script Writer 改剧本的请求到不了 `/turn`：两个前端入口（`src/store/agentUi.ts` Floating Bar + `src/views/agents/index.vue` Agent Center）各有一份 `CHANGE_INTENT_RE`，在发 `/turn` 之前就把变更意图文本截走、桥接进旧 changeSession（返回一大段方案审查输出）。

修法（两处同语义）：加 `DIRECT_EDIT_AGENTS = new Set(['script_writer'])` 守卫——具备直接编辑工具的 Agent 跳过 ChangeSession 桥接，直接发 `/turn` 由 agent 自己的 tool-loop 完成修改。

---

## 8. Phase 2 路线图（用户钦定顺序，逐段推进）

> 「先验证四件事：新 ID、顺序、字段结构、canonical 写入是否可靠。它稳定后，再让 Script Writer 连续调用 story.create。这样能分清：拆多了/拆少了，到底是 create 工具有问题，还是 Agent 多工具循环判断有问题。」

| 顺序 | 内容 | 验证重点 |
|------|------|----------|
| 2a | `story.create`（单个故事段） | 新 ID / 顺序（order）/ 字段结构 / canonical 写入可靠性 |
| 2b | 批量剧本拆分 | SW 对整篇剧本连续调用 `story.create`；区分「create 工具问题」vs「多工具循环判断问题」 |
| 2c | 故事段修改 | — |
| 2d | 删除（复用通用 Delete Service） | — |
| 3 | 自由多工具组合 | 多工具循环自由编排 |

**本阶段不开始 Phase 2**。等用户验收 Phase 1（含并发锁）。

---

## 9. 残留 / 已知

- 沙箱限制：本会话 bash 无法 spawn 外部二进制（curl/python → EPERM `uv_spawn bash.exe`），但 `pytest` 这次能跑（上次会话报 EPERM，本次通过）。若后续遇到 EPERM，让用户用 `! cd animation-storyboard && "D:/Delta Force/python.exe" -m pytest ...` 自跑。
- 后端改了 `src/agents/script_writer.py` → **需重启后端**（CLAUDE.md 规则：改 `src/agents/*` 属后端代码）。
- 这层并发锁是 story_text 字段级的；将来 story 域扩展到故事段（Phase 2a）时，`story.create` 同样需要 OCC（同模板：返回新段指纹、Agent 回传 expected_revision）。届时 `_story_token` 可复用/泛化为段级指纹。
