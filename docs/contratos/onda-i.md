# Contrato — Onda I (F3: MCP mínimo do painel descritivo)

**Dono do contrato:** orquestrador · **Data:** 2026-11-23 · **Válido para:** F3.0–F3.3.
**Base:** ADR-023 (parar no portão) + ADR-024 (F2 descritivo) + spec §7/§8/§9.
O sinal do F1b está falsificado: nada nesta onda pode sugerir previsão, nota, classe de
sinal, taxa de acerto ou recomendação.

## 0. Regras que não se quebram

1. **O Python calcula.** Nenhum tool/rota calcula número novo com LLM; o MCP só transporta
   o que a `QueryService` devolve.
2. **Uma camada de consulta.** HTTP (`/api/...`) e MCP (`/mcp`) chamam **as mesmas funções**
   de payload, sobre uma **única instância** de `QueryService` (criada no lifespan). SQL só
   em `store/` e `query/`.
3. **Descritivo.** `signal_backtest` **não** é exposto (ADR-024 + desvio registrado em
   ADR-025). `signal_class` não é lido nem devolvido.
4. **Falha explícita.** Erro = `{"error": {"code", "message", "hint"}}`; ausência = `null`
   + flag; nunca zero silencioso nem traceback no corpo.
5. **Limites.** `max_rows` padrão **20**, teto **60** (o teto é do transporte; a
   `QueryService` aceita mais). Resposta > **200 KB** trunca a lista principal, com
   `meta.truncated = true`, `meta.total` e `meta.returned` coerentes; célula nunca é
   cortada pela metade.
6. **Fórmula congelada.** `metrics/`, `ingest/`, `store/` e `derive/` de cálculo não são
   tocados; `model_version` continua **2**. Os números vêm de `metrics_*` já gravados.
7. **Container é o caminho oficial.** `uv lock` dentro do container (workaround do bind),
   `uv.lock` versionado.

## 1. Donos de arquivo

| Pacote | Arquivos |
|---|---|
| F3.0 | `backend/src/b3o/query/service.py` (Health + leitura da versão derivada), `query/__init__.py` |
| F3.1 | `backend/src/b3o/api/` (**novo**: `__init__.py`, `app.py`, `state.py`, `payloads.py`, `serialize.py`, `mcp_server.py`), `backend/pyproject.toml`, `backend/uv.lock`, `compose.yaml` (só se necessário) |
| F3.2 | `backend/src/b3o/query/service.py` (métodos novos + dataclasses), `query/__init__.py` |
| F3.3 | `backend/scripts/mcp_smoke.py` (novo) |
| docs | `docs/contratos/onda-i.md`, `docs/decisoes.md`, `docs/glossario.md` (só verbetes novos), `docs/relatorios/2026-11-23-revisao-f3.md`, `PLAN.md`, `AGENTS.md` |
| **congelado** | `metrics/`, `ingest/`, `store/`, `derive/` de cálculo, `frontend/`, `config.yaml` |

## 2. Envelope de resposta (HTTP e MCP)

```json
{
  "as_of_session": "2026-11-19",
  "model_version": 2,
  "data": { "...": "payload da tool" },
  "meta": { "total": 0, "returned": 0, "truncated": false }
}
```

- `as_of_session` = maior sessão do lake ≤ `date` (nunca o relógio, P-C); `null` só quando o
  lake não tem sessão (aí a rota já falhou antes).
- `model_version` = maior versão das linhas que produziram a resposta; ausência de métrica
  → versão derivada das tabelas (`QueryService.derived_model_version`), nunca 1 inventado.
- `meta` só existe quando há lista; `total` = linhas após filtro, `returned` = linhas
  efetivamente devolvidas, `truncated` = `returned < total`.
- Erro: o corpo é **apenas** `{"error": {...}}` (sem envelope), status HTTP 404
  (`ticker_not_found`, `no_session`, `maturity_not_found`, `expiry_not_found`) ou 400
  (`bad_request`); MCP devolve o mesmo dicionário como conteúdo da tool.

## 3. Health (F3.0)

`Health` passa a dizer a verdade sobre o modelo:

