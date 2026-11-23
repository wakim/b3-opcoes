"""Estado do processo da API/MCP (F3).

Umas das regras da onda: **uma única** instância de `QueryService`, criada no
lifespan do FastAPI e compartilhada por HTTP e MCP. O holder abaixo é o elo —
nada de abrir DuckDB por request nem de ler o lake em outro lugar.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock

from b3o.config import load_config
from b3o.query.service import QueryService

log = logging.getLogger(__name__)

CONFIG_ENV = "B3_CONFIG"
"""Caminho do `config.yaml` (default: `config.yaml` no cwd, como a CLI)."""

_lock = Lock()
_service: QueryService | None = None


def build_service() -> QueryService:
    """Lê o config validado (falha explícita) e monta a única `QueryService`."""
    config_path = Path(os.environ.get(CONFIG_ENV, "config.yaml"))
    config = load_config(config_path)
    log.info("b3o api: data_dir=%s model_version=%s", config.data_dir, config.model_version)
    return QueryService(config.data_dir)


def set_service(service: QueryService | None) -> None:
    """Instala a instância do processo (lifespan); `None` no desligamento."""
    global _service
    with _lock:
        _service = service


def get_service() -> QueryService:
    """A instância viva; erro explícito se alguém chamar fora do lifespan."""
    service = _service
    if service is None:
        raise RuntimeError(
            "QueryService indisponível: a API não inicializou o lifespan "
            "(use uvicorn b3o.api:app, não chame as rotas direto)"
        )
    return service
