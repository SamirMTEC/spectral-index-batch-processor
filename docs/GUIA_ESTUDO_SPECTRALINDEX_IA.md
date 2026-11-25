# Guia de estudo do código do SpectralIndex

**Finalidade:** estudar a estrutura e a programação do site, preparar uma apresentação e fornecer contexto a uma IA que fará perguntas ao estudante.

**Base examinada:** repositório `Spectral_automatized_index`, commit `c6a48ac`, em 09/10/2026. Este guia descreve o código observado. Documentos de arquitetura do repositório também contêm propostas futuras; uma proposta não é tratada aqui como recurso já implementado.

## 1 Objetivo do sistema

O SpectralIndex organiza imagens multiespectrais por projeto, dataset e captura. O pesquisador envia arquivos, revisa bandas e regiões de interesse, escolhe índices espectrais, acompanha o processamento e consulta resultados e exportações. A ideia central é transformar imagens de bandas separadas em resultados científicos rastreáveis.

Uma imagem espectral é uma matriz de pixels. Cada banda registra uma faixa do espectro, como vermelho (`RED`) ou infravermelho próximo (`NIR`). Um índice combina bandas por uma fórmula aplicada pixel a pixel. O sistema precisa preservar a relação entre arquivo de origem, banda, fórmula, parâmetros e resultado.

## 2 Arquitetura implementada

```text
Navegador React
    | HTTP e JSON com token Auth0
    v
API FastAPI ---- PostgreSQL (usuários, projetos, datasets, jobs, resultados)
    |       \
    |        Redis (mensagens e tarefas)
    |         |-- Redis Streams -> worker de ingestão
    |         `-- Celery -> worker de cálculo e exportação
    |
    `---- MinIO (originais, prévias, rasters e exports)

Frontend público: Cloudflare Pages
API e storage desta instalação: contêineres locais publicados por Cloudflare Tunnel
```

O frontend é uma aplicação React com TypeScript e Vite. Ele cuida da navegação, formulários, estados de carregamento, upload e apresentação dos resultados. A API usa FastAPI e Python. Ela autentica pedidos, verifica a propriedade dos recursos, valida entradas, registra estados e despacha tarefas. O PostgreSQL é o registro persistente dos dados estruturados. O MinIO guarda arquivos binários grandes. O Redis transporta trabalho assíncrono.

Dois processos de worker têm papéis diferentes. O serviço `worker` consome Redis Streams para validar e ingerir arquivos. O serviço `processing-worker` executa tarefas Celery para cálculos científicos e exportações. Ambos aparecem em `docker-compose.yml`.

**Distinção de projeto:** `docs/ARCHITECTURE.md` descreve também uma arquitetura desejada, com itens como outbox transacional e serviços adicionais. A criação de jobs observada em `apps/api/src/spectral_api/routes/processing.py` grava o job e então envia tarefas diretamente ao Celery. Na apresentação, descreva o comportamento do código atual, sem atribuir a ele todo o plano do documento de arquitetura.

## 3 Organização do repositório

- `apps/web`: interface React, componentes, estilos, autenticação e cliente HTTP.
- `apps/api`: endpoints FastAPI, regras de acesso, modelos do banco e esquemas de entrada e saída.
- `apps/worker`: ingestão, processamento, geração de arquivos e consumidores de fila.
- `packages/science`: motor independente que calcula índices e estatísticas sobre matrizes.
- `packages/python`: configuração, logging e catálogo compartilhado de fórmulas.
- `tests`: testes do motor, API, workers e componentes.
- `docs`: especificações e documentação. Algumas seções descrevem direção futura.
- `infrastructure` e `docker-compose*.yml`: imagens, contêineres e publicação.

**Ordem recomendada de leitura:** `apps/web/src/main.tsx` -> `apps/web/src/app.tsx` -> `apps/web/src/lib/api.ts` -> `apps/api/src/spectral_api/main.py` -> uma rota em `apps/api/src/spectral_api/routes` -> `apps/api/src/spectral_api/domain/models.py` -> worker correspondente -> `packages/science/src/spectral_engine/engine.py`.

