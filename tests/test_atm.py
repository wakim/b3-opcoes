"""ATM 30d (F1.5, spec §6.4) — funções puras, números conferidos à mão.

O que se prova: a IV em `K = F` sai da interpolação de **variância total** em
`ln(K/F)` (não da IV); `K == F` na grade devolve o ponto plantado exato; a
interpolação entre vencimentos bate com `w = w1 + (w2−w1)·(T−T1)/(T2−T1)`
calculada fora do módulo; e cada beco sem saída devolve `None` + a flag certa
(`extrapolated_blocked`/`iv_extrapolated`, `no_short_expiry`, `no_expiry`,
`sparse_chain`, `smile_outside_range`, `no_forward`).

A entrada é sempre sintética e o esperado nunca sai do módulo: sai da fórmula da
spec escrita à mão no teste.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date

import pytest

from b3o.derive.atm import (
    FLAG_INVALID_TENOR,
    FLAG_INVALID_VARIANCE,
    FLAG_IV_EXTRAPOLATED,
    FLAG_NO_EXPIRY,
    FLAG_NO_FORWARD,
    FLAG_NO_SHORT_EXPIRY,
    FLAG_SMILE_OUTSIDE_RANGE,
    FLAG_SPARSE_CHAIN,
    METHOD_EXTRAPOLATED_BLOCKED,
    METHOD_INTERPOLATED,
    METHOD_NONE,
    MIN_STRIKES,
    TARGET_T_30D,
    Atm30d,
    ExpiryAtm,
    SmilePoint,
    atm_by_expiry,
    atm_interpolate_30d,
)

FORWARD = 4000.0  # centavos (R$ 40,00)
T_21D = 21 / 252
T_26D = 26 / 252
T_40D = 40 / 252
T_42D = 42 / 252


def _point(
    strike: int, iv_pct: float | None, expiry: date, t: float, ticker: str = ""
) -> SmilePoint:
    return SmilePoint(
        ticker=ticker or f"SYN{strike}",
        maturity=expiry,
        strike=strike,
        t=t,
        iv_pct=iv_pct,
        delta=None,
    )


def _smile(
    strikes: list[int],
    expiry: date,
    t: float,
    iv_of: Callable[[float], float],
    forward: float = FORWARD,
) -> list[SmilePoint]:
    """Cadeia sintética com IV = `iv_of(ln(K/F))`, uma série por strike."""
    return [_point(strike, iv_of(math.log(strike / forward)), expiry, t) for strike in strikes]


def _quadratic_iv(x: float) -> float:
    """Smile sintético quadrático em `x = ln(K/F)`: 30 − 8x + 4x² (IV em `K=F` = 30)."""
    return 30.0 - 8.0 * x + 4.0 * x * x


# --- ATM por vencimento -------------------------------------------------------


def test_atm_recupera_a_iv_plantada_no_strike_igual_ao_forward() -> None:
    expiry = date(2026, 12, 18)
    chain = _smile([3600, 3800, 4000, 4200, 4400], expiry, T_40D, _quadratic_iv)

    result = atm_by_expiry(chain, FORWARD)

    assert len(result) == 1
    atm = result[0]
    assert atm.maturity == expiry
    assert atm.t == pytest.approx(T_40D)
    assert atm.n_strikes == 5
    assert atm.flags == ()
    # K = F exato na grade: o ponto plantado, sem interpolar (contrato §5.2).
    # (a curva quadrática NÃO seria recuperada por interpolação linear em w;
    #  recuperar 30,0 é exatamente a prova de que o ponto foi usado.)
    assert atm.atm_iv == pytest.approx(30.0, rel=1e-6)


def test_atm_interpola_variancia_total_quando_o_forward_cai_entre_strikes() -> None:
    # F = 4100 entre 4000 e 4200. Conta à mão, fora do módulo:
    #   x1 = ln(4000/4100), x2 = ln(4200/4100); w_i = (iv_i/100)²·t
    #   w0 = w1 + (w2 − w1)·(0 − x1)/(x2 − x1)
    #   iv0 = 100·sqrt(w0/t)
    forward = 4100.0
    t = T_21D
    x1 = math.log(4000.0 / forward)
    x2 = math.log(4200.0 / forward)
    w1 = (_quadratic_iv(x1) / 100.0) ** 2 * t
    w2 = (_quadratic_iv(x2) / 100.0) ** 2 * t
    w0 = w1 + (w2 - w1) * (0.0 - x1) / (x2 - x1)
    expected = 100.0 * math.sqrt(w0 / t)

    chain = _smile(
        [3800, 4000, 4200, 4400], date(2026, 12, 18), t, _quadratic_iv, forward=forward
    )
    atm = atm_by_expiry(chain, forward)[0]

    assert atm.atm_iv == pytest.approx(expected, rel=1e-12)
    # e a IV plantada em K=F (30,0) NÃO é recuperada: entre strikes a interpolação
    # é linear em w, então a curvatura do smile quadrático fica de fora.
    assert atm.atm_iv != pytest.approx(30.0, rel=1e-6)


def test_atm_consolida_call_e_put_do_mesmo_strike_pela_media() -> None:
    expiry = date(2026, 12, 18)
    chain = [
        _point(3900, 34.0, expiry, T_40D, "SYNC3900"),
        _point(3900, 36.0, expiry, T_40D, "SYNP3900"),
        _point(4000, 30.0, expiry, T_40D, "SYNC4000"),
        _point(4100, 32.0, expiry, T_40D, "SYNC4100"),
    ]

    atm = atm_by_expiry(chain, FORWARD)[0]

    assert atm.n_strikes == 3  # strikes distintos, não linhas de série
    assert atm.atm_iv == pytest.approx(30.0)
    assert atm.flags == ()


def test_atm_dois_strikes_e_sparse_chain() -> None:
    chain = _smile([3800, 4200], date(2026, 12, 18), T_40D, _quadratic_iv)

    atm = atm_by_expiry(chain, FORWARD)[0]

    assert MIN_STRIKES == 3
    assert atm.atm_iv is None
    assert atm.flags == (FLAG_SPARSE_CHAIN,)
    assert atm.n_strikes == 2


def test_atm_strike_zero_e_iv_none_nao_contam_no_smile() -> None:
    expiry = date(2026, 12, 18)
    chain = [
        _point(0, 40.0, expiry, T_40D),  # órfã de grade (AGENTS.md): fora
        _point(3900, None, expiry, T_40D),  # inconsistente: fora
        _point(4000, 30.0, expiry, T_40D),
        _point(4100, 31.0, expiry, T_40D),
    ]

    atm = atm_by_expiry(chain, FORWARD)[0]

    assert atm.n_strikes == 2
    assert atm.atm_iv is None
    assert atm.flags == (FLAG_SPARSE_CHAIN,)


def test_atm_sem_forward_e_no_forward_em_todo_vencimento() -> None:
    chain = _smile([3800, 4000, 4200], date(2026, 12, 18), T_40D, _quadratic_iv)

    result = atm_by_expiry(chain, None)

    assert result[0].atm_iv is None
    assert result[0].flags == (FLAG_NO_FORWARD,)
    assert result[0].n_strikes == 3  # a grade existe; só não há onde avaliar

    # forward <= 0 não é forward: `ln(K/F)` não existe.
    assert atm_by_expiry(chain, 0.0)[0].flags == (FLAG_NO_FORWARD,)


def test_atm_forward_fora_da_faixa_nao_extrapola() -> None:
    chain = _smile([3600, 3800, 4000, 4200, 4400], date(2026, 12, 18), T_40D, _quadratic_iv)

    atm = atm_by_expiry(chain, 5000.0)[0]

    assert atm.atm_iv is None
    assert atm.flags == (FLAG_SMILE_OUTSIDE_RANGE,)


def test_atm_tenor_invalido_e_iv_nao_finita_nao_viram_numero() -> None:
    expiry = date(2026, 12, 18)
    zero_t = _smile([3800, 4000, 4200], expiry, 0.0, _quadratic_iv)
    assert atm_by_expiry(zero_t, FORWARD)[0].flags == (FLAG_INVALID_TENOR,)

    nan_iv = [
        _point(3800, 30.0, expiry, T_40D),
        _point(4000, float("nan"), expiry, T_40D),
        _point(4200, 31.0, expiry, T_40D),
    ]
    atm = atm_by_expiry(nan_iv, FORWARD)[0]
    assert atm.atm_iv is None
    assert atm.flags == (FLAG_INVALID_VARIANCE,)


def test_atm_ordena_por_vencimento_e_nao_muta_a_entrada() -> None:
    late = date(2027, 3, 19)
    early = date(2026, 12, 18)
    chain = _smile([3800, 4000, 4200], late, T_42D, _quadratic_iv) + _smile(
        [3800, 4000, 4200], early, T_21D, _quadratic_iv
    )
    snapshot = list(chain)

    result = atm_by_expiry(chain, FORWARD)

    assert [expiry.maturity for expiry in result] == [early, late]
    assert chain == snapshot
    assert all(expiry.atm_iv == pytest.approx(30.0) for expiry in result)


# --- interpolação do 30d -----------------------------------------------------


def _expiry(maturity: date, t: float, atm_iv: float | None) -> ExpiryAtm:
    return ExpiryAtm(maturity=maturity, t=t, atm_iv=atm_iv, n_strikes=5, flags=())


def test_30d_interpola_w_linear_no_tenor() -> None:
    # Vencimentos plantados: iv = 20% em 21 du (w = 0,04·21/252) e 30% em 42 du
    # (w = 0,09·42/252). Alvo 30/252:
    #   w  = 0,04/12 + (0,09/6 − 0,04/12)·(30−21)/(42−21)
    #      = 0,003333… + 0,011666…·(9/21) = 0,008333… = 1/120
    #   iv = 100·sqrt((1/120)/(30/252)) = 100·sqrt(0,07) = 26,4575131…
    front = _expiry(date(2026, 12, 9), T_21D, 20.0)
    back = _expiry(date(2027, 1, 21), T_42D, 30.0)

    result = atm_interpolate_30d([back, front])  # ordem embaralhada de propósito

    assert result.method == METHOD_INTERPOLATED
    assert result.flags == ()
    assert result.front == front
    assert result.back == back
    assert result.iv_atm_30d == pytest.approx(26.4575131106, rel=1e-10)


def test_30d_vencimento_exatamente_no_alvo_usa_a_propria_iv() -> None:
    at_target = _expiry(date(2026, 12, 18), TARGET_T_30D, 27.5)
    far = _expiry(date(2027, 3, 19), T_42D, 31.0)

    result = atm_interpolate_30d([at_target, far])

    assert result.method == METHOD_INTERPOLATED
    assert result.front == at_target
    assert result.back == at_target  # span zero: a interpolação degenera no ponto
    assert result.iv_atm_30d == pytest.approx(27.5)


def test_30d_alem_do_ultimo_vencimento_bloqueia_extrapolacao() -> None:
    only_short = _expiry(date(2026, 12, 9), T_21D, 22.0)

    result = atm_interpolate_30d([only_short])

    assert result.iv_atm_30d is None
    assert result.method == METHOD_EXTRAPOLATED_BLOCKED
    assert result.flags == (FLAG_IV_EXTRAPOLATED,)
    assert result.front == only_short  # último válido, para diagnóstico
    assert result.back is None


def test_30d_grade_curta_e_no_short_expiry() -> None:
    only_long = _expiry(date(2027, 1, 21), T_42D, 28.0)

    result = atm_interpolate_30d([only_long])

    assert result.iv_atm_30d is None
    assert result.method == METHOD_NONE
    assert result.flags == (FLAG_NO_SHORT_EXPIRY,)
    assert result.front is None
    assert result.back == only_long


def test_30d_sem_vencimento_valido_e_no_expiry() -> None:
    result = atm_interpolate_30d(
        [_expiry(date(2026, 12, 18), T_40D, None), _expiry(date(2027, 1, 21), T_42D, None)]
    )

    assert result == Atm30d(
        iv_atm_30d=None, method=METHOD_NONE, front=None, back=None, flags=(FLAG_NO_EXPIRY,)
    )


def test_30d_ignora_vencimento_invalido_na_escolha_das_pontas() -> None:
    # O vencimento sem ATM (grade rala) fica na lista, mas não escolhe ponta:
    # mesmo tendo `t` menor que o front válido, `front` é o de 26 du.
    sparse = ExpiryAtm(
        maturity=date(2026, 12, 9), t=T_21D, atm_iv=None, n_strikes=2,
        flags=(FLAG_SPARSE_CHAIN,),
    )
    front = _expiry(date(2026, 12, 16), T_26D, 25.0)
    back = _expiry(date(2027, 1, 21), T_42D, 29.0)

    result = atm_interpolate_30d([sparse, front, back])

    assert result.front == front
    assert result.back == back
    assert result.method == METHOD_INTERPOLATED
