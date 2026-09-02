# Director Sub-Agent Architecture V1 · Part 1：本体论与知识所有权

> 分支 `design/subagent-v1`（自 `feat/stress-runtime-hardening` 切出）。
> 事实依据：`_stress/STRESS_REPORT.md` §6/§8/§12/§14（实验证据）；`src/agents/director.py` /
> `sessions.py` / `session_governance.py`（代码现状）。
> 参考口径：`docs/agents/session_governance_design.md`（其结论视为"当前假设"，本文对其
> 中 §14 的架构判断做了有边界的修正，见 §0.2）。
> 本轮范围：**只做本体论与知识所有权设计**。不写生产代码、不改 Runtime、不实现
> delegation、不做字段级 contract schema、不实现 diff engine、不修改既有 Session
> Governance。数值阈值（spawn 信号 N/M 等）一律留给 Part 2+ 调参，本文只定义概念与
> 可观测信号。

## 0. 证据基线与本文立场

### 0.1 三条已验证事实（本文一切论断的唯一依据）

| #   | 事实                                                                                                                                    | 来源            | 对本设计的含义                                                               |
| --- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------- | --------------------------------------------------------------------- |
| F-1 | 55 镜一次性生成零退化；退化来自**多轮结构修改后的 session staleness、跨轮修改完备性下降、核验幻觉**                                                                        | §4.2 / §12    | **不得以 Shot 总规模作为 Sub-Agent 的理由**。Sub-Agent 针对的是"多轮局部结构工作"这个退化域，不是规模   |
| F-2 | scoped、隔离、重读 canonical、明确权限边界的工作实例有效（F4：同一定义 + 干净会话 ≈ 高质量审查）                                                                          | §8 案例 3 / §14 | Scene Agent = 把这个已被证明的模式从"只读审查"推广到"读 + 产出 proposal"。**不发明新 agent 类型** |
| F-3 | V1 约束保持：Planning 可并行；canonical commit 由 Main Director 串行；Scene Agent 无 canonical 写权限；不重新设计 OCC；不允许多 Agent 竞争 Shot canonical ownership | 任务约束          | 一切权力划分以此为硬边界                                                          |

### 0.2 与 STRESS §14 的关系（必须正面回答，不得回避）

§14 明确"不推荐 Scene Director Sub-Agent：无证据……按场景拆分只会增加协调成本，
解决不存在的问题"。本文的立场：

1. **§14 在其自己的范围内继续成立**：按场景拆分**初生成**、以**规模**为由拆分——
   本文同样反对。C1 模式（一次性全片生成）在 V1 保持 Main Director 独占，不
   delegate。本设计不推翻该结论，只收窄它的适用域。
2. **§14 未覆盖的域**：四项 Runtime 增强（Clean Review / Re-grounding / Zero-read
   Gate / Compaction 遥测）治理的是**主会话的症状**（staleness 计数、审查、重置），
   但**结构修改工作本身仍发生在那个会退化的大会话里**。E1→E2b 的修改完备性劣化
   （制造重复 → 去重不彻底）没有被任何一项增强解决——因为问题不是"记错了"，而是
   "带着被污染的工作记忆干活"。本设计把这类工作移进一次性的、scoped 的、从
   canonical 重新接地的工作实例。
3. **诚实标注**：30+ 轮的长程退化域**尚未被实验覆盖**（实测只到第 10 轮）。本设计
   中针对该域的论断属于"有依据的前瞻"而非"已验证"——依据是 F-1 的退化机制外推 +
   F-2 的模式有效性。哪些部件承担这个未验证风险，见 §12 已知缺陷清单。

### 0.3 一句话本体

> **V1 Sub-Agent 架构 = 把 Clean Review 已证明的"calibrated scoped instance"模式，
> 从只读审查推广到"读 + 提案"，并把局部创作决策理由从注定被周期性截肢的会话记忆
> 中迁移到带前提指纹的持久决策记忆里。**

***

## 1. 八问 → 章节映射

| 问题                                                         | 章节              |
| ---------------------------------------------------------- | --------------- |
| Q1 为什么 Scene-local 而非 professional-function                | §2              |
| Q2 三实体是什么                                                  | §3              |
| Q3 各自 authority（含 spawn 决策权）                               | §4（spawn 信号 §5） |
| Q4 哪些知识 global / local                                     | §6              |
| Q5 persistent identity / working context / decision memory | §7              |
| Q6 Scene 生命周期状态机（四种变异）                                     | §8              |
| Q7 Scene 内 / Scene 间 continuity ownership                  | §6.3 / §9       |
| Q8 为 delegation contract / proposal protocol 留出的接口         | §13             |

***

## 2. Q1：为什么 Scene-local，而非 professional-function（公平对照）

### 2.1 对照组的最强形态（不立稻草人）

专业职能型 Sub-Agent = 按电影工业职能横切全片：节奏/剪辑 Agent（全片节奏曲线）、
对白风格 Agent（全片台词统一）、空间调度 Agent（全片场面调度）、连续性监督 Agent
（script supervisor，全片连续性）。这是工业现实的映射（剧组按职能分工，不按场景
分工），是本对照组的**主场论证**。

### 2.2 五个维度的公平比较

