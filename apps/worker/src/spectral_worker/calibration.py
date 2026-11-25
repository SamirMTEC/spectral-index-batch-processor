"""Windowed calibration of immutable captures with reproducible audit exports."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import math
import re
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import boto3
import numpy as np
import rasterio
from botocore.config import Config
from rasterio.features import geometry_mask
from rasterio.windows import Window
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from spectral_common import Settings
from spectral_engine.calibration import ALGORITHM_VERSION, camera_signal, fit_panel, roi_statistics
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from spectral_worker.inspection import build_preview_png


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _named_calibrated_file(filename: str) -> str:
    """Keep the source image name visible in a desktop batch export."""
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(filename).stem).strip("._")
    if not stem:
        raise ValueError("A source image needs a usable filename")
    return f"{stem}_calibrated.tif"


def _combine_reference_models(references: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Form one per-band transform from independently measured panel captures."""
    if not references:
        raise ValueError("Shared calibration requires a panel capture")
    bands = set(references[0]["bands"])
    if any(set(reference["bands"]) != bands for reference in references[1:]):
        raise ValueError("Reference captures do not contain the same bands")
    combined: dict[str, dict[str, Any]] = {}
    for code in sorted(bands):
        models = [reference["bands"][code]["model"] for reference in references]
        slopes = [float(model["slope"]) for model in models]
        intercepts = [float(model["intercept"]) for model in models]
        if not all(math.isfinite(value) for value in slopes + intercepts):
            raise ValueError(f"{code}: non-finite reference coefficient")
        combined[code] = {
            "slope": sum(slopes) / len(slopes),
            "intercept": sum(intercepts) / len(intercepts),
            "method": "mean_coefficients",
            "reference_capture_ids": [str(reference["capture_id"]) for reference in references],
        }
    return combined


def _geometry_window(
    coordinates: list[list[float]],
    width: int,
    height: int,
    offset: tuple[float, float],
    inset_percent: float,
) -> tuple[Window, np.ndarray]:
    points = np.asarray(coordinates, dtype=float)
    if len(points) < 3 or np.any(~np.isfinite(points)) or np.any((points < 0) | (points > 1)):
        raise ValueError("Invalid normalized panel ROI")
    points *= [width, height]
    points += offset
    centroid = points.mean(axis=0)
    points = centroid + (points - centroid) * (1 - inset_percent / 100)
    left = max(0, math.floor(float(points[:, 0].min())))
    top = max(0, math.floor(float(points[:, 1].min())))
    right = min(width, math.ceil(float(points[:, 0].max())))
    bottom = min(height, math.ceil(float(points[:, 1].max())))
    if right <= left or bottom <= top:
        raise ValueError("Panel ROI lies outside the target band")
    window = Window(left, top, right - left, bottom - top)
    polygon = {
        "type": "Polygon",
        "coordinates": [[point.tolist() for point in points] + [points[0].tolist()]],
    }
    mask = geometry_mask(
        [polygon],
        out_shape=(bottom - top, right - left),
        transform=rasterio.Affine.translation(left, top),
        invert=True,
    )
    return window, mask


def _roi_for_band(rois: list[dict[str, Any]], surface: str, band: str) -> dict[str, Any] | None:
    exact = next(
        (roi for roi in rois if roi["surface"] == surface and roi.get("band") == band), None
    )
    return exact or next(
        (roi for roi in rois if roi["surface"] == surface and roi.get("band") is None), None
    )


def _metrics(predicted: list[float], reference: list[float]) -> dict[str, float]:
    residuals = np.asarray(predicted) - np.asarray(reference)
    return {
        "rmse": float(np.sqrt(np.mean(residuals**2))),
        "mae": float(np.mean(np.abs(residuals))),
        "bias": float(np.mean(residuals)),
    }


