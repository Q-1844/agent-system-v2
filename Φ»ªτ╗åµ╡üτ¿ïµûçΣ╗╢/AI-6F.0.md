# AI-6F.0 — Durable AgentTask Bridge & Production Progress Contract

AI-6E has been completed and committed:

```text
b76e201  AI-6E.0 Agent Role Contracts
f5dc5a0  AI-6E Production Planner Runtime
```

AI-6E proved:

```text
Producer
→ AgentTask
→ Production Planner
→ existing Production Planner workflow
→ same persisted Production Plan
→ existing Planner Studio
```

No Shot generation is started automatically.

The next phase begins the execution side of the Agent team.

However, do NOT connect real Agnes generation yet.

The purpose of AI-6F.0 is to build the durable lifecycle bridge required before Shot Producer can safely own long-running production work.

The central invariant is:

> An Agent model invocation may finish quickly, while the work it initiated may continue for minutes.
> AgentTask must remain durably attached to the existing Job / Production Run until the real work finishes.

This phase must also establish the progress semantics that future Producer and Agent UX will use.

---

# 1. Preserve the current checkpoint

Before editing:

```bash
git status
git diff --stat
git log --oneline -20
```

Confirm:

```text
b76e201
f5dc5a0
```

or the equivalent current history.

Do not reset, clean, stash, checkout, discard, or rewrite unrelated user work.

Create no speculative refactor.

---

# 2. Read the real implementation first

Inspect at minimum:

```text
src/agents/models.py
src/agents/tasks.py
src/agents/runtime.py
src/agents/capabilities.py
src/agents/context.py

docs/agents/COLLABORATION_PROTOCOL.md
docs/agents/roles/producer.md
docs/agents/roles/production_planner.md
docs/agents/roles/shot_producer.md

existing JobQueue implementation
existing job persistence / recovery
existing SceneAutopilotRun implementation
existing ProjectProductionRun implementation
existing run persistence / checkpoint logic
existing Job origin metadata
existing startup recovery
existing SSE job stream
existing shot generation business helpers
```

Find the actual current file names before modifying anything.

Do not assume names from this prompt when the repository differs.

---

# 3. Do NOT create AgentProductionRun

This is a hard red line.

The project already has durable production execution concepts such as:

```text
SceneAutopilotRun
ProjectProductionRun
JobQueue
Job origin
checkpoints
restart recovery
asset/version lineage
production manifests
```

AI-6F must reuse/reference those structures.

Do NOT introduce:

```text
AgentProductionRun
AgentJobQueue
AgentGenerationPipeline
AgentVideoRunner
```

or another parallel execution system.

AgentTask is a coordination record.

ProductionRun / Job is the execution record.

They must be linked, not duplicated.

---

# 4. Separate Agent lifetime from work lifetime

Do not keep `/delegate` HTTP open until a video/job finishes.

Required lifecycle:

```text
Producer delegates task
        ↓
AgentTask persisted
status = pending
        ↓
Shot Producer capability starts/attaches to existing durable work
        ↓
AgentTask status = running
        ↓
HTTP request may return
        ↓
Job / Run continues
        ↓
real Job / Run reaches terminal state
        ↓
AgentTask settles
completed / failed
```

AI-6E was allowed to await Planner work because planning is comparatively short.

Shot production must not use that assumption.

---

# 5. AgentTask must reference durable execution

Determine the smallest compatible extension to the current AgentTask representation.

The task must be able to durably recover references such as:

```text
job_id
run_id
artifact_refs
current production phase
```

Reuse existing metadata/result fields if they safely support this.

Do NOT add fields merely because this prompt names them.

Do NOT redesign the entire AgentTask persistence schema.

The requirement is semantic:

> Given an AgentTask after backend restart, the Runtime must be able to determine which already-existing Job / Run belongs to it.

---

# 6. Idempotency / duplicate-generation protection

This is mandatory.

The same AgentTask must never accidentally submit the same production operation twice because of:

```text
HTTP retry
browser retry
backend restart
Agent Runtime recovery
settlement retry
SSE reconnect
```

Before starting work:

```text
AgentTask
→ inspect persisted execution reference
```

If an existing non-terminal Job / Run already belongs to the task:

```text
reattach
```

Do NOT:

```text
submit another generation
```

If the referenced execution is already complete:

```text
settle from the existing result
```

Again, do not regenerate.

Test this explicitly.

---

# 7. Fake execution only

AI-6F.0 must NOT perform real Agnes image/video generation.

Use the project's existing FakeAgnes / test doubles where appropriate.

