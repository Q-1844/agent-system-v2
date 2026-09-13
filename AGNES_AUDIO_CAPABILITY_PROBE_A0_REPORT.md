# AGNES_AUDIO_CAPABILITY_PROBE_A0_REPORT

> **A0 阶段：只调查 Agnes 真实音频能力（measure first, design later）。**
> 生产代码 diff = 0。全部产物在 scratch `_audio_probe/`（不进 git）。
> 本报告只有 **CAPABILITY FINDINGS**，不包含任何声音架构设计（14.md §23 约束）。

---

## 0. 元信息与方法

| 项 | 值 |
|---|---|
| 调查日期 | 2026-09-13 ~ 09-14 |
| Endpoint | `https://api.agnes-ai.cn`（免费档账户 key） |
| 样本总量 | **106 条真实视频**（99 条测试 spec + 13 条能力探针生成）+ 15 张首帧图 |
| 服务端视频用量 | **476.5 秒**（probe 独立账本） |
| 模型 | agnes-video-v2.0（80 条）、agnes-video-2.5-flash（9 条对照 + 7 探针）、reference 模式 9 条 |
| 证据规范 | 每条：request.json / create_response.json / final_poll.json / video.mp4 / audio.wav / ffprobe.json / loudness.json / notes.json / spectrogram.png / waveform.png / acoustic.json / stt.json |
| 客观工具 | ffprobe / ffmpeg ebur128+volumedetect / numpy+scipy 频谱·F0·chroma / faster-whisper small（STT+CER） |
| 人工听评 | **尚未进行**——所有需要耳朵的结论已备好 AB 拼接 wav（25 组，见 §5 清单），逐项标注 |

**本报告结论分三级标注**：
- `[客观]` = 程序测量/STT/频谱可直接证实
- `[程序代理]` = 客观指标强指向，但最终判别需要人耳/人眼
- `[待人工]` = 只能由听评/看片给出

---

## 1. §2 Provider Capability Matrix（全部来自真实 API 探针与实测，无行业常识推测）

| Capability | agnes-video-v2.0 | agnes-video-2.5 | agnes-video-2.5-flash |
|---|---|---|---|
| native audio output | ✅ AAC **48kHz** stereo（82 条实测） | （付费墙，未取得样本，见下） | ✅ AAC **32kHz** stereo（24 条实测） |
| dialogue from prompt | ✅ 精确可控（§4 D/E） | 未测 | ✅（XD 组 CER=0） |
| music from prompt | ✅ 生成（但身份不受控，§7/8） | 未测 | 未测（推定同 v2.0） |
| ambience / SFX | ✅（§3 A/B） | 未测 | ✅（探针静音样本） |
| **audio reference input** | ⚠️ `audio`/`extra_body.audio` 字段被接受但**无可闻效果**（p6/p7，输出=普通环境音）→ 判定静默忽略 | 未测 | ✅ keyframe 契约收 `audio`+`audio_url`；**reference 契约收 `images[]`+`audios[]`+`videos[]`**（p1/p10/p11 报错原文点名） |
| audio reference 效果 | 无 | 未测 | ✅ **控制音色/音区，不控制内容**（§9 L/LB/p13） |
| number of audio refs | — | — | ≥1（数组字段；多参考未测） |
| keyframe + audio ref 同时 | — | — | ✅ 形状上可同用（p3/p4 = keyframe+audio 字段 200） |
| image reference | ✅ i2v / keyframes | keyframe 模式（既有实测） | ✅ keyframe 单帧 + **reference `images[]`**（p11 实测，视觉身份保持，§12 O） |
| first/last frame | ✅ legacy keyframes | 未验证（生产代码拒绝，既有事实） | 未验证 |
| seed | ✅ | 未测 | ✅（p12 单独测 200） |
| negative_prompt | ✅ legacy 参数 | ❌ 400「不是允许的请求字段」 | ❌ 同左（严格 schema 白名单） |
| 文本模型音频输入（STT 路线） | ❌ chat completions 拒 audio_url 内容块（p8） | 同左 | 同左 |

