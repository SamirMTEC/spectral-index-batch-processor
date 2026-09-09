"""Versioned, allow-listed scientific definitions shared by every service."""

from __future__ import annotations

from dataclasses import dataclass

SOFTWARE_NAME = "SpectralIndex Batch Processor"
SOFTWARE_VERSION = "0.1.0"
SNAPSHOT_SCHEMA_VERSION = "1.0.0"
RECIPE_SCHEMA_VERSION = "1.0.0"
DEFAULT_STATISTICS = ("mean", "median", "std", "p05", "p25", "p75", "p95")
SUPPORTED_STATISTICS = (
    "count",
    "mean",
    "median",
    "min",
    "max",
    "std",
    "p05",
    "p25",
    "p75",
    "p95",
)


@dataclass(frozen=True, slots=True)
class FormulaDefinition:
    """One immutable formula implementation contract."""

    index: str
    formula_version: str
    expression: str
    required_bands: tuple[str, ...]
    allowed_parameters: frozenset[str] = frozenset()
    default_parameters: tuple[tuple[str, float], ...] = ()
    scientific_note: str | None = None

    def parameters_with_defaults(self, supplied: dict[str, float]) -> dict[str, float]:
        """Return a new parameter dictionary without mutating caller input."""
        return {**dict(self.default_parameters), **supplied}


FORMULA_DEFINITIONS: dict[tuple[str, str], FormulaDefinition] = {
    ("NDVI", "1.0.0"): FormulaDefinition(
        "NDVI", "1.0.0", "(NIR - RED) / (NIR + RED)", ("NIR", "RED")
    ),
    ("GNDVI", "1.0.0"): FormulaDefinition(
        "GNDVI", "1.0.0", "(NIR - GREEN) / (NIR + GREEN)", ("NIR", "GREEN")
    ),
    ("SAVI", "1.0.0"): FormulaDefinition(
        "SAVI",
        "1.0.0",
        "((NIR - RED) / (NIR + RED + L)) * (1 + L)",
        ("NIR", "RED"),
        frozenset({"L"}),
        (("L", 0.5),),
    ),
    ("CIGREEN", "1.0.0"): FormulaDefinition(
        "CIGREEN", "1.0.0", "(NIR / GREEN) - 1", ("NIR", "GREEN")
    ),
    ("CIRED_EDGE", "1.0.0"): FormulaDefinition(
        "CIRED_EDGE", "1.0.0", "(NIR / RED_EDGE) - 1", ("NIR", "RED_EDGE")
    ),
    ("NDRE", "1.0.0"): FormulaDefinition(
        "NDRE", "1.0.0", "(NIR - RED_EDGE) / (NIR + RED_EDGE)", ("NIR", "RED_EDGE")
    ),
    ("MCARI", "1.0.0"): FormulaDefinition(
        "MCARI",
        "1.0.0",
        "((RED_EDGE - RED) - 0.2 * (RED_EDGE - GREEN)) * (RED_EDGE / RED)",
        ("GREEN", "RED", "RED_EDGE"),
        scientific_note=(
            "A precisão do MCARI depende da posição espectral real da banda RedEdge do sensor, "
            "usada aqui como representação da região próxima de 700 nm."
        ),
    ),
    ("VARI", "1.0.0"): FormulaDefinition(
        "VARI",
        "1.0.0",
        "(GREEN - RED) / (GREEN + RED - BLUE)",
        ("GREEN", "RED", "BLUE"),
    ),
    ("EXG", "1.0.0"): FormulaDefinition(
        "EXG", "1.0.0", "2 * GREEN - RED - BLUE", ("GREEN", "RED", "BLUE")
    ),
}

CURRENT_FORMULA_VERSIONS = {
    "NDVI": "1.0.0",
    "GNDVI": "1.0.0",
    "SAVI": "1.0.0",
    "CIGREEN": "1.0.0",
    "CIRED_EDGE": "1.0.0",
    "NDRE": "1.0.0",
    "MCARI": "1.0.0",
    "VARI": "1.0.0",
    "EXG": "1.0.0",
}


def canonicalize_index_name(index: str) -> str:
    """Normalize public spellings without changing the persisted scientific identity."""
    normalized = index.strip().upper().replace(" ", "_")
    aliases = {
        "CI_GREEN": "CIGREEN",
        "CIRED-EDGE": "CIRED_EDGE",
        "CIREDEDGE": "CIRED_EDGE",
    }
    return aliases.get(normalized, normalized)


def get_formula_definition(index: str, formula_version: str | None = None) -> FormulaDefinition:
    """Resolve an exact formula; callers must never silently fall back across versions."""
    canonical = canonicalize_index_name(index)
    resolved_version = formula_version or CURRENT_FORMULA_VERSIONS.get(canonical)
    if resolved_version is None or (canonical, resolved_version) not in FORMULA_DEFINITIONS:
        rendered = formula_version or "current"
        raise KeyError(f"Unsupported formula version: {canonical}@{rendered}")
    return FORMULA_DEFINITIONS[(canonical, resolved_version)]
