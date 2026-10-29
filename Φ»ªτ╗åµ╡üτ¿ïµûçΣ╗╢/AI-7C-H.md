# AI-7C-H — Truthful Agent Autonomy & Post-Freeze Production Hardening

## Runtime Truth → Bounded Autonomy → Minimal Clarification → Production Recovery → Full Natural-Language Golden Path

---

# 0. Your Role

You are the **primary implementation agent** for this phase.

Use the strongest available GLM-5.3 reasoning mode.

You are no longer acting as the read-only independent auditor from AI-7C-S.

You may modify production code, add tests, run the real frontend/backend, use the built-in browser, perform restart/soak testing, and when necessary run real Agnes acceptance.

However:

**Do not begin AI-7D.**

This phase is a narrow post-freeze hardening phase named:

```text
AI-7C-H
```

Its purpose is to make the already-working Agent execution backbone:

1. recover correctly from the remaining known crash windows;
2. faithfully regenerate affected Character assets;
3. survive real full-page refresh;
4. avoid duplicate Planner work after restart;
5. make Agent/user interaction reflect real runtime truth;
6. substantially reduce unnecessary user clarification;
7. prove one complete natural-language → final.mp4 browser path.

AI-7C-S remains frozen.

Do not rewrite it.

---

# 1. Establish the Real Baseline First

Before changing anything:

```text
git status
git rev-parse HEAD
git log --oneline -20
```

Record:

* current branch;
* current HEAD;
* all dirty/untracked files;
* which files belong to the user;
* any remaining `normalize_module` WIP;
* running frontend/backend processes if any.

Do not:

```text
git reset --hard
git clean -fd
git checkout .
git restore .
git stash
```

against unknown user work.

Do not silently include unrelated WIP in your commits.

Read the actual current implementation before trusting any old execution document.

At minimum inspect the current equivalents of:

```text
src/agents/**
src/revision/**
routers/agents.py

execution_service.py
character_views_handler.py
change_analysis.py
inquiry.py

src/store/changeSession.ts
src/store/agentUi.ts
src/store/project*

TeamChatPanel
ChangePlanReview.vue

src/components/agent/
    FloatingAgentHost.vue
    FloatingAgentBar.vue
    AgentConversationSurface.vue
    AgentSwitcher.vue
    AgentContextChips.vue

src/views/agents/index.vue
src/api/index.ts

ProjectProductionRun
JobQueue
production planner
composition / final output path
```

Use current code as authority.

---

# 2. Locked Architecture

Do not replace the architecture that AI-7A / 7B / 7C / 7C-S already proved.

The following remain locked concepts:

```text
Agent
    = reasoning / decision layer

ChangeSession
    = durable proposal lifecycle

ChangeExecution
    = durable execution lifecycle

Workspace / ChangeSet
    = controlled creative modification

Canonical Project
    = authoritative creative state

Manifest
    = deterministic production impact

JobQueue / ProductionRun
    = production execution truth
```

Preserve:

```text
proposal lifecycle != execution lifecycle
```

Preserve:

```text
approved plan
→ deterministic execution
```

Do not let Agents reinterpret an approved plan during production.

Do not create:

```text
AgentRuntimeV2
ChangeSystemV2
new workflow engine
new realtime bus
another production runtime
generic rebase framework
```

Persistence remains truth.

Polling/event streams are only delivery mechanisms.

---

# 3. The Central Product Rule of This Phase

The most important rule is:

> **Agent language is never execution truth.**

An LLM saying:

```text
“我已经开始修改。”
“正在重新生成。”
“视频已经完成。”
```

must never be sufficient for the UI to display those states.

The product must distinguish two layers.

## Layer A — Conversation

Human-readable Agent discussion.

Examples:

```text
“我理解你的要求。”
“这个变化会影响角色参考图和第 4 场的 3 个镜头。”
“我需要你确认一个创作选择。”
```

## Layer B — Runtime Truth

Deterministic state rendered from persisted backend objects.

Examples:

```text
ChangeSession.status
ChangeExecution.status
ChangeExecution.stage
AgentTask.status
Job.status
ProductionRun.status
artifact existence / persisted Asset state
```

