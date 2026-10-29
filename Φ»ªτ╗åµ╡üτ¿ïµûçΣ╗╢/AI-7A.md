# AI-7A — Project Intelligence & Read-Only Producer

## Project Understanding · Team Group Chat · Context Packs · Read-Only Change Analysis

AI-7A begins the Project Agent stage.

The previously established roadmap defines the boundary clearly:

```text
AI-7A
Project Agent read-only:
understand the whole project and answer questions

AI-7B
Agent Proposal:
natural language → ChangeSet → Visual Diff
without Apply

AI-7C
Agent Action:
Apply ChangeSet + existing workflows + Production
```

That boundary must remain intact. fileciteturn1file0L31-L44

AI-7A therefore does **not** make the Producer autonomous over project mutation.

Its purpose is to prove something more fundamental:

> The Producer can reliably understand a large AGNES project, retrieve only the relevant context, consult the correct Specialist Agents when useful, preserve a durable team conversation, explain cross-module consequences, and answer project-level questions without changing any project truth.

The primary flow is:

```text
User
  ↓
AI Team Group Chat
  ↓
Producer
  ↓
understand question / request
  ↓
inspect project overview
  ↓
retrieve only relevant project context
  ↓
optionally consult Specialist Agents
  ↓
combine evidence
  ↓
answer user

NO project mutation
NO ChangeSet
NO Apply
NO production execution
```

For a modification request, AI-7A may go one step further:

```text
User asks for a change
  ↓
Producer analyzes intent
  ↓
relevant Specialists inspect their modules
  ↓
deterministic dependency information is collected
  ↓
Producer creates an ANALYSIS-ONLY Change Plan Draft
  ↓
user sees:

Requested changes
Downstream consequences
Unchanged areas
Open decisions

STOP
```

The draft is informational only.

It is **not executable**.

---

# 1. Why AI-7A must remain read-only

AGNES already has several powerful mutation and production systems:

```text
Creative Workspace
ChangeSet
Validation
Impact Analysis
Visual Diff

Director Proposal Runtime

Production Planner Runtime

Shot Producer Runtime

JobQueue
Agnes generation
restart recovery
```

The Project Agent must sit above these systems rather than replace them.

The architectural rule remains:

```text
Agent intelligence
        ↓
structured project systems
        ↓
deterministic workflows
        ↓
production
```

not:

```text
Agent
↓
directly edits random JSON
↓
directly calls arbitrary generation endpoints
```

The project data, revision layer, impact system, and production system must remain independently valid even if the Producer does not exist. This separation was already established as a core architecture principle. fileciteturn1file0L11-L27

AI-7A proves the top intelligence layer can **understand** that system before it is allowed to control it.

---

# 2. Current AGNES Agent baseline

Before implementation, inspect the real repository and treat current code as authoritative.

At minimum read:

```text
docs/agents/README.md
docs/agents/COLLABORATION_PROTOCOL.md

docs/agents/roles/producer.md
docs/agents/roles/script_writer.md
docs/agents/roles/character_designer.md
docs/agents/roles/director.md
docs/agents/roles/production_planner.md
docs/agents/roles/shot_producer.md
docs/agents/roles/continuity_reviewer.md
docs/agents/roles/editor.md
```

Then inspect:

```text
src/agents/models.py
src/agents/registry.py
src/agents/capabilities.py
src/agents/context.py
src/agents/tasks.py
src/agents/runtime.py

routers/agents.py
```

Also inspect the existing implementations behind:

```text
project.inspect
script inspect
character inspect
storyboard inspect
shot inspect
production-plan inspect
composition inspect
```

Use the actual capability names from the repository.

Do not rename working capabilities merely to match this document.

Also inspect:

```text
src/revision/
ProjectStore / project models
Production Plan store
AgentTask persistence
AgentSession persistence
Director ProposalStore
JobQueue
ImageBatch / VideoVersion history
```

Finally inspect the current frontend implementation for:

```text
AI Team
Agent Center
direct Agent conversations
Floating Agent UI
AgentTask state
```

Do not create a second Agent application beside the existing one.

---

# 3. Preserve the fixed Agent organization

AI-7A does not redesign the Agent roster.

The established organization remains conceptually:

```text
                         Producer
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
     Script Writer     Character Agent     Director
                                              │
                                       future Scene Workers

                            │
                            ▼
                  Production Planner
                            │
                            ▼
                     Shot Producer
                            │
                            ▼
                  Continuity Reviewer
                            │
                            ▼
                         Editor
```

The four highest-level creative control Agents remain:

```text
Producer
Script Writer
Character Agent
Storyboard Director
```

Production Planner and Shot Producer remain critically important, but they belong to the production layer.

AI-7A does not change ownership.

Producer:

```text
owns:
project-level understanding
routing
coordination
team-facing answer
analysis-only Change Plan Draft

does not own:
script content
character content
storyboard content
production planning
asset generation
```

Specialists remain authoritative for their domains.

---

# 4. The Producer must become a real Project Intelligence Agent

The current Producer must no longer be limited to answering from a shallow project summary.

It should be able to answer questions such as:

```text
How many Scenes are in this project?

Which Scenes contain Aran?

Does Xiaoyu appear less in the second half?

Which Shots use the current mechanical-dog character?

Which Scenes still have no Production Plan?

Which Shots already have videos?

Which Scenes are the longest?

Where does Scene 18 depend on Scene 17 continuity?

What changed in this Creative Workspace?

Which generated assets belong to Shot s12_c04?

Which current videos depend on an older character definition?

What would probably be affected if I changed Xiaoyu's appearance?

Which part of the project is currently incomplete?
```

This requires **semantic project inspection**, not a larger system prompt.

---

# 5. Do not put the whole project into every Producer prompt

This is one of the defining AI-7A requirements.

A large AGNES project may contain:

```text
many characters
dozens of Scene Stories
hundreds of Shots
many Production Plans
hundreds or thousands of images
many VideoVersions
AgentTasks
Creative Workspaces
revision history
```

Do not serialize all of this into every Producer turn.

The preferred model is:

```text
small project overview
        ↓
Producer decides what it needs
        ↓
semantic inspection tool
        ↓
specific module / Scene / Character / Shot
        ↓
more inspection only if necessary
```

OpenAI's current Agent SDK documentation explicitly distinguishes local application context from LLM-visible context and recommends tools as a way to fetch information on demand; its manager pattern also keeps a central orchestrator in control while Specialists provide scoped expertise. ([GitHub][1])

Anthropic's current context-engineering guidance similarly recommends high-signal, just-in-time context rather than preloading every potentially relevant item into the model window. ([Anthropic][2])

Implement this principle using the existing AGNES Runtime.

Do **not** add the OpenAI Agents SDK as a dependency merely because it is referenced here.

---

# 6. Add a Project Intelligence Layer

Create or extend a backend service dedicated to semantic read-only project inspection.

A reasonable structure is:

```text
src/agents/
    project_intelligence.py
```

or extend the existing:

```text
src/agents/context.py
```

Use whichever fits the repository better.

Do not create a large new framework.

Conceptually:

```python
class ProjectIntelligenceService:
    def inspect_overview(...)
    def inspect_script(...)
    def inspect_characters(...)
    def inspect_character_usage(...)
    def inspect_storyboard_index(...)
    def inspect_scene(...)
    def inspect_shot(...)
    def inspect_history(...)
    def inspect_production(...)
    def inspect_lineage(...)
    def inspect_dependencies(...)
```

These are semantic responsibilities.

The public Agent capability surface may remain smaller.

