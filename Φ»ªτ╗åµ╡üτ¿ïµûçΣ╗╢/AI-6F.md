# AI-6F — Shot Producer Real Production Runtime & Reliability Hardening

AI-6F.0 — Durable AgentTask Bridge & Production Progress Contract has been completed.

Its verified result is:

```text
Producer
→ AgentTask
→ Shot Producer
→ durable fake Job
→ AgentTask.execution_ref
→ HTTP returns while task remains running
→ Job terminal
→ AgentTaskSettler
→ completed / failed
```

AI-6F.0 also proved:

```text
backend restart
→ existing AgentTask is recovered
→ existing Job is reattached
→ no duplicate Job submission
→ same AgentTask settles from the existing Job
```

AI-6F.0 deliberately sent:

```text
0 real Agnes requests
```

AI-6F now connects this proven lifecycle bridge to the project's **existing real Shot production pipeline**.

This is not a new generation system.

This is not a new Agent workflow engine.

This is the phase where Shot Producer receives real production capability.

The target runtime path is:

```text
Producer
    ↓ delegate

AgentTask
    ↓ durable lifecycle

Shot Producer capability
    ↓

existing JobQueue
    ↓

existing real JOB_HANDLER
    ↓

existing Shot generation business logic
    ↓

existing AgnesClient
    ↓

existing ImageBatch / Candidate / VideoVersion / Asset state
    ↓

Job terminal
    ↓

AgentTaskSettler
    ↓

AgentTask completed / failed
```

The most important rule of this phase is:

> Replace fake execution with real existing execution without changing the durable lifecycle architecture proven by AI-6F.0.

---

# 0. Stability definition for AI-6F

Do not call AI-6F complete merely because a real Agnes request succeeds once.

AI-6F succeeds only when all of the following are true:

```text
real first-frame generation works through AgentTask

real last-frame generation works through AgentTask

real video generation works through AgentTask

HTTP does not remain open for the long-running generation

AgentTask remains durably linked to the real Job

existing JobQueue remains the execution source of truth

existing Shot asset/version history remains correct

backend restart does not create duplicate generation

existing video_id recovery still works

AgentTask never completes before the real production artifact is persisted

failed production settles the AgentTask correctly

old generated assets are not destructively erased

missing/stale production planning becomes a blocker rather than improvisation

real progress comes from real Job / Agnes state

all previous tests remain green
```

This phase proves:

```text
Shot Producer execution stability
```

It does NOT yet prove:

```text
whole multi-Agent team autonomy
```

Those are different claims.

---

# 1. Mandatory checkpoint gate before editing

Before modifying AI-6F code:

```bash
git status
git diff --stat
git log --oneline -25
```

AI-6F.0 must be a clearly identifiable checkpoint.

Expected history should conceptually contain:

```text
b76e201  AI-6E.0 Agent Role Contracts
f5dc5a0  AI-6E Production Planner Runtime
<new>    AI-6F.0 Durable AgentTask Bridge
```

If AI-6F.0 is still uncommitted:

**do not mix AI-6F implementation into it.**

First verify AI-6F.0 after a real backend restart:

```text
JOB_HANDLERS register
startup settler starts
running fake AgentTask reattaches
terminal fake Job settles
no duplicate fake Job
```

Then report that AI-6F.0 needs its checkpoint.

Do not silently commit or push unless the user explicitly requested a commit.

Never:

```text
git reset
git clean
git stash
checkout unrelated files
discard user work
```

---

# 2. Read the real code before designing adapters

Inspect the current repository and find the actual implementations.

At minimum read:

```text
src/agents/models.py
src/agents/tasks.py
src/agents/runtime.py
src/agents/capabilities.py

AI-6F.0 Shot Producer capability adapter
AI-6F.0 fake Shot handlers
AI-6F.0 settlement implementation

docs/agents/COLLABORATION_PROTOCOL.md
docs/agents/roles/producer.md
docs/agents/roles/production_planner.md
docs/agents/roles/shot_producer.md

AGNES_API_FACTS.md

existing JobQueue
Job model
Job persistence
JOB_HANDLERS registration
startup Job recovery
SSE / job progress stream

routers/shots.py
shot service/business helpers
prompt builders
link/opening-source helpers

Project / Scene / Shot models
ImageBatch
Candidate
VideoVersion
first_frame_batches
last_frame_batches
selected first/last frame
final_video

Production Planner model/store
production_plans.json reader
approved/stale rules

SceneAutopilotRun
ProjectProductionRun
activate_planned_shot or equivalent
existing production checkpoint logic

AgnesClient:
generate_image
create_video
poll_video
video recovery
rate limiter
quota
ImageHost/public URL handling
```

