"""Forward por paridade (F1.2) — o esperado vem da paridade, nunca do estimador.

O instrumento do teste é a identidade do contrato: `C − P = DF·(F − K)`. O teste
escolhe `F`, `DF` e os `P` à mão, constrói `C` pela própria identidade e exige que o
estimador devolva o `F` que entrou. Nenhum preço esperado sai de `b3o`.
"""

from __future__ import annotations

import pytest

from b3o.derive.forward import (
    FLAG_AMERICAN_CALL_EX_DIV,
    FLAG_AMERICAN_CALL_ITM,
    FLAG_CROSSING_INCONSISTENT_SLOPE,
    FLAG_FEW_PAIRS,
    FLAG_INVALID_QUOTE,
    FLAG_NO_OTM_CROSSING,
    FLAG_NO_PAIRS,
    FLAG_NONPOSITIVE_SLOPE,
    FLAG_PUT_NOT_EUROPEAN,
    FLAG_STYLE_UNKNOWN,
    ParityQuote,
    forward_by_parity,
)

FORWARD = 10_000.0
"""Forward verdadeiro do cenário sintético, em centavos."""

DF = 0.99
"""Fator de desconto do cenário (taxa ~13% a.a. num prazo curto)."""

SPOT = 9_950.0
"""Spot do dia: serve só para escolher o lado OTM (não é a resposta)."""


def quote(strike: float, *, forward: float = FORWARD, put_mid: float | None = None) -> ParityQuote:
    """Par E/E com `C = P + DF·(F − K)` — paridade exata por construção.

    O put é escolhido grande o bastante para a call ficar positiva até em strike acima
    do forward (uma call OTM não pode valer menos que a diferença de paridade).
    """
    if put_mid is None:
        put_mid = 300.0 + max(0.0, DF * (strike - forward))
    return ParityQuote(
        strike=int(strike),
        call_mid=put_mid + DF * (forward - strike),
        put_mid=put_mid,
        call_style="E",
        put_style="E",
    )


def test_cruzamento_exato_entre_dois_strikes() -> None:
    chain = [quote(9_800.0), quote(10_200.0)]
    result = forward_by_parity(chain, spot=SPOT)
    assert result.method == "crossing"
    assert result.forward == pytest.approx(FORWARD, rel=1e-12)
    assert result.n_pairs == 2
    assert result.ols_slope is None
    assert result.flags == ()


def test_cruzamento_interpola_assimetrico() -> None:
    """Bracket assimétrico: 99 acima e −594 abaixo → a raiz fica em 9 900 + 100."""
    chain = [quote(9_900.0), quote(10_600.0)]
    result = forward_by_parity(chain, spot=SPOT)
    assert result.method == "crossing"
    assert result.forward == pytest.approx(9_900.0 + 700.0 * 99.0 / 693.0, rel=1e-12)
    assert result.forward == pytest.approx(FORWARD, rel=1e-12)


def test_cruzamento_escolhe_o_bracket_mais_proximo_do_spot() -> None:
    """Dois cruzamentos (cotação ruim): fica o mais próximo de `F₀` = spot."""
    noisy = [quote(9_000.0), quote(9_100.0), quote(10_800.0), quote(10_900.0)]
    result = forward_by_parity(noisy, spot=SPOT)
    # Os brackets contêm o forward verdadeiro (10 000) e um cruzamento espúrio perto de 9 050
    # só se a primeira reta cruzar; aqui `C−P` é linear em `K` nos dois pares, e o bracket
    # 9 100 → 10 800 é o único que troca de sinal.
    assert result.method == "crossing"
    assert result.forward == pytest.approx(FORWARD, rel=1e-12)


def test_ols_quando_nao_ha_cruzamento() -> None:
    """Todos os strikes abaixo do forward: `C−P > 0` em todos, sem raiz na grade."""
    chain = [quote(float(strike)) for strike in (9_400, 9_500, 9_600, 9_700, 9_800, 9_900)]
    result = forward_by_parity(chain, spot=SPOT)
    assert result.method == "ols"
    assert result.forward == pytest.approx(FORWARD, rel=1e-12)
    assert result.ols_slope == pytest.approx(DF, rel=1e-12)  # o slope é o DF
    assert result.n_pairs == 6
    assert FLAG_NO_OTM_CROSSING in result.flags


def test_ols_ignora_strikes_fora_da_banda_de_15pct() -> None:
    chain = [
        quote(8_000.0),  # 20% abaixo do spot: fora da banda
        quote(9_400.0),
        quote(9_500.0),
        quote(9_600.0),
    ]
    result = forward_by_parity(chain, spot=SPOT)
    assert result.method == "ols"
    assert result.n_pairs == 3
    assert result.forward == pytest.approx(FORWARD, rel=1e-12)


def test_ols_com_ruido_de_arredondamento() -> None:
    """Preços em centavos inteiros: o forward tem que ficar a menos de 0,1% do certo."""
    chain = [
        ParityQuote(
            strike=strike,
            call_mid=float(round(100 + DF * (FORWARD - strike))),
            put_mid=100.0,
            call_style="E",
            put_style="E",
        )
        for strike in (9_300, 9_450, 9_600, 9_750, 9_900)
    ]
    result = forward_by_parity(chain, spot=SPOT)
    assert result.method == "ols"
    assert result.forward == pytest.approx(FORWARD, rel=1e-3)


def test_ols_com_slope_nao_positivo_falha_explicito() -> None:
    """`P−C` decrescente em `K` (DF negativo) não é paridade: `None` + flag."""
    chain = [
        ParityQuote(strike=strike, call_mid=c, put_mid=100.0, call_style="E", put_style="E")
        for strike, c in ((9_700, 300.0), (9_800, 400.0), (9_900, 500.0))
    ]
    result = forward_by_parity(chain, spot=SPOT)
    assert result.method == "none"
    assert result.forward is None
    assert result.ols_slope is not None and result.ols_slope < 0
    assert FLAG_NONPOSITIVE_SLOPE in result.flags


