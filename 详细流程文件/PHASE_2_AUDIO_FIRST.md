# 阶段 2 · 配音前置与词级字幕（Audio-First）

> **目标**：把配音从「视频之后」提到「视频之前」，用真实音频时长反推 `num_frames`，并用 edge-tts 的词级时间戳生成精准 SRT。
> **前置**：阶段 1 全部验收通过。
> **参考开源项目**：`reference/agnes-video-generator/core/audio/tts.py` 和 `core/audio/subtitle.py`

---

## 2.1 为什么这是致命错误而不是优化项

现在的流程顺序是：

```
分镜 → 视频（固定 5 秒）→ 配音 → 合成
```

**这是反的。**

视频时长在提交时就被 `num_frames` 锁死，一旦生成完**改不了**；而配音时长要等 TTS 出来才知道。现在等于在赌"每句台词都刚好 5 秒"。

实际后果：台词 8 秒、画面 5 秒 → 合成时 `adelay` 按 5 秒/场景累加偏移 → **从第二个镜头开始音画就错位，且误差逐镜累积**。20 个镜头下来能偏 30 秒以上。那个"默认 5 秒时段"的硬编码 SRT 也是同一个病根。

**正确顺序**：

```
分镜图 → TTS 拿真实音频时长 → 反推 num_frames → 生成视频 → 拼接 → 整体叠音轨字幕
```

---

## 2.2 时长反推算法

```python
# src/core/timing.py
from __future__ import annotations
from dataclasses import dataclass

BREATH_SEC   = 0.6      # 台词前后留的呼吸口
MIN_FRAMES   = 81       # ≈3.4s @24fps
MAX_FRAMES   = 441      # ≈18.375s @24fps
SILENT_SEC   = 3.0      # 无台词镜头的默认时长

@dataclass
class ShotTiming:
    num_frames: int
    frame_rate: int
    planned_seconds: float
    audio_seconds: float
    needs_split: bool          # True = 这句台词塞不进一个镜头
    suggested_splits: int      # 建议拆成几个镜头

def snap_8n1(frames: int) -> int:
    n = round((frames - 1) / 8)
    return max(MIN_FRAMES, min(MAX_FRAMES, 8 * int(n) + 1))

def plan_shot_timing(audio_seconds: float, frame_rate: int = 24,
                     breath: float = BREATH_SEC) -> ShotTiming:
    """由真实音频时长反推视频帧数。AGNES_API_FACTS §3.4"""
    if audio_seconds <= 0:
        target = SILENT_SEC
    else:
        target = audio_seconds + breath

    max_sec = MAX_FRAMES / frame_rate           # 24fps 下 = 18.375s
    needs_split = target > max_sec
    splits = 1
    if needs_split:
        import math
        splits = math.ceil(target / (max_sec * 0.9))   # 留 10% 余量

    frames = snap_8n1(int(round(target * frame_rate)))
    return ShotTiming(num_frames=frames, frame_rate=frame_rate,
                      planned_seconds=frames / frame_rate,
                      audio_seconds=audio_seconds,
                      needs_split=needs_split, suggested_splits=splits)
```

### 🔴 超长台词必须拆镜头，不许硬塞

如果 `needs_split == True`，**不要**降 frame_rate 硬撑（降到 10fps 画面会明显卡顿，观感崩坏）。正确做法：

1. 后端返回 `needs_split: true` + `suggested_splits: N`
2. 前端在该 Shot 卡片上显示醒目警告：「台词 23.4s 超过单镜上限 18.4s，建议拆为 2 个镜头」
3. 提供一键「按句号拆分」按钮，调下面的拆分接口

