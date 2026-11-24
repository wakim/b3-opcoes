"""Carregador do backtest (F1b, spec §6.13) — I/O via `QueryService`, fórmula via `derive/`.

Este módulo **não** calcula Yang-Zhang nem classifica evento: ele monta as
observações ``(papel, data, classe, iv_atm_30d, rv_fwd20, earnings_na_janela)``
lendo o lake pela camada única de consulta e reusa ``derive/realized.py`` e
``derive/corporate_events.py``. A agregação e o veredito são puros
(``derive/backtest.py``). Regra de SQL do contrato §4.2: nada de ``duckdb`` aqui.

Convenções do ``rv_fwd20`` (documentadas no relatório):

- janela = **20 sessões seguintes** no calendário do lake (``data+1 … data+20``).
  O Yang-Zhang recebe ``window = 21`` barras (a barra-base de ``data`` + as 20
  futuras) → 20 retornos, exatamente o ``rv_fwd20`` pedido;
- usa o **mesmo mercado** (``010``/``020``) da linha de métrica — nunca mistura
  lote padrão com fracionário na mesma série;
- fator de escala acumulado nas barras **anteriores** a cada evento ``scale``
  (``corporate_events.event_factor_series``); gap de provento excluído pelas
  ``ex_div_dates`` do B2. A exclusão **puxa barras mais para trás** (a janela não
  encolhe — mesma convenção do ``rv20/30/60`` do pipeline);
- sem 20 sessões futuras no lake, sem barra na sessão final da janela, ou sem
  barras suficientes → ``None`` e a observação sai, **contada por motivo**;
- ``edge = iv_atm_30d(data) − rv_fwd20`` só entra com os dois valores **e**
  ``vrp_z_252`` preenchido (aquecimento de 252 sessões).

``earnings_na_janela``: resultado ``actual`` deslocado **1 sessão** (folga do
ADR-010: ``DT_RECEB`` vale como evento do pregão seguinte) dentro de
``data+1 … data+20``. Vira recorte separado, não muda a classe.
"""

from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from b3o.config import Config
from b3o.derive.backtest import (
    BacktestReport,
    GateVerdict,
    Observation,
    aggregate,
    gate_verdicts,
)
from b3o.derive.corporate_events import dividend_dates, event_factor_series, scale_factors
from b3o.derive.realized import OhlcBar, yang_zhang_vol
from b3o.derive.signal import CLASS_ORDER, NONE, PREMIUM_CHEAP, PREMIUM_RICH
from b3o.ingest.errors import SourceError, SourceFailure
from b3o.query.service import QueryService, SpotBar, UnderlyingMetricRow

log = logging.getLogger(__name__)

FWD_SESSIONS = 20
"""Sessões seguintes da janela do `rv_fwd20` (spec §6.13, pré-registrado)."""

RV_LOOKBACK_DAYS = 120
"""Dias corridos lidos antes da data para a barra-base e o pullback do ex-div.

Mesma constante do pipeline (`RV_LOOKBACK_DAYS`): cobre com folga a barra-base
mais as exclusões de provento sem precisar reler o lake por observação.
"""

PAST_RV_SESSIONS = 30
"""Sessões da janela de RV passada (rv30) usada no recorte informativo de 26/10."""

TRAP_DATE = date(2026, 10, 26)
"""Choque real de mercado (ADR-034): IBOV −14,35% no dia seguinte ao 2º turno.

Não é defeito do dado (o forward por paridade acompanha o spot novo). O nome da
constante ficou por compatibilidade; o recorte continua só informativo.
"""

LIQUID_PAPERS: tuple[str, ...] = (
    "PETR4", "VALE3", "ITUB4", "BBAS3", "BBDC4", "BOVA11", "B3SA3", "AXIA3",
    "SMAL11", "PRIO3", "ITSA4", "MGLU3", "GGBR4", "LREN3", "USIM5",
)
"""Os 15 com grade líquida (AGENTS, F0.11) — presença obrigatória no por-papel."""


@dataclass
class BacktestMeta:
    """Contagens do carregamento: o que entrou e o que saiu, por motivo (P-N)."""

    start: date
    end: date
    sessions: int
    rows_in_period: int = 0
    with_z: int = 0
    first_z_date: date | None = None
    dropped_no_z: int = 0
    dropped_no_iv: int = 0
    dropped_no_future_sessions: int = 0
    dropped_no_bar_at_end: int = 0
    dropped_rv_failed: int = 0
    rv_fail_flags: dict[str, int] = field(default_factory=dict)
    observations: int = 0
    model_versions: tuple[int, ...] = ()


