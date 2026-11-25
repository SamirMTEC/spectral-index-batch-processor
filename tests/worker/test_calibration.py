"""Real-image ROI reading and clearly synthetic export regression tests."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType
from uuid import uuid4
from zipfile import ZipFile

import numpy as np
import pytest
import rasterio
from spectral_engine.calibration import parse_raster_metadata, roi_statistics
from spectral_worker.calibration import CalibrationProcessor, _geometry_window
from spectral_worker.inspection import build_preview_png


def test_synthetic_batch_uses_each_capture_exposure_and_exports_qa(tmp_path: Path) -> None:
    """Synthetic mathematical test; these values are not measured reflectance."""
    codes = ("BLUE", "GREEN", "RED", "RED_EDGE", "NIR")
    source = tmp_path / "synthetic-original.tif"
    with rasterio.open(
        source, "w", driver="GTiff", width=16, height=16, count=1, dtype="uint16"
    ) as target:
        target.write(np.full((16, 16), 100, dtype="uint16"), 1)
    base_metadata = {
        "manufacturer": "DJI",
        "model": "FC6360",
        "sensor_gain": 1,
        "exposure_us": 1000,
        "black_level_dn": 0,
        "vignetting_coefficients": [0, 0, 0, 0, 0, 0],
        "vignetting_center_px": [8, 8],
        "relative_optical_center_px": [0, 0],
        "flight_speed_mps": [0, 0, 0],
    }
    panel_manifest = {
        "bands": [
            {
                "band": code,
                "filename": f"panel-{code}.tif",
                "metadata": {**base_metadata, "band": code},
            }
            for code in codes
        ],
        "targets": [{"capture_id": "plant-1"}],
    }
    config = {
        "panel_snapshot": {
            "name": "SYNTHETIC TEST ONLY",
            "source": {"kind": "synthetic", "certified": False},
            "values": {"Cinza-claro": {code: 0.2 for code in codes}},
        },
        "rois": [
            {
                "surface": "Cinza-claro",
                "band": code,
                "role": "fit",
                "coordinates": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
            }
            for code in codes
        ],
        "method": "proportional",
        "input_mode": "p4_signal",
        "reference_band": "NIR",
        "exclude_saturated": True,
        "edge_inset_percent": 0,
        "weighted": False,
        "robust": False,
        "max_cv": None,
        "max_saturation_percent": None,
        "max_validation_rmse": None,
    }
    processor = object.__new__(CalibrationProcessor)
    result = processor._calculate(
        config, panel_manifest, {code: source for code in codes}, tmp_path
    )
    assert all(result["bands"][code]["model"]["intercept"] == 0 for code in codes)
    assert result["radiometric_validation_status"] == "not_established"

    plant = tmp_path / "synthetic-plant.tif"
    with rasterio.open(
        plant, "w", driver="GTiff", width=16, height=16, count=1, dtype="uint16"
    ) as target:
        target.write(np.full((16, 16), 200, dtype="uint16"), 1)

    class LocalStore:
        def download_file(
            self, bucket: str, key: str, destination: str, ExtraArgs: object = None
        ) -> None:
            shutil.copyfile(plant, destination)

    processor.s3 = LocalStore()
    processor.settings = type("SettingsStub", (), {"s3_bucket": "test"})()
    capture = {
        "capture_id": "plant-1",
        "capture_key": "SYNTHETIC-PLANT",
        "bands": [
            {
                "band": code,
                "filename": f"plant-{code}.tif",
                "metadata": {**base_metadata, "band": code, "exposure_us": 2000},
                "object_key": code,
            }
            for code in codes
        ],
    }
    transferred = asyncio.run(
        processor._transfer_capture(config, capture, result["bands"], tmp_path)
    )
    assert transferred["state"] == "completed"
    for code in codes:
        with rasterio.open(tmp_path / "captures" / "plant-1" / f"reflectance-{code}.tif") as output:
            assert output.read(1)[8, 8] == pytest.approx(0.2, abs=1e-6)
        with rasterio.open(tmp_path / "captures" / "plant-1" / f"quality-{code}.tif") as quality:
            assert np.all(quality.read(1) == 0)
    assert (tmp_path / "captures" / "plant-1" / "QA.csv").exists()
    with rasterio.open(plant) as original:
        assert original.read(1)[8, 8] == 200
    result["targets"] = [transferred]
    result["target_summary"] = {"requested": 1, "completed": 1, "failed": 0, "skipped": 0}
    package = tmp_path / "synthetic-batch.zip"
    processor._make_bundle(tmp_path, result, package)
    with ZipFile(package) as archive:
        names = set(archive.namelist())
        for code in codes:
            assert f"captures/plant-1/reflectance-{code}.tif" in names
            assert f"captures/plant-1/quality-{code}.tif" in names
        assert "captures/plant-1/QA.csv" in names
        assert "captures/plant-1/calibration.json" in names
        assert not any(name.startswith("input-") or "/input-" in name for name in names)

    incomplete = {
        **capture,
        "capture_id": "plant-2",
        "bands": capture["bands"][:-1],
        "preflight_errors": ["Missing P4M band"],
    }
    failed = asyncio.run(processor._transfer_capture(config, incomplete, result["bands"], tmp_path))
    assert failed["state"] == "failed"
    assert (tmp_path / "captures" / "plant-2" / "QA.csv").exists()


@pytest.mark.parametrize("combined", [False, True])
def test_multiple_references_use_assigned_panel_for_each_target(
    tmp_path: Path, combined: bool
) -> None:
    """Synthetic signals verify the worker's reference routing and exported provenance."""
    originals = {}
    for name, dn in (("primary", 100), ("secondary", 200), ("plant", 200)):
        path = tmp_path / f"synthetic-{name}.tif"
        with rasterio.open(
            path, "w", driver="GTiff", width=16, height=16, count=1, dtype="uint16"
        ) as writer:
            writer.write(np.full((16, 16), dn, dtype="uint16"), 1)
        originals[name] = path

    codes = ("BLUE", "GREEN", "RED", "RED_EDGE", "NIR")
    metadata = {
        "manufacturer": "DJI",
        "model": "FC6360",
        "sensor_gain": 1,
        "exposure_us": 1000,
        "black_level_dn": 0,
        "vignetting_coefficients": [0, 0, 0, 0, 0, 0],
        "vignetting_center_px": [8, 8],
        "relative_optical_center_px": [0, 0],
        "flight_speed_mps": [0, 0, 0],
    }
    rois = [
        {
            "surface": "Cinza-escuro",
            "band": code,
            "role": "fit",
            "coordinates": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
        }
        for code in codes
    ]

    def bands(name: str) -> list[dict[str, object]]:
        return [
            {
                "band": code,
                "filename": f"{name}-{code}.tif",
                "object_key": f"{name}-{code}",
                "metadata": {**metadata, "band": code},
            }
            for code in codes
        ]

    manifest = {
        "bands": bands("primary"),
        "panel_capture": {"capture_id": "primary", "capture_key": "PRIMARY"},
        "additional_references": [
            {
                "capture_id": "secondary",
                "capture_key": "SECONDARY",
                "rois": rois,
                "bands": bands("secondary"),
            }
        ],
        "targets": [
            {
                "capture_id": "plant",
                "capture_key": "PLANT",
                "reference_capture_id": "combined" if combined else "secondary",
                "reference_capture_ids": ["primary", "secondary"] if combined else [],
                "bands": bands("plant"),
            }
        ],
    }
    config = {
        "reference_combination": "mean_coefficients" if combined else "per_target",
        "panel_snapshot": {
            "name": "SYNTHETIC TEST ONLY",
            "source": {"kind": "synthetic", "certified": False},
            "values": {"Cinza-escuro": {code: 0.2 for code in codes}},
        },
        "rois": rois,
        "method": "proportional",
        "input_mode": "p4_signal",
        "reference_band": "NIR",
        "exclude_saturated": True,
        "edge_inset_percent": 0,
        "weighted": False,
        "robust": False,
        "max_cv": None,
        "max_saturation_percent": None,
        "max_validation_rmse": None,
    }
    package = tmp_path / "synthetic-multiple-references.zip"

    class LocalStore:
        def download_file(
            self, bucket: str, key: str, destination: str, ExtraArgs: object = None
        ) -> None:
            shutil.copyfile(originals[key.split("-")[0]], destination)

        def upload_file(self, source: str, bucket: str, key: str, ExtraArgs: object = None) -> None:
            if key.endswith(".zip"):
                shutil.copyfile(source, package)

        def put_object(self, **kwargs: object) -> None:
            return None

    class Transaction:
        async def __aenter__(self) -> Transaction:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, *args: object, **kwargs: object) -> None:
            return None

    class Engine:
        def begin(self) -> Transaction:
            return Transaction()

    async def claim(self: CalibrationProcessor, run_id: object) -> dict[str, object]:
        return {
            "owner_id": "test-owner",
            "config_json": config,
            "manifest_json": manifest,
        }

    async def not_cancelled(self: CalibrationProcessor, run_id: object) -> bool:
        return False

    processor = object.__new__(CalibrationProcessor)
    processor.s3 = LocalStore()
    processor.engine = Engine()
    processor.settings = type("SettingsStub", (), {"s3_bucket": "test"})()
    processor._claim = MethodType(claim, processor)
    processor._cancel_requested = MethodType(not_cancelled, processor)
    asyncio.run(processor.process(uuid4()))

    with ZipFile(package) as archive:
        audit = json.loads(archive.read("audit.json"))
        assert {item["capture_id"] for item in audit["reference_captures"]} == {
            "primary",
            "secondary",
        }
        assert audit["targets"][0]["reference_capture_id"] == (
            "combined" if combined else "secondary"
        )
        assert audit["targets"][0]["bands"]["BLUE"]["object_key"].endswith(
            "/bands/plant/BLUE.tif"
        )
        assert len(audit["targets"][0]["bands"]["BLUE"]["checksum_sha256"]) == 64
        assert "reference-coefficients.csv" in archive.namelist()
        if combined:
            individual = [
                item["bands"]["BLUE"]["model"]["slope"] for item in audit["reference_captures"]
            ]
            assert audit["combined_models"]["BLUE"]["slope"] == pytest.approx(sum(individual) / 2)
            assert audit["combined_reference_check"]["BLUE"]["rmse"] > 0
            assert "combined-coefficients.csv" in archive.namelist()
            assert "mean_coefficients" in archive.read("coefficients.csv").decode("utf-8")
            assert "references/secondary/secondary-BLUE_calibrated.tif" in archive.namelist()
            assert "primary-BLUE_calibrated.tif" in archive.namelist()
            output_name = "captures/plant/plant-BLUE_calibrated.tif"
        else:
            assert "references/secondary/calibrated-BLUE.tif" in archive.namelist()
            output_name = "captures/plant/reflectance-BLUE.tif"
        with (
            rasterio.MemoryFile(archive.read(output_name)) as memory,
            memory.open() as output,
        ):
            assert output.read(1)[8, 8] == pytest.approx(0.3 if combined else 0.2, abs=1e-6)
            assert output.tags()["reference_capture_id"] == (
                "combined" if combined else "secondary"
            )
        if combined:
            with (
                rasterio.MemoryFile(archive.read("primary-BLUE_calibrated.tif")) as memory,
                memory.open() as output,
            ):
                assert output.read(1)[8, 8] == pytest.approx(0.15, abs=1e-6)
                assert output.tags()["reference_capture_ids"] == "primary,secondary"


