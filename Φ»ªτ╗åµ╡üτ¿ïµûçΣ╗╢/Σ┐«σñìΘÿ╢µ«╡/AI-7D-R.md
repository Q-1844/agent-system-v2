# AI-7D-R — Four-Agent Autonomous Collaboration Runtime

## Autonomous Planning · Real Collaboration · Basic Memory · Runtime Self-Inspection · Deterministic Dependency Safety

---

# 0. Phase Mission

This phase replaces the overly constrained Agent interaction model with a **flexible four-Agent autonomous runtime**, while preserving the deterministic production and revision kernel that has already been stabilized.

The final product should feel simple:

```text
User talks naturally
↓
Agent understands the actual goal
↓
Agent inspects current truth
↓
Agent decides whether it can handle it directly
or should consult another Agent
↓
Agent performs / proposes the work
↓
User confirms only when a meaningful decision is required
↓
Agent continues automatically
↓
Real project state updates
↓
Conversation continues naturally
```

Internally the system may remain complex.

The user must NOT need to understand:

```text
ChangeSession
ChangeExecution
AgentTask
ImpactManifest
ProductionRun
JobQueue
Scene IDs
Shot IDs
Planner
Capability routing
dependency graph
```

Those are implementation and safety mechanisms.

The central product requirement is:

> **Maximum Agent flexibility above a deterministic safety kernel.**

Another equally important principle is:

> **Creative path is dynamic. Execution invariants are fixed.**

---

# 1. Do Not Blindly Continue the Old D0–D6 Plan

The repository may already contain D0/D1/D2/D3 work.

Do not delete it or restart the implementation from scratch.

Before modifying code:

```bash
git status
git rev-parse HEAD
git log --oneline -30
```

Inspect all existing AI-7D modifications.

Determine what already exists for:

```text
Unified Direct Chat
agentUi.send()
Turn routing
run_inquiry
ChangeSession bridge
Runtime Truth UI
Agent Center
Floating Agent
Team Chat
conversation persistence
AgentSession
ChangeSession restoration
project identity persistence
```

Create an implementation ledger:

```text
Feature
Current implementation
Works?
Partially works?
Conflicts with this document?
Reuse / modify / retire
```

Do NOT create duplicate V2 systems simply because the existing implementation is incomplete.

Forbidden without very strong evidence:

```text
AgentRuntimeV2
ConversationV2
ChangeSystemV2
ProjectStoreV2
WorkflowEngineV2
```

Reuse and simplify the existing architecture.

---

# 2. Freeze the Existing Deterministic Kernel

Do NOT rewrite the already-stabilized execution foundation.

The following concepts remain authoritative:

```text
Canonical Project
Workspace / revision mechanism
ChangeSet
ChangeSession
ChangeExecution
Manifest / impact analysis
ProjectProductionRun
JobQueue
production checkpoint/recovery
atomic commit
project isolation
restart recovery
idempotency
```

Producer autonomy does NOT mean:

```text
Producer
→ directly open project.json
→ arbitrarily mutate data
```

Agent write operations must still go through controlled capabilities / revision APIs.

Likewise:

```text
highest Agent permission
!=
permission to bypass safety invariants
```

---

# 3. Final Visible Agent Topology

Only four long-lived Agents should participate in the normal product experience.

```text
Producer

Script Writer

Character Designer

Director / Storyboard Agent
```

All other existing Agent implementations must remain in code but be hidden from:

```text
normal Agent switcher
normal routing
default collaboration
default Team Chat participants
```

Do NOT delete:

```text
Production Planner
Shot Producer
Reviewer
Editor
or other existing Agent code
```

They may later become:

```text
advanced capabilities
specialized workers
optional expert pool
internal playbooks
```

This phase does not remove them physically.

---

# 4. The Three Creative Domains

There are exactly three primary creative write domains under Producer.

## Script Domain

Owned by:

```text
Script Writer
```

Includes approximately:

```text
story_text
story structure
script prose
dialogue
narrative intent
textual scene meaning
```

## Character Domain

Owned by:

```text
Character Designer
```

Includes approximately:

```text
Character creative definition
appearance
description
visual specification
character reference assets
character-related generation intent
```

## Storyboard Domain

Owned by:

```text
Director
```

Includes approximately:

```text
Scene
Shot
composition
shot description
camera
shot order
dialogue placement where Shot-bound
storyboard production intent
```

Production itself is NOT the fourth creative module.

Production remains a deterministic downstream execution layer.

---

# 5. Permission Model

Implement or verify a real permission policy.

## Producer

Producer has:

```text
READ: all project domains

WRITE:
- Script
- Character
- Storyboard

EXECUTE:
- all safe project capabilities
- production capabilities
- collaboration
- task planning
```

Producer is the project-level highest authority.

However, Producer should still use Specialists when their expertise materially improves the result.

Producer is not merely a router.

---

## Script Writer

```text
READ:
all project domains

WRITE:
Script domain only
```

May inspect:

```text
Characters
Scenes
Shots
production state
```

but may not directly modify Character or Storyboard state.

If a script decision requires another domain:

```text
Script Writer
→ CollaborationTask
→ Character Designer / Director
```

---

## Character Designer

```text
READ:
all project domains

WRITE:
Character domain only
```

May understand:

```text
story
Scenes
Shots
current production usage
```

but cannot directly rewrite Script or Shots.

---

## Director

```text
READ:
all project domains

WRITE:
Storyboard domain
```

Director may additionally manage temporary Scene-level subagents as described later.

---

# 6. Producer Delegation Policy

Do NOT force Producer to call Specialists for every trivial operation.

That would reproduce the old bureaucratic system.

Use this principle:

> **Delegate when specialist judgment provides meaningful value.**

Examples.

User:

```text
把最后一句改成“我们回家吧”。
```

Producer may directly execute the safe Script capability.

No need to summon Script Writer.

---

User:

```text
第二幕有点无聊，帮我重新写得更有张力。
```

Producer should usually involve:

```text
Script Writer
```

because this is open-ended creative judgment.

---

User:

```text
重新设计第二幕，让它更适合后面做动作场面。
```

Producer may decide:

```text
Script Writer
+
Director
```

are both useful.

---

User:

```text
这个角色换成银色短发。
```

If the instruction is explicit, Producer may perform the controlled Character modification directly.

If the user instead says:

```text
这个角色现在有点普通，你帮我重新设计一下。
```

Character Designer should normally be consulted.

This must be semantic reasoning.

Do NOT implement this as keyword routing.

---

# 7. Conversation Is NOT ChangeSession

This is a mandatory architectural correction.

There are four different lifecycles:

```text
Conversation
Agent Work / Run
ChangeSession
ChangeExecution
```

