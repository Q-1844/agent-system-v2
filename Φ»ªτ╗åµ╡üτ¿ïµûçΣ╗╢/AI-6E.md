# AI-6E — Production Planner Runtime

AI-6E.0 — Agent Role Contracts & Collaboration Protocol is complete.

The Agent organization and collaboration boundaries are now frozen.

This phase implements the first runtime capability defined by the new Production Planner contract:

```text
production_plan.create
```

The goal is NOT to build a new Planner.

The goal is:

> Wrap the existing, already-proven Production Planner workflow as a real capability owned by the `production_planner` Agent, make it usable through the existing AgentTask delegation model, persist the exact same Production Plan artifact used by the existing Production Planner UI, and preserve all canonical/storyboard/production execution boundaries.

The intended chain after this phase is:

```text
Canonical Storyboard Shot
        ↓
Production Planner Agent
        ↓
SceneProductionPlan
        ↓
human review / edit / approve
        ↓
approved Production Plan
        ↓
future AI-6F Shot Producer
```

Stop at the approved Production Plan boundary.

Do NOT begin Shot Producer execution in this phase.

---

# 1. FIRST: inspect and preserve the Git baseline

Current expected branch:

```text
refactor/shot-pipeline
```

The previous reported HEAD was:

```text
eb498bc
```

which contains AI-6D + AI-6D.1.

AI-6E.0 added only:

```text
docs/agents/
```

There are also unrelated pre-existing user changes such as:

```text
saved_characters/
提示词.md
the untracked AI-6E.0 spec / planning file
```

These must remain untouched.

Before editing:

1. run `git status`;
2. inspect the actual diff;
3. verify that the AI-6E.0 `docs/agents/` additions are isolated;
4. if the AI-6E.0 docs are still uncommitted, create a checkpoint containing ONLY `docs/agents/`;
5. do not include unrelated user changes;
6. do not reset, clean, stash, or rewrite unrelated work.

If the repository state differs materially from this report, inspect and explain the difference before making destructive Git changes.

---

# 2. Read the contracts before touching runtime code

Read these first:

```text
docs/agents/README.md
docs/agents/COLLABORATION_PROTOCOL.md
docs/agents/roles/production_planner.md
docs/agents/roles/director.md
docs/agents/roles/shot_producer.md
```

Then inspect the actual implementation:

```text
src/agents/models.py
src/agents/registry.py
src/agents/capabilities.py
src/agents/context.py
src/agents/tasks.py
src/agents/runtime.py
routers/agents.py

src/ai/production_planner.py
routers/production_planner.py
tests/test_production_planner.py
tests/test_agents.py
```

Also inspect the existing Production Planner frontend/API wiring so that the same persisted artifact remains visible through the current UI.

The `## AI-6E implementation readiness` section at the end of:

```text
docs/agents/roles/production_planner.md
```

is the primary implementation contract for this phase.

Do not silently replace it with a new architecture.

---

# 3. Architectural invariant

Preserve the existing AGNES layering:

```text
Agent
= decision / responsibility layer

Workflow
= deterministic domain execution

Capability
= controlled Agent access to a workflow

Project / Production Plan / Workspace / AgentTask
= persistent shared artifacts

JobQueue / production handlers
= execution infrastructure
```

For AI-6E:

```text
Production Planner Agent
        ↓
production_plan.create
        ↓
existing Production Planner business/workflow layer
        ↓
existing SceneProductionPlan
        ↓
existing production_plans.json
```

Do NOT create a second Planner implementation.

Do NOT duplicate the current Planner system prompt, sanitizer, derivation logic, stale detection, persistence format, or approval behavior.

---

# 4. Resolve the audited `shot.inspect` registry mismatch

AI-6E.0 found this real permission mismatch:

```text
production_planner.allowed_capabilities
contains:
    shot.inspect

but:

shot.inspect.allowed_agents
does not contain:
    production_planner
```

Resolve it in AI-6E by adding:

```text
production_planner
```

to:

```text
shot.inspect.allowed_agents
```

Do NOT change the capability owner.

The owner remains:

```text
shot_producer
```

This is intentional.

Production Planner is allowed to READ Shot production state and existing asset state, but it does not own Shot execution.

After this fix:

```text
can_execute(
    production_planner,
    shot.inspect,
    inspect
) == True
```

while all Shot mutation/generation capabilities remain unavailable to Production Planner.

Add explicit registry tests for this.

---

# 5. Implement `production_plan.create`

The currently reserved capability:

```text
production_plan.create
```

must become:

```text
implemented = True
```

Its owner remains:

```text
production_planner
```