def _classification(results: dict[str, Any], config: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    thresholds = [
        config.get(key) for key in ("max_cv", "max_saturation_percent", "max_validation_rmse")
    ]
    if not any(value is not None for value in thresholds):
        return "not_evaluable", ["Nenhum critério operacional foi configurado."]
    failed = False
    for band in results.values():
        for surface in band["surfaces"]:
            stats = surface["dn"]
            if (
                config.get("max_cv") is not None
                and surface["signal"].get("cv") is not None
                and surface["signal"]["cv"] > config["max_cv"]
            ):
                failed = True
                reasons.append(f"CV acima do limite: {band['band']}/{surface['surface']}")
            if (
                config.get("max_saturation_percent") is not None
                and stats["saturated_percent"] is not None
                and stats["saturated_percent"] > config["max_saturation_percent"]
            ):
                failed = True
                reasons.append(f"Saturação acima do limite: {band['band']}/{surface['surface']}")
        validation = band.get("validation")
        if config.get("max_validation_rmse") is not None:
            if validation is None:
                return "not_evaluable", [
                    "Falta referência independente para o limite de RMSE de validação."
                ]
            if validation["rmse"] > config["max_validation_rmse"]:
                failed = True
                reasons.append(f"RMSE de validação acima do limite: {band['band']}")
    if failed:
        return "rejected_by_criteria", reasons
    caveats: list[str] = []
    if not config["panel_snapshot"]["source"].get("certified"):
        caveats.append("Referências sem certificado radiométrico verificado.")
    if config["input_mode"] == "dn_empirical":
        caveats.append("Modelo empírico baseado em DN, sem correção radiométrica do sensor.")
    if any(band.get("validation") is None for band in results.values()):
        caveats.append("Sem validação independente em todas as bandas.")
    if caveats:
        return "approved_with_caveats", caveats
    return "approved_by_criteria", ["Todos os limites configurados foram atendidos."]


class CalibrationProcessor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = create_async_engine(settings.database_dsn, pool_pre_ping=True)
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
        self.s3.close()

    async def _claim(self, run_id: UUID) -> dict[str, Any] | None:
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text("SELECT * FROM calibration_runs WHERE id=:id FOR UPDATE"),
                        {"id": run_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["state"] != "queued":
                return None
            await connection.execute(
                text(
                    "UPDATE calibration_runs SET state='running', "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=:id"
                ),
                {"id": run_id},
            )
            return dict(row)

    async def process(self, run_id: UUID) -> None:
        row = await self._claim(run_id)
        if row is None:
            return
        config = _json(row["config_json"])
        manifest = _json(row["manifest_json"])
        with TemporaryDirectory(prefix=f"spectral-calibration-{run_id}-") as directory:
            root = Path(directory)
            paths: dict[str, Path] = {}
            for index, band in enumerate(manifest["bands"]):
                path = root / f"input-{index}.tif"
                extra = (
                    {"VersionId": band["object_version_id"]}
                    if band.get("object_version_id")
                    else None
                )
                await asyncio.to_thread(
                    self.s3.download_file,
                    self.settings.s3_bucket,
                    band["object_key"],
                    str(path),
                    ExtraArgs=extra,
                )
                if band.get("sha256") and _sha256(path) != band["sha256"]:
                    raise ValueError(f"Input checksum mismatch: {band['filename']}")
                paths[str(band["band"]).upper()] = path
            result = await asyncio.to_thread(self._calculate, config, manifest, paths, root)
            primary_id = str(manifest["panel_capture"]["capture_id"])
            reference_results: dict[str, dict[str, Any]] = {primary_id: result}
            reference_inputs: dict[str, tuple[list[dict[str, Any]], dict[str, Path], Path]] = {
                primary_id: (manifest["bands"], paths, root)
            }
            result["reference_captures"] = [
                {
                    "capture_id": primary_id,
                    "capture_key": manifest["panel_capture"]["capture_key"],
                    "bands": result["bands"],
                    "warnings": result["warnings"].copy(),
                    "classification": result["classification"],
                    "classification_reasons": result["classification_reasons"],
                    "output_directory": ".",
                }
            ]
            for reference in manifest.get("additional_references", []):
                reference_id = str(reference["capture_id"])
                directory = root / "references" / reference_id
                directory.mkdir(parents=True, exist_ok=True)
                reference_paths: dict[str, Path] = {}
                for band in reference["bands"]:
                    code = str(band["band"]).upper()
                    path = directory / f"input-{code}.tif"
                    extra = (
                        {"VersionId": band["object_version_id"]}
                        if band.get("object_version_id")
                        else None
                    )
                    await asyncio.to_thread(
                        self.s3.download_file,
                        self.settings.s3_bucket,
                        band["object_key"],
                        str(path),
                        ExtraArgs=extra,
                    )
                    if band.get("sha256") and _sha256(path) != band["sha256"]:
                        raise ValueError(f"Input checksum mismatch: {band['filename']}")
                    reference_paths[code] = path
                reference_config = {**config, "rois": reference["rois"]}
                reference_manifest = {
                    "bands": reference["bands"],
                    "targets": manifest.get("targets", []),
                    "panel_capture": {
                        "capture_id": reference_id,
                        "capture_key": reference["capture_key"],
                    },
                }
                reference_result = await asyncio.to_thread(
                    self._calculate,
                    reference_config,
                    reference_manifest,
                    reference_paths,
                    directory,
                )
                reference_results[reference_id] = reference_result
                reference_inputs[reference_id] = (reference["bands"], reference_paths, directory)
                result["reference_captures"].append(
                    {
                        "capture_id": reference_id,
                        "capture_key": reference["capture_key"],
                        "bands": reference_result["bands"],
                        "warnings": reference_result["warnings"],
                        "classification": reference_result["classification"],
                        "classification_reasons": reference_result["classification_reasons"],
                        "output_directory": f"references/{reference_id}",
                    }
                )
                result["warnings"].extend(
                    f"{reference['capture_key']}: {warning}"
                    for warning in reference_result["warnings"]
                )
            if len(result["reference_captures"]) > 1:
                classifications = {
                    reference["classification"] for reference in result["reference_captures"]
                }
                if "rejected_by_criteria" in classifications:
                    result["classification"] = "rejected_by_criteria"
                elif "not_evaluable" in classifications:
                    result["classification"] = "not_evaluable"
                elif "approved_with_caveats" in classifications:
                    result["classification"] = "approved_with_caveats"
                result["classification_reasons"] = [
                    f"{reference['capture_key']}: {reason}"
                    for reference in result["reference_captures"]
                    for reason in reference["classification_reasons"]
                ]
            combined_models: dict[str, dict[str, Any]] | None = None
            if config.get("reference_combination") == "mean_coefficients":
                combined_models = _combine_reference_models(result["reference_captures"])
                result["combined_models"] = combined_models
                result["reference_combination"] = "mean_coefficients"
                result["combined_reference_check"] = {}
                for code, model in combined_models.items():
                    observations = []
                    for reference in result["reference_captures"]:
                        fitting = next(
                            item
                            for item in reference["bands"][code]["surfaces"]
                            if item["role"] == "fit"
                        )
                        estimated = (
                            fitting["signal"]["median"] * model["slope"] + model["intercept"]
                        )
                        observations.append(
                            {
                                "capture_id": reference["capture_id"],
                                "capture_key": reference["capture_key"],
                                "reference": fitting["reference"],
                                "estimated": estimated,
                                "residual": estimated - fitting["reference"],
                            }
                        )
                    result["combined_reference_check"][code] = {
                        "rmse": math.sqrt(
                            sum(item["residual"] ** 2 for item in observations) / len(observations)
                        ),
                        "observations": observations,
                    }
                if len(result["reference_captures"]) > 1:
                    result["warnings"].append(
                        "Coeficientes das referências combinados por média aritmética; "
                        "a estabilidade da iluminação entre capturas não foi comprovada."
                    )
                for reference in result["reference_captures"]:
                    reference_id = reference["capture_id"]
                    source_bands, source_paths, output_directory = reference_inputs[reference_id]
                    for source_band in source_bands:
                        code = str(source_band["band"]).upper()
                        named_output = output_directory / _named_calibrated_file(
                            source_band["filename"]
                        )
                        with rasterio.open(source_paths[code]) as source:
                            distribution = await asyncio.to_thread(
                                self._write_geotiff,
                                source,
                                source_band["metadata"],
                                config,
                                combined_models[code],
                                named_output,
                                original_filename=source_band["filename"],
                                capture_id=reference_id,
                                reference_capture_id="combined",
                                reference_capture_ids=combined_models[code][
                                    "reference_capture_ids"
                                ],
                            )
                        old_output = output_directory / f"calibrated-{code}.tif"
                        old_quality = output_directory / f"quality-{code}.tif"
                        old_output.unlink()
                        old_quality.unlink()
                        details = reference["bands"][code]
                        details["distribution"] = distribution
                        details["applied_model"] = combined_models[code]
                        details["output"] = str(named_output.relative_to(root)).replace("\\", "/")
                        details["output_sha256"] = _sha256(named_output)
            cancelled = False
            if manifest.get("targets"):
                result["targets"] = []
                result["target_summary"] = {
                    "requested": len(manifest["targets"]),
                    "completed": 0,
                    "failed": 0,
                    "skipped": len(manifest["targets"]),
                }
                await self._save_progress(run_id, result)
                for target in manifest["targets"]:
                    if await self._cancel_requested(run_id):
                        cancelled = True
                        break
                    target_result = await self._transfer_capture(
                        config,
                        target,
                        (
                            {code: {"model": model} for code, model in combined_models.items()}
                            if combined_models is not None
                            else reference_results[
                                str(target.get("reference_capture_id") or primary_id)
                            ]["bands"]
                        ),
                        root,
                    )
                    result["targets"].append(target_result)
                    completed = sum(item["state"] == "completed" for item in result["targets"])
                    failed = len(result["targets"]) - completed
                    result["target_summary"] = {
                        "requested": len(manifest["targets"]),
                        "completed": completed,
                        "failed": failed,
                        "skipped": len(manifest["targets"]) - completed - failed,
                    }
                    await self._save_progress(run_id, result)
                succeeded = sum(item["state"] == "completed" for item in result["targets"])
                if succeeded == 0 and not cancelled:
                    raise ValueError("No plant capture could be calibrated; check capture errors")
                if result["target_summary"]["failed"]:
                    result["warnings"].append(
                        "Algumas capturas falharam; consulte o audit.json antes de usar o lote."
                    )
                if cancelled:
                    result["warnings"].append(
                        "Lote cancelado; as capturas restantes não foram processadas."
                    )
            elif await self._cancel_requested(run_id):
                cancelled = True
            preview_keys: dict[str, dict[str, str]] = {}
            for code, original in paths.items():
                preview_keys[code] = {}
                for kind, image in (
                    ("original", original),
                    (
                        "calibrated",
                        root
                        / (
                            _named_calibrated_file(
                                next(
                                    band["filename"]
                                    for band in manifest["bands"]
                                    if str(band["band"]).upper() == code
                                )
                            )
                            if combined_models is not None
                            else f"calibrated-{code}.tif"
                        ),
                    ),
                ):
                    preview_path = root / f"preview-{kind}-{code}.png"
                    preview, _ = await asyncio.to_thread(build_preview_png, image)
                    preview_path.write_bytes(preview)
                    key = (
                        f"calibration/results/{row['owner_id']}/{run_id}/preview-{kind}-{code}.png"
                    )
                    await asyncio.to_thread(
                        self.s3.upload_file,
                        str(preview_path),
                        self.settings.s3_bucket,
                        key,
                        ExtraArgs={"ContentType": "image/png"},
                    )
                    preview_keys[code][kind] = key
            result["preview_keys"] = preview_keys
            if combined_models is not None:
                capture_preview_keys: dict[str, dict[str, dict[str, str]]] = {
                    primary_id: preview_keys
                }
                for reference in result["reference_captures"][1:]:
                    reference_id = reference["capture_id"]
                    _, source_paths, _ = reference_inputs[reference_id]
                    capture_preview_keys[reference_id] = {}
                    for code, details in reference["bands"].items():
                        capture_preview_keys[reference_id][code] = {}
                        for kind, path in (
                            ("original", source_paths[code]),
                            ("calibrated", root / details["output"]),
                        ):
                            preview, _ = await asyncio.to_thread(build_preview_png, path)
                            key = (
                                f"calibration/results/{row['owner_id']}/{run_id}/"
                                f"{reference_id}-{kind}-{code}.png"
                            )
                            await asyncio.to_thread(
                                self.s3.put_object,
                                Bucket=self.settings.s3_bucket,
                                Key=key,
                                Body=preview,
                                ContentType="image/png",
                            )
                            capture_preview_keys[reference_id][code][kind] = key
                for target in result.get("targets", []):
                    if target["state"] != "completed":
                        continue
                    target_id = target["capture_id"]
                    capture_preview_keys[target_id] = {}
                    for code, details in target["bands"].items():
                        capture_preview_keys[target_id][code] = {}
                        for kind, path in (
                            ("original", root / "captures" / target_id / f"input-{code}.tif"),
                            ("calibrated", root / details["output"]),
                        ):
                            preview, _ = await asyncio.to_thread(build_preview_png, path)
                            key = (
                                f"calibration/results/{row['owner_id']}/{run_id}/"
                                f"{target_id}-{kind}-{code}.png"
                            )
                            await asyncio.to_thread(
                                self.s3.put_object,
                                Bucket=self.settings.s3_bucket,
                                Key=key,
                                Body=preview,
                                ContentType="image/png",
                            )
                            capture_preview_keys[target_id][code][kind] = key
                result["capture_preview_keys"] = capture_preview_keys
            for capture in [*result.get("reference_captures", []), *result.get("targets", [])]:
                if capture.get("state", "completed") != "completed":
                    continue
                capture_id = str(capture["capture_id"])
                for code, details in capture.get("bands", {}).items():
                    member = details.get("output")
                    if not isinstance(member, str):
                        continue
                    calibrated_path = root / member
                    if not calibrated_path.is_file():
                        raise ValueError(f"Calibrated TIFF missing: {member}")
                    band_key = (
                        f"calibration/results/{row['owner_id']}/{run_id}/"
                        f"bands/{capture_id}/{code}.tif"
                    )
                    checksum = await asyncio.to_thread(_sha256, calibrated_path)
                    await asyncio.to_thread(
                        self.s3.upload_file,
                        str(calibrated_path),
                        self.settings.s3_bucket,
                        band_key,
                        ExtraArgs={"ContentType": "image/tiff", "Metadata": {"sha256": checksum}},
                    )
                    details["object_key"] = band_key
                    details["checksum_sha256"] = checksum
            package = root / "calibration-export.zip"
            await asyncio.to_thread(self._make_bundle, root, result, package)
            object_key = f"calibration/results/{row['owner_id']}/{run_id}/calibration-export.zip"
            await asyncio.to_thread(
                self.s3.upload_file,
                str(package),
                self.settings.s3_bucket,
                object_key,
                ExtraArgs={"ContentType": "application/zip"},
            )
            async with self.engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE calibration_runs SET state=:state, "
                        "result_json=CAST(:result AS jsonb), output_key=:key, "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=:id"
                    ),
                    {
                        "id": run_id,
                        "state": "cancelled" if cancelled else "completed",
                        "result": json.dumps(result, allow_nan=False),
                        "key": object_key,
                    },
                )

    async def _cancel_requested(self, run_id: UUID) -> bool:
        async with self.engine.connect() as connection:
            state = await connection.scalar(
                text("SELECT state FROM calibration_runs WHERE id=:id"), {"id": run_id}
            )
        return state in {"cancel_requested", "cancelled"}

    async def _save_progress(self, run_id: UUID, result: dict[str, Any]) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE calibration_runs SET result_json=CAST(:result AS jsonb), "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=:id"
                ),
                {"id": run_id, "result": json.dumps(result, allow_nan=False)},
            )

    async def _transfer_capture(
        self,
        config: dict[str, Any],
        capture: dict[str, Any],
        panel_bands: dict[str, Any],
        root: Path,
    ) -> dict[str, Any]:
        capture_id = str(capture["capture_id"])
        directory = root / "captures" / capture_id
        directory.mkdir(parents=True, exist_ok=True)
        outcome: dict[str, Any] = {
            "capture_id": capture_id,
            "capture_key": capture["capture_key"],
            "reference_capture_id": capture.get("reference_capture_id"),
            "reference_capture_ids": capture.get("reference_capture_ids", []),
            "state": "completed",
            "bands": {},
            "errors": [],
        }
        try:
            if capture.get("preflight_errors"):
                raise ValueError("; ".join(capture["preflight_errors"]))
            for band in capture["bands"]:
                code = str(band["band"]).upper()
                input_path = directory / f"input-{code}.tif"
                extra = (
                    {"VersionId": band["object_version_id"]}
                    if band.get("object_version_id")
                    else None
                )
                await asyncio.to_thread(
                    self.s3.download_file,
                    self.settings.s3_bucket,
                    band["object_key"],
                    str(input_path),
                    ExtraArgs=extra,
                )
                if band.get("sha256") and _sha256(input_path) != band["sha256"]:
                    raise ValueError(f"Checksum mismatch: {band['filename']}")
                output = directory / (
                    _named_calibrated_file(band["filename"])
                    if config.get("reference_combination") == "mean_coefficients"
                    else f"reflectance-{code}.tif"
                )
                with rasterio.open(input_path) as source:
                    if source.count != 1:
                        raise ValueError(f"{code}: expected a single-band TIFF")
                    distribution = await asyncio.to_thread(
                        self._write_geotiff,
                        source,
                        band["metadata"],
                        config,
                        panel_bands[code]["model"],
                        output,
                        original_filename=band["filename"],
                        capture_id=capture_id,
                        reference_capture_id=capture.get("reference_capture_id"),
                        reference_capture_ids=capture.get("reference_capture_ids"),
                    )
                outcome["bands"][code] = {
                    "original_filename": band["filename"],
                    "original_sha256": band.get("sha256"),
                    "output": str(output.relative_to(root)).replace("\\", "/"),
                    "output_sha256": _sha256(output),
                    "distribution": distribution,
                    "metadata": band["metadata"],
                    "panel_coefficient": panel_bands[code]["model"],
                }
        except Exception as error:
            outcome["state"] = "failed"
            outcome["errors"].append(f"{type(error).__name__}: {error}")
            for output in list(directory.glob("reflectance-*.tif")) + list(
                directory.glob("quality-*.tif")
            ):
                output.unlink()
            outcome["bands"] = {}
        (directory / "calibration.json").write_text(
            json.dumps(outcome, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        with (directory / "QA.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "band",
                    "state",
                    "invalid_percent",
                    "saturated_percent",
                    "out_of_range_percent",
                    "error",
                ]
            )
            for code in ("BLUE", "GREEN", "RED", "RED_EDGE", "NIR"):
                details = outcome["bands"].get(code, {})
                distribution = details.get("distribution", {})
                writer.writerow(
                    [
                        code,
                        outcome["state"],
                        distribution.get("invalid_percent"),
                        distribution.get("saturated_percent"),
                        distribution.get("out_of_range_percent"),
                        "; ".join(outcome["errors"]),
                    ]
                )
        return outcome

    async def mark_failed(self, run_id: UUID, error: Exception) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE calibration_runs SET state='failed', error=:error, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=:id "
                    "AND state NOT IN ('completed', 'cancelled')"
                ),
                {"id": run_id, "error": f"{type(error).__name__}: {str(error)[:1000]}"},
            )

    def _calculate(
        self, config: dict[str, Any], manifest: dict[str, Any], paths: dict[str, Path], root: Path
    ) -> dict[str, Any]:
        panel = config["panel_snapshot"]
        references = panel["values"]
        rois = config["rois"]
        band_results: dict[str, Any] = {}
        warnings: list[str] = []
        if not panel["source"].get("certified"):
            warnings.append(
                "Valores de referência não certificados; reflectância estimada "
                "sem validação metrológica."
            )
        if config["input_mode"] == "dn_empirical":
            warnings.append(
                "Ajuste empírico de DN; diferenças de exposição, ganho e nível de preto "
                "podem invalidar a transferência."
            )
        if config["input_mode"] == "p4_signal":
            warnings.append(
                "Sinal de câmera corrigido segundo metadados DJI; não é radiância física absoluta."
            )
        if manifest.get("targets"):
            if config.get("illumination_method") != "stable_panel":
                warnings.append(
                    "Estabilidade da iluminação não verificada; o fator único pode não ser "
                    "transferível a todo o lote."
                )
            else:
                warnings.append(
                    "Iluminação estável declarada para o lote; não foi validada por "
                    "série de irradiância."
                )
        reference_band = str(config.get("reference_band") or "NIR").upper()
        batch = bool(manifest.get("targets"))
        metadata_by_band = {
            str(band["band"]).upper(): band["metadata"] for band in manifest["bands"]
        }
        reference_offset = metadata_by_band.get(reference_band, {}).get(
            "relative_optical_center_px"
        ) or [0, 0]
        with ExitStack() as stack:
            sources = {
                code: stack.enter_context(rasterio.open(path)) for code, path in paths.items()
            }
            for code, source in sources.items():
                metadata = metadata_by_band[code]
                if source.count != 1:
                    raise ValueError(f"Multiband source {code} requires an explicit band mapping")
                needs_propagation = any(
                    (selected := _roi_for_band(rois, surface_name, code)) is not None
                    and selected.get("band") is None
                    for surface_name, values in references.items()
                    if code in values
                )
                target_offset = metadata.get("relative_optical_center_px")
                if target_offset is None and code != reference_band:
                    if needs_propagation:
                        raise ValueError(
                            f"No band registration for {code}; draw band-specific ROIs"
                        )
                    target_offset = reference_offset
                if code != reference_band and needs_propagation:
                    speeds = metadata.get("flight_speed_mps")
                    if (
                        not isinstance(speeds, list)
                        or any(value is None for value in speeds)
                        or math.sqrt(sum(float(value) ** 2 for value in speeds)) > 0.2
                    ):
                        raise ValueError(
                            f"Metadata-only registration is unsafe for {code}; "
                            "draw band-specific ROIs"
                        )
                offset = (
                    (
                        float(target_offset[0] - reference_offset[0]),
                        float(target_offset[1] - reference_offset[1]),
                    )
                    if target_offset
                    else (0.0, 0.0)
                )
                surfaces: list[dict[str, Any]] = []
                for surface_name, band_values in references.items():
                    if code not in band_values:
                        continue
                    roi = _roi_for_band(rois, surface_name, code)
                    if roi is None:
                        continue
                    local_offset = (0.0, 0.0) if roi.get("band") == code else offset
                    window, polygon_mask = _geometry_window(
                        roi["coordinates"],
                        source.width,
                        source.height,
                        local_offset,
                        float(config["edge_inset_percent"]),
                    )
                    raw = source.read(1, window=window, masked=True)
                    valid = polygon_mask & ~np.ma.getmaskarray(raw)
                    if not np.any(valid):
                        raise ValueError(f"No valid ROI pixels for {code}/{surface_name}")
                    dn = np.asarray(raw.data, dtype=np.float64)
                    saturation = (
                        float(np.iinfo(np.dtype(source.dtypes[0])).max)
                        if np.issubdtype(np.dtype(source.dtypes[0]), np.integer)
                        else None
                    )
                    raw_stats = roi_statistics(dn[valid], saturation=saturation)
                    if config["exclude_saturated"] and saturation is not None:
                        valid &= dn < saturation
                    signal = camera_signal(
                        dn,
                        metadata,
                        x0=int(window.col_off),
                        y0=int(window.row_off),
                        mode=config["input_mode"],
                    )
                    signal_stats = roi_statistics(signal[valid])
                    surfaces.append(
                        {
                            "surface": surface_name,
                            "role": roi["role"],
                            "reference": float(band_values[code]),
                            "roi_coordinates": roi["coordinates"],
                            "roi_offset_px": local_offset,
                            "dn": raw_stats,
                            "signal": signal_stats,
                        }
                    )
                fitting = [item for item in surfaces if item["role"] == "fit"]
                minimum = 1 if config["method"] == "proportional" else 2
                if len(fitting) < minimum:
                    raise ValueError(f"Insufficient panel surfaces for {code}")
                weights = None
                if config["weighted"]:
                    weights = [1 / max(item["signal"]["std"] ** 2, 1e-12) for item in fitting]
                model = fit_panel(
                    [item["signal"]["median"] for item in fitting],
                    [item["reference"] for item in fitting],
                    method=config["method"],
                    weights=weights,
                    robust=bool(config["robust"]),
                )
                for item in surfaces:
                    item["estimated"] = (
                        item["signal"]["median"] * model["slope"] + model["intercept"]
                    )
                    item["residual"] = item["estimated"] - item["reference"]
                heldout = [item for item in surfaces if item["role"] == "validation"]
                heldout_metrics = (
                    _metrics(
                        [item["estimated"] for item in heldout],
                        [item["reference"] for item in heldout],
                    )
                    if heldout
                    else None
                )
                if not heldout:
                    warnings.append(
                        f"{code}: sem outras superfícies do painel para checagem interna."
                    )
                if any(item["estimated"] < 0 or item["estimated"] > 1 for item in surfaces):
                    warnings.append(
                        f"{code}: reflectância estimada fora de [0,1] em pelo menos uma referência."
                    )
                distribution = self._write_geotiff(
                    source,
                    metadata,
                    config,
                    model,
                    root / f"calibrated-{code}.tif",
                    original_filename=next(
                        item["filename"]
                        for item in manifest["bands"]
                        if str(item["band"]).upper() == code
                    ),
                    capture_id=str(manifest.get("panel_capture", {}).get("capture_id") or "panel"),
                    reference_capture_id=str(
                        manifest.get("panel_capture", {}).get("capture_id") or "panel"
                    ),
                )
                if distribution["out_of_range_percent"] > 0:
                    warnings.append(
                        f"{code}: há pixels com reflectância estimada fora de [0,1]; "
                        "os valores foram preservados para auditoria."
                    )
                band_results[code] = {
                    "band": code,
                    "filename": next(
                        item["filename"]
                        for item in manifest["bands"]
                        if str(item["band"]).upper() == code
                    ),
                    "metadata": metadata,
                    "surfaces": surfaces,
                    "model": model,
                    "fit_error": {key: model[key] for key in ("rmse", "mae", "bias", "r2")},
                    "validation": None if batch else heldout_metrics,
                    "internal_check": heldout_metrics if batch else None,
                    "distribution": distribution,
                }
        classification, reasons = _classification(band_results, config)
        return {
            "algorithm_version": ALGORITHM_VERSION,
            "processed_at": datetime.now(UTC).isoformat(),
            "method": config["method"],
            "input_mode": config["input_mode"],
            "panel": panel,
            "bands": band_results,
            "warnings": warnings,
            "classification": classification,
            "classification_reasons": reasons,
            "input_manifest": manifest,
            "reference_provenance": panel["source"],
            "session": {
                "name": config.get("session_name"),
                "date": config.get("session_date"),
                "timezone": config.get("session_timezone"),
                "illumination_method": config.get("illumination_method"),
                "illumination_notes": config.get("illumination_notes"),
                "panel_capture": manifest.get("panel_capture"),
            },
            "verification_status": "mathematically_computed",
            "internal_check_status": "performed"
            if batch and any(band.get("internal_check") for band in band_results.values())
            else "not_performed",
            "radiometric_validation_status": "not_established",
            "agisoft_comparison_status": "not_performed",
        }

    def _write_geotiff(
        self,
        source: Any,
        metadata: dict[str, Any],
        config: dict[str, Any],
        model: dict[str, Any],
        output: Path,
        *,
        original_filename: str | None = None,
        capture_id: str | None = None,
        reference_capture_id: str | None = None,
        reference_capture_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        profile = source.profile.copy()
        profile.update(
            driver="GTiff",
            dtype="float32",
            count=1,
            nodata=np.nan,
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress="deflate",
            predictor=3,
        )
        profile.pop("photometric", None)
        quality_name = output.name.replace("calibrated-", "quality-").replace(
            "reflectance-", "quality-"
        )
        if quality_name == output.name:
            quality_name = f"{output.stem}_quality.tif"
        quality_path = output.with_name(quality_name)
        quality_profile = profile.copy()
        quality_profile.update(dtype="uint8", predictor=1)
        quality_profile.pop("nodata", None)
        total = source.width * source.height
        invalid = 0
        saturated = 0
        out_of_range = 0
        sampled_before: list[np.ndarray] = []
        sampled_after: list[np.ndarray] = []
        sample_step = max(1, math.ceil(math.sqrt(total / 100_000)))
        saturation_dn = (
            float(np.iinfo(np.dtype(source.dtypes[0])).max)
            if np.issubdtype(np.dtype(source.dtypes[0]), np.integer)
            else None
        )
        with (
            rasterio.open(output, "w", **profile) as target,
            rasterio.open(quality_path, "w", **quality_profile) as quality,
        ):
            target.update_tags(**source.tags())
            quality.update_tags(
                quality_flags="1=invalid_or_nodata;2=saturated;4=outside_0_1",
                original_filename=original_filename or source.name.rsplit("/", 1)[-1],
            )
            for _, window in source.block_windows(1):
                raw = source.read(1, window=window, masked=True)
                signal = camera_signal(
                    np.asarray(raw.data),
                    metadata,
                    x0=int(window.col_off),
                    y0=int(window.row_off),
                    mode=config["input_mode"],
                )
                reflectance = signal * model["slope"] + model["intercept"]
                bad = np.ma.getmaskarray(raw) | ~np.isfinite(reflectance)
                saturated_mask = np.zeros(bad.shape, dtype=bool)
                if saturation_dn is not None:
                    saturated_mask = np.asarray(raw.data) >= saturation_dn
                    saturated += int(np.count_nonzero(saturated_mask & ~bad))
                    if config["exclude_saturated"]:
                        bad |= saturated_mask
                valid = ~bad
                outside_mask = ((reflectance < 0) | (reflectance > 1)) & valid
                invalid += int(np.count_nonzero(bad))
                out_of_range += int(np.count_nonzero(outside_mask))
                flags = (
                    bad.astype("uint8")
                    + 2 * saturated_mask.astype("uint8")
                    + 4 * outside_mask.astype("uint8")
                )
                quality.write(flags, 1, window=window)
                row_start = (-int(window.row_off)) % sample_step
                col_start = (-int(window.col_off)) % sample_step
                before_sample = signal[row_start::sample_step, col_start::sample_step]
                after_sample = reflectance[row_start::sample_step, col_start::sample_step]
                valid_sample = valid[row_start::sample_step, col_start::sample_step]
                sampled_before.append(before_sample[valid_sample])
                sampled_after.append(after_sample[valid_sample])
                reflectance[bad] = np.nan
                target.write(reflectance.astype("float32"), 1, window=window)
            target.update_tags(
                calibration_algorithm=ALGORITHM_VERSION,
                input_quantity=config["input_mode"],
                output_quantity="estimated_reflectance",
                reference_source=str(config["panel_snapshot"]["source"]),
                original_filename=original_filename or source.name.rsplit("/", 1)[-1],
                capture_id=capture_id or "panel",
                reference_capture_id=reference_capture_id or capture_id or "panel",
                reference_capture_ids=",".join(reference_capture_ids or []),
            )
        before = np.concatenate(sampled_before) if sampled_before else np.array([])
        after = np.concatenate(sampled_after) if sampled_after else np.array([])
        before_counts, before_edges = np.histogram(before, bins=32)
        after_counts, after_edges = np.histogram(after, bins=32)
        return {
            "invalid_percent": invalid / total * 100,
            "saturated_percent": saturated / total * 100,
            "out_of_range_percent": out_of_range / total * 100,
            "histogram_before": {"counts": before_counts.tolist(), "edges": before_edges.tolist()},
            "histogram_after": {"counts": after_counts.tolist(), "edges": after_edges.tolist()},
            "histogram_sampled_pixels": int(len(before)),
            "quality_mask_file": quality_path.name,
        }

    def _make_bundle(self, root: Path, result: dict[str, Any], package: Path) -> None:
        (root / "audit.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        with (root / "roi-statistics.csv").open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=[
                    "band",
                    "surface",
                    "role",
                    "reference",
                    "dn_median",
                    "dn_mean",
                    "dn_std",
                    "dn_cv",
                    "saturated_percent",
                    "signal_median",
                    "estimated",
                    "residual",
                    "valid_pixels",
                ],
            )
            writer.writeheader()
            for code, band in result["bands"].items():
                for item in band["surfaces"]:
                    writer.writerow(
                        {
                            "band": code,
                            "surface": item["surface"],
                            "role": item["role"],
                            "reference": item["reference"],
                            "dn_median": item["dn"]["median"],
                            "dn_mean": item["dn"]["mean"],
                            "dn_std": item["dn"]["std"],
                            "dn_cv": item["dn"]["cv"],
                            "saturated_percent": item["dn"]["saturated_percent"],
                            "signal_median": item["signal"]["median"],
                            "estimated": item["estimated"],
                            "residual": item["residual"],
                            "valid_pixels": item["dn"]["valid_pixels"],
                        }
                    )
        with (root / "coefficients.csv").open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=[
                    "band",
                    "coefficient_source",
                    "slope",
                    "intercept",
                    "r2_fit",
                    "rmse_fit",
                    "mae_fit",
                    "bias_fit",
                    "rmse_validation",
                    "mae_validation",
                    "bias_validation",
                    "rmse_internal_check",
                ],
            )
            writer.writeheader()
            for code, band in result["bands"].items():
                applied_model = result.get("combined_models", {}).get(code, band["model"])
                fit = band["fit_error"]
                validation = band["validation"] or {}
                internal = band.get("internal_check") or {}
                writer.writerow(
                    {
                        "band": code,
                        "coefficient_source": (
                            "mean_coefficients" if result.get("combined_models") else "individual"
                        ),
                        "slope": applied_model["slope"],
                        "intercept": applied_model["intercept"],
                        "r2_fit": fit["r2"],
                        "rmse_fit": fit["rmse"],
                        "mae_fit": fit["mae"],
                        "bias_fit": fit["bias"],
                        "rmse_validation": validation.get("rmse"),
                        "mae_validation": validation.get("mae"),
                        "bias_validation": validation.get("bias"),
                        "rmse_internal_check": internal.get("rmse"),
                    }
                )
        with (root / "reference-coefficients.csv").open(
            "w", newline="", encoding="utf-8"
        ) as output:
            writer = csv.DictWriter(
                output,
                fieldnames=[
                    "reference_capture_id",
                    "reference_capture_key",
                    "band",
                    "surface",
                    "reference_reflectance",
                    "roi_signal_median",
                    "slope",
                    "intercept",
                    "roi_signal_cv",
                    "output_directory",
                ],
            )
            writer.writeheader()
            for reference in result.get("reference_captures", []):
                for code, band in reference["bands"].items():
                    fitting = next(item for item in band["surfaces"] if item["role"] == "fit")
                    writer.writerow(
                        {
                            "reference_capture_id": reference["capture_id"],
                            "reference_capture_key": reference["capture_key"],
                            "band": code,
                            "surface": fitting["surface"],
                            "reference_reflectance": fitting["reference"],
                            "roi_signal_median": fitting["signal"]["median"],
                            "slope": band["model"]["slope"],
                            "intercept": band["model"]["intercept"],
                            "roi_signal_cv": fitting["signal"]["cv"],
                            "output_directory": reference["output_directory"],
                        }
                    )
        if result.get("combined_models"):
            with (root / "combined-coefficients.csv").open(
                "w", newline="", encoding="utf-8"
            ) as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=[
                        "band",
                        "slope",
                        "intercept",
                        "panel_rmse",
                        "reference_capture_ids",
                    ],
                )
                writer.writeheader()
                for code, model in result["combined_models"].items():
                    writer.writerow(
                        {
                            "band": code,
                            "slope": model["slope"],
                            "intercept": model["intercept"],
                            "panel_rmse": result["combined_reference_check"][code]["rmse"],
                            "reference_capture_ids": ";".join(model["reference_capture_ids"]),
                        }
                    )
        self._report(root / "calibration-report.pdf", result)
        with ZipFile(package, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            for path in root.rglob("*"):
                if path != package and path.is_file() and not path.name.startswith("input-"):
                    archive.write(path, str(path.relative_to(root)).replace("\\", "/"))

    def _report(self, path: Path, result: dict[str, Any]) -> None:
        page = canvas.Canvas(str(path), pagesize=A4)
        width, height = A4
        y = height - 45
        page.setFont("Helvetica-Bold", 16)
        page.drawString(45, y, "SpectralIndex - Auditoria de calibracao")
        y -= 28
        page.setFont("Helvetica", 9)
        lines = [
            f"Algoritmo: {result['algorithm_version']} | {result['processed_at']}",
            f"Metodo: {result['method']} | Entrada: {result['input_mode']}",
            f"Painel: {result['panel']['name']} | Fonte: {result['reference_provenance']}",
            f"Classificacao operacional: {result['classification']}",
            "Erros de ajuste, checagem interna e validacao sao distintos.",
            "Validacao radiometrica fisica: nao estabelecida.",
        ]
        if result.get("target_summary"):
            summary = result["target_summary"]
            lines.append(
                f"Lote: {summary['completed']}/{summary['requested']} capturas completas; "
                f"{summary['failed']} falhas."
            )
            lines.extend(
                f"Captura {item['capture_key']} (referencia {item.get('reference_capture_id')}): "
                f"{item['state']} {'; '.join(item['errors'])}"
                for item in result["targets"]
            )
        if len(result.get("reference_captures", [])) > 1:
            lines.append(
                f"Capturas de referencia: {len(result['reference_captures'])}; "
                "coeficientes individuais em reference-coefficients.csv."
            )
        if result.get("combined_models"):
            lines.append(
                "Coeficiente aplicado a todas as capturas: media aritmetica das referencias."
            )
        lines.extend(result["classification_reasons"])
        lines.extend(result["warnings"])
        for line in lines:
            if y < 80:
                page.showPage()
                y = height - 45
                page.setFont("Helvetica", 9)
            page.drawString(45, y, str(line)[:110])
            y -= 15
        y -= 10
        for code, band in result["bands"].items():
            if y < 190:
                page.showPage()
                y = height - 45
                page.setFont("Helvetica", 9)
            page.setFont("Helvetica-Bold", 11)
            applied_model = result.get("combined_models", {}).get(code, band["model"])
            page.drawString(
                45,
                y,
                f"{code}: rho = {applied_model['slope']:.8g} X + {applied_model['intercept']:.8g}",
            )
            y -= 18
            page.setFont("Helvetica", 9)
            r2 = band["fit_error"]["r2"]
            validation = band["validation"]
            internal = band.get("internal_check")
            summary = (
                f"Ajuste RMSE={band['fit_error']['rmse']:.5g} R2={r2 if r2 is not None else 'N/A'} "
                f"Checagem interna RMSE={internal['rmse'] if internal else 'N/A'} "
                f"Validacao RMSE={validation['rmse'] if validation else 'N/A'}"
            )
            page.drawString(
                45,
                y,
                summary[:110],
            )
            y -= 20
            chart_x, chart_y, size = 55, y - 110, 105
            page.setStrokeColorRGB(0.6, 0.6, 0.6)
            page.line(chart_x, chart_y, chart_x + size, chart_y + size)
            page.rect(chart_x, chart_y, size, size)
            for item in band["surfaces"]:
                page.setFillColorRGB(0.02, 0.43, 0.30) if item[
                    "role"
                ] == "fit" else page.setFillColorRGB(0.85, 0.35, 0.1)
                page.circle(
                    chart_x + size * item["reference"],
                    chart_y + size * item["estimated"],
                    2.5,
                    fill=1,
                    stroke=0,
                )
            page.setFillColorRGB(0, 0, 0)
            page.drawString(170, chart_y + 95, "Referencia vs estimativa")
            page.drawString(
                170, chart_y + 80, "Linha cinza: 1:1; verde: ajuste; laranja: validacao"
            )
            for index, item in enumerate(band["surfaces"][:4]):
                label = (
                    f"{item['surface']}: ref={item['reference']:.4f} "
                    f"est={item['estimated']:.4f} resid={item['residual']:.4f}"
                )
                page.drawString(
                    170,
                    chart_y + 60 - 14 * index,
                    label[:75],
                )
            y = chart_y - 24
        page.save()
