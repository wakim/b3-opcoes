"""Servidor MCP do b3o (F3): Streamable HTTP em `/mcp`, sobre a mesma `QueryService`.

As tools **transportam** o que a `QueryService` devolve — nenhum número nasce
aqui. Cada descrição diz "use quando / não use para", e nenhuma usa vocabulário
preditivo (ADR-023/024: o painel é descritivo, sem classe de sinal, nota ou
previsão).

Erro de dado vira `{"error": {code, message, hint}}` como conteúdo da tool
(nunca traceback).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from b3o.api import payloads
from b3o.api.serialize import (
    DEFAULT_MAX_ROWS,
    error_payload,
    parse_delta_range,
    parse_liquidity,
)
from b3o.api.state import get_service
from b3o.ingest.errors import SourceFailure

log = logging.getLogger(__name__)

mcp = MCPServer(
    name="b3o",
    title="Opções B3 — painel descritivo (EOD)",
    version="0.1.0",
    instructions=(
        "Leitura descritiva do mercado de opções da B3 em dado EOD (fechamento), "
        "calculado em Python. Nenhuma tool devolve preço vivo, classe de sinal, "
        "nota ou previsão: o sinal de VRP foi testado e não passou (ADR-023). "
        "Responda citando os campos devolvidos, com a sessão e a unidade."
    ),
)

TOOL_HEALTH = (
    "Saúde do lake EOD: última sessão, idade, contagens, fontes e versão do cálculo. "
    "Use quando precisar saber qual é a sessão de referência ou se alguma fonte falta. "
    "Não use para preço: não existe dado intraday."
)
TOOL_SCREEN = (
    "Painel descritivo de uma sessão: uma linha por papel do universo líquido, com IV ATM "
    "30d, RV, VRP/z, skew, liquidez, próximo resultado e flags; papel sem dado tem motivo "
    "explícito. Use quando quiser ler o que o dado diz no dia para o universo inteiro. "
    "Não use para prever retorno: não há classe de sinal nem nota; a ordem é alfabética e "
    "o recorte de linhas não indica preferência."
)
TOOL_UNDERLYING = (
    "Leitura de um papel: resumo da sessão, estrutura a termo por vencimento, smile de um "
    "vencimento e histórico recente (IV ATM 30d, RV, VRP/z, skew, forward por vencimento, "
    "liquidez, próximo resultado). Use quando precisar dos números de um papel específico. "
    "Não use para a cadeia completa (use chain), para uma série (use series) nem para o "
    "calendário e ajustes de strike (use events)."
)
TOOL_CHAIN = (
    "Cadeia de opções de um papel numa sessão: uma linha por série, com lado, estilo, "
    "strike, vencimento, preço, IV, gregas, spread e a marca de liquidez da regra §6.9. "
    "Use quando precisar ver strikes e vencimentos disponíveis. Não use para histórico "
    "(use series) nem para o resumo do papel (use underlying)."
)
TOOL_SERIES = (
    "Identidade, última linha e histórico de uma série (ticker + vencimento): preço, IV, "
    "delta e gregas da última sessão. Use quando precisar acompanhar uma série específica. "
    "Não use para a cadeia inteira (use chain)."
)
TOOL_EVENTS = (
    "Vencimentos listados, eventos de ajuste de strike (provento, escala ou indeterminado) "
    "e calendário de resultados da CVM do papel, com actual (confirmado) ou estimated "
    "(previsto). Use quando precisar do que aconteceu ou está datado para o papel. "
    "Não use para preço ou volatilidade (use underlying)."
)
TOOL_STRUCTURE = (
    "Avalia a estrutura que você especificou: payoff no vencimento, breakevens, prêmio "
    "líquido, perda e ganho máximo, capital comprometido, gregas agregadas e cenários "
    "(spot × IV × dias). Use quando já tiver as pernas (opções e ação, quantidade em ações) "
    "e quiser os números daquela estrutura na sessão. Não use para escolher, comparar ou "
    "ordenar estruturas: a tool só avalia a que você deu, sem sugestão e sem probabilidade. "
    "Para ver strikes e vencimentos antes de montar as pernas, use `chain`."
)


def _safe(builder: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Executa o construtor de payload; falha de dado vira erro estruturado."""
    try:
        return builder()
    except SourceFailure as exc:
        return error_payload(exc)
    except Exception as exc:  # rede de segurança: nada de traceback na tool
        log.exception("mcp: falha inesperada na tool")
        return error_payload(exc)


@mcp.tool(description=TOOL_HEALTH)
def health() -> dict[str, Any]:
    """Saúde do lake EOD (última sessão, idade, fontes, versão do cálculo)."""
    return _safe(lambda: payloads.health_payload(get_service()))


@mcp.tool(description=TOOL_SCREEN)
def screen(date: str | None = None, max_rows: int = DEFAULT_MAX_ROWS) -> dict[str, Any]:
    """Painel descritivo do universo líquido na sessão (`date` ISO, default última)."""
    return _safe(
        lambda: payloads.screen_payload(
            get_service(), date=date, max_rows=max_rows
        )
    )


@mcp.tool(description=TOOL_UNDERLYING)
def underlying(
    ticker: str,
    date: str | None = None,
    sections: str | None = None,
    expiry: str | None = None,
    days: int = 60,
) -> dict[str, Any]:
    """Leitura de um papel (resumo, termo, smile e histórico)."""
    return _safe(
        lambda: payloads.underlying_payload(
            get_service(),
            ticker,
            date=date,
            sections=sections,
            expiry=expiry,
            days=days,
        )
    )


@mcp.tool(description=TOOL_CHAIN)
def chain(
    ticker: str,
    date: str | None = None,
    expiry: str | None = None,
    delta_min: float | None = None,
    delta_max: float | None = None,
    liquidity: str = "strict",
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Cadeia do papel na sessão (uma linha por série)."""
    return _safe(
        lambda: payloads.chain_payload(
            get_service(),
            ticker,
            date=date,
            expiry=expiry,
            delta_range=parse_delta_range(delta_min, delta_max),
            liquidity=parse_liquidity(liquidity),
            max_rows=max_rows,
        )
    )


@mcp.tool(description=TOOL_SERIES)
def series(ticker: str, maturity: str, days: int = 60) -> dict[str, Any]:
    """Identidade, última linha e histórico de uma série (ticker + vencimento)."""
    return _safe(
        lambda: payloads.series_payload(
            get_service(), ticker, maturity, days=days
        )
    )


@mcp.tool(description=TOOL_EVENTS)
def events(ticker: str) -> dict[str, Any]:
    """Vencimentos, ajustes de strike e calendário de resultados do papel."""
    return _safe(lambda: payloads.events_payload(get_service(), ticker))


@mcp.tool(description=TOOL_STRUCTURE)
def structure_eval(
    legs: list[dict[str, Any]],
    underlying: str | None = None,
    date: str | None = None,
    capital: float | None = None,
    scenarios: bool = True,
) -> dict[str, Any]:
    """Avalia a estrutura dada (payoff, breakevens, capital, gregas e cenários)."""
    body = {
        "legs": legs,
        "underlying": underlying,
        "date": date,
        "capital": capital,
        "scenarios": scenarios,
    }
    return _safe(lambda: payloads.structure_payload(get_service(), body))


__all__ = [
    "TOOL_CHAIN",
    "TOOL_EVENTS",
    "TOOL_HEALTH",
    "TOOL_SCREEN",
    "TOOL_SERIES",
    "TOOL_STRUCTURE",
    "TOOL_UNDERLYING",
    "mcp",
]
