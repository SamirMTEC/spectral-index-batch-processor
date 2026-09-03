# SpectralIndex Batch Processor — Pipeline de Processamento

## 1. Objetivo e garantias

A pipeline transforma um conjunto de originals imutáveis em resultados e exports reproduzíveis. Ela é assíncrona, reiniciável e tolerante a entrega duplicada de mensagens.

Garantias:

- nenhum original aceito é modificado;
- associação automática não vira verdade sem revisão/confirmação;
- um job usa snapshots imutáveis de inputs, ROI, índice e parâmetros;
- falha de um item é contida;
- output só é publicado após validação e checksum;
- progresso é monotônico dentro de uma tentativa, mas pode ser reconciliado após retry;
- toda decisão relevante aparece no manifesto.

## 2. Visão ponta a ponta

```mermaid
flowchart LR
    A[Criar dataset] --> B[Upload direto]
    B --> C[Quarantine e validação]
    C --> D[Extrair metadados e preview]
    D --> E[Detectar tratamento, repetição, captura e banda]
    E --> F[Revisão humana]
    F --> G[Dataset ready]
    G --> H[Definir ROI]
    H --> I[Selecionar índices e parâmetros]
    I --> J[Pré-validar e congelar snapshots]
    J --> K[Criar job e itens]
    K --> L[Processar itens em paralelo]
    L --> M[Validar e publicar resultados]
    M --> N[Estatísticas e comparação]
    N --> O[CSV / XLSX / GeoTIFF / manifesto]
```

## 3. Fase A — criação e upload

### 3.1 Manifesto do cliente

O navegador enumera os arquivos escolhidos e envia metadados em páginas:

```json
{
  "mode": "directory",
  "files": [
    {
      "client_file_id": "opaque-local-id",
      "relative_path": "tratamento-A/rep-01/capture-001_RED.tif",
      "size_bytes": 18423322,
      "declared_mime": "image/tiff",
      "checksum": null
    }
  ]
}
```

`client_file_id` é apenas correlação de UI. O servidor gera IDs e chaves. Para conjuntos muito grandes, o browser envia lotes de 100–500 e recebe sessões/URLs paginadas.

### 3.2 Modos de entrada

- **Arquivos individuais:** cada arquivo vira um UploadObject.
- **Diretório:** usa seleção de diretório suportada pelo navegador e preserva somente caminho relativo sanitizado. Há fallback para ZIP.
- **ZIP:** o ZIP é um único upload original de ingestão e é expandido por worker isolado. Cada entrada válida produz um BandAsset próprio; o ZIP fonte pode ser retido ou descartado segundo política explícita, mas sua checksum entra no manifesto de ingestão.

### 3.3 Multipart e retomada

- Arquivos acima de um limiar configurável usam multipart.
- O cliente limita concorrência e repete somente parts com erro.
- Backend registra `multipart_upload_id` e ETags; conclusão é idempotente.
- Sessões expiram; reconciliador aborta multiparts abandonados e lifecycle limpa staging.
- Upload concluído não significa asset aceito: segue para `validating`.

## 4. Fase B — quarantine, validação e materialização de originals

Para cada objeto recebido:

1. Confirmar existência, tamanho e versão no storage.
2. Fazer streaming para calcular SHA-256 confiável.
3. Detectar formato/MIME por magic bytes e parser seguro; não confiar em extensão.
4. Verificar formato suportado, dimensões, dtype, número de canais, metadados e limites.
5. Executar varredura de malware onde aplicável.
6. Rejeitar arquivo truncado, corrompido ou que exceda quota/limites.
7. Promover/copy-once para chave final gerada em originals, usando precondição “não existe”.
8. Verificar checksum/versão do destino antes de marcar BandAsset `accepted`.

### ZIP seguro

Antes e durante a extração:

