# Agnes 模型契约与限额速查（model-capability-brief）

事实截至 2026-10-07（真机探针 `_p49_a1_probe.py` 抓包 + 官方 wiki 当日核读）。
价格与免费状态随时可能变化——生产前发现回执与本文不符时，以真实回执为准
并更新本文，不凭记忆继续写。

## agnes-image-2.5-flash（图像）

- 端点 `POST /v1/images/generations`；**所有输出档位（1K/2K/3K/4K）与参考
  图片当前免费（¥0）**。
- 支持：文生图、图生图、多图合成（`extra_body.image` 放参考图，URL 或
  data URI；`response_format` 只能在 extra_body 里）。
- `size` 档位式 1K–4K（兼容历史像素写法，会被标准化）；`ratio` 可选
  （16:9/9:16/1:1/…，默认 1:1）。
- 探针实证：t2i 1K 档 200，PNG 1.3MB 落盘成功（2026-10-07）。

## agnes-video-2.5-flash（视频，本轮免费主链）

- **当前限时免费（¥0/秒）**；720P 固定档。
- `seconds`：字符串 "4"–"12"，默认 "5"；**本轮生产政策 ≥8（见 SKILL.md）**。
- `mode` 三选一，**互斥**：
  - `text`：纯文生视频，不带任何媒体字段；
  - `keyframe`：`first_frame` / `last_frame` 至少一个（双帧=两端钉住）；
  - `reference`：`images`（≤5）或 `audios`（≤3）至少一类，语义参考不锁首帧；
    不得带 first_frame/last_frame。
- **Flash 不接受视频参考输入**（videos 字段不进 Flash 请求）。
- 探针实证：text 模式 seconds="8" → 200 受理；下载 mp4 ffprobe 实测 8.0s；
  内含 AAC 原生音轨（2026-10-07）。
- 提交后按 video_id/task_id 轮询；轮询走文本桶不占视频桶。

## agnes-video-2.5（标准版）

- 多模态参考（含视频参考）与生成**当前收费**——**不在本轮免费主链内**；
  不作为免费路径的方法菜单项。免费请求失败时**不得自动切换到收费模型**
  （系统无 fallback，见 config.yaml 红线注释；这是政策也是实现事实）。
- 后续若官方将其标记免费，再按当时事实扩展方法菜单。

## 文本模型与 Thinking

- 文本默认 `agnes-3.0-flash`；视觉审查模型 `agnes-2.5-flash`（独立配置）。
- **Thinking 只进文本请求**：出站体 `chat_template_kwargs.enable_thinking=true`
  （探针抓包实证，响应含 reasoning_content）。
- **图像与视频请求不带任何 thinking 字段**（探针抓包实证：两类请求体中
  thinking 字段为空）。文本与视觉审查调用一律 thinking=True。

## 配额（Token Plan 口径，账本 cache/quota.json）

- 视频 500 秒/天；生图 4000 张/天；文本按滚动窗口计（账本按日）。
- 提交前预检、提交成功后扣减（本地账本口径）；真实剩余以服务端为准——
  本地拒绝时区分"账本口径拦截"与"服务端错误"，不伪报可用。

## 出站自检清单（每次构造请求后过一遍）

1. 视频请求：mode 与图参数一一对应；keyframe 与 reference 字段不同时出现；
   seconds 为字符串且 ≥8（本轮政策）。
2. 图像/视频请求体：无 thinking/chat_template_kwargs 字段。
3. 文本请求：thinking=True 时 enable_thinking=true 已在出站体。
4. 模型 id 与免费主链一致（视频只有 agnes-video-2.5-flash）。
