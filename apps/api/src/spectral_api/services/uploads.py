"""Upload planning and object-storage coordination."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from spectral_common import Settings

from spectral_api.domain.models import AssetRole, UploadObject, UploadObjectState

SCIENTIFIC_EXTENSIONS = {".tif", ".tiff"}
AUXILIARY_EXTENSIONS = {".png", ".jpg", ".jpeg"}
ARCHIVE_EXTENSIONS = {".zip"}
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_SAFE_FILENAME_CHARACTER = re.compile(r"[^\w.()\[\] -]", flags=re.UNICODE)


def normalize_relative_path(value: str, *, max_depth: int = 64) -> str:
    """Return a canonical user-visible path while rejecting traversal and controls."""
    if value.startswith(("/", "\\")) or _WINDOWS_DRIVE.match(value):
        raise ValueError("relative_path must not be absolute")
    normalized = unicodedata.normalize("NFC", value.replace("\\", "/"))
    segments = normalized.split("/")
    if (
        not normalized
        or len(segments) > max_depth
        or any(segment in {"", ".", ".."} for segment in segments)
    ):
        raise ValueError("relative_path must be a safe relative path")
    safe_segments: list[str] = []
    for segment in segments:
        if len(segment.encode("utf-8")) > 255:
            raise ValueError("relative_path contains an oversized path segment")
        if any(unicodedata.category(character).startswith("C") for character in segment):
            raise ValueError("relative_path cannot contain control characters")
        safe_segment = _SAFE_FILENAME_CHARACTER.sub("_", segment).strip(" .")
        if not safe_segment:
            raise ValueError("relative_path contains an empty sanitized segment")
        safe_segments.append(safe_segment)
    return "/".join(safe_segments)


def sanitize_filename(path: str, *, fallback: str = "upload") -> str:
    """Create a bounded display name; storage keys never depend on this value."""
    name = PurePosixPath(normalize_relative_path(path)).name
    cleaned = _SAFE_FILENAME_CHARACTER.sub("_", name).strip(" .")
    if not cleaned:
        cleaned = fallback
    suffix = PurePosixPath(cleaned).suffix[:32]
    stem_budget = max(1, 255 - len(suffix.encode("utf-8")))
    stem = (
        PurePosixPath(cleaned).stem.encode("utf-8")[:stem_budget].decode("utf-8", errors="ignore")
    )
    return f"{stem or fallback}{suffix}"


def classify_extension(path: str) -> tuple[str, AssetRole | None]:
    extension = PurePosixPath(path).suffix.lower()
    if extension in SCIENTIFIC_EXTENSIONS:
        return extension, AssetRole.SCIENTIFIC
    if extension in AUXILIARY_EXTENSIONS:
        return extension, AssetRole.AUXILIARY
    if extension in ARCHIVE_EXTENSIONS:
        return extension, AssetRole.ARCHIVE
    return extension, None


@dataclass(frozen=True, slots=True)
class UploadPlanData:
    method: str
    upload_url: str | None
    part_size: int | None
    part_count: int | None


class UploadStorage:
    """Use the internal S3 client for control and public client for signatures."""

    def __init__(self, s3: Any, presigner: Any, settings: Settings) -> None:
        self._s3 = s3
        self._presigner = presigner
        self._settings = settings

    async def initialize(self, upload_object: UploadObject) -> UploadPlanData:
        if upload_object.object_key is None:
            raise ValueError("upload object has no storage key")
        if upload_object.declared_size < self._settings.upload_multipart_threshold_bytes:
            url = await run_in_threadpool(
                self._presigner.generate_presigned_url,
                "put_object",
                Params={
                    "Bucket": self._settings.s3_bucket,
                    "Key": upload_object.object_key,
                    "ContentType": "application/octet-stream",
                    "ContentLength": upload_object.declared_size,
                },
                ExpiresIn=self._settings.upload_url_expiry_seconds,
            )
            return UploadPlanData("single", url, None, None)

        response = await run_in_threadpool(
            self._s3.create_multipart_upload,
            Bucket=self._settings.s3_bucket,
            Key=upload_object.object_key,
            ContentType="application/octet-stream",
            Metadata={"upload-object-id": str(upload_object.id)},
        )
        upload_object.multipart_upload_id = str(response["UploadId"])
        part_count = math.ceil(upload_object.declared_size / self._settings.upload_part_size_bytes)
        return UploadPlanData("multipart", None, self._settings.upload_part_size_bytes, part_count)

    async def presign_parts(
        self, upload_object: UploadObject, part_numbers: list[int]
    ) -> list[tuple[int, str]]:
        if upload_object.object_key is None or upload_object.multipart_upload_id is None:
            raise ValueError("multipart upload is not initialized")
        expected_count = math.ceil(
            upload_object.declared_size / self._settings.upload_part_size_bytes
        )
        if any(number > expected_count for number in part_numbers):
            raise ValueError("part number exceeds the declared multipart plan")
        results: list[tuple[int, str]] = []
        for part_number in part_numbers:
            url = await run_in_threadpool(
                self._presigner.generate_presigned_url,
                "upload_part",
                Params={
                    "Bucket": self._settings.s3_bucket,
                    "Key": upload_object.object_key,
                    "UploadId": upload_object.multipart_upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=self._settings.upload_url_expiry_seconds,
            )
            results.append((part_number, url))
        return results

    async def complete(
        self, upload_object: UploadObject, parts: list[tuple[int, str]]
    ) -> dict[str, Any]:
        if upload_object.object_key is None:
            raise ValueError("upload object has no storage key")
        if upload_object.multipart_upload_id is not None:
            expected_count = math.ceil(
                upload_object.declared_size / self._settings.upload_part_size_bytes
            )
            if [number for number, _ in parts] != list(range(1, expected_count + 1)):
                raise ValueError("multipart completion must contain every planned part once")
            await run_in_threadpool(
                self._s3.complete_multipart_upload,
                Bucket=self._settings.s3_bucket,
                Key=upload_object.object_key,
                UploadId=upload_object.multipart_upload_id,
                MultipartUpload={
                    "Parts": [{"PartNumber": number, "ETag": etag} for number, etag in parts]
                },
            )
        elif parts:
            raise ValueError("single-part upload completion cannot contain multipart parts")
        return await run_in_threadpool(
            self._s3.head_object,
            Bucket=self._settings.s3_bucket,
            Key=upload_object.object_key,
        )

    async def abort(self, upload_object: UploadObject) -> None:
        if upload_object.object_key is None or upload_object.multipart_upload_id is None:
            return
        await run_in_threadpool(
            self._s3.abort_multipart_upload,
            Bucket=self._settings.s3_bucket,
            Key=upload_object.object_key,
            UploadId=upload_object.multipart_upload_id,
        )


def quarantine_key(session_id: UUID, object_id: UUID, extension: str) -> str:
    return f"quarantine/{session_id}/{object_id}{extension}"


def mark_invalid(upload_object: UploadObject, code: str, message: str) -> None:
    upload_object.state = UploadObjectState.INVALID
    upload_object.failure_code = code
    upload_object.failure_message = message
