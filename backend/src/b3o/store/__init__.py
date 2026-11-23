"""Camada de armazenamento: lake Parquet (`lake`) e estado da ingestão (`state`).

F0.5 do plano. O lake é imutável para quem lê (Parquet particionado por `year`, escrito de
forma atômica) e o `ingest_state.json` é o retrato do último `prepare` — o `health` lê os
dois, o MCP abre o lake em modo somente leitura (spec §4, AGENTS.md "Armazenamento").

`lake.py` é o único lugar que escreve/ler Parquet do COTAHIST; `state.py` é o único que
escreve `ingest_state.json`. Nada aqui calcula métrica: fórmula é `derive/`, consulta é
`query/service.py`.
"""

from __future__ import annotations

from b3o.store.lake import (
    PARTITION_FILE,
    RAW_COTAHIST_COLUMNS,
    RAW_COTAHIST_SCHEMA,
    RAW_COTAHIST_TABLE,
    READ_BATCH_ROWS,
    WRITE_BATCH_ROWS,
    WriteStats,
    cotahist_partition_path,
    read_cotahist_years,
    write_cotahist_year,
)
from b3o.store.state import (
    INGEST_STATE_FILE,
    MODEL_VERSION,
    IngestState,
    SkippedLines,
    ingest_state_path,
    load_ingest_state,
    save_ingest_state,
    skipped_lines_from_report,
    update_ingest_state,
)

__all__ = [
    "INGEST_STATE_FILE",
    "MODEL_VERSION",
    "PARTITION_FILE",
    "RAW_COTAHIST_COLUMNS",
    "RAW_COTAHIST_SCHEMA",
    "RAW_COTAHIST_TABLE",
    "READ_BATCH_ROWS",
    "WRITE_BATCH_ROWS",
    "IngestState",
    "SkippedLines",
    "WriteStats",
    "cotahist_partition_path",
    "ingest_state_path",
    "load_ingest_state",
    "read_cotahist_years",
    "save_ingest_state",
    "skipped_lines_from_report",
    "update_ingest_state",
    "write_cotahist_year",
]
