from celery import Celery
from celery.schedules import crontab

from cold_email.config import settings

app = Celery("cold_email")

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
    }
}

app.autodiscover_tasks(["cold_email.workers"])
