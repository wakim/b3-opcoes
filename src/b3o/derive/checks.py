"""Validadores de consistência do smile (F1.10; spec §6.11; contrato Onda E §2 e §5.7) — PURO.

Três testes de não-arbitragem, todos em **variância total** `w = (σ/100)²·t`, que é
a grandeza em que a convexidade e o calendário são lineares:

- **butterfly:** para três strikes adjacentes do mesmo vencimento,
  `w(K_{i+1}) − 2·w(K_i) + w(K_{i−1}) ≥ −tol`, com `tol = butterfly_tol · w_atm` —
  `butterfly_tol` é o **multiplicador** (5% da variância total ATM, contrato §5.7),
  nunca o valor absoluto. Variância não-convexa = densidade negativa implícita;
- **calendário:** no mesmo strike entre vencimentos adjacentes, `w` **não-decrescente**
  com a mesma folga relativa (`−butterfly_tol · w_atm` do vencimento da frente) —
  `w` menor no prazo longo é arbitragem de calendário. A folga é relativa pela mesma
  razão da butterfly: um mesmo erro absoluto em `w` pesa mais num tenor curto (o
  §5.7 fechou as duas tolerâncias juntas, então o multiplicador é um só);
- **monotonicidade:** por vencimento, IV **não-crescente em `K`** com tolerância de
  `monotonicity_tol` **vol points** (0,05; §5.7). Vale para call e para put por
  paridade (a put europeia de mesmo strike tem a mesma IV da call), por isso a
  comparação é entre **todos** os pontos do vencimento ordenados por strike — não
  entre "a perna de call" e "a perna de put" separadas.

Qualquer violação marca a série com a flag específica **e** com `inconsistent`: o
pipeline a exclui do smile e do mispricing (spec §6.11 — os validadores filtram,
não informam). `ok_smile` só é `True` quando nenhuma cadeia violou nada.

Convenções de atribuição (o contrato deixa a escolha aberta; fixadas aqui)
-----------------------------------------------------------------------
Uma violação é **de um conjunto de séries**, não de um ponto isolado, porque não há
como saber qual strike está errado — e o consumidor só precisa saber o que sai do
smile:

- **butterfly:** o trio inteiro (`i−1`, `i`, `i+1`) recebe `bfly_negative` (o
  contrato §2 aceita "a do meio e, se quiser rigor, as duas asas"; as três é o
  conjunto que, removido, garante que a não-convexidade sai da cadeia — tirar só o
  meio pode deixar as asas vizinhas formando outro trio torto);
- **monotonicidade:** as **duas** séries do par adjacente (`i`, `i+1`) recebem
  `monotonicity_violation`. O contrato aponta "a de `K` menor" como suspeita natural;
  o par é o que contém o valor alto — o caso real é a asa ilíquida invertida acima
  do vizinho, e marcar só o `K` menor deixaria justamente o ponto culpado no smile;
- **calendário:** as duas séries daquele strike (vencimento da frente e o seguinte)
  recebem `calendar_violation`.

Pontos com `strike <= 0` (armadilha do `strike == 0` de IBOV, AGENTS.md), `iv_pct`
não-finito ou `None`, `t` não-finito ou `<= 0` **não são avaliados e não geram
flag**: são ausência de dado, não inconsistência. Eles também não são removidos da
sequência de entrada — este módulo não muta nada.

Strikes repetidos no mesmo vencimento (call e put europeias do mesmo strike, que por
paridade têm a mesma IV; ou duas linhas do mesmo ticker) são consolidados pela
**média** de `iv` e de `w` por strike — mesma escolha do `atm.py` — e a violação
atinge **todas** as séries daquele strike.

Âncora da folga (`w_atm`)
------------------------
Por vencimento, `w_atm` é o `w` do strike **mais próximo de `forward`** (desempate
pelo strike maior). `forward is None`, não-finito ou `<= 0` → a âncora é a
**mediana dos `w` do vencimento**: o `F` não existe, mas a tolerância não pode virar
zero silencioso nem depender do strike que o acaso pôs na grade.

`strike` e `forward` em **centavos** (mesma unidade do `forward.py`/`atm.py`);
`iv_pct` em **% a.a.**; `t` em anos úteis (`du/252`). A cadeia é a superfície de
**um** papel numa data (o contrato não recebe o papel: quem chama fatia).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

from b3o.derive.atm import SmilePoint

BUTTERFLY_TOL = 0.05
"""Multiplicador da folga da butterfly (e do calendário): 5% da variância total ATM
(contrato §5.7). É **relativo**, não absoluto: o mesmo `Δ²w` que passa num
vencimento longo viola num curto."""

MONOTONICITY_TOL = 0.05
"""Folga da monotonicidade em **vol points** (0,05 = 5 centésimos de ponto de IV),
contrato §5.7. Absoluta porque IV já é a unidade final da comparação."""

FLAG_BFLY_NEGATIVE = "bfly_negative"
"""Segunda diferença de `w` abaixo de `−butterfly_tol·w_atm` (spec §5.4/§6.11)."""

FLAG_CALENDAR_VIOLATION = "calendar_violation"
"""`w` menor no vencimento seguinte, no mesmo strike, acima da folga relativa."""

FLAG_MONOTONICITY_VIOLATION = "monotonicity_violation"
"""IV cresce com `K` acima de `monotonicity_tol` vol points."""

FLAG_INCONSISTENT = "inconsistent"
"""Série com qualquer violação: fora do smile e do mispricing (spec §6.11)."""

FLAG_ORDER = (
    FLAG_BFLY_NEGATIVE,
    FLAG_CALENDAR_VIOLATION,
    FLAG_MONOTONICITY_VIOLATION,
    FLAG_INCONSISTENT,
)
"""Ordem determinística das flags no relatório (violação primeiro, `inconsistent` por último)."""

_FLOAT_REL_TOL = 1e-12
"""Folga de comparação de ponto flutuante — a borda da tolerância nunca é `==`."""


@dataclass(frozen=True, slots=True)
class ConsistencyReport:
    """Resultado dos validadores: o que sai do smile, e por quê."""

    series_flags: Mapping[str, tuple[str, ...]]
    """Ticker da série → flags. Só séries com ao menos uma flag aparecem."""

    ok_smile: bool
    """`True` = nenhuma violação em nenhuma cadeia (equivale a `series_flags` vazio)."""

    n_series: int
    """Pontos **avaliados** (com `strike > 0`, IV e `t` finitos e positivos)."""

    flags_by_expiry: Mapping[date, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    """Vencimento → união das flags das suas séries (com `inconsistent`); vazio no limpo."""


def check_chain(
    chain: Sequence[SmilePoint],
    forward: float | None,
    *,
    butterfly_tol: float = BUTTERFLY_TOL,
    monotonicity_tol: float = MONOTONICITY_TOL,
) -> ConsistencyReport:
    """Valida a cadeia de **um** papel (todas as maturities de uma data).

    Não muta `chain` e não depende da ordem de entrada: o resultado é ordenado por
    vencimento, strike e ticker.
    """
    usable: dict[date, list[SmilePoint]] = {}
    n_series = 0
    for point in chain:
        if not _is_usable(point):
            continue
        n_series += 1
        usable.setdefault(point.maturity, []).append(point)

    levels_by_maturity = {maturity: _levels(points) for maturity, points in usable.items()}

    flags: dict[str, list[str]] = {}

    for levels in levels_by_maturity.values():
        # Monotonicidade vale com 2 strikes; butterfly precisa de 3 (`_check_butterfly`
        # não faz nada com menos).
        _check_monotonicity(levels, monotonicity_tol, flags)
        _check_butterfly(levels, _w_atm(levels, forward), butterfly_tol, flags)

    _check_calendar(levels_by_maturity, forward, butterfly_tol, flags)

    return _report(flags, n_series, levels_by_maturity)


# --- as três regras ----------------------------------------------------------


def _check_butterfly(
    levels: Sequence[_Level], w_atm: float, tol_multiplier: float, flags: dict[str, list[str]]
) -> None:
    """`w` convexa em `K` por trio de strikes adjacentes (§5.7)."""
    bound = -tol_multiplier * w_atm
    for i in range(1, len(levels) - 1):
        second = levels[i + 1].w - 2.0 * levels[i].w + levels[i - 1].w
        if _below(second, bound):
            # Trio inteiro: é o conjunto que sai do smile (ver docstring do módulo).
            for level in levels[i - 1 : i + 2]:
                _flag(flags, level.tickers, FLAG_BFLY_NEGATIVE)


def _check_monotonicity(
    levels: Sequence[_Level], tol: float, flags: dict[str, list[str]]
) -> None:
    """IV não-crescente em `K` (§5.7), com folga `tol` em vol points."""
    for i in range(len(levels) - 1):
        rise = levels[i + 1].iv - levels[i].iv
        if _above(rise, tol):
            _flag(flags, levels[i].tickers, FLAG_MONOTONICITY_VIOLATION)
            _flag(flags, levels[i + 1].tickers, FLAG_MONOTONICITY_VIOLATION)


def _check_calendar(
    levels_by_maturity: Mapping[date, Sequence[_Level]],
    forward: float | None,
    tol_multiplier: float,
    flags: dict[str, list[str]],
) -> None:
    """`w` não-decrescente no mesmo strike entre vencimentos adjacentes (§5.7).

    A folga usa o `w_atm` do vencimento **da frente** (tenor menor): é ele que
    encolhe primeiro, e é a referência que não deixa um erro absoluto grande passar
    só porque o vencimento de trás é longo.
    """
    maturities = sorted(levels_by_maturity)
    for near, far in zip(maturities, maturities[1:], strict=False):
        near_levels = levels_by_maturity[near]
        far_by_strike = {level.strike: level for level in levels_by_maturity[far]}
        bound = -tol_multiplier * _w_atm(near_levels, forward)
        for level in near_levels:
            other = far_by_strike.get(level.strike)
            if other is None:
                continue
            if _below(other.w - level.w, bound):
                _flag(flags, level.tickers, FLAG_CALENDAR_VIOLATION)
                _flag(flags, other.tickers, FLAG_CALENDAR_VIOLATION)


# --- montagem do resultado ---------------------------------------------------


def _report(
    flags: Mapping[str, list[str]],
    n_series: int,
    levels_by_maturity: Mapping[date, Sequence[_Level]],
) -> ConsistencyReport:
    """Fecha o relatório: `inconsistent` em quem violou, ordem determinística."""
    series_flags: dict[str, tuple[str, ...]] = {}
    for ticker in sorted(flags):
        present = set(flags[ticker])
        present.add(FLAG_INCONSISTENT)
        series_flags[ticker] = tuple(flag for flag in FLAG_ORDER if flag in present)

    flags_by_expiry: dict[date, tuple[str, ...]] = {}
    for maturity in sorted(levels_by_maturity):
        present: set[str] = set()
        for level in levels_by_maturity[maturity]:
            for ticker in level.tickers:
                present.update(series_flags.get(ticker, ()))
        if present:
            flags_by_expiry[maturity] = tuple(flag for flag in FLAG_ORDER if flag in present)

    return ConsistencyReport(
        series_flags=MappingProxyType(series_flags),
        ok_smile=not series_flags,
        n_series=n_series,
        flags_by_expiry=MappingProxyType(flags_by_expiry),
    )


@dataclass(frozen=True, slots=True)
class _Level:
    """Um strike consolidado do vencimento: `w`, IV e as séries que o formaram."""

    strike: int
    w: float
    iv: float
    tickers: tuple[str, ...]


def _is_usable(point: SmilePoint) -> bool:
    """Ponto com dado utilizável — ausência de dado não é inconsistência."""
    if point.strike <= 0 or point.iv_pct is None:
        return False
    return math.isfinite(point.iv_pct) and math.isfinite(point.t) and point.t > 0.0


def _levels(points: Sequence[SmilePoint]) -> list[_Level]:
    """Consolida por strike (média de `w` e de `iv`), em ordem crescente de strike."""
    grouped: dict[int, list[SmilePoint]] = {}
    for point in points:
        grouped.setdefault(point.strike, []).append(point)

    levels: list[_Level] = []
    for strike in sorted(grouped):
        group = sorted(grouped[strike], key=lambda p: p.ticker)
        ws: list[float] = []
        ivs: list[float] = []
        for point in group:
            iv = point.iv_pct
            if iv is None:  # `_is_usable` já filtrou; guarda para o leitor/tipo
                continue
            ivs.append(iv)
            ws.append((iv / 100.0) ** 2 * point.t)
        levels.append(
            _Level(
                strike=strike,
                w=sum(ws) / len(ws),
                iv=sum(ivs) / len(ivs),
                tickers=tuple(p.ticker for p in group),
            )
        )
    return levels


def _w_atm(levels: Sequence[_Level], forward: float | None) -> float:
    """Âncora da folga: `w` do strike mais próximo de `F`; sem `F`, a mediana."""
    if forward is not None and math.isfinite(forward) and forward > 0.0:
        nearest = min(levels, key=lambda level: (abs(level.strike - forward), level.strike))
        return nearest.w
    ordered = sorted(level.w for level in levels)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def _flag(flags: dict[str, list[str]], tickers: Sequence[str], flag: str) -> None:
    for ticker in tickers:
        bucket = flags.setdefault(ticker, [])
        if flag not in bucket:
            bucket.append(flag)


def _below(value: float, bound: float) -> bool:
    """`value < bound`, ignorando a borda onde os dois são o mesmo float."""
    return value < bound and not math.isclose(value, bound, rel_tol=_FLOAT_REL_TOL, abs_tol=0.0)


def _above(value: float, bound: float) -> bool:
    """`value > bound`, idem `_below`."""
    return value > bound and not math.isclose(value, bound, rel_tol=_FLOAT_REL_TOL, abs_tol=0.0)
