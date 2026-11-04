# NATIVE_AV_DIRECTION_REBASELINE_REPORT
# Native AV 生产方向重定基线报告（Re-baseline）

日期：2026-09-15
性质：**审计 + 架构修正 + 规格 + 文档**。本轮零生产代码改动（§14 纪律；结论见 §8：连"防止进入 A2 主路径"的小纠偏也不需要——A2 本就从未接入任何生产路径，证据 §1.3）。
依据：
- `详细流程文件/修复阶段/AGNES_AUDIO_DIRECTION_CORRECTION_REPORT.md`（人工评审裁定，2026-09-14 人耳数据）
- `AGNES_AUDIO_CAPABILITY_PROBE_A0_REPORT.md`（106 条真实视频 / 13 探针的客观 provider 契约）
- 本轮四路代码审计（Visual V1 生成链 / A1 Voice Foundation / A2 AV Sync / Shot canonical 说话人语义，file:line 均经核实）

---

## 1. 裁决摘要

### 1.1 方向修正正式生效

自本报告起，主生产路径从

```text
Dialogue → standalone DialogueAudioVersion（A1/MiniMax）
         → standalone visual VideoVersion
         → external lip-sync（A2/SyncLabs）
         → mux → final shot
```

正式切换为

```text
Visual Intent + Dialogue/Speaker Intent + Voice Identity References + Shot-local Audio Event Intent
        ↓ NativeAVGenerationPack（derived / ephemeral / provider-neutral）
        ↓ Agnes Native AV Generation（一次生成画面+配音+口型+SFX+ambience）
        ↓ VideoVersion（含原生音轨的 mp4）→ final_video
```

依据（人工评审，2026-09-14）：跨镜声线同一性 5/4 分、构图变化同一人 5 分、音色参考=像同一人、音质 5 分、**原生口型同步好**；唯一实质问题=**偶发 speaker swap** + 个别音色偏细。外挂 lip-sync（SyncLabs）人工判定"不太行"。

### 1.2 废弃的主线假设（文档层生效，无需代码摘除）

| 废弃假设 | 新语义 |
|---|---|
| A1 DialogueAudioVersion = 银幕对白生产真相 | A1 = **旁白音源** + **角色声线参考库**（§4） |
| A2 SyncLabs = 常规生产必需 | A2 = **冻结的 fallback repair primitive**（§5） |
| 每个对白镜都需要后期 lip-sync | 永不默认；只有"画面不可再生+对白必须精确替换"才走 A2 |
| 每个 native AV 镜生成后应替换音频 | 明确禁止的反模式 |

同步停止的工作：SyncLabs model bake-off、lip-sync 质量优化、sync_input 扩展、A2 UI/API 接入、新增 lip-sync provider、自动对白替换。

### 1.3 关键审计结论：不需要任何"退出旧路径"的代码纠偏

全仓 grep 证实（本轮审计）：
- **A1（src/audio 主域）与 A2（src/audio/av_sync）从未接入 API 层**——`api_server.py:87-105` 的 17 个 `include_router` 无一 audio；全 `routers/` 对 `VoiceService|AVSyncService|dialogue_audio|av_sync` 零引用。A1/A2 的消费方只有 76+55 个测试和 2 个真机 driver。
- **A2 的入边为零**：`src/shot_binding/`、`src/core/`、`src/generation/`、routers 对 av_sync 零引用（依赖严格单向：av_sync → A1/core）。
- 生产系统今天**没有任何自动 lip-sync / 自动换音轨路径**——不存在需要"拔掉"的默认行为。

因此 §14 允许的"小纠偏"经评估**不需要执行**：旧路线的废弃由本报告 + 下一阶段设计文档承载，代码层维持冻结现状（A1/A2 全部保留）。

### 1.4 顺带发现（如实记录，不在本轮处理）

- A1 已实现但**未挂载**：旁白/参考音源的 HTTP 面为零；挂载属下一阶段范围（§7）。
- routers/shots.py 仍保留 legacy edge-tts 整镜配音路径（`_handler_audio`，`ShotAudioRequest voice="default"`）+ `target_duration_s=None` 时"时长跟随配音"的依赖方向（`src/core/models.py:298`）——为非 native 工作流保留，不动。
- 代码库存在**两套并行音频栈**（legacy edge-tts vs A1 Voice Foundation），互不感知。

---

## 2. 事实基础（本轮所有设计的证据底座）

### 2.1 人耳评审（架构级裁定，2026-09-14）

| 项 | 结果 |
|---|---|
| 跨镜声线同一性 | 5 分 + 4 分（音色一致可用；个别音色偏细） |
| 构图变化下同一人 | 5 分 |
| 音色参考 = 音色控制器 | 像同一人（成立） |
| 双说话人 | 两声可分、台词分配正确；**偶发 swap（唯一实质问题）** |
| 音质/清晰度/韵律 | 5 分 |
| Agnes 原生 lip-sync | 口型同步好 |
| A2 SyncLabs 对照 | 不太行（外挂路线整体劣势） |

