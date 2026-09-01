"""Typed inputs and immutable outputs of the spectral engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
from affine import Affine
from rasterio.crs import CRS


@dataclass(frozen=True, slots=True)
class BandData:
    """One canonical band plus validity and optional spatial metadata."""

    data: npt.ArrayLike
    nodata: float | int | None = None
    crs: CRS | str | None = None
    transform: Affine | tuple[float, ...] | list[float] | None = None


@dataclass(frozen=True, slots=True)
class IndexStatistics:
    count: int
    mean: float | None
    median: float | None
    min: float | None
    max: float | None
    std: float | None
    p05: float | None
    p25: float | None
    p75: float | None
    p95: float | None


@dataclass(frozen=True, slots=True)
class IndexResult:
    index_name: str
    formula_version: str
    values: npt.NDArray[np.float32]
    valid_mask: npt.NDArray[np.bool_]
    statistics: IndexStatistics
    width: int
    height: int
    crs: CRS | None = None
    transform: Affine | None = None
    parameters: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.values.dtype != np.float32:
            raise TypeError("IndexResult values must be float32")
        if self.valid_mask.dtype != np.bool_:
            raise TypeError("IndexResult valid_mask must be boolean")
        if self.values.shape != self.valid_mask.shape:
            raise ValueError("IndexResult values and valid_mask must have equal shapes")


BandInput = BandData | npt.ArrayLike
ROIInput = npt.ArrayLike | None
Parameters = dict[str, float | int]


def masked_array(value: Any) -> np.ma.MaskedArray[Any, Any]:
    """Keep an input mask while avoiding object/string arrays."""
    array = np.ma.asarray(value)
    if not np.issubdtype(array.dtype, np.number):
        raise TypeError("Band arrays must use a numeric dtype")
    return array
