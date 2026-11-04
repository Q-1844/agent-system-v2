# 阶段 3 执行计划（Video Pipeline）

> 依据 `PHASE_3_VIDEO_PIPELINE.md`。前置：阶段 1、2 已验收通过（tag phase-2-done）。
> 本计划补充文档未写明的点：**split-dialogue 拆出的新镜头 first_frame:pending 如何被分镜图/尾帧逻辑处理**（见 §8）。

## 0. 范围与原则
- 新增：`core/link_mode.py`、`core/prompt_builder.py`、`core/job_queue.py` + 5 个 shot 级 handler + orchestrator + 全局 SSE + 启动恢复。
- 不删旧：`/api/run`、scene 级分镜接口保留，仅标记 deprecated（阶段 5 清理）。
- 沿用阶段 1/2 兼容策略：走 `store.update_shot` 写回；不碰前端（阶段 4）。

## 1. 新增 core 模块
- `core/link_mode.py`：`auto_link_mode(prev, cur)` 按文档（跨场景->i2v，同场景同角色->keyframes，首镜->i2v）。
- `core/prompt_builder.py`：`build_shot_prompt` / `build_i2i_prompt` / `build_last_frame_prompt` / `build_video_prompt`（按文档 §3.2）。prompt 末尾统一拼 `project.style.suffix()`（全片风格地基）。
- `core/job_queue.py`：按文档 §3.5 的 `JobQueue`（视频串行 worker + 图片/音频 3 并发 worker，两条通道互不阻塞），持久化到 `projects/<p>/output/jobs.json`，SSE 广播。

## 2. first_frame 生成（`POST /api/project/{p}/shot/{shot_id}/first-frame`）
handler 流程（按文档 §3.2）：
1. `locked 且非 force` -> 跳过。
2. 取出场角色参考图：`Character.ref_url()` 有 URL 用 URL；只有 local -> 读文件转 `data:image/png;base64,...`（**图片接口支持 base64，AGNES_API_FACTS §2.4，无需 ImageHost**）；都没有 -> 文生图。
3. `variants=2` 调 `agnes_client.generate_image(build_i2i_prompt, image_refs=[ref], size_tier, ratio)` 两次（图片 20 RPM 可抽卡；**不用 LLM 打分**，红线 12）。
4. 下载每张存 `output/frames/{shot_id}_first_v{n}.png`，**同时存 Agnes 返回的公网 URL** 到候选 `Asset.url`。
5. `store.update_shot` 写回 `first_frame.candidates=[Asset(local,url,status=done), ...]`，`first_frame.status=pending`（等用户选）。
6. 候选选择 `POST /shot/{shot_id}/first-frame/choose {variant}` -> 设 `selected_index`，`first_frame.status=done`。

🔴 `first_frame.url_ready`（选中候选有 url）是视频接口的唯一入口。

## 3. last_frame 生成（`POST /api/project/{p}/shot/{shot_id}/last-frame`）
- 仅 `link_mode=="keyframes"`；否则 400。
- 前置 `first_frame.url_ready`。
- `generate_image(build_last_frame_prompt, image_refs=[first_frame.url])` 图生图（用公网 URL 作参考）。
- 存 `output/frames/{shot_id}_last.png` + `last_frame.url`，`store.update_shot` 写回。

## 4. 视频生成（`POST /api/project/{p}/shot/{shot_id}/video`）
按文档 §3.4：
- `preflight`：未配音/缺首帧 URL/keyframes 缺尾帧/配额不足 -> 直接报错，**不发起 API 请求**（省 60s）。
- `mode = keyframes->keyframes, i2v/none->i2v`；keyframes 用 `image_urls=[first.url, last.url]`，i2v 用 `image_url=first.url`。
- `agnes_client.create_video`（自带 1 RPM 限流 + num_frames 8n+1 + 400 降帧重试）。
- 🔴 提交成功**立刻落库 video_id**（崩了重启能接着轮询，不重提交、不重扣配额）。
- `poll_video`（5s 间隔，走 text 桶）-> `download`（无认证 client）-> **回读 `seconds_returned`/`size_returned`** 入库（红线 6）。