### 2.2 A0 客观 provider 契约（设计输入，全部来自真实 API 探针）

| 能力 | 事实 |
|---|---|
| 原生音频输出 | 恒有：v2.0=AAC 48kHz 立体声；2.5 家族=AAC 32kHz 立体声（106/106，**无 stems，单一混合轨**） |
| 台词来自 prompt | 文本层强可控：CER 0~9%（中英同优；XD 组 2.5-flash CER=0） |
| 台词进 prompt 的通路 | v2.0 与 2.5-flash 均成立（D/E 组 v2.0；XD 组 2.5-flash） |
| **音色控制唯一通路** | **仅 2.5-flash `mode:"reference" + images[] + audios[]`**（p10/p11 报错原文点名复数字段；data URI 可用）；效果=音色/音区锁定（F0 锁参考音 2Hz 级，跨性别生效），**不迁移内容**（参考音台词 CER≈1.0） |
| v2.0 的 audio 字段 | 被接受但无可闻效果（静默忽略）；keyframe 模式的 audio_url 同样无音色控制效果 |
| reference 模式 schema 限制 | 禁用单数 image/first_frame/last_frame（"reference 模式不能包含 first_frame 或 last_frame"）；**reference 与 keyframes/i2v 是不同请求形状，不是开关** |
| 多参考音 | ≥1 合法（数组字段）；**多音色参考未测** |
| 多说话人 | 文本编排层 5/5 可靠；音色分离初步存在；swap 由人评证实为偶发 |
| 音乐 | 单镜可生成但身份跨镜完全不受控（文字延续指令无效 chroma 衰减 0.65→0.34；音乐参考 chroma 0.73-0.87 是唯一牵引通路） |
| 静音 | 可达成（-48~-52 LUFS） |
| 环境音/拟音 | 原生最强层（声学签名与事件对应） |
| 已知语音风险 | 动画风画面+短句会诱发日语语音先验（F/I 组）；跨镜各一句场景文本崩坏率需持续人评监控 |

### 2.3 代码现状（审计要点，file:line 为证）

- **视频生成 100% 静音**：`agnes_client.create_video`（`src/api/agnes_client.py:562-645`）无任何 audio 参数；`BoundVideoRequest`（`src/generation/video_adapter.py:44-73`）无音频槽；`build_video_prompt`（`src/core/prompt_builder.py:106-155`）**从不读 `shot.dialogue`**。
- **Dialogue 真相**：`Shot.dialogue: str`（`src/core/models.py:268`）单字符串；说话人仅以文本前缀约定存在（Director 提示词 `src/agents/director.py:289-291`："小铃：「…」"），**无任何解析器**；`dialogue_id = shot_id` 1:1（`src/audio/voice_pack.py:5-11`）。
- **speaker 解析现状**（`src/audio/voice_pack.py:45-65`）：显式 speaker 必须 ∈ shot.characters；单角色自动裁决；0 角色=no_speaker；多角色=**ambiguous_speaker（不猜）**。speaker 进 `dialogue_fingerprint` 与 `generation_basis`。
- **Visual V1 双路径**：BOUND（有 binding → `build_bound_video_input`，boundary frame 带指纹 freshness gate，绝不回落 legacy）/ UNBOUND legacy（`routers/shots.py:1216-1220`）。
- **VideoVersion**（`src/core/models.py:193-211`）：无 generation_basis、无 probe、无音频字段；`final_video` 为 version_id 指针；文件级身份=内容 sha256（A2 `av_truth.py:9-10` 因"Visual V1 冻结不添加字段"而选定的口径）。
- **ImageBatch.generation_basis 先例**（`src/core/models.py:186-190`）：批次级 basis 字段已存在于 core schema（PackBasis 六键：binding_revision / binding_document_revision / binding_readiness / dependency_fingerprints / resolved_scene_instance_view_id / visual_profile_fingerprint）。
- **pack 家族先例**：VoiceGenerationPack（A1，derived/ephemeral/不持久化）、LipSyncGenerationPack（A2，13 键 basis）——provider-neutral 投影 + actual-dependencies-only 指纹族（sha256(canonical_json)[:8]）已是全仓统一范式。

---

## 3. 新主生产原则（结构定义）

### 3.1 Planning time（不生成媒体）

Director / Shot canonical 只表达：

```text
谁在说话（speaker）
说什么（Shot.dialogue，唯一文本真相）
表演意图（既有视觉表演字段：opening_state/end_state/motion/action_sequence/...）
声音事件意图（shot-local SFX/ambience cue——字段缺口，见 Q3）
必要的 ambience / diegetic cue
```

### 3.2 Production time（一次生成音画）

```text
Shot canonical（dialogue + 表演字段）
+ ShotProductionBinding（视觉绑定：scene/look/composition/prop/action）
+ DialogueSpeakerBinding（每句台词→说话人，最小契约见 Q5）
+ Voice References（从 A1 派生，见 Q4）
+ Audio Event Intent（shot-local）
        ↓ NativeAVGenerationPack（derived / ephemeral / deterministic / provider-neutral）
        ↓ Agnes（台词进 prompt；音色参考走 reference+audios[] 通路）
        ↓ VideoVersion（含原生音轨）→ 人工评审 → final_video
```

