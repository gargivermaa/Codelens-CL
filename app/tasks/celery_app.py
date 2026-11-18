from celery import Celery

from app.config import settings
from app.logging_config import configure_logging

celery_app = Celery(
    "codelens",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.review_task"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    # Fail fast when Redis is down so the webhook can answer 503 (Day 6).
    task_publish_retry=False,
    broker_connection_timeout=1,
    broker_transport_options={"socket_connect_timeout": 1, "socket_timeout": 1},
    # Reliability: a task is acknowledged only after it finishes, so a worker
    # crash re-delivers it instead of losing the review. Idempotency in the
    # review service makes re-delivery safe.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=1500,
    task_time_limit=1800,
)


from celery.signals import setup_logging  # noqa: E402


@setup_logging.connect
def _configure_worker_logging(**_):
    configure_logging()
