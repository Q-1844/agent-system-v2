# AI-7C-S / S5 — TRAE INDEPENDENT AUDIT（独立对抗审计最终报告）

> 审计者：Trae / GLM-5.3（全新独立审查者，非实现者）
> 执行文档：`详细流程文件/AI-7C_Trae.md`
> 审计对象：AI-7C-S / S5 稳定性收口（实现者声称 **AI-7C-S COMPLETE — PASS**）
> 事实来源优先级：Git 历史/代码 > 磁盘持久化数据 > 真实后端行为 > 真实前端行为 > 浏览器观察 > 真实 Agnes 结果 > 自动测试 > 实现者报告
>
> 证明层标记：**A** 确定性单元/协议 · **B** 集成生产代码路径（Agnes 仅网络边界 mock）· **C** 真实浏览器 E2E · **D** 真实 Agnes live · **E** 独立人工/静态审查

---

## 1. Baseline

| 项 | 值 |
|---|---|
| 分支 | `refactor/shot-pipeline` |
| HEAD | `96ddcc4261049d502e252075bc73385b2ae4dd3c` |
| 工作区 | 有未提交 WIP（见 §14；审计全程未修改任何生产代码/用户 WIP） |
| 审查提交 | `327b580` `02a02d2` `a35dfef` `144171f` `8b64e6b` `a1dbfaa` `d36d3b4` `96ddcc4`（全部存在，与声称链一致） |
| 环境 | Windows / Python 3.13.0 / 真实 Vite dev(7778) + 真实 FastAPI(8000) + 真实 Agnes（.env 凭证有效，未泄露） |
| 模型 | GLM-5.3 |
| 审计产出脚本（未入库） | `audit_browser_7c.py`（B1-B5 浏览器门禁）、`audit_soak_7c.py`（崩溃窗口 soak）、`audit_b1_diag.py`（B1 网络诊断） |

---

## 2. Report Claim Verification（claim ledger）

| Claim | 报告证据 | 独立验证方式 | Verdict |
|---|---|---|---|
| H1 polling 不冻结 | C/Browser/B1 | E 代码 + C 本审计浏览器实测（30s 诊断 14 tick；完整审计 8s 窗口 3 次 execution GET；stage 无 remount 推进到 production_planning）+ 后端访问日志交叉验证 | **VERIFIED** |
| H2 刷新/remount 恢复 | C/Browser/B2 | E 代码（`SESSION_NOT_RESTORABLE` 排除 approved；恢复 plan+execution+轮询）+ C 本审计 remount 实测（恢复 execution + 轮询续跑 2 tick/4s） | **VERIFIED**（F5 全页重载受 K3 限制，见 §13.3 —— 按 §9 分开审计，不判 H2 FAIL） |
| H3 真实 char-sync 路径已运行 | D/live, job 964eba120ca2, 3 次调用, views done+url+local | 持久化证据核验（jobs.json 中 job 存在、attempts=2 佐证跨重启复用、execution 记录 character_sync completed、3 个真实 PNG 329-542KB）+ **本审计 3 轮全新 live 重跑**（真实 Agnes 经公开 UI/HTTP 路径，每轮恰好 1 job/3 视图） | **VERIFIED** |
| M1 公开 execute 接通 | B/S5-1 endpoint | E 代码链追踪（execute 端点→start_replay_commit→commit→character_sync→resume→kickoff→planning）+ B 套件测试 + D 本审计 live 重跑 | **VERIFIED** |
| M2 manifest bounded | B/测试 | E `compute_production_manifest` 代码审查 + 测试 + D live manifest 实证（仅 s01/s01_c01/1 char，非整项目） | **VERIFIED** |
| M3 resume/restart 不重复生成 | B soak + D live | 本审计崩溃窗口 soak（S1/S2/S4）+ durable jobs.json 计数（每 execution 恰 1 job） | **VERIFIED**（附带 F-1 新发现：某崩溃窗口恢复被阻断——无重复但无法继续，见 §18） |
| M4 preflight scope | B via HTTP | E `preflight_project(target_scene_ids)` 代码 + s4 测试 + live manifest planner_scene_ids=[s01] | **VERIFIED** |
| M5 stale 不误导 Resume | E 代码 + B soak E | E 代码（`staleBlocked`→重新提案，不显尝试恢复）+ C 本审计 B4 实测（blocker 显示 + 无尝试恢复按钮 + 重新提案按钮存在） | **VERIFIED** |
| L1 handler 生产注册 | routers/agents.py:66 | E 启动图独立追踪（api_server.py:80 import routers.agents → 模块级 `JOB_HANDLERS.setdefault("character_views", handler)`）+ s2 测试 + D live（生产后端真的执行了 char_views job） | **VERIFIED** |
| L4 manifest 可重算 | B 测试 | E `_ensure_manifest` 代码 + s3 测试（缺失重算/未提交不伪造） | **VERIFIED** |
| L6 前端消费权威 eligibility | E 代码 | E 代码 + C B4 实测（stale banner 出现 + Execute 按钮 disabled） | **VERIFIED** |
| 498 passed + 1 pre-existing flake | 报告 | 本环境复跑：486P + 13F（**13 个失败全部为本机缺 ffmpeg 的环境失败**，均在 AI-5A 时代 autopilot 测试；总测试数 499 与实现者 498+1 完全吻合；**AI-7C/7C-S 测试 0 失败**） | **VERIFIED（环境差异已解释）** |
| vue-tsc 0 新错误 | 报告 | 本机复跑：6 个错误全部 pre-existing（store/index.ts×4 + 2 组件）；AI-7C-S 触碰文件 0 错误 | **VERIFIED** |
| vite build PASS | 30.34s | 本机复跑：EXIT 0，`✓ built in 36.57s`，dist 产出 | **VERIFIED** |

