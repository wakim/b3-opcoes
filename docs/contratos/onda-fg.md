# Contrato — Onda F (F1.11 CLI/backfill/validação) · Onda G (F1b portão)

**Dono do contrato:** orquestrador · **Data:** 2026-11-22 · **Válido para:** F1*, G1, G2.

**Os limiares do portão (§8 do termo de sessão) são PRÉ-REGISTRADOS aqui.**
Mudá-los depois de ver o resultado é proibido (P-L). Não existe "rodar até
passar": recorte, janela, subconjunto e limiar são estes e nenhum outro.

## 0. Donos de arquivo

| Pacote | Dono | Arquivos |
|---|---|---|
| F1* | impl. F | `src/b3o/metrics/` (pipeline por data), `src/b3o/cli.py` (comandos `metrics`/`--backfill`), `tests/test_metrics_*.py` — detalhado no fim, **após as ondas D/E pousarem** (este contrato fixa a semântica; a lista de arquivos fecha na abertura da onda) |
| G1 | impl. G | `src/b3o/derive/backtest.py`, `src/b3o/cli.py` (comando `backtest`), `out/backtest.md` |
| G2 | impl. G (mesmo dono, mesmo pacote) | `tests/test_backtest_lookahead.py` |

## 1. Onda F — métricas por (papel, data) (`core.metrics_*`)

- `b3o metrics --date AAAA-MM-DD`: calcula para o universo líquido do dia:
  - `metrics_series`: por série — `iv` (Black-76, % a.a.), `delta`, `gamma`,
    `theta_brl`, `theta_pct`, `vega_brl`, `rho`, `iv_pct_252` (percentil),
    `flags[]` (`wide_spread`, `no_trade_5d`, `bfly_negative`, `sparse_chain`,
    `one_sided`, `iv_extrapolated`, `scale_event`, `american_call_ex_div`,
    `inconsistent`, `style_unknown`).
  - `metrics_underlying`: `iv_atm_30d`, `iv_rank_252`, `iv_pct_252`, `rv20/30/60`,
    `vrp`, `vrp_z_252`, `rr25`, `bfly25`, `ts_slope_30_90`, `exp_move_1s_30d`,
    `implied_div_yield_30d`, `days_to_liquidate`, `forward_30d`, `rate_source`,
    `model_version`.
  - Grava em `data/metrics_underlying/`, `data/metrics_series/`
    (partição `year=YYYY`, ADR-009; `model_version=1` sempre gravado).
- `b3o metrics --backfill`: recalcula o histórico INTEIRO disponível
  (2026 primeiro; 2024/2025 depois de baixados — ADR-006); **z/percentis usam
  só dados ≤ data** (o teste de look-ahead do G2 caça o contrário).
- **Ingestão de 2024/2025**: `prepare` aceita `--year` (fetch dos anos
  fechados, imutáveis — sem reparse diário); o `health` mostra as partições.
- **Validação externa (F1.12, critérios do PLAN)**: (a) paridade call E × put E
  mesmo strike — mediana |ΔIV| ≤ 0,5, P90 ≤ 1,5 vol point em PETR4/VALE3/BBAS3/
  ITUB4/B3SA3; (b) PETR4 vs brapi sandbox (sem token; convenção dias corridos/365
  → converter para a variância total em T útil/252; ±2 vol points; diferença
  sistemática registrada no AGENTS). brapi fora → P-J (registra e segue).
- `american_call_ex_div`: call americana ITM com ex-dividendo antes do
  vencimento → IV contaminada → **fora do smile e do forward** (spec §6.2).

## 2. Onda G — o portão (pré-registrado; NÃO OTIMIZAR)

### G1 — backtest (§6.13)

