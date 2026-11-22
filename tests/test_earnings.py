"""Testes do calendário de resultados da CVM (F0.10) — sem rede real.

Os zips sintéticos reproduzem o layout medido em 22/11/2026: índice ITR/DFP
latin-1 com `;` e o membro `fca_cia_aberta_valor_mobiliario_AAAA.csv` do FCA.
O que se prova aqui é a regra da spec §6.14 (VERSAO 1, join por CNPJ, estimativa
no mesmo dia da semana com clamp no prazo) e o download condicional A1-style.
"""

from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from b3o.ingest.earnings import (
    EarningsDate,
    _next_year_same_weekday,
    _regulatory_deadline,
    build_earnings,
    dataset_meta_path,
    dataset_path,
    earnings_path,
    fetch_earnings,
    read_earnings,
    write_earnings,
)

DOC_HEADER = [
    "CNPJ_CIA",
    "DT_REFER",
    "VERSAO",
    "DENOM_CIA",
    "CD_CVM",
    "CATEG_DOC",
    "ID_DOC",
    "DT_RECEB",
    "LINK_DOC",
]

# Cabeçalho real do FCA (medido em 22/11/2026), na ordem do arquivo.
FCA_HEADER = [
    "CNPJ_Companhia",
    "Data_Referencia",
    "Versao",
    "ID_Documento",
    "Nome_Empresarial",
    "Valor_Mobiliario",
    "Sigla_Classe_Acao_Preferencial",
    "Classe_Acao_Preferencial",
    "Codigo_Negociacao",
    "Composicao_BDR_Unit",
    "Mercado",
    "Sigla_Entidade_Administradora",
    "Entidade_Administradora",
    "Data_Inicio_Negociacao",
    "Data_Fim_Negociacao",
    "Segmento",
    "Data_Inicio_Listagem",
    "Data_Fim_Listagem",
]

BB_CNPJ = "00.000.000/0001-91"
BB_CD = "001023"
PETR_CNPJ = "33.000.167/0001-01"
PETR_CD = "009512"
VALE_CNPJ = "33.592.510/0001-54"
VALE_CD = "004170"

PAYLOAD = b"PK\x03\x04" + b"conteudo-do-zip-de-teste" * 64
ETAG = '"6b02ca6b-1c24e18"'
LAST_MODIFIED = "Sun, 22 Nov 2026 10:56:43 GMT"


# --------------------------------------------------------------------------- #
# Construtores dos zips sintéticos
# --------------------------------------------------------------------------- #


