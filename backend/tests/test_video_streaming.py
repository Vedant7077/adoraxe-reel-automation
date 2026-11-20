"""
Tests for video streaming and range request playback.
"""
import subprocess
import pytest
from app.models.models import Video, VideoStatus
from app.repositories.video_repository import VideoRepository
from app.services.video.ffmpeg_service import convert_to_reel


@pytest.fixture
def sample_video(db_session, tmp_path):
    video = Video(
        youtube_video_id="STREAM_TEST_01",
        title="Stream Test Video",
        video_url="https://youtu.be/stream_test",
        status=VideoStatus.IMPORTED,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    # Create synthetic test mp4
    raw_video = str(tmp_path / "sample_source.mp4")
    gen_cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "testsrc=duration=2:size=640x360:rate=30",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=2",
        "-c:v", "libx264", "-c:a", "aac",
        raw_video
    ]
    subprocess.run(gen_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Add asset
    repo = VideoRepository(db_session)
    asset = repo.create_asset(video_id=video.id, file_path=raw_video, original_filename="sample_source.mp4")

    # Also convert a reel version
    reel_video = str(tmp_path / "sample_source_reel.mp4")
    convert_to_reel(raw_video, reel_video, crop_mode="pad")
    repo.mark_asset_processed(asset.id, cloudinary_url=f"file://{reel_video}", cloudinary_public_id=None)
    repo.update_status(video.id, VideoStatus.PROCESSED)

    return video.id


def test_stream_invalid_variant(client, sample_video):
    resp = client.get(f"/api/v1/videos/{sample_video}/stream/invalid_variant")
    assert resp.status_code == 400
    assert "Invalid variant" in resp.json()["detail"]


def test_stream_original_full(client, sample_video):
    resp = client.get(f"/api/v1/videos/{sample_video}/stream/original")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.headers.get("accept-ranges") == "bytes"
    assert len(resp.content) > 0


def test_stream_original_range_seeking(client, sample_video):
    # Test seeking with byte range 0-511
    resp = client.get(
        f"/api/v1/videos/{sample_video}/stream/original",
        headers={"Range": "bytes=0-511"}
    )
    assert resp.status_code == 206
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.headers.get("accept-ranges") == "bytes"
    assert "bytes 0-511/" in resp.headers["content-range"]
    assert len(resp.content) == 512


def test_stream_processed_reel_full(client, sample_video):
    resp = client.get(f"/api/v1/videos/{sample_video}/stream/processed")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.headers.get("accept-ranges") == "bytes"
    assert len(resp.content) > 0


def test_stream_processed_reel_range_seeking(client, sample_video):
    # Test seeking on processed 9:16 reel
    resp = client.get(
        f"/api/v1/videos/{sample_video}/stream/processed",
        headers={"Range": "bytes=100-599"}
    )
    assert resp.status_code == 206
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.headers.get("accept-ranges") == "bytes"
    assert "bytes 100-599/" in resp.headers["content-range"]
    assert len(resp.content) == 500


def test_video_details_response_includes_stream_metadata(client, sample_video):
    resp = client.get(f"/api/v1/videos/{sample_video}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_uploaded_video"] is True
    assert data["has_processed_reel"] is True
    assert data["original_video_url"] == f"/api/v1/videos/{sample_video}/stream/original"
    assert data["processed_video_url"] == f"/api/v1/videos/{sample_video}/stream/processed"
    assert len(data["assets"]) == 1
    assert data["assets"][0]["has_original_file"] is True
    assert data["assets"][0]["has_processed_file"] is True
