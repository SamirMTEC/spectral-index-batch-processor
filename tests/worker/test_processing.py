"""Raster-window processing verifies ROI, values, masks, and statistics."""

from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import rasterio
from affine import Affine
from spectral_engine import MissingBandError
from spectral_worker.processing import ProcessingItemProcessor


def write_band(path: Path, values: np.ndarray) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype=str(values.dtype),
        crs="EPSG:4326",
        transform=Affine.translation(-40, -12) @ Affine.scale(0.01, -0.01),
    ) as output:
        output.write(values, 1)


def test_windowed_item_calculation_applies_roi_and_preserves_grid(tmp_path: Path) -> None:
    red_path = tmp_path / "red.tif"
    nir_path = tmp_path / "nir.tif"
    output_path = tmp_path / "ndvi.tif"
    red = np.ones((4, 4), dtype=np.uint16)
    nir = np.full((4, 4), 3, dtype=np.uint16)
    write_band(red_path, red)
    write_band(nir_path, nir)
    processor = object.__new__(ProcessingItemProcessor)
    item = {
        "id": uuid4(),
        "index_name": "NDVI",
        "parameters_snapshot": {},
        "pixel_coordinates": [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]],
    }

    statistics, metadata = processor._calculate_windowed(
        item,
        {},
        {"RED": red_path, "NIR": nir_path},
        output_path,
        tmp_path,
    )

    with rasterio.open(output_path) as result:
        values = result.read(1)
        mask = result.read_masks(1) > 0
        expected_mask = np.array(
            [
                [True, True, False, False],
                [True, True, False, False],
                [False, False, False, False],
                [False, False, False, False],
            ]
        )
        np.testing.assert_array_equal(mask, expected_mask)
        np.testing.assert_allclose(values[mask], 0.5, rtol=1e-6, atol=1e-7)
        assert np.isnan(values[~mask]).all()
        assert result.dtypes == ("float32",)
        assert result.crs is not None and result.crs.to_string() == "EPSG:4326"

    assert statistics.count == 4
    assert statistics.mean == 0.5
    assert statistics.median == 0.5
    assert metadata["valid_pixel_count"] == 4
    assert metadata["invalid_pixel_count"] == 12


def test_windowed_item_calculation_rasterizes_polygon_by_pixel_center(tmp_path: Path) -> None:
    red_path = tmp_path / "red-polygon.tif"
    nir_path = tmp_path / "nir-polygon.tif"
    output_path = tmp_path / "ndvi-polygon.tif"
    red = np.arange(1, 17, dtype=np.uint16).reshape(4, 4)
    nir = red * np.uint16(3)
    write_band(red_path, red)
    write_band(nir_path, nir)
    processor = object.__new__(ProcessingItemProcessor)
    item = {
        "id": uuid4(),
        "index_name": "NDVI",
        "parameters_snapshot": {},
        "pixel_coordinates": [[0, 0], [4, 0], [0, 4], [0, 0]],
    }
    expected_mask = np.array(
        [
            [True, True, True, True],
            [True, True, True, False],
            [True, True, False, False],
            [True, False, False, False],
        ],
        dtype=np.bool_,
    )

    statistics, metadata = processor._calculate_windowed(
        item,
        {},
        {"RED": red_path, "NIR": nir_path},
        output_path,
        tmp_path,
    )

    with rasterio.open(output_path) as result:
        values = result.read(1)
        mask = result.read_masks(1) > 0
        np.testing.assert_array_equal(mask, expected_mask)
        np.testing.assert_allclose(values[mask], 0.5, rtol=1e-6, atol=1e-7)
        assert np.isnan(values[~mask]).all()

    assert statistics.count == 10
    assert statistics.mean == 0.5
    assert metadata["valid_pixel_count"] == 10
    assert metadata["invalid_pixel_count"] == 6


