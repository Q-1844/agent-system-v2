# AI-6B · Multi-Agent Foundation

你现在要继续开发 AGNES 项目。

本阶段名称：

**AI-6B — Multi-Agent Foundation**

本阶段不是制作一个复杂的全自动 Agent 系统，也不是一次完成所有 AI Agent 能力。

目标只有一个：

> **在现有 AGNES 稳定工作流、Production 系统、AI Workflow、Workspace / ChangeSet / Revision 基础上，建立一个简单、清晰、可扩展的 8-Agent 基础框架。**

先把骨架做正确。

不要过度设计。

---

# 0. 开始前必须先检查现有项目

在修改代码以前，先自行阅读并确认当前实际代码结构。

重点检查：

* 当前 AI-5B / Full Project Autopilot 的实现；
* AI-6A Workspace / ChangeSet / Revision / Impact 的实现；
* Project / Scene / Shot 数据模型；
* Production Planner；
* Production Run / Checkpoint；
* JobQueue；
* 当前 LLM / Agnes client；
* 当前 Settings；
* 当前前端路由和 Layout；
* 当前项目数据保存方式；
* 当前 API routers / services 的组织方式。

同时执行：

```bash
git status
git log --oneline -15
```

不要 reset、clean、checkout 或删除任何用户现有修改。

如果实现细节和本文描述略有不同，以**当前代码事实**为准，保持现有架构风格。

本阶段不要重构已经稳定工作的 Production Pipeline。

---

# 1. AI-6B 的核心架构

AGNES 从现在开始采用：

```text
Producer Supervisor
        │
        ├── Script Writer Agent
        ├── Character Designer Agent
        ├── Director Agent
        ├── Production Planner Agent
        ├── Shot Producer Agent
        ├── Continuity / Review Agent
        └── Editor Agent
```

一共固定 **8 个 Agent**：

```text
1. Producer Supervisor
2. Script Writer
3. Character Designer
4. Director
5. Production Planner
6. Shot Producer
7. Continuity Reviewer
8. Editor
```

这一阶段不要继续增加 Agent 类型。

---

# 2. 八个 Agent 的职责必须明确固定

## 2.1 Producer Supervisor

定位：

**项目总协调者。**

负责：

* 理解用户项目级要求；
* 阅读整个项目状态；
* 判断应该找哪个专业 Agent；
* 创建 AgentTask；
* 把任务分派给专业 Agent；
* 查看子任务状态；
* 汇总结果。

它逻辑上拥有整个能力目录。

但不要把所有底层工具 schema 一次性塞给它。

它主要看到：

```text
Story
Character
Storyboard
Production Planning
Shot Production
Review
Composition
Workspace / Revision
```

本阶段只允许：

```text
Producer
    ↓
Specialist Agent
```

**一层 delegation。**

不要实现：

```text
Producer
→ Director
→ Planner
→ Shot Producer
→ Reviewer
→ 无限递归
```

复杂递归 Agent 留以后。

---

## 2.2 Script Writer Agent

负责：

```text
Story
Scene
剧情结构
故事修改
故事续写
角色出场关系
```

不负责：

```text
角色图片生成
Shot 制作
Production Plan
视频生成
Compose
```

以后需要修改正式项目时，必须走已有安全业务接口或 Workspace / ChangeSet。

---

## 2.3 Character Designer Agent

负责：

```text
Character
角色描述
角色视觉设定
角色参考图
角色一致性相关建议
```

以后可以调用角色生成相关能力。

但本阶段不要重新实现角色生成 Pipeline。

优先包装现有能力。

---

## 2.4 Director Agent

负责：

```text
Scene → Shot
Storyboard
镜头结构
镜头节奏
镜头意图
KEEP
UPDATE
INSERT
DELETE
REORDER
```

非常重要：

Director **永远不允许直接覆盖 canonical project 的 shots 数组。**

以后所有已有 Storyboard 修改必须：