## 4 Fluxo completo de uma análise

### 4.1 Entrar e criar um projeto

`main.tsx` monta a aplicação dentro de `ApplicationAuthProvider`. Em produção, `apps/web/src/lib/auth.tsx` integra o Auth0 e disponibiliza um token de acesso para `apps/web/src/lib/api.ts`. O cliente `request` envia o token no cabeçalho `Authorization: Bearer ...`. Na API, `get_current_user` em `dependencies.py` valida o token e identifica o usuário. O modo de identidade de desenvolvimento existe no código, mas a configuração pública o desativa.

Na tela de projetos, o frontend chama `api.createProject`. A rota `POST /api/v1/projects` cria o registro no PostgreSQL. Consultas subsequentes filtram os recursos pelo usuário. Isso significa que esconder um botão na interface não é a proteção principal: a API confere a propriedade do recurso.

### 4.2 Criar dataset

O componente `DatasetForm` em `apps/web/src/app.tsx` guarda os campos digitados em estado React. A função `save` chama `api.createDataset(projectId, form)`. O cliente HTTP faz `POST /api/v1/projects/{project_id}/datasets`. Em `routes/projects.py`, a API verifica se o projeto pertence ao usuário e se não está arquivado; depois cria um `Dataset` no banco. O retorno contém o identificador usado para navegar à nova tela.

**Conceitos de programação:** estado React (`useState`), evento de formulário (`onSubmit`), função assíncrona (`async/await`), requisição HTTP `POST`, validação no servidor e persistência ORM com SQLAlchemy.

### 4.3 Enviar e validar imagens

O `UploadPanel` seleciona arquivos e pede à API uma sessão de upload. A rota em `routes/uploads.py` verifica limites, caminhos duplicados, estado do dataset e quota. Ela fornece planos e URLs temporárias. O navegador envia os bytes diretamente ao MinIO; a API não precisa receber o arquivo inteiro em uma requisição JSON. Arquivos grandes podem ser enviados em partes, com ETags usadas na conclusão.

Depois da conclusão, uma mensagem de validação é publicada no Redis Streams. `QueueWorker` lê a mensagem; `IngestionHandler` verifica o objeto, processa imagem ou ZIP, registra o asset e pode criar prévia. O arquivo enviado não se torna automaticamente uma banda válida só porque o upload terminou. A validação é uma etapa separada.

### 4.4 Organizar bandas e ROI

O modelo distingue `Treatment`, `Replicate`, `Capture` e `BandAsset`. Uma captura agrupa bandas que serão combinadas pelo motor científico. A interface consulta capturas e permite criar uma ROI, isto é, uma região de interesse da imagem. As rotas em `routes/rois.py` validam a geometria e criam versões. Uma nova versão não deve alterar silenciosamente o significado de um processamento antigo.

### 4.5 Criar o processamento

Na tela `ProcessingPage`, o usuário seleciona índices e escopos de análise. A API `POST /api/v1/datasets/{dataset_id}/processing-jobs` confirma a propriedade do dataset, evita duplicação por chave de idempotência ou pedido equivalente em andamento, localiza as capturas e monta um snapshot de reprodutibilidade.

O job recebe itens para cada combinação de captura, índice selecionado e escopo. Os escopos existentes no código são `roi` e `full_image`. Se a ROI é necessária e não existe, esse item pode ser marcado como ignorado, enquanto os demais seguem. O job e os itens são salvos no PostgreSQL; então os itens pendentes são enviados ao Celery. A API responde com o estado do job, sem esperar todos os cálculos terminarem.

### 4.6 Calcular e consultar resultados

Em `apps/worker/src/spectral_worker/tasks.py`, a tarefa Celery chama `ProcessingItemProcessor`. Esse processador reivindica o item, lê as bandas, calcula em janelas quando necessário, produz o raster e registra resultados e estatísticas. Há tratamento de falhas, tentativas e cancelamento. O motor em `packages/science/src/spectral_engine/engine.py` faz a matemática; ele não depende da interface React.

