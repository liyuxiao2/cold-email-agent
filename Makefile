.PHONY: up down worker beat dashboard dev test discovery

# Start Redis + Postgres
up:
	docker compose up -d

# Stop everything
down:
	docker compose down

# Run Celery worker
worker:
	uv run celery -A cold_email.celery_app worker --loglevel=info

# Run Celery Beat scheduler
beat:
	uv run celery -A cold_email.celery_app beat --loglevel=info

# Run FastAPI dashboard
dashboard:
	uv run uvicorn cold_email.api.main:app --reload --port 8000

# Start everything (infra + worker + beat + dashboard)
dev:
	docker compose up -d
	@echo "Starting worker, beat, and dashboard..."
	uv run celery -A cold_email.celery_app worker --loglevel=info & \
	uv run celery -A cold_email.celery_app beat --loglevel=info & \
	uv run uvicorn cold_email.api.main:app --reload --port 8000

# Trigger discovery manually
discovery:
	uv run python -c "from cold_email.workers.discovery import discovery_task; discovery_task.delay()"

# Run tests
test:
	uv run pytest
