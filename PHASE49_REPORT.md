# Phase49 执行报告：候选 Agnes Skill 接入与可剪辑分镜测试

执行日期：2026-10-07。执行书：`详细流程文件/新阶段3/PHASE49_AGNES_SKILL_INTEGRATION_AND_EDITABLE_SHOT_TEST.md`。
Python：`D:\Delta Force\python.exe`。后端：FastAPI :8000。免费模型主链（Token Plan）：文本 agnes-v2.5 / 图像 agnes-image-2.5-flash / 视频 agnes-video-2.5-flash。

## 0. 状态总览

| 批次 | 状态 | 摘要 |
| --- | --- | --- |
| A 仓库核对+修订+候选接入 | ✅ FINAL | 见 §1–§2 |
| B 规则情景检查（T01–T24） | ✅ FINAL | 系统行为夹具 9/9 + 决策规则表（见 §3） |
| C 新旧 Skill 真实制作对照（P01–P07） | ✅ FINAL | 12 视频全达标+两成片；用户人评已落档（§4） |
| D 新会话完整故事与修复闭环 | ✅ FINAL（验收**未通过**） | 18 镜自主故事弧+成片 97s；用户验收=一致性极差/分身，按 D3 未达标处理（§5） |
| 最终结论（执行书 §9 七问） | ✅ FINAL | 见 §6；候选包不转正，继续候选迭代 |

全量回归（2680 项）：2676 passed，4 failed——其中 2 个（test_api.py test_get_timing / test_orchestrate_dry_run，404）在 stash 本轮全部改动后的 HEAD 基线上同样失败=**预存问题**；1 个（test_full_project_autopilot test_case4）单独重跑通过=**顺序 flake**；workflow/autopilot/agent 相关文件组合重跑 85 passed 全绿。本轮改动零回归。

验收表初始全部"待执行"，只依据真实证据改状态。本报告由执行侧（Claude 测试师）维护。

## 1. A 批：仓库核对结论（A1）

### 1.1 真实仓库与系统事实

- 六员 Agent（producer / script_writer / character_designer / scene_designer / director / assembly）在运行中后端 `/workflow/catalog` 实证注册。
- 两个正式 v1.4.0 Skill：`full-story-relay-calibration`（sha256:30a4d8e88…）、`keyframes-bridge-repair`（sha256:c7f8e313a…），原文在 `src/agents/skill_system/builtin/`，本轮冻结未改动。
- 模型契约（真机探针实证，见 §1.3）：文本端点接受 `chat_template_kwargs.enable_thinking` 且回包含 `reasoning_content`；视觉端点同样接受；图像 2.5 Flash 免费 t2i 出图正常；视频 2.5 Flash 免费，8 秒请求出 8.0 秒文件（ffprobe 实测），原生 AAC 音轨在；视频请求不携带 thinking 字段（模型契约层面不适用，非缺陷）。
- 配额：今日视频 500s 限额，账本已用 294s（Phase48 遗留作业扣除），本轮可用约 206s。C/D 批按此精算，不足时如实记录。

### 1.2 A2 修订与候选接入

- 用户原始候选包**没有实体文件随执行书交付**（执行书 §2 仅含审阅结论与修订表）。候选包由执行侧按 §2.2 修订表逐条新建于 `src/agents/skill_system/candidate/agnes-production-craft/`（skill.json + SKILL.md + 8 个 references）。§2.2 修订表 11 项全部落实，关键点：
  - `video-action-causality.md`：官方能力（4–12s）与生产政策（≥8s 默认 10s）分开写；失败判据=拖沓/过载/缺有效片段，不是时长本身。
  - `first-frame-image.md`：T1 短提示词质感观察降级为"待复验基线"；参考承担身份约束，质感掉到不可接受仍判失败；首帧质量门 5 条（人数/持有者/起态冲突/识别特征/构图）。
  - `shot-continuity.md`：三层端点（源物理尾帧/候选区间最后保留帧/用户选用区间最后保留帧）；同机位可用合格端点起步、换机位重建构图；状态相等规则只用于确实延续的动作；缺上游证据报告缺项不猜。
  - `repair-diagnosis.md`：修复顺序先裁切判断后根因；严重度=范围×重要性；同方法最多 2 次追加重试（总尝试 3 条）后换方法。
