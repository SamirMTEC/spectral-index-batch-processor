# SpectralIndex Batch Processor — Segurança

## 1. Objetivos e postura

A plataforma processa dados científicos privados e arquivos não confiáveis. Segurança é aplicada em profundidade nos limites de identidade, projeto, API, object storage, fila e workers.

Objetivos:

- confidencialidade e isolamento entre projetos;
- integridade de originals, configurações, resultados e manifesto;
- disponibilidade sob lotes grandes ou maliciosos;
- rastreabilidade de ações e acessos sensíveis;
- privilégio mínimo para pessoas e workloads;
- recuperação de erro, comprometimento e deleção acidental.

Este documento define controles técnicos; não declara conformidade com LGPD, GDPR ou normas institucionais. A base legal, controlador/operador, residência e prazos devem ser validados antes de produção.

## 2. Dados e classificação

| Classe | Exemplos | Proteção mínima |
|---|---|---|
| Pública | landing, documentação pública | integridade, WAF/CDN, headers seguros |
| Interna | métricas agregadas, códigos de erro | autenticação operacional e retenção limitada |
| Confidencial | originals, resultados, ROIs, nomes de projetos, manifests | autorização por projeto, criptografia, URLs curtas, auditoria |
| Sensível de identidade | e-mail, sessões, convites, IP | acesso restrito, minimização, redaction e retenção definida |
| Secreta | chaves, tokens, credenciais de workload | secret manager, rotação, nunca em banco/log/mensagem |

Nomes de arquivo, caminhos, metadados EXIF/TIFF e geometria podem revelar informação confidencial e devem herdar a classificação do projeto.

## 3. Ameaças e limites de confiança

### Atores/ameaças

- visitante não autenticado explorando landing/API;
- usuário autenticado tentando acessar outro projeto;
- colaborador abusando de sua permissão ou link antigo;
- arquivo/ZIP construído para explorar parser, consumir recursos ou atravessar caminhos;
- URL pré-assinada vazada;
- mensagem duplicada/adulterada ou worker comprometido;
- dependência/container comprometido;
- operador com privilégio excessivo;
- deleção/sobrescrita acidental e falha regional.

### Limites

1. Internet → CDN/WAF/landing.
2. Browser → provedor de identidade/API.
3. Browser → object storage por URL pré-assinada.
4. API → banco/fila/storage/secret manager.
5. Fila → workers.
6. Workers → arquivos não confiáveis e storage.
7. Plataforma → observabilidade/administração.

Toda passagem revalida identidade, escopo, formato e tamanho adequado ao limite.

## 4. Autenticação e sessão

- Preferir provedor OIDC maduro; separar `User` do `auth_subject`.
- Verificação de e-mail antes de criar uploads/jobs ou convidar membros.
- Senhas, quando usadas, seguem política do provedor e são armazenadas com algoritmo resistente (Argon2id/bcrypt conforme capacidade), salt único e parâmetros atualizáveis.
- Recuperação usa token aleatório, single-use, de curta duração e armazenado como hash.
- Cookies de sessão: `Secure`, `HttpOnly`, `SameSite=Lax` ou `Strict`, path/domain mínimos; rotação após login e mudança de privilégio.
- Sessões revogáveis com timeout ocioso/absoluto; logout e suspensão invalidam refresh tokens.
- CSRF token em operações mutáveis quando autenticação usa cookie; APIs bearer não aceitam token em query string.
- Rate limiting e detecção de abuso em login, recuperação, convite e emissão de URLs.
- MFA opcional no MVP e obrigatório para administradores; evolução para enforcement por projeto/organização.
- Não revelar se um e-mail existe em recuperação/cadastro além do necessário.

## 5. Autorização

### 5.1 RBAC por projeto

| Ação | Owner | Editor | Contributor | Viewer |
|---|:---:|:---:|:---:|:---:|
| Ver projeto/dataset/resultados | ✓ | ✓ | ✓ | ✓ |
| Enviar arquivos e revisar associação | ✓ | ✓ | ✓ | — |
| Criar/editar ROI | ✓ | ✓ | ✓ | — |
| Criar/cancelar job e export | ✓ | ✓ | conforme política | —/download permitido |
| Gerir membros e retenção | ✓ | — | — | — |
| Arquivar/excluir projeto | ✓ | — | — | — |

A permissão exata de Contributor para processamento/export é configurável no produto, mas deve ser consistente no backend.

### 5.2 Regras de enforcement

