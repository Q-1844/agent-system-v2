# AI-7C-H Final Closure — Trae Execution Document

## Purpose

This is a **very narrow closure hotfix** for AI-7C-H.

The previous cancel/restart blocker has already been repaired and validated for the main running-production path. The remaining known hole is:

> An **owned `ProductionRun` already in `paused` state** can receive `cancel_requested`, but no active loop may remain to advance it to `cancelled`.  
> That can leave the parent `ChangeExecution` stuck in “stopping” forever and prevent the replacement ChangeSession from taking over.

This task exists only to close that state hole, preserve the already-correct cancel semantics, update the hotfix report, commit the change, and STOP.

Do **not** reopen AI-7C-H broadly.  
Do **not** start AI-7D.

---

# 1. Desired user-level invariant

The product promise is:

```text
User chooses:
“Stop current task and re-plan”
        ↓
old ChangeExecution enters stopping
        ↓
its exact owned ProductionRun is cancelled
        ↓
old ChangeExecution converges to cancelled
        ↓
old plan can never resume or create new production work
        ↓
replacement ChangeSession may start normally
```

This must also be true when the owned ProductionRun is already:

```text
paused
```

A paused run must not remain forever as:

```text
paused + cancel_requested
```

with no worker capable of finishing the transition.

---

# 2. Scope

Implement only two closure guarantees:

## A. Normal cancellation path

When `ProjectProductionManager.request_cancel(...)` is asked to cancel a run that is already `paused`:

```text
paused
→ cancelled
```

The transition should be immediate/deterministic because a paused run has no active production loop that needs to cooperatively wind down.

The cancellation must:

- preserve all already-generated assets;
- preserve historical records;
- not delete files;
- not create a new ProductionRun;
- not create new jobs;
- not resume the run;
- not touch another project's run;
- not touch a run not owned by this ChangeExecution.

## B. Restart reconciliation

During production-run recovery/startup reconciliation, if durable state contains:

```text
status = paused
cancel_requested = true
```

then recovery must converge it to:

```text
status = cancelled
```

This protects against:

- an older persisted state;
- crash between request-cancel and terminal-state persistence;
- any future code path that can persist the paused/cancel-requested combination.

After the owned run is terminal, the existing AI-7C-H cancel convergence logic must allow its parent `ChangeExecution` to finish as `cancelled`.

---

# 3. Before changing code

First inspect the current repository state.

Confirm and report:

- current branch;
- current HEAD;
- working tree status;
- whether user WIP is present;
- commits belonging to the AI-7C-H cancel hotfix;
- current implementation of:
  - `ProjectProductionManager.request_cancel`
  - ProductionRun persistence
  - ProductionRun startup/recovery reconciliation
  - ChangeExecution cancellation convergence
  - `production_run_id` ownership/association
  - current guarded mutation helper used by the hotfix.

If repository paths differ from names in this document, follow the real code.

Do not overwrite or commit unrelated user WIP.

If `AI-7C-H_CANCEL_HOTFIX_REPORT.md` exists in the repo, read it to understand the current declared limitation. Do not trust the report over code; code and durable behavior are authoritative.

---

# 4. Important existing semantics that must remain intact

Do not redesign the cancellation model.

The previous hotfix already established these invariants and they must remain:

1. `cancel_requested` is a real control signal.
2. `cancelled` is terminal and cannot be resurrected by stale in-memory state.
3. execution state mutation is guarded/locked so stale objects cannot overwrite a newer terminal state.
4. post-commit loops stop launching new work after cancellation is observed.
5. the exact owned `ProductionRun` is identified through the persisted execution → run association.
6. run-start and cancellation have a protected critical section so an orphan run cannot be created after cancellation.
7. the UI distinguishes:
   - stopping (`running + cancel_requested`)
   - fully cancelled.
8. the restart/re-plan flow waits for authoritative backend cancellation before starting the replacement ChangeSession.
9. already-generated creative/production assets are retained.
10. no new workflow framework is introduced.

This task must **extend** these semantics to `paused`; it must not replace them.

---

# 5. Implementation — normal path

Locate the real `ProjectProductionManager.request_cancel` or equivalent business method.

Today the relevant behavior is approximately:

```text
request_cancel(run)
→ set cancel_requested
→ active run loop eventually observes it
→ run becomes cancelled
```

That works for a running run because a loop still exists.

For `paused`, add a direct terminal transition.

Conceptually:

```python
if run.status == "paused":
    run.cancel_requested = True
    run.status = "cancelled"
    persist_atomically(run)
    return run
```

The exact code must follow current manager/store/locking patterns.

### Required properties

- perform the status mutation under the same synchronization discipline used by current ProductionRun writes;
- reload/validate current durable state if current manager conventions require it;
- remain idempotent:
  - cancelled → cancel again = still cancelled;
  - paused → cancel = cancelled;
  - repeated calls must not create side effects;
- do not broaden this direct-terminal rule to `running` unless current design already does so;
- do not fake-cancel an actively executing external operation;
- do not delete or null out generated assets;
- do not clear errors/history unrelated to cancellation;
- preserve run ownership and project identity.

### State expectations

At minimum:

```text
paused
 + request_cancel
→ cancelled
```

and:

```text
cancelled
 + request_cancel
→ cancelled
```

Running semantics should remain cooperative:

```text
running
 + request_cancel
→ cancel_requested
→ current indivisible unit may finish
→ no new work
→ cancelled
```

Do not accidentally turn all cancellation into immediate forced termination.

---

# 6. Implementation — restart/recovery reconciliation

Locate the real ProductionRun recovery path, for example:

- `recover_runs`
- manager startup recovery
- persisted run reconciliation
- equivalent function in the current repository.

Add a deterministic reconciliation rule:

```text
paused + cancel_requested
→ cancelled
```

This should happen before the run can be treated as resumable/active.

### Required behavior

On backend restart:

```text
persisted:
R1.status = paused
R1.cancel_requested = true

startup/recovery:
R1.status = cancelled
persist

parent E1:
owned production no longer active
→ existing execution cancellation convergence
→ E1.status = cancelled
```

Do not:

- resume R1;
- enqueue new work for R1;
- create a replacement R1;
- cancel another project's active run;
- use “find whichever run is active” heuristics when an exact `production_run_id` is available.

If the current recovery code has an explicit set of active/recoverable statuses, update it carefully so `paused + cancel_requested` is reconciled **before** normal paused handling.

---

# 7. Parent ChangeExecution convergence

Do not add a second cancellation implementation in `ChangeExecution`.

After the owned run reaches `cancelled`, reuse the existing AI-7C-H hotfix convergence rules.

Verify the following chain from real code:

```text
E1.cancel_requested = true
E1.production_run_id = R1

R1 = cancelled
        ↓
post-commit/recovery convergence
        ↓
E1 = cancelled
```

If the existing convergence code already does this, do not change it.

Only make a parent-side adjustment if the paused fix proves that the current convergence path cannot observe the newly terminal run.

Any such adjustment must remain minimal and use the existing guarded execution mutation helper.

---

# 8. Replacement ChangeSession behavior

The previous hotfix already changed “restart” from:

```text
cancelExecution()
+ immediately create replacement session
```

to:

```text
request cancellation
→ show stopping
→ wait for backend terminal truth
→ create replacement session
```

Do not redesign this.

For the paused case, verify that the same path now completes:

```text
R1 paused
→ user chooses stop and re-plan
→ R1 cancelled
→ E1 cancelled
→ replacement S2 starts
→ S2 is not blocked by PROJECT_PRODUCTION_ACTIVE
```

Do not shorten the existing wait window as part of this task.

Do not add another timeout system.

---

# 9. Deterministic tests — required

Do not build a large new test suite. Add the smallest tests that prove this exact closure.

## Case 1 — paused owned run cancels normally

Create durable state equivalent to:

```text
ChangeExecution E1:
status = running
cancel_requested = false
production_run_id = R1

ProductionRun R1:
status = paused
cancel_requested = false
owned by same project/execution
```

Trigger the real cancel path used by the product.

Prove:

```text
R1.status == cancelled
R1.cancel_requested == true   # if this flag remains part of current semantics
E1 eventually == cancelled
```

Also prove:

- no new ProductionRun;
- no new JobQueue job;
- no generated asset deletion;
- no `PROJECT_PRODUCTION_ACTIVE` blocker remains for a replacement session.

If replacement-session creation is easy to exercise deterministically, include it. Otherwise prove the active-production gate no longer sees R1 as active.

## Case 2 — restart reconciliation

Persist:

```text
R1.status = paused
R1.cancel_requested = true
```

with E1 pointing to R1 and cancellation already requested.

Simulate real backend recovery/startup.

Prove:

```text
R1 → cancelled
E1 → cancelled
```

and:

```text
0 new jobs
0 new production runs
```

No external Agnes call is required.

