# FABLE_PILOT_V1_REPORT — 34.md《狐狸和葡萄》Full Production Pilot 最终报告

日期：2026-09-23 ｜ 前置 commit `3832ed0`（33.md FINAL）→ 本阶段终 commit `858b298`
项目：`Fable_Pilot_Fox_and_Grapes_V1`（正式系统生产，8 镜 8 READY binding，multi 模式）
状态：**生产能力验证完成 + 分组迭代循环确立；最终选片待下一阶段（用户已定方向：专门 agent 针对性方法搜索）**
工作模式（用户裁定）：分组迭代循环——AI 按最佳标准整批生成（每组 1 条）→ 用户整批审查 → 点名问题组 → 只重做该组。用户作为质量闸门嵌入流程，不逐版写大报告。

---

## 一、执行摘要

从零建立《狐狸和葡萄》正式项目（Producer→Specialist 生产链），经**三轮迭代**完成 8 镜视频生产共 **35 条真实视频**（R1 16+1 / V1B 6 / V1C 6，全部 done，生成失败率仅 2 次瞬时网络/配置问题且零配额浪费）：

| 轮次 | 开场策略 | 核心数据 | 用户结论 |
|---|---|---|---|
| R1 | 全镜统一 R3 站姿参考 | 16+1 条；**首帧逐像素级相同**（用户观察，机器证实） | 动作被开场构图束缚 |
| V1B | 按镜姿态参考（6 变体，全部从 R3 派生） | 相邻首帧 diff 35-50（姿态多样性） | **动作灵活性最好**（"模型发挥空间更大"） |
| V1C | 尾帧接力（上一镜实际末帧做下一镜参考，链式） | 衔接 diff 3.3-9.0（无接力对照 35-50） | **连续性最好，但漂移比 V1B 多** |

**本轮最大产出**：①校准配方（multi+R3）成功迁移正式生产；②用户方向两次实证（姿态参考库、尾帧接力）并沉淀为系统能力；③确立"连续性 vs 身份稳定性"的核心 trade-off 数据——下一阶段（专门 agent 方法搜索）的输入。

## 二、§29 二十问逐答

