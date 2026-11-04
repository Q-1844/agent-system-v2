# AUDIO_V1_PHASE_A1_VOICE_FOUNDATION_REPORT

Audio V1 — Phase A1：Voice Foundation + MiniMax Speech 2.8 Turbo Validation
执行依据：`详细流程文件/修复阶段/15.md`
日期：2026-09-14
**状态：VOICE FOUNDATION A1 IMPLEMENTATION PASS + 人工听评通过（VOICE QUALITY 确认）**

---

## 1. Baseline

- 分支：`feat/scene-system-v1-s1`
- 起点 HEAD：`a9118a0`（A1 实现+自动测试提交；随后 adapter 三处官方契约增补与真机结果同一提交收口）
- worktree：主仓库（无独立 worktree）
- Visual V1 冻结基线：1537 passed / 0 failed / 0 skipped（`v1-frozen` 收口态）
- 本阶段对 Visual 冻结域源文件改动：**0 个文件**（全部新增 `src/audio/` + 4 个测试文件）

## 2. Existing Dialogue Truth Audit（15.md §3 全部问题）

| 审计问题 | 结论 |
|---|---|
| stable Dialogue ID？ | **无独立 Dialogue 实体**。对话真相 = `Shot.dialogue`（`src/core/models.py`），repo 自声明其为「镜头 canonical 创作字段，TTS 配音创作输入」（`src/storyboard/structure_service.py:341-343` 白名单注释） |
| speaker 如何表示？ | **无 speaker 字段**。Director 契约「可带说话人」（文本内嵌）；角色关联 = `Shot.characters: List[str]`（角色名） |
| canonical text 在哪？ | `Shot.dialogue`。写入点三处：用户（`routers/shots.py:1575` 手改、248/259 split-dialogue）、Director agent（`SHOT_UPDATABLE_FIELDS`）、structure_service 白名单。**Script Writer 只写 story_text，不碰 dialogue** |
| language？ | 不存在 |
| delivery/performance intent？ | 不存在 |
| revision / fingerprint？ | 无 dialogue 专用指纹（Director 有 shot 内容指纹但非 dialogue 专用）→ Voice 侧 derived 指纹解决，不写回 canonical |
| dialogue text 是否被复制？ | **无重复复制**：不进图片 prompt（prompt_builder 不消费）、subtitle 消费 audio words、timing 只用音频时长。单一真相成立 |
| Character 身份 | `Character` 无 `character_id`，全项目身份原语 = **character_name**（`src/resources/models.py:13` Resources V1 冻结例外先例） |

### 强规则判定

- **已有 authoritative Dialogue truth（Shot.dialogue）→ MUST reuse：已复用**。Audio 模块对它是纯读，不建 `tts_text/speech_text/audio_text` 第二套真相。
- **DialogueLine 最小实体评估**：另建实体 = mirror `Shot.dialogue`（违反 §3A 强规则）或改造全部 3 个写入路径 + Director + 内容指纹体系（= 大规模 migration → STOP A）。**不建，复用成立，未触发 STOP A**。

## 3. Architecture Decision

四个实体职责不同，各归其位：

```
Shot.dialogue            what is said（canonical，Audio 只读）
CharacterVoiceProfile    who this person sounds like（canonical，voice_profiles.json）
VoiceGenerationPack      deterministic generation projection（derived/ephemeral，不持久化）
DialogueAudioVersion     generated artifact + provenance（dialogue_audio.json）
```

- dialogue_id = **shot_id**。这不是「shot_id+index 伪造」：本项目业务模型中对话与 Shot 1:1（多句台词 = split 产生新 shot、新身份证号 id）；shot_id 是项目唯一身份原语（`structure_service.py:6-8`：插入不重排、删除不回收、move 不改 id），index 只是显示号、不参与身份。
- **speaker 解析（derived only）**：`Shot.characters` 恰好 1 个 → 该角色；0 个 → NOT_READY(no_speaker)；多个 → NOT_READY(ambiguous_speaker)，可由显式 `speaker` 参数裁决（必须 ∈ 本镜角色名单，进 dialogue_fingerprint 与 generation_basis——是生成依赖，不是第二真相）。

