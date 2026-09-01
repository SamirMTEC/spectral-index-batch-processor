"""Raster and ZIP inspection tests using real files."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pytest
import rasterio
from affine import Affine
from spectral_worker.inspection import (
    InvalidAssetError,
    build_preview_png,
    extract_zip_member,
    inspect_raster,
    open_validated_zip,
    sanitize_display_filename,
    sha256_file,
)


def write_tiff(path: Path) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=3,
        count=2,
        dtype="uint16",
        crs="EPSG:4326",
        transform=Affine.translation(-40, -12) @ Affine.scale(0.01, -0.01),
        nodata=0,
    ) as output:
        output.write(np.ones((2, 3, 4), dtype=np.uint16))
        output.update_tags(sensor="test-sensor")


def test_valid_geotiff_metadata_and_checksum(tmp_path: Path) -> None:
    path = tmp_path / "capture.tif"
    write_tiff(path)
    metadata = inspect_raster(path)
    assert metadata.filename == "capture.tif"
    assert metadata.width == 4
    assert metadata.height == 3
    assert metadata.dtype == "uint16,uint16"
    assert metadata.band_count == 2
    assert metadata.nodata == "0.0"
    assert metadata.crs == "EPSG:4326"
    assert metadata.transform is not None
    assert metadata.metadata["sensor"] == "test-sensor"
    assert len(sha256_file(path)) == 64

    preview, valid_fraction = build_preview_png(path)
    assert preview.startswith(b"\x89PNG\r\n\x1a\n")
    assert valid_fraction == 1.0


def test_corrupt_tiff_is_an_individual_invalid_asset(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.tif"
    path.write_bytes(b"not-a-tiff")
    with pytest.raises(InvalidAssetError) as caught:
        inspect_raster(path)
    assert caught.value.code == "content_type_mismatch"


def test_truncated_tiff_with_valid_signature_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "truncated.tif"
    path.write_bytes(b"II*\x00invalid-directory")
    with pytest.raises(InvalidAssetError) as caught:
        inspect_raster(path)
    assert caught.value.code == "corrupt_image"


def test_zip_rejects_path_traversal(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.zip"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("../escape.tif", b"bytes")
    with pytest.raises(InvalidAssetError) as caught:
        open_validated_zip(path)
    assert caught.value.code == "zip_unsafe_path"


@pytest.mark.parametrize(
    "member_name",
    ["/absolute.tif", "C:/windows.tif", "a/../../escape.tif", "a/./image.tif", "a/\x01.tif"],
)
def test_zip_rejects_absolute_traversal_and_control_paths(tmp_path: Path, member_name: str) -> None:
    path = tmp_path / "unsafe-name.zip"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(member_name, b"bytes")
    with pytest.raises(InvalidAssetError) as caught:
        open_validated_zip(path)
    assert caught.value.code == "zip_unsafe_path"


def test_zip_rejects_duplicate_casefolded_paths(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.zip"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("plot/BAND.tif", b"first")
        archive.writestr("plot/band.TIF", b"second")
    with pytest.raises(InvalidAssetError) as caught:
        open_validated_zip(path)
    assert caught.value.code == "zip_duplicate_path"


def test_zip_bomb_ratio_entry_and_total_limits_are_enforced(tmp_path: Path) -> None:
    path = tmp_path / "compressed.zip"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("zeros.tif", b"\x00" * (1024 * 1024))

    with pytest.raises(InvalidAssetError) as ratio_error:
        open_validated_zip(path, max_compression_ratio=2)
    assert ratio_error.value.code == "zip_compression_ratio"

    with pytest.raises(InvalidAssetError) as entry_error:
        open_validated_zip(path, max_entry_bytes=1024)
    assert entry_error.value.code == "zip_entry_size_limit"

    with pytest.raises(InvalidAssetError) as total_error:
        open_validated_zip(path, max_uncompressed_bytes=1024)
    assert total_error.value.code == "zip_expansion_limit"


def test_streaming_zip_extraction_enforces_actual_byte_budget(tmp_path: Path) -> None:
    path = tmp_path / "bounded.zip"
    target = tmp_path / "entry.tif"
    with ZipFile(path, "w", ZIP_DEFLATED) as writer:
        writer.writestr("entry.tif", b"x" * 4096)
    with ZipFile(path) as archive:
        member = archive.infolist()[0]
        with pytest.raises(InvalidAssetError) as caught:
            extract_zip_member(archive, member, target, max_bytes=1024)
    assert caught.value.code == "zip_entry_size_limit"
    assert not target.exists()


def test_extension_cannot_disguise_another_supported_format(tmp_path: Path) -> None:
    path = tmp_path / "renamed.tif"
    with rasterio.open(
        path,
        "w",
        driver="PNG",
        width=2,
        height=2,
        count=1,
        dtype="uint8",
    ) as output:
        output.write(np.ones((1, 2, 2), dtype=np.uint8))
    with pytest.raises(InvalidAssetError) as caught:
        inspect_raster(path)
    assert caught.value.code == "content_type_mismatch"


def test_raster_pixel_and_decoded_size_limits_are_enforced(tmp_path: Path) -> None:
    path = tmp_path / "limited.tif"
    write_tiff(path)
    with pytest.raises(InvalidAssetError) as pixel_error:
        inspect_raster(path, max_pixels=11)
    assert pixel_error.value.code == "raster_pixel_limit"

    with pytest.raises(InvalidAssetError) as size_error:
        inspect_raster(path, max_decoded_bytes=47)
    assert size_error.value.code == "raster_decoded_size_limit"


def test_malicious_display_filename_is_reduced_to_a_safe_basename() -> None:
    assert sanitize_display_filename("folder/<img src=x onerror=alert(1)>.tif") == (
        "_img src_x onerror_alert(1)_.tif"
    )
