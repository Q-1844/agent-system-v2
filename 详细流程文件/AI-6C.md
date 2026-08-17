# AGNES Floating Agent UX + Storyboard Director Contextual Interaction

你正在修改现有 AGNES 项目。

这不是一次普通的聊天侧栏改版，也不是重新设计 Agent Center。

本次目标是建立 AGNES 正式产品级的 Agent 人机交互范式：

**全局主 Agent 使用可自由拖拽、可展开、可收缩的 Floating Agent Bar。**

**Storyboard 中的 Director 不创建第二套聊天窗口，而是作为 contextual specialist 出现在当前 Story / Shot 上；需要自然语言交流时复用同一个 Floating Agent Bar；Director 的正式修改结果未来以 inline Proposal 的形式直接呈现在 Storyboard Workspace 中。**

请先完整阅读现有代码后再修改。

不要凭空新建第二套 Agent 架构，不要重写已经稳定的 Agent Runtime、Session、Task、Capability、ModelProfile。

---

# 1. 先理解现状

先检查并确认以下现有实现，不要假设文件结构完全与描述一致：

`src/layouts/MainLayout.vue`

`src/views/agents/index.vue`

`src/views/storyboard/**`

`src/components/**`

`src/store/**`

`src/api/index.ts`

`src/router/index.ts`

以及现有 Agent 相关前端类型和 API。

同时阅读后端已有：

Agent list

Agent settings

Agent session

Agent messages

Agent tasks

Capability

Director

Producer

相关接口。

本轮优先复用现有 API。

严禁为了 UI 方便复制一套 Agent 状态。

---

# 2. 产品原则

AGNES 是专业 AI 影视创作工作台，不是 AI SaaS Chat 页面。

Agent UI 必须符合以下关系：

```text
Agent Center
= Team / Agent information / Session history / management

Floating Agent Bar
= 全局、高频、人机自然语言入口

Contextual Specialist UI
= 专业 Agent 在业务现场的入口

Workspace
= Agent 结果最终发生和被用户确认的位置
```

用户不应该为了让 Director 查看当前 Shot 而：

```text
离开 Storyboard
→ Agent Center
→ 找 Director
→ 打开聊天
→ 再描述当前 Shot
```

Agent 必须天然知道用户正在什么 Workspace、Story、Shot 上工作。

---

# 3. 设计参考原则

不要复制任何产品外观，但交互思想参考以下成熟模式：

Photoshop Contextual Task Bar：
on-canvas、context-aware、可拖动、可 reposition、可隐藏/固定。

Figma Agent：
on-canvas prompt；Agent 可以作用于当前选中对象；AI 属于 canvas workflow，而不天然属于固定侧栏。

Descript Selection Toolbar / Underlord：
选择具体业务内容以后出现 Ask Agent 能力，Agent 自动继承 selection context。

Adobe Firefly Boards：
生成入口和当前 canvas 共存，selected canvas assets 可以直接成为生成上下文。

LTX Studio：
AI 能力最终仍然围绕 Scene / Shot / Sequence 组织，而不是围绕聊天记录组织。

最终视觉必须是：

**professional cinematic creative software + restrained intelligence**

不要做：

紫色 AI 渐变

彩虹描边

大面积霓虹 glow

玻璃拟态堆叠

卡通机器人头像

大量 sparkle icon

巨大圆角 SaaS 卡片

固定右侧 Copilot sidebar

ChatGPT clone

---

# 4. 新建全局 Floating Agent Bar

创建合适的组件，例如：

`src/components/agent/FloatingAgentBar.vue`

如果现有目录命名约定不同，遵循项目现状。

该组件应该挂在 MainLayout 的全局层级中。

它必须跨页面存在，但页面切换不能销毁当前打开状态和当前聊天上下文，除非 project 改变。

使用 viewport overlay，而不是占据 normal document flow。

不能压缩 Storyboard。

不能把页面推开。

---

# 5. Floating Agent Bar 三种状态

状态 A：Collapsed

极小状态。

约：

44×44px 到 52×52px。

只显示非常克制的 AGNES 标记，例如：

`A`

或现有品牌 icon。

不要机器人头像。

鼠标 hover 显示 tooltip：

`Ask AGNES`

点击恢复 compact。

---

状态 B：Compact Bar

这是默认主要状态。

建议约：

320–380px 宽

46–52px 高

视觉类似专业 creative command bar，而不是巨大聊天输入框。

结构示意：

```text
┌─────────────────────────────────────────┐
│ ⋮⋮  AGNES / Producer    Ask anything… ↑│
└─────────────────────────────────────────┘
```