Use the real current file/function names.

Do not force the repository to match examples in this prompt.

---

# 3. Preserve the existing Agent role contract

The current responsibility chain remains:

```text
Director
= decides what the Shot should be

Production Planner
= decides how that Shot should be produced

Shot Producer
= executes that production strategy

Continuity Reviewer
= evaluates the generated result
```

AI-6F must not blur these responsibilities.

Shot Producer owns:

```text
production execution
first-frame generation
last-frame generation
video generation
candidate/version creation
execution retry where explicitly allowed
production Job tracking
```

Shot Producer does NOT own:

```text
Storyboard redesign

Shot creative rewriting

Production Plan invention

Character identity redesign

quality acceptance

automatic continuity review

final composition

story rewriting
```

If execution cannot proceed because an upstream artifact is invalid:

return a structured blocker.

Do not silently fix the upstream domain.

---

# 4. Production Plan is a gate, not a suggestion

The Shot Producer contract is:

> Execute an approved Production Plan.

Before Agent-controlled production, resolve:

```text
project
scene
shot
current production plan
planned shot entry
```

Verify using the existing real semantics:

```text
plan exists
plan belongs to the correct Scene
plan is approved
plan is not stale
shot exists
planned shot exists
required dependency state is valid
```

If a plan is:

```text
missing
draft
stale
structurally incompatible
```

Shot Producer must NOT invent another production strategy.

Conceptually return:

```text
blocker_code
human-readable reason
scope
recommended upstream owner
```

For example:

```text
PRODUCTION_PLAN_MISSING
→ Production Planner

PRODUCTION_PLAN_STALE
→ Production Planner

SHOT_STRUCTURE_CHANGED
→ Director / Planner depending on existing contract
```

Use the current AgentTask failure/blocker representation.

Do NOT redesign the persistent AgentTask status model solely for this.

Do NOT automatically delegate to Planner in AI-6F.

Specialist → Specialist autonomous chaining remains out of scope.

---

# 5. Do not create another execution engine

Hard red line.

Do NOT add:

```text
AgentProductionRun
AgentShotRun
AgentGenerationQueue
AgentImagePipeline
AgentVideoPipeline
AgentScheduler
AgentWorker
```

The repository already has:

```text
JobQueue
SceneAutopilotRun
ProjectProductionRun
existing handlers
existing checkpoints
existing Asset history
existing video_id recovery
```

Reuse them.

AI-6F is an **adapter/integration phase**.

Not an execution rewrite.

---

# 6. Keep the AI-6F.0 lifecycle bridge generic

AI-6F.0 already established:

```text
AgentTask.execution_ref
AgentTaskSettler
startup reconciliation
idempotent attachment
```

Do not fork this into separate lifecycle implementations such as:

```text
FirstFrameTaskSettler
LastFrameTaskSettler
VideoTaskSettler
```

unless the current abstraction genuinely requires a tiny adapter.

Prefer:

```text
same durable bridge
+
real Job kind
+
artifact resolver
```

The lifecycle must remain:

```text
AgentTask pending
↓
real Job persisted
↓
execution_ref persisted
↓
AgentTask running
↓
HTTP returns
↓
Job continues
↓
Job terminal
↓
artifact validated
↓
AgentTask settles
```

---

# 7. Fake Job kinds must not become a second real pipeline

Inspect the AI-6F.0 fake Job handlers.

They existed only to prove lifecycle behavior.

For AI-6F:

either remove them from the production capability path or keep them test-only.

Do NOT implement:

```text
agent.real.first_frame
agent.real.last_frame
agent.real.video
```

by copying all existing generation logic into new handlers.

The preferred semantic is:

```text
Shot Producer capability
→ enqueue/attach existing real Job kind
```

If a thin adapter is genuinely necessary to carry Agent metadata:

it may validate metadata and call the existing business helper.

It must NOT contain another Agnes generation implementation.

---

# 8. AgentTask idempotency boundary

This rule must be explicit.

## Same AgentTask

The same AgentTask represents the same requested execution.

Therefore:

```text
same AgentTask
+ retrying HTTP
+ backend restart
+ Runtime reconstruction
+ settlement retry
```

must NOT generate another candidate/video.

It reattaches to the existing execution.

## New explicit user regeneration request

A new user intent such as:

```text
“这张首帧不好，再生成一次”
```

is new work.

It should produce:

```text
a new AgentTask
```

and therefore may create:

```text
a new ImageBatch / Candidate
```

