"""CLI b3o — `prepare` (pipeline one-shot), `health` (saúde do lake) e `rates`.

Contrato: `docs/contratos/onda-c.md` §3 e §5. O `prepare` costura as ondas A e B
num fluxo reproduzível:

```
config → client → COTAHIST → validate → parse (contando por mercado, D3) → lake
       → legs + strike_events → rates → earnings → OI → liquidez → relatório
       → ingest_state → resumo de uma tela (P-G)
```

`prepare --year` de um ano fechado é **honesto** (§4.4): a partição daquele ano é
reconstruída, mas `last_session` continua sendo a maior data do lake inteiro e
`counts_by_date`/`counts_by_market` são mesclados — preparar 2025 depois de 2026
não apaga o retrato de 2026. Identidade do arquivo (`etag`/`sha256`/`trailer`/
`skipped_lines`) só é gravada quando o ano preparado é o mais novo do lake.

`rates --backfill`: preenche a Selic plana (`ingest/selic.py`, uma chamada ao SGS
11) nas sessões do lake sem curva; a curva PRE da B3 tem precedência.

Regras que este arquivo respeita:

- **cada fonte falha independente** (spec §4): sem rates/earnings/OI o prepare
  SEGUE e o `health` acusa (P-E/P-M/P-N). Só "sem COTAHIST **e** sem lake" sai
  não-zero;
- **o client httpx nasce aqui** (D2): `httpx.Timeout(connect=10, read=120)` +
  `BROWSER_UA` + `follow_redirects` — `read=120` porque o servidor de OI estoura
  leitura com 60 s (medido no B5). O timeout é afirmado no teste;
- **D3**: o parse conta as linhas `01` **por mercado** — inclusive os mercados
  descartados (`017`, `021`, `030`…), que o parser filtra antes de entregar. A
  contagem vem de uma passada barata sobre os bytes do zip e vai para o
  `prepare_state.json`; ADR-007 sem contagem é silêncio disfarçado;
- **a data de referência vem do arquivo** (P-C): a sessão e o ano de referência
  saem do COTAHIST/lake. O relógio só escolhe **qual arquivo anual** baixar,
  carimba `updated_at` e agenda o `--watch` (exceção ADR-010, marcada no código);
- **escrita atômica**: legs, strike_events e o relatório passam por
  `tmp/` + `fsutil.replace_file` (bind do Windows nega `os.replace` sobre
  arquivo existente);
- `--watch`: roda e dorme até as próximas 21:30 de `America/Sao_Paulo`
  (offset fixo −03:00 quando não há tzdata — sem horário de verão desde 2019).
"""

from __future__ import annotations

import json
import logging
import resource
import time
import uuid
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import typer

from b3o.config import Config, load_config
from b3o.derive.legs import build_legs
from b3o.derive.liquidity import (
    DAYS_WINDOW,
    TRADES_WINDOW,
    build_liquidity,
    render_report,
)
from b3o.derive.strike_events import classify_strike_events
from b3o.fsutil import replace_file
from b3o.ingest.earnings import build_earnings, fetch_earnings, write_earnings
from b3o.ingest.errors import SourceError, SourceFailure
from b3o.ingest.fetch import BROWSER_UA, FetchResult, fetch_cotahist
from b3o.ingest.open_interest import fetch_open_interest_window
from b3o.ingest.parse import KEPT_MARKETS, CotahistRow, iter_cotahist
from b3o.ingest.rates import fetch_rates_di
from b3o.ingest.selic import backfill_selic_flat
from b3o.ingest.validate import ValidationReport, validate_cotahist
from b3o.metrics.pipeline import run_metrics
from b3o.query.service import PrepareState, QueryService, load_prepare_state, save_prepare_state
from b3o.store.lake import read_cotahist_years, write_cotahist_year
from b3o.store.state import load_ingest_state, skipped_lines_from_report, update_ingest_state

app = typer.Typer(
    help="Opções B3 — dados EOD públicos da B3, cálculo em Python, decisão no humano.",
    no_args_is_help=True,
)

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #

CONNECT_TIMEOUT_S = 10.0
"""Timeout de conexão (D2)."""

READ_TIMEOUT_S = 120.0
"""Timeout de leitura (D2): o servidor de OI estoura 60 s e já deu 170 s sob carga."""

DEFAULT_CONFIG_PATH = Path("config.yaml")
"""`config.yaml` na raiz (montado em `/app/config.yaml` na imagem)."""

WATCH_HOUR = 21
WATCH_MINUTE = 30
"""Horário do `prepare` diário em `America/Sao_Paulo` (spec §11)."""

OI_WINDOW_SESSIONS = 10
"""Quantas sessões recentes do lake entram na janela do OI (a fonte guarda 20)."""

LIQUIDITY_SESSIONS = DAYS_WINDOW + TRADES_WINDOW
"""Recorte lido do lake para a liquidez: 20 sessões + a janela de 5 dos negócios."""

PARTITION_BATCH_ROWS = 50_000
"""Linhas por lote na escrita Parquet de legs/strike_events."""

try:  # imagem sem tzdata: BRT é offset fixo −03:00 desde 2019
    _TZ: Any = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:  # pragma: no cover - só em imagem mínima sem tzdata
    _TZ = timezone(timedelta(hours=-3))


# --------------------------------------------------------------------------- #
# Client httpx (D2)
# --------------------------------------------------------------------------- #


