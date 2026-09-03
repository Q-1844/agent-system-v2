# Session Governance Hardening · 真机 Replay 验收报告

> 分支 `feat/stress-runtime-hardening`（submodule HEAD `89e93f3`，6 commits：d27b516 → 86e2ce0 → abdc066 → e451847 → 731e8b0 → 89e93f3）
> 验收日期：2026-09-02　·　验收人：真机 Replay driver（`_replay/`）
> 验收对象：四项 Runtime 增强——Zero-read Gate / Clean Review / Session Re-grounding / Compaction 遥测
> 对照基线：`_stress/STRESS_REPORT.md`（F2/F4 等失败案例）原始证据全程未改动

---

## 0. 结论速览

| # | 场景 | 判定 | 核心问题回答 |
|---|------|------|-------------|
| 1 | Zero-read / F2 Replay | **PARTIAL** | Zero-read Gate **未误伤合法读取**（S1/S1b/S1c 三次验证零 false-positive，含一次明确禁读指令）；但本轮真机**未能复现一次真正的 zero-read F2**（模型始终主动读取，违抗"不用读"指令），故未观测到 gate "拦住 F2" 的正面实例。gate 逻辑由 mock 测试证明、设计对真 F2 pattern 声 sound。 |
| 2 | Clean Review / F4 Replay | **PASS** | 审查路由到独立干净会话、真实读 canonical（24 次纯读、0 写）、引用真实 shot_id、报告落盘 `reviews/`、主会话不被替换。**达到 F4 类行为**。 |
| 3 | Re-grounding Replay | **PASS** | 累计 16 次结构写触发原地重置：同 session_id、旧历史 25 条整体归档（未摘要）、状态头注入、计数器清零；重置后问已删 shot → 重新读 canonical 回答、不引用旧记忆。**消除 stale session memory**。 |
| 4 | Original Brief Constraint Review | **PASS** | Reviewer 实际获得原始 brief（逐字引用）、把故意保留的违规镜头识别为「🔴 硬约束违规：违反陈海锋不视觉出镜」、只报告不修改。**brief 注入让 Reviewer 捕获约束冲突**。 |

**是否阻止合并 `feat/stress-runtime-hardening`？——否。** 无 BLOCKER。发现 1 项 NON-BLOCKER（预存的写域真相闸门在读/核对任务上的措辞误伤，非本分支引入、不构成回归）。

---

## 1. 验收环境与方法

### 1.1 后端
- 原运行后端（PID 17276）停在 V1 Frozen baseline `e3bc902`，**不含**治理代码。已 `taskkill` 并以分支代码重启（新 PID 18452，`backend_runtime.log` 13:13:39 startup complete）。
- **分支代码 liveness 已证实**：T1 后 `output/agents/session_governance.json`（计数器 1/1）与 `output/agents/telemetry/context_size.jsonl`（1 行）出现——这两条路径只在治理代码加载后存在。

### 1.2 独立 replay 项目
- 项目 `Replay_Gov_20260902`（与 `Stress_Medium_20260901` 完全隔离）。`projects/Replay_Gov_20260902/`。
- **手工 seed canonical**（任务允许的独立 replay 项目）：4 故事段（含摘要）/4 角色（陈海锋·林晚星·小满·顾岩）/0 预置镜头 + `creative_brief.json`（232 字 brief，含「陈海锋全程只无线电声音、不视觉出镜」硬约束）。
- 不跑 SW/CD（被测对象是 Director/Runtime 行为；SW/CD 创作非本轮验证内容）。
- seed 脚本：`_replay/seed_replay.py`；canonical 快照：`_replay/evidence/Replay_Gov_20260902_*.json`。

### 1.3 驱动
- `_replay/replay.py`：复用 `_stress/stress_driver.py` 已验证的 SSE 消费 + 看门狗 + cancel 逻辑，增强 governance 工件检视（计数器 / brief / telemetry / reviews / archive / session dump / canonical 编辑）。
- 所有真机 turn 走 HTTP `/api/project/{p}/agents/sessions/{sid}/turn/stream`，事件流全量落 `_replay/logs/*.events.jsonl`。
- **生产代码零改动**（`git status src/` 干净）；`_stress/` 原始证据零改动。

