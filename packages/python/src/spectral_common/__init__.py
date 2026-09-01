"""Shared building blocks for SpectralIndex services."""

from spectral_common.config import Settings, get_settings
from spectral_common.science_catalog import (
    CURRENT_FORMULA_VERSIONS,
    DEFAULT_STATISTICS,
    FORMULA_DEFINITIONS,
    RECIPE_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    SOFTWARE_NAME,
    SOFTWARE_VERSION,
    SUPPORTED_STATISTICS,
    FormulaDefinition,
    get_formula_definition,
)

__all__ = [
    "CURRENT_FORMULA_VERSIONS",
    "DEFAULT_STATISTICS",
    "FORMULA_DEFINITIONS",
    "RECIPE_SCHEMA_VERSION",
    "SNAPSHOT_SCHEMA_VERSION",
    "SOFTWARE_NAME",
    "SOFTWARE_VERSION",
    "SUPPORTED_STATISTICS",
    "FormulaDefinition",
    "Settings",
    "get_formula_definition",
    "get_settings",
]
