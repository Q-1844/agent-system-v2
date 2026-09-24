# FULL_STORY_CALIBRATION_PHASE37A_REPORT — Phase 37A（37.md §18）

日期：2026-09-24 ｜ 项目：Fable_Pilot_Fox_and_Grapes_V1（长期 Calibration Project）
执行依据：37.md ｜ 前置：36.md ｜ 分支 `feat/agent-v2-phase-d-golden-run`
正式 Calibration Trace：run `cal_20260924_230746_2e89`（output/experiments/calibration_runs/，9 links，completed）

---

## 1. 实际修改文件

**§1.1 BLOCKER（commit 0773b7b）**
- `src/core/prompt_builder.py`：+`prompt_semantic_sig(shot, stage)` / `video_prompt_freshness(shot)`（sha256_16 签名引擎；video 签名含 description——multi 编译不消费 description 的缺口由此覆盖）
- `src/storyboard/structure_service.py`：`_invalidate_stale_prompts` 公开为 `invalidate_stale_prompts`（正式 mutation receipt 入口）
- `routers/shots.py`：REST `PUT /shot/{id}` 与 update_shot_fields 同口径 invalidate + `prompts_invalidated` receipt；写 `*_prompt_en` 未带 sig 时服务端自动落签名（先失效后写入，合并请求不清新文本）；`generate_prompt_for_stage(persist=True)` 落签名；新增 `POST /shot/{id}/prompt/rebuild`
- `src/generation/video_adapter.py`：BOUND multi freshness gate（stale en 不 shadow 新字段；missing+description→`video_prompt_stale` fail closed）
- `routers/shots.py::_handler_video`：multi 生成前自动重建 `_ensure_multi_video_prompt`（fresh/override 不动；空回复 fail closed）

**§1.2（commit 0773b7b）**
- `src/core/job_queue.py`：Job 增 `error_class/error_stage/error_provider/error_model`；`str(e)` 为空以异常类名兜底；emit 记录最后到达阶段
- `routers/shots.py::_extract_video_end_frame`：错误不再为空（rc/which-ffmpeg/out_exists/stderr-len 入错误——战役期间末帧提取故障由此定位）

**§2/§3/§16（commit 2da732d）**
- 新域 `src/experiments/`：`calibration_trace.py`（run/link 两级，非 canonical，merge 语义，枚举校验）+ `tail_assessment.py`（11 维 Vision 尾帧评估，JSON-only+一次 repair）
- `routers/experiments.py`：正式 REST 契约（runs CRUD + link upsert/get）
- `api_server.py`：路由挂载

**战役脚本（commit be3b94b 等）**
- `_37_fable/`：STORY_FROZEN.md（冻结剧本）、_37_story.py（唯一事实源）、_37_setup_story.py、_37_setup_scene_system.py、_37_generate.py（submit/record/status）、_37_review_server.py（:8781）、CHECKPOINT.html、_37_trace_run.json、media/
- `_37_project_cleanup_manifest.json`（§14 删除清单）

## 2. 阻断修复

1. **prompt 静默污染**（36.md §1.2.3 系统级修复）：三道闸=签名引擎+统一 mutation 路径+multi 生成前自动重建/adapter fail-closed。**铁证**：v1g s03 实际 prompt_snapshot=旧葡萄园文本（"Grape cluster hangs still..."），雨林校准全未生效——36.md 把 s03 视频里出现葡萄串归因「故事段语义拉力」，**实为旧 prompt 文本直接入镜**。本战役 9 镜 prompt 全部新鲜重建（hash 在档），mushroom 入镜是剧本语义（经 prompt 正确到达），与 v1g 的污染现象同形不同因。
2. **JobQueue 空错误**：error_class/stage/provider/model 在档。
3. **SIV fauna 根因**（新发现，36.md §4.4 悬案告破）：SIV prompt SECTION 1 用 `identity_description`——禁 fauna 条款只写在 `description` 字段从未进入 SIV prompt。补进 identity 后 seg-A 一次出干净板（实证图在档）。**代价**：location identity 变更使两个基础 View 成为 stale 母版（期望指纹失配）→ revoke+re-approve 刷新指纹。
4. **末帧提取进程退化**：同代码在新进程成功——长进程环境退化为根因，重启即愈；诊断加固后未来同类故障可直接从错误串定位。

## 3. 删除的旧 Project（§14，用户确认 A+B 档）