```
para cada (papel, data) do histórico com classe != none:
    rv_fwd20 = Yang-Zhang das 20 sessões SEGUINTES (ajustada por corporate_events)
    edge = iv_atm_30d(data) − rv_fwd20        # pontos de vol
    acerto: premium_rich → edge > 0; premium_cheap → edge < 0
base de comparação: classe none (mesmas datas)
recortes: por classe, por papel, e "janela contém resultado" (earnings)
```

- Classes (§6.9, limiares FIXOS): `premium_rich` = `vrp_z ≥ +1,5` E `vrp ≥ +4`;
  `premium_cheap` = `vrp_z ≤ −1,5` E `vrp ≤ −4`; `skew_anomaly` =
  `|rr25 − mediana252| > 2σ252` com grade ≥5 strikes; `event_pressure` =
  `iv_atm(front) > iv_atm(back) + 2`; senão `none`.
- **Aquecimento**: só (papel, data) com ≥252 sessões de histórico de
  `vrp`/`rr25` antes da data.
- **Período**: 2024–2026 completos (após baixar os anos; ADR-006) — se um
  ano não existir na fonte, o relatório diz qual e por quê.
- **Janela com resultado**: earnings DENTRO das 20 sessões seguintes —
  com a **folga de 1 sessão do ADR-010** (DT_RECEB vale como evento do
  pregão SEGUINTE — nunca usar o resultado no dia em que "saiu" como se
  o pregão já soubesse).
- Relatório `out/backtest.md` (P-T): `n`, taxa de acerto, mediana e P10 de
  `edge`, **sempre** com a classe `none` ao lado (base), por classe e por
  papel. Nenhuma seleção de subconjunto além disso.
- `signal_backtest(signal_class, ticker)` (F2/MCP consome depois; aqui só a
  função pura sobre as métricas gravadas).

### Critérios do portão (pré-registrados — §8 do termo)

O sinal PASSA se, no período completo (2024–2026, após aquecimento):
- `premium_rich`: `n ≥ 50`, taxa ≥ 55%, mediana de `edge ≥ +1,0` vol point,
  e taxa ≥ taxa do `none` + 5 p.p.;
- `premium_cheap` simétrico: `edge < 0` (acerto), mediana ≤ −1,0, mesma
  diferença contra `none`.

**Passou** → registrar em decisoes + AGENTS, marcar F1b, seguir para F2/F3.
**Não passou** → PARAR o desenvolvimento de produto; `out/backtest.md` com
o resultado + leitura em 10 linhas + decisão proposta (segue / muda limiar com
justificativa a priori / troca de sinal) em decisoes como pendente do dono;
marcar só os critérios que passaram.

PROIBIDO na G: mudar limiar; mudar janela (`rv_fwd20` → outra); escolher
subconjunto de papéis; rodar até achar recorte que passe; recalcular
`model_version` para "consertar" o resultado.

### G2 — teste de look-ahead (o teste que PROVA que o resultado pode estar errado)

- Teste que **permuta `rv_fwd20` entre datas** (embaralha o futuro) e **exige
  mudança material** no relatório (taxa de acerto, mediana de edge): se o
  resultado permute == original, o teste FALHA — ele está provando que a
  métrica não é constante e que o pipeline consome o futuro de verdade.
- E o complementar: **z da data usa só dados até a data** — construa uma
  série sintética em que vazar 1 dia muda o z e prove que o código do F não
  vaza (cite a linha que fatia o histórico).
- O teste roda no `pytest` normal (determinístico, seed fixa).

## 3. Definição de pronto

1. pytest verde + ruff limpos.
2. G1 roda no histórico real e gera `out/backtest.md` com os recortes
   registrados — saídas coladas na resposta (n por classe, taxas, medianas).
3. G2 verde com a permutação MUDANDO o resultado (cole antes/depois).
4. Commit `feat(f1b/backtest): …` + o relatório NÃO entra no commit (out/ é
   ignorado; o orquestrador arquiva o texto na mensagem final).
5. Nenhuma decisão de "seguir/parar" é sua: o orquestrador aplica os critérios
   pré-registrados e o dono ratifica.

