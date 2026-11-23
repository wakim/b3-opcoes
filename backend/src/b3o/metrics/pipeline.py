"""Pipeline de métricas por data (F1b, contrato §4.5) — o I/O mora aqui.

`run_metrics(config, dates=..., backfill=...)` percorre as sessões em ordem
**crescente** e, para cada uma:

1. lê as opções do dia (`QueryService.options_on`) e agrupa por papel;
2. monta o calendário estendido (lake + `derive/calendar.py`), calcula `T` e o
   `DF` da curva da sessão (sem curva → `no_rates`, nunca `DF = 1`);
3. acha o forward por vencimento pela paridade (`derive/forward.py`) com o
   **fechamento** das duas pernas (ADR-017);
4. inverte IV e gregas por série (`derive/iv.py`, `derive/greeks.py`);
5. consolida o smile por strike (`metrics/series.py`), roda os validadores
   (`derive/checks.py`) e tira o ATM 30d (`derive/atm.py`), skew, slope, RV,
   VRP/z/rank, classe de sinal, OI e calendário de resultados;
6. grava `metrics_series` e `metrics_underlying` em blocos de até 20 sessões
   (`refdates_replaced`), mantendo o estado rolante em memória — o `--backfill`
   não relê o próprio Parquet a cada dia.

**Sem look-ahead:** as estatísticas de 252 sessões usam o estado semeado do
histórico **anterior** à primeira data pedida e o que já foi calculado nesta
execução, em ordem crescente. Uma data posterior nunca entra no `vrp_z` de uma
data anterior (é o teste que o G2 vai caçar).

**Regras de smile calibradas no dado real (2026-11-19):**

- os validadores rodam com `butterfly_tol = 0,25` e `monotonicity_tol = 1,0` vol
  point (o default de 0,05 é 20× menor que o ruído de tick medido — ADR-018) e
  só na banda `|ln(K/F)| ≤ 0,10` (a asa de IV de 410% não informa nada);
- **`inconsistent` não tira o strike do ATM**: `atm_by_expiry` recebe todos os
  strikes com IV válida (o ATM precisa dos dois vizinhos de F); a flag serve
  para excluir a série do skew/butterfly (RR25/BFLY25);
- **forward só com ≥ 3 pares utilizáveis** e com carry plausível
  (`|ln(F/S)/T − r| ≤ 5% a.a.`): senão o vencimento fica sem `F`, a série ganha
  `forward_outlier`/`no_forward` e o ATM não o usa — nunca se "conserta" o F.

**Falha de um papel não derruba a data:** sem taxa, sem forward, sem spot ou
vencimento vencido viram `None` + flag; só falha estrutural (lake ausente,
partição de legs ausente) levanta `SourceFailure`.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from b3o.config import Config
from b3o.derive import calendar as b3calendar
from b3o.derive.atm import ExpiryAtm, atm_by_expiry, atm_interpolate_30d
from b3o.derive.checks import check_chain
from b3o.derive.conventions import log_df_interp_or_none
from b3o.derive.corporate_events import dividend_dates, event_factor_series, scale_factors
from b3o.derive.forward import ParityQuote, forward_by_parity
from b3o.derive.realized import OhlcBar, yang_zhang_vol
from b3o.derive.signal import CLASS_ORDER
from b3o.derive.skew import DeltaPoint, SkewMetrics, rr25_bfly25
from b3o.derive.vrp import iv_percentile_252, iv_rank_252, vrp, vrp_z
from b3o.ingest.errors import SourceError, SourceFailure
from b3o.metrics import series as series_mod
from b3o.metrics import underlying as und_mod
from b3o.metrics.series import (
    EvaluatedQuote,
    OptionQuote,
    build_strike_smile,
    evaluate_quote,
    inconsistent_flags,
    series_metrics,
)
from b3o.metrics.underlying import (
    FLAG_NO_90D,
    FLAG_NO_FORWARD,
    FLAG_OI_UNAVAILABLE,
    FLAG_VOLUME_UNAVAILABLE,
    TARGET_T_30D,
    TARGET_T_90D,
    UnderlyingMetrics,
    days_to_liquidate,
    earnings_before_expiry,
    expected_move_cents,
    implied_div_yield_pct,
    interpolate_variance_iv,
    nearest_expiry,
    window_with_today,
)
from b3o.query.service import OptionDay, QueryService, SpotBar
from b3o.store.metrics import (
    read_metrics_series,
    read_metrics_underlying,
    write_metrics_series,
    write_metrics_underlying,
)

log = logging.getLogger(__name__)

BLOCK_SESSIONS = 20
"""Sessões por bloco de escrita (`--backfill`: evita reescrever a partição 221 vezes)."""

RV_WINDOWS: tuple[int, ...] = (20, 30, 60)
"""Janelas do Yang-Zhang publicadas (§5.3)."""

RV_LOOKBACK_DAYS = 120
"""Dias corridos lidos antes da data para alimentar a janela de 60 sessões."""

HISTORY_WINDOW = 252
"""Sessões das janelas de VRP/z/rank/percentil (§6.7) e de `iv_pct_252` da série (§4.6)."""

LIQUIDITY_SESSIONS = 5
"""Janela de negócios da liquidez (§6.9)."""

SMILE_BUTTERFLY_TOL = 0.25
"""Folga da butterfly/calendário: **25% da variância total ATM**.

O default do `derive/checks.py` é 5% — calibrado em cadeia sintética. No
fechamento real da B3 um tick de 1 ¢ já move ~0,2 vol point no ATM (ADR-018:
paridade E/E mediana 1,2, P90 5), e 5% de `w_atm` fica abaixo desse ruído: em
2026-11-19 a tolerância antiga marcava 5.893 de 14.000 séries. 25% ainda pega
trios realmente tortos (densidade negativa de verdade) sem transformar ruído de
tick em arbitragem.
"""

SMILE_MONOTONICITY_TOL = 1.0
"""Folga da monotonicidade em **vol points** (default do `checks.py`: 0,05).

