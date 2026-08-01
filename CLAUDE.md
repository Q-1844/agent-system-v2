# CLAUDE.md - 动画分镜工作流项目指引

## 概述
AI 驱动的动画分镜工作流：YAML 剧本 -> 角色设定图 / 分镜图 / 过渡动画 / 配音 -> ffmpeg 合成最终视频。
后端 FastAPI（端口 8000）+ 前端 Vue/Vite（端口 7777）。

## 核心服务
- **Agnes AI**（`apihub.agnes-ai.cn`，国内直连无需代理，三模态免费）：文生图/图生图/视频/文本。`src/api/agnes_client.py`。
- **edge-tts**：TTS 语音合成（免费，无需 key）。`src/api/edge_tts_client.py`。
- **rembg**（isnet-anime）：本地抠图。`src/segmentation/rembg_module.py`。
- **ffmpeg**：最终合成。`src/pipeline/video_composer.py`。

## 常用命令
```bash
# 后端（Python 路径有空格，注意引号）
cd animation-storyboard && "D:/Delta Force/python.exe" api_server.py        # 端口 8000

# 前端
cd animation-storyboard && npm run dev                                       # 端口 7777

# 测试（不真调 Agnes，mock 拦截）
cd animation-storyboard && "D:/Delta Force/python.exe" -m pytest tests/ -q   # 28 passed
```

## 重启后端的时机
改了 `api_server.py` / `routers/*.py` / `services/*.py` / `src/api/*.py` / `src/pipeline/*.py` / `src/core/*.py` / `config/*.yaml` / `.env` -> **重启后端**。
只改 `src/views/*.vue` / `src/api/index.ts` / 样式 -> Vite 热更新，**不用重启**。

## 架构要点（阶段5 重构后）
- **入口** `api_server.py`（~126 行）= app + 中间件 + startup + `include_router`(8)。
- **routers/**：system / projects / script / characters / compose / jobs / shots + `services/lifecycle.py`。
- **deps.py**：进程级单例 + getter。`get_agnes_client()` / `get_project_manager()` / `get_job_queue()` / `get_rehost_host()`。
  reassigned 单例用 getter（请求时读 global），dict 单例（`JOB_HANDLERS` / `task_store`）用 `from deps import`。
- **shot 级管线**：Project/Scene/Shot 三层（`src/core/models.py`），Shot 是最小单位。`ProjectStore`（`src/core/store.py`）原子写 + v1->v2 迁移。**所有生成结果写回必须走 `store.update_shot()`**，禁止只改内存。
- **JobQueue**（`src/core/job_queue.py`）：视频串行 worker（1 RPM）+ 轻任务并发 3。全局单例，跨项目共用。崩溃恢复：`recover_pending_videos` 续轮询不重提交。
- **controller.py**：现仅作"配置与客户端工厂"（startup 构造后把 agnes_client/project_manager 拆到 deps）。构造逻辑含 `${AGNES_API_KEY}` env 替换。

## 关键约定（实测踩坑，别违反）
- **AgnesClient** 只接受 `size_tier`/`ratio`（不是 `size=`），返回 `{url, b64}`（无 data 字段）。垫片已全清。
- **图片资产 URL**（`platform-outputs.agnes-ai.space`）是 GCS 公开读对象，下载**不带 Authorization**（带则 401），URL 不过期。
- **视频 URL fallback**：`remixed_from_video_id -> url -> video_url`（.cn 直生成 remixed 为 null，URL 在 url 字段）。
- **num_frames** 必须 8n+1，≤441（720p 档；1080p=169 待实测）。
- **response_format** 只能放 `extra_body`，不在顶层。
- **imgbb** 图床：本地首帧补公网 URL（100% 保真，按文件哈希缓存），`get_rehost_host()`。
- **内容审核**：Agnes 拒 "pure/solid white background" 组合（疑似种族过滤）。角色页 prompt 用 "pure white backdrop"。排查工具 `scripts/probe_content_moderation.py`（通用二分排除）。

## 已知技术债（不阻塞使用，记下不动）
- `routers/characters.py`（878 行）/ `routers/shots.py`（940 行）超 400 行--功能面大（13/11 接口）所致，非组织混乱。下次往这俩文件加新接口时顺手拆。
- `src/store/index.ts` 有 4 个 pre-existing TS 类型错（非本轮引入，Vite 用 esbuild 不检查，不影响运行）。
- `src/pipeline/controller.py` 工厂容器未删（构造含 env 替换，迁移须真实后端验）。低优先。

## 文档
- `详细流程文件/AGNES_API_FACTS.md`：Agnes API 真相源（实测结论，计费/尺寸/限流/字段）。
- `详细流程文件/HANDOVER.md`：阶段5 交接（含垫片1/2 验证规则：改请求参数必须实量生成图尺寸）。
- `详细流程文件/PHASE_*.md`：阶段1-5 执行计划。