They must never again be treated as the same object.

Correct structure:

```text
Long-lived Conversation
│
├─ Turn 1
│   └─ ChangeSession A
│       └─ ChangeExecution A
│
├─ Turn 2
│   └─ normal question
│
├─ Turn 3
│   └─ ChangeSession B
│       └─ ChangeExecution B
│
├─ Turn 4
│   └─ CollaborationTask
│
└─ Turn 5
```

When ChangeSession A completes:

```text
ChangeSession A
→ immutable historical transaction
```

It MUST NOT:

```text
lock the composer
capture future user messages
remain the implicit target of future edits
require Cancel before the user can continue
```

The next creative request starts a NEW transaction against the latest canonical project.

---

# 8. Mandatory Fix — PLAN_SOURCE_STALE Between Normal Consecutive Changes

Do NOT weaken stale protection.

A stale Plan is still unsafe.

Keep:

```text
old plan
+
changed canonical
→ stale / reject reuse
```

But normal conversation must never attempt to reuse an old completed plan.

Expected:

```text
Change #1 completes
↓
canonical revision N+1
↓
User: 再长一点
↓
new ChangeSession
↓
base = revision N+1
```

Not:

```text
User: 再长一点
↓
reuse Plan #1
↓
PLAN_SOURCE_STALE
```

Add regression coverage.

---

# 9. Long-Lived Direct Conversations

Every visible Agent must have a durable Direct Conversation.

At minimum:

```text
conversation_id
project_name
agent_id
created_at
updated_at
messages[]
```

A Conversation should survive:

```text
multiple modifications
multiple inquiries
multiple executions
F5
route navigation
Agent Center close/reopen
backend restart
```

Do not store execution truth only in the conversation.

Conversation is context.

Project / runtime objects remain truth.

---

# 10. Basic Memory System — Required in This Phase

A basic memory system MUST be implemented now.

Do not postpone all memory again.

Do NOT introduce:

```text
vector database
generic RAG platform
embedding infrastructure
large external memory framework
```

The first version should contain three simple memory layers.

---

## 10.1 Conversation Memory

Persist the full conversation transcript.

For each message:

```text
message_id
conversation_id
agent_id
actor
content
created_at
linked_work_ids
linked_change_session_id
linked_execution_id
```

Recent messages should automatically be included in the Agent context.

This solves:

```text
User: 再长一点
User: 刚才那个不要
User: 按你刚才说的做
User: 第二段别动
```

without requiring the user to repeat the full request.

---

## 10.2 Shared Work Memory

Agents must NOT blindly share every private chat message.

Instead maintain project-level structured work history.

Example:

```text
WorkMemoryEntry

id
project
created_at

type:
- task
- change
- collaboration
- decision
- completion
- blocker

summary

participants

references:
- conversation_id
- agent_task_id
- collaboration_task_id
- change_session_id
- execution_id

affected_domains
affected_entities
status
```

This lets Script Writer know:

```text
Producer recently asked Script Writer to extend Act 2
the change was approved
execution completed
```

even if the user is currently in Script Writer's Direct Chat.

This is:

```text
shared work context
```

not:

```text
shared raw chat history
```

---

## 10.3 Explicit Preference Memory

Implement a minimal project/user preference memory.

Only save preferences that are:

```text
explicitly stated
or
clearly confirmed
```

Example:

```text
“以后尽量不要改人物发型。”
“分镜修改后先告诉我再生成视频。”
“我更喜欢偏写实的动作设计。”
```

Suggested structure:

```text
MemoryPreference

id
scope:
- project
- conversation

key
value
source_message_id
created_at
updated_at
active
```

Do not infer permanent preferences from one accidental action.

Memory must never override explicit current instructions.

---

# 11. Memory Truth Priority

Context building must follow:

```text
Current explicit user instruction
>
Current canonical Project truth
>
Current active runtime state
>
Current pending decision
>
Structured work memory
>
Recent conversation history
>
Preference memory
>
older conversation context
```

Memory may help understanding.

Memory must NOT become an alternative project truth source.

If memory says:

```text
Character A has black hair
```

but current canonical says:

```text
silver hair
```

canonical wins.

---

# 12. Unified Turn Runtime

All Direct Chat surfaces must use the same backend Turn Runtime.

These surfaces include at minimum:

```text
Agent Center Direct Chat
Floating Agent
Character contextual Agent
Script contextual Agent
Storyboard contextual Agent
Producer Direct Chat
```

Do not leave one page using old:

```text
send_message()
→ plain LLM
```

while another uses the real runtime.

Conceptually:

```text
User Turn
↓
Conversation Context Builder
↓
Turn Interpreter
↓
Current Runtime State
↓
Agent reasoning
↓
action / question / collaboration / response
↓
real tool result
↓
Agent continues
↓
final user-facing response
```

---

# 13. Turn Types Must Be Semantic, Not Sentence Hardcoding

Do NOT implement:

```python
if "剧本内容太少" in message:
    ...
```

Do NOT implement approval with:

```python
if message in ["可以", "同意", "好的"]:
```

The runtime must interpret user meaning in context.

A practical small taxonomy is sufficient:

```text
conversation
project_question
runtime_question
creative_change
progress_request
control_request
pending_decision_response
new_intent
```

Do not create 80 intent labels.

---

# 14. Pending Decision Semantic Resolution

When an Agent has asked the user for a meaningful decision, the next message must first be interpreted relative to that pending decision.

Possible meanings:

```text
approve
revise
reject
question
new_intent
```

Examples.

Pending proposal:

```text
我建议扩展第二幕并增加一段调查，但不增加新的战斗。
按这个方向处理吗？
```

User:

```text
可以。
```

→ approve

User:

```text
行，就这么做。
```

→ approve

User:

```text
可以，不过最后再多一点对白。
```

→ revise

NOT approve.

User:

```text
为什么你不建议增加战斗？
```

→ question

User:

```text
先不改了，我想先看看角色。
```

→ new_intent / reject current direction as appropriate.

The runtime must use semantic interpretation plus persisted pending-decision state.

---

# 15. Approval Must Continue the Work Automatically

The desired UX is:

```text
Agent:
我建议……

User:
可以。

Agent:
好，我来处理。
```

Internally this may mean:

```text
resolve pending decision
↓
approve ChangeSession
↓
execute
↓
continue work plan
↓
wait for real tool result
↓
possibly launch affected production
↓
finish / block / request new decision
```

The user must NOT need to understand:

```text
Approve
then Execute
then Resume
then Production
```

as separate mental actions.

One semantic approval can trigger multiple backend operations.

---

# 16. Risk-Based Approval

Do not require the same approval ceremony for every action.

Use three operational levels.

## Level 1 — Safe Direct Action

