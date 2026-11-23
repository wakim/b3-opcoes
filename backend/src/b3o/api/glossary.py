"""Verbetes do glossário (F5.2) — parser **puro**: texto entra, estrutura sai.

A fonte única é `docs/glossario.md`; nada de verbete duplicado em Python. Quem lê
o arquivo é a borda (`b3o.api.payloads`); aqui só há transformação de texto.

Regras do arquivo:
- `# Nível N — ...` troca o nível corrente;
- `## Título` abre um verbete; o corpo vai até o próximo `#`/`##`;
- `key` é o slug do título (minúsculas, sem acento, pontuação → `-`);
- o verbete "Taxa histórica do sinal" traz o marcador "Fora do produto"
  (ADR-023/024) e **não** é servido: o glossário do painel é descritivo.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

GLOSSARY_PATH_ENV = "B3_GLOSSARY_PATH"
"""Caminho do glossário por ambiente (fora do config.yaml, que é fechado)."""

DEFAULT_GLOSSARY_PATH = "/app/docs/glossario.md"


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    key: str
    title: str
    level: int
    body_md: str


def glossary_path() -> Path:
    return Path(os.environ.get(GLOSSARY_PATH_ENV, DEFAULT_GLOSSARY_PATH))


def slugify(title: str) -> str:
    """'IV ATM 30d' → 'iv-atm-30d'; 'Preço (spot / fechamento)' → 'preco-spot-fechamento'."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LEVEL = re.compile(r"^#\s+N[íi]vel\s+(\d+)\b", re.IGNORECASE)
_RULE = re.compile(r"\n?-{3,}\s*$")
_OUT_OF_PRODUCT = "fora do produto"


def parse_glossary(text: str) -> list[GlossaryEntry]:
    """Conteúdo Markdown → lista de verbetes com nível e corpo (sem I/O).

    Um h1 que não seja nível (título do arquivo, "As 5 armadilhas") apenas
    encerra o verbete corrente. h3+ dentro do corpo é preservado como texto.
    """
    level: int | None = None
    title: str | None = None
    body: list[str] = []
    entries: list[GlossaryEntry] = []

    def close() -> None:
        nonlocal title, body
        if title is not None and level is not None:
            content = _RULE.sub("", "\n".join(body).strip()).strip()
            if _OUT_OF_PRODUCT not in content[:400].lower():
                entries.append(
                    GlossaryEntry(
                        key=slugify(title),
                        title=title,
                        level=level,
                        body_md=content,
                    )
                )
        title, body = None, []

    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            marks, heading_title = heading.group(1), heading.group(2).strip()
            if len(marks) == 1:
                close()
                found = _LEVEL.match(line)
                level = int(found.group(1)) if found else None
                continue
            if len(marks) == 2:
                close()
                title = heading_title
                continue
        if title is not None:
            body.append(line)
    close()
    return entries


__all__ = [
    "DEFAULT_GLOSSARY_PATH",
    "GLOSSARY_PATH_ENV",
    "GlossaryEntry",
    "glossary_path",
    "parse_glossary",
    "slugify",
]
