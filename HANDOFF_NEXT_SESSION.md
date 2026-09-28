# 下一会话执行提示词（Phase 39C Canary → Skill 迭代 → 生成测试交接）

> **进度更新（2026-09-26，本会话）**：§3.1 已完成（子仓 322c17e：两个红测试语义反转为断言 skill 包锚点）；§3.2 已完成（fd32878：F1-F6 教训进 full-story-relay-calibration 的 SKILL.md/director.md/script_writer.md/producer.md，锚定测试 `tests/test_skill_content_canary_lessons.py`）；§3.3 已完成（ba7215a：`src/agents/pregen_checks.py` 姿态冲突预检 + `negative_prompt` 两层白名单写通路，测试 `tests/test_p39d_prechecks.py`）；父指针已补（abc9f12）。全量回归 2391 passed / 40 failed（39=baseline av_sync 同根因 + 1=cancel_race 已知间歇单跑即绿）——baseline 闸门通过。**当前停在 §3.4：等用户确认 2×3 重建方向与旧产物处置。** §3.4/3.5/3.6 原文如下。

> 本文件是上一会话（Phase 39A/39B/39C + Skill System V1 检查）的完整交接。新会话以本文为执行依据，开工前**先完整读一遍**，并遵守【纪律】节——每一条都是本会话实测踩坑后由用户亲自定版的，不是建议。

---

## 0. 你的身份

你是 zcode（执行侧），仓库 `animation-storyboard`（父仓库 `ai工作流`，Windows，Git Bash，Python 用 `D:/Delta Force/python.exe`）。用户已批准：Phase 39A（集成）、39B（Shadow Replay）、39C Canary 前 2 镜已跑完；Skill System V1 已由另一会话建成并经本会话逐项验证。**当前任务：按本文 §3 的顺序继续。**

## 1. 用户已定版的结论与裁定（不要重新讨论）

1. **Phase B 结论（用户措辞）**：Control/Safety Layer: PASS；Decision Layer: PASS WITH KNOWN EVIDENCE LIMITATIONS；批准受控 LIVE canary，不批准"已校准的自主决策"。
2. **Agent 名单**：完整协作 roster = 5 Agent（producer/script_writer/character_designer/scene_designer/director）；两种校准模式默认 active set = 4；**scene_designer 条件参与**（缺场景参考/SIV stale/SceneState·Location 根因/需重新生成审批场景参考时由 Producer 路由进来；即便参与也不拿校准工具）。skill 包 full-story-relay-calibration 绑定 5 Agent 与此一致。
3. **桥 = 用户参与触发的修复**：需要双端对齐的断缝（跨 Location、大姿态反转、级联断缝）→ keyframes 桥，由用户要求修复时触发 keyframes-bridge-repair skill；不裸重试不硬接受。
4. **Baseline 处置**：39 个 av_sync 失败挂账（AGNES_GoldenPath_H fixture 被 09-24 清理删除），不阻任何阶段；cancel_race 间歇失败是独立 issue 不扩 scope；Skill 轮的 autopilot 13 项 FileExistsError 已由本会话亲自验证为"运行中后端占 projects/ 句柄"（停后端即全绿）。
5. **丹顶鹤**：Crane_Lake_P39C 的大鹤 = 丹顶鹤设定（用户裁定 B），approved Look `look_ffc8094d1cdb`（站姿正面，参考资产 `ref_4e452a094407`）。
6. **Phase E（真实视频 E2E）等用户放行**；不要重新跑整套 Shadow Replay 去刷百分比。

## 2. 当前已验证状态快照（本会话逐项亲自核实过）

### Skill System V1（commit 9bc1ae9..6791834，工作区干净）
- 架构：`src/agents/skill_system/`（models/registry/runtime/tools）+ `builtin/` 两个 Golden Skill 包（full-story-relay-calibration 绑 5 Agent、keyframes-bridge-repair 绑 Director/Producer；SKILL.md + references/<agent>.md 渐进披露）。
- 5 个 Agent prompt 已做减法（校准/桥 workflow 段删除，身份/权限/真相纪律保留——已亲自 grep 验证）；工具面 31 个（28+skill.list/load/read_resource）。
- **纪律迁移 1:1 已验证**：窄锚定（非关键表情偏差/双端证据/不得仅凭表情偏差）、bridge_frame_check、boundary_frame_stale、三档裁定、Vision 盲区、trace 字段全部在 skill 包中（锚点级 grep 全命中）。
- **Phase D discovery 双通道已亲自复跑验证**（`_40_skill_replay.py --baseline`，日志 `_p40_d_baseline_verify.log`）：无 hint 下 Agent 第一步 skill.load 正确技能 + 13 次工具调用（含 calibration.trace_read）。**但注意**：最终回复没有完成裁定方法（reply_method_anchors 全 false，回复是 zero-write 纠偏话语"这次我没有真正执行修改…"）——skill 加载成功 ≠ 方法执行完成，这是 skill 内容迭代的直接素材。
- **发现的缺口（待修，见 §3.1）**：`tests/test_p39_live_hardening.py` 有 2 个测试仍在断言 prompt（`test_director_prompt_narrow_decision_anchors`、`test_director_prompt_bridge_frame_check_into_trace`），但锚定已迁入 skill 包 → 这 2 个测试当前是红的（CodeBuddy 的"回归全绿"漏了它们，语义反转没做完整）。

