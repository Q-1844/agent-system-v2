# AI-7B — Agent Change Proposal & Unified Change Plan

## Natural Language → Specialist Proposals → Producer Change Plan → Deterministic Change Preview → Visual Diff

---

# 0. Phase Positioning

AI-7A is complete and committed.

Do **not** reopen or redesign AI-7A.

The established roadmap remains:

```text
AI-7A
Project Intelligence
Read-only Producer
Team Chat
Specialist consultation
Read-only change analysis

↓

AI-7B
Agent Proposal
Natural language
→ real Specialist proposals
→ Unified Change Plan
→ deterministic ChangeOperations
→ Visual Diff / Impact
→ user approval

NO APPLY

↓

AI-7C
Agent Action
→ revalidate approved plan
→ Apply ChangeSets
→ module commits
→ existing workflows
→ Production Planner
→ Shot Producer
→ real production
```

AI-7B is therefore the **proposal bridge** between:

```text
AI understands the project
```

and:

```text
AI is allowed to change the project
```

The most important phase invariant is:

> **AI-7B may decide and demonstrate exactly what should change, but it may not make that change real.**

---

# 1. Exact Success Condition

AI-7B succeeds when the following statement is truthful:

> A user can describe a project modification in normal language to the Producer or to an appropriate Specialist. AGNES identifies the affected creative modules, asks the correct module Owners for structured proposals, computes deterministic dependency and impact facts, resolves or surfaces conflicts, compiles a unified cross-module Change Plan, validates that plan, deterministically converts the accepted professional intentions into supported semantic ChangeOperations, and presents a real Visual Diff and Impact preview based on the exact current project state — while canonical creative state, Creative Workspace state, Production Plans, generated assets and production Jobs remain unchanged.

A representative flow must work:

```text
User:

"Make Xiaoyu's hair long and black,
change the ending so she rescues Aran,
and make the final fight more tragic."

        ↓

Producer
understands request

        ↓

ChangeSession created

        ↓

┌─────────────────┬─────────────────┬─────────────────┐
│ Script Writer   │ Character Agent │ Director        │
│ proposal        │ proposal        │ proposal        │
└─────────────────┴─────────────────┴─────────────────┘

        ↓

Dependency Facts
Impact Facts
Conflict Analysis

        ↓

optional targeted second round

        ↓

Producer
Unified Change Plan

        ↓

Plan Validator

        ↓

deterministic proposal compilers

        ↓

ChangeOperation[]
PREVIEW ONLY

        ↓

Aggregate Visual Diff
+
Impact Report

        ↓

User sees:

What you asked to change
Related changes
What remains unchanged
Production consequences
Decisions still required

        ↓

[Approve Plan]
[Request Changes]
[Reject]

        ↓

Approve Plan

        ↓

status = APPROVED_FOR_EXECUTION

        ↓

STOP

0 Apply
0 project mutation
0 production
```

---

# 2. Hard Stop Boundary

AI-7B is **not AI-7C**.

AI-7B must not:

```text
apply a Workspace ChangeSet

modify canonical project.json

modify Workspace working_project.json

promote a Workspace

merge Workspace → canonical

run Production Planner automatically

modify Production Plans

call Shot Producer

generate first frames

generate last frames

generate audio

generate video

compose final video

trigger JobQueue production jobs

regenerate affected assets

delete historical assets

perform autonomous retries that mutate project state

start Reviewer correction loops
```

The user may approve a proposal.

Approval means:

```text
"This plan is approved for a future execution phase."
```

It must **not** mean:

```text
"Apply now."
```

No button, API, runtime path or internal callback in AI-7B may turn proposal approval into project mutation.

---

# 3. Read the Real Repository Before Changing Anything

Before implementation, perform a baseline audit of the current committed repository.

Do not assume old execution-document filenames still exactly match the final AI-7A implementation.

Inspect at minimum:

```text
src/agents/**
routers/agents.py
tests/test_agents*.py

AI-7A Project Intelligence implementation
AI-7A Team Chat persistence
AI-7A ChangeRequestDraft
AI-7A ChangeAnalysisSession
AI-7A UnifiedChangePlanDraft
AI-7A Specialist consultation runtime
AI-7A polling-first frontend
AI-7A stale/restart/idempotency logic

src/revision/**
routers/workspaces.py
Creative Workspace models/store
ChangeOperation models
ChangeSet preview
ChangeSet apply
Impact Engine
Visual Diff
creative source signature
protected/locked handling

Director Proposal Runtime
storyboard.propose_change
Director semantic proposal models
Director proposal compiler
Director Proposal persistence
Director selective preview/apply path

Agent Registry
Capability Registry
AgentSession
AgentTask
ModelProfileRegistry
role contracts under docs/agents/** if present

Project / Scene / Shot / Character current models
ProjectStore
Production Plan store/readers
```

Confirm the committed AI-7A baseline tests before implementing AI-7B.

Record:

```text
baseline commit
baseline test count
frontend build status
current dirty files
```

Do not mix unrelated cleanup into AI-7B.

---

# 4. Locked Existing Architecture

These existing systems are now dependencies, not redesign targets.

## AI-6A

```text
Creative Workspace
Semantic ChangeOperation
ChangeSet validation
Impact Engine
Visual Diff
revision checks
before-value checks
source signatures
stable Shot IDs
crash-safe apply
```

LOCKED.

## Director Proposal Runtime

```text
LLM creative intention
→ semantic Director proposal
→ deterministic compiler
→ ChangeOperation
→ Preview
```

LOCKED as a design pattern.

AI-7B may extract reusable lower-level helpers.

It must not break the existing direct Director workflow.

## AI-7A

```text
Project Intelligence
Evidence references
bounded project reads
Team Chat
Direct Agent Chat
Specialist consultations
persistent sessions
persistent analysis
restart recovery
polling-first UI
read-only execution guard
```

LOCKED.

AI-7B extends these systems.

Do not replace them.

---

# 5. The Fundamental AI-7B Architecture

The final architecture should be:

```text
User Natural Language
        ↓
ChangeRequest
        ↓
ChangeSession
        ↓
Producer Routing
        ↓
Module Owners
        ↓
ModuleProposal[]
        ↓
Deterministic Dependency Facts
        ↓
Deterministic Impact Facts
        ↓
Conflict Analysis
        ↓
optional targeted second round
        ↓
Producer Change Plan Compiler
        ↓
UnifiedChangePlan
        ↓
ChangePlanValidator
        ↓
Module Proposal Compilers
        ↓
existing/new ChangeOperation[]
        ↓
Proposal Projection Engine
        ↓
existing revision validation
existing Impact Engine
existing Diff builders
        ↓
PlanPreviewArtifact
        ↓
Team Chat Visual Diff
        ↓
User Approval
        ↓
APPROVED_FOR_EXECUTION
        ↓
STOP
```

The LLM must never be the mutation engine.

---

# 6. Add a New Runtime Mode: `PROPOSAL_ONLY`

AI-7A should already have an equivalent of a read-only execution boundary.

Do not weaken it.

Add a second explicit mode:

```python
class AgentExecutionMode(str, Enum):
    READ_ONLY = "read_only"
    PROPOSAL_ONLY = "proposal_only"
    # existing normal modes remain unchanged
```

Conceptually:

```text
READ_ONLY

may:
inspect

may not:
propose mutation
compile mutation
apply mutation
produce


PROPOSAL_ONLY

may:
inspect
generate structured professional proposals
generate semantic change intentions
participate in ChangeSession

may not:
apply
commit
generate assets
run production
```

Capability authorization must distinguish:

```text
inspect
propose
mutate
produce
```

For example:

```python
if ctx.mode == AgentExecutionMode.READ_ONLY:
    if operation != "inspect":
        raise AgentPermissionError(...)

if ctx.mode == AgentExecutionMode.PROPOSAL_ONLY:
    if operation not in {"inspect", "propose"}:
        raise AgentPermissionError(...)
```

Do not depend on prompts such as:

```text
"Please do not modify anything."
```

The guard must exist in code.

---

# 7. AI-7B Agent Permission Matrix

## Producer

May:

```text
inspect project
create ChangeRequest
create ChangeSession
choose Specialists
delegate proposal tasks
read ModuleProposals
read deterministic impact facts
read conflict facts
request one targeted second round
compile UnifiedChangePlan
request user decisions
validate proposal state
present plan
record user approval/rejection
```

May not:

```text
write Script content itself
write Character content itself
design Shots itself
invent professional module content
Apply ChangeSet
modify project
start production
```

---

## Script Writer

Owns proposal content for:

```text
story_text
Scene narrative content
Scene structure
Scene ordering
Scene character requirements
```

May:

```text
story.inspect
story.propose_change
```

May not:

```text
modify Character identity
design final Storyboard Shots
apply its proposal
modify generated assets
```

---

## Character Agent

Owns proposal content for:

```text
Character creation
Character removal
Character textual visual identity
Character description
Character identity requirements
```

May:

```text
character.inspect
character.propose_change
```

May not:

```text
rewrite story
redesign Shot structure
generate replacement assets in AI-7B
apply its proposal
```

---

## Storyboard Director

Owns proposal content for:

```text
Shot creation
Shot deletion
Shot ordering
Shot creative description
Shot character composition
Shot duration
other fields already supported by revision layer
```

May:

```text
storyboard.inspect
storyboard.propose_change
```

Reuse the real Director semantic Proposal Runtime.

Do not create a second Director.

---

## Production Planner

Normally **not a creative ModuleProposal owner in AI-7B**.

It may provide read-only feasibility facts when specifically useful.

It must not produce a new Production Plan.

The established boundary remains:

```text
creative changes finalized
        ↓
creative state committed
        ↓
Production Planner
```

