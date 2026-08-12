# Multi-stage Dockerfile for Cold Email Agent (FastAPI / Celery Worker / Celery Beat)
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency definitions.
# NOTE: uv.lock is intentionally NOT copied — it is generated on a network that
# pins packages to a private, authenticated mirror unreachable from CI. We
# resolve fresh from the default public index (pypi.org) inside the clean build
# container instead. See .gcloudignore.
COPY pyproject.toml ./

# Install dependencies into virtualenv (resolves + locks against pypi.org)
RUN uv sync --no-install-project --no-dev


# ----------------- Production Runner Image -----------------
FROM python:3.12-slim AS runner

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Install runtime libpq if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed venv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy source code, migrations, and scripts
COPY cold_email/ /app/cold_email/
COPY migrations/ /app/migrations/
COPY scripts/ /app/scripts/
COPY pyproject.toml /app/

RUN chmod +x /app/scripts/start.sh

# Expose default port (Cloud Run sets $PORT dynamically)
EXPOSE 8000

# Default command: start Celery worker in background and FastAPI server in foreground
CMD ["/bin/bash", "/app/scripts/start.sh"]

