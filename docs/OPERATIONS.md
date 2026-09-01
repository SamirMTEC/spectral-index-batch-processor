# Operações de produção

## Objetivos operacionais iniciais

Estes valores são pontos de partida e devem ser revisados após 30 dias de telemetria:

- disponibilidade mensal da API: 99,5%;
- p95 de rotas de controle (sem upload/processamento): menor que 750 ms;
- 99% dos jobs aceitos saem de `queued` ou apresentam erro acionável em até 5 min;
- RPO PostgreSQL: 15 min; RTO PostgreSQL: 2 h;
- nenhum original confirmado pode ser alterado em lugar.

## Probes e encerramento

`GET /health` é liveness e não consulta dependências. Use-o para decidir reinício do
processo. `GET /ready` testa PostgreSQL, Redis e R2 em paralelo com timeout e não divulga
mensagens internas; use-o para retirar/incluir réplicas no balanceador.

No deploy ou scale-down, o orquestrador envia `SIGTERM`, retira a réplica do tráfego e
aguarda pelo menos 60 s. Uvicorn tem 45 s para finalizar requests e fechar pools. Celery
deve parar de buscar novas tarefas e concluir/reenfileirar itens conforme late ack. Nunca
use `SIGKILL` como caminho normal.

## Logs estruturados

API e workers escrevem JSON em `stdout`; o runtime coleta e envia ao agregador. Cada
request recebe `X-Request-ID`. Registre campos como `event`, `level`, `timestamp`,
`request_id`, `job_id`, `item_id`, `dataset_id`, `duration_ms` e `error_code`.

Nunca registre:

- URLs pré-assinadas ou query strings;
- `Authorization`, cookies, tokens, URLs de banco/Redis ou chaves R2;
- stack trace em resposta ao usuário;
- pixels/imagens brutos ou metadados pessoais sem necessidade.

Retenção inicial: 30 dias para logs de aplicação e 180 dias para audit trail científico,
ajustada à política institucional. Controle de acesso e trilha de consulta são obrigatórios.

## Métricas e alertas

Colete no mínimo:

- requests por status, p50/p95/p99 e timeouts;
- readiness por dependência e saturação do pool PostgreSQL;
- conexões, locks, replication lag, disco e transações longas do PostgreSQL;
- memória Redis, clientes, rejected connections, lag/tamanho da fila e idade do item mais
  antigo;
- workers ativos, tasks running/retried/failed, soft/hard timeouts e duração por índice;
- jobs por estado e diferença entre `processed_items` e `total_items`;
- R2: erros 4xx/5xx, bytes, multipart incompletos e falhas de checksum.

Alertas prioritários:

| Severidade | Condição inicial | Ação |
|---|---|---|
| P1 | perda/alteração de original, vazamento entre owners, backup irrecuperável | conter acesso, preservar evidência, acionar responsável |
| P1 | API indisponível por 10 min | verificar ingress, `/health`, `/ready`, dependências e rollback |
| P2 | fila sem progresso por 15 min | verificar Redis, workers, OOM e item venenoso |
| P2 | PostgreSQL > 80% disco ou conexões por 15 min | reduzir carga, investigar queries, ampliar capacidade |
| P2 | erro de itens > 10% por 15 min | agrupar por `error_code`, pausar submissões se sistêmico |
| P3 | URL pré-assinada expirada ou CORS crescente | validar relógio, expiry e política CORS |

## Limites e capacidade

Valores padrão de produção:

- request para API: 16 MiB;
- timeout de request do control plane: 30 s;
- upload individual: 50 GiB;
- sessão de upload: 100 GiB e 10.000 arquivos;
- dataset: 1 TiB;
- ZIP expandido: 100 GiB, 10.000 entradas, razão máxima 200:1;
- raster: 500 milhões de pixels, 64 bandas e 64 GiB decodificados;
- timeout científico: soft 900 s, hard 930 s;
- Celery concurrency: 2 por container, prefetch 1;
- API: 2 processos por container, pools de 5 + overflow 5 por processo.

O limite de conexões do PostgreSQL precisa cobrir:
`réplicas API × workers API × (pool + overflow) + processos Celery + ingestão + migrations`,
com reserva administrativa. Reduza pools ou use pooler compatível antes de escalar réplicas.

Uploads grandes não passam pela API. Para falhas multipart, gere URLs novas somente para
as partes pendentes e aborte sessões expiradas. Uma URL pré-assinada expirada pode aparecer
ao browser como erro CORS; renovar antes do vencimento evita ambiguidade.

## Backup do PostgreSQL

### Política

- backups automáticos do provedor diariamente;
- WAL/PITR contínuo com janela mínima de 14 dias;
- retenção mensal de 12 meses quando exigida pelo projeto científico;
- cópia lógica semanal criptografada (`pg_dump --format=custom`) em destino separado;
- criptografia em trânsito e repouso, acesso por papel e alertas de falha;
- inventário das versões PostgreSQL e extensões junto do backup.

