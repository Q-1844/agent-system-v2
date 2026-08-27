# AI-7C-H 最终交接（稳定事实，供下一阶段依赖）

> 以下仅为已验证的稳定事实。细节与证据见 `AI-7C-H_COMPLETION_REPORT.md`。

## 分支与代码状态

- 分支 `refactor/shot-pipeline`，本阶段终态 HEAD：`bd6f98a`
- 本阶段提交：`cd17535`（后端 F-1/K1/F-2+H0+H3 后端）、`636c7ae`（前端 H2/H3）、`8d3e24c`+`c10bcea`（H4 测试）、`bd6f98a`（H3 澄清加固）
- 全量测试 509 passed；typecheck 仅 6 个预存在错误（AI-7C-S 基线一致）；vite build 通过

## 稳定架构事实（下一阶段可直接依赖）

1. **F-1**：`creative_source_signature`（src/revision/models.py）不含 Character.views——生产写回不触发 stale；description_en 等真创作突变仍触发。autopilot 的 `build_project_source_signature`（独立函数，含 views）不受影响、行为不变。
2. **K1**：`character_appearance_sig(description_en, art_style)`（character_views_handler.py）。job.params.appearance_sig 与 `output/characters/<safe_name>/views_meta.json` 的 sig 一致→resume 跳过 done views；不同/无记录→全部真实重生成；无 sig（legacy）→done-skip。文件版本化 `{view}_{sig8}_seg.png`，历史不删。中文名角色目录 = `_safe_filename(name)`（中文→`_`）。
3. **F-2**：`run_production_planning` per-scene checkpoint 落 `ex.planning`（scene_id+plan_id+status=approved）；re-entry 用 done_records 跳过（planner 不重复调用）。
4. **Event B**：`recover_executions(project_dir, project_name, rt=...)` 对 stage∈{character_sync, production_planning} 且非 blocked/终态的 execution 启动自动 kickoff（`_maybe_kickoff_postcommit`，进程内 `_INFLIGHT_POSTCOMMIT` 防双派发）。JobQueue `_load` 将非 video 的 running job 重置 queued 并在 `start()` re-route（同一 job_id 复用）。
5. **K3**：项目身份 localStorage key `agnes:project:selected:v1`；`projectStore.identityReady`（Promise）——任何后端真相恢复前必须 await。
6. **意图路由**：前端 `agentUi.CHANGE_INTENT_RE` 与后端 `inquiry._CHANGE_VERBS` 同族（含 换回/恢复到/让…更 等）；写意图经 `_bridgeChangeIntent` 进 ChangeSession（source=floating_bar|direct_agent_chat + contextScope）。后端 `_classification_with_scope`：模块补充**只由 scope（entity_refs）驱动**；文本提及角色名≠character 模块（回归锚定）。character_proposal 内部已合并 entity_refs 的角色指向。
7. **外观变更词表**（inquiry._MODULE_KEYWORDS.character）：短发/长发/发型/发色/造型/外观/盔甲/装甲/战衣 等 18+ 词——外观变更请求必须命中 character 模块，否则 K1 链断。
8. **characters 页**：选中角色自动设置 Agent context（type=character, name）。

## 已验证的端到端事实

- 真实浏览器自然语言 → final.mp4 全链通（含 F5 + 后端硬重启中途恢复），证据：`projects/AGNES_GoldenPath_H/`（final.mp4、新三视图 f326d472、新视频、s02 未动）
- 双角色崩溃重启（mock soak）：A 不重生成、B 续跑、无 stale 误报——`tests/test_ai_7ch_h4.py::test_f1_eventb_two_char_crash_restart`

## 已知限制（勿在新阶段当作 bug 重查）

- 创作历史版本不可知（"换回之前那个造型"只能问用户要描述）
- char-sync 单角色 all-or-nothing（mid-flight 崩溃重做该角色 3 view）
- 小视口下 Floating Bar 遮挡底部按钮（坐标点击失效）
- 无 scope 无指向的变更请求 → blocked("无 completed 提案可编译")（信息不友好）
- 前端把 blocked 会话视为 alive（新请求并入而非新开）
- jobs.json 无限增长（每次全量序列化）+ update_shot 读-改-写非原子——预存在
- start.py 明文 key 待轮换（未自动改写）

## 运行环境

- 后端：`python -m uvicorn api_server:app --port 8000`（需 AGNES_API_KEY 环境变量，.env 已配）
- 前端：`npm run dev`（vite，端口 7777 起顺延；API base 硬编码 http://localhost:8000）
- Golden Path 一次性项目：`AGNES_GoldenPath_H`（保留供验收；其 6 个 ChangeSession 均 rejected——探针后清理）
- seed 工具（未提交）：`gp_seed.py`、`gp_views.py`；jobs 备份：`cache/jobs.json.bak-7ch`

## AI-7C-H 状态：PASS，STOP（未开始 AI-7D）