## 4. Canonical Stores

- `projects/{project}/output/voice_profiles.json` — CharacterVoiceProfile（§6）
- `projects/{project}/output/dialogue_audio.json` — DialogueAudioVersion artifact store（§19）
- `projects/{project}/output/dialogue_audio/raw/*.mp3` — provider original（§17A）
- `projects/{project}/output/dialogue_audio/production/*.wav` — production normalized（§17B）
- `projects/{project}/output/dialogue_audio/evidence/`、`listening/` — 证据与听评包

复用项目既有模式（未发明第二套并发架构）：`src/core/atomic.atomic_write_json`（原子写）+ `src/core/write_lock.project_write_lock`（同 project_name 锁族，与 project.json / resources.json / project_profile.json 写路径同族）+ load_or_none 读零副作用 + 坏 JSON fail closed（VisualProfileSchemaError 同构 → VoiceSchemaError）。

## 5. OCC / Revision

- revision = **sha256(canonical_json(voice_semantics_snapshot))[:8]**（同 visual_profile 指纹族）。
- 语义子集 = provider / provider_voice_id / default_model / language_boost / speed / vol / pitch（§21 actual dependencies only）。**label（display）与 approval_status（生成门）不进指纹**——改 label 不触发 STALE（测试钉死）。
- update/approve：expected_revision check-then-write，stale → reject 不 silent retry（visual_profile 同口径；sync 单写者上下文 + async 侧持 project_write_lock）。

## 6. Voice Readiness（derived only，绝不写 canonical status）

| 状态 | 判定 |
|---|---|
| UNBOUND | speaker 角色无 VoiceProfile |
| NOT_READY | dialogue_not_found / empty_dialogue / no_speaker / ambiguous_speaker / invalid_speaker / profile_not_approved / provider_voice_id_missing / unsupported_provider |
| READY | Dialogue + approved VoiceProfile 均可确定性解析 |

自动测试钉死 readiness 纯读零副作用（连续读不创建任何文件）。

## 7. Generation Pack

`VoiceGenerationPack`：derived / ephemeral / read-only / 无 business ID / 不持久化。字段：dialogue_id、dialogue_fingerprint、speaker_character_name、**text（canonical 原样，§15）**、language（占位 seam，Dialogue 级 language 字段 canonical 未存在）、delivery_intent（§11 占位 seam，本阶段绝不解释）、language_boost、voice_profile_id/revision、provider_binding{provider, provider_voice_id, default_model}、synthesis_defaults{speed, vol, pitch}、generation_basis。同输入 → 同 pack（自动测试钉死 determinism）。

## 8. MiniMax Adapter（真实 request mapping）

- `POST https://api.minimax.cn/v1/t2a_v2`（官方 OpenAPI servers 一致；国内直连）
- `model=speech-2.8-turbo`（§0 正式默认）、`stream=false`
- `voice_setting{voice_id, speed[0.5,2], vol(0,10], pitch[-12,12]}`（范围与官方 schema 逐字核对，双重守卫）
- `audio_setting{sample_rate:32000, bitrate:128000, format:mp3, channel:1}`（请求值；**实际输出每次 ffprobe 实测，不 hardcode**）
- `language_boost`（profile 携带时下发；官方枚举 `Chinese`）
- `subtitle_enable=true, subtitle_type=word`（词级时间戳经响应 `data.subtitle_file` URL 下发 → 已下载留证）
- `pronunciation_dict`（官方语法 `原文/(pin1)(yin1)`）作为**显式 caller 参数**（C3 测试 fixture 用；Adapter 绝不从生产 Dialogue 文本自动注入，§16）
- Text 原样：request.text = pack.text = Shot.dialogue 原样（自动测试钉死）

## 9. Audio Artifact Policy

