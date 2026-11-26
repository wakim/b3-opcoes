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

## ADR-016 — Repositório no GitHub e push por pacote (diretiva do dono, 22/11/2026)

- **Data:** 2026-11-22 · **Status:** ratificada pelo dono na sessão · **Custo de reverter:** baixo
- **Contexto:** o termo da sessão dizia "sem push (não há remote)". O dono criou `github.com/wakim/b3-opcoes` e pediu o envio do que existe e a manutenção do plano atualizado.
- **Decisão:** `origin = git@github.com:wakim/b3-opcoes.git`, branch `master`, autenticação por SSH (`id_ed25519` do perfil, já autorizada — `ssh -T git@github.com` devolve `Hi wakim!`). Push depois de cada pacote verde; `data/` e `out/` continuam ignorados (dado e relatório não versionam).
- **Por quê:** pedido explícito do dono; o motivo original do "sem push" (ausência de remote) deixou de existir.
- **Consequência:** o `PLAN.md`, este log e o `AGENTS.md` passam a ser a memória pública do projeto; segredo continua só em `.env` (fora do git) e não há credencial em arquivo versionado.
- **Revisar em:** se o dono quiser outro fluxo (branch por sessão, PR).

## ADR-017 — Preço para IV/forward é o fechamento do negócio (não o mid do book)

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** médio (recalcular o histórico)
- **Contexto:** o contrato da Onda D e o da C falavam em "mid" para IV e forward (`ParaityQuote.call_mid`). Medição em 3 datas × 5 ações: o book de fechamento da B3 é largo e velho — PETRX44 (K=4316, 18/12) tinha `70/167` e fechamento 157 ¢; o mid (118,5) dá IV 24,8% e o fechamento dá 32,8%, e o call do mesmo strike fecha em 158 ¢ (IV 32,84%).
- **Decisão:** a série/forward/ATM usam o **`close`** (último negócio do dia) como preço de inversão. `best_bid`/`best_ask` continuam guardados e alimentam **só** a classificação de liquidez (§6.9) e as flags (`one_sided`, `wide_spread`). O `mid` fica para `structure_eval` (F4), onde o que importa é o preço executável.
- **Por quê:** paridade E/E com fechamento dá mediana 1,2 vol point contra 4,8 com mid; o fechamento é uma transação real, o book de fim de dia é uma foto de intenções.
- **Consequência:** `metrics_series.mid` passa a carregar o `close` (documentado no schema); quem quiser o mid lê `trades`/book da tabela bruta. Recalcular se o dono preferir mid.
- **Revisar em:** F4 (estruturas) e no primeiro dia com book realmente líquido.

## ADR-018 — Paridade E/E: critério do PLAN não é atingível (ratificado 23/11/2026)

- **Data:** 2026-11-22 · **Status:** **ratificada pelo dono em 23/11/2026** · **Custo de reverter:** baixo (é critério, não código)
- **Contexto:** o PLAN pede `|IV_call − IV_put|` com mediana ≤ 0,5 e P90 ≤ 1,5 vol point nos pares E/E ATM ±10%. Medido com a melhor convenção de preço (fechamento, ADR-017): **mediana 1,22 / P90 5,14** (19/11, n=140), **1,09 / 6,33** (13/11, n=173) e **1,84 / 16,96** (15/10, n=202). A causa é física: vega no ATM de 20 dias ≈ 4,8 ¢ por ponto de vol, então **1 centavo** de diferença entre call e put já é 0,21 vol point; o tick da B3 é 1 ¢ e os fechamentos das duas pernas vêm de negócios em horários diferentes.
- **Medição independente de 23/11 (revisão do F1, v2 recalculado, `parity_layers.py`):** todos os pares E/E ATM ±10% com `trades>0` nas duas: **1,137/4,459 (n=137)**, **0,966/5,713 (n=166)**, **1,789/12,117 (n=181)** — confirma o ADR-018. **A proposta original de duas camadas NÃO fecha**: com book nos dois lados `spread ≤ 5%` há **zero pares** nas três datas (o book de fechamento da B3 é largo); com `spread ≤ 5%` e negócio nas duas há só **11/5/21 pares** e mediana **1,24/0,80/1,29** — ainda acima de 0,5. Ou seja, a camada (a) não é mensurável com estabilidade e a camada (b) falharia o próprio P90≤8 em 15/10.
- **Decisão ratificada:** **paridade E/E deixa de ser critério binário de aceitação** e passa a ser **evidência descritiva obrigatória** no relatório de validação: para cada data, publicar `n`, mediana, P90 e o recorte exato (e a fração com book/spread estreito). O que ela precisa provar — que `forward`, taxa e preço estão certos — já é provado de forma mais forte pela **conferência à mão** (IV de 125 séries PETR4 por bissecção própria, max `1,97e−14` em fração; forward por paridade idêntico ao gravado). Não se inventou novo limiar para "passar": removeu-se um gate que o dado não pode sustentar, com os números expostos.
- **Consequência:** critério 1 do PLAN marcado `[x]` como "ratificado — evidência descritiva", com a medição ao lado (não com "passa no limiar original").
- **Revisar em:** F2, se o `screen` passar a exibir paridade; ou se a B3 mudar o book de fechamento.

## ADR-019 — O projeto não tem suíte de testes (diretiva do dono, 22/11/2026)

- **Data:** 2026-11-22 · **Status:** ratificada pelo dono na sessão · **Custo de reverter:** baixo (o histórico do git tem tudo)
- **Contexto:** o projeto nasceu com "toda fórmula e o parser têm teste" e chegou a 523 testes em `tests/`. O dono decidiu, com o produto ainda em formação, que **testes não fazem sentido neste estágio**.
- **Decisão:** `tests/` foi **removido** do repositório; `pytest` saiu de `pyproject.toml`/`uv.lock` e do caminho oficial; o serviço `test` do compose vira **container de ferramentas** (roda `b3o`, `python`, `ruff`); `.gitattributes` perde as regras de fixture. O que substitui o teste:
  1. **rodar no dado real** (`prepare`, `health`, `metrics`, `backtest`) e conferir 1 número à mão por caminho independente;
  2. **auditoria independente** de dado (recálculo por fora do código, como a do F0);
  3. **falha explícita** (`null` + flag) em vez de zero silencioso;
  4. **`model_version`** + recálculo inteiro quando a fórmula muda.
- **Por quê:** diretiva do dono; o custo de manter uma suíte grande num produto cujas fórmulas ainda estão mudando não se pagava.
- **Consequência:** os critérios do `PLAN.md` que dizem "pytest verde" ficam **revogados** (o texto fica como registro); as armadilhas do `AGENTS.md` passam a ser a única proteção contra regressão de dado — por isso medição nova entra lá com número. Reintroduzir teste exige pedido do dono.
- **Revisar em:** quando o produto estabilizar (F4/F5) ou se um erro de fórmula escapar para um número exibido.

## ADR-020 — Superfície: calibração dos validadores, `inconsistent` informativo e carry assimétrico (`model_version` 2)

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** caro (recalcular 722 sessões, ~26 min)
- **Contexto:** a primeira rodada real do pipeline (19/11/2026) marcou **47% das séries como `inconsistent`** com as tolerâncias do contrato (0,05 vol point), o que **excluía o strike ATM** do smile e dava `iv_atm_30d` 29,83 na PETR4 em vez do valor conferido à mão (32,84). Ao mesmo tempo, a sanidade de carry simétrica do forward (`|ln(F/S)/T − r| ≤ 5%`) cortava papel de dividendo alto (BBAS3, `q ≈ 7,3% a.a.`).
- **Decisão:**
  1. `butterfly_tol = 0,25` (25% da variância total ATM) e `monotonicity_tol = 1,0` vol point, com validação só em `|ln(K/F)| ≤ 0,10` — calibrados no ruído de tick medido (ADR-018), não "até passar";
  2. **`inconsistent` é flag informativa**: não tira o strike do **ATM** (que precisa dos dois vizinhos de F) e tira só do **skew/butterfly**;
  3. carry do forward **assimétrico**: `F` acima da curva é rejeitado (+5% a.a.); dividendo implícito até **25% a.a.**; fora disso `forward_outlier`, vencimento sem `F`;
  4. `days_to_liquidate` = Σ OI ÷ **quantidade** média do à vista 20d (não volume financeiro);
  5. `model_version` sobe para **2** e o histórico é recalculado inteiro.
- **Por quê:** o número que o produto exibe tem que ser o mesmo que se confere à mão; e regra simétrica em carry confunde dividendo com erro de cotação.
- **Consequência:** `inconsistent` caiu de 47% para 6,5% (19/11) e 10,0% no período; PETR4 volta a 31,43 no 30d (interpolação entre 32,84 em 18/12 e 30,96 em 15/01); BBAS3 recupera o ATM. O skew usa só as séries sem flag — quem quiser filtrar mais, filtra a flag.
- **Revisar em:** F2 (quando o `screen` exibir RR/BFLY) e no portão.

## ADR-021 — Critérios 5, 6 e 7 do F1: ruído de fechamento e doc desatualizada (ratificado 23/11/2026)

- **Data:** 2026-11-23 · **Status:** ratificada pelo dono · **Custo de reverter:** baixo (são critérios, não código)
- **Contexto:** a revisão independente do F1 mediu pela primeira vez os critérios 3–6 no dado real e encontrou:
  - **critério 5** (ATM 30d sem salto > 2 vol points em dia de rolagem): **7 saltos em dias de rolagem** em 2026 (28/01 +2,11 · 23/03 −3,06 · 08/04 −3,74 · 12/05 −2,91 · 22/07 +4,78 · 28/08 +2,27 · 01/09 +2,75). O script oficial checa todos os dias e acha 26; restringindo a rolagem (o que o PLAN pede) ainda falha.
  - **critério 6** (RR25 da PETR4 negativo em ≥ 80% dos dias): **72/143 = 50,3%**, mediana −0,007, em 2026. Em 19/11 o valor é −1,60 (bate com o relatório de 22/11) — o sinal existe no dia, mas não é persistente no fechamento.
  - **critério 7** (nenhuma série `inconsistent` entra no smile): o ADR-020 tornou a flag informativa — ela fica no ATM e sai só do skew/butterfly. O PLAN está desatualizado, não o código.
