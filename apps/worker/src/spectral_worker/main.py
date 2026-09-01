"""Worker process entrypoint."""

import asyncio
import os
import signal
import socket
from contextlib import suppress

import structlog
from redis.asyncio import Redis
from spectral_common import get_settings
from spectral_common.logging import configure_logging

from spectral_worker.ingestion import IngestionHandler
from spectral_worker.queue import QueueWorker


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    redis_client = Redis.from_url(settings.redis_dsn, decode_responses=True)
    ingestion_handler = IngestionHandler(settings)
    consumer_name = f"{socket.gethostname()}-{os.getpid()}"
    worker = QueueWorker(
        redis=redis_client,
        stream=settings.redis_stream,
        consumer_group=settings.redis_consumer_group,
        consumer_name=consumer_name,
        ingestion_handler=ingestion_handler.handle,
    )

    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        # Windows uses KeyboardInterrupt when running outside containers.
        with suppress(NotImplementedError):
            loop.add_signal_handler(handled_signal, worker.request_stop)

    logger = structlog.get_logger(__name__)
    logger.info("worker.starting", consumer=consumer_name, stream=settings.redis_stream)
    try:
        await worker.run()
    finally:
        await redis_client.aclose()
        await ingestion_handler.close()
        logger.info("worker.stopped", consumer=consumer_name)


def main() -> None:
    """Run the async worker until SIGINT or SIGTERM."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
