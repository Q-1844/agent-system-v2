# 阶段 5 交接文档

> 本文件供阶段 5（清理 + 拆分）使用，记录阶段 1-4 遗留的垫片、旧接口、待修项、实测结论。
> 清空对话后这些「为什么保留」的上下文会丢，务必先读此文件再动刀。

---

## 一、完整垫片清单（DEPRECATED-SHIM，阶段 5 移除）

### `src/api/agnes_client.py`

| # | 垫片 | 位置 | 被谁调用 | 当初为什么保留 |
|---|---|---|---|---|
| 1 | `generate_image(size=...)` legacy size 入参 | L181-185 | `character_gen.py`、`storyboard_gen.py`（旧管线读 `config.settings.image.size="1152x768"`） | 阶段 1 整文件重写 agnes_client 时，旧调用方仍用 `size="1152x768"` 入参；新代码用 `size_tier/ratio`。为"只改 agnes_client + 旧接口仍可用"保留，避免阶段 1 就改穿旧管线。 |
| 2 | `generate_image` 返回 `data` 字段 | L225-226 | `character_gen.py`、`storyboard_gen.py`（读 `result["data"][0]["url"]`） | 旧调用方读 `data[0].url`；新代码读 `result["url"]`。同上，避免改穿。 |
| 3 | `understand_text` -> `chat` | L355-362 | `evaluator.py` L64、`script_parser.py` | 旧名，等价 `chat`。evaluator/script_parser 仍在用，改名要同步改这两处。 |
| 4 | `generate_video` -> `create_video`+`poll_video` | L366-373 | `animation_gen.py`（旧 v1 管线） | 旧名：提交+轮询一步到位返回旧形状 dict；新代码 `create_video`+`poll_video` 分两步（提交即落库 video_id，崩了能续轮询）。animation_gen 阶段 5 删。 |

### `src/api/edge_tts_client.py`

| # | 垫片 | 位置 | 被谁调用 | 当初为什么保留 |
|---|---|---|---|---|
| 5 | `synthesize` -> 返回 mp3 bytes（无词级时间戳） | L115-119 | `tts_gen.py`（v1 管线） | 阶段 2 重写为 `synthesize_full`（词级 WordBoundary + ffprobe 实测时长）。tts_gen 仍用旧 `synthesize`，阶段 5 随 v1 管线删。 |

**清理方式**：阶段 5 删 v1 管线（character_gen/storyboard_gen/animation_gen/tts_gen/evaluator）后，这些垫片无人调用，直接删。删前 grep 确认无调用方。

---

## 二、保留的旧接口清单（阶段 5 清理）

| 旧接口 | 位置 | 用途 | 新接口（替代） |
|---|---|---|---|
| `POST /api/run` | api_server.py L1351 | 旧一键流水线（scene 级） | `POST /api/project/{p}/orchestrate`（shot 级，dry_run+断点续跑） |
| `POST /api/project/{p}/audio/{scene_id}/generate` | L785 | scene 级配音 | `POST /api/project/{p}/shot/{id}/audio`（shot 级，反推 num_frames） |
| `POST /api/project/{p}/compose` | L3098 | 旧合成（scenes 级，VideoComposer.compose） | `POST /api/project/{p}/compose-shots`（shot 级，compose_shots） |
| `GET /api/projects/{p}/scenes` | L447 | 旧 scene 级列表 | `GET /api/project/{p}/shots`（v2 结构） |
| `POST /api/projects/{p}/scenes/{id}/regenerate` | L547 | 旧 scene 重生成 | shot 级 `/first-frame` 等 |
| `POST /api/projects/{p}/scenes/{id}/confirm` | L591 | 旧 scene 确认 | （无对应，删） |
| `POST /api/projects/{p}/animations/{from}/{to}/regenerate` | L727 | 旧过渡动画 | shot 级 `/shot/{id}/video` |
| `POST /api/projects/{p}/audio/generate` | L752 | 旧批量配音 | `POST /api/project/{p}/audio/batch` |
| `POST /api/projects/{p}/audio/{id}/regenerate` | L847 | 旧配音重生成 | shot 级 `/shot/{id}/audio` |

**旧 VideoComposer.compose(scenes)**：`video_composer.py` L32（ scenes 级合成，旧 adelay 逐镜偏移）。新 `compose_shots` L192（concat->整音轨->烧SRT，Ken Burns 兜底）。旧 `compose` 被 `/compose` 路由调，阶段 5 随路由删。

**旧 `agnes.settings` 块**（config.yaml L36-54）：`settings.image.size/num_candidates/quality_threshold`、`character_sheet.size`、`video.num_frames/polling_interval` 等。character_gen/storyboard_gen 仍读。阶段 5 随旧管线删。

---

## 三、GCS URL 复访（阶段 5 回归测试必做）

**记录日期**：2026-07-31

