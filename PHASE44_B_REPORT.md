# PHASE 44B 报告——基础成片 Agent、时间线与无声导出

日期：2026-10-01。执行：zcode（B 批次）。依据：
`详细流程文件/新阶段1/PHASE44_B_基础成片Agent.md`。
输入：Phase 44A 正式快照 `snap_44a_formal_20261001_001753`
（`_44a/assembly_input_manifest_formal.json`，13/13 user_locked，媒体三方哈希核验——
本批开工前对 live 文件/镜像/登记哈希做了 13/13 复核，全部一致）。

状态标记：〔已核验〕＝命令/回执/哈希可复现；〔待用户〕＝等用户看片/裁定。

---

## §0 结论摘要

1. **基础成片能力建成并全链路验收**〔已核验〕：独立 Assembly Agent（第五个正式
   Specialist）+ 确定性时间线工具，全部落在既有 Sequence 域上——无任何平行真相。
   89.9s 全片（13 镜）可播放 mp4、时间线契约文档（AssemblyManifest v1/v2）、
   接缝机器检查、崩溃恢复全部 PASS。
2. **两轮真实 Agent 会话**〔已核验〕：Agent 自主完成 briefing→快照读取→
   sequence 创建（OCC verified）→渲染发起→渲染核验（deep audit）→manifest 发布→
   评审提案。工具轨迹/事件/回执全部落 `_44b/`。
3. **零视频生成费用**〔已核验〕：本批隔离作业文件中只有 `sequence_render` 一种
   kind（9+ 次渲染全部本地 ffmpeg）；配额账本与常驻后端共享，其 video_seconds
   变化（37→45）来自后端并行活动（用户侧），与本批无关（§8.3 证据口径说明）。
4. **三处仓库既有缺陷在本批发现并修复**（§6）：S1 诊断读取 stdout 而滤镜输出在
   stderr（advisory 恒空）；诊断解析不兼容 `key: value` 空格形式与跨行输出；
   render profile 冻结值（1280x704）与本片素材（720 为主）不符。
5. **正式交付物**〔待用户〕：评审页 http://127.0.0.1:8786/_44b/review/REVIEW_44b.html
   （4 条可播放成片 + 时间线表 + 审计 + 提案）。`final_render` 的 select 与成片
   定版是用户显式动作（agent 无此工具）。

---

## §1 B0 设计决策（编码前已定，全文见 `_44b/DESIGN_DECISIONS.md`）

三方案对比：①纯脚本拼接（拒绝：无 canonical/OCC/恢复/提案面）；②在 Director 里加
导出命令（拒绝：违反 V2 §16 所有权矩阵——Sequence 域明定「未来 Assembly Agent 只读
消费」；render 持久 job 与对话 turn 生命周期错配）；**③独立 Assembly Agent + 确定性
时间线工具，全部经既有 SequenceService/JobQueue 正式链路（选定）**。

边界矩阵（核心几条，全文见设计文档 §1）：

| 问题 | 决定 |
|---|---|
| 谁选择镜头顺序与节奏 | Agent 提案（默认 canonical 叙事顺序全保留/整镜/硬切），用户终审 |
| Agent 能改什么 | 仅 SequenceBlock（clips/trim/render_profile，OCC）+ AssemblyManifest（确定性构建）+ 提案 md。不碰 Story/Look/SIV/Shot.final_video/生成 |
| 渲染任务生命周期 | `render_submit`→create_render（append queued）→JobQueue `sequence_render`（持久化/看门狗/崩溃恢复）；重试=新 render version，绝不覆盖旧输出 |
| 媒体在哪 | 源=Shot.final_video→VideoVersion.local（唯一源真相）；哈希 pin=44A 快照（对齐核验 fail-closed）；输出版本化 `output/sequences/<sid>/<srender_id>.mp4` |
| 用户终审环节 | ①剪辑提案 ②成片看片 ③final_render select（REST API，agent 无工具）④正式成片只能出自 formal 快照 |
| 禁止事项 | Agent 永不输出/执行 ffmpeg shell——ffmpeg 只存在于确定性代码（media_ops/handler） |