---

# 7. Prefer semantic inspection over low-level API mirroring

Do not expose tools like:

```text
read_project_json
read_production_plans_json
list_output_folder
read_image_batch_file
get_scene_array_index
```

The Agent should see project concepts:

```text
inspect project overview
inspect character usage
inspect Scene
inspect Shot
inspect production state
inspect history
inspect lineage
```

Anthropic's engineering guidance on Agent tools specifically recommends meaningful, intentional tool boundaries and token-efficient responses rather than blindly wrapping internal APIs one-for-one. ([Anthropic][3])

AGNES should follow that principle without introducing a new tool framework.

---

# 8. Project inspection request model

Use structured input.

For example:

```python
from typing import Literal
from pydantic import BaseModel, Field


class ProjectInspectRequest(BaseModel):
    project_name: str

    view: Literal[
        "overview",
        "script",
        "characters",
        "character_usage",
        "storyboard",
        "scene",
        "shot",
        "history",
        "production",
        "lineage",
        "dependencies",
    ]

    entity_id: str | None = None
    scene_id: str | None = None
    shot_id: str | None = None
    character_id: str | None = None

    include_history: bool = False
    limit: int = Field(default=50, ge=1, le=200)
```

Adapt fields to current IDs and models.

Do not introduce fake IDs if Characters currently use names instead of IDs.

---

# 9. Inspection responses must be bounded

Every inspection response should have explicit bounds.

For example:

```python
class ProjectInspectResult(BaseModel):
    project_name: str
    view: str

    summary: str

    data: dict

    evidence_refs: list["ProjectEvidenceRef"] = []

    truncated: bool = False
    next_cursor: str | None = None

    snapshot_signature: str | None = None
```

A tool must never accidentally return:

```text
all 800 Shots
+
all prompts
+
all image URLs
+
all video history
+
all AgentTasks
```

because the user asked:

> "Where does Xiaoyu appear?"

Instead return something like:

```json
{
  "character": "Xiaoyu",
  "scene_count": 12,
  "shot_count": 41,
  "scene_ids": [
    "s03",
    "s04",
    "s08",
    "s09"
  ],
  "more": true
}
```

Then the Producer can inspect those Scenes if needed.

---

# 10. Introduce stable evidence references

Producer answers should be traceable back to project entities.

Add a lightweight reference model.

Example:

```python
class ProjectEvidenceRef(BaseModel):
    module: str

    entity_type: str
    entity_id: str

    revision_id: str | None = None

    artifact_type: str | None = None
    artifact_id: str | None = None

    label: str | None = None
```

Examples:

```text
script / scene / s18
storyboard / shot / s18_c04
character / character / xiaoyu
production / plan / s18
asset / video_version / vv_abc123
workspace / changeset / cs_xyz
```

These references are internal structured metadata.

The final natural-language answer can remain simple.

---

# 11. Do not invent history that does not exist

AI-7A must understand existing history, but it must not fabricate a new version system merely to answer questions.

Inspect actual stores.

For example, AGNES may already have:

```text
Creative Workspace revision history
ChangeSets
ImageBatch history
VideoVersion history
Production Plan history
AgentTask history
```

while a full:

```text
Script Pocket
Character Pocket
```

may not yet be implemented.

If history is unavailable:

return:

```text
history_available = false
```

or equivalent.

Do not pretend:

```text
Script Revision S17
```

exists if the repository currently has no such revision.

AI-7A is an inspection layer.

It should expose reality.

---

# 12. Build a lightweight Project Index

Do not create Neo4j, a vector database, or a generic knowledge graph in AI-7A.

A normal Python project index is sufficient.

Conceptually:

```python
class ProjectIndex(BaseModel):
    project_name: str

    character_to_scenes: dict[str, list[str]]
    character_to_shots: dict[str, list[str]]

    scene_to_shots: dict[str, list[str]]

    shot_to_characters: dict[str, list[str]]

    scene_order: list[str]

    shot_count_by_scene: dict[str, int]

    production_state_by_scene: dict[str, str]
```

Additional mappings can be added when supported by real data.

This index should contain:

```text
IDs
counts
relationships
light metadata
```

not:

```text
full prompts
image binaries
video bytes
entire Agent histories
```

---

# 13. Build the index from current truth sources

Do not create a second persistent truth source.

The index must be derived from:

```text
canonical project
revision stores
production plans
asset/version stores
AgentTask stores
```

If it is cached, it is only a cache.

It must be safely rebuildable.

A process restart must not depend on a stale index file to understand the project.

---

# 14. Project-level overview

`overview` should answer the common first inspection.

Example shape:

```json
{
  "project_name": "My Film",
  "story_present": true,

  "characters": 4,

  "scenes": 38,
  "shots": 217,

  "storyboard": {
    "scenes_with_shots": 36,
    "empty_scenes": 2
  },

  "production": {
    "approved_plans": 31,
    "draft_or_stale_plans": 5,
    "shots_with_first_frame": 183,
    "shots_with_video": 146
  },

  "workspaces": {
    "active": 2
  }
}
```

Only use data that exists.

Do not create fake completeness metrics.

---

# 15. Storyboard inspection must scale

For a large Storyboard, Producer should first receive an index:

```text
Scene 01
12 Shots
Characters: Aran

Scene 02
9 Shots
Characters: Aran / Mechanical Dog

Scene 03
14 Shots
Characters: Xiaoyu / Aran
```

Then it may call:

```text
inspect_scene("s03")
```

Only that Scene returns individual Shot summaries.

This is essential.

Do not make:

```text
inspect_storyboard
```

return the complete contents of every Shot by default.

---

# 16. Character usage inspection

This should become a particularly strong read-only capability.

Example:

```python
def inspect_character_usage(
    project_name: str,
    character_ref: str,
) -> CharacterUsage:
    ...
```

Possible result:

```json
{
  "character": "Xiaoyu",

  "scene_count": 11,
  "shot_count": 38,

  "first_scene": "s05",
  "last_scene": "s31",

  "scenes": [
    {
      "scene_id": "s05",
      "shot_count": 3
    }
  ]
}
```

This allows the Producer to reason about questions such as:

> "Does Xiaoyu disappear too much in the second half?"

without reading hundreds of unrelated Shots.

---

# 17. Production inspection

Producer must also understand production status.

It should be able to answer:

```text
which Scenes have approved plans
which plans are stale
which Shots have selected first frames
which Shots have videos
which tasks are currently running
which Shots are blocked
```

Use the existing Production Plan, Asset, JobQueue, and AgentTask truth.

Do not infer production completion from filenames.

---

# 18. Lineage inspection

Lineage is extremely important because AGNES already preserves multiple generated versions.

Producer should be able to inspect:

```text
AgentTask
  ↓
Job
  ↓
ImageBatch / VideoVersion
```

or:

```text
Shot
  ↓
selected FirstFrame
  ↓
VideoVersion
```

Use exact identifiers.

Never answer:

> "This is probably the latest video."

when the system can resolve the actual selected or referenced version.

---

# 19. Project Context Pack

Do not send raw tool responses directly into every Specialist prompt.

Introduce a reusable context structure.

Example:

```python
class ProjectContextPack(BaseModel):
    project_name: str

    request: str

    snapshot_signature: str | None = None

    current_scope: list[ProjectEvidenceRef] = []

    project_overview: dict | None = None

    module_context: dict[str, dict] = {}

    dependency_facts: list[dict] = []

    prior_summary_refs: list[str] = []

    evidence_refs: list[ProjectEvidenceRef] = []

    truncated_sections: list[str] = []
```

