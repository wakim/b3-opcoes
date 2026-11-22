"""Liquidez por papel (F0.11, spec §6.9) — PURO: recebe linhas, devolve números.

A pergunta que este módulo responde: **quais papéis têm mercado de opções de
verdade**, e não um pregão sortudo. Tudo sai das linhas do COTAHIST já tipadas
(`CotahistRow`), sem I/O, sem duckdb e sem pyarrow.

Regras (contrato Onda C §4, spec §6.9):

- **série líquida no dia:** `Σ trades ≥ 5` nos 5 pregões que terminam nele,
  `spread_pct ≤ 10`, `bid > 0` **e** `ask > 0` (sem book = `one_sided`, e
  `one_sided` **não** é líquida), `strike > 0` e `K/S` dentro da banda;
- **banda ≈ 20Δ–80Δ por moneyness:** `K/S ∈ [0,75; 1,25]`. É aproximação por
  spot — Δ real só existe no F1, e o relatório diz isso em uma linha. `strike
  == 0` (órfã de ajuste de grade do IBOV, AGENTS.md) nunca entra em banda;
- **dia líquido do papel:** `≥ 5` strikes distintos líquidos na banda **e**
  `≥ 2` vencimentos líquidos no mesmo dia (`liquid_days_20d` conta esses dias
  na janela de 20 sessões do recorte);
- **papel no universo:** volume financeiro médio 20d do **subjacente** (mercado
  `010`, fallback `020` quando o papel não tem lote padrão no dia — nunca somar
  os dois, §6.1) `≥ R$ 5 M` = `500_000_000` centavos;
- **`days_to_liquidate`:** `Σ posições em aberto ÷ quantidade média negociada
  20d`. O `oi` usado é `total_qty` (`Total de posições`, unidade do
  ativo-objeto: ações) — `OpnIntrst`/`Contratos em aberto` vem vazio em 100% das
  opções de ação (AGENTS.md). O denominador é **quantidade** (ações médias por
  dia), não o financeiro: dividir ações por R$ não tem unidade. Sem OI →
  `None` + flag no relatório, nunca zero (P-N).

Memória: o `prepare` passa o recorte das últimas sessões (o ano inteiro são 3,5 M
linhas); este módulo guarda as linhas do recorte e descarta o que está fora da
janela de 20 sessões **antes** de agregar.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from b3o.ingest.parse import SPOT_MARKETS, CotahistRow

DAYS_WINDOW = 20
"""Quantas sessões o `liquid_days_20d` e o volume médio olham."""

TRADES_WINDOW = 5
"""Janela da condição `trades ≥ 5`: os 5 pregões que terminam no dia avaliado."""

TRADES_MIN = 5
"""Mínimo de negócios na janela para a série ser considerada negociada."""

SPREAD_MAX_PCT = 10.0
"""Spread máximo do dia, em % do mid (`(ask − bid) / mid × 100`)."""

BAND_LOW = 0.75
"""Banda de moneyness proxy (≈ 20Δ) — Δ real chega no F1."""

BAND_HIGH = 1.25
"""Banda de moneyness proxy (≈ 80Δ)."""

MIN_LIQUID_STRIKES = 5
"""Strikes distintos líquidos para o dia contar como líquido."""

MIN_LIQUID_MATURITIES = 2
"""Vencimentos distintos líquidos no mesmo dia."""

VOLUME_MIN_CENTS = 500_000_000
"""R$ 5 M em centavos (R$ 1 = 100¢) — corte do universo (spec §6.9)."""

MARKET_SPOT = "010"
"""Mercado à vista que define o spot e o volume do subjacente (§6.1)."""

MARKET_FRACTIONAL = "020"
"""Fracionário: só entra quando o papel não tem linha `010` no dia."""


@dataclass(frozen=True, slots=True)
class LiquidityUnderlying:
    """Liquidez de um papel: dias líquidos, volume médio, dias para liquidar o OI."""

    underlying: str
    """Ticker do ativo-objeto (`PETR4`)."""

    liquid_days_20d: int
    """Dias, na janela de 20 sessões, com ≥ 5 strikes líquidos na banda e ≥ 2 vencimentos."""

    avg_volume_brl_20d: int
    """Volume financeiro médio diário do subjacente, em **centavos** (mercado 010)."""

    days_to_liquidate: float | None
    """`Σ total_qty / quantidade média 20d`; `None` quando não há OI para o papel."""

    in_universe: bool
    """`avg_volume_brl_20d ≥ R$ 5 M` (spec §6.9)."""


_Spot = tuple[str, int, int, int]
"""`(ticker, close, qty, volume)` — o ticker do papel vem da linha à vista."""

_Option = tuple[str, date, int, int, int, int]
"""`(ticker, maturity, strike, best_bid, best_ask, trades)` — o recorte mínimo do dia."""


def build_liquidity(
    rows: Iterable[CotahistRow],
    oi: Mapping[date, Mapping[str, int]],
) -> list[LiquidityUnderlying]:
    """Agrega as linhas do recorte em uma linha de liquidez por ativo-objeto.

    `rows` precisa conter as linhas de opção (`070`/`080`) **e** as do à vista
    (`010`/`020`) das mesmas sessões: o spot e o volume do papel saem do à vista
    e o vínculo opção→papel sai do `isin_underlying` (o ISIN é do ativo-objeto,
    nunca da série — AGENTS.md, armadilha 1).

    `oi` é `{refdate: {ticker_da_série: total_qty}}`. O papel usa a **última**
    data presente — OI é estoque, não série histórica. `total_qty` já está na
    unidade do ativo-objeto (ações), a mesma do denominador.
    """
    spots: dict[tuple[date, str], dict[str, _Spot]] = defaultdict(dict)
    options: list[tuple[date, str, _Option]] = []
    dates: dict[date, date] = {}

    for row in rows:
        known = dates.get(row.refdate)
        refdate = known if known is not None else _remember(dates, row.refdate)
        isin = row.isin_underlying.strip()
        if row.tpmerc in SPOT_MARKETS:
            spots[(refdate, isin)][row.tpmerc] = (
                row.ticker,
                row.close,
                row.qty,
                row.volume,
            )
            continue
        if row.side is None or row.maturity is None:
            continue
        options.append(
            (
                refdate,
                isin,
                (
                    row.ticker,
                    row.maturity,
                    row.strike,
                    row.best_bid,
                    row.best_ask,
                    row.trades,
                ),
            )
        )

    # ISIN → snapshot do papel: a linha 010 manda; 020 só quando não há 010 no dia
    # (nunca somar os dois mercados — §6.1). O snapshot mais recente vence.
    paper_of_isin: dict[str, tuple[date, str]] = {}
    for (refdate, isin), by_market in spots.items():
        chosen = _preferred_spot(by_market)
        if chosen is None:
            continue
        current = paper_of_isin.get(isin)
        if current is None or refdate >= current[0]:
            paper_of_isin[isin] = (refdate, chosen[0])

    if not paper_of_isin:
        return []

    spot_series: dict[str, dict[date, _Spot]] = defaultdict(dict)
    for (refdate, isin), by_market in spots.items():
        mapped = paper_of_isin.get(isin)
        chosen = _preferred_spot(by_market)
        if mapped is None or chosen is None:
            continue
        spot_series[mapped[1]][refdate] = chosen

    sessions = sorted(dates)
    if not sessions:
        return []
    window = sessions[-DAYS_WINDOW:]
    session_index = {day: index for index, day in enumerate(sessions)}

    paper_of_ticker: dict[str, str] = {}
    trades_by_series: dict[tuple[str, date], dict[date, int]] = defaultdict(dict)
    day_options: dict[tuple[str, date], list[_Option]] = defaultdict(list)

    for refdate, isin, option in options:
        mapped = paper_of_isin.get(isin)
        if mapped is None:
            continue
        paper = mapped[1]
        ticker, maturity = option[0], option[1]
        paper_of_ticker[ticker] = paper
        day_options[(paper, refdate)].append(option)
        # trades por série sai das próprias linhas: o recorte tem uma por (ticker, dia)
        trades_by_series[(ticker, maturity)][refdate] = option[5]

    latest_oi_date = max(oi) if oi else None
    oi_totals = oi.get(latest_oi_date, {}) if latest_oi_date is not None else {}
    totals_by_paper: dict[str, int] = defaultdict(int)
    for ticker, quantity in oi_totals.items():
        if quantity is None:
            continue  # coluna vazia no arquivo (`-`): não é zero, é ausência
        paper = paper_of_ticker.get(ticker)
        if paper is not None:
            totals_by_paper[paper] += int(quantity)

    results: list[LiquidityUnderlying] = []
    for paper in sorted(set(paper_of_ticker.values()) | set(spot_series)):
        liquid_days = 0
        for day in window:
            liquid_strikes: set[int] = set()
            liquid_maturities: set[date] = set()
            for ticker, maturity, strike, bid, ask, _trades in day_options.get((paper, day), ()):
                if not _liquid_in_band(
                    day,
                    strike,
                    bid,
                    ask,
                    spot_series.get(paper, {}),
                    trades_by_series.get((ticker, maturity), {}),
                    sessions,
                    session_index,
                ):
                    continue
                liquid_strikes.add(strike)
                liquid_maturities.add(maturity)
            if (
                len(liquid_strikes) >= MIN_LIQUID_STRIKES
                and len(liquid_maturities) >= MIN_LIQUID_MATURITIES
            ):
                liquid_days += 1

        volumes = [spot_series.get(paper, {}).get(day, ("", 0, 0, 0))[3] for day in window]
        quantities = [spot_series.get(paper, {}).get(day, ("", 0, 0, 0))[2] for day in window]
        avg_volume = sum(volumes) // len(window)
        avg_quantity = sum(quantities) // len(window)

        total_oi = totals_by_paper.get(paper)
        days_to_liquidate = (
            total_oi / avg_quantity if total_oi is not None and avg_quantity > 0 else None
        )
        results.append(
            LiquidityUnderlying(
                underlying=paper,
                liquid_days_20d=liquid_days,
                avg_volume_brl_20d=avg_volume,
                days_to_liquidate=days_to_liquidate,
                in_universe=avg_volume >= VOLUME_MIN_CENTS,
            )
        )

    results.sort(
        key=lambda item: (not item.in_universe, -item.avg_volume_brl_20d, item.underlying)
    )
    return results


def render_report(
    items: Iterable[LiquidityUnderlying],
    *,
    last_session: date,
    first_session: date,
    sessions: int,
    oi_date: date | None,
) -> str:
    """Relatório Markdown (P-T): universo no topo, tabela por papel e as ressalvas.

    Função pura: recebe os números já calculados e devolve texto. Nenhuma data sai
    do relógio — `last_session` é a última sessão do lake.
    """
    rows = list(items)
    universe = [row for row in rows if row.in_universe]
    tradable = [row for row in rows if row.liquid_days_20d > 0]
    lines: list[str] = [
        f"# Liquidez por papel — sessão {last_session.isoformat()}",
        "",
        f"**Universo: {len(universe)} papel(is)** com volume financeiro médio 20d "
        "≥ R$ 5 M (mercado 010).",
        "",
        f"Com grade de opções líquida em ao menos 1 dia: **{len(tradable)} papel(is)** "
        "(o corte de volume não exige opção — ETF, unit e BDR entram no primeiro número).",
        "",
        f"Recorte lido: {sessions} sessão(ões), {first_session.isoformat()} a "
        f"{last_session.isoformat()} (a métrica de dias e volume usa as 20 últimas). "
        f"Papéis com linha no recorte: {len(rows)}.",
        "",
        "**Banda aproximada por moneyness** `K/S ∈ [0,75; 1,25]` — não é Δ real; "
        "a banda por delta chega no F1.",
        "",
        "Série líquida no dia: ≥ 5 negócios na janela de 5 pregões, `spread_pct ≤ 10`, "
        "`bid > 0` e `ask > 0` (sem book — `one_sided` — não conta) e `strike > 0`.",
        "Dia líquido do papel: ≥ 5 strikes distintos na banda **e** ≥ 2 vencimentos. "
        "O livro de fechamento da B3 é largo em quase toda a grade: isso é medido, não "
        "filtrado.",
        "",
        "`days_to_liquidate` é a versão **por papel**: Σ posições em aberto "
        "(`Total de posições`, em ações do ativo-objeto) ÷ quantidade média negociada "
        "20d. OI de "
        f"{oi_date.isoformat() if oi_date else 'nenhuma data'}; `—` = sem OI para o "
        "papel (P-N: null com flag, nunca zero).",
        "",
        "| papel | liquid_days_20d | avg_volume_brl_20d (R$) | days_to_liquidate | "
        "in_universe |",
        "|---|---:|---:|---:|---|",
    ]
    for row in rows:
        volume = _number(row.avg_volume_brl_20d / 100)
        liquidation = "—" if row.days_to_liquidate is None else _number(row.days_to_liquidate)
        lines.append(
            f"| {row.underlying} | {row.liquid_days_20d} | {volume} | {liquidation} | "
            f"{'sim' if row.in_universe else 'não'} |"
        )
    lines.append("")
    return "\n".join(lines)


def _number(value: float) -> str:
    """Número com milhar por espaço e vírgula decimal (`40 000 000,00`)."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _preferred_spot(by_market: Mapping[str, _Spot]) -> _Spot | None:
    """Snapshot do papel no dia: `010` se houver, senão `020`; nunca os dois somados."""
    return by_market.get(MARKET_SPOT) or by_market.get(MARKET_FRACTIONAL)