**🔴 定价分层（新事实，建议回填 AGNES_API_FACTS）**：`agnes-video-2.5`（非 flash）**不在免费额度内**——403 `insufficient_user_quota`「用户剩余额度: ￥0.10，需要预扣: ￥0.75」（5s），两次复现；同时刻 `agnes-video-v2.0` 与 `agnes-video-2.5-flash` 均正常出片。**v2.0 / 2.5-flash 维持免费；2.5 非 flash 是预付费模型（¥0.15/s）。**

**输出格式分层**：v2.0 → 1280x704 / AAC 48kHz；2.5 家族 → 1280x720 / AAC 32kHz。

**Schema 探针报错原文（2.5 reference 模式）**：
- `"reference 模式需要 images、audios 或 videos"`
- 带 `image`（单数）→ `"reference 模式不能包含 first_frame 或 last_frame"`（单数字段被内部映射为 first_frame；**复数 images/audios/videos 才是 reference 模式正确字段**）
- `"negative_prompt 不是允许的请求字段"`

---

## 2. Test A — Native Audio Baseline `[客观]`

同一张 Alice 首帧 + 同一 prompt（安静室内、角色不讲话、**不要求任何声音**）×5（v2.0）+ 2.5-flash ×3。

- 集成响度 **-34.5 ~ -41.7 LUFS**（很安静；语音常态是 -16~-24 LUFS）
- 频谱：低频谐波堆（房间底噪/闷雷）+ 2 次短促宽带瞬态（轻微拟音事件），**无音乐结构、无语音共振峰**
- 2.5-flash 探针对照（CAP_p3/p12）：同为安静环境音（-30 LUFS 档）

> **结论：完全不指定声音时，Agnes 不强行配乐/加人声，默认生成安静环境音 + 偶发小 SFX。** 用户观察「自动配乐有时尚可、环境音通常尚可」中的"环境音"基线成立；"自动配乐"在本基线场景（室内安静）并未主动出现——它更可能在戏剧化场景被触发（见 CR/PM 组：要求才生成，但生成后响度可观 -11~-26 LUFS）。

---

## 3. Test B — Environment / Foley `[客观签名 + 待人工同步]`

| 事件 | 响度 | 声学签名 | 判读 |
|---|---|---|---|
| B1 木地板行走 ×3 | -33~-35 LUFS（轻） | onset 1.5-2.4/s、质心 437-623Hz（低频闷响） | 与脚步+房间 tone 相符 |
| B2 放下金属枪 ×3 | -27.6~-31.2 LUFS | **onset 仅 0.59-1.48/s（少量独立事件）**、B2_01 flatness 0.0001（金属泛音环） | 与"一次放置接触声"相符 |
| B3 海边强风 ×3 | **-23~-25.9 LUFS（响）** | 质心 846-1000Hz、onset 3.6/s（宽带呼啸） | 与风+浪相符 |

> **结论：三类目标声的声学签名全部与 prompt 事件对应，Agnes 对 visual-aware SFX/ambience 有真实能力。** 事件是否**精确同步**于画面动作（手触枪面的瞬间等）无法程序判定 → `[待人工]`（听 B2 三条 + 看画面）。拼接：`results/montage/B1_footsteps_3.wav`、`B2_metal_pistol_3.wav`、`B3_sea_wind_3.wav`。

---

## 4. Test D/E — 精确对白（中/英）`[客观]`

whisper small 转写 + 字级 CER（参考句 = prompt 台词）：

| 组 | 语言 | n | CER 均值 | 最差 | 明细 |
|---|---|---:|---:|---:|---|
| DS 短句「我们现在出发」 | 中文 | 5 | **0.000** | 0 | 5/5 逐字全对 |
| DL 长句（灯塔台词 19 字） | 中文 | 5 | **0.055** | 0.091 | 4/5 全对；DL_03 首二字近音错「灯塔→等他」 |
| ES 短句 "We leave now." | 英文 | 5 | **0.000** | 0 | 5/5 全对 |
| EL 长句 | 英文 | 5 | **0.000** | 0 | 5/5 全对 |
| XD（2.5-flash 短句） | 中文 | 3 | **0.000** | 0 | 3/3 全对 |

