# Phase 44B 设计决策记录（B0，先于编码）

日期：2026-10-01。执行：zcode（B 批次）。
依据：`详细流程文件/新阶段1/PHASE44_B_基础成片Agent.md` + `PHASE44_A_REPORT.md`（含 §11）。

## 0. 开工审计结论（决定选型的仓库事实）

| 审计项 | 结论 | 证据 |
|---|---|---|
| Sequence 装配域 | **已存在且闭合**：S1（hard-cut 装配/trim/basis/freshness/render 版本/job）+ S2（AudioPlan 混音层）全部 IMPLEMENTED，全量回归绿 | `src/sequence/`（models/store/timeline/basis/media_ops/service/audio_*）+ `routers/sequences.py`；SEQUENCE_S1/S2 报告 |
| 渲染 job 通道 | `sequence_render` kind 已注册 JobQueue（轻 worker 3 并发、持久化、看门狗、崩溃恢复、SSE） | `routers/sequences.py:74`、`src/core/job_queue.py` |
| 源真相 | Sequence 只认 `Shot.final_video → VideoVersion → local 文件`，禁 glob/末位/mtime 回落；basis 逐 clip 全文件 sha256 + revision + profile 指纹 | `src/sequence/basis.py:37-95` |
| 44A 冻结快照 | 正式快照 `snap_44a_formal_20261001_001753`，13/13 user_locked，manifest 15 字段/镜；本批实测 live 文件 sha = 镜像 sha = 登记哈希 **13/13 三方一致**；project.json 的 13 个 final 指针与 manifest **逐一吻合** | `_44a/assembly_input_manifest_formal.json`；本批 `_44b1` 前置核验 |
| 素材异构性 | 11/13 镜 1280x720，2/13（s01_c04、s02_c02＝44A 回拨镜）1280x704；全部 h264 yuv420p 24fps + aac **32kHz** stereo，start_time=0，tbn 1/12288 | 本批 ffprobe 全量实测 |
| 既有全局渲染 profile | `SEQUENCE_RENDER_PROFILE = 1280x704@24`，冻结自 Native AV 时代源现实——**与本片素材（720 为主）不符**：若直接用会 11/13 镜两侧加 14px 黑边 | `src/sequence/models.py:42-52` |
| Agent 系统 | 9 身份硬编码 registry，加法式扩展纪律（scene_designer 为先例）；工具=OpenAI function schema + TOOL_EXECUTORS；OCC sha256-8 指纹；真相闸门/单写串行/回执账本镜像于各 agent 模块 | `src/agents/registry.py`、`scene_designer.py` |
| V2 审计定位 | §13：Sequence 装配=IMPLEMENTED 而 **Assembly Agent=ABSENT**；§16 Canonical Ownership Matrix：Sequence 行「未来 Assembly Agent 只读消费」；§15：高层任务型工具 10-14 个起步、OCC 必须透传 | `AGENT_SYSTEM_V2_CURRENT_STATE_AUDIT.md` |
| 导出三路并存 | legacy compose / manifest autopilot / Sequence render（Blocker B5）——本批**不收敛**，但成片能力只挂 Sequence 一路，并在报告声明 | 同上 §13/B5 |

## 1. 三种方案与选型

### 方案一：纯脚本拼接（把 `_44a3_seams.py` 式脚本扩成成片脚本）
- 做法：脚本读 manifest → ffmpeg concat → 输出 mp4。
- 优点：最快，零新概念。
- 拒绝理由：无 canonical（顺序/trim 无真相、无 revision）、无 OCC/审计/回执、渲染不可恢复不可重试（进程死=孤儿 mp4，恰是 B4.4 明确不接受的状态）、Agent 无可审提案面（B2 要求的「读取→草案→检查→渲染→观察→修订→提交评审」闭环无处落）、能力不复用（Sequence 域 8 端点+basis+freshness 全部白建）。与「不复制平行真相」直接冲突。