The previous valid batch remains history.

Do not make idempotency accidentally prevent legitimate new user-requested generation.

The invariant is:

> same task = same execution
> new user regeneration intent = new task / new version

---

# 9. Preserve historical assets

Regeneration means:

```text
create another version
```

not:

```text
erase the old result
```

The project already has concepts such as:

```text
first_frame_batches
last_frame_batches
candidate_id
selected candidate
video_versions
final_video
```

Reuse these exact existing semantics.

AI-6F must not replace them with Agent-specific asset fields.

Example:

```text
first-frame generation #1
→ batch A

user dislikes result

first-frame generation #2
→ batch B
```

Expected:

```text
batch A still exists
batch B exists
```

Do not destructively replace A.

The same principle applies to video versions.

---

# 10. Never identify output by “latest array item” when a stable ID exists

Do not settle a task using assumptions such as:

```python
shot.first_frame_batches[-1]
shot.video_versions[-1]
```

unless the existing Job contract explicitly guarantees that identity.

Concurrent/manual/recovered work can make “latest” ambiguous.

Prefer stable references already produced by existing infrastructure:

```text
batch_id
candidate_id
video_version_id
job_id
origin metadata
```

If current Job terminal result already stores these:

reuse it.

If the handler currently cannot expose a stable output reference to the generic Agent settler:

make the smallest backward-compatible enhancement necessary.

Do not redesign Asset models.

---

# 11. Job origin / traceability

A real production Job initiated by an AgentTask must remain traceable.

Reuse existing Job metadata/origin fields where possible.

Conceptually the system should be able to answer:

```text
this Job came from which AgentTask?
which capability?
which project?
which scene?
which shot?
which Production Plan?
```

Do not duplicate fields unnecessarily.

At minimum the relationship:

```text
AgentTask ↔ Job
```

must be durable in at least one authoritative direction already supported by AI-6F.0.

Never make correctness depend only on in-memory maps.

---

# 12. Capability 1 — `shot.first_frame`

Connect the real capability first.

Do not connect last-frame or video until the first-frame Gate passes.

Required path:

```text
Producer
→ AgentTask
→ shot_producer
→ shot.first_frame
→ existing JobQueue
→ existing first-frame handler
→ existing Agnes image generation
→ existing ImageBatch/Candidate persistence
→ Job done
→ AgentTask completed
```

Preserve all existing first-frame semantics:

```text
prompt building
character references
style
aspect ratio
image size tier
candidate generation
ImageBatch
Candidate IDs
public Agnes URLs
local download
error handling
```

Do NOT create another first-frame prompt builder.

Do NOT call Agnes directly from `src/agents`.

---

# 13. First-frame completion semantics

A `shot.first_frame` AgentTask is complete when:

```text
the requested generation Job is terminal-success
AND
the resulting ImageBatch / Candidate data is durably persisted
```

It does NOT need to pretend the user has selected a candidate if candidate selection is a separate current operation.

If current workflow semantics are:

```text
generate batch
→ user chooses candidate
```

preserve that.

Do not silently auto-select simply because the work came from an Agent.

If another already-existing deterministic execution mode, such as SceneAutopilotRun, has its own automatic seeded-selection behavior:

preserve that behavior only inside that existing execution mode.

Do not globally change manual/Agent `shot.first_frame` semantics.

---

# 14. First-frame regeneration semantics

If the Shot already has valid historical batches:

an explicit new `shot.first_frame` AgentTask must create a new batch according to the current business rules.

It must not:

```text
delete previous batch
overwrite previous candidates
pretend the previous execution never happened
```

After completion, AgentTask result/metadata should reference the new stable output IDs if the current schema supports it.

---

# 15. Gate A — first-frame acceptance

Before implementing `shot.last_frame`, prove all of:

```text
1. FakeAgnes test:
   Producer → AgentTask → real first-frame Job handler

2. AgentTask goes pending → running.

3. delegate HTTP returns before fake image work finishes.

4. existing first-frame Job is linked through execution_ref.

5. success persists a real ImageBatch/Candidate structure.

6. AgentTask settles only after persistence.

7. Job failure settles AgentTask failed.

8. Runtime restart reattaches to the same Job.

9. restart does not create another ImageBatch.

10. repeated delegate/recovery of same AgentTask does not enqueue another Job.

11. a separate new AgentTask may create a new batch.

12. old batches remain preserved.

13. progress is reconstructed from persistent Job state.

14. existing direct first-frame API behavior is unchanged.
```

Run focused tests.

Do not move to Gate B until these pass.

---

