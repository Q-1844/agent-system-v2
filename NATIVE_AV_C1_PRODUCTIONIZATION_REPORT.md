# NATIVE AV C1 — PRODUCTIONIZATION REPORT（21.md）

日期：2026-09-15
规格：`详细流程文件/修复阶段/21.md`（Native AV C1 — Productionization & Truth Integration）
性质：**收缩手术（retraction）+ 一处 freshness 修正 + 测试/E2E/报告按新口径重做**
前置事实：C1 曾按 18.md（20.md 为其重写）实施并人评 FINAL PASS（bcade40→f1185cb）。
用户裁定（21.md §18/§20/§21）：上一版把 AudioEventIntent / music_policy / no-BGM prompt /
Director 声音表演纪律**提前带入**——本次全部撤回，归入未来 C2。

---

## 1. 与上一版 C1 的差异总览

| 项 | 上一版（18.md） | 本版（21.md） | 动作 |
|---|---|---|---|
| AudioEventIntent（music_policy+directives） | 已实现 | **禁止** | 撤回（R1） |
| music_policy 默认策略 + no-BGM prompt 语句 | 已实现 | **禁止**（BGM 归 C2 prompt 实验） | 撤回（R1） |
| compiler 声音语义区（Zone 4） | 已实现 | **删除** | 撤回（R1） |
| Director 五条创作纪律 | 已实现 | 只允许两条数据卫生 | 收敛（R3） |
| provider/model/options 与 freshness | 记录但不参与对比（设计收窄） | **§14 明确要求进 STALE 触发集** | 修正（R2） |
| prompt compiler schema | native_av_prompt_v1 | **v2**（规则集变更，§13 纪律） | 修正（R2） |
| routing predicate / 默认启用 / language truth / pack / basis / freshness 四态 / race guard / BOUND+UNBOUND 统一编译 / stage direction 三分类 / no-auto-regen | 已实现且与 21.md 一致 | 保留 | 核实无误，零改动 |

## 2. 实施段（commits，均可独立回滚）

```text
b3657f7  C1-R1  撤回 AudioEventIntent/music_policy/no-BGM prompt（21.md §2/§6/§18/§20/§21）
b1d8a81  C1-R2  provider/model/options 进 freshness 对比 + prompt schema v1→v2（21.md §13/§14/§16）
6d851ac  C1-R3  Director 创作纪律收敛为两条数据卫生规范（21.md §19）
<本提交> C1-R4  全量回归 + 真机 E2E + 收口报告（21.md §22-§27）
```

### R1 撤回细节
- `src/shot_binding/models.py`：删 AudioEventIntent 块；**旧 bindings.json 中残留的
  `audio_event_intent` 键读取时静默忽略（legacy 容忍，键保留在 from_dict 排除集）**。
- `src/shot_binding/service.py` / `__init__.py`：删解析与导出。
- `src/generation/native_av_pack.py`：删 read_audio_intent / pack.audio_intent /
  basis 的 audio_event_intent_fingerprint + music_policy 键。
- `src/generation/av_prompt_compiler.py`：删 _NO_BGM_GUARD 与 Shot-local sound 区；
  prompt 只保留 B1 已验证的 Dialogue/Speaker/Language 控制 + stage direction 不朗读。
- 测试：删 test_native_av_c1_audio_intent.py；pack_compiler/basis_race 剥 audio 断言
  并加「撤回键不得残留」反向锁。

### R2 修正细节
- `NATIVE_AV_DEPENDENCY_KEYS` 增 provider/model/provider_mode/provider_options_fingerprint。
- `compute_current_dependency_keys` 增 provider_options 同窗参数：generation 窗口内
  （pre-dispatch/pre-commit）携带本次请求 options 对齐比较；freshness 读模型无请求
  上下文 → **默认 dispatch 投影**（mode/num_frames/frame_rate/width/height/seed/
  negative_prompt 来自 shot+project canonical，model=None、无 duration_s）。
  当前 UI 不传 video_model（只有类型声明）→ 默认投影即生产现实，无假 STALE。
  **显式传 model 生成的 final 会诚实显示 STALE(model)**——21.md §14 的字面语义。
- `NATIVE_AV_PROMPT_SCHEMA` v1→v2：撤回 no-BGM/声音规则后生成语义已变，旧 v1 版本
  按 §13 纪律诚实 STALE（经 prompt_compiler_schema 键不一致暴露）。