- **Decisão ratificada:**
  1. Os critérios 5 e 6 passam a ser **informativos**, não gates de aceitação: a oscilação do ATM e do RR25 no fechamento da B3 é ruído de preço (tick de 1 ¢, book velho) mais o choque real de mercado de 2026-10-26 (ADR-034), não erro de fórmula — as conferências à mão (IV, forward, gregas, RV, VRP, rank/percentil) batem todas. Publicar os números e o `n` em `out/`/relatório; **não** suavizar ATM nem trocar a banda de skew para "passar" (isso seria P-L + `model_version` 3 sem evidência de defeito).
  2. O critério 7 do PLAN é reescrito para refletir o ADR-020: "`inconsistent` não entra no **skew/butterfly**; continua no ATM, que precisa dos dois vizinhos de F".
  3. O critério 4 do PLAN é mantido, mas a medição oficial passa a reportar **também** o `max|ΔIV|` na banda validada `|ln(K/F)| ≤ 0,10` (medido **0,199**, n=68; em `|K−F|/F ≤ 5%` é 0,053, n=34): o valor literal 7,196 vem de opções a 67–82% de moneyness, fora do domínio que o modelo usa. Nenhum limiar é alterado.
- **Consequência:** F1 pode ser marcado `[x]` com **ressalva explícita**, e a Onda G segue com os limiares pré-registrados intactos. O relatório do portão deve mencionar os 346 `premium_cheap` de 26/10. *Corrigido pelo ADR-034:* não são artefato de dado — 26/10 é choque real de mercado — e nenhum deles entrou no backtest (não havia 20 sessões futuras).
- **Revisar em:** portão do F1b (se o sinal não se sustentar, voltar aqui antes de mexer em qualquer limiar) e F2 (quando RR/BFLY aparecerem na tela).

## ADR-022 — F1b: convenções do `rv_fwd20`, do recorte e da estatística (implementador)

- **Data:** 2026-11-23 · **Status:** provisória · **Custo de reverter:** baixo (recarregar o backtest, ~1 min)
- **Contexto:** a spec §6.13 e o contrato onda-fg §2 fixam *o quê* medir (Yang-Zhang das 20 sessões seguintes, `edge = iv_atm_30d − rv_fwd20`, classes gravadas), mas não fecham quatro detalhes de *como*: o número de barras do Yang-Zhang, o mercado, o que fazer sem barra na ponta da janela e a definição do recorte informativo da data-armadilha.
- **Decisão:**
  1. `rv_fwd20` reusa `derive/realized.yang_zhang_vol` com **`window = 21` barras** (a barra-base de `data` + as 20 futuras) → **20 retornos**, exatamente as 20 sessões `data+1 … data+20`. `window=20` daria 19 retornos — off-by-one que a spec não pede.
  2. Usa o **mesmo mercado** (`underlying_market`, `010`/`020`) da linha de métrica, via método novo `QueryService.spot_bars_in_market` — nunca mistura lote padrão com fracionário na mesma série de RV.
  3. Fator de escala acumulado nas barras **anteriores** ao evento (`corporate_events.event_factor_series`); gap de provento excluído pelas `ex_div_dates` do B2. A exclusão **puxa barras mais para trás** (a janela não encolhe — mesma convenção do `rv20/30/60` do pipeline); lookback de 120 dias corridos para a barra-base e o pullback.
  4. Descarte **contado por motivo**: sem 20 sessões futuras no lake; sem barra na sessão final da janela (`data+20`); Yang-Zhang sem barras suficientes. Medido: 420 sem sessões futuras, 0 sem barra na ponta, 0 RV falhou (os 37 papéis com z são líquidos).
  5. Recorte informativo da trap: janela de RV **passada** = 30 sessões até `data` (rv30) **ou futura** = 20 sessões seguintes; `2026-10-26` dentro dela marca a observação. **Só informativo** — o veredito usa o período completo (pré-registrado).
  6. Estatística: mediana `statistics.median`; P10/P90 por **interpolação linear** entre ranks (convenção `linear`); taxas em %. `signal_backtest(signal_class, ticker=None)` é pura em `derive/backtest.py` (F2/F3 consome depois).
- **Por quê:** menor escopo e mais reversível; tudo reaproveita o Yang-Zhang e o ajuste de escala já validados à mão no F1, sem reimplementar fórmula (que exigiria `model_version` 3).
- **Consequência:** o `rv_fwd20` de papel que não negocia na sessão `data+20` sai (honesto, contado); a janela pode estender-se um pouco antes de `data` quando faltam barras intermediárias ou há provento — documentado no relatório. Nenhuma fórmula de `derive/realized.py` mudou.
- **Revisar em:** ratificação do portão (ADR-023) e F2 (quando `signal_backtest` virar tool).

## ADR-023 — F1b: o portão NÃO PASSA — RATIFICADO: parar no portão (Opção 1)

- **Data:** 2026-11-23 · **Status:** **ratificada pelo dono em 23/11/2026 — Opção 1 (parar no portão)** · **Custo de reverter:** médio (repensar F2+ se o sinal for trocado)
- **Ratificação (23/11/2026):** o dono escolheu a **Opção 1 — parar no portão**, após parecer independente (`docs/relatorios/2026-11-23-validacao-f1b.md`, **APROVADO**) confirmar que o NÃO PASSA se reproduz por fora com diferença 0 em 6.773 observações, sem look-ahead e com critérios intactos. **O sinal de reversão do VRP está falsificado neste dado:** as duas classes têm acerto **abaixo da base `none`** (rich 42,3% vs 43,7%; cheap 49,8% vs 56,3%) — valor negativo, não falha marginal. O dado mostra *momentum/clustering* de vol, não reversão. **F2+ não será construído sobre este sinal.** O produto pode seguir como **painel descritivo** (IV/RV/VRP/gregas/liquidez/estrutura) sem alegação preditiva. Um futuro estudo de *momentum* só é honesto **fora da amostra**, com critérios pré-registrados às cegas (não um re-run nestes dados — P-L). A Opção 3 (mudar limiar no mesmo dado) permanece proibida.
- **Contexto:** o backtest rodou no histórico real (2024-01-02→2026-11-19, 722 sessões, `model_version=2`, 6.773 observações válidas após aquecimento de 252 sessões por papel, que começa em 2025-02-13). As três provas de que o resultado *pode estar errado* passaram: permutar o `rv_fwd20` muda o resultado (rich 42,3%→67,3%, mediana −1,41→+7,84); o z recalculado por fora bate exato e não vaza futuro; um `rv_fwd20` da PETR4 (2025-02-20) conferido à mão por Yang-Zhang independente bate com diferença 0. **O veredito abaixo é mecânico sobre os critérios pré-registrados; a decisão de seguir/parar não é do implementador (P-L).**
- **Medição (período completo, como pré-registrado):**
  | Classe | n | Acerto | Mediana edge | Base `none` | Margem | Veredito |
  |---|---:|---:|---:|---:|---:|---|
  | `premium_rich` | 617 | 42,3% | −1,41 | 43,7% (edge>0) | −1,4 p.p. | **NÃO PASSA** |
  | `premium_cheap` | 498 | 49,8% | +0,00 | 56,3% (edge<0) | −6,5 p.p. | **NÃO PASSA** |
  Nenhuma classe bate acerto ≥ 55%, mediana no sinal certo (≥ +1 / ≤ −1) nem margem ≥ +5 p.p. sobre `none`. `skew_anomaly` (n=29) e `event_pressure` (n=1117) só relatam distribuição.
- **Leitura (até 10 linhas):** a tese de reversão do VRP não se sustenta neste dado EOD da B3. Quando a IV está cara vs a RV passada (`premium_rich`), a RV futura tende a ser **ainda mais alta** (acerto 42,3%, abaixo da base 43,7%; mediana de edge negativa) — ~~vol clustering/momentum, não reversão~~. O `premium_cheap` tem 33 obs com 2026-10-26 na janela (acerto 100%, mediana −32,8); **sem** elas, o cheap cai para 46,2% de acerto e mediana +0,17 — também não passa. O produto não deve seguir para F2+ sem o dono decidir.
  **Leitura corrigida pelo ADR-034 (23/11/2026):** 26/10 não é "split sem ajuste" nem artefato — é choque **real** de mercado (IBOV −14,35%, sem evento corporativo, forward por paridade no spot novo). A margem negativa do `premium_rich` vem inteira de 142 observações anteriores ao choque (acerto 0%, mediana −68,29), com a IV alta porque o evento estava agendado; fora delas o `rich` tem 54,9% contra a base de 45,0% (mediana +0,84) — também não passa nos critérios, mas "momentum de vol" e "valor negativo" não são leituras sustentadas. O veredito e a Opção 1 não mudam.
- **Proposta (o dono escolhe, não o implementador):**
  1. **Parar no portão (default P-L):** não construir F2+ sobre este sinal; repensar a tese antes de continuar.
  2. **Mudar limiar com justificativa *a priori*** (ex.: exigir `vrp` maior, ou tenor casado diferente) **e validar num período fora da amostra** — proibido otimizar no mesmo dado (P-L); qualquer mudança de fórmula sobe `model_version` e recalcula o histórico.
  3. **Trocar o sinal:** testar uma tese de *momentum* de vol (a direção que o dado mostra) em vez de reversão, com novos critérios pré-registrados antes de olhar o resultado.
- **Consequência (ratificada):** `PLAN.md` F1b marcado `[x]` com a evidência (medição + decisão registrada); `AGENTS.md` registra "o sinal NÃO PASSA — parar no portão" (não "passou"). O relatório completo está em `data/out/backtest.md` (fora do git). **F2 em diante fica suspenso** até a tese ser repensada; nenhum funil/ranking é construído sobre o sinal falsificado.
- **Revisar em:** se o dono quiser retomar o produto preditivo (estudo de momentum fora da amostra, pré-registrado às cegas) ou redefinir o produto como painel descritivo.

## ADR-024 — F2 descritivo: sem classe de sinal, sem ranking preditivo

- **Data:** 2026-11-23 · **Status:** ratificada pelo dono (pedido para iniciar o F2 na modalidade descritiva) · **Custo de reverter:** baixo (é uma tela/relatório; não toca fórmula nem `model_version`)
- **Contexto:** o ADR-023 (Opção 1) suspendeu o F2+ preditivo e autorizou seguir como painel descritivo. O F2 do PLAN, como escrito, dependia das classes de sinal (`premium_rich`/`premium_cheap`/…) e da taxa histórica da classe — tudo falsificado pelo portão. Construí-lo literalmente violaria a decisão.
- **Decisão:**
  1. O F2 passa a ser **descritivo**: `QueryService.screen(date)` devolve uma linha por papel do universo líquido com IV ATM 30d, IV rank/percentil, RV20/30/60, VRP, z, RR25, BFLY25, exp. move, `days_to_liquidate`, nº de séries líquidas, próximo resultado (`actual`/`estimated`) e flags; papel sem dado sai com **motivo** explícito.
  2. **`signal_class`, taxa de acerto e qualquer rótulo preditivo não aparecem** na saída (CLI nem relatório). O campo continua gravado em `metrics_underlying` por compatibilidade do lake; a tela simplesmente não o usa.
  3. A ordenação é **determinística e neutra** (alfabética por papel). Não há score, nota nem posição de ranking; "componentes por coluna" valem como leitura, não como pontuação.
  4. `b3o screen --date` grava `data/out/screen-AAAA-MM-DD.md` (P-T) com tabela + ficha por papel.
  5. As tarefas e a aceitação do F2 foram reescritas no `PLAN.md` (F2d.1–F2d.4); o F2 original fica preservado em bloco suspenso.