| # | 问题 | 回答 |
|---|---|---|
| 1 | calibration 结论是否成功迁移到正式生产？ | **是，经一次最小接线**。33.md 胜出配方=multi 单参考直生+R3；生产链原有 i2v/keyframes 无法消费 R3（§27 blocker 类型）→ LinkMode.MULTI 经 GenerationRequest seam 接入（Binding/Pack 零改动），35 条视频全部 multi 生成 |
| 2 | R3 在完整 8 镜故事中是否仍稳定？ | 身份稳定（R1 轮无系统性崩坏；无双狐/多肢/物种漂移）。但**统一 R3 的"准首帧复刻"使 8 镜开场全同**——R3 作为唯一参考在连续叙事中不够 |
| 3 | P1/base 是否足够完成真实作品？ | **是**。8 镜 prompt 808-1040 字符（动作+camera+风格行），无一段靠加码约束；s05 跳跃（最复杂动作）在 P1 密度下完成全动作链 |
| 4 | 狐狸 identity 是否跨 8 镜保持？ | R1/V1B：稳定（look 参考承载）。**V1C 接力链：逐代漂移**（用户证实"漂移会多一点"）——链上每代条件于上一代输出，这是尾帧接力的核心代价 |
| 5 | quadruped motion 是否达到 production usable？ | **是**（R1 s02 四足行走✓、V1B 各姿态镜✓）。最大风险（双足直立吸引子）通过参考图规避——文本约束不可靠（FACTS 实证） |
| 6 | jump 能否通过有限 candidates 获得可用结果？ | **是**。s05 两轮均产出完整跳跃动作（蓄力→腾空→落地）；顶点触葡萄=模型条件化限制复现（33.md 已知），候选间随机性可管理 |
| 7 | 葡萄/场景物体稳定性实际如何？ | R1 s05_B 末段疑似葡萄脱落（机器目检红点）；葡萄挂矮=用户已裁定接受。未出现整段物体消失（P3 禁令遵守下） |
| 8 | variant regeneration + selection 是否足以覆盖随机失败？ | **是**。2 次生成问题（网络瞬断/派生未批准回落）全部经既有机制恢复/暴露，零静默失败；s05 恢复路径（video_id 续轮询）实测有效 |
| 9 | 是否出现必须靠新增 prompt constraint 才能解决的问题？ | **无**。全部问题经参考策略（姿态库/尾帧接力）、镜头修订、候选选择解决——P3 禁令全程遵守 |
| 10 | 是否存在新的 system-level missing capability？ | **5 项，全部按 §5 最小修复并带回归**：multi 接线（e44b90b）/ look attach+人评证据（dae7931）/ scene-only binding+rehost（31e62a5）/ derive_look_reference+按镜参考选择（9e3d921）/ prev_tail 尾帧接力（bdef4e3+7fb9037）。全量回归终态 **2294 passed 零失败** |
| 11 | no-BGM 是否成立？ | **不成立（PROVIDER STOCHASTIC）**。用户听感：多数视频后段出现 BGM（仅 s01/s08 两条环境镜干净）；机器分段核查证 s04/s07（后段谐波组织 0.44/0.46）。生产请求零音频指令 → 归类 provider 随机行为；成片 BGM 本就后期添加（33.md 架构），selection 可选最轻者 |
| 12 | native footsteps / movement SFX 是否成立？ | **部分**。环境声底全部在场；动作瞬态（落地等）在 s05/s08 可测（0.4-2.4 onsets/s）；脚步级细节机器不可确证，人评未专门标记——留待成片轮 |
| 13 | Scene consistency 如何？ | 好。1 Location+8 实例，跨镜藤架/土路/光线一致；场景参考（s01/s08 空镜）直接可用 |
| 14 | style consistency 如何？ | 好。2D storybook 全程稳定（style line + VisualProfile）；vision 审查对 R3 系的"偏 3D"标记被人评覆盖（用户裁定） |
| 15 | Producer / Specialist ownership 是否保持正确？ | **保持，但有运行性教训**：Producer 优先级漂移（多目标任务跳过最高优先级）→ 协作式取消×2 → 确立"每轮单一目标任务"；session 跨 turn 累积历史导致重复旧工作。zero direct canonical writes 保持（5 项系统修复均走正式 service/REST） |
| 16 | binding / provenance / VideoVersion 是否完整？ | **是**。8/8 READY（scene-only binding 补齐后）；每 candidate 完整 provenance（candidates.jsonl/v1b/v1c_runs + generation_basis + 参考 identity + 音频核查）。**发现**：VideoVersion.local 按镜覆盖——多候选必须即时从 provider URL 独立存档（已修入 driver，正式修复留待下一阶段） |
| 17 | 人工最终选择了哪些 candidates？ | **尚未最终选片**——三轮是策略对比数据。用户结论：V1C 连续性最好/漂移多，V1B 灵活性最好；最终版本待下一阶段方法搜索后逐镜定 |
| 18 | 8 镜是否能连续观看并形成完整寓言？ | V1C 链连续性最好（衔接 diff 3.3-9.0，s02 空路入画/s06 地面起始实测衔接），但漂移待解；完整成片判定留待方法收敛后 |
| 19 | 是否已经足以进入更复杂的双动物作品？ | **不急于进入**。单动物的三种参考策略 trade-off 刚量化完；双动物交互（多主体参考+相互遮挡）复杂度更高，应先解决单主体的连续性/漂移方法 |
| 20 | 下一阶段真正应该增加的复杂度是什么？ | **用户已定**：专门 agent 针对性方法搜索——目标=在 multi 单参考槽内兼得"尾帧接力的位置连续性"与"look 参考的身份稳定性"（候选方向：混合调度策略/重锚定节奏/尾帧+look 的派生融合/裁剪定位等——由专门 agent 系统性搜索，勿由本报告预设） |