- limitar tamanho do ZIP, número de entradas, tamanho total descompactado, tamanho por entrada, profundidade e razão de compressão;
- normalizar Unicode e separadores; rejeitar caminhos absolutos, `..`, links simbólicos/hardlinks e nomes especiais;
- descompactar em sandbox com CPU, memória, disco e tempo limitados;
- ignorar diretórios vazios/metadados do sistema conforme política, registrando aviso;
- não executar macros, scripts ou conteúdo incorporado;
- falhar o ZIP inteiro em violação de segurança; arquivos cientificamente inválidos podem ser rejeitados individualmente, conforme política apresentada ao usuário.

## 5. Fase C — metadados, previews e detecção

As tarefas podem ocorrer em paralelo após aceitação, com fan-out controlado.

### 5.1 Extração técnica

- dimensões, dtype, canais, nodata, CRS, affine transform, tags relevantes e timestamps;
- histograma amostrado e estatísticas leves para preview;
- thumbnail/tile derivado com checksum e versão do gerador;
- limites impedem decompression bomb em formatos de imagem.

### 5.2 Motor de detecção

O motor recebe, por asset:

- caminho relativo e nome normalizados;
- extensão e metadados embutidos;
- regras selecionadas do dataset;
- aliases do catálogo de bandas;
- contexto de outros arquivos do lote.

Ordem recomendada:

1. Metadado explícito validado.
2. Regra de caminho/nome específica do projeto.
3. Convenção padrão versionada.
4. Inferência contextual do conjunto.
5. Sem sugestão quando persistir ambiguidade.

Cada sugestão guarda:

```text
field, suggested_value, normalized_value,
rule_id, rule_version, matched_fragment,
confidence, warnings, competing_suggestions
```

Confiança não é probabilidade científica; é um indicador operacional. Thresholds apenas priorizam revisão. Nenhuma sugestão ambígua é confirmada automaticamente.

### 5.3 Formação de grupos

- Tratamento é criado pela chave normalizada.
- Repetição pertence exatamente a um tratamento.
- Capture agrupa a mesma repetição + instante/posição/chave de captura.
- BandAsset recebe uma banda canônica dentro da captura.

Validações cruzadas detectam:

- duas imagens candidatas para a mesma banda/captura;
- banda desconhecida ou alias que casa com múltiplas bandas;
- tratamento/repetição ausente;
- dimensões, CRS ou transform incompatíveis;
- conjunto de bandas incompleto;
- arquivos idênticos associados a grupos diferentes;
- timestamp/posição inconsistentes dentro da captura.

## 6. Fase D — revisão da associação de bandas

A UI trabalha sobre uma revisão rascunho com version/ETag.

### Recursos da tela

- agrupamento hierárquico por tratamento → repetição → captura → banda;
- tabela virtualizada e paginação server-side;
- filtros “sem banda”, “duplicado”, “baixa confiança”, “incompatível” e “não classificado”;
- preview sincronizado de bandas da captura;
- edição individual e em massa, undo local e resumo do impacto;
- explicação da regra/evidência para cada sugestão;
- contador de erros bloqueantes e avisos não bloqueantes.

### Confirmação

Ao confirmar:

1. API verifica optimistic lock.
2. Reexecuta invariantes no servidor, não apenas no cliente.
3. Persiste uma `ClassificationRevision` imutável com checksum.
4. Marca sugestões como aceitas/overridden e registra auditoria.
5. Move Dataset para `ready` se houver pelo menos uma captura processável e nenhum erro bloqueante.

Uma edição posterior cria nova revisão. Jobs existentes continuam referenciando a revisão anterior.

## 7. Fase E — visualizador e ROI

### 7.1 Referência espacial

Ao criar a ROI, registrar:

- asset/capture de referência;
- largura e altura da grade;
- tipo (`rectangle`/`polygon`);
- espaço (`pixel`, `normalized` ou `projected`);
- CRS/affine transform quando existentes;
- geometria e checksum.

