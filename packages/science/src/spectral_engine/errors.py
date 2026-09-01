"""Explicit failures raised before or during scientific calculation."""


class SpectralEngineError(ValueError):
    """Base error for deterministic user/input failures."""


class UnknownIndexError(SpectralEngineError):
    """The requested index is not present in the versioned registry."""


class MissingBandError(SpectralEngineError):
    """One or more required canonical bands are missing."""


class IncompatibleDimensionsError(SpectralEngineError):
    """Required bands or ROI do not share one raster grid shape."""


class SpatialMetadataMismatchError(SpectralEngineError):
    """Required bands disagree on CRS or affine transform."""


class InvalidParameterError(SpectralEngineError):
    """Index parameters are unknown, missing, or non-finite."""
