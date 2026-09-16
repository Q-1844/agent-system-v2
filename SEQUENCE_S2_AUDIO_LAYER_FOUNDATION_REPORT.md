# SEQUENCE_S2_AUDIO_LAYER_FOUNDATION_REPORT

Sequence S2 — Audio Layer Foundation 执行报告（25.md）。

日期：2026-09-16
状态：**FINAL PASS**（2026-09-16 人工听评通过，见 §11；§50 STOP 已遵守——AI Music Generation / automatic ducking / LUFS / Mastering / Film-level calibration / Sequence Agent / Editor Agent 一概未动）
分支：`feat/scene-system-v1-s1`（与 S1 及之前各阶段连续提交）

---

## 1. 结论摘要

- Sequence 声音侧 canonical **SequenceAudioPlan** 建成（独立 `sequence_audio_plans.json`，独立 revision domain）；混音走 **AudioMixPlan 纯派生 → ffmpeg filter graph 现场编译**（canonical 零 ffmpeg syntax）。
- 产出仍是 **SequenceRenderVersion**（§24 ✓）：`renders[]` / `final_render` 机制原样；无 AudioPlan 时渲染 = S1 语义逐字节不变（§25，测试锁死）。
- 真机 E2E ALL PASS：A（S1 baseline）/ B（+Music，fade 2s/3s）/ B2（music −22dB，§42 A/B 对）/ C（+Music+Narration[A1 production WAV artifact]）；§44 只改 gain → STALE（0 ffmpeg/provider）→ 恢复 → CURRENT。
- 全量回归 **2029 passed / 0 failed**（12:03；上基线 1995 → +34 S2 测试，零回归）。
- 冻结域零接触（§48）：本阶段改动 = `src/sequence/*` 新文件/扩展 + `routers/sequences.py` 增端点 + `src/sequence/models.py` 两个可选字段；`routers/shots.py`、`src/generation/*`、Visual V1、compiler、Agnes adapter、A1（`src/audio/*`）**全部零 diff**。

---

## 2. §5 开工审计结论（五问）

| 问题 | 答案（证据） |
|---|---|
| 1. 通用 audio resource/artifact？ | **无**。resources（V1 冻结）与 shared_library 均无 audio kind；唯一音频 artifact 系统 = A1 `DialogueAudioVersion`（dialogue_audio.json：per-dialogue versions + `selected_audio_version_id` 指针 + production_wav{path, audio_spec, ffprobe}） |
| 2. A1 production WAV 下游引用 | `doc.dialogues[dialogue_id].audio_versions[]` 显式 audio_version_id → `production_wav.path`（repo 根相对/绝对双形态，resolver 双兼容） |
| 3. 音频 sha256/duration/spec 元数据？ | duration/采样率有（ffprobe 落盘）；**sha256 无** → S2 render Phase 1 现场计算（同 S1 视频源纪律，§28） |
| 4. SequenceRenderVersion basis | S1 `sequence_render_basis_v1` 在档；S2 按 §26 扩 `sequence_render_basis_v2`（v1 全键保留 + audio 段） |
| 5. S1 renderer 最干净扩展点 | §23 方案 A：execute 阶段 video stream copy + 纯音频混音；mixer 独立 `src/sequence/audio_mix.py`（§31：filter graph 不散落 router/service） |

---

## 3. §23 实现选择：方案 A（video stream copy）+ 诚实降级

**选择方案 A**，理由对应 §23 三原则：
- **最少重复转码**：混音命令 `-c:v copy`——调 gain/fade 的迭代渲染只做音频重编码（秒级），视频比特零重编码。E2E 实证：B 的 base = 一次既有 done 渲染，B2 的 base = B，C 的 base = B2（`base_render_version_id` 链条在档）。
- **最清晰 provenance**：`base_render_version_id` 记录视频来源；basis v2 的 audio 段记录 plan revision + 逐层语义 + 源 sha256。base id 是 provenance **非 freshness 依赖**（base 换了但 S1 键相同 → 视频比特一致 → 仍 CURRENT）。
- **最少 truth duplication**：base 渲染仍是 S1 truth，混音渲染只是"更完整的一次 Sequence Render"（§24），不建任何新版本对象。

