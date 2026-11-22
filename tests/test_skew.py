"""Skew 25Δ (F1.8, spec §6.8) — cadeia plantada, contas de mão no comentário.

O que se prova: `RR25 = iv(25Δ call) − iv(25Δ put)` e
`BFLY25 = ½(iv25c + iv25p) − iv_atm` recuperam os valores plantados; a
interpolação em Δ é linear (metade do caminho entre 0,20 e 0,30 é a média das
IVs); o sinal do Δ separa as pontas (call > 0, put < 0); banda `|Δ| ∈ [0,20;
0,80]` com mínimo de 5 pontos; sem bracket de 25Δ naquele lado → `None` +
`sparse_chain`; sem ATM → `bfly25 = None` + `no_atm_iv` mas `rr25` continua.
"""

from __future__ import annotations

import pytest

from b3o.derive.skew import (
    BAND_HIGH,
    BAND_LOW,
    FLAG_NO_ATM_IV,
    FLAG_SPARSE_CHAIN,
    MIN_STRIKES_BAND,
    DeltaPoint,
    SkewMetrics,
    rr25_bfly25,
)


def _chain(*pairs: tuple[float, float]) -> list[DeltaPoint]:
    return [DeltaPoint(delta=delta, iv_pct=iv_pct) for delta, iv_pct in pairs]


# --- valores plantados -------------------------------------------------------


def test_rr25_e_bfly25_recuperam_as_ivs_plantadas() -> None:
    # Pontas plantadas com IV constante em volta de 25Δ:
    #   calls: 20Δ = 30,0 · 30Δ = 30,0 → iv(25Δ call) = 30,0
    #   puts:  20Δ = 34,0 · 30Δ = 34,0 → iv(25Δ put)  = 34,0
    #   RR25  = 30,0 − 34,0 = −4,0
    #   BFLY  = ½(30,0 + 34,0) − 32,0 = 32,0 − 32,0 = 0,0
    chain = _chain(
        (0.20, 30.0),
        (0.30, 30.0),
        (0.40, 28.0),
        (0.50, 27.0),
        (-0.20, 34.0),
        (-0.30, 34.0),
        (-0.40, 36.0),
        (-0.50, 37.0),
    )

    metrics = rr25_bfly25(chain, atm_iv=32.0)

    assert metrics.n_strikes_band == 8
    assert metrics.flags == ()
    assert metrics.rr25 == pytest.approx(-4.0)
    assert metrics.bfly25 == pytest.approx(0.0)


def test_25_delta_vem_de_interpolacao_linear_em_delta() -> None:
    # calls: 20Δ = 32,0 e 30Δ = 28,0 → 25Δ = 32,0 + (28,0 − 32,0)·(0,05/0,10) = 30,0
    # puts:  20Δ = 38,0 e 30Δ = 34,0 → 25Δ = 36,0
    #   RR25  = 30,0 − 36,0 = −6,0
    #   BFLY  = ½(30,0 + 36,0) − 32,0 = 33,0 − 32,0 = +1,0
    chain = _chain(
        (0.20, 32.0),
        (0.30, 28.0),
        (0.40, 26.0),
        (-0.20, 38.0),
        (-0.30, 34.0),
        (-0.40, 40.0),
    )

    metrics = rr25_bfly25(chain, atm_iv=32.0)

    assert metrics.n_strikes_band == 6
    assert metrics.rr25 == pytest.approx(-6.0)
    assert metrics.bfly25 == pytest.approx(1.0)


def test_delta_exatamente_25_usa_o_ponto_sem_interpolar() -> None:
    chain = _chain(
        (0.25, 29.0),
        (0.40, 27.0),
        (0.60, 24.0),
        (-0.25, 33.0),
        (-0.40, 35.0),
        (-0.60, 38.0),
    )

    metrics = rr25_bfly25(chain, atm_iv=31.0)

    # RR = 29,0 − 33,0 = −4,0 · BFLY = ½(29,0+33,0) − 31,0 = 0,0
    assert metrics.rr25 == pytest.approx(-4.0)
    assert metrics.bfly25 == pytest.approx(0.0)