---

## 4. Ajustes na abertura da Onda F — 22/11/2026 (orquestrador)

O contrato acima fixou a **semântica**; esta seção fecha os **nomes** (a lista
de arquivos, como prometido) e os furos encontrados ao ler o lake real.
Registrado em `docs/decisoes.md` (ADR-014).

### 4.1 Fato do lake que muda o desenho

`data/legs/` guarda **só identidade de leg** (`leg_id, ticker, maturity, side,
style, refdate, leg_index, strike, distribution_id, flags`) — **nenhum preço**.
E `ticker → underlying` **não é persistido em lugar nenhum**: hoje só existe em
memória (`strike_events._underlying_of`, `liquidity.paper_of_isin`), derivado do
`isin_underlying` contra a linha `010`/`020` do mesmo dia. Consequência: o
pipeline de métricas re-junta `legs` × `raw_cotahist` por `(ticker, refdate)` e
resolve o papel pelo ISIN **no mesmo SQL** — e a junção tem que ser conferida:
`strike` e `distribution_id` do leg persistido são a autoridade (fronteira de
leg não se atravessa), o preço vem do raw.

### 4.2 Donos de arquivo (fecha a lista)

| Pacote | Arquivos |
|---|---|
| F1a (implementador F1a) | `src/b3o/query/service.py` (só métodos novos de leitura + `health.rates_sessions`/`unexplained_gap`), `src/b3o/store/metrics.py`, `src/b3o/ingest/selic.py`, `src/b3o/cli.py` (só `prepare --year` honesto e `rates --backfill`), `tests/test_query_service.py`, `tests/test_store_metrics.py`, `tests/test_selic.py`, `tests/test_cli_prepare.py` (acréscimos) |
| F1b (implementador F1b) | `src/b3o/derive/calendar.py`, `src/b3o/derive/signal.py`, `src/b3o/metrics/series.py`, `src/b3o/metrics/underlying.py`, `src/b3o/metrics/pipeline.py`, `src/b3o/metrics/validation.py`, `src/b3o/cli.py` (só o comando `metrics` e o resumo), `tests/test_calendar.py`, `tests/fixtures/sessions_2026.txt`, `tests/test_metrics_series.py`, `tests/test_metrics_underlying.py`, `tests/test_metrics_pipeline.py`, `tests/test_signal.py`, `tests/test_validation.py` |
| congelado | todo o resto |

Regra de SQL (teste de arquitetura): `duckdb` continua **proibido** em
`metrics/`. Leitura vai por `QueryService` (métodos novos, um por consulta);
escrita por `store/metrics.py` (que é `store/`, logo pode usar pyarrow e
`b3o.fsutil.replace_file`).

### 4.3 Tabelas de saída (schema exato)

`data/metrics_series/year=YYYY/part-0.parquet` — uma linha por
`(refdate, ticker)`:

```
refdate date, ticker str, maturity date, leg_id str, side str, style str|null,
strike int64 (¢/ação), t double (du/252), mid double (¢/ação), quote_factor int64,
iv double|null (% a.a.), delta double|null, gamma double|null,
theta_brl double|null (R$/ação/dia útil), theta_pct double|null (% do prêmio/dia),
vega_brl double|null (R$/ação/1 p.p. de vol), rho double|null,
iv_pct_252 double|null, is_liquid bool, flags list<str>, model_version int64
```

`data/metrics_underlying/year=YYYY/part-0.parquet` — uma linha por
`(refdate, underlying)`:

```
refdate date, underlying str, spot double (¢/ação, mercado 010 ou 020),
underlying_market str, iv_atm_30d double|null (% a.a.),
iv_rank_252 double|null, iv_pct_252 double|null,
rv20 double|null, rv30 double|null, rv60 double|null (% a.a. base 252),
vrp double|null (vol points), vrp_z_252 double|null,
rr25 double|null, bfly25 double|null (vol points),
ts_slope_30_90 double|null (vol points; §ADR-013),
exp_move_1s_30d double|null (fração), implied_div_yield_30d double|null (% a.a.),
days_to_liquidate double|null, forward_30d double|null (¢/ação),
rate_source str, signal_class str ("premium_rich"|"premium_cheap"|
"skew_anomaly"|"event_pressure"|"none"), flags list<str>, model_version int64
```

- **Unidade**: preço e strike em **centavos por ação** (o `/quote_factor` do
  FATCOT é aplicado aqui — `mid = (bid+ask)/2 / FATCOT`, `close/FATCOT`);
  IV e vols em **% a.a.**; VRP/skew/slope em **pontos de vol**; exp_move em
  **fração**. Nada de R$ de contrato nesta tabela (contrato = ×100 só em F4).
- `model_version` vem do `config.yaml` e é gravado em **toda** linha.
- `flags` (união das flags da série/papel, sem repetição, ordem alfabética):
  as de §1 + `smile_outside_range`, `no_forward`, `no_short_expiry`,
  `no_expiry`, `no_rates`, `scale_event`, `earnings_before_expiry`,
  `earnings_unmapped`.
- Partição é **substituída por inteiro** quando o ano é recalculado
  (`--backfill`), e por `(refdate)` quando o cálculo é de um dia (merge: lê a
  partição, remove as linhas daquele `refdate`, concatena, ordena). Idempotente.

### 4.4 `rate_source` por sessão — a curva DI não existe para o histórico

Medido: `data/rates_di` tem **1 data** (2026-11-19). O proxy da B3 só publica as
últimas 20 sessões, então 2024/2025 e quase todo o 2026 ficariam sem taxa.

O `ingest/rates.py` da Onda B **já tem** o fallback `selic_flat` (SGS 11 do BCB,
anualizada, curva plana de nós `du = 1…756`), mas ele busca **uma data por
chamada** (`SGS_LOOKBACK_DAYS=30`) e o `prepare` só busca a última sessão do ano.
Falta o preenchimento em lote: `src/b3o/ingest/selic.py` faz **uma** chamada ao
SGS para o intervalo inteiro (`dataInicial`/`dataFinal`), anualiza
`(1+t/100)^252 − 1` (em **% a.a.**), e para cada sessão do lake **sem** curva
grava a curva plana (`source="selic_flat"`) com `write_rates_partition` (reusa o
escritor da Onda B). A curva PRE da B3 (`b3_reference`) tem precedência: só as
datas sem curva são preenchidas. Sessão cujo dia não tem ponto no SGS (feriado)
usa o último valor anterior — o SGS só publica em dia útil.
Sem nenhuma fonte de taxa para a data → `df = 1.0` **não** é permitido: a série
sai com `None` + flag `no_rates` e não entra em IV/forward.

`prepare --year` (ano fechado) tem que continuar honesto: `last_session` do
`ingest_state` passa a ser **a maior data do lake inteiro** (não a do ano
preparado) e `counts_by_date` é **mesclado**, não substituído — senão preparar
2024 apaga o retrato de 2026.

### 4.5 Pipeline (F1) — ordem por data, tudo em memória para um dia

`run_metrics(data_dir, *, dates, model_version, config) -> MetricsSummary`.

Para cada `refdate` (em ordem crescente; `--backfill` itera 2024→2026):

1. **spot**: linha `010` (senão `020`) do papel; `spot` em ¢/ação = `close`
   (o `010` não tem FATCOT; `020` também não). Papel sem negócio no dia → fora.
   **Só papéis com ao menos uma linha de opção (`070`/`080`) no dia entram em
   `metrics_underlying`** — o resto do universo aparece no `screen` do F2 com
   motivo de exclusão (`no_options`), não como linha de métrica vazia.