### 方案二：在现有 Director 里增加导出命令
- 做法：给 `src/agents/director.py` 加 assembly.* 工具。
- 拒绝理由：
  1. **所有权矩阵冲突**：V2 §16 明确 Sequence 域「未来 Assembly Agent 只读消费」；Director 是分镜（storyboard）唯一写者，混入成片写域会让一个会话同时持两把资源锁、修改面不可回滚。
  2. **生命周期错配**：Director turn 是对话式短事务；render 是可恢复的持久 job（崩溃恢复/receipt/重试），挂在 turn 内会让「会话已死、渲染还在跑」成为常态歧义。
  3. 回归面：Director 全部测试+前端分镜页都会被波及，违反冻结纪律。

### 方案三（选定）：独立 Assembly Agent + 确定性时间线工具，全部经既有 Sequence 域
- 新 Specialist `assembly`（加法式注册），工具面 11 个高层工具（§3）；**没有任何工具能触到 ffmpeg/shell**——ffmpeg 只存在于 `src/sequence/media_ops.py` 与 job handler 的确定性代码中。
- canonical 编辑面 = `SequenceBlock`（顺序+trim+per-sequence 渲染 profile，OCC revision）；成片输入契约与时间线投影 = **派生文档**（AssemblyManifest，§2），由确定性工具生成，LLM 不得手写。
- render 走既有 `sequence_render` job kind：持久化/进度 SSE/看门狗/崩溃恢复/receipt 全部白得；重试=显式新 render version（版本化输出，绝不覆盖旧输出）。
- 选型理由：满足 B0「复用优先、不建平行真相」；满足 B2 全部边界；B4.4 的中断恢复直接继承 JobQueue 既有机制；下一批（C）的声音接线点（SequenceAudioPlan）已在同一域内。

## 2. 数据契约与真相分工（防第二真相）

```
Shot.final_video ──(唯一源真相，Sequence §7)──▶ VideoVersion.local（实际文件字节）
       ▲                                            ▲
       │ 44A manifest pin（version_id+sha256+probe） │ 本批三方哈希核验已过
       │                                            │
AssemblyManifest（派生文档，append 版本化）          SequenceBlock（canonical）
  snapshot_id + assembly_revision +                clips[shot_id,trim_in,out]（OCC revision）
  逐镜 video_version_id/media_sha256/              render_profile（新增，进 basis 指纹）
  source_in/out + timeline_start +                 ──render──▶ SequenceRenderVersion（append，
  transition/scene_boundary + seam_notes                       done/failed/raced，输出版本化不覆盖）
  + audio_strategy + narration_cues[] + music_bed
  + render_profile（与 block 一致性校验）
```

- **SequenceBlock 是唯一 canonical**；AssemblyManifest 是确定性投影+快照 pin 的可评审载体（`output/assembly/manifest_v{N}.json`，只追加），构建器 fail-closed 校验：`source_out > source_in` 且不超源实测时长、排序决定总时长、版本/哈希与冻结快照逐一相等、与 block.revision 绑定。任何不符 → 结构化错误（shot_id + 期望/实际），不偷换素材。
- `narration_cues`/`music_bed` 本批恒为 `[]`/`null` 占位（C 批次写入），字段留在 manifest 保证契约前向兼容。
- 成片输出：`projects/雨灯归航/output/sequences/{seq_id}/{srender_id}.mp4`。

## 3. Assembly Agent 工具面（11 个，T-A 高层任务型）

**只读（不占写槽，可并发）**
1. `assembly.briefing` — 项目+冻结快照+全部 sequence+render 状态一览（入口定位）
2. `assembly.snapshot_read` — 读取冻结快照 manifest（全量/单镜）
3. `assembly.sequence_read` — SequenceBlock + render versions + freshness + 音轨策略
4. `assembly.media_probe` — 对任意列出的媒体文件 ffprobe 实测（含 manifest 逐镜批量模式）
5. `assembly.timeline_preview` — **dry-run**：给定 clips 草案（不改 canonical）解析+探测+算 timeline+不变式校验，返回逐镜起止表与违规清单
6. `assembly.render_status` — render version/job 状态+输出诊断（时长偏差/黑帧/静音/接缝检查摘要）