41 个 DISPOSABLE，释放 **684.2MB**：33 个 A 档（__ 前缀 e2e/smoke ×23、Subagent_* ×6、Stress_Medium、Replay_Gov、_test_7cs_soakD）+ 9 个 B 档（PhaseB/PhaseC1_* ×6、Director冒烟测试、Golden_Path_AI_20260901_1132、AGNES_GoldenPath_H）。`_test_full_project` 已不存在。清单：`_37_project_cleanup_manifest.json`。

## 4. 保留 Project（18 个）

- **PROTECTED**：Fable_Pilot_Fox_and_Grapes_V1（37A 主场）、Golden_Full_Production_V1（《山雨》成片，_phase_d_gallery 本地引用）、Golden_山雨信、验收A-D ×4、scene_style_gallery、我的动画短片
- **HISTORICAL_EVIDENCE**：AGNES_完整验收_A、_p4_accept（conftest 引用）、Golden_31_Integration_Fixture
- **UNKNOWN**：1、2（用户手动测试，未删）

## 5. Calibration Trace schema

run 级：`run_id, story_id, title, created_at, story_frozen, chain_root_reference_id, story_plan[{shot_id,description,expected_tail,duration_s,story_type}], status, notes`
link 级（37.md §2 全字段）：`provider_id, provider_model, generation_mode, duration_s, prompt_hash, prompt_snapshot, reference_asset_ids, opening_reference_type, expected_tail, end_frame_url/local, mae_prev, vision_assessment{11 维+meta}, vision_meta, human_deviation_level(none|minor|major), human_action, human_reason, vision_vs_human_diff, shot_patch, prompt_rebuilt, next_shot_id, result_after_correction, retry_or_adapt, choice_reason, attempt1_rejected, notes`
§12 replay 能力：story_plan + link 记录可直接构造 `AgentCalibrationInput{story_plan, current_shot, next_planned_shot, expected_tail, actual_tail_assessment, project_state}`；`AgentDecision vs HumanDecision` 对比字段（vision_vs_human_diff / human_reason）已结构化。

## 6-9. 完整故事、Shot list、expected tails、真实视频/尾帧

《雨林奇遇》9 镜 × 2 段（s09 晨雾小径 / s10 空地水塘），全文与逐镜预编剧见 `_37_fable/STORY_FROZEN.md`（生成前冻结，未改一字）。每镜真实视频 `projects/.../output/videos/{shot_id}.mp4`（11 个版本含 2 重试）；每镜真实尾帧 `output/frames/{shot_id}_video_end.jpg`（Pages 托管 URL 在 trace）。人评页 `_37_fable/CHECKPOINT.html`（9 镜全量，媒体本地化）。

## 10. A. Link-level table（§10.A）

| 镜 | 时长 | 尾帧 vs 预期 | Vision | 人工 | 处置 | 下一镜结果 |
|---|---|---|---|---|---|---|
| N1 | 8s | 朝向/环境✓；姿态中途减速未站定 | minor | minor | 按预编剧继续（N2 首句吸收） | N2 一次成功 |
| N2 | 10s | 蘑菇/光尘/驻足✓；鼻尖接触未达 | none | **minor** | 按预编剧继续（N3 首句吸收） | N3 一次成功 |
| N3 | 5s | 坐姿/朝向✓；头歪幅度弱 | none | none | 按预编剧继续 | N4 一次成功 |
| N4 | 10s | 萤火虫✓叙事✓；**机位=脸部大特写**（吸引子） | **major** | **minor** | 用户裁定接受（接点不一致+运动稍怪可接受） | N5 一次成功（特写→大远景标准切） |
| N5 | 8s | 空地水塘/倒影✓；饮水端点未落 | minor | minor | 按预编剧继续 | N6 一次成功 |
| N6 | 8s | a1: ECU+开心表情+地理丢失→**重试**；a2: 全身侧面+浅水涟漪✓ | a1:**none(误报)** a2:minor | a1:**major** a2:minor | 重试 1 次，取 a2 | N7 一次成功 |
| N7 | 10s | 回头+蝴蝶+近景+定格✓；接触面滑至蕨叶 | none | minor | 按预编剧继续 | N8 一次成功 |
| N8 | 8s | 蝴蝶落红花+趴石+同框，**近乎完美** | none | none | 按预编剧继续 | N9 一次成功 |
| N9 | 5s | a1: 醒着+白雾伪影→**重试**；a2: **安睡**✓+蝴蝶被链继承 | a1:major a2:minor | a1:**major** a2:minor | 重试 1 次，取 a2 | **链闭合（9/9）** |

## 10.B Failure taxonomy（实际发生，未预设）