# 16. Capability 2 — `shot.last_frame`

Only after Gate A is green, connect:

```text
shot.last_frame
```

Required path:

```text
AgentTask
→ Shot Producer
→ existing last-frame Job
→ existing last-frame handler
→ Agnes image generation
→ existing last-frame batch/candidate state
→ settle
```

Preserve existing prerequisites.

For example, if current production logic requires:

```text
selected/effective first frame with usable URL
keyframes-compatible production mode
```

then missing prerequisites are blockers.

Do not silently:

```text
generate a new first frame
change i2v → keyframes
change keyframes → i2v
invent another plan
```

---

# 17. Last-frame input correctness

Do not rebuild its reference-image logic in Agent code.

Continue using the existing validated handler/business helper.

The existing generation system, not the Agent, owns details such as:

```text
effective first-frame reference
character reference URLs
preserve/change prompt semantics
ImageHost requirements
Agnes image request shape
```

Agent code should provide scope and authorization.

Production code performs production.

---

# 18. Gate B — last-frame acceptance

Prove all of:

```text
1. valid keyframes Shot can run last-frame through AgentTask.

2. missing selected/effective first frame does not call Agnes.

3. incompatible Production Plan does not call Agnes.

4. missing/stale plan produces a structured blocker.

5. success produces the existing real last-frame batch/version structure.

6. AgentTask settles from that real Job.

7. old last-frame history is preserved.

8. same AgentTask cannot duplicate generation after restart.

9. a genuinely new AgentTask can create another version.

10. direct existing last-frame API behavior remains unchanged.
```

Only then move to video.

---

# 19. Capability 3 — `shot.video`

This is the highest-risk part of AI-6F.

Connect it last.

Required path:

```text
Producer
→ AgentTask
→ shot_producer
→ shot.video
→ existing video Job
→ existing video handler
→ AgnesClient.create_video
→ video_id persisted
→ AgnesClient.poll_video
→ download
→ VideoVersion persisted
→ real returned metadata persisted
→ Job done
→ AgentTask settle
```

Do not put any video polling loop inside Agent Runtime.

Do not let AgentTask poll Agnes directly.

The existing video Job remains responsible for:

```text
rate limiting
submission
video_id persistence
polling
download
recovery
VideoVersion creation
video-end handling
```

AgentTask only follows the real execution.

---

# 20. Preserve Agnes video recovery

This is a hard invariant.

If:

```text
POST /v1/videos succeeded
video_id was persisted
backend crashes
```

restart must:

```text
recover the existing remote Agnes task
```

not:

```text
POST /v1/videos again
```

AI-6F must not weaken the existing:

```text
video_id → resume poll
```

behavior.

The Agent layer should observe the recovered Job and remain attached to it.

There must never be a second remote video generation merely because:

```text
Agent Runtime restarted
settler restarted
HTTP was retried
browser refreshed
SSE reconnected
```

---

# 21. Respect the existing Agnes API truth source

Do not invent Agnes parameters.

Follow `AGNES_API_FACTS.md`.

In particular:

```text
endpoint/model names come from configuration

do not hardcode model IDs in business/Agent code

video image inputs require valid public URLs

video uses existing create_video/poll_video implementation

num_frames validation remains in existing Agnes/video layer

actual returned seconds/size remain authoritative

rate limiting remains centralized
```

Do not move Agnes request-shape knowledge into `src/agents`.

---

# 22. Video preflight remains deterministic

Before a video request is actually sent, continue using the current production preflight.

Depending on current code, this may include:

```text
effective first frame ready

first-frame public URL ready

keyframes last frame ready

audio/timing prerequisite if currently required

quota

duration / frame count validity

production-plan compatibility
```

Do not ask the LLM whether a prerequisite is “probably okay”.

Code decides.

If preflight fails:

```text
0 real video submissions
```

and the AgentTask receives a truthful structured failure/blocker.

---

# 23. Do not silently add `shot.audio`

AI-6F's currently contracted execution capabilities are:

```text
shot.first_frame
shot.last_frame
shot.video
```

If the real video helper requires audio:

honor the existing prerequisite.

Do not silently broaden this phase into:

```text
shot.audio
TTS Agent
audio redesign
```

If audio is missing:

return the existing appropriate blocker.

A later phase can explicitly assign audio ownership if necessary.

---

# 24. Video result integrity

A video AgentTask must NOT settle `completed` merely because:

```text
Job.status == done
```

if the expected production result is missing.

Before successful settlement, resolve the persisted result.

Verify according to existing models that the generated output exists, such as:

