from celery import Celery
from celery.schedules import crontab

from cold_email.config import settings

app = Celery(
    "cold_email",
    include=[
        "cold_email.workers.discovery",
        "cold_email.workers.research",
        "cold_email.workers.drafting",
        "cold_email.workers.logistics",
    ],
)

app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Toronto",
    enable_utc=True,
)

app.conf.beat_schedule = {
    "discovery-every-monday": {
        "task": "cold_email.workers.discovery.discovery_task",
        "schedule": crontab(hour=8, minute=0, day_of_week="monday"),
    },
    # Drafting is now dispatched by POST /api/outreach when a user selects
    # companies. This hourly sweep only recovers rows whose dispatch was lost
    # (e.g. a Redis hiccup during that request) — a safety net, not the
    # primary path.
    "drafting-recovery-sweep": {
        "task": "cold_email.workers.drafting.drafting_recovery_task",
        "schedule": crontab(minute=0),
    },
}
