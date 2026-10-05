# Phase48 B批｜原生音效保留与装配政策报告

> 日期：2026-10-06 · 分支 `feat/agent-v2-phase-d-golden-run`
> 执行书：`docs/workflow/PHASE48_B_NATIVE_AUDIO_AND_ASSEMBLY_POLICY.md`
> 前置：A 批出口满足（PHASE48_A_BASELINE_AND_REPAIR_REPORT.md §12）
> 隔离项目：`P48_B_Audio`（12 镜音频样本）· 全部生成走系统正式路径，零旁白

## 1. 结论总览

| 项 | 结果 |
| --- | --- |
| B1 政策与契约修改 | 完成：assembly 角色政策重写（原生音效优先）+ W08/W09 装配契约更新 + 模板版本 `p48_templates_v1`；新增 5 项政策回归，受影响回归 52+187 全过 |
| B2 12 条音频样本 | 完成：12/12 真实生成+分类（声学+ASR 双证据）；对白 2 条 ASR 逐字命中冻结台词；**12 条均无意外旁白**（该覆盖类如实记录为"未观测到"，不伪造样本） |
| B3 装配对照 | 完成：两个 W08 run（基线/政策版）由 assembly Agent 自主完成，均停靠成片审定（用户关口）；两版渲染**逐字节相同**（sha256 一致）——全部输入合格时政策输出=原音全保留，这是政策的正确结果而非无差异 |
| 刷新对账演练 | 完成：后端重启后 run 状态/审核关口/渲染文件逐项保持；成片审定 pending 未被翻转 |
| 用户关口 | 两版成片候选（`srender_5ddd85d0` / `srender_202f5365`）均停在 n_rv1 成片审定，等待用户试听与定版 |

## 2. B1 政策与契约修改（代码 diff）

### 2.1 `src/agents/assembly.py`（ASSEMBLY_SYSTEM 声音工作流重写）

旧政策以旁白为中心（brief 给定旁白密度→narration_read→tts），音乐/环境声为 plan 层，"原音静默=base_gain_db=-60"。新政策（Phase48 起）：

- **默认保留合格原生环境声与动作声**；"没有配乐"≠"应该静音"——整条关原音只允许显式策略（profile audio=silent 或用户 brief 明确要求）。
- **先分类后决定**：逐镜实测原生音轨（media_probe/ffprobe+声学指标+ASR），标注【合格环境声/动作声】【混有音乐】【画面内对白】【意外旁白或乱语】【无音轨/近静音】之一，分类与依据写入提案；不得凭"有音轨"宣称合格，也不得只凭响度低判静音。
- **全片禁止自动旁白**：TTS 能力保留但降级为"仅当用户 brief 明确要求"；无要求时旁白工具一律不用。
- **画面内对白**：原音候选保留并标注"待对白专项验收"，不静音、不替换。
- **意外旁白/乱语**：如实标记不合格+修复选项（重生成该镜声音/保留原片另制音轨/局部处理方案与损失），不自动整片静音掩盖。
- 音乐仅按作品 brief（imported_audio 语义照旧）；deep_audit 增加对照分类的"该保留的在场、该排除的已处理"核查。

### 2.2 `src/workflow/templates.py`（装配契约）

- W08 默认目标 → "默认保留合格原生环境声与动作声（逐镜先分类再决定），禁止自动旁白；音乐仅按作品政策"；requiredInputs "旁白和音乐政策"→"音频政策（禁止自动旁白/默认保留合格原生音效/音乐按作品要求）"；expectedOutputs "真实 TTS"→"音轨（原生保留或按政策处理）"。
- W09 默认装配目标 → 删除 Phase47 的"**外部旁白+连续音乐，原视频静音/控音**"（用户观察到的静音问题根源），替换为新政策语句。
- `TEMPLATE_VERSION` `p47_templates_v1` → **`p48_templates_v1`**（实质政策变更必须可追溯；A 批冻结基线 JSON 保留旧值作历史记录）。

### 2.3 Skill 名册边界（执行书要求）

assembly 仍**不在**可绑定 Skill 名册（skill.json bindings 无 assembly）——本批只修改角色政策与契约，未伪造 assembly Skill 绑定；relay/bridge 两 Skill hash 零改动（复验 `sha256:4c529461…` / `eb23f971…` 与 A 批冻结一致）。

### 2.4 回归

| 套件 | 结果 |
| --- | --- |
| 新增 `tests/test_p48_b_audio_policy.py` | 5 passed（默认目标承载新政策/W09 静音默认删除/版本升级/元数据/角色政策条款） |
| A 批修复回归 + p46/p47 语义 | 52 passed |
| assembly/sequence/audio 关键词扫描 | 187 passed |

## 3. B2 样本生产与音频标签表

### 3.1 生产路径

