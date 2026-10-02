# PHASE 44C 报告——MiniMax 旁白、连续音乐与混音

日期：2026-10-01。执行：zcode（C 批次）。依据：
`详细流程文件/新阶段1/PHASE44_C_旁白音乐与混音.md`。
输入：B 批时间线（manifest_v2 → seq_a3d5b19b，89.9s/13 镜）+ cue 时间契约。
前置核验：B 批产物/manifest/评审页/回归全部完好（见开工复核记录）。

状态标记：〔已核验〕＝命令/回执/哈希可复现；〔待用户〕＝等用户试听/裁定。

---

## §0 结论摘要

> **当前终版（2026-10-01 晚，用户试听反馈"还不错"）**：
> `srender_fa9a2b7a` —— 原音静默 + 三段分场景配乐 + 全片 13 句旁白（62.9s 语音 /
> 89.9s 片长），I=-16.0 LUFS 正中目标，验收全过（详见 §9.4；试听页
> http://127.0.0.1:8786/_44c/review/LISTEN_44c.html）。原生音轨分离路线已实测
> 不可行、正式关闭（§9.5）。以下 §1–§8 为该终版之下逐轮迭代的完整过程记录
> （§9.1–§9.3 为历次用户反馈与根因修复，保留不删）。

1. **声音三轨闭环建成并双路径验收**〔已核验〕：旁白（MiniMax TTS 真机）+ 贯穿
   配乐（本地合成器乐垫）+ ducking 混音，全部落在既有 S2 AudioPlan canonical 上。
   驱动器路径（cue nc01–05）与 **真实 Agent 会话**（cue na01–05，会话
   `sess_fca916781076`）各自完成「提案→合成→编排→渲染→分轨→manifest→提案」，
   两版混音成片 A/B 供用户试听。
2. **量化达标**〔已核验〕：终版混音 I=-16.0 LUFS（目标 -16，桌面/流媒体口径）、
   真峰值 -1.8 dBTP（无削波）、A/V 时长差 0.0ms；重复渲染解码音频哈希一致
   （可复现）。
3. **发现并修复 S2 一个潜在设计缺陷**（§6.1）：混音渲染可被选为方案 A 的 base
   → 迭代混音整体叠加。该缺陷被本批 F4 可复现性测试首次触发，已修复+测试锁定。
4. **零视频生成费用**；TTS 为逐句短文本同步调用（全部 trace_id/哈希/参数留证，
   幂等缓存命中零重复付费）。
5. **交付**：终版试听页 LISTEN_44c.html（13 句逐句 + 三段音乐 + 分轨 + 成片 +
   旧版 A/B）；历史评审页 http://127.0.0.1:8786/_44b/review/REVIEW_44b.html
   C 区块保留。定版 `select final_render` 待用户明确指令。

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

**新增/修改（代码/管线）**：`src/audio/narration.py`（旁白仓+TTS 胶水）、
`src/sequence/audio_models.py`（ducking/loudness/base_gain 模型）、`audio_mix.py`
（mixer v2+stem 编译）、`audio_basis.py`/`service.py`（basis/put/方案 A 修复/
两遍渲染）、`minimax_adapter.py`（2054）、`src/agents/assembly.py`（+5 声音工具，
11→16）。批次驱动脚本：`_44c1_assets.py`–`_44c5_agent_run.py`（首轮）、
`_44c7_music_v2.py`/`_44c8_narration_full.py`/`_44c9_music_scenes.py`/
`_44c10_mix_full.py`（终版轮）。变更统计见提交。

**可播放成片**（按演进顺序，全部保留可回看）：
| 版本 | render | 内容 |
|---|---|---|
| **终版（当前推荐）** | **srender_fa9a2b7a** | **原音静默 + 三段分场景配乐 + 全片 13 句旁白，I=-16.0 LUFS** |
| 纯 BGM+旁白（5 句旧版） | srender_c081beae | 单段音乐贯穿 · 原生静默（两遍渲染修复后首个干净版） |
| 混音版（BGM v2 重调） | srender_a95fbb7a | 环境声 -6dB + 音乐 -13.5dB + 旁白 5 句 |
| 混音版 A（执行侧 cue） | srender_ce68b7f0 | 环境声-3dB+音乐-10dB+旁白+2dB（首轮配比） |
| 混音版 B（Agent cue） | srender_3da0e76d | Agent 会话自主 na01–05 |
| 原生音轨原片 | srender_aca99c81 | 13 镜原生音轨零处理（B 批产物，终版视频 base） |
| 静音画面基线 | srender_9e9ba8ee | audio=silent |

