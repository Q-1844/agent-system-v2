# Phase 47 测试证据索引（48 情景矩阵）

> 日期：2026-10-04 · 分支 `feat/agent-v2-phase-d-golden-run`
> 状态口径：PASS=真实证据；FAIL=失败待修；PENDING=尚未执行/等待用户；BLOCKED_OK=预期阻塞。
> 本文件随批推进更新；最终结论以 `PHASE47_WORKFLOW_IMPLEMENTATION_REPORT.md` 为准。
>
> **Phase48 A批更新（2026-10-06）**：依 `PHASE48_A_BASELINE_AND_MINIMAL_REPAIR.md` A02/A03/A04
> 修正下列行——P47 16/21/22/23/24/25/42/44/45/46（D/F 批真实证据落账）、P47 26（A11 演练闭合）、
> P47 37（A10 真机直接演练闭合）。原始行文以 git 历史（d985783/e52d615）为准；每行标注证据类别
> （direct_runtime=真机直接 / mechanism_test=机制回归 / equivalent_scenario=等价路径）。
> 量化统计新增 §5（可重算账本：`PHASE48_A_CANDIDATE_LEDGER.json`）。

## 1. 证据类型说明

| 标记 | 含义 |
| --- | --- |
| [单测] | 零 LLM 引擎/语义单测（`tests/test_p47_workflow_semantics.py` 17 用例 + `tests/test_p46_workflow_engine.py` 18 用例） |
| [真机] | 真实后端 + 真实模型/图像/视频（探针脚本 `_p47_probe_*.py`，结果 JSON+日志在工作区） |
| [浏览器] | 前端页面走查（截图） |
| [回归] | 全量 pytest |

## 2. 情景矩阵

### 9.1 定义与工作流入口 8 项

| 编号 | 情景 | 状态 | 证据 |
| --- | --- | --- | --- |
| P47 01 | W00-W09 从页面/模板 API 创建 | 部分 PASS | [真机] `_p47_probe_c_stdout4.log`：W00/W01/W02/W03/W04/W05 六个模板经 `POST /workflow/templates/{tid}/create` 真实保存（revision 1，validation ok）；来源模板版本 `p47_templates_v1` 记录于 definition.template；W06-W08 模板已创建+校验通过（单测 test_templates_all_validate_and_compile），真机运行证据待补 |
| P47 02 | 复制模板并改目标 | PASS | [真机] 同一项目多次模板创建产生多个独立 workflowId（wf_w00_af4cd6 / wf_w00_2ed015 / wf_w00_2334d0…），互不影响；definition.template 保留来源 |
| P47 03 | 六种 Agent 选择与真实目录 | PASS | [真机] `GET /workflow/catalog` 六员全量（工具面 24/16/20/22/32/18）+ registryDigest；[单测] validate AGENT_UNKNOWN/AGENT_DISABLED |
| P47 04 | 两种 Skill 绑定 | PASS | [单测] W05/W09→full-story-relay-calibration、W07→keyframes-bridge-repair 编译进 plan.skills；非法绑定 BIND_NOT_APPLICABLE/BIND_AGENT_UNSUPPORTED 校验（validate.py）；[真机] W05 定义含 n_skill_0+eb 边 |
| P47 05 | 连续两次审核 | PASS | [单测] test_p46 test_two_consecutive_reviews_first_only（二审独立等待，意见不覆盖）；[真机] P46_F_DualReview 切片 + W09 模板 7 道审核定义 |
| P47 06 | director 两个节点及其他重复实例 | PASS | [单测] W09 编译 order 含 n_dir_board+n_dir_video 两个 director 实例、分别派发；[真机] P46 探针 director 与 W04 分镜/视频链分别派发 |
| P47 07 | 错接/环/分叉/孤立/草稿缺目标 | PASS | [单测] test_p46 test_validate_rejects_cycle_branch_merge_isolated 等（环/分叉/汇合/孤立/Skill 端口/缺目标） |
| P47 08 | 保存冲突及双击启动 | PASS | [单测] revision 冲突 409、幂等键同参复用/异参 409；[真机] C 探针多次 409 重存路径（save_def 函数真实走到） |

