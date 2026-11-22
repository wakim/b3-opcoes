"""Testes da curva DI (F0.9) — cascata, `rate_source` e parquet, sem rede real.

`httpx.MockTransport` cobre as três fontes: o proxy de taxas referenciais da B3
(CSV em base64 dentro de 200), o SGS 11 do BCB (JSON) e as falhas (4xx/5xx,
transporte, corpo torto). O que se prova é o contrato de `onda-b.md` §4: a
primeira fonte que responde vence, `source` sempre gravado, ausência de data
cascateia (não é erro) e falha total vira `RatesResult.error` sem exceção.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from b3o.ingest.fetch import BROWSER_UA
from b3o.ingest.rates import (
    FLAT_HORIZON_DU,
    RATES_SCHEMA,
    RATES_TABLE,
    RatePoint,
    fetch_rates_di,
)

DATE = date(2026, 11, 19)
HEADER = "Descrição da Taxa;Dias Úteis;Dias Corridos;Preço/Taxa"
PRE_ROWS = (
    "DI x pré;1;4;13,65",
    "DI x pré;14;21;13,70",
    "DI x pré;21;32;13,72",
    "DI x pré;41;62;13,76",
)
# Recorte REAL do arquivo de 19/11/2026 (baixado do proxy em 22/11/2026): o
# golden do contrato §8 é 1 du = 13,65 / 21 du = 13,72 / 41 du = 13,76.
REAL_PRE_LINES = ("DI x pré;1;4;13,65", "DI x pré;21;32;13,72", "DI x pré;41;62;13,76")
# Selic a.d. de 19/11/2026 (SGS 11, valor literal devolvido pelo BCB).
SELIC_DAILY_PCT = 0.050788
# Conferido à mão: ((1 + 0.050788/100)**252 - 1) * 100
SELIC_ANNUAL_PCT = 13.649989315282562


def _b64_text(text: str) -> bytes:
    """CSV como o proxy devolve: base64 de um texto latin-1 com CRLF."""
    return base64.b64encode(text.encode("latin-1"))


def _csv_b64(*rows: str) -> bytes:
    return _b64_text(HEADER + "\r\n" + "".join(f"{row}\r\n" for row in rows))


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def _payload_of(request: httpx.Request) -> dict[str, str]:
    """Decodifica o b64 do caminho: prova que a URL saiu como o proxy espera."""
    encoded = request.url.path.rsplit("/", 1)[-1]
    return json.loads(base64.b64decode(encoded))


def _partition(data_dir: Path, year: int = 2026) -> Path:
    return data_dir / RATES_TABLE / f"year={year}" / "part-0.parquet"


def test_pre_disponivel_vira_b3_reference_com_du_do_csv(tmp_path: Path) -> None:
    seen: list[tuple[str, dict[str, str], str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, _payload_of(request), request.headers.get("user-agent")))
        return httpx.Response(200, content=_csv_b64(*PRE_ROWS))

    with _client(handler) as client:
        result = fetch_rates_di(DATE, client=client, data_dir=tmp_path)

    assert result.error is None
    assert result.source_final == "b3_reference"
    assert [p.du for p in result.points] == [1, 14, 21, 41]
    assert result.points[0] == RatePoint(
        refdate=DATE, du=1, rate_annual=13.65, source="b3_reference"
    )
    assert result.points[-1].rate_annual == 13.76
    assert all(p.refdate == DATE for p in result.points)

    # Uma chamada só: com PRE o APR nem é consultado.
    assert len(seen) == 1
    host, payload, ua = seen[0]
    assert host == "sistemaswebb3-derivativos.b3.com.br"
    assert payload == {"language": "pt-br", "date": "2026-11-19", "id": "PRE"}
    assert ua == BROWSER_UA  # sem UA de browser a B3 responde 520/404

    assert result.path == _partition(tmp_path)


def test_dias_corridos_nao_entra_na_curva(tmp_path: Path) -> None:
    """`Dias Corridos` não é derivável de `Dias Úteis` (19/11: du 1 → 4) e é ignorado."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_csv_b64("DI x pré;1;999;13,65"))

    with _client(handler) as client:
        result = fetch_rates_di(DATE, client=client, data_dir=tmp_path)

    assert [(p.du, p.rate_annual) for p in result.points] == [(1, 13.65)]


