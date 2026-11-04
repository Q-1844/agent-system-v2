# AI-7C-H Final Closure — Claude Code Independent Verification

## Role

You are the **independent adversarial verifier**, not the implementer.

Trae has been asked to make one narrow final closure:

> Fix cancellation convergence when the exact `ProductionRun` owned by a `ChangeExecution` is already `paused`.

Your job is to try to prove that this fix is incomplete or incorrect.

Do **not** modify production code.  
Do **not** commit anything.  
Do **not** begin AI-7D.  
Do **not** re-audit all of AI-7C-H A–K.

The previous independent audit already found the main §15 cancellation blocker, and the broader hotfix already repaired the running/post-commit path. This verification is intentionally narrow.

---

# 1. What must now be true

The user-level behavior must be:

```text
E1 owns R1
R1 is paused

User chooses:
“Stop current task and re-plan”
        ↓
backend requests cancellation
        ↓
R1 becomes cancelled
        ↓
E1 becomes cancelled
        ↓
old execution cannot resume
        ↓
replacement ChangeSession may proceed
```

After backend restart, durable state:

```text
R1 = paused + cancel_requested
```

must also converge to:

```text
R1 = cancelled
E1 = cancelled
```

without new work.

---

# 2. Verification discipline

Follow this order:

1. inspect current branch / HEAD / worktree;
2. identify exactly which commit(s) implement the paused closure;
3. inspect the diff before reading the implementer's conclusions;
4. read the real manager/recovery/execution code;
5. run targeted deterministic attacks;
6. run the relevant existing cancellation/race regressions;
7. only then read the updated hotfix report and compare claims against reality.

Do not accept:

```text
“test exists”
```

as proof without understanding the production path being exercised.

Do not treat an already-generated report as authoritative.

If you find a bug, report it; do not fix it.

---

# 3. Repository hygiene

First report:

- branch;
- HEAD;
- working tree status;
- paused-closure commit(s);
- unrelated WIP, if any.

Do not alter user WIP.

Confirm the implementation scope is narrow.

If the patch contains unrelated refactors, call that out even if tests pass.

---

# 4. Attack A — normal paused cancellation

Inspect the actual `ProjectProductionManager.request_cancel` (or equivalent real business method).

Try to prove one of these failures:

- paused run only gets `cancel_requested=true` but remains paused;
- paused run requires a loop that no longer exists;
- status changes only in memory and is not persisted;
- cancellation deletes generated assets;
- cancellation mistakenly resumes work;
- cancellation mutates the wrong project/run;
- cancellation chooses “current active run” instead of exact owned run;
- repeated cancel creates duplicate side effects.

Expected durable result:

```text
before:
R1.status = paused

after real request_cancel:
R1.status = cancelled
```

If `cancel_requested` remains part of the current model, its final value should be internally consistent with existing semantics.

---

# 5. Attack B — restart reconciliation

Create/persist a state equivalent to:

```text
R1.status = paused
R1.cancel_requested = true
```

with E1 durably pointing to R1.

Exercise the real startup/recovery path, not a helper that bypasses production reconciliation.

Expected:

```text
R1 → cancelled
E1 → cancelled
```

Attack specifically for:

- recovery treating paused as resumable before checking cancellation;
- recovery leaving R1 paused forever;
- recovery creating jobs;
- recovery creating another ProductionRun;
- recovery losing the `production_run_id` relationship;
- recovery cancelling a different run;
- recovery requiring an in-memory object that disappears across restart.

Prove call/job/run counts where practical.

---

# 6. Attack C — parent execution convergence

The paused fix is insufficient if only R1 becomes cancelled while E1 remains forever:

```text
running + cancel_requested
```

Inspect the real parent convergence path.

Expected:

```text
E1.cancel_requested = true
E1.production_run_id = R1
R1.status = cancelled
        ↓
E1.status = cancelled
```

Try to prove:

- E1 never notices R1 terminal;
- E1 is marked cancelled before owned run is actually safe;
- stale in-memory E1 can still overwrite `cancelled`;
- backend restart reactivates E1;
- `resume` can revive E1;
- UI can claim “cancelled” while durable E1/R1 disagree.

The existing guarded mutation semantics must remain intact.

---

# 7. Attack D — exact ownership

This is critical.

Construct or inspect a scenario with:

```text
Project P
R1 = owned by E1
R2 = another unrelated ProductionRun
```

Cancellation of E1 must target exactly R1.

Try to prove code still uses a heuristic like:

```text
find active run for project
```

instead of the persisted owned `production_run_id`.

Expected:

```text
cancel E1
→ R1 cancelled
→ R2 unchanged
```

Do not require live Agnes for this proof.

---

# 8. Attack E — active/running semantics regression

The paused closure must not accidentally change the already-correct cooperative behavior for a running production.

A running run may have an indivisible external unit in progress. The system may need that unit to return before fully converging.

Verify the patch did **not** turn all statuses into immediate hard-cancel.

Expected running semantics remain approximately:

```text
running
→ cancel_requested
→ no new work
→ current indivisible unit may settle
→ cancelled
```

Attack for:

- immediate `status=cancelled` while a child run continues generating;
- UI terminal state before backend has actually stopped;
- lost `cancel_requested`;
- new planner/character/run work after cancellation;
- cancelled resurrection.