**不**再人为拆成独立音频系统后重新拼接。不要再把"天然耦合的表演"拆散再同步。

---

## 4. Q1–Q12 逐题回答

### Q1 哪个对象最适合作为最终 Native AV Shot truth？

**`VideoVersion`（经 `Shot.final_video` 指针），不新增任何 canonical。**

- 它已是 authoritative video 唯一真相源：A2 的 `resolve_authoritative_video`（`src/audio/av_sync/av_truth.py:27-33`）与 `project_intelligence._resolve_final_video`（`src/agents/project_intelligence.py:618`）都只认 `final_video → video_versions`，永不 glob/mtime。
- mp4 文件本身允许携带原生音轨——container 层无障碍（A2 mux 已产出 h264+aac；A0 实测原生输出恒为 mp4+AAC）。
- 版本共存、人工评审、选优（append-only `video_versions` + 显式指针 `set-final`）机制全部现成（`routers/shots.py:1272-1295, 1341-1383`）。

禁止创建：`NativeAVVersion` / `FinalAVVersion` / `AudioVideoShot` / 第二套平行 canonical。**"含音轨的 VideoVersion"在真值层面就是 Native AV Shot truth。**

### Q2 如何让现有 video generation 支持 native audio（不建第二套生产系统）？

作为**现有 BOUND/legacy 视频生成链的能力扩展**，改动面收敛在四处：

1. **Prompt 侧**：`build_video_prompt` 增加确定性 dialogue/speaker 段编译（从 NativeAV pack 的 dialogue block 输入；v2.0 与 2.5-flash 均已实证"台词进 prompt 即说"）。prompt 仍是 derived 投影，canonical 不变。
2. **Provider 侧**：`agnes_client.create_video` 增加可选 `audios: List[str]`（data URI wav）+ `mode:"reference"` 分发（仅 2.5 家族；A0 p10/p11 已验证请求形状）。v2.0 路径一个字节不动（其 audio 字段为 no-op，绝不发送）。
3. **Pack 侧**：新增 NativeAVGenerationPack 编译器（Q6），复用 `compile_reference_pack` 的视觉产物与 `_pre_dispatch_guard` 的 race guard 模式。
4. **Provenance 侧**：生成时把 basis + prompt 快照记入版本级 provenance（载体见 Q9）。

**能力分层（按 A0 实证诚实划分）**：

| 层 | 模型/模式 | 音色控制 | 视觉输入 | 定位 |
|---|---|---|---|---|
| Tier A | v2.0 i2v/keyframes（现状形状） | 无（模型自采样） | boundary frames（既有 freshness gate 不变） | 台词可控、声线靠模型+人评把关 |
| Tier B | 2.5-flash `mode:"reference"` | **有**（audios[] 锁音区） | `images[]`（禁用 first/last 单数字段） | swap 防护主通路（Q8） |

### Q3 canonical 已有哪些 dialogue/speaker/performance/audio intent 字段？缺什么？

**已有**：

| 意图 | 字段 | 位置 |
|---|---|---|
| 台词文本 | `Shot.dialogue: str`（唯一真相；说话人仅文本前缀约定） | `core/models.py:268` |
| 在场角色 | `Shot.characters: List[str]`（name FK，A1 speaker 解析与 prompt 角色身份都读它） | `core/models.py:267` |
| 表演意图（画面侧） | `opening_state/end_state/motion/motion_pace/action_sequence/environment_motion/frame_change/...` | `core/models.py:275-297` |
| 动作意图（镜内起止态） | `BindingAction.start_state/end_state/note` | `shot_binding/models.py:120-138` |
| 时长意图 | `target_duration_s`（legacy 语义：None=跟随配音音频反推） | `core/models.py:298` |
| 自由生产备注 | `ShotProductionBinding.production_notes` | `shot_binding/models.py:186` |
| 声音身份 | `CharacterVoiceProfile`（approved 后即角色声线真相） | `audio/models.py:118-209` |

**缺口（Q5/Q6 的设计对象）**：

1. **每句台词的说话人归属**——多角色镜完全无法表达"哪句是谁说的"（A1 因此 fail closed 为 ambiguous_speaker）。这是本次方向下**第一风险（swap）的 canonical 缺口**。
2. **Shot-local 声音事件意图**（SFX/ambience/diegetic cue）——无任何字段。
3. **语音表演意图**（delivery：语气/情绪/语速的台词级意图）——A1 pack 留了 `delivery_intent` 占位 seam（`audio/models.py:254`），canonical 无对应。
4. **语言**——A1 pack 同样留了 `language` 占位 seam（`audio/models.py:253`）。
5. **voice reference 链接**——角色→approved 声线→参考音频，今天只存在于 A1 域内，不在任何生成链上（Q4 解决，不新造 canonical）。

### Q4 VoiceReference 如何从 A1 派生（不创建第二套 voice truth）？

