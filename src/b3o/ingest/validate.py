"""Validação coletiva do COTAHIST (F0.4).

Roda ANTES do parse (`prepare`: fetch → validate → parse → store). Coleta os
problemas em vez de parar no primeiro e devolve relatório; **fatal é só o que é
estrutural** (header, contagem do trailer, zip/I-O) — linha torta é registrada,
pulada na ingestão e contada (ADR-007).

Layout e offsets: `docs/contratos/onda-a.md` §3 (validados em 16.134 linhas de
19/11/2026, zero violações). Largura: 245 bytes em **toda** linha (00/01/99).
Trailer `99` carrega o total de linhas `01` em `l[32:42]`.

Leitura em streaming dentro do zip: o arquivo tem ~3,5 M linhas (P-I), nunca em memória.
"""

from __future__ import annotations

import hashlib
import logging
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO

from b3o.ingest.errors import SourceError

log = logging.getLogger(__name__)

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
    """Linha que o prepare pula e conta (ADR-007); `line_no` é 1-based no arquivo."""

    line_no: int
    kind: str  # "width" | "numeric" | "date" | "tipreg"
    excerpt: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Veredito do arquivo; `ok` é estrutural: header, contagem e nenhum erro de I/O (ADR-007).

    `bad_width`/`bad_numeric` são problemas por linha: aparecem nas contagens e não
    derrubam o `ok`; `errors` é só nível I/O/zip (zip ilegível, membro múltiplo).
    `bad_width_count`/`bad_numeric_count` são os TOTAIS sem capo (D2): as listas
    param em 20 para o relatório caber, a contagem nunca mente.
    """

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
    # Totais sem capo (D2). Defaults para não quebrar quem monta o relatório à mão.
    bad_width_count: int = 0
    bad_numeric_count: int = 0


def validate_cotahist(zip_path: Path) -> ValidationReport:
    """Valida o zip do COTAHIST e devolve o relatório (nunca levanta por linha ruim).

    Linha torta não é fatal: entra em `bad_width`/`bad_numeric` para o prepare pular
    e o health contar (ADR-007, spec §11). Fatal: zip/I-O, header e contagem do trailer.
    """
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
    trailer_conflict = False
    bad_width: list[BadLine] = []
    bad_numeric: list[BadLine] = []
    errors: list[SourceError] = []
    bad_width_count = 0
    bad_numeric_count = 0

    def add_width(line_no: int, line: str) -> None:
        """Conta SEMPRE; a lista para nas 20 primeiras (D2: a contagem não mente)."""
        nonlocal bad_width_count
        bad_width_count += 1
        if len(bad_width) < MAX_BAD_LINES:
            bad_width.append(BadLine(line_no=line_no, kind="width", excerpt=line[:80]))

    def add_problem(kind: str, line_no: int, excerpt: str) -> None:
        nonlocal bad_numeric_count
        bad_numeric_count += 1
        if len(bad_numeric) < MAX_BAD_LINES:
            bad_numeric.append(BadLine(line_no=line_no, kind=kind, excerpt=excerpt))

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
                # Dois terminadores: a contagem do arquivo deixou de ser confiável.
                trailer_conflict = True
                log.warning("%s: linha %d: trailer '99' duplicado", path.name, line_no)
            trailer_seen = True
            trailer_total = _trailer_total(line)
            if trailer_total is None:
                start, end = TRAILER_TOTAL_OFFSET
                log.warning(
                    "%s: linha %d: total do trailer ilegível em [%d:%d] = %r",
                    path.name,
                    line_no,
                    start,
                    end,
                    line[start:end],
                )
        elif tipreg != HEADER_TIPREG:
            add_problem("tipreg", line_no, f"tipreg={tipreg!r} {line[:60]}")

        if len(line) != LINE_WIDTH:
            add_width(line_no, line)
            continue

        if tipreg != DATA_TIPREG:
            continue

        bad_fields = _bad_numeric_fields(line)
        if bad_fields:
            # Uma linha entra uma vez só, mesmo com vários campos tortos.
            field = bad_fields[0]
            start, end = _FIELD_BOUNDS[field]
            add_problem("numeric", line_no, f"{field}=[{line[start:end]}] {line[:60]}")
            continue

        messages = _bad_date_messages(line)
        if messages:
            add_problem("date", line_no, f"{'; '.join(messages)} | {line[:60]}")

    omitted_width = bad_width_count - len(bad_width)
    omitted_numeric = bad_numeric_count - len(bad_numeric)
    if omitted_width or omitted_numeric:
        # O relatório é limitado a 20 por espécie; o total não pode sumir em silêncio.
        log.warning(
            "%s: %d linha(s) torta(s) além das %d registradas (largura=%d, linha=%d)",
            path.name,
            omitted_width + omitted_numeric,
            MAX_BAD_LINES,
            omitted_width,
            omitted_numeric,
        )

    if not trailer_seen:
        count_match: bool | None = None
    elif trailer_total is None or trailer_conflict:
        count_match = False
    else:
        count_match = trailer_total == line_count

    # ADR-007: fatal é estrutural; linha torta é registrada e pulada, não reprova o arquivo.
    ok = header_ok and count_match is True and not errors

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
        bad_width_count=bad_width_count,
        bad_numeric_count=bad_numeric_count,
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
