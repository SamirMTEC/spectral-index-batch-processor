"""Asynchronous, reproducible scientific export generation."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import re
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

import boto3
from botocore.config import Config
from openpyxl import Workbook  # type: ignore[import-untyped]
from spectral_common import Settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

RESULT_COLUMNS = [
    "project",
    "dataset",
    "treatment",
    "repetition",
    "index",
    "analysis_scope",
    "mean",
    "median",
    "min",
    "max",
    "std",
    "p05",
    "p25",
    "p75",
    "p95",
    "valid_pixels",
    "roi_id",
    "processing_job_id",
]
SUMMARY_COLUMNS = [
    "project",
    "dataset",
    "treatment",
    "index",
    "analysis_scope",
    "repetitions",
    "mean",
    "std",
]
ARTIFACT_PATHS = {
    "csv": "results.csv",
    "xlsx": "results.xlsx",
    "summary_csv": "results_summary.csv",
    "summary_xlsx": "results_summary.xlsx",
    "roi_json": "roi/roi.json",
    "manifest_json": "metadata/manifest.json",
    "processing_log": "logs/processing.log",
}
SENSITIVE_CONFIGURATION_KEYS = (
    "password",
    "secret",
    "token",
    "credential",
    "access_key",
    "database",
    "redis",
    "endpoint",
    "dsn",
)


def _safe_segment(value: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or fallback).strip())
    return cleaned.strip("._")[:120] or fallback


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_manifest_value(value: Any) -> Any:
    """Remove accidental infrastructure secrets from user-visible export metadata."""
    if isinstance(value, dict):
        return {
            str(key): _safe_manifest_value(nested)
            for key, nested in value.items()
            if not any(marker in str(key).casefold() for marker in SENSITIVE_CONFIGURATION_KEYS)
        }
    if isinstance(value, list):
        return [_safe_manifest_value(nested) for nested in value]
    return value


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str] = RESULT_COLUMNS) -> None:
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        writer.writerows({column: row.get(column) for column in columns} for row in rows)


def _write_xlsx(
    path: Path, rows: list[dict[str, Any]], columns: list[str] = RESULT_COLUMNS
) -> None:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Results")
    header = []
    for name in columns:
        header.append(name)
    sheet.append(header)
    for row in rows:
        sheet.append(
            [
                str(value) if isinstance(value := row.get(column), UUID) else value
                for column in columns
            ]
        )
    workbook.save(path)


class ExportGenerator:
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
        if settings.s3_endpoint_url:
            options["endpoint_url"] = settings.s3_endpoint_url
        if settings.s3_access_key_id:
            options["aws_access_key_id"] = settings.s3_access_key_id
        if settings.s3_secret:
            options["aws_secret_access_key"] = settings.s3_secret
        self.s3 = boto3.client(**options)

    async def close(self) -> None:
        await self.engine.dispose()
        close = getattr(self.s3, "close", None)
        if close:
            close()

    async def generate(self, artifact_ids: list[UUID]) -> None:
        artifacts = await self._claim(artifact_ids)
        if not artifacts:
            return
        try:
            job_id = UUID(str(artifacts[0]["job_id"]))
            snapshot = await self._snapshot(job_id)
            with TemporaryDirectory(prefix="spectral-export-") as directory:
                root = Path(directory) / "SpectralIndex_Export"
                self._write_common_files(root, snapshot)
                for artifact in artifacts:
                    output = self._output_for(artifact, root, snapshot)
                    await self._upload(artifact, output)
        except Exception:
            await self._mark_failed([UUID(str(artifact["id"])) for artifact in artifacts])
            raise

    async def _claim(self, artifact_ids: list[UUID]) -> list[dict[str, Any]]:
        if not artifact_ids:
            return []
        async with self.engine.begin() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT id, project_id, dataset_id, job_id, artifact_type
                        FROM export_artifacts
                        WHERE id = ANY(:ids) AND state = 'queued'
                        FOR UPDATE
                        """
                        ),
                        {"ids": artifact_ids},
                    )
                )
                .mappings()
                .all()
            )
            if not rows:
                return []
            job_ids = {row["job_id"] for row in rows}
            if len(job_ids) != 1 or None in job_ids:
                raise ValueError("Export task must contain artifacts from one processing job")
            await connection.execute(
                text(
                    "UPDATE export_artifacts SET state = 'generating', "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": [row["id"] for row in rows]},
            )
            return [dict(row) for row in rows]

    async def _snapshot(self, job_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            job = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT job.id, job.project_id, job.dataset_id, job.state,
                               job.configuration_snapshot, job.reproducibility_snapshot,
                               job.snapshot_schema_version, job.snapshot_checksum,
                               job.software_version, job.parent_job_id, job.recipe_id,
                               job.created_at, job.finished_at, project.name AS project_name,
                               dataset.name AS dataset_name, dataset.radiometry_mode
                        FROM processing_jobs job
                        JOIN projects project ON project.id = job.project_id
                        JOIN datasets dataset ON dataset.id = job.dataset_id
                        WHERE job.id = :job_id
                        """
                        ),
                        {"job_id": job_id},
                    )
                )
                .mappings()
                .one()
            )
            raw_rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT item.id AS item_id, item.index_name, item.formula_version,
                               item.analysis_scope,
                               item.formula_expression, item.parameters_snapshot,
                               item.input_snapshot, item.roi_snapshot, item.roi_id,
                               item.roi_version_id, item.state AS item_state, item.error_code,
                               item.error_message, item.error_file, item.error_capture,
                               capture.id AS capture_id, capture.capture_key,
                               replicate.label AS repetition, treatment.name AS treatment,
                               result.object_key AS result_object_key,
                               result.object_version_id AS result_object_version_id,
                               result.valid_pixel_count, stats.mean, stats.median,
                               stats.min, stats.max,
                               stats.std, stats.p05, stats.p25, stats.p75, stats.p95
                        FROM processing_items item
                        LEFT JOIN captures capture ON capture.id = item.capture_id
                        LEFT JOIN replicates replicate ON replicate.id = capture.replicate_id
                        LEFT JOIN treatments treatment
                          ON treatment.id = COALESCE(capture.treatment_id, replicate.treatment_id)
                        LEFT JOIN index_results result ON result.processing_item_id = item.id
                        LEFT JOIN statistics stats ON stats.index_result_id = result.id
                        WHERE item.job_id = :job_id
                        ORDER BY treatment.name NULLS LAST, replicate.label NULLS LAST,
                                 item.index_name
                        """
                        ),
                        {"job_id": job_id},
                    )
                )
                .mappings()
                .all()
            )
            capture_ids = [row["capture_id"] for row in raw_rows if row["capture_id"]]
            band_rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT capture_id, band_code, original_filename, sha256, object_version_id
                        FROM band_assets
                        WHERE capture_id = ANY(:capture_ids) AND role = 'scientific'
                          AND state = 'accepted'
                        ORDER BY band_code, original_filename
                        """
                        ),
                        {"capture_ids": capture_ids},
                    )
                )
                .mappings()
                .all()
                if capture_ids
                else []
            )
            roi_rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT roi.id AS roi_id, roi.name, version.id AS roi_version_id,
                               version.version_number, version.geometry_type,
                               version.normalized_coordinates, version.pixel_coordinates,
                               version.reference_width, version.reference_height, version.crs,
                               version.transform, version.spatial_coordinates,
                               version.geometry_checksum
                        FROM processing_items item
                        JOIN rois roi ON roi.id = item.roi_id
                        JOIN roi_versions version ON version.id = item.roi_version_id
                        WHERE item.job_id = :job_id
                        ORDER BY roi.name, version.version_number
                        """
                        ),
                        {"job_id": job_id},
                    )
                )
                .mappings()
                .all()
            )
        rows = [dict(row) for row in raw_rows]
        bands_by_capture: dict[Any, list[dict[str, Any]]] = {}
        for row in rows:
            frozen_inputs = row.get("input_snapshot") or []
            if row.get("capture_id") and frozen_inputs:
                bands_by_capture[row["capture_id"]] = [
                    {
                        "capture_id": row["capture_id"],
                        "band_code": value.get("band"),
                        "original_filename": value.get("filename"),
                        "sha256": value.get("checksum_sha256"),
                        "object_version_id": value.get("object_version_id"),
                    }
                    for value in frozen_inputs
                ]
        if not bands_by_capture:
            for band in band_rows:
                bands_by_capture.setdefault(band["capture_id"], []).append(dict(band))
        frozen_rois = [
            capture.get("roi")
            for capture in dict(job).get("reproducibility_snapshot", {}).get("captures", [])
            if capture.get("roi")
        ]
        return {
            "job": dict(job),
            "items": rows,
            "bands_by_capture": bands_by_capture,
            # PostgreSQL cannot apply DISTINCT to the JSON geometry fields above.
            # A processing job may refer to the same ROI version for several indexes,
            # so de-duplicate by its stable identifier after reading the rows.
            "rois": frozen_rois
            or list({row["roi_version_id"]: dict(row) for row in roi_rows}.values()),
        }

    def _write_common_files(self, root: Path, snapshot: dict[str, Any]) -> None:
        (root / "roi").mkdir(parents=True, exist_ok=True)
        (root / "metadata").mkdir(parents=True, exist_ok=True)
        (root / "logs").mkdir(parents=True, exist_ok=True)
        rows = self._result_rows(snapshot)
        summary_rows = self._summary_rows(snapshot, rows)
        _write_csv(root / "results.csv", rows)
        _write_xlsx(root / "results.xlsx", rows)
        _write_csv(root / "results_summary.csv", summary_rows, SUMMARY_COLUMNS)
        _write_xlsx(root / "results_summary.xlsx", summary_rows, SUMMARY_COLUMNS)
        (root / "roi" / "roi.json").write_text(
            json.dumps(snapshot["rois"], indent=2, default=str), encoding="utf-8"
        )
        config = _safe_manifest_value(snapshot["job"]["configuration_snapshot"])
        (root / "metadata" / "processing_config.json").write_text(
            json.dumps(config, indent=2, default=str), encoding="utf-8"
        )
        (root / "metadata" / "manifest.json").write_text(
            json.dumps(self._manifest(snapshot), indent=2, default=str), encoding="utf-8"
        )
        (root / "logs" / "processing.log").write_text(self._public_log(snapshot), encoding="utf-8")

    def _result_rows(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        job = snapshot["job"]
        return [
            {
                "project": job["project_name"],
                "dataset": job["dataset_name"],
                "treatment": item["treatment"],
                "repetition": item["repetition"],
                "index": item["index_name"],
                "analysis_scope": item["analysis_scope"],
                "mean": item["mean"],
                "median": item["median"],
                "min": item["min"],
                "max": item["max"],
                "std": item["std"],
                "p05": item["p05"],
                "p25": item["p25"],
                "p75": item["p75"],
                "p95": item["p95"],
                "valid_pixels": item["valid_pixel_count"],
                "roi_id": item["roi_id"],
                "processing_job_id": job["id"],
            }
            for item in snapshot["items"]
            if item["result_object_key"]
        ]

    def _summary_rows(
        self, snapshot: dict[str, Any], rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Summarize repetition means by treatment and index."""
        grouped: dict[tuple[str | None, str, str], list[float]] = {}
        for row in rows:
            value = row.get("mean")
            if value is None:
                continue
            key = (
                row.get("treatment"),
                str(row["index"]),
                str(row.get("analysis_scope") or "roi"),
            )
            grouped.setdefault(key, []).append(float(value))

        job = snapshot["job"]
        summaries: list[dict[str, Any]] = []
        for (treatment, index, analysis_scope), values in sorted(
            grouped.items(), key=lambda item: ((item[0][0] or ""), item[0][1], item[0][2])
        ):
            repetitions = len(values)
            mean = sum(values) / repetitions
            variance = sum((value - mean) ** 2 for value in values) / repetitions
            summaries.append(
                {
                    "project": job["project_name"],
                    "dataset": job["dataset_name"],
                    "treatment": treatment,
                    "index": index,
                    "analysis_scope": analysis_scope,
                    "repetitions": repetitions,
                    "mean": mean,
                    "std": variance**0.5,
                }
            )
        return summaries

    def _manifest(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        job = snapshot["job"]
        warnings: list[dict[str, Any]] = []
        failed_items: list[dict[str, Any]] = []
        for item in snapshot["items"]:
            if item["item_state"] in {"failed", "skipped"}:
                record = {
                    "capture": item["capture_key"] or item["error_capture"],
                    "index": item["index_name"],
                    "analysis_scope": item.get("analysis_scope") or "roi",
                    "error_code": item["error_code"],
                    "message": item["error_message"],
                    "file": item["error_file"],
                }
                (failed_items if item["item_state"] == "failed" else warnings).append(record)
        frozen = _safe_manifest_value(job.get("reproducibility_snapshot") or {})
        manifest = dict(frozen) if isinstance(frozen, dict) else {}
        manifest.update(
            {
                "manifest_schema_version": job.get("snapshot_schema_version") or "legacy",
                "processing_date": job["finished_at"] or job["created_at"],
                "processing_job_id": job["id"],
                "parent_processing_job_id": job.get("parent_job_id"),
                "snapshot_checksum_sha256": job.get("snapshot_checksum"),
                "project": job["project_name"],
                "dataset_name": job["dataset_name"],
                "user_selected_parameters": _safe_manifest_value(job["configuration_snapshot"]),
                "warnings": warnings,
                "failed_items": failed_items,
            }
        )
        return manifest

    def _public_log(self, snapshot: dict[str, Any]) -> str:
        job = snapshot["job"]
        lines = [f"{datetime.now(UTC).isoformat()} job={job['id']} export=generated"]
        for item in snapshot["items"]:
            lines.append(
                " ".join(
                    [
                        f"item={item['item_id']}",
                        f"state={item['item_state']}",
                        f"capture={item['capture_key'] or item['error_capture'] or '-'}",
                        f"index={item['index_name']}",
                        f"scope={item.get('analysis_scope') or 'roi'}",
                        f"error_code={item['error_code'] or '-'}",
                        f"message={item['error_message'] or '-'}",
                    ]
                )
            )
        return "\n".join(lines) + "\n"

    def _output_for(self, artifact: dict[str, Any], root: Path, snapshot: dict[str, Any]) -> Path:
        artifact_type = str(artifact["artifact_type"])
        if artifact_type == "calibrated_rasters_zip":
            calibrated_root = root.parent / "SpectralIndex_Calibrated"
            self._add_calibrated_rasters(calibrated_root, snapshot)
            output = root.parent / "SpectralIndex_Calibrated.zip"
            with zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                for source in calibrated_root.rglob("*"):
                    if source.is_file():
                        archive.write(source, source.relative_to(calibrated_root.parent))
            return output
        if artifact_type in {"roi_rasters_zip", "full_image_rasters_zip"}:
            scope = "roi" if artifact_type == "roi_rasters_zip" else "full_image"
            return self._raster_archive(root, snapshot, scope)
        if artifact_type != "bundle_zip":
            return root / ARTIFACT_PATHS[artifact_type]
        self._add_rasters(root, snapshot)
        self._add_calibrated_rasters(root, snapshot)
        output = root.parent / "SpectralIndex_Export.zip"
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for source in root.rglob("*"):
                if source.is_file():
                    archive.write(source, source.relative_to(root.parent))
        return output

    def _add_calibrated_rasters(self, root: Path, snapshot: dict[str, Any]) -> None:
        inputs: dict[str, dict[tuple[str, str], str]] = {}
        frozen = snapshot["job"].get("reproducibility_snapshot") or {}
        archive_key = frozen.get("calibration_archive_key")
        if isinstance(archive_key, str):
            for capture in frozen.get("captures", []):
                label = _safe_segment(capture.get("capture"), "capture")
                for code, member in capture.get("calibrated_outputs", {}).items():
                    if isinstance(member, str):
                        inputs.setdefault(archive_key, {})[(label, str(code))] = member
        for item in snapshot["items"]:
            capture = _safe_segment(item.get("capture_key"), "capture")
            for band in item.get("input_snapshot") or []:
                key = band.get("calibration_archive_key")
                member = band.get("calibration_member")
                code = band.get("band")
                if isinstance(key, str) and isinstance(member, str) and isinstance(code, str):
                    inputs.setdefault(key, {})[(capture, code)] = member
        for number, (key, selected) in enumerate(inputs.items()):
            archive_path = root.parent / f"calibration-input-{number}.zip"
            self.s3.download_file(self.settings.s3_bucket, key, str(archive_path))
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    for (capture, code), member in selected.items():
                        info = archive.getinfo(member)
                        if (
                            info.is_dir()
                            or info.file_size > 2_000_000_000
                            or not member.lower().endswith(".tif")
                        ):
                            raise ValueError("Invalid calibrated TIFF in calibration archive")
                        target = (
                            root
                            / "calibrated_images"
                            / capture
                            / (
                                f"{_safe_segment(code, 'band')}_"
                                f"{_safe_segment(Path(member).name, 'image.tif')}"
                            )
                        )
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as source, target.open("wb") as destination:
                            shutil.copyfileobj(source, destination)
            finally:
                archive_path.unlink(missing_ok=True)

    def _raster_archive(self, root: Path, snapshot: dict[str, Any], analysis_scope: str) -> Path:
        scoped_root = root.parent / (
            "SpectralIndex_ROI" if analysis_scope == "roi" else "SpectralIndex_Full_Image"
        )
        self._add_rasters(scoped_root, snapshot, analysis_scope=analysis_scope)
        output = root.parent / (
            "SpectralIndex_ROI.zip" if analysis_scope == "roi" else "SpectralIndex_Full_Image.zip"
        )
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for source in scoped_root.rglob("*"):
                if source.is_file():
                    archive.write(source, source.relative_to(scoped_root.parent))
        return output

    def _add_rasters(
        self,
        root: Path,
        snapshot: dict[str, Any],
        *,
        analysis_scope: str | None = None,
    ) -> None:
        for item in snapshot["items"]:
            key = item["result_object_key"]
            scope = str(item.get("analysis_scope") or "roi")
            if not key or (analysis_scope is not None and scope != analysis_scope):
                continue
            treatment = _safe_segment(item["treatment"], "unassigned")
            capture = _safe_segment(item["capture_key"], "capture")
            index = _safe_segment(item["index_name"], "index")
            scope_directory = "roi" if scope == "roi" else "full_image"
            target = root / "rasters" / scope_directory / treatment / f"{capture}_{index}.tif"
            target.parent.mkdir(parents=True, exist_ok=True)
            extra = (
                {"VersionId": item["result_object_version_id"]}
                if item["result_object_version_id"]
                else None
            )
            if extra:
                self.s3.download_file(self.settings.s3_bucket, key, str(target), ExtraArgs=extra)
            else:
                self.s3.download_file(self.settings.s3_bucket, key, str(target))

    async def _upload(self, artifact: dict[str, Any], output: Path) -> None:
        artifact_id = UUID(str(artifact["id"]))
        checksum = await asyncio.to_thread(_sha256, output)
        suffix = output.suffix.lower()
        content_type = (
            "application/zip"
            if suffix == ".zip"
            else (
                "application/json"
                if suffix == ".json"
                else "text/csv"
                if suffix == ".csv"
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if suffix == ".xlsx"
                else "text/plain"
            )
        )
        object_key = (
            f"exports/{artifact['project_id']}/{artifact['dataset_id']}/{artifact['job_id']}/"
            f"{artifact_id}/{output.name}"
        )
        await asyncio.to_thread(
            self.s3.upload_file,
            str(output),
            self.settings.s3_bucket,
            object_key,
            ExtraArgs={
                "ContentType": content_type,
                "Metadata": {"sha256": checksum, "export-artifact-id": str(artifact_id)},
            },
        )
        head = await asyncio.to_thread(
            self.s3.head_object, Bucket=self.settings.s3_bucket, Key=object_key
        )
        output_size = await asyncio.to_thread(lambda: output.stat().st_size)
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                UPDATE export_artifacts SET state = 'ready', object_key = :key,
                    object_version_id = :version, size_bytes = :size, checksum_sha256 = :checksum,
                    updated_at = CURRENT_TIMESTAMP WHERE id = :id
            """),
                {
                    "id": artifact_id,
                    "key": object_key,
                    "version": head.get("VersionId"),
                    "size": int(head.get("ContentLength", output_size)),
                    "checksum": checksum,
                },
            )

    async def _mark_failed(self, artifact_ids: list[UUID]) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                UPDATE export_artifacts SET state = 'failed', updated_at = CURRENT_TIMESTAMP
                WHERE id = ANY(:ids) AND state = 'generating'
            """),
                {"ids": artifact_ids},
            )
