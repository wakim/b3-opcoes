"""Testes do parser do COTAHIST.

Fixture `fixtures/cotahist_2026_sample.txt`: 10 linhas `01` REAIS copiadas do
`COTAHIST_A2026.ZIP` (pregão 2026-11-19) + header `00` + trailer `99`, bytes originais
(latin-1, 245 por linha, EOL CRLF). Os valores esperados estão em
`docs/contratos/onda-a.md` §5 e foram conferidos contra o bruto.

O recorte cobre: à vista (010), fracionário (020), exercício (012), call (070), put (080),
mercado fora do universo (021 — filtrado), token europeu (`PETRE`, `BBDCE`, `BMOBE`),
nome com `/EJ`, marcador semanal (`BBDCW182`) e `W` como código de mês (`BMOBW256`),
`FATCOT` 100 (`IBOVL7`).
"""

from __future__ import annotations

import inspect
import zipfile
from datetime import date
from pathlib import Path

import pytest

from b3o.ingest.errors import SourceError
from b3o.ingest.parse import (
    KEPT_MARKETS,
    LINE_WIDTH,
    CotahistParseError,
    CotahistRow,
    iter_cotahist,
    parse_line,
    style_of,
)

FIXTURE = Path(__file__).with_name("fixtures") / "cotahist_2026_sample.txt"

# Linhas reais do fixture, na ordem em que estão no arquivo.
TICKERS = [
    ("PETR4", "010"),
    ("ENEV3F", "020"),
    ("BEEFL262E", "012"),
    ("PETRK410", "070"),
    ("BBDCL178", "070"),
    ("IBOVL7", "070"),
    ("PETRM410", "080"),
    ("BBDCW182", "080"),
    ("BMOBW256", "080"),
    ("MOTV3Q", "021"),  # lote/bloco: fora de KEPT_MARKETS
]


def raw_lines() -> list[bytes]:
    """Linhas do fixture como bytes, sem o EOL."""
    data = FIXTURE.read_bytes()
    assert data.endswith(b"\r\n"), "fixture sem EOL final"
    return data.split(b"\r\n")[:-1]


def lines() -> list[str]:
    return [raw.decode("latin-1") for raw in raw_lines()]


def row(ticker: str, tpmerc: str) -> CotahistRow:
    """Linha do fixture já parseada (o fixture tem 1 linha por ticker)."""
    for line in lines():
        parsed = parse_line(line)
        if parsed is not None and parsed.ticker == ticker and parsed.tpmerc == tpmerc:
            return parsed
    raise AssertionError(f"{ticker}/{tpmerc} não está no fixture")


def zipped(tmp_path: Path, raw: list[bytes] | None = None) -> Path:
    """Fixture (ou as linhas dadas) dentro de um ZIP com o layout do COTAHIST."""
    payload = raw_lines() if raw is None else raw
    zip_path = tmp_path / "COTAHIST_A2026.ZIP"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("COTAHIST_A2026.TXT", b"\r\n".join(payload) + b"\r\n")
    return zip_path


def wider(line: str) -> str:
    """Insere 1 byte no nome resumido: reproduz a linha de 246 bytes do arquivo de 2026.

    Lá o nome é `FROTA OCEÂNICA` com um `Â` em UTF-8 (`C3 82`): 246 bytes em vez de 245.
    """
    return line[:30] + "\u00c2" + line[30:]


def replace(line: str, field: slice, value: str) -> str:
    """Troca um campo de largura fixa, preservando a largura da linha."""
    assert len(value) == field.stop - field.start
    return line[: field.start] + value + line[field.stop :]


def test_fixture_tem_header_dados_e_trailer() -> None:
    raw = raw_lines()
    assert len(raw) == 12  # header + 10 linhas de dados + trailer
    assert all(len(line) == LINE_WIDTH for line in raw)
    assert raw[0][:2] == b"00"
    assert raw[-1][:2] == b"99"
    assert sum(1 for line in raw if line[:2] == b"01") == 10