## §2 代码变更清单（12 个既有文件 +415/−41 行，7 个新模块/脚本 ≈2400 行）

**修改（全部加法式，冻结域零语义变更）**：

| 文件 | 变更 |
|---|---|
| `src/sequence/models.py` | +`render_profile` 可选字段（None=全局 profile，S1 语义逐字节不变）；+`validate_render_profile`（完整规格/白名单/偶数宽高 fail-closed）；+`effective_render_profile()`/`render_profile_fingerprint_for()` |
| `src/sequence/basis.py` | basis 的 profile 指纹取 `block.effective_render_profile()`（覆盖→新指纹进 basis；None→与旧指纹逐字节一致） |
| `src/sequence/service.py` | create/update 接收 render_profile（空 dict=清除覆盖）；execute 传 profile+`audio=silent` 策略；含 AudioPlan 时 profile 必须为 48k/2（混音规格守卫） |
| `src/sequence/media_ops.py` | `_normalize_vf` 支持 `fill=crop`（scale-increase+中心裁切，与 pad 同为保比例配方）；**+44B 修复**：`_run_diag`（诊断滤镜读 stderr）、`_parse_diag` 重写（空格形式+跨行累积）；+`inspect_frames`/`audit_output_seams`（B3 帧级审计） |
| `src/agents/assembly.py`（新，~1000 行） | Assembly Agent：11 工具 schema+执行器+tool loop（镜像 scene_designer：OCC/真相闸门/单写串行/回执账本/renewable window） |
| `src/agents/registry.py` / `capabilities.py` / `models.py` / `runtime.py` / `project_events.py` / `routers/agents.py` | `assembly` 加法式注册：SPECIALISTS、AgentDefinition（write_scope=sequence_assembly）、`assembly.inspect/assemble` 能力（CapabilityCategory+SEQUENCE）、unified_turn 分流、流式端点分支（RESOURCE_SEQUENCE 锁域）、`sequence.changed` 事件 |
| `src/sequence/assembly_manifest.py`（新） | AssemblyManifest 确定性构建器/校验器/只追加落盘（§4） |
| `tests/test_agents.py`、`test_p39_prompts_discipline.py` | roster 9→10（加法式更新，同 scene_designer 先例）；assembly 零 Skill 绑定断言 |

**新增测试**：`tests/test_44b_render_profile.py`（13）、`test_44b_assembly_manifest.py`
（11）、`test_44b_assembly_agent.py`（13）+共享 fixture，共 44 项（零 ffmpeg/零 LLM）。

**新增脚本**：`_44b1_e2e.py`（B4 工具验证驱动器，552 行）、`_44b2_agent_run.py`
（真实会话运行器，219 行）、`_44b3_review_server.py`（评审页生成+HTTP，196 行）。

## §3 B1 时间线契约（schema 与实例）

**真相分工**：SequenceBlock=唯一剪辑 canonical；AssemblyManifest=派生文档
（快照 pin+时间线投影+C 批次占位），构建器是唯一写入者，LLM 不得手写；文档指纹
排除 assembly_revision（同语义重发=空转版本，发布层拒绝），任何内容篡改→指纹不符
fail-closed。

schema（`assembly_manifest_v1`，实例=`projects/雨灯归航/output/assembly/manifest_v1.json`）：

