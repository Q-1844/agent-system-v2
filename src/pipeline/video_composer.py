"""
视频合成器
用 ffmpeg 把分镜图(Ken Burns 推拉)/视频段 拼接成最终 mp4，可叠加音频。
字幕烧录暂留接口（TODO：srt 按场景偏移后用 subtitles filter）。
"""
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from ..utils.file_saver import FileSaver
from ..utils.logger import setup_logger

logger = setup_logger("video_composer")


class VideoComposer:
    """最终视频合成器（ffmpeg subprocess）"""

    def __init__(self, file_saver: FileSaver):
        self.file_saver = file_saver
        self.output_dir = file_saver.output_dir
        self.cache_dir = self.output_dir / "cache" / "segments"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compose(
        self,
        scenes: List[Dict[str, Any]],
        width: int = 1280,
        height: int = 720,
        fps: int = 24,
        scene_duration: float = 5.0,
        burn_subtitles: bool = False,
        on_progress: Optional[Callable[[str, float], None]] = None,
    ) -> Path:
        """
        合成最终视频。
        每个场景：有 transitions.to_next/video_path 用视频段；否则用 image_path 做 Ken Burns。
        叠加各场景 audio_path（按时间偏移 amix）。
        返回 final.mp4 路径。
        """
        def log(msg: str, pct: float = None):
            logger.info(msg)
            if on_progress:
                on_progress(msg, pct)

        valid = [
            s for s in scenes
            if s.get("image_path") or s.get("transitions", {}).get("to_next") or s.get("video_path")
        ]
        if not valid:
            raise Exception("没有可合成的场景（需分镜图或视频段）")

        log(f"开始合成：{len(valid)} 个场景，每场景 {scene_duration}s，{width}x{height}@{fps}fps", 0.0)

        # 1. 每场景生成统一规格的片段
        seg_paths: List[Path] = []
        seg_scenes: List[Dict[str, Any]] = []
        for i, scene in enumerate(valid):
            seg = self._make_segment(scene, i, width, height, fps, scene_duration)
            if seg:
                seg_paths.append(seg)
                seg_scenes.append(scene)
                log(f"  场景 {scene.get('id')} 片段已生成 ({i+1}/{len(valid)})", (i + 1) / len(valid) * 0.7)
        if not seg_paths:
            raise Exception("所有场景片段生成失败")

        # 2. 拼接
        concat_path = self._concat(seg_paths)
        log("拼接完成", 0.85)

        # 3. 叠加音频
        final_path = self.output_dir / "final.mp4"
        ok = self._mux_audio(concat_path, seg_scenes, scene_duration, final_path)
        if not ok:
            # 降级：仅视频
            logger.warning("音频叠加失败，降级为纯视频")
            cmd = ["ffmpeg", "-y", "-i", str(concat_path),
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(final_path)]
            subprocess.run(cmd, capture_output=True, text=True)

        if final_path.exists():
            log(f"最终视频已生成: {final_path}", 1.0)
        else:
            raise Exception("最终视频生成失败")
        return final_path

    def _make_segment(
        self, scene: Dict[str, Any], idx: int, w: int, h: int, fps: int, duration: float
    ) -> Optional[Path]:
        """为单个场景生成 mp4 片段（统一尺寸/帧率/编码）"""
        seg = self.cache_dir / f"seg_{idx:03d}.mp4"
        scale_pad = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}")

        # 优先用已有视频段
        video_path = scene.get("transitions", {}).get("to_next") or scene.get("video_path")
        if video_path and Path(str(video_path)).exists():
            cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vf", scale_pad,
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(seg)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and seg.exists():
                return seg
            logger.warning(f"视频段处理失败({scene.get('id')}): {r.stderr[-300:]}")

        # 退回 Ken Burns（分镜图推拉）
        image_path = scene.get("image_path")
        if image_path and Path(str(image_path)).exists():
            frames = max(int(duration * fps), 1)
            # 先放大2倍再 zoompan，避免 zoompan 放大模糊
            zoompan = f"zoompan=z='min(zoom+0.0015,1.5)':d={frames}:s={w}x{h}:fps={fps}"
            cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
                   "-vf", f"scale={w*2}:{h*2}:force_original_aspect_ratio=decrease,"
                          f"pad={w*2}:{h*2}:(ow-iw)/2:(oh-ih)/2:black,{zoompan},"
                          f"trim=duration={duration},setsar=1",
                   "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-an", str(seg)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and seg.exists():
                return seg
            logger.warning(f"Ken Burns 生成失败({scene.get('id')}): {r.stderr[-300:]}")
        return None

    def _concat(self, seg_paths: List[Path]) -> Path:
        """拼接片段（先尝试 stream copy，失败则重编码）"""
        list_file = self.cache_dir / "concat.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for p in seg_paths:
                # Windows 路径转义
                f.write(f"file '{p.absolute().as_posix()}'\n")
        out = self.cache_dir / "concat.mp4"
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
               "-c", "copy", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and out.exists():
            return out
        logger.warning(f"concat copy 失败，重编码: {r.stderr[-300:]}")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
               "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise Exception(f"拼接失败: {r.stderr[-300:]}")
        return out

    def _mux_audio(
        self, concat_path: Path, scenes: List[Dict[str, Any]],
        scene_duration: float, final_path: Path
    ) -> bool:
        """叠加各场景音频（adelay 偏移 + amix）"""
        audio_inputs: List[str] = []
        audio_filters: List[str] = []
        amix_list: List[str] = []
        delay = 0.0
        idx = 1  # 输入0是视频
        for scene in scenes:
            ap = scene.get("audio_path")
            if ap and Path(str(ap)).exists():
                audio_inputs += ["-i", str(ap)]
                delay_ms = int(delay * 1000)
                audio_filters.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[ad{idx}]")
                amix_list.append(f"[ad{idx}]")
                idx += 1
            delay += scene_duration

        if not amix_list:
            # 无音频，只重编码视频
            cmd = ["ffmpeg", "-y", "-i", str(concat_path),
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(final_path)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            return r.returncode == 0

        filter_complex = ";".join(audio_filters) + ";" + "".join(amix_list) + f"amix=inputs={len(amix_list)}:duration=longest,apad[aout]"
        cmd = ["ffmpeg", "-y", "-i", str(concat_path)] + audio_inputs + [
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(final_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            logger.warning(f"音频叠加失败: {r.stderr[-400:]}")
            return False
        return True
