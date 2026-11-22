"""Health (F0.12): idade contada no **calendário do lake**, nada de zero silencioso.

O ponto central: `age_sessions` é a distância, em pregões, entre o que o
`ingest_state` diz ter carregado e a última sessão que o lake de fato tem. O
teste não usa relógio: monta três sessões sintéticas e move só o `last_session`
do estado.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from b3o.ingest.earnings import EarningsDate, write_earnings
from b3o.ingest.open_interest import OIRow, save_open_interest
from b3o.ingest.parse import CotahistRow
from b3o.ingest.rates import RatePoint, write_rates_partition
from b3o.query.service import (
    PrepareState,
    QueryService,
    load_prepare_state,
    prepare_state_path,
    save_prepare_state,
)
from b3o.store.lake import write_cotahist_year
from b3o.store.state import update_ingest_state

D17 = date(2026, 11, 17)
D18 = date(2026, 11, 18)
D19 = date(2026, 11, 19)
STAMP = datetime(2026, 11, 19, 22, 5, tzinfo=UTC)
ISIN = "BRPETRACNPR6"


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
        "isin_underlying": ISIN,
        "distribution_id": "",
        "style": None,
        "side": None,
    }
    base.update(overrides)
    return CotahistRow(**base)  # type: ignore[arg-type]


def _lake(data_dir: Path, *, last_state_session: date = D18, with_oi: bool = True) -> None:
    """Lake sintético: 3 sessões, 2 linhas por dia, estado e sidecar do prepare."""
    rows: list[CotahistRow] = []
    for day in (D17, D18, D19):
        rows.append(_row(day))
        rows.append(
            _row(
                day,
                bdi="78",
                ticker="PETRX360",
                tpmerc="070",
                corp_name="PETRE",
                spec="CALL      ",
                close=120,
                best_bid=119,
                best_ask=121,
                trades=20,
                qty=500,
                volume=60_000,
                strike=3_600,
                maturity=date(2026, 12, 18),
                quote_factor=1,
                strike_pts=3_600,
                distribution_id="168",
                style="E",
                side="C",
            )
        )
    write_cotahist_year(rows, 2026, data_dir)
    update_ingest_state(
        data_dir,
        updated_at=STAMP,
        etag='"abc:0"',
        last_modified="Thu, 19 Nov 2026 08:00:00 GMT",
        sha256="f" * 64,
        trailer_total=6,
        last_session=last_state_session,
        counts_by_date={D17: 2, D18: 2, D19: 2},
        skipped_lines={"width": 1, "numeric": 0},
        model_version=1,
    )
    save_prepare_state(
        data_dir,
        PrepareState(
            counts_by_market={"2026": {"010": 3, "017": 1, "070": 3, "030": 2}},
            universe=("PETR4", "VALE3"),
            universe_session=D19,
            last_error={
                "code": "source_unavailable",
                "message": "OI fora do ar",
                "hint": "tente de novo",
            },
            rss_peak_mib=612.5,
        ),
    )
    write_rates_partition(
        [RatePoint(D19, 1, 13.65, "b3_reference"), RatePoint(D19, 21, 13.72, "b3_reference")],
        data_dir,
    )
    write_earnings(
        [
            EarningsDate("PETR4", date(2026, 9, 30), date(2026, 11, 10), "actual"),
            EarningsDate("PETR4", date(2026, 12, 31), date(2026, 3, 20), "estimated"),
        ],
        data_dir,
    )
    if with_oi:
        save_open_interest(
            [OIRow(D19, "PETRX360", None, 100, 200, 300)],
            data_dir,
        )


def test_health_completo(tmp_path: Path) -> None:
    _lake(tmp_path)
    health = QueryService(tmp_path).health()

    assert health.last_session == "2026-11-19"
    # estado parado em 18/11: um pregão atrás da última sessão do lake
    assert health.age_sessions == 1
    assert health.lake_sessions == 3
    assert health.lake_years == [2026]
    assert health.counts_by_date == {
        "2026-11-17": 2,
        "2026-11-18": 2,
        "2026-11-19": 2,
    }
    assert health.counts_by_market == {"2026": {"010": 3, "017": 1, "070": 3, "030": 2}}
    assert health.discarded_by_market == {"017": 1, "030": 2}  # D3: contados, não sumidos
    assert health.skipped_lines == {"numeric": 0, "width": 1}  # ADR-007
    assert health.rate_source == {
        "refdate": "2026-11-19",
        "source": "b3_reference",
        "nodes": 2,
    }
    assert health.oi_available_dates == ["2026-11-19"]
    assert health.oi_unavailable is False
    assert health.earnings_coverage == {
        "universe": 2,
        "mapped": 1,
        "unmapped_count": 1,
        "unmapped_sample": ["VALE3"],
        "rows": 2,
    }
    assert health.universe == {"session": "2026-11-19", "count": 2, "tradable_count": 0}
    assert health.unexplained_gap is None  # F0 não tem RV: campo honesto
    assert health.last_error is not None and health.last_error["code"] == "source_unavailable"
    assert health.rss_peak_mib == 612.5
    assert health.model_version == 1
    assert health.as_of == STAMP.isoformat()


def test_age_sessions_zero_quando_o_estado_cobre_o_lake(tmp_path: Path) -> None:
    _lake(tmp_path, last_state_session=D19)
    assert QueryService(tmp_path).health().age_sessions == 0


def test_age_sessions_sem_estado_e_o_lake_inteiro(tmp_path: Path) -> None:
    """Sem `last_session` no estado, o state está atrás de tudo o que existe."""
    _lake(tmp_path)
    state_path = tmp_path / "ingest_state.json"
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw["last_session"] = None
    state_path.write_text(json.dumps(raw), encoding="utf-8")
    assert QueryService(tmp_path).health().age_sessions == 3


def test_health_sem_lake_nao_vira_zero(tmp_path: Path) -> None:
    """Lake vazio: cada item diz o erro; nada de "0 sessões" como se fosse verdade."""
    health = QueryService(tmp_path).health()
    payload = health.to_dict()

    assert payload["last_session"] is None
    assert payload["age_sessions"]["error"]["code"] == "lake_missing"
    assert payload["lake_sessions"]["error"]["code"] == "lake_missing"
    assert payload["counts_by_date"]["error"]["code"] == "lake_missing"
    assert payload["skipped_lines"]["error"]["code"] == "state_missing"
    assert payload["rate_source"]["error"]["code"] == "rates_missing"
    assert payload["counts_by_market"]["error"]["code"] == "prepare_state_missing"
    assert payload["oi_unavailable"] is True
    assert payload["oi_available_dates"]["error"]["code"] == "oi_missing"
    assert payload["earnings_coverage"]["error"]["code"] == "earnings_missing"
    assert payload["universe"]["error"]["code"] == "universe_missing"
    assert payload["last_error"] is None
    assert payload["rss_peak_mib"] is None
    assert payload["unexplained_gap"] is None
    assert payload["model_version"] == 1


def test_health_oi_marca_indisponivel_quando_a_ultima_sessao_nao_tem_oi(
    tmp_path: Path,
) -> None:
    """P-N: a flag existe para o consumidor não tratar ausência como zero."""
    _lake(tmp_path, with_oi=False)
    save_open_interest([OIRow(D18, "PETRX360", None, 1, 2, 3)], tmp_path)
    health = QueryService(tmp_path).health()
    assert health.oi_available_dates == ["2026-11-18"]
    assert health.oi_unavailable is True  # a última sessão (19/11) não tem OI


def test_lake_calendar_e_spot_tickers(tmp_path: Path) -> None:
    _lake(tmp_path)
    service = QueryService(tmp_path)
    assert service.lake_calendar(2026) == [D17, D18, D19]
    assert service.lake_calendar(2025) == []  # ano sem partição não é erro
    assert service.spot_tickers(2026) == ["PETR4"]
    assert service.counts_by_date(2026) == {D17: 2, D18: 2, D19: 2}


def test_open_interest_totals_filtra_datas(tmp_path: Path) -> None:
    _lake(tmp_path)
    save_open_interest(
        [OIRow(D18, "PETRX360", None, 100, 900, 1_000)],
        tmp_path,
    )
    service = QueryService(tmp_path)
    assert service.open_interest_dates() == [D18, D19]
    assert service.open_interest_totals([D18]) == {D18: {"PETRX360": 1_000}}
    assert service.open_interest_totals([date(2026, 11, 10)]) == {}


def test_prepare_state_roundtrip_e_tolerante(tmp_path: Path) -> None:
    state = PrepareState(
        counts_by_market={"2026": {"010": 10}},
        universe=("PETR4",),
        universe_session=D19,
        oi_date=D19,
        rss_peak_mib=1.5,
    )
    save_prepare_state(tmp_path, state)
    assert load_prepare_state(tmp_path) == state

    # JSON de versão antiga/torto não pode derrubar o health: degrada para default
    prepare_state_path(tmp_path).write_text("{nao é json", encoding="utf-8")
    assert load_prepare_state(tmp_path) == PrepareState()

    path = prepare_state_path(tmp_path)
    path.write_text(json.dumps({"counts_by_market": {"2026": {"010": "x"}}}), encoding="utf-8")
    assert load_prepare_state(tmp_path).counts_by_market == {"2026": {}}


def test_health_le_o_universo_do_sidecar_para_a_cobertura_de_earnings(
    tmp_path: Path,
) -> None:
    _lake(tmp_path)
    save_prepare_state(
        tmp_path,
        PrepareState(
            counts_by_market={"2026": {"010": 3}},
            universe=("PETR4", "VALE3", "BBAS3"),
            universe_session=D19,
        ),
    )
    coverage = QueryService(tmp_path).health().earnings_coverage
    assert coverage["universe"] == 3
    assert coverage["mapped"] == 1
    assert coverage["unmapped_count"] == 2
    assert coverage["unmapped_sample"] == ["BBAS3", "VALE3"]


@pytest.mark.parametrize("day", [D17, D18, D19])
def test_last_session_e_a_maior_data_do_lake(tmp_path: Path, day: date) -> None:
    """A data de referência vem do arquivo (P-C): nada de relógio no caminho."""
    write_cotahist_year([_row(day)], 2026, tmp_path)
    assert QueryService(tmp_path).health().last_session == day.isoformat()
