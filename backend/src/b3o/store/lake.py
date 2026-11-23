"""Lake Parquet do COTAHIST (F0.5): particionado por ano, escrito em streaming, trocado por átomo.

Contrato: `docs/contratos/onda-b.md` §2 (P-A, spec §5.1 e §11). Regras que este arquivo respeita:

- **uma partição por ano**: `data_dir/raw_cotahist/year=YYYY/part-0.parquet`. O ano corrente é
  republicado inteiro todo dia pela B3, então a partição é **reconstruída inteira** — nunca
  há append (P-C/spec §11). `year` vive no diretório, não como coluna do arquivo;
- **streaming**: os 3,5 M de linhas do arquivo anual nunca cabem juntos em RAM (o runtime tem
  3,7 GiB). Escreve em lotes de `WRITE_BATCH_ROWS` e lê em lotes de `READ_BATCH_ROWS`;
- **escrita atômica**: grava em `data_dir/tmp/…`, confere a contagem do Parquet recém-escrito,
  e só então publica a partição (`fsutil.replace_file`: `os.replace` puro; no bind do Windows,
  onde replace sobre arquivo existente dá EACCES, unlink + rename com janela documentada).
  Falha no meio do iterável deixa a partição antiga intacta e não deixa `tmp/` órfão;
- **nada de arredondamento**: preços, volume, strike e `strike_pts` são `int64` em centavos
  (volume de série quente passa de R$ 21 M/dia e estoura `int32`); datas são `date32`;
  `style`/`side`/`maturity` são nulos de verdade (`NullArray` desnecessário: coluna nullable);
- **falha explícita**: diretório inválido, lote vazio, `refdate` de outro ano, partição ausente
  ou Parquet corrompido → `SourceFailure` com `SourceError` {code, message, hint}. Erro de
  disco (permissão, volume cheio, I/O) em escrita **ou** leitura vira
  `code="storage_error"` — nunca traceback cru de `OSError` escapando do módulo.

Os bytes do Parquet são determinísticos para o mesmo lote (medido no teste de idempotência):
o writer do pyarrow não embute timestamp, e cada `write_batch` de `WRITE_BATCH_ROWS` vira
exatamente um row group (confirmado no pyarrow 26: 3 lotes → 3 row groups) — o mesmo input
produz o mesmo `sha256`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from b3o.fsutil import replace_file
from b3o.ingest.errors import SourceError, SourceFailure
from b3o.ingest.parse import KEPT_MARKETS, CotahistRow

log = logging.getLogger(__name__)

RAW_COTAHIST_TABLE = "raw_cotahist"
"""Nome da tabela no lake (`data_dir/raw_cotahist/…`)."""

PARTITION_FILE = "part-0.parquet"
"""Único arquivo de uma partição: o ano é reconstruído inteiro, nunca por append."""

TMP_DIR = "tmp"
"""Diretório de staging dentro do lake (mesmo filesystem → `os.replace` é atômico)."""

WRITE_BATCH_ROWS = 100_000
"""Linhas por `RecordBatch` na escrita (contrato §2; nunca o ano inteiro em RAM)."""

READ_BATCH_ROWS = 50_000
"""Linhas por lote na leitura.