def test_user_dji_tiffs_roi_digital_values() -> None:
    sample = Path(".local/calibration-sample")
    files = sorted(sample.glob("DJI_*.TIF"))
    if len(files) != 5:
        pytest.skip("User-provided DJI TIFFs are not part of the repository")
    band_names = set()
    capture_ids = set()
    # Rectangle well inside the white surface shown in the original NIR image.
    white_roi = [[0.13, 0.39], [0.19, 0.39], [0.19, 0.46], [0.13, 0.46]]
    for file in files:
        with rasterio.open(file) as source:
            metadata = parse_raster_metadata(
                source.tags(),
                source.tags(ns="EXIF"),
                source.tags(ns="xml:XMP").get("xml:XMP"),
                dtype=source.dtypes[0],
                count=source.count,
            )
            band_names.add(metadata["band"])
            capture_ids.add(metadata["capture_uuid"])
            window, mask = _geometry_window(white_roi, source.width, source.height, (0, 0), 5)
            values = source.read(1, window=window, masked=True)
            valid = mask & ~np.ma.getmaskarray(values)
            statistics = roi_statistics(values.data[valid])
            assert statistics["valid_pixels"] > 100
            assert statistics["median"] > 0
    assert band_names == {"BLUE", "GREEN", "RED", "RED_EDGE", "NIR"}
    assert len(capture_ids) == 1


