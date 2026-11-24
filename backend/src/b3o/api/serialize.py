"""Serialização das respostas da API/MCP (F3) — PURO: sem I/O, sem DuckDB.

Contrato (`docs/contratos/onda-i.md` §2): toda resposta carrega `as_of_session` e
`model_version`; listas trazem `meta.total/returned/truncated`; resposta acima de
**200 KB** trunca a lista principal sem cortar célula pela metade. Erro é
`{"error": {code, message, hint}}` — nunca traceback.

Este módulo não conhece o lake nem a `QueryService`: recebe dataclasses/dicts e
devolve estruturas JSON-ready. Tanto a rota HTTP quanto a tool MCP chamam os
**mesmos** construtores (`b3o.api.payloads`), que é o que faz o número bater.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

from b3o.ingest.errors import SourceError, SourceFailure

DEFAULT_MAX_ROWS = 20
"""`max_rows` padrão do transporte (spec §8)."""

MAX_ROWS = 60
"""Teto de linhas por resposta (spec §8); a `QueryService` aceita mais."""

MAX_RESPONSE_BYTES = 200_000
"""Teto de resposta em bytes (spec §8): acima disso a lista principal trunca."""

_STATUS_BY_CODE = {
    "bad_request": 400,
    "unauthorized": 401,
    "bad_legs": 400,
    "underlying_mismatch": 400,
    "maturity_mismatch": 400,
    "ticker_not_found": 404,
    "no_session": 404,
    "maturity_not_found": 404,
    "expiry_not_found": 404,
    "metrics_missing": 404,
    "not_found": 404,
    "position_not_found": 404,
    "decision_not_found": 404,
    "position_closed": 409,
    "position_id_conflict": 409,
    "decision_id_conflict": 409,
    "method_not_allowed": 405,
    "frontend_missing": 503,
    "glossary_missing": 503,
    "lake_missing": 503,
    "storage_error": 503,
    "bad_config": 503,
}


def clamp_max_rows(value: int | None) -> int:
    """`None` → padrão; abaixo de 0 → 0 (lista vazia); acima do teto → teto."""
    if value is None:
        return DEFAULT_MAX_ROWS
    return max(0, min(MAX_ROWS, int(value)))


def clamp_page_size(value: int | None) -> int:
    """Tamanho de página: 1…60. Zero não pode produzir `next_offset` imóvel."""
    limit = clamp_max_rows(value)
    if limit < 1:
        raise request_error("max_rows precisa ser pelo menos 1", "use um inteiro entre 1 e 60")
    return limit


def parse_offset(value: object) -> int:
    """Offset 0-based dentro do int64; zero é válido, texto/bool não."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise request_error(
            "offset precisa ser inteiro não negativo dentro do int64",
            "use 0 para a primeira página",
        )
    return value


def jsonable(value: Any) -> Any:
    """Converte datas e dataclasses para JSON (listas/dicts recursivos).

    `math.nan`/`inf` não são JSON válido: viram `None` com a honestidade de
    "indisponível" em vez de `NaN` cru (o lake não grava NaN, mas a borda não
    confia nisso).
    """
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [jsonable(item) for item in value]
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        # arredondamento só na borda (regra do AGENTS); 6 casas matam o ruído de
        # ponto flutuante (0,10000000000000009) sem perder grega nenhuma.
        return round(value, 6)
    return value


