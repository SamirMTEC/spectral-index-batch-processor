# Railway: API, workers, PostgreSQL e Redis

Este diretório documenta a implantação no Railway sem armazenar credenciais. O frontend
continua no Cloudflare Pages e os objetos no Cloudflare R2.

## Serviços do projeto

Crie cinco serviços no mesmo environment de produção:

| Serviço             | Origem             | Dockerfile                                | Comando     |
| ------------------- | ------------------ | ----------------------------------------- | ----------- |
| `api`               | repositório GitHub | `infrastructure/docker/api.Dockerfile`    | `serve`     |
| `worker-ingestion`  | repositório GitHub | `infrastructure/docker/worker.Dockerfile` | `ingestion` |
| `worker-processing` | repositório GitHub | `infrastructure/docker/worker.Dockerfile` | `celery`    |
| `Postgres`          | template Railway   | gerenciado                                | gerenciado  |
| `Redis`             | template Railway   | gerenciado                                | gerenciado  |

Use o diretório raiz `/` nos três serviços do repositório. Defina o caminho do
Dockerfile nas configurações de build. A API respeita automaticamente a variável `PORT`
injetada pelo Railway.

## Variáveis compartilhadas

Copie as variáveis não secretas de `.env.example`. Nos três containers, use referências
do Railway para não duplicar credenciais:

```text
SPECTRAL_DATABASE_URL=${{Postgres.DATABASE_URL}}
SPECTRAL_REDIS_URL=${{Redis.REDIS_URL}}
SPECTRAL_ENVIRONMENT=production
SPECTRAL_DEV_AUTH_ENABLED=false
SPECTRAL_CORS_ORIGINS=["https://spectral.classlab.com.br"]
SPECTRAL_AUTH0_DOMAIN=SEU_TENANT.auth0.com
SPECTRAL_AUTH0_AUDIENCE=https://api-spectral.classlab.com.br
SPECTRAL_S3_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
SPECTRAL_S3_PUBLIC_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
SPECTRAL_S3_REGION=auto
SPECTRAL_S3_BUCKET=spectral-index-production
SPECTRAL_S3_FORCE_PATH_STYLE=false
```

Cadastre `SPECTRAL_S3_ACCESS_KEY_ID` e `SPECTRAL_S3_SECRET_ACCESS_KEY` como secrets nos
três serviços. O código aceita a URL PostgreSQL padrão do Railway e a converte para o
driver `asyncpg`; a credencial nunca é regravada nem registrada em log.

## API e migration

- Configure o pre-deploy command da API como `migrate`.
- Configure healthcheck path `/health`.
- Gere um domínio Railway e valide `/ready` antes de associar
  `api-spectral.classlab.com.br` como custom domain.
- Mantenha uma instância API durante o primeiro smoke test. Escale apenas depois de medir
  RAM, conexões e fila.

O migration command usa exatamente a mesma imagem da API. Falha de migration deve impedir
o deploy; não execute downgrade automático.

## Workers

Os workers não recebem domínio público. Ambos usam rede privada para PostgreSQL e Redis:

- `worker-ingestion`: uma réplica, comando `ingestion`;
- `worker-processing`: uma réplica, comando `celery`, concurrency inicial `2`.

O filesystem é efêmero. Originais, resultados e exports devem estar no R2.

## Domínio da API

Depois que o serviço responder pelo domínio Railway:

1. adicione `api-spectral.classlab.com.br` em **Settings → Networking → Custom Domain**;
2. siga o CNAME exibido pelo Railway;
3. deixe o registro com proxy Cloudflare ativo;
4. valide `https://api-spectral.classlab.com.br/health` e `/ready`;
5. só então faça o smoke test autenticado e de upload multipart.

Não aponte o DNS antes de a API estar saudável: isso troca um erro de configuração por
indisponibilidade pública difícil de diagnosticar.