- **Por quê:** é o caminho já ratificado no ADR-023; entrega valor de leitura (o que o dado diz hoje) sem repetir a alegação que o portão derrubou.
- **Consequência:** F3 (MCP) passa a expor `screen` descritivo; o `signal_backtest` continua existindo como registro histórico do portão, **não** como coluna do funil. Qualquer reintrodução de ranking preditivo exige novo ADR e novo portão.
- **Revisar em:** quando o painel estiver no F5 (UI) ou se o dono decidir retomar o produto preditivo (estudo de momentum fora da amostra).

## ADR-025 — `health` honesto sobre `model_version` (F3.0)

- **Data:** 2026-11-23 · **Status:** provisória (orquestrador) · **Custo de reverter:** baixo
- **Contexto:** `b3o health` lia `model_version` do `ingest_state` e mostrava **1** enquanto `metrics_underlying`/`metrics_series` estão em **2** (o `prepare` não roda desde o bump para v2; medido em 23/11/2026). Com o F3 expondo o `health` ao agente via MCP, o número viraria mentira visível.
- **Decisão:** `Health.model_version` passa a ser o **maior** `model_version` das tabelas derivadas (`metrics_underlying` ∪ `metrics_series`), lido com parâmetros nomeados. Os campos novos dizem o resto: `ingest_model_version` (o que o `ingest_state` diz), `model_version_mismatch` (divergência) e `model_version_source`/`model_version_note` (de onde veio e por quê). Sem tabela derivada, o fallback para o `ingest_state` é explícito — nunca uma versão inventada.
- **Por quê:** a versão que importa é a que produziu o número que o painel mostra; o `ingest_state` é retrato do último `prepare`. Não se mexeu em `prepare`/`_update_state` (fora do escopo e sem necessidade).
- **Consequência:** `b3o health` passa a mostrar `2` com `mismatch: true` e a nota da divergência; nenhuma fórmula muda e `model_version` do lake continua 2.
- **Revisar em:** quando o `prepare` rodar de novo (o `ingest_state` se alinha sozinho) ou se o dono quiser reconciliar os dois de vez.

## ADR-026 — F3: FastAPI + MCP Streamable HTTP, uma `QueryService` e o envelope de resposta

- **Data:** 2026-11-23 · **Status:** provisória (orquestrador) · **Custo de reverter:** médio (troca de SDK/transporte)
- **Contexto:** o F3 precisava da API e do MCP no mesmo processo, sobre a mesma `QueryService` (spec §7/§8), com `max_rows`, teto de 200 KB e erro `{code,message,hint}`. O `compose.yaml` já chamava `uvicorn b3o.api:app`, mas o módulo não existia.
- **Dependências (ADR-008, última estável; `uv lock` no container):** `fastapi 0.165.0`, `uvicorn 0.54.0`, `mcp 2.3.0` (SDK oficial; transporte Streamable HTTP e cliente `mcp.Client`). O `uv.lock` foi regerado dentro do container pelo workaround do bind (copiar para `/tmp`, `uv lock`, copiar de volta).
- **Decisão:**
  1. `backend/src/b3o/api/`: `app.py` (FastAPI), `state.py` (uma instância no lifespan), `payloads.py` (construtores compartilhados por HTTP e MCP), `serialize.py` (envelope, limites, erro) e `mcp_server.py` (as 6 tools).
  2. **HTTP e MCP chamam as mesmas funções** de payload — o mesmo JSON é o critério de contrato, não uma coincidência.
  3. O app MCP é montado em `/` com `streamable_http_path="/mcp"` (montar em `/mcp` com caminho `/` devolvia 307 para `/mcp/`). O **lifespan do app montado não roda sozinho**: o lifespan do FastAPI executa `mcp_app.router.lifespan_context(mcp_app)`.
  4. `stateless_http=True` (leitura pura, sem sessão de servidor) e `TransportSecuritySettings(allowed_hosts=…)` para `127.0.0.1`, `localhost` e `host.containers.internal` (o default recusa Host fora de localhost com 421).
  5. Envelope `{as_of_session, model_version, data, meta}`; `meta.total/returned/truncated` (e `meta.lists` quando há mais de uma tabela). `max_rows` padrão **20**, teto **60**; o teto de **200 KB** trunca a lista principal pelo maior prefixo que cabe, sem cortar linha.
  6. O compose publica `${B3_APP_PORT:-8080}:8080`; o default continua 8080 (o host desta sessão tem outro projeto local nessa porta — a app dentro do container é sempre `:8080`).
- **Por quê:** o SDK oficial é o caminho mais curto para Streamable HTTP com cliente de fumaça; a montagem no mesmo processo garante a regra "uma camada de consulta"; o envelope único evita divergência entre bordas.
- **Consequência:** `podman compose up -d app` sobe HTTP + `/mcp`; o smoke `backend/scripts/mcp_smoke.py` prova `tools/list`, os números e a igualdade MCP×HTTP. Rotas de escrita/journal/positions/structure continuam fora (F4/F5).
- **Revisar em:** F5 (PWA/Tailscale: incluir o host `*.ts.net` no `allowed_hosts`) ou se o SDK mudar a API de montagem/lifespan.

## ADR-027 — F3: `signal_backtest` fora do MCP; leitura reconstrói forward/exp_move do termo

- **Data:** 2026-11-23 · **Status:** provisória (orquestrador) · **Custo de reverter:** baixo (é exposição/leitura; não toca fórmula)
- **Contexto:** a spec §7/§8 lista `signal_backtest`, mas o ADR-023/024 parou no portão e redefiniu o produto como descritivo. Além disso, a estrutura a termo precisa de `forward`/`exp_move` por vencimento, que **não** estão gravados (só o forward 30d e o exp_move 30d de `metrics_underlying`).
- **Decisão:**
  1. **`signal_backtest` não é exposto** no F3 (nem HTTP nem MCP). Ele continua como registro histórico do portão (`docs/relatorios/2026-11-23-validacao-f1b.md`, `data/out/backtest.md`); expô-lo convidaria a leitura preditiva que o ADR-023 falsificou. O `PLAN.md` F3 foi ajustado.
  2. `term.forward_brl` é reconstruído **na leitura** por paridade dos `mid`s gravados, chamando `derive.forward.forward_by_parity` (a mesma função pura do pipeline); `forward_method` diz `crossing`/`ols`/`none`. `term.exp_move_1s` usa `derive.dist.expected_move_1s` sobre o straddle ATM do vencimento (constante 1,25 da §6.10).
  3. Nenhuma métrica gravada é recalculada nem reescrita; `model_version` continua **2**. A reconstrução é determinística sobre o dado do lake e falha explícita (`null` + método `none`) quando não há par honesto.
  4. Campos de preço das leituras novas levam sufixo `_brl` (P-R: unidade no nome); o rascunho do prompt listava `bid/ask/mid/close/strike` sem sufixo. `ScreenRow` mantém os nomes do contrato do F2 (`spot_brl`, `forward_30d`).
- **Por quê:** expor `signal_backtest` contradiria o ADR-024; deixar `forward`/`exp_move` nulos empobreceria a estrutura a termo sem necessidade, já que os insumos e as funções puras existem e são os mesmos do cálculo gravado.
- **Consequência:** o termo é descritivo e reproduzível; quem precisa do número oficial de 30d continua lendo `summary.forward_30d`/`exp_move_1s_30d`. `docs/glossario.md` registra a diferença.
- **Revisar em:** se o dono pedir o backtest como leitura histórica explícita (nova rota read-only, com aviso do portão) ou no F4 (estruturas), que reusa o forward do termo.

## ADR-028 — F4: `structure_eval` descritivo — unidades, payoff, capital e cenários

- **Data:** 2026-11-23 · **Status:** provisória (orquestrador) · **Custo de reverter:** baixo (cálculo on-demand; nada é gravado)
- **Contexto:** a spec §7 fixa `structure_eval(legs, date=None, capital=None, scenarios=True)` e proíbe margem e PoP/RND; o ADR-017 fixa o preço de inversão (fechamento) e o ADR-023/024 fixam o produto como descritivo. O que não estava fechado: a **unidade de `quantity`**, o que entra no "prêmio líquido", o sinal da perda/ganho máximo, o que é "capital comprometido", a grade de cenários e como versionar a matemática nova sem tocar `model_version`.
- **Decisão:**
  1. `quantity` é **número inteiro de ações** (1 contrato = 100). Preço por ação = `metrics_series.mid` da sessão (ADR-017); perna de ação = fechamento do `010`/`020`. **Nenhum lote é inventado e nada é multiplicado por 100.**
  2. A ação entra no cálculo como perna de valor terminal `s` e fluxo de abertura `preço_da_sessão × quantity`. Isso faz `net_premium_brl` ser o **fluxo líquido de entrada**: positivo = débito pago, negativo = crédito recebido. A coberta tem breakeven `S₀ − prêmio da call`, como à mão.
  3. `max_loss_brl`/`max_gain_brl` são **magnitudes ≥ 0** (0 = "não perde"/"não ganha"); o sinal vive no payoff e nos cenários. `null` **só** quando ilimitado, junto de `unbounded_loss`/`unbounded_gain`.
  4. `capital_committed_brl` = débito líquido em estrutura de débito; perda máxima em estrutura de crédito/risco definido; `null` quando ilimitado. **Sem margem** (não está no dado; aproximação errada é pior que ausência). `capital` informado é orçamento: `capital_over: true` quando o comprometido passa dele — **não é erro**.
  5. Breakevens são **raízes lineares por segmento** em `s ≥ 0`, inclusive no trecho ilimitado (`slope∞ = Σ sinal×quantity` sobre ação e calls); perda/ganho máximo = mínimo/máximo do payoff nos kinks + `s = 0`, com o limite assintótico quando a inclinação não é zero.
  6. Cenários: grade fixa de **45 pontos** = 5 spots (`0,8–1,2 × S`) × 3 choques de IV (`−5/0/+5` pts) × 3 horizontes (`0/7/15` dias úteis). Opção reaprecificada com **Black-76 de `derive/iv.py`**, `F' = F·S'/S`, `σ' = (iv_pct + choque)/100`, `T' = max(t − dias/252, 0)`; em `T' = 0` vale o intrínseco; `DF` restante sai da curva da sessão (`log_df_interp_or_none`). Insumo faltando → **`scenarios = null`** + `scenarios_unavailable` e a causa (`forward_unavailable`/`no_rates`/`no_iv`); o payoff no vencimento continua.
  7. `structure_version = 1` no módulo, **separado de `model_version`** (que segue 2): a matemática nova é pura, on-demand, e não recalcula nem regrava métrica. `POST /api/structure` é **cálculo, não gravação** — não há estrutura persistida, journal ou posição nesta onda.
  8. Faixa `mid ± ½ spread` (PLAN F4.1) por perna de opção quando os dois lados do book existem; sem book → `null` (nunca 0). A perna de ação fica sem faixa nesta onda (o `_spot_brl` lê o close).