@pytest.mark.parametrize("pre_status", [404, 200])
def test_sem_pre_cai_para_di1(tmp_path: Path, pre_status: int) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _payload_of(request)
        calls.append(payload["id"])
        if payload["id"] == "PRE":
            # 404 e corpo vazio são as duas formas medidas de "sem dado".
            body = b"" if pre_status == 200 else None
            return httpx.Response(pre_status, content=body)
        return httpx.Response(200, content=_csv_b64("Ajuste pré;1;4;13,66"))

    with _client(handler) as client:
        result = fetch_rates_di(DATE, client=client, data_dir=tmp_path)

    assert calls == ["PRE", "APR"]  # sem dado no PRE não impede consultar o APR
    assert result.error is None
    assert result.source_final == "di1_futures"
    assert [p.rate_annual for p in result.points] == [13.66]
    assert all(p.source == "di1_futures" for p in result.points)


def test_csv_torto_no_pre_cascateia_sem_matar_o_dia(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _payload_of(request)
        calls.append(payload["id"])
        if payload["id"] == "PRE":
            return httpx.Response(200, content=base64.b64encode(b"resposta inesperada"))
        return httpx.Response(200, content=_csv_b64("Ajuste pré;21;32;13,72"))

    with _client(handler) as client:
        result = fetch_rates_di(DATE, client=client, data_dir=tmp_path)

    assert calls == ["PRE", "APR"]
    assert result.error is None
    assert result.source_final == "di1_futures"
    assert result.points[0].rate_annual == 13.72


def test_b3_fora_do_ar_cai_para_selic_flat(tmp_path: Path) -> None:
    seen_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "GetDownloadFile" in request.url.path:
            return httpx.Response(500)
        seen_params.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=[
                {"data": "18/11/2026", "valor": "0.050788"},
                {"data": "19/11/2026", "valor": "0.050788"},
            ],
        )

    with _client(handler) as client:
        result = fetch_rates_di(DATE, client=client, data_dir=tmp_path)

    assert seen_params == {
        "formato": "json",
        "dataInicial": "20/10/2026",  # date_ref - 30 dias
        "dataFinal": "19/11/2026",
    }
    assert result.error is None
    assert result.source_final == "selic_flat"
    assert all(p.source == "selic_flat" for p in result.points)
    assert [p.du for p in result.points] == list(range(1, FLAT_HORIZON_DU + 1))

    expected = ((1 + SELIC_DAILY_PCT / 100) ** 252 - 1) * 100
    assert result.points[0].rate_annual == pytest.approx(expected, rel=0, abs=1e-12)
    # Golden conferido à mão (não é o valor da fórmula recalculado no teste).
    assert result.points[0].rate_annual == pytest.approx(SELIC_ANNUAL_PCT, rel=0, abs=1e-9)
    assert {p.rate_annual for p in result.points} == {result.points[0].rate_annual}