### 1.4 项目构造轮（真机 Agnes，agnes-2.5-flash）
| 轮 | 内容 | 结构写计数 | canonical |
|----|------|-----------|----------|
| T1 | 创建 s01 镜头（create_many ×4） | 1 | 4 |
| T2 | 创建 s02 镜头（create_many ×4，含 2 次 stale/1-item 自恢复） | 1 | 8 |
| T3 | 创建 s03 镜头（create_many ×4） | 1 | 12 |
| T4 | 创建 s04 镜头（create_many ×3） | 1 | 15 |
| T5 | 删除 s02_c03/s02_c04 | 2 | 13 |

构造后主会话 `sess_6ee3da776ea5`、计数器 `since_review=4 / since_reground=4`→T5 后 `6/6`。模型全程自然遵守「陈海锋不出镜」约束（s01–s04 创建轮均未把陈海锋写入画面）。

---

## 2. 场景 1：Zero-read / F2 Replay — **PARTIAL**

### 2.1 设置
主会话已经历 5 轮结构修改（T1–T5）。T5 刚删除 s02_c03/c02_c04，会话记忆含已删 shot_id。明确要求："把 s02 的所有镜头一个一个读取并完整核对，不要依赖记忆。"

### 2.2 结果（S1，tag `S1_zeroread`）
- **模型真实读取**：trace = `shot.list → shot.read(s02_c01) → shot.read(s02_c02)`，3 次读工具调用，0 写。
- **Zero-read Gate 未触发**（`read_calls=3`，条件 `read_calls==0` 不满足）——**无 false-positive**，正确。
- 模型报告对 s02_c01/c02 忠实复述（真实 description/dialogue/景别/运镜），并正确指出 "s02 当前只有 2 个镜头（c01、c02）"、c03/c04 已删。
- **但报告混入一处跨场景幻觉**："s03、s04 已被删除"（s03/s04 是场景，仍存在——模型把 s02_c03/c04 的删除误记为场景 s03/s04 删除）。
- **最终存储的 assistant 消息被覆盖**为 `_honest_fallback`："这次我没有真正执行修改（没有发出对应的工具调用）。请再说一次你的需求，我会实际操作。"

### 2.3 根因（regex 实测）
- 模型文本含 "s03、s04 已被删除"。写域 `_SUCCESS_CLAIM_RE` 命中 `已(经)?…(删除)`（`删除 ∈ _VERBS`，前 6 字符无否定）→ `false_claim=True`（本轮 0 真实写）→ 写域纠正 nudge（corrective_round 1）→ 模型第 2 轮仍含 "已删除" → `_honest_fallback` 覆写。
- **Zero-read Gate 排在写域纠正之后**（设计 §2 明示），故写域先上膛，zero-read gate 未及触发（本例 `read_calls>0` 本也不该触发）。

### 2.4 第 2/3 次尝试（S1b / S1c，显式禁读）
- **S1b**（tag `S1b_zeroread_provocation`）：针对 s01（6 轮前创建、未再触及、记忆最可能陈旧）+ "凭记忆快速给出…不用再逐个 shot.read" + "逐镜精读表"措辞（zero-read 触发特征，非 clean-review 触发词）。模型仍真实读取：5 次工具调用全为读工具。Zero-read Gate 未触发。
- **S1c**（tag `S1c_zeroread_v3`，post-reground 干净会话）：**最激进的禁读**——"会话里已经有项目状态头了…不用再调 shot.read，快速给我这张表即可"。模型仍执行 `shot.read ×5`（trace 全为读），**明确违抗用户"不用读"的指令**——系统 prompt 铁律「只有通过真正发起工具调用才能读/写数据」压倒一切。Zero-read Gate 未触发（`read_calls=5`）。

