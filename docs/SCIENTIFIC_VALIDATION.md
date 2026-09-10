# Validação científica do motor espectral

## Escopo e versão auditada

Esta validação cobre o motor numérico interno do SpectralIndex Batch Processor
`0.1.0` e os 39 índices correntes documentados em
`docs/SPECTRAL_INDEX_CATALOG.md`. O ExG corrente é `2.0.0`; a fórmula não
normalizada `1.0.0` permanece testada exclusivamente para reprodutibilidade de
jobs históricos. O objetivo é verificar fórmulas, promoção de tipos, escala de
reflectância, regras de validade, recorte por ROI e estatísticas descritivas. A
auditoria não valida calibração radiométrica do sensor, alinhamento físico entre
bandas ou interpretação agronômica.

## Metodologia

Cada índice possui um teste independente com três níveis de comparação:

1. uma matriz sintética pequena, legível e com valores calculáveis à mão;
2. uma expressão NumPy de referência, independente das funções do motor e
   executada em `float64`;
3. o resultado do motor, persistido como `float32`.

As matrizes manuais são comparadas à referência NumPy e, separadamente, a saída
do sistema é comparada à referência. Máscara de validade e valores inválidos
(`NaN`) também são comparados explicitamente. Os testes estão em
`tests/science/test_scientific_audit.py` e
`tests/science/test_extended_index_catalog.py`; os dados compartilhados estão
em `tests/science/synthetic_datasets.py`.

A matriz estendida executa, para cada um dos 39 índices correntes: comparação
com expressão NumPy independente, dtype `float32`, máscara de NoData/NaN/Inf e
equivalência entre reflectância 0–1 e valores 0–10000 quando
`reflectance_scale=10000`. Há casos independentes para denominadores zero ou
numericamente próximos de zero e argumentos negativos de raiz.

As estatísticas de referência são calculadas somente sobre os valores válidos
já convertidos para `float32`, reproduzindo o conjunto efetivamente persistido
no GeoTIFF, mas sem reutilizar `calculate_statistics`.

## Dados sintéticos

O dataset base de 2 × 3 pixels usa as bandas abaixo. Há variantes equivalentes
em `uint8`, `uint16` (valores multiplicados por 10) e `float32` (valores `uint8`
divididos por 10).

```text
RED   = [[10, 20, 30], [40, 50, 60]]
NIR   = [[30, 20, 10], [80,100,120]]
GREEN = [[20, 30, 40], [50, 60, 70]]
RED_EDGE = [[15, 20, 25], [40, 50, 60]]
BLUE  = [[ 5, 10, 15], [20, 25, 30]]
```

O dataset de invalidade, em `float32`, é:

```text
RED = [[1, NoData, NaN, Inf], [-1, -2,  2,  3]]
NIR = [[3,      5,   7,   9], [ 1, -4, -2, -3]]
NoData = -9999
```

Ele contém, de forma isolável, NoData, NaN, Inf, entradas negativas válidas e
quatro situações de denominador zero.

## Resultados por índice

Os resultados esperados abaixo estão arredondados apenas para apresentação. Os
testes usam constantes `float32` e tolerâncias explícitas, sem arredondamento
textual.

| Índice | Tipo de entrada | Resultado esperado e obtido |
| --- | --- | --- |
| NDVI | `uint8` | `[[0.5, 0, -0.5], [0.333333, 0.333333, 0.333333]]` |
| GNDVI | `uint16` | `[[0.2, -0.2, -0.6], [0.230769, 0.25, 0.263158]]` |
| SAVI (`L=0.5`) | `float32` | `[[0.666667, 0, -0.666667], [0.48, 0.483871, 0.486486]]` |
| CIgreen | `uint16` | `[[0.5, -0.333333, -0.75], [0.6, 0.666667, 0.714286]]` |
| CIred-edge | `uint16` | `[[1, 0, -0.6], [1, 1, 1]]` |
| NDRE | `uint16` | `[[0.333333, 0, -0.428571], [0.333333, 0.333333, 0.333333]]` |
| MCARI | `float32` | `[[2.8, 1.2], [2.1, 7.6]]` em seu dataset manual independente |
| VARI | `uint16` | `[[0.4, 0.25, 0.181818], [0.142857, 0.117647, 0.1]]` |
| ExG legado `1.0.0` | `uint8` | `[[25, 30, 35], [40, 45, 50]]` |

Além desses casos manuais legados, todos os 39 índices correntes foram
comparados a expressões NumPy independentes sobre uma matriz 2 × 2 de
reflectância. Todos ficaram dentro de `rtol=1e-6` e `atol=1e-7`. A saída foi
`float32` em todos os casos. Um teste adicional executa NDVI com os mesmos
valores em `uint8`, `uint16` e `float32`, confirmando a promoção antes das
operações e a ausência de overflow do tipo de origem.