- **raw**：provider 原始字节直接落盘 `raw/{audio_version_id}.mp3`（同 id 重写 = 程序错误，拒绝）
- **production WAV**：deterministic ffmpeg（`-ac 1 -ar 48000 -sample_fmt s16 -f wav -map_metadata -1`）→ PCM s16 / 48kHz / mono，未来 lip-sync/timeline/mix/mux 统一消费
- 每次生成 **ffprobe 双份实测**（raw + production），失败记录 `{"error": "ffprobe_unavailable"}`，不伪造数据
- 🔴 音频处理路径解码 bug 修复（本阶段实录）：ffprobe JSON 的 filename 字段含项目路径（中文用户名），默认 GBK locale 解码 UTF-8 输出会 UnicodeDecodeError → 已显式 `decode("utf-8", errors="replace")`

## 10. generation_basis（完整）

```json
{
  "schema": "voice_generation_basis_v1",
  "dialogue_id": "…",
  "dialogue_fingerprint": "sha256({dialogue_id,text,speaker})[:8]",
  "speaker_character_name": "…",
  "voice_profile_id": "…",
  "voice_profile_revision": "sha256(voice_semantics)[:8]",
  "provider": "minimax",
  "model": "speech-2.8-turbo",
  "provider_voice_id": "…",
  "synthesis_settings_fingerprint": "sha256({language_boost,speed,vol,pitch})[:8]"
}
```

无 clock / 无随机 UUID / 无临时路径；deterministic / inspectable（自动测试钉死）。

## 11. Freshness（MISSING / CURRENT / STALE）

- MISSING：无 selected audio（含选择指针悬空——绝不 glob 最新文件顶替，测试钉死）
- CURRENT：selected version 的 generation_basis 与当前 Dialogue+VoiceProfile 重算结果逐键一致
- STALE：任一 actual dependency 变化（text / speaker / profile 语义 / provider voice / settings）
- **actual-dependencies-only**：只比本句 Dialogue + 本 speaker 的 profile 语义子集；无关角色变化绝不 false-stale（测试钉死 Bob 变 Alice 不动）；不比整份 voice_profiles.json 文档
- **no auto regenerate**：STALE 后连续 freshness/list 只读操作 provider call = 0（测试钉死）；regenerate 只能显式调用

## 12. Provider Error Contract

structured `VoiceProviderError`（绝不信封成普通 500 字符串）：
`provider_not_configured / provider_request_failed / provider_invalid_voice / provider_quota_exceeded / audio_decode_failed` + service 层 `voice_not_ready / dependency_changed_before_voice_generation / audio_normalization_failed / validation_error / stale_revision / …`。
detail 保留 provider status_code / status_msg（截断）/ **trace_id** / model / voice_id；未命中错误码映射一律 `provider_request_failed` 且 raw code 保留；key 零出现（构造性保证 + 测试钉死）。

## 13. Automated Tests

**76 passed / 0 failed / 0 skipped**（全 Fake Provider，不联网），四个文件：

| 文件 | 数 | 覆盖 |
|---|---|---|
| `tests/test_voice_a1_profile.py` | 22 | create/get/list/update/approve、OCC stale、invalid character/voice_id/source_kind/speed、label 不改 revision、fail closed（坏 JSON/未知 schema）、指纹纪律 |
| `tests/test_voice_a1_readiness_pack.py` | 17 | UNBOUND/NOT_READY×7/READY、derived-only 零副作用、pack determinism、text 原样、speaker/voice 正确、basis 完整可检查 |
| `tests/test_voice_a1_adapter.py` | 16 | request mapping、secret 构造性脱敏、hex 解码、错误映射（1008/2049/未知码/空音频/坏 hex/网络/非 JSON/无 key=0 请求）、词级时间戳 |
| `tests/test_voice_a1_generation_freshness.py` | 21 | artifact/raw+wav/usage/metadata、mutation audit、freshness 全例（含 unrelated 零 false-stale、0-call、悬空指针）、race 三例、selection/approve |

## 14. Real MiniMax Tests（scratch `projects/__voice_foundation_a1`）

