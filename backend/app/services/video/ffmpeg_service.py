"""
FFmpeg video processing service.
Converts videos to Instagram Reel compatible format (9:16, 1080x1920).
"""
import os
import subprocess
import json
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Optional, Callable


FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_PATH = shutil.which("ffprobe") or "ffprobe"

# Instagram Reel specifications (as of 2024/2026 Meta Graph API)
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
REEL_MAX_DURATION_SECONDS = 900  # 15 minutes (Meta Instagram Reels Graph API maximum limit)
REEL_MIN_DURATION_SECONDS = 3
REEL_MAX_FILE_SIZE_MB = 1000  # 1GB
REEL_VIDEO_CODEC = "libx264"
REEL_AUDIO_CODEC = "aac"
REEL_AUDIO_BITRATE = "128k"
REEL_VIDEO_BITRATE = "3500k"
REEL_FPS = 30
REEL_PIXEL_FORMAT = "yuv420p"


def get_adaptive_video_bitrate(duration_seconds: Optional[float]) -> str:
    """
    Calculate an optimal video bitrate to ensure the generated Reel
    never exceeds Cloudinary's 100MB free-tier upload limit (targeting <= 92MB).
    For short videos (< 3.5 min), uses standard 3500k high quality.
    For longer videos, scales bitrate so the file fits within Cloudinary limits.
    """
    if not duration_seconds or duration_seconds <= 0:
        return REEL_VIDEO_BITRATE

    # Target 92 MB total container size (leaving headroom under 100MB = 104,857,600 bytes)
    TARGET_TOTAL_BYTES = 92 * 1024 * 1024
    target_total_bps = (TARGET_TOTAL_BYTES * 8) / duration_seconds
    # Audio is 128 kbps
    audio_bps = 128 * 1000
    available_video_bps = target_total_bps - audio_bps

    # Standard default is 3500 kbps, minimum is 450 kbps
    target_video_kbps = min(3500, max(450, int(available_video_bps // 1000)))
    return f"{target_video_kbps}k"


@dataclass
class VideoProbeResult:
    duration_seconds: float
    width: int
    height: int
    codec: str
    fps: float
    file_size_bytes: int
    has_audio: bool
    audio_codec: Optional[str]
    is_valid: bool
    error: Optional[str] = None


def probe_video(file_path: str) -> VideoProbeResult:
    """Run FFprobe on a video file and return metadata."""
    if not os.path.exists(file_path):
        return VideoProbeResult(0, 0, 0, "", 0, 0, False, None, False, error="File not found")

    if not shutil.which("ffprobe"):
        return VideoProbeResult(0, 0, 0, "", 0, 0, False, None, False, error="ffprobe not installed")

    cmd = [
        FFPROBE_PATH, "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        return VideoProbeResult(0, 0, 0, "", 0, 0, False, None, False, error=str(e))

    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    if not video_stream:
        return VideoProbeResult(0, 0, 0, "", 0, 0, False, None, False, error="No video stream found")

    # Parse FPS
    fps_str = video_stream.get("r_frame_rate", "30/1")
    try:
        num, den = fps_str.split("/")
        fps = float(num) / float(den)
    except Exception:
        fps = 30.0

    duration = float(fmt.get("duration", 0))
    file_size = int(fmt.get("size", os.path.getsize(file_path)))

    return VideoProbeResult(
        duration_seconds=duration,
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        codec=video_stream.get("codec_name", ""),
        fps=fps,
        file_size_bytes=file_size,
        has_audio=audio_stream is not None,
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        is_valid=True,
    )


def validate_for_reel(probe: VideoProbeResult, is_output_reel: bool = False) -> tuple[bool, str]:
    """Validate a video probe result against Instagram Reel specs."""
    if not probe.is_valid:
        return False, probe.error or "Invalid video"
    if probe.duration_seconds < REEL_MIN_DURATION_SECONDS:
        return False, f"Video too short ({probe.duration_seconds:.1f}s). Minimum {REEL_MIN_DURATION_SECONDS}s."
    max_duration = REEL_MAX_DURATION_SECONDS if is_output_reel else 90 * 60
    if probe.duration_seconds > max_duration:
        return False, f"Video too long ({probe.duration_seconds:.0f}s). Maximum {max_duration}s."
    if probe.file_size_bytes > REEL_MAX_FILE_SIZE_MB * 1024 * 1024:
        return False, f"File too large ({probe.file_size_bytes // 1024 // 1024}MB). Maximum {REEL_MAX_FILE_SIZE_MB}MB."
    return True, "OK"


def convert_to_reel(
    input_path: str,
    output_path: str,
    crop_mode: str = "blur",
    duration_seconds: Optional[float] = None,
    progress_callback: Optional[Callable[..., None]] = None,
) -> tuple[bool, str]:
    """
    Convert a video to Instagram Reel compatible format (1080x1920, 9:16).

    crop_mode options:
    - "blur" (default): Full content visible, background padded with blurred video (ideal for Reels).
    - "pad": Full content visible, letterbox / pillarbox with black bars.
    - "crop": Scale to cover 9:16 then crop center (zoomed in, cuts left/right edges).

    Always preserves the original file. Writes to output_path only.
    Enforces Meta's 900-second (15-minute) Reel duration limit by trimming if source exceeds 900s.
    Streams real-time progress to progress_callback(percentage: int, stage_desc: str).
    """
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg is not installed"

    # Resolve video duration early for bitrate calculation, duration trimming, and progress calculation
    total_duration = duration_seconds
    if not total_duration or total_duration <= 0:
        try:
            in_probe = probe_video(input_path)
            if in_probe.is_valid and in_probe.duration_seconds > 0:
                total_duration = in_probe.duration_seconds
        except Exception:
            total_duration = 0

    # Ensure generated Reel complies with Meta's 900-second (15-minute) Reel ceiling
    duration_limit_args = []
    effective_duration = total_duration
    if total_duration and total_duration > REEL_MAX_DURATION_SECONDS:
        duration_limit_args = ["-t", str(REEL_MAX_DURATION_SECONDS)]
        effective_duration = float(REEL_MAX_DURATION_SECONDS)

    # Dynamically select bitrate so file size never exceeds Cloudinary's 100MB limit
    v_bitrate = get_adaptive_video_bitrate(effective_duration)
    buf_size = f"{int(v_bitrate.rstrip('k')) * 2}k"

    mode = (crop_mode or "blur").lower().strip()

    if mode in ("blur", "blur_fill", "smart"):
        # Full content visible in center with blurred background filling the 9:16 frame
        filter_complex = (
            f"[0:v]scale={REEL_WIDTH}:{REEL_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={REEL_WIDTH}:{REEL_HEIGHT},"
            f"boxblur=25:5[bg];"
            f"[0:v]scale={REEL_WIDTH}:-2:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-progress", "pipe:1",
            "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", REEL_VIDEO_CODEC,
            "-preset", "fast",
            "-threads", "2",
            "-b:v", v_bitrate,
            "-maxrate", v_bitrate,
            "-bufsize", buf_size,
            "-pix_fmt", REEL_PIXEL_FORMAT,
            "-r", str(REEL_FPS),
            "-c:a", REEL_AUDIO_CODEC,
            "-b:a", REEL_AUDIO_BITRATE,
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            *duration_limit_args,
            output_path,
        ]
    elif mode == "crop":
        # Scale to cover 9:16 then crop center (cuts edges)
        vf = (
            f"scale={REEL_WIDTH}:{REEL_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={REEL_WIDTH}:{REEL_HEIGHT}"
        )
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-progress", "pipe:1",
            "-i", input_path,
            "-vf", vf,
            "-c:v", REEL_VIDEO_CODEC,
            "-preset", "fast",
            "-threads", "2",
            "-b:v", v_bitrate,
            "-maxrate", v_bitrate,
            "-bufsize", buf_size,
            "-pix_fmt", REEL_PIXEL_FORMAT,
            "-r", str(REEL_FPS),
            "-c:a", REEL_AUDIO_CODEC,
            "-b:a", REEL_AUDIO_BITRATE,
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            *duration_limit_args,
            output_path,
        ]
    else:
        # "pad" or any fallback: Scale to fit within 9:16, pad with black
        vf = (
            f"scale={REEL_WIDTH}:{REEL_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={REEL_WIDTH}:{REEL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black"
        )
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-progress", "pipe:1",
            "-i", input_path,
            "-vf", vf,
            "-c:v", REEL_VIDEO_CODEC,
            "-preset", "fast",
            "-threads", "2",
            "-b:v", v_bitrate,
            "-maxrate", v_bitrate,
            "-bufsize", buf_size,
            "-pix_fmt", REEL_PIXEL_FORMAT,
            "-r", str(REEL_FPS),
            "-c:a", REEL_AUDIO_CODEC,
            "-b:a", REEL_AUDIO_BITRATE,
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            *duration_limit_args,
            output_path,
        ]

    calc_duration = effective_duration if (effective_duration and effective_duration > 0) else total_duration

    try:
        with tempfile.TemporaryFile(mode="w+t") as stderr_file:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                bufsize=1,
            )

            last_cb_time = 0.0
            last_reported_pct = -1
            current_speed = "1.0x"

            if proc.stdout:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("speed="):
                        current_speed = line.split("=", 1)[1].strip()
                    elif line.startswith("out_time_us="):
                        val_str = line.split("=", 1)[1].strip()
                        if val_str.isdigit() and calc_duration and calc_duration > 0:
                            us = int(val_str)
                            cur_secs = us / 1_000_000.0
                            pct = min(99, max(0, int((cur_secs / calc_duration) * 100)))
                            now = time.time()
                            # Throttle updates: at most once every 0.75s or if pct jumped by >= 2%
                            if (now - last_cb_time >= 0.75 or pct >= last_reported_pct + 2) and progress_callback:
                                last_cb_time = now
                                last_reported_pct = pct
                                cur_m, cur_s = divmod(int(cur_secs), 60)
                                tot_m, tot_s = divmod(int(calc_duration), 60)
                                stage_msg = f"Encoding 9:16 ({mode.title()}): {cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d} ({pct}%) • {current_speed}"
                                try:
                                    progress_callback(pct, stage_msg)
                                except TypeError:
                                    progress_callback(pct)
                                except Exception:
                                    pass

            proc.wait(timeout=3600)
            if proc.returncode != 0:
                stderr_file.seek(0)
                err_text = stderr_file.read()
                return False, f"FFmpeg error: {err_text[-500:]}"

            if progress_callback:
                try:
                    progress_callback(100, f"FFmpeg {mode.title()} encoding complete")
                except TypeError:
                    progress_callback(100)
                except Exception:
                    pass

            return True, "OK"
    except subprocess.TimeoutExpired:
        return False, "FFmpeg processing timed out (1 hour limit)"
    except Exception as e:
        return False, str(e)
