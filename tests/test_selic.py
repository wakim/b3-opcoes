"""Selic plana em lote (`ingest/selic.py`, §4.4).

O que este arquivo prova:

- **uma** chamada ao SGS 11 para N sessões (contador no `MockTransport`), com o
  intervalo em `DD/MM/AAAA`;
- anualização `((1 + 0.043739/100)**252 − 1) * 100 = 11,65% a.a.` — o número
  está calculado à mão aqui em cima e no teste, não derivado do código;
- feriado (sessão sem ponto) usa o **último valor anterior**, não zero;
- sessão que já tem curva PRE da B3 **não** é sobrescrita;
- intervalo sem ponto, sessão antes do primeiro ponto e lista vazia de sessões
  são falha explícita com `hint`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import httpx
import pytest

from b3o.ingest.errors import SourceFailure
from b3o.ingest.rates import FLAT_HORIZON_DU, RatePoint, write_rates_partition
from b3o.ingest.selic import backfill_selic_flat

D17 = date(2026, 11, 17)
D18 = date(2026, 11, 18)  # feriado: sem ponto no SGS
D19 = date(2026, 11, 19)

DAILY_PCT = 0.043739
# (1 + 0.00043739)**252 - 1 = 0.1164992 → 11,65% a.a.
ANNUAL_PCT = 11.6499


class Sgs:
    """SGS 11 falso: conta chamadas e devolve o JSON montado à mão."""

    def __init__(self, payload: list[dict[str, str]] | None = None) -> None:
        self.calls = 0
        self.urls: list[str] = []
        self.payload = (
            payload
            if payload is not None
            else [
                {"data": "17/11/2026", "valor": "0.043739"},
                {"data": "19/11/2026", "valor": "0.043739"},
            ]
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.urls.append(str(request.url))
        return httpx.Response(200, json=self.payload)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _curve_rows(data_dir: Path) -> dict[date, list[tuple[int, float, str]]]:
    path = data_dir / "rates_di" / "year=2026" / "part-0.parquet"
    assert path.is_file()
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT refdate, du, rate_annual, source FROM read_parquet(?) ORDER BY 1, 2",
            [str(path)],
        ).fetchall()
    curve: dict[date, list[tuple[int, float, str]]] = {}
    for refdate, du, rate, source in rows:
        curve.setdefault(refdate, []).append((du, rate, source))
    return curve


def test_backfill_uma_chamada_anualiza_e_usa_valor_anterior_no_feriado(
    tmp_path: Path,
) -> None:
    sgs = Sgs()
    with sgs.client() as client:
        summary = backfill_selic_flat([D19, D17, D18], client=client, data_dir=tmp_path)

    assert sgs.calls == 1  # uma chamada para o intervalo inteiro
    assert "api.bcb.gov.br" in sgs.urls[0]
    assert httpx.URL(sgs.urls[0]).params["dataInicial"] == "17/11/2026"
    assert httpx.URL(sgs.urls[0]).params["dataFinal"] == "19/11/2026"

    assert summary["sessions"] == 3
    assert summary["written"] == 3
    assert summary["skipped_existing"] == 0
    assert summary["source"] == "selic_flat"
    assert summary["errors"] == []
    assert set(summary["selic_pct"]) == {"2026-11-17", "2026-11-18", "2026-11-19"}
    assert summary["selic_pct"]["2026-11-18"] == DAILY_PCT

    curve = _curve_rows(tmp_path)
    assert set(curve) == {D17, D18, D19}
    for day, nodes in curve.items():
        assert len(nodes) == FLAT_HORIZON_DU
        assert [node[0] for node in nodes] == list(range(1, FLAT_HORIZON_DU + 1))
        assert {node[2] for node in nodes} == {"selic_flat"}
        assert nodes[0][1] == pytest.approx(ANNUAL_PCT, abs=0.01), day


def test_sessao_com_curva_pre_nao_e_sobrescrita(tmp_path: Path) -> None:
    write_rates_partition(
        [
            RatePoint(D18, 1, 13.65, "b3_reference"),
            RatePoint(D18, 21, 13.72, "b3_reference"),
        ],
        tmp_path,
    )
    sgs = Sgs()
    with sgs.client() as client:
        summary = backfill_selic_flat([D17, D18, D19], client=client, data_dir=tmp_path)

    assert summary["written"] == 2
    assert summary["skipped_existing"] == 1

    curve = _curve_rows(tmp_path)
    assert curve[D18] == [(1, 13.65, "b3_reference"), (21, 13.72, "b3_reference")]
    assert len(curve[D17]) == FLAT_HORIZON_DU
    assert {node[2] for node in curve[D19]} == {"selic_flat"}


def test_nada_a_fazer_nao_chama_o_bcb(tmp_path: Path) -> None:
    write_rates_partition([RatePoint(D19, 1, 13.65, "b3_reference")], tmp_path)
    sgs = Sgs()
    with sgs.client() as client:
        summary = backfill_selic_flat([D19], client=client, data_dir=tmp_path)

    assert sgs.calls == 0
    assert summary["written"] == 0
    assert summary["skipped_existing"] == 1
    assert summary["selic_pct"] == {}


def test_intervalo_sem_ponto_falha_com_hint(tmp_path: Path) -> None:
    sgs = Sgs(payload=[])
    with sgs.client() as client, pytest.raises(SourceFailure) as excinfo:
        backfill_selic_flat([D17], client=client, data_dir=tmp_path, retry_delays=())

    assert excinfo.value.code == "source_unavailable"
    assert excinfo.value.hint
    assert not (tmp_path / "rates_di").exists()


def test_sessao_antes_do_primeiro_ponto_falha_com_hint(tmp_path: Path) -> None:
    sgs = Sgs(payload=[{"data": "19/11/2026", "valor": "0.043739"}])
    with sgs.client() as client, pytest.raises(SourceFailure) as excinfo:
        backfill_selic_flat([D17, D19], client=client, data_dir=tmp_path, retry_delays=())

    assert excinfo.value.code == "source_unavailable"
    assert "2026-11-17" in excinfo.value.message
    assert excinfo.value.hint


def test_sem_sessao_falha_explicito(tmp_path: Path) -> None:
    sgs = Sgs()
    with sgs.client() as client, pytest.raises(SourceFailure) as excinfo:
        backfill_selic_flat([], client=client, data_dir=tmp_path)

    assert excinfo.value.code == "bad_data"
    assert sgs.calls == 0


def test_http_fora_do_ar_com_retry(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=[{"data": "19/11/2026", "valor": "0.043739"}])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        summary = backfill_selic_flat(
            [D19], client=client, data_dir=tmp_path, retry_delays=(0.0,)
        )

    assert calls["n"] == 2  # 5xx é retentado uma vez
    assert summary["written"] == 1