- **Por quê:** menor escopo e mais reversível; tudo reusa as funções puras já validadas (`iv`, `greeks` via leitura, `forward`, `conventions`, `dist`); unidade em ações é a única que não inventa contrato; e o "sem margem" é explícito da spec.
- **Consequência:** a tool **avalia a estrutura dada** — não sugere, não compara, não ordena; `signal_class` não é lido. O resumo do CLI e o payload HTTP/MCP saem do **mesmo** `payloads.structure_payload` (o número não pode divergir entre bordas). Erros novos (`bad_legs`, `underlying_mismatch`, `maturity_mismatch`) entram no mapa de status do envelope (400) e os de dado seguem 404.
- **Revisar em:** F5 (builder da UI) e F6 (posições — onde o preço de entrada deixa de ser o da sessão e vira `entry_brl`).

## ADR-029 — F5: stack do frontend e versões auditadas (ADR-008)

- **Data:** 2026-11-23 · **Status:** provisória (orquestrador) · **Custo de reverter:** baixo (lock + scaffold)
- **Contexto:** o F5.0 precisava fixar a stack da SPA. ADR-008 exige a última **estável** de cada biblioteca; o host não tem Node, então a auditoria foi feita com `npm view <pkg> version` **dentro do `node:26-slim`**.
- **Decisão:** React **19.3.1**, `react-dom` 19.3.1, `@tanstack/react-router` **1.170.46**, `@tanstack/router-plugin` **1.169.0**, `@tanstack/react-query` **5.108.4**, Tailwind **4.3.5** + `@tailwindcss/vite` 4.3.5, Vite **8.3.8**, ESLint **10.15.0**, `@eslint/js` 10.0.1, `typescript-eslint` **8.74.0**, `eslint-plugin-react-hooks` 7.1.1, `globals` 17.14.0, `@types/react`/`@types/react-dom` 19.3.0. **TypeScript 5.9.3**, não o `latest` 7.0.2: o `typescript-eslint` estável mais novo declara peer `typescript >=4.8.4 <6.1.0`, e a única versão compatível com TS 7 é a canary `8.74.1-alpha.0` (não estável). 5.9.3 é a última 5.x estável que fecha a cadeia sem `--legacy-peer-deps`. `package-lock.json` versionado; build com `npm ci`; `node_modules` no volume nomeado `b3o-node-modules`.
- **Por quê:** ADR-008 (última estável) aplicado com o critério de **compatibilidade entre estáveis**; forçar TS 7 exigiria downgrade do lint para alpha, o que é mais frágil que ficar uma major atrás no compilador.
- **Consequência:** `npm run lint` e `tsc --noEmit` limpos no container; avisos `[MODULE_LEVEL_DIRECTIVE] "use client"` do Rolldown (Vite 8) são cosméticos em SPA pura (não há RSC). `routeTree.gen.ts` é versionado para que `tsc --noEmit` funcione em checkout limpo.
- **Revisar em:** quando houver `typescript-eslint` estável compatível com TS 7 (então subir o compilador); ou F5-B se alguma lib travar.

## ADR-030 — F5: um processo, dispatcher `/mcp` × SPA, cache e `X-App-Token` adiado

- **Data:** 2026-11-23 · **Status:** provisória (orquestrador) · **Custo de reverter:** médio (montagem do app)
- **Contexto:** o app MCP é montado em `/` (`streamable_http_path="/mcp"`) desde o F3. Montar `StaticFiles` também em `/` faria um engolir o outro. Além disso, `@app.middleware("http")` usa `BaseHTTPMiddleware`, que quebra streaming — e os smokes F3/F4 (53/53, 93/93) têm de continuar intactos.
- **Decisão:**
  1. Um **dispatcher ASGI** montado em `/` decide por caminho: `/mcp*` → `mcp_app`; resto → `dist` do Vite. Rotas `/api` registradas **antes** do mount. Nenhum segundo processo/servidor.
  2. SPA fallback: caminho sem extensão (inclusive `/t/PETR4`) → `index.html`; asset com extensão inexistente → **404** (nunca o shell). `index.html` `no-store`; `/assets/*` `immutable`; `/api` (sucesso **e** erro) `no-cache`. `GZipMiddleware` é ASGI puro — seguro para o MCP.
  3. O carimbo `sessão AAAA-MM-DD · D-1` vem de `/api/health` na tela; o dispatcher também injeta a última sessão do lake no `<meta name="b3o-session">` do `index.html` (uma leitura no lifespan), para um `GET /` já trazer o carimbo sem JS.
  4. `dist` ausente → **503 `frontend_missing`** com dica do build, nunca página em branco. Caminho por env `B3_FRONTEND_DIR` (fora do `config.yaml`, que tem quatro chaves fechadas — `bad_config` se mudar).
  5. **`X-App-Token` fica para o F6**: nesta versão não existe rota de escrita, então não há o que proteger. `POST /api/structure` continua sendo cálculo puro.
- **Por quê:** menor escopo e mais reversível; o dispatcher resolve a armadilha do mount sem tocar no transporte MCP nem no envelope.
- **Consequência:** HTTP e MCP seguem na mesma porta e na mesma `QueryService`; `mcp_smoke.py` 53/53 e `structure_smoke.py` 93/93 inalterados. Static servido pelo FastAPI, sem servidor no frontend.
- **Revisar em:** F6 (introduzir `X-App-Token` junto da primeira rota que grava); F5-D (`allowed_hosts` com `*.ts.net`).

## ADR-031 — F5: glossário com fonte única e parser puro

- **Data:** 2026-11-23 · **Status:** provisória (orquestrador) · **Custo de reverter:** baixo (um arquivo de parser)
- **Contexto:** `/api/glossary` e `/api/glossary/{key}` precisam servir verbetes sem duplicar conteúdo em Python. `.dockerignore` exclui `docs/` e `*.md`, então o arquivo não entraria na imagem. A spec §9 prevê "verbete + exemplo com o dado da sessão" — injetar o número do dia no texto é uma fonte nova de divergência.
- **Decisão:**
  1. Fonte única `docs/glossario.md`; `.dockerignore` re-inclui só esse arquivo (`docs/*` + `!docs/glossario.md` **depois** do `*.md`) e o Dockerfile o copia para `/app/docs/glossario.md`. Caminho por env `B3_GLOSSARY_PATH`; ausência → `503 glossary_missing`.
  2. `api/glossary.py` é **puro**: `parse_glossario(texto) -> verbetes`. A leitura do arquivo fica na borda (`payloads._load_glossary`). `# Nível N` troca o nível; `## Título` abre verbete; `key` = slug (minúsculas, sem acento, pontuação → `-`): "IV ATM 30d" → `iv-atm-30d`, "Preço (spot / fechamento)" → `preco-spot-fechamento`.
  3. **Desvio declarado da spec §9:** nesta versão **não** há injeção de exemplo da sessão no `body_md` — o texto é servido como está. A ficha com "o número de hoje" é trabalho da F5-B (sheet de métrica).
  4. O verbete "Taxa histórica do sinal" está marcado "**Fora do produto**" no próprio `glossario.md` (ADR-023/024): o parser o **omite** para que o glossário servido seja descritivo. Os 30 restantes são servidos. As palavras "caro/barato" que aparecem nos verbetes de IV rank/z-score/estrutura a termo são **prosa explicativa pré-existente** ("caro para este papel, no seu próprio histórico", com o aviso "contexto, não gatilho"), não classe de sinal nem recomendação — o arquivo não foi editado.
- **Por quê:** uma fonte só evita divergência entre doc e API; parser puro é testável no texto; omitir o verbete fora do produto cumpre o ADR-024 sem reescrever o documento do dono.
- **Consequência:** `/api/glossary` devolve `{key, title, level}` (30 verbetes, ≤ 60); `/api/glossary/{key}` devolve `{key, title, level, body_md}`; chave desconhecida é `404 not_found`. Nenhum número da sessão entra no texto.
- **Revisar em:** F5-B (sheet de métrica com o número do dia) e se o dono quiser o verbete do portão no app (decisão dele, ADR-023).

## ADR-032 — F5: cliente tipado escrito à mão, `fetch` único

- **Data:** 2026-11-23 · **Status:** provisória (orquestrador) · **Custo de reverter:** baixo (um arquivo)
- **Contexto:** os endpoints do F3/F4 devolvem `dict` genérico, então `/api/openapi.json` não traz schema útil para geração de tipos. A stack precisa de um contrato único entre API e UI.
- **Decisão:** `frontend/src/lib/api.ts` é o **único** arquivo com `fetch` e com os tipos (`Envelope<T>`, `Health`, `Screen*`, `Underlying`/`Term`/`Smile`/`History`, `ChainRow`, `SeriesDetail`, `Events`, `Structure`, `TickerRow`, `Glossary*`), escritos à mão a partir do OpenAPI + contratos onda-i/onda-j. `apiGet` trata status HTTP, envelope de erro (`ApiError` com `code`/`message`/`hint`) e avisa quando `meta.truncated`; `apiPost` existe apenas para o cálculo de `POST /api/structure`. `VITE_API_BASE` default vazio (mesma origem). TanStack Query no `__root`: 60 s para leitura de sessão, 1 h para glossário/tickers.
- **Por quê:** gerador não agregaria valor sobre `dict`; um único arquivo torna divergência de contrato impossível de esconder e é onde o grep de `fetch` procura.
- **Consequência:** nenhum componente chama `fetch` direto; erros `not_found` e de rede viram estados explícitos na tela (F5.3).
- **Revisar em:** se as rotas passarem a declarar modelos Pydantic com schema (aí gerar a partir do OpenAPI passa a valer a pena).

## ADR-033 — F5-PreB: ficha do Aprender em accordion independente