| 组 | 内容 | calls | 结果 |
|---|---|---|---|
| P0 | 真实 get_voice（`POST /v1/get_voice`，voice_type=system）**303 个在册音色**；male-qn-qingse / male-qn-jingying / female-shaonv 全部在册校验 | 0（不计） | PASS |
| P1 | 双 Profile create+approve → READY | 0 | PASS |
| A（male 陈默） | same text ×3（我们现在出发。）+ diff ×4（等一下。/门锁住了。/风越来越大。/把灯打开。） | 7 | PASS |
| B（female 林晚） | 同 A | 7 | PASS |
| C1 | 长句 56 字 | 1 | PASS |
| C2 | pause `<#0.4#>`：词级时间戳实测 **gap=500.0ms**（0→1304.8ms「我们等到天亮。」→1804.8ms「再走。」） | 1 | PASS |
| C3 | 多音字「主角」plain vs 官方 pronunciation_dict `主角/(zhu3)(jue2)` | 2 | PASS |
| C4 | 语气词 `(sighs)`（2.8 官方 tag 表内） | 1 | PASS |
| D | freshness E2E：D1 生成→CURRENT；D2 文本变→**STALE/0 call**；D3 显式 regenerate→CURRENT；D4 换声 male-qn-qingse→male-qn-jingying→**STALE/0 call**；D5 regenerate→CURRENT | 2 | PASS |

**成功 TTS calls = 22**（≈20 目标、24 硬上限内；C3 双样本比预算多 1）。
执行器：`_voice_a1_real_driver.py`（scratch 直驱）+ `_voice_a1_postpass.py`（零 call 修正）。
首跑 2 个 FAIL 均为驱动侧测量问题并已修正（见 §22 Deviations 5/6），未重烧 call。

## 15. Provider Evidence

- model：`speech-2.8-turbo`（22/22）
- voice_id：`male-qn-qingse` / `male-qn-jingying` / `female-shaonv`（全部真实 get_voice 在册）
- latency：min 2233ms / max 5099ms / mean 3491ms
- usage_characters：单 call 7–107，总 480
- audio spec（ffprobe 实测）：raw = mp3 / 32000Hz / mono（与 §0 冒烟契约一致）；production = pcm_s16le / 48000Hz / mono
- trace_id：22/22 捕获入 artifact metadata（`provider_extra_info.trace_id`）
- 词级时间戳：subtitle_file URL 全量下载留证（`evidence/*_subtitle.json`，schema = segments[].timestamped_words）
- 证据文件：每 call `*_request_snapshot.json` / `*_provider_response_metadata.json` / `*_ffprobe.json` / `*_subtitle.json`（**无任何 key**）

## 16. Cost

- total usage_characters = **480**
- report-time estimate：按本轮官方 Turbo 价格 $60/1M chars ≈ **$0.0288**
- 价格未写入生产代码（canonical 只存 usage_characters）

## 17. ASR / CER（faster-whisper small，probe 工具，不进 production dependency）

| 组 | CER |
|---|---|
| A 同文本×3 | 0.000 / 0.000 / 0.000 |
| A diff ×4 | 全 0.000 |
| B 同文本×3 | 全 0.000 |
| B diff ×4 | 全 0.000 |
| C1 长句（56 字） | 0.118（whisper 误听「攥紧→钻紧」等 ASR 侧误差） |
| C2 pause | 0.000（target 已去 `<#0.4#>` 标记） |
| C3 plain / override | 0.000 / 0.000（读音正确性属人耳判定） |
| C4 (sighs) | 0.000（target 已去语气词标签） |
| D 组 | 0.000 |

**16 条中 15 条 CER=0.000**。v1 首转 male 组出现繁体伪差（whisper 解码偏好，非 TTS 错误）→ v2 `initial_prompt` 简体偏置重转修正；v1 结果保留 `cer_results.json`，v2 = `cer_results_v2.json`。

## 18. Listening Pack

