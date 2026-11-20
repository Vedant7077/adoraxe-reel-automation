# Adoraxe Database Schema Documentation

Database: `adoraxe_db`\
Engine: PostgreSQL 16 (Hosted on existing `shortly-postgres` container)\
ORM: SQLAlchemy 2.0\
Migrations: Alembic

---

## Entity Relationship Overview

```
 ┌───────────────┐
 │   channels    │
 └───────┬───────┘
         │ 1:N
 ┌───────▼───────┐          1:N           ┌──────────────────┐
 │    videos     ├───────────────────────►│   video_assets   │
 └───────┬───────┘                        └──────────────────┘
         │ 1:N
 ┌───────▼───────────────┐  N:1           ┌──────────────────┐
 │   publishing_jobs     │◄───────────────┤instagram_accounts│
 └───────┬───────────────┘                └─────────┬────────┘
         │ 1:1                                      │ 1:N
 ┌───────▼───────────────┐                          │
 │  publishing_history   │◄─────────────────────────┘
 └───────────────────────┘
```

---

## Tables

### 1. `channels`
Stores imported YouTube channels, subscriber statistics, and sync timestamps.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | VARCHAR(36) | PRIMARY KEY | UUID v4 |
| `youtube_channel_id` | VARCHAR(255) | UNIQUE, NOT NULL, INDEX | YouTube channel identifier |
| `name` | VARCHAR(500) | NOT NULL | Channel display name |
| `handle` | VARCHAR(255) | NULLABLE | Channel handle (e.g. `@mkbhd`) |
| `description` | TEXT | NULLABLE | Channel description |
| `thumbnail_url` | TEXT | NULLABLE | URL to avatar image |
| `banner_url` | TEXT | NULLABLE | URL to banner image |
| `subscriber_count` | BIGINT | NULLABLE | Total subscribers |
| `video_count` | INTEGER | NULLABLE | Total video uploads |
| `channel_url` | TEXT | NOT NULL | Full channel URL |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT true | Ingestion active toggle |
| `last_synced_at` | TIMESTAMP | NULLABLE | Last metadata sync |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Creation timestamp |
| `updated_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Last update timestamp |

---

### 2. `videos`
Stores ingested video metadata, view counts, and processing state.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | VARCHAR(36) | PRIMARY KEY | UUID v4 |
| `channel_id` | VARCHAR(36) | FOREIGN KEY (channels.id) | Owning channel |
| `youtube_video_id` | VARCHAR(255) | UNIQUE, NOT NULL, INDEX | YouTube video identifier |
| `title` | VARCHAR(1000) | NOT NULL | Video title |
| `description` | TEXT | NULLABLE | Video description |
| `thumbnail_url` | TEXT | NULLABLE | Thumbnail image URL |
| `duration_seconds` | INTEGER | NULLABLE | Video duration in seconds |
| `view_count` | BIGINT | NULLABLE | Total views |
| `video_url` | TEXT | NOT NULL | Canonical video link |
| `channel_name` | VARCHAR(500) | NULLABLE | Channel attribution |
| `channel_url` | TEXT | NULLABLE | Channel link |
| `uploaded_at` | TIMESTAMP | NULLABLE | YouTube upload timestamp |
| `status` | ENUM | NOT NULL, DEFAULT 'IMPORTED' | `IMPORTED`, `PROCESSING`, `PROCESSED`, `FAILED`, `READY` |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Creation timestamp |
| `updated_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Last update timestamp |

---