That begins in AI-7C or later.

---

## Shot Producer

Read-only status/asset inspection only.

Never participate as a creative proposal author.

---

# 8. Upgrade AI-7A Draft Models Instead of Creating Parallel Systems

AI-7A intentionally introduced non-executable draft objects.

AI-7B should evolve those objects.

Do not create:

```text
ChangeSystemV2
ChangeSession2
AgentPlanNew
```

Preferred evolution:

```text
ChangeRequestDraft
        ↓
ChangeRequest

ChangeAnalysisSession
        ↓
ChangeSession

ModuleConsultation
        ↓
ModuleProposalTask / ModuleProposal

UnifiedChangePlanDraft
        ↓
UnifiedChangePlan
```

Old persisted AI-7A data must remain loadable.

Use one of:

```text
schema_version migration
from_dict compatibility
explicit legacy loader
```

Do not invalidate existing Team Chat history merely because AI-7B adds proposal capability.

---

# 9. Schema Versioning

Add explicit schema versioning if AI-7A persistence does not already have it.

For example:

```python
schema_version: int = 2
```

Legacy objects should map safely.

Example:

```text
AI-7A ChangeAnalysisSession
status=ready
plan_draft_id=...

→ load as historical read-only analysis session

NOT
→ silently reinterpret as executable AI-7B proposal
```

Historical AI-7A plans stay:

```text
analysis_only
```

Only newly created AI-7B sessions can become:

```text
proposal_only
```

---

# 10. `ChangeRequest`

Replace the draft-only request for new AI-7B flows.

Conceptually:

```python
class ChangeRequest(BaseModel):
    request_id: str
    project_name: str

    source: Literal[
        "team_chat",
        "direct_agent_chat",
    ]

    source_agent_id: str | None = None
    source_session_id: str | None = None
    source_message_id: str | None = None

    original_user_request: str

    primary_module: str | None = None
    suspected_modules: list[str] = []

    entity_refs: list[ProjectEvidenceRef] = []

    source_snapshot_signature: str

    mode: Literal["proposal_only"] = "proposal_only"

    created_at: float
```

The original request must remain immutable.

Do not replace it with Producer's interpretation.

---

# 11. `ChangeSession`

One coordinated modification discussion becomes one durable `ChangeSession`.

Suggested shape:

```python
class ChangeSession(BaseModel):
    session_id: str
    project_name: str

    request_id: str

    status: Literal[
        "routing",
        "consulting",
        "analyzing_dependencies",
        "resolving_conflicts",
        "awaiting_user_decision",
        "synthesizing_plan",
        "validating_plan",
        "building_preview",
        "ready_for_review",
        "approved_for_execution",
        "rejected",
        "stale",
        "blocked",
        "failed",
        "cancelled",
    ]

    source_snapshot_signature: str

    consultation_round: int = 0

    module_proposal_ids: list[str] = []

    impact_fact_ids: list[str] = []
    conflict_ids: list[str] = []

    active_plan_id: str | None = None
    plan_version: int = 0

    pending_decision_ids: list[str] = []

    error: str | None = None

    created_at: float
    updated_at: float
```

Persist every state transition.

---

# 12. Do Not Use Free-Form Agent Group Chat as the Coordination Engine

The frontend may look conversational.

The backend must remain structured.

Never implement:

```text
while agents_disagree:
    send_every_message_to_every_agent()
```

The backend should know:

```text
which round

which Agent

which exact question

which project evidence

which proposal artifact

which dependency fact

which conflict

which decision

which final plan version
```

Team Chat is the human-facing visualization of that state.

It is not the state itself.

---

# 13. `ModuleProposal`

Each domain Owner returns a structured professional proposal.

Conceptually:

```python
class ModuleProposal(BaseModel):
    proposal_id: str

    change_session_id: str

    module: Literal[
        "script",
        "character",
        "storyboard",
    ]

    owner_agent_id: str

    round: int

    status: Literal[
        "pending",
        "running",
        "completed",
        "failed",
        "superseded",
    ]

    summary: str

    items: list["ModuleProposalItem"]

    dependency_notes: list["ProposalDependencyNote"] = []

    blockers: list[str] = []

    user_decisions_needed: list[str] = []

    evidence_refs: list[ProjectEvidenceRef] = []

    source_snapshot_signature: str

    agent_task_id: str | None = None

    created_at: float
    updated_at: float
```

The Proposal is a durable artifact.

Do not rely on raw assistant prose as the actual proposal.

---

# 14. `ModuleProposalItem`

Every professional change should have a server-generated stable item ID.

Conceptually:

```python
class ModuleProposalItem(BaseModel):
    item_id: str

    module: str

    action: str

    target_refs: list[ProjectEvidenceRef] = []

    intent: dict

    reason: str

    source: Literal[
        "user_requested",
        "downstream_impact",
    ]

    required: bool = True

    depends_on_item_ids: list[str] = []
```

Do not ask the LLM to invent:

```text
item_id
ChangeSet ID
Workspace ID
revision
before values
stable entity IDs
signatures
```

Those remain deterministic backend responsibilities.

---

# 15. Structured Output Is Mandatory

Specialists should return a typed structured result, not an essay that Producer later tries to regex.

Use the project's current Pydantic/dataclass conventions.

The current OpenAI Agents documentation uses typed `output_type` schemas for exactly this class of structured Agent result, and continues to distinguish manager/orchestrator systems from handoff systems. AGNES should keep its own runtime, but the pattern is a useful reference. ([OpenAI GitHub][1])

A Specialist result should conceptually contain:

```text
summary

proposal items

required dependencies

current-state evidence

downstream concerns

blockers

user decisions needed
```

No hidden reasoning.

No chain-of-thought.

---

# 16. Script Writer Proposal Runtime

Add a real:

```text
story.propose_change
```

if it does not already exist.

The Script Writer should receive:

```text
original user request

current relevant story_text section

relevant Scene summaries

relevant Scene order

Character names relevant to those Scenes

current project evidence

other Proposal summaries only when needed

deterministic dependency facts already known

exact question from Producer
```

Do not send the entire project by default.

---

# 17. Script Writer Semantic Intent Types

The Script Writer must not emit raw ChangeOperations.

Suggested semantic intentions:

```text
update_story_text

update_scene

insert_scene

delete_scene

reorder_scenes
```

Examples:

```json
{
  "action": "update_scene",
  "scene_id": "s31",
  "new_summary": "Xiaoyu reaches Aran before the collapse...",
  "reason": "The requested ending requires Xiaoyu to rescue Aran."
}
```

or:

```json
{
  "action": "insert_scene",
  "after_scene_id": "s31",
  "summary": "Xiaoyu finds a second route into the ruined station.",
  "characters": ["Xiaoyu"]
}
```

Do not include:

```text
before values
permanent new scene ID
revision
ChangeOperation JSON
```

---

# 18. Character Agent Proposal Runtime

Add:

```text
character.propose_change
```

The Character Agent receives:

```text
user request

current Character description

style

current Character usage

affected Scene IDs

affected Shot IDs

current generated Character view metadata

other relevant proposal summaries
```

It returns semantic intentions.

---

# 19. Character Semantic Intent Types

First AI-7B version should support:

```text
update_character

insert_character

delete_character
```

Do **not** implement Character rename casually.

Current AGNES history has used Character names as references in Scene/Shot data.

Therefore:

> If the current repository still uses `Character.name` as identity, treat the name as immutable in AI-7B.

If the real current model now has a stable `character_id`, use that existing ID.

Do not introduce a hidden identity migration merely for rename support.

A rename request may be converted into:

```text
needs_user_decision / unsupported_in_this_phase
```

unless the current model already makes rename safe.

---

# 20. Character Appearance Changes

A request such as:

```text
"Make Xiaoyu's hair long and black."
```

should normally produce:

```text
update_character.description_en
```

It must **not** directly modify:

```text
Character.views
first frames
last frames
videos
asset files
```

Existing images/videos become:

```text
historical existing assets
+
impact/review facts
```

They are never deleted automatically.

Real regeneration begins later.

---

# 21. Reuse the Director Proposal Runtime

Do not build a second Storyboard proposal engine.

AI-7B should reuse:

```text
Director semantic Proposal generation

Director proposal validation

Director proposal item schema where appropriate

Director deterministic compiler helpers
```

However, the current direct Director workflow may also:

```text
create Workspace
preview
select subset
apply to Workspace
```

That behavior belongs to the existing Director feature.

The AI-7B **Team ChangeSession path** must use only the proposal-generation/semantic-compilation portion.

It must never call the Director's Apply path.

If necessary, refactor the Director service into layers such as:

```text
generate_semantic_proposal(...)

compile_semantic_items(...)

direct_director_workspace_flow(...)
```

The existing direct UI continues using all three.

AI-7B uses only the safe lower layers.

Regression-test the old Director workflow after this refactor.

---

# 22. Producer Does Not Write Professional Content

This rule is absolute:

```text
Script content
→ Script Writer

Character content
→ Character Agent

Storyboard content
→ Director

Cross-module combination / ordering
→ Producer
```

Producer may decide:

```text
which proposal items belong in the final plan

which modules are affected

which steps depend on which steps

which Specialists need another question

which conflicts are factual

which conflict needs user choice
```

Producer may not rewrite a Script Writer's proposed Scene itself.

If Producer thinks a Script proposal is incompatible with the Storyboard:

```text
Producer
→ ask Script Writer / Director to revise
```

not:

```text
Producer
→ secretly invent new Script content
```

---

# 23. Producer Change Plan Compiler

The Producer should not simply receive three blobs of text and answer:

```text
"Here is the best plan."
```

The Change Plan should be **compiled**.