If the existing JobQueue can accept deterministic fake handlers, use it.

Prove lifecycle semantics using fake long-running jobs.

Do not consume Agnes API quota or wait for real video generation in this phase.

---

# 8. Progress is a first-class durable concept

Future Agent UX must answer:

```text
Who is working?
What are they doing?
What scope are they working on?
What stage is the work in?
What has finished?
What is waiting?
Is user action required?
Why is something blocked?
```

Do not implement a fake universal percentage.

Agent reasoning and deterministic production progress are different.

For model/reasoning phases, human-facing progress may be qualitative:

```text
analyzing
planning
preparing proposal
```

For deterministic production phases, progress should derive from real state:

```text
queued
first_frame
last_frame
video
review
completed
```

or equivalent current domain terminology.

Where a run contains N concrete items, expose real counts:

```text
completed_items
total_items
```

Do not manufacture `47%` for an LLM call.

---

# 9. Keep AgentTask status small

Do not expand the persistent AgentTask status enum into dozens of UI states unless the current design genuinely requires it.

Prefer:

```text
AgentTask.status
= pending / running / completed / failed
```

using the real current enum.

Represent detailed execution progress separately through existing task metadata / structured result / execution references where possible.

Examples:

```text
status = running
phase = queued

status = running
phase = video_generating

status = running
phase = waiting_for_existing_job
```

The exact schema must follow the real repository.

The principle is:

> lifecycle status and progress detail are not the same thing.

---

# 10. Progress must survive restart

Test:

```text
AgentTask = running
linked Job = running
backend stops
backend starts
```

After startup recovery:

```text
AgentTask remains running
same job_id/run_id remains linked
no duplicate job is submitted
progress can be reconstructed
```

When the old Job eventually succeeds:

```text
same AgentTask
→ completed
```

When it fails:

```text
same AgentTask
→ failed
```

No manual repair should be required for the normal recovery path.

---

# 11. Job / Run is source of truth for execution

Do not make AgentTask pretend to be the production engine.

For long-running execution:

```text
AgentTask
= who asked whom to do what

ProductionRun / Job
= what is actually executing
```

If they disagree, reconcile AgentTask from the durable execution state rather than inventing an independent Agent execution reality.

---

# 12. Progress event bridge

Inspect the existing JobQueue event/SSE infrastructure.

Reuse it.

Do not create a second unrelated Agent SSE system if the current stream can safely provide the underlying production updates.

Build the smallest bridge needed so that an AgentTask can expose current linked execution progress.

Conceptually:

```text
Job changes
    ↓
linked AgentTask progress can be refreshed
    ↓
Agent Center / Producer can understand current work
```

Polling fallback and restart reconstruction must remain possible.

Do not make correctness depend solely on an in-memory SSE event.

SSE is notification.

Persistent state is truth.

---

# 13. Introduce the minimal-impact production principle

Document this invariant now even though story editing is NOT implemented in AI-6F.0:

> A user revision should invalidate and regenerate only the smallest domain scope actually affected.

Examples for future phases:

```text
bad generated candidate
→ regenerate candidate only

same Shot creative intent, bad production strategy
→ re-plan/re-produce affected Shot

Storyboard creative change
→ Director revision
→ affected Production Plan becomes stale
→ re-produce affected Shots

Scene story change
→ future Script Writer revision
→ re-evaluate affected downstream Scene only

whole-story rewrite
→ future project-level creative version
→ never silently delete old work
```

This section is a contract for later phases.

Do NOT implement screenplay rewriting here.

---

# 14. Preserve previous work

Another hard invariant:

> Regeneration is version creation, not destructive erasure.

Where the existing system already has candidate/version/history semantics, preserve them.

Do not delete previously generated valid assets merely because another attempt is created.

Do not rewrite historical run lineage.

Do not modify canonical merge / make-official semantics.

---

# 15. Explicit user request vs autonomous cascade

AI-6F.0 does NOT implement autonomous Producer chaining.

However, future behavior must distinguish:

```text
user explicitly requested regeneration
```

from:

```text
Agent inferred that another domain should change
```

A Specialist may execute work that is within its own accepted task.

It must not silently perform an upstream creative revision simply to make execution easier.

Examples:

```text
Shot Producer discovers missing/stale Production Plan
→ blocker

NOT:
Shot Producer invents a new plan
```

```text
Production Planner discovers Shot creative intent is invalid
→ blocker / upstream suggestion

NOT:
Planner silently rewrites Storyboard
```

Preserve the role contracts created in AI-6E.0.

---