O MVP não presume que imagens de mesmo tamanho estejam alinhadas. Compatibilidade exige critérios explícitos:

| Situação | Ação |
|---|---|
| mesmas dimensões e mesma grade/transform | aplicar diretamente |
| dimensões diferentes, mas CRS/transform válidos | fora do caminho estrito do MVP; transformação versionada futura |
| sem georreferência, dimensões diferentes | incompatível sem mapeamento/calibração explícita |
| mesma dimensão, alinhamento desconhecido | aviso e confirmação apenas se política científica permitir; default é bloquear |
| transformação registrada aprovada | aplicar transformação e registrar método/versão |

### 7.2 Rasterização da ROI

- Validar polígono simples, área positiva, vértices finitos e limites.
- Definir regra de inclusão de pixel (por exemplo, centro do pixel) e versão do rasterizador.
- Produzir máscara na grade de cada banda ou na grade de cálculo escolhida.
- Registrar transformação, resampling (se houver) e checksum da máscara.
- Máscara é derivada/cacheável e nunca modifica a banda.

No MVP, cálculo usa bandas já co-registradas na mesma grade; não há resampling silencioso.

## 8. Fase F — seleção de índices e pré-validação

### 8.1 Catálogo

Cada versão do índice declara:

- bandas canônicas requeridas;
- fórmula/implementação;
- schema e defaults de parâmetros;
- dtype de trabalho e output;
- política de nodata, divisão por zero e domínio inválido;
- faixa esperada (alerta, não clipping silencioso);
- versão do runtime/container.

### 8.2 Matriz de elegibilidade

O servidor constrói a matriz `capture × ROI × index` e classifica cada combinação:

- `eligible`;
- `missing_band`;
- `duplicate_band`;
- `roi_incompatible`;
- `invalid_parameter`;
- `asset_unavailable`.

Modo estrito recusa o job se houver combinação inválida. Um modo parcial futuro pode criar itens `skipped` explicitamente. A tela mostra total de itens e motivos antes da submissão.

### 8.3 Snapshot

Na submissão, congelar:

- classification revision;
- IDs, object versions e SHA-256 de cada BandAsset;
- ROI versions/geometrias;
- index definition versions;
- parâmetros validados;
- política de nodata/precisão;
- seleção de treatments/replicates/captures;
- versão do schema do job.

O checksum canônico da configuração, mais o `Idempotency-Key`, impede jobs duplicados por duplo clique/retry HTTP.

O worker lê `input_snapshot`, `roi_snapshot`, `band_mapping_snapshot` e `formula_version`
do item. Ele não volta à associação atual do dataset para jobs versionados e confirma o
SHA-256 do arquivo baixado antes de calcular. Assim, uma revisão posterior de banda, ROI
ou catálogo não muda a execução já submetida.

### 8.4 Processing Recipe e reprocessamento

- Uma recipe congela índices, versões de fórmula, parâmetros e estatísticas preferidas.
- A recipe pertence ao pesquisador, não ao dataset, e pode ser aplicada em outro dataset.
- “Reprocessar com os mesmos parâmetros” cria um novo `ProcessingJob` com
  `parent_job_id`; snapshot, checksum e versões científicas permanecem iguais.
- Items e resultados novos recebem IDs e object keys próprios. O job anterior permanece
  terminal e imutável.
- Jobs legados sem snapshot completo não podem alegar reprocessamento idêntico.

## 9. Fase G — criação, despacho e claim de items

1. Transação cria ProcessingJob `queued`, todos os ProcessingItems `queued` e evento outbox.
2. Publicador envia comandos após commit.
3. Worker recebe mensagem e tenta claim atômico:

```text
UPDATE processing_item
SET state='running', lease_owner=?, lease_expires_at=?, attempt_count=attempt_count+1
WHERE id=? AND state='queued' AND cancel_requested=false
RETURNING ...
```

