# AI-7C Final Handover (post AI-7C-S stabilization)

Stable facts an AI-7D (or any future) phase can depend on. Generated after AI-7C-S PASS.

## 1. AI-7C final user path
`ChangeSession` (polling-first durable state) → proposal (AI-7B, proposal-only, applyable≡False) → `approve` → `approved_for_execution` → `POST /execute` (production_mode = `creative_only` | `affected_production`) → `start_replay_commit`: revalidate (§17) → workspace replay → `commit_canonical` (atomic creative commit, at most once §16/§34) → stage `character_sync` (affected_production) or `done` (creative_only) → `POST /resume` or auto-kickoff → post-commit background.

## 2. ChangeSession vs ChangeExecution lifecycle
- **ChangeSession** (polling-first durable): statuses `routing/consulting/analyzing_dependencies/synthesizing_plan/validating_plan/building_preview/awaiting_user_decision` (ACTIVE) → `ready_for_review` → `approved_for_execution` (proposal settled, still restorable/executable) → `rejected/stale/failed/cancelled` (terminal).
- **ChangeExecution** (separate durable object): `pending/running/waiting_user` (ACTIVE) → `completed/failed/cancelled` (terminal) | `blocked` (with `blocker.code`; stale `PROJECT_CHANGED_AFTER_APPROVAL` not rebaseable §81).
- **Frontend predicates** (`src/store/changeSession.ts`): `shouldPoll()` = proposal-active OR (approved AND execution-active) — proposal lifecycle ≠ execution lifecycle. `restoreActive()` uses `SESSION_NOT_RESTORABLE={rejected,stale,failed,cancelled}` (approved IS restorable; restores plan + execution + resumes polling). `approved_for_execution` is NOT terminal-for-restoration.

## 3. Execute / Resume / refresh behavior
- **Execute** (`POST /execute`): idempotent — same (plan_id, plan_version) → same execution. Under `session_lock(project, session)`. Revalidation failure/stale/active-production → blocked.
- **Resume** (`POST /resume`): idempotent. pre-commit stages → sync `start_replay_commit`; post-commit (character_sync/production_planning) → background kickoff (`_maybe_kickoff_postcommit`); blocked → returns blocked (no rebase). `_INFLIGHT_POSTCOMMIT` (in-process dict) prevents double background kickoff.
- **Refresh**: `restoreActive` restores the approved session + its execution + plan; resumes polling if approved+execution-active. (Frontend `projectName` is a pinia ref, not persisted across full page reload — in-app navigation preserves it; a real browser refresh resets to default project.)

## 4. Canonical commit
`commit_canonical` (execution_service.py): atomic, at-most-once (§16). `source_snapshot_signature` (creative_source_signature) revalidated at execute vs approval; `expected_after_signature` = projected post-ops signature. COMMIT_INTENT crash reconciled at startup (`_reconcile_commit`, recover_executions §35).

## 5. Character sync / Audio sync
- **Character sync** (`run_character_sync`, affected_production): reads manifest `character_actions` (regenerate/generate_new); per **unsynced** char (durable `ex.character_sync` record) → `_real_character_view_generator` submits a `character_views` JobQueue job (origin=execution_id + stable stage_key) + polls → `handler_character_views` (registered production handler, `routers/agents.py:66`) regenerates front/side/back via real Agnes + rembg, writes `Character.views` (all-or-nothing per char). stage → `production_planning`.
- **Idempotency**: `find_by_origin` reuses the job (no re-submit); handler `done_views` skips already-done views; `ex.character_sync` durable per-char record → restart re-entry skips synced chars. `_load` resets orphaned non-video running jobs → queued on restart.
- **Audio sync**: v1 limited (no dialogue op on existing shots; insert_shot → generate_new).

## 6. affected-production planning
`run_production_planning`: reads manifest `planner_scene_ids` → per scene `plan_creator` (real `plan_production_business` + `approve_plan_business`, Agnes) → §54 all-plan gate → `run_starter` creates targeted `ProjectProductionRun` (`target_scene_ids` + `shot_actions`).

## 7. scoped ProductionRun
`ProjectProductionManager.start_run` with `target_scene_ids` + per-shot `ShotProductionActions` (keep/regenerate/generate_new/remove_from_timeline). Bounded change does NOT become whole-project regen (§39/M2). M4 preflight checks `scenes_to_run` for targeted runs.

## 8. Restart recovery
- JobQueue `_load`: non-video running jobs → queued (re-processed by worker); video running jobs stay (recover_pending_videos re-polls same video_id, no re-create).
- `recover_executions`: discovers non-terminal executions + reconciles COMMIT_INTENT; does NOT auto-advance (resume triggers).
- Char-sync mid-restart: char_views job reused (find_by_origin), worker re-processes (done_views skip), `ex.character_sync` durable → no duplicate Agnes generation. Proven live (S5-2 A).

## 9. Idempotency invariants (7D can rely on)
- Same (plan_id, plan_version) → at most one execution; canonical commit at most once.
- Same (execution, character) → at most one logical char-sync (job + handler + durable record).
- Resume/refresh/restart do NOT re-execute completed/persisted work (job reuse + done_views + durable record).
- Stale `PROJECT_CHANGED_AFTER_APPROVAL` → blocked, not auto-rebased (re-plan required).

## 10. Project isolation
Per-project `ProjectStore` (atomic write, v1→v2 migration); JobQueue jobs tagged by `project`; `session_lock(project, session)`; `onProjectChange` clears frontend session/execution. Same char name in A/B does not cross-contaminate. Proven (soak C).

## 11. Real Agnes Character path (proven)
`POST /execute` → commit → `run_character_sync` → `_real_character_view_generator` → `character_views` job → `handler_character_views` → `agnes.generate_image` (front text2img → side img2img from front → back img2img from side) → `_download` → rembg `isnet-anime` → `Character.views[v] = Asset(url, local, status=done)`. Live: S5-2 A (real Agnes, 3 views, restart-reused job, no duplicate).

## 12. Public HTTP / browser path (proven)
- HTTP (B): `tests/test_ai_7c_s5_endpoint.py` — real FastAPI endpoint, Agnes mocked at client boundary.
- Browser (C): `e2e_7c_s5_browser.py` — real frontend + real FastAPI + real Agnes; B1 polling-survives-ticks, B2 remount-restore, B5 completed-truthful.

## 13. Known limitations (7D must respect)
1. char-sync `done_views` skip is coarse v1: after a description change with **done** views, views are NOT regenerated (update_character doesn't clear views). Regeneration needs non-done views.
2. char-sync permanent failure → execution blocked, no auto-retry (re-plan required).
3. Frontend `projectName` not persisted across full page reload (in-app nav preserves it).
4. `normalize_module` WIP uncommitted in `src/agents/{inquiry,change_analysis}.py` (analysis-layer module name normalization; disposition pending — not committed this round).

## 14. AI-7D dependable invariants
- ChangeSession/ChangeExecution are separate durable objects; `approved_for_execution` is restorable+executable, not terminal.
- Polling truth = persisted state (GET session + GET execution); no realtime bus.
- Canonical commit is atomic + at-most-once + revalidatable.
- char_views handler is registered in production JOB_HANDLERS; `_real_character_view_generator` is the default; char-sync reuses the JobQueue job + skips done views + durable per-char record → restart-safe.
- Manifest is deterministic from approved ops + committed canonical (recomputable; no second creative truth source).
- Restart recovery: JobQueue `_load` (non-video running→queued) + recover_executions + recover_pending_videos; no duplicate external generation.

## STOP
AI-7C-S complete. Do not begin AI-8 / AI-7D without a new approved plan.