0,05 vol point é 20× menor que o ruído medido de fechamento (ADR-017/018: ~1,2
vol point de mediana na paridade). Subir para 1,0 mantém a flag para kinks
grosseiros e não marca a asa de call normal de um smile em U.
"""

SMILE_MONEYNESS_BAND = 0.10
"""Banda de moneyness validada: `|ln(K/F)| ≤ 0,25` (≈ K/F em [0,78; 1,28]).

Fora dela a IV é pura asa de tick (PETR4 19/11 tem strikes com IV de 410% e
195%) e nenhuma métrica do papel (ATM, 20Δ–80Δ) usa esses pontos. Validar a asa
inteira inflava `inconsistent` sem informar nada: 2.425 de 14.000 séries com a
banda de ±60%, contra <10% com esta.
"""

FORWARD_CARRY_TOL = 0.05
FORWARD_DIVIDEND_MAX = 0.25
"""Sanidade do forward contra a curva: `|ln(F/S)/T − r| > 5% a.a.` é outlier.

O forward pela paridade é o preço do mercado; se o carry implícito se afasta da
DI da sessão em mais de 5 pontos percentuais ao ano, o par que o produziu está
errado (bracket de 2 pontos, print velho, série ilíquida). O vencimento fica
sem `F`, as séries dele ganham `forward_outlier` e o ATM não o usa — nunca se
"conserta" o forward na mão.
"""

MIN_FORWARD_PAIRS = 3
"""Pares call/put utilizáveis mínimos para aceitar o cruzamento da paridade.

