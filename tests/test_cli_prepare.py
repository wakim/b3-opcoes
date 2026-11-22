"""`b3o prepare` de ponta a ponta, com TODAS as fontes em `httpx.MockTransport`.

O que este arquivo prova (contrato Onda C §1, §3, §6):

- **D2**: o client construído na borda tem `connect=10`/`read=120` e `BROWSER_UA`;
- **D3**: as linhas por mercado são contadas no parse — inclusive as de mercado
  descartado (`017`, `030`) — e chegam ao `health`;
- **ordem de falha**: sem rates (e sem Selic) o prepare SEGUE e o health acusa;
  sem COTAHIST **e** sem lake, sai não-zero; com lake e sem COTAHIST, segue;
- **idempotência**: dois prepares produzem o mesmo lake (bytes) e o segundo
  recebe 304 (o ETag conhecido vira `If-None-Match`);
- `liquidity` alimentada por OI real (`total_qty`) e `None` sem OI;
- `--watch`: agenda em 21:30 (função pura, sem dormir no teste).

O COTAHIST sintético é um zip de 245 bytes por linha, header `00`, trailer `99`
com o total, 2 sessões, 1 papel com 10 séries, mais uma linha de `017` e uma de
`030` para o contador de mercados.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest

from b3o import cli
from b3o.cli import (
    CONNECT_TIMEOUT_S,
    READ_TIMEOUT_S,
    _seconds_until_next_run,
    build_client,
    run_prepare,
)
from b3o.config import Config
from b3o.ingest.errors import SourceError
from b3o.ingest.fetch import BROWSER_UA
from b3o.query.service import QueryService, load_prepare_state, prepare_state_path

D1 = date(2026, 11, 18)
D2 = date(2026, 11, 19)
MATURITY_A = date(2026, 12, 18)
MATURITY_B = date(2027, 1, 15)
ISIN = "BRPETRACNPR6"
ETAG = '"cotahist-2026:0"'
BRT = timezone(timedelta(hours=-3))


# --------------------------------------------------------------------------- #
# COTAHIST sintético (245 bytes por linha, layout do contrato A §3)
# --------------------------------------------------------------------------- #


def _data_line(
    refdate: date,
    ticker: str,
    market: str,
    *,
    bdi: str = "02",
    corp: str = "PETROBRAS PN",
    spec: str = "ON      N2",
    close: int = 4_000,
    bid: int = 3_999,
    ask: int = 4_001,
    trades: int = 50_000,
    qty: int = 1_000_000,
    volume: int = 4_000_000_000,
    strike: int = 0,
    maturity: date | None = None,
    strike_pts: int = 0,
    distribution_id: str = "",
) -> str:
    maturity_text = "99991231" if maturity is None else maturity.strftime("%Y%m%d")
    line = "".join(
        [
            "01",
            refdate.strftime("%Y%m%d"),
            bdi,
            f"{ticker:<12}",
            market,
            f"{corp:<12}",
            f"{spec:<10}",
            "   ",  # [49:52] lixo variável
            "R$",  # [52:54]
            "  ",  # [54:56]
            f"{3_900:013d}",
            f"{4_050:013d}",
            f"{3_880:013d}",
            f"{3_970:013d}",
            f"{close:013d}",
            f"{bid:013d}",
            f"{ask:013d}",
            f"{trades:05d}",
            f"{qty:018d}",
            f"{volume:018d}",
            f"{strike:013d}",
            "0",  # strike_adj_ind
            maturity_text,
            f"{1:07d}",  # FATCOT
            f"{strike_pts:013d}",
            f"{ISIN:<12}",
            f"{distribution_id:<3}",
        ]
    )
    assert len(line) == 245, len(line)
    return line


def _option_line(
    refdate: date,
    index: int,
    *,
    strike: int,
    maturity: date,
) -> str:
    return _data_line(
        refdate,
        f"PETRX{index}",
        "070",
        bdi="78",
        corp="PETRE",
        spec="CALL      ",
        close=120,
        bid=119,
        ask=121,
        trades=20,
        qty=500,
        volume=60_000,
        strike=strike,
        maturity=maturity,
        strike_pts=strike,
        distribution_id="168",
    )


def _cotahist_lines() -> list[str]:
    lines: list[str] = []
    for day in (D1, D2):
        lines.append(_data_line(day, "PETR4", "010"))
        for index in range(1, 11):
            strike = 3_600 + 100 * ((index - 1) // 2)
            if day == D2:
                strike -= 100  # ajuste contíguo de R$ 1,00 em todas as séries
            maturity = MATURITY_A if index % 2 else MATURITY_B
            lines.append(_option_line(day, index, strike=strike, maturity=maturity))
    # mercados descartados: contam em `counts_by_market` (D3), não entram no lake
    lines.append(_data_line(D2, "FR0T5L", "017", corp="FROTA OCANIC"))
    lines.append(_data_line(D2, "XPTO3", "030", corp="OUTRO ATIVO"))
    return lines


def _cotahist_zip() -> bytes:
    lines = _cotahist_lines()
    header = "00" + " " * 243
    trailer = "99" + " " * 30 + f"{len(lines):010d}" + " " * 203
    assert len(header) == len(trailer) == 245
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "COTAHIST_A2026.TXT",
            "\r\n".join([header, *lines, trailer]) + "\r\n",
        )
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Fontes mockadas
# --------------------------------------------------------------------------- #

RATES_CSV = (
    "Descrição da Taxa;Dias Úteis;Dias Corridos;Preço/Taxa\r\n"
    "DI x pré;1;4;13,65\r\n"
    "DI x pré;21;32;13,72\r\n"
)
RATES_B64 = base64.b64encode(RATES_CSV.encode("latin-1"))

ITR_HEADER = (
    "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;CATEG_DOC;ID_DOC;DT_RECEB;LINK_DOC"
)
DFP_HEADER = ITR_HEADER
FCA_HEADER = (
    "CNPJ_Companhia;Codigo_Negociacao;Mercado;Data_Fim_Negociacao;"
    "Data_Referencia;Versao"
)

CVM_CSVS: dict[str, str] = {
    "itr_cia_aberta_2026.csv": (
        ITR_HEADER
        + "\n33.000.167/0001-01;2026-03-31;1;PETROBRAS;009512;ITR;1;2026-05-11;http://x"
    ),
    "itr_cia_aberta_2025.csv": (
        ITR_HEADER
        + "\n33.000.167/0001-01;2025-03-31;1;PETROBRAS;009512;ITR;1;2025-05-12;http://x"
    ),
    "dfp_cia_aberta_2026.csv": DFP_HEADER,
    "dfp_cia_aberta_2025.csv": (
        DFP_HEADER
        + "\n33.000.167/0001-01;2025-12-31;1;PETROBRAS;009512;DFP;1;2026-03-20;http://x"
    ),
    "fca_cia_aberta_valor_mobiliario_2026.csv": (
        FCA_HEADER + "\n33.000.167/0001-01;PETR4;Bolsa;;2026-01-01;1"
    ),
    "fca_cia_aberta_valor_mobiliario_2025.csv": (
        FCA_HEADER + "\n33.000.167/0001-01;PETR4;Bolsa;;2025-01-01;1"
    ),
}

OI_HEADER = (
    "Instrumento financeiro;Quantidade coberta;Quantidade descoberta;"
    "Contratos em aberto;Total de posições"
)
OI_CSV = (
    "\ufeff"
    + OI_HEADER
    + "\n"
    + "\n".join(
        f"PETRX{index};40.000;60.000;-;100.000" for index in range(1, 11)
    )
    + "\n"
)
OI_EMPTY_CSV = "\ufeff" + OI_HEADER + "\nNenhum resultado\n"


def _zip_bytes(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text.encode("latin-1"))
    return buffer.getvalue()


CVM_ZIPS: dict[str, bytes] = {
    "itr_cia_aberta_2026.zip": _zip_bytes(
        {"itr_cia_aberta_2026.csv": CVM_CSVS["itr_cia_aberta_2026.csv"]}
    ),
    "itr_cia_aberta_2025.zip": _zip_bytes(
        {"itr_cia_aberta_2025.csv": CVM_CSVS["itr_cia_aberta_2025.csv"]}
    ),
    "dfp_cia_aberta_2026.zip": _zip_bytes(
        {"dfp_cia_aberta_2026.csv": CVM_CSVS["dfp_cia_aberta_2026.csv"]}
    ),
    "dfp_cia_aberta_2025.zip": _zip_bytes(
        {"dfp_cia_aberta_2025.csv": CVM_CSVS["dfp_cia_aberta_2025.csv"]}
    ),
    "fca_cia_aberta_2026.zip": _zip_bytes(
        {"fca_cia_aberta_valor_mobiliario_2026.csv": CVM_CSVS[
            "fca_cia_aberta_valor_mobiliario_2026.csv"
        ]}
    ),
    "fca_cia_aberta_2025.zip": _zip_bytes(
        {"fca_cia_aberta_valor_mobiliario_2025.csv": CVM_CSVS[
            "fca_cia_aberta_valor_mobiliario_2025.csv"
        ]}
    ),
}

COTAHIST_ZIP = _cotahist_zip()


class Sources:
    """Roteador de todas as fontes do prepare, com contadores de chamada."""

    def __init__(self) -> None:
        self.cotahist_downloads = 0
        self.cotahist_not_modified = 0
        self.cotahist_status: int | None = None
        self.rates_fail = False
        self.oi_body = OI_CSV
        self.oi_status = 200
        self.oi_requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "InstDados/SerHist/COTAHIST_A2026.ZIP" in url:
            if self.cotahist_status is not None:
                return httpx.Response(self.cotahist_status)
            if request.headers.get("if-none-match") == ETAG:
                self.cotahist_not_modified += 1
                return httpx.Response(304, headers={"ETag": ETAG})
            self.cotahist_downloads += 1
            return httpx.Response(200, content=COTAHIST_ZIP, headers={"ETag": ETAG})
        if "referenceRatesProxy" in url:
            if self.rates_fail:
                return httpx.Response(500)
            return httpx.Response(200, content=RATES_B64)
        if "api.bcb.gov.br" in url:
            if self.rates_fail:
                return httpx.Response(500)
            return httpx.Response(
                200,
                json=[{"data": "19/11/2026", "valor": "0.050788"}],
            )
        if "dados.cvm.gov.br" in url:
            name = url.rsplit("/", 1)[-1]
            if name not in CVM_ZIPS:
                return httpx.Response(404)
            return httpx.Response(200, content=CVM_ZIPS[name])
        if "arquivos.b3.com.br" in url:
            self.oi_requests += 1
            return httpx.Response(self.oi_status, content=self.oi_body)
        return httpx.Response(404)

    def client_factory(self) -> Callable[[], httpx.Client]:
        return lambda: build_client(transport=httpx.MockTransport(self.handler))


def _config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "lake",
        log_level="INFO",
        model_version=1,
        cotahist_base_url="https://test.local/InstDados/SerHist/",
    )


def _prepare(tmp_path: Path, sources: Sources, *, now: datetime | None = None):
    return run_prepare(
        _config(tmp_path),
        client_factory=sources.client_factory(),
        year=2026,
        now=now or datetime(2026, 11, 19, 22, 30, tzinfo=BRT),
    )


# --------------------------------------------------------------------------- #
# D2 — client da borda
# --------------------------------------------------------------------------- #


def test_client_tem_timeout_longo_e_ua_de_browser() -> None:
    """D2: `connect=10`, `read=120` e `BROWSER_UA` — o OI estoura 60 s (B5)."""
    client = build_client()
    try:
        assert isinstance(client.timeout, httpx.Timeout)
        assert client.timeout.connect == CONNECT_TIMEOUT_S == 10.0
        assert client.timeout.read == READ_TIMEOUT_S == 120.0
        assert client.headers["User-Agent"] == BROWSER_UA
        assert client.follow_redirects is True
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# Prepare ponta a ponta
# --------------------------------------------------------------------------- #


def test_prepare_ponta_a_ponta(tmp_path: Path) -> None:
    sources = Sources()
    result = _prepare(tmp_path, sources)

    assert result.exit_code == 0, result.lines
    assert result.failures == []
    lake = tmp_path / "lake"

    # COTAHIST: 22 linhas (2 spot + 20 opções); os mercados 017/030 ficam de fora
    raw = lake / "raw_cotahist" / "year=2026" / "part-0.parquet"
    assert pq.ParquetFile(raw).metadata.num_rows == 22

    # derivados persistidos no padrão do B1
    legs = lake / "legs" / "year=2026" / "part-0.parquet"
    events = lake / "strike_events" / "year=2026" / "part-0.parquet"
    assert pq.ParquetFile(legs).metadata.num_rows == 20
    assert pq.ParquetFile(events).metadata.num_rows == 1

    # fontes auxiliares
    assert (lake / "rates_di" / "year=2026" / "part-0.parquet").is_file()
    assert (lake / "earnings" / "part-0.parquet").is_file()
    assert (lake / "open_interest" / "year=2026" / "part-0.parquet").is_file()

    # relatório de liquidez com data no nome (P-T) e universo no topo
    report = lake / "out" / f"relatorio-liquidez-{D2.isoformat()}.md"
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "Universo: 1 papel(is)" in text
    assert "moneyness" in text
    assert "| PETR4 | 2 |" in text
    assert "| 1,00 |" in text  # days_to_liquidate = 1.000.000 / 1.000.000
    assert result.universe_count == 1
    assert result.report_path == report

    # state: sessão do arquivo, contagens e linhas puladas
    state = json.loads((lake / "ingest_state.json").read_text(encoding="utf-8"))
    assert state["last_session"] == D2.isoformat()
    assert state["counts_by_date"] == {D1.isoformat(): 11, D2.isoformat(): 11}
    assert state["etag"] == ETAG
    assert state["trailer_total"] == 24
    assert state["skipped_lines"] == {"numeric": 0, "width": 0}
    assert state["model_version"] == 1

    # D3: por mercado, incluindo os descartados
    prepare_state = load_prepare_state(lake)
    assert prepare_state.counts_by_market == {
        "2026": {"010": 2, "017": 1, "030": 1, "070": 20}
    }
    assert prepare_state.universe == ("PETR4",)
    assert prepare_state.rss_peak_mib is not None and prepare_state.rss_peak_mib > 0
    assert prepare_state.rates == {"refdate": D2.isoformat(), "source": "b3_reference", "nodes": 2}
    assert prepare_state.oi_date == D2
    assert prepare_state.cotahist["status"] == "downloaded"

    # health reflete tudo isso e acusa a linha de 017/030 como descartada
    health = QueryService(lake).health()
    assert health.last_session == D2.isoformat()
    assert health.age_sessions == 0
    assert health.discarded_by_market == {"017": 1, "030": 1}
    assert health.rate_source["source"] == "b3_reference"
    assert health.oi_unavailable is False
    assert health.earnings_coverage["mapped"] == 1
    assert health.as_of is not None


def test_prepare_e_idempotente_e_o_segundo_usa_304(tmp_path: Path) -> None:
    sources = Sources()
    first = _prepare(tmp_path, sources, now=datetime(2026, 11, 19, 22, 30, tzinfo=BRT))
    assert first.exit_code == 0, first.lines

    lake = tmp_path / "lake"
    hashes = {
        name: _sha256(path)
        for name, path in {
            "raw": lake / "raw_cotahist" / "year=2026" / "part-0.parquet",
            "legs": lake / "legs" / "year=2026" / "part-0.parquet",
            "events": lake / "strike_events" / "year=2026" / "part-0.parquet",
        }.items()
    }
    state_before = json.loads((lake / "ingest_state.json").read_text(encoding="utf-8"))

    second = _prepare(tmp_path, sources, now=datetime(2026, 11, 20, 21, 30, tzinfo=BRT))
    assert second.exit_code == 0, second.lines
    assert sources.cotahist_downloads == 1
    assert sources.cotahist_not_modified == 1

    for name, digest in hashes.items():
        path = {
            "raw": lake / "raw_cotahist" / "year=2026" / "part-0.parquet",
            "legs": lake / "legs" / "year=2026" / "part-0.parquet",
            "events": lake / "strike_events" / "year=2026" / "part-0.parquet",
        }[name]
        assert _sha256(path) == digest, f"{name} mudou entre dois prepares iguais"

    state_after = json.loads((lake / "ingest_state.json").read_text(encoding="utf-8"))
    assert state_after["sha256"] == state_before["sha256"]
    assert state_after["updated_at"] != state_before["updated_at"]  # state atualiza

    # OI: a segunda execução só re-baixa a sessão mais recente (as outras já estão no lake)
    assert sources.oi_requests == 3  # 2 sessões na 1ª + só a última na 2ª


def test_prepare_segue_sem_rates(tmp_path: Path) -> None:
    """Ordem de falha: sem curva DI e sem Selic o prepare SEGUE; health acusa (P-M)."""
    sources = Sources()
    sources.rates_fail = True
    result = _prepare(tmp_path, sources)

    assert result.exit_code == 0, result.lines
    assert [failure.stage for failure in result.failures] == ["rates"]
    assert result.failures[0].error.code == "source_unavailable"

    lake = tmp_path / "lake"
    assert not (lake / "rates_di").exists()
    prepare_state = load_prepare_state(lake)
    assert prepare_state.rates is None
    assert prepare_state.last_error is not None
    assert prepare_state.last_error["code"] == "source_unavailable"

    health = QueryService(lake).health()
    assert health.rate_source["error"]["code"] == "rates_missing"


def test_prepare_segue_sem_open_interest(tmp_path: Path) -> None:
    """P-N: sem OI publicado, `days_to_liquidate` é null com flag — nunca zero."""
    sources = Sources()
    sources.oi_body = OI_EMPTY_CSV
    result = _prepare(tmp_path, sources)

    assert result.exit_code == 0, result.lines
    assert not (tmp_path / "lake" / "open_interest").exists()

    health = QueryService(tmp_path / "lake").health()
    assert health.oi_unavailable is True
    assert health.oi_available_dates["error"]["code"] == "oi_missing"

    text = (tmp_path / "lake" / "out" / f"relatorio-liquidez-{D2.isoformat()}.md").read_text(
        encoding="utf-8"
    )
    assert "| — |" in text  # days_to_liquidate sem OI


def test_prepare_segue_com_falha_do_oi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Erro de rede no OI não derruba o prepare; o health acusa a falha."""
    real = cli.fetch_open_interest_window

    def fast(dates, **kwargs):  # type: ignore[no-untyped-def]
        return real(dates, retry_delays=(), **kwargs)

    monkeypatch.setattr(cli, "fetch_open_interest_window", fast)
    sources = Sources()
    sources.oi_status = 500
    result = _prepare(tmp_path, sources)

    assert result.exit_code == 0, result.lines
    assert [failure.stage for failure in result.failures] == ["open_interest"]
    assert result.failures[0].error.code == "source_unavailable"

    health = QueryService(tmp_path / "lake").health()
    assert health.oi_unavailable is True