```text
Director Proposal
        ↓
Semantic ChangeSet
        ↓
AI-6A Validation
        ↓
Impact
        ↓
Workspace
```

AI-6B 只建立这个权限边界和未来接口。

**本阶段不要正式实现复杂 Storyboard Variant。**

原本规划的 Director Variant 暂时留到下一阶段。

---

## 2.5 Production Planner Agent

职责：

> **决定这个 Shot 应该怎么制作。**

负责：

```text
Production Plan
制作策略
参考来源
continuity strategy
opening source
ending strategy
prompt strategy
generation strategy
```

它不负责真正调用图片/视频生成 API。

Planner 和 Shot Producer 必须保持分离。

---

## 2.6 Shot Producer Agent

职责：

> **按照 Production Plan 真正执行镜头制作。**

以后控制：

```text
first frame
last frame
continuous frame
image generation
video generation
retry
candidate
production run
```

但不要为：

```text
首帧
尾帧
图片
视频
```

分别建立不同 Agent。

这些都是 **Shot Producer 的不同 Capability**。

一个 Shot Producer Agent 可以有不同 scope：

```text
Shot Producer
scope = s01_c01

Shot Producer
scope = s01_c02
```

这是同一种 Agent 的不同任务实例。

---

## 2.7 Continuity Reviewer Agent

负责：

```text
角色一致性
画面连续性
空间连续性
动作连续性
生成结果检查
失败原因判断
retry proposal
quality review
```

本阶段原则上是：

**Inspect + Review + Proposal**

不要让 Reviewer 随意直接修改 Project。

---

## 2.8 Editor Agent

负责：

```text
版本选择
镜头排列检查
Composition
字幕
音轨
最终导出
```

以后控制现有 Compose / Export 能力。

不要让 Editor 修改 Story / Character / Storyboard 内容。

---

# 3. 最重要的架构原则

必须保持：

```text
Agent = 决策层 / 大脑

Workflow = 确定性执行系统

Tool / Capability = Agent 的手

Project / Workspace = 共享状态

JobQueue / Dependency / Checkpoint = 执行规则
```

不要把现有 Production Controller、JobQueue、Checkpoint、Dependency 判断改成 LLM 决策。

例如：

```text
Shot 1 没完成
↓
Shot 2 是否能开始
```

必须继续由代码判断。

不能让 Agent 自己猜。

---

# 4. 新增 AgentRegistry

建立一个简单的 Agent Registry。

具体文件位置按照当前项目结构决定，例如：

```text
src/agents/
```

不要为了符合这个示例强行改变项目已有目录组织。

建议基础结构：

```text
agents/
    models.py
    registry.py
    capabilities.py
    runtime.py
    sessions.py
    tasks.py
```

不要创建几十个文件。

第一版保持简单。

AgentDefinition 至少包含类似：

```python
id
name
description
role
allowed_capabilities
read_scopes
write_scopes
can_delegate_to
default_model_profile
enabled
```

注册固定八个 Agent。

例如稳定 ID：

```text
producer
script_writer
character_designer
director
production_planner
shot_producer
continuity_reviewer
editor
```

这些 ID 后续会进入持久化数据。

因此不要轻易改变。

---

# 5. CapabilityRegistry

不要采用：

> 一个 Workflow Node = 一个 Agent

而采用：

> 一个 Workflow Node = 一个 Capability

多个 Capability 可以属于同一个 Agent。

建立：

```text
CapabilityDefinition
```

至少包含类似：

```python
id
name
description
category
owner_agent
allowed_agents
operations
risk_level
```

operations 第一版可以支持：

```text
inspect
execute
retry
cancel
```

并不是每个 Capability 都必须拥有全部 operation。

例如：

```text
story.inspect
story.edit

character.inspect
character.design

storyboard.inspect
storyboard.propose_change

production_plan.inspect
production_plan.create

shot.first_frame
shot.last_frame
shot.video

review.shot
review.scene

composition.inspect
composition.compose
```

注意：

**AI-6B 不要求这些 Capability 全部真正执行。**

