# Contrato — Onda A (F0.2/F0.4 + F0.3/F0.7/F0.13)

**Dono do contrato:** orquestrador. **Data:** 2026-11-22. **Válido para:** pacotes A1 e A2.

Divergência de nome de campo, unidade ou offset em relação a este documento é bug.
Este contrato foi derivado de medições no arquivo real de 2026 (ver `AGENTS.md`).

---

## 1. Arquivos e donos

| Pacote | Dono (único) | Arquivos |
|---|---|---|
| A1 | implementador 1 | `src/b3o/ingest/fetch.py`, `src/b3o/ingest/validate.py`, `tests/test_fetch.py`, `tests/test_validate.py` |
| A2 | implementador 2 | `src/b3o/ingest/parse.py`, `tests/fixtures/cotahist_2026_sample.txt`, `tests/test_parse.py` |
| congelado | orquestrador | `src/b3o/ingest/__init__.py`, `src/b3o/ingest/errors.py`, este contrato |

Nenhum implementador edita arquivo fora da sua lista. Erro compartilhado vive em
`b3o.ingest.errors.SourceError` (**importar; não recriar**).

## 2. Tipos compartilhados

```python
# b3o.ingest.errors (JÁ EXISTE)
@dataclass(frozen=True, slots=True)
class SourceError:
    code: str      # ex.: "source_unavailable", "bad_data"
    message: str   # o que aconteceu, em uma linha
    hint: str      # o que fazer a respeito
    def to_dict(self) -> dict[str, str]: ...
```

```python
# fetch.py (A1)
@dataclass(frozen=True, slots=True)
class FetchResult:
    status: Literal["downloaded", "not_modified", "error"]
    path: Path | None          # zip no disco; None só em "error"
    etag: str | None
    last_modified: str | None
    sha256: str | None         # do conteúdo do zip (meta antiga em not_modified)
    bytes: int | None
    downloaded_at: str | None # ISO 8601 com offset (América/São_Paulo)
    error: SourceError | None

BROWSER_UA: str  # User-Agent de browser; SEM ele a B3 devolve 520/404 (medido)

def fetch_cotahist(
    year: int,
    dest_dir: Path,
    base_url: str,             # ex.: "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/"
    *,
    client: httpx.Client,
    retry_delays: tuple[float, ...] = (1.0, 2.0),   # entre tentativas; testes passam (0,0)
) -> FetchResult: ...
```

- URL: `f"{base_url}COTAHIST_A{year}.ZIP"` (note o `.ZIP` maiúsculo).
- Cache/meta: `dest_dir / f"COTAHIST_A{year}.ZIP.meta.json"` com chaves
  `{"etag", "last_modified", "sha256", "bytes", "downloaded_at"}` — lido antes do GET;
  se existir, enviar `If-None-Match` **e** `If-Modified-Since`.
- 304 → `not_modified` (não baixa, não reescreve meta, `path` = zip existente).
- 200 → baixa **em streaming** para `dest_dir/tmp-COTAHIST_A{year}.ZIP.part`,
  confere `Content-Length` (se vier), calcula sha256 no fluxo, `os.replace` para o
  nome final, grava meta nova. Falha no meio → apaga `.part`, não deixa meta inconsistente.
- Retry: só erro de transporte (`httpx.TransportError`) e 5xx; no máximo
  `1 + len(retry_delays)` tentativas; 4xx não re-tenta (permanente).
- Timeout do client: connect 10 s, read 90 s (quem cria o client define; o fetch
  apenas usa).
- Toda falha final → `SourceError(code="source_unavailable", message=…, hint=…)`
  dentro de `FetchResult(status="error")`. **Nunca exceção que escape da função.**

