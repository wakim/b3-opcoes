"""Painel descritivo do F2 (ADR-024) — PURO: recebe linhas, devolve texto.

Zero I/O, zero DuckDB. A agregação (contagens por motivo, mediana de IV e de
VRP entre os `ok`, fontes de taxa) e a serialização (terminal + Markdown) moram
aqui; quem lê o lake é `QueryService.screen`/`screen_bundle`.

O que este módulo **não** faz (ADR-023/ADR-024): classe de sinal, taxa de
acerto, nota, posição de ordem preditiva ou qualquer rótulo que sugira previsão.
A ordem é alfabética por papel e a leitura é só do que o dado diz na sessão.
`signal_class` não é lido em lugar nenhum.

Regras de serialização: célula sem dado é `—` (nunca `0`); R$ e pontos de vol
com 2 casas; números com vírgula decimal (padrão dos relatórios do projeto) —
o arredondamento acontece **só** aqui, nunca no cálculo.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # evita dependência de runtime de `derive` para `query`
    from b3o.query.service import ScreenRow

OK = "ok"

RV_SHOCK_FLAG = "rv_shock_day"
"""Flag de leitura: um pregão responde por ≥ 50% da variância de 30 (ADR-035)."""
EXCLUDED = "excluded"

REASON_ORDER: tuple[str, ...] = (
    "no_trade",
    "no_options",
    "no_metrics",
    "no_rates",
    "no_forward",
    "sparse_chain",
    "no_expiry",
)
"""Motivos de exclusão na ordem de precedência do contrato §1."""

REASON_TEXT: dict[str, str] = {
    "no_trade": "não negociou no mercado à vista (010/020) na sessão",
    "no_options": "sem linha de opção (070/080) na sessão",
    "no_metrics": "tem opção na sessão, mas sem valor legível em metrics_underlying",
    "no_rates": "sessão sem curva de taxa: IV e forward não calculados",
    "no_forward": "nenhum vencimento com forward utilizável na sessão",
    "sparse_chain": "grade esparsa: strikes insuficientes para o ATM",
    "no_expiry": "sem vencimento utilizável para o smile",
}
"""Texto curto de cada motivo (o relatório nunca mostra só o código)."""


@dataclass(frozen=True, slots=True)
class ScreenSummary:
    """Retrato de uma sessão do painel: contagens, medianas e fontes."""

    session: date
    as_of: date
    model_version: int
    total: int
    ok: int
    excluded: int
    by_reason: dict[str, int]
    iv_median: float | None
    iv_count: int
    vrp_median: float | None
    vrp_count: int
    rate_sources: tuple[str, ...]
    outside_universe: tuple[str, ...]
    """Papéis com métrica na sessão e fora do corte de volume (não entram)."""
    earnings_coverage_start: date | None = None
    """Primeira data do calendário de resultados no lake (aviso quando a sessão é anterior)."""
    rv_shock_count: int = 0
    """Papéis `ok` com a flag `rv_shock_day` (um pregão ≥ 50% da variância de 30)."""
    rv_shock_dates: tuple[tuple[date, int], ...] = ()
    """`(pregão dominante, nº de papéis)` entre os sinalizados, do mais frequente."""


def aggregate(
    rows: Sequence[ScreenRow],
    *,
    session: date,
    model_version: int,
    outside_universe: Sequence[str] = (),
    earnings_coverage_start: date | None = None,
) -> ScreenSummary:
    """Agrega as linhas do painel (puro): contagens por motivo e medianas dos `ok`.

    Mediana só entre os `ok` **com valor** (célula `—` não entra, e o `n` vai
    junto no relatório: `—` com `n=0` é diferente de "mediana zero").
    """
    ok_rows = [row for row in rows if row.status == OK]
    counts = Counter(
        (row.exclusion or "no_metrics") for row in rows if row.status != OK
    )
    by_reason = {
        reason: counts[reason] for reason in REASON_ORDER if reason in counts
    }
    extras = sorted(reason for reason in counts if reason not in by_reason)
    by_reason.update({reason: counts[reason] for reason in extras})
    iv_values = [row.iv_atm_30d for row in ok_rows if row.iv_atm_30d is not None]
    vrp_values = [row.vrp for row in ok_rows if row.vrp is not None]
    shocked = [row for row in ok_rows if RV_SHOCK_FLAG in row.flags]
    return ScreenSummary(
        session=session,
        as_of=rows[0].as_of_session if rows else session,
        model_version=model_version,
        total=len(rows),
        ok=len(ok_rows),
        excluded=len(rows) - len(ok_rows),
        by_reason=by_reason,
        iv_median=statistics.median(iv_values) if iv_values else None,
        iv_count=len(iv_values),
        vrp_median=statistics.median(vrp_values) if vrp_values else None,
        vrp_count=len(vrp_values),
        rate_sources=tuple(
            sorted({row.rate_source for row in ok_rows if row.rate_source})
        ),
        outside_universe=tuple(sorted(set(outside_universe))),
        earnings_coverage_start=earnings_coverage_start,
        rv_shock_count=len(shocked),
        rv_shock_dates=tuple(
            sorted(
                Counter(row.rv_shock_date for row in shocked if row.rv_shock_date).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
    )


# --------------------------------------------------------------------------- #
# Terminal (P-G: uma tela)
# --------------------------------------------------------------------------- #

_TERMINAL_COLUMNS: tuple[tuple[str, int, str], ...] = (
    ("papel", 7, "l"),
    ("situação", 10, "l"),
    ("spot", 8, "r"),
    ("IV30", 7, "r"),
    ("rank", 6, "r"),
    ("pct", 6, "r"),
    ("RV20", 7, "r"),
    ("RV30", 7, "r"),
    ("RV60", 7, "r"),
    ("VRP", 8, "r"),
    ("z", 7, "r"),
    ("RR25", 7, "r"),
    ("BFLY25", 7, "r"),
    ("exp%", 7, "r"),
    ("liq.d", 7, "r"),
    ("séries", 10, "r"),
    ("venc", 5, "r"),
    ("resultado", 17, "l"),
)
"""Colunas do terminal: o essencial de leitura; o relatório tem a tabela completa."""


def render_summary(
    summary: ScreenSummary,
    rows: Sequence[ScreenRow],
    *,
    max_rows: int | None = 20,
) -> list[str]:
    """Linhas do terminal: cabeçalho, truncagem declarada e a tabela.

    `max_rows=None` mostra todas; um número mostra as N primeiras em ordem
    alfabética (nunca "as melhores" — não há nota). O relatório `.md` é sempre
    completo.
    """
    lines = [
        f"screen {summary.session.isoformat()} · as_of {summary.as_of.isoformat()} · "
        f"model_version {summary.model_version} · {summary.total} papel(is) no universo",
        f"ok {summary.ok} · excluídos {summary.excluded}"
        + (f" ({_reason_counts(summary)})" if summary.by_reason else ""),
        f"IV ATM 30d mediana {_pct(summary.iv_median)} (n={summary.iv_count}) · "
        f"VRP mediana {_pts(summary.vrp_median)} pts (n={summary.vrp_count}) · "
        f"taxa {_sources(summary.rate_sources)}",
    ]
    if summary.outside_universe:
        lines.append(
            f"fora do universo com métrica (não entram): {len(summary.outside_universe)} — "
            + ", ".join(summary.outside_universe)
        )
    if (
        summary.earnings_coverage_start is not None
        and summary.session < summary.earnings_coverage_start
    ):
        lines.append(
            f"resultados: calendário da CVM começa em "
            f"{summary.earnings_coverage_start.isoformat()}; nesta sessão o campo sai —"
        )
    shown = list(rows) if max_rows is None else list(rows)[: max(0, max_rows)]
    truncation = (
        "todas as linhas"
        if len(shown) == len(rows)
        else f"{len(shown)} de {len(rows)} linhas (--max-rows 0 = todas; completa no relatório)"
    )
    lines.append(f"tabela: {truncation}, ordem alfabética")
    lines.append("")
    lines.extend(_terminal_table(shown))
    return lines


def _terminal_table(rows: Sequence[ScreenRow]) -> list[str]:
    """Tabela alinhada do terminal (cabeçalho + linhas, sem Markdown)."""
    header = "  ".join(
        _cell(name, width, align) for name, width, align in _TERMINAL_COLUMNS
    )
    lines = [header.rstrip(), "-" * len(header.rstrip())]
    for row in rows:
        cells = _terminal_cells(row)
        line = "  ".join(
            _cell(value, width, align)
            for value, (_name, width, align) in zip(cells, _TERMINAL_COLUMNS, strict=True)
        )
        lines.append(line.rstrip())
    return lines


def _terminal_cells(row: ScreenRow) -> list[str]:
    """Células do terminal na ordem de `_TERMINAL_COLUMNS`."""
    return [
        row.underlying,
        OK if row.status == OK else (row.exclusion or "excluído"),
        _num(row.spot_brl),
        _num(row.iv_atm_30d),
        _num(row.iv_rank_252),
        _num(row.iv_pct_252, 1),
        _num(row.rv20),
        _num(row.rv30),
        _num(row.rv60),
        _num(row.vrp),
        _num(row.vrp_z_252),
        _num(row.rr25),
        _num(row.bfly25),
        _scaled(row.exp_move_1s_30d, 100.0, 2),
        _num(row.days_to_liquidate),
        f"{row.n_liquid_series}/{row.n_series}" if row.status == OK else "—",
        _num(row.n_expiries, 0) if row.status == OK else "—",
        _earnings(row, short=True),
    ]


def _cell(value: str, width: int, align: str) -> str:
    """Preenche uma célula; número maior que a coluna cresce (não trunca dado)."""
    return f"{value:<{width}}" if align == "l" else f"{value:>{width}}"


# --------------------------------------------------------------------------- #
# Markdown (P-T: relatório do dia, completo e idempotente)
# --------------------------------------------------------------------------- #

_MD_COLUMNS = (
    "papel",
    "situação",
    "spot (R$)",
    "IV ATM 30d (%)",
    "rank 252",
    "percentil 252",
    "RV20 (%)",
    "RV30 (%)",
    "RV60 (%)",
    "VRP (pts)",
    "z 252",
    "RR25 (pts)",
    "BFLY25 (pts)",
    "slope 30–90 (pts)",
    "exp. 1σ 30d (%)",
    "div. impl. 30d (%)",
    "forward 30d (R$)",
    "liquidação (dias)",
    "séries líq/tot",
    "vencimentos",
    "próximo resultado",
)
"""Colunas da tabela do relatório (todas as linhas, sempre)."""

_MD_ALIGN = (
    "l",
    "l",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "r",
    "l",
)


def render_markdown(summary: ScreenSummary, rows: Sequence[ScreenRow]) -> str:
    """Relatório completo do dia (contrato onda-h §3), sem truncagem.

    Idempotente: nada de relógio, `uuid` ou ordem de dicionário — duas execuções
    no mesmo lake produzem o mesmo byte.
    """
    parts: list[str] = [
        f"# Painel descritivo — sessão {summary.session.isoformat()}\n\n",
        f"- **Sessão:** {summary.session.isoformat()} · "
        f"**as_of:** {summary.as_of.isoformat()} · "
        f"**model_version:** {summary.model_version}\n",
        f"- **Universo:** {summary.total} papel(is) com volume financeiro médio 20d "
        "≥ R$ 5 M (mercado `010`, janela das 20 sessões encerrada na sessão)\n",
        f"- **Com métrica (`ok`):** {summary.ok}\n",
        f"- **Excluídos:** {summary.excluded}"
        + (f" ({_reason_counts(summary)})" if summary.by_reason else "")
        + "\n",
        f"- **Mediana entre os `ok`:** IV ATM 30d {_pct(summary.iv_median)} "
        f"(n={summary.iv_count}) · VRP {_pts(summary.vrp_median)} pts "
        f"(n={summary.vrp_count})\n",
        f"- **Fontes de taxa:** {_sources(summary.rate_sources)}\n",
    ]
    if summary.rv_shock_count:
        dates = ", ".join(
            f"{day.isoformat()} ({count})" for day, count in summary.rv_shock_dates
        )
        parts.append(
            f"- **RV dominada por um pregão:** {summary.rv_shock_count} papel(is) `ok` "
            "têm ≥ 50% da variância de 30 pregões vinda de um só dia — RV, VRP e z "
            f"desses papéis refletem esse dia (flag `rv_shock_day`). Pregões: {dates}\n"
        )
    if summary.outside_universe:
        parts.append(
            f"- **Com métrica fora do universo (não entram neste recorte):** "
            f"{len(summary.outside_universe)} — "
            + ", ".join(summary.outside_universe)
            + "\n"
        )
    if (
        summary.earnings_coverage_start is not None
        and summary.session < summary.earnings_coverage_start
    ):
        parts.append(
            f"- **Resultados:** o calendário da CVM no lake começa em "
            f"{summary.earnings_coverage_start.isoformat()}; nesta sessão o campo "
            "**não tem cobertura** e sai `—` (não é ausência de resultado).\n"
        )
    parts.extend(
        [
            "\nLeitura descritiva do que o dado diz na sessão: sem nota, sem classe de "
            "sinal e sem qualquer alegação preditiva. A tabela está em ordem alfabética "
            "por papel (a ordem não é uma posição de preferência).\n\n",
            "`ok` = linha em `metrics_underlying` na sessão. As flags do papel "
            "(`no_forward`, `sparse_chain`, `iv_expired`, …) explicam as células `—` e "
            "**não** tiram o papel do recorte. `—` é ausência de dado, nunca zero. "
            "O vínculo opção→papel é resolvido pelo ISIN (`isin_underlying`), nunca "
            "pelo prefixo do ticker de opção.\n\n",
        ]
    )

    parts.append("\n## Tabela\n\n")
    parts.append(
        "| " + " | ".join(_MD_COLUMNS) + " |\n"
        "|" + "|".join(_md_rule(align) for align in _MD_ALIGN) + "|\n"
    )
    for row in rows:
        parts.append(
            "| " + " | ".join(_md_cells(row)) + " |\n"
        )

    parts.append("\n## Fichas (`ok`)\n\n")
    for row in rows:
        if row.status != OK:
            continue
        parts.extend(_ficha(row))

    parts.append("\n## Excluídos\n\n")
    for reason, count in summary.by_reason.items():
        parts.append(f"### {reason} ({count})\n\n")
        for row in rows:
            if row.exclusion == reason:
                parts.append(
                    f"- {row.underlying} — {REASON_TEXT.get(reason, reason)}\n"
                )
        parts.append("\n")
    return "".join(parts)


def _md_rule(align: str) -> str:
    """Linha de alinhamento da tabela Markdown."""
    return "---:" if align == "r" else "---"


def _md_cells(row: ScreenRow) -> list[str]:
    """Células da tabela Markdown na ordem de `_MD_COLUMNS`."""
    values = [
        row.underlying,
        OK if row.status == OK else (row.exclusion or "excluído"),
        _num(row.spot_brl),
        _num(row.iv_atm_30d),
        _num(row.iv_rank_252),
        _num(row.iv_pct_252, 1),
        _num(row.rv20),
        _num(row.rv30),
        _num(row.rv60),
        _num(row.vrp),
        _num(row.vrp_z_252),
        _num(row.rr25),
        _num(row.bfly25),
        _num(row.ts_slope_30_90),
        _scaled(row.exp_move_1s_30d, 100.0, 2),
        _num(row.implied_div_yield_30d),
        _num(row.forward_30d),
        _num(row.days_to_liquidate),
        f"{row.n_liquid_series}/{row.n_series}" if row.status == OK else "—",
        _num(row.n_expiries, 0) if row.status == OK else "—",
        _earnings(row),
    ]
    return [_escape(value) for value in values]


def _ficha(row: ScreenRow) -> list[str]:
    """Ficha de um papel `ok` (bullets do contrato §3, item 3)."""
    return [
        f"### {row.underlying}\n\n",
        f"- **spot:** {_brl(row.spot_brl)}\n",
        f"- **IV ATM 30d:** {_pct(row.iv_atm_30d)}\n",
        f"- **IV rank 252:** {_num(row.iv_rank_252)} · "
        f"**percentil 252:** {_num(row.iv_pct_252, 1)}\n",
        f"- **RV 20/30/60:** {_pct(row.rv20)} / {_pct(row.rv30)} / {_pct(row.rv60)}\n",
        f"- **VRP:** {_pts_unit(row.vrp)} · **z 252:** {_num(row.vrp_z_252)}\n",
        f"- **RR25:** {_pts_unit(row.rr25)} · **BFLY25:** {_pts_unit(row.bfly25)} · "
        f"**slope 30–90:** {_pts_unit(row.ts_slope_30_90)}\n",
        f"- **exp. move 1σ 30d:** {_exp_move(row.exp_move_1s_30d)}\n",
        f"- **dividendo implícito 30d:** {_pct(row.implied_div_yield_30d)}\n",
        f"- **forward 30d:** {_brl(row.forward_30d)}\n",
        f"- **liquidez:** {row.n_liquid_series}/{row.n_series} séries líquidas · "
        f"{row.n_expiries} vencimento(s) · days_to_liquidate: "
        f"{_num(row.days_to_liquidate)}\n",
        f"- **próximo resultado:** {_earnings(row)} · antes do vencimento: "
        f"{'sim' if row.earnings_before_expiry else 'não'}\n",
        f"- **fonte da taxa:** {row.rate_source or '—'}\n",
        f"- **pregão dominante na RV 30:** {_shock(row)}\n",
        f"- **flags:** {', '.join(row.flags) if row.flags else '—'}\n",
        "\n",
    ]


# --------------------------------------------------------------------------- #
# Formatação (só a serialização arredonda)
# --------------------------------------------------------------------------- #


def _shock(row: ScreenRow) -> str:
    """`AAAA-MM-DD (NN% da variância)` do pregão dominante, ou `—`."""
    if row.rv_shock_date is None or row.rv_shock_share_pct is None:
        return "—"
    # piso, não arredondamento: 49,97% não pode aparecer como "50%" sem a flag
    share = str(int(row.rv_shock_share_pct))
    return f"{row.rv_shock_date.isoformat()} ({share}% da variância)"


def _number(value: float, digits: int) -> str:
    """Número com milhar por espaço e vírgula decimal (`-1 234,56`)."""
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def _num(value: float | None, digits: int = 2) -> str:
    """Número formatado ou `—` para ausência (nunca zero silencioso)."""
    return "—" if value is None else _number(float(value), digits)


def _scaled(value: float | None, factor: float, digits: int) -> str:
    """Valor multiplicado na serialização (fração → %), sem arredondar o dado."""
    return "—" if value is None else _number(float(value) * factor, digits)


def _pct(value: float | None, digits: int = 2) -> str:
    """Percentual já em pontos (`31,43%`), com `—` quando não há dado."""
    return "—" if value is None else _number(float(value), digits) + "%"


def _pts(value: float | None) -> str:
    """Pontos de vol com 2 casas (o sufixo fica no rótulo, não na célula)."""
    return _num(value)


def _pts_unit(value: float | None) -> str:
    """Pontos de vol com o sufixo, para a ficha (`—` quando não há dado)."""
    return "—" if value is None else _num(value) + " pts"


def _brl(value: float | None) -> str:
    """Reais com 2 casas e o prefixo `R$` (ficha e colunas de preço)."""
    return "—" if value is None else "R$ " + _number(float(value), 2)


def _exp_move(value: float | None) -> str:
    """Exp. move em % do spot e a fração crua (a unidade do contrato)."""
    if value is None:
        return "—"
    return f"{_number(float(value) * 100.0, 2)}% ({_number(float(value), 4)} do spot)"


def _earnings(row: ScreenRow, *, short: bool = False) -> str:
    """Próximo resultado: data + `confirmado`/`previsto` (`actual`/`estimated`)."""
    if row.earnings_next_date is None:
        return "—"
    if short:
        suffix = "conf" if row.earnings_next_kind == "actual" else "prev"
    else:
        suffix = "confirmado" if row.earnings_next_kind == "actual" else "previsto"
    return f"{row.earnings_next_date.isoformat()} {suffix}"


def _reason_counts(summary: ScreenSummary) -> str:
    """`no_options 124 · no_trade 2`, na ordem de `REASON_ORDER`."""
    return " · ".join(f"{reason} {count}" for reason, count in summary.by_reason.items())


def _sources(sources: Sequence[str]) -> str:
    """Fontes de taxa da sessão (`—` quando nenhuma linha trouxe fonte)."""
    return ", ".join(sources) if sources else "—"


def _escape(value: str) -> str:
    """Escapa o pipe que quebraria a célula da tabela Markdown."""
    return value.replace("|", "\\|")


__all__ = [
    "EXCLUDED",
    "OK",
    "REASON_ORDER",
    "REASON_TEXT",
    "ScreenSummary",
    "aggregate",
    "render_markdown",
    "render_summary",
]