左侧必须有清晰但低干扰的 drag handle。

Agent 名称可以点击切换 Agent。

Input 支持直接输入。

Send 使用简单箭头/icon。

Enter 发送。

Shift+Enter 换行只在 expanded composer 中启用；compact 可以按现有产品习惯处理。

---

状态 C：Expanded Conversation

用户：

发送消息

点击历史

点击展开

或 Agent 回复

以后，在 Floating Bar 上方展开 Conversation Surface。

不要改变 Floating Bar 本身的位置。

例如：

```text
┌─────────────────────────────────────────┐
│ DIRECTOR                            ─ × │
│                                         │
│ context                                 │
│ Story 02 · Shot 03                      │
│                                         │
│ User                                    │
│ 这个镜头感觉没有冲击力。                 │
│                                         │
│ Director                                │
│ ……                                      │
│                                         │
└─────────────────────────────────────────┘
┌─────────────────────────────────────────┐
│ ⋮⋮  DIRECTOR                Ask…      ↑ │
└─────────────────────────────────────────┘
```

建议 conversation surface：

宽度与 bar 相同或略宽

最大高度约 420–520px

超过后内部滚动

根据 bar 当前 viewport 位置判断向上还是向下展开

靠近顶部时允许向下展开

靠近底部时优先向上展开

不能超出 viewport。

---

# 6. 真正实现自由拖拽

Floating Agent Bar 必须可以拖动到 viewport 中的任意合理位置。

不能只实现几个固定 dock position。

拖动必须：

使用 Pointer Events

mouse / trackpad 正常

拖动期间禁止误选文本

保持 12–16px viewport safe margin

window resize 后自动 clamp 回可视区

collapsed / compact / expanded 都不能掉出屏幕

拖拽 handle 和输入区域必须分离，输入文本时不能触发 drag。

保存用户位置。

优先 localStorage，例如：

`agnes:floating-agent:position:v1`

建议保存归一化 viewport 坐标，而不是只保存绝对 pixel，避免分辨率变化后跑出屏幕。

如果现有项目已有 UI preference store，则优先放入现有 store。

---

# 7. Position UX

第一次进入时不要居中挡住作品。

默认建议在：

viewport 右下区域，但距离边缘约 24–32px。

注意：

这是**默认出生位置**，

不是固定右下角。

用户拖走以后尊重用户位置。

提供菜单：

```text
Collapse
Reset position
Agent Center
```

如果实现 pin 概念，应保证语义明确：

pin 只是锁住 bar，防止误拖，

不是把它变成 sidebar。

---

# 8. Floating Agent Store

建立单一的 UI 状态管理。

例如：

`src/store/agentUi.ts`

或按现有 store 规范命名。

至少管理：

current UI state

collapsed / compact / expanded

activeAgentId

activeSessionId

currentContext

position

isDragging

conversation open state

unread / running state

不要复制后端 Session 数据。

Conversation 数据继续来自已有 Agent Session / Messages API。

---

# 9. Agent Context Model

前端定义一个统一轻量 Context 类型。

例如概念上：

```ts
type AgentUiContext =
  | { type: 'project'; projectName: string }
  | { type: 'script'; ... }
  | { type: 'character'; characterId: string; ... }
  | { type: 'story'; sceneId: string; ... }
  | { type: 'shot'; sceneId: string; shotId: string; ... }
  | { type: 'shots'; sceneId: string; shotIds: string[]; ... }
  | { type: 'production'; ... }
```

这里只是 UI context descriptor。

不要复制完整 project object。

真正 Agent 上下文仍由后端现有 context/runtime 机制读取。

前端只负责明确告诉系统：

用户当前指的是谁。

---

# 10. Agent Switcher

Floating Agent Bar 默认可以显示：

AGNES / Producer

但允许切换到已有 Specialist。

不要做 8 张巨大卡片。

点击 Agent 名称后使用非常紧凑的 popover / command menu：

```text
Producer
Director
Script Writer
Character Designer
Production Planner
Shot Producer
Continuity Reviewer
Editor
```

必须使用现有 Registry/API 返回的数据，而不是重新硬编码另一份 Agent metadata。

disabled Agent 必须表现为 disabled。

切换 Agent 时：

如果当前 context 与 Agent 匹配，保留 context；

如果不匹配，不要偷偷伪造业务上下文。

---

# 11. Agent Center 保留

不要删除现有 Agent Center。

Agent Center 继续承担：

Team overview

Agent capability information

Model profile

Enabled state

Session history

独立查看 Conversation

但将它视为低频管理页面。