```json
{
  "schema": "assembly_manifest_v1",
  "project_id": "雨灯归航", "snapshot_id": "snap_44a_formal_20261001_001753",
  "snapshot_mode": "formal",
  "sequence_id": "seq_900fdf75", "sequence_revision": "…",
  "assembly_revision": 1,
  "shot_items": [{
    "index": 0, "shot_id": "s01_c01", "scene_id": "s01",
    "scene_boundary": false,
    "video_version_id": "vv_55accb16", "media_sha256": "707e26…(64hex)",
    "source_file": "…", "source_duration_ms": 8000,
    "source_in_ms": 0, "source_out_ms": 8000, "effective_ms": 8000,
    "timeline_start_ms": 0, "timeline_end_ms": 8000,
    "transition": "cut", "approved_status": "user_locked",
    "probe_now": {"width": 1280, "height": 720, "fps": 24.0, "vcodec": "h264",
                   "pix_fmt": "yuv420p", "has_audio": true, "acodec": "aac"}
  } /* ×13 */],
  "total_duration_ms": 89902, "seam_count": 12,
  "seam_notes": [], "narration_cues": [], "music_bed": null,
  "render_profile": {…1280x720@24, libx264/yuv420p, aac 48k/2, fill=crop, audio=native},
  "audio_strategy": "native", "renderer_schema": "sequence_renderer_v1",
  "doc_fingerprint": "…"
}
```

最小不变式（全部 fail-closed，带 shot_id 定位，逐项测试覆盖）：`source_out >
source_in` 且不超源实测时长（ffprobe 现场）；时间线 cursor 单调+与 TimelinePlan
复算互证；live final==快照 pin 且 sha256==登记哈希（版本/哈希/快照 revision 完全
匹配）；媒体丢失/不可解码/越界→可定位错误，绝不偷换素材。转场恒 `cut`
（renderer v1 硬切），溶解类建议只进提案文档。

## §4 B2 Agent 与工具边界（11 工具）

只读：`assembly.briefing`（全局状态+按素材实测推导推荐 profile）/`snapshot_read`/
`sequence_read`/`media_probe`/`timeline_preview`（dry-run 预演）/`render_status`
（含 deep_audit 帧级审计）。
写（单写串行、OCC 透传、写后重读 canonical 验证、DirectEditAudit 审计）：
`sequence_create`（前置快照对齐核验）/`sequence_update`（expected_revision 409）/
`render_submit`（JobQueue 入队，回执含 job_id+render_version_id）/
`manifest_publish`（无 done 渲染拒绝）/`proposal_write`。

物理边界（工具面里就没有，非提示词）：无任何生成工具；无 Shot/Story 写工具；
无 select 工具；无 ffmpeg/shell 入口。快照读取限制在仓库根内（防越界路径）。

## §5 B3 渲染实现与双音轨策略

- **探测**：13/13 源 h264 yuv420p 24fps + aac 32kHz stereo，start_time=0；
  异构性=11 镜 1280x720 + 2 镜 1280x704（s01_c04/s02_c02，44A 回拨镜为旧代际产物）。
- **管线**：逐 clip 一遍完成 trim+归一化（AV together，scale/pad 或 crop/setsar/fps +
  libx264 yuv420p + aac 48k stereo，无音轨补等长 silence）→ concat demuxer
  stream copy 硬切（流已同构）。不使用 `-c copy` 直拼异构源。
- **本片 profile**：1280x720@24 + fill=crop + audio=native。fill 取舍：pad（仓库
  标准）会对 2 镜 704 素材上下各加 8px 黑边（硬切处可见）；crop（scale-increase
  1.0227x + 中心裁切）满幅一致、画面比例严格保持、边缘共裁 ~1.1%。两案均保比例，
  选 crop 并在 profile 显式声明（进 basis 指纹）。无信息放大（704→720 为 2.3% 归一
  化，48kHz 重采样仅统一容器规格）。
- **双音轨策略**：`audio: native|silent` 显式入 profile（进 basis→策略变化=诚实
  STALE）。实测：silent 渲染 volumedetect mean/max=−91dB（数字静音）vs native
  mean −35.2dB/max −6.5dB；策略回退后 native 渲染 freshness 恢复 CURRENT、silent
  诚实 STALE。本批成片不加任何 BGM/旁白（杜绝与未来统一 BGM 叠加）。

## §6 本批发现并修复的仓库既有缺陷

