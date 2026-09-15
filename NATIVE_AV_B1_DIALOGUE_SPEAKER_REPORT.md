# NATIVE AV B1 — Dialogue + Speaker Control 报告

执行依据：`详细流程文件/修复阶段/17.md`（B1 最小增量验证阶段）。
上游：`NATIVE_AV_DIRECTION_REBASELINE_REPORT.md`、`AGNES_AUDIO_DIRECTION_CORRECTION_REPORT.md`、A0 能力探针证据。

---

## 0. 当前状态

```text
B1 FINAL PASS（2026-09-15 用户人工评审裁定：方向确认，无大问题）
Q1 台词/语言控制 = 明确正收益；Q2 speaker swap = 零出现（控制未引入 swap）；
Q3 视觉不退化 = 通过
§21 判定 = 情况 A（+E 成分）→ 下一步优先 productionize B1；Voice Reference 不做
用户点名的后续方向：提示词优化 + 创作手法，做画面与声音的良好匹配
```

人评前曾维持 `B1 IMPLEMENTATION PASS / HUMAN QUALITY REVIEW PENDING`（§19/§23 口径）。

---

## 1. 修改前真实链路（代码事实审计，§4）

| # | 审计项 | 事实 | 位置 |
|---|---|---|---|
| 1 | `Shot.dialogue` 格式 | str；多句 `\n` 分隔；行可带 `角色名：` 前缀（有时带 `「」`）；偶有 `（舞台指示）` | `src/core/models.py:268` |
| 2 | Director 写入约定 | dialogue=台词唯一归属字段，“可多句、可带说话人，例如：小铃：「…」” | `src/agents/director.py:291` |
| 3 | `Shot.characters` | `List[str]` | `src/core/models.py:267` |
| 4 | `ShotProductionBinding` | dataclass + known-field 前向兼容；schema v1 fail closed | `src/shot_binding/models.py` |
| 5 | `build_video_prompt` | 纯函数，仅 2 个生产调用点（legacy handler + BOUND adapter） | `src/core/prompt_builder.py:93` |
| 6 | `BoundVideoRequest` | ephemeral、无 invented provider 槽；prompt=creative+ctx/style tail | `src/generation/video_adapter.py` |
| 7 | BOUND/UNBOUND | `shot_has_binding` → BOUND（永不回落 legacy），否则 legacy | `routers/shots.py` `_handler_video` |
| 8 | Agnes `create_video` | prompt 是唯一可承载 B1 变化的字段；config 默认 `agnes-video-v2.0` | `src/api/agnes_client.py:562` |
| 9 | VideoVersion/selection | `prompt_snapshot` 已全量落库 compiled prompt（B1 零 schema 改动的现成证据位） | `routers/shots.py` |

**审计与 Re-baseline 报告的偏差**：无重大偏差。新增记录一个真实数据事实：
既有项目存在“行内 speaker 前缀 ∉ Shot.characters”的 shot（如 Replay_Gov s01_c03：
陈海锋 ∉ [林晚星,小满]）——正是 §8 要求 reject 的场景，B1 gate 开启时这类镜头会
fail closed 并给出结构化诊断（属预期行为，不是缺陷）。

## 2. 修改文件

```text
src/generation/dialogue_control.py            新增（parser/resolver/block compiler）
src/shot_binding/models.py                    +DialogueSpeakerLine/+DialogueSpeakerBinding
                                              （ShotProductionBinding 可选块）
src/generation/__init__.py                    导出新公共面
src/generation/video_adapter.py               build_bound_video_input +2 个显式 kwargs
routers/shots.py                              /video 端点 +handler 两处接线
tests/test_native_av_b1_dialogue_control.py   新增 30 测
tests/test_native_av_b1_prompt_integration.py 新增 15 测
_b1_dialogue_probe/                           E2E scratch（driver/样本/证据）
_native_av_b1_review_server.py                评审网站（scratch）
```

Commit 分段（§22）：`1ffffaa`（B1-A parser+resolution+binding 块+测试）→
`4e03885`（B1-B prompt integration+feature gate+回归测试）→ 本报告（B1-C）。