| 维度                                 | Professional-function                                                                                                                    | Scene-local                                                                                                          | 判定                                           |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **对应哪条已验证失败**                      | 连续性监督职能 ↔ F4/Clean Review（**已被实现且证明不需要持久身份**）；节奏/对白/风格职能 ↔ **无任何实测失败证据**（C1 节奏分布被评为教科书级）                                                 | 多轮局部结构修改的完备性劣化（E1→E2b）、局部细节churn导致的主会话 staleness（F2 机制）                                                              | **Scene-local 胜**：它切在证据所在的地方                 |
| **canonical 写权限冲突（F-3 硬约束）**       | 职能横切 → 任一职能 agent 的产出覆盖所有 scene 的 shots → 要么多 agent 写同一 scene（违反"不竞争 ownership"），要么全员 propose-only                                       | 天然无冲突：一个 scene 一个提案者，canonical 写权限唯一归 Main Director                                                                  | **Scene-local 胜**                            |
| **provoke-only 模式下的 reconcile 负载** | 一个"全片对白统一"proposal 覆盖 15 段 → 单个巨型 proposal、basis 只能是全项目指纹、任一段被 SW 改写则整体失效、Main Director 一次 reconcile 全片                                  | proposal 以 scene 为单位、basis 是 per-scene 派生指纹、局部失效局部隔离                                                                 | **Scene-local 胜**                            |
| **知识归属（关键论证）**                     | 职能知识（节奏曲线、角色弧线、视觉母题）是**全片尺度**的知识——它已经有一个 owner：Main Director。职能 agent 要掌握它就必须复制 Main Director 的全局视图（空心化 Main Director，或制造"节奏到底归谁"的双头所有权） | Scene 内镜头结构、局部调度、局部决策理由是**局部尺度**的知识——它现在**无家可归**：只能活在主会话聊天历史里，而 Re-grounding 的携带物白名单（状态头 + brief + 最近意图）注定每次重置都把它截肢掉 | **Scene-local 胜**：给无家的知识一个家，而不是给已有归属的知识建第二个家 |
| **自身 staleness 特性**                | 职能 agent 的工作上下文天然横跨全片 → 它自己长成"第二个主会话"，**复制** F2 风险而非解决                                                                                   | 工作上下文以 scene 为界，天生有界、可丢弃、可再生                                                                                         | **Scene-local 胜**                            |

### 2.3 职能型工作的去处（不否认其价值）

职能型不是失败方案，它的两个自然归宿在 V1 都**已有归属**：

- **审查职能**（连续性监督）→ Clean Review（已实现，无身份一次性实例，F4 证据）。

- **全局决策职能**（节奏曲线、对白风格决策）→ Main Director 全局层（它本来就永久
  掌握这些知识，见 §6）。

### 2.4 诚实结论

Scene-local 在**证据所在的战场**（局部结构工作的会话内退化）胜出，且是唯一不与
F-3 硬约束冲突的切分。但纯跨段职能任务（"全片对白风格统一"）**是 Scene-local 的
结构性边界而非胜利**——详见攻击场景 1（§11.1）的正面承认。职能型执行器（如
"风格清扫"执行 agent）留作未来扩展，V1 不建。

***

## 3. Q2：三个实体分别是什么

三者共享**同一 Director agent 定义**（同一模型、同一工具集骨架、同一 prompt 基座）
——这是 F4 的直接推论：有效的差异变量是**会话状态与权限边界**，不是 agent 定义。
不发明新的 agent 类型。

| 实体                | 本体                                                                                                                                                                           | 生命周期                                     | 权限边界                                                               |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------ |
| **Main Director** | 现有 director agent 定义 + **长存主会话**（`get_or_create_conversation` 语义不变）                                                                                                          | 项目级长存（受既有 Re-grounding 治理）               | shot 域 canonical **唯一写权限**；delegation 唯一发起者；reconcile/commit 唯一执行者 |
| **Scene Agent**   | **持久身份记录**（registry 中以 scene\_id 为键的一条记录，持有 decision memory 与元数据）+ **一次性工作实例**（per-delegation 的 calibrated session，`create_ephemeral` 模式）的**复合体**。不是常驻进程——只在 delegation 期间存在 | 身份：随 scene 生命周期（§8）；工作实例：随 delegation 生灭 | 无 canonical 写权限；对本 scene 产出 proposal；border 只能建议不能决定               |
| **Clean Review**  | 现有 `run_clean_review` 的**无身份一次性只读审查实例**（scope\_type="review"，不复用、用完即弃）                                                                                                       | 单次触发即弃（现状不变）                             | 全 canonical 只读；产出问题清单；零修改权                                         |

三者关系：Main Director 是唯一的"作家"（commit）；Scene Agent 是"局部设计师"
（propose）；Clean Review 是"全片质检员"（verify）。三者没有对等通信——Scene Agent
与 Clean Review 互不可见，一切信息经由 Main Director 或 canonical 中转。

***

## 4. Q3：Authority 划分（含 spawn 决策权）

### 4.1 权限表

| 权力                 | Main Director | Scene Agent                               | Clean Review         | Runtime（确定性代码，非 agent） | 用户           |
| ------------------ | ------------- | ----------------------------------------- | -------------------- | ---------------------- | ------------ |
| shot 域 canonical 写 | **唯一**        | 无                                         | 无                    | 无                      | —            |
| story/character 域  | 只读（现状）        | 只读（本 scene 相关）                            | 只读                   | 无                      | —            |
| proposal 产出        | —（它是消费者）      | **本 scene 的 desired end state**           | 无（产出问题清单，非 proposal） | 结构合法性校验                | —            |
| border 决定          | **唯一裁决**      | 建议（border\_change\_request）               | 无                    | 无                      | 争议升级时的终审     |
| 全片连续性审查            | 发起/消费         | 无                                         | **执行**               | 触发计数（现状）               | 主动触发         |
| spawn 决策           | **提议权**       | 无                                         | 无                    | **资格闸门（veto）**         | **显式要求（最高）** |
| decision memory 写入 | 无             | 经 proposal 结构化携带 rationale，由 Runtime 收集入库 | 无                    | 收集/隔离/注入（确定性）          | —            |
| scene 生命周期检测       | 无（不需要知道时机）    | 无（不在运行时无从检测）                              | 无                    | **唯一检测者**（§8）          | —            |

### 4.2 spawn 决策权：三层结构（【必须回答】）

**结论：用户显式要求 > Main Director 提议 > Runtime 确定性资格闸门。**

- **Main Director 持有 spawn 的发起权**：current-task complexity 是语义信号——
  "把 s05 全部重做"与"改 s05 第 3 镜对白"在计数器上可能完全相同，只有理解任务
  语义的 Main Director 能区分。Runtime 规则永远做不到这一点。

- **Runtime 持有否决权（资格闸门）**：delegation 请求必须携带信号依据（历史压力
  计数，或任务复杂度声明），Runtime 校验"依据非空 + 预算未超 + 目标 scene 存活"，
  不满足则拒绝并回退主会话直接执行。为什么不能信任 Director 自主：压力测试证据
  表明模型在负载下会走低 effort 路径（F2 零工具幻觉、40 次并发写偷懒模式）——
  "把活儿推给子 agent 省自己事"是同族风险，确定性闸门让无证据的廉价路径不可达。

