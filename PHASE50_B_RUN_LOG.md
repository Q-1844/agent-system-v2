# Phase50 B 批运行日志（过程档）

作品：写实电影短片《深水惊魂》（系统 Agent 自主命名前暂名）——克苏鲁式海怪追渔船，船长+大副两人。
执行侧：Claude（本轮同时承担 Zcode 部分）。项目：`P50_B`（photorealistic 16:9）。

## 运行序列

| run | 定义 | 结果 | 失败原因与处理 |
| --- | --- | --- | --- |
| run_d60cfe3fb6b5 | W09（P50_B_W09） | producer✓ writer✓ 故事审定✓ → **n_cd failed ×2** | ①attempt1：产出 4 characters+三视图，但无 approved Look（未走 look 管线）；②attempt2（continue 重试）：补 4 Looks（gen ready）+20 条 SIV 审查记录，被 `require_new` 的"本 attempt 新写入"判定判死（P49 §4.2 同型缺陷：重试延续语义 vs 逐 attempt 新写）→ 定义改 `allow_verified_reuse` 后停旧起新 |
| run_e786730f019b | W09r2 | producer✓ writer✓ → n_cd **success**（reuse 修复生效）→ **n_rv_cd 拒绝收场** | 执行侧资源审定：大副候选=西装商务装+多视图板三人同框、渔船候选含站立人形、海怪直立 humanoid——用 `reject` 语义希望"退回返工"，实测 **reject=停止下游无自动返工**（executor.py:647），run 终态 rejected |
| run_3698ccc77b9a | W09r3 | producer✓ → **n_sw failed** | 项目内已有 run1 写好的 5 段故事（n_rv_sw 已审定），writer 核验后零新写入 → `require_new` 判死（同一缺陷族）→ n_sw/n_sc 也改 `allow_verified_reuse` |
| run_b25f5b900c3b | W09r4（**主运行**） | 进行中 | n_sw 核验采用既有故事✓；n_cd 定点修复②③④后 success✓；n_rv_cd 审定通过（船长选用 ref_e5e8b8e0a54c）✓；n_sc 进行中 |

## 资产审定记录（n_rv_cd，执行侧代行+人评优先原则）

4 Looks 最终全部 approved（资源 JSON 实证）：
- **船长** ref_e5e8b8e0a54c（工作流 selection 通道）——写实皮肤/织物/身份最佳。
- **大副** ref_e8858d5baf12（资源 API + human review）——修复西装→工装后可用；纹身细节风险注记。
- **海怪** ref_9c0ba4bed38c（资源 API + human review）——头部触须/体表/半淹没形态锚定；SIV 的 bipedal 风格异议以人评裁定记录（视频审查层把关站姿入画）。
- **渔船** ref_87639e9db8d5（执行侧介入修复，账本 manual_intervention）——**12 轮候选全部人数污染**（资源 Agent 3 轮重试+硬负向约束均失败；SIV 全部拦住）；执行侧纯 t2i 出空船+i2i 重涂至藏青船体；SIV 判"general vessel identity 达标+细节打磨项"→人评 pass（33.md 人评优先原则），细节项留档。

## 关键系统发现（进行中累计）

1. `require_new` 完成判定与"重试延续/核验复用"语义系统性矛盾（n_cd/n_sw 连续踩中；P49 单例 → P50 复现实例 ×3）。
2. 审核 `reject` = 停止无返工；"退回返工"应通过 continue（新 attempt+失败事实反馈）或资源层定点修复。
3. 人数污染在"船+人"题材的 i2i 生成中高发（12/12）；**纯 t2i + 明确 no-people 提示词可破**（与 P49 C批 s06 无参考出写实一致）。
4. truth gate（approve 需 overall=pass 证据）+ human review 通道设计按预期工作（挡住 12 张污染候选）。


## 视频阶段（run_8af134406dde，FINAL）

- n_dir_video：20/20 视频真实提交+完成（8.0-10.1s ffprobe 实测，全原生 AAC）；导演自核回执/时长/首中尾帧后节点 success。**零 REST 补交**（Phase49 的 9/18 补交问题未复现）。
- n_rv_v1 自动通过；n_rv_v2 执行侧二审：排除 s04_c04（关键事件缺失）/s05_c03（虚构到港），裁切 s01_c02 [0,4.9s)（人物消失点 ffmpeg 实证），降级 s03_c03/s04_c02，其余 17 条整段选用。
- n_asm：装配渲染 **56.56s 含 AAC**（seq_6ee98d6e/srender_c0951729.mp4），排除/裁切全部落实（7 点抽样+2s 间隔哈希映射 29 帧全唯一复核）。
- n_rv_final：执行侧技术审定通过 → **run COMPLETED**。等待用户审美/剧情验收。

### 视频阶段计时（账本实测）
远端等待合计 8326s（均值 416s/条，min 150 max 753）；在途峰值 2、重叠 2231s；**委派空闲 2071s（12 段）→ 净提速仅 2%**——服务端并发收益被导演 LLM 委派节拍吃掉。详见 PHASE50_B_REPORT §5。