```python
# validate.py (A1)
@dataclass(frozen=True, slots=True)
class BadLine:
    line_no: int     # 1-based no arquivo
    kind: str        # "width" | "numeric" | "date" | "tipreg"
    excerpt: str     # ~80 primeiros chars

@dataclass(frozen=True, slots=True)
class ValidationReport:
    ok: bool                  # ver regra abaixo
    zip_path: Path
    sha256: str
    line_count: int           # linhas de dados ("01") lidas
    header_ok: bool          # primeira linha "00"
    trailer_total: int | None # inteiro do trailer [32:42]
    count_match: bool | None  # trailer_total == line_count; None se sem trailer
    bad_width: tuple[BadLine, ...]      # limitado às primeiras 20
    bad_numeric: tuple[BadLine, ...]     # idem
    errors: tuple[SourceError, ...]     # nível I/O (zip corrompido etc.)

def validate_cotahist(zip_path: Path) -> ValidationReport: ...
```

- `ok = header_ok and count_match is True and not bad_width and not bad_numeric and not errors`.
- **Coleta, não explode:** nenhuma linha ruim levanta exceção; tudo entra nas listas
  (limitadas às primeiras 20 por espécie) e o resultado decide o exit code.
- Zip com membro único; `sha256` do zip inteiro.
- Largura esperada: **245** para TODAS as linhas (00, 01, 99).
- Trailer: `tipreg == "99"`, total em `l[32:42]` (10 dígitos; medido: `0003570794`).
- Numéricos que devem parsear em toda linha "01": `open/high/low/avg/close/bid/ask`,
  `trades`, `qty`, `volume`, `strike`, `quote_factor`, `strike_pts`; datas: `date`
  válida; `maturity` válida **ou** `"99991231"`.

```python
# parse.py (A2)
SPOT_MARKETS = frozenset({"010", "020"})
OPTION_MARKETS = frozenset({"070", "080"})
EXERCISE_MARKETS = frozenset({"012", "013"})
KEPT_MARKETS = SPOT_MARKETS | OPTION_MARKETS | EXERCISE_MARKETS

@dataclass(frozen=True, slots=True)
class CotahistRow:
    refdate: date
    bdi: str
    ticker: str           # strip() sempre (campo right-aligned)
    tpmerc: str
    corp_name: str        # strip() — o 1º token define o estilo
    spec: str             # strip()
    open: int             # TODOS os preços: centavos int64 (2 decimais implícitos)
    high: int
    low: int
    avg: int
    close: int
    best_bid: int
    best_ask: int
    trades: int
    qty: int              # unidades negociadas (int64)
    volume: int           # centavos int64 — JÁ vem ÷FATCOT na fonte; guardar como está
    strike: int           # centavos; 0 quando não é opção
    strike_adj_ind: str   # [201:202], 1 char, como está na fonte
    maturity: date | None # None quando "99991231"
    quote_factor: int     # FATCOT
    strike_pts: int
    isin_underlying: str  # é do ATIVO-OBJETO, nunca da série (AGENTS §1)
    distribution_id: str
    style: str | None     # "A" | "E" | None (None = style_unknown)
    side: str | None      # "C" (070) | "P" (080) | None

def parse_line(line: str) -> CotahistRow | None: ...
    # None para header "00" e trailer "99".
    # SourceError em linha "01" estruturalmente impossível (largura ≠ 245,
    # numérico não-parseável): a validação global (A1) roda ANTES no prepare;
    # aqui é rede de segurança, falha explícita.

def iter_cotahist(zip_path: Path, *, markets: frozenset[str] = KEPT_MARKETS) -> Iterator[CotahistRow]: ...
    # streaming linha a linha; NUNCA o arquivo inteiro em memória (3,5 M linhas).

def style_of(ticker: str, corp_name: str) -> str | None: ...
    # regra medida — ver §4. Pure function, testável isolada.
```

## 3. Offsets validados (0 violações em 16.134 linhas de 19/11/2026)

