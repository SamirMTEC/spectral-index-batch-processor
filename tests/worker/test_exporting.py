"""Scientific export file layout and manifest redaction tests."""

import csv
from pathlib import Path

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]
from spectral_worker.exporting import (
    RESULT_COLUMNS,
    SUMMARY_COLUMNS,
    ExportGenerator,
    _safe_manifest_value,
    _write_csv,
    _write_xlsx,
)


def test_tabular_exports_use_the_scientific_result_columns(tmp_path: Path) -> None:
    row = {column: f"value-{column}" for column in RESULT_COLUMNS}
    csv_path = tmp_path / "results.csv"
    xlsx_path = tmp_path / "results.xlsx"

    _write_csv(csv_path, [row])
    _write_xlsx(xlsx_path, [row])

    with csv_path.open(encoding="utf-8", newline="") as source:
        csv_rows = list(csv.DictReader(source))
    workbook = load_workbook(xlsx_path, read_only=True)
    sheet = workbook["Results"]
    xlsx_rows = list(sheet.iter_rows(values_only=True))

    assert csv_rows[0]["processing_job_id"] == "value-processing_job_id"
    assert list(xlsx_rows[0]) == RESULT_COLUMNS
    assert xlsx_rows[1][RESULT_COLUMNS.index("valid_pixels")] == "value-valid_pixels"


def test_manifest_sanitizer_removes_infrastructure_secrets() -> None:
    safe = _safe_manifest_value(
        {
            "parameters": {"L": 0.5},
            "redis_url": "redis://private",
            "nested": {"api_token": "do-not-export", "keep": "visible"},
        }
    )

    assert safe == {"parameters": {"L": 0.5}, "nested": {"keep": "visible"}}


def test_summary_export_aggregates_repetition_means_by_treatment_and_index(
    tmp_path: Path,
) -> None:
    generator = object.__new__(ExportGenerator)
    snapshot = {"job": {"project_name": "Project", "dataset_name": "Dataset"}}
    rows = [
        {"treatment": "T1", "index": "NDVI", "analysis_scope": "roi", "mean": 0.2},
        {"treatment": "T1", "index": "NDVI", "analysis_scope": "roi", "mean": 0.4},
        {
            "treatment": "T1",
            "index": "NDVI",
            "analysis_scope": "full_image",
            "mean": 0.9,
        },
        {"treatment": "T1", "index": "SAVI", "analysis_scope": "roi", "mean": 0.3},
    ]

    summaries = generator._summary_rows(snapshot, rows)
    ndvi = next(
        row
        for row in summaries
        if row["index"] == "NDVI" and row["analysis_scope"] == "roi"
    )
    full_ndvi = next(
        row
        for row in summaries
        if row["index"] == "NDVI" and row["analysis_scope"] == "full_image"
    )
    assert ndvi["repetitions"] == 2
    assert ndvi["mean"] == pytest.approx(0.3, abs=1e-12)
    assert ndvi["std"] == pytest.approx(0.1, abs=1e-12)
    assert full_ndvi["repetitions"] == 1
    assert full_ndvi["mean"] == pytest.approx(0.9, abs=1e-12)

    path = tmp_path / "results_summary.csv"
    _write_csv(path, summaries, SUMMARY_COLUMNS)
    with path.open(encoding="utf-8", newline="") as source:
        exported = list(csv.DictReader(source))
    exported_roi = next(
        row
        for row in exported
        if row["index"] == "NDVI" and row["analysis_scope"] == "roi"
    )
    assert exported_roi["repetitions"] == "2"
    assert exported_roi["mean"] == "0.30000000000000004"
