# Contrato — Onda B (F0.5 store · F0.6 legs · F0.9 rates · F0.10 earnings · F0.8 OI)

**Dono do contrato:** orquestrador · **Data:** 2026-11-22 · **Válido para:** B1..B5.

Regras inválidas aqui (AGENTS.md): o Python calcula; uma camada de consulta
(SQL só em `store/` e `query/service.py`); falha explícita (`SourceError`
{code,message,hint}; indisponível = `null` + flag); sem arredondamento no
cálculo; `model_version = 1`; **container é o caminho oficial**.

Erro que precisa interromper: `from b3o.ingest.errors import SourceError, SourceFailure`
(`SourceFailure` carrega o `SourceError`; capturar na borda, nunca silenciar).

---

## 0. Donos de arquivo

| Pacote | Dono | Arquivos |
|---|---|---|
| B1 | impl. B1 | `src/b3o/store/__init__.py`, `src/b3o/store/lake.py`, `src/b3o/store/state.py`, `tests/test_lake.py`, `tests/test_state.py` |
| B2 | impl. B2 | `src/b3o/derive/legs.py`, `src/b3o/derive/strike_events.py`, `tests/fixtures/strike_events_2026_sample.txt`, `tests/test_legs.py`, `tests/test_strike_events.py` |
| B3 | impl. B3 | `src/b3o/ingest/rates.py`, `tests/test_rates.py` |
| B4 | impl. B4 | `src/b3o/ingest/earnings.py`, `tests/test_earnings.py` |
| B5 | impl. B5 | `src/b3o/ingest/open_interest.py`, `tests/test_open_interest.py` |
| congelado | orquestrador | `src/b3o/ingest/{__init__,errors,parse,fetch,validate}.py`, `src/b3o/derive/__init__.py`, `docs/contratos/*` |

`store/` e `derive/` são leitura/escrita **só dos seus donos**. `derive/` é
**puro**: recebe iteráveis de dataclasses, devolve dataclasses/geradores; zero
I/O, zero duckdb/pyarrow (quem lê o lake é `store/lake.py`, quem orquestra é o
prepare do C1). O teste de arquitetura proíbe `duckdb.connect|\.execute(`
fora de `store/` + `query/service.py` — e você não precisa deles aqui.

## 1. Insumos congelados da Onda A

- `CotahistRow` (b3o.ingest.parse): refdate, bdi, ticker, tpmerc, corp_name,
  spec, open/high/low/avg/close/best_bid/best_ask (¢ int64), trades, qty,
  volume (¢), strike (¢), strike_adj_ind (sempre `'0'` em 2026 — não usar para
  nada), maturity (`None` quando `99991231`), quote_factor, strike_pts,
  isin_underlying, distribution_id, style, side.
- `FetchResult`/`SourceError` (b3o.ingest.fetch/errors).
- `KEPT_MARKETS` = {010, 020, 070, 080, 012, 013}.

## 2. B1 — store (F0.5)

`src/b3o/store/lake.py`:

```python
RAW_COTAHIST_COLUMNS = [...]  # ordem canônica abaixo

def write_cotahist_year(rows: Iterable[CotahistRow], year: int, data_dir: Path) -> WriteStats: ...
    # data_dir/<tabela>/year=YYYY/…  (P-A); partição do ano reconstruída INTEIRA:
    # escreve em data_dir/tmp/… e os.replace por partição; nunca sobrescreve antes
    # de validar o lote novo (escreve tudo em tmp, confere contagem, só então replace).
    # Streaming: RecordBatch a cada N linhas (N=100_000); NUNCA o ano inteiro em RAM
    # (3,5 M linhas; MV tem 3,7 GiB — o parse mediu 21 MiB de pico, mantenha essa ordem).

def read_cotahist_years(data_dir: Path, years: Iterable[int], markets: frozenset[str] = KEPT_MARKETS) -> Iterator[CotahistRow]: ...
    # leitura streaming por pyarrow (sem duckdb em derive/); preserva a memória baixa.
```

- Tabela: `data_dir/raw_cotahist/year=YYYY/part-0.parquet`.
- Colunas (nomes §5.1 + extras da Onda A, em centavos int64 onde for preço):
  `refdate, bdi, ticker, tpmerc, corp_name, spec, open, high, low, avg, close,
  best_bid, best_ask, trades, qty, volume, strike, strike_adj_ind, maturity,
  quote_factor, strike_pts, isin_underlying, distribution_id, style, side`.
  `refdate`/`maturity` como `date32`; `style`/`side` string nullable.
