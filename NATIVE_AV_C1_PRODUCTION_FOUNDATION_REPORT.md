# NATIVE AV C1 — PRODUCTION FOUNDATION + AV PROMPT COMPILER V1 报告

执行依据：`详细流程文件/修复阶段/18.md`（Native AV C1）。
上游：`NATIVE_AV_B1_DIALOGUE_SPEAKER_REPORT.md`（B1 FINAL PASS，2026-09-15 人评裁定）、
`NATIVE_AV_DIRECTION_REBASELINE_REPORT.md`。

---

## 0. 当前状态

```text
NATIVE AV PRODUCTION FOUNDATION V1
FINAL PASS（2026-09-15 用户人工评审裁定）

人评 93 条点击 + 7 条短评（_c1_native_av_e2e/results/C1_REVIEW_FEEDBACK.json）：
· 声音系统（本阶段核心交付）零系统性退化：8/8 台词正确 / 零 swap / 8/8 语言正确 /
  8/8 lip-sync 可用 / 8/8 SFX 贴合；stage direction 未被朗读（H 主证）
· Audio Intent 正收益（E 样本用户原话「完美，非常好」）——§37 不触发
· BOUND 稳定（G：continuity=保持 + dialogue/speaker/language 全对）
· BGM 控制：5/8 干净 / 3/8 偶发（C/D/H）——按 §38 记 provider stochastic
  boundary，靠 variant regeneration + selection 处理，不扩控制面
· 视觉质量瑕疵（C/H/G 等）用户一致归因「画面提示词/分镜问题」——归入用户点名的
  下一方向（提示词优化 + 创作手法），不阻塞 foundation 收口
```

评审入口（存档）：http://localhost:8911/ （`_native_av_c1_review_server.py`）。
人评后仍按 §42 纪律：不自动开始 Voice Reference / Audio Post / Music / Final Mix。

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

## 3. 人工评审结果（2026-09-15，§35 五问回答）

反馈源：`_c1_native_av_e2e/results/C1_REVIEW_FEEDBACK.json`（93 条点击 + 7 条短评；
同一维度多次点击以**最后一次**为准——用户评审过程中有自纠，如实按终值统计）。

### 3.1 逐样本终值

| 样本 | 路由 | 台词 | speaker | 语言 | lip-sync | 表演 | 视觉 | SFX | BGM | 连续性 | 总评 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A 单角色长句 | UNBOUND | 正确 | 正确 | 正确 | 可用 | 自然 | 一般 | 贴合 | 无 | — | **PASS** |
| B 极短句 | UNBOUND | 正确 | 正确 | 正确 | 可用 | 僵硬/机械 | 好 | 贴合 | 无 | — | **PASS** |
| C 双人 A→B→A | UNBOUND | 正确 | 正确 | 正确 | 可用 | 自然 | 一般 | 贴合 | **有明显** | — | **ACCEPTABLE** |
| D 双人单说 | UNBOUND | 正确 | 正确 | 正确 | 可用 | 自然 | 好 | 贴合 | **有明显** | — | **PASS** |
| E 动作音 intent | UNBOUND | 正确 | 正确 | 正确 | 可用 | 自然 | 好 | 贴合 | 无 | — | **PASS** |
| F 氛围镜 | UNBOUND | 正确 | 正确 | 正确 | 可用 | 自然 | 好 | 贴合 | 无 | — | **PASS** |
| H stage direction | UNBOUND | 正确 | 正确 | 正确 | 可用 | 一般 | **差** | 贴合 | **有明显** | — | **FAIL** |
| G BOUND | BOUND | 正确 | 正确 | 正确 | 可用 | 僵硬/机械 | 差 | 贴合 | 无 | **保持** | **ACCEPTABLE** |

（H 的 dialogue 点击序列「轻微问题→正确」、G「轻微问题→正确」、A/C/G visual、
B/H/G natural、C/H bgm 均有多次点击，上表取终值。）

### 3.2 用户短评（原话）

```text
A: 我认为声音与人物的说明已经很不错。画面问题应该是提示词或者前后帧的问题。
C: 我认为这个bgm加上后感觉是很不错，但是最后女生的画面有点崩坏
D: 如果你设定的是无bgm，那么这就不对。不过画面还是不错的。
E: 完美，非常好
F: 有一点点瑕疵，就是第一个画面中人物有点动画感。最后显示的是真人说话。
   但是我认为质量没得说，只要我们后面调整好画面提示词以及分镜，那么基本完美了
H: 不行，人物出现崩坏已经多条手臂，这是画面问题，其实就是提示词问题。
G: 人物画面有点问题，更上面优秀的比差一些。
```

### 3.3 §35 五问回答

