"""Versioned queue envelope and dispatch tests."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from spectral_worker.queue import QueueEnvelope, QueueWorker, UnsupportedMessageError


def make_worker() -> QueueWorker:
    return QueueWorker(
        redis=cast(Redis, object()),
        stream="spectral:test",
        consumer_group="test-workers",
        consumer_name="test-worker",
    )


def test_queue_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        QueueEnvelope.model_validate(
            {
                "message_id": str(uuid4()),
                "message_type": "system.ping",
                "occurred_at": datetime.now(UTC),
                "payload": {},
                "unexpected": True,
            }
        )


@pytest.mark.asyncio
async def test_worker_accepts_operational_ping() -> None:
    envelope = QueueEnvelope(
        message_id=uuid4(),
        message_type="system.ping",
        occurred_at=datetime.now(UTC),
    )

    await make_worker().dispatch(envelope)


@pytest.mark.asyncio
async def test_worker_rejects_unimplemented_scientific_messages() -> None:
    envelope = QueueEnvelope(
        message_id=uuid4(),
        message_type="processing.index.requested",
        occurred_at=datetime.now(UTC),
    )

    with pytest.raises(UnsupportedMessageError):
        await make_worker().dispatch(envelope)


@pytest.mark.asyncio
async def test_worker_dispatches_ingestion_object_independently() -> None:
    received = []

    async def ingest(object_id: UUID) -> None:
        received.append(object_id)

    worker = QueueWorker(
        redis=cast(Redis, object()),
        stream="spectral:test",
        consumer_group="test-workers",
        consumer_name="test-worker",
        ingestion_handler=ingest,
    )
    object_id = uuid4()
    await worker.dispatch(
        QueueEnvelope(
            message_id=uuid4(),
            message_type="ingestion.validate.requested",
            occurred_at=datetime.now(UTC),
            payload={"upload_object_id": str(object_id)},
        )
    )
    assert received == [object_id]
