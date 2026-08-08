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


def escape_ffmpeg_path(path: str) -> str:
    """Windows 下 ffmpeg subtitles filter 对路径挑剔：反斜杠转正斜杠、盘符冒号转义成 \\:。
    实测（2026-07-30）本机构建需「引号 + \\: 转义」并用，单独任一都会失败。
    返回值调用方需再用单引号包裹（见 _mux_shots）。"""
    return path.replace("\\", "/").replace(":", "\\:")


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
            subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")

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
            r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
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
            r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
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
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and out.exists():
            return out
        logger.warning(f"concat copy 失败，重编码: {r.stderr[-300:]}")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
               "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
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
            r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
            return r.returncode == 0

        filter_complex = ";".join(audio_filters) + ";" + "".join(amix_list) + f"amix=inputs={len(amix_list)}:duration=longest,apad[aout]"
        cmd = ["ffmpeg", "-y", "-i", str(concat_path)] + audio_inputs + [
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(final_path),
        ]
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            logger.warning(f"音频叠加失败: {r.stderr[-400:]}")
            return False
        return True

    # ==================== 阶段 2：shot 级合成（配音前置后的新顺序）====================
    def compose_shots(self, project, width: int = 1280, height: int = 720,
                      fps: int = 24, burn_subtitles: bool = True,
                      on_progress: Optional[Callable[[str, float], None]] = None) -> Path:
        """concat 视频 -> 整条音轨（每段 apad 补到镜头时长）-> 整体烧录全片 SRT。
        无 Agnes 视频时用 Ken Burns 兜底。时长用 seconds_returned（服务端回读值）。
        这套顺序杜绝了旧 adelay 逐段偏移的累积误差。"""
        def log(msg: str, pct: float = None):
            logger.info(msg)
            if on_progress:
                on_progress(msg, pct)

        from src.core.subtitle import build_global_srt

        shots = [s for s in project.all_shots()
                 if (s.video.local and Path(str(s.video.local)).exists())
                 or (s.first_frame.selected
                     and Path(str(s.first_frame.selected.local or "")).exists())]
        if not shots:
            raise Exception("没有可合成的镜头（需分镜图或视频段）")
        log(f"开始合成（shot 级）：{len(shots)} 个镜头，{width}x{height}@{fps}fps", 0.0)

        # 1. 每镜头生成统一片段 + 收集时间线
        seg_paths: List[Path] = []
        timeline = []
        cursor_ms = 0
        for i, sh in enumerate(shots):
            dur = self._shot_duration(sh)
            seg = self._make_shot_segment(sh, i, width, height, fps, dur)
            if not seg:
                log(f"  镜头 {sh.shot_id} 片段生成失败，跳过", None)
                continue
            seg_paths.append(seg)
            timeline.append({"start_ms": cursor_ms, "words": sh.audio.words or [],
                             "duration": dur, "shot_id": sh.shot_id})
            cursor_ms += int(dur * 1000)
            log(f"  镜头 {sh.shot_id} 片段 {dur:.2f}s ({i+1}/{len(shots)})",
                (i + 1) / len(shots) * 0.6)
        if not seg_paths:
            raise Exception("所有镜头片段生成失败")
        for s in shots:
            _ = s  # 仅避免未用告警

        # 2. 拼接视频
        concat_path = self._concat(seg_paths)
        log("视频拼接完成", 0.7)

        # 3. 整条音轨（每段 apad 补到镜头时长再 concat，天然等于视频长度）
        narration = self._build_audio_track(shots, timeline)
        log("音轨拼接完成", 0.82)

        # 4. 全片单条 SRT
        srt_path = self.output_dir / "subtitles" / "final.srt"
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(build_global_srt(timeline), encoding="utf-8")
        log(f"全片 SRT 已生成: {srt_path}", 0.88)

        # 5. 合成：视频 + 音轨 + 烧 SRT
        final_path = self.output_dir / "final.mp4"
        self._mux_shots(concat_path, narration,
                        srt_path if burn_subtitles else None,
                        width, height, final_path)
        if final_path.exists():
            log(f"最终视频已生成: {final_path}", 1.0)
        else:
            raise Exception("最终视频生成失败")
        return final_path

    def _shot_duration(self, shot) -> float:
        if shot.video.seconds_returned:
            return float(shot.video.seconds_returned)
        if shot.video.num_frames and shot.video.frame_rate:
            return shot.video.num_frames / shot.video.frame_rate
        return 3.0   # 静默默认

    def _make_shot_segment(self, shot, idx: int, w: int, h: int, fps: int,
                           duration: float) -> Optional[Path]:
        """单镜头片段：优先 Agnes 视频，否则 Ken Burns 兜底（用 first_frame 选中候选）。"""
        seg = self.cache_dir / f"shot_{idx:03d}.mp4"
        scale_pad = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}")
        vp = shot.video.local
        if vp and Path(str(vp)).exists():
            cmd = ["ffmpeg", "-y", "-i", str(vp), "-vf", scale_pad,
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(seg)]
            r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
            if r.returncode == 0 and seg.exists():
                return seg
            logger.warning(f"视频段处理失败({shot.shot_id}): {r.stderr[-300:]}")
        fp = shot.first_frame.selected.local if shot.first_frame.selected else None
        if fp and Path(str(fp)).exists():
            frames = max(int(duration * fps), 1)
            zoompan = f"zoompan=z='min(zoom+0.0015,1.5)':d={frames}:s={w}x{h}:fps={fps}"
            cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(fp),
                   "-vf", f"scale={w*2}:{h*2}:force_original_aspect_ratio=decrease,"
                          f"pad={w*2}:{h*2}:(ow-iw)/2:(oh-ih)/2:black,{zoompan},"
                          f"trim=duration={duration},setsar=1",
                   "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-an", str(seg)]
            r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
            if r.returncode == 0 and seg.exists():
                return seg
            logger.warning(f"Ken Burns 失败({shot.shot_id}): {r.stderr[-300:]}")
        return None

    def _build_audio_track(self, shots, timeline) -> Optional[Path]:
        """每镜头音频 apad 补到镜头时长再 concat 成整条 narration。
        无音频用静音。这样音轨长度天然等于视频长度，不靠 adelay、不累积误差。"""
        seg_audios: List[Path] = []
        for i, (sh, tl) in enumerate(zip(shots, timeline)):
            dur = tl["duration"]
            seg = self.cache_dir / f"aud_{i:03d}.m4a"
            ap = sh.audio.local if sh.audio.ready else None
            if ap and Path(str(ap)).exists():
                cmd = ["ffmpeg", "-y", "-i", str(ap),
                       "-af", f"apad=whole_dur={dur:.3f},atrim=0:{dur:.3f},asetpts=N/SR/TB",
                       "-c:a", "aac", "-b:a", "192k", str(seg)]
            else:
                cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                       "-t", f"{dur:.3f}", "-c:a", "aac", "-b:a", "192k", str(seg)]
            r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
            if r.returncode == 0 and seg.exists():
                seg_audios.append(seg)
            else:
                logger.warning(f"音频段 {sh.shot_id} 失败: {r.stderr[-200:]}")
        if not seg_audios:
            return None
        list_file = self.cache_dir / "audio_concat.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for p in seg_audios:
                f.write(f"file '{p.absolute().as_posix()}'\n")
        out = self.cache_dir / "narration.m4a"
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(list_file), "-c", "copy", str(out)],
                           capture_output=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                                "-i", str(list_file), "-c:a", "aac", "-b:a", "192k", str(out)],
                               capture_output=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                logger.warning(f"音轨拼接失败: {r.stderr[-300:]}")
                return None
        return out

    def _mux_shots(self, concat_path: Path, narration, srt_path,
                   w: int, h: int, final_path: Path):
        """视频 + 音轨 + 可选 SRT 烧录。SRT 烧录失败则降级（仅音视频）。"""
        has_audio = narration and Path(str(narration)).exists()
        has_srt = srt_path and Path(str(srt_path)).exists()

        def run_mux(burn: bool):
            cmd = ["ffmpeg", "-y", "-i", str(concat_path)]
            if has_audio:
                cmd += ["-i", str(narration)]
            vf = []
            if burn and has_srt:
                # 实测需「单引号 + \\: 转义」并用；force_style 指定中文字体避免缺字方块
                esc = escape_ffmpeg_path(str(srt_path))
                vf.append(f"subtitles='{esc}':force_style='FontName=Microsoft YaHei,"
                          f"FontSize=20,Outline=1,Shadow=0'")
            if vf:
                cmd += ["-vf", ",".join(vf)]
            cmd += ["-map", "0:v"]
            if has_audio:
                cmd += ["-map", "1:a"]
            cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
            if has_audio:
                cmd += ["-c:a", "aac", "-b:a", "192k"]
            cmd += ["-shortest", str(final_path)]
            return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")

        r = run_mux(True)
        if r.returncode != 0 and has_srt:
            logger.warning(f"含 SRT 烧录失败，降级为仅音视频: {r.stderr[-300:]}")
            r = run_mux(False)
        if r.returncode != 0:
            logger.error(f"合成失败: {r.stderr[-400:]}")

    # ==================== AI-5A：Manifest 驱动合成（不读 shot.final_video）====================
    def compose_video_versions(self, clips: List[Any], output_path,
                               width: int = 1280, height: int = 720, fps: int = 24) -> Path:
        """用 Composition Manifest 的精确 clip 合成完整影片（§三十八/§三十九/§四十/§四十一）。
        - 每个 clip 归一化到 width x height @ fps，libx264 + aac 48000 stereo（concat 兼容）。
        - 保留 Agnes 原生 audio（有音轨时，§四十）；无音轨补等长 silence，避免 concat 失败（§四十一）。
        - 不读 shot.final_video（§三十九），不改任何 Shot（纯输出，§四十四）。
        clips: list[CompositionClip]（含 local_path + duration_s + shot_id）。返回 final.mp4 Path。"""
        seg_paths: List[Path] = []
        for i, clip in enumerate(clips):
            local = str(clip.local_path) if getattr(clip, "local_path", None) else ""
            if not local or not Path(local).exists():
                raise RuntimeError(f"镜头 {getattr(clip,'shot_id','?')} 视频文件不存在: {local}")
            dur = float(clip.duration_s) if getattr(clip, "duration_s", None) else self._probe_file_duration(Path(local))
            if not dur or dur <= 0:
                dur = 3.0
            seg = self._make_manifest_segment(local, i, width, height, fps, getattr(clip, "shot_id", f"shot{i}"))
            if not seg:
                raise RuntimeError(f"镜头 {getattr(clip,'shot_id','?')} 片段生成失败")
            seg_paths.append(seg)
            logger.info(f"  镜头 {getattr(clip,'shot_id','?')} 片段 {dur:.2f}s ({i+1}/{len(clips)})")
        if not seg_paths:
            raise RuntimeError("没有可合成的片段")
        concat_path = self._concat(seg_paths)
        final = Path(output_path)
        final.parent.mkdir(parents=True, exist_ok=True)
        # _concat 输出在 cache_dir；复制到目标输出路径（每 Run 独立 final.mp4，§四十二/§五十八）
        import shutil
        shutil.copy2(concat_path, final)
        logger.info(f"Manifest 合成完成: {final}（{len(seg_paths)} 片段）")
        return final

    def _make_manifest_segment(self, video_local: str, idx: int, w: int, h: int,
                               fps: int, shot_id: str) -> Optional[Path]:
        """单 clip 归一化片段：video scale/pad/fps + audio（原生保留 / 无音轨补 silence）。
        所有片段统一 libx264 + aac 48000 stereo + yuv420p，保证 concat 兼容。"""
        seg = self.cache_dir / f"manifest_{idx:03d}_{shot_id}.mp4"
        scale_pad = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}")
        if self._probe_has_audio(Path(video_local)):
            # 保留 Agnes 原生 audio，归一化到 aac 48000 stereo
            cmd = ["ffmpeg", "-y", "-i", video_local, "-vf", scale_pad,
                   "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                   str(seg)]
        else:
            # 无音轨：anullsrc 补等长 silence（-shortest 以视频长度为准）
            cmd = ["ffmpeg", "-y", "-i", video_local,
                   "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                   "-vf", scale_pad, "-map", "0:v", "-map", "1:a",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                   "-shortest", str(seg)]
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and seg.exists():
            return seg
        logger.warning(f"Manifest 片段生成失败({shot_id}): {r.stderr[-300:]}")
        return None

    @staticmethod
    def _probe_has_audio(path: Path) -> bool:
        """ffprobe 探是否存在音频流。"""
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=10)
            return bool((r.stdout or "").strip())
        except Exception:
            return False

    @staticmethod
    def _probe_file_duration(path: Path) -> float:
        """ffprobe 探文件时长（秒）。失败返回 0。"""
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=10)
            return float((r.stdout or "").strip())
        except Exception:
            return 0.0
