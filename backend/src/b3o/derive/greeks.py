"""Gregas de Black-76 em unidade interpretável (F1.4, spec §5.4/§6.0) — PURO.

Com `V` = preço Black-76, `φ`/`N` = densidade/CDF normal padrão, `d1`, `d2` como em
`iv.py` e `DF = e^{−rT}` (a taxa contínua sai do próprio DF: `r = −ln(DF)/T`, a mesma
que o `DF` da curva implica — não se passa taxa por fora):

    delta_call = DF·N(d1)                  delta_put = −DF·N(−d1)
    gamma      = DF·φ(d1) / (F·σ·√T)       (= ∂²V/∂F²; F é o proxy do spot da Onda D)
    vega       = DF·F·φ(d1)·√T             (∂V/∂σ, por 1.00 de σ)
    theta      = r·V − DF·F·φ(d1)·σ/(2√T)  (= −∂V/∂T, anual)
    rho        = −T·V                      (= ∂V/∂r, com F fixo)

As duas identidades usadas acima são exatas no Black-76: `F·φ(d1) = K·φ(d2)` (daí o
termo único de theta/vega) e `∂d1/∂T = ∂d2/∂T + σ/(2√T)`. Fonte da forma fechada:
Black (1976); a mesma tabela em Haug (2007), cap. "Black-76" — e o teste confere cada
grega contra **diferença finita independente**, não contra a fórmula.

Unidades (contrato Onda D §1, `docs/glossario.md`):
- `delta`: por ação, adimensional, call `+`, put `−`;
- `gamma`: variação de delta por unidade de `F`, na moeda de `F` (se `F` é centavos,
  γ sai "por centavo"); a fronteira em `metrics/series.py` multiplica por 100 para
  gravar a variação do delta por ação para cada R$1 de variação de F/forward;
- `theta_brl`: R$ (ou ¢, a moeda de `F`) **por dia útil** — Θ anual ÷ 252,
  por ação (÷ `quote_factor`, porque `preço por ação = preço ÷ FATCOT`);
- `theta_pct`: % do prêmio **por dia útil** (`theta_brl / prêmio × 100`);
- `vega_brl`: R$ por ação por **1 ponto** de vol (σ 25% → 26%: derivada × 0,01),
  já dividido pelo `quote_factor`;
- `rho`: R$ por ação por **1 ponto percentual** de taxa (derivada × 0,01), ÷ `quote_factor`.

`t == 0` não tem grega definida (delta é degrau, gamma é infinito): levanta `ValueError`
— na prática a IV dessa série é `None` (`iv_expired`) e a linha não chega aqui.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from b3o.derive.conventions import norm_cdf, norm_pdf

SESSIONS_PER_YEAR = 252
"""Θ por **dia útil**: Θ anual ÷ 252 (spec §6.0)."""


@dataclass(frozen=True, slots=True)
class Greeks:
    """As seis gregas em unidade interpretável (cada campo diz a sua no docstring)."""

    delta: float
    """Por ação; call `+`, put `−` (nunca `|delta|`)."""

    gamma: float
    """∂²V/∂F² por ação, na moeda de `F` (se `F` é centavos, γ é por centavo;
    a fronteira de métricas converte centavo→por real multiplicando por 100)."""

    theta_brl: float
    """Θ por **dia útil**, por ação, na moeda de `F` (÷ `quote_factor`)."""

    theta_pct: float | None
    """Θ em % do prêmio por dia útil; `None` quando o prêmio é `<= 0` (não há % de zero)."""

    vega_brl: float
    """ν por ação por **1 ponto** de vol, na moeda de `F` (÷ `quote_factor`)."""

    rho: float
    """ρ por ação por **1 ponto percentual** de taxa, na moeda de `F` (÷ `quote_factor`)."""


def greeks_black76(
    kind: Literal["C", "P"],
    F: float,
    K: float,
    t: float,
    df: float,
    sigma: float,
    *,
    quote_factor: int = 1,
) -> Greeks:
    """Gregas de uma call (`kind="C"`) ou put (`kind="P"`) europeia sobre o forward.

    `quote_factor` é o `FATCOT` da série (1 na maioria, 100 em parte das opções):
    as gregas de preço (`theta_brl`, `vega_brl`, `rho`) saem por ação dividindo por
    ele; `delta` é adimensional e `gamma` fica na unidade de `F`.
    """
    _validate(kind, F, K, t, df, sigma, quote_factor)

    sqrt_t = math.sqrt(t)
    vol_t = sigma * sqrt_t
    log_moneyness = math.log(F / K)
    d1 = (log_moneyness + 0.5 * vol_t * vol_t) / vol_t
    d2 = d1 - vol_t
    cdf_d1 = norm_cdf(d1)
    cdf_d2 = norm_cdf(d2)
    pdf_d1 = norm_pdf(d1)
    discounted_forward = df * F
    decay = discounted_forward * pdf_d1 * sigma / (2.0 * sqrt_t)  # −∂[DF·F·(termo)]/∂T
    rate = -math.log(df) / t  # taxa contínua implicada pelo próprio DF

    if kind == "C":
        price = df * (F * cdf_d1 - K * cdf_d2)
        delta = df * cdf_d1
    else:
        price = df * (K * norm_cdf(-d2) - F * norm_cdf(-d1))
        delta = -df * norm_cdf(-d1)

    theta_annual = rate * price - decay
    factor = float(quote_factor)
    theta_brl = theta_annual / SESSIONS_PER_YEAR / factor
    vega_brl = discounted_forward * pdf_d1 * sqrt_t * 0.01 / factor
    rho = -t * price * 0.01 / factor
    gamma = discounted_forward * pdf_d1 / (F * F * vol_t)
    theta_pct = (theta_annual / SESSIONS_PER_YEAR / price) * 100.0 if price > 0.0 else None

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta_brl=theta_brl,
        theta_pct=theta_pct,
        vega_brl=vega_brl,
        rho=rho,
    )


def _validate(
    kind: str,
    F: float,
    K: float,
    t: float,
    df: float,
    sigma: float,
    quote_factor: int,
) -> None:
    """Entradas válidas e domínio em que a grega existe (t > 0, σ > 0)."""
    if kind not in ("C", "P"):
        raise ValueError(f"kind precisa ser 'C' ou 'P': {kind!r}")
    if not all(math.isfinite(value) for value in (F, K, t, df, sigma)):
        raise ValueError(f"entrada não finita: F={F}, K={K}, t={t}, df={df}, sigma={sigma}")
    if F <= 0.0 or K <= 0.0:
        raise ValueError(f"F e K precisam ser positivos: F={F}, K={K}")
    if df <= 0.0:
        raise ValueError(f"DF precisa ser positivo: {df}")
    if t <= 0.0:
        raise ValueError(f"gregas não existem em t={t} (vencimento no fechamento: só intrínseco)")
    if sigma <= 0.0:
        raise ValueError(f"gregas não existem em sigma={sigma}")
    if quote_factor <= 0:
        raise ValueError(f"quote_factor precisa ser positivo: {quote_factor}")