- `projects/__voice_foundation_a1/output/dialogue_audio/listening/male_voice_continuity.wav`（7 段：same×3 + diff×4，段间 0.7s 静音，1.20MB）
- `projects/__voice_foundation_a1/output/dialogue_audio/listening/female_voice_continuity.wav`（同构，1.19MB）
- `projects/__voice_foundation_a1/output/dialogue_audio/listening/LISTENING_MANIFEST.md`（逐段 dialogue_id / audio_version_id / voice_profile_id / text）
- 同文本×3 时长散布（机测参考）：male 19.9%（av02 1440ms 为离群）、female 7.5%——自然生成方差；**同一性判断 = 用户人耳**

### 听评网站（scratch 工具，已上线并完成一轮评审）

`_voice_a1_listen_server.py`（standalone http.server，端口 8907，双击 `_voice_a1_listen.bat`）：逐段播放 + 评语按钮（可多选）+ 自由输入，反馈实时落盘 `listening/FEEDBACK.json`。零生产依赖、零冻结域触碰；后续需人耳评审的阶段（Agnes 音频架构 25 组 montage、未来表演/混音评审）复用此模式。

### 人工听评结果（2026-09-14，用户真实提交，25 条反馈）

| 评审项 | 用户判定 |
|---|---|
| 男声 7 句连续性 | **同一角色** ✓ |
| 女声 7 句连续性 | **同一角色** ✓ |
| 整体音质 | **Turbo 音质够用** ✓ |
| 19 段逐段（A/B/C/D 全部版本） | 全部「自然」，零负面标签（无不像同一人/音色崩坏/不自然/咬字含糊） |
| C3 读音（plain vs override） | 「读音相同」——人耳未分辨差异（默认读音已正确） |
| C1 长句旁白 | 「旁白这样已经非常可以了」 |

用户自由评语（原话，诚实留档）：
1. 「我认为这个不需要再测试了，完全是没问题的，我估计重点搞错了」——TTS 音质方向确认收口，无需再投测试。
2. 「我认为是可以的，就是有点像人机的声音，不过目前最重要的是要实现音画同步问题」——已接受，但存在轻微合成感（记录，不阻塞）；**用户指示下一重点 = 音画同步**。
3. 「旁白这样已经非常可以了。」

**VOICE QUALITY：人工听评通过（此前的 PENDING HUMAN REVIEW 已落定）。**

## 19. Freshness E2E（真机链）

```
generate（D1）        → CURRENT
dialogue text change  → STALE（provider call = 0，无自动再生成）
explicit regenerate   → CURRENT（av02，指针显式迁移）
voice profile 换声    → STALE（provider call = 0，无自动再生成）
explicit regenerate   → CURRENT（av03）
```

## 20. Mutation Audit（§42/§45）

- **自动测试字节级**：五冻结 store（project.json / resources.json / scene_system.json / project_profile.json / shot_production_bindings.json）占位落盘参与 sha256 审计 → generate 前后既有文件字节恒等；新增只允许 voice 自己的 store 与音频文件
- **真机逐字段比对**：project.json 的 dialogue/characters/scenes/story_text/style 全部与 bootstrap 预期恒等（s04_c01 即 D2 显式编辑后文本）；voice 代码对 project.json **零写入**
- Voice 只写：voice_profiles.json、dialogue_audio.json、dialogue_audio/ 音频与证据

## 21. Regression（§43）

- Voice targeted：**76 passed** / 0 failed
- Visual frozen redline（S3B.1/S3B.2/S3B.3/S3C.1/S3C.2/baseview_profile_fp）：**184 passed** / 0 failed（实现后、真机前实跑）
- 全量回归：**1762 passed / 0 failed / 0 skipped**（709.47s；含冻结基线 1537——收集总数与基线恒等零回归）
  - 首跑 1761+1 失败（`test_provider_not_configured_zero_requests`，环境交叉，见 §22.8）→ 修复后复跑 1762/0

## 22. Deviations

