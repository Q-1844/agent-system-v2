# 研究自查（research-self-check，接入说明——不要求生产 Agent 每次加载）

## 本包的来源与边界

- 本包按《PHASE49_AGNES_SKILL_INTEGRATION_AND_EDITABLE_SHOT_TEST.md》§2
  审查结论整理自用户候选：§2.1 的骨架保留，§2.2 的十二处问题在成文前修订。
- 文档完整 ≠ 制作效果已通过；候选作者的观察不升级为新的受控实验结论。
  本包的所有方法主张与旧 Skill 一样，要靠 C 批同条件对照和 D 批实践检验。

## 修订前后条款对照（摘自执行书 §2.2，成文时已落实）

| 位置 | 候选原条款的问题 | 本包成文处理 |
| --- | --- | --- |
| video-action-causality 时长 | 用 4–6 秒描述单主动作；把"全镜统一变长"写成失败出口 | 保留官方 4–12；生产政策 ≥8 默认 10；失败判据=拖沓/过载/缺有效片段（SKILL.md 时长表、本包 video-action-causality.md） |
| first-frame-image 质感基线 | "短提示词质感最好"被写成普遍结论 | 改为待复验基线，注明来源与检验方式（first-frame-image.md 基线节） |
| first-frame-image 参考路径 | "质感可让渡"可能降低用户标准 | 参考承担身份约束；质感掉到不可接受仍判失败（质量门第 4 条+基线节） |
| 提示词删减 | 绝对禁止重复静态信息可能删掉动作约束 | 删冗余、保关键关系，标准="不冲突且足够明确"（video-action-causality.md 删减节） |
| shot-continuity 空间连续 | 同空间换机位被直接送原尾帧起步 | 同机位可用合格尾帧起步；新机位重建构图核对轴线（shot-continuity.md 同机位节） |
| shot-continuity 状态相等 | 正常换场也被要求物理尾态相等 | 相等规则只用于确实延续的动作；换场按剧本允许变化核对（shot-continuity.md 状态相等节） |
| 缺上游媒体处理 | 为继续执行擅自改成正常切镜 | 读合法事实或报告缺项；不猜、不悄悄改叙事（shot-continuity.md 缺上游证据节） |
| repair-diagnosis 小偏离 | 按发型/配色类别固定判级 | 范围×重要性双维；识别性特征局部错也可重大（repair-diagnosis.md 严重度节） |
| 修复顺序 | 只有重试/根帧/重建 | 先审片→裁切判断→剪辑建议→再修根因（repair-diagnosis.md 顺序节） |
| 跨镜接力端点 | 默认上一源视频物理最后一帧 | 三层端点；依据实际保留片段最后保留帧（shot-continuity.md 端点节） |
| 免费模型路径 | 标准版视频参考混进方法菜单 | 免费主链只有 agnes-video-2.5-flash；标准版列为收费备选不进菜单（model-capability-brief.md） |
| 时长失败出口 | "全镜统一变长"当失败出口 | 失败判据与时长分开；加长不能挽救过载动作（video-action-causality.md） |

## 一手资料（仅支撑契约与价格，不证明画质）

- Agnes Image 2.5 Flash：https://wiki.agnes-ai.cn/zh-Hans/docs/agnes-image-25-flash
- Agnes Video 2.5 Flash：https://wiki.agnes-ai.cn/zh-Hans/docs/agnes-video-25-flash
- Agnes Video 2.5：https://wiki.agnes-ai.cn/zh-Hans/docs/agnes-video-25
- Token Plan FAQ：https://wiki.agnes-ai.cn/zh-Hans/docs/tokenplan

## 已知的规则检查边界

执行书 §12 的 Python 规则检查（时长区间、裁切区间算术、保留帧≠物理尾帧）
是合成时间区间的规则与算术检查，不是视频模型实测，不证明更长素材必然提高
可用性。真实效果以 C/D 批的媒体证据为准。