A Context Pack contains only what the current Agent needs.

The previously agreed collaboration design already specifies this approach: Specialists receive the current request, relevant revisions/current state, latest Proposal summaries, dependency facts, and the specific question—not the complete team-room transcript. fileciteturn2file0L138-L175

---

# 20. Context Pack builder

Implement a deterministic builder.

Conceptually:

```python
class ProjectContextBuilder:
    async def build_for_producer(...):
        ...

    async def build_for_specialist(
        self,
        *,
        project_name: str,
        agent_id: str,
        request: str,
        scope: list[ProjectEvidenceRef],
        consultation_question: str,
    ) -> ProjectContextPack:
        ...
```

The builder decides what application data to fetch.

The LLM should not decide file paths.

---

# 21. Context budget

Use a configurable context budget.

Do not hardcode a giant prompt.

For example:

```python
@dataclass
class ContextBudget:
    overview_chars: int = 8_000
    module_chars: int = 20_000
    history_chars: int = 12_000
    total_chars: int = 48_000
```

These numbers are examples, not sacred constants.

Prefer existing token utilities if the project already has them.

Important behavior:

```text
budget exceeded
↓
summarize / truncate / return references
↓
Producer requests more detail if required
```

Never silently drop information without:

```text
truncated = true
```

metadata.

---

# 22. AI Team Group Chat

Add one project-scoped team conversation entry to the existing AI Team UI.

Conceptually:

```text
AI Team

[ Team Chat ]

Producer
Script Writer
Character Agent
Director
Production Planner
Shot Producer
Continuity Reviewer
Editor
```

The existing direct Agent conversations remain.

The established product rule is:

> Team Group Chat and individual Agent conversations coexist. Raw direct-chat messages are not copied wholesale into the group room; important outcomes appear as collaboration summary cards. fileciteturn2file0L15-L62

Do not remove the existing direct Agent interface.

---

# 23. Team Chat is project-scoped

Project A and Project B must never share team conversation state.

Use the current project identifier in the thread identity.

Conceptually:

```text
team:{project_name}
```

or a stable project ID if one exists.

Do not use:

```text
global_team_chat
```

for all projects.

Switching projects must immediately switch:

```text
Team thread
Producer context
Agent direct-session scope
Change analysis sessions
```

---

# 24. Persist Team Threads

Do not keep group chat only in Vue memory.

Introduce or extend durable conversation persistence.

Example models:

```python
class TeamThread(BaseModel):
    thread_id: str
    project_name: str

    created_at: float
    updated_at: float


class TeamMessage(BaseModel):
    message_id: str
    thread_id: str

    message_type: Literal[
        "user",
        "agent",
        "summary_card",
        "analysis_card",
        "system",
    ]

    agent_id: str | None = None

    text: str | None = None

    source_session_id: str | None = None
    source_task_id: str | None = None
    source_change_session_id: str | None = None

    created_at: float
```

Adapt to the existing AgentSession storage conventions.

Do not introduce SQLite/Postgres merely for this phase if current AGNES Agent persistence is file-backed and reliable.

---

# 25. The group transcript is not the model context

This distinction must be explicit in code.

```text
TeamThread
=
what the user sees

ProjectContextPack
=
what the Agent sees for one run
```

Do not implement:

```python
messages = load_all_team_messages()
prompt = SYSTEM + messages + project_json
```

That architecture will degrade rapidly.

---

# 26. Direct conversation → Team Summary Card

Do not copy every direct conversation turn.

When a direct Specialist conversation produces a team-relevant result, create a compact collaboration card.

Example data:

```python
class CollaborationSummaryCard(BaseModel):
    card_id: str

    project_name: str

    source_agent_id: str
    source_session_id: str

    title: str
    summary: str

    affected_modules: list[str] = []

    evidence_refs: list[ProjectEvidenceRef] = []

    cross_module_attention: bool = False

    created_at: float
```

Example visible card:

```text
Character Agent

Character discussion completed

Xiaoyu:
short hair → long black hair

Potential downstream areas:
• character reference images
• storyboard Shots containing Xiaoyu

Open direct conversation
```

The previously agreed behavior is exactly this: raw direct dialogue remains in the direct conversation; the team room receives only the meaningful summary. fileciteturn2file0L25-L62

---

# 27. Do not add a second LLM call for every message

Summary-card generation should be cheap.

First try to derive it from:

```text
AgentStructuredResult.summary
artifact_refs
warnings
suggested_next_actions
existing task metadata
```

Only if current direct-chat architecture cannot produce a reliable structured summary should a small dedicated summarization step be considered.

Do not automatically run another expensive model call after every casual question.

---

# 28. When should a direct conversation become team-visible?

In AI-7A, classify results conceptually as:

```text
local_information
local_decision
cross_module_change_intent
project_level_blocker
```

Examples:

```text
"What is Xiaoyu's current description?"
→ local_information
→ no Team Card required

"Please make Xiaoyu's hair longer."
→ local_decision / change intent
→ Team Card useful

"Remove the mechanical dog from Scene 12 onward."
→ cross_module_change_intent
→ Team Card
→ escalate to Producer analysis
```

Do not build a giant classification taxonomy.

---

# 29. Cross-module direct-chat escalation

The previously agreed collaboration rule remains:

```text
Specialist direct conversation
        ↓
cross-module consequence detected
        ↓
Change Request Draft
        ↓
Producer
        ↓
Team Group Chat
        ↓
Change Analysis Session
```

fileciteturn2file0L88-L128

In AI-7A, however:

> This escalation is analysis-only.

It does not modify any module.

---

# 30. Add ChangeRequestDraft

Do not yet use an executable ChangeRequest.

Create an explicitly non-executable model.

Example:

```python
class ChangeRequestDraft(BaseModel):
    request_id: str

    project_name: str

    source: Literal[
        "team_chat",
        "direct_agent_chat",
    ]

    source_agent_id: str | None = None
    source_session_id: str | None = None

    original_user_request: str

    primary_module: str | None = None

    suspected_modules: list[str] = []

    entity_refs: list[ProjectEvidenceRef] = []

    snapshot_signature: str | None = None

    created_at: float
```

The word `Draft` matters.

It must not be mistaken for an execution object.

---

# 31. Change Analysis Session

This is the first small piece of the future Change Session architecture.

In AI-7A it is read-only.

Suggested model:

```python
class ChangeAnalysisSession(BaseModel):
    session_id: str
    project_name: str

    request: ChangeRequestDraft

    status: Literal[
        "analyzing",
        "consulting",
        "synthesizing",
        "ready",
        "blocked",
        "failed",
        "cancelled",
        "stale",
    ]

    project_signature_at_start: str | None = None

    consultations: list["ModuleConsultation"] = []

    plan_draft_id: str | None = None

    error: str | None = None

    created_at: float
    updated_at: float
```

This object must persist.

---

# 32. Module Consultation

Producer does not invent professional module content.

When necessary, it asks the relevant Owner.

Example:

```python
class ModuleConsultation(BaseModel):
    consultation_id: str

    change_session_id: str

    agent_id: str
    module: str

    question: str

    context_refs: list[ProjectEvidenceRef]

    status: Literal[
        "pending",
        "running",
        "completed",
        "failed",
    ]

    agent_task_id: str | None = None

    result_summary: str | None = None
    artifact_refs: list[str] = []

    created_at: float
    updated_at: float
```

Use existing `AgentTask` wherever practical.

Do not create a second task engine.

---

