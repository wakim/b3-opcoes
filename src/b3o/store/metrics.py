"""Escrita das tabelas de métrica (Onda F): `metrics_series` e `metrics_underlying`.

Contrato: `docs/contratos/onda-fg.md` §4.3 (schema exato) e ADR-009
(`data/<tabela>/year=YYYY/part-0.parquet`). Este módulo é **só escrita** — quem
calcula é `b3o/metrics/` (pacote F1b), que não pode importar `duckdb` nem
`pyarrow` para I/O de tabela; a leitura das métricas já gravadas (histórico de
`iv_pct_252`, `vrp_z_252`) também passa por aqui, mas pelo lado de leitura do
DuckDB, que é `query/service.py`.

Regras que este arquivo respeita:

- **schema fechado**: coluna faltando, coluna extra, tipo errado ou valor fora do
  domínio → `SourceFailure(code="bad_data")` com a linha e o campo no `message`.
  Nunca se grava um Parquet divergente do contrato "porque o consumidor entende";
  um `float` no lugar de `int64` é um bug de 100× esperando para acontecer;
- **unidades do contrato**: preço/strike em **centavos por ação**, IV e vols em
  **% a.a.**, VRP/skew/slope em **pontos de vol**, `exp_move` em **fração**;
  nada de R$ de contrato aqui (o ×100 é do F4);
- **partição**: `year=YYYY` (ADR-009), substituída inteira em `--backfill` e
  mesclada por `refdate` no cálculo de um dia (idempotente: duas escritas iguais
  produzem o mesmo `sha256` — ordenação determinística + zstd);
- **escrita atômica**: staging em `data_dir/tmp/`, contagem conferida, publicação
  por `b3o.fsutil.replace_file` (no bind do Windows `os.replace` sobre arquivo
  existente dá EACCES — medido 22/11/2026).
"""

from __future__ import annotations

import hashlib
import math
import shutil
import time
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from b3o.fsutil import replace_file
from b3o.ingest.errors import SourceError, SourceFailure
from b3o.store.lake import PARTITION_FILE, TMP_DIR, WriteStats

METRICS_SERIES_TABLE = "metrics_series"
"""`data/metrics_series/year=YYYY/part-0.parquet` — uma linha por `(refdate, ticker)`."""

METRICS_UNDERLYING_TABLE = "metrics_underlying"
"""`data/metrics_underlying/year=YYYY/part-0.parquet` — uma linha por `(refdate, underlying)`."""

METRICS_SERIES_COLUMNS: list[str] = [
    "refdate",
    "ticker",
    "maturity",
    "leg_id",
    "side",
    "style",
    "strike",
    "t",
    "mid",
    "quote_factor",
    "iv",
    "delta",
    "gamma",
    "theta_brl",
    "theta_pct",
    "vega_brl",
    "rho",
    "iv_pct_252",
    "is_liquid",
    "flags",
    "model_version",
]
"""Ordem canônica das colunas de `metrics_series` (contrato §4.3)."""

METRICS_UNDERLYING_COLUMNS: list[str] = [
    "refdate",
    "underlying",
    "spot",
    "underlying_market",
    "iv_atm_30d",
    "iv_rank_252",
    "iv_pct_252",
    "rv20",
    "rv30",
    "rv60",
    "vrp",
    "vrp_z_252",
    "rr25",
    "bfly25",
    "ts_slope_30_90",
    "exp_move_1s_30d",
    "implied_div_yield_30d",
    "days_to_liquidate",
    "forward_30d",
    "rate_source",
    "signal_class",
    "flags",
    "model_version",
]
"""Ordem canônica das colunas de `metrics_underlying` (contrato §4.3)."""

SIGNAL_CLASSES: tuple[str, ...] = (
    "premium_rich",
    "premium_cheap",
    "skew_anomaly",
    "event_pressure",
    "none",
)
"""Classes da spec §6.9 — domínio fechado de `signal_class`."""

_HINT_SCHEMA = (
    "o schema das tabelas de métrica é o contrato docs/contratos/onda-fg.md §4.3; "
    "confira nomes, tipos e unidades (preço em centavos, vol em % a.a.)"
)
_HINT_DISK = (
    "confira espaço/permissão do volume de dados do container (B3_DATA_DIR, /data); "
    "a partição anterior não foi tocada"
)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


