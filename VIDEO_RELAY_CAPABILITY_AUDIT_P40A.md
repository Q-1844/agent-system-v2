# 视频接力能力只读审计报告（Crane_Lake_P40A / agnes-video-v2.0）

日期：2026-09-27。范围：只读审计，未改代码、未改 Skill、未发起任何付费生成。
项目更正：用户所述 s02_c01/s02_c02/s03_c01 资产实际位于 `projects/Crane_Lake_P40A/`（P39D 无 s03_c01，仅 6 镜）。P39D 数据在附录 A 略述。

---

## 1. 生产实际调用的模型 / 版本 / 适配器 / 模式

| 项 | 实际值 | 证据 |
|---|---|---|
| 视频模型（默认/生产） | `agnes-video-v2.0`（legacy 契约） | config/config.yaml `agnes.models.video`；P40A 三镜全部视频 URL 路径 `cos-platform-outputs.agnes-ai.cn/videos/agnes-video-v2.0/...`，video_id base64 解码 `model_id:agnes-video-v2.0`（project.json 实测） |
| 视频模型（备用/新契约） | `agnes-video-2.5-flash`（mode text/keyframe + seconds 4-12s，720P） | config.yaml `video_25`；src/api/agnes_client.py:531-568；_video25_probe/ 实测记录（v25.try*.error.json / smoke.log）。**生产未用过**（全部项目 grep 0 命中） |
| 图片模型 | `agnes-image-2.1-flash`（image_provider=agnes 时） | config.yaml `agnes.models.image`；agnes_client.py:473-490（`models["image"]`）；P40A first_frame batch generation_basis.image_provider="agnes" |
| 文本模型 | `agnes-2.5-flash`（主）；`sensenova-6.8-flash-lite`（手动切换） | config.yaml；review_records.jsonl 审查模型=agnes-2.5-flash |
| 视频适配器链 | `BoundVideoRequest`(src/generation/video_adapter.py) → `GenerationRequest`(src/generation/request.py) → `compile_agnes_video_call`(src/generation/agnes_adapter.py:88-142) → `AgnesClient.create_video`(src/api/agnes_client.py:570-653)。UNBOUND legacy 直调（routers/shots.py:1595-1643） | 31.md C1 seam |
| 生产模式（三镜） | `link_mode=multi` + `video_reference_asset_id=prev_tail`（组内接续=尾帧接力）；组首 s02_c01/s03_c01 由 Phase40 Canary 自主从 i2v 残留改配为 multi+BOUND | project.json；PHASE40_SYSTEM_AGENT_CANARY_REPORT.md:23 |
| Receipt 缺口 | `VideoVersion.model` 字段=null（只有显式传 video_model 才落库，routers/shots.py:1629-1653、1708）；真实模型只能靠 URL 路径/video_id 解码佐证 | project.json 实测 model=null |

## 2. 名称 → Provider 请求编译对照

全部走 `POST /v1/videos`（agnes_client.py:596/633）。

| 本系统名称 | provider_mode | v2.0 实际请求体 | 证据 |
|---|---|---|---|
| i2v（首帧硬钉） | i2v | `{model, prompt, num_frames, frame_rate, width, height, image:"<公网URL>"}`——顶层单图槽 | agnes_client.py:616-618；FACTS §3.2 B |
| keyframes（双端钉死/桥） | keyframes | 同上 + `extra_body:{image:[first,last], mode:"keyframes"}`（≥2 张） | agnes_client.py:619-622；agnes_adapter.py:130-137 |
| multi（单语义参考直生） | multi | 同上 + `extra_body:{image:[单张]}`（不带 mode）——服务端按默认 ti2vid 处理，**最多 1 张**（2 张 400：`ti2vid supports at most 1 image`，32.md X005 真机勘误） | agnes_client.py:623-624；agnes_adapter.py:110-129；FACTS §3.2 D |
| 尾帧硬接（inherit_previous） | i2v（首槽） | 首槽 = 来源镜头真实视频末帧 URL（`_effective_relay_first_frame`，video_adapter.py:122-132）；无 last-frame-only 模式 | video_adapter.py:9-13 |
| 尾帧接力（组内 multi relay） | multi | 单参考 = 影片顺序上一镜真实末帧（`resolve_multi_relay_source`，按全片展平序，video_adapter.py:143-168）；**Look 不进请求** | video_adapter.py:294-325 |
| keyframes 桥（bridge_create） | keyframes | 首槽=锚点真实尾帧（opening_source=inherit_previous）、尾槽=后继镜实际首帧（提取+托管）；≥10s、≥3 中间节拍、seed 后禁 refresh | src/storyboard/bridge_service.py:136-151；director_bridge.py:113-156 |
| reference（2.5 契约） | — | 2.5 契约 mode 枚举含 `reference`，但本地 `_video25_mode` 只映射 t2v→text、i2v→keyframe，其余 raise；多图 reference 字段形状"未验证（勿发）" | agnes_client.py:544-568；FACTS §3.0 |