Um backup só é válido após restore. Mensalmente restaure a cópia mais recente em uma
instância isolada, execute migrations/status, contagens de projetos/jobs/assets e verifique
checksums amostrais contra R2. Registre duração, ponto restaurado e evidência.

### Restore

1. Declare incidente e congele migrations/escritas quando necessário.
2. Escolha timestamp anterior ao evento e restaure PITR em **nova instância**.
3. Restrinja rede e valide integridade/migrations.
4. Compare jobs, manifestos e referências de objetos R2.
5. Troque o secret de conexão via deploy controlado.
6. Verifique `/ready` e smoke test; mantenha instância anterior isolada até encerrar o
   período de observação.

`pg_dump` oferece uma cópia lógica portável, mas não substitui PITR/WAL. Nunca grave dumps
no filesystem efêmero do container; envie por streaming ao cofre de backup.

## Proteção e ciclo de vida do R2

O bucket permanece privado: `r2.dev` desativado, sem custom domain e sem listagem pública.
Somente API e workers possuem token bucket-scoped. Downloads e previews são URLs GET
curtas e específicas; uploads são PUT/multipart específicos. Rotacione chaves a cada 90
dias ou imediatamente após suspeita de exposição.

- `originals/`: versionamento lógico por chave, nunca sobrescrever; bucket lock conforme
  retenção institucional.
- `results/` e `manifests/`: retenção igual ao job científico.
- `previews/` e `tmp/`: lifecycle documentado; abortar multipart incompleto.
- exclusão: autorização por owner, audit trail e política científica/legal.

R2 não é backup do banco: o banco contém ownership e ligações de reprodutibilidade. O
banco também não é backup dos objetos: mantenha política institucional de retenção e,
quando necessário, replicação/export periódico para conta ou provedor separado.

## Runbooks

### `/ready` falha

1. Consulte o array `checks` sem reiniciar todas as réplicas.
2. PostgreSQL: conectividade TLS, credenciais, limite de conexão e locks.
3. Redis: memória, autenticação, TLS e failover.
4. R2: endpoint, token, permissão `HeadBucket` e status Cloudflare.
5. Se a falha começou com release, reverta a imagem; não reverta banco automaticamente.

### Fila cresce sem progresso

1. Confirme workers saudáveis e consumers ativos.
2. Compare idade da fila e tasks running; procure OOM/hard timeout.
3. Agrupe falhas por `error_code`, asset e capture.
4. Isole item corrompido; não derrube o lote inteiro.
5. Escale concurrency somente após medir RAM e I/O.

### Falha de upload R2

1. Valide CORS usando `Origin: https://spectral.classlab.com.br`.
2. Confirme relógio, expiração, Content-Type/checksum assinados e tamanho da parte.
3. Liste partes pelo backend autenticado; gere retry apenas das ausentes.
4. Aborte multipart abandonado. Nunca marque `uploaded` sem confirmação/checksum.

### Release científico incorreto

1. Pause criação de novos jobs para a versão afetada.
2. Preserve jobs, rasters, manifests e logs como evidência; não sobrescreva.
3. Identifique `software_version`, `formula_version` e recipe snapshots afetados.
4. Corrija e publique nova versão; reprocessamento cria resultados independentes.
5. Comunique limitação e conjunto afetado aos pesquisadores.

## Manutenção e rollback

- Atualizações de dependências passam por testes científicos e scan antes do deploy.
- Rebuild periódico aplica patches da imagem base sem alterar tags já publicadas.
- Rollback de código usa digest anterior; Pages usa deployment anterior.
- Schema segue expand-and-contract. Downgrade destrutivo não é procedimento operacional.
- A cada trimestre: restore drill, rotação de R2, teste de cancelamento/timeout, teste de
  graceful shutdown e revisão de limites.

## Checklist de plantão

- [ ] backup e PITR saudáveis;
- [ ] restore drill dentro do RTO;
- [ ] `/health` e `/ready` monitorados separadamente;
- [ ] fila, idade do item e workers monitorados;
- [ ] logs sem secrets/presigned URLs;
- [ ] bucket privado e CORS exato;
- [ ] imagens em digest e migration revision registradas;
- [ ] runbook de rollback testado em staging.

## Referências

- [PostgreSQL: backup e restore](https://www.postgresql.org/docs/current/backup.html)
- [PostgreSQL: pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html)
- [PostgreSQL: continuous archiving e PITR](https://www.postgresql.org/docs/current/continuous-archiving.html)
- [Cloudflare R2: data security](https://developers.cloudflare.com/r2/reference/data-security/)
- [Cloudflare R2: object lifecycles](https://developers.cloudflare.com/r2/buckets/object-lifecycles/)
