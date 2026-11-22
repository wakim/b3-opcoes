"""`ingest_state` do lake (F0.5): o espelho do que entrou e do que foi pulado.

Contrato: `docs/contratos/onda-b.md` §2 (spec §5.5, ADR-007, P-C). O arquivo
`data_dir/ingest_state.json` responde, sem abrir os Parquets, a quatro perguntas do `health`:

- de qual arquivo-fonte veio o dado (`etag`, `last_modified`, `sha256`, `trailer_total`);
- até que sessão o lake está cheio (`last_session`) e quantas linhas há por data
  (`counts_by_date`);
- quantas linhas tortas foram **puladas** na ingestão (`skipped_lines`; ADR-007: o que não
  pode ser silêncio vira contagem explícita);
- com que versão de modelo o dado foi derivado (`model_version`, hoje 1).

Regras:

- **sem relógio**: `updated_at` é parâmetro de quem grava (P-C); este módulo nunca chama
  `date.today`/`datetime.now` (o teste de arquitetura garante);
- **escrita atômica**: `data_dir/tmp/ingest_state-*.json` e publicação via
  `fsutil.replace_file` (no bind do Windows, `os.replace` sobre arquivo existente dá EACCES —
  medido 22/11/2026); um crash no meio não deixa JSON pela metade;
- **update incremental**: `update_ingest_state` lê-modifica-grava e só toca os campos
  informados; o resto do estado é preservado (reparsar 2026 não apaga 2025);
- **falha explícita**: JSON corrompido, chave desconhecida, tipo errado, `updated_at` sem
  offset ou contagem negativa → `SourceFailure` com `SourceError` {code, message, hint}.

Formato do JSON (chaves ordenadas, datas ISO):

```json
{
  "counts_by_date": {"2026-11-19": 16134},
  "etag": "\"hash:0\"",
  "last_modified": "Thu, 20 Nov 2026 08:12:00 GMT",
  "last_session": "2026-11-19",
  "model_version": 1,
  "sha256": "…",
  "skipped_lines": {"numeric": 0, "width": 1},
  "trailer_total": 16134,
  "updated_at": "2026-11-22T18:30:00+00:00"
}
```
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

from b3o.fsutil import replace_file
from b3o.ingest.errors import SourceError, SourceFailure
from b3o.ingest.validate import ValidationReport

INGEST_STATE_FILE = "ingest_state.json"
"""Nome do arquivo de estado na raiz do lake (`data_dir/ingest_state.json`)."""

TMP_DIR = "tmp"
"""Diretório de staging; fica no mesmo filesystem para o `os.replace` ser atômico."""

MODEL_VERSION = 1
"""Versão do cálculo que produz o dado derivado; sobe a cada mudança de fórmula (AGENTS.md)."""

_STATE_KEYS = frozenset(
    {
        "etag",
        "last_modified",
        "sha256",
        "trailer_total",
        "last_session",
        "counts_by_date",
        "model_version",
        "skipped_lines",
        "updated_at",
    }
)

_HINT_DIR = "confira B3_DATA_DIR (no container é /data) e se o caminho é um diretório gravável"
_HINT_STATE = "ingest_state.json ilegível: rode `b3o prepare` de novo (o Parquet não depende dele)"


class _Unset:
    """Marcador de 'não mexa neste campo' — `None` é valor legítimo (limpa o campo)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Unset()


@dataclass(frozen=True, slots=True)
class SkippedLines:
    """Linhas que o prepare pulou e contou (ADR-007): largura errada e numérico torto."""

    width: int = 0
    numeric: int = 0

    def to_dict(self) -> dict[str, int]:
        """Serialização para o JSON (`{width, numeric}`)."""
        return {"numeric": self.numeric, "width": self.width}

    @classmethod
    def from_mapping(cls, raw: object, *, where: str) -> SkippedLines:
        """Lê `{width, numeric}` do JSON; campo ausente vira 0 e chave estranha falha."""
        if not isinstance(raw, Mapping):
            raise _bad(where, "skipped_lines", raw, "objeto {width, numeric}")
        unknown = set(raw) - {"width", "numeric"}
        if unknown:
            raise _bad(where, "skipped_lines", sorted(unknown), "só width e numeric")
        width = _count(raw.get("width", 0), where, "skipped_lines.width")
        numeric = _count(raw.get("numeric", 0), where, "skipped_lines.numeric")
        return cls(width=width, numeric=numeric)