- **用户显式要求是最高权威**（"这段找个分镜专家重做"）：跳过资格闸门中的信号
  依据检查，但仍受预算与存活检查保护（防无限 spawn）。用户不应被要求理解会话
  治理机制，所以它只在"用户主动说"时生效，不作为常规路径。

### 4.3 与四项既有 Runtime 增强的组合关系

- **Zero-read Gate**：对 Scene Agent 工作实例**照常生效**——提案者也必须真实读取
  canonical（F2 教训适用于任何会话，与身份无关）。

- **Clean Review**：机制完全不变；delegation 的 commit 计入既有结构写计数器
  （见 §10.3），大 redesign 自然触发事后全片审查。

- **Re-grounding**：Main Director 的既有治理不变；Scene Agent 工作实例天然"每次
  delegation 全新"，长 delegation 内部同样可用 `reground_session` 原语。

- **Compaction 遥测**：Scene Agent 会话独立成行（既有 `log_context_telemetry`
  支持任意 session id，无改动）。

***

## 5. 设计对象 A：Spawn 信号

### 5.1 两个独立来源 + 一个辅助（概念定义，不拍数值）

**来源一：historical local pressure（该 scene 的历史压力）**——可从既有 receipts +
work memory 确定性派生（回执携带 scene\_id）：

- 该 scene 自上次 delegation / 主会话 Re-grounding 以来的**结构写累计**（create /
  create\_many / delete / move，口径与既有 `STRUCTURAL_WRITE_OPS` 一致，按 scene 聚合）；

- 指向该 scene 的 **redesign 轮数**（用户意图分类为"重做/重设计/大改 s0X"的轮次）；

- 该 scene 的**局部 staleness 浓度代理**（该 scene 结构写占全片结构写的比例）。

**来源二：current-task complexity（本轮任务复杂度）**——语义信号，由 Main Director
声明、Runtime 粗校验：

- 本轮任务 scope 声明为**单 scene 的大规模局部 redesign / expansion / restructure**，
  即使该 scene 从未被修改过（零历史压力）也允许直接 delegate；

- 任务被声明为 **continuity-sensitive planning**（触及跨段接力、道具弧线局部段）；

- 探索型任务（需要多方案比较的局部设计）。

**辅助信号（最多辅助，永不充分）**：scene 的 shot 数量——只用于成本估计与预算，
**不得**单独构成 spawn 依据（F-1：规模不是理由）。

### 5.2 反模式（明确不得触发）

- 单点内容修改（"改第 3 镜对白"）——无论历史计数多高，主会话 + 既有闸门胜任；

- 初次全片生成（C1 模式）——永不 delegate（§0.2）；

- 纯探讨/审查轮——那是 Clean Review 的领地；

- 纯跨段职能任务（"全片节奏调整"）——全局层领地（§11.1）。

***

## 6. Q4：知识分工（设计对象 C）

### 6.1 三层知识表

| 知识                                  | 归属                                             | 载体                                       |
| ----------------------------------- | ---------------------------------------------- | ---------------------------------------- |
| 全片节奏曲线                              | **Main Director 永久掌握**                         | 主会话（全局级决策记录）+ canonical-derived 状态头      |
| 角色跨 Scene 弧线                        | **Main Director 永久掌握**                         | 同上 + character canonical 只读              |
| 视觉母题分布                              | **Main Director 永久掌握**                         | 同上                                       |
| 全局导演意图 / 用户 brief                   | **Main Director 永久掌握**（注入所有实例）                 | `creative_brief.json`（既有）                |
| Scene 间 continuity、border ownership | **Main Director 最终归属**                         | border lock / snapshot（§9）+ reconcile 决策 |
| delegation 编排知识（谁在做什么、proposal 状态）  | **Main Director**                              | 结构化摘要 + proposal\_id（§10）                |
| Scene 内镜头结构（顺序/景别/机位/运镜）            | **Scene Agent 深耕**                             | proposal + decision memory               |
| 局部动作连续性 / 局部空间调度 / Scene 内节奏        | **Scene Agent 深耕**                             | 同上                                       |
| 局部导演决策及理由                           | **Scene Agent 深耕**（**本项目唯一被持久化的创作 rationale**） | decision memory（带前提指纹，§7.3）              |
| Scene 内接力图（relay 边）                 | **Scene Agent**（段内）/ **border 域**（跨段）          | proposal / border\_change\_request       |
| 故事文本、角色名单、canonical 现状              | **共享，以 canonical 为准**（无人拥有"自己的版本"）             | 读工具                                      |

### 6.2 禁止越界（双向）

- Scene Agent **不得**将邻接关系、跨段事实作为持久 decision memory（§7.4 易失级）；

- Main Director 主会话**不得**累积镜级细节 churn（§10.1——这不仅是经济问题，正是
  F2 的生成机制）。

### 6.3 连续性 ownership 原则（Q7）

> **Scene 内连续性默认归 Scene Agent；Scene 间连续性最终归 Main Director。**

- **Scene 内**（段内动作承接、空间、时间、道具段内状态）：Scene Agent 在 proposal
  内自证（desired end state 本身就是段内连续性的声明），加上确定性结构校验
  （接力闭包等，§12 接口）。

- **Scene 间**（handoff、跨段时序、跨段道具/角色状态传递）：Main Director——事前
  用 locked border 约束输入，commit 时用 reconcile 裁决；Clean Review 事后全片核验。

- **灰区（跨段接力边）**：归 border 域——Scene Agent 可读（snapshot）、可申请改
  （border\_change\_request）、不可自行决定。

### 6.4 Clean Review 与 Scene Agent 并存（【必须回答】）

**全局连续性审查权归 Clean Review**——它是唯一具有全 canonical 只读视野的审查
实体，此权不因 Scene Agent 的存在而转移（Scene Agent 的视野被刻意限制在单 scene +
border snapshot，物理上做不了全局审查）。

