# 视频生成能力事实表（按日期维护——生成前必读，判断用 SKILL.md，事实用本表）

> 维护纪律：本表只记【带来源与日期的事实】，不写操作规则；每次真实调用/官方
> 公告变化后更新对应行。任何空白写"未确认"。六状态含义：
> 设计可选（本系统架构有此概念）/ 官方支持（provider 文档）/ 本地编译可用
> （本仓库编译器+client 实际能发出的请求）/ 最近服务端成功（真实调用回执）/
> 视觉已验证（人工/工具看过成片帧）/ 项目资产已批准（Look/SIV/版本选中）。

## 模型与服务状态

| 事实 | 状态 | 日期 | 来源 |
|---|---|---|---|
| `agnes-video-v2.0` 官方公告下线（迁移至 2.5 系列） | 已证实（公告） | 2026-09-25 23:59:59 UTC+8 | wiki.agnes-ai.com/zh-Hans/docs/agnes-video-v20（2026-09-27 核读） |
| **v2.0 已从本系统删除**：config 默认/director 枚举/client 全部收敛到 2.5-flash；AgnesClient 对非 2.5 模型 id 显式拒绝（"已删除"） | 已证实（本地代码） | 2026-09-28 | 用户指令 + src/api/agnes_client.py create_video 守卫 |
| v2.0 实际仍受理并出片（下线公告后、删除前） | 已证实（真实回执，历史） | 2026-09-27 | expB `vv_6e26ffbc` URL `…/videos/agnes-video-v2.0/video_d2266f81….mp4` |
| `/v1/models` 列表（.cn 入口）：`['agnes-video-2.5-flash', 'agnes-video-v2.0']`（v2.0 仍列名，但官方状态=已下线） | 已证实（真实 GET） | 2026-09-28 | `_video25_probe/models.json` |
| `agnes-video-2.5-flash` 为唯一视频模型；**Token Plan 覆盖：500 秒/天 + 当前限时免费（$0/秒，720P 列牌价 $0.025）** | 已证实（官方文档） | 2026-09-28 核读 | wiki.agnes-ai.com Token Plan FAQ + Agnes Video 2.5 Flash 页 |
| 2.5-flash 契约：mode 必填（text/keyframe/reference）；seconds 字符串 "4"-"12"（默认 "5"）；size 仅 "720P"；aspect_ratio 默认 16:9（16:9 输出 1280x704）；seed integer 可复现；n 仅 1 | 已证实（官方文档 + 2026-09-06 实测） | 2026-09-28 核读 | wiki.agnes-ai.com/en/docs/agnes-video-25-flash + `_video25_probe/` |
| 2.5-flash reference 模式：`images` 数组 **≤5 图**（flash 专属上限，基础版 2.5 为 ≤8）、audios ≤3、videos 不支持；与 first_frame/last_frame 互斥；**参考不锁首帧**（may recompose）；prompt 可用 `<Picture N>` 指名用途 | 已证实（官方文档） | 2026-09-28 核读 | wiki.agnes-ai.com/en/docs/agnes-video-25(-flash) |
| 2.5-flash reference 本地编译 | **已实现**（multi→reference，images 数组 1-5，2026-09-28） | 2026-09-28 | src/api/agnes_client.py `_video25_mode/_video25_image` + agnes_adapter multi 1-5 |
| 2.5 keyframe：官方字段 `first_frame`/`last_frame`；本仓库发顶层 `image`（2026-09-06 实测出片的兼容形态，生产 s02_c02 复证） | 已证实 | 2026-09-28 核读 | 官方文档 + `_video25_probe/v25d.*` |
| 2.5 双帧 keyframes（首+尾）契约 | **已验证（2026-09-29 真机探针 `_probe_video25_keyframes.py`：官方 `first_frame`/`last_frame` 双帧 200 受理→69s 出片；帧目检：首帧逐像素级对齐输入、尾帧构图钉住、中间平滑过渡——桥路径解锁）**；client 已放行（`_video25_mode/_video25_image`） | 2026-09-29 | `_video25_probe/kf.*`；42.md §四 P2 |
| 2.5 契约无 negative_prompt 字段——非空显式拒绝（绝不静默丢参，需并入 prompt 文本） | 已证实（官方参数表无此字段） | 2026-09-28 | 官方文档 + client 守卫 |
| 图片 i2i：`extra_body.image` 数组，本仓库 cap=6 张（URL 或 data URI） | 已证实 | 长期 | AGNES_API_FACTS §2；image_adapter MAX_PROVIDER_INPUT_IMAGES |

