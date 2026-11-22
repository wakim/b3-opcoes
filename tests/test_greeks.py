"""Gregas de Black-76 (F1.4) — conferidas por diferença finita, não pela fórmula.

Diferença finita central com `h = 1e-4` em `F` e em `σ`, `h = 1e-6` em `t` e em `r`
(contrato §1), tolerância relativa `1e-3`. As identidades exatas (paridade de delta,
vega igual para call e put, `θ_pct = θ_brl/prêmio`) fecham o resto.
"""

from __future__ import annotations

import math

import pytest

from b3o.derive.greeks import SESSIONS_PER_YEAR, Greeks, greeks_black76
from b3o.derive.iv import black76_call_price, black76_put_price

CASES = [
    (100.0, 100.0, 0.25, 0.98, 0.30),
    (100.0, 90.0, 1.0, 0.93, 0.25),
    (100.0, 115.0, 0.08, 0.99, 0.60),
    (4300.0, 4400.0, 0.08, 0.989, 0.33),
    (50.0, 60.0, 2.0, 0.80, 0.20),
]


def price_fn(kind: str):
    return black76_call_price if kind == "C" else black76_put_price


def fd_delta(kind: str, F: float, K: float, t: float, df: float, sigma: float) -> float:
    h = 1e-4 * F
    fn = price_fn(kind)
    return (fn(F + h, K, t, df, sigma) - fn(F - h, K, t, df, sigma)) / (2.0 * h)


def fd_gamma(kind: str, F: float, K: float, t: float, df: float, sigma: float) -> float:
    h = 1e-4 * F
    fn = price_fn(kind)
    upper = fn(F + h, K, t, df, sigma)
    middle = fn(F, K, t, df, sigma)
    lower = fn(F - h, K, t, df, sigma)
    return (upper - 2.0 * middle + lower) / (h * h)


def fd_vega(kind: str, F: float, K: float, t: float, df: float, sigma: float) -> float:
    h = 1e-4
    fn = price_fn(kind)
    return (fn(F, K, t, df, sigma + h) - fn(F, K, t, df, sigma - h)) / (2.0 * h)


def fd_theta(kind: str, F: float, K: float, t: float, df: float, sigma: float) -> float:
    """Θ anual = −∂V/∂T com `r` constante (o DF acompanha o prazo)."""
    h = 1e-6
    rate = -math.log(df) / t
    fn = price_fn(kind)
    later = fn(F, K, t + h, math.exp(-rate * (t + h)), sigma)
    earlier = fn(F, K, t - h, math.exp(-rate * (t - h)), sigma)
    return -(later - earlier) / (2.0 * h)


def fd_rho(kind: str, F: float, K: float, t: float, df: float, sigma: float) -> float:
    """∂V/∂r com `F` fixo, por 1,00 de taxa."""
    h = 1e-6
    rate = -math.log(df) / t
    fn = price_fn(kind)
    up = fn(F, K, t, math.exp(-(rate + h) * t), sigma)
    down = fn(F, K, t, math.exp(-(rate - h) * t), sigma)
    return (up - down) / (2.0 * h)


@pytest.mark.parametrize("kind", ["C", "P"])
@pytest.mark.parametrize("case", CASES)
def test_delta_contra_diferenca_finita(kind: str, case: tuple[float, ...]) -> None:
    F, K, t, df, sigma = case
    greeks = greeks_black76(kind, F, K, t, df, sigma, quote_factor=1)
    assert greeks.delta == pytest.approx(fd_delta(kind, F, K, t, df, sigma), rel=1e-3)


@pytest.mark.parametrize("kind", ["C", "P"])
@pytest.mark.parametrize("case", CASES)
def test_gamma_contra_diferenca_finita(kind: str, case: tuple[float, ...]) -> None:
    F, K, t, df, sigma = case
    greeks = greeks_black76(kind, F, K, t, df, sigma)
    assert greeks.gamma > 0.0
    assert greeks.gamma == pytest.approx(fd_gamma(kind, F, K, t, df, sigma), rel=1e-3)


@pytest.mark.parametrize("kind", ["C", "P"])
@pytest.mark.parametrize("case", CASES)
def test_vega_contra_diferenca_finita(kind: str, case: tuple[float, ...]) -> None:
    F, K, t, df, sigma = case
    greeks = greeks_black76(kind, F, K, t, df, sigma)
    # vega_brl é por 1 ponto percentual: a derivada vezes 0,01
    assert greeks.vega_brl == pytest.approx(0.01 * fd_vega(kind, F, K, t, df, sigma), rel=1e-3)


@pytest.mark.parametrize("kind", ["C", "P"])
@pytest.mark.parametrize("case", CASES)
def test_theta_contra_diferenca_finita(kind: str, case: tuple[float, ...]) -> None:
    F, K, t, df, sigma = case
    greeks = greeks_black76(kind, F, K, t, df, sigma)
    # theta_brl é por DIA ÚTIL: Θ anual ÷ 252
    assert greeks.theta_brl == pytest.approx(
        fd_theta(kind, F, K, t, df, sigma) / SESSIONS_PER_YEAR, rel=1e-3
    )


