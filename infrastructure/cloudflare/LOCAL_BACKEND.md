# Backend local com frontend no Cloudflare Pages

## Implantação desta máquina (configurada em 08/09/2026)

O túnel `spectral-index-local` usa o identificador público
`17665fa3-4d92-4763-a289-077c38e438e6`. O conector roda no Docker, com reinício
automático, usando `tunnel.docker.yml`: API em `http://api:8000` e armazenamento
em `http://minio:9000`. As credenciais ficam fora do repositório, em
`%USERPROFILE%\.cloudflared\17665fa3-4d92-4763-a289-077c38e438e6.json`.

Para iniciar ou atualizar o modo público, na raiz do projeto:

```powershell
docker compose -f docker-compose.yml -f docker-compose.public-local.yml up -d
docker compose -f docker-compose.yml -f docker-compose.public-local.yml ps
docker compose -f docker-compose.yml -f docker-compose.public-local.yml logs --tail 30 tunnel
```

O override ativa Auth0, desativa identidade de desenvolvimento e configura CORS e
as URLs pré-assinadas HTTPS. Todos os serviços, inclusive migrations, recebem a
configuração de produção. Os volumes locais existentes são preservados. Contas
Auth0 têm ownership próprio; projetos da identidade de desenvolvimento não são
transferidos automaticamente para elas.

Nesta máquina, `.env` também define `COMPOSE_PATH_SEPARATOR=;` e
`COMPOSE_FILE=docker-compose.yml;docker-compose.public-local.yml`. Assim,
`docker compose up -d` mantém o modo público seguro por padrão.

Para interromper somente o acesso externo:

```powershell
docker compose -f docker-compose.yml -f docker-compose.public-local.yml stop tunnel
```

Para retornar ao desenvolvimento, **pare primeiro o túnel** com o comando acima
e depois execute `docker compose -f docker-compose.yml up -d`. Para tornar o modo
de desenvolvimento o padrão novamente, remova as duas variáveis `COMPOSE_*`
acima do `.env`. Não execute a configuração de
desenvolvimento enquanto o túnel estiver publicando a API.

Para parar toda a stack sem apagar dados:

```powershell
docker compose -f docker-compose.yml -f docker-compose.public-local.yml stop
```

O Docker Desktop precisa iniciar com o Windows; a política `unless-stopped`
reinicia os containers que estavam ativos. Containers parados manualmente
precisam do comando `up -d`. Suspender ou desligar a máquina interrompe o backend.

O frontend deve permitir `https://storage-spectral.classlab.com.br` em
`connect-src` e `img-src`, e o domínio Auth0 em `connect-src` e `frame-src`.
Essas regras estão em `apps/web/public/_headers` e exigem um novo deploy Pages.

## Configuração manual de outra instalação

Este modo mantém na sua máquina a API, os workers, PostgreSQL, Redis e MinIO. O frontend
continua no Cloudflare Pages. Um Cloudflare Tunnel nomeado publica somente dois serviços:

| Hostname                           | Serviço local                       |
| ---------------------------------- | ----------------------------------- |
| `api-spectral.classlab.com.br`     | `http://host.docker.internal:57705` |
| `storage-spectral.classlab.com.br` | `http://host.docker.internal:57703` |

PostgreSQL, Redis e o console do MinIO não devem ser publicados.

## Configuração

1. Crie um Tunnel nomeado no Cloudflare Zero Trust e adicione as duas rotas acima.
2. Use `local-tunnel-config.example.yml` como referência para a configuração ingress.
3. Instale o `cloudflared` ou execute-o por Docker.
4. Guarde o token somente em variável de ambiente local:

```powershell
$env:CLOUDFLARE_TUNNEL_TOKEN = "TOKEN_FORNECIDO_PELO_CLOUDFLARE"
docker run --rm --name spectral-cloudflared `
  --add-host host.docker.internal:host-gateway `
  -e TUNNEL_TOKEN=$env:CLOUDFLARE_TUNNEL_TOKEN `
  cloudflare/cloudflared:latest tunnel run
```

Não coloque o token em `.env.example`, no Git ou em uma imagem Docker.

## Variáveis do backend local público

No `.env` local, mantenha os segredos locais e altere somente as variáveis de integração:

```text
SPECTRAL_ENVIRONMENT=production
SPECTRAL_DEV_AUTH_ENABLED=false
SPECTRAL_AUTH0_DOMAIN=spectralindex-classlab.us.auth0.com
SPECTRAL_AUTH0_AUDIENCE=https://api-spectral.classlab.com.br
SPECTRAL_CORS_ORIGINS=["https://spectral.classlab.com.br"]
SPECTRAL_S3_PUBLIC_ENDPOINT_URL=https://storage-spectral.classlab.com.br
MINIO_API_CORS_ALLOW_ORIGIN=https://spectral.classlab.com.br
```

O endpoint interno continua `http://minio:9000`; somente as URLs pré-assinadas usam o
hostname público do storage. O frontend deve ser publicado com:

```text
VITE_API_BASE_URL=https://api-spectral.classlab.com.br
```

O multipart permanece em partes de 8 MiB, evitando depender de uma única requisição do
tamanho total do TIFF.

## Operação

Inicie a stack antes do tunnel:

```powershell
docker compose up --build -d
docker compose ps
```

Valide a API pela Internet:

```powershell
Invoke-WebRequest https://api-spectral.classlab.com.br/health
Invoke-WebRequest https://api-spectral.classlab.com.br/ready
```

O computador precisa permanecer ligado, conectado e com o Docker em execução. Faça
backup do volume `minio_data` para outro disco ou serviço externo. Esse modo é adequado
para desenvolvimento, demonstração e piloto; não oferece a disponibilidade, redundância
ou backup gerenciado de um object storage dedicado.