**原则：reference 是 derived read-only 投影，voice 真相只有一份（CharacterVoiceProfile + 其 approved 产物）。**

```text
character_name
  → CharacterVoiceProfile（approved，唯一声线真相，src/audio/service.py:102-121）
  → 参考 wav 来源（两选一）：
     (a) 首选：该角色已 approved 的 selected DialogueAudioVersion.production_wav
         （48kHz/mono/s16，规格确定性，src/audio/media_ops.py:32-33）
     (b) 兜底：无任何 approved 台词音频时，用该 profile 经 MiniMax 合成
         一条「声线样本」（非任何 Shot 的 DialogueAudioVersion，独立标本产物）
  → VoiceReference（derived）：{character_name, voice_profile_id,
     voice_profile_revision, source: (a)|(b) 的 artifact id,
     audio_fingerprint: sha256(文件), spec}
```

- **不复制**：不新建 voice canonical、不把 wav 拷贝成第二份"参考库"——直接引用 A1 已有产物路径 + 指纹。
- **freshness**：`voice_profile_revision` 变 or 源产物指纹变 → reference STALE（0 provider call 重算，同 A1/A2 范式）。
- **provider 语义对齐 A0**：参考音=音色/音区控制器，不迁移内容；台词内容永远来自 prompt 文本——"文本管内容、参考音管音色"（A0 §12 结论）。
- A0 实证该通路把音色锁到 2Hz 级（对照组原生散布 127Hz）——这正是人评裁定"音色参考=像同一人"的机制化。

### Q5 DialogueSpeakerBinding 最小 contract 是什么？

**目标链路**：`Dialogue → intended speaker → visual character → voice reference → provider input` 全程可追踪。

**最小 contract（v1）**：

```text
DialogueSpeakerBinding（附着于 shot_production_bindings.json 的一条 ShotProductionBinding
                        的可选块；不进 project.json）：
  lines: [ { index: int,                      # 行号（对 Shot.dialogue 的确定性切分序）
             speaker_character_name: str } ]  # 必须 ∈ Shot.characters
  resolution: "single_auto" | "explicit"      # 单角色镜自动=沿用 A1 resolve_speaker 语义；
                                              # 多角色镜必须显式
```

设计约束：

1. **不复制台词文本**（维持"A1 §3A 不建第二套文本真相"）：binding 只存 `index → speaker` 映射，行文本永远在编译时从 `Shot.dialogue` 原样切分读取。文本变 → dialogue_fingerprint 变 → STALE，链路自然成立。
2. **切分确定性**：Director 已有的说话人前缀约定（`小铃：「…」`）+ 换行/句读，编译器按规则确定性切分；无法唯一切分或多角色镜无显式 binding → **fail closed**（复用 A2 `multi_face_target_required` 的哲学："不猜"）。
3. **speaker 合法性**：每个 `speaker_character_name` 必须 ∈ `Shot.characters`（复用 A1 `invalid_speaker` 语义），且应能对应 `CharacterBinding`（有 look/voice 的在场角色）。
4. **provider 不支持结构化绑定（今日事实）**：由 pack 编译器把 lines 确定性编译为 provider-specific 排布——prompt 中按序标注说话人的台词段 + 每说话人一条参考音（audios[] 顺序与 lines 对齐）。**provider-specific 排布绝不写回 canonical**。
5. A1 的 `resolve_speaker`（单角色自动路径）保持原语义不变；binding 是它的显式超集，不是替代。

### Q6 NativeAVGenerationPack 应包含哪些 actual dependencies？

沿用全仓 pack 家族范式（derived / ephemeral / deterministic / 无 business ID / 不持久化 / 同输入→同 pack）：

```text
NativeAVGenerationPack（schema: native_av_generation_pack_v1）
  shot_id, scene_id
  visual:
    provider_mode                # i2v | keyframes | reference（映射规则见 Q8）
    visual_intent                # build_video_prompt 结构段 + _production_context_text + 风格指令（复用现有编译）
    visual_references            # Tier A: boundary frames（既有 freshness gate）；Tier B: reference images[]（复用 compile_reference_pack 的 refs）
    basis                        # 现有 PackBasis 六键（binding_revision/resolved SIV/...）
  dialogue:
    dialogue_id                  # = shot_id
    dialogue_fingerprint         # {dialogue_id, text, speaker_map}——扩展现有 A1 指纹（speaker 从单值→映射）
    lines                        # [{index, text 原样切片, speaker_character_name}]
  voice_references:              # 每说话人一条（Q4）
    [{character_name, voice_profile_id, voice_profile_revision,
      reference_artifact_id, audio_fingerprint}]
  audio_event_intent:
    directives                   # shot-local SFX/ambience 文本意图（来源见下）
    music_policy                 # "none"(默认) —— 音乐绝不默认进 per-shot native 生成（§Q10）
  provider_binding:
    provider, model, mode, provider_options_fingerprint
  generation_basis               # 见 Q7
```