## 3. 能否直接向视频传 Look + 尾帧两张参考图？

**不能。** 分层定责：
- **v2.0（生产模型）：服务端/模型层不支持。** multi 形态实测 2 张即 400（X005）；i2v 只有顶层单图槽；keyframes 的双图是首尾边界槽不是参考槽。这不是适配器问题——本地编译器与 server 行为一致。
- **2.5（未用）：适配器尚未实现 + 契约未验证。** 契约 mode 枚举有 `reference`，但 `_video25_mode` 显式拒绝（agnes_client.py:554-556），多图形状无实测（FACTS §3.0"未验证勿发"）。
- **唯一双参考可执行层 = 图片生成（i2i）**：`generate_image` 的 `image_refs` 列表（≤6 张，MAX_PROVIDER_INPUT_IMAGES，image_adapter.py:500）可同时装 Look + 尾帧。这正是 reference_previous 首帧合成路径（§5）。

## 4. 真实尾帧设为首帧 + Look 独立参考？

**当前 Provider 上不能同时成立：**
- v2.0 i2v：1 个图片槽。给首帧就没有独立参考槽——**模型/服务端不支持**（同 §3 证据）。
- multi：单参考与 boundary 槽互斥——`compile_agnes_video_call` 对 first/last 与 video_reference 并存直接 `multi_slots_conflict` 结构化拒绝（agnes_adapter.py:110-115）；尾帧接力时参考槽被尾帧占用，Look 出局。
- keyframes：首尾双槽都是边界帧，无第三槽。
- **2.5 mode=reference 属"适配器尚未实现"**（且形状未验证）；v2.0 属"模型不支持"。
- **当前代码可执行的等价路径**：先用图片 i2i 把 Look + 尾帧合成一张新首帧（reference_previous BOUND 路径，image_adapter.py:816-830），再把该首帧喂 i2v（或作为 multi 单参考）。Look 身份经合成首帧间接进入视频。

## 5. opening_source=reference_previous 全链路

**是——确实读取"已批准 Look + 指定镜头真实尾帧"并生成一张新首帧（i2i）。**
- BOUND 路径（image_adapter.py:768-854）：`opening_source==reference_previous && source_shot_id` → 取 `prev.video_end_frame`（url 或 local→data URI，:816-824）追加到 image_refs 尾部（ref_layout=continuity_tail），prompt 追加 continuity 子句"保持人物/环境/服装/光向连续，允许换景别机位，不逐像素复制"（:826-830）。Look 经 pack `character_look` 参考（temporal 过滤后每角色 1 张，D2 预算层）进入 refs。
- Legacy 路径（routers/shots.py:412-440, 668-672）：同语义（continuity ref + 子句）。
- **约束**：尾帧 local 即可（base64 可作图片参考，continuity_status :482-484）；但 `continuity_status` 要求来源镜头同场景（:469-470）——跨段 reference_previous 会被读模型标 broken，而 BOUND 编译器本身无此检查（语义不一致点）。
- Receipt：批次落库 `generation_basis`（binding_revision/resolved SIV/scene_reference_mode/human_reference_mode/**ref_layout/sent_ref_sequence/used_reference_ids**/image_provider，routers/shots.py:712-729）+ prompt_snapshot 含 continuity 子句；`ReferenceInput(source_type="continuity_previous_end")` 只在 ephemeral GenerationRequest.provenance，不落 canonical。视频侧 receipt = `VideoVersion.input_visuals`（39D，hard_start_boundary/relay_start_boundary/reference_only 角色清单，routers/shots.py:1398-1442）。
- 真实用例：`AGNES_完整验收_A` 8 镜 reference_previous（旧 schema，generation_basis={} 无 provenance——老批次）。P40A 三镜均未用 reference_previous（全走 multi relay）。
- ⚠️ 39E 槽同步（routers/shots.py:533-594）：视频完成后若 input_visuals 为 relay_start_boundary，把首帧槽同步为上镜尾帧并 mark_done（"实际首帧=上镜真实尾帧"）。副作用：multi 镜的首帧槽从此 = 漂移尾帧，若该镜后续改 i2v 会直接复用漂移图。

