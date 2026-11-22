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