O cruzamento usa 2 pontos por construção; com **só** 2 pares na cadeia não há
como saber que a reta é paridade e não dois prints velhos (PETR4 2026-12-23:
2 pares davam forward de 4372 ¢ contra spot 4267 ¢). Com 2 pares o vencimento
fica sem forward; o OLS também precisa de 3 na banda (`forward.MIN_OLS_PAIRS`).
"""

_RATE_SOURCE_PRIORITY: dict[str, int] = {"b3_reference": 0, "selic_flat": 1}
"""Precedência da curva por sessão: PRE da B3 manda; Selic plana é fallback (ADR-014)."""


# --------------------------------------------------------------------------- #
# Resumo
# --------------------------------------------------------------------------- #


@dataclass
class MetricsSummary:
    """Resumo de uma tela do `b3o metrics` (P-G)."""

    dates: int = 0
    series: int = 0
    underlyings: int = 0
    with_iv: int = 0
    classes: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name in CLASS_ORDER}
    )
    flags: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    series_sha256: str = ""
    underlying_sha256: str = ""
    iv_atm_median: float | None = None
    first_session: date | None = None
    last_session: date | None = None

    def render(self) -> list[str]:
        """Linhas do resumo (uma tela, sem arredondar o dado — só a exibição)."""
        span = ""
        if self.first_session is not None and self.last_session is not None:
            span = (
                f"{self.first_session.isoformat()}"
                if self.first_session == self.last_session
                else f"{self.first_session.isoformat()}→{self.last_session.isoformat()}"
            )
        median = "—" if self.iv_atm_median is None else f"{self.iv_atm_median:.1f}"
        classes = " · ".join(f"{name} {self.classes.get(name, 0)}" for name in CLASS_ORDER)
        lines = [
            f"metrics {span} · {self.dates} sessão(ões) · {self.underlyings} papel(is) · "
            f"{self.series} série(s) · {self.with_iv} com IV · "
            f"iv_atm_30d mediana {median} · classes: {classes} · {self.seconds:.1f}s"
        ]
        top_flags = sorted(self.flags.items(), key=lambda item: (-item[1], item[0]))[:10]
        if top_flags:
            lines.append(
                "flags: " + " · ".join(f"{name} {count}" for name, count in top_flags)
            )
        if self.errors:
            lines.append(f"erros ({len(self.errors)}): " + "; ".join(self.errors[:5]))
        return lines


# --------------------------------------------------------------------------- #
# Estado rolante (histórico até a data, nunca depois)
# --------------------------------------------------------------------------- #


def _series() -> defaultdict[str, deque[float]]:
    """Deque de `HISTORY_WINDOW` por chave: o histórico rolante de papel/leg."""
    return defaultdict(lambda: deque(maxlen=HISTORY_WINDOW))


@dataclass
class _RollingState:
    """Séries curtas por chave, alimentadas em ordem crescente (nada do futuro)."""

    vrp: dict[str, deque[float]] = field(default_factory=_series)
    iv_atm: dict[str, deque[float]] = field(default_factory=_series)
    rr25: dict[str, deque[float]] = field(default_factory=_series)
    series_iv: dict[str, deque[float]] = field(default_factory=_series)
    series_seeded: bool = False

    def push_underlying(
        self,
        underlying: str,
        vrp_value: float | None,
        iv_value: float | None,
        rr_value: float | None,
    ) -> None:
        """Registra os valores do papel **depois** de calcular as métricas do dia."""
        if vrp_value is not None:
            self.vrp[underlying].append(vrp_value)
        if iv_value is not None:
            self.iv_atm[underlying].append(iv_value)
        if rr_value is not None:
            self.rr25[underlying].append(rr_value)


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #


def run_metrics(
    config: Config,
    *,
    dates: Sequence[date],
    backfill: bool = False,
) -> MetricsSummary:
    """Calcula e grava `metrics_series`/`metrics_underlying` para as sessões pedidas."""
    started = time.perf_counter()
    data_dir = Path(config.data_dir)
    service = QueryService(data_dir)
    try:
        lake = service.lake_calendar()
    except SourceFailure:
        raise
    if not lake:
        raise SourceFailure(
            SourceError(
                code="lake_missing",
                message=f"nenhuma partição do COTAHIST em {data_dir / 'raw_cotahist'}",
                hint="rode `b3o prepare` antes de `b3o metrics`",
            )
        )
    lake_set = set(lake)
    lake_last = lake[-1]
    requested = sorted({day for day in dates})
    if not requested:
        raise SourceFailure(
            SourceError(
                code="no_dates",
                message="nenhuma data para calcular",
                hint="passe --date AAAA-MM-DD ou --backfill (com --from/--to opcionais)",
            )
        )
    missing = [day.isoformat() for day in requested if day not in lake_set]
    if missing:
        raise SourceFailure(
            SourceError(
                code="no_session",
                message=f"data(s) sem sessão no lake: {', '.join(missing)}",
                hint=(
                    f"o lake tem {len(lake)} sessão(ões), de {lake[0].isoformat()} a "
                    f"{lake_last.isoformat()} — use uma data desse intervalo ou rode `b3o prepare`"
                ),
            )
        )

    context = _Context(
        service=service,
        lake=lake,
        lake_last=lake_last,
        data_dir=data_dir,
        model_version=config.model_version,
        first_session=requested[0],
    )
    context.spot_range_until(requested[-1])
    summary = MetricsSummary(
        dates=len(requested),
        first_session=requested[0],
        last_session=requested[-1],
    )
    iv_values: list[float] = []

    chunks = _chunks(requested, BLOCK_SESSIONS if backfill else len(requested))
    for chunk in chunks:
        series_rows: list[dict[str, object]] = []
        underlying_rows: list[dict[str, object]] = []
        for refdate in chunk:
            day = context.compute_day(refdate)
            series_rows.extend(day.series_rows)
            underlying_rows.extend(day.underlying_rows)
            summary.errors.extend(day.errors)
            for row in day.underlying_rows:
                label = str(row["signal_class"])
                summary.classes[label] = summary.classes.get(label, 0) + 1
                value = row["iv_atm_30d"]
                if isinstance(value, int | float):
                    iv_values.append(float(value))
            for row in day.series_rows:
                if row["iv"] is not None:
                    summary.with_iv += 1
            _count_flags(summary.flags, day.series_rows)
            _count_flags(summary.flags, day.underlying_rows)

        written_dates = {row["refdate"] for row in series_rows}
        written_dates |= {row["refdate"] for row in underlying_rows}
        if series_rows:
            stats = write_metrics_series(
                series_rows, data_dir, refdates_replaced=written_dates
            )
            summary.series_sha256 = stats.sha256
            summary.series += len(series_rows)
        if underlying_rows:
            stats = write_metrics_underlying(
                underlying_rows, data_dir, refdates_replaced=written_dates
            )
            summary.underlying_sha256 = stats.sha256
            summary.underlyings += len(
                {(row["refdate"], row["underlying"]) for row in underlying_rows}
            )
        log.info(
            "metrics bloco %s..%s: %d série(s), %d papel(is)",
            chunk[0].isoformat(),
            chunk[-1].isoformat(),
            len(series_rows),
            len({row["underlying"] for row in underlying_rows}),
        )
        del series_rows
        del underlying_rows
        context.prune(chunk[-1])

    summary.iv_atm_median = statistics.median(iv_values) if iv_values else None
    summary.seconds = time.perf_counter() - started
    if summary.errors:
        log.warning("metrics terminou com %d erro(s) de papel", len(summary.errors))
    return summary


def _chunks(items: Sequence[date], size: int) -> list[list[date]]:
    """Fatia a lista de sessões em blocos de escrita (`size` ≥ 1)."""
    step = max(1, size)
    return [list(items[start : start + step]) for start in range(0, len(items), step)]


def _count_flags(counter: dict[str, int], rows: Sequence[Mapping[str, object]]) -> None:
    """Conta cada flag uma vez por linha (série/papel) no resumo da CLI."""
    for row in rows:
        for flag in row.get("flags") or ():  # type: ignore[union-attr]
            counter[flag] = counter.get(flag, 0) + 1


# --------------------------------------------------------------------------- #
# Um dia
# --------------------------------------------------------------------------- #


@dataclass
class _DayResult:
    """Resultado de uma sessão: linhas prontas para os dois escritores."""

    series_rows: list[dict[str, object]] = field(default_factory=list)
    underlying_rows: list[dict[str, object]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class _Context:
    """Cache e estado de uma execução do `b3o metrics` (um `run_metrics`)."""

    def __init__(
        self,
        *,
        service: QueryService,
        lake: Sequence[date],
        lake_last: date,
        data_dir: Path,
        model_version: int,
        first_session: date,
    ) -> None:
        self.service = service
        self.lake = tuple(lake)
        self.lake_set = set(self.lake)
        self.lake_last = lake_last
        self.data_dir = data_dir
        self.model_version = model_version
        self.first_session = first_session
        self.state = _RollingState()

        events = service.strike_events()
        self.scale_by_paper = scale_factors(events)
        self.dividends_by_paper = dividend_dates(events)
        self.events_by_paper: dict[str, list[tuple[date, str]]] = defaultdict(list)
        for event in events:
            self.events_by_paper[event.underlying].append((event.date, event.kind))
        self.earnings_by_paper: dict[str, list[tuple[date, str]]] = defaultdict(list)
        for row in service.earnings_rows():
            self.earnings_by_paper[row.ticker].append((row.date, row.kind))
        self.earnings_tickers = set(self.earnings_by_paper)

        self._extended: dict[date, tuple[date, ...]] = {}
        self._curves: dict[date, tuple[list[tuple[int, float]], str]] = {}
        self._oi: dict[date, dict[str, int]] = {}
        self._series_5d: dict[str, int] = {}
        self._spot: dict[str, list[SpotBar]] = {}
        self._window_20: dict[date, tuple[date, ...]] = {}
        self._recent: dict[str, dict[str, date]] = defaultdict(dict)
        self._seed_recent_tickers()
        self._spot_start = first_session - timedelta(days=RV_LOOKBACK_DAYS)
        self._spot_end = first_session
        self._underlying_seeded = False

    # ------------------------------ leituras ------------------------------ #

    def curve(self, refdate: date) -> tuple[list[tuple[int, float]], str]:
        """Curva da sessão (nós deduplicados por `du`, PRE antes de Selic) e a fonte."""
        cached = self._curves.get(refdate)
        if cached is not None:
            return cached
        nodes = self.service.rates_curve(refdate)
        chosen: dict[int, tuple[int, float, str]] = {}
        for node in nodes:
            priority = _RATE_SOURCE_PRIORITY.get(node.source, len(_RATE_SOURCE_PRIORITY))
            current = chosen.get(node.du)
            if current is None or (priority, node.source) < (current[0], current[2]):
                chosen[node.du] = (priority, node.rate_annual, node.source)
        if not chosen:
            result: tuple[list[tuple[int, float]], str] = ([], "rates_missing")
        else:
            curve = [(du, value[1]) for du, value in sorted(chosen.items())]
            source = min(chosen.values(), key=lambda item: (item[0], item[2]))[2]
            result = (curve, source)
        self._curves[refdate] = result
        return result

    def sessions_until(self, maturity: date) -> tuple[date, ...]:
        """Calendário do lake + extensão por dia útil/feriado até `maturity` (ADR-015)."""
        cached = self._extended.get(maturity)
        if cached is not None:
            return cached
        extension = b3calendar.trading_sessions(self.lake_last, maturity, known=self.lake)
        sessions = tuple(sorted(self.lake_set | set(extension)))
        self._extended[maturity] = sessions
        return sessions

    def trades_5d(self, refdate: date, tickers: Sequence[str]) -> dict[str, int]:
        """`Σ trades` das últimas 5 sessões do lake para cada ticker (§6.9)."""
        wanted = list(tickers)
        if not wanted:
            return {}
        history = self.service.series_history(wanted, refdate, LIQUIDITY_SESSIONS)
        return {
            ticker: sum(day.trades for day in days)
            for ticker, days in history.items()
        }

    def open_interest(self, refdate: date) -> dict[str, int]:
        """`ticker -> total_qty` da sessão (cache por data; `{}` = sem OI, P-N)."""
        cached = self._oi.get(refdate)
        if cached is None:
            cached = self.service.open_interest_on(refdate)
            self._oi[refdate] = cached
        return cached

    def spot_bars(self, underlying: str) -> list[SpotBar]:
        """Barras do papel no recorte do run (filtradas por data no cálculo do dia)."""
        cached = self._spot.get(underlying)
        if cached is None:
            cached = list(
                self.service.spot_bars(underlying, self._spot_start, self._spot_end)
            )
            self._spot[underlying] = cached
        return cached

    def spot_range_until(self, refdate: date) -> None:
        """Amplia o recorte de spot para a última data pedida (uma leitura por papel)."""
        if refdate > self._spot_end:
            self._spot_end = refdate

    def average_quantity_20d(self, underlying: str, refdate: date) -> float | None:
        """Quantidade média negociada do **papel** nas últimas 20 sessões (ações/dia).

        Mesma convenção do relatório de liquidez do F0.11: as 20 sessões vêm do
        calendário do lake (sessão sem negócio conta como 0 no numerador e no
        denominador) e a barra escolhida já é o mercado preferido (`010` → `020`)
        resolvido pelo `spot_bars`. É o denominador de `days_to_liquidate` na
        **mesma unidade do OI** (`Total de posições`, em ações do ativo-objeto).
        """
        window = self.window_20(refdate)
        if not window:
            return None
        bars = {
            bar.refdate: bar
            for bar in self.spot_bars(underlying)
            if bar.refdate <= refdate
        }
        total = sum(bars[day].qty if day in bars else 0 for day in window)
        return total / len(window)

    def window_20(self, refdate: date) -> tuple[date, ...]:
        """Últimas 20 sessões do lake até `refdate` (inclusive)."""
        cached = self._window_20.get(refdate)
        if cached is None:
            cached = tuple([day for day in self.lake if day <= refdate][-20:])
            self._window_20[refdate] = cached
        return cached

    def _seed_recent_tickers(self) -> None:
        """Séries vistas nas 20 sessões anteriores: o OI cobre série sem negócio hoje.

        O `open_interest_on` devolve ~33 k séries de opção, mas o COTAHIST só lista
        quem negociou no dia (~14 k). Somar o OI apenas das séries de hoje
        subestima a posição do papel (PETR4 dava 6,6 dias contra os 8,95 do
        relatório de liquidez do F0.11); a janela de 20 sessões reconstrói o
        universo de séries do papel para o numerador.
        """
        previous = [day for day in self.lake if day < self.first_session][-20:]
        for day in previous:
            try:
                options = self.service.options_on(day)
            except SourceFailure:
                continue
            for option in options:
                self._recent[option.underlying][option.ticker] = day

    # ---------------------------- histórico ------------------------------ #

    def prune(self, refdate: date) -> None:
        """Descarta o estado de legs vencidos e de séries fora da janela de 20 sessões.

        O `--backfill` de três anos passa por ~600 k legs; manter o deque de IV de
        cada um até o fim custaria centenas de MB sem uso (a série vencida não
        aparece mais). A poda roda por bloco de escrita, não por dia.
        """
        stale = [
            leg_id
            for leg_id in self.state.series_iv
            if _leg_maturity(leg_id) is not None and _leg_maturity(leg_id) < refdate
        ]
        for leg_id in stale:
            del self.state.series_iv[leg_id]
        cutoff = refdate - timedelta(days=40)
        for tickers in list(self._recent.values()):
            for ticker, last_seen in list(tickers.items()):
                if last_seen < cutoff:
                    del tickers[ticker]

    def _seed_underlying(self) -> None:
        """Semeia as janelas do papel com o histórico **anterior** à primeira data."""
        if self._underlying_seeded:
            return
        self._underlying_seeded = True
        try:
            rows = read_metrics_underlying(self.data_dir)
        except SourceFailure as exc:  # histórico ilegível não pode derrubar o cálculo
            log.warning("metrics: histórico de papel ilegível (%s); começa sem janela", exc)
            return
        for row in rows:
            refdate = row.get("refdate")
            if not isinstance(refdate, date) or refdate >= self.first_session:
                continue
            underlying = str(row.get("underlying"))
            _push_optional(self.state.vrp[underlying], row.get("vrp"))
            _push_optional(self.state.iv_atm[underlying], row.get("iv_atm_30d"))
            _push_optional(self.state.rr25[underlying], row.get("rr25"))

    def _seed_series(self, leg_ids: Sequence[str]) -> None:
        """Semeia o deque de IV por leg com o histórico anterior à primeira data."""
        if self.state.series_seeded:
            return
        self.state.series_seeded = True
        try:
            rows = read_metrics_series(
                self.data_dir, leg_ids=leg_ids, until=self.first_session
            )
        except SourceFailure as exc:
            log.warning("metrics: histórico de série ilegível (%s); começa sem janela", exc)
            return
        for row in rows:
            _push_optional(self.state.series_iv[str(row.get("leg_id"))], row.get("iv"))

    # ------------------------------ um dia ------------------------------- #

    def compute_day(self, refdate: date) -> _DayResult:
        """Calcula as duas tabelas da sessão (uma linha por série e por papel)."""
        self._seed_underlying()
        self.spot_range_until(refdate)
        result = _DayResult()
        try:
            options = self.service.options_on(refdate)
        except SourceFailure as exc:
            result.errors.append(f"{refdate.isoformat()}: {exc.code} — {exc.message}")
            return result
        if not options:
            result.errors.append(f"{refdate.isoformat()}: sem linhas de opção no lake")
            return result

        grouped: dict[str, list[OptionDay]] = defaultdict(list)
        for option in options:
            grouped[option.underlying].append(option)
            self._recent[option.underlying][option.ticker] = refdate

        tickers = [option.ticker for option in options]
        self._seed_series([option.leg_id for option in options])
        trades_5d = self.trades_5d(refdate, tickers)

        for underlying in sorted(grouped):
            try:
                row, series = self._paper(
                    refdate, underlying, grouped[underlying], trades_5d
                )
            except (SourceFailure, ValueError, ArithmeticError) as exc:
                result.errors.append(
                    f"{refdate.isoformat()} {underlying}: {type(exc).__name__}: {exc}"
                )
                continue
            result.series_rows.extend(row)
            result.underlying_rows.append(series.to_row())
        return result

    # ------------------------------ um papel ----------------------------- #

    def _paper(
        self,
        refdate: date,
        underlying: str,
        group: Sequence[OptionDay],
        trades_5d: Mapping[str, int],
    ) -> tuple[list[dict[str, object]], UnderlyingMetrics]:
        """Calcula todas as séries e a linha do papel para a sessão."""
        spot = float(group[0].spot_close)
        market = group[0].underlying_market
        maturities = sorted({option.maturity for option in group})
        max_maturity = maturities[-1]
        sessions = self.sessions_until(max_maturity)
        curve, rate_source = self.curve(refdate)
        t_estimated = max_maturity > self.lake_last

        t_by_maturity: dict[date, float] = {}
        log_df_by_maturity: dict[date, float | None] = {}
        df_by_maturity: dict[date, float | None] = {}
        for maturity in maturities:
            if maturity <= refdate:
                t_by_maturity[maturity] = 0.0
                log_df_by_maturity[maturity] = None
                df_by_maturity[maturity] = None
                continue
            days = b3calendar.business_days(sessions, refdate, maturity)
            t_value = days / 252.0
            t_by_maturity[maturity] = t_value
            if not curve or t_value <= 0.0:
                log_df_by_maturity[maturity] = None
                df_by_maturity[maturity] = None
                continue
            log_df = log_df_interp_or_none(days, curve)
            log_df_by_maturity[maturity] = log_df
            df_by_maturity[maturity] = math.exp(log_df) if log_df is not None else None

        dividends = self.dividends_by_paper.get(underlying, frozenset())
        forwards: dict[date, float | None] = {}
        outlier_maturities: set[date] = set()
        for maturity in maturities:
            pairs = self._parity_chain(group, maturity, spot, dividends, refdate)
            usable = _usable_pairs(pairs)
            if len(usable) < MIN_FORWARD_PAIRS:
                # Cruzamento com 2 pontos é ruído: sem corroboração, sem forward.
                forwards[maturity] = None
                continue
            estimate = forward_by_parity(pairs, spot=spot)
            forward_value = estimate.forward
            if forward_value is None:
                forwards[maturity] = None
                continue
            log_df = log_df_by_maturity[maturity]
            t_value = t_by_maturity[maturity]
            if (
                log_df is not None
                and t_value > 0.0
                and not _carry_plausible(forward_value, spot, t_value, log_df)
            ):
                forwards[maturity] = None
                outlier_maturities.add(maturity)
                continue
            forwards[maturity] = forward_value

        scale_today = any(
            day == refdate and kind == "scale"
            for day, kind in self.events_by_paper.get(underlying, ())
        )
        evaluated: list[EvaluatedQuote] = []
        for option in group:
            if option.strike <= 0:
                continue  # órfã de ajuste de grade (AGENTS): sai, conta `zero_strike`
            quote = OptionQuote(
                refdate=refdate,
                ticker=option.ticker,
                underlying=underlying,
                underlying_market=market,
                maturity=option.maturity,
                leg_id=option.leg_id,
                leg_index=option.leg_index,
                side=option.side,
                style=option.style,
                strike=option.strike,
                strike_raw=option.strike_raw,
                distribution_id=option.distribution_id,
                quote_factor=option.quote_factor,
                close=option.close,
                best_bid=option.best_bid,
                best_ask=option.best_ask,
                trades=option.trades,
                volume=option.volume,
                spot=spot,
                t=t_by_maturity[option.maturity],
            )
            extra = (
                (series_mod.FLAG_FORWARD_OUTLIER,)
                if option.maturity in outlier_maturities
                else ()
            )
            evaluated.append(
                evaluate_quote(
                    quote,
                    forward=forwards[option.maturity],
                    df=df_by_maturity[option.maturity],
                    lake_last=self.lake_last,
                    dividend_dates=dividends,
                    scale_event=scale_today,
                    trades_5d=trades_5d.get(option.ticker, 0),
                    extra_flags=extra,
                )
            )

        by_maturity: dict[date, list[EvaluatedQuote]] = defaultdict(list)
        for item in evaluated:
            by_maturity[item.quote.maturity].append(item)

        smiles: dict[date, list] = {}
        all_points: list = []
        for maturity in maturities:
            forward_value = forwards[maturity]
            if forward_value is None or forward_value <= 0.0:
                continue
            points = build_strike_smile(by_maturity[maturity], forward_value)
            if not points:
                continue
            converted = [point.point(maturity, t_by_maturity[maturity]) for point in points]
            smiles[maturity] = converted
            all_points.extend(converted)

        extra_flags: dict[tuple[date, int], tuple[str, ...]] = {}
        inconsistent_keys: set[tuple[date, int]] = set()
        checked_points: list = []
        for maturity, points in smiles.items():
            forward_value = forwards.get(maturity)
            if forward_value is None or forward_value <= 0.0:
                continue
            checked_points.extend(
                point
                for point in points
                if abs(math.log(point.strike / forward_value)) <= SMILE_MONEYNESS_BAND
            )
        if checked_points:
            representative = _representative_forward(forwards, t_by_maturity)
            report = check_chain(
                checked_points,
                representative,
                butterfly_tol=SMILE_BUTTERFLY_TOL,
                monotonicity_tol=SMILE_MONOTONICITY_TOL,
            )
            ticker_to_key = {
                point.ticker: (point.maturity, point.strike)
                for point in checked_points
            }
            extra_flags = inconsistent_flags(
                dict(report.series_flags), ticker_to_key
            )
            # `inconsistent` NÃO tira o strike do ATM: o ATM precisa exatamente
            # dos dois strikes vizinhos de F, e remover um deles troca o número
            # por outro strike (foi o defeito medido: PETR4 caía de 32,84 para
            # 29,83 com o strike ATM marcado). O ATM usa **todos** os strikes com
            # IV válida; a flag serve para excluir a série do skew/butterfly.
            inconsistent_keys = set(extra_flags)

        expiries: list[ExpiryAtm] = []
        for maturity in sorted(smiles):
            forward_value = forwards[maturity]
            if forward_value is None:
                continue
            expiries.extend(atm_by_expiry(smiles[maturity], forward_value))
        atm = atm_interpolate_30d(expiries)
        expiry_by_maturity = {expiry.maturity: expiry for expiry in expiries}

        skew_maturity = (
            atm.front.maturity
            if atm.front is not None
            else _nearest_maturity(expiries, TARGET_T_30D)
        )
        skew_points = (
            [
                point
                for point in smiles[skew_maturity]
                if (skew_maturity, point.strike) not in inconsistent_keys
            ]
            if skew_maturity in smiles
            else []
        )
        expiry = expiry_by_maturity.get(skew_maturity) if skew_maturity else None
        skew = self._skew(
            skew_points,
            expiry.atm_iv if expiry is not None else None,
        )

        iv_90 = interpolate_variance_iv(expiries, TARGET_T_90D)
        slope = (
            iv_90 - atm.iv_atm_30d
            if iv_90 is not None and atm.iv_atm_30d is not None
            else None
        )

        front_maturity = skew_maturity
        forward_front = forwards.get(front_maturity) if front_maturity else None
        t_front = t_by_maturity.get(front_maturity, 0.0) if front_maturity else 0.0
        log_df_front = log_df_by_maturity.get(front_maturity) if front_maturity else None
        straddle = _atm_straddle(by_maturity.get(front_maturity, []), forward_front, spot)
        exp_move = expected_move_cents(straddle, spot)
        implied_div = implied_div_yield_pct(log_df_front, forward_front, spot, t_front)

        rv, rv_flags = self._realized_vol(refdate, underlying, dividends)

        vrp_value = (
            vrp(atm.iv_atm_30d, rv[30])
            if atm.iv_atm_30d is not None and rv[30] is not None
            else None
        )
        vrp_window = window_with_today(self.state.vrp[underlying], vrp_value)
        vrp_z_value = vrp_z(vrp_window) if vrp_window is not None else None
        iv_window = window_with_today(self.state.iv_atm[underlying], atm.iv_atm_30d)
        iv_rank_value = iv_rank_252(iv_window) if iv_window is not None else None
        iv_pct_value = iv_percentile_252(iv_window) if iv_window is not None else None
        rr_window = window_with_today(self.state.rr25[underlying], skew.rr25)
        label, _median, _std = und_mod.classify(
            vrp_value,
            vrp_z_value,
            skew.rr25,
            rr_window,
            atm.front.atm_iv if atm.front is not None else None,
            atm.back.atm_iv if atm.back is not None else None,
            skew.n_strikes_band,
        )

        earnings_rows = self.earnings_by_paper.get(underlying, [])
        earnings_flag = earnings_before_expiry(
            earnings_rows, refdate, front_maturity
        )

        oi_map = self.open_interest(refdate)
        window = self.window_20(refdate)
        window_start = window[0] if window else refdate
        recent = self._recent.get(underlying, {})
        paper_tickers = {
            ticker for ticker, last_seen in recent.items() if last_seen >= window_start
        }
        present_oi = [ticker for ticker in paper_tickers if ticker in oi_map]
        total_oi = sum(oi_map[ticker] for ticker in present_oi)
        avg_quantity = self.average_quantity_20d(underlying, refdate)
        liquidation = days_to_liquidate(
            total_oi if present_oi else None, avg_quantity
        )

        flags: set[str] = set()
        if any(option.strike <= 0 for option in group):
            flags.add(series_mod.FLAG_ZERO_STRIKE)
        if not curve:
            for maturity in maturities:
                if t_by_maturity[maturity] > 0.0:
                    flags.add(series_mod.FLAG_NO_RATES)
                    break
        if not any(
            forwards[maturity] is not None
            for maturity in maturities
            if t_by_maturity[maturity] > 0.0
        ):
            flags.add(FLAG_NO_FORWARD)
        if t_estimated:
            flags.add(series_mod.FLAG_T_ESTIMATED)
        if scale_today:
            flags.add(series_mod.FLAG_SCALE_EVENT)
        flags.update(atm.flags)
        flags.update(skew.flags)
        if slope is None:
            flags.add(FLAG_NO_90D)
        if earnings_flag:
            flags.add("earnings_before_expiry")
        if underlying not in self.earnings_tickers:
            flags.add("earnings_unmapped")
        if not present_oi:
            flags.add(FLAG_OI_UNAVAILABLE)
        elif liquidation is None:
            flags.add(FLAG_VOLUME_UNAVAILABLE)
        if outlier_maturities:
            flags.add(series_mod.FLAG_FORWARD_OUTLIER)
        for window_value in RV_WINDOWS:
            flags.update(rv_flags.get(window_value, ()))
        if any(
            item.quote.t <= 0.0 for item in evaluated
        ):
            flags.add(series_mod.FLAG_IV_EXPIRED)

        rows: list[dict[str, object]] = []
        for item in evaluated:
            key = (item.quote.maturity, item.quote.strike)
            percentile = self._series_percentile(item.quote.leg_id, item.iv_pct)
            rows.append(
                series_metrics(
                    item,
                    iv_pct_252=percentile,
                    model_version=self.model_version,
                    extra_flags=extra_flags.get(key, ()),
                ).to_row()
            )

        series = UnderlyingMetrics(
            refdate=refdate,
            underlying=underlying,
            spot=spot,
            underlying_market=market,
            iv_atm_30d=atm.iv_atm_30d,
            iv_rank_252=iv_rank_value,
            iv_pct_252=iv_pct_value,
            rv20=rv[20],
            rv30=rv[30],
            rv60=rv[60],
            vrp=vrp_value,
            vrp_z_252=vrp_z_value,
            rr25=skew.rr25,
            bfly25=skew.bfly25,
            ts_slope_30_90=slope,
            exp_move_1s_30d=exp_move,
            implied_div_yield_30d=implied_div,
            days_to_liquidate=liquidation,
            forward_30d=forward_front,
            rate_source=rate_source,
            signal_class=label,
            flags=sorted(flags),
            model_version=self.model_version,
        )

        self.state.push_underlying(underlying, vrp_value, atm.iv_atm_30d, skew.rr25)
        for item in evaluated:
            if item.iv_pct is not None:
                self.state.series_iv[item.quote.leg_id].append(item.iv_pct)
        return rows, series

    # ------------------------------ peças -------------------------------- #

    def _parity_chain(
        self,
        group: Sequence[OptionDay],
        maturity: date,
        spot: float,
        dividends: Sequence[date],
        refdate: date,
    ) -> list[ParityQuote]:
        """Pares call/put do vencimento em centavos por ação (ADR-017: o `close`)."""
        calls: dict[int, tuple[tuple[int, int, str], OptionDay]] = {}
        puts: dict[int, tuple[tuple[int, int, str], OptionDay]] = {}
        for option in group:
            if option.maturity != maturity or option.strike <= 0:
                continue
            target = calls if option.side == "C" else puts
            # preferir europeia, depois maior volume, depois ticker (determinístico)
            key = (0 if option.style == "E" else 1, -option.volume, option.ticker)
            current = target.get(option.strike)
            if current is None or key < current[0]:
                target[option.strike] = (key, option)

        pairs: list[ParityQuote] = []
        for strike in sorted(set(calls) & set(puts)):
            call = calls[strike][1]
            put = puts[strike][1]
            ex_div = (
                call.style == "A"
                and strike < spot
                and any(refdate <= day <= maturity for day in dividends)
            )
            pairs.append(
                ParityQuote(
                    strike=strike,
                    call_mid=call.close / call.quote_factor,
                    put_mid=put.close / put.quote_factor,
                    call_style=call.style,
                    put_style=put.style,
                    call_ex_div=ex_div,
                )
            )
        return pairs

    def _skew(
        self,
        points: Sequence,
        atm_iv: float | None,
    ) -> SkewMetrics:
        """RR25/BFLY25 do vencimento de referência, **sem** os strikes inconsistentes.

        `inconsistent` tira a série do skew/butterfly (a cauda é onde o ruído de
        tick pesa mais), mas não do ATM — ver o comentário no `_paper`.
        """
        if not points:
            return SkewMetrics(None, None, 0, ("no_expiry",))
        chain = [
            DeltaPoint(delta=point.delta, iv_pct=point.iv_pct)
            for point in points
            if point.delta is not None and point.iv_pct is not None
        ]
        return rr25_bfly25(chain, atm_iv)

    def _realized_vol(
        self,
        refdate: date,
        underlying: str,
        dividends: Sequence[date],
    ) -> tuple[dict[int, float | None], dict[int, tuple[str, ...]]]:
        """Yang-Zhang 20/30/60 com fator de escala e datas ex de provento."""
        bars = [bar for bar in self.spot_bars(underlying) if bar.refdate <= refdate]
        if not bars:
            return {window: None for window in RV_WINDOWS}, {}
        dates = [bar.refdate for bar in bars]
        factors = self.scale_by_paper.get(underlying, {})
        adj = event_factor_series(dates, factors)
        ohlc = [
            OhlcBar(
                refdate=bar.refdate,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                adj_factor=adj[bar.refdate],
            )
            for bar in bars
        ]
        results: dict[int, float | None] = {}
        flag_map: dict[int, tuple[str, ...]] = {}
        for window in RV_WINDOWS:
            outcome = yang_zhang_vol(ohlc, window, ex_div_dates=dividends)
            results[window] = outcome.value
            flag_map[window] = outcome.flags
        return results, flag_map

    def _series_percentile(self, leg_id: str, iv_pct: float | None) -> float | None:
        """Percentil inclusivo da IV da série em 252 sessões (hoje incluso, §4.6)."""
        if iv_pct is None:
            return None
        history = self.state.series_iv.get(leg_id)
        if not history or len(history) < HISTORY_WINDOW - 1:
            return None
        values = list(history)[-(HISTORY_WINDOW - 1) :] + [iv_pct]
        return 100.0 * sum(1 for value in values if value <= iv_pct) / len(values)


# --------------------------------------------------------------------------- #
# Auxiliares puros do dia
# --------------------------------------------------------------------------- #


def _leg_maturity(leg_id: str) -> date | None:
    """Vencimento embutido no `leg_id` (`ticker|maturity|leg_index`)."""
    parts = leg_id.split("|")
    if len(parts) != 3:
        return None
    try:
        return date.fromisoformat(parts[1])
    except ValueError:
        return None


def _push_optional(bucket: deque[float], value: object) -> None:
    """Empurra um número finito no deque; `None`/bool/NaN não entram."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return
    number = float(value)
    if math.isfinite(number):
        bucket.append(number)