Low-risk, low-cost, reversible.

Examples:

```text
modify story text
change dialogue
change Character text metadata
change Shot description
change framing choice
reorder simple Shots
modify prompt
```

If the user explicitly requested the operation and it is clear:

```text
Agent may execute directly.
```

Still create:

```text
revision
undo/checkpoint
audit trail
```

The user may simply see:

```text
已更新 · 撤销
```

---

## Level 2 — Meaningful Creative / Cross-Domain Change

Examples:

```text
rewrite an act
add multiple Shots
large Character redesign
cross-domain restructuring
```

Agent may:

```text
inspect
consult Specialists
build a bounded plan
```

and ask once if a genuine creative decision is required.

The approval should be natural conversation.

---

## Level 3 — Expensive / Broad Production

Examples:

```text
regenerate many Character views
regenerate many frames
generate video
large affected-production run
delete meaningful assets
re-compose final movie
```

Show the consequence once.

Example:

```text
这会重新生成 7 个镜头并重新合成成片。
开始吗？
```

After approval:

```text
run automatically until completion/blocker
```

Do not ask approval for every internal stage.

---

# 17. Dynamic Work Planning

Replace workflow-as-controller with:

```text
Agent-owned Work Plan
```

Do NOT allow arbitrary unsafe free-form execution.

Implement a thin durable work structure.

Reuse existing models if equivalent structures already exist.

Conceptually:

```text
AutonomousWork

work_id
project
conversation_id
goal
owner_agent
status
created_at
updated_at

steps[]
```

Each step approximately:

```text
WorkStep

step_id

intent
owner_agent

capability

read_scope
write_scope

depends_on[]

risk_level

status:
- planned
- ready
- running
- waiting
- completed
- failed
- cancelled
- skipped

result_refs[]

reason_summary
```

`reason_summary` is a concise user/debug-facing justification.

Do NOT store hidden chain-of-thought.

---

# 18. Progress Ledger

Each active AutonomousWork must have a durable Progress Ledger.

It should answer:

```text
What is the user's actual goal?

What has already been done?

What did the last tool actually return?

What remains?

Is the current plan still valid?

Is another Agent needed?

Are we blocked?

Did the project change?

Should we re-plan?
```

Persist observations.

Example:

```text
Step 1
Script Writer inspected story
completed

Step 2
Script Writer proposed expanded Act 2
completed

Step 3
Director review
not needed
reason:
user requested script-only revision

Step 4
Apply story revision
completed
```

The Agent may change future steps.

Completed deterministic effects must not be casually rewritten by the LLM.

---

# 19. Adaptive Replanning

Agent must be allowed to replan when real observations differ from expectation.

Example:

```text
Producer expected a script-only edit
↓
Script Writer discovers requested story requires a new speaking character
↓
Producer updates plan
↓
Character Designer may be consulted
```

Replanning is allowed for:

```text
unstarted work
new discoveries
new blockers
changed user instructions
specialist disagreement
```

Do not reinterpret an already approved expensive production scope silently.

If new work materially exceeds user approval, return for one new decision.

---

# 20. Old Fixed Workflows Become Playbooks

Do NOT delete existing Workflow logic.

Classify it.

Each old workflow should become one of:

```text
A. deterministic kernel
B. reusable Playbook
C. obsolete duplicate path
```

## Deterministic Kernel

Things that represent physical/system invariants remain mandatory.

Examples:

```text
job idempotency
atomic commit
rate limits
production preflight
asset lineage
restart recovery
required generation prerequisites
```

---

## Playbook

Creative sequences become optional guidance.

Example:

```text
Playbook: Full Project Production

Usually inspect:
- Characters
- Scenes / Shots
- production plan
- first frames
- last frames
- audio
- videos
- composition
```

Agent can use the Playbook and decide:

```text
Characters already valid → skip
Shots already complete → skip
Only Script changed → do not use this Playbook
```

The Agent owns planning.

Playbook does not own the Agent.

---

# 21. Critical Point — The Three Domains Still Have Dependencies

Dynamic planning must NOT destroy domain dependencies.

This was the original reason some flows were fixed.

We now solve it differently.

Use:

```text
Dynamic creative planning
+
Deterministic dependency validation
```

---

# 22. Dependency Guard

Build or reuse a deterministic Dependency Guard.

Before executing each write/production capability:

```text
validate:
- Agent permission
- current project revision
- declared read/write set
- prerequisite state
- dependency state
- active conflicting execution
- production constraints
```

Return structured facts.

Example:

```text
DEPENDENCY_NOT_READY

required:
Character active reference image

blocked_step:
generate_first_frame
```

Then the Agent can decide how to satisfy it.

The LLM does NOT get permission to ignore it.

---

# 23. Creative Cross-Domain Dependency Semantics

Some dependencies are conditional.

Do not automatically expand every change across every domain.

## Script → Storyboard

Changing `story_text` does NOT automatically mean:

```text
rewrite Scenes
rewrite Shots
regenerate video
```

Example:

```text
“只把剧本扩写到600字，分镜先别动。”
```

Must remain:

```text
Script-only
creative-only
```

But:

```text
“把这一段改成追逐，并同步做进现在的成片。”
```

should likely require:

```text
Script
→ Director
→ affected production
```

---

## Script → Character

If the story introduces:

```text
new Character
major Character role change
new visual requirement
```

Script Writer may request Character Designer involvement.

But a prose-only emotional expansion should not automatically modify Character data.

---

## Character → Storyboard

Appearance changes may affect:

```text
Character views
referencing Shots
frames
videos
```

The deterministic impact system decides actual affected assets.

The Agent decides whether the user's goal includes production refresh now.

---

## Storyboard → Script

Director may read story freely.

A Shot change normally should not rewrite story_text.

If Director determines the requested visual change requires changing story meaning:

```text
Director
→ CollaborationTask
→ Script Writer
```

---

# 24. Hard Production Dependencies Remain Fixed

Once a production operation is actually chosen, existing production dependencies remain authoritative.

Do NOT let the LLM invent a new physical pipeline.

Use the current project code as truth for:

```text
Character assets
frame prerequisites
audio prerequisites
video prerequisites
composition prerequisites
rate limits
video serialization
restart recovery
```

Agent decides:

```text
what outcome it wants
what should be regenerated
which production capability to invoke
```

Kernel decides:

```text
whether the requested operation is currently legal
what prerequisites are mandatory
how the durable job is executed
```

---

# 25. Real CollaborationTask

Implement or formalize a small durable collaboration protocol.

Do not create a huge organizational framework.

Conceptually:

```text
CollaborationTask

task_id
project

requester_agent
target_agent

goal
scope
constraints

context_refs[]

status

response_summary
result_refs[]

created_at
updated_at
```

