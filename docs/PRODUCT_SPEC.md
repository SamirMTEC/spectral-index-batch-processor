# SpectralIndex Batch Processor — Especificação de Produto

## 1. Visão

O **SpectralIndex Batch Processor** é uma plataforma web para pesquisadores organizarem grandes coleções de imagens espectrais, validarem a associação entre tratamentos, repetições e bandas, definirem regiões de interesse (ROIs), executarem índices espectrais em lote e obterem resultados científicos reproduzíveis.

O produto deve reduzir trabalho manual sem esconder decisões que afetam o resultado. Toda inferência automática é revisável; toda execução é versionada e rastreável; arquivos originais são imutáveis.

## 2. Objetivos e não objetivos

### Objetivos

- Ingerir centenas ou milhares de imagens sem trafegar os binários pelo backend da aplicação.
- Detectar tratamento, repetição, captura e banda a partir de nomes e metadados, apresentando confiança e conflitos.
- Permitir revisão em massa antes do processamento.
- Definir uma ROI em uma banda de referência e aplicá-la de forma coerente às bandas correspondentes.
- Executar índices de forma assíncrona, isolada e tolerante a falhas individuais.
- Disponibilizar visualização, estatísticas, comparação e exports reprodutíveis.
- Manter proveniência completa: entradas, parâmetros, versões, resultados, logs e manifesto.

### Não objetivos do MVP

- Substituir um GIS desktop completo.
- Editar radiometricamente arquivos originais.
- Orquestrar treinamento de modelos de machine learning.
- Oferecer colaboração em tempo real no mesmo canvas.
- Suportar mosaicos, ortorretificação ou alinhamento automático sofisticado.
- Executar processamento arbitrário enviado pelo usuário.

## 3. Personas e papéis

| Persona | Necessidade principal | Papel inicial |
|---|---|---|
| Pesquisador responsável | Organizar o experimento e garantir rastreabilidade | `project_owner` |
| Pesquisador colaborador | Revisar dados, criar ROIs e executar análises | `editor` |
| Assistente de pesquisa | Enviar e classificar imagens | `contributor` |
| Revisor/consultor | Inspecionar configuração e resultados | `viewer` |
| Operador da plataforma | Diagnosticar saúde sem acessar dados por padrão | `platform_admin` |

Permissões são atribuídas por projeto. `platform_admin` não implica acesso automático ao conteúdo científico; acesso excepcional deve ser temporário, justificado e auditado.

## 4. Conceitos do produto

- **Projeto:** limite de colaboração, autorização e organização de uma pesquisa.
- **Dataset:** lote lógico de imagens originais e seus metadados, importado para um projeto.
- **Tratamento:** grupo experimental (por exemplo, irrigado ou controle).
- **Repetição:** unidade replicada dentro de um tratamento.
- **Captura:** conjunto de bandas que representa a mesma observação de uma repetição em um instante/posição.
- **Band asset:** arquivo original associado a uma banda de uma captura.
- **ROI:** geometria retangular ou poligonal em um sistema de coordenadas explicitamente declarado.
- **Job:** fotografia imutável da configuração submetida ao processamento.
- **Item:** menor unidade isolada de trabalho, normalmente um índice para uma captura e uma ROI.
- **Resultado:** raster e estatísticas gerados por um item.
- **Export artifact:** arquivo derivado sob demanda (CSV, XLSX, GeoTIFF ou manifesto).

## 5. Jornadas principais

### 5.1 Descoberta e acesso

1. Visitante acessa landing page pública com proposta de valor, fluxo, índices suportados, limites e política de dados.
2. Pesquisador cria conta, verifica e-mail e autentica-se.
3. Usuário cria um projeto ou aceita convite para um projeto existente.

### 5.2 Ingestão e revisão