def test_golden_petr4_a_vista() -> None:
    """Contrato §5, coluna PETR4: tudo conferido contra o bruto."""
    r = row("PETR4", "010")
    assert r.refdate == date(2026, 11, 19)
    assert r.bdi == "02"
    assert r.tpmerc == "010"
    assert r.corp_name == "PETROBRAS"
    assert (r.open, r.high, r.low, r.avg, r.close) == (4353, 4360, 4241, 4291, 4267)
    assert (r.best_bid, r.best_ask) == (4265, 4267)
    assert r.trades == 45389
    assert r.qty == 39230100
    assert r.volume == 168353821600
    assert r.strike == 0
    assert r.strike_adj_ind == "0"
    assert r.maturity is None  # "99991231"
    assert r.quote_factor == 1
    assert r.strike_pts == 0
    assert r.isin_underlying == "BRPETRACNPR6"
    assert r.distribution_id == "230"
    assert r.style is None
    assert r.side is None


def test_volume_petr4_nao_fecha_qty_vezes_avg() -> None:
    """`avg` é o preço médio com 2 decimais; o volume é o financeiro exato da fonte.

    Medido (2026-11-19, PETR4): volume/qty = 4291,4196 ¢, então
    qty × avg = 168.336.359.100 ¢ fica **abaixo** do volume 168.353.821.600 ¢, e a
    diferença é menor que 1 ¢ por ação. A identidade exata `volume == qty × avg` do
    contrato §5 **não** fecha no arquivo: `volume` vem dos negócios, não de qty × avg.
    """
    r = row("PETR4", "010")
    assert r.volume // r.qty == r.avg  # truncado, confere com o avg publicado
    assert 0 < r.volume - r.qty * r.avg < r.qty  # menos de 1 centavo por ação de resto
    assert r.volume == 168353821600  # valor bruto, sem arredondamento no parse


def test_golden_petrk410_call() -> None:
    r = row("PETRK410", "070")
    assert r.refdate == date(2026, 11, 19)
    assert r.bdi == "78"
    assert r.tpmerc == "070"
    assert r.ticker == "PETRK410"
    assert r.corp_name == "PETR"  # token da americana
    assert (r.open, r.high, r.low, r.avg, r.close) == (1300, 1300, 1300, 1300, 1300)
    assert (r.best_bid, r.best_ask) == (1252, 1359)
    assert r.trades == 1
    assert r.qty == 400
    assert r.volume == 520000
    assert r.strike == 3569
    assert r.maturity == date(2026, 11, 19)
    assert r.quote_factor == 1
    assert r.isin_underlying == "BRPETRACNOR9"
    assert r.distribution_id == "208"
    assert r.style == "A"
    assert r.side == "C"


def test_golden_petrm410_put() -> None:
    r = row("PETRM410", "080")
    assert r.bdi == "82"
    assert r.tpmerc == "080"
    assert r.corp_name == "PETRE"  # token da europeia
    assert (r.open, r.high, r.low, r.avg, r.close) == (2, 3, 2, 2, 3)
    assert (r.best_bid, r.best_ask) == (0, 0)  # caso one_sided futuro
    assert r.trades == 2
    assert r.qty == 200
    assert r.volume == 500
    assert r.strike == 3569
    assert r.maturity == date(2027, 1, 15)
    assert r.isin_underlying == "BRPETRACNOR9"
    assert r.distribution_id == "208"
    assert r.style == "E"
    assert r.side == "P"


def test_isin_e_do_ativo_objeto_e_nao_da_serie() -> None:
    """Armadilha AGENTS §1: call e put do mesmo papel carregam o MESMO ISIN."""
    call = row("PETRK410", "070")
    put = row("PETRM410", "080")
    assert call.isin_underlying == put.isin_underlying == "BRPETRACNOR9"
    assert call.ticker != put.ticker


def test_ticker_com_espacos_a_esquerda_parseia_igual() -> None:
    """Campo de largura fixa: espaços à esquerda não podem entrar no ticker."""
    original = next(line for line in lines() if line[12:24].strip() == "PETRK410")
    assert original[12:24] == "PETRK410    "  # na fonte vem alinhado à esquerda
    right_aligned = replace(original, slice(12, 24), "    PETRK410")
    assert parse_line(right_aligned) == parse_line(original)
    assert parse_line(right_aligned).ticker == "PETRK410"