**降级路径**：有 AudioPlan 但无合格 base（无 done 渲染 / S1 子集已 STALE）→ 同一次显式 render 内先重建基线（临时 concat）再混——视频重编码一次，诚实行为、报告在案。这避免了"改了 clips 后必须先手动渲染基线再渲染混音"的死锁，同时不违反 No Auto Render（渲染始终由显式 POST 触发）。

**base 合格判据**：done + 输出文件在 + basis 的 **S1 子集**（v1 键）与当前重算一致（v2 basis 含全部 v1 键，同样可作 base；audio 段与视频判定无关）。

---

## 4. 实现清单

```
src/sequence/
  audio_models.py   SequenceAudioSourceRef（两 kind：a1_dialogue_audio[显式双键，§10
                    绝不默认 selected]/ imported_audio[audio_imports/ 规范相对路径，
                    拒绝绝对/..]）+ SequenceAudioLayer（role 三值 §8；Sequence 时间坐标
                    §11；gain [-60,24] §16；fade 和≤duration §17；layer_id 不进
                    revision=同语义同混音）+ SequenceAudioPlan（独立 revision §29）
  audio_store.py    独立 output/sequence_audio_plans.json（S1 store 零改动；
                    空 layers 归一化不落盘 = 无双真相；同 lock 族）
  audio_basis.py    resolve_audio_source（§10 显式引用 fail-closed；§33 无音频流
                    FAIL CLOSED 不补 silence；sha256 现场算 §28）+
                    build_render_basis_v2 / compute_current_render_basis（§26）+
                    full_basis_mismatched_keys（freshness §27/§28/§29 与 race §30
                    共用；audio_plan_untracked / audio_plan_removed 边界）
  audio_mix.py      AudioMixPlan 纯派生（§32 ephemeral/deterministic/零 I/O）+
                    compile_audio_filter_graph（atrim→volume dB→afade 线性→
                    adelay all=1→amix normalize=0[ffmpeg 实测支持，不衰减]；
                    duration=first 以 base native 音轨为准 §18；-c:v copy）+
                    measure_peak_db/peak_warning（§21 clipping advisory）
  service.py        PreparedRender（Phase1 快照含 audio 解析 + §12/§14 越界
                    fail-closed）；execute 方案 A/降级/S1 三路；commit 全量 basis
                    比较 + 峰值 advisory；get/put_audio_plan（OCC §38）；delete
                    连带清 plan
  models.py         SequenceRenderVersion + audio_peak_warning/base_render_version_id
                    （可选字段，known-field 过滤旧行兼容）
routers/sequences.py  GET/PUT audio-plan（§37 一个完整 PUT + OCC）+ handler 适配
```

API（§37 最小集）：`GET/PUT /api/project/{p}/sequences/{sid}/audio-plan`（PUT 带 `expected_revision` → 409；`layers: []` = 清空恢复 S1 语义）。render/select/freshness 端点复用 S1。

---

## 5. 关键设计决策