4. Se claim não retorna linha, a mensagem é duplicada/stale e pode ser confirmada.
5. Worker cria ProcessingAttempt, inicia heartbeat e marca job `running` via transição idempotente.
6. Mensagem contém IDs e checksums, nunca credenciais permanentes.

Fairness limita itens simultâneos por projeto/usuário. Mensagens muito grandes são evitadas: snapshots ficam no banco; mensagem carrega referência e versão.

## 10. Fase H — execução de um ProcessingItem

### 10.1 Escopos de análise

Cada combinação `captura × índice` gera dois itens independentes e identificados no
snapshot imutável:

- `roi`: aplica a máscara da ROI confirmada; sem ROI, somente este item é marcado
  como `skipped`;
- `full_image`: calcula o mesmo índice sobre toda a grade original, preservando as
  máscaras de NoData e pixels inválidos.

Estatísticas e GeoTIFFs nunca são agregados entre escopos. O pacote completo separa
os rasters em `rasters/roi/` e `rasters/full_image/`, e exports específicos permitem
baixar apenas um desses conjuntos.

Estágios recomendados e pesos iniciais de progresso:

| Estágio | Peso | Operação |
|---|---:|---|
| `acquiring_inputs` | 10% | autorizar internamente, verificar versões/checksums e baixar/streamar |
| `decoding` | 10% | abrir rasters com limites e validar metadados contra snapshot |
| `building_mask` | 10% | rasterizar/obter máscara ROI e nodata comum |
| `normalizing` | 10% | converter cálculo para precisão definida, sem alterar original |
| `computing_index` | 30% | calcular em janelas/chunks |
| `statistics` | 10% | agregadores streaming sobre pixels válidos |
| `encoding_result` | 10% | escrever TIFF/GeoTIFF float32 temporário |
| `validating_output` | 5% | reabrir, validar tags/dtype/dimensões e checksum |
| `publishing` | 5% | promover objeto e transacionar IndexResult/item |

Pesos podem ser calibrados por telemetria, mas a versão da política deve ser registrada.

### Regras numéricas

- Decodificar conforme dtype de origem; cálculo em `float32` ou `float64` conforme definição versionada.
- Propagar nodata/máscara de qualquer banda requerida.
- Divisão por zero e valores não finitos seguem política do índice; normalmente tornam-se nodata/NaN, nunca zero silencioso.
- Não aplicar clipping a faixa esperada sem parâmetro explícito.
- Output científico é float32; quantização/colormap só em preview.
- Estatísticas ignoram nodata e registram contagens válidas/ausentes.
- Processamento em windows/chunks mantém uso de memória limitado.

### Publicação atômica lógica

1. Escrever em `staging/{attempt_id}`.
2. Fechar e reabrir arquivo; validar dtype, dimensões, geometadados e checksum.
3. Copy/promote para chave definitiva única com precondição de não sobrescrita.
4. Em transação compare-and-set, criar IndexResult/Statistics e marcar item `succeeded` se o attempt ainda é o vigente.
5. Se CAS falhar, não sobrescrever resultado; staging vira candidato a cleanup.

## 11. Falhas, retry e DLQ

### 11.1 Classificação

| Classe | Exemplos | Ação |
|---|---|---|
| Transitória | timeout de storage, rate limit, indisponibilidade da fila | retry com exponential backoff + jitter |
| Determinística de input | TIFF corrompido, banda incompatível, ROI inválida | falhar item; sem retry automático |
| Determinística de configuração | parâmetro inválido, versão inexistente | idealmente bloqueada na pré-validação; falhar job/item |
| Recurso | OOM, disco efêmero insuficiente | retry limitado em pool maior se política permitir |
| Bug/unknown | exceção não classificada | retry conservador; depois DLQ e alerta |
| Cancelamento | sinal do usuário | parar em checkpoint seguro, limpar staging e marcar cancelled |

### 11.2 Política