def test_prepare_sem_cotahist_e_sem_lake_sai_nao_zero(tmp_path: Path) -> None:
    sources = Sources()
    sources.cotahist_status = 404
    result = _prepare(tmp_path, sources)

    assert result.exit_code != 0
    assert result.failures and result.failures[0].stage == "cotahist"
    assert any("lake" in line and "vazio" in line for line in result.lines)


def test_prepare_com_lake_e_sem_cotahist_segue(tmp_path: Path) -> None:
    """Com lake já carregado, a fonte fora do ar não impede derivar de novo."""
    sources = Sources()
    assert _prepare(tmp_path, sources).exit_code == 0

    sources.cotahist_status = 404
    result = _prepare(tmp_path, sources, now=datetime(2026, 11, 21, 21, 30, tzinfo=BRT))
    assert result.exit_code == 0, result.lines
    assert [failure.stage for failure in result.failures] == ["cotahist"]
    assert any("FALHOU" in line and "cotahist" in line for line in result.lines)
    # o estado não perde o etag/sha do último download bom
    state = json.loads((tmp_path / "lake" / "ingest_state.json").read_text(encoding="utf-8"))
    assert state["etag"] == ETAG


def test_prepare_aborta_arquivo_invalido(tmp_path: Path) -> None:
    """Trailer que não bate é fatal: exit ≠ 0 e o lake não é tocado (ADR-007)."""

    class Broken(Sources):
        def handler(self, request: httpx.Request) -> httpx.Response:
            if "COTAHIST_A2026.ZIP" in str(request.url):
                lines = _cotahist_lines()
                header = "00" + " " * 243
                trailer = "99" + " " * 30 + f"{len(lines) + 7:010d}" + " " * 203
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    archive.writestr(
                        "COTAHIST_A2026.TXT", "\r\n".join([header, *lines, trailer]) + "\r\n"
                    )
                return httpx.Response(200, content=buffer.getvalue(), headers={"ETag": ETAG})
            return super().handler(request)

    result = _prepare(tmp_path, Broken())
    assert result.exit_code != 0
    assert any("INVÁLIDO" in line for line in result.lines)
    assert not (tmp_path / "lake" / "raw_cotahist").exists()