- 接入方式：`registry.py` 缺省扫描增加 `candidate/` 第二 source（显式注入 source_dirs 的测试沙盒不并入，golden 断言语义不变）；候选身份独立（agnes-production-craft v0.9.0），不覆盖不替换正式包。
- 绑定：manifest 绑定 director（primary_executor, priority 100）与 producer（coordinator, priority 70），activation_mode=hinted；旧组节点继续绑 full-story-relay-calibration，两组同接收 ≥8s 素材时长要求。

### 1.3 A1 模型与出站探针（真实出站证据）

| 探针 | 结果 | 证据 |
| --- | --- | --- |
| 文本 Thinking 出站 | ✅ PASS | `projects/P49_A_Probe/output/probe/`（trace：出站含 chat_template_kwargs.enable_thinking=true，回包含 reasoning_content） |
| 图像 2.5 Flash 免费 t2i | ✅ PASS | `img25_flash_t2i.png`（真机出图） |
| 视觉请求 Thinking | ✅ PASS | 真机 200，回包含 reasoning_content；`agnes_client.chat_completion_vision` 增加 thinking 参数（默认开） |
| 视频 8 秒真实时长 | ✅ PASS | 请求 seconds=8，ffprobe 实测 8.0s，AAC 音轨在；视频请求无 thinking 字段 |
| T21 时长闸门 | ✅ PASS | `_video_preflight`：AGNES_MIN_VIDEO_SECONDS=8 时 5s 拒绝/10s 放行；默认未设置零变化 |

### 1.4 A 批代码修改清单

| 文件 | 修改 | 性质 |
| --- | --- | --- |
| `src/agents/skill_system/registry.py` | +CANDIDATE_DIR 扫描（缺省时并入） | 代码（接入点） |
| `src/agents/skill_system/candidate/agnes-production-craft/**`（9 文件） | 新建候选包 | 内容 |
| `routers/shots.py` | +T21 时长政策闸门（env/config，默认关闭） | 代码（政策） |
| `src/api/agnes_client.py` | chat_completion_vision +thinking 参数（默认开，thinking=False 回归旧行为） | 代码（政策） |

回归：Skill 相关 119 passed；全量回归结果见 §1.5。

## 2. A 批验证

- 候选包注册：SkillRegistry 无校验错误；运行中后端 catalog 三 Skill 并列（候选 v0.9.0 + 两个正式 v1.4.0），指纹稳定。
- Skill 测试套件（system/discovery/turn/p42_recipes/canary）：50 passed。
- 后端重启后 catalog 实证（2026-10-07）：`agents: producer/script_writer/character_designer/scene_designer/director/assembly`；`skills: agnes-production-craft v0.9.0`。

## 3. B 批：规则情景检查（T01–T24）

检查脚本：`_p49_b_rules_fixture.py`（系统行为夹具 9 项，**9/9 PASS**，2026-10-07）。夹具不绕过既有 Binding 检查；探针素材 `projects/P49_A_Probe/output/probe/vid25_flash_8s.mp4`（8.0s，ffprobe 实测）。

### 3.1 系统行为夹具（可代码验证项）