本阶段优先建立：

```text
Capability Registry
+
权限判断
+
调用 adapter 接口
```

然后只接通少量安全能力用于证明框架成立。

不要为了“看起来完整”重新包装整个 AGNES 后端。

---

# 6. Capability 必须包装现有业务能力

禁止重新写一套：

```text
AgentImageGenerator
AgentVideoGenerator
AgentComposer
```

如果项目已经有：

```text
character service
shot service
production planner
production controller
compose service
workspace service
```

Agent Capability 应该调用这些已有 service。

正确：

```text
Agent
↓
Capability
↓
Existing Service
↓
Existing Workflow
```

错误：

```text
Agent
↓
重新写一份生成逻辑
↓
直接调用 Agnes
```

---

# 7. Agent 权限必须真正检查

建立统一权限检查。

例如：

```python
registry.can_execute(
    agent_id="director",
    capability="storyboard.propose_change",
)
```

允许。

但：

```python
registry.can_execute(
    agent_id="editor",
    capability="character.design",
)
```

必须拒绝。

不要只把权限写在 UI 文案里。

后端必须检查。

---

# 8. AgentSession

建立最小 AgentSession。

用户以后需要分别和八个 Agent 聊天。

因此不同 Agent 的 Conversation 必须隔离。

至少区分：

```text
project
agent_id
scope_type
scope_id
session_id
```

例如：

```text
Producer
project = demo

Director
project = demo
scene = s03

Shot Producer
project = demo
shot = s03_c02
```

AgentSession 可以保存：

```python
session_id
project
agent_id
scope_type
scope_id
created_at
updated_at
```

消息历史可以使用简单 JSON persistence。

不要引入向量数据库。

不要做长期记忆系统。

不要做 RAG。

不要做自动摘要系统。

第一版只需要：

> 不同 Agent 的聊天历史互不混在一起。

---

# 9. Agent 之间不要共享全部聊天历史

所有 Agent 共享的是：

```text
Project State
Workspace State
AgentTask
Artifacts
Production Plan
Production Run
```

不是：

```text
Producer 的全部对话
+
Director 的全部对话
+
Character 的全部对话
```

例如 Character Agent 得到最终结果：

```text
Character LinXia
appearance_version = 4
```

Director 以后应该读取结构化 Project / Artifact。

不应该收到 Character Agent 二十分钟聊天原文。

---

# 10. AgentTask

建立最小 AgentTask 模型。

至少包含类似：

```python
task_id
parent_task_id
project
requester_agent
target_agent
session_id
scope_type
scope_id
instruction
capability_id
status
result_summary
artifact_refs
error
created_at
updated_at
```

状态第一版保持简单：

```text
pending
running
completed
failed
cancelled
```

不要做复杂 DAG。

不要做 distributed task engine。

不要重新实现 JobQueue。

AgentTask 是：

> AI 任务协调记录。

JobQueue 是：

> 图片、视频、Production 等真正执行任务。

二者不要混为一个概念。

---

# 11. 实现最小 delegation

本阶段必须证明：

```text
Producer
↓
创建 AgentTask
↓
交给 Specialist Agent
↓
Specialist 完成
↓
返回结构化结果
↓
Producer 可以读取结果
```

例如可以做一个安全 Demo：

```text
Producer:
“检查 Scene 3 的分镜结构”

↓

Director Agent

↓

storyboard.inspect

↓

返回：

{
  "summary": "...",
  "scene_id": "s03",
  "issues": [...],
  "artifact_refs": [...]
}
```

不需要一开始就让 Director 自动修改 Storyboard。

先证明：

**Agent Registry → Permission → Task → Specialist → Result**

整条链路成立。

---

# 12. Specialist 暂时不要互相 delegation

AI-6B 第一版：

```text
Producer
→ Specialist
```

允许。

```text
Specialist
→ Specialist
```

暂时禁止。

因此：

```text
Director → Planner
Planner → Shot Producer
Reviewer → Shot Producer
```

以后再开放。

