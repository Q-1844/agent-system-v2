# Phase48 总评、网络研究与分批决策

日期：2026-10-05。交付性质：基于用户提供的 Phase47 最终报告进行复核，以及公开一手资料研究；本文件不是仓库修改报告，也不是新视频实测报告。本轮没有启动模型生成任务，没有替用户选择成片。

## 1. 结论与下一步

**保留现有系统，先完成 A 批小修与证据收口，然后开展 B—E 批。无需重建六员体系，也无需再设计一套更复杂的工作流引擎。**

现有 canonical、ownership、OCC、receipt、冻结交接、人工审核、Sequence 和恢复机制已经形成可用的生产骨架。接下来最值得优化的是：音频装配政策、镜头起止状态规划、真实输入依赖留证、人物对白、剧情因果密度。扩大工作流节点种类、建立通用节点图或引入本地大型视频模型，均不是本轮必要任务。

用户观察到新视频没有背景音乐但保留音效，这是立即调整生产政策的重要依据；但需要用新样本确认覆盖范围，不能写成供应商已经承诺“所有视频绝不生成背景音乐”。

用户关于“观察尾帧，再生成合适的新首帧”的经验合理。现有历史融合首帧实验也提供了局部支持。然而 Phase47 四部整链影片主要采用独立图生视频和正常切镜，它们不能单独证明“尾帧接续不行”，也不能证明四部影片实际使用了尾帧再锚定。C 批将补齐这个对照。

## 2. Phase47 报告复核

以下是文档层面发现；涉及运行和源码的结论仍须 A 批回到真实仓库、数据库、回执和媒体文件核对。

| 项目 | 当前证据与问题 | 本轮处理 |
|---|---|---|
| 六员整链 | 主报告记载 D/F 四轮六员节点 success，均停在“成片审定”；`final_render=None` 不能写成用户已定版 | 保留人工定版状态，统一报告口径 |
| W00—W09 | 主报告称正式可用；目录仍将 W09 列为旧 run 进行中，证据索引仍有多项 PENDING | 依据真实运行补齐索引，不能批量把旧项改成 PASS |
| 量化统计 | 主报告指向 evidence index §5，但当前附件该节不存在；首次候选合格率缺少统一分母 | 建立可重算的候选账本，缺记录则标未知 |
| 冻结基线 | HEAD 为 `d98578312bda7a818849bb2f6ad5809bb00d9bae`；Skill 展示 hash 为截短串；text_model 是包含注释的 YAML 行 | 补全实际模型、路由、完整文件 hash、配置和工作树差异；不凭这些缺项断言发生漂移 |
| 恢复证据 | provider ID 后杀进程、特定持久化窗口和半途改 Skill 等部分证据属于机制或等价窗口 | 分开“机制回归”“直接演练”；关键缺口补做，保留旧证据等级 |
| 运行中停止 | 报告保留了 failed/stopped 标签边界；晚到结果不翻转终态已有证据 | 最小修正语义，并保留真实失败历史 |
| 资源发布 | `resource.library.publish_character` 的视图文件校验缺口已被报告承认 | 复现后堵住无文件发布路径；不能破坏 Look 复用 |
| 修复产物 | W06/W07 的 verified reuse 对“已有视频满足修复目标”仍需核对 | 核对问题、候选版本与修复目标；旧产物满足条件可复用，但不能靠任意已有视频完成修复 |
| 音频结论 | 主报告 §13 用低整段 LUFS 推导近似无声，再将 silent 和原轨保留视作等效 | 撤销等效推论；逐事件试听和音画核验，默认保留合格原生音效 |
| 尾帧接力 | 主报告称 D/F 的 `source_shot_id` 全 null，主要独立生成 | 不拿 Skill 加载或整链成功冒充接续质量；另查新首帧图像生成是否读取尾帧 |
| 场景目标 | F2 将 LocationView 当成 SceneInstanceView，定向目标修正后继续 | 模板明确目标资产类型和具体场景状态，按任务要求而非所有场景一律生成新图 |
| 回归状态 | 全量有失败，隔离复验通过；vue-tsc 有既有错误 | 如实记录，做受影响回归；不宣布全套已绿，也不把无关历史错误全部拖入本轮 |

### 两个必须澄清的技术概念

**无背景音乐 ≠ 静音。** 短暂脚步、关门、纸张、水声可以使整段综合响度很低，却是重要叙事信息。音频指标负责检测，人工或可信听音审核负责判断“是什么声音”。只看 AAC 存在、LUFS 或音量波形不能证明无音乐、无对白。