The Production Planner itself may execute it.

Producer must NOT directly execute it.

The intended permission relationship is:

```text
can_execute(
    producer,
    production_plan.create,
    ...
) == False
```

but:

```text
Producer
    ↓ delegate through AgentTask
Production Planner
    ↓ execute production_plan.create
```

must work.

Do not weaken Producer's direct capability boundary.

---

# 6. Scene scope only

AI-6E v1 is Scene-scoped.

Expected scope:

```text
scope_type = scene
scope_id   = existing scene_id
```

Do not implement:

```text
project-wide planning
cross-Scene dependency planning
shot-only partial-plan mutation
automatic multi-Scene recursive planning
```

The capability must validate that:

* the project exists;
* the Scene exists;
* the Scene belongs to the project;
* the Scene has Shots;
* the scope is appropriate for Production Planner planning.

Use the existing project/store/domain validation patterns.

Do not invent a parallel scope system.

---

# 7. Reuse the existing Production Planner implementation

The existing implementation already contains the important domain logic, including concepts such as:

```text
ProductionPlannerWorkflow.plan_scene
sanitize_plan
rederive_from_edits
is_plan_stale
SceneProductionPlan
PlannedShot
run_seed
dependency derivation
opening_source derivation
link_mode validation
```

and shared business helpers such as the existing planning and approval business functions.

Reuse them.

Do NOT copy their logic into `src/agents`.

The Agent layer should orchestrate and authorize.

The existing Planner layer should continue to understand how a Production Plan is created.

---

# 8. Preserve the router import boundary

The AI-6B import rule remains valid:

```text
src/agents/*
must not import routers/*
```

Therefore:

```text
src/agents
```

must NOT import:

```text
routers.production_planner
```

If the reusable planning business helper still physically lives inside the router, do not bypass the rule.

Instead, extract only the smallest reusable router-independent business function into an appropriate neutral existing service/domain location.

Then both:

```text
routers/production_planner.py
```

and:

```text
Production Planner Agent capability
```

must call the SAME shared business function.

Do not duplicate the business logic.

Do not introduce a DI framework.

---

# 9. Preserve the existing Planner AI contract

The AI model is allowed to decide only the semantic production strategy already permitted by the existing Production Planner.

Conceptually, this includes fields such as:

```text
opening_source
link_mode
reason
```

Use the exact current schema from `src/ai/production_planner.py`.

Do not invent a new LLM response schema if the existing one is already sufficient.

The AI must NOT freely generate authoritative structural fields such as:

```text
shot_id
source_shot_id
dependency edges
run_seed
derived generation flags
canonical Shot fields
plan status
asset paths
job IDs
```

Those remain code-derived.

Preserve the current sanitizer and derivation behavior.

For example:

```text
reference_previous / inherit_previous
```

must derive their source from the valid adjacent previous Shot according to the existing rules.

The first Shot must remain subject to the existing independent-opening rule.

Do not allow the model to create cross-Scene source dependencies.

---

# 10. Deterministic fields remain deterministic

Anything that can be derived by code must remain derived by code.

This includes all current deterministic behavior documented in the Production Planner implementation.

In particular:

```text
source_shot_id
requires_previous_video_end
generation flags
run_seed
first-shot restrictions
dependency relationships
sanitized link mode
sanitized opening source
```

must remain server/domain-derived according to the existing implementation.

Do not move these decisions into the Agent system prompt.

Do not move these decisions into free-form Agent chat.

---

# 11. AgentTask must support the real Planner capability

This is important.

AI-6B delegation was originally synchronous because only fast inspect capabilities were implemented.

`production_plan.create` performs real AI planning and therefore must use an async execution path.

Make the minimum runtime change required so that:

```text
Producer
    ↓
AgentTask
    ↓
production_planner
    ↓
production_plan.create
    ↓
await existing Planner workflow
    ↓
persist result
    ↓
AgentTask completed/failed
```

works correctly.

Do NOT create:

```text
Celery
Redis
distributed workers
a new task engine
background autonomous agents
recursive delegation
```

The HTTP request may await the Planner operation in AI-6E.

The existing AgentTask lifecycle remains conceptually:

```text
pending
→ running
→ completed
```

or:

```text
pending
→ running
→ failed
```

Persist transitions exactly as the current AgentTask store expects.

Existing inspect delegation must continue working.

---

# 12. Do not expand Specialist → Specialist delegation

The current rule remains:

```text
Producer → Specialist
allowed

Specialist → Specialist
not implemented
```

Production Planner must NOT directly delegate to Director.

Production Planner must NOT directly delegate to Shot Producer.

