# 继续与复用（continuation）

## 五种继续意图（判别顺序即优先级）

1. **resume_run**：恢复中断的生产运行（run 级）。证据：checkpoint / B1 状态。
2. **add_assets**：只补缺失素材（镜头/图片/参考资产），不动故事结构。
3. **extend_existing**：延长旧片（`additional_content`：只算新增部分；
   `final_total`：旧片+新增合计达标）。
4. **sequel**：独立续集，新 output；沿用 continuity 锚点（Location /
   SceneState / Character 只读投影），不自动拼接原片。
5. **complete_project**：把项目做完（剩余场次/镜头继续生产）。

**判别铁律**：有 source_project 且文本含"继续"类语义时，永不判为 create。

## 复用规则

- 已有有效资产（Shot/Video/图片）保持原引用，**不重新付费**；
- 只对真正缺失/失败/过期的部分生成（`incremental_actions` 逐项列明
  generate_missing 与 reuse，reuse 必须带可核验来源：ID / SHA / 检查点引用）；
- 已有 Director/Planner 的 fresh draft 可复用，但换了 SceneBudget 约束后
  必须重新校验（constraint fingerprint，PR B1 接入）；
- 续作/延长不重写既有 Story/Shot（append-only）。

## 与现有机制的关系

- resume_run 的执行走既有续跳链（resume_dispatch / resume_patrol，
  含档位透传），本技能只负责判别与派发参数组装；
- complete_project / add_assets 的执行在 PR C1 接入
  （ProjectSnapshot + IncrementalWorkPlan）；现在先产出正确的决策与工作清单。
