# Catálogo de índices espectrais

## Contrato científico

O catálogo corrente possui 39 índices calculáveis exclusivamente com `BLUE`,
`GREEN`, `RED`, `RED_EDGE` e `NIR`. `RED_EDGE` é o identificador interno já
adotado pelo projeto para a banda apresentada como **RedEdge** na interface.

Cada definição central registra fórmula, versão, bandas, categoria, parâmetros,
unidade, intervalo esperado quando aplicável, aliases e ressalvas científicas.
API, worker, recipes, snapshots e interface consomem esse mesmo catálogo. Uma
recipe não executa aliases como cálculos separados: `RVI`, `Simple Ratio` e
`SR`, por exemplo, resolvem para a única identidade persistida `RVI`.

Todos os índices aceitam `reflectance_scale`, cujo padrão explícito é `1`. O
motor divide as bandas por esse valor antes de calcular. Para reflectância em
0–10000, o usuário deve informar `10000`; nenhuma escala é inferida pelo valor
dos pixels. `SAVI` também aceita `L` (padrão `0.5`) e `ARVI` aceita `gamma`
(padrão `1`).

## Definições correntes

| Índice | Fórmula | Bandas | Categoria | Direto/Aproximado |
| --- | --- | --- | --- | --- |
| NDVI | `(NIR - RED) / (NIR + RED)` | NIR + RED | Vegetação | Direto |
| GNDVI | `(NIR - GREEN) / (NIR + GREEN)` | NIR + GREEN | Clorofila | Direto |
| NDRE | `(NIR - RED_EDGE) / (NIR + RED_EDGE)` | NIR + RED_EDGE | RedEdge | Direto |
| CIGREEN | `(NIR / GREEN) - 1` | NIR + GREEN | Clorofila | Direto |
| CIRED_EDGE | `(NIR / RED_EDGE) - 1` | NIR + RED_EDGE | RedEdge | Direto |
| DVI | `NIR - RED` | NIR + RED | Vegetação | Direto |
| RVI | `NIR / RED` | NIR + RED | Vegetação | Direto |
| IPVI | `NIR / (NIR + RED)` | NIR + RED | Vegetação | Direto |
| RDVI | `(NIR - RED) / sqrt(NIR + RED)` | NIR + RED | Vegetação | Direto |
| SAVI | `((NIR - RED) / (NIR + RED + L)) * (1 + L)` | NIR + RED | Ajustado ao solo | Direto |
| OSAVI | `1.16 * (NIR - RED) / (NIR + RED + 0.16)` | NIR + RED | Ajustado ao solo | Direto |
| MSAVI2 | `(2*NIR + 1 - sqrt((2*NIR + 1)^2 - 8*(NIR - RED))) / 2` | NIR + RED | Ajustado ao solo | Direto |
| TVI | `sqrt(NDVI + 0.5)` | NIR + RED | Vegetação | Direto |
| CTVI | `sign(NDVI + 0.5) * sqrt(abs(NDVI + 0.5))` | NIR + RED | Vegetação | Direto |
| NIRV | `NDVI * NIR` | NIR + RED | Vegetação | Direto |
| EVI | `2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)` | NIR + RED + BLUE | Resistente à atmosfera | Direto |
| EVI2 | `2.5 * (NIR - RED) / (NIR + 2.4*RED + 1)` | NIR + RED | Resistente à atmosfera | Direto |
| ARVI | `(NIR - RB) / (NIR + RB); RB = RED - gamma*(BLUE - RED)` | NIR + RED + BLUE | Resistente à atmosfera | Direto |
| MNDVI | `(NIR - RED) / (NIR + RED - 2*BLUE)` | NIR + RED + BLUE | Resistente à atmosfera | Direto |
| SIPI | `(NIR - BLUE) / (NIR - RED)` | NIR + BLUE + RED | Pigmentos | Direto |
| NPCI | `(RED - BLUE) / (RED + BLUE)` | RED + BLUE | Pigmentos | Direto |
| CI | `(RED - BLUE) / RED` | RED + BLUE | Pigmentos | Direto |
| CVI | `(NIR * RED) / GREEN^2` | NIR + RED + GREEN | Clorofila | Direto |
| GLI | `(2*GREEN - RED - BLUE) / (2*GREEN + RED + BLUE)` | GREEN + RED + BLUE | RGB / Visível | Direto |
| NGRDI | `(GREEN - RED) / (GREEN + RED)` | GREEN + RED | RGB / Visível | Direto |
| RGBVI | `(GREEN^2 - RED*BLUE) / (GREEN^2 + RED*BLUE)` | GREEN + RED + BLUE | RGB / Visível | Direto |
| MGRVI | `(GREEN^2 - RED^2) / (GREEN^2 + RED^2)` | GREEN + RED | RGB / Visível | Direto |
| VARI | `(GREEN - RED) / (GREEN + RED - BLUE)` | GREEN + RED + BLUE | RGB / Visível | Direto |
| EXG | `2*g - r - b; r,g,b = RGB/(RED+GREEN+BLUE)` | GREEN + RED + BLUE | RGB / Visível | Direto |
| EXR | `1.4*r - g; r,g = RED,GREEN/(RED+GREEN+BLUE)` | GREEN + RED + BLUE | RGB / Visível | Direto |
| EXGR | `ExG - ExR` | GREEN + RED + BLUE | RGB / Visível | Direto |
| TGI | `GREEN - 0.39*RED - 0.61*BLUE` | GREEN + RED + BLUE | RGB / Visível | Aproximado |
| RRI1 | `NIR / RED_EDGE` | NIR + RED_EDGE | RedEdge | Direto |
| ARI | `(1 / GREEN) - (1 / RED_EDGE)` | GREEN + RED_EDGE | Pigmentos | Aproximado |
| MARI | `NIR * ((1 / GREEN) - (1 / RED_EDGE))` | NIR + GREEN + RED_EDGE | Pigmentos | Aproximado |
| MCARI | `((RED_EDGE - RED) - 0.2*(RED_EDGE - GREEN)) * (RED_EDGE / RED)` | GREEN + RED + RED_EDGE | Clorofila | Aproximado |
| MCARI1 | `1.2 * (2.5*(NIR - RED) - 1.3*(NIR - GREEN))` | NIR + RED + GREEN | Clorofila | Direto |
| MCARI2 | `1.5*(2.5*(NIR-RED)-1.3*(NIR-GREEN)) / sqrt((2*NIR+1)^2-(6*NIR-5*sqrt(RED))-0.5)` | NIR + RED + GREEN | Clorofila | Direto |
| PSRI | `(RED - GREEN) / NIR` | RED + GREEN + NIR | Pigmentos | Aproximado |

ARI e mARI são marcados como aproximações porque sua interpretação depende da
posição espectral das bandas Green e RedEdge. MCARI usa RedEdge como aproximação
da região de 700 nm. TGI depende da resposta espectral do RGB do sensor, e PSRI
depende originalmente de comprimentos de onda mais específicos. Essas ressalvas
são exibidas na interface e congeladas no snapshot e no manifesto.

## Versionamento e compatibilidade

O ExG legado `1.0.0` calculava `2*GREEN - RED - BLUE`. Ele permanece registrado
para reproduzir jobs históricos. Novos jobs usam o ExG `2.0.0`, com RGB
normalizado, conforme o contrato atual. Alterações futuras devem criar nova
versão; nunca sobrescrever uma definição usada em snapshots existentes.

## Índices não incluídos

Não foram adicionados índices que precisem de SWIR, SWIR1, SWIR2, MIR, Thermal,
Coastal, múltiplas RedEdge ou vários comprimentos estreitos simultâneos. Nenhuma
banda disponível foi usada como substituta artificial dessas bandas. Também não
foram acrescentadas fórmulas além da lista solicitada: o projeto não mantém uma
bibliografia adicional versionada que autorizasse novas definições sem inventar
proveniência científica.