- **Data:** 2026-11-23 · **Status:** ratificada pelo dono · **Custo de reverter:** baixo (um arquivo)
- **Contexto:** a ficha do verbete abria no fim da página. O prompt da sessão recomendava bottom sheet, mas aceitava expandir/colapsar inline mediante ADR curto; o dono preferiu **accordion independente** — vários verbetes abertos ao mesmo tempo para comparar definições.
- **Decisão:** `aprender.tsx` guarda um `Set<string>` de chaves abertas. Cada verbete é um `EntryRow` com `aria-expanded`/`aria-controls` e um painel `role="region"` sempre no DOM, com `hidden={!open}`; o corpo vem de `EntryDetail`, uma `useQuery` por chave com `staleTime` 1 h. Sem backdrop, sem trava de `overflow`, sem focus trap e sem `position: fixed`. O componente `Sheet` da primeira passagem foi removido.
- **Por quê:** para consulta, comparar duas fichas vale mais que a garantia de `scrollY` inalterado do sheet; o accordion também remove toda a maquinaria de modal — menos código e nenhum estado de foco para manter.
- **Consequência:** a explicação aparece logo abaixo do verbete tocado; se ele estiver no fim da viewport, é preciso um pequeno scroll para ler. Vários painéis abertos deixam a lista longa — custo aceito da comparação. `plain()` continua removendo `>`, `---` e links (B2 fechada).
- **Revisar em:** F5-B, se o padrão de leitura pedir um sheet de métrica com o número do dia.

## ADR-034 — 2026-10-26 é choque real de mercado, não artefato de dado (corrige ADR-021/023)

- **Data:** 2026-11-23 · **Status:** provisória (reavaliação pedida pelo dono) · **Custo de reverter:** baixo (texto; nenhuma fórmula, `model_version` segue 2)
- **Contexto:** desde 22/11 o projeto tratava 26/10/2026 como "data-armadilha" ("split sem ajuste", "RV inflada", "346 `premium_cheap` são artefato"). A reavaliação de 23/11 (só leitura do lake, DuckDB por fora do pipeline) mostrou que o diagnóstico estava errado.
- **Evidência:**
  1. **Índice:** `IBOV11` 204.890 → **175.492 pts (−14,35%)** em 26/10, e segue caindo (167.938 em 30/10).
  2. **Câmbio:** BDRs e ETF de S&P sobem no mesmo dia — AAPL34 +6,3%, MSFT34 +9,1%, IVVB11 +9,0%; VALE3 (exportadora) +4,0%.
  3. **Amplitude e unicidade:** 45 papéis abriram a ≤ 0,60 do fechamento anterior (2º pior dia em 2024–2026: 8) e 163 fecharam a ≤ 0,80, entre 1.328 papéis contíguos com fechamento anterior > R$ 1.
  4. **Persistência:** B3SA3 23,65 → 15,07 (fech.) → 14,28 em 19/11; CYRE3, XPBR31, RENT3 seguem em 0,52–0,55 do preço de 23/10. Nada reverte.
  5. **As opções concordam com o preço novo:** o forward por paridade de 26/10 acompanha o spot novo (B3SA3 15,21 × 15,07; MGLU3 5,36 × 5,30; BBAS3 17,37 × 17,47). Desdobramento sem ajuste deixaria as opções precificando o spot antigo. "0 strikes mudaram, 0 eventos B2" é prova de que **não houve evento corporativo**, não de artefato.
  6. **Event crush da IV:** IV ATM 30d antes → depois: B3SA3 73 → 40, BBAS3 59 → 38–44, BOVA11 38 → 22, PETR4 42 → 31 — evento agendado precificado e realizado. A data é a segunda-feira seguinte ao 2º turno da eleição (25/10/2026); a causa não está no dado.
- **O que continua suspeito (não verificável no COTAHIST):** 23 papéis abriram a exatamente 0,49–0,51 do fechamento anterior, 12 deles com abertura = mínima do dia — padrão de leilão de abertura no limite de preço. O Yang-Zhang usa a abertura e amplia a RV: rv20 de 19/11 YZ × fechamento-a-fechamento — B3SA3 261 × 169, BBAS3 159 × 131, PETR4 61 × 44, BOVA11 69 × 60, CYRE3 273 × 224 (MGLU3 é o inverso: 277 × 344). A RV é alta **de verdade** em qualquer estimador.
- **Impacto no painel:** de 26/10 a 19/11, ~86 de ~170 papéis com métrica têm rv20 > 100%; mediana rv20 ≈ 100 (set/2026: 36) e mediana VRP ≈ −31 pts (set/2026: +5). O número é correto pela definição, mas dominado por um dia e sem aviso na tela. Pelos cálculos: a rv20 normaliza 1–2 pregões depois do próximo `prepare`; a rv30 (VRP) no início de dezembro; a rv60 no fim de janeiro; rank/z de 252 sessões carregam o evento por um ano.
- **Impacto no portão (recálculo por fora com `load_observations`, mesmas 6.773 observações):** os 346 `premium_cheap` posteriores a 26/10 **nunca entraram no backtest** (sem 20 sessões futuras). O que entrou foram observações **anteriores** cuja RV futura contém o choque:

  | Recorte | `premium_rich` | `premium_cheap` | `none` edge>0 |
  |---|---|---|---|
  | todas (oficial) | n 617 · 42,3% · med −1,41 | n 498 · 49,8% | 43,7% |
  | janela contém 26/10 | n 142 · **0%** · med **−68,29** | n 33 · 100% | n 132 · 0% |
  | janela não contém | n 475 · **54,9%** · med **+0,84** | n 465 · 46,2% | 45,0% |

- **Decisão:**
  1. 26/10/2026 passa a ser registrado como **choque real de mercado**. Os textos de "artefato"/"split sem ajuste" foram corrigidos (AGENTS, ADR-021/023, docstrings, título do recorte no `b3o backtest`) e os relatórios históricos ganharam nota de correção, sem reescrever o que diziam.
  2. **O veredito do portão não muda:** pelos critérios pré-registrados, no período completo, as duas classes NÃO PASSAM, e a Opção 1 (ADR-023) segue ratificada. **A leitura muda:** a margem negativa do `premium_rich` vem inteira de 142 observações (23% da classe) em que a IV estava alta porque o evento estava agendado. Fora delas o `rich` fica +9,9 p.p. acima da base, mas também não passaria (54,9% < 55%; mediana 0,84 < 1). "Momentum de vol" e "valor negativo" não são leituras sustentadas. O `premium_cheap` falha de verdade (−8,8 p.p. fora do choque).
  3. Excluir o choque depois de ver o resultado é escolher dado (P-L). "`premium_rich` sem evento binário agendado na janela" é **hipótese nova**: só vale pré-registrada e fora da amostra.
  4. Os nomes `TRAP_DATE`/`trap_in_rv_window` ficam por compatibilidade (renomear não muda número).
  5. **Descasamento de prazo do portão (registrado na mesma revisão):** `iv_atm_30d` é IV de **30 dias úteis** (`T = 30/252`, ≈ 6 semanas), e o desfecho é `rv_fwd20`, a RV dos **20** pregões seguintes. O `glossario.md` (verbete VRP) diz que tenores diferentes "fabricam VRP". Como estava pré-registrado (spec §6.13), o veredito **não** se refaz nesta amostra (P-L). **Regra para qualquer estudo futuro:** IV de N pregões contra a RV dos N pregões seguintes, com N fixado antes de olhar o dado.
- **Consequência:** nenhuma fórmula, tabela ou `model_version` muda. Fica pendente, fora deste ADR: flag por linha no `screen` quando a janela de RV contém um dia extremo de mercado (e talvez a RV fechamento-a-fechamento ao lado), além de cobrir `rich`/`none` no recorte do relatório do backtest (hoje só `cheap`).
- **Revisar em:** F5-B (como o painel mostra RV/VRP nas próximas sessões) e em qualquer estudo novo de sinal.

## ADR-035 — Aviso de pregão dominante na RV (`rv_shock_day`), calculado na leitura

- **Data:** 2026-11-23 · **Status:** provisória (pedido do dono depois do ADR-034) · **Custo de reverter:** baixo (leitura; nada gravado, `model_version` segue 2)
- **Contexto:** depois de 26/10/2026, a RV/VRP/z de metade do universo contam a história de um único pregão (ADR-034), e a tela não dizia isso.
- **Decisão:**
  1. Função pura `derive.realized.dominant_day(ohlc, window, ex_div_dates)`: retornos **fechamento-a-fechamento** das últimas 30 barras (a janela da `rv30`, que entra no VRP), com o mesmo fator de escala e a mesma exclusão de data ex da RV; devolve o pregão de maior `r²` e a fração `r²_max / Σr²` (momento não centrado). Menos de 10 retornos → `None`.
  2. `QueryService.screen_bundle` preenche `rv_shock_date` e `rv_shock_share_pct` (0–100) em toda linha `ok` (uma leitura só para o universo) e acrescenta a flag `rv_shock_day` quando a fração é ≥ **50%**. Com retornos normais i.i.d. em 29 pregões, o maior dia responde em média por ~20%; metade é cauda extrema. É **aviso de leitura**, não limiar de sinal, e não exclui nada.
  3. O resumo do `screen` ganha `rv_shock_count` e `rv_shock_dates`. Como o `underlying` reusa a linha do `screen`, o Ticker herda os campos. Há verbete novo ("Pregão dominante na RV"). Na tela: aviso no Hoje, marca `†` no prêmio de volatilidade, métrica + aviso no Ticker. A fração é exibida com **piso** (49,97% não pode aparecer como "50%" sem a flag).
  4. Fechamento-a-fechamento, e não Yang-Zhang: a pergunta é "um movimento de preço domina a janela?". A decomposição abertura/intradiário do Yang-Zhang é outra questão (aberturas a ~0,50 em 26/10, ADR-034).
- **Medido em 19/11/2026:** **116 de 163** papéis `ok` sinalizados (115 por 26/10, 1 por 20/10). B3SA3 91,8% · BBAS3 88,0% · BOVA11 86,6% · MGLU3 82,3% · ITUB4 74,7% sinalizados; ABEV3 49,97% e PETR4 31,9% não; VALE3 14,8% (16/11). `screen_bundle` inteiro em 1,7 s.
- **Consequência:** nenhuma métrica gravada muda. Até a próxima rodada do `prepare`, o aviso fica em quase todo o universo; ele some sozinho quando 26/10 sai da janela de 30 pregões (início de dezembro).
- **Revisar em:** F5-C (se o aviso deve aparecer também na Série) e se o dono quiser estender o aviso à rv60/rank/z (janelas mais longas, que carregam o choque por mais tempo).

## ADR-036 — `prepare-watch` padrão, horário, com métricas incrementais