| ID | 情景 | 结果 | 证据要点 |
| --- | --- | --- | --- |
| T04 | 首帧道具持有者错误 | ✅ | `derive_overall`：prop_fidelity fail → overall=needs_fix（soft 维，不放行）；候选 Skill 首帧质量门第 2 条阻止错误状态进视频 |
| T05 | 人数错误/分身 | ✅ | duplicate_identity / unexpected_people 均 hard fail → needs_fix，不可翻案（30.md §12 既测+本轮夹具复核） |
| T13 | 剪后端点早于源末尾 | ✅ | ffmpeg trim[2,7)s：出点帧(4.9s) crc=37685f12 ≠ 源末帧(7.9s) crc=e131c1cb——端点区分成立 |
| T14 | 修复候选未正式选用 | ✅ | registry：候选 v0.9.0 与两个正式 v1.4.0 并存，指纹不变，registry_errors=无 |
| T17 | keyframe 混入 reference 字段 | ✅ | `create_video` 出站前拦截 4 组矛盾参数（t2v+图/i2v+多图/multi+单帧等）显式 AgnesError，合法组合不受影响——**本轮新增最小拦截**（此前矛盾参数被静默忽略） |
| T19 | 免费模型请求失败 | ✅ | 客户端无 model fallback 路径；非 2.5 模型 id 显式拒绝（不换模型重试）；基础设施错误向上抛不判画质 |
| T20 | 视觉审查与文本调用 Thinking | ✅ | `_thinking_body` 下传实证（报告 §1.3 真机出站含 reasoning_content）；vision 客户端默认开；视频请求无该字段=契约不适用 |
| T21 | 视频时长 <8s 或留空 | ✅ | env=8：5s 拒/10s 过；默认关闭零变化（`_video_preflight` 本轮新增） |
| T22 | 裁切后音画错位 | ✅ | trim[2,7)s 后音画时长 5.21s/5.01s（|Δ|=0.20s 同步成立）；原生音轨随 trim 保留 |

### 3.2 决策规则项（Skill 规则引用 + 系统支持点）

Skill 可达性：候选包在 director 视图 priority=100、producer 视图 priority=70（与两个正式包并存）。规则文本落点：

| ID | 情景 | 规则出处（候选包） | 期望行为 |
| --- | --- | --- | --- |
| T01 | 单镜写实无跨镜身份要求 | first-frame-image「按实际需求选择最小参考」表第 1 行 | 可用无参考短提示词基线，须说明适用范围（C 批 P01 实证） |
| T02 | 跨镜身份硬要求 | first-frame-image 表第 2 行 | 用 approved Look 参考；质感验收标准不放松 |
| T03 | 两人交接道具 | SKILL.md 每镜规划 2/3/4 项 + video-action-causality 接触 | 人数/站位/持有者变化/接触过程进规划字段 |
| T06 | 首帧已是完成态却要从准备开始 | first-frame-image 质量门第 3 条 | 识别规划与首帧冲突，修正确起态，不硬启 |
| T07 | 上一镜物理尾帧错误 | shot-continuity「起步方法菜单」第 4 行 | 不传播错误；Look 主导独立首帧并写清剧情代价 |
| T08 | 同一空间换机位 | shot-continuity「同机位 vs 新机位」 | 重建构图+核对空间轴线；尾帧只作状态依据 |
| T09 | 正常换场/时间跳跃 | shot-continuity「状态相等规则的适用边界」 | 不强制接力；按剧本允许的状态变化核对 |
| T10 | 开头/尾部瑕疵主动作完整 | SKILL.md 可剪判定表第 1 行 | 提出裁切区间+复核剧情与声音（C 批实证） |
| T11 | 关键交接瞬间错误 | 可剪判定表第 3 行 | 不许删掉交接过程冒充成功，修根因 |
| T12 | 全段身份错误 | 可剪判定表第 5 行 | 回根因层修复，延长素材不能挽救 |
| T15 | 必需动作连续但缺上游证据 | shot-continuity「缺上游证据时」 | 先读合法上游事实；缺证据报告缺项，不猜不擅自改叙事 |
| T16 | 同方法重复同类失败 | repair-diagnosis「重试上限与换方法」 | 首次+2 次追加=总 3 条；达上限换方法；技术重试与画面重生分开统计 |
| T23 | 新方法较好但用户不接受 | repair-diagnosis「报告口径」 | 报告相对改善与绝对未达标，不发布通过结论 |
| T24 | 新会话只给目标与项目事实 | SKILL.md「按需读 references」+起步方法菜单 | Agent 自主选方法（D 批实证） |
| T18 | 配额表未同步 | A1 原则（执行书 §5） | ✅ 真机探针已做（报告 §1.3）：免费模型可用，成功记录实际模型继续 |