---

## 3. H1 — execution polling 不再冻结

- **code path**：`src/store/changeSession.ts` `shouldPoll()`（L60-66）将 `approved_for_execution && executionActive` 作为独立轮询判据；`_set()` 用 `shouldPoll()` 决定 start/stop。旧代码（S1 diff 确认）`if (ACTIVE_STATUSES.has(s.status)) startPoll(); else stopPoll()` —— approved 不在 ACTIVE → 一 tick 后杀轮询。根因声明与 diff 一致。
- **browser reproduction（本审计独立执行）**：
  - 诊断脚本（`audit_b1_diag.py`）：Execute 后 **30 秒连续采样，每 2.0-2.2s 恰好 1 次 session GET + 1 次 execution GET（共 14 tick）**，stage 持续 `同步角色参考图`，**0 console 错误**。
  - 完整审计（B1）：8s 采样窗口 3 次 execution GET + stage 在**无 remount**情况下由 polling 推进到 `准备制作计划`（UI 只能经 `loadExecution()` 更新——stage 前进即轮询存活的直接证据）。
  - 后端访问日志交叉验证：`GET .../cs_a` 与 `GET .../executions/{eid}` 成对出现。
- **审计过程披露**：本审计最初的 B1 网络计数断言连续 2 轮报 0，一度疑似冻结复发。经隔离排查确认为**测量方法缺陷**（Playwright sync 模式下纯 `time.sleep` 期间 request 事件不派发给 Python 处理器；改为每秒采样泵事件循环后计数正常）。此为审计工具问题，非产品缺陷；诊断脚本与修复后的完整审计双重确认 H1。
- **verdict**：**VERIFIED** · proof: **C + E**

## 4. H2 — 刷新/重挂载恢复 execution

- **restore path**：`restoreActive()`（L309-333）用 `SESSION_NOT_RESTORABLE={rejected,stale,failed,cancelled}` 过滤（approved 可恢复）；approved 分支 `loadPlan()`（F-Extra1）+ `listChangeExecutions` + `shouldPoll()` 续轮询。S1 diff 确认旧代码用 `TERMINAL_STATUSES`（含 approved）过滤 → approved 分支为死代码——根因声明与 diff 一致。
- **F5 / remount 区分**：
  - **in-app remount（B2）**：实测 `/characters → /agents → Team Chat` 重挂载后 `restoreActive` 恢复 execution，`同步角色参考图` 仍 current，**轮询恢复（4s 内 2 tick）**。
  - **F5 全页 reload**：实测确认 projectName（pinia ref）复位为默认 `我的动画短片`（SSE 重连日志 `connect project=我的动画短片` 佐证）→ throwaway 项目上下文丢失。**这是 K3 已知产品限制，不是 restoreActive 逻辑缺陷**：按 §9 指引分开审计——H2 的生命周期不变量（不误滤 approved session）经 remount 独立验证成立；K3 严重度判断见 §13.3。
- **verdict**：**VERIFIED** · proof: **C + E**（F5 场景记录为 K3 限制）

## 5. H3 — 真实 Character sync production path

