"""Testes da validação do COTAHIST (F0.4) sobre zips sintéticos.

As linhas são montadas aqui com 245 bytes exatos (latin-1, `\\r\\n`, como o arquivo
da B3) para provar as regras do contrato sem depender do arquivo de 70 MB.
O número de linhas é conferido à mão: header + N dados + trailer ⇒ `line_count == N`
e `trailer_total == N`.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from b3o.ingest.validate import LINE_WIDTH, validate_cotahist

MEMBER = "COTAHIST_A2026.TXT"
ZIP_NAME = "COTAHIST_A2026.ZIP"


def _put(line: list[str], start: int, end: int, value: str) -> None:
    """Escreve `value` alinhado à direita em `line[start:end]` (como o layout da B3)."""
    line[start:end] = list(value.rjust(end - start)[: end - start])


def header_line() -> str:
    line = [" "] * LINE_WIDTH
    _put(line, 0, 2, "00")
    _put(line, 2, 10, "20261119")
    _put(line, 27, 39, "BOLSA DE VALORES")
    return "".join(line)


def data_line(
    *,
    refdate: str = "20261119",
    ticker: str = "PETRK410",
    open_: str = "1300",
    maturity: str = "20270115",
) -> str:
    line = [" "] * LINE_WIDTH
    _put(line, 0, 2, "01")
    _put(line, 2, 10, refdate)
    _put(line, 10, 12, "78")
    _put(line, 12, 24, ticker)
    _put(line, 24, 27, "070")
    _put(line, 27, 39, "PETR")
    _put(line, 39, 49, "ON      NM")
    _put(line, 52, 54, "R$")  # a moeda mora aqui; [49:52] é lixo variável
    _put(line, 56, 69, open_)
    _put(line, 69, 82, "1300")
    _put(line, 82, 95, "1300")
    _put(line, 95, 108, "1300")
    _put(line, 108, 121, "1300")
    _put(line, 121, 134, "1252")
    _put(line, 134, 147, "1359")
    _put(line, 147, 152, "1")
    _put(line, 152, 170, "400")
    _put(line, 170, 188, "520000")
    _put(line, 188, 201, "3569")
    _put(line, 201, 202, "-")
    _put(line, 202, 210, maturity)
    _put(line, 210, 217, "1")
    _put(line, 217, 230, "0")
    _put(line, 230, 242, "BRPETRACNOR9")
    _put(line, 242, 245, "208")
    return "".join(line)


def trailer_line(total: int) -> str:
    line = [" "] * LINE_WIDTH
    _put(line, 0, 2, "99")
    _put(line, 2, 10, "20261119")
    _put(line, 32, 42, f"{total:010d}")  # total de linhas "01" (medido: 0003570794)
    return "".join(line)


def make_zip(tmp_path: Path, lines: list[str], *, member: str = MEMBER) -> Path:
    payload = "".join(f"{line}\r\n" for line in lines).encode("latin-1")
    path = tmp_path / ZIP_NAME
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, payload)
    return path


# Linha real do COTAHIST 2026 (2.087.780ª): corp_name "FROTA OCEÂNICA" veio em UTF-8
# (\xc3\x82 = "Â") dentro de um arquivo latin-1, então ela tem 246 bytes e os offsets
# seguintes ficam deslocados em 1. É a única linha assim em 3.570.794 (medido 22/11/2026).
REAL_WIDE_LINE = (
    b"012026102252FR0T5L      017FROTA OCE\xc3\x82NIPNA*         R$  000"
    b"000000000500000000000050000000000005000000000000500000000000"
    b"050000000000000000000000000000001000000000006930312000000000"
    b"000034652000000000000009999123100010000000000000000BRFR0TACN"
    b"PA9109"
)


def test_zip_valido_confere_contagem_do_trailer(tmp_path: Path) -> None:
    # 1 header + 3 dados + 1 trailer. Contado à mão: line_count = 3, trailer = 3.
    path = make_zip(
        tmp_path,
        [header_line(), data_line(), data_line(ticker="PETRM410"), data_line(), trailer_line(3)],
    )

    report = validate_cotahist(path)

    assert report.ok is True
    assert report.zip_path == path
    assert report.header_ok is True
    assert report.line_count == 3
    assert report.trailer_total == 3
    assert report.count_match is True
    assert report.bad_width == ()
    assert report.bad_numeric == ()
    assert report.errors == ()
    assert report.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_maturity_sem_vencimento_99991231_e_aceita(tmp_path: Path) -> None:
    path = make_zip(
        tmp_path,
        [header_line(), data_line(maturity="99991231"), trailer_line(1)],
    )

    report = validate_cotahist(path)

    assert report.ok is True
    assert report.errors == ()


def test_trailer_com_total_errado_vira_count_match_false(tmp_path: Path) -> None:
    # 2 linhas de dados e trailer dizendo 4: a conta não fecha.
    path = make_zip(
        tmp_path,
        [header_line(), data_line(), data_line(), trailer_line(4)],
    )

    report = validate_cotahist(path)

    assert report.line_count == 2
    assert report.trailer_total == 4
    assert report.count_match is False
    assert report.ok is False
    assert report.errors == ()  # erro de contagem não é erro de I/O


def test_linha_de_largura_244_entra_em_bad_width(tmp_path: Path) -> None:
    torta = data_line()[: LINE_WIDTH - 1]
    assert len(torta) == 244
    path = make_zip(
        tmp_path,
        [header_line(), data_line(), torta, trailer_line(2)],
    )

    report = validate_cotahist(path)

    assert len(report.bad_width) == 1
    assert report.bad_width[0].line_no == 3  # header é 1, primeira data é 2
    assert report.bad_width[0].kind == "width"
    assert report.line_count == 2  # a linha torta continua contando para o trailer
    assert report.count_match is True
    assert report.ok is False


def test_campo_numerico_com_letra_entra_em_bad_numeric(tmp_path: Path) -> None:
    path = make_zip(
        tmp_path,
        [header_line(), data_line(open_="00000000043A3"), trailer_line(1)],
    )

    report = validate_cotahist(path)

    assert len(report.bad_numeric) == 1
    assert report.bad_numeric[0].line_no == 2
    assert report.bad_numeric[0].kind == "numeric"
    assert "open" in report.bad_numeric[0].excerpt
    assert report.ok is False


def test_linha_real_de_246_bytes_do_arquivo_de_2026_e_acusada(tmp_path: Path) -> None:
    """Regressão do arquivo real: 1 linha em 3.570.794 tem 246 bytes (UTF-8 no meio)."""
    assert len(REAL_WIDE_LINE) == 246

    path = make_zip(
        tmp_path,
        [header_line(), REAL_WIDE_LINE.decode("latin-1"), trailer_line(1)],
    )

    report = validate_cotahist(path)

    assert report.header_ok is True
    assert report.line_count == 1  # a linha torta continua contando para o trailer
    assert report.trailer_total == 1
    assert report.count_match is True
    assert len(report.bad_width) == 1
    assert report.bad_width[0].line_no == 2
    assert report.bad_width[0].kind == "width"
    assert report.ok is False


def test_data_invalida_entra_em_errors(tmp_path: Path) -> None:
    path = make_zip(
        tmp_path,
        [header_line(), data_line(refdate="20261332"), trailer_line(1)],
    )

    report = validate_cotahist(path)

    assert len(report.errors) == 1
    assert report.errors[0].code == "bad_data"
    assert "date" in report.errors[0].message
    assert report.errors[0].hint.strip()
    assert report.ok is False


def test_maturity_invalida_entra_em_errors(tmp_path: Path) -> None:
    path = make_zip(
        tmp_path,
        [header_line(), data_line(maturity="20261332"), trailer_line(1)],
    )

    report = validate_cotahist(path)

    assert len(report.errors) == 1
    assert "maturity" in report.errors[0].message
    assert report.ok is False


def test_sem_trailer_count_match_e_none(tmp_path: Path) -> None:
    path = make_zip(tmp_path, [header_line(), data_line(), data_line()])

    report = validate_cotahist(path)

    assert report.header_ok is True
    assert report.line_count == 2
    assert report.trailer_total is None
    assert report.count_match is None
    assert report.ok is False


def test_sem_header_header_ok_false(tmp_path: Path) -> None:
    path = make_zip(tmp_path, [data_line(), trailer_line(1)])

    report = validate_cotahist(path)

    assert report.header_ok is False
    assert report.line_count == 1
    assert report.count_match is True  # a contagem está certa; falta o header
    assert report.ok is False


def test_listas_de_linhas_ruins_ficam_limitadas_a_20(tmp_path: Path) -> None:
    # 25 linhas tortas: relatório guarda as 20 primeiras, mas conta as 25 no trailer.
    tortas = [data_line()[:244] for _ in range(25)]
    path = make_zip(tmp_path, [header_line(), *tortas, trailer_line(25)])

    report = validate_cotahist(path)

    assert report.line_count == 25
    assert report.trailer_total == 25
    assert report.count_match is True
    assert len(report.bad_width) == 20
    assert report.bad_width[0].line_no == 2
    assert report.bad_width[-1].line_no == 21
    assert report.ok is False


def test_zip_com_dois_membros_e_erro(tmp_path: Path) -> None:
    path = tmp_path / ZIP_NAME
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(MEMBER, f"{header_line()}\r\n")
        archive.writestr("outro.txt", "x")

    report = validate_cotahist(path)

    assert report.ok is False
    assert report.line_count == 0
    assert len(report.errors) == 1
    assert "membro" in report.errors[0].message


def test_arquivo_inexistente_vira_relatorio_com_erro(tmp_path: Path) -> None:
    report = validate_cotahist(tmp_path / "nao-existe.ZIP")

    assert report.ok is False
    assert report.sha256 == ""
    assert len(report.errors) == 1
    assert report.errors[0].code == "bad_data"
    assert report.errors[0].hint.strip()
