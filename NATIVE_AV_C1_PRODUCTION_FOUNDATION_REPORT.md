# NATIVE AV C1 — PRODUCTION FOUNDATION + AV PROMPT COMPILER V1 报告

执行依据：`详细流程文件/修复阶段/18.md`（Native AV C1）。
上游：`NATIVE_AV_B1_DIALOGUE_SPEAKER_REPORT.md`（B1 FINAL PASS，2026-09-15 人评裁定）、
`NATIVE_AV_DIRECTION_REBASELINE_REPORT.md`。

---

## 0. 当前状态

```text
NATIVE AV PRODUCTION FOUNDATION
IMPLEMENTATION PASS

HUMAN FINAL REVIEW PENDING
评审入口：http://localhost:8911/ （_native_av_c1_review_server.py）
反馈落盘：_c1_native_av_e2e/results/C1_REVIEW_FEEDBACK.json
```

按 18.md §42 已 STOP——不自动开始 Voice Reference / Audio Post / Music / Final Mix。

---

## 1. Commit 分段（§40）

| Commit | 内容 | 条款 |
|---|---|---|
| `bcade40` C1-A | routing cleanup + production policy | §3/§4/§5 |
| `54f07ce` C1-B | NativeAVGenerationPack + AV Prompt Compiler V1 | §6-§15/§21 |
| `6b9274c` C1-C | Shot-local Audio Event Intent | §16/§17/§18 |
| `7ccc808` C1-D | VideoVersion generation_basis + freshness + race guard | §22/§25/§26/§27 |
| `644b144` C1-E | Director Native-AV planning discipline | §19/§20 |
| 本报告 C1-F | 真机 E2E + 评审网站 + 报告 | §30-§35 |

每段独立通过 targeted tests；全量回归在 C1-F 收口。

---

## 2. §41 十七问逐答

### Q1 B1 如何从实验 flag 变成正式 production policy？

三处语义翻转（`bcade40`）：

* 路由判定换新谓词（见 Q3）；
* 端点 `native_dialogue_control`：`False`(旧默认关) → **`None`=自动**——
  `Shot.dialogue` 非空即启用 Controlled Dialogue（`_handler_video` 内
  `ndc = params.get("native_dialogue_control") is not False`）；显式 `False` 仅作
  debug/compat override（visual-only，dialogue 不进 prompt，generation_basis 记 `{}`）；
  显式 `True` 保持兼容；
* language 不再要求每次手工传（见 Q2）。

无 dialogue 的 Shot 行为逐字节不变（`resolve_native_dialogue` 先查台词后查语言；
测试 `test_default_on_no_dialogue_unchanged` 锁死）。

### Q2 language truth 最终放在哪里？

`Project.native_av.spoken_language`（`src/core/models.py` 新增 `NativeAVSettings`；
project.json 新顶层键 `native_av`，旧项目缺键 → 空 settings，T17 shape 冻结测试已更新）。
读写端点：`GET/PUT /api/project/{p}/native-av-settings`。
precedence（`dialogue_control.resolve_dialogue_language` 唯一实现）：

```text
显式 request override（native_dialogue_language）> project.native_av.spoken_language
```

有台词 + 皆缺 → `language_missing` **fail closed**（零 provider 调用）——
绝不恢复成让 Agnes 猜语言（B1 人评已证伪）。本阶段不做 localization、不自动猜。

### Q3 dialogue-only metadata 如何避免误触 BOUND？

新增 `shot_has_visual_production_binding`（image_adapter.py）：只有 binding 携带
**视觉生产内容**（scene_binding / character_bindings / prop_bindings / action 任一）
才路由 BOUND。首帧/尾帧/视频三处调用点统一换用。
`shot_has_binding`（key-presence）保留为「存在任何 binding 元数据」的诊断语义。
`dialogue_speaker_binding` / `audio_event_intent` / `production_notes` 单独存在
≠ Visual Production Binding（测试锁死：这类 binding 走 legacy 路径且 dialogue 控制
照常生效——B1 报告如实暴露的缺陷就此关闭）。

