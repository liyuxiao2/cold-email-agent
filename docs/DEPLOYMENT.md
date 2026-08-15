# Deployment Guide: Google Cloud Platform & Vercel

This guide provides instructions for deploying the **Cold Email Agent** backend pipeline to **Google Cloud Platform (GCP)** and the interactive frontend dashboard to **Vercel**.

---

## 1. Architecture Summary

- **Frontend (Vercel)**: Next.js interactive web app for reviewing drafts, approving outreach, and triggering pipeline sweeps.
- **Backend API (Google Cloud Run)**: FastAPI REST API providing endpoints for lead discovery, review queues, and logistics.
- **Background Worker & Scheduler (GCP)**: Celery worker and Beat scheduler running automated scraping, enrichment, and drafting jobs.
- **Data Layer**: PostgreSQL (Cloud SQL / Supabase / Neon) + Redis (GCP Memorystore / Upstash).

---

## 2. Secrets & Environment Variables

### Backend Configuration (GCP)

| Variable | Description | Example / Source |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection URL with asyncpg | `postgresql+asyncpg://user:pass@host:5432/cold_email` |
| `CELERY_BROKER_URL` | Redis broker URL | `redis://host:6379/0` (or `rediss://...` for Upstash) |
| `CELERY_RESULT_BACKEND` | Redis result backend URL | `redis://host:6379/1` |
| `FIRECRAWL_API_KEY` | Firecrawl Web Scraping API Key | `fc-...` |
| `GEMINI_API_KEY` | Google Gemini API Key | `AI...` |
| `GROQ_API_KEY` | Groq API Key (llama models in fallback chain) | `gsk_...` |
| `HUNTER_API_KEY` | Hunter.io Email Finder Key | From Hunter dashboard |
| `MODEL_FALLBACK_CHAIN` | Optional JSON array overriding the LLM chain | `["llama-3.3-70b-versatile","gemini-3.5-flash-lite"]` |
| `GMAIL_CLIENT_ID` | Google OAuth2 Client ID — APP-level; Google requires it to refresh any user's token, so there is one pair for the whole deployment, not one per user | GCP Credentials |
| `GMAIL_CLIENT_SECRET` | Google OAuth2 Client Secret — APP-level, same reasoning as above | GCP Credentials |
| `SESSION_SECRET` | HS256 signing key for the session JWT | `secrets.token_urlsafe(48)` |
| `ENCRYPTION_KEY` | Fernet key encrypting per-user Gmail refresh tokens at rest | `Fernet.generate_key()` |
| `GOOGLE_REDIRECT_URI` | OAuth callback URL; must exactly match the Google Cloud Console entry | `https://<backend>/api/auth/google/callback` |
| `FRONTEND_URL` | Where the OAuth callback redirects back to after login | `https://your-app.vercel.app` |
| `ADMIN_EMAIL` | Google account seeded (or promoted) with `role='admin'` on every boot | `you@company.com` |
| `COOKIE_SECURE` | Set the session cookie's `Secure` flag; `true` in production, `false` only for local `http` dev | `true` |
| `CORS_ORIGINS` | Allowed frontend domains — **must be an explicit list, never `["*"]`**: browsers reject a wildcard origin combined with `allow_credentials=True`, so a wildcard would silently break cookie-based sessions | `["https://your-app.vercel.app", "http://localhost:3000"]` |

> Sender identity (name, intro, links, résumé, experience bullets) is per-user data in the `profiles` table (set through onboarding / `PUT /api/profile`), not config or code — see CLAUDE.md's Sender Identity section. There is no `GMAIL_REFRESH_TOKEN` / `GMAIL_SENDER_EMAIL` env var: each user's refresh token is captured during their own Google sign-in and stored encrypted on the `users` table, resolved per-request/per-sweep rather than read from settings.

> ⚠️ **Back up `ENCRYPTION_KEY` before any user signs in.** It is unrecoverable: losing or rotating it makes every stored Gmail refresh token undecryptable and forces every user to re-consent through Google Sign-In.

### Post-`create_all` DDL (résumé storage)

Production provisions its schema with `Base.metadata.create_all`
(`scripts/start.sh`), which cannot express every DDL SQLAlchemy's Column API
has no vocabulary for — column storage strategy is one. `profiles.resume_pdf`
needs `STORAGE EXTERNAL` (out-of-line, uncompressed) instead of Postgres's
default `EXTENDED` (compress then out-of-line): PDFs are already compressed,
so the default strategy burns CPU on every write for zero size gain.

| What | Where | When it runs |
|---|---|---|
| `ALTER TABLE profiles ALTER COLUMN resume_pdf SET STORAGE EXTERNAL` | `migrations/storage.sql` | Applied via `scripts/apply_storage.py`, called from `scripts/start.sh` on **every** container boot (idempotent — re-applying the same storage strategy is a no-op) |