### R3 细节
- `src/agents/director.py` dialogue 纪律保留 ①多角色 speaker 前缀 ②dialogue 只写
  spoken text；撤回 ③表演节奏 ④多说话人克制 ⑤声音克制（C2 创作手法阶段再议）。

## 3. 21.md §26 成功标准逐条

| 标准 | 证据 | 结论 |
|---|---|---|
| B1 默认生产化 | routing/policy 测试（先前 C1-A 实现，本轮核实未回退） | ✅ |
| language 有正式 truth | project.native_av.spoken_language + override precedence + language_missing fail closed | ✅ |
| dialogue metadata 不污染 Visual routing | test_native_av_c1_routing_policy.py 四条锁死 | ✅ |
| NativeAVGenerationPack 成立 | pack 测试：确定性/无业务 ID/derived slice/撤回键零残留 | ✅ |
| VideoVersion generation_basis 成立 | E2E 五样本 basis 落库（§3.3 证据） | ✅ |
| freshness 成立 | 四态测试 + E2E 五样本全部 CURRENT | ✅ |
| legacy 兼容 | 旧 VideoVersion basis={} → untracked_legacy；旧 binding 残键静默忽略 | ✅ |
| race guard 成立 | 自动化（dialogue/speaker/visual 变更拒绝 attach）+ **真机注入**（§3.4） | ✅ |
| BOUND/UNBOUND 共用同一语义 | 单一 build_native_av_pack + compile_native_av_prompt；E2E E 样本 prompt 与 UNBOUND dialogue 区逐字节一致 | ✅ |
| stage direction 不会成为 spoken text | 三分类解析测试 + C 样本真机 | ✅ |
| no auto regeneration | freshness 纯读测试（零 provider 调用零写）+ boundary_frame_stale fail-closed 真机实证 | ✅ |
| Visual V1 无 regression | 全量回归 1928 passed 零失败 | ✅ |
| 真实 BOUND/UNBOUND E2E 通过 | §3.3/§3.4 | ✅ |
| 用户人评无明显回退 | **待本轮人评（§5）** | ⏳ |

## 3.3 真机 E2E（21.md §24：生产 handler 全链，6 条视频）

路径：`_c1_native_av_e2e` 同款生产接线，新 scratch `_c1_prod_e2e/`（证据 + review manifest）。

| 样本 | 类别（§24） | route | freshness | 机器验证(§34) | 音量 | 时长 |
|---|---|---|---|---|---|---|
| A | UNBOUND 单人对白 | unbound | current | 7/7 PASS | -25.8dB | 6.0s |
| B | UNBOUND 多人 A→B→A | unbound | current | 7/7 PASS | -28.3dB | 8.0s |
| C | UNBOUND stage-direction 输入 | unbound | current | 7/7 PASS | -24.0dB | 6.0s |
| D | UNBOUND 无对白 | unbound | current | 7/7 PASS | -34.0dB | 5.0s |
| E | BOUND 对白（S3C.2 golden path 项目） | bound | current | 7/7 PASS | -28.8dB | 4.0s |
| R | §16 race 真机注入 | — | （拒绝 attach） | pass=True | — | — |

- D（无对白）prompt 与 visual prompt **逐字节一致**（§4：无 dialogue 行为不变）。
- 五样本 basis 结构 = §11/§14 口径：撤回键零残留、prompt_compiler_schema=v2、
  provider 事实在位（model=None = 默认 dispatch）。
- 编译 prompt 抽查：music/Shot-local sound 字串零出现；红线一/二 + swap guard + silent 声明在位。

### §16 真机 race 注入（R）
生成窗口内（poll 回调）改写 dialogue → pre-commit 重算依赖键 →
`native_dependency_changed_before_commit` → **final_video 不变、不建 VideoVersion**；
provider 输出仅存 diagnostic_video_id（证据）。收据：`_c1_prod_e2e/evidence/R/race_receipt.json`。

### 真机过程实证的两条既有设计（非本轮引入，如实记录）
1. **boundary_frame_stale fail-closed（§14/§39）**：E 首跑被正确拦截——boundary 帧的
   binding_revision（dab4f503）≠ 当前（ebfde13b），系统拒绝烧视频。处理 =
   显式重生成 boundary 帧（`_c1_prod_e2e/regen_bound_frames.py`，走生产
   _handler_first_frame/_handler_last_frame），重跑即过。零 auto-refresh，符合规格。
