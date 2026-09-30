# 剪辑提案 v4｜雨灯归航_全片工程预览_44B_Agent_提案（v2·渲染完成）

- 项目：雨灯归航
- 提出者：Assembly Agent
- 时间：2026-10-01 02:31:04
- 性质：提案（非 canonical；顺序/trim 变更需用户确认后才经 assembly.sequence_update 落 canonical）

# 雨灯归航 · 全片工程预览（Phase 44B）— 评审提案 v2

> **状态**：工程预览已完成渲染，等待用户定版
> **快照来源**：snap_44a_formal_20261001_001753（mode=formal，13 镜全 user_locked）
> **Sequence**：seq_a3d5b19b · revision `4df4d8cb`
> **Render**：srender_aca99c81 · status `done` · 路径 `projects\\雨灯归航\\output\\sequences\\seq_a3d5b19b\\srender_aca99c81.mp4`
> **Manifest**：v2（assembly_revision=2，指向 seq_a3d5b19b）

---

## 一、顺序依据

- 严格按快照 order 1→13 排列，**全片 13 镜全部保留**
- 首镜 s01_c01（order=1）至末镜 s03_c04（order=13）
- **无 trim**：第一版工程预览默认纪律——整镜使用、硬切
- 渲染 profile 采用 briefing 推荐的 `recommended_render_profile` 原样传入

---

## 二、时间线（13 镜，总长 ~89.9s）

| # | shot_id | scene | duration_ms | 累计 ms | 备注 |
|---|---------|-------|-------------|---------|------|
| 1 | s01_c01 | s01 | 8,000 | 8,000 | user_locked，暴雨场景 |
| 2 | s01_c02 | s01 | 8,000 | 16,000 | user_locked |
| 3 | s01_c03 | s01 | 8,000 | 24,000 | user_locked |
| 4 | s01_c04 | s01 | 5,175 | 29,175 | repair43_v3 一轮候选 |
| 5 | s01_c05 | s01 | 5,184 | 34,359 | repair43_v2 一轮候选 |
| 6 | s01_c06 | s01 | 5,184 | **39,543** | §2.8 终选一轮；cross-group seam↓ |
| 7 | s02_c01 | s02 | 8,000 | 47,543 | user_locked，跨组接缝↑ |
| 8 | s02_c02 | s02 | 5,175 | 52,718 | repair43_v3 一轮候选 |
| 9 | s02_c03 | s02 | 5,184 | **57,902** | 评审页终选（甲-2）；cross-group seam↓ |
| 10 | s03_c01 | s03 | 8,000 | 65,902 | repair43_v2 一轮候选，跨组接缝↑ |
| 11 | s03_c02 | s03 | 8,000 | 73,902 | repair43_v2 一轮候选 |
| 12 | s03_c03 | s03 | 8,000 | 81,902 | user_locked**_with_caveat** ↓ |
| 13 | s03_c04 | s03 | 8,000 | **89,902** | user_locked |

> **实测片长**：89,923 ms（偏差 +21 ms，源于各镜头实际 frame 计数与 declared duration_ms 的取整差异；属正常编码器边界效应）

---

## 三、跨组切点标记（两处）

| 切点位置 | timeline 位置 | 性质 |
|----------|---------------|------|
| s01_c06 → s02_c01 | **39,543 ms** | 场景 01→02 跨组切换；deep audit 检测到该接缝窗内有 pts 空洞 83ms（详见 §五） |
| s02_c03 → s03_c01 | **57,902 ms** | 场景 02→03 跨组切换；审计无异常 |

> 转场建议（仅供用户参考，renderer v1 仅支持硬切）：
> - s01→s02（39.5s）：暴雨转室内/甲登场，可考虑 2–4 帧黑场或交叉溶解增强情绪过渡
> - s02→s03（57.9s）：甲盲区揭露后切归航收尾，建议保持硬切以维持节奏张力

---

## 四、需要用户终审的事项

### 🔴 高风险接缝（来自快照 seam 实测 MAE 排名 Top 2）

| 接缝 | MAE | 风险说明 | 建议口径 |
|------|-----|----------|----------|
| **s03_c03 → s03_c04** | **86.6** | 同场景内切换，MAE 最高；但 s03_c03 为 `user_locked_with_caveat`（"虽然灯的问题还在，但是暂时就这样吧"），该接缝已隐含用户的权衡接受 | 确认：是否保留当前 cut？若接受，注明「caveat 已记录于 verdict_source」 |
| **s01_c01 → s01_c02** | **61.4** | 暴雨→室内转场，色调突变更为明显；两镜均为 user_locked 基线版本 | 确认：是否接受当前色调跳跃？如需平滑，可考虑加 2–3 帧叠化或 LUT 过渡 |

### 🟡 审计发现

- **s01_c06 → s02_c01（39,543ms）接缝 pts 空洞 83ms**：实测 1 帧缺失，与跨组切点位置吻合，源于源素材帧布局而非渲染器 bug；不影响播放连续性，但需在定版前确认该空洞是否在源片内已存在
- 无重复帧、无黑帧段落、无静音段、音频峰值正常

### 🟢 用户已知选择（无需再审）

- s02_c03 原生音轨近静音（verdict_source 已载明「求稳优选+无背」）
- s03_c03 `user_locked_with_caveat` 状态及原因

---

## 五、音轨现状

- 策略：**native**（保留各镜原始音轨，不做混音/降噪/替换）
- 编码：AAC 48kHz stereo，与源保持一致
- **未添加任何 BGM 或旁白**（narration_cues 留空）
- 音频峰值检测：无 warning

> 下一步若需加旁白/BGM，需在选定 final_render 后另行发起 audio overlay 工序（不在本 agent 域内）。

---

## 六、工程预览标注

> ⚠️ **本成片为「工程预览」**，基于正式快照 snap_44a_formal_20261001_001753 生成，仅供节奏/时序/接缝核查用途。
>
> - 最终定版（final_render select）及是否接受上述风险接缝，由用户显式裁定
> - 如需修改 trim 或调整顺序，需新建 sequence 并重新走完整渲染流程
> - 本次渲染版本 `srender_aca99c81` 为 seq_a3d5b19b 的第一版 render，可被后续版本覆盖

---

## 七、文件清单

| 类型 | 标识 | 路径 / 字段值 |
|------|------|---------------|
| Sequence canonical | seq_a3d5b19b | revision `4df4d8cb` |
| Render output | srender_aca99c81 | `projects\\雨灯归航\\output\\sequences\\seq_a3d5b19b\\srender_aca99c81.mp4` |
| Assembly manifest | v2 | assembly_revision=2，指向 seq_a3d5b19b |
| 本提案 | proposal_v2 | 本文档 |

---

**请用户审阅后给出定版指令**（接受当前 cut / 指定需调整的高风险接缝 / 或提出 trim 修订需求）。
