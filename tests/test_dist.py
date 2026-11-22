"""Expected move e dividendo implícito (F1.9, spec §6.10) — contas de mão.

O que se prova: `expected_move_1s` é `1,25 × straddle/spot` em **fração**
(straddle 2,00 em spot 40,00 → 1,25 × 0,05 = 0,0625 exato); `implied_div_yield`
é `r − 100·ln(F/S)/T` em **% a.a.**, conferido contra a conta feita fora do
módulo; e as guardas (`t <= 0`, preço ≤ 0) devolvem `None`, nunca zero.
"""

from __future__ import annotations

import math

import pytest

from b3o.derive.dist import EXP_MOVE_FACTOR, expected_move_1s, implied_div_yield


def test_expected_move_de_1_sigma_e_fracao_do_spot() -> None:
    # straddle 2,00 / spot 40,00 = 0,05  →  1,25 × 0,05 = 0,0625 (6,25%)
    # 0,0625 é exato em binário (1/16), então a igualdade é exata, sem tolerância.
    assert EXP_MOVE_FACTOR == 1.25
    assert expected_move_1s(straddle_mid=2.00, spot=40.00) == 0.0625


def test_expected_move_escala_com_o_straddle_e_o_spot() -> None:
    assert expected_move_1s(2.00, 40.00) == pytest.approx(1.25 * 2.0 / 40.0, rel=1e-15)
    assert expected_move_1s(4.00, 40.00) == pytest.approx(0.125)
    assert expected_move_1s(0.00, 40.00) == 0.0


def test_expected_move_recusa_preco_impossivel() -> None:
    assert expected_move_1s(2.00, 0.00) is None
    assert expected_move_1s(2.00, -40.00) is None
    assert expected_move_1s(-0.01, 40.00) is None


def test_implied_div_yield_confere_com_a_conta_de_mao() -> None:
    # r = 13,0% a.a.; F = 105, S = 100, T = 0,25 ano.
    #   ln(F/S) = ln(1,05) = 0,0487901641694…
    #   ln(F/S)/T = 0,1951606566777…  →  ×100 = 19,51606566777… % a.a.
    #   implied_div_yield = 13,0 − 19,51606566777… = −6,51606566777… % a.a.
    # (forward acima do spot mostra carry total maior que o juro: dividendo
    #  implícito negativo = custo de empréstimo/escassez, não dividendo.)
    result = implied_div_yield(r_pct=13.0, forward=105.0, spot=100.0, t=0.25)

    assert result == pytest.approx(-6.51606566777, rel=1e-11, abs=1e-12)
    # e a conta escrita com log do teste bate com a do módulo
    assert result == pytest.approx(13.0 - 100.0 * math.log(1.05) / 0.25, rel=1e-15)


def test_implied_div_yield_com_forward_no_spot_devolve_o_juro() -> None:
    # ln(S/S) = 0  →  o carry é todo juro: yield implícito = r.
    assert implied_div_yield(r_pct=13.65, forward=40.0, spot=40.0, t=30 / 252) == pytest.approx(
        13.65
    )


def test_implied_div_yield_desconta_dividendo_quando_o_forward_fica_abaixo() -> None:
    # F/S = 0,98 em 30 du (T = 30/252 = 0,1190476…):
    #   ln(0,98) = −0,0202027…  →  /T = −0,169702…  →  ×100 = −16,9702… %
    #   yield = 13,65 − (−16,9702…) = 30,6202… % a.a. (dividendo grande, plausível)
    result = implied_div_yield(r_pct=13.65, forward=98.0, spot=100.0, t=30 / 252)

    assert result == pytest.approx(13.65 + 100.0 * 0.0202027073175 / (30 / 252), rel=1e-12)
    assert result > 13.65


def test_implied_div_yield_recusa_prazo_e_preco_impossiveis() -> None:
    assert implied_div_yield(13.0, 105.0, 100.0, 0.0) is None
    assert implied_div_yield(13.0, 105.0, 100.0, -0.1) is None
    assert implied_div_yield(13.0, 0.0, 100.0, 0.25) is None
    assert implied_div_yield(13.0, 105.0, 0.0, 0.25) is None