def build_client(*, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Client de borda: UA de browser, timeout de leitura longo e redirects.

    `read=120` não é folga: o export do OI (3 MB) já estourou 60 s e até 170 s
    sob carga (medido no B5); o retry dos módulos cobre o resto. O teste afirma o
    timeout construído — abaixar isso é bug, não otimização.
    """
    return httpx.Client(
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "text/csv, application/zip, application/json, */*",
        },
        timeout=httpx.Timeout(
            connect=CONNECT_TIMEOUT_S,
            read=READ_TIMEOUT_S,
            write=READ_TIMEOUT_S,
            pool=CONNECT_TIMEOUT_S,
        ),
        follow_redirects=True,
        transport=transport,
    )


def _now() -> datetime:
    """Instante de parede: só carimbo (`updated_at`) e agenda do watch (ADR-010).

    # carimbo de download/prepare (excecao ADR-010) — a data de REFERENCIA
    # continua vindo do arquivo (P-C); nada aqui decide que dia é "hoje" no dado
    """
    return datetime.now(_TZ)


def _seconds_until_next_run(now: datetime) -> float:
    """Segundos até as próximas 21:30 locais (função pura: recebe o `now`)."""
    target = now.replace(hour=WATCH_HOUR, minute=WATCH_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


# --------------------------------------------------------------------------- #
# Prepare
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StageFailure:
    """Falha de uma fonte/estágio que **não** derrubou o prepare (P-E)."""

    stage: str
    error: SourceError

    def to_dict(self) -> dict[str, str]:
        """`{stage, code, message, hint}` para o sidecar e o health."""
        return {"stage": self.stage, **self.error.to_dict()}


@dataclass(slots=True)
class PrepareResult:
    """Resultado do prepare: código de saída, resumo de uma tela e artefatos."""

    exit_code: int
    lines: list[str] = field(default_factory=list)
    failures: list[StageFailure] = field(default_factory=list)
    report_path: Path | None = None
    universe_count: int = 0


def run_prepare(
    config: Config,
    *,
    client_factory: Callable[[], httpx.Client] = build_client,
    year: int | None = None,
    now: datetime | None = None,
) -> PrepareResult:
    """Executa o pipeline inteiro uma vez; nunca levanta por fonte indisponível.

    `client_factory` e `now` existem para o teste: com `httpx.MockTransport` o
    pipeline roda ponta a ponta sem rede, e o `now` injetado mantém o teste longe
    do relógio de parede na parte que pode ser determinística.
    """
    started = time.perf_counter()
    stamp = now or _now()
    reference_year = int(year if year is not None else stamp.year)
    data_dir = config.data_dir
    failures: list[StageFailure] = []
    lines: list[str] = []
    report_path: Path | None = None
    universe: list[str] = []
    counts_by_market: dict[str, int] = {}
    counts_by_date: dict[date, int] = {}
    validation: ValidationReport | None = None
    fetch: FetchResult | None = None
    rates_summary: dict[str, object] | None = None
    oi_summary: dict[str, object] | None = None
    earnings_summary: dict[str, object] | None = None
    legs_summary: dict[str, object] | None = None
    events_summary: dict[str, object] | None = None
    liquidity_summary: dict[str, object] | None = None
    oi_map: dict[date, dict[str, int]] = {}

    client = client_factory()
    try:
        # 1-2. COTAHIST: download condicional, validação e reconstrução do ano.
        fetch = fetch_cotahist(
            reference_year,
            data_dir / "raw",
            config.cotahist_base_url,
            client=client,
        )
        if fetch.status == "error":
            failures.append(StageFailure("cotahist", fetch.error))
            lines.append(
                f"cotahist   FALHOU ({fetch.error.code}): {fetch.error.message}"
            )
        else:
            validation = validate_cotahist(fetch.path)
            if not validation.ok:
                detail = "; ".join(error.message for error in validation.errors)
                if not detail:
                    detail = (
                        f"header_ok={validation.header_ok} "
                        f"count_match={validation.count_match} "
                        f"(trailer={validation.trailer_total}, "
                        f"linhas={validation.line_count})"
                    )
                lines.append(f"cotahist   INVÁLIDO: {detail}")
                lines.append(
                    "prepare abortado: arquivo estruturalmente inválido não entra no lake "
                    f"(linhas tortas puladas: {validation.bad_width_count} de largura, "
                    f"{validation.bad_numeric_count} de numérico)"
                )
                return PrepareResult(
                    exit_code=1,
                    lines=lines,
                    failures=failures,
                )

            # D3: contagem por mercado de TODAS as linhas 01 (inclusive as descartadas)
            counts_by_market = _count_markets(fetch.path)
            by_date: Counter[date] = Counter()
            stats = write_cotahist_year(
                _count_dates(iter_cotahist(fetch.path), by_date),
                reference_year,
                data_dir,
            )
            counts_by_date = dict(by_date)
            lines.append(_cotahist_line(fetch, validation, stats, counts_by_market))

        # 3. Calendário do lake: a sessão de referência vem daqui (P-C).
        service = QueryService(data_dir)
        try:
            sessions = service.lake_calendar(reference_year)
        except SourceFailure as exc:
            failures.append(StageFailure("lake", exc.error))
            sessions = []
        if not sessions:
            lines.append(
                f"lake       vazio para {reference_year}: sem COTAHIST utilizável não há o "
                "que derivar (exit 1)"
            )
            return PrepareResult(exit_code=1, lines=lines, failures=failures)
        last_session = sessions[-1]

        # O retrato do estado é do LAKE INTEIRO, não do ano preparado (§4.4): a
        # maior data de todas as partições é o que `age_sessions` compara.
        try:
            lake_sessions = service.lake_calendar()
        except SourceFailure as exc:
            failures.append(StageFailure("lake", exc.error))
            lake_sessions = sessions
        lake_last = lake_sessions[-1] if lake_sessions else last_session

        # 4-5. Legs e eventos de strike (B2, puros) persistidos no padrão do lake.
        legs_summary, legs_error = _stage(
            "legs", lambda: _write_legs(data_dir, reference_year)
        )
        if legs_error is not None:
            failures.append(legs_error)
            lines.append(f"legs       FALHOU: {legs_error.error.message}")
        else:
            lines.append(_legs_line(legs_summary or {}))

        events_summary, events_error = _stage(
            "strike_events", lambda: _write_strike_events(data_dir, reference_year)
        )
        if events_error is not None:
            failures.append(events_error)
            lines.append(f"eventos    FALHOU: {events_error.error.message}")
        else:
            lines.append(_events_line(events_summary or {}))

        # 6. Curva DI da última sessão do lake (P-M: fallback e rate_source).
        try:
            rates = fetch_rates_di(last_session, client=client, data_dir=data_dir)
        except SourceFailure as exc:
            failures.append(StageFailure("rates", exc.error))
            lines.append(f"rates      FALHOU: {exc.message}")
        else:
            if rates.error is not None:
                failures.append(StageFailure("rates", rates.error))
                lines.append(f"rates      FALHOU: {rates.error.message}")
            else:
                rates_summary = {
                    "refdate": last_session.isoformat(),
                    "source": rates.source_final,
                    "nodes": len(rates.points),
                }
                lines.append(
                    f"rates      {rates.source_final} · {len(rates.points)} nós "
                    f"({last_session.isoformat()})"
                )

        # 7. Calendário de resultados: ITR/DFP/FCA do ano corrente E do anterior
        #    (o DFP corrente é quase vazio; sem o anterior não há actual 31-12).
        earnings_summary, earnings_error = _stage(
            "earnings",
            lambda: _load_earnings(
                data_dir,
                service,
                reference_year,
                last_session,
                client,
            ),
        )
        if earnings_error is not None:
            failures.append(earnings_error)
            lines.append(f"earnings   FALHOU: {earnings_error.error.message}")
        else:
            lines.append(_earnings_line(earnings_summary or {}))

        # 8. OI: última janela do lake que cabe na janela de 20 sessões da fonte.
        oi_map, oi_summary, oi_error = _load_open_interest(
            data_dir, service, sessions, client
        )
        if oi_error is not None:
            failures.append(oi_error)
            lines.append(f"oi         FALHOU: {oi_error.error.message}")
        else:
            lines.append(_oi_line(oi_summary or {}))

        # 9. Liquidez do papel (F0.11) + relatório com data (P-T).
        liquidity_summary, liquidity_error = _stage(
            "liquidity",
            lambda: _build_and_write_liquidity(
                data_dir, reference_year, sessions, oi_map
            ),
        )
        if liquidity_error is not None:
            failures.append(liquidity_error)
            lines.append(f"liquidez   FALHOU: {liquidity_error.error.message}")
        else:
            summary = liquidity_summary or {}
            universe_raw = summary.get("universe")
            universe = (
                [str(item) for item in universe_raw]
                if isinstance(universe_raw, list)
                else []
            )
            path_value = summary.get("report")
            report_path = Path(path_value) if isinstance(path_value, str) else None
            lines.append(
                f"liquidez   {len(universe)} papel(is) no universo (volume ≥ R$ 5 M) · "
                f"{summary.get('tradable_count', 0)} com grade líquida · "
                f"{summary.get('liquid_days', 0)} dia(s) líquido(s) somados · "
                f"OI de {summary.get('oi_date') or 'nenhuma data'}"
            )
            if report_path is not None:
                lines.append(f"relatório  {report_path}")

        # 10. ingest_state + sidecar de diagnóstico (D3, último erro, RSS).
        try:
            _update_state(
                data_dir,
                stamp,
                fetch=fetch,
                validation=validation,
                counts_by_date=counts_by_date,
                last_session=lake_last,
                model_version=config.model_version,
                prepared_year=reference_year,
            )
        except SourceFailure as exc:
            failures.append(StageFailure("ingest_state", exc.error))
            lines.append(f"estado     FALHOU: {exc.message}")

        rss_mib = _rss_peak_mib()
        duration = time.perf_counter() - started
        previous_state = load_prepare_state(data_dir)
        # preparar um ano fechado não pode apagar o retrato do ano mais novo
        counts_by_market_sidecar = dict(previous_state.counts_by_market)
        if counts_by_market:
            counts_by_market_sidecar[str(reference_year)] = counts_by_market
        universe_sidecar = tuple(universe)
        universe_session_sidecar = lake_last
        if reference_year < lake_last.year and previous_state.universe:
            universe_sidecar = previous_state.universe
            universe_session_sidecar = previous_state.universe_session
        prepare_state = PrepareState(
            counts_by_market=counts_by_market_sidecar,
            universe=universe_sidecar,
            universe_session=universe_session_sidecar,
            last_error=failures[-1].error.to_dict() if failures else None,
            failures=tuple(failure.to_dict() for failure in failures),
            rss_peak_mib=rss_mib,
            rates=rates_summary,
            open_interest=oi_summary,
            oi_date=max(oi_map) if oi_map else None,
            earnings=earnings_summary,
            liquidity=liquidity_summary,
            cotahist=_cotahist_summary(fetch, validation, reference_year),
            duration_seconds=round(duration, 1),
        )
        try:
            save_prepare_state(data_dir, prepare_state)
        except OSError as exc:  # diagnóstico não derruba o prepare
            lines.append(f"sidecar    não gravado ({exc}); o health fica sem contagens")

        lines.insert(
            0,
            f"prepare {last_session.isoformat()}"
            + (f" · lake até {lake_last.isoformat()}" if lake_last != last_session else "")
            + f" · {duration:.1f}s · "
            f"rss pico {rss_mib:.0f} MiB · "
            f"{len(failures)} falha(s) de fonte",
        )
        return PrepareResult(
            exit_code=0,
            lines=lines,
            failures=failures,
            report_path=report_path,
            universe_count=len(universe),
        )
    finally:
        client.close()


def _stage(
    name: str, action: Callable[[], dict[str, object]]
) -> tuple[dict[str, object] | None, StageFailure | None]:
    """Executa um estágio derivado; falha vira `StageFailure`, nunca exceção."""
    try:
        return action(), None
    except SourceFailure as exc:
        return None, StageFailure(name, exc.error)
    except ValueError as exc:  # contrato de função (ex.: kind inválido)
        return None, StageFailure(
            name,
            SourceError(
                code="bad_data",
                message=f"{name}: valor inesperado no dado: {exc}",
                hint="rode `b3o prepare` de novo; se persistir, é bug de contrato",
            ),
        )


def _cotahist_line(
    fetch: FetchResult,
    validation: ValidationReport,
    stats: Any,
    counts_by_market: Mapping[str, int],
) -> str:
    """Linha do resumo do COTAHIST: status, linhas, trailer e descartados por mercado."""
    skipped = validation.bad_width_count + validation.bad_numeric_count
    discarded = {
        market: count for market, count in counts_by_market.items() if market not in KEPT_MARKETS
    }
    status = "304 not modified" if fetch.status == "not_modified" else "baixado"
    parts = [
        f"cotahist   {status} · {stats.rows} linhas no lake · trailer "
        f"{validation.trailer_total} ({'ok' if validation.count_match else 'divergente'})",
    ]
    if discarded:
        detail = ", ".join(f"{market}={count}" for market, count in sorted(discarded.items()))
        parts.append(f"descartados por mercado: {detail}")
    if skipped:
        parts.append(
            f"puladas: largura={validation.bad_width_count} "
            f"numérico={validation.bad_numeric_count}"
        )
    return " · ".join(parts)


def _legs_line(summary: Mapping[str, object]) -> str:
    return (
        f"legs       {summary.get('rows', 0)} linha(s) · "
        f"{summary.get('series', 0)} série(s) · {summary.get('legs', 0)} leg(s)"
    )


def _events_line(summary: Mapping[str, object]) -> str:
    by_kind = summary.get("by_kind") or {}
    detail = ", ".join(f"{kind}={count}" for kind, count in sorted(by_kind.items()))
    return (
        f"eventos    {summary.get('rows', 0)} evento(s) de strike"
        + (f" ({detail})" if detail else "")
    )


def _earnings_line(summary: Mapping[str, object]) -> str:
    return (
        f"earnings   {summary.get('rows', 0)} data(s) · "
        f"{summary.get('actual', 0)} actual / {summary.get('estimated', 0)} estimated · "
        f"{summary.get('tickers', 0)} papel(éis)"
    )


def _oi_line(summary: Mapping[str, object]) -> str:
    return (
        f"oi         {summary.get('with_data', 0)} data(s) com dado · "
        f"{summary.get('without_data', 0)} sem dado · "
        f"{summary.get('errors', 0)} erro(s)"
    )


# --------------------------------------------------------------------------- #
# Estágios do prepare
# --------------------------------------------------------------------------- #


def _count_markets(zip_path: Path) -> dict[str, int]:
    """Conta as linhas `01` do zip por `tpmerc`, **sem parse** (D3).

    Passada barata sobre os bytes: só o campo de mercado (3 bytes) é decodificado.
    É a única forma de contar `017`/`021`/`030`: o parser descarta esses mercados
    antes de entregar a linha, e mercado descartado sem contagem é silêncio
    disfarçado (ADR-007).
    """
    counts: Counter[str] = Counter()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            member = _data_member(archive, zip_path)
            with archive.open(member) as stream:
                for raw in stream:
                    if raw[:2] != b"01":
                        continue
                    market = raw[24:27].decode("latin-1").strip() or "?"
                    counts[market] += 1
    except (OSError, zipfile.BadZipFile) as exc:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"não foi possível contar os mercados de {zip_path.name}: {exc}",
                hint="o zip foi validado antes; erro aqui é I/O — rode o prepare de novo",
            )
        ) from exc
    return dict(sorted(counts.items()))


def _data_member(archive: zipfile.ZipFile, zip_path: Path) -> str:
    """Membro de dados do zip (o COTAHIST tem um `.TXT`; o parser usa a mesma regra)."""
    names = [name for name in archive.namelist() if not name.endswith("/")]
    texts = [name for name in names if name.lower().endswith(".txt")]
    if not names:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"{zip_path.name} sem membro de dados",
                hint="baixe o COTAHIST de novo (b3o prepare)",
            )
        )
    return (texts or names)[0]


def _count_dates(rows: Iterable[CotahistRow], by_date: Counter[date]) -> Iterator[CotahistRow]:
    """Repassa as linhas contando por `refdate` (o trailer conta registros, não datas)."""
    for row in rows:
        by_date[row.refdate] += 1
        yield row


LEGS_SCHEMA = pa.schema(
    [
        pa.field("leg_id", pa.string(), nullable=False),
        pa.field("ticker", pa.string(), nullable=False),
        pa.field("maturity", pa.date32(), nullable=False),
        pa.field("side", pa.string(), nullable=False),
        pa.field("style", pa.string(), nullable=True),
        pa.field("refdate", pa.date32(), nullable=False),
        pa.field("leg_index", pa.int32(), nullable=False),
        pa.field("strike", pa.int64(), nullable=False),
        pa.field("distribution_id", pa.string(), nullable=False),
        pa.field("flags", pa.list_(pa.string()), nullable=False),
    ]
)
"""Schema de `data/legs/year=YYYY/part-0.parquet` (espelha `derive.legs.LegRow`)."""

STRIKE_EVENTS_SCHEMA = pa.schema(
    [
        pa.field("underlying", pa.string(), nullable=False),
        pa.field("date", pa.date32(), nullable=False),
        pa.field("kind", pa.string(), nullable=False),
        pa.field("delta_strike", pa.int64(), nullable=True),
        pa.field("factor", pa.float64(), nullable=True),
        pa.field("n_series", pa.int32(), nullable=False),
        pa.field("ambiguous", pa.bool_(), nullable=False),
    ]
)
"""Schema de `data/strike_events/year=YYYY/part-0.parquet`."""


def _write_legs(data_dir: Path, year: int) -> dict[str, object]:
    """Grava a tabela de legs do ano em streaming, contando séries e legs sem set."""
    target = data_dir / "legs" / f"year={year:04d}" / "part-0.parquet"
    counters = {"rows": 0, "series": 0, "legs": 0}
    state: dict[str, object] = {"series": None, "leg": None}

    def batches() -> Iterator[list[dict[str, object]]]:
        batch: list[dict[str, object]] = []
        for leg in build_legs(read_cotahist_years(data_dir, [year])):
            key = (leg.ticker, leg.maturity)
            if key != state["series"]:
                counters["series"] += 1
                state["series"] = key
            leg_key = (key, leg.leg_index)
            if leg_key != state["leg"]:
                counters["legs"] += 1
                state["leg"] = leg_key
            counters["rows"] += 1
            batch.append(
                {
                    "leg_id": leg.leg_id,
                    "ticker": leg.ticker,
                    "maturity": leg.maturity,
                    "side": leg.side,
                    "style": leg.style,
                    "refdate": leg.refdate,
                    "leg_index": leg.leg_index,
                    "strike": leg.strike,
                    "distribution_id": leg.distribution_id,
                    "flags": list(leg.flags),
                }
            )
            if len(batch) >= PARTITION_BATCH_ROWS:
                yield batch
                batch = []
        if batch:
            yield batch

    written = _write_batches(target, LEGS_SCHEMA, batches())
    return {
        "rows": written,
        "series": counters["series"],
        "legs": counters["legs"],
        "path": str(target),
    }


def _write_strike_events(data_dir: Path, year: int) -> dict[str, object]:
    """Grava os eventos de ajuste de strike do ano (classificação do §6.6)."""
    target = data_dir / "strike_events" / f"year={year:04d}" / "part-0.parquet"
    events = classify_strike_events(read_cotahist_years(data_dir, [year]))
    by_kind = Counter(event.kind for event in events)
    payload = [
        {
            "underlying": event.underlying,
            "date": event.date,
            "kind": event.kind,
            "delta_strike": event.delta_strike,
            "factor": event.factor,
            "n_series": event.n_series,
            "ambiguous": event.ambiguous,
        }
        for event in events
    ]
    # Zero eventos é a verdade de um ano sem ajuste de strike: partição vazia vale.
    rows = _write_batches(target, STRIKE_EVENTS_SCHEMA, _chunks(payload), allow_empty=True)
    return {"rows": rows, "by_kind": dict(sorted(by_kind.items())), "path": str(target)}


def _chunks(payload: list[dict[str, object]]) -> Iterator[list[dict[str, object]]]:
    """Fatia uma lista já materializada em lotes de escrita."""
    for start in range(0, len(payload), PARTITION_BATCH_ROWS):
        yield payload[start : start + PARTITION_BATCH_ROWS]


def _write_batches(
    target: Path,
    schema: pa.Schema,
    batches: Iterable[list[dict[str, object]]],
    *,
    allow_empty: bool = False,
) -> int:
    """Escreve lotes num Parquet e publica com `fsutil.replace_file` (padrão B1).

    `store/lake.py` só expõe o escritor do COTAHIST, então a escrita das tabelas
    derivadas mora aqui (registrado no relatório do C1): mesma disciplina —
    staging em `data_dir/tmp/`, contagem conferida, troca atômica, sem sobra.

    `allow_empty=True` só para tabela cujo vazio é a verdade (eventos de ajuste
    de um ano sem ajuste): partição de legs vazia continua sendo erro, porque
    significaria que o lake perdeu as opções.
    """
    root = target.parents[2]
    tmp_dir = root / "tmp" / f"partition-{uuid.uuid4().hex[:12]}"
    tmp_path = tmp_dir / "part-0.parquet"
    written = 0
    try:
        tmp_dir.mkdir(parents=True)
        with open(tmp_path, "wb") as handle:
            with pq.ParquetWriter(handle, schema, compression="snappy") as writer:
                for batch in batches:
                    writer.write_batch(pa.RecordBatch.from_pylist(batch, schema=schema))
                    written += len(batch)
        if written == 0 and not allow_empty:
            raise SourceFailure(
                SourceError(
                    code="bad_data",
                    message=f"nenhuma linha para {target}; a partição vazia apagaria o dado bom",
                    hint="confira o recorte do lake (a escrita é abortada de propósito)",
                )
            )
        stored = pq.ParquetFile(tmp_path).metadata.num_rows
        if stored != written:
            raise SourceFailure(
                SourceError(
                    code="bad_data",
                    message=f"Parquet de {target} com {stored} linhas, consumidas {written}",
                    hint="escrita truncada: disco cheio ou falha do pyarrow; partição intacta",
                )
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        replace_file(tmp_path, target)
    except OSError as exc:
        raise SourceFailure(
            SourceError(
                code="storage_error",
                message=f"falha de disco ao gravar {target}: {exc}",
                hint="confira espaço/permissão do volume de dados do container (B3_DATA_DIR)",
            )
        ) from exc
    finally:
        _remove_tree(tmp_dir)
    return written


def _remove_tree(path: Path) -> None:
    """Remove o staging best-effort (sem mascarar o erro original)."""
    for child in path.glob("*"):
        try:
            child.unlink()
        except OSError:  # pragma: no cover - limpeza best-effort
            pass
    try:
        path.rmdir()
    except OSError:  # pragma: no cover - limpeza best-effort
        pass


def _load_earnings(
    data_dir: Path,
    service: QueryService,
    year: int,
    last_session: date,
    client: httpx.Client,
) -> dict[str, object]:
    """Baixa ITR/DFP/FCA do ano corrente **e do anterior** e grava o calendário."""
    fetched = fetch_earnings((year, year - 1), data_dir / "cvm", client=client)
    if fetched.failures:
        first = fetched.errors[0]
        raise SourceFailure(
            SourceError(
                code=first.code,
                message=(
                    f"{len(fetched.failures)} download(s) da CVM falharam; "
                    f"primeiro: {first.message}"
                ),
                hint=first.hint,
            )
        )
    tickers = service.spot_tickers(year)
    rows = build_earnings(
        fetched.paths("itr"),
        fetched.paths("dfp"),
        fetched.paths("fca"),
        tickers,
        last_session,
    )
    if rows:
        write_earnings(rows, data_dir)
    actual = sum(1 for row in rows if row.kind == "actual")
    return {
        "rows": len(rows),
        "actual": actual,
        "estimated": len(rows) - actual,
        "tickers": len({row.ticker for row in rows}),
        "years": [year - 1, year],
    }


def _load_open_interest(
    data_dir: Path,
    service: QueryService,
    sessions: list[date],
    client: httpx.Client,
) -> tuple[dict[date, dict[str, int]], dict[str, object], StageFailure | None]:
    """Baixa a janela de OI que falta, lê a partição e devolve `{data: {ticker: total_qty}}`.

    A fonte guarda 20 sessões; o prepare pede as últimas 10 do lake. Data já
    gravada não é re-baixada (o OI do dia não muda), exceto a mais recente, que
    pode ter sido publicada depois do último prepare. Data sem dado publicado fica
    simplesmente sem OI (`oi_unavailable`) — P-N, não é erro.
    """
    wanted = sessions[-OI_WINDOW_SESSIONS:]
    try:
        existing = set(service.open_interest_dates())
    except SourceFailure:
        existing = set()  # nunca baixado: baixa a janela toda
    to_fetch = [day for day in wanted if day not in existing or day == wanted[-1]]

    errors: list[SourceError] = []
    with_data: list[date] = []
    without_data: list[date] = []
    if to_fetch:
        window = fetch_open_interest_window(to_fetch, client=client, data_dir=data_dir)
        errors.extend(window.errors)
        with_data = list(window.dates_with_data)
        without_data = list(window.dates_without_data)

    try:
        oi_map = service.open_interest_totals(wanted)
    except SourceFailure as exc:
        # A leitura da partição falhou: engolir silenciosamente viraria "sem OI"
        # (zero mentiroso no health). Registra o erro e deixa o mapa vazio — o
        # consumidor ainda cai em oi_unavailable, mas a falha aparece no sidecar.
        errors.append(exc.error)
        oi_map = {}

    summary = {
        "requested": [day.isoformat() for day in sorted(set(wanted))],
        "fetched": [day.isoformat() for day in to_fetch],
        "with_data": len(with_data),
        "without_data": len(without_data),
        "errors": len(errors),
        "dates": [day.isoformat() for day in sorted(oi_map)],
    }
    error = StageFailure("open_interest", errors[0]) if errors else None
    return oi_map, summary, error


def _build_and_write_liquidity(
    data_dir: Path,
    year: int,
    sessions: list[date],
    oi_map: Mapping[date, Mapping[str, int]],
) -> dict[str, object]:
    """Lê o recorte recente do lake, calcula a liquidez e escreve o relatório (P-T)."""
    window = sessions[-LIQUIDITY_SESSIONS:]
    cutoff = window[0]
    recorte = (
        row for row in read_cotahist_years(data_dir, [year]) if row.refdate >= cutoff
    )
    rows = build_liquidity(recorte, oi_map)
    last_session = sessions[-1]
    report = render_report(
        rows,
        last_session=last_session,
        first_session=cutoff,
        sessions=len(window),
        oi_date=max(oi_map) if oi_map else None,
    )
    target = data_dir / "out" / f"relatorio-liquidez-{last_session.isoformat()}.md"
    _write_text_atomic(target, report)
    universe = [row.underlying for row in rows if row.in_universe]
    tradable = [row.underlying for row in rows if row.liquid_days_20d > 0]
    return {
        "report": str(target),
        "universe": universe,
        "universe_count": len(universe),
        "tradable": tradable,
        "tradable_count": len(tradable),
        "sessions": len(window),
        "papeis": len(rows),
        "liquid_days": sum(row.liquid_days_20d for row in rows),
        "oi_date": max(oi_map).isoformat() if oi_map else None,
    }


def _write_text_atomic(target: Path, text: str) -> None:
    """Texto atômico: staging em `data_dir/tmp/` e `fsutil.replace_file`."""
    root = target.parents[1]
    tmp_dir = root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{target.name}-{uuid.uuid4().hex[:12]}.part"
    try:
        tmp.write_text(text, encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        replace_file(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _update_state(
    data_dir: Path,
    stamp: datetime,
    *,
    fetch: FetchResult | None,
    validation: ValidationReport | None,
    counts_by_date: Mapping[date, int],
    last_session: date,
    model_version: int,
    prepared_year: int,
) -> None:
    """Atualiza `ingest_state.json` com o que este prepare viu (P-C/ADR-007).

    **`prepare --year` honesto (§4.4):**

    - `last_session` é a **maior data do lake inteiro** (todos os anos), não a do
      ano preparado — preparar 2025 depois de 2026 não pode rejuvenescer o state;
    - `counts_by_date` é **mesclado**: as datas de `prepared_year` são
      substituídas (a partição do ano é reconstruída, então data que sumiu do
      arquivo tem que sumir da contagem) e as dos outros anos ficam intactas;
    - `trailer_total`/`etag`/`last_modified`/`sha256`/`skipped_lines` descrevem
      **o arquivo do ano preparado** e só são gravados quando ele é o mais novo
      do lake (`prepared_year >= last_session.year`); caso contrário, o retrato
      do arquivo corrente ficaria apontando para um ano fechado.
    """
    current = load_ingest_state(data_dir)
    changes: dict[str, object] = {
        "updated_at": stamp,
        "last_session": last_session,
        "model_version": model_version,
    }
    if prepared_year >= last_session.year:
        if fetch is not None and fetch.status != "error":
            changes.update(
                etag=fetch.etag,
                last_modified=fetch.last_modified,
                sha256=fetch.sha256,
            )
        if validation is not None:
            changes.update(
                trailer_total=validation.trailer_total,
                skipped_lines=skipped_lines_from_report(validation),
            )
    merged = {
        day: count for day, count in current.counts_by_date.items() if day.year != prepared_year
    }
    merged.update(counts_by_date)
    if merged:
        changes["counts_by_date"] = merged
        changes["replace_counts"] = True
    update_ingest_state(data_dir, **changes)  # type: ignore[arg-type]



def _cotahist_summary(
    fetch: FetchResult | None, validation: ValidationReport | None, year: int
) -> dict[str, object] | None:
    """`{year, status, etag, sha256, trailer_total, lines}` para o sidecar."""
    if fetch is None:
        return None
    return {
        "year": year,
        "status": fetch.status,
        "etag": fetch.etag,
        "sha256": fetch.sha256,
        "trailer_total": validation.trailer_total if validation is not None else None,
        "lines": validation.line_count if validation is not None else None,
    }


def _rss_peak_mib() -> float:
    """Pico de RSS do processo em MiB (`ru_maxrss` é KiB no Linux)."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(usage / 1024.0, 1)


# --------------------------------------------------------------------------- #
# Log
# --------------------------------------------------------------------------- #


class _JsonFormatter(logging.Formatter):
    """Log em JSON, uma linha por evento (P-F)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, _TZ).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Configura o logging da borda (JSON; nível do `config.yaml`)."""
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #


@app.callback()
def root() -> None:
    """b3o — Opções B3.

    Toda métrica é calculada em Python sobre dados EOD; nenhum número nasce de LLM.
    A data de referência vem sempre do arquivo baixado, nunca do relógio.
    """


@app.command()
def prepare(
    config: Annotated[
        Path, typer.Option("--config", help="caminho do config.yaml (chaves planas, P-B)")
    ] = DEFAULT_CONFIG_PATH,
    year: Annotated[
        int | None,
        typer.Option("--year", help="ano do COTAHIST (default: ano corrente do calendário)"),
    ] = None,
    watch: Annotated[
        bool, typer.Option("--watch", help="repete todo dia às 21:30 America/Sao_Paulo")
    ] = False,
) -> None:
    """Baixa as fontes, valida, reconstrói o lake e deriva o essencial (1×/dia)."""
    if not watch:
        raise typer.Exit(code=_run_once(config, year))

    while True:
        code = _run_once(config, year)
        target = _seconds_until_next_run(_now())
        typer.echo(
            f"prepare-watch: exit {code}; próxima execução em {target / 3600:.2f} h "
            f"(21:30 America/Sao_Paulo)"
        )
        remaining = target
        while remaining > 0:
            time.sleep(min(remaining, 60.0))
            remaining -= 60.0


def _run_once(config_path: Path, year: int | None) -> int:
    """Um ciclo do prepare; devolve o código de saída (0 = ok)."""
    try:
        config = load_config(config_path)
    except SourceFailure as exc:
        typer.secho(f"config     {exc.code}: {exc.message}", fg=typer.colors.RED, err=True)
        typer.secho(f"           dica: {exc.hint}", err=True)
        return 1
    configure_logging(config.log_level)
    result = run_prepare(config, year=year)
    for line in result.lines:
        typer.echo(line)
    if result.failures:
        typer.echo("")
        typer.echo("fontes com falha (o prepare seguiu; o health acusa):")
        for failure in result.failures:
            typer.echo(
                f"  - {failure.stage}: {failure.error.code} — {failure.error.message}"
            )
            typer.echo(f"    dica: {failure.error.hint}")
    return result.exit_code


@app.command()
def health(
    config: Annotated[
        Path, typer.Option("--config", help="caminho do config.yaml (chaves planas, P-B)")
    ] = DEFAULT_CONFIG_PATH,
) -> None:
    """Saúde do lake: última sessão, idade, contagens, fontes, último erro."""
    try:
        settings = load_config(config)
    except SourceFailure as exc:
        typer.secho(f"config     {exc.code}: {exc.message}", fg=typer.colors.RED, err=True)
        typer.secho(f"           dica: {exc.hint}", err=True)
        raise typer.Exit(code=1) from exc

    payload = QueryService(settings.data_dir).health().to_dict()
    # relógio só como carimbo informativo na borda (ADR-010); a idade vem do lake
    payload["now_wall_clock_informative"] = _now().isoformat(timespec="seconds")
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@app.command()
def rates(
    backfill: Annotated[
        bool,
        typer.Option(
            "--backfill",
            help="preenche a Selic plana em toda sessão do lake que não tem curva",
        ),
    ] = False,
    config: Annotated[
        Path, typer.Option("--config", help="caminho do config.yaml (chaves planas, P-B)")
    ] = DEFAULT_CONFIG_PATH,
) -> None:
    """Preenche o histórico de taxa com a Selic plana do SGS 11 (uma chamada)."""
    if not backfill:
        typer.echo("rates: use --backfill (o lote é o único modo; a curva PRE vem do prepare)")
        raise typer.Exit(code=2)

    try:
        settings = load_config(config)
    except SourceFailure as exc:
        typer.secho(f"config     {exc.code}: {exc.message}", fg=typer.colors.RED, err=True)
        typer.secho(f"           dica: {exc.hint}", err=True)
        raise typer.Exit(code=1) from exc
    configure_logging(settings.log_level)

    service = QueryService(settings.data_dir)
    try:
        sessions = service.lake_calendar()
    except SourceFailure as exc:
        typer.secho(f"lake       {exc.code}: {exc.message}", fg=typer.colors.RED, err=True)
        typer.secho(f"           dica: {exc.hint}", err=True)
        raise typer.Exit(code=1) from exc
    if not sessions:
        typer.echo(f"rates      lake vazio em {settings.data_dir}; rode `b3o prepare` antes")
        raise typer.Exit(code=1)

    client = build_client()
    try:
        summary = backfill_selic_flat(sessions, client=client, data_dir=settings.data_dir)
    except SourceFailure as exc:
        typer.secho(f"rates      {exc.code}: {exc.message}", fg=typer.colors.RED, err=True)
        typer.secho(f"           dica: {exc.hint}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        client.close()

    used = sorted({float(value) for value in summary["selic_pct"].values()})  # type: ignore[union-attr]
    if len(used) > 1:
        several = f"{used[0]:.6f}-{used[-1]:.6f}% a.d."
    elif used:
        several = f"{used[0]:.6f}% a.d."
    else:
        several = ""
    detail = (
        f"{len(used)} valor(es), {several}"
        if used
        else "nada gravado (todas as sessões já têm curva)"
    )
    typer.echo(
        f"rates      {summary['source']} · {summary['sessions']} sessão(ões) no lake · "
        f"{summary['written']} gravada(s) · {summary['skipped_existing']} já com curva · "
        f"Selic {detail}"
    )
    errors = summary["errors"]
    if errors:
        typer.echo(f"           {len(errors)} erro(s): {errors}")  # type: ignore[arg-type]
        raise typer.Exit(code=1)


@app.command()
def metrics(
    config: Annotated[
        Path, typer.Option("--config", help="caminho do config.yaml (chaves planas, P-B)")
    ] = DEFAULT_CONFIG_PATH,
    date_: Annotated[
        date | None,
        typer.Option("--date", help="recalcula só esta sessão (merge na partição)"),
    ] = None,
    backfill: Annotated[
        bool,
        typer.Option("--backfill", help="recalcula o intervalo do lake em ordem crescente"),
    ] = False,
    from_: Annotated[
        date | None, typer.Option("--from", help="primeira sessão do --backfill")
    ] = None,
    to: Annotated[
        date | None, typer.Option("--to", help="última sessão do --backfill")
    ] = None,
) -> None:
    """Calcula `metrics_series` e `metrics_underlying` (IV, gregas, ATM, VRP, classe).

    `--date AAAA-MM-DD` recalcula **só** aquela sessão e faz merge na partição;
    `--backfill` percorre o intervalo em ordem crescente (a janela de 252 sessões
    do z/percentil usa só o passado). Resumo de uma tela (P-G); exit ≠ 0 só em
    falha estrutural (sem lake, partição ausente, data sem sessão).
    """
    try:
        settings = load_config(config)
    except SourceFailure as exc:
        typer.secho(f"config     {exc.code}: {exc.message}", fg=typer.colors.RED, err=True)
        typer.secho(f"           dica: {exc.hint}", err=True)
        raise typer.Exit(code=1) from exc
    configure_logging(settings.log_level)

    if date_ is None and not backfill:
        typer.echo("metrics: use --date AAAA-MM-DD ou --backfill [--from/--to]")
        raise typer.Exit(code=2)

    service = QueryService(settings.data_dir)
    try:
        lake = service.lake_calendar()
    except SourceFailure as exc:
        typer.secho(f"lake       {exc.code}: {exc.message}", fg=typer.colors.RED, err=True)
        typer.secho(f"           dica: {exc.hint}", err=True)
        raise typer.Exit(code=1) from exc
    if not lake:
        typer.echo(
            f"metrics    lake vazio em {settings.data_dir}; rode `b3o prepare` antes"
        )
        raise typer.Exit(code=1)

    if date_ is not None:
        dates: list[date] = [date_]
    else:
        start = from_ or lake[0]
        end = to or lake[-1]
        dates = [day for day in lake if start <= day <= end]
        if not dates:
            typer.echo(
                f"metrics    nenhuma sessão do lake no intervalo {start}→{end} "
                f"(lake: {lake[0]}→{lake[-1]})"
            )
            raise typer.Exit(code=1)

    try:
        summary = run_metrics(settings, dates=dates, backfill=backfill)
    except SourceFailure as exc:
        typer.secho(f"metrics    {exc.code}: {exc.message}", fg=typer.colors.RED, err=True)
        typer.secho(f"           dica: {exc.hint}", err=True)
        raise typer.Exit(code=1) from exc

    for line in summary.render():
        typer.echo(line)
    if summary.series_sha256:
        typer.echo(
            f"partição   metrics_series sha256 {summary.series_sha256[:12]}… · "
            f"metrics_underlying sha256 {summary.underlying_sha256[:12]}…"
        )


def main() -> None:
    """Entry point (console script)."""
    app()
