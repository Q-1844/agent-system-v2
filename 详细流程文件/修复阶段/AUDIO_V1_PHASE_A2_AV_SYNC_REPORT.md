# AUDIO_V1_PHASE_A2_AV_SYNC_REPORT

Audio V1 — Phase A2：Dialogue Timing + Lip-sync Production + Duration / Speaker Adaptation
执行依据：`详细流程文件/修复阶段/16.md`
日期：2026-09-14
**状态：A2 收口——REAL E2E 已执行（3/3 成功，技术 17/17 PASS）+ 人工验收完成：技术链路成立但外挂 lip-sync 路线判定「不太行」，用户裁定方向修正为 Agnes 原生音频生成（见 `AGNES_AUDIO_DIRECTION_CORRECTION_REPORT.md`）。A2 全部资产搁置留档为 fallback primitive。**

---

## 1. Baseline

- 分支：`feat/scene-system-v1-s1`；A2 起点=A1 收口态（697ff97）
- Commit 划分（16.md §42）：A `ab345a6` models+timing/readiness → B `1e1fee3` pack+basis+store+freshness → C `d962f99` SyncLabs adapter → D `99d1511` service+race guard+sync_input+mux → E `f98487b`+本文档 测试+报告
- worktree：主仓库

## 2. Existing Video Truth Audit（16.md §1，2026-09-14 实查）

| 审计项 | 结论 |
|---|---|
| 正式 Visual Video 表示 | `Shot.final_video`（version_id 指针）→ `video_versions[]` 匹配项（status DONE + local 文件）。既有权威解析先例 = `project_intelligence._resolve_final_video`，A2 同口径只读复用 |
| legacy 并存 | 旧 `Shot.video` 已由 `ProjectStore.load()` 自动迁移为 `legacy_video` 版本 + 指针推断 → **加载后权威路径恒可确定，STOP A 不触发**；A2 新链只消费指针所选版本 |
| generation_basis | `VideoVersion` 无 basis 字段（Visual V1 冻结不添加）→ 视频身份 = **本地文件内容 sha256**（`source_video_fingerprint`，内容级身份更强）；STOP B 不触发（零 Visual schema 改动） |
| 字段清单 | version_id / video_id / task_id / local / url / seconds_returned / size_returned / model / status——时长与规格一律 ffprobe 实测（`seconds_returned` 不进 timing plan，§5） |
| S3C.2 entry | `build_bound_video_input`（src/generation/video_adapter.py）产出的是「生成请求」；生成成功后 handler 落 `video_versions` + `final_video` 指针 → **指针所选 DONE 版本 = production 应消费的视频真相** |
| 素材池 | 68 条 authoritative 视频（h264 1280x704 3.4–10s，全带混合 AAC + Agnes 公网 URL 不过期）——§31 probe 素材充足，无需重烧 Agnes |

## 3. A1 reuse confirmation（§2/§45.2）

- audio truth：selected `DialogueAudioVersion`（production WAV 48k/mono/s16）直接消费；`voice_profiles.json` / `dialogue_audio.json` / A1 freshness 语义零改动
- A1 freshness 作为 A2 生成门之一：selected audio stale → 拒绝生成 lip-sync（先修 audio）
- speaker 解析复用 A1（derived；多角色需显式裁决）——与 §10「谁说话≠哪张脸」分层：speaker 由 A1 解析，face target 由 A2 `LipSyncSpeakerTarget` 裁决
- 本阶段对 A1 冻结文件零修改（`src/audio/` A1 五件 + `tests/test_voice_a1_*` 均未触碰）

## 4. AVSyncReadiness（§4）

纯派生 / 零副作用（连续调用零文件变化，测试钉死）。分层 fail fast：speaker → audio → video → target → duration → provider。

| status | 语义 |
|---|---|
| ready | 可显式 generate |
| missing_audio / audio_not_approved / audio_stale | A1 audio 侧不可用（stale=先回 A1 显式 regenerate） |
| missing_video / video_not_ready / unsupported_video | 指针缺失或无匹配版本 / 状态非 DONE·文件缺失·ffprobe 失败 / 无视频流·时长非正 |
| no_speaker / ambiguous_speaker | A1 speaker 解析失败 |
| multi_face_target_required | 多角色镜未显式裁决画面目标（§11 fail closed） |
| video_too_short | audio 放不进 video（附 required_video_duration_ms） |
| unsupported_provider | provider 未实现（A2 仅 sync_labs） |