**分轨**：终版 `stem_narration_reve4c7b33f.wav` / `stem_music_reve4c7b33f.wav`
（plan rev e4c7b33f）；历史 rev2543e107 / rev0ff0b92b 对应各版。

**证据链**：`_44c/mix_full_evidence.json`（终版验收）、`narration_full_table.json`
（13 句逐句表）、`music_scenes_params.json`（三段音乐参数+哈希）、
`native_audio_scan.json`（逐镜原生响度扫描）、`cue_table.json`/`mix_evidence.json`
（历史轮）。文案+时间码：manifest_v3 narration_cues + 试听页逐句表。

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
8. **原生音轨不可分离**（§9.5 实测结案）：生成模型单次烧录的混合音（配乐+
   环境声+音效）对盲分离模型属分布外输入，Demucs/HTDemucs 实测环境声与
   配乐同生共死（保留轨三场景能量一律 -9~-14dB）。想用原片真实环境声只能
   走人工修补/真实素材库/生成线 prompt 修复，不走分离。

## §9.1 增补（2026-10-01 用户试听反馈轮）

用户试听评审页后反馈三项，处置如下〔已核验〕：

1. **"旁白分轨只有一句"** → 实测分轨 5 句俱全（silencedetect 定位 1.7/34.9/48.2/
   70.9/83.9s）；真因=评审 HTTP 服务不支持 Range 请求，浏览器进度条拖动失效。
   **已修复**（206 Partial Content，实测返回 206），全站媒体可拖动。
2. **"music 分轨没有音乐/成片仍是缝合感"** → v1 正弦垫电平过低（stem 峰值
   -20.2dBFS）被环境声掩盖。处置：
   a. MiniMax Music API 真机探测 → **HTTP 410 / status 2153**（"no longer
      available to new users"，与官方 2026-08 停开公告一致）——该账户无法真实
      生成音乐（证据 `_44c/music_gen_probe.json`）；
   b. 替代=**程序化作曲 BGM v2**（`_44c7_music_v2.py`，numpy 合成）：A 小调
      60BPM 96s，弦乐垫+钢琴主旋律（5 乐句 50 音符）+低音声部，原始
      -13.6 LUFS；素材身份 `locally_composed_synthesized_test_asset`；
   c. 配比重调：音乐 -13.5dB（混入 ≈-27 LUFS，旁白下 ~4LU）、环境声 -6dB
      （原 -3）。新版混音 `srender_a95fbb7a`：**I=-16.7 LUFS / 峰值 -1.5 dBFS /
      A/V 0ms**；新分轨 rev0ff0b92b（music stem 峰值 -16.4dB，清晰可闻）。
3. **旁白男声确认**：温润男声（Chinese (Mandarin)_Gentleman）定为主声线。
4. 提案编号撞号修复（取最大 N+1，不再覆盖历史提案）。

## §9.2 增补（2026-10-01 用户第二次反馈）

用户确认混音构成（BGM+旁白）与男声选择；指出**部分源视频自带背景音属视频生成
线缺陷**，混音侧只负责自己的部分。处置〔已核验〕：

1. **逐镜原生音轨电平扫描**（`_44c/native_audio_scan.json`，交视频线的缺陷清单）：
   最响 s03_c03（mean -27.5 / max -1.9dB，近满量程）、s03_c04、s02_c03、s01_c02、
   s01_c01（mean ≈-28~-31dB）。
2. **"纯 BGM+旁白版"渲染** `srender_d5a9ca9f`（plan rev 2509868f：base_gain=-60
   原生音轨静默，音乐/旁白/ducking/loudness 不变）：**I=-16.7 LUFS / 峰值
   -1.1 dBFS**——缺陷音频不进成片。评审页新增该版本；"含环境声版"
   （srender_a95fbb7a，base_gain=-6）保留可对比。
3. 镜头本身的重生成由视频线按缺陷清单处置；本批不动任何 Shot。

## §9.3 增补（2026-10-01 测试师接管轮：成片"听不到旁白"二次根因）

用户试听 `srender_f746f196`/`srender_9978e82a` 仍听不到旁白。接管复验（逐句
旁白 stem 与成片对应时间窗的 Pearson 相关，比频谱比值更硬的判据）发现：

1. **§9.2 后的 asplit 修复（ducking 图标签复用）真实生效**，但只救回了 nc01
   （corr +0.990）；**nc02–nc05 全部缺失**（corr ≈ 0）。前一轮验证只查了
   nc01 时间窗，恰好是旧缺陷下唯一幸存句，造成"已修复"假象。