Run the original cancel/race tests that previously proved these semantics.

---

# 9. Attack F — cancel / run-start race regression

The prior hotfix protected:

```text
final cancel check
+ create ProductionRun
+ persist execution.production_run_id
```

with compatible synchronization.

Verify the paused patch did not weaken or bypass this.

Try the existing barrier/race tests for:

```text
cancel before run start
cancel during run-start critical section
cancel after run exists
```

Expected:

- no orphan ProductionRun;
- no run created after cancellation wins;
- if run-start wins first, the exact run becomes owned and cancellation propagates to it;
- E1 cannot resurrect.

---

# 10. Attack G — replacement ChangeSession

The product behavior is not complete until the abandoned run stops blocking the replacement request.

Exercise or reason through the real restart path:

```text
E1 / R1 paused
→ stop current task and re-plan
→ R1 cancelled
→ E1 cancelled
→ create S2
```

Expected:

- S2 can be created/advanced;
- no stale `PROJECT_PRODUCTION_ACTIVE` caused by R1;
- S2 does not begin before backend truth says E1/R1 are safe;
- no duplicate session caused by repeated frontend polling/retry;
- existing wait/fallback behavior remains unchanged.

A deterministic backend/UI-store test is acceptable. A full live Agnes run is not necessary.

---

# 11. Attack H — asset preservation

Inspect persisted run/project assets before and after paused cancellation.

Cancellation should be control-plane termination, not destructive cleanup.

Expected:

- generated images remain;
- generated videos remain;
- historical metadata remains;
- already-committed creative changes remain according to existing AI-7C-H semantics;
- only execution/run state changes.

If the patch deletes production assets to force cancellation, that is a blocker.

---

# 12. Attack I — idempotency

Test or inspect:

```text
paused R1
→ cancel
→ cancel again
→ restart
→ recover
→ resume/cancel again
```

Expected:

```text
one R1
one E1
terminal state remains terminal
0 duplicate jobs
0 duplicate runs
```

A second cancel must not reopen or alter history.

---

# 13. Tests to run

Keep this narrow.

At minimum run:

## New closure tests

- paused owned run → cancel → run cancelled → execution cancelled;
- persisted paused+cancel_requested → restart → run/execution cancelled.

## Existing hotfix regression

The most relevant pre-existing tests for:

- cancel during character sync;
- cancel during planning;
- stale execution resurrection;
- cancel/run-start race;
- existing run cancellation propagation;
- backend restart while stopping;
- resume respects cancelling/cancelled;
- exact owned-run behavior.

Also run the smallest AI-7C lifecycle/API regression set necessary to make sure the patch did not change execution semantics.

Do **not** demand a new full live Agnes Golden Path unless the code/tests reveal a production-only path discrepancy.

Do not turn environment/pre-existing failures into closure blockers unless the patch caused them.

---

# 14. Report audit

After you have formed your own technical conclusion, read:

```text
AI-7C-H_CANCEL_HOTFIX_REPORT.md
```

and/or the final closure note produced by Trae.

Check:

1. does it accurately describe the paused root cause?
2. does it accurately describe normal-path direct convergence?
3. does it accurately describe restart reconciliation?
4. are claimed tests actually the tests that exercise production code?
5. does it incorrectly claim live Agnes if none was run?
6. does it remove paused cancellation from known limitations only if truly fixed?
7. does it preserve other known limitations honestly?
8. does it claim PASS while any parent/run state can still remain permanently “stopping”?

---

# 15. BLOCK criteria

Return `BLOCKED` only for a real issue that breaks this final closure or regresses the existing hotfix.

Examples:

- paused owned run can still remain indefinitely non-terminal;
- restart can revive or fail to converge the old run;
- parent E1 remains indefinitely stopping;
- cancelled execution can resurrect;
- cancel targets the wrong ProductionRun;
- generated work continues after cancellation;
- replacement S2 remains blocked by abandoned R1;
- patch introduces an orphan ProductionRun race;
- asset deletion is used as the cancellation mechanism;
- repeated cancel/restart duplicates external work or runs;
- patch breaks the previously-correct running cancellation semantics.

Do **not** block on:

- known 240s wait policy;
- historical jobs.json growth;
- unrelated F5/project-name limitations;
- video-binary environment setup;
- update_shot atomicity;
- UI polish;
- API-key hygiene;
- AI-7D ideas;
- already-accepted unrelated limitations.

Those are outside this closure.

---

# 16. Final verdict format

Return exactly one top-level verdict:

```text
PASS
```

or:

```text
BLOCKED
```

Then provide concise evidence.

## If PASS

Include:

- commit(s) reviewed;
- exact normal paused-cancel path;
- exact restart-reconciliation path;
- parent E1 convergence;
- ownership proof;
- original race regression result;
- tests executed and actual results;
- any accepted remaining limitations;
- statement:

```text
AI-7C-H cancellation closure can be frozen.
Do not reopen without a concrete regression.
```

## If BLOCKED

Include only true blockers:

- reproduction;
- expected result;
- actual result;
- production code location;
- why it invalidates the closure;
- smallest repair boundary.

Do not fix the issue.

---

# 17. STOP

If PASS:

```text
STOP.
```

AI-7C-H is frozen.

Do not perform another A–K audit.

Do not start AI-7D in this conversation.

If BLOCKED, return the blocker to the user and stop.
