"""Construtores de payload da API/MCP (F3) — a **única** ponte entre HTTP e MCP.

Cada função recebe a `QueryService` do processo e devolve o dicionário do
envelope (`serialize.envelope`). HTTP e MCP chamam as mesmas funções — é isso
que garante o mesmo número nas duas bordas (critério do contrato §7).

Nada aqui calcula métrica: o `screen` reusa a agregação pura do F2
(`derive.screen.aggregate`) e as leituras do F3 vêm da `QueryService`. Nenhum
campo preditivo é exposto (`signal_class` não é lido).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from b3o.api.glossary import GlossaryEntry, glossary_path, parse_glossary
from b3o.api.serialize import (
    DEFAULT_MAX_ROWS,
    MAX_ROWS,
    SECTIONS,
    clamp_max_rows,
    envelope,
    jsonable,
    parse_date,
    parse_sections,
    request_error,
)
from b3o.derive.screen import aggregate
from b3o.ingest.errors import SourceError, SourceFailure
from b3o.query.service import QueryService


def health_payload(service: QueryService) -> dict[str, Any]:
    """`GET /api/health` / tool `health`: o `Health` do F0.12 + envelope."""
    data = service.health().to_dict()
    return envelope(
        data,
        as_of_session=data.get("last_session"),
        model_version=data.get("model_version"),
    )


def screen_payload(
    service: QueryService,
    date: Any = None,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Painel descritivo da sessão: resumo + as N primeiras linhas (alfabéticas)."""
    limit = clamp_max_rows(max_rows)
    bundle = service.screen_bundle(_as_date(date))
    summary = aggregate(
        bundle.rows,
        session=bundle.session,
        model_version=bundle.model_version,
        outside_universe=bundle.outside_universe,
        earnings_coverage_start=bundle.earnings_coverage_start,
    )
    rows = [jsonable(row) for row in bundle.rows[:limit]]
    data = {
        "session": bundle.session.isoformat(),
        "summary": {
            "total": summary.total,
            "ok": summary.ok,
            "excluded": summary.excluded,
            "by_reason": dict(summary.by_reason),
            "iv_median": summary.iv_median,
            "iv_count": summary.iv_count,
            "vrp_median": summary.vrp_median,
            "vrp_count": summary.vrp_count,
            "rate_sources": list(summary.rate_sources),
            "outside_universe": list(summary.outside_universe),
            "earnings_coverage_start": (
                summary.earnings_coverage_start.isoformat()
                if summary.earnings_coverage_start
                else None
            ),
        },
        "rows": rows,
    }
    return envelope(
        data,
        as_of_session=bundle.session,
        model_version=bundle.model_version,
        list_key="rows",
        total=len(bundle.rows),
    )


def underlying_payload(
    service: QueryService,
    ticker: str,
    date: Any = None,
    sections: Any = None,
    expiry: Any = None,
    days: int = 60,
) -> dict[str, Any]:
    """Leitura de um papel (`underlying`), com as seções pedidas capadas em 60."""
    wanted = parse_sections(sections) if isinstance(sections, str | None) else sections
    wanted = tuple(wanted or SECTIONS)
    detail = service.underlying(
        ticker,
        date=_as_date(date),
        sections=wanted,
        expiry=_as_date(expiry, "expiry"),
        days=days,
    )
    term = [jsonable(row) for row in detail.term[:MAX_ROWS]]
    smile = [jsonable(row) for row in detail.smile[:MAX_ROWS]]
    history = [jsonable(row) for row in detail.history[:MAX_ROWS]]
    data = {
        "ticker": detail.ticker,
        "in_universe": detail.in_universe,
        "summary": jsonable(detail.summary),
        "term": term,
        "smile": smile,
        "smile_maturity": (
            detail.smile_maturity.isoformat() if detail.smile_maturity else None
        ),
        "history": history,
        "notes": list(detail.notes),
        "counts": {
            "term": len(detail.term),
            "smile": len(detail.smile),
            "history": len(detail.history),
        },
    }
    primary = (
        "smile" if "smile" in wanted else "history" if "history" in wanted else "term"
    )
    counts = {
        key: value for key, value in data["counts"].items() if key != primary
    }
    return envelope(
        data,
        as_of_session=detail.as_of_session,
        model_version=detail.model_version,
        list_key=primary,
        total=data["counts"][primary],
        counts=counts,
    )


