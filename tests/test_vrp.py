"""VRP e estatísticas de IV (F1.7, spec §5.3/§6.7) — contas de mão no comentário.

O que se prova: `vrp` é subtração de vol points; `vrp_z` é desvio **amostral**
(n−1) da janela de 252 com o valor de hoje incluso; `iv_rank_252` é posição
min–max e `iv_percentile_252` é percentil inclusivo (duas métricas diferentes);
janela incompleta devolve `None`; e — o mais importante — **nada olha o futuro**:
o z de uma série truncada em `t` difere do z da série inteira, e valor antigo
demais não entra na conta.

Os esperados saem de álgebra fechada escrita aqui, nunca do módulo.
"""

from __future__ import annotations

import math

import pytest

from b3o.derive.vrp import IV_WINDOW, VRP_Z_WINDOW, iv_percentile_252, iv_rank_252, vrp, vrp_z


def test_vrp_e_a_diferenca_de_pontos_de_vol() -> None:
    assert vrp(26.5, 18.0) == pytest.approx(8.5)
    assert vrp(20.0, 24.5) == pytest.approx(-4.5)


# --- vrp_z -------------------------------------------------------------------


def test_vrp_z_confere_com_media_e_desvio_amostral_calculados_a_mao() -> None:
    # Janela de 5 (para caber na mão): [1, 2, 3, 4, 5].
    #   média    = 15/5 = 3
    #   desvios  = [-2, -1, 0, 1, 2]; Σ desvio² = 4+1+0+1+4 = 10
    #   variância amostral (n−1 = 4) = 2,5  →  desvio = √2,5 = 1,5811388301…
    #   z = (5 − 3)/1,5811388301… = 1,2649110640…
    assert vrp_z([1.0, 2.0, 3.0, 4.0, 5.0], window=5) == pytest.approx(
        2.0 / math.sqrt(2.5), rel=1e-12
    )
    assert vrp_z([1.0, 2.0, 3.0, 4.0, 5.0], window=5) == pytest.approx(1.2649110640, rel=1e-9)


def test_vrp_z_na_janela_cheia_de_252_confere_a_mao() -> None:
    # 251 valores iguais a 1 e o último igual a 2, com n = 252:
    #   média = (251·1 + 2)/252 = 253/252
    #   desvios: 251 × (−1/252) e um × (2 − 253/252) = 251/252
    #   Σ desvio² = (251 + 251²)/252² = 251·252/252² = 251/252
    #   variância amostral = (251/252)/(252−1) = 1/252  →  desvio = 1/√252
    #   z = (251/252)·√252 = 251/√252 = 15,8113883…
    series = [1.0] * 251 + [2.0]

    assert vrp_z(series) == pytest.approx(251 / math.sqrt(252), rel=1e-12)
    assert VRP_Z_WINDOW == 252


def test_vrp_z_sem_aquecimento_devolve_none_nunca_zero() -> None:
    assert vrp_z([1.0] * 251) is None
    assert vrp_z([], window=252) is None
    assert vrp_z([1.0, 2.0], window=1) is None  # n−1 = 0: desvio não existe


def test_vrp_z_de_serie_constante_devolve_none_nao_infinito() -> None:
    assert vrp_z([7.0] * 252) is None


def test_valor_nao_finito_na_janela_derruba_a_metrica() -> None:
    # NaN/±inf não pode virar média nem "percentil 0": a janela inteira falha e a
    # métrica é None (falha explícita, nunca NaN disfarçado de número).
    poisoned = [10.0] * 230 + [float("nan")] + [10.0 + 0.1 * i for i in range(21)]

    assert vrp_z(poisoned) is None
    assert iv_rank_252(poisoned) is None
    assert iv_percentile_252(poisoned) is None
    # fora da janela, não contamina: o inf é a observação 254 para trás e o corte
    # de 252 o descarta.
    assert vrp_z([float("inf")] + [10.0] * 251 + [11.0]) is not None


def test_vrp_z_nao_olha_o_futuro() -> None:
    """O z de hoje usa só as 252 observações que terminam hoje.

    Série sintética de 320 sessões: 200 dias de VRP 10 e 120 dias de VRP 20. No
    "dia 300" a janela de 252 tem 152 dias em 10 e 100 em 20 → média 13,97 e
    desvio 4,90 → z(20) = +1,23. No "dia 320" a janela tem 132 dias em 10 e 120
    em 20 → média 14,76, desvio 5,00 → z(20) = +1,05. Se a função olhasse o
    futuro, o z do dia 300 já viria contaminado com as observações 301–320 e os
    dois números seriam iguais; não são. E o z da série inteira é idêntico ao da
    janela `[-252:]`, então o passado fora da janela é descartado, não somado.
    """
    full = [10.0] * 200 + [20.0] * 120
    truncated = full[:300]

    z_full = vrp_z(full)
    z_truncated = vrp_z(truncated)

    assert z_full is not None
    assert z_truncated is not None
    assert z_truncated != pytest.approx(z_full, rel=1e-6)
    # Direção medida: a janela truncada tem menos dias "caros", logo o último valor
    # está mais longe da própria média → z maior.
    assert z_truncated > z_full
    assert z_truncated - z_full > 0.1
    # Janela para trás: o passado distante é descartado, não contaminado.
    assert vrp_z(full) == pytest.approx(vrp_z(full[-252:]), rel=1e-15, abs=1e-15)


# --- iv_rank_252 / iv_percentile_252 -----------------------------------------


def _series_252() -> list[float]:
    """252 observações: 1, 2, …, 251 e hoje = 25,5 (dentro da faixa, descentralizado)."""
    return [float(i) for i in range(1, 252)] + [25.5]


def test_iv_rank_252_e_a_posicao_min_max() -> None:
    # min = 1, max = 251, hoje = 25,5  →  100·(25,5 − 1)/(251 − 1) = 2450/250 = 9,8
    assert IV_WINDOW == 252
    assert iv_rank_252(_series_252()) == pytest.approx(9.8, rel=1e-12)


def test_iv_percentile_252_e_o_percentil_inclusivo_e_difere_do_rank() -> None:
    # #{v <= 25,5} = 25 (de 1 a 25) + 1 (o próprio hoje) = 26; 100·26/252 = 10,31746…
    # 10,31746… ≠ 9,8: são duas métricas, não uma (ADR-013).
    percentile = iv_percentile_252(_series_252())

    assert percentile == pytest.approx(100.0 * 26 / 252, rel=1e-12)
    assert percentile != pytest.approx(9.8, rel=1e-6)


def test_rank_e_percentil_sem_aquecimento_devolvem_none() -> None:
    short = [float(i) for i in range(1, 252)]  # 251 observações

    assert iv_rank_252(short) is None
    assert iv_percentile_252(short) is None
    assert iv_rank_252([]) is None
    assert iv_percentile_252([]) is None


def test_rank_de_faixa_degenerada_devolve_none_mas_percentil_nao() -> None:
    constant = [20.0] * 252

    assert iv_rank_252(constant) is None  # max == min: não há posição a informar
    assert iv_percentile_252(constant) == pytest.approx(100.0)  # todos <= hoje


def test_rank_e_percentil_ignoram_o_que_ficou_fora_da_janela() -> None:
    series = [999.0] * 100 + _series_252()  # 352 observações

    assert iv_rank_252(series) == pytest.approx(9.8, rel=1e-12)
    assert iv_percentile_252(series) == pytest.approx(100.0 * 26 / 252, rel=1e-12)