## 3. Dialogue 如何解析（§7）

* `split_dialogue_lines`：`\n`/`\r\n` 分句 → 逐行 strip → 丢空行（确定性）。
* `split_speaker_prefix`：`角色名：` 前缀（全角/半角冒号，name ≤20 非空字符）→
  **剥离前缀是 speaker 元数据分离，不是改写台词**；台词切片=冒号后原样
  （含 `「」`，仅去首尾空白）。无前缀 → 整行原样。
* **不做**：不改写/总结/润色/翻译/补词（§5）；`（…）`舞台指示保留原样（如实报告：
  这类指示词会进入 prompt 的台词引用内，E2E 样本未选用带指示的镜头）。

## 4. Speaker 如何解析（§6/§7/§8）

优先级（对每一句，按非空行序 0-based index）：

1. `ShotProductionBinding.dialogue_speaker_binding` 显式裁决（若该 index 被覆盖）；
2. 单角色镜（规范化去重后恰好 1 个角色）→ `single_auto`（与 A1 `resolve_speaker`
   哲学一致）；
3. 多角色镜 → 行内 `角色名：` 前缀作为解析辅助证据（§7.2）；
4. 仍无法唯一确定 → **fail closed**（绝不默认第一个角色/猜脸/猜性别/LLM 猜/静默继续）。

`control.resolution`：`single_auto`（纯单角色 auto）| `explicit`（binding 或内联前缀参与）。
`silent_characters` = Shot.characters − 已解析 speakers（保序派生）。

## 5. Fail-closed 规则（结构化诊断 `DialogueControlError`）

| code | 触发 | 来源条款 |
|---|---|---|
| `language_missing` | gate on 且 language 空 | §10（不建语言系统） |
| `ambiguous_speaker` | 多角色、无裁决、无前缀 | §7.2 |
| `speaker_not_in_characters` | binding 或前缀 speaker ∉ Shot.characters（不自动加角色/不纠错） | §8 |
| `invalid_binding` | index 非整数/越界/重复、speaker 空 | §6 |

fail closed 发生在任何 provider 调用之前 → **provider 调用数 = 0**。
`bindings.json` 损坏 → `ShotBindingSchemaError` 向上传播（与 `shot_has_binding` §39 同口径）。
`dialogue_speaker_binding` 变化会翻动 `binding_revision` → 既有 pre-dispatch race guard
自动覆盖 B1 块。

## 6. Prompt 编译形式（§10）

现有 visual prompt **原样在前**，dialogue block **单空格追加在后**（整条维持 A0 已验证
的单行形态——A0 D/J 组即“单行 + 内联「」引用”；不引入未验证的多行 prompt 形态）：

```text
Animate this image: 两人站在门前，交替看向对方与木门。, keep character identity and
outfit consistent On-screen spoken dialogue in Mandarin Chinese: 1. 林晚星 says:
「门开了吗？」 2. 顾岩 replies: 「还没有。」 3. 林晚星 replies: 「那就快一点。」
Keep every line assigned to its specified speaker. Do not swap the speakers and do
not invent any additional dialogue. The characters' mouths move naturally in sync
with their own spoken lines.
```

* 台词文本 = `Shot.dialogue` 原始切片；speaker = resolved binding；换人=replies、
  同人续说=then says（确定性）。
* 沉默角色显式声明（如 Sample D：`小满 remains completely silent in this shot…`）。
* **language 来源**（§10 要求记录）：E2E 以显式 caller 输入传 `"Mandarin Chinese"`
  ——依据=项目创作语料的既成事实（Director 全中文台词、A0 D 组实测中文对白稳定），
  非新语言系统；生产化时的默认策略留给人评后的下一阶段决策。

## 7. Feature gate（§12）

* `POST /api/project/{p}/shot/{s}/video?native_dialogue_control=true&native_dialogue_language=...`
  → `job.params` → handler（`video_model` 先例同型）；默认 False=行为逐字节不变。