## 6. s02_c01 → s02_c02 → s03_c01 资产链（P40A 实况）

| 项 | s02_c01 | s02_c02 | s03_c01 |
|---|---|---|---|
| shot_size | extreme wide shot（**远景**） | medium shot | medium close-up |
| link_mode / 接力 | multi / prev_tail，参考=s01_c02 尾帧（跨组接力） | multi / prev_tail，参考=**s02_c01 尾帧** | multi / prev_tail，参考=**s02_c02 旧版尾帧（vv_e716ddfe）** |
| final_video | vv_97699959（8s, done） | vv_11e01881（8s, done；旧版 vv_e716ddfe 仍在案） | vv_f4112813（8s, done） |
| 视频模型 | agnes-video-v2.0（URL+解码） | 同左 | 同左 |
| 首帧槽 | ff_05f730c5_1（BOUND 重生成，status=**pending** 未批） | cand_ffsync_a2ff33（=s02_c01 尾帧，done） | cand_ffsync_8786e8（=s02_c02 旧尾帧，done） |
| 实际视频视觉输入 | input_visuals=[relay_start_boundary ← s01_c02 尾帧 vv_fff51dbf] | 同左语义 ← s02_c01 尾帧 bd5c0f8a7247dbd2.jpg | ← s02_c02 **旧**尾帧 f568d9120cf68b40.jpg（**过期依赖**：s02_c02 已重生成 vv_11e01881，新尾帧 333e9c3abd5150f5.jpg） |
| 首帧图片批次 refs | sent=[scene_instance_view, character_look]（Look 进了首帧图） | sent=[scene_instance_view, character_look] | **sent_ref_sequence=[]**（MCU→auto=context_only+human drop，零参考生成） |
| binding | scin_64690c4eaad3 / look_aa18634a3553 / rev 3cbad0ef | 同左 / rev f28fc0dc | scin_c3032a178fa6 / 同 look / rev 8962ce09，SIV sciv_96882e7dc2f4 已 approve（ref_e9732a7fe99d） |

**目检结论（本审计直接看图）：**
- **s02_c01 尾帧 = 远景**：湖面镜面反光占 2/3 画面，鹤是中上部小剪影（约画面 5%），翼展姿态可读、身份特征（黑颈/红冠）完全不可读。构图与 expected_tail 相符，**不是"画错"，是"远景天然不携带身份信息"**——用它做 multi 单参考，身份必然由模型脑补。
- **s02_c02 尾帧（新旧两版目检）**：白鸟红头浮在水面带涟漪，颈全红、无黑颈、体态似天鹅浴水——与批准 Look（黑颈红冠丹顶鹤，look_aa18634a3553/ref_310e243e4f14）**身份已漂移**。用户观察成立。
- **s03_c01 实际接力输入 = 上述漂移尾帧的旧版**（已抓取图床原件目检：白鸟红头浮水、双翼摊开）；其产出尾帧为"红颈展翅鸟"——漂移继续放大。s03_c01 形象不合格成立，且根因是"接力链上没有一环重新注入 Look"。
- 讽刺点：s03_c01 的 BOUND 首帧候选 ff_7bbf9e12_1（零参考、纯 prompt 生成）反而是合格丹顶鹤（黑颈红冠）——但 **multi 模式不消费首帧槽**，该候选从未进入视频请求。
- 批准状态：Look approved（rev_d3833bf1fac6 verified=true）；s02/s03 SIV 均 approved；三镜 locked=false，无 shot 级 approve/lock 记录；首帧"批准"仅体现为 ffsync 候选 done。s02_c01 选中候选未 mark_done。
- Receipt 完整性缺口：ffsync 候选 local 指向 `frames/s02_c02_video_end.jpg`——重生成后同路径被新尾帧字节覆盖，local 与记录 URL 不再对应（URL 才是权威）；ffsync prompt 括号里的版本号是本镜新视频版本而非尾帧来源版本（三处一致，模板措辞问题）。

