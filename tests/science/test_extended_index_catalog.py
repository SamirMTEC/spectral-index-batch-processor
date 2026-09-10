"""Independent numerical validation for the complete five-band index catalog."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import numpy.typing as npt
import pytest
from spectral_common import CURRENT_FORMULA_VERSIONS, FORMULA_DEFINITIONS
from spectral_engine import BandData, InvalidParameterError, calculate_index

RTOL = 1e-6
ATOL = 1e-7


@pytest.fixture
def reflectance_bands() -> dict[str, npt.NDArray[np.float64]]:
    return {
        "BLUE": np.array([[0.10, 0.20], [0.15, 0.25]], dtype=np.float64),
        "GREEN": np.array([[0.30, 0.40], [0.35, 0.45]], dtype=np.float64),
        "RED": np.array([[0.20, 0.30], [0.25, 0.40]], dtype=np.float64),
        "RED_EDGE": np.array([[0.45, 0.50], [0.40, 0.55]], dtype=np.float64),
        "NIR": np.array([[0.70, 0.80], [0.65, 0.90]], dtype=np.float64),
    }


def _reference(name: str, bands: Mapping[str, npt.NDArray[np.float64]]) -> npt.NDArray[np.float64]:
    blue = bands["BLUE"]
    green = bands["GREEN"]
    red = bands["RED"]
    red_edge = bands["RED_EDGE"]
    nir = bands["NIR"]
    ndvi = (nir - red) / (nir + red)
    total = red + green + blue
    normalized_red = red / total
    normalized_green = green / total
    normalized_blue = blue / total
    references: dict[str, npt.NDArray[np.float64]] = {
        "NDVI": ndvi,
        "GNDVI": (nir - green) / (nir + green),
        "NDRE": (nir - red_edge) / (nir + red_edge),
        "CIGREEN": nir / green - 1,
        "CIRED_EDGE": nir / red_edge - 1,
        "DVI": nir - red,
        "RVI": nir / red,
        "IPVI": nir / (nir + red),
        "RDVI": (nir - red) / np.sqrt(nir + red),
        "SAVI": ((nir - red) / (nir + red + 0.5)) * 1.5,
        "OSAVI": 1.16 * (nir - red) / (nir + red + 0.16),
        "MSAVI2": (2 * nir + 1 - np.sqrt((2 * nir + 1) ** 2 - 8 * (nir - red))) / 2,
        "TVI": np.sqrt(ndvi + 0.5),
        "CTVI": np.sign(ndvi + 0.5) * np.sqrt(np.abs(ndvi + 0.5)),
        "NIRV": ndvi * nir,
        "EVI": 2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1),
        "EVI2": 2.5 * (nir - red) / (nir + 2.4 * red + 1),
        "ARVI": (nir - (2 * red - blue)) / (nir + (2 * red - blue)),
        "MNDVI": (nir - red) / (nir + red - 2 * blue),
        "SIPI": (nir - blue) / (nir - red),
        "NPCI": (red - blue) / (red + blue),
        "CI": (red - blue) / red,
        "CVI": (nir * red) / green**2,
        "GLI": (2 * green - red - blue) / (2 * green + red + blue),
        "NGRDI": (green - red) / (green + red),
        "RGBVI": (green**2 - red * blue) / (green**2 + red * blue),
        "MGRVI": (green**2 - red**2) / (green**2 + red**2),
        "VARI": (green - red) / (green + red - blue),
        "EXG": 2 * normalized_green - normalized_red - normalized_blue,
        "EXR": 1.4 * normalized_red - normalized_green,
        "EXGR": (
            2 * normalized_green
            - normalized_red
            - normalized_blue
            - (1.4 * normalized_red - normalized_green)
        ),
        "TGI": green - 0.39 * red - 0.61 * blue,
        "RRI1": nir / red_edge,
        "ARI": 1 / green - 1 / red_edge,
        "MARI": nir * (1 / green - 1 / red_edge),
        "MCARI": ((red_edge - red) - 0.2 * (red_edge - green)) * (red_edge / red),
        "MCARI1": 1.2 * (2.5 * (nir - red) - 1.3 * (nir - green)),
        "MCARI2": (
            1.5
            * (2.5 * (nir - red) - 1.3 * (nir - green))
            / np.sqrt((2 * nir + 1) ** 2 - (6 * nir - 5 * np.sqrt(red)) - 0.5)
        ),
        "PSRI": (red - green) / nir,
    }
    return references[name]


@pytest.mark.parametrize("index_name", sorted(CURRENT_FORMULA_VERSIONS))
def test_each_current_index_matches_independent_numpy_reference(
    index_name: str,
    reflectance_bands: dict[str, npt.NDArray[np.float64]],
) -> None:
    definition = FORMULA_DEFINITIONS[(index_name, CURRENT_FORMULA_VERSIONS[index_name])]
    required = {name: reflectance_bands[name] for name in definition.required_bands}

    result = calculate_index(index_name, required)
    expected = _reference(index_name, reflectance_bands).astype(np.float32)

    assert result.values.dtype == np.float32
    np.testing.assert_array_equal(result.valid_mask, np.ones((2, 2), dtype=np.bool_))
    np.testing.assert_allclose(result.values, expected, rtol=RTOL, atol=ATOL)


@pytest.mark.parametrize("index_name", sorted(CURRENT_FORMULA_VERSIONS))
def test_each_index_respects_explicit_reflectance_scale(
    index_name: str,
    reflectance_bands: dict[str, npt.NDArray[np.float64]],
) -> None:
    definition = FORMULA_DEFINITIONS[(index_name, CURRENT_FORMULA_VERSIONS[index_name])]
    reflectance = {name: reflectance_bands[name] for name in definition.required_bands}
    scaled = {name: value * 10_000 for name, value in reflectance.items()}

    direct = calculate_index(index_name, reflectance)
    converted = calculate_index(index_name, scaled, parameters={"reflectance_scale": 10_000})

    np.testing.assert_array_equal(converted.valid_mask, direct.valid_mask)
    np.testing.assert_allclose(converted.values, direct.values, rtol=RTOL, atol=ATOL)


@pytest.mark.parametrize("index_name", sorted(CURRENT_FORMULA_VERSIONS))
def test_each_index_preserves_nodata_nan_and_infinity(
    index_name: str,
    reflectance_bands: dict[str, npt.NDArray[np.float64]],
) -> None:
    definition = FORMULA_DEFINITIONS[(index_name, CURRENT_FORMULA_VERSIONS[index_name])]
    bands: dict[str, BandData | npt.NDArray[np.float64]] = {}
    for position, band_name in enumerate(definition.required_bands):
        source = np.repeat(reflectance_bands[band_name][0, 0], 4).reshape(1, 4)
        if position == 0:
            source = np.array([[source[0, 0], -9999.0, np.nan, np.inf]])
            bands[band_name] = BandData(source, nodata=-9999.0)
        else:
            bands[band_name] = source

    result = calculate_index(index_name, bands)

    np.testing.assert_array_equal(result.valid_mask, [[True, False, False, False]])
    assert np.isfinite(result.values[0, 0])
    assert np.isnan(result.values[0, 1:]).all()


@pytest.mark.parametrize(
    ("index_name", "bands"),
    [
        ("RVI", {"NIR": [[1.0]], "RED": [[0.0]]}),
        ("IPVI", {"NIR": [[1.0]], "RED": [[-1.0]]}),
        ("RDVI", {"NIR": [[1.0]], "RED": [[-1.0]]}),
        ("OSAVI", {"NIR": [[0.1]], "RED": [[-0.26]]}),
        ("CTVI", {"NIR": [[1.0]], "RED": [[3.0]]}),
        ("EVI", {"NIR": [[0.5]], "RED": [[0.1]], "BLUE": [[0.28]]}),
        ("EVI2", {"NIR": [[0.5]], "RED": [[-0.625]]}),
        ("ARVI", {"NIR": [[0.5]], "RED": [[0.2]], "BLUE": [[0.9]]}),
        ("MNDVI", {"NIR": [[0.5]], "RED": [[0.3]], "BLUE": [[0.4]]}),
        ("SIPI", {"NIR": [[0.5]], "RED": [[0.5]], "BLUE": [[0.1]]}),
        ("NPCI", {"RED": [[1.0]], "BLUE": [[-1.0]]}),
        ("CI", {"RED": [[0.0]], "BLUE": [[1.0]]}),
        ("CVI", {"NIR": [[1.0]], "RED": [[1.0]], "GREEN": [[0.0]]}),
        ("GLI", {"GREEN": [[1.0]], "RED": [[-1.0]], "BLUE": [[-1.0]]}),
        ("NGRDI", {"GREEN": [[1.0]], "RED": [[-1.0]]}),
        ("RGBVI", {"GREEN": [[1.0]], "RED": [[1.0]], "BLUE": [[-1.0]]}),
        ("MGRVI", {"GREEN": [[0.0]], "RED": [[0.0]]}),
        ("EXG", {"GREEN": [[1.0]], "RED": [[1.0]], "BLUE": [[-2.0]]}),
        ("EXR", {"GREEN": [[1.0]], "RED": [[1.0]], "BLUE": [[-2.0]]}),
        ("EXGR", {"GREEN": [[1.0]], "RED": [[1.0]], "BLUE": [[-2.0]]}),
        ("RRI1", {"NIR": [[1.0]], "RED_EDGE": [[0.0]]}),
        ("ARI", {"GREEN": [[0.0]], "RED_EDGE": [[1.0]]}),
        ("MARI", {"NIR": [[1.0]], "GREEN": [[1.0]], "RED_EDGE": [[0.0]]}),
        ("PSRI", {"RED": [[1.0]], "GREEN": [[0.5]], "NIR": [[0.0]]}),
    ],
)
def test_zero_or_near_zero_denominators_remain_invalid(
    index_name: str, bands: Mapping[str, npt.ArrayLike]
) -> None:
    result = calculate_index(index_name, bands)

    assert not result.valid_mask[0, 0]
    assert np.isnan(result.values[0, 0])
    assert result.statistics.count == 0


@pytest.mark.parametrize(
    ("index_name", "bands"),
    [
        ("RDVI", {"NIR": [[-2.0]], "RED": [[1.0]]}),
        ("TVI", {"NIR": [[0.1]], "RED": [[1.0]]}),
        ("MSAVI2", {"NIR": [[0.5]], "RED": [[-1.0]]}),
        ("MCARI2", {"NIR": [[0.5]], "RED": [[-1.0]], "GREEN": [[0.3]]}),
    ],
)
def test_negative_square_root_arguments_remain_invalid(
    index_name: str, bands: Mapping[str, npt.ArrayLike]
) -> None:
    result = calculate_index(index_name, bands)

    assert not result.valid_mask[0, 0]
    assert np.isnan(result.values[0, 0])


def test_aliases_share_one_canonical_implementation(
    reflectance_bands: dict[str, npt.NDArray[np.float64]],
) -> None:
    canonical = calculate_index("RVI", reflectance_bands)
    simple_ratio = calculate_index("Simple Ratio", reflectance_bands)
    short_alias = calculate_index("SR", reflectance_bands)

    assert canonical.index_name == simple_ratio.index_name == short_alias.index_name == "RVI"
    np.testing.assert_array_equal(canonical.values, simple_ratio.values)
    np.testing.assert_array_equal(canonical.values, short_alias.values)
    assert len(CURRENT_FORMULA_VERSIONS) == 39


def test_rededge_band_public_spelling_maps_to_internal_red_edge() -> None:
    result = calculate_index("NDRE", {"NIR": [[0.8]], "REDEDGE": [[0.4]]})

    np.testing.assert_allclose(result.values, [[1 / 3]], rtol=RTOL, atol=ATOL)


def test_configurable_arvi_gamma_is_applied() -> None:
    bands = {"NIR": [[0.8]], "RED": [[0.3]], "BLUE": [[0.1]]}
    result = calculate_index("ARVI", bands, parameters={"gamma": 0.5})
    corrected_red = 0.3 - 0.5 * (0.1 - 0.3)
    expected = (0.8 - corrected_red) / (0.8 + corrected_red)

    np.testing.assert_allclose(result.values, [[expected]], rtol=RTOL, atol=ATOL)


def test_near_zero_denominator_is_not_treated_as_scientific_data() -> None:
    denominator = np.finfo(np.float64).eps / 2
    result = calculate_index("RVI", {"NIR": [[1.0]], "RED": [[denominator]]})

    assert not result.valid_mask[0, 0]
    assert np.isnan(result.values[0, 0])


def test_exg_historical_version_remains_reproducible_and_current_is_normalized() -> None:
    bands = {"RED": np.array([[1.0]]), "GREEN": np.array([[2.0]]), "BLUE": np.array([[1.0]])}

    historical = calculate_index("EXG", bands, formula_version="1.0.0")
    current = calculate_index("EXG", bands)

    np.testing.assert_allclose(historical.values, [[2.0]], rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(current.values, [[0.5]], rtol=RTOL, atol=ATOL)
    assert historical.formula_version == "1.0.0"
    assert current.formula_version == "2.0.0"


def test_invalid_reflectance_scale_is_rejected() -> None:
    with pytest.raises(InvalidParameterError, match="greater than zero"):
        calculate_index(
            "EVI2", {"NIR": [[0.8]], "RED": [[0.2]]}, parameters={"reflectance_scale": 0}
        )


def test_scale_is_never_inferred_from_large_pixel_values() -> None:
    scaled = {"NIR": [[8_000.0]], "RED": [[2_000.0]]}
    explicit = calculate_index("EVI2", scaled, parameters={"reflectance_scale": 10_000})
    unscaled = calculate_index("EVI2", scaled)

    assert explicit.values[0, 0] != pytest.approx(unscaled.values[0, 0])