- Toda rota resolve o recurso e compara `project_id` com membership ativa.
- Nunca aceitar `project_id` do cliente como prova; derivá-lo do recurso quando possível.
- Consultas incluem escopo do projeto (`WHERE project_id = :authorized_project`) e retornam 404 quando revelar existência seria indevido.
- FKs compostas/constraints impedem referências cross-project.
- PostgreSQL Row-Level Security pode ser defesa adicional com contexto transacional; testes devem provar que conexão pooled não vaza contexto.
- URLs assinadas, mensagens, cache keys, SSE e exports herdam a mesma autorização.
- Revogação de membership bloqueia novas URLs e sessões de progresso imediatamente; URLs já emitidas são mitigadas por TTL curto e, quando suportado, revogação/policy.

### 5.3 Administração excepcional

Administradores da plataforma não recebem acesso permanente a conteúdo de projetos. Suporte excepcional exige ticket/razão, aprovação quando aplicável, elevação temporária, banner ao operador, auditoria e notificação ao owner conforme política. Operações administrativas usam identidade separada e MFA.

## 6. Segurança da aplicação web e API

- TLS 1.2+ em trânsito, HSTS e redirect permanente para HTTPS.
- Headers: CSP restritiva com nonce/hash, `frame-ancestors 'none'` ou allowlist, `X-Content-Type-Options: nosniff`, Referrer-Policy e Permissions-Policy.
- Escape contextual no frontend; sanitizar rich text ou não aceitá-lo no MVP.
- CORS allowlist exata; nunca `*` com credenciais.
- Schema validation server-side, limites de payload/campos/listas e rejeição de propriedades inesperadas onde crítico.
- OpenAPI e códigos de erro estáveis; detalhes internos/stack traces nunca retornam ao cliente.
- Paginação e limites máximos evitam enumeração e consultas explosivas.
- Idempotency keys vinculadas a ator, projeto, endpoint e hash do payload; reutilização com payload diferente retorna conflito.
- Concorrência otimista evita overwrite de revisão/ROI.
- Rate limits por IP, usuário e projeto, com limites específicos para upload/job/export.
- Proteção contra injection: queries parametrizadas, sem shell com input, fórmulas não interpretadas como código arbitrário.
- Não buscar URLs fornecidas pelo usuário no MVP; se surgir import remoto, usar egress proxy, allowlist, DNS/IP pinning e proteção SSRF.

## 7. Upload direto e object storage

### 7.1 Emissão de URL

Antes de emitir:

- autenticar usuário e verificar permissão/quota;
- criar chave opaca server-side sob prefixo da sessão;
- limitar operação (`PUT`/part específico), bucket, key, content length e TTL;
- condicionar checksum/tipo quando o provedor suportar;
- registrar intenção e correlation ID, sem registrar a URL completa.

TTL deve ser o mínimo operacional. Uma URL só permite o objeto/part específico e não lista bucket. Backend nunca fornece credencial geral do storage ao browser.

### 7.2 Quarantine

- Upload chega a prefix/bucket sem leitura pelo usuário e sem servir conteúdo inline.
- Tipo, tamanho e checksum são verificados após upload.
- Arquivo só é promovido a original após validações.
- Varredura de malware é controle adicional, não substitui parser sandboxed.
- Resposta de download usa attachment e MIME seguro; previews são re-encodados pelo serviço, não HTML/SVG arbitrário do usuário.

### 7.3 Imutabilidade de originals

- Versionamento de objeto e, quando disponível, Object Lock/WORM em modo compatível com retenção.
- Service account de ingestão pode criar, mas não sobrescrever/deletar originals.
- Worker de processamento tem leitura de originals e escrita apenas em staging/derived.
- Deleção exige workflow separado, identidade privilegiada, retenção cumprida e auditoria.
- Banco registra key + version ID + SHA-256 + tamanho; processamento confirma versão/checksum.
- Bucket policy nega operação sem criptografia e transporte seguro.

### 7.4 Criptografia

- Server-side encryption no storage; KMS gerenciado preferível.
- Chaves separáveis por ambiente e, futuramente, por organização de alta exigência.
- Rotação e grants de KMS por workload.
- Backups, snapshots e réplicas também criptografados.

## 8. Arquivos, imagens e ZIPs não confiáveis

Workers de ingestão/preview executam com:

- container não root, filesystem root read-only e diretório temporário dedicado;
- sem Docker socket, sem host mounts e sem credenciais desnecessárias;
- rede de saída negada por padrão;
- seccomp/AppArmor/isolamento equivalente;
- limites de CPU, RAM, disco, processos, descritores e tempo;
- bibliotecas atualizadas e parsers configurados com limites;
- cleanup por attempt e lifecycle independente.

