"""FastAPI do b3o (F3/F5): HTTP em `/api/...`, MCP em `/mcp` e a SPA em `/`.

Uma única instância de `QueryService` nasce no lifespan e é usada pelas bordas;
as rotas e as tools chamam os **mesmos** construtores de payload
(`b3o.api.payloads`), então o número é o mesmo por construção.

Montagem (armadilha do AGENTS, confirmada no F5): o app MCP é um ASGI montado em
`/` com `streamable_http_path="/mcp"`. **Não** montar `StaticFiles` em `/` — ele
engole `/mcp` (ou é engolido). O que funciona é um **dispatcher ASGI**: `/mcp*`
vai para o `mcp_app`, todo o resto para o `dist` do Vite com fallback de SPA.
As rotas `/api` ficam registradas **antes** do mount, como sempre.

Cache: `index.html` `no-store`, `/assets/*` `immutable`, JSON de `/api` `no-cache`.
`frontend/dist` ausente é `503 frontend_missing`, nunca página em branco.
Escritas de posição (`POST /api/positions*`) exigem `X-App-Token` (F6); cálculo de
estrutura continua sem gravação.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from mcp.server.transport_security import TransportSecuritySettings
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from b3o.api import payloads
from b3o.api.auth import require_write_token, write_token_required
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
from b3o.ingest.errors import SourceError, SourceFailure

log = logging.getLogger(__name__)

FRONTEND_DIR_ENV = "B3_FRONTEND_DIR"
"""Caminho do `dist` (default `/app/frontend/dist` na imagem). Fora do config.yaml."""


def _mcp_allowed_hosts() -> list[str]:
    """Hosts locais + os hosts do tailnet informados em `B3_MCP_ALLOWED_HOSTS`.

    A lista é separada por vírgula; `host` e `host:*` podem ser informados para
    cobrir o Host com e sem porta. Nenhum hostname de deploy fica no código.
    """
    hosts = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "host.containers.internal",
        "host.containers.internal:*",
    ]
    configured = os.environ.get("B3_MCP_ALLOWED_HOSTS", "")
    hosts.extend(host.strip() for host in configured.split(",") if host.strip())
    return list(dict.fromkeys(hosts))


def _api_error(status_code: int, content: dict[str, Any]) -> JSONResponse:
    """Erro HTTP no envelope, com o mesmo `no-cache` das rotas `/api`."""
    return JSONResponse(
        status_code=status_code, content=content, headers={"Cache-Control": "no-cache"}
    )


_API_HINT = (
    "rotas: /api/health, /api/screen, /api/tickers, /api/glossary, "
    "/api/t/{ticker}, /api/t/{ticker}/chain, /api/t/{ticker}/events, /api/liquid, "
    "/api/positions, /api/positions/{id}, /api/journal, /api/journal/calibration, "
    "/api/journal/{id}, /api/s/{ticker}/{maturity} e POST /api/structure"
)


class _NoCacheRoute(APIRoute):
    """Toda resposta `/api` sai com `Cache-Control: no-cache`.

    Usa a classe de rota em vez de um `@app.middleware("http")`: middleware
    baseado em `BaseHTTPMiddleware` quebra o streaming do MCP montado (os smokes
    F3/F4 têm de continuar 53/53 e 93/93).
    """

    def get_route_handler(self):  # type: ignore[no-untyped-def]
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            response.headers["Cache-Control"] = "no-cache"
            return response

        return handler


def _frontend_missing() -> JSONResponse:
    """Falha explícita e acionável quando o build do frontend não existe."""
    failure = SourceError(
        code="frontend_missing",
        message=(
            f"frontend/dist não encontrado em {_frontend_dir()} "
            "(ou não contém index.html)"
        ),
        hint=(
            "rode `npm run build` em frontend/ — no caminho oficial: "
            "`podman compose --profile build run --rm web`"
        ),
    )
    return JSONResponse(status_code=503, content=error_payload(failure))


def _frontend_dir() -> Path:
    return Path(os.environ.get(FRONTEND_DIR_ENV, "/app/frontend/dist"))


class _SpaDispatcher:
    """ASGI montado em `/`: `/mcp` → MCP, resto → SPA com fallback.

    Não é `StaticFiles`: o mount do MCP em `/` e o estático não podem disputar a
    mesma raiz. A decisão é por caminho, antes de qualquer leitura de arquivo.

    `as_of_session` é preenchido no lifespan e injetado no `index.html` no campo
    `name="b3o-session"`, para que um `GET /` mostre o carimbo mesmo sem JS. A tela
    continua lendo `/api/health` para o carimbo visível (P-C: vem do lake).
    """

    def __init__(self, mcp_app: Any, dist_dir: Path) -> None:
        self.mcp_app = mcp_app
        self.dist_dir = dist_dir
        self.as_of_session: str | None = None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.mcp_app(scope, receive, send)
            return
        path = str(scope.get("path", "/"))
        if path == "/mcp" or path.startswith("/mcp/"):
            await self.mcp_app(scope, receive, send)
            return
        await self._response(path)(scope, receive, send)

    def _response(self, path: str) -> Response:
        index = self.dist_dir / "index.html"
        if not self.dist_dir.is_dir() or not index.is_file():
            return _frontend_missing()

        rel = path.lstrip("/")
        # SPA fallback: caminho sem extensão (`/`, `/t/PETR4`, `/s/PETR4/...`) é
        # sempre o shell. Asset com extensão inexistente é 404 — nunca o index.
        if not rel or "." not in Path(rel).name:
            try:
                html = index.read_text(encoding="utf-8")
            except OSError:
                return _frontend_missing()
            html = html.replace("__B3_SESSION__", self.as_of_session or "")
            return Response(
                content=html,
                media_type="text/html",
                headers={"Cache-Control": "no-store"},
            )

        target = (self.dist_dir / rel).resolve()
        try:
            target.relative_to(self.dist_dir.resolve())
        except ValueError:
            return Response(status_code=404, content="Not Found")
        if not target.is_file():
            return Response(status_code=404, content="Not Found")
        headers: dict[str, str] = {}
        if rel.startswith("assets/"):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return FileResponse(target, headers=headers)


def create_app() -> FastAPI:
    """Monta o app: lifespan único, rotas HTTP, estático e o MCP em `/mcp`."""
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=_mcp_allowed_hosts(),
        ),
    )
    spa = _SpaDispatcher(mcp_app, _frontend_dir())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        service = build_service()
        set_service(service)
        try:
            # O carimbo vem do lake (P-C). Uma leitura no startup; a tela usa
            # /api/health para o valor visível.
            calendar = service.lake_calendar()
            spa.as_of_session = calendar[-1].isoformat() if calendar else None
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
    app.router.route_class = _NoCacheRoute
    # GZip é ASGI puro (não quebra o streaming do MCP). JSON/HTML/JS/CSS acima
    # de 500 bytes saem comprimidos quando o cliente aceita gzip.
    app.add_middleware(GZipMiddleware, minimum_size=500)

    @app.exception_handler(SourceFailure)
    async def _source_failure(_request: Request, exc: SourceFailure) -> JSONResponse:
        return _api_error(error_status(exc), error_payload(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Parâmetro malformado também sai no envelope (`400 bad_request`)."""
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.get('loc', ()))}: "
            f"{error.get('msg', 'inválido')}"
            for error in exc.errors()[:3]
        )
        failure = request_error(
            f"parâmetros inválidos ({details})",
            "use date ISO AAAA-MM-DD, inteiros para max_rows/days e floats para delta; "
            "os tipos estão em /api/openapi.json",
        )
        return _api_error(400, error_payload(failure))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """404/405 das rotas `/api` no mesmo formato de erro do projeto."""
        code = {404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return _api_error(
            exc.status_code,
            {
                "error": {
                    "code": code,
                    "message": str(exc.detail),
                    "hint": _API_HINT,
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("api: falha inesperada")
        return _api_error(500, error_payload(exc))

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        """Saúde do lake (P-C): última sessão, idade, fontes e versão do cálculo.

        Só no HTTP: `writes_require_token` diz ao PWA se as escritas pedem token
        (ADR-044). Não é dado do lake, por isso não entra na tool MCP.
        """
        payload = payloads.health_payload(_service())
        payload["data"]["writes_require_token"] = write_token_required()
        return payload

    @app.get("/api/screen")
    def api_screen(
        date: str | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Painel descritivo da sessão (resumo + uma página alfabética via `offset`)."""
        return payloads.screen_payload(
            _service(), date=_date(date), max_rows=max_rows, offset=offset
        )

    @app.get("/api/tickers")
    def api_tickers(q: str = "", max_rows: int = DEFAULT_MAX_ROWS) -> dict[str, Any]:
        """Busca de papel (prefixo primeiro, depois contém), capada em 60 linhas."""
        return payloads.tickers_payload(_service(), q=q, max_rows=max_rows)

    @app.get("/api/glossary")
    def api_glossary() -> dict[str, Any]:
        """Índice dos verbetes do glossário (chave, título, nível)."""
        return payloads.glossary_payload(_service())

    @app.get("/api/glossary/{key}")
    def api_glossary_entry(key: str) -> dict[str, Any]:
        """Um verbete do glossário; chave desconhecida é 404 no envelope."""
        return payloads.glossary_entry_payload(_service(), key)

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

    @app.get("/api/liquid")
    def api_liquid(
        date: str | None = None,
        limit: int = DEFAULT_MAX_ROWS,
    ) -> dict[str, Any]:
        """Séries líquidas da sessão, ordenadas por volume financeiro (F5-E)."""
        return payloads.liquid_payload(
            _service(), date=_date(date), limit=limit
        )

    @app.get("/api/journal")
    def api_journal(
        date: str | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Decisões do dono + previsão/observado/status, paginadas."""
        return payloads.journal_payload(
            _service(),
            date=_date(date),
            max_rows=max_rows,
            offset=offset,
        )

    @app.get("/api/journal/calibration")
    def api_journal_calibration(
        max_rows: int = DEFAULT_MAX_ROWS,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Calibração pessoal por métrica + horizonte; void separado, sem ranking."""
        return payloads.journal_calibration_payload(
            _service(),
            max_rows=max_rows,
            offset=offset,
        )

    @app.get("/api/journal/{decision_id}")
    def api_journal_decision(decision_id: str) -> dict[str, Any]:
        """Detalhe de uma decisão; leitura liberada na tailnet."""
        return payloads.journal_decision_payload(_service(), decision_id)

    @app.post("/api/journal", dependencies=[Depends(require_write_token)])
    async def api_journal_open(request: Request) -> dict[str, Any]:
        """Registra decisão sem posição; token validado antes do corpo JSON."""
        body = await _json_object_body(request)
        return payloads.journal_open_payload(_service(), body)

    @app.get("/api/positions")
    def api_positions(
        date: str | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Posições abertas + agregados (leitura liberada na tailnet)."""
        return payloads.positions_payload(
            _service(),
            date=_date(date),
            max_rows=max_rows,
            offset=offset,
        )

    @app.get("/api/positions/{position_id}")
    def api_position(
        position_id: str,
        date: str | None = None,
    ) -> dict[str, Any]:
        """Detalhe de uma posição com marcação, gregas, tese e payoff."""
        return payloads.position_payload(
            _service(), position_id, date=_date(date)
        )

    @app.post("/api/positions", dependencies=[Depends(require_write_token)])
    async def api_position_open(request: Request) -> dict[str, Any]:
        """Abre posição; o token é validado antes de ler/validar o corpo JSON."""
        body = await _json_object_body(request)
        return payloads.position_open_payload(_service(), body)

    @app.post(
        "/api/positions/{position_id}/close",
        dependencies=[Depends(require_write_token)],
    )
    async def api_position_close(
        position_id: str,
        request: Request,
    ) -> dict[str, Any]:
        """Fecha posição pelo mark EOD; o token vem antes da validação do corpo."""
        body = await _json_object_body(request, allow_empty=True)
        return payloads.position_close_payload(_service(), position_id, body)

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

    @app.post("/api/structure")
    def api_structure(body: dict[str, Any]) -> dict[str, Any]:
        """Avalia a estrutura dada (payoff, breakevens, capital, gregas, cenários).

        É cálculo, não gravação: não cria posição, journal nem arquivo (F6 é que
        grava). O corpo é o do contrato onda-j §2.
        """
        return payloads.structure_payload(_service(), body)

    @app.get("/api/{rest:path}")
    def api_not_found(rest: str) -> dict[str, Any]:
        """Rota `/api` desconhecida: erro no envelope, não o 404 do app montado."""
        raise SourceFailure(
            SourceError(
                code="not_found",
                message=f"rota /api/{rest} não existe no F3/F4/F5/F6/F7",
                hint=_API_HINT,
            )
        )

    # Dispatcher por último: as rotas /api e o MCP continuam intactos.
    app.mount("/", spa)
    return app


_MAX_WRITE_BODY_BYTES = 65_536


async def _json_object_body(request: Request, *, allow_empty: bool = False) -> dict[str, Any]:
    """Corpo JSON limitado; sem modelo Pydantic para a auth rodar antes do parse."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError as exc:
            raise request_error("Content-Length inválido", "envie um corpo JSON válido") from exc
        if length < 0 or length > _MAX_WRITE_BODY_BYTES:
            raise request_error(
                "corpo da escrita excede 64 KiB",
                "reduza notas/pernas; o contrato limita 60 pernas e 2000 caracteres de notas",
            )
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_WRITE_BODY_BYTES:
            raise request_error(
                "corpo da escrita excede 64 KiB",
                "reduza notas/pernas; o contrato limita 60 pernas e 2000 caracteres de notas",
            )
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw and allow_empty:
        return {}
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise request_error("corpo da escrita não é JSON válido", "envie um objeto JSON") from exc
    if not isinstance(body, dict):
        raise request_error("corpo da escrita precisa ser um objeto JSON", "envie { ... }")
    return body


def _service():
    """Import tardio do holder (evita ciclo na importação do módulo)."""
    from b3o.api.state import get_service

    return get_service()


def _date(value: str | None, name: str = "date") -> date | None:
    return parse_date(value, name)


app = create_app()

__all__ = ["app", "create_app"]