```python
# src/core/timing.py（续）
import re

def split_dialogue(text: str, frame_rate: int = 24,
                   chars_per_sec: float = 4.0) -> list[str]:
    """把超长台词按标点贪心切成能塞进单镜的片段。
    参考开源项目 core/pipelines/manuscript_video.py 的分段策略：
    按 。？！ 切句，按约 4 字/秒估算时长，贪心合并成 5–12 秒的片段。"""
    max_sec = (MAX_FRAMES / frame_rate) * 0.85
    sentences = [s for s in re.split(r'(?<=[。？！；\.\?\!])', text) if s.strip()]
    out, buf = [], ""
    for s in sentences:
        cand = buf + s
        if len(cand) / chars_per_sec > max_sec and buf:
            out.append(buf); buf = s
        else:
            buf = cand
    if buf: out.append(buf)
    return out or [text]
```

> **注意 4 字/秒只是估算，用于「切之前预判」。真正的时长一律以 TTS 实测为准。** 切完后每段仍要走一次 TTS 拿真实时长。

---

## 2.3 edge-tts 词级时间戳

edge-tts 提供 `WordBoundary` 事件，能拿到每个词的精确起止时间。现在项目的"默认 5 秒时段"硬编码字幕必然飘。

```python
# src/api/edge_tts_client.py  （整文件替换）
from __future__ import annotations
import asyncio, logging
from pathlib import Path
from typing import List, Dict, Any
import edge_tts

log = logging.getLogger("EdgeTTS")

VOICES = {
    "female":  "zh-CN-XiaoxiaoNeural",   # 晓晓 · 温柔女声
    "male":    "zh-CN-YunxiNeural",      # 云希 · 沉稳男声
    "female2": "zh-CN-XiaoyiNeural",     # 晓伊 · 活泼女声
    "male2":   "zh-CN-YunyangNeural",    # 云扬 · 少年音
    "default": "zh-CN-XiaoxiaoNeural",
}

class EdgeTTSClient:
    async def synthesize(self, text: str, *, voice: str = "default",
                         rate: str = "+0%", volume: str = "+0%",
                         out_path: str | Path) -> Dict[str, Any]:
        """合成语音并返回真实时长 + 词级时间戳。

        返回：{"path": str, "duration_ms": int,
               "words": [{"text": "你好", "offset_ms": 0, "duration_ms": 320}, ...]}
        """
        voice_id = VOICES.get(voice, voice)
        out_path = Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)

        comm = edge_tts.Communicate(text, voice_id, rate=rate, volume=volume)
        audio = bytearray()
        words: List[Dict[str, Any]] = []

        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # edge-tts 的单位是 100 纳秒（ticks）
                words.append({
                    "text": chunk["text"],
                    "offset_ms": chunk["offset"] // 10_000,
                    "duration_ms": chunk["duration"] // 10_000,
                })

        if not audio:
            raise RuntimeError(f"edge-tts 返回空音频，text={text[:40]!r}")
        out_path.write_bytes(bytes(audio))

        duration_ms = await self._probe_duration_ms(out_path)
        if duration_ms <= 0 and words:
            last = words[-1]
            duration_ms = last["offset_ms"] + last["duration_ms"] + 200

        log.info("TTS 完成 %s: %.2fs, %d 个词边界",
                 out_path.name, duration_ms / 1000, len(words))
        return {"path": str(out_path), "duration_ms": duration_ms, "words": words}

    async def _probe_duration_ms(self, path: Path) -> int:
        """用 ffprobe 拿准确时长。WordBoundary 只覆盖到最后一个词，
        尾部静音拿不到，所以必须 ffprobe。"""
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        try:
            return int(float(out.decode().strip()) * 1000)
        except Exception:
            log.warning("ffprobe 读取时长失败: %s", path)
            return 0

    @staticmethod
    def list_voices() -> List[dict]:
        return [{"id": k, "voice": v, "label": lbl} for (k, v), lbl in zip(
            VOICES.items(), ["晓晓 · 温柔女声", "云希 · 沉稳男声",
                             "晓伊 · 活泼女声", "云扬 · 少年音", "默认"])]
```

---

## 2.4 从词级时间戳生成 SRT