**写（经 SequenceService 正式链路，单写串行，OCC 透传）**
7. `assembly.sequence_create` — 从冻结快照顺序创建 SequenceBlock（前置快照对齐核验）
8. `assembly.sequence_update` — 改 clips/trim/profile（`expected_revision` 409 OCC）
9. `assembly.render_submit` — 显式 render：append queued render version + JobQueue 入队，返回 job_id/render_version_id（回执含真实任务号）
10. `assembly.manifest_publish` — 确定性构建/追加 AssemblyManifest 新版（assembly_revision 自增；非 LLM 手写）
11. `assembly.proposal_write` — 写可评审提案文档（顺序/留白/trim 依据/接缝注记/旁白候选→`output/assembly/proposal_v{N}.md`）

**明确不给的工具（物理边界，非提示词）**：无任何视频生成工具；无 Shot/Story/Look/SIV 写工具；无 `final_render` select 工具（select 是用户显式动作，走既有 REST API）；无 ffmpeg/shell 入口。

## 4. 关键字段与语义决定

### 4.1 per-sequence 渲染 profile（本批新增，加法式）
- `SequenceBlock.render_profile: Optional[dict]`，None = 既有全局 `SEQUENCE_RENDER_PROFILE`（S1 语义逐字节不变，旧行 known-field 过滤兼容）。
- 本片 profile：`1280x720@24 + libx264 yuv420p + aac 48k stereo + fill=crop`——依据：仓库现有标准=素材现实（11/13 为 720；video 2.5 档）；2 镜 704 素材按 **scale-to-cover+中心裁切**（高度 704→720 放大 2.3%、两侧共裁 ~1.1% 宽）保满幅无黑边、保持画面比例；若用 pad 则 2 镜上下各 8px 黑边在硬切处可见。两方案均在报告中列明，fill 策略显式入 profile 并进 basis 指纹。
- `audio` 策略同样入 profile：`native`（保留原生环境声，默认）| `silent`（整片静音轨）。B3「两策略」落地；进 basis → 策略变化=诚实 STALE。
- 不放大信息：不使用任何 >1.03x 的放大档；48kHz 重编码仅容器规格统一（源 32kHz）。

### 4.2 音轨
- 13/13 源带 aac 流（甲-2 内容近静音，用户听审已确认无背景乐）。B 批成片默认 `native`；另渲染一版 `silent` 作「完全不依赖 TTS/任何音频生成」的交付样。**不添加任何 BGM**（杜绝原生声与未来统一 BGM 叠加——C 批次接 cue 时再由用户定 ducking/替换策略）。

### 4.3 渲染任务生命周期
创建：`render_submit` → `create_render`（append queued）→ `JobQueue.submit("sequence_render")`；入队失败→行回标 failed（无幽灵 queued，既有 handler 语义）。
恢复：job 持久化 `cache/_44b_jobs.json`（驱动器隔离文件）→ 进程重启后 light worker 重推 queued/running→ Phase 2 重跑（中间段在 `_work/`，重跑即重建）。
重试：**永远新 render version**；失败行保留 error；绝不覆盖已 done 输出；`raced_superseded` 输出保留为 evidence。
凭证：「已渲染成功」必须回执=render version 行（status=done）+ 输出路径 + ffprobe 实测 + basis 一致；孤儿 mp4 不算完成。

### 4.4 用户终审环节（不可自动化）
① 剪辑提案（顺序/trim/转场建议）看 proposal 后确认；② 成片预览（评审页）看片；③ `final_render` select（用户显式 API 调用）；④ 交付成片只能来自正式快照（本片已是 formal；若快照回退工程态，成片必须标注「工程预览」）。

## 5. 不做清单（任务书明确排除）
复杂转场库、自动配乐生成、镜头内对白对嘴、自动字幕版式、自由拖拽 UI、BGM/旁白接线（C 批次）、三路导出收敛（D/后续）、S3 场景级 resolution/分镜级 re-anchor。