1. Pesquisador cria um dataset e escolhe arquivos, diretório ou ZIP.
2. Cliente solicita sessões de upload e envia diretamente ao object storage por URLs pré-assinadas/multipart.
3. Plataforma valida tamanho, checksum, formato e segurança; ZIP é expandido em área isolada.
4. Metadados e padrões de nome são analisados para sugerir tratamento, repetição, captura e banda.
5. Tela de revisão mostra agrupamentos, confiança, campos ausentes, duplicidades e conflitos.
6. Usuário corrige individualmente ou em massa e confirma o dataset.

### 5.3 ROI e processamento

1. Usuário abre uma captura em visualizador com ajuste de contraste e escolha da banda base.
2. Desenha ROI retangular ou poligonal livre.
3. Plataforma valida limites e compatibilidade geométrica, mostrando em quais bandas a ROI pode ser aplicada.
4. Usuário seleciona índices e configura parâmetros.
5. Uma pré-validação apresenta itens esperados, combinações inválidas e estimativa qualitativa de custo.
6. Usuário submete; o job progride em segundo plano e pode ser cancelado.
7. Falha de um item não cancela os demais, salvo falha sistêmica ou configuração global inválida.

### 5.4 Resultados e exportação

1. Usuário filtra resultados por tratamento, repetição, captura, ROI e índice.
2. Visualiza raster, máscara, legenda, estatísticas e alertas de qualidade.
3. Compara distribuições e estatísticas agregadas entre tratamentos.
4. Solicita CSV, XLSX, GeoTIFF `float32` e manifesto completo.
5. Consulta histórico, configuração, versões, logs sanitizados e tentativas.

## 6. Requisitos funcionais

### 6.1 Landing page

- Conteúdo público, responsivo e indexável.
- CTA para cadastro/login, documentação de formatos e política de privacidade.
- Nenhum identificador de projeto ou conteúdo privado em analytics públicos.

### 6.2 Autenticação e colaboração

- Cadastro, verificação de e-mail, login, logout e recuperação de senha.
- OAuth/OIDC institucional é futuro; arquitetura deve permitir provedor externo.
- Sessões revogáveis e MFA opcional no MVP, exigível em versão futura.
- Convites e papéis por projeto: owner, editor, contributor e viewer.

### 6.3 Dashboard e projetos

- Resumo de projetos recentes, datasets, jobs ativos, falhas e uso de armazenamento.
- Criar, renomear, arquivar e listar projetos conforme permissão.
- Arquivamento preserva dados e histórico; exclusão segue retenção e confirmação explícita.

### 6.4 Datasets e uploads

- Upload de arquivos individuais, seleção de diretório pelo navegador e ZIP.
- Preservar `relative_path` para inferência, sem aceitar caminhos absolutos ou travessia de diretórios.
- Upload direto, retomável e multipart para arquivos grandes.
- Deduplicação por dataset com checksum, sem sobrescrever originais.
- Feedback por arquivo: pendente, enviando, recebido, validando, aceito ou rejeitado.
- O dataset só fica pronto para análise após conclusão explícita da ingestão.

### 6.5 Detecção e associação

- Extração por regras configuráveis de nome/pasta e metadados embutidos.
- Sugestões para tratamento, repetição, captura e banda, cada uma com origem e confiança.
- Catálogo canônico de bandas com aliases; não confundir automaticamente bandas ambíguas.
- Tela tabular/agrupada, filtros de inconsistência, edição em massa e preview da captura.
- Bloquear confirmação enquanto houver banda obrigatória ausente, associação ambígua ou duplicidade não resolvida para um índice selecionável.

### 6.6 Visualizador e ROI

- Visualizar formatos suportados por meio de preview derivado; originals não são alterados.
- Pan, zoom, histograma/contraste, alternância de banda e overlay de ROI.
- ROI retangular e poligonal livre; vértices editáveis e validação de geometria.
- Armazenar geometria normalizada e também a base espacial usada na criação.
- Aplicar a ROI às bandas de uma captura somente quando grade, dimensões e alinhamento forem compatíveis; caso contrário, exigir transformação registrada ou correção manual.
- Versionar ROI: editar cria nova versão; jobs antigos continuam apontando para a versão usada.

### 6.7 Índices e parâmetros