- 性别/年龄感（听评项）`[待人工]`；clarity/prosody `[待人工]`（拼接：`D_short_cn_5.wav` / `D_long_cn_5.wav` / `E_short_en_5.wav` / `E_long_en_5.wav`）
- **中英对比：无中文特有弱点**（D/E 同结构同精度）→ 用户观察「人物说话质量差」若指**音质/音色**，不是文本转写层的语言问题

> **结论：指定台词的文本层可控性极强（CER 0~9%）。语音质量本身（音色自然度）待人工。**

---

## 5. Test F — Same Character Voice Identity（最高优先级）`[客观散布 + 待人工盲听]`

固定 Alice 视觉/年龄/场景家族，8 个不同 Shot 各一句短对白（**不描述任何音色**）：

**F0 中位数（跨镜头声线客观锚点）**：

```
F_01:244Hz  F_02:329Hz  F_03:333Hz  F_04:296Hz  F_05:111Hz*  F_06:245Hz  F_07:318Hz  F_08:390Hz*
           *F_05/F_08 疑似自相关八度误判（频谱谐波间距复查中），剔除后仍散布 244-333Hz
```

**同文本对照组（证明散布不是台词差异造成）**：
- DS（同图同句原生 ×5）：229-356Hz，散布 **127Hz**
- XD（2.5-flash 同句 ×3）：121-267Hz（XD_02 落入男声区——**女性角色图不保证女声**）
- DL（同句长台词 ×5）：249-303Hz

**文本层同时崩坏**：F 组 CER=0.51——F_01「我们走」→日语「おぬぞ」、F_03「门锁住了」→「むんそくちゅれ」、F_07「快进去」→「快勤ぜ」；**4/8 台词正确、4/8 乱码或日语**。（I 组构图变化更差：CER 0.60，I_04 整句日语）

> **结论：同角色跨 Shot 的语音身份不稳定 `[客观]`——音高散布 + 50% 台词崩坏率（动画风画面+短句会把模型推向日语语音先验）。** 用户观察「切换 Shot 后人物音色可能改变」被客观证实。
> **盲听评审包**：`results/montage/F_voice_identity_8shots.wav`（8 条拼接，1=明显不同人 … 5=稳定同一人，两轮独立打分表见 §16）；配套 I 组 `I_composition_5.wav`。

---

## 6. Test G — Same BGM Across Shots `[客观]`

同一连续场景 5 Shot，每条都要求「同一低沉悬疑的弦乐配乐持续演奏」：

- 相邻镜头 chroma 余弦：0.503 / 0.774 / 0.638 / 0.491；首尾 0.458
- **对照 CR（同一音乐 prompt 重生成 ×3）：0.58-0.69** —— 与 G 组同区间
- （同 prompt 环境音对照 A_01|A_02 = 0.92，证明该指标对"真的相同的内容"是敏感的）

> **结论：prompt 文字不足以维持跨 Shot 音乐身份 `[客观]`——不仅跨镜头不维持，连同一 prompt 两次生成的音乐都不同。** 文字指定的"风格"能触发音乐层（CR/PM 都有音乐），但 key/tempo/motif/配器身份每次独立采样。拼接：`G_music_5shots.wav`。

---

## 7. Test H — "Continue Previous Music" `[客观]`

G_01 为锚点，后续 3 条加「Continue exactly the same background score from the previous shot. 保持与上一个镜头完全相同的配乐」：

```
G_01|H_02 chroma=0.649  →  G_01|H_03 0.564  →  G_01|H_04 0.341
```

相似度随链距离单调衰减，H_04 已**低于**重生成噪声区间下限（0.49）。

> **结论：`[客观]` 文字延续指令不是音频身份。textual continuity instruction is not audio identity——与 14.md 预案一致。** 拼接：`H_chain_anchor_G1_plus_3.wav`（含锚点共 4 条）。

---

## 8. Test I — Same Character + Different Shot（构图 vs 声线）`[客观 + 待人工]`

wide/medium/close-up/侧面/攀爬动作 5 种构图，各一句短对白：

- 文本：4/5 正确；I_01「跟我来」→「恭喜你来」（错词）、I_04「就在前面」→**整句日语**
- 与 F 组合并解读：**构图/景别变化会同时扰动声线采样与台词保真**——视觉构图改变确实导致模型重新采样 speaker identity
- F0/音色逐镜对照 `[待人工]`：`I_composition_5.wav`