# 16. Do not confuse “bad result” with “bad creative intent”

Document this distinction for the future Producer orchestration layer.

Conceptually:

```text
result problem
→ Reviewer / Shot Producer

production-strategy problem
→ Production Planner

Storyboard problem
→ Director

story problem
→ Script Writer

character identity problem
→ Character Designer

composition problem
→ Editor
```

AI-6F.0 does not implement all of those capabilities.

It only ensures the execution foundation will support them later.

---

# 17. Shot Producer capability scaffolding

Prepare only the minimum infrastructure required for AI-6F to later connect:

```text
shot.first_frame
shot.last_frame
shot.video
```

Do NOT actually broaden into all generation operations during this phase.

The final AI-6F path will be:

```text
Producer
→ AgentTask
→ Shot Producer
→ existing business helper
→ JobQueue
→ existing artifact
→ AgentTask settlement
```

Do not copy router handlers into `src/agents`.

Do not rewrite existing image/video generators.

Do not rewrite Scheduler / JobQueue.

---

# 18. Tests — lifecycle

Add focused tests proving at minimum:

```text
1. Producer can create a long-running Shot Producer AgentTask using the fake capability path.

2. AgentTask persists pending → running.

3. Starting long-running work returns without waiting for fake work to complete.

4. AgentTask stores/reconstructs its linked durable execution reference.

5. linked fake Job success settles AgentTask completed.

6. linked fake Job failure settles AgentTask failed.

7. backend/runtime reconstruction reattaches to the same running Job.

8. reconstruction does not submit a duplicate Job.

9. repeated settlement is idempotent.

10. completed Job discovered after restart settles the existing AgentTask rather than regenerating.

11. failed Job discovered after restart settles failed correctly.

12. progress detail can be reconstructed from persistent execution state.

13. AgentTask remains running while its linked Job is queued/running.

14. AgentTask never reports completed while its linked real execution is still non-terminal.
```

Use actual current status names if they differ.

---

# 19. Tests — regression

Run the existing focused suites around:

```text
agents
production planner
job queue
production runs
autopilot
shot services
startup recovery
```

Then run the full test suite.

AI-6F.0 must cause zero unrelated regressions.

Do not fix unrelated pre-existing failures unless this phase caused them.

Report them separately if encountered.

---

# 20. No real-generation browser acceptance yet

Because this phase uses fake execution only, browser acceptance should verify lifecycle/progress semantics, not Agnes output quality.

If an Agent Center path can safely trigger the fake/dev path, test:

```text
create/delegate task
→ task becomes running
→ linked execution visible
→ page refresh
→ same task still running
→ execution finishes
→ task becomes completed
```

If fake execution is test-only and cannot safely appear in production UI, keep browser acceptance limited and prove lifecycle through automated integration tests.

Do NOT create a fake production-only UI just for this phase.

---

# 21. Explicit non-goals

AI-6F.0 must NOT implement:

```text
real Agnes image generation
real Agnes video generation

full Shot Producer Runtime

Continuity Reviewer Runtime

Script Writer story mutation

Character Designer mutation

Director expansion

autonomous Producer

automatic Specialist → Specialist chaining

full-story rewrite

canonical merge

make-workspace-official

new ProductionRun type

new JobQueue

new workflow engine

LangGraph
CrewAI
AutoGen
Redis
Celery

major Agent Center visual redesign
```

---

# 22. Completion condition

AI-6F.0 succeeds when this is true:

```text
Producer
    ↓ traceable AgentTask

Shot Producer
    ↓ starts or attaches to durable fake execution

existing Job / ProductionRun
    ↓ continues independently of HTTP / model invocation

backend restart
    ↓

same AgentTask reattaches
    ↓

no duplicate generation
    ↓

real execution terminal state
    ↓

AgentTask settles correctly
```

and when the system can truthfully answer:

```text
who is working
what they are doing
what scope they are working on
which real Job/Run owns the execution
what phase it is in
whether it is queued/running/completed/failed
whether user action is required
```

without inventing fake progress.

---

# 23. Final report

Return:

```text
1. files changed
2. AgentTask persistence changes
3. Job/Run reference design
4. settlement mechanism
5. restart recovery mechanism
6. duplicate-generation protection
7. progress representation
8. SSE/event integration
9. tests added
10. focused test results
11. full regression result
12. manual acceptance result
13. anything intentionally deferred to AI-6F
```

Also state explicitly:

```text
whether any new ProductionRun model was created
whether any existing generation workflow was rewritten
whether any real Agnes request was sent
```

All three should normally be:

```text
no
no
no
```
