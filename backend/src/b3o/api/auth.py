"""Autenticação das escritas HTTP (F6): `X-App-Token` ↔ `APP_TOKEN` (ADR-044).

O token é **opcional**: com `APP_TOKEN` vazio ou ausente, as escritas ficam
abertas e o controle de acesso é da rede (porta em 127.0.0.1 + `tailscale serve`
só na tailnet). Com `APP_TOKEN` definido, a escrita exige o cabeçalho igual.
O estado é publicado em `/api/health` (`writes_require_token`) para o PWA
mostrar ou esconder o pedido de token.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Request

from b3o.ingest.errors import SourceError, SourceFailure

APP_TOKEN_ENV = "APP_TOKEN"


def write_token_required() -> bool:
    """`True` quando o servidor tem `APP_TOKEN` configurado."""
    return bool(os.environ.get(APP_TOKEN_ENV, "").strip())


def require_write_token(request: Request) -> None:
    """Valida o cabeçalho `X-App-Token` quando há token; comparação em tempo constante."""
    expected = os.environ.get(APP_TOKEN_ENV, "").strip()
    if not expected:
        return
    provided = request.headers.get("X-App-Token", "")
    if not provided or not hmac.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    ):
        raise SourceFailure(
            SourceError(
                code="unauthorized",
                message="token de escrita inválido ou ausente",
                hint="informe X-App-Token com o APP_TOKEN configurado no servidor",
            )
        )