**Scene Agent 的 proposal 是否/何时过 Clean Review**：

1. **事后自动**：Main Director commit 一个 proposal = 一批结构写操作 → 计入既有
   `structural_since_review` 计数器 → 大 redesign（≥8 阈值量级）自然触发全片审查。
   proposal 的全局影响（对邻段的隐性破坏）在这一层被核验。
2. **用户主动**：现状触发 B 不变。
3. **V1 不设事前强制审查门**：commit 前的质量关口是 Main Director 的 reconcile
   （它对照 global view + border 事实），再叠一层事前审查会双倍延迟，且与
   "reconcile 责任在 Main Director"的权力划分矛盾。

即：**Clean Review 是事后核验层，不是事前审批层**；proposal 的质量链条 =
Scene Agent 自证（段内）→ Main Director reconcile（跨段）→ Clean Review（全片，事后）。

***

## 7. Q5：Persistent Identity / Working Context / Decision Memory

### 7.1 三者区分

| <br />                  | 是什么                                                                                                               | 持久性                                               | 载体                         | 失效方式                      |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- | -------------------------- | ------------------------- |
| **persistent identity** | "我是谁"：绑定 stable scene\_id 的注册表记录（生命周期状态 + 元数据）                                                                    | 随 scene 生命周期（§8）                                  | registry（新增概念，Part 2 落盘形态） | scene 消亡 → retire/archive |
| **working context**     | "我现在看着什么"：一次 delegation 的 LLM 会话（校准注入：scene-scoped 状态头 + brief + delegation 任务书 + 有效 decision memory + border 信息） | **一次性**——每次 delegation 全新，**永不跨 delegation 携带对话** | `create_ephemeral` 模式会话    | 用完即弃；长任务内可 re-ground      |
| **decision memory**     | "我得出过什么结论、为什么"：带前提指纹的**策展过的**决策条目                                                                                 | 跨 delegation 持久（但每条可独立失效）                         | registry 内的 memory 存储      | 前提指纹不匹配 → 隔离（quarantine）  |

### 7.2 不把 F2 复制到局部 Agent（结构性保证，非提示词保证）

主会话 F2 的机制 = **无前提追踪的持久对话记忆**冒充读取。Scene Agent 的对应防线：

1. working context **永不跨 delegation 持久**——会话记忆的积累面被 delegation 边界
   硬性切断；
2. 唯一跨边界持久物（decision memory）**必须带可检测前提**（指纹），无前提追踪的
   持久物一律不允许存在；
3. memory 注入时明示"advisory prior decisions（含出处与前提指纹），不是证据，事实
   必须用读工具重新确认"；
4. Zero-read Gate 对 Scene Agent 实例照常上膛（§4.3）。

### 7.3 decision memory 条目的可失效性（【必须回答】）

每条决策条目必须携带（概念结构，非字段 schema）：

- **decision + rationale**：结论与理由（例如"保留 s05\_c04 的特写，因为它承担红围巾
  撕裂的情绪锚点"）；

- **provenance（来源轮次）**：产生于哪次 delegation（含一次性工作实例 id）、第几
  turn——使引用链可审计；

- **basis fingerprint（生成时的 scene 内容指纹）**：决策做出时刻该 scene 的
  per-scene 派生指纹（§7.5）——**这是前提可被检测过期的核心**：注入时与当前指纹
  比对，不匹配即隔离，宁错杀不放过（over-invalidation 是安全的：代价只是重新推导；
  under-invalidation 才危险：那是把 F2 装进记忆里）；

- **dependency class（依赖类别）**：`durable`（只依赖本 scene 内容 + 用户 brief）/
  `volatile`（依赖邻接、border、跨段事实）——类别在收集时由 Runtime 依内容判定。

### 7.4 两级记忆：durable 与 volatile

- **durable 条目**：进入持久 decision memory，带指纹跨 delegation 存活；

- **volatile 条目（邻接/border/跨段依赖的一切）**：**根本不持久化**——每次
  delegation 时从 canonical 重新计算（任务指令："邻接关系不得作为永久 decision
  memory 保存"的直接落实）。D 节的四种 scene 变异中，move/reorder 只影响 volatile
  层（零记忆失效），内容改写只影响 durable 层（指纹比对）——依赖类别切分让失效
  路径变成两个互不干扰的确定性检查。

### 7.5 per-scene 派生指纹（为 F 节与 §7.3 服务）

现有 OCC 指纹是全项目级（`_shots_token` / `_shots_content_token`）。本设计派生
**scene slice fingerprint**：对该 scene 自身内容字段（summary/title/location/
time\_of\_day/characters，即 `story.update` 白名单域）+ 其有序 shots（id 顺序 + 可写
内容字段）+ 段内接力边，做确定性哈希——与既有 token 同码形，**读时现算、不新增
canonical schema**。它同时服务两个用途：decision memory 的 basis fingerprint 与
proposal 的 base fingerprint（§12.3）。

***

## 8. Q6：Scene 生命周期状态机（设计对象 D）

### 8.0 前置事实：scene 级变异全部源自 SW 域

scene 的 create/update/move/delete 是 story 域写操作（`story.create` / `story.update`
/ `story.move` / `story.delete`），**权限在 Script Writer**；Director 对 story 域
只读。因此 Scene Agent 生命周期完全由"SW 侧 scene 变异 + MD 侧 delegation 决策"
驱动，且 SW **不需要知道** Scene Agent 存在（知识边界：SW 的本体论里没有子代理，
检测责任不能落在它身上）。

### 8.1 状态机

```
                    spawn（首次 delegation 通过资格闸门，惰性创建）
 (nonexistent) ────────────────────────────────▶ active ──────────▶ retired(archived)
                                                   │  ▲                 ▲
                                                   │  └─ resume ─────────┘（不可逆）
              resume = 同 scene_id 再次 delegation：身份与 decision memory 保留，
                        working context 全新 + 注入前执行失效清扫
```

### 8.2 四种变异 + SW 改写（【必须回答】逐条）

