# src/storyboard/continuity_service.py
# Phase 39（39.md §6.1/§6.3/§6.5）：连续性测量的正式化——把 37A/38 两轮人工实证脚本里
# 的机械行为（帧提取、128 灰度 MAE、接缝测量、中间帧采样）收进 backend service。
#
# 定位：
#   - 确定性服务：零 LLM。Agent 只表达意图（"测这条接缝""采样这条桥的中间帧"），
#     机械正确性（ffmpeg 参数、灰度/缩放规则、MAE 公式、帧 provenance）由本模块保证。
#   - 算法纪律（39.md §6.1）：接缝 MAE 使用 Phase38 同一算法（PIL → L 灰度 →
#     resize((128,128)) → 逐像素绝对差均值，round 1 位），不得改变预处理导致
#     历史阈值（≈2 / 26-60 / 90+ 三档基线）失效。版本号显式随结果返回。
#   - 指标纪律：tail_to_first_mae（上镜实际尾帧 → 本镜实际首帧，真正的接续性指标）
#     与 end_to_end_mae（两端点构图漂移）是两个量，字段名分开，禁止混写（39.md 坑 13）。
#   - 不写 canonical：全部只读测量；采样/提取产物落 output/frames/（媒体缓存，
#     非 canonical 字段），托管（host）由调用方注入（依赖倒置，src 不 import deps）。
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

# Phase38 接缝算法的预处理版本标识（37A/38 报告实测基线基于此算法）。
MAE_PREPROCESSING_VERSION = "gray128_bicubic_v1_phase38"

# 中间帧采样的五个标准位（39.md §6.5：beginning/early-middle/middle/late-middle/ending）。
SAMPLE_POSITIONS = ("beginning", "early_middle", "middle", "late_middle", "ending")


class ContinuityError(Exception):
    """结构化失败：调用方（REST/agent tool）据此返回明确错误，不伪装成功。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# ============ Phase38 同一 MAE 算法 ============
def mae_gray128(path_a: str | Path, path_b: str | Path,
                w: int = 128, h: int = 128) -> float:
    """接缝 MAE（Phase38 同一算法，与 _37_fable/_38_*.py 的 mae() 逐字同口径）：
    PIL 打开 → convert("L") 8-bit 灰度 → resize((w,h))（PIL 默认 BICUBIC）→
    逐像素绝对差均值，round 1 位小数。任何算法变更都必须换
    MAE_PREPROCESSING_VERSION 并重新校准历史基线。"""
    from PIL import Image
    ia = Image.open(path_a).convert("L").resize((w, h))
    ib = Image.open(path_b).convert("L").resize((w, h))
    pa, pb = list(ia.getdata()), list(ib.getdata())
    return round(sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa), 1)


# ============ 帧提取（正式化 38.md §2.3 步骤 1 的 script 手术）============
def _run_ffmpeg(cmd: List[str], out_path: Path) -> None:
    """统一 ffmpeg 执行：失败抛 ContinuityError（错误带 returncode + stderr 尾部，
    沿用 37A 纪律——错误不得为空）。"""
    try:
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except Exception as e:   # noqa: BLE001
        raise ContinuityError("frame_extraction_failed", f"ffmpeg 异常: {e}") from e
    if r.returncode != 0 or not out_path.exists():
        import shutil
        which = shutil.which("ffmpeg") or "PATH-not-found"
        raise ContinuityError(
            "frame_extraction_failed",
            f"ffmpeg 提取失败: rc={r.returncode} ffmpeg={which} "
            f"out_exists={out_path.exists()} stderr_tail={(r.stderr or '')[-300:]}")


def _ffprobe_duration(video: Path) -> Optional[float]:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(video)],
            capture_output=True, encoding="utf-8", errors="replace", timeout=30)
        if r.returncode == 0 and (r.stdout or "").strip():
            return float(r.stdout.strip().splitlines()[0])
    except Exception:   # noqa: BLE001
        return None
    return None


def resolve_final_video_file(shot: Any) -> Optional[Path]:
    """镜头最终视频的本地文件（final_video 指针 → VideoVersion.local；回退旧
    video.local）。文件不存在返回 None（fail closed，不猜）。"""
    vv_id = getattr(shot, "final_video", None)
    if vv_id:
        for vv in (getattr(shot, "video_versions", None) or []):
            if vv.version_id == vv_id and vv.local:
                p = Path(vv.local)
                if p.exists():
                    return p
        # An explicit version pointer must never fall back to another artifact.
        return None
    old = getattr(getattr(shot, "video", None), "local", None)
    if old:
        p = Path(old)
        if p.exists():
            return p
    return None


def _version_id_of(shot: Any) -> Optional[str]:
    return getattr(shot, "final_video", None) or getattr(shot, "video_end_source_version", None)


def extract_video_frame(video: Path, out_path: Path, *,
                        position: "str | float" = "first") -> Path:
    """从视频文件提取一帧。position: "first"（第 0 帧）|"end"（-sseof 尾帧，与
    _extract_video_end_frame 同参数）| float 秒（-ss 输入侧 seek）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if position == "end":
        cmd = ["ffmpeg", "-y", "-v", "error", "-sseof", "-0.2", "-i", str(video),
               "-frames:v", "1", "-q:v", "2", str(out_path)]
    elif position == "first":
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video),
               "-frames:v", "1", "-q:v", "2", str(out_path)]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{float(position):.3f}",
               "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out_path)]
    _run_ffmpeg(cmd, out_path)
    return out_path


