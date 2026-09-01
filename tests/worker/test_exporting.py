"""Scientific export file layout and manifest redaction tests."""

import csv
from pathlib import Path

from openpyxl import load_workbook  # type: ignore[import-untyped]
from spectral_worker.exporting import RESULT_COLUMNS, _safe_manifest_value, _write_csv, _write_xlsx


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