```text
VideoVersion exists
version_id is resolvable
local output is recorded where required
remote URL is recorded where applicable
actual returned duration metadata is persisted
```

Use current real invariants.

If Job says success but the expected persistent output cannot be resolved:

do not falsely report AgentTask completed.

Treat that as an execution integrity error.

Do not regenerate automatically.

---

# 25. Gate C — video automated acceptance

Before any real-network smoke test, automated FakeAgnes/integration tests must prove:

```text
1. Producer can delegate shot.video.

2. AgentTask becomes running immediately.

3. request returns while video Job remains non-terminal.

4. video Job uses the existing real handler path.

5. create_video is called once.

6. video_id persistence occurs.

7. simulated long polling leaves AgentTask running.

8. poll progress can be reflected without fake percentages.

9. completed video creates/resolves existing VideoVersion.

10. only after VideoVersion persistence does AgentTask complete.

11. terminal remote failure → Job failed → AgentTask failed.

12. local poll/network interruption with recoverable video_id does not create another video.

13. backend reconstruction reattaches same AgentTask + same Job + same video_id.

14. repeated startup settlement is idempotent.

15. no second create_video call occurs after restart.

16. direct non-Agent shot.video behavior still works.

17. existing JobQueue video serialization/rate limiting remains unchanged.
```

This Gate must include a call-count assertion using FakeAgnes/test doubles:

```text
create_video call count == 1
```

across simulated restart.

This is one of the most important AI-6F tests.

---

# 26. Gate D — real Agnes acceptance

After all automated gates pass, perform one minimal real Agnes production acceptance.

Do NOT make real Agnes calls in the normal automated test suite.

Use the configured real client only for explicit acceptance.

Prefer one controlled keyframes Shot because it can exercise:

```text
real first frame
real last frame
real video
```

within one Shot.

Use an expendable test Shot/project.

Do not overwrite a valuable user-approved production asset.

The real acceptance should prove:

```text
Producer/Agent endpoint
→ AgentTask
→ Shot Producer
→ real JobQueue
→ real first-frame Agnes request
→ persisted candidate/batch
→ existing candidate selection mechanism as required
→ real last-frame Agnes request
→ persisted last-frame output
→ real video Agnes request
→ video_id persisted
→ poll
→ download
→ VideoVersion persisted
→ AgentTask completed
```

Never log or print the Agnes API key.

---

# 27. Mandatory real restart recovery acceptance for stability claim

To claim **Shot Producer runtime stability**, perform one controlled restart during the real video path.

Recommended point:

```text
video Job has successfully submitted
AND
video_id has been persisted
BUT
video has not yet settled completed
```

Then:

```text
stop backend cleanly

restart backend

reload persisted AgentTask

reload persisted JobQueue

verify same AgentTask.execution_ref

verify same job_id

verify same persisted video_id

verify no new AgentTask

verify no new video Job

verify system resumes polling existing Agnes video

wait for terminal result

verify same AgentTask settles
```

The acceptance report must explicitly state whether another remote video submit occurred.

Expected:

```text
no
```

If this test cannot be completed for an environmental reason:

do not claim full real restart stability.

Report exactly what was and was not proven.

---

# 28. Progress must describe real work

AI-6F.0 created the progress contract.

AI-6F must now populate it from real production state.

Examples:

```text
Shot Producer
正在生成 s03_c02 首帧

Job
queued
```

then:

```text
Shot Producer
正在生成 s03_c02 首帧

Job
running
```

video may become:

```text
Shot Producer
正在生成 s03_c02 视频

Agnes video task submitted
video_id = persisted
progress = <real progress if Agnes provides it>
```

Do not expose fake:

```text
47% analyzing
63% thinking
```

For LLM work:

qualitative phase only.

For Job/Agnes work:

real deterministic state/progress.

---

# 29. SSE is notification, persistence is truth

Continue using the existing JobQueue/SSE infrastructure.

Do not create:

```text
/api/agent-progress-stream-v2
AgentSSEBus
AgentWebSocketServer
```

unless absolutely required by the real repository.

Agent progress correctness must survive:

```text
SSE disconnect
browser refresh
network reconnect
backend restart
```

The source of truth is:

```text
persisted AgentTask
+
persisted Job
+
persisted Run/Asset
```

SSE only makes UI updates faster.

---

# 30. Minimal Agent UI verification

Do not redesign Agent Center in AI-6F.

However, if current AgentTask UI already displays task state/progress, verify that a real Shot Producer task can truthfully show:

```text
Agent:
Shot Producer

scope:
project / scene / shot

capability:
first-frame / last-frame / video

task:
running

execution:
queued / running / polling / completed / failed

linked Job:
job_id

user action:
only when genuinely required
```

If a tiny mapping is required for the real Job kinds:

make the smallest change.

No visual overhaul.

No new page.

---

# 31. User-action-required behavior

Do not confuse:

```text
long-running
```

with:

```text
waiting for user
```

Examples:

## Generated first-frame candidates exist, but selection is required

If current workflow requires explicit user selection:

the generation AgentTask may be completed because its requested generation work completed.

The surrounding Producer/UI may show:

```text
需要选择首帧候选
```

Do not keep the generation Job falsely “running”.

## Production prerequisite missing

Example:

```text
no approved Plan
```

The task should fail/block according to existing semantics.

It is not a running generation.

Be truthful about the state.

---

# 32. Failure taxonomy

Preserve underlying real errors and make them understandable.

At minimum distinguish conceptually:

```text
authorization / permission failure

invalid scope

missing Project / Scene / Shot

missing Production Plan

stale Production Plan

preflight dependency failure

image generation failure

image download/persistence failure

video submission failure

video remote terminal failure

video polling/recovery failure

artifact integrity failure

cancelled existing Job
```

Do not create a giant new enum unless the current models need it.

Structured metadata/error codes are preferred where existing schemas support them.

User-facing text should remain concise.

Logs may retain detailed diagnostic information.

Never include API keys.

---

# 33. Retry behavior

Do not turn every failure into automatic regeneration.

A failed Job discovered during:

```text
startup recovery
settlement
AgentTask reload
```

must remain failed.

It must NOT automatically submit a new generation.

For AI-6F:

```text
execute operation is idempotent
```

If the current Runtime already has an explicit, proven retry operation:

preserve its semantics.

If Agent-level retry semantics are not yet fully defined:

do not invent a large retry framework in this phase.

A new explicit user regeneration request may create a new AgentTask/version.

---

# 34. Production Plan changes during execution

Do not let Shot Producer silently switch plans halfway through a task.

Persist enough traceability to know which plan the task was accepted against, using existing task metadata/input/result where possible.

If current production execution already freezes/snapshots the Plan through:

```text
SceneAutopilotRun
ProjectProductionRun
```

reuse that mechanism where applicable.

Do NOT build another independent Agent plan-snapshot system.

If the low-level single-shot capability cannot safely use a modified plan after enqueue:

prefer failing/blocking on a detected incompatible change rather than silently executing a different strategy.

Do not overwrite historical assets generated under an earlier plan.

---

# 35. Existing SceneAutopilotRun semantics are not to be duplicated

Inspect how the existing executor handles:

```text
approved Plan
activate_plan
opening_source
source_shot_id
source_version
link_mode
dependencies
seeded candidate selection
checkpoint
video version
video_end
restart
```

AI-6F must not reimplement those semantics inside Agent Runtime.

If an Agent capability needs behavior already owned by the existing executor:

reuse/extract the same existing business helper.

Do not produce two competing definitions of:

```text
“execute the Production Plan”
```

There must remain one production truth.

---

# 36. Canonical mutation boundary remains unchanged

AI-6F does not implement:

```text
Workspace make-official

canonical story merge

Director proposal auto-apply

story mutation

character mutation
```

Production execution may continue to perform the same canonical production-state mutations that the existing proven executor already performs at execution time.

Do not broaden that permission.

In particular:

```text
approved Production Plan
```

does not mean:

```text
Shot Producer may creatively rewrite the Shot
```

---

# 37. No automatic Reviewer

When a video completes:

AI-6F stops at:

```text
production completed
```

Do NOT automatically invoke:

```text
Continuity Reviewer
```

AI-6G owns that.

The future chain will be:

```text
Shot Producer completes asset
↓
Reviewer receives generated result
↓
Review finding / retry proposal
```

Do not implement it early.

---

# 38. No automatic Producer cascade

Do not implement:

```text
Planner completed
→ automatically start Shot Producer

Shot Producer completed
→ automatically start Reviewer

Reviewer rejected
→ automatically regenerate
```

That orchestration needs explicit policy later.

AI-6F proves reliable execution primitives first.

This is intentional.

---

# 39. No `shot.produce` mega-capability yet

Do not introduce a broad autonomous capability such as:

```text
shot.produce_everything
scene.generate_all
agent.make_shot
```

merely to make the demo look more autonomous.

The first real execution layer stays explicit:

```text
shot.first_frame
shot.last_frame
shot.video
```

Once those primitives are stable, later Producer orchestration may compose them safely.