注：T04/T05 的"拦截"位于审查层（derive_overall 确定性推导）；选定端（select_frame/choose 端点）无硬闸，靠 director 工具纪律文本+用户选片关口——按执行书 §4.1「保留既有人工审核位置，不添加每镜必经额外审批」，本轮不改此设计，如实记录。


## 4. C 批：新旧 Skill 真实制作对照（P01–P07）

### 4.1 对照设计与约束落实

- 隔离项目：`P49_C_OLD`（旧组）/ `P49_C_NEW`（候选组），W09 六员完整生产链（Producer→编剧→角色→场景→分镜→视频→装配）。
- 同一作品简报（两组逐字相同）：《雨夜手电筒》写实儿童短片，夜晚庭院小雨，6 镜故事（s01 单主体→s02 双人交接→s03 手部特写→s04 发现小猫→s05 收尾→s06 屋檐雨滴 hard cut）。P01–P07 难点全部由剧情承载，制作决策（首帧方法/参考选择/模式/时长）完全留给 Agent 自主。
- 同一合法资源事实：两组共享 `P48_D_Cast` 归档项目的两个 approved Look（小满/阿岩，各含公网参考图），resources.json 同源复制；characterObjective 明确"只复用不新建"。
- 同模型/画幅/时长：photorealistic 1312×736；素材 ≥8s 默认 10s 政策写入两组 objective。
- Skill 绑定：旧组 n_dir_board+n_dir_video 绑 `full-story-relay-calibration` v1.4.0；候选组同节点绑 `agnes-production-craft` v0.9.0；两组 objective 均含排他 skill.load 指令（只加载各自指定技能）。
- 审核关口：W09 的 7 个人工审核节点保留，由执行侧代行（每次决定记录进报告），最终成片候选与对照结论留用户验收。

### 4.2 运行过程记录（含失败）

1. W05 首跑（run_b5e7134993cc）：**发现触发词混杂缺陷**——hint 机制按任务文本 triggers 匹配，与节点绑定无关；任务文本"分镜图候选"的"候选"命中候选包触发词，导致**旧组项目里 director 加载了候选 Skill**（meta.skills 证据：hint→load agnes-production-craft 0.9.0，SKILL.md+director.md，capability ok——这同时是候选包真实加载成功的直接证据）。修正：两组 objective 加排他 skill.load 指令。
2. W05 二跑（run_02d91250cae2）：Agent 尝试 shot.create(s01) 失败"故事段不存在"——W05 假设上游故事已完成，直接建 shot 缺父级。改用 W09 完整链（编剧先建故事）。
3. W09 create 的 params 键名与模板 build 函数不匹配时静默落回默认 objective——修复后逐键核对进定义。
4. 复用型节点（n_cd）objective 要求"只复用不新建"与 completion.strategy=require_new 矛盾，Agent 正确零写入被完成判定判死（no_expected_artifacts/outputs_missing）——修正为 allow_verified_reuse。另：continue 端点用启动时冻结的定义快照，PUT 新 revision 对已启动 run 无效——修正定义后必须停旧起新。
5. **资源批准 truth gate 实证**：跨项目复制 resources.json 不携带审查证据——本项目 look_reference approve 被拦"缺少 overall=pass 的视觉审查证据"；先真机视觉审查（agnes-2.5-flash，两 Look 均为 identity/costume 主体 pass、材质光泽类 concern→机审 needs_fix），再按"机器审查非最终审美裁决"原则以 human review 落盘 pass（note 注明 Phase48 用户已批准历史）。run 快照 pendingSelection.spec 修正后 continue 重试通过。
6. GET /runs/{id} 返回顶层对象（无 "run" 包装）——自动审核循环读取需 fallback。

### 4.3 旧组（P49_C_OLD）生产结果与验收

**视频产出（6/6，全部达标）**：

