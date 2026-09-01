"""Small Redis Streams consumer with explicit acknowledgement semantics."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.asyncio import Redis
from redis.exceptions import ResponseError


class QueueEnvelope(BaseModel):
    """Versioned envelope shared by all initial worker messages."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    message_id: UUID
    message_type: str = Field(min_length=1, max_length=100)
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class UnsupportedMessageError(ValueError):
    """Raised when no handler exists for a valid envelope."""


class QueueWorker:
    """Consume operational messages; scientific handlers are intentionally absent."""

    def __init__(
        self,
        redis: Redis,
        stream: str,
        consumer_group: str,
        consumer_name: str,
        ingestion_handler: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        self._redis = redis
        self._stream = stream
        self._consumer_group = consumer_group
        self._consumer_name = consumer_name
        self._ingestion_handler = ingestion_handler
        self._dead_letter_stream = f"{stream}:dead-letter"
        self._heartbeat_key = f"spectral:workers:{consumer_name}"
        self._stop = asyncio.Event()
        self._logger = structlog.get_logger(__name__)

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        await self._ensure_consumer_group()
        while not self._stop.is_set():
            await self._redis.set(
                self._heartbeat_key,
                datetime.now(UTC).isoformat(),
                ex=30,
            )
            messages = await self._redis.xreadgroup(
                groupname=self._consumer_group,
                consumername=self._consumer_name,
                streams={self._stream: ">"},
                count=10,
                block=1_000,
            )
            await self._consume(messages)

    async def _ensure_consumer_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                name=self._stream,
                groupname=self._consumer_group,
                id="0",
                mkstream=True,
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def _consume(self, streams: Any) -> None:
        for _, entries in streams:
            for entry_id, fields in entries:
                await self._consume_one(str(entry_id), fields)

    async def _consume_one(self, entry_id: str, fields: dict[str, Any]) -> None:
        raw_message = fields.get("data")
        try:
            if not isinstance(raw_message, str):
                raise ValueError("stream entry must contain a string data field")
            envelope = QueueEnvelope.model_validate_json(raw_message)
            await self.dispatch(envelope)
        except (ValidationError, ValueError) as error:
            await self._send_to_dead_letter(entry_id, type(error).__name__)
            self._logger.warning(
                "worker.message_rejected",
                entry_id=entry_id,
                error_code=type(error).__name__,
            )
        else:
            self._logger.info(
                "worker.message_succeeded",
                entry_id=entry_id,
                message_id=str(envelope.message_id),
                message_type=envelope.message_type,
            )
        await self._redis.xack(self._stream, self._consumer_group, entry_id)

    async def dispatch(self, envelope: QueueEnvelope) -> None:
        """Dispatch only non-scientific operational messages in this scaffold."""
        if envelope.message_type == "system.ping":
            return
        if envelope.message_type == "ingestion.validate.requested":
            if self._ingestion_handler is None:
                raise UnsupportedMessageError("ingestion handler is unavailable")
            raw_id = envelope.payload.get("upload_object_id")
            if not isinstance(raw_id, str):
                raise ValueError("upload_object_id must be a string UUID")
            await self._ingestion_handler(UUID(raw_id))
            return
        raise UnsupportedMessageError(envelope.message_type)

    async def _send_to_dead_letter(self, entry_id: str, error_code: str) -> None:
        await self._redis.xadd(
            self._dead_letter_stream,
            {
                "source_stream": self._stream,
                "source_entry_id": entry_id,
                "error_code": error_code,
                "failed_at": datetime.now(UTC).isoformat(),
            },
        )