这样可以避免第一版出现：

* 无限循环；
* 重复任务；
* Agent 相互踢皮球；
* 上下文爆炸。

---

# 13. ModelProfile

用户以后会有多个推理模型、多个图片模型和多个 Agnes account。

现在建立简单 ModelProfile。

不要复杂。

至少支持逻辑配置：

```python
id
name
reasoning_model
fallback_reasoning_model
image_model
video_model
account_policy
```

注意：

```text
Image Model ≠ Agent
Video Model ≠ Agent
```

它们只是 Agent 可以使用的工具模型。

例如：

```text
Director
reasoning = model_d

Character Designer
reasoning = model_a
image = image_model_1

Shot Producer
reasoning = model_b
image = image_model_2
video = agnes_video
```

第一版只需要支持：

> Agent → ModelProfile

映射。

不要做复杂模型路由。

不要做动态 benchmark。

不要做成本优化算法。

不要做自动模型评测。

---

# 14. AccountPool 只做最小骨架

用户目前可能有多个 Agnes Account。

不要把 API Key 写进 Agent。

建立逻辑 account alias / policy，例如：

```text
auto
agnes_a
agnes_b
```

或者按照当前项目已有配置系统实现。

Agent 只保存：

```text
account_policy
```

API Key 继续走安全配置 / environment。

前端绝对不能展示完整 secret。

本阶段不需要复杂负载均衡。

如果当前已有 Agnes account abstraction，则复用。

如果没有，只建立简单接口和配置结构，不要为了这个阶段大改 AgnesClient。

---

# 15. Agent Runtime

建立非常薄的一层：

```text
AgentRuntime
```

负责：

```text
resolve agent
resolve session
resolve model profile
build context
permission check
dispatch capability
create/update AgentTask
return structured result
```

不要建立复杂：

```text
reflection loop
planning loop
critic loop
self correction loop
multi-agent debate
recursive planning
```

AI-6B 一律不要。

---

# 16. Agent system prompt

每一个 Agent 可以有自己的基础 system role。

但第一版保持简短稳定。

system prompt 应至少明确：

```text
你是谁
你的职责
你可以操作什么
你不能操作什么
当前 project / scope
输出要求
```

不要写几千字人格 Prompt。

Agent 是专业角色，不是聊天人格。

---

# 17. Project Context Builder

不要让每个 Agent 自己随意扫描整个项目。

建立一个简单 Context Builder。

根据 Agent 类型提供最小必要上下文。

例如：

```text
Script Writer
→ Story + Scenes + Characters summary

Character Designer
→ Character + Style + related scenes

Director
→ Scene + Characters + Shots + Style

Planner
→ Shot + continuity + existing assets + production state

Shot Producer
→ Production Plan + Shot + required asset refs

Reviewer
→ Shot + expected plan + generated asset metadata

Editor
→ Shot order + chosen versions + audio/subtitle/composition state

Producer
→ Project summary + workflow state + agent task summary
```

第一版不需要 token optimizer。

只保持职责隔离。

---

# 18. 6A Workspace / ChangeSet 是 Agent 修改项目的安全边界

这是本阶段不能破坏的红线。

任何未来 Agent 对已有项目做结构性修改时：

```text
Agent Proposal
↓
Semantic ChangeSet
↓
Workspace
↓
Validation
↓
Impact
↓
Apply
```

不要新增：

```text
agent.save_project()
agent.write_project_json()
```

Director 特别禁止直接：

```python
project.scenes[x].shots = new_shots
```

如果目前 AI-6A 已经提供相关 service / API，Capability 应预留连接这些接口。

AI-6B 不要求把 Director Variant 全做完。

---

# 19. Canonical Project 保护规则

必须继续遵守：

> Agent 不得绕过现有业务层随意修改 canonical project。

Canonical 写入继续通过：

```text
现有业务 API / Service
```

Creative 修改继续通过：

```text
Workspace / ChangeSet
```

Agent Runtime 不是新的数据层。

---

