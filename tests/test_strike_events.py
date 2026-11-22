"""Eventos de strike: os 4 casos reais do contrato §8 + a assinatura do §6.6.

Fixture `fixtures/strike_events_2026_sample.txt` (recorte REAL do `COTAHIST_A2026.ZIP`,
latin-1, 245 bytes, CRLF) — ver o docstring de `test_legs.py` para o conteúdo.

Números medidos no arquivo inteiro de 2026 (não só no recorte), em 22/11/2026:

| Evento | Data (contígua) | Séries contíguas no ano | Assinatura |
|---|---|---|---|
| SBSP ×0,2 (desdobramento) | 2026-04-29 | 106 | razão 0,199967–0,200048 (spread 0,041%) |
| SIMH ×2 (grupamento) | 2026-02-18 | 7 | razão 2,000000 exata |
| LOGG R$ 2,86 (provento) | 2026-06-12 | 6 | Δ = −286 ¢ em todas |
| ABEV 4 ¢ (provento) | 2026-06-23 | 109 | Δ = −4 ¢ em todas |
| ABEV 6 ¢ (provento) | 2026-09-22 | 121 | Δ = −6 ¢ em todas |

O `ABEVX131` (12,04 → 12,00 → 11,94) é o exemplo perfeito da armadilha 3 do AGENTS:
a **série** não negociou no pregão anterior aos dois ajustes (15/06 → 23/06 e
18/09 → 22/09), então a mudança dela não data nada; quem data os eventos de 4 ¢ e
6 ¢ são as outras séries do papel, contíguas. Sem esse grupo, o ABEVX131 sozinho
não produziria evento nenhum.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import date
from pathlib import Path

from b3o.derive.strike_events import (
    DIVIDEND,
    INDETERMINATE,
    SCALE,
    StrikeEvent,
    classify_signature,
    classify_strike_events,
)
from b3o.ingest.parse import CotahistRow, parse_line

FIXTURE = Path(__file__).with_name("fixtures") / "strike_events_2026_sample.txt"


def fixture_rows() -> list[CotahistRow]:
    text = FIXTURE.read_bytes().decode("latin-1")
    parsed = (parse_line(line) for line in text.split("\r\n") if line)
    return [row for row in parsed if row is not None]


def base() -> CotahistRow:
    return next(row for row in fixture_rows() if row.tpmerc == "070")


def option(
    refdate: date,
    strike: int,
    *,
    ticker: str = "TESTE1",
    maturity: date = date(2026, 12, 18),
    isin: str = "BRTESTACNOR0",
    side: str = "C",
    tpmerc: str = "070",
) -> CotahistRow:
    return replace(
        base(),
        refdate=refdate,
        ticker=ticker,
        tpmerc=tpmerc,
        maturity=maturity,
        strike=strike,
        side=side,
        style="E",
        isin_underlying=isin,
        distribution_id="208",
        strike_adj_ind="0",
    )


def spot(
    refdate: date,
    *,
    ticker: str = "TEST3",
    isin: str = "BRTESTACNOR0",
    tpmerc: str = "010",
) -> CotahistRow:
    return replace(
        base(),
        refdate=refdate,
        ticker=ticker,
        tpmerc=tpmerc,
        maturity=None,
        strike=0,
        style=None,
        side=None,
        isin_underlying=isin,
    )


def events(rows: list[CotahistRow]) -> list[StrikeEvent]:
    return classify_strike_events(rows)


# --------------------------------------------------------------------------- #
# Os quatro casos reais do contrato §8
# --------------------------------------------------------------------------- #


def test_classify_e_funcao_pura_que_devolve_lista() -> None:
    assert not inspect.isgeneratorfunction(classify_strike_events)
    assert isinstance(events(fixture_rows()), list)


def test_eventos_do_recorte_real() -> None:
    """Exatamente 5 eventos no recorte: os 4 casos + o provento de 6 ¢ de setembro."""
    assert [(event.underlying, event.date, event.kind) for event in events(fixture_rows())] == [
        ("SIMH3", date(2026, 2, 18), SCALE),
        ("SBSP3", date(2026, 4, 29), SCALE),
        ("LOGG3", date(2026, 6, 12), DIVIDEND),
        ("ABEV3", date(2026, 6, 23), DIVIDEND),
        ("ABEV3", date(2026, 9, 22), DIVIDEND),
    ]


def test_sbsp_29_04_e_escala_vezes_02_e_nunca_dividendo() -> None:
    """SBSP 29/04/2026: ×0,2 em 106 séries — a armadilha do "Δ<0 = provento"."""
    event = next(
        event for event in events(fixture_rows()) if event.underlying == "SBSP3"
    )
    assert event.date == date(2026, 4, 29)
    assert event.kind == SCALE
    assert event.factor is not None and abs(event.factor - 0.2) < 1e-4
    assert event.n_series == 106
    assert event.delta_strike is None  # não é dividendo: nada entra em dividends_implied
    assert event.ambiguous is False


def test_simh_18_02_e_grupamento_vezes_2() -> None:
    event = next(event for event in events(fixture_rows()) if event.underlying == "SIMH3")
    assert event.date == date(2026, 2, 18)
    assert event.kind == SCALE
    assert event.factor == 2.0
    assert event.n_series == 7
    assert event.delta_strike is None
    assert event.ambiguous is False


def test_simh_19_02_nao_gera_evento_nao_contiguo() -> None:
    """25 séries mudam em 19/02 depois de uma lacuna: a data observada não é a ex."""
    datas = {event.date for event in events(fixture_rows()) if event.underlying == "SIMH3"}
    assert date(2026, 2, 19) not in datas
    assert datas == {date(2026, 2, 18)}


def test_logg_12_06_e_provento_de_286_centavos() -> None:
    event = next(event for event in events(fixture_rows()) if event.underlying == "LOGG3")
    assert event.date == date(2026, 6, 12)
    assert event.kind == DIVIDEND
    assert event.delta_strike == 286  # R$ 2,86
    assert event.factor is None
    assert event.n_series == 6
    assert event.ambiguous is False


def test_abevx131_dois_proventos_de_4_e_6_centavos() -> None:
    """12,04 → 12,00 (23/06) → 11,94 (22/09): dois proventos, datados pelas outras séries."""
    abev = [event for event in events(fixture_rows()) if event.underlying == "ABEV3"]
    assert [(event.date, event.kind, event.delta_strike) for event in abev] == [
        (date(2026, 6, 23), DIVIDEND, 4),
        (date(2026, 9, 22), DIVIDEND, 6),
    ]
    assert [event.n_series for event in abev] == [8, 8]  # amostra do recorte (ano: 109/121)


def test_abevx131_sozinho_nao_data_nada() -> None:
    """A série do ABEVX131 mudou em 23/06 e 22/09 sem ter negociado no pregão anterior.

    O calendário de sessões é **dado** (vem das linhas recebidas): por isso o insumo
    traz o spot do papel nos dias em que a série não negociou. Passar uma série só
    (sem o pregão de 22/06 e 21/09) encolheria o calendário e inventaria contiguidade
    — é o chamador que tem que entregar o recorte inteiro do ano.
    """
    rows = [row for row in fixture_rows() if row.ticker in ("ABEVX131", "ABEV3")]
    assert {row.refdate for row in rows} >= {date(2026, 6, 22), date(2026, 9, 21)}
    assert events(rows) == []


def test_abevx131_muda_entre_legs_mas_sem_evento_proprio() -> None:
    """O contraste que importa: a série tem 3 legs e o papel tem 2 eventos."""
    rows = fixture_rows()
    abev = [event for event in events(rows) if event.underlying == "ABEV3"]
    propria = [(row.refdate, row.strike) for row in rows if row.ticker == "ABEVX131"]
    assert propria == [
        (date(2026, 6, 15), 1204),
        (date(2026, 6, 23), 1200),
        (date(2026, 9, 18), 1200),
        (date(2026, 9, 22), 1194),
    ]
    assert [event.date for event in abev] == [date(2026, 6, 23), date(2026, 9, 22)]


# --------------------------------------------------------------------------- #
# Assinatura (§6.6) — função pura sobre números
# --------------------------------------------------------------------------- #


def test_delta_igual_e_provento_mesmo_com_razao_parecida() -> None:
    """Δ = −4 ¢ em strikes bem diferentes: as razões variam, o Δ não."""
    kind, delta, factor = classify_signature([(1204, 1200), (2000, 1996), (990, 986)])
    assert (kind, delta, factor) == (DIVIDEND, 4, None)


def test_razao_igual_com_delta_variando_e_escala() -> None:
    kind, delta, factor = classify_signature([(1000, 500), (2000, 1000), (4000, 2000)])
    assert kind == SCALE and delta is None and factor is not None
    assert abs(factor - 0.5) < 1e-12  # desdobramento: fator < 1


def test_razao_igual_maior_que_1_e_grupamento() -> None:
    kind, _, factor = classify_signature([(603, 1206), (703, 1406), (723, 1446)])
    assert kind == SCALE and factor == 2.0  # fator > 1


def test_ambos_cabem_e_provento() -> None:
    """Provento pequeno: Δ igual E razão quase igual. A spec manda decidir provento."""
    changes = [(10000, 9990), (20000, 19990), (30000, 29990)]
    assert classify_signature(changes) == (DIVIDEND, 10, None)


def test_um_strike_so_e_indeterminado() -> None:
    """Sem outro strike na mesma data não há assinatura (spec §6.6)."""
    assert classify_signature([(1000, 990)]) == (INDETERMINATE, None, None)
    assert classify_signature([]) == (INDETERMINATE, None, None)


def test_nenhuma_assinatura_e_indeterminada() -> None:
    """Nem Δ igual, nem razão igual (o SBSP de 20/03/2026 real tem essa cara)."""
    kind, delta, factor = classify_signature([(8977, 8894), (15000, 14907), (20070, 19969)])
    assert (kind, delta, factor) == (INDETERMINATE, None, None)


def test_delta_igual_mas_para_cima_nao_e_provento() -> None:
    """AGENTS.md, armadilha 2: strike ascendente é escala, nunca provento."""
    kind, delta, _ = classify_signature([(1000, 1100), (2000, 2100)])
    assert kind != DIVIDEND and delta is None


def test_strike_zero_nao_entra_na_assinatura() -> None:
    """Linha órfã de grade (IBOV): o 0 não é strike válido para classificar."""
    assert classify_signature([(0, 1000), (0, 1000)]) == (INDETERMINATE, None, None)
    # com um par válido e um zero, só o válido conta — e um par só não classifica
    assert classify_signature([(1000, 990), (0, 500)]) == (INDETERMINATE, None, None)


# --------------------------------------------------------------------------- #
# Contiguidade e agrupamento por papel
# --------------------------------------------------------------------------- #


def test_mudanca_nao_contigua_nao_gera_evento() -> None:
    """Série que ficou 3 pregões sem negócio e voltou com strike menor: sem data ex."""
    calendario = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 5)]
    rows = [spot(day) for day in calendario]
    rows.append(option(date(2026, 3, 2), 1000))
    rows.append(option(date(2026, 3, 5), 990))
    rows.append(spot(date(2026, 3, 6)))
    rows.append(option(date(2026, 3, 6), 990))
    assert events(rows) == []


def test_mudanca_contigua_data_o_evento() -> None:
    calendario = [date(2026, 3, 2), date(2026, 3, 3)]
    rows = [spot(day) for day in calendario]
    rows += [
        option(calendario[0], 1000, ticker="TESTE1"),
        option(calendario[0], 2000, ticker="TESTE2"),
        option(calendario[1], 990, ticker="TESTE1"),
        option(calendario[1], 1990, ticker="TESTE2"),
    ]
    assert events(rows) == [
        StrikeEvent(
            underlying="TEST3",
            date=date(2026, 3, 3),
            kind=DIVIDEND,
            delta_strike=10,
            factor=None,
            n_series=2,
            ambiguous=False,
        )
    ]


def test_papeis_diferentes_nao_se_misturam() -> None:
    """Um par do papel A não classifica o papel B: assinatura é entre strikes do MESMO papel."""
    calendario = [date(2026, 3, 2), date(2026, 3, 3)]
    rows = [
        spot(calendario[0], ticker="AAA3", isin="BRAAAACNOR0"),
        spot(calendario[1], ticker="AAA3", isin="BRAAAACNOR0"),
        spot(calendario[0], ticker="BBB3", isin="BRBBBACNOR0"),
        spot(calendario[1], ticker="BBB3", isin="BRBBBACNOR0"),
        # AAA: Δ igual em duas séries -> provento
        option(calendario[0], 1000, ticker="AAAAC1", isin="BRAAAACNOR0"),
        option(calendario[1], 990, ticker="AAAAC1", isin="BRAAAACNOR0"),
        option(calendario[0], 2000, ticker="AAAAC2", isin="BRAAAACNOR0"),
        option(calendario[1], 1990, ticker="AAAAC2", isin="BRAAAACNOR0"),
        # BBB: um strike só -> indeterminado
        option(calendario[0], 5000, ticker="BBBAC1", isin="BRBBBACNOR0"),
        option(calendario[1], 4995, ticker="BBBAC1", isin="BRBBBACNOR0"),
    ]
    result = events(rows)
    assert [(event.underlying, event.kind) for event in result] == [
        ("AAA3", DIVIDEND),
        ("BBB3", INDETERMINATE),
    ]
    assert [event.n_series for event in result] == [2, 1]
    assert [event.ambiguous for event in result] == [False, True]


def test_papel_desconhecido_usa_o_isin_e_marca_ambiguidade() -> None:
    """Sem linha `010` do ISIN não há ticker do ativo-objeto; o evento não desaparece."""
    calendario = [date(2026, 3, 2), date(2026, 3, 3)]
    rows = [
        option(calendario[0], 1000, ticker="ZZZAC1", isin="BRZZZACNOR9"),
        option(calendario[1], 990, ticker="ZZZAC1", isin="BRZZZACNOR9"),
        option(calendario[0], 2000, ticker="ZZZAC2", isin="BRZZZACNOR9"),
        option(calendario[1], 1990, ticker="ZZZAC2", isin="BRZZZACNOR9"),
        spot(calendario[0]),
        spot(calendario[1]),
    ]
    event = events(rows)[0]
    assert event.underlying == "BRZZZACNOR9"
    assert event.kind == DIVIDEND
    assert event.ambiguous is True


def test_linha_de_mercado_020_desempata_o_spot() -> None:
    """Preferência é `010`; `020` só quando o lote padrão não negociou (§6.1)."""
    calendario = [date(2026, 3, 2), date(2026, 3, 3)]
    rows = [
        spot(calendario[0], ticker="FRAC3", isin="BRFRACACNOR0", tpmerc="020"),
        spot(calendario[1], ticker="FRAC3", isin="BRFRACACNOR0", tpmerc="020"),
        spot(calendario[0], ticker="LOTE3", isin="BRLOTEACNOR0", tpmerc="010"),
        spot(calendario[1], ticker="LOTE3", isin="BRLOTEACNOR0", tpmerc="020"),
        option(calendario[0], 1000, ticker="FRACC1", isin="BRFRACACNOR0"),
        option(calendario[1], 990, ticker="FRACC1", isin="BRFRACACNOR0"),
        option(calendario[0], 2000, ticker="FRACC2", isin="BRFRACACNOR0"),
        option(calendario[1], 1990, ticker="FRACC2", isin="BRFRACACNOR0"),
        option(calendario[0], 3000, ticker="LOTEC1", isin="BRLOTEACNOR0"),
        option(calendario[1], 2990, ticker="LOTEC1", isin="BRLOTEACNOR0"),
        option(calendario[0], 4000, ticker="LOTEC2", isin="BRLOTEACNOR0"),
        option(calendario[1], 3990, ticker="LOTEC2", isin="BRLOTEACNOR0"),
    ]
    assert {event.underlying for event in events(rows)} == {"FRAC3", "LOTE3"}


def test_eventos_ordenados_por_data_e_papel() -> None:
    result = events(fixture_rows())
    assert result == sorted(result, key=lambda event: (event.date, event.underlying))
