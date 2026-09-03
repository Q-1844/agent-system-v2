# Agent System V1 · Core Agents Complete & Frozen

> 2026-09-03。三个核心专业 Agent（Script Writer / Character Designer / Director）及其子系统全部完成并冻结。本节点定义为：
>
> **Agent System V1 — Core Agents Complete & Frozen**
>
> 建立在 [[V1_FROZEN]]（2026-09-01，`e3bc902`）之上。V1 Frozen 是"分镜工作流五阶段重构 + V1 收口 + Golden Path 生产链"的基线；本节点在其上补齐 **Agent 智能层**（三个 OpenAI-Agents-SDK 风格原生 tool-loop Specialist + Director 治理四件套 + Scene Sub-Agent），构成完整的 Agent System V1。

## 三大核心 Agent 冻结状态

### 1. Script Writer Agent（剧本作家）
- **域**：story/scene 写域（YAML 剧本→Scene 结构）。只写 story/scene，不碰镜头/角色。
- **关键能力**：原生 function-calling tool-loop；shot 级 OCC 指纹复用；`web.search`（Tavily Hub 国内代理，轮内 3 次硬预算 + 谎报闸门）；聊天探讨路由（只读不调写工具）；故事段局部读取策略；批量工具 `story.delete_many`/`story.insert_many`（minItems=2，单写走单工具）；长批量超时恢复（ReadTimeout 降级 5→2→1 + checkpoint nudge）。
- **关键 commit**：`c9c3993`（AI 角色设计底座）→ `be72e6c`（一期）→ `744cd52`（web search）→ `1891a44`（聊天探讨）→ `9f8bc0f`（批量工具）→ `a646c3d`（超时恢复）。
- **状态**：已完成，用户多轮真机验收 PASS（见 memory AI-7D-R Test Phase Directive ⑧–⑫）。

### 2. Character Designer Agent（角色设计师）
- **域**：角色设定图写域（角色 canonical + 三视图生成）。
- **关键能力**：原生 tool-loop；`character.generate_views` 三视图生成；character-sync 中途重启不重复 Agnes；只写角色，不越权镜头/story。
- **关键 commit**：`c9c3993` → `536130c`（一期 + 三视图）→ `32c3d6f`（AI-7C real handler + self-test §44/§45/§99）。
- **状态**：已完成，AI-7C-S 稳定化收口 PASS（498 passed，§20 fresh reviewer CLEAR）。

### 3. Director Agent（导演）
子系统冻结状态：

#### 3a. Main Director（主导演）
- **域**：分镜写域（shot.create/update/delete/move + story/character 只读）。canonical 写权限唯一归 Main Director。
- **关键能力**：原生 tool-loop + 真相闸门（假成功纠偏/计数夸大收口/收尾重读）；三条镜头域 OCC 指纹（结构/单镜内容/集合内容）；单写串行 + 按工具防失控计数；超时批量降级；`_loose_json` 字符串化参数容错（大参数 + 中文引号行为）。
- **关键 commit**：`be72e6c`（一期）+ 多轮 AI-7D-R 真机收口（memory ⑧–⑫）。
- **状态**：已完成，多轮真机 PASS。

#### 3b. Scene Sub-Agent（场景子导演）— 本节点最新冻结
- **域**：per-scene 镜头重设计提案（proposal-only，**物理无 canonical 写权限**）。
- **核心链路**（真机重复走通 3 次）：`scene.delegate → SA 读 canonical → proposal.submit → MD proposal.read → reconcile → proposal.commit → DiffEngine → canonical`。
- **关键能力**：ephemeral 一次性 instance；DiffEngine（端态→原语，零 LLM）；OCC basis 防伪（scene_slice_fingerprint）；E1–E6 校验；re-grounding 扩展；三个嵌套参数字符串化兼容（end_state/removed_shot_rationales/basis）。
- **关键 commit**：`3d9d221`→`0f106c6`→`1c200be`→`18bd415`→`972efb9`→`3c51758`→`1c06f02`→`15a5277`→`40ca621`→`a994d2e`→`f7f633d`→`780c361`→`5d19b7b`（本体 7 阶段 + 治理）→ `9f51bd5`/`3cce547`/`0d752a1`（三字符串化修复）。
- **冻结验收**：详见 [[SCENE_SUBAGENT_V1_FROZEN]]。**PASS WITH NON-BLOCKERS**。

