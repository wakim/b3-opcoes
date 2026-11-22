"""Calendário de sessões da B3 — PURO (ADR-015).

O lake só conhece o passado: o COTAHIST anual termina em D-1 e há vencimentos
anos à frente. Sem extensão, `TradingCalendar` devolveria `T = 0` para todo
vencimento futuro e a IV da grade inteira sairia `iv_expired` (§4.9).

A regra, **medida** no arquivo de 2026: os **221** pregões entre 02/01 e 19/11
são exatamente os dias úteis do intervalo menos 11 feriados — zero sobra, zero
falta. Logo:

- feriados fixos: 01/01, 21/04, 01/05, 07/09, 12/10, 02/11, 15/11, 20/11 e 25/12;
- dias sem negócio: 24/12 e 31/12;
- móveis, derivados da Páscoa: Carnaval (−48 e −47), Sexta-feira Santa (−2) e
  Corpus Christi (+60). A Páscoa sai do algoritmo de Meeus (Gregoriano anônimo),
  sem tabela por ano e sem dependência externa;
- `known` (as sessões que o lake tem) **vence a regra**: uma data em `known` é
  sessão mesmo que a regra diga feriado, e data em `known` que cai em fim de
  semana também entra. O dado vence a suposição.

A consequência aceita é que um feriado municipal/atípico futuro desloca `T` em
um dia; quem consome marca `t_estimated` nas maturities depois da última sessão
do lake (ADR-015): o número não se passa por medido.

Sem I/O, sem relógio. Funções puras: ano entra, datas saem.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Collection, Iterable
from datetime import date, timedelta

FIXED_HOLIDAYS: tuple[tuple[int, int], ...] = (
    (1, 1),
    (4, 21),
    (5, 1),
    (9, 7),
    (10, 12),
    (11, 2),
    (11, 15),
    (11, 20),
    (12, 25),
)
"""Feriados de data fixa (mês, dia) — inclui 20/11 (Consciência Negra, desde 2024)."""

NO_TRADING_DAYS: tuple[tuple[int, int], ...] = ((12, 24), (12, 31))
"""Dias sem negócio que não são feriado (vésperas de Natal e Ano-Novo)."""

EASTER_OFFSETS: tuple[int, ...] = (-48, -47, -2, 60)
"""Dias móveis a partir da Páscoa: Carnaval (seg/ter), Sexta Santa e Corpus Christi."""


def easter_sunday(year: int) -> date:
    """Domingo de Páscoa do ano — algoritmo de Meeus (Gregoriano anônimo).

    Só aritmética inteira; válido para o calendário gregoriano (1583 em diante),
    que cobre todo o universo de vencimentos do lake.
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def b3_holidays(year: int) -> frozenset[date]:
    """Feriados e dias sem negócio do ano (fixos + móveis derivados da Páscoa)."""
    days = {date(year, month, day) for month, day in FIXED_HOLIDAYS}
    days.update(date(year, month, day) for month, day in NO_TRADING_DAYS)
    easter = easter_sunday(year)
    days.update(easter + timedelta(days=offset) for offset in EASTER_OFFSETS)
    return frozenset(days)


def trading_sessions(
    start: date,
    end: date,
    known: Collection[date] = (),
) -> tuple[date, ...]:
    """Sessões entre `start` e `end` (inclusive), ordenadas e sem repetição.

    `known` são as sessões do lake: entram **sempre** (o dado vence a regra, e
    uma sessão em fim de semana/suposto feriado continua sendo sessão). Fora
    delas, vale "dia útil menos feriado".
    """
    if end < start:
        return ()
    sessions: set[date] = {day for day in known if start <= day <= end}
    holidays: set[date] = set()
    for year in range(start.year, end.year + 1):
        holidays |= b3_holidays(year)
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in holidays:
            sessions.add(day)
        day += timedelta(days=1)
    return tuple(sorted(sessions))


def business_days(sessions: Iterable[date], start: date, end: date) -> int:
    """Sessões **depois** de `start` e **até** `end` (inclusive): `idx(end) − idx(start)`.

    Mesma convenção de `TradingCalendar.business_days` (contrato Onda D §1:
    vencimento no fechamento; vence hoje → 0). `sessions` precisa estar ordenada
    (é a saída de `trading_sessions`/`QueryService.lake_calendar`).
    """
    if end < start:
        raise ValueError(f"business_days com end < start: {start} -> {end}")
    ordered = tuple(sessions)
    return bisect_right(ordered, end) - bisect_right(ordered, start)


__all__ = [
    "EASTER_OFFSETS",
    "FIXED_HOLIDAYS",
    "NO_TRADING_DAYS",
    "b3_holidays",
    "business_days",
    "easter_sunday",
    "trading_sessions",
]