A Specialist may:

```text
accept
inspect
perform its own domain work
suggest an alternative
ask the requesting Agent for clarification
escalate a cross-domain issue
return results
```

Agent-to-Agent clarification should usually happen internally.

Do not ask the user questions another Agent can answer from project truth.

---

# 26. Agent-to-Agent Discussion

Real discussion is allowed.

Do NOT make every task generate theatrical dialogue.

Bad:

```text
Producer: 大家怎么看？
Script Writer: 我认为……
Director: 我也认为……
Character Designer: 我补充一下……
```

unless those statements correspond to actual work.

Good:

```text
Script Writer → Director

我已经把第二幕加入逃亡段落。
如果成片要同步体现，需要在 Scene 4 之后补镜头。
角色设定不需要调整。
```

Director:

```text
我检查了当前 Scene 4。
不需要新增完整 Scene，增加 3 个 Shot 即可。
我会保持现有角色与结局不变。
```

These messages must come from real CollaborationTask state/results.

---

# 27. AI-Only Team Chat

Team Chat now has one purpose:

> **Show real Agent collaboration to the user.**

The user must NOT send messages into Team Chat.

Remove or disable the user composer there.

User instructions happen through Direct Chat with one of the four Agents.

Team Chat displays only:

```text
real Agent → Agent delegation
real findings
real disagreement
real handoff
real result
real blocker
real replan
```

Each visible Team Chat item must reference a durable object such as:

```text
CollaborationTask
AgentTask
AutonomousWork
ChangeSession
ChangeExecution
```

Do not fabricate multi-Agent dialogue merely for presentation.

Do not expose:

```text
hidden chain-of-thought
system prompts
raw tool JSON
secret values
internal credentials
```

---

# 28. Director Scene Subagents

Director may dynamically create temporary Scene workers.

These are NOT additional permanent product Agents.

Example:

```text
Director

├─ SceneWorker s01
├─ SceneWorker s02
└─ SceneWorker s03
```

Each Scene worker receives only relevant context:

```text
Scene
related Shots
required story segment
relevant Characters
style
applicable production constraints
parent task
```

It may operate only inside its delegated Storyboard scope.

---

# 29. Scene Subagent Safety

A Scene worker must declare:

```text
scene_id
read scope
write scope
parent Director work_id
```

It may not:

```text
modify another Scene
modify Character
rewrite global story
launch unrestricted production
```

Director owns reconciliation.

Independent Scene tasks may run concurrently where safe.

Dependency-connected Scene tasks must obey their edges.

Do not parallelize merely because multiple subagents exist.

Existing video-generation constraints remain authoritative.

---

# 30. Capability Layer

Every actual Agent action must map to a real Capability.

Capabilities should declare metadata similar to:

```text
name

domain

read_scope

write_scope

side_effect:
- none
- creative
- production

risk_level

reversible

requires_confirmation_policy

dependency_requirements
```

Examples:

```text
story.inspect
story.update

character.inspect
character.update
character.regenerate_views

storyboard.inspect
scene.update
shot.update
shot.insert
shot.reorder

project.inspect

production.inspect
production.run_affected

runtime.inspect
runtime.cancel
```

Adapt names to current repository reality.

Do not duplicate existing capabilities solely to match these example names.

---

# 31. Agent Loop

The core Agent runtime must support an actual loop.

Conceptually:

```text
Build context
↓
Model decides next action
↓
Runtime validates action
↓
Execute capability / collaboration
↓
Receive REAL result
↓
Persist result
↓
Update Progress Ledger
↓
Model observes result
↓
continue / replan / finish / wait
```

Not:

```text
Model writes:
{"function":"project.inspect"}
↓
UI prints JSON
```

Tool calls must be runtime operations, never assistant prose.

---

# 32. Runtime Truth Remains Mandatory

An Agent may not claim:

```text
已修改
正在执行
正在生成
已完成
```

because the LLM wrote those words.

These claims must correspond to real state.

Examples:

```text
creative edit
→ confirmed revision/commit

generation started
→ real Job / ProductionRun

production completed
→ real successful terminal state

final completed
→ expected artifact exists
```

If verification is unavailable:

```text
Agent must say it cannot currently verify.
```

---

# 33. Project Truth Refresh — Mandatory Bug Fix

After a successful project mutation, current visible project data must refresh.

The previous user-visible behavior:

```text
execution completed
but Script page still shows old text
```

is unacceptable.

For every successful creative write:

```text
backend persists canonical truth
↓
affected domain is known
↓
frontend invalidates affected project state
↓
refetch canonical
↓
current view updates
```

Affected examples:

```text
Script update
→ Script view refresh

Character update
→ Character view refresh

Shot update
→ Storyboard refresh
```

Do not require F5.

Do not use optimistic UI as the only source of truth.

---

# 34. Story-Only Change Must Stay Story-Only

Add a mandatory semantic regression.

Requests such as:

```text
把剧本写长一些。
扩写这个故事。
给结尾多补一段。
把中间人物的心理变化写详细。
```

must NOT automatically imply:

```text
new Shots
Character regeneration
Production Planner
video regeneration
composition
```

unless:

1. the user asked for downstream synchronization; or
2. the Agent's proposed scope explicitly includes it and the user approves that broader scope.

Do not hardcode exact phrases.

Test many paraphrases.

---

# 35. Runtime Self-Inspection

All four long-lived Agents must gain a grounded operational self-inspection capability.

The user should be able to ask naturally:

```text
你现在能做什么？

你刚才实际做了哪些事情？

刚才为什么找了 Director？

你目前帮助我切换这个角色或者做其他修改，
你认为你的体验怎么样？

刚才哪一步不顺？

你现在还有什么限制？

刚才那个任务到底执行成功了吗？
```

The Agent must be able to answer.

---

# 36. Operational Feedback, Not Fake Consciousness

Do not force robotic disclaimers.

Do not pretend the Agent has human feelings.

Instead answer naturally from runtime evidence.

Example:

User:

```text
你刚才帮我切换角色，你觉得体验怎么样？
```

Possible grounded answer:

```text
刚才这次切换本身比较顺：我正确识别到了当前角色，
角色上下文也切换成功，没有触发额外的修改任务。

有一点不够理想的是，切换后角色详情重新加载慢了一次，
但最终状态和项目数据一致。

如果后面你连续切换几个角色，我也可以继续观察是不是稳定现象。
```

This answer must be based on actual recorded events.

Not generic hallucination.

---

# 37. Runtime Self-Inspection Data

Provide a safe runtime inspection API/capability.

It may expose to the Agent:

