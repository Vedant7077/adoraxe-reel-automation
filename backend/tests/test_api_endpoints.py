"""
Tests for FastAPI API endpoints: channels, videos, publishing, history, instagram.
"""
def test_import_channel_demo(client):
    response = client.post("/api/v1/channels/import", json={"url": "https://www.youtube.com/@mkbhd"})
    assert response.status_code == 201
    data = response.json()
    assert "[DEMO]" in data["name"]
    assert data["handle"] == "@mkbhd"
    assert "id" in data


def test_list_channels(client):
    # Ensure at least one channel
    client.post("/api/v1/channels/import", json={"url": "https://www.youtube.com/@mkbhd"})
    response = client.get("/api/v1/channels")
    assert response.status_code == 200
    channels = response.json()
    assert isinstance(channels, list)
    assert len(channels) >= 1


def test_list_videos(client):
    response = client.get("/api/v1/videos")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data


def test_instagram_status(client):
    response = client.get("/api/v1/instagram/status")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] in ["NOT_CONNECTED", "CONNECTED", "TOKEN_EXPIRED"]


def test_publish_validation_requires_rights(client, db_session):
    # Missing rights_confirmed
    response = client.post("/api/v1/publishing", json={
        "video_id": "any-video-id",
        "caption": "Test caption",
        "rights_confirmed": False
    })
    assert response.status_code == 422  # Pydantic validation error


def test_publish_caption_length_validation(client):
    # Caption exceeding 2200 chars
    long_caption = "a" * 2201
    response = client.post("/api/v1/publishing", json={
        "video_id": "any-video-id",
        "caption": long_caption,
        "rights_confirmed": True
    })
    assert response.status_code == 422


def test_history_endpoint(client):
    response = client.get("/api/v1/history")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


def test_queue_remove_and_add_endpoints(client, db_session):
    from app.models.models import Video, VideoAsset, VideoStatus

    vid = Video(
        title="Queue Test Video",
        youtube_video_id="yt-queue-test-1",
        video_url="https://youtube.com/watch?v=yt-queue-test-1",
        status=VideoStatus.PROCESSED,
    )
    db_session.add(vid)
    db_session.commit()

    asset = VideoAsset(
        video_id=vid.id,
        is_processed=True,
        is_reel_compatible=True,
        crop_mode="crop",
        width=1080,
        height=1920,
        cloudinary_url="https://res.cloudinary.com/demo/reels/test.mp4",
    )
    db_session.add(asset)
    db_session.commit()

    # 1. Video appears in queue with crop_mode and specs
    res = client.get("/api/v1/publishing/queue-videos")
    assert res.status_code == 200
    items = res.json()
    item = next((i for i in items if i["id"] == vid.id), None)
    assert item is not None
    assert item["crop_mode"] == "crop"
    assert item["width"] == 1080
    assert item["height"] == 1920
    assert item["cloudinary_url"] == "https://res.cloudinary.com/demo/reels/test.mp4"

    # 2. Remove from queue
    del_res = client.post(f"/api/v1/publishing/queue/{vid.id}/remove")
    assert del_res.status_code == 200
    assert del_res.json()["video_id"] == vid.id

    # Verify removed from queue list
    res_after = client.get("/api/v1/publishing/queue-videos")
    assert not any(i["id"] == vid.id for i in res_after.json())

    # 3. Re-add to queue
    add_res = client.post(f"/api/v1/publishing/queue/{vid.id}/add")
    assert add_res.status_code == 200
    assert add_res.json()["video_id"] == vid.id

    # Verify re-appears in queue list
    res_restored = client.get("/api/v1/publishing/queue-videos")
    assert any(i["id"] == vid.id for i in res_restored.json())


def test_instagram_accounts_list_and_activate(client, db_session):
    """Test retrieving connected Instagram accounts and activating one."""
    from app.models.models import InstagramAccount, InstagramAccountStatus

    acc = InstagramAccount(
        instagram_user_id="17841454191511409",
        username="yotube_insta_automation",
        full_name="Vedant",
        account_type="CREATOR",
        status=InstagramAccountStatus.CONNECTED,
        encrypted_access_token="enc_token_test",
    )
    db_session.add(acc)
    db_session.commit()

    res = client.get("/api/v1/instagram/accounts")
    assert res.status_code == 200
    accounts = res.json()
    assert isinstance(accounts, list)
    assert len(accounts) >= 1
    target_id = accounts[0]["id"]

    act_res = client.post(f"/api/v1/instagram/accounts/{target_id}/activate")
    assert act_res.status_code == 200
    data = act_res.json()
    assert data["account_id"] == target_id
    assert "activated" in data["message"]
