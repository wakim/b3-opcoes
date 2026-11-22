"""Classificação do ajuste de strike pela assinatura entre strikes (spec §6.6) — puro.

Por que a regra é esta (AGENTS.md, armadilha 2 do dado): a B3 mexe no strike por
provento **e** por desdobramento/grupamento/bonificação, e **não existe limiar de
razão** que separe os dois — um provento de R$ 2,86 sobre strike baixo dá razão
0,77, uma bonificação de 10% dá 0,909, e há dividendo com razão 0,51 numa série.
O que separa é a assinatura **entre strikes do mesmo papel, na mesma data**:

    Δstrike igual em todos (±1 ¢)                    -> ``dividend``
    razão nova/antiga igual em todos (±0,1%), Δ varia -> ``scale``
    as duas cabem (provento pequeno)                 -> ``dividend``
    nenhuma cabe, ou um strike só                    -> ``indeterminate``

Proibido (spec §6.6): "Δ < 0 = dividendo". O SBSP de 29/04/2026 cai ×0,2 em 106
séries — ``Δ < 0`` transformaria um desdobramento em "provento de 80%".

Só mudança **contígua** data e classifica (AGENTS.md, armadilha 3): o COTAHIST só
lista a série que negociou, e 70% das mudanças de strike aparecem depois de uma
lacuna — a data observada **não é a data ex**. Mudança não contígua não gera
evento próprio; ela pertence a um evento que outra série datou.

Este módulo é puro: recebe iterável de ``CotahistRow`` (opções + as linhas `010`
do ativo-objeto) e devolve ``StrikeEvent``. Zero I/O, zero duckdb/pyarrow.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from b3o.ingest.parse import OPTION_MARKETS, SPOT_MARKETS, CotahistRow

DIVIDEND = "dividend"
SCALE = "scale"
INDETERMINATE = "indeterminate"

DELTA_TOLERANCE_CENTS = 1
"""Tolerância do Δstrike entre séries: ±1 centavo (arredondamento da B3)."""

RATIO_TOLERANCE = 0.001
"""Tolerância da razão entre séries: ±0,1% (spec §6.6)."""


@dataclass(frozen=True, slots=True)
class StrikeEvent:
    """Um ajuste de strike datado, com a assinatura que o classificou."""

    underlying: str
    """Ticker do ativo-objeto: ``isin_underlying`` → linha `010`/`020` do mesmo dia."""

    date: date
    """Primeira data com mudança **contígua** — não é a data ex (spec §6.6)."""

    kind: str
    """``dividend`` | ``scale`` | ``indeterminate``."""

    delta_strike: int | None
    """Queda do strike em centavos (provento sempre reduz o strike), ``None`` se não é dividendo."""

    factor: float | None
    """Razão nova/antiga (escala): < 1 desdobramento/bonificação, > 1 grupamento."""

    n_series: int
    """Quantas séries entraram na assinatura (as que mudaram de forma contígua)."""

    ambiguous: bool
    """``True`` quando a classificação é indeterminada ou o papel não pôde ser resolvido."""


def classify_signature(
    changes: Sequence[tuple[int, int]],
) -> tuple[str, int | None, float | None]:
    """Classifica a assinatura de um conjunto de mudanças ``(strike_antigo, strike_novo)``.

    Função pura sobre números: recebe os pares do **mesmo papel e da mesma data** e
    devolve ``(kind, delta_strike, factor)``. Um par só não prova assinatura nenhuma
    (spec §6.6: "só 1 strike observado → indeterminado"), e strike `0` (linha órfã de
    ajuste de grade, AGENTS.md) nunca entra na conta.
    """
    pairs = [(prev, cur) for prev, cur in changes if prev > 0 and cur > 0]
    if len(pairs) < 2:
        return (INDETERMINATE, None, None)

    deltas = [cur - prev for prev, cur in pairs]
    if max(deltas) - min(deltas) <= 2 * DELTA_TOLERANCE_CENTS and max(deltas) < 0:
        # Δ igual e para baixo: provento. A mediana é um Δ observado (sem inventar
        # centavo) e o campo é o tamanho da queda, positivo.
        ordered = sorted(deltas)
        return (DIVIDEND, -ordered[len(ordered) // 2], None)

    ratios = [cur / prev for prev, cur in pairs]
    if min(ratios) > 0 and max(ratios) / min(ratios) - 1.0 <= RATIO_TOLERANCE:
        return (SCALE, None, sum(ratios) / len(ratios))

    return (INDETERMINATE, None, None)


def _underlying_of(isin: str, spot_tickers: dict[str, str]) -> tuple[str, bool]:
    """Ticker do ativo-objeto a partir do ISIN; ``(isin, True)`` quando não resolve."""
    ticker = spot_tickers.get(isin)
    if ticker:
        return (ticker, False)
    return (isin, True)


def _previous_sessions(rows_dates: Iterable[date]) -> dict[date, date]:
    """Sessão imediatamente anterior a cada data presente no recorte."""
    ordered = sorted(set(rows_dates))
    return {cur: prev for prev, cur in zip(ordered, ordered[1:], strict=False)}


def classify_strike_events(rows: Iterable[CotahistRow]) -> list[StrikeEvent]:
    """Datam e classificam as mudanças contíguas de strike, um evento por papel e data.

    ``rows`` precisa conter as linhas de opção **e** as linhas de mercado `010`
    (`020` como reserva): o ticker do ativo-objeto sai do ``isin_underlying`` casado
    com o ISIN da linha à vista do mesmo dia, e o calendário de sessões sai do
    conjunto de todas as datas recebidas.

    Ordem de serviço por série ``(isin, ticker, maturity)``: só a mudança que ocorre
    na sessão seguinte à última observação da série conta (contiguidade). Os pares
    contíguos são agrupados por ``(papel, data)`` — é essa a assinatura do §6.6.
    """
    sessions: set[date] = set()
    spot_tickers: dict[str, str] = {}
    spot_market: dict[str, str] = {}
    series: dict[tuple[str, str, date], dict[date, int]] = defaultdict(dict)
    dates: dict[date, date] = {}

    for row in rows:
        sessions.add(row.refdate)
        isin = row.isin_underlying.strip()
        if row.tpmerc in SPOT_MARKETS:
            # 010 manda; 020 só entra quando o papel não tem lote padrão no dia (§6.1).
            if isin not in spot_market or (spot_market[isin] == "020" and row.tpmerc == "010"):
                spot_market[isin] = row.tpmerc
                spot_tickers[isin] = row.ticker.strip()
            continue
        if row.tpmerc not in OPTION_MARKETS or row.maturity is None:
            continue
        refdate = row.refdate
        known = dates.get(refdate)
        if known is not None:
            refdate = known
        else:
            dates[refdate] = refdate
        series[(isin, row.ticker, row.maturity)].setdefault(refdate, row.strike)

    previous = _previous_sessions(sessions)

    changes: dict[tuple[str, date], list[tuple[int, int]]] = defaultdict(list)
    unmapped: set[str] = set()
    for (isin, _ticker, _maturity), by_date in series.items():
        underlying, unknown = _underlying_of(isin, spot_tickers)
        if unknown:
            unmapped.add(underlying)
        days = sorted(by_date.items())
        for (day_prev, strike_prev), (day_cur, strike_cur) in zip(
            days, days[1:], strict=False
        ):
            if strike_prev == strike_cur:
                continue
            if strike_prev == 0 or strike_cur == 0:
                # strike 0 em linha de opção é órfã de ajuste de grade (IBOV): nunca
                # entra em classificação, nem como valor de fronteira.
                continue
            if previous.get(day_cur) != day_prev:
                continue  # lacuna: a data observada não é a ex (AGENTS, armadilha 3)
            changes[(underlying, day_cur)].append((strike_prev, strike_cur))

    events: list[StrikeEvent] = []
    for (underlying, day) in sorted(changes, key=lambda key: (key[1], key[0])):
        kind, delta, factor = classify_signature(changes[(underlying, day)])
        events.append(
            StrikeEvent(
                underlying=underlying,
                date=day,
                kind=kind,
                delta_strike=delta,
                factor=factor,
                n_series=len(changes[(underlying, day)]),
                ambiguous=kind == INDETERMINATE or underlying in unmapped,
            )
        )
    return events
