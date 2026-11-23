"""Ponte pura do B2 para as séries por papel que a RV e o F1b consomem (F1.6b).

O B2 (``b3o.derive.strike_events``) já classificou cada ajuste de strike em
``dividend`` / ``scale`` / ``indeterminate`` pela assinatura **entre strikes do
mesmo papel e data** (spec §6.6). Aqui nada é reclassificado:

- evento de escala vira **fator por data** (``scale_factors``);
- evento de provento vira **data ex por papel** (``dividend_dates``);
- **indeterminado fica de fora** dos dois. ``leg_ambiguity`` no B2 quer dizer "não
  sei se é provento, escala ou ajuste de grade"; inventar fator 1,0 (que não
  ajusta nada) ou data ex (que apaga uma observação boa) contaminaria RV, forward
  e IV em silêncio. Quem quiser tratar esses casos decide no pipeline, vendo-os.

Unidade do fator: ``factor = strike_novo / strike_antigo``, que é também o
multiplicador de **preço** do passado (spec §6.6): desdobramento 1:3 → 1/3 (o
preço antigo × 1/3 volta à escala nova); grupamento ×2 → 2. O fator gravado pelo
B2 é a **média** das razões medidas, não o valor canônico (SBSP 29/04/2026:
0,20000430744063075, porque o strike é centavo inteiro) — compare com tolerância.

``event_factor_series`` acumula esses eventos num multiplicador por barra, que é
exatamente o que ``OhlcBar.adj_factor`` (``realized``) espera: a barra **anterior**
ao evento recebe o fator; a barra do dia do evento já está na escala nova (o
COTAHIST não reajusta preço, e no dia ex o papel já abre no preço novo — medido
no arquivo de 2026: SBSP3 em 29/04/2026 tem overnight de ln(0,2) exato).

Este módulo é puro: sem I/O, sem numpy, sem duckdb. Aceita o ``StrikeEvent`` do B2
ou qualquer objeto/mapa com ``underlying``, ``date``, ``kind`` e ``factor`` (a
linha do Parquet de ``core.strike_events`` também serve, sem reconstruir dataclass).
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Protocol, cast

DIVIDEND = "dividend"
"""Espelha ``b3o.derive.strike_events.DIVIDEND`` (teste trava a igualdade)."""

SCALE = "scale"
"""Espelha ``b3o.derive.strike_events.SCALE`` (teste trava a igualdade)."""


class StrikeEventLike(Protocol):
    """O que esta ponte precisa de um evento do B2 — nada além disto (só leitura)."""

    @property
    def underlying(self) -> str: ...

    @property
    def date(self) -> date: ...

    @property
    def kind(self) -> str: ...

    @property
    def factor(self) -> float | None: ...


def scale_factors(
    events: Iterable[StrikeEventLike | Mapping[str, object]],
) -> dict[str, dict[date, float]]:
    """Fatores de escala por papel e data: ``{underlying: {date: factor}}``.

    Só ``kind == "scale"`` entra; provento não tem fator de preço e indeterminado
    não tem classe. Evento de escala sem ``factor`` positivo e finito não entra —
    não há o que ajustar, e devolver 1,0 fingiria um ajuste que ninguém provou.

    Dois fatores **diferentes** para o mesmo papel e dia são contradição do B2
    (ele agrupa por ``(papel, data)``); a função falha em vez de escolher um.
    """
    factors: dict[str, dict[date, float]] = {}
    for event in events:
        if _field(event, "kind") != SCALE:
            continue
        raw = _field(event, "factor")
        if raw is None:
            continue
        factor = float(raw)  # type: ignore[arg-type]
        if not math.isfinite(factor) or factor <= 0.0:
            continue
        underlying = cast(str, _field(event, "underlying"))
        day = cast(date, _field(event, "date"))
        per_day = factors.setdefault(underlying, {})
        known = per_day.get(day)
        if known is not None and not math.isclose(known, factor, rel_tol=1e-9):
            raise ValueError(
                f"dois fatores de escala para {underlying} em {day}: {known!r} e {factor!r} "
                "— o B2 agrupa por (papel, data); evento duplicado é bug de entrada"
            )
        per_day[day] = factor
    return factors


def dividend_dates(
    events: Iterable[StrikeEventLike | Mapping[str, object]],
) -> dict[str, frozenset[date]]:
    """Datas ex de provento por papel: ``{underlying: frozenset[date]}``.

    Só ``kind == "dividend"`` entra. O tamanho do provento (``delta_strike``) não
    viaja aqui: a RV só precisa saber **qual** observação sai da janela; o valor
    em R$/ação é de ``core.dividends_implied`` (F1b), outro consumidor do mesmo B2.
    """
    dates: dict[str, set[date]] = {}
    for event in events:
        if _field(event, "kind") != DIVIDEND:
            continue
        underlying = cast(str, _field(event, "underlying"))
        dates.setdefault(underlying, set()).add(cast(date, _field(event, "date")))
    return {underlying: frozenset(days) for underlying, days in dates.items()}


def event_factor_series(
    dates: Iterable[date],
    factors: Mapping[date, float],
) -> dict[date, float]:
    """``adj_factor`` por barra: produto dos eventos **posteriores** àquela data.

    ``factors`` é o mapa de um papel (``scale_factors(events)["SBSP3"]``). Para a
    barra de data ``d``, ``fator(d) = Π{f(e) : e > d}`` — a barra do evento e as
    seguintes já estão na escala nova, então recebem 1,0; as anteriores recebem o
    produto. Dois eventos no ano compõem (0,5 em abril × 2,0 em junho dá 1,0 até
    abril).

    ``dates`` é o calendário de barras do papel (as ``refdate`` das ``OhlcBar``)
    e o resultado tem uma entrada por data — é o insumo direto de
    ``OhlcBar.adj_factor``.
    """
    event_days = sorted(factors)
    if not event_days:
        return {day: 1.0 for day in dates}

    suffix = [1.0] * (len(event_days) + 1)
    for index in range(len(event_days) - 1, -1, -1):
        suffix[index] = suffix[index + 1] * factors[event_days[index]]

    return {day: suffix[bisect_right(event_days, day)] for day in dates}


def _field(event: StrikeEventLike | Mapping[str, object], name: str) -> object:
    """``event.name`` no dataclass do B2, ``event[name]`` numa linha do Parquet."""
    if isinstance(event, Mapping):
        return event[name]
    return getattr(event, name)