- `audio_event_intent` 初版来源：`ShotProductionBinding` 新增可选小字段（或先用 `production_notes` 约定段，方案在下一阶段定稿时二选一）；**不自动发明**，空=让模型按画面自演绎（A0 实证这是它最强的层）。
- 复用而不复制：visual 块复用 `compile_reference_pack` 产物；dialogue 块复用 A1 的 fingerprint 函数族；race guard 复用 `_pre_dispatch_guard` 模式。**禁止把 project.json / 整份 resources.json / 整份 voice 域 JSON 塞进依赖。**

### Q7 generation basis 应 hash 哪些输入？哪些绝不 hash？

**进 basis（actual dependencies only，指纹族 sha256(canonical_json)[:8]）**：

1. `shot_id`
2. 视觉身份：`binding_revision`、`resolved_scene_instance_view_id`、`visual_profile_fingerprint`（现有 PackBasis 口径）
3. 视觉引用：Tier A = 所用 boundary candidate id + 其批次 basis 对比键；Tier B = 各 reference image 的资产指纹
4. `dialogue_id` + `dialogue_fingerprint`（含完整文本 + speaker 映射——**speaker map 变 = stale**）
5. 每说话人：`voice_profile_id` + `voice_profile_revision` + `reference_audio_fingerprint`
6. `audio_event_intent` 指纹
7. `provider` / `model` / `provider_mode` / `provider_options_fingerprint`

**绝不 hash**（先例依据）：

- project.json / resources.json / voice_profiles.json / dialogue_audio.json 整文件（A2 basis §13 原文禁令）
- 无关 Shot、无关角色的任何变化（"无关变化不 stale"是全仓测试钉死的性质）
- 时间戳、随机 UUID、临时路径、working 文件路径（pack 家族共性禁令）
- API key / 任何 secret（A1/A2 adapter 构造性脱敏）
- `approval_status`、`label`、`origin_run_id`（A1 先例：批准是生成门，不是已生成产物的语义）
- 编译后的 prompt 全文（它是以上各项的 derived 投影；prompt 快照进 provenance 记录，不进 basis——Resources 先例可带 prompt，A1 先例不带；此处取 A1 口径，快照另存）

### Q8 Agnes adapter 当前需要怎样扩展？

`src/api/agnes_client.py`（现状 `create_video` `:562-645`，双契约已分离）：

1. **新增可选参数** `audios: Optional[List[str]]`（wav data URI 数组）与 `mode:"reference"` 分发（仅 `_is_video_25` 分支）：请求体 `{model, prompt, mode:"reference", seconds, images:[...], audios:[...]}`——A0 p10/p11 实证形状（data URI 免图床）。
2. **诚实 capability 门**（沿用 `end_state_semantic_target_only` 式诊断风格）：
   - reference 模式拒收单数 image/first_frame/last_frame（schema 冲突，A0 报错原文为证）→ Tier B 的视觉锚改走 `images[]`；
   - v2.0 + audios → 拒绝或显式诊断（实测 no-op，绝不静默发送制造虚假 provenance）；
   - 2.5 家族 + `negative_prompt` → 现状已不发（非白名单字段），保持；
   - keyframes + audio 字段同发虽 200 但无音色效果（A0 §11 附注）→ 不作为 Tier B 形状。
3. **多参考音未测**的诚实暴露：pack/adapter 对 `len(audios)>1` 打 diagnostic（`multi_audio_reference_unverified`），交人评验证，不静默。
4. `poll_video`/`download` 不需要动（产物仍是 mp4，URL fallback 链与无 Authorization 下载规则不变）。
5. 模型路由：Tier B 必须 2.5-flash（免费档）；`agnes-video-2.5`（非 flash）付费墙（A0 §1），禁止默认选入。

### Q9 VideoVersion / final_video 是否无需 schema 改动即可继续作为 authoritative result？

**真值角色：是，零改动即可。** final_video 指针、append-only 版本、legacy 迁移（`legacy_video` 自动迁移 `core/models.py:243-250`）、机器验证（ffprobe `has_audio/audio_codec/duration`，工具现成于 `av_truth.ffprobe_video`）都不需要新字段。

**Provenance 角色：有一个最小缺口。** 生成含原生音轨的版本时，"这版是基于什么（对话/声线参考/模式）生成的"没有落点。两个候选：

- **方案 1（推荐）：`VideoVersion` 增加可选 `generation_basis: Dict[str, Any] = {}`**——完全复制 `ImageBatch.generation_basis` 的既有先例（`core/models.py:186-190`）。兼容性证据：`_parse_video_version`（`core/models.py:224-225`）known-field 过滤——旧代码读新文档=丢弃新键不炸；新代码读旧文档=默认 `{}`（消费方对 `{}` fail closed，与 ImageBatch 同口径）。这是对 A2 时期"VideoVersion 不添加字段"冻结决定的**有据重开**：当时 A2 无需该字段（用文件 sha256 替代）；Native AV 的生成依赖是 pack 语义而非文件内容，sha256 无法表达"基于哪个声线参考生成"。
- **方案 2（零 core 改动备选）**：版本级 provenance 记入伴随 JSON（`src/resources/generation_records.py` 范式），以 version_id 为键。代价：多一份文档 + 查找间接层，正是 A1/A2 一开始想避免的"第二真相源"。

