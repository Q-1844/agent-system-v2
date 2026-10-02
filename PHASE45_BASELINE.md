# PHASE45 基线核验记录（45.0）

日期：2026-10-02。执行：zcode（45 批次）。依据：`PHASE45_ASSEMBLY_AGENT_COORDINATOR_INTEGRATION.md`。
本文件是任务书 §1 要求的 `PHASE45_BASELINE.md`：B/C 报告陈述逐条对照仓库真实状态，
标出差异；末尾是本批次的最小设计决策记录（编码前定稿）。

状态标记：〔已核验〕＝命令/文件/行号可复现；〔差异〕＝与报告陈述不符处。

---

## §1 仓库与工作区状态

- 子模块 HEAD：`4fe15a2`（"44C+ 测试师接管：成片旁白二次缺失根因=单遍「混音图→AAC」丢延迟层"）。
- 未提交 diff（3 文件 + 1 未跟踪）：
  `PHASE44_C_REPORT.md`（+140 行，§9.2/§9.3 增补——44C+ 二次反馈轮）、
  `_44c/jobs_44c.json`（+23，隔离作业账本追加）、
  `_44c/review/LISTEN_44c.html`（重写为终版试听页）、
  未跟踪 `PHASE44_D_REPORT.md`（44D 盲测报告，本阶段暂缓项）。
  判断：均为 44C+ 收尾的真实遗留，与本批次无冲突；本批次继续在其上追加，不回滚。
- 测试基线（本批开工复跑）：44B/44C+ 音频服务定向套件 **104 passed**；
  producer/agents/sequence 相关 **197 passed**（46.7s）。
  44C 报告声称的 44C 增补修复（a–e）确认在当前代码并有测试锁定（§2）。

## §2 B/C 报告声明 vs 仓库真实状态

| # | 报告声明 | 核验结果 |
|---|---|---|
| 1 | 44A 正式快照 `snap_44a_formal_20261001_001753`，13/13 user_locked | 〔已核验〕`_44a/snapshot_formal.json`（mode=formal，13 shots，media mirror `_44a/snapshot_media`）；`_44a/assembly_input_manifest_formal.json` 存在 |
| 2 | B：`assembly` 第五 Specialist，11 工具 | 〔已核验〕`src/agents/registry.py:27-33` SPECIALISTS 含 `assembly`；`src/agents/assembly.py:621-628` 16 工具（B 11+C 5） |
| 3 | B：`seq_a3d5b19b`（89.9s 全片 Agent 装配，manifest_v2） | 〔已核验〕`projects/雨灯归航/output/sequences.json` 中 seq_a3d5b19b，13 clips，`final_render: null`，19 条 render_versions 全部 sequence_revision `4df4d8cb`；磁盘 18 个 `srender_*.mp4` |
| 4 | C：旁白仓 + MiniMax TTS + AudioPlan 混音 + stem + 5 声音工具 | 〔已核验〕`src/audio/narration.py`、`output/narration_audio.json`（28 cues：nd01–13/na01–05/nc01–05/探针）、`output/sequence_audio_plans.json`（seq_a3d5b19b plan revision `e4c7b33f`，16 层=music×3+narration×13）、`stem_{narration,music}_reve4c7b33f.wav` |
| 5 | 推荐版 `srender_fa9a2b7a`（13 句+三段音乐+原生静默，I=-16.0 LUFS） | 〔已核验〕render 行 created_at 1790850135 紧跟 plan e4c7b33f 更新；`_44c/mix_full_evidence.json` 验收数据在档。**注意：该版为执行侧脚本驱动（`_44c8`–`_44c10`），不是 Agent 自主轨迹——本阶段 45.1 要用新 Agent 会话重做** |
| 6 | 用户已定创作约束（温润男声/原音静默/全片旁白/每场景不同音乐/无脚本音效/"还不错"） | 〔已核验〕44C 报告 §9.1/§9.4 有记录；作为本阶段 Agent 运行的输入约束（不给逐句旧文案） |
| 7 | MiniMax Music API 410/2153 关闭；音乐为本地合成 | 〔已核验〕`_44c/music_gen_probe.json` 在档；三段 wav 在 `output/audio_imports/music/`（s1/s2/s3，参数+sha256 在 `_44c/music_scenes_params.json`）。**性质=测试垫乐，正式音乐待用户授权** |
| 8 | 44C 修复：两遍 PCM→AAC | 〔已核验〕`src/sequence/audio_mix.py:103-192`（图只产 PCM，`final_label`）+ `service.py:396-424` 两遍；回归锁 `tests/test_sequence_audio_service.py:126-150` |
| 9 | 44C 修复：纯 S1 base | 〔已核验〕`service.py:476-495` `_eligible_mix_base` 跳过 v2 basis |
| 10 | 44C 修复：`speed:null` 归一 | 〔已核验〕`src/audio/narration.py:210-215` |
| 11 | 44C 修复：plan float 归一 | 〔已核验〕`service.py:251-254` 经 `_check_gain`；`audio_models.py:340` `__post_init__` |
| 12 | 44C 修复：proposal 编号取最大 N+1 | 〔已核验〕`assembly.py:1122-1127` |
| 13 | 44C：全句音频审计 | 〔差异/缺口〕**未实装**。`render_status` deep_audit（assembly.py:899-920）只有 B 批帧级审计（PTS/黑帧/切点窗/全片静音 advisory），无逐句旁白/逐段音乐落窗证据——逐句验证仅存在于一次性脚本 `_44c10_mix_full.py`。**45.1 补口 ①** |
| 14 | manifest_v3 narration_cues | 〔已核验〕schema 恒为 `assembly_manifest_v1`（`assembly_manifest.py:29`），v1/v2/v3 是 revision 序号；narration_cues 字段已实装。〔差异〕**music_bed 无任何调用方传值（恒 null）**，plan 的三段音乐不投影进 manifest——45.1 补口 ③ |
| 15 | Agent 无 select 工具；`/renders/select` 用户专用 | 〔已核验〕工具面无 select；REST `routers/sequences.py` select 端点独立 |
| 16 | 调用链入口 | 〔已核验〕Direct Chat `POST /turn`（routers/agents.py:229）、流式 `:285-468`；unified_turn 分流 runtime.py:349-518（producer gate :403-407 → producer_turn）；assembly project 会话走原生 `assembly_turn`（:374-395） |

