"""Owner-scoped panel library, capture inspection, and calibration history."""

from __future__ import annotations

import csv
import hashlib
import io
import math
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

import numpy as np
import rasterio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from rasterio.windows import Window
from spectral_common import get_settings
from spectral_engine.calibration import (
    ALGORITHM_VERSION,
    camera_signal,
    p4_signal_available,
    parse_raster_metadata,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import (
    BandAsset,
    CalibrationPanel,
    CalibrationRun,
    Capture,
    ROIGeometryType,
)
from spectral_api.routes.datasets import get_owned_dataset
from spectral_api.routes.rois import _capture_assets, _preview_url, get_owned_capture
from spectral_api.schemas.calibration import CalibrationPanelCreate, CalibrationRunCreate
from spectral_api.services.roi_geometry import InvalidGeometryError, canonicalize_geometry

router = APIRouter(tags=["calibration"])
Session = Annotated[AsyncSession, Depends(get_session)]


def _resources(request: Request) -> Any:
    resources = getattr(request.app.state, "resources", None)
    if resources is None:
        raise RuntimeError("Application resources are not initialized")
    return resources


def _csv_values(text: str) -> dict[str, dict[str, float]]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows or len(rows) > 10_000:
        raise ValueError("CSV must contain 1 to 10,000 data rows")
    values: dict[str, dict[str, float]] = {}
    for row in rows:
        normalized = {
            str(key or "").strip().lower(): str(value or "").strip() for key, value in row.items()
        }
        surface = (
            normalized.get("surface")
            or normalized.get("superficie")
            or normalized.get("superfície")
        )
        if not surface:
            raise ValueError("CSV requires a surface/superficie column")
        if "band" in normalized or "banda" in normalized:
            band = (normalized.get("band") or normalized.get("banda") or "").upper()
            raw = (
                normalized.get("reflectance")
                or normalized.get("reflectancia")
                or normalized.get("reflectância")
            )
            entries = [(band, raw)]
        else:
            entries = [
                (key.upper(), value)
                for key, value in normalized.items()
                if key not in {"surface", "superficie", "superfície"} and value
            ]
        for band, raw in entries:
            if not band or raw is None:
                continue
            try:
                value = float(raw.replace(",", "."))
            except ValueError as error:
                raise ValueError(f"Invalid reflectance for {surface}/{band}") from error
            if not 0 <= value <= 1:
                raise ValueError(f"Reflectance outside [0,1] for {surface}/{band}")
            if band in values.setdefault(surface, {}):
                raise ValueError(f"Duplicate reference for {surface}/{band}")
            values[surface][band] = value
    if not values:
        raise ValueError("CSV has no reference values")
    return values


def _panel_response(panel: CalibrationPanel) -> dict[str, Any]:
    return {
        "id": str(panel.id),
        "name": panel.name,
        "manufacturer": panel.manufacturer,
        "model": panel.model,
        "serial_number": panel.serial_number,
        "calibrated_at": panel.calibrated_at,
        "notes": panel.notes,
        "values": panel.values_json,
        "source": panel.source_json,
        "certificate_sha256": panel.certificate_sha256,
        "created_at": panel.created_at,
    }


def _run_response(run: CalibrationRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "dataset_id": str(run.dataset_id),
        "capture_id": str(run.capture_id),
        "panel_id": str(run.panel_id),
        "state": run.state,
        "method": run.method,
        "input_mode": run.input_mode,
        "config": run.config_json,
        "manifest": run.manifest_json,
        "result": run.result_json,
        "error": run.error,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "download_available": run.state in {"completed", "cancelled"} and bool(run.output_key),
    }


async def _owned_panel(session: AsyncSession, panel_id: UUID, owner_id: UUID) -> CalibrationPanel:
    panel = await session.scalar(
        select(CalibrationPanel).where(
            CalibrationPanel.id == panel_id, CalibrationPanel.owner_id == owner_id
        )
    )
    if panel is None:
        raise HTTPException(status_code=404, detail="Calibration panel not found")
    return panel


async def _owned_run(session: AsyncSession, run_id: UUID, owner_id: UUID) -> CalibrationRun:
    run = await session.scalar(
        select(CalibrationRun).where(
            CalibrationRun.id == run_id, CalibrationRun.owner_id == owner_id
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Calibration run not found")
    return run


@router.get("/calibration/panels")
async def list_panels(session: Session, user: CurrentUser) -> list[dict[str, Any]]:
    panels = await session.scalars(
        select(CalibrationPanel)
        .where(CalibrationPanel.owner_id == user.id)
        .order_by(CalibrationPanel.created_at.desc())
    )
    return [_panel_response(panel) for panel in panels]


@router.post("/calibration/panels", status_code=status.HTTP_201_CREATED)
async def create_panel(
    payload: CalibrationPanelCreate, request: Request, session: Session, user: CurrentUser
) -> dict[str, Any]:
    values = payload.values
    certificate_key = None
    certificate_sha256 = None
    if payload.certificate_csv:
        try:
            values = _csv_values(payload.certificate_csv)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        certificate_bytes = payload.certificate_csv.encode("utf-8")
        certificate_sha256 = hashlib.sha256(certificate_bytes).hexdigest()
        certificate_key = f"calibration/panels/{user.id}/{uuid4()}/certificate.csv"
        await run_in_threadpool(
            _resources(request).s3.put_object,
            Bucket=get_settings().s3_bucket,
            Key=certificate_key,
            Body=certificate_bytes,
            ContentType="text/csv; charset=utf-8",
        )
    if not values or any(not bands for bands in values.values()):
        raise HTTPException(status_code=422, detail="Panel requires reference values")
    panel = CalibrationPanel(
        owner_id=user.id,
        name=payload.name,
        manufacturer=payload.manufacturer,
        model=payload.model,
        serial_number=payload.serial_number,
        calibrated_at=payload.calibrated_at,
        certificate_key=certificate_key,
        certificate_sha256=certificate_sha256,
        values_json=values,
        source_json={
            "kind": "certificate_csv" if certificate_key else "manual",
            "label": payload.source_label,
            "certified": payload.certified,
        },
        notes=payload.notes,
    )
    session.add(panel)
    await session.commit()
    await session.refresh(panel)
    return _panel_response(panel)


def _inspect_asset(request: Request, asset: BandAsset) -> dict[str, Any]:
    cached = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    if isinstance(cached, dict) and isinstance(cached.get("calibration"), dict):
        return cast(dict[str, Any], cached["calibration"])
    if asset.size_bytes is not None and asset.size_bytes > 100_000_000:
        raise ValueError("Metadata inspection of pre-existing files over 100 MB requires re-import")
    params: dict[str, str] = {"Bucket": get_settings().s3_bucket, "Key": asset.object_key}
    if asset.object_version_id:
        params["VersionId"] = asset.object_version_id
    body = _resources(request).s3.get_object(**params)["Body"]
    try:
        data = body.read(100_000_001)
    finally:
        body.close()
    if len(data) > 100_000_000:
        raise ValueError("Metadata inspection exceeds 100 MB")
    with rasterio.MemoryFile(data) as memory, memory.open() as source:
        return parse_raster_metadata(
            source.tags(),
            source.tags(ns="EXIF"),
            source.tags(ns="xml:XMP").get("xml:XMP"),
            dtype=source.dtypes[0],
            count=source.count,
        )


async def _bands(request: Request, session: AsyncSession, capture: Capture) -> list[dict[str, Any]]:
    assets = await _capture_assets(session, capture)
    result = []
    for asset in assets:
        try:
            metadata = await run_in_threadpool(_inspect_asset, request, asset)
        except (ValueError, rasterio.errors.RasterioError) as error:
            metadata = {"inspection_error": str(error), "band": asset.band_code}
        result.append(
            {
                "id": str(asset.id),
                "band": asset.band_code or metadata.get("band"),
                "filename": asset.original_filename,
                "width": asset.width,
                "height": asset.height,
                "count": asset.band_count,
                "dtype": asset.dtype,
                "sha256": asset.sha256,
                "metadata": metadata,
                "preview_url": await _preview_url(request, asset),
                "p4_signal_available": p4_signal_available(metadata),
                "object_key": asset.object_key,
                "object_version_id": asset.object_version_id,
            }
        )
    return result


@router.get("/calibration/captures/{capture_id}/bands")
async def capture_bands(
    capture_id: UUID, request: Request, session: Session, user: CurrentUser
) -> list[dict[str, Any]]:
    capture = await get_owned_capture(session, capture_id, user.id)
    bands = await _bands(request, session, capture)
    return [
        {
            key: value
            for key, value in band.items()
            if key not in {"object_key", "object_version_id"}
        }
        for band in bands
    ]


def _read_pixel(
    request: Request, asset: BandAsset, x: int, y: int, metadata: dict[str, Any]
) -> dict[str, Any]:
    if asset.size_bytes is not None and asset.size_bytes > 100_000_000:
        raise ValueError("Direct pixel inspection is limited to 100 MB rasters")
    params: dict[str, str] = {"Bucket": get_settings().s3_bucket, "Key": asset.object_key}
    if asset.object_version_id:
        params["VersionId"] = asset.object_version_id
    body = _resources(request).s3.get_object(**params)["Body"]
    try:
        data = body.read(100_000_001)
    finally:
        body.close()
    if len(data) > 100_000_000:
        raise ValueError("Direct pixel inspection is limited to 100 MB rasters")
    with rasterio.MemoryFile(data) as memory, memory.open() as source:
        if not 0 <= x < source.width or not 0 <= y < source.height:
            raise ValueError("Pixel is outside the raster")
        pixel = source.read(1, window=Window(x, y, 1, 1), masked=True)[0, 0]
        if np.ma.is_masked(pixel):
            raise ValueError("Pixel is masked or nodata")
        dn = float(pixel)
        signal = None
        if p4_signal_available(metadata):
            signal = float(camera_signal(np.asarray([[dn]]), metadata, x0=x, y0=y)[0, 0])
        return {"x": x, "y": y, "dn": dn, "camera_signal": signal, "input_quantity": "DN"}


@router.get("/calibration/captures/{capture_id}/pixels/{band_code}")
async def capture_pixel(
    capture_id: UUID,
    band_code: str,
    x: int,
    y: int,
    request: Request,
    session: Session,
    user: CurrentUser,
) -> dict[str, Any]:
    capture = await get_owned_capture(session, capture_id, user.id)
    assets = await _capture_assets(session, capture)
    asset = next((value for value in assets if value.band_code == band_code.upper()), None)
    if asset is None:
        raise HTTPException(status_code=404, detail="Band not found")
    metadata = await run_in_threadpool(_inspect_asset, request, asset)
    try:
        return await run_in_threadpool(_read_pixel, request, asset, x, y, metadata)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/datasets/{dataset_id}/calibration-runs")
async def list_runs(dataset_id: UUID, session: Session, user: CurrentUser) -> list[dict[str, Any]]:
    await get_owned_dataset(session, dataset_id, user.id)
    runs = await session.scalars(
        select(CalibrationRun)
        .where(CalibrationRun.dataset_id == dataset_id, CalibrationRun.owner_id == user.id)
        .order_by(CalibrationRun.created_at.desc())
    )
    return [_run_response(run) for run in runs]


@router.post("/calibration/runs", status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: CalibrationRunCreate, request: Request, session: Session, user: CurrentUser
) -> dict[str, Any]:
    dataset = await get_owned_dataset(session, payload.dataset_id, user.id)
    capture = await get_owned_capture(session, payload.capture_id, user.id)
    if capture.dataset_id != dataset.id:
        raise HTTPException(status_code=422, detail="Capture does not belong to dataset")
    panel = await _owned_panel(session, payload.panel_id, user.id)
    bands = await _bands(request, session, capture)
    if not bands:
        raise HTTPException(status_code=422, detail="Capture has no accepted scientific bands")
    if payload.selected_band:
        bands = [
            band for band in bands if str(band["band"]).upper() == payload.selected_band.upper()
        ]
        if not bands:
            raise HTTPException(status_code=422, detail="Selected band is not part of capture")
    if payload.reference_band.upper() not in {str(band["band"]).upper() for band in bands}:
        raise HTTPException(status_code=422, detail="Reference band is not part of capture")
    capture_uuids = {
        str(band["metadata"].get("capture_uuid"))
        for band in bands
        if band["metadata"].get("capture_uuid")
    }
    if len(capture_uuids) > 1:
        raise HTTPException(status_code=422, detail="Bands have different capture UUIDs")
    if any(
        band["metadata"].get("band") and band["metadata"]["band"] != band["band"] for band in bands
    ):
        raise HTTPException(
            status_code=422, detail="Filename band order conflicts with camera metadata"
        )
    for band in bands:
        code = str(band["band"]).upper()
        if code == payload.reference_band.upper():
            continue
        referenced_surfaces = {
            surface
            for surface, values in panel.values_json.items()
            if isinstance(values, dict) and code in values
        }
        propagated = any(
            roi.band is None
            and roi.surface in referenced_surfaces
            and not any(
                other.surface == roi.surface and other.band == code for other in payload.rois
            )
            for roi in payload.rois
        )
        if propagated:
            metadata = band["metadata"]
            speeds = metadata.get("flight_speed_mps")
            if (
                metadata.get("relative_optical_center_px") is None
                or not isinstance(speeds, list)
                or any(value is None for value in speeds)
                or math.sqrt(sum(float(value) ** 2 for value in speeds)) > 0.2
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{code}: automatic ROI propagation requires documented optical "
                        "offsets and a near-stationary capture; draw band-specific ROIs"
                    ),
                )
    for roi in payload.rois:
        try:
            canonicalize_geometry(ROIGeometryType.POLYGON, roi.coordinates)
        except InvalidGeometryError as error:
            raise HTTPException(
                status_code=422, detail=f"Invalid ROI {roi.surface}: {error}"
            ) from error
    if payload.input_mode == "p4_signal" and any(not band["p4_signal_available"] for band in bands):
        raise HTTPException(
            status_code=422, detail="Documented DJI camera-signal metadata is incomplete"
        )
    targets: list[dict[str, Any]] = []
    additional_references: list[dict[str, Any]] = []
    required = {"BLUE", "GREEN", "RED", "RED_EDGE", "NIR"}
    if (payload.target_capture_ids or payload.additional_references) and {
        str(band["band"]).upper() for band in bands
    } != required:
        raise HTTPException(status_code=422, detail="The panel capture requires all five P4M bands")
    for reference in payload.additional_references:
        other = await get_owned_capture(session, reference.capture_id, user.id)
        if other.dataset_id != dataset.id:
            raise HTTPException(
                status_code=422, detail="Reference capture does not belong to dataset"
            )
        other_bands = await _bands(request, session, other)
        if len(other_bands) != 5 or {str(band["band"]).upper() for band in other_bands} != required:
            raise HTTPException(
                status_code=422, detail=f"{other.capture_key}: reference requires five P4M bands"
            )
        if any(not band["p4_signal_available"] for band in other_bands):
            raise HTTPException(
                status_code=422, detail=f"{other.capture_key}: DJI correction metadata incomplete"
            )
        other_uuids = {
            str(band["metadata"].get("capture_uuid"))
            for band in other_bands
            if band["metadata"].get("capture_uuid")
        }
        if len(other_uuids) > 1 or any(
            band["metadata"].get("band") and band["metadata"]["band"] != band["band"]
            for band in other_bands
        ):
            raise HTTPException(
                status_code=422, detail=f"{other.capture_key}: inconsistent band metadata"
            )
        for roi in reference.rois:
            try:
                canonicalize_geometry(ROIGeometryType.POLYGON, roi.coordinates)
            except InvalidGeometryError as error:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid ROI in {other.capture_key}/{roi.surface}: {error}",
                ) from error
        for band in other_bands:
            code = str(band["band"]).upper()
            fitting = {
                roi.surface
                for roi in reference.rois
                if roi.role == "fit" and roi.band in {None, code}
            }
            available = {
                surface
                for surface, values in panel.values_json.items()
                if isinstance(values, dict) and code in values
            }
            if len(fitting & available) < 1:
                raise HTTPException(
                    status_code=422,
                    detail=f"{other.capture_key}/{code}: missing panel ROI or reflectance",
                )
            if code != payload.reference_band.upper() and any(
                roi.band is None
                and roi.surface in available
                and not any(
                    item.surface == roi.surface and item.band == code for item in reference.rois
                )
                for roi in reference.rois
            ):
                metadata = band["metadata"]
                speeds = metadata.get("flight_speed_mps")
                if (
                    metadata.get("relative_optical_center_px") is None
                    or not isinstance(speeds, list)
                    or any(value is None for value in speeds)
                    or math.sqrt(sum(float(value) ** 2 for value in speeds)) > 0.2
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"{other.capture_key}/{code}: draw band-specific ROIs; "
                            "propagation is unsafe"
                        ),
                    )
        additional_references.append(
            {
                "capture_id": str(other.id),
                "capture_key": other.capture_key,
                "rois": [roi.model_dump(mode="json") for roi in reference.rois],
                "bands": [
                    {
                        key: band[key]
                        for key in (
                            "id",
                            "band",
                            "filename",
                            "sha256",
                            "object_key",
                            "object_version_id",
                            "metadata",
                            "width",
                            "height",
                            "dtype",
                            "count",
                        )
                    }
                    for band in other_bands
                ],
            }
        )
    if payload.target_capture_ids:
        for target_id in payload.target_capture_ids:
            target = await get_owned_capture(session, target_id, user.id)
            if target.dataset_id != dataset.id:
                raise HTTPException(
                    status_code=422, detail="Plant capture does not belong to dataset"
                )
            target_bands = await _bands(request, session, target)
            target_uuids = {
                str(band["metadata"].get("capture_uuid"))
                for band in target_bands
                if band["metadata"].get("capture_uuid")
            }
            targets.append(
                {
                    "capture_id": str(target.id),
                    "capture_key": target.capture_key,
                    "reference_capture_id": str(
                        payload.target_reference_map.get(target.id, payload.capture_id)
                    )
                    if payload.reference_combination == "per_target"
                    else "combined",
                    "reference_capture_ids": (
                        [
                            str(payload.capture_id),
                            *[str(item.capture_id) for item in payload.additional_references],
                        ]
                        if payload.reference_combination == "mean_coefficients"
                        else []
                    ),
                    "preflight_errors": (
                        (
                            ["Missing or duplicate P4M bands"]
                            if len(target_bands) != 5
                            or {str(band["band"]).upper() for band in target_bands} != required
                            else []
                        )
                        + (
                            ["DJI correction metadata incomplete"]
                            if any(not band["p4_signal_available"] for band in target_bands)
                            else []
                        )
                        + (["Capture UUID mismatch"] if len(target_uuids) > 1 else [])
                    ),
                    "bands": [
                        {
                            key: band[key]
                            for key in (
                                "id",
                                "band",
                                "filename",
                                "sha256",
                                "object_key",
                                "object_version_id",
                                "metadata",
                                "width",
                                "height",
                                "dtype",
                                "count",
                            )
                        }
                        for band in target_bands
                    ],
                }
            )
    for band in bands:
        code = str(band["band"] or "").upper()
        fitting = {
            roi.surface for roi in payload.rois if roi.role == "fit" and roi.band in {None, code}
        }
        available = {
            surface
            for surface, values in panel.values_json.items()
            if isinstance(values, dict) and code in values
        }
        if len(fitting & available) < (1 if payload.method == "proportional" else 2):
            raise HTTPException(status_code=422, detail=f"Insufficient references for {code}")
    config = payload.model_dump(mode="json", exclude={"execute"})
    config["panel_snapshot"] = jsonable_encoder(_panel_response(panel))
    config["algorithm_version"] = ALGORITHM_VERSION
    manifest = {
        "bands": [
            {
                key: band[key]
                for key in (
                    "id",
                    "band",
                    "filename",
                    "sha256",
                    "object_key",
                    "object_version_id",
                    "metadata",
                    "width",
                    "height",
                    "dtype",
                    "count",
                )
            }
            for band in bands
        ],
        "targets": targets,
        "panel_capture": {"capture_id": str(capture.id), "capture_key": capture.capture_key},
        "additional_references": additional_references,
    }
    run = CalibrationRun(
        owner_id=user.id,
        project_id=dataset.project_id,
        dataset_id=dataset.id,
        capture_id=capture.id,
        panel_id=panel.id,
        state="queued" if payload.execute else "draft",
        method=payload.method,
        input_mode=payload.input_mode,
        config_json=config,
        manifest_json=manifest,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    if payload.execute:
        try:
            await run_in_threadpool(
                _resources(request).celery.send_task, "spectral.calibration.run", args=[str(run.id)]
            )
        except Exception as error:
            run.state = "failed"
            run.error = f"Could not queue calibration: {type(error).__name__}"
            await session.commit()
            raise HTTPException(status_code=503, detail="Calibration worker unavailable") from error
    return _run_response(run)


@router.get("/calibration/runs/{run_id}")
async def get_run(
    run_id: UUID, request: Request, session: Session, user: CurrentUser
) -> dict[str, Any]:
    run = await _owned_run(session, run_id, user.id)
    response = _run_response(run)
    if run.state in {"completed", "cancelled"} and isinstance(run.result_json, dict):
        preview_keys = run.result_json.get("preview_keys", {})
        preview_urls: dict[str, dict[str, str]] = {}
        if isinstance(preview_keys, dict):
            for code, kinds in preview_keys.items():
                if not isinstance(kinds, dict):
                    continue
                preview_urls[str(code)] = {}
                for kind, key in kinds.items():
                    if kind not in {"original", "calibrated"} or not isinstance(key, str):
                        continue
                    preview_urls[str(code)][kind] = await run_in_threadpool(
                        _resources(request).s3_presign.generate_presigned_url,
                        "get_object",
                        Params={"Bucket": get_settings().s3_bucket, "Key": key},
                        ExpiresIn=get_settings().upload_url_expiry_seconds,
                    )
        if isinstance(response["result"], dict):
            capture_preview_urls: dict[str, dict[str, dict[str, str]]] = {}
            for capture_id, band_previews in run.result_json.get(
                "capture_preview_keys", {}
            ).items():
                if not isinstance(band_previews, dict):
                    continue
                capture_preview_urls[capture_id] = {}
                for code, kinds in band_previews.items():
                    if not isinstance(kinds, dict):
                        continue
                    capture_preview_urls[capture_id][code] = {}
                    for kind, key in kinds.items():
                        if kind not in {"original", "calibrated"} or not isinstance(key, str):
                            continue
                        capture_preview_urls[capture_id][code][kind] = await run_in_threadpool(
                            _resources(request).s3_presign.generate_presigned_url,
                            "get_object",
                            Params={"Bucket": get_settings().s3_bucket, "Key": key},
                            ExpiresIn=get_settings().upload_url_expiry_seconds,
                        )
            response["result"] = {
                **response["result"],
                "preview_urls": preview_urls,
                "capture_preview_urls": capture_preview_urls,
            }
    return response


@router.post("/calibration/runs/{run_id}/execute")
async def execute_draft(
    run_id: UUID, request: Request, session: Session, user: CurrentUser
) -> dict[str, Any]:
    run = await _owned_run(session, run_id, user.id)
    if run.state != "draft":
        raise HTTPException(status_code=409, detail="Only saved drafts can be executed")
    run.state = "queued"
    run.updated_at = datetime.now(UTC)
    await session.commit()
    await run_in_threadpool(
        _resources(request).celery.send_task, "spectral.calibration.run", args=[str(run.id)]
    )
    return _run_response(run)


@router.post("/calibration/runs/{run_id}/cancel")
async def cancel_run(run_id: UUID, session: Session, user: CurrentUser) -> dict[str, Any]:
    run = await _owned_run(session, run_id, user.id)
    if run.state not in {"draft", "queued", "running"}:
        raise HTTPException(status_code=409, detail="Only active calibration can be cancelled")
    run.state = "cancel_requested" if run.state == "running" else "cancelled"
    run.updated_at = datetime.now(UTC)
    await session.commit()
    return _run_response(run)


@router.post("/calibration/runs/{run_id}/retry-failed", status_code=status.HTTP_201_CREATED)
async def retry_failed(
    run_id: UUID, request: Request, session: Session, user: CurrentUser
) -> dict[str, Any]:
    run = await _owned_run(session, run_id, user.id)
    if run.state not in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Wait for the current calibration to finish")
    manifest = run.manifest_json if isinstance(run.manifest_json, dict) else {}
    targets = manifest.get("targets") or []
    if not targets:
        raise HTTPException(status_code=409, detail="This session has no plant captures")
    result = run.result_json if isinstance(run.result_json, dict) else {}
    completed = {
        item["capture_id"] for item in result.get("targets", []) if item.get("state") == "completed"
    }
    remaining = [item["capture_id"] for item in targets if item["capture_id"] not in completed]
    if not remaining:
        raise HTTPException(status_code=409, detail="All plant captures completed")
    config = dict(run.config_json)
    config["target_capture_ids"] = remaining
    config["target_reference_map"] = {
        capture_id: reference_id
        for capture_id, reference_id in config.get("target_reference_map", {}).items()
        if capture_id in {str(item) for item in remaining}
    }
    config["execute"] = True
    return await create_run(CalibrationRunCreate.model_validate(config), request, session, user)


@router.get("/calibration/runs/{run_id}/download")
async def download_run(
    run_id: UUID, request: Request, session: Session, user: CurrentUser
) -> dict[str, str]:
    run = await _owned_run(session, run_id, user.id)
    if run.state not in {"completed", "cancelled"} or not run.output_key:
        raise HTTPException(status_code=409, detail="Calibration export is not ready")
    url = await run_in_threadpool(
        _resources(request).s3_presign.generate_presigned_url,
        "get_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": run.output_key},
        ExpiresIn=get_settings().upload_url_expiry_seconds,
    )
    return {"url": url}


@router.get("/calibration/runs/{run_id}/download-file")
async def download_run_file(
    run_id: UUID, request: Request, session: Session, user: CurrentUser
) -> StreamingResponse:
    run = await _owned_run(session, run_id, user.id)
    if run.state not in {"completed", "cancelled"} or not run.output_key:
        raise HTTPException(status_code=409, detail="Calibration export is not ready")
    params = {"Bucket": get_settings().s3_bucket, "Key": run.output_key}
    response = await run_in_threadpool(_resources(request).s3.get_object, **params)
    body = response["Body"]
    return StreamingResponse(
        body,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="calibracao-{run_id}.zip"'},
    )
