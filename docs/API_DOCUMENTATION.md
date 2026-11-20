# Adoraxe API Documentation

Base URL: `http://localhost:8000/api/v1`

Interactive API Docs:
- Swagger UI: `http://localhost:8000/api/docs`
- ReDoc: `http://localhost:8000/api/redoc`
- OpenAPI JSON: `http://localhost:8000/api/openapi.json`

---

## 1. Health & Telemetry

### `GET /health`
Returns live system health telemetry, including PostgreSQL connectivity, FFmpeg installation status, and configuration flags.

**Response:** `200 OK`
```json
{
  "status": "ok",
  "version": "1.0.0",
  "demo_mode": true,
  "database": true,
  "ffmpeg": true,
  "cloudinary_configured": false,
  "instagram_configured": false
}
```

---

## 2. Channels

### `POST /channels/import`
Imports a YouTube channel given a channel URL or handle. In DEMO_MODE, imports verified mock metadata.

**Request Body:**
```json
{
  "url": "https://www.youtube.com/@mkbhd"
}
```

**Response:** `201 Created`
```json
{
  "id": "c1f7b822-4412-4ee1-b92c-87d4615d9a9f",
  "youtube_channel_id": "UCBJycsmduvYEL83R_U4JriQ",
  "name": "[DEMO] Marques Brownlee",
  "handle": "@mkbhd",
  "description": "[DEMO DATA] Quality tech videos. YouTuber. Geek.",
  "thumbnail_url": "https://yt3.googleusercontent.com/...",
  "subscriber_count": 18400000,
  "video_count": 1600,
  "channel_url": "https://www.youtube.com/@mkbhd",
  "is_active": true,
  "last_synced_at": "2026-11-19T11:35:00Z",
  "created_at": "2026-11-19T11:35:00Z"
}
```

### `GET /channels`
Lists all active imported channels.

**Response:** `200 OK` (Array of Channel objects)

### `GET /channels/{id}/videos`
Retrieves video catalog for the specified channel with pagination.

**Query Parameters:**
- `page` (default: 1)
- `page_size` (default: 24)

---

## 3. Videos & Processing

### `GET /videos`
Retrieves paginated video library items with optional status and search filters.

**Query Parameters:**
- `page` (integer, default: 1)
- `page_size` (integer, default: 24)
- `status` (string: `IMPORTED`, `PROCESSING`, `PROCESSED`, `FAILED`, `READY`)
- `search` (string)

**Response:** `200 OK`
```json
{
  "items": [
    {
      "id": "v7d8a923-...",
      "youtube_video_id": "DEMO_VID_001",
      "title": "[DEMO] Sample Video 1",
      "duration_seconds": 660,
      "view_count": 100000,
      "video_url": "https://www.youtube.com/watch?v=DEMO_VID_001",
      "status": "IMPORTED",
      "created_at": "2026-11-19T11:35:00Z"
    }
  ],
  "total": 12,
  "page": 1,
  "page_size": 24,
  "total_pages": 1
}
```

### `GET /videos/{id}`
Returns details for a single video, including associated processing assets.

### `POST /videos/upload`
Uploads an authorized source video file for local processing (multipart/form-data).
- Allowed formats: MP4, MOV, AVI, WEBM, MKV
- Maximum size: 2 GB

### `POST /videos/{id}/process`
Initiates asynchronous FFmpeg transcoding to 9:16 Reel format.

**Request Body:**
```json
{
  "crop_mode": "pad",
  "target_width": 1080,
  "target_height": 1920
}
```

**Response:** `200 OK`
```json
{
  "job_id": "j9d8a111-...",
  "message": "Processing started",
  "status": "PROCESSING"
}
```

### `GET /videos/jobs/{job_id}`
Returns real-time progress and completion status for a video processing job.

---

## 4. Instagram Management

### `GET /instagram/status`
Returns connection state, account profile metrics, and token health.

**Response:** `200 OK`
```json
{
  "status": "NOT_CONNECTED",
  "username": null,
  "full_name": null,
  "followers_count": null,
  "token_expires_at": null,
  "instagram_configured": false,
  "demo_mode": true
}
```

### `GET /instagram/connect`
Generates a secure Meta OAuth 2.0 authorization URL containing a cryptographically random `state` parameter to prevent CSRF.

### `GET /instagram/callback`
OAuth callback redirect handler. Validates `state`, exchanges authorization `code` for long-lived access token, encrypts token using Fernet, and stores account details.

### `POST /instagram/disconnect`
Disconnects active Instagram account and purges encrypted access credentials.

---

## 5. Reel Publishing

### `POST /publishing`
Creates and dispatches an Instagram Reel publishing job.

**Validation Rules:**
- `rights_confirmed` must be strictly `true`
- `caption` maximum 2200 characters
- Duplicate publishing protection: rejects dispatch if a job is already in progress for this video

**Request Body:**
```json
{
  "video_id": "v7d8a923-...",
  "caption": "Check out this workflow! #tech #reels",
  "rights_confirmed": true
}
```

**Response:** `200 OK`
```json
{
  "job_id": "p4d1a999-...",
  "status": "PENDING",
  "message": "SIMULATED publish started (DEMO_MODE)"
}
```

### `GET /publishing/{id}`
Returns status and container details for an active publishing job.

---

## 6. History

### `GET /history`
Returns paginated publishing history audit log.

**Query Parameters:**
- `page` (default: 1)
- `page_size` (default: 20)
- `status` (`PUBLISHED`, `SIMULATED`, `FAILED`)
