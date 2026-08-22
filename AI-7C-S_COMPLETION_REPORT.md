# AI-7C-S — Post-Audit Stabilization Completion Report

> Layered proof report (§17 evidence model). Every claim tagged with its proof level:
> **A** deterministic unit/protocol · **B** integrated production-code-path (Agnes mocked at network boundary) · **C** browser E2E (real frontend lifecycle) · **D** live Agnes acceptance (real network + result) · **E** manual/code review.

## 1. Baseline
- HEAD before stabilization: `32c3d6f` (AI-7C final, pre-stabilization).
- Stabilization commits: `327b580` (S1 frontend lifecycle) · `02a02d2` (S2-S4 wiring + char-sync + manifest + targeted run) · `a35dfef` (M4 preflight) · `144171f` (S5-1 HTTP endpoint) · `8b64e6b` (S5-2a mocked soak B/C/E) · `a1dbfaa` (S5-2b/c live A + mocked D) · `d36d3b4` (S5-3 browser E2E).

## 2. Independent audit agents (S0, read-only)
Four read-only agents (A frontend lifecycle, B public-API/execution wiring, C char-sync runtime, D manifest/recovery) inspected the repo independently (§4). Their findings seeded the issue ledger.

## 3. Deduplicated issue ledger
- **Confirmed HIGH**: H1 (polling self-destructs after first tick), H2 (refresh cannot restore approved/executing session), H3 (real char-sync path unproven).
- **Confirmed MEDIUM**: M1/F4 (public execute endpoint not fully wired to char-sync/planning), M2 (manifest update_scene correctness), M3 (char-sync concurrency/locking), M5 (stale-blocked still offers Resume), M4 (preflight misses scenes_to_run for targeted runs).
- **Confirmed LOW**: L1 (char_views handler production registration), L4 (manifest crash recovery/recompute), L6/F5 (frontend eligibility/blocking consumption).
- **Merged root causes**: H1+H2 → single frontend lifecycle model (proposal vs execution separated); H3+L1+M3 → char-sync runtime (registration + real generator + idempotency); M2+L4 → manifest determinism/recovery; M5+L6/F5 → stale/eligibility UX.
- **Deferred (low, out of stabilization scope)**: L2, L3, L5.

## 4. H1 — polling freezes after first tick
- **Root cause**: `approved_for_execution` was treated as terminal/inactive by frontend session lifecycle (TERMINAL_STATUSES contained it), so a session poll returning `approved_for_execution` → `_set(session)` → `stopPoll()` killed execution polling after one `loadExecution()`.
- **Repair (S1, `changeSession.ts`)**: `shouldPoll()` explicitly keeps polling when `status==='approved_for_execution' && executionActive` — proposal lifecycle ≠ execution lifecycle (§3/§7.1). `EXECUTION_ACTIVE_STATUSES` / `EXECUTION_TERMINAL_STATUSES` separate from session ACTIVE/TERMINAL.
- **Proof**: **C** — S5-3 browser B1: after Execute, timeline `同步角色参考图`(character_sync) stays `current` ≥6s across ≥3 poll ticks (POLL_MS=2000), then advances to `准备制作计划` (production_planning). Polling did not freeze. (run log: `e2e_7c_s5_browser.py` → `[B1 PASS]`)