- **Data:** 23/11/2026 · **Decidido pelo dono** (pedido explícito).
- **Antes:** `prepare-watch` no profile `watch` (não subia no `compose up`), uma vez por dia às 21:30; e o `prepare` **não** calculava `metrics_*` — com o watch ligado, o bruto atualizava e a tela ficava no dia anterior até alguém rodar `b3o metrics --date`.
- **Decisão:**
  1. `prepare-watch` sem profile, `restart: unless-stopped`; roda **ao iniciar** e a cada `--interval` minutos (default 60, env `B3_WATCH_INTERVAL_MIN`). Um ciclo que levanta exceção é registrado e o laço segue.
  2. No watch, o ciclo **pula** quando: COTAHIST 304 **e** `ingest_state.sha256` = sha do zip **e** mesmo `model_version` **e** o último prepare sem falha de fonte **e** `metrics_underlying` cobre `last_session`. Medido: 0,2–0,3 s por ciclo pulado. Fonte com falha (rates/OI/earnings) força o ciclo completo na hora seguinte — é assim que o watch re-tenta.
  3. O `prepare` ganhou o estágio `metrics` (depois da liquidez): calcula **só** as sessões do lake posteriores à última em `metrics_underlying`, buscando a curva DI de sessões de recuperação que não a têm. Recusa com falha explícita (`metrics_missing` / `model_version_mismatch`) quando o histórico está vazio ou é de outro modelo — o backfill inteiro (~26 min) continua sendo `b3o metrics --backfill` manual, para não misturar modelos no rank (regra 6).
- **Medido no primeiro ciclo completo (23/11/2026):** COTAHIST de 2026 já trazia a sessão **2026-11-23** às 21:35 (o arquivo saiu com `Last-Modified` 00:23 GMT do dia 24) — "D-1" não é regra rígida; prepare inteiro 320 s, RSS pico 1.762 MiB, estágio metrics 48 s para 1 sessão (167 papéis, 11.014 séries).
- **Consequências:** o watch reconstrói o ano a cada arquivo novo (~5 min, ~1,7 GiB) com o `app` ligado na mesma máquina de 3,7 GiB. O `prepare` de um ano fechado (`--year`) não recalcula métricas daquele ano. O carimbo `b3o-session` do `index.html` é lido no lifespan do `app` (a tela usa `/api/health`, que é ao vivo).
- **Revisar em:** 30 dias de uso (RAM do watch concorrente com o app; intervalo).

## ADR-037 — F5-D: Serve 8443 only-tailnet, allowlist por ambiente e ícone iOS

- **Data:** 2026-11-24 · **Status:** ratificada pelo dono (Android confirmado) · **Custo de reverter:** baixo (exposição + metadados; nenhuma fórmula)
- **Contexto:** a spec D12 escolhe `tailscale serve`, mas este host já usa a 443 com **Funnel público** para outro projeto (`realestate` → `127.0.0.1:8080`). O b3o tem leitura sem autenticação; publicá-lo por Funnel ampliaria o painel para a internet. Além disso, o `TransportSecuritySettings` do MCP só aceitava hosts locais e devolvia **421** para o hostname `*.ts.net`. O PWA também ainda não tinha o `apple-touch-icon` de 180 px exigido pelo iOS.
- **Decisão:**
  1. O b3o roda em `127.0.0.1:8083` e é publicado por `tailscale serve --bg --https=8443 http://127.0.0.1:8083`. A Serve **coexiste** com o Funnel: `tailscale serve status` mostra `8443 (tailnet only)` para o b3o e `443 (Funnel on)` para o `realestate`. A 443 não foi alterada; **nunca** usar Funnel para o b3o sem decisão do dono.
  2. `B3_MCP_ALLOWED_HOSTS` é uma lista separada por vírgulas, lida por `b3o.api.app._mcp_allowed_hosts()` e acrescentada aos defaults locais. O `.env` fica fora do git e informa o hostname com e sem porta (`host,host:*`). Nenhum hostname de deploy fica no código; Host não listado continua **421**.
  3. O gerador de ícones passa a produzir `apple-touch-icon.png` **180×180** com o mesmo desenho grafite + agulha. `index.html` ganha `apple-touch-icon` e os três metadados iOS; o ícone entra no precache. Manifest, nome e `standalone` não mudam.
  4. Nenhuma métrica, fórmula, SQL, journal ou `model_version` muda. O app segue `model_version 2`.
- **Evidência (24/11/2026):** HTTPS 200; `/api/health` local = HTTPS (`2026-11-23`, `age_sessions 0`, `model_version 2`); `mcp_smoke` **53/53** local e HTTPS; `structure_smoke` **93/93** local e HTTPS; Host `nao-autorizado.example` → **421 Invalid Host header**; PWA com 1 registro, controller ativo, precache + `b3o-api` (6 entradas, sonda fora do cache); offline com servidor parado serviu o shell, banner “sem rede — última sessão 2026-11-23” e carimbo “offline” sem `D-n`; retorno online com 289 linhas e zero erros de console. `data/` inalterado (202 arquivos, SHA-256/tamanho/mtime).
- **Consequência:** a URL pronta é `https://desktop-vlp5tqv.tail5745cb.ts.net:8443`. O dono confirmou em 24/11/2026 que o **Android está instalado e funcionando**, incluindo o ciclo offline/online. As capturas não foram anexadas, mas a confirmação explícita fecha F5.9 e a aceitação de celular no `PLAN.md`.
- **Revisar em:** confirmação da instalação no celular ou mudança do hostname/porta da tailnet.

## ADR-038 — F5-E: liquidez como descoberta descritiva, não ranking preditivo

- **Data:** 2026-11-24 · **Status:** provisória · **Custo de reverter:** baixo (uma consulta read-only e um bloco de UI)
- **Contexto:** a busca de papel exige saber o ticker. O dono pediu um ponto de partida por liquidez no Hoje, sem transformar volume/IV/VRP em recomendação. O `screen` já tinha contagens de séries líquidas; o top de séries por volume ainda não existava.
- **Decisão:**
  1. O bloco **Liquidez** fica abaixo do aviso de pregão dominante e acima de “Com opções”. A aba **Papéis** ordena no cliente `n_liquid_series DESC`, `n_expiries DESC`, `n_series DESC`, `ticker ASC`, mostra top 10 e “Ver todos”. É apresentação dos campos do `screen`, não número novo.
  2. `GET /api/liquid?date=&limit=` nasce na mesma `QueryService`, read-only. Filtra `metrics_series.is_liquid` gravado (§6.9), resolve opção→papel pelo ISIN e ordena por volume financeiro do COTAHIST DESC, `trades DESC`, `ticker ASC`. `volume_brl = volume / 100` (FATCOT já aplicado); bid/ask/spread seguem o `chain`.
  3. A aba **Séries mais negociadas** consome o top 20 do endpoint e abre `/s/{ticker}/{maturity}`. `meta.total` é contado antes do corte; `limit` usa o teto canônico de 60 e o envelope mantém o teto de 200 KB.
  4. Nenhuma métrica é gravada ou recalculada; `model_version` continua 2. Não há tool MCP nova nesta onda, preservando o catálogo/smoke de sete tools.
- **Por quê:** líquidez responde “o que consigo negociar hoje?” sem alegar retorno ou vantagem. A ordem é declarada, reversível e auditável; o Python continua dono do número.
- **Consequência:** a nota “ordenado por liquidez; não é recomendação” permanece visível. Nada de “melhores”, score, estrela, caro/barato ou acerto. Carteira/Diário continuam placeholders até F6.
- **Revisar em:** se o dono quiser filtro por papel, contrato MCP ou ordenação por volume também no card de papéis.

## ADR-039 — F5-F: escala tipográfica desktop sem tocar no mobile

- **Data:** 2026-11-24 · **Status:** superada pela ADR-043 · **Custo de reverter:** baixo (dois blocos CSS + documento de direção)
- **Contexto:** a base de 13,5 px e os rótulos de 11 px funcionam no celular, mas ficam pequenos no monitor a 100%. O dono pediu legibilidade desktop sem zoom e sem alterar o mobile.
- **Decisão:**
  1. Um bloco `@media (min-width: 1024px)` em `styles.css`/`styles-view.css` eleva a base para **15 px**, título de tela para **32 px**, rótulos/notas para **12 px**, valores para **18/24 px**, tabela para **12,5/12 px**, cadeia para **13/12 px**, eixos/legendas para **11 px**, busca para **12,5/11,5 px**, builder para **12/13 px** e modal para **15/20 px**.
  2. O corte < 1024 px não recebe nenhuma sobrescrita: tamanhos e alvos de toque continuam idênticos. Não usar `zoom`, `transform: scale` ou aumento de espaço vertical.
  3. `docs/direcao.md` registra a escala desktop. Cor, matiz, raio, espaçamento e hierarquia não mudam.
- **Por quê:** melhorar leitura sem redesenhar; media query mantém o comportamento mobile já validado e torna a exceção auditável.
- **Consequência:** nenhum componente, número, dado ou `model_version` muda. A validação compara computed styles antes/depois em 412, 1280 e 1440 px.
- **Revisar em:** após uma semana de uso no monitor ou se o dono preferir outra densidade.

## ADR-043 — F8: revisão aprovada da tipografia desktop com tokens Tailwind

- **Data:** 2026-11-24 · **Status:** ratificada pelo dono · **Custo de reverter:** baixo (tokens + utilitários)
- **Contexto:** o dono considerou o texto desktop pequeno. A escala do ADR-039 (base 15, título 32, rótulos 12) fica substituída; o mobile < 1024 px não muda. A revisão também vale para os primitives shadcn da F8, que não podem herdar `text-sm`.
- **Decisão:**
  1. `@theme` centraliza a nova escala em px: corpo 16, título de tela 36, título de bloco 16, lede 15, rótulos/notas 13, métrica 20, herói 28, tabela 14/13, cadeia/detalhe 14/13, gráfico 12, busca 14/12,5, builder 13/14 e modal 16/22.
  2. Line-heights: corpo 1,5; tabela 1,4; títulos 1,15. A aplicação desktop usa exclusivamente utilitários `lg:`; os blocos `@media (min-width: 1024px)` de tipografia foram removidos.
  3. Button, Input, Select, Table e Dialog gerados pelo shadcn usam os mesmos tokens. Sans de sistema e mono tabular continuam; hierarquia por tamanho e cor, sem peso extra, caixa alta ou tracking.
  4. Nenhum `zoom`, `transform: scale` ou tamanho arbitrário espalhado. A validação é feita em 1280, 1440 e 412 px, incluindo overflow, truncamento, console e alvos de toque.
- **Por quê:** atende ao pedido explícito de legibilidade a 100% sem redesenhar a interface e move a escala para um contrato único, reutilizável pela F8.
- **Consequência:** o ADR-039 fica superado. `docs/direcao.md` registra a revisão aprovada e o relatório da F8 guarda a evidência. Nenhum número, fórmula, dado ou `model_version` muda.
- **Revisar em:** depois de uma semana de uso no monitor ou se o dono pedir outra densidade.

## ADR-040 — F6: journal append-only por eventos e escritas com token