## ROI e validade

A ROI retangular usa a máscara conhecida:

```text
[[1, 1, 0],
 [0, 1, 0]]
```

Somente seus três pixels contribuem para valores e estatísticas. A ROI poligonal
é o triângulo `(0,0) → (4,0) → (0,4) → (0,0)`. Com a regra de centro do pixel
(`all_touched=false`), sua cobertura esperada e obtida é:

```text
[[1, 1, 1, 1],
 [1, 1, 1, 0],
 [1, 1, 0, 0],
 [1, 0, 0, 0]]
```

O mesmo polígono foi testado no processamento raster em blocos do worker: 10
pixels válidos e 6 pixels externos, sem mudança de coordenadas entre janelas.

No dataset de invalidade, apenas `(0,0)` e `(1,1)` permanecem válidos. Seus NDVI
são, respectivamente, `0.5` e `1/3`. NoData, NaN, Inf e denominador zero
permanecem com máscara inválida e valor `NaN`; não são convertidos para zero.
As fórmulas com divisão possuem casos representativos de denominador zero. ExG,
ExR e ExGR também invalidam o pixel quando a soma usada para normalizar RGB é
zero. RDVI, TVI, MSAVI2 e MCARI2 possuem casos específicos de argumento negativo
de raiz. Nenhum desses valores é convertido artificialmente em zero.

Bandas ausentes são rejeitadas separadamente para cada fórmula. Dimensões
incompatíveis entre bandas e entre ROI e raster também produzem erro antes do
cálculo.

## Estatísticas

O vetor sintético válido persistido é `[1, 3, 5, 8, 16]`. A referência NumPy e
o sistema produziram:

| Medida | Esperado | Obtido |
| --- | ---: | ---: |
| `count` | 5 | 5 |
| `mean` | 6.6 | 6.6 |
| `median` | 5.0 | 5.0 |
| `std` | 5.238320341483519 | 5.238320341483519 |
| `p05` | 1.4 | 1.4 |
| `p25` | 3.0 | 3.0 |
| `p75` | 8.0 | 8.0 |
| `p95` | 14.4 | 14.4 |

O desvio padrão é populacional (`ddof=0`). Os percentis usam interpolação
linear do NumPy (`method="linear"`). Mínimo e máximo também são verificados,
embora não estejam repetidos na tabela.

## Tolerâncias

- valores raster: `rtol=1e-6`, `atol=1e-7`;
- estatísticas escalares: `rtol=1e-9`, `atol=1e-12`;
- máscaras, contagens, dimensões e dtype: igualdade exata;
- pixels inválidos: `NaN` e máscara `false`, verificados explicitamente.

As tolerâncias de raster acomodam o arredondamento esperado da persistência em
`float32`. As estatísticas são calculadas em `float64` sobre esses valores já
persistidos, por isso usam tolerância mais estrita.

## Divergências investigadas

O catálogo anterior definia ExG como `2*GREEN - RED - BLUE`, enquanto o novo
contrato exige RGB normalizado. Sobrescrever a fórmula quebraria a
reprodutibilidade dos jobs existentes. A correção foi versionar o ExG: `1.0.0`
continua disponível para snapshots antigos e `2.0.0` é a versão corrente.

Índices com constantes não são invariantes à escala digital. Em vez de inferir
ou dividir silenciosamente por 10000, o motor agora aplica somente o
`reflectance_scale` explicitamente congelado no job. Os testes confirmam a
equivalência numérica entre entradas 0–1 e 0–10000 quando a escala correta é
informada.

## Limitações e próximos passos de validação

- Datasets sintéticos provam aritmética e regras de máscara, mas não substituem
  validação com alvos de reflectância e dados de sensores reais.
- A auditoria pressupõe bandas previamente registradas na mesma grade. Ela não
  mede erro de corregistro nem executa reprojeção.
- NoData numérico usa igualdade exata com o valor declarado; produtos com
  sentinela em ponto flutuante imprecisa precisam de política específica do
  formato/sensor.
- A cobertura poligonal segue o centro do pixel. Uma futura opção
  `all_touched=true` constituiria mudança metodológica e deve ser versionada.
- O MCARI usa `RED_EDGE` como representação da região próxima de 700 nm. Sua
  precisão e comparabilidade dependem da posição espectral e da largura de banda
  reais do sensor; essa ressalva também é persistida no snapshot e no manifesto.
- ARI, mARI, TGI e PSRI também carregam ressalvas de aproximação espectral no
  catálogo e nos metadados de reprodutibilidade.
- Não foram avaliados propagação de incerteza, sensibilidade radiométrica,
  significância estatística, desempenho ou variações entre arquiteturas de
  hardware.
- Antes de uma publicação científica, recomenda-se validar contra rasters reais
  pequenos, um software independente reconhecido e resultados revisados por um
  especialista do domínio.
