"""Curva DI por dia útil (F0.9) — cascata com `rate_source` sempre gravado.

Contrato: `docs/contratos/onda-b.md` §4, spec §5.5 (`core.rates_di`) e §6.0
(`r` = taxa efetiva anual, base 252). A cascata existe por P-M: curva ausente
**não** bloqueia o `prepare`, vira fallback com `rate_source` explícito.

Ordem (a primeira que responder com dado vence; a fonte fica no `source` de
cada ponto):

1. `b3_reference` — "DI x pré" (produto `PRE`) do portal de taxas referenciais
   da B3.
2. `di1_futures` — ajustes de DI1 (produto `APR`), mesmo endpoint e mesma
   janela; é a curva de futuros, um degrau abaixo da referencial.
3. `selic_flat` — Selic diária do SGS 11 do BCB, anualizada e repetida em todos
   os dias úteis até 3 anos: é o único fallback com cobertura histórica
   completa (serve ao backfill 2024–2026).

Unidade — **`RatePoint.rate_annual` é % a.a.** (`13.65` = 13,65% a.a.), como
diz o contrato §4 e como as duas fontes da B3 entregam o campo `Preço/Taxa`. O
SGS devolve % **a.d.**, então a anualização é
`((1 + valor/100)**252 - 1) * 100` — o ×100 é obrigatório: uma cascata em que
uma fonte está em fração e outra em porcento é bug silencioso de 100×. Quem
precisa de fração (`DF = exp(-r·T)` do §6.0) divide por 100 no F1; aqui não se
arredonda nem se interpola (interpolação em log-DF é do F1, §6).

Medições de 22/11/2026 (não redescubra):

- o corpo é o CSV **em base64** (latin-1 por dentro, CRLF, vírgula decimal),
  com `Descrição da Taxa;Dias Úteis;Dias Corridos;Preço/Taxa` no header;
- `Dias Corridos` não é 7 × `Dias Úteis` nem derivável dele (19/11/2026: du 1 →
  4 dias corridos, du 14 → 21); só `Dias Úteis` entra na curva;
- data fora da janela ou feriado devolve **200 com corpo vazio** (não 404, não
  erro): vazio é "sem dado" e o fluxo cascateia;
- a janela de `Search/GetDate/{b64}` tem exatamente **20 sessões** (19/11/2026 →
  22/10/2026); o SGS 11 só tem ponto em dia útil, daí a regra do último valor
  anterior (ver `_fetch_selic_flat`).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from b3o.ingest.errors import SourceError, SourceFailure
from b3o.ingest.fetch import BROWSER_UA

log = logging.getLogger(__name__)

B3_RATES_BASE_URL = "https://sistemaswebb3-derivativos.b3.com.br/referenceRatesProxy"
SGS_SELIC_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados"

SOURCE_B3_REFERENCE = "b3_reference"
SOURCE_DI1_FUTURES = "di1_futures"
SOURCE_SELIC_FLAT = "selic_flat"

PRODUCT_B3_REFERENCE = "PRE"  # "DI x pré"
PRODUCT_DI1_FUTURES = "APR"  # "Ajuste pré" (ajustes de DI1)

#: Horizonte do fallback plano, em dias úteis (~3 anos): a Selic é uma taxa só,
#: então o dia inteiro sai com o mesmo valor — o grid evita que o F1 extrapole
#: fora dos nós (nenhum vencimento de opção de ação passa de ~3 anos).
FLAT_HORIZON_DU = 756

#: Ponto do SGS é único por dia útil; feriado/fim de semana não existe linha.
#: Buscar 30 dias para trás cobre o maior vão possível (feriadão + reunião).
SGS_LOOKBACK_DAYS = 30

RATES_TABLE = "rates_di"
PARTITION_FILE = "part-0.parquet"

RATES_SCHEMA = pa.schema(
    [
        pa.field("refdate", pa.date32(), nullable=False),
        pa.field("du", pa.int32(), nullable=False),
        pa.field("rate_annual", pa.float64(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
    ]
)

_HINT_NETWORK = (
    "confira rede/DNS do container e tente de novo; os dois endpoints exigem "
    "User-Agent de browser (sem ele a B3 responde 520/404)"
)
_HINT_CSV = (
    "o proxy devolve o CSV em base64 (latin-1, ';', vírgula decimal); se o "
    "formato mudou, o parser precisa acompanhar antes de confiar na curva"
)
_HINT_B3_STATUS = (
    "sessão fora da janela (~20 sessões) devolve 200 vazio e cascateia; erro "
    "HTTP aqui é fonte fora do ar ou URL do proxy mudou — confira o portal de "
    "taxas referenciais da B3"
)


@dataclass(frozen=True, slots=True)
class RatePoint:
    """Um nó da curva: `rate_annual` em **% a.a.** efetiva, base 252."""

    refdate: date
    du: int
    rate_annual: float
    source: str


@dataclass(frozen=True, slots=True)
class RatesResult:
    """Resultado da cascata; `error` só aparece quando TODAS as fontes falharam.

    `points` vazio + `source_final = None` + `error` preenchido é o caminho de
    P-M: o dia fica com `r = null` e `rate_source = null` no health, nunca zero.
    `path` é a partição gravada (None quando não havia o que gravar).
    """

    points: tuple[RatePoint, ...]
    source_final: str | None
    error: SourceError | None
    path: Path | None = None


def fetch_rates_di(date_ref: date, *, client: httpx.Client, data_dir: Path) -> RatesResult:
    """Busca a curva DI de `date_ref` na cascata e grava a partição do ano.

    `client` é criado por quem chama (timeout, retry e headers de borda são
    dela). `date_ref` vem por parâmetro — nunca do relógio (P-C).
    """
    attempts: list[str] = []
    points: tuple[RatePoint, ...] = ()
    source_final: str | None = None

    for source, product in (
        (SOURCE_B3_REFERENCE, PRODUCT_B3_REFERENCE),
        (SOURCE_DI1_FUTURES, PRODUCT_DI1_FUTURES),
    ):
        try:
            found = _fetch_b3_curve(client, date_ref, product_id=product, source=source)
        except SourceFailure as exc:
            attempts.append(f"{source}: {exc.error.message}")
            log.warning("curva DI %s: %s indisponível (%s)", date_ref, source, exc.message)
            continue
        if found:
            points, source_final = found, source
            log.info("curva DI %s: %d nós de %s", date_ref, len(found), source)
            break
        attempts.append(f"{source}: sem dado para a data")

    if source_final is None:
        try:
            points = _fetch_selic_flat(client, date_ref)
            source_final = SOURCE_SELIC_FLAT
            log.warning(
                "curva DI %s: caiu para %s (%d nós planos a %.4f%% a.a.)",
                date_ref,
                SOURCE_SELIC_FLAT,
                len(points),
                points[0].rate_annual,
            )
        except SourceFailure as exc:
            attempts.append(f"{SOURCE_SELIC_FLAT}: {exc.error.message}")
            log.error("curva DI %s: todas as fontes falharam", date_ref)
            return RatesResult(
                points=(),
                source_final=None,
                error=_all_failed_error(date_ref, attempts),
            )

    try:
        path = write_rates_partition(points, data_dir)
    except OSError as exc:
        # A curva em memória vale; o disco é problema local, não da fonte.
        log.error("curva DI %s: não foi possível gravar a partição: %s", date_ref, exc)
        return RatesResult(
            points=points,
            source_final=source_final,
            error=SourceError(
                code="storage_error",
                message=f"curva DI de {date_ref.isoformat()} obtida mas não gravada: {exc}",
                hint=(
                    "confira espaço/permissão do volume de dados do container "
                    "(B3_DATA_DIR); a curva em memória continua válida"
                ),
            ),
            path=None,
        )
    log.info("curva DI %s: %d nós gravados em %s", date_ref, len(points), path)
    return RatesResult(points=points, source_final=source_final, error=None, path=path)


def write_rates_partition(points: Iterable[RatePoint], data_dir: Path) -> Path:
    """Grava/atualiza `data_dir/rates_di/year=YYYY/part-0.parquet` (atômico).

    A partição do ano é reconstruída a partir do que já está lá: as linhas do
    `refdate` do lote são substituídas e o resto é preservado — chamar duas
    vezes para o mesmo dia dá o mesmo arquivo (idempotência), chamar para um
    dia novo acrescenta (o `prepare` roda por dia, não por ano, ao contrário do
    COTAHIST).
    """
    batch = tuple(points)
    if not batch:
        raise ValueError("nada para gravar: a cascata devolveu zero nós")
    refdates = {point.refdate for point in batch}
    if len(refdates) != 1:
        raise ValueError(f"um lote por dia: recebi {len(refdates)} datas distintas")

    refdate = batch[0].refdate
    target = Path(data_dir) / RATES_TABLE / f"year={refdate.year}" / PARTITION_FILE
    new = _points_table(batch)

    if target.is_file():
        try:
            old = pq.read_table(target)
            keep = pc.invert(pc.equal(old.column("refdate"), pa.scalar(refdate, pa.date32())))
            table = pa.concat_tables([old.filter(keep), new])
        except (OSError, pa.ArrowException) as exc:
            raise OSError(
                f"partição existente ilegível em {target} ({exc}); "
                "apague data/rates_di e rode o prepare de novo"
            ) from exc
    else:
        table = new

    table = table.sort_by([("refdate", "ascending"), ("du", "ascending")])

    directory = target.parent
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f"{target.name}.tmp"
    try:
        pq.write_table(table, tmp, compression="snappy")
        written = pq.read_metadata(tmp).num_rows
        if written != table.num_rows:
            raise OSError(f"parquet gravado tem {written} linhas, esperado {table.num_rows}")
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return target


def _fetch_b3_curve(
    client: httpx.Client, date_ref: date, *, product_id: str, source: str
) -> tuple[RatePoint, ...] | None:
    """Curva de um produto do proxy da B3; `None` = a data não tem dado (cascata)."""
    payload = _encode_payload(
        {"language": "pt-br", "date": date_ref.isoformat(), "id": product_id}
    )
    url = f"{B3_RATES_BASE_URL}/Search/GetDownloadFile/{payload}"
    response = _get(client, url, source=source)

    # 404/410 também é "sem dado" — o contrato manda cascatear, não falhar.
    if response.status_code in (404, 410):
        log.info("%s: sem arquivo para %s (HTTP %d)", source, date_ref, response.status_code)
        return None
    if response.status_code >= 400:
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=f"HTTP {response.status_code} em {url}",
                hint=_HINT_B3_STATUS,
            )
        )

    text = _decode_base64_payload(response.content, source=source)
    if not text.strip():
        log.info("%s: corpo vazio para %s (fora da janela de ~20 sessões?)", source, date_ref)
        return None

    points = _parse_reference_csv(text, refdate=date_ref, source=source)
    if not points:
        log.info("%s: CSV sem nenhum nó para %s", source, date_ref)
        return None
    return points


def _fetch_selic_flat(client: httpx.Client, date_ref: date) -> tuple[RatePoint, ...]:
    """Selic do SGS 11 anualizada, repetida em todos os dias úteis do horizonte.

    **Decisão registrada:** o SGS só publica em dia útil — sábado, domingo e
    feriado não têm linha. Se `date_ref` não tem ponto exato, usa-se o **último
    valor anterior** ao dia (a Selic meta é constante entre reuniões do Copom,
    e o SGS republica o mesmo valor em todo dia útil do intervalo), e o nó sai
    com `refdate = date_ref` mesmo assim: a data pedida é a data da curva.
    """
    start = date_ref - timedelta(days=SGS_LOOKBACK_DAYS)
    params = {
        "formato": "json",
        "dataInicial": start.strftime("%d/%m/%Y"),
        "dataFinal": date_ref.strftime("%d/%m/%Y"),
    }
    response = _get(client, SGS_SELIC_URL, source=SOURCE_SELIC_FLAT, params=params)
    if response.status_code >= 400:
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=f"HTTP {response.status_code} em {SGS_SELIC_URL}",
                hint=(
                    "a série 11 do SGS cobre qualquer data histórica; erro HTTP "
                    "aqui é BCB fora do ar ou URL da série mudou"
                ),
            )
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=f"resposta do SGS 11 não é JSON: {exc}",
                hint="confira https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados",
            )
        ) from exc
    if not isinstance(payload, list):
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=f"SGS 11 devolveu {type(payload).__name__}, esperado lista",
                hint="a API do BCB mudou de forma; confira o endpoint antes de confiar",
            )
        )

    latest: tuple[date, float] | None = None
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            when = datetime.strptime(str(item.get("data", "")), "%d/%m/%Y").date()
            daily_pct = _parse_br_float(str(item.get("valor", "")))
        except ValueError:
            continue
        if when <= date_ref and (latest is None or when > latest[0]):
            latest = (when, daily_pct)

    if latest is None:
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=(
                    f"SGS 11 sem Selic entre {start.isoformat()} e {date_ref.isoformat()}"
                ),
                hint=(
                    "o SGS 11 é diário e histórico; lista vazia significa data "
                    "fora da série ou endpoint errado"
                ),
            )
        )

    when, daily_pct = latest
    if when != date_ref:
        log.info(
            "SGS 11: sem ponto em %s; usando o último valor anterior (%s) — Selic é "
            "constante entre reuniões",
            date_ref,
            when,
        )
    rate_annual = ((1.0 + daily_pct / 100.0) ** 252 - 1.0) * 100.0
    return tuple(
        RatePoint(refdate=date_ref, du=du, rate_annual=rate_annual, source=SOURCE_SELIC_FLAT)
        for du in range(1, FLAT_HORIZON_DU + 1)
    )


def _get(
    client: httpx.Client,
    url: str,
    *,
    source: str,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """GET com o erro de transporte convertido em `SourceFailure` (nunca escapa)."""
    headers = {"User-Agent": BROWSER_UA, "Accept": "*/*"}
    try:
        return client.get(url, params=params, headers=headers)
    except httpx.InvalidURL as exc:
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=f"URL inválida para {source}: {exc}",
                hint="confira a constante do endpoint no módulo b3o.ingest.rates",
            )
        ) from exc
    except httpx.HTTPError as exc:
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=f"falha de rede em {source} ({url}): {type(exc).__name__}: {exc}",
                hint=_HINT_NETWORK,
            )
        ) from exc


def _encode_payload(payload: dict[str, str]) -> str:
    """b64 do JSON que o proxy da B3 espera no caminho da URL."""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_base64_payload(raw: bytes, *, source: str) -> str:
    """Decodifica o corpo (base64 → CSV latin-1); vazio devolve string vazia."""
    compact = b"".join(raw.split())
    if not compact:
        return ""
    try:
        blob = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=f"resposta de {source} não é base64 válido: {exc}",
                hint=_HINT_CSV,
            )
        ) from exc
    return blob.decode("latin-1")


def _parse_reference_csv(text: str, *, refdate: date, source: str) -> tuple[RatePoint, ...]:
    """CSV `Descrição da Taxa;Dias Úteis;Dias Corridos;Preço/Taxa` → nós da curva."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ()
    header = lines[0]
    if "dias" not in header.casefold():
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=f"header inesperado em {source}: {header[:80]!r}",
                hint=_HINT_CSV,
            )
        )

    by_du: dict[int, RatePoint] = {}
    duplicates = 0
    for line in lines[1:]:
        fields = line.split(";")
        if len(fields) < 4:
            raise _row_error(source, line, f"{len(fields)} campo(s), esperado 4")
        try:
            du = int(fields[1].strip())
            rate_annual = _parse_br_float(fields[3])
        except ValueError as exc:
            raise _row_error(source, line, str(exc)) from exc
        if du <= 0:
            raise _row_error(source, line, f"dias úteis {du} não é nó de curva")
        if du in by_du:
            duplicates += 1
            continue
        by_du[du] = RatePoint(
            refdate=refdate, du=du, rate_annual=rate_annual, source=source
        )

    if duplicates:
        log.warning("%s: %d linha(s) repetida(s) de dias úteis ignorada(s)", source, duplicates)
    return tuple(by_du[du] for du in sorted(by_du))


