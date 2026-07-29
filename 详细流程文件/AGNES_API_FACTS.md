# AGNES_API_FACTS.md — Agnes AI 接口事实基准

> **本文件是唯一真相来源（Single Source of Truth）。**
> 任何阶段文档、任何代码实现，如果与本文件冲突，以本文件为准。
> 如果本文件与你的记忆冲突，以本文件为准（你的训练数据里没有 Agnes）。
> 数据来源：用户上传的三份官方文档（大模型 / 生图 / 生视频）+ 官方 Token Plan FAQ（agnes-ai.com/zh-Hans/docs/tokenplan，2026-06-28 版）。

---

## 0. 基础信息

| 项 | 值 |
|---|---|
| Base URL | `https://apihub.agnes-ai.com` |
| 认证 | `Authorization: Bearer YOUR_API_KEY` |
| Content-Type | `application/json` |
| 文本模型 | `agnes-2.0-flash` |
| 图片模型 | `agnes-image-2.1-flash`（另有 `agnes-image-2.0-flash`） |
| 视频模型 | `agnes-video-v2.0` |
| 价格 | 当前全部 $0（文本 $0/1M token、图片 $0/张、视频 $0/秒） |

**模型名必须走配置文件，不许硬编码在业务代码里。** Agnes 迭代很快，新版本随时可能上线。

---

## 1. 文本接口 `/v1/chat/completions`

OpenAI 兼容。

- 必填：`model`、`messages`
- 可选：`temperature`、`top_p`、`max_tokens`、`stream`、`tools`、`tool_choice`
- 支持图片理解：`messages[].content` 用数组，元素 `{"type":"image_url","image_url":{"url":"..."}}`，**图片 URL 必须公网可访问**
- 开启思考：OpenAI 风格 `chat_template_kwargs: {"enable_thinking": true}`；Anthropic 风格 `thinking: {"type":"enabled","budget_tokens":2048}`
- Context 256K，Max Output 65.5K
- 响应：`choices[0].message.content`

---

## 2. 图片接口 `/v1/images/generations`

### 2.1 必填 / 选填

| 参数 | 位置 | 说明 |
|---|---|---|
| `model` | 顶层 | 必填 |
| `prompt` | 顶层 | 必填 |
| `size` | 顶层 | 必填 |
| `return_base64` | 顶层 | **仅文生图**要 base64 时用 `true` |
| `extra_body.response_format` | extra_body | `"url"` 或 `"b64_json"` |
| `extra_body.image` | extra_body | 图生图输入图数组 |

### 2.2 🔴 三个必须记住的坑

**坑 1：`response_format` 绝对不能放顶层。** 放顶层返回 400。只能放 `extra_body`。

**坑 2：图生图 `image` 数组的位置，官方文档自相矛盾。**
- 文档正文和"备注"章节说：*"请将输入图片放在顶层 image 数组中"*
- 文档里 5 个 curl 示例（示例 3/4/5 和两个"正确示例"）全部放在 `extra_body.image`

**实现要求：写成"先 extra_body，收到 400 再 fallback 顶层"的双位置兼容，并把成功的那个位置缓存下来，同一进程内后续请求直接用。** 不许赌其中一个。

**坑 3：不要传 `tags: ["img2img"]`。** 文档明确说图生图不需要这个字段。

### 2.3 size：两种写法，优先用档位

生图文档给的是精确尺寸写法（如 `"1024x768"`），但官方 Token Plan 文档说明了更重要的事实：

> 如果请求 `1920x1080` 或 `2560x1440` 这类不受支持的精确尺寸，服务可能会自动标准化到最接近的输出档位和宽高比。

**并且图片 RPM 是按档位分级的**（见 §4）。所以推荐用档位式：

```json
{ "size": "1K", "ratio": "16:9" }
```

档位对照表：

