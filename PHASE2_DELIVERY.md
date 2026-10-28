# A 仓 `66b81092`｜阶段二：Producer 自主分镜/剧情修订（本地实现交付）

交付日期：2026-10-27。代码基线：用户上传的 `A仓_C2增强_修复链_完整源码_66b81092.zip`（从实际 ZIP 内容构建本地基线；原始包没有可信 `_handoff/COMMIT_ID.txt`，不能宣称独立核验了 GitHub commit）。

## 结论与边界

**阶段二核心代码已编写并接入本地 C2/FullProject 运行链；241 项关联离线回归通过。** 包含真实 `FullProjectManager.start_run` 的启动、冻结计划的追加式修订、跨 hop 的 CAS/项目重放、媒体版本保护和 native POST 意图绑定。测试中使用假 Agnes/虚拟媒体，**未完成真实 Agnes 画质验收、GitHub CI 全仓绿灯、真实长片跨天 E2E**。这些须在最新 HEAD 与账号上完成，不能冒充已通过。

**原有宽松 P0 审核保持不变**，没有将严格审核接入强制生产路径。内容修订必须有指定、可检查的 R3/R4 发现记录；没有证据时不凭模型猜测质量缺陷自动改故事。发生技术故障/UNKNOWN 时先对账，不把技术异常算作剧情失败。阶段三（协作模式/智能并行升级）没有包含在本包。

## 1. 阶段二具体改动

1. `src/ai/repair_revision.py`：新增 `RepairFinding` 验证、专家建议与 Producer 明确批准、校验新镜头 ID/内容/时长/角色、CAS 先写后物化、修订链 SHA256 防篡改、保留 `base_plan_fingerprint`，记录 `effective_plan_digest`，支持多轮修订/修订后场景摘要、镜头替换/新增及显式尾帧依赖失效；保留原已接受历史视频版本，防止旧镜头回选。
2. `tools/c2_repair_bridge.py`：C2 有效计划恢复校验、受影响以外的已合格镜头锁定复用、按真实 Director 失败或经验证的 R3/R4 画面缺陷触发专家提案、Producer 决策、批准后新建 FullProjectRun。技术完成的 FullRun 只接受有确切 `verified_quality_review` 画面证据的修订，不把已有宽松 P0 结果改成严格审核。
3. `tools/full_project_cloud_acceptance.py`：C2 从原 `summary == frozen summary` 改成校验 `原冻结计划 + 修订事件` 的有效计划；承认跨 hop 尚未写完的 CAS 事件；真正替换 `FullRun` 并更新 checkpoint/summary 的运行 ID；新视频 POST 之前将 `revision_id, logical_shot_id, attempt_id, immutable_input_digest` 和 `SUBMITTING` 写入 CAS；UNKNOWN 阻断自动再提交。
4. `routers/shots.py`、`src/core/media_submission_context.py`：用 ContextVar 将原生 JobQueue 的持久化提交意图传递到 C2 CAS 包装器；不变更公开 Agnes 请求协议。
5. `src/ai/full_project_autopilot.py`：`read_scene_budgets()` 采用派生 overlay 中被批准的软预算窗口；原 `output/ai/scene_budgets.json` 文件不会覆盖；显式用户最终片长仍走原有最终校验。
6. 兼容成本与续跑修改：图片单任务 10,000、文本 50,000、视频理论授权 1,000,000,000 秒；每日真实供应商视频配额 500 秒仍按天重置；自动续跳最大 50,000 跳；修复旧恢复器 5,000 秒上限、旧图片/文本运行上限及 UTC/北京时间日界。旧冻结预算和历史 request digest 不改写。
7. `tests/test_c2_repair_revision_p1.py`：新增跨 CAS 提交中断、项目文件同步中断、原始媒体/依赖失效、两次连续修订、多轮恢复、真实旧 artifact 假快照恢复、原生 Controller 启动、专家建议批准与质量证据路径等测试；调整少数写死旧低成本限制的原测试期待值。

## 2. 修订发生时的真实顺序

`FullRun 失败或已有可信 R3/R4 画面缺陷 → RepairFinding → Director/Script Writer/相关专家 Thinking 建议 → Producer 选定并批准 → 检查所有 JobQueue/UNKNOWN 在途 → CAS 追加修订 → 物化有效项目 Shot/Scene → 仅清除受影响及显式依赖镜头的选择状态（旧视频历史留存） → 锁定无需重做的媒体 → 新 FullRun → 重新规划新镜头/生成 → 原验收、归档与恢复链继续`