- **registration**：`api_server.py:80` `from routers import agents` → 模块 import 期执行 `routers/agents.py:65-66` `JOB_HANDLERS.setdefault("character_views", handler_character_views)`。生产启动图独立追踪确认（非测试内手工 instantiate）。`tests/test_ai_7c_s2_wiring.py::test_character_views_handler_registered_in_production` 断言之。
- **production generator**：`run_character_sync` 默认 `lambda cn: _real_character_view_generator(...)`（execution_service.py:852-853）——无条件默认，无 fake 泄漏路径（`all_fake_handlers()` 无 character_views 键，不会 shadow `setdefault`）。
- **real workflow reuse**：handler 复用 `routers.characters` 的 `_build_view_prompt/_build_img2img_prompt/_get_segmentor`（不另造生成系统）。
- **live evidence（本审计独立重跑，D）**：
  - 3 轮全新 disposable 项目（`__audit_7c_a` ×3）：经**真实 UI 点击 执行计划**（公开路径）→ commit → char-sync → 真实 Agnes 生成（front text2img → side/back img2img + rembg）→ Character.views done+local+url → planning → production run。
  - durable 证据：全局 `cache/jobs.json` 中每个 execution_id **恰好 1 个** character_views job（exec_5abb…/exec_3483…/exec_758e… 各 1，state=done）。
  - 历史持久化证据核验：`projects/__gated_7c_cr/cache/jobs.json` 中 **job_id `964eba120ca2` 存在**，kind=character_views，origin=exec_bca5244d6486，state=done，**attempts=2（跨重启复用的直接佐证）**；execution 记录 `character_sync completed`（views back/front/side）+ stage=production_planning；3 个真实 PNG（542KB/332KB/329KB，时间戳 19:30:05→19:30:46 顺序生成）。与实现者声明一致。
- **restart result**：R1（char-sync 执行中重启）由实现者 S5-2 A live 证明（job 复用、无重复）+ 本审计 S4 重启模拟（新 JobQueue 实例 `_load` 后 `find_by_origin` 复用 done job，0 新生成）复核。
- **verdict**：**VERIFIED** · proof: **D + E（+ B 套件）**

## 6. M1–M5 / L1 / L4 / L6 逐条结论

| ID | 结论 | 证据要点 |
|---|---|---|
| M1 | **VERIFIED**（B+D+E） | 公开 `POST /execute` → `start_replay_commit`（revalidate→replay→`commit_canonical`）→ character_sync；`POST /resume` → stage 分发（pre-commit 同步推进 / post-commit `_maybe_kickoff_postcommit` 后台 / blocked no-op）。端点测试走真实 FastAPI client；本审计 live 重跑覆盖 |
| M2 | **VERIFIED**（B+E+D） | manifest 确定性（0 LLM）从批准 ops+投影后 project 派生；update_scene characters 触发 first_frame REGENERATE（§41 修复在位）；live manifest 实证 bounded（仅 s01/s01_c01/1 char） |
| M3 | **VERIFIED**（B+D，附 F-1） | 幂等链完整：`find_by_origin` 不重提交 + handler `done_views` 跳过 + `ex.character_sync` durable 记录 + `_INFLIGHT_POSTCOMMIT` 进程内防双派发。崩溃窗口 soak（S1/S2/S4）0 重复生成；**但发现 F-1：预写回窗口崩溃后恢复被 §68 冻结门阻断（无法继续，非重复）** |
| M4 | **VERIFIED**（B+E） | `preflight_project(target_scene_ids)` targeted 只查 scenes_to_run；非 targeted 行为不变（s4 测试双向断言）；live planner_scene_ids=[s01] |
| M5 | **VERIFIED**（C+E+B） | `staleBlocked` → 隐藏"尝试恢复"、显示"重新提案"；后端 `resume_execution` blocked no-op。B4 实测：API 强制 stale execute → blocked + UI 无 Resume + 有重新提案 |
| L1 | **VERIFIED**（E+D） | 见 §5 registration |
| L4 | **VERIFIED**（B+E） | `_ensure_manifest`：committed 且缺失 → 从 canonical+批准 ops 确定性重算落盘；未提交 → 返回 None 不伪造（s3 双测试） |
| L6 | **VERIFIED**（C+E） | `loadEligibility`→`canExecute`/`execEligibilityReason`，server truth wins；B4 实测 stale banner + Execute disabled（unknown(null) 不阻断，后端幂等兜底） |

## 7. Public Endpoint Trace（完整链路，全部实证）

```
前端 Execute 按钮（真实 UI 点击）
→ POST /api/project/{p}/change-sessions/{sid}/execute          [routers/agents.py:627, session_lock]
→ execute_plan（幂等 find_execution + §17 全量 revalidation）  [execution_service.py:181]
→ run_creative_replay（workspace 隔离 + 拓扑序 step replay + §27 等价校验）
→ commit_canonical（§35 对账 + §33 write_lock + §32 二次 stale + §31 投影 + §27 等价 + §34 INTENT/DONE）
→ compute_production_manifest（确定性，bounded）                [execution_service.py:643]
→ stage=character_sync
→（前端自动 / POST resume）→ _maybe_kickoff_postcommit → 后台 _run_postcommit_to_completion
→ run_character_sync（§68 gate → manifest char_actions → _real_character_view_generator）
→ JobQueue character_views job（origin=execution_id+stage_key）
→ handler_character_views（生产注册）→ Agnes generate_image ×3 + rembg → Character.views
→ stage=production_planning → run_production_planning（真 planner + §54 all-plan gate）
→ ProjectProductionManager.start_run（target_scene_ids + shot_actions，targeted run）
→ 既有 JobQueue/Producer（first_frame/video/compose）
```
- **creative_only**：commit → `done/completed` + `production_skipped_by_user`，0 Agnes/0 ProductionRun（代码路径 + 7C-3/7C-6 测试）。
- 本审计 3 轮 live 重跑覆盖到 production run（2 轮跑出 final.mp4 全链路产物）。