### Phase 39C Canary（已暂停，等本会话按 §3 推进）
- 项目 `projects/Crane_Lake_P39C`：6 段 × 每段 1 镜（**结构错误**，用户已认定"不对的"）；镜头已 multi 化（s02-s06 vref=prev_tail，**s01 vref=None**——turn7 设置链根参考的指令未被执行）；场景链 approved（2 Location + 6 Instance + 6 SIV）；大鹤三视图 + approved Look。
- 已生成 2 镜视频：s01_c01（major：镜内飞行→站姿→飞离硬切）、s02_c01（minor；接缝 Agent 报 1.8 / 我复测 3.1，像素接缝成立但接的是 s01 漂移尾帧）。
- **根因已查实（亲眼看图 + 读 prompt 原文）**，证据归档 `_37_fable/_39c_canary/manual_review_20260925.json`：
  - F1 冻结↔场景参考冲突无人核对（s01 SIV 含森林湖泊 vs 冻结"纯天空"）；
  - F2 镜内硬切（站姿身份参考 vs 飞行文本的 prompt-reference 姿态冲突——night_cloak 同类，模型震荡）；
  - F3 像素接缝≠内容接缝；
  - F4 UI 首帧语义错位（接力镜显示分镜图资产而非继承的 prev_tail 帧）；
  - F5 结构 6×1；
  - F6 执行侧（zcode）没亲眼看图就汇报。
- trace run `cal_20260925_201335_5c77`：有 initial assessment 但**裁定枚举字段未填**（trace 完整性缺口）。
- 前端 localhost:7777（可能还活着）；后端 PID 6620 运行中（**重启后端 = 清 turn_registry，专治 specialist_busy 僵尸**，这是既定运维手段）。

### 纪律固化清单（已进代码，别动坏）
- Clean Review 窄豁免：`session_governance.has_mutation_intent`（否定窗口）+ `should_route_to_clean_review`；director 主 turn 用它路由。
- thinking 随 Producer turn 透传（`_delegate_specialist(thinking=...)`，测试时全开 thinking=true）。
- `visual.sample_frames` 对 keyframes 桥镜回执带 `trace_reminder`（bridge_frame_check 落档）。
- 测试：`tests/test_p39_live_hardening.py`（路由/否定窗口/桥提醒）、`tests/test_p39_prompts_discipline.py`、baseline manifest `_p39_baseline_manifest.json`。

## 3. 接下来要做的（按序执行；1-2 是小步，3 起需要用户确认一次）

### 3.1 修 2 个红测试（分钟级）
把 `tests/test_p39_live_hardening.py` 的 `test_director_prompt_narrow_decision_anchors` / `test_director_prompt_bridge_frame_check_into_trace` 语义反转为断言 skill 包内容（读 `src/agents/skill_system/builtin/full-story-relay-calibration/references/director.md` 与 `keyframes-bridge-repair/references/director.md`，断言锚点在其中）。跑定向回归确认全绿。

### 3.2 Skill 包内容迭代（"改方法不改 Agent"——把 Canary F1-F6 教训写进方法）
编辑 skill 包（主要是 `full-story-relay-calibration/references/director.md` + `script_writer.md` + `SKILL.md`，必要时 `keyframes-bridge-repair`），加入：
1. **首镜/直生镜参考姿态匹配**（38 walk_side 先例）：Look 需多姿态身份参考（站姿+与首镜动作匹配的侧视），Director 提交前必须核对参考姿态与镜头动作一致；不一致 → 先补参考或换参考，不带冲突提交。
2. **bind 前场景↔冻结一致性核对**：production.inspect 的 SIV 摘要与镜头冻结 description 的地点/构图要素逐项对照，冲突（如"纯天空"vs"含森林湖泊"）→ 停下报告，不带冲突生成。
3. **负面约束进生成字段**：冻结时把 negative constraints 写进 shot 的 `negative_prompt` 字段（数据通路已存在），不是只进 trace。
4. **结构纪律**：段 = 地点/叙事单元，段内多镜；禁止 6 段×1 镜式碎片结构。
5. **方法完成度收口**（针对 Phase D 的 reply 未完成问题）：skill 的 workflow 末尾显式写明"本轮交付物 = trace_upsert（含裁定枚举字段，不许空串）+ 一句话裁定总结；缺裁定字段的落档视为未完成"。
同步更新对应测试锚点。commit。