def test_poucos_pares_falha_explicito() -> None:
    chain = [quote(9_400.0), quote(9_500.0)]  # dois pares, sem cruzamento
    result = forward_by_parity(chain, spot=SPOT)
    assert result.method == "none"
    assert result.forward is None
    assert result.n_pairs == 2
    assert FLAG_FEW_PAIRS in result.flags


def test_sem_dado_none_com_flag() -> None:
    result = forward_by_parity([], spot=SPOT)
    assert result.forward is None
    assert result.method == "none"
    assert result.n_pairs == 0
    assert result.flags == (FLAG_NO_PAIRS,)


def test_strike_zero_e_preco_invalido_ficam_fora() -> None:
    zero_strike = ParityQuote(
        strike=0, call_mid=100.0, put_mid=100.0, call_style="E", put_style="E"
    )
    zero_put = ParityQuote(
        strike=10_000, call_mid=1.0, put_mid=0.0, call_style="E", put_style="E"
    )
    result = forward_by_parity([zero_strike, zero_put], spot=SPOT)
    assert result.forward is None
    assert FLAG_INVALID_QUOTE in result.flags
    assert FLAG_NO_PAIRS in result.flags


def test_put_americana_exclui_o_par() -> None:
    """Put 'A' não existe no dado de 2026, mas se aparecer não entra (spec §6.2)."""
    american_put = ParityQuote(
        strike=10_000, call_mid=120.0, put_mid=100.0, call_style="E", put_style="A"
    )
    result = forward_by_parity([american_put], spot=SPOT)
    assert result.forward is None
    assert FLAG_PUT_NOT_EUROPEAN in result.flags


def test_call_americana_itm_nao_entra() -> None:
    """A call americana ITM carrega prêmio de exercício: fora do forward."""
    itm = ParityQuote(strike=9_500, call_mid=600.0, put_mid=100.0, call_style="A", put_style="E")
    result = forward_by_parity([itm], spot=10_000.0)
    assert result.forward is None
    assert FLAG_AMERICAN_CALL_ITM in result.flags


def test_call_americana_otm_entra() -> None:
    otm = ParityQuote(
        strike=10_400,
        call_mid=500.0 + DF * (FORWARD - 10_400),
        put_mid=500.0,
        call_style="A",
        put_style="E",
    )
    other = quote(9_600.0)
    result = forward_by_parity([otm, other], spot=10_000.0)
    assert result.method == "crossing"
    assert result.forward == pytest.approx(FORWARD, rel=1e-12)
    assert FLAG_AMERICAN_CALL_ITM not in result.flags


def test_call_americana_com_ex_dividendo_nao_entra() -> None:
    contaminated = ParityQuote(
        strike=10_400,
        call_mid=100.0,
        put_mid=100.0,
        call_style="A",
        put_style="E",
        call_ex_div=True,
    )
    result = forward_by_parity([contaminated], spot=10_000.0)
    assert result.forward is None
    assert FLAG_AMERICAN_CALL_EX_DIV in result.flags


def test_estilo_desconhecido_com_strict_nao_entra() -> None:
    def unknown(strike: int) -> ParityQuote:
        return ParityQuote(
            strike=strike,
            call_mid=100.0 + DF * (FORWARD - strike),
            put_mid=100.0,
            call_style=None,
            put_style=None,
        )

    chain = [unknown(9_700), unknown(9_800), unknown(9_900)]
    strict = forward_by_parity(chain, spot=SPOT)
    assert strict.forward is None
    assert FLAG_STYLE_UNKNOWN in strict.flags

    relaxed = forward_by_parity(chain, spot=SPOT, style_strict=False)
    assert relaxed.method == "ols"
    assert relaxed.forward == pytest.approx(FORWARD, rel=1e-12)


def test_sem_spot_usa_strike_de_diff_minimo_como_referencia() -> None:
    """Sem `spot`, o `F₀` é o strike de `|C−P|` mínimo — só para escolher o lado."""
    chain = [quote(9_800.0), quote(10_050.0), quote(10_200.0)]
    result = forward_by_parity(chain)
    assert result.method == "crossing"
    assert result.forward == pytest.approx(FORWARD, rel=1e-12)


def test_strike_repetido_fica_com_o_primeiro() -> None:
    chain = [quote(10_200.0), quote(10_200.0, put_mid=200.0), quote(9_800.0)]
    result = forward_by_parity(chain, spot=SPOT)
    assert result.flags == ("duplicate_strike",)
    assert result.forward == pytest.approx(FORWARD, rel=1e-12)


def test_cruzamento_com_print_velho_nao_engana() -> None:
    """Print a 45 ¢ da curva (PETR4 19/11, strike 4344) não pode virar "forward"."""
    stale = ParityQuote(
        strike=10_080, call_mid=900.0, put_mid=300.0, call_style="E", put_style="E"
    )
    chain = [
        quote(9_850.0, put_mid=450.0),  # C−P = +148,5
        quote(10_050.0, put_mid=1_049.5),  # C−P = −49,5
        stale,  # C−P = +600: slope de −21,6 no bracket vizinho
    ]
    result = forward_by_parity(chain, spot=FORWARD)
    # sem a guarda, o bracket 10 050 → 10 080 daria um forward absurdo; com ela, o
    # cruzamento bom (9 850/10 050) é o que sobrevive.
    assert result.method == "crossing"
    assert result.forward == pytest.approx(FORWARD, rel=1e-12)
    assert FLAG_CROSSING_INCONSISTENT_SLOPE in result.flags