| 变异                              | 发起者                                     | 对 Scene Agent 的影响                                                                                                                                                                                         | 谁检测 / 何时检测                                                                                                                                                                                                                                                                       |
| ------------------------------- | --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **创建后 spawn**                   | SW 创建 scene（新 scene\_id）                | 惰性：无 agent，直到首次 delegation 通过闸门才 spawn。**不预建**                                                                                                                                                            | Runtime 在 delegation 派发时创建（惰性）                                                                                                                                                                                                                                                   |
| **resume**                      | Main Director（再次 delegation）            | 身份保留；working context 全新；注入前执行：① 指纹失效清扫（§7.3）② 邻接重算（volatile 层，§7.4）③ liveness 检查                                                                                                                          | Runtime，delegation 派发时（同步、确定性）                                                                                                                                                                                                                                                   |
| **delete → retire/archive**     | **SW**（`story.delete` 级联删镜头，跨 agent 事件） | 身份 retire；decision memory 与审计历史**归档不删除**（scene\_id 按 canonical 分配规则不复用，无身份碰撞风险）                                                                                                                           | **Runtime（唯一检测者）**，两个时点：① delegation 派发时 liveness 检查（scene\_id 不在 canonical → 拒绝派发 + 顺手 retire）② Main Director turn 收尾 sweep（registry 与 canonical scene 列表的集合差，搭载既有治理后处理，零 LLM 成本）；**delegation 在飞行中 scene 被删** → commit 时 liveness 复查失败 → 拒绝 commit + retire + 通知 Main Director |
| **move / reorder**              | SW（`story.move`）                        | **身份零变化**（绑定 scene\_id 非位置）；durable 记忆**零失效**（move 不改 scene 内容字段与 shots → scene slice fingerprint 不变——这正是指纹把邻接排除在外的目的）；volatile 层（邻接）定义上失效，下次 delegation 重算；**邻段的 border\_snapshot** 各自在下次 delegation 时刷新 | Runtime，下次 delegation 派发时（惰性）——无需即时事件                                                                                                                                                                                                                                            |
| **内容被 SW 改写（story.update）**（新增） | SW                                      | durable decision memory 的前提**批量失效**：scene 内容字段变化 → scene slice fingerprint 变化 → 注入时所有 basis fingerprint 不匹配的条目被隔离（保留在盘供审计，不注入）；若全部隔离 → 等效记忆重置但身份保留（F4 模式：fresh + canonical 即高质量）；注入头明示"因剧情改写已隔离 N 条历史决策"  | Runtime，下次 delegation 派发时（惰性、确定性指纹比对，无守护进程、无 LLM）                                                                                                                                                                                                                                |

### 8.3 检测时机的统一原则

**全部惰性（lazy）+ 全部确定性**：不设任何后台守护；一切检测发生在三个已有的同步
节点——delegation 派发时（liveness + 指纹清扫 + 邻接重算）、commit 时（liveness +
basis 复查）、MD turn 收尾时（registry sweep）。这与既有治理原语（计数器在 turn 收尾
结算）的节奏完全一致。

***

## 9. 设计对象 E：Border Ownership

### 9.1 delegation 携带的两种边界信息（严格区分）

| <br /> | locked\_border\_constraints                   | border\_snapshot                                    |
| ------ | --------------------------------------------- | --------------------------------------------------- |
| 性质     | **约束**（本次任务中不可违反的出入口条件）                       | **事实**（canonical 当前相邻段交接状态）                         |
| 来源     | Main Director 的全局连续性知识（跨段时序、道具/角色状态传递、节奏衔接意图） | delegation 时刻从 canonical 确定性派生（邻段出入口镜头状态摘要 + 跨段接力边） |
| 可否违反   | 不可——违反即 proposal 校验失败（确定性检查）                  | 可以提出修改——但只能经 border\_change\_request                |
| 生命周期   | 单次 delegation 内锁定                             | 单次 delegation 内快照（过期由 proposal\_basis 在 commit 时兜底） |

### 9.2 border\_change\_request 的语义

Scene Agent 认为必须修改边界时，在 proposal 中返回 **explicit border\_change\_request**
（说明：请求改什么、为什么、对自己段内设计的影响）。它**不是协商通道**——V1 无
agent 间通信，请求的接收者是 Main Director，由它以全局视野 reconcile（接受 / 拒绝 /
改锁）。接受后的新边界成为**下一轮**双方 delegation 的 locked constraints（§11.5
防循环的关键）。

***

## 10. 设计对象 G：Main Director 上下文经济

### 10.1 首要收益是卫生，其次才是经济

主会话不进镜级细节 churn，**首先是 staleness 预防**：F2 的机制就是局部细节在会话
记忆里发酵成"记忆冒充读取"。上下文经济（省 token）只是这个卫生原则的副产品——
压测证明主会话从未逼近窗口上限（11k chars），所以"省上下文"单独不构成理由。

### 10.2 proposal 的流转规则

- **全文持久化**于 proposal store（Part 2 落盘形态），不进主会话历史；

- 主会话默认只接收**结构化摘要 + proposal\_id**：结构变化量（±N 镜）、关键结构移动、
  border\_change\_requests、proposal\_basis（base fingerprint）、自证读取量；

- **commit / reconcile 时按需读取全文**（一次性工具读取，进当轮上下文、不进长期
  历史的重放负载）；

- reconcile 结论以结构化摘要形式留在主会话（有界）。

### 10.3 计入既有治理

- delegation 的 **commit = 结构写** → 计入 `structural_since_review` /
  `structural_since_reground` 两个既有计数器（口径不变：成功且非幂等的
  create/delete/move）；

- delegation / reconcile 事件记入 `meta.events`；

- Scene Agent 会话独立落 Compaction 遥测行。

### 10.4 反空心化不变量

> **Main Director 做全局决策所需的任何事实，要么在 canonical 里，要么可从 canonical
> 派生；局部细节只存在于 proposal 与 decision memory 中，永不以"主会话记忆"形式
> 存在。**

由此 Main Director 的 global director view 永远是 canonical-derived（既有
`build_state_header` 的输出即其形态）、随时可重建（Re-grounding 已证明）、不依赖
任何 Scene Agent 的存在（稀疏模型安全性，§11.4）。

