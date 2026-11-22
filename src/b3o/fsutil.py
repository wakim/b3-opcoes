"""Utilitários de filesystem para o bind mount do Windows (medido 22/11/2026).

Armadilha: dentro do container, no bind `./data:/data`, `os.replace` sobre um
arquivo que JÁ EXISTE falha com `PermissionError` (EACCES) — rename para nome
novo funciona. Quem reescreve partição/meta no segundo `prepare` bate nisso.

`replace_file(src, dst)`: atômico quando o SO colabora (dst não existe); quando
dst existe e o replace falha com EACCES, apaga dst e renomeia — a janela sem
arquivo é documentada e aceita (ordem de segundos no fim de um prepare).
"""

from __future__ import annotations

import os
from pathlib import Path


def replace_file(src: Path | str, dst: Path | str) -> None:
    """Move `src` para `dst` de forma tão atômica quanto o bind permitir.

    - dst não existe: `os.replace` puro (atômico).
    - dst existe e o SO recusa (EACCES no bind do Windows): unlink dst +
      rename. A janela sem arquivo é aceita e documentada (AGENTS.md).
    - Qualquer outro erro de SO: propaga (não silencia).
    """
    src_p, dst_p = Path(src), Path(dst)
    try:
        os.replace(src_p, dst_p)
        return
    except PermissionError:
        # bind do Windows: dst já existe e o replace é recusado
        dst_p.unlink(missing_ok=True)
        os.replace(src_p, dst_p)


def write_json_atomic(path: Path | str, text: str) -> None:
    """Escreve `text` em `path` via tmp + replace_file (o caminho do ingest_state)."""
    path_p = Path(path)
    tmp = path_p.with_name(path_p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    replace_file(tmp, path_p)