## 7. Skill 规则盘点与冲突

规则来源：`src/agents/skill_system/builtin/full-story-relay-calibration/SKILL.md`、`keyframes-bridge-repair/SKILL.md`（39F 定版）。

一致的部分：桥=双端必须保留+业务连续过渡（intentional cut/新组/跳转默认不插桥）；双端证据才插桥；10s+、≥3 节拍、seed 后零 refresh、bridge_frame_check 落档；级联检查；MAE 是证据不是裁定。用户"端点不合格暂不桥接"与 39F 桥前提一致。

**冲突/缺口清单：**
1. **组首接力 vs 代码无组概念**：Skill："新组首镜用新参考图/新首帧图，不用上组尾帧作边界"（relay SKILL.md:104-105）。代码：`resolve_multi_relay_source` 只看 `video_reference_asset_id=="prev_tail"`，按全片展平序找上一镜，无组/段检查（video_adapter.py:143-168）→ 组首镜带 prev_tail 残留就接上组尾帧（P40A s02_c01 实证，39E:112 自己承认"组首镜 i2v 残留"问题）。
2. **"作参考优先" vs 首帧槽同步**：Skill 输入角色分类说"作参考优先——不钉死画面"（relay SKILL.md:39-44）；39E 槽同步却把接力参考写进 first_frame 槽并 mark_done 为"实际首帧"（routers/shots.py:535-558）——multi 下它语义是"参考"，落库后却可被后续 i2v 当真首帧复用（漂移固化风险）。
3. **同场景约束 vs 跨段接力常态**：continuity_status 强制同场景（routers/shots.py:469-470），而 multi prev_tail relay 明确按全片展平序跨段找上一镜（"跨场景衔接是常态"，video_adapter.py:147-151）——两套接力机制口径不一致。
4. **组首镜图 vs multi 参考槽**：Skill 说"组首镜的图作参考优先"，但组首镜走 multi relay 时参考槽被上组尾帧占用，本镜生成的合格首帧图（如 s03_c01 ff_7bbf9e12_1）根本进不了视频请求——Skill 语义与槽位编译无通路。
5. **Canary 记录的决策缺口**（不扣分已成档）：下游断缝升级时未给"顺延重生成"第三选项（PHASE40:56）。

## 8. 能力表

| # | 路径 | 概念上可选 | 当前代码可执行 | Provider 接收的字段 | 能否约束真实首帧 | 是否重新注入 Look | 证据位置 |
|---|---|---|---|---|---|---|---|
| A | i2v 首帧硬钉 | 是 | 是 | v2.0 顶层 `image`（1 张，公网 URL）+ prompt | **是**（像素锚） | 否（Look 只进 prompt 文本/生产上下文） | agnes_client.py:616-618; video_adapter.py:331-347 |
| B | keyframes 双端钉 | 是 | 是（v2.0） | `extra_body.image=[first,last]`+mode:keyframes | 是（首尾双钉） | 否（双帧须先经图片生成合成 Look） | agnes_client.py:619-622; agnes_adapter.py:130-137 |
| C | multi 尾帧接力（现生产路径） | 是 | 是 | `extra_body.image=[上镜尾帧]`（1 张） | **否**（纯语义参考，构图可漂移） | **否**（参考槽被尾帧占用） | video_adapter.py:294-325; FACTS §3.2 D/X005 |
| D | multi Look 直生 | 是 | 是 | `extra_body.image=[look]`（1 张） | 否 | 是（单参考=look primary） | video_adapter.py:219-260; agnes_adapter.py:116-129 |
| E | 视频双参考（Look+尾帧） | 是 | **否** | —（v2.0 无 2 图槽） | — | — | 32.md X005（400 实证）; agnes_adapter.py:116-123 |
| F | 尾帧为首帧+Look 独立参考 | 是 | **否** | —（v2.0 i2v 单槽；multi 槽互斥） | — | — | agnes_adapter.py:104-115; agnes_client.py:616-618 |
| G | 2.5 keyframe（新契约 i2v） | 是 | 是（显式 model=agnes-video-2.5-flash + mode i2v） | `{mode:"keyframe", image:URL, seconds:"4-12"}` | 是（首帧锚，720P） | 否 | agnes_client.py:585-602; FACTS §3.0 |
| H | 2.5 reference 多参考 | 是（契约枚举） | **否（适配器未实现+形状未验证）** | —（本地 raise） | — | — | agnes_client.py:544-568; FACTS §3.0 |
| I | 图片 i2i 合成首帧（Look+尾帧）→ 再 i2v | 是 | **是（唯一双参考通路）** | 图片接口 `extra_body.image`（≤6 张，URL/dataURI） | 首帧图=合成结果；视频首帧=该图（走 A/G） | 是（Look 进 refs） | image_adapter.py:812-830, 500; routers/shots.py:655-672 |
| J | keyframes 桥（bridge_create） | 是 | 是 | 同 B；首=锚点尾帧，尾=后继首帧 | 是（双端构造闭合） | 否（间接经首/尾帧图） | bridge_service.py:136-151 |