2. **真实根因（新进程复现排除"旧代码常驻进程"假说）**：单遍「混音图 → AAC」
   丢延迟层。`amix`+`adelay` 与定长帧编码器（ffmpeg 原生 aac）组合下，多个
   延迟输入层仅第一个进成片。同一滤镜图：PCM/MP3/FLAC 输出逐句相关
   0.91–0.95 全在场、PCM 两次运行结果一致；AAC 输出 nc02 在全片任何位置
   都不存在（滞后扫描最佳相关仅 0.106）。触发条件随延迟值漂移
   （34.9s 通过 / 1.5s 反而丢），`apad` 替代、`asetnsamples=1024` 均不可靠
   ——属 ffmpeg 编码路径深层缺陷，图内无稳妥规避。
3. **修复：两遍渲染**〔已核验〕。`_mix_to` 改为：第一遍 filter_complex →
   中间 PCM WAV（`final_label` 供 map；空文件 fail closed）；第二遍视频流
   copy + WAV→AAC（无 filter_complex）。`compile_audio_filter_graph` 不再
   返回 AAC `out_args`（杜绝"规格声明≠实际输出"第二真相）。回归测试锁定
   两遍命令形状（tests/test_sequence_audio_service.py）。
4. **重渲 `srender_c081beae`**（plan rev be64b193 不变）：五句旁白逐句相关
   0.912–0.990 全在场；音乐窗高频比 0.0005（环境声 0.0371，无泄漏）；
   I=-18.1 LUFS / 峰值 -1.9 dBFS。缺陷版 `srender_9978e82a` 作废留档。
   试听页 LISTEN_44c.html / 主评审页已指向新成片。
5. 环境备注：系统 PATH 中 ffmpeg 路径用户名乱码（`锟斤拷…`）导致子进程
   找不到 ffmpeg——本轮所有命令以前置真实路径（`C:\Users\曹文淇\Downloads\
   ffmpeg-2026-01-07-git-af6a1dd0b2-full_build\bin`）运行。建议后续修 PATH。

## §9.4 增补（2026-10-01 18:00 用户指令轮：全片旁白 + 分场景音乐终版）

用户指令：①不用音频分离，原视频静默后加旁白和背景音；②分镜接好后每个场景
配不同背景音；③不脚本生成音效；④旁白做成全片 ~90s，不要只有几句。处置〔已核验〕：

1. **全片旁白 13 句**（每镜一句，`_44c8_narration_full.py` → `narration_full_table.json`）：
   265 字 → 62.9s 语音铺满 89.9s；落点规则 start≥镜起+300ms / end≤镜尾-150ms，
   **13/13 落窗**。nd02（8.06s>8s 窗）、nd09（差 142ms 触线）按 C2 纪律改短文案
   重合成（仅 2 次真实 TTS 调用，其余 11 句幂等缓存命中零付费）；超窗长版本
   nver_c542a921 / nver_6ef97d96 留档可回看。
2. **分场景音乐三段**（`_44c9_music_scenes.py` → `music_scenes_params.json`）：
   同已批准 v2 合成器风格（钢琴 6 谐波+弦垫+低音，60BPM），三段各有性格——
   s1 河岸 Am-Em-F-G-Am-F（43s，忧郁稀疏）/ s2 修理间 F-C-Dm-C（21s，高八度
   温暖）/ s3 登塔 Am-F-C-G-F-C（38s，攀升至 E5 收束）；实测 -13.4/-13.7/-14.1
   LUFS，sha256 入档；**零音效**（用户指令）。
3. **音乐落位**：s1@0–39543 / s2@39543–57902 / s3@57902–89902（source_in=6000：
   从 F 和弦干净进入，E5 长音正落在片尾灯塔光束镜头）；场景切点处短淡入淡出
   接力，无拍子突变。三段共享 ducking 键信号的滤镜图（1 键喂 3 路
   sidechaincompress）先以 lavfi 探针验证 ffmpeg 接受，再进正式渲染。
4. **终版混音** `srender_fa9a2b7a`（plan rev e4c7b33f，16 层 = music×3 +
   narration×13；base_gain=-60 原生静默；旁白 +2dB / 音乐 -13.5dB / ducking
   -30dB·4:1·50ms·800ms / loudnorm I=-16·TP=-2·LRA=11；base=aca99c81
   video copy，22.2s）。
5. **验收全过**（`_44c10_mix_full.py` → `mix_full_evidence.json`）：13 句旁白
   逐句相关 0.859–0.978 全在场；三段音乐间隙窗相关 0.998–1.000；原生泄漏
   相关 ≈0.005（静默确认）；**I=-16.0 LUFS 正中目标** / 真峰值 -1.8 dBTP /
   LRA 4.0 LU / A/V 时长差 0ms。分轨 stem_{narration,music}_reve4c7b33f.wav。
