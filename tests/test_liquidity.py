"""Liquidez por papel (F0.11, spec §6.9) — função pura, números conferidos à mão.

O que se prova: contagem de dias líquidos com a banda de moneyness, a janela de
5 pregões dos negócios, `one_sided` fora, `strike == 0` fora de toda conta, o
corte de R$ 5 M do universo e `days_to_liquidate` com `total_qty` do OI (e
`None` quando não há OI — nunca zero).
"""

from __future__ import annotations

from datetime import date, timedelta

from b3o.derive.liquidity import (
    BAND_HIGH,
    BAND_LOW,
    VOLUME_MIN_CENTS,
    LiquidityUnderlying,
    build_liquidity,
    render_report,
)
from b3o.ingest.parse import CotahistRow

ISIN = "BRPETRACNPR6"
D0 = date(2026, 10, 22)
D1 = date(2026, 11, 18)
D2 = date(2026, 11, 19)


def _row(refdate: date, **overrides: object) -> CotahistRow:
    """Linha padrão: PUT europeia de strike 40 com book apertado e 10 negócios."""
    base: dict[str, object] = {
        "refdate": refdate,
        "bdi": "82",
        "ticker": "PETRV400",
        "tpmerc": "080",
        "corp_name": "PETRE",
        "spec": "PUT       ",
        "open": 100,
        "high": 120,
        "low": 90,
        "avg": 100,
        "close": 100,
        "best_bid": 99,
        "best_ask": 101,
        "trades": 10,
        "qty": 100,
        "volume": 10_000,
        "strike": 4_000,
        "strike_adj_ind": "0",
        "maturity": date(2026, 12, 18),
        "quote_factor": 1,
        "strike_pts": 4_000,
        "isin_underlying": ISIN,
        "distribution_id": "168",
        "style": "E",
        "side": "P",
    }
    base.update(overrides)
    return CotahistRow(**base)  # type: ignore[arg-type]


def _spot(
    refdate: date,
    *,
    close: int = 4_000,
    ticker: str = "PETR4",
    tpmerc: str = "010",
    qty: int = 1_000_000,
    volume: int = 4_000_000_000,
) -> CotahistRow:
    """Linha à vista: R$ 40,00 e volume bem acima de R$ 5 M por padrão."""
    return _row(
        refdate,
        bdi="02",
        ticker=ticker,
        tpmerc=tpmerc,
        corp_name="PETROBRAS   PN",
        spec="ON      N2",
        close=close,
        best_bid=0,
        best_ask=0,
        trades=50_000,
        qty=qty,
        volume=volume,
        strike=0,
        maturity=None,
        style=None,
        side=None,
    )