If planning discovers that the creative Shot itself is unusable, contradictory, or insufficiently defined:

```text
return a structured blocker / failed structured result
```

whose summary and suggested next action make clear that the conceptual upstream owner is:

```text
Director
```

but do NOT execute Director delegation.

Similarly, completing a Production Plan must NOT automatically call Shot Producer.

---

# 13. Do not redesign TaskStatus in this phase

AI-6E.0 documented the current semantic gap:

```text
blocked
needs_upstream_change
needs_specialist
```

currently collapse into the existing failed-task semantics.

Do NOT redesign the persistent AgentTask status model solely for this phase.

Use the existing:

```text
TaskStatus
AgentStructuredResult
warnings
suggested_next_actions
summary
data
```

to represent a planning blocker as cleanly as possible.

Do not introduce a new persistent workflow-state taxonomy unless the existing implementation genuinely cannot represent the required result.

If you discover that such a schema change is unavoidable, stop and report it instead of expanding the phase silently.

---

# 14. Structured capability result

A successful `production_plan.create` must return an `AgentStructuredResult`.

The structured result should reference the real persisted Production Plan artifact.

Use the current artifact-reference conventions already used by the Agent Runtime.

Do not invent a second persistent Agent-specific copy of the plan.

The structured `data` should expose the existing `SceneProductionPlan` representation, or the smallest safe API representation already used by the Production Planner layer.

The source of truth remains:

```text
projects/{project}/output/ai/production_plans.json
```

using the current per-Scene persistence behavior.

The AgentTask may store a reference/summary.

It must NOT own a second copy of authoritative Production Plan state.

---

# 15. Artifact-first handoff

This phase must demonstrate the collaboration rule established in AI-6E.0:

```text
AgentTask instruction
        ↓
Production Planner
        ↓
persistent ProductionPlan artifact
        ↓
lightweight AgentStructuredResult reference
```

Do NOT copy the full Producer or Planner chat transcript into the Production Plan.

Do NOT make downstream execution depend on chat history.

The Production Plan is the handoff artifact.

---

# 16. Existing Production Planner UI must use the same artifact

The existing Production Planner Studio/workflow must remain valid.

A particularly important integration proof is:

```text
Producer delegates production_plan.create
        ↓
AgentTask completes
        ↓
production_plans.json contains the new plan
        ↓
open existing Production Planner Studio
        ↓
the same plan is visible
        ↓
user can inspect/edit it
        ↓
user can approve it through the existing approval flow
```

There must NOT be:

```text
Agent Production Plan
vs
UI Production Plan
```

as two competing data sources.

One artifact only.

Do not redesign the Planner Studio.

Do not create a second Planner UI.

---

# 17. Preserve the human approval gate

`production_plan.create` creates/plans.

It does NOT approve.

Do not automatically call:

```text
approve_plan_business
```

after generation.

The existing human review/edit/approve step remains.

The user must still be able to inspect and modify the permitted planning fields before approval.

Approval remains the existing explicit action.

---

# 18. Canonical write boundary — critical invariant

Production Plan creation must NOT write canonical Storyboard state.

In particular, creating a plan must NOT modify:

```text
project.json Shot.description
Shot.characters
Shot.target_duration_s
Shot.opening_source
Shot.source_shot_id
Shot.source_version
Shot.link_mode
generated assets
```

unless some listed field is already part of the existing Production Plan artifact rather than canonical Shot state.

Follow the actual current model.

Most importantly:

```text
Production Plan intention
!=
effective Shot continuity state
```

The existing invariant remains:

> `approve` does not write `shot.opening_source`.

The effective `opening_source` is applied only later by the execution layer when the prerequisite source asset actually exists.

That later execution belongs to AI-6F / existing execution infrastructure.

Do not move it into AI-6E.

---

# 19. Stale-plan behavior remains unchanged

Preserve the existing `is_plan_stale(...)` semantics.

Storyboard creative/structural changes that currently invalidate a plan must continue to do so.

Asset/job execution changes that currently do not invalidate it must remain non-staling.

Do not silently auto-refresh or auto-replan a stale plan.

If a plan is stale:

```text
inspect
→ report stale

approve/edit according to existing behavior
→ existing conflict / 409 behavior

new explicit production_plan.create
→ may create a fresh plan using the current Scene
```

according to the current Planner business rules.

Do not invent background automatic replanning.

---

# 20. Keep the existing Production Planner endpoint behavior stable

The existing Production Planner API and UI are already proven.

Do not break their request/response behavior while extracting/reusing shared business logic.