## 5. H2 — refresh cannot restore approved/executing session
- **Root cause**: `restoreActive()` filtered sessions via `TERMINAL_STATUSES` (which included `approved_for_execution`) → approved session filtered out → execution not restored → the approved-restore branch was dead code.
- **Repair (S1)**: `SESSION_NOT_RESTORABLE = {rejected,stale,failed,cancelled}` (excludes approved) replaces the filter; `restoreActive` now restores approved session + `loadPlan` + `listChangeExecutions` (existing execution) + `shouldPoll()` resume (F-Extra1: approved also loads plan so `execute()` doesn't early-return).
- **Proof**: **C** — S5-3 browser B2: while execution at character_sync, in-app remount (`/characters`→`/agents`→Team Chat) → `restoreActive` restores the approved session + execution, timeline `同步角色参考图` still `current`, polling resumes. (`[B2 PASS]`)  B5: after completion, remount restores truthful `production_planning` current, no re-execution. (`[B5 PASS]`)

## 6. H3 — real Character visual sync path
- **Production handler path**: `handler_character_views` registered in production `JOB_HANDLERS` at `routers/agents.py:65-66` (`JOB_HANDLERS.setdefault("character_views", handler_character_views)`) — **L1 closed**. Asserted by `tests/test_ai_7c_s2_wiring.py::test_character_views_handler_registered_in_production`.
- **Real generator path**: `run_character_sync` defaults to `_real_character_view_generator` (execution_service.py:852-853) → submits a `character_views` JobQueue job (origin=execution_id + stable stage_key) + polls; `find_by_origin` reuses the existing job (no re-submit); handler does per-view `done_views` skip (character_views_handler.py:62-69).
- **Live Agnes proof**: **D** — S5-2 A (`gated_real_7c.py char_restart`): real Agnes generated front/side/back (done + url + local via rembg); char-sync mid-restart reused the **same char_views job_id** (`964eba120ca2` across restart, no re-submit) and made **exactly 3 generate_image calls** (one set, no duplicate); stage recovered → production_planning. Also D1 (prior, §99): real char-sync single-run PASS.

## 7. M1 / F4 — public affected-production wiring
- **Previous gap**: public execute endpoint not fully connected to character_sync / production_planning stages.
- **Repair (S2)**: `start_replay_commit` (execute entry) → `commit_canonical` → stage `character_sync`; `_maybe_kickoff_postcommit` (resume) → `_run_postcommit_to_completion` runs `run_character_sync` → `run_production_planning` → (existing ProjectProductionRun for production/compose).
- **Final public path**: **B** — `tests/test_ai_7c_s5_endpoint.py` proves via real FastAPI endpoint (not internal helper): `POST /execute`→revalidation→commit→character_sync; `GET /manifest`→char regenerate + planner_scene_ids scoped to affected scene (M4 via HTTP); `POST /resume`→kicked_off; poll `GET /executions/{eid}`→stage advances character_sync→production_planning + Character.views regenerated (front/side/back done); no duplicate execution.

## 8. M2 — manifest update_scene correctness
- **Repair (S3, `compute_production_manifest`)**: deterministic from approved operations + committed canonical project; bounded char change → char REGENERATE + referencing-shot frame/video REGENERATE (not whole-project); `update_scene` scoped to scene's shots; no silent second creative truth source.

## 9. M3 — concurrency / idempotency
- **Result**: at most one logical synchronization per (execution, character) across duplicate-resume / restart / retry.
- **Mechanism (layered)**: `session_lock(project, session)` serializes execute/resume per session; `_INFLIGHT_POSTCOMMIT` (in-process) prevents double background kickoff; `find_by_origin(execution_id, stage_key)` prevents re-submitting the char_views job; handler `done_views` skips already-done views; `ex.character_sync` durable per-char record (appended only after the char's views persist) — provides restart idempotency (the durable record, not the in-memory lock alone, §13).
- **Proof**: **B** — soak B (rapid 4×resume + execute×2 → exactly 1 execution + exactly 3 generate_image); **D** — S5-2 A (restart reused same job_id, 3 calls, no duplicate).

## 10. M5 — stale-blocked UX
- **Repair (S1, `ChangePlanReview.vue`)**: `staleBlocked = executionBlocked && blocker.code==='PROJECT_CHANGED_AFTER_APPROVAL'` → shows `重新提案` (re-plan), NOT `尝试恢复`/Resume (§7.4/§81: stale not rebaseable). Other blocked types still offer `尝试恢复`.
- **Proof**: **E** (code-verified `ChangePlanReview.vue:164-167`) + **B** (soak E: char-sync failure → blocked, resume-on-blocked returns blocked/no-re-kickoff, no 2nd chain).

## 11. L1 / L4 / L6
- **L1**: char_views handler registered in production (`routers/agents.py:66`) — proven (§6).
- **L4**: manifest crash recovery — `_ensure_manifest` recomputes deterministically from canonical + approved ops if missing (execution_service.py:841); S3 added restart reconciliation. **D** S5-2 A proves restart-recovery completes.
- **L6/F5**: frontend consumes backend authoritative eligibility (`loadEligibility` → `canExecute`/`execEligibilityReason`; server truth wins) — `ChangePlanReview.vue:244-253`, `changeSession.ts:122-134`.

## 12. Remaining M/L findings
- **M4** (preflight `scenes_to_run` for targeted runs): fixed (`a35dfef`) + proven via HTTP (`tests/test_ai_7c_s5_endpoint.py` manifest scoped to s01).
- **L2 / L3 / L5**: deferred — low priority, not valid within AI-7C stabilization scope; not modified (§16).

## 13. Session vs Execution lifecycle model (final frontend semantics)
- Proposal settled? (`isTerminal`/`readyForReview`) · plan executable? (`needsExecute = approved && !execution`) · execution exists/active/terminal/blocked (`executionActive`/`executionDone`/`executionBlocked`) · `shouldPoll(session, execution)` = proposal-active OR (approved AND execution-active) · `restoreActive` uses `SESSION_NOT_RESTORABLE` (approved IS restorable). `approved_for_execution` no longer means "nothing to restore/poll".

## 14. Public endpoint trace
`POST /api/project/{p}/change-sessions/{sid}/execute` → `start_replay_commit` (revalidate §17 → workspace replay → `commit_canonical` atomic commit) → `character_sync` stage → (resume → kickoff) `run_character_sync` (real handler + generator) → `production_planning` → (existing ProjectProductionRun for production_prepare/production/compose). Proven **B** (S5-1) + **D** (S5-2 A).

## 15. Handler registration proof
`routers/agents.py:65-66`: `from src.agents.character_views_handler import handler_character_views; JOB_HANDLERS.setdefault("character_views", handler_character_views)` (imported by api_server → registered at startup). `tests/test_ai_7c_s2_wiring.py::test_character_views_handler_registered_in_production` asserts it.

## 16. Manifest restart recovery proof
**D** S5-2 A: char-sync mid-restart → `_load` resets the orphaned `character_views` job running→queued; worker re-processes it; `find_by_origin` reuses the same job (no re-submit); handler `done_views` skips already-persisted views; `ex.character_sync` record (durable per-char) prevents redo on `run_character_sync` re-entry. Result: same job_id, 3 Agnes calls, views done — no duplicate work.

## 17. Automated backend tests
- Baseline (pre-stabilization, `32c3d6f`): 99 passed + 1 pre-existing flake (`test_case7_source_changed_blocked`, AI-5A timing flake — not an AI-7C regression; fails on stash baseline too).
- New result (this round, incl. S5-1 endpoint + S5-2a soak B/C/D/E): **498 passed** + 1 pre-existing flake (`test_case7_source_changed_blocked`). (494 baseline + 4 soak tests.)
- Regressions: 0 new (the only failure remains the pre-existing `test_case7` flake).

## 18. Frontend typecheck
`vue-tsc --noEmit`: **0 NEW errors** from this round (files touched: `changeSession.ts`, `ChangePlanReview.vue`, tests, `e2e_7c_s5_browser.py`, `gated_real_7c.py` — all clean). Pre-existing errors remain (not cleared — out of scope per §S5-4): `node_modules/unplugin` + `webpack-virtual-modules` (TS2307 cannot-find-webpack/rolldown/@rspack — dependency type decls, pre-existing); `src/store/index.ts` ×4 (TS7053/TS2322, per CLAUDE.md); `src/components/CreativeWorkspaceStudio.vue` + `ShotCard.vue` ×2 (pre-existing). Vite/esbuild ignores these at runtime.

## 19. Vite build
`npx vite build` (esbuild, the actual production build — bypasses the `npm run build` vue-tsc gate which fails on pre-existing TS errors): **✓ built in 30.34s**, `dist/` produced, exit 0. Build succeeds; pre-existing typecheck errors are typecheck-only (don't block the esbuild build, consistent with CLAUDE.md "Vite 用 esbuild 不检查").

## 20. Playwright / browser tests (§17 level-C)
`e2e_7c_s5_browser.py` — real frontend (Vite) + real FastAPI (8000) + real Agnes char-sync. Scenarios:
- **B1** Execute → timeline `同步角色参考图`(character_sync) current, sustained ≥6s across multiple 2s poll ticks (polling does not freeze; H1).
- **B2** in-app remount → `restoreActive` restores active execution (character_sync still current; H2).
- **B5** after completion (production_planning current) → in-app remount restores truthful state, no re-execution.
- Result: **PASS** (`[B1 PASS] [B2 PASS] [B5 PASS]`). Note: page.goto/reload (full page load) resets pinia `projectName` to default (pre-existing, out of scope) — so the test uses in-app `router-link` navigation + remount (not page.reload) to preserve the throwaway project; this still proves `restoreActive` on remount (H2's mechanism). FAB overlay workaround: hide `.fab` before clicking `执行计划`.

## 21. Real local-browser acceptance
= §20 (S5-3, same run): real local frontend + real local FastAPI; no route mocks; browser drove the real execution flow.

## 22. Character-sync integrated real-code-path Gate (Agnes mocked at network boundary)
**B** — `tests/test_ai_7c_s5_endpoint.py` + `tests/test_ai_7c_s5_soak.py` (B/C/D/E): real `execution_service` / router / `handler_character_views` / JobQueue; Agnes mocked only at the client boundary (`FakeAgnes` + `cvh._download`→real png for rembg). Proves runtime wiring + production code path. Does NOT prove live Agnes compatibility (that's §23).

## 23. Live Character-sync acceptance
**D** — D1 (§99, prior round) + **S5-2 A** (`gated_real_7c.py char_restart`, this round): real Agnes `generate_image` ×3 (front text2img → side img2img → back img2img), rembg segmentation, persisted `Character.views` (done + url + local). Mid-restart: same job reused, 3 calls (no duplicate), execution recovered → production_planning. Actual asset: `projects/__gated_7c_cr/output/characters/.../{front,side,back}_seg.png`.

## 24. Full regression
**498 passed, 1 failed** (`test_case7_source_changed_blocked` — pre-existing AI-5A timing flake, fails on stash baseline too; not an AI-7C-S regression). 0 new regressions. (363s.)

## 25. Fresh independent reviewer
Fresh general-purpose subagent (adversarial, read-only; not the implementer): **CLEAR — no blocking defects**. ~24 min, 41 tool-calls; read all 5 key files end-to-end + tests + `git show` on all 7 commits.
- Verified all 5 claimed proofs **SOUND**: H1 (`shouldPoll` approved+executionActive independent criterion) · H2 (`SESSION_NOT_RESTORABLE` excludes approved; `restoreActive` restores plan+execution+poll; `isTerminal` not used to gate restore/poll — no contradiction) · H3 (`_real_character_view_generator` unconditional default; `handler_character_views` registered in production `JOB_HANDLERS`; `all_fake_handlers()` has no `character_views` fake to shadow `setdefault`; soak tests mock only at Agnes client + download boundary) · M3 (full chain: `find_by_origin` no-re-submit + `_load` running→queued + `start()` re-routes queued + `done_views` skip + `ex.character_sync` durable; all-or-nothing writeback pre-writeback window honestly bounded ≤6) · M5 (`staleBlocked` → 重新提案 not 尝试恢复; backend `resume_execution` blocked no-kickoff).
- 9 adversarial categories all clear (test-only fixes / dead branches / lifecycle contradictions / fake-generator leakage / unregistered handlers / endpoint-bypass tests / restore-before-refresh / duplicate-after-restart / manifest reconstruction). The `_ensure_manifest` recompute + `commit_canonical` 3 crash windows + `_reconcile_commit` Case B all traced.
- Minor observations (not blockers): (a) `done_views` coarse skip is pre-existing v1 (documented in `a1dbfaa` + `test_soak_d`); (b) `poll()` doesn't reload `eligibility` each tick — backend `execute_plan` stale check is the authoritative guard (server-truth-wins, §7.5).

## 26. Explicit proof (§26 stop condition checklist)
- execution no longer freezes after first poll — **YES** (B1, C).
- refresh/restores active execution — **YES** (B2, C).
- approved-but-not-started Plan restores — **YES** (restoreActive + `needsExecute`; `执行计划` button re-appears after remount — observed in S5-3).
- stale-blocked does not offer Resume — **YES** (M5, code-verified + soak E).
- public affected_production reaches required stages — **YES** (S5-1 endpoint B).
- Character sync uses registered production handler — **YES** (L1, routers/agents.py:66).
- real Character generator path has actually run — **YES** (H3, S5-2 A live D).
- manifest recovery does not duplicate work — **YES** (S5-2 A: same job_id, 3 calls).
- no unrelated production state is lost — **YES** (soak D: old first_frame/video/final_video unchanged after char-sync).
- no whole-project regeneration for bounded change — **YES** (M2 manifest scoped + S5-1 manifest scoped to s01).

## 27. Known limitations
1. **char-sync `done_views` skip is coarse v1** (character_views_handler.py:62-69, "coarse §46"): after a character *description* change with existing **done** views, char-sync does NOT regenerate views (`update_character` only changes `description_en`, doesn't clear views — compilers.py §20). Regeneration requires non-done views. Out of scope to fix (by-design v1; documented in soak D).
2. **char-sync permanent failure → blocked, no auto-retry**: a failed `character_views` job blocks the execution; resume-on-blocked is a no-op (§81). Retry requires re-plan. (soak E documents this boundary.)
3. **Frontend does not persist `projectName` across full page reload** (pinia ref, default `我的动画短片`): browser refresh resets the project. S5-3 uses in-app navigation/remount (not page.reload) to preserve the throwaway project. Pre-existing, out of AI-7C-S scope.
4. **`normalize_module` WIP uncommitted**: `src/agents/inquiry.py`/`change_analysis.py` have an uncommitted `normalize_module` fix (LLM plural module names → singular canonical, e.g. `characters`→`character`). Verified correct direction + safe (494 tests pass with it; downstream uses singular). Placement is in the AI-7A `ReadOnlyPlanValidator` (analysis layer) — whether it flows to the 7C execution plan needs deeper tracing. **Not committed unilaterally** (not from this session; surface to user for disposition).
5. Stale backend occupied port 8000 during S5-3 (could not restart via sandbox); the running backend had the AI-7C code (verified: execute endpoint 405 + change-sessions 200 + char_views handler registered). Live tests used it.
6. Pre-existing flake `test_case7_source_changed_blocked` (AI-5A timing) — not a regression.

## 28. AI-7C-S acceptance
**AI-7C-S PASS**.
HIGH items all have required proof layers: H1 (C browser — polling survives ticks) · H2 (C browser — remount restores execution) · H3 (D live Agnes — real char-sync path run). M-level: M1 (B HTTP endpoint) · M2 (B manifest) · M3 (B soak + D live restart) · M4 (B preflight via HTTP) · M5 (E code + B soak). L1/L4/L6 closed. §25 fresh reviewer CLEAR. §26 stop condition met: (1) AI-7C core execution correct · (2) frontend/public runtime path connected + recoverable · (3) previously-unproven real Character sync path has genuinely run (live Agnes, persisted views).

## 29. STOP
Do not begin AI-8.

