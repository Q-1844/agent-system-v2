# AI-7C — Approved Change Plan Execution & Production Orchestration

## Approved Plan → Revalidation → Execution Workspace → Atomic Creative Commit → Production Synchronization → Production Planner → Shot Production

---

# 0. Phase Positioning

AI-7B is complete.

The current implementation has already proved:

```text
User natural-language change request
        ↓
Specialist ModuleProposals
        ↓
Dependency / Impact / Conflict analysis
        ↓
Producer UnifiedChangePlan
        ↓
PlanValidator
        ↓
deterministic ChangeOperation projection
        ↓
PlanPreviewArtifact
        ↓
Visual Diff / Impact
        ↓
User Approval
        ↓
approved_for_execution
        ↓
STOP
```

AI-7C starts from that exact durable artifact.

The roadmap is now:

```text
AI-7A
Understand safely

↓

AI-7B
Propose safely

↓

AI-7C
Execute safely

↓

future AI-8
Review generated results and propose targeted correction
```

AI-7C is **not another creative reasoning phase**.

Its primary responsibility is:

> Execute exactly the approved plan, prove that the approved preview still matches current project truth, commit the approved creative state safely, synchronize the production inputs affected by that creative change, and then reuse AGNES's existing Planner / Shot Production / durable ProductionRun infrastructure to regenerate only the production work that is actually required.

The defining rule is:

> **The LLM decides what should change before approval. Code decides how an approved change is executed after approval.**

Do not ask Producer, Script Writer, Character Agent or Director to creatively reinterpret the approved plan during execution.

---

# 1. Mandatory AI-7B Final Preflight

Before changing any AI-7C code:

1. Confirm branch and clean baseline.
2. Confirm AI-7B commits are present.
3. Restart the backend because AI-7B changed:

   * API server / routers;
   * revision code;
   * Agent runtime.
4. Run the existing AI-7B §122 browser acceptance checklist on port 7777.
5. Do not fake a browser PASS.

Expected current automated baseline from the AI-7B completion report is approximately:

```text
417 backend tests passing

frontend:
0 new typecheck errors
6 pre-existing errors unchanged

vite build:
PASS
```

Do not hardcode those numbers as future truth.

Record the actual current baseline.

If §122 browser acceptance fails:

```text
STOP AI-7C

fix the failure as AI-7B scope
rerun the AI-7B gate
commit the repair
then begin AI-7C
```

Do not mix an AI-7B browser defect into the first AI-7C commit.

---

# 2. Exact Success Condition

AI-7C succeeds when the following statement is truthful:

> A user can approve a Unified Change Plan in AI-7B, explicitly start execution, and AGNES can safely revalidate that exact approved artifact against current project truth, replay the exact approved semantic operations inside an isolated execution Workspace, prove that the resulting project equals the approved Preview, atomically commit the approved creative change without losing unrelated production state, regenerate required character references and other production prerequisites when necessary, rebuild and approve Production Plans for the affected scope, run the required Shot production through the existing durable production infrastructure, survive refresh and backend restart without duplicate creative commits or duplicate Agnes generation, preserve historical assets, and present truthful progress until execution completes or stops at a real blocker.

Representative flow:

```text
AI-7B approved Plan
        ↓
User clicks Execute Plan
        ↓
create ChangeExecutionSession
        ↓
REVALIDATE
        ↓
source still exact?
approval still valid?
plan version still exact?
preview still reproducible?
0 unresolved decisions?
        ↓
create isolated Execution Workspace
        ↓
replay approved Plan Steps
        ↓
exact approved ChangeOperations only
        ↓
verify final Workspace result
==
approved PlanPreviewArtifact
        ↓
short final stale check
        ↓
ATOMIC CREATIVE COMMIT
        ↓
canonical creative state updated once
        ↓
production impact synchronization
        ↓
Character visual synchronization if required
        ↓
Production Planner for affected Scenes
        ↓
all required Plans valid + approved
        ↓
freeze production execution manifest
        ↓
existing durable production infrastructure
        ↓
Shot Producer / JobQueue / Agnes
        ↓
affected assets regenerated
        ↓
optional existing project composition
        ↓
COMPLETE
```

---

# 3. AI-7C Must Preserve the Meaning of AI-7B Approval

AI-7B intentionally established:

```text
Approve Plan
≠
Apply immediately
```

Do not destroy that boundary merely because AI-7C now exists.

The UI should remain:

```text
Approve Plan
        ↓
approved_for_execution
        ↓
STOP

[Execute Plan]
```

Execution requires a separate explicit action.

Do not change the AI-7B approval endpoint so that it automatically starts AI-7C.

This separation is valuable for:

```text
auditability
refresh/restart
delayed execution
stale detection
user control
debugging
```

---

# 4. Do Not Turn `applyable` Into a Backdoor

AI-7B may currently expose an `applyable` property which is always false.

Do not simply flip:

```python
applyable = True
```

after approval.

That would confuse:

```text
Workspace ChangeSet Apply

with

Approved Change Plan execution
```

They are not the same operation.

Prefer a distinct concept such as:

```text
execution_eligibility
```

with states equivalent to:

```text
eligible
already_started
already_completed
stale
blocked
invalid_approval
```

The plan remains a proposal artifact.

The `ChangeExecutionSession` is the execution artifact.

---

# 5. Read the Real Repository Before Implementing

Inspect the current committed code.

At minimum read the actual current implementations of:

```text
AI-7B models
AI-7B ChangeSession persistence
PlanApproval
UnifiedChangePlan
ChangePlanStep
ModuleProposal
PlanPreviewArtifact
ProposalProjectionEngine
PlanValidator
ConflictAnalyzer
Dependency / Impact facts

revision operation models
preview_operations_on_project
ChangeSet preview/apply
WorkspaceStore
Workspace locking
creative_source_signature

ProjectStore
atomic project write behavior
all current canonical mutation helpers

Director proposal runtime

Production Planner:
production_plan.create
plan validation
approve_plan_business
production_plans.json
stale rules

Shot Producer:
AgentTask durable execution bridge
shot.first_frame
shot.last_frame
shot.video

existing audio business helper

existing Character three-view / chain generation workflow

JobQueue
AgnesClient
rate limiter

SceneAutopilotRun
ProjectProductionRun
FullProjectRun

production run restart recovery
composition manifest
asset/version lineage

frontend:
ChangePlanReview.vue
changeSession store
TeamChatPanel
current polling infrastructure
useJobStream
```

Do not assume an old execution document filename still matches current code.

Code is the final authority.

---

# 6. Locked Existing Architecture

AI-7C must reuse rather than replace:

```text
AI-6A
Workspace / ChangeSet / revision / preview / impact

AI-6D
Director proposal architecture

AI-6E
Production Planner Runtime

AI-6F
Shot Producer durable AgentTask → Job bridge

AI-4B
SceneAutopilotRun

AI-5A
ProjectProductionRun

AI-5B
FullProjectRun durability / preparation checkpoint patterns

AI-7A
Project Intelligence / Team persistence

AI-7B
ChangeSession / Proposal / Plan / Preview / Approval
```

Do not create:

```text
WorkspaceV2

AgentProductionRun

ChangeExecutionWorkflowFramework

SecondJobQueue

SecondAgnesClient

SecondProductionPlanner

ChangePlanV2

GenericSagaFramework
```

Add only the execution structures actually missing.

---

# 7. New Execution Mode

AI-7B established a hard execution boundary such as:

```text
READ_ONLY
PROPOSAL_ONLY
```

AI-7C may extend the mode model with an explicitly guarded execution mode.

Conceptually:

```python
class AgentExecutionMode(str, Enum):
    READ_ONLY = "read_only"
    PROPOSAL_ONLY = "proposal_only"
    APPROVED_PLAN_EXECUTION = "approved_plan_execution"
```

Use actual current naming conventions.

`APPROVED_PLAN_EXECUTION` must never be obtainable merely because:

```text
Producer wants it
Specialist asks for it
Team Chat message says "just do it"
```

It requires:

```text
valid approved_plan_id
+
exact plan_version
+
valid PlanApproval
+
successful AI-7C preflight
+
ChangeExecutionSession
```

The runtime capability boundary remains fail-closed.

---

# 8. Producer Does Not Receive Arbitrary Write Power

Do not make this:

```text
Producer can now call every mutation capability.
```

The Producer is still not a universal editor.

Execution ownership remains:

```text
Script proposal content
→ Script Writer

Character proposal content
→ Character Agent

Storyboard proposal content
→ Director

Production Plan
→ Production Planner

real asset execution
→ Shot Producer
```

However, after approval, the creative proposals have already been compiled into deterministic `ChangeOperation`s.

Therefore AI-7C does **not** need to call those creative Specialists again merely to perform the commit.

The execution engine applies their already-approved operations.

This preserves:

```text
proposal item
→ plan item
→ operation
→ execution step
```

traceability.

---

# 9. The Most Important AI-7C Rule: No Creative Re-Deciding

Forbidden execution path:

```text
approved Script proposal
        ↓
AI-7C asks Script Writer again
        ↓
Script Writer produces slightly different text
        ↓
system applies new text
```

Also forbidden:

```text
approved Character proposal
        ↓
Character Agent invents additional visual details
```

Also forbidden:

```text
approved Director proposal
        ↓
Director silently redesigns another Shot
```

The user approved a specific projected result.

Execution must use that result.

If the approved operation cannot still be applied exactly:

```text
BLOCK
```

Do not creatively repair it.

A new creative interpretation requires:

```text
new proposal
new Plan version
new preview
new approval
```

---

# 10. `ChangeExecutionSession`

Introduce one durable execution artifact.

Suggested conceptual model:

```python
class ChangeExecutionSession(BaseModel):
    schema_version: int

    execution_id: str
    project_name: str

    change_session_id: str
    plan_id: str
    plan_version: int
    approval_id: str

    source_signature: str
    preview_artifact_id: str

    execution_mode: str

    status: str
    stage: str

    execution_workspace_id: str | None = None

    steps: list[ExecutionStepRecord] = []

    canonical_commit: CanonicalCommitRecord | None = None

    production_mode: str

    character_sync: list[CharacterSyncRecord] = []
    planning: list[PlanningExecutionRecord] = []

    production_manifest_file: str | None = None
    production_run_id: str | None = None

    blocker: dict | None = None
    error: dict | None = None

    cancel_requested: bool = False

    created_at: float
    updated_at: float
```

Adapt to current Pydantic/dataclass style.

Do not duplicate entire Plan / Project / ProductionRun objects inside this model.

Store references and stable snapshots where required.

---

# 11. Execution Status vs Stage

Keep status small.

Suggested:

```text
pending
running
waiting_user
blocked
failed
cancelled
completed
```

Use `stage` for detail:

```text
revalidate

workspace_create

creative_replay

workspace_verify

canonical_commit

character_sync

production_planning

production_prepare

production

compose

done
```

Do not create 30 top-level status values.

The UI can derive detailed progress from:

```text
status
stage
step records
Planner records
ProductionRun
Jobs
```

---

# 12. Execution Step Records

The approved Plan already has:

```text
ChangePlanStep
step DAG
owner_agent_id
plan_item_ids
```

AI-7C should add only execution state.

Example:

```python
class ExecutionStepRecord(BaseModel):
    step_id: str
    module: str
    owner_agent_id: str

    operation_ids: list[str]

    status: str

    workspace_revision_before: int | None = None
    workspace_revision_after: int | None = None

    changeset_id: str | None = None

    operation_digest: str

    started_at: float | None = None
    completed_at: float | None = None

    error: dict | None = None
```

Every executable operation must be traceable to:

```text
execution step
→ plan item
→ proposal item
```

---

# 13. Bind Plan Steps to Exact Operations

Before execution, verify AI-7B already persists enough traceability to map:

```text
ChangePlanStep
        ↓
ChangePlanItem
        ↓
proposal_item_ids
        ↓
ChangeOperation[]
```

If the mapping already exists:

reuse it.

If the Preview stores a flat operation list but does not expose stable execution bindings:

add the minimum deterministic binding layer.

Conceptually:

```text
step_id
→ operation_ids
→ operation_digest
```

Do **not** ask an LLM to reconstruct the mapping.

If an old approved Plan cannot be unambiguously bound:

```text
PLAN_EXECUTION_BINDING_MISSING
```

and do not execute it.

---

# 14. Operation Digest

Persist a deterministic digest for the exact approved operation sequence.

For example:

```text
hash(
  normalized operation type
  target IDs
  before values
  after values
  order
)
```

Do not include irrelevant timestamps.

Use this to prove:

```text
the operations executed
==
the operations previewed
```

---

# 15. Start Execution API Must Be Idempotent

Conceptually:

```text
POST
/project/{p}/team/change-sessions/{session_id}/plans/{version}/execute
```

Request may contain:

```json
{
  "production_mode": "affected_production"
}
```

Supported v1 modes:

```text
creative_only

affected_production
```

Do not add `full_project_regeneration` unless the user explicitly requests it and current production infrastructure already supports it safely.

Idempotency key should effectively include:

```text
project
plan_id
plan_version
approval_id
```

Calling execute twice must not create two execution sessions.

Return the existing execution.

---

# 16. One Approved Plan Version → One Creative Execution

Default invariant:

```text
same approved Plan version
→ at most one canonical creative commit
```

If production later fails:

resume the same execution.

Do not create another creative execution.

If the user wants a new creative modification:

create a new Plan version.

If the user wants only another production attempt:

that is a production retry/new ProductionRun under the already-committed execution, not a second creative commit.

---

# 17. Revalidation Must Happen Before Any Mutation

The first real stage is:

```text
REVALIDATE
```

Reload from persistence:

```text
ChangeSession
UnifiedChangePlan
PlanApproval
PlanPreviewArtifact
ModuleProposal refs
```

Verify:

```text
plan exists

exact version exists

PlanApproval belongs to this exact version

status == approved_for_execution

0 blocking user decisions

PlanValidator still passes

all referenced proposal items still exist

operation binding is complete

preview artifact integrity is valid
```

Then reload current canonical project.

Compare current creative source signature against the approved source signature.

---

# 18. No Automatic Rebase

If the project changed after approval:

```text
PROJECT_CHANGED_AFTER_APPROVAL
```

Do not:

```text
auto-rebase the Plan

silently rerun Preview

silently change before-values

ask Producer to reinterpret changes

apply only the pieces that still work
```

Even if the new change appears unrelated.

For AI-7C v1:

```text
stale Plan
→ execution blocked
→ new proposal/Plan version required
```

This makes the approval boundary understandable.

---

# 19. Production-State Changes Must Not Be Accidentally Treated as Creative Changes

Use the existing project's real `creative_source_signature`.

Do not switch to:

```text
whole project.json byte hash
```

if generated assets are intentionally excluded from creative staleness.

A video finishing after AI-7B approval should not necessarily invalidate a creative Plan.

However:

the later canonical commit must preserve that newly generated asset state.

This is why AI-7C must never implement canonical commit by blindly replacing `project.json` with an old Workspace snapshot.

---

# 20. Active Production Preflight

Before creative commit, inspect current durable production state.

If another active production flow is mutating the same project's production state:

```text
active ProjectProductionRun
active FullProjectRun
critical active generation Jobs
```

prefer to block execution with a truthful code such as:

```text
PROJECT_PRODUCTION_ACTIVE
```

rather than allowing two autonomous project-wide workflows to race.

Do not automatically cancel the user's existing production.

If the current repository already has a safe coexistence rule, preserve it.

The gate is:

> AI-7C must not create lost updates between creative commit and existing production writes.

---

# 21. Create an Execution Workspace Only After Revalidation

After revalidation succeeds:

```text
current canonical
        ↓
create isolated Creative Workspace
        ↓
mark workspace purpose = approved_plan_execution
        ↓
bind execution_id
```

Use the existing Workspace Store.

Do not create a second Workspace implementation.

Recommended metadata if current Workspace schema can safely accept it:

```text
execution_id
approved_plan_id
approved_plan_version
source_signature
purpose
```

Do not force schema fields if existing metadata already provides equivalent traceability.

---

# 22. Execution Workspace Is a Staging Transaction

The execution Workspace serves three purposes:

1. apply module steps safely without touching canonical;
2. preserve step-by-step audit/revision history;
3. prove the final executed result still equals what the user approved.

It is **not** the final project truth.

Do not run real production from this Workspace in AI-7C unless the current production system has already become source-aware.

The established production systems currently operate on committed project truth.

---

# 23. Apply Approved Steps Through Existing ChangeSet Semantics

For each approved `ChangePlanStep`:

```text
load exact operation binding
        ↓
create ChangeSet against current execution Workspace revision
        ↓
preview through existing revision engine
        ↓
validate
        ↓
Apply to execution Workspace
        ↓
persist ExecutionStepRecord
```

Do not write `working_project.json` directly.

Do not duplicate operation application logic.

Reuse:

```text
ChangeSet preview
ChangeSet apply
before checks
workspace revision checks
workspace lock
```

---

# 24. Do Not Concurrently Write the Same Workspace

The Plan DAG may say:

```text
Script
and
Character
may run in parallel
```

That metadata was important when the professional proposals were being produced.

During deterministic local operation application, parallel writes provide little value and introduce unnecessary race conditions.

It is acceptable to execute ready creative steps in a stable topological sequence.

For example:

```text
topological batch 1:
Script
Character

execute deterministically:
Script
then Character

batch 2:
Storyboard
```

The resulting semantic state must be identical.

Do not create concurrent Workspace Apply races merely to visually demonstrate DAG parallelism.

---

# 25. Respect the DAG

Although mutation may be serialized, dependencies remain mandatory.

Never execute:

```text
Storyboard step
```

before required:

```text
Script / Character step
```

has successfully applied to the staging Workspace.

Cycle detection remains the AI-7B PlanValidator's responsibility.

AI-7C should still fail if an impossible DAG somehow reaches execution.

---

# 26. No New LLM Calls During Creative Replay

Creative replay must be capable of running with:

```text
0 Producer reasoning calls
0 Script Writer calls
0 Character proposal calls
0 Director proposal calls
```

It is deterministic.

This should be a testable invariant.

Production Planner later is allowed to use its existing reasoning model because it is producing a **production plan**, not changing approved creative meaning.

---

# 27. Final Workspace Equivalence Gate

After all creative Plan Steps apply:

calculate the resulting creative signature/diff.

Compare it to the approved `PlanPreviewArtifact`.

At minimum verify:

```text
operation digest matches

expected entities modified match

projected creative signature matches

aggregate Visual Diff semantic result matches
```

Use the real available preview artifact structure.

Do not implement brittle HTML/string comparisons.

If the final Workspace cannot prove equivalence:

```text
EXECUTION_PREVIEW_MISMATCH
```

Canonical remains unchanged.

---

# 28. Why Module Steps Must Not Commit Directly to Canonical

Do not implement:

```text
Script Commit → canonical

Character Commit → canonical

crash

Storyboard never committed
```

That leaves canonical project truth in a partially executed Change Plan.

Instead:

```text
all module commits
        ↓
Execution Workspace only

all pass
        ↓
single canonical creative transaction
```

This is one of AI-7C's most important safety guarantees.

---

# 29. Canonical Commit Is Not Generic Workspace Merge

AI-7C should **not** finally implement a generic:

```text
Make Workspace Official

Promote Workspace

Merge arbitrary Workspace → canonical
```

Those are broader product semantics.

AI-7C needs a much narrower operation:

> Commit the exact operations from one approved Plan version to canonical after those exact operations have been staged and verified.

Suggested service concept:

```text
ApprovedPlanCommitService
```

or naming consistent with current project style.

It must reject arbitrary Workspaces.

---

# 30. Canonical Commit Must Apply Operations to the Latest Canonical Project

Never do:

```python
canonical = execution_workspace.working_project
save(canonical)
```

That can overwrite unrelated production state which changed after Workspace creation.

Instead:

```text
acquire short canonical mutation lock
        ↓
reload latest canonical project
        ↓
re-check approved creative source signature
        ↓
apply exact approved ChangeOperations
to this latest in-memory canonical object
        ↓
verify resulting creative signature
        ↓
atomic ProjectStore write
```

This preserves unrelated fields such as:

```text
newly completed image batches
video versions
Job-produced asset metadata
other production state
```

provided they are not themselves touched by the approved creative operations.

---

# 31. Reuse the Pure Projection Core for Canonical Commit

AI-7B added or extracted a pure core equivalent to:

```text
preview_operations_on_project
```

Reuse it.

Conceptual pattern:

```python
async with project_write_lock(project_name):
    current = store.load()

    if creative_source_signature(current) != execution.source_signature:
        raise ProjectChangedAfterApproval()

    projected = preview_operations_on_project(
        current,
        approved_operations,
    )

    if creative_source_signature(projected.project) != approved_preview.after_signature:
        raise ExecutionPreviewMismatch()

    write_commit_intent(...)

    store.save_atomic(projected.project)

    write_commit_complete(...)
```

Adapt to the real function signatures.

Do not copy this literally if current revision core already has a safer helper.

---

# 32. The Final Stale Check Must Occur Inside the Write Critical Section

There are two stale checks:

```text
AI-7C start
        ↓
revalidation

and

immediately before canonical save
        ↓
revalidation again
```

The second check closes the time-of-check/time-of-use window.

Do not rely only on the check performed several seconds earlier before Workspace replay.

---

# 33. Use the Same Canonical Write Serialization Mechanism as Other Project Writes

Before implementing this section, audit current ProjectStore write concurrency.

If the repository already has:

```text
per-project mutation lock
compare-and-swap
transaction helper
file lock
```

reuse it.

If it does not, introduce the smallest shared primitive necessary to prevent lost updates.

Do not hold this lock around:

```text
LLM calls
Agnes calls
character image generation
Production Planner calls
video polling
```

The canonical creative commit critical section should be short.

Add a concurrency test proving that an overlapping legitimate project write is either:

```text
serialized
or
causes an optimistic conflict
```

and is never silently lost.

---

# 34. Write-Ahead Commit Intent

The dangerous crash window is:

```text
canonical project save succeeds
        ↓
process crashes
        ↓
ChangeExecutionSession still says
"canonical_commit pending"
```

Therefore persist a write-ahead commit record before the side effect.

Conceptually:

```python
class CanonicalCommitRecord(BaseModel):
    status: str

    before_signature: str
    expected_after_signature: str

    operation_digest: str

    commit_intent_at: float
    committed_at: float | None = None
```

Persist:

```text
COMMIT_INTENT
```

before canonical save.

Then:

```text
atomic project save
```

then:

```text
COMMIT_DONE
```

---

# 35. Canonical Commit Crash Reconciliation

At startup or resume:

if execution has `COMMIT_INTENT` but no `COMMIT_DONE`:

reload canonical.

Case A:

```text
current creative signature
==
before_signature
```

The commit did not happen.

Resume commit.

Case B:

```text
current creative signature
==
expected_after_signature
```

The commit happened.

Reconcile the execution record to committed.

Do **not** apply again.

Case C:

```text
current signature
matches neither
```

Block:

```text
CANONICAL_COMMIT_RECONCILIATION_CONFLICT
```

Do not guess.

---

# 36. Preserve a Pre-Commit Audit Snapshot

Before canonical commit, save an immutable execution-local snapshot of the relevant canonical creative state.

For example under the current AI output conventions:

```text
change_executions/{execution_id}/
    execution.json
    canonical_before.json
    production_manifest.json
```

Adapt to existing storage conventions.

This snapshot is for:

```text
audit
debugging
reconciliation
future restore tooling
```

Do not build a complete Project Checkpoint / Pocket / Undo product in AI-7C.

Do not expose automatic rollback merely because a snapshot now exists.

---

# 37. After Canonical Commit, Creative Commit Is Not Automatically Rolled Back

If:

```text
creative commit succeeds
        ↓
Production Planner later fails
```

do not automatically revert the creative changes.

The approved creative change is already valid.

Execution should report:

```text
creative_committed = true

production_status = blocked/failed
```

The same execution can later resume production.

Automatic rollback after irreversible generation side effects is more dangerous than leaving truthful partial completion.

---

# 38. Cancellation Semantics Change After Creative Commit

Before canonical commit:

```text
Cancel
→ stop safely
→ canonical unchanged
```

After canonical commit:

```text
Cancel
→ stop future production work
→ committed creative change remains
```

The UI must clearly say this.

Do not pretend cancellation means undo.

If a remote Agnes video request has already been submitted:

preserve existing AI-6F truth:

```text
local cancellation
≠
guaranteed remote cancellation
```

unless Agnes currently provides and the project already implements a real remote-cancel API.

---

# 39. Recompute Production Impact From the Verified Result

Before starting production, build a deterministic production execution manifest.

Inputs:

```text
approved Plan
approved Impact facts
exact committed creative result
existing Production Plans
current asset/version state
```

Do not simply regenerate every asset in every affected Scene.

The goal is:

> regenerate only what the approved creative change actually invalidated, plus deterministic dependency closure.

---

# 40. `ProductionExecutionManifest`

Suggested conceptual structure:

```python
class ProductionExecutionManifest(BaseModel):
    execution_id: str
    project_name: str

    committed_source_signature: str

    affected_character_ids: list[str]
    affected_scene_ids: list[str]
    affected_shot_ids: list[str]

    character_actions: dict[str, str]

    shot_actions: dict[str, ShotProductionActions]

    planner_scene_ids: list[str]

    baseline_asset_refs: dict[str, dict]

    expected_video_requests: int

    production_plan_ids: dict[str, str] = {}

    created_at: float
```

Example action model:

```python
class ShotProductionActions(BaseModel):
    first_frame: str
    audio: str
    last_frame: str
    video: str
```

Values may conceptually be:

```text
keep
regenerate
generate_new
remove_from_timeline
```

Use enums consistent with current style.

---

# 41. Production Impact Rules Must Be Deterministic

Do not ask an LLM:

> Which assets probably need regeneration?

Code decides.

Use the actual supported operation fields.

Conceptual examples:

### Character visual identity / description changes

For Shots referencing that Character:

```text
Script:
KEEP

Storyboard structure:
KEEP unless approved Plan changed it

Character views:
REGENERATE if affected_production mode

first frame:
REGENERATE

last frame:
REGENERATE when required by current execution mode

audio:
KEEP

video:
REGENERATE
```

### Shot visual description / characters / camera-related creative fields

Typically:

```text
first frame:
REGENERATE

last frame:
REGENERATE if used

video:
REGENERATE
```

### Dialogue/audio content change

Typically:

```text
audio:
REGENERATE

video:
REGENERATE when current video timing/content depends on the new audio
```

### Target duration change

Typically:

```text
video:
REGENERATE
```

and allow existing audio/duration validation to determine prerequisites.

### Insert Shot

Generate all required production assets for that new Shot.

### Delete Shot

```text
no new generation

remove from current composition timeline

preserve historical files/runs
```

### Reorder Shots

Do not automatically regenerate every image.

However continuity/opening-source dependencies may change.

Let deterministic dependency analysis + new Production Plans determine whether boundary Shots require regeneration.

Implement only rules supported by real current operations.

---

# 42. Do Not Delete Historical Generated Assets

AI-7C must preserve the existing non-destructive version philosophy.

Character change:

```text
old Character views
→ do not delete files
```

Shot regeneration:

```text
old ImageBatch
old LastFrame batch
old VideoVersion
→ preserve
```

Deleted Shot:

do not recursively delete historical:

```text
PNG
MP4
ProductionRun manifests
```

merely because it is no longer part of current creative structure.

---

# 43. Character Visual Synchronization Is Required

AI-7B correctly treated:

```text
Character description change
```

separately from:

```text
generated Character views
```

AI-7C must now close this production consequence.

Example:

```text
User approved:
"Make Xiaoyu's hair long and black."
```

If AI-7C only updates:

```text
Character.description_en
```

but then first-frame generation still reads old Xiaoyu reference images:

the execution is semantically wrong.

Therefore, when deterministic impact says the current Character visual references are outdated and:

```text
production_mode == affected_production
```

AI-7C must regenerate/update the Character's current reference views before affected Shot production begins.

---

# 44. Reuse the Existing Character Generation Workflow

Do not invent another Character image generator.

Reuse the existing proven Character workflow:

```text
Character description / generation prompt
        ↓
existing chain / three-view generation
        ↓
current project Character views
```

The existing project previously preferred chain generation for view consistency.

Use the current real business implementation.

Do not call Agnes image endpoints directly from new AI-7C orchestration code.

---

# 45. Character Agent Execution Capability

If the Agent Capability Registry still has only:

```text
character.inspect
character.propose_change
```

AI-7C may add a narrowly defined real execution capability owned by Character Agent.

Name according to current conventions, conceptually:

```text
character.views.generate
```

or:

```text
character.reference.generate
```

It means:

> generate current production reference views using the already-approved Character identity.

It does **not** mean:

```text
redesign the Character
rewrite description
invent clothing
change age
change identity
```

No new creative reasoning.

---

# 46. Character Visual Sync Must Be Durable

Do not allow backend restart halfway through:

```text
front view generated
side view pending
```

to restart the entire chain and create uncontrolled duplicates.

First inspect how the existing Character chain persists stages.

If it is already restart-safe:

reuse it.

If not, add the smallest durable stage checkpoint needed.

Every real Agnes request triggered by AI-7C should be recoverable/idempotent using the project's existing:

```text
JobQueue
origin metadata
stable stage keys
or equivalent durable execution mechanism
```

Do not create a second image queue.

---

# 47. Do Not Automatically Regenerate Character Views in `creative_only`

If execution mode is:

```text
creative_only
```

commit the approved Character creative change.

Leave current generated views/history untouched.

Expose:

```text
Character visual references are now outdated.
Production synchronization was not requested.
```

Do not silently spend generation time.

---

# 48. Audio Must Be Handled Explicitly

AI-6F intentionally did **not** silently add:

```text
shot.audio
```

That was correct for AI-6F.

AI-7C now needs to handle the gap because approved Script/Storyboard changes can change:

```text
dialogue
timing
video duration prerequisites
```

If deterministic Impact says audio is stale and the existing video helper requires current audio:

AI-7C must not simply hit a blocker forever.

This is the appropriate phase to explicitly assign the capability.

---

# 49. Add `shot.audio` Only as a Narrow Shot Producer Capability

If current role contracts confirm Shot Producer owns shot-level production assets:

implement:

```text
shot.audio
```

as a real Shot Producer execution capability.

It must wrap the existing shot-level audio business logic.

Do not create:

```text
TTS Agent

Audio Director

second TTS pipeline
```

Do not call Edge TTS directly from Agent orchestration.

Use the current existing helper/Job path.

---

# 50. Audio Execution Must Preserve Existing Timing Semantics

The current video pipeline historically uses audio/timing information for frame count and duration validation.

Do not duplicate those calculations inside `src/agents`.

Shot Producer provides:

```text
scope
authorization
execution request
```

Existing production business code owns:

```text
voice synthesis
duration
word timing
num_frames consequences
```

Add focused tests proving:

```text
dialogue changed
→ old audio not reused incorrectly
→ new audio persisted
→ video preflight sees new audio
```

---

# 51. Character Sync Must Finish Before Shot Production Snapshot Freeze

When Character views are required:

```text
creative commit
        ↓
Character visual sync
        ↓
all required Character views ready
        ↓
Production Plan / Production execution freeze
```

Do not allow:

```text
first half of affected Shots use old views
second half use new views
```

A production run must have one coherent Character reference state.

---

# 52. Production Planner Runs Only After Creative Commit

Do not run Production Planner against the temporary execution Workspace and then later commit something different.

Correct:

```text
approved creative Plan
        ↓
atomic canonical creative commit
        ↓
required Character visual synchronization
        ↓
Production Planner
```

This preserves the existing Production Planner assumption:

```text
plan current committed creative truth
```

---

# 53. Planner Scope: Affected Scenes Only

Do not re-plan 80 Scenes because 2 Scenes changed.

Derive:

```text
planner_scene_ids
```

from deterministic impact.

For affected Scenes:

```text
Production Planner AgentTask
        ↓
production_plan.create
        ↓
existing ProductionPlannerWorkflow
        ↓
production_plans.json
```

Use the existing capability implemented in AI-6E.

---

# 54. All Required Production Planning Must Finish Before First Generation

Preserve the strong AI-5B principle:

> Prepare the entire required production scope first, then begin long-running generation.

Do not:

```text
plan Scene 1
→ spend 45 minutes generating
→ discover Scene 3 Planner fails
```

Instead:

```text
affected Scene 1 plan
affected Scene 2 plan
affected Scene 3 plan
        ↓
all valid
        ↓
all approved
        ↓
freeze production manifest
        ↓
first production Job
```

---

# 55. Reuse Existing Planner Auto-Approval Semantics

AI-5B already established safe project-autopilot semantics for:

```text
generate valid Plan
validate
auto approve
```

when the user has already authorized the automated production workflow.