| Ratio | 1K | 2K | 3K | 4K |
|---|---|---|---|---|
| `1:1` | 1024x1024 | 2048x2048 | 3072x3072 | 4096x4096 |
| `3:4` | 864x1152 | 1728x2304 | 2592x3456 | 3456x4608 |
| `4:3` | 1152x864 | 2304x1728 | 3456x2592 | 4608x3456 |
| `16:9` | 1312x736 | 2624x1472 | 3936x2208 | 5248x2944 |
| `9:16` | 736x1312 | 1472x2624 | 2208x3936 | 2944x5248 |
| `21:9` | 1568x672 | 3136x1344 | 4704x2016 | 6272x2688 |

**本项目一律用 1K 档**（20 RPM，最划算；2K 只有 10 RPM，4K 只有 1 RPM）。
16:9 → `1312x736`，9:16 → `736x1312`。
注意这和项目现在配置的 `1152x768` 不一致，**必须改**，否则会被服务端归一化，导致本地记录的尺寸和实际产出对不上。

### 2.4 输入图格式

图片接口的 `image` 数组支持两种：
1. 公网可访问的 HTTPS URL
2. Data URI Base64：`data:image/png;base64,BASE64_HERE`

**注意：这是图片接口的能力。视频接口不支持 base64（见 §3.3）。**

### 2.5 响应

```json
{ "created": 1780000000,
  "data": [ { "url": "https://storage.googleapis.com/agnes-aigc/xxx.png",
              "b64_json": null, "revised_prompt": null } ] }
```
- URL 模式取 `data[0].url`
- Base64 模式取 `data[0].b64_json`

### 2.6 超时

官方建议客户端超时设 **60s ~ 360s**。本项目取 300s。

---

## 3. 视频接口

### 3.1 创建任务 `POST /v1/videos`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | string | 是 | `agnes-video-v2.0` |
| `prompt` | string | 是 | |
| `image` | string / array | 否 | **顶层**，图生视频用单个 URL 字符串 |
| `mode` | string | 否 | `ti2vid` / `keyframes` |
| `width` | int | 否 | 默认 1152 |
| `height` | int | 否 | 默认 768 |
| `num_frames` | int | 否 | **≤ 441 且必须满足 8n+1** |
| `frame_rate` | number | 否 | 1–60 |
| `num_inference_steps` | int | 否 | |
| `seed` | int | 否 | |
| `negative_prompt` | string | 否 | |
| `extra_body.image` | array | 否 | 多图 / 关键帧模式的图片 URL 数组 |
| `extra_body.mode` | string | 否 | `keyframes` |

### 3.2 三种调用形态（照抄官方示例结构）

**A. 文生视频** — 只传 model + prompt（+ 尺寸帧数）

**B. 图生视频（单图动画化）** — 顶层 `"image": "https://..."`（**字符串，不是数组**）

**C. 关键帧动画** — `extra_body.image` 是数组 + `extra_body.mode: "keyframes"`
```json
{ "model":"agnes-video-v2.0", "prompt":"...",
  "extra_body": { "image": ["https://.../first.png","https://.../last.png"],
                  "mode":"keyframes" },
  "num_frames":121, "frame_rate":24 }
```

**D. 多图指导** — `extra_body.image` 是数组，**不带** mode

### 3.3 🔴 视频接口的图片必须是公网可访问 URL

官方"准备工作"章节原文要求：*"如需使用图生视频、多图视频或关键帧动画，请准备可公网访问的图片 URL。"*

**视频接口没有任何地方提到支持 Data URI base64。** 这是整个项目最大的架构约束，直接导致必须有 ImageHost 抽象层（见阶段 1）。

#### 3.3.1 "用 Agnes 图生图把本地图洗成公网 URL" 的保真待测（阶段 3 回答）

参考项目 `agnes_video.py::_upload_image_to_url` 用图片接口（i2i + prompt "Keep the image exactly as is"）把本地图片重新生成成一个公网 URL，**零配置图床**，是 ImageHost 的一种潜在实现（`agnes_rehost`）。但它在视频管线里能不能用，取决于两个**尚未实测**的保真问题：

