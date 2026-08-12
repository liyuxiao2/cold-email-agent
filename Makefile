.PHONY: up down worker beat dashboard dev test discovery frontend deploy-gcp

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

# Run FastAPI dashboard / API server
dashboard:
	uv run uvicorn cold_email.api.main:app --reload --port 8000

# Run Next.js frontend
frontend:
	cd frontend && npm run dev

# Start everything (infra + worker + beat + backend dashboard)
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

