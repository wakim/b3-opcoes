"""Camada de consulta (F0.12): `QueryService` é a única porta de leitura do lake.

MCP (`/mcp`) e HTTP chamam o mesmo objeto, no mesmo processo (spec §7). As 10
funções de leitura nascem aqui aos poucos: o F0 entrega `health()` e os helpers de
calendário que o `prepare` usa.
"""

from __future__ import annotations

from b3o.query.service import (
    PREPARE_STATE_FILE,
    Health,
    PrepareState,
    QueryService,
    load_prepare_state,
    prepare_state_path,
    save_prepare_state,
)

__all__ = [
    "PREPARE_STATE_FILE",
    "Health",
    "PrepareState",
    "QueryService",
    "load_prepare_state",
    "prepare_state_path",
    "save_prepare_state",
]
