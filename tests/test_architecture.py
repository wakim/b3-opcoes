"""Teste de arquitetura (provisório, ADR-005) — entra cedo porque agora é barato.

Regras:
1. SQL/duckdb só em src/b3o/store/ e src/b3o/query/service.py — a camada de consulta é única.
2. Relógio nunca em src/ (P-C: a data de referência vem do arquivo) — com UMA
   exceção documentada (ADR-010): carimbos de download (`downloaded_at`) em
   b3o/ingest/fetch.py e b3o/ingest/earnings.py, marcados no código com
   "carimbo de download". A data de REFERÊNCIA nunca sai do relógio.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

# Padrões que só podem aparecer na camada autorizada.
SQL_PATTERN = re.compile(r"duckdb\.connect|\.execute\(")

# Relógio: pega now()/today() COM ou SEM argumentos (datetime.now(_tz) já escapa
# do guarda antigo que só casava o literal sem args).
CLOCK_PATTERN = re.compile(r"\b(?:datetime\.)?(?:now|today)\s*\(")

ALLOWED_PREFIXES = ("b3o/store/",)
ALLOWED_FILES = ("b3o/query/service.py",)

# ADR-010: carimbos de borda (downloaded_at da meta, updated_at do state e agenda
# do watch na CLI) — não são data de referência.
CLOCK_ALLOWED_FILES = ("b3o/ingest/fetch.py", "b3o/ingest/earnings.py", "b3o/cli.py")
CLOCK_MARKER = "carimbo de download"


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_sql_so_na_camada_autorizada() -> None:
    offenders: list[str] = []
    for path in _python_files():
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED_FILES or rel.startswith(ALLOWED_PREFIXES):
            continue
        text = path.read_text(encoding="utf-8")
        if SQL_PATTERN.search(text):
            offenders.append(rel)
    assert not offenders, (
        f"SQL fora da camada de consulta (store/ e query/service.py): {offenders}"
    )


def test_nenhum_relogio_em_src() -> None:
    offenders: list[str] = []
    for path in _python_files():
        rel = path.relative_to(SRC).as_posix()
        if rel in CLOCK_ALLOWED_FILES:
            # exceção documentada: cada chamada precisa do marcador (ADR-010)
            text = path.read_text(encoding="utf-8")
            for m in CLOCK_PATTERN.finditer(text):
                window = text[max(0, m.start() - 200) : m.end() + 200]
                assert CLOCK_MARKER in window, (
                    f"{rel}: chamada de relógio sem o marcador '{CLOCK_MARKER}' "
                    "(exceção ADR-010 exige o comentário na mesma janela)"
                )
            continue
        text = path.read_text(encoding="utf-8")
        if CLOCK_PATTERN.search(text):
            offenders.append(rel)
    assert not offenders, (
        f"A data de referência vem do arquivo, nunca do relógio (P-C/ADR-010): {offenders}"
    )