### 3. `video_assets`
Stores physical video files, transcode parameters, and Cloudinary CDN URLs.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | VARCHAR(36) | PRIMARY KEY | UUID v4 |
| `video_id` | VARCHAR(36) | FOREIGN KEY (videos.id) ON DELETE CASCADE | Associated video |
| `original_filename` | VARCHAR(1000) | NULLABLE | Name of uploaded file |
| `file_path` | TEXT | NULLABLE | Local filesystem path |
| `cloudinary_public_id`| VARCHAR(500) | NULLABLE | Cloudinary public asset ID |
| `cloudinary_url` | TEXT | NULLABLE | Secure HTTPS delivery URL |
| `duration_seconds` | INTEGER | NULLABLE | Probed duration |
| `width` | INTEGER | NULLABLE | Probed horizontal resolution |
| `height` | INTEGER | NULLABLE | Probed vertical resolution |
| `codec` | VARCHAR(100) | NULLABLE | Video codec (e.g. `h264`) |
| `file_size_bytes` | BIGINT | NULLABLE | File size |
| `is_processed` | BOOLEAN | NOT NULL, DEFAULT false | Transcoding status |
| `is_reel_compatible`| BOOLEAN | NOT NULL, DEFAULT false | 9:16 Reel compatibility flag |
| `processing_error` | TEXT | NULLABLE | FFmpeg error output |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Creation timestamp |
| `updated_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Last update timestamp |

---

### 4. `instagram_accounts`
Stores authenticated Instagram Creator / Professional accounts.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | VARCHAR(36) | PRIMARY KEY | UUID v4 |
| `instagram_user_id` | VARCHAR(255) | UNIQUE, NOT NULL, INDEX | Meta Graph user ID |
| `username` | VARCHAR(255) | NOT NULL | Instagram handle |
| `full_name` | VARCHAR(500) | NULLABLE | Account display name |
| `profile_picture_url`| TEXT | NULLABLE | Avatar URL |
| `account_type` | VARCHAR(50) | NULLABLE | `BUSINESS` or `CREATOR` |
| `followers_count` | INTEGER | NULLABLE | Follower count |
| `following_count` | INTEGER | NULLABLE | Following count |
| `encrypted_access_token`| TEXT | NULLABLE | **Fernet-encrypted** OAuth access token |
| `token_expires_at` | TIMESTAMP | NULLABLE | Expiration datetime |
| `status` | ENUM | NOT NULL, DEFAULT 'NOT_CONNECTED' | `NOT_CONNECTED`, `CONNECTED`, `TOKEN_EXPIRED`, `ERROR` |
| `connected_at` | TIMESTAMP | NULLABLE | Connection timestamp |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Creation timestamp |
| `updated_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Last update timestamp |

---

### 5. `publishing_jobs`
Tracks execution state of Reel processing and dispatch pipelines.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | VARCHAR(36) | PRIMARY KEY | UUID v4 |
| `video_id` | VARCHAR(36) | FOREIGN KEY (videos.id) | Target video |
| `instagram_account_id`| VARCHAR(36) | FOREIGN KEY (instagram_accounts.id) | Destination account |
| `video_asset_id` | VARCHAR(36) | FOREIGN KEY (video_assets.id) | Transcoded asset |
| `caption` | TEXT | NULLABLE | Reel caption (<= 2200 chars) |
| `status` | ENUM | NOT NULL, DEFAULT 'PENDING' | `PENDING`, `PROCESSING`, `FINISHED`, `FAILED`, `SIMULATED` |
| `instagram_container_id`| VARCHAR(255)| NULLABLE | Meta Graph creation ID |
| `cloudinary_url` | TEXT | NULLABLE | Public HTTPS video URL |
| `processing_progress`| INTEGER | NOT NULL, DEFAULT 0 | 0 - 100 percentage |
| `error_message` | TEXT | NULLABLE | Failure reason |
| `rights_confirmed` | BOOLEAN | NOT NULL, DEFAULT false | User rights declaration |
| `is_demo` | BOOLEAN | NOT NULL, DEFAULT false | Simulation flag |
| `scheduled_at` | TIMESTAMP | NULLABLE | Scheduled dispatch time |
| `started_at` | TIMESTAMP | NULLABLE | Execution start |
| `completed_at` | TIMESTAMP | NULLABLE | Execution end |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Creation timestamp |
| `updated_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Last update timestamp |

---

### 6. `publishing_history`
Immutable audit log of all publishing events and returned Meta Media IDs.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | VARCHAR(36) | PRIMARY KEY | UUID v4 |
| `publishing_job_id` | VARCHAR(36) | FOREIGN KEY (publishing_jobs.id) | Originating job |
| `video_id` | VARCHAR(36) | FOREIGN KEY (videos.id) | Source video |
| `instagram_account_id`| VARCHAR(36) | FOREIGN KEY (instagram_accounts.id) | Destination account |
| `video_title` | VARCHAR(1000) | NOT NULL | Video title |
| `instagram_username`| VARCHAR(255) | NULLABLE | Destination handle |
| `instagram_media_id` | VARCHAR(255) | NULLABLE | Live Instagram Media ID |
| `status` | ENUM | NOT NULL, DEFAULT 'PENDING' | `PENDING`, `PROCESSING`, `PUBLISHED`, `FAILED`, `SIMULATED` |
| `error_message` | TEXT | NULLABLE | Error detail if failed |
| `published_at` | TIMESTAMP | NULLABLE | Live confirmation timestamp |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() | Record creation |
