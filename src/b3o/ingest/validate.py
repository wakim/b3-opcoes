"""Validação coletiva do COTAHIST (F0.4).

Roda ANTES do parse (`prepare`: fetch → validate → parse → store). Um arquivo torto
não entra no lake e o erro não pode ser silencioso, mas também não pode parar na
primeira linha ruim: **coleta** (primeiras 20 de cada espécie) e devolve relatório;
`ok` é quem decide o exit code.

Layout e offsets: `docs/contratos/onda-a.md` §3 (validados em 16.134 linhas de
19/11/2026, zero violações). Largura: 245 bytes em **toda** linha (00/01/99).
Trailer `99` carrega o total de linhas `01` em `l[32:42]`.

Leitura em streaming dentro do zip: o arquivo tem ~3,5 M linhas (P-I), nunca em memória.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO

from b3o.ingest.errors import SourceError

LINE_WIDTH = 245
MAX_BAD_LINES = 20

HEADER_TIPREG = "00"
DATA_TIPREG = "01"
TRAILER_TIPREG = "99"
TRAILER_TOTAL_OFFSET = (32, 42)
UNDATED_MATURITY = "99991231"

# (campo, início, fim) — todos dígitos zero-preenchidos em toda linha "01".
NUMERIC_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("open", 56, 69),
    ("high", 69, 82),
    ("low", 82, 95),
    ("avg", 95, 108),
    ("close", 108, 121),
    ("bid", 121, 134),
    ("ask", 134, 147),
    ("trades", 147, 152),
    ("qty", 152, 170),
    ("volume", 170, 188),
    ("strike", 188, 201),
    ("quote_factor", 210, 217),
    ("strike_pts", 217, 230),
)

_FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    name: (start, end) for name, start, end in NUMERIC_FIELDS
}

DATE_OFFSET = (2, 10)
MATURITY_OFFSET = (202, 210)

_HINT_RESAVE = "baixe o arquivo de novo; se persistir, a fonte publicou um layout novo"
_CHUNK_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class BadLine:
    """Uma linha com problema; `line_no` é 1-based no arquivo."""

    line_no: int
    kind: str  # "width" | "numeric" | "date" | "tipreg"
    excerpt: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Veredito do arquivo inteiro; `ok` só é True com header, contagem e campos limpos."""

    ok: bool
    zip_path: Path
    sha256: str
    line_count: int
    header_ok: bool
    trailer_total: int | None
    count_match: bool | None
    bad_width: tuple[BadLine, ...]
    bad_numeric: tuple[BadLine, ...]
    errors: tuple[SourceError, ...]


def validate_cotahist(zip_path: Path) -> ValidationReport:
    """Valida o zip do COTAHIST e devolve o relatório (nunca levanta por linha ruim)."""
    path = Path(zip_path)
    try:
        sha256 = sha256_file(path)
    except OSError as exc:
        return _failed(
            path,
            sha256="",
            message=f"não foi possível ler {path}: {exc}",
            hint="confira o caminho e o volume de dados do container",
        )

    try:
        with zipfile.ZipFile(path) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            if len(members) != 1:
                names = ", ".join(info.filename for info in members[:5])
                return _failed(
                    path,
                    sha256=sha256,
                    message=(
                        f"zip deve ter exatamente 1 membro, encontrou {len(members)}: {names}"
                    ),
                    hint=_HINT_RESAVE,
                )
            with archive.open(members[0]) as stream:
                return _scan(path, sha256, stream)
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        NotImplementedError,
        RuntimeError,
        EOFError,
        OSError,
    ) as exc:
        return _failed(
            path,
            sha256=sha256,
            message=f"zip ilegível em {path}: {type(exc).__name__}: {exc}",
            hint=_HINT_RESAVE,
        )