* gate off 时不调用 dialogue_control 任何函数；两条路径（legacy/BOUND）均验证
  逐字节等价（见 §8）。
* 未做项目级/全局默认开启；本阶段仍是 capability validation。

## 8. 自动测试结果（§14）

```text
tests/test_native_av_b1_dialogue_control.py    30 passed（parser/解析规则/fail closed/
                                               binding round-trip+revision 翻动/确定性/
                                               A1A2 零依赖 AST+fresh-import 双检）
tests/test_native_av_b1_prompt_integration.py  15 passed（两条路径 flag OFF 逐字节等价/
                                               flag ON/fail closed 零 provider 调用/
                                               request shape 无 audios+reference mode/
                                               非 prompt 字段逐字节等价/keyframes 双帧/
                                               prompt_snapshot 证据/端点 gate 参数）
全量回归：1861 passed，零新增 failure（基线 1817 → +45）
```

回归覆盖（§14 对应）：无 dialogue 行为不变 ✓ / 原文进 block 且不改写 ✓ / deterministic ✓ /
单角色 single_auto ✓ / explicit binding ✓ / 缺失或歧义 fail closed ✓ / 非法 speaker reject ✓ /
多句顺序 ✓ / flag OFF 请求等价 ✓ / visual prompt 原有内容不丢 ✓ / first/last/keyframes 不变 ✓ /
无 audios ✓ / 无 reference mode ✓ / A1/A2 零依赖 ✓。

## 9. Regression

`git log` 两段 commit 均通过全量回归；S3C.2 既有 BOUND 行为测试（`test_shot_binding_s3c2.py`）
零改动零失败；Visual V1 视频链路（boundary frames/freshness/guard/VideoVersion）零触碰。

## 10. 真机 E2E（§15/§16/§17）

* 执行方式：**生产编译函数实时编译**（`build_video_prompt` / `compile_prompt_with_native_dialogue`，
  非 prompt 复制品）+ 生产 `AgnesClient.create_video`（config 默认 v2.0，i2v，1280x704）；
  A/B 同 shot / 同首帧（A0 已验证资产 URL）/ 同模型 / 同时长，**唯一差异 = prompt**。
* 样本（项目 `projects/__native_av_b1_e2e`，dialogue 全按 Director 既有约定）：

| 样本 | Shot | 内容 | 考察 | variants |
|---|---|---|---|---|
| A | s01_c01 | 单角色长句（24 字） | 台词准确性/中文稳定/lip-sync | A×1 + B×1 |
| B | s01_c02 | 单角色超短句「我们走。」 | 语言先验风险 | A×1 + B×1 |
| C | s02_c01 | 双角色三句 林晚星→顾岩→林晚星 | **speaker swap（最重要）** | A×3 + B×3 |
| D | s02_c02 | 双角色仅林晚星说话 | 沉默控制 | A×1 + B×1 |

* 结果：**12/12 生成成功，0 失败**；时长 8.0s/5.0s 全部符合规划；12/12 含原生音轨；
  账本 84.5/400 video_seconds。
* 证据（§13 实验证据，非 production truth）：`_b1_dialogue_probe/evidence/S*/`
  （request.json 含 compiled prompt + speaker map + dialogue truth；create_response；
  final_poll；video.mp4；ffprobe.json）；汇总 `_b1_dialogue_probe/evidence/compiled_prompts.json`
  与 `review_manifest.json`。

## 11. 评审页面（§18）

```text
启动："D:/Delta Force/python.exe" _native_av_b1_review_server.py
地址：http://localhost:8910/
反馈：_b1_dialogue_probe/results/B1_REVIEW_FEEDBACK.json
```

每个样本展示：Shot ID / A、B 徽标 / 视频 / dialogue truth / 预期 speaker 序列（含沉默方）/
首帧缩略图 / compiled prompt 明细（折叠）。用户对**每条视频**判 5 项（§18 原表）：
① 台词正确性（正确/轻微问题/错误）② 说话人分配（正确/swap 或错人）③ 语言（正确/错误）
④ 原生 lip-sync（可用/不可用）⑤ 整体 AV（PASS/ACCEPTABLE/FAIL）+ 自由短评。
无任何自动声学评分参与判定（§19）。