- `max_attempts` por classe/fila, normalmente 3 para transitórias.
- Backoff limitado e com jitter; respeitar `Retry-After`.
- Cada tentativa é append-only.
- Após limite, item `failed`; mensagem problemática vai à DLQ com referência, sem payload sensível.
- Reprocessar falhas cria job filho contendo snapshots dos itens selecionados; uma versão/correção diferente cria novo job comum, não “edita” o anterior.

### 11.3 Falhas globais

Marcar job `failed` sem executar itens quando snapshot está irrecuperável, versão de algoritmo não está disponível, credencial de workload/configuração central está inválida ou todos os itens falham sem resultado. Uma indisponibilidade temporária deve manter/recolocar itens em fila, não converter prematuramente em erro científico.

## 12. Cancelamento

1. API autoriza e grava `cancel_requested_at/by` no job.
2. Dispatcher deixa de publicar itens; itens `queued` viram `cancelled` em lote.
3. Workers consultam sinal entre estágios/chunks e interrompem com cleanup.
4. Publicação iniciada usa CAS: se resultado já foi confirmado antes do cancelamento efetivo, ele permanece válido e é marcado como parcial no contexto do job.
5. Reconciliador marca job `cancelled` apenas quando não há item ativo.

Cancelamento não apaga resultados nem logs produzidos.

## 13. Progresso e conclusão

### 13.1 Progresso

Cada item publica `current_stage` e `progress_basis_points` com throttling para não sobrecarregar banco/fila. Progresso do job é ponderado:

```text
sum(peso_item * progresso_item) / sum(peso_item)
```

No MVP, `peso_item=1`; pesos por pixels estimados são opção futura. Item terminal vale 100%, inclusive failed/cancelled para representar trabalho encerrado, enquanto contadores comunicam o desfecho. A UI nunca exibe “100% concluído” sem mostrar falhas.

### 13.2 Estado terminal reconciliado

Quando nenhum item está `queued/running`:

- todos succeeded → `completed`;
- algum succeeded e algum failed/skipped inesperado → `completed_with_errors`;
- nenhum succeeded e houve falha → `failed`;
- cancel_requested e itens restantes cancelados → `cancelled`.

Reconciliador recalcula contadores diretamente dos items e corrige divergências. Eventos SSE são notificações, não fonte de verdade.

## 14. Estatísticas e comparação

### 14.1 Por resultado/captura

Durante cálculo, acumuladores streaming produzem contagem válida, média, variância estável, min/max e amostra/estrutura para percentis. Método e aproximação dos percentis são versionados.

### 14.2 Por repetição

Uma repetição pode conter várias capturas. O MVP deve exigir uma política explícita:

- uma captura por repetição; ou
- média ponderada/não ponderada das estatísticas de captura; ou
- agregação de todos os pixels, claramente rotulada como pixel-weighted.

O default científico recomendado para comparação é obter um valor-resumo por repetição e usar a repetição como `sample_unit`.

### 14.3 Entre tratamentos

Para cada índice/ROI:

- tabela de valores por repetição;
- média/mediana, dispersão e contagem de repetições por tratamento;
- distribuição/boxplot descritivo;
- indicação de dados ausentes e resultados parciais.

Testes inferenciais, correções de múltiplas comparações e modelos de efeitos mistos ficam fora do MVP até o desenho experimental ser modelado de forma suficiente.

## 15. Visualização de resultados

- Servir preview/tile com colormap, faixa manual/automática e legenda.
- Exibir máscara/nodata e quality flags.
- Permitir alternar original/bandas/índice e manter viewport sincronizado quando grades forem compatíveis.
- Dados float permanecem no artifact; PNG/JPEG de visualização é derivado e não é fonte de export científico.
- URLs de acesso são curtas e emitidas após autorização.

## 16. Exports

Exports são jobs assíncronos independentes com snapshot de seleção.

### CSV