# 20. 前端：新增一个最简单的 Agent Center

不要大改现有 UI。

增加一个基础入口：

```text
AI 团队
或
Agent Center
```

页面保持简单。

左侧或顶部显示 8 个 Agent：

```text
Producer
Script Writer
Character Designer
Director
Production Planner
Shot Producer
Continuity Reviewer
Editor
```

每张卡至少显示：

```text
名称
职责一句话
当前 Model Profile
状态
```

点击 Agent 后进入它自己的简单聊天区域。

第一版聊天 UI 只需要：

```text
消息列表
输入框
发送
当前 Project
当前 Agent
可选 Scope
```

不要实现复杂 floating panel。

不要实现拖拽。

不要实现多窗口。

不要重新设计整个网站。

---

# 21. Agent 独立聊天

用户必须能够：

```text
和 Producer 聊
和 Director 聊
和 Character Designer 聊
...
```

不同 Agent 使用不同 AgentSession。

切换 Agent 时：

```text
Producer history
```

不能出现在：

```text
Director history
```

如果当前项目已经存在通用 LLM 调用 abstraction：

优先复用。

如果当前没有适合的 reasoning model runtime：

先建立接口 abstraction，并允许 Agent Chat 进入：

```text
not_configured
```

状态。

**不要为了让聊天框马上“看起来能说话”而硬编码一个未知模型。**

---

# 22. Producer 的最小 UI

Producer 页面可以比其他 Agent 多一个简单区域：

```text
Delegated Tasks
```

显示：

```text
Task
Target Agent
Status
Scope
Result Summary
```

只需要能看。

不做复杂任务图。

---

# 23. Settings：Agents & Models

在现有 Settings 中增加一个简单区域：

```text
Agents & Models
```

显示八个 Agent。

例如：

```text
Director

Reasoning Model
[ profile ▼ ]

Account Policy
[ Auto ▼ ]

Status
Enabled
```

如果 ModelProfile 本身包含 Image / Video，就不需要在每张 Agent 卡重复很多字段。

保持 UI 简单。

第一版核心只是：

> 看得见每个 Agent 当前绑定哪个 ModelProfile，并且可以修改。

配置必须持久化。

---

# 24. 不要在 AI-6B 做这些事情

本阶段明确禁止扩 scope：

```text
× 不做完整 autonomous Producer
× 不做一句话自动完成整部电影
× 不做递归 multi-agent planning
× 不做 Agent debate
× 不做 reflection loop
× 不做复杂 memory
× 不做 vector database
× 不做 RAG
× 不做 MCP
× 不引入 LangGraph / CrewAI / AutoGen 等大型 Agent framework
× 不重写 Production Workflow
× 不重写 JobQueue
× 不重写 Production Planner
× 不重写 AI-5B Autopilot
× 不重写 AI-6A Workspace
× 不做 Director Storyboard Variant 完整功能
× 不生成 N 个 Variant
× 不做 Workspace Production
× 不做自动视频 Review loop
× 不做自动 Retry loop
× 不做模型自动选择
× 不做复杂 Agnes 双账号负载均衡
× 不重新设计整个前端
```

如果一个功能不是完成 Multi-Agent Foundation 所必需：

**不要做。**

---

# 25. 不要引入新的大型依赖

除非当前项目已经使用，否则不要为了 AI-6B 引入：

```text
LangGraph
CrewAI
AutoGen
Semantic Kernel
新的数据库
Redis
Celery
```

目前项目自己的：

```text
Project
Workspace
Production
JobQueue
API
Service
```

已经足够成为 Agent Runtime 的底层。

---

# 26. Persistence

Agent runtime 数据不要随意塞进 canonical `project.json`。

以下属于运行时数据：

```text
AgentSession
AgentTask
Conversation
Agent Settings
```

应该按照当前项目已有 persistence 风格单独保存。

具体目录由你根据实际代码决定。

要求：

```text
服务重启以后
Agent Settings 不丢
AgentSession 不丢
AgentTask 历史不丢
```

