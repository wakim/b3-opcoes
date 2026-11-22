"""Black-76 e inversão de IV (F1.3) — valor de referência calculado à mão e quadratura.

Referências usadas aqui (nenhuma delas vem de `b3o`):

1. **Casos ATM exatos.** Com `F = K` e `σ√T = v`, Black-76 dá
   `C = DF·F·(2Φ(v/2) − 1)`. Em `v = 2` o fator é `2Φ(1) − 1 = 0,6826894921370859`
   (a "regra dos 68,27%"; `Φ(1) = 0,8413447460685429`, tabela normal padrão em
   Abramowitz & Stegun, cap. 26, e em Haug 2007, apêndice de tabelas). Com `F = K = 100`
   e `DF = 1`: `C = 68,26894921370859` — valor escrito à mão, não gerado.
2. **Caso Hull/Haug.** `F = K = 100`, `T = 1`, `r = 5%`, `σ = 20%`: `d1 = 0,1`,
   `d2 = −0,1`, `Φ(0,1) = 0,5398278373`, `Φ(−0,1) = 0,4601721627`,
   `e^{−0,05} = 0,9512294245` → `C = 0,9512294245 · 100 · 0,0796556746 ≈ 7,57708`.
   (A forma fechada é a de Black 1976; a tabela é a mesma de Haug 2007.)
3. **Quadratura independente** da esperança lognormal,
   `C = DF·E[(F·e^{−v²/2 + vZ} − K)⁺]`, por Simpson com 8 000 pontos — valida o termo
   de moneyness sem usar o código sob teste.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from b3o.derive.iv import (
    IV_MAX,
    IV_MIN,
    black76_call_price,
    black76_put_price,
    implied_vol,
    implied_vol_detail,
    implied_vol_many,
)

SQRT_2PI = math.sqrt(2.0 * math.pi)
PHI_1 = 0.8413447460685429
"""`Φ(1)`, tabela normal padrão (Abramowitz & Stegun 26.2.19; Haug 2007, apêndices)."""


def test_call_atm_valor_de_referencia() -> None:
    """`v = σ√T = 2` → `C = 100 · (2Φ(1) − 1) = 68,26894921370859` (df = 1)."""
    price = black76_call_price(F=100.0, K=100.0, t=1.0, df=1.0, sigma=2.0)
    assert price == pytest.approx(100.0 * (2.0 * PHI_1 - 1.0), rel=1e-12)
    assert price == pytest.approx(68.26894921370859, rel=1e-12)


def test_call_atm_com_desconto_valor_de_mao() -> None:
    """Hull/Haug: F = K = 100, T = 1, σ = 20%, r = 5% → C ≈ 7,5771 (conta à mão)."""
    df = math.exp(-0.05)
    price = black76_call_price(F=100.0, K=100.0, t=1.0, df=df, sigma=0.2)
    assert price == pytest.approx(7.57708, abs=1e-5)
    put = black76_put_price(F=100.0, K=100.0, t=1.0, df=df, sigma=0.2)
    assert put == pytest.approx(price, rel=1e-12)  # ATM: put = call


def test_put_mais_call_e_o_desconto_do_forward_menos_strike() -> None:
    """Paridade: `C − P = DF·(F − K)` é exata em Black-76 (independe de modelo)."""
    cases = [
        (100.0, 100.0, 0.5, 0.97, 0.35),
        (100.0, 80.0, 1.0, 0.93, 0.25),
        (100.0, 130.0, 0.08, 0.99, 0.8),
        (50.0, 50.0, 2.0, 0.8, 0.15),
    ]
    for F, K, t, df, sigma in cases:
        call = black76_call_price(F, K, t, df, sigma)
        put = black76_put_price(F, K, t, df, sigma)
        expected = df * (F - K)
        assert call - put == pytest.approx(expected, abs=1e-12 * max(1.0, abs(expected)))
        assert put == pytest.approx(call - expected, abs=1e-12 * max(1.0, abs(expected)))


def test_limite_sigma_zero_e_intrinseco_descontado() -> None:
    assert black76_call_price(110.0, 100.0, 0.5, 0.97, 0.0) == 0.97 * 10.0
    assert black76_call_price(90.0, 100.0, 0.5, 0.97, 0.0) == 0.0
    assert black76_put_price(90.0, 100.0, 0.5, 0.97, 0.0) == 0.97 * 10.0
    assert black76_put_price(110.0, 100.0, 0.5, 0.97, 0.0) == 0.0


def test_t_zero_e_intrinseco() -> None:
    """Vence hoje: só intrínseco (`DF = 1` é a única chamada coerente)."""
    assert black76_call_price(110.0, 100.0, 0.0, 1.0, 0.3) == 10.0
    assert black76_put_price(90.0, 100.0, 0.0, 1.0, 0.3) == 10.0
    assert black76_call_price(90.0, 100.0, 0.0, 1.0, 0.3) == 0.0


def test_monotonicidade_em_sigma_e_em_F() -> None:
    grid = np.linspace(0.01, 3.0, 60)
    calls = [black76_call_price(100.0, 105.0, 0.25, 0.98, float(s)) for s in grid]
    puts = [black76_put_price(100.0, 95.0, 0.25, 0.98, float(s)) for s in grid]
    assert all(left < right for left, right in zip(calls, calls[1:], strict=False))
    assert all(left < right for left, right in zip(puts, puts[1:], strict=False))
    forwards = np.linspace(60.0, 160.0, 50)
    call_in_f = [black76_call_price(float(f), 100.0, 0.5, 0.97, 0.3) for f in forwards]
    put_in_f = [black76_put_price(float(f), 100.0, 0.5, 0.97, 0.3) for f in forwards]
    assert all(left < right for left, right in zip(call_in_f, call_in_f[1:], strict=False))
    assert all(left > right for left, right in zip(put_in_f, put_in_f[1:], strict=False))


def test_limites_de_arbitragem() -> None:
    """`0 <= C <= DF·F`, `0 <= P <= DF·K`; σ grande leva o preço ao teto."""
    F, K, t, df = 100.0, 95.0, 0.5, 0.98
    for sigma in (0.05, 0.3, 1.0, 3.0, 10.0):
        call = black76_call_price(F, K, t, df, sigma)
        put = black76_put_price(F, K, t, df, sigma)
        assert df * max(F - K, 0.0) <= call <= df * F
        assert df * max(K - F, 0.0) <= put <= df * K
    huge = black76_call_price(F, K, t, df, 20.0)
    assert huge == pytest.approx(df * F, rel=1e-6)


def _lognormal_call(F: float, K: float, t: float, df: float, sigma: float) -> float:
    """`DF·E[(F·e^{−v²/2 + vZ} − K)⁺]` por Simpson — independente de Black-76."""
    v = sigma * math.sqrt(t)
    low, high, points = -9.0, 9.0, 8000
    step = (high - low) / points

    def integrand(z: float) -> float:
        terminal = F * math.exp(-0.5 * v * v + v * z)
        density = math.exp(-0.5 * z * z) / SQRT_2PI
        return max(terminal - K, 0.0) * density

    total = integrand(low) + integrand(high)
    for index in range(1, points):
        weight = 4.0 if index % 2 else 2.0
        total += weight * integrand(low + index * step)
    return df * total * step / 3.0


def test_preco_bate_com_quadratura_da_esperança_lognormal() -> None:
    cases = [
        (100.0, 100.0, 0.25, 0.99, 0.3),
        (100.0, 90.0, 0.5, 0.98, 0.25),
        (100.0, 115.0, 0.5, 0.98, 0.25),
        (100.0, 115.0, 0.05, 1.0, 0.6),
        (4300.0, 4400.0, 0.08, 0.989, 0.33),
    ]
    for F, K, t, df, sigma in cases:
        model = black76_call_price(F, K, t, df, sigma)
        integral = _lognormal_call(F, K, t, df, sigma)
        assert model == pytest.approx(integral, rel=1e-6)


def test_entradas_invalidas_levantam() -> None:
    with pytest.raises(ValueError, match="positivos"):
        black76_call_price(0.0, 100.0, 0.5, 0.98, 0.2)
    with pytest.raises(ValueError, match="t negativo"):
        black76_call_price(100.0, 100.0, -0.5, 0.98, 0.2)
    with pytest.raises(ValueError, match="DF"):
        black76_put_price(100.0, 100.0, 0.5, 0.0, 0.2)
    with pytest.raises(ValueError, match="sigma negativo"):
        black76_put_price(100.0, 100.0, 0.5, 0.98, -0.1)


def test_inversao_no_par_de_mao() -> None:
    """O preço ATM de 68,26894921370859 (σ = 2, v = 2) volta a σ = 2."""
    sigma = implied_vol(68.26894921370859, 100.0, 100.0, 1.0, 1.0, is_call=True)
    assert sigma == pytest.approx(2.0, rel=1e-9)


def test_ida_e_volta_preco_iv_preco() -> None:
    """σ conhecido → preço → IV → σ, a 1e-6 relativo (contrato §1)."""
    for F, K, t, df in ((4300.0, 4400.0, 0.08, 0.989), (100.0, 80.0, 1.0, 0.93)):
        for sigma in (0.05, 0.15, 0.3, 0.6, 1.2):
            for is_call in (True, False):
                price_fn = black76_call_price if is_call else black76_put_price
                price = price_fn(F, K, t, df, sigma)
                if price <= 1e-12:
                    continue
                found = implied_vol(price, F, K, t, df, is_call=is_call)
                assert found is not None
                assert found == pytest.approx(sigma, rel=1e-6)
                assert price_fn(F, K, t, df, found) == pytest.approx(price, rel=1e-6)


def test_iv_de_t_zero_e_none_com_motivo() -> None:
    assert implied_vol(10.0, 110.0, 100.0, 0.0, 1.0, is_call=True) is None
    assert implied_vol_detail(10.0, 110.0, 100.0, 0.0, 1.0, is_call=True) == (
        None,
        "iv_expired",
    )


def test_preco_abaixo_do_intrinseco_e_inconsistente() -> None:
    # call ITM com intrínseco descontado 0,99·10 = 9,9: preço 9,0 é impossível
    assert implied_vol_detail(9.0, 110.0, 100.0, 0.5, 0.99, is_call=True) == (
        None,
        "iv_inconsistent_input",
    )
    assert implied_vol(9.0, 110.0, 100.0, 0.5, 0.99, is_call=True) is None


def test_preco_acima_do_teto_e_inconsistente() -> None:
    # call nunca vale mais que DF·F = 0,99·100 = 99
    assert implied_vol_detail(120.0, 100.0, 50.0, 0.5, 0.99, is_call=True) == (
        None,
        "iv_inconsistent_input",
    )
    assert implied_vol_detail(-1.0, 100.0, 50.0, 0.5, 0.99, is_call=True)[1] == (
        "iv_inconsistent_input"
    )
    assert implied_vol_detail(1.0, 0.0, 50.0, 0.5, 0.99, is_call=True)[1] == (
        "iv_inconsistent_input"
    )


def test_preco_fora_da_faixa_de_busca() -> None:
    """Preço legítimo, mas a σ que o explica está acima do `hi` pedido."""
    price = black76_call_price(100.0, 100.0, 0.25, 0.98, 1.5)
    assert implied_vol_detail(price, 100.0, 100.0, 0.25, 0.98, is_call=True, hi=1.0) == (
        None,
        "iv_out_of_range",
    )
    found = implied_vol(price, 100.0, 100.0, 0.25, 0.98, is_call=True)
    assert found == pytest.approx(1.5, rel=1e-6)


def test_preco_no_intrinseco_devolve_o_piso() -> None:
    """Preço exatamente no intrínseco: a raiz está no piso da grade (`lo`)."""
    sigma, reason = implied_vol_detail(10.0, 110.0, 100.0, 0.5, 1.0, is_call=True)
    assert reason is None
    assert sigma == IV_MIN


def test_lote_igual_ao_escalar() -> None:
    call_prices, put_prices = [], []
    forwards, strikes, times, dfs = [], [], [], []
    for F, K, t, df in ((4300.0, 4200.0, 0.08, 0.989), (100.0, 100.0, 0.5, 0.98)):
        for sigma in (0.1, 0.25, 0.7, 1.4):
            call_prices.append(black76_call_price(F, K, t, df, sigma))
            put_prices.append(black76_put_price(F, K, t, df, sigma))
            forwards.append(F)
            strikes.append(K)
            times.append(t)
            dfs.append(df)

    expected_calls = [
        implied_vol(price, F, K, t, df, is_call=True)
        for price, F, K, t, df in zip(call_prices, forwards, strikes, times, dfs, strict=True)
    ]
    expected_puts = [
        implied_vol(price, F, K, t, df, is_call=False)
        for price, F, K, t, df in zip(put_prices, forwards, strikes, times, dfs, strict=True)
    ]
    calls = implied_vol_many(call_prices, forwards, strikes, times, dfs, is_call=True)
    puts = implied_vol_many(put_prices, forwards, strikes, times, dfs, is_call=False)
    for got, want in zip(calls.tolist(), expected_calls, strict=True):
        assert got == pytest.approx(want, rel=1e-9)
    for got, want in zip(puts.tolist(), expected_puts, strict=True):
        assert got == pytest.approx(want, rel=1e-9)


def test_lote_marca_nan_nos_invalidos() -> None:
    result = implied_vol_many(
        [10.0, 0.0, -1.0, 5.0],
        [110.0, 100.0, 100.0, 100.0],
        [100.0, 100.0, 100.0, 100.0],
        [0.5, 0.5, 0.5, 0.0],
        [0.99, 0.99, 0.99, 0.99],
        is_call=True,
    )
    assert result.shape == (4,)
    assert math.isnan(result[1])  # preço zero
    assert math.isnan(result[2])  # preço negativo
    assert math.isnan(result[3])  # t = 0 → iv_expired
    assert not math.isnan(result[0])


def test_lote_aceita_escalares_e_broadcast() -> None:
    price = black76_call_price(100.0, 100.0, 0.25, 0.98, 0.3)
    result = implied_vol_many([price, price], 100.0, 100.0, 0.25, 0.98, is_call=True)
    assert result.shape == (2,)
    assert result[0] == pytest.approx(0.3, rel=1e-9)


def test_faixa_de_busca_padrao() -> None:
    assert IV_MIN == 1e-4
    assert IV_MAX == 5.0