**视频的 source_shot_id 为空 ≠ 新首帧完全没有参考前镜。** 如果图像生成阶段读取了前镜尾帧，新视频仍可由一张重新构图的首帧起步。需要追溯“视频版本 → 实际尾帧 → 图像生成请求 → 新首帧 → 视频请求”的完整依赖，而不是只查一个字段。

## 3. 网上哪些做法值得借鉴

研究范围包括首尾帧生成、故事成片、分镜平台、原生多镜头与公开技术资料。优先采用官方产品帮助页、官方教程、开发者项目文档和论文。宣传演示的成功率、平台隐藏算法和用户所指但尚未提供名称的那个网站，均没有被本轮独立验证。

| 平台 / 资料 | 可以确认的公开机制 | 用途与适用判断 | 我们的转化方式 |
|---|---|---|---|
| Agnes Video Generator [S01] | 创意模式公开列出 keyframes、ti2vid、none；ti2vid 描述为前镜尾帧经图生图得到下一首帧；支持自定义或生成结束帧，也有无旁白创意示例 | 既有故事生产，也有带解说的文稿模式；不是只服务解说视频。部分文档仍使用 v2.0，不能照抄其 API | C 批对照“直接尾帧”“重建首帧”“重建首帧＋计划结束帧”；E 批保留视觉说明片类型，关闭旁白 |
| Higgsfield Cinema Studio [S02] | 先建立可用画面，再控制运动；公开说明起止帧、镜头计划及多镜头能力，不同模式有组合限制 | 叙事、产品、摄影风格均适用；不能将网页支持复制为 Agnes 支持 | 前期批准起止画面；按镜头类型选择方式，避免不合法参数组合 |
| Higgsfield 新版工程叙述 [S03] | 还公开讨论视频优先、多镜头及可复用 Elements 的路线 | 表明平台并非始终依赖逐镜首尾帧链；属开发者机制描述，不是独立质量评测 | 保留模型原生参考 / 多镜头能力的适配空间，当前只启用本地实际支持路径 |
| Google Flow [S04] | Frames to Video 接收开始或结束图片；引用资产帮助角色与场景复用；功能依赖具体模型 | 叙事和连续场景；Extension 是视频扩展机制，不能与单张尾帧续写混为一谈 | 把连续状态、画面边界和资源引用分开；只借鉴生产逻辑，不换供应商 |
| Adobe Firefly [S05] | 明确首尾图生成与运动描述，结束帧可能限制部分控制 | 转场和有确定动作终点的镜头，并不限于旁白内容 | 首尾一致性前置审核，生成后复核实际动作过程和终态 |
| LTX Studio [S06] | 图像创作、角色 / 物品资源、Storyboard、修订片段和编辑装配组成完整流程 | 可自动化的成片仍依赖分镜与候选管理；资料不支持“每镜必用首尾帧” | 复用已批准 Look/Prop/Location，局部重试，保留好的候选 |
| OpenArt Story / Storyboard [S07] | 公开提供故事类型、分镜编辑，以及将批准分镜用于视频的流程 | 有 explainer、故事和其他类型，不能从“一键”推断内部全部双端点 | E 批同时测叙事与无旁白视觉说明；不模仿默认解说音轨 |
| Keyframe [S08] | 脚本到角色、地点、镜头、视觉计划、生成和选片装配的公开路径 | 名称本身不是首尾帧 API 的证据 | 借鉴计划、素材复用与选片，而非假设其隐藏连接算法 |
| LibTV 官方 Agent 接口 [S09] | 官方 Skill 展示提交需求与素材、查询任务和取得结果，由后端规划生产 | 支持完整成片，但接口未公开足以还原全部内部镜头生成策略的内容 | 不把其成功演示推导成“首尾帧必胜”；我们继续保留真实步骤账本 |
| STAGE 研究 [S10] | 研究将镜头起止分镜与多镜头记忆结合，包含模型层方法 | 支持探索成对关键帧规划；其训练、记忆和编码机制不能由 prompt 直接复现 | 借鉴“目标状态＋跨镜头记忆”，不引入本地 GPU 或宣称实现论文性能 |

### 研究结论及证据边界

