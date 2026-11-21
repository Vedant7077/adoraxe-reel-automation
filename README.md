# Adoraxe – YouTube to Instagram Reel Automation Portal

[![Backend Tests](https://img.shields.io/badge/pytest-187%20passed%2C%201%20skipped-brightgreen.svg)]()
[![Frontend Build](https://img.shields.io/badge/sveltekit-passing-blue.svg)]()
[![Graph API](https://img.shields.io/badge/Meta%20Graph%20API-v26.0%20Unified%20Login-purple.svg)]()
[![FFmpeg](https://img.shields.io/badge/FFmpeg-9.0.2%20Reel%20Transcoding-orange.svg)]()
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20Docker-blue.svg)]()

Adoraxe is a full-stack, enterprise-grade SaaS platform designed to automate the lifecycle of transforming YouTube long-form and short-form videos into Instagram-compliant 9:16 vertical Reels, and publishing or scheduling them directly to Instagram Creator and Business accounts using the official **Meta Facebook Login for Business (Unified Login)** and **Meta Graph API (v26.0)**.

---

## Table of Contents

- [Problem Statement & Overview](#problem-statement--overview)
- [Key Features](#key-features)
- [Technology Stack](#technology-stack)
- [End-to-End Workflow](#end-to-end-workflow)
- [System Architecture](#system-architecture)
- [Persistent Publishing State Machine](#persistent-publishing-state-machine)
- [Idempotency & Duplicate Protection](#idempotency--duplicate-protection)
- [Timeout Handling & Read-Only Reconciliation](#timeout-handling--read-only-reconciliation)
- [UI & Screenshots](#ui--screenshots)
- [Quick Start & Local Setup](#quick-start--local-setup)
- [Database Setup & Migrations](#database-setup--migrations)
- [Meta & Instagram Configuration](#meta--instagram-configuration)
- [Cloudinary & FFmpeg Configuration](#cloudinary--ffmpeg-configuration)
- [Running Automated Tests](#running-automated-tests)
- [Operating Modes: Demo vs. Live](#operating-modes-demo-vs-live)
- [Troubleshooting Common Errors](#troubleshooting-common-errors)
- [Documentation Index](#documentation-index)
- [Known Limitations & Future Roadmap](#known-limitations--future-roadmap)

---

## Problem Statement & Overview

Content creators and media teams invest significant effort producing YouTube content, but repurposing this video library into vertical formats for Instagram Reels typically requires repetitive manual steps: downloading high-res streams, manual trimming, aspect-ratio reformatting (with letterboxing, center-cropping, or background blur), staging on media CDNs, and navigating platform-specific publishing constraints.

Adoraxe solves this by unifying video ingestion, automated 9:16 transcoding, cloud staging, encrypted OAuth token storage, and resilient Instagram publishing into a single database-backed portal.

---

## Key Features

* **YouTube Channel Ingestion**: Ingests YouTube channel catalogs via handle or URL with subscriber statistics, view counts, upload dates, and video metadata.
* **Intelligent 9:16 Transcoding & Live Progress**: Built-in FFmpeg 9.0.2 processing supporting three conversion modes with real-time percentage progress streaming to the browser:
  * **Crop (Center)**: Automatically crops horizontally to 9:16 without distortion.
  * **Pad (Bars)**: Preserves original aspect ratio with vertical letterboxing.
  * **Blur Fill**: Fills vertical letterbox margins with a blurred, scaled background copy of the video.
* **Strict Technical Reel Compliance & Duration Trimming**: Enforces 1080×1920 resolution, H.264 video codec, AAC stereo audio, 30 fps, Cloudinary 100MB adaptive bitrate scaling, and automated 15-minute (900-second) Meta Reels Graph API ceiling trimming (`-t 900`).
* **Cloudinary CDN Staging & Instant Verification**: Automatically stages transcoded media to Cloudinary, ensuring signed, byte-range compliant HTTPS URLs required by Meta media containers with zero-latency streaming delivery checks.
* **Meta Unified Login (Facebook Login for Business)**: Secure OAuth connection supporting System-User Access Tokens (SUAT), non-expiring credentials, and automated assigned Instagram account discovery.
* **Encrypted Credential Storage**: Symmetric Fernet encryption for all Instagram OAuth access tokens stored in PostgreSQL. Tokens are never exposed in plaintext.
* **Persistent Publishing State Machine**: 8-stage lifecycle tracked in PostgreSQL (`QUEUED` → `CONTAINER_CREATING` → `CONTAINER_PROCESSING` → `READY_TO_PUBLISH` → `PUBLISH_REQUESTED` → `PUBLISHED`).
* **Extended Meta Container Tolerance**: 10-minute (120 attempts) polling tolerance with real-time heartbeat progress for large video transcodes on Meta's servers, capturing transparent error subcodes without timeouts.
* **Strict Idempotency & Duplicate Prevention**: Cryptographically derived idempotency keys (`pub_{account_id}_{video_id}_{caption_hash}`) block accidental re-posts.
* **Resilient Timeout Handling & Reconciliation**: Network timeouts during publishing transition jobs to `OUTCOME_UNKNOWN` without triggering destructive automatic retries. Safe read-only reconciliation queries Meta Graph API to verify publication.
* **Non-Blocking Decoupled Architecture**: Background publishing execution persists and continues seamlessly even if the user navigates between pages or refreshes the browser.
* **Single Environment Configuration**: Consolidated root `.env` architecture for frontend and backend with fail-closed production security checks.

---

## Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Frontend** | SvelteKit 2, TypeScript, Tailwind CSS, Svelte 5 runes (`$state`, `$derived`, `$props`), Vite |
| **Backend API** | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, httpx |
| **Database** | PostgreSQL 16 (reusing existing Docker container `shortly-postgres`, database `adoraxe_db`) |
| **Video Engine** | FFmpeg 9.0.2, FFprobe, yt-dlp (optional media retriever) |
| **Media CDN** | Cloudinary signed video asset staging & delivery |
| **External APIs** | Meta Facebook Login for Business, Meta Instagram Graph API (v26.0) |
| **Security** | Single-owner administrator authentication (`X-Admin-Key`), fail-closed production gate, Fernet symmetric token encryption (`cryptography`), timing-safe key validation, HTTPS OAuth callbacks, CORS isolation |
| **Testing** | Pytest, pytest-asyncio, svelte-check, Vite production builder |

---

## End-to-End Workflow

```
[1. YouTube Channel]
       │
       ▼
[2. Ingestion & Catalog] ─── Ingest channel handle or URL; cache video metadata
       │
       ▼
[3. Video Selection] ────── Select eligible YouTube video from Video Library
       │
       ▼
[4. FFmpeg Transcoding] ─── Transcode to 9:16 vertical (Crop Center / Pad Bars / Blur Fill)
       │
       ▼
[5. Cloudinary Staging] ─── Upload processed MP4; verify public HTTPS byte-range stream
       │
       ▼
[6. Queue & Preview] ────── Preview 9:16 player in Queue; select profile; compose caption
       │
       ▼
[7. Meta Unified Login] ─── Authenticate via Facebook Login for Business (SUAT token)
       │
       ▼
[8. Instagram Publish] ──── Create media container; poll processing; dispatch media_publish
       │
       ▼
[9. History Tracking] ───── Store media ID & permalink; provide read-only audit log
```

---

## System Architecture

Adoraxe is architected as a decoupled system where state persistence is strictly isolated from HTTP request lifecycles.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        SvelteKit Frontend                              │
│  Dashboard  │  Channels  │  Library  │  Instagram  │  Queue  │ History │
│                                                                        │
│                 publishingStore (Svelte 5 Runes)                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / REST
┌───────────────────────────────────▼────────────────────────────────────┐
│                        FastAPI Backend Layer                           │
│  Lifespan Startup Recovery  │  Pydantic Schemas  │  CORS Middleware    │
├────────────────────────────────────────────────────────────────────────┤
│                           Service Layer                                │
│   YouTube Provider   │   FFmpeg Processor   │   Cloudinary CDN Client  │
│   Token Manager      │   Meta OAuth Client  │   Instagram Publisher    │
├────────────────────────────────────────────────────────────────────────┤
│                          Repository Layer                              │
│   ChannelRepo │ VideoRepo │ AssetRepo │ AccountRepo │ JobRepo │ HistRepo   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ SQLAlchemy 2.0
┌───────────────────────────────────▼────────────────────────────────────┐
│                    PostgreSQL 16 (adoraxe_db)                          │
│ channels │ videos │ video_assets │ instagram_accounts │ jobs │ history │
└────────────────────────────────────────────────────────────────────────┘
```

For complete technical specifications, see [docs/ARCHITECTURE.md](file:///Users/shankar/Downloads/adoraxe-reel-automation/docs/ARCHITECTURE.md).

---

## Persistent Publishing State Machine

Every publishing attempt creates a persistent database row in `publishing_jobs` before any external network requests are dispatched.

```mermaid
stateDiagram-v2
    [*] --> QUEUED: Job Created (idempotency key stored)
    QUEUED --> CONTAINER_CREATING: Worker dispatched
    CONTAINER_CREATING --> CONTAINER_PROCESSING: Container ID assigned by Meta
    CONTAINER_PROCESSING --> READY_TO_PUBLISH: Meta container status FINISHED
    CONTAINER_PROCESSING --> FAILED: Meta reports transcoding error
    READY_TO_PUBLISH --> PUBLISH_REQUESTED: media_publish sent (60s timeout)
    PUBLISH_REQUESTED --> PUBLISHED: Meta confirms live Reel (200 OK)
    PUBLISH_REQUESTED --> OUTCOME_UNKNOWN: Network timeout or socket drop
    OUTCOME_UNKNOWN --> RECONCILIATION_REQUIRED: Safe recovery required
    RECONCILIATION_REQUIRED --> PUBLISHED: Read-only check detects live Reel
    RECONCILIATION_REQUIRED --> FAILED: Read-only check confirms absence
    PUBLISHED --> [*]
    FAILED --> [*]
```

### The 8 Lifecycle Stages

1. **`QUEUED`**: Initial persistent database state. Idempotency key verified.
2. **`CONTAINER_CREATING`**: Dispatches `POST /{ig_user_id}/media` with Cloudinary HTTPS video URL and caption.
3. **`CONTAINER_PROCESSING`**: Polls Meta's container status (`GET /{container_id}?fields=status_code`) until `FINISHED`.
4. **`READY_TO_PUBLISH`**: Container transcoding is verified complete on Meta servers.
5. **`PUBLISH_REQUESTED`**: Dispatches `POST /{ig_user_id}/media_publish` with a 60-second read timeout.
6. **`PUBLISHED`**: Meta returns confirmed `media_id`. Live permalink and timestamps are permanently stored.
7. **`OUTCOME_UNKNOWN`**: Network connection dropped or timed out after the request reached Meta.
8. **`RECONCILIATION_REQUIRED`**: Flags the job for safe read-only reconciliation before permitting user retries.

---

## Idempotency & Duplicate Protection

Publishing content to a live social channel must never result in duplicate posts. Adoraxe enforces three layers of duplicate protection:

1. **Deterministic Idempotency Key**:
   $$\text{idempotency\_key} = \text{"pub\_"} + \text{account\_id} + \text{"\_"} + \text{video\_id} + \text{"\_"} + \text{SHA256}(\text{caption})[0:16]$$
   Backed by a PostgreSQL unique index on `publishing_jobs.idempotency_key`. Concurrent submissions return the existing in-flight job rather than spawning a second worker.
2. **Pre-Flight Publication Guard**:
   `JobRepository.can_publish_video()` inspects both active jobs and historical records. Videos that have already been published or simulated are blocked with HTTP `409 Conflict`.
3. **Monotonic History Guarantee**:
   `PublishingHistoryRepository` rejects updates that attempt to overwrite a verified `PUBLISHED` status with `FAILED`.

---

## Timeout Handling & Read-Only Reconciliation

### Why Blind Retries Cause Duplicate Posts
Meta's `media_publish` call is an asynchronous commitment. If an HTTP timeout occurs while Meta is executing publication:
* The client receives a network timeout error.
* However, Meta's backend may have successfully committed and posted the Reel to the user's Instagram profile.
* If the application naively retried `media_publish` on failure, a **duplicate second Reel** would be published.

### Safe Read-Only Reconciliation Protocol
When a publish call times out:
1. The job transitions to `OUTCOME_UNKNOWN` / `RECONCILIATION_REQUIRED`.
2. Automatic retries are strictly prohibited.
3. The user or startup recovery invokes `POST /api/v1/publishing/jobs/{job_id}/reconcile`.
4. Adoraxe executes a safe read-only query to `GET /{ig_user_id}/media?fields=id,caption,permalink,timestamp`.
5. If the Reel is verified live on Instagram, the job is marked `PUBLISHED` and the permalink is recovered.
6. If confirmed absent, the job is marked `FAILED`, safely enabling a deliberate retry.

For complete workflow details, see [docs/PUBLISHING_WORKFLOW.md](file:///Users/shankar/Downloads/adoraxe-reel-automation/docs/PUBLISHING_WORKFLOW.md).

---

## UI & Screenshots

Adoraxe features a dark SaaS design system (inspired by Instagram tones and modern glass-morphism) developed using SvelteKit 2, Tailwind CSS, and custom glass-morphic components.

### 1. Dashboard & Publishing Activity Overview
![Dashboard Overview](docs/screenshots/dashboard.jpg)

*Monitor active processing jobs, total imported videos, publication success rates, and live publishing history across connected accounts.*

---

### 2. YouTube Channel Ingestion & Catalog
![YouTube Channel Catalog](docs/screenshots/channels.jpg)

*Ingest any public YouTube channel by handle or URL. Browse cataloged videos with view counts, durations, and one-click import into the automation pipeline.*

---

### 3. Video Library & Side-by-Side Comparison
![Side-by-Side Video Comparison](docs/screenshots/library_comparison.jpg)

*Inspect and compare original 16:9 source video alongside the transcoded 9:16 vertical Reel. Choose between **Blur Fill**, **Pad (Bars)**, or **Crop Center** modes with built-in yt-dlp media acquisition and ownership verification.*

---

### 4. Real-Time Conversion & Encoding Progress
![Real-Time Encoding Progress](docs/screenshots/conversion_progress.jpg)

*Live FFmpeg encoding telemetry streamed directly to the browser with percentage completion, elapsed vs. total timestamps, transcode speed, and automatic duration limiting to Meta's 15-minute maximum.*

---

### 5. Publishing Queue with 9:16 Reel Player & Caption Composer
![Publishing Queue](docs/screenshots/publishing_queue.jpg)

*Select ready Reels from the queue, preview in a vertical 9:16 HTML5 video player, compose rich captions with hashtags/emojis, verify Cloudinary HTTPS delivery, and dispatch directly to Instagram Creator profiles.*

---

### 6. Instagram Connection Details & Token Diagnostics
![Instagram Account Diagnostics](docs/screenshots/instagram.jpg)

*Verify Meta Unified Login connection health, System-User Access Token (SUAT) validity, non-expiring credentials, Fernet encryption state, and assigned Instagram Creator permissions.*

---

---

## Quick Start & Local Setup

### 1. Prerequisites
* Python 3.11+
* Node.js 20+
* Docker (running PostgreSQL 16)
* FFmpeg 9.0.2+

For detailed step-by-step setup, see [docs/SETUP.md](file:///Users/shankar/Downloads/adoraxe-reel-automation/docs/SETUP.md).

### 2. Configuration Setup
```bash
cp .env.example .env
```
Generate your Fernet encryption key and secret key:
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python3 -c "import secrets; print(secrets.token_hex(32))"
```
Add the keys to your `.env`.

### 3. Backend Startup
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run pytest suite
pytest -q

# Start FastAPI server on http://localhost:8000
python run.py
```

### 4. Frontend Startup
```bash
cd frontend
npm install

# Build check
npm run build

# Start SvelteKit development server on http://localhost:5173
npm run dev
```

---

## Database Setup & Production Migrations

Adoraxe supports both local Docker PostgreSQL (`adoraxe_db`) and hosted PostgreSQL (Neon, Supabase, AWS RDS). Database schema versioning is managed via **Alembic**:
* `001_initial_schema`: Initial tables for channels, videos, assets, accounts, jobs, and history.
* `002_publishing_state_machine`: 8-stage publishing lifecycle columns, `idempotency_key` (unique), live Instagram `permalink`, and diagnostics.

### Local Docker Database
```bash
# Verify PostgreSQL container is healthy:
docker ps --filter "name=shortly-postgres"

# Inspect database tables:
docker exec shortly-postgres psql -U adoraxe_user -d adoraxe_db -c "\dt"

# Run migrations to head:
cd backend
./venv/bin/alembic upgrade head
```

### Production Hosted Database (Neon / Supabase)
On fresh cloud databases, migrations execute automatically on container startup (`backend/Dockerfile` CMD runs `alembic upgrade head`). Alternatively, execute migrations directly from your workstation:
```bash
cd backend
DATABASE_URL="postgresql+psycopg2://user:pass@ep-xyz.neon.tech/adoraxe_db?sslmode=require" ./venv/bin/alembic upgrade head
```

For schema details and entity relationship diagrams, see [docs/DATABASE_SCHEMA.md](file:///Users/shankar/Downloads/adoraxe-reel-automation/docs/DATABASE_SCHEMA.md).

---

## Meta & Instagram Configuration

To connect live Instagram Creator or Business accounts:
1. Create a Meta Developer App with **Facebook Login for Business**.
2. Configure **General** variation and **System-user access token (SUAT)** with expiration set to **Never**.
3. Request scopes:
   * `instagram_basic`
   * `instagram_content_publish`
   * `instagram_manage_messages`
   * `public_profile`
4. Register your HTTPS redirect URI (e.g. `https://xxxx.ngrok-free.app/api/v1/instagram/callback`).
5. Configure `.env`:
   ```env
   META_APP_ID=your_meta_app_id
   META_APP_SECRET=your_meta_app_secret
   META_CONFIG_ID=your_configuration_id
   META_REDIRECT_URI=https://xxxx.ngrok-free.app/api/v1/instagram/callback
   META_API_VERSION=v26.0
   ```

---

## Cloudinary & FFmpeg Configuration

* **FFmpeg Installation**:
  ```bash
  # macOS:
  brew install ffmpeg
  # Linux:
  sudo apt install -y ffmpeg
  ```
* **Cloudinary CDN Credentials**:
  Set `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, and `CLOUDINARY_API_SECRET` in `.env`.
  Verify via `http://localhost:8000/api/v1/health` where `cloudinary_configured: true` will be confirmed.

---

## Running Automated Tests

### Backend Test Suite (Pytest)
```bash
cd backend
./venv/bin/pytest -q
```
* **Result**: **160 passed**, 1 skipped (live network upload skipped in offline sandbox), 0 failed. Includes complete crash recovery and state-machine tests.

### Frontend Type & Build Verification
```bash
cd frontend
npx svelte-check --tsconfig ./tsconfig.json
npm run build
```
* **Result**: **0 errors**, production bundle compiled in ~500ms.

---

## Production Cloud Deployment (Vercel + Render + Neon)

Adoraxe is pre-configured for deployment without paying for cloud servers:
* **Frontend**: Hosted on [Vercel](https://vercel.com) (`@sveltejs/adapter-auto`, Root Directory `frontend`).
* **Backend**: Hosted on [Render](https://render.com) as a Docker Web Service (`backend/Dockerfile`, Root Directory `backend`, Blueprint `render.yaml`).
* **Database**: Serverless PostgreSQL on [Neon.tech](https://neon.tech) (Free Tier, SSL enabled).
* **Media CDN**: Cloudinary signed video asset delivery.

### Single-Owner Administrator Authentication (Production Security)
Adoraxe features a dedicated fail-closed administrator authentication system designed specifically for single-owner portfolio and demo deployments:
* **Header Authorization**: Every API route (except `/health` and `/instagram/callback`) enforces the `X-Admin-Key` header with timing-attack resistant comparison (`secrets.compare_digest`).
* **Session Storage Only**: The administrator enters their key once per browser session. The key is stored strictly in `sessionStorage` (never in `localStorage`, cookies, or JavaScript bundles) and cleared on logout or HTTP 401.
* **Fail-Closed Gate**: If `AUTH_ENABLED=true` (or running in production on Render), but `ADMIN_API_KEY` is missing or under 16 characters, the backend aborts startup immediately to prevent unauthenticated public exposure.
* **Local Development**: Unauthenticated local development is preserved via `AUTH_ENABLED=false` (default) without altering local configurations.

### Production YouTube Ingestion & Media Retrieval
* **YouTube Metadata Provider (`YOUTUBE_PROVIDER="http"`)**: Render production uses the lightweight HTTP metadata scraper (`HttpScraperService`) to ingest live YouTube channel catalogs and video metadata without requiring Google YouTube Data API keys or browser automation overhead.
* **Direct Media Retrieval (`YTDLP_ENABLED="true"`)**: Direct video stream extraction via `yt-dlp` is enabled in production. The backend Docker image (`backend/Dockerfile`) installs NodeSource Node.js 22.x (required by `yt-dlp >= 2026.10` for `yt-dlp-ejs` JavaScript challenge solving) and `ffmpeg`, ensuring seamless downloading and 9:16 vertical transcoding on Render.

### Automated Boot Migrations
The production `backend/Dockerfile` executes `alembic upgrade head` automatically on container boot, meaning zero manual shell or pre-deploy commands are required on Render.

### Render Free Tier Limitations & Workarounds
* **15-Minute Sleep**: Render free web services spin down after 15 minutes of zero HTTP traffic. Active frontend polling while on `/queue` or `/history` maintains activity during publishing.
* **512 MB Memory Budget**: FFmpeg operations are optimized for 1080p, 4.5 Mbps H.264, 30fps under 60s Reels, consuming ~250MB RAM.
* **Crash Recovery**: If Render stops an in-flight background task during a deploy, FastAPI's `lifespan` hook automatically recovers the job upon reboot via read-only reconciliation.

### Meta App Review & Access Modes
* **Development Mode (Current)**: Allows immediate, unrestricted live publishing for any Instagram account registered with an **App Role** (Admin, Developer, Tester) in your Meta Developer App. No App Review required.
* **Live Mode (Public Users)**: Requires Business Verification and Meta App Review for `instagram_basic` and `instagram_content_publish` before unapproved external users can connect.

---

## Operating Modes: Demo vs. Live

| Feature | `DEMO_MODE=true` (Default) | `DEMO_MODE=false` (Production) |
| :--- | :--- | :--- |
| **YouTube Metadata** | Returns verified sample channels (`[DEMO]`) | Direct HTTP scraping or yt-dlp |
| **Video Transcoding** | Local real FFmpeg 9:16 processing | Local real FFmpeg 9:16 processing |
| **Cloudinary Delivery** | Full signed upload & verification | Full signed upload & verification |
| **Instagram Connection** | Simulated demo identity | Live Meta Unified Login (SUAT) |
| **Publishing Execution** | Simulated stages (`SIMULATED`), no live API calls | Live Meta Graph API `media_publish` |
| **Duplicate Prevention** | Active (blocks repeated simulated posts) | Active (blocks duplicate live Reels) |

---

## Troubleshooting Common Errors

### 1. Meta OAuth Error 200 (`Permissions=error`)
* **Cause**: The Instagram Creator account is not linked to a Facebook Page inside your Meta Business Portfolio, or the ad account field was misconfigured.
* **Resolution**: In Meta Business Manager, link your Instagram account to a Facebook Page. In Facebook Login for Business configuration, ensure **Ad account** is set to `Optional`.

### 2. Container Processing Timeout (`CONTAINER_PROCESSING`)
* **Cause**: Video file is too large or Cloudinary URL did not respond to Meta's byte-range probe.
* **Resolution**: Ensure the video duration is under 60 seconds and bitrate is around 4.5 Mbps. Cloudinary delivery handles byte-ranges automatically.

### 3. Cloudinary Upload Failure (`verify_url` returns false)
* **Cause**: Incorrect Cloudinary API credentials or network restriction.
* **Resolution**: Verify credentials in `.env` and test via `GET /api/v1/health`.

### 4. Fernet Decryption Error (`InvalidToken`)
* **Cause**: `TOKEN_ENCRYPTION_KEY` was modified in `.env` after an account was connected.
* **Resolution**: Reconnect your Instagram account at `/instagram` to generate and encrypt a fresh token.

---

## Documentation Index

* [docs/ARCHITECTURE.md](file:///Users/shankar/Downloads/adoraxe-reel-automation/docs/ARCHITECTURE.md) – Detailed system architecture, data flow, and security design.
* [docs/SETUP.md](file:///Users/shankar/Downloads/adoraxe-reel-automation/docs/SETUP.md) – Step-by-step local installation and cloud setup guide.
* [docs/PUBLISHING_WORKFLOW.md](file:///Users/shankar/Downloads/adoraxe-reel-automation/docs/PUBLISHING_WORKFLOW.md) – Persistent publishing state machine and reconciliation protocol.
* [docs/API_DOCUMENTATION.md](file:///Users/shankar/Downloads/adoraxe-reel-automation/docs/API_DOCUMENTATION.md) – Complete REST API reference and payload specifications.
* [docs/DATABASE_SCHEMA.md](file:///Users/shankar/Downloads/adoraxe-reel-automation/docs/DATABASE_SCHEMA.md) – PostgreSQL table definitions, indexes, and constraints.

---

## Known Limitations & Future Roadmap

* **Single-Video Trimming**: The current video pipeline processes one 9:16 Reel per YouTube video. Future releases will support automated multi-clip extraction using audio silence detection and transcript analysis.
* **Instagram Carousel & Stories**: Currently focused on vertical Reels. Support for 1:1 image carousels and Stories will be added in v1.1.
* **Scheduled Auto-Publishing Cron**: Background jobs currently execute on immediate demand. Future versions will incorporate Celery / Redis for long-term scheduled calendar posting.