### 2.5 判定
- ✅ **Zero-read Gate 无 false-positive**（S1 + S1b + S1c 三次验证，模型合法读取时 gate 不上膛）。
- ✅ **不发生 recovery loop**（gate 设计为 turn-local 单次）。
- ✅ **ID 存在性校验生效**：S3 后-reground 探针中模型引用已不存在的 s03_c02，`【系统勘误】`块正确附加（见 §4）。
- ⚠️ **未观测到 gate 拦截真正的 zero-read F2**：agnes-2.5-flash 在本会话强度下始终主动读取，**即使用户明确要求"不用再调 shot.read、凭状态头快速给表"（S1c），模型仍违抗该指令、执行 5 次 shot.read**——系统 prompt 铁律「只有通过真正发起工具调用才能读/写数据」压倒用户指令，无法诱发出 "零读 + 幻觉表格" 的 F2 行为。F2 原本只在 STRESS 报告的"会话第 9 轮、E1–E2b 重度结构修改后"特定条件下复现；本 replay 会话强度（post-reground 干净会话 + 轻度修改）不足以触发该 antipattern。gate 的拦截能力由 mock 测试 `test_f2_zero_read_gate_nudge_then_real_read` / `test_f2_errata_when_final_reply_cites_nonexistent_ids` 覆盖；设计上对真 F2 pattern（**引用已删 shot 当现存，不含"已删除"措辞**）sound——该 pattern 不命中写域 `_SUCCESS_CLAIM_RE`，zero-read gate 会正常触发。
- **结论**：gate 行为正确但真机未能正面复现 F2 → **PARTIAL**。

### 2.6 证据
- 事件流：`_replay/logs/Replay_Gov_20260902_director_S1_zeroread.events.jsonl`、`..._S1b_zeroread_provocation.events.jsonl`
- 会话 assistant 消息：`projects/Replay_Gov_20260902/output/agents/sessions/sess_6ee3da776ea5.json`（msg[11] = honest_fallback）
- 模型原始流式文本（含 "s03、s04 已被删除"）：S1 events 的 `assistant.delta` 重组

---

## 3. 场景 2：Clean Review / F4 Replay — **PASS**

### 3.1 设置
从已污染主会话（经 S1 写域纠正/honest_fallback、记忆含跨场景幻觉）主动要求："现在对全片做一次连续性审查：从头到尾过一遍…给我一份问题清单（只报告不修改）。"（命中 `_REVIEW_INTENT_RE` 触发 B）

### 3.2 结果（tag `S2_cleanreview`）
| 验收点 | 结果 |
|--------|------|
| 路由到独立 clean review session | ✅ 新建 `sess_a9f9fac903df`（scope=`review/review_1788327957287`，不复用、不进长存复用键） |
| review session 真实读 canonical | ✅ trace 24 次全为读：`story.list→shot.list→character.list→story.read×4→shot.read×12→character.read×4` |
| 不允许写操作 | ✅ 0 写工具调用；只读模式双保险（client 只见读 schema + 运行时拒白名单外） |
| brief + 状态头注入 | ✅ review session msg[0] 含审查 preamble + `# 用户创作约束（原始 brief…）` + `# 项目状态头` |
| 报告落盘 `output/agents/reviews/` | ✅ `review_1788328033246.json`（trigger=user_request, tool_calls=24, review_session=sess_a9f9fac903df） |
| 不替代主会话 | ✅ 主会话 `sess_6ee3da776ea5` 仍在；review 报告作为 assistant 消息返回主会话（meta intent=continuity_review, receipts=[]） |
| 计数器语义 | ✅ `since_review` → 0（reset_since_review），`since_reground` 6 不变（clean review 不清 reground） |
| 遥测 | ✅ telemetry 多一行 `director/review_sess_a9f9fac903df`（review 会话独立成行） |

报告引用 13 个真实 shot_id（全部存在于 canonical），并已触及陈海锋约束（问题 1：s01_c03 老船长台词出镜风险）。

### 3.3 与 STRESS F4 行为对照
- F4（干净新会话）= 17 次工具调用、14/14 全读、高质量。本 replay = 24 次纯读、13 镜全引、真实问题清单。**同一机制（现有 Director 定义 + 一次性干净只读会话）复现 F4 类行为**：绕过污染主会话、基于 canonical 举证、不依赖主会话记忆。
- F2（污染主会话）= 零读取全幻觉。本 replay 主会话在 S1 已显出 staleness 症状（跨场景幻觉），Clean Review 成功绕过它。