1. **`character_id` → `character_name`**：15.md §7 语义要求 VoiceProfile 只经 character 引用 Character；本项目 Character 无 character_id（Resources V1 冻结决策先例），故 FK 字段名如实为 `character_name`（语义等价：项目唯一角色身份原语）。
2. **不建 DialogueLine，dialogue_id = shot_id**：§3B 允许局部补实体，但任何实体化都会 mirror Shot.dialogue（第二真相）或触发大规模写入路径迁移（STOP A）。复用 authoritative truth + 稳定 shot 身份是唯一不违 §3A 的解。speaker 为 derived 解析（1 角色即说者 / 多角色需显式裁决）——生产级 speaker 字段留待未来 Dialogue 结构化阶段。
3. **显式 generate 自动把 store 选择指针迁到新 audio_version_id**（approved 门独立）：15.md §20 禁的是「filesystem newest wins / glob 猜测」；本实现指针是 store 内显式 artifact id，lip-sync 未来消费 `selected_audio_version_id`（自动测试钉死悬空指针 → MISSING 不顶替）。
4. **adapter 新增显式 `pronunciation_dict` 参数**（官方 capability；C3 测试用）：不进 pack、不从文本自动注入（§16 seam 合规）；trace_id 与 subtitle_file_url 增补进 provider metadata（官方响应真实字段）。
5. **真机首跑 2 FAIL 均为驱动测量问题**（非架构/供应商问题）：C2 解析字段名与 subtitle 实际 schema 不符 → 按真实 schema 重解析得 gap=500ms 实证；mutation audit 基线取在 D2 显式编辑之前 → 修正为「voice 写入为零」的正确语义（D2 是测试扮演用户的合法 canonical 编辑）。修正全部零 TTS call（post-pass 本地重算）。
6. **TTS calls = 22**（≈20 目标、24 硬上限内）：C3 双样本（plain+override）。
7. **CER v1 → v2**：繁体解码伪差属 ASR 工具侧，非 TTS 质量问题；两组结果都保留可查。
8. **adapter key 解析语义修正**（全量回归首跑唯一失败 `test_provider_not_configured_zero_requests` 的根因）：`api_key=""`（falsy）曾回退读 environ，而 session 级 `client` fixture 会经 api_server startup 把 .env 灌进 environ（与 Freeze Stabilization 记录的 deps 环境污染同族）→ 单跑过/全量挂。修正为 `None`=读环境、`""`=显式禁用（caller 裁决优先于环境），并在人为污染（env 预置 key）下验证通过。

## 23. Recommendation

**Voice Foundation architecture：PASS READY。**
Dialogue 复用单一真相、CharacterVoiceProfile canonical + OCC、READY 门、deterministic Pack + basis、actual-dependencies-only freshness、显式再生成、显式 selection/approve、Visual 冻结域零触碰——自动测试 + 真机全链验证成立。

**MiniMax speech-2.8-turbo 作为首个 Voice provider：技术上适合（TECHNICALLY SUITABLE）+ 人工听评通过（QUALITY CONFIRMED）。**
22/22 成功、HTTP 契约与官方 OpenAPI 一致、延迟 mean 3.5s、词级时间戳经 subtitle_file 可用、成本口径清晰（480 chars ≈ $0.029）、文本可懂度机测优秀（15/16 CER=0）。
人工听评：男女声连续性均「同一角色」、19 段全「自然」、整体「Turbo 音质够用」、C1 旁白「非常可以」。轻微合成感（「有点像人机」）被用户接受并记录，不阻塞。

**方向结论（用户明确指示）：**
- TTS 音质/音色方向**收口**——「不需要再测试了」；Voice Design/Clone/换 provider 均不启动（15.md §47 一致）。
- **下一重点 = 音画同步**（用户原话「目前最重要的是要实现音画同步问题」）——属新阶段（15.md §2 明确 A1 不做 lip-sync / timeline / mux），本报告只记录方向，不展开。技术前提已由 A1 备齐：production WAV 48k/mono 统一规格 + 词级时间戳（subtitle_file）+ selected/approved artifact 指针。

---

【最终回归】**1762 passed / 0 failed / 0 skipped**（全量 pytest，含 A1 voice 76 项与冻结基线 1537 项，零回归）——
**VOICE FOUNDATION A1 IMPLEMENTATION PASS + 人工听评通过（VOICE QUALITY 确认）。
A1 全部收口。下一重点（用户指示）：音画同步——另立阶段规格后启动。**