但不要因此引入数据库。

第一版 JSON persistence 足够。

使用原子写。

---

# 27. API

根据当前 Router 风格建立简洁 API。

不强制路径完全一致，但至少需要覆盖以下语义：

```text
GET agents
GET agent capabilities

GET/UPDATE agent settings

CREATE/LIST agent sessions
READ agent session

SEND agent message

CREATE agent task
LIST agent tasks
READ agent task
```

Producer delegation 必须走正式 AgentTask。

不要偷偷在聊天 endpoint 内部创建不可追踪的后台任务。

---

# 28. Structured Result

Specialist 返回 Producer 的结果不要只是自由文本。

使用简单结构，例如：

```json
{
  "status": "completed",
  "summary": "...",
  "artifact_refs": [],
  "warnings": [],
  "suggested_next_actions": []
}
```

不要让 Producer 必须解析另一位 Agent 的长篇聊天才能理解任务结果。

---

# 29. 错误处理

至少明确处理：

```text
unknown agent
disabled agent
unknown capability
permission denied
unknown project
unknown scope
model not configured
provider unavailable
task failed
```

Agent 调用失败不能影响已有 Workflow。

---

# 30. 至少做一个真正的端到端证明

AI-6B 完成时必须存在一个很小但真实的闭环：

```text
用户打开 Agent Center
↓
选择 Producer
↓
Producer 创建 delegation task
↓
Target Specialist 收到 AgentTask
↓
权限检查通过
↓
调用一个安全 capability
↓
生成 structured result
↓
Task = completed
↓
Producer 页面可以看到结果
```

建议使用 **read-only / inspect 类型 capability** 作为第一次闭环。

不要第一次就拿：

```text
删除 Shot
生成视频
批量修改项目
```

证明框架。

---

# 31. Tests

至少补基础测试覆盖：

```text
8 个 Agent 是否全部注册
Agent ID 是否唯一
Capability owner 是否有效
permission allow
permission deny
AgentSession 隔离
AgentTask persistence
Producer → Specialist delegation
specialist 不能继续 delegation
ModelProfile persistence
未知模型配置错误
服务重启数据恢复
```

另外跑现有核心回归测试，确保：

```text
Project
Storyboard
Production Planner
Production Run
Workspace / ChangeSet
JobQueue
Compose
```

没有被破坏。

---

# 32. UI 验收

完成后至少人工验证：

```text
1. Agent Center 能看到固定 8 个 Agent

2. 每个 Agent 职责描述正确

3. 点 Producer 能进入自己的 Conversation

4. 点 Director 能进入另一个独立 Conversation

5. 两者聊天记录不串

6. Settings 能看到 Agent → ModelProfile

7. 修改配置后刷新页面仍存在

8. Producer 可以创建一次 Specialist Task

9. Task 有 pending/running/completed/failed 状态

10. Target Agent 和 Capability 权限错误时明确拒绝

11. 原有 Storyboard / Production 页面仍正常
```

---

# 33. 建议的数据关系

最终关系保持简单：

```text
AgentDefinition
        │
        ├── ModelProfile
        │
        ├── Capability[]
        │
        └── Session[]

Producer Session
        │
        └── AgentTask
               │
               └── Specialist Agent
                      │
                      └── Capability
                             │
                             └── Existing Service / Workflow
```

不要再加一层抽象，除非当前代码确实需要。

---

# 34. 八个 Agent 权限基线

第一版可以按照下面的原则建立权限矩阵：