**命名适配说明**（16.md 允许）：§4 枚举的 VIDEO_STALE 语义 =「已存在 selected sync version 但其 source video/timing/target 已过时」——这是对既有 sync 的陈旧信号而非生成阻断（对新视频显式重新生成合法），故经 `existing_sync_freshness`（missing/current/stale + stale_reasons）暴露，生成门不吸收它。

## 5. DialogueTimingPlan（§5）

derived / ephemeral / deterministic / 无 business ID / 不持久化。字段全落实：dialogue_id、shot_id、audio_version_id、audio_duration_ms（ffprobe）、video_artifact_id、video_duration_ms（ffprobe）、speech_start_ms、speech_end_ms、lead_in_ms、tail_silence_ms、required_video_duration_ms、duration_relation、adaptation_mode、playback_ratio。**无任何 DB 猜测时长**。

## 6. Duration adaptation policy（§6/§7/§8/§9）

- **Case A（video ≥ audio）默认 `pad_audio`**：sync_input = [lead 静音][approved audio][tail 静音] ≈ video 时长；**完整视觉镜头保留，绝不截 video**。speech_start_ms 作为显式生成参数（默认 0，接口保留）
- **Case B（audio > video）`VIDEO_TOO_SHORT` fail closed**：返回 required_video_duration_ms = speech_start + audio + tail_handle——绝不自动裁对白/loop/bounce/加速/重新生成 Voice/偷偷截末句
- **`remap_video` 仅 caller 显式**：playback_ratio 进 basis；fits 时显式 remap 回落 pad（remap 无意义）
- sync_input 为 derived working artifact：以 provider generation id 命名于 `working/`，**绝不写回 dialogue_audio.json、绝不改 approved 产物**；adjusted word timestamps 可按 speech_start 派生，不改 A1 原始 timestamp
- 实现：纯 Python WAV 构造（48 samples/ms 整除无舍入；源规格校验 48k/mono/s16，不符拒绝——不静默重采样）

## 7. Speaker targeting（§10/§11）

`LipSyncSpeakerTarget`：`single_visible_speaker`（单人物镜默认，允许自动）/ `auto_detect`（必须 caller 显式，进 basis）/ `manual_target`（frame_number + coordinates，provider-specific 扩展槽）。V1 规则：单人物镜允许自动 targeting；多人物镜无显式裁决 → `MULTI_FACE_TARGET_REQUIRED` fail closed——**绝不静默同步「画面里最大的脸」，character_name 绝不伪装成 face coordinates**（测试钉死）。

## 8. LipSyncGenerationPack（§12）

derived / ephemeral / deterministic / 无 business ID / 不持久化 / 无 clock·random·temp path。结构：schema + shot_id/dialogue_id/speaker + source_video{artifact_id,duration,fingerprint} + source_audio{audio_version_id,duration,basis_fingerprint} + timing{start/lead/tail/sync_input_duration/adaptation_mode/playback_ratio} + speaker_target + provider_binding{provider,model} + generation_basis。

## 9. generation_basis（§13）

`av_sync_generation_basis_v1`，13 键：schema / shot_id / dialogue_id / source_video_artifact_id / **source_video_fingerprint**（文件 sha256）/ dialogue_audio_version_id / **dialogue_audio_generation_basis_fingerprint**（A1 basis 整体指纹）/ speaker_character_name / timing_fingerprint / speaker_target_fingerprint / provider / model / provider_options_fingerprint。actual dependencies only——绝不 hash project.json / 整份 resources.json / 整份 dialogue_audio.json；无关 Shot / 无关角色变化不影响 basis（测试钉死）。

## 10. SyncLabs adapter（§16/§17）