def _scan(path: Path, sha256: str, stream: BinaryIO) -> ValidationReport:
    line_count = 0
    header_ok = False
    trailer_total: int | None = None
    trailer_seen = False
    bad_width: list[BadLine] = []
    bad_numeric: list[BadLine] = []
    errors: list[SourceError] = []
    suppressed = 0

    def add_error(message: str) -> None:
        nonlocal suppressed
        if len(errors) < MAX_BAD_LINES:
            errors.append(SourceError(code="bad_data", message=message, hint=_HINT_RESAVE))
        else:
            suppressed += 1

    line_no = 0
    for raw in stream:  # binário: quebra em \n e nunca carrega o arquivo inteiro
        line_no += 1
        line = raw.rstrip(b"\r\n").decode("latin-1")
        tipreg = line[:2]

        if line_no == 1 and tipreg == HEADER_TIPREG:
            header_ok = True

        if tipreg == DATA_TIPREG:
            # Conta mesmo linha torta: o trailer conta registros, não registros válidos.
            line_count += 1
        elif tipreg == TRAILER_TIPREG:
            if trailer_seen:
                add_error(f"linha {line_no}: trailer '99' duplicado")
            trailer_seen = True
            trailer_total = _trailer_total(line)
            if trailer_total is None:
                start, end = TRAILER_TOTAL_OFFSET
                add_error(
                    f"linha {line_no}: total do trailer ilegível em "
                    f"[{start}:{end}] = {line[start:end]!r}"
                )
        elif tipreg != HEADER_TIPREG:
            add_error(f"linha {line_no}: tipreg desconhecido {tipreg!r}")

        if len(line) != LINE_WIDTH:
            if len(bad_width) < MAX_BAD_LINES:
                bad_width.append(BadLine(line_no=line_no, kind="width", excerpt=line[:80]))
            continue

        if tipreg != DATA_TIPREG:
            continue

        bad_fields = _bad_numeric_fields(line)
        if bad_fields and len(bad_numeric) < MAX_BAD_LINES:
            field = bad_fields[0]  # uma linha entra uma vez só, mesmo com vários campos tortos
            start, end = _FIELD_BOUNDS[field]
            bad_numeric.append(
                BadLine(
                    line_no=line_no,
                    kind="numeric",
                    excerpt=f"{field}=[{line[start:end]}] {line[:80]}",
                )
            )

        for message in _bad_date_messages(line):
            add_error(f"linha {line_no}: {message}")

    if suppressed:
        errors.append(
            SourceError(
                code="bad_data",
                message=f"{suppressed} problema(s) adicional(is) omitido(s) do relatório",
                hint=_HINT_RESAVE,
            )
        )

    if not trailer_seen:
        count_match: bool | None = None
    elif trailer_total is None:
        count_match = False
    else:
        count_match = trailer_total == line_count

    ok = (
        header_ok
        and count_match is True
        and not bad_width
        and not bad_numeric
        and not errors
    )

    return ValidationReport(
        ok=ok,
        zip_path=path,
        sha256=sha256,
        line_count=line_count,
        header_ok=header_ok,
        trailer_total=trailer_total,
        count_match=count_match,
        bad_width=tuple(bad_width),
        bad_numeric=tuple(bad_numeric),
        errors=tuple(errors),
    )


def sha256_file(path: Path) -> str:
    """sha256 do arquivo inteiro, em streaming (o zip de 2026 tem dezenas de MB)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trailer_total(line: str) -> int | None:
    start, end = TRAILER_TOTAL_OFFSET
    text = line[start:end].strip()
    return int(text) if text.isdigit() else None


def _bad_numeric_fields(line: str) -> list[str]:
    """Campos numéricos de uma linha "01" que não são dígitos (preços/quantidades inteiros)."""
    bad: list[str] = []
    for name, start, end in NUMERIC_FIELDS:
        text = line[start:end].strip()
        if not text.isdigit():
            bad.append(name)
    return bad


def _bad_date_messages(line: str) -> list[str]:
    """Problemas de data de uma linha "01": `date` válida e `maturity` válida ou 99991231."""
    messages: list[str] = []
    start, end = DATE_OFFSET
    if not _valid_yyyymmdd(line[start:end]):
        messages.append(f"date inválida {line[start:end]!r}")
    start, end = MATURITY_OFFSET
    maturity = line[start:end]
    if maturity != UNDATED_MATURITY and not _valid_yyyymmdd(maturity):
        messages.append(f"maturity inválida {maturity!r}")
    return messages


def _valid_yyyymmdd(text: str) -> bool:
    if len(text) != 8 or not text.isdigit():
        return False
    try:
        date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return False
    return True


def _failed(
    path: Path, *, sha256: str, message: str, hint: str
) -> ValidationReport:
    """Relatório de falha de I/O: sem os números, mas com o motivo explícito."""
    return ValidationReport(
        ok=False,
        zip_path=path,
        sha256=sha256,
        line_count=0,
        header_ok=False,
        trailer_total=None,
        count_match=None,
        bad_width=(),
        bad_numeric=(),
        errors=(SourceError(code="bad_data", message=message, hint=hint),),
    )


_FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    name: (start, end) for name, start, end in NUMERIC_FIELDS
}
