"""One-capture/one-index raster processing with isolated persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

import boto3
import numpy as np
import rasterio
import structlog
from affine import Affine
from botocore.config import Config
from rasterio.features import geometry_mask
from spectral_common import CURRENT_FORMULA_VERSIONS, FORMULA_DEFINITIONS, Settings
from spectral_engine import (
    BandData,
    IncompatibleDimensionsError,
    InvalidParameterError,
    MissingBandError,
    SpatialMetadataMismatchError,
    calculate_index,
    calculate_statistics,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

REQUIRED_BANDS = {
    name: definition.required_bands
    for (name, version), definition in FORMULA_DEFINITIONS.items()
    if CURRENT_FORMULA_VERSIONS.get(name) == version
}
NON_RETRYABLE = (
    MissingBandError,
    IncompatibleDimensionsError,
    InvalidParameterError,
    SpatialMetadataMismatchError,
    ValueError,
)


class ProcessingCancelled(Exception):
    """The job was cancelled before the item could be committed."""


class ProcessingItemProcessor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: AsyncEngine = create_async_engine(settings.database_dsn, pool_pre_ping=True)
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
        self.s3 = boto3.client(**options)
        self.logger = structlog.get_logger(__name__)
        self.current_file: str | None = None

    async def close(self) -> None:
        await self.engine.dispose()
        close = getattr(self.s3, "close", None)
        if close is not None:
            close()

    async def process(self, item_id: UUID, task_id: str | None) -> None:
        item = await self._claim(item_id, task_id)
        if item is None:
            return
        with TemporaryDirectory(prefix=f"spectral-{item_id}-") as directory:
            workdir = Path(directory)
            assets = await self._assets(item)
            paths = await self._download_bands(assets, workdir)
            output = workdir / f"{item['index_name'].lower()}.tif"
            statistics, metadata = await asyncio.to_thread(
                self._calculate_windowed,
                item,
                assets,
                paths,
                output,
                workdir,
            )
            if await self._is_cancelled(item["job_id"]):
                raise ProcessingCancelled
            checksum = await asyncio.to_thread(self._sha256, output)
            object_key = (
                f"results/{item['project_id']}/{item['dataset_id']}/{item['job_id']}/"
                f"{item['capture_id']}/{item['analysis_scope']}/"
                f"{item['index_name'].lower()}-{item_id}.tif"
            )
            await asyncio.to_thread(
                self.s3.upload_file,
                str(output),
                self.settings.s3_bucket,
                object_key,
                ExtraArgs={
                    "ContentType": "image/tiff",
                    "Metadata": {
                        "processing-item-id": str(item_id),
                        "sha256": checksum,
                    },
                },
            )
            head = await asyncio.to_thread(
                self.s3.head_object,
                Bucket=self.settings.s3_bucket,
                Key=object_key,
            )
            if await self._is_cancelled(item["job_id"]):
                await asyncio.to_thread(
                    self.s3.delete_object,
                    Bucket=self.settings.s3_bucket,
                    Key=object_key,
                )
                raise ProcessingCancelled
            await self._persist_success(
                item,
                object_key,
                head.get("VersionId"),
                int(head.get("ContentLength", output.stat().st_size)),
                checksum,
                statistics,
                metadata,
            )

    async def _claim(self, item_id: UUID, task_id: str | None) -> dict[str, Any] | None:
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT item.id, item.job_id, item.project_id, item.owner_id,
                                   item.dataset_id, item.capture_id, item.roi_version_id,
                                   item.index_name, item.analysis_scope, item.formula_version,
                                   item.formula_expression, item.parameters_snapshot,
                                   item.input_snapshot, item.band_mapping_snapshot,
                                   item.roi_snapshot, item.attempts,
                                   item.max_attempts, capture.capture_key,
                                   version.pixel_coordinates AS legacy_pixel_coordinates,
                                   job.software_version
                            FROM processing_items AS item
                            JOIN processing_jobs AS job ON job.id = item.job_id
                            JOIN captures AS capture ON capture.id = item.capture_id
                            LEFT JOIN roi_versions AS version ON version.id = item.roi_version_id
                            WHERE item.id = :item_id
                            FOR UPDATE OF item, job
                            """
                        ),
                        {"item_id": item_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            job_state = await connection.scalar(
                text("SELECT state FROM processing_jobs WHERE id = :id"), {"id": row["job_id"]}
            )
            item_state = await connection.scalar(
                text("SELECT state FROM processing_items WHERE id = :id"), {"id": item_id}
            )
            if job_state == "cancelled":
                await self._terminal_update(
                    connection,
                    item_id,
                    "skipped",
                    "job_cancelled",
                    "Processamento cancelado pelo usuário",
                    row["capture_key"],
                    None,
                )
                await self._finalize_job(connection, row["job_id"])
                return None
            if item_state != "pending":
                return None
            await connection.execute(
                text(
                    """
                    UPDATE processing_items
                    SET state = 'running', attempts = attempts + 1,
                        celery_task_id = COALESCE(:task_id, celery_task_id),
                        started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                        error_code = NULL, error_message = NULL, internal_traceback = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :item_id
                    """
                ),
                {"item_id": item_id, "task_id": task_id},
            )
            await connection.execute(
                text(
                    """
                    UPDATE processing_jobs
                    SET state = 'running', started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :job_id AND state = 'queued'
                    """
                ),
                {"job_id": row["job_id"]},
            )
            claimed = dict(row)
            claimed["attempts"] = int(row["attempts"]) + 1
            roi_snapshot = claimed.get("roi_snapshot") or {}
            claimed["pixel_coordinates"] = roi_snapshot.get("pixel_coordinates") or claimed.get(
                "legacy_pixel_coordinates"
            )
            if claimed["analysis_scope"] == "roi" and claimed["pixel_coordinates"] is None:
                raise ValueError("Processing item has no immutable ROI version")
            return claimed

    async def _assets(self, item: dict[str, Any]) -> dict[str, dict[str, Any]]:
        required = REQUIRED_BANDS.get(str(item["index_name"]).upper())
        if required is None:
            raise InvalidParameterError(f"Unsupported index: {item['index_name']}")
        frozen_inputs = item.get("input_snapshot") or []
        if str(item.get("formula_version") or "legacy") != "legacy":
            assets: dict[str, dict[str, Any]] = {}
            for frozen in frozen_inputs:
                code = str(frozen.get("band") or "").upper()
                if code not in required:
                    continue
                if code in assets:
                    raise MissingBandError(f"Duplicate frozen association for band {code}")
                assets[code] = {
                    "band_code": code,
                    "object_key": frozen.get("object_key"),
                    "object_version_id": frozen.get("object_version_id"),
                    "original_filename": frozen.get("filename"),
                    "sha256": frozen.get("checksum_sha256"),
                    "nodata": frozen.get("nodata"),
                    "width": frozen.get("width"),
                    "height": frozen.get("height"),
                    "crs": frozen.get("crs"),
                    "transform": frozen.get("transform"),
                }
            missing = [code for code in required if code not in assets]
            if missing:
                raise MissingBandError(f"Missing required frozen band(s): {', '.join(missing)}")
            return assets
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT band_code, object_key, object_version_id, original_filename,
                               nodata, width, height, crs, transform
                        FROM band_assets
                        WHERE capture_id = :capture_id AND state = 'accepted'
                          AND role = 'scientific' AND band_code = ANY(:bands)
                        """
                    ),
                    {"capture_id": item["capture_id"], "bands": list(required)},
                )
            ).mappings()
        legacy_assets: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row["band_code"]).upper()
            if code in legacy_assets:
                raise MissingBandError(f"Duplicate association for band {code}")
            legacy_assets[code] = dict(row)
        missing = [code for code in required if code not in legacy_assets]
        if missing:
            raise MissingBandError(f"Missing required band(s): {', '.join(missing)}")
        return legacy_assets

    async def _download_bands(
        self, assets: dict[str, dict[str, Any]], workdir: Path
    ) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        for code, asset in assets.items():
            self.current_file = str(asset["original_filename"])
            path = workdir / f"{code.lower()}.tif"
            extra = (
                {"VersionId": asset["object_version_id"]} if asset["object_version_id"] else None
            )
            await asyncio.to_thread(
                self.s3.download_file,
                self.settings.s3_bucket,
                asset["object_key"],
                str(path),
                ExtraArgs=extra,
            )
            expected_checksum = asset.get("sha256")
            if expected_checksum:
                observed_checksum = await asyncio.to_thread(self._sha256, path)
                if observed_checksum != expected_checksum:
                    raise ValueError(
                        f"Checksum mismatch for immutable input {asset['original_filename']}"
                    )
            paths[code] = path
        self.current_file = ", ".join(str(asset["original_filename"]) for asset in assets.values())
        return paths

    def _calculate_windowed(
        self,
        item: dict[str, Any],
        assets: dict[str, dict[str, Any]],
        paths: dict[str, Path],
        output_path: Path,
        workdir: Path,
    ) -> tuple[Any, dict[str, Any]]:
        index_name = str(item["index_name"]).upper()
        required = REQUIRED_BANDS[index_name]
        with ExitStack() as stack:
            rasters = {code: stack.enter_context(rasterio.open(paths[code])) for code in required}
            reference = rasters[required[0]]
            width, height = reference.width, reference.height
            for code, raster in rasters.items():
                if (raster.width, raster.height) != (width, height):
                    raise IncompatibleDimensionsError(f"Band {code} has incompatible dimensions")
                if raster.crs != reference.crs or not raster.transform.almost_equals(
                    reference.transform
                ):
                    raise SpatialMetadataMismatchError(f"Band {code} uses a different raster grid")
            profile = reference.profile.copy()
            profile.update(
                driver="GTiff",
                count=1,
                dtype="float32",
                nodata=np.nan,
                compress="deflate",
                predictor=3,
            )
            profile.pop("photometric", None)
            if not profile.get("tiled", False):
                # Strip-based inputs may expose BLOCKXSIZE/BLOCKYSIZE in their profile,
                # but GDAL only accepts those creation options for tiled outputs.
                profile.pop("blockxsize", None)
                profile.pop("blockysize", None)
            values_path = workdir / "valid-values.float32"
            values = np.memmap(values_path, dtype=np.float32, mode="w+", shape=(width * height,))
            offset = 0
            polygon = (
                {
                    "type": "Polygon",
                    "coordinates": [item["pixel_coordinates"]],
                }
                if item.get("analysis_scope", "roi") == "roi"
                else None
            )
            with (
                rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True),
                rasterio.open(output_path, "w", **profile) as destination,
            ):
                for _, window in reference.block_windows(1):
                    band_data = {
                        code: BandData(
                            rasters[code].read(1, window=window, masked=True),
                            nodata=rasters[code].nodata,
                            crs=rasters[code].crs,
                            transform=rasters[code].window_transform(window),
                        )
                        for code in required
                    }
                    roi = (
                        geometry_mask(
                            [polygon],
                            out_shape=(int(window.height), int(window.width)),
                            transform=Affine.translation(window.col_off, window.row_off),
                            invert=True,
                        )
                        if polygon is not None
                        else None
                    )
                    result = calculate_index(
                        index_name,
                        band_data,
                        roi=roi,
                        parameters=item["parameters_snapshot"],
                        formula_version=(
                            str(item["formula_version"]) if item.get("formula_version") else None
                        ),
                    )
                    destination.write(result.values, 1, window=window)
                    destination.write_mask(result.valid_mask.astype(np.uint8) * 255, window=window)
                    selected = result.values[result.valid_mask]
                    values[offset : offset + selected.size] = selected
                    offset += selected.size
                valid_values = np.asarray(values[:offset], dtype=np.float32)
                statistics = calculate_statistics(
                    valid_values, np.ones(valid_values.shape, dtype=np.bool_)
                )
                destination.update_tags(
                    SPECTRAL_INDEX=index_name,
                    SPECTRAL_FORMULA_VERSION=str(item.get("formula_version") or "1.0.0"),
                    SPECTRAL_FORMULA=str(item.get("formula_expression") or ""),
                    SPECTRAL_SOFTWARE_VERSION=str(item.get("software_version") or "0.1.0"),
                    SPECTRAL_PARAMETERS=json.dumps(item["parameters_snapshot"], sort_keys=True),
                    SPECTRAL_PROCESSING_ITEM=str(item["id"]),
                    SPECTRAL_ANALYSIS_SCOPE=str(item.get("analysis_scope") or "roi"),
                )
            values.flush()
            del values
            return statistics, {
                "width": width,
                "height": height,
                "crs": reference.crs.to_string() if reference.crs else None,
                "transform": list(reference.transform)[:6],
                "valid_pixel_count": offset,
                "invalid_pixel_count": width * height - offset,
            }

    async def _persist_success(
        self,
        item: dict[str, Any],
        object_key: str,
        version_id: str | None,
        size_bytes: int,
        checksum: str,
        statistics: Any,
        metadata: dict[str, Any],
    ) -> None:
        async with self.engine.begin() as connection:
            job_state = await connection.scalar(
                text("SELECT state FROM processing_jobs WHERE id = :id FOR UPDATE"),
                {"id": item["job_id"]},
            )
            if job_state == "cancelled":
                raise ProcessingCancelled
            result_id = uuid4()
            await connection.execute(
                text(
                    """
                    INSERT INTO index_results (
                        id, project_id, owner_id, dataset_id, processing_item_id,
                        object_key, object_version_id, index_name, dtype, nodata,
                        analysis_scope, formula_version, software_version, width, height, crs,
                        transform, valid_pixel_count,
                        invalid_pixel_count, created_at, updated_at
                    ) VALUES (
                        :id, :project_id, :owner_id, :dataset_id, :item_id,
                        :object_key, :version_id, :index_name, 'float32', 'nan',
                        :analysis_scope, :formula_version, :software_version, :width, :height, :crs,
                        CAST(:transform AS jsonb), :valid_count,
                        :invalid_count, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    ) ON CONFLICT (processing_item_id) DO NOTHING
                    """
                ),
                {
                    "id": result_id,
                    "project_id": item["project_id"],
                    "owner_id": item["owner_id"],
                    "dataset_id": item["dataset_id"],
                    "item_id": item["id"],
                    "object_key": object_key,
                    "version_id": version_id,
                    "index_name": item["index_name"],
                    "analysis_scope": item["analysis_scope"],
                    "formula_version": item["formula_version"],
                    "software_version": item["software_version"],
                    "width": metadata["width"],
                    "height": metadata["height"],
                    "crs": metadata["crs"],
                    "transform": json.dumps(metadata["transform"]),
                    "valid_count": metadata["valid_pixel_count"],
                    "invalid_count": metadata["invalid_pixel_count"],
                },
            )
            stored_result = await connection.scalar(
                text("SELECT id FROM index_results WHERE processing_item_id = :id"),
                {"id": item["id"]},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO statistics (
                        id, project_id, owner_id, dataset_id, index_result_id,
                        count, mean, median, min, max, std, p05, p25, p75, p95,
                        created_at, updated_at
                    ) VALUES (
                        :id, :project_id, :owner_id, :dataset_id, :result_id,
                        :count, :mean, :median, :min, :max, :std, :p05, :p25, :p75, :p95,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    ) ON CONFLICT (index_result_id) DO NOTHING
                    """
                ),
                {
                    "id": uuid4(),
                    "project_id": item["project_id"],
                    "owner_id": item["owner_id"],
                    "dataset_id": item["dataset_id"],
                    "result_id": stored_result,
                    "count": statistics.count,
                    "mean": statistics.mean,
                    "median": statistics.median,
                    "min": statistics.min,
                    "max": statistics.max,
                    "std": statistics.std,
                    "p05": statistics.p05,
                    "p25": statistics.p25,
                    "p75": statistics.p75,
                    "p95": statistics.p95,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO export_artifacts (
                        id, project_id, owner_id, dataset_id, job_id, processing_item_id,
                        artifact_type, state, object_key, object_version_id, size_bytes,
                        checksum_sha256, created_at, updated_at
                    ) VALUES (
                        :id, :project_id, :owner_id, :dataset_id, :job_id, :item_id,
                        'geotiff', 'ready', :object_key, :version_id, :size_bytes,
                        :checksum, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "project_id": item["project_id"],
                    "owner_id": item["owner_id"],
                    "dataset_id": item["dataset_id"],
                    "job_id": item["job_id"],
                    "item_id": item["id"],
                    "object_key": object_key,
                    "version_id": version_id,
                    "size_bytes": size_bytes,
                    "checksum": checksum,
                },
            )
            await self._terminal_update(
                connection, item["id"], "completed", None, None, item["capture_key"], None
            )
            await self._finalize_job(connection, item["job_id"])

    async def prepare_retry(self, item_id: UUID, error: BaseException, trace: str) -> bool:
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT item.attempts, item.max_attempts, item.job_id,
                                   capture.capture_key
                            FROM processing_items item
                            JOIN captures capture ON capture.id = item.capture_id
                            JOIN processing_jobs job ON job.id = item.job_id
                            WHERE item.id = :id AND job.state <> 'cancelled'
                            FOR UPDATE OF item
                            """
                        ),
                        {"id": item_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or int(row["attempts"]) >= int(row["max_attempts"]):
                return False
            await connection.execute(
                text(
                    """
                    UPDATE processing_items SET state = 'pending', error_code = 'retrying',
                        error_message = :message, internal_traceback = :trace,
                        updated_at = CURRENT_TIMESTAMP WHERE id = :id
                    """
                ),
                {"id": item_id, "message": self._public_message(error), "trace": trace[-20_000:]},
            )
            return True

    async def mark_failed(self, item_id: UUID, error: BaseException, trace: str) -> None:
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT item.job_id, capture.capture_key
                            FROM processing_items item
                            LEFT JOIN captures capture ON capture.id = item.capture_id
                            WHERE item.id = :id FOR UPDATE OF item
                            """
                        ),
                        {"id": item_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return
            code = self._error_code(error)
            await self._terminal_update(
                connection,
                item_id,
                "failed",
                code,
                self._public_message(error),
                row["capture_key"],
                trace[-20_000:],
                self.current_file,
            )
            await self._finalize_job(connection, row["job_id"])

    async def mark_cancelled(self, item_id: UUID) -> None:
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text("SELECT job_id, error_capture FROM processing_items WHERE id = :id"),
                        {"id": item_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return
            await self._terminal_update(
                connection,
                item_id,
                "skipped",
                "job_cancelled",
                "Processamento cancelado pelo usuário",
                row["error_capture"],
                None,
            )
            await self._finalize_job(connection, row["job_id"])

    async def _is_cancelled(self, job_id: UUID) -> bool:
        async with self.engine.connect() as connection:
            state = await connection.scalar(
                text("SELECT state FROM processing_jobs WHERE id = :id"), {"id": job_id}
            )
            return bool(state == "cancelled")

    async def _terminal_update(
        self,
        connection: Any,
        item_id: UUID,
        state: str,
        code: str | None,
        message: str | None,
        capture: str | None,
        trace: str | None,
        file: str | None = None,
    ) -> None:
        await connection.execute(
            text(
                """
                UPDATE processing_items SET state = :state, error_code = :code,
                    error_message = :message, error_capture = COALESCE(:capture, error_capture),
                    error_file = COALESCE(:file, error_file), internal_traceback = :trace,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP WHERE id = :id
                """
            ),
            {
                "id": item_id,
                "state": state,
                "code": code,
                "message": message,
                "capture": capture,
                "trace": trace,
                "file": file,
            },
        )

    async def _finalize_job(self, connection: Any, job_id: UUID) -> None:
        counts = {
            str(row[0]): int(row[1])
            for row in (
                await connection.execute(
                    text(
                        "SELECT state, count(*) FROM processing_items "
                        "WHERE job_id = :id GROUP BY state"
                    ),
                    {"id": job_id},
                )
            ).all()
        }
        completed = counts.get("completed", 0)
        failed = counts.get("failed", 0)
        skipped = counts.get("skipped", 0)
        processed = completed + failed + skipped
        total = sum(counts.values())
        current = await connection.scalar(
            text("SELECT state FROM processing_jobs WHERE id = :id FOR UPDATE"), {"id": job_id}
        )
        if current == "cancelled":
            state = "cancelled"
        elif processed == total:
            state = "completed_with_errors" if failed or skipped else "completed"
        else:
            state = "running"
        await connection.execute(
            text(
                """
                UPDATE processing_jobs
                SET state = :state, total_items = :total, processed_items = :processed,
                    completed_items = :completed, failed_items = :failed,
                    skipped_items = :skipped,
                    finished_at = CASE WHEN :terminal THEN CURRENT_TIMESTAMP ELSE NULL END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
                """
            ),
            {
                "id": job_id,
                "state": state,
                "total": total,
                "processed": processed,
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "terminal": state in {"completed", "completed_with_errors", "cancelled"},
            },
        )

    @staticmethod
    def _error_code(error: BaseException) -> str:
        if isinstance(error, MissingBandError):
            return "missing_band"
        if isinstance(error, IncompatibleDimensionsError):
            return "incompatible_dimensions"
        if isinstance(error, SpatialMetadataMismatchError):
            return "spatial_mismatch"
        if isinstance(error, InvalidParameterError):
            return "invalid_parameters"
        return "processing_failed"

    @staticmethod
    def _public_message(error: BaseException) -> str:
        if isinstance(error, NON_RETRYABLE):
            return str(error)
        return "Falha ao processar esta captura"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


def formatted_traceback() -> str:
    return traceback.format_exc()