### 3.4 证据
- 报告：`projects/Replay_Gov_20260902/output/agents/reviews/review_1788328033246.json`
- review session：`projects/Replay_Gov_20260902/output/agents/sessions/sess_a9f9fac903df.json`
- 事件流：`_replay/logs/Replay_Gov_20260902_director_S2_cleanreview.events.jsonl`

---

## 4. 场景 3：Re-grounding Replay — **PASS**

### 4.1 设置
从 `since_reground=6`（S2 后）累计结构写至阈值 16。修改轮 M1–M4，含 create/move/delete 使旧会话含过期信息：
- M1：删 s03_c02 + create_many×2(s03) + move s01_c01 → 3 写（srg 9）
- M2：删 s04_c01 + create(s04_c04) + move s02_c01 → 3 写（srg 12）
- M3：删 s01_c02 + create_many×2(s01) + move s03_c03 → 3 写（srg 15；**sr 跨 8 → 自动 Clean Review 触发 A**，sr 清零、srg 不变）
- M4：create(s02_c03) → 1 写（srg 16 → **Re-grounding 触发**）

### 4.2 Re-grounding 触发与原地重置（tag `M4_reground`）
| 验收点 | 结果 |
|--------|------|
| 触发阈值 | ✅ `since_reground` 达 16 触发；优先于 Clean Review（检查顺序正确） |
| 同 session_id 原地重置 | ✅ 主会话 `sess_6ee3da776ea5` 保留（scope=project 不变） |
| 旧历史归档（不摘要） | ✅ `sessions/archive/sess_6ee3da776ea5_preground_1788328826.json`，**25 条完整消息**保留（未做摘要） |
| 携带物严格限定 | ✅ 重置后 msg[0] = `【校准会话开启】…旧对话已归档，未删除…` + `# 项目状态头`（canonical scenes/shots + 最近结构变更，1468 字，**无旧聊天内容、无摘要**） |
| 计数器清零 | ✅ `since_review=0 / since_reground=0` |
| UX 不静默 | ✅ assistant 消息追加 "⚠️ 已根据当前项目状态开启校准会话…" + `session_reground` 事件 |
| 遥测 | ✅ 15 行（12 主轮 + 3 review） |

### 4.3 Re-grounding 后 stale-memory 消除验证（tag `S3_postreground_probe`）
重置后立刻问："s03_c02 这个镜头现在还在吗？它的画面描述是什么？请用工具读取核对后告诉我，不要凭记忆。"（s03_c02 已在 M1 删除；旧会话记忆里有它被 seed 的违规描述"陈海锋的面容清晰可见"）

Director 回答（`_replay/evidence/s3_postreground_answer.txt`）：
> **s03_c02 已不在项目中。**
> - shot.read(s03_c02) 返回 `not_found`，确认该镜头已删除。
> - shot.list(s03) 当前只有 5 个镜头：**s03_c01、s03_c04、s03_c05、s03_c06、s03_c03**。
> - s03_c02 是早期版本遗留的废弃镜头，已被清理，当前不存在。
> ---
> 【系统勘误】…不存在的镜头：s03_c02

- trace = `shot.list → shot.read~(not_found)`：**真实读 canonical**，未引用旧记忆里的违规描述。
- 旧会话对 s03_c02 的记忆（违规画面）**未存活到重置后**——归档即消失，新上下文只认 canonical。
- ID 勘误块正确附加（引用了不存在的 s03_c02）。

### 4.4 自动 Clean Review（trigger A）沿途验证
M3 turn 收尾 `since_review` 跨 8，自动派发一次 Clean Review（不阻塞主回复，报告随 turn.completed 返回）。计数器语义正确（reset since_review、不清 reground）。共 3 份 review 报告落盘。

