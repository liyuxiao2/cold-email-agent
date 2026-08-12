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
| `ANTHROPIC_API_KEY` | Anthropic Claude API Key | `sk-ant-...` |
| `FIRECRAWL_API_KEY` | Firecrawl Web Scraping API Key | `fc-...` |
| `INSTANTLY_API_KEY` | Instantly.io API Key (optional) | From Instantly Settings |
| `INSTANTLY_CAMPAIGN_ID` | Instantly Campaign UUID (optional) | From Instantly Campaigns |
| `GMAIL_CLIENT_ID` | Google OAuth2 Client ID (if using Gmail) | GCP Credentials |
| `GMAIL_CLIENT_SECRET` | Google OAuth2 Client Secret | GCP Credentials |
| `GMAIL_REFRESH_TOKEN` | Google OAuth2 Refresh Token | Gmail OAuth flow |
| `GMAIL_SENDER_EMAIL` | Sender email address | `you@company.com` |
| `SENDER_NAME` | Name in email signature | `Liyu Xiao` |
| `SENDER_ROLE` | Title in email signature | `Software Engineer` |
| `SENDER_COMPANY` | Company name in email signature | `Your Company` |
| `CORS_ORIGINS` | Allowed frontend domains | `["https://your-app.vercel.app", "http://localhost:3000"]` |

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
   ```bash
   gcloud run services update cold-email-backend \
       --region us-central1 \
       --set-env-vars DATABASE_URL="<YOUR_ASYNC_POSTGRES_URL>",CELERY_BROKER_URL="<YOUR_REDIS_URL>",ANTHROPIC_API_KEY="<KEY>",FIRECRAWL_API_KEY="<KEY>"
   ```

4. **Run Celery Worker on Cloud Run or Compute Engine**:
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
