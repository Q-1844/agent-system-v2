# 32.md Video-First Phase V1 — 能力审计（§三 Capability Inventory）与实验计划

日期：2026-09-20 ｜ 分支 `feat/agent-v2-phase-d-golden-run` @ fb20b7f ｜ 审计人：Claude（只读零改动）

## 一、Capability Inventory（代码路径实证，Explore 审计 + 数据核验）

### 1. 模式入口与首帧强制

| 模式 | Provider client 层 | 生产 handler 层 | 真机验证状态 |
|---|---|---|---|
| t2v | `agnes_client.create_video(mode="t2v")`（:580-625） | **无入口**（handler 只映射 i2v/keyframes，`shots.py:1301`；preflight 硬拦无首帧 `shots.py:1217`） | ✅ 31.md 探针 s17_c03 直调成功 |
| multi | `mode="multi"` → `extra_body.image=[URL...]`（:623-624，**不带 mode 字段**） | **无入口** | ⚠️ 31.md 探针成功但**实际只传 1 张参考**（人物 look；SIV 无公网 URL 被过滤）——**双参考 multi 未验证** |
| i2v | `mode="i2v"` → 顶层 `body["image"]=URL` | ✅ 唯一单图路径（`link_mode=i2v`） | ✅ 生产主链，4 条 Golden 视频 |
| keyframes | `mode="keyframes"` → `extra_body.image=[first,last]`（<2 张拒绝） | ✅（`link_mode=keyframes`，尾帧就绪等待 180s） | ✅ s16_c02 / fixture Case C |

- **首帧强制是 handler 层 fail-closed 设计**（`_video_preflight`；BOUND `_resolve_boundary_frame` 解析不到→`reference_media_unresolvable`），非 client 限制。无首帧路径=直调 client（探针已证）。
- BOUND/UNBOUND 均 fail-closed，无模式间自动 fallback；唯一自动重试=v2.0 提交 400 含 num_frames 的降帧重试（≤3 次）。

### 2. Provider 契约（agnes_client.py:528-646）