def test_strike_zero_e_tres_decimais_do_arquivo() -> None:
    """`strike_pts`[217:230] é outro campo (não o strike); à vista não há strike."""
    spot = row("PETR4", "010")
    assert spot.strike == 0 and spot.strike_pts == 0
    option = row("PETRK410", "070")
    assert option.strike == 3569  # R$ 35,69
    assert option.strike_pts == 0


def test_fatcot_cem_no_ibov() -> None:
    """FATCOT [210:217] ≠ 1: no índice o fator é 100 (e o strike é em pontos)."""
    r = row("IBOVL7", "070")
    assert r.quote_factor == 100
    assert r.volume == 3502150  # guardado como vem da fonte (já ÷FATCOT)
    assert r.strike == 17000000  # 170.000 pontos, 2 decimais implícitos: não é R$
    assert r.style == "E"
    assert r.side == "C"


def test_estilo_das_linhas_do_fixture() -> None:
    assert row("BBDCL178", "070").style == "E"  # call europeia (token BBDCE)
    assert row("BBDCW182", "080").style == "E"  # semanal: W182 depois de dígito
    assert row("BMOBW256", "080").style == "E"  # W é mês de put, não semanal
    assert row("BMOBW256", "080").corp_name == "BMOBE    /EJ"  # sufixo ex-direito
    assert row("PETR4", "010").style is None  # não é opção: token longo demais


def test_mercado_de_exercicio_nao_tem_estilo_nem_lado() -> None:
    """012/013 são registros de exercício (ticker com `E` no fim), não opções vivas.

    Documenta o comportamento medido da regra §4 nesses mercados: a raiz do ticker de
    exercício (`BEEFL262E`) não casa com o token (`BEEF`), então o estilo fica `None`.
    """
    r = row("BEEFL262E", "012")
    assert r.strike == 262
    assert r.maturity == date(2026, 12, 18)
    assert r.side is None
    assert r.style is None


def test_parse_line_pula_header_e_trailer() -> None:
    raw = lines()
    assert parse_line(raw[0]) is None
    assert parse_line(raw[-1]) is None


def test_iter_cotahist_e_gerador() -> None:
    assert inspect.isgeneratorfunction(iter_cotahist)


def test_iter_cotahist_le_o_zip_e_filtra_mercados(tmp_path: Path) -> None:
    zip_path = zipped(tmp_path)
    kept = list(iter_cotahist(zip_path))
    assert [(r.ticker, r.tpmerc) for r in kept] == [
        pair for pair in TICKERS if pair[1] in KEPT_MARKETS
    ]
    assert "MOTV3Q" not in {r.ticker for r in kept}  # 021 (lote/bloco) fica de fora


def test_iter_cotahist_respeita_o_filtro_pedido(tmp_path: Path) -> None:
    zip_path = zipped(tmp_path)
    only_lote = list(iter_cotahist(zip_path, markets=frozenset({"021"})))
    assert [(r.ticker, r.tpmerc) for r in only_lote] == [("MOTV3Q", "021")]
    assert list(iter_cotahist(zip_path, markets=frozenset())) == []


def test_iter_cotahist_pula_linha_torta_de_mercado_fora_do_universo(tmp_path: Path) -> None:
    """Medido: a única linha de 246 bytes do arquivo de 2026 é do mercado 017.

    Ela não pertence ao universo (`017` é leilão) e não pode derrubar a ingestão.
    """
    lote = next(line for line in lines() if line[24:27] == "021")
    torta = wider(lote)
    assert len(torta) == LINE_WIDTH + 1
    zip_path = zipped(tmp_path, [raw_lines()[0], torta.encode("latin-1"), raw_lines()[-1]])
    assert list(iter_cotahist(zip_path)) == []


def test_iter_cotahist_nao_perdoa_linha_torta_de_mercado_do_universo(tmp_path: Path) -> None:
    spot = next(line for line in lines() if line[12:24].strip() == "PETR4")
    torta = wider(spot)
    zip_path = zipped(tmp_path, [raw_lines()[0], torta.encode("latin-1"), raw_lines()[-1]])
    with pytest.raises(CotahistParseError) as caught:
        list(iter_cotahist(zip_path))
    assert "COTAHIST_A2026.TXT:2:" in caught.value.message  # número da linha
    assert "246" in caught.value.message
    assert caught.value.hint