## 路径能力表

| 路径 | 设计可选 | 官方支持 | 本地编译可用 | 最近服务端成功 | 视觉已验证 | 项目资产已批准 |
|---|---|---|---|---|---|---|
| v2.0 i2v / keyframes / multi（历史存量） | — | — | **否（v2.0 已删除，本地拒绝）** | 是（历史回执，2026-09-27 前） | 是（历史；multi 身份漂移=负例） | 历史资产仍批准，重生成走 2.5 路径 |
| v2.0 双参考（Look+尾帧同发） | — | 否（ti2vid 1 图上限） | 否 | 否（400，32.md X005） | — | — |
| 2.5 keyframe（顶层 `image`+seconds，首帧硬边界） | 是 | 是 | 是（config 默认模型） | 是（2026-09-06 探针；2026-09-27 s02_c02 生产；2026-09-28 A/B 对照） | **是（2026-09-28 A/B：硬首帧在 2.5 上锁身份/画风，中末段稳）** | s02_c02 已选 2.5 版（P4 雨灯归航） |
| 2.5 reference 多图（multi→`images` 数组，双图+seed 实测） | 是 | 是（≤5 图，不锁首帧） | **是（2026-09-28 实现）** | **是（2026-09-28 探针 `ref.*`：双图+seed=42，5s 720P，回执 task_xWtO1hyIkkazisEVEs60iBqTrjYcmlAt；提交即受理由 flash 校验前置保证）** | 是（帧目检：`<Picture 1>` 身份跨帧保持、`<Picture 2>` 场景/光感语义被采用；画面风格随参考图） | — |
| 2.5 reference relay+Look 双参考（哨兵 `prev_tail_look`：尾帧+hero Look 同发，`<Picture 1/2>` 指称自动附加） | 是（2026-09-29 接线） | 是（≤5 图） | **是（2026-09-29 实现，opt-in 非 default）** | **是（2026-09-29 P1 探针 task_VxgTFsmE…，199s 出片；经生产编译器同源请求）** | **是（P1 帧目检：中尾段画风向 Look 回正、空间叙事正确、身份特征保持；端到端链用户验收通过。已知权衡：Look 与尾帧锚定竞争——端点帧已冻结的偏差特征可随尾帧胜出，根帧有错先修根帧）** | 定版配方（root i2v→尾帧托管→双参考接力）经多轮用户逐链验收 |
| 2.5 reference relay+Look+场景三参考（哨兵 `prev_tail_look_scene`：尾帧+hero Look+本镜 SIV 同发，`<Picture 3>` 指称=场景元素位置样式以此为准） | 是（2026-09-30 接线） | 是（≤5 图） | **是（2026-09-30 实现，opt-in 非 default）** | **是（2026-09-30 43.md §二凭空门修复轮真机：门先验进请求后拱形门形态正确、幽灵门消失；此前三次生成三种不存在的门）** | **是（帧目检；注意副作用=规格钉死过强吃掉开门动作——约束留动作空间）** | 待用户终审 |
| keyframes 桥（`shot.bridge_create`） | 是 | **是（2.5 双帧 2026-09-29 探针实证）** | **是（client 已放行双帧）** | 是（2026-09-29 探针 kf.*；38 桥实证历史） | 是（探针帧目检：双端钉住+过渡平滑；真镜样本待 42.md §四/盲测） | 端点须逐案批 |
| 融合首帧起步（Look+尾帧经图片 i2i 合成新首帧→i2v） | 是 | 是（图片接口） | 是（BOUND reference_previous 链路） | 是（2026-09-27） | **是（正例：身份/景别/动作显著优于裸尾帧接力；已知瑕疵=结尾提前触水、末帧颈前偏粉）** | 实验候选（expB），**未正式选片** |
| inherit_previous（来源真实末帧直接作视频首帧） | 是 | 是 | 是 | 是（历史项目） | 部分 | 逐案 |