#### 3c. Clean Review / Session Governance（连续性审查 / 会话治理）
- **关键能力**：Zero-read Gate（读域真相闸门 + ID 存在性勘误）；Clean Review（独立干净会话只读审查，只报告不修改）；Session Re-grounding（结构修改累积后校准会话重置，归档不摘要）；Compaction 遥测（每 turn 落盘上下文规模）；structural flat-counting（幂等 no-op 正确排除）。
- **关键 commit**：`d27b516`（共享原语）→ `86e2ce0`（zero-read）→ `abdc066`（clean review）→ `e451847`（re-grounding）→ `731e8b0`（compaction）→ `89e93f3`（设计说明）。
- **状态**：已完成，长 run 真机验证（35 turn + re-grounding，post-reground stale 消除）。

## Agent System V1 总览
```
┌─────────────────────────────────────────────────────────┐
│                    Agent System V1                      │
│              Core Agents Complete & Frozen              │
├──────────────┬──────────────┬──────────────────────────┤
│ Script Writer│  Character   │       Director           │
│  (story/scene│  Designer    │  ┌────────────────────┐  │
│   写域)       │  (角色写域)    │  │ Main Director     │  │
│              │              │  │ (shot 写域，唯一)   │  │
│ web.search   │ generate_views│  ├────────────────────┤  │
│ 聊天探讨      │  char-sync   │  │ Scene Sub-Agent    │  │
│ 批量工具      │              │  │ (proposal-only)    │  │
│              │              │  ├────────────────────┤  │
│              │              │  │ Clean Review /     │  │
│              │              │  │ Session Governance │  │
│              │              │  └────────────────────┘  │
├──────────────┴──────────────┴──────────────────────────┤
│  共用：OpenAI-Agents-SDK 风格原生 tool-loop + 三层薄约束   │
│  （工具 schema / OCC 指纹 / 真相闸门）+ ProjectStore canonical │
└─────────────────────────────────────────────────────────┘
```

## 冻结纪律（V1 收口后）
1. **三个核心 Agent 不主动修改**：除非真实生产出现 canonical corruption / 越权写 / 核心 proposal 链路失效等**明确问题**。
2. **NON-BLOCKER backlog 不在本基线修**：进入 backlog，独立分支/后续版本处理。Scene Sub-Agent backlog 见 [[SCENE_SUBAGENT_V1_FROZEN]] N1–N9。
3. **下一阶段起点**：Agent System V1 已冻结，可作为下一阶段（如：多 Agent 协作编排 / Autopilot 全自动 / 生产规模化 / 新 Agent 扩展）的**稳定基线**。下一阶段工作应在**独立分支**进行，不混入本冻结基线。

## 关键基线指针
- **V1 Frozen（2026-09-01）**：submodule `e3bc902`，父 `3d781d1`，tag `v1-frozen-20260901`。详见 [[V1_FROZEN]]。
- **Scene Sub-Agent V1 Frozen（2026-09-03）**：submodule `0d752a1`，详见 [[SCENE_SUBAGENT_V1_FROZEN]]。
- **Agent System V1（2026-09-03，本节点）**：submodule `0d752a1`（同 Scene Sub-Agent 冻结点，因 Scene Sub-Agent 是最后一个补齐的子系统）。

## 证据与文档
- 架构文档：`docs/agents/director_subagent_v1_part{1,2,3}_*.md`（Sub-Agent 本体论/协议/实现）+ `docs/agents/session_governance_design.md` + `docs/agents/roles/*.md`（8 角色）
- 验收证据：`_stress/longrun_20260903_1203_FINAL_REPORT.md`（长 run）+ `_stress/logs/Subagent_Freeze*_*.events.jsonl`（冻结验收）+ `_golden_path_driver.py`/`_gp_verify_assets.py`（Golden Path）
- memory：见 `[[ai-7d-r-test-phase-directive]]` / `[[ai-7c-s-progress]]` / `[[scene-subagent-v1-smoke-20260903]]` / `[[v1-frozen-baseline]]` / `[[golden-path-verification-pass]]`
