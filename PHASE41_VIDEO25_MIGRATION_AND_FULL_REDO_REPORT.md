# PHASE 41 — 视频模型统一 2.5-flash（v2.0 删除）×《雨灯归航》全量重测 报告

日期：2026-09-28。指令：用户（本对话）。执行：Claude Code（系统侧准备+记录）+
系统 Director Agent（分镜重生成，全新干净会话，正式 director_turn 入口）。

## 0. 用户指令（原文要点）

1. `agnes-video-2.5-flash` 全部使用；**2.0 不准再用，从系统删除**；
2. 《雨灯归航》全部分镜的**视频和图片重新测试**（人物/场景图不改，分镜重测）；
3. **测试由系统 agent 来做**（不是人工脚本）——每组分镜的视频和图片都重新来。

## 1. 计费与配额澄清（用户问：「2.5 不是不包含在 tokenplan 之内嘛」）

- 澄清结论：**`agnes-video-2.5-flash` 正是 Token Plan 覆盖的模型**——
  Token Plan FAQ：video 配额 500 秒/天（Starter/Plus/Pro 同），video RPM 有效值 5；
  官方 2.5-flash 页：**Current Price $0/second（限时免费，720P 列牌 $0.025/秒）**。
  基础版 `agnes-video-2.5`（$0.025/s）才是不在免费/plan 内的付费模型——本系统
  使用的从来是 flash 版。
- 本轮量级：探针 1 条 5s + 全量重测 13 镜（5-10s 配方）≈ 75-105s ≪ 500s/天。
- 参考 media 计费：前 5 张参考图免费；本系统请求 1-2 张 → 免费侧。

## 2. v2.0 删除（系统侧改动清单）

依据：官方下线公告（2026-09-25 23:59 UTC+8，用户提供的 wiki 页面复制）+
用户指令。`/v1/models`（.cn，2026-09-28 实测）仍列 `agnes-video-v2.0`，但按
官方=已下线，本地不再可能发出 v2.0 请求。

| 文件 | 改动 |
|---|---|
| `config/config.yaml` | `models.video` = `agnes-video-2.5-flash`（唯一模型）；`video_25` 键删除 |
| `src/api/agnes_client.py` | `create_video`：非 2.5 模型 id 显式拒绝（"已删除"）；**legacy v2.0 请求体分支整支删除**（num_frames/8n+1/width/height/降帧重试）；`_video25_mode` 增 **multi→reference**；`_video25_image` 增 **multi→顶层 `images` 数组（1-5，flash 上限）**；seed 按官方文档发送；negative_prompt 非空显式拒绝（2.5 无此字段，绝不静默丢参） |
| `src/generation/agnes_adapter.py` | `compile_agnes_video_call` multi 参考 1-5 结构化校验（原 v2.0 ti2vid 恰好 1） |
| `src/agents/director_visual.py` | `video_model` 枚举/校验 fallback 收敛 `{"agnes-video-2.5-flash"}` |
| `routers/shots.py` | video_model 一律解析为具体 id（job.params > config 默认 > 硬编码）并落库——恢复/续轮询同一 model_name |
| Skill `full-story-relay-calibration` | `capability-facts.md` 全表改版（六状态重判）；`SKILL.md` 路径 b/e、multi provider 事实更新 |
| `详细流程文件/AGNES_API_FACTS.md` | 新增 §3.0.1（2026-09-28 更新：唯一模型 + 官方契约表）；§3.1 legacy 契约标历史存档 |
| tests | `mock_agnes` TEST_CONFIG 默认 2.5-flash；`test_agnes.py` 重写 legacy 用例 → 新增 v2.0 拒绝 / multi→reference body / images 数量边界 / negative_prompt 拒绝；`test_director_visual.py` 无模型用例改断言解析为 2.5-flash |

## 3. 2.5 reference 模式真机探针（官方文档核读 + 实测双确认）

- 官方文档（用户复制 wiki 页）：reference 模式 `images` 字符串数组（flash ≤5）、
  与 first_frame/last_frame 互斥、参考不锁首帧（may recompose）、`<Picture N>`
  占位符指名用途、seed integer、seconds 字符串 "4"-"12"。
- 真机探针（`_probe_video25_ref.py` → `_video25_probe/ref.*`）：
  **attempt 0 一次命中**——`mode=reference + images[2]（双图）+ seed=42` 接受，
  250s 出片 5s 720P（回执 `task_xWtO1hyIkkazisEVEs60iBqTrjYcmlAt`）。
- 帧目检（ref_frame_first/mid.jpg）：`<Picture 1>` 角色身份跨帧保持；
  `<Picture 2>`（生产 s01_c01 分镜图）的灯市/雨夜场景与光感被语义采用。
  **multi→reference 契约闭环：本地编译 ✓ / 服务端成功 ✓ / 视觉语义 ✓。**
- 意义：2.5 reference 是 v2.0 multi（ti2vid 单图上限）的正统继任——接力语义
  保留；Look+尾帧**双参考同发**在 client/编译层已通，Binding 层仍单参考
  （扩展=后续阶段）。

## 4. 回归

（待回填：全量 pytest 结果）

## 5. 全量重测执行（系统 agent）

- 驱动：`_41_redo_run.py`（复用 Phase 40 Canary 正式装配：进程内 director_turn、
  真实 AgnesClient、隔离队列 `cache/_41_redo_jobs.json`、全新会话零历史）。
- 任务书：`_41_redo/task_full_redo.txt`（13 镜分镜图+视频全部重做；人物/场景
  只读；skill 纪律：接缝测量+trace 闭环、major 停用户闸门、单次锚定重试）。
- 结果：（待回填）

## 6. 遗留与后续

- （待回填）