Inputs:

```text
ChangeRequest

Project Intelligence evidence

ModuleProposal[]

deterministic Dependency Facts

deterministic Impact Facts

Conflict Facts

user decisions

source signature
```

Output:

```text
UnifiedChangePlan
```

This is the central AI-7B capability.

---

# 24. First-Round Specialist Routing

Producer first identifies candidate modules.

Examples:

```text
"Make Xiaoyu's jacket black"
→ Character

"Change the ending"
→ Script

"Make Scene 18 faster"
→ Storyboard

"Remove the mechanical dog after Scene 12"
→ Script
+ likely Character
+ Storyboard

"Add a new girl who appears in the ending"
→ Script
+ Character
+ Storyboard likely
```

Routing may combine:

```text
LLM interpretation
+
deterministic module ownership
+
current dependency facts
```

The Agent set itself is fixed.

Producer cannot invent a new module Owner.

---

# 25. Independent First-Round Proposals May Run in Parallel

For example:

```text
Script Writer
Character Agent
```

may often analyze independently.

Parallelism is allowed.

OpenAI's current Agent examples continue to include parallel independent Agent execution as a standard orchestration pattern. ([GitHub][2])

But persisted state remains truth.

Before work starts:

```text
persist ModuleProposal shell
persist AgentTask
```

After each finishes:

```text
persist that Proposal independently
```

If:

```text
Script ✓
Character ✓
Director ✗
```

a restart or retry must not rerun Script and Character.

---

# 26. Maximum Two Autonomous Consultation Rounds

Keep the AI-7A discipline.

Round 1:

```text
Producer
→ relevant module Owners
```

Then:

```text
Dependency Analyzer

Conflict Analyzer

Producer checks missing information
```

If necessary:

```text
one targeted Round 2
```

Examples:

```text
Director:
"I need the final Scene 54 narrative outcome before
I can propose the ending Shots."

Producer
→ ask Script Writer one precise follow-up
```

or:

```text
Script Writer says Xiaoyu survives.
Director proposal visually implies Xiaoyu dies.

Producer
→ ask Director to revise against the confirmed Script outcome.
```

Do not implement recursive debate.

After Round 2:

```text
resolved
→ compile plan

creative ambiguity remains
→ NEEDS_USER_DECISION

hard incompatibility remains
→ BLOCKED
```

---

# 27. Dependency Facts Must Be Deterministic Where Possible

Do not ask an LLM:

> "Which Shots reference Xiaoyu?"

The project can answer exactly.

Build on AI-7A Project Intelligence.

Examples of deterministic dependency facts:

```text
Character
→ Scene references
→ Shot references

Scene
→ Shot children

Shot
→ Production Plan reference

Shot
→ generated frame/audio/video assets

Scene narrative
→ Scene Storyboard review relationship

Shot order
→ adjacency/continuity relationship
```

The LLM may interpret creative meaning.

Code should answer structural dependency questions.

---

# 28. Add / Extend Deterministic Impact Rules

AI-6A already has deterministic Scene/Shot impact logic.

Reuse it.

AI-7B extends it for new creative operations.

Do not replace the existing Impact Engine with Producer reasoning.

Suggested new reason codes include:

```text
STORY_TEXT_CHANGED

SCENE_CONTENT_CHANGED
SCENE_INSERTED
SCENE_DELETED
SCENE_REORDERED
SCENE_CAST_CHANGED

CHARACTER_ADDED
CHARACTER_DESCRIPTION_CHANGED
CHARACTER_REMOVED

CHARACTER_SCENE_REFERENCES_AFFECTED
CHARACTER_SHOT_REFERENCES_AFFECTED
CHARACTER_ASSETS_REVIEW_REQUIRED

STORYBOARD_REVIEW_REQUIRED

PRODUCTION_PLAN_STALE
PROMPT_CONTEXT_AFFECTED
CONTINUITY_REVIEW_REQUIRED
FRAME_REGENERATION_RECOMMENDED
VIDEO_REGENERATION_RECOMMENDED
```

Use the existing repository's actual enum/reason-code conventions where available.

---

# 29. Story Change Impact

A Story/Scene change should not automatically delete downstream Storyboard.

Instead deterministic impact should say things such as:

```text
Scene narrative changed

affected Storyboard must be reviewed

Production Plan for affected Shots may be stale

generated assets for Shots eventually replaced
should be treated as historical output
```

The system must not claim:

```text
"all downstream Shots must be deleted"
```

unless an explicit final proposal actually deletes/rebuilds them.

---

# 30. Character Appearance Impact

For:

```text
short hair
→ long black hair
```

deterministic facts should normally include:

```text
Character textual identity changed

Scenes using Character remain structurally valid

Storyboard structure does not automatically change

existing Character reference images may be visually stale

existing generated frames/videos containing Character
may need regeneration after execution

historical assets remain preserved
```

This is a very important distinction.

Do not force Director to redesign Shots solely because hair color changed.

---

# 31. Character Addition Impact

For a new Character:

```text
Character entity must exist

Script Scenes requiring the Character must reference it

Storyboard Shots may need to include it

new visual Character assets will be required later

affected Production Plans will need review after creative commit
```

If the new Character is created but never used:

```text
no automatic Storyboard rewrite
```

---

# 32. Character Deletion / Replacement Impact

Deletion is one of the hardest cases.

A Character cannot simply disappear while:

```text
Scene.characters references it

Shot.characters references it
```

The final proposal must either:

```text
remove those references

replace those references

or cancel Character deletion
```

The validator must reject:

```text
DeleteCharacter("Xiaoyu")
```

while the projected final creative state still references Xiaoyu.

Do not automatically erase references without Specialist proposals.

---

# 33. Conflict Analyzer

Add a deterministic conflict layer.

Examples of hard conflicts:

```text
Proposal A deletes Scene s31
Proposal B updates Scene s31

Proposal A deletes Character Xiaoyu
Proposal B adds Xiaoyu to Shot s31_c04

two proposal items write incompatible after-values
to the same semantic field

two Scene reorder intentions produce incompatible orders

Director references a proposed Scene that is not created

Script removes Character from Scene
while Director retains Character in a Shot in that Scene
```

Represent conflicts structurally.

Example:

```python
class ProposalConflict(BaseModel):
    conflict_id: str

    severity: Literal[
        "hard",
        "needs_review",
        "creative_choice",
    ]

    proposal_item_ids: list[str]

    entity_refs: list[ProjectEvidenceRef]

    reason_code: str
    summary: str
```

Do not make conflict detection only a Producer prompt responsibility.

---

# 34. Creative Conflicts Are Different From Structural Conflicts

Example:

```text
Script Writer:
ending should be tragic but hopeful

Director:
ending should end on a completely hopeless image
```

Code cannot determine the correct artistic answer.

Producer may ask one targeted second-round question.

If still ambiguous:

```text
NEEDS_USER_DECISION
```

Present normal-language choices.

Do not let Producer silently pick a preference unless the user's original instruction clearly resolves it.

---

# 35. User Decision Requests

Introduce a durable object such as:

```python
class ChangeDecisionRequest(BaseModel):
    decision_id: str

    change_session_id: str

    question: str

    options: list["ChangeDecisionOption"]

    blocking: bool = True

    status: Literal[
        "pending",
        "answered",
        "cancelled",
    ]

    selected_option_id: str | None = None
```

Primary UI must not expose internal IDs.

Example:

```text
The new ending can be interpreted in two ways:

A. Xiaoyu rescues Aran and survives.
B. Xiaoyu rescues Aran but sacrifices herself.

Which direction do you want?
```

After answer:

```text
persist decision

resume same ChangeSession

target only affected proposals

compile new plan version
```

---

# 36. `UnifiedChangePlan`

The final Producer artifact should be a real durable plan.

Conceptually:

```python
class UnifiedChangePlan(BaseModel):
    plan_id: str

    change_session_id: str
    project_name: str

    version: int

    source_snapshot_signature: str

    status: Literal[
        "draft",
        "needs_user_decision",
        "validated",
        "preview_ready",
        "approved_for_execution",
        "rejected",
        "stale",
    ]

    direct_change_items: list["ChangePlanItem"]

    downstream_change_items: list["ChangePlanItem"]

    unchanged_scope: list[ProjectEvidenceRef]

    steps: list["ChangePlanStep"]

    dependency_edges: list["PlanDependencyEdge"]

    user_decision_ids: list[str]

    module_proposal_ids: list[str]

    validation_issues: list["PlanValidationIssue"] = []

    preview_id: str | None = None

    approval: "PlanApproval | None" = None

    created_at: float
```

Unlike AI-7A:

```text
execution_ready = always false
```

is no longer the right abstraction.

AI-7B can become:

```text
approved_for_execution
```

but **execution is still technically unavailable in this phase**.

---

# 37. `ChangePlanItem`

A Plan item should reference professional proposal content.

Producer should not duplicate or rewrite it.

Conceptually:

```python
class ChangePlanItem(BaseModel):
    plan_item_id: str

    module: str
    owner_agent_id: str

    proposal_item_ids: list[str]

    scope_refs: list[ProjectEvidenceRef]

    summary: str
    reason: str

    source: Literal[
        "user_requested",
        "downstream_impact",
    ]

    required: bool = True
```

---

# 38. `ChangePlanStep`

Execution order must already be explicit in AI-7B even though execution itself waits for AI-7C.

Suggested:

```python
class ChangePlanStep(BaseModel):
    step_id: str

    module: str
    owner_agent_id: str

    plan_item_ids: list[str]

    depends_on_step_ids: list[str] = []

    parallel_group: str | None = None
```

Example:

```text
Step 1A
Script Writer
Revise Scenes 51–56

Step 1B
Character Agent
Update Xiaoyu visual identity

1A and 1B may run in parallel

        ↓

Step 2
Director
Rebuild Storyboard for Scenes 54–56
using finalized Script + Character identity
```

This plan-level DAG is future execution metadata.

It is not a production scheduler.

---

# 39. Dependency DAG

Use deterministic graph validation.

Do not add a generic workflow framework.

Small local functions are enough:

```python
def topo_sort_plan_steps(steps, edges):
    ...

def find_cycle(...):
    ...
```

Validator must reject:

```text
A depends on B
B depends on A
```

with:

```text
PLAN_DEPENDENCY_CYCLE
```

Do not ask Producer to "remember not to make cycles."

---

# 40. Producer May Decide Orchestration, Not Domain Content

Producer may decide:

```text
Script and Character can run in parallel.

Storyboard must wait.

Character deletion must occur after reference removal.

This optional proposal item should be excluded.

A second consultation is needed.
```

Producer may not decide:

```text
Scene 54 should contain this new line of story text
```

unless that content came from Script Writer.

The plan compiler should preserve:

```text
proposal_item_id → plan_item_id
```

traceability.

---

# 41. Change Plan Validator

Implement a deterministic validator.

This is one of the most important AI-7B components.

Conceptually:

```python
class ChangePlanValidator:
    def validate(
        self,
        *,
        request: ChangeRequest,
        session: ChangeSession,
        plan: UnifiedChangePlan,
        proposals: list[ModuleProposal],
        project_snapshot,
        impact_facts,
        conflicts,
    ) -> list[PlanValidationIssue]:
        ...
```

---

# 42. Validator — Request Coverage

Verify:

```text
every explicit user request is covered

or

explicitly marked unsupported / needs decision
```

Producer cannot silently forget:

```text
"also make the final fight more tragic"
```

just because Script and Character changes were easier.

---

# 43. Validator — Module Ownership

Verify:

```text
Script items authored by Script Writer

Character items authored by Character Agent

Storyboard items authored by Director
```

Reject:

```text
Producer invents Script proposal

Director edits Character description

Character Agent deletes Shots
```

---

# 44. Validator — Entity Existence

Every existing target must exist in the source snapshot.

Every proposed target must be traceable to a valid creation proposal.

Reject invented references.

---

# 45. Validator — Dependency Coverage

If deterministic Impact says:

```text
Character deletion leaves 8 Shot references
```

the plan cannot claim:

```text
Character deletion is complete
```

until those references are addressed.

---

# 46. Validator — Conflict Coverage

Every hard conflict must be:

```text
resolved
or
blocking
```

Never:

```text
hard conflict exists
+
plan still marked preview_ready
```

---

# 47. Validator — User Decision Coverage

Blocking decisions must be answered before final preview.

The plan may show a partial analysis while waiting.

But:

```text
preview_ready
```

requires:

```text
0 unresolved blocking decisions
```

---

# 48. Validator — Source Signature

Plan must reference the exact creative source signature.

Before:

```text
final synthesis
preview build
user approval
```

recheck current signature.

Mismatch:

```text
PLAN_STALE
```

Do not silently present old proposal content as current.

---

# 49. Validator — Lock / Protected Scope

Respect existing:

```text
Shot.locked
protected_entities
other creative protection mechanisms
```

A plan touching locked content should not silently proceed.

Return something like:

```text
LOCKED_SCOPE_REQUIRES_USER_DECISION
```

The user may later explicitly choose to allow a future execution stage to modify it.

AI-7B itself still does not unlock or modify anything.

---

# 50. LLM Must Not Produce Final `ChangeOperation`

This remains the most important mutation-safety principle inherited from Director Proposal Runtime.

Never:

```text
LLM
→ WorkspaceChangeSet JSON
```

Never trust the model to produce:

```text
before
revision
signature
stable IDs
full reorder arrays
ChangeSet IDs
```

The architecture remains:

```text
LLM professional semantic intention

        ↓

strict validation

        ↓

read exact projected state

        ↓

deterministic compiler

        ↓

ChangeOperation
```

---

# 51. Extend the Existing Semantic `ChangeOperation` Layer Carefully

AI-6A intentionally began with Scene/Shot operations.

AI-7B now genuinely needs Script and Character proposals.

Extend the existing revision operation union rather than creating a second mutation format.

First inspect the current real operation schema.

Only add missing operations required for AI-7B.

---

# 52. `UpdateStoryTextOperation`

If not already present:

```python
class UpdateStoryTextOperation(BaseModel):
    type: Literal["update_story_text"]

    before: str
    after: str
```

`before` must be read deterministically from the exact source projection.

The LLM only proposes:

```text
new story intent/text
```

The compiler fills `before`.

---

# 53. Scene Collection Operations

AI-7B should support the minimum real Script revision set:

```text
UpdateScene
InsertScene
DeleteScene
ReorderScenes
```

Reuse existing `UpdateScene`.

Conceptually:

```python
class InsertSceneOperation(BaseModel):
    type: Literal["insert_scene"]

    after_scene_id: str | None

    proposed_scene_id: str

    summary: str
    characters: list[str]
```

```python
class DeleteSceneOperation(BaseModel):
    type: Literal["delete_scene"]

    scene_id: str

    before: dict
```

```python
class ReorderScenesOperation(BaseModel):
    type: Literal["reorder_scenes"]

    before_order: list[str]
    after_order: list[str]
```

Do not renumber existing Scene IDs.

---

# 54. Stable Scene IDs

New Scene IDs must be allocated by deterministic backend code.

The LLM never invents permanent IDs.

If current store already has a stable scene-sequence allocator, reuse it.

Do not reuse deleted IDs.

Do not rename existing Scene IDs after reorder.

Stable identity matters for:

```text
Agent evidence
history
Proposal refs
Shot lineage
Production Plans
Visual Diff
```

---

# 55. Character Operations

Add only what is needed:

```text
UpdateCharacter
InsertCharacter
DeleteCharacter
```

Conceptually:

```python
class UpdateCharacterOperation(BaseModel):
    type: Literal["update_character"]

    character_ref: EntityRef

    description_en: FieldChange | None = None
```

Do not make generated views a normal editable field.

---

# 56. `InsertCharacterOperation`

Conceptually:

```python
class InsertCharacterOperation(BaseModel):
    type: Literal["insert_character"]

    name: str
    description_en: str
```

Server validates uniqueness using the real current Character identity rules.

No generated views.

No Agnes image call.

---

# 57. `DeleteCharacterOperation`

Conceptually:

```python
class DeleteCharacterOperation(BaseModel):
    type: Literal["delete_character"]

    character_ref: EntityRef

    before: dict
```

Validation must inspect the **projected final state**, not only the original project.

Deletion is valid only when the final projection contains no forbidden current references.

Asset files remain on disk/history.

---

# 58. Existing Shot Operations Stay the Source of Truth

Reuse existing:

```text
UpdateShot
InsertShot
DeleteShot
ReorderShots
```

Do not widen the operation model casually to every Shot field.

Use whatever field set the current revision engine actually supports.

If camera/framing/motion support has already been safely added since AI-6A, use it.

If not, stay within existing supported fields.

AI-7B is not a revision-layer rewrite.

---

# 59. Do Not Put Generated Assets in Creative ChangeOperations

Never directly modify through Agent proposal:

```text
first_frame

last_frame

audio

video

generated prompt history

asset file paths

asset URLs

Job IDs

video IDs

production status
```

These are downstream production artifacts.

Impact may mark them as stale/recommended for regeneration.

AI-7B does not change them.

---

# 60. Proposal Projection Engine

This is the most important deterministic engineering component after the Plan Validator.

AI-7B needs to show the **final projected state** without applying anything.

Do not fake the Visual Diff by asking Producer to describe it.

Introduce a small service conceptually like:

```python
class ProposalProjectionEngine:
    def build_preview(
        self,
        *,
        source_project,
        plan,
        proposals,
    ) -> PlanPreviewArtifact:
        ...
```

---

# 61. Do Not Mutate a Creative Workspace to Build the AI-7B Preview

The Team ChangeSession proposal path should not need to Apply intermediate ChangeSets to a Workspace merely to understand later dependencies.

Preferred behavior:

```text
load source snapshot

deep-copy / immutable projection

compile Step 1 operations

run shared revision validation on projection

apply operations to IN-MEMORY projection

compile Step 2 against projected state

run validation

apply to IN-MEMORY projection

...

build final diff:
source snapshot
vs
final projected state
```

Nothing is written to:

```text
ProjectStore
Workspace working_project
Production Plan store
```

---

# 62. Reuse the Revision Engine's Pure Preview Logic

Do not implement a second version of ChangeOperation application rules.

Inspect the existing AI-6A preview implementation.

If it contains logic conceptually equivalent to:

```python
clone = deepcopy(project)
apply_operations(clone, operations)
validate(...)
build_diff(...)
```

extract the minimum reusable pure function.

For example:

```python
preview_operations_on_project(
    project,
    operations,
)
```

Then:

```text
Workspace preview
→ calls shared pure preview core

AI-7B Proposal Projection
→ calls same shared pure preview core
```

Do not duplicate the operation semantics inside `src/agents`.

---

# 63. Projected State Is Derived Data, Not Truth

The in-memory projection is:

```text
"What the project would look like
if this approved plan were executed later."
```

It is not:

```text
current project
```

Do not let Project Intelligence accidentally read the proposal projection as canonical truth.

The Plan Preview should be explicitly marked:

```text
derived
proposal_only
non_authoritative
```

---

# 64. Do Not Persist a Fake New Project as the Primary Artifact

The durable truth of AI-7B should be:

```text
ChangeRequest
ModuleProposals
UnifiedChangePlan
PlanPreviewArtifact
```

not:

```text
mysterious proposal_project.json
```

The projection should be reproducible deterministically from durable proposal artifacts while the source signature is unchanged.

Persist the Diff/Impact/compiled operation bundle required for UI/recovery.

Avoid creating another project truth source.

---

# 65. `PlanPreviewArtifact`

Conceptually:

```python
class PlanPreviewArtifact(BaseModel):
    preview_id: str

    change_session_id: str
    plan_id: str
    plan_version: int

    source_snapshot_signature: str

    module_operations: list["ModuleOperationBundle"]

    aggregate_diff: dict
    aggregate_impact: dict
    warnings: list[dict]

    projected_signature: str

    status: Literal[
        "preview_only"
    ] = "preview_only"

    applyable: bool = False

    created_at: float
```

`applyable` must always be `False` in AI-7B.

No API may change it.

---

# 66. Module Operation Bundles

Preserve ownership traceability:

```python
class ModuleOperationBundle(BaseModel):
    step_id: str

    module: str
    owner_agent_id: str

    proposal_item_ids: list[str]

    operations: list[ChangeOperation]
```

This gives AI-7C a clean future handoff.

It also lets debugging answer:

```text
Which Agent proposed the operation that changes Scene s31?
```

without reading chat prose.

---

# 67. Aggregate Visual Diff

Extend existing Diff data rather than building UI-only comparison logic.

The final Plan preview should support:

```text
Story

Characters

Scenes

Shots
```

Possible structure:

```python
class PlanVisualDiff(BaseModel):
    story: StoryDiff | None

    characters: list[CharacterDiff]

    scenes: list[SceneDiff]

    summary: PlanDiffSummary
```

Reuse existing SceneDiff / ShotDiff where possible.

---

# 68. Story Diff

Show:

```text
before
after
```

for `story_text`.

Do not show raw JSON.

For very long story text, UI may show:

```text
changed section
collapsed surrounding context
```

Backend still stores exact before/after values required by operation validation.

---

# 69. Character Diff

For each Character:

```text
ADDED

MODIFIED

REMOVED
```

Example:

```text
Xiaoyu

Appearance

Before:
short dark hair, gray coat

After:
long black hair, gray coat

Existing generated assets:
17 affected assets
historical files preserved
```

Do not visually imply that existing images have already changed.

---

# 70. Scene / Shot Diff

Reuse existing AI-6A visual diff semantics.

The user should see things like:

```text
Scene 54
MODIFIED

Story
- Aran reaches the gate alone.
+ Xiaoyu reaches Aran and pulls him away before the collapse.

Storyboard
Shot s54_c03
MODIFIED

Shot s54_c04
REMOVED

New Shot
ADDED
```

Stable IDs should remain internal/secondary.

Use normal labels such as:

```text
Scene 54
Shot 3
```

in primary UI.

---

# 71. Aggregate Impact

User-facing Impact should answer:

```text
What will need review?

What existing production becomes stale?

Which generated assets may need regeneration later?

What historical assets are preserved?

Which portions are unaffected?
```

Do not say:

```text
"47 videos will be regenerated"
```

because AI-7B does not schedule regeneration.

Say:

```text
"47 existing generated assets may no longer match
the proposed Character appearance."
```

---

# 72. Preserve Historical Assets

This remains a hard invariant:

```text
proposal changes creative current state conceptually

historical images/videos remain historical
```

AI-7B must never:

```text
delete old image
delete old video
overwrite old generated asset
```

Impact and Diff may reference those assets.

They are evidence/history, not garbage.

---

# 73. User-Facing Plan Language

Internal Change Plan structures may be technical.

The primary user-facing card must be normal language.

The established grouping remains mandatory:

```text
What you asked to change

Related changes

What will remain unchanged

Needs your decision
```

For a Chinese user, render these naturally in Chinese.

Example:

```text
变更方案

你要求修改
────────────────────
• 小雨改成长黑发。
• 结局改成小雨救下阿岚。
• 最后的战斗氛围更悲壮。

牵连修改
────────────────────
• 剧本：
  最后 6 个故事段需要调整。

• 角色：
  小雨的文字视觉设定会更新。
  现有角色图会保留，但之后可能需要重新生成。

• 分镜：
  第 54–56 场需要重新设计部分镜头。

保持不变
────────────────────
• 第 1–50 场剧情
• 阿岚的角色设定
• 已生成素材历史

影响
────────────────────
• 23 个现有镜头需要重新检查
• 17 个包含小雨的已有视频可能不再匹配新外观
• 当前生产计划之后需要重新规划
```

Do not put:

```text
UpdateSceneOperation
before_signature
EntityRef
revision=17
```

in the primary card.

---

# 74. Technical Detail Panel

A collapsible developer/debug section may show:

```text
ChangeSession ID

source signature

ModuleProposal IDs

Proposal item IDs

ChangePlan version

step DAG

ChangeOperations

validation rules

Impact reason codes

evidence refs

AgentTask IDs
```

This is extremely useful while developing AI-7B.

Do not expose secrets.

Do not expose hidden chain-of-thought.

---

# 75. User Approval

AI-7B should finally add a real plan approval state.

Conceptually:

```python
class PlanApproval(BaseModel):
    plan_id: str
    plan_version: int

    decision: Literal[
        "approved",
        "rejected",
    ]

    source_snapshot_signature: str

    decided_at: float
```

Approval must be bound to:

```text
exact plan version
+
exact source signature
```

---

# 76. Approval Must Not Execute Anything

This must have a mandatory negative test.

When user clicks:

```text
Approve Plan
```

backend does only:

```text
reload current project signature

verify plan not stale

persist approval

plan.status = approved_for_execution

session.status = approved_for_execution

persist Team message/card
```

Then STOP.

It must not call:

```text
workspace.apply

ChangeSetEngine.apply

ProductionPlanner

ShotProducer

JobQueue

Agnes image endpoint

Agnes video endpoint
```

Current OpenAI Agents HITL documentation uses an explicit pause/approve/resume model for sensitive actions, and supports persistence across longer approval waits. AI-7B should use the architectural idea — durable proposal state and an explicit human decision boundary — without adding the SDK as a dependency. ([OpenAI GitHub][3])

---

# 77. Approval UI Wording Must Be Unambiguous

Do not use a misleading primary button:

```text
执行修改
```

in AI-7B.

Use something like:

```text
批准此方案
```

with visible helper text:

```text
批准只会保存你的确认。
当前阶段不会立即修改项目或重新生成素材。
```

After approval:

```text
方案已批准
等待执行阶段
```

This keeps the AI-7B / AI-7C boundary visible to the user.

---

# 78. Request Changes

User may respond:

```text
"Keep the long hair,
but don't change Scene 55."
```

Do not mutate the existing Plan in place.

Create:

```text
plan version N+1
```

Preserve:

```text
version N
```

as historical proposal state.

The new version should carry:

```text
parent_plan_id
user_feedback_message_id
```

Only affected Specialist proposals need to be rerun where possible.

Do not rerun all Agents blindly.

---

# 79. Plan Versioning

A `UnifiedChangePlan` should be immutable after it becomes:

```text
preview_ready
```

User feedback produces a new version.

Example:

```text
Plan v1
preview_ready
        ↓
user requests changes

Plan v1
superseded

Plan v2
synthesizing
        ↓
preview_ready
```

This makes Team Chat history understandable after restart.

---

# 80. Rejection

Rejecting a Plan means:

```text
session status = rejected
```

Do not delete historical Proposal artifacts.

The user should be able to inspect what was proposed later.

No project state changes.

---

# 81. Direct Agent Chat → Proposal Flow

Preserve direct Specialist conversations.

Example:

User asks Character Agent:

```text
"Make Xiaoyu's hair long."
```

Character Agent can create:

```text
ModuleProposal
```

If deterministic impact finds:

```text
only Character visual identity
+
downstream asset staleness
```

a lightweight ChangeSession may still package it into a single-module Plan.

Producer does not need to dominate the conversation.

---

# 82. Direct Chat Cross-Module Escalation

Example:

User asks Script Writer:

```text
"Remove the mechanical dog after Scene 12."
```

Script Writer discovers:

```text
Storyboard references exist
Character entity may become unused
generated assets exist
```

Then:

```text
Direct Script Chat
        ↓
ChangeRequest
        ↓
Producer ChangeSession
        ↓
Team Chat collaboration card
```

Do not copy the entire private Script Writer transcript into Team Chat.

Sync a collaboration summary.

---

# 83. Team Chat ChangeSession Cards

Team Chat should show meaningful structured events.

Example:

```text
Producer
正在分析你的修改要求。

Script Writer
已完成剧情修改方案。

Character Agent
已检查角色影响。

Storyboard Director
正在检查第 54–56 场。

Producer
发现 1 个需要确认的问题。

[选择结局方向]

Producer
方案已经整理完成。

[查看完整变更方案]
```

These messages come from persisted state.

Do not invent fake live progress text merely to make the UI feel busy.

---

# 84. Polling-First Remains the Realtime Strategy

AI-7A intentionally used persistence + polling-first.

Keep it.

Do not introduce a Team SSE bus as part of AI-7B unless the committed AI-7A implementation unexpectedly already added one.

Recommended:

```text
active ChangeSession
→ poll every ~2 seconds

idle Team Chat
→ slower polling or stop

terminal session
→ stop high-frequency polling
```

The aggregated ChangeSession response should contain Specialist statuses so the frontend does not need to poll N AgentTasks individually.

Persistence remains truth.

Realtime transport is only presentation.

---

# 85. Do Not Reuse `/api/jobs/stream` for Agent Proposal Events

