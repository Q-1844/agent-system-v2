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
    # Phase 44C（语义快照 dict 形态来自 plan；默认 = v1 语义）
    base_gain_db: float = 0.0
    ducking: Optional[Dict[str, Any]] = None    # SequenceDuckingConfig.semantic_snapshot()
    loudness: Optional[Dict[str, Any]] = None   # SequenceLoudnessTarget.semantic_snapshot()

    def to_dict(self) -> Dict[str, Any]:
        return {"base_file": self.base_file, "base_version_id": self.base_version_id,
                "layers": [vars(l) | {} for l in self.layers],
                "base_gain_db": self.base_gain_db,
                "ducking": dict(self.ducking) if self.ducking else None,
                "loudness": dict(self.loudness) if self.loudness else None}


def build_audio_mix_plan(base_file: str, base_version_id: str,
                         layers: List[MixLayer], *,
                         base_gain_db: float = 0.0,
                         ducking: Optional[Dict[str, Any]] = None,
                         loudness: Optional[Dict[str, Any]] = None) -> AudioMixPlan:
    """纯组装（无 I/O；校验已在 canonical/Phase 1 完成——此处只做编译前防御）。"""
    for l in layers:
        if l.fade_in_ms + l.fade_out_ms > l.duration_ms:
            raise SequenceError("audio_layer_invalid",
                                f"layer {l.layer_id}: fade 超出层时长（编译前防御）")
    return AudioMixPlan(base_file=base_file, base_version_id=base_version_id,
                        layers=list(layers), base_gain_db=float(base_gain_db or 0.0),
                        ducking=ducking, loudness=loudness)


# ============ ffmpeg filter graph 编译（§31：不散落 router/service）============

def _layer_chain(l: MixLayer, i: int) -> str:
    """单层处理链：源内裁切 → 时间基重置 → 增益 → 线性淡入/淡出（层自身时间轴）
    → 定位到 Sequence 时间轴。mix 与 stem 编译共用（语义唯一）。"""
    end_s = (l.source_in_ms + l.duration_ms) / 1000.0
    fade_out_start_s = (l.duration_ms - l.fade_out_ms) / 1000.0
    return (
        f"[{i}:a]atrim=start={l.source_in_ms / 1000.0:.6f}:end={end_s:.6f},"
        f"asetpts=PTS-STARTPTS,"
        f"volume={l.gain_db:.6f}dB,"
        f"afade=t=in:st=0:d={l.fade_in_ms / 1000.0:.6f},"
        f"afade=t=out:st={fade_out_start_s:.6f}:d={l.fade_out_ms / 1000.0:.6f},"
        f"adelay=delays={l.start_ms}:all=1"
        f"[l{i}]"
    )


def _ducking_config_of(plan: AudioMixPlan) -> Optional[Dict[str, Any]]:
    d = plan.ducking
    if not d or not d.get("trigger_roles") or not d.get("ducked_roles"):
        return None
    return d