## 5. JobQueue + handler 注册 + 启动恢复
- 5 个 handler：`first_frame`/`last_frame`/`audio`/`video`/`compose`。audio/compose 复用阶段 2 已有逻辑（抽成可被 handler 调用的函数）。
- `JobQueue.start()` 在 startup 调；视频任务走串行 worker，图片/音频走 3 并发。
- 🔴 `recover_pending_videos`（文档 §3.4）：重启后对 `video.status=="generating" 且有 `video_id` 的镜头先 `poll_video` 查一次，不盲目重提交。
- 队列接口：`GET /jobs`、`GET /api/jobs/stream`（全局单 SSE，替代按任务订阅）、`POST /jobs/{id}/cancel`、`/retry`、`/jobs/retry-failed`、`DELETE /jobs/finished`。

## 6. Orchestrator（`POST /api/project/{p}/orchestrate`）
- 替代 `/api/run`：内部逐个调 shot 级 handler（已写回正确），白捡断点续跑。
- 顺序不可调换：`first_frame -> audio -> last_frame -> video -> compose`。
- 每阶段只入队 `not ready` 的镜头（`first_frame.ready`/`audio.ready`/`last_frame.ready`/`video.ready` 为 gate）。
- 🔴 `dry_run` 必须实现：返回视频数量、预计时间（n×70s）、配额消耗，让用户在 1 RPM 现实下预判。

## 7. 合成改造
- 阶段 2 的 `VideoComposer.compose_shots` 已是新顺序（concat->整音轨->烧 SRT）。
- 阶段 3 补：视频段（`video.local`）与 Ken Burns 段（`first_frame.local`）混合出片；SRT 烧录用 `escape_ffmpeg_path()`（盘符冒号 `\:`、反斜杠转正斜杠）+ `force_style='FontName=Microsoft YaHei'`，失败降级。

## 8. 🔴 split-dialogue 新镜头 first_frame:pending 的处理（你关心的点）

阶段 2 的 split-dialogue 拆出的新 Shot：`first_frame=ImageAsset()`（candidates 空、status=`pending`）、`last_frame=ImageAsset()`（pending）、继承原镜头的 camera/lighting/characters、`status=pending`、无 audio/video。它怎么走完分镜->视频：

**8.1 link_mode：硬编码 keyframes，不走 auto_link_mode（按你的反馈改）**
- split 拆出的两段定义上就是同场景同角色动作延续（红线 #11 的典型场景），**100% 该接力**，不是"猜"。
- 所以 split-dialogue 里**直接硬编码**：原镜头 O 和所有新镜头 link_mode = `"keyframes"`。
- 不调 `auto_link_mode(原,新)` 的通用启发式--免得它别的判断条件在极端情况误判成非 keyframes。`auto_link_mode` 只用于"两个任意镜头是否该接力"的通用场景。
- 🔴 副作用：O 拆分前若是纯 i2v（只有首帧、没尾帧），拆分后 O 变 keyframes，**需要补一张尾帧**（见 8.3）。

**8.2 分镜图逻辑怎么接住新镜头的 pending first_frame**
- orchestrator 的 first_frame 阶段 gate 是 `not s.first_frame.ready`。
- `ImageAsset.ready` = 选中候选 status=done 且有 local。新镜头 candidates 空 -> `selected=None` -> `ready=False`。**所以新镜头一定被 first_frame 阶段纳入**，不会漏。
- 走 §2 的 first_frame handler：用继承的 characters 取角色参考图（base64 或 URL）-> 图生图出 2 候选 -> 存 local + 公网 url -> 用户点选 -> `first_frame.url_ready=True`。
- pending 不是特殊状态，就是「还没生成分镜图」的正常初态，靠 `ready` gate 自然进入。

**8.3 🔴 补尾帧 + O2 首帧 = O 尾帧（触发点写清楚，别漏）**
- 拆分后 O（seg1）变 keyframes，需要尾帧 = "seg1 动作结束态"。这张尾帧同时是 O2（seg2）的首帧（接力点，保证 O->O2 视觉无缝：O 的末帧 = O2 的首帧）。
- **触发点在 split-dialogue**（不是留到阶段 3 才发现"咦 O 缺尾帧"）：
  - split-dialogue 给 Shot 新增字段 `split_next: Optional[str]`（拆分后继 shot_id）。O.split_next = O2.shot_id（多段时 seg_i.split_next = seg_{i+1}.shot_id）。
  - split-dialogue 设 O 与所有新镜头 link_mode=keyframes + 设 split_next 标记后，**立即入队 O 的 last_frame 生成任务**（JobQueue，图片 20 RPM 够用，红线 #12 禁的是 LLM 打分择优，不禁多生图）。
  - **桥接拷贝规则**（last_frame handler 里）：生成完某镜头 X 的尾帧后，若 `X.split_next` 非空，把该尾帧 Asset（local+url）**拷贝**到后继镜头的 first_frame（后继.first_frame = X.last_frame）。这样 O2.first_frame = O.last_frame，天然无缝。
  - 若拆分时 O 还没有首帧（罕见，比如刚配完音就拆、还没跑 first_frame）：split-dialogue 不立即入队 last_frame（没首帧没法图生图），仅设 split_next + keyframes 标记；phase 3 orchestrator 顺序保证 first_frame -> last_frame，O 首帧生成后 last_frame 阶段再生成桥接尾帧并按 split_next 拷贝给 O2。
- O2 自己的尾帧（seg2 动作结束态，非桥接）：走 phase 3 正常 last_frame gate（`not ready` -> 生成），不靠 split_next。
- 多段（seg1..segN）：seg1..seg_{N-1} 的尾帧都是桥接（生成后按 split_next 拷贝给下一段首帧）；segN 的尾帧是终点，走正常 gate。

**8.4 顺序保证**
- orchestrator 严格 `first_frame -> audio -> last_frame -> video`。新镜头 first_frame 一定在 video 之前生成；O 的桥接尾帧在 last_frame 阶段生成并拷贝给 O2 首帧。
- `video.preflight` 校验 `first_frame.url_ready` + keyframes 还查 `last_frame.url_ready`，缺则报错不提交（省 60s）。

> 一句话：split 新镜头 link_mode 硬编码 keyframes（不走 auto_link_mode）；O 拆分后补的桥接尾帧在 split-dialogue 触发生成（设 split_next 标记 + 入队 last_frame），生成后按 split_next 拷贝给 O2 作首帧，保证 O->O2 无缝；O2 终点尾帧走 phase 3 正常 gate。pending first_frame 靠 `ImageAsset.ready=False` gate 被 first_frame 阶段接住。

## 9. 验收对照（PHASE_3 §3.8）
逐条跑：auto_link_mode 跨/同场景、first_frame 2候选点选+url写回、last_frame 非keyframes返回400、preflight 未配音不请求、3 视频提交间隔≈70s 无429、kill+重启 recover 接着轮询不重提交、seconds_returned/size_returned 写回、单镜失败可重试不影响其它、全局 SSE、dry_run 预估、Ken Burns+视频段混合出片、中文路径字幕烧录。

## 10. 提交计划（每个可验证小步一个 commit）
1. `feat(core): link_mode + prompt_builder`
2. `feat(core): JobQueue（串行视频+并发轻任务，持久化+SSE）`
3. `feat(api): first_frame/last_frame 接口 + handler`
4. `feat(api): video 接口 + preflight + recover_pending_videos`
5. `feat(api): orchestrator + dry_run + 全局 SSE + 队列管理接口`
6. `feat(core): Shot 增 split_next 字段 + 改 split-dialogue`（硬编码 keyframes、设 split_next、入队 O 桥接尾帧；last_frame handler 按 split_next 拷贝给后继首帧）
7. `feat(composer): Ken Burns+视频段混合 + 字幕烧录路径转义`
8. 跑验收 -> tag phase-3-done

## 11. 决策（split 已按你反馈定稿，另两项用我推荐值，可改）
- **split 新镜头 link_mode**：✅ 已定 = 硬编码 `"keyframes"`（不走 auto_link_mode），O 拆分后也变 keyframes 并补桥接尾帧，O2 首帧 = O 尾帧（split_next 标记 + last_frame handler 拷贝）。
- **ImageHost**：phase 3 不强制接（默认 passthrough）。正常流程不需要：首帧来自 Agnes 自带公网 URL，角色参考图用 base64 走图片接口。只在用户本地编辑过首帧、丢公网 URL 时才需要。agnes_rehost 保真问题留实测（你之前定的）。
- **`/api/run`**：保留并标 deprecated（不删），阶段 5 统一清理。
