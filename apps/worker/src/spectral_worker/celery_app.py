"""Celery application dedicated to scientific processing items."""

from celery import Celery
from spectral_common import get_settings
from spectral_common.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
celery_app = Celery(
    "spectral-processing",
    broker=settings.redis_dsn,
    backend=settings.redis_dsn,
    include=["spectral_worker.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    result_expires=86_400,
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_hijack_root_logger=False,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_soft_time_limit=settings.processing_task_soft_limit_seconds,
    task_time_limit=settings.processing_task_hard_limit_seconds,
    worker_max_tasks_per_child=settings.processing_max_tasks_per_child,
)