***

## 11. 必测攻击场景逐条分析

### 11.1 纯跨段职能任务（专业职能型的主场）

**场景**："全片对白风格统一" / "全片节奏曲线调整"。

**诚实回答：Scene-local 在这里不赢，这是它的结构性边界。** 任务的切分轴是"职能 ×
全片"，Scene-local 的切分轴是"位置 × 单段"——轴不匹配，局部实例物理上看不到全片，
做不了风格曲线决策。

V1 的处理：**全局层全权**——Main Director 做全局分析与决策（它永久掌握节奏曲线与
风格意图，§6.1）；执行若需要大规模逐段落地，各段的落地工作可按 current-task
complexity 信号分别 delegate（段内执行是 scene-local 的本职），但**分析与决策永不
delegate**；事后 Clean Review 核验全片一致性。

职能型的优势在此场景真实存在，V1 以"Main Director + Clean Review + 执行 fan-out"
吸收其八成收益而不引入持久职能 agent 的双头所有权问题。剩余两成（专职职能视角的
深度）明确放弃，记入 §12 缺陷清单。

### 11.2 30+ 轮后 decision memory 漂移与自我强化

**机制**：早期决策被注入 → 后期 proposal 引用它 → 引用固化为"先例" → 与 canonical
脱节的局部正统。这是 F2 机制的 memory 版本。

**防线（四层 + 监测）**：① 前提指纹——basis 不匹配的条目进不了注入集（陈旧前提
无法被引用）；② 注入纪律——memory 以"带出处与前提指纹的 advisory prior"形态注入，
显式声明非证据，事实必须重读（zero-read gate 上膛）；③ 注入上限——只注入最近 K 条
有效条目（K 留给 Part 2 调参），provenance 可见使"引用一条第 2 轮的旧决策"在上下文
里显老；④ 反权威——推翻旧决策零成本（memory 记录 rationale，不是法律）；漂移的
本质是盲引用，provenance 标签让引用"可见地旧"。

**监测信号**：每次 delegation 记录"新决策引用旧决策的比率"（self-reference rate）；
持续高位 → 触发记忆压缩审查（Clean-Review 式实例重推导哪些决策仍成立——V1 人工/
用户触发，不自动）。

**残余风险**：LLM 天然过度采信被注入的记忆。接受，记入 §12。

### 11.3 SW 改写某段剧情后决策前提整体失效

**路径（全确定性，零 LLM）**：`story.update(scene_id)` → scene 内容字段变化 →
scene slice fingerprint 变化 → 下次 delegation 注入时，全部 basis fingerprint 不
匹配的 durable 条目被隔离 → 注入头明示"因剧情改写已隔离 N 条" → Scene Agent 以
canonical + brief 从零推导（F4 证据：fresh + canonical 即高质量）。

**故意保守**：哪怕 SW 只改一个词，指纹也会变、记忆也会被全量隔离——宁可错杀。
over-invalidation 的代价只是重推导（安全），under-invalidation 的代价是把 F2 装进
持久层（危险）。

**边界情形**：SW 改写 summary 但 shots 未动 → shots 的设计前提仍失效（它们的前提
包含 scene 内容）——这正是把 scene 内容字段纳入指纹的目的，行为正确。

### 11.4 稀疏模型（15 段只有 4 个 Agent）下 Main Director 全局视图是否足以 reconcile

**结论：足以，因为全局视图从来不依赖 Scene Agent 存在。**

- Main Director 的 global view 是 canonical-derived（状态头形态），与有多少 agent
  无关；

- Scene Agent 不持有 canonical——它们只是提案者；reconcile 所需的全部输入 =
  canonical（含 border 事实）+ proposal + 全局视野，三者恒在；

- 稀疏覆盖的真实含义是**局部 rationale 深度不对称**：4 段有策展记忆，11 段的局部
  理由只活在主会话历史（受既有 Re-grounding 治理）。这个不对称**与压力分布同构**
  （spawn 信号即压力信号：被反复改的段才有 agent），无压力的段本来就没有值得持久
  化的决策史；

- 风险点：压力信号**迟触发**（某段已在主会话churn多轮后才首次 delegate，首代 agent
  对那些轮次失忆）。接受：主会话侧由 Re-grounding 自治，agent 侧 fresh + canonical
  重推导的质量已被 F4 证明。

### 11.5 邻接 Agent 并行产出边界冲突 + border\_change\_request 循环

**结构性事实先行**：V1 无 agent 间通信；两个邻段 agent 并行时各自对着**同一份
canonical 快照 + 各自的 locked border** 干活；产出物只是 proposal，canonical commit
是 Main Director 串行点。所以"并行冲突"的真实形态 = 两个矛盾 proposal 到达串行
commit 点。

**处理**：Main Director 以全局视野联合 reconcile（同时看到两份提案，不是先后被
蒙蔽）；矛盾不可同时满足时，以全局连续性（跨段时序、道具状态、节奏）为裁决依据。

**防循环（A 请求改 B 的边界、B 同时请求改 A）**：① 边界在 delegation 内**锁定**——
请求是返回通道不是协商通道，双方在飞行中互相看不见对方的请求（无乒乓的物理基础）；
② 被接受的边界修改在 commit 时生效，成为**下一轮**双方的新 locked constraints
（无活体拉锯）；③ **flip-flop 守卫**：同一边界连续 ≥2 次 reconcile 反复 → 升级给
用户（带双方 rationale）——这是创作分歧不是优化问题，需要人的品味；④ 用户裁决
即为终审（§4.2 的权威序）。

### 11.6 端态 proposal 与级联删除的语义冲突

**场景**：end state 要删 s06\_c03 但保留被它接力的 s06\_c04。若 diff 出裸
`delete(s06_c03)`，现有级联（`delete_service` 按 source\_shot\_id 传递闭包）会连带
删掉 c04——端态如何表达"删镜头并重接下游"？

**答案：端态是图完备的（graph-complete），接力边本身是 desired end state 的一等
公民。**

- desired end state 声明的不是"哪些镜头存在"，而是"**目标镜头集 + 目标接力图**"：
  c04 存活 ⇒ 它的目标态必须显式声明其接力来源（改锚到 c02，或 relay=null 摘除依赖）；