- `WriteStats`: `{rows, path, sha256, seconds}` (para o health).
- **Idempotência provada no teste:** duas escritas do mesmo lote → mesmos bytes
  (compare sha256 dos parquet; se o writer embutir algo não determinístico,
  compare conteúdo lido por `read_cotahist_years` ordenado — e REGISTRE o fato).
- `src/b3o/store/state.py`: `ingest_state` em `data/ingest_state.json`
  (esquema do data/.gitkeep), escrita atômica (tmp + replace), campos:
  `{etag, last_modified, sha256, trailer_total, last_session, counts_by_date,
  model_version: 1, skipped_lines: {width, numeric}, updated_at}`.
  `skipped_lines` vem do `ValidationReport` da Onda A (ADR-007: o que não pode
  ser silêncio vira contagem explícita aqui).

## 3. B2 — legs (F0.6)

`src/b3o/derive/strike_events.py` + `src/b3o/derive/legs.py` — **puros**.

```python
@dataclass(frozen=True, slots=True)
class LegRow:            # uma linha por (ticker, refdate)
    leg_id: str          # f"{ticker}|{maturity}|{leg_index}"
    ticker: str
    maturity: date
    side: str            # "C" | "P"
    style: str | None    # consolidação por MAIORIA do leg; empate/conflicto -> flag
    refdate: date
    leg_index: int       # incrementa a cada fronteira
    strike: int          # ¢, o do dia
    distribution_id: str
    flags: tuple[str, ...]  # ex.: ("leg_ambiguity", "style_conflict")

def build_legs(rows: Iterable[CotahistRow]) -> Iterator[LegRow]: ...
    # fronteira = mudança de strike OU strike_adj_ind OU distribution_id
    # (§5.2 — mesmo com strike_adj_ind '0' em 2026: zero custo, à prova de arquivo futuro).
    # SÓ linhas de opção (070/080). Série = (ticker, maturity); por série, ordene por refdate.

@dataclass(frozen=True, slots=True)
class StrikeEvent:
    underlying: str      # ticker do ativo-objeto derivado do isin_underlying + linha 010 do dia
    date: date           # primeira mudança CONTÍGUA (spec §6.6: a data observada não é a ex)
    kind: str            # "dividend" | "scale" | "indeterminate"
    delta_strike: int | None      # ¢ (dividend)
    factor: float | None          # razão (scale): <1 desdobramento, >1 grupamento
    n_series: int
    ambiguous: bool

def classify_strike_events(rows: Iterable[CotahistRow]) -> list[StrikeEvent]: ...
```

Regra §6.6 (sem limiar de razão — proibido):
1. **Só mudanças contíguas** datam e classificam: a série negociou no pregão
   anterior (por série, não por papel). Mudança não contígua não gera evento
   próprio; se a série reaparece com strike diferente, é o leg atual, sem data ex.
2. Assinatura **entre strikes do mesmo papel e data**: `Δstrike` igual em todos
   (±1¢) → `dividend`; razão igual em todos (±0,1%) com Δ variando → `scale`
   (fator = razão; <1 desdobramento, >1 grupamento); ambos cabem → `dividend`;
   nenhum/1 strike só → `indeterminate` + flag, tentando a mesma data em outras
   séries do papel antes de desistir.
3. **Nunca** classificar por "Δ<0 = dividendo" (SBSP ×0,2 vira provento de 80%).
4. Casos de teste REAIS no fixture (extraia do ZIP como na Onda A):
   SBSP 29/04/2026 (×0,2, ~106 séries — escala, NÃO dividendo), SIMH 18/02/2026
   (×2 — grupamento), LOGG 06/2026 (Δ ≈ R$ 2,86 → dividendo), ABEVX131
   (12,04 → 12,00 → 11,94 — dois eventos contíguos de dividendo).
5. `dividends_implied`/`corporate_events` ficam como listas derivadas do
   `StrikeEvent` (o store do C1 grava; aqui só a classificação pura).

## 4. B3 — curva DI (F0.9)

`src/b3o/ingest/rates.py` — cascata com `rate_source` SEMPRE gravado:

```python
@dataclass(frozen=True, slots=True)
class RatePoint:
    refdate: date
    du: int              # dias úteis
    rate_annual: float   # % a.a. efetiva, base 252 — SEM arredondar
    source: str          # "b3_reference" | "di1_futures" | "selic_flat"

def fetch_rates_di(date_ref: date, *, client: httpx.Client, data_dir: Path) -> RatesResult: ...
```

Fontes (todas medidas em 22/11/2026 — os endpoints são do SPA da B3):
1. **`b3_reference` (primária, "DI x pré")**: GET
   `https://sistemaswebb3-derivativos.b3.com.br/referenceRatesProxy/Search/GetDownloadFile/{b64}`
   com payload `{"language":"pt-br","date":"YYYY-MM-DD","id":"PRE"}` (b64 do
   JSON). Resposta = **CSV em base64** (`Descrição da Taxa;Dias Úteis;Dias
   Corridos;Preço/Taxa`, CRLF, `13,65` com vírgula). Janela: **~20 sessões**
   (`Search/GetDate/{b64({"language","id"})}` lista as datas disponíveis) — não
   há histórico automático além disso.
2. **`di1_futures`**: id `APR` (ajustes DI1), mesmo endpoint/mesma janela.
3. **`selic_flat` (fallback integral)**: BCB SGS série 11
   `https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados?formato=json&dataInicial=DD/MM/YYYY&dataFinal=DD/MM/YYYY`
   → Selic % a.d.; anual = `(1 + valor/100)**252 − 1` (float, sem arredondar).
   Cobertura histórica completa (serve para TODO o backfill 2024–2026).
   Alternativa diária no BDI: `POST /bdi/table/DIover/{d}/{d}/1/20` (colunas
   RptDt, Average, DailyFactor, SelicRate).
- Toda falha de rede/parse → `SourceError`/`SourceFailure` com code
  `source_unavailable` + hint; a ausência de uma data na fonte 1/2 NÃO é erro —
  é cascata. Se TODAS falharem → `RatesResult.error` (prepare não morre;
  `r = null` + flag no dia — P-M).
- Persistência: `data_dir/rates_di/year=YYYY/part-0.parquet`
  (refdate, du, rate_annual, source), uma linha por (refdate, du).
- Interpolação em log-DF NÃO é daqui (spec: fica no `derive/conventions.py`, F1).

## 5. B4 — calendário de resultados (F0.10)

`src/b3o/ingest/earnings.py` — nomes exatos CONFIRMADOS (spec §6.14 "a confirmar"):

```python
@dataclass(frozen=True, slots=True)
class EarningsDate:
    ticker: str          # ex.: "PETR4"
    ref_period: date     # fim do trimestre de referência
    date: date           # data do resultado (ou prevista)
    kind: str            # "actual" | "estimated"

def build_earnings(itr_dir: Path, dfp_dir: Path, fca_zip: Path, tickers: list[str]) -> list[EarningsDate]: ...
    # funções puras sobre arquivos já baixados; download (com ETag/304, A1-style)
    # separado em fetch_earnings(...)
```

- URLs (medidas, 200 OK): `https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_{AAAA}.zip`;
  `…/DOC/DFP/DADOS/dfp_cia_aberta_{AAAA}.zip`;
  `…/DOC/FCA/DADOS/fca_cia_aberta_{AAAA}.zip` (dataset `cia_aberta-doc-fca`).
- ITR/DFP (índice, primeiro membro do zip): `CNPJ_CIA, DT_REFER, VERSAO,
  DENOM_CIA, CD_CVM, CATEG_DOC, ID_DOC, DT_RECEB, LINK_DOC` (latin-1, `;`).
  **`actual` = `DT_RECEB` da `VERSAO == 1`** por `(CD_CVM, DT_REFER)`.
  ITR cobre ref 31-03/30-06/30-09; DFP cobre 31-12.
- Ticker→empresa: membro `fca_cia_aberta_valor_mobiliario_{AAAA}.csv`
  (`CNPJ_Companhia, Data_Referencia, Versao, ID_Documento, Nome_Empresarial,
  Valor_Mobiliario, Codigo_Negociacao, Mercado, Data_Inicio_Negociacao,
  Data_Fim_Negociacao, Segmento`) — usar `Mercado == "Bolsa"` e
  `Data_Fim_Negociacao` vazio (papel ativo). `Codigo_Negociacao` → `CNPJ_Companhia`
  → join com `CNPJ_CIA` do ITR/DFP.