def _remember(cache: dict[date, date], value: date) -> date:
    """Interna a data (o recorte tem centenas de milhares de linhas)."""
    cache[value] = value
    return value


def _liquid_in_band(
    day: date,
    strike: int,
    bid: int,
    ask: int,
    spots: Mapping[date, _Spot],
    series_trades: Mapping[date, int],
    sessions: list[date],
    session_index: Mapping[date, int],
) -> bool:
    """A série está líquida **naquele dia** e dentro da banda de moneyness?"""
    if strike <= 0:
        return False  # órfã de ajuste de grade (AGENTS.md): fora de toda conta
    if bid <= 0 or ask <= 0:
        return False  # one_sided não é líquida (spec §6.9)
    mid = (bid + ask) / 2
    if mid <= 0 or (ask - bid) / mid * 100 > SPREAD_MAX_PCT:
        return False

    spot = spots.get(day)
    if spot is None or spot[1] <= 0:
        return False  # sem spot do dia não há moneyness — não se inventa
    ratio = strike / spot[1]
    if ratio < BAND_LOW or ratio > BAND_HIGH:
        return False

    index = session_index.get(day)
    if index is None:
        return False
    start = max(0, index - TRADES_WINDOW + 1)
    total = sum(series_trades.get(sessions[position], 0) for position in range(start, index + 1))
    return total >= TRADES_MIN
