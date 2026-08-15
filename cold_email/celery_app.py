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
    # Governs BEAT's cron interpretation only. The due-send scanner
    # (send_due_task / reap_stuck_sends) compares timestamps with
    # datetime.now(timezone.utc) explicitly -- relying on this process
    # default for those comparisons is how a scheduler ends up five hours off
    # in production and correct on a laptop.
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
    # Scheduled sends. pending_sends treats a NULL scheduled_send_at as due, so
    # an approve with no schedule goes out on the next tick (<= 5 minutes).
    "send-due-sweep": {
        "task": "cold_email.workers.logistics.send_due_task",
        "schedule": crontab(minute="*/5"),
    },
    # Surface sends whose outcome is unknown. Hourly is enough -- these are
    # worker crashes, and they are reported rather than retried.
    "reap-stuck-sends": {
        "task": "cold_email.workers.logistics.reap_stuck_sends",
        "schedule": crontab(minute=30),
    },
}
