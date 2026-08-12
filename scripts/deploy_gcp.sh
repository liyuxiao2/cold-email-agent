#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Google Cloud Platform (GCP) Deployment Script for Cold Email Agent
# ==============================================================================
# Prerequisites:
#   1. gcloud CLI installed and authenticated (`gcloud auth login`)
#   2. Active GCP Project set (`gcloud config set project <PROJECT_ID>`)
#   3. Managed PostgreSQL (Cloud SQL / Supabase / Neon) & Redis (Memorystore / Upstash)
# ==============================================================================

REGION="${GCP_REGION:-us-central1}"
PROJECT_ID="$(gcloud config get-value project 2>/dev/null || echo '')"
IMAGE_TAG="gcr.io/${PROJECT_ID}/cold-email-agent:latest"

if [ -z "$PROJECT_ID" ]; then
    echo "Error: No active GCP project found. Run 'gcloud config set project <PROJECT_ID>'"
    exit 1
fi

echo "==> Deploying Cold Email Agent to GCP [Project: ${PROJECT_ID}, Region: ${REGION}]"

# 1. Enable required GCP services
echo "==> Enabling GCP APIs..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    containerregistry.googleapis.com \
    secretmanager.googleapis.com

# 2. Build and submit container image via Google Cloud Build
echo "==> Building container image via Cloud Build..."
gcloud builds submit --tag "${IMAGE_TAG}" .

# 3. Deploy FastAPI backend to Cloud Run
echo "==> Deploying FastAPI Service to Cloud Run..."
gcloud run deploy cold-email-backend \
    --image "${IMAGE_TAG}" \
    --platform managed \
    --region "${REGION}" \
    --allow-unauthenticated \
    --set-env-vars "PORT=8000"

BACKEND_URL="$(gcloud run services describe cold-email-backend --region "${REGION}" --format="value(status.url)")"

echo "================================================================="
echo " Backend deployed successfully to: ${BACKEND_URL}"
echo "================================================================="
echo ""
echo "Next steps:"
echo "1. Set your environment variables and secrets on Cloud Run:"
echo "   gcloud run services update cold-email-backend \\"
echo "     --region ${REGION} \\"
echo "     --set-env-vars DATABASE_URL='<POSTGRES_URL>',CELERY_BROKER_URL='<REDIS_URL>',ANTHROPIC_API_KEY='<KEY>',FIRECRAWL_API_KEY='<KEY>'"
echo ""
echo "2. Deploy frontend to Vercel:"
echo "   cd frontend && npx vercel --prod --env NEXT_PUBLIC_API_URL=${BACKEND_URL}"
echo "================================================================="
