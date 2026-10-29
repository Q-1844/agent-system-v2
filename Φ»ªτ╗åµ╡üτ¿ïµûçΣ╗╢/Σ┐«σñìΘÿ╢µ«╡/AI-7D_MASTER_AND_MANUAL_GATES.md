# AI-7D Master Plan — Unified Agent Interaction Runtime

> 目标：先把 **所有用户可见 Agent 入口真正接到已经存在的 Agent / ChangeSession / ChangeExecution / ProductionRun 后端**，再优化 Agent 的“聊天感”。
>
> 执行顺序：**Claude Code 负责前后端统一接线与真实浏览器验收 → 用户逐段手测 → Trae 负责交互自然度与 Agent 协作表现优化。**
>
> 本阶段不重做 AI-7A / 7B / 7C-H 后端协议；不提前做 Reviewer / 自主质量循环；不以“改 Prompt 避免吐 JSON”代替真实运行时接线。

---

## 0. 本阶段为什么存在

当前已确认至少存在两类用户入口：

1. 新路径：自然语言进入 `ChangeSession / ChangeExecution / ProductionRun`，有真实后端对象、持久状态与恢复能力。
2. 旧路径：Agent Center 的 Direct Chat 仍可能落到 legacy `send_message()`，仅把模型文本原样保存；模型即使输出“工具调用”也没有真正执行。

所以 AI-7D 的问题不是“模型不够聪明”，而是：

> **同一个产品里，不同输入框进入了不同代的 Agent Runtime。**

AI-7D 必须把所有 Agent 前门统一到一套行为语义。

---

# 1. 产品决策（已拍板，不再让实现 Agent 自行改方向）

## 1.1 Direct Chat

用户可以与每个 Agent 单独交流：

- Producer
- Script Writer
- Character Designer
- Director
- Production Planner
- Shot Producer
- Continuity Reviewer
- Editor

Direct Chat 是用户唯一的自然语言对话入口。

用户说一句话后，系统自动判断它属于：

- 普通交流
- 项目事实查询
- 创作修改
- 运行状态查询
- 运行控制 / 决策

用户不需要知道后台应该调用哪个 Agent、哪个 API、哪个 ChangeSession。

---

## 1.2 Team Chat 改为“AI-only Coordination Room”

Team Chat / Group Chat **不再允许用户输入消息**。

它的作用只有一个：

> **真实展示 Agent 之间为了完成用户任务而发生的协作。**

因此前端必须：

- 移除或禁用 Team Chat 的用户输入框；
- 不把用户的 Direct Chat 原文复制进群聊；
- 显示真实 Agent → Agent 的请求、回复、协调、发现、阻塞与交接；
- 每条协作消息必须能追溯到真实 `AgentTask / TeamInquiry / ChangeSession / ChangeExecution` 等对象；
- 不展示隐藏 Chain-of-Thought；
- 不展示模型伪造的 function JSON；
- 允许用户点击 Agent 名称跳到其 Direct Chat；
- 允许查看任务详情 / 依据 / 运行卡；
- 审批可以保留按钮，但“补充说明/要求修改”必须回到某个 Direct Chat（优先 Producer）。

Team Chat 不是“第二个用户聊天入口”，而是“制作团队会议室的观察窗”。

---

## 1.3 Agent 交互必须像真实聊天，而不是状态机朗读器

完成接线后，Agent 对用户必须有连续、自然的对话表现：

- 先理解用户真正想做什么；
- 需要查看项目时，真的查看后再回答；
- 需要修改项目时，真的创建 ChangeSession；
- 需要等待时，用自然语言说明正在做什么，但不能假装已经执行；
- 工具调用、ID、JSON、内部字段默认不暴露给用户；
- 一个真实动作完成后必须回到对话，用正常语言告诉用户结果；
- 非必要不每句都“好的，我将……”“已为您……”；
- 不要求用户为了后台架构反复切页面、重新输入同一句需求。

---

# 2. 关于“Agent 记忆”的当前定义

AI-7D 开始前必须先审计，而不是假设“记忆已经完整实现”。

目前应区分三种东西：

### A. Conversation persistence
Agent Direct Chat 的 session / messages 能否跨刷新、跨后端重启恢复。

### B. Operational memory
`TeamThread / AgentTask / ChangeSession / ChangeExecution / ProductionRun` 等持久对象能否让 Agent 知道“刚才真实发生了什么”。

### C. Long-term learned memory
跨会话总结用户偏好、长期习惯、过去纠正、可复用经验的 semantic memory / vector memory。

AI-7D 必须先验证 A + B。

**不要默认 C 已经存在。**
如果代码审计确认没有真正的 long-term memory，这是正常历史边界，不得临时引入向量数据库。

若 A + B 已经足以让 Direct Chat 连续自然地工作，本阶段不新增 Memory 系统。

若 Direct Chat 因长历史截断而无法保持连续性，允许增加一个很小的、Project-scoped 的 `ConversationMemorySummary`：

- 必须可追溯到原始 session/message/task；
- 只保存稳定事实、用户明确决定、未完成事项；
- 不保存模型猜测；
- 不跨 Project；
- 不做向量数据库；
- 不把摘要当作 canonical truth；
- 用户新指令与后端项目事实优先于旧摘要。

---

# 3. 执行责任