@dataclass(frozen=True, slots=True)
class IngestState:
    """Retrato do último `prepare`: fonte, cobertura, contagens e linhas puladas.

    `updated_at` é `None` só no estado default (lake ainda sem `ingest_state.json`);
    `save_ingest_state` recusa gravar estado sem timestamp, e o timestamp precisa ter offset
    (P-D: borda ISO com offset; nada de hora local solta).
    """

    etag: str | None = None
    last_modified: str | None = None
    sha256: str | None = None
    trailer_total: int | None = None
    last_session: date | None = None
    counts_by_date: dict[date, int] = field(default_factory=dict)
    model_version: int = MODEL_VERSION
    skipped_lines: SkippedLines = field(default_factory=SkippedLines)
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialização do estado (datas ISO, chaves `counts_by_date` ordenadas)."""
        return {
            "etag": self.etag,
            "last_modified": self.last_modified,
            "sha256": self.sha256,
            "trailer_total": self.trailer_total,
            "last_session": self.last_session.isoformat() if self.last_session else None,
            "counts_by_date": {
                day.isoformat(): count for day, count in sorted(self.counts_by_date.items())
            },
            "model_version": self.model_version,
            "skipped_lines": self.skipped_lines.to_dict(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def ingest_state_path(data_dir: Path) -> Path:
    """Caminho do estado: `data_dir/ingest_state.json`."""
    return Path(data_dir) / INGEST_STATE_FILE


def load_ingest_state(data_dir: Path) -> IngestState:
    """Lê o estado do lake; sem arquivo devolve o estado default (primeiro `prepare`).

    JSON corrompido ou fora do schema **não** vira default silencioso: `SourceFailure`.
    """
    path = ingest_state_path(data_dir)
    if Path(data_dir).exists() and not Path(data_dir).is_dir():
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"data_dir não é diretório: {data_dir}",
                hint=_HINT_DIR,
            )
        )
    if not path.is_file():
        return IngestState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"não foi possível ler {path}: {exc}",
                hint=_HINT_STATE,
            )
        ) from exc
    return _state_from_json(raw, path)


def save_ingest_state(data_dir: Path, state: IngestState) -> Path:
    """Grava o estado de forma atômica (`tmp/` → `os.replace`) e devolve o caminho final."""
    root = _ensure_dir(data_dir)
    path = ingest_state_path(root)
    _check_state(state, path)
    text = json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    tmp_dir = root / TMP_DIR
    tmp_path = tmp_dir / f"ingest_state-{uuid.uuid4().hex[:12]}.json"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        # bind do Windows: `os.replace` sobre arquivo existente dá EACCES → `replace_file`
        replace_file(tmp_path, path)
    except OSError as exc:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"não foi possível gravar {path}: {exc}",
                hint=_HINT_DIR,
            )
        ) from exc
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:  # pragma: no cover - limpeza best-effort
                pass
    return path


def update_ingest_state(
    data_dir: Path,
    *,
    updated_at: datetime,
    etag: str | None | _Unset = _UNSET,
    last_modified: str | None | _Unset = _UNSET,
    sha256: str | None | _Unset = _UNSET,
    trailer_total: int | None | _Unset = _UNSET,
    last_session: date | None | _Unset = _UNSET,
    counts_by_date: Mapping[date, int] | _Unset = _UNSET,
    skipped_lines: SkippedLines | Mapping[str, int] | _Unset = _UNSET,
    model_version: int | _Unset = _UNSET,
    replace_counts: bool = False,
) -> IngestState:
    """Lê, aplica só os campos informados e grava — o resto do estado fica como estava.

    `counts_by_date` **mescla** por data (reparsar 2026 hoje não perde as contagens de 2025);
    `replace_counts=True` troca o mapa inteiro (use quando o ano corrente é reconstruído e as
    datas saíram do arquivo). `skipped_lines` aceita `SkippedLines` (substitui) ou um
    `Mapping` parcial (mescla campo a campo com o valor atual).

    Campos não passados continuam iguais; `None` explícito limpa (ex.: `etag=None`).
    """
    current = load_ingest_state(data_dir)
    changes: dict[str, object] = {}
    if not isinstance(etag, _Unset):
        changes["etag"] = etag
    if not isinstance(last_modified, _Unset):
        changes["last_modified"] = last_modified
    if not isinstance(sha256, _Unset):
        changes["sha256"] = sha256
    if not isinstance(trailer_total, _Unset):
        changes["trailer_total"] = trailer_total
    if not isinstance(last_session, _Unset):
        changes["last_session"] = last_session
    if not isinstance(model_version, _Unset):
        changes["model_version"] = model_version

    if not isinstance(counts_by_date, _Unset) and not isinstance(counts_by_date, Mapping):
        raise _bad("counts_by_date", "valor", counts_by_date, "Mapping[date, int]")
    if not isinstance(counts_by_date, _Unset):
        counts = {} if replace_counts else dict(current.counts_by_date)
        for day, count in counts_by_date.items():
            if not isinstance(day, date) or isinstance(day, datetime):
                raise _bad("counts_by_date", "chave", day, "date (não datetime)")
            counts[day] = _count(count, "counts_by_date", day.isoformat())
        changes["counts_by_date"] = counts

    if not isinstance(skipped_lines, _Unset):
        if isinstance(skipped_lines, Mapping):
            unknown = set(skipped_lines) - {"width", "numeric"}
            if unknown:
                raise _bad("skipped_lines", "chave", sorted(unknown), "só width e numeric")
            changes["skipped_lines"] = SkippedLines(
                width=_count(
                    skipped_lines.get("width", current.skipped_lines.width),
                    "skipped_lines",
                    "width",
                ),
                numeric=_count(
                    skipped_lines.get("numeric", current.skipped_lines.numeric),
                    "skipped_lines",
                    "numeric",
                ),
            )
        elif isinstance(skipped_lines, SkippedLines):
            changes["skipped_lines"] = skipped_lines
        else:
            raise _bad("skipped_lines", "valor", skipped_lines, "SkippedLines ou Mapping")

    state = replace(current, updated_at=updated_at, **changes)  # type: ignore[arg-type]
    save_ingest_state(data_dir, state)
    return state


def skipped_lines_from_report(report: ValidationReport) -> SkippedLines:
    """Converte o `ValidationReport` da Onda A nas contagens de linhas puladas (ADR-007)."""
    return SkippedLines(width=len(report.bad_width), numeric=len(report.bad_numeric))


def _state_from_json(raw: object, path: Path) -> IngestState:
    """Valida e converte o JSON do estado; qualquer divergência de schema falha explícita."""
    if not isinstance(raw, dict):
        raise _bad(path.name, "raiz", type(raw).__name__, "objeto JSON")
    unknown = set(raw) - _STATE_KEYS
    if unknown:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"chaves desconhecidas em {path}: {sorted(unknown)}",
                hint=(
                    "schema do ingest_state.json mudou: atualize o leitor "
                    "ou rode `b3o prepare` para regravar o estado"
                ),
            )
        )

    counts_raw = raw.get("counts_by_date", {})
    if not isinstance(counts_raw, Mapping):
        raise _bad(path.name, "counts_by_date", counts_raw, "objeto {YYYY-MM-DD: n}")
    counts_by_date: dict[date, int] = {}
    for key, value in counts_raw.items():
        day = _iso_date(key, path.name, "counts_by_date")
        counts_by_date[day] = _count(value, path.name, f"counts_by_date.{key}")

    model_version = _count(raw.get("model_version", MODEL_VERSION), path.name, "model_version")
    if model_version < 1:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"model_version inválido em {path}: {model_version}",
                hint="model_version começa em 1 e sobe a cada mudança de fórmula (AGENTS.md)",
            )
        )

    trailer_total = _opt_count(raw.get("trailer_total"), path.name, "trailer_total")
    return IngestState(
        etag=_opt_str(raw.get("etag"), path.name, "etag"),
        last_modified=_opt_str(raw.get("last_modified"), path.name, "last_modified"),
        sha256=_opt_str(raw.get("sha256"), path.name, "sha256"),
        trailer_total=trailer_total,
        last_session=_opt_iso_date(raw.get("last_session"), path.name, "last_session"),
        counts_by_date=counts_by_date,
        model_version=model_version,
        skipped_lines=SkippedLines.from_mapping(
            raw.get("skipped_lines", {}), where=path.name
        ),
        updated_at=_opt_timestamp(raw.get("updated_at"), path.name),
    )


def _check_state(state: IngestState, path: Path) -> None:
    """Invariantes antes de gravar: timestamp com offset, versão ≥ 1, contagens ≥ 0."""
    if state.updated_at is None:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"estado sem updated_at: {path}",
                hint="updated_at vem por parâmetro (nunca do relógio do módulo, P-C)",
            )
        )
    if state.updated_at.tzinfo is None or state.updated_at.utcoffset() is None:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"updated_at sem offset: {state.updated_at.isoformat()}",
                hint="timestamp de borda é ISO com offset (P-D); passe datetime com tzinfo",
            )
        )
    if state.model_version < 1:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"model_version inválido: {state.model_version}",
                hint="model_version começa em 1 e sobe a cada mudança de fórmula (AGENTS.md)",
            )
        )
    for name in ("etag", "last_modified", "sha256"):
        _opt_str(getattr(state, name), path.name, name)
    if state.last_session is not None and (
        not isinstance(state.last_session, date) or isinstance(state.last_session, datetime)
    ):
        raise _bad(path.name, "last_session", state.last_session, "date ou None")
    for day, count in state.counts_by_date.items():
        _count(count, path.name, f"counts_by_date.{day.isoformat()}")
    _count(state.skipped_lines.width, path.name, "skipped_lines.width")
    _count(state.skipped_lines.numeric, path.name, "skipped_lines.numeric")
    if state.trailer_total is not None:
        _count(state.trailer_total, path.name, "trailer_total")


def _bad(where: str, field_name: str, value: object, expected: str) -> SourceFailure:
    """Erro de schema do estado, com campo, valor e o que era esperado."""
    return SourceFailure(
        SourceError(
            code="bad_data",
            message=f"{where}: {field_name} inválido ({value!r}), esperado {expected}",
            hint=_HINT_STATE,
        )
    )


def _count(value: object, where: str, field_name: str) -> int:
    """Contagem inteira não negativa (bool não conta: `True` não é 1 neste contexto)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad(where, field_name, value, "inteiro")
    if value < 0:
        raise _bad(where, field_name, value, "inteiro ≥ 0")
    return value