### 9.2 六员和完成语义 10 项

| 编号 | 情景 | 状态 | 证据 |
| --- | --- | --- | --- |
| P47 09 | producer 计划正常完成 | PASS | [真机] C 探针 W00 run_a1a30e4c581a completed（模板创建→运行→审核通过→完成；无越权委派） |
| P47 10 | 编剧真实新写与修订 | PASS | [真机] B 探针 S0 run_0db838c43200 completed（2 场景 created+hash）；C 探针 W01 run_12faea654aef completed |
| P47 11 | 新角色与 Look 初次生产 | PASS | [真机] B 探针 S1（角色三视图 54.2s 真实生成+Look 参考图真实生成，run_bfd46af65322 图像部分 PASS）；C 探针 W02 run_43f241b4c3aa completed（模板链：生成→审核→批准选用） |
| P47 12 | 已批准 Look 新视角/造型 | PASS | [真机] B2 探针 S2b（新造型「雪天信使」真实出图，原版 reference_asset_ids 原样保留） |
| P47 13 | 场景与环境视图 | PASS | [真机] W03 首跑暴露 outputs.py bug（location_views 字段名）→修复；恢复轮 run（C2）PASS：2 Location + 3 视图真实图像可读（复用核验 fileOk=true）；干净新项目 require_new 版随 D 批验证 |
| P47 14 | 分镜图与绑定 | PASS | [真机] 首跑 run_c1ff3cbabe38：3 镜+绑定 created；图像生成后 withImage 曾误报缺失 → 定位 outputs.py `_file_ok` 路径基准 bug（repo 根相对 vs 项目相对）→修复；恢复轮 run_2fe33eae9111 completed：3 镜 outputs_verified（reused ×3, fileOk=true） |
| P47 15 | 导演真视频生产 | PASS | [真机] C 探针 W05 run_ff815a61a1dc completed：真实视频 vv_2855ff25（5s/720p/i2v）生成+选片指针落盘；provenance=生成依据+首中尾帧提取+visual.review_shot pass（12 维度） |
| P47 16 | assembly 真成片 | PASS | [真机][direct_runtime]（A批更新）D 批《风后邮站》run_9cc683f2ce6c + F1《山铃回声》+ F2《夜邮灯塔》+ F3《山雀夜航》：真实 Sequence+TTS（narration 落盘）+音乐+混音 render；四片 render 文件在盘（srender_ea44c7a9/cf03b1ff/c57fb8be/8fb05fe6，h264 1280×704+aac，6.0–7.7MB）。成片审定（n_rv_final）四轮均 waiting_human=保留用户 |
| P47 17 | 合法复用与混合补缺 | PASS | [真机] B 探针 S3（故事复用 reusedRefs+服务端核验）、S5（复用合格 Look+补缺新 Look，outputs 同时含 created+reused） |
| P47 18 | 无关域写入/旧素材冒充/假完成 | PASS | [真机] B 探针 S4（require_new+「不写就复用」目标 → 无回执 → 节点失败不推进）；[单测] test_require_new_rejects_existing_entity |

### 9.3 Skill 和视频方法 8 项

