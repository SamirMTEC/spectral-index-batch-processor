"""Numerical verification of formulas, masks, statistics, and GeoTIFF output."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from spectral_engine import (
    BandData,
    IncompatibleDimensionsError,
    InvalidParameterError,
    MissingBandError,
    SpatialMetadataMismatchError,
    UnknownIndexError,
    calculate_index,
    write_geotiff,
)

RTOL = 1e-6
ATOL = 1e-7


@pytest.fixture
def manual_bands() -> dict[str, np.ndarray[tuple[int, int], np.dtype[np.float64]]]:
    return {
        "RED": np.array([[1, 2], [3, 4]], dtype=np.float64),
        "NIR": np.array([[3, 2], [1, 0]], dtype=np.float64),
        "GREEN": np.array([[2, 4], [6, 8]], dtype=np.float64),
        "RED_EDGE": np.array([[1.5, 2], [2.5, 4]], dtype=np.float64),
        "BLUE": np.ones((2, 2), dtype=np.float64),
    }


@pytest.mark.parametrize(
    ("index_name", "required", "parameters", "expected"),
    [
        (
            "NDVI",
            ("NIR", "RED"),
            None,
            np.array([[0.5, 0], [-0.5, -1]], dtype=np.float32),
        ),
        (
            "GNDVI",
            ("NIR", "GREEN"),
            None,
            np.array([[0.2, -1 / 3], [-5 / 7, -1]], dtype=np.float32),
        ),
        (
            "SAVI",
            ("NIR", "RED"),
            {"L": 0.5},
            np.array([[2 / 3, 0], [-2 / 3, -4 / 3]], dtype=np.float32),
        ),
        (
            "CIGREEN",
            ("NIR", "GREEN"),
            None,
            np.array([[0.5, -0.5], [-5 / 6, -1]], dtype=np.float32),
        ),
        (
            "CIRED_EDGE",
            ("NIR", "RED_EDGE"),
            None,
            np.array([[1, 0], [-0.6, -1]], dtype=np.float32),
        ),
        (
            "NDRE",
            ("NIR", "RED_EDGE"),
            None,
            np.array([[1 / 3, 0], [-3 / 7, -1]], dtype=np.float32),
        ),
        (
            "MCARI",
            ("GREEN", "RED", "RED_EDGE"),
            None,
            np.array([[0.9, 0.4], [1 / 6, 0.8]], dtype=np.float32),
        ),
        (
            "VARI",
            ("GREEN", "RED", "BLUE"),
            None,
            np.array([[0.5, 0.4], [0.375, 4 / 11]], dtype=np.float32),
        ),
        (
            "ExG",
            ("GREEN", "RED", "BLUE"),
            None,
            np.array([[2, 5], [8, 11]], dtype=np.float32),
        ),
    ],
)
def test_index_formulas_match_manual_values(
    manual_bands: dict[str, np.ndarray[tuple[int, int], np.dtype[np.float64]]],
    index_name: str,
    required: tuple[str, ...],
    parameters: dict[str, float] | None,
    expected: np.ndarray[tuple[int, int], np.dtype[np.float32]],
) -> None:
    result = calculate_index(
        index_name,
        {name: manual_bands[name] for name in required},
        parameters=parameters,
        formula_version="1.0.0" if index_name.upper() == "EXG" else None,
    )

    assert result.values.dtype == np.float32
    np.testing.assert_array_equal(result.valid_mask, np.ones((2, 2), dtype=np.bool_))
    np.testing.assert_allclose(result.values, expected, rtol=RTOL, atol=ATOL)


def test_savi_uses_default_and_configurable_soil_factor() -> None:
    bands = {"NIR": np.array([[3.0]]), "RED": np.array([[1.0]])}
    default = calculate_index("SAVI", bands)
    configured = calculate_index("SAVI", bands, parameters={"L": 1.0})

    np.testing.assert_allclose(default.values, [[2 / 3]], rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(configured.values, [[0.8]], rtol=RTOL, atol=ATOL)
    assert default.parameters == {"L": 0.5, "reflectance_scale": 1.0}
    assert configured.parameters == {"L": 1.0, "reflectance_scale": 1.0}


def test_formula_version_is_explicit_and_unknown_versions_are_rejected() -> None:
    bands = {"NIR": np.array([[3.0]]), "RED": np.array([[1.0]])}
    result = calculate_index("NDVI", bands, formula_version="1.0.0")

    assert result.formula_version == "1.0.0"
    np.testing.assert_allclose(result.values, [[0.5]], rtol=RTOL, atol=ATOL)
    with pytest.raises(UnknownIndexError, match="NDVI@9.9.9"):
        calculate_index("NDVI", bands, formula_version="9.9.9")


@pytest.mark.parametrize(
    ("public_name", "expected_name", "bands"),
    [
        ("CIgreen", "CIGREEN", {"NIR": [[3.0]], "GREEN": [[2.0]]}),
        ("CIred-edge", "CIRED_EDGE", {"NIR": [[3.0]], "RED_EDGE": [[2.0]]}),
        ("CIrededge", "CIRED_EDGE", {"NIR": [[3.0]], "RED_EDGE": [[2.0]]}),
    ],
)
def test_chlorophyll_index_public_names_resolve_to_stable_identifiers(
    public_name: str,
    expected_name: str,
    bands: dict[str, list[list[float]]],
) -> None:
    result = calculate_index(public_name, bands)

    assert result.index_name == expected_name
    assert result.formula_version == "1.0.0"
    np.testing.assert_allclose(result.values, [[0.5]], rtol=RTOL, atol=ATOL)


def test_invalid_sources_roi_and_zero_denominator_remain_invalid() -> None:
    red = BandData(
        np.array(
            [[1.0, -9999.0, np.nan], [np.inf, 1.0, -1.0]],
            dtype=np.float64,
        ),
        nodata=-9999,
    )
    nir = np.array([[3.0, 4.0, 5.0], [6.0, 1.0, 1.0]], dtype=np.float64)
    roi = np.array([[True, True, True], [True, False, True]], dtype=np.bool_)
    result = calculate_index("NDVI", {"RED": red, "NIR": nir}, roi=roi)

    expected_mask = np.array([[True, False, False], [False, False, False]], dtype=np.bool_)
    np.testing.assert_array_equal(result.valid_mask, expected_mask)
    np.testing.assert_allclose(result.values[0, 0], 0.5, rtol=RTOL, atol=ATOL)
    assert np.isnan(result.values[~expected_mask]).all()
    assert result.statistics.count == 1
    assert result.statistics.mean == pytest.approx(0.5, rel=RTOL, abs=ATOL)


def test_chlorophyll_indices_preserve_nodata_nan_inf_roi_and_zero_denominator() -> None:
    green = BandData(
        np.array([[2.0, 0.0, -9999.0], [2.0, np.nan, 2.0]]),
        nodata=-9999.0,
    )
    nir = np.array([[3.0, 3.0, 3.0], [np.inf, 3.0, 3.0]])
    roi = np.array([[True, True, True], [True, True, False]])
    result = calculate_index("CIGREEN", {"NIR": nir, "GREEN": green}, roi=roi)

    expected_mask = np.array([[True, False, False], [False, False, False]])
    np.testing.assert_array_equal(result.valid_mask, expected_mask)
    np.testing.assert_allclose(result.values[0, 0], 0.5, rtol=RTOL, atol=ATOL)
    assert np.isnan(result.values[~expected_mask]).all()
    assert result.statistics.count == 1


@pytest.mark.parametrize(
    ("index_name", "bands"),
    [
        (
            "NDRE",
            {
                "NIR": np.array([[3.0, 3.0, np.inf, 3.0]]),
                "RED_EDGE": BandData(np.array([[1.0, -3.0, 1.0, -9999.0]]), nodata=-9999),
            },
        ),
        (
            "MCARI",
            {
                "GREEN": np.array([[1.0, 1.0, 1.0, 1.0]]),
                "RED": BandData(np.array([[2.0, 0.0, np.nan, -9999.0]]), nodata=-9999),
                "RED_EDGE": np.array([[4.0, 4.0, 4.0, 4.0]]),
            },
        ),
    ],
)
def test_new_red_edge_indices_preserve_invalid_pixels(
    index_name: str, bands: dict[str, BandData | np.ndarray]
) -> None:
    result = calculate_index(index_name, bands)

    np.testing.assert_array_equal(result.valid_mask, [[True, False, False, False]])
    assert np.isfinite(result.values[0, 0])
    assert np.isnan(result.values[0, 1:]).all()
    assert result.statistics.count == 1


def test_negative_values_are_valid_when_formula_is_defined() -> None:
    result = calculate_index(
        "NDVI",
        {"NIR": np.array([[-1.0]]), "RED": np.array([[-2.0]])},
    )

    assert result.valid_mask[0, 0]
    np.testing.assert_allclose(result.values, [[-1 / 3]], rtol=RTOL, atol=ATOL)


def test_integer_extremes_are_converted_before_arithmetic() -> None:
    maximum = np.iinfo(np.int32).max
    minimum = np.iinfo(np.int32).min
    result = calculate_index(
        "EXG",
        {
            "GREEN": np.array([[maximum]], dtype=np.int32),
            "RED": np.array([[minimum]], dtype=np.int32),
            "BLUE": np.array([[minimum]], dtype=np.int32),
        },
        formula_version="1.0.0",
    )
    expected = float(2 * int(maximum) - int(minimum) - int(minimum))

    assert result.valid_mask[0, 0]
    np.testing.assert_allclose(result.values, [[expected]], rtol=RTOL, atol=1.0)


def test_result_overflow_to_float32_is_invalid_not_infinite() -> None:
    extreme = 2e38
    result = calculate_index(
        "EXG",
        {
            "GREEN": np.array([[extreme]]),
            "RED": np.array([[-extreme]]),
            "BLUE": np.array([[0.0]]),
        },
        formula_version="1.0.0",
    )

    assert not result.valid_mask[0, 0]
    assert np.isnan(result.values[0, 0])
    assert result.statistics.count == 0


def test_native_masked_array_mask_is_preserved() -> None:
    red = np.ma.array([[1.0, 2.0]], mask=[[False, True]])
    nir = np.array([[3.0, 4.0]])
    result = calculate_index("NDVI", {"RED": red, "NIR": nir})

    np.testing.assert_array_equal(result.valid_mask, [[True, False]])
    np.testing.assert_allclose(result.values[0, 0], 0.5, rtol=RTOL, atol=ATOL)
    assert np.isnan(result.values[0, 1])


def test_statistics_use_only_valid_float32_pixels() -> None:
    desired = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64)
    result = calculate_index(
        "EXG",
        {"GREEN": desired / 2, "RED": np.zeros((2, 2)), "BLUE": np.zeros((2, 2))},
        formula_version="1.0.0",
    )
    statistics = result.statistics

    assert statistics.count == 4
    assert statistics.mean == pytest.approx(2.5, rel=RTOL, abs=ATOL)
    assert statistics.median == pytest.approx(2.5, rel=RTOL, abs=ATOL)
    assert statistics.min == pytest.approx(1.0, rel=RTOL, abs=ATOL)
    assert statistics.max == pytest.approx(4.0, rel=RTOL, abs=ATOL)
    assert statistics.std == pytest.approx(np.sqrt(1.25), rel=RTOL, abs=ATOL)
    assert statistics.p05 == pytest.approx(1.15, rel=RTOL, abs=ATOL)
    assert statistics.p25 == pytest.approx(1.75, rel=RTOL, abs=ATOL)
    assert statistics.p75 == pytest.approx(3.25, rel=RTOL, abs=ATOL)
    assert statistics.p95 == pytest.approx(3.85, rel=RTOL, abs=ATOL)


def test_missing_band_and_incompatible_dimensions_fail_before_calculation() -> None:
    with pytest.raises(MissingBandError, match="NIR"):
        calculate_index("NDVI", {"RED": np.ones((2, 2))})
    with pytest.raises(IncompatibleDimensionsError, match="dimensions differ"):
        calculate_index(
            "NDVI",
            {"RED": np.ones((2, 2)), "NIR": np.ones((3, 2))},
        )
    with pytest.raises(IncompatibleDimensionsError, match="ROI shape"):
        calculate_index(
            "NDVI",
            {"RED": np.ones((2, 2)), "NIR": np.ones((2, 2))},
            roi=np.ones((3, 2), dtype=np.bool_),
        )


def test_non_finite_or_unknown_parameter_is_rejected() -> None:
    bands = {"NIR": np.array([[3.0]]), "RED": np.array([[1.0]])}
    with pytest.raises(InvalidParameterError, match="finite"):
        calculate_index("SAVI", bands, parameters={"L": np.nan})
    with pytest.raises(InvalidParameterError, match="Unknown"):
        calculate_index("NDVI", bands, parameters={"L": 0.5})


def test_spatial_metadata_mismatch_is_rejected() -> None:
    red = BandData(np.ones((1, 1)), crs="EPSG:4326", transform=Affine.identity())
    nir = BandData(np.ones((1, 1)), crs="EPSG:31984", transform=Affine.identity())
    with pytest.raises(SpatialMetadataMismatchError, match="CRS"):
        calculate_index("NDVI", {"RED": red, "NIR": nir})


def test_geotiff_is_float32_masked_and_preserves_grid(tmp_path: Path) -> None:
    transform = Affine.translation(-40, -12) @ Affine.scale(0.01, -0.01)
    red = BandData(
        np.array([[1.0, -1.0], [3.0, 4.0]]),
        crs="EPSG:4326",
        transform=transform,
    )
    nir = BandData(
        np.array([[3.0, 1.0], [1.0, 0.0]]),
        crs="EPSG:4326",
        transform=transform,
    )
    result = calculate_index("NDVI", {"RED": red, "NIR": nir})
    path = write_geotiff(result, tmp_path / "ndvi.tif")

    with rasterio.open(path) as raster:
        assert raster.dtypes == ("float32",)
        assert raster.width == 2
        assert raster.height == 2
        assert raster.crs is not None
        assert raster.crs.to_string() == "EPSG:4326"
        assert raster.transform.almost_equals(transform)
        assert raster.nodata is not None and np.isnan(raster.nodata)
        np.testing.assert_array_equal(
            raster.read_masks(1), np.array([[255, 0], [255, 255]], dtype=np.uint8)
        )
        read = raster.read(1)
        np.testing.assert_allclose(
            read[[0, 1, 1], [0, 0, 1]],
            np.array([0.5, -0.5, -1], dtype=np.float32),
            rtol=RTOL,
            atol=ATOL,
        )
        assert np.isnan(read[0, 1])
        assert raster.tags()["SPECTRAL_INDEX"] == "NDVI"
        assert raster.tags()["SPECTRAL_FORMULA_VERSION"] == "1.0.0"
