# 阶段 4 执行计划（Frontend 接入 Phase 3 shot 级管线）

> 依据 `PHASE_4_FRONTEND.md`（待写）+ 阶段 3 已落地的后端接口。前置：阶段 3 已验收（tag phase-3-done）。
> 本计划聚焦：把 phase 3 的 shot 级接口与全局 SSE 接到前端，让用户能跑完 first_frame -> choose -> audio -> last_frame -> video -> compose 全流程并实时看队列。

## 0. 范围与原则
- 新增：`composables/useJobStream.ts`（全局 SSE）、`store/jobs.ts`（队列状态）、`api/index.ts` 补 phase 3 方法、`views/storyboard` 改造为 shot 级候选点选、`components/QueuePanel.vue`、`components/OrchestrateModal.vue`。
- 不改后端（阶段 3 已稳定）。前端 Vite 热更新，改 `.vue`/`.ts` 不需重启后端。
- 沿用 Naive UI + Pinia + 现有 `src/utils/media.ts` 图片 URL 工具。

## 1. 🔴 SSE 重连/刷新规范（核心，必须先定）

后端 `GET /api/jobs/stream?project=<p>` 已是「全局单 SSE + snapshot」：
- 连接建立时**第一帧**发 `event: snapshot`，data = `list_jobs(project)`（当前完整队列数组）。
- 之后每次任务状态变化发 `event: job`，data = 单个 job + `queue` 统计。
- 每 15s 无事件发 `: keepalive` 注释（防代理超时）。

**客户端必须这样处理，否则刷新/重连后队列面板空白：**

```ts
// composables/useJobStream.ts
export function useJobStream(project: string) {
  const jobs = ref<Job[]>(([]));        // 完整队列（snapshot 替换，job 增量 upsert）
  const queue = ref({video_queued:0, video_eta_s:0, running:0, failed:0});
  let es: EventSource | null = null;

  function connect() {
    es = new EventSource(`${API_BASE}/api/jobs/stream?project=${encodeURIComponent(project)}`);
    // 🔴 snapshot：替换整个队列。每次（重）连接都会收到，必须每次都替换，不能只在首次 merge。
    es.addEventListener('snapshot', (ev) => {
      jobs.value = JSON.parse(ev.data);          // REPLACE，不是 concat/merge
      // queue 统计可从 snapshot 不含，由后续 job 帧补；也可 snapshot 时不更新 queue
    });
    // 🔴 job：单条增量 upsert
    es.addEventListener('job', (ev) => {
      const j = JSON.parse(ev.data);
      const i = jobs.value.findIndex(x => x.job_id === j.job_id);
      if (i >= 0) jobs.value[i] = j; else jobs.value.unshift(j);
      if (j.queue) queue.value = j.queue;        // 附带的队列统计
    });
    es.onerror = () => { /* EventSource 会自动重连；重连后服务端再发 snapshot，自动恢复完整状态 */ };
  }
  function close() { es?.close(); es = null; }
  onMounted(connect); onUnmounted(close);
  return { jobs, queue, close };
}
```

**三条铁律：**
1. **snapshot = REPLACE，不是 merge。** 刷新页面 / EventSource 自动重连 / 切换项目重连，都会触发新的 snapshot。客户端必须用 snapshot 整体替换 `jobs.value`，否则：重连后旧的 done/failed 任务残留在 UI、新提交的任务丢失、队列与现实不一致。
2. **job = UPSERT。** 按 `job_id` 更新或插入单条。`j.queue` 附带的统计覆盖 `queue.value`。
3. **不要用 `onmessage`。** 后端用了具名事件（`event: snapshot` / `event: job`），必须用 `addEventListener('snapshot'/'job')`；`onmessage` 只收无 event 名的消息，会漏掉所有数据。keepalive 注释 `: keepalive` 不会触发任何监听器，自动忽略。

**刷新场景验证：** 用户 F5 刷新页面 -> EventSource 重新创建 -> 服务端发 snapshot -> `jobs.value` 被替换为当前完整队列 -> 面板立即显示所有运行中/排队/完成/失败任务。不会空白。

**心跳（已实现，确认）：** 后端 `jobs_stream` 每 15s 无事件时发 `: keepalive\n\n` 注释帧（`asyncio.wait_for(q.get(), timeout=15)` 超时分支）。SSE 注释帧 EventSource 不触发任何监听器，自动忽略，但**它确实刷新了 TCP 连接的活跃状态**，防止代理/浏览器在长任务中途掐断空闲连接。前端无需处理。长任务中 poll 进度每 5s 推 `job` 事件，连接本身也是活跃的；心跳是 idle 期（无任务）的兜底。