1. **输出是否保持原始宽高比？**（图片接口会要求 `size`，可能强行重采样）
2. **输出图与输入图是否逐像素一致 / 视觉一致？**（i2i 是"尽量保持构图"，不是无损复制）

**结论规则（阶段 3 实测后填写）**：
- 若保真（宽高比不变 + 视觉一致）-> 可用于首帧/尾帧/参考图，`agnes_rehost` 作为默认 ImageHost。
- 若不保真 -> **只能用于参考图，不能用于尾帧接力**（尾帧逐帧一致性要求高）。此时优先做 `tunnel` 方案（cloudflared 隧道，零注册、原图直出保真，但隧道断 URL 失效，适合调试与中短期跑）。

> 本阶段（阶段 1）**不实现** `agnes_rehost`，先留接口位。

### 3.4 时长计算

```
seconds = num_frames / frame_rate
```
- `num_frames ≤ 441`
- `num_frames` 必须是 8n+1（81 / 121 / 161 / 241 / 441 ...）
- `frame_rate` ∈ [1, 60]

24fps 常用值：

| 目标时长 | num_frames | frame_rate |
|---|---|---|
| ~3s | 81 | 24 |
| ~5s | 121 | 24 |
| ~10s | 241 | 24 |
| ~18s | 441 | 24 |

**24fps 下单条上限 = 441/24 = 18.375 秒。** 想更长只能降 fps（例如 441/22 ≈ 20s，这也是开源项目 README 写"20s per clip"的由来）。

#### 3.4.1 num_frames 的"全局上限"与"按分辨率上限"

`num_frames ≤ 441` 是**全局绝对上限**。但视频生成算力随像素数增长，**分辨率越高，实际允许的 num_frames 越低**——超过会被服务端 400 拒绝。因此代码里不要写死 441，要写成 `max_frames_for(width, height)`：

```python
ABS_MAX_FRAMES = 441
def max_frames_for(width, height):
    pixels = width * height
    if pixels > 1280*720: return 169    # 1080p 档
    if pixels > 854*480:  return 409    # 720p 档
    return 961                           # 480p 档
```

| 分辨率档 | 像素阈值 | num_frames 上限 | 24fps 下时长上限 |
|---|---|---:|---:|
| 1080p | > 1280×720 | 169 | ~7.0s |
| 720p | > 854×480 且 ≤ 1280×720 | 409 | ~17.0s |
| 480p | ≤ 854×480 | 961 | ~40s |

> ⚠️ **上表分档值参考自 lcy362/agnes-video-generator 实测（来自 Agnes 400 报错），尚未在本项目实测确认。** 待 Clash 起来后用 `create_video` 在 1312×736（本项目默认 1K 16:9，像素 965632 > 1280×720，按上表落 1080p 档=169）实测以下值并回填本表：
> - 1312×736 的真实 num_frames 上限（按表是 169，但 1312×736 仅略超 1280×720，可能实际归到 720p 档=409，需确认）。
> - 各档边界的准确像素阈值。
>
> 即便 `max_frames_for` 初值不准，`create_video` 已带 **400 自动降帧重试**（命中 400 且报错含 num_frames 时按 0.7×降到下一个 8n+1 重试，最多 3 次）作为兜底，运行时仍能发现真实上限。

### 3.5 查询结果（推荐方式）

```
GET https://apihub.agnes-ai.com/agnesapi?video_id=<VIDEO_ID>
Authorization: Bearer <API_KEY>
```
**官方建议轮询间隔 5 秒。**（项目现在配的 10 秒，改成 5。）

可选参数 `&model_name=agnes-video-v2.0`。

兼容旧方式：`GET /v1/videos/{task_id}`。新代码用 `video_id` 方式。

### 3.6 创建响应 & 结果响应