1. **S1 诊断双缺陷**（`media_ops.py`）：`detect_black_segments`/`detect_silence_
   segments` ①读 stdout 而滤镜报告在 stderr→**advisory 恒空列表**（S1/S2 报告的
   「黑帧/静音 0」属空断言）；②解析器只匹配 `key:value` 紧凑形式且假设单行，实际
   ffmpeg（2026-01-07 build）输出 `key: value` 且 silencedetect start/end 跨行。
   修复后本片实测：全片黑帧 0（真实零）、静音段 0、silent 渲染静音段=整片。
2. **渲染 profile 冻结值过期**：`SEQUENCE_RENDER_PROFILE=1280x704` 冻结自 Native AV
   时代，video 2.5 迁移后生产现实为 720 为主——直接沿用会 11/13 镜两侧加 14px 黑边。
   以 per-sequence 覆盖解决（全局常量按 S1 冻结纪律不动）。
3. **Assembly 工具 bug**（本批自测发现）：render_submit 回执构造用了 `job.status`
   （真实 Job 字段为 `.state`）→ 回执异常。已修+测试桩镜像真实字段防回归。

## §7 B4 验收结果（全部真实数据）

### 7.1 小样本（B4.1）〔已核验〕
`s01_c04→s01_c05`（同组接力，44A 实测 MAE 10.8 带）+`s01_c05→s02_c01`（跨组切换）。
sequence `seq_22ff8de0` / render `srender_664a49b9`：**8.1s 渲染完成**，计划
18359ms/实测 18380ms（Δ+21ms）。两切点 ±500ms 窗各 24 帧、无空洞/无重复帧/无黑帧。
预览帧 7 张（开头/中点/两切点±0.25s/尾帧）落 `_44b/previews/`。

### 7.2 全片工程预览（B4.2）〔已核验〕
13 镜（快照 order 1→13 全保留整镜硬切），sequence `seq_900fdf75` / render
`srender_53486e6a`：**28.2s 渲染**，片长 89902ms 计划/89923ms 实测（Δ+21ms）。
逐镜时间线表（起止/来源版本/裁定状态/跨组标记）见评审页与 `manifest_v1.json`。
12 切点：11 处全净；1 处（s01_c06→s02_c01 @39543ms）切点窗内帧空洞 83ms——
段时长非帧对齐拼接的固有现象（1 帧槽位），时长误差在容差内，视觉不可察，
列入已知限制（§9）。接缝 MAE（44A 实测）随 manifest 归档供用户看片对照：
s03_c03→s03_c04=86.6（从未看片，最高风险）、s01_c01→s01_c02=61.4。

### 7.3 修改测试（B4.3）〔已核验〕
- **trim 修订**：s01_c04 掐尾 200ms→新 render version `srender_620ec518`（26.2s）；
  旧输出 sha 不变、manifest_v1 sha 不变；freshness 精确显示
  `clips[3].trim_out_ms` mismatch→STALE；回退 trim 后 v1 **无需重渲染**恢复
  CURRENT（basis 机制）。
- **版本替换**（fixture `projects/_44b_swap_fixture`，2 镜×2 版本）：用户把
  sA_c01 final 换到 vv_a2→CURRENT→STALE→重渲染→新 render version 只换该镜来源
  （`source_video_versions`: sA_c01→vv_a2，sB_c01→vv_b1 不变）；旧输出/哈希可找回。

### 7.4 中断测试（B4.4）〔已核验〕
子进程提交全片渲染后 25s `os._exit(2)` 硬杀（render 行状态=rendering）。恢复进程以
同一 jobs cache 启动 JobQueue→running→queued 重推→**同一 render version 完成回执**
（`srender_2d46f206` done，89923ms）；旧 done 输出各持版本化文件未被覆盖
（v1 sha 复核一致）。孤儿 mp4 未作为凭证——完成状态以 render 行+输出+probe 为准。