- **官方契约重核（2026-09-14，https://sync.so/docs/openapi.json 机器可读 spec）**：Base `https://api.sync.so`；Auth header `x-api-key`；`POST /v2/generate`（multipart/form-data：model 必填 + video/audio binary 文件直传 或 input JSON[url/assetId] + options JSON 字符串）；`GET /v2/generate/{id}` 轮询；status ∈ PENDING/PROCESSING/COMPLETED/FAILED/REJECTED；outputUrl/outputDuration/errorCode；模型枚举 sync-3 / lipsync-2 / lipsync-1.9.0-beta / lipsync-2-pro / react-1。**历史参数已过时，按 spec 如实重建**
- 模型经 `provider_binding.model` 传入（默认常量 `lipsync-2`，config/caller 可改）；**不做 heuristic routing**
- 文件直传 = 无需公网图床（Agnes 视频本有公网 URL，但直传路径对任意本地权威视频都成立）；输出恒重编 H.264（CRF17）——因此 §22 的 production mux 用我们自己的音频重铺

## 11. Provider lifecycle / error contract（§18/§19/§20）

- **sync_mode 映射（只此层，§36）**：`pad_audio → silence`（保留完整视频——provider 侧同义映射）；`remap_video → remap`（仅显式）；**bounce/loop/cut_off 绝不映射**（provider 默认 bounce，拒用——§18 禁令 + 测试钉死）
- **active_speaker_detection 映射**：auto_detect → `{auto_detect:true}`；manual → `{frame_number, coordinates[x,y]}`；single_visible → 不设
- **真实 async 生命周期**：submit（multipart 文件直传，300s 上传超时）→ generation id → poll（5s 间隔 / 30min 上限 / cancel_event 可取消）→ COMPLETED → 下载（预签名 URL，无 key）；**绝不做阻塞式假同步**；A2 服务层 async，JobQueue 接线留给未来 API 集成阶段（§27 无 HTTP/UI，不建第二套 Runtime）
- **structured error**：provider_not_configured / provider_request_failed / provider_generation_failed（FAILED+REJECTED，provider errorCode 透传）/ provider_timeout / provider_invalid_media / provider_download_failed + service 层 av_sync_not_ready / dependency_changed_before_commit / audio_preparation_failed / video_probe_failed / validation_error / multi_face_target_required / video_too_short
- key 语义 `None=env / ""=显式禁用`（A1 环境交叉教训同款）；构造性脱敏 + 测试钉死

## 12. DialogueVideoSyncVersion（§14）

artifact + provenance + review state。字段全落实（§14 建议清单逐项）：sync_version_id（`{dialogue_id}_sync{NN}` 单调不回收）/ shot_id / dialogue_id / speaker / source_video_artifact_id+fingerprint / source_audio_version_id+basis_fingerprint / provider / model / generation_basis / provider_generation_id / provider_metadata / raw_output_path / production_output_path / working_sync_input_path / video_probe / audio_probe / timing / speaker_target / created_at。

Store：`output/dialogue_video_sync.json`（与 A1 store 同构：atomic + project_write_lock + 读零副作用 + fail closed）；目录 `output/dialogue_video_sync/{raw,production,working,evidence}/`。

## 13. selection / approval（§15）

显式 `selected_sync_version_id` 指针（严禁 glob/mtime/newest——测试钉死悬空指针不顶替）；approval 独立人类门：**generate 不自动 approve**（新版本指针迁移但 approved=False，测试钉死）；select 换版本 → 回未批准；approve 只批当前 selected。

## 14. Freshness（§24/§25）

MISSING（无 selected/指针悬空）/ CURRENT（stored basis 与重算 basis 逐键一致）/ STALE（任一 actual dependency 变化：audio 换选、A1 audio basis 变、video 内容变、timing policy 变、speaker target 变、provider/model/options 变、production 产物文件缺失）。重算全程 **0 provider call**；无关 Shot / 无关角色变化保持 CURRENT（测试钉死）；**No Auto Re-Lipsync**：一切变化只能显式 regenerate（测试钉死 0 auto call）。

## 15. Race / mutation audit（§21/§42）