ZIPs: rejeitar traversal, links, dispositivos, paths absolutos, nomes Unicode ambíguos, entradas criptografadas não suportadas, excesso de quantidade/profundidade/tamanho/razão e headers inconsistentes. Calcular limites durante streaming; não confiar apenas no diretório central do ZIP.

Imagens: validar magic bytes, dimensões antes da alocação, canais/dtype, comprimento e decompressed size. Desativar plugins/codecs desnecessários. Arquivo que derruba parser resulta em erro isolado e sinal de segurança.

## 9. Fila, workers e processamento

- Identidades de workload distintas para ingestion, preview, processing e export.
- Mensagens contêm somente IDs opacos, versão, checksum e correlation ID; nunca token/URL assinada permanente.
- Fila criptografada, acesso privado e policies por produtor/consumidor.
- Consumidor trata mensagem como não confiável: valida schema/version, recupera recurso do banco e revalida estado/projeto.
- Idempotência, leases e compare-and-set impedem replay de publicar resultado duplicado/stale.
- DLQ tem acesso restrito, retenção definida e payload minimizado.
- Resultados são escritos em staging e promovidos somente após validação/checksum.
- Fórmulas de índices são implementações/DSL allowlisted e versionadas; jamais `eval` de texto do usuário.
- Jobs não executam código arbitrário nem acessam rede externa.
- Autoscaling e quotas protegem contra negação de serviço/noisy neighbor.

## 10. Banco de dados

- Rede privada, TLS, autenticação forte e contas separadas por API, migration e operação.
- Conta da aplicação sem privilégios de DDL/superuser.
- Backups point-in-time, criptografados e testes de restore.
- Queries parametrizadas; ORM não dispensa review de SQL dinâmico.
- Constraints de projeto e integridade conforme [DATA_MODEL.md](./DATA_MODEL.md).
- PII minimizada e, se necessário, criptografia de campo para atributos de alta sensibilidade.
- Réplicas/analytics recebem apenas dados necessários e mantêm autorização/anonimização.
- Migrations aprovadas, assinadas no pipeline e executadas por identidade separada.

## 11. Segredos e configuração

- Secret manager, nunca `.env` versionado, imagem, manifesto, mensagem ou log.
- Credenciais curtas por workload/managed identity quando possível.
- Rotação automática e procedimento de emergência testado.
- Ambientes dev/staging/prod em contas/projetos cloud separados; dados reais não copiados para dev.
- Configurações de segurança têm default fechado e mudanças auditadas.
- Chaves de assinatura, KMS e credenciais de banco com owners e calendário de rotação.

## 12. Integridade, proveniência e supply chain

- SHA-256 para originals, resultados, exports e manifesto.
- Imagens de container referenciadas por digest; SBOM gerada por release.
- Dependências com lockfile, análise de vulnerabilidade, atualização regular e verificação de licença.
- CI usa runners efêmeros, permissões mínimas, branch protection e revisão obrigatória.
- Artefatos de build assinados/provenance (por exemplo, SLSA progressivo) antes de produção.
- Separação entre quem altera código/índice e quem promove release em ambientes regulados.
- Definições científicas têm versão, review e testes com vetores conhecidos.
- Manifesto pode receber assinatura digital futura; no MVP, checksum e armazenamento imutável.

## 13. Logs, telemetria e auditoria

### Nunca registrar

- senha, cookie, bearer/recovery token;
- secret/KMS material;
- URL pré-assinada completa ou query string sensível;
- bytes/preview de arquivo;
- stack trace no frontend;
- geometria/metadata científica completa sem necessidade;
- e-mail/nome de arquivo cru em métrica de alta cardinalidade.

### Registrar com minimização

- ator/ação/recurso e resultado;
- IDs opacos de projeto/job/item/attempt;
- códigos de erro e métricas de duração/volume;
- IP truncado/hasheado apenas conforme base legal e retenção;
- acesso administrativo, mudança de papel, emissão/download e deleção.

Logs operacionais e audit trail possuem armazenamentos, acesso e retenções diferentes. Auditoria é append-only; alertas detectam tentativa de alteração/desativação.

## 14. Privacidade, retenção e exclusão

- Coletar somente identidade necessária; conteúdo científico não alimenta analytics de produto.
- Política de privacidade explica subprocessadores, região e finalidade.
- Owner define retenção dentro de mínimos/máximos institucionais.
- Originals não expiram automaticamente por simples inatividade no MVP.
- Exports temporários podem expirar; registro/checksum permanece.
- Solicitação de exclusão cria `deletion_pending`, bloqueia novas operações, calcula impacto e respeita legal hold/retention.
- Deleção física percorre derived, exports e originals por IDs exatos; gera tombstones/auditoria e verifica ausência.
- Backups expiram conforme janela documentada; restauração deve reaplicar tombstones pendentes.
- Exportação de dados pessoais e remoção de usuário preservam autoria histórica por pseudônimo quando necessário.

