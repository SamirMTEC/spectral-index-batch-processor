"""Panel reference validation and calibration route registration."""

import pytest
from pydantic import ValidationError
from spectral_api.main import create_app
from spectral_api.routes.calibration import _csv_values
from spectral_api.schemas.calibration import CalibrationPanelCreate, CalibrationRunCreate


def test_certificate_csv_accepts_long_and_wide_forms() -> None:
    assert _csv_values("surface,band,reflectance\nPreto,BLUE,0.03\nBranco,BLUE,0.86\n") == {
        "Preto": {"BLUE": 0.03},
        "Branco": {"BLUE": 0.86},
    }
    assert _csv_values("surface,BLUE,NIR\nPreto,0.03,0.04\n") == {
        "Preto": {"BLUE": 0.03, "NIR": 0.04},
    }
    with pytest.raises(ValueError):
        _csv_values("surface,band,reflectance\nPreto,BLUE,1.2\n")


def test_no_invented_certification_or_unacknowledged_dn() -> None:
    with pytest.raises(ValidationError):
        CalibrationPanelCreate(
            name="MAPIR",
            source_label="manual",
            certified=True,
            values={"Preto": {"BLUE": 0.03}},
        )
    with pytest.raises(ValidationError):
        CalibrationRunCreate(
            dataset_id="00000000-0000-0000-0000-000000000001",
            capture_id="00000000-0000-0000-0000-000000000002",
            panel_id="00000000-0000-0000-0000-000000000003",
            method="proportional",
            input_mode="dn_empirical",
            rois=[{"surface": "Preto", "coordinates": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]}],
        )


def test_calibration_routes_registered() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/v1/calibration/panels" in paths
    assert "/api/v1/calibration/captures/{capture_id}/bands" in paths
    assert "/api/v1/calibration/runs" in paths
    assert "/api/v1/calibration/runs/{run_id}/cancel" in paths
    assert "/api/v1/calibration/runs/{run_id}/retry-failed" in paths


def test_batch_requires_one_surface_all_bands_and_corrected_signal() -> None:
    data = {
        "dataset_id": "00000000-0000-0000-0000-000000000001",
        "capture_id": "00000000-0000-0000-0000-000000000002",
        "target_capture_ids": ["00000000-0000-0000-0000-000000000004"],
        "panel_id": "00000000-0000-0000-0000-000000000003",
        "method": "proportional",
        "input_mode": "p4_signal",
        "rois": [
            {
                "surface": "Cinza-claro",
                "band": "NIR",
                "coordinates": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]],
            }
        ],
    }
    assert CalibrationRunCreate.model_validate(data).target_capture_ids
    for changes in (
        {"method": "linear"},
        {"selected_band": "NIR"},
        {"input_mode": "dn_empirical", "acknowledge_dn_limits": True},
        {
            "rois": data["rois"]
            + [
                {
                    "surface": "Branco",
                    "band": "NIR",
                    "coordinates": [[0.3, 0.3], [0.4, 0.3], [0.4, 0.4]],
                }
            ]
        },
    ):
        with pytest.raises(ValidationError):
            CalibrationRunCreate.model_validate({**data, **changes})


def test_multiple_references_require_explicit_valid_target_assignment() -> None:
    primary = "00000000-0000-0000-0000-000000000002"
    secondary = "00000000-0000-0000-0000-000000000005"
    target = "00000000-0000-0000-0000-000000000004"
    roi = {
        "surface": "Cinza-escuro",
        "band": "NIR",
        "coordinates": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]],
    }
    data = {
        "dataset_id": "00000000-0000-0000-0000-000000000001",
        "capture_id": primary,
        "target_capture_ids": [target],
        "additional_references": [{"capture_id": secondary, "rois": [roi]}],
        "target_reference_map": {target: secondary},
        "panel_id": "00000000-0000-0000-0000-000000000003",
        "method": "proportional",
        "input_mode": "p4_signal",
        "rois": [roi],
    }
    assert CalibrationRunCreate.model_validate(data).additional_references
    combined = {
        **data,
        "reference_combination": "mean_coefficients",
        "target_reference_map": {},
    }
    assert (
        CalibrationRunCreate.model_validate(combined).reference_combination == "mean_coefficients"
    )
    assert (
        CalibrationRunCreate.model_validate(
            {**combined, "additional_references": []}
        ).reference_combination
        == "mean_coefficients"
    )
    with pytest.raises(ValidationError):
        CalibrationRunCreate.model_validate({**combined, "target_reference_map": {target: primary}})
    for changes in (
        {"target_reference_map": {}},
        {"target_reference_map": {target: "00000000-0000-0000-0000-000000000099"}},
        {"additional_references": [{"capture_id": primary, "rois": [roi]}]},
        {
            "additional_references": [
                {"capture_id": secondary, "rois": [{**roi, "surface": "Branco"}]}
            ]
        },
        {"target_capture_ids": [target, secondary]},
    ):
        with pytest.raises(ValidationError):
            CalibrationRunCreate.model_validate({**data, **changes})