Do not hide unstable chaining inside Shot Producer.

---

# 40. Cancellation

Do not redesign cancellation in AI-6F.

If the existing JobQueue already supports cancellation:

preserve it.

Do not claim that cancelling a local Job necessarily cancels an already-submitted remote Agnes task unless the real API/current implementation actually supports that.

Never fake remote cancellation semantics.

If this creates a known limitation:

report it.

---

# 41. Concurrency and rate limits

Do not add Agent-specific concurrency.

Agent generation must go through the same central JobQueue/rate-limiter path as manual/UI production.

Therefore:

```text
manual video generation
Agent video generation
Autopilot video generation
```

must share the same actual video execution constraints.

Do not create a second worker pool that bypasses Agnes rate limits.

Do not create another API key pool.

Do not attempt to “speed up Agent execution” by breaking existing queue rules.

---

# 42. Tests — authorization and role boundaries

Add tests proving:

```text
shot_producer can execute shot.first_frame

shot_producer can execute shot.last_frame

shot_producer can execute shot.video

unauthorized agents cannot execute these operations

Production Planner cannot execute real Shot generation

Director cannot execute Shot production

Shot Producer cannot silently call production_plan.create

Shot Producer cannot mutate Storyboard to satisfy a generation failure
```

Reuse current registry/can_execute semantics.

---

# 43. Tests — Plan gates

Prove:

```text
approved valid Plan
→ production may proceed

missing Plan
→ no Agnes call

draft/unapproved Plan
→ no Agnes call

stale Plan
→ no Agnes call

planned Shot missing
→ no Agnes call
```

Use FakeAgnes call counts.

This must be deterministic.

---

# 44. Tests — artifact/version history

Prove:

```text
new first-frame task creates new batch
old batch remains

new last-frame task creates new batch/version
old result remains

new video task creates new VideoVersion
old VideoVersion remains

same AgentTask recovery creates no additional batch/version
```

Explicitly distinguish:

```text
new task
```

from:

```text
same task replay
```

---

# 45. Tests — settlement integrity

Prove at minimum:

```text
Job queued
→ AgentTask running

Job running
→ AgentTask running

Job done + artifact persisted
→ AgentTask completed

Job failed
→ AgentTask failed

Job done but expected artifact missing
→ AgentTask must not falsely complete

settle called twice
→ same final result

runtime restart
→ same result reconstructed
```

---

# 46. Tests — restart windows

Simulate the dangerous crash windows.

## Window A

```text
Job persisted
execution_ref not yet fully observed by caller
crash
```

Recovery must find/reuse the Job.

## Window B

```text
image generation completed
artifact persisted
AgentTask not yet settled
crash
```

Recovery must settle from existing output.

No second image generation.

## Window C

```text
video submitted
video_id persisted
poll not finished
crash
```

Recovery must continue the original video.

No second `create_video`.

## Window D

```text
video completed and VideoVersion persisted
AgentTask not yet settled
crash
```

Recovery settles existing task.

No second video.

These tests are central to this phase.

---

# 47. Regression suites

Run focused suites around:

```text
agents

AI-6D Director

AI-6E Production Planner

AI-6F.0 lifecycle bridge

shot services

first-frame generation

last-frame generation

video generation

JobQueue

startup recovery

video recovery

SceneAutopilotRun

ProjectProductionRun

candidate/version history

SSE/progress
```

Then run the full suite.

The previous full baseline was reported as:

```text
305 passed
```

Do not assume the final number must remain exactly 305 because AI-6F will add tests.

The requirement is:

```text
all pre-existing passing tests remain passing
+
new AI-6F tests pass
```

Do not fix unrelated failures unless AI-6F caused them.

Report unrelated pre-existing failures separately.

---

# 48. Required implementation order

Do not connect all three capabilities in one edit and debug them together.

Strict execution order:

```text
Step 1
real first-frame adapter

↓ tests

Gate A pass

Step 2
real last-frame adapter

↓ tests

Gate B pass

Step 3
real video adapter

↓ tests

Gate C pass

Step 4
real Agnes smoke

Step 5
real mid-video restart recovery

Gate D pass

Step 6
full regression
```

If a Gate fails:

fix that layer first.

Do not continue downward hoping later code fixes it.

---

# 49. Explicit non-goals

AI-6F must NOT implement:

```text
new ProductionRun type

new JobQueue

new workflow engine

new Agnes client

AgentImageGenerator

AgentVideoGenerator

parallel Agent worker system

shot.audio unless explicitly already contracted

Continuity Reviewer Runtime

review.shot

review.scene

Script Writer mutation

whole-story rewrite

Character Designer mutation

Director expansion

autonomous Producer

automatic Specialist → Specialist delegation

automatic Planner → Shot Producer cascade

automatic Shot Producer → Reviewer cascade

automatic Reviewer retry loop

shot.produce mega-capability

Project-level Agent autopilot

canonical Workspace merge

make-official

new composition pipeline

Agent Center visual redesign

LangGraph
CrewAI
AutoGen
Celery
Redis
```

---

# 50. Completion condition

AI-6F is complete only when this statement is truthful:

```text
A Producer can delegate a real Shot production capability
to the Shot Producer through AgentTask.

The request returns without waiting for long Agnes work.

The AgentTask stays durably attached to the existing real Job.

The existing JobQueue and existing generation handlers perform
the actual production.

The resulting existing ImageBatch / Candidate / VideoVersion
is durably persisted.

The AgentTask settles from that real persistent execution state.

If the backend restarts, the same AgentTask reattaches to the
same Job / video_id and does not submit duplicate generation.

If the user explicitly asks for another generation later,
a new AgentTask creates another version without erasing history.

If the Production Plan is missing/stale/invalid,
Shot Producer refuses to improvise and reports the blocker.

Progress shown to the user reflects real execution state.
```

---

# 51. Stability claim wording

At the end, do NOT overclaim:

```text
“All Agents are stable”
```

AI-6F may claim:

```text
“Shot Producer real execution runtime is stable under the tested
first-frame / last-frame / video / restart / idempotency conditions.”
```

The following are still future work:

```text
Continuity Reviewer
story revision
character revision
Producer autonomous routing
multi-Agent recovery policy
```

Be precise.

---

# 52. Final report — mandatory

Return a detailed numbered report containing:

```text
1. Git baseline
2. files changed
3. AI-6F.0 code reused unchanged
4. any AI-6F.0 bridge changes and why they were necessary

5. shot.first_frame real mapping
6. shot.last_frame real mapping
7. shot.video real mapping

8. Production Plan preflight design
9. role-boundary enforcement

10. real Job kinds used
11. Job origin / AgentTask traceability
12. execution_ref shape

13. first-frame artifact settlement
14. last-frame artifact settlement
15. video artifact settlement

16. new-task vs same-task idempotency behavior
17. candidate/batch history behavior
18. video version history behavior

19. startup recovery behavior
20. video_id recovery behavior
21. duplicate-generation protection

22. progress mapping
23. SSE integration
24. user-action-required behavior

25. Gate A tests + result
26. Gate B tests + result
27. Gate C tests + result

28. real Agnes first-frame result
29. real Agnes last-frame result
30. real Agnes video result

31. real mid-video backend restart procedure
32. job_id before/after restart
33. AgentTask id before/after restart
34. video_id before/after restart
35. whether another create_video submission occurred

36. focused regression results
37. full regression result
38. final test count

39. manual acceptance limitations
40. known risks
41. anything intentionally deferred to AI-6G
```

Also answer these questions explicitly with:

```text
yes / no
```

Expected answers:

```text
Did AI-6F create a new ProductionRun model?
NO

Did AI-6F create a new JobQueue?
NO

Did AI-6F rewrite the existing image generation workflow?
NO

Did AI-6F rewrite the existing video generation workflow?
NO

Did AI-6F allow Shot Producer to rewrite Storyboard?
NO

Did AI-6F allow Shot Producer to invent a Production Plan?
NO

Did AI-6F automatically invoke Reviewer?
NO

Did automated tests call real Agnes?
NO

Did manual AI-6F acceptance call real Agnes?
YES

Can the same AgentTask accidentally create duplicate generation
during normal retry/restart recovery?
NO
```

Finally state separately:

```text
Real Agnes restart recovery proven:
YES / NO

Shot Producer first-frame stability proven:
YES / NO

Shot Producer last-frame stability proven:
YES / NO

Shot Producer video stability proven:
YES / NO

Whole multi-Agent team stability proven:
NO
```

Do not claim `YES` unless the corresponding test was actually executed successfully.

---

# 53. Stop after AI-6F

When AI-6F is complete:

stop.

Do not begin AI-6G.

The next intended phase is:

```text
AI-6G — Continuity Reviewer Runtime
```

AI-6G will answer a different question:

```text
“Shot Producer can reliably make the asset.

Can another Specialist now reliably inspect that result,
compare it against creative intent / Production Plan / continuity,
and produce a structured accept/reject/retry recommendation
without silently changing production?”
```

Do not implement that during AI-6F.
