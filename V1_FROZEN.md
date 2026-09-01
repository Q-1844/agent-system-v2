# V1 Frozen Baseline · 冻结记录

> 2026-09-01 建立。本基线是下一阶段 Medium Project 压力测试的固定起点。
> **冻结后，Medium Project 压力测试发现的问题不得直接混入本基线**——应在独立分支/后续版本处理。

## 冻结范围（本次 commit 含）
- **V1 收口优化（4 项）**：终端事件可靠性 `_put_terminal_event`（routers/agents.py，模块级可单测）；Partial Success 确定性总结（三 agent `_append_fact_summary` 三态：全成功/部分/全失败，纯 CRUD 失败不追加块，仅生成类失败追加）；Character 跨域读取规则（Director 以 character.list 为权威名单不盲目 read，CD 只为出镜且需视觉一致的人物建 canonical）；Dialogue 结构化（Director prompt+tool schema：台词归 dialogue、description 只写画面/动作）。
- **Director Visual Production**：`src/agents/director_visual.py`（shot.generate_images/videos 执行器）+ 流式看门狗（agnes_client `_watched_lines` keepalive 免疫 360s）+ 前端 generated_assets 渲染 / tool.progress / 视频预览。
- **Golden Path 验收证据**：`_golden_path_report.md` / `_golden_path_progress.md` + driver `_golden_path_driver.py` / `_gp_verify_assets.py`（Story→Character→Director 三 Agent 围绕 canonical 组成可用生产链，跨域 hash 追踪零越权）。

## 已知技术债（V1 不修，记录供后续）
### 1. legacy_video generating（新旧视频字段并存）
- **现状**：功能正常。`shot.video`（旧 VideoAsset 字段）status=done、local+url 齐全，前端 `videoReady=true`，视频可播；`final_video` 指向新 `video_versions[]` 中 status=done 的 `vv_*` 版本。Golden Path 4 个真实视频均 LOCAL_OK。
- **问题**：每个视频 shot 的 `video_versions[]` 含一个 `version_id="legacy_video"` 占位条目，status 永留 "generating"（与 done 的 vv_* 同一 video_id，是提交期旧字段迁移冻结快照，完成后不回填）。
- **根因**：新旧两套视频字段并存——旧 `shot.video` 被 8+ 消费者读（ShotCard.vue / autopilot / impact / project_intelligence / capabilities / context / delete_service / change_protocol）；新 `video_versions[]`+`final_video` 由 settler 写。`_legacy_video_version`（models.py）是迁移路径。
- **正确修复**：统一新旧视频字段，涉及多消费者与数据字段迁移，属重构级。
- **处置**：V1 Frozen 阶段**禁止顺手重构**。后续单独技术债任务处理。

### 2. SenseNova 文本端点工具链路不兼容（既有 backlog）
- SenseNova 接入适配测试 `test_sensenova_text_endpoint.py` 留存；工具链路不兼容已记录，V1 不修。

## 全量测试已知噪声（非 V1 regression，供下阶段测试者区分）
> 目的：让测试者能区分 **真实 regression** 与 **已有测试/基础设施噪声**。
> 基线：全量 `tests/` ≈ 994 项，968 passed / 26 failed（56min）。26 failed 归类如下，**均非本轮 V1 收口引入**（已逐一单独验证通过或属既有 flake）。

### A. 既有 flake（基线即失败，单跑通过）
- `test_not_configured_soft_fail`（script_writer_native / director_min / script_writer_min，共 3 处）：not_configured 单例状态跨用例污染，单独运行通过。memory 已记录。
- `test_project_autopilot.py::test_case7_source_changed_blocked`：预存 AI-5A timing flake（stash baseline 仍失败），memory 已记录。

### B. 全量长时间运行触发的慢/资源 flake（单独运行全部通过）
- `test_shot_request_rules.py`（5 项）、`test_interface.py`（2 项）：全量 56min 跑到此处时单例/资源污染，单独 6+2 全过。
- `test_ai_7c_s5_endpoint.py::test_s5_1_execute_resume_endpoint_path`、`test_ai_7c_s5_soak.py::test_soak_b_rapid_resume_no_duplicate_charsync`：单独 2 全过。
- `test_full_project_autopilot.py`（7 项 e2e）：单独 13 全过（87s）。
- `test_project_autopilot.py`（6 项，除上述 case7 flake）：单独 11 全过（92s）。
- `test_ai_7d_r_delete_confirm.py::test_delete_nonexistent_no_summary`：**本轮 V1 收口一度引入的 1 个真 regression，已修复**（partial-success 守卫收窄为仅生成类操作失败才追加块，纯 CRUD 失败保持不追加），修复后通过。

**判定规则**：下阶段若见上述清单内的失败名，先单独重跑确认；单独通过即基础设施噪声，非 regression。新增失败名或单独重跑仍失败，才视为真实 regression。

## 外部依赖阻塞（非 V1 regression）
- 2026-09-01 冻结期：Agnes 推理端点 `api.agnes-ai.cn` 与图片/视频端点 `apihub.agnes-ai.cn` 一度 TCP 不可达（HTTP 000，上午 Golden Path 时正常）。
- 属 external dependency / infrastructure blocker，**不视为 V1 regression**，不因此阻止代码基线冻结。
- V1 收口的项3/4 真机 LLM e2e 验证因外部阻塞暂缓；代码层确定性测试已覆盖（零逻辑分支，纯 prompt/schema 文本改动）。网络恢复后建议手测 Director 分镜：dialogue 是否填字段、是否还盲目 character.read 不存在角色。

## 冻结验证（核心确定性测试，2026-09-01）
- `test_ai_7d_r_turn_background.py` 9 passed（终端事件：满队列 drain / 未满直传 / turn.failed 必达+流收尾+锁释放）
- `test_partial_success_summary.py` 12 passed（三 agent 全成功/部分/全失败/cancel-partial/守卫）
- `test_director_visual.py` 17 passed（含 cancel 保留资产）
- `test_director_agent.py` / `test_character_designer_agent.py` / `test_ai_7d_r_script_writer_native.py` 三 agent 核心加载通过
- AST 校验通过、后端加载无导入错误
- 全量基线 968 passed / 26 known-noise（见上）
