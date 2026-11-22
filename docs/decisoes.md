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
- **Decisão:** `python:3.15-slim` como base; `uv` copiado de `ghcr.io/astral-sh/uv`; estágios `deps` → `runtime` (sem dev deps) e `dev` (com dev deps); estágio `web` com `node:26-slim` gerando `dist`, que o estágio final copia para `/app/web/dist`. `uv sync --frozen`.
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
- **Consequência:** `uv.lock` muda com frequência (diff grande e semântico — sempre commit separado `chore(deps): …`); Python sobe para 3.15 (duckdb tem wheel abi3 cp312+, numpy/pyarrow/pydantic-core têm cp315) e Node sobe para 26 (LTS ativa, a mais nova); pacotes que não sobem sem quebrar ficam registrados aqui com o motivo.
- **Revisar em:** a cada sessão que tocar dependências.

## ADR-009 — Caminho do lake: `data/<tabela>/year=YYYY` (sem prefixo `lake/`)

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** médio (mover partições)
- **Contexto:** P-A dizia `data/lake/<tabela>/year=YYYY`; o contrato da Onda B especificou (e B1/B3/B4/B5 implementaram) `data/<tabela>/year=YYYY` direto — `raw_cotahist`, `rates_di`, `earnings`, `open_interest` etc. como filhos imediatos de `data_dir`.
- **Decisão:** adotar o caminho plano (o contrato da onda venceu o detalhe de pasta do P-A). Registra-se aqui para o P-A não voltar a criar dúvida.
- **Por quê:** um nível de nesting a menos, `B3_DATA_DIR` já é o "lake" (o único conteúdo de `/data` são as tabelas + `ingest_state.json` + `raw/`), e todas as ondas B já gravaram nesse layout com testes.
- **Consequência:** `ingest_state.json` continua em `data/ingest_state.json` (esquema do `.gitkeep`); `data/raw/` continua como cache de downloads. Mover para `data/lake/` exigiria regenerar partições — só se o dono pedir.
- **Revisar em:** se o dono quiser o prefixo de volta (questão estética, não funcional).

## ADR-010 — Divergências do pacote earnings (B4), registradas na revisão da Onda B

- **Data:** 2026-11-22 · **Status:** provisórias · **Custo de reverter:** baixo
- **Contexto:** a revisão da Onda B aprovou o B4 com quatro desvios do contrato/espec que são justificados mas não estavam registrados (autonomia não pode virar memória invisível).
- **Decisões:**
  1. `build_earnings(itr_zip, dfp_zip, fca_zip, tickers, last_session)` aceita `Path | Sequence[Path]` por dataset (o contrato §5 desenhou `itr_dir`; a forma de lista serve o backfill e o DFP corrente+anterior). **A assinatura do código vence**; este ADR a torna oficial.
  2. `data/earnings/part-0.parquet` **sem** `year=YYYY` (tabela pequena, ~3,7 k linhas) — exceção documentada ao ADR-009.
  3. **Folga de 1 sessão no backtest:** `earnings.py:492` publica `actual` quando `DT_RECEB <= last_session` — mas o resultado sai **após o fechamento** (spec §6.14: o pregão afetado é o SEGUINTE). Não é look-ahead de execução (a data já era pública), mas o recorte "janela contém resultado" do F1b deve tratar `DT_RECEB` como evento do **pregão seguinte**; registrar no contrato do F1b.
  4. `datetime.now(_tz)` em `earnings.py:846` é carimbo de download (`downloaded_at` da meta), mesma exceção documentada do `fetch.py` — o teste de arquitetura ganha whitelist explícita (arquivo: `fetch.py`, `earnings.py`) para a garantia ser real.
- **Consequência:** dívida de consistência registrada (não bug): `earnings.py` reimplementa ~250 linhas de download condicional do `fetch.py` e mantém `_replace` privado em paralelo ao `b3o.fsutil` — consolidar quando o arquivo for tocado de novo.
- **Revisar em:** C1 (usa `paths()` e `last_session`), F1b (folga de sessão).

