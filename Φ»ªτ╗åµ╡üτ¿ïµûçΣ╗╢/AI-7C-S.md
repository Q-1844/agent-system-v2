# AI-7C-S — Post-Audit Stabilization, Runtime Wiring Closure & Real Acceptance

## Purpose

AI-7C implementation is already complete.

This task does **not** re-implement AI-7C and does **not** begin AI-8.

This is a post-completion stabilization round triggered by an independent code audit that found several real gaps which the original AI-7C automated Gates did not catch.

The current picture is:

```text
AI-7C core protocol
→ largely sound

but

Frontend execution lifecycle
→ contains confirmed HIGH bugs

Character synchronization production path
→ not genuinely exercised end-to-end

Public affected-production wiring
→ requires verification/fix

Manifest correctness/recovery
→ requires targeted hardening

Browser acceptance
→ insufficiently executed

Real character-sync acceptance
→ missing
```

The goal of this round is:

> Close the difference between “AI-7C architecture/tests look correct” and “the actual user-visible AI-7C production path is demonstrably connected, recoverable, refresh-safe, and capable of exercising the real Character synchronization path.”

Do not expand product scope.

Do not redesign AI-7C.

Do not begin Reviewer / AI-8.

---

# 1. Known Audit Findings

Treat the following as the starting audit ledger, but verify the real repository before changing code.

## Confirmed HIGH — independently reproduced

### H1 — execution polling destroys itself after the first session tick

Known area:

```text
src/store/changeSession.ts
```

Observed root cause:

```text
approved_for_execution
```

is currently treated as terminal/inactive by frontend session lifecycle logic even though the backend keeps the ChangeSession in that status while a separate ChangeExecution is active.

Observed sequence:

```text
Execute
→ execution polling starts
→ session poll returns approved_for_execution
→ _set(session)
→ status logic considers session inactive/terminal
→ stopPoll()
→ execution gets one load
→ no later tick
→ UI progress freezes
```

Do not assume the correct long-term fix is merely deleting one value from one Set.

The actual conceptual bug is:

```text
Proposal/session lifecycle
≠
Execution lifecycle
```

`approved_for_execution` may simultaneously mean:

```text
proposal work is settled
AND
the approved Plan is executable or currently executing
```

Frontend lifecycle predicates must model those facts separately.

---

### H2 — refresh cannot restore approved/executing ChangeSession

Known area:

```text
restoreActive()
src/store/changeSession.ts
```

Current filtering treats:

```text
approved_for_execution
```

as something that cannot be restored, making the later approved-session handling branch effectively unreachable.

Result:

```text
execution active
→ browser refresh
→ approved session filtered out
→ execution not restored
→ UI loses active execution state
```

Fix this together with H1 using a coherent lifecycle model.

---

### H3 — real Character visual synchronization path has not been proven end-to-end

Relevant areas include:

```text
execution_service.py
character_views_handler.py
gated_real_7c.py
handler registration / production startup
```

The audit found that:

```text
4b_sync tests
→ inject _fake_view_generator

earlier real acceptance
→ exercised Shot-oriented production

Character-sync real acceptance
→ deferred / not genuinely completed
```

Therefore a passing Character-sync unit/integration test does not currently prove that:

```text
public AI-7C execution
→ Character sync stage
→ production handler
→ _real_character_view_generator
→ existing Character generation workflow
→ real persisted views
```

actually works.

This must be closed.

---

# 2. Important Medium / Low Audit Areas

Known audit areas also include:

```text
M1 / F4
public execution endpoint does not fully connect
character_sync / planning into affected_production

M2
ProductionExecutionManifest update_scene correctness

M3
Character synchronization concurrency / locking

M5
stale-blocked execution still presents a recovery/resume action

L1
production Character-sync handler registration / real-path availability

L4
manifest crash recovery / recomputation

L6 / F5
frontend does not correctly consume execution eligibility / blocking state
```

There were also lower-priority findings such as:

```text
M4
L2
L3
L5
```

Do not invent their meaning from these labels.