def test_o_sinal_do_delta_e_que_separa_a_ponta() -> None:
    # Duas cadeias de mesma magnitude e sinais opostos possíveis. A ponta call é
    # formada só pelos Δ positivos e a put só pelos negativos: o 25Δ da call vale
    # 40,0 e o da put vale 30,0  →  RR = +10,0.
    chain = _chain(
        (0.25, 40.0),
        (0.30, 39.0),
        (-0.25, 30.0),
        (-0.30, 31.0),
        (-0.40, 32.0),
    )
    assert rr25_bfly25(chain, atm_iv=35.0).rr25 == pytest.approx(10.0)

    # Com todos os Δ positivos não há ponta put nenhuma: sem bracket → None com
    # flag, e nunca uma média silenciosa de calls com puts.
    no_put_side = _chain(
        (0.25, 40.0),
        (0.30, 39.0),
        (0.40, 38.0),
        (0.50, 37.0),
        (0.60, 36.0),
    )
    metrics = rr25_bfly25(no_put_side, atm_iv=35.0)
    assert metrics.rr25 is None
    assert metrics.flags == (FLAG_SPARSE_CHAIN,)


# --- becos sem saída ---------------------------------------------------------


def test_menos_de_cinco_pontos_na_banda_e_sparse_chain() -> None:
    chain = _chain((0.20, 30.0), (0.30, 29.0), (-0.20, 34.0), (-0.30, 35.0))

    metrics = rr25_bfly25(chain, atm_iv=32.0)

    assert MIN_STRIKES_BAND == 5
    assert metrics == SkewMetrics(
        rr25=None, bfly25=None, n_strikes_band=4, flags=(FLAG_SPARSE_CHAIN,)
    )


def test_pontos_fora_da_banda_nao_contam_nem_interpolam() -> None:
    # 0,10 e 0,90 estão fora de [0,20; 0,80] e não salvam a grade: só 4 na banda.
    chain = _chain(
        (0.10, 45.0),
        (0.20, 31.0),
        (0.30, 29.0),
        (0.90, 20.0),
        (-0.20, 35.0),
        (-0.30, 36.0),
    )

    metrics = rr25_bfly25(chain, atm_iv=33.0)

    assert BAND_LOW == 0.20
    assert BAND_HIGH == 0.80
    assert metrics.n_strikes_band == 4
    assert metrics.rr25 is None
    assert metrics.flags == (FLAG_SPARSE_CHAIN,)


def test_sem_bracket_de_25_delta_naquele_lado_devolve_none() -> None:
    # Sete pontos na banda (passa o mínimo), mas a put só tem |Δ| >= 0,60:
    # não existe vizinho em volta de 0,25 naquele lado → não se extrapola a asa.
    chain = _chain(
        (0.20, 31.0),
        (0.30, 30.0),
        (0.40, 29.0),
        (0.50, 28.0),
        (-0.60, 36.0),
        (-0.70, 38.0),
        (-0.80, 41.0),
    )

    metrics = rr25_bfly25(chain, atm_iv=33.0)

    assert metrics.n_strikes_band == 7
    assert metrics.rr25 is None
    assert metrics.bfly25 is None
    assert metrics.flags == (FLAG_SPARSE_CHAIN,)


def test_sem_atm_o_rr25_sai_e_o_bfly25_vira_none() -> None:
    chain = _chain(
        (0.20, 30.0),
        (0.30, 30.0),
        (0.40, 29.0),
        (-0.20, 34.0),
        (-0.30, 34.0),
        (-0.40, 35.0),
    )

    metrics = rr25_bfly25(chain, atm_iv=None)

    assert metrics.rr25 == pytest.approx(-4.0)
    assert metrics.bfly25 is None
    assert metrics.flags == (FLAG_NO_ATM_IV,)


def test_cadeia_vazia_devolve_none_com_sparse_chain() -> None:
    metrics = rr25_bfly25([], atm_iv=30.0)

    assert metrics == SkewMetrics(
        rr25=None, bfly25=None, n_strikes_band=0, flags=(FLAG_SPARSE_CHAIN,)
    )