## §3 Producer／协调链现状（45.2 的复用点）

- Producer V1 原生 tool-loop：`src/agents/producer.py`（1330 行）。委派工具 4 个：
  `script_writer/character_designer/scene_designer/director .delegate`
  （`DELEGATE_TOOLS` producer.py:93-94；schema :283-328；执行器注册 :1226-1228）。
- 委派执行器 `_delegate_specialist`（producer.py:710-896）：envelope 校验 → lazy import
  child turn + 写域表 → child session（`get_or_create_conversation` 长存）→
  `turn_registry.acquire` 互斥 → AgentTask 创建（`parent_task_id=prev_task_id` 链）→
  Bridge event_cb（锁接线+事件转发）→ `await turn_fn(...)` → 结算 AgentTask +
  bridge_messages.jsonl 两条 → Task Result 回 Producer loop。**父子任务持久化已存在**。
- registry 层 `can_delegate_to=list(SPECIALISTS)` 已含 `assembly`
  （registry.py:38-46）——**权限层无需改动**；Producer 工具面缺第 5 个 delegate——45.2 补口。
- Task Result 派生 `_derive_changed_refs`（producer.py:498-551）只认
  scene/shot/character/资源域键，**不认 sequence 域回执**——assembly 委派后
  changed_refs 恒空。45.2 补口 ②（加法式加 `sequences` 键）。
- 会话持久化：`projects/{p}/output/agents/sessions/{sid}.json` 原子写
  （sessions.py:25-145），重启 `load(sid)` 直接恢复；AgentTask 一任务一 JSON
  （tasks.py）；workflow `workflows.json` + `bridge_messages.jsonl`（producer.py:637-698）。
- 崩溃恢复：JobQueue 重启重置重跑（job_queue.py:81-93）；AgentTaskSettler
  对账 running task↔linked Job（settlement.py:186-384）。
  〔差异〕**sync-delegate 型 running task（无 execution_ref job）重启后无对账路径**——
  45.3 补口。
- 事件：`RESOURCE_SEQUENCE` 锁域 + `sequence.changed` 事件已接线
  （project_events.py:28-38、:127-141；assembly 写域映射 WRITE_TOOL_DOMAIN
  assembly.py:85-95；流式端点 :337-343）。Producer Bridge 复用同语义。

## §4 音频资产状态（45.0.3 结论）