`date[2:10]`, `bdi[10:12]`, `ticker[12:24]`, `market[24:27]`, `corp_name[27:39]`,
`spec[39:49]`, `open[56:69]`, `high[69:82]`, `low[82:95]`, `avg[95:108]`,
`close[108:121]`, `bid[121:134]`, `ask[134:147]`, `trades[147:152]`, `qty[152:170]`,
`volume[170:188]`, `strike[188:201]`, `strike_adj_ind[201:202]`, `maturity[202:210]`,
`quote_factor[210:217]`, `strike_pts[217:230]`, `isin[230:242]`, `dist_id[242:245]`.

Não usar `[49:52]` (lixo variável); `R$` mora em `[52:54]`. Trailer: total em `[32:42]`.
Encodings: latin-1, 245 bytes/linha, arquivo **não ordenado por data** (AGENTS §4).

## 4. Regra de estilo (medida no arquivo de 2026 inteiro)

```python
WEEKLY = re.compile(r"(?<=\d)W\d+$")   # marcador semanal só depois de dígito (strike)

def _series_root(ticker: str) -> str | None:
    t = WEEKLY.sub("", ticker)          # "ABEVA140W1" -> "ABEVA140"
    letters = t.rstrip("0123456789")   # "ABEVA140" -> "ABEVA" (código de mês fica)
    if len(letters) > 1:
        return letters[:-1]             # tira o código de mês: "ABEVA" -> "ABEV"
    return letters or None

def style_of(ticker: str, corp_name: str) -> str | None:
    parts = corp_name.split()
    token = parts[0] if parts else ""
    root = _series_root(ticker)
    if not token or not root or not token.startswith(root):
        return None                    # style_unknown
    if len(token) > len(root) + 2:
        return None
    return "E" if token.endswith("E") else "A"
```

Validação desta regra (22/11/2026, 3.060.453 linhas de opção):
puts 1.508.144 E e **zero** A; calls 818.364 A / 729.416 E; `None` em 0,15%
(só trimestrais de IBOV — fora do universo de ações). Sufixos `/EJ`, `/EDJ`, `/AT`,
`FM` no corp_name não afetam (só o 1º token entra). BRAVA cai sem caso especial
(`BRAVA`/`BRAVAE` = raiz `BRAV` +1/+2).

## 5. Golden values — 2026-11-19 (para os testes; conferidos contra o bruto)

| campo | PETR4 (010) | PETRK410 (070) | PETRM410 (080) |
|---|---|---|---|
| bdi | `02` | `78` | `82` |
| open..close (¢) | 4353/4360/4241/4291/4267 | 1300/1300/1300/1300/1300 | 2/3/2/2/3 |
| best_bid/best_ask (¢) | 4265/4267 | 1252/1359 | 0/0 |
| trades | 45389 | 1 | 2 |
| qty | 39230100 | 400 | 200 |
| volume (¢) | 168353821600 | 520000 | 500 |
| strike (¢) | 0 | 3569 | 3569 |
| maturity | None | 2026-11-19 | 2027-01-15 |
| quote_factor | 1 | 1 | 1 |
| isin_underlying | BRPETRACNPR6 | BRPETRACNOR9 | BRPETRACNOR9 |
| distribution_id | 230 | 208 | 208 |
| corp_name (token) | PETROBRAS | PETR | PETRE |
| style | None | `A` | `E` |
| side | None | `C` | `P` |

Sanidade à mão: PETR4 `volume = qty × avg` fecha exato (39.230.100 × 4291);
PETRM410 tem bid=ask=0 (caso `one_sided` futuramente).

## 6. Definição de pronto (dos dois pacotes)

1. `podman compose run --rm test pytest` verde e
   `podman compose run --rm test ruff check src tests` limpo.
2. Roda no container; nada de Python/uv no host.
3. Falha explícita em tudo: erro = `SourceError`; nunca exceção crua, nunca silêncio.
4. Nenhum arredondamento no parse; inteiros desde a origem.
5. Commit só dos seus arquivos; mensagem `feat(f0/…): …`.
