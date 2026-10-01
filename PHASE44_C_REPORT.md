# PHASE 44C 报告——MiniMax 旁白、连续音乐与混音

日期：2026-10-01。执行：zcode（C 批次）。依据：
`详细流程文件/新阶段1/PHASE44_C_旁白音乐与混音.md`。
输入：B 批时间线（manifest_v2 → seq_a3d5b19b，89.9s/13 镜）+ cue 时间契约。
前置核验：B 批产物/manifest/评审页/回归全部完好（见开工复核记录）。

状态标记：〔已核验〕＝命令/回执/哈希可复现；〔待用户〕＝等用户试听/裁定。

---

## §0 结论摘要

1. **声音三轨闭环建成并双路径验收**〔已核验〕：旁白（MiniMax TTS 真机）+ 贯穿
   配乐（本地合成器乐垫）+ ducking 混音，全部落在既有 S2 AudioPlan canonical 上。
   驱动器路径（cue nc01–05）与 **真实 Agent 会话**（cue na01–05，会话
   `sess_fca916781076`）各自完成「提案→合成→编排→渲染→分轨→manifest→提案」，
   两版混音成片 A/B 供用户试听。
2. **量化达标**〔已核验〕：最终混音 I=-16.7 LUFS（目标 -16，桌面/流媒体口径）、
   采样峰值 -1.1 dBFS（无削波）、A/V 时长差 0.0ms；重复渲染解码音频哈希一致
   （可复现）。
3. **发现并修复 S2 一个潜在设计缺陷**（§6.1）：混音渲染可被选为方案 A 的 base
   → 迭代混音整体叠加。该缺陷被本批 F4 可复现性测试首次触发，已修复+测试锁定。
4. **零视频生成费用**；TTS 为逐句短文本同步调用（6+5+3+2 次真实调用，全部
   trace_id/哈希/参数留证，幂等缓存命中零重复付费）。
5. **交付**〔待用户〕：评审页 http://127.0.0.1:8786/_44b/review/REVIEW_44b.html
   ——C 区块含：混音成片×2（A/B）、静音基线、分轨×2、声线 A/B×3、cue 表、
   Agent 会话证据。定版与 `final_render` select 由用户完成。

---

## §1 C0 声音策略与素材身份

- **分轨计划**：`video_original`（原生环境声保留，base_gain -3dB 让位）/
  `narration`（MiniMax TTS，5 句）/ `music`（一条器乐垫贯穿）。
- **克制原则执行**：13 镜只配 5 句旁白，全部落在慢镜头留白处；画面已讲清的动作
  不配旁白；跨组切点（39543/57902ms）±200ms 无起句收句；不跨剧情状态切点。
- **音乐素材身份**（无既有资产库，按纪律不擅自外采）：
  `origin=locally_synthesized_test_asset`，`license=self_generated_no_third_party_rights`，
  A 小调 Am–F–C–G 四和弦环境垫，8s/和弦×12 段=96.12s≥89.9s，正弦叠加+微失谐
  拍频+低通 1800+颤音+回声，参数全量入档 `_44c/music_params.json`（sha256
  c277d1fa…）。**性质=合法测试素材**：正式音乐待用户提供/授权，替换=换
  imported_audio 文件+新 plan revision，管线零改动。
- 全文见 `_44c/DESIGN_DECISIONS.md`。

## §2 C1 MiniMax 适配器（复用，非重建）

- **审计结论**：系统已有完整适配器 `src/audio/minimax_adapter.py`（A1 阶段真机
  冒烟过）：`POST https://api.minimax.cn/v1/t2a_v2`（国内端点）、模型
  `speech-2.8-turbo`、hex 音频解码、`base_resp.status_code` 检查、trace_id、
  structured 错误、secret 脱敏（key 只进 Authorization header，request snapshot
  构造性无密钥）。密钥=`MINIMAX_API_KEY`（.env，本批不读取/不输出值）。