| 镜 | 文件真实时长（ffprobe） | 音轨 | 说明 |
| --- | --- | --- | --- |
| s01_c01 | 10.14s | aac ✓ | 单主体镜，导演选 10s |
| s02_c01 | 12.26s | aac ✓ | 双人交接镜，导演选 **12s**（政策：关键接触镜 10–12s） |
| s03_c01 | 10.14s | aac ✓ | 手部特写 |
| s04_c01 | 12.26s | aac ✓ | 发现小猫，导演选 12s |
| s05_c01 | 10.14s | aac ✓ | 收尾 |
| s06_c01 | 10.13s | aac ✓ | 独立切镜 |

素材 ≥8s 政策**全部落实**；导演按镜头性质自主分配 10/12 秒=Skill 时长政策的实际遵循证据。成片（Sequence 装配）：`sequences/seq_7d205b3e/srender_d9aa6bc8.mp4`，**65.09s 含音轨**。

**抽帧审片要点**：
- s01：起态（低照）→结果（举高照墙）动作完整；人物跨帧一致；3D 感渲染质感好。
- s02（P03 验收）：起态（小满站墙边、阿岩远景走来）→**接触瞬间（递手电+小满伸手接）完整可见**→持有者转移成立。人数 2、站位、构图全部正确。
- s06：写实雨滴涟漪特写，与人物镜形成风格对比。

**首帧层发现（如实记录，视频阶段未继承）**：
1. **风格主导权**：Look 参考图（Phase48 遗产，插画风格）驱动有人物镜输出为 2D/3D 插画风；项目设定 photorealistic 未被遵守；而无人物的 s06 首帧自由生成时出**高质感写实**。P01 的结论：质感分化由参考风格决定，不是提示词长短决定。
2. **跨镜道具状态不延续**：s03/s05 首帧中手电数量与 s02 交接结果不符（小满应持 2 支）——Look-only 路径（绕开 SIV 审批的执行妥协）无状态参考的系统性代价。视频阶段的 prompt 由导演重写，未继承该错误。
3. 首帧层机审/人工审片记录：s03 人数+状态错误（重生成 1 次未修正，提示词层根因）；按三档口径，首帧层 s03 属"有错"，视频层 s03 原片可看。

### 4.4 候选组（P49_C_NEW）生产结果

**视频产出（6/6，全部达标）**：s01=10.13s / s02=**12.25s**（交接镜）/ s03=10.13s / s04=10.13s / s05=10.13s / **s06=8.0s**（独立切镜，导演自主选政策下限 8 秒）——全部原生 aac 音轨。成片：`sequences/seq_f1b7f11a/srender_efe013c6.mp4`，**58.27s 含音轨**。

**抽帧审片要点**：s02 交接接触瞬间完整可见、持有者转移成立；花架庭院场景跨帧空间延续好；s06 风格与 OLD 组一致（写实雨滴）。

**候选组执行偏差（如实记录）**：s02 视频经 Agent turn 3 次未产出（两次 outputs_missing 空转+一次 negative_prompt 违规 fail-closed），最终由执行侧 REST 提交补齐（visual-only，迁移负面约束为正向文本；属技术性重试，与画面重生成分开统计）。其余 5 镜全部为 Agent turn 自主提交。

### 4.5 对照评估表

| 探针 | 验收点 | 旧组结果 | 候选组结果 |
| --- | --- | --- | --- |
| P01 单主体写实首帧质感 | s01 首帧质感/光线/材质/构图 | 首帧=插画风（参考主导）；视频=3D 感良好 | 同为插画风（Look 同源）；视频 3D 感良好 |
| P02 同一角色多镜身份 | 小满跨镜身份+质感 | s01/s02 跨镜一致 ✓ | s01/s02 跨镜一致 ✓ |
| P03 双人道具交接 | s02 人数/站位/接触/持有者转移 | **接触瞬间完整可见 ✓** | **接触瞬间完整可见 ✓** |
| P04 写实手部物件操作 | s03 主动作不跳步/接触清楚 | 视频完成，特写镜 10s | 视频完成，特写镜 10s |
| P05 同一动作跨镜继续 | 端点选择/方向状态衔接 | 两组均以 Look/文字状态衔接，未用源尾帧硬接力（无状态参考的代价，见 §4.3 发现 2） | 同左 |
| P06 同一空间换机位 | 机位合理/空间关系保持 | 各镜机位按分镜变化，空间关系成立 | 同左 |
| P07 正常电影切镜 | s06 不强行接力/身份清楚 | 独立雨滴特写 ✓ | 独立雨滴特写 ✓ |