## Case 3 — idempotency

Recommended if very small:

```text
paused R1
→ cancel
→ cancel again
→ restart/recover
```

Result remains one terminal R1, one terminal E1, no duplicate side effects.

This can be folded into Case 1 or Case 2.

---

# 10. Regression tests — required but narrow

Re-run the existing cancellation/race coverage created by the prior hotfix, especially the tests that attack:

- stale execution resurrection;
- cancel during character sync;
- cancel during production planning;
- cancel vs run-start race;
- cancellation after ProductionRun already exists;
- cancellation during backend restart;
- resume while stopping/cancelled;
- exact owned-run propagation.

Do not repeat an expensive live Agnes Golden Path for this closure unless deterministic tests expose a suspicious production-path discrepancy.

Also run the smallest relevant AI-7C / AI-7C-H regression set needed to prove no lifecycle regression.

For frontend:

- typecheck/build only if touched code can affect frontend or if current workflow expects the standard hotfix gate;
- classify pre-existing failures separately;
- do not “fix” unrelated pre-existing errors.

---

# 11. Optional browser smoke

A real browser/live Agnes rerun is **not required** for this final closure.

If a cheap local browser path already exists to seed a paused ProductionRun, one small smoke is useful:

```text
paused owned run
→ click “stop current task and re-plan”
→ “stopping”
→ “cancelled”
→ replacement ChangeSession appears
```

But do not spend Agnes quota merely to recreate a paused run.

Durable deterministic proof is sufficient here.

---

# 12. Forbidden changes

Do not touch unrelated limitations, including:

- the existing 240s restart wait policy;
- historical jobs.json growth;
- video-binary environment issues;
- F5 project identity behavior unless it is directly broken by this patch;
- update_shot atomicity;
- blocked-session “alive” semantics;
- Floating Bar layout;
- Agent prompt redesign;
- character generation logic;
- planner logic;
- manifest logic;
- Workspaces;
- AI-7D;
- reviewer agents;
- new workflow/orchestration frameworks.

Do not introduce:

- Temporal;
- Celery;
- a new state machine framework;
- a new cancellation abstraction parallel to existing manager logic;
- broad ProductionRun refactors.

---

# 13. Report update

Update the existing:

```text
AI-7C-H_CANCEL_HOTFIX_REPORT.md
```

or, if repository conventions require a new closure note, create:

```text
AI-7C-H_CANCEL_FINAL_CLOSURE.md
```

Prefer updating the existing report if it is clearly the canonical hotfix report.

The report must state:

1. baseline HEAD;
2. files changed;
3. exact root cause of paused cancellation non-convergence;
4. normal-path fix;
5. restart-reconciliation fix;
6. why active/running cancellation semantics remain cooperative;
7. why already-generated assets are retained;
8. parent ChangeExecution convergence result;
9. replacement ChangeSession result;
10. deterministic tests and actual results;
11. original cancel-race regression results;
12. build/typecheck result if run;
13. remaining known limitations — excluding paused cancellation if fixed;
14. final decision:
   - `AI-7C-H cancellation closure PASS`
   - or `BLOCKED`.

Do not claim a live Agnes proof if one was not run.

---

# 14. Git discipline

Keep this as one narrow logical closure.

Before commit:

```text
git diff
git status
```

Confirm no unrelated user WIP is included.

Suggested commit meaning:

```text
fix paused production cancellation convergence
```

Use the repository's existing commit convention if different.

Do not rewrite history.

---

# 15. PASS gates

This closure is PASS only if all are true:

```text
[ ] paused owned ProductionRun cancels to terminal cancelled
[ ] repeated cancel is idempotent
[ ] paused + cancel_requested is reconciled to cancelled after restart
[ ] parent ChangeExecution converges to cancelled
[ ] cancelled Execution cannot resurrect
[ ] no new Character work starts after cancellation
[ ] no new planner scene starts after cancellation
[ ] no new ProductionRun is created after cancellation
[ ] no unrelated ProductionRun is cancelled
[ ] no generated assets are deleted
[ ] replacement ChangeSession is no longer blocked by abandoned R1
[ ] original running-run cancellation semantics still pass
[ ] original cancel/run-start race tests still pass
[ ] no AI-7C lifecycle regression introduced
```

---

# 16. STOP

When the above gates pass:

```text
STOP.
```

Do not begin AI-7D.

Do not reopen unrelated AI-7C-H issues.

Return the updated report, commit hash, and concise execution summary for independent Claude Code verification.
