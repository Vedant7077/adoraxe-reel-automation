# Adoraxe Implementation Plan

## 1. Project Overview & Architectural Vision
Adoraxe is an enterprise-grade YouTube-to-Instagram Reel Automation Portal designed to streamline the lifecycle of longform content ingestion, video transcoding, asset staging, and scheduled or automated dispatch to Instagram via the official Meta Graph API.

### High-Level Architecture
```
┌────────────────────────────────────────────────────────┐
│             Adoraxe SvelteKit Frontend                 │
│   (Dashboard, Channels, Library, Queue, History, etc.) │
└───────────────────────────┬────────────────────────────┘
                            │ REST JSON / Multipart
┌───────────────────────────▼────────────────────────────┐
│              FastAPI Asynchronous Backend              │
│  ├── YouTube Metadata Provider (Demo & yt-dlp)         │
│  ├── FFmpeg Transcoding Engine (9:16 Pad & Crop)       │
│  ├── Fernet Token Security Engine                      │
│  ├── Cloudinary Asset Delivery Service                 │
│  └── Meta Graph API v21.0 Reel Publisher               │
└─────────────┬───────────────────────────┬──────────────┘
              │                           │
┌─────────────▼──────────────┐ ┌──────────▼──────────────┐
│  Existing Docker Postgres  │ │    Cloudinary CDN /     │
│       (adoraxe_db)         │ │   Meta Graph API v21    │
└────────────────────────────┘ └─────────────────────────┘
```

## 2. Phase-by-Phase Roadmap

### Phase 1: Exploration, UI Design & Scaffolding
- **Stitch MCP UI Design**: Initialized project `Adoraxe – YouTube to Instagram Reel Automation Portal` (`13423803999737446783`), established dark-mode brand design system (`assets/14450634496556538111`), and generated all 8 primary screens.
- **Docker PostgreSQL Inspection**: Detected active healthy PostgreSQL 16 container (`shortly-postgres`). Created isolated database `adoraxe_db` owned by `shortly_user`.
- **System Tooling**: Verified Python 3.13, Node.js 22, and installed FFmpeg 9.0.2 with ffprobe.
- **Security & Git**: Initialized `.gitignore` and `.env.example` protecting all tokens, video assets, database dumps, and credentials.

### Phase 2: Frontend Implementation (SvelteKit + Tailwind CSS)
- Initialized SvelteKit TypeScript project with Tailwind CSS.
- Configured custom typography (Plus Jakarta Sans + Inter) and Instagram gradient palette (`#E1306C` to `#F77737`).
- Implemented all 8 pages:
  - `/` (Dashboard Overview with 4 stat cards, URL ingest, recent jobs)
  - `/channels` (YouTube channel manager with state previews)
  - `/library` (3-column video cards, filtering, search, bulk selection)
  - `/library/[id]` (16:9 player, metadata inspection, transcode stepper)
  - `/instagram` (OAuth connection card, profile statistics, token health)
  - `/queue` (Selected video dispatch, caption editor, 2200 char counter, rights confirmation)
  - `/history` (Publishing audit log, status indicators, Instagram Media IDs)
  - `/settings` (Real-time telemetry, service indicators, sandbox toggle)

### Phase 3: Backend API & Database Infrastructure
- Scaffolded modular FastAPI backend: `api/routes`, `core`, `db`, `models`, `schemas`, `repositories`, `services`.
- Built 6 SQLAlchemy 2.0 models using UUID primary keys, foreign key constraints, and indexes.
- Configured Alembic migration environment (`001_initial_schema`) and applied tables to `adoraxe_db`.

### Phase 4: YouTube Ingestion Module
- Defined abstract base class `BaseYouTubeProvider`.
- Implemented `DemoYouTubeProvider` returning sample metadata with explicit `[DEMO]` labeling.
- Implemented `YtDlpYouTubeProvider` for metadata extraction without video scraping or policy violations.

### Phase 5: FFmpeg Video Processing & Cloudinary
- Built `ffmpeg_service.py` with `ffprobe` stream inspector and `validate_for_reel`.
- Implemented safe aspect ratio transformation (`pad` with black bars vs. `crop` 9:16).
- Built `CloudinaryService` with signed secure HTTPS delivery and token secrecy.

### Phase 6: Instagram OAuth & Security
- Built `InstagramOAuth` targeting Meta Graph API v21.0 with minimal required scopes (`instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`).
- Implemented `TokenManager` using Fernet symmetric encryption to ensure access tokens are never saved or exposed in plaintext.

### Phase 7: Instagram Reel Publishing Pipeline
- Implemented official 10-step asynchronous publishing pipeline:
  1. Validate video & rights confirmation
  2. Verify Instagram connection
  3. Validate Cloudinary media URL
  4. Create Instagram media container
  5. Store container ID
  6. Poll processing status
  7. Wait for `FINISHED`
  8. Call `media_publish`
  9. Store confirmed Instagram Media ID
  10. Update publishing history audit log
- Built `DEMO_MODE` simulation path flagging entries as `SIMULATED`.

### Phase 8: Testing & Verification
- Authored 22 unit, integration, and service tests covering:
  - Health checks & system telemetry
  - Demo provider and provider factory
  - FFmpeg probe, validation, and real synthetic video generation/conversion
  - Token encryption, decryption, and error rejection
  - Cloudinary configuration verification
  - Instagram OAuth URL and scope constraints
  - Pydantic schema validation for rights confirmation and caption limits
- Verified SvelteKit production build (`adapter-auto` SSR/client compilation).