# 33. Producer chooses Specialists

First-pass routing can combine:

```text
LLM classification
+
deterministic module ownership
```

For example:

```text
"Make Xiaoyu's jacket black"
→ character

"Change the ending"
→ script

"Make Scene 18 faster and more tense"
→ storyboard

"Remove mechanical dog from the final act"
→ script + character + storyboard likely involved
```

Producer may decide which Specialists must be consulted.

But the allowed set is deterministic:

```text
script questions
→ Script Writer

character questions
→ Character Agent

storyboard questions
→ Director

production-plan questions
→ Production Planner

production execution/status
→ Shot Producer
```

Producer cannot route a Script question to Shot Producer merely because the model feels like it.

---

# 34. Independent consultations may run in parallel

If:

```text
Script Writer
and
Character Agent
```

can independently inspect their current modules, they may be consulted concurrently.

Example:

```python
results = await asyncio.gather(
    consult_script_writer(...),
    consult_character_agent(...),
    return_exceptions=True,
)
```

However, do not make `asyncio.gather()` the source of truth.

Before starting:

```text
persist consultation
persist AgentTask
```

After each completion:

```text
persist result independently
```

If one Agent fails:

```text
successful consultation remains completed
failed consultation remains failed
```

Resume only what is missing.

OpenAI's official examples include manager-style Agents-as-tools and parallel specialist execution as standard orchestration patterns. ([GitHub][4])

Again, use those as engineering references only.

Do not add that SDK.

---

# 35. Maximum two consultation rounds in AI-7A

Do not create open-ended Agent debate.

First round:

```text
Producer
→ asks each relevant Owner
```

Then Producer checks:

```text
Do answers conflict?
Is important information missing?
Is there an unresolved dependency?
```

If necessary:

```text
Producer
→ one targeted second-round question
```

Then stop.

Do not implement:

```text
while not everyone_agrees:
    agents_chat_again()
```

AI-7A is not a free-form multi-Agent society.

---

# 36. No Specialist → Specialist delegation

Keep the existing permission model.

Specialists do not directly spawn each other in AI-7A.

Use:

```text
Specialist
→ structured result
→ Producer
→ next consultation if required
```

This preserves central coordination.

Future Storyboard Scene Workers are not implemented here.

---

# 37. Analysis-only Unified Change Plan Draft

When the user asks for a modification, Producer may synthesize a plan.

But it must use a model structurally incapable of execution.

Example:

```python
class ChangePlanDraftItem(BaseModel):
    module: str

    owner_agent_id: str

    scope_refs: list[ProjectEvidenceRef] = []

    summary: str

    reason: str

    source: Literal[
        "user_requested",
        "downstream_impact",
    ]


class UnifiedChangePlanDraft(BaseModel):
    plan_draft_id: str

    project_name: str

    change_session_id: str

    status: Literal["analysis_only"] = "analysis_only"

    direct_changes: list[ChangePlanDraftItem] = []

    downstream_changes: list[ChangePlanDraftItem] = []

    unchanged_scope: list[ProjectEvidenceRef] = []

    dependency_edges: list[dict] = []

    user_decisions_needed: list[str] = []

    consulted_agents: list[str] = []

    source_snapshot_signature: str | None = None

    execution_ready: bool = False
```

`execution_ready` must always be `False` in AI-7A.

---

# 38. Do not put ChangeOperation inside the draft

This is critical.

AI-7A plan items may say:

```text
Script Writer
Revise the final six Scene Stories so Xiaoyu rescues Aran.
```

They must not contain:

```json
{
  "op": "update_scene",
  "scene_id": "s31",
  "after": {...}
}
```

No:

```text
UpdateShot
InsertShot
DeleteShot
ReorderShots
UpdateCharacter
```

Those belong to later proposal/ChangeSet work.

---

# 39. User-facing plan presentation

The user previously established an important product requirement:

A cross-module plan must be understandable in normal language and clearly separate:

```text
what the user explicitly requested

what must change because of downstream dependencies

what will stay unchanged
```

The team-room plan card should therefore conceptually render:

```text
Change analysis

What you asked to change
────────────────────────
• Change Xiaoyu to long black hair.
• Change the ending so she rescues Aran.
• Make the final sequence more tragic.

Related changes
────────────────────────
• Character Agent:
  Xiaoyu's current visual identity would need a new version.

• Script Writer:
  the final six Scene Stories would need revision.

• Director:
  Scenes 32–34 would likely need storyboard revision after the script is finalized.

Will remain unchanged
────────────────────────
• Scenes 1–25
• Aran's character identity
• existing historical generated assets

Needs your decision
────────────────────────
• Should the existing ending be preserved as a historical version?
```

Do not expose:

```text
revision hash
ChangeOperation
DependencyEdge IDs
project.json
stale signature
```

in the primary card.

Those may appear in a developer/debug panel.

---

# 40. Plan ownership

The ownership rule is already frozen:

```text
Module Proposal / professional opinion
→ module Owner

Unified Change Plan
→ Producer

Actual module commit
→ module Owner

Production Plan
→ Production Planner

Actual generation
→ Shot Producer
```

The current collaboration design explicitly establishes this separation. fileciteturn3file0L60-L94

AI-7A implements only:

```text
professional read-only consultation
+
analysis-only Producer synthesis
```

No commits occur.

---

# 41. Production Planner boundary

Do not send raw Script/Character/Storyboard consultation output directly to Production Planner.

Production Planner answers:

> How should the final, already-decided Shot be produced?

It does not decide:

> What should the story become?

The agreed chain remains:

```text
User requirement

→ module Proposals

→ Producer Unified Change Plan

→ module Owners commit changes

→ final Script / Character / Storyboard state

→ Production Planner

→ Production Plan

→ Shot Producer
```

fileciteturn2file0L181-L239

AI-7A stops before any commit and therefore normally stops before new Production Planning.

---

# 42. Add a read-only Plan Validator

The future executable Change Plan Validator will be more extensive.

AI-7A only needs a deterministic validator appropriate for analysis.

For example:

```python
class ReadOnlyPlanValidator:
    def validate(
        self,
        draft: UnifiedChangePlanDraft,
        project_state,
    ) -> list[ValidationIssue]:
        ...
```

Validate at least:

```text
referenced project exists

referenced entities exist

module owner is correct

no Agent is assigned work outside ownership

source revision/signature is recorded

no executable ChangeOperation exists

execution_ready == false

no mutation capability is referenced

dependency edges reference real plan items

direct vs downstream categorization is valid

unknown modules are rejected
```

---

# 43. Read-only enforcement must exist in code

Do not rely only on the Producer prompt saying:

> "Do not modify the project."

Add runtime enforcement.

During a Team/Producer AI-7A run:

```text
allowed capabilities
=
read-only capability set
```

Any attempt to invoke:

```text
story.edit
character.design
storyboard.propose_change
production_plan.create
shot.first_frame
shot.last_frame
shot.video
composition.compose
```

must be rejected by capability authorization.

This is one of the most important acceptance requirements.

---

# 44. Producer prompt

Keep the Producer prompt clear and operational.

Do not write a giant personality biography.

Conceptually:

```text
You are the project-level Producer for one AGNES project.

Your job in AI-7A is to understand the user's question or change request,
inspect the project using read-only project tools,
consult the appropriate specialist when needed,
and give the user a precise project-level answer.

You do not modify project content in this phase.

You do not invent Script, Character, or Storyboard decisions on behalf
of their owning specialists.

For large projects, inspect incrementally.
Start with a project overview and fetch only the relevant scope.

When the user asks for a cross-module change:
1. identify the directly requested changes;
2. consult the relevant owners;
3. collect deterministic dependency facts;
4. separate requested changes from downstream consequences;
5. clearly state what remains unchanged;
6. produce an analysis-only plan draft.

Never claim that a change has been applied.
```