创建时返回：
```json
{ "id":"task_xxx", "task_id":"task_xxx", "video_id":"video_xxx",
  "object":"video", "model":"agnes-video-v2.0",
  "status":"queued", "progress":0, "created_at":1780457477,
  "seconds":"10.0", "size":"1280x768" }
```

完成时返回：
```json
{ "id":"task_xxx", "video_id":"video_xxx", "status":"completed", "progress":100,
  "seconds":"10.0", "size":"1280x768",
  "remixed_from_video_id":"https://storage.googleapis.com/.../video_xxx.mp4",
  "error": null }
```

**🔴 `remixed_from_video_id` 就是最终 mp4 的 URL**（字段名极具误导性，别当成 ID）。仅在 `status == "completed"` 时可用。

**🔴 `seconds` 和 `size` 是服务端归一化后的真实值，必须回读入库。** 提交的 `num_frames`/`width`/`height` 可能被改。前端展示的时长一律用回读值，不许用提交值。

### 3.7 状态机

`queued` → `in_progress` → `completed` / `failed`

### 3.8 视频接口错误码

文档列出：400 / 401 / 404 / 500 / 503。
**但网关层仍可能返回 429、502、504、520、522、524，实现时一并按可重试处理。**

---

## 4. 🔴🔴 速率限制与配额（架构级约束）

来源：官方 Token Plan FAQ，生效日期 2026-06-22，视频 RPM 更新于 2026-06-28。

### 4.1 免费 / 默认用户（本项目的实际情况）

| 模型 | 分辨率档 | 允许发起 RPM | **实际 RPM** |
|---|---|---:|---:|
| 文本 | - | 30 | **20** |
| 图片 | 1K | 30 | **20** |
| 图片 | 2K | 20 | **10** |
| 图片 | 3K | 2 | **1** |
| 图片 | 4K | 1 | **1** |
| **视频** | - | 2 | **🔴 1** |

### 4.2 这意味着什么

**视频 1 RPM 是本项目最硬的约束，它决定架构而不是决定参数。**

- 一个 20 镜头的项目，光提交任务就要排 **20 分钟**，且完全不能并发
- 前端点"全部生成"时并发提交 20 个 → 19 个失败
- 所以必须有：全局令牌桶限流器 + 单进程串行队列 + 排队可视化 + 断点续跑
- 「多版本抽卡择优」策略只适用于图片（20 RPM），**绝对不能用在视频上**

### 4.3 配额

- 视频还额外受**每日生成秒数**配额约束（Token Plan 用户是每天 500 秒；免费用户官方未公布具体数值，**按存在配额来设计**）
- 图片按**张数**计
- 文本按**请求次数**计

### 4.4 限制池是按「密钥类型」共享的

> 创建多个相同类型的密钥，不会增加总 RPM 或总配额。

**所以"多申请几个 key 绕过限流"这条路是堵死的，不要在代码里做多 key 轮询。**

---

## 5. 与项目现有代码的差异清单（必须修正）

| 项 | 项目现状 | 正确做法 |
|---|---|---|
| 轮询间隔 | 10s | **5s** |
| 图片 size | `1152x768` | **`1K` + `ratio`**，16:9 → 1312x736 |
| 图生图 image 位置 | 只写 extra_body | **双位置 fallback** |
| num_frames 校验 | 无 | **8n+1 夹取 + ≤441 校验** |
| seconds/size 回读 | 无（前端写死 '5s'） | **必须回读入库** |
| 限流器 | 无 | **必须有，视频 1 RPM** |
| 配额账本 | 无 | **必须有** |
| 错误重试 | 只处理 429 / timeout | **408/429/500/502/503/504/520/522/524 全部指数退避** |
| 视频输入图 | 计划用本地路径 | **必须公网 URL，走 ImageHost** |
| 视频音频 | 判断为静音 | ✅ 正确，参数表无任何音频字段 |
| `remixed_from_video_id` | 已识别为 URL | ✅ 正确 |

