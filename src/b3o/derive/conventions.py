"""Convenções de tempo, desconto e curva DI (F1.1, spec §6.0) — PURO.

Unidades (herdadas do contrato Onda D §0):
- `rate_annual_pct` em **% a.a. efetiva, base 252** — quem usa em `DF = exp(−r·T)`
  divide por 100 (é o que `discount_factor` faz);
- `t` em anos = dias úteis ÷ 252 (`year_fraction`);
- `du` = dias úteis desde a data de referência (a grade da curva DI);
- `log_df` = ln(DF), negativo para taxa positiva.

Convenção de tempo (contrato Onda D §1, "expira no fechamento")
---------------------------------------------------------------
`business_days(refdate, maturity)` conta as sessões **depois** de `refdate` e **até**
`maturity` (inclusive): é `índice(maturity) − índice(refdate)` no calendário. Lido
como "quantos dias úteis faltam", isso dá a convenção pedida — a sessão do vencimento
conta como o último passo, a data de referência não entra na contagem, e uma opção que
vence **hoje** tem `T = 0` (só intrínseco, IV `None`). Uma opção que vence na sessão
seguinte tem `T = 1/252`.

Por que o calendário vem do dado (P-C)
--------------------------------------
`TradingCalendar` é derivado das **datas de sessão presentes no lake**, nunca de uma
tabela de feriados: se a B3 não abriu, não há linha, e a contagem sai certa de graça.
A consequência é que ele só sabe o passado. Para avaliar uma opção que vence no
**futuro**, o calendário precisa conter as sessões futuras do intervalo (o C1/F1
monta a lista; este módulo não inventa calendário). Um calendário só com o passado
devolve `T = 0` para todo vencimento futuro — e a IV correspondente sai `iv_expired`,
não "tempo inventado". Falha explícita em vez de número silencioso.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from datetime import date

SESSIONS_PER_YEAR = 252
"""Base de anualização (spec §6.0: `T` em dias úteis ÷ 252; DI é efetiva base 252)."""

_INV_SQRT_2 = 1.0 / math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    """Distribuição normal padrão acumulada: ``½(1 + erf(x/√2))``.

    Sem `scipy` (ADR-004): `math.erf` é da biblioteca padrão e tem precisão de double.
    """
    return 0.5 * (1.0 + math.erf(x * _INV_SQRT_2))


def norm_pdf(x: float) -> float:
    """Densidade normal padrão: ``exp(−x²/2)/√(2π)``."""
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


class TradingCalendar:
    """Sessões de negociação em ordem — a fonte da verdade do tempo (P-C).

    Construído com as datas de sessão do lake (as que têm linha no COTAHIST). Não há
    tabela de feriados: dia sem linha é dia sem mercado. Datas repetidas são
    deduplicadas e a ordem é irrelevante na entrada.

    Para vencimentos futuros o chamador precisa acrescentar as sessões futuras do
    intervalo (ver o docstring do módulo) — o lake, sozinho, termina em D-1.
    """

    __slots__ = ("_sessions", "_index")

    def __init__(self, sessions: Iterable[date]) -> None:
        ordered = tuple(sorted(set(sessions)))
        self._sessions = ordered
        self._index = {day: position for position, day in enumerate(ordered)}

    @property
    def sessions(self) -> tuple[date, ...]:
        """As sessões, em ordem crescente."""
        return self._sessions

    def __len__(self) -> int:
        """Quantas sessões o calendário conhece."""
        return len(self._sessions)

    def __repr__(self) -> str:  # pragma: no cover — só diagnóstico
        if not self._sessions:
            return "TradingCalendar(())"
        return f"TradingCalendar({self._sessions[0]}..{self._sessions[-1]}, n={len(self)})"

    def is_session(self, day: date) -> bool:
        """O dia é sessão de negociação no calendário?"""
        return day in self._index

    def next_session(self, day: date) -> date | None:
        """Primeira sessão **estritamente depois** de `day`; `None` se o calendário acaba."""
        position = bisect_right(self._sessions, day)
        if position >= len(self._sessions):
            return None
        return self._sessions[position]

    def sessions_between(self, start: date, end: date) -> tuple[date, ...]:
        """Sessões com ``start <= dia <= end`` (inclusive nas duas pontas).

        É a lista crua do intervalo — útil para janelas de liquidez/realizada. Para o
        tempo até o vencimento use `business_days`, que tem a convenção do contrato.
        """
        low = bisect_left(self._sessions, start)
        high = bisect_right(self._sessions, end)
        return self._sessions[low:high]

    def business_days(self, start: date, end: date) -> int:
        """Sessões **depois** de `start` e **até** `end` (inclusive): `idx(end) − idx(start)`.

        Convenção de `T` (contrato §1): vencimento no fechamento do dia; vence hoje →
        `0`. Com as duas pontas no calendário o resultado é a diferença de índices.
        `end < start` levanta `ValueError` — tempo negativo é erro de chamada, não dado.
        """
        if end < start:
            raise ValueError(f"business_days com end < start: {start} -> {end}")
        return bisect_right(self._sessions, end) - bisect_right(self._sessions, start)


def business_days_between(cal: TradingCalendar, start: date, end: date) -> int:
    """Atalho funcional para `cal.business_days(start, end)` (contrato §1)."""
    return cal.business_days(start, end)


def year_fraction(business_days: int) -> float:
    """`T = du/252`, exato (sem arredondar — P-R)."""
    if business_days < 0:
        raise ValueError(f"year_fraction com dias úteis negativos: {business_days}")
    return business_days / SESSIONS_PER_YEAR


def discount_factor(rate_annual_pct: float, t: float) -> float:
    """`DF = exp(−r/100 · t)` — `rate_annual_pct` em % a.a., `t` em anos (spec §6.0)."""
    if not math.isfinite(rate_annual_pct):
        raise ValueError(f"taxa não finita: {rate_annual_pct}")
    if t < 0.0 or not math.isfinite(t):
        raise ValueError(f"t inválido para desconto: {t}")
    return math.exp(-(rate_annual_pct / 100.0) * t)


def log_df_interp(du: int, curve: Sequence[tuple[int, float]]) -> float:
    """ln(DF) em `du`, interpolado **linearmente em DU** entre os nós da curva (spec §6.0).

    `curve` são pares `(du, rate_annual_pct)` — a curva DI do dia. A interpolação é feita
    no log-DF (equivalente a taxa forward constante por trecho), não na taxa.

    Regras de borda (contrato §1):
    - **abaixo do primeiro nó**: usa o primeiro nó (a curva DI começa em `du=1`; um
      vencimento mais curto que o primeiro nó é curto demais para extrapolar taxa);
    - **acima do último nó**: `ValueError` — a curva não se extrapola para frente.
      Quem precisa de `None` + flag usa `log_df_interp_or_none`.
    """
    nodes, log_dfs = _curve_arrays(curve)
    if du <= nodes[0]:
        return log_dfs[0]
    if du > nodes[-1]:
        raise ValueError(
            f"du {du} acima do último nó da curva DI ({nodes[-1]}): não se extrapola "
            "para frente (spec §6.0)"
        )
    position = bisect_right(nodes, du) - 1
    if nodes[position] == du:
        return log_dfs[position]
    span = nodes[position + 1] - nodes[position]
    weight = (du - nodes[position]) / span
    return log_dfs[position] + weight * (log_dfs[position + 1] - log_dfs[position])


def log_df_interp_or_none(du: int, curve: Sequence[tuple[int, float]]) -> float | None:
    """Igual a `log_df_interp`, mas devolve `None` acima do último nó (null + flag).

    É o caminho para a camada que não pode levantar: vencimento além da curva →
    indisponível, nunca taxa inventada (regra 5 do AGENTS).
    """
    try:
        return log_df_interp(du, curve)
    except ValueError:
        return None


def _curve_arrays(curve: Sequence[tuple[int, float]]) -> tuple[list[int], list[float]]:
    """Nós ordenados por DU e o ln(DF) de cada um; valida a grade da curva."""
    if not curve:
        raise ValueError("curva DI vazia: sem nó não há desconto")
    by_du: dict[int, float] = {}
    for du, rate in curve:
        if du <= 0:
            raise ValueError(f"nó de curva com du <= 0: {du}")
        if not math.isfinite(rate):
            raise ValueError(f"taxa não finita no nó du={du}: {rate}")
        previous = by_du.get(du)
        if previous is not None and previous != rate:
            raise ValueError(f"dois nós para du={du} com taxas diferentes: {previous} e {rate}")
        by_du[du] = rate
    nodes = sorted(by_du)
    log_dfs = [-(by_du[node] / 100.0) * (node / SESSIONS_PER_YEAR) for node in nodes]
    return nodes, log_dfs