## 9. 最小实验方案（≤2 次视频生成；本阶段不执行）

目标：验证"身份回正"的两条当前可执行通路。共 2 次视频（8s×2）、约 4-6 张图片。

**实验 1（组首身份回正）：s03_c01 换 i2v + 新首帧**
1. 为 s03_c01 重新生成分镜首帧（BOUND，显式 `scene_reference_mode=full` 或 `human_reference_mode=keep/crop`——**必须覆盖 auto**，否则 MCU→context_only+drop 又是零参考），refs=[SIV 场景图, approved Look]；候选挑合格者 select+mark_done。
2. s03_c01 link_mode 改 i2v、opening_source=independent（避免槽内 ffsync 漂移尾帧被复用——当前 first_frame 槽=旧漂移尾帧，**不得直接 i2v**），提交 8s 视频。
- 检查：首帧 vs Look 三要素（黑颈/红冠/黑次级飞羽与腿）；视频 0s 帧≈首帧；t≈2/4/6s 身份稳定；尾帧 vs expected_tail（双脚触水前）。
- 花费：1×8s 视频 + 2 张首帧图。

**实验 2（组内接续双参考合成）：s02_c02 走 reference_previous 合成首帧 + i2v**
1. s02_c02 设 opening_source=reference_previous、source=s02_c01（同场景，continuity_status=ready），BOUND 首帧生成 refs=[Look, s02_c01 远景尾帧]（ref_layout=continuity_tail），prompt 自动带 continuity 子句——验证"Look 身份 + 尾帧构图/动作状态"能否合成一张双约束新首帧。
2. s02_c02 link_mode 改 i2v，用该合成首帧提交 8s 视频。上游 s02_c01 不动（cascade=0）。
- 检查：新首帧是否同时保住 Look 身份与 s02_c01 结尾的俯冲/位置状态（不逐像素复制）；视频首帧=合成图；中间帧无身份跳变；尾帧落点（落水瞬间）。
- 花费：1×8s 视频 + 2 张图。

**合计**：视频 16s（今日账本已用 62.4/500s，余 437.6s，cache/quota.json 实测）；图片 4-6/4000 张；文本若干。货币成本：仓库无 Agnes 单次计价记录——**货币未确认**，按订阅配额口径扣（500s/天）。

**判读标准**：实验 2 成功 ⇒ "组内接续+身份回正"有了不插桥的标准配方（尾帧+Look 合成首帧→i2v），可写入 Skill 候补；实验 1 成功 ⇒ 组首用 Look 直生首帧+硬首帧钉死即足。两实验都失败 ⇒ 缺口确认在模型层，再评估 2.5 mode=reference 的契约验证（需先过"未验证勿发"门槛）。

## 10. 未确认清单（不补猜）
- Agnes 视频生成的货币单价（仓库无记录）。
- 2.5 契约 `mode=reference` 多图字段真实形状（官方未验证，本地勿发）。
- P39D s02_c02 的接力输入 URL（该项目 project.json 无 input_visuals 字段，仅 trace 语义可证）。
- ffsync prompt 括号版本号指向本镜版本是否有意（模板语义）。
- 本审计未逐帧播放视频 mp4（中间帧判定基于尾帧图+trace 文案；实验阶段用 visual.sample_frames 落实）。
