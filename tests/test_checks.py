"""Validadores do smile (F1.10, spec §6.11, contrato da Onda E §5.7).

Cadeias **plantadas**: cada número esperado está calculado à mão no comentário, e
o teste não usa o módulo para gerar o esperado. O ponto central do §5.7 é que a
folga da butterfly é **relativa** (`butterfly_tol · w_atm`): o mesmo `Δ²w`
absoluto viola num vencimento curto e passa num longo — o teste 3 planta os dois
casos com o mesmo `−0,0028`.
"""

from __future__ import annotations

from datetime import date

import pytest

from b3o.derive.atm import SmilePoint
from b3o.derive.checks import (
    FLAG_BFLY_NEGATIVE,
    FLAG_CALENDAR_VIOLATION,
    FLAG_INCONSISTENT,
    FLAG_MONOTONICITY_VIOLATION,
    ConsistencyReport,
    check_chain,
)

D1 = date(2026, 12, 18)
D2 = date(2027, 1, 15)
D3 = date(2027, 6, 18)


def _point(ticker: str, maturity: date, strike: int, t: float, iv: float | None) -> SmilePoint:
    return SmilePoint(
        ticker=ticker, maturity=maturity, strike=strike, t=t, iv_pct=iv, delta=None
    )


def _flags(**pairs: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    return dict(pairs)


# --- 1. cadeia limpa ---------------------------------------------------------


def test_cadeia_limpa_nao_gera_flag() -> None:
    # Duas maturities, 5 strikes, IV decrescente em K e w convexa.
    # t = 0,1 (D1): IV 30,0 · 28,5 · 27,5 · 26,8 · 26,3
    #   w = (iv/100)²·t = 0,009000 · 0,0081225 · 0,0075625 · 0,0071824 · 0,0069169
    #   Δ²w (i=2) = 0,0075625 − 2·0,0081225 + 0,009000 = +0,0003175  (convexa)
    # t = 0,25 (D2): IV 32,0 · 30,0 · 28,5 · 27,5 · 27,0
    #   w = 0,025600 · 0,022500 · 0,02030625 · 0,01890625 · 0,018225
    #   Δ²w (i=2) = 0,02030625 − 2·0,022500 + 0,025600 = +0,00090625 (convexa)
    # F = 4000: w_atm(D1) = 0,0075625 → folga 0,000378; w_atm(D2) = 0,02030625.
    # Calendário: w(D2) > w(D1) em todos os strikes. Nada viola.
    chain = [
        *(
            _point(f"A{k}", D1, k, 0.10, iv)
            for k, iv in zip(
                (3800, 3900, 4000, 4100, 4200), (30.0, 28.5, 27.5, 26.8, 26.3), strict=True
            )
        ),
        *(
            _point(f"B{k}", D2, k, 0.25, iv)
            for k, iv in zip(
                (3800, 3900, 4000, 4100, 4200), (32.0, 30.0, 28.5, 27.5, 27.0), strict=True
            )
        ),
    ]

    report = check_chain(chain, forward=4000.0)

    assert report.ok_smile is True
    assert report.series_flags == {}
    assert report.flags_by_expiry == {}
    assert report.n_series == 10


# --- 2. butterfly abaixo da folga -------------------------------------------


def test_butterfly_negativa_marca_o_trio() -> None:
    # t = 0,25; w plantada 0,0100 · 0,0099 · 0,0070 · 0,0050
    # Δ²w (i=1, trio 3800·3900·4000) = 0,0070 − 2·0,0099 + 0,0100 = −0,0028
    # F = 3900 → w_atm = 0,0099 → folga = 5% = 0,000495 → −0,0028 < −0,000495: viola.
    # Δ²w (i=2, trio 3900·4000·4100) = 0,0050 − 2·0,0070 + 0,0099 = +0,0009: passa.
    # IV = 200·sqrt(w) = 20,0 · 19,8997 · 16,7332 · 14,1421 (decrescente: sem monotonicidade).
    # O trio (3800, 3900, 4000) inteiro sai do smile; 4100 fica.
    chain = [
        _point("T380", D2, 3800, 0.25, 20.0),
        _point("T390", D2, 3900, 0.25, 19.899748742132401),
        _point("T400", D2, 4000, 0.25, 16.73320053068151),
        _point("T410", D2, 4100, 0.25, 14.142135623730951),
    ]

    report = check_chain(chain, forward=3900.0)

    expected = (FLAG_BFLY_NEGATIVE, FLAG_INCONSISTENT)
    assert report.ok_smile is False
    assert report.series_flags == _flags(T380=expected, T390=expected, T400=expected)
    assert "T410" not in report.series_flags
    assert dict(report.flags_by_expiry) == {D2: expected}
    assert report.n_series == 4


# --- 3. a folga é relativa, não absoluta ------------------------------------


def test_mesmo_delta_w_absoluto_viola_no_curto_e_passa_no_longo() -> None:
    # Curto: t = 0,25, w = 0,0102 · 0,0100 · 0,0070
    #   Δ²w = 0,0070 − 2·0,0100 + 0,0102 = −0,0028
    #   F = 3900 → w_atm = 0,0100 → folga 5% = 0,0005 → VIOLA.
    # Longo: t = 0,50, w = 0,0830 · 0,0800 · 0,0742
    #   Δ²w = 0,0742 − 2·0,0800 + 0,0830 = −0,0028 (mesmo valor absoluto)
    #   F = 3900 → w_atm = 0,0800 → folga 5% = 0,0040 → PASSA.
    short = [
        _point("S380", D1, 3800, 0.25, 20.19900987672416),
        _point("S390", D1, 3900, 0.25, 20.0),
        _point("S400", D1, 4000, 0.25, 16.73320053068151),
    ]
    long = [
        _point("L380", D3, 3800, 0.50, 40.74309757492601),
        _point("L390", D3, 3900, 0.50, 40.0),
        _point("L400", D3, 4000, 0.50, 38.52272077813711),
    ]

    # Confere que os dois `Δ²w` são o mesmo número, à mão.
    def _second(points: list[SmilePoint]) -> float:
        ws = [(p.iv_pct / 100.0) ** 2 * p.t for p in points if p.iv_pct is not None]
        return ws[2] - 2.0 * ws[1] + ws[0]

    assert _second(short) == pytest.approx(-0.0028, abs=1e-9)
    assert _second(long) == pytest.approx(-0.0028, abs=1e-9)

    short_report = check_chain(short, forward=3900.0)
    long_report = check_chain(long, forward=3900.0)

    expected = (FLAG_BFLY_NEGATIVE, FLAG_INCONSISTENT)
    assert short_report.ok_smile is False
    assert short_report.series_flags == _flags(S380=expected, S390=expected, S400=expected)
    assert long_report.ok_smile is True
    assert long_report.series_flags == {}


# --- 4. calendário -----------------------------------------------------------


def test_w_decrescente_entre_vencimentos_e_calendar_violation() -> None:
    # D1 (t = 0,1): K 3800 → w = 0,31²·0,1 = 0,00961 · K 4000 → w = 0,30²·0,1 = 0,009
    # D2 (t = 0,25): K 3800 → w = 0,20²·0,25 = 0,010 (cresce, ok)
    #                K 4000 → w = 0,17²·0,25 = 0,007225
    #   frente D1, w_atm (F = 4000) = 0,009 → folga 0,00045
    #   0,007225 − 0,009 = −0,001775 < −0,00045 → viola no strike 4000, nos DOIS vencimentos.
    # Monotonicidade: 31 > 30 e 20 > 17 (ok). Sem trio (2 strikes por vencimento).
    chain = [
        _point("C380", D1, 3800, 0.10, 31.0),
        _point("C400", D1, 4000, 0.10, 30.0),
        _point("D380", D2, 3800, 0.25, 20.0),
        _point("D400", D2, 4000, 0.25, 17.0),
    ]

    report = check_chain(chain, forward=4000.0)

    expected = (FLAG_CALENDAR_VIOLATION, FLAG_INCONSISTENT)
    assert report.ok_smile is False
    assert report.series_flags == _flags(C400=expected, D400=expected)
    assert "C380" not in report.series_flags
    assert "D380" not in report.series_flags
    assert dict(report.flags_by_expiry) == {D1: expected, D2: expected}
    assert report.n_series == 4


# --- 5. monotonicidade -------------------------------------------------------


def test_iv_crescendo_com_k_acima_da_folga_e_monotonicity_violation() -> None:
    # t = 0,25; IV 30,0 · 28,0 · 27,0 · 27,5 em K 3800 · 3900 · 4000 · 4100.
    # O par (4000, 4100) sobe 0,5 vol point > 0,05 → viola.
    # A convexidade fica de pé para isolar a flag: Δ²w (i=2) = 0,01890625 − 2·0,018225
    # + 0,0196 = +0,00205625, e o trio (i=2) é o último — nenhuma butterfly.
    chain = [
        _point("M380", D2, 3800, 0.25, 30.0),
        _point("M390", D2, 3900, 0.25, 28.0),
        _point("M400", D2, 4000, 0.25, 27.0),
        _point("M410", D2, 4100, 0.25, 27.5),
    ]

    report = check_chain(chain, forward=4000.0)

    expected = (FLAG_MONOTONICITY_VIOLATION, FLAG_INCONSISTENT)
    assert report.ok_smile is False
    # O par adjacente inteiro é marcado (o contrato aponta o K menor como suspeita
    # natural; o par contém o ponto alto, que é o culpado no dado real).
    assert report.series_flags == _flags(M400=expected, M410=expected)
    assert "M380" not in report.series_flags
    assert "M390" not in report.series_flags


def test_iv_crescendo_dentro_da_folga_nao_gera_flag() -> None:
    # Mesma cadeia com bump de 0,02 vol point < 0,05 → sem flag.
    # Δ²w (i=2) = 0,01825201 − 2·0,018225 + 0,0196 = +0,00140201 (convexa).
    chain = [
        _point("M380", D2, 3800, 0.25, 30.0),
        _point("M390", D2, 3900, 0.25, 28.0),
        _point("M400", D2, 4000, 0.25, 27.0),
        _point("M410", D2, 4100, 0.25, 27.02),
    ]

    report = check_chain(chain, forward=4000.0)

    assert report.ok_smile is True
    assert report.series_flags == {}
    assert report.n_series == 4


# --- 6. ausência de dado não é inconsistência --------------------------------


def test_pontos_invalidos_nao_entram_nem_geram_flag() -> None:
    # Trio limpo (D2, t = 0,25, IV 30 · 28 · 27: convexo e decrescente) + lixo:
    # IV None, strike 0 (IBOV, AGENTS.md), t = 0 (vencendo hoje) e IV NaN.
    good = [
        _point("P380", D2, 3800, 0.25, 30.0),
        _point("P390", D2, 3900, 0.25, 28.0),
        _point("P400", D2, 4000, 0.25, 27.0),
    ]
    chain = [
        *good,
        _point("NONE", D2, 4200, 0.25, None),
        _point("ZERO", D2, 0, 0.25, 40.0),
        _point("TJUS", D2, 4300, 0.0, 40.0),
        _point("NAN", D2, 4400, 0.25, float("nan")),
    ]

    report = check_chain(chain, forward=3900.0)

    assert report.ok_smile is True
    assert report.series_flags == {}
    assert report.n_series == 3  # só os três pontos utilizáveis
    assert len(chain) == 7  # a entrada não é mutilada


# --- 7. entrada imutável -----------------------------------------------------


def test_entrada_nao_e_mutada() -> None:
    # Cadeia com violação (para garantir que o caminho que escreve não toca a entrada).
    chain = [
        _point("T380", D2, 3800, 0.25, 20.0),
        _point("T390", D2, 3900, 0.25, 19.899748742132401),
        _point("T400", D2, 4000, 0.25, 16.73320053068151),
    ]
    before = list(chain)
    before_ids = [id(point) for point in chain]

    check_chain(chain, forward=3900.0)

    assert chain == before
    assert [id(point) for point in chain] == before_ids


# --- 8. sem forward ----------------------------------------------------------

def test_forward_none_usa_a_mediana_dos_w_do_vencimento() -> None:
    # t = 0,25; w = 0,0120 · 0,0100 · 0,00745
    #   Δ²w = 0,00745 − 2·0,0100 + 0,0120 = −0,00055
    # F = 3900 (strike mais próximo) → folga = 5%·0,0120 = 0,0006 → −0,00055 passa.
    # F = None → âncora documentada = mediana dos w = 0,0100 → folga 0,0005 → viola.
    chain = [
        _point("F390", D2, 3900, 0.25, 21.908902300206645),
        _point("F400", D2, 4000, 0.25, 20.0),
        _point("F410", D2, 4100, 0.25, 17.26267650163207),
    ]

    with_forward = check_chain(chain, forward=3900.0)
    without_forward = check_chain(chain, forward=None)

    assert with_forward.ok_smile is True
    assert without_forward.ok_smile is False
    expected = (FLAG_BFLY_NEGATIVE, FLAG_INCONSISTENT)
    assert without_forward.series_flags == _flags(
        F390=expected, F400=expected, F410=expected
    )


# --- bordas ------------------------------------------------------------------


def test_cadeia_vazia_e_ok() -> None:
    report = check_chain([], forward=4000.0)

    assert report == ConsistencyReport(
        series_flags={}, ok_smile=True, n_series=0, flags_by_expiry={}
    )


def test_call_e_put_do_mesmo_strike_sao_consolidados() -> None:
    # Call e put europeias do mesmo strike têm a mesma IV (paridade): o strike conta
    # uma vez e a flag atinge as duas séries. Mesma forma plantada do teste 2 (trio
    # 3800·3900·4000 com Δ²w = −0,0028), agora com as duas pontas em cada strike.
    iv_3800, iv_3900, iv_4000 = 20.0, 19.899748742132401, 16.73320053068151
    chain = [
        _point("CA380", D2, 3800, 0.25, iv_3800),
        _point("PE380", D2, 3800, 0.25, iv_3800),
        _point("CA390", D2, 3900, 0.25, iv_3900),
        _point("PE390", D2, 3900, 0.25, iv_3900),
        _point("CA400", D2, 4000, 0.25, iv_4000),
        _point("PE400", D2, 4000, 0.25, iv_4000),
    ]

    report = check_chain(chain, forward=3900.0)

    expected = (FLAG_BFLY_NEGATIVE, FLAG_INCONSISTENT)
    assert report.ok_smile is False
    assert report.n_series == 6
    assert report.series_flags == _flags(
        CA380=expected,
        PE380=expected,
        CA390=expected,
        PE390=expected,
        CA400=expected,
        PE400=expected,
    )
