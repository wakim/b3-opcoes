"""Black-76 e inversão de IV (F1.3, spec §6.3) — PURO.

Black-76 sobre `(F, K, T, DF, σ)`::

    d1 = (ln(F/K) + σ²T/2) / (σ√T)      d2 = d1 − σ√T
    call = DF·(F·N(d1) − K·N(d2))        put = DF·(K·N(−d2) − F·N(−d1))

Unidades: `F`, `K` e o preço na **mesma** moeda (centavos, no pipeline), `t` em anos
(dias úteis/252, `conventions.year_fraction`), `df` adimensional e `σ` em **fração**
(0.25 = 25% a.a.); a serialização para % é do C1. `t == 0` devolve o intrínseco
descontado — com `df = 1` é o intrínseco puro, a única chamada coerente.

Inversão (ADR-004: sem scipy)
-----------------------------
Brent próprio em `[1e-4, 5]` (a função preço é monótona crescente em σ, então o
intervalo sempre bracketa a raiz quando ela existe). Toda IV achada é **conferida**:
o preço é recalculado com ela e a diferença relativa tem que ficar abaixo de `1e-6`;
se não ficar, o resultado é `None` — nunca um número que não reproduz o preço.

Falhas explícitas (regra 5 do AGENTS), devolvidas por `implied_vol_detail`:

- `iv_expired` — `t <= 0`: vencimento no fechamento, só intrínseco; não se extrapola.
- `iv_inconsistent_input` — preço abaixo do intrínseco descontado, acima do teto
  (`DF·F` para call, `DF·K` para put) ou entrada não finita/negativa.
- `iv_out_of_range` — preço legítimo, mas fora do que `[lo, hi]` alcança (σ pedida
  abaixo de `1e-4` ou acima de `5`).

`implied_vol` é o contrato (σ ou `None`); `implied_vol_detail` leva o motivo junto
para quem precisa gravar `flags[]`. `implied_vol_many` é a versão de lote (numpy),
mesma semântica, com `NaN` no lugar de `None`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from b3o.derive.conventions import norm_cdf

IV_MIN = 1e-4
"""Piso da busca de σ (0,01% a.a.): abaixo disso o preço é intrinsicamente indistinguível."""

IV_MAX = 5.0
"""Teto da busca de σ (500% a.a.): IV acima disso não é mercado, é dado quebrado."""

PRICE_TOL = 1e-9
"""Tolerância relativa da checagem de no-arbitrage (só absorve ruído de float)."""

SANITY_RTOL = 1e-6
"""Tolerância da conferência preço → IV → preço (contrato §1)."""

_EXPIRED = "iv_expired"
_INCONSISTENT = "iv_inconsistent_input"
_OUT_OF_RANGE = "iv_out_of_range"

_INV_SQRT_2 = 1.0 / math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)
_EPS = 2.220446049250313e-16


def black76_call_price(F: float, K: float, t: float, df: float, sigma: float) -> float:
    """Preço de call europeia sobre o forward, por ação, na moeda de `F` e `K`."""
    _validate(F, K, t, df, sigma)
    if t == 0.0 or sigma == 0.0:
        return df * max(F - K, 0.0)
    sqrt_t = math.sqrt(t)
    vol_t = sigma * sqrt_t
    d1 = (math.log(F / K) + 0.5 * vol_t * vol_t) / vol_t
    d2 = d1 - vol_t
    return df * (F * norm_cdf(d1) - K * norm_cdf(d2))


def black76_put_price(F: float, K: float, t: float, df: float, sigma: float) -> float:
    """Preço de put europeia sobre o forward, por ação, na moeda de `F` e `K`."""
    _validate(F, K, t, df, sigma)
    if t == 0.0 or sigma == 0.0:
        return df * max(K - F, 0.0)
    sqrt_t = math.sqrt(t)
    vol_t = sigma * sqrt_t
    d1 = (math.log(F / K) + 0.5 * vol_t * vol_t) / vol_t
    d2 = d1 - vol_t
    return df * (K * norm_cdf(-d2) - F * norm_cdf(-d1))


def implied_vol(
    price: float,
    F: float,
    K: float,
    t: float,
    df: float,
    *,
    is_call: bool,
    lo: float = IV_MIN,
    hi: float = IV_MAX,
) -> float | None:
    """Volatilidade implícita em fração (0.25 = 25% a.a.), ou `None` se não há raiz.

    Use `implied_vol_detail` quando o motivo da falha importa (é o caso do pipeline,
    que grava `flags[]`).
    """
    return implied_vol_detail(price, F, K, t, df, is_call=is_call, lo=lo, hi=hi)[0]


def implied_vol_detail(
    price: float,
    F: float,
    K: float,
    t: float,
    df: float,
    *,
    is_call: bool,
    lo: float = IV_MIN,
    hi: float = IV_MAX,
) -> tuple[float | None, str | None]:
    """IV e o motivo (`None` se deu certo): `iv_expired`, `iv_inconsistent_input` ou
    `iv_out_of_range`."""
    if not (math.isfinite(price) and math.isfinite(F) and math.isfinite(K)):
        return None, _INCONSISTENT
    if not (math.isfinite(t) and math.isfinite(df)):
        return None, _INCONSISTENT
    if t <= 0.0:
        return None, _EXPIRED  # vence hoje: só intrínseco, não se extrapola tempo
    if F <= 0.0 or K <= 0.0 or df <= 0.0 or price <= 0.0:
        return None, _INCONSISTENT
    if not (0.0 < lo < hi):
        raise ValueError(f"intervalo de busca inválido: lo={lo}, hi={hi}")

    lower, upper = _no_arbitrage_bounds(F, K, df, is_call)
    tolerance = PRICE_TOL * max(1.0, upper)
    if price < lower - tolerance or price > upper + tolerance:
        return None, _INCONSISTENT

    price_at = _price_function(is_call, F, K, t, df)
    at_lo = price_at(lo) - price
    at_hi = price_at(hi) - price
    if at_lo > 0.0 or at_hi < 0.0:
        return None, _OUT_OF_RANGE
    if at_lo == 0.0:
        sigma = lo
    elif at_hi == 0.0:
        sigma = hi
    else:
        sigma = _brent(lambda value: price_at(value) - price, lo, hi, at_lo, at_hi)

    if abs(price_at(sigma) - price) > SANITY_RTOL * price:
        return None, _INCONSISTENT  # a raiz não reproduz o preço: não é raiz
    return sigma, None


def implied_vol_many(
    prices: Sequence[float] | np.ndarray,
    forwards: Sequence[float] | np.ndarray,
    strikes: Sequence[float] | np.ndarray,
    times: Sequence[float] | np.ndarray,
    dfs: Sequence[float] | np.ndarray,
    *,
    is_call: bool,
    lo: float = IV_MIN,
    hi: float = IV_MAX,
) -> np.ndarray:
    """Lote de IVs — numpy, mesma semântica de `implied_vol` (`NaN` no lugar de `None`).

    O preço em lote é avaliado de forma vetorizada; a CDF normal usa `math.erf`
    elemento a elemento (o numpy só tem `erf` via scipy — ADR-004). O refinamento é
    Newton salvaguardado pelo bracket `[lo, hi]`: mesma raiz do Brent escalar, e o
    teste compara os dois caminhos.
    """
    arrays = np.broadcast_arrays(
        np.asarray(prices, dtype=float),
        np.asarray(forwards, dtype=float),
        np.asarray(strikes, dtype=float),
        np.asarray(times, dtype=float),
        np.asarray(dfs, dtype=float),
    )
    price, forward, strike, time, df = (array.reshape(-1) for array in arrays)
    result = np.full(price.shape, np.nan, dtype=float)

    valid = (
        np.isfinite(price)
        & np.isfinite(forward)
        & np.isfinite(strike)
        & np.isfinite(time)
        & np.isfinite(df)
        & (price > 0.0)
        & (forward > 0.0)
        & (strike > 0.0)
        & (time > 0.0)
        & (df > 0.0)
    )
    if not valid.any():
        return result.reshape(arrays[0].shape)

    lower, upper = _bounds_arrays(forward[valid], strike[valid], df[valid], is_call=is_call)
    tolerance = PRICE_TOL * np.maximum(1.0, upper)
    price_v = price[valid]
    inside = (price_v >= lower - tolerance) & (price_v <= upper + tolerance)
    selected = np.flatnonzero(valid)[inside]
    if selected.size == 0:
        return result.reshape(arrays[0].shape)

    sigma = _invert_batch(
        price[selected],
        forward[selected],
        strike[selected],
        time[selected],
        df[selected],
        is_call=is_call,
        lo=lo,
        hi=hi,
    )
    # Conferência preço→IV→preço, igual à escalar (lote e escalar não podem divergir).
    checked = _price_array(
        forward[selected], strike[selected], time[selected], df[selected], sigma, is_call=is_call
    )
    ok = np.isfinite(sigma) & (np.abs(checked - price[selected]) <= SANITY_RTOL * price[selected])
    result[selected[ok]] = sigma[ok]
    return result.reshape(arrays[0].shape)


def _no_arbitrage_bounds(F: float, K: float, df: float, is_call: bool) -> tuple[float, float]:
    """Faixa de preço que ainda pode ter raiz em σ: `[intrínseco descontado, teto σ→∞]`."""
    if is_call:
        return df * max(F - K, 0.0), df * F
    return df * max(K - F, 0.0), df * K


def _bounds_arrays(
    F: np.ndarray, K: np.ndarray, df: np.ndarray, *, is_call: bool
) -> tuple[np.ndarray, np.ndarray]:
    """`_no_arbitrage_bounds` vetorizado (`t` não entra: intrínseco é o limite σ→0)."""
    if is_call:
        return df * np.maximum(F - K, 0.0), df * F
    return df * np.maximum(K - F, 0.0), df * K


def _price_function(is_call: bool, F: float, K: float, t: float, df: float):
    """Fecha o preço numa função de σ só (o formato que o Brent quer)."""
    if is_call:
        return lambda sigma: black76_call_price(F, K, t, df, sigma)
    return lambda sigma: black76_put_price(F, K, t, df, sigma)


def _brent(f, a: float, b: float, fa: float, fb: float, *, maxiter: int = 200) -> float:
    """Brent (raiz isolada em `[a, b]`, com `fa <= 0 <= fb`) — implementação própria.

    Segue o algoritmo clássico (Brent 1973; pseudocódigo de Numerical Recipes/Wikipedia):
    interpolação inversa quadrática com salvaguarda de bisseção. `xtol`/`rtol` apertados
    porque a conferência final exige `|Δpreço| < 1e-6` relativo.
    """
    if fa == 0.0:
        return a
    if fb == 0.0:
        return b
    if fa > 0.0 or fb < 0.0:
        raise ValueError("Brent exige f(a) <= 0 <= f(b)")
    xtol = 1e-14
    rtol = 1e-14

    c, fc = a, fa
    d = e = b - a
    for _ in range(maxiter):
        if (fb > 0.0) == (fc > 0.0):
            c, fc = a, fa
            d = e = b - a
        if abs(fc) < abs(fb):
            a, fa = b, fb
            b, fb = c, fc
            c, fc = a, fa
        tolerance = 2.0 * rtol * abs(b) + xtol
        midpoint = 0.5 * (c - b)
        if abs(midpoint) <= tolerance or fb == 0.0:
            return b
        if abs(e) < tolerance or abs(fa) <= abs(fb):
            d = e = midpoint  # bisseção
        else:
            s = fb / fa
            if a == c:
                p = 2.0 * midpoint * s
                q = 1.0 - s
            else:
                q = fa / fc
                r = fb / fc
                p = s * (2.0 * midpoint * q * (q - r) - (b - a) * (r - 1.0))
                q = (q - 1.0) * (r - 1.0) * (s - 1.0)
            if p > 0.0:
                q = -q
            else:
                p = -p
            if 2.0 * p < min(3.0 * midpoint * q - abs(tolerance * q), abs(e * q)):
                e = d
                d = p / q
            else:
                d = e = midpoint
        a, fa = b, fb
        if abs(d) > tolerance:
            b += d
        else:
            b += tolerance if midpoint > 0.0 else -tolerance
        fb = f(b)
    return b


def _invert_batch(
    price: np.ndarray,
    F: np.ndarray,
    K: np.ndarray,
    t: np.ndarray,
    df: np.ndarray,
    *,
    is_call: bool,
    lo: float,
    hi: float,
) -> np.ndarray:
    """IV vetorizada: bisseção salvaguardando um passo de Newton (mesma raiz do Brent)."""
    sqrt_t = np.sqrt(t)
    low = np.full(price.shape, lo, dtype=float)
    high = np.full(price.shape, hi, dtype=float)
    sigma = 0.5 * (lo + hi)
    target = 1e-12 * np.maximum(price, 1.0)

    for _ in range(200):
        current = _price_array(F, K, t, df, sigma, is_call=is_call)
        diff = current - price
        if np.all(np.abs(diff) <= target) or np.all(high - low <= 1e-13):
            break
        low = np.where(diff < 0.0, sigma, low)
        high = np.where(diff > 0.0, sigma, high)
        vega = df * F * _norm_pdf_array(_d1_array(F, K, t, sigma)) * sqrt_t
        with np.errstate(divide="ignore", invalid="ignore"):
            newton = sigma - diff / vega
        bracketed = np.isfinite(newton) & (newton > low) & (newton < high)
        sigma = np.where(bracketed, newton, 0.5 * (low + high))
    return sigma


def _d1_array(F: np.ndarray, K: np.ndarray, t: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """`d1` vetorizado (σ e t já validados > 0)."""
    sqrt_t = np.sqrt(t)
    vol_t = sigma * sqrt_t
    return (np.log(F / K) + 0.5 * vol_t * vol_t) / vol_t


def _price_array(
    F: np.ndarray,
    K: np.ndarray,
    t: np.ndarray,
    df: np.ndarray,
    sigma: np.ndarray,
    *,
    is_call: bool,
) -> np.ndarray:
    """Preço Black-76 vetorizado, com o limite `σ → 0` = intrínseco descontado."""
    intrinsic = df * np.maximum(F - K, 0.0) if is_call else df * np.maximum(K - F, 0.0)
    safe_sigma = np.where(sigma > 0.0, sigma, 1.0)
    d1 = _d1_array(F, K, t, safe_sigma)
    d2 = d1 - safe_sigma * np.sqrt(t)
    if is_call:
        price = df * (F * _norm_cdf_array(d1) - K * _norm_cdf_array(d2))
    else:
        price = df * (K * _norm_cdf_array(-d2) - F * _norm_cdf_array(-d1))
    return np.where(sigma > 0.0, price, intrinsic)


def _norm_cdf_array(x: np.ndarray) -> np.ndarray:
    """`norm_cdf` elemento a elemento (o numpy não traz `erf` sem scipy — ADR-004)."""
    values = [0.5 * (1.0 + math.erf(value * _INV_SQRT_2)) for value in x.reshape(-1).tolist()]
    return np.asarray(values, dtype=float).reshape(x.shape)


def _norm_pdf_array(x: np.ndarray) -> np.ndarray:
    """`φ(x)` vetorizado."""
    return _INV_SQRT_2PI * np.exp(-0.5 * x * x)


def _validate(F: float, K: float, t: float, df: float, sigma: float) -> None:
    """Entradas do modelo: só número utilizável entra (falha explícita, nunca zero mudo)."""
    if not (math.isfinite(F) and math.isfinite(K) and math.isfinite(t)):
        raise ValueError(f"entrada não finita: F={F}, K={K}, t={t}")
    if not (math.isfinite(df) and math.isfinite(sigma)):
        raise ValueError(f"entrada não finita: df={df}, sigma={sigma}")
    if F <= 0.0 or K <= 0.0:
        raise ValueError(f"F e K precisam ser positivos: F={F}, K={K}")
    if t < 0.0:
        raise ValueError(f"t negativo: {t}")
    if df <= 0.0:
        raise ValueError(f"DF precisa ser positivo: {df}")
    if sigma < 0.0:
        raise ValueError(f"sigma negativo: {sigma}")