| 编号 | 情景 | 状态 | 证据 |
| --- | --- | --- | --- |
| P47 19 | 尾帧接力校准实际加载 | PASS | [真机] W05 run_ff815a61a1dc：plan.skills 含 relay@1.3.1；run 快照 skillVersions.contentHash=sha256:4c529461af9e20ada9b8b80b；attempt.skillEvidence 真实 load 事件（skill_id=full-story-relay-calibration, ok=true） |
| P47 20 | 组内连续动作 | PASS | [真机] W05 运行中 calibration.trace_upsert 落档 + visual.sample_frames（severe=0, suspected=1 纸张过渡压缩如实报告）+ visual.review_shot pass；W09 链视频节点同样绑定（D 批证据追加） |
| P47 21 | 远景转近景/身份风险 | NOT_EXECUTED | [direct_runtime 边界]（A批更新）D/F 四轮导演均自主判定 i2v independent+正常切镜（`source_shot_id` 全 null）——无接力链运行，该专项情景未发生；relay Skill 绑定+load+组内帧检证据见 P47 19/20。不拿"整链成功"冒充接力质量 |
| P47 22 | 新故事段正常切镜 | PASS（链级） | [direct_runtime]（A批更新）D/F 四轮 6 镜链全部以独立 i2v+intentional hard cut 产出可审成片（衔接由装配侧保证）；桥接 Skill 未触发（双端证据门槛未满足）——与 §13.2 用户调研一致，定调不强推接力 |
| P47 23 | 修复错误根帧或视频 | PASS | [真机][direct_runtime]（A批更新）C3 W06 run_7f30add8a220：问题候选 vv_2855ff25（suspected=1 纸张过渡压缩）→ 新修复候选 vv_47dd2ea1 真实生成（job 59f6895c1a56，2 版本，原版保留）；outputs_verified |
| P47 24 | 合格端点 keyframes 桥接 | PASS | [真机][direct_runtime]（A批更新）C3 W07 run_10e21ba7f1cb：keyframes 桥真实生成（vv_53d832b2，双端 s01_c02→s02_c01）+ bridge Skill 真实 load（skillEvidence hint+load 事件） |
| P47 25 | 无效端点和无需桥接负例 | NOT_EXECUTED | [mechanism_test]（A批更新）负例判定语义内置于 W07 objective（"正常切镜判为不需要桥接，无效端点先修端点"）+ D/F 四轮自主未触发桥（真机）；专门负例夹具未跑——如实保留 NOT_EXECUTED |
| P47 26 | Skill 内容变化及非法 provider 参数 | PASS | [单测] 非法 completion/selection 配置 fail closed；[真机][direct_runtime]（A批更新）A11 演练 `_p48_a11_skill_snapshot_drill.py`：隔离 registry 内容更新后，旧 run 冻结原 contentHash 不改写（磁盘重读一致）、新 run 读新 hash（≠旧且=当前包），F3 快照 hash 与现行正式 relay 包一致（冻结后零漂移） |

### 9.4 审核与交接 8 项

| 编号 | 情景 | 状态 | 证据 |
| --- | --- | --- | --- |
| P47 27 | 只作确认的审核 | PASS | [真机] C 探针 W00/W01 审核 approve（无 selection 配置→不动任何 selected/final 指针）；[单测] selection 缺省 none |
| P47 28 | 下游需要正式批准/选片 | PASS | [真机] B2 S1b（look_reference 批准→approved_reference_asset_ids 真实落盘）+ C 探针 W02（selectionState=done）+ W05（video_version final_video 指针） |
| P47 29 | 选片成功/审核响应不明对账 | PASS | [单测] test_review_selection_failure_then_recovery（决策落账→选用失败 needs_reconciliation→continue 幂等重试→成功）；两步独立落账（decisionReceipt/selectionReceipt） |
| P47 30 | 一审过后二审未决定 | PASS | [单测] test_two_consecutive_reviews_first_only（独立等待、下游零派发） |
| P47 31 | 一审/二审驳回 | PASS | [单测] test_review_reject_stops_downstream；[真机] P46 双审核切片驳回路径 |
| P47 32 | 待审对象被外部修改 | PASS | [单测] test_review_content_change_rejects_stale_decision（内容快照 hash 复核→409 review_object_changed） |
| P47 33 | 选片正式切换/candidate 未选 | PASS | [真机] W05（切换后 final_video 指针真实变更；未选版本保留为候选）；[单测] video_version reviewState selected/candidate 推导 |
| P47 34 | 审核卡片和浏览器刷新 | PASS | [浏览器] IAB 走查（截图存证）：模板弹层 10 模板 → 创建载入画布（已保存）→ 运行观察（派发中/运行中实时状态）→ 运行中刷新恢复（面板从后端找回 run）→ 审核卡片通过 → 选片卡片（候选单选+当前已选标记+通过并选用→final_video 指针真实变更 vv_47dd2ea1→vv_2855ff25）→ 失败诚实显示 |