def envelope(
    data: dict[str, Any],
    *,
    as_of_session: date | str | None,
    model_version: int | None,
    list_key: str | None = None,
    total: int | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Envelope canônico; trunca a lista `list_key` se passar de 200 KB.

    `total` permite declarar o tamanho **antes** do corte (ex.: as 60 primeiras
    de 782 séries); `counts` leva os totais das outras listas do payload
    (`underlying`/`events` têm mais de uma tabela, e o `meta` principal é o da
    lista declarada em `list_key`).
    """
    payload: dict[str, Any] = {
        "as_of_session": (
            as_of_session.isoformat() if isinstance(as_of_session, date) else as_of_session
        ),
        "model_version": model_version,
        "data": jsonable(data),
    }
    if list_key is not None:
        rows = payload["data"][list_key]
        declared = len(rows) if total is None else int(total)
        payload["meta"] = {
            "total": declared,
            "returned": len(rows),
            "truncated": len(rows) < declared,
        }
        if counts:
            payload["meta"]["lists"] = {
                key: {
                    "total": int(value),
                    "returned": len(payload["data"].get(key) or []),
                    "truncated": len(payload["data"].get(key) or []) < int(value),
                }
                for key, value in counts.items()
            }
        _fit(payload, list_key)
    return payload


def _fit(payload: dict[str, Any], list_key: str) -> None:
    """Reduz a lista principal até caber em `MAX_RESPONSE_BYTES`.

    Busca binária pelo maior prefixo que cabe; nunca corta uma linha/célula. O
    `meta` acompanha (`returned`/`truncated`). Lista esvaziada e ainda acima do
    teto (payload escalar gigante, não acontece no F3) deixa `note` explícita.
    """
    if _size(payload) <= MAX_RESPONSE_BYTES:
        return
    rows = payload["data"][list_key]
    total = int(payload["meta"]["total"])
    low, high = 0, len(rows)
    while low < high:
        middle = (low + high + 1) // 2
        payload["data"][list_key] = rows[:middle]
        payload["meta"]["returned"] = middle
        payload["meta"]["truncated"] = middle < total
        if _size(payload) <= MAX_RESPONSE_BYTES:
            low = middle
        else:
            high = middle - 1
    payload["data"][list_key] = rows[:low]
    payload["meta"]["returned"] = low
    payload["meta"]["truncated"] = low < total
    if low == 0 and _size(payload) > MAX_RESPONSE_BYTES:
        payload["meta"]["note"] = (
            "resposta base acima de 200 KB mesmo sem linhas; a lista foi esvaziada"
        )


def _size(payload: dict[str, Any]) -> int:
    """Bytes do JSON da resposta (UTF-8, sem espaços supérfluos)."""
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def error_payload(exc: BaseException) -> dict[str, Any]:
    """`{"error": {code, message, hint}}` no formato do projeto, sem traceback."""
    if isinstance(exc, SourceFailure):
        error = exc.error
    elif isinstance(exc, SourceError):
        error = exc
    else:  # rede de segurança: nada de traceback vazando
        error = SourceError(
            code="internal_error",
            message=f"{type(exc).__name__}: {exc}",
            hint="erro inesperado na API; confira o log do container",
        )
    return {"error": error.to_dict()}


def error_status(exc: BaseException) -> int:
    """Status HTTP pelo código do erro (a borda HTTP usa; o MCP ignora)."""
    code = getattr(getattr(exc, "error", None), "code", None)
    return _STATUS_BY_CODE.get(str(code), 500)


def request_error(message: str, hint: str) -> SourceFailure:
    """Erro de parâmetro (`bad_request`) no formato do projeto."""
    return SourceFailure(SourceError(code="bad_request", message=message, hint=hint))


SECTIONS: tuple[str, ...] = ("summary", "term", "smile", "history")
"""Seções de `underlying` (spec §7)."""


def parse_date(value: str | None, name: str = "date") -> date | None:
    """`YYYY-MM-DD` → `date`; vazio/None → None; torto → `bad_request`."""
    if value is None or not str(value).strip():
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise request_error(
            f"{name} inválida ({value!r}): esperado ISO AAAA-MM-DD",
            "use a data de uma sessão do lake (o `health` diz a última)",
        ) from exc


def parse_sections(value: str | None) -> tuple[str, ...]:
    """Lista de seções separada por vírgula; desconhecida → `bad_request`."""
    if value is None or not str(value).strip():
        return SECTIONS
    wanted = tuple(part.strip() for part in str(value).split(",") if part.strip())
    unknown = sorted(set(wanted) - set(SECTIONS))
    if unknown:
        raise request_error(
            f"seções desconhecidas: {unknown}",
            f"as seções válidas são {list(SECTIONS)}",
        )
    return wanted


def parse_delta_range(
    low: float | None, high: float | None
) -> tuple[float, float] | None:
    """Banda `|Δ|` do `chain`; sem parâmetro, o default da spec é 0,25–0,75.

    `0`–`1` pede a grade inteira (ainda exige `delta` gravado); `None` só existe
    para a `QueryService` chamada direto (sem filtro nenhum).
    """
    if low is None and high is None:
        return (0.25, 0.75)
    if (low is None) != (high is None):
        raise request_error(
            "delta_min e delta_max devem vir juntos",
            "informe a banda completa (ex.: delta_min=0.25&delta_max=0.75) ou nenhuma",
        )
    assert low is not None and high is not None
    if not 0.0 <= low < high <= 1.0:
        raise request_error(
            f"banda de delta inválida ({low}, {high})",
            "use 0 ≤ delta_min < delta_max ≤ 1 sobre |Δ| (puts têm Δ negativo)",
        )
    return (float(low), float(high))


def parse_liquidity(value: str | None) -> str:
    """`strict` (§6.9 gravado) ou `all`; outro valor → `bad_request`."""
    normalized = (value or "strict").strip().lower()
    if normalized not in ("strict", "all"):
        raise request_error(
            f"liquidity inválido ({value!r})",
            "use liquidity=strict (regra §6.9 gravada) ou liquidity=all",
        )
    return normalized