- **最小闭环真机验证**〔已核验〕：nc01 句真机合成 → 3.692s WAV（48kHz/mono/
  pcm_s16le），trace_id `070c906a…`，latency 2658ms；第二次同参调用**缓存命中
  零 API**。
- **旁白产物仓**（新增 `src/audio/narration.py` + `output/narration_audio.json`）：
  cue_id→versions[]（A1 同构）；幂等键=指纹(text+model+voice+speed/vol/pitch)；
  产物 WAV 落 `output/audio_imports/narration/`（S2 imported_audio 规范目录，
  引用即溯源）；raw mp3 双留证（`.raw/`）；断线恢复=查仓+文件哈希，不重复付费。
- **错误契约补充**：实测当前端点对不存在 voice 返回 **2054**（文档口径 2049）——
  已补映射至 `provider_invalid_voice`，raw status_code 保留。

## §3 C2 旁白落点（实测时长重排）

执行侧候选（nc01–05，声线=温润男声 `Chinese (Mandarin)_Gentleman`，speed 0.9，
逐句响度实测 ≈-25.4 LUFS）：

| cue | 镜 | 时间线 ms | 实测 | 文字稿 |
|---|---|---|---|---|
| nc01 | s01_c01 | 1500–4902 | 3402ms | 暴雨过后的河面，总会留下一些东西。 |
| nc02 | s01_c06 | 34859–38214 | 3355ms | 山坡上的灯塔，很多年没人点亮过了。 |
| nc03 | s02_c02 | 47943–52297 | 4354ms | 灯芯亮起的那一刻，像有什么被重新修好了。 |
| nc04 | s03_c02 | 70722–73752 | 3030ms | 抱着灯往上走的人，其实也在往家走。 |
| nc05 | s03_c04 | 83702–87487 | 3785ms | 灯亮了，晚归的船，就认得回家的路。 |

落点纪律实测生效：nc04 初排 offset 5500 超窗（end 74432 > 镜尾-150ms）→ 按 C2
规则移到邻近空隙（70722），**同句缓存命中零重复付费**。逐句依据与听觉空间见
`_44c/cue_table.json`。声线 A/B 三样张（温润男声/成熟女声/阅历姐姐）在评审页试听。

## §4 C3 混音与量化验证

- **S2 加法扩展**（mixer schema v2，默认配置下图与 v1 逐字节等价）：
  `base_gain_db`（环境声压低）、`ducking`（sidechaincompress：旁白层混合为键信号
  → 音乐层压低；threshold_db→线性换算在编译层）、`loudness`（终混 loudnorm+
  aresample）；全部进 plan revision→basis（改参数=诚实 STALE）。
- **混音配比**（响度实测定值）：旁白 +2dB（源 ≈-25.4 LUFS）、音乐 -10dB
  （源 -19.2 LUFS→垫底 ≈ 旁白下 4 LU）、环境声 -3dB、音乐淡入 3s/淡出 4s、
  ducking（threshold -30dB/ratio 4/attack 50ms/release 800ms）、终混
  I=-16/TP=-2/LRA=11（TP 留 0.5dB 余量，见 §6.4）。
- **执行侧混音版** `srender_ce68b7f0`（base=纯 S1 渲染 aca99c81，video stream
  copy 12.1s）：**I=-16.7 LUFS / 峰值 -1.1 dBFS / A/V 时长差 0.0ms**。
- **分轨**：`stem_narration_rev2543e107.wav` / `stem_music_rev2543e107.wav`
  （文件名含 plan revision 可追溯）；**静音画面基线** `srender_9e9ba8ee`
  （seq_900fdf75 audio=silent，26.3s 渲染）。
- 完整文案+时间码：manifest_v3 narration_cues + 评审页 cue 表。

## §5 C 验收——真实 Agent 会话〔已核验〕