```python
# src/core/subtitle.py
from __future__ import annotations
from typing import List, Dict, Any

MAX_LINE_CHARS   = 18      # 单行最大字数，超了折成两行
MAX_CUE_SEC      = 3.0     # 单条字幕最长时间
MAX_CUE_CHARS    = 30      # 单条字幕最大字数
BREAK_PUNCT      = "，,。.！!？?；;、"

def _fmt(ms: int) -> str:
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _wrap(text: str) -> str:
    """长文本折成两行，优先在标点处断。"""
    if len(text) <= MAX_LINE_CHARS:
        return text
    mid = len(text) // 2
    best, bestd = mid, 10**9
    for i, ch in enumerate(text):
        if ch in BREAK_PUNCT and abs(i - mid) < bestd:
            best, bestd = i + 1, abs(i - mid)
    return text[:best].strip() + "\n" + text[best:].strip()

def words_to_cues(words: List[Dict[str, Any]],
                  offset_ms: int = 0) -> List[Dict[str, Any]]:
    """把词级时间戳贪心合并成 2–3 秒一条的字幕。
    参考：reference/agnes-video-generator/core/audio/subtitle.py"""
    cues, buf = [], []

    def flush():
        if not buf: return
        start = buf[0]["offset_ms"] + offset_ms
        end   = buf[-1]["offset_ms"] + buf[-1]["duration_ms"] + offset_ms
        cues.append({"start_ms": start, "end_ms": end,
                     "text": "".join(w["text"] for w in buf).strip()})
        buf.clear()

    for w in words:
        if buf:
            span  = (w["offset_ms"] + w["duration_ms"] - buf[0]["offset_ms"]) / 1000
            chars = sum(len(x["text"]) for x in buf) + len(w["text"])
            if span > MAX_CUE_SEC or chars > MAX_CUE_CHARS:
                flush()
        buf.append(w)
        if w["text"] and w["text"][-1] in "。！？.!?":
            flush()
    flush()
    return cues

def cues_to_srt(cues: List[Dict[str, Any]]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{_fmt(c['start_ms'])} --> {_fmt(c['end_ms'])}\n"
                   f"{_wrap(c['text'])}\n")
    return "\n".join(out)

def build_global_srt(shots_timeline: List[Dict[str, Any]]) -> str:
    """全片一条 SRT。

    shots_timeline: [{"start_ms": 该镜头在成片中的起点, "words": [...]}, ...]

    🔴 关键：全片只生成一个 SRT，在最终拼接完成后整体叠加。
    绝对不要「每镜一个 srt，合成时逐段偏移叠加」——那样误差会累积。
    这也是开源项目 core/compositor/concatenator.py 采用的策略。"""
    all_cues = []
    for item in shots_timeline:
        all_cues.extend(words_to_cues(item["words"], offset_ms=item["start_ms"]))
    all_cues.sort(key=lambda c: c["start_ms"])
    return cues_to_srt(all_cues)
```

---

## 2.5 后端接口

### 2.5.1 单镜头配音（改造现有）

```
POST /api/project/{p}/shot/{shot_id}/audio
Body: {"voice": "female", "rate": "+0%", "regenerate": false}
```

后台任务流程：

1. 读 Shot，取 `dialogue`
2. `dialogue` 为空 → `audio.status = "skipped"`，`plan_shot_timing(0)` 给静默时长，直接返回
3. 调 `EdgeTTSClient.synthesize` → `output/audio/{shot_id}.mp3`
4. 调 `plan_shot_timing(duration_ms/1000, fps)`
5. **写回**（走 `store.update_shot`）：
   - `audio.local` / `audio.duration_ms` / `audio.words` / `audio.voice` / `audio.rate`
   - `video.num_frames` / `video.frame_rate`（这是关键——视频参数在这一步就定死了）
6. 若 `needs_split` → SSE 推 `warning` 事件，带 `suggested_splits`
7. SSE 推进度

### 2.5.2 批量配音

```
POST /api/project/{p}/audio/batch
Body: {"voice":"female","rate":"+0%","scope":"all"|"pending"|["s01_c01",...],"skip_locked":true}
```