@dataclass
class BacktestRun:
    """Resultado completo: relatório puro, metadados, veredito e texto pronto."""

    report: BacktestReport
    meta: BacktestMeta
    verdicts: dict[str, GateVerdict]
    markdown: str
    summary: list[str]


def load_observations(
    config: Config, *, start: date | None = None, end: date | None = None
) -> tuple[list[Observation], BacktestMeta]:
    """Monta as observações do período lendo o lake via `QueryService`.

    Ordem crescente de `refdate` (a classe e o z já vêm gravados; nada se
    recalcula). Cada descarte é contado por motivo — nunca zero silencioso.
    """
    service = QueryService(Path(config.data_dir))
    lake = service.lake_calendar()
    if not lake:
        raise SourceFailure(
            SourceError(
                code="lake_missing",
                message=f"nenhuma partição do COTAHIST em {Path(config.data_dir) / 'raw_cotahist'}",
                hint="rode `b3o prepare` antes de `b3o backtest`",
            )
        )
    first = start or lake[0]
    last = end or lake[-1]
    period = [day for day in lake if first <= day <= last]
    if not period:
        raise SourceFailure(
            SourceError(
                code="no_session",
                message=f"nenhuma sessão do lake no intervalo {first}→{last}",
                hint=f"o lake tem {len(lake)} sessões, de {lake[0]} a {lake[-1]}",
            )
        )
    meta = BacktestMeta(start=period[0], end=period[-1], sessions=len(period))

    rows = service.metrics_underlying_rows(period[0], period[-1])
    meta.rows_in_period = len(rows)
    meta.model_versions = tuple(sorted({row.model_version for row in rows}))

    events = service.strike_events()
    scale_by_paper = scale_factors(events)
    dividends_by_paper = dividend_dates(events)

    lake_index = {day: index for index, day in enumerate(lake)}
    earnings_sessions = _earnings_sessions(service, lake)
    bars_cache: dict[tuple[str, str], tuple[list[SpotBar], list[date]]] = {}

    observations: list[Observation] = []
    for row in rows:
        if row.vrp_z_252 is None:
            meta.dropped_no_z += 1
            continue
        meta.with_z += 1
        if meta.first_z_date is None or row.refdate < meta.first_z_date:
            meta.first_z_date = row.refdate
        if row.iv_atm_30d is None:
            meta.dropped_no_iv += 1
            continue
        observation = _observation(
            row, lake, lake_index, scale_by_paper, dividends_by_paper,
            earnings_sessions, bars_cache, service, meta,
        )
        if observation is not None:
            observations.append(observation)
    meta.observations = len(observations)
    return observations, meta


def _earnings_sessions(service: QueryService, lake: Sequence[date]) -> dict[str, set[date]]:
    """Datas `actual` da CVM deslocadas para o pregão seguinte (folga ADR-010).

    `DT_RECEB` é a entrega do documento (sai após o fechamento); o pregão afetado
    é o **próximo** do calendário do lake. `bisect_right` acha a primeira sessão
    estritamente posterior. Entrega depois da última sessão do lake não tem
    pregão seguinte no histórico — fica de fora (não se inventa data).
    """
    sessions: dict[str, set[date]] = {}
    for row in service.earnings_rows():
        if row.kind != "actual":
            continue
        index = bisect_right(lake, row.date)
        if index < len(lake):
            sessions.setdefault(row.ticker, set()).add(lake[index])
    return sessions


def _bars_for(
    service: QueryService,
    cache: dict[tuple[str, str], tuple[list[SpotBar], list[date]]],
    lake: Sequence[date],
    underlying: str,
    market: str,
) -> tuple[list[SpotBar], list[date]]:
    """Barras do papel no mercado da métrica + suas datas, cacheadas por (papel, mercado)."""
    key = (underlying, market)
    cached = cache.get(key)
    if cached is None:
        bars = service.spot_bars_in_market(underlying, lake[0], lake[-1], market)
        cached = (bars, [bar.refdate for bar in bars])
        cache[key] = cached
    return cached