---

## 9. Test J — Two Speakers `[客观文本 + 待人工分配]`

Alice+Bob 同框，三人次对白（「门开了吗？」「还没有。」「那就快一点。」）×5（8s）：

- **STT：5/5 三句全对、零漏句、零改写、零 swap 文本** `[客观]`
- F0 双峰：男声带（<175Hz）帧占 27-35%——Bob 的语音真实存在且与 Alice 的女声（249-306Hz）可分 `[程序代理]`
- 音色分化质量/是否 swap/对话自然度 `[待人工]`：`J_two_speakers_5.wav`

> **结论：多角色对白的**文本编排层可靠（台词全中、顺序全对）；**说话人音色分离**初步存在但需听评定级。

---

## 10. Test K — Lip Sync `[待人工 + 客观辅助]`

对 6 条最清晰样本生成语音区间 vs 帧条带（`results/lipsync/*_sheet.png`，含 speech_interval.json）：

- 目检（EL_01 条带）：嘴部在语音期持续开合；语音检测区间结束后嘴仍动——但 -40dB silencedetect 对安静母带**低估语音区间**（英文长句实际 ~4s，检出 0.6s），不能据此判"嘴动先于/晚于声音"
- 「嘴在动」≠「精确 lip sync」——音节级 plausibility、起止同步 `[待人工]`（看 6 张条带 + 对应视频）
- 未发现反向证据（无"完全静止的脸配语音"样本）

---

## 11. Test L / LB — Audio Reference 控制什么（2.5-flash reference 模式）`[客观]`

**API 通路**：p10 证实 `mode:"reference" + audios:["data:audio/wav;base64,..."]` 200 可用（data URI 免图床）。

**LA 组（参考音=晓晓女声 231Hz + prompt 指定同一句台词）×3**：

| 项 | 结果 |
|---|---|
| F0 中位数 | **233 / 235 / 233 Hz——散布 2Hz，锁死在参考音 231Hz**（对照组 DS 同句原生散布 127Hz） |
| 内容 | 2/3 逐字全对，1/1 近音错（CER 0.049）——内容来自 **prompt 文本** |

**LB 组（同一参考音，prompt 不含任何文本）×3**：

| 项 | 结果 |
|---|---|
| 内容 | 参考音的台词**没有被迁移**：1 条 stock 问候语「大家好…」、2 条乱码（CER≈1.0） |
| F0 | 237-255Hz——音区仍被参考音拉住 |

**p13 鉴别实验（男声参考云希 ~120Hz + 女性角色图）**：

| 项 | 结果 |
|---|---|
| F0 | **151Hz（123-196）**——输出音区跨性别跟随男声参考（该角色原生女声 233-390Hz） |
| 内容 | 仍是 stock 问候语（不跟随参考音文本） |

> **结论（本 probe 最硬的发现之一）`[客观]`：**
> **audio reference = 音色/音区（pitch register）控制器，效果强且跨性别生效；≠ 内容控制器（台词文本完全不被迁移）；是否达到"同一个人"级音色克隆 `[待人工]`（LA_01-03 与参考音 AB 对听：`LA_voice_ref_3.wav` vs `assets/ref_alice_edge.wav`）。**
> 附：CAP_p4（keyframe 模式 + `audio_url` 字段）产出语音但 F0=142Hz 未跟随参考 → **keyframe 模式的 audio 字段无音色控制效果；音色控制只在 reference+audios 通路上实测成立。**

---

## 12. Test M — External Voice as Reference `[客观 + 待人工]`

外部语音源 = 项目既有 edge-tts（晓晓/云希，免费合法）——即 §11 的 LA/LB/p13 全部使用外部 TTS 作参考：

- voice identity retention：**音区级保持 `[客观]`**；"像不像同一个人" `[待人工]`
- speech content retention：**无 `[客观]`**（模型不会"替你说参考音的词"）
- lip synchronization：模型自己重新生成语音后对口型（参考音文本与画面语音无关）
- visual degradation：reference 模式 + `images[]` 视觉身份保持良好（§14 O）

