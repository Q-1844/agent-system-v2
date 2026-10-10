# Phase51 C 批运行日志（C-T03 起始）

## 冻结快照（run 启动时，C2 要求）

| 项 | 值 |
| --- | --- |
| 启动时刻 | 2026-10-10 02:41（本地） |
| 代码 | 子仓 c52460f（A f98b654 + B 5411005/93794c0/a89a801 + B 报告 c52460f）；tracked dirty 仅 components.d.ts（用户历史改动，与本轮无关） |
| Skill | full-story-relay-calibration v1.5.0（sha256:878fa2a14047835b）+ keyframes-bridge-repair v1.4.0 |
| 定义 | P51_C_W01 rev1 contentHash sha256:41c7d5f7a7edfbd0（n_dir_video=require_new×20 / board=verify×20 / skill 绑定 1.5.0） |
| 模型 | text agnes-3.0-flash / image agnes-image-2.1-flash / video agnes-video-2.5-flash；Thinking 全开 |
| 并发/限流 | AGNES_VIDEO_INFLIGHT=2 / AGNES_VIDEO_RPM=2（不扩）；poll 自适应退避（60s 后 20s） |
| 项目 | P50_B（canonical 复用：冻结分镜 20 镜+approved 4 Looks+场景+首帧选用） |
| 配额起点 | video 40/500s（B 实验已用）→ 本轮可用 ~460s；images 11/4000 |

## 运行 run_36919eb5a073 时间线（attempt 级，本地时刻）

| 时刻 | 事件 |
| --- | --- |
| 02:41 | run 启动（idempotencyKey p51_c_run1） |
| 02:41-02:42 | n_producer success（~43s）；n_sw success（核验采用，changed_refs 空=allow_verified_reuse 正常） |
| 02:43 | 【人工介入 MI-1】故事审定代行 approve（5 段核验采用与冻结分镜一致） |
| 02:47 | n_cd success att=1（核验+定点修复 looks:1/refs:2，~4min） |
| 02:48 | 【人工介入 MI-2】资源审定代行 approve+选用（船长 Look ref_e5e8b8e0a54c；selection 服务端执行 done） |
| 02:49 | n_sc dispatching（场景核验中） |
| 02:56 | n_sc success（核验无修改，~7min）；【人工介入 MI-3】场景审定代行 approve |
| 03:00-03:33 | n_dir_board success att=1（~33min：20 镜结构核对+拍摄记录 14 镜落 canonical+s04_c02 大副站位锁船上（用户 F2 闭合）+s01_c05 防虚构码头） |
| 03:35 | 【人工介入 MI-4】分镜审定代行 approve+首帧选用——**选用执行失败**（AttributeError: Asset 无 candidate_id——Phase47 潜伏缺陷：候选 id 在 first_frame_batches 非 Asset 聚合；P50 靠传 exact selected 版本绕过，本轮 selected 已变更而暴露） |
| 03:40 | 【系统修复】selection.py 两处修复（resolver+selector 改读 batches 真实候选）commit；后端重启（快照 v2=c52460f+selection fix） |
| 03:42 | continue 选用幂等重试 → done；【人工介入 MI-5】选用重试 |
| 03:42 | n_dir_video dispatching（视频阶段开始：20 镜 require_new×20） |

**快照变更记录（C3 纪律）**：v1=c52460f；v2=c52460f+selection fix（commit 在 n_rv_board 选用重试前生效；受影响验证=board 选用已按新代码重做完成；视频阶段以 v2 快照运行）。 |

## 与 P50 基线的初步对照（同节点，第 1-4 节点）

| 节点 | P50 run6 | P51_C run1 | 备注 |
| --- | --- | --- | --- |
| n_producer | 45s | ~43s | 相当 |
| n_sw | 52s | ~40s | 核验采用（无写入） |
| n_cd | 301s | ~240s | 均为核验+定点修复 |
| 重启次数 | 5（前面 5 个 run 全废） | 0 | A 批修复生效中 |

（后续逐段补记）

| 03:21 | n_dir_board success（~20min，拍摄记录 14 镜+s04_c02 站位锁定）；【MI-4】分镜审定 approve+选用→选用失败（Asset 无 candidate_id——Phase47 潜伏缺陷） |
| 03:40 | selection.py 修复+后端重启（快照 v2）；03:42 continue 选用重试 done（MI-5）；n_dir_video att1 派发 |
| 03:24-03:54 | att1 失败：DNS 故障（getaddrinfo）连杀 7 job+2 条 negative_prompt 契约拒绝+1 条 boundary_frame_stale——导演以 update_many 清 14 镜负面字段（契约正确处理） |
| 04:11-05:36 | att2 success（85min）：16+镜完成（DNS 间歇仍致数条失败-重试）；video 计入窗口修复提交（快照 v3） |
| 05:47-07:37 | 一审驳回（4 镜叙事失败）→repair→att3 success（110min，越界重生成 16 镜——范围纪律偏差记录）；二审驳回首验帧 |
| 07:46-08:20 | 一审 2 驳（s04_c04/s05_c03 attractor）→repair→att4 success（33min，再度全员重生成）；配额 494/500 耗尽 |
| 08:20-08:29 | 一审终审通过（18 保留/2 排除+根因=首帧 attractor 实证）；v2 选用 |
| 08:29-08:43 | 装配 att1=1-clip 错误→【MI-10】驳回→att2 修复=18 镜 171.8s |
| 08:45 | 成片审定通过（MI 记录）→ **run COMPLETED** |

## 最终配额与产物

- video 494/500s（B 实验 40s + C 运行 ~454s）；images 43/4000
- 成片 seq_6c503ed5/srender_9b115ac6.mp4（171.8s 18 镜）；逐镜 20 条全新版本
- 快照链：v1=c52460f → v2=+selection修复 → v3=+video_version计入窗口（每步变更均有提交与回归）