def test_windowed_item_calculation_processes_complete_image_without_roi(tmp_path: Path) -> None:
    red_path = tmp_path / "red-full.tif"
    nir_path = tmp_path / "nir-full.tif"
    output_path = tmp_path / "ndvi-full.tif"
    red = np.ones((4, 4), dtype=np.uint16)
    nir = np.full((4, 4), 3, dtype=np.uint16)
    write_band(red_path, red)
    write_band(nir_path, nir)
    processor = object.__new__(ProcessingItemProcessor)
    item = {
        "id": uuid4(),
        "index_name": "NDVI",
        "analysis_scope": "full_image",
        "parameters_snapshot": {},
        "pixel_coordinates": None,
    }

    statistics, metadata = processor._calculate_windowed(
        item,
        {},
        {"RED": red_path, "NIR": nir_path},
        output_path,
        tmp_path,
    )

    with rasterio.open(output_path) as result:
        mask = result.read_masks(1) > 0
        np.testing.assert_array_equal(mask, np.ones((4, 4), dtype=np.bool_))
        np.testing.assert_allclose(result.read(1), 0.5, rtol=1e-6, atol=1e-7)
        assert result.tags()["SPECTRAL_ANALYSIS_SCOPE"] == "full_image"

    assert statistics.count == 16
    assert statistics.mean == 0.5
    assert metadata["valid_pixel_count"] == 16
    assert metadata["invalid_pixel_count"] == 0


@pytest.mark.parametrize(
    ("index_name", "denominator_band", "expected"),
    [("CIGREEN", "GREEN", 0.5), ("CIRED_EDGE", "RED_EDGE", 0.5)],
)
def test_windowed_chlorophyll_indices_use_required_band_and_preserve_grid(
    tmp_path: Path,
    index_name: str,
    denominator_band: str,
    expected: float,
) -> None:
    denominator_path = tmp_path / f"{denominator_band.lower()}.tif"
    nir_path = tmp_path / "nir.tif"
    output_path = tmp_path / f"{index_name.lower()}.tif"
    write_band(denominator_path, np.full((3, 3), 2, dtype=np.uint16))
    write_band(nir_path, np.full((3, 3), 3, dtype=np.uint16))
    processor = object.__new__(ProcessingItemProcessor)
    item = {
        "id": uuid4(),
        "index_name": index_name,
        "analysis_scope": "full_image",
        "parameters_snapshot": {},
        "pixel_coordinates": None,
    }

    statistics, metadata = processor._calculate_windowed(
        item,
        {},
        {"NIR": nir_path, denominator_band: denominator_path},
        output_path,
        tmp_path,
    )

    with rasterio.open(output_path) as result:
        np.testing.assert_allclose(result.read(1), expected, rtol=1e-6, atol=1e-7)
        assert result.dtypes == ("float32",)
        assert result.tags()["SPECTRAL_INDEX"] == index_name
    assert statistics.count == 9
    assert statistics.mean == expected
    assert metadata["valid_pixel_count"] == 9


def test_windowed_mcari_uses_red_edge_and_masks_zero_red(tmp_path: Path) -> None:
    paths = {
        "GREEN": tmp_path / "green-mcari.tif",
        "RED": tmp_path / "red-mcari.tif",
        "RED_EDGE": tmp_path / "red-edge-mcari.tif",
    }
    write_band(paths["GREEN"], np.array([[1, 2], [3, 4]], dtype=np.uint16))
    write_band(paths["RED"], np.array([[2, 2], [4, 0]], dtype=np.uint16))
    write_band(paths["RED_EDGE"], np.array([[4, 3], [6, 10]], dtype=np.uint16))
    output_path = tmp_path / "mcari.tif"
    processor = object.__new__(ProcessingItemProcessor)
    item = {
        "id": uuid4(),
        "index_name": "MCARI",
        "analysis_scope": "full_image",
        "parameters_snapshot": {},
        "pixel_coordinates": None,
    }

    statistics, metadata = processor._calculate_windowed(item, {}, paths, output_path, tmp_path)

    with rasterio.open(output_path) as result:
        values = result.read(1)
        np.testing.assert_allclose(values[0, :], [2.8, 1.2], rtol=1e-6, atol=1e-7)
        np.testing.assert_allclose(values[1, 0], 2.1, rtol=1e-6, atol=1e-7)
        assert np.isnan(values[1, 1])
    assert statistics.count == 3
    assert metadata["valid_pixel_count"] == 3


async def test_versioned_item_never_falls_back_to_current_band_associations() -> None:
    processor = object.__new__(ProcessingItemProcessor)
    with pytest.raises(MissingBandError, match="frozen"):
        await processor._assets(
            {
                "index_name": "NDVI",
                "formula_version": "1.0.0",
                "input_snapshot": [],
            }
        )