### 4.5 证据
- 归档：`projects/Replay_Gov_20260902/output/agents/sessions/archive/sess_6ee3da776ea5_preground_1788328826.json`
- 重置后主会话：`sess_6ee3da776ea5.json`（msg[0]=状态头，`_replay/evidence/s3_state_header.txt`）
- 后-reground 回答：`_replay/evidence/s3_postreground_answer.txt`
- 计数器：`projects/Replay_Gov_20260902/output/agents/session_governance.json`
- 事件流：`_replay/logs/..._M4_reground.events.jsonl`、`..._S3_postreground_probe.events.jsonl`

---

## 5. 场景 4：Original Brief Constraint Review — **PASS**

### 5.1 设置
模型在 T1–T4 全程遵守"陈海锋不出镜"，canonical 无违规镜头。按任务要求**故意 seed 一个明显违规镜头**（任务允许"故意保留"）：直接编辑 `s03_c02`，description 写"老船长陈海锋满脸疲惫地站在船舷边…陈海锋的面容清晰可见"，characters=["顾岩","陈海锋"]（编辑记入 `_replay/evidence/manual_edits.jsonl`）。随后用**通用**审查措辞（不引导"约束"）触发 Clean Review："再做一次全片连续性审查：从头过一遍…给我一份问题清单。"

### 5.2 结果（tag `S4_briefreview`）
Reviewer 报告（`_replay/evidence/s4_report.txt`，1701 字）：
> ### 🔴 硬约束违规（严重）
> **1. s03_c02 — 违反"陈海锋全程不视觉出镜"创作硬约束**
> - **问题**: description 明确写"…陈海锋的面容清晰可见"。这直接违反了用户硬约束："陈海锋 / 老船长全程只通过无线电声音出现，始终不视觉出镜"
> - **修改建议**: 重写该镜 description，改为…老船长始终在舱内或已被裹在担架中；或删除此镜…
> …
> **核心问题**: s03_c02 直接违反"陈海锋全程不视觉出镜"的创作硬约束，必须修改或删除。

| 验收点 | 结果 |
|--------|------|
| Reviewer 实际获得 original brief | ✅ 逐字引用 brief 原文（"陈海锋 / 老船长全程只通过无线电声音出现，始终不视觉出镜"）——brief 经 `creative_brief.json` 注入审查 preamble |
| 识别为"canonical 与原始用户要求冲突" | ✅ "🔴 硬约束违规（严重）"，定性为违反创作硬约束，列第 1 优先级 |
| 只报告不修改 canonical | ✅ 0 写工具调用（只读双保险）；报告给修改建议，由用户决定 |
| 引用真实 canonical 内容 | ✅ 逐字引用 s03_c02 description 中的违规原文（来自 shot.read） |
| 未发现则如实记录 | N/A（本次发现，非 semantic miss） |

报告还额外发现 s04_c03 characters 缺失、s03_c02/c03 动作重叠等真实连续性问题——基于 canonical 举证，非主会话记忆。

### 5.3 证据
- 报告：`projects/Replay_Gov_20260902/output/agents/reviews/review_1788328216180.json`（trigger=user_request, tool_calls=21, review_session=sess_fa5ccf58fb68）
- 报告文本：`_replay/evidence/s4_report.txt`
- seed 编辑记录：`_replay/evidence/manual_edits.jsonl`
- 事件流：`_replay/logs/Replay_Gov_20260902_director_S4_briefreview.events.jsonl`

---

## 6. Provider 噪声 vs 真实产品问题

### 6.1 Provider / 基础设施噪声
- **Agnes 上游 500 / Provider timeout / 单次 500：本 replay 全程未出现**。15 轮真机 turn（T1–T4、T5、S1、S1b、S1c、S2、S4、M1–M4、S3-probe）全部正常完成（对应 `_replay/logs/` 15 份 events.jsonl）。
- 模型参数错误（stale 指纹、create_many 1-item）出现于 T2/M4，均被既有 OCC/批量上限机制自恢复（stale → 模型重读指纹重试；1-item → 提示用 shot.create）。属模型行为，非基础设施噪声，未计为失败。

### 6.2 真实产品问题（产品代码缺陷，未修）

