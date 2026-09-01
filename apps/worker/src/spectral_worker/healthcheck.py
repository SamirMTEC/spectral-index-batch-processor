"""Container health probe for worker dependencies."""

from redis import Redis
from spectral_common import get_settings


def main() -> None:
    """Fail unless the managed queue is reachable within a short deadline."""
    settings = get_settings()
    client = Redis.from_url(
        settings.redis_dsn,
        socket_connect_timeout=settings.readiness_timeout_seconds,
        socket_timeout=settings.readiness_timeout_seconds,
    )
    try:
        if client.ping() is not True:
            raise RuntimeError("Redis did not acknowledge the worker health probe")
    finally:
        client.close()


if __name__ == "__main__":
    main()
