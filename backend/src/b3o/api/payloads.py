"""Construtores de payload da API/MCP (F3) — a **única** ponte entre HTTP e MCP.

Cada função recebe a `QueryService` do processo e devolve o dicionário do
envelope (`serialize.envelope`). HTTP e MCP chamam as mesmas funções — é isso
que garante o mesmo número nas duas bordas (critério do contrato §7).

Nada aqui calcula métrica: o `screen` reusa a agregação pura do F2
(`derive.screen.aggregate`) e as leituras do F3 vêm da `QueryService`. Nenhum
campo preditivo é exposto (`signal_class` não é lido).
"""

from __future__ import annotations

from datetime import date
from typing import Any

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


def _as_date(value: Any, name: str = "date") -> date | None:
    """Aceita `date`, ISO ou None (as rotas e as tools passam strings)."""
    if value is None or isinstance(value, date):
        return value
    return parse_date(str(value), name)
