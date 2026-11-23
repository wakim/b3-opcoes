"""Camada de consulta (F0.12): `QueryService` é a única porta de leitura do lake.

MCP (`/mcp`) e HTTP chamam o mesmo objeto, no mesmo processo (spec §7). O F0
entregou `health()` e os helpers de calendário; o F2, o painel descritivo; o F3,
as leituras de detalhe (`underlying`, `chain`, `series`, `events`).
"""

from __future__ import annotations

from b3o.query.service import (
    PREPARE_STATE_FILE,
    ChainResult,
    ChainRow,
    EarningsEventRow,
    EventsDetail,
    Health,
    HistoryRow,
    PrepareState,
    QueryService,
    ScreenBundle,
    ScreenRow,
    SeriesDetail,
    SeriesPoint,
    SmileRow,
    StrikeEventRow,
    TermRow,
    UnderlyingDetail,
    load_prepare_state,
    prepare_state_path,
    save_prepare_state,
)

__all__ = [
    "PREPARE_STATE_FILE",
    "ChainResult",
    "ChainRow",
    "EarningsEventRow",
    "EventsDetail",
    "Health",
    "HistoryRow",
    "PrepareState",
    "QueryService",
    "ScreenBundle",
    "ScreenRow",
    "SeriesDetail",
    "SeriesPoint",
    "SmileRow",
    "StrikeEventRow",
    "TermRow",
    "UnderlyingDetail",
    "load_prepare_state",
    "prepare_state_path",
    "save_prepare_state",
]