Metade do lote de escrita de propósito: na leitura cada linha é materializada em Python
(`CotahistRow`), o que pesa mais que o `RecordBatch` do writer. 50 k linhas ficam na casa de
dezenas de MiB de pico — a mesma ordem do parser (21 MiB medidos).
"""

RAW_COTAHIST_COLUMNS: list[str] = [
    "refdate",
    "bdi",
    "ticker",
    "tpmerc",
    "corp_name",
    "spec",
    "open",
    "high",
    "low",
    "avg",
    "close",
    "best_bid",
    "best_ask",
    "trades",
    "qty",
    "volume",
    "strike",
    "strike_adj_ind",
    "maturity",
    "quote_factor",
    "strike_pts",
    "isin_underlying",
    "distribution_id",
    "style",
    "side",
]
"""Ordem canônica das colunas (§5.1 + extras da Onda A); é também a ordem do `CotahistRow`."""

_STRING_COLUMNS = (
    "bdi",
    "ticker",
    "tpmerc",
    "corp_name",
    "spec",
    "strike_adj_ind",
    "isin_underlying",
    "distribution_id",
    "style",
    "side",
)
_INT_COLUMNS = (
    "open",
    "high",
    "low",
    "avg",
    "close",
    "best_bid",
    "best_ask",
    "trades",
    "qty",
    "volume",
    "strike",
    "quote_factor",
    "strike_pts",
)
_NULLABLE_COLUMNS = ("maturity", "style", "side")

_COLUMN_TYPES: dict[str, pa.DataType] = {
    "refdate": pa.date32(),
    "maturity": pa.date32(),
    **{name: pa.int64() for name in _INT_COLUMNS},
    **{name: pa.string() for name in _STRING_COLUMNS},
}

RAW_COTAHIST_SCHEMA = pa.schema(
    [
        pa.field(name, _COLUMN_TYPES[name], nullable=name in _NULLABLE_COLUMNS)
        for name in RAW_COTAHIST_COLUMNS
    ]
)
"""Schema do Parquet: `date32` em `refdate`/`maturity`, `int64` em todo número, string no resto."""

_HINT_DIR = "confira B3_DATA_DIR (no container é /data) e se o caminho é um diretório gravável"
_HINT_DISK = (
    "confira espaço e permissão do volume de dados do container (B3_DATA_DIR, /data); "
    "a partição anterior não foi tocada"
)
_HINT_PREPARE = "rode `b3o prepare` para o ano correspondente antes de ler o lake"


@dataclass(frozen=True, slots=True)
class WriteStats:
    """Resumo de uma escrita de partição (vai para o health): linhas, arquivo, hash e duração."""

    rows: int
    path: Path
    sha256: str
    seconds: float


def cotahist_partition_path(data_dir: Path, year: int) -> Path:
    """Caminho do Parquet de um ano: `data_dir/raw_cotahist/year=YYYY/part-0.parquet`."""
    return Path(data_dir) / RAW_COTAHIST_TABLE / f"year={int(year):04d}" / PARTITION_FILE


def write_cotahist_year(rows: Iterable[CotahistRow], year: int, data_dir: Path) -> WriteStats:
    """Escreve a partição de **um** ano, reconstruindo-a inteira e de forma atômica.

    `rows` é consumido em streaming (o chamador pode passar o gerador do parser direto).
    Toda linha precisa ter `refdate.year == year`: um lote de anos misturados é erro de
    chamada, não algo a filtrar em silêncio — a partição é a unidade de verdade do lake e
    um `year` divergente produziria um arquivo cuja partição não corresponde ao conteúdo.

    Falha em qualquer ponto deixa a partição anterior **intacta**: o Parquet novo só aparece
    no caminho final depois de fechado, com contagem conferida contra o que foi consumido.
    """
    root = _ensure_dir(data_dir, action="escrever")
    partition_path = cotahist_partition_path(root, year)
    partition_dir = partition_path.parent
    tmp_dir = root / TMP_DIR / f"cotahist-{int(year):04d}-{uuid.uuid4().hex[:12]}"
    tmp_path = tmp_dir / PARTITION_FILE

    started = time.perf_counter()
    written = 0
    batch: list[CotahistRow] = []

    try:
        tmp_dir.mkdir(parents=True)
        with open(tmp_path, "wb") as handle:
            with pq.ParquetWriter(
                handle,
                RAW_COTAHIST_SCHEMA,
                compression="snappy",
            ) as writer:
                for row in rows:
                    if row.refdate.year != year:
                        raise SourceFailure(
                            SourceError(
                                code="bad_data",
                                message=(
                                    f"linha de {row.refdate.isoformat()} ({row.ticker}) "
                                    f"na partição year={int(year):04d}"
                                ),
                                hint=(
                                    "write_cotahist_year recebe UM ano: filtre as linhas por "
                                    "refdate.year antes de chamar (partição = ano)"
                                ),
                            )
                        )
                    batch.append(row)
                    if len(batch) >= WRITE_BATCH_ROWS:
                        writer.write_batch(_record_batch(batch))
                        written += len(batch)
                        batch = []
                if batch:
                    writer.write_batch(_record_batch(batch))
                    written += len(batch)
                    batch = []
            handle.flush()
            os.fsync(handle.fileno())

        if written == 0:
            raise SourceFailure(
                SourceError(
                    code="bad_data",
                    message=f"nenhuma linha para a partição year={int(year):04d}",
                    hint=(
                        "arquivo vazio ou filtro que não casou: partição vazia apagaria o dado "
                        "bom; a escrita é abortada de propósito"
                    ),
                )
            )

        stored = pq.ParquetFile(tmp_path).metadata.num_rows
        if stored != written:
            raise SourceFailure(
                SourceError(
                    code="bad_data",
                    message=(
                        f"Parquet gravado com {stored} linhas, consumidas {written} "
                        f"(year={int(year):04d})"
                    ),
                    hint="escrita truncada: disco cheio ou falha do pyarrow; partição intacta",
                )
            )

        sha256 = _sha256_file(tmp_path)
        partition_dir.mkdir(parents=True, exist_ok=True)
        # bind do Windows: `os.replace` sobre arquivo existente dá EACCES → `replace_file`
        # (unlink + rename, janela documentada em AGENTS.md/fsutil)
        replace_file(tmp_path, partition_path)
        _remove_stale_parts(partition_dir)
    except OSError as exc:
        # permissão, disco cheio, I/O: vira SourceError explícito (padrão de rates/OI)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise _storage_failure("gravar a partição", partition_path, exc) from exc
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    shutil.rmtree(tmp_dir, ignore_errors=True)
    seconds = time.perf_counter() - started
    log.info(
        "lake gravou %s: %d linhas, sha256=%s, %.2fs",
        partition_path,
        written,
        sha256[:12],
        seconds,
    )
    return WriteStats(rows=written, path=partition_path, sha256=sha256, seconds=seconds)


def read_cotahist_years(
    data_dir: Path,
    years: Iterable[int],
    markets: frozenset[str] = KEPT_MARKETS,
) -> Iterator[CotahistRow]:
    """Lê as partições dos anos pedidos, uma linha por vez (gerador de verdade).

    O filtro `markets` é defensivo: o lake já só guarda `KEPT_MARKETS`, mas quem lê não
    deve depender disso. Partição ausente e Parquet corrompido falham de forma explícita
    (`SourceFailure`); nada é devolvido pela metade em silêncio. Erro de disco na leitura
    (permissão, I/O) vira `storage_error`; arquivo corrompido continua `bad_data`.
    """
    root = Path(data_dir)
    if root.exists() and not root.is_dir():
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"data_dir não é diretório: {root}",
                hint=_HINT_DIR,
            )
        )
    for year in years:
        path = cotahist_partition_path(root, year)
        if not path.is_file():
            raise SourceFailure(
                SourceError(
                    code="lake_missing",
                    message=f"partição do COTAHIST de {int(year):04d} não existe: {path}",
                    hint=_HINT_PREPARE,
                )
            )
        try:
            parquet = pq.ParquetFile(path)
            names = tuple(parquet.schema_arrow.names)
        except OSError as exc:
            raise _storage_failure("ler a partição", path, exc) from exc
        except pa.ArrowException as exc:
            raise SourceFailure(
                SourceError(
                    code="bad_data",
                    message=f"Parquet ilegível: {path}: {exc}",
                    hint=(
                        "arquivo corrompido ou de versão anterior do schema; "
                        "rode `b3o prepare` de novo para reconstruir a partição"
                    ),
                )
            ) from exc
        if names != tuple(RAW_COTAHIST_COLUMNS):
            raise SourceFailure(
                SourceError(
                    code="bad_data",
                    message=(
                        f"schema inesperado em {path}: {list(names)} "
                        f"(esperado {RAW_COTAHIST_COLUMNS})"
                    ),
                    hint="schema do lake mudou: rode `b3o prepare` para reescrever a partição",
                )
            )
        yield from _iter_rows(parquet, markets, path)


def _iter_rows(
    parquet: pq.ParquetFile,
    markets: frozenset[str],
    path: Path,
) -> Iterator[CotahistRow]:
    """Itera um Parquet por lotes, materializando `CotahistRow` só no lote corrente."""
    try:
        for batch in parquet.iter_batches(
            batch_size=READ_BATCH_ROWS,
            columns=RAW_COTAHIST_COLUMNS,
        ):
            columns = [batch.column(i).to_pylist() for i in range(len(RAW_COTAHIST_COLUMNS))]
            for values in zip(*columns, strict=True):
                row = CotahistRow(*values)
                if row.tpmerc in markets:
                    yield row
    except OSError as exc:
        raise _storage_failure("ler a partição", path, exc) from exc
    except pa.ArrowException as exc:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"falha ao ler {path}: {exc}",
                hint=(
                    "arquivo corrompido ou truncado; "
                    "rode `b3o prepare` de novo para reconstruir a partição"
                ),
            )
        ) from exc


def _record_batch(rows: Sequence[CotahistRow]) -> pa.RecordBatch:
    """Empacota um lote de `CotahistRow` no schema canônico, coluna por coluna."""
    arrays = [
        pa.array(
            [getattr(row, name) for row in rows],
            type=RAW_COTAHIST_SCHEMA.field(name).type,
        )
        for name in RAW_COTAHIST_COLUMNS
    ]
    return pa.RecordBatch.from_arrays(arrays, schema=RAW_COTAHIST_SCHEMA)


def _sha256_file(path: Path) -> str:
    """`sha256` dos bytes do arquivo, em blocos (nunca o arquivo inteiro em RAM)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_stale_parts(partition_dir: Path) -> None:
    """Garante que a partição é exatamente `part-0.parquet` (o ano inteiro, sem sobras)."""
    for path in partition_dir.glob("*.parquet"):
        if path.name != PARTITION_FILE:
            path.unlink()
            log.info("lake removeu parte obsoleta %s", path)