6. 试听页 LISTEN_44c.html 已重写为终版（13 句逐句 + 三段音乐 + 分轨 + 成片 +
   旧版 A/B）；上一版 render/plan 全部保留可回看（OCC，零覆盖）。
7. **用户试听反馈（2026-10-01 晚）**："还不错"——暂定认可终版，尚未下达
   `select final_render` 定版指令；同轮确认本终版为**脚本驱动的工具验证路径**
   （创意决策由批次执行 AI 完成），Agent 自主路径已另在 5 句规模验收（§5）。
8. **原片交接（音频分离实验）**：原生音轨原片 `srender_aca99c81`（13 镜零处理
   混合音）+ 逐镜响度对照表 `_44c/native_audio_scan.json` 交付用户，供其它
   agent 测试"去配乐、留环境声/音效"的分离可行性。已知难点：AI 生成音频对
   主流分离模型（人声/伴奏训练）属分布外输入，雨声等宽频噪声易被误切。
   若分离出可用音轨，可经 imported_audio 接回混音管线出 A/B 对比版。

## §9.5 结案（2026-10-01 晚：原生音轨分离实验——目前方式不可行）

§9.4-8 将原生音轨原片交由专门 agent 做盲分离测试（任务书
`_44c/separation_task_brief.md`）。实验完成，**结论：目前方式不行，该路线
正式关闭**。过程与证据：

1. **专门 agent 的做法**（`Downloads\output_rainlamp_separation\README_分离报告.md`）：
   Demucs v4.1.0 / HTDemucs 盲分离（CPU，7s 分段）→ 取 `other` 茎 →
   谐波/瞬态频谱掩蔽保守压音乐；保留轨=环境声/音效候选，去除轨=原声−保留轨。
2. **其自评**：「需人工修补（中等可用），不建议直接作为最终混音的唯一环境声
   来源」——无独立分轨；雨声与铺底配乐同为宽频持续成分，模型无法稳定分开；
   保留轨有音乐残留且水声/风声片段被一并削弱。
3. **本线一手核验**〔已核验〕（`_tmp/verify_separation.py`，非转述）：时长
   全对齐（保留/去除轨 89.902s，两 mp4 89.922s）；响度 原生 -28.4 / 保留
   -37.4 / 去除 -31.0 LUFS（与其报告一致）。**关键证据——保留轨三场景能量
   一律下降 9.0~14.1 dB**（s01 河岸雨声最重 -10.4 / s02 修理间 -14.1 /
   s03 登塔 -9.0）：若分离成功，保留轨在环境声主导的 s01 应只低约 3~6 dB
   （仅扣除配乐）；实测普遍大幅衰减，说明**环境声与配乐被一并削掉**——正是
   任务书 §4.2 预判的"宽频噪声误切/同生共死"失效模式。去除轨仅比原生低
   2.6 LU，绝大部分节目能量（配乐+大部分环境声）都进了去除轨。
4. **判定**：盲分离（去配乐、留环境声）对本素材**不可作为管线步骤**。该
   agent 报告数字与实测完全吻合，评级诚实；失败源于素材本身（生成模型单次
   烧录混合音、无结构先验），不是执行问题。
5. **处置**：分离版/剪辑版 mp4 与保留/去除轨 WAV 留存于
   `c:\Users\曹文淇\Downloads\output_rainlamp_separation\`（不入库、不接管线）；
   用户试听后结论"做不到"。**现行方案维持** `srender_fa9a2b7a`（原生静默 +
   三段场景配乐 + 全片旁白）。
6. **若仍要真实环境声，替代路径**（未排期，供后续批次选）：a) 用保留轨中的
   雨声/脚步/机械瞬态段做逐镜人工修补（劳动密集，质量取决于人）；b) 授权
   真实环境声素材库按场景铺底——S2 已有 ambience 层角色，管线即插即用；
   c) 推动视频生成线修 prompt 使原生音轨只含环境声（本轮开头已有此设想）。

## §10 用户待办

1. **试听终版** `srender_fa9a2b7a`（试听页 LISTEN_44c.html ④，含逐句/分轨/旧版
   A/B）：确认全片旁白密度、三段场景音乐衔接、旁白与音乐配比是否达到预期；
   满意后对该 render 显式 `select final_render` 定版。
2. **微调入口**：改任意一句文案/换声线 → 只重合成该句（新版本）→ plan put
   （OCC）→ 重渲染；换某段音乐 → 换 imported_audio 文件 + 新 plan revision，
   其它段不变；旧版全部可回看。
3. **正式音乐**（可选）：提供授权素材后按场景替换重渲染，管线零改动。

——以上，Phase 44C 完毕（成片定版与 select 待用户试听后闭合）。