A tela `ResultsPage` consulta `GET /api/v1/processing-jobs/{job_id}/results`. Ela apresenta visão geral, tabela, imagens, erros, arquivos e informações de reprodutibilidade. A exportação é pedida por outra rota e gerada em segundo plano. A API retorna links de download temporários após autorizar o acesso.

## 5 Exemplo do motor científico

O NDVI usa vermelho e infravermelho próximo:

```text
NDVI = (NIR - RED) / (NIR + RED)
```

Com `NIR = 0,6` e `RED = 0,2`, o resultado é `(0,6 - 0,2) / (0,6 + 0,2) = 0,5`. Na prática, a operação é aplicada a cada posição de duas matrizes alinhadas. O catálogo `packages/python/src/spectral_common/science_catalog.py` informa fórmula, versão, bandas exigidas e parâmetros permitidos. `calculate_index` valida bandas ausentes, dimensões e metadados espaciais. Pixels marcados como `nodata`, não finitos ou com divisão por zero não entram nas estatísticas. O resultado inclui uma máscara de validade e valores `float32`.

**Como explicar o modo radiométrico:** `digital_number` indica valor digital bruto; `reflectance` indica reflectância; `unknown` significa que a informação não foi definida. Não diga que o programa converte automaticamente qualquer DN em reflectância. O código aceita parâmetros como `reflectance_scale` em fórmulas específicas; o significado científico da entrada continua importante.

## 6 Modelo de dados e estados

`Project` agrupa o trabalho do usuário. `Dataset` descreve uma coleta. `UploadSession` e `UploadObject` acompanham o envio. `BandAsset` representa um arquivo de banda aceito. `Capture` agrupa bandas relacionadas. `ROI` e `ROIVersion` registram área e revisão. `ProcessingJob` representa um pedido de análise; `ProcessingItem` é uma unidade menor de execução. `IndexResult` e `Statistics` guardam metadados do resultado; `ExportArtifact` acompanha arquivos gerados.

Não confunda banco e storage. O banco contém identificadores, estados, relações, parâmetros, checksums e referências. MinIO contém os bytes das imagens e dos resultados. Essa separação ajuda a manter consultas rápidas e evita guardar rasters grandes como linhas do PostgreSQL.

Um job pode estar na fila, em execução, concluído, concluído com erros, falhar ou ser cancelado. Um item pode ter estado próprio. Isso permite que um item falhe sem necessariamente apagar o trabalho dos demais. `ProcessingPage` consulta periodicamente o estado, em vez de manter uma única requisição aberta até o fim.

## 7 Conceitos de programação que você deve saber explicar

1. **Monorepo:** um repositório contém frontend, API, workers, bibliotecas compartilhadas e infraestrutura.
2. **Componente React:** função que retorna interface a partir de estado e propriedades; por exemplo `DatasetForm`.
3. **TypeScript:** acrescenta tipos ao JavaScript; `DatasetInput` define o formato esperado pela interface.
4. **HTTP e JSON:** contrato entre navegador e API. `GET` lê; `POST` cria ou inicia uma ação; `PATCH` altera parcialmente; `DELETE` remove ou arquiva conforme a rota.
5. **FastAPI e Pydantic:** rotas recebem dados validados por esquemas e retornam respostas tipadas.
6. **SQLAlchemy ORM:** classes Python representam tabelas e relações do PostgreSQL.
7. **`async/await`:** permite esperar operações de rede e banco sem escrever lógica de callback em cadeia. Não significa que um cálculo pesado deve ocorrer dentro da requisição da API.
8. **Fila e worker:** a API registra o pedido; outro processo executa o trabalho demorado.
9. **Idempotência:** repetir o mesmo pedido com a mesma chave não deve criar processamento duplicado.
10. **Snapshot:** cópia dos parâmetros, versões e referências que explica exatamente o que um job usou.
11. **Máscara de pixels válidos:** separa valores aproveitáveis de `nodata`, `NaN` e divisões inválidas.
12. **Teste automatizado:** `tests/science/test_engine.py`, `tests/api/test_processing.py` e `tests/worker/test_processing.py` exercitam as camadas separadamente.

