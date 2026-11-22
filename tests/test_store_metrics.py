"""Escritores das tabelas de métrica (Onda F, contrato §4.3).

O que este arquivo prova:

- **ida e volta** pelo Parquet com o schema exato do contrato (nomes e tipos);
- `refdate_replaced` faz **merge** (remove só o dia pedido) e `None` **substitui**
  a partição — a idempotência do `--backfill`/`--date` depende disso;
- schema fechado: coluna extra, coluna faltando, tipo errado, `flags` como string,
  `flags` fora de ordem, domínio inválido e `NaN` são `SourceFailure bad_data`
  com a linha e o campo no `message` — nunca um Parquet divergente gravado;
- duas escritas iguais produzem o **mesmo `sha256`** (ordenação determinística +
  zstd), inclusive em data_dirs diferentes.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from b3o.ingest.errors import SourceFailure
from b3o.store.metrics import (
    METRICS_SERIES_COLUMNS,
    METRICS_UNDERLYING_COLUMNS,
    metrics_series_path,
    metrics_underlying_path,
    write_metrics_series,
    write_metrics_underlying,
)

D18 = date(2026, 11, 18)
D19 = date(2026, 11, 19)
D20 = date(2026, 11, 20)
MATURITY = date(2026, 12, 18)


def _series_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "refdate": D19,
        "ticker": "PETRE410",
        "maturity": MATURITY,
        "leg_id": "PETRE410|2026-12-18|0",
        "side": "C",
        "style": "E",
        "strike": 4_100,
        "t": 0.0794,
        "mid": 1.2,
        "quote_factor": 100,
        "iv": 32.5,
        "delta": 0.52,
        "gamma": 0.01,
        "theta_brl": -0.02,
        "theta_pct": -1.5,
        "vega_brl": 0.03,
        "rho": 0.01,
        "iv_pct_252": 0.4,
        "is_liquid": True,
        "flags": ["one_sided", "wide_spread"],
        "model_version": 1,
    }
    base.update(overrides)
    return base


def _underlying_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "refdate": D19,
        "underlying": "PETR4",
        "spot": 4_000.0,
        "underlying_market": "010",
        "iv_atm_30d": 31.2,
        "iv_rank_252": 0.55,
        "iv_pct_252": 60.0,
        "rv20": 27.0,
        "rv30": 28.5,
        "rv60": 29.0,
        "vrp": 2.7,
        "vrp_z_252": 0.8,
        "rr25": -3.1,
        "bfly25": 1.4,
        "ts_slope_30_90": 0.9,
        "exp_move_1s_30d": 0.077,
        "implied_div_yield_30d": 6.2,
        "days_to_liquidate": 8.95,
        "forward_30d": 4_012.0,
        "rate_source": "b3_reference",
        "signal_class": "none",
        "flags": [],
        "model_version": 1,
    }
    base.update(overrides)
    return base


def _read(path: Path) -> list[dict[str, object]]:
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY 1, 2", [str(path)]
        ).fetchall()
        names = [column[0] for column in connection.description]
    return [dict(zip(names, row, strict=True)) for row in rows]


# --------------------------------------------------------------------------- #
# Ida e volta
# --------------------------------------------------------------------------- #


def test_metrics_series_ida_e_volta(tmp_path: Path) -> None:
    stats = write_metrics_series(
        [_series_row(), _series_row(ticker="PETRV352", side="P", style=None, iv=None)],
        tmp_path,
    )

    path = metrics_series_path(tmp_path, 2026)
    assert stats.path == path
    assert stats.rows == 2
    assert stats.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

    table = pq.read_table(path)
    assert table.column_names == METRICS_SERIES_COLUMNS
    assert table.schema.field("strike").type.__str__() == "int64"
    assert table.schema.field("refdate").type.__str__() == "date32[day]"
    assert table.schema.field("flags").type == pa.list_(pa.string())

    rows = _read(path)
    call = rows[0]
    assert call["refdate"] == D19
    assert call["ticker"] == "PETRE410"
    assert call["strike"] == 4_100
    assert call["quote_factor"] == 100
    assert call["iv"] == pytest.approx(32.5)
    assert call["flags"] == ["one_sided", "wide_spread"]
    assert call["is_liquid"] is True
    put = rows[1]
    assert put["style"] is None  # `|null` do contrato
    assert put["iv"] is None


def test_metrics_underlying_ida_e_volta(tmp_path: Path) -> None:
    write_metrics_underlying([_underlying_row()], tmp_path)

    path = metrics_underlying_path(tmp_path, 2026)
    table = pq.read_table(path)
    assert table.column_names == METRICS_UNDERLYING_COLUMNS
    assert table.schema.field("spot").type.__str__() == "double"

    row = _read(path)[0]
    assert row["underlying"] == "PETR4"
    assert row["spot"] == pytest.approx(4_000.0)
    assert row["signal_class"] == "none"
    assert row["vrp"] == pytest.approx(2.7)  # pontos de vol, não %
    assert row["flags"] == []


# --------------------------------------------------------------------------- #
# Partição: merge por data × substituição
# --------------------------------------------------------------------------- #


def test_refdate_replaced_faz_merge_e_none_substitui_a_particao(tmp_path: Path) -> None:
    path = metrics_series_path(tmp_path, 2026)
    write_metrics_series(
        [
            _series_row(refdate=D18),
            _series_row(refdate=D18, ticker="PETRV352", side="P", iv=20.0),
        ],
        tmp_path,
    )
    assert len(_read(path)) == 2

    # dia novo entra sem tocar no 18/11
    write_metrics_series([_series_row(refdate=D19)], tmp_path, refdate_replaced=D19)
    rows = _read(path)
    assert [row["refdate"] for row in rows] == [D18, D18, D19]

    # recalcular o mesmo dia substitui só ele (idempotente em contagem)
    write_metrics_series(
        [_series_row(refdate=D19, iv=99.0)], tmp_path, refdate_replaced=D19
    )
    rows = _read(path)
    assert len(rows) == 3
    assert [row["iv"] for row in rows if row["refdate"] == D19] == [99.0]
    assert [row["iv"] for row in rows if row["refdate"] == D18] == [32.5, 20.0]

    # `None` substitui a partição inteira (--backfill de um recorte)
    write_metrics_series([_series_row(refdate=D20)], tmp_path)
    rows = _read(path)
    assert [(row["refdate"], row["ticker"]) for row in rows] == [(D20, "PETRE410")]


def test_merge_recusa_datas_misturadas(tmp_path: Path) -> None:
    write_metrics_series([_series_row(refdate=D18)], tmp_path)
    with pytest.raises(SourceFailure) as excinfo:
        write_metrics_series(
            [_series_row(refdate=D18), _series_row(refdate=D19)],
            tmp_path,
            refdate_replaced=D19,
        )
    assert excinfo.value.code == "bad_data"
    assert "refdate" in excinfo.value.message


# --------------------------------------------------------------------------- #
# Schema fechado
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"oi": 10}, "extra"),
        ({"mid": None}, "mid"),
        ({"strike": "4100"}, "strike"),
        ({"flags": "one_sided"}, "flags"),
        ({"flags": ["wide_spread", "one_sided"]}, "flags"),
        ({"flags": ["x", "x"]}, "flags"),
        ({"side": "X"}, "side"),
        ({"style": "B"}, "style"),
        ({"model_version": 0}, "model_version"),
        ({"quote_factor": 0}, "quote_factor"),
        ({"iv": float("nan")}, "iv"),
        ({"refdate": "2026-11-19"}, "refdate"),
        ({"is_liquid": 1}, "is_liquid"),
    ],
)
def test_metrics_series_recusa_linha_fora_do_contrato(
    tmp_path: Path, overrides: dict[str, object], detail: str
) -> None:
    with pytest.raises(SourceFailure) as excinfo:
        write_metrics_series([_series_row(**overrides)], tmp_path)
    error = excinfo.value.error
    assert error.code == "bad_data"
    assert detail in error.message
    assert "linha 0" in error.message
    assert error.hint
    # nada foi gravado: a partição divergente não existe
    assert not metrics_series_path(tmp_path, 2026).exists()


def test_metrics_series_recusa_lista_vazia(tmp_path: Path) -> None:
    with pytest.raises(SourceFailure) as excinfo:
        write_metrics_series([], tmp_path)
    assert excinfo.value.error.code == "bad_data"


def test_metrics_underlying_recusa_classe_e_mercado_invalidos(tmp_path: Path) -> None:
    for overrides, detail in (
        ({"signal_class": "rich"}, "signal_class"),
        ({"underlying_market": "030"}, "underlying_market"),
        ({"rate_source": None}, "rate_source"),
        ({"spot": 0.0}, "spot"),
        ({"exp_move_1s_30d": float("inf")}, "exp_move_1s_30d"),
    ):
        with pytest.raises(SourceFailure) as excinfo:
            write_metrics_underlying([_underlying_row(**overrides)], tmp_path)
        assert excinfo.value.error.code == "bad_data"
        assert detail in excinfo.value.error.message


# --------------------------------------------------------------------------- #
# Determinismo
# --------------------------------------------------------------------------- #


def test_duas_escritas_iguais_produzem_o_mesmo_sha(tmp_path: Path) -> None:
    rows = [
        _series_row(ticker="PETRE410"),
        _series_row(ticker="PETRE450", strike=4_500),
        _series_row(ticker="PETRV352", side="P"),
    ]
    first = write_metrics_series(rows, tmp_path / "a")
    second = write_metrics_series(rows, tmp_path / "b")
    assert first.sha256 == second.sha256

    again = write_metrics_series(rows, tmp_path / "a")
    assert again.sha256 == first.sha256  # reescrever o mesmo dia não muda bytes
    assert again.rows == 3


def test_ordena_por_refdate_e_ticker(tmp_path: Path) -> None:
    write_metrics_series(
        [
            _series_row(refdate=D19, ticker="PETRV352", side="P"),
            _series_row(refdate=D18, ticker="PETRV352", side="P"),
            _series_row(refdate=D18, ticker="PETRE410"),
        ],
        tmp_path,
    )
    rows = _read(metrics_series_path(tmp_path, 2026))
    assert [(row["refdate"], row["ticker"]) for row in rows] == [
        (D18, "PETRE410"),
        (D18, "PETRV352"),
        (D19, "PETRV352"),
    ]
