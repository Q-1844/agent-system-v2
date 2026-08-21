# AI-7C Real Acceptance Results（§103/§104/§106）

> 模板。验收时填实际结果。**不伪造 PASS**——环境/网络无法跑则记为 PENDING，分开报告自动化 proof 与缺失的 live proof（§104）。

## 0. 环境
- 后端重启：[ ] 已重启（`D:/Delta Force/python.exe api_server.py` port 8000）——**必须先重启，否则 AI-7B/7C 代码未加载**
- 前端：[ ] `npm run dev` port 7777
- AGNES_API_KEY：[ ] 已配置（不打印）
- 测试项目：[ ] disposable（throwaway，非正式项目）

## 1. §103 真实 Agnes 最小执行（gated_real_7c.py Phase A）
命令：`D:/Delta Force/python.exe gated_real_7c.py`

| 检查 | 结果 |
|---|---|
| execute → 原子 canonical commit（committed） | [ ] PASS / FAIL |
| manifest 落盘 + char/shot actions 正确 | [ ] PASS / FAIL |
| §109 lineage（plan_id/approval_id/operation_digest/preview_id） | [ ] PASS / FAIL |
| character_sync（shot-only no-op，0 Agnes） | [ ] PASS / FAIL |
| run_production_planning（真 Planner + targeted run 创建，target_scene_ids=[s01]） | [ ] PASS / FAIL |
| 真首帧生成（real first_frame） | [ ] PASS / FAIL |
| 真视频 create_video + video_id 持久化 | [ ] PASS / FAIL |
| 0 第二次 create_video | [ ] PASS / FAIL |

Phase A capture：execution_id=____ run_id=____ job_id=____ video_id=____

## 2. §104 真实视频中途后端重启（gated_real_7c.py Phase B）
> 技术价值最高项。失败即整体 FAIL。

| 检查 | 结果 |
|---|---|
| 视频已提交 Agnes + 已得 video_id（Phase A） | [ ] PASS |
| 后端重启（asyncio.run cleanup / 实重启） | [ ] done |
| ChangeExecutionSession 恢复（same execution_id） | [ ] PASS / FAIL |
| ProjectProductionRun 恢复（same run_id） | [ ] PASS / FAIL |
| Job 恢复（same job_id，保留 RUNNING） | [ ] PASS / FAIL |
| 续 poll 原 video_id（不重 POST /v1/videos） | [ ] PASS / FAIL |
| 最终视频正常完成（mp4 落盘） | [ ] PASS / FAIL |

video_id before/after：____ / ____（应相同）

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