The existing production Job SSE represents production Jobs.

AI-7B Proposal tasks are not image/video generation Jobs.

Do not overload that event stream.

Keep production progress and Agent proposal coordination conceptually separate.

---

# 86. Persistence

Persist AI-7B state under the existing per-project Agent data convention.

A reasonable conceptual layout:

```text
projects/<project>/output/agents/

    team/
        ...

    sessions/
        ...

    tasks/
        ...

    changes/
        requests/
        sessions/
        proposals/
        plans/
        previews/
        decisions/
```

Adapt to the real AI-7A directory structure.

Do not introduce a database merely for AI-7B.

Continue atomic JSON writes if that is the existing persistence standard.

---

# 87. Restart Recovery

The following must survive restart:

```text
ChangeRequest

ChangeSession

completed ModuleProposals

pending ModuleProposals

failed ModuleProposals

user decisions

UnifiedChangePlan versions

PlanPreviewArtifact

approval state
```

Example:

```text
Script ✓
Character ✓
Director running
```

Restart.

Expected:

```text
same ChangeSession

Script not rerun

Character not rerun

Director reconciled/resumed

one final Plan only
```

---

# 88. Idempotency

Every externally triggered step should have an idempotency key.

At minimum:

```text
Team message → ChangeRequest

ChangeRequest → ChangeSession

Session + Agent + round → ModuleProposal task

Session + plan_version → plan synthesis

plan_id + source_signature → preview

plan_id + version → approval
```

Double-clicking:

```text
Approve Plan
```

must not create two approvals.

Refreshing:

```text
POST retry
```

must not create duplicate ModuleProposals.

---

# 89. Per-Session Execution Lock

Use a small per-ChangeSession async/process lock consistent with the current runtime.

The purpose is:

```text
prevent two resume calls
from compiling the same plan simultaneously
```

Do not hold a project write lock across LLM calls.

AI-7B is proposal-only.

Project reads should remain short.

---

# 90. Stale Source Protection

Capture source signature:

```text
SIG_A
```

Then project changes legitimately through another existing workflow:

```text
SIG_B
```

Any AI-7B object based on SIG_A becomes stale.

Before:

```text
second consultation
plan synthesis
preview
approval
```

check again.

Expected:

```text
session.status = stale

plan.status = stale

approval rejected

no old Diff presented as current
```

UI:

```text
项目在方案生成后发生了变化。
请基于当前版本重新分析。
```

Do not implement automatic rebase in AI-7B.

---

# 91. Project Isolation

Every object lookup must verify:

```text
project_name
```

Never rely only on frontend route state.

Project A must never resolve:

```text
Project B ChangeSession
Project B ModuleProposal
Project B Plan
Project B Preview
Project B Decision
```

Test this negatively.

---

# 92. API Surface

Extend the existing Agent router/service.

Do not expose every internal micro-step as a public endpoint.

A conceptual API may include:

```text
POST
/api/project/{p}/team/messages

GET
/api/project/{p}/team/thread

GET
/api/project/{p}/change-sessions/{session_id}

POST
/api/project/{p}/change-sessions/{session_id}/resume

GET
/api/project/{p}/change-sessions/{session_id}/plan

GET
/api/project/{p}/change-sessions/{session_id}/preview

POST
/api/project/{p}/change-sessions/{session_id}/decision

POST
/api/project/{p}/change-sessions/{session_id}/plan-feedback
```

Exact routes should follow the committed AI-7A conventions.

Do not create endpoints such as:

```text
/run-script-agent
/run-character-agent
/run-impact
/run-conflict
/compile-step-3
```

The backend service owns orchestration.

---

# 93. Proposal Decision Request API

Conceptually:

```json
POST /change-sessions/{id}/decision

{
  "decision_id": "...",
  "option_id": "..."
}
```

Backend verifies:

```text
decision belongs to session

decision pending

option valid

session not stale
```

Then resumes only the required proposal path.

---

# 94. Plan Approval API

Conceptually:

```json
POST /change-sessions/{id}/approve

{
  "plan_id": "...",
  "plan_version": 2
}
```

Backend must:

```text
reload plan

verify active version

verify preview exists

verify validation PASS

verify 0 blocking decision

verify source signature current

persist approval

return approved_for_execution
```

Nothing else.

---

# 95. Frontend — Team Change Plan Surface

Build on the AI-7A Team Chat.

Do not create a separate application-level "Change Chat".

Team Chat is still the Producer workspace.

Add a structured ChangeSession card.

States:

```text
analyzing

consulting

needs decision

compiling plan

validating

building preview

ready

approved

rejected

stale

failed
```

---

# 96. Change Plan Review Layout

Recommended structure:

```text
┌─────────────────────────────────────┐
│  AI Team · Change Plan              │
│  Ready for review                   │
├─────────────────────────────────────┤
│  You asked                          │
│  ...                                │
│                                     │
│  Related changes                    │
│  ...                                │
│                                     │
│  Will remain unchanged              │
│  ...                                │
├─────────────────────────────────────┤
│  Visual changes                     │
│  [Story] [Characters] [Storyboard]  │
├─────────────────────────────────────┤
│  Impact                             │
│  ...                                │
├─────────────────────────────────────┤
│  Execution order                    │
│  Script + Character                 │
│          ↓                          │
│       Storyboard                    │
├─────────────────────────────────────┤
│  [Request Changes] [Reject]         │
│                     [Approve Plan]  │
└─────────────────────────────────────┘
```

Do not overload the Team Chat message itself with the complete Diff.

Use an expandable/detail surface.

---

# 97. Show Module Ownership

The user does not need internal technical jargon.

But seeing team ownership is useful:

```text
剧本 Agent
角色 Agent
分镜导演
```

For example:

```text
剧本
最后 6 个故事段会调整

角色
小雨视觉设定会修改

分镜
第 54–56 场会重新设计部分镜头
```

This reinforces that Producer coordinates rather than pretending to be every Specialist.

---

# 98. Execution Order Visualization

A lightweight DAG visualization is enough.

Do not build a general workflow editor.

For example:

```text
Script ─────┐
            ├──→ Storyboard
Character ──┘
```

or:

```text
1. 剧本 + 角色（可并行）

2. 分镜

3. 制作规划（后续执行阶段）
```

The Production step may be shown as:

```text
future consequence
```

but must not be started.

---

# 99. Existing Direct Director UI Must Continue to Work

AI-7B's Team ChangeSession does not replace:

```text
Storyboard
→ select Shot
→ Director
→ generate proposal
→ preview
→ apply to Creative Workspace
```

That is an existing product workflow.

Regression-test it.

The new Team Proposal flow is:

```text
project-level coordination
```

not a replacement for specialist in-context editing.

---

# 100. Agnes / Model Policy

Do not hardcode a new text model for AI-7B.

Use the existing:

```text
Agent ModelProfileRegistry
Agent settings
account policy
fallback policy
```

Agnes model names are already required to come from configuration rather than being hardcoded in business logic. 

AI-7B should make text/reasoning calls only.

No image/video API calls belong in this phase.

---

# 101. Failure Taxonomy

Use structured failure codes.

Suggested examples:

```text
CHANGE_SESSION_NOT_FOUND

CHANGE_SESSION_STALE

MODULE_PROPOSAL_FAILED

MODULE_PROPOSAL_INVALID

PROPOSAL_OUTPUT_INVALID

PROPOSAL_REPAIR_FAILED

UNSUPPORTED_CHANGE_INTENT

PLAN_REQUEST_NOT_COVERED

PLAN_OWNER_MISMATCH

PLAN_DEPENDENCY_CYCLE

PLAN_UNRESOLVED_CONFLICT

PLAN_USER_DECISION_REQUIRED

PLAN_ENTITY_NOT_FOUND

PLAN_REFERENCE_CONFLICT

PLAN_LOCK_CONFLICT

PLAN_SOURCE_STALE

PLAN_VALIDATION_FAILED

PLAN_PREVIEW_FAILED

PLAN_VERSION_CHANGED

PLAN_NOT_READY

PLAN_ALREADY_REJECTED

PLAN_APPROVAL_STALE
```

Frontend should translate these into understandable messages.

---

# 102. LLM Output Repair

For Specialist structured outputs:

```text
strict schema parse
        ↓
validation
        ↓
one targeted repair attempt
        ↓
fail cleanly
```

Do not implement unlimited:

```text
retry until JSON works
```

Persistence should record failure.

User may retry explicitly.

---

# 103. Partial Specialist Failure

Suppose:

```text
Script ✓
Character ✓
Director failed
```

If Director is required:

```text
session = blocked / failed
```

Do not create a final Plan pretending Storyboard is covered.

Successful proposals remain stored.

A retry resumes only Director.

---

# 104. Unsupported Requests

If user asks:

```text
"Change the entire visual style from anime to photorealistic."
```

and AI-7B has no legitimate module Owner / semantic operation for project style:

do not invent one.

Return:

```text
This proposal type is not yet supported by the current
project revision protocol.
```

The Producer may explain affected current scope.

Do not widen the revision system ad hoc merely to satisfy one request.

---

# 105. User Intent Must Remain Visible

Store:

```text
original_user_request
```

and show it in the Plan.

Producer summaries may never replace the original source request.

This is important for later debugging:

```text
User asked X

Module owners proposed Y

Producer plan contains Z
```

---

# 106. Evidence Trace

Every important Plan item should be traceable to:

```text
user request

and/or

ModuleProposal item

and/or

deterministic Impact Fact
```

A debugger should be able to answer:

```text
Why is Scene 54 included?
```

with something like:

```text
user requested ending change
→ Script proposal SP-3 modifies Scene 54
→ Director proposal DP-7 rebuilds its Storyboard
→ Impact rule SCENE_CONTENT_CHANGED marks its Production Plan stale
```

No hidden reasoning required.

---

# 107. Plan Completeness Should Be More Important Than Producer Eloquence

Do not optimize AI-7B primarily for prettier Producer prose.

The main engineering goal is:

```text
coverage
ownership
dependencies
conflicts
source correctness
preview correctness
```

A plain but complete Plan is better than a fluent plan that misses one affected module.

---

# 108. Core Acceptance Case A — Storyboard-Only Change

User:

```text
"Make Scene 18 faster and more tense."
```

Expected:

```text
Producer routes Director only

Director creates real ModuleProposal

no unnecessary Script Writer

no unnecessary Character Agent

deterministic compiler
→ existing Shot operations

Plan validates

real Visual Diff generated

Impact reports Production Plan / prompts / generation review
```

Verify:

```text
canonical bytes unchanged

Workspace working bytes unchanged

0 production jobs
```

---

# 109. Core Acceptance Case B — Script Change Only

User:

```text
"Change the ending so Aran decides to leave the city alone."
```

Expected:

```text
Script Writer proposal

relevant Scene changes

Impact Analyzer detects Storyboard review requirement

Producer decides whether Director consultation is required
based on actual current storyboard dependency

final plan accurately separates:
direct story change
downstream storyboard consequence
```

If existing Storyboard must concretely change to represent the new ending:

```text
Director proposal required
```

---

# 110. Core Acceptance Case C — Add Character

User:

```text
"Add a mysterious girl in the final act.
She helps Xiaoyu open the gate."
```

Expected:

```text
Script Writer
→ narrative + Scene character requirements

Character Agent
→ new Character definition

Director
→ affected final-act Shot proposal

Producer
→ dependency order

Script + Character
can be parallel where safe

Storyboard depends on finalized upstream intent
```

Visual Diff:

```text
Character ADDED

Scenes MODIFIED

Shots ADDED/MODIFIED
```

No Character images generated.

---

# 111. Core Acceptance Case D — Delete / Replace Character

User:

```text
"Remove the mechanical dog after Scene 12."
```

System finds:

```text
6 later Scenes
24 later Shots
17 generated videos
```

Expected Plan:

```text
Direct request
Script:
remove/replace narrative references

Related
Storyboard:
remove/replace affected Shot references

Character:
delete Character only if no required current references remain

Impact:
17 historical generated videos may no longer represent
the final creative state, but remain preserved
```

Validator must fail any Plan that leaves forbidden references.

---

# 112. Core Acceptance Case E — Appearance-Only Character Change

User:

```text
"Make Xiaoyu's hair long and black."
```

Expected:

```text
Character Agent proposal

no Script rewrite

no automatic Shot structural rewrite
```

Impact:

```text
Character views may need regeneration later

affected frames/videos may need regeneration later

historical assets preserved
```

This acceptance test prevents over-cascade.

---

# 113. Core Acceptance Case F — Three-Module Request

User:

```text
"Make Xiaoyu's hair long,
change the ending so she rescues Aran,
and make the final fight more tragic."
```

Expected:

```text
Character Proposal
Script Proposal
Director Proposal

Round 1 parallel where possible

Dependency analysis

optional targeted Round 2

Producer Unified Change Plan

validator PASS

projection PASS

Visual Diff PASS
```

Plan card must include:

```text
Requested
Related
Unchanged
Impact
Execution order
```

---

# 114. Core Acceptance Case G — Structural Conflict

Inject:

```text
Script proposal deletes Scene 54

Director proposal updates Shots in Scene 54
```

Expected:

```text
hard conflict

no preview_ready

Producer asks targeted correction

or session blocked
```

Never silently pick one.

---

# 115. Core Acceptance Case H — Creative User Decision

User says:

```text
"Make the ending more tragic."
```

Script/Director identify two materially different valid interpretations.

Expected:

```text
NEEDS_USER_DECISION

two short human-readable options

user selects one

same ChangeSession resumes

new/final plan generated
```

---

# 116. Core Acceptance Case I — Stale Plan

Create Plan from:

```text
SIG_A
```

Before approval, modify canonical through an existing legitimate path.

Current:

```text
SIG_B
```

Expected:

```text
Approve → 409 / structured stale error

session = stale

0 mutation
```

---

# 117. Core Acceptance Case J — Restart

State before restart:

```text
Script Proposal completed
Character Proposal completed
Director Proposal running
```

Restart.

Expected:

```text
same ChangeSession

same Script Proposal

same Character Proposal

Director reconciled/resumed

one Plan version

no duplicate AgentTask
```

---

# 118. Core Acceptance Case K — User Feedback Versioning

Plan v1 ready.

User:

```text
"Don't modify Scene 55."
```

Expected:

```text
Plan v1 preserved

Plan v2 created

only affected consultation rerun if required

preview rebuilt

Plan v2 references current source signature
```

---

# 119. Core Acceptance Case L — Approval Hard Guard

Approve a valid Plan.

Verify all of:

```text
approval persisted

status = approved_for_execution

canonical project identical byte-for-byte

Workspace working projects identical

Production Plans identical

0 new generation Jobs

0 Agnes image requests

0 Agnes video requests

0 ChangeSet Apply call
```

This is mandatory.

---

# 120. Core Acceptance Case M — Direct Chat Escalation

Character direct chat:

```text
"Make Xiaoyu's hair long."
```

Expected:

```text
Character proposal
single-module ChangeSession
Team summary card
```

Script direct chat:

```text
"Remove the dog from Scene 12 onward."
```

Expected:

```text
cross-module escalation
Producer ChangeSession
Director consultation
```

Private raw conversation remains private.

---

# 121. Large Project Acceptance

Use a several-hundred-Shot fixture.

Request:

```text
"Remove Character X from the final third
and redesign the affected scenes."
```

Verify:

```text
Project Intelligence finds exact affected scope

Specialists receive bounded Context Packs

no Agent receives complete hundreds-of-Shots payload
unless explicitly proven necessary

Proposal references exact entities

projection succeeds

response remains restart-safe
```

---

# 122. Frontend Browser Acceptance

Manual path:

```text
open project

open AI Team

Team Chat

send:
"小雨改成长发，结局改成她救阿岚，
最后几个镜头更悲壮"

see Producer analyzing

see relevant Specialist statuses

see completed Script / Character / Director summaries

if decision requested:
answer it

see Change Plan

open Visual Diff

inspect Story changes

inspect Character changes

inspect Storyboard changes

inspect Impact

refresh browser

same ChangeSession restored

approve Plan

see:
"方案已批准，等待执行阶段"

return to project

verify project content unchanged

open production/job UI

verify no new production job
```

---

# 123. Testing Philosophy

Do not write hundreds of shallow tests merely because AI-7B is large.

Focus on invariants.

At every implementation checkpoint, run focused tests.

At final Gate, run full regression.

Critical test categories:

```text
proposal schema

permissions

module ownership

compiler correctness

Impact rules

conflict rules

DAG validation

projection correctness

stale protection

restart

idempotency

project isolation

approval hard stop

Director regression

Workspace regression

Production regression
```

Anthropic's current Agent evaluation guidance similarly emphasizes testing multi-step Agent behavior and making failures observable rather than relying only on happy-path output quality. ([Anthropic][4])

---

# 124. Recommended Implementation Sequence

Do not implement all of AI-7B in one uncontrolled pass.

Use checkpoints.

---

## AI-7B-1 — Proposal Protocol + Hard Runtime Boundary

Implement:

```text
baseline audit

schema migration

PROPOSAL_ONLY execution mode

ChangeRequest

ChangeSession

ModuleProposal

UnifiedChangePlan models

Plan approval model

permission negatives

persistence
```

No new Specialist intelligence yet.

Gate:

```text
legacy AI-7A sessions load

PROPOSAL_ONLY cannot Apply

persistence survives restart
```

Checkpoint 1.

---

## AI-7B-2 — Real Script + Character Proposals

Implement:

```text
story.propose_change

character.propose_change

structured outputs

strict parsing

one repair attempt

semantic proposal items

bounded Context Packs

AgentTask integration
```

Also expose/reuse the Director semantic proposal layer without changing direct Director behavior.

Gate:

```text
Script real proposal works

Character real proposal works

Director existing proposal still works

0 mutation
```

Checkpoint 2.

---

## AI-7B-3 — Dependency / Impact / Conflict + Producer Compiler

Implement:

```text
deterministic dependency facts

new Impact rules

Conflict Analyzer

second-round protocol

user decision requests

Producer Change Plan Compiler

Plan DAG

Change Plan Validator
```

Gate:

```text
single module

two modules

three modules

hard conflict

creative user decision

coverage failure

ownership failure
```

Checkpoint 3.

---

## AI-7B-4 — Revision Operations + Proposal Projection + Real Visual Diff

Implement only required revision extensions:

```text
UpdateStoryText

Insert/Delete/Reorder Scene if absent

Update/Insert/Delete Character

reuse existing Shot operations
```

Then:

```text
shared pure preview core

ProposalProjectionEngine

PlanPreviewArtifact

Story Diff

Character Diff

aggregate Scene/Shot Diff

aggregate Impact
```

Gate:

```text
projection exactly matches expected after-state

0 ProjectStore write

0 Workspace Apply

Impact deterministic

Visual Diff real
```

Checkpoint 4.

---

## AI-7B-5 — Team Chat Change Plan UX

Implement:

```text
ChangeSession progress card

Specialist proposal summaries

Needs Decision UI

Plan Review UI

Visual Diff tabs

Impact

execution order

Request Changes

Reject

Approve Plan

refresh restore

polling-first active state
```