### Q4 NativeAVGenerationPack 最终结构是什么？

`src/generation/native_av_pack.py`（`native_av_generation_pack_v1`）：
derived / ephemeral / deterministic / provider-neutral / 无业务 ID / 不持久化。

```text
schema / shot_id / scene_id
visual:          route(bound|unbound) + 对比键子集（见 Q10/Q23）
dialogue:        dialogue_fingerprint / spoken_language / lines[index,exact_text,
                 speaker,delivery_hints] / silent_characters / speaker_map_fingerprint /
                 directions / resolution      （无 spoken 台词 → None）
audio_intent:    music_policy + directives + source(binding|default)
provider_binding: provider/model/mode/options_fingerprint
compiler:        prompt_compiler_schema
generation_basis: §23 派生（写入 VideoVersion）
```

§8：`lines[].exact_text` 只是 `Shot.dialogue` 的 derived slice——真相仍只有一份，
pack 不持久化第二份 dialogue。§6：视觉侧组合/引用现有 Visual V1（BOUND=PackBasis
对比键，UNBOUND=boundary frame 身份），未复制 ReferencePack、未重造依赖图。

### Q5 Prompt Compiler V1 有哪些规则？每条解决哪个已观察问题？

`src/generation/av_prompt_compiler.py`（`native_av_prompt_v1`），四语义区单行编译：

| 规则 | 文本要点 | 解决的已观察问题 |
|---|---|---|
| §12 红线一 | "Only the explicitly quoted dialogue is spoken aloud: do not speak, narrate, or read any visual description, camera instruction, action description, character motion, sound-effect instruction, or production note." | B1 A 侧把 motion prompt「它微微点头，转身欲行」当台词朗读 |
| §13 红线二 | "Speak exactly the supplied dialogue: do not translate, paraphrase, summarize, or invent additional spoken lines; natural tone, breathing, and non-verbal vocalizations are allowed…" | B1 A 侧日语/语言漂移；同时保留自然表演空间（不走向机械 TTS） |
| §14 speaker 确定性 | 编号行 `N. 角色名 says/replies/then says: 台词` + "Do not swap the speakers." + silent 声明 | B1 已证有效的控制形态原样保留 |
| §17 no-BGM | "No background music or musical score: use only natural dialogue, diegetic sounds, and appropriate environmental ambience." | B1 人评「对白镜自动出现背景音乐」 |
| §7 non-spoken 区 | 整行指示 → "Stage direction (not spoken, do not read aloud): …"；语气提示 → "X delivery direction (not spoken): …" | §7 stage direction 被朗读风险 |
| §18 sound 区 | "Shot-local sound (generate natively within the scene, not as separate audio): d1; d2." | 动作-声音共同发生（§15），不是手工时间线 |
| §15 visual 原样 | visual prompt 逐字节在前，不重排、不发明动作 | 现有视觉主链零回归 |
| §4 兜底 | 无 dialogue 且 sound 区为空 → 返回 visual prompt 逐字节原样 | 无台词镜头行为不变 |

Zone 顺序 = §11：visual → non-spoken → spoken dialogue → sound environment。
无观察依据的规则没有加（§29）。

### Q6 stage direction 如何处理？

`dialogue_control.classify_dialogue_body` 确定性三分类（绝不用 LLM 解释字符串）：

```text
整行括号（全角/半角，可带 speaker 前缀）→ non_spoken_direction（不进 spoken）
行首连续括号 + 正文             → delivery hint（非台词）+ spoken text
句中/句尾/嵌套括号              → ambiguous_dialogue_markup fail closed（不生成）
```

* binding index 空间 = 原始非空行序（B1 兼容）；binding 指向指示行 →
  `invalid_binding` fail closed；