Floating Agent Bar 才是正式高频入口。

如果现有 sidebar 有「AI 团队」，本轮可以继续保留。

不要因为 Floating Bar 出现就删除 Agent Center。

---

# 12. Storyboard：实现 Contextual Director

这是本轮视觉与交互重点。

不要增加固定 Director sidebar。

不要在每一个 Shot 上永久显示：

`✨ AI`

不要制造视觉噪音。

Director UI 必须只在 context 成立时出现。

---

# 13. Shot Selection

检查现有 Storyboard ShotCard / Shot Workspace。

建立明确的 selection 状态：

current Story

selected Shot

以后允许扩展 multi-shot selection，但如果当前架构没有多选，本轮不要强行重写。

选中 Shot 后必须存在清楚但克制的 selection treatment：

轻微 border

很浅的 highlight

不要高亮整张卡为品牌色。

---

# 14. Director Context Toolbar

用户选中 Shot 后，在 Shot 附近出现一个 contextual floating toolbar。

位置优先：

Shot 上边缘附近

或 Shot title/header 附近

不要遮挡首帧图片

不要漂移到页面固定位置。

示意：

```text
              DIRECTOR
      ┌─────────────────────────────┐
      │ 节奏  构图  重做镜头  Ask… │
      └─────────────────────────────┘
```

视觉应非常紧凑。

约 36–40px 高。

不要大面积背景。

只保留极轻阴影 + 1px border + solid surface。

如果空间不足，自动 flip 到 Shot 下方。

---

# 15. Toolbar Actions

根据当前已有 Director capability，首先提供不会制造虚假功能的入口。

至少：

`Ask Director`

以及可以转化为 prompt 的快捷意图，例如：

`分析镜头`

`优化节奏`

`分析构图`

`重新设计`

但这些 quick action 本质都应该：

设置 activeAgent = director

注入当前 context

把 Floating Agent Bar 展开

发送或预填明确 prompt

不要创建另一套 Director Chat。

---

# 16. Story Level Director

如果用户没有选具体 Shot，但当前位于某个 Story Segment：

允许在 Story header 通过非常克制的入口调用 Director。

例如 Story 标题旁：

`Director`

不要使用永久发光按钮。

点击后：

activeAgent = director

context = current story

打开 Floating Agent Bar。

因此：

Story 级问题和 Shot 级问题使用同一套系统。

---

# 17. Context Chips

当 Floating Agent Bar 因业务上下文被打开时，显示 compact context chips。

例如：

```text
DIRECTOR

[Story 02] [Shot 03]
```

或者：

```text
SCRIPT WRITER

[Scene 04]
```

Chip 应该非常克制。

避免巨大 capsule。

允许 X 清除 context。

鼠标 hover 可以说明：

`Director will use this shot as context`

中文 UI 则统一使用中文。

---

# 18. Director Conversation 不等于 Proposal

必须在代码结构中明确区分：

conversation message

structured proposal

不要把 future proposal 当 Markdown 聊天消息硬解析。

建立独立的 UI/domain 类型边界。

例如未来：

`DirectorProposal`

`ProposalOperation`

但本轮必须先检查现有后端类型和 AI-6A ChangeSet 类型。

如果已有 ChangeSet / Workspace 类型：

直接复用或适配。

禁止重新创造竞争数据模型。

---

# 19. 为 Inline Proposal 建立正式 UI 组件

即使本轮 `storyboard.propose_change` 后端尚未真正接通，也请建立合理的组件边界。

例如：

`DirectorProposalOverlay.vue`

`ProposedShotCard.vue`

`ProposalReviewBar.vue`

但：

**禁止放假 AI 数据欺骗用户。**

没有真实 Proposal 时这些组件不显示。

可以通过 dev fixture / component test 验证，不要在正式产品路径随机显示 mock。

---

# 20. Proposal 必须进入 Storyboard

未来 Director 返回 Proposal 后：

不要主要把 Proposal 塞在 Floating Chat Window 里。

聊天区只显示非常简短的总结，例如：

```text
我建议调整 3 个镜头。
2 个更新，1 个新增。

[在分镜中查看]
```

真正 Proposal 必须呈现在 Storyboard。

---

# 21. INSERT Proposal

如果 Director 建议新增 Shot：

直接在正确的 Shot sequence 位置出现 Ghost Shot。

例如：

```text
Shot 02

┌ ┄ ┄ ┄ ┄ ┄ ┄ ┄ ┄ ┄ ┐
│ DIRECTOR · INSERT    │
│                     │
│ Proposed Shot 03    │
│ 近景                │
│ 阿岚突然停下……      │
│                     │
│ [Accept] [Dismiss]  │
└ ┄ ┄ ┄ ┄ ┄ ┄ ┄ ┄ ┄ ┘

Shot 03
```

