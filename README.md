# SpectralIndex Batch Processor

Fundação funcional do monorepo do SpectralIndex. O projeto entrega frontend, API, worker, banco, fila, object storage local, migrations e um motor científico interno testável.

## Site público com backend nesta máquina

O frontend está em `https://spectral.classlab.com.br`. Nesta instalação, inicie
API, workers, banco, MinIO e Cloudflare Tunnel com:

```powershell
docker compose -f docker-compose.yml -f docker-compose.public-local.yml up -d
```

Esse modo exige as credenciais locais do túnel e usa Auth0. Mantenha o computador
ligado e o Docker em execução. Consulte [o guia de backend local](infrastructure/cloudflare/LOCAL_BACKEND.md)
para reiniciar, parar, verificar ou voltar ao desenvolvimento. Antes de usar os
comandos de desenvolvimento abaixo, pare o túnel conforme esse guia.

## Estrutura

```text
apps/
  api/                 FastAPI, readiness e migrations Alembic
  web/                 React, TypeScript, Vite, Tailwind e shadcn/ui
  worker/              ingestão via Redis Streams e processamento Celery
docs/                   especificações e arquitetura
infrastructure/docker/  imagens da API e do worker
packages/python/        settings e logging compartilhados
packages/science/       cálculo científico e export GeoTIFF independente da web
tests/                  testes Python da API, common e worker
```

PostgreSQL é a fonte de verdade transacional. Redis atende a ingestão por Streams e a fila Celery de processamento científico. MinIO oferece a API S3 no desenvolvimento local; os mesmos parâmetros permitem usar Cloudflare R2 ou outro serviço S3-compatible em produção.

## Pré-requisitos

- Docker Desktop com Docker Compose.
- Python 3.13.
- Node.js 22.13 ou superior e npm 10 ou superior (22.16 recomendado para produção).

As versões usadas no scaffold foram validadas com Python 3.13, Node 24 e Docker Compose 5.

## Instalar

### 1. Configurar o ambiente

No PowerShell:

```powershell
Copy-Item .env.example .env
```

No macOS/Linux:

```bash
cp .env.example .env
```

Edite `.env`, preencha os valores secretos vazios com credenciais locais aleatórias e URL-safe e configure `SPECTRAL_DATABASE_URL`/`SPECTRAL_REDIS_URL` para o host quando executar processos fora do Docker. O arquivo `.env` está ignorado pelo Git e nunca deve ser commitado.

Se uma porta padrão já estiver ocupada, altere `POSTGRES_PORT`, `REDIS_PORT`, `MINIO_API_PORT`, `MINIO_CONSOLE_PORT` ou `API_PORT`. Ao executar API/worker no host, ajuste também as URLs `SPECTRAL_*` correspondentes.

### 2. Instalar Python

PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e packages/python -e packages/science -e apps/api -e apps/worker -r requirements/dev.txt
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e packages/python -e packages/science -e apps/api -e apps/worker -r requirements/dev.txt
```

### 3. Instalar frontend

```bash
npm install
```

O `package-lock.json` deve ser mantido no repositório para builds reproduzíveis.

## Executar

### Stack backend completa em Docker

```bash
docker compose up --build -d
docker compose ps
```

O serviço one-shot `migrate` executa `alembic upgrade head` antes da API e dos workers. O serviço `minio-init` cria um bucket privado e habilita versionamento.

Execute o frontend no host para preservar hot reload:

```bash
npm run dev
```

Serviços locais:

| Serviço       | URL                          |
| ------------- | ---------------------------- |
| Web           | http://localhost:5173        |
| API           | http://localhost:8000        |
| OpenAPI       | http://localhost:8000/docs   |
| Liveness      | http://localhost:8000/health |
| Readiness     | http://localhost:8000/ready  |
| MinIO S3      | http://localhost:9000        |
| MinIO Console | http://localhost:9001        |

### Backend no host com infraestrutura em Docker

Útil para reload da API e depuração dos workers:

```bash
docker compose up -d postgres redis minio minio-init
alembic -c apps/api/alembic.ini upgrade head
uvicorn spectral_api.main:app --reload --host 127.0.0.1 --port 8000
```

Em outro terminal, com o mesmo virtualenv:

```bash
python -m spectral_worker
```

Para iniciar também o worker de processamento científico no host:

```bash
celery -A spectral_worker.celery_app:celery_app worker --loglevel=INFO --concurrency=2
```

## Exportação científica

Ao término de um processamento, abra **Resultados → Arquivos**. É possível gerar
CSV, XLSX, ROIs, manifesto, logs e o **Baixar pacote completo**. A geração é
assíncrona; a tela consulta o estado até que o artefato fique disponível.

O pacote ZIP possui a raiz `SpectralIndex_Export/`, com `results.csv`,
`results.xlsx`, os rasters GeoTIFF científicos em `rasters/`, a ROI em
`roi/roi.json`, manifesto e configuração em `metadata/`, e o log público em
`logs/processing.log`. O manifesto registra fórmulas, parâmetros, bandas,
checksums quando disponíveis, ROIs e falhas, mas remove chaves que possam
conter credenciais ou detalhes internos da infraestrutura.

Para integrações, os endpoints autenticados são:

```text
GET  /api/v1/processing-jobs/{job_id}/exports
POST /api/v1/processing-jobs/{job_id}/exports
```

O `POST` recebe, por exemplo, `{ "formats": ["csv", "xlsx", "complete"] }`.
Pedidos equivalentes enquanto a geração está em curso reutilizam os artefatos
pendentes, evitando duplicação de trabalho.

## Verificações

### Python

```bash
ruff check .
ruff format --check .
mypy
pytest
```

Para aplicar a formatação Python:

```bash
ruff check . --fix
ruff format .
```

### Frontend

```bash
npm run lint
npm run format:check
npm run typecheck
npm run test
npm run build
```

Para aplicar a formatação:

```bash
npm run format
```

## Migrations

Criar uma migration após introduzir/alterar models:

```bash
alembic -c apps/api/alembic.ini revision --autogenerate -m "describe change"
```

Aplicar:

```bash
alembic -c apps/api/alembic.ini upgrade head
```

Verificar a revisão atual:

```bash
alembic -c apps/api/alembic.ini current
```

As migrations iniciais criam o domínio persistido (`User`, `Project`, `Dataset`, `Treatment`, `Capture`, `BandAsset`, `ROI`, `ProcessingJob`, `ProcessingItem`, `IndexResult` e `ExportArtifact`).

## Motor científico

O pacote `spectral-index-engine` é independente da API e da interface web. Sua API principal recebe bandas canônicas, uma máscara ROI opcional e parâmetros:

```python
from spectral_engine import BandData, calculate_index, write_geotiff

