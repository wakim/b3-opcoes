"""Teste de arquitetura (provisório, ADR-005) — entra cedo porque agora é barato.

Regras:
1. SQL/duckdb só em src/b3o/store/ e src/b3o/query/service.py — a camada de consulta é única.
2. date.today() nunca em src/ — a data de referência vem do arquivo (P-C).
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

# Padrões que só podem aparecer na camada autorizada.
SQL_PATTERN = re.compile(r"duckdb\.connect|\.execute\(")

ALLOWED_PREFIXES = ("b3o/store/",)
ALLOWED_FILES = ("b3o/query/service.py",)


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


def test_nenhum_date_today_em_src() -> None:
    offenders: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        if "date.today()" in text or "datetime.now()" in text:
            offenders.append(path.relative_to(SRC).as_posix())
    assert not offenders, (
        f"A data de referência vem do arquivo, nunca do relógio (P-C): {offenders}"
    )