## Claude Code：先做“真实接线”

Claude Code 负责：

1. 全入口审计；
2. 统一 Turn Runtime；
3. Direct Chat → Inquiry / ChangeSession / Runtime Truth；
4. Team Chat AI-only；
5. 现有持久化/记忆接线；
6. 前后端真实浏览器 E2E；
7. 每个 checkpoint 完成后 STOP，等待用户手测。

Claude Code **不负责**大规模“人格润色”。

---

## 用户：每个 checkpoint 后亲自手测

用户的手测优先级高于实现 Agent 的自评。

任何阶段：

- 后端测试 PASS，但真实前端 FAIL → 阶段 FAIL；
- 浏览器自动化 PASS，但用户实际聊天明显异常 → 阶段不能继续；
- 模型偶发输出不理想，但底层真实动作正确 → 记录给 Trae，不要立刻破坏运行时。

---

## Trae：后做“交互感优化”

只有当 Claude Code 的完整接线阶段通过后，Trae 才进入。

Trae 负责：

- Agent 回复自然度；
- 角色专业感；
- 上下文承接；
- Agent-to-Agent 协作对话可读性；
- 减少模板腔；
- 控制澄清问题数量；
- 优化“行动前 / 行动中 / 行动后”的话术节奏；
- 建立真实交互 Evals。

Trae 不允许重新发明 ChangeSession / Execution 协议。

---

# 4. 阶段顺序与人工 Gate

| Checkpoint | 实现者 | 核心目标 | 自动浏览器验证 | 用户手测后才能继续 |
|---|---|---|---|---|
| D0 | Claude Code | 全入口真实调用图 | 可选 | 是 |
| D1 | Claude Code | Direct Chat 普通问答 + 真查询 | 必须 | 是 |
| D2 | Claude Code | Direct Chat 修改 → 真 ChangeSession | 必须 | 是 |
| D3 | Claude Code | 状态/取消/恢复 → Runtime Truth | 必须 | 是 |
| D4 | Claude Code | AI-only Team Chat | 必须 | 是 |
| D5 | Claude Code | 会话/任务/重启记忆连续性 | 必须 | 是 |
| D6 | Claude Code | 全入口 Golden Path + regression | 必须 | 是 |
| Q1-Q4 | Trae | 自然聊天与 Agent 协作质感 | 必须做 transcript/eval | 用户最终验收 |

---

# 5. 强制停止规则

Claude Code 每完成一个 checkpoint 必须输出：

1. 本段改了什么；
2. 真实前端路径是什么；
3. 自动测试与浏览器测试结果；
4. 当前服务地址；
5. 给用户的 3–5 条手测消息；
6. 预期 UI / 后端对象；
7. 已知限制。

然后明确：

> `STOP — waiting for manual interaction acceptance.`

没有用户明确通过，不得进入下一 checkpoint。

---

# 6. 绝对禁止

AI-7D 不得：

- 用 Prompt Patch 掩盖 legacy runtime；
- 让用户看到 `{"function": ...}`；
- 让模型文本决定“运行中/完成”；
- Direct Chat 检测到修改后要求用户去另一个输入框重输；
- Team Chat 继续作为用户群聊输入框；
- 将整个 Team Chat transcript 无限制塞进模型上下文；
- 复制全部 private Direct Chat 到 Team Chat；
- 新建第二套 Change Plan；
- 直接让 Specialist 修改 canonical；
- 把真实长期 Memory 偷换成“把所有聊天历史全塞给模型”；
- 因为后端 pytest 通过就跳过真实浏览器；
- 未经用户手测就宣布 AI-7D 完成；
- 开始 AI-7E / Reviewer / 自动审片循环。

---

# 7. 最终成功定义

AI-7D 接线阶段完成时，用户从任何 Direct Chat 发消息都必须满足：

```text
普通交流       → 自然 Agent reply
项目事实查询   → 真 inspect / 真 delegation → 自然 reply
修改项目       → 真 ChangeSession → Proposal / Review / Execute
问进度         → 真 Session/Execution/Run/Job 状态
停止/恢复      → 真 backend control
跨模块协作     → 真 AgentTask / ChangeSession + AI-only Team Chat 可见
刷新/重启      → 会话和运行状态恢复
```

并且：

> **任何用户可见 Agent 界面都不能再退回“模型假装有工具、后端却什么都没做”的 chatbot。**

---

# 8. Research basis（只借鉴原则，不照搬框架）

本方案参考但不复制以下公开模式：

- OpenAI Agents SDK：Agent = instructions + tools + runtime loop；Manager/Agents-as-tools 与 Handoffs；Session 持久化。
- OpenAI Agent orchestration：LLM orchestration 与 code orchestration 可混合；对确定性路由使用结构化输出/代码。
- Anthropic Building Effective Agents：优先简单可组合架构；Agent 必须从环境工具获得 ground truth。
- Anthropic Multi-Agent Research：Lead Agent + Specialist Workers；并行只用于独立子问题；要有可观察性与测试。
- Anthropic long-running agent harness：长任务依赖外部持久状态桥接不同上下文，而不是相信模型“自己记住”。
- OpenAI Session / Memory 文档：conversation session memory 与更长期的 learned memory 是不同层。

这些原则只用于校准 AGNES 自己的运行时设计。