| Campo | Regra |
|---|---|
| `model_version` | **maior** `model_version` de `metrics_underlying`/`metrics_series`; sem tabela derivada → versão do `ingest_state` |
| `ingest_model_version` | o que o `ingest_state` diz (diagnóstico) |
| `model_version_mismatch` | `true` quando os dois existem e divergem |
| `model_version_source` | `"metrics"` ou `"ingest_state"` (de onde veio o `model_version`) |

Nada mais do `health` muda; `prepare` não é tocado. ADR-025 registra.

## 4. Leituras da `QueryService` (F3.2)

Todas devolvem dataclasses `frozen, slots`; preço em **R$ na borda** (`_brl`), IV/RV em
**% a.a.**, gregas como gravadas, `t` em **dias úteis / 252**, `exp_move` em **fração**.
Ticker é normalizado (`strip().upper()`); papel inexistente no lake é
`SourceFailure(ticker_not_found)` com dica.

### 4.1 `underlying(ticker, date=None, sections=("summary","term","smile","history"), expiry=None, days=60)`

```python
UnderlyingDetail(as_of_session, model_version, ticker, in_universe,
                 summary: ScreenRow | None,
                 term: list[TermRow], smile: list[SmileRow], smile_maturity: date | None,
                 history: list[HistoryRow], notes: list[str])
```

- `summary` = **a mesma linha** de `screen_bundle(date)` (campos e unidades de `ScreenRow`,
  sem `signal_class`); papel fora do universo → `summary = None` + `in_universe = false` +
  nota.
- `TermRow(maturity, t, iv_atm, atm_strike_brl, forward_brl, forward_method, exp_move_1s,
  n_strikes, n_liquid_strikes)`:
  - `iv_atm` = IV da série com `|Δ|` mais próximo de 0,5 naquele vencimento (seleção sobre o
    que está gravado; não recalcula IV);
  - `forward_brl` = paridade put-call **reconstruída dos mids gravados** com
    `derive.forward.forward_by_parity` (mesma função pura do pipeline; `method` diz
    `crossing`/`ols`/`none`, `null` quando não há par honesto);
  - `exp_move_1s` = `derive.dist.expected_move_1s(call_mid + put_mid no strike ATM, spot)`;
  - `t` = o `t` gravado do vencimento; `n_strikes` = strikes distintos (strike > 0) com
    série no dia; `n_liquid_strikes` = idem com `is_liquid`.
- `SmileRow(strike_brl, iv_pct, delta, side, is_liquid)` — uma linha por **série** com IV
  não nulo, ordenada por `(strike, side)`; `expiry=None` usa o vencimento de `t` mais
  próximo de 30/252 com IV. máximo de 60 linhas no transporte.
- `HistoryRow(date, iv_atm_30d, rv30, vrp, vrp_z_252, rr25)` — `metrics_underlying` do papel,
  `date <= as_of_session`, últimas `days` sessões.
- Seção pedida sem dado → lista vazia + `notes` explicando (nunca inventa).

### 4.2 `chain(ticker, expiry=None, date=None, delta_range=(0.25, 0.75), liquidity="strict", max_rows=60)`

```python
ChainRow(ticker, side, style, maturity, strike_brl, t, bid_brl, ask_brl, mid_brl,
         close_brl, iv_pct, delta, gamma, theta_brl, vega_brl, spread_brl, spread_pct,
         is_liquid, flags)
```

- Fonte: `metrics_series` da sessão (IV/gregas/liquidez/`mid`) + `raw_cotahist` do dia
  (`bid`/`ask`/`close`), com o papel resolvido **pelo ISIN** (nunca por prefixo).
- `liquidity="strict"` → só `is_liquid` gravado (§6.9); `"all"` → tudo.
- `delta_range` aplica a **|Δ|** (puts têm Δ negativo; o `side` diz o sinal); `None` não
  filtra; linha sem Δ sai do recorte quando há banda.
- `expiry` filtra o vencimento (data ISO); desconhecido → `expiry_not_found` com dica.
- Ordem: `maturity, strike, side`; `max_rows` aplica **depois** do filtro (as N primeiras,
  nunca "as melhores"). Papel sem opção no dia → lista vazia + `notes`.

### 4.3 `series(ticker, maturity, days=60)`

