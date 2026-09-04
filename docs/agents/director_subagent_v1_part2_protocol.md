# Director Sub-Agent Architecture V1 · Part 2：Delegation Contract / Proposal Protocol / Diff Engine / Registry

> 分支 `design/subagent-v1`。基线：Part 1（`5edc0e1`）+ Errata（`1c200be`，**冲突时以
> Errata 为准**）+ 接力可写性修复（`0f106c6`）+ diff spike（`937603c`，13 场景全 PASS）。
> **rev2 修订（架构检查）**：DiffEngine 拓扑序新增 **pre-detach 前置子阶段**（§6.1-D2 /
> §6.2 阶段 0.5）——原"delete 先行"在"删接力链中段 + 保留重接下游"的合法端态下会
> 级联误删 keep 镜（spike S11 实测复现），修订后 spike 14 场景全 PASS。
> 本轮范围：**协议与机制的字段级 schema + 算法规格**。可含确定性代码的规格说明
> （伪代码），但不实现 Scene Agent 执行链路、不修改主链路行为——所有新工具面
> 由配置门控（§2.4），默认关闭时主链路与当前基线 bit-for-bit 一致（§10 合规表）。

## 0. 术语与总览

### 0.1 端到端时序（一次 delegation 的完整生命周期）

```
MD turn（主会话，subagent 功能开启时）
  ├─ ① MD 调 scene.delegate(scene_id, task_brief, signal)          ← LLM 决策（spawn 提议）
  │     └─ Runtime 资格闸门（确定性 veto，§2.1）
  │          ├─ 信号依据非空 + 预算未超 + scene 存活 + 无同段在飞 delegation
  │          └─ 通过 → 构造 DelegationContract（§2：basis/邻接快照/记忆清扫/预算）
  │              → spawn 一次性工作实例（create_ephemeral, scope_type="scene_agent"）
  │              → 注入：Director 系统头 + scene 状态头 + 用户 brief
  │                       + 有效 decision memory + border 包 + 任务书（§3）
  │              → 实例 tool-loop（读白名单 + proposal.submit，§3.3）
  │                   ├─ 读 canonical（zero-read gate 照常上膛）
  │                   └─ proposal.submit(end_state, …)               ← 提案通道
  │                        └─ executor 确定性校验（§4.4：schema / 图 / 删申明 /
  │                             basis / 读取证据）
  │                             ├─ 不合格 → bounce（实例还活着，自愈重交，§6.2-E1）
  │                             └─ 合格 → 落盘 proposal store，返回 proposal_id
  │     ← scene.delegate 返回 {proposal_id, 结构化摘要}（≤500 字，§3.4）
  ├─ ② MD 调 proposal.read(proposal_id)（按需读全文，不进长期历史的重放负载）
  ├─ ③ MD 语义 reconcile（对照 global view + constraint_responses + bcr，§5）
  │     → proposal.commit(...) / proposal.reject(...)
  │          └─ commit executor = DiffEngine（零 LLM，§6）
  │               ├─ liveness + basis 复查（commit 时点权威校验）
  │               ├─ diff 派生 ops（pre-detach → delete → create(无锚) → reorder
  │               │    → update(含改锚)；S11 修订见 §6.1-D2）
  │               ├─ 串行执行（每步读最新指纹）+ commit journal（§7.3 崩溃恢复）
  │               └─ 终态断言 + receipts + work_memory + 记忆入库（accepted-only，E2）
  └─ ④ turn 收尾（惰性检测节点③）：既有双计数器照常计数 + registry sweep（§7.2）
```

同步模型（Part 1 Model A）：delegation 在 MD turn **内部**同步完成（实例即
`run_clean_review` 模式的推广——F4 实测 38s 只读；设计实例预算 600s，§11）。
跨 turn 异步 delegation 留给后续版本；本协议的 basis/liveness 复查设计对异步同样
成立，不需推翻。

### 0.2 本文档用到的既有事实（全部已核对源码）