Integrate this into the existing role-contract architecture rather than duplicating role text in many places.

---

# 45. Specialist consultation prompt

A Specialist receives a Context Pack and a narrow question.

Example:

```text
You are the Character Agent.

You are being consulted by the Producer.

You are not being asked to modify the Character module.

Project:
<project>

User request:
"Make Xiaoyu's hair long and change the ending so she rescues Aran."

Relevant current character state:
...

Dependency facts:
...

Question:
"What character-level change is directly required, and what downstream
areas may become stale?"

Return:
- conclusion
- affected Character entities
- factual dependencies
- blockers or ambiguity
- evidence refs

Do not edit anything.
Do not redesign Script or Storyboard.
```

This should reuse role-contract instructions.

---

# 46. Structured Specialist result

Prefer structured results.

Example:

```python
class ConsultationResult(BaseModel):
    status: Literal[
        "completed",
        "blocked",
        "needs_user",
    ]

    summary: str

    owner_module: str

    direct_implications: list[str] = []

    downstream_facts: list[str] = []

    evidence_refs: list[ProjectEvidenceRef] = []

    user_decisions_needed: list[str] = []
```

Do not ask Specialists to return an executable operation.

---

# 47. Dependency facts should come from code where possible

The Producer should not hallucinate:

```text
Xiaoyu appears in 48 Shots
```

if the repository can calculate it.

Use deterministic services for:

```text
character → Scene usage

character → Shot usage

Scene → Shot membership

Shot order

production plan coverage

asset presence

continuity links

workspace ChangeSet impact

revision lineage
```

The model may explain those facts.

The code determines them.

---

# 48. Do not create a universal Dependency Graph framework yet

Do not introduce:

```text
Neo4j
GraphQL graph layer
Generic DAG framework
CQRS
Event sourcing
```

AI-7A only needs project-specific dependency inspection.

A normal Python representation is sufficient:

```python
class DependencyEdge(BaseModel):
    source: ProjectEvidenceRef
    target: ProjectEvidenceRef
    relation: str
```

Example relations:

```text
character_used_by_scene
character_used_by_shot

scene_contains_shot

shot_depends_on_previous_end_frame

shot_covered_by_production_plan

video_generated_from_shot

workspace_changes_shot
```

Only add relations the current repository can prove.

---

# 49. Project changes while analysis is running

A Change Analysis Session is based on a snapshot.

Record:

```text
project_signature_at_start
```

Prefer the current Creative Source Signature or equivalent existing revision signature.

Before finalizing a plan draft:

```text
current_signature
vs
session.project_signature_at_start
```

If they differ:

```text
session.status = stale
```

Do not present the plan as current.

User-facing behavior:

```text
The project changed while this analysis was running.
Re-run the analysis using the latest project state.
```

Do not auto-apply anything.

---

# 50. Team Chat message flow

A normal project question:

```text
User
"Where does Xiaoyu appear in the second half?"

        ↓

TeamMessage(user)

        ↓

Producer

        ↓

project overview

        ↓

character usage

        ↓

specific Scene inspection if needed

        ↓

TeamMessage(producer answer)
```

No Specialist consultation is mandatory if deterministic project inspection is sufficient.

---

# 51. Multi-Specialist question flow

Example:

```text
User
"Why does the final section feel inconsistent?
The story says Xiaoyu saves Aran,
but the storyboard barely shows her."

        ↓

Producer
        ↓
inspect project
        ↓
consult Script Writer
consult Director
        ↓
results persisted
        ↓
Producer compares:
Script intent
vs
Storyboard coverage
        ↓
answer
```

No modification.

---

# 52. Cross-module modification request flow

Example:

```text
User
"Make Xiaoyu's hair long,
change the ending so she rescues Aran,
and make the final fight more tragic."
```

AI-7A:

```text
Producer
  ↓
classify
  ↓
Character
Script
Storyboard
are involved
  ↓
build Context Packs
  ↓
consult Character Agent
consult Script Writer
consult Director
  ↓
collect deterministic dependency facts
  ↓
second round only if necessary
  ↓
Producer synthesizes
  ↓
ReadOnlyPlanValidator
  ↓
UnifiedChangePlanDraft
  ↓
Team Chat plan card

STOP
```

No ChangeSet.

No Workspace.

No Apply.

No Production Plan regeneration.

No Agnes.

---

# 53. Direct Specialist escalation flow

Example:

```text
User → Script Writer direct chat

"Remove the mechanical dog after Scene 12."
```

Script Writer identifies:

```text
primary module:
script

cross-module consequence:
character references
storyboard references
generated assets
```

Then:

```text
create ChangeRequestDraft
        ↓
create CollaborationSummaryCard
        ↓
post card to Team Chat
        ↓
Producer creates ChangeAnalysisSession
```

The previously agreed product behavior is that cross-module direct-chat requests are promoted to the Producer rather than allowing the Specialist to silently modify other domains. fileciteturn2file0L88-L134

---

# 54. Persistence and restart recovery

Team Chat and Change Analysis Sessions must survive backend restart.

Persist:

```text
TeamThread
TeamMessage
CollaborationSummaryCard

ChangeRequestDraft
ChangeAnalysisSession
ModuleConsultation
UnifiedChangePlanDraft

linked AgentTask IDs
```

Do not depend on:

```text
in-memory asyncio Task
Vue component state
SSE connection
```

for correctness.

Anthropic's long-running Agent engineering guidance emphasizes structured persisted artifacts between sessions so later execution can recover coherent state rather than depending on one continuous model context. ([Anthropic][5])

OpenAI's current Agents SDK similarly treats sessions as persistent conversation state and supports durable backing stores; this is a useful implementation reference even though AGNES should continue using its own existing persistence architecture. ([GitHub][6])

---

# 55. Recovery behavior

Example:

```text
Change Session CS17

Script consultation       completed
Character consultation    completed
Director consultation     running

backend stops
```

After restart:

```text
load CS17

Script result
→ keep

Character result
→ keep

Director consultation
→ reconcile using linked AgentTask

if terminal
→ consume result

if safely resumable
→ resume

if failed
→ mark only Director consultation failed
```

Do not rerun all completed Specialists.

---

# 56. Idempotency

Repeated:

```text
browser refresh
SSE reconnect
GET session
backend startup reconciliation
```

must not create:

```text
new ChangeAnalysisSession
new AgentTask
new TeamMessage
duplicate SummaryCard
```

Use stable IDs and idempotency checks.

---

# 57. No infinite recovery loop

Persist retry metadata.

For example:

```python
attempt_count: int
last_error: str | None
```

A consultation that repeatedly fails should become:

```text
failed
```

and require explicit retry/resume.

Do not let startup endlessly relaunch a broken model call.

---

# 58. Team Chat frontend

Keep the first UI functional.

Do not spend AI-7A on a large visual redesign.

The AI Team page should expose:

```text
Team Chat
```

alongside existing Agent entries.

The group room should support:

```text
normal user message
Producer response
Specialist response when consulted
Collaboration Summary Card
Analysis Plan Card
loading/running state
failed consultation state
stale analysis state
```

The frontend can expose more technical detail than the final consumer UX.

Backend correctness is more important.

---

# 59. Conversation display