2. **séries**: linhas `070`/`080` do dia, juntadas ao leg persistido por
   `(ticker, refdate)` com `strike` igual ao do leg; `strike <= 0` sai (IBOV
   órfã); `mid`/`one_sided` por `bid>0 e ask>0`, senão `close` + `one_sided`.
3. **T** por `(refdate, maturity)` com `TradingCalendar` do lake (exclui o dia
   do vencimento; vencido sai).
4. **DF**: `log_df_interp(du = round(T·252), curva_da_sessão)`; sem curva →
   `no_rates`, papel fora de IV.
5. **forward** por `(papel, vencimento)` com `forward_by_parity` (irmã europeia
   preferida; call americana só OTM sem ex-div; put 'A' exclui o par).
6. **IV + gregas** por série (Black-76 sobre F do vencimento), com
   `american_call_ex_div` (call americana ITM e ex-div antes do vencimento →
   flag e fora do smile) e `quote_factor` aplicado no preço.
7. **validadores** (`check_chain`) por vencimento → `inconsistent` fora do
   smile.
8. **ATM** por vencimento (`atm_by_expiry`) e **30d** (`atm_interpolate_30d`);
   `ts_slope_30_90` = IV ATM em `90/252` menos `iv_atm_30d` (mesma
   interpolação de variância total, `None`+flag se não houver bracket).
9. **skew**: os `SmilePoint` do vencimento mais próximo de 30d (o `front` do
   `Atm30d`; se não houver, o de menor `|t − 30/252|`) → `rr25`/`bfly25`.
10. **RV**: Yang-Zhang 20/30/60 sobre o OHLC do papel (mercado escolhido em 1),
    `adj_factor` acumulado de `strike_events` kind=`scale` (fator multiplicado
    nas barras anteriores ao evento) e `ex_div_dates` = datas dos eventos
    kind=`dividend` do papel.
11. **VRP/z/rank**: `vrp = iv_atm_30d − rv30`; `vrp_z_252`/`iv_rank_252`/
    `iv_pct_252` com **o histórico já gravado ≤ data** (lido da própria
    `metrics_underlying`, nunca do futuro) — `--backfill` em ordem crescente é
    o que garante isso.
12. **classe** (`derive/signal.py`, thresholds fixos da §6.9) e
    `earnings_before_expiry` (data `actual`/`estimated` antes do vencimento do
    `front`, com a folga de 1 sessão do ADR-010).
13. **liquidez**: `is_liquid` por série (§6.9: `trades ≥ 5` em 5 sessões,
    `spread_pct ≤ 10`, `bid > 0`, `ask > 0`) — precisa das 5 sessões
    anteriores do próprio ticker: o pipeline carrega `refdate-5 sessões` para
    esse cálculo e para o Yang-Zhang (60 sessões), sem gravar nada antes da
    data pedida.
14. grava as duas tabelas da data.

### 4.6 `iv_pct_252` de série

Percentil da IV da própria série nas 252 sessões anteriores (inclusive a de
hoje), **calculado na gravação** com um dicionário `leg_id → deque(maxlen=252)`
alimentado em ordem cronológica (mesmo `--backfill`) — não é pós-processamento
SQL (SQL fora de `store/` e `query/` é bug) e não é window do DuckDB.

### 4.7 CLI

```
b3o metrics --date 2026-11-19 [--config …]      # um dia; imprime resumo de uma tela
b3o metrics --backfill [--from 2024-01-01] [--to 2026-11-19] [--config …]
b3o metrics --checks --date 2026-11-19          # (F2) evidência de aceitação em out/
```

- `--date` sem `--backfill`: recalcula **só** aquele `refdate` (merge na
  partição). `--backfill` recalcula o intervalo inteiro do lake em ordem
  **crescente** e substitui as partições dos anos tocados.
- resumo: `metrics <data> · N papel(is) · M série(s) · K com IV · iv_atm_30d
  mediana · classes: {rich: x, cheap: y, none: z} · flags principais`.
