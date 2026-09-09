"""Overflow-safe spectral index calculations on aligned two-dimensional arrays."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from affine import Affine
from rasterio.crs import CRS
from spectral_common import (
    CURRENT_FORMULA_VERSIONS,
    FORMULA_DEFINITIONS,
    canonicalize_index_name,
    get_formula_definition,
)

from spectral_engine.errors import (
    IncompatibleDimensionsError,
    InvalidParameterError,
    MissingBandError,
    SpatialMetadataMismatchError,
    UnknownIndexError,
)
from spectral_engine.models import (
    BandData,
    BandInput,
    IndexResult,
    IndexStatistics,
    Parameters,
    ROIInput,
    masked_array,
)


@dataclass(frozen=True, slots=True)
class IndexDefinition:
    required_bands: tuple[str, ...]
    parameters: frozenset[str] = frozenset()


INDEX_DEFINITIONS: dict[str, IndexDefinition] = {
    name: IndexDefinition(definition.required_bands, definition.allowed_parameters)
    for (name, version), definition in FORMULA_DEFINITIONS.items()
    if CURRENT_FORMULA_VERSIONS.get(name) == version
}
DEFAULT_SAVI_L = 0.5


@dataclass(frozen=True, slots=True)
class PreparedBand:
    values: npt.NDArray[np.float64]
    invalid: npt.NDArray[np.bool_]
    crs: CRS | None
    transform: Affine | None


def calculate_index(
    index_name: str,
    bands: Mapping[str, BandInput],
    roi: ROIInput = None,
    parameters: Mapping[str, float | int] | None = None,
    *,
    formula_version: str | None = None,
) -> IndexResult:
    """Calculate one index, retaining invalid pixels as NaN plus an explicit mask."""
    canonical_name = canonicalize_index_name(index_name)
    try:
        scientific_definition = get_formula_definition(canonical_name, formula_version)
    except KeyError:
        raise UnknownIndexError(
            f"Unsupported spectral formula: {canonical_name}@{formula_version or 'current'}"
        ) from None
    definition = IndexDefinition(
        scientific_definition.required_bands, scientific_definition.allowed_parameters
    )
    canonical_bands = _canonicalize_bands(bands)
    missing = [name for name in definition.required_bands if name not in canonical_bands]
    if missing:
        raise MissingBandError(f"{canonical_name} requires missing band(s): {', '.join(missing)}")
    normalized_parameters = _parameters(canonical_name, definition, parameters)
    prepared = {name: _prepare_band(canonical_bands[name]) for name in definition.required_bands}
    shape = _validate_shapes(prepared)
    crs, transform = _spatial_metadata(prepared)
    valid = np.ones(shape, dtype=np.bool_)
    for band in prepared.values():
        valid &= ~band.invalid
    if roi is not None:
        roi_mask = np.asarray(roi)
        if roi_mask.shape != shape:
            raise IncompatibleDimensionsError(
                f"ROI shape {roi_mask.shape} does not match band shape {shape}"
            )
        valid &= np.asarray(roi_mask, dtype=np.bool_)

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        calculated, denominator = _formula(
            canonical_name,
            scientific_definition.formula_version,
            prepared,
            normalized_parameters,
        )
    if denominator is not None:
        valid &= denominator != 0
        valid &= np.isfinite(denominator)
    valid &= np.isfinite(calculated)

    with np.errstate(over="ignore", invalid="ignore"):
        converted = calculated.astype(np.float32, copy=False)
    valid &= np.isfinite(converted)
    output = np.full(shape, np.nan, dtype=np.float32)
    output[valid] = converted[valid]
    return IndexResult(
        index_name=canonical_name,
        formula_version=scientific_definition.formula_version,
        values=output,
        valid_mask=valid,
        statistics=calculate_statistics(output, valid),
        width=shape[1],
        height=shape[0],
        crs=crs,
        transform=transform,
        parameters=normalized_parameters,
    )


def calculate_statistics(
    values: npt.NDArray[np.float32], valid_mask: npt.NDArray[np.bool_]
) -> IndexStatistics:
    """Calculate population statistics over exactly the persisted valid float32 values."""
    selected = np.asarray(values[valid_mask], dtype=np.float64)
    if selected.size == 0:
        return IndexStatistics(0, None, None, None, None, None, None, None, None, None)
    percentiles = np.percentile(selected, [5, 25, 75, 95], method="linear")
    return IndexStatistics(
        count=int(selected.size),
        mean=float(np.mean(selected, dtype=np.float64)),
        median=float(np.median(selected)),
        min=float(np.min(selected)),
        max=float(np.max(selected)),
        std=float(np.std(selected, dtype=np.float64, ddof=0)),
        p05=float(percentiles[0]),
        p25=float(percentiles[1]),
        p75=float(percentiles[2]),
        p95=float(percentiles[3]),
    )


def _canonicalize_bands(bands: Mapping[str, BandInput]) -> dict[str, BandInput]:
    canonical: dict[str, BandInput] = {}
    for key, value in bands.items():
        name = key.strip().upper().replace(" ", "_")
        if name in canonical:
            raise MissingBandError(f"Band name is ambiguous after normalization: {name}")
        canonical[name] = value
    return canonical


def _prepare_band(value: BandInput) -> PreparedBand:
    band = value if isinstance(value, BandData) else BandData(value)
    source = masked_array(band.data)
    if source.ndim != 2:
        raise IncompatibleDimensionsError(
            f"Band arrays must be two-dimensional, received {source.ndim} dimensions"
        )
    values = np.asarray(source.data, dtype=np.float64)
    invalid = np.asarray(np.ma.getmaskarray(source), dtype=np.bool_)
    invalid |= ~np.isfinite(values)
    if band.nodata is not None:
        nodata = float(band.nodata)
        invalid |= np.isnan(values) if math.isnan(nodata) else values == nodata
    return PreparedBand(
        values=values,
        invalid=invalid,
        crs=None if band.crs is None else CRS.from_user_input(band.crs),
        transform=_affine(band.transform),
    )


def _validate_shapes(bands: Mapping[str, PreparedBand]) -> tuple[int, int]:
    shapes = {band.values.shape for band in bands.values()}
    if len(shapes) != 1:
        rendered = ", ".join(str(shape) for shape in sorted(shapes))
        raise IncompatibleDimensionsError(f"Required band dimensions differ: {rendered}")
    shape = next(iter(shapes))
    return int(shape[0]), int(shape[1])


def _spatial_metadata(
    bands: Mapping[str, PreparedBand],
) -> tuple[CRS | None, Affine | None]:
    crs_values = {band.crs for band in bands.values()}
    if len(crs_values) > 1:
        raise SpatialMetadataMismatchError("Required bands have different or missing CRS values")
    transform_values = {band.transform for band in bands.values()}
    if len(transform_values) > 1:
        raise SpatialMetadataMismatchError(
            "Required bands have different or missing affine transforms"
        )
    return next(iter(crs_values)), next(iter(transform_values))


def _affine(value: Affine | Sequence[float] | None) -> Affine | None:
    if value is None:
        return None
    if isinstance(value, Affine):
        return value
    if len(value) < 6:
        raise SpatialMetadataMismatchError("Affine transform must contain at least six values")
    numbers = tuple(float(item) for item in value[:6])
    if not all(math.isfinite(item) for item in numbers):
        raise SpatialMetadataMismatchError("Affine transform values must be finite")
    return Affine(*numbers)


def _parameters(
    index_name: str,
    definition: IndexDefinition,
    parameters: Mapping[str, float | int] | None,
) -> dict[str, float]:
    supplied = dict(parameters or {})
    unknown = set(supplied) - definition.parameters
    if unknown:
        raise InvalidParameterError(
            f"Unknown parameter(s) for {index_name}: {', '.join(sorted(unknown))}"
        )
    if index_name == "SAVI" and "L" not in supplied:
        supplied["L"] = DEFAULT_SAVI_L
    normalized = {name: float(value) for name, value in supplied.items()}
    if any(not math.isfinite(value) for value in normalized.values()):
        raise InvalidParameterError("Index parameters must be finite")
    return normalized


def _formula(
    index_name: str,
    formula_version: str,
    bands: Mapping[str, PreparedBand],
    parameters: Parameters | Mapping[str, float],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64] | None]:
    if formula_version != "1.0.0":
        raise InvalidParameterError(
            f"Formula implementation is unavailable: {index_name}@{formula_version}"
        )
    red = bands.get("RED")
    green = bands.get("GREEN")
    blue = bands.get("BLUE")
    nir = bands.get("NIR")
    red_edge = bands.get("RED_EDGE")
    if index_name in {"NDVI", "SAVI"} and red is not None and nir is not None:
        denominator = nir.values + red.values
        if index_name == "SAVI":
            soil = float(parameters["L"])
            denominator = denominator + soil
            return ((nir.values - red.values) / denominator) * (1 + soil), denominator
        return (nir.values - red.values) / denominator, denominator
    if index_name == "GNDVI" and green is not None and nir is not None:
        denominator = nir.values + green.values
        return (nir.values - green.values) / denominator, denominator
    if index_name == "CIGREEN" and green is not None and nir is not None:
        denominator = green.values
        return (nir.values / denominator) - 1, denominator
    if index_name == "CIRED_EDGE" and red_edge is not None and nir is not None:
        denominator = red_edge.values
        return (nir.values / denominator) - 1, denominator
    if index_name == "VARI" and green is not None and red is not None and blue is not None:
        denominator = green.values + red.values - blue.values
        return (green.values - red.values) / denominator, denominator
    if index_name == "EXG" and green is not None and red is not None and blue is not None:
        return 2 * green.values - red.values - blue.values, None
    raise MissingBandError(f"Internal band validation failed for {index_name}")
