"""Build canonical immutable provenance snapshots for scientific jobs."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from spectral_common import SNAPSHOT_SCHEMA_VERSION, SOFTWARE_NAME, SOFTWARE_VERSION
from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.domain.models import (
    ROI,
    AssetRole,
    BandAsset,
    BandAssetState,
    Capture,
    Dataset,
    ProcessingRecipe,
    Replicate,
    ROIVersion,
    Treatment,
)


@dataclass(frozen=True, slots=True)
class CaptureProvenance:
    """Exact immutable inputs selected for each index of one capture."""

    roi_id: UUID | None
    roi_version_id: UUID | None
    roi_snapshot: dict[str, Any]
    inputs_by_index: dict[str, list[dict[str, Any]]]
    mappings_by_index: dict[str, dict[str, Any]]


def canonical_checksum(value: dict[str, Any]) -> str:
    """Hash canonical JSON used for identity and tamper detection."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _asset_snapshot(asset: BandAsset) -> dict[str, Any]:
    return {
        "asset_id": str(asset.id),
        "band": (asset.band_code or "").upper() or None,
        "filename": asset.original_filename,
        "object_key": asset.object_key,
        "object_version_id": asset.object_version_id,
        "checksum_sha256": asset.sha256,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "dtype": asset.dtype,
        "nodata": asset.nodata,
        "crs": asset.crs,
        "transform": asset.transform,
    }


def _roi_snapshot(roi: ROI, version: ROIVersion) -> dict[str, Any]:
    return {
        "roi_id": str(roi.id),
        "roi_version_id": str(version.id),
        "name": roi.name,
        "geometry_type": version.geometry_type.value,
        "normalized_coordinates": version.normalized_coordinates,
        "pixel_coordinates": version.pixel_coordinates,
        "reference_width": version.reference_width,
        "reference_height": version.reference_height,
        "reference_asset_id": str(version.reference_asset_id),
        "geometry_checksum": version.geometry_checksum,
        "area_pixels": version.area_pixels,
        "approximate_valid_pixels": version.approximate_valid_pixels,
        "crs": version.crs,
        "transform": version.transform,
        "spatial_coordinates": version.spatial_coordinates,
    }


async def build_reproducibility_snapshot(
    session: AsyncSession,
    dataset: Dataset,
    captures: list[Capture],
    index_definitions: list[dict[str, Any]],
    statistics: list[str],
    recipe: ProcessingRecipe | None,
) -> tuple[dict[str, Any], dict[UUID, CaptureProvenance]]:
    """Freeze all scientific inputs while associations and current ROI are still mutable."""
    capture_ids = [capture.id for capture in captures]
    assets = list(
        await session.scalars(
            select(BandAsset).where(
                BandAsset.capture_id.in_(capture_ids),
                BandAsset.owner_id == dataset.owner_id,
                BandAsset.role == AssetRole.SCIENTIFIC,
                BandAsset.state == BandAssetState.ACCEPTED,
            )
        )
    )
    assets_by_capture: dict[UUID, list[BandAsset]] = defaultdict(list)
    for asset in assets:
        if asset.capture_id is not None:
            assets_by_capture[asset.capture_id].append(asset)

    roi_rows = (
        await session.execute(
            select(ROI, ROIVersion)
            .join(ROIVersion, ROIVersion.id == ROI.current_version_id)
            .where(
                ROI.capture_id.in_(capture_ids),
                ROI.owner_id == dataset.owner_id,
                ROI.state == "active",
            )
            .order_by(ROI.capture_id, ROI.updated_at.desc())
        )
    ).all()
    roi_by_capture: dict[UUID, tuple[ROI, ROIVersion]] = {}
    for roi, version in roi_rows:
        if roi.capture_id is not None and roi.capture_id not in roi_by_capture:
            roi_by_capture[roi.capture_id] = (roi, version)

    replicate_ids = [capture.replicate_id for capture in captures if capture.replicate_id]
    replicates = list(
        await session.scalars(
            select(Replicate).where(Replicate.id.in_(replicate_ids))
            if replicate_ids
            else select(Replicate).where(false())
        )
    )
    replicate_by_id = {replicate.id: replicate for replicate in replicates}
    treatment_ids: set[UUID] = set()
    for capture in captures:
        if capture.treatment_id is not None:
            treatment_ids.add(capture.treatment_id)
        replicate = replicate_by_id.get(capture.replicate_id) if capture.replicate_id else None
        if replicate is not None and replicate.treatment_id is not None:
            treatment_ids.add(replicate.treatment_id)
    treatments = list(
        await session.scalars(
            select(Treatment).where(Treatment.id.in_(treatment_ids))
            if treatment_ids
            else select(Treatment).where(false())
        )
    )
    treatment_by_id = {treatment.id: treatment for treatment in treatments}

    contexts: dict[UUID, CaptureProvenance] = {}
    capture_snapshots: list[dict[str, Any]] = []
    for capture in captures:
        replicate = replicate_by_id.get(capture.replicate_id) if capture.replicate_id else None
        treatment_id = capture.treatment_id or (replicate.treatment_id if replicate else None)
        treatment = treatment_by_id.get(treatment_id) if treatment_id else None
        roi_pair = roi_by_capture.get(capture.id)
        frozen_roi = _roi_snapshot(*roi_pair) if roi_pair else {}
        inputs_by_index: dict[str, list[dict[str, Any]]] = {}
        mappings_by_index: dict[str, dict[str, Any]] = {}
        all_used_assets: dict[str, dict[str, Any]] = {}
        for definition in index_definitions:
            index_name = str(definition["index"])
            required = {str(value) for value in definition["required_bands"]}
            selected = [
                _asset_snapshot(asset)
                for asset in assets_by_capture[capture.id]
                if (asset.band_code or "").upper() in required
            ]
            inputs_by_index[index_name] = selected
            mappings_by_index[index_name] = {
                "required_bands": sorted(required),
                "assets": [
                    {"band": asset["band"], "asset_id": asset["asset_id"]} for asset in selected
                ],
            }
            for selected_asset in selected:
                all_used_assets[str(selected_asset["asset_id"])] = selected_asset
        contexts[capture.id] = CaptureProvenance(
            roi_id=roi_pair[0].id if roi_pair else None,
            roi_version_id=roi_pair[1].id if roi_pair else None,
            roi_snapshot=frozen_roi,
            inputs_by_index=inputs_by_index,
            mappings_by_index=mappings_by_index,
        )
        capture_snapshots.append(
            {
                "capture_id": str(capture.id),
                "capture": capture.capture_key,
                "repetition": replicate.label if replicate else None,
                "treatment": treatment.name if treatment else None,
                "roi": frozen_roi or None,
                "band_mappings": [
                    {
                        "band": asset["band"],
                        "asset_id": asset["asset_id"],
                        "filename": asset["filename"],
                        "checksum_sha256": asset["checksum_sha256"],
                        "object_version_id": asset["object_version_id"],
                    }
                    for asset in all_used_assets.values()
                ],
                "input_assets": list(all_used_assets.values()),
            }
        )

    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "software": SOFTWARE_NAME,
        "software_version": SOFTWARE_VERSION,
        "dataset": {
            "id": str(dataset.id),
            "name": dataset.name,
            "radiometry_mode": dataset.radiometry_mode.value,
        },
        "recipe": (
            {
                "id": str(recipe.id),
                "name": recipe.name,
                "definition_checksum": recipe.definition_checksum,
            }
            if recipe
            else None
        ),
        "indices": index_definitions,
        "statistics": statistics,
        "captures": capture_snapshots,
    }
    return snapshot, contexts
