"""FastAPI do b3o (F3): HTTP em `/api/...` e MCP Streamable HTTP em `/mcp`.

Uma única instância de `QueryService` nasce no lifespan e é usada pelas duas
bordas; as rotas e as tools chamam os **mesmos** construtores de payload
(`b3o.api.payloads`), então o número é o mesmo por construção.

Rotas do F3 (contrato onda-i §5): `health`, `screen`, `underlying`, `chain`,
`series`, `events`. Escritas, journal, positions, structure, glossary e
briefing ficam para F4/F5; `signal_backtest` não é exposto (ADR-024/025).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings

from b3o.api import payloads
from b3o.api.mcp_server import mcp
from b3o.api.serialize import (
    DEFAULT_MAX_ROWS,
    error_payload,
    error_status,
    parse_date,
    parse_delta_range,
    parse_liquidity,
    parse_sections,
    request_error,
)
from b3o.api.state import build_service, set_service
from b3o.ingest.errors import SourceFailure

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Monta o app: lifespan único, rotas HTTP e o MCP em `/mcp`."""
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                "127.0.0.1",
                "127.0.0.1:*",
                "localhost",
                "localhost:*",
                "host.containers.internal",
                "host.containers.internal:*",
            ],
        ),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        set_service(build_service())
        try:
            # O lifespan do app MCP não roda sozinho quando montado: sem isto o
            # session manager do Streamable HTTP não inicia (armadilha do AGENTS).
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        finally:
            set_service(None)

    app = FastAPI(
        title="b3o — opções B3 (EOD descritivo)",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.exception_handler(SourceFailure)
    async def _source_failure(_request: Request, exc: SourceFailure) -> JSONResponse:
        return JSONResponse(status_code=error_status(exc), content=error_payload(exc))

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("api: falha inesperada")
        return JSONResponse(status_code=500, content=error_payload(exc))

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        """Saúde do lake (P-C): última sessão, idade, fontes e versão do cálculo."""
        return payloads.health_payload(_service())

    @app.get("/api/screen")
    def api_screen(date: str | None = None, max_rows: int = DEFAULT_MAX_ROWS) -> dict[str, Any]:
        """Painel descritivo da sessão (resumo + N primeiras linhas alfabéticas)."""
        return payloads.screen_payload(
            _service(), date=_date(date), max_rows=max_rows
        )

    @app.get("/api/t/{ticker}")
    def api_underlying(
        ticker: str,
        date: str | None = None,
        sections: str | None = None,
        expiry: str | None = None,
        exp: str | None = None,
        days: int = 60,
    ) -> dict[str, Any]:
        """Leitura de um papel: resumo, estrutura a termo, smile e histórico."""
        return payloads.underlying_payload(
            _service(),
            ticker,
            date=_date(date),
            sections=parse_sections(sections),
            expiry=_date(expiry or exp, "expiry"),
            days=days,
        )

    @app.get("/api/t/{ticker}/chain")
    def api_chain(
        ticker: str,
        date: str | None = None,
        expiry: str | None = None,
        delta_min: float | None = None,
        delta_max: float | None = None,
        liquidity: str = "strict",
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> dict[str, Any]:
        """Cadeia do papel na sessão, uma linha por série."""
        return payloads.chain_payload(
            _service(),
            ticker,
            date=_date(date),
            expiry=_date(expiry, "expiry"),
            delta_range=parse_delta_range(delta_min, delta_max),
            liquidity=parse_liquidity(liquidity),
            max_rows=max_rows,
        )

    @app.get("/api/t/{ticker}/events")
    def api_events(ticker: str) -> dict[str, Any]:
        """Vencimentos, eventos de ajuste de strike e calendário de resultados."""
        return payloads.events_payload(_service(), ticker)

    @app.get("/api/s/{ticker}/{maturity}")
    def api_series(ticker: str, maturity: str, days: int = 60) -> dict[str, Any]:
        """Identidade e histórico de uma série (ticker + vencimento)."""
        parsed = parse_date(maturity, "maturity")
        if parsed is None:
            raise request_error(
                "maturity ausente",
                "use /api/s/<ticker>/<AAAA-MM-DD> com o vencimento da série",
            )
        return payloads.series_payload(_service(), ticker, parsed, days=days)

    app.mount("/", mcp_app)
    return app


def _service():
    """Import tardio do holder (evita ciclo na importação do módulo)."""
    from b3o.api.state import get_service

    return get_service()


def _date(value: str | None, name: str = "date") -> date | None:
    return parse_date(value, name)


app = create_app()

__all__ = ["app", "create_app"]