If a later change needs another `create_all`-inexpressible column setting,
append its `.sql` file to `scripts/apply_storage.py`'s `SQL_FILES` tuple —
don't add a second script-plus-boot-hook mechanism for it.

**Résumé uploads are capped at 5MB** (`resume_store.MAX_RESUME_BYTES`), enforced
server-side (`validate_resume`) regardless of what the upload UI pre-checks.
This isn't just about request size: **Cloud SQL disk grows automatically but
never shrinks**, so an unbounded upload path would permanently inflate the
instance — and every backup taken of it — the first time someone uploads an
oversized file.

### Frontend Configuration (Vercel)

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_URL` | The public URL of your deployed Cloud Run backend (e.g. `https://cold-email-backend-xyz-uc.a.run.app`) |

---

## 3. Deploying the Backend to Google Cloud

### Method A: Google Cloud Run (Serverless)

1. **Authenticate and set your GCP project**:
   ```bash
   gcloud auth login
   gcloud config set project <YOUR_GCP_PROJECT_ID>
   ```

2. **Run the deployment script**:
   ```bash
   chmod +x scripts/deploy_gcp.sh
   ./scripts/deploy_gcp.sh
   ```

3. **Set backend environment secrets**:

   > ⚠️ **Use `--update-env-vars`, never `--set-env-vars`, on a running service.**
   > `--set-env-vars` *replaces the entire env-var set* with exactly what you
   > pass — every variable you omit (`DATABASE_URL`, `CELERY_BROKER_URL`, every
   > API key, ...) is deleted, which takes the service down. `--update-env-vars`
   > merges instead of replacing.

   ```bash
   gcloud run services update cold-email-backend \
       --region us-central1 \
       --update-env-vars DATABASE_URL="<YOUR_ASYNC_POSTGRES_URL>",CELERY_BROKER_URL="<YOUR_REDIS_URL>",GEMINI_API_KEY="<KEY>",GROQ_API_KEY="<KEY>",FIRECRAWL_API_KEY="<KEY>",HUNTER_API_KEY="<KEY>"
   ```

4. **Create the two auth secrets in Secret Manager** and wire them into Cloud Run:
   ```bash
   uv run python -c "import secrets; print(secrets.token_urlsafe(48))" | \
     gcloud secrets create session-secret --data-file=-
   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | \
     gcloud secrets create encryption-key --data-file=-

   gcloud run deploy cold-email-backend \
     --image=us-central1-docker.pkg.dev/<YOUR_GCP_PROJECT_ID>/cold-email-repo/cold-email-backend:latest \
     --region us-central1 \
     --update-secrets=SESSION_SECRET=session-secret:latest,ENCRYPTION_KEY=encryption-key:latest \
     --update-env-vars=GOOGLE_REDIRECT_URI=https://<backend>/api/auth/google/callback,FRONTEND_URL=https://your-app.vercel.app,ADMIN_EMAIL=you@company.com,COOKIE_SECURE=true,CORS_ORIGINS='["https://your-app.vercel.app"]'
   ```
   ⚠️ **Back up the `encryption-key` secret's value before any user signs in.**
   It cannot be recovered from Secret Manager metadata alone if the version is
   destroyed — losing or rotating it makes every stored Gmail refresh token
   undecryptable and forces every user to re-consent through Google Sign-In.

5. **Run Celery Worker on Cloud Run or Compute Engine**:
   To run the background worker container continuously:
   ```bash
   # On GCE VM with Docker or as a Cloud Run worker container:
   docker run -d --name cold-email-worker \
       --env-file .env \
       gcr.io/<YOUR_GCP_PROJECT_ID>/cold-email-agent:latest \
       celery -A cold_email.celery_app worker --loglevel=info
   ```

---

## 4. Deploying the Frontend to Vercel

1. Navigate to the `frontend/` directory:
   ```bash
   cd frontend
   ```

2. Deploy using Vercel CLI (or connect the repository via GitHub on [vercel.com](https://vercel.com)):
   ```bash
   npx vercel
   ```

3. Configure the environment variable in Vercel:
   - Go to your Project Settings in the Vercel Dashboard -> **Environment Variables**.
   - Add `NEXT_PUBLIC_API_URL` = `<YOUR_CLOUD_RUN_BACKEND_URL>`
   - Redeploy or run `npx vercel --prod`.

---

## 5. Local Testing

To test the entire stack locally:
- **Backend API & Workers**:
  ```bash
  make dev
  ```
- **Frontend Dashboard**:
  ```bash
  cd frontend && npm run dev
  ```
  Open [http://localhost:3000](http://localhost:3000) to interact with the dashboard.