Ghost Shot 要明显表示：

**尚未写入 Workspace。**

但不要做成警告框。

使用：

dashed / subtle border

低一级 surface

小型 operation label

即可。

---

# 22. UPDATE Proposal

UPDATE 不生成脱离上下文的大卡片。

直接在目标 Shot 上显示 Proposal 状态。

提供：

Before

After

或 compact diff。

不要把整个 Shot 复制两份长期并排。

默认显示 change summary。

用户点击 Review 才展开细节。

---

# 23. DELETE Proposal

DELETE 状态：

原 Shot 仍然存在

降低部分视觉权重

显示：

`DIRECTOR · PROPOSED DELETE`

用户确认之前绝不真正删除。

---

# 24. REORDER Proposal

不要仅显示文字：

`Move Shot 04 before Shot 02`

Storyboard 本身要出现位置反馈。

建议：

destination insertion line

ghost placeholder

Shot number transition

让用户肉眼看懂它将移动到哪里。

---

# 25. KEEP Proposal

KEEP 默认不需要高亮整个 Shot。

最多在 Proposal Review 状态显示很小：

`KEEP`

不要让没有变化的内容制造噪音。

---

# 26. Proposal Review Bar

当存在 active Proposal 时，在当前 Storyboard workspace 内出现一条 review control。

不是 global footer。

不是 Director sidebar。

例如：

```text
Director Proposal
3 changes · 1 insert · 1 update · 1 reorder

[Dismiss]    [Review impact]    [Apply selected]
```

位置应该跟随当前 Story Segment 工作区。

用户滚动 Storyboard 时可以适度 sticky，但不要覆盖 Floating Agent Bar。

---

# 27. Selective Apply

UI 必须从第一天就支持：

接受全部

接受部分

忽略某项

不要假设 Proposal 是 all-or-nothing。

这与后续 ChangeSet Validation / Impact / Workspace 兼容。

应用真正修改之前必须继续走现有或下一阶段定义的：

Proposal

→ ChangeSet

→ Validation

→ Impact

→ Workspace

不要在前端直接 mutation `project.json` 或 Shot store 来“模拟应用”。

---

# 28. Running State

Agent 工作时不要显示：

巨大 spinner

“AI is thinking…”

彩虹 loading

使用专业工具式状态。

Floating Bar 可以：

agent label 旁出现细微 activity indicator

显示：

`Director · analysing Shot 03`

如果后端存在 task / job 状态，应读取真实状态。

不要 fake progress percentage。

---

# 29. Session 行为

从 Storyboard 调 Director 时：

优先复用相同：

agent_id

scope_type

scope_id

对应的现有 Agent Session 语义。

必须先阅读当前 `get_or_create` session 后端逻辑。

不要因为 Floating Agent Bar 新建了 UI 就每次创建新 session。

用户关闭 Floating Bar 后：

Session 不消失。

重新打开相同 Story / Shot context 时：

可以恢复对应 conversation。

---

# 30. 页面切换

Floating Agent Bar 跨页面保留。

但 current contextual attachment 必须有清楚规则。

例如：

从 Storyboard Shot 03

切到 Characters

不能继续静默显示：

Director · Shot 03

除非 UI 明确说明这是一个仍打开的历史 conversation。

建议：

active conversation 可以保留

但 context attachment 与当前 workspace 分离时，应视觉提示：

`Context from Storyboard`

而不是假装它仍是当前选择。

---

# 31. Responsive

桌面是第一优先级。

AGNES 属于专业创作工具，不要为了手机 responsive 破坏 desktop UX。

但至少：

窄屏不能让 Floating Agent Bar 跑出 viewport

conversation surface 最大宽度不得超过可视宽度

拖动位置自动 clamp

小尺寸时可以自动进入 compact/collapsed。

---

# 32. Visual Language

先读取现有 AGNES theme token。

尽量使用现有：

background

surface

border

text primary

text secondary

brand accent

不要新建一套颜色世界。

整体建议：

dark cinematic neutral surface

低对比层级

1px hairline border

soft shadow

8–12px radius

清晰 typography hierarchy

少量 accent

图像内容比 UI 更醒目。

Agent 活跃状态可以使用现有品牌强调色，但面积必须小。

---

# 33. Motion

动画必须短、克制。

建议：

compact → expanded：
160–220ms

context toolbar：
120–160ms fade + slight translate

collapse：
140–180ms