- Catálogo versionado de índices, com fórmula, bandas requeridas, domínio, parâmetros, limites e versão do algoritmo.
- Pelo menos um conjunto inicial configurado pelo produto; a lista exata deve ser aprovada cientificamente antes da implementação.
- Validar disponibilidade de bandas e parâmetros antes de criar o job.
- Salvar snapshot imutável da fórmula/configuração no job.

### 6.8 Processamento e progresso

- Fila assíncrona, workers escaláveis e idempotência por item.
- Progresso em níveis: job, captura e item; atualização por SSE ou polling com fallback.
- Estados detalhados e contadores reconciliáveis.
- Cancelamento cooperativo: itens não iniciados são cancelados; item em execução verifica sinal entre etapas seguras.
- Retry automático apenas para erros transitórios; erros determinísticos exigem correção/nova execução.
- Reexecutar somente itens com falha cria novo job ligado ao anterior, preservando histórico.

### 6.9 Resultados, estatísticas e comparação

- Raster do índice por captura/ROI, armazenado como GeoTIFF `float32` quando houver georreferência ou TIFF `float32` com metadados espaciais locais quando não houver CRS global.
- Nodata e máscara de pixels inválidos explícitos; nunca converter silenciosamente NaN para zero.
- Estatísticas por repetição: contagem válida, média, mediana, desvio padrão, mínimo, máximo e percentis configurados.
- Agregação entre capturas deve declarar unidade e método; não tratar pixels como réplicas independentes.
- Comparação entre tratamentos com tabela e visualização descritiva. Inferência estatística avançada fica fora do MVP.
- Proveniência acessível a partir de cada resultado.

### 6.10 Exports, manifesto, logs e histórico

- CSV em UTF-8 com esquema documentado e valores em formato independente de locale.
- XLSX com abas para resumo, resultados, parâmetros e avisos.
- GeoTIFF `float32` por resultado, com CRS/transform quando conhecidos, nodata e tags de proveniência.
- Para múltiplos arquivos, empacotar artifacts em ZIP gerado assincronamente.
- Manifesto JSON contendo entradas/checksums, associações, ROI, índice, parâmetros, versões, ambiente, itens, resultados, erros e artifacts.
- Logs técnicos estruturados e sanitizados, filtráveis por job/item e com correlação.
- Histórico imutável de execuções, tentativas, cancelamentos e exports.

## 7. Estados visíveis ao usuário

| Entidade | Estados principais |
|---|---|
| Dataset | `draft`, `uploading`, `ingesting`, `needs_review`, `ready`, `invalid`, `archived` |
| Upload/asset | `pending`, `uploading`, `uploaded`, `validating`, `accepted`, `rejected` |
| ProcessingJob | `queued`, `running`, `completed`, `completed_with_errors`, `failed`, `cancelled` |
| ProcessingItem | `queued`, `running`, `succeeded`, `failed`, `cancelled`, `skipped` |
| ExportArtifact | `queued`, `generating`, `ready`, `failed`, `expired` |

Transições e invariantes completas estão em [DATA_MODEL.md](./DATA_MODEL.md).

## 8. Requisitos não funcionais e metas iniciais

As metas são SLOs de projeto, a validar com telemetria e orçamento.

| Área | Meta inicial |
|---|---|
| Escala de ingestão | 10.000 arquivos por dataset sem exigir uma requisição HTTP por arquivo ao backend de aplicação |
| Tamanho | multipart configurável; limite por arquivo e por projeto definido por plano/quota |
| Disponibilidade da API | 99,5% mensal no MVP, excluindo manutenção anunciada |
| Durabilidade | originals em storage durável, versionado e com política contra sobrescrita |
| Progresso | atualização percebida em até 5 s para jobs ativos, sob operação normal |
| Idempotência | submissões repetidas não geram processamento/resultados duplicados |
| Recuperação | workers podem reiniciar sem perder estado confirmado |
| Observabilidade | 100% dos jobs e itens com correlation ID, métricas e eventos de estado |
| Acessibilidade | WCAG 2.2 AA para fluxos principais |
| Compatibilidade | versões atuais dos navegadores modernos; seleção de diretório onde File System API/webkitdirectory for suportada |
| Privacidade | isolamento por projeto, criptografia em trânsito e repouso, retenção configurável |