## 8. Restart / Soak Matrix（本审计崩溃窗口攻击）

| case | iterations | 重启/崩溃窗口 | expected | actual | 重复计数 | verdict |
|---|---|---|---|---|---|---|
| S1 (R2/R5) 完成后 rapid resume | 8 轮 resume | 无（post-completion） | 0 重复副作用 | img 调用恒 3；1 execution；1 job；blocked 后 resume no-op | 0 | **PASS** |
| S2 (W4) 预写回窗口崩溃 | 1 | views 已持久化、durable 记录未写 | 重入不重复生成 | 0 新 Agnes 调用；job 复用 1；**但 §68 冻结门阻断恢复（PRODUCTION_PREREQUISITE_FAILED→blocked）** | 0 | **FINDING F-1**（无重复；可继续性破坏） |
| S3 (R4) planning 中途崩溃 | 1（s01 成功 s02 崩）→重入 | 多 scene 部分创建 | 持久态一致重入 | planning 记录完整一致；planner 对 s01 重复调用 1 次（外部 LLM 重复，持久覆盖一致） | 持久态 0；外部调用 +1 | **PASS（含 F-2 备注）** |
| S4 (R3) char-sync 完成后重启 | 1（新 JobQueue `_load`） | post-char-sync | job 复用、planning 继续 | 重启实例 `find_by_origin` 复用 done job；0 新 img；planning 完成 | 0 | **PASS** |
| R1（char-sync 执行中重启） | 实现者 live（attempts=2 佐证） | job running | 同 job 复用不重生成 | 持久化证据核验一致 | 0 | **VERIFIED（持久证据）** |
| 套件 soak B/C/D/E | 各 1 | 快速 resume/隔离/失败/资产保留 | 见测试 | 全部通过（本机复跑含在 486P 内） | 0 | **PASS** |

## 9. Browser Matrix（本审计独立执行，真实 Vite 7778 + 真实 FastAPI 8000 + 真实 Agnes）

| case | 方法 | 结果 |
|---|---|---|
| B1 多 tick 轮询 | 网络请求计数（事件循环泵采样）+ stage 采样 + 后端日志交叉 | **PASS**：诊断 30s/14 tick 无冻结 0 console error；完整审计 8s 窗口 3 次 execution GET；stage 无 remount 推进至 production_planning |
| B2 恢复 | in-app remount（`/characters→/agents→Team Chat`） | **PASS**：restoreActive 恢复 execution + timeline current + 轮询续跑（2 tick/4s） |
| B2-F5 | 真正 F5 全页 reload | **RECORDED**：projectName 复位默认（K3 实测确认；H2 机制经 remount 独立验证，不因此判 FAIL） |
| B3 approved 未执行 | 离开→返回（2 轮） | **PASS**：Approved + Execute 保持；0 自动执行（后端 executions 列表为空） |
| B4 stale blocked | 改 canonical → 观察 eligibility → API 强制 execute → UI | **PASS**：stale banner（"计划已过期…"）+ Execute disabled（L6）+ blocked `PROJECT_CHANGED_AFTER_APPROVAL` + **无"尝试恢复"** + 有"重新提案" |
| B5 completed 不重执行 | char-sync 完成后 remount | **PASS**：同 execution_id；executions 列表仍 1；全局 jobs.json 该 execution 恰 1 个 character_views job；views 三视图 done+local+url |

## 10. Live Agnes

- **是否重跑**：是（本审计 3 轮独立 live 验收 + 1 轮诊断），符合 §14 推荐的最小 scope（disposable 项目 / 1 Character / appearance-only approved change / 公开 execute 路径）。
- **请求种类**：generate_image（front text2img + side/back img2img）×3/轮、planner LLM chat、first_frame img2img、video create/poll（2 轮跑完 compose）。
- **凭证**：.env 读取，未打印、未写入本报告。
- **logical calls**：每轮恰好 3 次 character view 生成（全局 jobs.json 每 execution 1 job + views 顺序时间戳佐证）。
- **job id**：历史 live 证据 `964eba120ca2`（attempts=2，跨重启复用）核验属实；本审计新增 3 个 job（各 attempts=1，无重复）。
- **restart**：R1 由实现者 live 证明 + 本审计 S4 复核。
- **resulting views / persistence**：`Character.views` front/side/back = done + url + local（PNG 落盘 `output/characters/{name}/*_seg.png`）。
- 结论：**live D 级证据成立且可复现**。