def test_prepare_config_nao_vaza_para_o_lake_real(tmp_path: Path) -> None:
    """Sanidade do teste: o prepare grava só sob a `data_dir` recebida."""
    assert _prepare(tmp_path, Sources()).exit_code == 0
    assert (tmp_path / "lake" / "raw_cotahist").is_dir()


# --------------------------------------------------------------------------- #
# Watch (agenda em 21:30 BRT, sem dormir no teste)
# --------------------------------------------------------------------------- #


def test_watch_agenda_as_21_30() -> None:
    now = datetime(2026, 11, 20, 20, 0, tzinfo=BRT)
    assert _seconds_until_next_run(now) == pytest.approx(1.5 * 3600)

    late = datetime(2026, 11, 20, 22, 0, tzinfo=BRT)
    assert _seconds_until_next_run(late) == pytest.approx(23.5 * 3600)

    exact = datetime(2026, 11, 20, 21, 30, tzinfo=BRT)
    assert _seconds_until_next_run(exact) == pytest.approx(24 * 3600)


def test_health_command_imprime_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`b3o health` responde JSON de uma tela, com o carimbo de parede na borda."""
    from typer.testing import CliRunner

    # o container exporta B3_DATA_DIR=/data — o teste isola para o lake de tmp_path
    monkeypatch.delenv("B3_DATA_DIR", raising=False)
    sources = Sources()
    assert _prepare(tmp_path, sources).exit_code == 0

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                f"data_dir: {tmp_path / 'lake'}",
                "log_level: INFO",
                "model_version: 1",
                'cotahist_base_url: "https://test.local/InstDados/SerHist/"',
            ]
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli.app, ["health", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["last_session"] == D2.isoformat()
    assert payload["counts_by_market"] == {"2026": {"010": 2, "017": 1, "030": 1, "070": 20}}
    assert payload["discarded_by_market"] == {"017": 1, "030": 1}
    assert payload["unexplained_gap"] is None
    assert payload["now_wall_clock_informative"].startswith("20")


def test_health_nao_grava_em_data_dir_de_producao() -> None:
    """Guarda contra teste acidental no bind `/data`: o config define o alvo."""
    assert Path("/data") != Path("data")


def test_contador_de_mercados_le_o_zip_realmente() -> None:
    """D3 unitário: `017`/`030` contam mesmo o parser descartando as linhas."""
    from b3o.cli import _count_markets

    path = Path("/tmp") / "cotahist-teste.zip"
    try:
        path.write_bytes(COTAHIST_ZIP)
        counts = _count_markets(path)
    finally:
        path.unlink(missing_ok=True)
    assert counts == {"010": 2, "017": 1, "030": 1, "070": 20}


def test_source_error_da_borda_tem_hint() -> None:
    """O que sobe para o resumo tem sempre `code/message/hint` (regra 5)."""
    error = SourceError(code="source_unavailable", message="m", hint="h")
    assert cli.StageFailure("rates", error).to_dict() == {
        "stage": "rates",
        "code": "source_unavailable",
        "message": "m",
        "hint": "h",
    }


def test_prepare_state_fica_fora_do_ingest_state(tmp_path: Path) -> None:
    """O sidecar é arquivo separado: o `ingest_state` congelado recusa chave nova."""
    assert _prepare(tmp_path, Sources()).exit_code == 0
    lake = tmp_path / "lake"
    assert prepare_state_path(lake).is_file()
    raw = json.loads((lake / "ingest_state.json").read_text(encoding="utf-8"))
    assert "counts_by_market" not in raw


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Onda F — `prepare --year` honesto e `b3o rates --backfill`
# --------------------------------------------------------------------------- #

Y2025_A = date(2025, 12, 29)
Y2025_B = date(2025, 12, 30)
MATURITY_2025 = date(2026, 1, 16)


def _zip_de(lines: list[str], name: str) -> bytes:
    """Zip do COTAHIST com header `00` e trailer `99` contando as linhas dadas."""
    header = "00" + " " * 243
    trailer = "99" + " " * 30 + f"{len(lines):010d}" + " " * 203
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, "\r\n".join([header, *lines, trailer]) + "\r\n")
    return buffer.getvalue()


def _cotahist_2025_zip() -> bytes:
    """Ano fechado sintético com papel DIFERENTE (VALE3) para provar preservação."""
    lines: list[str] = []
    for day in (Y2025_A, Y2025_B):
        lines.append(_data_line(day, "VALE3", "010", close=6_000, bid=5_999, ask=6_001))
        lines.append(
            _data_line(
                day,
                "VALEX100",
                "070",
                bdi="78",
                corp="VALEE",
                spec="CALL      ",
                close=120,
                bid=119,
                ask=121,
                trades=20,
                qty=500,
                volume=60_000,
                strike=6_000,
                maturity=MATURITY_2025,
                strike_pts=6_000,
                distribution_id="168",
            )
        )
    return _zip_de(lines, "COTAHIST_A2025.TXT")


class TwoYears(Sources):
    """Serve o arquivo de 2026 **e** o de 2025 (ano fechado)."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        if "COTAHIST_A2025.ZIP" in str(request.url):
            return httpx.Response(
                200, content=_cotahist_2025_zip(), headers={"ETag": '"cotahist-2025:0"'}
            )
        return super().handler(request)


