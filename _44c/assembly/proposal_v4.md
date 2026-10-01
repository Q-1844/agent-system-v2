# 剪辑提案 v4｜雨灯归航 Phase 44C 工程预览：旁白与配乐 A/B 候选

- 项目：雨灯归航
- 提出者：Assembly Agent
- 时间：2026-10-01 08:41:07
- 性质：提案（非 canonical；顺序/trim 变更需用户确认后才经 assembly.sequence_update 落 canonical）

# 雨灯归航 Phase 44C 工程预览
## 旁白与配乐 A/B 候选评审

---

## 一、渲染状态确认

| 字段 | 值 |
|------|-----|
| sequence_id | `seq_a3d5b19b` |
| render_version_id | `srender_3da0e76d` |
| 计划时长 | 89902 ms |
| 实测时长 | 89926 ms（偏差 +24 ms） |
| 状态 | ✅ done |
| 输出文件 | `projects\雨灯归航\output\sequences\seq_a3d5b19b\srender_3da0e76d.mp4` |
| audio_peak_warning | null（无 clipping） |

---

## 二、我的旁白 cue 表（na01–na05）

| Cue | 落点 (ms) | 实测时长 (ms) | 文本 | 依据 |
|-----|-----------|---------------|------|------|
| na01 | 500 | 4969 | 暴雨过后，河面上只剩下一盏被摔坏的旧铜灯。 | 场景转换：从船头紧张对峙（s01_c03）切到雨停 aftermath，点出核心道具 |
| na02 | 40000 | 4029 | 修理间的灯亮了，铜灯里的火苗也醒了。 | 关键转折：人物动机明确化——修灯不是普通修理，是唤醒 |
| na03 | 58500 | 3262 | 它捧着灯，沿石阶一圈圈向上走。 | 行为动作：仪式感的上升路径，连接修理间与塔顶 |
| na04 | 66500 | 3553 | 灯座上的铜灯亮起，光束扫过河面。 | 高潮：灯亮瞬间，视觉奇观用文字强化 |
| na05 | 82500 | 4354 | 远处的船看见了光，调转船头，往回走。 | 收尾：归航完成，呼应标题，留白不煽情 |

**切点合规性：** 所有 cue 均不跨 scene_boundary 或相邻镜边界 ±200ms。

---

## 三、A/B 试听说明

### A 候选：执行侧版本
- **旁白 cue**：nc01–nc05（执行侧已合成）
- **渲染版本**：`srender_ce68b7f0`
- **输出文件**：`projects\雨灯归航\output\sequences\seq_a3d5b19b\srender_ce68b7f0.mp4`
- **特点**：执行侧独立设计，文案风格与我版本不同

### B 候选：Agent 版本（本渲染）
- **旁白 cue**：na01–na05（本 Agent 设计）
- **渲染版本**：`srender_3da0e76d`
- **输出文件**：`projects\雨灯归航\output\sequences\seq_a3d5b19b\srender_3da0e76d.mp4`
- **特点**：克制叙事，只补画面未讲清的信息，每句 ≤40 字

**建议试听顺序：** 先 A 后 B，或先 B 后 A，对比文案风格与情绪节奏。

---

## 四、音乐素材身份声明

| 字段 | 值 |
|------|-----|
| 当前素材 | `audio_imports/music/bed_44c_v1.wav` |
| 素材来源 | 本地合成测试垫 |
| License | `self_generated_no_third_party_rights` |
| 正式音乐 | **待用户提供** |

**替换流程：** 用户确认正式音乐后，只需：
1. 上传正式音乐文件到 `audio_imports/music/`
2. 调用 `audio_plan_put` 更新 plan（换 file 路径，新 revision）
3. 重新 `render_submit` 生成新版本

---

## 五、音轨策略

| 配置项 | 值 |
|--------|-----|
| base_gain_db | -3 dB |
| music gain | -10 dB |
| narration gain | +2 dB |
| music fade_in | 3000 ms |
| music fade_out | 4000 ms |
| narration fade_in | 30 ms |
| narration fade_out | 60 ms |
| ducking | threshold=-30dB, ratio=4:1, attack=50ms, release=800ms |
| loudness target | I=-16 LUFS, TP=-2 dB, LRA=11 LU |

**策略说明：**
- 原生环境声保留（base_gain -3dB），不压制
- 旁白期间音乐自动 ducking（-30dB 阈值触发，4:1 压缩比）
- 音乐淡入淡出平滑过渡，避免突兀

---

## 六、分轨导出

| 分轨类型 | 文件路径 | SHA256 |
|----------|----------|--------|
| narration | `projects\雨灯归航\output\assembly\stems\stem_narration_rev73766c28.wav` | `da8cc199...` |
| music | `projects\雨灯归航\output\assembly\stems\stem_music_rev73766c28.wav` | `083cd877...` |

**Plan Revision:** `73766c28`

---

## 七、待用户裁定事项

1. **A vs B 候选**：哪版旁白更贴合故事气质？
2. **落点微调**：是否需要调整某句的 start_ms？
3. **音乐素材**：提供正式音乐文件或确认使用测试垫
4. **最终 select final_render**：用户显式动作，由用户决定

---

## 八、工程预览标注

⚠️ **本版本为「工程预览」**，成片定版与 final_render select 由用户完成。

渲染版本 `srender_3da0e76d` 仅供参考，不代表最终成片。

---

**提案文件**: `projects\雨灯归航\output\assembly\proposal_v4.md`
**最后更新**: 2026-10-01
