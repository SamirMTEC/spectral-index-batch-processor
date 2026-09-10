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
    category: str
    description: str
    allowed_parameters: frozenset[str] = frozenset({"reflectance_scale"})
    default_parameters: tuple[tuple[str, float], ...] = (("reflectance_scale", 1.0),)
    scientific_note: str | None = None
    approximate: bool = False
    unit: str = "dimensionless"
    expected_range: tuple[float, float] | None = None
    aliases: tuple[str, ...] = ()

    def parameters_with_defaults(self, supplied: dict[str, float]) -> dict[str, float]:
        """Return a new parameter dictionary without mutating caller input."""
        return {**dict(self.default_parameters), **supplied}


def _definition(
    index: str,
    expression: str,
    required_bands: tuple[str, ...],
    category: str,
    description: str,
    *,
    version: str = "1.0.0",
    parameters: frozenset[str] = frozenset(),
    defaults: tuple[tuple[str, float], ...] = (),
    scientific_note: str | None = None,
    approximate: bool = False,
    unit: str = "dimensionless",
    expected_range: tuple[float, float] | None = None,
    aliases: tuple[str, ...] = (),
) -> FormulaDefinition:
    return FormulaDefinition(
        index=index,
        formula_version=version,
        expression=expression,
        required_bands=required_bands,
        category=category,
        description=description,
        allowed_parameters=frozenset({"reflectance_scale", *parameters}),
        default_parameters=(("reflectance_scale", 1.0), *defaults),
        scientific_note=scientific_note,
        approximate=approximate,
        unit=unit,
        expected_range=expected_range,
        aliases=aliases,
    )


