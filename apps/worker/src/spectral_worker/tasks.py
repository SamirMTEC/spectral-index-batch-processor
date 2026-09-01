"""Celery tasks with bounded retry and safe item-level failures."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from billiard.exceptions import SoftTimeLimitExceeded
from spectral_common import get_settings

from spectral_worker.celery_app import celery_app
from spectral_worker.exporting import ExportGenerator
from spectral_worker.processing import (
    NON_RETRYABLE,
    ProcessingCancelled,
    ProcessingItemProcessor,
    formatted_traceback,
)


async def _process_item_async(self: Any, item_id: str) -> None:
    processor = ProcessingItemProcessor(get_settings())
    identifier = UUID(item_id)
    try:
        await processor.process(identifier, self.request.id)
    except ProcessingCancelled:
        await processor.mark_cancelled(identifier)
    except SoftTimeLimitExceeded as error:
        trace = formatted_traceback()
        should_retry = await processor.prepare_retry(identifier, error, trace)
        if should_retry:
            raise self.retry(
                exc=error, countdown=min(60, 2 ** (self.request.retries + 1))
            ) from error
        await processor.mark_failed(identifier, error, trace)
    except NON_RETRYABLE as error:
        await processor.mark_failed(identifier, error, formatted_traceback())
    except Exception as error:
        trace = formatted_traceback()
        should_retry = await processor.prepare_retry(identifier, error, trace)
        if should_retry:
            raise self.retry(
                exc=error, countdown=min(60, 2 ** (self.request.retries + 1))
            ) from error
        await processor.mark_failed(identifier, error, trace)
    finally:
        await processor.close()


def _process_item(self: Any, item_id: str) -> None:
    # Keep the engine, all asyncpg connections and disposal on the same event loop.
    asyncio.run(_process_item_async(self, item_id))


process_item: Any = celery_app.task(
    bind=True,
    name="spectral.processing.process_item",
    max_retries=4,
    acks_late=True,
    reject_on_worker_lost=True,
)(_process_item)


async def _generate_exports_async(artifact_ids: list[str]) -> None:
    generator = ExportGenerator(get_settings())
    try:
        await generator.generate([UUID(value) for value in artifact_ids])
    finally:
        await generator.close()


def _generate_exports(artifact_ids: list[str]) -> None:
    asyncio.run(_generate_exports_async(artifact_ids))


generate_exports: Any = celery_app.task(
    name="spectral.export.generate",
    acks_late=True,
    reject_on_worker_lost=True,
)(_generate_exports)