### 9.5 恢复与稳定性 8 项

| 编号 | 情景 | 状态 | 证据 |
| --- | --- | --- | --- |
| P47 35 | 派发前窗口杀断重启 | PASS | [真机] R1（run_0a51b844b40d）：派发意图无任务关联→诚实 needs_reconciliation→旧 attempt 保留→显式 continue 新 attempt 真实执行（内容失败=注入目标无产出，恢复语义完整验证） |
| P47 36 | turn 已关联后杀断 | PASS | [真机] R2 定向（run_31a0879f270c）：运行态遗留 task→needs_reconciliation（错误含 task id，不盲重发）→continue 新 attempt completed；首轮演练暴露注入夹具绕过 runs_index→夹具修复后复验通过 |
| P47 37 | 真视频 ID 后杀断轮询 | PASS | [机制] JobQueue `recover_pending_videos` 按 provider ID 续轮询不重提交（既有机制+历史回归）；[真机][direct_runtime]（A批更新）A10 直接演练 `_p48_a10_stepA/B_*.py`：隔离项目真实提交 t2v 4s（provider job task_SMMqkUvC0F6Qa6BNX6NjThqCgbYPhaNx 持久化）→ 硬杀进程（taskkill PID 36940）→ 恢复进程查询同一 job、provider create_video 调用数=0（防重提交哨兵计数）、真实媒体落盘（4.0s/720P，sha256=1fa2e153…74cc2a5，1369434B）、wall 193.7s |
| P47 38 | 人审等待杀断重启 | PASS | [真机] R4（run_38300846d218）：waiting_human 杀断重启→review/材料/意见原样保留→通过后下游**恰好一次**派发（downstream_attempts=1）；下游内容失败=只读目标无写入（诚实），恢复语义完整验证 |
| P47 39 | 审核后派发窗口杀断 | PASS（等价路径） | [机制] 决策落盘与派发解耦（decide→状态落地；drive 由路由层触发）；R4 覆盖「审核等待跨重启→通过→下游唯一派发」全链；派发窗口 attempt 先落盘语义与 R1 同一机制（R1 已证）。（A批更新注：派发窗口的专门直接演练仍未单独执行，保持等价路径等级不升格） |
| P47 40 | 停止后真结果晚到 | PASS（带边界记录） | [真机] R7（run_b03181397c8b）：停止后 run 保持终态、下游 pending 不派发、指针零变更 ✓；边界如实记录：运行中停止的 turn 以 failed（而非 stopped）标签落账（语义瑕疵，机制保证不破坏）——**A批已修**（producer settle cancelled + executor 对账分支，tests/test_p48_a_repairs.py），Phase48 A06 |
| P47 41 | 提交结果不明与重复事件 | PASS | [真机] R6：同幂等键双启动→同 run（reused=true）；[单测] 重复决策 alreadyDecided；[真机] R2 对账幂等（遗留 task 只结算一次） |
| P47 42 | 冻结后三轮 F1-F3 | PASS（工程链；媒体待用户评审） | [真机][direct_runtime]（A批更新）F1 run_181eff42300e（风铃山道）/ F2 run_13e741c1c934（夜邮刺猬）/ F3 run_fb5e2c27eac8（山雀夜航，复用轮含真实中断+对账续跑）六员全 success、停靠成片审定；render 全部落盘。四轮 run 状态、节点、审核、媒体由 A批 A01 逐项核对（PHASE48_A_CANDIDATE_LEDGER.json）。用户审美验收与 final 定版：F1/F2/F3 序列层 final_render 已于 10-05 16:56—17:00 经用户 REST 入口选中（晚于主报告）；run 层成片审定仍 pending |

### 9.6 音频和工程检查 6 项