def _csv_text(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _write_zip(path: Path, members: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text.encode("latin-1"))
    return path


def _doc(
    cnpj: str,
    cd_cvm: str,
    ref: str,
    versao: int,
    recebido: str,
    *,
    nome: str = "EMPRESA TESTE",
    categ: str = "ITR",
) -> list[str]:
    return [cnpj, ref, str(versao), nome, cd_cvm, categ, f"{cd_cvm}-{ref}", recebido, "http://x"]


def _itr_zip(tmp_path: Path, year: int, rows: Sequence[Sequence[str]]) -> Path:
    name = f"itr_cia_aberta_{year}"
    return _write_zip(
        tmp_path / "src" / f"{name}.zip", {f"{name}.csv": _csv_text(DOC_HEADER, rows)}
    )


def _dfp_zip(tmp_path: Path, year: int, rows: Sequence[Sequence[str]]) -> Path:
    name = f"dfp_cia_aberta_{year}"
    return _write_zip(
        tmp_path / "src" / f"{name}.zip", {f"{name}.csv": _csv_text(DOC_HEADER, rows)}
    )


def _fca_row(
    cnpj: str,
    ticker: str,
    *,
    referencia: str = "2026-01-01",
    versao: int = 1,
    mercado: str = "Bolsa",
    fim: str = "",
    nome: str = "EMPRESA TESTE",
) -> list[str]:
    return [
        cnpj,
        referencia,
        str(versao),
        "1",
        nome,
        "Ações Ordinárias",
        "",
        "",
        ticker,
        "",
        mercado,
        "B3",
        "B3 S.A.",
        "1990-01-01",
        fim,
        "Novo Mercado",
        "",
        "",
    ]


def _fca_zip(tmp_path: Path, year: int, rows: Sequence[Sequence[str]]) -> Path:
    name = f"fca_cia_aberta_{year}"
    member = f"fca_cia_aberta_valor_mobiliario_{year}.csv"
    return _write_zip(
        tmp_path / "src" / f"{name}.zip",
        {
            f"fca_cia_aberta_{year}.csv": _csv_text(
                ["CNPJ_Companhia", "Data_Referencia", "Versao"], []
            ),
            member: _csv_text(FCA_HEADER, rows),
        },
    )


@pytest.fixture
def cvm(tmp_path: Path) -> dict[str, object]:
    """Dois papéis mapeados (BB, PETR3/PETR4), um delistado e um de balcão."""
    itr_2025 = _itr_zip(
        tmp_path,
        2025,
        [
            _doc(BB_CNPJ, BB_CD, "2025-03-31", 1, "2025-05-13", nome="BCO BRASIL S.A."),
            _doc(PETR_CNPJ, PETR_CD, "2025-03-31", 1, "2025-05-13", nome="PETROBRAS"),
            _doc(PETR_CNPJ, PETR_CD, "2025-06-30", 1, "2025-08-07", nome="PETROBRAS"),
        ],
    )
    itr_2026 = _itr_zip(
        tmp_path,
        2026,
        [
            # VERSAO 1 é a data do resultado; a revisão não muda a data.
            _doc(BB_CNPJ, BB_CD, "2026-03-31", 1, "2026-05-13", nome="BCO BRASIL S.A."),
            _doc(BB_CNPJ, BB_CD, "2026-03-31", 2, "2026-06-10", nome="BCO BRASIL S.A."),
            _doc(PETR_CNPJ, PETR_CD, "2026-03-31", 1, "2026-05-11", nome="PETROBRAS"),
            _doc(PETR_CNPJ, PETR_CD, "2026-06-30", 1, "2026-08-06", nome="PETROBRAS"),
        ],
    )
    dfp_2025 = _dfp_zip(
        tmp_path,
        2025,
        [
            _doc(BB_CNPJ, BB_CD, "2025-12-31", 1, "2026-03-26", categ="DFP"),
            _doc(PETR_CNPJ, PETR_CD, "2025-12-31", 1, "2026-03-25", categ="DFP"),
            _doc(VALE_CNPJ, VALE_CD, "2025-12-31", 1, "2026-03-20", categ="DFP"),
        ],
    )
    fca = _fca_zip(
        tmp_path,
        2026,
        [
            # PETR4 aparece duas vezes: o FCA é reenviado com outra Data_Referencia.
            _fca_row(PETR_CNPJ, "PETR3", referencia="2025-03-31", versao=1),
            _fca_row(PETR_CNPJ, "PETR4", referencia="2025-03-31", versao=1),
            _fca_row(PETR_CNPJ, "PETR4", referencia="2026-01-01", versao=2),
            _fca_row(BB_CNPJ, "BBAS3", referencia="2026-01-01"),
            # VALE3 saiu da Bolsa na versão mais recente: não mapeia.
            _fca_row(VALE_CNPJ, "VALE3", referencia="2025-01-02"),
            _fca_row(VALE_CNPJ, "VALE3", referencia="2026-01-01", fim="2025-06-30"),
            # Balcão não é Bolsa.
            _fca_row("99.999.999/0001-99", "XPTO3", mercado="Balcão"),
        ],
    )
    return {"itr": [itr_2025, itr_2026], "dfp": [dfp_2025], "fca": [fca]}


def _build(cvm: dict[str, object], tickers: Sequence[str], last_session: date) -> list:
    return build_earnings(
        cvm["itr"],  # type: ignore[arg-type]
        cvm["dfp"],  # type: ignore[arg-type]
        cvm["fca"],  # type: ignore[arg-type]
        tickers,
        last_session,
    )


def _by_key(rows: Sequence[EarningsDate]) -> dict[tuple[str, date, str], date]:
    return {(r.ticker, r.ref_period, r.kind): r.date for r in rows}


# --------------------------------------------------------------------------- #
# actual
# --------------------------------------------------------------------------- #


def test_actual_usa_dt_receb_da_versao_1(cvm: dict[str, object]) -> None:
    rows = _build(cvm, ["BBAS3"], date(2026, 11, 21))
    key = ("BBAS3", date(2026, 3, 31), "actual")
    assert _by_key(rows)[key] == date(2026, 5, 13)
    # A revisão (VERSAO 2, 2026-06-10) não vira data de resultado.
    assert date(2026, 6, 10) not in {r.date for r in rows}


def test_dois_tickers_do_mesmo_cnpj_mapeiam_igual(cvm: dict[str, object]) -> None:
    rows = _build(cvm, ["PETR3", "PETR4"], date(2026, 11, 21))
    mapped = _by_key(rows)
    assert mapped[("PETR3", date(2026, 3, 31), "actual")] == date(2026, 5, 11)
    assert (
        mapped[("PETR4", date(2026, 3, 31), "actual")]
        == mapped[("PETR3", date(2026, 3, 31), "actual")]
    )
    assert (
        mapped[("PETR4", date(2026, 6, 30), "actual")]
        == mapped[("PETR3", date(2026, 6, 30), "actual")]
    )


def test_ticker_minusculo_e_normalizado(cvm: dict[str, object]) -> None:
    rows = _build(cvm, [" petr4 "], date(2026, 11, 21))
    assert {r.ticker for r in rows} == {"PETR4"}


def test_actual_posterior_a_last_session_nao_vaza(cvm: dict[str, object]) -> None:
    """Nenhum look-ahead: em 01/08/2026 o resultado de 06/08/2026 ainda não era público."""
    rows = _build(cvm, ["PETR4"], date(2026, 8, 1))
    mapped = _by_key(rows)
    assert ("PETR4", date(2026, 6, 30), "actual") not in mapped
    # Como havia estimativa para a mesma referência, ela aparece — e é futura.
    estimated = mapped[("PETR4", date(2026, 6, 30), "estimated")]
    assert estimated == date(2026, 8, 6)


def test_actual_publicado_no_dia_entra(cvm: dict[str, object]) -> None:
    rows = _build(cvm, ["PETR4"], date(2026, 8, 6))
    assert _by_key(rows)[("PETR4", date(2026, 6, 30), "actual")] == date(2026, 8, 6)


# --------------------------------------------------------------------------- #
# estimated
# --------------------------------------------------------------------------- #


def test_estimated_um_ano_depois_no_mesmo_dia_da_semana() -> None:
    # Q1/2026 do BB caiu numa quarta (2026-05-13); em 2027 a mesma semana tem
    # quarta em 12/05 — é o valor que o contrato cita.
    shifted = _next_year_same_weekday(date(2026, 5, 13))
    assert shifted == date(2027, 5, 12)
    assert shifted.weekday() == date(2026, 5, 13).weekday() == 2
    # Mesma semana ISO da data-base (13/05/2027), não a semana seguinte.
    assert shifted.isocalendar()[:2] == date(2027, 5, 13).isocalendar()[:2]


def test_prazos_regulatorios() -> None:
    assert _regulatory_deadline(date(2026, 3, 31)) == date(2026, 5, 15)  # ITR: +45 dias
    assert _regulatory_deadline(date(2026, 6, 30)) == date(2026, 8, 14)
    assert _regulatory_deadline(date(2026, 9, 30)) == date(2026, 11, 14)
    assert _regulatory_deadline(date(2026, 12, 31)) == date(2027, 3, 31)  # DFP: +3 meses


def test_estimated_dezembro_usa_a_data_do_ano_anterior(cvm: dict[str, object]) -> None:
    rows = _build(cvm, ["PETR4", "BBAS3"], date(2026, 11, 21))
    mapped = _by_key(rows)
    # DFP 2025 da Petrobras: 2026-03-25 (quarta) -> 2027-03-24 (quarta).
    assert mapped[("PETR4", date(2026, 12, 31), "estimated")] == date(2027, 3, 24)
    # BB: 2026-03-26 (quinta) -> 2027-03-25 (quinta), dentro do prazo 31/03.
    assert mapped[("BBAS3", date(2026, 12, 31), "estimated")] == date(2027, 3, 25)


def test_estimated_respeita_o_prazo_do_itr(tmp_path: Path) -> None:
    """Data do ano anterior tardia: a estimativa é puxada para o prazo (15/05)."""
    itr_2025 = _itr_zip(
        tmp_path, 2025, [_doc(BB_CNPJ, BB_CD, "2025-03-31", 1, "2025-05-20", nome="BB")]
    )
    fca = _fca_zip(tmp_path, 2026, [_fca_row(BB_CNPJ, "BBAS3")])
    rows = build_earnings(itr_2025, [], fca, ["BBAS3"], date(2026, 4, 1))
    mapped = _by_key(rows)
    # Sem clamp seria 2026-05-19; o prazo do ITR é 45 dias após 31/03.
    assert mapped[("BBAS3", date(2026, 3, 31), "estimated")] == date(2026, 5, 15)


def test_estimated_respeita_o_prazo_da_dfp(tmp_path: Path) -> None:
    """DFP entregue fora do prazo em 2026-04-02: a estimativa é 31/03 do ano seguinte."""
    dfp_2025 = _dfp_zip(
        tmp_path,
        2025,
        [_doc(BB_CNPJ, BB_CD, "2025-12-31", 1, "2026-04-02", categ="DFP")],
    )
    fca = _fca_zip(tmp_path, 2026, [_fca_row(BB_CNPJ, "BBAS3")])
    rows = build_earnings([], dfp_2025, fca, ["BBAS3"], date(2026, 11, 21))
    mapped = _by_key(rows)
    assert mapped[("BBAS3", date(2026, 12, 31), "estimated")] == date(2027, 3, 31)


def test_estimativa_no_passado_nao_e_inventada(tmp_path: Path) -> None:
    """Referência que já venceu sem documento não ganha data retroativa."""
    itr_2025 = _itr_zip(
        tmp_path, 2025, [_doc(BB_CNPJ, BB_CD, "2025-06-30", 1, "2025-08-07", nome="BB")]
    )
    fca = _fca_zip(tmp_path, 2026, [_fca_row(BB_CNPJ, "BBAS3")])
    rows = build_earnings(itr_2025, [], fca, ["BBAS3"], date(2026, 11, 21))
    # Só o actual de 2025: nada de "previsto 2026-08-06" para uma referência que
    # já venceu sem documento.
    assert [(r.ref_period, r.kind) for r in rows] == [(date(2025, 6, 30), "actual")]


# --------------------------------------------------------------------------- #
# Cobertura e forma
# --------------------------------------------------------------------------- #


def test_papel_sem_fca_ou_fora_da_bolsa_fica_de_fora(cvm: dict[str, object]) -> None:
    rows = _build(cvm, ["VALE3", "XPTO3", "SEMD3", "BBAS3"], date(2026, 11, 21))
    tickers = {r.ticker for r in rows}
    assert tickers == {"BBAS3"}  # delistado, balcão e sem ITR: ninguém inventado


def test_colunas_e_tipos(cvm: dict[str, object]) -> None:
    rows = _build(cvm, ["PETR4", "BBAS3"], date(2026, 11, 21))
    assert rows
    for row in rows:
        assert isinstance(row, EarningsDate)
        assert isinstance(row.ticker, str) and row.ticker
        assert isinstance(row.ref_period, date)
        assert isinstance(row.date, date)
        assert row.kind in {"actual", "estimated"}


def test_ordem_estavel(cvm: dict[str, object]) -> None:
    rows = _build(cvm, ["PETR4", "BBAS3"], date(2026, 11, 21))
    assert rows == sorted(rows, key=lambda r: (r.ticker, r.ref_period, r.kind))


# --------------------------------------------------------------------------- #
# Parquet
# --------------------------------------------------------------------------- #


def test_parquet_round_trip(tmp_path: Path, cvm: dict[str, object]) -> None:
    rows = _build(cvm, ["PETR4", "BBAS3"], date(2026, 11, 21))
    target = write_earnings(rows, tmp_path)
    assert target == earnings_path(tmp_path) == tmp_path / "earnings" / "part-0.parquet"
    assert not list((tmp_path / "tmp").glob("*.parquet"))  # tmp não sobra
    assert read_earnings(tmp_path) == rows

    table = pq.read_table(target)
    assert table.schema.field("ref_period").type == pa.date32()
    assert table.schema.field("date").type == pa.date32()
    assert table.schema.names == ["ticker", "ref_period", "date", "kind"]


def test_regravar_sobre_arquivo_existente_tolera_eacces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bind do Windows `os.replace` sobre arquivo existente dá EACCES (medido)."""
    write_earnings([], tmp_path)
    first = write_earnings(
        [EarningsDate("PETR4", date(2026, 3, 31), date(2026, 5, 11), "actual")], tmp_path
    )
    real_replace = os.replace
    calls: list[str] = []

    def flaky_replace(src, dst):  # type: ignore[no-untyped-def]
        calls.append(str(dst))
        if len(calls) == 1:
            raise PermissionError(13, "Permission denied", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    write_earnings(
        [
            EarningsDate("PETR4", date(2026, 3, 31), date(2026, 5, 11), "actual"),
            EarningsDate("PETR4", date(2026, 6, 30), date(2026, 8, 6), "actual"),
        ],
        tmp_path,
    )
    monkeypatch.undo()

    assert calls  # passou pelo caminho que falhou
    assert len(read_earnings(tmp_path)) == 2  # e a tabela nova foi publicada
    assert first.exists()


def test_kind_invalido_falha_explicito(tmp_path: Path) -> None:
    rows = [EarningsDate("PETR4", date(2026, 3, 31), date(2026, 5, 11), "announced")]
    with pytest.raises(ValueError, match="kind inválido"):
        write_earnings(rows, tmp_path)


def test_read_earnings_ausente_e_erro(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_earnings(tmp_path)


# --------------------------------------------------------------------------- #
# Download (A1-style, sem rede)
# --------------------------------------------------------------------------- #


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def test_fetch_200_grava_meta_e_usa_ua(tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        seen["ua"] = request.headers.get("user-agent")
        seen["if_none_match"] = request.headers.get("if-none-match")
        return httpx.Response(
            200,
            content=PAYLOAD,
            headers={
                "ETag": ETAG,
                "Last-Modified": LAST_MODIFIED,
                "Content-Length": str(len(PAYLOAD)),
            },
        )

    with _client(handler) as client:
        result = fetch_earnings([2026], tmp_path, client=client, retry_delays=(0.0,))

    assert result.ok
    assert len(result.results) == 3  # itr + dfp + fca
    itr = next(r for r in result.results if r.dataset == "itr")
    assert itr.status == "downloaded"
    assert itr.path == dataset_path(tmp_path, "itr", 2026)
    assert (
        "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_2026.zip" in urls
    )
    assert all(url.endswith(".zip") for url in urls)
    assert seen["ua"] and "Mozilla" in str(seen["ua"])
    assert seen["if_none_match"] is None  # sem meta não há GET condicional
    meta = json.loads(dataset_meta_path(tmp_path, "itr", 2026).read_text(encoding="utf-8"))
    assert meta["etag"] == ETAG
    assert meta["bytes"] == len(PAYLOAD)


def test_fetch_304_aproveita_cache(tmp_path: Path) -> None:
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("if-none-match"))
        if len(calls) <= 3:  # primeira rodada: itr, dfp, fca baixam
            return httpx.Response(200, content=PAYLOAD, headers={"ETag": ETAG})
        return httpx.Response(304, headers={"ETag": ETAG})

    with _client(handler) as client:
        fetch_earnings([2026], tmp_path, client=client, retry_delays=(0.0,))
        second = fetch_earnings([2026], tmp_path, client=client, retry_delays=(0.0,))

    assert second.ok
    assert {r.status for r in second.results} == {"not_modified"}
    assert calls[3:] == [ETAG, ETAG, ETAG]  # itr/dfp/fca mandaram If-None-Match


def test_fetch_5xx_vira_source_error_sem_excecao(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="fora do ar")

    with _client(handler) as client:
        result = fetch_earnings([2026], tmp_path, client=client, retry_delays=(0.0, 0.0))

    assert not result.ok
    assert len(result.failures) == 3
    error = result.failures[0].error
    assert error is not None
    assert error.code == "source_unavailable"
    assert error.hint
    assert not dataset_path(tmp_path, "itr", 2026).exists()  # nada parcial no nome final


def test_fetch_falha_de_um_dataset_nao_derruba_os_outros(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/DFP/" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=PAYLOAD)

    with _client(handler) as client:
        result = fetch_earnings([2026], tmp_path, client=client, retry_delays=(0.0,))

    assert [r.dataset for r in result.failures] == ["dfp"]
    assert result.failures[0].error is not None
    assert result.failures[0].error.code == "source_unavailable"
    assert len(result.paths("itr")) == 1
    assert result.path("dfp", 2026) is None