- 默认模型 **agnes-video-v2.0**（legacy 契约）：`num_frames` 8n+1 ≤441（下限 81）、`frame_rate`、`width/height`（16:9 硬编码 1280x704）、`seed` 可传、`negative_prompt` 可传。
- 2.5 系列契约：`seconds` 字符串 4-12、**拒绝** num_frames/seed/negative/**multi/keyframes**（结构化拒绝，:544-556）。→ **本轮全部实验固定 v2.0**（multi 仅 v2.0 可用 + 与 31.md 探针可比）。
- 视频输出实测 1280x704 / 24fps；首帧图为 1312x736（2K 档），i2v 送图有 ~1% 上下裁切（FACTS §3.3 实测已知）。

### 3. 参考图资产现状（Golden_Full_Production_V1 实测）

- 人物 look：9 个，**7 个有公网 hero URL**（`cos-platform-outputs.agnes-ai.cn`），直接可用。
- 场景 SIV：34 个，**0 个有公网 URL**；25 个无 reference_asset，9 个有本地 PNG（~4.3MB 2K）。→ multi 场景参考需先经 imgbb 托管（`ImgbbHost` sha1 缓存，实验侧不改资源库，来源 asset_id+checksum 记录进实验记录）。
- 首帧：52 镜全部有已选定首帧公网 URL；尾帧：10 镜有选定尾帧公网 URL（keyframes 可测）。
- 31.md multi 探针 refs=1 的根因即 SIV url=None 被 `[u for u in refs if u]` 过滤。

### 4. 候选/溯源/接力/入口

- 产物：`VideoVersion`（prompt 快照/video_id/generation_basis）+ `final_video` 指针；BOUND 溯源随 `GenerationRequest.provenance`（provider_mode/used_reference_ids/semantic_roles）。
- Director/Producer/用户 REST/autopilot/恢复全部汇聚 `JobQueue.submit("video")` → 串行 1 RPM worker → `_handler_video`（调用链唯一）。
- 尾帧接力机制在档：ffmpeg `-sseof -0.2` 提取 → imgbb → `shot.video_end_frame`；4 条 Golden 视频均有 end_frame。本轮只做单镜，接力实验留下一阶段（§十五）。

### 5. 配额（FACTS §4，实验规模依据）

- 视频 **1 RPM 硬约束**（31.md 实测口径：串行 + 75s 间隔 + 429 自愈重试）。
- **每日生成秒数配额存在**（Token Plan 500s/天；免费档官方未公布）→ 实验按批执行，监视 4xx 配额信号；单镜时长统一 121 帧（5.04s）控制消耗。
- 本轮计划 ~63 条（批次 1-2）+ 批次 3 目标性 10-20 条 ≈ 300-400 秒视频量级；如配额截断，保留完整记录如实报告。

## 二、测试矩阵（§五 12 类别全覆盖 + 5 类已知问题形态转正式实验）

| # | Shot | 类别 | t2v | multi(look) | multi(look+scene) | i2v | keyframes | 覆盖的已知问题 |
|---|---|---|---|---|---|---|---|---|
| 1 | s09_c01 | 人物跟拍/单人远景 | 2 | 2 | 1 | 1 | | 单人克隆、EWS 景别 |
| 2 | s09_c02 | 单人全身运动 | 2 | 2 | 1 | | 1 | 动作链（下马→牵马）、首尾帧方向 |
| 3 | s09_c08 | 双人互动 CU | 2 | 2 | 1 | | | 人数=2、双人同框身份 |
| 4 | s10_c01 | 室内三人融合 | 2 | 2 | 1 | | | 人数=3、SIV 复刻副作用 |
| 5 | s10_c03 | 单人 CU 表演 | 2 | 2 | 1 | 1 | | CU 遵从（紧景别墙）、微表情 |
| 6 | s11_c06 | 简单道具交互 | 2 | 2 | 1 | | | 取物手部接触、道具出现 |
| 7 | s13_c01 | 强运镜 EWS | 2 | 1 | 1 | | | 拉远、剪影占比约束 |
| 8 | s13_c06 | 手部特写 | 1 | 1 | 1 | | 1 | 手部+刚性道具（断剑） |
| 9 | s14_c01 | 环境建立镜 | 2 | | 1 | | | 纯环境 t2v、场景参考收益 |
| 10 | s14_c04 | 风景/空镜 ECU | 2 | | | | | 无 Binding 镜 UNBOUND 事实编译 |
| 11 | s16_c05 | 状态变化（剑断） | 1 | 1 | 1 | | 1 | 物理断裂、方向反转 |
| 12 | s17_c01 | 复杂连续动作 | 2 | 2 | 1 | 1 | | 动作顺序（跌→抓箱→撬→发现） |
| 13 | s17_c03 | 静态表演（基线镜） | P0×1+P2×1 | P0×1+P2×1 | P2×1 | P0×1+P2×1 | | 基线复现 + 开头僵硬 |

- s14_c04 无 Binding（52 镜中 5 镜无 Binding 之一）→ 按 UNBOUND 事实编译，记录于每条实验。
- 其余 12 镜 Binding 全部 `readiness=READY`（驱动逐条复核并记录 binding_revision）。
- keyframes 前置检查（§六 Mode D）：运行前 Read 首尾帧图核对人物/方向/物理/中间态，矛盾则记录「测试条件不成立」不强行送模型。
- i2v 对照仅放在 4 镜（i2v 行为已在 31.md 充分表征，够用）。

## 三、提示词设计（§九 最小充分约束）

- **P0（legacy 编译，基线复现专用）**：31.md s17_c03 原编译 prompt（2420 字符，逐字节来自 `_31_integration_e2e/MANIFEST.json`）。仅用于批次 1 三条件复现。
- **P1（简洁创作意图）**：主体+动作+运镜+景别+风格行，无身份锚、无时序、无 negative。~2 句。
- **P2（P1+动作时序+必要身份锚）**：P1 基础上加起止状态/动作顺序 + 每角色一行精简外观锚（姓名+2-3 个辨识特征；不堆服装细节）。**同一镜 t2v/multi/i2v/keyframes 共用同一 P2**（消除提示词混淆变量）。无默认 negative、无 STRICTLY/MUST 堆叠。
- **P3（单变量定向约束）**：批次 3，针对已观察到的具体错误一次加一条，无效即撤回。
- 风格行（来自 ProjectVisualProfile）：`Visual production style: 2D animated image. Do not render as live-action photography or 3D rendered CGI.` 一行固定。
- multi 参考最小充分（§六 Mode B）：普通人物镜=look（每角色 1 张）；室内/环境融合镜加 SIV 1 张；特写优先人物身份。**不超过 3 张**（v2.0 多参考上限未知，保守起步；参考顺序=人物 look 在前、SIV 在后，逐条记录 role+url）。

## 四、批次与执行（§七）

| 批次 | 内容 | 数量 | 备注 |
|---|---|---|---|
| T0 冒烟 | s14_c04 t2v×1（兼批次 2 样本） | 1 | 验证驱动全链（记录/抽帧/指标） |
| 批次 1 基线复现 | s17_c03 P0 三条件（t2v/multi/i2v） | 3 | 与 31.md 既有产物对照 |
| 批次 2A | 8 个人物镜各 t2v×1+mL×1+mLS×1 | ~24 | 首轮横扫，双参考 multi 首证 |
| 批次 2B | 其余镜 + 二轮重复（2×→只在有信息量处补）+ D 模式 + P1/P2 消融 | ~30 | 依据 2A 评审自适应 |
| 批次 3 | 失败集中/潜力案例定向单变量（P3） | 10~20 | 证据驱动 |
| 评审 | 每批后：抽帧接触表 + 模型预评 + GALLERY 盲评页（模式默认隐藏可揭示）| — | 复用 _31 评审服务模式 |

- seed 策略（§八）：每镜配对 seed（该镜所有模式 run1 共用同 seed，便于配对比较）；run2 不带 seed（贴近生产随机性）。seed 值=shot_id 稳定哈希，逐条记录。
- 运行间隔 75s 串行；单条预计 3-6 分钟（提交+轮询+冷却）；60 条 ≈ 4-6 小时墙钟，分批后台执行。
- 每条记录（EXPERIMENTS.jsonl）：exp_id/批/shot/mode/prompt_style/完整 prompt/refs(role,url,来源 asset)/seed/num_frames/尺寸/提交与完成时间/video_id+task_id/provider url/本地 mp4/错误/receipt/readiness+binding_revision/ffprobe/首秒运动代理指标/评审分。

## 五、评估（§十/§十一）

- 机器辅助：每视频抽 7 帧（0.1/0.5/1.0s + 25/50/75% + 末帧）→ 接触表 PNG；首秒僵硬代理=开头两帧间平均像素差 vs 中段帧间差（比值）。**仅作证据不作结论**。
- 模型（Claude 视觉）预评：接触表逐条按 §十一 6 维打 1-5 分+严重错误标记，全部标注 `PRELIMINARY(frame-based)`。
- 用户人评：GALLERY.html（video 播放）+ 盲评开关 + FEEDBACK.json 落盘（复用 31.md 评审服务模式）。
- 运动/运镜/表演维度的最终分以人评为准；机器不能验证的明确留「人工审查待定」，不伪称自动验收。

## 六、边界（§十三）

- 不改 Binding/Pack/canonical/Production 策略；实验仅直调 client 层（与 31.md 探针同位），保持 UNBOUND 兼容行为不变。
- 实验产物全部落 `_32_video_experiments/`，不写回 Golden Project 正式 shot 状态；Golden 仅只读引用。
- 确定性系统 BUG 才做最小修复+原案例复跑+回归；当前审计未发现阻塞实验的 BUG。