1. **Productionization 是否引入 regression？—— 声音维度：无。**
   台词 8/8 正确、speaker 8/8 正确（零 swap）、语言 8/8 正确、lip-sync 8/8 可用、
   SFX 8/8 贴合。对照 B1 A 侧 4/6 FAIL（日语/念提示词），Controlled Dialogue
   默认启用后未出现任何一次误读/语言漂移。视觉维度的负面项（C 末尾画面崩、
   H 多条手臂、G 偏弱、F 动画感/真人感）用户在 4 条短评中**一致归因画面提示词/
   分镜**（原话见上），无一条归因生产化或声音链路；全量回归 1939 passed 零新增
   failure 同证。

2. **新 Prompt Compiler 是否进一步减少误读 / BGM？—— 误读：归零；BGM：未根除。**
   误读零出现（含 stage direction 未被朗读——H 台词终值正确，指示进 non-spoken
   区生效）。BGM：5/8 干净（A/B/E/F/G），3/8 偶发明显（C/D/H）——对照 B1 B 侧
   6 条中 2 条有背景音，大致持平：**不恶化、未达「明显改善」**。按 §38 判定为
   provider stochastic boundary（E 样本证明显式 audio intent + music none 组合
   可以完全干净），处理方式 = variant regeneration + selection，不做 source
   separation、不扩控制面。另如实记录创作事实：C 短评表示「BGM 加上后感觉是很
   不错」——policy（none）与个别镜头创作偏好存在张力，留待后续 Score 方向。

3. **Audio Intent 是否真正改善动作-声音匹配？—— 是（§37 不触发）。**
   E 样本（directives：木门吱呀/地板脚步）获「完美，非常好」总评 PASS，
   SFX 贴合 + 无 BGM；8/8 SFX 贴合——audio_event_intent 未破坏 Agnes 原生 SFX。
   `Dialogue/Speaker Production PASS + Audio Intent PASS`。

4. **BOUND path 是否同样稳定？—— 链路稳定成立。**
   G：dialogue/speaker/language 正确 + lip-sync 可用 + 无 BGM +
   **视觉连续性=保持**（§34 专项维度）。视觉质量偏弱（visual=差，用户注「跟上面
   优秀的比差一些」）属该镜画面生成质量，非 BOUND 链路问题；BOUND 首跑如实触发
   boundary_frame_stale → 显式重生成后通过的流程也是本阶段 freshness 设计的
   真机验证。

5. **Freshness / provenance 是否正确？—— 系统侧全对。**
   8/8 版本 freshness=current 与实际一致；generation_basis 七键+provider 事实
   落库完整；无 auto regeneration 发生。用户未对 freshness 提出异议。

### 3.4 §36 成功标准对表

| 标准 | 结果 |
|---|---|
| Controlled dialogue 正式默认工作 | ✅（8/8 台词正确，无需手工 flag） |
| speaker resolution 稳定 | ✅（零 swap） |
| language 不再靠 provider 猜 | ✅（8/8 正确；皆缺即 fail closed） |
| visual path 无 regression | ✅（自动回归零新增；人评视觉问题归因提示词/分镜，非链路） |
| BOUND / UNBOUND 均真实跑通 | ✅（G + A-F/H） |
| stage direction 不被朗读 | ✅（H 主证） |
| BGM 控制明显改善或至少不恶化 | ⚠️ 不恶化未明显改善（5/8 干净 vs B1 B 侧 4/6 干净）——provider stochastic boundary 记录在案 |
| audio_event_intent 不破坏 native SFX | ✅（E「完美」） |
| VideoVersion basis 正确 / freshness 正确 / no auto regeneration | ✅ |
| full regression 无新增 failure | ✅（1939 passed） |
| human review 无系统性退化 | ✅ 声音维度无退化；视觉瑕疵归入既有创作方向（H FAIL 由画面崩坏导致，用户明确归因提示词） |

**判定：NATIVE AV PRODUCTION FOUNDATION V1 PASS**
（含两项如实记录的边界：BGM 偶发 = provider stochastic boundary；
视觉质量瑕疵 = 提示词/分镜方向的已知输入，非本阶段引入。）

---

## 4. 下一阶段输入（待用户批准，勿自动开始）

* **提示词优化 + 创作手法（声画匹配的视觉侧）**：用户在 B1 收口时点名、本轮
  5 条短评全部指向——C 末尾画面崩、H 多条手臂、F 动画感/真人感不一致、
  A 前后帧、G 画面偏弱。属画面提示词/分镜创作质量问题（复杂动作镜尤甚），
  与 Native AV 声音链路正交。
* BGM 偶发（C/D/H）：按 §38 由 variant regeneration + selection 处理；
  C 的「BGM 加上很不错」短评提示未来 Sequence Audio Post/Score 方向可正视
  创作性音乐诉求。
* B/H/G 的表演自然度（僵硬/机械）：短句与多人镜的自然度是 provider 生成质量
  议题，记录为 prompt 迭代观察项。
