"""Posições em aberto por série (F0.8) — arquivo regulatório do BDI da B3.

Contrato: `docs/contratos/onda-b.md` §6 e spec §5.5 (`core.open_interest`). A
fonte não é o COTAHIST nem página pública: é a **tabela do BDI** da B3, exportada
como CSV (`OpenPositionsEquities`, "Posições em aberto", derivativos de renda
variável).

O que foi **medido** em 22/11/2026 no arquivo real de 19/11/2026 (35.380 linhas,
2.776.725 bytes) — não redescubra:

- `POST https://arquivos.b3.com.br/bdi/table/export/csv` com corpo JSON exato
  `{"Name":"OpenPositionsEquities","Date":"YYYY-MM-DD","FinalDate":"YYYY-MM-DD",
  "ClientId":"","Filters":null}` → 200 `text/csv`. O `User-Agent` de browser é
  obrigatório (o mesmo `BROWSER_UA` do COTAHIST).
- O CSV é **UTF-8 com BOM** (não latin-1: os acentos do header são multibyte), 18
  colunas, `;` como separador, `.` de milhar e `-` ou vazio como ausência. A
  leitura tenta `utf-8-sig` e cai para `latin-1` se não decodificar.
- O **preâmbulo não é fixo**: 19/11 tem 1 linha de descrição + 1 de glossário + 1
  vazia antes do header (linha 3); 22/11 (domingo, sem dado) tem o header na
  linha 0. Por isso o header é achado **pelo conteúdo** (`Instrumento
  financeiro`), nunca por offset.
- Data sem dado **não é erro**: 200 com o header e uma única linha `Nenhum
  resultado` (é o caso de sábado/domingo e de dia fora da janela) →
  `OIResult.available = False`, zero linhas, `error = None` (P-E/P-N).
- **`Contratos em aberto` vem vazio em 100% das opções de ação**: 19/11 tem
  33.366 linhas `EQUITY CALL`/`EQUITY PUT` (17.035 + 16.331) e **nenhuma** com
  valor nessa coluna. Ela só é preenchida para `FINANCIAL` (987),
  `AGRIBUSINESS` (664) e `EQUITY FORWARD` (363) — futuro e termo. Para opção, a
  posição vem em `Quantidade coberta` + `Total de posições bloqueadas` +
  `Quantidade descoberta` (= `Total de posições`, conferido em 4.250 linhas, zero
  divergência), na **unidade do ativo-objeto** (PETR: 100% dos totais são
  múltiplos de 100; PETRA462 tem 19.801.700 com 109.500 ações negociadas no dia).
  Daí `OIRow.oi` ser `int | None` e o campo extra `total_qty`: converter
  quantidade de ativo-objeto em contrato é do C1/F1 (precisa do tamanho do
  contrato), aqui não se inventa número.
- `Instrumento financeiro` é o ticker da série (`PETRPZ26`, `PETRA365`,
  `PETRA462`) — único por dia (zero duplicados em 19/11, zero ISIN duplicado).
  Opções sobre futuro/índice usam nome **sintético** (`BGIX26P035000`,
  `BGIX26C038000`): 1.386 linhas de 13 caracteres, todas de commodity/índice,
  fora do universo de ações.
- `Preço a termo` é o único campo com **vírgula decimal** (`339.060,6200`), só em
  `EQUITY FORWARD`; nenhuma das colunas que este módulo lê tem vírgula.
- A janela da fonte é curta (o `AGENTS.md` mede ~10–12 sessões): data fora dela
  devolve `Nenhum resultado`, que aqui vira `available = False`, não erro.

Regra 5 do `AGENTS.md`: falha explícita. Rede/tempo esgotado/parse torto →
`SourceError(code="source_unavailable")` carregado no `OIResult`; nada de exceção
escapando de `fetch_open_interest` (a única exceção que sai deste módulo é
`OSError` de gravação, convertido em `SourceError(code="storage_error")` pelo
próprio `fetch`, como faz `b3o.ingest.rates`).
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

import httpx
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from b3o.fsutil import replace_file
from b3o.ingest.errors import SourceError
from b3o.ingest.fetch import BROWSER_UA

log = logging.getLogger(__name__)

BDI_EXPORT_URL: Final = "https://arquivos.b3.com.br/bdi/table/export/csv"
BDI_TABLE_NAME: Final = "OpenPositionsEquities"

OI_TABLE: Final = "open_interest"
PARTITION_FILE: Final = "part-0.parquet"

#: Único `refdate` por partição de ano; `oi` é nullable porque a coluna vem vazia
#: em toda opção de ação (ver docstring do módulo).
OI_SCHEMA: Final = pa.schema(
    [
        pa.field("refdate", pa.date32(), nullable=False),
        pa.field("ticker", pa.string(), nullable=False),
        pa.field("oi", pa.int64(), nullable=True),
        pa.field("covered_qty", pa.int64(), nullable=True),
        pa.field("uncovered_qty", pa.int64(), nullable=True),
        pa.field("total_qty", pa.int64(), nullable=True),
    ]
)

#: Nomes das colunas como o arquivo os escreve (comparados sem acento/caixa).
COL_INSTRUMENT: Final = "instrumento financeiro"
COL_OI: Final = "contratos em aberto"
COL_COVERED: Final = "quantidade coberta"
COL_UNCOVERED: Final = "quantidade descoberta"
COL_TOTAL: Final = "total de posicoes"

#: Linha que o arquivo devolve quando a data não tem posição publicada.
NO_DATA_MARKER: Final = "nenhum resultado"

_HEADERS = {"User-Agent": BROWSER_UA, "Accept": "text/csv, */*"}

#: 2,7 MB por dia e leitura lenta às vezes: uma retentativa curta resolve e não
#: transforma a janela de 12 sessões em 12 erros (medido: um `ReadTimeout` com
#: `read=60 s` em 19/11/2026).
DEFAULT_RETRY_DELAYS: Final = (2.0, 5.0)

_HINT_NETWORK = (
    "confira rede/DNS do container e tente de novo; o BDI exige User-Agent de "
    "browser e o CSV do dia tem ~2,7 MB (use read ≥ 120 s no cliente httpx)"
)
_HINT_FORMAT = (
    "o export do BDI entrou com formato inesperado; a data sem dado devolve o "
    "header + a linha 'Nenhum resultado' (não um corpo vazio). Confira o export "
    "manual em https://arquivos.b3.com.br/bdi/tabelas antes de confiar no arquivo"
)
_HINT_STATUS = (
    "erro HTTP no export do BDI: fonte fora do ar ou URL mudou — confira "
    "https://arquivos.b3.com.br/bdi/tabelas. A data sem dado não é erro: ela "
    "responde 200 com 'Nenhum resultado'"
)

_INT_GROUPS = re.compile(r"^\d{1,3}(?:\.\d{3})*$|^\d+$")


@dataclass(frozen=True, slots=True)
class OIRow:
    """Posição em aberto de uma série em um dia.

    `oi` é a coluna `Contratos em aberto` do arquivo e é `None` em **toda** opção
    de ação (medido em 19/11/2026); `covered_qty`/`uncovered_qty`/`total_qty` são
    `Quantidade coberta`, `Quantidade descoberta` e `Total de posições`, na
    unidade do ativo-objeto. Os quatro são `None` quando o arquivo traz `-` ou
    vazio: nunca zero silencioso.
    """

    refdate: date
    ticker: str
    oi: int | None
    covered_qty: int | None
    uncovered_qty: int | None
    total_qty: int | None = None


@dataclass(frozen=True, slots=True)
class OIResult:
    """Resultado de **uma** data.

    `available = False` com `error = None` é o caminho normal de P-N: a fonte
    respondeu que não há posição publicada para o dia (`Nenhum resultado`) e o
    C1 marca `oi_unavailable`. `error` preenchido é falha de fonte (rede/parse)
    ou de disco, e `available` também é `False`.
    """

    refdate: date
    available: bool
    rows: tuple[OIRow, ...] = ()
    skipped_lines: int = 0
    duplicate_tickers: int = 0
    error: SourceError | None = None
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class OIWindowResult:
    """Resultado em lote (a janela curta do BDI, tipicamente 10–12 sessões).

    Uma data que falha não derruba a janela: ela entra em `errors` e as outras
    datas são gravadas. `dates_without_data` são as datas que a fonte respondeu
    como vazias — `oi = null` + `oi_unavailable` no dia, não erro (P-N).
    """

    rows: tuple[OIRow, ...]
    dates_with_data: tuple[date, ...]
    dates_without_data: tuple[date, ...]
    errors: tuple[SourceError, ...] = ()
    path: Path | None = None


def fetch_open_interest(
    date_ref: date,
    *,
    client: httpx.Client,
    data_dir: Path | None = None,
    retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
) -> OIResult:
    """Posições em aberto de `date_ref`; nunca levanta por falha de fonte.

    `client` é criado por quem chama (timeout de leitura ≥ 120 s, headers de
    borda). `retry_delays` tem uma entrada por re-tentativa; os testes passam
    `()` para não dormir. `data_dir` é opcional: quando vem, a partição do ano é
    gravada antes de retornar (o C1 pode preferir `fetch_open_interest_window`
    para pagar uma gravação só).
    """
    url = BDI_EXPORT_URL
    body = _request_body(date_ref)
    attempts = 1 + len(retry_delays)
    last_error: SourceError | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = client.post(url, json=body, headers=_HEADERS)
        except httpx.HTTPError as exc:
            last_error = SourceError(
                code="source_unavailable",
                message=(
                    f"falha de rede no BDI ({date_ref.isoformat()}): "
                    f"{type(exc).__name__}: {exc}"
                ),
                hint=_HINT_NETWORK,
            )
        except httpx.InvalidURL as exc:  # URL constante: só muda se o módulo for editado
            return _failed(
                date_ref,
                SourceError(
                    code="source_unavailable",
                    message=f"URL inválida no BDI: {exc}",
                    hint="confira BDI_EXPORT_URL em b3o.ingest.open_interest",
                ),
            )
        else:
            status = response.status_code
            if status == 200:
                result = parse_open_interest_csv(response.content, date_ref=date_ref)
                return _maybe_save(result, data_dir)
            if status >= 500 or status in (408, 425, 429):
                last_error = SourceError(
                    code="source_unavailable",
                    message=f"HTTP {status} no export do BDI ({date_ref.isoformat()})",
                    hint="a B3 está fora do ar ou limitando; re-tentar mais tarde",
                )
            else:
                return _failed(
                    date_ref,
                    SourceError(
                        code="source_unavailable",
                        message=f"HTTP {status} no export do BDI ({date_ref.isoformat()})",
                        hint=_HINT_STATUS,
                    ),
                )

        if attempt < attempts:
            delay = float(retry_delays[attempt - 1])
            log.warning(
                "OI %s: tentativa %d/%d falhou (%s); nova tentativa em %.1fs",
                date_ref,
                attempt,
                attempts,
                last_error.message if last_error is not None else "?",
                delay,
            )
            if delay > 0:
                time.sleep(delay)

    error = last_error or SourceError(
        code="source_unavailable",
        message=f"falha desconhecida no export do BDI ({date_ref.isoformat()})",
        hint=_HINT_NETWORK,
    )
    log.error("OI %s: desisti após %d tentativa(s): %s", date_ref, attempts, error.message)
    return _failed(date_ref, error)


def fetch_open_interest_window(
    dates: Iterable[date],
    *,
    client: httpx.Client,
    data_dir: Path | None = None,
    retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
) -> OIWindowResult:
    """Baixa várias datas e grava a partição **uma vez** no fim.

    A janela do BDI é curta e o `prepare` precisa de todas as sessões recentes:
    pagar `save_open_interest` por data significaria reler e reescrever o parquet
    do ano N vezes. Aqui as linhas de todas as datas disponíveis são concatenadas
    e gravadas juntas; data que falhar entra em `errors` e não impede as outras.
    """
    ordered = sorted(set(dates))
    rows: list[OIRow] = []
    with_data: list[date] = []
    without_data: list[date] = []
    errors: list[SourceError] = []

    for day in ordered:
        result = fetch_open_interest(day, client=client, retry_delays=retry_delays)
        if result.error is not None:
            errors.append(result.error)
            continue
        if not result.available:
            without_data.append(day)
            continue
        with_data.append(day)
        rows.extend(result.rows)

    path: Path | None = None
    if rows and data_dir is not None:
        try:
            path = save_open_interest(rows, data_dir)
        except OSError as exc:
            errors.append(_storage_error(exc, data_dir))

    if without_data:
        log.info("OI: sem dado publicado em %d data(s): %s", len(without_data), without_data)
    if errors:
        log.warning("OI: %d falha(s) na janela: %s", len(errors), [e.code for e in errors])

    return OIWindowResult(
        rows=tuple(rows),
        dates_with_data=tuple(with_data),
        dates_without_data=tuple(without_data),
        errors=tuple(errors),
        path=path,
    )


def parse_open_interest_csv(payload: bytes | str, *, date_ref: date) -> OIResult:
    """CSV do BDI → `OIResult` de uma data (puro: não toca rede nem disco).

    O header é achado pelo conteúdo e as colunas são lidas pelo **nome**, não por
    posição: o preâmbulo varia (3 linhas em 19/11, zero em 22/11) e nome de
    coluna é contrato mais estável que índice.
    """
    text = _decode(payload)
    if not text.strip():
        return _failed(
            date_ref,
            SourceError(
                code="source_unavailable",
                message=f"corpo vazio no export do BDI ({date_ref.isoformat()})",
                hint=_HINT_FORMAT,
            ),
        )

    lines = text.splitlines()
    header_at = _find_header(lines)
    if header_at is None:
        preview = next((line.strip() for line in lines if line.strip()), "")[:120]
        return _failed(
            date_ref,
            SourceError(
                code="source_unavailable",
                message=(
                    f"header 'Instrumento financeiro' não encontrado no CSV do BDI "
                    f"({date_ref.isoformat()}); início do corpo: {preview!r}"
                ),
                hint=_HINT_FORMAT,
            ),
        )

    columns = _column_index(lines[header_at])
    missing = [
        name
        for name in (COL_INSTRUMENT, COL_OI, COL_COVERED, COL_UNCOVERED)
        if name not in columns
    ]
    if missing:
        return _failed(
            date_ref,
            SourceError(
                code="source_unavailable",
                message=(
                    f"coluna(s) {missing} ausente(s) no CSV do BDI "
                    f"({date_ref.isoformat()}); header lido: {lines[header_at][:200]!r}"
                ),
                hint=_HINT_FORMAT,
            ),
        )

    rows: list[OIRow] = []
    seen: set[str] = set()
    skipped = 0
    duplicates = 0
    no_data = False
    needed = max(columns[name] for name in (COL_INSTRUMENT, COL_OI, COL_COVERED, COL_UNCOVERED))
    needed = max(needed, columns.get(COL_TOTAL, 0))

    for line in lines[header_at + 1 :]:
        if not line.strip():
            continue
        fields = line.split(";")
        if len(fields) == 1 and _norm(fields[0]) == NO_DATA_MARKER:
            no_data = True
            continue
        if len(fields) <= needed:
            skipped += 1
            log.warning(
                "OI %s: linha com %d campo(s), esperado ≥ %d; pulada: %r",
                date_ref,
                len(fields),
                needed + 1,
                line[:120],
            )
            continue
        ticker = fields[columns[COL_INSTRUMENT]].strip()
        try:
            oi = _parse_qty(fields[columns[COL_OI]])
            covered = _parse_qty(fields[columns[COL_COVERED]])
            uncovered = _parse_qty(fields[columns[COL_UNCOVERED]])
            total = _parse_qty(fields[columns[COL_TOTAL]]) if COL_TOTAL in columns else None
        except ValueError as exc:
            skipped += 1
            log.warning("OI %s: linha ilegível pulada (%s): %r", date_ref, exc, line[:120])
            continue
        if not ticker:
            skipped += 1
            log.warning("OI %s: linha sem instrumento pulada: %r", date_ref, line[:120])
            continue
        if ticker in seen:
            # (refdate, ticker) é a chave de junção do C1: duplicar aqui viraria
            # fan-out silencioso no join.
            duplicates += 1
            log.warning("OI %s: %s repetido; mantida a primeira linha", date_ref, ticker)
            continue
        seen.add(ticker)
        rows.append(
            OIRow(
                refdate=date_ref,
                ticker=ticker,
                oi=oi,
                covered_qty=covered,
                uncovered_qty=uncovered,
                total_qty=total,
            )
        )

    if not rows and not no_data:
        detail = (
            f"{skipped} linha(s) de dado, todas ilegíveis e puladas"
            if skipped
            else "nenhuma linha de dado nem 'Nenhum resultado'"
        )
        return OIResult(
            refdate=date_ref,
            available=False,
            rows=(),
            skipped_lines=skipped,
            error=SourceError(
                code="source_unavailable",
                message=f"CSV do BDI ({date_ref.isoformat()}) com header mas {detail}",
                hint=_HINT_FORMAT,
            ),
        )

    if no_data and rows:
        log.warning(
            "OI %s: 'Nenhum resultado' junto com %d linha(s) de dado; valem as linhas",
            date_ref,
            len(rows),
        )

    log.info(
        "OI %s: %d série(s)%s%s",
        date_ref,
        len(rows),
        f", {skipped} linha(s) pulada(s)" if skipped else "",
        ", sem dado publicado" if not rows else "",
    )
    return OIResult(
        refdate=date_ref,
        available=bool(rows),
        rows=tuple(rows),
        skipped_lines=skipped,
        duplicate_tickers=duplicates,
    )


def open_interest_path(data_dir: Path, year: int) -> Path:
    """Caminho da partição do ano: `<data_dir>/open_interest/year=YYYY/part-0.parquet`."""
    return Path(data_dir) / OI_TABLE / f"year={year}" / PARTITION_FILE


def save_open_interest(rows: Iterable[OIRow], data_dir: Path) -> Path | None:
    """Grava/atualiza as partições por ano (atômico: `tmp` → `b3o.fsutil.replace_file`).

    O lote substitui as linhas do(s) seu(s) `refdate`(s) e preserva o resto da
    partição: chamar duas vezes para o mesmo dia dá o mesmo conteúdo
    (idempotência) e chamar para um dia novo acrescenta — o `prepare` roda por
    janela de sessões, não reconstrói o ano como o COTAHIST.

    A publicação usa `replace_file` porque no bind mount do Windows `os.replace`
    por cima de arquivo existente é negado com `PermissionError` (medido
    22/11/2026): sem o fallback a segunda gravação de uma partição morreria.

    Devolve o caminho da última partição escrita, ou `None` se o lote estava
    vazio (dia sem dado: nada a gravar, não é erro).
    """
    batch = tuple(rows)
    if not batch:
        return None

    by_year: dict[int, list[OIRow]] = {}
    for row in batch:
        by_year.setdefault(row.refdate.year, []).append(row)

    written: Path | None = None
    for year in sorted(by_year):
        year_rows = by_year[year]
        refdates = {row.refdate for row in year_rows}
        target = open_interest_path(data_dir, year)
        new = _rows_table(year_rows)

        if target.is_file():
            try:
                old = pq.read_table(target)
                keep = pc.invert(
                    pc.is_in(old.column("refdate"), value_set=pa.array(sorted(refdates)))
                )
                table = pa.concat_tables([old.filter(keep), new])
            except (OSError, pa.ArrowException) as exc:
                raise OSError(
                    f"partição existente ilegível em {target} ({exc}); "
                    f"apague data/{OI_TABLE} e rode o prepare de novo"
                ) from exc
        else:
            table = new

        table = table.sort_by([("refdate", "ascending"), ("ticker", "ascending")])
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.tmp")
        try:
            pq.write_table(table, tmp, compression="snappy")
            stored = pq.read_metadata(tmp).num_rows
            if stored != table.num_rows:
                raise OSError(f"parquet gravado tem {stored} linhas, esperado {table.num_rows}")
            replace_file(tmp, target)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        written = target
        log.info("OI year=%d: %d linha(s) em %s", year, len(year_rows), target)
    return written


def read_open_interest(data_dir: Path, years: Iterable[int] | None = None) -> Iterator[OIRow]:
    """Lê as partições (streaming por parquet, sem duckdb fora de `store/`)."""
    root = Path(data_dir) / OI_TABLE
    if not root.is_dir():
        return
    wanted = None if years is None else {int(year) for year in years}
    for directory in sorted(root.glob("year=*")):
        try:
            year = int(directory.name.split("=", 1)[1])
        except (IndexError, ValueError):
            log.warning("OI: partição com nome inesperado ignorada: %s", directory)
            continue
        if wanted is not None and year not in wanted:
            continue
        for path in sorted(directory.glob("*.parquet")):
            for batch in pq.ParquetFile(path).iter_batches(batch_size=100_000):
                yield from _batch_rows(batch)


def _batch_rows(batch: pa.RecordBatch) -> Iterator[OIRow]:
    """`RecordBatch` do parquet → `OIRow` (nullable preservado)."""
    data = batch.to_pydict()
    for index in range(batch.num_rows):
        yield OIRow(
            refdate=data["refdate"][index],
            ticker=data["ticker"][index],
            oi=data["oi"][index],
            covered_qty=data["covered_qty"][index],
            uncovered_qty=data["uncovered_qty"][index],
            total_qty=data["total_qty"][index],
        )


def _request_body(date_ref: date) -> dict[str, object]:
    """Corpo JSON exato do export (ordem das chaves é a que a B3 espera)."""
    day = date_ref.strftime("%Y-%m-%d")
    return {
        "Name": BDI_TABLE_NAME,
        "Date": day,
        "FinalDate": day,
        "ClientId": "",
        "Filters": None,
    }


def _decode(payload: bytes | str) -> str:
    """UTF-8 com BOM (medido) e, se não decodificar, latin-1 (o contrato supunha)."""
    if isinstance(payload, str):
        return payload.lstrip("\ufeff")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        log.warning("OI: corpo não é UTF-8; decodificando como latin-1")
        return payload.decode("latin-1").lstrip("\ufeff")


def _find_header(lines: Sequence[str]) -> int | None:
    """Índice da linha de header, achada pelo primeiro campo (preâmbulo varia)."""
    for index, line in enumerate(lines):
        if ";" not in line:
            continue
        if _norm(line.split(";", 1)[0]) == COL_INSTRUMENT:
            return index
    return None


def _column_index(header: str) -> dict[str, int]:
    """Header normalizado (sem acento/caixa) → índice; primeira ocorrência vence."""
    columns: dict[str, int] = {}
    for index, name in enumerate(header.split(";")):
        key = _norm(name)
        if key and key not in columns:
            columns[key] = index
    return columns


def _norm(text: str) -> str:
    """Minúsculas, sem acento e sem espaço duplicado — para casar nome de coluna."""
    stripped = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(char for char in stripped if not unicodedata.combining(char))
    return " ".join(ascii_only.casefold().split())


def _parse_qty(text: str) -> int | None:
    """`12.651.200` → 12651200; `-`/vazio → None; ambíguo → `ValueError`.

    `.` é separador de milhar (medido). Um ponto que não fecha grupo de 3 dígitos
    (`12.65`) seria decimal e viraria número errado — por isso é erro explícito,
    nunca palpite.
    """
    value = text.strip()
    if value in ("", "-"):
        return None
    sign = ""
    digits = value
    if digits.startswith(("+", "-")):
        sign, digits = digits[0], digits[1:]
    if not _INT_GROUPS.fullmatch(digits):
        raise ValueError(f"quantidade ilegível: {value!r}")
    return int(sign + digits.replace(".", ""))


def _rows_table(rows: Sequence[OIRow]) -> pa.Table:
    return pa.Table.from_pydict(
        {
            "refdate": pa.array([row.refdate for row in rows], type=pa.date32()),
            "ticker": pa.array([row.ticker for row in rows], type=pa.string()),
            "oi": pa.array([row.oi for row in rows], type=pa.int64()),
            "covered_qty": pa.array([row.covered_qty for row in rows], type=pa.int64()),
            "uncovered_qty": pa.array([row.uncovered_qty for row in rows], type=pa.int64()),
            "total_qty": pa.array([row.total_qty for row in rows], type=pa.int64()),
        },
        schema=OI_SCHEMA,
    )


def _maybe_save(result: OIResult, data_dir: Path | None) -> OIResult:
    """Grava a partição quando o chamador pediu; falha de disco não perde as linhas."""
    if data_dir is None or not result.available:
        return result
    try:
        path = save_open_interest(result.rows, data_dir)
    except OSError as exc:
        return OIResult(
            refdate=result.refdate,
            available=result.available,
            rows=result.rows,
            skipped_lines=result.skipped_lines,
            duplicate_tickers=result.duplicate_tickers,
            error=_storage_error(exc, data_dir),
            path=None,
        )
    return OIResult(
        refdate=result.refdate,
        available=result.available,
        rows=result.rows,
        skipped_lines=result.skipped_lines,
        duplicate_tickers=result.duplicate_tickers,
        error=None,
        path=path,
    )


def _storage_error(exc: OSError, data_dir: Path) -> SourceError:
    return SourceError(
        code="storage_error",
        message=f"posições em aberto obtidas mas não gravadas em {data_dir}: {exc}",
        hint=(
            "confira espaço/permissão do volume de dados do container (B3_DATA_DIR); "
            "as linhas em memória continuam válidas"
        ),
    )


def _failed(date_ref: date, error: SourceError) -> OIResult:
    """Resultado de falha: zero linhas, `available = False`, erro com hint."""
    return OIResult(refdate=date_ref, available=False, rows=(), error=error)