会话 `sess_fca916781076`（T1 130.1s + T2 105.7s，真实 Agnes LLM + 真实 MiniMax
TTS），证据 `_44c/events_cagent_{1,2}.jsonl`、`result_cagent_{1,2}.json`、
`session_after_cagent.json`。

- **T1**：narration_read（读故事/时间线/执行侧候选）→ 自主提出 **na01–05**（文字
  稿与执行侧不同：如 na02「修理间的灯亮了，铜灯里的火苗也醒了。」）→ 逐句
  tts_synthesize（前 2 次调用因 LLM 传 `speed:null` 触发本批已修的缺陷（§6.3），
  结构化报错后自行纠正）→ audio_plan_read→audio_plan_put（OCC，6 层）→
  render_submit。
- **T2**：render_status（deep audit：PTS 单调✅、2 处已知帧槽空洞如实上报）→
  stem_render×2 → manifest_publish（**manifest_v3**，narration_cues=Agent cue 表）→
  proposal_write（A/B 试听说明+素材声明）。
- Agent 版混音 `srender_3da0e76d`（base=纯 S1 渲染，**freshness=current**）。
- 会话发现并修复的缺陷见 §6（Agent 报错即缺陷探测器）。

## §6 本批发现并修复的缺陷（全部带回归测试）

1. **S2 方案 A 叠加缺陷**（严重，C 批首次触发）：`_eligible_mix_base` 允许混音
   渲染（v2 basis）作 base——mix 图对 base 的 `[0:a]` 语义=native 音轨，用已混音
   渲染做 base 会把上一版 layers 整体叠加（实测：混音链 3 次叠加、F4 重复渲染
   哈希不一致暴露）。修复=base 必须纯 S1（v1）basis；仅存混音 base 时走降级重建
   路径。S2 既有测试曾把该行为锁死（`test_second_render_uses_base_video_copy`），
   已按正确语义改写+新增纯 base 复用测试。**受累渲染**：ff7bd34b/62fb082b/
   0bf5c256（叠加链，保留为 evidence）；修复后全部新渲染干净。
2. **plan 指纹 int/float 漂移**：put_audio_plan 直赋 `base_gain_db`（LLM 常传
   int）绕过 `__post_init__` float 归一 → 内存指纹 `-3` ≠ 重载指纹 `-3.0`
   （Agent put 回执 verified=False 实证）。修复=赋值经 `_check_gain`。
3. **TTS None 参数崩溃**：LLM 显式传 `speed:null` → `float(None)` TypeError。
   修复=narration.py 入口 None 归一到默认值。
4. **AAC 解码过冲**：混音输出 AAC 默认码率（~128k）解码峰值较 loudnorm 输出
   （TP=-1.5）过冲 ~2dB → 触顶 0 dBFS。修复=mix 输出 `-b:a 192k` + TP 目标
   -2.0（留解码余量）→ 实测解码峰值 -1.1 dBFS。
5. **stem 图文件索引**：stem 无 base，ffmpeg 文件索引应从 0 起（沿用 mix 的
   1..N 会 "Invalid file index"）。
6. **提案编号撞号**：proposal 版本号按文件数计算 → 历史文件缺失时覆盖旧提案
   （C 提案覆盖了 B 提案 v4；B 版已有归档无损失）。修复=取最大 N+1。
7. **MiniMax 2054 错误码**补映射（§2）。

## §7 C4 故障与回归

| 案例 | 结果 |
|---|---|
| 坏 voice_id（真实调用） | provider 2054 "voice id not exist" → `provider_invalid_voice`，structured detail（status_code/msg/trace_id），零产物落盘 |
| 音乐时长超源 | render Phase 1 fail closed：`[audio_layer_invalid] duration(999999) > 源时长(96120)ms`，render 行 failed、无输出；plan 还原后 revision 逐字节一致 |
| cue 文案变更 | 新版本 nver_2e88885f（真实 TTS），旧 cue 版本/旧渲染零污染 |
| 同 plan 重复渲染 ×2 | 解码音频哈希一致（**reproducible=True**），旧 done 输出零覆盖 |
| 空 TTS 数据/配额 1008/2049/超时/坏 hex/非 JSON | adapter MockTransport 全覆盖（既有 test_voice_a1_adapter 12 项）+ 44C 新增 narration 层传播测试（仓零写入） |
| URL 时效 | 本链路 hex 直解码+本地双留证（raw+wav），产物仓无 URL 依赖 |
| 中途渲染失败 | B4.4 崩溃恢复 + S2 failed 行语义（既有）覆盖 |

