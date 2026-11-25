"""Known-value checks for the independent radiometric calculations."""

import numpy as np
import pytest
import rasterio
from spectral_engine.calibration import (
    camera_signal,
    effective_band_reflectance,
    fit_panel,
    p4_signal_available,
    parse_raster_metadata,
    roi_statistics,
)


def test_documented_dji_metadata_from_user_sample() -> None:
    from pathlib import Path

    sample = Path(".local/calibration-sample/DJI_0301.TIF")
    if not sample.exists():
        pytest.skip("User sample is not part of the repository")
    with rasterio.open(sample) as source:
        parsed = parse_raster_metadata(
            source.tags(),
            source.tags(ns="EXIF"),
            source.tags(ns="xml:XMP").get("xml:XMP"),
            dtype=source.dtypes[0],
            count=source.count,
        )
    assert parsed["band"] == "BLUE"
    assert parsed["capture_uuid"]
    assert parsed["black_level_dn"] == 4096
    assert p4_signal_available(parsed)


def test_camera_signal_applies_black_exposure_gain_and_vignette() -> None:
    metadata = {
        "manufacturer": "DJI",
        "model": "FC6360",
        "black_level_dn": 4096,
        "sensor_gain": 2,
        "exposure_us": 1000,
        "vignetting_coefficients": [0, 0, 0, 0, 0, 0],
        "vignetting_center_px": [0, 0],
    }
    result = camera_signal(np.array([[4096, 5096]]), metadata)
    assert result[0, 0] == 0
    assert result[0, 1] == pytest.approx((1000 / 65535) / 0.002)


def test_regression_and_independent_metrics() -> None:
    model = fit_panel([10, 20, 30, 40], [0.1, 0.3, 0.5, 0.7], method="linear")
    assert model["slope"] == pytest.approx(0.02)
    assert model["intercept"] == pytest.approx(-0.1)
    assert model["rmse"] < 1e-12
    assert model["r2"] == pytest.approx(1)
    proportional = fit_panel([10], [0.4], method="proportional")
    assert proportional["slope"] == pytest.approx(0.04)
    with pytest.raises(ValueError):
        fit_panel([10], [0.4], method="linear")
    with pytest.raises(ValueError, match="nonzero"):
        fit_panel([0], [0.4], method="proportional")


def test_spectral_curve_and_roi_stats() -> None:
    assert effective_band_reflectance([400, 500], [0.2, 0.4], [425, 475], [1, 1]) == pytest.approx(
        0.3
    )
    statistics = roi_statistics(np.array([1, 2, 3, np.nan, 5]), saturation=5)
    assert statistics["valid_pixels"] == 4
    assert statistics["invalid_pixels"] == 1
    assert statistics["saturated_percent"] == pytest.approx(25)
