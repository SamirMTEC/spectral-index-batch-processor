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
    default_parameters: tuple[tuple[str, float], ...] = ()


INDEX_DEFINITIONS: dict[str, IndexDefinition] = {
    name: IndexDefinition(
        definition.required_bands,
        definition.allowed_parameters,
        definition.default_parameters,
    )
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
        scientific_definition.required_bands,
        scientific_definition.allowed_parameters,
        scientific_definition.default_parameters,
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
        if name == "REDEDGE":
            name = "RED_EDGE"
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
    supplied = {**dict(definition.default_parameters), **dict(parameters or {})}
    unknown = set(supplied) - definition.parameters
    if unknown:
        raise InvalidParameterError(
            f"Unknown parameter(s) for {index_name}: {', '.join(sorted(unknown))}"
        )
    normalized = {name: float(value) for name, value in supplied.items()}
    if any(not math.isfinite(value) for value in normalized.values()):
        raise InvalidParameterError("Index parameters must be finite")
    if normalized.get("reflectance_scale", 1.0) <= 0:
        raise InvalidParameterError("reflectance_scale must be greater than zero")
    return normalized


def _formula(
    index_name: str,
    formula_version: str,
    bands: Mapping[str, PreparedBand],
    parameters: Parameters | Mapping[str, float],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64] | None]:
    if formula_version not in {"1.0.0", "2.0.0"}:
        raise InvalidParameterError(
            f"Formula implementation is unavailable: {index_name}@{formula_version}"
        )
    scale = float(parameters.get("reflectance_scale", 1.0))
    red = bands["RED"].values / scale if "RED" in bands else None
    green = bands["GREEN"].values / scale if "GREEN" in bands else None
    blue = bands["BLUE"].values / scale if "BLUE" in bands else None
    nir = bands["NIR"].values / scale if "NIR" in bands else None
    red_edge = bands["RED_EDGE"].values / scale if "RED_EDGE" in bands else None
    if index_name in {"NDVI", "SAVI"} and red is not None and nir is not None:
        denominator = nir + red
        if index_name == "SAVI":
            soil = float(parameters["L"])
            denominator = denominator + soil
            return _safe_divide(nir - red, denominator) * (1 + soil), denominator
        return _safe_divide(nir - red, denominator), denominator
    if index_name == "GNDVI" and green is not None and nir is not None:
        denominator = nir + green
        return _safe_divide(nir - green, denominator), denominator
    if index_name == "CIGREEN" and green is not None and nir is not None:
        return _safe_divide(nir, green) - 1, green
    if index_name == "CIRED_EDGE" and red_edge is not None and nir is not None:
        return _safe_divide(nir, red_edge) - 1, red_edge
    if index_name == "NDRE" and red_edge is not None and nir is not None:
        denominator = nir + red_edge
        return _safe_divide(nir - red_edge, denominator), denominator
    if index_name == "MCARI" and red is not None and green is not None and red_edge is not None:
        absorption = (red_edge - red) - 0.2 * (red_edge - green)
        return absorption * _safe_divide(red_edge, red), red
    if index_name == "VARI" and green is not None and red is not None and blue is not None:
        denominator = green + red - blue
        return _safe_divide(green - red, denominator), denominator
    if index_name == "EXG" and green is not None and red is not None and blue is not None:
        if formula_version == "1.0.0":
            return 2 * green - red - blue, None
        red_normalized, green_normalized, blue_normalized, total = _normalized_rgb(red, green, blue)
        return 2 * green_normalized - red_normalized - blue_normalized, total
    if index_name == "DVI" and red is not None and nir is not None:
        return nir - red, None
    if index_name == "RVI" and red is not None and nir is not None:
        return _safe_divide(nir, red), red
    if index_name == "IPVI" and red is not None and nir is not None:
        denominator = nir + red
        return _safe_divide(nir, denominator), denominator
    if index_name == "RDVI" and red is not None and nir is not None:
        denominator = _safe_sqrt(nir + red)
        return _safe_divide(nir - red, denominator), denominator
    if index_name == "OSAVI" and red is not None and nir is not None:
        denominator = nir + red + 0.16
        return 1.16 * _safe_divide(nir - red, denominator), denominator
    if index_name == "MSAVI2" and red is not None and nir is not None:
        root = _safe_sqrt((2 * nir + 1) ** 2 - 8 * (nir - red))
        return (2 * nir + 1 - root) / 2, None
    if index_name in {"TVI", "CTVI", "NIRV"} and red is not None and nir is not None:
        ndvi_denominator = nir + red
        ndvi = _safe_divide(nir - red, ndvi_denominator)
        if index_name == "TVI":
            return _safe_sqrt(ndvi + 0.5), ndvi_denominator
        if index_name == "CTVI":
            shifted = ndvi + 0.5
            result = np.full(shifted.shape, np.nan, dtype=np.float64)
            valid = np.isfinite(shifted) & (shifted != 0)
            result[valid] = np.sign(shifted[valid]) * np.sqrt(np.abs(shifted[valid]))
            return result, ndvi_denominator
        return ndvi * nir, ndvi_denominator
    if index_name == "EVI" and red is not None and nir is not None and blue is not None:
        denominator = nir + 6 * red - 7.5 * blue + 1
        return 2.5 * _safe_divide(nir - red, denominator), denominator
    if index_name == "EVI2" and red is not None and nir is not None:
        denominator = nir + 2.4 * red + 1
        return 2.5 * _safe_divide(nir - red, denominator), denominator
    if index_name == "ARVI" and red is not None and nir is not None and blue is not None:
        gamma = float(parameters["gamma"])
        corrected_red = red - gamma * (blue - red)
        denominator = nir + corrected_red
        return _safe_divide(nir - corrected_red, denominator), denominator
    if index_name == "MNDVI" and red is not None and nir is not None and blue is not None:
        denominator = nir + red - 2 * blue
        return _safe_divide(nir - red, denominator), denominator
    if index_name == "SIPI" and red is not None and nir is not None and blue is not None:
        denominator = nir - red
        return _safe_divide(nir - blue, denominator), denominator
    if index_name == "NPCI" and red is not None and blue is not None:
        denominator = red + blue
        return _safe_divide(red - blue, denominator), denominator
    if index_name == "CI" and red is not None and blue is not None:
        return _safe_divide(red - blue, red), red
    if index_name == "CVI" and red is not None and green is not None and nir is not None:
        denominator = green**2
        return _safe_divide(nir * red, denominator), denominator
    if index_name == "GLI" and green is not None and red is not None and blue is not None:
        denominator = 2 * green + red + blue
        return _safe_divide(2 * green - red - blue, denominator), denominator
    if index_name == "NGRDI" and green is not None and red is not None:
        denominator = green + red
        return _safe_divide(green - red, denominator), denominator
    if index_name == "RGBVI" and green is not None and red is not None and blue is not None:
        denominator = green**2 + red * blue
        return _safe_divide(green**2 - red * blue, denominator), denominator
    if index_name == "MGRVI" and green is not None and red is not None:
        denominator = green**2 + red**2
        return _safe_divide(green**2 - red**2, denominator), denominator
    if index_name in {"EXR", "EXGR"} and green is not None and red is not None and blue is not None:
        red_normalized, green_normalized, blue_normalized, total = _normalized_rgb(red, green, blue)
        exr = 1.4 * red_normalized - green_normalized
        if index_name == "EXR":
            return exr, total
        exg = 2 * green_normalized - red_normalized - blue_normalized
        return exg - exr, total
    if index_name == "TGI" and green is not None and red is not None and blue is not None:
        return green - 0.39 * red - 0.61 * blue, None
    if index_name == "RRI1" and red_edge is not None and nir is not None:
        return _safe_divide(nir, red_edge), red_edge
    if index_name in {"ARI", "MARI"} and green is not None and red_edge is not None:
        ari = _safe_divide(np.ones_like(green), green) - _safe_divide(
            np.ones_like(red_edge), red_edge
        )
        if index_name == "MARI" and nir is not None:
            return nir * ari, None
        return ari, None
    if index_name == "MCARI1" and red is not None and green is not None and nir is not None:
        return 1.2 * (2.5 * (nir - red) - 1.3 * (nir - green)), None
    if index_name == "MCARI2" and red is not None and green is not None and nir is not None:
        numerator = 1.5 * (2.5 * (nir - red) - 1.3 * (nir - green))
        denominator = _safe_sqrt((2 * nir + 1) ** 2 - (6 * nir - 5 * _safe_sqrt(red)) - 0.5)
        return _safe_divide(numerator, denominator), denominator
    if index_name == "PSRI" and red is not None and green is not None and nir is not None:
        return _safe_divide(red - green, nir), nir
    raise MissingBandError(f"Internal band validation failed for {index_name}")


def _safe_divide(
    numerator: npt.NDArray[np.float64], denominator: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    """Divide finite arrays while leaving zero or invalid denominators as NaN."""
    result = np.full(numerator.shape, np.nan, dtype=np.float64)
    valid = (
        np.isfinite(numerator)
        & np.isfinite(denominator)
        & ~np.isclose(denominator, 0, rtol=0, atol=np.finfo(np.float64).eps)
    )
    np.divide(numerator, denominator, out=result, where=valid)
    return result


def _safe_sqrt(values: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    result = np.full(values.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(values) & (values >= 0)
    np.sqrt(values, out=result, where=valid)
    return result


def _normalized_rgb(
    red: npt.NDArray[np.float64],
    green: npt.NDArray[np.float64],
    blue: npt.NDArray[np.float64],
) -> tuple[
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
]:
    total = red + green + blue
    return (
        _safe_divide(red, total),
        _safe_divide(green, total),
        _safe_divide(blue, total),
        total,
    )
