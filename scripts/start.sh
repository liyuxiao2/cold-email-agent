#!/usr/bin/env bash
set -e

export PYTHONUNBUFFERED=1
export C_FORCE_ROOT=1

echo "=== Starting Celery Worker with Beat scheduler ==="
celery -A cold_email.celery_app worker --loglevel=info -B &

echo "=== Starting FastAPI Server on port ${PORT:-8000} ==="
exec uvicorn cold_email.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