| 类型 | URL |
|---|---|
| 图片 | `https://platform-outputs.agnes-ai.space/images/i2i/task_td7ZBL1pqNPG5iqDJQWNX1OzbRcXCCp7/output.png` |
| 视频 1 | `https://platform-outputs.agnes-ai.space/videos/agnes-video-v2.0/task_X7JQUhSIs3GZ6C5Yxux8oYVXPgSxmJhk.mp4` |
| 视频 2 | `https://platform-outputs.agnes-ai.space/videos/agnes-video-v2.0/task_880lCMyrJWkLnhoGf1DmSk6Xu4g4sDvT.mp4` |

**复访判断标准**（阶段 5 回归测试执行）：
- **能打开（HTTP 200）** -> URL 不过期，坐实 `AGNES_API_FACTS.md` L266 结论（GCS 公开读，所有 Agnes 生成图 url 长期有效，**不需要全托管**，只有 v1 迁移/本地编辑的图 url 空才 imgbb 托管）。preflight 维持"url空才托管"。
- **打不开（403/404/过期）** -> URL 会过期，**所有送进视频接口的图都要先 imgbb 托管**（包括 Agnes 刚生成的）。preflight 逻辑要改：url 有也要检查存活，过期重新托管。`AGNES_API_FACTS.md` L266 结论作废，重写。

复访方法：`curl -sI <URL>` 看状态码。3 个 URL 都测。

---

## 四、已知待修（阶段 5）

1. **`storyboard_gen.py` L163 把本地路径当 image_refs 传**
   - L89 `scene["image_path"] = str(filepath)`（本地路径），L163 `image_refs=references` 直接传给 `agnes_client.generate_image`。
   - Agnes image_refs 要公网 URL 或 data URI，本地路径无效。v1 管线 bug。
   - 阶段 5 删 storyboard_gen（被 shot 级 `_handler_first_frame` 替代）或修（传 base64 data URI）。

2. **`evaluator.py` L64 用 LLM 打分选图（违反红线 #12）**
   - L64 `understand_text`（LLM）打分，L71 `score`，L72 `图片评估完成 score/10`。
   - 红线 #12：生成 2 张让用户点选，而非 AI 打分。阶段 4 已实现 `first_frame` 2 候选 + `choose` 用户点选。
   - 阶段 5 删 evaluator（或改用别的用途，如质量提示，不参与选图）。

---

## 五、阶段 1-4 关键实测结论索引（指向 `详细流程文件/AGNES_API_FACTS.md`）

| 主题 | 章节 | 结论 |
|---|---|---|
| 图片档位制 | §2.3 | 1K 16:9 -> 1312×736；图片接口归一化，档位表无 1.8181 |
| response_format 坑 | §2.2 | 只能在 extra_body，不在顶层（坑1） |
| agnes_rehost 保真 | §3.3.1 | **不保真**：i2i "keep as is" 改比例(3:2->16:9)+重采样(mean_diff 6.6)；仅参考图用，默认不启用 |
| num_frames 上限/尺寸归一化 | §3.4.1 | 720p=441 帧；1312×736 被归一化成 1280×704；直提交 1280×704 省归一化 |
| 视频 URL fallback | §3.6 | `remixed_from_video_id` -> `url` -> `video_url`；.cn 直生成 remixed 为 null，url 在 `url` 字段 |
| seconds/size 回读 | §3.6 | 服务端归一化后真实值，必须回读入库，前端展示用回读值 |
| 资产下载认证 | L266 | `platform-outputs.agnes-ai.space` 是 **GCS 公开读**（非预签名，URL 无签名 query **不过期**）；带 Authorization 401。纠正旧"预签名"判断 |
| 限流 | §4 | video 1 RPM（并发=全部 429）；image_1k 20 RPM；text 20 RPM |

---

## 六、阶段 5 回归测试基准项目

- **`projects/_p4_accept`**：阶段 4 副本，跑通过完整链路（rehost imgbb / 重新生成 / 配音 / dry_run / video / compose）。**阶段 5 全部验收通过前不要删**，用作回归基准。
- 阶段 5 回归测试清单（含 GCS URL 复访）做完后再删 `_p4_accept`。

---

## 七、阶段 5 清理顺序建议

1. 删 v1 管线：`character_gen.py` / `storyboard_gen.py` / `animation_gen.py` / `tts_gen.py` / `evaluator.py`（确认无调用方后）
2. 删垫片（本文件第一节 5 个）
3. 删旧接口（本文件第二节，含 `/api/run`、scene 级 audio/compose、旧 scene 级路由）
4. 删旧 `agnes.settings` 块（config.yaml L36-54）
5. `api_server.py` 拆 routers（3000+ 行，按域拆：project/shot/job/image_host）
6. GCS URL 复访（本文件第三节）-> 决定 preflight 是否要改
7. VideoComposer 字幕烧录可考虑自带 CJK 字体（防 ffmpeg 无中文字体）
8. 回归测试用 `_p4_accept`，全过后删