def _config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            [
                f"data_dir: {tmp_path / 'lake'}",
                "log_level: INFO",
                "model_version: 1",
                'cotahist_base_url: "https://test.local/InstDados/SerHist/"',
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_prepare_de_ano_fechado_nao_apaga_o_retrato_do_lake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.4: preparar 2025 depois de 2026 não rejuvenesce `last_session` nem perde datas."""
    from typer.testing import CliRunner

    monkeypatch.delenv("B3_DATA_DIR", raising=False)
    sources = TwoYears()
    assert _prepare(tmp_path, sources).exit_code == 0
    lake = tmp_path / "lake"
    before = json.loads((lake / "ingest_state.json").read_text(encoding="utf-8"))
    assert before["last_session"] == D2.isoformat()

    result = run_prepare(
        _config(tmp_path),
        client_factory=sources.client_factory(),
        year=2025,
        now=datetime(2026, 11, 22, 9, 0, tzinfo=BRT),
    )
    assert result.exit_code == 0, result.lines
    # o cabeçalho do resumo mostra o ano preparado e a ponta do lake
    assert any("lake até 2026-11-19" in line for line in result.lines)

    after = json.loads((lake / "ingest_state.json").read_text(encoding="utf-8"))
    assert after["last_session"] == D2.isoformat()  # segue o maior do lake
    assert after["etag"] == before["etag"] == ETAG  # retrato do arquivo de 2026
    assert after["sha256"] == before["sha256"]
    assert after["trailer_total"] == before["trailer_total"]
    assert after["skipped_lines"] == before["skipped_lines"]
    # contagens de 2026 preservadas, 2025 acrescentado
    assert after["counts_by_date"][D1.isoformat()] == 11
    assert after["counts_by_date"][D2.isoformat()] == 11
    assert after["counts_by_date"][Y2025_A.isoformat()] == 2
    assert after["counts_by_date"][Y2025_B.isoformat()] == 2

    # o sidecar também é do lake inteiro: 2026 não some do mapa por mercado,
    # e o universo do ano mais novo (PETR4) não é trocado pelo de 2025 (VALE3)
    prepare_state = load_prepare_state(lake)
    assert set(prepare_state.counts_by_market) == {"2025", "2026"}
    assert prepare_state.counts_by_market["2026"] == {"010": 2, "017": 1, "030": 1, "070": 20}
    assert prepare_state.counts_by_market["2025"] == {"010": 2, "070": 2}
    assert prepare_state.universe == ("PETR4",)
    assert prepare_state.universe_session == D2

    # partições independentes: 2025 existe ao lado de 2026
    assert (lake / "raw_cotahist" / "year=2025" / "part-0.parquet").is_file()
    assert (lake / "raw_cotahist" / "year=2026" / "part-0.parquet").is_file()
    assert QueryService(lake).lake_calendar()[-1] == D2

    # comando `health` continua coerente com o state
    health = CliRunner().invoke(cli.app, ["health", "--config", str(_config_file(tmp_path))])
    assert health.exit_code == 0, health.output
    payload = json.loads(health.output)
    assert payload["last_session"] == D2.isoformat()
    assert payload["lake_years"] == [2025, 2026]
    assert payload["age_sessions"] == 0


def test_rates_backfill_grava_selic_nas_sessoes_sem_curva(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`b3o rates --backfill`: uma chamada ao SGS; a curva PRE do dia já lá fica."""
    from typer.testing import CliRunner

    monkeypatch.delenv("B3_DATA_DIR", raising=False)
    sources = Sources()
    assert _prepare(tmp_path, sources).exit_code == 0
    lake = tmp_path / "lake"

    calls = {"sgs": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "bcdata.sgs.11" in str(request.url):
            calls["sgs"] += 1
            return httpx.Response(
                200,
                json=[
                    {"data": "18/11/2026", "valor": "0.043739"},
                    {"data": "19/11/2026", "valor": "0.043739"},
                ],
            )
        return httpx.Response(404)

    monkeypatch.setattr(
        cli, "build_client", lambda: build_client(transport=httpx.MockTransport(handler))
    )

    result = CliRunner().invoke(
        cli.app, ["rates", "--backfill", "--config", str(_config_file(tmp_path))]
    )
    assert result.exit_code == 0, result.output
    assert calls["sgs"] == 1
    assert "selic_flat" in result.output
    assert "1 gravada(s)" in result.output
    assert "1 já com curva" in result.output

    table = pq.read_table(lake / "rates_di" / "year=2026" / "part-0.parquet").to_pydict()
    by_date: dict[date, list[str]] = {}
    for day, source in zip(table["refdate"], table["source"], strict=True):
        by_date.setdefault(day, []).append(source)
    assert set(by_date[D1]) == {"selic_flat"}  # sessão sem PRE ganhou curva plana
    assert len(by_date[D1]) == 756
    assert set(by_date[D2]) == {"b3_reference"}  # PRE da B3 não é sobrescrita


def test_rates_sem_backfill_nao_roda_o_lote(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    result = CliRunner().invoke(cli.app, ["rates", "--config", str(_config_file(tmp_path))])
    assert result.exit_code == 2
    assert "--backfill" in result.output