def _opt_count(value: object, where: str, field_name: str) -> int | None:
    """Contagem inteira ou `None`."""
    if value is None:
        return None
    return _count(value, where, field_name)


def _opt_str(value: object, where: str, field_name: str) -> str | None:
    """String ou `None`."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _bad(where, field_name, value, "string ou null")
    return value


def _iso_date(value: object, where: str, field_name: str) -> date:
    """Data ISO (`YYYY-MM-DD`) vinda do JSON."""
    if not isinstance(value, str):
        raise _bad(where, field_name, value, "data ISO YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _bad(where, field_name, value, "data ISO YYYY-MM-DD") from exc


def _opt_iso_date(value: object, where: str, field_name: str) -> date | None:
    """Data ISO ou `None`."""
    if value is None:
        return None
    return _iso_date(value, where, field_name)


def _opt_timestamp(value: object, where: str) -> datetime | None:
    """Timestamp ISO com offset vindo do JSON; sem offset é erro (P-D)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _bad(where, "updated_at", value, "timestamp ISO com offset")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise _bad(where, "updated_at", value, "timestamp ISO com offset") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"{where}: updated_at sem offset ({value})",
                hint="timestamp de borda é ISO com offset (P-D); grave com tzinfo",
            )
        )
    return stamp


def _ensure_dir(data_dir: Path) -> Path:
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
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"não foi possível preparar {root}: {exc}",
                hint=_HINT_DIR,
            )
        ) from exc
    return root


__all__ = [
    "INGEST_STATE_FILE",
    "MODEL_VERSION",
    "IngestState",
    "SkippedLines",
    "ingest_state_path",
    "load_ingest_state",
    "save_ingest_state",
    "skipped_lines_from_report",
    "update_ingest_state",
]