def test_synthetic_export_is_labelled_and_preserves_source() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        original = root / "synthetic-input.tif"
        data = np.full((20, 20), 100, dtype=np.uint16)
        data[:, 10:] = 200
        with rasterio.open(
            original, "w", driver="GTiff", width=20, height=20, count=1, dtype="uint16"
        ) as target:
            target.write(data, 1)
        manifest = {
            "bands": [
                {
                    "band": "BLUE",
                    "filename": "synthetic-input.tif",
                    "sha256": "synthetic-test",
                    "metadata": {"band": "BLUE", "relative_optical_center_px": [0, 0]},
                }
            ]
        }
        config = {
            "panel_snapshot": {
                "name": "SYNTHETIC TEST ONLY",
                "source": {"kind": "synthetic", "certified": False},
                "values": {"Preto": {"BLUE": 0.1}, "Branco": {"BLUE": 0.8}},
            },
            "rois": [
                {
                    "surface": "Preto",
                    "band": None,
                    "role": "fit",
                    "coordinates": [[0.1, 0.1], [0.4, 0.1], [0.4, 0.9], [0.1, 0.9]],
                },
                {
                    "surface": "Branco",
                    "band": None,
                    "role": "fit",
                    "coordinates": [[0.6, 0.1], [0.9, 0.1], [0.9, 0.9], [0.6, 0.9]],
                },
            ],
            "method": "linear",
            "input_mode": "dn_empirical",
            "reference_band": "BLUE",
            "exclude_saturated": True,
            "edge_inset_percent": 0,
            "weighted": False,
            "robust": False,
            "max_cv": None,
            "max_saturation_percent": None,
            "max_validation_rmse": None,
        }
        processor = object.__new__(CalibrationProcessor)
        result = processor._calculate(config, manifest, {"BLUE": original}, root)
        calibrated = root / "calibrated-BLUE.tif"
        with rasterio.open(calibrated) as source:
            output = source.read(1)
            assert output.dtype == np.float32
            assert output[5, 5] == pytest.approx(0.1)
            assert output[5, 15] == pytest.approx(0.8)
        preview, _ = build_preview_png(calibrated)
        assert preview.startswith(b"\x89PNG\r\n\x1a\n")
        with rasterio.open(original) as source:
            assert np.array_equal(source.read(1), data)
        assert result["panel"]["name"] == "SYNTHETIC TEST ONLY"
        assert result["bands"]["BLUE"]["validation"] is None
        package = root / "export.zip"
        processor._make_bundle(root, result, package)
        with ZipFile(package) as archive:
            assert {
                "calibrated-BLUE.tif",
                "calibration-report.pdf",
                "audit.json",
                "coefficients.csv",
                "roi-statistics.csv",
            } <= set(archive.namelist())