Only Layer B may cause the UI to show:

```text
正在分析
正在生成
正在执行
等待确认
已阻塞
失败
已完成
```

Never derive those states from LLM prose.

---

# 4. No More “Hallucinated Running”

Audit every Agent interaction entry point, especially:

```text
Floating Agent Bar
Direct Agent Chat
Team Chat
Producer
Specialist direct conversations
```

Find every path where an assistant message can imply that work has started.

A visible state such as:

```text
正在执行
正在分析
已开始修改
正在生成
```

may appear only after a corresponding durable object has actually been created and persisted.

Examples:

```text
POST message
↓
server creates ChangeSession cs_xxx
↓
ChangeSession persisted with status=routing
↓
response returns session_id
↓
frontend may now show “正在理解修改要求”
```

Not:

```text
POST message
↓
LLM replies “我开始处理了”
↓
frontend sets running=true
```

The second pattern is forbidden.

Audit `agentUi.ts`.

A generic frontend boolean such as:

```text
running = true
```

must not become an independent execution truth source.

It may be derived from authoritative backend state, but must not invent it.

---

# 5. Agent Message Routing Must Match User Intent

The Floating Agent Bar is now a real product entry point.

It must not behave like a generic chatbot disconnected from the AI-7A/7B/7C machinery.

A user message should be classified into a small number of meaningful paths.

Do not create a giant intent taxonomy.

Conceptually:

```text
A. conversational/read-only question
B. creative change request
C. progress/control request
D. genuinely ambiguous decision
```

## A. Read-only question

Example:

```text
“这个角色现在在哪些场景出现？”
```

Expected:

```text
TeamInquiry / existing read-only investigation
→ automatic inspection
→ grounded answer
```

No approval.

No fake ChangeExecution.

No production.

---

## B. Creative change request

Example:

```text
“把赤焰守卫改成银色短发和黑色装甲，
同时让它第一次进入机库的场景更有压迫感。”
```

Expected:

```text
user message
↓
durable ChangeSession
↓
routing
↓
specialist consultation where needed
↓
dependency analysis
↓
plan synthesis
↓
validation
↓
preview
↓
ready_for_review
```

The user should not manually decide:

```text
which Agent to call
which capability to invoke
which Scene IDs to send
which model to use
which image mode to use
which generation parameters to use
```

The Producer/runtime owns orchestration.

The user supplies creative intent.

---

# 6. Default to Bounded Autonomy

AGNES should behave like an autonomous production assistant inside a safe project boundary.

Within an already-authorized project, automatically perform low-risk/reversible analysis steps:

```text
inspect project
inspect history
route request
consult specialists
analyze dependencies
compile plan
validate plan
build preview
calculate impact
restore persisted state
poll jobs
continue approved production stages
```

Do not ask the user:

```text
“要不要分析一下？”
“是否查看受影响镜头？”
“是否让 Character Agent 检查？”
“是否生成制作计划？”
“要不要继续？”
```

when the next step is already implied by the user's goal and is safe.

Those questions destroy autonomy.

---

# 7. One Meaningful Approval, Not Ten Small Approvals

For a normal creative modification flow, the desired interaction is:

```text
User gives goal
↓
AGNES investigates automatically
↓
AGNES prepares one bounded proposal
↓
User reviews meaningful creative consequences
↓
[批准并执行]
↓
AGNES executes the approved plan to completion
```

After:

```text
approved_for_execution
```

and the user chooses:

```text
批准并执行
```

AGNES should not repeatedly ask:

```text
Generate Character views?
Create Production Plan?
Generate frames?
Generate video?
Compose final video?
```

Those are implementation steps of the already-approved production consequence.

They should proceed automatically through the existing deterministic pipeline.

Pause only for an actual blocker or a genuinely new user decision.

---

# 8. Clarification Policy

This phase must explicitly reduce unnecessary clarification.

The Agent may ask the user only when at least one of the following is true:

### Case 1 — Material creative ambiguity

Two or more plausible interpretations would produce meaningfully different creative results.

Example:

```text
“换成之前那个造型。”
```

and there are several plausible old versions.

### Case 2 — Required information cannot be derived