def _schema(spec: Mapping[str, _Column]) -> pa.Schema:
    """Schema pyarrow a partir da especificação das colunas (ordem do contrato)."""
    types = {
        "date": pa.date32(),
        "str": pa.string(),
        "int": pa.int64(),
        "float": pa.float64(),
        "bool": pa.bool_(),
        "str_list": pa.list_(pa.string()),
    }
    return pa.schema(
        [
            pa.field(name, types[column.kind], nullable=column.nullable)
            for name, column in spec.items()
        ]
    )


@dataclass(frozen=True, slots=True)
class _Column:
    """Tipo lógico de uma coluna + se aceita `None` (contrato §4.3: só os `|null`)."""

    kind: str
    nullable: bool = False


_OPT = True

METRICS_SERIES_SPEC: dict[str, _Column] = {
    "refdate": _Column("date"),
    "ticker": _Column("str"),
    "maturity": _Column("date"),
    "leg_id": _Column("str"),
    "side": _Column("str"),
    "style": _Column("str", nullable=_OPT),
    "strike": _Column("int"),
    "t": _Column("float"),
    "mid": _Column("float"),
    "quote_factor": _Column("int"),
    "iv": _Column("float", nullable=_OPT),
    "delta": _Column("float", nullable=_OPT),
    "gamma": _Column("float", nullable=_OPT),
    "theta_brl": _Column("float", nullable=_OPT),
    "theta_pct": _Column("float", nullable=_OPT),
    "vega_brl": _Column("float", nullable=_OPT),
    "rho": _Column("float", nullable=_OPT),
    "iv_pct_252": _Column("float", nullable=_OPT),
    "is_liquid": _Column("bool"),
    "flags": _Column("str_list"),
    "model_version": _Column("int"),
}

METRICS_UNDERLYING_SPEC: dict[str, _Column] = {
    "refdate": _Column("date"),
    "underlying": _Column("str"),
    "spot": _Column("float"),
    "underlying_market": _Column("str"),
    "iv_atm_30d": _Column("float", nullable=_OPT),
    "iv_rank_252": _Column("float", nullable=_OPT),
    "iv_pct_252": _Column("float", nullable=_OPT),
    "rv20": _Column("float", nullable=_OPT),
    "rv30": _Column("float", nullable=_OPT),
    "rv60": _Column("float", nullable=_OPT),
    "vrp": _Column("float", nullable=_OPT),
    "vrp_z_252": _Column("float", nullable=_OPT),
    "rr25": _Column("float", nullable=_OPT),
    "bfly25": _Column("float", nullable=_OPT),
    "ts_slope_30_90": _Column("float", nullable=_OPT),
    "exp_move_1s_30d": _Column("float", nullable=_OPT),
    "implied_div_yield_30d": _Column("float", nullable=_OPT),
    "days_to_liquidate": _Column("float", nullable=_OPT),
    "forward_30d": _Column("float", nullable=_OPT),
    # §4.4 manda a sessão sem curva sair com None + flag `no_rates`; o contrato
    # §4.3 não declara `rate_source` nulo, então a coluna é obrigatória e quem
    # grava nomeia a fonte tentada (o F1b documenta o valor no relatório).
    "rate_source": _Column("str"),
    "signal_class": _Column("str"),
    "flags": _Column("str_list"),
    "model_version": _Column("int"),
}

METRICS_SERIES_SCHEMA = _schema(METRICS_SERIES_SPEC)
METRICS_UNDERLYING_SCHEMA = _schema(METRICS_UNDERLYING_SPEC)

_SERIES_SORT_KEYS = [("refdate", "ascending"), ("ticker", "ascending")]
_UNDERLYING_SORT_KEYS = [("refdate", "ascending"), ("underlying", "ascending")]


# --------------------------------------------------------------------------- #
# Caminhos
# --------------------------------------------------------------------------- #


def metrics_series_path(data_dir: Path, year: int) -> Path:
    """`data_dir/metrics_series/year=YYYY/part-0.parquet`."""
    return Path(data_dir) / METRICS_SERIES_TABLE / f"year={int(year):04d}" / PARTITION_FILE


def metrics_underlying_path(data_dir: Path, year: int) -> Path:
    """`data_dir/metrics_underlying/year=YYYY/part-0.parquet`."""
    return Path(data_dir) / METRICS_UNDERLYING_TABLE / f"year={int(year):04d}" / PARTITION_FILE


# --------------------------------------------------------------------------- #
# Escritores
# --------------------------------------------------------------------------- #