- 三段音乐 wav **已在 S2 imported_audio 规范目录** `output/audio_imports/music/`
  并被正式 AudioPlan（revision `e4c7b33f`）按 `source={kind:"imported_audio", file:...}`
  引用——**Agent 已可通过 audio_plan_read 看到它们；但没有任何"资产库枚举"工具**
  （narration_read 只覆盖旁白仓）。45.1 补口 ②：只读 `assembly.audio_assets_read`
  （枚举 audio_imports 文件 + ffprobe 实测 + sha256 + 当前 plan 引用 + 可选身份档）。
- imported_audio 无"注册端点"（渲染期 fail-closed 校验目录+哈希即真相）。
  身份/许可信息目前只在 `_44c/music_scenes_params.json`（批次档案，非 canonical）。
  最小方案：`output/audio_imports/assets_meta.json` 单文件身份档
  （relpath→origin/license/created_by/note/sha256），由确定性脚本一次性登记现有
  三段测试垫乐；`audio_assets_read` 透出身份，无档案的标 `identity: unregistered`。
  **不新建资产管理系统**，不虚称授权音乐。

## §5 Schema/迁移与兼容

- 无持久化 schema 破坏性变更：assets_meta.json 为新增可选文件（缺失=无身份，工具面
  优雅降级）；manifest 仍 `assembly_manifest_v1`（music_bed/narration 富化是文档内容
  演进，构建器单一写入者不变，指纹机制不变）；AgentTask/changed_refs 均加法式加键
  （`sequences`），旧 workflow/Room 投影对新键容错（与 44B/C 同惯例）。
- 旧会话/待审请求：Producer system prompt 增量更新（4→5 Specialist 行文）；
  assembly 会话工具面新增只读工具不破坏旧轨迹回放。

## §6 本批次最小设计决策（编码前定稿）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 委派复用 `_delegate_specialist` 泛化链，只加 assembly 分支（4 处：DELEGATE_TOOLS/_SPECIALIST_RESOURCE/lazy-import/executor 注册+schema） | 任务书 §2"复用既有任务/会话框架"；44B 已建 registry 授权 |
| D2 | 逐句/逐段音频审计落确定性模块 `src/sequence/audio_audit.py`，经 `render_status deep_audit=true` 透出（不新建平行工具面） | 任务书 45.1.4"逐一取落窗证据"；render_status 已是 Agent 审计入口；深审计=probe+帧审计+音频审计三段 |
| D3 | 审计算法=源 WAV（仓内 sha256 已知）与终混对应窗 PCM 的 Pearson 相关 + 分窗电平；音乐段双窗采样；LUFS 用 ebur128 实测 | 与 `_44c10` 验收同判据，脚本判据升正式工具面；ffmpeg 已是渲染依赖 |
| D4 | `audio_assets_read` 只读；身份档 assets_meta.json 一次性确定性登记 | 任务书 45.0.3"最小补口；禁止 Agent shell 搬运" |
| D5 | `narration_read` 加可选 sequence_id：窗口从该 sequence 的 canonical TimelinePlan 复算（缺省回退最新 manifest） | Agent 新建自己的 sequence 后窗口应来自其 canonical，而非旧 manifest |
| D6 | manifest_publish：narration_cues/music_bed 缺省时由确定性构建器从 plan+旁白仓投影（Agent 传值仅作显式覆盖并标注 source） | manifest=派生文档单一写入者；消除 music_bed 恒 null |
| D7 | Producer 消费 assembly 回执：changed_refs 加 `sequences` 键；Task Result 加 `assembly_evidence`（sequence/plan/render/manifest revision+哈希，全 receipts 派生）；Producer 只读面复用 `assembly.sequence_read`/`assembly.render_status` schema | 任务书 45.2.3"消费 receipt 时校验谱系，不把文案当事实" |
| D8 | sync-delegate 任务中断恢复：AgentTaskSettler 对无 execution_ref 的 running task 重启后标 failed（`interrupted_by_restart`），workflow 同步标 interrupted；child session 消息已在盘，续做=新任务新版本 | 任务书 45.3 第 1 行；诚实失败优于永久 running |
| D9 | 45.1/45.4 真实会话均 headless 正式入口（`assembly_turn` / `producer_turn` 真实 Agnes LLM），事件 jsonl+result json+session 快照留档；渲染走隔离 jobs 文件不污染生产队列 | 与 44B/C 验收同标准；任务书 45.2"不能用脚本拼产物冒充 Agent" |
| D10 | No Auto Select 不变：Agent 产出候选+提案；`select final_render` 仅用户 REST | 冻结纪律 |

——45.0 完毕。后续按 D1–D10 实施，差异再追加记录。
