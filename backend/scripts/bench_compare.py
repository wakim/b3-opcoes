"""Comparador offline do benchmark F10 (pacote P6) — só JSON intermediário.

Este módulo **não** captura, **não** abre navegador, **não** escreve relatório e
**não** conclui o F10. Ele lê o bruto gravado por P3/P4/P5, consulta o nosso lado
**somente pela API HTTP** (a mesma `QueryService` do produto), normaliza unidades
de forma explícita e grava `b3o-bench-compare/1` para o relatório futuro
(F10.6). O relatório em Markdown e a classificação de divergência ficam para
outra sessão (PLAN.md F10, estado da retomada de 26/11/2026).

Regras não negociáveis
----------------------
* Python calcula, agente interpreta, humano decide (AGENTS.md, regra 1). Todo
  número aqui vem de bruto ou de ``bench_recalc`` (P7) — nenhum nasce de LLM.
* O lado interno vem só de ``/api/health``, ``/api/screen`` (paginado),
  ``/api/t/{ticker}?sections=summary,term,smile,history&days=252`` e
  ``/api/s/{ticker}/{maturity}``. Nunca DuckDB/Parquet, nunca ``b3o``.
* A API não expõe rho: a linha traz ``internal_api_missing_rho`` e ``null``;
  nada é inventado para igualar o externo.
* Ausência é ``null`` + motivo, nunca zero silencioso (AGENTS.md, regra 5).
  Fonte ausente/corrompida/mismatch vira seção ``unavailable``.
* Strike é atributo da linha: conversão para centavos com ``Decimal``, nunca
  leitura do ticker (armadilha 1 e 2 do AGENTS.md). Casamento por 4 chaves;
  fallback ``fallback_unique`` só com um candidato de cada lado.
* ``vi_ratio``/IV bruta do opcoes.net.br são fração (×100); campos já em
  porcentagem não multiplicam. No modelo 3, gamma já é por R$1 de forward e é
  comparado diretamente; o candidato ×100 fica só na evidência histórica do
  modelo 2. Theta/vega só comparados quando a base é confirmada, senão
  ``unit_unknown``.
* ``skew_25d_30d_ratio`` do OBM é registrado **bruto**, nunca comparado como
  RR25 enquanto ``fields[]`` não definir a fórmula (F10.1 pendente).

Uso (tudo em container, pela raiz do repositório, serviço ``test``)::

    podman compose run --rm test python scripts/bench_compare.py --help
    podman compose run --rm test python scripts/bench_compare.py \
        --date 2026-11-25

Sem rede externa: somente ``B3_HTTP_URL`` (default ``http://app:8080``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html import unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

import bench_recalc as recalc

SCHEMA = "b3o-bench-compare/1"
DEFAULT_HTTP_URL = os.environ.get("B3_HTTP_URL", "http://app:8080")
DATA_DIR = os.environ.get("B3_DATA_DIR", "/data")
DEFAULT_INPUT_DIR = Path(DATA_DIR) / "bench"
DEFAULT_OUTPUT = Path(DATA_DIR) / "out"
PAGE_SIZE = 60
MAX_SCREEN_PAGES = 200
HTTP_TIMEOUT = 60.0
METRICS: tuple[str, ...] = ("price", "iv", "delta", "gamma", "theta", "vega", "rho")

DATE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUMBER_LINE = re.compile(r"[+\-\u2212\u2013]?\d[\d.,]*")
PRECO_LINE = re.compile(r"R\$\s*([+\-\u2212\u2013]?\d[\d.,]*)")

# Fração confirmada vs. já porcentagem (docs/glossario.md: "Volatilidade
# implícita (IV)"). Qualquer campo fora destas listas fica `unit_unknown`.
FRACTION_FIELDS: frozenset[str] = frozenset(
    {
        "viratio",
        "ivratio",
        "ivratiopctsource",
        "ivatm30dratio",
        "ivatm60dratio",
        "ivatm90dratio",
    }
)
PCT_FIELDS: frozenset[str] = frozenset(
    {"iv", "ivpct", "ivatm30d", "ivatm60d", "ivatm90d", "volimplicitav"}
)

OBM_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "ticker": ("ticker", "TckrSymb", "codigo", "codigoNegociacao", "ativo", "symbol", "code"),
    "maturity": ("maturity", "vencimento", "expiration", "expiryDate", "dataVencimento"),
    "strike": ("strike", "strike_brl", "strikePrice", "precoExercicio", "k"),
    "side": ("side", "optionSide", "tipo", "type", "callPut"),
    "price": ("premio", "premium", "price", "last", "close", "ultimo"),
    "bid": ("bid", "bestBid"),
    "ask": ("ask", "bestAsk"),
    "iv": ("vi_ratio", "iv_ratio", "iv", "vi", "volImplícita"),
    "delta": ("delta", "delta_ratio"),
    "gamma": ("gamma", "gamma_ratio"),
    "theta": ("theta", "theta_ratio"),
    "vega": ("vega", "vega_ratio"),
    "rho": ("rho", "rho_ratio"),
    "volume": ("volume", "volume_financial", "trades_count", "negocios"),
    "oi": ("oi", "openInterest", "posicoesAbertas"),
    "liq": ("liq", "liquidity", "liquididade"),
}

# O volume de ações do OHLC da OBM é `volume_shares` (o field `volume` do
# COTAHIST é financeiro); os dois são conservados como brutos distintos.
OHLC_QUANTITY_ALIASES: tuple[str, ...] = (
    "volume_shares",
    "shares",
    "qty",
    "quantity",
    "quantidade",
    "tradesQty",
)

DIVIDEND_TICKER_ALIASES: tuple[str, ...] = (
    "asset_ticker",
    "assetTicker",
    "ticker",
    "TckrSymb",
    "codigo",
    "underlying",
    "ativo",
)

OPCOES_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "ticker": ("ticker", "codigo", "ativo", "opcao", "código da opção"),
    "side": ("tipo", "lado", "call/put", "c/p", "operação"),
    "style": ("mod.", "mod", "estilo", "style"),
    "strike": ("strike", "preço de exercício", "preco de exercicio", "exercicio"),
    "maturity": ("vencimento", "venc", "maturity"),
    "price": ("último", "ultimo", "last", "fechamento"),
    # Cabeçalho REAL do DataTable em 2026-11-25: "Vol. Impl. (%)", não
    # "Vol. Implícita". Alias explícito evita depender de casamento parcial;
    # o valor da coluna é fração (0,4412) e continua ×100 em `parse_opcoes`.
    "iv": ("iv", "vol. impl. (%)", "vol. implícita", "volatilidade implícita", "vi"),
    "delta": ("delta", "δ"),
    "gamma": ("gamma", "γ"),
    "theta": ("theta", "θ", "theta r$"),
    "vega": ("vega", "ν"),
    "volume": ("volume", "vol"),
    "trades": ("negócios", "negocios", "trades"),
}

UNDERLYING_FIELDS: tuple[str, ...] = (
    "iv_atm_30d",
    "iv_rank_252",
    "iv_pct_252",
    "rv20",
    "rv30",
    "rv60",
    "vrp",
    "vrp_z_252",
    "rr25",
    "bfly25",
    "ts_slope_30_90",
    "exp_move_1s_30d",
    "implied_div_yield_30d",
    "forward_30d",
    "days_to_liquidate",
    "spot_brl",
    "rv_shock_share_pct",
)

OBM_AGGREGATE_ALIASES: dict[str, tuple[str, ...]] = {
    "iv_atm_30d": ("iv_atm_30d_ratio", "ivAtm30dRatio", "iv_atm_30d"),
    "iv_atm_60d": ("iv_atm_60d_ratio", "ivAtm60dRatio", "iv_atm_60d"),
    "iv_atm_90d": ("iv_atm_90d_ratio", "ivAtm90dRatio", "iv_atm_90d"),
    "skew_25d_30d_ratio": ("skew_25d_30d_ratio", "skew25d30dRatio"),
    "iv_rank_252": ("iv_rank", "rank", "ivRank", "iv_rank_252"),
    "iv_pct_252": (
        "percentil",
        "iv_percentile",
        "ivPercentil",
        "iv_pct_252",
        "iv_percentile_252",
    ),
}

GAMMA_NOTE = (
    "Modelo 3: gamma é comparado diretamente por R$1 de forward. A razão "
    "gamma_externo/gamma_api ≈ 100 e o candidato ×100 ficam apenas na "
    "evidência histórica do modelo 2 (`benchmark-2026-11-25-p6b-stale.json`)."
)
RHO_NOTE = "A API não expõe rho; rho externo fica bruto e o interno é null com flag."
SKEW_NOTE = (
    "skew_25d_30d_ratio do OBM fica bruto. A própria `fields[]` do agregado define "
    "o campo como 'IV da put 25Δ menos a da put 50Δ, levadas a 30 dias corridos', "
    "com peso pelo número de negócios nas séries vizinhas — ponto de 30 dias "
    "corridos e uma perna put-put. Não é o nosso RR25 (call 25Δ − put 25Δ no "
    "vencimento smile, 30 dias úteis): nunca é comparado como rr25."
)
B3LAB_THETA_NOTE = (
    "B3Lab: theta é por dia CALENDÁRIO; o valor em dia útil é somente candidato "
    "theta×365/252, com o bruto preservado. Vega e rho são por 1 ponto (1%) de σ; "
    "rho é derivada parcial com q fixo, não rho Black-76 com q=r atrelado — "
    "metodologia declarada, sem conversão."
)
B3LAB_INPUT_NOTE = (
    "B3Lab: record.inputs traz F/df/t/du (S/df/t/du no bruto); "
    "record.b3lab.inputs traz business_days/sigma_pct/rate_pct/"
    "dividend_yield_pct. IV é input (sigma_pct), nunca output extraído; preço é "
    "teórico, não fechamento."
)
STRUCTURE_QTY_SHARES = 100
"Quantidade do montador OBM: saídas já são totais para 100 ações."

STRUCTURE_FIELD_SOURCE: dict[str, str] = {
    "net_premium_brl": "net_cost",
    "max_gain_brl": "max_profit",
    "max_loss_brl": "max_loss",
    "breakeven_brl": "breakevens",
}
"Tradução explícita dos campos brutos do `recalc.response.text` do OBM."

STRUCTURE_GREEK_FIELDS: tuple[str, ...] = ("delta", "gamma", "theta_brl", "vega_brl")


class CompareError(Exception):
    """Falha explícita no formato ``{code, message, hint}`` do projeto."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def payload(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


# --------------------------------------------------------------------------- #
# Helpers puros — nenhum I/O, nenhum número inventado                          #
# --------------------------------------------------------------------------- #


def canonical(value: Any) -> str:
    """JSON canônico para SHA; sem arredondar número nenhum."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_sha(value: Any) -> str:
    return sha256_hex(canonical(value).encode("utf-8"))


def now_iso() -> str:
    """ISO com offset do ``TZ=America/Sao_Paulo`` do container."""
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def as_float(value: Any) -> float | None:
    return float(value) if is_number(value) else None


def key_of(value: Any) -> str:
    """Chave de alias/cabeçalho: sem acento, caixa baixa, só alfanumérico."""
    text = unicodedata.normalize("NFKD", str(value))
    plain = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", plain.lower())


def alias_get(
    record: Mapping[str, Any], aliases: Sequence[str]
) -> tuple[Any, str | None]:
    """Primeiro alias explícito presente; ``(None, None)`` quando ausente."""
    normalized = {key_of(name): (name, value) for name, value in record.items()}
    for alias in aliases:
        wanted = key_of(alias)
        if not wanted:
            continue
        exact = normalized.get(wanted)
        if exact is not None:
            return exact[1], exact[0]
        partial = next(
            (
                pair
                for norm, pair in normalized.items()
                if len(wanted) >= 3 and (wanted in norm or norm in wanted)
            ),
            None,
        )
        if partial is not None:
            return partial[1], partial[0]
    return None, None


def column_index(columns: Sequence[Any], aliases: Sequence[str]) -> int | None:
    """Índice da coluna pelo **cabeçalho**, nunca por posição fixa."""
    normalized = [key_of(column) for column in columns]
    for alias in aliases:
        wanted = key_of(alias)
        if not wanted:
            continue
        exact = next((i for i, name in enumerate(normalized) if name == wanted), None)
        if exact is not None:
            return exact
        partial = next(
            (
                i
                for i, name in enumerate(normalized)
                if name and len(wanted) >= 3 and (wanted in name or name in wanted)
            ),
            None,
        )
        if partial is not None:
            return partial
    return None


def decimal_from(value: Any) -> Decimal | None:
    """Decimal de número ou texto pt-BR (vírgula decimal, ponto de milhar)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value)) if math.isfinite(float(value)) else None
    if not isinstance(value, str):
        return None
    text = value.replace("R$", "").replace("\xa0", "").strip()
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def ptbr_float(value: Any) -> float | None:
    """Número de texto pt-BR/sinal Unicode; ``None`` quando ilegível."""
    if is_number(value):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\u2212", "-").replace("\u2013", "-")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    if not NUMBER_LINE.fullmatch(text):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def strike_to_cents(value: Any) -> tuple[int | None, float | None, list[str]]:
    """Strike da **linha** para centavos (Decimal), nunca do ticker."""
    flags: list[str] = []
    number = decimal_from(value)
    if number is None or number <= 0:
        return None, None, ["invalid_strike"]
    cents = int((number * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP))
    if cents <= 0:
        return None, None, ["invalid_strike"]
    return cents, float(number), flags


def side_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = key_of(value)
    if text in {"c", "call", "callamericana", "callEuropeia", "calle"}:
        return "C"
    if text in {"p", "put", "puteuropeia", "putamericana"}:
        return "P"
    if "put" in text:
        return "P"
    if "call" in text:
        return "C"
    return None


def normalize_iv(
    value: Any, field: str, *, known_fraction: bool = False
) -> tuple[float | None, list[str]]:
    """IV em pontos percentuais: fração ×100; campo já pct intacto."""
    number = as_float(value)
    if number is None:
        return None, ["invalid_iv"]
    normalized_field = key_of(field)
    if known_fraction or normalized_field in FRACTION_FIELDS:
        return number * 100.0, []
    if normalized_field in PCT_FIELDS:
        return number, []
    return None, ["unit_unknown"]


def normalize_price(value: Any, *, unit: str | None) -> tuple[float | None, list[str]]:
    """Preço em R$/ação; fonte externa só normaliza com unidade confirmada."""
    number = as_float(value)
    if number is None:
        return None, ["invalid_price"]
    if unit in {"brl_per_share", "brl", "reais_por_acao"}:
        return number, []
    return None, ["unit_unknown"]


def normalize_theta(
    value: Any, inputs: Mapping[str, Any] | None
) -> tuple[float | None, list[str], str | None]:
    """Theta em R$/ação/dia útil (docs/glossario.md: "Theta")."""
    number = as_float(value)
    if number is None:
        return None, ["invalid_theta"], None
    data = dict(inputs or {})
    basis = data.get("day_basis")
    if basis is None and "business_days" in data:
        basis = "business_day"
    if basis is None and "calendar_days" in data:
        basis = "calendar_day"
    if basis == "business_day":
        return number, [], "brl_per_share_per_business_day"
    if basis == "calendar_day":
        return (
            number * 365.0 / 252.0,
            ["theta_calendar_to_business_candidate"],
            "brl_per_share_per_business_day",
        )
    return None, ["unit_unknown"], None


def normalize_vega(
    value: Any, inputs: Mapping[str, Any] | None
) -> tuple[float | None, list[str], str | None]:
    """Vega em R$/ação/ponto de vol; 1% de σ é 1 ponto."""
    number = as_float(value)
    if number is None:
        return None, ["invalid_vega"], None
    data = dict(inputs or {})
    basis = data.get("vega_basis")
    if basis is None and data.get("sigma_pct") is not None:
        basis = "per_pct"
    if basis == "per_pct":
        return number, [], "brl_per_share_per_vol_point"
    if basis == "per_unit":
        return number / 100.0, ["vega_per_unit_to_point_candidate"], "brl_per_share_per_vol_point"
    return None, ["unit_unknown"], None


def median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def quantile_linear(values: Sequence[float], q: float) -> float:
    """P90 explícito: posição ``(n−1)·q`` com interpolação linear."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] + weight * (ordered[high] - ordered[low])


def difference_stats(differences: Sequence[float]) -> dict[str, Any]:
    """Estatística por fonte/métrica: ``externo_normalizado − interno_normalizado``."""
    if not differences:
        return {
            "n": 0,
            "median_abs": None,
            "p90_abs": None,
            "max_abs": None,
            "median_signed": None,
            "min_signed": None,
            "max_signed": None,
        }
    absolute = [abs(value) for value in differences]
    signed = sorted(differences)
    return {
        "n": len(differences),
        "median_abs": median(absolute),
        "p90_abs": quantile_linear(absolute, 0.9),
        "max_abs": max(absolute),
        "median_signed": median(signed),
        "min_signed": signed[0],
        "max_signed": signed[-1],
    }


def match_rows(
    internal: Sequence[Mapping[str, Any]], external: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Casa por (ticker, vencimento, strike em centavos, lado).

    Fallback **único** por (ticker, vencimento, lado) só quando sobra exatamente
    um candidato de cada lado; duplicidade vira ``ambiguous_match`` e é pulada.
    """
    matches: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    used_internal: set[int] = set()
    used_external: set[int] = set()
    exact_internal: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    exact_external: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for index, row in enumerate(internal):
        exact_internal[row["match_key"]].append(index)
    for index, row in enumerate(external):
        exact_external[row["match_key"]].append(index)

    def emit(i: int, e: int, method: str, flags: list[str]) -> None:
        used_internal.add(i)
        used_external.add(e)
        matches.append(
            {
                "internal": internal[i],
                "external": external[e],
                "match_method": method,
                "match": dict(internal[i]["match_key_raw"]),
                "flags": list(flags),
            }
        )

    for match_key in sorted(set(exact_internal) & set(exact_external), key=str):
        inside, outside = exact_internal[match_key], exact_external[match_key]
        if len(inside) == 1 and len(outside) == 1:
            emit(inside[0], outside[0], "exact", [])
        else:
            skipped.append(
                {
                    "match_key": list(match_key),
                    "reason": "ambiguous_match",
                    "n_internal": len(inside),
                    "n_external": len(outside),
                }
            )

    partial_internal: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    partial_external: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for index, row in enumerate(internal):
        if index not in used_internal:
            partial_internal[row["partial_key"]].append(index)
    for index, row in enumerate(external):
        if index not in used_external:
            partial_external[row["partial_key"]].append(index)
    for partial_key in sorted(set(partial_internal) & set(partial_external), key=str):
        inside, outside = partial_internal[partial_key], partial_external[partial_key]
        if len(inside) == 1 and len(outside) == 1:
            emit(
                inside[0],
                outside[0],
                "fallback_unique",
                ["fallback_unique", "strike_mismatch_preserved"],
            )
        else:
            skipped.append(
                {
                    "match_key": list(partial_key),
                    "reason": "ambiguous_match",
                    "n_internal": len(inside),
                    "n_external": len(outside),
                }
            )
    for index, row in enumerate(external):
        if index not in used_external:
            skipped.append(
                {
                    "match_key": list(row["match_key"]),
                    "reason": "unmatched_external",
                }
            )
    for index, row in enumerate(internal):
        if index not in used_internal:
            skipped.append(
                {
                    "match_key": list(row["match_key"]),
                    "reason": "unmatched_internal",
                }
            )
    return matches, skipped


