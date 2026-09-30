# 剪辑提案 v1｜雨灯归航_全片工程预览_44B_Agent_提案

- 项目：雨灯归航
- 提出者：Assembly Agent
- 时间：2026-10-01 02:28:47
- 性质：提案（非 canonical；顺序/trim 变更需用户确认后才经 assembly.sequence_update 落 canonical）

# 雨灯归航 — 全片工程预览（Phase 44B）剪辑提案

## 一、序列信息

| 字段 | 值 |
|------|-----|
| sequence_id | `seq_a3d5b19b` |
| title | 雨灯归航_全片工程预览_44B_Agent |
| revision | `4df4d8cb` |
| snapshot pin | `snap_44a_formal_20261001_001753`（mode=formal，13 镜全部 user_locked）|
| 总时长 | 89,902 ms（≈89.9 s）|
| 渲染 profile | 1280×720 / 24fps / libx264 / yuv420p / aac 48kHz stereo / mp4 / crop fill / native audio |
| 剪辑纪律 | 全部保留、整镜使用、硬切（无 trim）|

> **注**： briefing 显示已存在同内容序列 `seq_900fdf75`（title「雨灯归航_全片工程预览（44B）」，revision 同为 `4df4d8cb`，已有 9 个 done render version）。本次按任务要求新建 title 为「雨灯归航_全片工程预览_44B_Agent」的序列，clips/profile/snapshot pin 完全一致，属等价副本。

## 二、镜头顺序与接缝表

| # | shot_id | scene_id | video_version_id | duration_ms | timeline_start_ms | timeline_end_ms | 场景边界 |
|---|---------|----------|-------------------|-------------|-------------------|-----------------|----------|
| 1 | s01_c01 | s01 | vv_55accb16 | 8000 | 0 | 8000 | — |
| 2 | s01_c02 | s01 | vv_9cda3271 | 8000 | 8000 | 16000 | — |
| 3 | s01_c03 | s01 | vv_703eb03f | 8000 | 16000 | 24000 | — |
| 4 | s01_c04 | s01 | vv_80dc90a5 | 5175 | 24000 | 29175 | — |
| 5 | s01_c05 | s01 | vv_70728b9b | 5184 | 29175 | 34359 | — |
| 6 | s01_c06 | s01 | vv_5ad9192d | 5184 | 34359 | 39543 | — |
| 7 | s02_c01 | s02 | vv_ebbc77f8 | 8000 | 39543 | 47543 | ✓ s01→s02 |
| 8 | s02_c02 | s02 | vv_4bdf6b08 | 5175 | 47543 | 52718 | — |
| 9 | s02_c03 | s02 | vv_49e4bd19 | 5184 | 52718 | 57902 | — |
| 10 | s03_c01 | s03 | vv_f741a354 | 8000 | 57902 | 65902 | ✓ s02→s03 |
| 11 | s03_c02 | s03 | vv_03ff6e45 | 8000 | 65902 | 73902 | — |
| 12 | s03_c03 | s03 | vv_49b85cd1 | 8000 | 73902 | 81902 | — |
| 13 | s03_c04 | s03 | vv_6684f5dd | 8000 | 81902 | 89902 | — |

**场景边界切点**：39543 ms（s01→s02）、57902 ms（s02→s03）。

## 三、Trim 依据

**无 trim**。第一版工程预览默认纪律：全部保留、整镜使用。各镜头 source_in=0、source_out=duration，effective_ms = source_duration_ms。

## 四、转场建议

 renderer v1 仅支持硬切，本草案全为硬切。建议用户评审时考虑：
- s01_c03 → s01_c04（同场景内，灯特写切入）：可保持硬切以保留节奏跳跃感
- s01_c06 → s02_c01（场景切换）：硬切即可，画面内容自然承接
- s02_c03 → s03_c01（场景切换）：同上

## 五、旁白候选

本期为纯视觉工程预览，**旁白留空**（B 批标准做法）。如有旁白需求，请在 C 批 manifest_publish 时传入 narration_cues。

## 六、需要用户终审的事项

1. **是否接受硬切全片**（无溶解/桥接）
2. **s03_c03 为 user_locked_with_caveat**（「虽然灯的问题还在，但是暂时就这样吧」），是否在本次预览中保留
3. **s01_c04 / s02_c02 分辨率 1280×704（非 720p 标准）**，渲染 profile 指定 crop fill，请确认裁切效果可接受
4. **现有序列 `seq_900fdf75` 已有 9 个 done 版本但 final_render=null**；本次新建 `seq_a3d5b19b` 作为 Agent 署名副本，请裁定最终使用哪一条作为正式成片通道
5. 渲染版本 `srender_aca99c81` 当前状态 **queued**，待完成后由用户 select final_render

## 七、违规 / 异常报告

- `assembly.render_submit` 调用返回工具内部异常 `'Job' object has no attribute 'status'`，但 render version `srender_aca99c81` 已入队（status=queued）。渲染异步进行，状态以 render_status 为准。
- 无媒体违规：timeline_preview 全部 pin_match=true，无 violations。

---

**提案状态**：待用户评审后裁定。
**渲染状态**：queued（srender_aca99c81），未完成，未发布 manifest。
