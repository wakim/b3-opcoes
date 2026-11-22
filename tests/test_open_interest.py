"""Testes do open interest do BDI (F0.8) — CSV sintético, nullable e parquet.

`httpx.MockTransport` cobre a fonte (o export CSV do BDI): sucesso com preâmbulo,
`Nenhum resultado`, 500, timeout e retry. O que se prova é o contrato de
`docs/contratos/onda-b.md` §6: `oi` é a coluna `Contratos em aberto` e é `None`
quando o arquivo traz `-`; data sem dado é `available=False` **sem erro** (P-N);
falha de fonte vira `SourceError` com hint; o parquet preserva `int64` nullable e
regravar o mesmo dia é idempotente.

As linhas abaixo são recortes **reais** de 19/11/2026 (baixado do BDI em
22/11/2026), inclusive o golden do contrato §8 (`PETRPZ26` = 12.651.200) — o
teste não depende do arquivo grande, só destes bytes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from b3o.ingest.fetch import BROWSER_UA
from b3o.ingest.open_interest import (
    BDI_EXPORT_URL,
    OI_SCHEMA,
    OI_TABLE,
    OIRow,
    fetch_open_interest,
    fetch_open_interest_window,
    open_interest_path,
    parse_open_interest_csv,
    read_open_interest,
    save_open_interest,
)

DATE = date(2026, 11, 19)
PREVIOUS = date(2026, 11, 18)
EMPTY_DAY = date(2026, 11, 17)

DESCRIPTION = (
    "Apresenta o volume e valor das posições em aberto em derivativos de renda "
    "variável, indicando o interesse do mercado."
)
GLOSSARY = (
    "Glossário;https://www.b3.com.br/data/files/1E/D1/BA/58/"
    "C841B810E9C1AAA8AC094EA8/DerivativesOpenPositionFile%202023.pdf"
)
HEADER = ";".join(
    [
        "Instrumento financeiro",
        "Código ISIN",
        "Ativo",
        "Código de expiração",
        "Segmento",
        "Contratos em aberto",
        "Variação de contratos em aberto",
        "Identificador da distribuição",
        "Quantidade coberta",
        "Total de posições bloqueadas",
        "Quantidade descoberta",
        "Total de posições",
        "Quantidade de tomadores",
        "Quantidade de doadores",
        "Quantidade atual",
        "Contratos travados",
        "Contratos baixados por transferência",
        "Preço a termo",
    ]
)

# Linhas reais de 19/11/2026 (bytes do CSV baixado, sem edição de valor).
ROW_PETRPZ26 = (
    "PETRPZ26;BRPETR4Z0070;PETRP;Z26;FINANCIAL;12.651.200;5.000.100;-;-;-;-;-;-;"
    "-;-;-;-;-"
)
ROW_PETRA365 = (
    "PETRA365;BRPETR4A1S70;PETR;-;EQUITY CALL;-;-;224;100;3.500;18.700;22.300;4;9;"
    "-;-;-;-"
)
ROW_BGIX26 = "BGIX26;BRBMEFBGI7W1;BGI;X26;AGRIBUSINESS;20.679;-513;-;-;-;-;-;-;-;-;743;;-"
ROW_SYNTHETIC = (
    "BGIX26P035000;BRBMEFBGI7W1;BGI;X26;AGRIBUSINESS;1.234;-7;-;-;-;-;-;-;-;-;;;-"
)


def _csv(*rows: str, preamble: bool = True) -> bytes:
    """CSV como o BDI devolve: UTF-8 com BOM, `;`, CRLF, preâmbulo antes do header."""
    lines: list[str] = []
    if preamble:
        lines.extend([DESCRIPTION, GLOSSARY, ""])
    lines.extend([HEADER, *rows])
    return ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def _body_of(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content.decode("utf-8"))


def _partition(data_dir: Path, year: int = 2026) -> Path:
    return data_dir / OI_TABLE / f"year={year}" / "part-0.parquet"


# --- parsing -----------------------------------------------------------------


def test_csv_com_preambulo_le_por_nome_e_preserva_golden() -> None:
    result = parse_open_interest_csv(
        _csv(ROW_PETRPZ26, ROW_PETRA365, ROW_BGIX26), date_ref=DATE
    )

    assert result.error is None
    assert result.available is True
    assert result.skipped_lines == 0
    assert [row.ticker for row in result.rows] == ["PETRPZ26", "PETRA365", "BGIX26"]

    pz, a365, bgi = result.rows
    # Golden do contrato §8: 12.651.200 contratos em aberto, com `.` de milhar.
    assert pz == OIRow(
        refdate=DATE,
        ticker="PETRPZ26",
        oi=12_651_200,
        covered_qty=None,
        uncovered_qty=None,
        total_qty=None,
    )
    # Opção de ação: `Contratos em aberto` é `-` (None) e a posição vem em
    # quantidade de ativo-objeto — coberta 100, descoberta 18.700, total 22.300.
    assert a365.oi is None
    assert (a365.covered_qty, a365.uncovered_qty, a365.total_qty) == (100, 18_700, 22_300)
    assert a365.refdate == DATE  # a data é a pedida, nunca a do relógio (P-C)
    # `-` e campo vazio são os dois jeitos de dizer "sem valor" no arquivo.
    assert (bgi.oi, bgi.total_qty) == (20_679, None)


def test_ticker_sintetico_de_commodity_e_guardado_verbatim() -> None:
    result = parse_open_interest_csv(_csv(ROW_SYNTHETIC), date_ref=DATE)

    assert [row.ticker for row in result.rows] == ["BGIX26P035000"]
    assert result.rows[0].oi == 1_234


def test_nenhum_resultado_nao_e_erro() -> None:
    """Dia sem dado: 200 com header + `Nenhum resultado` → null + flag, não erro."""
    result = parse_open_interest_csv(_csv("Nenhum resultado"), date_ref=EMPTY_DAY)

    assert result.error is None
    assert result.available is False
    assert result.rows == ()


def test_nenhum_resultado_sem_preambulo_tambem_nao_e_erro() -> None:
    """22/11/2026 real: header na primeira linha e uma linha só de marcador."""
    result = parse_open_interest_csv(
        _csv("Nenhum resultado", preamble=False), date_ref=EMPTY_DAY
    )

    assert result.error is None
    assert result.available is False
    assert result.rows == ()


def test_latin1_sem_bom_tambem_decodifica() -> None:
    """O contrato supunha latin-1; o arquivo é UTF-8 com BOM. Os dois entram."""
    payload = _csv(ROW_PETRA365).decode("utf-8-sig").encode("latin-1")

    result = parse_open_interest_csv(payload, date_ref=DATE)

    assert result.error is None
    assert [row.ticker for row in result.rows] == ["PETRA365"]


def test_corpo_vazio_e_falha_explicita() -> None:
    result = parse_open_interest_csv(b"", date_ref=DATE)

    assert result.available is False
    assert result.error is not None
    assert result.error.code == "source_unavailable"
    assert result.error.hint


def test_corpo_sem_header_e_falha_explicita() -> None:
    html = b"<html><body>manutencao</body></html>"

    result = parse_open_interest_csv(html, date_ref=DATE)

    assert result.available is False
    assert result.error is not None
    assert result.error.code == "source_unavailable"
    assert "Instrumento financeiro" in result.error.message


def test_coluna_ausente_e_falha_explicita() -> None:
    header = HEADER.replace(";Contratos em aberto", ";Contratos")

    result = parse_open_interest_csv(
        ("\ufeff" + header + "\r\n" + ROW_PETRA365 + "\r\n").encode(),
        date_ref=DATE,
    )

    assert result.error is not None
    assert "contratos em aberto" in result.error.message


def test_header_sem_dado_nem_marcador_e_falha_explicita() -> None:
    """Header e nada depois dele é arquivo truncado, não "dia sem dado"."""
    result = parse_open_interest_csv(
        ("\ufeff" + HEADER + "\r\n").encode(), date_ref=DATE
    )

    assert result.available is False
    assert result.error is not None
    assert "Nenhum resultado" in result.error.message


def test_linha_torta_e_pulada_e_contada() -> None:
    torta = "PETRX000;BRPETR4X000;PETR;-;EQUITY CALL"

    result = parse_open_interest_csv(_csv(ROW_PETRA365, torta), date_ref=DATE)

    assert [row.ticker for row in result.rows] == ["PETRA365"]
    assert result.skipped_lines == 1
    assert result.error is None


@pytest.mark.parametrize("valor", ["12.65", "1.234,56", "abc", "1.2345"])
def test_quantidade_ambigua_nao_vira_numero_errado(valor: str) -> None:
    """Ponto fora de grupo de 3 dígitos seria decimal: erro explícito, não palpite."""
    row = ROW_PETRPZ26.replace("12.651.200", valor)

    result = parse_open_interest_csv(_csv(row), date_ref=DATE)

    assert result.rows == ()
    assert result.available is False
    assert result.skipped_lines == 1
    assert result.error is not None
    assert "1 linha(s) de dado" in result.error.message


def test_ticker_repetido_conta_e_nao_duplica_o_join() -> None:
    result = parse_open_interest_csv(
        _csv(ROW_PETRPZ26, ROW_PETRPZ26), date_ref=DATE
    )

    assert len(result.rows) == 1
    assert result.duplicate_tickers == 1


# --- fetch (MockTransport) ---------------------------------------------------


def test_corpo_do_post_e_o_medido() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_csv(ROW_PETRPZ26))

    with _client(handler) as client:
        result = fetch_open_interest(DATE, client=client, retry_delays=())

    assert result.error is None
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == BDI_EXPORT_URL
    assert request.headers["user-agent"] == BROWSER_UA  # sem UA a B3 responde 520/404
    assert _body_of(request) == {
        "Name": "OpenPositionsEquities",
        "Date": "2026-11-19",
        "FinalDate": "2026-11-19",
        "ClientId": "",
        "Filters": None,
    }


def test_http_500_vira_source_error_com_hint() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="fora do ar")

    with _client(handler) as client:
        result = fetch_open_interest(DATE, client=client, retry_delays=())

    assert len(calls) == 1
    assert result.available is False
    assert result.rows == ()
    assert result.error is not None
    assert result.error.code == "source_unavailable"
    assert "503" in result.error.message
    assert result.error.hint


def test_timeout_vira_source_error_sem_excecao() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("leitura estourou", request=request)

    with _client(handler) as client:
        result = fetch_open_interest(DATE, client=client, retry_delays=())

    assert result.available is False
    assert result.error is not None
    assert result.error.code == "source_unavailable"
    assert "ReadTimeout" in result.error.message
    assert "read ≥ 120" in result.error.hint


def test_retry_curto_recupera_um_timeout() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("sem rota", request=request)
        return httpx.Response(200, content=_csv(ROW_PETRA365))

    with _client(handler) as client:
        result = fetch_open_interest(DATE, client=client, retry_delays=(0.0,))

    assert len(calls) == 2
    assert result.error is None
    assert [row.ticker for row in result.rows] == ["PETRA365"]


def test_404_e_permanente_e_nao_re_tenta() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    with _client(handler) as client:
        result = fetch_open_interest(DATE, client=client, retry_delays=(0.0, 0.0))

    assert len(calls) == 1
    assert result.error is not None
    assert result.error.code == "source_unavailable"


def test_dia_vazio_pelo_http_nao_e_erro(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_csv("Nenhum resultado", preamble=False))

    with _client(handler) as client:
        result = fetch_open_interest(
            EMPTY_DAY, client=client, data_dir=tmp_path, retry_delays=()
        )

    assert result.error is None
    assert result.available is False
    assert result.path is None  # nada a gravar, nada gravado
    assert not _partition(tmp_path).exists()


# --- parquet -----------------------------------------------------------------


def test_parquet_round_trip_preserva_int64_nullable(tmp_path: Path) -> None:
    rows = [
        OIRow(
            refdate=DATE,
            ticker="PETRPZ26",
            oi=12_651_200,
            covered_qty=None,
            uncovered_qty=None,
            total_qty=None,
        ),
        OIRow(
            refdate=DATE,
            ticker="PETRA365",
            oi=None,
            covered_qty=100,
            uncovered_qty=18_700,
            total_qty=22_300,
        ),
    ]

    path = save_open_interest(rows, tmp_path)

    assert path == _partition(tmp_path)
    table = pq.read_table(_partition(tmp_path))
    assert table.schema.equals(OI_SCHEMA)
    assert table.schema.field("refdate").type == pa.date32()
    for field in ("oi", "covered_qty", "uncovered_qty", "total_qty"):
        assert table.schema.field(field).type == pa.int64()
        assert table.schema.field(field).nullable is True

    data = table.to_pydict()
    assert data["refdate"] == [DATE, DATE]
    assert data["ticker"] == ["PETRA365", "PETRPZ26"]  # ordem estável por ticker
    assert data["oi"] == [None, 12_651_200]
    assert data["covered_qty"] == [100, None]

    assert tuple(read_open_interest(tmp_path)) == (
        rows[1],
        rows[0],
    )


def test_regravar_o_mesmo_dia_e_idempotente(tmp_path: Path) -> None:
    first = [
        OIRow(refdate=DATE, ticker="PETRPZ26", oi=12_651_200, covered_qty=None,
              uncovered_qty=None, total_qty=None),
        OIRow(refdate=DATE, ticker="PETRA365", oi=None, covered_qty=100,
              uncovered_qty=18_700, total_qty=22_300),
    ]

    save_open_interest(first, tmp_path)
    save_open_interest(first, tmp_path)

    assert len(list(read_open_interest(tmp_path))) == 2
    assert pq.read_metadata(_partition(tmp_path)).num_rows == 2


def test_salvar_dia_novo_preserva_o_anterior(tmp_path: Path) -> None:
    save_open_interest(
        [OIRow(refdate=PREVIOUS, ticker="PETRPZ26", oi=11_000, covered_qty=None,
               uncovered_qty=None, total_qty=None)],
        tmp_path,
    )
    save_open_interest(
        [OIRow(refdate=DATE, ticker="PETRPZ26", oi=12_651_200, covered_qty=None,
               uncovered_qty=None, total_qty=None)],
        tmp_path,
    )

    rows = list(read_open_interest(tmp_path))

    assert [(row.refdate, row.oi) for row in rows] == [
        (PREVIOUS, 11_000),
        (DATE, 12_651_200),
    ]


def test_ano_diferente_vai_para_particao_diferente(tmp_path: Path) -> None:
    save_open_interest(
        [OIRow(refdate=date(2025, 12, 30), ticker="PETRPZ26", oi=7, covered_qty=None,
               uncovered_qty=None, total_qty=None)],
        tmp_path,
    )
    save_open_interest(
        [OIRow(refdate=DATE, ticker="PETRPZ26", oi=8, covered_qty=None,
               uncovered_qty=None, total_qty=None)],
        tmp_path,
    )

    assert _partition(tmp_path, 2025).is_file()
    assert _partition(tmp_path, 2026).is_file()
    assert [row.oi for row in read_open_interest(tmp_path, years=[2025])] == [7]


def test_salvar_lote_vazio_nao_cria_arquivo(tmp_path: Path) -> None:
    assert save_open_interest([], tmp_path) is None
    assert list(read_open_interest(tmp_path)) == []


def test_gravacao_sobre_particao_existente_tolera_replace_negado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bind mount do Windows nega `os.replace` por cima de arquivo existente.

    Medido no podman/Hyper-V em 22/11/2026: rename para nome novo funciona,
    rename sobre nome existente dá `PermissionError`. `b3o.fsutil.replace_file`
    cobre o caso (unlink + rename); sem ele a segunda gravação da mesma partição
    — o caminho idempotente do `prepare` — morreria.
    """
    import os

    real_replace = os.replace

    def fake_replace(src: str, dst: str) -> None:
        if Path(dst).exists():
            raise PermissionError(13, "Permission denied", str(dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fake_replace)

    save_open_interest(
        [OIRow(refdate=DATE, ticker="PETRPZ26", oi=11_000, covered_qty=None,
               uncovered_qty=None, total_qty=None)],
        tmp_path,
    )
    save_open_interest(
        [OIRow(refdate=DATE, ticker="PETRPZ26", oi=12_651_200, covered_qty=None,
               uncovered_qty=None, total_qty=None)],
        tmp_path,
    )

    assert [row.oi for row in read_open_interest(tmp_path)] == [12_651_200]
    assert not (tmp_path / OI_TABLE / "year=2026" / "part-0.parquet.bak").exists()
    assert not _partition(tmp_path).with_name("part-0.parquet.tmp").exists()


def test_fetch_com_data_dir_grava_a_particao(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_csv(ROW_PETRPZ26))

    with _client(handler) as client:
        result = fetch_open_interest(
            DATE, client=client, data_dir=tmp_path, retry_delays=()
        )

    assert result.path == _partition(tmp_path)
    assert open_interest_path(tmp_path, 2026) == _partition(tmp_path)
    assert [row.ticker for row in read_open_interest(tmp_path)] == ["PETRPZ26"]


# --- janela ------------------------------------------------------------------


def test_janela_grava_uma_vez_e_isola_a_data_que_falha(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        day = str(_body_of(request)["Date"])
        if day == "2026-11-18":
            return httpx.Response(500)
        if day == "2026-11-17":
            return httpx.Response(200, content=_csv("Nenhum resultado", preamble=False))
        return httpx.Response(200, content=_csv(ROW_PETRPZ26))

    with _client(handler) as client:
        window = fetch_open_interest_window(
            [DATE, PREVIOUS, EMPTY_DAY], client=client, data_dir=tmp_path, retry_delays=()
        )

    assert window.dates_with_data == (DATE,)
    assert window.dates_without_data == (EMPTY_DAY,)
    assert len(window.errors) == 1
    assert window.errors[0].code == "source_unavailable"
    assert [row.ticker for row in window.rows] == ["PETRPZ26"]
    assert window.path == _partition(tmp_path)
    assert [row.refdate for row in read_open_interest(tmp_path)] == [DATE]


def test_janela_sem_data_dir_nao_grava(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_csv(ROW_PETRA365))

    with _client(handler) as client:
        window = fetch_open_interest_window([DATE, DATE], client=client, retry_delays=())

    assert window.path is None
    assert window.dates_with_data == (DATE,)  # data repetida é pedida uma vez
    assert not (tmp_path / OI_TABLE).exists()


def test_janela_em_dia_nao_util_nao_e_erro() -> None:
    """sábado/domingo: a fonte responde `Nenhum resultado` — oi null + flag."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_csv("Nenhum resultado", preamble=False))

    with _client(handler) as client:
        window = fetch_open_interest_window(
            [date(2026, 11, 21), date(2026, 11, 22)], client=client, retry_delays=()
        )

    assert window.dates_without_data == (date(2026, 11, 21), date(2026, 11, 22))
    assert window.rows == ()
    assert window.errors == ()


def test_data_nunca_vem_do_relogio() -> None:
    """P-C: a data é parâmetro; nenhum `date.today()` no caminho do parse."""
    result = parse_open_interest_csv(_csv(ROW_PETRPZ26), date_ref=date(2024, 3, 1))

    assert result.refdate == date(2024, 3, 1)
    assert result.rows[0].refdate == date(2024, 3, 1)
    assert isinstance(result.rows[0].refdate, date)
    assert not isinstance(result.rows[0].refdate, datetime)
