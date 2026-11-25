"""Auditable panel calibration primitives. Values are never silently reinterpreted."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

ALGORITHM_VERSION = "panel-linear-1.0"
P4_BANDS = {"BLUE": 450, "GREEN": 560, "RED": 650, "RED_EDGE": 730, "NIR": 840}
_BAND_ALIASES = {
    "BLUE": "BLUE",
    "GREEN": "GREEN",
    "RED": "RED",
    "REDEDGE": "RED_EDGE",
    "RE": "RED_EDGE",
    "NIR": "NIR",
}


def _number(value: object) -> float | None:
    try:
        result = float(str(value).strip("() "))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _sequence(value: object) -> list[float]:
    if isinstance(value, str):
        return [
            float(part)
            for part in re.split(r"[,;\s]+", value.strip())
            if part and _number(part) is not None
        ]
    if isinstance(value, Sequence):
        return [float(part) for part in value if _number(part) is not None]
    return []


def parse_raster_metadata(
    tags: Mapping[str, str], exif: Mapping[str, str], xmp: str | None, *, dtype: str, count: int
) -> dict[str, Any]:
    """Extract only documented fields; keep the source name for each measurement."""
    fields: dict[str, str] = {}
    if xmp and len(xmp) <= 1_000_000:
        try:
            root = ET.fromstring(xmp)
            for element in root.iter():
                for key, value in element.attrib.items():
                    fields.setdefault(key.rsplit("}", 1)[-1], value)
                if element.text and element.text.strip():
                    fields.setdefault(element.tag.rsplit("}", 1)[-1], element.text.strip())
                if element.tag.rsplit("}", 1)[-1] in {
                    "VignettingPolynomial",
                    "VignettingCenter",
                    "RadiometricCalibration",
                }:
                    values = [
                        child.text.strip()
                        for child in element.iter()
                        if child.tag.rsplit("}", 1)[-1] == "li" and child.text
                    ]
                    if values:
                        fields[element.tag.rsplit("}", 1)[-1]] = ",".join(values)
        except ET.ParseError:
            pass
    band_text = fields.get("BandName") or (tags.get("BandName") if tags else None) or ""
    band = _BAND_ALIASES.get(re.sub(r"[^A-Z]", "", band_text.upper()))
    make = fields.get("Make") or tags.get("TIFFTAG_MAKE")
    model = fields.get("Model") or tags.get("TIFFTAG_MODEL")
    black = _number(fields.get("BlackCurrent") or exif.get("EXIF_BlackLevel"))
    gain = _number(fields.get("SensorGain"))
    exposure_us = _number(fields.get("ExposureTime"))
    vignette = _sequence(fields.get("VignettingPolynomial") or fields.get("VignettingData"))
    center = _sequence(fields.get("VignettingCenter"))
    if len(center) != 2:
        center = [
            _number(fields.get("CalibratedOpticalCenterX")) or math.nan,
            _number(fields.get("CalibratedOpticalCenterY")) or math.nan,
        ]
    offset = [
        _number(fields.get("RelativeOpticalCenterX")),
        _number(fields.get("RelativeOpticalCenterY")),
    ]
    result: dict[str, Any] = {
        "manufacturer": make,
        "model": model,
        "band": band,
        "wavelength_nm": _number(fields.get("CentralWavelength"))
        or (P4_BANDS.get(band) if band else None),
        "capture_uuid": fields.get("CaptureUUID"),
        "acquired_at": exif.get("EXIF_DateTimeOriginal") or tags.get("TIFFTAG_DATETIME"),
        "exposure_us": exposure_us,
        "sensor_gain": gain,
        "iso": _number(exif.get("EXIF_ISOSpeed")),
        "black_level_dn": black,
        "irradiance": _number(fields.get("Irradiance")),
        "sensor_gain_adjustment": _number(fields.get("SensorGainAdjustment")),
        "vignetting_coefficients": vignette if len(vignette) == 6 else None,
        "vignetting_center_px": center if all(math.isfinite(value) for value in center) else None,
        "relative_optical_center_px": offset
        if all(value is not None for value in offset)
        else None,
        "flight_speed_mps": [
            _number(fields.get("FlightXSpeed")),
            _number(fields.get("FlightYSpeed")),
            _number(fields.get("FlightZSpeed")),
        ],
        "latitude": _number(fields.get("GpsLatitude")),
        "longitude": _number(fields.get("GpsLongitude")),
        "dtype": dtype,
        "band_count": count,
        "source": "XMP/EXIF/TIFF" if fields else "TIFF/EXIF",
    }
    return result


def p4_signal_available(metadata: Mapping[str, Any]) -> bool:
    return (
        str(metadata.get("manufacturer") or "").upper() == "DJI"
        and str(metadata.get("model") or "").upper() == "FC6360"
        and all(
            _number(metadata.get(key)) is not None and float(metadata[key]) > 0
            for key in ("sensor_gain", "exposure_us")
        )
        and _number(metadata.get("black_level_dn")) is not None
        and isinstance(metadata.get("vignetting_coefficients"), list)
        and len(metadata["vignetting_coefficients"]) == 6
        and isinstance(metadata.get("vignetting_center_px"), list)
        and len(metadata["vignetting_center_px"]) == 2
    )


def camera_signal(
    dn: np.ndarray,
    metadata: Mapping[str, Any],
    *,
    x0: int = 0,
    y0: int = 0,
    mode: str = "p4_signal",
) -> np.ndarray:
    """Return DJI camera signal or explicitly selected empirical DN, never radiance."""
    values = np.asarray(dn, dtype=np.float64)
    if mode == "dn_empirical":
        return values
    if mode != "p4_signal" or not p4_signal_available(metadata):
        raise ValueError("Documented P4 camera-signal metadata is incomplete")
    black = float(metadata["black_level_dn"])
    gain = float(metadata["sensor_gain"])
    exposure_seconds = float(metadata["exposure_us"]) / 1_000_000.0
    center_x, center_y = (float(value) for value in metadata["vignetting_center_px"])
    k = np.asarray(metadata["vignetting_coefficients"], dtype=np.float64)
    yy, xx = np.ogrid[y0 : y0 + values.shape[0], x0 : x0 + values.shape[1]]
    radius = np.hypot(xx - center_x, yy - center_y)
    correction = 1.0 + sum(k[index] * radius ** (index + 1) for index in range(6))
    if not np.all(np.isfinite(correction)) or np.any(correction <= 0):
        raise ValueError("Vignetting correction is invalid")
    return np.asarray(
        ((values - black) / 65535.0) * correction / (gain * exposure_seconds),
        dtype=np.float64,
    )


def effective_band_reflectance(
    curve_nm: Sequence[float],
    curve_reflectance: Sequence[float],
    response_nm: Sequence[float],
    response: Sequence[float],
) -> float:
    """Integrate a certified curve against a measured spectral response."""
    wavelengths = np.asarray(curve_nm, dtype=float)
    reflectance = np.asarray(curve_reflectance, dtype=float)
    band_nm = np.asarray(response_nm, dtype=float)
    weights = np.asarray(response, dtype=float)
    if (
        len(wavelengths) < 2
        or len(band_nm) < 2
        or len(wavelengths) != len(reflectance)
        or len(band_nm) != len(weights)
    ):
        raise ValueError("Curve and response require matching samples")
    if (
        np.any(np.diff(wavelengths) <= 0)
        or np.any(np.diff(band_nm) <= 0)
        or band_nm[0] < wavelengths[0]
        or band_nm[-1] > wavelengths[-1]
    ):
        raise ValueError("Spectral response lies outside the certified curve")
    if (
        not np.all(np.isfinite(reflectance))
        or np.any((reflectance < 0) | (reflectance > 1))
        or not np.all(np.isfinite(weights))
        or np.any(weights < 0)
    ):
        raise ValueError("Invalid reflectance or spectral response")
    denominator = float(np.trapezoid(weights, band_nm))
    if denominator <= 0:
        raise ValueError("Spectral response integral must be positive")
    return float(
        np.trapezoid(np.interp(band_nm, wavelengths, reflectance) * weights, band_nm) / denominator
    )


def roi_statistics(
    values: np.ndarray, *, saturation: float | None = None, histogram_bins: int = 32
) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        raise ValueError("ROI has no valid pixels")
    percentiles = np.percentile(finite, [5, 25, 50, 75, 95])
    mean = float(finite.mean())
    std = float(finite.std(ddof=1)) if finite.size > 1 else 0.0
    hist, edges = np.histogram(finite, bins=histogram_bins)
    return {
        "valid_pixels": int(finite.size),
        "invalid_pixels": int(array.size - finite.size),
        "mean": mean,
        "median": float(percentiles[2]),
        "std": std,
        "cv": std / abs(mean) if mean else None,
        "min": float(finite.min()),
        "max": float(finite.max()),
        "percentiles": {
            str(k): float(v) for k, v in zip((5, 25, 50, 75, 95), percentiles, strict=True)
        },
        "saturated_percent": float(np.mean(finite >= saturation) * 100)
        if saturation is not None
        else None,
        "histogram": {"counts": hist.tolist(), "edges": edges.tolist()},
    }


def fit_panel(
    x: Sequence[float],
    y: Sequence[float],
    *,
    method: str,
    weights: Sequence[float] | None = None,
    robust: bool = False,
) -> dict[str, Any]:
    measured = np.asarray(x, dtype=float)
    reference = np.asarray(y, dtype=float)
    if len(measured) != len(reference) or len(measured) < (1 if method == "proportional" else 2):
        raise ValueError("Insufficient panel references")
    if (
        not np.all(np.isfinite(measured))
        or not np.all(np.isfinite(reference))
        or np.any((reference < 0) | (reference > 1))
    ):
        raise ValueError("Non-finite observations or reflectance outside [0,1]")
    if method not in {"proportional", "linear"}:
        raise ValueError("Unknown calibration method")
    if method == "linear" and np.ptp(measured) <= 0:
        raise ValueError("Reference intensities do not vary")
    if method == "proportional" and np.all(measured == 0):
        raise ValueError("Proportional calibration requires a nonzero panel signal")
    base = np.ones(len(measured)) if weights is None else np.asarray(weights, dtype=float)
    if base.shape != measured.shape or np.any(~np.isfinite(base)) or np.any(base <= 0):
        raise ValueError("Invalid regression weights")
    design = (
        measured[:, None]
        if method == "proportional"
        else np.column_stack((measured, np.ones(len(measured))))
    )
    current = base.copy()
    iterations = 12 if robust and len(measured) >= 3 else 1
    for _ in range(iterations):
        coefficients = np.linalg.lstsq(
            design * np.sqrt(current[:, None]), reference * np.sqrt(current), rcond=None
        )[0]
        residuals = reference - design @ coefficients
        if iterations == 1:
            break
        scale = 1.4826 * np.median(np.abs(residuals - np.median(residuals)))
        if scale <= 1e-12:
            break
        current = base * np.minimum(1.0, 1.345 * scale / np.maximum(np.abs(residuals), 1e-12))
    slope = float(coefficients[0])
    intercept = float(coefficients[1]) if method == "linear" else 0.0
    predicted = measured * slope + intercept
    residuals = predicted - reference
    ss_total = float(np.sum((reference - reference.mean()) ** 2))
    return {
        "slope": slope,
        "intercept": intercept,
        "r2": 1 - float(np.sum(residuals**2)) / ss_total if ss_total > 0 else None,
        "rmse": float(np.sqrt(np.mean(residuals**2))),
        "mae": float(np.mean(np.abs(residuals))),
        "bias": float(np.mean(residuals)),
        "predicted": predicted.tolist(),
        "residuals": residuals.tolist(),
        "method": method,
        "robust": robust,
        "weighted": weights is not None,
    }