三段 guard：Phase 1 短（read→validate→snapshot→sync_input 构造，以 generation id 命名无碰撞）→ Phase 2 无锁（provider 分钟级任务绝不持锁）→ Phase 3 短锁（write_lock 内三重门：①basis 重算失败归一化 reject；②**A1 freshness 重查**——dialogue 文本/声线窗口内变化时 audio 记录 basis 不变但 A1 freshness 变 stale（审计补全的链路）；③basis 逐键对比）。不一致 → `dependency_changed_before_commit`，provider 输出移入 `evidence/` 作 diagnostics，**绝不 attach/selected/冒充 CURRENT**（三例测试 + 冒烟全过）。

Mutation audit：generate_lipsync 对 project.json 字节恒等；新增文件仅允许 voice/dialogue_audio/dialogue_video_sync 域（测试钉死）；A1 与 Visual V1 零语义改动（§3）。

## 16. Production mux（§22/§23）

`ffmpeg -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k -ar 48000`：provider raw mp4 原样留存 + **production review mp4 = provider 视频流 copy + 我们 approved 的 sync_input 音频**——review 听到的必须是我们 approved 的 DialogueAudio，不依赖 provider 是否重编码声音（§22 目标）；避免无意义二次视频编码。ffprobe 实测：video/audio duration、fps、resolution、video/audio codec。Agnes 混合 AAC 音轨不拆 stems（§23），mux 直接替换音频轨。

## 17. Automated tests（§28）

**55 passed / 0 failed**（三文件，全 fake provider + 真实 ffprobe/ffmpeg）：

| 文件 | 数 | 覆盖 |
|---|---|---|
| `tests/test_av_sync_a2_readiness_timing.py` | 19 | readiness 十态+ready、纯读零副作用、真 mp4 探测、timing 四例+determinism、speaker 五例、video truth |
| `tests/test_av_sync_a2_pack_freshness.py` | 17 | pack determinism/无 ID/basis 13 键/actual-deps-only、freshness 八例、no-auto、selection/approve、store fail closed |
| `tests/test_av_sync_a2_adapter.py` | 19 | sync_mode/ASD 映射、lifecycle 真契约、六类错误、race×3+evidence、mutation audit、key 纪律 |

## 18. Real probe（§29-§33）—— 2026-09-14 真机执行完毕

**REAL E2E EXECUTED — 3/3 生成全部成功，技术检查 17/17 PASS，预算精确 3/3。**

用户预算约束：免费档 3 次/月、单条 ≤20s。样本决策：3 calls = 3 样本最大化覆盖（A+D 合一、B/C、C2）；
sync-3 对照保留至技术失败时启用（实际零失败未动用）；multi-face 样本轮空（fail-closed 契约已有确定性测试）；
remap 本轮不测（§5）；audio>video 不烧 provider（deterministic policy 已证）。

| 样本 | shot | 源视频 | 台词（正式 A1 链 approved 音频） | audio | video | src=raw=prod | gen id | wall |
|---|---|---|---|---|---|---|---|---|
| A+D 主测试 | s01_c01 | gp_s01_c02（正面近中景/嘴清晰） | 「等一下，快好了，马上就好。」 | 3100ms | 4042ms | **4042=4042=4042ms** | ad38cfdf… | 232s |
| B/C 角度 | s01_c02 | gp_s03_c01（3/4 角度/正常动作） | 「信号稳定了，把数据全部传回去。」 | 3750ms | 5042ms | **5042=5042=5042ms** | c2c600e3… | 394s |
| C2 更难 | s01_c03 | sm_s01_c04（幼齿画风 3/4 张嘴） | 「爷爷，我们什么时候才能到家？」 | 2601ms | 5042ms | **5042=5042=5042ms** | 17aa6f50… | 293s |

技术结论（§5/§9/§10/§21 逐项）：
- **§5 duration 主测试成立**：三样本 src=raw=prod 逐毫秒一致——完整视觉镜头被保留（pad_audio + provider sync_mode=silence），没有因为对白较短而截视频
- 全部经正式 A2 service 链（readiness→plan→pack→adapter→submit→poll→download→dependency recheck→version→production mux），无任何绕过（§8）
- provider_generation_id / raw 留存 / production mux（h264 video copy + aac 我们 approved 的音频）/ basis 13 键 / working sync_input 全部在证（§9）
- generate 后未自动 approve（selection 语义保持，§9/§15）
- §10 freshness 真机抽查：s01_c01 = CURRENT（0 provider call）
- 素材零重烧：3 条视频全部来自既有 authoritative Agnes 视频池（§3）
- audio 侧全部经正式 A1 generation entry（阿默=male-qn-qingse / 小满=female-shaonv，approved+CURRENT，§4）