## 12. 明确没有实现（§2 禁止清单全部未做）

VoiceReference / `audios[]` / reference mode / 2.5 reference visual pipeline /
CharacterVoiceProfile 接线 / `VideoVersion.generation_basis` / Audio Event Intent /
SFX / Music / Sequence Audio Post / narration pipeline / A2(SyncLabs) 改动 /
source separation / 新 Agent / Director 大改 / 自动 speaker 检测 / 自动声音评分——
**均未实现，连同“顺手做掉”一起未做**。

另如实记录的实施口径（非缺陷，后续阶段可议）：

1. E2E 走 **legacy/UNBOUND 路径**（当前生产默认路径）；`dialogue_speaker_binding`
   解析在 BOUND adapter 同样生效并由测试覆盖，但未跑 BOUND 全链真机（BOUND 全链
   需要完整 binding 基建，与本阶段“验证 prompt 质量收益”目标正交）。
2. language 为显式 caller 输入，未建语言 canonical。
3. prompt block 为单行形态（A0 已验证形态；多行 prompt 形态未验证，未采用）。
4. resolver 读取 `dialogue_speaker_binding` 与 BOUND 路由相互独立：仅带 dialogue
   块、无完整生产绑定的 shot 在 gate on + 走后端视频端点时仍会被 `shot_has_binding`
   路由进 BOUND 链并因 binding 不完整失败——B1 不改该语义（如实暴露给下一阶段）。

## 13. 当前状态（人评后收口）

```text
实现（B1-A/B）：已提交 1ffffaa / 4e03885，全量回归 1861 passed 零新增 failure
真机 E2E（B1-C）：12/12 成功，证据齐备
人评（2026-09-15）：62 条点击 + 9 条短评 → B1 FINAL PASS，方向确认
评审页空白修复：PAGE raw 字符串（195ee46）
```

## 14. 人工评审结果（§21 决策树输入）

反馈源：`_b1_dialogue_probe/results/B1_REVIEW_FEEDBACK.json`（62 条）。

| 样本 | A 侧（当前生产） | B 侧（B1 控制） |
|---|---|---|
| A 单角色长句 | PASS | PASS |
| B 单角色短句 | **FAIL**——把 motion 提示词「它微微点头，转身欲行」当台词念出 + 自动加背景音乐 | PASS（"没问题"） |
| C 三句对话 | **3/3 FAIL**——说日语（2 条明确标注日语 + 1 条 language=错误） | **3/3 PASS**（"中文，效果还不错，很好"；"声音效果最自然，可能是用了标点符号的原因"；B02 有背景音） |
| D 沉默控制 | dialogue 正确但有背景音 | ACCEPTABLE（"没什么大问题"） |

三问答案（人评口径）：
* **Q1**：明确改善——A 侧台词样本 4/6 FAIL（日语 / 念提示词 / 语言错误），B 侧 **6/6 PASS**。
* **Q2**：12 条 speaker 全部正确（零 swap）；B 侧控制未引入 swap。
* **Q3**：无视觉负面标注；lip-sync 全部"可用"。

**§21 判定 = 情况 A（含 E 成分）**：
* 下一步优先 **productionize B1**（默认策略 / generation basis / freshness / 必要 UI——
  待用户批准规格后另立阶段文档，勿自动开始）；
* **Voice Reference（B2）不做**（A0 已证声线一致性可接受 + 本轮无跨镜漂移诉求）；
* 用户点名的后续方向：**提示词优化 + 创作手法，做画面与声音的良好匹配**
  （SC_B03"标点符号"短评与 A 侧"念提示词"现象均是直接输入）。

背景音乐溢出（A 侧 B/D 样本、B 侧 SC_B02/SD）是 A0 已知边界（对白场景 BGM 层级
控制不完美），本轮不为它扩控制面——归入后续提示词优化方向。