* 全部行都是指示 → 无 spoken 台词 → 按 no-dialogue 处理（resolve 返回 None）；
* 指示/提示进 prompt 的 non-spoken 区并显式标注 not spoken（E2E 样本 H 真机验证）。

### Q7 audio_event_intent 最终 contract 是什么？

挂在 `ShotProductionBinding` 的可选块（`AudioEventIntent`）：

```text
music_policy: "none" | "allow_native"     （两侧 fail-closed 校验）
directives:  [非空短句，…]                （意图，不是时间线）
```

* 读取与路由解耦：`read_audio_intent` 直接读 bindings.json——audio-intent-only
  binding 按 §3 路由 UNBOUND，但 intent 照常生效（E2E 样本 E 即此形态）；
* directives 确定性进 compiler sound 区；绝不出现时间戳/SFX 资产引用（测试锁死）；
* 非法 music_policy / 空 directive：写入（service 回执 fail closed）与读取
  （NativeAVError）两侧拒绝；
* 明确不做：SFXAsset/SFXVersion/AmbienceVersion/AudioStem/AudioTimeline（测试锁死）；
* binding_revision 随块翻动 → 既有 race guard 自动覆盖。

### Q8 music_policy 如何工作？

```text
dialogue 非空 + 无显式块 → 默认 "none"（no-BGM 语句进 prompt；保留门声/脚步/环境声）
无台词   + 无显式块 → 默认 "allow_native"（不加任何声音约束 → visual-only 行为不变）
显式 "none"         → no-BGM 语句
显式 "allow_native" → 抑制 no-BGM 语句（特殊镜头 opt-in；非正式 Score 系统）
```

### Q9 BOUND / UNBOUND 如何共享同一 Native AV compiler？

唯一编译点 `compile_native_av_prompt(NativeAVGenerationPack, visual_prompt)`：

```text
UNBOUND visual input ─┐
                      ├→ build_native_av_pack（同一 dialogue/language/audio intent
BOUND visual input ───┘   解析）→ Prompt Compiler → Agnes
```

BOUND 侧：adapter 在 creative+ctx+style 之后调用同一 pack+compiler（prompt =
`Create a smooth cinematic transition…+ Scene/角色/风格 + AV 编译区`）；
UNBOUND 侧：handler 在 legacy prompt 之后调用。差别只能来自 visual input source
（测试 `test_bound_and_unbound_share_dialogue_zone` 锁死：两路 AV 区逐字节一致）。
E2E 双路真机跑通（A-F/H UNBOUND + G BOUND，见 §4）。

### Q10 VideoVersion generation_basis 最终字段是什么？

`VideoVersion.generation_basis: Dict`（可选，`{}`=legacy；asdict/字段过滤解析，
旧 JSON 加载零破坏）：

```text
schema("native_av_generation_basis_v1") / shot_id
visual_basis_fingerprint / dialogue_fingerprint / speaker_map_fingerprint /
spoken_language / audio_event_intent_fingerprint / music_policy /
prompt_compiler_schema                      ← 七个依赖键（freshness 对比集）
provider / model / provider_mode / provider_options_fingerprint  ← per-generation 事实
```

attach 语义：submit 路径记录派生 basis；**recover（续轮询）路径诚实记 `{}`**
（原始编译输入不可考 → untracked_legacy，不冒充 fresh）。

### Q11 freshness 如何计算？

`native_av_freshness(project_dir, project, shot)`（纯读模型）：

```text
missing          无 selected/final video（或版本未 done）
untracked_legacy generation_basis={}（旧版本/override-off/recover——可继续播放/保留/
                 手工选择，系统诚实表示无法证明其 Native AV freshness）
current          stored basis 与当前重算依赖键一致
stale            任一依赖键不一致（detail.mismatched_keys 逐键列出；
                 recompute 失败也如实 stale+reason）
```

