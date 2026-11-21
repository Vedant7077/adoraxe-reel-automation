"""
Tests for FFmpeg video processing and validation.
"""
import os
import tempfile
import pytest
from app.services.video.ffmpeg_service import (
    probe_video,
    validate_for_reel,
    convert_to_reel,
    VideoProbeResult,
    FFMPEG_PATH,
    FFPROBE_PATH,
)


def test_probe_nonexistent_file():
    result = probe_video("/path/to/nonexistent/file.mp4")
    assert not result.is_valid
    assert result.error == "File not found"


def test_validate_for_reel_specs():
    # Valid probe
    valid_probe = VideoProbeResult(
        duration_seconds=30.0,
        width=1080,
        height=1920,
        codec="h264",
        fps=30.0,
        file_size_bytes=10 * 1024 * 1024,
        has_audio=True,
        audio_codec="aac",
        is_valid=True,
    )
    is_ok, reason = validate_for_reel(valid_probe)
    assert is_ok
    assert reason == "OK"

    # Valid output reel (within 900s)
    is_ok_out, reason_out = validate_for_reel(valid_probe, is_output_reel=True)
    assert is_ok_out
    assert reason_out == "OK"

    # Too long output reel (> 900s)
    long_probe = VideoProbeResult(
        duration_seconds=914.0,
        width=1080,
        height=1920,
        codec="h264",
        fps=30.0,
        file_size_bytes=50 * 1024 * 1024,
        has_audio=True,
        audio_codec="aac",
        is_valid=True,
    )
    is_ok_long_orig, _ = validate_for_reel(long_probe, is_output_reel=False)
    assert is_ok_long_orig is True  # Original video can be trimmed

    is_ok_long_out, reason_long_out = validate_for_reel(long_probe, is_output_reel=True)
    assert is_ok_long_out is False
    assert "too long" in reason_long_out.lower()

    # Too short
    short_probe = VideoProbeResult(
        duration_seconds=1.5,
        width=1080,
        height=1920,
        codec="h264",
        fps=30.0,
        file_size_bytes=1024,
        has_audio=True,
        audio_codec="aac",
        is_valid=True,
    )
    is_ok, reason = validate_for_reel(short_probe)
    assert not is_ok
    assert "too short" in reason.lower()

    # Invalid probe
    invalid_probe = VideoProbeResult(
        duration_seconds=0,
        width=0,
        height=0,
        codec="",
        fps=0,
        file_size_bytes=0,
        has_audio=False,
        audio_codec=None,
        is_valid=False,
        error="Corrupt header",
    )
    is_ok, reason = validate_for_reel(invalid_probe)
    assert not is_ok
    assert "Corrupt header" in reason


def test_ffmpeg_probe_and_convert_synthetic():
    """Create a 4-second synthetic test video using ffmpeg and test probe + convert."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg or ffprobe not installed on this machine")

    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "test_input.mp4")
        output_file = os.path.join(tmpdir, "test_output_reel.mp4")

        # Generate a 4-second 1280x720 video with test audio
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=4:size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=4",
            "-c:v", "libx264", "-c:a", "aac",
            input_file
        ]
        res = subprocess.run(cmd, capture_output=True)
        assert res.returncode == 0
        assert os.path.exists(input_file)

        # Test probe
        probe = probe_video(input_file)
        assert probe.is_valid
        assert probe.duration_seconds >= 3.9
        assert probe.width == 1280
        assert probe.height == 720
        assert probe.has_audio

        # Test validation
        valid, msg = validate_for_reel(probe)
        assert valid

        # Test conversion to 9:16 padded
        success, err = convert_to_reel(input_file, output_file, crop_mode="pad")
        assert success
        assert os.path.exists(output_file)

        # Verify output dimensions
        out_probe = probe_video(output_file)
        assert out_probe.is_valid
        assert out_probe.width == 1080
        assert out_probe.height == 1920

        # Test conversion to 9:16 with blur fill (full content visible with blurred backdrop)
        output_blur = os.path.join(tmpdir, "test_output_blur.mp4")
        success_b, err_b = convert_to_reel(input_file, output_blur, crop_mode="blur")
        assert success_b
        assert os.path.exists(output_blur)
        out_blur_probe = probe_video(output_blur)
        assert out_blur_probe.is_valid
        assert out_blur_probe.width == 1080
        assert out_blur_probe.height == 1920

        # Test conversion to 9:16 crop center
        output_crop = os.path.join(tmpdir, "test_output_crop.mp4")
        success_c, err_c = convert_to_reel(input_file, output_crop, crop_mode="crop")
        assert success_c
        assert os.path.exists(output_crop)
        out_crop_probe = probe_video(output_crop)
        assert out_crop_probe.is_valid
        assert out_crop_probe.width == 1080
        assert out_crop_probe.height == 1920
