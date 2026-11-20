# Adoraxe Installation & Setup Guide

This guide walks through configuring and running the **Adoraxe YouTube-to-Instagram Reel Automation Portal** in both local development and production environments.

---

## 1. Prerequisites

Ensure your development machine meets the following requirements:

* **Operating System**: macOS (ARM/Intel), Linux (Ubuntu 20.04+), or Windows (WSL2 recommended)
* **Python**: `3.11` to `3.13` (`python3 --version`)
* **Node.js**: `20.x` or `22.x` (`node --version`) and `npm` (`npm --version`)
* **Docker**: Docker Engine or Docker Desktop (running PostgreSQL 16)
* **FFmpeg & FFprobe**: Version `7.x` or `9.x` (`ffmpeg -version` and `ffprobe -version`)
* **Secure HTTPS Tunnel**: `ngrok` or Cloudflare Tunnel (required for Meta OAuth callbacks during local testing)

### Installing FFmpeg
* **macOS (Homebrew)**:
  ```bash
  brew install ffmpeg
  ```
* **Linux (Ubuntu/Debian)**:
  ```bash
  sudo apt update && sudo apt install -y ffmpeg
  ```
* **Verify Installation**:
  ```bash
  ffmpeg -version | head -n 1
  ffprobe -version | head -n 1
  ```

---

## 2. Database Setup (PostgreSQL 16)

Adoraxe connects to a PostgreSQL database named `adoraxe_db`. If using an existing Docker PostgreSQL container (e.g., `shortly-postgres`):

```bash
# 1. Verify your PostgreSQL container is running
docker ps --filter "name=shortly-postgres"

# 2. Create the database and user if not already present
docker exec -i shortly-postgres psql -U shortly_user -d postgres << 'EOF'
CREATE USER adoraxe_user WITH PASSWORD 'adoraxe_pass_2026';
CREATE DATABASE adoraxe_db OWNER adoraxe_user;
GRANT ALL PRIVILEGES ON DATABASE adoraxe_db TO adoraxe_user;
EOF

# 3. Verify connection to adoraxe_db
docker exec shortly-postgres psql -U adoraxe_user -d adoraxe_db -c "\dt"
```

If starting a fresh standalone PostgreSQL container:
```bash
docker run -d \
  --name adoraxe-postgres \
  -e POSTGRES_USER=adoraxe_user \
  -e POSTGRES_PASSWORD=adoraxe_pass_2026 \
  -e POSTGRES_DB=adoraxe_db \
  -p 5432:5432 \
  postgres:16
```

---

## 3. Environment Configuration

1. Copy the example configuration template to `.env` in the repository root:
   ```bash
   cp .env.example .env
   ```
2. Generate a secure 32-character url-safe base64 **Fernet Encryption Key** for Instagram OAuth token protection:
   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. Generate a secure random **Secret Key**:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
4. Open `.env` and set:
   ```env
   SECRET_KEY=your_generated_secret_key
   TOKEN_ENCRYPTION_KEY=your_generated_fernet_key
   DATABASE_URL=postgresql+psycopg2://adoraxe_user:adoraxe_pass_2026@localhost:5432/adoraxe_db
   DEMO_MODE=true
   YOUTUBE_PROVIDER=http
   YTDLP_ENABLED=true
   ```

---

## 4. Backend Setup (FastAPI)

1. Navigate to the `backend/` directory:
   ```bash
   cd backend
   ```
2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install backend dependencies:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
4. Run database table initialization and startup verification:
   ```bash
   python -c "from app.db.database import init_db; init_db(); print('Database tables initialized.')"
   ```
5. Run the automated test suite (154 tests):
   ```bash
   pytest -q
   ```
6. Start the FastAPI development server:
   ```bash
   python run.py
   # Or directly via uvicorn:
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```
7. Verify the backend is responding:
   ```bash
   curl http://localhost:8000/api/v1/health
   # Expected response: {"status":"ok","version":"1.0.0","demo_mode":true,"database":true,"ffmpeg":true,...}
   ```

Interactive API documentation will be available at:
* Swagger UI: `http://localhost:8000/api/docs`
* ReDoc: `http://localhost:8000/api/redoc`

---

## 5. Frontend Setup (SvelteKit)

1. Navigate to the `frontend/` directory:
   ```bash
   cd frontend
   ```
2. Install npm dependencies:
   ```bash
   npm install
   ```
3. Run component and type checks:
   ```bash
   npx svelte-check --tsconfig ./tsconfig.json
   ```
4. Build the production application bundle:
   ```bash
   npm run build
   ```
5. Start the frontend development server:
   ```bash
   npm run dev
   ```
   Open `http://localhost:5173` in your browser.

---

## 6. Meta Developer & Instagram Unified Login Setup