- **Data:** 2026-11-24 · **Status:** **superada pela ADR-041** · **Custo de reverter:** médio (schema novo e API de escrita)
- **Contexto:** a spec §14 pede `position`/`position_leg`, abertura/fechamento só pela app e marcação EOD. O projeto já declarava `journal.db` append-only; atualizar `status`/`closed_at` na linha da posição contradiria essa regra.
- **Decisão:**
  1. `journal.py` é o único repositório SQLite. Abertura insere `position`, `position_leg`, evento `open` e `prediction` opcional; fechamento insere somente evento `close`. Status e sessão de fechamento são derivados do evento. Triggers bloqueiam `UPDATE`/`DELETE` nas quatro tabelas.
  2. GET não cria `journal.db`; a primeira escrita cria schema/triggers. Caminho default `<B3_DATA_DIR>/journal.db`, override `B3_JOURNAL_PATH`.
  3. `POST /api/positions*` exige `X-App-Token` igual a `APP_TOKEN`; sem token no servidor → `503 app_token_missing`, token inválido → `401 unauthorized`. Leitura fica liberada na tailnet. O token do PWA fica no `localStorage`.
  4. `kind` é derivado das pernas; `quantity` é em ações. Preço/strike/IV/delta de entrada vêm do lake. O journal consolida opção como `call`/`put`; a API devolve `type: option` + `option_side`.
  5. Marcação, P&L, gregas, breakevens e flags são calculados na `QueryService`/funções puras. Marca ou grega ausente → agregado `null` + flag, nunca parcial silencioso. `model_version` continua 2.
  6. A tool MCP `positions` é read-only e compartilha o payload HTTP. Não existe tool de escrita.
- **Limite declarado:** ex-dividendo futuro não existe no COTAHIST; `event_in_window` cobre vencimento/evento conhecido na janela, sem inventar data futura.
- **Consequência:** F6 persiste exposição sem quebrar a regra de uma camada de consulta do lake. A reconciliação com duas posições reais da corretora continua sendo prova do dono.
- **Revisar em:** F7 (avaliação da previsão sem UPDATE, provavelmente evento de avaliação) ou se o dono quiser múltiplos usuários.

## ADR-041 — F6: journal PostgreSQL, Alembic e volume nomeado

- **Data:** 2026-11-24 · **Status:** provisória · **Custo de reverter:** médio (dump/restore e API de escrita)
- **Contexto:** o SQLite do journal recebeu `disk I/O error` no bind virtiofs do Windows. O arquivo oficial `data/journal.db` nunca existiu; portanto não há dado para migrar. O lago analítico continua Parquet/DuckDB e não muda.
- **Decisão:**
  1. O journal operacional usa **PostgreSQL + SQLAlchemy Core + Alembic**. `backend/migrations/versions/0001_f6_initial.py` é a única migration inicial; nenhuma rota cria schema. `app` e `dev` executam `alembic upgrade head` antes do uvicorn.
  2. `DATABASE_URL` aponta para o serviço `db` em `app`, `dev` e `test`. O banco não publica porta no host. O volume nomeado `b3o-postgres` é montado em `/var/lib/postgresql` (layout PostgreSQL 18+); `data/` continua só com lake/raw.
  3. Abertura e fechamento continuam transacionais e append-only, com `SELECT ... FOR UPDATE` e índices únicos parciais para no máximo um evento `open` e um `close` por posição. Triggers bloqueiam UPDATE/DELETE/TRUNCATE nas quatro tabelas.
  4. GET não cria schema nem posição. Sem schema migrado, a falha é `storage_error` explícito; com schema vazio, devolve lista vazia e totais zero. Escritas seguem exigindo `X-App-Token`.
  5. UI e smokes usam schema PostgreSQL temporário com `search_path` próprio (ou banco descartável), nunca o schema oficial. O script de smoke não aceita caminho de arquivo e não apaga banco/volume.
  6. Volume não é backup. A cópia operacional é `pg_dump` do banco (formato custom, sem senha no comando versionado) com retenção fora do volume e restauração periódica em banco descartável. A automação/retenção do dump fica pendente até o dono escolher o destino; não se afirma backup executado.
- **Por quê:** PostgreSQL no volume nomeado evita o bind virtiofs, oferece lock transacional real para o fechamento concorrente e mantém a separação entre dado operacional e lago analítico.
- **Consequência:** `APP_TOKEN` continua só no `.env`; logs e documentação nunca o revelam. A migration inicial não precisa migrar SQLite. `model_version` continua inalterado.
- **Revisar em:** F7 (avaliação de previsão por evento) ou quando houver destino de backup/múltiplos usuários.

## ADR-042 — F7: decisão e avaliação como eventos append-only

- **Data:** 2026-11-24 · **Status:** provisória · **Custo de reverter:** médio (schema/API novos)
- **Contexto:** a spec §6.12 pede previsão avaliável, e o prompt F7 exige decisões sem posição,
  avaliação no lote EOD e estado corrente sem UPDATE. A migration F6 já é imutável.
- **Decisão:**
  1. `decision` é o próprio evento append-only da decisão. `prediction` continua semente imutável;
     `status`/`observed` legados ficam `pending`/`null`. `prediction_evaluation` guarda observado,
     status e motivo de `void`; a leitura deriva o evento mais recente.
  2. `0002_f7_journal` acrescenta `decision_id` e o `CHECK` de um pai; `0003_f7_prediction_parent`
     torna `prediction.position_id` nullable. A abertura F6 espelha uma decisão `entry` vinculada,
     pois a previsão de posição também entra na calibração.
  3. Idempotência da decisão por `client_decision_id` + fingerprint; avaliação por índice único
     `(prediction_id, session)` e lock. Triggers bloqueiam UPDATE/DELETE/TRUNCATE nas seis tabelas.
  4. O lote usa o primeiro pregão real posterior a `evaluate_on`. Métrica ausente vira `void` com
     motivo; fechar posição não cancela previsão. `prepare-watch` aplica Alembic e roda o lote após
     métricas verdes.
  5. Calibração pessoal agrega por métrica + horizonte, com `n`, `hit`, `miss`, `void` e taxa sobre
     `hit + miss`. Não compara com o F1b, não ranqueia e não recomenda (ADR-023/024).
- **Por quê:** preserva a história auditável e permite medir as afirmações do dono sem fabricar
  desfecho ou transformar o produto em sinal.
- **Consequência:** a aceitação F7 depende de uso real (30 dias e ≥ 10 previsões avaliadas), não da
  implementação. `model_version` continua 2.
- **Revisar em:** aceitação de uso real ou se o dono quiser múltiplos usuários.

## ADR-044 — F8: migração shadcn/Tailwind em paralelo com F6/F7 pendentes

- **Data:** 2026-11-24 · **Status:** ratificada pelo dono · **Custo de reverter:** médio (migração de UI)
- **Contexto:** o PLAN declara F8 dependente de F6 e F7, mas as duas fases estão tecnicamente
  implementadas e aguardam aceitação de uso real: duas reconciliações de corretora e 30 sessões de
  diário com 10 previsões. Em 24/11/2026 o dono autorizou explicitamente migrar F8.2/F8.3 em paralelo,
  sem esperar essa aceitação longitudinal.
- **Decisão:**
  1. F8.2/F8.3 podem avançar agora como exceção à dependência do PLAN. F6 e F7 continuam
     **implementação validada, aceitação pendente**; nenhum checkbox de uso é marcado.
  2. A migração é somente de interface: primitives shadcn + utilitários Tailwind. Contratos HTTP,
     cálculos, dados, gráficos SVG, `model_version`, journal e regras offline/`NetworkOnly` não mudam.
  3. Preservar acessibilidade e comportamento já validado: foco, trap, Escape, retorno de foco,
     estados busy, 401/409, rascunho, alvos ≥ 44 px e viewports 412/1280/1440.
  4. Entregar em pacotes separados (F8.2 e F8.3), com regressão técnica e navegador real antes de
     marcar qualquer aceitação F8.
- **Por quê:** separa a dívida visual da aceitação longitudinal do produto e permite melhorar a
  interface sem fabricar uso real nem reabrir o sinal F1b.
- **Consequência:** o PLAN registra a exceção e o progresso real. F8 só será aceita quando seus quatro
  critérios próprios passarem; F6/F7 permanecem independentes.
- **Revisar em:** conclusão de F8.2/F8.3 ou quando F6/F7 forem aceitas.

## ADR-007 — Validador: fatal é estrutural; linha torta se pula e se conta

- **Data:** 2026-11-22 · **Status:** provisória · **Custo de reverter:** baixo
- **Contexto:** o contrato da Onda A definia `ok = … and not bad_width and not bad_numeric`, mas o COTAHIST 2026 real tem exatamente **1 linha de 246 bytes** (linha 2.087.780; `FR0T5L`, mercado 017: `corp_name` em UTF-8 — `Â` — dentro do arquivo latin-1; todos os offsets posteriores deslocados). Com o `ok` estrito, o `prepare` reprovaria para sempre num arquivo perfeitamente utilizável.
- **Decisão:** `ok` = só condições estruturais (header, contagem do trailer, zip ilegível). Problema por linha (largura/numérico/data/tipreg) é **registrado, pulado na ingestão e contado**; contagens expostas no `health`. A linha de mercado não-ingerido (017) nem chega ao lake por causa do filtro de mercados. `errors` fica reservado a I/O/zip.
  **Precisão medida na revisão do F0 (22/11/2026):** o `parse_line` **levanta** em linha de largura errada, e `iter_cotahist` só a ignora quando o campo de mercado (`l[24:27]`) está inteiro e fora dos mercados ingeridos — é o caso da linha de 246 bytes (017). Uma linha torta em mercado **ingerido** aborta o `prepare` de propósito: é corrupção de cotação real, não linha de mercado descartado. Não há, em 2024/2025/2026, nenhuma linha assim (as três passadas do validador dão 1 / 0 / 0 de largura).
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

## ADR-045 — Escritas abertas: `X-App-Token` opcional

- **Data:** 2026-11-24 · **Status:** ratificada pelo dono · **Custo de reverter:** baixo (descomentar `APP_TOKEN` no `.env` e recriar o `app`)
- **Contexto:** o pedido de token ("Liberar escritas") era atrito sem ganho no arranjo atual: o `app` só escuta em `127.0.0.1` e a tailnet expõe a 8443 **só para a tailnet** (`tailscale serve`), cujo único usuário é o dono. O dono decidiu controlar o acesso pela rede por enquanto.
- **Decisão:**
  1. `APP_TOKEN` vazio ou ausente = escritas (`POST /api/journal`, `/api/positions*`) **abertas**. Com `APP_TOKEN` definido, o comportamento da ADR-040 volta inteiro (`401 unauthorized` sem o cabeçalho certo). O `503 app_token_missing` deixa de existir.
  2. `GET /api/health` publica `writes_require_token` (só no HTTP; não é dado do lake, não entra na tool MCP). O PWA esconde o `TokenGate` e o "Trocar token" quando é `false`; se o servidor responder 401, o pedido de token volta.
  3. A porta padrão do host no compose passa de 8080 para **8083**: a 8080 deste host é o alvo do **Funnel público** (443) do `realestate`. Sem o `.env`, o b3o subiria na 8080 e, com o outro projeto parado, ficaria na internet com escrita aberta.