```text
Producer
READ:
  entire project summary
  workflow state
  agent task state
DELEGATE:
  all specialists
WRITE:
  AgentTask
DIRECT BUSINESS WRITE:
  none by default


Script Writer
READ:
  story
  scenes
  characters
WRITE DOMAIN:
  story / scene proposal


Character Designer
READ:
  character
  project style
  related scenes
WRITE DOMAIN:
  character proposal / character assets


Director
READ:
  scene
  character
  shots
  style
WRITE DOMAIN:
  storyboard proposal via Workspace / ChangeSet


Production Planner
READ:
  shot
  assets
  continuity
  production state
WRITE DOMAIN:
  Production Plan


Shot Producer
READ:
  shot
  Production Plan
  assets
  Job state
EXECUTE DOMAIN:
  shot production capabilities


Continuity Reviewer
READ:
  expected plan
  shot
  generated assets
  continuity context
WRITE DOMAIN:
  review / retry proposal


Editor
READ:
  shots
  versions
  audio
  subtitles
  composition state
EXECUTE DOMAIN:
  composition / export
```

这只是权限基线。

请根据当前真实 service/API 名称连接。

---

# 35. 关于原 AI-6B Storyboard Variant

之前 AI-6B 曾规划：

```text
Director
读取已有 Storyboard
↓
KEEP
UPDATE
INSERT
DELETE
REORDER
↓
ChangeSet
↓
Workspace
```

这个设计仍然有效。

但现在路线升级成 Multi-Agent 后：

**本阶段只建立 Director Agent + Capability 权限 + Workspace 接口边界。**

不要在本阶段实现完整 Variant Engine。

完成 Multi-Agent Foundation 后，再单独实现它。

---

# 36. 代码风格要求

保持现有项目代码风格。

优先：

```text
small dataclass / pydantic models
simple registry
explicit permissions
explicit service adapters
simple JSON persistence
```

避免：

```text
metaclass
plugin magic
dynamic import magic
dependency injection framework
复杂 event sourcing
复杂 graph runtime
```

AGNES 当前最重要的是：

**可读、可查、可调试。**

---

# 37. 完成后的文档

完成后新增一份：

```text
AI_6B_MULTI_AGENT_FOUNDATION.md
```

记录：

```text
1. AI-6B 实际实现了什么
2. 8 个 Agent 的实际 ID
3. Capability Registry
4. Agent 权限矩阵
5. ModelProfile
6. AgentSession
7. AgentTask
8. Producer delegation 流程
9. Persistence 文件位置
10. 新增 API
11. 新增前端入口
12. 测试结果
13. 当前尚未实现的能力
14. 下一阶段推荐入口
```

不要把未来功能写成“已经完成”。

---

# 38. 最终汇报格式

完成后不要只回复：

> 完成了。

请按以下结构汇报：

```text
AI-6B Multi-Agent Foundation

1. Git 状态 / 基线

2. 新增文件

3. 修改文件

4. 八个 Agent
   - ID
   - 职责
   - 权限

5. Capability Registry
   - 已注册哪些
   - 哪些真正接通
   - 哪些只是预留

6. AgentSession
   - 如何保存
   - 如何隔离

7. AgentTask
   - Producer delegation 如何运行

8. ModelProfile / Account Policy
   - 当前做到什么程度

9. Frontend
   - Agent Center
   - Agents & Models

10. End-to-End Demo
   Producer
   → Specialist
   → Capability
   → Result

11. Tests
   - 新增测试
   - 现有回归
   - 结果

12. 没有做什么

13. 风险 / TODO

14. AI-6C 推荐内容
```

---

# 39. 本阶段成功标准

AI-6B 成功不是：

> “八个 AI 已经可以自动制作整部电影。”

而是：

> **AGNES 已经正式拥有一个清晰的 8-Agent Runtime 骨架。**

具体表现为：

```text
固定 8 个 Agent
✓

职责边界明确
✓

Capability 可注册
✓

Capability 有权限
✓

AgentSession 独立
✓

AgentTask 可追踪
✓

Producer 能 delegate
✓

Specialist 能返回结构化结果
✓

ModelProfile 可配置
✓

前端能看到 Agent Team
✓

Agent 可以独立进入 Conversation
✓

现有 Workflow 没被破坏
✓
```

做到这里立即停止。

**不要顺手进入复杂 Director Variant，不要开始完整 autonomous Producer，不要开始 Workspace Production。**

先把 AI-6B 这个基础框架封板。
