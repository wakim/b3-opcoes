"""Avaliação de uma estrutura de opções (F4.1, contrato onda-j §3) — PURO.

Recebe as pernas **já resolvidas** (ticker→papel pelo ISIN, preço da sessão, IV e
gregas gravadas) e devolve o que é matemática, sem I/O, sem DuckDB e sem numpy:

- **payoff no vencimento**: valor terminal por perna (ação `s`, call `max(s−K,0)`,
  put `max(K−s,0)`), menos o prêmio líquido pago/recebido na abertura;
- **breakeven**: raízes lineares por segmento (kinks nos strikes > 0; o último
  segmento tem inclinação `Σ sign × quantity`, porque put não inclina acima do seu
  strike);
- **perda/ganho máximo**: extremos nos kinks + `s = 0`, com `−∞`/`+∞` quando a
  inclinação do último segmento aponta para lá — as magnitudes saem `None` + flag;
- **capital comprometido**: o débito quando há débito, a perda máxima quando há
  crédito (nunca margem: ela não está no dado);
- **gregas agregadas**: `Σ sign × quantity × grega` (ação: Δ = 1, Γ = Θ = ν = 0);
- **cenários**: 5 spots × 3 choques de IV × 3 horizontes = 45 pontos, com a opção
  reaprecificada por Black-76 (`derive/iv.py`) e o DF restante da curva DI da
  sessão (`log_df_interp_or_none`, nunca extrapolado).

Convenções (contrato §3.1 e ADR-017):

- `pnl_brl(s) = terminal_brl(s) − net_premium_brl`, com `net_premium > 0` = débito
  pago. A ação entra como perna de valor terminal `s` e "prêmio" `sessão × quantity`,
  que é o que faz a coberta ter breakeven `S₀ − prêmio da call`;
- preço **por ação** em R$ e `quantity` em ações — nada é multiplicado por 100;
- `max_loss_brl`/`max_gain_brl` são **magnitudes ≥ 0** (0 = "não perde"/"não ganha");
- `T' = 0` é intrínseco puro (`DF = 1`), nunca o primeiro nó da curva — a armadilha
  de `log_df_interp` com `du <= 0` está fechada aqui;
- sem perna de opção, os cenários não dependem de forward/DF/IV (só de `s'`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from b3o.derive.conventions import SESSIONS_PER_YEAR, log_df_interp_or_none
from b3o.derive.iv import black76_call_price, black76_put_price

structure_version = 1
"""Versão da matemática on-demand (contrato §0.6: nunca entra no `model_version`)."""

MAX_POINTS = 60
"""Teto de pontos por lista (contrato §0.5): payoff e cenários cabem em 60."""

PAYOFF_BASE_POINTS = 41
"""Grade base do gráfico entre `0,6·S` e `1,4·S` (contrato §3.6)."""

SPOT_MULTIPLIERS = (0.80, 0.90, 1.00, 1.10, 1.20)
"""Grade de spot dos cenários (contrato §3.5)."""

IV_SHIFTS_PTS = (-5.0, 0.0, 5.0)
"""Choques de IV em pontos (a IV gravada é % a.a.)."""

DAYS_FORWARD = (0, 7, 15)
"""Horizontes dos cenários, em dias úteis."""

_EPS = 1e-9
"""Tolerância de unicidade de breakeven/ponto (contrato §3.2)."""


@dataclass(frozen=True, slots=True)
class StructureLeg:
    """Uma perna **já resolvida** (entrada do cálculo; nada aqui é I/O)."""

    kind: str
    """`option` | `stock`."""

    ticker: str
    side: str
    """`long` | `short`."""

    quantity: int
    """Número de **ações** (1 contrato padrão = 100), nunca contratos."""

    price_brl: float
    """Preço por ação usado na abertura (opção: `mid`; ação: fechamento)."""

    price_low_brl: float | None
    """Piso da faixa `mid − spread/2`; `None` sem os dois lados do book."""

    price_high_brl: float | None
    """Teto da faixa `mid + spread/2`; `None` sem os dois lados do book."""

    is_liquid: bool
    option_side: str | None
    """`C` | `P`; `None` em ação."""

    style: str | None
    """`A` | `E` | `None` (ação e estilo desconhecido)."""

    strike_brl: float | None
    iv_pct: float | None
    """% a.a., como gravada; `None` quando a série não tem IV."""

    delta: float | None
    gamma: float | None
    theta_brl: float | None
    """R$/ação por dia útil (como gravada)."""

    vega_brl: float | None
    """R$/ação por 1 ponto de vol (como gravada)."""

    maturity: date | None
    """Vencimento da série (`None` na perna de ação)."""


@dataclass(frozen=True, slots=True)
class LegRow:
    """Eco resolvido da perna + o fluxo de caixa na abertura (contrato §3.7/§4)."""

    kind: str
    ticker: str
    side: str
    quantity: int
    price_brl: float
    price_low_brl: float | None
    price_high_brl: float | None
    premium_brl: float
    """`+quantity × price` (long paga) ou `−quantity × price` (short recebe)."""

    is_liquid: bool
    option_side: str | None
    style: str | None
    strike_brl: float | None
    iv_pct: float | None
    delta: float | None
    gamma: float | None
    theta_brl: float | None
    vega_brl: float | None
    maturity: date | None


@dataclass(frozen=True, slots=True)
class PayoffPoint:
    """Um ponto da curva de payoff no vencimento (R$)."""

    spot_brl: float
    pnl_brl: float


@dataclass(frozen=True, slots=True)
class ScenarioRow:
    """Um ponto da grade de cenários: P&L com o spot, a IV e o tempo deslocados."""

    spot_brl: float
    iv_shift_pts: float
    days_forward: int
    pnl_brl: float


@dataclass(frozen=True, slots=True)
class StructureResult:
    """O resultado descritivo da estrutura (contrato §3.7, ordem e nomes fixos)."""

    structure_version: int
    spot_brl: float
    forward_brl: float | None
    df: float | None
    t: float | None
    maturity: date | None
    legs: list[LegRow]
    net_premium_brl: float
    """Positivo = débito pago; negativo = crédito recebido (inclui a ação)."""

    net_premium_low_brl: float | None
    net_premium_high_brl: float | None
    breakeven_brl: list[float]
    """Raízes em `s ≥ 0`, ordenadas e únicas; lista vazia é resposta legítima."""

    max_loss_brl: float | None
    """Magnitude ≥ 0; `None` só quando a perda é ilimitada (+ flag)."""

    max_gain_brl: float | None
    """Magnitude ≥ 0; `None` só quando o ganho é ilimitado (+ flag)."""

    capital_committed_brl: float | None
    capital_input_brl: float | None
    capital_over: bool
    delta: float
    gamma: float
    theta_brl: float
    vega_brl: float
    payoff: list[PayoffPoint]
    scenarios: list[ScenarioRow] | None
    """`None` = não calculado/indisponível; lista vazia não é resposta honesta."""

    flags: list[str]
    """Subconjunto fixo do contrato §3.7, em ordem alfabética."""


def evaluate(
    legs: Sequence[StructureLeg],
    *,
    spot_brl: float,
    forward_brl: float | None,
    df: float | None,
    t: float | None,
    curve: Sequence[tuple[int, float]] = (),
    maturity: date | None = None,
    capital_brl: float | None = None,
    scenarios: bool = True,
) -> StructureResult:
    """Avalia a estrutura: payoff, breakevens, capital, gregas e cenários.

    `forward_brl`/`df`/`t` são os insumos do vencimento (paridade + curva da sessão,
    como o pipeline calcula). `curve` é a curva DI da sessão em `(du, taxa % a.a.)`
    e só é usada para o DF **restante** dos cenários com `dias > 0`; `dias = 0` usa
    o `df` recebido. Sem perna de opção, nada disso é exigido: os cenários de uma
    estrutura só de ação valem `s'` em qualquer horizonte.

    `scenarios=False` devolve `scenarios=None` **sem** flag (o chamador pediu para
    não calcular); indisponibilidade por falta de insumo vira flag.
    """
    resolved = tuple(legs)
    if not resolved:
        raise ValueError("estrutura sem pernas")
    if not (math.isfinite(spot_brl) and spot_brl > 0.0):
        raise ValueError(f"spot inválido para a estrutura: {spot_brl!r}")
    for leg in resolved:
        _validate_leg(leg)

    net_premium = sum(_premium(leg) for leg in resolved)
    rows = [_leg_row(leg) for leg in resolved]
    low, high = _premium_band(resolved)
    kinks = _kinks(resolved)
    values = [0.0, *kinks]
    pnl_at = [_pnl(resolved, net_premium, point) for point in values]
    slope_inf = _slope_above_strikes(resolved)

    breakevens = _breakevens(values, pnl_at, slope_inf)
    pnl_min, pnl_max = _extrema(pnl_at, slope_inf)
    flags: set[str] = set()
    if pnl_min is None:
        flags.add("unbounded_loss")
    if pnl_max is None:
        flags.add("unbounded_gain")

    max_loss = None if pnl_min is None else max(0.0, -pnl_min)
    max_gain = None if pnl_max is None else max(0.0, pnl_max)
    # Risco ilimitado não tem "capital comprometido": o débito pago não é o risco
    # (ratio write compra ação no débito e vende mais calls do que tem — perda
    # ilimitada). `None` aqui é a resposta honesta (contrato §3.3).
    if max_loss is None:
        committed = None
    elif net_premium > 0.0:
        committed = net_premium
    else:
        committed = max_loss
    capital_over = (
        capital_brl is not None and committed is not None and committed > capital_brl
    )
    if capital_over:
        flags.add("capital_over")
    if any(not leg.is_liquid for leg in resolved):
        flags.add("illiquid_leg")

    scenario_rows: list[ScenarioRow] | None = None
    if scenarios:
        scenario_rows, causes = _scenario_grid(
            resolved,
            net_premium=net_premium,
            spot_brl=spot_brl,
            forward_brl=forward_brl,
            df=df,
            t=t,
            curve=curve,
        )
        if scenario_rows is None:
            flags.update(causes)
            flags.add("scenarios_unavailable")

    payoff = _payoff_curve(
        resolved,
        net_premium,
        spot_brl,
        [*kinks, *breakevens],
        must_keep=breakevens,
    )
    delta, gamma, theta, vega = _aggregate_greeks(resolved)

    return StructureResult(
        structure_version=structure_version,
        spot_brl=spot_brl,
        forward_brl=forward_brl,
        df=df,
        t=t,
        maturity=maturity,
        legs=rows,
        net_premium_brl=net_premium,
        net_premium_low_brl=low,
        net_premium_high_brl=high,
        breakeven_brl=breakevens,
        max_loss_brl=max_loss,
        max_gain_brl=max_gain,
        capital_committed_brl=committed,
        capital_input_brl=capital_brl,
        capital_over=capital_over,
        delta=delta,
        gamma=gamma,
        theta_brl=theta,
        vega_brl=vega,
        payoff=payoff,
        scenarios=scenario_rows,
        flags=sorted(flags),
    )


# --------------------------------------------------------------------------- #
# Validação e leitura das pernas
# --------------------------------------------------------------------------- #


def _validate_leg(leg: StructureLeg) -> None:
    """Perna resolvida só entra com forma e números utilizáveis (falha explícita)."""
    if leg.kind not in ("option", "stock"):
        raise ValueError(f"kind inválido na perna: {leg.kind!r}")
    if leg.side not in ("long", "short"):
        raise ValueError(f"side inválido na perna: {leg.side!r}")
    if isinstance(leg.quantity, bool) or not isinstance(leg.quantity, int):
        raise ValueError(f"quantity precisa ser inteiro de ações: {leg.quantity!r}")
    if leg.quantity <= 0:
        raise ValueError(f"quantity precisa ser > 0: {leg.quantity!r}")
    if not (math.isfinite(leg.price_brl) and leg.price_brl >= 0.0):
        raise ValueError(f"preço inválido em {leg.ticker}: {leg.price_brl!r}")
    if leg.kind == "option":
        if leg.strike_brl is None or not math.isfinite(leg.strike_brl):
            raise ValueError(f"perna de opção sem strike: {leg.ticker}")
        if leg.strike_brl <= 0.0:
            raise ValueError(f"strike não positivo em {leg.ticker}: {leg.strike_brl!r}")


def _sign(side: str) -> float:
    """`+1` em long, `−1` em short (única fonte do sinal da perna)."""
    return 1.0 if side == "long" else -1.0


def _premium(leg: StructureLeg) -> float:
    """Fluxo de caixa na abertura: long paga (+), short recebe (−)."""
    return _sign(leg.side) * leg.quantity * leg.price_brl


def _premium_band(legs: Sequence[StructureLeg]) -> tuple[float | None, float | None]:
    """Faixa do prêmio líquido; `None` quando alguma perna não tem book dos dois lados.

    O "low" é o menor prêmio líquido possível dado o spread de cada perna (long no
    bid, short no ask) e o "high" o maior — a faixa é ordenada por construção, o que
    um `Σ ± quantity × price_low` cru não garante numa estrutura vendida.
    """
    low = 0.0
    high = 0.0
    for leg in legs:
        if leg.price_low_brl is None or leg.price_high_brl is None:
            return None, None
        sign = _sign(leg.side)
        first = sign * leg.quantity * leg.price_low_brl
        second = sign * leg.quantity * leg.price_high_brl
        low += min(first, second)
        high += max(first, second)
    return low, high


def _leg_row(leg: StructureLeg) -> LegRow:
    """Eco da perna com o prêmio (contrato §3.7)."""
    return LegRow(
        kind=leg.kind,
        ticker=leg.ticker,
        side=leg.side,
        quantity=leg.quantity,
        price_brl=leg.price_brl,
        price_low_brl=leg.price_low_brl,
        price_high_brl=leg.price_high_brl,
        premium_brl=_premium(leg),
        is_liquid=leg.is_liquid,
        option_side=leg.option_side,
        style=leg.style,
        strike_brl=leg.strike_brl,
        iv_pct=leg.iv_pct,
        delta=leg.delta,
        gamma=leg.gamma,
        theta_brl=leg.theta_brl,
        vega_brl=leg.vega_brl,
        maturity=leg.maturity,
    )


# --------------------------------------------------------------------------- #
# Payoff, breakeven e extremos
# --------------------------------------------------------------------------- #


def _terminal_value(leg: StructureLeg, spot_brl: float) -> float:
    """Valor terminal por ação da perna em `spot_brl` (contrato §3.1)."""
    if leg.kind == "stock":
        return spot_brl
    strike = leg.strike_brl or 0.0
    if leg.option_side == "C":
        return max(spot_brl - strike, 0.0)
    return max(strike - spot_brl, 0.0)


def _terminal(legs: Sequence[StructureLeg], spot_brl: float) -> float:
    """`Σ sign × quantity × valor_terminal` (R$)."""
    return sum(
        _sign(leg.side) * leg.quantity * _terminal_value(leg, spot_brl) for leg in legs
    )


def _pnl(legs: Sequence[StructureLeg], net_premium: float, spot_brl: float) -> float:
    """`terminal(s) − net_premium` (R$)."""
    return _terminal(legs, spot_brl) - net_premium


def _kinks(legs: Sequence[StructureLeg]) -> list[float]:
    """Strikes (> 0) que quebram a linearidade do payoff, ordenados e únicos."""
    return _unique(
        leg.strike_brl
        for leg in legs
        if leg.kind == "option" and leg.strike_brl is not None and leg.strike_brl > 0.0
    )


def _slope_above_strikes(legs: Sequence[StructureLeg]) -> float:
    """Inclinação do **último** segmento do payoff (contrato §3.2).

    Acima de todos os strikes só a ação (`s`) e a call (`s − K`) continuam lineares;
    a put satura no seu strike (valor terminal 0 dali para cima) e contribui `0` —
    é isso que dá perda máxima finita a uma put vendida.
    """
    return sum(
        _sign(leg.side) * leg.quantity
        for leg in legs
        if leg.kind == "stock" or leg.option_side == "C"
    )


def _breakevens(
    points: Sequence[float], values: Sequence[float], slope_inf: float
) -> list[float]:
    """Raízes de `pnl(s)` em `s ≥ 0`, ordenadas e únicas (contrato §3.2).

    `points` são `0` + os kinks e `values` o P&L em cada um. A função é linear por
    partes entre kinks, então cada raiz sai de interpolação linear; o último
    segmento é a reta de inclinação `slope_inf` a partir do último kink (raiz finita
    mesmo com perda/ganho ilimitado). O próprio kink entra quando `pnl = 0` nele —
    inclusive `s = 0`.
    """
    roots: list[float] = []
    for index, (point, value) in enumerate(zip(points, values, strict=True)):
        if value == 0.0:
            roots.append(point)  # no kink (ou em s=0) o próprio ponto é raiz
        if index + 1 >= len(points):
            break
        next_point, next_value = points[index + 1], values[index + 1]
        if value == 0.0 or next_value == 0.0:
            continue  # já capturado como kink; não se interpola sobre zero
        if (value < 0.0) == (next_value < 0.0):
            continue
        roots.append(point + (next_point - point) * value / (value - next_value))
    if slope_inf != 0.0:
        last_point, last_value = points[-1], values[-1]
        root = last_point - last_value / slope_inf
        # A reta do último segmento só vale acima do último kink: raiz "para trás"
        # já foi (ou não é) resolvida pelos trechos finitos — não se inventa raiz.
        if root >= last_point:
            roots.append(root)
    return [root for root in _unique(roots) if root >= 0.0]


def _extrema(values: Sequence[float], slope_inf: float) -> tuple[float | None, float | None]:
    """`(pnl_min, pnl_max)` nos kinks + `s = 0`; `None` quando o limite é infinito."""
    pnl_min = min(values)
    pnl_max = max(values)
    if slope_inf < 0.0:
        return None, pnl_max
    if slope_inf > 0.0:
        return pnl_min, None
    return pnl_min, pnl_max


# --------------------------------------------------------------------------- #
# Gregas
# --------------------------------------------------------------------------- #


def _aggregate_greeks(legs: Sequence[StructureLeg]) -> tuple[float, float, float, float]:
    """`Σ sign × quantity × grega`; ação entra com Δ = 1 e Γ = Θ = ν = 0.

    Perna sem grega gravada contribui `0` (o `None` continua visível no campo da
    perna; a agregação é sobre o que existe — contrato §3.4, sem flag `no_greeks`).
    """
    delta = gamma = theta = vega = 0.0
    for leg in legs:
        sign = _sign(leg.side)
        quantity = leg.quantity
        if leg.kind == "stock":
            delta += sign * quantity
            continue
        delta += sign * quantity * (leg.delta or 0.0)
        gamma += sign * quantity * (leg.gamma or 0.0)
        theta += sign * quantity * (leg.theta_brl or 0.0)
        vega += sign * quantity * (leg.vega_brl or 0.0)
    return delta, gamma, theta, vega


# --------------------------------------------------------------------------- #
# Cenários
# --------------------------------------------------------------------------- #


def _scenario_grid(
    legs: Sequence[StructureLeg],
    *,
    net_premium: float,
    spot_brl: float,
    forward_brl: float | None,
    df: float | None,
    t: float | None,
    curve: Sequence[tuple[int, float]],
) -> tuple[list[ScenarioRow] | None, set[str]]:
    """45 pontos (5 spots × 3 choques × 3 horizontes) ou `None` + as causas.

    Sem perna de opção nada é exigido: o valor da ação é `s'` em qualquer horizonte
    e a grade é sempre calculável. Com opção, falta de forward/DF/IV derruba a grade
    inteira (lista parcial seria mentira) e as causas voltam para as flags.
    """
    options = [leg for leg in legs if leg.kind == "option"]
    causes: set[str] = set()
    if options:
        if forward_brl is None or not math.isfinite(forward_brl) or forward_brl <= 0.0:
            causes.add("forward_unavailable")
        if t is None or not math.isfinite(t):
            causes.add("scenarios_unavailable")
        if df is None or not math.isfinite(df) or df <= 0.0:
            causes.add("no_rates")
        if any(leg.iv_pct is None for leg in options):
            causes.add("no_iv")
    if causes:
        return None, causes
    time_to_expiry = t if t is not None else 0.0
    du = round(time_to_expiry * SESSIONS_PER_YEAR)
    rows: list[ScenarioRow] = []
    for multiplier in SPOT_MULTIPLIERS:
        spot_scenario = spot_brl * multiplier
        for shift in IV_SHIFTS_PTS:
            for days in DAYS_FORWARD:
                total = 0.0
                for leg in legs:
                    sign = _sign(leg.side)
                    if leg.kind == "stock":
                        total += sign * leg.quantity * spot_scenario
                        continue
                    time_left = max(time_to_expiry - days / SESSIONS_PER_YEAR, 0.0)
                    forward_scenario = forward_brl * spot_scenario / spot_brl
                    strike = leg.strike_brl or 0.0
                    if time_left == 0.0:
                        value = _intrinsic(leg, forward_scenario, strike)
                    else:
                        discount = _remaining_df(df, du - days, curve, days)
                        if discount is None:
                            return None, {"no_rates"}
                        sigma = max((leg.iv_pct or 0.0) + shift, 0.0) / 100.0
                        value = _black76(
                            leg, forward_scenario, strike, time_left, discount, sigma
                        )
                    total += sign * leg.quantity * value
                rows.append(
                    ScenarioRow(
                        spot_brl=spot_scenario,
                        iv_shift_pts=shift,
                        days_forward=days,
                        pnl_brl=total - net_premium,
                    )
                )
    return rows, causes


def _remaining_df(
    df: float | None, du_left: int, curve: Sequence[tuple[int, float]], days: int
) -> float | None:
    """DF do prazo restante: `dias = 0` usa o `df` do vencimento; senão, a curva.

    Curva que não cobre `du − dias` (acima do último nó) devolve `None`: não se
    extrapola taxa, e `DF = 1` só vale em `T' = 0`, nunca para "fingir" ausência.
    """
    if days == 0:
        return df
    if du_left <= 0:
        return 1.0  # prazo restante zero: intrínseco/hoje tem DF = 1 por definição
    log_df = log_df_interp_or_none(du_left, curve)
    if log_df is None:
        return None
    return math.exp(log_df)


def _intrinsic(leg: StructureLeg, forward_brl: float, strike_brl: float) -> float:
    """Intrínseco puro (`DF = 1`) — o valor em `T' = 0`."""
    if leg.option_side == "C":
        return max(forward_brl - strike_brl, 0.0)
    return max(strike_brl - forward_brl, 0.0)


def _black76(
    leg: StructureLeg,
    forward_brl: float,
    strike_brl: float,
    time_left: float,
    discount: float,
    sigma: float,
) -> float:
    """Preço Black-76 da opção no cenário (σ ≤ 0 vira o limite intrínseco × DF)."""
    if sigma <= 0.0:
        return discount * _intrinsic(leg, forward_brl, strike_brl)
    if leg.option_side == "C":
        return black76_call_price(forward_brl, strike_brl, time_left, discount, sigma)
    return black76_put_price(forward_brl, strike_brl, time_left, discount, sigma)


# --------------------------------------------------------------------------- #
# Curva de payoff (gráfico)
# --------------------------------------------------------------------------- #


def _payoff_curve(
    legs: Sequence[StructureLeg],
    net_premium: float,
    spot_brl: float,
    specials: Sequence[float],
    must_keep: Sequence[float] = (),
) -> list[PayoffPoint]:
    """41 pontos entre `0,6·S` e `1,4·S` + strikes/breakevens, capado em 60 (§3.6).

    Prioridade quando não cabe tudo: **breakevens** (`must_keep`) primeiro, depois os
    strikes e, por último, a grade base (reduzida uniformemente). Preserva os
    breakevens até o teto de 60; acima disso (impossível com o limite de 60 pernas
    do contrato) eles são reduzidos uniformemente.
    """
    low = 0.6 * spot_brl
    high = 1.4 * spot_brl
    inside = _unique(point for point in specials if low <= point <= high)
    must = _unique(point for point in must_keep if low <= point <= high)
    if len(must) >= MAX_POINTS:
        lattice = _thin(must, MAX_POINTS)
    elif len(inside) >= MAX_POINTS:
        remaining = MAX_POINTS - len(must)
        others = [
            point
            for point in inside
            if all(abs(point - kept) > _EPS for kept in must)
        ]
        lattice = _unique([*must, *_thin(others, remaining)])
    else:
        base = [
            low + (high - low) * index / (PAYOFF_BASE_POINTS - 1)
            for index in range(PAYOFF_BASE_POINTS)
        ]
        lattice = _unique([*inside, *_thin(base, MAX_POINTS - len(inside))])
    return [
        PayoffPoint(spot_brl=point, pnl_brl=_pnl(legs, net_premium, point))
        for point in lattice
    ]


# --------------------------------------------------------------------------- #
# Utilidades puras
# --------------------------------------------------------------------------- #


def _unique(values: Iterable[float]) -> list[float]:
    """Ordena e remove repetidos com tolerância `1e-9` (contrato §3.2)."""
    ordered = sorted(float(value) for value in values)
    result: list[float] = []
    for value in ordered:
        if not result or abs(value - result[-1]) > _EPS:
            result.append(value)
    return result


def _thin(values: Sequence[float], limit: int) -> list[float]:
    """Reduz a lista a `limit` pontos, uniformemente, mantendo as duas pontas."""
    if limit <= 0:
        return []
    if len(values) <= limit:
        return list(values)
    if limit == 1:
        return [values[0]]
    step = (len(values) - 1) / (limit - 1)
    return [values[round(index * step)] for index in range(limit)]