A consultation may visually look like:

```text
User
Make Xiaoyu's hair long and change the ending.

Producer
I need Character, Script, and Storyboard to inspect this.

Character Agent
The appearance change is straightforward.
Xiaoyu currently appears in 41 Shots.

Script Writer
The ending change affects the final six Scene Stories.

Director
I would preserve Scenes 26–28 and revisit Scenes 29–34.

Producer
I have combined the analysis.

[View change analysis]
```

But these visible messages should be generated from persisted structured events.

Do not implement free Agent-to-Agent chat.

---

# 60. Plan card UI

Show three primary sections:

```text
Requested changes

Related changes

Unchanged
```

Then optionally:

```text
Needs your decision
```

and:

```text
Technical details
```

collapsed by default.

There must be no active:

```text
Apply
Execute
Start production
```

button in AI-7A.

If a future-looking button is shown for layout purposes, it must be disabled and clearly non-functional.

Prefer not to show it.

---

# 61. Debug details

Because the current goal is backend correctness, allow a developer/debug expansion to show:

```text
Change Session ID
project signature
consulted Agents
AgentTask IDs
evidence refs
Context Pack sizes
tool calls
consultation status
stale state
```

Do not expose these in the primary user copy.

---

# 62. Context observability

Add lightweight logging or debug metadata for each Producer run:

```text
initial project overview loaded

inspection calls:
- character_usage Xiaoyu
- scene s29
- scene s30
- production s29

Specialists consulted:
- script_writer
- director

Context Pack:
18.4 KB

truncated:
false
```

This will make future failures much easier to diagnose.

Do not log:

```text
API keys
full private prompts unnecessarily
binary media
```

---

# 63. No fake Agent progress

For AI-7A, progress can be qualitative:

```text
Understanding request
Inspecting project
Consulting Script Writer
Consulting Director
Combining analysis
Ready
```

Do not invent:

```text
37% thinking
64% understanding
```

If an AgentTask has real state, use it.

---

# 64. Existing direct conversations remain isolated

Each direct Specialist session remains scoped by:

```text
project
agent
scope
```

A Team Thread does not replace those sessions.

The team-room Summary Card references the direct session:

```text
source_session_id
```

so the user can open the original conversation.

Do not copy the original transcript into the TeamThread.

---

# 65. Do not change module ownership

AI-7A must not weaken any of these boundaries:

```text
Script Writer
→ Script only

Character Agent
→ Character only

Director
→ Storyboard only

Production Planner
→ Production Plan only

Shot Producer
→ production execution only

Reviewer
→ review findings only

Editor
→ composition only
```

Producer coordinates.

Producer does not become a universal editor.

---

# 66. AI-7A must not activate reserved mutation capabilities

If a capability is currently reserved or unimplemented, leave it that way unless AI-7A explicitly needs a **read-only** inspect capability.

Do not implement in this phase:

```text
story.edit

character.design / character.update

new Storyboard modification capabilities

production_plan.create changes

shot generation changes

review execution

composition execution
```

Director's already-existing proposal runtime may remain implemented, but AI-7A Producer must not call it.

---

# 67. Do not change Director Proposal behavior

Existing:

```text
Director
→ semantic proposal
→ Workspace
→ ChangeSet
→ Preview / Impact
```

is already a proven pipeline.

Do not refactor it into the new Change Analysis Session.

AI-7A analysis is a higher-level project understanding layer.

The Director proposal system remains a separate existing capability.

---

# 68. Do not change Shot Producer

No modifications to:

```text
JobQueue
first-frame generation
last-frame generation
video generation
Agnes video polling
video_id recovery
VideoVersion persistence
```

unless a read-only inspection adapter needs to read their state.

No generation is triggered by AI-7A.

---

# 69. Do not change Agnes integration

No new Agnes generation path.

If the current Agent model uses Agnes for text reasoning, reuse the existing model client exactly as other Agents do.

Do not introduce:

```text
another LLM client
hardcoded model ID
new API key
```

No image or video generation belongs to AI-7A.

---

# 70. Do not introduce another Agent framework

Do not add:

```text
LangGraph
CrewAI
AutoGen
OpenAI Agents SDK
new MCP server
generic workflow engine
```

The existing AGNES Agent Runtime already owns:

```text
Agent registry
permissions
AgentTask
AgentSession
delegation
capabilities
persistence
```

Use it.

External implementations are references, not dependencies.

---

# 71. Suggested code organization

Choose the smallest structure that fits the actual repository.

A reasonable implementation may look like:

```text
src/agents/
    project_intelligence.py
    team_chat.py
    change_analysis.py
```

while continuing to reuse:

```text
models.py
context.py
runtime.py
tasks.py
capabilities.py
registry.py
```

Frontend may add:

```text
TeamChatPanel
CollaborationSummaryCard
ChangeAnalysisCard
```

inside the current Agent Center structure.

Do not create:

```text
src/project_agent_framework/
src/multi_agent_v2/
```

---

# 72. Suggested Project Intelligence code

Example:

```python
class ProjectIntelligenceService:
    def __init__(
        self,
        *,
        project_loader,
        plans_reader,
        workspace_store,
        agent_task_store,
    ):
        self.project_loader = project_loader
        self.plans_reader = plans_reader
        self.workspace_store = workspace_store
        self.agent_task_store = agent_task_store

    def overview(self, project_name: str) -> ProjectInspectResult:
        project = self.project_loader(project_name)

        scene_count = len(project.scenes)
        shot_count = sum(len(scene.shots) for scene in project.scenes)

        return ProjectInspectResult(
            project_name=project_name,
            view="overview",
            summary=f"{scene_count} scenes, {shot_count} shots",
            data={
                "scene_count": scene_count,
                "shot_count": shot_count,
                "character_count": len(project.characters),
            },
        )
```

This code is illustrative.

Use existing repository helpers instead of duplicating load logic.

---

# 73. Suggested Character usage code

```python
def character_usage(project, character_name: str) -> dict:
    scenes = []
    shots = []

    for scene in project.scenes:
        scene_match = False

        for shot in scene.shots:
            if character_name in shot.characters:
                shots.append(shot.shot_id)
                scene_match = True

        if scene_match:
            scenes.append(scene.scene_id)

    return {
        "character": character_name,
        "scene_ids": scenes,
        "shot_ids": shots,
        "scene_count": len(scenes),
        "shot_count": len(shots),
    }
```

Adapt equality/identity rules to real Character refs.

Do not assume names are always unique if the current model has stable Character IDs.

---

# 74. Suggested persisted Change Session runner

Conceptually:

```python
async def continue_change_analysis(session_id: str):
    session = store.load(session_id)

    if session.status in TERMINAL:
        return session

    if project_signature(session.project_name) != session.project_signature_at_start:
        session.status = "stale"
        store.save(session)
        return session

    pending = [
        c for c in session.consultations
        if c.status == "pending"
    ]

    if pending:
        await run_consultations(pending)

    session = store.load(session_id)

    failures = [
        c for c in session.consultations
        if c.status == "failed"
    ]

    if failures:
        session.status = "blocked"
        store.save(session)
        return session

    session.status = "synthesizing"
    store.save(session)

    draft = await producer_synthesize_read_only_plan(session)

    issues = read_only_validator.validate(draft)

    if issues:
        session.status = "failed"
        session.error = serialize_issues(issues)
        store.save(session)
        return session

    plan_store.save(draft)

    session.plan_draft_id = draft.plan_draft_id
    session.status = "ready"
    store.save(session)

    return session
```