Ghost Proposal：
约 180–240ms

使用项目现有 easing。

尊重：

`prefers-reduced-motion`

禁止：

bounce

elastic spring

glow pulse

旋转 AI icon

---

# 34. Accessibility

Floating Agent Bar 必须 keyboard accessible。

所有 icon button 必须有 aria-label。

drag 不能成为唯一 reposition 手段：

menu 中保留 Reset Position。

context toolbar 可通过 keyboard focus 访问。

Esc：

优先关闭 popover / toolbar

然后关闭 expanded conversation

不要直接丢失 session。

---

# 35. 不要做的事情

本轮明确禁止：

固定 Director 右侧栏

第二套 Director Chat

每张 Shot 永久 AI 按钮

Agent 卡通头像

大面积紫蓝渐变

AI sparkle everywhere

假 Proposal

假进度

前端直接绕过 ChangeSet 修改数据

重写 Agent Runtime

重写 Production Pipeline

删除 Agent Center

引入大型新 UI framework

为了 Floating Bar 改变 Storyboard 主布局宽度

---

# 36. 建议组件关系

按现有代码调整命名，但结构思想如下：

```text
MainLayout
└── FloatingAgentHost
    ├── FloatingAgentBar
    ├── AgentConversationSurface
    ├── AgentSwitcher
    └── AgentContextChips

Storyboard
├── StorySegment
├── ShotCard
│   └── DirectorContextToolbar
│
├── DirectorProposalOverlay
│   ├── ProposedInsertShot
│   ├── ProposedUpdate
│   ├── ProposedDelete
│   └── ProposedReorder
│
└── ProposalReviewBar
```

Floating Agent 和 Storyboard 不通过复杂 event chain 互相耦合。

通过统一 agent UI store / context API 协作。

---

# 37. 实施顺序

第一步：

只读代码，输出当前：

Agent API

Session 模型

Agent Center

Storyboard

Shot selection

MainLayout

状态管理

的真实结构。

第二步：

实现 Floating Agent UI Store。

第三步：

实现 Floating Agent Bar：

compact

collapsed

drag

position persistence

viewport clamp。

第四步：

接已有 Agent Session / Messages API，实现真实 conversation。

第五步：

增加 Agent switcher 和 context chip。

第六步：

Storyboard 建立 Shot selection。

第七步：

实现 Director Context Toolbar，并让它复用 Floating Agent。

第八步：

建立 Proposal UI component contracts，但不要显示 mock production data。

第九步：

如果仓库已有真实 ChangeSet / Proposal 数据结构，则做 adapter；如果没有，停在清晰 interface boundary，不擅自设计后端协议。

第十步：

运行现有 frontend build / lint / tests。

---

# 38. 验收场景

场景一：

进入 Dashboard。

看到 Floating Agent Bar。

拖到左下。

刷新。

位置仍然合理恢复。

---

场景二：

点击 collapse。

只留下小型 AGNES launcher。

再次点击恢复 compact。

---

场景三：

输入一句普通问题。

Conversation 在 bar 上方/下方展开。

页面不 reflow。

---

场景四：

进入 Storyboard。

Floating Bar 保留。

选中 Shot 02。

Shot 附近出现 Director Context Toolbar。

---

场景五：

点击：

`Ask Director`

Floating Bar 自动：

active agent → Director

context → 当前 Story + Shot

expanded → true。

页面没有出现第二套聊天 UI。

---

场景六：

切换到 Shot 03。

Director contextual state 正确更新。

不会出现多个 Director chat panels。

---

场景七：

拖动 Floating Bar 穿过页面不同位置。

不会：

选中文字

触发 Shot 点击

跑出 viewport。

---

场景八：

窗口 resize。

Bar 自动 clamp。

不会消失到屏幕外。

---

场景九：

关闭 expanded conversation。

Storyboard 不改变尺寸。

重新打开后 Session 仍存在。

---

场景十：

没有真实 Proposal 数据时：

Storyboard 绝不能出现虚假的 AI 建议。

---

# 39. 最终输出

完成后告诉我：

修改了哪些文件；

新增了哪些组件；

Floating Agent 状态模型；

position persistence 实现方式；

现有 Agent Session API 如何复用；

Storyboard context 如何注入；

Director contextual toolbar 如何触发 Floating Agent；

Proposal UI 为下一阶段留下了什么接口；

哪些地方因为后端 `storyboard.propose_change` 尚未接通而刻意没有伪造；

frontend build / tests 结果；

最后给出一次完整手测路径。

不要只说“已完成”。

我要能根据你的说明直接逐项验收。
