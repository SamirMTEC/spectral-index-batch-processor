"""Independent NumPy audit of formulas, validity rules, ROI, and statistics."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import numpy.typing as npt
import pytest
from affine import Affine
from rasterio.features import geometry_mask
from spectral_engine import (
    BandData,
    IncompatibleDimensionsError,
    MissingBandError,
    calculate_index,
)

from tests.science.synthetic_datasets import (
    FLOAT32_BANDS,
    INVALIDITY_NIR,
    INVALIDITY_RED,
    RECTANGULAR_ROI,
    TRIANGULAR_ROI_PIXELS,
    UINT8_BANDS,
    UINT16_BANDS,
)

VALUE_RTOL = 1e-6
VALUE_ATOL = 1e-7
STAT_RTOL = 1e-9
STAT_ATOL = 1e-12


def _float64(values: npt.ArrayLike) -> npt.NDArray[np.float64]:
    return np.asarray(values, dtype=np.float64)


def _assert_scientific_values(
    observed: npt.NDArray[np.float32], expected: npt.NDArray[np.float32]
) -> None:
    np.testing.assert_allclose(
        observed,
        expected,
        rtol=VALUE_RTOL,
        atol=VALUE_ATOL,
        equal_nan=True,
    )


def test_ndvi_matches_manual_values_and_independent_numpy_reference() -> None:
    red = UINT8_BANDS["RED"]
    nir = UINT8_BANDS["NIR"]
    manual = np.array(
        [[0.5, 0.0, -0.5], [1 / 3, 1 / 3, 1 / 3]],
        dtype=np.float32,
    )
    reference = ((_float64(nir) - _float64(red)) / (_float64(nir) + _float64(red))).astype(
        np.float32
    )

    _assert_scientific_values(reference, manual)
    result = calculate_index("NDVI", {"NIR": nir, "RED": red})
    _assert_scientific_values(result.values, reference)
    assert result.values.dtype == np.float32


def test_gndvi_matches_manual_values_and_independent_numpy_reference() -> None:
    green = UINT16_BANDS["GREEN"]
    nir = UINT16_BANDS["NIR"]
    manual = np.array(
        [[0.2, -0.2, -0.6], [3 / 13, 0.25, 5 / 19]],
        dtype=np.float32,
    )
    reference = ((_float64(nir) - _float64(green)) / (_float64(nir) + _float64(green))).astype(
        np.float32
    )

    _assert_scientific_values(reference, manual)
    result = calculate_index("GNDVI", {"NIR": nir, "GREEN": green})
    _assert_scientific_values(result.values, reference)
    assert result.values.dtype == np.float32


def test_savi_matches_manual_values_and_independent_numpy_reference() -> None:
    red = FLOAT32_BANDS["RED"]
    nir = FLOAT32_BANDS["NIR"]
    soil_factor = 0.5
    manual = np.array(
        [
            [2 / 3, 0.0, -2 / 3],
            [0.48, 15 / 31, 18 / 37],
        ],
        dtype=np.float32,
    )
    reference = (
        ((_float64(nir) - _float64(red)) / (_float64(nir) + _float64(red) + soil_factor))
        * (1 + soil_factor)
    ).astype(np.float32)

    _assert_scientific_values(reference, manual)
    result = calculate_index(
        "SAVI",
        {"NIR": nir, "RED": red},
        parameters={"L": soil_factor},
    )
    _assert_scientific_values(result.values, reference)
    assert result.values.dtype == np.float32


def test_cigreen_matches_manual_values_and_independent_numpy_reference() -> None:
    green = UINT16_BANDS["GREEN"]
    nir = UINT16_BANDS["NIR"]
    manual = np.array(
        [[0.5, -1 / 3, -0.75], [0.6, 2 / 3, 5 / 7]],
        dtype=np.float32,
    )
    reference = ((_float64(nir) / _float64(green)) - 1).astype(np.float32)

    _assert_scientific_values(reference, manual)
    result = calculate_index("CIGREEN", {"NIR": nir, "GREEN": green})
    _assert_scientific_values(result.values, reference)
    assert result.values.dtype == np.float32


def test_cired_edge_matches_manual_values_and_independent_numpy_reference() -> None:
    red_edge = UINT16_BANDS["RED_EDGE"]
    nir = UINT16_BANDS["NIR"]
    manual = np.array(
        [[1.0, 0.0, -0.6], [1.0, 1.0, 1.0]],
        dtype=np.float32,
    )
    reference = ((_float64(nir) / _float64(red_edge)) - 1).astype(np.float32)

    _assert_scientific_values(reference, manual)
    result = calculate_index("CIRED_EDGE", {"NIR": nir, "RED_EDGE": red_edge})
    _assert_scientific_values(result.values, reference)
    assert result.values.dtype == np.float32


def test_ndre_matches_manual_values_and_independent_numpy_reference() -> None:
    red_edge = UINT16_BANDS["RED_EDGE"]
    nir = UINT16_BANDS["NIR"]
    manual = np.array(
        [[1 / 3, 0.0, -3 / 7], [1 / 3, 1 / 3, 1 / 3]],
        dtype=np.float32,
    )
    reference = (
        (_float64(nir) - _float64(red_edge)) / (_float64(nir) + _float64(red_edge))
    ).astype(np.float32)

    _assert_scientific_values(reference, manual)
    result = calculate_index("NDRE", {"NIR": nir, "RED_EDGE": red_edge})
    _assert_scientific_values(result.values, reference)
    assert result.values.dtype == np.float32


def test_mcari_matches_manual_values_and_independent_numpy_reference() -> None:
    green = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    red = np.array([[2.0, 2.0], [4.0, 5.0]], dtype=np.float32)
    red_edge = np.array([[4.0, 3.0], [6.0, 10.0]], dtype=np.float32)
    manual = np.array([[2.8, 1.2], [2.1, 7.6]], dtype=np.float32)
    reference = (
        ((_float64(red_edge) - _float64(red)) - 0.2 * (_float64(red_edge) - _float64(green)))
        * (_float64(red_edge) / _float64(red))
    ).astype(np.float32)

    _assert_scientific_values(reference, manual)
    result = calculate_index("MCARI", {"GREEN": green, "RED": red, "RED_EDGE": red_edge})
    _assert_scientific_values(result.values, reference)
    assert result.values.dtype == np.float32


def test_vari_matches_manual_values_and_independent_numpy_reference() -> None:
    red = UINT16_BANDS["RED"]
    green = UINT16_BANDS["GREEN"]
    blue = UINT16_BANDS["BLUE"]
    manual = np.array(
        [[0.4, 0.25, 2 / 11], [1 / 7, 2 / 17, 0.1]],
        dtype=np.float32,
    )
    reference = (
        (_float64(green) - _float64(red)) / (_float64(green) + _float64(red) - _float64(blue))
    ).astype(np.float32)

    _assert_scientific_values(reference, manual)
    result = calculate_index("VARI", {"GREEN": green, "RED": red, "BLUE": blue})
    _assert_scientific_values(result.values, reference)
    assert result.values.dtype == np.float32


def test_exg_matches_manual_values_and_independent_numpy_reference() -> None:
    red = UINT8_BANDS["RED"]
    green = UINT8_BANDS["GREEN"]
    blue = UINT8_BANDS["BLUE"]
    manual = np.array([[25, 30, 35], [40, 45, 50]], dtype=np.float32)
    reference = (2 * _float64(green) - _float64(red) - _float64(blue)).astype(np.float32)

    _assert_scientific_values(reference, manual)
    result = calculate_index(
        "EXG",
        {"GREEN": green, "RED": red, "BLUE": blue},
        formula_version="1.0.0",
    )
    _assert_scientific_values(result.values, reference)
    assert result.values.dtype == np.float32


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16, np.float32])
def test_input_dtype_does_not_change_ndvi_and_output_is_float32(
    dtype: type[np.generic],
) -> None:
    red = np.array([[10, 20], [30, 40]], dtype=dtype)
    nir = np.array([[30, 40], [50, 80]], dtype=dtype)
    reference = ((_float64(nir) - _float64(red)) / (_float64(nir) + _float64(red))).astype(
        np.float32
    )

    result = calculate_index("NDVI", {"RED": red, "NIR": nir})

    assert result.values.dtype == np.float32
    _assert_scientific_values(result.values, reference)


def test_rectangular_roi_masks_pixels_before_statistics() -> None:
    red = UINT8_BANDS["RED"]
    nir = UINT8_BANDS["NIR"]
    reference = ((_float64(nir) - _float64(red)) / (_float64(nir) + _float64(red))).astype(
        np.float32
    )
    expected = np.where(RECTANGULAR_ROI, reference, np.float32(np.nan))

    result = calculate_index("NDVI", {"RED": red, "NIR": nir}, roi=RECTANGULAR_ROI)

    np.testing.assert_array_equal(result.valid_mask, RECTANGULAR_ROI)
    _assert_scientific_values(result.values, expected)
    assert result.statistics.count == int(np.count_nonzero(RECTANGULAR_ROI))


def test_polygonal_roi_rasterization_has_known_pixel_coverage() -> None:
    polygon = {
        "type": "Polygon",
        "coordinates": [[(0, 0), (4, 0), (0, 4), (0, 0)]],
    }
    roi = geometry_mask(
        [polygon],
        out_shape=(4, 4),
        transform=Affine.identity(),
        invert=True,
    )
    np.testing.assert_array_equal(roi, TRIANGULAR_ROI_PIXELS)
    red = np.arange(1, 17, dtype=np.float32).reshape(4, 4)
    nir = red * np.float32(3)

    result = calculate_index("NDVI", {"RED": red, "NIR": nir}, roi=roi)

    np.testing.assert_array_equal(result.valid_mask, TRIANGULAR_ROI_PIXELS)
    _assert_scientific_values(
        result.values[TRIANGULAR_ROI_PIXELS],
        np.full(np.count_nonzero(TRIANGULAR_ROI_PIXELS), 0.5, dtype=np.float32),
    )
    assert np.isnan(result.values[~TRIANGULAR_ROI_PIXELS]).all()


def test_nodata_nan_inf_zero_denominator_and_negative_values_follow_reference_mask() -> None:
    red64 = _float64(INVALIDITY_RED)
    nir64 = _float64(INVALIDITY_NIR)
    denominator = nir64 + red64
    source_valid = np.isfinite(red64) & np.isfinite(nir64) & (red64 != -9999.0)
    expected_mask = source_valid & np.isfinite(denominator) & (denominator != 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        reference64 = (nir64 - red64) / denominator
    expected_mask &= np.isfinite(reference64)
    expected = np.full(red64.shape, np.nan, dtype=np.float32)
    expected[expected_mask] = reference64[expected_mask].astype(np.float32)

    result = calculate_index(
        "NDVI",
        {"RED": BandData(INVALIDITY_RED, nodata=-9999.0), "NIR": INVALIDITY_NIR},
    )

    np.testing.assert_array_equal(
        expected_mask,
        np.array(
            [[True, False, False, False], [False, True, False, False]],
            dtype=np.bool_,
        ),
    )
    np.testing.assert_array_equal(result.valid_mask, expected_mask)
    _assert_scientific_values(result.values, expected)
    assert result.values[1, 1] == pytest.approx(1 / 3, rel=VALUE_RTOL, abs=VALUE_ATOL)


@pytest.mark.parametrize(
    ("index_name", "bands", "parameters"),
    [
        ("NDVI", {"NIR": [[-2.0]], "RED": [[2.0]]}, None),
        ("GNDVI", {"NIR": [[-2.0]], "GREEN": [[2.0]]}, None),
        ("SAVI", {"NIR": [[-2.5]], "RED": [[2.0]]}, {"L": 0.5}),
        ("CIGREEN", {"NIR": [[2.0]], "GREEN": [[0.0]]}, None),
        ("CIRED_EDGE", {"NIR": [[2.0]], "RED_EDGE": [[0.0]]}, None),
        ("NDRE", {"NIR": [[-2.0]], "RED_EDGE": [[2.0]]}, None),
        ("MCARI", {"GREEN": [[1.0]], "RED": [[0.0]], "RED_EDGE": [[2.0]]}, None),
        ("VARI", {"GREEN": [[2.0]], "RED": [[3.0]], "BLUE": [[5.0]]}, None),
    ],
)
def test_each_denominator_formula_leaves_zero_denominator_invalid(
    index_name: str,
    bands: Mapping[str, npt.ArrayLike],
    parameters: Mapping[str, float] | None,
) -> None:
    result = calculate_index(index_name, bands, parameters=parameters)

    assert not result.valid_mask[0, 0]
    assert np.isnan(result.values[0, 0])
    assert result.statistics.count == 0


@pytest.mark.parametrize(
    ("index_name", "bands", "missing_name"),
    [
        ("NDVI", {"RED": np.ones((2, 2))}, "NIR"),
        ("GNDVI", {"NIR": np.ones((2, 2))}, "GREEN"),
        ("SAVI", {"RED": np.ones((2, 2))}, "NIR"),
        ("CIGREEN", {"NIR": np.ones((2, 2))}, "GREEN"),
        ("CIRED_EDGE", {"NIR": np.ones((2, 2))}, "RED_EDGE"),
        ("NDRE", {"NIR": np.ones((2, 2))}, "RED_EDGE"),
        (
            "MCARI",
            {"GREEN": np.ones((2, 2)), "RED_EDGE": np.ones((2, 2))},
            "RED",
        ),
        ("VARI", {"GREEN": np.ones((2, 2)), "RED": np.ones((2, 2))}, "BLUE"),
        ("EXG", {"GREEN": np.ones((2, 2)), "BLUE": np.ones((2, 2))}, "RED"),
    ],
)
def test_each_formula_rejects_missing_required_bands(
    index_name: str,
    bands: Mapping[str, npt.ArrayLike],
    missing_name: str,
) -> None:
    with pytest.raises(MissingBandError, match=missing_name):
        calculate_index(index_name, bands)


def test_incompatible_band_and_roi_dimensions_are_rejected() -> None:
    with pytest.raises(IncompatibleDimensionsError, match="dimensions differ"):
        calculate_index(
            "NDVI",
            {"RED": np.ones((2, 3)), "NIR": np.ones((3, 2))},
        )
    with pytest.raises(IncompatibleDimensionsError, match="ROI shape"):
        calculate_index(
            "NDVI",
            {"RED": np.ones((2, 3)), "NIR": np.ones((2, 3))},
            roi=np.ones((3, 2), dtype=np.bool_),
        )


def test_statistics_match_numpy_population_statistics_and_linear_percentiles() -> None:
    green = np.array([[0.5, 1.0, 1.5], [2.5, 4.0, 8.0]], dtype=np.float32)
    zeros = np.zeros_like(green)
    roi = np.array([[True, False, True], [True, True, True]], dtype=np.bool_)
    result = calculate_index(
        "EXG",
        {"GREEN": green, "RED": zeros, "BLUE": zeros},
        roi=roi,
        formula_version="1.0.0",
    )
    persisted_reference = (2 * green.astype(np.float64)).astype(np.float32)[roi].astype(np.float64)
    percentiles = np.percentile(persisted_reference, [5, 25, 75, 95], method="linear")
    statistics = result.statistics

    assert statistics.count == persisted_reference.size
    assert statistics.mean == pytest.approx(
        np.mean(persisted_reference), rel=STAT_RTOL, abs=STAT_ATOL
    )
    assert statistics.median == pytest.approx(
        np.median(persisted_reference), rel=STAT_RTOL, abs=STAT_ATOL
    )
    assert statistics.min == pytest.approx(np.min(persisted_reference), abs=STAT_ATOL)
    assert statistics.max == pytest.approx(np.max(persisted_reference), abs=STAT_ATOL)
    assert statistics.std == pytest.approx(
        np.std(persisted_reference, ddof=0), rel=STAT_RTOL, abs=STAT_ATOL
    )
    assert statistics.p05 == pytest.approx(percentiles[0], rel=STAT_RTOL, abs=STAT_ATOL)
    assert statistics.p25 == pytest.approx(percentiles[1], rel=STAT_RTOL, abs=STAT_ATOL)
    assert statistics.p75 == pytest.approx(percentiles[2], rel=STAT_RTOL, abs=STAT_ATOL)
    assert statistics.p95 == pytest.approx(percentiles[3], rel=STAT_RTOL, abs=STAT_ATOL)