def write_metrics_series(
    rows: Iterable[Mapping[str, object]],
    data_dir: Path,
    *,
    refdate_replaced: date | None = None,
) -> WriteStats:
    """Grava `metrics_series` (uma linha por `(refdate, ticker)`).

    `refdate_replaced is None` → **substitui** a partição do(s) ano(s) das linhas
    (`--backfill`). Com uma data → **merge**: lê a partição do ano, remove as
    linhas daquele `refdate`, concatena as novas, ordena por `(refdate, ticker)`
    e regrava. Nesse modo todas as linhas precisam ter `refdate == refdate_replaced`
    (o cálculo é de um dia por vez; misturar datas numa mesclagem é bug de chamada).

    Unidades: `strike`/`mid` em **centavos por ação** (`mid = (bid+ask)/2 / FATCOT`
    já resolvido pelo chamador), `t` em dias úteis / 252, IV e gregas como no
    contrato §4.3. `None` só nas colunas que o contrato declara `|null`.
    """
    return _write(
        rows,
        data_dir,
        table=METRICS_SERIES_TABLE,
        columns=METRICS_SERIES_COLUMNS,
        spec=METRICS_SERIES_SPEC,
        schema=METRICS_SERIES_SCHEMA,
        sort_keys=_SERIES_SORT_KEYS,
        path_for=metrics_series_path,
        refdate_replaced=refdate_replaced,
    )


def write_metrics_underlying(
    rows: Iterable[Mapping[str, object]],
    data_dir: Path,
    *,
    refdate_replaced: date | None = None,
) -> WriteStats:
    """Grava `metrics_underlying` (uma linha por `(refdate, underlying)`).

    Mesma semântica de partição/merge de `write_metrics_series`, ordenando por
    `(refdate, underlying)`. `spot` e `forward_30d` em **centavos por ação**;
    IV e RV em **% a.a.**; `vrp`/`rr25`/`bfly25`/`ts_slope_30_90` em pontos de
    vol; `exp_move_1s_30d` em fração.

    `rate_source` é **obrigatório** (§4.3 não o declara nulo): a sessão sem curva
    sai com as métricas `None` + flag `no_rates`, e quem grava nomeia a fonte
    tentada — `None` aqui é erro de contrato, não ausência de dado.
    """
    return _write(
        rows,
        data_dir,
        table=METRICS_UNDERLYING_TABLE,
        columns=METRICS_UNDERLYING_COLUMNS,
        spec=METRICS_UNDERLYING_SPEC,
        schema=METRICS_UNDERLYING_SCHEMA,
        sort_keys=_UNDERLYING_SORT_KEYS,
        path_for=metrics_underlying_path,
        refdate_replaced=refdate_replaced,
    )


def _write(
    rows: Iterable[Mapping[str, object]],
    data_dir: Path,
    *,
    table: str,
    columns: Sequence[str],
    spec: Mapping[str, _Column],
    schema: pa.Schema,
    sort_keys: list[tuple[str, str]],
    path_for,
    refdate_replaced: date | None,
) -> WriteStats:
    """Núcleo dos dois escritores: valida, agrupa por ano e publica por partição."""
    started = time.perf_counter()
    payload = _validated(rows, table=table, columns=columns, spec=spec)

    if refdate_replaced is not None:
        wrong = sorted({row["refdate"] for row in payload if row["refdate"] != refdate_replaced})
        if wrong:
            raise _bad_row(
                table,
                f"merge de um dia recebeu refdate(s) {wrong} além de {refdate_replaced}",
                hint="passe refdate_replaced=None para substituir a partição inteira",
            )

    by_year: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in payload:
        by_year[row["refdate"].year].append(row)  # type: ignore[union-attr]

    root = Path(data_dir)
    total_rows = 0
    last_path: Path | None = None
    last_sha = ""
    for year in sorted(by_year):
        target = path_for(root, year)
        rows_in_partition, sha256 = _publish(
            by_year[year],
            target,
            schema=schema,
            sort_keys=sort_keys,
            merge_day=refdate_replaced,
        )
        total_rows += rows_in_partition
        last_path = target
        last_sha = sha256

    if last_path is None:  # pragma: no cover - _validated recusa lista vazia
        raise _bad_row(table, "nenhuma partição para gravar")
    seconds = time.perf_counter() - started
    return WriteStats(rows=total_rows, path=last_path, sha256=last_sha, seconds=seconds)