If the existing route is refactored to call a newly extracted neutral helper:

* preserve route paths;
* preserve request schema;
* preserve response schema;
* preserve status codes;
* preserve stale behavior;
* preserve persistence;
* preserve UI behavior.

This phase is Agent integration, not Planner API redesign.

---

# 21. Minimal Agent Center wiring

Inspect the current Agent Center delegation form.

AI-6B originally defaulted AgentTask operation to `inspect`.

That cannot accidentally cause:

```text
production_plan.create
```

to be submitted using the wrong operation.

Make the smallest frontend/API adjustment needed so that the Producer can delegate the real Planner capability using its registered allowed operation.

Prefer deriving the valid operation from Capability metadata.

If a capability exposes only one operation, the UI does not need a noisy extra selector.

Do NOT redesign Agent Center.

Do NOT add a new Planner dashboard.

The goal is simply to make the existing delegation surface capable of submitting:

```text
target_agent = production_planner
capability_id = production_plan.create
scope_type = scene
scope_id = <scene>
operation = <the capability's registered create/execute operation>
instruction = <task instruction>
```

using the actual registry definition.

---

# 22. Do not turn Agent chat into autonomous tool calling

Direct Conversation and Delegated AgentTask remain distinct concepts.

Do NOT implement:

```text
LLM tool calling from normal Agent chat
automatic capability selection from free-form conversation
auto-execute after a Planner chat message
recursive planning
chat-controlled production
```

The Production Planner Conversation may remain a conversation surface.

The real `production_plan.create` capability is explicitly invoked through the capability/task flow.

Do not broaden AI-6E into autonomous agents.

---

# 23. System prompts

AI-6E.0 confirmed that the current Production Planner system prompt already matches the new role contract.

Do not expand it into the full Markdown contract.

Do not copy `production_planner.md` into runtime prompts.

Only change a runtime prompt if implementation reveals a real contradiction.

Any change must remain concise and limited to:

```text
identity
mission
owns
does-not-own
scope
write boundary
output expectation
```

Otherwise leave runtime prompts untouched.

---

# 24. Tests — capability and authorization

Extend the Agent tests to prove at minimum:

### Registry

1. `production_plan.create` exists.
2. `production_plan.create.implemented == True`.
3. owner is `production_planner`.
4. Production Planner may execute the registered operation.
5. Producer may NOT directly execute it.
6. an unrelated Specialist may NOT execute it.
7. `production_planner + shot.inspect` now passes authorization.
8. `shot.inspect.owner_agent` remains `shot_producer`.

### Delegation

9. Producer may delegate `production_plan.create` to Production Planner.
10. Specialist → Production Planner delegation remains rejected.
11. invalid Scene scope is rejected cleanly.
12. unknown Scene is rejected cleanly.
13. disabled Production Planner remains rejected according to current Runtime rules.

---

# 25. Tests — real Planner integration

Use deterministic/fake Agnes responses.

Do not require the public Agnes service for automated tests.

Prove:

1. Producer delegates a Scene planning task.
2. AgentTask persists `pending/running/completed`.
3. the existing Production Planner workflow is called.
4. a real `SceneProductionPlan` is produced.
5. the plan is persisted in the existing `production_plans.json`.
6. `production_plan.inspect` can immediately read the same plan.
7. the returned `AgentStructuredResult` references that artifact.
8. a newly constructed AgentTaskStore/runtime can recover the completed task.
9. the existing Production Planner reader/API can read the same plan.

Do not mock the Production Plan by constructing an unrelated fake shape if the real domain model can be used.

---

# 26. Tests — deterministic planning boundary

Preserve and/or extend current Production Planner tests proving that the model cannot become authoritative over deterministic fields.

At minimum verify the existing rules around:

```text
opening_source sanitization
link_mode sanitization
first Shot restrictions
source_shot_id derivation
adjacent previous-Shot dependency
requires_previous_video_end
derived generation fields
run_seed
```

Use the actual current field names and rules from `src/ai/production_planner.py`.

Do not rewrite tests around assumptions from this prompt if the real implementation names differ.

---

# 27. Tests — canonical protection

Add an Agent-runtime-level proof.

Before delegated planning:

```text
read/hash projects/{p}/output/project.json
```

Then:

```text
Producer
→ Production Planner AgentTask
→ production_plan.create
→ completed plan
```

Then verify:

```text
project.json
```

is byte-for-byte unchanged.

Also preserve the existing regression proving that Planner approval does NOT write effective:

```text
shot.opening_source
```

or its source identity into canonical Shot state.

This is one of the primary acceptance criteria.

---

