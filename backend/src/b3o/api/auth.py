"""Autenticação das escritas HTTP (F6): `X-App-Token` ↔ `APP_TOKEN`.

Leitura continua liberada na tailnet. Escrita exige token; sem token configurado
a falha é explícita (`app_token_missing`), nunca "autorizado por padrão".
"""

from __future__ import annotations

import hmac
import os

from fastapi import Request

from b3o.ingest.errors import SourceError, SourceFailure

APP_TOKEN_ENV = "APP_TOKEN"


def require_write_token(request: Request) -> None:
    """Valida o cabeçalho `X-App-Token`; comparação em tempo constante."""
    expected = os.environ.get(APP_TOKEN_ENV, "")
    if not expected:
        raise SourceFailure(
            SourceError(
                code="app_token_missing",
                message="APP_TOKEN não está configurado no servidor",
                hint="defina APP_TOKEN no .env do host e recrie o container app",
            )
        )
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