The authoritative project state does not contain a required fact and there is no safe/default interpretation.

### Case 3 — Conflicting specialist decisions

Two valid proposals conflict and deterministic project rules cannot resolve them.

### Case 4 — Consequential action outside existing approval scope

The requested action exceeds the current approved plan or project permission boundary.

### Case 5 — External/manual blocker

Credential, authentication, unavailable service, missing required source, etc.

---

# 9. What Must NOT Trigger Clarification

Do not ask the user for information the system already knows or can determine.

Forbidden examples:

```text
“What is the Scene ID?”
“What Shot should I inspect?”
“What model should I use?”
“What resolution?”
“What size_tier?”
“Use text2img or img2img?”
“Should I regenerate the affected Shot?”
“Which Agent should handle this?”
“Should I check dependencies?”
```

Do not expose internal implementation choices.

Use:

```text
current page context
selected Character / Scene / Shot
project state
AgentContext
project style
project aspect ratio
current active assets
dependency graph
existing defaults
approved production plan
```

to resolve them automatically.

---

# 10. Consolidate Clarification

Never produce parameter interrogation:

```text
Agent: Which Scene?
User: Scene 3.

Agent: Which Character?
User: Red Guard.

Agent: Should I update reference images?
User: Yes.

Agent: Should affected videos regenerate?
...
```

Instead, gather uncertainty first.

If user input is genuinely required, create **one Decision Request**.

Conceptually:

```text
需要你确认 1 件事

“旧版造型”可能指：

○ 昨天的银色短发版本   [推荐]
○ 更早的长发版本

[使用推荐] [选择其他版本]
```

If several closely related decisions exist, show them in the same review surface.

Do not ask one question per model turn.

---

# 11. Durable Waiting-for-User State

“等待用户” must be a real backend condition.

Reuse or extend the current durable state model.

Do not represent it only as chat text.

Expected behavior:

```text
session.status = awaiting_user_decision
decision object persisted
↓
browser closes
↓
browser reopens
↓
same Decision Request restored
↓
user chooses
↓
same session resumes
```

Do not restart the entire analysis unless stale project state requires it.

---

# 12. Runtime Status UX

The conversation surface should show a compact structured run card/timeline.

Do not dump internal logs into chat.

Suggested user-facing proposal stages:

```text
理解你的要求
检查当前项目
协调相关专业 Agent
分析影响
整理修改方案
校验方案
准备预览
等待你确认
```

These labels map to real persisted ChangeSession states.

Suggested execution stages:

```text
应用创作修改
同步角色参考图
准备制作计划
生成受影响镜头
生成视频
合成成片
完成
```

Again:

**the text is presentation; backend state is truth.**

---

# 13. Progress Must Be Evidence-Based

Users should be able to ask:

```text
“现在做到哪了？”
```

The answer must be synthesized from current persisted state.

Example:

```text
当前执行仍在进行。

已完成：
• 创作修改已写入正式项目
• 赤焰守卫正面/侧面参考图已完成

正在进行：
• 背面参考图生成

之后：
• Scene 3 制作计划
• 3 个受影响镜头
• 视频与合成
```

Do not allow the model to guess progress based on conversation history.

If the backend cannot verify a stage:

say that it cannot verify it.

---

# 14. Completion Must Also Be Truthful

Never show:

```text
已完成
```

just because an Agent finished talking.

For a production execution, completion requires the authoritative execution to reach its terminal successful state.

If this path expects a composed output, also verify the final artifact recorded by the production system actually exists / is valid according to current project conventions.

Only then may the UX display:

```text
制作完成
```

and link/open the actual final result.

If creative execution completed but production did not:

say exactly that.

Example:

```text
创作修改已完成，但视频制作在角色参考图阶段失败。
```

Not:

```text
修改已经全部完成。
```

---

# 15. User Control During Long Runs

The user must remain able to:

```text
查看进度
停止/取消
查看当前阶段
查看 blocker
```

Do not implement uncontrolled mid-execution mutation.

If a new creative instruction arrives while an already-approved execution is actively mutating/producing, do not silently modify the approved plan.

For this phase use a deterministic rule:

```text
active execution
+
new creative change
```

must produce a clear choice such as:

```text
当前计划正在执行。

[让当前任务完成后处理]
[停止当前任务并重新规划]
```

Do not pretend the running execution dynamically absorbed the request.

---

# 16. Fix F-1 — Sanctioned Character Writeback Must Not Break Recovery

The independent audit proved that current `creative_source_signature` includes Character `views` keys.

Character sync legitimately writes those views.

That sanctioned production writeback changes the signature and may cause restart re-entry to incorrectly report:

```text
PROJECT_CHANGED_AFTER_APPROVAL
```

even though the mutation was made by the execution itself.

Fix this correctly.

Do not merely suppress the error.

Prefer separating:

```text
creative-semantic identity
```

from:

```text
production-generated asset state
```

The execution freeze gate should detect real creative mutation after approval but must tolerate production writes that the approved execution itself is authorized to create.

A likely clean design is to exclude production-managed Character view output fields from the creative semantic signature used by the freeze gate.

However, inspect the current signature consumers first.

Do not blindly remove a field if another correctness invariant depends on it.

Acceptance must prove both:

```text
sanctioned Character views writeback
→ recovery continues
```

and:

```text
real external creative mutation after approval
→ execution still blocks
```

---

# 17. F-1 Required Crash Test

Create at least a two-Character affected-production scenario.

Required sequence:

```text
Character A regeneration completes
↓
A views persisted
↓
Character B begins / remains pending
↓
hard-stop backend
↓
restart backend
↓
restore execution
```

Expected:

```text
A is NOT regenerated again
B continues
execution continues
no PROJECT_CHANGED_AFTER_APPROVAL false positive
```

This is mandatory.

A single-Character happy path is insufficient.

---

# 18. Fix K1 — Appearance Changes Must Really Regenerate Character Views

Current known limitation:

```text
Character description changes
+
existing views already status=done
↓
done_views skip
↓
0 new Character generation
↓
downstream Shots use old appearance
```

This violates user intent.

Fix it.

If the approved manifest says:

```text
character action = regenerate
```

then existing `done` views from the old Character appearance must not cause the new execution to skip regeneration.

Preserve historical assets.

Do not delete old images merely to force generation.

Use an explicit regeneration intent/version/signature or equivalent current architecture.

Distinguish:

```text
resume same generation
→ done views may safely be skipped
```

from:

```text
new approved appearance revision requires regeneration
→ previous done views are stale inputs
```

After regeneration:

```text
new active Character views
→ affected Shot generation
```

must use the new references.

---

# 19. K1 Required Real Acceptance

Start with a real Character that already has:

```text
front done
side done
back done
```

User asks through the Agent:

```text
“把这个角色改成银色短发和黑色战斗装甲。”
```

After approval/execution verify:

```text
Character description changed
new Character view generation actually occurred
new front/side/back assets differ in lineage/version
old assets still exist in history where appropriate
affected Shots consume the new active references
```

Do not accept:

```text
manifest says regenerate
```

as proof.

Real generation must happen.

---

# 20. Fix K3 — Full Browser Refresh Must Restore the Correct Project

Current behavior:

```text
in-app remount
→ works

F5
→ projectName resets to default
→ wrong project restored
```

Fix the minimum correct product behavior.

Prefer using an authoritative route/project identity if the current router can support it cleanly.

If that would require a dangerous whole-app routing migration, a persisted ProjectStore identity is acceptable for this phase.

Requirements:

```text
selected project persists across F5
restoration occurs before Agent/ChangeSession restore
invalid/deleted project does not create a crash loop
explicit project switching still works
project isolation remains intact
```

Do not persist execution truth only in localStorage.

Only project identity/UI navigation state may use it.

Session/execution truth remains backend persisted state.

---

# 21. Fix F-2 — Durable Per-Scene Production Planning Checkpoint

Current behavior:

```text
s01 planning completed
s02 planning crashes
restart
s01 planner LLM runs again
```

Final state remains correct, but external work is duplicated.

Add durable per-scene progress.

After each Scene plan has actually been produced/approved, persist enough state to prove:

```text
scene_id
plan identity/version
completed state
```