| 类别 | 次数 | 实例 |
|---|---|---|
| camera deviation（正面特写吸引子，36.md §3.7 复现） | 2 | N4（保留）、N6-a1（重试修复） |
| action endpoint 未落尾帧 | 2 | N2（鼻尖触）、N5（低头饮） |
| 叙事/情绪反转 | 1 | N6-a1（开心 vs 惊恐） |
| 参考链道具继承（36.md §3.6 实证） | 1 | N9（蝴蝶+红花从 N8 继承；重试仍继承，读作守候收尾） |
| 伪影 | 1 | N9-a1（右侧白雾状） |
| identity drift / 双足吸引子 / style drift / 故事段语义拉力（stale-prompt 型） | **0** | —— |

**零语义拉力归因**：本战役 prompt 全部新鲜重建——v1g 的「葡萄串入镜」类污染没有再发生（mushroom 入镜是剧本经 prompt 正确到达）。

## 10.C Calibration effectiveness

- **MINOR→transition：5/5 全部救回**。N1/N2/N3/N5/N7 的偏离全部被下一镜**预编剧首句**自然吸收（「忽然停下」「后退半步蹲坐」等）——**零文字修改、零加戏**。冻结预编剧自带过渡句的设计使 minor 校准成本=0。
- **MAJOR→retry：2/2 单次重试解决**（N6、N9），未触发 insert_story_beat（「情节不乱加」纪律下无此需求）。
- 用户两条纪律的实证结果：盯尾帧偏离（3 处实质偏离全部当场暴露）+ 情节不乱加（全程 0 情节修改）。

## 10.D Retry vs Adapt（从真实数据归纳，不写死）

| 判据 | 处置 | 实证 |
|---|---|---|
| 叙事/情绪与剧本相反（开心 vs 惊恐；醒 vs 睡） | **retry** | N6-a1、N9-a1——单次重试同时修复机位+情绪+地理（MAE 48.3→41.5） |
| 叙事命中但机位偏（吸引子） | **可 accept+adapt** | N4（用户裁定）；特写→大远景标准切可用 |
| 叙事命中但动作端点未落尾帧 | **accept+adapt** | N2、N5（下一镜首句吸收） |
| 接触精度极限（多体接触） | **accept+adapt** | N7（蝶落蕨叶 vs 尾尖） |
| 道具链继承 | 难以重试消除（参考本身携带）；读作叙事元素 | N9 |

**Vision 可靠性边界（重要）**：3 处 vision-human 分歧——N2（vision none/人工 minor：过宽）、N4（vision major/人工 minor：机位过敏）、**N6-a1（vision none/人工 major：误报，描述了不存在的画面）**。结论：Vision 是证据不是裁定；**人工必须看图**；vision 误报在情绪/地理类偏离上风险最高。

## 14. 时长变量（§7）

8s（N1/N5/N6/N8）全部一次通过；10s（N2/N4/N7）动作弧完整、无多余动作、尾帧可控（N4 机位漂移非时长问题）；5s（N3/N9）静态镜尾帧稳定（N9 需重试系叙事态而非时长）。当前数据支持：**静态/反应镜 5s；动态镜 8s；高运动/复杂交互 10s**——与 36.md §1.2.4「动作镜给足时长让尾帧落稳定态」一致，无一镜出现「动作中间被截断」。

## 15. 链根与故事段（§8/§9）

- chain_root_reference_id=`ref_d27e1a01c36f`（2D 雨林 walk_side，三重锚定）——全片 2D 风格零漂移、狐狸身份零漂移（9 镜一致），链根原则再次实证。
- 新故事段=正式语义边界：s09/s10 各建 SceneInstance+SIV（空板已批）+binding；跨段接力（N4→N5）成立。
- `new_story_segment_required`：本轮未触发（单故事内完成）。

## 16. Provider evidence（§13/§17）

provider_id=agnes，provider_model=agnes-video-v2.0，全部经 JobQueue 串行（1 RPM）；11 次视频生成（9 镜+2 重试）零 provider 失败、零 429；单镜端到端 ≈4 分钟（提交+轮询+末帧提取+Pages）。全部为 real E2E 真实生成；Vision 评估 model=agnes-2.5-flash（11 次，1 次解析 repair）。图片配额：SIV 清图 ~40 张+首末帧，Token Plan 内充裕；视频配额 ~90s 消耗。

## 17. 发现的新 Agnes 行为（本轮新增）