## 9. Critérios de sucesso

- Pesquisador conclui upload, revisão, ROI, processamento e export sem intervenção operacional.
- Percentual de associações automáticas aceitas, com medição separada por regra e nível de confiança.
- Taxa de jobs concluídos e concluídos com erros; distribuição de causas por item.
- Tempo entre finalização do upload e dataset revisável.
- Tempo de fila versus processamento.
- Taxa de retry e de correções manuais.
- Capacidade de reproduzir um resultado usando o manifesto e as versões referenciadas.

Métricas nunca devem misturar eventos analíticos de produto com conteúdo científico sensível.

## 10. Escopo do MVP

- Landing page, autenticação por e-mail/senha e papéis por projeto.
- Dashboard, projetos e datasets.
- Upload direto de arquivos, diretório e ZIP com multipart e validação.
- Detecção baseada em regras de tratamento, repetição, captura e banda; revisão manual/em massa.
- Preview de imagens; ROI retangular e poligonal em bandas já alinhadas.
- Catálogo inicial versionado de índices e parâmetros validados.
- Processamento assíncrono com progresso, cancelamento, retry transitório e isolamento de falhas.
- Visualização de resultados e estatísticas descritivas por repetição/tratamento.
- CSV, XLSX, GeoTIFF `float32`, manifesto JSON, logs e histórico.
- Implantação single-region com workers escaláveis horizontalmente.

## 11. Versões futuras

- SSO/OIDC/SAML, SCIM e MFA obrigatório por organização.
- Organizações, quotas/planos, cobrança e centros de custo.
- Detecção treinável por projeto e templates de convenção de nomes.
- Registro automático subpixel, reprojeção e ROIs em coordenadas geográficas.
- Mosaicos, séries temporais, dados hiperespectrais e Cloud Optimized GeoTIFF.
- Fórmulas customizadas em DSL segura e catálogo compartilhável.
- Estatística inferencial com desenho experimental explícito.
- API pública, webhooks e integração com notebooks.
- Execução multi-região, compute próximo ao storage e classes de GPU.
- Revisão/aprovação formal e assinaturas de releases científicos.

## 12. Decisões, alternativas, riscos e trade-offs

### Decisões

- Separar plano de controle (API/metadados) do plano de dados (object storage/workers).
- Tratar originals como imutáveis e resultados como derivados versionados.
- Exigir confirmação humana da associação antes do processamento.
- Usar item granular para conter falhas e permitir retry seletivo.
- Congelar configuração e versões no momento da submissão.

### Alternativas consideradas

- **Upload passando pelo backend:** mais simples inicialmente, rejeitado por custo, timeout e falta de escalabilidade.
- **Processamento síncrono:** simples para demos, inadequado para lotes grandes e recuperação.
- **Um job por arquivo:** isolamento máximo, porém excesso de coordenação; adotado item por captura/índice/ROI com etapas internas por banda.
- **ROI apenas em pixels:** fácil, mas ambígua entre resoluções; adotado modelo espacial explícito.
- **Falha total por qualquer item:** consistência aparente, mas desperdiça lotes grandes; adotado `completed_with_errors`.

### Riscos e mitigação

| Risco | Mitigação |
|---|---|
| Nomes ambíguos geram associação incorreta | confiança, regras explicáveis, revisão obrigatória e bloqueio de conflitos |
| Bandas não registradas tornam ROI inválida | checagem de compatibilidade e proibição de transformação implícita |
| ZIP bomb/malware/path traversal | limites de expansão, sandbox, varredura, paths normalizados |
| Custos altos de storage/egress/compute | quotas, lifecycle, previews, estimativas e métricas por projeto |
| Estatísticas cientificamente enganosas | unidade experimental explícita, manifesto e avisos; sem inferência automática no MVP |
| Jobs presos ou duplicados | leases, heartbeat, idempotency keys e reconciliador |
| Exposição por URL assinada/log | TTL curto, escopo mínimo, redaction e autorização antes da emissão |