before moving to the next Scene.

On re-entry:

```text
completed + still valid
→ reuse / skip

not completed
→ run
```

Do not simply rely on an in-memory set.

Required test:

```text
2 scenes
s01 planner completes
crash during s02
restart
```

Expected:

```text
planner call count s01 = 1
planner resumes s02
final execution correct
```

---

# 22. Resolve `normalize_module` Before Golden Path

The previous audit recorded uncommitted WIP in:

```text
src/agents/inquiry.py
src/agents/change_analysis.py
```

around module-name normalization.

The full natural-language Golden Path now exercises exactly that front-half analysis layer.

Therefore inspect the WIP explicitly.

Do not accidentally depend on uncommitted code.

Choose one outcome based on current code/tests:

```text
A. implementation is correct and required
→ formalize it
→ add focused regression tests
→ commit it as this phase's intentional work

or

B. current committed code no longer needs it
→ document why
→ leave user WIP untouched unless explicitly safe to remove
```

The final Golden Path must run from a known reproducible code state.

---

# 23. Floating Agent Bar Integration

Preserve the current Floating Agent UI concept.

Do not redesign it into a sidebar.

Do not rebuild the Agent Center.

But change its behavioral meaning.

The Floating Agent Bar should be:

```text
the user's high-frequency natural-language control surface
```

not merely:

```text
a thin wrapper around sendAgentMessage()
```

If a user sends a creative change intent through the Floating Bar, it must reach the real ChangeSession workflow rather than ending as an ordinary AgentMessage that only *talks* about changing the project.

Read-only conversation may continue using normal Agent sessions.

Write/change intent must bridge into the current Producer/ChangeSession architecture.

Reuse existing AI-7A/7B escalation mechanisms where possible.

Do not create another change-plan system.

---

# 24. Specialist Direct Chat Behavior

A specialist may remain the user's conversational context.

Example:

```text
Character Designer:
“把这个角色的头发改成银白色。”
```

The specialist must not directly mutate canonical.

Instead:

```text
direct conversation
↓
recognized creative change
↓
existing ChangeRequest / ChangeSession escalation
↓
Producer coordination where needed
↓
proposal
```

The user should not have to manually leave the Specialist and open Team Chat.

The UI may surface the resulting ChangeSession inline or provide a compact:

```text
正在由制作团队整理修改方案
```

card.

But this message must reference the real persisted session ID.

---

# 25. Do Not Make Users Manage the Agent Team

A normal user should not need to know:

```text
Producer
Director
Character Agent
Production Planner
Shot Producer
```

to finish a task.

Those identities remain useful for transparency and expert control.

But orchestration should happen automatically.

The primary mental model is:

```text
User
→ AGNES
→ finished creative result
```

not:

```text
User
→ manually pick Agent A
→ answer parameter
→ manually ask Agent B
→ press next
→ manually ask Agent C
```

Agent identity is explanation of responsibility, not workflow bureaucracy.

---

# 26. Failure UX

When something fails, display the real failure boundary.

Examples:

```text
角色参考图生成失败
视频服务暂时不可用
正式项目在批准后发生变化
制作计划生成失败
```

Provide only actions that actually work.

Examples:

```text
[重试当前可重试阶段]
[重新提案]
[查看详情]
[停止任务]
```

Do not show:

```text
[继续]
```

if backend Resume is a no-op for that blocker.

Do not let the model respond:

```text
“没问题，我正在重试。”
```

unless a retry object/job was really submitted.

---

# 27. Browser Refresh / Restart Truth

During:

```text
routing
consulting
analysis
preview
awaiting_user_decision
approved execution
character_sync
planning
production
compose
```

test browser reload/remount behavior.

After refresh the page should reconstruct truth from:

```text
backend persisted objects
```

not from old chat narration.

If the backend itself restarted, recover using existing durable recovery mechanisms.

---

# 28. Do Not Add Fake Progress Percentages

Do not invent:

```text
42%
67%
90%
```

unless the denominator is deterministically known.

For stages with known units, real counts are allowed:

```text
角色参考图 2 / 3
受影响镜头 4 / 7
视频 1 / 3
```

