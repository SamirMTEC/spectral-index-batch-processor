"""Owner-scoped capture viewer and immutable ROI version endpoints."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from spectral_common import get_settings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import (
    ROI,
    AlignmentStatus,
    AssetRole,
    BandAsset,
    BandAssetState,
    Capture,
    Replicate,
    ROIState,
    ROIVersion,
)
from spectral_api.routes.datasets import get_owned_dataset
from spectral_api.schemas.rois import (
    CaptureBand,
    CaptureDetail,
    CaptureSummary,
    ROIGeometryInput,
    ROIResponse,
    ROIVersionResponse,
)
from spectral_api.services.roi_geometry import (
    InvalidGeometryError,
    canonicalize_geometry,
    geometry_checksum,
    normalized_to_pixels,
    pixels_to_spatial,
    polygon_area,
)

router = APIRouter(tags=["rois"])
Session = Annotated[AsyncSession, Depends(get_session)]
CANONICAL_BANDS = ("BLUE", "GREEN", "RED", "RED_EDGE", "NIR")


async def get_owned_capture(session: AsyncSession, capture_id: UUID, owner_id: UUID) -> Capture:
    capture = await session.scalar(
        select(Capture).where(Capture.id == capture_id, Capture.owner_id == owner_id)
    )
    if capture is None:
        raise HTTPException(status_code=404, detail="Capture not found")
    return capture


async def _capture_assets(session: AsyncSession, capture: Capture) -> list[BandAsset]:
    return list(
        await session.scalars(
            select(BandAsset)
            .where(
                BandAsset.capture_id == capture.id,
                BandAsset.owner_id == capture.owner_id,
                BandAsset.role == AssetRole.SCIENTIFIC,
                BandAsset.state == BandAssetState.ACCEPTED,
                BandAsset.band_code.is_not(None),
            )
            .order_by(BandAsset.band_code, BandAsset.created_at)
        )
    )


def _grid_error(reference: BandAsset, candidate: BandAsset, capture: Capture) -> str | None:
    if capture.alignment_status in {
        AlignmentStatus.INCOMPATIBLE,
        AlignmentStatus.REQUIRES_TRANSFORM,
    }:
        return "A captura exige uma transformação espacial explícita"
    if not reference.width or not reference.height or not candidate.width or not candidate.height:
        return "Dimensões raster ausentes"
    if (reference.width, reference.height) != (candidate.width, candidate.height):
        return "Dimensões incompatíveis com a banda de referência"
    if reference.crs != candidate.crs:
        return "CRS incompatível com a banda de referência"
    if (reference.transform is None) != (candidate.transform is None):
        return "Transformação espacial ausente em uma das bandas"
    if (
        reference.transform is not None
        and candidate.transform is not None
        and (
            len(reference.transform) != len(candidate.transform)
            or any(
                not math.isclose(float(left), float(right), rel_tol=0, abs_tol=1e-9)
                for left, right in zip(reference.transform, candidate.transform, strict=True)
            )
        )
    ):
        return "Transformação espacial incompatível com a banda de referência"
    return None


def _preferred_reference(assets: list[BandAsset]) -> BandAsset | None:
    order = {code: index for index, code in enumerate(CANONICAL_BANDS)}
    candidates = [asset for asset in assets if (asset.band_code or "").upper() in order]
    return min(candidates, key=lambda asset: order[(asset.band_code or "").upper()], default=None)


async def _preview_url(request: Request, asset: BandAsset) -> str | None:
    if asset.preview_object_key is None:
        return None
    resources = getattr(request.app.state, "resources", None)
    if resources is None:
        raise RuntimeError("Application resources are not initialized")
    params: dict[str, Any] = {
        "Bucket": get_settings().s3_bucket,
        "Key": asset.preview_object_key,
    }
    if asset.preview_object_version_id:
        params["VersionId"] = asset.preview_object_version_id
    return await run_in_threadpool(
        resources.s3_presign.generate_presigned_url,
        "get_object",
        Params=params,
        ExpiresIn=get_settings().upload_url_expiry_seconds,
    )


async def _detail(request: Request, session: AsyncSession, capture: Capture) -> CaptureDetail:
    replicate_label = None
    if capture.replicate_id:
        replicate_label = await session.scalar(
            select(Replicate.label).where(
                Replicate.id == capture.replicate_id,
                Replicate.owner_id == capture.owner_id,
            )
        )
    assets = await _capture_assets(session, capture)
    reference = _preferred_reference(assets)
    counts: dict[str, int] = {}
    for asset in assets:
        code = (asset.band_code or "").upper()
        counts[code] = counts.get(code, 0) + 1
    bands: list[CaptureBand] = []
    for asset in assets:
        if asset.width is None or asset.height is None:
            continue
        code = (asset.band_code or "").upper()
        error = None if reference is None else _grid_error(reference, asset, capture)
        if counts.get(code, 0) > 1:
            error = "Mais de um arquivo está associado a esta banda"
        bands.append(
            CaptureBand(
                id=asset.id,
                band_code=code,
                filename=asset.original_filename,
                width=asset.width,
                height=asset.height,
                crs=asset.crs,
                transform=asset.transform,
                preview_url=await _preview_url(request, asset),
                approximate_valid_fraction=asset.approximate_valid_fraction,
                compatible=error is None,
                compatibility_error=error,
            )
        )
    return CaptureDetail(
        id=capture.id,
        dataset_id=capture.dataset_id,
        replicate_id=capture.replicate_id,
        replicate_label=replicate_label,
        capture_key=capture.capture_key,
        alignment_status=capture.alignment_status,
        bands=bands,
        created_at=capture.created_at,
        updated_at=capture.updated_at,
    )


@router.get("/datasets/{dataset_id}/captures", response_model=list[CaptureSummary])
async def list_captures(
    dataset_id: UUID, session: Session, user: CurrentUser
) -> list[CaptureSummary]:
    await get_owned_dataset(session, dataset_id, user.id)
    captures = list(
        await session.scalars(
            select(Capture)
            .where(Capture.dataset_id == dataset_id, Capture.owner_id == user.id)
            .order_by(Capture.created_at)
        )
    )
    capture_ids = [capture.id for capture in captures]
    band_codes_by_capture: dict[UUID, set[str]] = defaultdict(set)
    if capture_ids:
        band_rows = (
            await session.execute(
                select(BandAsset.capture_id, BandAsset.band_code).where(
                    BandAsset.capture_id.in_(capture_ids),
                    BandAsset.state == BandAssetState.ACCEPTED,
                    BandAsset.role == AssetRole.SCIENTIFIC,
                    BandAsset.band_code.is_not(None),
                )
            )
        ).all()
        for capture_id, band_code in band_rows:
            if capture_id is not None and band_code is not None:
                band_codes_by_capture[capture_id].add(str(band_code))
    results: list[CaptureSummary] = []
    for capture in captures:
        replicate_label = None
        if capture.replicate_id:
            replicate_label = await session.scalar(
                select(Replicate.label).where(Replicate.id == capture.replicate_id)
            )
        band_count = int(
            await session.scalar(
                select(func.count(BandAsset.id)).where(
                    BandAsset.capture_id == capture.id,
                    BandAsset.state == BandAssetState.ACCEPTED,
                    BandAsset.role == AssetRole.SCIENTIFIC,
                )
            )
            or 0
        )
        if band_count == 0:
            continue
        band_codes = sorted(band_codes_by_capture[capture.id])
        roi_count = int(
            await session.scalar(
                select(func.count(ROI.id)).where(
                    ROI.capture_id == capture.id,
                    ROI.state == ROIState.ACTIVE,
                    ROI.owner_id == user.id,
                )
            )
            or 0
        )
        results.append(
            CaptureSummary(
                id=capture.id,
                dataset_id=capture.dataset_id,
                replicate_id=capture.replicate_id,
                replicate_label=replicate_label,
                capture_key=capture.capture_key,
                band_count=band_count,
                band_codes=band_codes,
                roi_count=roi_count,
                created_at=capture.created_at,
            )
        )
    return results


@router.get("/captures/{capture_id}", response_model=CaptureDetail)
async def get_capture(
    capture_id: UUID, request: Request, session: Session, user: CurrentUser
) -> CaptureDetail:
    capture = await get_owned_capture(session, capture_id, user.id)
    return await _detail(request, session, capture)


async def _roi_response(session: AsyncSession, roi: ROI) -> ROIResponse:
    if roi.current_version_id is None:
        raise RuntimeError("ROI has no current version")
    version = await session.get(ROIVersion, roi.current_version_id)
    if version is None:
        raise RuntimeError("ROI current version does not exist")
    if roi.capture_id is None:
        raise RuntimeError("Versioned ROI has no capture scope")
    return ROIResponse(
        id=roi.id,
        dataset_id=roi.dataset_id,
        replicate_id=roi.replicate_id,
        capture_id=roi.capture_id,
        name=roi.name,
        description=roi.description,
        state=roi.state,
        current_version=ROIVersionResponse.model_validate(version),
        created_at=roi.created_at,
        updated_at=roi.updated_at,
    )


@router.get("/captures/{capture_id}/rois", response_model=list[ROIResponse])
async def list_rois(capture_id: UUID, session: Session, user: CurrentUser) -> list[ROIResponse]:
    capture = await get_owned_capture(session, capture_id, user.id)
    rois = list(
        await session.scalars(
            select(ROI)
            .where(
                ROI.capture_id == capture.id,
                ROI.owner_id == user.id,
                ROI.state == ROIState.ACTIVE,
                ROI.current_version_id.is_not(None),
            )
            .order_by(ROI.created_at)
        )
    )
    return [await _roi_response(session, roi) for roi in rois]


async def _build_version(
    session: AsyncSession,
    capture: Capture,
    roi_id: UUID,
    version_number: int,
    payload: ROIGeometryInput,
    user_id: UUID,
) -> ROIVersion:
    reference = await session.scalar(
        select(BandAsset).where(
            BandAsset.id == payload.reference_asset_id,
            BandAsset.capture_id == capture.id,
            BandAsset.owner_id == user_id,
            BandAsset.role == AssetRole.SCIENTIFIC,
            BandAsset.state == BandAssetState.ACCEPTED,
        )
    )
    if reference is None:
        raise HTTPException(status_code=422, detail="Reference band is not part of this capture")
    if reference.width is None or reference.height is None:
        raise HTTPException(status_code=409, detail="Reference band has no raster dimensions")
    assets = await _capture_assets(session, capture)
    duplicates: set[str] = set()
    seen: set[str] = set()
    for asset in assets:
        code = (asset.band_code or "").upper()
        if code in seen:
            duplicates.add(code)
        seen.add(code)
    if duplicates:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate band associations must be resolved: {', '.join(sorted(duplicates))}",
        )
    incompatible = [
        f"{asset.band_code}: {error}"
        for asset in assets
        if (error := _grid_error(reference, asset, capture)) is not None
    ]
    if incompatible:
        raise HTTPException(
            status_code=409,
            detail="ROI cannot be propagated safely. " + "; ".join(incompatible),
        )
    try:
        normalized = canonicalize_geometry(payload.geometry_type, payload.normalized_coordinates)
        pixels = normalized_to_pixels(normalized, reference.width, reference.height)
    except InvalidGeometryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    area = polygon_area(pixels)
    valid_pixels = (
        round(area * reference.approximate_valid_fraction)
        if reference.approximate_valid_fraction is not None
        else None
    )
    return ROIVersion(
        id=uuid4(),
        roi_id=roi_id,
        version_number=version_number,
        geometry_type=payload.geometry_type,
        normalized_coordinates=[[x, y] for x, y in normalized],
        pixel_coordinates=[[x, y] for x, y in pixels],
        reference_capture_id=capture.id,
        reference_asset_id=reference.id,
        reference_width=reference.width,
        reference_height=reference.height,
        crs=reference.crs,
        transform=reference.transform,
        spatial_coordinates=(
            [[x, y] for x, y in spatial]
            if reference.crs and (spatial := pixels_to_spatial(pixels, reference.transform))
            else None
        ),
        geometry_checksum=geometry_checksum(
            payload.geometry_type, normalized, reference.width, reference.height
        ),
        area_pixels=area,
        approximate_valid_pixels=valid_pixels,
        created_by=user_id,
        created_at=datetime.now(UTC),
    )


@router.post(
    "/captures/{capture_id}/rois",
    response_model=ROIResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_roi(
    capture_id: UUID,
    payload: ROIGeometryInput,
    session: Session,
    user: CurrentUser,
) -> ROIResponse:
    capture = await get_owned_capture(session, capture_id, user.id)
    roi = ROI(
        id=uuid4(),
        project_id=capture.project_id,
        owner_id=user.id,
        dataset_id=capture.dataset_id,
        replicate_id=capture.replicate_id,
        capture_id=capture.id,
        name=payload.name.strip(),
        description=payload.description,
        geometry_type=payload.geometry_type.value,
        geometry={"normalized_coordinates": payload.normalized_coordinates},
        state=ROIState.ACTIVE,
    )
    session.add(roi)
    await session.flush()
    version = await _build_version(session, capture, roi.id, 1, payload, user.id)
    session.add(version)
    await session.flush()
    roi.current_version_id = version.id
    await session.commit()
    await session.refresh(roi)
    return await _roi_response(session, roi)


@router.post("/rois/{roi_id}/versions", response_model=ROIResponse)
async def create_roi_version(
    roi_id: UUID,
    payload: ROIGeometryInput,
    session: Session,
    user: CurrentUser,
) -> ROIResponse:
    roi = await session.scalar(
        select(ROI)
        .where(ROI.id == roi_id, ROI.owner_id == user.id, ROI.state == ROIState.ACTIVE)
        .with_for_update()
    )
    if roi is None or roi.capture_id is None:
        raise HTTPException(status_code=404, detail="ROI not found")
    if payload.expected_version is None:
        raise HTTPException(status_code=428, detail="expected_version is required")
    current = await session.get(ROIVersion, roi.current_version_id)
    if current is None:
        raise HTTPException(status_code=409, detail="ROI current version is missing")
    if payload.expected_version != current.version_number:
        raise HTTPException(status_code=409, detail="ROI was changed by another editor")
    capture = await get_owned_capture(session, roi.capture_id, user.id)
    version = await _build_version(
        session, capture, roi.id, current.version_number + 1, payload, user.id
    )
    session.add(version)
    await session.flush()
    roi.current_version_id = version.id
    roi.name = payload.name.strip()
    roi.description = payload.description
    roi.geometry_type = payload.geometry_type.value
    roi.geometry = {"normalized_coordinates": payload.normalized_coordinates}
    await session.commit()
    await session.refresh(roi)
    return await _roi_response(session, roi)


@router.delete("/rois/{roi_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_roi(roi_id: UUID, session: Session, user: CurrentUser) -> None:
    roi = await session.scalar(select(ROI).where(ROI.id == roi_id, ROI.owner_id == user.id))
    if roi is None:
        raise HTTPException(status_code=404, detail="ROI not found")
    roi.state = ROIState.ARCHIVED
    await session.commit()