@pytest.mark.parametrize("kind", ["C", "P"])
@pytest.mark.parametrize("case", CASES)
def test_rho_contra_diferenca_finita(kind: str, case: tuple[float, ...]) -> None:
    F, K, t, df, sigma = case
    greeks = greeks_black76(kind, F, K, t, df, sigma)
    # rho é por 1 ponto percentual de taxa (a derivada em r vezes 0,01)
    assert greeks.rho == pytest.approx(0.01 * fd_rho(kind, F, K, t, df, sigma), rel=1e-3)


def test_delta_de_put_e_negativo_e_a_diferenca_e_o_df() -> None:
    """Paridade derivada: `Δcall − Δput = DF` (identidade exata do Black-76)."""
    for F, K, t, df, sigma in CASES:
        call = greeks_black76("C", F, K, t, df, sigma)
        put = greeks_black76("P", F, K, t, df, sigma)
        assert call.delta > 0.0
        assert put.delta < 0.0
        assert call.delta - put.delta == pytest.approx(df, rel=1e-12)


def test_vega_e_igual_para_call_e_put() -> None:
    for F, K, t, df, sigma in CASES:
        call = greeks_black76("C", F, K, t, df, sigma)
        put = greeks_black76("P", F, K, t, df, sigma)
        assert call.vega_brl == pytest.approx(put.vega_brl, rel=1e-14)


def test_theta_pct_e_theta_por_premio() -> None:
    for F, K, t, df, sigma in CASES:
        call = greeks_black76("C", F, K, t, df, sigma)
        premium = black76_call_price(F, K, t, df, sigma)
        assert call.theta_pct == pytest.approx(call.theta_brl / premium * 100.0, rel=1e-12)
        put = greeks_black76("P", F, K, t, df, sigma)
        put_premium = black76_put_price(F, K, t, df, sigma)
        assert put.theta_pct == pytest.approx(put.theta_brl / put_premium * 100.0, rel=1e-12)


def test_theta_de_call_atm_e_negativo() -> None:
    greeks = greeks_black76("C", 100.0, 100.0, 0.25, 0.98, 0.3)
    assert greeks.theta_brl < 0.0
    assert greeks.theta_pct is not None and greeks.theta_pct < 0.0


def test_theta_de_put_profunda_itm_pode_ser_positivo() -> None:
    """Carrego do juro: put deep ITM ganha com o tempo (r·P domina o decay)."""
    greeks = greeks_black76("P", 50.0, 200.0, 1.0, 0.87, 0.2)
    assert greeks.theta_brl > 0.0


def test_quote_factor_divide_as_grega_de_preco() -> None:
    """`preço por ação = preço ÷ FATCOT`: só as gregas de dinheiro mudam."""
    base = greeks_black76("C", 100.0, 100.0, 0.25, 0.98, 0.3, quote_factor=1)
    factored = greeks_black76("C", 100.0, 100.0, 0.25, 0.98, 0.3, quote_factor=100)
    assert factored.theta_brl == pytest.approx(base.theta_brl / 100.0, rel=1e-14)
    assert factored.vega_brl == pytest.approx(base.vega_brl / 100.0, rel=1e-14)
    assert factored.rho == pytest.approx(base.rho / 100.0, rel=1e-14)
    assert factored.delta == base.delta
    assert factored.gamma == base.gamma
    assert factored.theta_pct == pytest.approx(base.theta_pct, rel=1e-14)


def test_limites_de_delta() -> None:
    deep_call = greeks_black76("C", 1000.0, 100.0, 0.1, 0.99, 0.1)
    assert deep_call.delta == pytest.approx(0.99, rel=1e-6)  # DF
    assert deep_call.gamma < 1e-8
    deep_put = greeks_black76("P", 100.0, 1000.0, 0.1, 0.99, 0.1)
    assert deep_put.delta == pytest.approx(-0.99, rel=1e-6)


def test_dominio_invalido_levanta() -> None:
    with pytest.raises(ValueError, match="kind"):
        greeks_black76("X", 100.0, 100.0, 0.25, 0.98, 0.3)
    with pytest.raises(ValueError, match="t="):
        greeks_black76("C", 100.0, 100.0, 0.0, 1.0, 0.3)
    with pytest.raises(ValueError, match="sigma="):
        greeks_black76("C", 100.0, 100.0, 0.25, 0.98, 0.0)
    with pytest.raises(ValueError, match="quote_factor"):
        greeks_black76("C", 100.0, 100.0, 0.25, 0.98, 0.3, quote_factor=0)
    with pytest.raises(ValueError, match="positivos"):
        greeks_black76("C", 0.0, 100.0, 0.25, 0.98, 0.3)


def test_greeks_e_dataclass_frozen() -> None:
    greeks = greeks_black76("C", 100.0, 100.0, 0.25, 0.98, 0.3)
    assert isinstance(greeks, Greeks)
    with pytest.raises(AttributeError):
        greeks.delta = 1.0  # type: ignore[misc]
