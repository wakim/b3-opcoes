"""API HTTP + MCP do b3o (F3).

O alvo do uvicorn no `compose.yaml` é `b3o.api:app`. As rotas `/api/...` e as
tools MCP em `/mcp` chamam os mesmos construtores de payload sobre a **mesma**
`QueryService` (contrato `docs/contratos/onda-i.md`).
"""

from __future__ import annotations

from b3o.api.app import app, create_app

__all__ = ["app", "create_app"]
