"""Erro de fonte no formato do projeto: {code, message, hint}.

Regra 5 do AGENTS.md: falha é explícita — código, mensagem e dica de ação.
Fonte indisponível nunca vira exceção que mata o processo e nunca vira zero
silencioso: vira SourceError (ou null + flag, no dado derivado).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceError:
    """Falha de fonte com código, mensagem e dica — nunca exceção silenciosa."""

    code: str
    message: str
    hint: str

    def to_dict(self) -> dict[str, str]:
        """Serialização para health/log/CLI."""
        return {"code": self.code, "message": self.message, "hint": self.hint}


class SourceFailure(Exception):
    """Exceção que carrega um SourceError (code/message/hint) até a borda.

    Use quando uma falha de fonte precisa interromper o fluxo atual SEM perder
    a estrutura {code, message, hint}. Quem captura decide: log + health,
    resposta HTTP de erro, ou skip com contagem. Nunca capture e silencie.
    """

    def __init__(self, error: SourceError) -> None:
        self.error = error
        super().__init__(f"{error.code}: {error.message} — {error.hint}")

    @property
    def code(self) -> str:
        return self.error.code

    @property
    def message(self) -> str:
        return self.error.message

    @property
    def hint(self) -> str:
        return self.error.hint

    def to_dict(self) -> dict[str, str]:
        return self.error.to_dict()