### 7.5 真实 Agent 会话（B4.5）〔已核验〕
会话 `sess_4f24b44e5672`（project scope），真实 Agnes LLM 两轮（T1 133.8s +
T2 124.8s），事件/结果/会话快照落 `_44b/events_agent_*.jsonl`、`result_agent_*.json`、
`session_after_agent.json`、`agent_run_summary.json`。

- **T1**：briefing→snapshot_read→timeline_preview→`sequence_create` 创建
  `seq_a3d5b19b`（13 clips，revision 4df4d8cb，**verified**，复用了 briefing 推荐
  profile）→render_submit（当时命中 §6.3 的 job.status bug，回执异常）→agent 转向
  sequence_read 读回确认渲染实际 done（**真相闸门按设计工作**：语言申报被确定性
  诚实覆写，只引用 verified 回执）→`manifest_publish` 发布 manifest_v2（verified）
  →提案 v1/v2。
- **T2**：render_status（deep_audit=true）自主核验→报告 PTS 单调✅/重复帧 0✅/
  缺帧 1⚠️（pts 949）/黑帧 0✅/接缝异常 1 处（与本批驱动器独立审计**完全一致**）→
  提案 v4（含顺序依据、跨组切点、高风险接缝清单、「select 由您显式执行」提示）。
- Agent 行为记录：尝试过一次不存在的工具名（`assembly.manifest_read`，被拒后
  自行纠正）；写过一页废稿提案 v3（标题 "test"，已清理，回执留痕）。
- **工具验证 vs Agent 验收**：B4.1–B4.4 为确定性驱动器（工具验证）；本节为 Agent
  验收——两者产出独立可对照（两条全片 sequence 的时长均为 89902→89923ms）。

## §8 交付物与入口

**新增/复用入口**：
- REST（复用既有 8+2 端点）：`/api/project/{p}/sequences` CRUD、`/render`、
  `/renders`、`/renders/select`（用户专用）、`/freshness`、`/audio-plan`（C 批）。
- Agent：Direct Chat/流式端点 `assembly` 会话（runtime/registry 已接线）；headless
  范本 `_44b2_agent_run.py`。
- Job：`sequence_render`（复用，含崩溃恢复）。
- 事件：`sequence.changed` + `RESOURCE_SEQUENCE` 资源锁（SSE）。

**成片清单**（全部可播放，见评审页）：
| 成片 | sequence/render | 片长 | 音轨 |
|---|---|---|---|
| 全片（驱动器） | seq_900fdf75/srender_53486e6a | 89923ms | native |
| 全片（Agent 装配） | seq_a3d5b19b/srender_aca99c81 | 89923ms | native |
| 小样本 native | seq_22ff8de0/srender_664a49b9 | 18380ms | native |
| 小样本 silent | seq_22ff8de0/srender_4c781784 | 18355ms | 静音轨（−91dB） |

**测试**：新增 44 项确定性测试全绿；受影响既有套件（sequence 全家 83 项、agents 31 项
等）全绿；最终全量回归 **2568 passed / 0 failed**（16:13，`_44b/_full_regression.log`；
roster/哨兵 3 项按加法式惯例更新后纳入通过基线）。

**§8.3 零费用证据口径**：配额账本 `cache/quota.json` 与常驻后端（:8000）共享，
video_seconds 在本批窗口内 37→45 的变化对应后端并行活动（text_calls 同步 +13，
本批无 LLM 之外的调用窗口）。批次内证明=隔离作业文件 `_44b/jobs_44b.json` 全量
作业 kind 集合 ⊆ {sequence_render}（`e2e_evidence.json.batch_job_kinds`，逐条可查）。

## §9 已知限制（不隐瞒）

1. **1 帧槽位接缝空洞**（83ms @39543ms，s01_c06→s02_c01）：段时长非帧对齐
   （5184ms=124.4 帧）拼接时 1 个帧时间槽跳跃。属 concat stream-copy（S1 §11 冻结
   设计）固有；消除需 concat 层重编码或段尾帧对齐 trim，本批不动冻结设计。全片
   仅此 1 处，时长误差 +21ms。