## 15. Disponibilidade e abuso

- WAF/CDN, proteção DDoS do provedor e rate limit em endpoints caros.
- Quotas por projeto: bytes, arquivos, sessões, jobs ativos, items e exports.
- Limites globais e circuit breakers para storage/fila/banco.
- Fair scheduling por projeto e pools de recursos.
- Timeouts em todas as dependências; retries com backoff/jitter e orçamento.
- Upload não reservado indefinidamente: quota é reconciliada contra tamanho observado.
- ZIP/imagem possui limite de expansão/alocação.
- Alertas para spikes de upload, falhas de parser, emissão/download e DLQ.

## 16. Backups, continuidade e resposta a incidentes

### Backups e recuperação

- PostgreSQL com PITR, snapshots periódicos, cópia em conta separada e restore testado.
- Object storage versionado; replicação/backup conforme RPO/RTO aprovado.
- Infra/configuração reproduzível por IaC e imagens por digest.
- Runbook para reconstruir derivados a partir de originals + manifest/version.
- RPO/RTO finais são decisão de produto/negócio; não prometer antes de teste.

### Incidentes

1. Detectar, classificar e preservar evidências.
2. Conter: revogar sessões/URLs, desabilitar identidade/workload, isolar worker/queue.
3. Erradicar e rotacionar segredos/chaves afetados.
4. Recuperar de artefatos confiáveis, verificar checksums e monitorar regressão.
5. Notificar responsáveis/usuários/autoridades conforme obrigação e plano.
6. Produzir postmortem sem culpa e ações verificáveis.

Runbooks específicos: vazamento de URL, acesso cross-project, parser exploit, supply-chain compromise, perda/corrupção de objeto, deleção acidental e exfiltração por logs.

## 17. Verificação de segurança

### Antes do MVP público

- threat modeling revisado e data-flow atualizado;
- testes unitários/integrados de RBAC e isolamento cross-project;
- teste de IDOR em toda rota e emissão de URL;
- fuzz/property tests para paths, ZIP, parsers, geometrias e schemas;
- SAST, secret scan, dependency/container scan no CI;
- DAST/pentest dos fluxos críticos;
- teste de quota/rate limit e arquivos decompression bomb controlados;
- restore de banco/storage e simulação de worker morto/replay;
- confirmação de headers, cookies, CORS, CSP e CSRF;
- revisão de bucket/IAM/KMS por infraestrutura;
- prova de que originals aceitos não podem ser sobrescritos pelas identidades comuns.

### Testes contínuos

- canary de autorização cross-tenant;
- restauração periódica;
- revisão de privilégios e memberships administrativas;
- rotação de segredos;
- atualização de dependências e re-scan;
- amostragem de checksum/integridade;
- tabletop de incidente.

## 18. Matriz de riscos prioritários

| Risco | Probabilidade | Impacto | Controle principal | Residual esperado |
|---|---|---|---|---|
| IDOR/cross-project | média | crítico | escopo obrigatório + FK/RLS + testes | baixo |
| Exploit de parser/ZIP | média | alto | quarantine, sandbox, limites, patches | médio |
| Sobrescrita/deleção de original | baixa | crítico | versioning/lock, IAM deny, checksum | baixo |
| Vazamento de URL assinada | média | alto | TTL/escopo mínimo, TLS, redaction | baixo-médio |
| DoS por lote/ZIP | alta | alto | quotas, fairness, limites e autoscaling | médio |
| Resultado adulterado/stale | baixa | alto | snapshot, digest, CAS, checksum | baixo |
| Segredo em log | média | alto | redaction, testes e secret scanning | baixo-médio |
| Dependência comprometida | baixa-média | crítico | lock, SBOM, scan, assinatura | médio |
| Abuso administrativo | baixa | crítico | JIT, MFA, aprovação e auditoria | baixo-médio |

## 19. Itens fora do MVP que exigem novo threat model

- importação por URL/cloud drive;
- fórmulas customizadas ou execução de notebooks;
- compartilhamento público/link anônimo;
- plugins de terceiros;
- SSO multi-tenant/SCIM;
- multi-região e replicação cross-border;
- API pública/webhooks;
- processamento GPU e dados em ambientes de parceiros.

Nenhum desses recursos deve herdar permissões por suposição; cada um requer modelagem de ameaça e controles próprios.