Recover their exact evidence from the previous audit context if available, or independently rediscover them from the repository.

Do not modify code merely because an old severity label exists.

---

# 3. Revised Engineering Principle

The original AI-7C specification separated:

```text
ChangeSession
→ polling-first durable state

Production jobs
→ JobQueue SSE
```

Keep that architecture.

But frontend state must now explicitly distinguish at least these concepts:

```text
proposal/session settled?

plan executable?

execution exists?

execution active?

execution waiting for user?

execution terminal?

execution blocked?

production run active?
```

Do not infer all of these from:

```text
ChangeSession.status
```

alone.

In particular:

```text
approved_for_execution
```

must not automatically mean:

```text
nothing needs restoring
nothing needs polling
nothing is active
```

because the execution is a separate durable object.

Prefer clear predicates such as conceptually:

```text
isProposalSettled(session)

isExecutionActive(execution)

isExecutionRestorable(execution)

canStartExecution(session)

shouldPollSession(session, execution)

shouldPollExecution(execution)
```

Exact names should follow the existing frontend style.

Do not create unnecessary abstractions merely to match these example names.

---

# 4. Revised Agent Workflow

Do not let several agents simultaneously modify AI-7C core files.

Use agents primarily for **independent evidence gathering and verification**.

The main Claude Code session is the coordinator and eventual implementer.

## Audit Wave — four read-only agents

Spawn four independent subagents.

They must not edit files.

If custom subagents are available, deny Write/Edit tools for this wave.

Each agent receives only its domain and must inspect the repository independently before seeing other agents' conclusions.

### Agent A — Frontend Execution Lifecycle Reviewer

Own:

```text
changeSession.ts
ChangePlanReview.vue
execution restoration
polling
eligibility
blocked / resume UX
refresh behavior
```

Must independently prove or reject:

```text
H1
H2
M5
L6 / F5
```

For every finding report:

```text
Finding ID
CONFIRMED / REJECTED / PARTIAL

exact files/functions

exact control-flow path

minimum user-visible reproduction

root cause

smallest safe repair boundary

regression risks

required browser acceptance
```

For HIGH findings, a line-number observation alone is insufficient.

Trace the entire path.

---

### Agent B — Public API / Execution Wiring Reviewer

Start from the **public AI-7C execute API**, not from internal helper functions.

Trace:

```text
execute endpoint
→ execution service
→ stages
→ manifest
→ character synchronization
→ production planning
→ ProductionRun
```

Verify:

```text
M1 / F4

affected_production genuinely reaches
Character synchronization when required

affected_production genuinely reaches
Production Planner when required

creative_only does not accidentally run production
```

A direct unit call to an internal service does not prove the public path is connected.

Report exact missing links.

---

### Agent C — Character Sync Runtime Reviewer

Own:

```text
character_views_handler.py

_real_character_view_generator

handler registry/startup

Character generation workflow reuse

durability

idempotency

locking/concurrency
```

Verify:

```text
H3
L1
M3
```

Answer explicitly:

```text
Who registers the handler?

What production event invokes it?

Does the production path use the real generator by default?

Can two resumes generate the same Character views twice?

What durable state proves completion?

What happens after backend restart?

Does it reuse the existing Character generation workflow,
or directly call Agnes from new 7C code?
```

No code modifications in this audit wave.

---

### Agent D — Manifest / Recovery Reviewer

Own:

```text
ProductionExecutionManifest

update_scene action

impact recomputation

startup/restart reconciliation

execution resume

stage persistence

idempotency
```

Verify:

```text
M2
L4
M4 if related
remaining recovery findings
```

Pay particular attention to:

```text
persisted manifest truth
vs
recomputed current truth
```

and whether a crash can leave a manifest that causes:

```text
missing work
duplicate work
wrong Scene scope
whole-project regeneration
```

---

# 5. Independent Review Rules

The four agents must report independently first.

Do not show Agent A's conclusion to Agent B before B finishes, etc.

This avoids correlated confirmation.

After all four reports return, the main session creates one deduplicated issue ledger:

```text
issue
root cause
severity
affected layers
dependencies
repair checkpoint
required proof
```

If two findings have the same root cause, merge them.

For example:

```text
H1 + H2
```

may share the lifecycle-model root cause while still requiring two separate acceptance scenarios.

After deduplication:

```text
HIGH
→ must be independently rechecked before implementation

MEDIUM
→ reproduce or establish direct code evidence

LOW
→ only repair if still valid and within this stabilization scope
```

Do not turn the audit into a speculative refactor.

---

# 6. Implementation Ownership

After the audit wave:

**the main Claude Code session performs the repairs sequentially.**

Do not have multiple write-capable agents simultaneously edit:

```text
changeSession.ts
execution_service.py
manifest code
handler registry
production execution code
```

These areas are strongly coupled.

A fresh read-only reviewer may be spawned after each checkpoint.

That reviewer must not be the agent that wrote the implementation.

---

# 7. Checkpoint S1 — Frontend Execution Lifecycle

Fix:

```text
H1
H2
M5
L6 / F5
```

as one frontend lifecycle checkpoint.

## 7.1 Session vs Execution state

Do not reuse a single:

```text
TERMINAL_STATUSES
ACTIVE_STATUSES
```

classification for every purpose if it conflates proposal and execution semantics.

Preserve legitimate AI-7B meaning:

```text
approved_for_execution
=
proposal is complete and approved
```

while allowing AI-7C meaning:

```text
approved_for_execution
+
active ChangeExecution
=
execution is still active/restorable
```

The final implementation may use separate predicates, separate polling ownership, or another simple solution consistent with current store design.

Keep it simple.

---

## 7.2 Polling

Execution polling must continue until the **execution** reaches a real terminal/blocking/waiting condition.

A session state update must not accidentally kill execution polling.

Prove:

```text
Execute Plan

→ first tick

→ second tick

→ later tick

→ execution stage continues changing
```

not merely:

```text
one successful loadExecution()
```

Avoid brittle fixed sleeps in tests where possible.

---

## 7.3 Refresh restoration

Refresh during execution must recover:

```text
ChangeSession
approved Plan
ChangeExecution
current execution stage
production run if present
```

and resume polling.

An approved session with an active/restorable execution must not be filtered out before its execution state is inspected.

Also preserve the case:

```text
approved_for_execution
but execution has not started yet
```

The user must still be able to return to the approved Plan and execute it.

---

## 7.4 Stale-blocked UX

If execution is blocked because:

```text
PROJECT_CHANGED_AFTER_APPROVAL
```

or equivalent stale state:

do not present:

```text
Resume
Try to resume
```

as a normal recovery action.

The server already treats this as non-rebasable.

Frontend should guide toward the existing correct flow:

```text
Update / regenerate Change Plan
→ review new Preview
→ approve again
```

Do not invent automatic rebase.

---

## 7.5 Eligibility

Frontend must consume authoritative backend execution eligibility/block reason where available.

Do not duplicate complex eligibility rules in Vue if the backend already returns them.

Frontend may perform obvious UI guards, but server truth wins.

---

# 8. Checkpoint S1 Browser Gate

This checkpoint is not complete from TypeScript/unit tests alone.

Add a small Playwright/E2E regression suite if the project does not already have suitable coverage.

Tests should verify **user-visible behavior**, not implementation internals.

Minimum cases:

### Browser Case 1 — polling survives multiple ticks

```text
approved Plan
→ Execute Plan
→ execution begins
→ UI continues progressing after the first polling interval
→ timeline does not freeze
```

### Browser Case 2 — refresh restores active execution

```text
execution active
→ browser refresh
→ same execution is restored
→ current stage visible
→ polling continues
```

### Browser Case 3 — approved but not executed

```text
approved_for_execution
→ refresh
→ approved Plan is restored
→ Execute Plan remains available
```

### Browser Case 4 — stale blocked

```text
execution blocked as stale
→ no misleading Resume button
→ correct re-plan action is presented
```

### Browser Case 5 — completed execution

