# Phase51-E run1 复盘（run_9a05fd44f358，01:37:29 启动 → 02:27:12 停止）

## 时间线
| 时刻 | 事件 |
| --- | --- |
| 01:37:29 | run1 启动（P51_E_W01，startScope=11 镜） |
| 01:38-01:39 | n_producer success（~43s）；n_sw success（核验采用） |
| 01:39:53 | 【MI-1】故事审定 approve |
| 01:40-01:41:58 | n_cd success（~2min，核验采用，快于 C-T03 的 ~4min） |
| 01:43:46 | 【MI-2】资源审定 approve+选用（船长 Look，selection 服务端执行一次过） |
| 01:43:47-01:56:52 | n_sc（~13min，慢于 C-T03 的 ~7min；场景核验+状态视图工具调用） |
| 01:57:36 | 【MI-3】场景审定 approve |
| 01:57:50-02:12:39 | n_dir_board att1：**真实写入 6/20 → 判缺项 failed** |
| 02:16:46 | continue（explicit_retry；in-run 恢复路径工作正常） |
| 02:17-02:22:03 | att2：4 次工具调用+LLM 核对后**零写入**收工 → 仍 6/20 failed |
| 02:27:12 | 执行侧停止 run1（结构性矛盾不可重试解决） |

## 根因：完成判据与 LLM 行为的结构性矛盾
n_dir_board 的 completion=require 20 镜「本节点各 attempt 真实写入且满足质量标记」。
该判据对 C-T03（首轮核对，拍摄记录 14 镜+2 修复=真实有 20 镜工作量）成立；
对 E 轮（复验轮——C-T03 已把分镜核对做完、拍摄记录已写全）不成立：LLM 核对后
判定无需要更新的镜头（att2 零写入是诚实行为，不是偷懒），判据却强要 20 个写入。
两轮 attempt 实证 6/0，重试无解。

**干跑其实预警过**：dry_run 显示 n_dir_board will_need_work「本节点各 attempt
真实写入且满足质量标记 0 个（需 20）」——我误读为"board 有真实工作属正常"。
教训：**复验轮的定义复用必须逐节点重审 completion 语义**（require_new 的产物
类节点要新写入=对；核验/记录类节点的强写入判据在复验轮=矛盾）。

## 处置
- 定义修复（rev 2/3）：n_dir_board 先改 minCount=0（409：schema 要求正整数，
  **minCount 不支持 0**）→ 最终删除 completion（同 n_producer 的 no_spec 语义：
  attempt 正常收尾即成功），objective 注明「复验轮无分镜修改工作，仅三镜新首帧
  与描述矛盾时定点更新」。
- run1 停止（stop 语义正常：status=failed，worker finally 清 scope）。
- video 额度零损失（未到视频阶段）；损耗=~50min 墙钟+少量文本额度。
- run2（run_58a5f25aeeec）02:30:14 启动，同 allowedShotIds=11 镜。

## 数据点（对效率结论的意义）
- run1 前置 4 节点全部核验采用快速通过（43s/1min/2min/13min）——A 批修复的
  复验语义工作正常，无 require_new 判死。
- MI-2 的 look 选用 selectionOverride 一次成功（C-T03 曾因 selection 缺陷失败
  一次+修复重启）。
- board 失败→continue→att2 的 in-run 恢复链路真实工作（P50 时代=整轮重启）。
- 新增损耗类型归档：「复用 run 定义的复验轮判据矛盾」——修复=定义层（本轮），
  系统层 backlog：dry_run 对此类矛盾给出更醒目的告警（已有 will_need_work，
  需要区分「有工作量」与「判据与现状矛盾」两种 will_need_work）。