确定性测试：`tests/test_44c_audio_narration.py` **19 项**全绿（mixer v2 图形/
分轨/plan 指纹/旁白仓/幂等/故障传播）；S2 既有 60 项全绿；**全量回归
2586 passed / 0 failed**（16:22，`_44c/_full_regression.log`）。

## §8 交付物清单

**新增/修改**：`src/audio/narration.py`（旁白仓+TTS 胶水）、`src/sequence/
audio_models.py`（ducking/loudness/base_gain 模型）、`audio_mix.py`（mixer v2+
stem 编译）、`audio_basis.py`/`service.py`（basis/put/方案 A 修复）、
`minimax_adapter.py`（2054）、`src/agents/assembly.py`（+5 声音工具，11→16）、
`_44c1_assets.py`/`_44c2_cues.py`/`_44c3_mix.py`/`_44c4_faults.py`/
`_44c5_agent_run.py`、评审页 C 区块。变更统计见提交。

**可播放成片**（评审页）：
| 版本 | render | 内容 |
|---|---|---|
| 混音版 A（执行侧 cue） | srender_ce68b7f0 | 环境声-3dB+音乐-10dB+旁白+2dB，ducking，loudnorm -16 |
| 混音版 B（Agent cue） | srender_3da0e76d | Agent na01–05，同一时间线 |
| 静音画面基线 | srender_9e9ba8ee | audio=silent |
| 小样本 native/silent | （B 批） | 双音轨策略样片 |

**分轨**：narration/music stem（rev2543e107 与 agent rev 对应版）；**文案+时间码**：
manifest_v3 narration_cues + `_44c/cue_table.json`。

## §9 已知限制（不隐瞒）

1. **音乐=本地合成测试垫**：可用但朴素；正式音乐待用户提供/授权（替换管线
   已备：换 imported_audio 文件+新 plan revision+重渲染）。
2. **旁白声线未定版**：主选温润男声，A/B 三样张待用户试听后定；换声线=逐句
   重合成（幂等缓存按参数隔离，旧版本保留）。
3. **loudnorm 单遍**（动态模式）：响度达标（-16.7 vs -16），复杂素材迭代建议
   未来加两遍测量模式（plan 字段已备扩展位）。
4. **2 处切点 83ms 帧槽空洞**（B 批已知项，Agent deep audit 独立复现同一结论）。
5. **旁白 ducking 是电平压低**（sidechaincompress），非频谱避让；正式混音如需
   EQ 避让属后续批次。
6. 混音渲染历史中 3 个叠加版本（ff7bd34b/62fb082b/0bf5c256）保留为缺陷证据，
   不可作为成片候选（音频为多层叠加）；评审页只列干净版本。
7. Agent T1 有 2 次 TTS 调用失败（speed=null，§6.3，已修）与 2 次空参提案写入
   （串行闸门+参数校验拦截）——如实留痕。

## §10 用户待办

1. **试听定版**：评审页 A/B 两版混音（执行侧 cue vs Agent cue）+ 声线 A/B +
   分轨；选定后对相应 render 显式 `select final_render`。
2. **正式音乐**：提供授权素材后一句命令替换重渲染。
3. **旁白微调**：任何一句改文案/换声线 → `tts_synthesize`（新版本）→ plan put
   （OCC）→ 重渲染该版；旧版可回看。

——以上，Phase 44C 完毕（成片定版与 select 待用户试听后闭合）。