1. **§9 source 引用**：审计确认无通用音频资源系统 → 允许的薄 `SequenceAudioSourceRef` 两 kind：`a1_dialogue_audio`（只读 A1 store，dialogue_id+audio_version_id 双键显式引用——§10 禁止默认 selected/末位）与 `imported_audio`（用户/测试显式放入 `output/audio_imports/` 的文件，仓库内相对路径，ref 层+resolve 层双守卫）。不复制音频文件进 canonical。
2. **§4 A1 定位**：A1 生成接口经 `resolve_dialogue` 取文本——**文本真相是 Shot.dialogue**，任意旁白文本无法不重构 A1 而生成；A1 **artifact**（production WAV）可被只读引用。E2E 的 narration 层 = `__voice_foundation_a1` 的 selected A1 production WAV（s03_c01_av01，13.51s），导入目标项目后以 imported_audio 引用（provenance 在报告与文件名中注明）；`a1_dialogue_audio` 引用路径由确定性测试覆盖（真实 store 形状 fixture + fail-closed 全分支）。**Narration 独立层，零接触 Shot.dialogue/Agnes prompt/lip-sync（§34 ✓）**。
3. **§35 诚实声明**：**Music generation source = NOT IMPLEMENTED**（无 Music Provider/Agent，§3）。E2E music = ffmpeg 本地合成的 30s 测试 pad（A 小三和弦），明确标注 test artifact。ambience D 未测（§40：不为凑测试制造无意义素材——控制面与 music 完全同构）。
4. **混音语义**：`amix normalize=0`（本机 ffmpeg 2026-01-07 build 实测支持）——层音量不被 N 输入平均衰减；`duration=first` 以 base（native Shot 音轨）为准——层永不越界拖长输出（越界已在 Phase 1 §12 fail closed）。
5. **§21 clipping advisory**：混音后 volumedetect 实测峰值，> −0.3dBFS → `audio_peak_warning`（记入渲染行 + API 返回）。**不自动改 gain**。E2E 实测峰值 −7.5dB → 无告警（机制在位、单测覆盖告警分支）。
6. **§29 双 revision domain**：AudioPlan 放独立文件——clips 顺序变 → SequenceBlock revision 变、AudioPlan revision 不变、final render STALE（v1 键）；gain 变 → AudioPlan revision 变、SequenceBlock revision 不变、final render STALE（audio 键）。互不污染，测试逐条锁定。

---

## 6. 测试（§46 清单逐项）

确定性测试 **34 项新增**（全部零 ffmpeg——probe/render/concat/峰值 monkeypatch）：

| 文件 | 覆盖 |
|---|---|
| test_sequence_audio_models_store.py（13） | source ref 校验（a1 双键/imports 路径守卫/未知 kind）、layer 校验矩阵（role §8/负值/fade §17/gain §16）、revision 语义（layer_id 不进指纹）、store 往返+fail-closed+空 plan 归一化 |
| test_sequence_audio_basis.py（6） | resolve happy + 全 fail-closed 分支（store 无/dialogue 无/version 无/文件无/无音频流 §33）、basis v2 形状（§26 v1 键保留）、mismatch 全矩阵（§25 plan removed / §27 gain+untracked / §28 sha256 / §29 clips / layer_id 不误报）、freshness 端到端 |
| test_sequence_audio_mix.py（5） | plan determinism、0 层直通、层语义逐键编码（源内裁切 12s→34.8s/−18dB/淡入淡出时刻/delay 定位）、graph 确定、峰值解析+advisory |
| test_sequence_audio_service.py（9） | §25 无 plan=S1 锁死（basis v1+无混音调用+concat 原样）、方案 A 两路径（首渲染重建+mix / 二渲染 base copy 零新分段）、§12/§14/§33 fail-closed、§30 plan 窗口内变更 raced、§27 freshness+恢复、§38 OCC 409、§21 advisory 落行、plan 归一化 |
| test_sequences_api.py（+1） | audio-plan GET/PUT/OCC 409/非法 role+kind 400/空 layers 清空/No Auto Render |

**全量回归 2029 passed / 0 failed**（12:03）。S1 全部 49 项语义不变（仅 prepare 返回形状升级为 PreparedRender，行为断言原样通过）。

---

## 7. 真机 E2E（§40/§42/§43/§44）——ALL PASS

复用 S1 已验证序列 `seq_3164f556`（`__native_av_c1_e2e`，4 镜 22.8s，人评通过；未重新生成 Shot §40 ✓）。驱动 `_sequence_s2_e2e.py`（scratch）；结果 `_sequence_s2_e2e_results.json`。

| 项 | 版本 | 结果 |
|---|---|---|
| §40 A baseline | srender_b6b126d2 | plan 清空后 **CURRENT**（S1 兼容 ✓） |
| §40 B +Music | srender_06485e0f | done，Δ+30ms，**fade_in 2s/fade_out 3s（§43）**，base=671945a7（video copy） |
| §42 B2 −8dB | srender_af8fe767 | done，base=B；**差信号机器证据**：B−A=−43dB（音乐确实混入）、B−B2=−50.3dB（增益差真实生效）、A−A=−91dB（静音底基准） |
| §40 C +Music+Narration | srender_1f0630d0 | done，narration=A1 production WAV artifact（start 4200ms，13.51s），base=B2 |
| §44 freshness | — | 只改 gain → **STALE**（mismatched=['audio.audio_plan_revision', 'audio.layers[1].gain_db']）→ 恢复 → CURRENT；渲染行数/文件 mtime 全等（**0 ffmpeg/provider**） |
| §21 峰值 | — | 三次混音实测 −7.5dBFS → 无告警（advisory 机制单测覆盖，真机未触发属正常） |