- **proposal 结构校验（确定性，第一道防线）**：任一存活镜头的接力来源指向非存活
  镜头 → proposal 不合格，打回 Scene Agent 修补（或标记给 Main Director）；

- **diff engine 的排序责任（第二道防线，Part 2）**：目标图可达性由操作顺序保证——
  先发 `update(c04.relay: c03→c02)`，后发 `delete(c03)`。**这正是"proposal = 端态
  而非操作序列"的根本理由**：级联是 current canonical + 当前接力边的性质，端态定义
  目标图，而确定性 diff 代码能算出安全拓扑序（detach-before-delete）——LLLM 发
  操作序列则天然容易踩级联；

- 若 Scene Agent 声明"删 c03 且 c04 存活但不再有任何接力来源"——可表达（relay=null
  是端态词汇的一部分）；不可表达的是"删 c03 并隐式级联"——隐式级联正是本设计要
  消除的歧义，端态的定义就是显式枚举存活者；

- **知识前提**：Scene Agent 必须能看到段内完整接力图——delegation 的 scene slice
  包含段内全部接力边；**跨段接力边**（若有）归 border 域（§6.3）：可读
  （snapshot）、可申请（border\_change\_request）、不可自行改。

***

## 12. 已知缺陷与接受理由

| #  | 缺陷                                                                                                 | 接受理由                                                                                          | 监测/缓解                                                         |
| -- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| 1  | **纯跨段职能任务不在 Scene-local 能力域内**（§11.1 诚实承认的边界）                                                      | 压测证据中**不存在**全局风格/节奏质量失败（C1 节奏被评为教科书级）；复杂度预算花在证据所在处                                            | Main Director + Clean Review + 执行 fan-out 吸收大部分收益；职能执行器留作未来扩展 |
| 2  | **decision memory 漂移/自我强化残余风险**（LLM 过度采信注入记忆）                                                      | 四层防线（指纹/纪律/上限/反权威）把风险压到"引用可见地旧"的程度，无法归零                                                       | self-reference rate 遥测 + 用户触发的记忆压缩审查                          |
| 3  | **30+ 轮长程域未被实验覆盖**（实测仅 10 轮）                                                                       | 设计依据是 F-1 机制外推 + F-2 模式有效性；标注为前瞻而非已验证                                                         | 上线后用 `_stress/stress_driver.py` 同法压测 30+ 轮验证                  |
| 4  | **delegation 延迟**（spawn + 冷启动实例 + 串行 reconcile）                                                    | 只在压力/复杂度信号命中时发生，默认路径（点改、初生成）零变化                                                               | 信号闸门 + 预算上限；延迟成本由 reconcile 结构化摘要控制                           |
| 5  | **稀疏覆盖的 rationale 不对称**（无 agent 的段理由只在主会话）                                                         | 不对称与压力分布同构（有 churn 的段才 spawn）；无压力段的决策史本就稀薄                                                    | 压力迟触发场景由 fresh + canonical 重推导兜底（F4 证据）                       |
| 6  | **reconcile 质量依赖 Main Director 的全局视野**——commit 时读全量 proposal 有一次性上下文成本                             | 成本有界（单 proposal 尺寸，非历史累积）；read-on-demand 不进长期历史                                               | 结构化摘要先行，全文按需                                                  |
| 7  | **border 争议最坏情况需要用户裁决**（flip-flop 守卫升级）                                                            | 连续两轮 reconcile 反复的边界分歧本质是创作品味分歧，自动化只会来回震荡                                                     | 升级时携带双方 rationale，用户裁决即终审                                     |
| 8  | **Scene Agent 视野受限可能产出局部最优、全局平庸的 proposal**                                                        | 设计使然（"段内归 agent、段间归 Director"）；全局质量由 locked constraints 输入 + reconcile + 事后 Clean Review 三层把关 | 接受为架构性质而非缺陷                                                   |
| 9  | **本设计的 proposal 语义是对 Part 2 diff engine 的前瞻承诺**——若 diff 证明不可行（表达力/排序复杂度爆炸），proposal 需降级为"带注解的操作建议" | 端态 + 图完备 + 确定性排序在 §11.6 已论证可达；降级路径存在且不推翻本体论（ownership/lifecycle/border 与 diff 无关）             | Part 2 设计时先行验证 §11.6 的排序不变式                                   |
| 10 | **scene slice fingerprint 覆盖域依赖 SW 字段白名单的稳定性**（`story.update` 白名单若扩字段，指纹须同步）                       | 指纹是读时派生，扩字段只是重算口径变化（触发一次全量隔离——安全方向）                                                           | Part 2 实现时将指纹输入口径写为单一函数并加测试                                   |

***

## 13. Q8：为 Part 2 留出的接口（接口级，不到字段 schema）

```
┌─ DelegationContract（Main Director → Scene Agent 工作实例）──────────────┐
│  scene_id · 任务书（brief）· locked_border_constraints · border_snapshot  │
│  · 校准注入包（scene-scoped 状态头 / 用户 brief / 有效 decision memory）   │
│  · proposal 要求（端态格式期望 · 预算）· delegation 实例 id                 │
├─ ProposalReturn（Scene Agent → Main Director）──────────────────────────┤
│  proposal_id · proposal_basis（scene_id + base scene slice fingerprint   │
│  + delegation 实例 id）· desired end state（有序镜头集 + 接力图 + 设计    │
│  理由）· border_change_request（可选，显式）· 自证读取量（zero-read 依据） │
├─ ReconcileDecision（Main Director 内部 → commit 路径）───────────────────┤
│  accept / rebase / reject / partial ·（basis 变化时 reconcile 而非套用，  │
│  §F 要求）· 触发 diff 派生 primitive operations                          │
├─ DiffEngine（Part 2，本设计只承诺不变式）────────────────────────────────┤
│  输入：current canonical scene slice + desired end state                  │
│  输出：primitive operations（含安全拓扑序：detach-before-delete）          │
├─ SceneAgentRegistry（确定性，无 LLM）────────────────────────────────────┤
│  spawn / resume / retire / archive / quarantine（记忆隔离）/ sweep         │
├─ GovernanceIntegration（对接点，零新概念）────────────────────────────────┤
│  commit 计入既有双计数器 · delegation/reconcile 事件入 meta.events        │
│  · Scene Agent 会话独立遥测行 · zero-read gate 对实例照常生效             │
└──────────────────────────────────────────────────────────────────────────┘
```