对比键 = `NATIVE_AV_DEPENDENCY_KEYS` 七键（actual dependencies only）。
**对 §25 的记录在案的收窄**：`provider/model/options` 记录进 basis（provenance）
但不参与 freshness 对比——provider 是 per-generation 事实而非 current canonical
依赖（与 ImageBatch/PackBasis 先例同口径：模型切换不追溯作废旧产物；§25 的
"例如"清单在此收窄，理由如上）。language recompute 带 override 同窗对齐；
freshness 读模型传 None（override 是 ephemeral request 事实）。
HTTP：`GET /api/project/{p}/shot/{s}/video/freshness`。
**对 §25 的收窄**：`provider/model/options` 记录进 basis（provenance）
但不参与 freshness 对比——provider 是 per-generation 事实而非 current canonical
依赖（与 ImageBatch/PackBasis 先例同口径；模型切换不追溯作废旧产物，重生成即新版本）。

### Q12 race window 如何保护新增 dependency？

§27 双窗（A2 av_service 同口径）：

* **pre-dispatch**：provider 调用前重算七依赖键 vs pack basis——canonical 已变 →
  `native_dependency_changed_before_generation`，零 provider 调用；
* **pre-commit**：poll 完成后、attach 前重算——已变 →
  `native_dependency_changed_before_commit`，**不建 VideoVersion、不设
  final_video、不冒充 CURRENT**（handler 级真链测试锁死：窗口内改台词 →
  旧 final 保持、零新版本）；
* `language_override` 与本次 generation 编译同窗对齐（否则 override 生成会被误判）；
* binding 侧依赖（dialogue_speaker_binding/audio_event_intent）经 binding_revision
  由既有 S3C.2 pre-dispatch guard 覆盖；
* 不长时间持 project lock（重算 = 轻量 canonical 读）。

### Q13 Director 做了哪些最小 prompt 修改？

仅 `DIRECTOR_SYSTEM` 的 dialogue 条目追加 5 条创作纪律 + 工具 schema description
镜像（不改 Runtime/tools/Sub-Agent 架构，§19 边界）：
① 多角色每句明确 speaker 前缀；② dialogue 只写真正说出口的文本，动作/语气/镜头/
音效说明归视觉字段；③ 台词依赖动作时 motion/action_sequence 与台词节奏匹配；
④ 无明确价值不在极短镜塞多角色轮流说话；⑤ 声音克制，只写叙事上重要的声音。
§20 ownership 不变：Script Writer 仍独占对白内容，Director 未扩权。

### Q14 哪些东西明确没有实现？

§2 禁止清单全部未做：Voice Reference / audios[] / Agnes reference mode /
Visual reference pipeline 重构 / A2(SyncLabs) / source separation / 独立对白音频
替换 / Music generation / Sequence Audio Post / Final Mix / Sound Designer Agent /
新 AV canonical / 新 VideoVersion 类型（Native AV 仍是现有 Video Production 的
能力升级）。另如实记录三个设计收窄：(1) provider 事实键不参与 freshness 对比
（Q11）；(2) freshness 无自动 UI 面板——只读端点 + 评审页展示（§34 不做正式 UI）；
(3) BOUND 全链真机依赖复用 S3C.2 golden path 项目 fixture（含边界帧显式重生——
本身即 §17 语义的真机演练）。

### Q15 targeted / full regression 结果？

```text
C1-A targeted: 117 passed（含 S3C1/S3C2 路由套件）
C1-B/C/D/E targeted 累计: 263+213 passed（零回归）
全量回归（C1-F 收口）：1939 passed，零新增 failure（B1 基线 1861 → +78 新增测试）
既有 4 处测试按 C1 语义有意更新：test_interface / test_shot_request_rules
（补生产前提：项目语言+角色）、test_resources_phase1b T17（project.json shape
+native_av 键）、B1 flag-off 语义改「显式 False=override」
```