_FORMULAS = (
    _definition(
        "NDVI",
        "(NIR - RED) / (NIR + RED)",
        ("NIR", "RED"),
        "vegetation",
        "Índice normalizado de vigor da vegetação.",
        expected_range=(-1, 1),
    ),
    _definition(
        "GNDVI",
        "(NIR - GREEN) / (NIR + GREEN)",
        ("NIR", "GREEN"),
        "chlorophyll",
        "Índice normalizado sensível à clorofila.",
        expected_range=(-1, 1),
    ),
    _definition(
        "NDRE",
        "(NIR - RED_EDGE) / (NIR + RED_EDGE)",
        ("NIR", "RED_EDGE"),
        "red_edge",
        "Índice normalizado RedEdge para vegetação desenvolvida.",
        expected_range=(-1, 1),
    ),
    _definition(
        "CIGREEN",
        "(NIR / GREEN) - 1",
        ("NIR", "GREEN"),
        "chlorophyll",
        "Índice de clorofila baseado na banda Green.",
        aliases=("CIgreen",),
    ),
    _definition(
        "CIRED_EDGE",
        "(NIR / RED_EDGE) - 1",
        ("NIR", "RED_EDGE"),
        "red_edge",
        "Índice de clorofila baseado na banda RedEdge.",
        aliases=("CIred-edge", "CIrededge"),
    ),
    _definition(
        "DVI",
        "NIR - RED",
        ("NIR", "RED"),
        "vegetation",
        "Diferença simples entre NIR e Red.",
        unit="reflectance",
    ),
    _definition(
        "RVI",
        "NIR / RED",
        ("NIR", "RED"),
        "vegetation",
        "Razão simples entre NIR e Red.",
        aliases=("Simple Ratio", "SR"),
    ),
    _definition(
        "IPVI",
        "NIR / (NIR + RED)",
        ("NIR", "RED"),
        "vegetation",
        "Índice percentual de vegetação infravermelha.",
        expected_range=(0, 1),
    ),
    _definition(
        "RDVI",
        "(NIR - RED) / sqrt(NIR + RED)",
        ("NIR", "RED"),
        "vegetation",
        "Índice de diferença renormalizada.",
    ),
    _definition(
        "SAVI",
        "((NIR - RED) / (NIR + RED + L)) * (1 + L)",
        ("NIR", "RED"),
        "soil_adjusted",
        "Índice ajustado à influência do solo.",
        parameters=frozenset({"L"}),
        defaults=(("L", 0.5),),
    ),
    _definition(
        "OSAVI",
        "1.16 * (NIR - RED) / (NIR + RED + 0.16)",
        ("NIR", "RED"),
        "soil_adjusted",
        "SAVI otimizado com constante 0,16.",
    ),
    _definition(
        "MSAVI2",
        "(2*NIR + 1 - sqrt((2*NIR + 1)^2 - 8*(NIR - RED))) / 2",
        ("NIR", "RED"),
        "soil_adjusted",
        "SAVI modificado com ajuste automático do solo.",
    ),
    _definition(
        "TVI",
        "sqrt(NDVI + 0.5)",
        ("NIR", "RED"),
        "vegetation",
        "Transformação por raiz do NDVI deslocado.",
    ),
    _definition(
        "CTVI",
        "sign(NDVI + 0.5) * sqrt(abs(NDVI + 0.5))",
        ("NIR", "RED"),
        "vegetation",
        "TVI corrigido que preserva o sinal.",
    ),
    _definition(
        "NIRV",
        "NDVI * NIR",
        ("NIR", "RED"),
        "vegetation",
        "Reflectância NIR atribuída à vegetação.",
        unit="reflectance",
    ),
    _definition(
        "EVI",
        "2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)",
        ("NIR", "RED", "BLUE"),
        "atmosphere_resistant",
        "Índice de vegetação melhorado com correções atmosféricas.",
    ),
    _definition(
        "EVI2",
        "2.5 * (NIR - RED) / (NIR + 2.4*RED + 1)",
        ("NIR", "RED"),
        "atmosphere_resistant",
        "EVI de duas bandas.",
    ),
    _definition(
        "ARVI",
        "(NIR - RB) / (NIR + RB); RB = RED - gamma*(BLUE - RED)",
        ("NIR", "RED", "BLUE"),
        "atmosphere_resistant",
        "Índice resistente à atmosfera com correção da banda Red.",
        parameters=frozenset({"gamma"}),
        defaults=(("gamma", 1.0),),
    ),
    _definition(
        "MNDVI",
        "(NIR - RED) / (NIR + RED - 2*BLUE)",
        ("NIR", "RED", "BLUE"),
        "atmosphere_resistant",
        "NDVI modificado com correção Blue.",
    ),
    _definition(
        "SIPI",
        "(NIR - BLUE) / (NIR - RED)",
        ("NIR", "BLUE", "RED"),
        "pigments",
        "Índice de pigmentos insensível à estrutura.",
    ),
    _definition(
        "NPCI",
        "(RED - BLUE) / (RED + BLUE)",
        ("RED", "BLUE"),
        "pigments",
        "Índice normalizado de pigmentos de clorofila.",
        expected_range=(-1, 1),
    ),
    _definition(
        "CI", "(RED - BLUE) / RED", ("RED", "BLUE"), "pigments", "Índice de coloração visível."
    ),
    _definition(
        "CVI",
        "(NIR * RED) / GREEN^2",
        ("NIR", "RED", "GREEN"),
        "chlorophyll",
        "Índice de vegetação de clorofila.",
    ),
    _definition(
        "GLI",
        "(2*GREEN - RED - BLUE) / (2*GREEN + RED + BLUE)",
        ("GREEN", "RED", "BLUE"),
        "rgb_visible",
        "Índice de folha verde em bandas visíveis.",
        expected_range=(-1, 1),
    ),
    _definition(
        "NGRDI",
        "(GREEN - RED) / (GREEN + RED)",
        ("GREEN", "RED"),
        "rgb_visible",
        "Diferença normalizada Green-Red.",
        expected_range=(-1, 1),
    ),
    _definition(
        "RGBVI",
        "(GREEN^2 - RED*BLUE) / (GREEN^2 + RED*BLUE)",
        ("GREEN", "RED", "BLUE"),
        "rgb_visible",
        "Índice de vegetação RGB.",
        expected_range=(-1, 1),
    ),
    _definition(
        "MGRVI",
        "(GREEN^2 - RED^2) / (GREEN^2 + RED^2)",
        ("GREEN", "RED"),
        "rgb_visible",
        "Índice Green-Red modificado.",
        expected_range=(-1, 1),
    ),
    _definition(
        "VARI",
        "(GREEN - RED) / (GREEN + RED - BLUE)",
        ("GREEN", "RED", "BLUE"),
        "rgb_visible",
        "Índice visível resistente à atmosfera.",
    ),
    _definition(
        "EXG",
        "2*GREEN - RED - BLUE",
        ("GREEN", "RED", "BLUE"),
        "rgb_visible",
        "Excesso de verde legado sem normalização.",
        version="1.0.0",
        unit="input digital level",
        scientific_note="Versão preservada exclusivamente para reproduzir jobs históricos.",
    ),
    _definition(
        "EXG",
        "2*g - r - b; r,g,b = RGB/(RED+GREEN+BLUE)",
        ("GREEN", "RED", "BLUE"),
        "rgb_visible",
        "Excesso de verde sobre RGB normalizado.",
        version="2.0.0",
    ),
    _definition(
        "EXR",
        "1.4*r - g; r,g = RED,GREEN/(RED+GREEN+BLUE)",
        ("GREEN", "RED", "BLUE"),
        "rgb_visible",
        "Excesso de vermelho sobre RGB normalizado.",
    ),
    _definition(
        "EXGR",
        "ExG - ExR",
        ("GREEN", "RED", "BLUE"),
        "rgb_visible",
        "Diferença entre excesso de verde e excesso de vermelho.",
    ),
    _definition(
        "TGI",
        "GREEN - 0.39*RED - 0.61*BLUE",
        ("GREEN", "RED", "BLUE"),
        "rgb_visible",
        "Aproximação RGB do Triangular Greenness Index.",
        approximate=True,
        unit="reflectance",
        scientific_note=(
            "O resultado depende das características espectrais reais das bandas RGB do sensor."
        ),
    ),
    _definition(
        "RRI1",
        "NIR / RED_EDGE",
        ("NIR", "RED_EDGE"),
        "red_edge",
        "Razão simples NIR/RedEdge.",
        aliases=("RedEdge Ratio Index",),
    ),
    _definition(
        "ARI",
        "(1 / GREEN) - (1 / RED_EDGE)",
        ("GREEN", "RED_EDGE"),
        "pigments",
        "Índice de reflectância de antocianina.",
        approximate=True,
        unit="inverse reflectance",
        scientific_note=(
            "A interpretação depende da posição espectral real das bandas Green e RedEdge."
        ),
    ),
    _definition(
        "MARI",
        "NIR * ((1 / GREEN) - (1 / RED_EDGE))",
        ("NIR", "GREEN", "RED_EDGE"),
        "pigments",
        "ARI modificado pela reflectância NIR.",
        aliases=("mARI",),
        approximate=True,
        scientific_note=(
            "A interpretação depende da posição espectral real das bandas Green e RedEdge."
        ),
    ),
    _definition(
        "MCARI",
        "((RED_EDGE - RED) - 0.2*(RED_EDGE - GREEN)) * (RED_EDGE / RED)",
        ("GREEN", "RED", "RED_EDGE"),
        "chlorophyll",
        "Índice modificado de razão de absorção de clorofila.",
        scientific_note=(
            "A precisão depende da posição espectral real da RedEdge, usada como "
            "aproximação da região de 700 nm."
        ),
        approximate=True,
        unit="reflectance",
    ),
    _definition(
        "MCARI1",
        "1.2 * (2.5*(NIR - RED) - 1.3*(NIR - GREEN))",
        ("NIR", "RED", "GREEN"),
        "chlorophyll",
        "Primeira variante do MCARI com NIR.",
        unit="reflectance",
    ),
    _definition(
        "MCARI2",
        "1.5*(2.5*(NIR-RED)-1.3*(NIR-GREEN)) / sqrt((2*NIR+1)^2-(6*NIR-5*sqrt(RED))-0.5)",
        ("NIR", "RED", "GREEN"),
        "chlorophyll",
        "Segunda variante do MCARI com normalização por raiz.",
    ),
    _definition(
        "PSRI",
        "(RED - GREEN) / NIR",
        ("RED", "GREEN", "NIR"),
        "pigments",
        "Aproximação do índice de reflectância de senescência.",
        approximate=True,
        scientific_note=(
            "A fórmula é aproximada porque o PSRI original depende de comprimentos "
            "de onda específicos."
        ),
    ),
)