方案 A 真机链条：B→B2→C 的 `base_render_version_id` 依次指向前一渲染——**音频迭代零视频重转码**的架构承诺兑现。

---

## 8. 人工听评指引（§41 十问 + §42/§43）

审阅页（scratch，全中文）：`http://localhost:8915/_sequence_s2_review.html?project=__native_av_c1_e2e`
（用户稍后自测即可——页面常驻：后端 :8000 + 静态 :8915 均已在运行。）

页面提供：S1 baseline 与各混音版本逐个播放（每版本卡片标混音构成/增益/峰值）、音频层面板（角色/开始/时长/增益/淡入淡出/源 + 源音频试听）、渲染/设为正式按钮。

**建议听评顺序**：
1. 「srender_b6b126d2」= S1 baseline（纯原生声画）——对照基准；
2. 「srender_06485e0f」（B：music −14dB，2s 淡入/3s 淡出）——验证 §41 问 1/2/3/4/9/10；
3. 「srender_af8fe767」（B2：music −22dB）——与 B 对比，**必须明显听出音乐变轻（§42）**；
4. 「srender_1f0630d0」（C：music −14dB + narration 4.2s 起）——验证 §41 问 6/7（旁白时机/不破坏 AV sync）。

只判断系统控制能力（§41），不评价音乐/旁白艺术上限。

---

## 9. 遗留 / 边界说明

- **A1 narration 生成接口**：文本真相耦合 Shot.dialogue（§4 审计结论）。未来 narration 需要 A1 侧新入口（narration text → artifact），属后续阶段决策，本阶段只消费 artifact、不重构 A1。
- **E2E 过程修正**：`probe_video` 原为视频中心（纯音频容器被误拒）——E2E 首跑暴露，已改为宽容探测（`has_video` 标志）+ 消费点分别强制（视频源/混音输出要求视频流，音频源要求音频流 §33）。教训与 S1 output_file 同型：**单测夹具全绿不等于真机媒体形状全覆盖**。
- **§45 race 真机**：未做（确定性测试已覆盖 plan 窗口内变更 → raced_superseded 全路径；真机注入成本不成本文——按 §45 免）。
- **已冻结域零接触**（§48）：git diff 范围 = src/sequence/*、routers/sequences.py、api_server.py（无新改动）、tests/。`src/audio/*` 零改动（A1 只被只读引用）。

## 10. STOP（§50）

AI Music Generation / automatic ducking / LUFS normalization / Mastering / Film-level calibration / Sequence Agent / Editor Agent **均未开始**。等待用户听评与下一阶段（预计 S3 Collective Audio Calibration，§51）规格——先讨论，不自动实现。

## 11. 人工听评结论（2026-09-16，用户）

**S2 人工听评通过 → SEQUENCE S2 AUDIO LAYER FOUNDATION PASS = FINAL**。

用户原话：「音乐和旁白都准确加入了视频，虽然说音乐只是单音调，但是完全没问题。」

- §41 十问核心项逐条确认：原生声画完整保留 ✓ / 音乐跨切镜连续 ✓ / 起止位置正确 ✓ / 淡入淡出正确 ✓ / 旁白时机正确且不破坏 AV sync ✓ / 无爆音 click 异常静音 ✓ / 整体正常播放 ✓；
- §42 gain A/B（−14dB vs −22dB）实际听评通过，且机器侧有差信号证据（B−B2 = −50.3dB）；
- 用户对音乐素材为单音调测试音明确表示不影响结论——本阶段验证的是**控制能力**（§47），音乐素材质量属于未来 Audio Source Generation 阶段。

→ 至此 **S1 + S2 = Sequence Assembly Foundation 双层收口 FINAL**。下一阶段（预计 S3 Collective Audio Calibration，§51）**先讨论再规格，不自动开始**。
