#!/usr/bin/env bash
set -e

export PYTHONUNBUFFERED=1
export C_FORCE_ROOT=1

# Ensure any new model tables exist (idempotent — create_all only creates
# missing tables, never alters/drops). Currently this provisions dead_letter
# (the DLQ) on first boot after the 004 migration; existing tables are skipped.
echo "=== Ensuring database tables exist ==="
python -c "from cold_email.database import Base, sync_engine; Base.metadata.create_all(sync_engine)"

# create_all does not provision VIEWS (R23) — apply them separately, every
# boot, so pending_drafts/pending_sends/available_contacts exist for the
# drafting and logistics workers even on a create_all-only database.
echo "=== Applying database views ==="
python -m scripts.apply_views || echo "WARNING: view provisioning failed; continuing"

# create_all also can't express column storage strategy (R32) — profiles.resume_pdf
# would otherwise sit at the default EXTENDED strategy and Postgres would waste
# CPU trying to compress every already-compressed PDF write — AND create_all
# only ever CREATEs tables, never ALTERs an existing one (R43), so a migration
# that adds columns to `users` needs a real ALTER TABLE applied here too, or
# it's simply invisible on any database that already has that table.
echo "=== Applying storage DDL ==="
python -m scripts.apply_storage || echo "WARNING: storage DDL failed; continuing"

echo "Seeding admin user..."
python -m scripts.seed_admin || echo "WARNING: admin seed failed; continuing"

echo "=== Starting Celery Worker with Beat scheduler ==="
celery -A cold_email.celery_app worker --loglevel=info -B &

echo "=== Starting FastAPI Server on port ${PORT:-8000} ==="
exec uvicorn cold_email.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
