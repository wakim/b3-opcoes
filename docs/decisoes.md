# Decisões

Log das decisões tomadas **sem o dono no circuito**. Existe por um motivo só: autonomia não pode virar memória invisível. Toda escolha que a spec não cobre entra aqui, com o padrão adotado, o motivo e **quando** ela precisa ser revista.

Regra: **decisão registrada não bloqueia a execução.** O padrão vale a partir do momento em que é escrito; a revisão é periódica, não um portão. Só o que está marcado como **🔒 irreversível** espera o dono.

---

## Como registrar

```md
## ADR-0NN — título curto no imperativo
- **Data:** AAAA-MM-DD · **Status:** provisória | ratificada | revista
- **Custo de reverter:** barato | médio | caro
- **Contexto:** o que forçou a decisão (1–3 linhas).
- **Decisão:** o padrão adotado, concreto (nome de arquivo, valor, biblioteca).
- **Por quê:** o critério usado — sempre "menor escopo v1" e "mais reversível" empatam primeiro.
- **Consequência:** o que passa a ser verdade, inclusive o que fica pior.
- **Revisar em:** F1b | F3 | F5 | depois de 30 dias de uso | nunca
```

Critérios de desempate, nesta ordem:

1. A spec vence. Se a spec não diz, o `PLAN.md` vence. Se nenhum dos dois diz, menor escopo.
2. Mais reversível vence o mais elegante.
3. Dado real vence suposição — mas **medir** custa tempo: se a medição levar mais de 30 min, adote o default e registre a dúvida.
4. Nada entra que exija dinheiro, conta, credencial ou decisão jurídica. Isso **para** e vira pergunta.

---

## ADR-001 — Motor de containers: Podman rodando compose padrão