- **`estimated`** = mesma `DT_REFER` do ano anterior, deslocada para o **mesmo
  dia da semana**, limitada pelo prazo regulatório (ITR: 45 dias após o
  trimestre; DFP: 3 meses após o exercício). Marca `kind="estimated"`.
- Papel sem correspondência → fica de FORA da lista (quem expõe é o C1/health
  com flag `earnings_unmapped`); NUNCA data inventada.
- Nada de `date.today()`: o "futuro" é relativo à última sessão do lake, que o
  C1 passa como argumento.

## 6. B5 — open interest (F0.8)

`src/b3o/ingest/open_interest.py`:

```python
@dataclass(frozen=True, slots=True)
class OIRow:
    refdate: date
    ticker: str
    oi: int               # contratos em aberto
    covered_qty: int | None
    uncovered_qty: int | None

def fetch_open_interest(date_ref: date, *, client: httpx.Client) -> OIResult: ...
```

- Endpoint MEDIDO (não está em doc pública; é o BDI da B3):
  `POST https://arquivos.b3.com.br/bdi/table/export/csv` com corpo JSON exato
  `{"Name":"OpenPositionsEquities","Date":"YYYY-MM-DD","FinalDate":"YYYY-MM-DD",
  "ClientId":"","Filters":null}` — resposta `text/csv`:
  2 linhas de descrição + 1 de glossário + 1 vazia + header + dados, `;` como
  separador, latin-1. Colunas (ordem): Instrumento financeiro; Código ISIN;
  Ativo; Código de expiração; Segmento; **Contratos em aberto**; Variação …;
  Identificador da distribuição; Quantidade coberta; Total de posições
  bloqueadas; Quantidade descoberta; Total de posições; Quantidade de
  tomadores; Quantidade de doadores; Quantidade atual; Contratos travados;
  Contratos baixados por transferência; Preço a termo. Números com `.` de
  milhar (ex.: `12.651.200`); vazio = `-`.
- **Data sem dado:** linha única `Nenhum resultado` → `oi = null` +
  `oi_unavailable` (NÃO é erro). Janela medida: ~10–12 sessões (19/11 OK,
  11/10 OK com 44.755 linhas, 02/11 já vazio).
- Falha de rede/parse → `SourceError(code="source_unavailable", …)`.
- Persistência: `data_dir/open_interest/year=YYYY/part-0.parquet`
  (refdate, ticker, oi, covered_qty, uncovered_qty).
- O join `(refdate, ticker)` com as séries é problema do C1/F1 — aqui só a ingerência.

## 7. Definição de pronto (todos)

1. `podman compose run --rm test pytest` verde; `ruff check src tests` limpo.
2. Roda em container; nada de Python/uv no host; o ZIP real está acessível ao
   container via `-v "C:\Users\wakim\AppData\Local\Temp\opencode\b3test:/b3test:ro"`.
3. Falha explícita em tudo (`SourceError`/`SourceFailure` ou null+flag).
4. Número conferido à mão em ≥1 caso por pacote (golden values abaixo ou do
   fixture real).
5. Commit só dos seus arquivos, `feat(f0/<escopo>): …`, com retry em index.lock.
6. Descoberta sobre o dado → relate na resposta (o orquestrador registra no AGENTS.md).

## 8. Golden values já medidos (para os testes)

- SBSP 29/04/2026: ×0,2 em ~106 séries (escala, strike 20,00→4,00 aprox.).
- SIMH 18/02/2026: ×2 (grupamento).
- LOGG 06/2026: Δ ≈ R$ 2,86 (dividendo).
- ABEVX131: 12,04 → 12,00 → 11,94 (dois proventos contíguos).
- OI 19/11/2026: `PETRPZ26` = 12.651.200 contratos em aberto (futuro); para **opções** o `Contratos em aberto` é `-` (sempre — ver AGENTS) e a posição é `Total de posições`: `PETRA365` EQUITY CALL = total **22.300** (coberta 100, bloqueada 3.500, descoberta 18.700 — em ações-objeto, não contratos).
- Curva PRE 19/11/2026: DI x pré 1 du = 13,65; 21 du = 13,72; 41 du = 13,76
  (aprox.; confira no CSV real baixado na hora).
- ITR 2026: BCO BRASIL S.A. (CD_CVM 001023), DT_REFER 2026-03-31, VERSAO 1,
  DT_RECEB 2026-05-13 → resultado Q1 2026 em 13/05/2026.