def _resolve_local(project_dir: Path, local: Optional[str]) -> Optional[Path]:
    """local 资产路径解析：绝对/相对（相对 project_dir）都支持；不存在 → None。"""
    if not local:
        return None
    p = Path(local)
    if not p.exists():
        alt = Path(project_dir) / local
        if alt.exists():
            return alt
        return None
    return p


def extract_shot_frame_local(project_dir: Path | str, project: Any, shot: Any, *,
                             position: "str | float" = "first",
                             tag: str = "frame") -> Dict[str, Any]:
    """提取镜头最终视频的实际帧到 output/frames/（本地落盘，不托管）。
    视频缺失 fail closed。返回 {local, url=None, source:{...}}。"""
    video = resolve_final_video_file(shot)
    if video is None:
        raise ContinuityError(
            "video_file_missing",
            f"镜头 {shot.shot_id} 没有可用的最终视频文件（final_video 未完成或文件缺失）")
    frames_dir = Path(project_dir) / "output" / "frames"
    stamp = f"{float(position):.3f}" if isinstance(position, (int, float)) else position
    out = frames_dir / f"{shot.shot_id}_{tag}_{stamp}.jpg"
    extract_video_frame(video, out, position=position)
    return {"local": str(out), "url": None,
            "source": {"shot_id": shot.shot_id, "version_id": _version_id_of(shot),
                       "position": stamp}}


async def extract_shot_frame(project_dir: Path | str, project: Any, shot: Any, *,
                             position: "str | float" = "first", tag: str = "frame",
                             host=None) -> Dict[str, Any]:
    """提取镜头最终视频的实际帧（39.md §6.3 正式化）：提取 → output/frames/ 落盘 →
    可选托管（host.upload 为 async——GithubPagesHost 同款接口；GITHUB_TOKEN 等凭证
    由注入的 host 持有，绝不进 Agent 上下文）。托管失败 fail closed。"""
    entry = extract_shot_frame_local(project_dir, project, shot,
                                     position=position, tag=tag)
    if host is not None:
        try:
            entry["url"] = await host.upload(entry["local"])
        except Exception as e:   # noqa: BLE001
            raise ContinuityError("frame_hosting_failed",
                                  f"帧托管失败（{Path(entry['local']).name}）: {e}") from e
    return entry


# ============ 接缝测量（39.md §6.1）============
def _flat_shots(project: Any) -> List[Any]:
    """播放序：scenes → shots 展平（与 video_adapter.resolve_multi_relay_source
    同一口径——上一镜是展平序列的前一项，跨场景成立）。"""
    flat: List[Any] = []
    for sc in (getattr(project, "scenes", None) or []):
        flat.extend(getattr(sc, "shots", None) or [])
    return flat


def play_order_prev(project: Any, shot_id: str) -> Optional[Any]:
    """播放序上一镜（展平序列 idx-1）。没有上一镜返回 None。"""
    flat = _flat_shots(project)
    for idx, s in enumerate(flat):
        if s.shot_id == shot_id:
            return flat[idx - 1] if idx > 0 else None
    return None