To enable live Instagram publishing (`DEMO_MODE=false`), configure Meta Facebook Login for Business:

### 6.1 Create & Configure the Meta App
1. Log in to [Meta for Developers](https://developers.facebook.com/).
2. Create an App with the **Business** use case.
3. Under **Add Use Cases**, select **Facebook Login for Business**.
4. In the configuration settings:
   * **Login variation**: `General`
   * **Access token**: `System-user access token (SUAT)`
   * **Expiration**: `Never`
   * **Instagram account**: `Required`
   * **Ad account**: `Optional`
5. Note your **Configuration ID** (e.g. `4577605369153649`).
6. Request the required permissions:
   * `instagram_basic`
   * `instagram_content_publish`
   * `instagram_manage_messages`
   * `public_profile`

### 6.2 Set Up HTTPS Redirect URI
Meta requires an HTTPS redirect URI for OAuth callbacks:
1. Start an HTTPS tunnel pointing to your FastAPI port (8000):
   ```bash
   ngrok http 8000
   ```
2. Copy the resulting forwarding URL (e.g. `https://xxxx.ngrok-free.app`).
3. In Meta App Dashboard -> Facebook Login for Business -> Settings:
   * Add: `https://xxxx.ngrok-free.app/api/v1/instagram/callback` to **Valid OAuth Redirect URIs**.
4. Update your `.env`:
   ```env
   META_APP_ID=your_meta_app_id
   META_APP_SECRET=your_meta_app_secret
   META_CONFIG_ID=your_configuration_id
   META_REDIRECT_URI=https://xxxx.ngrok-free.app/api/v1/instagram/callback
   META_API_VERSION=v26.0
   ```
5. Navigate to `http://localhost:5173/instagram` and click **Connect Instagram Account** to authenticate.

---

## 7. Cloudinary Media Storage Setup

Meta's media container creation API requires video files to be hosted on a publicly accessible HTTPS CDN with byte-range request support.

1. Create a free account at [Cloudinary](https://cloudinary.com/).
2. From your Cloudinary Dashboard, copy:
   * **Cloud Name**
   * **API Key**
   * **API Secret**
3. Add them to your `.env` file:
   ```env
   CLOUDINARY_CLOUD_NAME=your_cloud_name
   CLOUDINARY_API_KEY=your_api_key
   CLOUDINARY_API_SECRET=your_api_secret
   ```
4. Verify by checking `GET http://localhost:8000/api/v1/health` in your browser. `cloudinary_configured` will report `true`.

---

## 8. Switching Between Demo and Live Publishing

| Feature | `DEMO_MODE=true` | `DEMO_MODE=false` |
| :--- | :--- | :--- |
| **YouTube Ingestion** | Returns verified sample channels (`[DEMO]`) | HTTP metadata scraping or yt-dlp |
| **FFmpeg 9:16 Transcoding** | Executes real local transcoding | Executes real local transcoding |
| **Cloudinary Staging** | Uploads & stages signed media | Uploads & stages signed media |
| **Instagram Connection** | Simulated demo identity | Live Creator/Business account via Meta Unified Login |
| **Reel Publishing** | Simulates job stages (`SIMULATED`), no live API calls | Live Meta Graph API `media_publish` |
| **Duplicate Protection** | Active (prevents repeated simulated posts) | Active (prevents duplicate live posts) |


---

## 9. Production Cloud Deployment: Vercel + Render + Neon

Adoraxe is optimized for zero-cost cloud deployment using Vercel (Frontend), Render (Docker Backend), and Neon (Serverless Hosted PostgreSQL).

### 9.1 Database Setup (Neon PostgreSQL)
1. Sign up for a free PostgreSQL database at [Neon.tech](https://neon.tech).
2. Create a database called `adoraxe_db`.
3. Copy the connection string. Neon formats it as:
   `postgresql://adoraxe_user:password@ep-xyz.us-east-2.aws.neon.tech/adoraxe_db?sslmode=require`
4. The Adoraxe backend automatically normalizes `postgresql://` and `postgres://` to `postgresql+psycopg2://`.

### 9.2 Running Production Migrations
Because Render Free does not offer interactive shell access or pre-deploy commands, Adoraxe executes migrations automatically:
* **Automatic on Boot (Built into Dockerfile)**:
  The `backend/Dockerfile` entrypoint executes `alembic upgrade head` on container boot before launching Uvicorn. Every deployment automatically applies pending migrations.
* **Manual Execution from Local Terminal (Alternative)**:
  You can execute migrations directly from your local terminal against your Neon database:
  ```bash
  cd backend
  DATABASE_URL="postgresql+psycopg2://user:password@ep-xyz.neon.tech/adoraxe_db?sslmode=require" ./venv/bin/alembic upgrade head
  ```

### 9.3 Backend Deployment (Render Docker Web Service)
1. In the [Render Dashboard](https://dashboard.render.com), click **New** -> **Web Service**.
2. Connect your repository.
3. Configure settings:
   * **Name**: `adoraxe-backend`
   * **Root Directory**: `backend`
   * **Runtime**: `Docker` (Render automatically uses `backend/Dockerfile`)
   * **Plan**: `Free`
   * **Health Check Path**: `/api/v1/health`
4. Add Environment Variables (from your production configuration):
   * `DEMO_MODE=true` (or `false` for live Instagram)
   * `AUTH_ENABLED=true`
   * `ADMIN_API_KEY=your_generated_random_admin_key_min_32_chars`
   * `FORWARDED_ALLOW_IPS=10.0.0.0/8,127.0.0.1`
   * `DATABASE_URL=postgresql+psycopg2://user:pass@ep-xyz.neon.tech/adoraxe_db?sslmode=require`
   * `FRONTEND_URL=https://your-app.vercel.app`
   * `ALLOWED_ORIGINS=https://your-app.vercel.app`
   * `SECRET_KEY=your_generated_random_secret_key`
   * `TOKEN_ENCRYPTION_KEY=your_generated_fernet_key`
   * `META_APP_ID=your_meta_app_id`
   * `META_APP_SECRET=your_meta_app_secret`
   * `META_CONFIG_ID=your_config_id`
   * `META_REDIRECT_URI=https://adoraxe-backend.onrender.com/api/v1/instagram/callback`
   * `META_API_VERSION=v26.0`
   * `CLOUDINARY_CLOUD_NAME=your_cloud_name`
   * `CLOUDINARY_API_KEY=your_api_key`
   * `CLOUDINARY_API_SECRET=your_api_secret`
   * `YOUTUBE_PROVIDER=http` (production HTTP metadata scraping)
   * `YTDLP_ENABLED=true` (enables direct creator video stream extraction)
5. Click **Deploy Web Service**.

### 9.4 Frontend Deployment (Vercel)
1. In the [Vercel Dashboard](https://vercel.com), click **Add New** -> **Project**.
2. Import your repository.
3. In Project Settings:
   * **Framework Preset**: `SvelteKit`
   * **Root Directory**: `frontend`
4. In **Environment Variables**:
   * Add: `VITE_BACKEND_URL=https://adoraxe-backend.onrender.com`
5. Click **Deploy**.

---

## 10. Render Free Tier Limitations & Operational Handling

| Limitation | Impact on Adoraxe | Operational Handling / Mitigation |
| :--- | :--- | :--- |
| **Inactivity Spin-Down** | Spins down after 15 min of zero HTTP traffic. Cold start takes ~50s. | Frontend polling while on `/queue` or `/history` sends periodic HTTP requests, keeping the backend awake during active use. |
| **No Shell Access** | Cannot run `alembic` commands via web terminal. | Migrations run automatically on container startup (`alembic upgrade head`) or via local CLI pointing to the hosted database URL. |
| **No Pre-Deploy Hook** | Pre-deploy hooks are paid-only. | Mitigated by container entrypoint migration execution. |
| **512 MB RAM Threshold** | High-bitrate 4K encoding could cause OOM. | Adoraxe limits Reel video sources to 1080p, 4.5 Mbps H.264, and under 60 seconds, which operates well within 250 MB RAM. |
| **No Background Workers** | Background worker services are paid (\$7/mo). | Adoraxe runs the publishing runner inside FastAPI using `BackgroundTasks` + PostgreSQL state persistence + startup recovery. |

---

## 11. Meta App Review & Access Modes

When connecting Instagram Creator or Business accounts:

### Development Mode (Current & Testing)
* **Access Scope**: Standard Access.
* **Eligible Users**: Only individuals registered with an **App Role** (Administrator, Developer, or Tester) inside your Meta Developer App.
* **Requirements**: No App Review required. Immediate live publishing works for developers and designated testers whose Instagram accounts are linked to a Facebook Page in your Business Portfolio.
* **Use Case**: Creator personal publishing, portfolio demonstrations, and private workflows.

### Live Mode (Public SaaS Users)
* **Access Scope**: Advanced Access.
* **Eligible Users**: Any public Instagram Creator or Business account worldwide.
* **Requirements**:
  1. Business Verification in Meta Business Manager.
  2. Formal **Meta App Review** submission with video screen recordings demonstrating the use of `instagram_basic` and `instagram_content_publish`.
  3. Hosted Privacy Policy and Terms of Service URLs.
* **Recommendation**: For project evaluation and demo purposes, operate in **Development Mode** with your test account registered under App Roles.
