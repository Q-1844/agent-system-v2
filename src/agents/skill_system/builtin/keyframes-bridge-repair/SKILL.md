# keyframes 接续桥：接缝修复的标准手段（Keyframes Bridge Repair）

## 目的（Purpose）

当两个镜头之间需要**同时固定两侧边界**的过渡时，插入一个 keyframes 桥梁镜：
首槽+尾槽都是确定边界帧，中间过程=provider 插值——由构造保证双侧闭合。

## 三种视频模式的边界控制力（根本区分）

- **multi relay**：只钉开场（上一镜尾帧），尾帧自由落点；
- **i2v**：只钉首帧；
- **keyframes 桥**：双端钉死。

因此【需要同时固定两侧边界时的标准修复=插桥】——桥由构造保证双侧闭合；
**裸 retry 是重掷骰子（方差事故风险），硬 accept 是容忍断缝，都不是接缝修复。**

## 适用性（触发判据）

- 跨 Location 且需要连续过渡；
- 大姿态反转；
- 重生成级联造成的下游断缝；
- 任何需要「首尾都对齐」的过渡；
- 用户直接要求在两镜之间加过渡镜头。

🔴 **窄锚定（39B 实证，防 over-trigger）**：插桥判定必须依据【双端证据】
（两侧接缝数据或两端实际状态对照）或多帧连续性证据——单一端点/单帧异常不
构成插桥依据，先补测另一端或用 visual.sample_frames 采样核实再裁定；镜内
吸引子/端点类问题（重试可解）不因 MAE 单点异常而改道插桥。

明确的电影式 hard cut 不是 continuity failure：在 trace 记
continuity_type=intentional_cut，不强求近零 MAE。

🔴 **Bridge 前提（39F 定版）**：插桥的前提不是「画面差得大」，而是
【A 端点必须保留 + B 端点必须保留 + 两者业务上必须连续过渡】三者同时成立。
因此 intentional cut / new group / deliberate location jump / montage cut
默认**不是** bridge repair——「跨 Location」本身不构成插桥理由（跨 Location
【且】业务要求连续过渡才在列）。保留既有纪律：双端证据、桥时长 ≥10s、
≥3 段中间节拍、seed 后禁止 refresh binding。

## 参与者与角色

| Agent | 角色 | 职责 |
|---|---|---|
| Director | primary_executor | 触发判定（双端证据）→ shot.bridge_create → 双侧验收 → trace 落档 |
| Producer | coordinator | 把桥作为 repair option 之一理解并编排；向用户报告桥的时长成本（10s+ 视频配额）与 cascade=0 优势 |

## 总体工作流

```
[Director] 双端证据齐备 → 判定插桥（非 retry/accept）
   ↓
shot.bridge_create(after_shot_id, transition_description, duration_s, …)
   ↓（后端桥服务七步事务，确定性时序由代码强制）
   确定性校验 → 后继镜首帧提取+托管 → 插桥梁镜 → relay+字段+binding
   → compile basis → seed 尾边界 → 预提交守卫（boundary_frame_stale fail closed）
   → 提交生成 → 桥视频+末帧自动托管
   ↓
[Director] 验收：双侧 seam_measure + visual.sample_frames 帧检
   ↓
trace 落档（含 bridge_frame_check 帧检证据）
```

## 决策模型

| 情境 | 选择 |
|---|---|
| 需要双端对齐 + 双端证据齐备 | **插桥** |
| 单一端点/单帧异常、镜内问题 | 先补证据；重试可解的不插桥 |
| 明显 hard cut 意图 | 记 intentional_cut，不修 |
| 桥失败 | 如实报告；不为了画面好看重生成两侧已合格镜头 |

## 完成标准

- 双侧接缝（上镜尾→桥首、桥尾→后继首）`calibration.seam_measure` 进入对应
  区间（同场景桥近零带 1.7-2.4；跨 Location ~26 量级，按场景放宽）；
- `visual.sample_frames(桥)` 五位采样逐段检查：无突然 hard cut、无中间形态
  跳变、无 identity jump、无 unexpected element、过渡动作成立、桥内容不重复
  后继镜开头叙事；
- **桥帧检证据必须落 trace**（39B 实证）：sample_frames 返回的帧清单与
  pairwise_evidence 结论作为 bridge_frame_check 字段写进该桥的 trace record——
  只有两端 MAE、无帧检记录，不得在 trace 中宣布桥成立；
- 最终目检保留人工（帧文件路径随回执供人审）。

## 失败模型

1. **短时长无 morph 空间（M5B-a1）**：5s 桥在两钉点之间直接硬切——服务拒绝
   <10s 的桥，别绕。
2. **两点一线诱发切变（M5B-a2）**：transition_description 不写中间节拍会被
   拒绝/诱发切变——必须 ≥3 段中间节拍。
3. **seed 后 basis 失效（38 桥时序坑）**：seed 之后任何 binding refresh 顶掉
   basis → `boundary_frame_stale` fail closed，绝不偷偷重新 seed；video_adapter
   的 BOUND freshness gate 是第二道独立防线。
4. **桥尾帧是强机位先验**：桥尾帧成为下镜 relay 开口，其近景正面构图是强
   机位先验 → 下镜需尾态尺寸锚定（远景首句+全身收束句）对冲，否则特写吸引子
   连击（M4 a2/a3 实证，a4 破解）。
5. **误判样本**：M5B-a1 曾凭两端 MAE 宣布桥成立、实际中间跳变——帧检证据
   落 trace 是硬要求。