2. **三路导出并存**（V2 Blocker B5）：legacy compose/manifest autopilot/Sequence
   render 未收敛；本批成片能力只挂 Sequence 一路，legacy 路径未动。
3. **fill=crop 的 2.3% 归一化**：2 镜 704 素材上采样至 720（信息无增益），
   是满幅一致与黑边之间的取舍，已在 profile 显式声明；若用户偏好零上采样可切回
   fill=pad（一处工具调用，basis 会诚实 STALE）。
4. **提案非结构化**：proposal 为 Markdown（人读）；顺序/trim 的机器执行面只在
   SequenceBlock canonical——C 批若需要「提案一键应用」，再加确定性 applier。
5. **Agent 流式端点未做浏览器 UI**：assembly 会话可经 API/headless 使用；前端
   Agent 房间展示（roomIdentity 等）留给后续（本批未排）。
6. **评审 HTTP 服务不支持 Range**：视频可播放、拖动进度条受限（SimpleHTTPRequest
   Handler 局限）；本地 file:// 播放或换 http.server 增强即可。
7. `final_render` 全部为 None（符合 No Auto Select）；freshness 对 final 指针报
   missing 属预期语义（指定 version_id 可查得 current）。

## §10 供 C 批次使用的音轨与 cue 时间契约

1. **时间基准**：C 批全部 cue 以 `manifest_v{N}.json`（最新=AssemblyManifest v2，
   指向 Agent 序列 `seq_a3d5b19b`）的 `timeline_start_ms/timeline_end_ms` 为唯一
   时间坐标（0ms=成片第一帧）。manifest 指纹校验通过才可消费（`validate_assembly_
   manifest`）。
2. **旁白 cue schema 占位**：`narration_cues: []`——建议元素
   `{cue_id, shot_id, start_ms, end_ms, text_ref, note}`；写入经
   `assembly.manifest_publish`（参数已预留）或未来 cue 专用端点；渲染接线直接复用
   Sequence S2 `SequenceAudioPlan`（narration 层 role="narration"，a1_dialogue_audio
   或 imported_audio 引用，OCC + 越界 fail-closed 已备）。
3. **music_bed**：本批恒 null。统一 BGM 接入=S2 music 层；**注意**：当前成片
   audio=native（12/13 镜有原生环境声），BGM 叠加前需用户定 ducking/替换口径
   （s02_c03 甲-2 原生近静音是用户已知选择）。
4. **切点表**：12 个硬切点 = manifest shot_items[0..11].timeline_end_ms
   （8000/16000/24000/29175/34359/39543⚡跨组/47543/52718/57902⚡跨组/65902/73902/
   81902）；旁白入出点避开切点 ±200ms 为宜（供参考，非硬约束）。
5. **音轨策略**：manifest `audio_strategy` 字段即当前成片音轨事实（native）；
   C 批混音渲染会自动走 S2 方案 A（video stream copy + 音频重编码），与本批
   profile 的 48k/2 守卫兼容。

## §11 用户待办

1. **看片**：http://127.0.0.1:8786/_44b/review/REVIEW_44b.html ——4 条成片+时间线+
   审计+提案 v4。重点口径：s03_c03→s03_c04（MAE 86.6）与 s01_c01→s01_c02（61.4）
   是否需要处理；fill=crop 满幅 vs 黑边取舍是否接受。
2. **定版**：满意后对全片 sequence 显式 `select final_render`（REST）即完成成片
   选择；需要 trim/顺序调整直接告知（Agent 会话或人工皆可，OCC 保护）。
3. **C 批交接**：cue 时间契约见 §10；正式 cue 定稿后由 C 批接线（本批已留占位与
   守卫）。

——以上，Phase 44B 完毕（成片定版与 select 两项待用户裁定后闭合）。