- **Risco aceito:** qualquer coisa que alcance a porta do `app` (aparelho na tailnet, processo local) grava no journal append-only, sem desfazer. Nunca apontar `tailscale funnel` para o b3o enquanto esta ADR valer.
- **Revisar em:** novo usuário/aparelho na tailnet, deploy público ou identidade pelo cabeçalho `Tailscale-User-Login`.

## ADR-046 — F8: o que fica em CSS global depois da migração Tailwind

- **Data:** 2026-11-24 · **Status:** ratificada pelo dono em 25/11/2026, após avaliação manual da app 8092 · **Custo de reverter:** baixo (CSS de exceção isolado)
- **Contexto:** o critério F8 pedia "CSS global só para tokens e base". Depois da F8.3, `styles-view.css` ainda tinha 1.097 linhas (cartões de posição, formulários, Diário, TokenGate e utilitários compartilhados `.note`/`.quiet`/`.num`/`.block-*`). A migração final levou tudo o que é layout, espaço, tipografia e cor de componente para utilitários; uma parte não é razoável em utilitário sem virar CSS escrito dentro de `className`.
- **Decisão:**
  1. `styles.css` (110 linhas) = `@import "tailwindcss"`, tokens `@theme`, variáveis da paleta e `@layer base` só com seletores de elemento (`*`, `html/body`, `a`, `button`, `main`). Nenhuma classe de componente.
  2. `styles-view.css` (390 linhas, `@layer components`) guarda **somente** as exceções, cada uma com motivo:
     - gráficos SVG `.chart*` — `stroke`, `stroke-dasharray`, `vector-effect: non-scaling-stroke`, `fill`, tooltip ancorado com media query própria;
     - régua/agulha `.ruler*` — o triângulo da agulha é `::after` com bordas;
     - tabelas `.table-wrap`, `.table-wrap--scroll`, `.screen-table` — primeira coluna e cabeçalho sticky sobre `border-collapse: separate`, com `z-index` cruzado (cabeçalho 2, canto 4, corpo 1);
     - `.liquidity-table` — hover e foco em `tbody tr`/`td` descendentes;
     - `.position-flags` — separador `" · "` em `::after` entre irmãos.
  3. Classes compartilhadas viraram constantes em `frontend/src/lib/classes.ts`, com o valor **efetivo** do legado (as duas folhas somadas, a última vencendo); contexto que zera margem usa a variante `*Base`. Variantes `lg:` continuam no elemento.
  4. Dependência implícita por seletor (`[.position-card_&]:mb-[12px]` no `MetricGrid`) virou prop explícita (`inCard`).
- **Por quê:** essas regras dependem de seletor estrutural, pseudo-elemento ou propriedade SVG; escrevê-las como `[&_tbody_tr:hover_td]:…` esconderia o mesmo CSS num atributo mais difícil de ler e revisar.
- **Consequência:** o critério F8 passa a ler "Tailwind é a camada principal de layout/espaçamento/tipografia; CSS global só para tokens, base e as exceções da ADR-046". Classe nova em `styles-view.css` precisa caber numa dessas categorias. A equivalência foi sustentada pela regressão técnica da F8.3 e pela avaliação manual do dono na 8092, aprovada em 25/11/2026; o fecho não exigiu nem executou nova comparação pixel a pixel. A referência canônica da migração continua sendo `57f5f84` (relatório F8).
- **Revisar em:** se o Tailwind ganhar utilitário para `vector-effect`/sticky cruzado, ou se um novo gráfico entrar.

## ADR-047 — F0–F8 construídos; F6/F7 aceitam só no uso real

- **Data:** 2026-11-24 · **Status:** ratificada pelo dono e reconciliada com a aceitação da F8 em 25/11/2026 · **Custo de reverter:** nenhum (é estado, não mudança)
- **Contexto:** F0–F5 e F8 estão aceitas; F6 e F7 têm implementação validada (smokes, navegador, migrations, lote de avaliação), mas os critérios restantes exigem uso: P&L conferido com a corretora em 2 posições reais (F6) e 30 sessões de pregão consecutivas com decisão real + ≥ 10 previsões avaliadas automaticamente (F7). O dono ainda não opera opções. A F9 é um novo pedido de apresentação, continua planejada e não faz parte deste fecho.
- **Decisão:**
  1. A construção de **F0–F8 está concluída**. Nenhuma funcionalidade nova, fórmula, contrato, dado ou `model_version` (segue 2) muda para "fechar" F6/F7. A F9 permanece planejada, sem checklist de aceitação marcado.
  2. F6 e F7 ficam no estado **"implementação validada, aceitação no uso real"**. A contagem começa quando o dono registrar a primeira posição/decisão no journal oficial (8083).
  3. O que conta como aceitação **não muda**: F6 = P&L batendo com o extrato da corretora em 2 posições; F7 = 30 sessões de pregão consecutivas com decisão real, sem lacuna, e ≥ 10 previsões distintas avaliadas pelo lote. Smokes e schemas descartáveis não contam.
  4. Defeito encontrado durante o uso entra como correção pontual (com o mesmo rito de validação), não reabre a construção.
- **Por quê:** separar "pronto para usar" de "provado no uso" evita marcar checkbox com dado fabricado e evita inventar trabalho de construção enquanto a prova depende do calendário.
- **Consequência:** `PLAN.md` → "Retomar em" = começar a usar F6/F7 (decisão diária no Diário + 2 posições reais para reconciliar), com a F9 preservada como trabalho planejado. Revisão das ADRs provisórias na pauta "30 dias de uso".
- **Revisar em:** primeira posição real aberta, ou 30 sessões depois da primeira decisão.

## ADR-048 — tipografia global +40% com fator central

- **Data:** 2026-11-25 · **Status:** provisória — aplicada e aguardando avaliação final do dono · **Custo de reverter:** baixo (uma variável e os `calc()` de tipografia)
- **Contexto:** o dono pediu aumento de fonte em todo o PWA. Depois da primeira rodada de +20%, esclareceu que queria **40% acima do original**. O projeto fixa tamanhos em px nos componentes, tokens desktop, gráficos e tabelas; mudar apenas o `body` ou o `rem` raiz não atinge toda a interface e ampliaria espaçamento por engano.
- **Decisão:**
  1. `--font-scale: 1.4` em `:root` é a única fonte da escala. Mobile, desktop, componentes, gráficos, régua, tabelas, modais e forms usam `calc(<base>px * var(--font-scale))`.
  2. Os tokens `--text-*` de `@theme` também usam o fator; `--text-xs` é sobrescrito para que a exceção do primitive Button não fique menor. O `line-height` fixo de 20 px da tabela de liquidez acompanha a fonte.
  3. Espaço, raio, ícones, larguras e geometria dos gráficos não escalam. Não usar `zoom`, `transform: scale` nem aumentar o `rem` raiz.
  4. `docs/direcao.md` registra os novos valores: corpo mobile 18,9 px, corpo desktop 22,4 px, título desktop 50,4 px e gráfico desktop 16,8 px.
- **Validação:** `npm run check` e cobertura 26/63/45 verdes; duas builds limpas com 10 arquivos idênticos; 15 capturas de rota e 24 estados interativos em 412/1280/1440 px, zero overflow horizontal do documento e zero erro de console. `data/`, API, fórmulas e `model_version` (2) não mudaram.
- **Por quê:** um fator único atende ao pedido e permite ajustar a densidade depois sem procurar tamanhos espalhados.
- **Consequência:** nenhum número exibido ou contrato muda. A aprovação visual final continua com o dono; se pedir outro valor, altera-se somente `--font-scale` e atualizam-se os valores documentados.
- **Revisar em:** retorno do dono sobre a legibilidade em uso real.

## ADR-050 — gráfico do papel e eixo de pregão no gráfico da série

- **Data:** 2026-11-25 · **Status:** provisória — implementada a pedido do dono, salvo ratificação · **Custo de reverter:** baixo (rota de leitura + componente)
- **Contexto:** o dono perguntou se fazia sentido ter gráfico de cotação da opção e do papel. Medição no lake de 2026 (vencimentos já expirados): a mediana de pregões com negócio por série é 10–14 (PETR4, VALE3, BBAS3, BOVA11, MGLU3); os 20 contratos de maior volume por papel negociam em 56% (BBAS3) a 96% (VALE3) dos pregões; 51.463 de 224.567 séries de 2026 (23%) trocam de `leg_id` no meio da vida. O gráfico da série existente usava o índice do ponto como eixo e ligava pernas diferentes.
- **Decisão:**
  1. `GET /api/t/{t}/prices` (só HTTP, mesma `QueryService`): fechamento `010` nas últimas `days` sessões **do calendário do lake** (sessão sem negócio = `null`, não some). Desdobramento/grupamento do B2 aplicado às barras anteriores ao evento (mesma regra da RV); **provento não é descontado** — o preço mostrado é o negociado, e a data ex vira marca.
  2. Marcas: `dividend`/`scale`/`indeterminate` (B2), `earnings` (CVM) e `gap` = abertura ≥ 40% acima/abaixo do fechamento anterior, sem evento do B2 no dia, com piso de R$ 1 e negócio na sessão anterior. O pregão dominante da RV (`rv_shock_date`) é marcado pela tela a partir do `summary`.
  3. Papel sem nenhuma opção do ISIN na janela sai com `no_event_coverage`: o B2 não enxerga desdobramento dele (BKNG34 06/04/2026, razão 0,039, é exatamente esse caso).
  4. Série: cada ponto ganha `session_index` (posição no calendário do lake desde o 1º ponto), `leg_id` e `strike_brl` da data. A tela usa o pregão como eixo, tracejado nas lacunas, **quebra a linha** na troca de perna e marca a troca.
  5. Sem candle, média móvel ou indicador técnico: o painel é descritivo (ADR-023/024).
- **Por quê:** contexto visual do que a tela já mostra (choque de 26/10, proventos, resultados) sem número novo; a escala é aplicada porque sem ela SBSP3 mostraria queda de 80% em 29/04/2026 que não aconteceu.
- **Consequência:** nenhuma fórmula muda; `model_version` segue **2**. Cada página de `prices` custa ~1,6 s (leituras pequenas do lake); a tela faz 5 páginas com `staleTime` de 10 min. Não há tool MCP `prices` nesta versão.
- **Revisar em:** se o dono quiser o preço ajustado por provento (retorno total) ou a série de preço no MCP.