## 19. Human review（§34）—— 评审网站就绪，待用户验收

评审网站已上线：`http://localhost:8908/`（`_voice_a2_review_server.py`，双击 `_voice_a2_review.bat`）。
Source | Production 并排对比；每样本 PASS/ACCEPTABLE/FAIL + 十项重点快评（lip timing/mouth shape/teeth/face identity/jitter/edge·blending/angle robustness/expression damage/source identity/duration）+ 自由短评，反馈落盘 `REVIEW_FEEDBACK.json`。

**A2 最终判定（2026-09-15 用户评审落定）：REAL E2E 技术链路成立（3/3 成功、时长/artifact/provenance 全对），但外挂 lip-sync 成片质量用户判定「不太行」；结合 Agnes 原生配音的评审结果（口型同步好、声线同一性 5/4 分、音色参考成立），用户裁定方向修正为 Agnes 原生音频生成——A2 全部资产（models/store/adapter/service/55 测试/3 条真实样本）**搁置留档为 fallback primitive**，不删不改。详见 `AGNES_AUDIO_DIRECTION_CORRECTION_REPORT.md`。**

## 20. Regression（§41）

- A2 targeted：**55 passed / 0 failed**
- A1 voice targeted：76 项（全量内含）
- Visual frozen redline（S3B.1-3/S3C.1-2/baseview_profile_fp）：全量内含
- 全量回归：**1817 passed / 0 failed / 0 skipped**（756.43s；= A1 收口基线 1762 + A2 新增 55，收集总数恒等零新增 failure）

## 21. Deviations

1. **§4 VIDEO_STALE 命名适配**：作为 `existing_sync_freshness` 暴露而非生成阻断（语义见 §4 表述；16.md 允许命名适配）。
2. **readiness 检查顺序**：A1 speaker 门先于 multi_face 门——多角色镜连说话人都未裁决时先报 `ambiguous_speaker`（分层语义，两门各有测试钉死）。
3. **Phase 3 增加 A1 freshness 重查**（超出 §21 最低要求）：dialogue 文本/声线窗口内变化时，audio 记录的 basis 指纹不变，仅靠 basis 对比会漏放——重查补全链路（race 测试钉死）。
4. **sync_input 以 provider generation id 预命名**（working/），commit 时更名正式 sync_version_id——避免并发生成碰撞且不引入时钟。
5. **测试侧**：FakeSyncAdapter 回放真实 mp4 字节使 mux/ffprobe 真实可跑；跨测试文件导入按 repo 惯例 `from tests.test_xxx import`。

## 22. Blockers

- 真机 probe 唯一阻塞 = `SYNC_API_KEY` 未配置（用户侧凭证）；架构/实现/测试/回归全部就绪，key 到位即可跑（§18 驱动说明）。

## 23. Recommendation

**A2 IMPLEMENTATION PASS**：authoritative video truth 复用成立、A1 audio truth 复用成立、readiness 纯派生、timing deterministic、Case A padding 正确、Case B fail closed、speaker target 不猜、pack deterministic、SyncLabs adapter 按官方 spec 成立、async 生命周期正确、race commit guard 成立、sync version 完整 provenance、显式 select/approve、freshness actual-dependencies-only、no auto re-lipsync、production review 使用正式 DialogueAudio、A1 与 Visual V1 零回归、targeted tests PASS、全量回归无新 failure（§39 成功标准 1-19 全部满足）。

**下一步（16.md §44，不自动继续）**：A3 = Timeline + Music + Agnes ambience/SFX + Final Mix——需新阶段规格。A2 真机 probe 在 SYNC_API_KEY 到位后补跑（4-6 条）。