## ADR-011 — Runtime: AGENTS (22/11) supera a spec §4.1 em Python e Node

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** médio (rebaixar imagem)
- **Contexto:** a spec §4.1 fixa `python:3.12-slim` / `node:22-slim`; o dono editou o `AGENTS.md` em 22/11 exigindo **Python 3.15** e **`node:26-slim`**. As duas fontes conflitam na versão de runtime.
- **Decisão:** vale o `AGENTS.md` (vontade mais recente do dono). `requires-python = ">=3.15"`, `python:3.15-slim` (3.15.2), `node:26-slim`, `ruff target-version = "py315"`; `uv.lock` regerado no container (workaround do bind). A spec §4.1 fica **desatualizada de propósito** e será corrigida na próxima revisão dela.
- **Por quê:** runtime não é "atualização de biblioteca" (ADR-008) nem escolha do agente; é diretiva escrita do dono. Um conflito silencioso entre duas fontes de verdade é pior que a divergência declarada.
- **Consequência:** build do zero medido em **62,8 s** (critério F0: < 5 min), 276 testes e `ruff` verdes em 3.15.2; `duckdb` vem em wheel `abi3 cp312+`, numpy/pyarrow/pydantic-core têm wheel cp315 — nenhum pacote caiu para build de fonte.
- **Revisar em:** próxima revisão da spec (§4.1 e §10.1).

## ADR-012 — Diagnóstico do `prepare` mora em sidecar (`prepare_state.json`), não no `ingest_state`

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** baixo
- **Contexto:** o C1 precisava gravar `counts_by_market` (D3), o último erro, as falhas de fonte, o pico de RSS, o retrato de rates/OI/earnings/liquidez e a duração do `prepare`. O `ingest_state.json` da Onda B é schema **fechado** (`store/state.py` recusa chave desconhecida, `unknown = set(raw) - _STATE_KEYS` → erro explícito) e é justamente o arquivo cuja integridade a Onda B testa.
- **Decisão:** o `prepare` grava dois arquivos em `data/`: `ingest_state.json` (contrato da Onda B, intocado) e `prepare_state.json` (sidecar versionado, `PREPARE_STATE_VERSION = 1`, escrita atômica via `b3o.fsutil.replace_file`). O `health` lê o sidecar para os campos de diagnóstico; ausente/ilegível degrada para `null`, nunca zero.
- **Por quê:** abrir o schema do state para diagnóstico misturaria "verdade do dado" com "telemetria do job" e obrigaria a reescrever os testes da Onda B. Menor escopo e reversível.
- **Consequência:** existem dois JSONs de estado (o `health` é a visão única); quem apagar o sidecar perde telemetria, não dado. Consolidar num schema único quando o `ingest_state` for revisto (F2/F3).
- **Revisar em:** F2 (quando o `screen` passar a ler o universo do state).

## ADR-013 — Furos do contrato da Onda E fechados na abertura (tipo de entrada, ATM sem curva, duas estatísticas de IV)

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** baixo
- **Contexto:** o contrato `docs/contratos/onda-e.md` citava `OptionQuote` e `CurveInput` sem defini-los, dava a `atm_by_expiry` uma curva que a aritmética de smile não usa, e definia só uma de duas métricas que a spec §5.3 pede (`iv_rank_252` **e** `iv_pct_252`). Deixar assim faria dois implementadores inventarem tipos diferentes.
- **Decisão:** (1) um tipo único `SmilePoint` em `atm.py` (ticker, maturity, strike ¢, t, iv_pct, delta, flags), importado por `checks.py`; (2) `atm_by_expiry(chain, forward)` **sem** curva — `t` já vem no ponto; (3) `iv_rank_252` = posição min–max, `iv_percentile_252` = percentil inclusivo, `vrp_z` = desvio amostral (n−1), todas fatiando 252 internamente; (4) banda de skew em `|Δ|`, interpolação linear em Δ; (5) tolerâncias de `checks` relativas à variância ATM (`5%`), não absolutas; (6) `expected_move_1s` devolve fração.
- **Por quê:** nome de campo/tipo divergente é o bug mais caro do projeto; e a regra "fórmula pura recebe número" obriga a tirar calendário/curva de `derive/`.
- **Consequência:** o `PLAN` não nomeia `ts_slope_30_90`; fica definido na Onda F como `iv_atm(90d) − iv_atm(30d)` em vol points (contango positivo), `null` + flag quando não há vencimento para 90d.
- **Revisar em:** F2 (quando o `screen` exibir as métricas e o glossário ganhar os verbetes `iv_rank`/`iv_percentile`).