def _publish(
    rows: list[dict[str, object]],
    target: Path,
    *,
    schema: pa.Schema,
    sort_keys: list[tuple[str, str]],
    merge_day: date | None,
) -> tuple[int, str]:
    """Escreve uma partição (substituindo o ano ou mesclando um `refdate`).

    Devolve `(linhas_no_arquivo, sha256)` — em merge, `linhas_no_arquivo` é o
    tamanho final da partição (as linhas preservadas também contam).
    """
    new = pa.Table.from_pylist(rows, schema=schema)
    if merge_day is not None and target.is_file():
        existing = _read_partition(target, schema)
        keep = pc.invert(pc.equal(existing.column("refdate"), pa.scalar(merge_day, pa.date32())))
        table = pa.concat_tables([existing.filter(keep), new])
    else:
        table = new
    table = table.sort_by(sort_keys)

    tmp_dir = target.parents[2] / TMP_DIR / f"metrics-{uuid.uuid4().hex[:12]}"
    tmp_path = tmp_dir / PARTITION_FILE
    try:
        tmp_dir.mkdir(parents=True)
        pq.write_table(table, tmp_path, compression="zstd")
        written = pq.read_metadata(tmp_path).num_rows
        if written != table.num_rows:
            raise SourceFailure(
                SourceError(
                    code="bad_data",
                    message=f"Parquet de {target} com {written} linhas, esperadas {table.num_rows}",
                    hint="escrita truncada: disco cheio ou falha do pyarrow; partição intacta",
                )
            )
        sha256 = _sha256_file(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # bind do Windows: replace sobre arquivo existente dá EACCES (AGENTS.md)
        replace_file(tmp_path, target)
        _remove_stale_parts(target.parent)
    except OSError as exc:
        raise SourceFailure(
            SourceError(
                code="storage_error",
                message=f"falha de disco ao gravar {target}: {exc}",
                hint=_HINT_DISK,
            )
        ) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return table.num_rows, sha256


def _read_partition(target: Path, schema: pa.Schema) -> pa.Table:
    """Lê a partição existente; schema diferente é erro explícito, não conversão."""
    try:
        table = pq.read_table(target)
    except (OSError, pa.ArrowException) as exc:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"partição de métrica ilegível em {target}: {exc}",
                hint="rode `b3o metrics --backfill` para reconstruir a partição",
            )
        ) from exc
    if not table.schema.equals(schema, check_metadata=False):
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=(
                    f"schema divergente em {target}: {table.schema.names} "
                    f"(esperado {schema.names})"
                ),
                hint=_HINT_SCHEMA,
            )
        )
    return table


def _remove_stale_parts(directory: Path) -> None:
    """Garante que a partição é exatamente `part-0.parquet` (sem sobras de escrita)."""
    for path in directory.glob("*.parquet"):
        if path.name != PARTITION_FILE:
            path.unlink()