def test_all_five_dji_tiffs_with_synthetic_references_only(tmp_path: Path) -> None:
    """Exercise the real TIFF decoder; output values are a synthetic test only."""
    files = sorted(Path(".local/calibration-sample").glob("DJI_*.TIF"))
    if len(files) != 5:
        pytest.skip("User-provided DJI TIFFs are not part of the repository")
    paths: dict[str, Path] = {}
    manifest: dict[str, list[dict[str, object]]] = {"bands": []}
    for file in files:
        with rasterio.open(file) as source:
            metadata = parse_raster_metadata(
                source.tags(),
                source.tags(ns="EXIF"),
                source.tags(ns="xml:XMP").get("xml:XMP"),
                dtype=source.dtypes[0],
                count=source.count,
            )
        code = metadata["band"]
        assert isinstance(code, str)
        paths[code] = file
        manifest["bands"].append(
            {
                "band": code,
                "filename": file.name,
                "sha256": "not-used-in-local-test",
                "metadata": metadata,
            }
        )
    surfaces = {
        "Preto": [[0.27, 0.24], [0.33, 0.24], [0.33, 0.31], [0.27, 0.31]],
        "Cinza-escuro": [[0.12, 0.24], [0.18, 0.24], [0.18, 0.31], [0.12, 0.31]],
        "Cinza-claro": [[0.27, 0.40], [0.33, 0.40], [0.33, 0.46], [0.27, 0.46]],
        "Branco": [[0.12, 0.40], [0.18, 0.40], [0.18, 0.46], [0.12, 0.46]],
    }
    config = {
        "panel_snapshot": {
            "name": "SYNTHETIC TEST ONLY — NOT REAL MAPIR VALUES",
            "source": {"kind": "synthetic_test", "certified": False},
            "values": {
                name: {code: value for code in paths}
                for name, value in zip(surfaces, (0.05, 0.2, 0.5, 0.85), strict=True)
            },
        },
        "rois": [
            {"surface": name, "band": None, "role": "fit", "coordinates": coordinates}
            for name, coordinates in surfaces.items()
        ],
        "method": "linear",
        "input_mode": "p4_signal",
        "reference_band": "NIR",
        "exclude_saturated": True,
        "edge_inset_percent": 5,
        "weighted": False,
        "robust": False,
        "max_cv": None,
        "max_saturation_percent": None,
        "max_validation_rmse": None,
    }
    processor = object.__new__(CalibrationProcessor)
    result = processor._calculate(config, manifest, paths, tmp_path)
    assert set(result["bands"]) == {"BLUE", "GREEN", "RED", "RED_EDGE", "NIR"}
    assert result["reference_provenance"]["kind"] == "synthetic_test"
    for code in paths:
        with rasterio.open(tmp_path / f"calibrated-{code}.tif") as output:
            assert output.dtypes == ("float32",)
            assert output.width == 1600
            assert output.height == 1300


