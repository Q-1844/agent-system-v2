# 崩溃与恢复（failure-recovery）

## 两段式身份（PR B1 落地，现在就要遵守语义）

1. **TaskSpec 身份**：任务接受时就固定（task_spec_sha256）。阶段 0（规划中）崩溃
   → 重启后可以**重新规划**（重新调 Script Writer），但文本额度不回退、
   重规划必须产出与原 TaskSpec 一致的意图与时长契约；
2. **Blueprint 身份**：规划通过校验后 CAS 冻结（plan_fingerprint）。冻结后崩溃
   → **必须恢复原蓝图**，不得借机改剧情、改预算、改时长结构。

## 崩溃窗口速查

| 崩溃点 | 恢复动作 | 媒体 |
|---|---|---|
| Stage 0 文本调用中 | 重规划（额度不回退，跨 hop 有界） | 禁止 |
| 冻结 CAS 中 | 读 B1 判定冻结态；未冻结→重规划；已冻结→恢复蓝图 | 禁止 |
| 冻结后、FullRun 建立前 | 同一 Blueprint 重物化，不重调 Script Writer | 禁止 |
| FullRun 进行中 | 走既有续跳链（含档位/plan hash 透传） | 按既有预算 |

## 红线

- Stage 0 未冻结 → **零媒体调用**；
- auto 续跳缺 plan_fingerprint 或错用旧档参数 → 一律拒绝，不默认 200 档；
- 已受理视频的 UNKNOWN/SUBMITTING 状态不得自动重 POST（对账优先）；
- 已标记复用的素材必须有可核验来源，不接受自然语言"已存在"。