2. **recover 路径诚实 untracked**：续轮询 attach 的版本 basis={} → untracked_legacy
   （既有测试 + 上一轮 E2E 结论沿用）。

## 3.5 测试与回归（21.md §22/§23）

| 套件 | 结果 |
|---|---|
| C1 targeted（pack_compiler / basis_freshness_race / routing_policy / dialogue_stage_directions） | 全过（basis_race 38、总 245 含 shot-binding 家族） |
| B1 targeted | 44 passed |
| Director / agents | 37+68 passed |
| **full regression** | **1928 passed，0 failed**（较上轮 -11 = 删除的 audio intent 测试，新增 4 条 §14 用例已计入） |

新增测试要点：model 变 → STALE；provider options 变 → STALE；默认投影==dispatch
（防假 STALE）；options 漂移 pre-dispatch 拒绝；撤回键零残留反向锁。

## 4. 明确未实现（21.md §2 禁令，本轮如实保持）

```text
Voice Reference / audios[] / Agnes reference mode
AudioEventIntent / SFX prompt framework / BGM prompt optimization / music_policy
复杂 AV prompt 创作优化 / 声音创作手法研究（→ C2）
Sequence Audio Post / Music / Narration timeline / Final Mix
A2 / SyncLabs 复活 / source separation
新 Agent / Sound Designer
```

## 5. Human Review（21.md §25——轻量：只判断「productionization 没把已验证效果弄坏」）

- 评审网站：`"D:/Delta Force/python.exe" _native_av_c1r_review_server.py` → **http://localhost:8912/**
- 5 条样本视频 + race 状态行；6 项判据（台词/speaker/语言/stage direction 未朗读/
  BOUND 连续性/overall vs B1）+ 短评。
- 反馈落盘：`_c1_prod_e2e/results/C1R_REVIEW_FEEDBACK.json`。
- 证据目录：`_c1_prod_e2e/evidence/<key>/`（job_receipt / video_version / freshness /
  machine_validation / video.mp4）+ `review_manifest.json`。

## 6. 结论

```text
NATIVE AV C1（21.md）
PRODUCTIONIZATION IMPLEMENTATION PASS
HUMAN FINAL REVIEW PENDING
```

STOP——等待用户人评。不自动进入 C2（AV Prompt & Sound Staging）、Voice Reference、
Music、Audio Post、Final Mix。

## 7. 人评收口（2026-09-15 用户裁定）

**C1（21.md）FINAL PASS**——productionization 未把 B1 已验证效果弄坏。

评审点击（`_c1_prod_e2e/results/C1R_REVIEW_FEEDBACK.json`）：
- **dialogue / speaker / language：5/5 样本全部「正确」**——声音维度零回退；
- overall：B「更好」、D「更好」，A/C/E「相当」，**无「更差」**；
- E（BOUND）continuity=「退化」；B/C 短评「声音确实更自然」；D「场景转换很优秀/非常好」。

用户短评指认的剩余问题**全部为画面侧**（非 C1 引入）：
- C：人物没有绕着楼梯走（动作与提示词/分镜不符）、男方只出现一只手——「提示词的问题」；
- E：表情不够紧张、手枪方向拿起来后发生变化（镜内物体连续性）——「声音还好，画面问题」。

**用户两条裁定（记录为后续阶段约束）**：
1. 后续 E2E 只对**效果差的样本做专门测试**，不再跑全量样本套件；
2. 当前问题主要是**画面问题**——下一方向为画面/提示词（人物-动作贴合、镜内物体
   连续性、表情表演），具体规格待用户给出，不自动开始。

### 给 C2 的预留观察（不实施）
- BGM：撤回 no-BGM 规则后恢复 B1 原生行为（本轮 5 样本未见异常 BGM——样本少不构成
  结论；C2 做 prompt-level no-music 实验时再评）。
- Audio Intent：上一轮人评曾给正收益（E「完美」），本轮按用户裁定撤回；C2 在真实
  创作证据上重新决定是否需要结构化 intent。
- 表演自然度（短句/多人镜）：C2 prompt 优化观察项。