def test_one_band_one_surface_proportional(tmp_path: Path) -> None:
    sample = Path(".local/calibration-sample/DJI_0305.TIF")
    if not sample.exists():
        pytest.skip("User-provided DJI TIFF is not part of the repository")
    with rasterio.open(sample) as source:
        metadata = parse_raster_metadata(
            source.tags(),
            source.tags(ns="EXIF"),
            source.tags(ns="xml:XMP").get("xml:XMP"),
            dtype=source.dtypes[0],
            count=source.count,
        )
    config = {
        "panel_snapshot": {
            "name": "SYNTHETIC ONE-BAND TEST",
            "source": {"kind": "synthetic_test", "certified": False},
            "values": {"Cinza-escuro": {"NIR": 0.2284}},
        },
        "rois": [
            {
                "surface": "Cinza-escuro",
                "band": "NIR",
                "role": "fit",
                "coordinates": [[0.12, 0.24], [0.18, 0.24], [0.18, 0.31], [0.12, 0.31]],
            }
        ],
        "method": "proportional",
        "input_mode": "p4_signal",
        "reference_band": "NIR",
        "exclude_saturated": True,
        "edge_inset_percent": 5,
        "weighted": False,
        "robust": False,
        "max_cv": None,
        "max_saturation_percent": None,
        "max_validation_rmse": None,
    }
    manifest = {"bands": [{"band": "NIR", "filename": sample.name, "metadata": metadata}]}
    processor = object.__new__(CalibrationProcessor)
    result = processor._calculate(config, manifest, {"NIR": sample}, tmp_path)
    assert list(result["bands"]) == ["NIR"]
    assert result["bands"]["NIR"]["model"]["intercept"] == 0
    assert (tmp_path / "calibrated-NIR.tif").exists()
