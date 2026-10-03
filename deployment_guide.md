# 🚀 Deployment Guide — AI Resume Analyzer & Job Recommendation System

> **Version:** 1.0.0 | **Last Updated:** June 2026 | **Targets:** Streamlit Cloud · Render · Docker

---

## Table of Contents

1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Environment Variables](#environment-variables)
3. [Streamlit Cloud Deployment](#streamlit-cloud-deployment)
4. [Render Deployment](#render-deployment)
5. [Docker Deployment](#docker-deployment)
6. [Database Setup & Migration](#database-setup--migration)
7. [Production Configuration](#production-configuration)
8. [Monitoring & Logging](#monitoring--logging)
9. [Bug Tracking Template](#bug-tracking-template)
10. [Deployment Checklist](#deployment-checklist)

---

## Pre-Deployment Checklist

Before deploying, verify the following:

- [ ] `GEMINI_API_KEY` obtained from [Google AI Studio](https://aistudio.google.com) (free tier available)
- [ ] All tests passing: `pytest tests/test_suite.py` → 98 passed, 0 failed
- [ ] `.env.example` reviewed and all secrets noted
- [ ] `requirements.txt` up to date
- [ ] `data/` directory created (or DB auto-creates on first run)
- [ ] LinkedIn cookies saved (optional) at `data/sessions/linkedin_cookies.pkl`
- [ ] No hardcoded secrets in source code (grep for passwords/keys)

---

## Environment Variables

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Google Gemini AI API key | `AIzaSy...` |
| `DATABASE_PATH` | SQLite DB file path | `data/database.db` |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LINKEDIN_EMAIL` | — | LinkedIn account email for Selenium tier |
| `LINKEDIN_PASSWORD` | — | LinkedIn account password |
| `SCRAPER_HEADLESS` | `true` | Run browser headless (`true`/`false`) |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `SESSION_TIMEOUT_HOURS` | `24` | User session timeout |
| `MAX_RESUME_SIZE_MB` | `10` | Maximum upload size |
| `SCRAPER_TIMEOUT` | `30` | Selenium page timeout seconds |

### `.env.example`

```env
# ── Required ───────────────────────────────────────────────
GEMINI_API_KEY=your_google_gemini_api_key_here

# ── Database ──────────────────────────────────────────────
DATABASE_PATH=data/database.db

# ── LinkedIn Scraping (optional) ──────────────────────────
LINKEDIN_EMAIL=your@email.com
LINKEDIN_PASSWORD=yourpassword
SCRAPER_HEADLESS=true
SCRAPER_TIMEOUT=30

# ── App Settings ──────────────────────────────────────────
LOG_LEVEL=INFO
SESSION_TIMEOUT_HOURS=24
MAX_RESUME_SIZE_MB=10
```

---

## Streamlit Cloud Deployment

### Step 1: Prepare GitHub Repository

```bash
# 1. Initialize git (if not already done)
git init
git add .
git commit -m "Initial commit — AI Resume Analyzer"

# 2. Create GitHub repo and push
git remote add origin https://github.com/YOUR_USERNAME/AI-Resume_Analyzer.git
git branch -M main
git push -u origin main
```

### Step 2: Configure `.gitignore`

Ensure these are excluded:

```
.env
data/database.db
data/sessions/
logs/
venv/
__pycache__/
*.pkl
*.pyc
.pytest_cache/
```

### Step 3: Deploy on Streamlit Cloud

1. Go to **[share.streamlit.io](https://share.streamlit.io)**
2. Click **"New app"**
3. Select your GitHub repository
4. Set **Main file path:** `app.py`
5. Set **Python version:** `3.11`
6. Click **"Advanced settings"** → Add Secrets:

```toml
# Streamlit Cloud Secrets (TOML format)
GEMINI_API_KEY = "AIzaSy..."
DATABASE_PATH = "data/database.db"
SCRAPER_HEADLESS = "true"
LOG_LEVEL = "INFO"
```

7. Click **"Deploy"**

### Step 4: Streamlit Cloud Notes

> **Important:** Streamlit Cloud uses ephemeral storage. The SQLite database resets on each redeployment. For persistence, consider:
> - Upgrading to [Streamlit Community Cloud](https://streamlit.io/cloud) with file persistence
> - Using an external DB (PostgreSQL on Supabase — free tier)

**LinkedIn Selenium** does not work on Streamlit Cloud (no Chrome binary). The system automatically falls back to:
- ✅ LinkedIn Guest API (works everywhere)
- ✅ Remotive API (works everywhere)
- ✅ Curated pool (always available)

### `packages.txt` (for Streamlit Cloud)

Create this file in your project root for system packages:

```
chromium-driver
chromium
```

---

## Render Deployment

Render supports persistent disks and background workers — better for production.

### Step 1: Create `render.yaml`

```yaml
services:
  - type: web
    name: ai-resume-analyzer
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
    envVars:
      - key: GEMINI_API_KEY
        sync: false
      - key: DATABASE_PATH
        value: /data/database.db
      - key: SCRAPER_HEADLESS
        value: "true"
      - key: LOG_LEVEL
        value: INFO
    disk:
      name: app-data
      mountPath: /data
      sizeGB: 1
```

### Step 2: Deploy on Render

1. Go to **[render.com](https://render.com)** → Sign up / Log in
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub repository
4. Configure:
   - **Name:** `ai-resume-analyzer`
   - **Environment:** `Python`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`
5. Add **Environment Variables** in the Render dashboard:
   - `GEMINI_API_KEY` → your key
   - `DATABASE_PATH` → `/data/database.db`
6. Add a **Disk** (for SQLite persistence):
   - Mount path: `/data`
   - Size: 1 GB
7. Click **"Create Web Service"**

### Step 3: Render Environment Notes

```bash
# Update DATABASE_PATH to use the persistent disk
DATABASE_PATH=/data/database.db

# Logs viewable in Render dashboard → Logs tab
# DB persists across deployments on the mounted disk
```

---

## Docker Deployment

For self-hosted or VPS deployment.

### `Dockerfile`

```dockerfile
FROM python:3.11-slim

# Install Chrome for Selenium (optional)
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories
RUN mkdir -p data/sessions logs

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false"]
```

### `docker-compose.yml`

```yaml
version: "3.9"

services:
  app:
    build: .
    ports:
      - "8501:8501"
    volumes:
      - app_data:/app/data
      - app_logs:/app/logs
    environment:
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - DATABASE_PATH=data/database.db
      - SCRAPER_HEADLESS=true
      - LOG_LEVEL=INFO
    restart: unless-stopped

volumes:
  app_data:
  app_logs:
```

### Run with Docker

```bash
# Build and start
docker compose up -d --build

# View logs
docker compose logs -f

# Stop
docker compose down

# Rebuild after code changes
docker compose up -d --build --force-recreate
```

---

## Database Setup & Migration

### Auto-Initialization

The database auto-creates on first run. No manual migration needed:

```python
# This runs automatically when the app starts:
from utils.database import init_database
init_database()  # Creates all tables if they don't exist
```

### Manual Initialization

```bash
python -c "from utils.database import init_database; init_database(); print('DB ready')"
```

### Schema Tables

| Table | Purpose |
|-------|---------|
| `users` | User accounts (UUID PK, email unique, bcrypt hash) |
| `resume_analysis` | AI analysis results per user |
| `scraped_jobs` | LinkedIn/Remotive job listings |
| `job_recommendations` | Legacy compatibility table |
| `job_search_prefs` | User search preferences |
| `saved_jobs` | Bookmarked jobs per user |
| `application_history` | Application status tracking |
| `search_history` | Search history per user |

### Database Backup

```bash
# Backup (run daily via cron)
cp data/database.db data/database_backup_$(date +%Y%m%d).db

# Restore
cp data/database_backup_20260604.db data/database.db
```

### SQLite to PostgreSQL Migration (if needed)

```bash
# Install pgloader
pip install pgloader

# Export from SQLite
sqlite3 data/database.db .dump > backup.sql

# Import to PostgreSQL (adjust connection string)
psql postgresql://user:pass@host:5432/dbname < backup.sql
```

---

## Production Configuration

### Streamlit Config (`config.toml`)

Create `.streamlit/config.toml`:

```toml
[server]
headless = true
enableCORS = false
enableXsrfProtection = true
maxUploadSize = 10

[theme]
base = "light"
primaryColor = "#0ea5e9"
backgroundColor = "#f0f9ff"
secondaryBackgroundColor = "#e0f2fe"
textColor = "#0f172a"

[client]
showErrorDetails = false
toolbarMode = "minimal"

[logger]
level = "info"
```

### Security Hardening

```python
# Already implemented in the codebase:
# ✅ bcrypt password hashing (12 rounds)
# ✅ UUID user IDs (no sequential IDs)
# ✅ Parameterized SQL queries (no injection)
# ✅ Input validation on all forms
# ✅ File type validation on upload
# ✅ Session state management
```

### Rate Limiting (Production Add-on)

```python
# Add to app.py for API rate limiting:
import time

if "last_analysis" not in st.session_state:
    st.session_state.last_analysis = 0

cooldown = 30  # seconds between AI analyses
elapsed = time.time() - st.session_state.last_analysis
if elapsed < cooldown:
    st.warning(f"Please wait {int(cooldown - elapsed)}s before another analysis.")
    st.stop()
```

---

## Monitoring & Logging

### Log Structure

All logs written to `logs/` directory:

```
logs/
├── app_YYYYMMDD.log          # Streamlit app logs
├── scraper_YYYYMMDD.log      # LinkedIn scraper logs
└── analyzer_YYYYMMDD.log     # AI analysis logs
```

### Log Format

```
2026-06-04 05:52:49 | INFO     | backend.job_scraper | search_jobs: 20 jobs found
2026-06-04 05:52:50 | WARNING  | backend.scraper     | load_session: no cookies found
2026-06-04 05:52:51 | ERROR    | backend.llm_analyzer| Gemini API rate limit hit
```

### Key Metrics to Monitor

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| App response time | < 2s | > 5s |
| AI analysis time | < 30s | > 60s |
| Job search time | < 15s | > 30s |
| DB query time | < 100ms | > 500ms |
| Error rate | < 1% | > 5% |

### Streamlit Cloud Monitoring

- **Dashboard:** share.streamlit.io → App → Logs
- **Metrics:** Streamlit Cloud shows memory/CPU usage
- **Alerts:** Set up via GitHub Actions on deployment

### Render Monitoring

- **Logs:** Render Dashboard → Service → Logs (real-time)
- **Metrics:** Render Dashboard → Service → Metrics
- **Health Check:** Configure at `/health` endpoint
- **Alerts:** Render sends email on service crash

### Health Check Endpoint

Add to `app.py`:

```python
# Simple health check via query param
if st.query_params.get("health") == "check":
    st.json({"status": "ok", "version": "1.0.0", "db": "connected"})
    st.stop()
```

Test: `curl https://your-app.streamlit.app?health=check`

---

## Bug Tracking Template

Use this template when reporting bugs:

```markdown
## Bug Report

**Title:** [Short description]
**Severity:** Critical / High / Medium / Low
**Reporter:** [Name]
**Date:** YYYY-MM-DD

### Environment
- OS: Windows 11 / macOS / Linux
- Python version: 3.11.x
- Streamlit version: 1.38.x
- Browser: Chrome 124 / Firefox 125

### Steps to Reproduce
1. Go to [page]
2. Click [button]
3. Upload [file type]
4. See error

### Expected Behavior
[What should happen]

### Actual Behavior
[What actually happens]

### Error Message / Stack Trace
```
ERROR | module | error message here
Traceback (most recent call last):
  File "...", line N, in function_name
    ...
```

### Screenshots
[Attach if applicable]

### Possible Fix
[Optional: suggested fix]

### Related Files
- `backend/module.py` line N
- `frontend/page.py` line N
```

---

## Deployment Checklist

### Pre-Deployment

- [ ] All 98 tests passing (`pytest tests/test_suite.py -q`)
- [ ] `requirements.txt` up to date
- [ ] `.env.example` reviewed, no secrets committed
- [ ] `.gitignore` includes: `.env`, `data/`, `logs/`, `venv/`, `*.pkl`
- [ ] `GEMINI_API_KEY` valid and tested
- [ ] Database auto-init tested locally
- [ ] LinkedIn Guest API tested (`search_jobs` returns jobs)
- [ ] Resume upload tested with PDF, DOCX, TXT
- [ ] Auth flow tested: register → login → upload → analyze → recommend

### Streamlit Cloud Specific

- [ ] `packages.txt` present (if using Selenium)
- [ ] Secrets configured in Streamlit Cloud dashboard
- [ ] App URL shared and tested from incognito browser
- [ ] Memory usage < 1GB (Streamlit free tier limit)

### Render Specific

- [ ] `render.yaml` present and validated
- [ ] Persistent disk mounted at `/data`
- [ ] `DATABASE_PATH=/data/database.db` set
- [ ] Health check configured
- [ ] Auto-deploy from `main` branch enabled

### Post-Deployment

- [ ] Register a test user — success
- [ ] Login with test user — success
- [ ] Upload a sample resume — parsed correctly
- [ ] AI analysis runs — returns skills/score
- [ ] Job search returns results — at least from curated tier
- [ ] Job detail modal opens — shows match analysis
- [ ] Save job action works — saved to DB
- [ ] Search preferences save/load — persists correctly
- [ ] Logs are clean — no unexpected errors
- [ ] Performance acceptable — page loads < 3s

---

## Troubleshooting

### Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| `GEMINI_API_KEY not found` | Missing env var | Add to `.env` or platform secrets |
| `no such table: users` | DB not initialized | Run `python -c "from utils.database import init_database; init_database()"` |
| `LinkedIn jobs not showing` | No cookies/auth | Uses Guest API automatically — check internet connection |
| `Resume parse failed` | Corrupted file | Try re-saving the PDF/DOCX and re-uploading |
| `App won't start on Render` | Port mismatch | Use `--server.port $PORT` in start command |
| `Memory error on Streamlit Cloud` | File too large | Reduce `MAX_RESUME_SIZE_MB` |
| `bcrypt error on import` | Missing C compiler | `pip install --upgrade bcrypt` |

### Reset Database

```bash
# Delete and recreate (WARNING: all data lost)
rm data/database.db
python -c "from utils.database import init_database; init_database()"
```

---

*Deployment Guide v1.0 — AI Resume Analyzer & Job Recommendation System*