### 3.3 两个确定性预检（小代码 + 测试，prose 不够的地方给牙齿）
1. **prompt-reference 姿态冲突预检**：视频提交前（director_visual precheck 或 adapter 层）校验参考资产描述与镜头动作语义（站姿类参考 + 飞行/奔跑类 motion → 结构化 warning，bound_required 下阻断）。night_cloak + s01 两次实证。
2. **冻结/更新镜头时 negative constraints → negative_prompt 字段**的写通路（工具或提示词层面保证）。
commit。

### 3.4 重建 Crane_Lake_P39C 分镜结构（**先问用户**）
6×1 → **2 段×3 镜**（s01 天空森林上空 3 镜 / s02 湖泊 3 镜）。已生成的 2 镜视频与 6×1 结构产物作废（或存档）。用户上轮未明确答复重组方向——开工前确认一次（顺带确认 s01 major 的处置并入重建）。重建走 Producer 正式入口（SW 改段结构 → Director 重排镜头 → Scene Agent 按新段映射 SIV → Director bind）。

### 3.5 重跑 2-shot Canary Gate（用户放行后）
 Producer 正式 REST 入口 + thinking=true；只生成前 2 镜；每镜完整链路（生成→接缝→Vision→亲证→三档裁定→trace_upsert 含枚举字段）；major 停等用户。**执行侧（你）在向用户报任何画面结论前，必须亲自抽帧并 Read 图片**（上会话的 F6 教训，用户点名批评过）。完成后启动前端（`_launch_frontend_detached.py`，localhost:7777）请用户审。

### 3.6 桥接测试（用户参与触发）
2 镜通过后放行 3-6 镜；跨 Location（天空→湖泊）接缝若出现双端断缝，用户要求修复时触发 keyframes-bridge-repair skill → Director 走 `shot.bridge_create`（10s+、中间节拍、seed 后零 refresh、bridge_frame_check 落档）。若接缝本来就平滑，不硬造桥。

## 4. 关键路径速查

| 项 | 路径 |
|---|---|
| Skill 报告/设计 | `SKILL_SYSTEM_V1_REPORT.md` / `详细流程文件/修复阶段/SKILL_SYSTEM_V1_DESIGN.md` |
| 39 报告 | `AGENT_RELAY_CALIBRATION_PHASE39_REPORT.md` |
| Skill 包 | `src/agents/skill_system/builtin/`（两个包） |
| Canary 证据 | `_37_fable/_39c_canary/`（turn1-7 transcript + manual_review_20260925.json） |
| Canary 驱动 | `_37_fable/_39c_canary_driver.py`（REST 流式 + thinking=true + transcript 存档） |
| Shadow Replay 驱动 | `_37_fable/_39_shadow_replay.py`（不再重跑刷分） |
| Phase D replay | `_40_skill_replay.py`（--baseline 无 hint 对照；我跑过一次通过） |
| Baseline manifest | `_p39_baseline_manifest.json`（39 nodeids；另 autopilot 13 + cancel_race 2 = 环境） |
| Gold trace | `projects/Fable_Pilot_Fox_and_Grapes_V1/.../cal_20260925_004933_6b30`（只读，绝不改） |
| Canary 项目 | `projects/Crane_Lake_P39C`（沙盒副本 `_P39B` 是 replay 用，别混） |
| 后端/前端 | `_launch_backend_detached.py`（:8000）/ `_launch_frontend_detached.py`（:7777） |
| Python | `D:/Delta Force/python.exe` |

## 5. 纪律红线（违反=事故，每条都真实发生过）

1. **亲眼看图**：任何向用户汇报的画面质量结论，必须先亲自抽帧并 Read；Agent 数字只是线索不是证据（F6，用户点名批评）。
2. **major 用户闸门**：Agent 判 major → 停下等用户，绝不自动重试；zcode 也不代批。
3. **沙盒隔离**：任何 Agent 批量评测/试跑用沙盒项目（_P39B 先例），真项目 canonical 零风险。
4. **baseline 闸门**：`final_failures − baseline = ∅`；环境性失败（后端占句柄）先停后端验证再下结论。
5. **不绕过正式路径**：所有 live mutation/generation 必须可追溯到 Agent turn → tool call → receipt；zcode 不直接 curl 改 canonical（建项目/建 run 这类脚手架除外，需说明）。
6. **停后端清锁**是处理 specialist_busy 僵尸的既定手段（先试 turn/cancel 端点，无效再重启）。
7. **每批次 commit**：子仓库 commit → 父仓库 submodule pointer commit（当前父指针落后约 10 个 commit，首次 commit 时一并补上）。
8. **测试阶段 thinking=true**。
9. **不开发第二批 Skill / 不做 40.md 范围外的系统重排**（40.md 的 STOP 纪律仍然有效）。

## 6. 开场白建议

向用户报告 §2 的验证结论 + §3.1/3.2/3.3 的工作清单（预计小时级），请求放行 3.3 之后进入 3.4 结构重建（需要用户确认 2×3 方向）。