```text
execution completes
→ polling stops
→ refresh restores truthful completed state
→ no duplicate execution
```

Use Playwright's user-facing locators and retrying/web-first assertions.

Do not make final browser acceptance depend only on CSS classes or internal Pinia state.

---

# 9. Checkpoint S2 — Public Affected-Production Wiring

Next repair the public execution path.

Start from the actual endpoint a frontend user invokes.

Do not start from:

```text
execution_service._some_internal_helper()
```

and conclude the product is connected.

Prove this path:

```text
approved Plan
→ POST execute
→ ChangeExecution created/restored
→ creative replay/commit
→ ProductionExecutionManifest
→ Character sync when required
→ Production Planner when required
→ scoped production
```

Fix:

```text
M1 / F4
H3 / L1 integration point
```

as required.

---

# 10. Character Sync Handler Registration

The production Character synchronization handler must be registered in the real application startup/runtime.

It is not enough for a test to instantiate it manually.

Trace and prove:

```text
server startup
→ production handler registry
→ character-sync kind
→ real handler
```

A missing registry edge is a production bug even if direct handler tests pass.

Add an integration assertion that boots the same application/dependency graph used by the API and confirms this handler is available.

---

# 11. Real Character Generator Path

The production Character-sync path must default to:

```text
_real_character_view_generator
```

or the current production equivalent.

Tests may still replace the **external Agnes transport**.

But the acceptance path must not replace the production Character generator itself with:

```text
_fake_view_generator
```

because that bypasses exactly the code we are trying to prove.

For deterministic automated testing, prefer:

```text
real public endpoint
real execution service
real production handler
real _real_character_view_generator
real Character workflow
MockTransport / FakeAgnes only at the external API boundary
```

This is materially stronger than:

```text
fake Character generator
→ fake success
```

---

# 12. Character Sync Must Reuse Existing Workflow

Do not implement another Character-generation system inside AI-7C-S.

Reuse the existing Character generation service/workflow already used by the Character page.

The AI-7C path should orchestrate it.

It should not duplicate:

```text
prompt construction
segmentation rules
view persistence
Agnes request formatting
project isolation
```

unless the current repository proves a minimal adapter is necessary.

---

# 13. Character Sync Concurrency / Idempotency

Verify M3 before choosing the exact lock.

The goal is:

```text
same execution
same Character
same required visual refresh

→ at most one logical synchronization
```

across:

```text
duplicate resume

two execution workers

backend restart

retry after persistence ambiguity
```

Do not add one global lock around all Character generation.

Prefer the narrowest existing concurrency primitive consistent with the production architecture.

Potential scope may be:

```text
project + execution

or

project + character
```

but choose from the real call graph.

The durable completion record, not the in-memory lock alone, must provide restart idempotency.

---

# 14. Checkpoint S3 — ProductionExecutionManifest Correctness

Now address:

```text
M2
L4
related recovery findings
```

Do not redesign the manifest unless necessary.

## Manifest rule

The manifest is an execution artifact describing deterministic production consequences.

It must not silently become a second creative truth source.

Verify `update_scene` semantics against real approved operations.

For every action type answer:

```text
what creative change caused this action?

which Scene/Shot/Character does it target?

what existing asset becomes stale?

what must be regenerated?

what remains valid?
```

A bounded Scene change must not accidentally become:

```text
regenerate whole project
```

and must not accidentally omit dependent Shots.

---

# 15. Manifest Crash Recovery

Test the actual persisted recovery path.

Important cases include:

```text
creative commit succeeded
manifest not fully persisted

manifest persisted
stage marker not advanced

restart while character sync active

restart before planner creation

restart after planner creation
```

On resume the system must choose among:

```text
reuse proven durable state

reconcile state

recompute deterministic manifest

block if truth is ambiguous
```

Do not blindly trust stale in-memory data.

Do not blindly repeat external generation.

If deterministic manifest recomputation is already possible from:

```text
approved operations
+
committed canonical project
+
execution state
```

prefer that over introducing another fragile truth source.

---

# 16. Remaining Audit Findings