AI-7C's explicit:

```text
Execute Plan
+
affected_production
```

is equivalent production authorization for the approved affected scope.

Therefore reuse existing:

```text
approve_plan_business
```

or the current safe equivalent.

Do not implement a second Planner approval system.

---

# 56. Planner May Reason About Production — Not Creative Meaning

Production Planner may decide:

```text
opening source
link mode
keyframes vs i2v
generation strategy
```

according to its existing contract.

It may not decide:

```text
rewrite Scene story
change Character identity
add another Shot because it feels better
```

If the Planner believes creative input is insufficient:

```text
BLOCK
```

Do not automatically call Director.

That would create unapproved creative changes.

---

# 57. Production Scope Expansion

Normally AI-7B Impact + AI-7C deterministic rules should predict the required production scope.

After new Production Plans are created, compare the final required production scope against the approved expected scope.

If Planner dependencies unexpectedly require significant additional Shots/assets:

do not silently generate them.

Set:

```text
waiting_user
```

with a structured record:

```text
approved production scope:
12 Shots

new required dependency scope:
15 Shots

additional:
s08_c03
s08_c04
s09_c01
```

Allow a narrow production-only decision such as:

```text
Continue with expanded production scope

or

Stop after committed creative change
```

This decision must not change creative project content.

Normal acceptance cases should not need this path.

---

# 58. Do Not Build Another ProductionRun

AI-6F already established this invariant.

AI-7C must reuse:

```text
SceneAutopilotRun

ProjectProductionRun
```

and existing:

```text
JobQueue
AgentTask settlement
restart recovery
video_id recovery
asset version lineage
composition manifest
```

`ChangeExecutionSession` is an orchestration parent/reference.

It is not another Shot production engine.

---

# 59. Extend Existing Production Runs for Affected-Scope Production

The current historical `ProjectProductionRun` was designed primarily for project-wide production.

AI-7C must not regenerate an entire 400–800 Shot project after a small approved change.

Audit whether the current ProductionRun already supports target subsets.

If not, add backward-compatible optional target scope.

Conceptually:

```python
class ProjectProductionRun(...):
    target_scene_ids: list[str] | None = None
    target_shot_ids: list[str] | None = None
```

Legacy:

```text
None
→ existing full-project behavior
```

AI-7C:

```text
target_shot_ids = affected production closure
```

Do not change old UI/API defaults.

---

# 60. SceneAutopilotRun May Need a Targeted Regeneration Scope

If current `SceneAutopilotRun` always creates a fresh version for every Shot in a Scene, extend it minimally.

Conceptually:

```text
target Shot set

and optionally

per-Shot asset action set
```

Example:

```text
s12_c01:
audio + video

s12_c02:
first_frame + last_frame + video

s12_c03:
video only
```

When omitted:

preserve existing SceneAutopilot behavior.

Do not duplicate its:

```text
checkpoint system
seeded candidate selection
origin_run_id
pause/resume
video version tracking
source dependency handling
```

---

# 61. Use Existing Autopilot Candidate Selection

AI-6F intentionally preserved manual semantics:

```text
generate candidates
→ user chooses
```

while existing `SceneAutopilotRun` already has a deterministic seeded candidate-selection mode for unattended execution.

AI-7C affected-production should reuse that existing automated production mode.

Do not globally make all ordinary `shot.first_frame` AgentTasks auto-select.

Execution-specific automation belongs inside the proven autopilot path.

---

# 62. Continuity Dependencies Must Expand Scope Deterministically

Example:

```text
s12_c04
opening_source = reference_previous
source = s12_c03 final video end
```

If c04 must regenerate but c03 remains unchanged:

reuse the exact frozen compatible version of c03.

If the new approved production plan requires a new c03 output:

include c03 in the dependency closure before production begins.

Do not discover this halfway through an 8-hour run.

---

# 63. Freeze Production Inputs Before Generation

Before the first real image/video generation:

persist:

```text
post-commit creative source signature

Character reference identities

approved Production Plan IDs / snapshots

target Shot/action manifest

baseline selected asset/version IDs

run seed

ChangeExecutionSession ID
```

This is the exact production input contract.

If project creative state changes afterward:

existing ProductionRun stale/source checks should block safely.

Do not silently switch to newer creative truth.

---

# 64. Baseline Assets for Unaffected Shots

For targeted production, freeze current selected asset/version identities for unaffected Shots required for:

```text
continuity
composition
final timeline
```

Do not later compose using:

```text
whatever happens to be current at compose time
```

The existing ProjectProductionRun already established the principle that composition must use exact version IDs, not "latest item in array."

Extend that principle to targeted runs.

---

# 65. Composition

Do not implement an Editor Agent in AI-7C.

If the existing `ProjectProductionRun` already composes the project after production:

reuse it.

For targeted production:

the final composition manifest should combine:

```text
new exact VideoVersion IDs
for regenerated Shots

+

frozen baseline VideoVersion IDs
for unaffected Shots
```

according to the committed post-change Shot order.

Deleted Shots are excluded.

Inserted Shots use new generated versions.

If existing composition supports image/Ken Burns fallback, preserve current semantics.

Do not invent a new compositor.

---

# 66. Production Mode `creative_only`

For:

```text
production_mode = creative_only
```

AI-7C stops after:

```text
atomic creative commit
```

It may still calculate and display:

```text
production consequences
```

but must not:

```text
regenerate Character views
create Production Plans
enqueue generation Jobs
call Agnes image/video
```

Execution status may still be:

```text
completed
```

with:

```text
production_skipped_by_user = true
```

---

# 67. Production Mode `affected_production`

For:

```text
production_mode = affected_production
```

the required flow is:

```text
creative commit

→ Character sync if required

→ audio capability available if required

→ affected Production Plans

→ validate all Plans

→ approve/freeze all Plans

→ targeted durable ProductionRun

→ affected asset generation

→ existing composition if applicable
```

---

# 68. First Agnes Call Hard Gate

Before any new real Agnes image/video request caused by AI-7C:

assert:

```text
canonical creative commit == complete

required Character references == ready

all required Production Plans == approved + fresh

ProductionExecutionManifest == persisted

ProductionRun == persisted

target source signature == current/frozen truth
```

Automated tests should prove:

```text
any failed prerequisite
→ 0 Agnes image/video calls
```

---

# 69. AgentTask / Job / Run Traceability

Every production Job started by AI-7C should be traceable through existing metadata.

Where current schemas permit, record references such as:

```text
change_execution_id

approved_plan_id

production_run_id

scene_run_id

agent_task_id

capability_id
```

Do not copy whole objects into Job metadata.

The question:

> Why was this video generated?

should be answerable:

```text
Job
→ Shot Producer task/run
→ ChangeExecutionSession
→ approved Plan
→ proposal item
→ user request
```

---

# 70. Shot Producer Remains the Real Generation Owner

AI-7C Producer should not directly call:

```text
AgnesClient.generate_image

AgnesClient.create_video

Edge TTS
```

The chain remains:

```text
Producer execution orchestration
        ↓
Shot Producer / existing production executor
        ↓
existing business helper
        ↓
JobQueue
        ↓
AgnesClient / TTS
```

Preserve role ownership.

---

# 71. Preserve AI-6F Idempotency

The following guarantees remain mandatory:

```text
same AgentTask
+ HTTP retry
+ backend restart
+ startup reconciliation
→ no duplicate generation
```

A new explicit production regeneration is allowed to create:

```text
new AgentTask
new ImageBatch
new VideoVersion
```

but recovery of the same request must not.

---

# 72. Preserve Video `video_id` Recovery

If backend restarts after Agnes accepted a video generation request:

resume:

```text
same Job
same AgentTask/run stage
same video_id
```

and poll the existing remote task.

Do not submit another video generation request.

AI-7C must not weaken the AI-6F real-restart proof.

---

# 73. Long Production Must Survive Backend Restart

`ChangeExecutionSession` startup recovery should:

```text
load active executions
```

Then reconcile based on stage.

### revalidate/workspace

resume deterministic execution if safe.

### canonical commit

use COMMIT_INTENT reconciliation.

### character_sync

reattach existing durable generation/checkpoints.

### production_planning

reuse persisted AgentTask / existing plan artifacts.

### production

resume the exact existing ProjectProductionRun.

Never create a second ProductionRun merely because the parent execution restarted.

---

# 74. Planning Crash Reconciliation

Dangerous window:

```text
Production Planner created plan
        ↓
plan saved to production_plans.json
        ↓
backend crashes
        ↓
execution record has not stored plan_id
```

Do not create another plan blindly.

Reuse the same write-ahead / reconciliation philosophy already proven in AI-5B.

Before a Planner side effect:

persist enough identity to rediscover it.

After restart:

search for the exact plan/task produced for:

```text
execution_id
scene_id
planning request
```

where current metadata permits.

If current Planner persistence does not carry origin metadata, add the smallest backward-compatible field.

---

# 75. ProductionRun Creation Crash Reconciliation

Dangerous window:

```text
ProjectProductionRun created
        ↓
run.json persisted
        ↓
execution parent crashes before saving run_id
```

On resume:

search by stable parent identity, e.g.:

```text
change_execution_id
```

or extend current production-run parent/origin metadata minimally.

Reuse existing parent-child duplicate prevention concepts.

Do not create a second run.

---

# 76. Do Not Auto-Replan During Active Production

If a committed project's creative source changes while AI-7C production is running:

use existing source-signature protection.

Stop/block.

Do not:

```text
silently call Planner again
silently change frozen Plans
silently migrate remaining Shots to new source
```

A ProductionRun represents one frozen production version.

---

# 77. User Edits After Creative Commit

After AI-7C commits creative changes and begins production, the user may manually edit the project.

Current durable production semantics should detect source mismatch.

If mismatch affects the run:

```text
ProductionRun BLOCKED
ChangeExecutionSession BLOCKED
```

UI should explain:

```text
The project changed after this production run was prepared.
No new generation has been started beyond the safe checkpoint.
```

Do not undo the user's edit.

---

# 78. Failure Taxonomy

Reuse existing error structures where possible.

Add only execution-specific codes actually needed.

Conceptually:

```text
PLAN_NOT_APPROVED

PLAN_VERSION_MISMATCH

PLAN_EXECUTION_BINDING_MISSING

PROJECT_CHANGED_AFTER_APPROVAL

PROJECT_PRODUCTION_ACTIVE

EXECUTION_PREVIEW_MISMATCH

CANONICAL_COMMIT_CONFLICT

CANONICAL_COMMIT_RECONCILIATION_CONFLICT

CHARACTER_SYNC_FAILED

CHARACTER_REFERENCE_NOT_READY

PRODUCTION_PLAN_FAILED

PRODUCTION_PLAN_INVALID

PRODUCTION_SCOPE_EXPANDED

PRODUCTION_SOURCE_CHANGED

PRODUCTION_RUN_FAILED
```

Do not create a giant abstract exception hierarchy.

---

# 79. Pre-Commit Failure vs Post-Commit Failure Must Be Visible

The UI must clearly distinguish:

### Before canonical creative commit

```text
No project change has been committed.
```

### After canonical creative commit

```text
Creative changes are committed.
Production has not finished.
```

This distinction matters more than a generic:

```text
Execution failed
```

---

# 80. Resume Semantics

Resume is allowed for recoverable post-approval failures.

Examples:

```text
backend restart

temporary Agnes failure

Planner network failure

production Job failure
```

Resume means:

```text
same execution_id

same creative commit

same frozen Plan where still valid

same existing Job/ProductionRun where recoverable
```

Do not restart from the user's original sentence.

---

# 81. A Stale Pre-Commit Execution Is Not Resumable by Rebasing

If execution was blocked because:

```text
PROJECT_CHANGED_AFTER_APPROVAL
```

do not offer:

```text
Resume
```

as if nothing happened.

The correct user action is:

```text
Update / regenerate Change Plan
→ new preview
→ new approval
```

---

# 82. Polling / Realtime

Do not create a new realtime bus merely because AI-7C adds execution progress.

Continue the project's established split:

```text
ChangeExecutionSession
→ polling-first durable state

Production Jobs
→ existing JobQueue SSE + persistence
```

Frontend can poll active execution every approximately current established interval.

When production starts:

reuse existing JobQueue SSE for real Job progress.

Persistence remains truth.

---

# 83. Execution API Surface

Exact URLs should follow current router conventions.

Conceptually AI-7C needs:

```text
POST
.../plans/{version}/execute

GET
.../executions/{execution_id}

POST
.../executions/{execution_id}/resume

POST
.../executions/{execution_id}/cancel

POST
.../executions/{execution_id}/decision
```

The decision endpoint is only for explicit execution-time decisions such as:

```text
production scope expansion
```

Do not expose one public endpoint per internal stage.

---

# 84. Frontend — Approved Plan State

Existing `ChangePlanReview.vue` should preserve AI-7B review behavior.

For an approved Plan show:

```text
✓ Plan approved

This approval has not changed the project yet.

[Execute Plan]
```

Do not silently start execution when the component mounts.

---

# 85. Execute Modal

Before starting, show compact execution consequences.

At minimum:

```text
Creative modules to commit

Character references requiring refresh

Affected Scenes

Affected Shots

Estimated generation work

Production mode
```

Do not hardcode:

```text
1 RPM
```

into UI text if the project already exposes live configured limits.

Use existing limit/rate data where practical.

Allow:

```text
Apply creative changes only

Apply + regenerate affected production
```

The default should be explicit, not hidden.

---

# 86. Execution Progress UI

After start, the Change Plan panel can transition into an execution timeline.

Example:

```text
✓ Revalidated approved Plan

✓ Created execution Workspace

✓ Script changes staged

✓ Character changes staged

✓ Storyboard changes staged

✓ Verified approved Preview

✓ Creative changes committed

● Refreshing Xiaoyu reference views

○ Preparing Production Plans

○ Producing affected Shots

○ Final composition
```

Show real state.

Do not simulate Agent chatter.

---

# 87. Module Owner Presentation

The UI may show:

```text
Script Writer
Character Agent
Director
```

next to deterministic commit steps because they own the approved proposal content.

But do not imply the Agents are currently rethinking.

Better wording:

```text
Applying Script Writer's approved changes
```

rather than:

```text
Script Writer is thinking...
```

when no LLM call is occurring.

---

# 88. Production Progress

Once real production starts, surface:

```text
affected Shots completed / total

current Scene

current Shot

current stage

queued video jobs

current Job status

errors/blockers
```

Reuse current ProductionRun / Job information.

Do not derive progress from chat messages.

---

# 89. Refresh Recovery

Browser refresh during:

```text
creative replay
canonical commit
Character sync
planning
video generation
```

must restore the current execution.

Frontend flow:

```text
load approved Plan

load execution linked to Plan

poll execution

if production_run_id exists:
load production run

Job SSE reconnects
```

No duplicate execution.

---

# 90. Existing Manual Workflows Must Remain Valid

AI-7C must not break:

```text
manual Workspace Preview / Apply

Director selective Workspace apply

manual Character generation

manual Production Planner Studio

manual Scene Autopilot

manual Project Production

direct Shot generation

JobQueue UI

existing Full Project Autopilot
```

AI-7C is an additional orchestration path over existing capabilities.

---

# 91. No Reviewer in AI-7C

After generated assets exist:

do not automatically:

```text
watch video

judge consistency

ask Director to fix Shot

regenerate until "good"
```

That is AI-8 / Reviewer territory.

AI-7C may retry real technical failures according to existing retry rules.

It does not perform creative quality loops.

---

# 92. No Automatic Correction Loop

Forbidden:

```text
video completed
        ↓
Producer dislikes result
        ↓
Director changes prompt
        ↓
regenerate
```

Also forbidden:

```text
Planner output technically valid
        ↓
Producer decides another production strategy is prettier
```

Execution stops at production completion.

Quality evaluation is a separate phase.

---

# 93. No Generic Project Undo

AI-7C stores enough audit state to explain what happened.

Do not use this phase to implement:

```text
Project Pocket
general Checkpoint restore
arbitrary undo tree
```

A future restore feature may use:

```text
canonical_before.json
execution records
operation history
```

later.

---

# 94. No Deletion of Approved Artifacts

After execution completes, preserve:

```text
ChangeSession

ModuleProposals

UnifiedChangePlan

PlanApproval

PlanPreviewArtifact

ChangeExecutionSession

execution Workspace

canonical commit record

ProductionExecutionManifest

ProductionRun references
```

They form the audit lineage.

Do not collapse everything into the current project state.

---

# 95. Gate Philosophy

Do not implement all of AI-7C in one uncontrolled pass.