def _observation(
    row: UnderlyingMetricRow,
    lake: Sequence[date],
    lake_index: dict[date, int],
    scale_by_paper: dict[str, dict[date, float]],
    dividends_by_paper: dict[str, frozenset[date]],
    earnings_sessions: dict[str, set[date]],
    bars_cache: dict[tuple[str, str], tuple[list[SpotBar], list[date]]],
    service: QueryService,
    meta: BacktestMeta,
) -> Observation | None:
    """Resolve o `rv_fwd20` de uma linha; `None` + contagem quando não dá."""
    index = lake_index[row.refdate]
    future = lake[index + 1 : index + 1 + FWD_SESSIONS]
    if len(future) < FWD_SESSIONS:
        meta.dropped_no_future_sessions += 1
        return None
    window_end = future[-1]

    bars, bar_dates = _bars_for(
        service, bars_cache, lake, row.underlying, row.underlying_market
    )
    lo = row.refdate - timedelta(days=RV_LOOKBACK_DAYS)
    start_i = bisect_left(bar_dates, lo)
    end_i = bisect_right(bar_dates, window_end)
    window_bars = bars[start_i:end_i]
    if not window_bars or window_bars[-1].refdate != window_end:
        meta.dropped_no_bar_at_end += 1
        return None

    dates = [bar.refdate for bar in window_bars]
    adj = event_factor_series(dates, scale_by_paper.get(row.underlying, {}))
    ohlc = [
        OhlcBar(
            refdate=bar.refdate,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            adj_factor=adj[bar.refdate],
        )
        for bar in window_bars
    ]
    outcome = yang_zhang_vol(
        ohlc,
        FWD_SESSIONS + 1,
        ex_div_dates=dividends_by_paper.get(row.underlying, frozenset()),
    )
    if outcome.value is None:
        meta.dropped_rv_failed += 1
        for flag in outcome.flags:
            meta.rv_fail_flags[flag] = meta.rv_fail_flags.get(flag, 0) + 1
        return None

    future_set = set(future)
    earnings = any(
        session in future_set for session in earnings_sessions.get(row.underlying, ())
    )
    trap_window = lake[max(0, index - PAST_RV_SESSIONS + 1) : index + 1 + FWD_SESSIONS]
    return Observation(
        underlying=row.underlying,
        refdate=row.refdate,
        signal_class=row.signal_class,
        iv_atm_30d=row.iv_atm_30d,
        rv_fwd20=outcome.value,
        earnings_na_janela=earnings,
        trap_in_rv_window=TRAP_DATE in trap_window,
    )


def run_backtest(
    config: Config,
    *,
    start: date | None = None,
    end: date | None = None,
    commit: str | None = None,
) -> BacktestRun:
    """Carrega, agrega, aplica o portão e renderiza o relatório + resumo."""
    observations, meta = load_observations(config, start=start, end=end)
    report = aggregate(observations)
    verdicts = gate_verdicts(report)
    markdown = render_report(report, meta, verdicts, commit=commit)
    summary = render_summary(report, meta, verdicts)
    return BacktestRun(report, meta, verdicts, markdown, summary)


# --------------------------------------------------------------------------- #
# Renderização (só a serialização arredonda — AGENTS)
# --------------------------------------------------------------------------- #


def _fmt(value: float | None, digits: int = 2) -> str:
    """Número formatado ou `—` para ausência (nunca zero silencioso)."""
    return "—" if value is None else f"{value:.{digits}f}"


def render_summary(
    report: BacktestReport, meta: BacktestMeta, verdicts: dict[str, GateVerdict]
) -> list[str]:
    """Resumo de uma tela (P-G): n, acerto, mediana e veredito por classe."""
    lines = [
        f"backtest {meta.start}→{meta.end} · {meta.sessions} sessões · "
        f"{meta.with_z} (papel,data) com z · {meta.observations} obs · "
        f"mv {','.join(str(v) for v in meta.model_versions) or '—'}"
    ]
    for label in CLASS_ORDER:
        stats = report.by_class[label]
        hit = _fmt(stats.hit_rate_pct, 1) + "%" if stats.hit_rate_pct is not None else "—"
        lines.append(
            f"  {label:<15} n={stats.n:<5} acerto={hit:<7} mediana={_fmt(stats.median)}"
        )
    for label, verdict in verdicts.items():
        marks = " ".join(
            f"{item.name}:{'✔' if item.passed else '✘'}" for item in verdict.criteria
        )
        lines.append(f"  portão {label}: {marks} → {'PASSA' if verdict.passed else 'NÃO PASSA'}")
    return lines


