"""
FFmpeg video processing service.
Converts videos to Instagram Reel compatible format (9:16, 1080x1920).
"""
import os
import subprocess
import json
import shutil
import tempfile
from dataclasses import dataclass
from typing import Optional, Callable


FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_PATH = shutil.which("ffprobe") or "ffprobe"

# Instagram Reel specifications (as of 2024)
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
REEL_MAX_DURATION_SECONDS = 90 * 60  # 90 minutes max technically, but 15min recommended
REEL_MIN_DURATION_SECONDS = 3
REEL_MAX_FILE_SIZE_MB = 1000  # 1GB
REEL_VIDEO_CODEC = "libx264"
REEL_AUDIO_CODEC = "aac"
REEL_AUDIO_BITRATE = "128k"
REEL_VIDEO_BITRATE = "3500k"
REEL_FPS = 30
REEL_PIXEL_FORMAT = "yuv420p"


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


def validate_for_reel(probe: VideoProbeResult) -> tuple[bool, str]:
    """Validate a video probe result against Instagram Reel specs."""
    if not probe.is_valid:
        return False, probe.error or "Invalid video"
    if probe.duration_seconds < REEL_MIN_DURATION_SECONDS:
        return False, f"Video too short ({probe.duration_seconds:.1f}s). Minimum {REEL_MIN_DURATION_SECONDS}s."
    if probe.duration_seconds > REEL_MAX_DURATION_SECONDS:
        return False, f"Video too long ({probe.duration_seconds:.0f}s). Maximum {REEL_MAX_DURATION_SECONDS}s."
    if probe.file_size_bytes > REEL_MAX_FILE_SIZE_MB * 1024 * 1024:
        return False, f"File too large ({probe.file_size_bytes // 1024 // 1024}MB). Maximum {REEL_MAX_FILE_SIZE_MB}MB."
    return True, "OK"


def convert_to_reel(
    input_path: str,
    output_path: str,
    crop_mode: str = "blur",
    progress_callback: Optional[Callable[[int], None]] = None,
) -> tuple[bool, str]:
    """
    Convert a video to Instagram Reel compatible format (1080x1920, 9:16).

    crop_mode options:
    - "blur" (default): Full content visible, background padded with blurred video (ideal for Reels).
    - "pad": Full content visible, letterbox / pillarbox with black bars.
    - "crop": Scale to cover 9:16 then crop center (zoomed in, cuts left/right edges).

    Always preserves the original file. Writes to output_path only.
    """
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg is not installed"

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
            "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", REEL_VIDEO_CODEC,
            "-preset", "fast",
            "-threads", "2",
            "-b:v", REEL_VIDEO_BITRATE,
            "-maxrate", REEL_VIDEO_BITRATE,
            "-bufsize", "7000k",
            "-pix_fmt", REEL_PIXEL_FORMAT,
            "-r", str(REEL_FPS),
            "-c:a", REEL_AUDIO_CODEC,
            "-b:a", REEL_AUDIO_BITRATE,
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
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
            "-i", input_path,
            "-vf", vf,
            "-c:v", REEL_VIDEO_CODEC,
            "-preset", "fast",
            "-threads", "2",
            "-b:v", REEL_VIDEO_BITRATE,
            "-maxrate", REEL_VIDEO_BITRATE,
            "-bufsize", "7000k",
            "-pix_fmt", REEL_PIXEL_FORMAT,
            "-r", str(REEL_FPS),
            "-c:a", REEL_AUDIO_CODEC,
            "-b:a", REEL_AUDIO_BITRATE,
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
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
            "-i", input_path,
            "-vf", vf,
            "-c:v", REEL_VIDEO_CODEC,
            "-preset", "fast",
            "-threads", "2",
            "-b:v", REEL_VIDEO_BITRATE,
            "-maxrate", REEL_VIDEO_BITRATE,
            "-bufsize", "7000k",
            "-pix_fmt", REEL_PIXEL_FORMAT,
            "-r", str(REEL_FPS),
            "-c:a", REEL_AUDIO_CODEC,
            "-b:a", REEL_AUDIO_BITRATE,
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            output_path,
        ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if proc.returncode != 0:
            return False, f"FFmpeg error: {proc.stderr[-500:]}"
        return True, "OK"
    except subprocess.TimeoutExpired:
        return False, "FFmpeg processing timed out (1 hour limit)"
    except Exception as e:
        return False, str(e)