- **Data:** 2026-11-22 · **Status:** ratificada pelo dono · **Custo de reverter:** baixo
- **Contexto:** o projeto passou a exigir stack 100% em container. O host Windows **não tem `docker` no PATH**; tem Podman 6.1.4 com `podman compose` funcional (provider externo `docker-compose.exe` v5.8) e bind mount `C:\` ↔ container verificado nos dois sentidos.
- **Decisão:** o artefato versionado é `compose.yaml` **padrão** (nada exclusivo de Podman). O runner documentado aqui é `podman compose`; em qualquer outra máquina, `docker compose` roda o mesmo arquivo sem edição.
- **Por quê:** instalar Docker Desktop exigiria licença/admin e duplicaria motor já funcionando. O compose padrão mantém o projeto portátil.
- **Consequência:** os documentos falam `podman compose`. Nunca usar `podman-compose` (o pacote Python) nem recurso que o compose padrão não tenha. `podman compose` é mais lento no primeiro pull.
- **Revisar em:** se o host ganhar Docker; ou se algum recurso do compose não funcionar no provider.

## ADR-002 — Imagens e estágios

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** baixo
- **Contexto:** precisa de uma imagem para rodar a app e uma para rodar teste/lint, sem levar `pytest` para produção; e um estágio Node para o `web/dist`.
- **Decisão:** `python:3.12-slim` como base; `uv` copiado de `ghcr.io/astral-sh/uv`; estágios `deps` → `runtime` (sem dev deps) e `dev` (com dev deps); estágio `web` com `node:22-slim` gerando `dist`, que o estágio final copia para `/app/web/dist`. `uv sync --frozen`.
- **Por quê:** imagem final pequena, um único `uv.lock` manda nas duas, e o build do frontend não exige Node no host.
- **Consequência:** `uv.lock` é versionado (removido do `.gitignore`). `podman compose run --rm app pytest` **falha de propósito** — teste é no serviço `test`.
- **Revisar em:** F5.0 (quando o `web/` existir).

## ADR-003 — Volumes: um bind (dado) e volumes nomeados (ambiente)

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** baixo
- **Contexto:** no bind mount do Windows os arquivos aparecem como `nobody:nobody`, modo `666`, e a vigilância de arquivo é lenta.
- **Decisão:** `./data:/data` é o único bind de dado. `.venv`, `node_modules` e caches de ferramenta ficam em volumes nomeados. Código (`src/`, `tests/`, `config.yaml`, `web/`) pode ser bind no perfil `dev`.
- **Por quê:** dado precisa sobreviver a `compose down` e ser visível no host; ambiente não precisa (e sofre no bind).
- **Consequência:** `data/` continua no host e no `.gitignore`; `data/.gitkeep` mantém a pasta. Trocar `pyproject.toml` exige `podman compose build`, não só restart.
- **Revisar em:** F5.

## ADR-004 — Dependências do backend

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** médio
- **Decisão:** `httpx` (download condicional, timeout, retry), `typer` (CLI), `pydantic` v2 (contratos), `numpy` + `pyarrow` + `duckdb` (cálculo e lake), `pyyaml` (config), `mcp` SDK para o Streamable HTTP (F3). Dev: `pytest`, `ruff`. **Sem** `pandas`, **sem** `scipy` (Brent é implementável em ~20 linhas e o teste cobre), **sem** `requests`.
- **Por quê:** `pandas` duplica o que DuckDB + numpy já fazem e esconde o custo de memória; `scipy` é ~30 MB para uma função.
- **Consequência:** qualquer biblioteca nova precisa de justificativa curta aqui antes de entrar.
- **Revisar em:** F1 (se o Brent/numpy apertar) e F3 (SDK `mcp`).

## ADR-005 — Nomenclatura de camadas e módulos

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** médio
- **Decisão:** `src/b3o/{ingest,derive,store,query,api,cli}/`; fórmulas puras em `derive/` (recebem número, devolvem número); I/O em `ingest/` e `store/`; **toda** leitura de dado passa por `query/service.py::QueryService`.
- **Por quê:** a regra "uma camada de consulta" só sobrevive se houver um lugar óbvio onde ela mora.
- **Consequência:** um teste de arquitetura (`test_sem_sql_fora_do_service`) varre `src/` procurando `duckdb.connect`/`execute(` fora de `store/` e `query/service.py` e falha se achar. Isso entra no F0.1b, antes de existir SQL — barato agora, caro depois.
- **Revisar em:** F3.

## ADR-008 — Dependências sempre na última versão estável

- **Data:** 2026-11-22 · **Status:** ratificada pelo dono (diretiva na sessão) · **Custo de reverter:** baixo
- **Contexto:** o dono definiu como pré-requisito permanente do projeto: bibliotecas e imagens de base sempre atualizadas ("sempre ter as bibliotecas atualizadas").
- **Decisão:** a cada sessão que mexa em dependências (ou ao abrir fase nova), auditar `pyproject.toml` + `uv.lock` + imagens do `Dockerfile` contra as últimas versões estáveis; subir floors e regerar `uv.lock` (workaround do container — AGENTS.md); rebuild + `pytest`/`ruff` verdes antes de commit. Se uma versão nova quebrar, voltar a floor da culpada e registrar aqui.
- **Por quê:** diretiva do dono; manutenção contínua evita dívida de upgrade gigante no fim.
- **Consequência:** `uv.lock` muda com frequência (diff grande e semântico — sempre commit separado `chore(deps): …`); Python permanece 3.12 e Node 22 (convenção/spec — upgrade de linguagem NÃO é "atualização de biblioteca" e é decisão de spec); pacotes que não sobem sem quebrar ficam registrados aqui com o motivo.
- **Revisar em:** a cada sessão que tocar dependências.

## ADR-007 — Validador: fatal é estrutural; linha torta se pula e se conta

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** baixo
- **Contexto:** o contrato da Onda A definia `ok = … and not bad_width and not bad_numeric`, mas o COTAHIST 2026 real tem exatamente **1 linha de 246 bytes** (linha 2.087.780; `FR0T5L`, mercado 017: `corp_name` em UTF-8 — `Â` — dentro do arquivo latin-1; todos os offsets posteriores deslocados). Com o `ok` estrito, o `prepare` reprovaria para sempre num arquivo perfeitamente utilizável.
- **Decisão:** `ok` = só condições estruturais (header, contagem do trailer, zip ilegível). Problema por linha (largura/numérico/data/tipreg) é **registrado, pulado na ingestão e contado**; contagens expostas no `health`. A linha de mercado não-ingerido (017) nem chega ao lake por causa do filtro de mercados. `errors` fica reservado a I/O/zip.
- **Por quê:** spec §11 ("rejeitar linha torta contando, não silenciando") + P-E; o critério "nenhuma linha de largura errada passa" refere-se a **entrar no lake**, não a abortar o arquivo.
- **Consequência:** o `prepare` não morre por linha torta; em contrapartida, o `health` precisa mostrar as contagens de linhas puladas (senão vira zero silencioso disfarçado).
- **Revisar em:** fim do F0 (auditoria de dado).

## ADR-006 — Backfill começa por 2026

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** barato
- **Contexto:** a spec pede backfill de 2024–2026 (A5) e o plano exige validação no dado real de 2026.
- **Decisão:** primeiro 2026 sozinho, ponta a ponta, até os critérios do F0 passarem; só então baixar 2024 e 2025, em ordem decrescente. 2026 é reconstruído inteiro a cada `prepare`.
- **Por quê:** 2024/2025 são arquivos fechados e imutáveis — não ensinam nada sobre o parser que 2026 não ensine, e triplicam o tempo de cada iteração.
- **Consequência:** o `health` mostra as três partições separadamente; "faltando 2024" não é erro.
- **Revisar em:** F1b (o backtest precisa das três).

---

## Decisões pré-autorizadas (defaults de fábrica)

Já valem sem nova entrada no log. Se a execução precisar de uma delas, apenas **use** — e cite o número na mensagem de commit ou no relatório.

| # | Situação | Default |
|---|---|---|
| P-A | Onde mora o dado derivado | `data/lake/<tabela>/year=YYYY/*.parquet`; `model=vN` só depois do F1 |
| P-B | Formato do `config.yaml` | chaves planas, nomes iguais aos campos do código; segredo nunca no YAML (vai em `.env`) |
| P-C | Data de referência | a **última** do arquivo; nunca `date.today()` |
| P-D | Fuso | `America/Sao_Paulo`; datas internas são `date`, borda ISO com offset |
| P-E | Erro que não é bug | fonte indisponível → `null` + flag + entrada no `health`, código `source_unavailable`, nunca exceção que mata o `prepare` |
| P-F | Log | `logging` padrão em JSON uma linha por evento, nível por `LOG_LEVEL`; sem `structlog` |
| P-G | CLI | `typer`; todo comando aceita `--date` e imprime resumo de uma tela; exit ≠ 0 em falha |
| P-H | Teste | `pytest` sem plugin exótico; fixture com recorte real do COTAHIST (10 linhas, arquivo pequeno versionado) além do arquivo grande |
| P-I | Parser | streaming linha a linha, `latin-1`, largura 245, `int64` em centavos; rejeitar linha torta contando, não silenciando |
| P-J | Validação externa falhou (brapi fora, rate limit) | registrar em `decisoes.md` e seguir com a validação interna (paridade call E × put E), que é a principal |
| P-K | Universo < 8 papéis | aceitar o produto como painel de poucos papéis (risco já previsto no plano); **não** afrouxar o corte de liquidez |
| P-L | Sinal do F1b fraco | parar no portão, escrever `out/backtest.md` e a leitura em `decisoes.md`; **não** otimizar limiar no mesmo dado |
| P-M | Curva DI indisponível | fallback em cascata, `rate_source` gravado e exibido; não bloquear |
| P-N | OI indisponível | `oi = null` + `oi_unavailable`; `days_to_liquidate = null` |
| P-O | Dependência nova | só se a alternativa for > 50 linhas de código próprio; registrar ADR curto |
| P-R | Nome de campo | inglês com unidade (`iv_pct`); nunca abreviação obscura |
| P-S | Commit | um commit por pacote de trabalho, mensagem `tipo(escopo): resultado`; sem push (não há remote) |
| P-T | Arquivo de saída | `out/` (ignorado pelo git), sempre Markdown com data no nome quando for relatório (`out/backtest-2026-11-22.md`) |

## Quando **parar** e perguntar

Só estas quatro coisas interrompem a execução autônoma:

1. algo exige **dinheiro, conta, credencial ou contrato** (ex.: brapi Pro, certificado, domínio);
2. o critério de uma fase **falha e a correção muda a spec** (fórmula, convenção de tempo, schema publicado);
3. o dado não existe na fonte depois de todos os fallbacks previstos (ex.: OI **e** curva DI indisponíveis no mesmo dia);
4. decisão que apaga ou reescreve trabalho do dono (docs de planejamento, direção visual, mockups).

Em qualquer outro caso: adote o default, registre, siga.

## Pauta de revisão

| Quando | O que revisar |
|---|---|
| Fim do F0 | ADR-002/003/005/006 + relatório de liquidez (define o universo real) |
| Fim do F1b | P-L e o resultado do portão: o sinal segue? |
| Fim do F3 | ADR-004 (SDK `mcp`), ADR-005 (camadas), formato das respostas |
| Antes do F5.0 | ADR-003 (volumes do frontend) e a direção visual, se a primeira tela pedir ajuste |
| 30 dias de uso | todas as provisórias que sobrarem |