## 11. Project Isolation

- 套件 soak C（A/B 同名 Character "LinXia"）通过：A execution 只写 A、B canonical 签名不变、B views 仍空、仅 A 产生 3 次调用。
- 代码层隔离键独立审查：per-project `ProjectStore`；JobQueue job 按 `project` 标记；`session_lock(project, session)`；char-sync 的 job origin=`{execution_id}:{char}`（execution_id 内含项目作用域）；handler 经 `get_project_manager().get_project_dir(job.project)` 解析——late async callback 目标由 job.project 决定，不依赖前端 current project。
- 本审计多项目并行执行（__audit_7c_a/b/diag/soak×4 同 backend）期间无串扰迹象（各项目 jobs/manifest 独立正确）。
- 结论：**VERIFIED（B + E）**。

## 12. Manifest

- **bounded scope**：live manifest 实证——update_character（单角色外貌变更）→ `character_actions={Xiaoxia: regenerate}`、`shot_actions={s01_c01: first_frame/video regenerate}`、`planner_scene_ids=[s01]`。**未整项目重算**；依赖闭包覆盖（引用该 char 的 shot frame+video）。
- **update_scene**：M2 修复实证（summary 或 characters 变更 → scene 内 shot first_frame regenerate；s3 双测试）。
- **reconstruction（L4）**：manifest 可从（批准 ops + committed canonical + execution）确定性重算（纯函数 0 LLM）；崩溃窗口模拟（删 manifest + 清字段）→ `_ensure_manifest` 重算落盘；未提交时不伪造。**不构成第二 creative 真源**（committed canonical 仍是唯一真源，manifest 是 execution artifact）。
- **资产保留**：soak D 实证 char-sync 只写 Character.views，旧 first_frame/video/final_video 不被删改。
- 结论：**VERIFIED（B + E + D 实证）**。

## 13. Known Limitations Review（独立判断）

### 13.1 K1 — done_views 粗粒度跳过 → **ACCEPTED NON-BLOCKER（附实际后果说明）**
- 代码证实：`compilers.py` update_character 只改 description_en 不清 views；handler `done_views` 跳过已 done 视图。
- **实际后果（需明确记录）**：对已有 done views 的角色做外貌变更 → manifest 要求 regenerate → char-sync 进入但 **0 次 Agnes 调用、views 不刷新** → 下游 shot 重生成使用**旧角色参考图**。即"外貌变更→参考图刷新"这条最自然的产品预期在 v1 不成立（所有证明场景均用无 views 角色规避了该分支）。
- 判断：**非 blocker**——这是 AI-7C 原始设计（handler 头注释 "coarse v1, 够 §103 acceptance"）且在报告/handover/测试注释三处一致记录，不属 AI-7C-S 稳定化回归；但**建议 AI-7D 之前修复**，否则依赖 char-sync 语义的下游阶段会建立在"参考图可能过期"之上。

### 13.2 K2 — permanent failure → blocked 无自动重试 → **ACCEPTED NON-BLOCKER**
- 代码证实：char_views job 失败 → `_run_postcommit_to_completion` catch → blocked（blocker 保留）；`resume_execution` blocked → no-op（§81）；重试需重新提案。
- soak E 三层一致性（persisted/API/UI）验证通过；B4 实测 UI 引导重新提案而非误导 Resume。与设计声明一致。**不建议擅自加自动重试**（同意实现者）。

### 13.3 K3 — projectName 不跨 full reload 持久 → **ACCEPTED NON-BLOCKER（产品导航级限制，非生命周期缺陷）**
- 实测确认：F5 后 pinia projectName 复位默认 → 恢复的是默认项目上下文。
- 判断依据：① H2 的核心不变量（restoreActive 不误滤 approved session、execution 恢复、轮询续跑）经 in-app remount 独立验证成立——remount 走的正是 `restoreActive` 同一代码路径（TeamChatPanel mount）；② 该限制先于 AI-7C-S 存在、影响所有项目导航而非变更执行特有；③ in-app 导航（产品正常使用路径）完整保留上下文。
- **不构成 AI-7C refresh-recovery 承诺的破坏**（该承诺的对象是 execution/session 生命周期，且在可达路径上成立）；但作为产品体验缺口建议后续用 localStorage 持久化 projectName。

### 13.4 K4 — normalize_module WIP 未提交 → **ACCEPTED NON-BLOCKER（deferred，实证无依赖）**
- 4 个问题逐一回答：
  1. AI-7C/7C-S **不依赖**该 WIP：WIP 位于 `ReadOnlyPlanValidator`（AI-7A 分析层，变更会话的咨询/分析阶段），而 AI-7C 执行路径的 module 名来自 plan compiler（不经该 validator）；所有 AI-7C 测试直接 seed plan。
  2. **clean HEAD 验证**：临时 detached worktree（96ddcc4，零 WIP）上运行全部 AI-7A/7B/7C/7C-S 测试文件 → **125 passed, 0 failed**。PASS 来自已提交仓库。
  3. WIP 是未来 AI-7A 分析归一化方向（LLM 复数 module 名→规范名），方向正确、下游使用单数。
  4. handover **未**依赖 WIP 行为。
