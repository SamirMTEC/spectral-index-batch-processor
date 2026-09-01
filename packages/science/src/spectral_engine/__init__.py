"""Public API for deterministic scientific spectral-index processing."""

from spectral_engine.engine import (
    DEFAULT_SAVI_L,
    INDEX_DEFINITIONS,
    calculate_index,
    calculate_statistics,
)
from spectral_engine.errors import (
    IncompatibleDimensionsError,
    InvalidParameterError,
    MissingBandError,
    SpatialMetadataMismatchError,
    SpectralEngineError,
    UnknownIndexError,
)
from spectral_engine.geotiff import write_geotiff
from spectral_engine.models import BandData, IndexResult, IndexStatistics

__all__ = [
    "INDEX_DEFINITIONS",
    "DEFAULT_SAVI_L",
    "BandData",
    "IncompatibleDimensionsError",
    "IndexResult",
    "IndexStatistics",
    "InvalidParameterError",
    "MissingBandError",
    "SpatialMetadataMismatchError",
    "SpectralEngineError",
    "UnknownIndexError",
    "calculate_index",
    "calculate_statistics",
    "write_geotiff",
]