## 8 Segurança e implantação

Em produção, a interface usa Auth0. A API valida o token e filtra recursos por `owner_id`. URLs pré-assinadas permitem upload e download por tempo limitado. A API configura CORS para a origem pública do frontend. Existem limites de tamanho, número de arquivos e duração de tarefas.

O frontend desta instalação está em `https://spectral.classlab.com.br`. A API, PostgreSQL, Redis, MinIO e workers executam em Docker nesta máquina. O Cloudflare Tunnel publica a API e o storage. O arquivo `docker-compose.public-local.yml` aplica a configuração pública. Se a máquina, o Docker ou o túnel ficam indisponíveis, o frontend pode carregar mas chamadas `fetch` falham. `/health` indica que o processo da API responde; `/ready` também verifica dependências. Isso explica os erros observados nas telas anteriormente.

**Limite importante para a apresentação:** esta instalação depende da disponibilidade do computador local. Não a descreva como uma infraestrutura inteiramente hospedada na nuvem ou com alta disponibilidade. Também não afirme que todos os mecanismos previstos em `docs/ARCHITECTURE.md` estão implementados.

## 9 Roteiro de apresentação de cinco minutos

**Abertura, 40 segundos:** “O SpectralIndex organiza imagens espectrais em lotes e transforma bandas em índices e estatísticas rastreáveis. O usuário trabalha com projetos, datasets e capturas.”

**Arquitetura, 70 segundos:** “A interface React roda no navegador e chama uma API FastAPI. A API valida usuários e operações. PostgreSQL guarda estados e relações; MinIO guarda arquivos grandes. Redis conecta a API aos workers.”

**Fluxo, 100 segundos:** “Ao criar um dataset, a API salva seus metadados. No upload, o navegador envia arquivos ao storage usando URLs temporárias. O worker de ingestão valida os arquivos. Depois o usuário seleciona índices e, se quiser, uma ROI. A API cria um job com snapshot e o worker científico calcula os itens em segundo plano.”

**Ciência, 50 segundos:** “O NDVI, por exemplo, usa NIR e RED. O motor confere bandas e alinhamento, exclui pixels inválidos, calcula a fórmula por pixel e produz estatísticas.”

**Fechamento, 40 segundos:** “Os resultados ficam associados às entradas e versões usadas. Isso ajuda a explicar e reproduzir a análise. A instalação pública atual tem frontend na Cloudflare e backend nesta máquina, conectado por túnel.”

## 10 Perguntas para praticar

### Nível básico

1. Qual problema o SpectralIndex resolve?
2. O que são projeto, dataset, captura e banda?
3. Quais linguagens e frameworks aparecem no frontend e na API?
4. Por que PostgreSQL e MinIO têm papéis diferentes?
5. O que faz o arquivo `apps/web/src/lib/api.ts`?
6. O que acontece quando se clica em “Criar dataset”?
7. Qual é a diferença entre `/health` e `/ready`?
8. Por que a interface pode abrir enquanto o Dashboard mostra “Failed to fetch”?

### Nível intermediário

9. Por que o navegador envia os bytes do upload diretamente ao storage?
10. Qual é a diferença entre o worker de ingestão e o worker Celery?
11. O que é uma URL pré-assinada e por que ela expira?
12. Por que o upload concluído ainda precisa de validação?
13. O que é um `ProcessingJob`? O que é um `ProcessingItem`?
14. Como um job com várias capturas e índices vira várias unidades de trabalho?
15. O que acontece quando o usuário escolhe ROI e uma captura não tem ROI ativa?
16. Por que o processamento não ocorre dentro da requisição HTTP?
17. Como o frontend acompanha o progresso do job?
18. Como a API impede que um usuário leia o dataset de outro?

### Nível avançado