AI-7C has several fundamentally different risk boundaries:

```text
approved artifact integrity

deterministic creative execution

canonical mutation

production synchronization

long-running Agnes execution
```

Each needs an independent checkpoint.

---

# 96. AI-7C-1 — Execution Protocol + Revalidation

Implement:

```text
ChangeExecutionSession

execution mode

execute endpoint

execution idempotency

approval/plan/version binding

operation bindings/digest

full revalidation

stale blocking

execution persistence

startup discovery
```

Do not mutate Workspace or canonical yet.

Focused Gate:

```text
approved fresh Plan
→ execution created

same POST twice
→ same execution

unapproved Plan
→ rejected

wrong version
→ rejected

stale project
→ blocked

0 Workspace Apply
0 canonical mutation
0 Production Plan mutation
0 Agnes
```

Commit separately.

---

# 97. AI-7C-2 — Execution Workspace + Exact Creative Replay

Implement:

```text
execution Workspace

step records

topological deterministic replay

existing ChangeSet preview/apply

restart reconciliation

final Workspace equivalence check
```

Focused Gate:

```text
all approved operations apply to Workspace

result == AI-7B approved Preview

canonical byte/signature unchanged

restart does not double-apply step

stale Workspace revision blocks

0 production
```

Commit separately.

---

# 98. AI-7C-3 — Atomic Canonical Creative Commit

Implement:

```text
short canonical write critical section

second stale check

latest canonical reload

pure semantic operation apply

preview/signature equivalence

COMMIT_INTENT

atomic ProjectStore write

COMMIT_DONE

crash reconciliation

pre-commit audit snapshot
```

Mandatory crash tests:

### Window A

Crash before canonical save.

Resume:

```text
commit once
```

### Window B

Canonical save succeeds, crash before `COMMIT_DONE`.

Resume:

```text
detect expected_after_signature
mark committed
0 second apply
```

### Window C

Canonical changed to unrelated creative truth before commit.

```text
block
0 canonical overwrite
```

### Production-state preservation

Create/update unrelated generated asset metadata after Plan approval.

Canonical commit must preserve that current production state rather than replacing it with stale Workspace bytes.

Commit separately.

---

# 99. AI-7C-4 — Production Impact + Character / Audio Synchronization

Implement:

```text
ProductionExecutionManifest

deterministic asset action rules

Character reference synchronization

character execution capability if required

shot.audio capability if required

durable generation checkpointing

creative_only mode

affected_production mode
```

Focused Gates:

### Appearance-only Character change

```text
Character description committed

Script unchanged

Storyboard structure unchanged

old views preserved historically

new current views generated in affected_production

affected Shot frames/videos marked for regeneration
```

### Dialogue change

```text
new audio generated

video production uses current audio/timing

unaffected audio not regenerated
```

### creative_only

```text
0 Agnes
0 audio generation
production consequences reported
```

Commit separately.

---

# 100. AI-7C-5 — Affected Production Planner + Scoped ProductionRun

Implement:

```text
affected Scene planning

all-plan-before-production gate

existing Planner validation

existing Planner approval path

scope-expansion protection

targeted ProjectProductionRun support

targeted SceneAutopilot support if required

exact baseline version manifest

existing Shot Producer / JobQueue execution

composition using exact version IDs
```

Do not start live Agnes acceptance until FakeAgnes/integration gates pass.

Focused Gate:

```text
change affects 3 Shots in a 50-Shot project

→ no 50-Shot regeneration

only deterministic affected/dependency closure runs
```

Commit separately.

---

# 101. AI-7C-6 — UX + Resume + Cancellation

Implement/finish:

```text
Execute Plan button

execution mode modal

execution timeline

pre/post-commit state wording

polling

JobQueue production progress

resume

cancel

waiting_user production scope expansion

refresh restoration
```

No visual redesign of Team Chat.

No new realtime bus.

Commit separately.

---

# 102. AI-7C-7 — Final Behavioral Gates

This checkpoint is integration/acceptance.

Do not postpone all testing until here; every previous checkpoint already needs focused tests.

Final cases:

---

## Case A — Small Storyboard-Only Change

Approved Plan:

```text
update one Shot description
```

Expected:

```text
revalidate

workspace replay

preview equivalence

one atomic creative commit

affected Scene re-planned

only required Shot production regenerated

historical VideoVersion preserved

new VideoVersion becomes run-selected result
```

---

## Case B — Cross-Module Script + Character + Storyboard

Approved request:

```text
Make Xiaoyu's hair long and black,
change the ending so she rescues Aran,
and make the final fight more tragic.
```

Expected:

```text
Script operations

Character operations

Storyboard operations

all staged first

one canonical creative commit

Xiaoyu views regenerated

only affected Scenes re-planned

affected Shot assets regenerated

unaffected project assets untouched
```

---

## Case C — Appearance-Only Character Change

Expected:

```text
Character changed

0 Script rewrite

0 automatic Shot structure rewrite

Character views refreshed

affected Shot visuals regenerated

audio preserved
```

This protects against over-cascade.

---

## Case D — Stale After Approval

```text
approve Plan

modify creative canonical project

Execute
```

Expected:

```text
PROJECT_CHANGED_AFTER_APPROVAL

0 Workspace mutation if blocked before workspace creation,
or no canonical mutation if discovered later

0 Production Plan mutation

0 Agnes
```

---

## Case E — Crash After Canonical Save

Simulate:

```text
COMMIT_INTENT saved

canonical atomic save succeeds

process dies before COMMIT_DONE
```

Restart.

Expected:

```text
same execution recovered

canonical recognized as expected_after state

0 second creative commit

execution continues
```

---

## Case F — Planning Failure After Creative Commit

```text
creative commit succeeds

Planner fails for affected Scene
```

Expected:

```text
creative state remains committed

execution = failed/blocked at production_planning

no automatic creative rollback

resume retries Planner stage only
```

---

## Case G — Production Restart

Run one affected video generation.

After remote video submission:

```text
capture execution_id
AgentTask/run IDs
Job ID
video_id

restart backend
```

Expected:

```text
same ChangeExecutionSession

same ProductionRun

same Job

same video_id

0 second create_video request

production resumes polling

exact asset version persists

execution eventually settles
```

---

## Case H — Bounded Large Project

Fixture:

```text
50+ Scenes
400–800 Shots
```

Approved change affects a small bounded subset.

Prove:

```text
impact calculation is bounded

Planner only runs affected Scenes

ProductionRun targets affected/dependency closure

no whole Storyboard prompt dump

no whole-project regeneration
```

Do not require fragile millisecond benchmarks.

---

# 103. Real Agnes Acceptance

After all automated FakeAgnes/integration gates pass:

perform one minimal real-network AI-7C production acceptance.

Do not use a valuable user project.

Use a safe test project / disposable Shot.

The smallest useful live path is:

```text
approved tiny Change Plan

→ execute

→ creative commit

→ required Production Plan

→ real first frame

→ real last frame if current plan requires it

→ real video

→ durable final asset
```

If the case includes Character view regeneration, one small Character sync can also be tested.

Do not make large live calls merely for Gate count.

---

# 104. Mandatory Real Restart Stability Proof

Because AI-7C now claims autonomous production execution:

perform one controlled backend restart during the live video path.

The final report must explicitly state whether this real restart acceptance was performed.

If environment/network prevents it:

do not falsely claim:

```text
full live restart stability PASS
```

Report the automated proof separately from the missing live proof.

---

# 105. Regression Suite

After focused tests:

run existing:

```text
AI-7A tests

AI-7B tests

revision / Workspace tests

Director Proposal tests

Production Planner tests

Shot Producer tests

Autopilot tests

ProjectProductionRun tests

FullProjectRun tests

JobQueue tests

full backend suite

frontend typecheck

Vite build
```

Distinguish:

```text
pre-existing frontend errors

from

new AI-7C errors
```

Do not fix unrelated technical debt.

---

# 106. Browser Acceptance

Run a real browser flow after backend restart.

At minimum:

```text
open Team Chat

create / load approved Plan

open Change Plan Review

confirm approved state does NOT auto execute

click Execute Plan

see revalidation

see module staging

see canonical commit state

refresh

execution restores

see production preparation

see real production progress

refresh again

progress restores

completion state is truthful
```

Also test:

```text
stale Plan
```

and:

```text
cancel before commit
```

and:

```text
cancel after commit
```