def make_match_row(
    *,
    ticker: str,
    maturity: str | None,
    strike_value: Any,
    side: str | None,
) -> dict[str, Any]:
    """Linha normalizada de casamento: strike em centavos com ``Decimal``."""
    cents, strike_brl, flags = strike_to_cents(strike_value)
    normalized_side = side_code(side) if isinstance(side, str) else side
    return {
        "ticker": str(ticker).strip().upper() if ticker else None,
        "maturity": maturity,
        "side": normalized_side,
        "strike_brl": strike_brl,
        "strike_cents": cents,
        "flags": list(flags),
        "match_key": (str(ticker).strip().upper(), maturity, cents, normalized_side),
        "partial_key": (str(ticker).strip().upper(), maturity, normalized_side),
        "match_key_raw": {
            "ticker": str(ticker).strip().upper() if ticker else None,
            "maturity": maturity,
            "strike_cents": cents,
            "side": normalized_side,
        },
    }


# --------------------------------------------------------------------------- #
# API local — única fonte do lado interno                                      #
# --------------------------------------------------------------------------- #


@dataclass
class ApiClient:
    """Cliente stdlib sobre ``B3_HTTP_URL``; erros no envelope do projeto."""

    base_url: str

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        query = urlencode(dict(params or {}))
        url = f"{self.base_url.rstrip('/')}{path}" + (f"?{query}" if query else "")
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "b3o-bench-compare/1"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT) as response:  # noqa: S310 - URL local
                raw = response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:1000]
            try:
                error = json.loads(body).get("error", {})
            except (ValueError, AttributeError):
                error = {}
            raise CompareError(
                str(error.get("code") or "http_error"),
                str(error.get("message") or f"HTTP {exc.code} em {url}"),
                str(error.get("hint") or "confira B3_HTTP_URL e se a app está no ar"),
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise CompareError(
                "http_unavailable",
                f"não foi possível acessar {url}: {exc}",
                "use B3_HTTP_URL e `podman compose up -d app`; nada vem do relógio",
            ) from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise CompareError(
                "bad_payload", f"resposta não JSON de {url}", "confira se aponta para /api"
            ) from exc
        if not isinstance(payload, dict):
            raise CompareError(
                "bad_payload", f"resposta inesperada de {url}", "esperado objeto JSON"
            )
        if isinstance(payload.get("error"), dict):
            error = payload["error"]
            raise CompareError(
                str(error.get("code", "api_error")),
                str(error.get("message", "")),
                str(error.get("hint", "confira os parâmetros e a sessão do lake")),
            )
        return payload


def fetch_health(client: ApiClient) -> dict[str, Any]:
    payload = client.get("/api/health")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    session = payload.get("as_of_session") or data.get("last_session")
    if not isinstance(session, str) or not DATE_ISO.match(session):
        raise CompareError(
            "health_incomplete",
            f"/api/health não declarou sessão utilizável: {session!r}",
            "rode `b3o prepare` e confira /api/health antes de comparar",
        )
    return {
        "session": session,
        "last_session": data.get("last_session"),
        "model_version": payload.get("model_version"),
        "raw": payload,
    }


def guard_health(health: Mapping[str, Any], reference: str, phase: str) -> None:
    """Valida o health **atual** para uma comparação histórica.

    A sessão atual precisa ser igual em ``as_of_session``/``last_session`` e
    nunca pode ser anterior a ``--date``. Os endpoints históricos é que devolvem
    exatamente ``reference``; o health atual pode estar em 26/11 ou depois.
    ``model_version`` inteiro, sem ``model_version_mismatch`` e sem
    ``last_error`` continuam sendo condições duras.
    """
    raw = health.get("raw") if isinstance(health.get("raw"), Mapping) else health
    data = raw.get("data") if isinstance(raw.get("data"), Mapping) else {}
    observed = (raw.get("as_of_session"), data.get("last_session"))
    if any(not isinstance(value, str) or not DATE_ISO.match(value) for value in observed):
        raise CompareError(
            "health_session_incomplete",
            f"{phase}: health declarou sessão inválida {observed!r}",
            "confira /api/health antes de comparar",
        )
    if observed[0] != observed[1]:
        raise CompareError(
            "health_session_inconsistent",
            f"{phase}: as_of_session={observed[0]!r}, last_session={observed[1]!r}",
            "aguarde o lago estabilizar; não concilie sessões diferentes",
        )
    if observed[0] < reference:
        raise CompareError(
            "health_session_before_reference",
            f"{phase}: health atual {observed[0]!r} é anterior à referência {reference!r}",
            "a comparação histórica exige que o lago já contenha a sessão de referência",
        )
    model = raw.get("model_version")
    if model is None:
        model = data.get("model_version")
    if isinstance(model, bool) or not isinstance(model, int):
        raise CompareError(
            "health_model_version_invalid",
            f"{phase}: model_version={model!r} não é inteiro",
            "confira /api/health; nunca compare modelos diferentes no mesmo ranking",
        )
    if data.get("model_version_mismatch") is True:
        raise CompareError(
            "health_model_version_mismatch",
            f"{phase}: health sinalizou model_version_mismatch",
            "recalcule o histórico inteiro antes de comparar",
        )
    if data.get("last_error") is not None:
        raise CompareError(
            "health_last_error",
            f"{phase}: health registrou last_error={data.get('last_error')!r}",
            "resolva a falha de fonte do lake antes de comparar",
        )


def guard_api_reference(
    payload: Mapping[str, Any], reference: str, model_version: int, label: str
) -> None:
    """Endpoint histórico: sessão e modelo exatos, nunca o health atual."""
    if payload.get("as_of_session") != reference:
        raise CompareError(
            "api_session_mismatch",
            f"{label}: as_of_session={payload.get('as_of_session')!r}, esperado {reference!r}",
            "o endpoint histórico deve ser cortado exatamente na sessão de referência",
        )
    if payload.get("model_version") != model_version:
        raise CompareError(
            "api_model_version_mismatch",
            f"{label}: model_version={payload.get('model_version')!r}, "
            f"esperado {model_version!r}",
            "endpoint e health atual não podem misturar modelos",
        )


def guard_manifest_model(
    name: str, declared: Any, expected: int, manifest: str | None
) -> None:
    """Modelo declarado por P3/P4/P5 tem que ser o mesmo do health atual."""
    if declared is None:
        raise CompareError(
            f"{name}_model_version_missing",
            f"{name}: manifesto {manifest or 'desconhecido'} não declara model_version",
            "regrave a captura com o mesmo modelo do lake",
        )
    if isinstance(declared, bool) or not isinstance(declared, int) or declared != expected:
        raise CompareError(
            f"{name}_model_version_mismatch",
            f"{name}: model_version={declared!r} no manifesto, health={expected}",
            "capturas de modelos diferentes não podem entrar na mesma comparação",
        )


def guard_declared_session(
    name: str, location: str, declared: Any, expected: str
) -> None:
    """Data de sessão **declarada** tem que bater com ``--date``.

    Ausência (``None``) é dado auxiliar sem sessão declarada e é permitida;
    divergência é erro de sessão — nunca sucesso parcial com fonte utilizável.
    """
    if declared is None:
        return
    if not isinstance(declared, str) or declared != expected:
        raise CompareError(
            f"{name}_session_mismatch",
            f"{name} {location}: declara {declared!r}, pedido {expected!r}",
            "captura e comparação têm que usar exatamente a mesma sessão",
        )


def guard_manifest_session(
    name: str,
    declared: Any,
    expected: str,
    manifest: str | None,
    extras: Sequence[tuple[str, Any]] = (),
) -> None:
    """Sessão primária obrigatória e toda data auxiliar declarada no manifesto.

    ``extras`` traz as demais datas de sessão do mesmo manifesto (P3
    ``requested_date`` e P5 ``reference_date``): divergência entre qualquer uma
    e ``--date`` aborta a comparação, mesmo quando a primária confere.
    """
    if declared is None:
        raise CompareError(
            f"{name}_session_missing",
            f"{name}: manifesto {manifest or 'desconhecido'} não declara sessão",
            "regrave a captura com a sessão do lake",
        )
    guard_declared_session(name, "manifesto (sessão primária)", declared, expected)
    for extra_name, extra_date in extras:
        guard_declared_session(name, f"manifesto {extra_name}", extra_date, expected)


def fetch_screen_all(
    client: ApiClient, session: str, model_version: int
) -> dict[str, Any]:
    """`/api/screen` paginado; toda página precisa cortar na mesma sessão/modelo."""
    rows: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None
    pages = 0
    while pages < MAX_SCREEN_PAGES:
        payload = client.get(
            "/api/screen", {"date": session, "max_rows": PAGE_SIZE, "offset": offset}
        )
        guard_api_reference(
            payload, session, model_version, f"/api/screen?offset={offset}"
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        page_rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        rows.extend(row for row in page_rows if isinstance(row, dict))
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        total = meta.get("total") if is_number(meta.get("total")) else len(rows)
        pages += 1
        next_offset = meta.get("next_offset")
        if next_offset is None or not is_number(next_offset):
            break
        offset = int(next_offset)
    return {"rows": rows, "total": total, "pages": pages}


def fetch_underlying(
    client: ApiClient, ticker: str, session: str, model_version: int
) -> dict[str, Any]:
    payload = client.get(
        f"/api/t/{quote(ticker, safe='')}",
        {"date": session, "sections": "summary,term,smile,history", "days": 252},
    )
    guard_api_reference(payload, session, model_version, f"/api/t/{ticker}")
    return payload


def fetch_series(
    client: ApiClient, ticker: str, maturity: str, session: str, model_version: int
) -> dict[str, Any]:
    payload = client.get(
        f"/api/s/{quote(ticker, safe='')}/{quote(maturity, safe='')}",
        {"date": session},
    )
    guard_api_reference(
        payload, session, model_version, f"/api/s/{ticker}/{maturity}"
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return {"payload": payload, "data": data}


# --------------------------------------------------------------------------- #
# Manifestos e integridade                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class SourceInfo:
    """Estado de uma fonte — ausência sempre com motivo."""

    name: str
    status: str = "unavailable"
    reason: str | None = "not_discovered"
    manifest: str | None = None
    reference_date: str | None = None
    sha256: str | None = None
    url: str | None = None
    accessed_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def unavailable(self, code: str, **kwargs: Any) -> None:
        """Marca indisponibilidade sem colidir ``code`` com ``reason``.

        Chamada canônica: ``unavailable(code, reason=...)``. Quando nenhuma
        mensagem é passada, o próprio ``code`` serve de motivo — nunca ``None``
        silencioso (AGENTS.md, regra 5).
        """
        self.status = "unavailable"
        self.reason = kwargs.pop("reason", None) or code
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "manifest": self.manifest,
            "reference_date": self.reference_date,
            "sha256": self.sha256,
            "url": self.url,
            "accessed_at": self.accessed_at,
            "flags": sorted(set(self.flags)),
            "counts": {
                "artifacts": len(self.artifacts),
                "ok": sum(1 for item in self.artifacts if item.get("status") == "ok"),
            },
        }


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CompareError(
            f"{label}_unreadable", f"não foi possível ler {path}: {exc}", "confira --input-dir"
        ) from exc
    except ValueError as exc:
        raise CompareError(
            f"{label}_bad_json", f"JSON inválido em {path}: {exc}", "o bruto foi corrompido?"
        ) from exc
    if not isinstance(value, dict):
        raise CompareError(
            f"{label}_bad_json", f"{path} não é um objeto JSON", "manifesto inválido"
        )
    return value


def _ignored_capture_path(path: Path) -> bool:
    """Backups, temporários e probes nunca são manifestos ativos."""
    parts = [part.lower() for part in path.parts]
    return any(
        part.startswith(".tmp")
        or part.startswith("backup-")
        or ".backup-" in part
        or "probe" in part
        for part in parts
    )


def discover_manifests(
    input_dir: Path, notes: list[str], session: str | None = None
) -> dict[str, Any]:
    """Descobre P3/P4/P5 a partir da pasta da sessão **ou** da raiz `data/bench`.

    * P3 fica na pasta da sessão (`…/AAAA-MM-DD/manifest.json`);
    * P4 em `…/AAAA-MM-DD/b3lab/manifest.json`;
    * P5 em `…/structures-AAAA-MM-DD-*/manifest.json`, **irmão** da sessão.

    Backups (`.backup-`/`backup-`), `.tmp*` e `PROBE` são ignorados — o P5
    ativo de 2026-11-25 é `structures-2026-11-25-petr4/manifest.json`.
    """
    found: dict[str, Any] = {"capture": None, "b3lab": None, "structures": []}
    base = input_dir.resolve()
    if not base.is_dir():
        notes.append(f"input_dir_ausente: {input_dir}")
        return found
    guessed = base.name if DATE_ISO.match(base.name) else None
    wanted = session or guessed
    session_dir = base if guessed else (base / wanted if wanted else base)
    if session_dir != base and not session_dir.is_dir():
        notes.append(f"pasta_da_sessao_ausente: {session_dir}")
        session_dir = base

    capture_path = session_dir / "manifest.json"
    if capture_path.is_file() and not _ignored_capture_path(capture_path):
        try:
            probe = json.loads(capture_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            probe = None
        if isinstance(probe, dict) and "artifacts" in probe:
            found["capture"] = capture_path
            if wanted is None:
                wanted = probe.get("session") or probe.get("requested_date")
                if isinstance(wanted, str) and DATE_ISO.match(wanted):
                    session_dir = session_dir.parent / wanted
                    notes.append(f"sessao_inferida_do_manifest: {wanted}")
    if found["capture"] is None:
        for path in sorted(session_dir.rglob("manifest.json")):
            if _ignored_capture_path(path):
                continue
            if "b3lab" in path.parts or path.parent.name.startswith("structures-"):
                continue
            try:
                probe = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(probe, dict) and "artifacts" in probe:
                found["capture"] = path
                notes.append(f"p3_fora_da_raiz: {path.relative_to(base).as_posix()}")
                break

    b3lab_path = session_dir / "b3lab" / "manifest.json"
    if b3lab_path.is_file() and not _ignored_capture_path(b3lab_path):
        found["b3lab"] = b3lab_path
    else:
        for path in sorted(session_dir.rglob("manifest.json")):
            if "b3lab" in path.parts and not _ignored_capture_path(path):
                found["b3lab"] = path
                break

    root = session_dir.parent
    if wanted and DATE_ISO.match(str(wanted)):
        for candidate in sorted(root.glob(f"structures-{wanted}-*")):
            if not candidate.is_dir() or _ignored_capture_path(candidate):
                notes.append(f"p5_ignorado: {candidate.name}")
                continue
            manifest_path = candidate / "manifest.json"
            if manifest_path.is_file():
                found["structures"].append(manifest_path)
    else:
        for candidate in sorted(root.glob("structures-*-*")):
            if candidate.is_dir() and not _ignored_capture_path(candidate):
                manifest_path = candidate / "manifest.json"
                if manifest_path.is_file():
                    found["structures"].append(manifest_path)
    if not found["structures"]:
        notes.append(f"p5_nao_descoberto: structures-{wanted or 'AAAA-MM-DD'}-* em {root}")
    return found


def verify_capture(path: Path | None, session: str) -> SourceInfo:
    """P3: manifesto, data de referência e SHA do bruto **e** do meta_file."""
    info = SourceInfo("obm+opcoes_net")
    if path is None:
        info.unavailable("manifest_missing", reason="manifest P3 não encontrado")
        return info
    info.manifest = str(path)
    try:
        manifest = read_json(path, "capture_manifest")
    except CompareError as exc:
        info.unavailable(exc.code, reason=exc.message)
        return info
    info.payload = manifest
    declared = [
        value
        for value in (manifest.get("session"), manifest.get("requested_date"))
        if isinstance(value, str) and value
    ]
    if declared and any(value != session for value in declared):
        info.unavailable(
            "session_mismatch",
            reason=f"manifesto P3 declara {declared}, pedido {session}",
        )
        return info
    if manifest.get("status") != "complete":
        info.unavailable(
            "capture_incomplete",
            reason=f"status={manifest.get('status')!r}",
            reference_date=manifest.get("session"),
        )
        return info
    info.reference_date = manifest.get("session") or manifest.get("requested_date")
    info.sha256 = manifest.get("sha256")
    info.flags.extend(str(flag) for flag in manifest.get("flags") or [])
    for entry in manifest.get("artifacts") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("artifact") or "")
        raw_path = path.parent / name
        record: dict[str, Any] = {
            "artifact": name,
            "source": entry.get("source"),
            "kind": entry.get("kind"),
            "ticker": entry.get("ticker"),
            "maturity": entry.get("maturity"),
            "url": entry.get("url"),
            "accessed_at": entry.get("accessed_at"),
            "reference_date": entry.get("reference_date"),
            "declared_sha256": entry.get("sha256"),
            "meta_file": entry.get("meta_file"),
            "status": "ok",
            "reason": None,
        }
        # Toda sessão declarada no artefato é validada, inclusive quando o
        # próprio artefato é declarado unavailable ou o arquivo está faltando:
        # divergência aborta, nunca vira "fonte indisponível" silenciosa.
        guard_declared_session(
            "P3", f"artefato {name} reference_date", entry.get("reference_date"), session
        )
        if entry.get("status") not in (None, "ok"):
            record["status"] = "unavailable"
            record["reason"] = str(entry.get("reason") or "declared_unavailable")
        elif not name or not raw_path.is_file():
            record["status"] = "unavailable"
            record["reason"] = "artifact_missing"
        else:
            body = raw_path.read_bytes()
            record["bytes"] = len(body)
            record["sha256"] = sha256_hex(body)
            expected = entry.get("sha256")
            if isinstance(expected, str) and record["sha256"] != expected:
                record["status"] = "unavailable"
                record["reason"] = "sha256_mismatch"
        meta_name = entry.get("meta_file")
        if isinstance(meta_name, str) and meta_name:
            meta_path = path.parent / meta_name
            if not meta_path.is_file():
                record["status"] = "unavailable"
                record["reason"] = "meta_file_missing"
            else:
                try:
                    wrapper = read_json(meta_path, "meta_file")
                    expected_raw = wrapper.get("raw_sha256")
                    if isinstance(expected_raw, str) and expected_raw != record.get("sha256"):
                        record["status"] = "unavailable"
                        record["reason"] = "raw_sha256_mismatch"
                    record["meta"] = wrapper
                except CompareError as exc:
                    record["status"] = "unavailable"
                    record["reason"] = exc.code
        info.artifacts.append(record)
    ok_sources = sorted(
        {
            str(item["source"])
            for item in info.artifacts
            if item["status"] == "ok" and item["source"]
        }
    )
    info.status = "ok" if ok_sources else "unavailable"
    if info.status == "unavailable":
        info.reason = "no_usable_artifacts"
    else:
        info.reason = None
    return info


def split_capture(verified: SourceInfo, source: str, name: str) -> SourceInfo:
    """Deriva uma fonte independente do verificador P3 (``obm``/``opcoes_net``)."""
    info = SourceInfo(name)
    info.manifest = verified.manifest
    info.reference_date = verified.reference_date
    info.sha256 = verified.sha256
    info.flags = list(verified.flags)
    info.artifacts = [
        item for item in verified.artifacts if item.get("source") == source
    ]
    if verified.manifest is None:
        info.unavailable("manifest_missing", reason="manifesto P3 não encontrado")
    elif verified.status != "ok":
        info.unavailable(
            f"capture_{verified.reason or 'unavailable'}",
            reason=verified.reason,
            manifest=verified.manifest,
        )
    elif not any(item.get("status") == "ok" for item in info.artifacts):
        info.unavailable(
            f"no_usable_{source}_artifacts", manifest=verified.manifest
        )
    else:
        info.status = "ok"
        info.reason = None
    return info


def verify_b3lab(path: Path | None, session: str) -> SourceInfo:
    """P4: manifesto, sessão, arquivos por série e SHA declarado."""
    info = SourceInfo("b3lab")
    if path is None:
        info.unavailable("manifest_missing", reason="manifesto P4 não encontrado")
        return info
    info.manifest = str(path)
    try:
        manifest = read_json(path, "b3lab_manifest")
    except CompareError as exc:
        info.unavailable(exc.code, reason=exc.message)
        return info
    info.payload = manifest
    info.url = manifest.get("url")
    info.accessed_at = manifest.get("accessed_at")
    info.sha256 = manifest.get("sha256")
    info.flags.extend(str(flag) for flag in manifest.get("flags") or [])
    reference = manifest.get("reference_date")
    if reference != session:
        info.unavailable(
            "session_mismatch", reason=f"P4 declara {reference!r}, pedido {session}"
        )
        return info
    info.reference_date = reference
    series = manifest.get("series") if isinstance(manifest.get("series"), list) else []
    raw_records: list[Any] = []
    for item in series:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("file") or "")
        series_path = path.parent / filename
        record: dict[str, Any] = {"file": filename, "status": "ok", "reason": None}
        if not filename or not series_path.is_file():
            record["status"] = "unavailable"
            record["reason"] = "series_file_missing"
        else:
            try:
                body = json.loads(series_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record["status"] = "unavailable"
                record["reason"] = "series_file_unreadable"
                body = None
            if isinstance(body, dict):
                guard_declared_session(
                    "P4", f"corpo {filename} reference_date", body.get("reference_date"), session
                )
                raw_records.append(body)
                record["sha256"] = canonical_sha(body)
                # O corpo bruto é guardado: `parse_b3lab` lê só `entry["record"]`.
                record["record"] = body
                record["inputs"] = body.get("inputs")
                b3lab_body = body.get("b3lab")
                record["outputs"] = (
                    b3lab_body.get("outputs") if isinstance(b3lab_body, dict) else None
                )
        info.artifacts.append(record)
    declared_sha = manifest.get("sha256")
    if isinstance(declared_sha, str) and len(raw_records) == len(info.artifacts) and raw_records:
        if canonical_sha(raw_records) != declared_sha:
            info.flags.append("manifest_sha256_mismatch")
            info.unavailable(
                "sha256_mismatch", reason="SHA do manifest não bate com os brutos das séries"
            )
            return info
    info.status = "ok" if any(item["status"] == "ok" for item in info.artifacts) else "unavailable"
    if info.status == "unavailable":
        info.reason = "no_usable_series"
    else:
        info.reason = None
    return info


# Ausências explícitas aceitas pelo dono: não são captura vermelha.
STRUCTURE_ABSENCE_STATUSES: frozenset[str] = frozenset(
    {"no_public_single_leg_control", "unsupported_external"}
)


def _health_session_model(health: Any) -> tuple[str | None, Any]:
    """Sessão e `model_version` de um bloco `/api/health` congelado."""
    if not isinstance(health, Mapping):
        return None, None
    data = health.get("data") if isinstance(health.get("data"), Mapping) else {}
    session = health.get("as_of_session") or data.get("last_session")
    model = health.get("model_version")
    if model is None:
        model = data.get("model_version")
    return (
        session if isinstance(session, str) else None,
        model,
    )


def _health_is_red(health: Any, session: str) -> str | None:
    """Captura com erro de fonte/lake ou sessão diferente não é válida."""
    if not isinstance(health, Mapping):
        return "health_missing"
    data = health.get("data") if isinstance(health.get("data"), Mapping) else {}
    health_session, model = _health_session_model(health)
    if health_session != session:
        return "health_session_mismatch"
    if data.get("last_error") is not None:
        return "health_last_error"
    if data.get("model_version_mismatch") is True:
        return "health_model_version_mismatch"
    if model is None:
        return "health_model_version_missing"
    return None


def verify_structures(paths: Sequence[Path], session: str, notes: list[str]) -> SourceInfo:
    """P5: sessão/modelo, `source_sha256` do plano e SHA canônico dos records.

    O `sha256` do plano não é o do arquivo: é o SHA canônico do objeto sem a
    própria chave `source_sha256`. O `sha256` de cada record é o SHA canônico
    sem a chave `sha256`. Ausências explícitas (`put_caixa` e `venda_coberta`)
    são mantidas com flag, não tratadas como falha.
    """
    info = SourceInfo("structures")
    if not paths:
        info.unavailable("manifest_missing", reason="manifesto P5 não encontrado")
        return info
    for manifest_path in paths:
        try:
            manifest = read_json(manifest_path, "structures_manifest")
        except CompareError as exc:
            info.flags.append(f"{manifest_path}:{exc.code}")
            continue
        reference = manifest.get("reference_date") or manifest.get("session")
        if reference != session:
            info.flags.append(f"{manifest_path.name}:session_mismatch")
            continue
        red = _health_is_red(manifest.get("health_after"), session)
        if red is None:
            red = _health_is_red(manifest.get("health_before"), session)
        model = manifest.get("model_version")
        _, health_model = _health_session_model(manifest.get("health_after"))
        if not is_number(model):
            red = red or "model_version_missing"
        elif health_model is not None and model != health_model:
            red = red or "model_version_mismatch"
        if red is not None:
            info.flags.append(f"{manifest_path.name}:{red}")
            continue
        if info.reference_date is None:
            info.reference_date = reference
            info.manifest = str(manifest_path)
            info.sha256 = manifest.get("sha256")
            info.accessed_at = manifest.get("accessed_at")
            info.flags.extend(str(flag) for flag in manifest.get("flags") or [])

        # Plano: `source_sha256` = SHA canônico sem a própria chave.
        plan_path = manifest_path.parent / "plan.json"
        plan_candidates = [plan_path] if plan_path.is_file() else sorted(
            manifest_path.parent.glob("*.plan.json")
        )
        plan_verified = False
        for candidate in plan_candidates:
            try:
                loaded = read_json(candidate, "structures_plan")
            except CompareError as exc:
                info.flags.append(f"{candidate.name}:{exc.code}")
                continue
            without_source = {
                key: value for key, value in loaded.items() if key != "source_sha256"
            }
            computed = canonical_sha(without_source)
            declared = manifest.get("source_sha256") or loaded.get("source_sha256")
            plan_verified = isinstance(declared, str) and declared == computed
            guard_declared_session(
                "P5", f"plano {candidate.name} session", loaded.get("session"), session
            )
            guard_declared_session(
                "P5",
                f"plano {candidate.name} reference_date",
                loaded.get("reference_date"),
                session,
            )
            guard_manifest_model(
                "P5", loaded.get("model_version"), model, str(candidate)
            )
            break
        if not plan_verified:
            info.flags.append(f"{manifest_path.name}:source_sha256_unverified")
            continue

        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        declared_structures = (
            manifest.get("structures") if isinstance(manifest.get("structures"), dict) else {}
        )
        records: list[dict[str, Any]] = []
        for name, filename in sorted(files.items()):
            record_path = manifest_path.parent / str(filename)
            entry: dict[str, Any] = {
                "structure": name,
                "file": str(filename),
                "status": "ok",
                "reason": None,
            }
            if not record_path.is_file():
                entry["status"] = "unavailable"
                entry["reason"] = "structure_file_missing"
            else:
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    record = None
                if not isinstance(record, dict):
                    entry["status"] = "unavailable"
                    entry["reason"] = "structure_file_unreadable"
                else:
                    guard_declared_session(
                        "P5", f"record {name} session", record.get("session"), session
                    )
                    guard_declared_session(
                        "P5", f"record {name} reference_date", record.get("reference_date"), session
                    )
                    request = (
                        record.get("request")
                        if isinstance(record.get("request"), Mapping)
                        else {}
                    )
                    guard_declared_session(
                        "P5", f"record {name} request.date", request.get("date"), session
                    )
                    response = (
                        record.get("response")
                        if isinstance(record.get("response"), Mapping)
                        else {}
                    )
                    guard_declared_session(
                        "P5",
                        f"record {name} response.as_of_session",
                        response.get("as_of_session"),
                        session,
                    )
                    guard_manifest_model(
                        "P5", response.get("model_version"), model, str(record_path)
                    )
                    declared = record.get("sha256")
                    without_sha = {
                        key: value for key, value in record.items() if key != "sha256"
                    }
                    computed = canonical_sha(without_sha)
                    entry["sha256"] = computed
                    entry["declared_sha256"] = declared
                    manifest_sha = (
                        declared_structures.get(name, {}).get("sha256")
                        if isinstance(declared_structures.get(name), Mapping)
                        else None
                    )
                    entry["manifest_sha256"] = manifest_sha
                    if not isinstance(declared, str) or declared != computed:
                        entry["status"] = "unavailable"
                        entry["reason"] = "sha256_mismatch"
                    elif isinstance(manifest_sha, str) and manifest_sha != computed:
                        entry["status"] = "unavailable"
                        entry["reason"] = "manifest_sha256_mismatch"
                    status = str(record.get("status") or "")
                    if status in STRUCTURE_ABSENCE_STATUSES:
                        entry["absence_reason"] = record.get("reason")
                        entry["flags"] = ["explicit_absence", status]
                    elif status == "captured":
                        entry["flags"] = []
                    else:
                        entry["status"] = "unavailable"
                        entry["reason"] = "unexpected_structure_status"
                        entry["flags"] = [status or "status_missing"]
                    entry["record"] = record
            info.artifacts.append(entry)
            if entry["status"] == "ok":
                records.append(entry)
        if records:
            info.status = "ok"
            info.reason = None
            info.flags.append("plano_e_records_verificados")
            break
    if info.status != "ok":
        info.unavailable("no_usable_structures", reason="nenhum P5 passou nas verificações")
    notes.append(
        "P5: sessão/model_version e health sem erro; `source_sha256` do plano é o "
        "SHA canônico sem a própria chave; cada record é validado pelo SHA canônico "
        "sem a chave `sha256` e contra o manifesto. `put_caixa`/`venda_coberta` "
        "com ausência explícita são aceitos com flag."
    )
    return info


# --------------------------------------------------------------------------- #
# Parsers externos                                                             #
# --------------------------------------------------------------------------- #


def _find_rows(node: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Localiza a lista de registros dentro de JSON interceptado/RSC."""
    if depth > 4:
        return []
    if isinstance(node, list):
        rows = [item for item in node if isinstance(item, dict)]
        return rows if rows else []
    if isinstance(node, dict):
        for name in ("rows", "data", "opcoes", "options", "items", "result", "payload"):
            child = node.get(name)
            if isinstance(child, list):
                rows = [item for item in child if isinstance(item, dict)]
                if rows:
                    return rows
        for name in ("rows", "data", "result", "payload", "props", "value"):
            child = node.get(name)
            if isinstance(child, dict):
                found = _find_rows(child, depth + 1)
                if found:
                    return found
    return []


def _json_blobs(meta: Mapping[str, Any]) -> list[Any]:
    """Extrai JSON de RSC preservado sem reescrever o bruto."""
    blobs: list[Any] = []
    extracted = meta.get("extracted")
    if not isinstance(extracted, dict):
        return blobs
    for value in extracted.values():
        if isinstance(value, str) and value.lstrip()[:1] in ("{", "["):
            try:
                blobs.append(json.loads(value))
            except ValueError:
                continue
        elif isinstance(value, dict | list):
            blobs.append(value)
    return blobs


RSC_PUSH = re.compile(
    r"self\.__next_f\.push\(\s*\[\s*\d+\s*,\s*(\"(?:[^\"\\]|\\.)*\")\s*\]\s*\)"
)


def classify_obm(entry: Mapping[str, Any]) -> str:
    """Classifica pelo **segmento da URL**, não só pelo `kind` da página.

    A página `/opcoes/{t}` tem `kind == "underlying"`, mas carrega o agregado
    diário: o segmento `agregado-diario` da URL interceptada manda em
    `aggregates`. O mesmo vale para `vi-historico`, `volatilidade`, `acoes`,
    `tesouro-direto/curva` e `calendario-dividendos`.
    """
    path = urlparse(str(entry.get("url") or "")).path.lower()
    segments = [part for part in path.split("/") if part]
    joined = " ".join(segments)
    if "agregado-diario" in joined or "agregadodiario" in joined:
        return "agregado"
    if "vi-historico" in joined or "vihistorico" in joined:
        return "vi_historico"
    if "volatilidade" in joined:
        return "volatilidade"
    if "tesouro-direto" in joined or "curva" in joined:
        return "ettj"
    if "dividendos" in joined or "calendario" in joined:
        return "dividendos"
    if "montador" in joined:
        return "montador"
    if "acoes" in segments:
        return "ohlc"
    if "opcoes" in segments:
        return "underlying"
    return str(entry.get("kind") or "unknown")


def _rsc_json_blobs(text: str) -> list[Any]:
    """JSON embutido em RSC (`self.__next_f.push`), sem alterar o bruto."""
    blobs: list[Any] = []
    decoder = json.JSONDecoder()
    for match in RSC_PUSH.finditer(text):
        try:
            payload = json.loads(match.group(1))
        except ValueError:
            continue
        if not isinstance(payload, str):
            continue
        index = 0
        while index < len(payload):
            start = next(
                (pos for pos in (payload.find("{", index), payload.find("[", index)) if pos >= 0),
                None,
            )
            if start is None:
                break
            try:
                node, end = decoder.raw_decode(payload[start:])
            except ValueError:
                index = start + 1
                continue
            blobs.append(node)
            index = start + end
    return blobs


def artifact_priority(entry: Mapping[str, Any]) -> int:
    """JSON interceptado (``.json``) antes de HTML/RSC (``.html``)."""
    name = str(entry.get("artifact") or "")
    return 0 if Path(name).suffix.lower() == ".json" else 1


def artifact_ref(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    """Proveniência mínima de um registro OBM — o bruto fica no manifesto."""
    if not isinstance(entry, Mapping):
        return {"artifact": None}
    return {
        "artifact": entry.get("artifact"),
        "sha256": entry.get("sha256"),
        "url": entry.get("url"),
        "accessed_at": entry.get("accessed_at"),
        "source": entry.get("source"),
        "kind": entry.get("kind"),
        "ticker": entry.get("ticker"),
    }


def dedup_key_json(key: Any) -> Any:
    """Chave de dedup serializável (datas viram ISO)."""
    if isinstance(key, tuple):
        return [dedup_key_json(item) for item in key]
    if isinstance(key, date):
        return key.isoformat()
    return key


def _artifact_underlying(entry: Mapping[str, Any]) -> str | None:
    """Ativo-objeto declarado no artefato P3; nunca inferido do prefixo."""
    ticker = str(entry.get("ticker") or "").strip().upper()
    return ticker or None


def _node_underlying(node: Any, entry: Mapping[str, Any] | None = None) -> str | None:
    """Ticker declarado no JSON (`underlying.ticker`); nunca inferido do prefixo."""
    if isinstance(node, Mapping):
        underlying = node.get("underlying")
        if isinstance(underlying, Mapping):
            ticker = str(underlying.get("ticker") or "").strip().upper()
            if ticker:
                return ticker
        ticker = str(node.get("ticker") or "").strip().upper()
        if ticker:
            return ticker
    return _artifact_underlying(entry) if entry is not None else None


def _find_key(node: Any, key: str, depth: int = 0) -> Any:
    """Achata um container nomeado em JSON/RSC, sem reescrever o bruto."""
    if depth > 20:
        return None
    if isinstance(node, Mapping):
        if key in node:
            return node[key]
        for value in node.values():
            found = _find_key(value, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, Sequence) and not isinstance(node, str | bytes):
        for item in node:
            found = _find_key(item, key, depth + 1)
            if found is not None:
                return found
    return None


_HTML_TABLE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.I | re.S)
_HTML_TR = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_HTML_CELL = re.compile(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", re.I | re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HTML_TAG = re.compile(r"<[^>]+>")


def _plain_html(text: str) -> str:
    return unescape(_HTML_TAG.sub("", _HTML_COMMENT.sub("", text))).strip()


def _html_tables(text: str) -> list[list[list[str]]]:
    """Tabelas do HTML server-rendered como células de texto (sem BeautifulSoup)."""
    tables: list[list[list[str]]] = []
    for table in _HTML_TABLE.findall(text):
        rows: list[list[str]] = []
        for tr in _HTML_TR.findall(table):
            cells = [_plain_html(cell) for cell in _HTML_CELL.findall(tr)]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


TERM_TEXT = re.compile(r"(\d{2}/\d{2})\s*·\s*(\d+)\s*du\s*·\s*([\d,]+)%")
RSC_TERM_LABEL = re.compile(
    r'\\"(\d{4}-\d{2}-\d{2})\\",\{\\"className\\":\\"mono inline-flex '
    r'items-center gap-1\\",\\"children\\":\[\\"(\d{2}/\d{2})\\"'
)


def _term_iso_by_label(text: str) -> dict[str, str]:
    """Rótulos `dd/mm` do SVG casados com as datas ISO das chaves do RSC."""
    return {label: iso for iso, label in RSC_TERM_LABEL.findall(text)}


def _parse_vol_term(text: str) -> list[dict[str, Any]]:
    """Estrutura a termo renderizada: `dd/mm · N du · IV ATM%`."""
    plain = _plain_html(text)
    iso_by_label = _term_iso_by_label(text)
    rows: list[dict[str, Any]] = []
    for label, du, iv in TERM_TEXT.findall(plain):
        number = ptbr_float(iv)
        if number is None:
            continue
        rows.append(
            {
                "date": label,
                "maturity": iso_by_label.get(label),
                "du": int(du),
                "iv_pct": number,
                "raw": f"{label} · {du} du · {iv}%",
            }
        )
    return rows


def _parse_vol_surface(text: str) -> list[dict[str, Any]]:
    """Superfície `VENC \\ STRIKE`: uma linha por vencimento, strikes no cabeçalho."""
    for rows in _html_tables(text):
        header = rows[0]
        if not header or "strike" not in key_of(header[0]):
            continue
        iso_by_label = _term_iso_by_label(text)
        strikes = [ptbr_float(cell) for cell in header[1:]]
        result: list[dict[str, Any]] = []
        for cells in rows[1:]:
            label = cells[0]
            if not re.fullmatch(r"\d{2}/\d{2}", label):
                continue
            values = [ptbr_float(cell) for cell in cells[1:]]
            result.append(
                {
                    "date": label,
                    "maturity": iso_by_label.get(label),
                    "strikes_pct": strikes,
                    "iv_pct": values,
                    "raw": list(cells),
                }
            )
        return result
    return []


def _parse_ettj_curve(text: str) -> list[tuple[int, float]]:
    """ETTJ: tabela prefixada atual com os nós 21 e 42 (não a primeira)."""
    for rows in _html_tables(text):
        header = [key_of(cell) for cell in rows[0]] if rows else []
        if not any("vertice" in cell or "prazo" in cell for cell in header):
            continue
        if not any("taxa" in cell for cell in header):
            continue
        nodes: list[tuple[int, float]] = []
        for cells in rows[1:]:
            if len(cells) < 2:
                continue
            du_raw = NUMBER_LINE.search(cells[0])
            rate_raw = NUMBER_LINE.search(cells[1])
            if du_raw is None or rate_raw is None:
                continue
            du_number = ptbr_float(du_raw.group(0))
            rate_number = ptbr_float(rate_raw.group(0))
            if du_number is None or rate_number is None or du_number <= 0:
                continue
            nodes.append((int(du_number), rate_number))
        node_set = {du for du, _ in nodes}
        if 21 in node_set and 42 in node_set:
            return nodes
    return []


def parse_obm(info: SourceInfo, session: str, notes: list[str]) -> dict[str, Any]:
    """Séries, agregados, OHLC, ETTJ, proventos, VI e volatilidade dos brutos reais.

    Formatos medidos em P3 (2026-11-25):

    * cadeia = JSON com ``rows[]`` e ``call``/``put`` aninhados; o vencimento é
      ``vencimento.expiration_date``. Cada perna vira uma linha por
      ``(ticker, maturity, strike_cents, side)`` com o bruto preservado;
    * agregado = ``latest`` + ``fields[]`` + ``points[]`` + ``latest_positions``;
      só ``latest`` é usado como métrica atual; ``fields[]`` é preservado;
    * VI histórico = ``points[]`` com ``date``/``vi_ratio`` (252 por papel);
    * OHLC = ``initialPrices.items`` dentro do RSC, com ``volume_shares``;
    * ETTJ = tabela prefixada atual (nós 21 e 42), não a primeira;
    * proventos = ``items[]`` com ``asset_ticker``/``ex_date``/``value_per_share``;
    * volatilidade = HTML server-rendered (termo e superfície ``VENC \\ STRIKE``).

    O JSON interceptado tem prioridade sobre HTML/RSC; duplicatas são contadas
    em ``dedup``/``dedup_counts`` e o bruto permanece intacto no manifesto P3.
    """
    result: dict[str, Any] = {
        "series": [],
        "aggregates": [],
        "ohlc": [],
        "ohlc_qty": [],
        "curve": [],
        "dividends": [],
        "vi_history": [],
        "term": [],
        "surface": [],
        "skipped": [],
        "classifications": [],
        "dedup": [],
        "dedup_counts": {},
        "ohlc_sources": {},
        # As listas agregadas seguem existindo por compatibilidade; o recálculo
        # consome os mapas, que separam o dado por ativo-objeto declarado.
        "ohlc_by_underlying": {},
        "ohlc_qty_by_underlying": {},
        "vi_history_by_underlying": {},
        "ohlc_sources_by_underlying": {},
        "dividends_by_underlying": {},
        "term_by_underlying": {},
        "surface_by_underlying": {},
    }
    seen: dict[str, dict[Any, dict[str, Any]]] = defaultdict(dict)

    def keep(
        group: str,
        key: Any,
        value: Any,
        entry: Mapping[str, Any] | None = None,
        underlying: str | None = None,
    ) -> bool:
        """Mantém a primeira ocorrência; registra a duplicata sem apagar o bruto."""
        bucket = seen[group]
        ref = artifact_ref(entry)
        if key in bucket:
            kept = bucket[key]
            result["dedup"].append(
                {
                    "group": group,
                    "key": dedup_key_json(key),
                    "kept": kept,
                    "discarded": ref,
                }
            )
            result["dedup_counts"][group] = result["dedup_counts"].get(group, 0) + 1
            kept_value = result[group][kept["index"]]
            if isinstance(kept_value, dict) and isinstance(kept_value.get("flags"), list):
                if "duplicate_artifact_collapsed" not in kept_value["flags"]:
                    kept_value["flags"].append("duplicate_artifact_collapsed")
            return False
        bucket[key] = {"index": len(result[group]), "artifact": ref}
        result["dedup_counts"].setdefault(group, 0)
        result[group].append(value)
        by_underlying = result.get(f"{group}_by_underlying")
        if underlying is not None and isinstance(by_underlying, dict):
            by_underlying.setdefault(underlying, []).append(value)
        return True

    entries = [
        entry
        for entry in info.artifacts
        if entry.get("status") == "ok" and entry.get("source") == "obm"
    ]
    # Ordenação estável: JSON interceptado primeiro; empate = ordem do manifesto.
    entries.sort(key=artifact_priority)
    for entry in entries:
        artifact = entry.get("artifact") or ""
        classified = classify_obm(entry)
        result["classifications"].append({"artifact": artifact, "kind": classified})
        wrapper = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        raw_path = Path(info.manifest or ".").parent / artifact
        try:
            text = raw_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            result["skipped"].append({"artifact": artifact, "reason": "artifact_unreadable"})
            continue
        nodes: list[Any] = _rsc_json_blobs(text)
        stripped = text.lstrip()
        if stripped[:1] in ("{", "["):
            try:
                nodes.insert(0, json.loads(stripped))
            except ValueError:
                pass
        nodes.extend(_json_blobs(wrapper))

        if classified == "underlying":
            # Só as cadeias interceptadas em JSON entram: o HTML/RSC da página
            # traz vencimentos semanais fora de `captured_chains` (345 séries
            # extras em 25/11). O bruto segue intacto no manifesto P3.
            if Path(artifact).suffix.lower() != ".json":
                result["skipped"].append(
                    {"artifact": artifact, "reason": "html_chain_not_in_captured_chains"}
                )
                continue
            for node in nodes:
                vencimento = _find_key(node, "vencimento")
                rows = _find_key(node, "rows")
                if not isinstance(vencimento, Mapping) or not isinstance(rows, list):
                    continue
                maturity = str(vencimento.get("expiration_date") or "")[:10]
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    for side_name in ("call", "put"):
                        leg = row.get(side_name)
                        if not isinstance(leg, Mapping):
                            continue
                        series = _obm_chain_leg(leg, row, maturity, side_name, entry)
                        if series is None:
                            result["skipped"].append(
                                {"artifact": artifact, "reason": "obm_leg_unusable"}
                            )
                            continue
                        keep(
                            "series",
                            series["match"]["match_key"],
                            series,
                            entry,
                            series.get("underlying"),
                        )
        elif classified == "agregado":
            for node in nodes:
                latest = node.get("latest") if isinstance(node, Mapping) else None
                if not isinstance(latest, Mapping):
                    continue
                underlying = _node_underlying(node, entry)
                record = {
                    "underlying": underlying,
                    "reference_date": node.get("reference_date"),
                    "latest": dict(latest),
                    # Compatibilidade com `build_underlying_comparisons`.
                    "values": dict(latest),
                    "fields": list(node.get("fields") or []),
                    "points": list(node.get("points") or []),
                    "latest_positions": node.get("latest_positions"),
                    "raw": node if isinstance(node, dict) else dict(node),
                    "artifact": entry,
                }
                keep("aggregates", (underlying, latest.get("date")), record, entry, underlying)
        elif classified == "ohlc":
            underlying = _artifact_underlying(entry)
            for node in nodes:
                initial = _find_key(node, "initialPrices")
                items = (
                    initial.get("items")
                    if isinstance(initial, Mapping) and isinstance(initial.get("items"), list)
                    else None
                )
                if items is None:
                    items = _find_key(node, "items") if isinstance(node, Mapping) else None
                if not isinstance(items, list):
                    continue
                for row in items:
                    if not isinstance(row, Mapping):
                        continue
                    bar = _obm_bar(row)
                    if bar is not None and keep(
                        "ohlc", (underlying, bar.refdate), bar, entry, underlying
                    ):
                        ref = artifact_ref(entry)
                        result["ohlc_sources"][bar.refdate.isoformat()] = ref
                        if underlying is not None:
                            result["ohlc_sources_by_underlying"].setdefault(
                                underlying, {}
                            )[bar.refdate.isoformat()] = ref
                    quantity = _obm_qty(row)
                    if quantity is not None:
                        keep(
                            "ohlc_qty",
                            (underlying, quantity["date"]),
                            quantity,
                            entry,
                            underlying,
                        )
        elif classified == "ettj":
            curve = _parse_ettj_curve(text)
            for du, rate in curve:
                keep("curve", du, (du, rate), entry)
            if not curve:
                result["skipped"].append(
                    {"artifact": artifact, "reason": "ettj_prefixed_table_not_found"}
                )
        elif classified == "dividendos":
            # Calendário é global: o papel vem da linha, nunca do artefato.
            for node in nodes:
                items = _find_key(node, "items")
                if not isinstance(items, list):
                    continue
                for row in items:
                    if not isinstance(row, Mapping):
                        continue
                    item = _dividend(row)
                    if item is not None and item.get("ticker"):
                        ticker = item["ticker"]
                        keep(
                            "dividends",
                            (ticker, item["date"], item["amount_brl"]),
                            item,
                            entry,
                            ticker,
                        )
        elif classified == "vi_historico":
            for node in nodes:
                points = _find_key(node, "points")
                if not isinstance(points, list):
                    continue
                underlying = _node_underlying(node, entry)
                for row in points:
                    if not isinstance(row, Mapping):
                        continue
                    point = _vi_history_point(row)
                    if point is not None:
                        keep(
                            "vi_history",
                            (underlying, point["date"]),
                            point,
                            entry,
                            underlying,
                        )
        elif classified == "volatilidade":
            underlying = _artifact_underlying(entry)
            for point in _parse_vol_term(text):
                point = dict(point, underlying=underlying, artifact=artifact_ref(entry))
                keep(
                    "term",
                    (underlying, point["maturity"] or point["date"]),
                    point,
                    entry,
                    underlying,
                )
            for line in _parse_vol_surface(text):
                line = dict(line, underlying=underlying, artifact=artifact_ref(entry))
                keep(
                    "surface",
                    (underlying, line["maturity"] or line["date"]),
                    line,
                    entry,
                    underlying,
                )
    notes.append(
        "OBM: cadeia achatada de `rows[].call/put` por (ticker, maturity, "
        "strike_cents, side) com vencimento de `vencimento.expiration_date`; "
        "só JSON interceptado entra na cadeia (HTML/RSC da página traz semanais "
        "fora de `captured_chains` e é registrado em `skipped`); "
        "agregado usa `latest` e preserva `fields[]`/`points[]`/`latest_positions`; "
        "OHLC vem de `initialPrices.items` com `volume_shares`; ETTJ da tabela "
        "prefixada com du 21/42; proventos de `asset_ticker`/`ex_date`/"
        "`value_per_share`; volatilidade do HTML server-rendered. " + GAMMA_NOTE
    )
    notes.append(
        "OBM dedup: JSON interceptado (.json) tem prioridade sobre HTML/RSC (.html); "
        "empate mantém a primeira ocorrência do manifesto. Duplicatas são contadas em "
        "`dedup`/`dedup_counts`, a linha mantida ganha `duplicate_artifact_collapsed` e "
        "o bruto segue intacto no manifesto P3. OHLC/VI/termo/superfície dedup por "
        "(ativo-objeto, data), quantidade por (ativo-objeto, data) e provento por "
        "(ticker, data, valor); as listas agregadas são só compatibilidade, os mapas "
        "`*_by_underlying` alimentam o recálculo. Provento sem ticker fica fora de "
        "todos os mapas."
    )
    return result


def _obm_chain_leg(
    leg: Mapping[str, Any],
    row: Mapping[str, Any],
    maturity: str,
    side_name: str,
    entry: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Uma perna aninhada da cadeia OBM — bruto e proveniência preservados."""
    values: dict[str, Any] = {"raw": dict(leg), "row_raw": dict(row)}
    for name, aliases in OBM_FIELD_ALIASES.items():
        value, _ = alias_get(leg, aliases)
        if value is None and name == "strike":
            value, _ = alias_get(row, aliases)
        values[name] = value
    if not values.get("ticker") or not DATE_ISO.match(maturity):
        return None
    side = side_code(values.get("side") or side_name)
    if side is None:
        return None
    match = make_match_row(
        ticker=str(values["ticker"]),
        maturity=maturity,
        strike_value=values.get("strike"),
        side=side,
    )
    if match["strike_cents"] is None:
        return None
    raw_iv_field = next(
        (
            name
            for name in leg
            if key_of(name) in {key_of(alias) for alias in OBM_FIELD_ALIASES["iv"]}
        ),
        "vi_ratio",
    )
    # Contrato F10: `vi_ratio` é fração e `premio` é R$/ação, com unidade confirmada.
    iv, iv_flags = normalize_iv(values.get("iv"), str(raw_iv_field))
    price, price_flags = normalize_price(values.get("price"), unit="brl_per_share")
    delta = ptbr_float(values.get("delta"))
    if delta is not None and side == "P" and delta > 0:
        delta = -delta  # delta assinado da ponta (spec §6.2)
    values.update(
        {
            "maturity": maturity,
            "match": match,
            "underlying": _artifact_underlying(entry),
            "iv_raw": values.get("iv"),
            "price_raw": values.get("price"),
            "iv_normalized": iv,
            "price_normalized": price,
            "price_unit": "brl_per_share",
            "delta_signed": delta,
            "flags": list(match["flags"]) + iv_flags + price_flags,
            "artifact": dict(artifact_ref(entry), kind=entry.get("kind")),
        }
    )
    return values


def _obm_bar(row: Mapping[str, Any]) -> recalc.OhlcBar | None:
    day, _ = alias_get(row, ("date", "refdate", "data", "dt"))
    if not isinstance(day, str) or not DATE_ISO.match(day[:10]):
        return None
    numbers: dict[str, int] = {}
    for name in ("open", "high", "low", "close"):
        value, _ = alias_get(row, (name, f"{name}_brl", "abertura" if name == "open" else name))
        number = decimal_from(value)
        if number is None or number <= 0:
            return None
        numbers[name] = int((number * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP))
    return recalc.OhlcBar(
        refdate=date.fromisoformat(day[:10]),
        open=numbers["open"],
        high=numbers["high"],
        low=numbers["low"],
        close=numbers["close"],
    )


def _obm_qty(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Quantidade negociada (ações) da barra OHLC — base do `days_to_liquidate`."""
    day, _ = alias_get(row, ("date", "refdate", "data", "dt"))
    value, field = alias_get(row, OHLC_QUANTITY_ALIASES)
    number = decimal_from(value)
    if number is None or number <= 0 or not isinstance(day, str) or not DATE_ISO.match(day[:10]):
        return None
    shares_financial, _ = alias_get(row, ("volume_financial", "financialVolume"))
    trades, _ = alias_get(row, ("trades_count", "trades"))
    return {
        "date": day[:10],
        "qty": float(number),
        "field": field,
        "volume_financial_raw": shares_financial,
        "trades_count_raw": trades,
        "raw": dict(row),
    }


def _ettj_node(row: Mapping[str, Any]) -> tuple[int | None, float | None]:
    du_value, _ = alias_get(row, ("du", "diasUteis", "businessDays"))
    rate_value, _ = alias_get(row, ("rate", "taxa", "precoTaxa", "rate_pct"))
    du_number = decimal_from(du_value)
    rate_number = ptbr_float(rate_value)
    if du_number is None or rate_number is None:
        return None, None
    du = int(du_number.to_integral_value())
    return (du, rate_number) if du > 0 else (None, None)


def _dividend(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Provento com ticker da linha e ``du`` explícito quando declarado.

    O calendário OBM é global: sem ticker declarado o provento não pode ser
    atribuído a papel nenhum (``ticker: None``), nunca por prefixo de opção.
    """
    day, _ = alias_get(row, ("ex_date", "exDate", "dataEx", "date", "data"))
    amount, _ = alias_get(
        row, ("value_per_share", "valuePerShare", "amount", "valor", "dividend", "provento")
    )
    du_value, _ = alias_get(row, ("du", "diasUteis", "businessDays"))
    ticker_value, _ = alias_get(row, DIVIDEND_TICKER_ALIASES)
    value = decimal_from(amount)
    if value is None or value < 0 or not isinstance(day, str) or not DATE_ISO.match(day[:10]):
        return None
    du_number = decimal_from(du_value)
    du = int(du_number.to_integral_value()) if du_number is not None else None
    ticker = str(ticker_value).strip().upper() if ticker_value is not None else None
    return {
        "ticker": ticker or None,
        "date": day[:10],
        "amount_brl": float(value),
        "du": du if du is not None and du > 0 else None,
        "raw": dict(row),
    }


def _vi_history_point(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Ponto do VI histórico com data, para montar o histórico IV−RV30."""
    day, _ = alias_get(row, ("date", "refdate", "data", "dt"))
    value, field = alias_get(row, ("vi", "iv", "iv_pct", "vi_ratio", "value"))
    number = ptbr_float(value)
    if number is None or not isinstance(day, str) or not DATE_ISO.match(day[:10]):
        return None
    normalized, flags = normalize_iv(number, str(field or "iv"))
    if normalized is None:
        return None
    return {"date": day[:10], "iv_pct": normalized, "flags": flags, "raw": dict(row)}


OPCOES_HEADER_COLUMNS: tuple[str, ...] = ("Última", "IV Rank", "Percentil", "Mín", "Máx")
OPCOES_HEADER_LABELS: tuple[tuple[str, str], ...] = (
    ("historic", "Volatilidade Histórica"),
    ("calls", "Volatilidade Implícita CALLs"),
    ("puts", "PUTs"),
)


def _parse_opcoes_header(page_text: Any, artifact: Any) -> list[dict[str, Any]]:
    """Cabeçalho VH/CALLs/PUTs separado (Última, IV Rank, Percentil, Mín, Máx).

    A metodologia desse bloco ainda não foi confirmada: os números ficam
    brutos/normalizados por pt-BR, sem virar IV ou rank do produto.
    """
    if not isinstance(page_text, str):
        return []
    lines = page_text.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if all(
                key_of(word) in key_of(line)
                for word in (*OPCOES_HEADER_COLUMNS[:3], "min", "max")
            )
        ),
        None,
    )
    if header_index is None:
        return []
    columns = list(OPCOES_HEADER_COLUMNS)
    entries: list[dict[str, Any]] = []
    for name, label in OPCOES_HEADER_LABELS:
        line = next(
            (
                candidate.strip()
                for candidate in lines[header_index + 1 : header_index + 8]
                if key_of(label) in key_of(candidate)
            ),
            None,
        )
        if line is None:
            continue
        numbers = [ptbr_float(value) for value in NUMBER_LINE.findall(line)]
        numbers = numbers[-len(columns) :] if len(numbers) >= len(columns) else numbers
        entries.append(
            {
                "artifact": artifact,
                "name": name,
                "label": label,
                "columns": columns,
                "values": {
                    column: number
                    for column, number in zip(columns, numbers, strict=False)
                },
                "raw": line,
                "flags": ["header_methodology_unconfirmed"],
            }
        )
    return entries


def parse_opcoes(info: SourceInfo, notes: list[str]) -> dict[str, Any]:
    """Grade do opcoes.net.br pelo **cabeçalho** DataTable, nunca índice fixo."""
    result: dict[str, Any] = {
        "series": [],
        "headers": [],
        "header_metrics": [],
        "skipped": [],
    }
    for entry in info.artifacts:
        if entry.get("status") != "ok" or entry.get("source") != "opcoes_net":
            continue
        wrapper = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        extracted = wrapper.get("extracted") if isinstance(wrapper.get("extracted"), dict) else {}
        columns = extracted.get("columns")
        rows = extracted.get("rows")
        if not isinstance(columns, list) or not isinstance(rows, list):
            result["skipped"].append(
                {"artifact": entry.get("artifact"), "reason": "columns_or_rows_missing"}
            )
            continue
        indexes = {
            name: column_index(columns, aliases)
            for name, aliases in OPCOES_COLUMN_ALIASES.items()
        }
        result["headers"].append(
            {
                "artifact": entry.get("artifact"),
                "columns": list(columns),
                "indexes": indexes,
            }
        )
        header_metrics = _parse_opcoes_header(
            extracted.get("page_text"), entry.get("artifact")
        )
        result["header_metrics"].extend(header_metrics)
        if not header_metrics:
            result["skipped"].append(
                {"artifact": entry.get("artifact"), "reason": "header_metrics_unresolved"}
            )
        if indexes.get("ticker") is None or indexes.get("strike") is None:
            result["skipped"].append(
                {"artifact": entry.get("artifact"), "reason": "columns_unresolved"}
            )
            continue
        boxes = extracted.get("checked_expiries")
        checked = [str(day)[:10] for day in boxes] if isinstance(boxes, list) else []
        fallback_maturity = str(entry.get("maturity") or "")[:10] or None
        for row in rows:
            cells = list(row) if isinstance(row, list | tuple) else []
            if not cells and isinstance(row, dict):
                cells = [row.get(str(column)) for column in columns]

            def cell(
                name: str,
                _cells: Sequence[Any] = cells,
                _indexes: Mapping[str, int | None] = indexes,
            ) -> Any:
                index = _indexes.get(name)
                return _cells[index] if index is not None and index < len(_cells) else None

            maturity = str(cell("maturity") or "")[:10] or None
            if maturity is None and len(checked) == 1:
                maturity = checked[0]
            if maturity is None:
                maturity = fallback_maturity
            if maturity is None or not DATE_ISO.match(maturity):
                result["skipped"].append(
                    {"artifact": entry.get("artifact"), "reason": "maturity_ambiguous"}
                )
                continue
            side = side_code(cell("side"))
            if side is None:
                result["skipped"].append(
                    {"artifact": entry.get("artifact"), "reason": "side_unresolved"}
                )
                continue
            match = make_match_row(
                ticker=str(cell("ticker") or ""),
                maturity=maturity,
                strike_value=cell("strike"),
                side=side,
            )
            if match["strike_cents"] is None or not match["ticker"]:
                result["skipped"].append(
                    {"artifact": entry.get("artifact"), "reason": "strike_or_ticker_invalid"}
                )
                continue
            # Contrato F10: "Último" da grade é R$/ação; não exige "R$" no rótulo.
            price_raw = cell("price")
            price, price_flags = normalize_price(price_raw, unit="brl_per_share")
            # IV da grade é fração por contrato F10 (P3 captura sem normalizar).
            iv_raw = cell("iv")
            iv, iv_flags = normalize_iv(iv_raw, "vi_ratio", known_fraction=True)
            result["series"].append(
                {
                    "match": match,
                    "underlying": str(entry.get("ticker") or "").upper() or None,
                    "raw": dict(row) if isinstance(row, dict) else list(row),
                    "columns": list(columns),
                    "side": side,
                    "style": cell("style"),
                    "price_raw": price_raw,
                    "price_unit": "brl_per_share",
                    "price_normalized": price,
                    "iv_raw": iv_raw,
                    "iv_normalized": iv,
                    "delta": ptbr_float(cell("delta")),
                    "gamma": ptbr_float(cell("gamma")),
                    "theta_raw": ptbr_float(cell("theta")),
                    "vega_raw": ptbr_float(cell("vega")),
                    "flags": list(match["flags"]) + price_flags + iv_flags,
                    "artifact": {
                        "artifact": entry.get("artifact"),
                        "sha256": entry.get("sha256"),
                        "url": entry.get("url"),
                        "accessed_at": entry.get("accessed_at"),
                        "source": entry.get("source"),
                    },
                }
            )
    notes.append(
        "opcoes.net.br: colunas resolvidas pelo cabeçalho do DataTable; IV bruta "
        "é fração e virou pontos percentuais; 'Último' é R$/ação por contrato F10 "
        "(unidade explícita, não depende do rótulo conter 'R$'). Cabeçalho "
        "VH/CALLs/PUTs (Última, IV Rank, Percentil, Mín, Máx) preservado à parte, "
        "com metodologia ainda não confirmada."
    )
    return result


def parse_b3lab(info: SourceInfo) -> dict[str, Any]:
    """P4: séries com inputs/outputs brutos; nenhum output é recalculado aqui."""
    result: dict[str, Any] = {"series": [], "skipped": []}
    for entry in info.artifacts:
        if entry.get("status") != "ok":
            continue
        record = entry.get("record")
        if not isinstance(record, dict):
            continue
        inputs = record.get("inputs")
        b3lab = record.get("b3lab") if isinstance(record.get("b3lab"), dict) else {}
        outputs = b3lab.get("outputs") if isinstance(b3lab.get("outputs"), dict) else {}
        if not isinstance(inputs, dict) or not outputs:
            result["skipped"].append({"file": entry.get("file"), "reason": "outputs_missing"})
            continue
        match = make_match_row(
            ticker=str(inputs.get("ticker") or ""),
            maturity=str(inputs.get("maturity") or "")[:10],
            strike_value=inputs.get("strike") or inputs.get("K"),
            side=inputs.get("side") or inputs.get("SIDE_LABEL"),
        )
        if match["strike_cents"] is None or not match["ticker"]:
            result["skipped"].append({"file": entry.get("file"), "reason": "identity_invalid"})
            continue
        result["series"].append(
            {
                "match": match,
                "underlying": record.get("underlying"),
                "inputs": inputs,
                "outputs": outputs,
                "raw": record,
                "flags": list(b3lab.get("flags") or []),
                "artifact": {
                    "file": entry.get("file"),
                    "sha256": entry.get("sha256"),
                    "url": record.get("url"),
                    "accessed_at": record.get("accessed_at"),
                    "source": "b3lab",
                },
            }
        )
    return result


def extract_structure_external(record: Mapping[str, Any]) -> dict[str, Any]:
    """Lê SOMENTE ``obm.outputs.recalc.response.text`` validado (P5).

    As respostas em ``obm.outputs.api_responses`` incluem o auto-recálculo do
    preset antigo e não são usadas: só o ``recalc`` disparado com os inputs da
    planilha P5 é fonte legítima. O bruto do texto e o ``post_data`` ficam no
    resultado; cada ausência vira ``null`` + flag/motivo, nunca zero.
    """
    result: dict[str, Any] = {
        "values": {},
        "lists": {},
        "raw_values": {},
        "raw": {},
        "flags": ["source_recalc_response_text_only"],
        "reason": None,
        "recalc_meta": {},
    }
    obm = record.get("obm") if isinstance(record.get("obm"), Mapping) else {}
    status = obm.get("status")
    if status == "unsupported_external":
        result["flags"].append("unsupported_external")
        result["reason"] = obm.get("reason")
        return result
    if status == "no_public_single_leg_control":
        result["flags"].append("no_public_single_leg_control")
        result["reason"] = obm.get("reason")
        return result
    if status in (None, "null", "montador_fill_failed"):
        result["flags"].append(f"external_{status or 'missing'}")
        result["reason"] = obm.get("reason") or "sem captura externa utilizável"
        return result
    outputs = obm.get("outputs") if isinstance(obm.get("outputs"), Mapping) else {}
    recalc = outputs.get("recalc") if isinstance(outputs.get("recalc"), Mapping) else {}
    response = recalc.get("response") if isinstance(recalc.get("response"), Mapping) else {}
    validation = (
        recalc.get("validation") if isinstance(recalc.get("validation"), Mapping) else {}
    )
    request = recalc.get("request") if isinstance(recalc.get("request"), Mapping) else {}
    text = str(response.get("text") or "")
    post_data = request.get("post_data")
    result["raw"] = {
        "response_status": response.get("status"),
        "post_data": post_data,
        "recalc_response_text": text[:4000],
    }
    result["recalc_meta"] = {
        "source": "obm.outputs.recalc.response.text",
        "response_status": response.get("status"),
        "post_data": post_data,
        "be_source": validation.get("be_source"),
        "max_source": validation.get("max_source"),
        "be_tolerance_brl": validation.get("be_tolerance_brl"),
        "breakevens_from_obm_grid": validation.get("breakevens_from_obm_grid"),
        "expected": validation.get("expected"),
        "got": validation.get("got"),
        "validation_ok": validation.get("ok"),
        "problems": validation.get("problems"),
    }
    if validation.get("ok") is not True:
        # O recálculo que falhou na validação não é fonte de número: o bruto e
        # os metadados ficam, os valores saem null com flag explícita.
        result["flags"].append("external_recalc_validation_failed")
        result["reason"] = "recalc.validation.ok não é true"
        return result
    if response.get("status") != 200 or not text:
        result["flags"].append("external_recalc_response_invalid")
        result["reason"] = "recalc.response sem status 200 ou sem texto validado"
        return result
    try:
        node = json.loads(text)
    except ValueError:
        result["flags"].append("external_recalc_response_bad_json")
        result["reason"] = "recalc.response.text não é JSON"
        return result
    if not isinstance(node, Mapping):
        result["flags"].append("external_recalc_response_bad_json")
        result["reason"] = "recalc.response.text não é objeto JSON"
        return result
    for metric, source_field in STRUCTURE_FIELD_SOURCE.items():
        value = node.get(source_field)
        result["raw_values"][metric] = value
        if metric == "breakeven_brl":
            if isinstance(value, list):
                numbers = [ptbr_float(item) for item in value]
                if numbers and all(number is not None for number in numbers):
                    result["lists"][metric] = [float(number) for number in numbers]
                else:
                    result["flags"].append("external_breakevens_unreadable")
            else:
                result["flags"].append("external_breakevens_absent")
            continue
        result["raw_values"][metric] = value
        number = ptbr_float(value)
        if number is None:
            result["flags"].append(f"external_{source_field}_absent")
        else:
            # Bruto preservado: net_cost/max_* são TOTAIS para qty=100 e
            # max_loss chega negativo. A normalização é feita na comparação.
            result["values"][metric] = number
    greeks = node.get("greeks")
    if not isinstance(greeks, Mapping) or not greeks:
        result["flags"].append("external_greeks_absent")
    else:
        for metric in STRUCTURE_GREEK_FIELDS:
            greek_field = (
                "theta"
                if metric == "theta_brl"
                else "vega"
                if metric == "vega_brl"
                else metric
            )
            result["raw_values"][metric] = greeks.get(greek_field)
            number = ptbr_float(greeks.get(greek_field))
            if number is None:
                result["flags"].append(f"external_greek_{greek_field}_absent")
            else:
                result["values"][metric] = number
    if validation.get("be_source") == "grade_do_obm":
        result["flags"].append("be_source_grade_do_obm")
    if validation.get("max_source") == "grade_do_obm":
        result["flags"].append("max_source_grade_do_obm")
    return result


# --------------------------------------------------------------------------- #
# Recalculados (P7)                                                            #
# --------------------------------------------------------------------------- #


def recalc_row(
    *,
    underlying: str,
    metric: str,
    computed: Any,
    flags: Sequence[str],
    artifact: Any,
    internal_value: Any = None,
    internal_endpoint: str | None = None,
    internal_flags: Sequence[str] = (),
    external_normalized: float | None = None,
    external_raw: Any = None,
) -> dict[str, Any]:
    """Recalculado com lado interno explícito e diferença ``externo − interno``."""
    computed_value = computed.value if hasattr(computed, "value") else computed
    value = as_float(computed_value) if external_normalized is None else external_normalized
    raw = computed_value if external_raw is None else external_raw
    row_flags = list(flags) + [
        str(item) for item in getattr(computed, "flags", ())
    ]
    if value is None:
        row_flags.append("unavailable")
    internal_number = as_float(internal_value)
    if internal_number is None:
        row_flags.append("internal_unavailable")
    row_flags.extend(str(item) for item in internal_flags)
    difference = (
        value - internal_number
        if value is not None and internal_number is not None
        else None
    )
    return {
        "kind": "recalculated",
        "source": "bench_recalc",
        "underlying": underlying,
        "metric": metric,
        "external": {
            "raw": raw,
            "normalized": value,
            "flags": sorted(set(row_flags)),
        },
        "internal": {
            "raw": internal_value,
            "normalized": internal_number,
            "endpoint": internal_endpoint,
        },
        "difference": difference,
        "artifact": artifact,
        "flags": sorted(set(row_flags)),
    }


def _vrp_history_series(
    bars: Sequence[recalc.OhlcBar],
    points: Sequence[Mapping[str, Any]],
    ex_dates: set[date],
    today_vrp: float | None,
    session: str | None = None,
) -> list[float] | None:
    """IV−RV30 por data real (VI histórico + OHLC), nunca 252 VRPs constantes.

    Quando ``points`` já traz a sessão corrente, o VRP **desse ponto** é
    substituído por ``today_vrp`` (sem duplicata); se não traz, ``today_vrp`` é
    acrescentado ao fim. A série termina sempre em hoje, uma única vez.
    """
    if not points or today_vrp is None:
        return None
    ordered = sorted(bars, key=lambda bar: bar.refdate)
    series: list[float] = []
    replaced_today = False
    for point in points:
        try:
            day = date.fromisoformat(str(point.get("date") or "")[:10])
        except ValueError:
            return None
        window = [bar for bar in ordered if bar.refdate <= day]
        if len(window) < 30:
            return None
        realized = recalc.rv30(
            window, ex_div_dates={item for item in ex_dates if item <= day}
        )
        if realized.value is None:
            return None
        if session is not None and day.isoformat() == session:
            series.append(today_vrp)
            replaced_today = True
        else:
            series.append(float(point["iv_pct"]) - realized.value)
    if not replaced_today:
        series.append(today_vrp)
    return series if len(series) >= recalc.Z_WINDOW else None


def build_recalculated(
    *,
    underlying: str,
    obm: dict[str, Any],
    screen_row: Mapping[str, Any] | None,
    external_iv_30: float | None,
    artifact: Any,
    session: str,
    smile_maturity: str | None = None,
    forward_ref: float | None = None,
    delta_points: Sequence[tuple[Any, Any]] = (),
) -> list[dict[str, Any]]:
    """Aplica P7 ao bruto; ``du = 30`` (T = 30/252) e histórico VRP real.

    ``session`` é a data de referência do lake: data futura do VI entra no
    rank/percentil e no z-sem VRP duplicado; proventos anteriores à sessão ou
    além de 30 DU ficam fora do carry do forward.
    """
    screen = screen_row if isinstance(screen_row, Mapping) else {}
    endpoint = (
        "/api/t/{ticker}?sections=summary,term,smile,history&days=252"
    )
    bars = sorted(obm.get("ohlc") or [], key=lambda bar: bar.refdate)
    # O chamador já entrega só o deste papel; a checagem aqui é a última guarda:
    # ticker declarado diferente não entra, sem ticker entra na flag e não entra.
    dividends: list[dict[str, Any]] = []
    ticker_flags: list[str] = []
    for item in obm.get("dividends") or []:
        if not isinstance(item, dict):
            continue
        declared = str(item.get("ticker") or "").strip().upper()
        if not declared:
            if "dividend_ticker_unavailable" not in ticker_flags:
                ticker_flags.append("dividend_ticker_unavailable")
            continue
        if declared != underlying:
            continue
        dividends.append(item)
    ex_dates: set[date] = set()
    for item in dividends:
        try:
            ex_dates.add(date.fromisoformat(str(item.get("date"))[:10]))
        except (TypeError, ValueError):
            continue
    # Janela de 30 dias úteis do carry: só proventos da sessão em diante, com
    # `du` quando declarado. Provento sem `du` é mantido (e sinalizado) para não
    # descartar dado silenciosamente — o forward fica None + dividend_du_unavailable.
    session_day = date.fromisoformat(session) if DATE_ISO.match(session) else None
    carry_dividends: list[dict[str, Any]] = []
    for item in dividends:
        try:
            day = date.fromisoformat(str(item.get("date"))[:10])
        except (TypeError, ValueError):
            continue
        if session_day is not None and day < session_day:
            continue
        du = item.get("du")
        if is_number(du) and int(du) > 30:
            continue
        carry_dividends.append(item)
    rows: list[dict[str, Any]] = []

    def aggregate_value(names: set[str]) -> tuple[Any, str | None]:
        for bucket in obm.get("aggregates") or []:
            entry_artifact = bucket.get("artifact") or {}
            if str(entry_artifact.get("ticker") or "").upper() != underlying:
                continue
            values = bucket.get("values") if isinstance(bucket.get("values"), dict) else {}
            for name, value in values.items():
                if key_of(name) in names:
                    return value, str(name)
        return None, None

    # 1. RV Yang-Zhang 20/30/60 com datas ex do provento excluídas da janela.
    for window, function in ((20, recalc.rv20), (30, recalc.rv30), (60, recalc.rv60)):
        if len(bars) < window:
            computed: Any = None
            flags = ["ohlc_unavailable", "insufficient_history"] + ticker_flags
        else:
            computed = function(bars, ex_div_dates=ex_dates)
            flags = list(ticker_flags)
        rows.append(
            recalc_row(
                underlying=underlying,
                metric=f"rv{window}",
                computed=computed,
                flags=flags,
                artifact=artifact,
                internal_value=screen.get(f"rv{window}"),
                internal_endpoint=endpoint,
            )
        )

    # 2. Pregão dominante: sharé em % para comparar com rv_shock_share_pct.
    if len(bars) >= 10:
        shock = recalc.rv_shock_day(bars, 30, ex_div_dates=ex_dates)
        share_pct = shock.share * 100.0 if shock.share is not None else None
        shock_raw = {
            "date": shock.refdate.isoformat() if shock.refdate else None,
            "share": shock.share,
            "share_pct": share_pct,
            "n_returns": shock.n_returns,
            "is_shock": shock.is_shock,
        }
        rows.append(
            recalc_row(
                underlying=underlying,
                metric="rv_shock_day",
                computed=shock_raw,
                external_normalized=share_pct,
                external_raw=shock_raw,
                flags=list(shock.flags) + ticker_flags,
                artifact=artifact,
                internal_value=screen.get("rv_shock_share_pct"),
                internal_endpoint=endpoint,
            )
        )
    else:
        rows.append(
            recalc_row(
                underlying=underlying,
                metric="rv_shock_day",
                computed=None,
                flags=["ohlc_unavailable", "insufficient_returns"] + ticker_flags,
                artifact=artifact,
                internal_value=screen.get("rv_shock_share_pct"),
                internal_endpoint=endpoint,
            )
        )

    points = sorted(
        (
            item
            for item in (obm.get("vi_history") or [])
            if isinstance(item, dict) and is_number(item.get("iv_pct"))
        ),
        key=lambda item: str(item.get("date") or "")[:10],
    )
    # Rank/percentil usam OS 252 pontos VI históricos como vieram de
    # `vi-historico` (o último já é a sessão, 33,159631% na PETR4). O IV ATM 30d
    # do agregado NÃO substitui o ponto de hoje: são construções diferentes
    # (o agregado interpola em variância total para 30 dias corridos).
    iv_points = [dict(item) for item in points]
    iv_values = [float(item["iv_pct"]) for item in iv_points]
    vi_history_info = {
        "n_points": len(iv_points),
        "first_date": str(iv_points[0].get("date"))[:10] if iv_points else None,
        "last_date": str(iv_points[-1].get("date"))[:10] if iv_points else None,
        "last_iv_pct": iv_values[-1] if iv_values else None,
        "today_aggregate_iv_atm_30d": external_iv_30,
        "flags": ["rank_uses_raw_vi_history", "no_iv30_replacement"],
    }
    rv30_value = next(
        (
            row["external"]["normalized"]
            for row in rows
            if row["metric"] == "rv30" and row["external"]["normalized"] is not None
        ),
        None,
    )

    # 3. VRP no dia = IV ATM externa − RV30 recalculada.
    vrp_result = (
        recalc.vrp(external_iv_30, rv30_value)
        if external_iv_30 is not None and rv30_value is not None
        else None
    )
    rows.append(
        recalc_row(
            underlying=underlying,
            metric="vrp",
            computed=vrp_result if vrp_result is not None else None,
            flags=["missing_input"] if vrp_result is None else [],
            artifact=artifact,
            internal_value=screen.get("vrp"),
            internal_endpoint=endpoint,
        )
    )

    # 4. Rank/percentil 252 dos VI históricos (já em % a.a.).
    if len(iv_values) >= recalc.Z_WINDOW:
        rank = recalc.iv_rank_252(iv_values)
        percentile = recalc.iv_percentile_252(iv_values)
    else:
        rank = percentile = None
    for metric, computed in (("iv_rank_252", rank), ("iv_pct_252", percentile)):
        row = recalc_row(
            underlying=underlying,
            metric=metric,
            computed=computed,
            flags=(
                []
                if computed is not None
                else ["vi_history_unavailable", "insufficient_history"]
            ),
            artifact=artifact,
            internal_value=screen.get(metric),
            internal_endpoint=endpoint,
        )
        row["external"]["vi_history"] = vi_history_info
        rows.append(row)

    # 5. Z-score do VRP com histórico REAL IV−RV30 por data (nunca constante).
    today_vrp = vrp_result.value if vrp_result is not None else None
    vrp_history = _vrp_history_series(bars, iv_points, ex_dates, today_vrp, session=session)
    zscore = (
        recalc.vrp_z(vrp_history)
        if vrp_history is not None and len(vrp_history) >= recalc.Z_WINDOW
        else None
    )
    z_row = recalc_row(
        underlying=underlying,
        metric="vrp_z_252",
        computed=zscore,
        flags=(
            []
            if zscore is not None
            else (
                ["vrp_history_unavailable"]
                + (["insufficient_history"] if vrp_history is None else [])
            )
        )
        + ticker_flags,
        artifact=artifact,
        internal_value=screen.get("vrp_z_252"),
        internal_endpoint=endpoint,
    )
    z_row["external"]["vrp_history"] = {
        "method": "IV−RV30 por data (VI histórico + Yang-Zhang do OHLC OBM)",
        "n_points": len(vrp_history) if vrp_history is not None else 0,
        "last_value": vrp_history[-1] if vrp_history else None,
        "constant_series": False,
    }
    rows.append(z_row)

    # 6. RR25/BFLY25 dos pontos 25Δ do vencimento smile do OBM.
    smile_points = [
        recalc.DeltaPoint(delta=float(delta), iv_pct=float(iv))
        for delta, iv in delta_points
        if is_number(delta) and is_number(iv)
    ]
    skew = recalc.rr25_bfly25(smile_points, external_iv_30)
    for metric, value in (("rr25", skew.rr25), ("bfly25", skew.bfly25)):
        rows.append(
            recalc_row(
                underlying=underlying,
                metric=metric,
                computed=value,
                flags=list(skew.flags),
                artifact=artifact,
                internal_value=screen.get(metric),
                internal_endpoint=endpoint,
            )
        )

    # 7. Estrutura a termo: IV ATM 90d da agregação externa.
    iv_90, iv_90_field = aggregate_value({"ivatm90dratio", "ivatm90d"})
    iv_90_normalized, iv_90_flags = normalize_iv(iv_90, iv_90_field or "iv_atm_90d_ratio")
    rows.append(
        recalc_row(
            underlying=underlying,
            metric="ts_slope_30_90",
            computed=recalc.ts_slope_30_90(external_iv_30, iv_90_normalized),
            flags=iv_90_flags,
            artifact=artifact,
            internal_value=screen.get("ts_slope_30_90"),
            internal_endpoint=endpoint,
        )
    )

    # 8. Curva ETTJ/ANBIMA: du = 30 (T = 30/252), nunca 21.
    curve = [(int(du), float(rate)) for du, rate in (obm.get("curve") or [])]
    du_30 = 30
    spot = next(
        (float(bar.close) / 100.0 for bar in reversed(bars) if bar.close > 0),
        None,
    )
    if curve:
        df = recalc.discount_factor_from_curve(du_30, curve)
        rows.append(
            recalc_row(
                underlying=underlying,
                metric="df_30d",
                computed=df,
                flags=["df_internal_api_missing"],
                artifact=artifact,
                internal_value=None,
                internal_endpoint="/api/structure (df)",
            )
        )
        missing_du = [item for item in carry_dividends if item.get("du") is None]
        dividend_flags = ["dividend_du_unavailable"] if missing_du else []
        usable = [
            recalc.Dividend(du=int(item["du"]), amount_brl=float(item["amount_brl"]))
            for item in carry_dividends
            if item.get("du") is not None and int(item["du"]) > 0
        ]
        carry = (
            recalc.forward_by_carry(spot, curve, du_30 / 252.0, usable)
            if spot is not None and not missing_du
            else None
        )
        forward_flags = (
            ticker_flags
            + ([] if spot is not None else ["ohlc_unavailable"])
            + dividend_flags
            + (list(carry.flags) if carry is not None else [])
        )
        rows.append(
            recalc_row(
                underlying=underlying,
                metric="forward_30d",
                computed=carry.forward if carry is not None else None,
                flags=forward_flags,
                artifact=artifact,
                internal_value=screen.get("forward_30d"),
                internal_endpoint=endpoint,
            )
        )
        rate = carry.rate_pct if carry is not None else None
        implied = (
            recalc.implied_div_yield_pct(rate, carry.forward, spot, du_30 / 252.0)
            if carry is not None
            and carry.forward is not None
            and spot is not None
            else None
        )
        rows.append(
            recalc_row(
                underlying=underlying,
                metric="implied_div_yield_30d",
                computed=implied,
                flags=forward_flags if implied is None else list(ticker_flags),
                artifact=artifact,
                internal_value=screen.get("implied_div_yield_30d"),
                internal_endpoint=endpoint,
            )
        )
    else:
        for metric in ("df_30d", "forward_30d", "implied_div_yield_30d"):
            rows.append(
                recalc_row(
                    underlying=underlying,
                    metric=metric,
                    computed=None,
                    flags=["ettj_unavailable"],
                    artifact=artifact,
                    internal_value=screen.get(metric),
                    internal_endpoint=endpoint,
                )
            )

    # 9. Dias para liquidar: OI / média de quantidade (ações) de exatamente 20
    # sessões. Quantidades repetidas na mesma data contam uma vez; com menos de
    # 20 sessões o denominador é parcial e o resultado é None + flag.
    oi, _ = aggregate_value({"oi", "openinterest"})
    # `latest_positions` é estoque por série e NÃO é o OI atual do papel: nunca
    # é usado como substituto silencioso quando `latest.oi` falta.
    oi_flags: list[str] = []
    if oi is None:
        oi_flags.extend(["oi_unavailable", "latest_positions_not_used"])
    qty_by_day: dict[str, float] = {}
    for item in (obm.get("ohlc_qty") or []):
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")[:10]
        qty = as_float(item.get("qty"))
        if DATE_ISO.match(day) and qty is not None:
            qty_by_day.setdefault(day, qty)
    recent_quantities = [qty_by_day[day] for day in sorted(qty_by_day)[-20:]]
    if len(recent_quantities) == 20:
        average_quantity = math.fsum(recent_quantities) / 20.0
        quantity_flags: list[str] = []
    else:
        average_quantity = None
        quantity_flags = ["quantity_20d_unavailable"]
    dtl = recalc.days_to_liquidate(
        int(oi) if is_number(oi) else None, average_quantity
    )
    rows.append(
        recalc_row(
            underlying=underlying,
            metric="days_to_liquidate",
            computed=dtl,
            flags=oi_flags + quantity_flags,
            artifact=artifact,
            internal_value=screen.get("days_to_liquidate"),
            internal_endpoint=endpoint,
        )
    )

    # 10. Expected move: strike casado call+put mais perto do spot/forward do smile.
    matched: dict[int, dict[str, float]] = defaultdict(dict)
    for item in obm.get("series") or []:
        if str(item.get("underlying") or "").upper() != underlying:
            continue
        if smile_maturity is not None:
            maturity = (item.get("match") or {}).get("maturity")
            if maturity != smile_maturity:
                continue
        cents = (item.get("match") or {}).get("strike_cents")
        price = item.get("price_normalized")
        if cents is not None and price is not None:
            matched[int(cents)][item["match"]["side"]] = float(price)
    pairs = [
        (cents, values["C"] + values["P"])
        for cents, values in matched.items()
        if "C" in values and "P" in values
    ]
    reference_flags: list[str] = []
    reference_source = "internal_forward_30d"
    reference = forward_ref
    if reference is None:
        reference = spot
        reference_source = "spot_fallback"
        reference_flags.append("forward_30d_unavailable_spot_fallback")
    closest = (
        min(pairs, key=lambda pair: abs(pair[0] / 100.0 - reference))
        if pairs and reference is not None
        else None
    )
    straddle = closest[1] if closest is not None else None
    exp_row = recalc_row(
        underlying=underlying,
        metric="exp_move_1s_30d",
        computed=(
            recalc.expected_move_1s(straddle, spot)
            if straddle is not None and spot is not None
            else None
        ),
        flags=(
            []
            if straddle is not None and spot is not None
            else ["straddle_unavailable", "ohlc_unavailable"]
        )
        + reference_flags,
        artifact=artifact,
        internal_value=screen.get("exp_move_1s_30d"),
        internal_endpoint=endpoint,
    )
    exp_row["external"]["reference"] = {
        "source": reference_source,
        "forward_30d_internal": forward_ref,
        "spot_brl": spot,
        "used": reference,
        "matched_strike_cents": closest[0] if closest is not None else None,
        "straddle_brl": straddle,
    }
    rows.append(exp_row)
    return rows

# --------------------------------------------------------------------------- #
# Construção das linhas de comparação                                          #
# --------------------------------------------------------------------------- #


def series_row(
    *,
    source: str,
    metric: str,
    match: Mapping[str, Any],
    method: str,
    flags: Sequence[str],
    external_raw: Any,
    external_normalized: float | None,
    internal_raw: Any,
    internal_normalized: float | None,
    internal_endpoint: str,
    artifact: Any,
    external_unit: str | None = None,
    internal_unit: str | None = None,
    reason: str | None = None,
    internal_provenance: Mapping[str, Any] | None = None,
    external_inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    difference = (
        external_normalized - internal_normalized
        if external_normalized is not None and internal_normalized is not None
        else None
    )
    row_flags = list(flags)
    if external_normalized is None and reason is None:
        row_flags.append("external_normalization_unavailable")
    if internal_normalized is None and reason is None:
        row_flags.append("internal_normalization_unavailable")
    return {
        "kind": "series",
        "source": source,
        "metric": metric,
        "ticker": match.get("ticker"),
        "underlying": match.get("underlying"),
        "maturity": match.get("maturity"),
        "side": match.get("side"),
        "strike_cents_internal": match.get("strike_cents_internal"),
        "strike_cents_external": match.get("strike_cents_external"),
        "match": dict(match.get("keys") or {}),
        "match_method": method,
        "flags": sorted(set(row_flags)),
        "external": {
            "raw": external_raw,
            "normalized": external_normalized,
            "unit": external_unit,
        },
        "internal": {
            "raw": internal_raw,
            "normalized": internal_normalized,
            "endpoint": internal_endpoint,
            "unit": internal_unit,
            "provenance": dict(internal_provenance or {}),
        },
        "external_inputs": dict(external_inputs or {}),
        "difference": difference,
        "artifact": artifact,
        "reason": reason,
    }


def internal_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """Escalares internos por métrica, com bruto e proveniência — nunca o payload.

    As linhas de preço/IV carregam só o escalar bruto e de onde ele veio; o
    payload inteiro de ``/api/s`` fica fora do JSON intermediário (P6-A o
    embutia em ``internal.raw``). No modelo 3, gamma já é por R$1 e entra direto;
    a API não expõe rho, que fica ``null`` + flag.
    """
    last = data.get("last") if isinstance(data.get("last"), dict) else {}
    close = as_float(last.get("close_brl"))
    mid = as_float(last.get("mid_brl"))
    price_flags: list[str] = []
    price_field = "last.close_brl"
    price = close
    if close is None and mid is not None:
        price = mid
        price_field = "last.mid_brl"
        price_flags.append("price_from_mid")
    if price is None:
        price_flags.append("price_missing")
    gamma = as_float(data.get("gamma"))
    endpoint = "/api/s/{ticker}/{maturity}"
    provenance = {
        "price": {"field": price_field, "endpoint": endpoint},
        "iv": {"field": "last.iv_pct", "endpoint": endpoint},
        "delta": {"field": "last.delta", "endpoint": endpoint},
        "gamma": {"field": "gamma", "endpoint": endpoint},
        "theta": {"field": "theta_brl", "endpoint": endpoint},
        "vega": {"field": "vega_brl", "endpoint": endpoint},
        "rho": {"field": None, "endpoint": endpoint, "reason": "internal_api_missing_rho"},
    }
    return {
        "price": (price, price_flags),
        "iv": (as_float(last.get("iv_pct")), []),
        "delta": (as_float(last.get("delta")), []),
        "gamma": (gamma, [] if gamma is not None else ["invalid_gamma"]),
        "theta": (as_float(data.get("theta_brl")), []),
        "vega": (as_float(data.get("vega_brl")), []),
        "rho": (None, ["internal_api_missing_rho"]),
        "raw": {
            "price": close if close is not None else mid,
            "iv": last.get("iv_pct"),
            "delta": last.get("delta"),
            "gamma": gamma,
            "theta": data.get("theta_brl"),
            "vega": data.get("vega_brl"),
            "rho": None,
        },
        "provenance": provenance,
    }


def external_values(
    item: Mapping[str, Any], source: str
) -> dict[str, tuple[Any, list[str], Any]]:
    """``(normalizado, flags, bruto)`` por métrica — o bruto nunca é o normalizado."""
    flags_all = list(item.get("flags") or [])
    if source == "obm":
        return {
            "price": (
                item.get("price_normalized"),
                flags_all,
                item.get("price_raw", item.get("price")),
            ),
            "iv": (
                item.get("iv_normalized"),
                flags_all,
                item.get("iv_raw", item.get("iv")),
            ),
            "delta": (item.get("delta_signed", item.get("delta")), flags_all, item.get("delta")),
            "gamma": (ptbr_float(item.get("gamma")), flags_all, item.get("gamma")),
            "theta": (None, flags_all + ["unit_unknown"], item.get("theta")),
            "vega": (None, flags_all + ["unit_unknown"], item.get("vega")),
            "rho": (ptbr_float(item.get("rho")), flags_all, item.get("rho")),
        }
    if source == "opcoes_net":
        return {
            "price": (
                item.get("price_normalized"),
                flags_all,
                item.get("price_raw", item.get("price")),
            ),
            "iv": (
                item.get("iv_normalized"),
                flags_all,
                item.get("iv_raw", item.get("iv")),
            ),
            "delta": (item.get("delta"), flags_all, item.get("delta")),
            "gamma": (item.get("gamma"), flags_all, item.get("gamma")),
            "theta": (None, flags_all + ["unit_unknown"], item.get("theta_raw")),
            "vega": (None, flags_all + ["unit_unknown"], item.get("vega_raw")),
            "rho": (None, flags_all + ["external_rho_unavailable"], None),
        }
    inputs = item.get("inputs") if isinstance(item.get("inputs"), dict) else {}
    outputs = item.get("outputs") if isinstance(item.get("outputs"), dict) else {}
    theta_raw = outputs.get("theta")
    vega_raw = outputs.get("vega")
    # B3Lab: θ é por dia corrido (×365/252 só como candidato); ν e ρ por 1%.
    # A base tem que ser explícita: `business_days` nos inputs NÃO torna o
    # θ exibido "por dia útil" (F10.1 calibrado em 25/11).
    theta_basis = {"day_basis": "calendar_day", "business_days": inputs.get("business_days")}
    vega_basis = {"vega_basis": "per_pct", "sigma_pct": inputs.get("sigma_pct")}
    theta, theta_flags, _ = normalize_theta(ptbr_float(theta_raw), theta_basis)
    vega, vega_flags, _ = normalize_vega(ptbr_float(vega_raw), vega_basis)
    return {
        "price": (
            ptbr_float(outputs.get("preco")),
            flags_all + ["theoretical_price_not_close"],
            outputs.get("preco"),
        ),
        "iv": (
            None,
            flags_all + ["b3lab_iv_is_input_not_extracted"],
            inputs.get("sigma"),
        ),
        "delta": (ptbr_float(outputs.get("delta")), flags_all, outputs.get("delta")),
        "gamma": (ptbr_float(outputs.get("gamma")), flags_all, outputs.get("gamma")),
        "theta": (
            theta,
            flags_all + theta_flags + ["theta_b3lab_calendar_day_candidate_x365_252"],
            theta_raw,
        ),
        "vega": (vega, flags_all + vega_flags + ["vega_b3lab_per_1pct"], vega_raw),
        "rho": (
            ptbr_float(outputs.get("rho")),
            flags_all
            + [
                "internal_api_missing_rho",
                "rho_b3lab_partial_derivative_fixed_q",
                "rho_b3lab_per_1pct",
            ],
            outputs.get("rho"),
        ),
    }


def build_series_comparisons(
    *,
    source: str,
    external_series: Sequence[Mapping[str, Any]],
    internal_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    notes: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    internal_rows: list[dict[str, Any]] = []
    for (ticker, maturity), detail in sorted(internal_by_key.items()):
        for item in detail.get("series", []):
            row = make_match_row(
                ticker=ticker,
                maturity=maturity,
                strike_value=item.get("strike_brl"),
                side=item.get("side"),
            )
            row["underlying"] = detail.get("underlying")
            internal_rows.append(row)
    external_rows = [
        dict(item["match"], underlying=item.get("underlying"), raw=item.get("raw"))
        for item in external_series
        if isinstance(item.get("match"), dict)
    ]
    matches, skipped = match_rows(internal_rows, external_rows)
    rows: list[dict[str, Any]] = []
    for match in matches:
        inside = match["internal"]
        outside = match["external"]
        detail = internal_by_key.get((inside["ticker"], inside["maturity"]), {})
        internal_payload = None
        for item in detail.get("series", []):
            candidate = make_match_row(
                ticker=inside["ticker"],
                maturity=inside["maturity"],
                strike_value=item.get("strike_brl"),
                side=item.get("side"),
            )
            if candidate["match_key"] == inside["match_key"]:
                internal_payload = item
                break
        if internal_payload is None:
            continue
        values = internal_values(internal_payload)
        source_item = next(
            (
                item
                for item in external_series
                if item.get("match", {}).get("match_key") == outside["match_key"]
            ),
            None,
        )
        if source_item is None:
            continue
        ext = external_values(source_item, source)
        keys = {
            "ticker": inside["ticker"],
            "maturity": inside["maturity"],
            "side": inside["side"],
            "strike_cents": outside["strike_cents"],
        }
        artifact = source_item.get("artifact")
        external_inputs: dict[str, Any] = {}
        if source == "b3lab":
            record = source_item.get("raw") if isinstance(source_item.get("raw"), Mapping) else {}
            inputs = record.get("inputs") if isinstance(record.get("inputs"), Mapping) else {}
            lab = record.get("b3lab") if isinstance(record.get("b3lab"), Mapping) else {}
            lab_inputs = lab.get("inputs") if isinstance(lab.get("inputs"), Mapping) else {}
            external_inputs = {
                "contract_inputs": {
                    "F": inputs.get("S"),
                    "DF": inputs.get("df"),
                    "t": inputs.get("t"),
                    "du": inputs.get("du"),
                    "r_cont": inputs.get("r_cont"),
                    "q_pct": inputs.get("q_pct"),
                    "source": "record.inputs",
                },
                "b3lab_inputs": dict(lab_inputs),
                "flags": ["inputs_preserved", "b3lab_inputs_preserved"],
            }
        raw_scalars = values.get("raw") or {}
        provenance = values.get("provenance") or {}
        # Modelo 3: gamma da API já é variação do delta por R$1 de forward e
        # entra em METRICS direto, comparado com o externo sem nenhum fator.
        # A razão ×100 e o candidato de escala são apenas evidência histórica
        # do modelo 2 (P6-B stale); não são métrica comparada nem campo de saída.
        for metric in METRICS:
            internal_number, internal_flags = values.get(metric, (None, []))
            external_number, external_flags, external_raw_value = ext.get(
                metric, (None, [], None)
            )
            unit_external = "brl_per_share" if metric == "price" else None
            unit_internal = (
                "brl_per_share" if metric in {"price", "gamma", "theta", "vega"} else None
            )
            row = series_row(
                source=source,
                metric=metric,
                match={
                    # Chaves de casamento também no topo da linha: P6-A gravava
                    # `ticker/maturity/side` nulos e só os conservava em `keys`.
                    **keys,
                    "keys": keys,
                    "underlying": outside.get("underlying") or detail.get("underlying"),
                    "strike_cents_internal": inside["strike_cents"],
                    "strike_cents_external": outside["strike_cents"],
                },
                method=match["match_method"],
                flags=list(match["flags"]) + list(internal_flags) + list(external_flags),
                external_raw=external_raw_value,
                external_normalized=as_float(external_number),
                internal_raw=raw_scalars.get(metric, internal_number),
                internal_normalized=as_float(internal_number),
                internal_endpoint="/api/s/{ticker}/{maturity}",
                artifact=artifact,
                external_unit=unit_external,
                internal_unit=unit_internal,
                internal_provenance=provenance.get(metric),
                external_inputs=external_inputs,
            )
            rows.append(row)
    notes.append(
        f"{source}: {len(matches)} séries casadas, {len(skipped)} sem casamento; "
        f"{sum(1 for item in matches if item['match_method'] == 'fallback_unique')} fallback(s)."
    )
    return rows, skipped


def build_underlying_comparisons(
    *,
    screens: Mapping[str, Mapping[str, Any]],
    underlyings: Mapping[str, Mapping[str, Any]],
    aggregates: Sequence[Mapping[str, Any]],
    ohlc: Sequence[recalc.OhlcBar] = (),
    ohlc_sources: Mapping[str, Any] | None = None,
    notes: list[str],
    obm: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if obm is not None:
        # `obm` é o recorte do papel: o spot não pode vir da barra de outro ativo.
        ohlc = obm.get("ohlc") or ()
        ohlc_sources = obm.get("ohlc_sources") or {}
    rows: list[dict[str, Any]] = []
    # Spot externo é o fechamento da última barra OHLC OBM em R$/ação (centavos
    # ÷ 100) — comparação direta com `spot_brl` interno, com artefato declarado.
    # O lote de OHLC pode cobrir vários papéis: filtra pelo ticker do artefato.
    spot_bars = sorted(ohlc, key=lambda bar: bar.refdate)

    def spot_source(
        ticker: str,
    ) -> tuple[recalc.OhlcBar | None, dict[str, Any] | None]:
        candidates: list[tuple[recalc.OhlcBar, dict[str, Any] | None]] = []
        for bar in spot_bars:
            ref = (ohlc_sources or {}).get(bar.refdate.isoformat()) or {}
            ref_ticker = str(ref.get("ticker") or "").upper()
            if ref_ticker and ref_ticker != ticker:
                continue
            if bar.close > 0:
                candidates.append((bar, ref or None))
        return candidates[-1] if candidates else (None, None)

    aggregate_by_ticker: dict[str, dict[str, Any]] = defaultdict(dict)
    for bucket in aggregates:
        values = bucket.get("values") if isinstance(bucket.get("values"), dict) else {}
        artifact = bucket.get("artifact") or {}
        ticker = str(artifact.get("ticker") or "").upper()
        if not ticker:
            continue
        for metric, aliases in OBM_AGGREGATE_ALIASES.items():
            value, field = alias_get(values, aliases)
            if value is None:
                continue
            if metric in {"iv_rank_252", "iv_pct_252", "skew_25d_30d_ratio"}:
                # rank/percentil já são 0–100; skew é razão bruta — nunca IV.
                normalized, flags = as_float(value), []
                if metric == "skew_25d_30d_ratio":
                    flags.append("definition_from_fields")
                    flags.append("not_our_rr25")
            else:
                normalized, flags = normalize_iv(value, str(field or metric))
            aggregate_by_ticker[ticker][metric] = {
                "raw": value,
                "normalized": normalized,
                "flags": flags,
                "unit": "ratio" if metric == "skew_25d_30d_ratio" else None,
            }
            if metric == "skew_25d_30d_ratio":
                aggregate_by_ticker[ticker][metric]["flags"].extend(
                    ["definition_from_fields", "not_our_rr25"]
                )
    for ticker, detail in sorted(underlyings.items()):
        detail_data = detail.get("data") if isinstance(detail.get("data"), dict) else {}
        summary = detail_data.get("summary") if isinstance(detail_data.get("summary"), dict) else {}
        if not isinstance(summary, dict):
            summary = {}
        screen = screens.get(ticker, {})
        spot_bar, spot_ref = spot_source(ticker)
        for metric in UNDERLYING_FIELDS:
            internal = summary.get(metric)
            if internal is None:
                internal = screen.get(metric)
            if internal is None:
                continue
            external = aggregate_by_ticker.get(ticker, {}).get(metric)
            spot_from_ohlc = metric == "spot_brl" and spot_bar is not None
            if spot_from_ohlc:
                assert spot_bar is not None
                external_value = spot_bar.close / 100.0
                external_raw_value: Any = {
                    "refdate": spot_bar.refdate.isoformat(),
                    "close_cents": spot_bar.close,
                    "close_brl": external_value,
                }
                flags = ["obm_ohlc_close"]
                external_artifact: dict[str, Any] | None = {
                    "source": "obm",
                    "ticker": ticker,
                    "kind": "ohlc",
                    **(spot_ref or {}),
                }
            elif external:
                external_value = external["normalized"]
                external_raw_value = external["raw"]
                flags = list(external["flags"])
                external_artifact = {"source": "obm", "ticker": ticker}
            else:
                external_value = None
                external_raw_value = None
                flags = ["external_unavailable"]
                external_artifact = None
            if external_value is None:
                flags.append("external_unavailable")
            rows.append(
                {
                    "kind": "underlying",
                    "source": "obm" if (spot_from_ohlc or external) else None,
                    "metric": metric,
                    "ticker": ticker,
                    "underlying": ticker,
                    "match": {"ticker": ticker},
                    "match_method": (
                        "ticker_session" if (spot_from_ohlc or external) else "internal_only"
                    ),
                    "flags": sorted(set(flags)),
                    "external": {
                        "raw": external_raw_value,
                        "normalized": external_value,
                    },
                    "internal": {
                        "raw": internal,
                        "normalized": as_float(internal),
                        "endpoint": "/api/t/{ticker}?sections=summary,term,smile,history&days=252",
                    },
                    "difference": (
                        external_value - as_float(internal)
                        if external_value is not None and is_number(internal)
                        else None
                    ),
                    "artifact": external_artifact,
                    "reason": None,
                }
            )
    return rows


def build_structure_comparisons(info: SourceInfo, notes: list[str]) -> list[dict[str, Any]]:
    """Compara P5 com o recálculo validado do OBM (nunca com preset antigo).

    * saídas OBM são totais para 100 ações: ``per_share_brl = bruto/100``;
    * ``max_loss`` bruto negativo vira magnitude para comparar com o interno;
    * breakevens mantêm a lista bruta e as diferenças **por raiz**;
    * breakeven/máximo da grade do OBM são metodologia externa (flag), não bug
      interno (ADR-051);
    * put caixa e venda coberta ficam ``null`` + motivo (ADR-051).
    """
    rows: list[dict[str, Any]] = []
    for entry in info.artifacts:
        if entry.get("status") != "ok":
            continue
        record = entry.get("record")
        if not isinstance(record, Mapping):
            continue
        response = record.get("response") if isinstance(record.get("response"), Mapping) else {}
        data = response.get("data") if isinstance(response.get("data"), Mapping) else {}
        if not data:
            continue
        external = extract_structure_external(record)
        # O papel vem de response.data.underlying (PETR4), não do nome da estrutura.
        underlying = str(
            data.get("underlying") or record.get("underlying") or ""
        ).upper() or None
        meta = external["recalc_meta"]
        expected = meta.get("expected") if isinstance(meta.get("expected"), Mapping) else {}
        for metric in (
            "net_premium_brl",
            "breakeven_brl",
            "max_loss_brl",
            "max_gain_brl",
            "delta",
            "gamma",
            "theta_brl",
            "vega_brl",
        ):
            internal = data.get(metric)
            flags = set(external["flags"])
            source_field = STRUCTURE_FIELD_SOURCE.get(metric)
            external_raw = external["raw_values"].get(metric)
            external_list = external["lists"].get(metric)
            external_value = external["values"].get(metric)
            normalized = as_float(external_value)
            internal_normalized = as_float(internal)
            difference: Any = None
            root_differences: list[float] | None = None
            per_share: float | None = None
            if metric == "breakeven_brl":
                normalized = (
                    [float(value) for value in external_list]
                    if external_list is not None
                    else None
                )
                if isinstance(internal, list):
                    internal_list = [as_float(item) for item in internal]
                    if (
                        normalized is not None
                        and len(normalized) == len(internal_list)
                        and all(item is not None for item in internal_list)
                    ):
                        internal_normalized = internal_list
                        root_differences = [
                            external_item - float(internal_item)
                            for external_item, internal_item in zip(
                                normalized, internal_list, strict=True
                            )
                        ]
                        difference = root_differences
                        flags.add("breakeven_list_compared")
                    else:
                        internal_normalized = internal_list
                        difference = None
                        flags.add("breakeven_list_unavailable")
                elif internal is None:
                    flags.add("internal_missing")
            else:
                if metric == "max_loss_brl" and is_number(external_value):
                    # Bruto negativo preservado; normalizado é a magnitude.
                    normalized = abs(float(external_value))
                    flags.add("max_loss_magnitude_normalized")
                    if meta.get("max_source") == "grade_do_obm":
                        flags.add("max_source_grade_do_obm")
                    expected_loss = expected.get("max_loss_brl")
                    if is_number(expected_loss) and abs(
                        abs(float(expected_loss)) - normalized
                    ) > 0.5:
                        flags.add("max_loss_grade_differs_from_analytic")
                if metric in STRUCTURE_GREEK_FIELDS and external_value is None:
                    flags.add("external_greeks_absent")
                if source_field is not None and is_number(external_value):
                    # Nenhuma multiplicação do bruto: só a divisão por qty=100.
                    per_share = float(external_value) / STRUCTURE_QTY_SHARES
                if normalized is not None and internal_normalized is not None:
                    difference = normalized - internal_normalized
                elif internal is None and metric not in STRUCTURE_GREEK_FIELDS:
                    flags.add("internal_missing")
            # Metodologia externa é propriedade da FONTE do número, não da
            # magnitude da diferença: be_tolerance_brl continua em `methodology`
            # para medir o desvio, mas não condiciona a flag (ADR-051/item 25).
            if metric == "breakeven_brl":
                grid_source = meta.get("be_source") == "grade_do_obm"
                grid_difference = root_differences
            elif metric in {"max_loss_brl", "max_gain_brl"}:
                grid_source = meta.get("max_source") == "grade_do_obm"
                grid_difference = difference
            else:
                grid_source = False
                grid_difference = None
            if grid_source and grid_difference is not None:
                flags.add("external_grid_methodology")
            rows.append(
                {
                    "kind": "structure",
                    "source": "obm_montador",
                    "metric": metric,
                    "structure": record.get("name"),
                    "underlying": underlying,
                    "match": {"structure": record.get("name")},
                    "match_method": "p5_record",
                    "flags": sorted(flags),
                    "external": {
                        "raw": external_raw,
                        "normalized": normalized,
                        "raw_total_brl": external_value,
                        "per_share_brl": per_share,
                        "field": source_field,
                        "response_text": external["raw"].get("recalc_response_text"),
                        "breakevens_raw": external_list,
                        "root_differences_brl": root_differences,
                        "methodology": meta,
                    },
                    "internal": {
                        "raw": internal,
                        "normalized": internal_normalized,
                        "endpoint": "/api/structure",
                    },
                    "difference": difference,
                    "artifact": {
                        "file": entry.get("file"),
                        "sha256": entry.get("sha256"),
                        "manifest": info.manifest,
                    },
                    "reason": external.get("reason"),
                }
            )
    notes.append(
        "Estruturas: só `obm.outputs.recalc.response.text` validado é lido (nunca as "
        "respostas de auto-recálculo do preset antigo). Saídas são totais para "
        "100 ações (`per_share_brl = bruto/100`); max_loss negativo é preservado no "
        "bruto e comparado por magnitude; breakevens mantêm lista bruta e diferenças "
        "por raiz. Be/max da grade do OBM levam `external_grid_methodology` — "
        "metodologia externa, não bug interno (ADR-051). Put caixa fica `null` + "
        "`no_public_single_leg_control` e venda coberta `null` + `unsupported_external`; "
        "gregas externas ausentes ficam `null` + motivo."
    )
    return rows


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    unavailable: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        source = str(row.get("source") or "unknown")
        metric = str(row.get("metric") or "unknown")
        difference = row.get("difference")
        if is_number(difference):
            grouped[(source, metric)].append(float(difference))
        else:
            unavailable[(source, metric)] += 1
    by_source: dict[str, dict[str, Any]] = defaultdict(dict)
    for (source, metric), values in sorted(grouped.items()):
        stats = difference_stats(values)
        stats["n_unavailable"] = unavailable.get((source, metric), 0)
        by_source[source][metric] = stats
    for (source, metric), count in sorted(unavailable.items()):
        if (source, metric) not in grouped:
            stats = difference_stats([])
            stats["n_unavailable"] = count
            by_source[source][metric] = stats
    return {
        "by_source": dict(sorted(by_source.items())),
        "n_comparisons": sum(len(values) for values in grouped.values()),
        "n_unavailable": sum(unavailable.values()),
    }


# --------------------------------------------------------------------------- #
# Orquestração e CLI                                                           #
# --------------------------------------------------------------------------- #


def atomic_write_json(path: Path, value: Any) -> None:
    """Escrita atômica com unlink+rename (bind do Windows recusa replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.pending-{os.getpid()}")
    pending.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        if path.exists():
            path.unlink()
        pending.rename(path)
    except OSError as exc:
        pending.unlink(missing_ok=True)
        raise CompareError(
            "write_failed",
            f"não foi possível gravar {path}: {exc}",
            "confira --output e o bind ./data:/data",
        ) from exc


def build_output(args: argparse.Namespace) -> dict[str, Any]:
    """Monta o JSON intermediário; nenhuma etapa inventa dado ausente."""
    notes: list[str] = []
    unavailable: list[dict[str, str]] = []
    output: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": now_iso(),
        "reference_date": args.date,
        "comparison_model_version": None,
        "health_current_session": None,
        "model_version": None,
        "input_dir": str(args.input_dir),
        "api_url": args.api_url,
        "sources": {},
        "series_comparisons": {"obm": [], "opcoes_net": [], "b3lab": []},
        "underlying_comparisons": [],
        "structure_comparisons": [],
        "recalculated": [],
        "health_before": None,
        "health_after": None,
        "opcoes_header_metrics": [],
        "obm_field_definitions": [],
        "obm_skew": [],
        "summary": {},
        "unavailable": unavailable,
        "notes": notes,
    }
    # Guarda zero: /api/health ANTES de qualquer parsing/manifesto/trabalho.
    client = ApiClient(args.api_url)
    health = fetch_health(client)
    guard_health(health, args.date, "health_before")
    output["health_before"] = health["raw"]
    output["reference_date"] = args.date
    output["comparison_model_version"] = health["model_version"]
    output["health_current_session"] = health["session"]
    output["model_version"] = health["model_version"]
    manifests = discover_manifests(args.input_dir, notes, session=args.date)
    # `capture_verified` é só o verificador interno do P3: as fontes publicadas
    # no JSON são `obm` e `opcoes_net`, cada uma com status/artefatos próprios.
    capture_verified = verify_capture(manifests["capture"], args.date)
    obm_info = split_capture(capture_verified, "obm", "obm")
    opcoes_info = split_capture(capture_verified, "opcoes_net", "opcoes_net")
    b3lab_info = verify_b3lab(manifests["b3lab"], args.date)
    structures_info = verify_structures(
        manifests["structures"], args.date, notes
    )
    for info in (obm_info, opcoes_info, b3lab_info, structures_info):
        output["sources"][info.name] = info.as_json()
        if info.status != "ok":
            unavailable.append(
                {
                    "source": info.name,
                    "code": "source_unavailable",
                    "reason": info.reason or "unknown",
                }
            )

    # Guarda de sessão/modelo dos manifests: divergir aborta sem escrever output.
    model = health["model_version"]
    if manifests["capture"] is not None:
        capture_manifest = capture_verified.payload
        capture_path = str(manifests["capture"])
        guard_manifest_session(
            "P3",
            capture_manifest.get("session"),
            args.date,
            capture_path,
            extras=[("requested_date", capture_manifest.get("requested_date"))],
        )
        guard_manifest_model(
            "P3", capture_manifest.get("model_version"), model, capture_path
        )
    if manifests["b3lab"] is not None:
        lab_manifest = b3lab_info.payload
        lab_path = str(manifests["b3lab"])
        guard_manifest_session("P4", lab_manifest.get("reference_date"), args.date, lab_path)
        guard_manifest_model("P4", lab_manifest.get("model_version"), model, lab_path)
    for p5_path in manifests["structures"]:
        p5_manifest = read_json(Path(p5_path), "structures_manifest")
        guard_manifest_session(
            "P5",
            p5_manifest.get("session") or p5_manifest.get("reference_date"),
            args.date,
            str(p5_path),
            extras=[
                ("session", p5_manifest.get("session")),
                ("reference_date", p5_manifest.get("reference_date")),
            ],
        )
        guard_manifest_model(
            "P5", p5_manifest.get("model_version"), model, str(p5_path)
        )

    obm = parse_obm(obm_info, args.date, notes)
    opcoes = parse_opcoes(opcoes_info, notes)
    b3lab = parse_b3lab(b3lab_info)

    # Cabeçalho VH/CALLs/PUTs do opcoes.net.br: descritivo, metodologia NÃO
    # confirmada — não é Yang-Zhang nem ATM equivalente sem evidência.
    output["opcoes_header_metrics"] = opcoes["header_metrics"]
    notes.append(
        "opcoes.net.br VH/CALLs/PUTs (Última, IV Rank, Percentil, Mín, Máx) fica em "
        "`opcoes_header_metrics` como bruto/normalizado por pt-BR, com "
        "`header_methodology_unconfirmed`: não é Yang-Zhang nem ATM equivalente."
    )

    # `fields[]` do agregado OBM é a fonte da definição do skew bruto.
    for bucket in obm["aggregates"]:
        artifact = bucket.get("artifact") or {}
        ticker = str(artifact.get("ticker") or "").upper()
        if not ticker:
            continue
        for definition in bucket.get("fields") or []:
            if not isinstance(definition, Mapping):
                continue
            output["obm_field_definitions"].append(
                {
                    "underlying": ticker,
                    "key": definition.get("key"),
                    "label": definition.get("label"),
                    "description": definition.get("description"),
                }
            )
        values = bucket.get("values") if isinstance(bucket.get("values"), Mapping) else {}
        value, field = alias_get(values, ("skew_25d_30d_ratio",))
        if value is None:
            continue
        description = next(
            (
                str(item.get("description") or "")
                for item in bucket.get("fields") or []
                if isinstance(item, Mapping)
                and key_of(item.get("key") or "") == "skew25d30dratio"
            ),
            None,
        )
        output["obm_skew"].append(
            {
                "underlying": ticker,
                "source_field": field,
                "raw": value,
                "normalized": as_float(value),
                "field_definition": description,
                "flags": [
                    "raw_preserved",
                    "definition_from_fields",
                    "not_our_rr25",
                    "calendar_days_put_put",
                ],
            }
        )
    if output["obm_skew"]:
        notes.append(SKEW_NOTE)
    notes.append(B3LAB_THETA_NOTE)
    notes.append(B3LAB_INPUT_NOTE)

    underlyings: dict[str, str] = {}
    for group in (obm["series"], opcoes["series"], b3lab["series"]):
        for item in group:
            ticker = item.get("match", {}).get("ticker")
            underlying = item.get("underlying")
            if ticker and underlying and isinstance(underlying, str):
                underlyings[ticker] = underlying.upper()
    screen = fetch_screen_all(client, args.date, health["model_version"])
    screens = {
        str(row.get("underlying", "")).upper(): row
        for row in screen["rows"]
        if isinstance(row, dict) and row.get("underlying")
    }
    output["sources"]["api"] = {
        "status": "ok",
        "reason": None,
        "session": health["session"],
        "reference_date": args.date,
        "model_version": health["model_version"],
        "screen": {"total": screen["total"], "rows": len(screen["rows"]), "pages": screen["pages"]},
        "endpoints": [
            "/api/health",
            "/api/screen?date={reference}",
            "/api/t/{ticker}?date={reference}&sections=summary,term,smile,history&days=252",
            "/api/s/{ticker}/{maturity}?date={reference}",
        ],
    }

    internal_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    pairs = sorted(
        {
            (ticker, str(item.get("match", {}).get("maturity") or "")[:10])
            for ticker, item in (
                (t, s)
                for t, group in (
                    (None, obm["series"]),
                    (None, opcoes["series"]),
                    (None, b3lab["series"]),
                )
                for s in group
                for t in [s.get("match", {}).get("ticker")]
            )
            if ticker and item
        }
    )
    for ticker, maturity in pairs:
        try:
            detail = fetch_series(
                client, ticker, maturity, args.date, health["model_version"]
            )
        except CompareError as exc:
            unavailable.append(
                {
                    "source": "api",
                    "code": exc.code,
                    "reason": f"/api/s/{ticker}/{maturity}: {exc.message}",
                }
            )
            continue
        # Só o último negócio NA SESSÃO DO BENCHMARK pode entrar na comparação:
        # `/api/s` devolve o último negócio da série, que pode ser anterior à
        # sessão pedida (ex.: B3SAA107 em 25/11 com last em 23/11). A sessão do
        # payload já foi conferida em fetch_series; aqui vale a data do negócio.
        last = detail["data"].get("last")
        if not isinstance(last, dict):
            unavailable.append(
                {
                    "source": "api",
                    "code": "internal_last_missing",
                    "reason": (
                        f"/api/s/{ticker}/{maturity}: last ausente/inválido "
                        f"(ticker={ticker}, maturity={maturity}, session={args.date})"
                    ),
                }
            )
            continue
        last_date = last.get("date")
        last_date_str = str(last_date)[:10] if last_date is not None else ""
        if not DATE_ISO.match(last_date_str):
            unavailable.append(
                {
                    "source": "api",
                    "code": "internal_last_missing",
                    "reason": (
                        f"/api/s/{ticker}/{maturity}: last.date ausente/inválida "
                        f"(ticker={ticker}, maturity={maturity}, "
                        f"last_date={last_date!r}, session={args.date})"
                    ),
                }
            )
            continue
        if last_date_str != args.date:
            unavailable.append(
                {
                    "source": "api",
                    "code": "internal_stale",
                    "reason": (
                        f"/api/s/{ticker}/{maturity}: último negócio em {last_date_str} "
                        f"(ticker={ticker}, maturity={maturity}, "
                        f"last_date={last_date_str}, session={args.date})"
                    ),
                }
            )
            continue
        internal_by_key[(ticker, maturity)] = {
            "underlying": underlyings.get(ticker),
            "series": [
                {
                    "ticker": detail["data"].get("ticker", ticker),
                    "maturity": maturity,
                    "side": detail["data"].get("side"),
                    "strike_brl": detail["data"].get("strike_brl"),
                    "last": detail["data"].get("last") or {},
                    "gamma": detail["data"].get("gamma"),
                    "theta_brl": detail["data"].get("theta_brl"),
                    "vega_brl": detail["data"].get("vega_brl"),
                    "payload": detail["payload"],
                }
            ],
        }

    all_matches: list[dict[str, Any]] = []
    for source, group in (
        ("obm", obm["series"]),
        ("opcoes_net", opcoes["series"]),
        ("b3lab", b3lab["series"]),
    ):
        rows, skipped = build_series_comparisons(
            source=source,
            external_series=group,
            internal_by_key=internal_by_key,
            notes=notes,
        )
        output["series_comparisons"][source].extend(rows)
        all_matches.extend(rows)
        for item in skipped:
            unavailable.append(
                {
                    "source": source,
                    "code": str(item.get("reason") or "match_skipped"),
                    "reason": json.dumps(item, ensure_ascii=False),
                }
            )

    for underlying in sorted(set(underlyings.values())):
        try:
            detail = fetch_underlying(
                client, underlying, args.date, health["model_version"]
            )
        except CompareError as exc:
            unavailable.append(
                {
                    "source": "api",
                    "code": exc.code,
                    "reason": f"/api/t/{underlying}: {exc.message}",
                }
            )
            continue
        screen_row = screens.get(underlying, {})
        # Recorte deste papel. Curva e agregados continuam globais: cada bucket
        # de agregado já carrega o ticker do próprio artefato e é filtrado lá.
        obm_for_underlying: dict[str, Any] = {
            "ohlc": obm["ohlc_by_underlying"].get(underlying, []),
            "ohlc_qty": obm["ohlc_qty_by_underlying"].get(underlying, []),
            "vi_history": obm["vi_history_by_underlying"].get(underlying, []),
            "dividends": obm["dividends_by_underlying"].get(underlying, []),
            "ohlc_sources": obm["ohlc_sources_by_underlying"].get(underlying, {}),
            "aggregates": obm["aggregates"],
            "curve": obm["curve"],
            "series": obm["series"],
        }
        manifest_expiries = (
            capture_verified.payload.get("expiries") or {}
        )
        manifest_expiry = manifest_expiries.get(underlying)
        manifest_expiry = manifest_expiry if isinstance(manifest_expiry, dict) else {}
        detail_data = detail.get("data") if isinstance(detail.get("data"), dict) else {}
        term = detail_data.get("term") or []
        smile_maturity = (
            manifest_expiry.get("smile_maturity") or detail_data.get("smile_maturity")
        )
        if not isinstance(smile_maturity, str) or not smile_maturity:
            dated_terms = [
                row
                for row in term
                if isinstance(row, dict) and isinstance(row.get("maturity"), str)
            ]
            smile_maturity = min(
                dated_terms,
                key=lambda row: abs(float(row.get("t") or 0.0) - 30.0 / 252.0),
                default=None,
            )
            smile_maturity = smile_maturity.get("maturity") if smile_maturity else None
        # Expected move 30d: referência é o forward 30d INTERNO de
        # `metrics_underlying` (41,592692 para PETR4 em 25/11), não o
        # `forward_brl` do vencimento smile do termo (41,91). O spot é só
        # fallback explícito e sinalizado quando a métrica falta.
        detail_summary = (
            detail_data.get("summary") if isinstance(detail_data.get("summary"), Mapping) else {}
        )
        forward_ref = as_float(screen_row.get("forward_30d"))
        if forward_ref is None:
            forward_ref = as_float(detail_summary.get("forward_30d"))
        # Delta points 25Δ do vencimento smile (delta assinado, IV normalizada).
        delta_points = [
            (item.get("delta_signed"), item.get("iv_normalized"))
            for item in obm["series"]
            if str(item.get("underlying") or "").upper() == underlying
            and (item.get("match") or {}).get("maturity") == smile_maturity
            and item.get("delta_signed") is not None
            and item.get("iv_normalized") is not None
        ]
        external_iv: float | None = None
        for bucket in obm["aggregates"]:
            artifact = bucket.get("artifact") or {}
            if str(artifact.get("ticker") or "").upper() != underlying:
                continue
            values = bucket.get("values") if isinstance(bucket.get("values"), dict) else {}
            for name, value in values.items():
                if key_of(name) == "ivatm30dratio" and is_number(value):
                    external_iv, _ = normalize_iv(value, str(name))
                    break
            if external_iv is not None:
                break
        output["recalculated"].extend(
            build_recalculated(
                underlying=underlying,
                obm=obm_for_underlying,
                screen_row=screen_row,
                external_iv_30=external_iv,
                artifact={"source": "obm", "manifest": capture_verified.manifest},
                session=args.date,
                smile_maturity=smile_maturity,
                forward_ref=forward_ref,
                delta_points=delta_points,
            )
        )
        output["underlying_comparisons"].extend(
            build_underlying_comparisons(
                screens=screens,
                underlyings={underlying: {"data": detail}},
                aggregates=obm["aggregates"],
                notes=notes,
                obm=obm_for_underlying,
            )
        )
    output["structure_comparisons"].extend(
        build_structure_comparisons(structures_info, notes)
    )
    notes.append(RHO_NOTE)
    notes.append(
        "P&L/exposição delta reais vêm das estruturas P5 com P7; a linha "
        "ilustrativa `pnl_1x_stock` (entrada = saída = spot, P&L sempre zero) "
        "foi removida — não é benchmark de posição."
    )
    notes.append(
        "Nenhum número de relatório é produzido aqui: este JSON é intermediário "
        "e o relatório F10.6 fica para outra sessão."
    )
    output["summary"] = summarize(
        list(all_matches)
        + list(output["underlying_comparisons"])
        + list(output["structure_comparisons"])
        + list(output["recalculated"])
    )
    # Guarda final: o health ATUAL tem que permanecer no mesmo modelo e na mesma
    # sessão entre as duas leituras. A referência continua sendo args.date nos
    # endpoints históricos; se o lago avançar durante a comparação, aborta.
    health_after = fetch_health(client)
    guard_health(health_after, args.date, "health_after")
    before_state = (health["session"], health["model_version"])
    after_state = (health_after["session"], health_after["model_version"])
    if before_state != after_state:
        raise CompareError(
            "lake_changed_during_comparison",
            f"health atual mudou de {before_state!r} para {after_state!r}",
            "repita a comparação com o lago estável; não misture sessões/modelos",
        )
    output["health_after"] = health_after["raw"]
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench_compare",
        description=(
            "Comparador offline F10 (P6): lê o bruto de P3/P4/P5, consulta só a API "
            "local e grava JSON intermediário `b3o-bench-compare/1`. Sem Playwright, "
            "sem captura, sem relatório final; `--help` não faz rede."
        ),
    )
    parser.add_argument("--date", required=True, help="sessão do lake em AAAA-MM-DD")
    parser.add_argument(
        "--input-dir",
        default=None,
        help=f"pasta com os manifests (default: {DEFAULT_INPUT_DIR}/<data>)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=f"JSON intermediário (default: {DEFAULT_OUTPUT}/benchmark-<data>.json)",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_HTTP_URL,
        help=f"API local do b3o (default: B3_HTTP_URL ou {DEFAULT_HTTP_URL})",
    )
    return parser


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    if not DATE_ISO.match(args.date):
        raise CompareError(
            "bad_date",
            f"--date deve ser AAAA-MM-DD, recebido {args.date!r}",
            "use uma sessão do lake (veja /api/health)",
        )
    try:
        date.fromisoformat(args.date)
    except ValueError as exc:
        raise CompareError(
            "bad_date", f"--date não é uma data válida: {args.date!r}", "confira o calendário"
        ) from exc
    input_dir = (
        Path(args.input_dir)
        if args.input_dir
        else DEFAULT_INPUT_DIR / args.date
    )
    output = (
        Path(args.output)
        if args.output
        else DEFAULT_OUTPUT / f"benchmark-{args.date}.json"
    )
    return argparse.Namespace(
        date=args.date,
        input_dir=input_dir,
        output=output,
        api_url=args.api_url.rstrip("/"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except CompareError as exc:
        print(json.dumps({"error": exc.payload()}, ensure_ascii=False), file=sys.stderr)
        return 2
    try:
        output = build_output(args)
        atomic_write_json(args.output, output)
    except CompareError as exc:
        print(json.dumps({"error": exc.payload()}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:  # falha imprevista também é explícita, sem traceback
        error = CompareError(
            "compare_failed",
            f"falha inesperada: {type(exc).__name__}: {exc}",
            "nenhum dado externo foi inventado; confira os manifests e a API local",
        )
        print(json.dumps({"error": error.payload()}, ensure_ascii=False), file=sys.stderr)
        return 3
    print(
        json.dumps(
            {
                "schema": output["schema"],
                "reference_date": output["reference_date"],
                "output": str(args.output),
                "sources": {
                    name: value["status"] for name, value in output["sources"].items()
                },
                "n_comparisons": output["summary"].get("n_comparisons"),
                "n_unavailable": output["summary"].get("n_unavailable"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