def _usable_pairs(pairs: Sequence[ParityQuote]) -> list[ParityQuote]:
    """Pares que a paridade aceita (mesmo filtro de estilo do `forward.py`).

    Put `E` (as puts da B3 são todas europeias), estilo conhecido e call
    americana só sem ex-dividendo. O cruzamento de 2 pontos só é aceito com
    **3 pares utilizáveis** na cadeia como corroboração.
    """
    return [
        pair
        for pair in pairs
        if pair.put_style == "E"
        and pair.call_style is not None
        and not (pair.call_style == "A" and pair.call_ex_div)
    ]


def _carry_plausible(
    forward: float,
    spot: float,
    t: float,
    log_df: float,
) -> bool:
    """Carry plausível: `F` pode cair por provento, mas não subir acima da curva.

    `ln(F/S)/T = r − q` (taxa menos dividendo esperado). As duas assimetrias são
    reais e medidas:

    - **lado positivo** (F acima do que a curva permite) só existe com print
      errado/par de 2 pontos — rejeita acima de 5% a.a.;
    - **lado negativo** é dividendo esperado, legítimo: BBAS3 em 18/12/2026 tem
      `q ≈ 7,3% a.a.` e caía na regra simétrica (perdia o ATM). Passa a valer até
      **25% a.a.** de dividendo implícito; acima disso o número não é mercado.

    Fora da faixa, o vencimento fica sem `F`, a série ganha `forward_outlier` e o
    ATM não o usa — nunca se "conserta" o forward.
    """
    if spot <= 0.0 or t <= 0.0:
        return False
    rate = -log_df / t
    carry = math.log(forward / spot) / t
    if carry - rate > FORWARD_CARRY_TOL:
        return False
    return rate - carry <= FORWARD_DIVIDEND_MAX