| 编号 | 情景 | 状态 | 证据 |
| --- | --- | --- | --- |
| P47 43 | 全部文本出站 thinking | PASS | [真机] 全部探针 run snapshot.thinking=true、attempt.thinking=true；backend_runtime.log 逐请求 thinking=True（P46 机制沿用）；本轮各 run 快照记录于 results JSON；（A批追加）`_p48_a_outbound_thinking_probe.py` 抓包级核验：出站请求体实际含 `chat_template_kwargs.enable_thinking=true`，响应含 reasoning_content（`_p48_a_outbound_thinking_result.json`） |
| P47 44 | MiniMax 旁白响应和素材保存 | PASS | [真机][direct_runtime]（A批更新）D/F 四轮旁白 TTS 真实落盘（output/audio_imports/narration/*.wav + narration_audio.json 回执；provider=minimax / speech-2.8-turbo；D 批 6 句、F3 7 句）；账本含逐片 cues 数（PHASE48_A_CANDIDATE_LEDGER.json） |
| P47 45 | 旁白/音乐/原视频音频政策 | PASS（当时纪律） | [direct_runtime]（A批更新）四片按 Phase47 纪律执行：TTS 旁白+外部配乐+原视频音轨静音；源视频音轨实测 -52.9 LUFS 近似无声（主报告 §13.1）。用户反馈后政策修订（保留合格原生音效、禁旁白）归 Phase48 B 批执行——本轮不回溯改旧片 |
| P47 46 | 不同规格视频装配与导出 | PARTIAL | [direct_runtime]（A批更新）D/F render 真实导出（h264 1280×704+aac）；全部输入为同规格 720P——混合规格输入装配未专项测试，如实保留 PARTIAL |
| P47 47 | 前端类型检查和构建 | PASS | [回归] vue-tsc workflow 组件 0 错误（历史遗留错误不属本轮，如实记录）；vite build 成功（42.46s） |
| P47 48 | 全量回归及历史测试映射 | PASS | [回归] `pytest tests/ -q`：2655 passed, 2 failed（test_ai_7ch_cancel_race::test_case1_stale_object_resurrection、test_autopilot::test_start_locked_without_final_400——全量负载下偶发，隔离复验均通过，与本轮改动无关） |

## 3. 环境事件记录（真实排障）

| 时间 | 事件 | 处置 |
| --- | --- | --- |
| 10-04 下午 | `api.agnes-ai.cn` DNS 被本地路由污染（192.168.0.1 → 141.193.154.50，Meta 段；公共 DoH 223.5.5.5/119.29.29.29 一致确认真实 IP=111.7.87.222） | 写入 Windows hosts（`111.7.87.222 api.agnes-ai.cn`，管理员提权）；config base_url 保持 api.agnes-ai.cn 不变；apihub.agnes-ai.cn 虽可达但拒绝 api 入口 token（入口隔离），不采用 |
| 10-04 | C 探针首轮 W00 provider 400（tool call arguments 非法 JSON） | provider 瞬时错误（§11.2 第 5 层），重跑通过 |
| 10-04 | W08 首跑：装配读 44A 全局快照（跨项目对齐 fail-closed，诚实阻塞+诊断提案 proposal_v1.md） | 根因=快照加载无项目作用域 → 修复：snapshot_load 项目优先 + 新增 assembly.snapshot_freeze；重跑真实走通 |
| 10-04 | W08 r3：渲染失败 audio_source_missing（agent 音频计划引用不存在的 silent_placeholder.wav）+ 任务被 2s settlement 轮询误判"重启中断"（registry 释放与落账间竞态） | 前者修 objective 指令（无占位音频）；后者修 settlement boot 守卫（本 boot 任务不判中断） |
| 10-05 | W06 修复链 PASS（s01_c01 疑似问题→新修复候选，2 版本）；W07 桥接链 PASS（keyframes 桥真实生成） | C 批 W06/W07 证据闭合 |

## 4. 真机 run 索引（隔离项目）

| 项目 | run | 情景 | 结果 |
| --- | --- | --- | --- |
| P47_B_Probe | run_0db838c43200 | S0 编剧新写（require_new×2 场景） | completed |
| P47_B_Probe | run_d517d1454dba | S3 故事合法复用（reusedRefs） | completed |
| P47_B_Probe | run_5162feddd505 | S4 冒充负例（应失败） | failed（预期） |
| P47_B_Probe | run_bfd46af65322 | S1 角色三视图+Look 图（首次跑出 profile 缺前置→补 profile 后图像 PASS） | needs_reconciliation（选用 bug 已修） |
| P47_B_Probe | run_4eae8db12bec | S5 混合复用+补缺 | completed |
| P47_B_Probe | （B2 轮） | S1b 审核「通过并选用」Look 参考 | PASS（approved_reference_asset_ids 落盘） |
| P47_B_Probe | （B2 轮） | S2b 新造型「雪天信使」版本化 | PASS（原版保留） |
| P47_C_Templates | run_12faea654aef | W01 模板链 | completed |
| P47_C_Templates | run_a1a30e4c581a | W00 模板链 | completed |
| P47_C_Templates | run_43f241b4c3aa | W02 模板链（真实图像+批准选用） | completed |
| P47_C_Templates | run_c1ff3cbabe38 | W04 分镜（图像未完成→诚实拦截） | failed（正确行为） |
| P47_C_Templates | run_ff815a61a1dc | W05 视频（relay Skill 真实 load+视频生成+选片） | completed |
| P47_C_Templates | run_2fe33eae9111 | W04 分镜恢复（_file_ok 修复后 reused×3 fileOk） | completed |
| P47_C_Templates | （C3） | W06 修复（原版+修复候选=2 版本） | completed |
| P47_C_Templates | （C3） | W07 桥接（keyframes 桥真实生成） | completed |
| P47_C_Templates | run_a34b64fad732 | W08 装配（快照冻结→对齐门通过→render 完成文件落盘→审核通过） | completed |
| P47_C_Templates | run_9c30218aa00b | 浏览器选片走查（候选单选→通过并选用→指针变更） | completed |
| 风后邮站 | run_22b54d2261b9 | D 批 W09 首驱动（n_dir_board 失败，诚实失败历史保留） | failed |
| 风后邮站 | run_9cc683f2ce6c | D 批 W09 六员完整生产（终态 run） | waiting_human（成片审定） |
| 风铃山道 | run_181eff42300e | F1 复验 W09 | waiting_human（成片审定） |
| 夜邮刺猬 | run_13e741c1c934 | F2 复验 W09 | waiting_human（成片审定） |
| 山雀夜航 | run_fb5e2c27eac8 | F3 复验 W09（复用轮+真实中断演练） | waiting_human（成片审定） |

## 5. 量化统计（Phase48 A批补齐；可重算）

> 主报告 §7 曾引用本节，原文件缺失——现按真实文件补齐。原始记录见
> `PHASE48_A_CANDIDATE_LEDGER.json`（只读 project.json/sequences.json/workflow runs/媒体 hash 生成）。

| 指标 | 值 | 口径 |
| --- | --- | --- |
| 四部整链影片镜头 | 24 镜（6×4） | project.json 实际 shots |
| 视频版本候选 | 40 条（含 C3 夹具 4 条另计） | project.json video_versions 实数；含诚实失败重试的历史版本，全部保留 |
| 已选 final_video | 24/24 镜 | 视频二审「通过并选用」多选用 items 落账 |
| 首次合格率 | **不计** | 逐版本 visual.review_shot 明细未随版本持久化（在 attempt 事件流内），首次候选×合格的联合分母不可重算——按 A03 纪律标 unknown，不从成片数倒推 |
| attempt 级 verdict | 可重算 | 账本含全部 agent 节点 attempts 的 outputsVerdict（code/created/reused/error） |
| render | 4 片 4 render 全 done、文件在盘 | sequences.json + ffprobe/磁盘实证 |
| 用户定版 | D 片 final_render=None；F1/F2/F3 于 10-05 16:56—17:00 用户 REST 选中 | run 层成片审定四轮 pending |

**未选候选 ≠ 用户认可**：40 条候选中仅 24 条经二审选用；其余 16 条保留为候选历史（含 D 批 5 次 run、F2 4 次 run 中诚实失败驱动的重生成），不计入任何合格率。
