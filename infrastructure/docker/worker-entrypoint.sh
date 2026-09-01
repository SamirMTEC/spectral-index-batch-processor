#!/bin/sh
set -eu

case "${1:-ingestion}" in
  ingestion)
    exec python -m spectral_worker
    ;;
  celery)
    exec celery -A spectral_worker.celery_app:celery_app worker \
      --loglevel="${SPECTRAL_LOG_LEVEL:-INFO}" \
      --concurrency="${SPECTRAL_PROCESSING_CONCURRENCY:-2}" \
      --soft-time-limit="${SPECTRAL_PROCESSING_TASK_SOFT_LIMIT_SECONDS:-900}" \
      --time-limit="${SPECTRAL_PROCESSING_TASK_HARD_LIMIT_SECONDS:-930}" \
      --max-tasks-per-child="${SPECTRAL_PROCESSING_MAX_TASKS_PER_CHILD:-25}"
    ;;
  *)
    exec "$@"
    ;;
esac