**裁决建议**：下一阶段采用方案 1（一个可选字段，先例充分、双向兼容有代码证据），若用户坚持零 core schema 触碰则退方案 2。

### Q10 哪些声音属于 Shot-local native generation？哪些属于未来 Sequence Audio Post？

| Shot-local（跟画面一次生成） | 依据 | Sequence / project-bound（未来 Audio Post/Timeline） | 依据 |
|---|---|---|---|
| 银幕对白（dialogue performance） | 人评：原生配音流畅、口型同步好 | 旁白 / voice-over | A1 MiniMax 主路径（确定性、可控、便宜） |
| 呼吸 / 口部表演声 | 与口型同生同灭 | 背景音乐 / score / musical motif | **A0：音乐身份跨镜完全不可控**（G/H 组），人评未逐项验证连续性 |
| 脚步 / 衣物 / 开门 / 碰撞 / 物件交互 | A0 B 组声学签名匹配 | 场景级 atmosphere bed / 跨镜声音连续性 | 跨镜连续性是 sequence 议题 |
| 镜内 diegetic SFX | A0 B 组 | 转场声音 / crossfade | timeline 操作 |
| 镜内 ambience | A0：原生最强层 | 全局响度 / final mix / master export | 混音是 sequence 议题 |

**红线**：`Music 不得因为 Agnes 能生成而默认成为 per-shot native requirement`——A0 客观证明文字钉不住音乐身份；per-shot 生成音乐=每镜独立采样的配乐噪音。Shot 级默认 `music_policy="none"`（需要"无配乐"时用静音/低音乐指令），音乐进未来 Audio Post（届时音乐参考通路 chroma 0.73-0.87 是唯一候选机制，另行验证）。

原则一句话：**声音越依赖画面中的精确动作时机，越应该跟画面一起生成；越跨镜、越全局，越归 Audio Post。**

### Q11 A1 / A2 每个已有组件的新角色是什么？

**A1（全部冻结保留，生产语义转岗）**：

| 组件 | 新角色 |
|---|---|
| `CharacterVoiceProfile`（OCC/approve） | **Voice Identity 真相**（不变）+ Native AV 参考音源的身份锚（Q4 链条头） |
| `DialogueAudioVersion` | ①旁白生产产物；②**角色声线参考库**（approved production wav = VoiceReference 源）；③A2 fallback 的输入（不变） |
| `VoiceGenerationPack` / `resolve_speaker` / readiness / freshness | 旁白与参考音生成的内部机制（不变）；`resolve_speaker` 单角色自动语义被 Q5 binding 继承 |
| MiniMax adapter | **Narration 专用音源** + 声线标本合成（Q4 兜底）；不再承担银幕对白主生产 |
| store（dialogue_audio.json / voice_profiles.json） | 不变；未来若 Native AV 也能稳定做旁白，再降 MiniMax 地位（本轮不动） |

**A2（全部冻结保留，退出 active path，定位=fallback repair primitive）**：

| 组件 | 新角色 |
|---|---|
| `AVSyncReadiness` / `DialogueTimingPlan` / `LipSyncGenerationPack` / basis 13 键 | 冻结；唯一入口条件=「画面不可再生 + 对白必须精确替换」的 repair case |
| `DialogueVideoSyncVersion` / store / freshness / race guard / sync_media mux | 冻结；仅服务上述 repair case 的完整既有链 |
| SyncLabs adapter | 冻结；SYNC_API_KEY 免费额度停用 |
| `av_truth.py`（authoritative video 解析 / ffprobe / sha256） | 冻结在其包内；Native AV **不得 import av_sync**（保持冻结面纯净）——native AV 机器验证用自有轻量 ffprobe 工具（或未来把 media probe 上提 core，另行决策） |

禁止反模式（再次钉死）：新 Native AV 链 **不得** 生成后 → A2 → 换音轨 → 再 lip-sync。

### Q12 从当前代码到新主路径的最小实现 diff 是什么？

（本轮不动代码；以下为下一阶段范围，§7 展开。按侵入度排序：）

1. `src/core/models.py`：`VideoVersion` +1 可选字段 `generation_basis`（Q9 方案 1，ImageBatch 先例）。**唯一 core schema 触碰。**
2. `src/shot_binding/models.py`：`ShotProductionBinding` +可选块 `dialogue_speaker_binding`（Q5）与可选 `audio_event_intent`（Q6）；`schema_version` 维持 1（known-field 过滤双向兼容，与 A1/A2 增量同口径）。
3. `src/audio/voice_reference.py`（新文件，纯增量）：Q4 的 VoiceReference 派生 + freshness。
4. `src/generation/`（新文件 `native_av.py` 或扩展 `video_adapter.py`，增量优先）：NativeAVGenerationPack 编译器 + 扩展 BoundVideoRequest（可选 dialogue/audio 槽）。
5. `src/api/agnes_client.py`：`create_video` 增量参数（Q8）；v2.0 legacy 分支字节级不动。
6. `routers/shots.py` 视频链路：显式 opt-in 参数（默认关；关闭时与今日行为字节恒等，`test_b4_unbound_legacy_video_behavior_identical` / `test_q2` 式测试钉死）。
7. 评审网站（scratch，复用 `_voice_a1_listen_server.py` / `_agnes_audio_review_server.py` 模式）。

