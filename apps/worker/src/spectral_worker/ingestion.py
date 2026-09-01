"""Per-object ingestion handler with isolated failures and immutable promotion."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

import boto3
import structlog
from botocore.config import Config
from spectral_common import Settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from spectral_worker.inspection import (
    AUXILIARY_EXTENSIONS,
    InvalidAssetError,
    build_preview_png,
    extract_zip_member,
    inspect_raster,
    open_validated_zip,
    sanitize_display_filename,
    sha256_file,
)


class IngestionHandler:
    """Validate one uploaded object without affecting sibling objects."""

    _DEFAULT_SEQUENCE = ("BLUE", "GREEN", "RED", "RED_EDGE", "NIR")

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_async_engine(settings.database_dsn, pool_pre_ping=True)
        options: dict[str, Any] = {
            "service_name": "s3",
            "region_name": settings.s3_region,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
            ),
        }
        if settings.s3_endpoint_url is not None:
            options["endpoint_url"] = settings.s3_endpoint_url
        if settings.s3_access_key_id is not None:
            options["aws_access_key_id"] = settings.s3_access_key_id
        if settings.s3_secret is not None:
            options["aws_secret_access_key"] = settings.s3_secret
        self._s3 = boto3.client(**options)
        self._logger = structlog.get_logger(__name__)

    async def close(self) -> None:
        await self._engine.dispose()
        close = getattr(self._s3, "close", None)
        if close is not None:
            close()

    async def handle(self, upload_object_id: UUID) -> None:
        item = await self._claim(upload_object_id)
        if item is None:
            return
        try:
            with TemporaryDirectory(prefix="spectral-ingest-") as directory:
                local_path = Path(directory) / f"source{item['extension']}"
                await asyncio.to_thread(
                    self._s3.download_file,
                    self._settings.s3_bucket,
                    item["object_key"],
                    str(local_path),
                )
                checksum = await asyncio.to_thread(sha256_file, local_path)
                if item["client_checksum"] and checksum != item["client_checksum"]:
                    raise InvalidAssetError(
                        "checksum_mismatch", "Uploaded bytes do not match the declared checksum"
                    )
                if item["extension"] == ".zip":
                    metadata = await self._process_zip(item, local_path, checksum, Path(directory))
                else:
                    metadata = await self._process_image(item, local_path, checksum)
                await self._mark_valid(item, checksum, metadata)
        except InvalidAssetError as error:
            await self._mark_invalid(item, error.code, str(error))
        except Exception as error:
            self._logger.exception(
                "ingestion.object_failed",
                upload_object_id=str(upload_object_id),
                error_type=type(error).__name__,
            )
            await self._mark_failed(item, "validation_failed", type(error).__name__)
        finally:
            await self._finish_session(item["upload_session_id"], item["dataset_id"])

    async def _claim(self, object_id: UUID) -> dict[str, Any] | None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, upload_session_id, project_id, owner_id, dataset_id,
                           filename, extension, declared_size, object_key,
                           object_version_id, client_checksum, role
                    FROM upload_objects
                    WHERE id = :id
                    FOR UPDATE
                    """
                ),
                {"id": object_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            state = await connection.scalar(
                text("SELECT state FROM upload_objects WHERE id = :id"), {"id": object_id}
            )
            if state not in {"uploaded", "validating"}:
                return None
            await connection.execute(
                text(
                    "UPDATE upload_objects SET state = 'validating', "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = :id"
                ),
                {"id": object_id},
            )
            return dict(row)

    async def _process_image(
        self, item: dict[str, Any], local_path: Path, checksum: str
    ) -> dict[str, object]:
        metadata = await asyncio.to_thread(
            inspect_raster,
            local_path,
            item["filename"],
            max_pixels=self._settings.raster_max_pixels,
            max_bands=self._settings.raster_max_bands,
            max_decoded_bytes=self._settings.raster_max_decoded_bytes,
        )
        await self._ensure_not_duplicate(item["dataset_id"], checksum)
        asset_id = uuid4()
        original_key = (
            f"originals/{item['project_id']}/{item['dataset_id']}/{asset_id}/"
            f"original{item['extension']}"
        )
        copy_source: dict[str, str] = {
            "Bucket": self._settings.s3_bucket,
            "Key": item["object_key"],
        }
        if item["object_version_id"]:
            copy_source["VersionId"] = item["object_version_id"]
        response = await asyncio.to_thread(
            self._s3.copy_object,
            Bucket=self._settings.s3_bucket,
            Key=original_key,
            CopySource=copy_source,
            Metadata={"sha256": checksum, "source-upload-object-id": str(item["id"])},
            MetadataDirective="REPLACE",
        )
        preview_key, preview_version_id, valid_fraction = await self._store_preview(
            item, asset_id, local_path
        )
        await self._insert_asset(
            item=item,
            asset_id=asset_id,
            original_key=original_key,
            version_id=response.get("VersionId"),
            checksum=checksum,
            metadata=metadata.to_dict(),
            role="auxiliary" if item["extension"] in AUXILIARY_EXTENSIONS else "scientific",
            upload_object_id=item["id"],
            size_bytes=item["declared_size"],
            preview_key=preview_key,
            preview_version_id=preview_version_id,
            valid_fraction=valid_fraction,
        )
        return metadata.to_dict()

    async def _process_zip(
        self, item: dict[str, Any], local_path: Path, checksum: str, workdir: Path
    ) -> dict[str, object]:
        archive, members = await asyncio.to_thread(
            open_validated_zip,
            local_path,
            max_entries=self._settings.zip_max_entries,
            max_uncompressed_bytes=self._settings.zip_max_uncompressed_bytes,
            max_entry_bytes=self._settings.zip_max_entry_bytes,
            max_compression_ratio=self._settings.zip_max_compression_ratio,
            max_path_depth=self._settings.zip_max_path_depth,
        )
        await self._ensure_dataset_capacity(
            item["dataset_id"],
            item["upload_session_id"],
            sum(member.file_size for member in members),
        )
        valid_count = 0
        invalid_count = 0
        try:
            for member in members:
                extension = Path(member.filename).suffix.lower()
                child_id = uuid4()
                display_name = sanitize_display_filename(member.filename)
                child_path = f"{item['id']}/{child_id}{extension}"
                role = "auxiliary" if extension in AUXILIARY_EXTENSIONS else "scientific"
                if extension not in {".tif", ".tiff", *AUXILIARY_EXTENSIONS}:
                    await self._insert_zip_child(
                        item,
                        child_id,
                        child_path,
                        display_name,
                        extension,
                        member.file_size,
                        role,
                        "invalid",
                        "invalid_extension",
                        "Unsupported ZIP entry extension",
                        None,
                    )
                    invalid_count += 1
                    continue
                await self._insert_zip_child(
                    item,
                    child_id,
                    child_path,
                    display_name,
                    extension,
                    member.file_size,
                    role,
                    "validating",
                    None,
                    None,
                    None,
                )
                extracted = workdir / f"entry-{child_id}{extension}"
                try:
                    await asyncio.to_thread(
                        extract_zip_member,
                        archive,
                        member,
                        extracted,
                        max_bytes=self._settings.zip_max_entry_bytes,
                    )
                    entry_checksum = await asyncio.to_thread(sha256_file, extracted)
                    metadata = await asyncio.to_thread(
                        inspect_raster,
                        extracted,
                        display_name,
                        max_pixels=self._settings.raster_max_pixels,
                        max_bands=self._settings.raster_max_bands,
                        max_decoded_bytes=self._settings.raster_max_decoded_bytes,
                    )
                    await self._ensure_not_duplicate(item["dataset_id"], entry_checksum)
                    asset_id = uuid4()
                    original_key = (
                        f"originals/{item['project_id']}/{item['dataset_id']}/{asset_id}/"
                        f"original{extension}"
                    )
                    await asyncio.to_thread(
                        self._s3.upload_file,
                        str(extracted),
                        self._settings.s3_bucket,
                        original_key,
                        ExtraArgs={
                            "Metadata": {
                                "sha256": entry_checksum,
                                "source-upload-object-id": str(item["id"]),
                            }
                        },
                    )
                    head = await asyncio.to_thread(
                        self._s3.head_object,
                        Bucket=self._settings.s3_bucket,
                        Key=original_key,
                    )
                    preview_key, preview_version_id, valid_fraction = await self._store_preview(
                        item, asset_id, extracted
                    )
                    child_item = {
                        **item,
                        "id": child_id,
                        "filename": display_name,
                        "extension": extension,
                    }
                    await self._insert_asset(
                        item=child_item,
                        asset_id=asset_id,
                        original_key=original_key,
                        version_id=head.get("VersionId"),
                        checksum=entry_checksum,
                        metadata=metadata.to_dict(),
                        role=role,
                        upload_object_id=child_id,
                        size_bytes=member.file_size,
                        preview_key=preview_key,
                        preview_version_id=preview_version_id,
                        valid_fraction=valid_fraction,
                    )
                    await self._update_zip_child(child_id, "valid", None, None, entry_checksum)
                    valid_count += 1
                except InvalidAssetError as error:
                    await self._update_zip_child(child_id, "invalid", error.code, str(error), None)
                    invalid_count += 1
                except Exception as error:
                    await self._update_zip_child(
                        child_id, "failed", "zip_entry_failed", type(error).__name__, None
                    )
                    self._logger.exception(
                        "ingestion.zip_entry_failed",
                        upload_object_id=str(item["id"]),
                        entry_id=str(child_id),
                    )
                    invalid_count += 1
        finally:
            archive.close()
        return {
            "filename": item["filename"],
            "extension": ".zip",
            "sha256": checksum,
            "entry_count": len(members),
            "valid_entries": valid_count,
            "invalid_entries": invalid_count,
        }

    async def _ensure_dataset_capacity(
        self, dataset_id: UUID, upload_session_id: UUID, additional_bytes: int
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text("SELECT id FROM datasets WHERE id = :dataset_id FOR UPDATE"),
                {"dataset_id": dataset_id},
            )
            accepted_bytes = int(
                await connection.scalar(
                    text(
                        "SELECT COALESCE(sum(size_bytes), 0) FROM band_assets "
                        "WHERE dataset_id = :dataset_id AND state = 'accepted'"
                    ),
                    {"dataset_id": dataset_id},
                )
                or 0
            )
            reserved_bytes = int(
                await connection.scalar(
                    text(
                        "SELECT COALESCE(sum(total_bytes), 0) FROM upload_sessions "
                        "WHERE dataset_id = :dataset_id AND id <> :session_id "
                        "AND state IN ('pending', 'uploading')"
                    ),
                    {"dataset_id": dataset_id, "session_id": upload_session_id},
                )
                or 0
            )
            if (
                accepted_bytes + reserved_bytes + additional_bytes
                > self._settings.upload_max_dataset_bytes
            ):
                raise InvalidAssetError(
                    "dataset_quota_exceeded", "ZIP contents exceed the dataset storage quota"
                )
            await connection.execute(
                text(
                    "UPDATE upload_sessions SET total_bytes = :total_bytes, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = :session_id"
                ),
                {"session_id": upload_session_id, "total_bytes": additional_bytes},
            )

    async def _ensure_not_duplicate(self, dataset_id: UUID, checksum: str) -> None:
        async with self._engine.connect() as connection:
            existing = await connection.scalar(
                text(
                    "SELECT id FROM band_assets WHERE dataset_id = :dataset_id "
                    "AND sha256 = :checksum AND state = 'accepted' LIMIT 1"
                ),
                {"dataset_id": dataset_id, "checksum": checksum},
            )
        if existing is not None:
            raise InvalidAssetError("duplicate", "An identical original already exists")

    async def _insert_asset(
        self,
        *,
        item: dict[str, Any],
        asset_id: UUID,
        original_key: str,
        version_id: str | None,
        checksum: str,
        metadata: dict[str, object],
        role: str,
        upload_object_id: UUID,
        size_bytes: int,
        preview_key: str,
        preview_version_id: str | None,
        valid_fraction: float,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO band_assets (
                        id, upload_object_id, project_id, owner_id, dataset_id, capture_id,
                        band_code, original_filename, object_key, object_version_id, sha256,
                        size_bytes, role, extension, width, height, dtype, band_count, nodata,
                        crs, transform, metadata_json, immutable, state, created_at, updated_at
                        , preview_object_key, preview_object_version_id,
                        approximate_valid_fraction
                    ) VALUES (
                        :id, :upload_object_id, :project_id, :owner_id, :dataset_id, NULL,
                        NULL, :filename, :object_key, :version_id, :sha256,
                        :size_bytes, :role, :extension, :width, :height, :dtype, :band_count,
                        :nodata, :crs, CAST(:transform AS jsonb), CAST(:metadata AS jsonb),
                        true, 'accepted', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        , :preview_key, :preview_version_id, :valid_fraction
                    )
                    """
                ),
                {
                    "id": asset_id,
                    "upload_object_id": upload_object_id,
                    "project_id": item["project_id"],
                    "owner_id": item["owner_id"],
                    "dataset_id": item["dataset_id"],
                    "filename": metadata["filename"],
                    "object_key": original_key,
                    "version_id": version_id,
                    "sha256": checksum,
                    "size_bytes": size_bytes,
                    "role": role,
                    "extension": metadata["extension"],
                    "width": metadata["width"],
                    "height": metadata["height"],
                    "dtype": metadata["dtype"],
                    "band_count": metadata["band_count"],
                    "nodata": metadata["nodata"],
                    "crs": metadata["crs"],
                    "transform": json.dumps(metadata["transform"]),
                    "metadata": json.dumps(metadata["metadata"]),
                    "preview_key": preview_key,
                    "preview_version_id": preview_version_id,
                    "valid_fraction": valid_fraction,
                },
            )

    async def _store_preview(
        self, item: dict[str, Any], asset_id: UUID, local_path: Path
    ) -> tuple[str, str | None, float]:
        preview_bytes, valid_fraction = await asyncio.to_thread(build_preview_png, local_path)
        preview_key = f"previews/{item['project_id']}/{item['dataset_id']}/{asset_id}.png"
        response = await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._settings.s3_bucket,
            Key=preview_key,
            Body=preview_bytes,
            ContentType="image/png",
            CacheControl="private, max-age=31536000, immutable",
            Metadata={"source-asset-id": str(asset_id)},
        )
        return preview_key, response.get("VersionId"), valid_fraction

    async def _insert_zip_child(
        self,
        item: dict[str, Any],
        child_id: UUID,
        path: str,
        filename: str,
        extension: str,
        size: int,
        role: str,
        state: str,
        code: str | None,
        message: str | None,
        checksum: str | None,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO upload_objects (
                        id, upload_session_id, project_id, owner_id, dataset_id,
                        parent_upload_object_id, client_relative_path, filename, extension,
                        declared_size, observed_size, role, state, verified_checksum,
                        failure_code, failure_message, metadata_json, upload_attempts,
                        created_at, updated_at
                    ) VALUES (
                        :id, :session_id, :project_id, :owner_id, :dataset_id,
                        :parent_id, :path, :filename, :extension, :size, :size, :role, :state,
                        :checksum, :code, :message, CAST('{}' AS jsonb), 0,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": child_id,
                    "session_id": item["upload_session_id"],
                    "project_id": item["project_id"],
                    "owner_id": item["owner_id"],
                    "dataset_id": item["dataset_id"],
                    "parent_id": item["id"],
                    "path": path,
                    "filename": filename,
                    "extension": extension,
                    "size": size,
                    "role": role,
                    "state": state,
                    "checksum": checksum,
                    "code": code,
                    "message": message,
                },
            )

    async def _update_zip_child(
        self,
        child_id: UUID,
        state: str,
        code: str | None,
        message: str | None,
        checksum: str | None,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE upload_objects SET state = :state, verified_checksum = :checksum,
                        failure_code = :code, failure_message = :message,
                        updated_at = CURRENT_TIMESTAMP WHERE id = :id
                    """
                ),
                {
                    "id": child_id,
                    "state": state,
                    "checksum": checksum,
                    "code": code,
                    "message": message,
                },
            )

    async def _mark_valid(
        self, item: dict[str, Any], checksum: str, metadata: dict[str, object]
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE upload_objects
                    SET state = 'valid', verified_checksum = :checksum,
                        metadata_json = CAST(:metadata AS jsonb), updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                    """
                ),
                {"id": item["id"], "checksum": checksum, "metadata": json.dumps(metadata)},
            )

    async def _mark_invalid(self, item: dict[str, Any], code: str, message: str) -> None:
        await self._mark_terminal(item, "invalid", code, message)

    async def _mark_failed(self, item: dict[str, Any], code: str, message: str) -> None:
        await self._mark_terminal(item, "failed", code, message)

    async def _mark_terminal(
        self, item: dict[str, Any], state: str, code: str, message: str
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE upload_objects SET state = :state, failure_code = :code,
                        failure_message = :message, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                    """
                ),
                {"id": item["id"], "state": state, "code": code, "message": message},
            )

    async def _finish_session(self, session_id: UUID, dataset_id: UUID) -> None:
        async with self._engine.begin() as connection:
            remaining = await connection.scalar(
                text(
                    "SELECT count(*) FROM upload_objects WHERE upload_session_id = :id "
                    "AND state IN ('pending', 'uploading', 'uploaded', 'validating')"
                ),
                {"id": session_id},
            )
            if remaining:
                return
        # A capture can only be associated after every object in this session
        # reached a terminal state. Associating while validation is still in
        # progress would create incomplete groups (for example, without NIR).
        await self._associate_dji_assets(dataset_id)
        async with self._engine.begin() as connection:
            accepted = await connection.scalar(
                text(
                    "SELECT count(*) FROM band_assets WHERE dataset_id = :id AND state = 'accepted'"
                ),
                {"id": dataset_id},
            )
            await connection.execute(
                text(
                    "UPDATE upload_sessions SET state = 'completed', "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = :id AND state <> 'cancelled'"
                ),
                {"id": session_id},
            )
            await connection.execute(
                text(
                    "UPDATE datasets SET state = :state, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = :id"
                ),
                {"id": dataset_id, "state": "needs_review" if accepted else "invalid"},
            )

    async def _associate_dji_assets(self, dataset_id: UUID) -> None:
        """Associate suffix-numbered assets using the dataset's saved band sequence."""
        async with self._engine.begin() as connection:
            profile = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT profile.filename_prefix, profile.capture_prefix, profile.band_order
                        FROM datasets AS dataset
                        LEFT JOIN band_sequence_profiles AS profile
                          ON profile.id = dataset.band_sequence_profile_id
                        WHERE dataset.id = :dataset_id
                        """
                        ),
                        {"dataset_id": dataset_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            filename_prefix = str(profile["filename_prefix"] or "DJI_") if profile else "DJI_"
            capture_prefix = str(profile["capture_prefix"] or "DJI_") if profile else "DJI_"
            band_order = (
                tuple(profile["band_order"] or self._DEFAULT_SEQUENCE)
                if profile
                else self._DEFAULT_SEQUENCE
            )
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT id, project_id, owner_id, dataset_id, original_filename,
                               capture_id, width, height, crs, transform
                        FROM band_assets
                        WHERE dataset_id = :dataset_id
                          AND state = 'accepted'
                          AND role = 'scientific'
                        ORDER BY original_filename
                        """
                    ),
                    {"dataset_id": dataset_id},
                )
            ).mappings()
            groups: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                stem = Path(row["original_filename"]).stem
                if not stem.casefold().startswith(filename_prefix.casefold()):
                    continue
                sequence = stem[len(filename_prefix) :]
                if len(sequence) < 2:
                    continue
                if not sequence.isdigit():
                    continue
                position = int(sequence[-1])
                if position < 1 or position > len(band_order):
                    continue
                band = band_order[position - 1]
                capture_number = sequence[:-1]
                asset = dict(row)
                asset["band_code"] = band
                groups.setdefault(capture_number, []).append(asset)

            for capture_number, assets in groups.items():
                band_codes = {asset["band_code"] for asset in assets}
                if len(band_codes) != len(assets):
                    continue
                if band_codes != set(band_order):
                    continue

                first = assets[0]
                profile_key = filename_prefix.strip().casefold().replace("_", "-") or "sequence"
                replicate_key = f"{profile_key}-{capture_number}"
                replicate_id = await connection.scalar(
                    text(
                        """
                        SELECT id FROM replicates
                        WHERE dataset_id = :dataset_id AND normalized_key = :key
                        LIMIT 1
                        """
                    ),
                    {"dataset_id": dataset_id, "key": replicate_key},
                )
                if replicate_id is None:
                    replicate_id = uuid4()
                    await connection.execute(
                        text(
                            """
                            INSERT INTO replicates (
                                id, project_id, owner_id, dataset_id, label,
                                normalized_key, ordinal, metadata_json,
                                created_at, updated_at
                            ) VALUES (
                                :id, :project_id, :owner_id, :dataset_id, :label,
                                :key, :ordinal, CAST(:metadata AS jsonb),
                                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                            )
                            """
                        ),
                        {
                            "id": replicate_id,
                            "project_id": first["project_id"],
                            "owner_id": first["owner_id"],
                            "dataset_id": first["dataset_id"],
                            "label": f"{capture_prefix.rstrip('_')} {capture_number}",
                            "key": replicate_key,
                            "ordinal": int(capture_number),
                            "metadata": json.dumps({"source": "band_sequence_profile"}),
                        },
                    )

                capture_key = f"{capture_prefix}{capture_number}"
                associated_capture_ids = {
                    asset["capture_id"] for asset in assets if asset["capture_id"] is not None
                }
                if len(associated_capture_ids) > 1:
                    continue
                capture_id = next(iter(associated_capture_ids), None)
                if capture_id is None:
                    capture_id = await connection.scalar(
                        text(
                            """
                            SELECT id FROM captures
                            WHERE dataset_id = :dataset_id AND capture_key = :capture_key
                            LIMIT 1
                            """
                        ),
                        {"dataset_id": dataset_id, "capture_key": capture_key},
                    )
                width = first["width"]
                height = first["height"]
                same_grid = all(
                    asset["width"] == width
                    and asset["height"] == height
                    and asset["crs"] == first["crs"]
                    and asset["transform"] == first["transform"]
                    for asset in assets
                )
                if capture_id is None:
                    capture_id = uuid4()
                    await connection.execute(
                        text(
                            """
                            INSERT INTO captures (
                                id, project_id, owner_id, dataset_id, replicate_id,
                                capture_key, width, height, crs, transform,
                                alignment_status, created_at, updated_at
                            ) VALUES (
                                :id, :project_id, :owner_id, :dataset_id, :replicate_id,
                                :capture_key, :width, :height, :crs, CAST(:transform AS json),
                                :alignment_status, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                            )
                            """
                        ),
                        {
                            "id": capture_id,
                            "project_id": first["project_id"],
                            "owner_id": first["owner_id"],
                            "dataset_id": first["dataset_id"],
                            "replicate_id": replicate_id,
                            "capture_key": capture_key,
                            "width": width,
                            "height": height,
                            "crs": first["crs"],
                            "transform": json.dumps(first["transform"]),
                            "alignment_status": "compatible" if same_grid else "incompatible",
                        },
                    )

                for asset in assets:
                    await connection.execute(
                        text(
                            """
                            UPDATE band_assets
                            SET capture_id = :capture_id,
                                band_code = :band_code,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :asset_id
                              AND (capture_id IS NULL OR capture_id = :capture_id)
                            """
                        ),
                        {
                            "capture_id": capture_id,
                            "band_code": asset["band_code"],
                            "asset_id": asset["id"],
                        },
                    )