1. **尾帧接触类动作端点系统性缺席**：轻交互（触/饮）动作在尾帧上退为「邻近完成态」——模型避免物理接触瞬间作为静帧。
2. **多体接触精度极限**：蝶落尾尖毛→蝶落尾旁蕨叶（邻近接触）；回头/定格控制本身可靠。
3. **特写吸引子在复杂运动镜后发作**（N4/N6）：动作完成后模型倾向推近主角脸部作为收束。
4. **参考链道具继承不可靠消除**：N9 两次生成均继承蝴蝶红花——重试不能去除参考中显著存在的道具。
5. **段边界环境切换可靠**：N5 一次性兑现新 LocationView+SceneInstance 状态（空地/水塘/午后光）。
6. Vision（agnes-2.5-flash）在情绪/地理类偏离上会**误报 none**——不可作为唯一裁定者。

## 18. 系统问题 vs 模型问题（分开列）

**系统（已修/已证）**：prompt 静默污染（修复+实战验证）、REST PUT 绕过 invalidation（修复）、sig 死脚手架（接线）、SIV fauna 根因（identity_description 字段错位，修复）、基础 View 指纹失效需 revoke+re-approve（已处理，工具面待正式化）、末帧提取长进程退化（重启+诊断加固）、vision none 误报（流程已按「人工必看图」执行）。
**模型（属 Agnes 能力边界，按 §10.D 策略对冲）**：特写吸引子、接触端点缺席、多体接触精度、道具链继承。

## 19. 已有重复证据可固化的规则 / 20. 仍不能固化的

**可固化**（≥2 次独立证据）：尾帧接力衔接（全链 MAE 同刻度可比+零跳变叙事断点）；链根三重锚定风格/身份零漂移（v1g+本战役）；预编剧首句吸收 minor 偏差（5/5）；叙事错→单次重试有效（2/2）；静态 5s/动态 8s/复杂 10s 时长配方（9/9 无截断）。
**不能固化**：特写吸引子的触发条件（N4 发作 N8 不发作）；vision 误报的判别规则（需更多样本）；道具继承的消除方法（重试无效）；multi 模式 prompt 语言配方（本轮全程 LLM 重建英文 prompt，纯字段编译的 multi 从未测试）。

## 21. Phase 37B Multi-Provider 所需后端接口建议

1. provider 数据已预留：generation record 保存 provider_id/model/mode/timing（trace link 已含；建议 VideoVersion 增 provider_id 字段与 latency_s）。
2. 切换面：`image_provider` 覆盖先例（scene 系统 sensenova）可复制到 video provider（config 层 provider 注册 + adapter 路由）；生成入口已 provider-neutral（GenerationRequest/BoundVideoRequest 编译层，31.md seam）。
3. 需要：provider 能力描述表（mode 支持/时长上限/seed 范围/参考槽数）、per-provider 重试策略、trace 的 provider 维度对比视图。

## 22. Phase 37C Agent Shadow Replay 输入完整性

**已完整**：story_plan（冻结全文+expected tails+story_type）、每镜 prompt_hash+snapshot、reference 类型（链根/relay）、vision_assessment 全维、human 三档+reason+action、retry/adapt+判据原因、shot_patch（本轮全空=零修改链）、next_shot_id+result_after_correction、vision-human 分歧 3 例。`AgentCalibrationInput` 可直接构造；`AgentDecision vs HumanDecision` 可逐镜 replay（含 2 个 retry 决策点与 5 个 minor 吸收点）。

## 23. 完整回归

BLOCKER 修复后全量：**2319 passed / 0 failed（15m41s）**，含新增 test_prompt_freshness_37（17）、test_job_queue_errors_37（4）、test_calibration_trace_37（6）。战役后未再改 src/ 核心（仅脚本/媒体/报告），无需重跑。

## 24. 验收回答（37.md §19）

> 我们是否终于拥有足够真实、结构化、可 replay 的数据，知道 Agnes 在连续完整故事里究竟会怎么偏离，以及我们的校准方法究竟是否真的有效？

**是**——9 镜完整故事 11 次真实生成，失败分类学 6 类实证（吸引子/端点缺席/情绪反转/道具继承/伪影/零语义拉力），校准协议 5/5 minor 吸收 + 2/2 retry 修复，全程零加戏零剧本篡改，全部结构化在档可 replay。方法有效性成立；剩余不确定性（吸引子触发条件、vision 误报判别）已列入 §20 待验清单，建议经 stress cases 批次继续收集（37.md §5 正路）。

## 25. STOP（37.md §11/§20）

第一轮完整故事完成并形成本报告——**STOP**。不自动开始第二个故事、不接免费 Agnes API、不实现 Provider Router、不优化 Producer/Director Agent、不开前端面板。stress cases 批次与第二轮故事待用户指令。