不触碰：A1/A2 全部、`src/core/store.py` 写路径、Director/agents、legacy edge-tts 路径、Visual V1 service（除 binding 可选块外）。

---

## 5. 声音模块新定义（A/B/C/D 分层）

```text
A. Voice Identity / Reference   —— A1 提供（profile 真相 + 参考派生）
B. Shot Audio Intent            —— Shot planning / Director 语义（dialogue + speaker binding + shot-local cue）
C. Native AV Generation         —— 现有 Video Production / provider generation 的能力扩展
D. Sequence Audio Post          —— 未来独立阶段：music / narration 编排 / 跨镜 ambience /
                                   额外 SFX / crossfade / 响度 / final mix / master export
```

- 本轮只定义边界，**不实现 D**；A 已存在（转岗），B/C 是下一阶段范围。
- 不再设计"所有声音都归 Audio Module"的万能模块；声音领域 = A + B 的语义，C 的执行走视频生产链，D 另立阶段。

### 不做 source separation（§11 确认）

Native AV 输出整体视为 **Native Shot Audio Bed**（106/106 单一混音轨是 provider 事实，不是架构选择）。不做对白/SFX/ambience 分轨提取、不做 source separation、不默认 voice replacement。未来 Final Mix 允许：

```text
Native Shot Bed + Sequence Music + Narration + 必要 external overlays
```

——而不是先拆散再重建。

---

## 6. Regeneration policy 与质量评估重定义

### 6.1 Regeneration policy

Native AV Shot 出现 voice 略偏 / speaker swap / 表演弱 / 口型差 / 局部 SFX 差时，默认策略：

```text
regenerate 新 VideoVersion → 人工评审 → select better version → final_video 重指
```

禁止自动退回 extract → replace audio → external lip-sync → mux。仅 `shot visual is irreplaceable` 的特殊 repair case 走 A2（显式人工决策，不是路径分支）。

### 6.2 机器验证（只管客观技术健康）

- 视频流存在 / 音频流存在（`ffprobe has_audio, audio_codec`）/ 时长合法 / 编解码与容器合法 / 生成完成 / artifact 存在 / provenance 可解析
- 音频非静音 sanity（-LUFS 或 silencedetect 轻量检查；工具方法已在 A0/A2 实证）
- 可对 voice/speaker 异常给 **warning**（如 F0 离群检测），但**机测不得单方面否决**——A0 的最大教训：机测把人耳 5/4 分的声线判成 1.5 分，机测只能筛风险，不能下架构结论。

### 6.3 人评（凡"听起来像/自然"必须人耳）

每个新 provider / 关键能力阶段默认配轻量评审页（听评网站模式已两次验证有效：FEEDBACK.json 落盘）。固定清单：

```text
speaker 正确？（swap 专项）
voice 贴角色？（含跨镜拼接听评）
台词正确？（含动画+短句的日语先验监控）
表演自然？原生口型可接受？音质可接受？
SFX/ambience 合理？整镜 AV 可用？
```

---

## 7. 下一阶段建议：Native AV Production Foundation

> 不直接开始实现；以下为建议范围，等用户批准后立规格。

**目标**：打通"一条对白镜从 canonical 到含原生音轨的 VideoVersion"的最小真实闭环，人评验证 swap 防护与声线参考两条核心假设。

### 7.1 Minimal implementation scope

1. **NativeAVGenerationPack 编译器**（Q6）：dialogue block + speaker binding 解析（Q5）+ voice reference 解析（Q4）+ audio_event_intent 透传 + basis（Q7）。Tier A 先行（台词进 prompt），Tier B（reference+audios）同阶段实现但作为独立 opt-in。
2. **adapter 扩展**（Q8）：`create_video(audios=..., mode="reference")` + capability 门与诊断。
3. **VideoVersion.generation_basis**（Q9 方案 1）。
4. **ShotProductionBinding 可选块**：dialogue_speaker_binding + audio_event_intent（Q12.2）。
5. **VoiceReference 派生模块**（Q4）。
6. **视频 handler 接线**：显式 opt-in，默认关闭路径字节恒等。
7. **评审网站**：native AV 听评页（swap/voice-fit/lip-sync/台词四专项 + AB 拼接）。

明确不做：Sequence Audio Post（D 层）、音乐 per-shot 生成、source separation、A2 复活、A1 大规模重构、Director 重写、新 Agent。

### 7.2 Files likely affected