def _representative_forward(
    forwards: Mapping[date, float | None],
    t_by_maturity: Mapping[date, float],
) -> float | None:
    """Forward do vencimento mais próximo de 30d — âncora de tolerância do `check_chain`."""
    candidates = [
        (abs(t_by_maturity[maturity] - TARGET_T_30D), maturity, forward)
        for maturity, forward in forwards.items()
        if forward is not None and forward > 0.0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _nearest_maturity(expiries: Sequence[ExpiryAtm], target_t: float) -> date | None:
    """Vencimento de `|t − target|` mínimo (desempate pelo `t` menor)."""
    expiry = nearest_expiry(expiries, target_t)
    return expiry.maturity if expiry is not None else None


def _atm_straddle(
    evaluated: Sequence[EvaluatedQuote],
    forward: float | None,
    spot: float,
) -> float | None:
    """Straddle (call + put por ação) no strike mais próximo de `F` (proxy: spot).

    Preço do `close` por ação (ADR-017). Sem strike com as duas pernas → `None`
    (o expected move não se inventa por interpolação de prêmio).
    """
    reference = forward if forward is not None and forward > 0.0 else spot
    if reference <= 0.0:
        return None
    by_strike: dict[int, dict[str, float]] = defaultdict(dict)
    for item in evaluated:
        by_strike[item.quote.strike][item.quote.side] = item.price
    candidates = [
        strike
        for strike, prices in by_strike.items()
        if strike > 0 and prices.get("C", 0.0) > 0.0 and prices.get("P", 0.0) > 0.0
    ]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda strike: (abs(strike - reference), strike))
    prices = by_strike[nearest]
    return prices["C"] + prices["P"]


__all__ = [
    "BLOCK_SESSIONS",
    "MetricsSummary",
    "RV_WINDOWS",
    "run_metrics",
]