def _row_error(source: str, line: str, detail: str) -> SourceFailure:
    return SourceFailure(
        SourceError(
            code="source_unavailable",
            message=f"linha ilegível em {source} ({detail}): {line[:80]!r}",
            hint=_HINT_CSV,
        )
    )


def _parse_br_float(text: str) -> float:
    """Número em formato brasileiro (`13,65`; `1.234,56`) → float finito."""
    value = text.strip()
    if not value:
        raise ValueError("valor vazio")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"valor não finito ({text!r})")
    return number


def _points_table(points: tuple[RatePoint, ...]) -> pa.Table:
    return pa.Table.from_pydict(
        {
            "refdate": pa.array([p.refdate for p in points], type=pa.date32()),
            "du": pa.array([p.du for p in points], type=pa.int32()),
            "rate_annual": pa.array([p.rate_annual for p in points], type=pa.float64()),
            "source": pa.array([p.source for p in points], type=pa.string()),
        },
        schema=RATES_SCHEMA,
    )


def _all_failed_error(date_ref: date, attempts: list[str]) -> SourceError:
    detail = "; ".join(attempts) if attempts else "nenhuma fonte consultada"
    return SourceError(
        code="source_unavailable",
        message=f"sem curva DI para {date_ref.isoformat()}: {detail}",
        hint=(
            "as taxas referenciais da B3 guardam ~20 sessões (Search/GetDate) e o "
            "SGS 11 cobre qualquer data; confira rede/DNS e rode o prepare de novo. "
            "O dia fica com r = null e rate_source = null no health (P-M) — nunca zero"
        ),
    )