## 本仓库关键守卫（2026-09-28 更新）

- `AgnesClient.create_video`：非 2.5 模型 id 显式拒绝（v2.0 删除）；negative_prompt 非空拒绝（2.5 无此字段）。
- `_video25_image`：multi→`images` 数组 1-5 张（flash 上限），i2v→顶层 `image`，t2v 无图。
- `agnes_adapter.compile_agnes_video_call`：multi 参考 1-5 个（缺/超结构化拒绝）。
- `routers/shots.py`：video_model 一律解析为具体 id（job.params > config 默认 > 硬编码 2.5-flash）并落库——恢复/续轮询用同一 model_name。
- `_resolve_boundary_frame`：首帧槽批次带 `slot_semantics=multi_reference_only`（multi 接力同步产物）→ i2v/keyframes 消费被 `first_frame_slot_not_hard_boundary` 结构化拒绝（routers/shots 写标记，video_adapter 拦截）。
- **视觉状态注记边界（2026-09-30 实证）**：i2v 根帧的「未点亮」类状态注记可被模型忽略（用户裁定=模型能力问题）——状态强一致靠首帧画面而非文本注记。
- `resolve_multi_relay_source`：跨段接力返回 `cross_segment=true` → 诊断 `prev_tail_cross_segment_relay` + 进 visual basis 指纹（组首 prev_tail 残留不再静默）；哨兵 `prev_tail_look` 返回 `identity=true`（同进指纹）；哨兵 `prev_tail_look_scene`（2026-09-30）再返回 `scene=true`（三参考，SIV 缺失/非公网 URL fail closed——relay 镜场景元素锚定）。
- `calibration.run_create`（2026-09-29 进工具面）：校准 run 由 agent 自主创建（此前唯一入口是 REST，trace_upsert 对不存在 run 报 not_found——Phase 41 重测 13/13 trace 落档失败的根因）。
- 视频提交即落库 task id（routers/shots.py：video_id/task_id/model 提交成功立刻 update_shot）；崩溃恢复 lifecycle.recover_pending_videos（生产 startup + 参数化供进程内驱动器）只续轮询不重提交；「generating 且无 video_id」（提交结果不明）不自动处置，重提交时如实标注 previous_submit_uncertain。
- `_extract_video_end_frame`：末帧文件名带来源版本 id（`{shot_id}_video_end_{version_id}.jpg`），重提取不再覆盖旧字节。
- BOUND 首帧批次 `generation_basis.continuity_tail` 记录实际发送的尾帧身份（source_shot_id/end_source_version/url）。
- **`resolve_human_reference_mode` auto 默认翻转（2026-09-30，Phase 42 定版）**：景别驱动的紧景别（CU/ECU/MCU 家族）auto→**crop**（P3 三臂对照：相对 drop 构图不变身份变好；EXP2/3 用户逐图评审确认跨镜最稳配方——31.md §19 悬案落定）；角度驱动的 context_only（OTS/insert/detail）维持 **drop**（无 crop 实证，insert/detail 主体常为道具）。显式 keep/drop/crop 直通不变。派生失败回落源媒体+`identity_crop_derivation_failed` 诊断。
- **`generation_basis.diagnostics` 落档（2026-09-30）**：首帧/尾帧批次的 basis 现包含 bound 请求 diagnostics（此前只落 dropped_optional/semantic_roles——派生失败等诚实降级诊断曾不留痕）。