- 结论：deferred 合理；**不擅自提交**（非本审计产出），交由用户处置。

---

## 14. Clean HEAD / WIP Dependency

| 检查 | 结果 |
|---|---|
| clean HEAD（detached worktree @96ddcc4）后端 AI-7B/7C/7C-S 测试 | **125 passed / 0 failed** |
| 工作区（含 WIP）全量套件 | 486 passed / 13 failed（全部为本机缺 ffmpeg 的环境失败；AI-7C 相关 0 失败） |
| frontend build（esbuild） | EXIT 0（36.57s，dist 产出） |
| public API startup | 后端正常启动（含 AI-7C Execution 恢复：发现 3 个 active execution 并对账） |
| Character handler 注册 | 启动即注册（api_server→routers.agents→JOB_HANDLERS），live 多轮 job 执行佐证 |
| **结论** | **PASS 不依赖未提交 WIP；来自 committed repository** |

## 15. Regression

- **backend**：本环境 486P/13F（13F 全为 ffmpeg 缺失的环境失败，均在 `test_full_project_autopilot.py`/`test_project_autopilot.py` 的 video stage，`[WinError 2] 系统找不到指定的文件`；实现者环境有 ffmpeg 故为 498P）。测试总数 499 两边一致。**AI-7C/7C-S 测试 0 失败**。
- **flaky（test_case7_source_changed_blocked）**：本环境失败原因=ffmpeg 缺失（video stage 崩，非 timing）。代码路径分析：S 系列提交虽触碰 `project_autopilot.py`，但 non-targeted 路径（该测试所走）语义不变（`target_scene_ids=None` → 行为与旧代码一致，diff 逐行核对）。测试本身存在真实竞态（等 scene1 child 创建→改 source→等 scene2 校验捕获，若 scene1 极快完成则竞态失利）。**结论：PRE-EXISTING FLAKE**（AI-5A 时代测试 + 已知竞态 + S 提交未改其语义）。
- **browser**：B1-B5 全 PASS（第 1-2 轮 B1 失败为本审计测量工具缺陷，已定位并修复测量方法后 2 轮通过；产品行为无 flaky 证据）。
- **typecheck**：6 错误全部 pre-existing（`store/index.ts`×4、`CreativeWorkspaceStudio.vue`×1、`ShotCard.vue`×1）；AI-7C-S 触碰文件（changeSession.ts/ChangePlanReview.vue/api/index.ts）0 错误。
- **build**：EXIT 0。

## 16. Git Hygiene

- **AI-7C-S 提交范围（32c3d6f..96ddcc4，1859 insertions）**：扫描无 API Key / 凭证 / 生成素材 / 临时项目 / trace / debug print / dist。**干净**。
- **仓库历史（实现者声明之外）**：⚠️ 存在**先于 AI-7C-S 的明文密钥**——`start.py`（2026-07-29 基线提交，AGNES/MIMO key）、`test_apikey.py`/`test_apikey2.py`（2026-08-07）。属历史遗留、非本轮引入，不构成本轮 blocker，但**建议尽快轮换密钥并清理**（历史 rewrite 或至少作废该 key）。
- 工作区未提交内容与实现者声明一致（WIP/saved_characters/components.d.ts/流程文档），未擅自改动。

## 17. Adversarial Findings（15 类对抗检查）