1. “先做好画面，再让模型生成运动”是可验证的常见生产方式。更精确的说法是：先批准有意义的起止状态，再选择支持该约束的生成方式。
2. **成对关键帧生成**与**上一视频尾帧逐段接力**是不同技术。前者可以提前设计本镜结束状态；后者继承实际生成结果，也继承偏差。两者可以组合，也可以分开使用。
3. 好的首尾图不是充分条件：过程可能扭曲、突然变形、提前完成动作、错过道具交接或说错人。生成后的真实首 / 中 / 尾及完整观看仍不可省。
4. 不同机位之间往往应当正常切镜。连续性更重要的是同一人、同一道具、合理位置、动作阶段和因果，而不是两张图片像素完全相同。
5. 尾帧只保留某个瞬间的外观。相同位置可以对应不同运动方向、速度和动作阶段，因此尾帧必须与前镜末段、场景状态和动作描述一起判断。这是运动信息的逻辑限制，不代表任何具体模型已经被证明不可用。
6. 最贴近用户观察的是 [S01] 公布的 ti2vid 方式；但当前能读到的是开发者公开说明，源码目录获取受限，本轮未声称逐行核验了其最新流水线源码。
7. 未找到 Agnes 官方变更记录明确保证“更新后自动去掉所有 BGM 并保留全部音效”。因此 B 批把这项作为当前模型观测来验证，而不是平台硬保证。

## 4. Agnes 当前能力边界

以 [S11] 的 `agnes-video-2.5-flash` 官方文档为公开能力依据，再由 A 批验证项目所用实际路由和适配器：

- keyframe 使用 `first_frame` / `last_frame`；reference 使用 `images` / `audios`，两组输入互斥。Flash 的公开 reference 输入上限为 5 张图片、3 段音频，不接收视频参考。
- 公开时长范围为 4—12 秒、720P；具体返回尺寸与帧率仍以真实文件为准，不把 1280×704 写成 1280×720。
- 音频参考不等于保证长期固定声线，也不等于可在 keyframe 请求里同时加入音频。角色对白需要 D 批专门验证。
- 不添加文档未支持的 `no_music`、`audio_stems`、身份槽位、参考强度或口型参数；“无配乐、无旁白”的要求先写入提示词和实际审核政策。
- 官方文档与本项目账户路由可能不同。沿用已验证的域名、认证与账户；不能为了跟随官网示例直接切换 API 入口。

## 5. 分批执行顺序与规模

| 批次 | 执行文件 | 目标 | 进入条件 | 核心产出 |
|---|---|---|---|---|
| A | PHASE48_A_BASELINE_AND_MINIMAL_REPAIR.md | 证据、基线和真实缺口收口 | 当前仓库与 Phase47 可读 | 最小修复、直接恢复演练、统一账本 |
| B | PHASE48_B_NATIVE_AUDIO_AND_ASSEMBLY_POLICY.md | 保留音效、禁止旁白、修正装配策略 | A 的身份 / 恢复 / 文件安全关口通过 | 12 条新音频样本及装配对照 |
| C | PHASE48_C_PLANNED_KEYFRAMES_COMPARATIVE_TEST.md | 四种连续生成方式受控对照 | A/B 接口与音频政策可用 | 12 条共享源视频＋48 条目标视频，按类型结论 |
| D | PHASE48_D_MULTI_CHARACTER_AND_DIALOGUE_TEST.md | 多人物、道具交接、说话与声线 | C 有可用镜头路径；B 可保留对白 | 36 条核心镜头、配对组合和问题归因 |
| E | PHASE48_E_STYLE_STORY_AND_SKILL_ACCEPTANCE.md | 三种风格、丰富剧情、视觉说明和 Skill 验收 | C/D 决策已收束 | 6 部完整片与新对话自主验收 |

数量是计划值，不是已生成或合格数量。C/D 的重复用于观察随机性，不能当大样本统计证明。每批先确认合法输入与可用资源；系统性参数错误立即修正，429/503 按实际队列等待，不能机械生成错误请求凑数。可选扩展不计入核心分母。

**执行方式：** 先把 A 文件交给 Zcode。A 完成后执行 B，再执行 C。D 在连续生成方式可用后进入；E 作为整片与 Skill 终验。现有引擎保持顺序模板，各实验由独立项目 / run 承载，不要求新增引擎分支、并行或循环能力。用户已有 token plan，本轮不以假设费用限制为由强制只做一两条；候选数量仍需与问题、重复和队列容量对应。

## 6. 全批通用规则