**🔴 SSE 彻底失败时降级为定时轮询（必须实现）：** EventSource 自动重连，但若服务端长时间下线/网络断，`es.readyState` 停在 `CLOSED` 不再重连，或连续 N 次 onerror。此时不能让界面停在「最后一次收到的状态」装作还在跑。前端 `useJobStream` 需：
```ts
let pollTimer: number | null = null;
let failedStreak = 0;
es.onerror = () => {
  failedStreak++;
  if (failedStreak >= 3) {        // 连续 3 次错误 -> 切轮询
    es?.close(); es = null;
    startPolling();               // 降级：每 5s GET /project/{p}/jobs
  }
};
function startPolling() {
  pollTimer = window.setInterval(async () => {
    const r = await fetch(`${API_BASE}/api/project/${project}/jobs`);
    const j = await r.json();
    jobs.value = j.jobs; queue.value = j.queue;   // 整体替换（同 snapshot 语义）
    // 轮询期间定期尝试重连 SSE；一旦 SSE 恢复，停轮询
    tryReconnectSSE();
  }, 5000);
}
function tryReconnectSSE() {
  const test = new EventSource(`${API_BASE}/api/jobs/stream?project=${project}`);
  test.addEventListener('snapshot', () => {
    test.close();
    stopPolling(); failedStreak = 0; connect();   // SSE 恢复，回切
  });
  test.onerror = () => test.close();
}
```
- 降级时 UI 显示「⚠️ 实时连接断开，轮询中（5s）」徽标，让用户知道状态不是实时。
- 轮询整体替换 `jobs.value`（同 snapshot 语义），不增量，保证与服务器一致。
- SSE 恢复后自动回切，停轮询。

## 2. store/jobs.ts（Pinia）
- `jobs: Job[]`、`queue: QueueStats`、`connected: boolean`。
- actions：`upsertJob(j)`、`replaceAll(list)`、`clearFinished()`（调 `DELETE /jobs/finished` 后清本地）、`retry(job_id)`、`cancel(job_id)`、`retryFailed()`。
- getters：`byShot(shot_id)`、`running`、`failed`、`videoQueued`。
- 由 `useJobStream` 喂数据；store 本身不连 SSE（解耦：测试时可手动喂）。

## 3. api/index.ts 补 phase 3 方法
```
POST /project/{p}/shot/{id}/first-frame        {variants, force} -> {job_id}
POST /project/{p}/shot/{id}/first-frame/choose {variant}         -> {selected_index, url}
POST /project/{p}/shot/{id}/last-frame                           -> {job_id}
POST /project/{p}/shot/{id}/video                                -> {job_id}
POST /project/{p}/orchestrate                  {stages,scope,skip_locked,dry_run,...} -> plan/job_ids/eta
GET  /project/{p}/jobs?state=                                     -> {jobs, queue}
POST /jobs/{id}/cancel | /jobs/{id}/retry
POST /project/{p}/jobs/retry-failed | DELETE /project/{p}/jobs/finished
GET  /project/{p}/timing                                          -> 各镜 planned/num_frames/needs_split
```
TypeScript 接口定义 `Job`/`JobState`/`QueueStats`/`OrchestrateResult`。

## 4. UI：分镜画廊改造（views/storyboard/index.vue）
按 shot 展示卡片（不再是 scene 级）。每张卡片：
- **首帧候选区**：`first_frame.candidates` 缩略图横排（最多 2 张），当前选中高亮（`selected_index`）。
  - 「生成首帧」按钮 -> POST /first-frame -> 卡片进入「生成中」（job 进度由全局 SSE 推送）。
  - 候选出现后，点缩略图选中 -> POST /first-frame/choose {variant} -> 卡片标「已选定」，`url_ready=true`。
  - 🔴 **裁切安全框（必做）**：图片接口只能出 1312×736（ratio 1.7826），视频输出是 1280×704（ratio 1.8181，实测归一化，见 FACTS §3.4.1）。用户选图时看到的是 1312×736，成片会被中心裁切到 1.8181。候选预览上**叠加安全框**：全宽矩形、上下各内缩 ~7.2px（1312/1.8181≈721.6，736-721.6=14.4，对半），框外（上下细条）用半透明遮罩标「将被裁掉」。让用户选图时把主体放在框内。组件 `components/SafeFrameOverlay.vue`，按 1312×736→1280×704 算比例（top/bottom inset = (1 - 721.6/736)/2 ≈ 0.98%），缩略图与放大预览都叠。尾帧预览同理。
- **尾帧区**（仅 link_mode=keyframes）：「生成尾帧」按钮 -> POST /last-frame。展示 last_frame 缩略图。
- **配音状态**：audio.ready 显示 ✓ + 时长；未配音显示「去配音」（跳 audio 页）。
- **视频区**：「生成视频」按钮 -> POST /video。video.status=generating 显示轮询进度（SSE 推 poll 消息）；done 后内嵌 `<video>` 预览 `video.local`。展示 `seconds_returned`/`size_returned`（回读值，不是 planned）。
- **link_mode 选择**：下拉 i2v/keyframes/none（默认 auto_link_mode 推荐值，可手改）。
- 🔴 Preflight 友好提示：点「生成视频」时若首帧未选/未配音，前端先拦（调 dry_run 或本地校验），给明确文案，别让用户等 job 失败才知道。