# 28. Tests — stale behavior

Verify that plans created through the Agent capability participate in the exact same stale semantics as plans created through the existing Planner endpoint.

For example:

```text
Agent creates plan
→ plan is fresh
→ relevant Storyboard creative/structural change
→ production_plan.inspect reports stale
```

while existing non-staling execution/asset changes remain non-staling according to current rules.

Do not implement a second Agent-specific stale checker.

---

# 29. Regression suite

Run the relevant focused tests first:

```text
tests/test_agents.py
tests/test_production_planner.py
```

plus any new AI-6E-specific test file if you create one.

Then run the full backend suite using the repository's established Python executable:

```text
"D:/Delta Force/python.exe" -m pytest tests/ -q
```

If frontend code changes, also run the existing frontend build/type checks used by the project.

Do not fix unrelated pre-existing warnings/type errors unless AI-6E caused them.

Report pre-existing failures separately.

---

# 30. Manual integration proof

If the environment is configured and the existing UI can be run, verify this flow:

```text
1. Open Agent Center.
2. Use Producer delegation.
3. Target Production Planner.
4. Choose production_plan.create.
5. Select an existing Scene.
6. Submit the task.
7. Observe AgentTask complete.
8. Open the existing Production Planner Studio for that Scene.
9. Confirm the same plan is visible.
10. Edit an allowed planning choice.
11. Save.
12. Approve manually.
13. Confirm no Shot generation starts.
14. Confirm canonical Shot continuity has not been applied.
```

A live Agnes call is optional for this phase if automated FakeAgnes coverage already proves the runtime path.

Do not consume real generation resources unnecessarily.

---

# 31. Explicitly DO NOT implement

AI-6E must NOT implement:

```text
shot.first_frame
shot.last_frame
shot.video
actual image generation
actual video generation
automatic candidate selection
automatic retries
Shot Producer runtime
Continuity Reviewer runtime
review.shot
review.scene
automatic accept/reject
ProductionRun creation
new JobQueue behavior
new dependency scheduler
automatic plan approval
automatic production after approval
Specialist → Specialist delegation
Director auto-escalation
recursive planning
autonomous Producer
debate
reflection loops
memory
RAG
vector database
MCP
LangGraph
CrewAI
AutoGen
canonical Workspace merge
make-workspace-official
cross-Scene planning
Agent-chat autonomous tool calling
```

Do not redesign:

```text
Director Proposal schema
Workspace semantics
ChangeSet semantics
Agent UX
Production Planner Studio
canonical project model
```

---

# 32. Success condition

AI-6E succeeds when this is true:

```text
Producer
    ↓ traceable AgentTask
Production Planner
    ↓ authorized production_plan.create
existing ProductionPlannerWorkflow
    ↓
real SceneProductionPlan
    ↓
existing production_plans.json
    ↓
existing Planner Studio can inspect/edit it
    ↓
human approve
```

while simultaneously:

```text
canonical Storyboard unchanged
no Shot generated
no JobQueue execution started
no Specialist→Specialist delegation
no automatic approval
```

The strongest proof is:

> A Production Plan created through the Agent Runtime is indistinguishable, as a persisted domain artifact, from a Production Plan created through the existing Planner workflow, because both paths reuse the same business logic and the same source of truth.

Stop once this invariant is proven.

---

# 33. Final report

At completion, report exactly:

```text
AI-6E — Production Planner Runtime

1. Git baseline / AI-6E.0 checkpoint
2. Files added
3. Files modified
4. production_plan.create registry state
5. Planner ↔ shot.inspect mismatch resolution
6. Authorization behavior
7. Shared Planner business/workflow path reused
8. Any business-helper extraction performed
9. Async AgentTask execution path
10. Scene-scope validation
11. Structured input handling
12. SceneProductionPlan output
13. Deterministic vs AI-owned fields
14. Persistence / artifact handoff
15. Existing Planner Studio compatibility
16. Human approval behavior
17. Stale-plan behavior
18. Canonical project.json protection proof
19. Focused tests
20. Full regression/build result
21. Manual integration result, if performed
22. Explicitly unchanged runtime behavior
23. Anything intentionally deferred to AI-6F
```

If you discover a conflict between:

```text
docs/agents/roles/production_planner.md
```

and the current runtime implementation:

* do not silently reinterpret the role;
* identify the exact conflict;
* prefer the frozen role contract unless doing so would require a major architecture change;
* if it requires a major architecture change, stop and report before expanding scope.

The next intended phase after successful AI-6E is:

```text
AI-6F — Shot Producer Runtime
```

Do not begin AI-6F during this task.