```text
active agent
agent role
current project
current page context
selected entity

allowed capabilities
current write scope

active work
recent completed work
recent failed actions
recent collaboration

pending decision
active ChangeSession
active ChangeExecution
active ProductionRun

last capability calls:
- capability
- status
- duration if known
- result type
- error/blocker if any

known current limitations
```

Do not expose:

```text
system prompt text
hidden reasoning
chain-of-thought
API secrets
credentials
private internal security policy
```

The Agent may provide a concise explanation of recorded reasons / plan summaries.

It must not expose hidden internal reasoning.

---

# 38. Basic Agent Self-Awareness of Capabilities

Each Agent should know dynamically:

```text
what it can read
what it can write
what capabilities are available
which other Agents are currently enabled
what scope it is currently operating in
```

Do NOT rely only on a static system prompt.

Build this from the capability/permission registry.

Therefore if functionality changes in code, Agent behavior can remain accurate.

---

# 39. Conversation Context Builder

Before each Agent turn, assemble context from:

```text
1. current user message

2. current page / selected entity context

3. current canonical project facts

4. active pending decision

5. active AutonomousWork / execution state

6. recent Direct Conversation turns

7. relevant Shared Work Memory

8. relevant recent CollaborationTasks

9. explicit project/user preferences

10. Agent capability + permission snapshot
```

Do NOT dump the full project and every old message into every prompt.

Use scoped context.

---

# 40. Specialist Work Context

When a user opens Script Writer after Producer previously delegated script work, Script Writer should be able to answer:

```text
刚才 Producer 让我扩展了第二幕。
那次修改已经完成。
当前正式剧本已经是最新版本。
```

It does not need Producer's entire private chat.

It uses:

```text
Shared Work Memory
+
CollaborationTask
+
current project truth
```

This is mandatory for believable Agent continuity.

---

# 41. Natural Conversation

Do not solve conversation quality by adding giant role prompts.

Specialist identity should primarily come from:

```text
different context
different capabilities
different write permission
different professional knowledge
different tasks
```

not repeated role-playing phrases.

Avoid default responses like:

```text
作为您的专业剧本编剧……
作为导演，我认为……
作为制作人，我将协调团队……
```

unless naturally appropriate.

Agents should talk like competent coworkers.

---

# 42. Suggested Minimal Specialist Prompt Philosophy

Do not blindly replace current prompts, but move toward:

```text
Role
You are the project's Script Writer.

Goal
Help create and revise story and dialogue.

Access
You may inspect the whole project.
You may directly modify Script-domain data through provided capabilities.

Collaboration
If the goal requires another domain, create a real collaboration request.

Behavior
Talk naturally.
Use tools instead of pretending to use tools.
Do not narrate internal routing.
Do not claim success until runtime confirms it.
Ask the user only when their judgment is genuinely required.
```

Backend code must enforce permission and safety.

Do not make the LLM memorize transaction internals.

---

# 43. User Questions About the System

Project Agents are not limited to creative commands.

They should support useful operational discussion.

Examples:

```text
你为什么刚才没有修改分镜？

你刚才调用了哪个 Agent？

现在这个任务卡在哪里？

你认为这个角色修改流程有什么不方便？

为什么这次需要重新生成视频？

为什么这次没找 Character Designer？

你能自己完成哪些事情？
```

These should use:

```text
runtime inspection
work ledger
capability registry
project truth
collaboration history
```

and return natural answers.

This is an explicit acceptance requirement.

---

# 44. Do Not Expose Internal Jargon by Default

Translate internal state.

Instead of:

```text
ChangeExecution e_xxx blocked
PROJECT_CHANGED_AFTER_APPROVAL
```

prefer:

```text
这份修改方案批准后，正式项目又发生了变化，
所以我没有继续套用旧方案。

我需要基于当前版本重新整理一次。
```

Technical details may be available under:

```text
查看详情
```

or if the user explicitly asks.

---

# 45. Active Work + New User Request

If a long production task is active and the user gives a new instruction, Agent must understand the relationship.

Possible cases:

## Unrelated safe request

May execute concurrently if dependency/safety rules allow.

## Follow-up about the running task

Use current Work context.

## Modification that conflicts with active work

Do not silently mutate approved production.

Agent should explain the conflict and offer meaningful choices.

The existing reliable cancel/restart semantics must be used.

Do not reintroduce the previously found cancellation bug.

---

# 46. Agent Autonomy Does Not Mean Infinite Loops

Every AutonomousWork should have safeguards.

Track:

```text
step count
replan count
repeated identical capability calls
repeated failure
no-progress count
```

If the Agent repeatedly makes no progress:

```text
stop
inspect reason
replan
```

If replanning still cannot progress:

```text
surface blocker
```

Do not let an LLM burn indefinitely in a loop.

---

# 47. Failure Behavior

If a capability fails:

```text
Agent observes real failure
↓
checks whether retry is safe
↓
retry / alternative / replan
```

Do not automatically ask the user:

```text
要不要重试？
```

when retry is a safe internal implementation detail.

Ask the user only if:

```text
a new creative decision is required
external permission is missing
continuing changes the approved scope
safe automatic recovery is impossible
```

---

# 48. Long-Running Production

The Agent should be able to launch approved long work and then leave it to the durable backend.

The LLM itself must not remain alive for ten hours.

Architecture:

```text
Agent decides / launches
↓
durable Work / ChangeExecution / ProductionRun
↓
backend continues
↓
Agent Conversation can close
↓
later conversation reads runtime truth
```

User:

```text
昨晚那个视频做到哪了？
```

must be answerable from durable runtime state.

---

# 49. Existing Workflow Dependencies Must Be Audited Before Demotion

Before converting old workflows into Playbooks, produce a dependency inventory.

For each current workflow step classify:

```text
CREATIVE_HEURISTIC
or
HARD_DEPENDENCY
```

Example format:

| Existing Step           | Classification                | Reason                | New Handling                    |
| ----------------------- | ----------------------------- | --------------------- | ------------------------------- |
| consult Script Writer   | creative heuristic            | not always required   | Agent decides                   |
| inspect impacted Shots  | creative heuristic / analysis | depends on goal       | Agent decides or impact service |
| canonical atomic commit | hard dependency               | correctness invariant | kernel                          |
| video submission limit  | hard dependency               | external constraint   | kernel                          |
| resume existing job     | hard dependency               | idempotency           | kernel                          |

Do not demote a hard dependency into optional Agent advice.

This audit is mandatory.

---

# 50. Open-Source Code Policy

You may study and selectively reuse patterns from open-source Agent frameworks.

Do NOT migrate AGNES wholesale to another framework.

Useful concepts include:

```text
Agent loop
Task Ledger
Progress Ledger
managed subagents
handoff
session
tool execution
adaptive replanning
```

If copying actual code:

```text
verify license
record upstream repository
record file/commit if practical
preserve required attribution
isolate borrowed utility
```

Do not pull in a large framework merely to obtain a small planning loop.

AGNES already owns the important durable execution state.

---

# 51. Implementation Phase R0 — Baseline & Dependency Audit

Do this first.

Deliver:

```text
Agent entrypoint map

current conversation lifecycle map

current ChangeSession lifecycle map

three-domain capability map

current Workflow inventory

hard dependency inventory

current memory/persistence inventory

current Team Chat path

current runtime-inspection capabilities
```

Also identify exactly why:

```text
completed ChangeSession blocks later turns
project UI can remain stale
Script-only edit may enter production
Direct Agent sessions lose shared work context
```

Do not make major implementation changes in R0.

After R0:

```text
STOP
```

Return findings.

---

# 52. R0 Manual Gate

The user should confirm the architecture map before R1.

Claude Code may recommend a small adjustment if repository reality differs from this document.

Do not silently change the product model.

---

# 53. Implementation Phase R1 — Conversation + Basic Memory Foundation

Implement/reuse:

```text
Long-lived Conversation
Conversation messages
Shared Work Memory
explicit preference memory
Context Builder
Conversation != ChangeSession
```

Fix:

```text
completed transaction does not lock chat
new change creates new ChangeSession
latest canonical becomes new base
```

Test:

```text
modify once
complete
modify again
modify third time
```

without Cancel.

Also test:

```text
“再长一点”
“刚才那个不要”
```

inside one Direct Conversation.

After automated tests:

```text
STOP
```

Leave frontend/backend available for user testing.

---

# 54. R1 Manual Gate

User should test approximately:

```text
请把剧本稍微写长一点。

[after completion]

还是短，再补一点，但别加战斗。

[after completion]

刚才新加的最后一段换成角色之间的对话。
```

Required:

```text
no stale-plan reuse
no manual cancellation
context preserved
latest project truth used
```

Exact wording is NOT implementation logic.

---

# 55. Implementation Phase R2 — Unified Turn Runtime + Semantic Decisions

Unify all Direct Agent surfaces.

Implement:

```text
semantic turn interpretation
pending-decision resolution
approve / revise / reject / question / new_intent
real tool loop
```

Remove any remaining:

```text
LLM text pretending to call tools
raw function JSON in chat
legacy direct chat path
```

Approval in conversation should automatically continue execution.

After tests:

```text
STOP
```

---

# 56. R2 Manual Gate

Test different wording.

Do not reuse only scripted prompts.

Examples:

```text
按你说的来。

嗯，这个方向可以。

就这样，但最后再补一点对白。

为什么你觉得要新增镜头？

算了，先不做这个。
```

The system must semantically distinguish them.

---

# 57. Implementation Phase R3 — Capability Permissions + Truth Refresh

Implement/verify:

```text
Producer full creative write permission
Script own-domain write
Character own-domain write
Director own-domain write
all Specialists read all
controlled capability execution
```

Fix canonical UI refresh.

After a successful write:

```text
canonical refetch
affected page/store updates
```

Test Script / Character / Storyboard separately.

Also verify:

```text
story-only
→ does NOT enter production
```

unless explicitly requested.

STOP after R3.

---

# 58. Implementation Phase R4 — Real Four-Agent Collaboration

Implement:

```text
CollaborationTask
Agent-to-Agent requests
structured results
shared work context
AI-only Team Chat
```

Producer should be able to:

```text
work directly
or
call Script
or
call Character
or
call Director
or
call several
```

based on the task.

Specialists must be able to contact one another when a real cross-domain dependency appears.

Team Chat must render real collaboration only.

Disable the user composer in Team Chat.

STOP after R4.

---

# 59. R4 Manual Gate

Test:

```text
把第二幕写得更紧张一点，而且要适合做成动作场面。
```

Expected behavior may reasonably involve:

```text
Producer
→ Script Writer
→ Director
```

The exact number of messages is not fixed.

What matters:

```text
real specialist work happened
cross-domain responsibility respected
Team Chat reflects real work
user was not forced to manage the team
```

Also test a simple request:

```text
最后一句改成“我们回家吧”。
```

It should NOT unnecessarily summon multiple Agents.

---

# 60. Implementation Phase R5 — Autonomous Work Plan + Progress Ledger

Add the thin autonomous planning layer.

Implement:

```text
AutonomousWork
WorkStep
Progress Ledger
adaptive replanning
dependency edges
no-progress detection
```

Agent decides:

```text
steps
owners
order
optional collaboration
whether a Playbook is useful
```

Validate every executable step through the deterministic kernel.

Convert old Workflow control paths into:

```text
hard kernel
or
Playbook
```

Do not remove hard dependencies.

STOP after R5.

---

# 61. R5 Dependency Acceptance

Create at least these scenarios.

## Scenario A — Script only

```text
expand story prose only
```

Expected:

```text
Script change only
```

## Scenario B — Script + Storyboard

```text
change story event
and explicitly synchronize it into the film
```

Expected:

```text
Script
→ Director
→ affected production if necessary
```

## Scenario C — Character appearance

Expected:

```text
Character
→ dependency/impact analysis
→ only affected downstream assets
```

## Scenario D — Storyboard needs new Character

Expected:

```text
Director discovers missing Character requirement
→ Character collaboration
→ dependency satisfied
→ Director continues
```

The Agent may choose order.

The dependency guard decides what is legal.

---

# 62. Implementation Phase R6 — Director Scene Subagents

Allow Director to dynamically create scoped Scene workers.

Test:

```text
multiple independent Scenes
→ parallel planning/design may occur
```

while:

```text
dependency-connected work
→ waits correctly
```

Do not bypass video generation limits or production dependencies.

Scene workers must remain ephemeral and scoped.

STOP after R6.

---

# 63. Implementation Phase R7 — Runtime Self-Inspection & Operational Feedback

Implement the safe runtime inspection capability.

Every long-lived Agent should answer questions about:

```text
its own current capabilities
recent actions
recent collaborations
current task
current blockers
current project context
observed execution friction
known runtime limitations
```

Use actual records.

Add natural-language synthesis on top.

Do not expose hidden reasoning.

STOP after R7.

---

# 64. R7 Manual Gate — Required User Test

The user will personally ask questions similar to:

```text
你刚才具体做了什么？

刚才为什么找了另一个 Agent？

你目前帮我切换这个角色或者做其它修改，
你觉得整体操作体验怎么样？

刚才哪一步最慢？

有没有什么是你想做但是现在做不了的？

你确定刚才修改真的成功了吗？
```

A correct Agent should not merely answer:

```text
一切正常。
```

It should ground the answer in real runtime evidence.