- UTF-8, cabeçalho estável, separador configurável com default vírgula.
- Ponto como separador decimal e datas ISO 8601.
- Uma linha por combinação declarada (por exemplo, tratamento/repetição/captura/ROI/índice).
- Colunas incluem IDs, labels, versões, estatísticas, flags e estado.
- Prevenir CSV injection prefixando/escapando valores de texto iniciados por `=`, `+`, `-` ou `@` segundo política documentada.

### XLSX

- Abas: `Summary`, `Per_Replicate`, `Per_Capture`, `Parameters`, `Warnings`, `Provenance`.
- Tipos numéricos reais, datas UTC e unidades no cabeçalho/metadados.
- Limite de linhas do formato verificado; dividir abas/arquivos ou oferecer CSV quando excedido.
- Fórmulas não confiáveis não são inseridas.

### GeoTIFF float32

- Um raster por IndexResult; ZIP quando houver múltiplos.
- `float32`, nodata/máscara explícita, CRS/affine transform quando conhecidos.
- Sem georreferência global, exportar TIFF com tags locais e sidecar/manifest explicando pixel coordinate space; não inventar CRS.
- Tags mínimas: job/item/result IDs, index/version e checksum do manifesto, sem PII.

### Manifesto

- JSON canônico versionado conforme [DATA_MODEL.md](./DATA_MODEL.md).
- Gerado após reconciliação do job; pode haver nova versão de manifesto quando exports adicionais surgirem, ligada ao anterior.
- Checksum SHA-256 e, futuramente, assinatura digital.

### Publicação e download

Worker escreve, valida, calcula checksum e promove artifact. `ready` só após referência durável. Download usa URL assinada curta; expiração do arquivo move registro para `expired`, sem apagar a evidência histórica.

## 17. Logs e histórico

### Eventos estruturados mínimos

- `upload.created`, `upload.completed`, `asset.accepted/rejected`;
- `classification.suggested/confirmed/overridden`;
- `roi.version_created`;
- `job.queued/started/cancel_requested/finished`;
- `item.claimed/stage_changed/retried/succeeded/failed/cancelled`;
- `result.published`, `export.ready/failed`, `download.authorized`.

Campos comuns: timestamp, severity, event code, request/trace/message IDs, project/job/item/attempt IDs, duração, métricas e error code. Não registrar bytes de imagem, geometria completa desnecessariamente, token, URL assinada ou stack trace ao usuário. Stack trace fica restrita ao backend de logs.

Histórico da UI é derivado de entidades e audit events, não de parsing de texto de log.

## 18. Reconciliadores e tarefas de manutenção

- Upload sessions expiradas e multiparts abandonados.
- Objetos em quarantine sem registro ou registros sem objeto.
- Outbox não publicada.
- Items `running` com lease/heartbeat expirado.
- Contadores/estado do job divergentes.
- Staging outputs órfãos.
- Results/artifacts cujo checksum/metadado não confere (amostragem periódica).
- Exports expirados e lifecycle.
- DLQ e jobs ativos acima do tempo esperado.

Toda correção automática é idempotente, limitada em lote, metrificada e auditável.

## 19. Testes de aceitação da pipeline

- Repetir a mesma submissão/idempotency key cria um único job.
- Entrega duplicada da fila produz um único IndexResult.
- Worker morto durante cada estágio é retomado/reexecutado sem output parcial publicado.
- Alterar ROI/associação após submissão não altera o job em curso.
- Um item corrompido resulta em `completed_with_errors` quando outros têm sucesso.
- Cancelamento encerra queued/running sem apagar sucessos anteriores.
- ZIP traversal/bomb é bloqueado antes de promover originals.
- Um dataset com 10.000 arquivos pode ser listado/revisado com paginação e virtualização.
- GeoTIFF reabre como float32 e preserva CRS/transform/nodata quando fornecidos.
- CSV/XLSX/manifest reproduzem os mesmos números armazenados.
- Manifesto referencia checksums e versões de todas as entradas/resultados.