with correct wording.

---

# 107. Hard Mutation Boundary Tests

Before canonical commit:

assert:

```text
canonical creative state unchanged
```

After canonical commit:

assert:

```text
only approved creative operations changed creative state
```

Unrelated generated state must remain identical/preserved.

Before production gate opens:

assert:

```text
0 Agnes image/video calls
```

---

# 108. Permission Tests

Prove:

```text
READ_ONLY cannot execute Plan

PROPOSAL_ONLY cannot execute Plan

unapproved Plan cannot execute

wrong Plan version cannot execute

Producer direct chat cannot bypass execution endpoint

Script Writer cannot directly mutate Character

Character Agent cannot directly mutate Storyboard

Director cannot directly mutate Script

Production Planner cannot mutate creative project

Shot Producer cannot alter approved creative meaning
```

---

# 109. Execution Lineage Acceptance

For at least one completed production result, prove the lineage can be reconstructed:

```text
final VideoVersion
        ↓
Job / SceneRun / ProjectRun
        ↓
ChangeExecutionSession
        ↓
UnifiedChangePlan version
        ↓
PlanApproval
        ↓
ModuleProposal item
        ↓
original ChangeRequest
```

Exact implementation can use existing IDs.

Do not duplicate lineage if current models already contain sufficient references.

---

# 110. Definition of Done

AI-7C is complete only when every answer below is **YES**.

### Approval / execution boundary

```text
Does Plan approval still stop without mutation?

Does execution require an explicit second action?

Is execution bound to one exact Plan version and approval?
```

### Revalidation

```text
Is current creative truth rechecked before execution?

Is it rechecked again immediately before canonical commit?

Does stale state block instead of auto-rebase?
```

### Creative execution

```text
Are the exact AI-7B operations executed?

Are creative Specialists prevented from silently re-deciding content?

Are all module changes staged in an isolated Workspace first?

Does final staged state equal the approved Preview?
```

### Canonical commit

```text
Is canonical creative state committed atomically?

Can a crash after save reconcile without double apply?

Are unrelated production fields preserved?

Is generic Workspace→canonical merge still absent?
```

### Production synchronization

```text
Can changed Character visual identity refresh current references?

Can dialogue/audio changes use a real shot.audio path when required?

Are historical assets preserved?
```

### Planning

```text
Are only affected Scenes re-planned?

Are all required Plans ready before real generation starts?

Are Planner results validated and approved through existing logic?
```

### Production

```text
Is existing durable production infrastructure reused?

Can production target the affected/dependency scope rather than the whole project?

Are exact asset/version IDs tracked?

Do JobQueue and Agnes rate limits remain authoritative?

Does restart avoid duplicate generation?
```

### UX

```text
Can the user see whether creative commit has happened?

Can refresh restore execution?

Do cancel semantics remain truthful?

Does the UI distinguish waiting_user / blocked / failed?
```

### Regression

```text
Does existing Workspace still work?

Does Director Proposal still work?

Does Production Planner Studio still work?

Does Shot Producer direct execution still work?

Does Scene Autopilot still work?

Does Full Project Autopilot still work?

Do manual Character generation flows still work?
```

---

# 111. Explicit AI-7C Stop

When all AI-7C Gates pass:

**STOP.**

Do not continue into:

```text
AI-8

visual Reviewer

automatic video quality judgment

automatic continuity correction

Reviewer → Director correction

Reviewer → Character correction

automatic regenerate-until-good loop

recursive Agent self-improvement

autonomous unapproved creative correction
```

The correct final state is:

```text
User Request
        ↓
AI-7A Understand
        ↓
AI-7B Propose
        ↓
User Preview
        ↓
User Approves
        ↓
AI-7C Explicit Execute
        ↓
Revalidate
        ↓
Stage Exact Approved Operations
        ↓
Verify Approved Result
        ↓
Atomic Creative Commit
        ↓
Synchronize Required Production Inputs
        ↓
Production Planner
        ↓
Durable Targeted Production
        ↓
Real Generated Result
        ↓
STOP
```

No automatic creative review loop.

---

# 112. Final Architectural Principle

AI-7A proved:

> **The Agent Team can understand the project without changing it.**

AI-7B proved:

> **The Agent Team can agree on what should change and show the exact result before execution without changing the project.**

AI-7C must now prove:

> **The system can execute exactly what the user approved, commit that creative change safely as one coherent project state, propagate only the production consequences that actually follow from it, survive crashes and many-hour generation without duplicate work, and produce real updated assets without allowing execution-time Agents to silently change the approved creative decision.**

That is the full purpose of AI-7C.

---

# 113. Recommended Commit Cadence

Keep each Gate independently revertible.

Suggested commit sequence:

```text
feat(agent): add AI-7C approved-plan execution protocol and revalidation

feat(agent): add execution workspace replay and preview-equivalence gate

feat(revision): add atomic approved-plan canonical creative commit

feat(agent): add AI-7C production impact and reference synchronization

feat(production): add scoped approved-change production execution

feat(ui): add approved-plan execution and durable progress UX

test(agent): add AI-7C final execution and production gates
```

Adapt commit wording to current repository convention.

Do not squash all AI-7C work into one giant commit before validation.

---

# 114. Completion Report

When AI-7C implementation is complete, report using this structure:

```text
AI-7C — Approved Change Plan Execution & Production Orchestration

1. Git baseline
   - AI-7B final commit
   - §122 browser acceptance result

2. AI-7C commits

3. Files added

4. Files modified

5. ChangeExecutionSession
   - schema
   - status/stage
   - persistence
   - restart behavior

6. Execution authorization
   - APPROVED_PLAN_EXECUTION
   - exact Plan/Approval binding
   - idempotent start

7. Revalidation
   - checks performed
   - stale behavior
   - no-auto-rebase proof

8. Plan Step → Operation binding
   - traceability
   - operation digest

9. Execution Workspace
   - creation
   - per-step replay
   - revision behavior
   - restart reconciliation

10. Preview equivalence
    - how final staged state is compared with AI-7B Preview

11. Canonical creative commit
    - latest canonical reload
    - write serialization
    - second stale check
    - atomic save

12. Commit crash reconciliation
    - COMMIT_INTENT
    - before/after signature cases

13. Production-state preservation
    - proof unrelated assets were not overwritten

14. Character visual synchronization
    - existing workflow reused
    - durability
    - history preservation

15. shot.audio
    - whether required
    - ownership
    - existing helper reused
    - timing behavior

16. ProductionExecutionManifest
    - affected characters
    - affected scenes/shots
    - asset actions
    - baseline asset refs

17. Production Planner
    - affected-scope behavior
    - plan creation
    - validation
    - auto-approval
    - all-plan-before-production gate

18. Scoped ProductionRun
    - ProjectProductionRun changes
    - SceneAutopilotRun changes
    - backward compatibility

19. Shot Producer integration
    - capabilities used
    - AgentTask/Job traceability

20. Historical asset preservation

21. Composition
    - new version IDs
    - unaffected baseline IDs
    - final output behavior

22. Idempotency
    - duplicate execute
    - duplicate step
    - duplicate canonical commit
    - duplicate Planner
    - duplicate Agnes submission

23. Backend restart recovery

24. Cancellation
    - before creative commit
    - after creative commit
    - during remote Agnes work

25. Failure/blocker taxonomy

26. Frontend execution UX

27. Polling / Job SSE behavior

28. Focused checkpoint Gate results

29. Core Cases A–H

30. Real Agnes acceptance
    - exact requests made
    - assets produced

31. Real mid-video backend restart acceptance

32. Large-project bounded-scope proof

33. Full backend regression
    - previous baseline
    - new test count
    - regressions

34. Frontend typecheck
    - new errors
    - pre-existing errors

35. Vite build

36. Browser acceptance

37. Explicit proof:
    - Plan approval alone still causes 0 mutation
    - stale approved Plan causes 0 mutation
    - creative Specialists do not re-decide approved content
    - canonical creative commit happens at most once
    - unrelated production state is preserved
    - no whole-project regeneration for bounded changes
    - no duplicate Agnes video submission after restart
    - historical assets are preserved
    - existing manual Workspace / Planner / Shot Producer flows still work
    - no Reviewer / AI-8 behavior was introduced

38. Known limitations

39. AI-7C acceptance:
    PASS / FAIL

40. STOP
```

Do not begin AI-8 as part of this task.