## ADR-014 — Pipeline de métricas: tabelas, taxa por sessão (Selic plana) e classe de sinal no F1

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** médio (recalcular o histórico) · **Revisar em:** portão do F1b
- **Contexto:** ao abrir a Onda F quatro furos apareceram: (1) `legs` não carrega preço nem `underlying`, e `ticker → papel` só existia em memória; (2) `rates_di` tem **uma** data (o proxy da B3 publica 20 sessões) e o backtest precisa de 2024–2026; (3) o portão depende das **classes de sinal** que o PLAN põe no F2; (4) `mid`/preço por ação precisa decidir onde o `FATCOT` entra.
- **Decisão:**
  1. `metrics_*` re-juntam `legs` × `raw_cotahist` por `(ticker, refdate)` e resolvem o papel pelo ISIN contra o spot do mesmo dia; `strike`/`distribution_id` do leg persistido vencem (fronteira de leg).
  2. Curva por sessão: PRE da B3 quando existe; senão o fallback **Selic plana** que o `ingest/rates.py` do B3 já implementa (`selic_flat`, SGS 11 anualizada, nós `du = 1…756`), preenchido **em lote** por `ingest/selic.py` com **uma** chamada ao SGS para o intervalo. Sem curva → `iv=None` + `no_rates` (nunca `DF=1`). `prepare --year` de ano fechado não pode clobber `last_session`/`counts_by_date` do lake inteiro.
  3. `derive/signal.py` (classes da §6.9) entra no **F1**, não no F2: sem classe não há portão.
  4. Preço por ação = preço ÷ `quote_factor` (FATCOT) no pipeline; a tabela guarda ¢/ação e IV em %; o R$ de contrato fica para o F4.
  5. `iv_pct_252` da série é calculado na gravação com `leg_id → deque(252)` cronológico (window SQL violaria a regra de consulta única).
  6. A data ex usada no Yang-Zhang é a do `strike_events` de provento (é a primeira sessão com mudança contígua de strike — proxy honesto, documentado na flag).
- **Por quê:** o backtest não roda sem taxa em toda sessão nem sem classe; e preço em unidade errada é o erro mais caro do projeto.
- **Consequência:** `prepare` passa a fazer uma chamada ao BCB (só dias úteis, série pública sem token); `rate_source` vira atributo **por sessão** em `metrics_underlying`; recalcular `model_version` implica refazer o histórico inteiro (`--backfill`).
- **Revisar em:** F2 (quando o `screen` consumir as tabelas) e no portão (a Selic plana pode explicar parte do erro de forward/IV).

## ADR-015 — Calendário futuro: dia útil menos feriado (validado contra 221/221 sessões de 2026)

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** baixo · **Revisar em:** primeiro `prepare` com sessão de 20/11/2026
- **Contexto:** `TradingCalendar` é derivado das sessões do lake (P-C) e por isso **só conhece o passado**. O arquivo termina em 19/11/2026 e há vencimentos até **17/11/2028**; sem extensão, todo vencimento futuro devolveria `business_days = 0` → `T = 0` → `iv_expired` em 100% da grade. Aproximar por dias corridos erraria ~4% em T de 30d (≈0,7 vol point), grande o bastante para contaminar o VRP do portão.
- **Decisão:** `derive/calendar.py` (puro) calcula as sessões de um intervalo: usa as datas conhecidas quando existem e, fora delas, **dia útil menos feriado**. Feriados: fixos (01/01, 21/04, 01/05, 07/09, 12/10, 02/11, 15/11, 20/11, 25/12) + móveis derivados da Páscoa (Carnaval −48/−47, Sexta Santa −2, Corpus Christi +60) + dias sem negócio (24/12, 31/12). `easter_sunday` pelo algoritmo de Meeus (puro, testável).
- **Por quê:** a regra foi **medida**, não suposta: entre 02/01 e 19/11/2026 ela reproduz as 221 sessões do arquivo com zero divergência (nem sobra nem falta). É o único caminho que não exige manter tabela de feriados por ano nem aproximar tempo por dias corridos.
- **Consequência:** feriado municipal/atípico futuro (ou 20/11/2026, ainda não verificável) desloca `T` em 1 dia — o pipeline marca `t_estimated` nas maturities depois da última sessão do lake, para o número não se passar por exato. Teste de referência: fixture com as 221 datas de 2026.
- **Revisar em:** próximo `prepare` (confirmar 20/11) e na virada de ano (feriados móveis de 2027).

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
| Antes do F5.0 | ADR-003 (volumes do frontend) e a direção visual, se a primeira tela pedir ajuste · **Node**: resolvido em 22/11/2026 — `node:26-slim` (ADR-008) |
| 30 dias de uso | todas as provisórias que sobrarem |
