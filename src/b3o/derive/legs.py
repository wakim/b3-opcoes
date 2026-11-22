"""Legs de série: o trecho entre ajustes de strike (spec §5.2) — puro.

Uma linha por ``(ticker, refdate)``. O *leg* corta quando o strike, o
``strike_adj_ind`` ou o ``distribution_id`` muda; o ``leg_index`` incrementa a cada
corte. Nunca se atravessa a data de um ajuste com métrica: preço, IV e paridade
morrem no fim do leg (AGENTS.md, armadilha 2).

Duas armadilhas que este arquivo precisa respeitar:

- **strike por data, nunca da série.** 51.830 tickers de 2026 (23,5%) têm mais de um
  strike; o ajuste é do dia.
- **strike `0` existe em linha de opção** (órfã de ajuste de grade de IBOV). Aqui ele
  é tratado como valor legítimo — corta o leg se mudar —, mas marcado com a flag
  ``strike_zero``; a classificação de evento (``strike_events``) o recusa.

O estilo é consolidado por **maioria** dentro do leg: o parser resolve o estilo por
linha (o 1º token do nome resumido), e uma série pode ter token conflitante entre
datas — nesse caso o leg fica com a maioria e a flag ``style_conflict``.

Este módulo é puro: recebe iterável de ``CotahistRow``, devolve ``Iterator[LegRow]``.
Zero I/O, zero duckdb/pyarrow.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date

from b3o.ingest.parse import OPTION_MARKETS, SIDE_BY_MARKET, CotahistRow

LEG_AMBIGUITY = "leg_ambiguity"
STYLE_CONFLICT = "style_conflict"
STYLE_UNKNOWN = "style_unknown"
SIDE_CONFLICT = "side_conflict"
STRIKE_ZERO = "strike_zero"


@dataclass(frozen=True, slots=True)
class LegRow:
    """Uma linha de ``core.series_leg``: o leg vigente do ticker naquele pregão."""

    leg_id: str
    """``"{ticker}|{maturity}|{leg_index}"`` — a chave do leg."""

    ticker: str
    maturity: date
    side: str
    """``"C"`` (mercado 070) ou ``"P"`` (080)."""

    style: str | None
    """``"A"``/``"E"`` por maioria do leg; ``None`` + flag quando conflita ou falta."""

    refdate: date
    leg_index: int
    """Incrementa a cada fronteira (strike, ``strike_adj_ind`` ou ``distribution_id``)."""

    strike: int
    """O strike **do dia**, em centavos (nunca o da série)."""

    distribution_id: str
    flags: tuple[str, ...]


_Day = tuple[date, int, str, str, "str | None", str]
"""``(refdate, strike, strike_adj_ind, distribution_id, style, side)`` — compacto de propósito."""


def _interned(cache: dict, value):
    """Reaproveita o objeto já visto (3 M linhas × 40 B de `date` não é troco)."""
    known = cache.get(value)
    if known is None:
        cache[value] = value
        return value
    return known


def _boundary(previous: _Day, current: _Day) -> tuple[bool, bool]:
    """``(corta_leg, ambiguidade)``.

    Corta em qualquer um dos três sinais (spec §5.2: "o leg é cortado no primeiro
    sinal"). A ambiguidade é o caso em que o corte **não** vem do strike: a série
    continua com o mesmo strike sob outra distribuição — o sinal que a spec pede para
    validar contra ``strike_adj_ind``. Em 2026 ``strike_adj_ind`` é sempre ``'0'``
    (medido: 3.570.794 linhas), então o teste real dessa fronteira é o
    ``distribution_id``; o campo fica na fronteira de graça, à prova de arquivo futuro.
    """
    strike_changed = previous[1] != current[1]
    adj_changed = previous[2] != current[2]
    distribution_changed = previous[3] != current[3]
    cut = strike_changed or adj_changed or distribution_changed
    ambiguous = cut and not strike_changed
    return (cut, ambiguous)


def _consolidate_style(days: list[_Day]) -> tuple[str | None, list[str]]:
    """Estilo do leg por maioria; conflito vira flag (empate → ``None``)."""
    counts = Counter(style for day in days if (style := day[4]) is not None)
    if not counts:
        return (None, [STYLE_UNKNOWN])
    ordered = counts.most_common()
    flags = [STYLE_CONFLICT] if len(ordered) > 1 else []
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return (None, flags)
    return (ordered[0][0], flags)


def _consolidate_side(days: list[_Day]) -> tuple[str, list[str]]:
    """Lado do leg por maioria (é constante por ticker; divergência vira flag)."""
    counts = Counter(day[5] for day in days)
    ordered = counts.most_common()
    flags = [SIDE_CONFLICT] if len(ordered) > 1 else []
    return (ordered[0][0], flags)


def _leg_rows(
    ticker: str,
    maturity: date,
    leg_index: int,
    days: list[_Day],
    ambiguous: bool,
) -> list[LegRow]:
    """Materializa as linhas de um leg fechado."""
    style, style_flags = _consolidate_style(days)
    side, side_flags = _consolidate_side(days)
    flags: list[str] = []
    if ambiguous:
        flags.append(LEG_AMBIGUITY)
    flags.extend(style_flags)
    flags.extend(side_flags)

    rows: list[LegRow] = []
    for day in days:
        row_flags = flags + [STRIKE_ZERO] if day[1] == 0 else list(flags)
        rows.append(
            LegRow(
                leg_id=f"{ticker}|{maturity.isoformat()}|{leg_index}",
                ticker=ticker,
                maturity=maturity,
                side=side,
                style=style,
                refdate=day[0],
                leg_index=leg_index,
                strike=day[1],
                distribution_id=day[3],
                flags=tuple(row_flags),
            )
        )
    return rows


def build_legs(rows: Iterable[CotahistRow]) -> Iterator[LegRow]:
    """Gera uma ``LegRow`` por ``(ticker, refdate)``, com o leg vigente.

    Só linhas de opção (`070`/`080`) entram. A série é ``(ticker, maturity)`` e é
    ordenada por ``refdate`` internamente — o COTAHIST anual **não** está ordenado por
    data (AGENTS.md, armadilha 4), então nada aqui depende da ordem de entrada.

    Linha de opção sem vencimento (`99991231`) não é série e é descartada; o parser a
    aceita estruturalmente, mas ela não existe no arquivo real.
    """
    series: dict[tuple[str, date], list[_Day]] = defaultdict(list)
    dates: dict[date, date] = {}
    labels: dict[str, str] = {}
    for row in rows:
        if row.tpmerc not in OPTION_MARKETS or row.maturity is None:
            continue
        series[(row.ticker, row.maturity)].append(
            (
                _interned(dates, row.refdate),
                row.strike,
                _interned(labels, row.strike_adj_ind),
                _interned(labels, row.distribution_id),
                row.style,
                row.side or SIDE_BY_MARKET[row.tpmerc],
            )
        )

    for (ticker, maturity), unsorted_days in series.items():
        days = sorted(unsorted_days, key=lambda day: day[0])
        leg_index = 0
        current: list[_Day] = []
        ambiguous = False
        previous: _Day | None = None
        for day in days:
            if previous is not None:
                cut, ambiguous_at_cut = _boundary(previous, day)
                if cut:
                    yield from _leg_rows(ticker, maturity, leg_index, current, ambiguous)
                    leg_index += 1
                    current = []
                    ambiguous = ambiguous_at_cut
            current.append(day)
            previous = day
        if current:
            yield from _leg_rows(ticker, maturity, leg_index, current, ambiguous)