def measure_seam(project_dir: Path | str, project: Any, shot_id: str, *,
                 include_end_to_end: bool = False) -> Dict[str, Any]:
    """测量「上镜实际尾帧 → 本镜实际首帧」接缝（Phase38 同算法）。
    - left = 上镜 video_end_frame（自动提取+托管产物，local 必须存在）；
    - right = 本镜最终视频第 0 帧（本模块正式提取）；
    - include_end_to_end 时同时返回 end_to_end_mae（上镜尾帧 vs 本镜尾帧，
      构图漂移量——与接缝是两个语义，字段分开）。
    fail closed：上镜不存在/尾帧未就绪/本镜视频缺失/提取失败 → ContinuityError。"""
    from src.core.models import ShotStatus
    pdir = Path(project_dir)
    cur = project.get_shot(shot_id)
    if cur is None:
        raise ContinuityError("shot_not_found", f"镜头不存在: {shot_id}")
    prev = play_order_prev(project, shot_id)
    if prev is None:
        raise ContinuityError("prev_not_found",
                              f"镜头 {shot_id} 是播放序第一镜，没有上一镜接缝可测")

    tail_asset = getattr(prev, "video_end_frame", None)
    tail_local = _resolve_local(pdir, getattr(tail_asset, "local", None))
    if (tail_asset is None or tail_asset.status != ShotStatus.DONE.value
            or tail_local is None):
        raise ContinuityError(
            "prev_tail_not_ready",
            f"上一镜 {prev.shot_id} 的实际尾帧未就绪（video_end_frame 未完成或本地文件缺失）"
            "——先生成/等待其视频完成（末帧由生成管线自动提取）")

    first = extract_shot_frame_local(pdir, project, cur, position="first",
                                     tag="seam_first")
    mae = mae_gray128(tail_local, first["local"])

    out = {
        "ok": True,
        "operation": "continuity.seam_measure",
        "tail_to_first_mae": mae,
        "end_to_end_mae": None,
        "preprocessing_version": MAE_PREPROCESSING_VERSION,
        "left": {"shot_id": prev.shot_id, "kind": "video_end_frame",
                 "local": str(tail_local), "url": getattr(tail_asset, "url", None),
                 "source_version": getattr(prev, "video_end_source_version", None)},
        "right": {"shot_id": cur.shot_id, "kind": "video_first_frame",
                  "local": first["local"],
                  "source_version": first["source"]["version_id"]},
        "baseline_hint": ("同场景正常接力≈2（历史近零带 1.7-2.4）；跨场景/构图切换 "
                          "26-60；剧变构图 90+。区间是经验参照，不是自动验收阈值"
                          "（39.md §10）。"),
    }
    if include_end_to_end:
        cur_tail_local = _resolve_local(
            pdir, getattr(getattr(cur, "video_end_frame", None), "local", None))
        if cur_tail_local is not None:
            out["end_to_end_mae"] = mae_gray128(tail_local, cur_tail_local)
        else:
            out["end_to_end_mae_error"] = (
                f"本镜 {cur.shot_id} 尾帧未就绪，end_to_end_mae 不可测（不影响接缝值）")
    return out


# ============ 中间帧采样（39.md §6.5：桥验收不能只看两个端点）============
def sample_frames(project_dir: Path | str, project: Any, shot_id: str, *,
                  positions: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """按五个标准位采样镜头最终视频：beginning / early_middle / middle /
    late_middle / ending。ending 用 -sseof（与末帧提取同口径）；其余按时长比例
    -ss 定位（时长取 ffprobe，失败回退 shot.video.num_frames/fps）。
    只读测量：产物落 output/frames/，不写 canonical。"""
    cur = project.get_shot(shot_id)
    if cur is None:
        raise ContinuityError("shot_not_found", f"镜头不存在: {shot_id}")
    video = resolve_final_video_file(cur)
    if video is None:
        raise ContinuityError("video_file_missing",
                              f"镜头 {shot_id} 没有可用的最终视频文件")
    labels = list(positions or SAMPLE_POSITIONS)
    for lb in labels:
        if lb not in SAMPLE_POSITIONS:
            raise ContinuityError("invalid",
                                  f"采样位只支持 {SAMPLE_POSITIONS}，得到 {lb!r}")

    duration = _ffprobe_duration(video)
    if duration is None:
        nf = getattr(getattr(cur, "video", None), "num_frames", 0) or 0
        fps = max(getattr(getattr(cur, "video", None), "frame_rate", 0) or 0, 1)
        duration = nf / fps if nf else None
    if duration is None:
        raise ContinuityError("duration_unknown",
                              f"无法确定镜头 {shot_id} 视频时长（ffprobe 与 canonical 均不可用）")

    frac = {"beginning": 0.0, "early_middle": 0.25, "middle": 0.5,
            "late_middle": 0.75}
    out: List[Dict[str, Any]] = []
    for lb in labels:
        if lb == "ending":
            pos: "str | float" = "end"
            at = duration
        else:
            at = round(duration * frac[lb], 3)
            pos = at
        frames_dir = Path(project_dir) / "output" / "frames"
        f = extract_video_frame(video, frames_dir / f"{shot_id}_sample_{lb}.jpg",
                                position=pos)
        out.append({"label": lb, "at_seconds": at, "local": str(f),
                    "source_version": _version_id_of(cur)})
    return out
