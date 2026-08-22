# AI-7C Real Acceptance Results（§103/§104/§106）

> 模板。验收时填实际结果。**不伪造 PASS**——环境/网络无法跑则记为 PENDING，分开报告自动化 proof 与缺失的 live proof（§104）。

## 0. 环境
- 后端重启：[x] N/A for gated_real_7c（驱动 standalone，不经 uvicorn；浏览器 §106 才需重启）
- 前端：[ ] `npm run dev` port 7777（§106 待跑）
- AGNES_API_KEY：[x] 已配置（.env 加载，不打印）
- 测试项目：[x] disposable `projects/__gated_7c`（1 char + 1 scene + 1 shot，shot-only 变更）

## 1. §103 真实 Agnes 最小执行（gated_real_7c.py Phase A）— PASS
命令：`set -a && . ./.env && set +a && D:/Delta Force/python.exe gated_real_7c.py`

| 检查 | 结果 |
|---|---|
| execute → 原子 canonical commit（committed） | [x] PASS |
| manifest 落盘 + char/shot actions 正确 | [x] PASS |
| §109 lineage（plan_id/approval_id/operation_digest/preview_id） | [x] PASS |
| character_sync（shot-only no-op，0 Agnes） | [x] PASS |
| run_production_planning（真 Planner + targeted run 创建，target_scene_ids=[s01]） | [x] PASS |
| 真首帧生成（real first_frame done） | [x] PASS |
| 真视频 create_video + video_id 持久化 | [x] PASS |

Phase A capture（最近一次）：execution_id=exec_fe2d7789acd0 run_id=prun_98b362fba3
job_id=56657f25f193 video_id=task_R5cWhyhJeMJvDSo0huOHnSQ1A2VcJc7U

## 2. §104 真实视频中途后端重启（gated_real_7c.py Phase B）— PASS
> 技术价值最高项。失败即整体 FAIL。

| 检查 | 结果 |
|---|---|
| 视频已提交 Agnes + 已得 video_id（Phase A） | [x] PASS |
| 后端重启（asyncio.run cleanup 模拟，phase_A loop 关闭） | [x] done |
| ChangeExecutionSession 恢复（same execution_id） | [x] PASS |
| ProjectProductionRun 恢复（same run_id） | [x] PASS |
| Job 恢复（same job_id，保留 RUNNING orphan） | [x] PASS |
| 续 poll 原 video_id（不重 POST /v1/videos） | [x] PASS（video_id 未变） |
| 最终视频正常完成（mp4 落盘） | [x] PASS（status=done, final_video=vv_949cc128, s01_c01.mp4） |

video_id before/after：task_R5cWhyhJeMJvDSo0huOHnSQ1A2VcJc7U / task_R5cWhyhJeMJvDSo0huOHnSQ1A2VcJc7U（相同 ✓）

**NOTE**：原 video job J1 留 `running` orphan（phase_A worker 中途死）；video 经 restart-recovery
poll 同一 video_id 完成——既有 SceneAutopilotRun 重启恢复行为，非 7C bug、非 duplicate create_video。
（未来 cleanup：恢复时 settle 原 J1 job 状态；不阻塞 AI-7C acceptance。）

## 3. §106 浏览器验收（见 §106_browser_acceptance_checklist.md）
| 场景 | 结果 |
|---|---|
| 批准 ≠ apply（批准 0 mutation） | [ ] PASS / FAIL |
| Execute 不自动发生在批准时 | [ ] PASS / FAIL |
| execution timeline 反映真实 stage | [ ] PASS / FAIL |
| §79 pre/post-commit 措辞一致 | [ ] PASS / FAIL |
| canonical commit 至多一次 | [ ] PASS / FAIL |
| 受影响 scope only（不整项目重生成） | [ ] PASS / FAIL |
| 历史资产保留 | [ ] PASS / FAIL |
| refresh → execution 恢复（无重复 execution/Agnes） | [ ] PASS / FAIL |
| stale → blocked + 0 mutation + 不可 rebasing resume | [ ] PASS / FAIL |
| cancel before commit（canonical 不动） | [ ] PASS / FAIL |
| cancel after commit（creative 保留） | [ ] PASS / FAIL |
| JobQueue SSE 重连 + 降级轮询 | [ ] PASS / FAIL |

## 4. 自动化 baseline（§105）
- backend tests：____ passed（基线 481 + 1 pre-existing flaky deselected）
- frontend typecheck：____ new errors（基线 0 new / 6 pre-existing）
- vite build：[ ] PASS

## 5. 已知 deferred（需后续接通后再 acceptance）
- character visual sync（character_views chain handler）—— char 变更才触发；shot-only 变更绕过本次。
- shot.audio 真执行（dialogue 变更）—— v1 无 dialogue op。
- SceneAutopilotRun per-shot target §60（targeted run 当前跑全 target scene；单 shot 项目无影响）。

## 6. AI-7C acceptance
- [ ] PASS（1+2+3 全 PASS，无 FAIL）
- [ ] FAIL（列失败项 + 对应 commit 边界）
- [ ] PENDING（环境无法跑某项，列明）

> 全 PASS 后才写「AI-7C ACCEPTED」，建新 baseline，再讨论 AI-8（不提前进 AI-8，否则 Reviewer 错误视频分不清是 AI-8 问题还是 7C 执行不严格）。