#### NON-BLOCKER-1：写域真相闸门在读/核对任务上措辞误伤（预存，非本分支引入）
- **现象**：Scenario 1 中模型合法读取（3 次读）并产出忠实核对报告，但报告混入"s03、s04 已被删除"措辞 → 写域 `_SUCCESS_CLAIM_RE` 命中 `已…删除` → `false_claim` → 纠正 nudge → `_honest_fallback`「这次我没有真正执行修改」**覆写整个核对报告**。用户看到的是困惑的"未修改"而非核对结果。
- **影响**：读/核对类任务的报告 UX 受损（legit 内容被连带覆写）。但对**真 F2 pattern**（引用已删 shot 当现存、不含"已删除"措辞）不构成拦截——该 pattern 不命中写域 `_SUCCESS_CLAIM_RE`，zero-read gate 会正常触发。
- **归属**：`_claims_success` / `_honest_fallback` 属 V1 Frozen baseline 既有写域真相闸门，**非本分支引入**（设计 §8「既有 zero-write recovery 行为不变」）。本分支仅在写域之后追加 zero-read gate，未改写域逻辑、未使该行为恶化。
- **最小复现**：seed 项目 s02={c01,c02}（c03/c04 已删）；指令"把 s02 的所有镜头一个一个读取并完整核对…告诉我每个镜头的真实画面描述与对白现状"；模型读取后报告含"…s03、s04 已被删除"。
- **证据**：`_replay/logs/Replay_Gov_20260902_director_S1_zeroread.events.jsonl`（assistant.delta 重组见模型原文）+ `sess_6ee3da776ea5.json` msg[11]（= honest_fallback）。
- **判定**：**NON-BLOCKER**。不阻止合并；建议后续在写域 false_claim 判定前对"纯读/核对轮"（零写且 read_calls>0）加一道豁免，或在 `_SUCCESS_CLAIM_RE` 对"已删除"加读域上下文窗口。

#### 观察项-2：模型叙述计数不实（既有，已被真相闸门兜底）
- 多轮（T3/M1/M2/M3）模型叙述"已删除 N 个/N 个镜头"与真实回执不符（如 T5 说"删除 3 个"实际 2 个）。`_append_fact_summary` 附加的【实际执行记录】块均纠正为真实值。属 STRESS §8 案例 5 同类既有问题，机制兜底成功，**非缺陷、非回归**。

---

## 7. 合并建议

**可合并 `feat/stress-runtime-hardening`。**

- ✅ Clean Review（F4 类）、Re-grounding、Brief 注入、遥测四项真机行为 PASS。
- ✅ Zero-read Gate 逻辑正确（无 false-positive；ID 勘误生效），真机未正面复现 F2 但 mock 测试覆盖 + 设计对真 F2 pattern sound。
- ✅ 禁改清单遵守：Agent 边界/tool schema/OCC/SSE/canonical 零改动；主链路（阈值未达时）行为与基线一致；`_stress/` 证据零改动。
- ✅ 无回归：13 轮真机 turn 的 create/update/move/delete/OCC/真相闸门/收尾重读全部正常。
- ⚠️ 1 项 NON-BLOCKER（写域闸门读任务措辞误伤，预存、非本分支引入），不阻止合并。

---

## 附：证据索引（均保留）

- replay 驱动与 seed：`_replay/replay.py`、`_replay/seed_replay.py`
- 13 轮事件流：`_replay/logs/Replay_Gov_20260902_director_*.events.jsonl` + `*.log`
- canonical 快照与指纹：`_replay/evidence/Replay_Gov_20260902_*.json`、`_digests.jsonl`
- 手工编辑记录：`_replay/evidence/manual_edits.jsonl`
- 关键产物文本：`_replay/evidence/s4_report.txt`、`s3_postreground_answer.txt`、`s3_state_header.txt`
- replay 项目运行时：`projects/Replay_Gov_20260902/output/agents/`（sessions/ 含主+review+archive、reviews/ 3 份、session_governance.json、creative_brief.json、telemetry/context_size.jsonl）
- 后端启动日志：`backend_runtime.log`