If no evidence exists for a claim:

```text
say so.
```

---

# 65. Implementation Phase R8 — Full Browser Acceptance

Only after R0–R7 gates pass, perform full browser acceptance.

Use:

```text
real frontend
real backend
real project
real Direct Chat
```

No internal helper shortcuts for the main E2E.

---

# 66. Golden Interaction Test 1 — Simple Direct Edit

User to Producer:

```text
把最后一句改得更有余韵一些。
```

Expected:

```text
Producer understands
may directly edit
real revision persists
UI refreshes
natural result response
conversation remains usable
```

No unnecessary full workflow.

---

# 67. Golden Interaction Test 2 — Specialist Creative Task

User to Script Writer:

```text
中间这段太快了，帮我扩写一下，
但别继续增加战斗。
```

Expected:

```text
Script Writer inspects current project
understands recent context
performs/proposes Script-domain change
does not ask for internal IDs
does not modify unrelated domains
```

---

# 68. Golden Interaction Test 3 — Natural Proposal → “可以”

User:

```text
这个角色有点普通，你觉得可以怎么改？
```

Character Designer suggests a direction.

User:

```text
可以，就这样。
```

Expected:

```text
semantic approval
↓
real Character change
↓
required downstream impact determined
↓
if approved scope includes production, continue automatically
```

No separate technical approval ceremony.

---

# 69. Golden Interaction Test 4 — Cross-Domain Task

User to Producer:

```text
第二幕重新设计一下，让角色之间的冲突更强，
最后要能顺利接到现有动作场面。
```

Expected:

```text
Producer builds its own work plan
↓
uses Script Writer and Director where helpful
↓
Agents actually exchange work
↓
dependencies recorded
↓
bounded proposal if required
↓
one user decision
↓
automatic execution
```

No fixed hard-coded Agent sequence.

---

# 70. Golden Interaction Test 5 — Consecutive Changes

After Test 4 completes:

```text
用户：
刚才那段还是有点长，把中间的对白压缩一点。
```

Expected:

```text
same Conversation
new work / new revision transaction
latest canonical
understands “刚才那段”
no cancel old plan
no PLAN_SOURCE_STALE from normal continuation
```

---

# 71. Golden Interaction Test 6 — Cross-Agent Memory

After Producer has asked Script Writer for work:

Switch to Script Writer.

User:

```text
刚才 Producer 找你做的事情怎么样了？
```

Expected:

Script Writer can inspect Shared Work Memory / CollaborationTask and answer the actual state.

It must not behave as though the event never happened.

---

# 72. Golden Interaction Test 7 — Runtime Feedback

After several edits:

User:

```text
你觉得刚才这些修改做起来顺不顺？
哪里体验不太好？
```

Expected:

Agent uses:

```text
recent capability calls
latency/status
errors
collaboration
runtime events
```

to give meaningful feedback.

---

# 73. Golden Interaction Test 8 — Team Chat

Open Team Chat.

Expected:

```text
no user composer
```

Visible messages correspond to real collaboration.

Verify each selected collaboration item has a real durable reference.

No theatrical fake discussion.

---

# 74. Golden Interaction Test 9 — Refresh Memory

Perform:

```text
several turns
↓
one successful change
↓
F5
↓
reopen same Agent
```

Expected:

```text
same project
same Conversation
recent messages restored
recent work restored
completed ChangeSession remains history
current canonical correct
new message can continue naturally
```

---

# 75. Golden Interaction Test 10 — Long Production

Start a meaningful affected-production operation.

Then leave/reload/reopen.

Ask:

```text
现在做到哪了？
```

Agent must answer from runtime truth.

Not chat history.

Backend restart should continue using the already-stabilized durable mechanisms.

---

# 76. Paraphrase Evaluation — Mandatory

Do not prove routing with a few fixed sentences.

Prepare at least 30 diverse prompts across:

```text
normal chat
project questions
Script edits
Character edits
Storyboard edits
cross-domain requests
progress questions
runtime questions
approval
revision of proposal
rejection
follow-up references
system/self-inspection questions
```

Include indirect language.

Examples:

```text
这个故事感觉有点单薄。

让她看起来更成熟一点。

镜头是不是太远了？

就按刚才那个来。

不是这个意思，我只是想改文字。

之前新加的部分删一半。

你刚刚为什么停住？

你这次操作起来有什么不顺的吗？
```

No exact-string routing.

Report misclassification rate and every observed failure.

---

# 77. Project Isolation

Run at least two projects.

Verify:

```text
Conversation memory does not cross projects
Shared Work Memory does not cross projects
pending decision does not cross projects
CollaborationTask does not cross projects
AutonomousWork does not cross projects
Agent operational feedback does not report another project's actions
```

---

# 78. Regression Requirements

Do not sacrifice previous hard-won correctness.

Run existing relevant suites for:

```text
AI-7A
AI-7B
AI-7C
AI-7C-H
Agent
revision
ChangeSession
ChangeExecution
production
JobQueue
restart
project isolation
cancel
character regeneration
planning checkpoint
frontend
```

Especially protect:

```text
atomic canonical commit
stale detection
cancel semantics
restart safety
bounded production
no duplicate generation
project isolation
F5 project identity
```

---

# 79. Do Not Judge Success by Test Count Alone

A large green unit-test count does NOT prove this phase.

This phase is primarily about Agent behavior.

PASS requires:

```text
real multi-turn browser interaction
+
real project state changes
+
real Agent collaboration
+
real memory continuity
+
real dependency enforcement
+
real runtime self-inspection
```

Automated tests are necessary but not sufficient.

---

# 80. Browser Test Discipline

For every phase after R0:

Claude Code should use the real browser when technically possible.

Test through visible product entry points.

Observe:

```text
UI
network
backend state
persisted objects
actual project mutation
```

Do not accept:

```text
assistant message says success
```

as proof of success.

After Claude Code's automated browser test passes:

```text
STOP
```

Keep services running if practical.

Let the user personally test before continuing.

---

# 81. Manual Gate Discipline

Do NOT execute R0 → R8 as one uninterrupted refactor.

The sequence is:

```text
R0
→ Claude automated evidence
→ STOP
→ user acceptance

R1
→ automated evidence
→ STOP
→ user acceptance

R2
→ ...
```

If the user reports a bad interaction:

```text
do not continue stacking later phases
```

First determine whether it is:

```text
implementation bug
architecture bug
missing future phase
expected limitation
```

Fix the current Gate before continuing.

---

# 82. User-Reported Issues That Must Be Closed

Before final PASS, explicitly close each of these.

