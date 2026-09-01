"""Atomic float32 GeoTIFF serialization for scientific index results."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import rasterio

from spectral_engine.models import IndexResult


def write_geotiff(result: IndexResult, destination: str | Path) -> Path:
    """Write a single-band float32 TIFF with NaN NoData and an explicit validity mask."""
    path = Path(destination)
    if path.suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError("Scientific raster output must use .tif or .tiff")
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        prefix=f".{path.stem}-",
        suffix=path.suffix,
        dir=path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        profile: dict[str, object] = {
            "driver": "GTiff",
            "width": result.width,
            "height": result.height,
            "count": 1,
            "dtype": "float32",
            "nodata": np.nan,
            "compress": "deflate",
            "predictor": 3,
        }
        if result.crs is not None:
            profile["crs"] = result.crs
        if result.transform is not None:
            profile["transform"] = result.transform
        with (
            rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True),
            rasterio.open(temporary_path, "w", **profile) as output,
        ):
            output.write(result.values, 1)
            output.write_mask(result.valid_mask.astype(np.uint8) * 255)
            output.update_tags(
                SPECTRAL_INDEX=result.index_name,
                SPECTRAL_FORMULA_VERSION=result.formula_version,
                SPECTRAL_PARAMETERS=json.dumps(result.parameters, sort_keys=True),
                SPECTRAL_VALIDITY="nodata,nan,inf,roi,zero_denominator",
                SPECTRAL_STATISTICS=json.dumps(
                    {
                        "count": result.statistics.count,
                        "mean": result.statistics.mean,
                        "median": result.statistics.median,
                        "min": result.statistics.min,
                        "max": result.statistics.max,
                        "std": result.statistics.std,
                        "p05": result.statistics.p05,
                        "p25": result.statistics.p25,
                        "p75": result.statistics.p75,
                        "p95": result.statistics.p95,
                    },
                    sort_keys=True,
                ),
            )
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return path