result = calculate_index(
    "NDVI",
    {"NIR": BandData(nir, nodata=-9999), "RED": BandData(red, nodata=-9999)},
    roi=roi_mask,
)
write_geotiff(result, "ndvi.tif")
```

Estão disponíveis `NDVI`, `GNDVI`, `SAVI`, `VARI` e `ExG`. O SAVI usa `L=0.5` quando o parâmetro não é informado. O cálculo converte entradas para `float64` antes das operações e persiste o resultado como `float32`. NoData, NaN, Inf, máscara original, pixels fora da ROI, denominador zero e valores não representáveis em `float32` permanecem inválidos (`NaN` mais máscara explícita); não são convertidos artificialmente para zero.

## Processamento assíncrono

Na tela de um dataset, selecione **Processar índices**. A tela **Processamento** permite escolher NDVI, GNDVI, SAVI, VARI e ExG, configurar o `L` do SAVI, iniciar o lote, cancelar itens ainda não concluídos e repetir falhas recuperáveis. O frontend consulta a API a cada dois segundos; a barra usa exclusivamente `processed_items / total_items` persistido no PostgreSQL.

Cada tarefa Celery processa uma captura e um índice. As bandas são lidas por janelas raster, o que evita carregar o dataset inteiro na memória. Falhas ficam isoladas no item: um lote com sucessos e falhas termina como `completed_with_errors`. Resultados são GeoTIFF float32 no object storage, com estatísticas e checksums persistidos.

Ao terminar um lote, use **Ver resultados**. A página **Resultados** apresenta visão geral, tabela filtrável/ordenável, comparação entre preview da imagem original e preview pseudocolor do índice, erros com ações recomendadas, GeoTIFFs para download e manifesto de reprodutibilidade. O preview é um PNG transitório e não modifica o GeoTIFF científico.

Endpoints principais:

- `POST /api/v1/datasets/{dataset_id}/processing-jobs` cria um lote idempotente.
- `GET /api/v1/datasets/{dataset_id}/processing-jobs` lista o histórico.
- `GET /api/v1/processing-jobs/{job_id}` retorna progresso, itens e estatísticas.
- `GET /api/v1/processing-jobs/{job_id}/results` retorna a experiência completa de resultados.
- `GET /api/v1/index-results/{result_id}/preview` produz um PNG pseudocolor autenticado para visualização.
- `POST /api/v1/processing-jobs/{job_id}/cancel` solicita cancelamento.
- `POST /api/v1/processing-items/{item_id}/retry` repete um item elegível.

Use o header `Idempotency-Key` ao criar um job. Reutilizar a chave com o mesmo payload devolve o job original; reutilizá-la com outro payload retorna conflito.

## Parar

```bash
docker compose down
```

Isso preserva os volumes de PostgreSQL, Redis e MinIO.

## Resetar o ambiente local

> Atenção: o comando abaixo remove definitivamente todos os dados locais do PostgreSQL, Redis e MinIO desta stack.

```bash
docker compose down --volumes --remove-orphans
docker compose up --build -d
```

Ele não remove `.env`, `.venv` ou `node_modules`.

## Storage em produção

Para Cloudflare R2/S3, configure no secret manager do ambiente:

- `SPECTRAL_S3_ENDPOINT_URL`
- `SPECTRAL_S3_REGION`
- `SPECTRAL_S3_BUCKET`
- `SPECTRAL_S3_ACCESS_KEY_ID`, quando não houver identidade de workload
- `SPECTRAL_S3_SECRET_ACCESS_KEY`, quando não houver identidade de workload
- `SPECTRAL_S3_FORCE_PATH_STYLE`

O bucket e suas políticas de retenção/versionamento devem ser provisionados por infraestrutura, não pela API. Não use as credenciais root do MinIO fora do ambiente local.

O guia completo para Cloudflare Pages/R2, containers, banco e fila gerenciados está em [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Backup, observabilidade, incidentes e restore estão em [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Estado dos endpoints

- `GET /health` confirma que o processo da API está vivo sem consultar dependências.
- `GET /ready` consulta PostgreSQL, Redis e o bucket S3/MinIO. Retorna HTTP 503 se qualquer dependência estiver indisponível.

### Domínio inicial

- `GET`, `POST` `/api/v1/projects`
- `GET`, `PATCH` `/api/v1/projects/{project_id}` e `POST /api/v1/projects/{project_id}/archive`
- `GET`, `POST` `/api/v1/projects/{project_id}/datasets`
- `GET`, `DELETE` `/api/v1/datasets/{dataset_id}`
- `GET /api/v1/dashboard`

### Ingestão direta

- `POST /api/v1/datasets/{dataset_id}/uploads` registra um manifesto de arquivos, diretório ou ZIP e devolve planos de upload direto.
- Arquivos pequenos usam `PUT` pré-assinado; arquivos grandes usam multipart com URLs solicitadas em lotes de até 100 partes.
- `POST /api/v1/uploads/{session_id}/objects/{object_id}/complete` confirma tamanho e agenda validação assíncrona.
- Endpoints de `failed`, `retry` e `cancel` tratam interrupções recuperáveis sem afetar outros arquivos.
- `GET /api/v1/datasets/{dataset_id}/uploads` fornece o estado persistido de cada arquivo.

O navegador envia os bytes diretamente para MinIO/S3/R2. A API recebe apenas manifestos, ETags e comandos de controle. O worker calcula o SHA-256 confiável, extrai dimensões, dtype, bandas, NoData, CRS, transform e tags, e somente então promove uma cópia para uma chave imutável em `originals/`. TIFF é científico; PNG/JPEG recebe papel `auxiliary`; ZIP é inspecionado com limites e proteção contra path traversal.

### Visualizador e ROI

- `GET /api/v1/datasets/{dataset_id}/captures` lista capturas/repetições já associadas.
- `GET /api/v1/captures/{capture_id}` retorna bandas canônicas, metadados de grade e URLs curtas para previews PNG derivados.
- `GET`, `POST /api/v1/captures/{capture_id}/rois` lista ou cria ROIs desta captura.
- `POST /api/v1/rois/{roi_id}/versions` cria uma versão imutável com concorrência otimista por `expected_version`.
- `DELETE /api/v1/rois/{roi_id}` arquiva a identidade da ROI, preservando seu histórico.

O editor converte eventos do canvas para coordenadas normalizadas e então para pixels da imagem original. Cada versão registra as duas representações, resolução de referência, área, estimativa de pixels válidos, checksum e, quando disponível, CRS, transformação afim e coordenadas espaciais derivadas. A API recusa propagação se dimensões, CRS ou transformação das bandas forem incompatíveis. Não existe propagação automática entre repetições.

Enquanto o provedor de autenticação de pesquisadores não é integrado, desenvolvimento e testes usam uma ponte de identidade controlada pelo header `X-Dev-User-Id`. Ela é desativada por padrão em staging/produção; não a use como mecanismo de autenticação em deploys reais. Todas as consultas de recurso filtram pelo proprietário no servidor.

Consulte [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DATA_MODEL.md](docs/DATA_MODEL.md) e [docs/SECURITY.md](docs/SECURITY.md) antes de ampliar o domínio.