### Trade-offs

- Revisão humana adiciona uma etapa, mas reduz erro científico silencioso.
- Snapshots e artifacts imutáveis consomem mais armazenamento, mas garantem auditoria e reprodução.
- Arquitetura assíncrona aumenta complexidade operacional, mas é necessária para escala e tolerância a falhas.
- Compatibilidade estrita de grade limita datasets inicialmente, mas evita aplicar ROIs incorretamente.

## 13. Rastreabilidade dos requisitos solicitados

| # | Capacidade | Definição principal | Detalhamento técnico |
|---:|---|---|---|
| 1 | Landing pública | § 6.1 | ARCHITECTURE § 3, SECURITY § 6 |
| 2 | Autenticação de pesquisadores | § 6.2 | DATA_MODEL § 3, SECURITY § 4 |
| 3 | Dashboard | § 6.3 | ARCHITECTURE § 8 |
| 4 | Projetos de pesquisa | § 6.3 | DATA_MODEL § 3.2–3.3 |
| 5 | Datasets em projetos | § 6.4 | DATA_MODEL § 4.1 |
| 6 | Arquivos, diretório e ZIP | § 6.4 | PROCESSING_PIPELINE § 3 |
| 7 | Lotes grandes | § 8 | ARCHITECTURE § 5, § 9 |
| 8 | Detecção automática | § 6.5 | PROCESSING_PIPELINE § 5 |
| 9 | Revisão de bandas | § 6.5 | PROCESSING_PIPELINE § 6 |
| 10 | Visualizador | § 6.6 | ARCHITECTURE § 8, PROCESSING_PIPELINE § 15 |
| 11 | ROI retangular/poligonal | § 6.6 | DATA_MODEL § 5 |
| 12 | ROI em bandas correspondentes | § 6.6 | PROCESSING_PIPELINE § 7 |
| 13 | Seleção de índices | § 6.7 | DATA_MODEL § 6 |
| 14 | Parâmetros | § 6.7 | PROCESSING_PIPELINE § 8 |
| 15 | Processamento assíncrono | § 6.8 | ARCHITECTURE § 6–7 |
| 16 | Progresso | § 6.8 | PROCESSING_PIPELINE § 13 |
| 17 | Falhas individuais | § 6.8 | PROCESSING_PIPELINE § 11 |
| 18 | Visualização dos índices | § 6.9 | PROCESSING_PIPELINE § 15 |
| 19 | Estatísticas por repetição | § 6.9 | DATA_MODEL § 7.5, PROCESSING_PIPELINE § 14 |
| 20 | Comparação entre tratamentos | § 6.9 | PROCESSING_PIPELINE § 14.3 |
| 21 | CSV | § 6.10 | PROCESSING_PIPELINE § 16 |
| 22 | XLSX | § 6.10 | PROCESSING_PIPELINE § 16 |
| 23 | GeoTIFF float32 | § 6.9–6.10 | PROCESSING_PIPELINE § 16 |
| 24 | Manifesto completo | § 6.10 | DATA_MODEL § 9 |
| 25 | Logs | § 6.10 | SECURITY § 13, PROCESSING_PIPELINE § 17 |
| 26 | Histórico de execuções | § 6.10 | DATA_MODEL § 7–8 |

## 14. Questões a validar antes da implementação

- Formatos de entrada e bandas canônicas do primeiro domínio científico.
- Fórmulas, intervalos e defaults aprovados para o catálogo inicial de índices.
- Tamanho típico/máximo por arquivo e dataset, concorrência e prazo esperado.
- Se as imagens possuem CRS/transform, somente grade de pixels ou ambas.
- Regras reais de nomenclatura e exemplos anonimizados.
- Política de retenção, residência dos dados e exigências institucionais.
- Modelo de colaboração, quotas e responsabilidade por exclusão.