def test_selic_sem_ponto_no_dia_usa_ultimo_valor_anterior(tmp_path: Path) -> None:
    """Sábado não tem ponto no SGS: usa o último valor anterior, refdate fica o dia."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "GetDownloadFile" in request.url.path:
            return httpx.Response(200, content=b"")
        return httpx.Response(
            200,
            json=[
                {"data": "18/11/2026", "valor": "0.050788"},
                {"data": "19/11/2026", "valor": "0.050788"},
            ],
        )

    with _client(handler) as client:
        result = fetch_rates_di(date(2026, 11, 21), client=client, data_dir=tmp_path)

    assert result.source_final == "selic_flat"
    assert all(p.refdate == date(2026, 11, 21) for p in result.points)
    assert result.points[0].rate_annual == pytest.approx(SELIC_ANNUAL_PCT, rel=0, abs=1e-9)
    assert _partition(tmp_path).is_file()


def test_tudo_falha_vira_error_sem_excecao_e_sem_arquivo(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with _client(handler) as client:
        result = fetch_rates_di(DATE, client=client, data_dir=tmp_path)

    assert result.points == ()
    assert result.source_final is None
    assert result.path is None
    assert result.error is not None
    assert result.error.code == "source_unavailable"
    assert "2026-11-19" in result.error.message
    assert "b3_reference" in result.error.message
    assert "di1_futures" in result.error.message
    assert "selic_flat" in result.error.message
    assert result.error.hint  # nunca erro sem dica (regra 5 do AGENTS.md)
    assert not _partition(tmp_path).exists()


def test_falha_de_rede_nao_escapa(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rota", request=request)

    with _client(handler) as client:
        result = fetch_rates_di(DATE, client=client, data_dir=tmp_path)

    assert result.error is not None
    assert result.error.code == "source_unavailable"
    assert "ConnectError" in result.error.message
    assert not _partition(tmp_path).exists()


def test_sgs_vazio_vira_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "GetDownloadFile" in request.url.path:
            return httpx.Response(200, content=b"")
        return httpx.Response(200, json=[])

    with _client(handler) as client:
        result = fetch_rates_di(DATE, client=client, data_dir=tmp_path)

    assert result.error is not None
    assert "selic_flat" in result.error.message
    assert "SGS 11" in result.error.message


def test_parquet_round_trip_tipos_valores_e_idempotencia(tmp_path: Path) -> None:
    curves = {
        "2026-11-18": ["DI x pré;1;3;13,64", "DI x pré;21;31;13,71"],
        "2026-11-19": ["DI x pré;1;4;13,65", "DI x pré;21;32;13,72"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _payload_of(request)
        return httpx.Response(200, content=_csv_b64(*curves[payload["date"]]))

    def run(client: httpx.Client, when: date):
        return fetch_rates_di(when, client=client, data_dir=tmp_path)

    with _client(handler) as client:
        first = run(client, date(2026, 11, 19))
        again = run(client, date(2026, 11, 19))  # idempotente por dia
        second = run(client, date(2026, 11, 18))

    target = _partition(tmp_path)
    assert first.path == again.path == second.path == target
    table = pq.read_table(target)

    assert table.schema.field("refdate").type == pa.date32()
    assert table.schema.field("du").type == pa.int32()
    assert table.schema.field("rate_annual").type == pa.float64()
    assert table.schema.field("source").type == pa.string()
    assert table.schema.names == RATES_SCHEMA.names

    columns = table.to_pydict()
    assert len(columns["refdate"]) == 4  # duas datas × dois nós, nada duplicado
    assert columns["refdate"] == [
        date(2026, 11, 18),
        date(2026, 11, 18),
        date(2026, 11, 19),
        date(2026, 11, 19),
    ]
    assert columns["du"] == [1, 21, 1, 21]  # ordenado por (refdate, du)
    assert columns["rate_annual"] == [13.64, 13.71, 13.65, 13.72]
    assert columns["source"] == ["b3_reference"] * 4
    assert not list(target.parent.glob("*.tmp"))


def test_publicacao_usa_replace_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A publicação passa pelo `fsutil.replace_file` (fallback de EACCES do bind)."""
    from b3o.ingest import rates as rates_module

    real_replace = rates_module.replace_file
    calls: list[tuple[str, str]] = []

    def spy(src: Path | str, dst: Path | str) -> None:
        assert Path(src).is_file()  # o tmp já foi escrito e conferido
        calls.append((Path(src).name, Path(dst).name))
        real_replace(src, dst)

    monkeypatch.setattr(rates_module, "replace_file", spy)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_csv_b64("DI x pré;1;4;13,65"))

    with _client(handler) as client:
        fetch_rates_di(DATE, client=client, data_dir=tmp_path)
        fetch_rates_di(DATE, client=client, data_dir=tmp_path)  # segundo prepare

    assert calls == [
        ("part-0.parquet.tmp", "part-0.parquet"),
        ("part-0.parquet.tmp", "part-0.parquet"),
    ]
    assert _partition(tmp_path).is_file()


def test_golden_real_da_curva_pre_de_19_11_2026(tmp_path: Path) -> None:
    """Números baixados de verdade do proxy (contrato §8): 1/21/41 du."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_csv_b64(*REAL_PRE_LINES))

    with _client(handler) as client:
        result = fetch_rates_di(DATE, client=client, data_dir=tmp_path)

    by_du = {p.du: p.rate_annual for p in result.points}
    assert by_du == {1: 13.65, 21: 13.72, 41: 13.76}
    assert result.source_final == "b3_reference"