edge-tts 是免费本地服务，**不占 Agnes 限流桶**，可以并发。但仍要限制并发数为 4，避免被微软临时限流：

```python
sem = asyncio.Semaphore(4)
async def one(shot):
    async with sem:
        return await gen_audio(shot)
await asyncio.gather(*(one(s) for s in shots), return_exceptions=True)
```

### 2.5.3 台词拆分

```
POST /api/project/{p}/shot/{shot_id}/split-dialogue
Body: {"mode": "auto"}      # auto=按标点自动切；也可传 segments: ["...","..."]
```

- 调 `split_dialogue`
- 原 Shot 保留第 1 段
- 后续段插入为**同场次的新 Shot**，`shot_id` 递增（`s01_c02`, `s01_c03`），并把原 Shot 后面的镜头序号顺移
- 新 Shot 继承原 Shot 的 `camera` / `lighting` / `characters` / `link_mode`
- 新 Shot 的 `first_frame` 置 `pending`（需要重新生成分镜图）
- **原 Shot 的已有产物一律保留，不清空**

### 2.5.4 时长预览（不生成，只算）

```
GET /api/project/{p}/timing
```
返回每个 Shot 的 `audio_seconds` / `planned_seconds` / `needs_split`，以及全片预计总时长。前端时间线轨道直接用这个渲染。

---

## 2.6 🔴 合成阶段的顺序修正

现在的 `VideoComposer` 是「拼视频 → 按 adelay 偏移逐段叠音频」。改成：

```
1. 先把所有视频片段拼接完成（concat）
2. 再把整条音轨（所有镜头音频按时间线拼成一条 narration.mp3）叠加上去
3. 再把全片一条 SRT 整体烧录
```

**理由**：逐段叠加会产生累积误差。开源项目 README 明确写了这条策略。

音轨拼接：

```python
# 每个 shot 在成片中的起点 = 前面所有 shot 的 planned_seconds 之和
# 用 ffmpeg concat + apad 把每段音频补齐到该镜头的视频长度
def build_audio_track(shots, out_path):
    """每个 shot 生成一段「音频 + 静音补齐到镜头时长」，然后 concat。
    这样音轨长度天然等于视频长度，不需要 adelay 也不会累积误差。"""
    segments = []
    for s in shots:
        dur = s.video.seconds_returned or (s.video.num_frames / s.video.frame_rate)
        if s.audio.ready:
            # apad 补到 dur 秒后 atrim 截断，保证严格等长
            segments.append(("audio", s.audio.local, dur))
        else:
            segments.append(("silence", None, dur))
    # 逐段用 ffmpeg 生成等长 wav，再 concat demuxer 拼接
```

**🔴 注意用 `seconds_returned`（服务端回读值）而不是提交值**，因为服务端可能归一化过。

---

## 2.7 阶段 2 验收清单

- [ ] `plan_shot_timing(4.2, 24)` → `num_frames` 是 8n+1 且 ≈ 4.8s
- [ ] `plan_shot_timing(25.0, 24)` → `needs_split=True`，`suggested_splits=2`
- [ ] `plan_shot_timing(0, 24)` → 返回静默默认时长，不报错
- [ ] TTS 一句 10 字台词 → `words` 数组非空，`duration_ms` 与 ffprobe 结果一致（±50ms）
- [ ] 生成的 SRT 用播放器打开，字幕与语音对齐（人工看一遍，别只看代码）
- [ ] 单条字幕不超过 3 秒、不超过 30 字；超过 18 字的自动折成两行
- [ ] 批量配音 10 个镜头，`project.json` 里 10 个 shot 的 `audio` 和 `video.num_frames` 全部写回
- [ ] 台词拆分接口：一条 25 秒台词拆成 2 个 shot，shot_id 正确递增，后续镜头序号顺移正确
- [ ] `GET /api/project/{p}/timing` 返回的全片总时长 = 各镜 planned_seconds 之和
- [ ] **合成一次**（此时还没有 Agnes 视频，用 Ken Burns 兜底），确认音画不再逐镜偏移