> **外部声音管线若要"指定台词"，必须 prompt 文本+参考音双通道：文本管内容、参考音管音色。**

---

## 13. Test N — Music Reference `[程序代理 + 待人工]`

reference+`audios` 送入 10s 合成测试音乐（A 小调分解和弦垫，音级 A/C/E）×3：

- chroma 余弦 vs 参考音乐：**0.725 / 0.867 / 0.872**——显著高于一切文字条件（G 组跨镜 0.49-0.77、CR 同 prompt 重生成 0.58-0.69）
- LN_02/03 顶音级含 A/C/E 和弦音 → 调性被参考音乐拉住
- 是否"保留原曲/只保留调性/重构" `[待人工]`：`LN_music_ref_3.wav` vs `assets/ref_music_test.wav`

> **初步结论：音乐参考对调性有真实牵引力（文字做不到的事）——这可能是维持跨镜配乐 identity 的唯一原生通路候选，但保真度需听评定级。**

---

## 14. Test O — Reference/Audio 模式 vs Keyframe 模式 视觉权衡 `[程序代理]`

- **reference 模式 + `images[]` 不牺牲视觉控制**：CAP_p13/LA_02 抽帧目检——Alice 面容/风衣/灯塔房间保持一致（p13 甚至自行推近成面部特写）；输出 1280x720
- keyframe 模式同样原生带音频（XD 组有对白）
- 两者**不是互斥的二选一**：真正互斥的是「reference 模式禁用 first_frame/last_frame 单数字段」（schema 层）；同用 keyframe+audio 字段虽 200 但音频无控制效果（§11 附注）

> **结论：声音架构不必在"视觉一致性"与"声音控制"之间二选一——reference+images+audios 通路同时提供视觉锚与音色锚。** `[程序代理]`（抽帧样本有限，系统级对比 `[待人工]`：O1 = D/E 组（keyframe 原生）vs O2 = LA 组（reference+audio）逐条对看）。

---

## 15. Test P + §20 — Layer Control / Output Stems `[客观]`

| 指令组 | 实测 | 合规判读 |
|---|---|---|
| Dialogue only + no music + minimal ambience ×3 | 语音在场（CER=0）、LUFS -32~-34、微弱低频床 | 基本合规；"无音乐" `[待人工]` |
| Ambience only + no dialogue + no music ×3 | **-18~-21 LUFS（响）**、无语音、宽带呼啸 | 无语音 ✓；模型把"环境音"演绎成**响亮的暴风雨风声**（合规但响度失控） |
| Music only + no dialogue ×3 | -21~-29 LUFS、f0_voiced 0.87-0.93（谐波丰富=音乐在场） | ✓ |
| **Silent ×3** | **-48.8 / -50.5 / -51.7 LUFS，silence_ratio 0.82-0.955** | **≈真静音，可达成** ✓ |

**§20 Output Stems `[客观，106/106]`**：全部样本恒为**单一混合立体声 AAC 轨**。没有 speech/music/SFX 分轨。听得出三层 ≠ 存在三轨——**no stems observed**。

> **结论：原生输出是"混合生成声轨"，不是可控分轨系统。** 可控性排序：静音（强）> 指定对白（强）> 音乐在场/缺席（中，C 组无响亮 BGM 但微弱谐波成分待听评）> 环境音响度（弱）> 音乐身份（无）。

---

## 16. §21 评分矩阵（1-5）

> 打分依据：`[客观]` 项由数据直接定级；`[程序代理]` 项由指标定级并待人耳复核；`[待人工]` 项给暂定分 + 标 (H)。**不提供单一总分。**

