"""Testes do lake Parquet (F0.5, contrato Onda B §2).

Cobrem o que o contrato exige: ordem/tipo de coluna, round-trip campo a campo (inclusive
`None`), idempotência **provada** pelos bytes, escrita atômica com falha no meio do iterável,
partição reconstruída inteira, falha explícita (ano divergente, lote vazio, partição ausente,
Parquet corrompido) e leitura em streaming.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Iterator
from dataclasses import fields
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from b3o.ingest.errors import SourceFailure
from b3o.ingest.parse import CotahistRow
from b3o.store.lake import (
    PARTITION_FILE,
    RAW_COTAHIST_COLUMNS,
    RAW_COTAHIST_SCHEMA,
    cotahist_partition_path,
    read_cotahist_years,
    write_cotahist_year,
)

D19 = date(2026, 11, 19)
D18 = date(2026, 11, 18)
DEC18 = date(2026, 12, 18)
UNDERLYING_ISIN = "BRPETRACNPR6"


def _spot(**overrides: object) -> CotahistRow:
    """Linha à vista da PETR4 com valores conhecidos (conferidos à mão no teste do schema)."""
    base: dict[str, object] = {
        "refdate": D19,
        "bdi": "02",
        "ticker": "PETR4",
        "tpmerc": "010",
        "corp_name": "PETROBRAS   PN",
        "spec": "ON      N2",
        "open": 3845,
        "high": 3890,
        "low": 3810,
        "avg": 3852,
        "close": 3870,
        "best_bid": 3869,
        "best_ask": 3871,
        "trades": 52134,
        "qty": 1_234_567_890,
        # > 2**31: volume tem que ser int64 (21/11/2026)
        "volume": 4_754_123_456_789,
        "strike": 0,
        "strike_adj_ind": "0",
        "maturity": None,
        "quote_factor": 1,
        "strike_pts": 0,
        "isin_underlying": UNDERLYING_ISIN,
        "distribution_id": "",
        "style": None,
        "side": None,
    }
    base.update(overrides)
    return CotahistRow(**base)  # type: ignore[arg-type]


def _call(**overrides: object) -> CotahistRow:
    """Call europeia `PETRE410`: strike R$ 40,00, FATCOT 100, estilo `E`."""
    base: dict[str, object] = {
        "refdate": D19,
        "bdi": "78",
        "ticker": "PETRE410",
        "tpmerc": "070",
        "corp_name": "PETRE",
        "spec": "CALL      ",
        "open": 201,
        "high": 250,
        "low": 180,
        "avg": 215,
        "close": 240,
        "best_bid": 238,
        "best_ask": 242,
        "trades": 350,
        "qty": 1_234_500,
        "volume": 26_542_000,
        "strike": 4000,
        "strike_adj_ind": "0",
        "maturity": DEC18,
        "quote_factor": 100,
        "strike_pts": 4000,
        "isin_underlying": UNDERLYING_ISIN,
        "distribution_id": "410",
        "style": "E",
        "side": "C",
    }
    base.update(overrides)
    return CotahistRow(**base)  # type: ignore[arg-type]


def _put(**overrides: object) -> CotahistRow:
    """Put europeia `PETRV352`: strike R$ 35,00."""
    base: dict[str, object] = {
        "refdate": D19,
        "bdi": "82",
        "ticker": "PETRV352",
        "tpmerc": "080",
        "corp_name": "PETRE",
        "spec": "PUT       ",
        "open": 275,
        "high": 300,
        "low": 260,
        "avg": 282,
        "close": 290,
        "best_bid": 288,
        "best_ask": 292,
        "trades": 1200,
        "qty": 8_765_400,
        "volume": 24_718_428,
        "strike": 3500,
        "strike_adj_ind": "0",
        "maturity": DEC18,
        "quote_factor": 100,
        "strike_pts": 3500,
        "isin_underlying": UNDERLYING_ISIN,
        "distribution_id": "352",
        "style": "E",
        "side": "P",
    }
    base.update(overrides)
    return CotahistRow(**base)  # type: ignore[arg-type]


def _synthetic(count: int, *, refdate: date = D19) -> Iterator[CotahistRow]:
    """Gerador de `count` linhas sintéticas (não materializa o lote inteiro)."""
    for index in range(count):
        yield _call(
            refdate=refdate,
            ticker=f"TST{index:07d}",
            strike=1000 + index,
            close=100 + (index % 50),
        )


def _tmp_leftovers(data_dir: Path) -> list[Path]:
    tmp_dir = data_dir / "tmp"
    if not tmp_dir.exists():
        return []
    return sorted(path for path in tmp_dir.rglob("*") if path.is_file())


def test_colunas_do_parquet_batem_com_o_dataclass() -> None:
    """A ordem canônica do contrato é a ordem do `CotahistRow` (o reader constrói por posição)."""
    assert [field.name for field in fields(CotahistRow)] == RAW_COTAHIST_COLUMNS
    assert len(RAW_COTAHIST_COLUMNS) == 25
    assert RAW_COTAHIST_SCHEMA.names == RAW_COTAHIST_COLUMNS


def test_write_e_round_trip_campo_a_campo(tmp_path: Path) -> None:
    rows = [_spot(), _call(), _put()]
    stats = write_cotahist_year(rows, 2026, tmp_path)

    expected_path = tmp_path / "raw_cotahist" / "year=2026" / "part-0.parquet"
    assert stats.path == expected_path
    assert stats.rows == 3
    assert stats.seconds >= 0
    assert stats.sha256 == hashlib.sha256(expected_path.read_bytes()).hexdigest()
    assert cotahist_partition_path(tmp_path, 2026) == expected_path

    read_back = list(read_cotahist_years(tmp_path, [2026]))
    assert read_back == rows  # dataclass eq: cobre None de maturity/style/side


def test_idempotencia_e_provada_pelos_bytes(tmp_path: Path) -> None:
    """Duas escritas do mesmo lote → mesmo `sha256` E mesmos bytes no disco."""
    first = write_cotahist_year([_spot(), _call(), _put()], 2026, tmp_path)
    first_bytes = first.path.read_bytes()

    second = write_cotahist_year([_spot(), _call(), _put()], 2026, tmp_path)
    assert second.sha256 == first.sha256
    assert second.path.read_bytes() == first_bytes
    # e o conteúdo lido é igual, na mesma ordem
    assert list(read_cotahist_years(tmp_path, [2026])) == [_spot(), _call(), _put()]


def test_escrita_atomica_falha_no_meio_preserva_particao(tmp_path: Path) -> None:
    path = write_cotahist_year([_spot(), _call(), _put()], 2026, tmp_path).path
    before = path.read_bytes()

    def explodes() -> Iterator[CotahistRow]:
        yield _spot(refdate=D18, close=1)
        yield _call(refdate=D18, close=2)
        raise RuntimeError("falha injetada no meio da escrita")

    with pytest.raises(RuntimeError, match="falha injetada"):
        write_cotahist_year(explodes(), 2026, tmp_path)

    assert path.read_bytes() == before  # partição antiga intacta
    assert _tmp_leftovers(tmp_path) == []  # nenhum tmp órfão
    assert list(path.parent.glob("*.part")) == []
    assert sorted(p.name for p in path.parent.iterdir()) == [PARTITION_FILE]


def path_rows(data_dir: Path, year: int) -> list[CotahistRow]:
    """Atalho dos testes: todas as linhas de uma partição."""
    return list(read_cotahist_years(data_dir, [year]))


def test_particao_reconstruida_inteira_sem_append(tmp_path: Path) -> None:
    write_cotahist_year([_spot(), _call(), _put()], 2026, tmp_path)
    stale = tmp_path / "raw_cotahist" / "year=2026" / "part-1.parquet"
    stale.write_bytes(b"sobra de uma escrita antiga")

    stats = write_cotahist_year([_spot(refdate=D18)], 2026, tmp_path)

    assert stats.rows == 1
    assert path_rows(tmp_path, 2026) == [_spot(refdate=D18)]
    assert not stale.exists()  # a partição é exatamente part-0.parquet
    assert _tmp_leftovers(tmp_path) == []


def test_refdate_fora_do_ano_falha_explicito(tmp_path: Path) -> None:
    """`write_cotahist_year` recebe UM ano: linha de outro ano é erro, não filtro silencioso."""
    path = write_cotahist_year([_spot()], 2026, tmp_path).path
    before = path.read_bytes()

    with pytest.raises(SourceFailure) as excinfo:
        write_cotahist_year([_call(), _call(refdate=date(2025, 12, 30))], 2026, tmp_path)

    assert excinfo.value.code == "bad_data"
    assert "2025-12-30" in str(excinfo.value)
    assert path.read_bytes() == before
    assert _tmp_leftovers(tmp_path) == []


def test_lote_vazio_nao_apaga_particao(tmp_path: Path) -> None:
    path = write_cotahist_year([_spot()], 2026, tmp_path).path
    before = path.read_bytes()

    with pytest.raises(SourceFailure) as excinfo:
        write_cotahist_year(iter(()), 2026, tmp_path)

    assert excinfo.value.code == "bad_data"
    assert "nenhuma linha" in str(excinfo.value)
    assert path.read_bytes() == before
    assert _tmp_leftovers(tmp_path) == []


def test_data_dir_que_e_arquivo_falha_explicito(tmp_path: Path) -> None:
    file_path = tmp_path / "nao_e_diretorio"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(SourceFailure) as excinfo:
        write_cotahist_year([_spot()], 2026, file_path)
    assert excinfo.value.code == "bad_data"
    assert "não é diretório" in str(excinfo.value)

    with pytest.raises(SourceFailure) as excinfo:
        list(read_cotahist_years(file_path, [2026]))
    assert excinfo.value.code == "bad_data"


def test_schema_e_valores_crus_conferidos_a_mao(tmp_path: Path) -> None:
    """1 número conferido à mão: abre o Parquet direto pelo pyarrow e lê o valor cru."""
    path = write_cotahist_year([_spot(), _call(), _put()], 2026, tmp_path).path
    table = pq.read_table(path)

    assert table.schema.names == RAW_COTAHIST_COLUMNS
    assert "year" not in table.schema.names  # o ano vive no diretório, não no arquivo

    assert table.schema.field("refdate").type == pa.date32()
    assert table.schema.field("maturity").type == pa.date32()
    assert table.schema.field("style").type == pa.string()
    assert table.schema.field("side").type == pa.string()
    assert table.schema.field("refdate").nullable is False
    assert table.schema.field("style").nullable is True
    for name in ("open", "high", "low", "avg", "close", "volume", "strike", "qty", "trades"):
        assert table.schema.field(name).type == pa.int64(), name

    columns = table.to_pydict()
    # PETR4 à vista de 19/11/2026 sintética: R$ 38,45 de abertura, R$ 47,54 bi de volume
    assert columns["refdate"] == [D19, D19, D19]
    assert columns["open"][0] == 3845
    assert columns["close"][0] == 3870
    assert columns["volume"][0] == 4_754_123_456_789
    assert columns["ticker"] == ["PETR4", "PETRE410", "PETRV352"]
    assert columns["maturity"] == [None, DEC18, DEC18]
    assert columns["style"] == [None, "E", "E"]
    assert columns["side"] == [None, "C", "P"]
    assert columns["strike"] == [0, 4000, 3500]
    assert columns["quote_factor"] == [1, 100, 100]
    assert columns["distribution_id"] == ["", "410", "352"]


def test_ler_varios_anos_na_ordem_pedida(tmp_path: Path) -> None:
    write_cotahist_year([_spot(refdate=date(2025, 12, 30))], 2025, tmp_path)
    write_cotahist_year([_spot()], 2026, tmp_path)

    rows = list(read_cotahist_years(tmp_path, [2025, 2026]))
    assert [row.refdate.year for row in rows] == [2025, 2026]


def test_filtro_de_mercado_na_leitura(tmp_path: Path) -> None:
    write_cotahist_year([_spot(), _call(), _put()], 2026, tmp_path)

    only_spot = list(read_cotahist_years(tmp_path, [2026], markets=frozenset({"010"})))
    assert [row.ticker for row in only_spot] == ["PETR4"]

    no_options = list(read_cotahist_years(tmp_path, [2026], markets=frozenset({"010", "020"})))
    assert [row.ticker for row in no_options] == ["PETR4"]


def test_particao_ausente_falha_explicito(tmp_path: Path) -> None:
    with pytest.raises(SourceFailure) as excinfo:
        list(read_cotahist_years(tmp_path, [2024]))
    assert excinfo.value.code == "lake_missing"
    assert "2024" in str(excinfo.value)


def test_parquet_corrompido_falha_explicito(tmp_path: Path) -> None:
    path = cotahist_partition_path(tmp_path, 2026)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"isto nao e um parquet")

    with pytest.raises(SourceFailure) as excinfo:
        list(read_cotahist_years(tmp_path, [2026]))
    assert excinfo.value.code == "bad_data"


def test_schema_incompativel_falha_explicito(tmp_path: Path) -> None:
    """Partição de schema antigo não é lida "no melhor esforço": falha e manda reparsear."""
    path = cotahist_partition_path(tmp_path, 2026)
    path.parent.mkdir(parents=True)
    pq.write_table(pa.table({"refdate": [D19]}), path)

    with pytest.raises(SourceFailure) as excinfo:
        list(read_cotahist_years(tmp_path, [2026]))
    assert excinfo.value.code == "bad_data"
    assert "schema inesperado" in str(excinfo.value)


def test_leitura_e_gerador_de_verdade(tmp_path: Path) -> None:
    assert inspect.isgeneratorfunction(read_cotahist_years)
    write_cotahist_year(_synthetic(200), 2026, tmp_path)

    stream = read_cotahist_years(tmp_path, [2026])
    assert inspect.isgenerator(stream)
    first = next(stream)
    second = next(stream)
    assert (first.ticker, second.ticker) == ("TST0000000", "TST0000001")
    stream.close()  # consumo parcial: nada foi materializado de antemão


def test_round_trip_100k_linhas(tmp_path: Path) -> None:
    stats = write_cotahist_year(_synthetic(100_000), 2026, tmp_path)
    assert stats.rows == 100_000

    count = 0
    first: CotahistRow | None = None
    last: CotahistRow | None = None
    for row in read_cotahist_years(tmp_path, [2026]):
        if first is None:
            first = row
        last = row
        count += 1

    assert count == 100_000
    assert first is not None and first.ticker == "TST0000000"
    assert last is not None and last.ticker == "TST0099999"
    assert first.refdate == last.refdate == D19


def test_state_nao_e_tocado_pelo_lake(tmp_path: Path) -> None:
    """O lake não escreve `ingest_state.json`: quem grava estado é o C1/`state.py`."""
    write_cotahist_year([_spot()], 2026, tmp_path)
    assert not (tmp_path / "ingest_state.json").exists()