Do not copy this blindly.

Reuse existing Runtime and Task lifecycle.

---

# 75. API surface

Prefer extending the existing Agent router.

Exact URLs should follow current conventions.

Conceptually the frontend needs operations equivalent to:

```text
GET team thread for current project

POST user message to team thread

GET team messages

GET change analysis session

POST resume failed/stopped analysis

GET analysis plan draft
```

Do not create one endpoint per tiny internal operation.

Producer/Specialist orchestration belongs behind backend business services.

---

# 76. Sending a Team message

Conceptually:

```text
POST team message
        ↓
persist user TeamMessage
        ↓
create Producer AgentTask / run
        ↓
Producer inspects project
        ↓
optional consultation tasks
        ↓
persist intermediate team events
        ↓
persist Producer answer
```

The HTTP request should not need to remain open for an arbitrarily long multi-Agent analysis.

If current Agent Runtime already supports asynchronous AgentTask execution, reuse it.

---

# 77. SSE / frontend updates

Reuse existing AgentTask / SSE infrastructure where possible.

Do not create another realtime bus unless truly necessary.

The UI can refresh state using:

```text
existing AgentTask SSE
+
normal TeamThread fetch
```

or existing polling fallback patterns.

Persistence remains truth.

SSE remains notification.

---

# 78. Project isolation

Every AI-7A object must be project-scoped:

```text
TeamThread
ChangeRequestDraft
ChangeAnalysisSession
PlanDraft
Context Pack
evidence refs
consultation AgentTasks
```

Project A must never load:

```text
Project B team messages
Project B plan drafts
Project B evidence
```

This should be enforced in backend lookups, not only in frontend filtering.

---

# 79. Security against stale browser state

Every request should include or resolve the intended project.

Do not let:

```text
browser switches A → B
old A response arrives later
```

overwrite B's TeamChat state.

Frontend should reject responses whose:

```text
project_name
```

does not match the active project.

Backend AgentTask ownership must also record project scope.

---

# 80. AI-7A does not implement Script/Character revision history

Do not let the "Pocket" discussion explode this phase.

AI-7A may **inspect** whichever historical state already exists.

It must not now build the complete future:

```text
Script Pocket
Character Pocket
Project Checkpoint
history garbage collection
restore workflow
```

Those systems require their own mutation semantics.

However, design evidence/history APIs so future module histories can plug into them without replacing the Project Intelligence layer.

---

# 81. AI-7A does not implement Scene Workers

The Director may eventually spawn temporary Scene Workers for very large Storyboards.

Do not implement them here.

AI-7A can inspect hundreds of Shots through scoped tools without parallel Scene Agents.

Scene Workers become useful when actual Storyboard modification/planning workload is distributed.

This phase is read-only.

---

# 82. AI-7A does not implement Reviewer

Reviewer remains separate.

Project Intelligence may read existing asset/production metadata.

It does not perform visual quality review.

No:

```text
video frame extraction
visual judging
automatic retry
```

in AI-7A.

---

# 83. AI-7A does not implement autonomous production

Do not allow:

```text
Producer
→ Planner
→ Shot Producer
→ Agnes
```

automatically from Team Chat.

Even if the user says:

> "Make the whole movie now."

AI-7A should explain current project readiness and what would be required.

It must not start production.

---

# 84. Failure behavior

Keep failure states understandable.

Examples:

```text
PROJECT_NOT_FOUND

ENTITY_NOT_FOUND

HISTORY_NOT_AVAILABLE

CONTEXT_BUILD_FAILED

SPECIALIST_CONSULTATION_FAILED

PROJECT_CHANGED_DURING_ANALYSIS

ANALYSIS_VALIDATION_FAILED

AGENT_PERMISSION_DENIED
```

Reuse existing error structures where practical.

Do not introduce a giant new exception hierarchy.

---

# 85. Producer answer integrity

Producer should not make unsupported project claims.

A project-level factual answer should come from:

```text
Project Intelligence result
or
Specialist consultation
```

If data is unavailable:

say so.

Example:

```text
I can see the current Character definition,
but this project does not currently have Character revision history,
so I cannot compare it with an older Character version.
```

Do not hallucinate a history.

---

# 86. User ambiguity

For a normal read-only question, Producer may ask one clarifying question if necessary.

For a modification analysis:

if ambiguity changes the creative meaning substantially, include:

```text
user_decisions_needed
```

Example:

```text
"Make the ending more tragic."

Needs decision:
Should Xiaoyu survive?
```

Do not have Producer silently choose a major creative interpretation merely to complete a Plan Draft.

---

# 87. No hidden professional rewriting by Producer

This is especially important.

Producer may say:

```text
Script Writer recommends revising Scenes 29–34.
```

Producer must not silently rewrite that proposal as:

```text
Scene 31 will now end with Aran dying...
```

unless that content came from Script Writer.

Producer owns:

```text
combination
ordering
dependency
coordination
communication
```

not professional module content.

---

# 88. Minimal testing philosophy

Do not turn AI-7A into a giant testing project.

The important tests are behavioral boundaries.

Focus on:

```text
project understanding correctness

context scoping

project isolation

read-only authorization

consultation persistence

restart recovery

stale analysis

no duplicate tasks/messages

no accidental mutation
```

Normal unit details can follow existing repository practices.

---

# 89. Core acceptance case — project question

Project contains:

```text
40 Scenes
280 Shots
Xiaoyu in 11 Scenes / 38 Shots
```

User asks:

```text
"Where does Xiaoyu appear in the second half?"
```

Expected behavior:

```text
Producer loads overview

Producer inspects Xiaoyu usage

Producer inspects only relevant later Scenes if needed

Producer answers accurately
```

Verify:

```text
Producer did NOT receive all 280 Shot definitions in one prompt.

project.json unchanged.

No Workspace created.

No ChangeSet created.

No production task created.
```

---

# 90. Core acceptance case — multi-module question

User asks:

```text
"The ending says Xiaoyu rescues Aran,
but does the storyboard actually show enough of her?"
```

Expected:

```text
Producer
→ Script context
→ Storyboard context
→ optional Script Writer + Director consultation
→ combined answer
```

Answer should identify actual mismatch if present.

No mutation.

---

# 91. Core acceptance case — cross-module change analysis

User:

```text
"Make Xiaoyu's hair long,
change the ending so she rescues Aran,
and make the final fight more tragic."
```

Expected:

```text
Character consultation
Script consultation
Director consultation

possibly in parallel

↓
Producer Plan Draft
```

Plan card contains:

```text
Requested changes

Related changes

Unchanged

Needs your decision
```

Verify:

```text
execution_ready == false

0 ChangeOperations

0 Workspaces created by this session

0 Production Plans modified

0 image/video Jobs

0 Agnes generation calls
```

---

# 92. Core acceptance case — direct chat escalation

User enters Script Writer direct chat:

```text
"Remove the mechanical dog after Scene 12."
```

Expected:

```text
direct conversation remains in Script Writer session

TeamChat does NOT receive full transcript

TeamChat receives one collaboration summary card

cross-module ChangeRequestDraft created

Producer analysis session starts or is ready to start

no module modified
```

---

# 93. Core acceptance case — casual direct chat

User asks Character Agent:

```text
"What is Xiaoyu's current appearance description?"
```

Expected:

```text
direct Agent answers

no unnecessary Team Summary Card

no Producer intervention

no Change Analysis Session
```

---

# 94. Core acceptance case — restart

Create:

```text
ChangeAnalysisSession
```

with:

```text
Script consultation      completed
Character consultation   completed
Director consultation    running
```

Restart backend.

Expected:

```text
same ChangeAnalysisSession ID

same completed results

no duplicate Script consultation

no duplicate Character consultation

Director task reconciled/resumed

one final Plan Draft only
```

---

# 95. Core acceptance case — project changes mid-analysis

Start analysis.

Then change the canonical project's creative state through an existing legitimate mechanism.

Before Producer finalizes:

```text
signature changed
```

Expected:

```text
ChangeAnalysisSession = stale

no Plan Draft presented as current

user informed analysis must refresh
```

---

# 96. Core acceptance case — read-only hard guard

Attempt through the AI-7A Producer execution path to invoke:

```text
storyboard.propose_change
production_plan.create
shot.video
```

Expected:

```text
permission denied
```

and:

```text
no underlying workflow invocation
```

This test is mandatory.

---

# 97. Large-project context acceptance

Generate or use a fixture with several hundred Shots.

Ask:

```text
"Which Scenes contain Character X,
and which of those still have no completed video?"
```

Verify:

```text
bounded tool responses

scoped inspection

no giant whole-project prompt

correct result

reasonable response time
```

Do not benchmark LLM latency precisely.

The purpose is to catch context explosion.

---

# 98. Manual browser acceptance

Verify the actual UI path:

```text
open project

open AI Team

open Team Chat

ask project overview question

see Producer answer

open Character direct conversation

ask local question

return Team Chat
→ no duplicate raw chat

ask cross-module modification in direct Specialist chat

return Team Chat
→ Summary Card exists

open analysis

see:
Requested changes
Related changes
Unchanged

refresh browser
→ Team Thread restored
```

Do not spend the phase on pixel-perfect styling.

---

# 99. GitHub / engineering references

These references are for architecture and implementation ideas only.

Do not copy frameworks wholesale into AGNES.

### Manager / Agents-as-tools pattern

OpenAI Agents SDK example:

https://github.com/openai/openai-agents-python/blob/main/examples/agent_patterns/agents_as_tools.py

Official documentation describes the manager pattern as a central Agent invoking Specialists while retaining control of the conversation. ([GitHub][1])

### Parallel independent Specialist work

OpenAI examples index:

https://github.com/openai/openai-agents-python/tree/main/examples/agent_patterns

The official examples include parallel Agent execution for independent work. ([GitHub][4])

### Context management

OpenAI context documentation:

https://github.com/openai/openai-agents-python/blob/main/docs/context.md

It separates application-local context from LLM-visible context and describes tool-based on-demand retrieval. ([GitHub][7])

### Session persistence

OpenAI session documentation:

https://github.com/openai/openai-agents-python/blob/main/docs/sessions/index.md

The current SDK includes persistent session backends and bounded history retrieval; this is useful as a reference for durable project-scoped Team Threads. ([GitHub][6])

### Deferred / on-demand tool loading reference

OpenAI example:

https://github.com/openai/openai-agents-python/blob/main/examples/tools/tool_search.py

This demonstrates a current implementation of exposing large tool surfaces lazily rather than loading every tool eagerly. ([GitHub][8])

AGNES does not need this exact SDK feature, but the design principle is relevant to future large Agent capability sets.

### Anthropic context engineering

Official engineering article:

https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

Anthropic recommends high-signal context and just-in-time retrieval for Agent systems instead of continually expanding model context with every available fact. ([Anthropic][2])

### Anthropic Agent tool design

Official engineering article:

https://www.anthropic.com/engineering/writing-tools-for-agents

This is useful when designing AGNES semantic inspection tools: tools should have meaningful boundaries, clear inputs/outputs, and token-efficient responses. ([Anthropic][3])

### Long-running Agent state

Official Anthropic engineering article:

https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

It emphasizes carrying structured artifacts across separate Agent sessions instead of relying on one uninterrupted context window. ([Anthropic][5])

---

# 100. Explicitly out of scope

AI-7A must not implement:

```text
Script mutation

Character mutation

new Storyboard mutation

natural language → ChangeOperation

natural language → ChangeSet

Visual Diff from Producer plan

Apply

Workspace → canonical merge

automatic Production Planner invocation

automatic Shot Producer invocation

Agnes image generation

Agnes video generation

Reviewer

automatic retry loop

Editor execution

Script Pocket

Character Pocket

Project Checkpoint restore

Scene Worker spawning

Specialist → Specialist delegation

recursive Agent debate

autonomous Producer production loop

new generic Agent framework
```

---

# 101. Definition of done

AI-7A is complete when the following statement is genuinely true:

> A user can open the AI Team Group Chat and ask the Producer a project-level question about a large AGNES project. The Producer can inspect the project incrementally, retrieve only relevant context, consult the correct Specialist Agents when useful, cite or internally retain exact project evidence, and answer accurately without mutating the project.

And for modification requests:

> The Producer can analyze a cross-module request, consult the relevant module Owners, combine deterministic dependency facts, and show a clear analysis-only plan separated into requested changes, downstream consequences, unchanged scope, and unresolved decisions—while being technically incapable of applying the change.

The final hard invariants are:

```text
Project Agent understands the project
        ✓

Team Group Chat persists
        ✓

Direct Agent chats remain separate
        ✓

Important direct results can surface as Team Summary Cards
        ✓

Large project context is loaded incrementally
        ✓

Specialist consultations are scoped and structured
        ✓

Consultations can run concurrently when independent
        ✓

Analysis survives refresh/restart
        ✓

Project changes make old analysis stale
        ✓

Producer owns project-level synthesis
        ✓

Specialists retain domain ownership
        ✓

Producer cannot call mutation capabilities
        ✓

No ChangeSet is created
        ✓

No project content is modified
        ✓

No production is started
        ✓
```

Stop after these conditions are satisfied.

Do not begin executable Agent Proposal or Agent Action work as part of AI-7A.

[1]: https://github.com/openai/openai-agents-python/blob/main/docs/agents.md?utm_source=chatgpt.com "openai-agents-python/docs/agents.md at main · openai/openai-agents-python · GitHub"
[2]: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents?_bhlid=03c5c643f100889cdb09ac6e4a86834901945f67&utm_source=chatgpt.com "Effective context engineering for AI agents \ Anthropic"
[3]: https://www.anthropic.com/engineering/writing-tools-for-agents?_bhlid=90bbebb46e902b035258269e5c2824b76c092ac7&utm_source=chatgpt.com "Writing effective tools for AI agents—using AI agents \ Anthropic"
[4]: https://github.com/openai/openai-agents-python/blob/main/examples/agent_patterns/README.md?utm_source=chatgpt.com "openai-agents-python/examples/agent_patterns/README.md at main · openai/openai-agents-python · GitHub"
[5]: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents?lid=1f0n56CPItf3Nm9NR&utm_source=chatgpt.com "Effective harnesses for long-running agents \ Anthropic"
[6]: https://github.com/openai/openai-agents-python/blob/main/docs/sessions/index.md?utm_source=chatgpt.com "openai-agents-python/docs/sessions/index.md at main · openai/openai-agents-python · GitHub"
[7]: https://github.com/openai/openai-agents-python/blob/main/docs/context.md?utm_source=chatgpt.com "openai-agents-python/docs/context.md at main · openai/openai-agents-python · GitHub"
[8]: https://github.com/openai/openai-agents-python/blob/main/examples/tools/tool_search.py?utm_source=chatgpt.com "openai-agents-python/examples/tools/tool_search.py at main · openai/openai-agents-python · GitHub"