def chain_payload(
    service: QueryService,
    ticker: str,
    date: Any = None,
    expiry: Any = None,
    delta_range: tuple[float, float] | None = (0.25, 0.75),
    liquidity: str = "strict",
    max_rows: int | None = DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Cadeia do papel na sessão (uma linha por série), respeitando `max_rows`."""
    limit = clamp_max_rows(max_rows)
    detail = service.chain(
        ticker,
        expiry=_as_date(expiry, "expiry"),
        date=_as_date(date),
        delta_range=delta_range,
        liquidity=liquidity,
        max_rows=limit,
    )
    data = {
        "ticker": detail.ticker,
        "rows": [jsonable(row) for row in detail.rows],
        "notes": list(detail.notes),
    }
    return envelope(
        data,
        as_of_session=detail.as_of_session,
        model_version=detail.model_version,
        list_key="rows",
        total=detail.total,
    )


def series_payload(
    service: QueryService,
    ticker: str,
    maturity: Any,
    days: int = 60,
) -> dict[str, Any]:
    """Identidade + última linha + histórico de uma série (capado em 60 linhas)."""
    parsed = maturity if isinstance(maturity, date) else _as_date(maturity, "maturity")
    if parsed is None:
        raise request_error(
            "maturity ausente",
            "informe o vencimento da série (AAAA-MM-DD), como em `curve`/`events`",
        )
    detail = service.series(ticker, parsed, days=days)
    history = [jsonable(point) for point in detail.history[:MAX_ROWS]]
    data = {
        "ticker": detail.ticker,
        "maturity": detail.maturity.isoformat(),
        "side": detail.side,
        "style": detail.style,
        "strike_brl": detail.strike_brl,
        "leg_id": detail.leg_id,
        "t": detail.t,
        "is_liquid": detail.is_liquid,
        "flags": list(detail.flags),
        "gamma": detail.gamma,
        "theta_brl": detail.theta_brl,
        "vega_brl": detail.vega_brl,
        "last": jsonable(detail.last),
        "history": history,
        "counts": {"history": len(detail.history)},
    }
    return envelope(
        data,
        as_of_session=detail.as_of_session,
        model_version=detail.model_version,
        list_key="history",
        total=len(detail.history),
    )


def events_payload(service: QueryService, ticker: str) -> dict[str, Any]:
    """Vencimentos, eventos de strike e resultados do papel (listas capadas)."""
    detail = service.events(ticker)
    maturities = [day.isoformat() for day in detail.maturities[:MAX_ROWS]]
    strike_events = [jsonable(row) for row in detail.strike_events[:MAX_ROWS]]
    earnings = [jsonable(row) for row in detail.earnings[:MAX_ROWS]]
    data = {
        "ticker": detail.ticker,
        "maturities": maturities,
        "strike_events": strike_events,
        "earnings": earnings,
        "next_earnings": jsonable(detail.next_earnings),
        "notes": list(detail.notes),
        "counts": {
            "maturities": len(detail.maturities),
            "strike_events": len(detail.strike_events),
            "earnings": len(detail.earnings),
        },
    }
    return envelope(
        data,
        as_of_session=detail.as_of_session,
        model_version=detail.model_version,
        list_key="earnings",
        total=len(detail.earnings),
        counts={
            "maturities": len(detail.maturities),
            "strike_events": len(detail.strike_events),
        },
    )


STRUCTURE_KEYS = frozenset({"underlying", "date", "legs", "capital", "scenarios"})
"""Campos aceitos no corpo do `POST /api/structure` (contrato onda-j §2)."""


def structure_payload(
    service: QueryService, body: Any
) -> dict[str, Any]:
    """Avalia a estrutura **dada** (`POST /api/structure` e tool `structure_eval`).

    É a **única** ponte: o CLI `b3o struct` também chama esta função, então MCP,
    HTTP e CLI devolvem o mesmo JSON por construção. Não sugere, não compara e
    não ordena estruturas — só avalia o que o corpo especifica (ADR-024/028).

    Forma do corpo (contrato §2): `{underlying?, date?, legs, capital?, scenarios?}`;
    campo desconhecido é `bad_request` (não se ignora parâmetro em silêncio).
    """
    if not isinstance(body, Mapping):
        raise request_error(
            "corpo da estrutura inválido",
            'envie um objeto JSON {"underlying", "date", "legs", "capital", "scenarios"}',
        )
    unknown = sorted(str(key) for key in body if str(key) not in STRUCTURE_KEYS)
    if unknown:
        raise request_error(
            f"campos desconhecidos no corpo: {unknown}",
            f"os campos aceitos são {sorted(STRUCTURE_KEYS)}",
        )
    underlying = body.get("underlying")
    if underlying is not None and not isinstance(underlying, str):
        raise request_error(
            f"underlying inválido ({underlying!r})",
            "underlying é o ticker do papel (ex.: PETR4) ou omitido",
        )
    raw_date = body.get("date")
    if raw_date is not None and not isinstance(raw_date, str | date):
        raise request_error(
            f"date inválida ({raw_date!r})",
            "use uma sessão do lake em ISO AAAA-MM-DD (o `health` diz a última)",
        )
    scenarios = body.get("scenarios", True)
    if not isinstance(scenarios, bool):
        raise request_error(
            f"scenarios inválido ({scenarios!r})",
            "scenarios é booleano (true/false); omitido vale true",
        )
    detail = service.structure_eval(
        body.get("legs"),
        underlying=underlying,
        date=_as_date(raw_date),
        capital=body.get("capital"),
        scenarios=scenarios,
    )
    result = detail.result
    data = {
        "underlying": detail.underlying,
        "structure_version": result.structure_version,
        "spot_brl": result.spot_brl,
        "forward_brl": result.forward_brl,
        "df": result.df,
        "t": result.t,
        "maturity": result.maturity,
        "legs": [jsonable(leg) for leg in result.legs],
        "net_premium_brl": result.net_premium_brl,
        "net_premium_low_brl": result.net_premium_low_brl,
        "net_premium_high_brl": result.net_premium_high_brl,
        "breakeven_brl": list(result.breakeven_brl),
        "max_loss_brl": result.max_loss_brl,
        "max_gain_brl": result.max_gain_brl,
        "capital_committed_brl": result.capital_committed_brl,
        "capital_input_brl": result.capital_input_brl,
        "capital_over": result.capital_over,
        "delta": result.delta,
        "gamma": result.gamma,
        "theta_brl": result.theta_brl,
        "vega_brl": result.vega_brl,
        "payoff": [jsonable(point) for point in result.payoff],
        "scenarios": (
            None
            if result.scenarios is None
            else [jsonable(row) for row in result.scenarios]
        ),
        "flags": list(result.flags),
        "notes": list(detail.notes),
    }
    return envelope(
        data,
        as_of_session=detail.as_of_session,
        model_version=detail.model_version,
        list_key="payoff",
        total=len(result.payoff),
        counts={
            "legs": len(result.legs),
            "scenarios": 0 if result.scenarios is None else len(result.scenarios),
        },
    )


# --------------------------------------------------------------------------- #
# F5.2 — busca de papel e glossário (leitura, sem escrita)
# --------------------------------------------------------------------------- #


def tickers_payload(
    service: QueryService, q: str = "", max_rows: int | None = None
) -> dict[str, Any]:
    """`GET /api/tickers` — busca de papel na última sessão, capada em 60."""
    result = service.search_tickers(q, max_rows=clamp_max_rows(max_rows))
    data = {
        "q": str(q or ""),
        "rows": [jsonable(row) for row in result.rows],
    }
    return envelope(
        data,
        as_of_session=result.as_of_session,
        model_version=result.model_version,
        list_key="rows",
        total=result.total,
    )


def _load_glossary() -> list[GlossaryEntry]:
    """Leitura do arquivo na borda; ausência é `glossary_missing` explícito."""
    path = glossary_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SourceFailure(
            SourceError(
                code="glossary_missing",
                message=f"glossário não encontrado em {path}",
                hint=(
                    "confira B3_GLOSSARY_PATH (default /app/docs/glossario.md) e se "
                    "o Dockerfile copiou docs/glossario.md para a imagem"
                ),
            )
        ) from exc
    return parse_glossary(text)


def glossary_payload(service: QueryService) -> dict[str, Any]:
    """`GET /api/glossary` — índice `{key, title, level}` de todos os verbetes."""
    entries = _load_glossary()
    rows = [
        {"key": entry.key, "title": entry.title, "level": entry.level}
        for entry in entries[:MAX_ROWS]
    ]
    return envelope(
        {"entries": rows},
        as_of_session=service._session(None),
        model_version=service._model_version_for(),
        list_key="entries",
        total=len(entries),
    )


def glossary_entry_payload(service: QueryService, key: str) -> dict[str, Any]:
    """`GET /api/glossary/{key}` — o verbete completo; desconhecido é 404."""
    entries = _load_glossary()
    entry = next((item for item in entries if item.key == str(key).strip()), None)
    if entry is None:
        raise SourceFailure(
            SourceError(
                code="not_found",
                message=f"verbete {key!r} não existe no glossário",
                hint=(
                    "veja as chaves em GET /api/glossary (slug do título: minúsculas, "
                    "sem acento, pontuação vira '-')"
                ),
            )
        )
    return envelope(
        {
            "key": entry.key,
            "title": entry.title,
            "level": entry.level,
            "body_md": entry.body_md,
        },
        as_of_session=service._session(None),
        model_version=service._model_version_for(),
    )


def _as_date(value: Any, name: str = "date") -> date | None:
    """Aceita `date`, ISO ou None (as rotas e as tools passam strings)."""
    if value is None or isinstance(value, date):
        return value
    return parse_date(str(value), name)
