"""Leituras tipadas do `QueryService` (Onda F): o que o pipeline de métricas consome.

O lake sintético é mínimo e cobre as armadilhas medidas:

- **papel pelo ISIN**, nunca pelo ticker (§4.1): a opção `PETRE410` acha `PETR4`
  pelo `isin_underlying`; a opção cujo ISIN não tem spot no dia **sai** do
  retorno (descarte documentado, não campo inventado);
- `010` tem precedência sobre `020` no spot (§6.1), e `020` serve de fallback;
- **o leg manda no strike** (§4.8): leg 4500 × raw 4100 → `strike=4500`,
  `strike_raw=4100`;
- `strike == 0` **não** é filtrado aqui (IBOV órfã): o pipeline é quem filtra;
- `series_history` corta nas últimas N sessões **do calendário do lake**;
- OI é `total_qty` (nunca `oi`/`OpnIntrst`, que vem vazio em opção de ação).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from b3o.cli import LEGS_SCHEMA, STRIKE_EVENTS_SCHEMA
from b3o.ingest.earnings import EarningsDate, write_earnings
from b3o.ingest.errors import SourceFailure
from b3o.ingest.open_interest import OIRow, save_open_interest
from b3o.ingest.parse import CotahistRow
from b3o.ingest.rates import RatePoint, write_rates_partition
from b3o.query.service import QueryService
from b3o.store.lake import write_cotahist_year

D17 = date(2026, 11, 17)
D18 = date(2026, 11, 18)
D19 = date(2026, 11, 19)
D20 = date(2026, 11, 20)  # sexta; não é sessão do lake sintético
MATURITY = date(2026, 12, 18)
PETR_ISIN = "BRPETRACNPR6"
VALE_ISIN = "BRVALEACNOR0"


def _row(refdate: date, **overrides: object) -> CotahistRow:
    base: dict[str, object] = {
        "refdate": refdate,
        "bdi": "02",
        "ticker": "PETR4",
        "tpmerc": "010",
        "corp_name": "PETROBRAS   PN",
        "spec": "ON      N2",
        "open": 3_900,
        "high": 4_050,
        "low": 3_880,
        "avg": 3_970,
        "close": 4_000,
        "best_bid": 3_999,
        "best_ask": 4_001,
        "trades": 50_000,
        "qty": 1_000_000,
        "volume": 4_000_000_000,
        "strike": 0,
        "strike_adj_ind": "0",
        "maturity": None,
        "quote_factor": 1,
        "strike_pts": 0,
        "isin_underlying": PETR_ISIN,
        "distribution_id": "",
        "style": None,
        "side": None,
    }
    base.update(overrides)
    return CotahistRow(**base)  # type: ignore[arg-type]


def _option(
    refdate: date,
    ticker: str,
    *,
    market: str = "070",
    strike: int,
    isin: str = PETR_ISIN,
    quote_factor: int = 1,
    close: int = 120,
) -> CotahistRow:
    return _row(
        refdate,
        bdi="78" if market == "070" else "82",
        ticker=ticker,
        tpmerc=market,
        corp_name="PETRE",
        spec="CALL      " if market == "070" else "PUT       ",
        close=close,
        best_bid=close - 1,
        best_ask=close + 1,
        trades=20,
        qty=500,
        volume=60_000,
        strike=strike,
        maturity=MATURITY,
        quote_factor=quote_factor,
        strike_pts=strike,
        isin_underlying=isin,
        distribution_id="168",
        style="E",
        side="C" if market == "070" else "P",
    )


def _leg(
    refdate: date,
    ticker: str,
    *,
    strike: int,
    side: str = "C",
    style: str = "E",
) -> dict[str, object]:
    return {
        "leg_id": f"{ticker}|{MATURITY.isoformat()}|0",
        "ticker": ticker,
        "maturity": MATURITY,
        "side": side,
        "style": style,
        "refdate": refdate,
        "leg_index": 0,
        "strike": strike,
        "distribution_id": "168",
        "flags": [],
    }


def _write_legs(data_dir: Path, rows: list[dict[str, object]]) -> None:
    target = data_dir / "legs" / "year=2026" / "part-0.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=LEGS_SCHEMA), target)


def _lake(data_dir: Path) -> None:
    """9 linhas de spot + 11 de opção em 3 sessões, legs e fontes auxiliares."""
    raw: list[CotahistRow] = []
    legs: list[dict[str, object]] = []

    for day in (D17, D18, D19):
        if day != D18:  # D18 só negociou no fracionário (teste do fallback 020)
            raw.append(_row(day))
        raw.append(_option(day, "PETRE410", strike=4_100, quote_factor=100))
        raw.append(_option(day, "PETRV352", market="080", strike=3_520))
        legs.append(_leg(day, "PETRE410", strike=4_500))
        legs.append(_leg(day, "PETRV352", strike=3_520, side="P"))

    # fracionário em D18; em D19 os dois mercados (010 tem que vencer)
    raw.append(_row(D18, tpmerc="020", close=4_010))
    raw.append(_row(D19, tpmerc="020", close=4_020))

    # papel que só tem fracionário no dia: resolve com underlying_market = 020
    raw.append(_row(D19, ticker="VALE3", tpmerc="020", close=6_000, isin_underlying=VALE_ISIN))
    raw.append(_option(D19, "VALEX100", strike=6_000, isin=VALE_ISIN))
    legs.append(_leg(D19, "VALEX100", strike=6_000))

    # órfã de grade (strike 0) continua voltando: quem filtra é o pipeline
    raw.append(_option(D19, "IBOVA189E5", strike=0))
    legs.append(_leg(D19, "IBOVA189E5", strike=0))

    # opção cujo ISIN não casa com nenhum spot do dia → fora do retorno
    raw.append(_option(D19, "XPTE999", strike=1_000, isin="BRXPTOACNOR1"))
    legs.append(_leg(D19, "XPTE999", strike=1_000))

    write_cotahist_year(raw, 2026, data_dir)
    _write_legs(data_dir, legs)

    write_rates_partition(
        [
            RatePoint(D19, 21, 13.72, "b3_reference"),
            RatePoint(D19, 1, 13.65, "b3_reference"),
        ],
        data_dir,
    )
    write_earnings(
        [
            EarningsDate("PETR4", date(2026, 9, 30), date(2026, 11, 10), "actual"),
            EarningsDate("PETR4", date(2025, 12, 31), date(2026, 3, 20), "actual"),
            EarningsDate("PETR4", date(2026, 12, 31), date(2027, 3, 20), "estimated"),
        ],
        data_dir,
    )
    save_open_interest(
        [
            OIRow(D19, "PETRE410", None, 100, 200, 300),
            OIRow(D19, "PETRV352", None, None, None, None),
        ],
        data_dir,
    )
    _write_events(data_dir)


def _write_events(data_dir: Path) -> None:
    target = data_dir / "strike_events" / "year=2026" / "part-0.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "underlying": "PETR4",
            "date": date(2026, 6, 12),
            "kind": "dividend",
            "delta_strike": 286,
            "factor": None,
            "n_series": 40,
            "ambiguous": False,
        },
        {
            "underlying": "SBSP3",
            "date": date(2026, 4, 29),
            "kind": "scale",
            "delta_strike": None,
            "factor": 0.20000430744063075,
            "n_series": 106,
            "ambiguous": False,
        },
        {
            "underlying": "ORVR3",
            "date": date(2026, 8, 11),
            "kind": "indeterminate",
            "delta_strike": None,
            "factor": None,
            "n_series": 1,
            "ambiguous": True,
        },
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=STRIKE_EVENTS_SCHEMA), target)


# --------------------------------------------------------------------------- #
# options_on
# --------------------------------------------------------------------------- #


def test_options_on_resolve_o_papel_pelo_isin_e_o_leg_manda_no_strike(
    tmp_path: Path,
) -> None:
    _lake(tmp_path)
    options = QueryService(tmp_path).options_on(D19)

    # XPTE999 não tem spot do dia: sai do retorno (descarte documentado)
    assert [option.ticker for option in options] == [
        "IBOVA189E5",
        "PETRE410",
        "PETRV352",
        "VALEX100",
    ]

    by_ticker = {option.ticker: option for option in options}
    call = by_ticker["PETRE410"]
    assert call.underlying == "PETR4"
    assert call.underlying_market == "010"  # 010 vence o 020 do mesmo dia
    assert call.spot_close == 4_000
    assert call.quote_factor == 100
    assert call.side == "C" and call.style == "E"
    # o leg é a autoridade; o raw fica exposto para o pipeline detectar divergência
    assert call.strike == 4_500
    assert call.strike_raw == 4_100
    assert call.leg_id == "PETRE410|2026-12-18|0"
    assert call.maturity == MATURITY
    assert call.refdate == D19
    assert (call.close, call.best_bid, call.best_ask, call.trades, call.volume) == (
        120,
        119,
        121,
        20,
        60_000,
    )

    put = by_ticker["PETRV352"]
    assert put.side == "P"
    assert put.strike == 3_520

    # fracionário como única fonte do dia → underlying_market = 020
    fracionario = by_ticker["VALEX100"]
    assert fracionario.underlying == "VALE3"
    assert fracionario.underlying_market == "020"
    assert fracionario.spot_close == 6_000

    # strike 0 não é filtrado aqui
    assert by_ticker["IBOVA189E5"].strike == 0


def test_options_on_sem_particao_de_legs_falha_explicito(tmp_path: Path) -> None:
    _lake(tmp_path)
    for path in (tmp_path / "legs").glob("year=*/part-0.parquet"):
        path.unlink()
    with pytest.raises(SourceFailure) as excinfo:
        QueryService(tmp_path).options_on(D19)
    assert excinfo.value.code == "lake_missing"


# --------------------------------------------------------------------------- #
# spot_bars
# --------------------------------------------------------------------------- #


def test_spot_bars_prefere_010_e_cai_para_020(tmp_path: Path) -> None:
    _lake(tmp_path)
    bars = QueryService(tmp_path).spot_bars("PETR4", D17, D19)

    assert [bar.refdate for bar in bars] == [D17, D18, D19]  # ordenado por data
    assert [bar.market for bar in bars] == ["010", "020", "010"]
    assert bars[1].close == 4_010  # só o fracionário de D18
    assert bars[2].close == 4_000  # 010 de D19, não o 020 (4.020)
    assert bars[0].high == 4_050


def test_spot_bars_sem_dado_e_lista_vazia(tmp_path: Path) -> None:
    _lake(tmp_path)
    service = QueryService(tmp_path)
    assert service.spot_bars("NAOEXISTE", D17, D19) == []
    # ano sem partição também é vazio (ausência de dado não é erro de leitura)
    assert service.spot_bars("PETR4", date(2025, 1, 1), date(2025, 12, 31)) == []


# --------------------------------------------------------------------------- #
# rates_curve / strike_events / earnings_rows
# --------------------------------------------------------------------------- #


def test_rates_curve_ordena_por_du_e_sem_particao_e_vazio(tmp_path: Path) -> None:
    _lake(tmp_path)
    service = QueryService(tmp_path)

    curve = service.rates_curve(D19)
    assert [node.du for node in curve] == [1, 21]
    assert curve[0].rate_annual == pytest.approx(13.65)
    assert curve[0].source == "b3_reference"

    assert service.rates_curve(D18) == []  # sessão sem curva
    assert service.rates_curve(date(2025, 6, 2)) == []  # ano sem partição


def test_strike_events_devolve_magnitude_e_none_em_scale(tmp_path: Path) -> None:
    _lake(tmp_path)
    service = QueryService(tmp_path)

    events = service.strike_events()
    assert [event.underlying for event in events] == ["SBSP3", "PETR4", "ORVR3"]
    by_underlying = {event.underlying: event for event in events}
    assert by_underlying["PETR4"].delta_strike == 286  # magnitude, sem sinal
    scale = by_underlying["SBSP3"]
    assert scale.delta_strike is None  # o lake não inventa zero
    assert scale.factor == pytest.approx(0.2000043, rel=1e-3)  # média das razões
    assert by_underlying["ORVR3"].ambiguous is True

    assert service.strike_events(years=[2025]) == []
    assert len(service.strike_events(years=[2026])) == 3


def test_earnings_rows_filtra_pelo_ano_da_data_do_resultado(tmp_path: Path) -> None:
    _lake(tmp_path)
    service = QueryService(tmp_path)

    assert len(service.earnings_rows()) == 3
    assert [row.date for row in service.earnings_rows(years=[2026])] == [
        date(2026, 3, 20),
        date(2026, 11, 10),
    ]
    assert service.earnings_rows(years=[2024]) == []


# --------------------------------------------------------------------------- #
# open_interest_on
# --------------------------------------------------------------------------- #


def test_open_interest_on_usa_total_qty(tmp_path: Path) -> None:
    _lake(tmp_path)
    service = QueryService(tmp_path)

    assert service.open_interest_on(D19) == {"PETRE410": 300}
    assert service.open_interest_on(D18) == {}
    assert service.open_interest_on(D17) == {}


# --------------------------------------------------------------------------- #
# series_history
# --------------------------------------------------------------------------- #


def test_series_history_corta_nas_ultimas_sessoes_do_calendario(tmp_path: Path) -> None:
    _lake(tmp_path)
    service = QueryService(tmp_path)

    last_two = service.series_history(["PETRE410", "NAOEXISTE"], D19, 2)
    assert [day.refdate for day in last_two["PETRE410"]] == [D18, D19]
    assert last_two["PETRE410"][-1].trades == 20
    assert last_two["NAOEXISTE"] == []  # entrada vazia, não KeyError

    # `end` que não é sessão (sexta 20/11) não inventa sessão
    assert [day.refdate for day in service.series_history(["PETRE410"], D20, 2)["PETRE410"]] == [
        D18,
        D19,
    ]

    # a janela maior devolve o recorte inteiro do lake
    assert [day.refdate for day in service.series_history(["PETRV352"], D19, 10)["PETRV352"]] == [
        D17,
        D18,
        D19,
    ]

    # `end` no meio do calendário corta de verdade
    assert [day.refdate for day in service.series_history(["PETRE410"], D18, 2)["PETRE410"]] == [
        D17,
        D18,
    ]

    assert service.series_history(["PETRE410"], D19, 0) == {"PETRE410": []}