## 三、三轮参考策略实证数据（下一阶段方法搜索的核心输入）

| 维度 | R1 统一参考 | V1B 姿态参考库 | V1C 尾帧接力 |
|---|---|---|---|
| 开场多样性 | ✗（逐像素同） | ✓（diff 35-50） | ✓（各镜=上一镜结尾） |
| 镜间衔接 | ✗（状态跳变） | ✗（位置跳变 35-50） | **✓（diff 3.3-9.0）** |
| 动作灵活性/发挥空间 | 受构图束缚 | **最好（用户结论）** | 中（起点被钉住，过程自由） |
| 身份稳定性 | ✓（look 直锚） | ✓（look 直锚） | **逐代漂移（用户证实）** |
| 额外依赖 | 无 | 姿态库（derive 能力） | 上一镜视频完成（串行链） |

核心 trade-off：**单参考槽内，"位置连续性"（尾帧）与"身份稳定性"（look）不可兼得**——用户裁定下一阶段由专门 agent 针对性搜索方法。

## 四、失败归因（§20 四分类，全程滚动）

| 失败 | 归因 | 处置 |
|---|---|---|
| 狐狸双足直立（首批 4 图+重试 5+ 次） | MODEL（"角色设计"语感的双足吸引子；文本姿态约束不可靠）+ AGENT（检索失败） | 用户裁定校准 R3 导入（hr_0ed127877c8c） |
| V1C 首批 6 条白跑（开场仍全同） | SYSTEM（resolver 同场景 idx>0 找 prev，每段 1 镜永远 None→静默回落 hero） | 展平解析修复（7fb9037）+跨场景回归测试；零配额损失（fail-closed 在 provider 调用前） |
| s05 轮询网络瞬断 | PROVIDER/网络 | 恢复路径实测有效（video_id 续轮询，不重复扣配额） |
| Producer 优先级漂移×2 | AGENT（多目标任务排序） | 协作式取消+每轮单目标任务 |
| imgbb 大陆下载不稳 | PROVIDER/网络 | rehost url_override 换 Agnes provider URL |
| Location 改名级联 stale 全部 SIV+binding | SYSTEM（语义指纹设计使然，正确行为） | 回退名字即恢复；视图从未重生成 |

## 五、配额与产物

- 视频配额：两日合计 ≈180s / 500s 每日（35 条成功 + 6 条白跑 fail-closed 零消耗）；图片 ≈40 张 / 4000 每日
- 产物：`_34_fable_pilot/`（R1/V1B/V1C 三 Gallery + candidates/v1b/v1c_runs provenance + 音频核查×3 + 恢复视频×29 + PROGRESS + Audio Preflight）
- 项目：`projects/Fable_Pilot_Fox_and_Grapes_V1`（完整 canonical：故事 8 段/狐狸 6 approved 参考/葡萄园 1 Location 8 实例/8 镜 8 READY binding/35 VideoVersion 全 provenance）

## 六、§30 停止声明

报告完成即 STOP。不自行开始：双动物 calibration、《龟兔赛跑》、连续性方法搜索（用户已裁定方向：专门 agent 针对性搜索，待用户启动）。最终选片（§22）与成片 assembly（§24）随方法收敛后进行。

## 七、下一阶段交接输入（供专门 agent 方法搜索）

1. 已实证的三策略数据（本报告 §三）
2. 核心问题定义：multi 单参考槽内兼得位置连续性与身份稳定性
3. 约束：v2.0 ti2vid 单图上限；参考强条件化开场（~0.7-2.8s 复刻前缀）；look approved 链是身份权威
4. 现成机件：derive 派生 / 按镜参考选择 / 尾帧接力 / 尾帧自动提取托管 / vision 审查+人评证据链