**三条给 Part 2 的硬不变式（本文档的承诺边界）**：

1. canonical 写权限永远唯一（Main Director 串行 commit），Scene Agent 一切产出都是
   proposal；
2. 一切跨 delegation 持久物必须携带可检测前提（指纹），否则不允许持久化；
3. 一切 scene 生命周期检测是惰性 + 确定性的，发生在 delegation 派发 / commit /
   turn 收尾三个既有同步节点，无守护进程、无 LLM、无 SW 侧感知。

***

## 附：与既有代码的对应关系（供 Part 2 实现者定位）

| 本设计概念              | 既有对应物                                                 | 复用/新增                                          |
| ------------------ | ----------------------------------------------------- | ---------------------------------------------- |
| calibrated 工作实例    | `create_ephemeral` + `build_state_header` + brief 注入  | 复用（scope\_type 换专用值）                           |
| 一次性审查实例            | `run_clean_review` / `create_review_session`          | 原样复用                                           |
| 长任务内重接地            | `reground_session`                                    | 复用                                             |
| 实例内读域闸门            | `_run_tool_loop` 的 `tools_whitelist` + zero-read gate | 复用（Scene Agent 实例 = 读白名单 + proposal 输出通道）      |
| 结构写计数（项目级）         | `GovernanceStore` 双计数器                                | 复用；per-scene 聚合是 receipts 的自然派生（回执带 scene\_id） |
| per-scene 指纹       | `_shots_token` 同码形的 scene-scoped 变体                   | 新派生值（读时现算，不动 canonical）                        |
| 级联删除语义             | `delete_service`（source\_shot\_id 传递闭包）               | 不改；diff engine 以排序规避（§11.6）                    |
| proposal 全文 / 摘要分流 | 无（新持久物，类比 `output/agents/reviews/`）                   | Part 2 新增                                      |

***

## 附：Errata（2026-09-02 架构检查后修订，代码事实核对 + diff spike 驱动）

> 检查手段：Part 1 对 session/权限/canonical/governance 的代码描述逐条核对；
> 非生产 diff spike（`spike/subagent_diff_spike.py`，13 场景）验证 §F/§11.6 承诺。
> 本体论结论（Scene-local 切分、三实体、权限划分、生命周期、border 所有权）经核对
> 全部成立，以下为修订项。

| #   | 原文位置                          | 修正                                                                                                                                                                                             |
| --- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| E1  | §11.6 / 附录（级联删除语义）           | 原文"先发 `update(c04.relay: c03→c02)`，后发 `delete(c03)`"当时**不可执行**——`source_shot_id` 不在 Director 层 `SHOT_UPDATABLE_FIELDS` 与中性层 `_SHOT_WRITABLE_FIELDS` 任何一层白名单（spike S6/S10 实测 BLOCKER：detach-before-delete 与新镜头间接力挂接均不可达，唯一变通 = 删链重建，丢资产 + id 漂移）。**已修复**（commit 见 git log）：中性层新增 `update_shot_relay`（校验：存在/同段/无环/**相邻**——与 insert 的接力约束同口径；`None`=解除并清 `source_version`，`opening_source` 保留，生成侧双条件分支使其无害）；`shot.update`/`shot.update_many` 显式接线（save 前应用，失败整单拒绝）；`source_shot_id` 进 `SHOT_UPDATABLE_FIELDS` 使内容指纹覆盖接力变更（OCC 保护）；创建时显式拒绝（新镜 id 未分配，**先建后锚**）。diff 拓扑序修正为：`delete → create(不带接力) → reorder → update(含改锚)`。附录"级联删除语义不改，diff 以排序规避"作废——排序规避的前提（detach 先行）当时不存在，现在是显式原语。 |
| E2  | §4.1 / §7.3（decision memory 写入） | 补规则：**入库时点 = Main Director reconcile accept 且 commit 成功之后**。rejected / stale proposal 的 rationale 只留 proposal store 供审计，不入库；rebase 后以实际 commit 的版本为准。防止被否决的设计理由污染后续 delegation 的先验。                                                                     |
| E3  | §6.1 / §10.4（全局知识载体）          | 原文自相矛盾：全局知识载体写"主会话（全局级决策记录）"，但主会话是 Re-grounding 的截肢对象；§10.4 不变量只覆盖"事实"（canonical 可派生），**决策理由不是事实、不可派生**——Main Director 的全局创作决策 rationale 在 Re-grounding 后确实会丢失（work_memory 只记动作事实，brief 只覆盖初始约束）。**Part 2 需补**：Main Director 级 global decision memory（策展、带 provenance，与 scene 级同构），并纳入 Re-grounding 携带物白名单。                        |
| E4  | §7.3（dependency class 判定）      | "类别在收集时由 Runtime 依内容判定"不可行——即 Runtime 理解自由文本 rationale，违背确定性原则。改为：**Scene Agent 在 proposal 中结构化声明**依赖，Runtime 只做确定性校验与强制覆盖（声明依赖 border/邻接的一律 volatile 不持久化）。                                                                     |
| E5  | §7.5（per-scene 指纹）            | 标注复用关系：per-scene 指纹 = SW 侧 `_scene_token`（内容字段指纹）的 shots 域扩展，非全新概念。                                                                                                                                        |
| E6  | §8.2（"shot_id 删除不回收"）        | 与实测不符：shot_id 分配 = 段内**现存**最大序号+1，同轮删后新建会复用刚删除的 id（spike S5 实测；scene_id 侧因 `next_scene_seq` 项目级单调确实不复用）。decision memory 的 provenance 应引用 **(shot_id + basis 指纹)** 而非裸 shot_id，防同名不同物的审计混淆（前提指纹防线本身有效）。                                            |

