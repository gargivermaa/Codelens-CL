
from celery import Celery
from app.config import settings

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
    task_publish_retry=False,
    broker_connection_timeout=1,
    broker_transport_options={
    "socket_connect_timeout": 1,
    "socket_timeout": 1,
},
)