19. Para que servem chave de idempotência e hash do pedido?
20. O que o snapshot de reprodutibilidade preserva?
21. Por que versões de fórmula e ROI importam para a interpretação de um resultado antigo?
22. Como `calculate_index` lida com banda ausente, `nodata` e divisão por zero?
23. Calcule o NDVI para NIR 0,6 e RED 0,2 e explique o resultado numérico.
24. Por que uma máscara de validade acompanha o raster resultante?
25. O que distingue o estado de um item do estado do job inteiro?
26. Qual é o efeito de uma falha de Redis no envio de tarefas Celery?
27. Quais controles básicos reduzem riscos ao receber um ZIP de usuário?
28. Que evidências do código mostram que o serviço separa upload, validação e cálculo?
29. Qual parte de `docs/ARCHITECTURE.md` você evitaria afirmar como implementada sem conferir o código?
30. Se o site carrega mas a API pública responde 1033, por onde começaria o diagnóstico?

### Critérios de correção para a IA tutora

- Uma resposta correta identifica o componente responsável e explica a sequência causal, não apenas lista nomes de ferramentas.
- Em perguntas sobre ciência, a resposta deve mencionar bandas, fórmula, alinhamento e pixels inválidos quando pertinentes.
- Em perguntas sobre segurança, deve citar validação no servidor e escopo por usuário; esconder conteúdo na interface é insuficiente.
- Em perguntas sobre implantação, deve distinguir frontend público de backend local e reconhecer a dependência do túnel.
- Aceite sinônimos tecnicamente corretos. Aponte imprecisões concretas e peça ao estudante para refazer apenas a parte incorreta.

## 11 Prompt para usar com outra IA

Copie o texto abaixo depois de anexar este documento à IA:

```text
Você é meu tutor de programação para uma apresentação do SpectralIndex. Use o documento anexado como base principal e diferencie fatos do código atual de propostas de arquitetura. Faça uma pergunta por vez, começando no nível básico e aumentando a dificuldade conforme minhas respostas. Espere minha resposta antes de continuar. Depois de cada resposta, dê uma nota de 0 a 2: 0 incorreta, 1 parcialmente correta, 2 correta. Explique em até três frases o que acertei e o que faltou; cite o arquivo ou seção do documento relevante. Se eu errar, faça uma pergunta de acompanhamento sobre o mesmo conceito antes de mudar de assunto. Alterne perguntas sobre frontend, API, banco, upload, workers, ciência, segurança e deploy. Inclua perguntas em que eu precise seguir uma ação do usuário através de vários arquivos. Não revele a resposta antes de eu tentar. Depois de dez perguntas, resuma meus pontos fortes, meus pontos fracos e proponha três tópicos para revisar. Não invente detalhes ausentes do documento; quando houver dúvida, diga o que precisaria conferir no código.
```

## 12 Mapa de fontes do código

- Entrada React e autenticação: `apps/web/src/main.tsx`, `apps/web/src/lib/auth.tsx`.
- Formulários e navegação: `apps/web/src/app.tsx`; cliente HTTP: `apps/web/src/lib/api.ts`.
- Upload e telas de processamento: `apps/web/src/components/upload-panel.tsx`, `apps/web/src/components/processing-page.tsx`, `apps/web/src/components/results-page.tsx`.
- Montagem da API, autenticação e dependências: `apps/api/src/spectral_api/main.py`, `auth.py`, `dependencies.py`.
- Projetos, datasets, uploads, ROI, jobs, resultados e exportações: arquivos correspondentes em `apps/api/src/spectral_api/routes/`.
- Modelo persistente: `apps/api/src/spectral_api/domain/models.py`; migrations: `apps/api/migrations/`.
- Ingestão e fila: `apps/worker/src/spectral_worker/queue.py`, `ingestion.py`.
- Celery, cálculo e exportações: `apps/worker/src/spectral_worker/celery_app.py`, `tasks.py`, `processing.py`, `exporting.py`.
- Fórmulas e matemática: `packages/python/src/spectral_common/science_catalog.py`, `packages/science/src/spectral_engine/engine.py`.
- Deploy desta instalação: `docker-compose.yml`, `docker-compose.public-local.yml`, `infrastructure/cloudflare/LOCAL_BACKEND.md`.
- Testes representativos: `tests/science/test_engine.py`, `tests/api/test_processing.py`, `tests/worker/test_processing.py`.