```python
SeriesDetail(ticker, maturity, side, style, strike_brl, leg_id, t, is_liquid, flags,
             last: SeriesPoint, history: list[SeriesPoint], as_of_session, model_version)
SeriesPoint(date, close_brl, mid_brl, iv_pct, delta)
```

- Identidade e última linha vêm de `metrics_series` (última sessão ≤ data); `last` traz
  também `gamma`, `theta_brl`, `vega_brl`.
- `history` = últimas `days` linhas com `date <= as_of_session`.
- Ticker inexistente no lake → `ticker_not_found`; vencimento inexistente para a série →
  `maturity_not_found` (dica com vencimentos disponíveis); série existe mas sem métrica →
  `metrics_missing` (dica `b3o metrics --date`).

### 4.4 `events(ticker)`

```python
Events(ticker, as_of_session, model_version, maturities: list[date],
       strike_events: list[StrikeEventRow], earnings: list[EarningsEventRow],
       next_earnings: EarningsEventRow | None)
```

- `maturities` = vencimentos com série do papel na última sessão (ordenados);
- `strike_events` = `date, kind, delta_strike, factor, n_series, ambiguous` (histórico);
- `earnings` = `date, kind, actual|estimated, ref_period` (histórico + futuro);
- `next_earnings` = primeiro `date >= as_of_session` (`actual` ganha empate), com dica de
  cobertura quando a sessão é anterior ao calendário.

## 5. Rotas HTTP (F3.1)

| Rota | Payload |
|---|---|
| `GET /api/health` | `health` |
| `GET /api/screen?date=&max_rows=` | `screen` |
| `GET /api/t/{ticker}?date=&sections=&expiry=&days=` | `underlying` |
| `GET /api/t/{ticker}/chain?date=&expiry=&delta_min=&delta_max=&liquidity=&max_rows=` | `chain` |
| `GET /api/s/{ticker}/{maturity}?days=` | `series` |
| `GET /api/t/{ticker}/events` | `events` |

Fora do F3 (F4/F5): escritas, journal, positions, structure, glossary, briefing, tickers.
Nada de `signal_backtest` (ADR-025).

## 6. MCP

Streamable HTTP em **`/mcp`**, mesma instância de `QueryService`. Tools: `health`, `screen`,
`underlying`, `chain`, `series`, `events`. Cada descrição declara **"use quando"** e
**"não use para"**, sem vocabulário preditivo (sem `signal_class`, `premium`, `ranking`,
`acerto`, `caro/barato`, `recomend`).

## 7. Critério de pronto

Os 10 itens do §7 do prompt da sessão, provados com saída colada:
`podman compose config` válido; `up -d app` + `/api/health` com `as_of_session` e
`model_version = 2`; `tools/list` com as 6 tools; `screen` 289/163/126 + mediana IV 31,43
(n=35) + VRP −30,66 (n=35) iguais em MCP e HTTP; `underlying PETR4` 42,67/31,43/88-782/25;
`chain`/`events` coerentes; erros `ticker_not_found`/`no_session` sem traceback; nenhuma
resposta > 60 linhas e teto de 200 KB truncando; `grep` preditivo = 0; `ruff check src`
limpo; `b3o health`/`screen`/`metrics` intactos; `uv.lock` commitado e build do zero ok.

## 8. Desvios declarados (registrados em ADR-025)

1. **`signal_backtest` fora do F3** (ADR-024 + prompt). A spec §7/§8 ainda o lista; o
   `PLAN.md` F3 é ajustado e a spec não é reescrita nesta onda.
2. **Sufixo `_brl`** nos campos de preço das leituras novas (P-R: unidade no nome); o
   rascunho do prompt listava `bid`/`ask`/`mid`/`close`/`strike` sem sufixo.
3. **`term.forward`/`exp_move` são reconstruídos na leitura** a partir dos `mid`s gravados,
   com as funções puras de `derive/` (paridade e §6.10). Nenhuma métrica gravada é
   recalculada nem reescrita; `model_version` não muda.
4. **`max_rows` padrão 20 no transporte** (regra da spec §8); a `QueryService.chain` aceita
   o default 60 do enunciado, mas HTTP/MCP passam 20 e nunca acima de 60.