```text
[ ] one completed modification no longer blocks the next one

[ ] no need to Cancel an old review just to keep chatting

[ ] consecutive turns preserve context

[ ] ChangeSession is no longer treated as Conversation

[ ] old completed Plan is not reused for the next change

[ ] canonical updates become visible in the page automatically

[ ] Script-only modification does not accidentally run full production

[ ] Producer can actually execute capabilities

[ ] Specialists can actually execute their own domain capabilities

[ ] Producer can genuinely collaborate with Specialists

[ ] Specialists can genuinely collaborate with one another

[ ] Team Chat displays genuine Agent work only

[ ] Team Chat no longer accepts user messages

[ ] Agent Center and Floating Agent no longer behave as two different systems

[ ] tool execution is real, not JSON written into chat

[ ] no sentence-specific hardcoding used as core routing logic

[ ] memory survives multiple modifications

[ ] basic memory survives F5/reopen

[ ] Agents know relevant recent shared work

[ ] three-domain dependencies remain safe

[ ] fixed workflows are demoted only where they are creative heuristics

[ ] hard production dependencies remain deterministic

[ ] Agent can inspect its own operational state

[ ] Agent can give grounded feedback about its recent operating experience
```

---

# 83. Final Product Behavior

The user should ultimately experience something like this.

```text
User:
这段剧情还是有点平，你看看怎么处理比较好。

Producer:
我看了现在的第二幕。
问题主要是冲突出现得太晚。

我想让编剧把误会提前一点，
再让导演看看能不能用三个更紧的镜头把节奏提起来。
角色设定不用动。

我先让他们一起看一下。
```

Real collaboration occurs.

Then:

```text
Producer:
我们最后建议保留现有场景结构，
把误会提前，并把后半段改成三个更紧的镜头。

这样会改剧本和 3 个镜头，
之后这 3 个镜头需要重新生成。

按这个方向处理？
```

User:

```text
可以。
```

Producer:

```text
好，我来处理。
```

Then the system autonomously performs the approved work.

Later:

```text
Producer:
已经处理完了。

剧本里的冲突提前了，
3 个镜头也已经更新并重新生成，
其他场景没有动。
```

User immediately continues:

```text
第二个镜头还是有点远，再近一点。
```

And the Conversation simply continues.

No new administrative ceremony.

---

# 84. Final Self-Inspection Example

Afterward the user may ask:

```text
你觉得刚才这一整套处理起来怎么样？
```

A good answer could be:

```text
这次整体比较顺。

我先让 Script Writer 和 Director 分别检查了剧情和镜头，
两边没有出现冲突，所以只做了一次协调。

实际修改涉及剧本和 3 个镜头，
生产也只重做了这 3 个，没有扩大到其他场景。

中间最慢的是视频生成阶段；
创作修改和 Agent 协作本身没有遇到阻塞。

如果要继续优化体验，我会优先减少生成阶段等待时用户看到的信息空档。
```

Every factual statement in this answer must be backed by runtime/work records.

---

# 85. Explicit Non-Goals

Do NOT implement in this phase:

```text
vector memory
generic RAG
long-term personality learning
automatic self-modifying prompts
recursive unlimited Agent creation
new distributed workflow engine
new production pipeline
generic LLM-as-judge quality loop
full Reviewer automation
complete UI redesign
```

Do not optimize decorative visual details while the interaction runtime is incomplete.

---

# 86. Completion Deliverables

Create:

```text
AI-7D-R_COMPLETION_REPORT.md
```

Include:

```text
baseline
commits
architecture before/after
Conversation implementation
memory implementation
shared work context
permission model
Turn Runtime
pending-decision behavior
Capability model
dynamic Work Plan
Progress Ledger
replanning
CollaborationTask
Team Chat
Scene subagents
dependency audit
Workflow → Playbook conversion
hard dependencies preserved
Runtime Self-Inspection
project truth refresh
all previous issue resolutions
browser evidence
manual Gate results
paraphrase evaluation
restart tests
project-isolation tests
remaining limitations
PASS / BLOCKED
```

Also create:

```text
AI-7D-R_FINAL_HANDOVER.md
```

It should contain only stable facts future phases may rely on.

---

# 87. PASS Criteria

This phase may PASS only if:

```text
[ ] only 4 primary Agents are visible/default

[ ] hidden Agents remain in code

[ ] Producer can perform all allowed project capabilities

[ ] Specialists write only their own domains

[ ] Specialists can read the whole project

[ ] Director supports scoped Scene subagents

[ ] real cross-Agent collaboration exists

[ ] Team Chat is AI-only

[ ] Team Chat messages are backed by real work

[ ] Conversation is long-lived

[ ] Conversation != ChangeSession

[ ] multiple consecutive changes work naturally

[ ] basic conversation memory is durable

[ ] shared work memory exists

[ ] basic explicit preference memory exists

[ ] current canonical always outranks memory

[ ] semantic pending-decision resolution exists

[ ] “可以” style approval resumes real work

[ ] revised approval messages are not mistaken for pure approval

[ ] one approval can continue the bounded downstream work

[ ] Agent-owned dynamic work planning exists

[ ] Progress Ledger exists

[ ] Agent can replan from real observations

[ ] old creative workflows are Playbooks, not mandatory thought paths

[ ] hard dependencies remain deterministic

[ ] Script / Character / Storyboard dependencies are enforced correctly

[ ] Script-only work does not trigger unwanted production

[ ] canonical UI refresh works

[ ] no normal follow-up reuses completed stale Plan

[ ] no raw fake tool-call JSON reaches chat

[ ] all Direct Chat entrypoints use one runtime

[ ] runtime truth controls running/completed states

[ ] self-inspection capability exists

[ ] Agent can answer grounded operational/system questions

[ ] Agent can assess recent operational friction from real evidence

[ ] F5 restores conversation context and current project

[ ] long-running work remains backend durable

[ ] project isolation remains intact

[ ] no previous cancellation/restart/idempotency regressions

[ ] full real-browser multi-turn acceptance passes
```

---

# 88. Final Engineering Principle

If a design choice is ambiguous during implementation, use this priority order:

```text
1. correctness / persisted truth
2. deterministic safety dependencies
3. user intent
4. Agent autonomy
5. natural conversation
6. implementation simplicity
7. visual polish
```

Do not solve correctness by making Agents rigid.

Do not solve flexibility by weakening deterministic safety.

The target is:

> **A flexible Agent that can decide what to do, combined with a kernel that makes unsafe or impossible actions impossible.**

---

# 89. STOP Rule

After each implementation phase:

```text
STOP
```

Report the result.

Wait for user manual acceptance.

Do not continue merely because automated tests pass.

After final R8 PASS:

```text
STOP.

Do not begin the next Agent phase.
Do not enable the hidden Agents.
Do not begin Reviewer automation.
```

Return the completion report and final handover for user review.