Gate:

```text
full browser proposal experience

approval does not execute
```

Checkpoint 5.

---

## AI-7B-6 — Final Behavioral Gates

Run:

```text
stale

restart

idempotency

project isolation

partial Agent failure

large project

Direct Chat escalation

Director regression

Workspace regression

Production Planner regression

Shot Producer regression

full backend tests

frontend build

manual browser acceptance
```

Only then declare:

```text
AI-7B COMPLETE
```

---

# 125. Suggested Code Organization

Adapt to the committed AI-7A repository.

Do not force these exact paths if equivalent modules already exist.

Conceptually:

```text
src/agents/
    change_protocol/
        models.py
        store.py
        service.py

        routing.py
        dependency.py
        conflicts.py

        plan_compiler.py
        plan_validator.py

        projection.py

        script_proposal.py
        character_proposal.py

        migrations.py
```

or a smaller organization inside existing:

```text
src/agents/change_analysis.py
```

if AI-7A already established that structure.

The objective is clean responsibility boundaries, not directory proliferation.

---

# 126. Do Not Add Another Agent Framework

Do not add:

```text
OpenAI Agents SDK

LangGraph

CrewAI

AutoGen

Temporal

Dapr

Redis

Celery

vector database

generic event sourcing framework

generic workflow engine
```

AI-7B already has:

```text
Agent registry
AgentTask
AgentSession
Team Chat
persistence
Project Intelligence
Capability Registry
revision layer
```

Use them.

External Agent implementations are architecture references only.

---

# 127. Engineering References

### Manager / Specialist orchestration

OpenAI's current Agent documentation describes the manager pattern in which one central Agent retains conversation control and invokes Specialists for bounded tasks. This is the closest external reference to AGNES Producer + module Owners. [OpenAI Agents — manager and multi-agent patterns](https://openai.github.io/openai-agents-python/agents/) ([OpenAI GitHub][1])

### Structured Specialist output

The same documentation supports typed structured Agent outputs rather than depending on free-form prose. [OpenAI Agents structured outputs](https://openai.github.io/openai-agents-python/agents/) ([OpenAI GitHub][1])

### Parallel Specialist pattern

The official examples include parallel independent Agent execution. [OpenAI agent-pattern examples](https://github.com/openai/openai-agents-python/blob/main/examples/agent_patterns/README.md) ([GitHub][2])

### Human approval boundary

The current HITL documentation shows explicit pause, persisted run state, human approval/rejection and later resume. AGNES should retain its own runtime, but this is a useful reference for keeping proposal approval separate from execution. [OpenAI Agents human-in-the-loop guide](https://openai.github.io/openai-agents-python/human_in_the_loop/) ([OpenAI GitHub][3])

Do not install these frameworks merely because they are referenced here.

---

# 128. Suggested Commit Sequence

Keep commits independently verifiable.

For example:

```text
feat(agent): add AI-7B proposal-only protocol and execution guard

feat(agent): add script and character proposal runtimes

refactor(agent): expose reusable Director semantic proposal layer

feat(agent): add dependency and conflict analysis for change sessions

feat(agent): add Producer unified change plan compiler

feat(agent): add deterministic change plan validator

feat(revision): add story and character semantic operations

refactor(revision): expose pure project change preview core

feat(agent): add proposal projection and aggregate visual diff

feat(api): add change session review and approval endpoints

feat(ui): add AI Team change plan review experience

test(agent): add AI-7B proposal and no-apply behavioral gates
```

Do not mix unrelated cleanup.

---

# 129. Required Final Report

When AI-7B implementation is complete, report:

```text
AI-7B — Agent Change Proposal & Unified Change Plan

1. Git baseline

2. Files added

3. Files modified

4. Schema migration
   - AI-7A compatibility
   - new schema version

5. PROPOSAL_ONLY execution mode

6. ChangeRequest

7. ChangeSession
   - states
   - persistence
   - recovery

8. ModuleProposal
   - Script Writer
   - Character Agent
   - Director reuse

9. Producer routing

10. Dependency facts

11. Impact rules

12. Conflict Analyzer

13. Consultation round rules

14. User decision protocol

15. UnifiedChangePlan

16. Dependency DAG

17. ChangePlanValidator

18. Revision operation extensions
   - Story
   - Scene
   - Character
   - reused Shot operations

19. Deterministic proposal compilers

20. ProposalProjectionEngine

21. Visual Diff
   - Story
   - Character
   - Scene
   - Shot

22. Aggregate Impact

23. Team Chat UX

24. Plan approval / rejection / feedback

25. Polling and frontend restoration

26. Restart recovery

27. Stale protection

28. Idempotency

29. Project isolation

30. Director regression proof

31. Workspace regression proof

32. Production regression proof

33. Focused tests

34. Full regression

35. Frontend build

36. Browser acceptance

37. Known limitations

38. Explicit no-action proof:
    - canonical unchanged
    - Workspace unchanged
    - Production Plans unchanged
    - no ChangeSet Apply
    - no generation Job
    - no image/video generation
    - no AI-7C work
```

---

# 130. Definition of Done

AI-7B is complete only when every answer below is **YES**.

```text
Can the user describe a project change in normal language?
YES

Can Producer identify the correct affected modules?
YES

Can Script Writer produce a real structured Script proposal?
YES

Can Character Agent produce a real structured Character proposal?
YES

Can Director participate using the existing real Director proposal architecture?
YES

Are professional module contents still owned by their module Owners?
YES

Can independent proposal tasks run concurrently?
YES

Are completed proposal tasks durable across restart?
YES

Is autonomous consultation bounded?
YES

Are deterministic dependency facts used where possible?
YES

Are deterministic Impact rules used where possible?
YES

Can structural proposal conflicts be detected?
YES

Can creative ambiguity be escalated to the user?
YES

Can Producer compile one Unified Change Plan?
YES

Does the Plan explicitly separate direct changes and downstream changes?
YES

Does the Plan explicitly show unchanged scope?
YES

Does the Plan contain a valid dependency DAG?
YES

Does the deterministic Plan Validator reject incomplete or illegal plans?
YES

Does model output remain semantic rather than raw ChangeOperation JSON?
YES

Are before-values and stable IDs generated by backend code?
YES

Can Story changes be represented by the revision protocol?
YES

Can Character changes be represented by the revision protocol?
YES

Can existing Storyboard operations be reused?
YES

Can the whole Plan be projected without modifying real project state?
YES

Does the preview use real deterministic ChangeOperations?
YES

Does the preview produce a real Story Diff?
YES

Does the preview produce a real Character Diff?
YES

Does the preview reuse real Scene/Shot Diff logic?
YES

Does Impact reference real project dependencies?
YES

Are historical generated assets preserved?
YES

Can the user request changes to a Plan without overwriting history?
YES

Can a Plan have immutable versions?
YES

Can the user reject a Plan?
YES

Can the user approve a Plan?
YES

Is approval bound to the exact Plan version and source signature?
YES

Does approval survive refresh/restart?
YES

Does stale project state invalidate old approval?
YES

Does Direct Agent Chat still work?
YES

Can Direct Chat escalate cross-module requests into Team Chat?
YES

Does the existing Director Proposal workflow still work?
YES

Does the existing Creative Workspace workflow still work?
YES

Do Production Planner and Shot Producer existing workflows still work?
YES

After every AI-7B path, is canonical creative state unchanged?
YES

After every AI-7B Team Proposal path, is Workspace working state unchanged?
YES

Are Production Plans unchanged?
YES

Are production Jobs unchanged?
YES

Were zero Agnes image/video generation calls made?
YES

Is there no AI-7C Apply path?
YES
```

---

# 131. Explicit Phase Stop

When all AI-7B Gates pass:

**STOP.**

Do not continue into:

```text
AI-7C

Apply approved Change Plan

create execution Workspace

module Commit orchestration

Production Planning Request

Production Plan regeneration

Shot Producer execution

asset regeneration

long-running production scheduler

automatic continuation after approval

Reviewer-driven correction
```

The final AI-7B state should be:

```text
User Request
        ↓
Specialist Proposals
        ↓
Producer Unified Change Plan
        ↓
Validated
        ↓
Real ChangeOperation Preview
        ↓
Visual Diff / Impact
        ↓
User Approved
        ↓

APPROVED_FOR_EXECUTION

        ↓

STOP
```

AI-7C should later begin from this exact durable artifact.

The handoff contract is:

```text
approved Plan ID
+
Plan version
+
source snapshot signature
+
ModuleProposal IDs
+
Plan step DAG
+
proposal item IDs
+
deterministic preview artifact
```

AI-7C must still re-check current project truth before executing.

It must never interpret:

```text
"approved in AI-7B"
```

as permission to bypass stale checks, revision checks or existing Workspace validation.

---

# 132. Final Architectural Principle

AI-7A proved:

> **The Agent Team can understand the project without changing it.**

AI-7B must now prove:

> **The Agent Team can agree on exactly what should change, explain why, identify every important dependency, resolve or surface conflicts, translate professional proposals into deterministic project operations, and show the user the real result before execution — without changing the project.**

Only after that statement is genuinely true should AI-7C be allowed to give the Agent Team its hands.

[1]: https://openai.github.io/openai-agents-python/agents/ "https://openai.github.io/openai-agents-python/agents/"
[2]: https://github.com/openai/openai-agents-python/blob/main/examples/agent_patterns/README.md "https://github.com/openai/openai-agents-python/blob/main/examples/agent_patterns/README.md"
[3]: https://openai.github.io/openai-agents-python/human_in_the_loop/ "https://openai.github.io/openai-agents-python/human_in_the_loop/"
[4]: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents "https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents"
