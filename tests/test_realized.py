"""Yang-Zhang: referência independente, vol plantada e as armadilhas do dado.

A referência de cada teste é externa ao código sob teste: forma fechada tirada à
mão da equação (5) do paper, um caminho algébrico ALTERNATIVO (``u``/``d``
normalizados por ``O``, equações 3–4 do Yang & Zhang 2000) ou série sintética com
vol CONHECIDA. Nenhum esperado é gerado por ``yang_zhang_vol``.

A série sintética: preço log-normal com σ diária = vol/√252, decomposta em
overnight (peso 25%) e intraday (75%). O range do dia é o **bridge browniano
contínuo**, amostrado pelas marginais exatas do máximo/mínimo
(``P(M ≥ x) = exp(−2x(x−c)/σ²)``, ``x ≥ max(0,c)``; mínimo por simetria). Isso é o
modelo do paper (monitoramento contínuo), então o estimador é não-enviesado e a
vol nominal é a vol verdadeira — simular o range num caminho discreto de 12
passos subestima o range e o teste cobraria o viés da SIMULAÇÃO, não da fórmula
(medido: 12 passos recupera 25,2% de 30%; 1.000 passos, 29,2%).
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta

import pytest

from b3o.derive.corporate_events import event_factor_series
from b3o.derive.realized import (
    EX_DIV_EXCLUDED,
    INSUFFICIENT_HISTORY,
    INVALID_PRICE,
    WINDOWS,
    OhlcBar,
    flag_unexplained,
    yang_zhang_vol,
)

START = date(2026, 1, 2)


def bar(
    day: int,
    *,
    open: int,
    high: int,
    low: int,
    close: int,
    adj_factor: float = 1.0,
) -> OhlcBar:
    """Barra da sessão ``day`` contada a partir de ``START`` (calendário não entra)."""
    return OhlcBar(
        refdate=START + timedelta(days=day),
        open=open,
        high=high,
        low=low,
        close=close,
        adj_factor=adj_factor,
    )


def flat_bars(count: int, *, open: int, high: int, low: int, close: int) -> list[OhlcBar]:
    return [bar(day, open=open, high=high, low=low, close=close) for day in range(count)]


def synthetic_bars(
    count: int,
    *,
    vol: float = 0.30,
    seed: int = 7,
    overnight_share: float = 0.25,
    start_price: float = 10_000.0,
) -> list[OhlcBar]:
    """Série com vol anual CONHECIDA (``vol``) e range de Brownian bridge contínuo."""
    rng = random.Random(seed)
    sigma_daily = vol / math.sqrt(252)
    sigma_overnight = sigma_daily * math.sqrt(overnight_share)
    sigma_intraday = sigma_daily * math.sqrt(1.0 - overnight_share)
    variance_intraday = sigma_intraday * sigma_intraday

    close = start_price
    bars: list[OhlcBar] = []
    for day in range(count):
        open_ = close * math.exp(rng.gauss(0.0, sigma_overnight))
        bridge_end = rng.gauss(0.0, sigma_intraday)
        # máximo/mínimo marginais do bridge: x = (c ± √(c² − 2σ²·ln U)) / 2
        high_log = (
            bridge_end
            + math.sqrt(bridge_end * bridge_end - 2.0 * variance_intraday * math.log(rng.random()))
        ) / 2.0
        low_log = (
            bridge_end
            - math.sqrt(bridge_end * bridge_end - 2.0 * variance_intraday * math.log(rng.random()))
        ) / 2.0
        close = open_ * math.exp(bridge_end)

        open_cents = int(round(open_))
        close_cents = int(round(close))
        bars.append(
            OhlcBar(
                refdate=START + timedelta(days=day),
                open=open_cents,
                high=max(open_cents, close_cents, int(math.ceil(open_ * math.exp(high_log)))),
                low=min(open_cents, close_cents, int(math.floor(open_ * math.exp(low_log)))),
                close=close_cents,
            )
        )
    return bars


def reference_yang_zhang(bars: Sequence[OhlcBar]) -> float:
    """Yang-Zhang pelo caminho OPEN-NORMALIZADO do paper (equações 1–6).

    ``u_i = ln(H_i/O_i)`` e ``d_i = ln(L_i/O_i)``, ao contrário do módulo, que
    trabalha com ``ln(·/C_{i−1})``. Mesma matemática, álgebra diferente.
    """
    logs = [(math.log(b.open), math.log(b.high), math.log(b.low), math.log(b.close)) for b in bars]
    overnight: list[float] = []
    intraday: list[float] = []
    high: list[float] = []
    low: list[float] = []
    for index in range(1, len(logs)):
        previous_close = logs[index - 1][3]
        open_i, high_i, low_i, close_i = logs[index]
        overnight.append(open_i - previous_close)
        intraday.append(close_i - open_i)
        high.append(high_i - open_i)
        low.append(low_i - open_i)

    n = len(overnight)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))

    def sample_variance(values: Sequence[float]) -> float:
        mean = sum(values) / n
        return sum((value - mean) ** 2 for value in values) / (n - 1)

    rogers_satchell = (
        sum(
            high_i * (high_i - close_i) + low_i * (low_i - close_i)
            for high_i, low_i, close_i in zip(high, low, intraday, strict=True)
        )
        / n
    )
    variance = (
        sample_variance(overnight) + k * sample_variance(intraday) + (1.0 - k) * rogers_satchell
    )
    return 100.0 * math.sqrt(variance * 252.0)


# --- fórmula contra o paper -------------------------------------------------


def test_ratio_constante_do_range_bate_com_a_forma_fechada() -> None:
    """O=C com range de ±1%: só o Rogers-Satchell sobra, e ele entra com peso (1−k).

    σ²_o = 0 (overnight zero em toda barra), σ²_c = 0 (open = close) e
    σ²_rs = (1/N)Σ[(h−c)h + (l−c)l] = h² + l², com h = ln(1,01) e l = ln(0,99).
    N = 9 retornos (janela de 10) → k = 0,34/(1,34 + 10/8).
    """
    bars = flat_bars(10, open=1000, high=1010, low=990, close=1000)
    k = 0.34 / (1.34 + 10 / 8)
    expected = 100.0 * math.sqrt(
        252.0 * (1.0 - k) * (math.log(1.01) ** 2 + math.log(0.99) ** 2)
    )

    result = yang_zhang_vol(bars, 10)

    assert result.value == pytest.approx(expected, rel=1e-12)
    assert result.flags == ()
    assert result.n_bars == 10


def test_drift_nao_entra_na_vol() -> None:
    """Propriedade do paper (drift-independent): soma de drift não muda a vol.

    A mesma série com deriva de +5% a.a. e de −5% a.a. tem exatamente a mesma vol —
    Yang-Zhang mede variância, não tendência. (Arredondar centavo impede o teste
    degenerado de "preço × 1,01 por dia", onde o próprio arredondamento vira ruído.)
    """
    bars = synthetic_bars(120, seed=21)
    up = [
        replace(b, open=round(b.open * math.exp(0.05 * day / 252)),
                high=round(b.high * math.exp(0.05 * day / 252)),
                low=round(b.low * math.exp(0.05 * day / 252)),
                close=round(b.close * math.exp(0.05 * day / 252)))
        for day, b in enumerate(bars)
    ]
    down = [
        replace(b, open=round(b.open * math.exp(-0.05 * day / 252)),
                high=round(b.high * math.exp(-0.05 * day / 252)),
                low=round(b.low * math.exp(-0.05 * day / 252)),
                close=round(b.close * math.exp(-0.05 * day / 252)))
        for day, b in enumerate(bars)
    ]

    base = yang_zhang_vol(bars, 60)
    # tolerância de 1e-3: a deriva muda a ESCALA dos preços e o arredondamento
    # para centavo deixa ~1e-4 de diferença; a propriedade é exata em preço contínuo.
    assert yang_zhang_vol(up, 60).value == pytest.approx(base.value, rel=1e-3)
    assert yang_zhang_vol(down, 60).value == pytest.approx(base.value, rel=1e-3)


def test_serie_parada_tem_vol_zero_e_nao_none() -> None:
    """Vol zero de série parada é RESULTADO (não ausência): None só quando falta dado."""
    bars = flat_bars(60, open=1000, high=1000, low=1000, close=1000)

    result = yang_zhang_vol(bars, 60)

    assert result.value == 0.0
    assert result.flags == ()


def test_peso_k_e_rogers_satchell_pelo_calculo_de_mao() -> None:
    """N=19, σ²_o = σ²_c = 0 → o resultado é (1−k)·σ²_rs com k e RS escritos à mão.

    Cada barra: O=1000, C=1010, H=1020, L=990. Overnight constante (σ²_o = 0),
    open-to-close constante (σ²_c = 0) e só o termo de range sobra:
    σ²_rs = ln(1020/1010)·ln(1020/1000) + ln(990/1010)·ln(990/1000).
    N = 19 retornos → k = 0,34/(1,34 + 20/18).
    """
    bars = flat_bars(20, open=1000, high=1020, low=990, close=1010)
    rogers_satchell = math.log(1020 / 1010) * math.log(1020 / 1000) + math.log(
        990 / 1010
    ) * math.log(990 / 1000)
    k = 0.34 / (1.34 + 20 / 18)
    expected = 100.0 * math.sqrt(252.0 * (1.0 - k) * rogers_satchell)

    result = yang_zhang_vol(bars, 20)

    assert result.value == pytest.approx(expected, rel=1e-12)
    # sanidade do número: 29,3% a.a. — o range de ±2% do preço médio domina
    assert result.value == pytest.approx(29.32, abs=0.05)


def test_referencia_independente_pelo_caminho_open_normalizado() -> None:
    """Mesma série, álgebra do paper (u/d sobre O) — pega a troca de u por c."""
    bars = synthetic_bars(400, seed=3)
    expected = reference_yang_zhang(bars[-60:])

    result = yang_zhang_vol(bars, 60)

    assert result.value == pytest.approx(expected, rel=1e-12)


def test_escala_do_preco_nao_muda_a_vol() -> None:
    """Multiplicar todo preço por 7 não muda log-return nenhum."""
    bars = synthetic_bars(200, seed=5)
    scaled = [
        replace(b, open=b.open * 7, high=b.high * 7, low=b.low * 7, close=b.close * 7)
        for b in bars
    ]

    base = yang_zhang_vol(bars, 60)
    big = yang_zhang_vol(scaled, 60)

    assert big.value == pytest.approx(base.value, rel=1e-12)


# --- vol plantada -----------------------------------------------------------


def test_vol_de_30_por_cento_a_a_e_recuperada() -> None:
    """Série com σ = 30% a.a. plantada é recuperada em ±0,5 ponto de vol.

    Janela longa de propósito: com N = 19 o erro-padrão do estimador é da ordem de
    5 pontos de vol e ±0,5 não diria nada sobre o viés. A 1.500 retornos a média
    fica dentro de ±0,5 (aqui: 29,99%).
    """
    bars = synthetic_bars(2_000, vol=0.30, seed=7)

    result = yang_zhang_vol(bars, 1_500)

    assert result.flags == ()
    assert result.value == pytest.approx(30.0, abs=0.5)


def test_janelas_publicadas_20_30_60_tem_grandeza_certa() -> None:
    """As três janelas de produção devolvem a mesma ordem de grandeza (26–27% aqui)."""
    bars = synthetic_bars(2_000, vol=0.30, seed=7)

    assert WINDOWS == (20, 30, 60)
    for window in WINDOWS:
        result = yang_zhang_vol(bars, window)
        assert result.flags == ()
        assert result.n_bars == window
        assert result.value is not None
        assert 20.0 < result.value < 40.0


# --- falta de dado: None + flag, nunca zero mudo ----------------------------


def test_janela_incompleta_devolve_none_com_flag() -> None:
    bars = synthetic_bars(30, seed=1)

    result = yang_zhang_vol(bars, 60)

    assert result.value is None
    assert result.flags == (INSUFFICIENT_HISTORY,)
    assert result.n_bars == 30


def test_preco_zero_dentro_da_janela_e_invalid_price() -> None:
    bars = synthetic_bars(60, seed=1)
    bars[45] = replace(bars[45], close=0)

    result = yang_zhang_vol(bars, 20)

    assert result.value is None
    assert result.flags == (INVALID_PRICE,)


def test_preco_invalido_fora_da_janela_nao_derruba_a_estimativa() -> None:
    """Barra furada 50 sessões atrás não entra na conta de 20 — a janela é a janela."""
    bars = synthetic_bars(60, seed=1)
    bars[5] = replace(bars[5], close=0)

    result = yang_zhang_vol(bars, 20)

    assert result.flags == ()
    assert result.value is not None


def test_janela_menor_que_tres_e_erro_de_chamada() -> None:
    bars = synthetic_bars(10, seed=1)

    with pytest.raises(ValueError, match="janela mínima"):
        yang_zhang_vol(bars, 2)


def test_ordem_de_entrada_e_irrelevante() -> None:
    bars = synthetic_bars(120, seed=9)
    shuffled = bars[:]
    random.Random(42).shuffle(shuffled)

    assert yang_zhang_vol(shuffled, 60).value == pytest.approx(
        yang_zhang_vol(bars, 60).value, rel=1e-12
    )


def test_sessao_duplicada_fica_com_a_ultima_barra() -> None:
    """Partição reescrita duplicaria a sessão; o retorno fake encolheria a janela."""
    bars = synthetic_bars(60, seed=9)
    duplicated = bars[:30] + [replace(bars[29], open=bars[29].open + 50)] + bars[30:]
    last_wins = bars[:29] + [replace(bars[29], open=bars[29].open + 50)] + bars[30:]

    assert yang_zhang_vol(duplicated, 60).value == pytest.approx(
        yang_zhang_vol(last_wins, 60).value, rel=1e-12
    )


# --- desdobramento ----------------------------------------------------------


def test_desdobramento_1_para_3_sem_ajuste_explode() -> None:
    """Preço antigo 3× maior sem ``adj_factor``: o overnight de ln(1/3) domina a janela."""
    continuous = synthetic_bars(200, seed=11)
    split_at = 150
    split = [
        replace(b, open=b.open * 3, high=b.high * 3, low=b.low * 3, close=b.close * 3)
        if index < split_at
        else b
        for index, b in enumerate(continuous)
    ]

    base = yang_zhang_vol(continuous, 60)
    broken = yang_zhang_vol(split, 60)

    assert base.value == pytest.approx(30.0, abs=6.0)
    assert broken.flags == ()
    assert broken.value is not None
    assert broken.value > 3.0 * base.value  # explode, não "quase"


def test_desdobramento_1_para_3_com_adj_factor_volta_ao_valor_certo() -> None:
    """O fator do B2 (0,2 = strike novo/antigo) aplicado por barra desfaz o salto."""
    continuous = synthetic_bars(200, seed=11)
    split_at = 150
    split_date = continuous[split_at].refdate
    split = [
        replace(b, open=b.open * 5, high=b.high * 5, low=b.low * 5, close=b.close * 5)
        if index < split_at
        else b
        for index, b in enumerate(continuous)
    ]
    factors = event_factor_series([b.refdate for b in split], {split_date: 0.2})
    adjusted = [replace(b, adj_factor=factors[b.refdate]) for b in split]

    base = yang_zhang_vol(continuous, 60)
    with_factor = yang_zhang_vol(adjusted, 60)
    broken = yang_zhang_vol(split, 60)

    assert broken.value is not None and broken.value > 3.0 * base.value
    assert with_factor.flags == ()
    assert with_factor.value == pytest.approx(base.value, rel=1e-9)


def test_adj_factor_so_na_barra_anterior_ao_evento_ja_resolve() -> None:
    """A barra do dia do evento já está na escala nova: fator 1,0 nela, 1/3 antes."""
    bars = synthetic_bars(80, seed=13)
    event_day = bars[70].refdate
    factors = event_factor_series([b.refdate for b in bars], {event_day: 0.5})

    assert factors[event_day] == 1.0
    assert factors[bars[69].refdate] == 0.5
    assert factors[bars[0].refdate] == 0.5


# --- data ex de provento ----------------------------------------------------


def test_ex_div_exclui_observacao_sem_encolher_a_janela() -> None:
    """Provento de 8% plantado no meio da janela de 20 sai da conta e levanta a flag.

    Sem a exclusão a vol infla; com ela volta ao ~30% da série que gerou o dado, e
    ``n_bars`` fica em 21: a janela puxou uma barra mais para trás em vez de
    encolher.
    """
    bars = synthetic_bars(200, seed=17)
    ex_index = 190
    ex_date = bars[ex_index].refdate
    with_gap = [
        replace(b, open=round(b.open * 0.92), high=round(b.high * 0.92), low=round(b.low * 0.92),
                close=round(b.close * 0.92))
        if index >= ex_index
        else b
        for index, b in enumerate(bars)
    ]

    inflated = yang_zhang_vol(with_gap, 20)
    cleaned = yang_zhang_vol(with_gap, 20, ex_div_dates={ex_date})

    assert inflated.flags == ()
    assert cleaned.flags == (EX_DIV_EXCLUDED,)
    assert inflated.n_bars == 20
    assert cleaned.n_bars == 21
    assert inflated.value is not None and cleaned.value is not None
    assert inflated.value > cleaned.value + 2.0
    assert cleaned.value == pytest.approx(30.0, abs=4.0)


def test_ex_div_na_ultima_sessao_nao_conta_a_barra_descartada() -> None:
    """Excluir a última observação puxa a janela inteira para trás, sem inflar n_bars."""
    bars = synthetic_bars(60, seed=19)

    result = yang_zhang_vol(bars, 20, ex_div_dates={bars[-1].refdate})

    assert result.flags == (EX_DIV_EXCLUDED,)
    assert result.n_bars == 20
    assert result.value == pytest.approx(yang_zhang_vol(bars[:-1], 20).value, rel=1e-12)


def test_data_ex_fora_da_janela_nao_levanta_flag() -> None:
    """Evento de 100 sessões atrás não contamina a janela de 20 — nada a excluir."""
    bars = synthetic_bars(200, seed=17)

    result = yang_zhang_vol(bars, 20, ex_div_dates={bars[50].refdate})

    assert result.flags == ()
    assert result.n_bars == 20


def test_ex_div_sem_historico_para_puxar_e_insufficient_history() -> None:
    bars = synthetic_bars(20, seed=17)

    result = yang_zhang_vol(bars, 20, ex_div_dates={bars[10].refdate})

    assert result.value is None
    assert result.flags == (INSUFFICIENT_HISTORY,)


# --- rede de segurança do gap ----------------------------------------------


def test_flag_unexplained_acusa_so_o_gap_sem_evento() -> None:
    d = [START + timedelta(days=i) for i in range(1, 6)]
    returns = [
        (d[0], 0.45),    # sem evento → acusa
        (d[1], 0.45),    # evento no dia → explica
        (d[2], 0.30),    # abaixo do limiar
        (d[3], -0.55),   # gap negativo sem evento → acusa
        (d[4], 0.40),    # limiar é estrito ("> 40%")
    ]

    flagged = flag_unexplained(returns, {d[1]})

    assert flagged == [d[0], d[3]]


def test_flag_unexplained_nao_aceita_evento_na_vespera() -> None:
    """O evento do B2 é a sessão do gap; ``d−1`` não explica o overnight de ``d``.

    Medido no lake de 2026: aceitar a véspera esconderia um gap real na véspera de
    um evento (e não é o casamento que o dado mostra).
    """
    event_day = START + timedelta(days=5)
    previous = START + timedelta(days=4)

    flagged = flag_unexplained([(START, 0.60)], {previous})

    assert flagged == [START]
    assert flag_unexplained([(event_day, 0.60)], {event_day}) == []


def test_flag_unexplained_ordena_deduplica_e_aceita_limiar() -> None:
    d = [START + timedelta(days=i) for i in range(4)]
    returns = [(d[2], 0.31), (d[0], 0.31), (d[1], 0.31), (d[0], 0.31)]

    assert flag_unexplained(returns, set(), threshold=0.30) == [d[0], d[1], d[2]]
    assert flag_unexplained(returns, set()) == []
    assert flag_unexplained([], set()) == []