| # | 类别 | 结果 |
|---|---|---|
| 1 | test-only fix | 未发现。S1-S4 均改生产代码；测试走真实端点/handler |
| 2 | dead / unreachable branch | 未发现新增死分支。S1 修复的正是旧死分支（approved-restore） |
| 3 | lifecycle contradiction | 未发现。`TERMINAL_STATUSES`（提议语义）与 `SESSION_NOT_RESTORABLE`（可恢复性）分离自洽；`shouldPoll` 独立判据与 restore 一致 |
| 4 | fake generator leakage | 未发现。`_real_character_view_generator` 无条件默认；`all_fake_handlers()` 无 character_views 键；soak/endpoint 测试仅 mock Agnes client + `_download` 网络边界 |
| 5 | production handler 未注册 | 未发现。启动图实证注册 + live 执行 |
| 6 | test 绕过 public endpoint | 未发现。S5-1/S5-2a 均经真实 FastAPI TestClient 端点 |
| 7 | recovery 仅在 refresh 前有效 | **部分成立 → F-1**（预写回窗口：views 已持久化后崩溃 → 恢复被冻结门阻断） |
| 8 | restart 重复 external work | character 生成：未发现（job 复用 + done_views + durable 记录 + at-most-once 实证）；planner LLM：**F-2**（planning 中途崩溃重入会对已规划 scene 重复调用 planner） |
| 9 | manifest 无法由 durable truth 重建 | 未发现。`_ensure_manifest` 确定性重算实证 |
| 10 | late async callback 写错 project | 未发现。handler 按 `job.project` 解析目录；多项目并行 soak 无串扰 |
| 11 | completed state 被重新执行 | 未发现。`execute_plan` find_execution 幂等 + B5 实测（remount 后同 execution_id、1 job、无新 execution） |
| 12 | blocked state 与 UI 不一致 | 未发现。soak E 三层一致 + B4 实测 |
| 13 | current selection 与 historical asset 混淆 | 未发现。soak D 实证旧资产保留 |
| 14 | uncommitted WIP 隐式参与 production | 未发现依赖。clean HEAD 125 测试全过（K4） |
| 15 | broad scope regeneration | 未发现。live manifest bounded（单 scene/shot/char） |

## 18. New Findings（本审计新发现）

### F-1 · MEDIUM — char-sync 预写回崩溃窗口：恢复被 §68 冻结签名门永久阻断
- **reproduction**：`audit_soak_7c.py` S2（真实 `run_character_sync` 代码路径）：char-sync 完成（views 已持久化 + job done）→ 模拟崩溃（回滚 `ex.character_sync` 记录、stage 置回 character_sync）→ 重入 `run_character_sync` → `_assert_agnes_prerequisites` 抛 `PRODUCTION_PREREQUISITE_FAILED`（"canonical 提交后创作态已变化"）→ execution blocked。
- **root cause**：`creative_source_signature` 包含 character 的 **views 键名**（`src/revision/models.py:437`：`"views": sorted((c.get("views") or {}).keys())`）；§68 冻结门（`execution_service.py:809-813`）对 `cur_sig == expected_after_signature` 严格相等比较。**sanctioned 的 views 写回本身改变签名**，与外部篡改不可区分。首次进入时 gate 通过（views 尚未写），签名在写回后发散——此后任何重入（含正常多角色执行中第一个角色完成后的崩溃恢复）都被阻断。
- **影响窗口**：① 单角色：views 写回（handler 完成）与 `ex.character_sync` 记录落盘之间（亚秒级，但真实存在）；② **多角色：角色 A 完成后、角色 B 完成前的整个生成时段（真实 Agnes 下以分钟计，崩溃概率不可忽略）**。
- **impact**：无重复外部生成（at-most-once 保持）；views 已持久化；但 execution **无法恢复完成**（blocked + 误导性 blocker 文案"项目已变化"），需重新提案。与 handover §8/§14 的 restart-safe 精神冲突（字面上"不重复执行已完成工作"仍成立，但"崩溃后可续"不成立于该窗口）。
- **minimum repair boundary**（建议，未实施）：冻结门容忍"仅新增 views 键名"的签名差（例如按不含 views 键的签名比较，或在每角色写回后更新期望签名快照）；或 `ex.character_sync` 记录在写回前预写 intent。测试需补该窗口的回归。

### F-2 · LOW — planning 阶段无 per-scene durable checkpoint，崩溃重入重复调用 planner LLM
- **reproduction**：`audit_soak_7c.py` S3：双 scene planning，s01 创建成功后 s02 崩溃 → 重入 → s01 planner 被再次调用（计数 2 次）。
- **root cause**：`run_production_planning` 的 `ex.planning` 记录只在**全部** scene 批准后一次性写入（execution_service.py:938-939）；中途崩溃无 per-scene 已完成记录。
- **impact**：持久状态一致（production_plans.json 按 scene_id 覆盖写，无重复持久副本）；仅外部 planner LLM 调用重复（成本/时延）。`_real_plan_creator` 内部每 plan 即写盘（幂等覆盖），故无数据损坏。
- **minimum repair boundary**：逐 scene 追加 durable planning 记录（复用 `ex.planning` 渐进写入），重入跳过已 approved scene。

### F-3 · LOW（pre-existing，历史遗留）— 仓库历史含明文 API 密钥
- `start.py`（基线提交 085646d，2026-07-29）内嵌 AGNES/MIMO key；`test_apikey.py`/`test_apikey2.py`（d460d26，2026-08-07）内嵌 AGNES key。**均先于 AI-7C-S**，非本轮引入（AI-7C-S 提交范围扫描为干净）。本报告不打印密钥内容。
- **impact**：若仓库曾/将共享，密钥泄露风险。
- **minimum repair boundary**：轮换受影响密钥；密钥仅存 .env（已 gitignore）；清理历史（filter-repo）或至少删除明文文件。**处置权在用户**，本审计未改动。