def test_iter_cotahist_zip_ilegivel_falha_explicita(tmp_path: Path) -> None:
    bad = tmp_path / "COTAHIST_A2026.ZIP"
    bad.write_bytes(b"isto nao e um zip")
    with pytest.raises(CotahistParseError) as caught:
        list(iter_cotahist(bad))
    assert caught.value.code == "source_unavailable"
    assert caught.value.hint


def test_iter_cotahist_zip_ausente_falha_explicita(tmp_path: Path) -> None:
    with pytest.raises(CotahistParseError) as caught:
        list(iter_cotahist(tmp_path / "nao-existe.ZIP"))
    assert caught.value.code == "source_unavailable"
    assert caught.value.message


def test_parse_line_largura_errada_vira_erro_explicito() -> None:
    line = next(line for line in lines() if line[0:2] == "01")[: LINE_WIDTH - 1]
    with pytest.raises(CotahistParseError) as caught:
        parse_line(line)
    assert isinstance(caught.value.error, SourceError)
    assert caught.value.error.to_dict() == {
        "code": "bad_data",
        "message": caught.value.message,
        "hint": caught.value.hint,
    }
    assert caught.value.code == "bad_data"
    assert "244" in caught.value.message
    assert caught.value.hint
    assert caught.value.hint in str(caught.value)  # a dica não se perde no log


def test_parse_line_numerico_torto_vira_source_error() -> None:
    line = next(line for line in lines() if line[12:24].strip() == "PETR4")
    torta = replace(line, slice(147, 152), "ABCDE")  # trades
    with pytest.raises(CotahistParseError) as caught:
        parse_line(torta)
    assert caught.value.code == "bad_data"
    assert "trades" in caught.value.message
    assert caught.value.hint

    branco = replace(line, slice(108, 121), " " * 13)  # close em branco
    with pytest.raises(CotahistParseError) as caught:
        parse_line(branco)
    assert "close" in caught.value.message


def test_parse_line_data_invalida_vira_source_error() -> None:
    line = next(line for line in lines() if line[12:24].strip() == "PETR4")
    torta = replace(line, slice(2, 10), "20261332")  # mês 13
    with pytest.raises(CotahistParseError) as caught:
        parse_line(torta)
    assert "refdate" in caught.value.message


def test_parse_line_registro_desconhecido_vira_source_error() -> None:
    line = next(line for line in lines() if line[12:24].strip() == "PETR4")
    torta = replace(line, slice(0, 2), "77")
    with pytest.raises(CotahistParseError) as caught:
        parse_line(torta)
    assert "registro" in caught.value.message


def test_style_of_pure_function() -> None:
    """Casos medidos no arquivo de 2026 (contrato §4)."""
    cases = [
        ("PETRK410", "PETR", "A"),  # call americana: token = raiz pura
        ("PETRV352", "PETRE", "E"),  # put europeia com ticker de raiz comum
        ("BRAVD286", "BRAVA", "A"),  # raiz de 5 letras: token = raiz + 1
        ("BRAVM500", "BRAVAE", "E"),  # raiz de 5 letras: token = raiz + 2
        ("B3SAD557", "B3SAE", "E"),  # raiz com dígito no meio
        ("ABEVA140W1", "ABEV", "A"),  # semanal: W1 depois do strike
        ("BOVAW170", "BOVAE", "E"),  # W antes de dígito é mês de put, não semanal
        ("PETRA5", "PETROBRAS", None),  # token não é a raiz da série
        ("PETR4", "PETROBRAS", None),  # não é opção
        ("PETRK410", "", None),  # nome resumido vazio
        ("PETRK410", "PETRE  /EJ", "E"),  # sufixo ex-direito não interfere
        ("IBOVL7", "IBOVE  FM", "E"),
    ]
    for ticker, corp_name, expected in cases:
        assert style_of(ticker, corp_name) == expected, (ticker, corp_name)