- 所有实验与影片均无旁白；B/C 的核心测试还无人物对白，D/E 允许画面内角色说话。无剧情解释性画外音，不用长字幕补齐失败剧情。
- 实验阶段不加 BGM，以免掩盖原生音轨问题。音效与合格原生对白应保留。后续是否另配统一音乐由作品需求决定，不是本轮默认。
- 全部文本 Agent 继续 `agnes-3.0-flash` 且启用 thinking；独立 Vision 模型沿用既有配置，不能以“统一文本模型”为由改动。
- 不自动批准、选片或定版；也不反复请求已经授予的实验授权。严格遵守已有必须由人完成的 canonical 选择和最终审定关口。
- 输出候选与 canonical 分开，固定来源版本；记录真实模型请求、provider job id、媒体 hash、选择回执、Skill load 与 hash。网页资料和计划值不是运行证据。
- “测试通过”分别报告提交、收货、视觉、音频、剧情、人工选择和最终定版；任一层不能替代其他层。
- 正式 assembly 当前不在可绑定 Skill 名册内。B 批先修改现有 assembly 角色说明、装配政策和 W08/W09 契约，不能直接宣称加载了一个 assembly Skill。

## 7. 一手资料索引

访问日期均为 2026-10-05；网页可能随产品更新。下列摘要用于机制研究，不是平台排行或采购建议。

| 编号 | 资料与链接 | 证据用途 |
|---|---|---|
| S01 | [Agnes Video Generator Features](https://github.com/lcy362/agnes-video-generator/blob/master/docs/public/features.md)、[Usage](https://github.com/lcy362/agnes-video-generator/blob/master/docs/public/usage.md)、[项目与无旁白示例说明](https://github.com/lcy362/agnes-video-generator)、[在线项目](https://video.lichuanyang.top/zh) | 三种接续方式、故事与解说两类用途；旧模型示例不能照抄 |
| S02 | [Higgsfield Cinema Studio Guide](https://higgsfield.ai/blog/cinema-studio-guide)、[Canvas](https://higgsfield.ai/canvas-intro) | 画面优先、起止帧、转场与模式限制 |
| S03 | [How We Built Cinema Studio](https://higgsfield.ai/blog/how-we-built-cinema-studio)、[How We Built Supercomputer](https://higgsfield.ai/blog/how-we-built-supercomputer) | 可复用资源与视频优先路线，标为开发者叙述 |
| S04 | [Flow Create Videos](https://support.google.com/flow/answer/16353334?co=GENIE.Platform%3DDesktop&hl=en)、[模型功能矩阵](https://support.google.com/flow/answer/16352836?hl=en)、[编辑与扩展](https://support.google.com/flow/answer/16935718) | 图片边界、资产、模型差异、视频扩展 |
| S05 | [Adobe Generate Videos Using Images](https://helpx.adobe.com/in/firefly/web/work-with-audio-and-video/work-with-video/generate-videos-using-images.html) | 首尾图与控制限制 |
| S06 | [LTX Studio Tutorial](https://ltx.io/blog/ltx-studio-tutorial) | 资源、分镜、片段修订与编辑 |
| S07 | [OpenArt Story](https://openart.ai/story)、[AI Storyboard Generator](https://openart.ai/features/ai-storyboard-generator/) | 成片类型、分镜批准后动画化 |
| S08 | [Keyframe](https://www.keyframe.art/) | 剧本到视觉计划与选片装配 |
| S09 | [LibTV 官方 Skill](https://github.com/libtv-labs/libtv-skills/blob/main/skills/libtv-skill/SKILL.md) | 外部 Agent 接口与公开机制边界 |
| S10 | [STAGE: Storyboard-Anchored Generation for Cinematic Multi-shot Narrative](https://arxiv.org/abs/2512.12372) | 成对分镜、模型记忆及训练的研究 |
| S11 | [Agnes Video 2.5 Flash](https://wiki.agnes-ai.com/zh-Hans/docs/agnes-video-25-flash)、[Agnes Video 2.5](https://wiki.agnes-ai.com/zh-Hans/docs/agnes-video-25) | 实际参数边界、输入互斥、参考模式 |
| S12 | [FFmpeg Filters](https://ffmpeg.org/ffmpeg-filters.html) | 混音、响度检测与处理；不是语义听音识别器 |

本轮检索足以形成并执行以上方案，不需要把第三项整体转交研究团队。若进一步研究用户找到的特定网站，应提供其名称或链接；如要精确比较隐藏算法，则需要官方技术说明或可读源码，不能从宣传视频猜测。