FORMULA_DEFINITIONS: dict[tuple[str, str], FormulaDefinition] = {
    (definition.index, definition.formula_version): definition for definition in _FORMULAS
}
CURRENT_FORMULA_VERSIONS = {
    definition.index: definition.formula_version for definition in _FORMULAS
}


def canonicalize_index_name(index: str) -> str:
    """Normalize public spellings without changing the persisted scientific identity."""
    normalized = index.strip().upper().replace(" ", "_")
    aliases = {"CI_GREEN": "CIGREEN", "CIRED-EDGE": "CIRED_EDGE", "CIREDEDGE": "CIRED_EDGE"}
    for definition in _FORMULAS:
        aliases.update(
            {
                alias.strip().upper().replace(" ", "_"): definition.index
                for alias in definition.aliases
            }
        )
    return aliases.get(normalized, normalized)


def get_formula_definition(index: str, formula_version: str | None = None) -> FormulaDefinition:
    """Resolve an exact formula; callers must never silently fall back across versions."""
    canonical = canonicalize_index_name(index)
    resolved_version = formula_version or CURRENT_FORMULA_VERSIONS.get(canonical)
    if resolved_version is None or (canonical, resolved_version) not in FORMULA_DEFINITIONS:
        rendered = formula_version or "current"
        raise KeyError(f"Unsupported formula version: {canonical}@{rendered}")
    return FORMULA_DEFINITIONS[(canonical, resolved_version)]