隔离项目 `P48_B_Audio`（2d_animation），1 场景 12 镜夹具（ProjectStore 直写），生成走正式管线：`first-frame` 任务→choose→`video` 任务→set-final。模型 agnes-video-2.5-flash（i2v，4-12s 档取 5s），1280×704@24fps，aac 32kHz stereo 原生音轨。

**真实失败记录（未绕过）**：对白样本两次被系统 fail-closed 拦截——①`ambiguous_speaker`（多角色镜无 speaker 裁决，17.md §7.2）；②加内联"角色名："前缀后 `speaker_not_in_characters`（17.md §8）。修正：dialogue 字段加前缀 + `Shot.characters` 加入说话人（夹具数据修正，非代码绕过），第三次提交成功。该拦截链证明对白说话人绑定纪律在 REST 路径同样生效。

### 3.2 音频标签表（12/12）

分类=声学指标（ebur128 LUFS/astats）+ 本机 ASR（faster-whisper small）联合判定；"音乐 vs 环境/动作"的细分超出可自动判定边界，标 `music_uncertain` 留待人耳（试听关口见 §6）。完整数据：`_p48_b_audio_labels.json`。

| shot | 意图 | LUFS | ASR | 分类 | 备注 |
| --- | --- | --- | --- | --- | --- |
| s01_c01 | 动作音效 | -28.8 | 无 | non_speech_sound（动作/环境） | 响度最高，合书动作声 |
| s01_c02 | 动作音效 | -38.1 | 无 | non_speech_sound | 脚步/走廊 |
| s02_c01 | 环境声(雨) | -45.8 | 低置信幻听已过滤 | near_silent | 模型未给强雨声；**"缺音乐≠该静音"的实证** |
| s02_c02 | 环境声(夜) | -40.6 | 低置信幻听已过滤 | non_speech_sound | |
| s03_c01 | 混音乐(提琴) | -35.4 | 无 | non_speech_sound（music_uncertain） | 待人耳判音乐含量 |
| s03_c02 | 混音乐(收音机) | -42.4 | 无 | non_speech_sound（music_uncertain） | 同上 |
| s04_c01 | 人物对白 | -30.6* | **"你好！今天天气真好！"** | speech_dialogue_candidate | ASR 与冻结台词**逐字一致** |
| s04_c02 | 人物对白 | -31.2* | **"这把椅子还要再修一天"** | speech_dialogue_candidate | 同上 |
| s05_c01 | 预期静音 | -70.0 | 无 | near_silent | 静物空镜 |
| s05_c02 | 预期静音 | -63.4 | 无 | near_silent | 雪原空镜 |
| s06_c01 | 旁白风险(航拍) | -41.4 | 低置信幻听已过滤 | non_speech_sound | **无旁白产生** |
| s06_c02 | 旁白风险(烹饪) | -34.7 | 低置信幻听已过滤 | non_speech_sound | **无旁白产生** |

\* 对白条 LUFS 为整镜综合（含语音段），精确值见 labels JSON。

**覆盖类执行情况（如实）**：动作音效✓ 持续环境声✓ 混音乐✓（细分待人耳）人物对白✓✓ 无音轨/近静音✓✓；**"音效中夹意外旁白"在 12 条中未观测到**（两个旁白风险镜头均未产生解说）——该覆盖类按实际观测记录，不为凑类而伪造。

**ASR 幻听教训（影响 D 批）**：whisper 对雨声/烹饪噪声会产生高"文本形"幻听（s02_c01 曾输出多语乱语、logprob=-3.5）。语音判定必须同时要求 `no_speech_prob<0.4 且 avg_logprob≥-1.0`，否则会把噪声判成旁白/对白。D 批对白验收沿用此双阈值。

## 4. B3 装配对照（Agent 自主，非脚本装配）

### 4.1 两个 W08 run（同 12 条 final 版本受控输入）

| run | 目标 | 节点结果 | 产出 |
| --- | --- | --- | --- |
| B-ASM-NATIVE `run_11b02dd08639` | 基线：原生音轨全保留 | n_asm success（task_91102fa0a9ed，thinking=True） | seq_7fd54ccc + srender_5ddd85d0（62.09s，h264+aac 48k） |
| B-ASM-POLICY `run_9ca0c953571a` | 新政策：逐镜分类后决定 | n_asm success（task_304c4ec4ac36，16 次工具调用） | 复用 canonical 序列（诚实不重复建）+ srender_202f5365 + **12 镜逐镜分类表**（proposal_v2.md） |

- **两版渲染 sha256 逐字节相同**（`91e046b487b91ae8…`）：输入全合格时，政策版正确行为=原音全保留=与基线一致。这不是"无差异"，而是政策的可复现证明。
- **无旁白实证**：两 run 的 AudioPlan=null（无 narration 层）；新政策下 Agent 未调用任何 TTS/音乐工具；整片 -21.0 LUFS（非静音）。
- **原生音轨逐镜在场实证**（基线渲染分段 ebur128 vs 源镜头）：