def _storage_failure(action: str, path: Path, exc: OSError) -> SourceFailure:
    """Erro de disco com o formato do projeto: código `storage_error`, mensagem e dica.

    Mesmo padrão de `ingest/rates.py` e `ingest/open_interest.py`: um `OSError` (permissão,
    volume cheio, I/O) interrompe, mas nunca escapa cru — a borda (prepare/health) precisa
    de `{code, message, hint}`, não de traceback.
    """
    return SourceFailure(
        SourceError(
            code="storage_error",
            message=f"falha de disco ao {action} {path}: {exc}",
            hint=_HINT_DISK,
        )
    )


def _ensure_dir(data_dir: Path, *, action: str) -> Path:
    """Cria `data_dir` se preciso; recusa caminho que é arquivo ou não pode ser criado."""
    root = Path(data_dir)
    if root.exists() and not root.is_dir():
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"data_dir não é diretório: {root}",
                hint=_HINT_DIR,
            )
        )
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _storage_failure(f"{action} em", root, exc) from exc
    return root


__all__ = [
    "PARTITION_FILE",
    "RAW_COTAHIST_COLUMNS",
    "RAW_COTAHIST_SCHEMA",
    "RAW_COTAHIST_TABLE",
    "READ_BATCH_ROWS",
    "WRITE_BATCH_ROWS",
    "WriteStats",
    "cotahist_partition_path",
    "read_cotahist_years",
    "write_cotahist_year",
]