```text
src/core/models.py                                  # +1 可选字段（唯一 core 触碰）
src/shot_binding/models.py                          # +2 可选块
src/generation/native_av.py                         # 新：pack 编译器
src/generation/video_adapter.py                     # 小扩展（或由 native_av 组合，实施时定）
src/api/agnes_client.py                             # +audios / +reference mode
src/audio/voice_reference.py                        # 新：参考派生
routers/shots.py                                    # opt-in 接线
tests/test_native_av_*.py                           # 新测试（见 7.3）
详细流程文件/修复阶段/17.md（或用户指定编号）          # 下一阶段规格
_agnes_native_av_review_server.py                   # scratch 评审页
```

### 7.3 Files explicitly frozen

```text
src/audio/av_sync/**（A2 全部八文件 + 3 测试 + driver）
src/audio/models.py / service.py / store.py / voice_pack.py / minimax_adapter.py / media_ops.py   # A1 语义不动；voice_reference.py 为新增旁挂
src/core/store.py 写路径、src/core/timing.py、src/core/job_queue.py
src/resources/**、src/scene_system/**、src/visual_profile/**（冻结域照旧只读复用）
src/agents/**（Director/ScriptWriter 不改；对话仅按既有 dialogue 字段读写）
routers/shots.py 的 legacy edge-tts 路径（不动）
```

### 7.4 New tests required

- pack 确定性 / actual-dependencies-only（无关角色与无关镜变化 → basis 不变）
- speaker binding：单角色自动 / 多角色缺 binding fail closed / 非法 speaker 拒绝 / 文本变化 → dialogue_fingerprint 变
- voice reference：approved 派生 / 未 approved 拒绝 / profile revision 变 → stale / 源产物缺失 → stale
- adapter：Tier A 请求体与现状恒等（除 prompt 段）+ v2.0 永不带 audio 字段；Tier B reference 请求形状按 A0 实证 + reference 拒 first/last 单数 + 多参考音 diagnostic
- basis/freshness：Q7 各键逐键 stale / 无关变化 CURRENT
- legacy 恒等：opt-in 关闭时 handler 输出与今日字节恒等（b4/q2 式）
- provenance：VideoVersion.generation_basis 写入与 `{}` fail-closed 读取

### 7.5 Human E2E required

真机最小验证包（全部过人耳/人眼，评审页交付）：

1. 多说话人镜（2 角色 × 多句台词）×N 生成：swap 率人评 + 台词分配正确性（对照 A0 J 组与用户人评基线）
2. 同角色跨 4-6 镜（含构图变化）+ 声线参考（Tier B）拼接听评：跨镜同一性对照 Tier A
3. 原生 lip-sync 可接受度、SFX/ambience 合理性
4. 台词保真（短句/动画风日语先验监控）
5. Tier B 视觉身份：reference images[] 对照既有 boundary frame 路径的视觉质量（A0 §14 O1/O2 对看遗留项）

### 7.6 Migration / compatibility risks

| 风险 | 说明 | 缓解 |
|---|---|---|
| **Tier B 与 boundary-frame 体系形状冲突** | reference 模式禁用 first/last 单数帧——BOUND 路径的 freshness gate 建立在 boundary frames 上 | Tier B 视觉锚改走 images[]（pack refs 已有确定性选择序）；freshness 改比对 image 资产指纹；Tier A 不受影响。这是最大的设计张力，规格期专项设计 |
| Tier B 仅 2.5-flash | 音色控制只在此通路；32kHz 音频；2.5 非 flash 付费墙 | 模型路由显式化；默认 Tier A；付费模型禁默认 |
| 多参考音未测 | A0 明示"多参考未测" | 首个真机 E2E 专项验证；失败则退化到单参考+prompt 描述 |
| 动画风+短句语音先验（日语） | A0 F/I 组客观存在；人评显示整体可用但非零 | 人评清单固定项 + prompt 语言强化（"Speak in Mandarin Chinese…"）实验位 |
| swap 无法机测根除 | 人评唯一可靠 | 评审页常态化 + regenerate 策略（§6.1） |
| VideoVersion 字段重开冻结决定 | A2 曾记录"不添加" | 本报告 Q9 提供重开证据与兼容性论证；如用户不同意走方案 2 |
| 依赖方向纪律 | native_av 不得 import av_sync / 不得反向依赖 core→src | 测试钉死 import 边界 |

---

## 8. 纪律执行记录

本轮实际产出：本报告 + 四路代码审计（Visual V1 / A1 / A2 / canonical）。**生产代码 diff = 0**；无文件删除、无 schema 改动、无 A1/A2 处置、无新 Agent、无 A3/A4 实现。

§14 允许的小纠偏（防止默认进入 A2 主路径）经审计判定**无需执行**：A1/A2 从未挂载任何生产路径（§1.3 证据），不存在需要阻断的默认行为。旧→新路径切换由本报告承载，下一阶段规格（§7）批准后按 7.1 范围实施。

---

## 9. 最终原则重申

> Agnes 已经能够把角色表演、对白、口型和画面一起生成得足够好——系统顺着模型的原生能力设计，而不是把天然耦合的问题人为拆散后重新同步。
>
> **项目 canonical 决定应该生成什么；provider 决定像素和声音如何共同实现它。**
