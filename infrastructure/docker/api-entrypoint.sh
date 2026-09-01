#!/bin/sh
set -eu

case "${1:-serve}" in
  serve)
    exec uvicorn spectral_api.main:app \
      --host 0.0.0.0 \
      --port "${PORT:-8000}" \
      --workers "${SPECTRAL_API_WORKERS:-2}" \
      --timeout-keep-alive "${SPECTRAL_API_KEEPALIVE_SECONDS:-5}" \
      --timeout-graceful-shutdown "${SPECTRAL_GRACEFUL_SHUTDOWN_SECONDS:-45}" \
      --no-access-log
    ;;
  migrate)
    exec alembic -c /opt/spectral/api/alembic.ini upgrade head
    ;;
  migration-status)
    exec alembic -c /opt/spectral/api/alembic.ini current --check-heads
    ;;
  *)
    exec "$@"
    ;;
esac