def compile_audio_filter_graph(plan: AudioMixPlan) -> Dict[str, Any]:
    """把 AudioMixPlan 编译为 ffmpeg 参数片段。
    返回 {input_args: [...], filter_complex: str, final_label: str}：
    - input_args：除 base 外每层一个 `-i <source>`（base 由 caller 作为 input 0 传入）；
    - 44C：base 增益（volume）、music 侧链压低（sidechaincompress，trigger=旁白层
      混合为键信号）、终混响度（loudnorm+aresample，目标平台口径存 plan）。
    🔴 44C+ 接管修复：图只产出**中间 PCM**（final_label 供 -map）——单遍「混音图
    → AAC」实测丢延迟层（amix+adelay 与定长帧编码器组合：多句旁白仅第一句
    幸存；PCM/MP3/FLAC 输出全部正常，图本身逐句相关验证无误，触发条件随延迟
    值漂移、apad/asetnsamples 均不可靠）。成片编码由 service 两遍渲染的第二遍
    （视频流 copy + WAV→AAC，无 filter_complex）完成。
    确定性：同 plan → 同 graph 字符串（单测锁）。默认配置下图与 v1 逐字节等价。"""
    parts: List[str] = []
    n_layers = len(plan.layers)
    for i, l in enumerate(plan.layers, start=1):
        parts.append(_layer_chain(l, i))

    # base 音轨（input 0 的 audio stream）——native Shot audio 永远参与混音（§18）；
    # 44C：可整体压低（base_gain_db）给旁白/音乐让位。
    if plan.base_gain_db != 0.0:
        parts.append(f"[0:a]volume={plan.base_gain_db:.6f}dB[b0]")
        base_label = "[b0]"
    else:
        base_label = "[0:a]"

    # ---- 44C ducking：ducked_roles 层 × trigger_roles 键信号 → sidechaincompress ----
    # 仅当配置存在且 ducked/trigger 两类层都真实存在时启用；否则全层直通（v1 语义）。
    # 🔴 44C+ 修复：trigger（旁白）层要同时喂「键信号」和「最终 amix」——同一标签
    # 复用两处会让 ffmpeg 图连接错乱（实测 base 全电平泄漏、旁白丢失）。必须显式
    # asplit=2 分成键信号/最终混音两路。
    duck_cfg = _ducking_config_of(plan)
    ducked_idx: List[int] = []
    trigger_idx: List[int] = []
    if duck_cfg:
        for i, l in enumerate(plan.layers, start=1):
            if l.role in duck_cfg["ducked_roles"]:
                ducked_idx.append(i)
            elif l.role in duck_cfg["trigger_roles"]:
                trigger_idx.append(i)
    ducking_active = bool(duck_cfg and ducked_idx and trigger_idx)

    ducked_labels: List[str] = []
    final_narr_labels: List[str] = []
    if ducking_active:
        key_labels: List[str] = []
        for i in trigger_idx:
            parts.append(f"[l{i}]asplit=2[l{i}k][l{i}f]")
            key_labels.append(f"[l{i}k]")
            final_narr_labels.append(f"[l{i}f]")
        if len(key_labels) == 1:
            key_label = key_labels[0][1:-1]        # "l2k"（下方统一加括号）
        else:
            parts.append("".join(key_labels)
                         + f"amix=inputs={len(key_labels)}"
                           ":duration=longest:normalize=0[narrkey]")
            key_label = "narrkey"
        threshold_lin = 10 ** (float(duck_cfg["threshold_db"]) / 20.0)
        for i in ducked_idx:
            parts.append(
                f"[l{i}][{key_label}]sidechaincompress="
                f"threshold={threshold_lin:.9f}:"
                f"ratio={float(duck_cfg['ratio']):.6f}:"
                f"attack={int(duck_cfg['attack_ms'])}:"
                f"release={int(duck_cfg['release_ms'])}[l{i}d]")
            ducked_labels.append(f"[l{i}d]")

    other_idx = [i for i in range(1, n_layers + 1)
                 if i not in (ducked_idx if ducking_active else [])
                 and i not in (trigger_idx if ducking_active else [])]
    mix_inputs = ([base_label] + ducked_labels
                  + final_narr_labels
                  + [f"[l{i}]" for i in other_idx])
    parts.append("".join(mix_inputs)
                 + f"amix=inputs={len(mix_inputs)}:duration=first:normalize=0[mixed]")

    # ---- 44C 终混响度目标（loudnorm 单遍 + 采样率回 48k）----
    final_label = "[mixed]"
    if plan.loudness:
        ln = plan.loudness
        parts.append(f"[mixed]loudnorm=I={float(ln['i']):.1f}:"
                     f"TP={float(ln['tp']):.1f}:LRA={float(ln['lra']):.1f},"
                     f"aresample=48000[final]")
        final_label = "[final]"

    input_args: List[str] = []
    for l in plan.layers:
        input_args += ["-i", l.source_file]
    return {"input_args": input_args,
            "filter_complex": ";".join(parts),
            "final_label": final_label}


def compile_stem_filter_graph(plan: AudioMixPlan, roles: List[str]) -> Dict[str, Any]:
    """44C 分轨导出（C3 交付物）：指定 role 的层链合成纯音频 stem（无 base、无
    sidechain——旁白不在轨上时音乐不压低；溯源=plan revision）。WAV 48k/2。
    🔴 stem 没有 base 输入：ffmpeg 文件索引从 0 起（区别于 mix 图中 base 占 0、
    层占 1..N）。"""
    selected = [l for l in plan.layers if l.role in roles]
    if not selected:
        raise SequenceError("stem_invalid",
                            f"分轨无对应层：roles={roles}（plan 中现有 role: "
                            f"{sorted({l.role for l in plan.layers})}）")
    parts: List[str] = []
    input_args: List[str] = []
    labels: List[str] = []
    for i, l in enumerate(selected):        # 文件索引 0..k-1（无 base 占位）
        end_s = (l.source_in_ms + l.duration_ms) / 1000.0
        fade_out_start_s = (l.duration_ms - l.fade_out_ms) / 1000.0
        parts.append(
            f"[{i}:a]atrim=start={l.source_in_ms / 1000.0:.6f}:end={end_s:.6f},"
            f"asetpts=PTS-STARTPTS,"
            f"volume={l.gain_db:.6f}dB,"
            f"afade=t=in:st=0:d={l.fade_in_ms / 1000.0:.6f},"
            f"afade=t=out:st={fade_out_start_s:.6f}:d={l.fade_out_ms / 1000.0:.6f},"
            f"adelay=delays={l.start_ms}:all=1"
            f"[s{i}]"
        )
        labels.append(f"[s{i}]")
        input_args += ["-i", l.source_file]
    if len(labels) == 1:
        stem_label = labels[0]
    else:
        parts.append("".join(labels)
                     + f"amix=inputs={len(labels)}:duration=longest:normalize=0[stem]")
        stem_label = "[stem]"
    out_args = ["-map", stem_label, "-c:a", "pcm_s16le",
                "-ar", "48000", "-ac", "2", "-map_metadata", "-1"]
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