| Dimension | Score | 依据 |
|---|---:|---|
| ambience quality | 4 | A/B 组声学签名与环境匹配，响度自然 `[客观签名]`；美感 (H) |
| SFX quality | 3.5 | B 组事件签名匹配；同步精度未验 (H) |
| audiovisual synchronization | 3 (H) | 嘳动在语音期成立；音节级未验 |
| music quality single-shot | 3 (H) | CR/PM 在场且响度足；风格单一采样 |
| music continuity cross-shot | **1** | chroma=重生成噪声级，文字延续指令衰减至 0.34 `[客观]` |
| dialogue intelligibility | 4 | STT 全部可转写；崩坏样本是"语言错"非"听不清" `[客观]` |
| dialogue text accuracy | 3.5 | 单镜重复 CER≈0；**多镜各一句场景 CER 0.5+**（F/I）`[客观]` |
| Chinese speech quality | 3.5 (H) | 文本层无弱点；音色自然度待听 |
| English speech quality | 4 (H) | CER=0；自然度待听 |
| character voice consistency | **1.5** | F0 散布 127-222Hz / 8 镜 4 条崩坏 `[客观]` |
| multi-speaker reliability | 3.5 | 文本编排 5/5 全对 `[客观]`；音色分离 (H) |
| lip-sync quality | 2.5 (H) | 嘴动同步存在；音节级未验 |
| audio reference voice control | **4** | 音区跨性别跟随（2Hz 级锁定 vs 原生 127Hz 散布）`[客观]`；克隆级保真 (H) |
| audio reference rhythm control | 3.5 | 音乐参考 chroma 0.73-0.87 牵引 `[程序代理]`；节拍级未验 |
| prompt controllability | 2.5 | 对白/静音/音乐在场可控；音乐身份/环境响度不可控 `[客观]` |
| editability | **1** | 106/106 单一混音轨，no stems `[客观]` |

---

## 17. §22 最终回答

### A. Native Sound
- **ambience**：默认生成、质量良好、响度场景化（安静室内很安静、海边大风很响）——**最适合 Agnes 原生承担的层**
- **SFX**：事件对应签名正确（金属环、低频脚步）；同步精度待验
- **music**：不指定不出现（安静场景基线）；指定即生成；但**每次生成是独立采样**
- **speech**：指定台词即说；不指定不说（基线）；**特定画面+短句会自发说日语/stock 问候语**

### B. Dialogue
- accuracy：单镜重复 CER≈0（中英同优）；跨镜各一句 CER 0.5-0.6（4/8 崩坏，日语侵入）
- clarity：STT 层可转写；自然度 `[待人工]`
- language：中文无特有弱点；但动画风画面会诱发日语语音先验

### C. Voice Identity
- **不稳定（drifting）`[客观]`**：同角色跨 8 镜 F0 散布 244-333Hz+（含男声区离群），50% 台词崩坏

### D. Music Identity
- **regenerated（每次重采样）`[客观]`**：prompt 文字与"延续指令"都无法钉住；音乐参考（audio ref）是唯一显示牵引力的通路（chroma 0.73-0.87）

### E. Lip Sync
- 存在且方向正确（嘴动随语音期）；音节级精度 `[待人工]`

### F. Audio Reference
- **控制音色/音区（强、跨性别）；不控制内容（台词不迁移）；音乐参考控制调性（中）**
- 生效通路 = 2.5-flash `mode:reference + images[] + audios[]`（data URI 可用）；keyframe 模式的 audio 字段无效果；v2.0 的 audio 字段无效果

### G. Layer Control
- 静音：**可达成**（-48~-52 LUFS）
- 对白 only：可达成
- music only：可达成
- no-music：大体达成（无响亮 BGM；微弱谐波成分待听评）
- 环境音响度：不可控（"只要环境音"给到 -18 LUFS 暴风雨）
- **音乐身份：不可控（文字层完全无效）**

### H. Output Editability
- **只有混合立体声轨（106/106），no stems**——后期可做的只有整轨替换/混音，无法分轨编辑

### I. Visual Trade-off
- **未证实存在二选一**：reference+images 模式保持视觉身份且同时获得音色控制；keyframe 模式原生音频。真正损失的是：reference 模式不能用单数 image/first_frame/last_frame 形状；多参考图 keyframes（首尾帧）与 audios 同用未验证

### J. Recommended Production Role（按实测多选 + 适用条件）

**✅ 2. Agnes suitable for ambience/SFX, not precision dialogue/music** —— 环境与拟音是原生最强层（A/B 组），跨镜一致性要求低（环境音本身允许漂移）。