## 5. UI：QueuePanel.vue（全局队列面板，常驻 MainLayout 右侧或顶部）
- 订阅 `useJobStream(currentProject)`。
- 显示：`running` 个运行中、`video_queued` 个视频排队（`video_eta_s` 预估）、`failed` 个失败。
- 任务列表：每条 job 一行（kind 图标 + shot_id + 状态 + message + 进度条 + 取消/重试按钮）。
- 「重试所有失败」按钮 -> retryFailed()。「清理已完成」按钮 -> clearFinished()。
- 🔴 刷新不空白：依赖 §1 的 snapshot REPLACE。

## 6. UI：OrchestrateModal.vue（一键编排 + dry_run 预估）
- 选 stages（多选：first_frame/audio/last_frame/video/compose）、scope（all/pending/指定）、skip_locked。
- 「预估」按钮 -> POST /orchestrate {dry_run:true} -> 展示：本次将生成 N 个视频、预计 X 分钟（n×70s）、消耗 Y 秒配额、plan 列表。
- 「执行」按钮 -> POST /orchestrate {dry_run:false} -> 任务进队列（QueuePanel 实时显示）。
- 🔴 1 RPM 现实下，dry_run 预估是产品灵魂，必须显著展示视频数 + 预计时间。

## 7. 与现有页面整合
- `views/audio`：保留，shot 级配音（POST /shot/{id}/audio + 批量）。配音后 num_frames 写回，storyboard 卡片的「配音状态」联动。
- `views/animation`：改成展示已生成 video（按 shot），或合并进 storyboard 卡片。建议合并，减少跳转。
- `views/dashboard`：加「全局队列概况」卡片（running/queued/failed 计数，接 useJobStream）。
- `MainLayout`：导出视频按钮接 /orchestrate {stages:[compose]} 或直接 /compose-shots。

## 8. 验收清单
- [ ] 刷新页面后 QueuePanel 立即显示完整队列（snapshot REPLACE 验证）。
- [ ] 关闭后端再启动，前端 EventSource 自动重连，队列恢复完整。
- [ ] first_frame 生成 2 候选 -> 点选 -> `first_frame.url` 写回，卡片标已选定。
- [ ] 未选首帧点「生成视频」-> 前端拦截明确提示，不发起 job。
- [ ] video 生成中实时显示 poll 进度（SSE），完成后预览 + 展示 seconds_returned/size_returned。
- [ ] orchestrate dry_run 显示视频数/eta/配额；执行后任务进队列。
- [ ] 单镜失败 -> QueuePanel 标红 + 重试按钮 -> 重试单独重跑不影响其它。
- [ ] 切换项目时 SSE 重新订阅（旧连接 close，新 project snapshot 替换）。
- [ ] 候选预览叠安全框（1312×736 上下细条遮罩「将被裁掉」），用户能据此把主体放框内。
- [ ] SSE 彻底失败（连续 3 次 onerror）-> 降级轮询 GET /jobs（5s），UI 标「轮询中」；SSE 恢复自动回切。

## 9. 提交计划（每个可验证小步一个 commit）
1. `feat(api-client)`: api/index.ts 补 phase 3 方法 + TS 接口
2. `feat(store)`: store/jobs.ts（jobs/queue/upsert/replace/clear/retry）
3. `feat(composable)`: useJobStream.ts（全局 SSE + snapshot REPLACE + 心跳忽略 + 降级轮询）
4. `feat(ui)`: QueuePanel.vue + 接入 MainLayout
5. `feat(ui)`: storyboard 卡片改造（候选点选 + 裁切安全框 SafeFrameOverlay + 尾帧 + 视频预览 + preflight 拦截）
6. `feat(ui)`: OrchestrateModal.vue + dry_run 预估
7. `feat(ui)`: dashboard 队列概况 + 切项目重订阅
8. 跑验收 -> tag phase-4-done

## 10. 风险与注意
- **EventSource 无 Authorization 头**：后端 CORS 已 `allow_origins=["*"]`，SSE 走 query param `project`，不需鉴权头。若后续加鉴权，EventSource 不支持自定义头，需改用 fetch+ReadableStream 或 token 走 query。
- **浏览器 6 连接上限**：全局单 SSE（一条连接）正是为避开此限制；不要退回每任务一连接。
- **snapshot 体积**：list_jobs 默认 limit 300，大项目可能较大；如需可加 `?state=` 过滤或分页。当前够用。
- **keepalive 注释**：`": keepalive\n\n"` 是 SSE 注释行，EventSource 不触发 onmessage/addEventListener，自动忽略，无需处理。