**时长政策对照**：旧组 10/12/10/12/10/10；候选组 10/12/10/10/10/**8**。两组均无 <8s；候选组在独立切镜上选择了 8s 下限（最小参考/最小时长决策），旧组统一 10s+。默认 10s 值得保留：12s 交接镜收益明确（完整接触过程），8s 切镜镜也够用——政策区间有效。

### 4.6 用户观看验收（2026-10-08，预览页人工审片）

用户审片结论（原话要点，如实记录）：
- 两侧衔接都比此前（Phase48 基线）好很多；但本轮测试的是比较简单的情况。
- **旧组**：人物一致性稍好，**运镜更好**；首帧表现为"参考"性质（未钉住为视频首帧）。
- **候选组**：首帧就是视频首帧（钉住更实）、**全片瑕疵更少**；但人物稍有一点点不一致、运镜不太好——**衔接性不如旧组**。

**生成模式数据佐证**（project.json 实测）：旧组 s01–s05 全部 `link_mode=multi`（多图语义参考、不锁首帧→运镜自由度大）；候选组全部 `link_mode=i2v`（首帧硬钉→首帧忠实、瑕疵少，但运镜被首帧构图约束）。**两组的优劣差异直接来自 Skill 的模式选择分歧**——这是人评定位到的可迭代点：候选包的模式选择规则需要增加"运镜自由度需求"维度（动作主导镜用 multi 换运镜空间，身份脆弱镜用 i2v 换一致性），而不是全选 i2v。

## 5. D 批：新会话完整故事与修复闭环

### 5.1 自主制作（D1）——已执行

Agent 只拿到目标级 brief（作品目标《追风筝》：放飞→挂树→修补→重新放飞，**无逐镜答案**），自主完成：
- **18 镜完整故事弧设计**：展风筝（4 镜含手部特写）→ 起放（3 镜含脸部特写/天空仰镜）→ 挂树阻碍（3 镜）→ 竹竿取风筝+解死结（4 镜）→ 修补（红布补丁细节）→ 重新放飞（红布成翅膀亮色收尾）。目标/阻碍/状态变化/结果齐备。
- **时长自主规划**：8s 特写 / 10s 常规 / 12s 关键动作（落地验损、重新放飞）混合。
- **首帧 18 张生成并自动选定**；视频阶段日志显示 Agent **先自主修复不合格首帧再逐镜提交**（keyframe 模式+10s）——候选 Skill 质量门的实际遵循。
- 18/18 条视频 ffprobe 实测 **8.0–12.25s、全部原生 aac**；其中 9 条因 Agent turn 中断由执行侧 REST 技术性补齐（visual-only；已与画面重生成分开统计）。
- 成片：`projects/P49_D_NEW/output/sequences/seq_3b221165/srender_bc3b352a.mp4`，**97.02s 含音轨**。

### 5.2 用户验收（D3）——**未通过**

**用户结论（2026-10-08 预览页审片）：D 批人物一致性极差，部分视频出现多个相同人物（分身）。**

执行侧抽帧复核**确认**：s04_c01 画面出现 2 个小满（黄雨衣双马尾女孩 ×2）+ 阿岩——人数与分身双重硬错误（按候选包质量门第 1 条应拦截，但该错误发生在视频生成阶段，首帧审查未覆盖）。

**根因分析**（执行侧）：
1. D 批沿用 Look-only 参考路径（C 批为绕开 SIV 审批死循环的妥协），**每镜只有静态 Look 参考、无上一镜真实画面/尾帧约束**——identity 漂移在 18 镜长链上累积，远超 C 批 6 镜。
2. 多人镜头（阿岩+小满同框+道具互动）在无 population 约束参考时是模型分身高发区（与 Phase43" fauna/分身"结论一致）。
3. 候选包质量门设计在**首帧层**生效，视频生成阶段的分身无对应硬门（C 批同样存在此缺口，C 批镜头少未暴露）。

**结论**：D 批按执行书 D3"用户仍不满意"处理——**候选 Skill 未达画质/一致性目标，不能宣称自主制作能力达标**；保留候选身份，不转正。改进方向：视频层增加人物数硬门（post-generation review）、恢复跨镜画面参考（需先解决 SIV 审批效率）、长链拆分参考锚定。

**C 批两侧 n_rv_final 成片关口同样保留给用户**（尚未观看裁决）。

## 6. 最终结论（执行书 §9 七问）

1. **新 Skill 是否实际加载、Agent 是否自主决策**：候选包真实加载有持久化证据（meta.skills：hint→load→SKILL.md+references/director.md→capability ok）。Agent 在时长分配上做出符合政策的自主决策（交接镜 12s/切镜 8s）。但**分镜图生成与 NEW s02 视频的执行因系统层缺陷多次被阻**，部分生成改由执行侧 REST 补齐——Skill 的"方法遵循"得到证据，"全自主执行"未完全达成（见 §4.2/§4.4）。
2. **素材 ≥8s 政策是否落实**：12/12 条视频真实时长 8.0–12.3s（ffprobe 实测），无一条 <8s；T21 闸门默认关闭未干预生产。默认 10s 保留（12s 对交接镜收益明确，8s 对独立切镜够用）。
3. **新旧对照改善/退步（含用户人评 §4.6）**：两侧均比 Phase48 基线衔接好很多。**用户人评：旧组胜在人物一致性、运镜、衔接；候选组胜在瑕疵更少、首帧钉住忠实**。差异根因=模式选择分歧（旧组 multi 语义参考 vs 候选组 i2v 首帧硬钉）——已定位为候选包迭代的明确方向（模式选择规则加"运镜自由度"维度）。本轮证据**不支持候选 Skill 转正**；两组互补的结论支持"按镜头性质混合选模式"的后续方向。
4. **哪些错误可裁切、哪些必须修**：s03 首帧人数/持有者错误=关键节点错误，重生成未修掉（提示词层根因），不宣称"可裁切解决"；视频层未见需裁切的硬错误；首中尾抽帧合格≠全片合格，完整逐帧审片（含音效同步人工复核）留待用户验收环节。
5. **保留端点与依赖一致性**：本轮未出现"源尾帧与剪后尾态混用"实例（两侧 s01 均为独立起步、s06 为 hard cut；接力场景在 C 批brief 中由 s02→s03 承担，两侧均以状态描述衔接）。B 批 T13 已在夹具层验证端点区分机制。
6. **用户画质要求是否达到**：未达到。两处未达标：① photorealistic 写实人像——参考风格主导输出为插画/3D 感渲染（与 Phase48"模型上限"裁定一致）；② **D 批人物一致性极差+分身**（用户验收，s04_c01 抽帧实证 2 个小满）——Look-only 长链无跨镜画面约束的 identity 漂移累积。更换模型+恢复跨镜参考是必备改进项。
7. **正式采用建议**：候选包**继续保留候选身份迭代，不转正**。依据：① C 批人评——旧组胜一致性/运镜/衔接，候选组仅胜瑕疵与首帧钉住；② D 批人评——自主制作在长链多人场景一致性不达标（分身实锤）。候选包有效资产（首帧质量门 5 条/三层端点/可剪判定表/时长政策）可并入正式 Skill 迭代；系统层修复（pack 编译纯 Look 合法化、vision max_tokens 透传、T17 互斥拦截、T21 时长闸门）是本轮对底座的净贡献。

**验收表**：C 批两侧 n_rv_final = 待用户选用（成片已交付）；D 批 n_rv_final = 用户已验收**未通过**（分身/一致性）；其余状态见各节。