R3/R4 的生产中修订**不是修改原项目的冻结哈希**。原计划身份、已结算视频回执与历史视频文件始终保存。新增镜头需要使用该场景下尚未使用的 shot_id，如 `s01_c04`。在途 `SUBMITTING/UNKNOWN` 未对账、旧 Controller 仍在运行或预算请求越界时不会强行改写任务。

### 外部画面证据输入（可选）

原有 P0 审核保持宽松。若要对技术完成但内容有缺陷的镜头发起 R3/R4 修订，可由已有审片环节经人工或可信证据确认后写入 `<project>/output/ai/repair_findings.json` 或 C2 `evidence_dir/repair_findings.json`。例如：

```json
{
  "finding_id": "review_bridge_contact_001",
  "trigger_full_run_id": "frun_current_id",
  "scene_id": "s01",
  "shot_id": "s01_c02",
  "level": "R3",
  "kind": "action_contact",
  "source": "verified_quality_review",
  "review_id": "review_2026_001",
  "evidence": "00:04 手未接触绳索；00:07 同伴仍未被救出",
  "media_evidence": "path-or-hash-to-frame-and-timecodes"
}
```

注意：上述 `verified_quality_review` 依赖**来源方实际完成证据核验**；本阶段做字段、来源声明及可观察媒体引用的守卫，不提供独立防伪签名/全自动可信视觉审核。若缺乏此证据，制作完成时不会自行虚构一项质量失败。真实 Director 结构性失败可直接使用本机 FullRun 失败日志建立 R3 Finding。

## 3. 测试

```text
python -m compileall -q src/ai/repair_revision.py src/core/media_submission_context.py routers/shots.py tools/c2_repair_bridge.py tools/full_project_cloud_acceptance.py tools/full_project_resume.py tools/resume_dispatch.py src/ai/c2_production.py src/core/quota.py
python -m pytest -q \
 tests/test_stage0_auto_planning.py tests/test_producer_planning_models.py \
 tests/test_producer_planning_skill.py tests/test_c2_archive_sources.py \
 tests/test_c2_quality_followup.py tests/test_c2_repair_revision_p1.py \
 tests/test_c2_stage0_integration.py tests/test_c2_acceptance_provenance.py \
 tests/test_c2_cross_project.py tests/test_c2_recovery_project_name.py \
 tests/test_resume_dispatch.py tests/test_quota_gate.py \
 tests/relstore/test_full_project_cloud_acceptance.py \
 tests/relstore/test_media_download_resume.py --disable-warnings -x
```

**结果：241 passed，2 warnings**。增量 Patch 已做 `git apply --check`（基线正向）和 `git apply --reverse --check`（已改工作树反向）验证，完整 ZIP 已做 CRC 校验。测试报告和 SHA256 JSON 一并交付。

**没有通过/尚未执行的验收：** 部分旧 FullProject Autopilot 全流程测试在本地长时间没有结束，因此不列为通过，也不宣称全仓 CI 通过；真实 Agnes 的复杂动作质量、每日 500s 到次日连续运行、真实完成片/音画同步、并发竞态/真实供应商 UNKNOWN 回执，需要真机验证。旧源码缺少的 `tests/fixtures/a2_authoritative_video.mp4` 也不能由本轮构造替代。

## 4. 合并与使用

- 完整源码 ZIP 为本地修订版，不是已经推送至 GitHub 的分支。避免用旧文件整体覆盖 GLM 最新 HEAD；合并时以 `git apply --check` 或 cherry-pick 差异方式逐项审查。
- 对正在执行的任务，先完成或暂停并对账所有在途视频。不要在活动 FullRun 运行期间直接替换项目文件。
- 原冻结 plan/hash、nonce 与原请求文本用于恢复旧任务；修订 ID 是追加的第二层计划版本，不能替代旧任务身份。
- 本轮没有修改协作模式列表，没有启动生产视频，没有连接 GitHub，也没有调用真实 Agnes 接口。

## 5. 最终差异索引

详见 ZIP 中的 `PHASE2_DELIVERY.md`、`交付材料/阶段二_增量代码.patch`、`交付材料/阶段二_回归日志.txt`、`交付材料/阶段二_SHA256.json`。