def _sha256_file(path: Path) -> str:
    """`sha256` dos bytes do arquivo, em blocos (nunca o arquivo inteiro em RAM)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Validação de entrada (schema fechado)
# --------------------------------------------------------------------------- #


def _validated(
    rows: Iterable[Mapping[str, object]],
    *,
    table: str,
    columns: Sequence[str],
    spec: Mapping[str, _Column],
) -> list[dict[str, object]]:
    """Materializa e valida as linhas; qualquer divergência é `bad_data` com a linha."""
    expected = list(columns)
    wanted = set(expected)
    payload: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise _bad_row(table, f"linha {index}: esperado mapping, veio {type(row).__name__}")
        missing = [name for name in expected if name not in row]
        extra = sorted(str(key) for key in set(row) - wanted)
        if missing or extra:
            raise _bad_row(
                table,
                f"linha {index}: coluna(s) faltando={missing} extra={extra}",
                hint=_HINT_SCHEMA,
            )
        cleaned: dict[str, object] = {}
        for name in expected:
            cleaned[name] = _coerce(table, index, name, row[name], spec[name])
        _check_domain(table, index, cleaned)
        payload.append(cleaned)
    if not payload:
        raise _bad_row(
            table,
            "nenhuma linha para gravar",
            hint=(
                "partição vazia apagaria o dado bom: o vazio de uma data tem que ser "
                "ausência de linha na chamada, não uma escrita"
            ),
        )
    return payload


def _coerce(table: str, index: int, name: str, value: object, column: _Column) -> object:
    """Converte/valida um valor; `None` só nas colunas `|null` do contrato."""
    if value is None:
        if column.nullable:
            return None
        raise _bad_row(table, f"linha {index}: campo {name!r} é obrigatório (veio None)")
    if column.kind == "date":
        if not isinstance(value, date) or isinstance(value, datetime):
            raise _bad_row(table, f"linha {index}: campo {name!r} esperado date, veio {value!r}")
        return value
    if column.kind == "str":
        if not isinstance(value, str):
            raise _bad_row(table, f"linha {index}: campo {name!r} esperado str, veio {value!r}")
        return value
    if column.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _bad_row(table, f"linha {index}: campo {name!r} esperado int64, veio {value!r}")
        return value
    if column.kind == "float":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _bad_row(table, f"linha {index}: campo {name!r} esperado float, veio {value!r}")
        number = float(value)
        if not math.isfinite(number):
            raise _bad_row(
                table,
                f"linha {index}: campo {name!r} não finito ({value!r})",
                hint="NaN/inf em métrica é bug de cálculo: use None + flag",
            )
        return number
    if column.kind == "bool":
        if not isinstance(value, bool):
            raise _bad_row(table, f"linha {index}: campo {name!r} esperado bool, veio {value!r}")
        return value
    if column.kind == "str_list":
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise _bad_row(
                table,
                f"linha {index}: campo {name!r} esperado list[str], veio {value!r}",
                hint="flags é lista de strings (ordem alfabética, sem repetição)",
            )
        items = list(value)
        for item in items:
            if not isinstance(item, str):
                raise _bad_row(
                    table, f"linha {index}: campo {name!r} tem item não-string ({item!r})"
                )
        if items != sorted(set(items)):
            raise _bad_row(
                table,
                f"linha {index}: campo {name!r} fora de ordem ou com repetição ({items})",
                hint="contrato §4.3: flags sem repetição e em ordem alfabética",
            )
        return items
    raise _bad_row(  # pragma: no cover - tipo lógico sempre vem da spec
        table, f"linha {index}: campo {name!r} com tipo lógico desconhecido"
    )


def _check_domain(table: str, index: int, row: Mapping[str, object]) -> None:
    """Domínios fechados das colunas categóricas e invariantes numéricas."""
    if "side" in row and row["side"] not in {"C", "P"}:
        raise _bad_row(table, f"linha {index}: side {row['side']!r} fora de {{C, P}}")
    if "style" in row and row["style"] is not None and row["style"] not in {"A", "E"}:
        raise _bad_row(table, f"linha {index}: style {row['style']!r} fora de {{A, E, None}}")
    if "underlying_market" in row and row["underlying_market"] not in {"010", "020"}:
        raise _bad_row(
            table,
            f"linha {index}: underlying_market {row['underlying_market']!r} fora de {{010, 020}}",
        )
    if "signal_class" in row and row["signal_class"] not in SIGNAL_CLASSES:
        raise _bad_row(
            table,
            f"linha {index}: signal_class {row['signal_class']!r} fora de {list(SIGNAL_CLASSES)}",
        )
    for name in ("quote_factor", "model_version"):
        if name in row and int(row[name]) < 1:  # type: ignore[arg-type]
            raise _bad_row(table, f"linha {index}: {name} precisa ser ≥ 1 ({row[name]!r})")
    for name in ("strike", "t"):
        if name in row and float(row[name]) < 0:  # type: ignore[arg-type]
            raise _bad_row(table, f"linha {index}: {name} não pode ser negativo ({row[name]!r})")
    for name in ("spot", "forward_30d"):
        if name in row and row[name] is not None and float(row[name]) <= 0:  # type: ignore[arg-type]
            raise _bad_row(table, f"linha {index}: {name} precisa ser > 0 ({row[name]!r})")


def _bad_row(table: str, detail: str, *, hint: str = _HINT_SCHEMA) -> SourceFailure:
    """`bad_data` com a tabela, a linha e o campo no `message` (nunca schema divergente)."""
    return SourceFailure(SourceError(code="bad_data", message=f"{table}: {detail}", hint=hint))


__all__ = [
    "METRICS_SERIES_COLUMNS",
    "METRICS_SERIES_SCHEMA",
    "METRICS_SERIES_TABLE",
    "METRICS_UNDERLYING_COLUMNS",
    "METRICS_UNDERLYING_SCHEMA",
    "METRICS_UNDERLYING_TABLE",
    "SIGNAL_CLASSES",
    "metrics_series_path",
    "metrics_underlying_path",
    "write_metrics_series",
    "write_metrics_underlying",
]
