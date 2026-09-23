# FABLE_PILOT_V1_PROGRESS — 34.md《狐狸和葡萄》Full Production Pilot

日期：2026-09-21 ｜ 阶段：34.md Fable Pilot V1 ｜ 状态：**进行中**
当前 submodule HEAD：`3832ed0`（33.md FINAL；本阶段不 checkout 到 6a07839）

---

## 一、Calibration 既定输入（§1/§2，不重新实验）

证据源（全部真实在档）：

- `_33_fox_calibration/ANIMAL_CHARACTER_CALIBRATION_V1_REPORT.md`（人评收口：best 4/ok 1/bad 10）
- `_33_fox_calibration/EXPERIMENTS.jsonl`（15/15 真机，prompt/payload/refs 逐条）
- `_33_fox_calibration/FACTS.md`（固定常量）
- `_33_fox_calibration/references/R3_cinematic.png` + provenance（i2i from R1 plate；2K 16:9）

### 提取的关键事实

1. **R3 真实构造方法**：i2i，ref=R1 白底板 provider URL；prompt=狐狸身份段（红橙毛/白吻胸/黑褐下肢/大尖耳/琥珀眼/蓬尾淡尖/左耳小缺口/无衣物）+ 「立于葡萄园土路、身后少量葡萄串、电影感暖光、全身可见、画面焦点、无其他主体、简净构图、3/4 视角」+ 风格行；size_tier=2K, ratio=16:9。
2. **固定 style line**：`Cinematic storybook animation, stylized 2D animated look. Do not render as live-action photography or 3D rendered CGI.`
3. **P1/base 信息密度实证**（胜出样本）：
   - F004（CU）：动作+camera 两句（349 chars 含风格行）
   - F008（走）：动作+camera（319 chars）
   - F012（跳，base）：动作三句+camera（344 chars）
   - F013（跳，P1）：动作两句+camera（258 chars）
   - 编译规则：R3 prompt 不含任何毛色/耳朵/尾巴/背景文字——身份全由 reference 承载。
4. **视频契约**：model=agnes-video-v2.0；num_frames=121（5.04s）；frame_rate=24；1280x704；**mode=multi（extra_body.image=单公网 URL）**；seed 逐镜配对。
5. **no-BGM 生产规则**：生成视频应无背景音乐、须含脚步等现场音效；BGM/旁白后期添加（33.md 用户架构裁定）。
6. **F013 为何可用**：P1 最小动作意图 + R3 参考 → 做出了最难画面（跳跃+葡萄正常掉落物理）；验证「约束来自参考与镜头设计，不来自 prompt 加码」。
7. **F015 为何失败**：P3 定向约束（"The grapes remain hanging on the vine"）→ 触发葡萄整段消失（物体删除级副作用）→ **禁止 prompt escalation**。

## 二、Audio Preflight（§3，生产启动前唯一补项）

样本：F004/F008/F013 真实 MP4（`_33_fox_calibration/videos/`）。
方法：机器信号分析（`_34_fable_pilot/audio_preflight.json`；我无真实听觉，机器证据+人耳抽听页并行）。

| 样本 | 音频流 | RMS 均值 | 谱平坦度 | 音乐性谐波帧占比 | 瞬态/s |
|---|---|---|---|---|---|
| F004 (CU) | AAC 48kHz 立体声 ✓ | -46.9 dBFS | 0.41（宽带） | **0.000** | 0.0 |
| F008 (走) | AAC 48kHz 立体声 ✓ | -45.0 dBFS | 0.38（宽带） | 0.026 | 0.0 |
| F013 (跳) | AAC 48kHz 立体声 ✓ | -46.1 dBFS | 0.45（宽带） | 0.051 | 0.4 |

结论：

- **BGM**：三段均无持续谐波/音调组织（音乐性帧占比≈0），无 BGM 特征 → 符合目标。分类：生产 request 对音频零指令（C1-R 撤回 audio intent 系工程事实），provider 默认行为即无 BGM → **无需系统修正**；偶发风险留 candidate selection 兜底（PROVIDER STOCHASTIC 层，§3 归类）。
- **现场声**：三段均有低电平持续环境声（非静音帧占比 94-97%）。F013 有 ~0.4 瞬态/s（落地候选）。F008 走步镜 6dB 阈值下未见脚步瞬态——**脚步在场性机器不可确证，留人耳**。
- 人耳抽听页：`_34_fable_pilot/AUDIO_PREFLIGHT_LISTEN.html`（3 条内嵌播放）。
- 不新建 SFX Engine（§3 禁令遵守）。

