#!/usr/bin/env bash
set -e

export PYTHONUNBUFFERED=1
export C_FORCE_ROOT=1

# Ensure any new model tables exist (idempotent — create_all only creates
# missing tables, never alters/drops). Currently this provisions dead_letter
# (the DLQ) on first boot after the 004 migration; existing tables are skipped.
echo "=== Ensuring database tables exist ==="
python -c "from cold_email.database import Base, sync_engine; Base.metadata.create_all(sync_engine)"

echo "=== Starting Celery Worker with Beat scheduler ==="
celery -A cold_email.celery_app worker --loglevel=info -B &

echo "=== Starting FastAPI Server on port ${PORT:-8000} ==="
exec uvicorn cold_email.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
