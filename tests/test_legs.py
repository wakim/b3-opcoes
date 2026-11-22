"""Legs de série (`build_legs`) — fronteira por strike/distribution_id, estilo por maioria.

Fixture `fixtures/strike_events_2026_sample.txt`: recorte REAL do `COTAHIST_A2026.ZIP`
(linhas `01` de opção `070`/`080` + as linhas `010` dos papéis), header `00` e trailer
`99` no layout original (latin-1, 245 bytes, CRLF). Cobre:

- SBSP 28-29/04/2026 — o desdobramento ×0,2 (106 séries);
- SIMH 13-18-19/02/2026 — o grupamento ×2 (7 séries contíguas + 25 não contíguas);
- LOGG 11-12/06/2026 — provento de R$ 2,86;
- ABEVX131 (15/06 → 23/06 → 18/09 → 22/09) e a amostra do ABEV de 06/2026 e 09/2026;
- SBSPJ451 em 15/09/2026 — mudança **só** de `distribution_id` (168 → 169, strike igual).

Os casos sintéticos usam `dataclasses.replace` sobre uma linha real já parseada: o
insumo do módulo é `CotahistRow`, não texto.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date
from pathlib import Path

from b3o.derive.legs import LegRow, build_legs
from b3o.ingest.parse import CotahistRow, parse_line

FIXTURE = Path(__file__).with_name("fixtures") / "strike_events_2026_sample.txt"
SRC = Path(__file__).resolve().parents[1] / "src"

MATURITY = date(2026, 12, 18)


def fixture_rows() -> list[CotahistRow]:
    """Linhas `01` do fixture (header e trailer viram `None` no parser)."""
    text = FIXTURE.read_bytes().decode("latin-1")
    parsed = (parse_line(line) for line in text.split("\r\n") if line)
    return [row for row in parsed if row is not None]


def base() -> CotahistRow:
    return next(row for row in fixture_rows() if row.tpmerc == "070")


def option(
    refdate: date,
    strike: int,
    *,
    ticker: str = "TESTE1",
    maturity: date = MATURITY,
    isin: str = "BRTESTACNOR0",
    style: str | None = "E",
    side: str = "C",
    tpmerc: str = "070",
    distribution_id: str = "208",
    strike_adj_ind: str = "0",
) -> CotahistRow:
    """Linha de opção sintética com os campos que a derivação olha."""
    return replace(
        base(),
        refdate=refdate,
        ticker=ticker,
        tpmerc=tpmerc,
        maturity=maturity,
        strike=strike,
        style=style,
        side=side,
        isin_underlying=isin,
        distribution_id=distribution_id,
        strike_adj_ind=strike_adj_ind,
    )


def spot(
    refdate: date,
    *,
    ticker: str = "TEST3",
    isin: str = "BRTESTACNOR0",
    tpmerc: str = "010",
) -> CotahistRow:
    return replace(
        base(),
        refdate=refdate,
        ticker=ticker,
        tpmerc=tpmerc,
        maturity=None,
        strike=0,
        style=None,
        side=None,
        isin_underlying=isin,
    )


def legs(rows: list[CotahistRow]) -> list[LegRow]:
    return list(build_legs(rows))


def test_build_legs_e_gerador() -> None:
    import inspect

    assert inspect.isgeneratorfunction(build_legs)


def test_uma_linha_por_ticker_e_refdate() -> None:
    rows = fixture_rows()
    result = legs(rows)
    keys = [(row.ticker, row.refdate) for row in result]
    assert len(keys) == len(set(keys))
    referencias = {row.refdate for row in rows if row.tpmerc in ("070", "080")}
    assert {row.refdate for row in result} == referencias


def test_so_linhas_de_opcao_entram() -> None:
    rows = [
        spot(date(2026, 3, 2)),
        option(date(2026, 3, 2), 1000),
        replace(base(), refdate=date(2026, 3, 2), tpmerc="012", maturity=MATURITY),
        replace(base(), refdate=date(2026, 3, 2), tpmerc="020", maturity=None),
        option(date(2026, 3, 2), 1000, ticker="OUTRE1", maturity=date(2026, 1, 16)),
    ]
    assert [(row.ticker, row.strike) for row in legs(rows)] == [
        ("TESTE1", 1000),
        ("OUTRE1", 1000),
    ]


def test_fronteira_por_strike() -> None:
    dias = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 5)]
    rows = [
        option(day, strike) for day, strike in zip(dias, [1204, 1204, 1200, 1194], strict=True)
    ]
    result = legs(rows)
    assert [row.leg_index for row in result] == [0, 0, 1, 2]
    assert [row.strike for row in result] == [1204, 1204, 1200, 1194]
    assert [row.leg_id for row in result] == [
        f"TESTE1|2026-12-18|{index}" for index in (0, 0, 1, 2)
    ]
    # strike é do DIA, não da série (AGENTS.md, armadilha 2)
    assert result[2].strike == 1200 and result[3].strike == 1194
    assert all(row.flags == () for row in result)


def test_fronteira_por_distribution_id() -> None:
    dias = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
    rows = [
        option(dias[0], 4518, distribution_id="168"),
        option(dias[1], 4518, distribution_id="168"),
        option(dias[2], 4518, distribution_id="169"),
    ]
    result = legs(rows)
    assert [row.leg_index for row in result] == [0, 0, 1]
    assert [row.distribution_id for row in result] == ["168", "168", "169"]
    # o corte não veio do strike: é o caso ambíguo que a spec pede para sinalizar
    assert [row.flags for row in result] == [(), (), ("leg_ambiguity",)]


def test_fronteira_por_strike_adj_ind() -> None:
    """Sempre `'0'` em 2026, mas o campo fica na fronteira à prova de arquivo futuro."""
    dias = [date(2026, 3, 2), date(2026, 3, 3)]
    rows = [
        option(dias[0], 1000, strike_adj_ind="0"),
        option(dias[1], 1000, strike_adj_ind="1"),
    ]
    result = legs(rows)
    assert [row.leg_index for row in result] == [0, 1]
    assert result[1].flags == ("leg_ambiguity",)


def test_estilo_por_maioria_com_conflito() -> None:
    dias = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
    rows = [
        option(dias[0], 1000, style="E"),
        option(dias[1], 1000, style="E"),
        option(dias[2], 1000, style="A"),
    ]
    result = legs(rows)
    assert [row.style for row in result] == ["E", "E", "E"]
    assert [row.flags for row in result] == [("style_conflict",)] * 3


def test_estilo_empatado_fica_nulo_com_flag() -> None:
    dias = [date(2026, 3, 2), date(2026, 3, 3)]
    rows = [option(dias[0], 1000, style="E"), option(dias[1], 1000, style="A")]
    result = legs(rows)
    assert [row.style for row in result] == [None, None]
    assert [row.flags for row in result] == [("style_conflict",)] * 2


def test_estilo_desconhecido_em_todo_o_leg() -> None:
    rows = [option(date(2026, 3, 2), 1000, style=None)]
    assert legs(rows)[0].style is None
    assert legs(rows)[0].flags == ("style_unknown",)


def test_lado_divergente_marca_conflito() -> None:
    dias = [date(2026, 3, 2), date(2026, 3, 3)]
    rows = [
        option(dias[0], 1000, side="C"),
        option(dias[1], 1000, side="P", tpmerc="080"),
    ]
    result = legs(rows)
    assert [row.side for row in result] == ["C", "C"]
    assert [row.flags for row in result] == [("side_conflict",)] * 2


def test_strike_zero_e_fronteira_legitima_mas_marcada() -> None:
    """Strike 0 de linha de opção é órfã de grade: corta o leg se mudar, e é sinalizada."""
    dias = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
    rows = [
        option(dias[0], 0, ticker="IBOVE1", isin="BRXIBOVACNOR"),
        option(dias[1], 0, ticker="IBOVE1", isin="BRXIBOVACNOR"),
        option(dias[2], 1000, ticker="IBOVE1", isin="BRXIBOVACNOR"),
    ]
    result = legs(rows)
    assert [row.leg_index for row in result] == [0, 0, 1]
    assert [row.strike for row in result] == [0, 0, 1000]
    assert [row.flags for row in result] == [("strike_zero",)] * 2 + [()]


def test_legs_nao_dependem_da_ordem_de_entrada() -> None:
    dias = [date(2026, 3, 4), date(2026, 3, 2), date(2026, 3, 3)]
    entries = [option(dias[0], 1000), option(dias[1], 1010), option(dias[2], 1010)]
    result = legs(entries)
    assert [(row.refdate, row.strike) for row in result] == [
        (date(2026, 3, 2), 1010),
        (date(2026, 3, 3), 1010),
        (date(2026, 3, 4), 1000),
    ]
    assert [row.leg_index for row in result] == [0, 0, 1]


def test_series_diferentes_nao_se_misturam() -> None:
    dia = date(2026, 3, 2)
    rows = [
        option(dia, 1000, ticker="AAAAC1", maturity=date(2026, 6, 19)),
        option(dia, 2000, ticker="AAAAC1", maturity=date(2026, 12, 18)),
        option(dia, 3000, ticker="AAAAP1", maturity=date(2026, 6, 19), tpmerc="080", side="P"),
    ]
    result = legs(rows)
    assert len(result) == 3
    assert {row.leg_id for row in result} == {
        "AAAAC1|2026-06-19|0",
        "AAAAC1|2026-12-18|0",
        "AAAAP1|2026-06-19|0",
    }
    assert {row.side for row in result} == {"C", "P"}


def test_abellao_action_nao_entra() -> None:
    """Opção sem vencimento não é série (impossível no arquivo real, defendido aqui)."""
    rows = [replace(base(), refdate=date(2026, 3, 2), maturity=None)]
    assert legs(rows) == []


def test_legs_reais_do_abevx131() -> None:
    """12,04 → 12,00 → 11,94: dois ajustes, três legs (com buracos de negócio)."""
    result = [row for row in legs(fixture_rows()) if row.ticker == "ABEVX131"]
    assert [(row.refdate, row.strike, row.leg_index) for row in result] == [
        (date(2026, 6, 15), 1204, 0),
        (date(2026, 6, 23), 1200, 1),
        (date(2026, 9, 18), 1200, 1),
        (date(2026, 9, 22), 1194, 2),
    ]
    assert result[1].leg_id == "ABEVX131|2026-12-18|1"
    assert all(row.style == "E" for row in result)  # token ABEVE
    assert all(row.side == "P" for row in result)  # ABEVX131 é put (mercado 080)


def test_leg_real_com_fronteira_so_de_distribution_id() -> None:
    """SBSPJ451: strike 4518 o tempo todo, distribution_id 168 → 169 em 15/09/2026."""
    result = [row for row in legs(fixture_rows()) if row.ticker == "SBSPJ451"]
    assert [(row.refdate, row.leg_index, row.distribution_id) for row in result] == [
        (date(2026, 7, 15), 0, "168"),
        (date(2026, 7, 24), 0, "168"),
        (date(2026, 9, 15), 1, "169"),
    ]
    assert result[-1].flags == ("leg_ambiguity",)


def test_desdobramento_e_grupamento_reais_cortam_o_leg() -> None:
    rows = fixture_rows()
    sbsp = [row for row in legs(rows) if row.ticker == "SBSPE144"]
    assert [(row.refdate, row.strike, row.leg_index) for row in sbsp] == [
        (date(2026, 4, 28), 14378, 0),
        (date(2026, 4, 29), 2876, 1),
    ]
    simh = [row for row in legs(rows) if row.ticker == "SIMHN603"]
    assert [(row.refdate, row.strike, row.leg_index) for row in simh] == [
        (date(2026, 2, 13), 603, 0),
        (date(2026, 2, 18), 1206, 1),
    ]


def test_derive_e_puro_sem_io() -> None:
    """`derive/` recebe dataclasses e devolve dataclasses: nenhuma porta para arquivo."""
    proibidos = {"pathlib", "io", "zipfile", "sqlite3", "duckdb", "pyarrow", "httpx", "open"}
    for nome in ("legs", "strike_events"):
        fonte = (SRC / "b3o" / "derive" / f"{nome}.py").read_text(encoding="utf-8")
        arvore = ast.parse(fonte)
        importados: set[str] = set()
        for node in ast.walk(arvore):
            if isinstance(node, ast.Import):
                importados.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                importados.add(node.module.split(".")[0])
        assert not (importados & proibidos), (nome, importados & proibidos)
        assert "open(" not in fonte, nome