- `--checks` (F2) roda e **imprime** a tabela de aceitação do F1 (§4.9) e grava
  `out/validacao-f1-AAAA-MM-DD.md` (P-T).

### 4.8 Testes obrigatórios do F1

- pipeline sintético ponta a ponta (lake mínimo com 1 papel, 2 vencimentos,
  5 strikes, curva plana): IV de uma série plantada recupera ±1e-6; `mid` com
  FATCOT 100 divide por 100; `strike <= 0` fora; `one_sided` quando faltar book.
- `legs` × raw: série cujo `strike` do leg difere do raw **não** entra (o leg
  manda) e a linha ganha `leg_mismatch`.
- sem curva → `no_rates`, `iv = None` e **nada** de DF=1.
- idempotência: rodar duas vezes o mesmo dia produz o mesmo parquet (sha igual).
- look-ahead: `vrp_z` de uma data não muda quando o histórico **posterior** é
  acrescentado (mesma série, +1 dia futuro → mesmo z da data antiga).
- `--date` num dia já calculado substitui apenas aquele `refdate`.

### 4.9 Calendário: o lake só conhece o passado (furo medido)

O arquivo termina em 19/11/2026 e há vencimentos até **17/11/2028**. Com o
calendário feito só das sessões do lake, **todo** vencimento futuro daria
`business_days = 0` → `T = 0` → `iv_expired` em 100% da grade.

Medido: os **221** pregões de 2026 (02/01→19/11) são **exatamente** os dias
úteis do intervalo menos 11 feriados — zero sobra, zero falta. Logo:

- `derive/calendar.py` (puro): `easter_sunday(year)` (Meeus), `b3_holidays(year)`
  = fixos `01/01, 21/04, 01/05, 07/09, 12/10, 02/11, 15/11, 20/11, 25/12` +
  `24/12, 31/12` (sem negócio) + móveis `Páscoa−48`, `Páscoa−47`, `Páscoa−2`,
  `Páscoa+60`; `trading_sessions(start, end, known)` = datas conhecidas ∪
  (dias úteis − feriados) no intervalo, ordenado e sem repetição;
- teste de referência: fixture `tests/fixtures/sessions_2026.txt` com as **221**
  datas do lake → `trading_sessions(2026-01-01, 2026-11-19, known=())` tem que
  devolvê-las **todas, sem sobra** (é o que valida a regra);
- `t_estimated`: vencimento **depois** da última sessão do lake → `T` vem de
  dias úteis/feriado (não do arquivo), e a série/papel ganha a flag
  `t_estimated` — o número não se passa por medido.

### 4.10 Evidência de aceitação do F1 (o `--checks` produz)

1. paridade call E × put E, mesmo strike/vencimento, ATM ±10%, com negócio nas
   duas: mediana de `|iv_call − iv_put|` e P90 (vol points) em PETR4, VALE3,
   BBAS3, ITUB4, B3SA3 — critério: mediana ≤ 0,5 e P90 ≤ 1,5.
2. PETR4 contra brapi: **medido em 22/11 que a cadeia/IV saiu do sandbox
   (HTTP 401)**; a validação externa cai em P-J (registra e segue) — o
   `--checks` tenta `brapi.dev/api/v2/options/PETR4` uma vez, registra o status
   e segue.
3. forward OTM-only vs ITM-only ≤ 0,5% do spot (para cada papel/vencimento).
4. `r ± 100 bp` → `max|ΔIV| < 0,25` (recalcula IV com a curva deslocada).
5. ATM 30d sem salto > 2 vol points em dia de rolagem (série de 60 sessões,
   diferença dia a dia; lista os saltos).
6. RR25 da PETR4 negativo em ≥ 80% dos dias com `rr25` calculado.
7. nenhuma série `inconsistent` entra no smile: conta por data e prova com
   uma assertiva sobre a cadeia usada no ATM.
