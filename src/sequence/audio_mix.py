# src/sequence/audio_mix.py
# Sequence S2 — AudioMixPlan 纯派生 + ffmpeg filter graph 编译（25.md §31/§32）。
#
# 分层（§31/§32）：
#   build_audio_mix_plan   纯派生（ephemeral / deterministic / 输入解析后零 I/O）——
#                          把 base + layers 组装成与 ffmpeg 无关的混音计划；
#   compile_audio_filter_graph  把计划编译成 ffmpeg filter_complex 字符串 + 输入参数。
# canonical 绝不存 ffmpeg syntax（§32）；graph 只在 Phase 2 现场编译。
#
# 混音语义（25.md §16/§17/§18/§21）：
#   - base = S1 渲染的 native Shot 音轨（永远是底；不提取/不替换/不分离 §18）；
#   - 每层：源内裁切(atrim) → 音量(volume dB) → 淡入淡出(afade 线性) →
#     时间轴定位(adelay) → amix normalize=0 叠加（实测支持——不衰减）；
#   - 输出时长 = base 音频时长（amix duration=first，层越界已在 Phase 1 fail closed）；
#   - 混音后 volumedetect 峰值 → §21 audio_peak_warning（只报告，不自动改 gain）。
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import SequenceError


# §21：输出峰值超过该 dBFS 视为 clipping 风险（advisory，不自动处理）
PEAK_WARNING_THRESHOLD_DB = -0.3


# ============ AudioMixPlan（纯派生，25.md §32）============

@dataclass
class MixLayer:
    """一层与 ffmpeg 无关的混音指令。"""
    layer_id: str
    role: str
    source_file: str
    source_in_ms: int
    duration_ms: int
    start_ms: int
    gain_db: float
    fade_in_ms: int
    fade_out_ms: int


@dataclass
class AudioMixPlan:
    """纯派生混音计划（25.md §32：ephemeral / deterministic / 输入解析后零 I/O）。"""
    base_file: str                       # S1 渲染输出（native Shot 音轨所在）
    base_version_id: str                 # provenance（进 basis；非 freshness 依赖）
    layers: List[MixLayer] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"base_file": self.base_file, "base_version_id": self.base_version_id,
                "layers": [vars(l) | {} for l in self.layers]}


def build_audio_mix_plan(base_file: str, base_version_id: str,
                         layers: List[MixLayer]) -> AudioMixPlan:
    """纯组装（无 I/O；校验已在 canonical/Phase 1 完成——此处只做编译前防御）。"""
    for l in layers:
        if l.fade_in_ms + l.fade_out_ms > l.duration_ms:
            raise SequenceError("audio_layer_invalid",
                                f"layer {l.layer_id}: fade 超出层时长（编译前防御）")
    return AudioMixPlan(base_file=base_file, base_version_id=base_version_id,
                        layers=list(layers))


# ============ ffmpeg filter graph 编译（§31：不散落 router/service）============

def compile_audio_filter_graph(plan: AudioMixPlan) -> Dict[str, Any]:
    """把 AudioMixPlan 编译为 ffmpeg 参数片段。
    返回 {input_args: [...], filter_complex: str, out_args: [...]}：
    - input_args：除 base 外每层一个 `-i <source>`（base 由 caller 作为 input 0 传入）；
    - 输出：-map 0:v -c:v copy（方案 A：零视频重转码）+ 混音 aac。
    确定性：同 plan → 同 graph 字符串（单测锁）。"""
    parts: List[str] = []
    n_layers = len(plan.layers)
    # base 音轨（input 0 的 audio stream）——native Shot audio 永远参与混音（§18）
    mix_inputs = ["[0:a]"]
    for i, l in enumerate(plan.layers, start=1):
        end_s = (l.source_in_ms + l.duration_ms) / 1000.0
        fade_out_start_s = (l.duration_ms - l.fade_out_ms) / 1000.0
        # 源内裁切 → 时间基重置 → 增益 → 线性淡入/淡出（层自身时间轴）→ 定位到 Sequence 时间轴
        chain = (
            f"[{i}:a]atrim=start={l.source_in_ms / 1000.0:.6f}:end={end_s:.6f},"
            f"asetpts=PTS-STARTPTS,"
            f"volume={l.gain_db:.6f}dB,"
            f"afade=t=in:st=0:d={l.fade_in_ms / 1000.0:.6f},"
            f"afade=t=out:st={fade_out_start_s:.6f}:d={l.fade_out_ms / 1000.0:.6f},"
            f"adelay=delays={l.start_ms}:all=1"
            f"[l{i}]"
        )
        parts.append(chain)
        mix_inputs.append(f"[l{i}]")
    parts.append("".join(mix_inputs)
                 + f"amix=inputs={n_layers + 1}:duration=first:normalize=0[mixed]")
    input_args: List[str] = []
    for l in plan.layers:
        input_args += ["-i", l.source_file]
    out_args = ["-map", "0:v", "-c:v", "copy",          # 方案 A：视频流零重编码
                "-map", "[mixed]",
                "-c:a", "aac", "-ar", "48000", "-ac", "2",
                "-map_metadata", "-1"]
    return {"input_args": input_args,
            "filter_complex": ";".join(parts),
            "out_args": out_args}


def parse_peak_db(volumedetect_stderr: str) -> Optional[float]:
    """从 volumedetect 输出取 max_volume（dB）。无有效行 → None（诚实缺省）。"""
    for line in volumedetect_stderr.splitlines():
        if "max_volume:" in line:
            try:
                return float(line.split("max_volume:")[1].split("dB")[0].strip())
            except (ValueError, IndexError):
                return None
    return None


def measure_peak_db(path: str) -> Optional[float]:
    """实测混音输出峰值（§21 advisory 用）。探测失败 → None（不伪造）。"""
    import subprocess
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", "volumedetect", "-vn", "-f", "null", "-"],
            capture_output=True, timeout=600)
    except Exception:
        return None
    return parse_peak_db(proc.stderr.decode("utf-8", errors="replace"))


def peak_warning(max_volume_db: Optional[float]) -> Optional[Dict[str, Any]]:
    """§21 clipping advisory：只报告，不自动改 gain（用户/Agent 决定调整）。"""
    if max_volume_db is None:
        return None
    if max_volume_db > PEAK_WARNING_THRESHOLD_DB:
        return {"code": "audio_peak_warning",
                "max_volume_db": round(max_volume_db, 2),
                "threshold_db": PEAK_WARNING_THRESHOLD_DB,
                "hint": "混音峰值接近/达到满量程，存在 clipping 风险——"
                        "请显式调低相应层 gain_db（系统不自动修改）"}
    return None