| 段 | 渲染内 LUFS | 源镜头 LUFS | 判定 |
| --- | --- | --- | --- |
| s01_c01 动作 | -28.9 | -28.8 | 保留 1:1 |
| s02_c01 雨（近静） | -46.2 | -45.8 | 保留 1:1 |
| s04_c01 对白段 | -14.5 | （语音在场） | 原音保留、未替换 |
| s06_c02 烹饪 | -34.1 | -34.7 | 保留 1:1 |

- POLICY2 提案（proposal_v2.md）含逐镜分类表：12 镜均"保留"、无控增益、无不可用——与本报告 §3.2 独立分类结论一致（交叉验证）。
- **真实失败（保留）**：B-ASM-POLICY 首次提交 `run_d0f0b16d10d7` 0.16s failed——与基线 run 同时启动触发**同项目 assembly 会话互斥**（A09 单一驱动者机制）。失败历史保留；政策版于基线停靠后单独重启成功。教训：并行 W08 run 必须串行驱动。

### 4.2 刷新对账演练（B 执行书步骤 6）

后端进程 kill→重启（startup recover_workflows）后核对：

| 项 | 结果 |
| --- | --- |
| run 状态 | 两 run 均 waiting_human（未翻转） |
| 成片审定关口 | n_rv1 pending 两处全部保持，无自动派发下游 |
| 渲染文件 | 两个 mp4 sha256 刷新前后逐字节相同 |
| run JSON | 语义字段不变；文件 hash 有变化=恢复对账写入时间戳（如实记录，非状态改写） |

**判定：PASS**（语义级一致；文件级差异来自恢复机制的合法时间戳写入）。

## 5. 交付物

| 文件 | 内容 |
| --- | --- |
| 本报告 | 政策修改、样本分类、装配对照、演练结果 |
| 代码 diff | `src/agents/assembly.py`、`src/workflow/templates.py`、`tests/test_p48_b_audio_policy.py`（git 提交） |
| `_p48_b_samples.py` / `_p48_b_samples_evidence.json` | 样本生产驱动+逐镜证据（含 speaker fail-closed 记录） |
| `_p48_b_audio_classify.py` / `_p48_b_audio_labels.json` | 分类脚本+完整声学/ASR 数据 |
| `_p48_b_assembly.py` / `_p48_b_assembly_evidence.json` | 装配对照驱动+run 证据 |
| `_p48_b_refresh_before.json` | 刷新演练前快照 |
| **可播放产物** | `projects/P48_B_Audio/output/sequences/seq_7fd54ccc/srender_5ddd85d0.mp4`（基线）与 `srender_202f5365.mp4`（政策版，逐字节同） |

## 6. 用户关口与未解决问题

1. **两版成片候选停靠 n_rv1 成片审定**（用户 REST/界面显式动作）：试听点建议——s01_c01 动作声是否自然、s02_c01 近静音段是否可接受、s03_c01/c02 是否含模型自配音乐、s04 两句对白清晰度与口型。
2. **music_uncertain 两条（s03_c01/c02）需人耳判定"是否混有模型自配音乐"**——这决定"当前模型是否已完全取消 BGM"的结论边界（Phase48 总控 §1 保留项）。
3. **"意外旁白"覆盖类未观测到**：若用户希望强验证，可在 D 批对白测试中观察是否出现解说泄漏（D 批计划内）。
4. 并行 W08 run 的会话互斥：机制正确但用户需知"同项目同时只能驱动一个装配 Agent"。
5. 源素材 32kHz→渲染 48kHz 重采样为渲染器既有行为（POLICY2 提案已注明），无 действ损失判定需人耳复核。

## 7. 退出条件核对

| 执行书要求 | 状态 |
| --- | --- |
| 装配 Agent 保留合格原生音效、消除"没音乐就静音" | ✅ 政策+契约+真机 run 双证（W09 旧默认目标已删除） |
| 全部测试禁止旁白 | ✅ 两 run AudioPlan=null、无 TTS 调用、ASR 全片无高置信语音（除对白镜） |
| 对白保留并单独标记 | ✅ 对白原音保留，标注"待对白专项验收"（D 批验收） |
| 12 条新音频样本及装配对照 | ✅ 12/12 真实生成+分类（覆盖类偏差如实记录） |
| 刷新/中断对账 | ✅ 刷新演练 PASS |
| 正式入口 Agent 自主装配 | ✅ 两 run 均经 n_asm agent 节点（thinking=True，taskRefs 在案） |

**B 批出口：满足。C/D 可进入**（C 的接口与音频政策前提已具备；D 的对白/说话人绑定路径已验证可用）。
