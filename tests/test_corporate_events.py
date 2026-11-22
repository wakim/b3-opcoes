"""Ponte B2 → séries por papel: nada reclassificado, indeterminado fora.

Os eventos usados aqui são os do lake de 2026 (B2, 22/11/2026): SBSP ×0,2 em
29/04, SIMH ×2 em 18/02, os dois proventos da ABEV (4 ¢ e 6 ¢), LOGG R$ 2,86, e
indeterminados de verdade (SBSP 20/03, ITUB3, CMIG3). ``factor`` de escala é a
MÉDIA das razões medidas (0,20000430744063075 no SBSP) — comparação com tolerância.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from b3o.derive import strike_events
from b3o.derive.corporate_events import (
    DIVIDEND,
    SCALE,
    StrikeEventLike,
    dividend_dates,
    event_factor_series,
    scale_factors,
)
from b3o.derive.strike_events import INDETERMINATE, StrikeEvent

SBSP_SCALE = date(2026, 4, 29)
SBSP_MIXED = date(2026, 3, 20)
SIMH_SCALE = date(2026, 2, 18)
ABEV_FIRST = date(2026, 6, 23)
ABEV_SECOND = date(2026, 9, 22)
LOGG_DIV = date(2026, 6, 12)


def event(
    underlying: str,
    day: date,
    kind: str,
    *,
    factor: float | None = None,
    delta: int | None = None,
    n_series: int = 2,
) -> StrikeEvent:
    return StrikeEvent(
        underlying=underlying,
        date=day,
        kind=kind,
        delta_strike=delta,
        factor=factor,
        n_series=n_series,
        ambiguous=kind == INDETERMINATE,
    )


def lake_events() -> list[StrikeEvent]:
    return [
        event("SBSP3", SBSP_MIXED, INDETERMINATE, n_series=106),
        event("SBSP3", SBSP_SCALE, SCALE, factor=0.20000430744063075, n_series=106),
        event("SIMH3", SIMH_SCALE, SCALE, factor=2.0, n_series=7),
        event("ABEV3", ABEV_FIRST, DIVIDEND, delta=4, n_series=109),
        event("ABEV3", ABEV_SECOND, DIVIDEND, delta=6, n_series=121),
        event("LOGG3", LOGG_DIV, DIVIDEND, delta=286, n_series=6),
        event("ITUB3", date(2026, 3, 2), INDETERMINATE, n_series=1),
        event("CMIG3", date(2026, 5, 4), INDETERMINATE, n_series=1),
    ]


def test_constantes_espelham_o_b2() -> None:
    """A ponte não importa o B2 em runtime; o teste trava a divergência."""
    assert (DIVIDEND, SCALE) == (strike_events.DIVIDEND, strike_events.SCALE)
    assert set((DIVIDEND, SCALE, INDETERMINATE)) == {
        strike_events.DIVIDEND,
        strike_events.SCALE,
        strike_events.INDETERMINATE,
    }


def test_scale_factors_so_de_escala_e_por_papel_e_data() -> None:
    factors = scale_factors(lake_events())

    assert set(factors) == {"SBSP3", "SIMH3"}
    assert factors["SIMH3"] == {SIMH_SCALE: 2.0}
    assert set(factors["SBSP3"]) == {SBSP_SCALE}
    # média das razões, não 0,2 canônico — tolerância de propósito
    assert factors["SBSP3"][SBSP_SCALE] == pytest.approx(0.2, rel=1e-3)


def test_scale_factors_ignora_indeterminado_e_provento() -> None:
    """Indeterminado não tem classe e provento não tem fator de preço."""
    events: list[StrikeEventLike] = [
        event("XXXX3", SBSP_MIXED, INDETERMINATE, factor=0.5),
        event("XXXX3", ABEV_FIRST, DIVIDEND, factor=0.5, delta=4),
        event("XXXX3", SBSP_SCALE, SCALE, factor=1.5, n_series=3),
    ]

    factors = scale_factors(events)

    assert factors == {"XXXX3": {SBSP_SCALE: 1.5}}


def test_scale_factors_descarta_fator_ausente_ou_nao_positivo() -> None:
    """Sem fator não há o que ajustar; devolver 1,0 fingiria um ajuste não provado."""
    events = [
        event("AAAA3", SBSP_SCALE, SCALE, factor=None, n_series=2),
        event("AAAA3", SIMH_SCALE, SCALE, factor=0.0, n_series=2),
        event("AAAA3", ABEV_FIRST, SCALE, factor=-2.0, n_series=2),
        event("AAAA3", LOGG_DIV, SCALE, factor=float("nan"), n_series=2),
    ]

    assert scale_factors(events) == {}


def test_scale_factors_falha_em_dois_fatores_para_o_mesmo_dia() -> None:
    events = [
        event("AAAA3", SBSP_SCALE, SCALE, factor=0.5, n_series=2),
        event("AAAA3", SBSP_SCALE, SCALE, factor=2.0, n_series=2),
    ]

    with pytest.raises(ValueError, match="dois fatores"):
        scale_factors(events)


def test_scale_factors_colapsa_duplicata_identica() -> None:
    events = [
        event("AAAA3", SBSP_SCALE, SCALE, factor=0.5, n_series=2),
        event("AAAA3", SBSP_SCALE, SCALE, factor=0.5, n_series=2),
    ]

    assert scale_factors(events) == {"AAAA3": {SBSP_SCALE: 0.5}}


def test_dividend_dates_so_de_provento() -> None:
    dates = dividend_dates(lake_events())

    assert set(dates) == {"ABEV3", "LOGG3"}
    assert dates["ABEV3"] == frozenset({ABEV_FIRST, ABEV_SECOND})
    assert dates["LOGG3"] == frozenset({LOGG_DIV})
    assert isinstance(dates["ABEV3"], frozenset)


def test_eventos_vazios_nao_produzem_serie() -> None:
    assert scale_factors([]) == {}
    assert dividend_dates([]) == {}


def test_aceita_linha_do_parquet_alem_do_dataclass() -> None:
    """O consumidor pode passar a linha de ``core.strike_events`` sem recriar dataclass."""
    rows = [
        {
            "underlying": "SBSP3",
            "date": SBSP_SCALE,
            "kind": SCALE,
            "factor": 0.20000430744063075,
        },
        {"underlying": "ABEV3", "date": ABEV_FIRST, "kind": DIVIDEND, "factor": None},
        {"underlying": "SBSP3", "date": SBSP_MIXED, "kind": INDETERMINATE, "factor": None},
    ]

    factors = scale_factors(rows)
    dates = dividend_dates(rows)

    assert factors["SBSP3"][SBSP_SCALE] == pytest.approx(0.2, rel=1e-3)
    assert dates == {"ABEV3": frozenset({ABEV_FIRST})}


# --- event_factor_series ----------------------------------------------------


def test_event_factor_series_composto_por_papel() -> None:
    """Barra anterior a um evento recebe o fator; do dia em diante, só os futuros."""
    days = [date(2026, 1, d) for d in (5, 10, 15, 20, 25)]
    factors = {date(2026, 1, 10): 0.5, date(2026, 1, 20): 2.0}

    series = event_factor_series(days, factors)

    assert series[date(2026, 1, 5)] == pytest.approx(1.0)   # 0,5 × 2,0
    assert series[date(2026, 1, 10)] == pytest.approx(2.0)  # dia do 1º evento: escala nova
    assert series[date(2026, 1, 15)] == pytest.approx(2.0)
    assert series[date(2026, 1, 20)] == pytest.approx(1.0)  # dia do 2º evento
    assert series[date(2026, 1, 25)] == pytest.approx(1.0)


def test_event_factor_series_sem_evento_e_tudo_um() -> None:
    days = [date(2026, 1, 5), date(2026, 1, 6)]

    assert event_factor_series(days, {}) == {day: 1.0 for day in days}


def test_event_factor_series_usa_o_fator_medido_do_sbsp() -> None:
    """Desdobramento 1:5: as barras de março recebem 0,2000043…, não 1,0."""
    days = [date(2026, 3, 2), SBSP_SCALE, date(2026, 5, 4)]
    factors = scale_factors(lake_events())["SBSP3"]

    series = event_factor_series(days, factors)

    assert series[date(2026, 3, 2)] == pytest.approx(0.2, rel=1e-3)
    assert series[SBSP_SCALE] == 1.0
    assert series[date(2026, 5, 4)] == 1.0


def test_event_factor_series_fecha_com_a_razao_de_strike() -> None:
    """O fator que a ponte entrega é o multiplicador de preço do B2 (§6.6)."""
    factors = scale_factors(lake_events())

    assert math.log(factors["SIMH3"][SIMH_SCALE]) == pytest.approx(math.log(2.0))
    assert factors["SBSP3"][SBSP_SCALE] < 1.0