§31：现有 Visual V1 regression = 无（S3B.3/S3C.1/S3C.2 套件全过）。

### Q16 real E2E 结果？

真机 Agnes（生产 handler 全链，非实验入口）——`_c1_native_av_e2e/`：

| 样本 | 路径 | 内容 | 结果 |
|---|---|---|---|
| A | UNBOUND | 单角色长句 | DONE，freshness=current |
| B | UNBOUND | 极短中文对白「我们走。」 | DONE，current |
| C | UNBOUND | 双人 A→B→A 三句 | DONE，current |
| D | UNBOUND | 双人仅一人说话 | DONE，current |
| E | UNBOUND | audio_event_intent（门轴/脚步 directives） | DONE，current |
| F | UNBOUND | 易自动 BGM 的对白镜（music 默认 none） | DONE，current |
| H | UNBOUND | stage direction 真实格式 | DONE，current |
| G | **BOUND** | 完整 binding + boundary frames + dialogue | DONE，current |

* 8/8 生成成功；编译内容验证：G prompt=视觉连续性上下文+AV 区；H 的指示进
  non-spoken 区且台词区无指示文本；E 的 directives 进 sound 区 + no-BGM 语句；
* 真实 VideoVersion 落库（prompt_snapshot=完整编译 prompt、generation_basis 七键+
  provider 事实）、final_video 指针、freshness 端点读回 current；
* 过程亮点：G 首跑如实触发 `boundary_frame_stale`（GP 项目边界帧相对当前 binding
  revision 过期）——按 §17 语义经生产首尾帧 handler **显式重新生成**后通过，
  freshness 体系在真机首次完整演练；
* 配额：45.33 video_seconds + 2 images（budget 400s 内）；
* 证据：`_c1_native_av_e2e/evidence/<A-H>/`（job_receipt / video_version /
  freshness / ffprobe / video.mp4）+ `review_manifest.json`。

### Q17 human review 页面在哪里？

```text
启动："D:/Delta Force/python.exe" _native_av_c1_review_server.py
地址：http://localhost:8911/                （本报告提交时已在 8911 运行）
反馈：_c1_native_av_e2e/results/C1_REVIEW_FEEDBACK.json
维度（§34 十项/条）：① 台词正确性 ② 说话人分配 ③ 语言 ④ 原生 lip-sync
⑤ 表演自然度 ⑥ 视觉质量 ⑦ SFX/环境声贴合 ⑧ 意外 BGM ⑨ 视觉连续性（仅 G）
⑩ 整体 AV + 自由短评；每卡附 compiled prompt / generation_basis / freshness 折叠区
```

---

## 3. §35 人评重点（重申）

本轮不再验证「B1 是否成立」，主要判断：

```text
1. Productionization 是否引入 regression？
2. 新 Prompt Compiler 是否进一步减少误读 / BGM？
3. Audio Intent 是否真正改善动作-声音匹配？
4. BOUND path 是否同样稳定？
5. Freshness / provenance 是否正确？
```

---

## 4. §36-§39 预案状态（供人评后对表）

* §36 成功标准：实现+自动测试+全量回归+真机 E2E 已全部达成；
  「human review 无系统性退化」待人评；
* §37 Audio Intent 效果一般 → 允许 `Dialogue/Speaker PASS + Audio Intent PARTIAL`，
  不阻塞 foundation，不拆音轨；
* §38 BGM 偶发 → 先判 prompt 可改善 vs provider 边界；极少量 prompt iteration，
  不做 source separation；记录 provider stochastic boundary → variant
  regeneration + selection 处理；
* §39 多人 swap → 先查 binding/prompt layout/staging；偶发 → regenerate variant；
  系统性 → 另立专项（不自动进入 Voice Reference）。

---

## 5. 明确不做（§42 STOP）

Voice Reference / Audio Post / Music / Final Mix——用户评审后再确定下一阶段。