| 事实                                                                                                                 | 位置                             |
| ------------------------------------------------------------------------------------------------------------------ | ------------------------------ |
| `SHOT_UPDATABLE_FIELDS` ≈ 30 个创作字段（含 `source_shot_id`，OCC 内容指纹覆盖接力）                                                | director.py L157               |
| `update_shot_relay` 校验：存在/同段/非自身/无环/**相邻**；`None`=解除并清 `source_version`                                            | structure\_service.py L409     |
| `move_shot` **不检查接力邻接**——canonical 可存在"悬空接力"（source 非紧邻前驱）                                                         | structure\_service.py L237     |
| 接力按构造只在段内（`update_shot_relay` 同段校验 + shot\_id 含段前缀）                                                                | 同上                             |
| `_scene_token`（SW 侧段内容指纹）= `SCENE_UPDATABLE_FIELDS`（title/location/time\_of\_day/summary/characters）的 sha256 前 8 位 | script\_writer.py L208         |
| `DEFAULT_MAX_TOOL_STEPS = 32`；`DIRECTOR_LONG_TASK_TOOLS` 有专门的超时/取消通道                                               | director.py L114/L135          |
| 资源锁为进程内 per-project（单写者串行化成立）                                                                                      | project\_events.py             |
| spike `apply_end_state` = DiffEngine 最小原型，拓扑序 delete→create→reorder→update 已固化                                     | spike/subagent\_diff\_spike.py |

***

## 1. 端态表达学（先于 schema：一个关键的形式决策）

### 1.1 接力图 = 相邻对属性 → `chain: bool`

**代码事实**：`update_shot_relay` 强制"接力镜头紧跟来源之后"；insert 拒绝在接力对
之间插入。因此 **canonical 中合法的接力边只存在于紧邻镜头对之间**——接力不是
任意 DAG，是"相邻对是否连链"的一组布尔值。

spike 用 `source_shot_id` 字符串表达端态接力（新镜头需 `__NEW_0__` 占位符，
S10）。正式 schema 改为：

> **`ShotSpec.chain: bool`** —— 本镜是否接力（沿用紧邻前一项的末帧）。
> `chain[0]` 必须为 `false`（段首镜无前驱；跨段衔接是 border 语义不是 relay）。

这一形式决策**在类型层面消灭**了 spike 中两类运行期错误：

| spike 场景 | source\_shot\_id 形式的错误                  | chain:bool 形式            |
| -------- | --------------------------------------- | ------------------------ |
| S4b 接力倒序 | 校验期错误"紧邻前驱不是来源"                         | **不可表达**（不存在指向非前项的引用）    |
| S10 新镜互锚 | 需要 `__NEW_0__` 占位符 + create-后-update 挂接 | 占位符消失；diff 引擎内部用真实 id 挂接 |
| 环        | 需环检测                                    | **不可表达**（有序列表中引用只能向前一项）  |

接力闭包/紧邻/无环三类校验从"运行期检查"降为"构造期不可能"。`validate_end_state`
保留的唯一图校验是：`chain[0] == false`（schema 强制）与 §4.3 的删申明完备性。

### 1.2 完备枚举语义与"未申明删除"守卫

端态 = **目标镜头集的完备有序枚举**（Part 1 §F）。推论：canonical 中存在、端态中
缺席的镜头 = 被删除。这是强语义——**遗漏即删除**，直接对着压测证据里"修改完备性
随轮次劣化"（E1 造重复、E2b 删不彻底）的失败模式，必须加确定性守卫：

> **`removed_shot_rationales`**：`{shot_id → 非空理由}`。diff 校验规则：
> ① `canonical_ids − end_state_ids` 中每个 id 必须在此映射里有非空条目，否则
> **bounce**（错误码 `undeclared_deletion`）——把"静默遗漏"变成"显式、带理由的
> 删除决策"；
> ② 映射中引用不存在于当前 canonical 的 id → **bounce**（`stale_removal_ref`，
> 提示重读）。

### 1.3 悬空接力的规范化（诚实标注）

`move_shot` 不查接力 → canonical 可含悬空接力（被 move 拉开的接力对）。端态的
`chain` 语义天然只表达合法相邻对，因此**每次经 DiffEngine 的 commit 都会把该段
接力图规范化为邻接合法**。这既是特性（repair-on-touch）也是隐性变更（Scene Agent
可能没意识到某对悬空接力会被它顺手"修掉"）。协议对策：scene 状态头（§3.2）**显式
列出当前接力边并标注悬空者**，任务书模板含一句"段内存在悬空接力时必须在端态中
显式决定（连链/断链）"。规范化行为记入 commit receipt（`relay_normalized: [...]`），
对 MD 可见。

### 1.4 字段级表达：省略 = 保持（keeps）

对保留镜头（`shot_id` 存在的项）：字段**省略 = 保持 canonical 当前值**，显式提供
\= 设为目标值。全集 = `SHOT_UPDATABLE_FIELDS − {source_shot_id}`（接力由 `chain`
表达，不走字段）。这避免"重述全字段"的 token 浪费与误改风险；镜头**集合**仍是
完备枚举（§1.2），只是字段是 delta。新建项（无 `shot_id`）省略字段 = 工具默认值。

***

## 2. DelegationContract（设计对象 1：字段 schema）

### 2.1 资格闸门（先于 contract 构造，确定性 veto）

| 检查       | 规则                                                                                                                                                                                                                                     | 失败动作                            |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| 信号依据     | `signal.kind == "pressure"` 且 registry 中该 scene 的 `structural_since_delegation ≥ SPAWN_PRESSURE_THRESHOLD`（建议 6，§11）；或 `signal.kind == "declared"` 且 evidence 非空（MD 的复杂度声明文本）；或 `signal.kind == "user_explicit"` 且 evidence 非空（用户原话引用） | 拒绝派发，回执给 MD"缺依据，请在主会话直接执行或补充声明" |
| 预算       | 本 turn MD 发起的 delegation < 3；全来源累计 < 6（硬上限，用户显式也受限但触发时**向用户明示**而非静默）                                                                                                                                                                   | 拒绝 + 明示                         |
| liveness | scene\_id ∈ 当前 canonical                                                                                                                                                                                                               | 拒绝 + 提示（可能刚被 SW 删除）             |
| 单段单飞     | 该 scene\_id 无 status ∈ {dispatched} 的 delegation（含本进程内存活句柄检查，§7.3）                                                                                                                                                                     | 拒绝"该段已有在飞 delegation"           |

`user_explicit` 的判定是 MD 的语义声明（引用用户原话），Runtime 不做语义识别；
确定性正则（类似 `_REVIEW_INTENT_RE`）只作**升级标注**不作依据——诚实记录：这条
信任 MD 的分类，由硬上限兜底。

### 2.2 字段表

```
DelegationContract {
  # ---- 身份 ----
  delegation_id: str            # 服务端生成 "dlg_" + hex12；唯一键
  scene_id: str                 # 目标段（stable id，非 position）
  created_at: float
  origin: {
    kind: "md_initiated" | "user_explicit"
    signal: { kind: "pressure" | "declared" | "user_explicit",
              evidence: str }          # pressure 时 Runtime 补记计数值
  }
  # ---- 任务 ----
  task_brief: str               # MD 撰写的自然语言任务书（含目标、范围、风格口径）
  # ---- 前提（防语义 lost update 的锚）----
  basis: {
    scene_slice_fingerprint: str        # §4.2 定义；dispatch 时刻从 canonical 现算
    scene_content_token: str            # = SW 侧 _scene_token（E5：内容域复用）
    shots_structure_token: str          # = _shots_token（全项目结构指纹，防段集变动）
    computed_at: float
  }
  # ---- 边界（§2.3 展开）----
  border: BorderPack
  locked_border_constraints: [LockedConstraint]     # 可为空
  # ---- 校准注入 ----
  memory_injected: [entry_id]          # 派发时通过前提清扫后的条目 id（§7.1）
  memory_quarantined_count: int        # 因前提失效被隔离的条目数（注入头明示）
  brief_text: str                      # 用户原始 brief（BriefStore.get()，可为 null）
  # ---- 预算 ----
  budget: { max_steps: int = 32        # = DEFAULT_MAX_TOOL_STEPS
            wall_clock_s: int = 600
            max_proposal_items: int = 24 }
  # ---- 实例 ----
  instance: { session_id: str          # create_ephemeral 生成
              scope_type: "scene_agent"
              scope_id: f"{scene_id}:{delegation_id}" }
}
```

### 2.3 BorderPack（攻击点：'出入口取几镜'必须定义）

```
BorderPack {
  window_k: int = 2                    # 每侧镜头窗口数（建议值，§11）
  prev: NeighborView | null            # 首段时为 null（注入文本显式写"无上游段"）
  next: NeighborView | null            # 末段同上
}
NeighborView {
  scene_card: { scene_id, title, location, time_of_day, summary,
                characters: [str], total_shots: int }   # = story.read 输出域 + 计数
  boundary_window: [ShotCard]          # prev=末 k 镜（序）；next=首 k 镜（序）
}
ShotCard { shot_id, description, characters, dialogue, source_shot_id, chain_dangling: bool }
```

取值依据：k=2 覆盖"出口镜 + 建立其铺垫的前一镜"（接力链语境下边界内容通常跨
两镜），对称用于入口；邻接段**整段镜头数** included 使 Scene Agent 知道窗口是窗口
（防"这就是全部"的误判）。`chain_dangling` 标注邻段自己的悬空接力（信息性）。
**派发时从 canonical 现算，不持久化为决策依据**（邻接 = volatile，Part 1 §7.4）。

```
LockedConstraint {
  constraint_id: str             # MD 编号 "bc_" + 序号
  applies_to: "entry" | "exit" | "both"
  statement: str                 # 自然语言约束（注入任务书）
}
```

**Part 1 §9.1 的 Part 2 澄清**：原文"违反即 proposal 校验失败（确定性检查）"中，
确定性检查覆盖的是**结构面**（段范围、图完备、删申明——这些 DiffEngine 确定性
拦截）；**语义面**（"出口必须停在暴雨未歇"这类内容约束）无法确定性判定，由
`constraint_responses` 机制（§4.1）+ MD reconcile（§5）承担。两层合起来才是
"locked"的完整含义。

### 2.4 工具面与 bit-for-bit 门控

MD 新增 4 个工具（均为 delegation 域，不碰 canonical 域 schema）：

| 工具                | 类别                                          | 说明                                 |
| ----------------- | ------------------------------------------- | ---------------------------------- |
| `scene.delegate`  | 长任务类（仿 `DIRECTOR_LONG_TASK_TOOLS` 的超时/取消通道） | §2.1 闸门 → 构造 contract → 跑实例 → 返回摘要 |
| `proposal.read`   | 只读                                          | 全文按需读取（Part 1 §10.2）               |
| `proposal.commit` | 写类（受单写串行 fence 约束）                          | DiffEngine 确定性执行（§6）               |
| `proposal.reject` | 写类（只写 proposal store，不碰 canonical）          | 记录决策（§5）                           |

**门控**：`offered_tools = DIRECTOR_TOOLS + (SUBAGENT_TOOLS if cfg.subagent_enabled)`。
默认 `false` → 工具列表与系统提示逐字节不变 → 主链路 bit-for-bit（§10 合规表）。
Scene Agent 实例是独立一次性会话，其存在不影响主会话。

***

## 3. Scene Agent 工作实例（contract 的消费侧）

### 3.1 会话机制

`create_ephemeral("director", "scene_agent", f"{scene_id}:{delegation_id}")`——
不复用、用完即弃（`create_ephemeral` 既有语义，scope\_type 专用值不进复用键）。
实例 = `_run_tool_loop(tools_whitelist=SCENE_AGENT_TOOLS)`：

```
SCENE_AGENT_TOOLS = DIRECTOR_READ_TOOLS ∪ {"proposal.submit"}
# 不含 web.search（V1 刻意聚焦局部设计；审查实例需要查证，设计实例不需要。
#  与 REVIEW_READ_TOOLS 的差异是有意设计，非遗漏）
```

写域真相闸门不上膛（同 review 模式——实例无 canonical 写工具）；**zero-read gate
照常上膛**；`proposal.submit` 的读取证据前置检查（§4.4-E5）比 gate 更强。

### 3.2 校准注入包（首条 user 消息，一次性）

```
【Scene Agent 任务开启】你是段 {scene_id} 的分镜设计实例……（角色与规则 preamble：
 必须用读工具自主读取；你没有 canonical 写权限；产出=proposal.submit；
 悬空接力须显式决定；以下记忆是带前提的先验不是证据）

# 段状态头（系统生成，当前状态以此为准）
{scene 内容字段 + 有序镜头全列表（id/描述摘要/接力边/悬空标注/每镜内容指纹）}

# 邻接与边界（本次任务快照，不作为持久依据）
{BorderPack 渲染}
# 本次任务的锁定边界约束（不可违反；如认为必须修改 → border_change_request）
{locked_border_constraints 或 "（无）"}

# 用户创作约束（原始 brief）
{brief 或 "（未记录）"}

# 先验设计决策（advisory，含出处与前提指纹；{quarantined_count} 条因前提
  失效已被隔离；事实必须用读工具重新确认）
{有效 decision memory 渲染，≤ K 条}

# 本次任务书（Main Director）
{task_brief}

# 提案格式与提交规则
{端态 schema 说明 + proposal.submit 用法 + 预算}
```

### 3.3 `proposal.submit`（提案通道，唯一产出形式）

参数 = §4 的 ProposalReturn 内容字段。executor 在**落盘前**做 §4.4 全部确定性
校验；不合格 → 返回结构化错误（错误码 + 修复指引），实例可修后重交（**bounce
即自愈**，同 OCC stale-retry 的交互模式）。多次提交合法：每次生成新
proposal\_id，先前者标 `superseded`；**最后成功提交者为有效提案**。循环终止由
`max_steps` / `wall_clock_s` 兜底。

### 3.4 返回给 MD 的结构化摘要（≤500 字，Part 1 §10.2）

```
{proposal_id, scene_id, basis_fp,
 shots: {before → after, creates, deletes, moves, updates},
 chain_breaks: [位置摘要],          # 段内链结构变化点
 border_change_requests: [...],     # 原文（短）
 constraint_responses_complete: bool,
 read_evidence: {n_read_calls},
 design_notes_excerpt: str}         # 截断摘录
```

***

## 4. ProposalReturn（设计对象 2：字段 schema）

### 4.1 字段表

```
ProposalReturn {
  proposal_id: str               # 服务端生成 "prop_" + hex12
  delegation_id: str             # 回指 contract
  scene_id: str                  # 必须与 contract 一致（executor 校验）
  basis: { scene_slice_fingerprint: str }    # 防伪规则见 §4.4-E4

  # ---- 端态（§1 表达学）----
  end_state: [ShotSpec]                      # 有序完备枚举；可为空（删光本段全部镜头，
                                             # 须配 removed_shot_rationales 全量）
  removed_shot_rationales: {shot_id: str}    # §1.2 守卫

  # ---- 边界与约束回应 ----
  constraint_responses: [ {constraint_id, response: str} ]   # 每条 locked constraint
                                                             # 必须有一条回应（§4.4-E6）
  border_change_requests: [ {target: "prev"|"next",
                             statement: str, reason: str} ]  # 可空；显式请求，非协商

  # ---- 理由与依赖（记忆种子 + E4）----
  design_notes: str                         # 段级设计理由（提案级 rationale）
  design_dependencies: [Dependency]          # 提案级依赖声明
}

ShotSpec {
  shot_id: str | null          # 非空 = 保留/修改既有镜（必须 ∈ 本段 canonical）；
                               # null = 新建（id 由服务端分配，先建后锚）
  chain: bool                  # §1.1；item 0 必为 false（schema 强制）
  fields: {…}                  # SHOT_UPDATABLE_FIELDS − {source_shot_id} 的子集；
                               # keeps 省略=保持（§1.4）；creates 省略=默认
  rationale: str               # 本镜设计理由（decision memory 种子，建议非空）
  dependencies: [Dependency]   # 本镜决策前提声明（E4）
}

Dependency {
  kind: "shot" | "scene_content" | "character_canonical"
      | "border" | "adjacency" | "brief"
  ref: str                     # shot → shot_id；character → 名单；其余 → "current"
}
```

**Runtime 强制覆盖（E4）**：`kind ∈ {border, adjacency}` 的依赖 → 相关
rationale **不持久化**（volatile，只在 proposal store 留审计）。`brief` 类永不失效。
`shot`/`scene_content`/`character_canonical` 进记忆条目的 premises（§7.1）。

### 4.2 per-scene 派生指纹（正式定义，E5 复用关系）

```
scene_slice_fingerprint(scene) =
  sha256_8( json( sort_keys, {
    "content": {SCENE_UPDATABLE_FIELDS 五字段},          # 与 _scene_token 同域
    "shots": [ [shot.shot_id, _shot_token(shot)]         # 复用既有单镜内容指纹
               for shot in scene.shots 有序 ]             # （含 source_shot_id → 接力
  } ) )                                                   #  变更天然覆盖，0f106c6）
```

与 spike 版差异：spike 只哈希 `description`；正式版纳入**全部**单镜内容指纹
（`_shot_token` 覆盖 \~30 字段 + 接力）——失效方向更保守（宁可错杀）。三性质：
确定性、可从 canonical 现算、**零 canonical schema 变更**（读时派生）。

### 4.3 端态合法性（DiffEngine 第 0 阶段的规格，零副作用）

1. schema：`end_state[0].chain == false`；ShotSpec 字段名 ∈ 白名单；
   `shot_id` 非空时 ∈ 本段当前 canonical（否则 `unknown_shot_id` / `foreign_scene_id`）；
   项数 ≤ `max_proposal_items`。
2. 删申明完备：§1.2 两条规则。
3. （chain:bool 使接力闭包/邻接/环**构造性成立**，无需运行期校验——§1.1。）

### 4.4 submit 时确定性校验清单（全过才落盘）

| #  | 校验                                                                                        | 错误码                                | 处置                                |
| -- | ----------------------------------------------------------------------------------------- | ---------------------------------- | --------------------------------- |
| E1 | §4.3 全部                                                                                   | `bad_end_state`                    | bounce（附具体项）                      |
| E2 | `delegation_id`/`scene_id` 与实例 contract 一致                                                | `contract_mismatch`                | bounce                            |
| E3 | `constraint_responses` 覆盖全部 locked constraints                                            | `missing_constraint_response`      | bounce                            |
| E4 | **basis 防伪**：`basis.scene_slice_fingerprint` ∈ {本实例读工具真实返回过的指纹} **且** == 当前 canonical 现算值 | `basis_fabricated` / `basis_stale` | bounce（stale 附当前指纹 + "重读后按新基线重交"） |
| E5 | **读取证据**：trace 中存在 ≥1 次 `shot.list(scene_id)` 或本段镜头的 `shot.read`                          | `read_first`                       | bounce（F2 机制在提案域的直接拦截）            |
| E6 | `end_state` 非空时 `removed_shot_rationales` 覆盖 §1.2                                         | `undeclared_deletion`              | bounce                            |

`read_evidence.n_read_calls` 由 executor 从 trace 统计（**不是 LLM 自报**——防"自证
读取量"本身被幻觉）。

***

## 5. ReconcileDecision（设计对象 3）

### 5.1 四种决策的判定规则

| 决策          | 触发条件（全部满足）                                                                                                   | 执行                                                                                                                                                                           |
| ----------- | ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **accept**  | ① basis 复查通过（commit 时点 §6.2-C1）；② MD 语义认可（end state 对照 global view；每条 `constraint_responses` 核实非敷衍；bcr 裁决完成） | `proposal.commit(proposal_id, decision_rationale)` → DiffEngine 全量执行                                                                                                         |
| **rebase**  | basis 复查**失败**（dispatch→commit 间该段被改：MD 自己的写 / SW story.update）且 MD 认为设计意图仍有效                                | **不套用旧端态**（Part 1 §F）。执行 = `proposal.reject(kind="rebase")` + 新 `scene.delegate`（任务书 = 原意图 + 变更说明的 delta brief；新实例在新 basis 上重推导）。回退 = MD 用既有写工具手工完成（它有唯一写权限），决策记录标注 `manual` |
| **reject**  | MD 语义否决（设计质量/约束违反/bcr 不可接受）                                                                                  | `proposal.reject(proposal_id, kind="rejected", reasons[], decision_rationale)`；rationale 只留 proposal store，**不入记忆**（E2）                                                      |
| **partial** | MD 认可子集（如剔除某个越界的新镜）                                                                                          | `proposal.commit(proposal_id, decision_rationale, exclude_item_refs=[端态数组下标])`；排除语义见 §5.2                                                                                    |

**bcr 的裁决与记录**：`border_change_requests` 由 MD 在 reconcile 时裁决
（accept/reject/改写），裁决写入全局决策记忆（§8）——与 item 采纳**解耦**：被接受
的 bcr 影响的是**下一轮**双方 delegation 的 locked constraints（Part 1 §11.5 防循环
设计）。partial 与 bcr 的联动风险见 §9.4。

**决策强制留痕**：`decision_rationale` 是 commit/reject 的**必填**参数——它既是
审计记录又是全局记忆的种子（§8），缺失即工具调用非法。

### 5.2 partial adoption 的排除语义（spike S9 的一般化）

```
exclude_item_refs: [int]        # end_state 数组下标
排除一个项的效果：
  - create 项 → 从目标集合剔除（不创建）
  - keep 项   → 保留在集合中，但其 fields 回退为 canonical 当前值（该镜"维持现状"）
排除后的有效端态 = 原端态 − 被排除项（keeps 保留位置），随后**全量重跑 §4.3 校验**
```

**链切规则（deterministic）**：被排除项的**下一个保留项**若 `chain == true`，
该排除非法（`chain_cut_by_exclusion`）——排除不能隐式改变一个保留镜的接力语义
（它声明接力的是"被排除的那个前驱"）。合法排除 = 被排除项是末项，或其后继
`chain == false`。S9a（剔除尾部新镜）合法；S9b 类（剔除链中段）被拒。

***

## 6. DiffEngine 规格（设计对象 4：spike 原型 → 正式规格）

### 6.1 定义与不变式

```
DiffEngine: (current_canonical_scene_slice, effective_end_state, basis) → op 列表 → 执行
性质：
  D1 纯函数派生：op 列表 = F(current, effective_end_state)，零 LLM、零隐藏状态
  D2 拓扑序不变式（固定，spike 已固化，S11 修订）：
       pre-detach(待删镜头的 keep 下游先解除接力) → delete →
       create(不带接力，追加段尾) → reorder(selection 式 move)
       → update(内容 + 改锚/解除)
       【为何 pre-detach 必须先行】canonical 接力边在 delete 时刻仍存活（下游
       锚着待删镜），不先解除会级联误删 keep 镜——"删接力链中段 + 保留重接下游"
       是合法端态（端态的 chain 已声明重接），delete 先行会把它误判为
       ABORT_INTEGRITY。预 detach 只处理 keep 下游（del 下游的级联是合法删除）。
  D3 每步用最新指纹（结构 op 前重算 _shots_token；内容 op 前重算 _shot_token）
  D4 可重算性（崩溃恢复的根基）：F(current, desired) 对 current 的任意
     "已部分变换"中间态重跑，得到的 op 列表 = 剩余变换（§7.3）
  D5 零副作用校验先行：§4.3 + §5.2 重校验通过前不产生任何 op
```

### 6.2 阶段规格（伪代码，spike `apply_end_state` 的正式化）

```
阶段 C（commit 时点权威复查；任何失败 → 不产生 op）
  C1 liveness：scene_id ∈ canonical？        失败 → ABORT_LIVENESS（§6.3）
  C2 basis：scene_slice_fingerprint == proposal.basis？ 失败 → MD_REBASE
  C3 有效端态重校验（§4.3 + partial 排除后的 §5.2）

阶段 D（分类；零副作用）
  keep   = {item.shot_id ∈ current}
  del    = current_ids − keep（每项必须在 removed_shot_rationales，C3 已查）
  create = items 无 shot_id（不含被排除项）
  期望序 = [item.shot_id（keeps）或分配占位（creates）按 end_state 序]

阶段 0.5 pre-detach（S11 修订，先于一切 delete）
  for sh in canonical_scene.shots:
    if sh.source_shot_id ∈ del 且 sh.shot_id ∈ keep:
      shot.update(sh.shot_id, source_shot_id=None)   # 解除（内容指纹随即变化，
  ops.append(("pre-detach", sh.shot_id, …))           #  阶段 4 每镜前重算，天然兼容）
  # 被解除镜的端态 chain 期望由阶段 4 的 relay_op 重新应用（改锚需邻接——
  # reorder 在阶段 3；中间态"暂无接力"合法）。update 不改变结构指纹（_shots_token
  # 只含 id 序），后续 delete 的结构 OCC 不受影响。

阶段 1 delete（级联守卫 = 纵深防御）
  for sid in del:
    r = shot.delete(sid)                    # 真实 executor，receipt 入账
    if r.cascaded ∩ keep ≠ ∅: → ABORT_INTEGRITY（应被 C3/阶段0.5 拦下的 = 引擎 bug 警报）
  # 注：端态已保证被保留镜的 chain 指向保留项（C3 构造性成立），且阶段 0.5 已
  # 解除 keep 下游对待删镜的活接力边——意外级联只可能来自引擎缺陷，故为完整性
  # 警报而非 agent 错误

阶段 2 create（追加段尾，spec 不含 source_shot_id——先建后锚，0f106c6 语义）
  create_many（≥2）或 create；回填真实 id 到期望序占位

阶段 3 reorder（selection 式 move；每步前重算结构指纹）
  for i in 0..n: current[i] != expected[i] 时
      move(expected[i], after=expected[i-1] / before=current[0])

阶段 4 update（内容 + 接力；每镜前重算内容指纹）
  for item in 有效端态:
    fields_diff = item.fields − canonical 当前值（keeps；空则跳过——比 spike 更省）
    relay_op:  期望 chain 与 canonical 当前接力不一致时
               → source_shot_id = (chain ? 前一项真实 id : None)
               （改锚需邻接——阶段 3 已保证；走 update_shot_relay 语义）
    fields_diff ∪ relay_op 非空 → shot.update（relay 在 save 前应用，0f106c6 原子性）

阶段 5 终态断言
  canonical 有序 id 列表 == 期望序？  失败 → ABORT_INTEGRITY
  receipt 汇总：ops / relay_normalized（§1.3）/ work_memory 一条 delegation 摘要
```

### 6.3 错误分类学（三类处置的判定依据）

| 类别                          | 错误                                                                                   | 检测点             | 判定依据（为什么归这类）                                                                                                                                                                                                                   |
| --------------------------- | ------------------------------------------------------------------------------------ | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **bounce（打回 Scene Agent）**  | E1–E6 全部（§4.4）                                                                       | proposal.submit | **实例还活着**且错误源于它的产出——自愈成本 = 同实例内修正重交（OCC stale-retry 同模式）。涵盖：schema/图/删申明/约束回应缺失/basis 防伪与过期/读取证据                                                                                                                               |
| **MD 手动（标记 Main Director）** | `MD_REBASE`（commit 时 basis 过期）、`INTERRUPTED_COMMIT`（§7.3）                            | proposal.commit | **实例已消亡**（delegation 已返回），无法 bounce；且 MD 持有唯一写权限与全局视野——delta re-delegation 或手工修复都是它的合法动作                                                                                                                                       |
| **abort（整体中止）**             | `ABORT_LIVENESS`（scene 消亡，S8）、`ABORT_INTEGRITY`（级联守卫/终态断言失败）、执行期 op 异常（ OCC stale 等） | commit 各阶段      | liveness 是生命周期终态（retire 联动）；integrity = **应该已被上游拦截的错**，出现即引擎缺陷， loud log + 停止（不 bounce——不是 agent 的错）；执行期异常 = canonical 可能已部分变换，**诚实部分报告**（已执行 ops 的 receipts 全量返回，绝不假成功）+ `INTERRUPTED_COMMIT` 状态 → MD 决策（`resume` 重算剩余 or 手工） |

**分类边界的原则**：错误归给**能在最低成本处修复它的一方**，且只归给有证据指向的
一方——submit 阶段一切归实例（它可自愈），commit 阶段一切归 MD（实例不在了），
"不应发生的"归引擎自身（警报而非甩锅）。纵深防御层（级联守卫、终态断言）的
存在意义是捕获引擎 bug，不是扩展 bounce 面。

***

## 7. SceneAgentRegistry（设计对象 5：落盘形态 + 三节点惰性检测）

### 7.1 落盘布局

```
output/agents/scene_agents/
  registry.json                 # 索引：{version, scenes: {scene_id: {
                                #   status: active|retired,
                                #   first_delegation_at, last_delegation_at,
                                #   delegation_count, accepted_proposals,
                                #   structural_since_delegation: int,   # spawn 压力计数
                                #   active_memory_entries, quarantined_count }}}
  memory/{scene_id}.json        # {entries: [MemoryEntry]}（retire 后随身份归档，不删）
  delegations/{delegation_id}.json   # 生命周期 + commit journal（§7.3）
  global_memory.json            # Main Director 全局决策记忆（§8）
```

```
MemoryEntry {
  entry_id: str
  scene_id: str | null               # null = 全局（§8）
  text: str                          # 决策 + rationale
  provenance: { delegation_id, proposal_id,
                basis_fingerprint }  # E2/E6：入库时点的 scene slice 指纹
  premises: {                        # 可检测前提（自动 + 声明）
    shots: [ {shot_id, token} ]      # 引用镜的内容指纹（E6：防 id 复用混淆）
    scene_content_token: str         # 自动
    characters: [str]                # 声明的 character_canonical 依赖
  }
  volatile: bool                     # 声明 border/adjacency 依赖 → true → 不落此文件
  status: active | quarantined
  created_at: float
}
```

**入库规则（E2）**：只有 `proposal.commit` 成功后，从**实际 commit 的有效端态**
（partial 时 = 排除后版本）提取 `rationale`/`design_notes` 生成条目；`volatile=true`
的只留 proposal store。**rejected/stale 永不入库**。

**失效规则（惰性，派发节点执行）**：条目 `active` 且（所有 `shots` 的 token 与当前
一致 **且** `scene_content_token` 与当前一致）→ 保持；任一不匹配 → `quarantined`
（保留文件内供审计，注入头明示计数）。粒度 = per-shot + scene 内容（比 Part 1 的
整段一刀切更精细：MD 改 A 镜不灭 B 镜的决策——方向仍是宁可错杀：引用面越广
越易失效）。

**注入上限**：`K = 12` 条（最近优先），另加 per-shot 保留 `M = 3`（同镜只留最新
3 条 active）+ 段级 notes 保留 4 条（建议值，§11）。

### 7.2 三节点惰性检测（全部确定性、零守护进程、SW 零感知）

| 节点                  | 时点                                   | 动作                                                                                                                                                                                                                                                           |
| ------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| ① **派发**            | `scene.delegate` 闸门通过后、构造 contract 前 | liveness（不在 canonical → 拒绝 + 顺手 retire）；记忆前提清扫（§7.1 失效规则）；邻接快照现算（volatile 定义上不持久）；单段单飞检查                                                                                                                                                                     |
| ② **commit**        | DiffEngine 阶段 C                      | liveness 复查（飞行中 SW 删段 → `ABORT_LIVENESS` + retire，S8）；basis 复查；commit journal 落盘（§7.3）                                                                                                                                                                       |
| ③ **turn 收尾 sweep** | MD turn 成功收尾（搭载既有治理后处理，零 LLM）        | registry scenes − canonical scene\_ids → retire（SW `story.delete` 的检测点）；孤儿 delegation 清算（§7.3）；`structural_since_delegation` 从本轮 receipts 按 scene 聚合累加（receipts 带 scene\_id）；delegation 摘要已由 commit executor 写入 work\_memory（→ 状态头 / re-ground 携带物自动可见，零新管道） |

### 7.3 崩溃 / 并发一致性（攻击点，§9.3 详析）

**并发**：单后端进程 + per-project 资源锁 → 同项目 MD turn 串行、commit 串行；
registry/memory/delegations 全部 `atomic_write_json`（既有原子写）→ 无撕裂文件。
registry 无独立锁需求（继承写者串行化）。

**delegation 生命周期状态机**（`delegations/{id}.json` 内）：

```
dispatched → proposed → (committed | rejected | partial)
dispatched → aborted            # 崩溃/超时/取消，无有效提案
committing → committed | partial | interrupted_commit
```

**崩溃窗口逐个分析**：

| 窗口                   | 盘上状态                             | 恢复（全部惰性，下一 MD turn / 派发节点触发）                                                                                                                                                                                                                                             |
| -------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 实例运行中崩溃              | `dispatched`，无 proposal          | sweep：`dispatched` 且无本进程存活句柄且无 proposal → `aborted`（单进程语义：重启后无任何实例存活，判定确定性成立）；delegation 摘要写 work\_memory → MD 下轮可见                                                                                                                                                      |
| submit 后、reconcile 前 | `proposed`                       | 无需恢复——proposal 在 store，MD 下轮继续 reconcile（basis 若已过期走 rebase 路径）                                                                                                                                                                                                          |
| **commit 中途崩溃**      | `committing` + journal（op 列表已落盘） | **可重算恢复（D4）**：`proposal.commit(proposal_id, resume=true)` 对**当前** canonical 重跑 `F(current, desired)` = 恰好剩余变换；恢复**必须 MD 显式触发**（中断可能是用户主动停止，不自动续跑）；basis 检查对 resume 豁免（current ≠ 原 basis 是构造性的）。若 MD 不续跑：`interrupted_commit` 终态留档，canonical 停在合法中间态（每个原语各自原子且自洽），MD 手工收尾 |
| turn 收尾 sweep 前崩溃    | 计数/sweep 未跑                      | 下一成功 turn 补（既有治理同款语义：STOPPED/FAILED 只记数不触发）                                                                                                                                                                                                                              |

journal 内容：`{delegation_id, proposal_id, ops_plan（派生的 op 列表）, executed: [前缀 receipts], started_at}`——executed 前缀是**报告用**（诚实部分报告），恢复依据是
D4 重算而非 journal 回放（重算不依赖 journal 的执行进度记录，journal 只保证
"有人知道这里曾中断"）。

***

## 8. Main Director 全局决策记忆（设计对象 6，Errata E3）

### 8.1 条目来源（锚定真实决策，无自由格式工具）

| 来源           | 触发                                        | 条目                                 |
| ------------ | ----------------------------------------- | ---------------------------------- |
| reconcile 决策 | 每次 commit/reject（`decision_rationale` 必填） | "s05 提案 accept：保 B 弃 A，因为…"        |
| bcr 裁决       | reconcile 中对 border\_change\_request 的处理  | "接受 s05/s06 边界改为夜戏衔接，依据…"          |
| 用户显式全局指令     | MD 在 delegation 调用中转述（`signal.evidence`）  | 间接留痕于 origin，不单独建条（brief 机制覆盖用户约束） |

**刻意不做**自由格式 `memory.record` 工具：无锚定的自由记忆会退化成"第二个会话
历史"（F2 机制）；锚定到真实决策工件（proposal\_id）的条目才有可审计的 provenance。
非提案类全局决策（V1）接受其会话态（Re-grounding 截肢）——已知缺陷 §12。

### 8.2 条目 schema 与 Re-grounding 携带物扩展

`MemoryEntry`（§7.1）`scene_id = null`，premises 可空（空 = 永不自动失效的
standing decision，注入上限 `K_g = 8` 兜底增长）。

`reground_session` 携带物白名单扩展（**条件追加**，E3）：

```
seed = [校准提示, 状态头, brief,
        （若 global_memory 非空）"# 全局导演决策（advisory，带出处；事实以状态头为准）\n{≤K_g 条}",
        最近意图]
```

**bit-for-bit 保护**：无 subagent 活动的项目 global\_memory 为空 → 该段不出现 →
seed 与现行逐字节一致。有活动时行为变化是 E3 要求的功能本身（修复"全局决策理由
在 Re-grounding 后丢失"），不是回归。

***

## 9. 攻击性分析（必测四项 + 补充）

### 9.1 contract 字段缺失导致的歧义（逐字段攻击表）

| 若缺失/未定义               | 后果                                         | 本 schema 的封堵                                                 | <br />                   |
| --------------------- | ------------------------------------------ | ------------------------------------------------------------ | ------------------------ |
| border 窗口大小（"出入口取几镜"） | Scene Agent 不知快照边界，把窗口当全貌或反向要求读全片（越权读取扩散）  | `window_k=2` 显式 + `total_shots` 让窗口性可见                       | <br />                   |
| 首/末段的邻接               | null 语义不明 → 实例臆造"上游存在"                     | \`prev/next: NeighborView                                    | null\` + 注入文本显式"无上游/下游段" |
| basis                 | 提案无锚 → MD 改动后套用旧端态 = 语义 lost update（S7 反面） | contract 与 proposal 双层携带 + submit/commit 双时点校验 + 防伪（§4.4-E4） | <br />                   |
| 预算                    | 实例无界运行（成本失控）/ 端态无界（巨型提案撑爆 reconcile）       | `budget` 三字段 + executor 强制                                   | <br />                   |
| 删申明                   | 遗漏即删除的强语义变成静默数据丢失（E1→E2b 完备性劣化的放大器）        | `removed_shot_rationales` 守卫（§1.2）                           | <br />                   |
| chain 的 item-0 规则     | 段首镜声明接力 → 引用不存在的前驱                         | schema 强制 `chain[0]=false`                                   | <br />                   |
| 空端态语义                 | "删光本段" vs "无提案"混淆                          | 空端态合法但须全量删申明；"无提案"= 未 submit，由 delegation 状态区分               | <br />                   |
| memory 注入的前提标注        | 先验被当证据（F2 记忆版）                             | 注入头 advisory 声明 + 隔离计数 + zero-read gate + E5 读取证据            | <br />                   |
| 读取证据口径                | LLM 自报读取量 = 又一个可幻觉字段                       | executor 从 trace 统计（§4.4-E5 注）                               | <br />                   |

### 9.2 diff 错误分类的边界（打回 vs 手动 vs abort）

攻击：**"为什么不全部 bounce / 全部 abort？"**——若 commit 阶段错误也 bounce，
需要复活已消亡的实例（工作上下文已弃）或保活全部实例（成本 + 复制主会话的
staleness 问题）；若 submit 阶段错误也 abort，一个可自愈的 schema 笔误就浪费整次
delegation。分界线 = **实例存活状态**（submit 时活 / commit 时死）×**责任归属**
（agent 产出 / MD 上下文 / 引擎自身）。三条防误分类规则：① 纵深防御层的触发
（级联守卫、终态断言）永远 abort + 警报——它们捕获的是引擎 bug，bounce 会把
引擎缺陷伪装成 agent 错误并诱发无意义重试循环；② 执行期 OCC stale 归
`INTERRUPTED_COMMIT`（诚实部分报告）而非 bounce——串行 commit + 每步最新指纹下
stale 不应发生，发生了就是并发假设被打破，需要 MD 注视而非实例重试；③ liveness
失败联动 retire，不是可重试错误（scene 已不存在，重试无意义）。

攻击：**"bounce 会不会死循环？"**——`max_steps=32` + `wall_clock_s=600` 硬截断；
bounce 错误码全部带具体修复指引（镜像 OCC stale 回执"直接用回执里的正确指纹重试"
的已验证交互模式）。

### 9.3 registry 崩溃 / 并发一致性

攻击：**"delegation 飞行中进程重启"**——见 §7.3 表。核心论点：① 判死是确定的
（单进程语义：重启即无存活实例，不需要心跳/租约这类分布式机制——诚实标注：若
未来多进程部署需重审）；② commit 中断的恢复靠 **D4 可重算性**而非日志回放——
diff 是 `(current, desired)` 的纯函数，对任意中间态重跑得剩余变换，这是比
write-ahead log 更适合本系统的模型（op 全部是幂等语义上的"到达端态"函数，
且每个原语各自原子）；③ 恢复必须显式（防"用户杀进程却被自动续跑"）。
攻击：**"sweep 与派发竞争？"**——同 turn 内派发闸门先行检查 + 单段单飞 + 写者
串行化；sweep 只在 turn 边界跑，与派发不重叠。
攻击：**"registry 与 canonical 谁是真源？"**——canonical 永远是真源；registry
是**缓存 + 记忆载体**，任何不一致以 sweep 对账 canonical 收场（retired 误标可由
再次 spawn 复活——liveness 检查以 canonical 为准，registry 状态只影响记忆文件的
挂载与否）。

### 9.4 partial adoption 与 border 联动

攻击：**"MD partial 采纳了端态，但被剔除的项正是支撑某条 constraint\_response /
bcr 的设计"**——协议不试图确定性判定"哪些项支撑哪条回应"（语义问题），而是
三点结构性防御：① **链切规则**（§5.2）保证 partial 不隐式改变保留镜的接力语义
——结构面的联动被确定性封死；② commit 回执附带**有效端态**（排除后版本）的
constraint\_responses 重印，MD 在收据里直接看到"承诺对的是哪个版本"；③ bcr 裁决
与 item 采纳解耦（§5.1）——bcr 影响下一轮 locked constraints，本轮 partial 缩水
不自动撤销已接受的 bcr；MD 若反悔，路径是在下一轮 delegation 的
locked\_border\_constraints 里改锁（flip-flop 守卫照常计数，Part 1 §11.5）。
残余风险：MD 语义上"剔除了出口镜却保留了'出口停在暴雨'的约束回应"这类人级
疏忽——记入 §12，由事后 Clean Review（全局审查权）兜底。

### 9.5 补充攻击（本轮自加）

- **实例提交了他段镜头 id**：E2 `contract_mismatch` / `foreign_scene_id` 拦截。

- **实例伪造 basis**（抄历史指纹）：E4 防伪——必须 ∈ 本实例真实读到的指纹集合。
  代价：submit executor 需要访问实例 trace（同进程内可得，实现注记）。

- **MD 用 delegation 逃避主会话工作**（delegation-as-avoidance，Part 1 §4.2 风险）：
  资格闸门 + 每轮上限 3 + 计数器全落盘（可审计）。

- **同一 turn 多段 delegation**：允许（Planning 可并行）；V1 运行时**串行执行**
  （工具按序跑完）；协议本身不禁止真并发（per-scene basis 隔离 + commit 串行点），
  真并发执行留给后续版本——诚实标注，不算 Part 1 "Planning 可并行"的违反
  （并行的是 MD 的规划发起，不是执行）。

- **空 scene 的 delegation**（S1）：合法——从零设计；端态全 create，删申明为空。

***

## 10. 硬不变式合规表（交付自查）

| 不变式                                         | 机制                                                                                                              | 自查          |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ----------- |
| canonical 写权限唯一归 MD（串行 commit）              | Scene Agent 白名单物理无写工具；commit = MD 调用的确定性 executor，受单写 fence                                                     | ✅ §3.1/§2.4 |
| 跨 delegation 持久物必须带可检测前提；rejected/stale 不入库 | MemoryEntry.premises（token 级）+ E2 入库时点 + E4 volatile 不落盘 + submit/commit 双时点 basis                              | ✅ §7.1/§4.4 |
| 生命周期检测惰性 + 确定性                              | 三节点（§7.2）；无守护进程；sweep 纯集合运算；SW 侧零接口变更                                                                           | ✅           |
| DiffEngine 零 LLM                            | D1 纯函数；错误处置分类学不含"问 LLM"路径                                                                                       | ✅ §6        |
| 主链路 bit-for-bit（无 delegation 时）             | `subagent_enabled` 默认 false → 工具列表/系统提示不变；reground 扩展段条件出现；治理计数只在 commit 时产生（无 commit = 无变化）；实例为独立 ephemeral 会话 | ✅ §2.4/§8.2 |
| （本轮）不实现执行链路、不改主链路                           | 本文档 = 规格；spike 已在 `spike/`（非生产代码）；无生产代码改动                                                                       | ✅           |

***

## 11. 建议数值阈值汇总（全部标注依据，不拍死，实现后按遥测调）

| 常量                         | 建议值    | 依据                                                                                                                               |
| -------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------- |
| `SPAWN_PRESSURE_THRESHOLD` | 6      | 实测退化在项目级 \~13 次结构写后（E1+E2+E2b）；压测中重压力集中在 2 段 ≈ 5–6 次/段；段级浓度先于项目级总量到达设计负载。**低于** Clean Review 的 8：delegation 是比"审查+重置"链更早介入的廉价干预点 |
| `MAX_DELEGATIONS_PER_TURN` | 3      | 每实例 = 完整 LLM 会话（成本）；3 段并行 redesign 已是单轮罕见规模                                                                                      |
| `HARD_DELEGATION_CAP`      | 6      | 用户显式也受限（Part 1 §4.2"防无限 spawn"），超限**明示**不静默                                                                                      |
| `BORDER_WINDOW_K`          | 2 镜/侧  | 接力链语境下边界内容通常跨两镜（出口镜 + 铺垫镜）；k=1 丢铺垫，k=3 冗余                                                                                        |
| `MEMORY_INJECTION_CAP` K   | 12     | 段设计上下文规模（5–12 镜段 × 每镜 1 条 + 段级 notes 4）；防注入面成为新失真源                                                                               |
| `PER_SHOT_RETENTION` M     | 3      | 同镜决策保留最新 3 代——更早的已被后续迭代事实性取代                                                                                                     |
| `GLOBAL_MEMORY_CAP` K\_g   | 8      | 全局决策注入面远小于段级；Re-grounding 携带物增量控制在 \~1KB 级                                                                                       |
| `INSTANCE_MAX_STEPS`       | 32     | = `DEFAULT_MAX_TOOL_STEPS`（既有默认，S1–S10 实测远低于此）                                                                                   |
| `WALL_CLOCK_S`             | 600    | F4 只读审查 38s；设计 + 读 + 提交 3–5 倍余量；视觉生成类工具不在此实例内                                                                                    |
| `MAX_PROPOSAL_ITEMS`       | 24     | 压测高潮段 7 镜；24 = 超出单段设计-at-once 合理规模的上界信号                                                                                          |
| `MIN_READ_EVIDENCE`        | 1 次段级读 | zero-read gate 的提案域强化版；不要求更高（S1 场景一次 `shot.list(scene_id)` 即满足，空段无镜可 `shot.read`）                                                |

> rev3（2026-09-04）：`WALL_CLOCK_S` 不再作为实例硬停（取消传递）；`max_steps` 语义改为 renewable window（默认 1024，窗口用满注入 self-check nudge 续期，次数不限）。director 的 `_WallClockExceeded` 机制保留（默认 None = 行为不变）。

***

## 12. 已知缺陷与接受理由（Part 2 增量；Part 1 §12 继续有效）

| #    | 缺陷                                                             | 接受理由                                                                        | 缓解                                                                 |
| ---- | -------------------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| P2-1 | **`user_explicit`** **信任 MD 的语义分类**（引用原话可被编造）                  | 确定性正则识别委派意图的误伤/漏报都不可控；硬上限 + 全落盘审计把最坏情形限为"多花几次 delegation"                   | origin 全落盘；遥测异常率可观测                                                |
| P2-2 | **chain:bool 无法表达"接力但隔位"**——若未来放宽接力邻接约束（如允许隔 1 镜接力），schema 需升级 | 当前 canonical 语义（`update_shot_relay` 相邻校验）下该表达不存在合法需求；提前支持 = 为不存在的状态设计       | schema 版本字段预留；升级 = `chain` 扩展为 `relay_offset`， DiffEngine 派生规则局部变更 |
| P2-3 | **悬空接力被 commit 静默规范化**（§1.3）——Scene Agent 未注意时"顺手修复"           | move 造成的悬空接力本身是既有系统的灰区状态；端态强制显式决定优于维持灰区                                     | 状态头显式标注 + receipt `relay_normalized` + 任务书模板提示                     |
| P2-4 | **partial 的语义联动仅结构面封死**，约束/边界面的联动靠 MD 注视（§9.4）                 | 语义联动不可确定性判定；引入 LLM 判定违反 D1                                                  | 回执重印有效端态的 constraint\_responses；事后 Clean Review 兜底                 |
| P2-5 | **rebase = 重新 delegation 的成本可能高于 MD 手工**（小改动场景）                | 协议不强制 rebase 路径——MD 手工修复是内置回退（它有唯一写权限），决策记录标 `manual` 即可                    | delta brief 机制让 re-delegation 复用原意图                                |
| P2-6 | **basis 防伪需要 submit executor 访问实例 trace**（实现耦合点）               | 同进程内可得；跨进程部署时需随 proposal 附 trace 摘要                                         | 实现注记（Part 3）                                                       |
| P2-7 | **单进程判死假设**（§9.3）：多进程/多 worker 部署下"重启即无存活实例"不成立                | 当前系统为单后端进程 + per-project 锁（既有架构事实）                                          | 部署形态变更时重审（心跳/租约升级路径明确）                                             |
| P2-8 | **V1 delegation 串行执行**，多段 redesign 不真并发                        | 每实例是分钟级 LLM 会话，串行 = 简单且可观测；并发执行的正确性基础（per-scene basis 隔离 + 串行 commit）本协议已铺好 | 后续版本放开执行并发不需协议变更                                                   |
| P2-9 | **非提案类全局决策（MD 会话中的即兴判断）仍随 Re-grounding 丢失**                    | 自由格式记忆工具会复制 F2 机制（§8.1 论证）；锚定式入库只覆盖 delegation 决策                           | 已知边界；E3 修复的是"有工件的决策"，无工件的判断接受会话态                                   |

***

## 13. 实现映射（给 Part 3 的定位，本轮不实现）

| 模块                                 | 位置建议                                  | 依赖的既有物                                                            |
| ---------------------------------- | ------------------------------------- | ----------------------------------------------------------------- |
| `scene_slice_fingerprint` 等派生指纹    | `src/agents/subagent/fingerprints.py` | `_shot_token` / `_scene_token` 同码形（E5）                            |
| DiffEngine                         | `src/agents/subagent/diff_engine.py`  | `TOOL_EXECUTORS` 直接调（spike 模式）；`update_shot_relay`（0f106c6）       |
| Registry / memory / proposal store | `src/agents/subagent/registry.py`     | `atomic_write_json`；work\_memory 集成                               |
| `scene.delegate` / `proposal.*` 工具 | director.py 工具表（条件挂载）                 | `_run_tool_loop` 的 `tools_whitelist` / 长任务通道 / `create_ephemeral` |
| 全局记忆 + reground 扩展                 | `session_governance.py`（条件段）          | `reground_session` seed 追加（§8.2）                                  |
| 测试基线                               | `tests/test_subagent_*.py`            | spike 13 场景 = 第一批用例的种子（S4b/S9b 在 chain:bool 下转为 schema 不可表达测试）    |

**给 Part 3 的三条新硬约束**（继承 Part 1 三条 + 本轮新增）：

1. 一切 submit/commit 校验错误必须携带机器可读错误码 + 具体修复指引（bounce
   自愈的前提）；
2. commit 的 receipts 必须区分"计划 op"与"已执行 op"（诚实部分报告）；
3. 记忆/registry/proposal store 的每次写入必须原子且自带 schema version（未来
   迁移不靠猜测）。

