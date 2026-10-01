# Phase 44C 设计决策记录（C0，先于编码）

日期：2026-10-01。执行：zcode（C 批次）。前置核验：B 批次交付完好
（3 sequence 全 done 产物在盘、manifest v2 指纹校验通过、评审页 200、子模块
bd0cd85、全量回归 2568 绿）。

## 0. 开工审计结论（复用清单）

| 审计项 | 结论 | 证据 |
|---|---|---|
| MiniMax 适配器 | **已存在且真机冒烟过**：`src/audio/minimax_adapter.py`（15.md/A1，2026-09-14 冒烟 PASS）——`POST https://api.minimax.cn/v1/t2a_v2`（国内端点）、hex 音频解码、`base_resp.status_code` 检查、trace_id 留证、structured 错误、secret 脱敏、request snapshot 天然脱敏 | 模块头 + `_voice_docs/mm_T2A_HTTP.md` |
| 密钥 | `MINIMAX_API_KEY` 在 .env（本批不读取/不输出值） | 键名清单 |
| 模型/声线 | 系统默认 `speech-2.8-turbo`；系统音色清单在 `_voice_docs/mm_VOICES.md`（温润男声/播报男声/成熟女声等候选） | 文档 |
| 音频后处理 | `normalize_to_production_wav`（→ 48kHz/mono/pcm_s16le，与 S2 混音规格兼容）+ `ffprobe_audio` + 原始资产落盘 | `src/audio/media_ops.py` |
| 混音 canonical | S2 `SequenceAudioPlan`（layers + OCC revision + basis v2 + 方案 A 渲染）已闭合——本批只做**加法扩展**：ducking / loudnorm / base_gain | `src/sequence/audio_*` |
| 缺口 | ①旁白不是 Shot.dialogue（A1 域不可滥用）→ 需旁白产物仓；②ducking/loudnorm 不在 v1 混音器；③无音乐素材库；④cue 编排工具面 | 本文档 §1-§4 |

## 1. 分轨计划（C0）

| 轨 | 内容 | 处理 |
|---|---|---|
| `video_original` | 13 镜原生环境声（12/13 有 aac 流；s02_c03 甲-2 近静音为用户已定选择） | 审听后**保留并存**，整体 `base_gain_db=-3dB` 给旁白让出听觉空间（可调，进 plan/revision/basis） |
| `narration` | 5 个 cue（见 §2），MiniMax TTS → 48kHz mono WAV | ducking 触发源；独立分轨交付 |
| `music` | 一条器乐铺底贯穿全片（§3），淡入 3s/淡出 4s | 旁白期间 sidechaincompress 平滑压低，句尾恢复 |

无对白对嘴、无逐镜解说；**画面已讲清的动作不加旁白**（C0 克制原则）。

## 2. 旁白策略（cue 候选初稿，实测时长回来后重排）

叙事学依据：每句补【画面不可得】的信息（背景/情绪/主题），不描述正在发生的动作；
全部落在慢镜头/留白处，避开切点 ±200ms，不跨剧情状态切点。

| cue | 落点（timeline） | 文字稿 v1 | 为什么这里说 | 听觉空间 |
|---|---|---|---|---|
| nc01 | s01_c01 内 ~1.5s 起 | 暴雨过后的河面，总会留下一些东西。 | 定调+主题伏笔；大远景本就安静 | 镜头缓移，环境声最弱的段落 |
| nc02 | s01_c06 内 ~0.5s 起 | 山坡上的灯塔，很多年没人点亮过了。 | 补背景信息（灯塔久未点亮——画面不可得） | 收尾静观镜头，窗口 5.2s，文案限 ≤4.2s |
| nc03 | s02_c02 内 ~0.4s 起 | 灯芯亮起的那一刻，像有什么被重新修好了。 | 情绪句（修好的不只是灯） | 点灯后静帧感段落 |
| nc04 | s03_c02 内 ~5.5s 起 | 抱着灯往上走的人，其实也在往家走。 | 主题句（归航点题） | 盘旋楼梯中段，节奏最缓 |
| nc05 | s03_c04 内 ~1.8s 起 | 灯亮了，晚归的船，就认得回家的路。 | 收束（信号—回应闭环） | 结尾远景，留 2s+ 尾白给音乐淡出 |

声线：**温润男声 `Chinese (Mandarin)_Gentleman`**（主选，语速 0.9 沉稳叙事）；
A/B 备选 `female-chengshu`（成熟女声）、`Chinese (Mandarin)_Wise_Women`（阅历姐姐）
——同句试听页供用户选择；不克隆、不绑角色声线（C1.4 纪律）。

## 3. 音乐素材身份（C0）

- **没有现成音乐资产库/用户素材**（审计：仓库唯一先例是 S2 的 30s 测试 pad）。
- 按 C0 纪律**不擅自生成/购买/使用来路不明歌曲** → 采用**本地确定性合成的器乐
  环境垫**：A 小调三和弦垫（Am–F–C–G 循环，8s/和弦 ×12 段 = 96s ≥ 89.9s），
  正弦叠加+缓颤+低通+回声，片头淡入 3s/片尾淡出 4s，无循环接缝点。
- 素材身份字段：`origin=locally_synthesized_test_asset`、`license=self_generated_
  no_third_party_rights`、`params_snapshot`（全部合成参数入档，可复现）。
  **性质=test asset**：正式音乐仍待用户提供/授权素材，管线不变（替换 =
  imported_audio 换文件 + 新 plan revision）。

## 4. 工程结构决定

1. **旁白产物仓** `src/audio/narration.py` + `output/narration_audio.json`
   （A1 同构：cue_id → versions[]，幂等键=指纹(text+model+voice+speed/vol/pitch)，
   命中且文件在 → 零 API 调用复用）。产物 WAV 写入 `output/audio_imports/narration/`
   —— S2 `imported_audio` 的规范目录，混音引用零改动（S2 不管生成，§2/§3 分域）。
2. **S2 加法扩展**：plan 增 `base_gain_db`、`ducking`（trigger_roles/ducked_roles/
   threshold_db/ratio/attack_ms/release_ms，dB→线性换算在编译层）、`loudness`
   （I/TP/LRA，终混 loudnorm+aresample）；全部进 semantic_snapshot→revision→basis；
   mixer schema 升 `sequence_audio_mixer_v2`（默认值下图与 v1 逐字节等价）。
3. **分轨导出**：`render_audio_stems`（确定性工具）：同 plan 的 narration/music
   子图独立渲染 WAV（provenance=plan revision），供 A/B 与追溯，不产生新 canonical。
4. **Agent 工具面**（assembly 加法）：`narration_read`/`tts_synthesize`/
   `audio_plan_read`/`audio_plan_put`/`stem_render`（11→16，附理由）。
5. **渲染**：S2 方案 A 自动生效（base=既有 done 渲染，video stream copy，秒级混音）；
   静音基线=full-film silent profile 渲染（B 批机制直接复用）。

## 5. 目标响度

目标平台=**桌面/在线流媒体观看**：`loudnorm I=-16 LUFS, TP=-1.5 dBTP, LRA=11`
（参数存 plan，不硬编码"全平台通用"口径；测量用 ebur128 报告输入/输出响度与
真峰值，人耳验收不替代）。