def render_report(
    report: BacktestReport,
    meta: BacktestMeta,
    verdicts: dict[str, GateVerdict],
    *,
    commit: str | None = None,
) -> str:
    """Relatório Markdown completo (`data/out/backtest.md`, fora do git — P-T)."""
    parts: list[str] = []
    parts.append("# Backtest das classes de sinal — F1b (o portão)\n")
    parts.append(
        f"- **Período:** {meta.start} → {meta.end} · **{meta.sessions} sessões** no lake\n"
        f"- **Linhas de `metrics_underlying` no período:** {meta.rows_in_period}\n"
        f"- **`(papel, data)` com `vrp_z_252`:** {meta.with_z}"
        + (f" (primeira em {meta.first_z_date})" if meta.first_z_date else "")
        + "\n"
        f"- **Observações válidas (IV + RV futura + z):** {meta.observations}\n"
        f"- **`model_version`:** {', '.join(str(v) for v in meta.model_versions) or '—'}\n"
        f"- **Commit:** {commit or 'não resolvido no container'}\n"
    )
    parts.append(
        "\n> **Aquecimento:** o `vrp_z_252` exige 252 observações de `vrp` **por papel** "
        "(não só 252 sessões). O período efetivo começa em "
        f"{meta.first_z_date or '—'} — isso é o aquecimento pré-registrado, não defeito.\n"
    )
    parts.append("\n## Descartes (por motivo)\n")
    parts.append(
        "| Motivo | n |\n|---|---:|\n"
        f"| sem `vrp_z_252` (aquecimento) | {meta.dropped_no_z} |\n"
        f"| sem `iv_atm_30d` | {meta.dropped_no_iv} |\n"
        f"| sem 20 sessões futuras no lake | {meta.dropped_no_future_sessions} |\n"
        f"| sem barra na sessão final da janela | {meta.dropped_no_bar_at_end} |\n"
        f"| RV futura falhou (Yang-Zhang) | {meta.dropped_rv_failed} |\n"
    )
    if meta.rv_fail_flags:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(meta.rv_fail_flags.items()))
        parts.append(f"\nFlags da RV que falhou: {detail}.\n")

    parts.append("\n## Por classe (com a base `none` ao lado)\n")
    parts.append(
        "| Classe | n | Acerto % | edge>0 % | edge<0 % | Mediana | P10 | P90 | Mín | Máx |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    for label in CLASS_ORDER:
        stats = report.by_class[label]
        parts.append(
            f"| {label} | {stats.n} | {_fmt(stats.hit_rate_pct, 1)} | "
            f"{_fmt(stats.edge_pos_pct, 1)} | {_fmt(stats.edge_neg_pct, 1)} | "
            f"{_fmt(stats.median)} | {_fmt(stats.p10)} | {_fmt(stats.p90)} | "
            f"{_fmt(stats.edge_min)} | {_fmt(stats.edge_max)} |\n"
        )
    parts.append(
        "\n`Acerto %` só existe para `premium_rich` (edge>0) e `premium_cheap` (edge<0). "
        "`skew_anomaly`/`event_pressure`: só distribuição, sem veredito. "
        "`edge` em pontos de vol; P10/P90 por interpolação linear.\n"
    )

    parts.append("\n## Por papel\n")
    parts.append(
        "Os 15 com grade líquida (AGENTS) e todos os papéis com classe ≠ `none`.\n\n"
        "| Papel | Classe | n | Acerto % | Mediana |\n|---|---|---:|---:|---:|\n"
    )
    shown = set(LIQUID_PAPERS)
    for ticker, classes in report.by_ticker.items():
        if any(classes[label].n > 0 for label in (PREMIUM_RICH, PREMIUM_CHEAP)):
            shown.add(ticker)
    for ticker in sorted(shown):
        classes = report.by_ticker.get(ticker)
        if classes is None:
            parts.append(f"| {ticker} | — | 0 | — | — |\n")
            continue
        for label in (PREMIUM_RICH, PREMIUM_CHEAP, NONE):
            stats = classes[label]
            if stats.n == 0 and label == NONE:
                continue
            parts.append(
                f"| {ticker} | {label} | {stats.n} | "
                f"{_fmt(stats.hit_rate_pct, 1)} | {_fmt(stats.median)} |\n"
            )

    parts.append("\n## Recorte: resultado na janela (earnings, folga ADR-010)\n")
    parts.append(
        "`actual` deslocado 1 sessão, dentro de `data+1 … data+20`.\n\n"
        "| Classe | Com resultado n | Acerto % | Mediana | Sem resultado n | Acerto % | Mediana |\n"
        "|---|---:|---:|---:|---:|---:|---:|\n"
    )
    for label in CLASS_ORDER:
        with_e = report.earnings_split[label][True]
        without_e = report.earnings_split[label][False]
        parts.append(
            f"| {label} | {with_e.n} | {_fmt(with_e.hit_rate_pct, 1)} | {_fmt(with_e.median)} | "
            f"{without_e.n} | {_fmt(without_e.hit_rate_pct, 1)} | {_fmt(without_e.median)} |\n"
        )

    parts.append("\n## Recorte informativo: choque de mercado de 2026-10-26\n")
    parts.append(
        "**Só informativo — o veredito usa o período completo, como pré-registrado.** "
        "Observações cuja janela de RV (passada = rv30, ou futura = rv_fwd20) contém "
        f"{TRAP_DATE}. É um choque **real** de mercado, não artefato de dado (ADR-034): "
        "IBOV −14,35%, real desvalorizado, sem evento corporativo (0 eventos B2, strikes "
        "inalterados) e com o forward por paridade acompanhando o spot novo. A RV de "
        "30–60 sessões depois é alta de verdade; o Yang-Zhang ainda a amplia porque "
        "várias aberturas saíram a ~0,50 do fechamento anterior. Excluir estas "
        "observações é escolha depois de ver o dado (P-L) — não limpeza.\n\n"
        "| `premium_cheap` | n | Acerto % | Mediana | P10 | P90 |\n"
        "|---|---:|---:|---:|---:|---:|\n"
    )
    for present, title in ((False, "sem a trap na janela"), (True, "com a trap na janela")):
        stats = report.cheap_trap_split[present]
        parts.append(
            f"| {title} | {stats.n} | {_fmt(stats.hit_rate_pct, 1)} | "
            f"{_fmt(stats.median)} | {_fmt(stats.p10)} | {_fmt(stats.p90)} |\n"
        )

    parts.append("\n## Tabela do portão (critérios pré-registrados — contrato onda-fg §2)\n")
    parts.append(
        "O implementador preenche; **a decisão de seguir/parar é do dono** (P-L). "
        "Nenhum limiar, janela, recorte ou subconjunto foi alterado.\n\n"
        "| Classe | Critério | Medido | Exigido | Veredito |\n|---|---|---:|---|---|\n"
    )
    for label, verdict in verdicts.items():
        for item in verdict.criteria:
            parts.append(
                f"| {label} | {item.name} | {_fmt(item.measured)} | {item.required} | "
                f"{'passa' if item.passed else 'não passa'} |\n"
            )
        parts.append(
            f"| **{label}** | **todos** | — | — | "
            f"**{'PASSA' if verdict.passed else 'NÃO PASSA'}** |\n"
        )
    parts.append(
        "\n## Convenções\n"
        "- `rv_fwd20`: Yang-Zhang (`derive/realized.py`) das 20 sessões seguintes, "
        "`window=21` barras (base + 20 futuras) → 20 retornos; mercado da linha de "
        "métrica; fator de escala nas barras anteriores ao evento; gap de provento "
        "excluído (a janela puxa barras mais para trás, não encolhe).\n"
        "- `edge = iv_atm_30d(data) − rv_fwd20`, em pontos de vol.\n"
        "- Classe = `signal_class` gravada em `metrics_underlying` (não recalculada).\n"
        "- Mediana: `statistics.median`; P10/P90: interpolação linear entre ranks.\n"
    )
    return "".join(parts)


__all__ = [
    "FWD_SESSIONS",
    "LIQUID_PAPERS",
    "TRAP_DATE",
    "BacktestMeta",
    "BacktestRun",
    "load_observations",
    "render_report",
    "render_summary",
    "run_backtest",
]