For unknown-duration work use state labels:

```text
正在分析
正在等待生成结果
正在合成
```

Truth is more important than animation.

---

# 29. Golden Path — The Most Important Acceptance of This Phase

This phase does not PASS merely because unit tests pass.

You must prove one complete user-visible path.

Prepare a disposable real project containing:

```text
at least 2 Characters
at least one Character with existing done front/side/back views
multiple Scenes
existing Shots
at least one existing generated video
a valid compose path
```

Then use the **real browser UI**.

No internal helper shortcuts.

No manually seeded approved plan as the starting point.

---

# 30. Golden Path User Instruction

Enter one natural-language request through the real Agent interaction surface.

Use a request comparable to:

```text
“把赤焰守卫改成银色短发、黑色战斗装甲，
并把它第一次进入机库的那一段做得更有压迫感。
其他剧情和人物不要动。”
```

Do not manually provide internal IDs or production parameters.

---

# 31. Golden Path Expected Flow

The proof must cover:

```text
User natural-language message
↓
real intent routing
↓
durable ChangeSession exists
↓
relevant Agent consultation
↓
dependency analysis
↓
UnifiedChangePlan
↓
validation
↓
visual preview / impact
↓
user reviews
↓
Approve & Run
↓
real ChangeExecution
↓
atomic canonical creative commit
↓
Character regeneration
↓
new Character views
↓
production planning
↓
targeted affected Shots only
↓
first/last frames where applicable
↓
audio where applicable
↓
video
↓
compose
↓
real final.mp4
```

No mock at the product-path boundary.

---

# 32. Golden Path Adversarial Events

During the same acceptance, deliberately introduce:

## Event A — Full page F5

Perform a real browser reload during an active durable stage.

Expected:

```text
same project
same session/execution
truthful current stage
polling resumes
no duplicate work
```

## Event B — Backend restart during Character sync

After Character A has completed and Character B has not:

```text
kill backend
restart backend
```

Expected:

```text
A not regenerated
B continues
no false stale blocker
```

## Event C — Observe Agent messaging

Verify that at no time does chat claim:

```text
started
running
completed
```

without the corresponding durable runtime fact.

---

# 33. Clarification Acceptance

Run several interaction probes.

### Probe 1

```text
“让这一段更紧张一点。”
```

when invoked from a selected Story/Scene.

Expected:

AGNES uses page context.

It does not ask:

```text
Which Scene?
```

### Probe 2

```text
“把这个角色的盔甲换成黑色。”
```

from a selected Character context.

Expected:

uses selected Character.

No ID request.

### Probe 3

A genuinely ambiguous historical reference:

```text
“换回之前那个造型。”
```

with several plausible old versions.

Expected:

one consolidated decision request.

### Probe 4

Cross-module change.

Expected:

automatic Producer coordination.

Do not ask the user which Agents to involve.

---

# 34. Test Layers

Use layered evidence.

## A — Deterministic tests

Cover at minimum:

```text
F-1 sanctioned views writeback
F-1 real external mutation still blocks
K1 done views + appearance change regenerates
F-2 per-scene planning resume
project identity persistence
clarification classification/routing where deterministic
execution truth predicates
```

## B — HTTP integration

Real FastAPI routes.

Mocks only at explicit external network boundaries where needed.

## C — Browser E2E

Real frontend + real backend.

Use web-first assertions.

Do not prove polling by a single screenshot.

## D — Live Agnes

Use only where actual output semantics must be proven.

At minimum:

```text
K1 new Character views
Golden Path affected production
final compose
```

Avoid wasting quota through meaningless repeated live generations.

## E — Restart/soak

Required F-1 and F-2 crash windows.

---

# 35. Browser Testing Discipline

Use the built-in browser / Playwright capability properly.

Prefer observable assertions:

```text
locator text/state
network requests
backend state
persisted execution
actual artifact
```

Avoid using fixed `sleep` as the only proof.

The previous audit already discovered that Playwright sync event handlers can mislead tests if the Python event loop is not pumped.

Do not repeat that measurement error.

---

# 36. Regression Protection

Before declaring PASS run the relevant existing:

```text
AI-7A
AI-7B
AI-7C
AI-7C-S
Agent
revision
production
JobQueue
planner
frontend
```

tests.

Run:

```text
frontend build
frontend typecheck
```

Report pre-existing failures separately.

Do not “fix” unrelated failures just to obtain a green number.

---

# 37. Git Checkpoints

Prefer small phase checkpoints.

Suggested logical grouping:

```text
H0
baseline + WIP disposition

H1
F-1 + K1 production semantics

H2
K3 + F-2 durable recovery

H3
truthful Agent interaction + minimal clarification

H4
full Golden Path + tests + handover
```

Actual commit structure may differ if repository reality suggests a cleaner grouping.

Do not mix unrelated formatting changes into these commits.

---

# 38. Explicit Non-Goals

Do NOT implement:

```text
AI-7D
Continuity Reviewer autonomous loop
LLM-as-judge quality retry loop
generic Agent self-reflection
recursive autonomous delegation
new memory/vector database
new RAG architecture
MCP migration
new production pipeline
new Workspace architecture
generic distributed workflow engine
full UI visual redesign
```

Do not spend this phase polishing shadows, colors, avatars, card radii, or animations.

This phase is about:

```text
truth
autonomy
recovery
semantic correctness
real end-to-end behavior
```

---

# 39. Security Hygiene

The previous independent audit found historical plaintext Agnes/MIMO credentials in old repository history.

Do not print those credentials in logs or reports.

If the currently configured credentials are still the exposed values, report that they should be rotated.

Do not rewrite Git history automatically without explicit user approval.

Do not let secret cleanup derail this implementation phase.

---

# 40. Final Completion Report

Do not return only:

```text
Done.
All tests passed.
```

Produce:

```text
AI-7C-H_COMPLETION_REPORT.md
```

containing:

1. baseline HEAD / branch;
2. dirty WIP found before implementation;
3. every production file changed;
4. F-1 root cause and exact repair;
5. K1 root cause and exact repair;
6. K3 repair;
7. F-2 repair;
8. normalize_module disposition;
9. new Agent interaction routing;
10. how runtime truth is separated from Agent prose;
11. clarification rules actually implemented;
12. what automatically continues without user confirmation;
13. what still requires user input;
14. exact ChangeSession → ChangeExecution UI state mapping;
15. restart behavior;
16. F5 behavior;
17. all new deterministic tests;
18. HTTP integration results;
19. browser results;
20. live Agnes evidence;
21. Golden Path exact trace;
22. generated final.mp4 evidence;
23. regression results;
24. typecheck/build results;
25. commits created;
26. unresolved limitations;
27. whether AI-7C-H is PASS or BLOCKED.

Also create:

```text
AI-7C-H_FINAL_HANDOVER.md
```

containing only stable facts the next phase may rely on.

---

# 41. PASS Criteria

AI-7C-H may be marked PASS only if all of the following are true:

```text
[ ] F-1 fixed
[ ] multi-character restart window proven
[ ] no duplicate Character regeneration after restart
[ ] real creative mutation still triggers stale protection
[ ] K1 fixed
[ ] appearance change with old done views creates new real views
[ ] downstream production uses new references
[ ] K3 fixed with true F5 test
[ ] F-2 fixed with planner call-count proof
[ ] natural-language write intent reaches real ChangeSession
[ ] Floating Agent does not merely hallucinate execution
[ ] running/completed states come from durable truth
[ ] unnecessary parameter questioning removed
[ ] genuine ambiguity still pauses safely
[ ] one approval can launch the approved downstream production chain
[ ] full real browser path starts from the user's natural-language message
[ ] path reaches actual final.mp4
[ ] no broad unintended regeneration
[ ] no project cross-contamination
[ ] refresh/restart do not falsify UI state
[ ] existing AI-7A/B/C/C-S invariants remain intact
```

---

# 42. STOP Condition

When AI-7C-H reaches PASS:

```text
STOP.
```

Do not begin AI-7D.

Do not implement Reviewer Agent.

Do not “continue improving” unrelated parts of the system.

Return the completion report and handover to the user for manual acceptance.

The next phase begins only after explicit approval.