### F-4 · INFO — 审计工具经验（非产品缺陷，供后续审计参考）
Playwright Python sync 模式下，纯 `time.sleep` 期间 page 事件（request/console）不派发给已注册处理器；网络观测必须在采样循环中穿插 Playwright 调用（如 `inner_text`）泵事件循环。本审计前两轮 B1 误报即源于此，已修正方法后验证通过。

## 19. Final Decision

```
AI-7C-S / S5
INDEPENDENT REVIEW: CLEAR WITH NON-BLOCKING LIMITATIONS
```

**理由**：
- 三项 HIGH（H1/H2/H3）全部独立成立，且证明层达到要求（H1: C+E；H2: C+E；H3: D+E）——本审计用真实浏览器、真实前后端、真实 Agnes 重新执行并推翻失败。
- 关键 M/L（M1-M5/L1/L4/L6）无 blocker：公开路径真连接（B+D）、manifest bounded 且可重建（B+E+D 实证）、幂等无重复外部生成（B+D+持久化计数）、handler 真注册（E+D）、stale UX 正确（C+E）。
- browser lifecycle 真成立（B1-B5 全 PASS）。
- restart/idempotency 真成立（at-most-once 全窗口保持；R1 live 证据属实）。
- clean committed HEAD 不依赖 WIP（125 测试全过 + build/启动/注册验证）。
- F-1/F-2 为真实缺陷但**不在 §32 BLOCK 判据内**（无重复生成/无数据损坏/无状态污染/可经重新提案恢复），且不影响任何已声称证明的有效性——判定为 non-blocking，但**必须在冻结记录中显式登记**并安排修复。K1-K4 维持 non-blocker（判断见 §13）。
- pre-existing flake（test_case7）判定为 PRE-EXISTING（非 AI-7C-S 回归）；本环境 13 个失败均为 ffmpeg 缺失的环境差异，已定位解释。

## 20. AI-7C-S Freeze Recommendation

```
AI-7C-S may be frozen.
Do not reopen without a concrete regression.
```

冻结附带条件（登记在案，供 AI-7D 或 hotfix 排期，不重开 7C-S）：
1. **F-1**（MEDIUM）：char-sync 预写回窗口崩溃 → 恢复被 §68 冻结门阻断。多角色场景窗口以分钟计。
2. **F-2**（LOW）：planning 崩溃重入重复 planner LLM 调用（持久态一致）。
3. **F-3**（pre-existing）：历史明文密钥轮换 + 清理。
4. **K1 实际后果**：对已有 done views 角色的外貌变更不会刷新参考图，下游重生成用旧参考——AI-7D 依赖 char-sync 语义前应修复。

```
STOP.
DO NOT BEGIN AI-7D.
```

---

## 附：与实现者报告（Claude Code）的对比结论

| 维度 | 实现者报告 | 本独立审计 | 一致性 |
|---|---|---|---|
| H1/H2/H3 | PASS（B1/B2/B5 + live A） | PASS（独立重跑 B1-B5 + 3 轮 live + 持久证据核验） | ✅ 一致（本审计证据更宽：补 B3/B4、30s 轮询诊断、网络层计数） |
| M1-M5/L1/L4/L6 | PASS | 全部 VERIFIED | ✅ 一致 |
| 498 passed + 1 flake | 其环境结果 | 本环境 486P + 13 环境失败（总数 499 吻合；AI-7C 0 失败；ffmpeg 差异） | ✅ 一致（环境差异已解释） |
| typecheck/build | 0 新错误 / build PASS | 6 pre-existing / EXIT 0 | ✅ 一致 |
| fresh reviewer CLEAR | 声明 | 本审计独立得出 CLEAR WITH NON-BLOCKING LIMITATIONS | ✅ 基本一致；**本审计新增 F-1/F-2 两个实现者未报告的发现** |
| 已知限制 K1-K4 | 4 项 | 维持 non-blocker，但补 K1 实际后果（下游旧参考图）与 K3 实测 | ✅ 一致（附强化说明） |
| restart-safe 不变量 | "no duplicate generation" | **字面成立**；但发现特定崩溃窗口"无法继续"（F-1） | ⚠️ 实现者声明不完整——重复性无问题，可恢复性有缺口 |

**总评**：实现者的 PASS **真实成立**（无虚报、无 mock 泄漏、无 test-only 修复、clean HEAD 自洽）；两份收口文档的证据链经得起对抗复核。本审计的价值增量在于：① 用可复现实验补齐了 B3/B4 与网络层轮询证明；② 发现了实现者与内部 reviewer 均未覆盖的 F-1 冻结门窗口缺陷与 F-2 planner 重复调用；③ 实证了 K3 与历史密钥问题。PASS 判定维持，附带上述冻结条件。