def _liquid_day(
    refdate: date,
    *,
    strikes: int = 5,
    maturities: int = 2,
    spot_close: int = 4_000,
) -> list[CotahistRow]:
    """Um dia com `strikes` strikes líquidos em `maturities` vencimentos + o spot."""
    rows = [_spot(refdate, close=spot_close)]
    maturities_list = [date(2026, 12, 18), date(2027, 1, 15), date(2027, 2, 19)]
    for index in range(maturities * strikes):
        maturity = maturities_list[index % maturities]
        strike = 3_600 + (index // maturities) * 100  # 36,00 / 37,00 / … dentro da banda
        rows.append(
            _row(
                refdate,
                ticker=f"PETRV{strike}",
                maturity=maturity,
                strike=strike,
                strike_pts=strike,
            )
        )
    return rows


def test_dia_liquido_conta_uma_vez() -> None:
    rows = _liquid_day(D2)
    [paper] = build_liquidity(rows, {})
    assert paper.underlying == "PETR4"
    assert paper.liquid_days_20d == 1
    assert paper.avg_volume_brl_20d == 4_000_000_000
    assert paper.in_universe is True
    assert paper.days_to_liquidate is None  # sem OI: None, nunca zero


def test_menos_de_cinco_strikes_nao_e_dia_liquido() -> None:
    [paper] = build_liquidity(_liquid_day(D2, strikes=4), {})
    assert paper.liquid_days_20d == 0


def test_um_vencimento_so_nao_e_dia_liquido() -> None:
    [paper] = build_liquidity(_liquid_day(D2, maturities=1), {})
    assert paper.liquid_days_20d == 0


def test_strike_zero_fica_fora_da_banda() -> None:
    """6 strikes, um deles 0 (órfã de ajuste de grade do IBOV): sobram 5 → dia líquido."""
    rows = _liquid_day(D2, strikes=5)
    rows.append(_row(D2, ticker="PETRV000", strike=0, strike_pts=0))
    [paper] = build_liquidity(rows, {})
    assert paper.liquid_days_20d == 1


def test_strike_zero_com_menos_de_cinco_validos_nao_conta() -> None:
    rows = _liquid_day(D2, strikes=4)
    rows.extend(
        _row(D2, ticker=f"PETRV{i}000", strike=0, strike_pts=0, maturity=date(2027, 3, 19))
        for i in range(1)
    )
    [paper] = build_liquidity(rows, {})
    assert paper.liquid_days_20d == 0


def test_fora_da_banda_de_moneyness_nao_conta() -> None:
    """Strikes 2× o spot saem da banda: 5 na banda → líquido; 4 → não."""
    in_band = _liquid_day(D2)
    far = [
        _row(D2, ticker=f"PETRV{strike}", strike=strike, strike_pts=strike)
        for strike in (10_000, 20_000, 30_000)
    ]
    [paper] = build_liquidity(in_band + far, {})
    assert paper.liquid_days_20d == 1

    # tira um strike válido: os de fora da banda não podem compensar
    reduced = [row for row in in_band if row.ticker != "PETRV3600"]
    [paper] = build_liquidity(reduced + far, {})
    assert paper.liquid_days_20d == 0


def test_banda_aceita_os_limites() -> None:
    """K/S = 0,75 e 1,25 são exatamente os limites da banda (inclusivos)."""
    spot = 4_000
    rows = [_spot(D2, close=spot)]
    lows = int(spot * BAND_LOW)
    highs = int(spot * BAND_HIGH)
    targets = (lows, lows + 1, highs - 1, highs, 4_000)
    for index, strike in enumerate(targets):
        # 2 vencimentos distintos entre os 5 strikes (senão o dia não conta)
        maturity = date(2026, 12, 18) if index % 2 == 0 else date(2027, 1, 15)
        rows.append(
            _row(
                D2,
                ticker=f"PETRV{strike}",
                maturity=maturity,
                strike=strike,
                strike_pts=strike,
            )
        )
    [paper] = build_liquidity(rows, {})
    assert paper.liquid_days_20d == 1


def test_one_sided_nao_e_liquido() -> None:
    rows = _liquid_day(D2)
    # um dos strikes fica sem oferta → sobra 4 líquidos
    rows = [
        row if row.ticker != "PETRV4000" else _row(D2, ticker="PETRV4000", best_ask=0)
        for row in rows
    ]
    [paper] = build_liquidity(rows, {})
    assert paper.liquid_days_20d == 0


def test_spread_acima_de_10_por_cento_nao_e_liquido() -> None:
    rows = _liquid_day(D2)
    rows = [
        row
        if row.ticker != "PETRV4000"
        else _row(D2, ticker="PETRV4000", best_bid=80, best_ask=120)  # 40% do mid
        for row in rows
    ]
    [paper] = build_liquidity(rows, {})
    assert paper.liquid_days_20d == 0


def test_exige_cinco_negocios_na_janela() -> None:
    """`Σ trades ≥ 5` nos 5 pregões: 10/dia → todos os dias; 1/dia → só o 5º."""
    days = [D2 - timedelta(days=offset) for offset in range(4, -1, -1)]
    rows: list[CotahistRow] = []
    for day in days:
        rows.extend(_liquid_day(day))
    [paper] = build_liquidity(rows, {})
    assert paper.liquid_days_20d == len(days)

    thin: list[CotahistRow] = []
    for day in days:
        for row in _liquid_day(day):
            if row.tpmerc == "080":
                thin.append(
                    _row(
                        day,
                        ticker=row.ticker,
                        maturity=row.maturity,
                        strike=row.strike,
                        strike_pts=row.strike_pts,
                        trades=1,
                    )
                )
            else:
                thin.append(row)
    # 1 negócio por dia: a janela só fecha 5 a partir da quinta sessão do recorte
    [paper] = build_liquidity(thin, {})
    assert paper.liquid_days_20d == 1

    thinner: list[CotahistRow] = []
    for index, day in enumerate(days):
        for row in _liquid_day(day):
            if row.tpmerc == "080":
                # só um dos 5 dias tem 4 negócios: soma 4 < 5
                thinner.append(
                    _row(
                        day,
                        ticker=row.ticker,
                        maturity=row.maturity,
                        strike=row.strike,
                        strike_pts=row.strike_pts,
                        trades=4 if index == 0 else 0,
                    )
                )
            else:
                thinner.append(row)
    [paper] = build_liquidity(thinner, {})
    assert paper.liquid_days_20d == 0


def test_janela_de_20_sessoes_descarta_a_mais_antiga() -> None:
    """21 sessões: o volume médio e o dia líquido olham só as últimas 20."""
    days = [D0 + timedelta(days=offset) for offset in range(21)]
    rows: list[CotahistRow] = [_spot(days[0], volume=100 * 4_000_000_000)]
    rows.append(_liquid_day(days[0], strikes=5)[1:].__iter__().__next__())
    for day in days[1:]:
        rows.append(_spot(day, volume=4_000_000_000))
    rows.extend(_liquid_day(days[-1])[1:])  # 5 strikes líquidos só na última sessão
    [paper] = build_liquidity(rows, {})
    assert paper.liquid_days_20d == 1  # o dia com 5 strikes é o último
    # 20 sessões de 4e9 (a primeira, gigante, sai da janela)
    assert paper.avg_volume_brl_20d == 4_000_000_000


def test_volume_abaixo_de_5_milhoes_fica_fora_do_universo() -> None:
    rows = _liquid_day(D2)
    rows[0] = _spot(D2, volume=VOLUME_MIN_CENTS - 1, qty=1_000_000)
    [paper] = build_liquidity(rows, {})
    assert paper.in_universe is False
    assert paper.avg_volume_brl_20d == VOLUME_MIN_CENTS - 1


def test_days_to_liquidate_usa_total_qty_do_oi() -> None:
    """Σ total_qty ÷ quantidade média 20d: 1.000.000 ações ÷ 1.000.000/dia = 1,0 dia."""
    rows = _liquid_day(D2)
    oi = {D2: {"PETRV3600": 400_000, "PETRV3700": 600_000, "OUTRA9": 5_000_000}}
    [paper] = build_liquidity(rows, oi)
    assert paper.days_to_liquidate == 1.0


def test_days_to_liquidate_usa_a_ultima_data_de_oi() -> None:
    rows = _liquid_day(D2)
    oi = {
        D1: {"PETRV3600": 99_000_000},
        D2: {"PETRV3600": 500_000},
    }
    [paper] = build_liquidity(rows, oi)
    assert paper.days_to_liquidate == 0.5


def test_sem_oi_nao_vira_zero() -> None:
    [paper] = build_liquidity(_liquid_day(D2), {})
    assert paper.days_to_liquidate is None


def test_oi_sem_total_qty_nao_conta() -> None:
    [paper] = build_liquidity(_liquid_day(D2), {D2: {"PETRV3600": None}})  # type: ignore[dict-item]
    assert paper.days_to_liquidate is None


def test_francionario_serve_de_spot_quando_falta_o_lote_padrao() -> None:
    """Sem 010 no dia, o 020 dá o spot; os dois juntos nunca se somam (§6.1)."""
    rows = _liquid_day(D2)
    rows[0] = _spot(D2, tpmerc="020", close=4_000, volume=4_000_000_000)
    [paper] = build_liquidity(rows, {})
    assert paper.liquid_days_20d == 1
    assert paper.in_universe is True


def test_papel_sem_spot_no_dia_nao_tem_banda() -> None:
    """Sem spot não há K/S — e não se inventa: o papel nem entra na lista."""
    rows = _liquid_day(D2)[1:]  # só opções, nenhuma linha à vista
    assert build_liquidity(rows, {}) == []


def test_relatorio_tem_universo_no_topo_e_ressalva_de_moneyness() -> None:
    items = [
        LiquidityUnderlying(
            underlying="PETR4",
            liquid_days_20d=18,
            avg_volume_brl_20d=4_000_000_000,
            days_to_liquidate=1.25,
            in_universe=True,
        ),
        LiquidityUnderlying(
            underlying="AZUL4",
            liquid_days_20d=0,
            avg_volume_brl_20d=100_000_000,
            days_to_liquidate=None,
            in_universe=False,
        ),
    ]
    report = render_report(
        items,
        last_session=D2,
        first_session=D1,
        sessions=20,
        oi_date=D2,
    )
    assert "Universo: 1 papel(is)" in report
    assert "moneyness" in report and "F1" in report
    assert "PETR4" in report and "AZUL4" in report
    assert "| PETR4 | 18 | 40 000 000,00 | 1,25 | sim |" in report
    assert "| AZUL4 | 0 | 1 000 000,00 | — | não |" in report