**✅（条件性）3. Agnes suitable as audiovisual performance engine driven by external voice** —— 唯一当生产需要"指定音色的角色开口"时：`reference + images[] + audios[]（外部 TTS 参考音）+ prompt 台词文本` 双通道（文本管内容、参考音管音色）。代价：走 2.5-flash（32kHz 音频）、台词保真 2/3（F/I 场景的崩坏率同样适用）、克隆级音色 `[待人工]`。

**❌ 1. 不能作为 final soundtrack 独立承担**——音乐身份跨镜不维持（G/H）+ 无 stems（§20）+ 50% 对白崩坏率（F/I 真实场景）。
**❌ 4. 不必整体弃用**——ambience/SFX 与静音控制有真实价值。

** hybrids（如"Agnes 出 ambience/SFX 底床 + 外部 TTS 出对白 + 外部音乐钉身份 + 分轨合成"）属于架构设计——按 §23 约束不在本报告展开。**

---

## 18. 人工听评清单（PENDING——报告交付后进行）

| # | 听评包 | 任务 | 对应测试 |
|---|---|---|---|
| 1 | `results/montage/F_voice_identity_8shots.wav` | 1-5 盲评 ×2 轮：是否同一人；gender/age/accent/pitch/timbre/prosody drift | F |
| 2 | `results/montage/I_composition_5.wav` | 构图变化下同一人？ | I |
| 3 | `assets/ref_alice_edge.wav` vs `results/montage/LA_voice_ref_3.wav` | 参考音 vs 生成音色：像同一人？ | L/M |
| 4 | `results/montage/J_two_speakers_5.wav` | 两声可分？台词分配对？swap？ | J |
| 5 | `D_short_cn_5.wav` / `D_long_cn_5.wav` / `E_short_en_5.wav` / `E_long_en_5.wav` | 音质/清晰度/韵律自然度 | D/E/K |
| 6 | `B1/B2/B3_*_3.wav` + 对应视频 | 事件同步（触枪瞬间等） | B |
| 7 | `C_no_music_5.wav` / `CR_music_only_3.wav` / `P_*.wav` | 层级合规（有无 BGM 溢出） | C/P |
| 8 | `G_music_5shots.wav` / `H_chain_anchor_G1_plus_3.wav` / `LN_music_ref_3.wav` vs `assets/ref_music_test.wav` | 音乐身份/保真 | G/H/N |
| 9 | `results/lipsync/*_sheet.png` + 对应视频 | 音节级 lip sync | K |

## 19. 限制与诚实声明

- **S25（agnes-video-2.5 非 flash）无样本**：付费墙（403 预扣费）——该模型的一切能力"未测"而非"不支持"
- F0 自相关在个别样本可能八度误判（F_05=111Hz 疑似）；散布结论由多组同文本对照支撑，不依赖单点
- whisper 对近静音输入有 stock 短语幻觉（CAP_p3 教训）；所有 STT 结论仅采信响度+频谱佐证的样本
- LTAS 频谱包络相似度对音色漂移不敏感（F 组 0.996 vs 基线 0.999）——音色证据以 F0 + 听评为准
- silencedetect 对安静母带低估语音区间——lip-sync 条带仅供看片索引，不作同步判定
- 人工听评未进行；所有 `[待人工]`/`(H)` 项在听评后可能升降级
- C_01 首次下载损坏已重生成；LN 首跑因 scratch 侧 bug 失败已修复重跑（正确性不受影响）

## 20. 证据索引

```text
_audio_probe/
  specs.py / run_tests.py / capability_probe.py / analyze.py / probe_common.py / gen_assets.py
  evidence/<SPEC_ID>/         106 条完整证据（视频/音频/ffprobe/loudness/频谱/STT/notes）
  evidence/CAP_p1..p13/       13 个能力探针目录（含 400 报错原文存档 results/capability/*.json）
  results/summary_table.json  106 行总表（格式/响度/特征/STT）
  results/acoustic_table.json / similarity.json / stt_summary.json
  results/montage/*.wav       25 组人工 AB 听评包
  results/lipsync/*_sheet.png 6 张语音-帧条带
  results/PROBE_NOTES.md      探针阶段固化笔记
```

**——A0 调查到此为止。按 14.md §23：不创建正式 Audio Architecture，等设计指令。**
