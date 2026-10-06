# keyframes 接续桥：接缝修复的标准手段（Keyframes Bridge Repair）

## 目的（Purpose）

当两个镜头之间需要**同时固定两侧边界**的过渡时，插入一个 keyframes 桥梁镜：
首槽+尾槽都是确定边界帧，中间过程=provider 插值——由构造保证双侧闭合。

**2.5 状态（2026-09-29）**：双帧形态已在 `agnes-video-2.5-flash` 真机探针验证
（官方 `first_frame`/`last_frame` 字段 200 受理；帧目检=首帧逐像素级对齐输入、
尾帧构图钉住、中间平滑过渡）。真镜生产样本待冻结盲测——探针端点取自既有
成片，桥只负责「钉住」，端点质量与桥无关。

**定位（用户定版）**：桥是**修复阶段工具**——普通首次生成一般没有必要插桥；
成片生成后发现确有断裂、且两个端点各自合格时才进入本技能。

## 三种视频模式的边界控制力（根本区分）

- **multi relay**：只钉开场（上一镜尾帧），尾帧自由落点；
- **i2v**：只钉首帧；
- **keyframes 桥**：双端钉死。

因此【需要同时固定两侧边界时的标准修复=插桥】——桥由构造保证双侧闭合；
**裸 retry 是重掷骰子（方差事故风险），硬 accept 是容忍断缝，都不是接缝修复。**

🔴 **决策入口唯一（Phase 3 定版）**：该不该修、修端点还是修接缝、起步/再锚定
选哪条路径——一律先走关联技能 `full-story-relay-calibration` 的
「起步/再锚定决策入口」。桥只承接【两个端点自身合格且值得保留】之后的接缝
修复；端点已画错/身份不合格 → 先回连续生产 Skill 修端点（融合首帧/重生成），
**不插桥**（插桥会把错误端点钉进过渡）。

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
6. **端点帧自身伪影进请求（Phase48 C 实证）**：keyframes 双帧的**结束帧是
   图像生成产物**，本身有伪影率（受控 12 张计划结束帧 2 张不合格，含"桌面上
   第二颗同角色头"分身——`_p48_c_m4_endframe_audit.json`）；伪影端点被
   provider 插值忠实带进视频。**硬前置：双帧/keyframes 的两端图在进请求前
   必须过 Vision 伪影检查**（肢体错误/多余人头或肢体/透视崩坏），不过 →
   重生成端点图或改单端锚定；"先审图"不得省略。