## 三、生产系统架构审计（§13「先检查当前系统具体支持什么」）

HEAD `3832ed0` 生产视频链真实能力：

| 层 | 真实状态 |
|---|---|
| `agnes_client.create_video` | 支持 t2v/i2v/keyframes/**multi**（extra_body.image 数组） |
| `agnes_adapter.compile_agnes_video_call` | 接线前只有 i2v/keyframes 槽位 |
| `video_adapter.build_bound_video_input` | BOUND：boundary frames → i2v/keyframes |
| `routers/shots.py _handler_video` | mode= link_mode 二值映射；首帧硬前置 |
| `LinkMode` enum | keyframes/i2v/none（**无 multi**）；Director 白名单 i2v/keyframes |

**发现（34.md §27 System blocker 类型）**：正式 pipeline 无法正确消费 R3——R3 是视频语义参考而非首帧（首帧 conditioning=构图锁死+泄漏，31/32.md 实证 i2v 首帧冻结），而生产链无 multi 槽位。32.md 生产边界图谱与 31.md §14 用户裁定（multi 直生主力/生图降辅助）均为冻结结论。

**最小修复（§5 授权；经既有 GenerationRequest seam，Binding/Pack 零改动）**：

1. `models.py`：LinkMode.MULTI="multi"（注释：仅 BOUND 可用）
2. `structure_service.py` / `director.py`：link_mode 更新白名单 +multi
3. `video_adapter.py`：multi 分支——BOUND gate 不变；参考确定性选择（look hero primary → 任一 look → SIV 场景回退）；公网 URL-only（视频无 data URI 槽）；诚实 diagnostics；GenerationRequest 投影 video_reference（不冒充 first frame）
4. `agnes_adapter.py`：multi 编译——恰好 1 参考结构化校验（多/缺/URI 不合法全拒绝）
5. `prompt_builder.py`：multi 分支——动作句独立成段（无 "Animate this image:"），不追加 consistency 模板（身份由参考承载，密度对齐 P1/base）
6. `routers/shots.py`：mode 映射+preflight（multi 不要求首帧）+槽位解包+kwargs；**UNBOUND multi fail closed**
7. `native_av_pack.py`：freshness 读模型 mode 映射补 multi

测试：`tests/test_video_multi_mode_34.py` 9 项全过；全量回归 **2284 passed**（2 项既有键集穷举断言更新为含新字段，非行为回归）。
提交：submodule `e44b90b`；父仓库指针 `b37942a`。

## 四、Pilot Project

- 项目名：`Fable_Pilot_Fox_and_Grapes_V1`（正式 API 创建 ✓；setup-only：VisualProfile 初始选择（2d_animation + calibration 语义风格行 + 3 条 negative constraints）+ size_tier=2K，与 Phase D §34 同口径授权）
- Producer 为主入口（§5），zero direct canonical writes
- 故事规格：§6 经典八拍结构，1 狐/1 园/1 葡萄，无第二动物/人类/战斗/复杂抓取
- 8 镜：Establishing / Enter / Discovery CU / Prepare / Jump / Reaction / Leave / Final
- Producer Turn 1（p1_brief）：故事+角色+场景生产授权，进行中

## 五、Video 生产记录（§14/§15/§13 报告字段）

每镜 2 candidates（同 truth/同参考/同风格/同密度/同 intent；seed=None→provider 随机=真实随机变异）；备用 candidate 3 仅当人评后 A/B 全废（same-recipe）。

**生产前最终状态（dry-run 全过）**：

| Shot | link_mode | 参考槽 | 参考资产 | 备注 |
|---|---|---|---|---|
| s01_c01 Establishing | multi | scene_instance_view | ref_5a3af74c4a9c（重生成后 provider URL） | cast 清空，scene-only binding（§5 修复后） |
| s02_c01 Fox Enters | multi | character_look | ref_bcfeac7ed0d9（**校准 R3**，cover） | |
| s03_c01 Discovery CU | multi | character_look | ref_bcfeac7ed0d9 | motion 已按发现 CU 配方修订 |
| s04_c01 Prepare | multi | character_look | ref_bcfeac7ed0d9 | |
| s05_c01 The Jump | multi | character_look | ref_bcfeac7ed0d9 | |
| s06_c01 Reaction | multi | character_look | ref_bcfeac7ed0d9 | |
| s07_c01 Leave | multi | character_look | ref_bcfeac7ed0d9 | dialogue 已清空（口型依赖解除） |
| s08_c01 Final | multi | scene_instance_view | ref_4c2b0ba977d3（provider URL） | cast 清空 |

- 提示词密度终态：808-1040 字符（初版 1372-1443，经 pace 英文化/action_sequence 清空/look 描述缩短后收窄；残留中文仅"狐狸/葡萄园"专名标签）
- 运维：视频 job 状态轮询走 JobQueue（/api/project/{p}/jobs 的 state 字段），不是 /api/task/{id}
- 首镜（s01_c01 candidate 1，vv_caa39fb6）：5.04s、1280x704、AAC 音频在场、2D storybook 葡萄园、无狐静机位 ✓（contact sheet 目检）

（16+1 条 candidates 逐条数据在 `candidates.jsonl`；s01 因 driver 早期竞态产生第 3 个版本，列为 Candidate C）

### 候选矩阵（§14/§15 机器侧）

全部 17 条：5.04s / 1280x704 / h264 / AAC 48kHz 立体声音频在场 / mode=multi / 生成零失败。
磁盘覆盖问题：VideoVersion.local 按镜同路径覆盖——A 候选文件已从 provider URL 全量恢复到 `recovered/`（17/17，字节数各异证实为不同生成）。

音频机器初筛（`candidates_audio_check.json`；musical=音乐性谐波帧占比）：

- 无 BGM 特征（musical<0.15）：s01_B/s01_C、s02_A/B、s03_A/B、s04_A、s05_A/B、s06_B、s07_A/B、s08_A/B
- ⚠️ 标记待人耳：s01_A（0.333）、s04_B（0.224）、s06_A（0.365）——可能为 BGM，也可能为环境音调（机器不能裁决）
- 现场声在场：全部候选有持续环境声底；s05_A/B 有 0.4-0.8 onsets/s（落地/动作声候选）；s08_A 有 2.37 onsets/s（风叶声？）

关键镜机器注记（contact sheet 在 `_34_fable_pilot/`）：

- s02（入镜）：四足行走 ✓、身份稳定 ✓、镜头推入略过头（终帧近特写，超出 wide 意图）
- s05（跳跃）：A/B 均完成蓄力→腾空→落地全动作、四足解剖正常 ✓；**顶点嘴部接近/接触葡萄**（33.md 已知顶点触葡萄条件化限制复现）；B 末段疑似葡萄脱落红点
- s01/s08（空镜）：场景一致、无狐 ✓

## 六B、V1B 方向（Human Gate 用户提出，进行中）

用户观察（机器证实）：8 狐镜首帧逐像素级相同（R3 参考强条件化开场）→ 同一身份多姿态参考库、按镜起始状态选用。

已落地：

- `derive_look_reference`（commit 9e3d921）：从 approved 参考 i2i 派生姿态变体的正式能力（两阶段协议；产物入候选不自动批准）
- `Shot.video_reference_asset_id` 按镜参考选择（adapter 资格校验 + 进 creative freshness 指纹）
- 6 变体生成 + vision 审查（identity 结论）：walk_side ✓ / crouch_ready ✓ / sit_look_up ✓ / alert_gaze ✓ / turned_away ⚠️ / stand_front ✗（正面视角身份漂移）
- `REFERENCE_SELECT.html` 选择页（含审查徽标）→ 等用户按镜选择 → 按选择重生成

机器审查徽标只作参考——用户选择+人评记录是最高证据层（与 R3 cover 同机制）。

## 六、Human Gate（§18）——已到达，等待用户逐镜评价

**生产已停止**（8 镜第一轮 candidates + Gallery + 机器证据全部就绪）。未自动选择 winner、未自动 regenerate、未开始下一部作品。

交付物：

- `FABLE_PILOT_V1_GALLERY.html`（Shot 1→8 排列；每候选播放器/时长/尺寸/音频/provenance/实际 prompt/人评按钮）
- `FABLE_PILOT_V1_PROGRESS.md`（本文件）
- `candidates.jsonl` + `candidates_audio_check.json`（逐条 provenance 与机器初筛）
- `recovered/*.mp4`（17 条候选视频独立文件）

等待用户：逐镜 ⭐best / ✅usable / ❌reject + 备注。之后按 §19-§23 执行（winner 固化/有限 regenerate/selection/continuity review/assembly）。

## 七、失败与归因（滚动）

| # | 发现 | 归因 | 处置 |
|---|---|---|---|
| 1 | 狐狸 Look 首批 4 图全为双足直立/拟人化站姿+1 张 3D 漂移（§8 违反）；agent 文本描述含 "strictly quadrupedal" 仍被压过 | AGENT/CREATIVE→MODEL：生成模型对"角色设计"语感框架下的狐狸有**双足直立吸引子**；文本姿态约束不可靠 | turn 2 反馈动物学配方 |
| 2 | turn 3 用动物学配方 t2i 重试 5+ 次仍全直立（蹲坐/双脚站立）；agent 上报"模型固有限制"并称找不到 R3 本地文件 | MODEL（t2i 采样）+AGENT（检索失败：R3 在仓库根 `_33_fox_calibration/` 非项目目录；且公网 provider URL 在 provenance 里可直接用，无需本地文件） | **turn 4：以校准 R3 provider URL 为 i2i 基底构造性派生**（§9 明文允许并推荐的路径）；比对证据：calibration R1 t2i seed=777 一次即四足——同配方不同 seed 落点，非"固有限制" |
| 2b | **根因链闭合（turn 4 代码审计）**：`generate_look_candidates` 是从角色三视图 i2i 派生（write_service §12 fail-closed 用 character_reference_snapshot）→ i2i 强保姿态 → 直立 front 视图 × 5 次 look 重试全继承直立。真正的 t2i 采样全局只发生过 1 次（turn-1 front，直立）vs calibration 1 次（seed 777，四足）。**Agent 工具面系统缺口**：generate_views/look.prepare/REST 角色端点均无 seed 参数；无任意-ref i2i 工具；`register_reference_asset` 有正式 REST（routers/resources.py:712）但无 look 侧 attach 通路 | SYSTEM（工具面缺口）+ AGENT（front 视图采样） | **turn 5（Path B）**：description 换 calibration 原文配方 → 重生成三视图（front t2i 重试≤3）→ look i2i 自然四足 → 批准。备选 Path C：REST 导入 R3+补 attach 链（若 B 失败） |
| 3 | 镜头修订完成后 3 处待修：s03 偏离发现 CU 规格（爪伸向葡萄+眼在边缘→应为抬头/耳动/眼神聚焦）、s07 "tail拖地" 中英混杂、s01/s08 cast=狐狸但画面无狐（multi 参考槽会错选狐狸参考） | AGENT（Director 执行偏差） | turn 5 处理（fox 批准后）：s03/s07 字段修订+s01/s08 cast 清空+8 镜 binding |
| 4 | Producer 优先级漂移：多目标任务下跳过最高优先级（狐狸修正）去批量重生成首帧（错误狐狸的浪费功）；两次需协作式取消重聚焦 | AGENT（Producer 任务排序） | 单目标任务化（每轮一件事） |
| 5 | 葡萄园场景参考质量目检通过：2D storybook、藤架/葡萄串/土路/暖光与 calibration 场景事实一致；1 Location ✓ + 8 SceneInstance（每故事段一个）符合 §10；3 个位置视图+8 个 SCIV approved 且资产注册完整 | — | — |

## 八、运维事实

- 后端：`api_server.py` :8000（detached，日志 `_34_fable_pilot/backend_launch.log`）
- 配额：Token Plan video 500s/天（本阶段预计 ≤110s）；images 4000/天
- 视频串行 1 RPM

## 九、尾帧接力漂移数据扩充实验（REPORT FINAL 后续，2026-09-23，用户裁定继续测试接力）——4/6 收口

**背景**：REPORT §29.20 判定 n=1 不足以回答「逐代漂移能否解决」→ 用户裁定同链根独立重跑接力链扩数据；分组迭代循环（每轮每镜 1 条 → 用户逐镜建议 → 再继续）。

**数据（MAE 灰度 128，与报告同刻度）**：

| 衔接链接 | v1c_fallback（无接力对照*） | v1c_relay1（09-23 00:19） | v1c_relay2（09-23 13:xx，本轮 4/6） |
|---|---|---|---|
| s02←s01 根 | 57.8 | 4.2 | 4.4 |
| s03←s02 | 49.3 | 27.7 | 25.4 |
| s04←s03 | 49.7 | 3.5 | 3.6 |
| s05←s04 | 37.7 | 8.5 | 6.8 |
| s06←s05 | 18.3 | 4.3 | 3.9 |
| s07←s06 | 61.4 | 3.3 | 3.3 |

\* **历史修正（本轮机器证实）**：09-22 23:48「V1C 首跑」实为 resolver bug 白跑批（六镜首帧互 diff≈1.0=静默回落 hero），重标 `v1c_fallback`，数值上构成免费的无接力对照组。

**核心观察**：relay1/relay2 逐链接高一致复现（4.2/4.4、27.7/25.4、3.5/3.6、8.5/6.8）→ **衔接能力稳定、可复现、非随机**；s03 链接天然偏高（上镜尾=行走中态）两链一致，非回落信号。用户人评：接力2 衔接最好、漂移不多、画质较无接力对照略差 → 下一轮方向=**「动作接力参考组」（动作一致+视角自由）**：方案 A=语义选库（零系统改动零 imgbb 依赖）/ 方案 B=尾帧 i2i 派生（b64→GCS，需补任意-ref i2i 工具面）。待用户启动。

**imgbb 事故与收口（用户裁定：停止重试，4/6 收口）**：
- `i.ibb.co`（取图域名）13:24 起在中国线路被域名级阻断（同 IP api.imgbb.com 200 存活；443/80 双端口瞬间 reset；全球 DNS 一致；本机+Agnes 机房双端同病）→ 11 次重试全数「拉参考图失败 400 fail_to_fetch_task」，全部发生在生成前，**零配额损失**；断点保持在 s06（checkpoint 完好）
- 解法方向（已交用户调研）：自建图床（CF R2/OSS）/ 备用图床冗余 / i2i b64→Agnes GCS 转存（彻底去外部依赖）
- **恢复后续补只需一条命令**：`python _34_fable_pilot/_34_chain_run.py v1c_r3`（checkpoint 自动跳过 s02–s05，仅补 s06/s07；之后跑 `_34_chain_analysis.py v1c_relay2` + `_34_build_chain_gallery.py` 重建）
- 评审站 :8779 独立进程常驻（脱离会话，防内存压力误杀）；`FABLE_PILOT_V1C_CHAIN_COMPARE.html` 三列对比可评审

**产物**：`_34_chain_run.py`（断点续跑+金丝雀+逐镜量化）/ `_34_chain_analysis.py`（历史整备+统一量化）/ `_34_build_chain_gallery.py` / `chain_metrics.json` / 三列对比 Gallery / per-run 视频存档（recovered_v1c/{v1c_fallback,v1c_relay1,v1c_relay2}）。

### §九 收口补记（同日 23:45）——6/6 补齐 + Pages 图床实证

- **imgbb 故障根因确诊（只读诊断）**：i.ibb.co 在中国线路被 SNI 级域名阻断（同 IP 换 SNI 即通、TLS 握手被杀、任意对象全灭、上传 key 亦被拒），非宕机非我方回归；Agnes 侧自述抓图 read timeout=30s。
- **替代图床落地**：`Q-1844/agnes-frame-host` 公开仓库 + GitHub Pages（`<user>.github.io`）。实测：raw.githubusercontent.com 仅 2/10 可达（弃用）；`github.io` 路由健康；尾帧 JPEG q88（168KB，PNG 的 1/6——PNG 1MB 在晚高峰吞吐下会撞 Agnes 30s 抓图超时，JPEG 5 连验收全过）。
- **s06/s07 补齐流程（新图床首证）**：抽末帧→JPEG→push→轮询 Pages 200→`_34_set_relay_url.py` 手术改接力参考 URL（store.update_shot 原子写，freshness 只看 version 不受影响）→ 单镜补跑。s06 生成成功=Agnes 服务端实际抓取 Pages URL 实证。
- **最终 6/6**：[4.4, 25.4, 3.6, 6.8, 3.9, 3.3]——与 relay1 逐链接最大偏差 1.7，衔接复现性铁证。
- **系统固化（下一轮，待用户确认范围）**：ImageHost 家族新增 github_pages kind（~60 行+配置+回归）或驱动内置「抽帧→JPEG→上传→轮询」；imgbb 退役。