After S1–S3 are stable, re-evaluate:

```text
M4
L2
L3
L5
and any other audit notes
```

Repair them only if:

```text
the finding is still valid
AND
it belongs to AI-7C stabilization
```

Do not allow low-priority cleanup to trigger unrelated architecture changes.

No AI-8.

No visual redesign.

No broad refactor.

---

# 17. Revised Test Evidence Model

From now on, do not report simply:

```text
PASS
```

without saying what kind of evidence it is.

Every important Gate should be classified as one of:

```text
A. Deterministic unit / protocol test

B. Integrated production-code-path test
   external Agnes mocked only at network boundary

C. Browser E2E
   actual frontend lifecycle

D. Live Agnes acceptance
   real network and real Agnes result

E. Manual independent review
```

The completion report must explain what each layer proves and what it does **not** prove.

Example:

```text
Character sync integration:
PASS

Proof level:
B

Real production handler used:
YES

_real_character_view_generator used:
YES

Agnes network:
MOCKED

Therefore this proves:
runtime wiring and production code path

It does NOT prove:
current live Agnes compatibility
```

This distinction is mandatory.

---

# 18. Mandatory Live Character-Sync Acceptance

The previous AI-7C live acceptance is not sufficient if Character synchronization was deferred or replaced by a fake generator.

Run one new minimal live acceptance specifically for Character sync.

Use a disposable test project.

Keep the scope intentionally small.

Recommended scenario:

```text
one Character

one appearance-only approved change

one affected Scene / minimal Shot scope
```

Path must begin from the normal AI-7C execution entry, not a direct Character helper.

Prove:

```text
approved appearance change
→ execute affected_production
→ creative commit
→ production manifest says Character refresh required
→ real Character-sync handler runs
→ real existing Character workflow runs
→ real Agnes image request succeeds
→ new current Character view(s) persisted
→ execution checkpoint persists
→ downstream planning sees synchronized Character truth
```

Do not inject `_fake_view_generator`.

Record:

```text
actual request path

actual resulting asset

project.json state

execution state

handler/job lineage
```

Do not make unnecessary large live calls.

If a real video is required to prove the final public path, use only one minimal Shot/version.

---

# 19. Browser Acceptance Is Now a Blocking Gate

The earlier browser checklist must no longer remain an unchecked documentation section.

After fixes:

restart backend and frontend as required.

Run the AI-7C execution flow through the real frontend.

At minimum prove:

```text
approved Plan does not auto-execute

Execute works

progress survives more than one polling tick

refresh restores execution

production preparation appears

active production remains visible

stale execution gives correct action

completed execution remains truthful after refresh
```

Where browser automation is practical, use Playwright.

For final acceptance, do not replace the entire backend with frontend route mocks.

The browser should talk to the actual local FastAPI application for the final path.

---

# 20. Fresh Independent Final Reviewer

After implementation and tests are complete, spawn a **fresh read-only reviewer** that did not implement the fixes.

Give it:

```text
the original audit findings

the changed diff

the new tests

the final execution path
```

Ask it specifically to search for:

```text
a fix that only makes the test pass

dead/unreachable branches

status lifecycle contradictions

fake generator leakage into real production

unregistered handlers

direct helper tests that bypass the public endpoint

resume paths that work only before refresh

duplicate external work after restart

manifest state that cannot be reconstructed
```

The reviewer must return:

```text
CLEAR
```

or explicit blockers.

Do not allow the implementing main agent to self-certify every HIGH finding.

---

# 21. Regression Discipline

For each confirmed bug:

first create or identify a reproduction.

Then repair.

Then retain a regression test matching the failure mode.

Examples:

```text
H1
→ execution must visibly progress past first poll

H2
→ active execution must survive F5

H3
→ production Character handler and real generator path must be reached

M1
→ public affected_production execution must reach required downstream stages

M5
→ stale blocked UI must not offer Resume
```

Tests should encode **outcomes**, not merely the exact implementation chosen today.

---

# 22. What Not To Do

Do not:

```text
rerun or rewrite all of AI-7C

begin AI-8

add a Reviewer Agent to the product

replace current execution architecture

create a new production runtime

create another realtime bus

introduce generic rebase

cascade-delete creative data

make all existing pytest tests live-network tests

rewrite Character generation

turn this stabilization round into frontend redesign

have multiple write-capable agents edit the same execution files concurrently

claim live Character acceptance from a fake generator test

claim browser acceptance from unchecked boxes
```

---

# 23. Suggested Repair Order

Use this dependency order:

```text
S0
independent read-only audit + issue ledger

↓

S1
H1 + H2
frontend execution lifecycle

then
M5 + L6/F5
frontend blocker/eligibility behavior

↓

S2
M1/F4
public affected-production wiring

H3/L1/M3
Character-sync registration, real path and concurrency

↓

S3
M2
manifest update_scene correctness

L4
manifest crash recovery/recomputation

↓

S4
remaining validated M/L findings

↓

S5
public-path integration Gate

browser Gate

live Character-sync Gate

full regression

fresh independent reviewer
```

Do not parallelize checkpoints that modify the same execution lifecycle.

---

# 24. Checkpoint Commit Guidance

Keep stabilization commits independently inspectable.

Suggested shape:

```text
fix(ui): separate change session and execution lifecycle recovery

fix(ui): honor stale execution blockers and eligibility

fix(agent): wire affected production stages through public execution path

fix(production): register and harden character synchronization runtime

fix(production): correct manifest impact and restart reconciliation

test(ai-7c): add browser, real-path and live character-sync closure gates
```

Adapt to repository convention.

Do not force this exact number of commits if the real dependency graph suggests fewer.

---

# 25. Final Completion Report

When this stabilization round is complete, report:

```text
AI-7C-S — Post-Audit Stabilization

1. Baseline
   git HEAD before stabilization

2. Independent audit agents
   Agent A findings
   Agent B findings
   Agent C findings
   Agent D findings

3. Deduplicated issue ledger
   confirmed
   rejected
   merged root causes

4. H1
   root cause
   repair
   browser proof

5. H2
   root cause
   repair
   refresh proof

6. H3
   production handler path
   real generator path
   live Agnes proof

7. M1/F4
   previous missing wiring
   final public execution path

8. M2
   manifest correction

9. M3
   concurrency/idempotency result

10. M5
    stale-blocked UX

11. L1/L4/L6
    disposition and evidence

12. Remaining M/L findings
    fixed / rejected / deferred with reason

13. Session vs Execution lifecycle model
    final frontend semantics

14. Public endpoint trace
    execute
    → sync
    → planner
    → production

15. Handler registration proof

16. Manifest restart recovery proof

17. Automated backend tests
    old baseline
    new result
    regressions

18. Frontend typecheck

19. Vite build

20. Playwright/browser tests
    exact scenarios

21. Real local-browser acceptance

22. Character-sync integrated real-code-path Gate
    Agnes mocked at network boundary only

23. Live Character-sync acceptance
    actual real Agnes call
    generated asset
    persisted result

24. Full regression

25. Fresh independent reviewer
    CLEAR / blockers

26. Explicit proof:
    execution no longer freezes after first poll
    refresh restores active execution
    approved-but-not-started Plan restores
    stale-blocked does not offer Resume
    public affected_production reaches required stages
    Character sync uses registered production handler
    real Character generator path has actually run
    manifest recovery does not duplicate work
    no unrelated production state is lost
    no whole-project regeneration for bounded change

27. Known limitations

28. AI-7C-S acceptance:
    PASS / FAIL

29. STOP
```

If any HIGH item lacks the required proof layer, report:

```text
AI-7C-S FAIL / INCOMPLETE
```

Do not compensate with a larger pytest count.

---

# 26. Final Stop Condition

This stabilization round is complete only when all three statements are true:

```text
1.
AI-7C core execution remains correct.

2.
The actual frontend/public runtime path is connected and recoverable.

3.
The previously unproven real Character synchronization path has genuinely run.
```

Then STOP.

Do not begin AI-8.
