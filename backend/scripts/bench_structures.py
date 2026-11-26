"""P5 / F10.3 — mecanismo de benchmark das estruturas do montador do OBM.

Monta, a partir da **cadeia real do lago** (via `/api/t/{T}/chain`), as cinco
estruturas de referência (trava de alta, trava de baixa, straddle, put caixa e
venda coberta) e grava o bruto do `POST /api/structure` e, com ``--execute``, o
bruto do que o montador público do OBM devolve.

Regras que valem aqui (não negociáveis)
---------------------------------------
* Python calcula: nenhum strike, prêmio ou número de relatório é inventado;
  os valores vão da linha da cadeia e são **confirmados** pela resposta do
  ``POST /api/structure`` antes de irem para o OBM. Nunca se lê strike do ticker.
* ``quantity`` é **número de ações** (100 em toda perna, inclusive ação); nada
  é multiplicado por 100 (contrato onda-j §0.7 / ADR-028). A unidade do campo
  ``Qtd`` do OBM **não é normalizada aqui** — isso é trabalho do `bench_compare`.
* Falha explícita: ``{code, message, hint}`` e ``exit != 0``. Ausência vira
  ``null`` + motivo (`sparse_chain`, `missing_leg`, `unsupported_external`).
* A sessão do OBM só é aceita com prova positiva e explícita: a classe
  ``Opções`` e a fonte ``b3_option_prices`` precisam estar nela. Data de
  outra classe/fonte não conta, e não se chama ``api/v1`` direto.
* A UI pública do OBM não tem perna de ação (F10.1 alínea (d)): a venda coberta
  registra ``unsupported_external`` e o baseline bruto do POST, sem spot
  sintético e sem tentativa de contorno.
* Uma sessão de navegador sequencial, User-Agent de navegador, pausa 1–2 s,
  sem login e sem bypass de Turnstile/Cloudflare. ``--help`` nunca inicia o
  navegador e, sem ``--execute``, nenhum site externo é aberto.

Execução (tudo em container, pela raiz do repositório)::

    podman compose --profile bench run --rm bench python /scripts/bench_structures.py
    podman compose --profile bench run --rm bench python /scripts/bench_structures.py \
        --execute --date 2026-11-25 --output-dir /data/bench

Somente stdlib + Playwright (o driver vive no serviço `bench`, nunca no produto).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HTTP_URL = os.environ.get("B3_HTTP_URL", "http://host.containers.internal:8080")
DEFAULT_OUTPUT_DIR = Path(os.environ.get("B3_BENCH_DIR", "/data/bench"))
OBM_MONTADOR_URL = "https://obm.com.br/opcoes/montador"
OBM_FRESHNESS_PATH = "/api/v1/assets/freshness"
OBM_COVERAGE_PATH = "/api/v1/assets/coverage"
OBM_PUBLIC_ENDPOINTS = (OBM_FRESHNESS_PATH, OBM_COVERAGE_PATH)
"""Os dois endpoints públicos carregados pela página — fonte primária da guarda."""
OBM_OPTION_ASSET_CLASS = "Opções"
OBM_OPTION_PRICES_SOURCE = "b3_option_prices"
DEFAULT_UNDERLYING = "PETR4"
QUANTITY = 100
"""Ações por perna. 1 contrato padrão = 100 ações; não multiplicar (§0.7)."""

B3_TZ = timezone(timedelta(hours=-3))
"""America/Sao_Paulo sem DST desde 2019 — só para o carimbo `accessed_at`."""

PAUSE_S = 1.5
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

BULL_CALL_SPREAD = "trava_alta"
BEAR_PUT_SPREAD = "trava_baixa"
STRADDLE = "straddle"
SHORT_PUT = "put_caixa"
COVERED_CALL = "venda_coberta"
STRUCTURE_NAMES = (BULL_CALL_SPREAD, BEAR_PUT_SPREAD, STRADDLE, SHORT_PUT, COVERED_CALL)

UNSUPPORTED: dict[str, dict[str, str]] = {
    COVERED_CALL: {
        "status": "unsupported_external",
        "reason": (
            "A UI pública do montador do OBM só oferece as colunas Ação(lado), "
            "Tipo(Call/Put), Qtd, Strike e Prêmio e os presets trava de alta, trava "
            "de baixa, straddle e borboleta: não há controle para perna de ação. "
            "Não se inventa spot sintético nem se contorna a ausência."
        ),
        "evidence_url": OBM_MONTADOR_URL,
        "evidence_local": "data/bench/f10_1-verificacoes.md §(d)",
    }
}
"""Estruturas sem contraparte externa (F10.1 alínea (d))."""

PRESET_LABELS = {
    BULL_CALL_SPREAD: "Trava de alta",
    BEAR_PUT_SPREAD: "Trava de baixa",
    STRADDLE: "Straddle",
}
"""Únicos presets que correspondem às três primeiras estruturas."""


class BenchFailure(Exception):
    """Erro com `code`/`message`/`hint` — nunca traceback na borda."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def payload(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def _sha256(value: Any) -> str:
    """Hash canônico do que foi lido — serve de idempotência e de referência."""
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _record_sha(record: dict[str, Any]) -> str:
    """SHA do registro sem o próprio campo `sha256` (não é auto-referência)."""
    return _sha256({key: value for key, value in record.items() if key != "sha256"})


def _now_iso() -> str:
    return datetime.now(B3_TZ).isoformat(timespec="seconds")


def _request_json(
    url: str, payload: Any = None, timeout: float = 90.0
) -> tuple[int, Any]:
    """GET/POST JSON com stdlib → `(http_status, body)`.

    Erro HTTP/JSON vira `BenchFailure` com código/mensagem/dica, nunca um dict
    furado. O status viaja junto para que `evaluate_structure` grave o bruto
    também na borda, sem segunda chamada.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:600]
        raise BenchFailure(
            "http_error",
            f"{url} respondeu HTTP {exc.code}: {body}",
            "confira B3_HTTP_URL, a sessão pedida e se a app está no ar",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BenchFailure(
            "http_unavailable",
            f"{url} não respondeu: {exc}",
            "suba a app (`podman compose up -d app`) e confira B3_HTTP_URL",
        ) from exc
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BenchFailure(
            "bad_json",
            f"{url} devolveu corpo que não é JSON",
            "a rota pedida não é /api; confira B3_HTTP_URL",
        ) from exc
    return status, body


def _raise_if_error(body: Any, where: str) -> None:
    """Envelope de erro do projeto (HTTP 4xx/5xx já cai no `_request_json`)."""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        raise BenchFailure(
            str(error.get("code", "api_error")),
            f"{where}: {error.get('message')}",
            str(error.get("hint", "confira os parâmetros e a sessão do lake")),
        )


def read_health(base: str, expected_session: str | None = None) -> dict[str, Any]:
    """`GET /api/health` com guarda de sessão e de versão do modelo.

    `as_of_session`, `data.last_session` e `--date` precisam coincidir; o
    `model_version` precisa ser inteiro. A guarda final compara os dois lados
    (antes e depois da captura) com a mesma função.
    """
    _, body = _request_json(f"{base}/api/health")
    _raise_if_error(body, "/api/health")
    data = body.get("data") or {}
    session = body.get("as_of_session") or data.get("last_session")
    if not isinstance(session, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", session):
        raise BenchFailure(
            "health_session_missing",
            f"/api/health não trouxe as_of_session utilizável: {session!r}",
            "rode `b3o prepare` e suba a app antes de capturar",
        )
    if data.get("last_session") != session:
        raise BenchFailure(
            "health_session_inconsistent",
            f"/api/health: as_of_session={session} mas "
            f"data.last_session={data.get('last_session')!r}",
            "aguarde o lake estabilizar antes de capturar",
        )
    if expected_session is not None and session != expected_session:
        raise BenchFailure(
            "session_mismatch",
            f"/api/health está em {session}, mas a sessão pedida é {expected_session}",
            "o benchmark usa só a sessão congelada; ajuste --date ou atualize o lake",
        )
    model_version = body.get("model_version")
    if isinstance(model_version, bool) or not isinstance(model_version, int):
        raise BenchFailure(
            "health_model_version_invalid",
            f"/api/health trouxe model_version não inteiro: {model_version!r}",
            "rode `b3o metrics` na sessão antes de capturar",
        )
    return {
        "session": session,
        "model_version": model_version,
        "age_sessions": data.get("age_sessions"),
        "raw": body,
    }


def health_matches(health: dict[str, Any], session: str, model_version: Any) -> bool:
    """Guarda final: sessão, última sessão do lago e modelo nos dois lados."""
    data = (health.get("raw") or {}).get("data") or {}
    return (
        health.get("session") == session
        and health.get("model_version") == model_version
        and data.get("last_session") == session
        and isinstance(health.get("model_version"), int)
    )


def read_underlying(base: str, underlying: str, session: str) -> dict[str, Any]:
    """`GET /api/t/{T}?sections=term,smile` — smile_maturity e vencimentos."""
    query = urllib.parse.urlencode({"date": session, "sections": "term,smile"})
    _, body = _request_json(f"{base}/api/t/{urllib.parse.quote(underlying)}?{query}")
    _raise_if_error(body, "/api/t/...")
    data = body.get("data") or {}
    maturities = sorted(
        {
            str(row["maturity"])
            for row in (data.get("term") or [])
            if isinstance(row, dict) and isinstance(row.get("maturity"), str)
        }
    )
    summary = data.get("summary") or {}
    term = [row for row in (data.get("term") or []) if isinstance(row, dict)]
    return {
        "smile_maturity": data.get("smile_maturity"),
        "maturities": maturities,
        "term": term,
        "atm_strike_brl": summary.get("atm_strike_brl"),
        "raw": body,
    }


def read_prices(base: str, underlying: str, session: str) -> dict[str, Any]:
    """Âncora = fechamento D-1 da própria API, nunca o ticker nem o termo.

    `days=2` devolve os dois últimos pontos negociados; o penúltimo é o D-1.
    Se a API não trouxer D-1, a âncora volta `None` **com flag explícita** —
    não há fallback silencioso para ATM do termo ou mediana da cadeia.
    """
    query = urllib.parse.urlencode({"days": 2})
    _, body = _request_json(
        f"{base}/api/t/{urllib.parse.quote(underlying)}/prices?{query}"
    )
    _raise_if_error(body, "/api/t/.../prices")
    returned_session = body.get("as_of_session")
    if returned_session != session:
        raise BenchFailure(
            "session_mismatch",
            f"/prices está em {returned_session!r}; lago está em {session}",
            "a âncora D-1 tem que vir da mesma sessão do benchmark",
        )
    points = [
        point
        for point in ((body.get("data") or {}).get("points") or [])
        if isinstance(point, dict)
        and isinstance(point.get("date"), str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", point["date"])
        and point.get("traded") is True
        and _as_positive_float(point.get("close_brl")) is not None
    ]
    points.sort(key=lambda item: item["date"])
    previous = points[-2] if len(points) >= 2 else None
    anchor = _as_positive_float((previous or {}).get("close_brl"))
    result = {
        "anchor": anchor,
        "anchor_date": (previous or {}).get("date"),
        "anchor_source": "api.prices.previous_session_close" if anchor is not None else None,
        "flag": None if anchor is not None else "no_previous_session_close",
        "n_traded_points": len(points),
        "points": [
            {"date": p["date"], "close_brl": p["close_brl"], "traded": p["traded"]}
            for p in points
        ],
        "raw": body,
    }
    return result


def chain_query(
    base: str, underlying: str, session: str, expiry: str, delta_min: float, delta_max: float
) -> dict[str, Any]:
    """Uma página da cadeia; `meta.truncated` volta junto, nunca escondido."""
    params = {
        "date": session,
        "expiry": expiry,
        "delta_min": delta_min,
        "delta_max": delta_max,
        "liquidity": "all",
        "max_rows": 60,
    }
    _, body = _request_json(
        f"{base}/api/t/{urllib.parse.quote(underlying)}/chain?{urllib.parse.urlencode(params)}"
    )
    _raise_if_error(body, "/api/t/.../chain")
    data = body.get("data") or {}
    meta = body.get("meta") or {}
    return {
        "params": params,
        "rows": list(data.get("rows") or []),
        "total": meta.get("total"),
        "returned": meta.get("returned"),
        "truncated": bool(meta.get("truncated")),
        "raw": body,
    }


def read_chain(base: str, underlying: str, session: str, expiry: str) -> dict[str, Any]:
    """Cadeia real do vencimento: sem filtro de liquidez.

    O teto do servidor é 60 linhas por página (e a rota não aceita `offset`).
    Além da varredura 0–1, que dá o panorama e o `truncated`, pede uma faixa
    de delta em torno do ATM: é ela que traz os strikes vizinhos do anchor.
    Nenhum strike é inferido do ticker; o que não veio em linha não entra.
    """
    broad = chain_query(base, underlying, session, expiry, 0.0, 1.0)
    focused = chain_query(base, underlying, session, expiry, 0.3, 0.7)
    by_ticker: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for query in (broad, focused):
        for row in query["rows"]:
            if not isinstance(row, dict):
                continue
            ticker = row.get("ticker")
            if not isinstance(ticker, str) or not ticker:
                continue
            if ticker in by_ticker:
                duplicates += 1
                continue
            by_ticker[ticker] = row
    queries = [
        {
            "params": item["params"],
            "total": item["total"],
            "returned": item["returned"],
            "truncated": item["truncated"],
        }
        for item in (broad, focused)
    ]
    return {
        "rows": list(by_ticker.values()),
        "total": broad["total"],
        "truncated": bool(broad["truncated"] or focused["truncated"]),
        "queries": queries,
        "n_duplicates": duplicates,
        "raw": {"broad": broad["raw"], "focused": focused["raw"]},
    }


def _as_positive_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if number > 0.0 and number == number and number != float("inf") else None


def _pick_row(rows: list[dict[str, Any]], strike: float, side: str) -> dict[str, Any] | None:
    """Escolhe a linha da cadeia por strike/lado, preferindo a série europeia."""
    candidates = [
        row
        for row in rows
        if row.get("side") == side and _as_positive_float(row.get("strike_brl")) == strike
        and _as_positive_float(row.get("mid_brl")) is not None
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (str(item.get("style")) != "E", item["ticker"]))[0]


def choose_expiry(underlying: dict[str, Any], session: str) -> tuple[str | None, str]:
    """Prefere `smile_maturity`; senão o primeiro vencimento com grade."""
    preferred = underlying.get("smile_maturity")
    maturities = underlying.get("maturities") or []
    if isinstance(preferred, str) and preferred in maturities:
        return preferred, "smile_maturity"
    if maturities:
        return maturities[0], "first_term_maturity"
    return None, "no_maturity"


def expiry_anchor(underlying: dict[str, Any], expiry: str) -> tuple[float | None, str]:
    """Âncora de strike vem do termo do próprio vencimento, nunca do ticker."""
    row = next(
        (
            item
            for item in (underlying.get("term") or [])
            if isinstance(item, dict) and item.get("maturity") == expiry
        ),
        None,
    )
    candidates = (
        ("term.atm_strike_brl", (row or {}).get("atm_strike_brl")),
        ("term.forward_brl", (row or {}).get("forward_brl")),
        ("summary.atm_strike_brl", underlying.get("atm_strike_brl")),
    )
    for source, value in candidates:
        number = _as_positive_float(value)
        if number is not None:
            return number, source
    return None, "no_anchor"


def plan_structures(
    underlying: str,
    session: str,
    chain: dict[str, Any],
    expiry: str,
    atm_hint: Any,
    anchor_source: str = "api",
    anchor_date: str | None = None,
    prices_flag: str | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Monta os cinco pedidos a partir da cadeia; perna sem dado vira `null`."""
    rows = [row for row in chain["rows"] if isinstance(row, dict)]
    usable = [
        row
        for row in rows
        if row.get("maturity") == expiry
        and row.get("side") in ("C", "P")
        and _as_positive_float(row.get("strike_brl")) is not None
        and _as_positive_float(row.get("mid_brl")) is not None
    ]
    calls = sorted({float(row["strike_brl"]) for row in usable if row["side"] == "C"})
    puts = sorted({float(row["strike_brl"]) for row in usable if row["side"] == "P"})
    paired = sorted(set(calls) & set(puts))
    anchor = _as_positive_float(atm_hint)
    if anchor is None:
        anchor = paired[len(paired) // 2] if paired else None
        source = "paired_median"
    else:
        source = anchor_source

    def adjacent(strikes: list[float]) -> tuple[float, float] | None:
        """Dois strikes vizinhos ao redor da âncora; nunca as pontas da página."""
        if len(strikes) < 2:
            return None
        below = [strike for strike in strikes if strike <= anchor] if anchor is not None else []
        if below:
            low = below[-1]
            above = [strike for strike in strikes if strike > low]
            if above:
                return low, above[0]
            return strikes[-2], strikes[-1]
        return strikes[0], strikes[1]

    call_pair = adjacent(calls)
    put_pair = adjacent(puts)

    def nearest(strikes: list[float]) -> float | None:
        if not strikes:
            return None
        if anchor is None:
            return strikes[0]
        return min(strikes, key=lambda value: (abs(value - anchor), value))

    missing: list[str] = []
    if len(calls) < 2:
        missing.append("call_spread_needs_2_strikes")
    if len(puts) < 2:
        missing.append("put_spread_needs_2_strikes")
    if not paired:
        missing.append("straddle_needs_call_and_put_at_same_strike")
    if not puts:
        missing.append("short_put_needs_put")
    if not calls:
        missing.append("covered_call_needs_call")
    sparse = bool(missing)

    def leg(kind: str, side: str, ticker: str) -> dict[str, Any] | None:
        if not isinstance(ticker, str) or not ticker:
            return None
        return {"kind": kind, "ticker": ticker, "side": side, "quantity": QUANTITY}

    def spread_leg(
        pair: tuple[float, float] | None, index: int, side: str, option_side: str
    ) -> dict[str, Any] | None:
        strike = pair[index] if pair is not None else None
        return leg(
            "option", side, (_pick_row(usable, strike, option_side) or {}).get("ticker")
        )

    specs: dict[str, list[dict[str, Any] | None]] = {
        BULL_CALL_SPREAD: [
            spread_leg(call_pair, 0, "long", "C"),
            spread_leg(call_pair, 1, "short", "C"),
        ],
        BEAR_PUT_SPREAD: [
            spread_leg(put_pair, 1, "long", "P"),
            spread_leg(put_pair, 0, "short", "P"),
        ],
    }
    # Decisão do dono: straddle, put caixa e call da coberta usam o strike
    # SUPERIOR do par adjacente, não o mais próximo em valor absoluto.
    k = call_pair[1] if call_pair is not None else (nearest(paired) if paired else None)
    specs[STRADDLE] = [
        leg("option", "long", (_pick_row(usable, k, "C") or {}).get("ticker")) if k else None,
        leg("option", "long", (_pick_row(usable, k, "P") or {}).get("ticker")) if k else None,
    ]
    put_k = put_pair[1] if put_pair is not None else nearest(puts)
    call_k = call_pair[1] if call_pair is not None else nearest(calls)
    specs[SHORT_PUT] = [
        leg("option", "short", (_pick_row(usable, put_k, "P") or {}).get("ticker"))
        if put_k is not None
        else None
    ]
    specs[COVERED_CALL] = [
        leg("stock", "long", underlying),
        leg("option", "short", (_pick_row(usable, call_k, "C") or {}).get("ticker"))
        if call_k is not None
        else None,
    ]

    plans: dict[str, dict[str, Any]] = {}
    for name, legs in specs.items():
        if any(item is None for item in legs):
            plans[name] = {
                "request": None,
                "legs": None,
                "status": "null",
                "reason": "missing_leg" + ("/sparse_chain" if sparse else ""),
                "note": "perna sem linha válida na cadeia; nada foi substituído por chute",
            }
            continue
        real_legs = [item for item in legs if item is not None]
        plans[name] = {
            "request": {
                "underlying": underlying,
                "date": session,
                "legs": real_legs,
                "scenarios": False,
            },
            "legs": real_legs,
            "status": "planned",
            "reason": None,
        }
    diagnostics = {
        "expiry": expiry,
        "n_rows": len(rows),
        "n_usable": len(usable),
        "call_strikes": calls,
        "put_strikes": puts,
        "paired_strikes": paired,
        "anchor": anchor,
        "anchor_source": source,
        "anchor_date": anchor_date,
        "prices_flag": prices_flag,
        "spread_strikes": {"calls": call_pair, "puts": put_pair},
        "chain_total": chain.get("total"),
        "chain_truncated": chain.get("truncated"),
        "chain_queries": chain.get("queries"),
        "chain_duplicates": chain.get("n_duplicates"),
        "sparse_chain": sparse,
        "missing": sorted(set(missing)),
    }
    return plans, diagnostics


def evaluate_structure(base: str, name: str, plan: dict[str, Any]) -> dict[str, Any]:
    """POST `/api/structure` e guarda o bruto da requisição e da resposta."""
    if plan["request"] is None:
        return dict(plan)
    status, body = _request_json(f"{base}/api/structure", payload=plan["request"])
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        return {
            **plan,
            "status": "null",
            "reason": str(error.get("code", "api_error")),
            "message": str(error.get("message", "")),
            "hint": str(error.get("hint", "")),
            "http_status": status,
            "response": body,
        }
    return {**plan, "status": "evaluated", "reason": None, "http_status": status, "response": body}


# --------------------------------------------------------------------------- #
# OBM — uma sessão sequencial, presets públicos, nada de API direta           #
# --------------------------------------------------------------------------- #

DISCOVER_JS = """() => {
  const rows = Array.from(document.querySelectorAll('tbody tr, table tr')).filter((tr) =>
    tr.querySelectorAll('select').length >= 2 ||
    (tr.querySelectorAll('select').length >= 1 && tr.querySelectorAll('input').length >= 2));
  return {
    rows: rows.map((tr) => ({
      selects: Array.from(tr.querySelectorAll('select')).map((el) => ({
        value: el.value,
        options: Array.from(el.options).map((o) => ({value:o.value, text:(o.text||'').trim()})),
        outer: el.outerHTML.slice(0, 240),
      })),
      inputs: Array.from(tr.querySelectorAll('input')).map((el) => ({
        type: el.getAttribute('type'), value: el.value,
        placeholder: el.getAttribute('placeholder'), outer: el.outerHTML.slice(0, 240),
      })),
      text: (tr.innerText || '').trim().slice(0, 200),
      outer: tr.outerHTML.slice(0, 500),
    })),
    buttons: Array.from(document.querySelectorAll('button, [role="button"]')).map((b) => ({
      text: (b.innerText || '').trim().slice(0, 80), outer: b.outerHTML.slice(0, 240),
    })),
    body_text: (document.body.innerText || '').slice(0, 12000),
  };
}"""

FILL_JS = """({rowIndex, values}) => {
  const rows = Array.from(document.querySelectorAll('tbody tr, table tr')).filter((tr) =>
    tr.querySelectorAll('select').length >= 2 ||
    (tr.querySelectorAll('select').length >= 1 && tr.querySelectorAll('input').length >= 2));
  const tr = rows[rowIndex];
  if (!tr) return {ok:false, error:'row_missing', rowIndex};
  const setNative = (el, value) => {
    const proto = el.tagName === 'SELECT'
      ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (!desc || !desc.set) return false;
    desc.set.call(el, String(value));
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
    return true;
  };
  const selects = Array.from(tr.querySelectorAll('select'));
  const inputs = Array.from(tr.querySelectorAll('input'));
  const pick = (needle) => selects.find((el) => Array.from(el.options).some((o) =>
    (o.value + ' ' + o.text).toLowerCase().includes(needle)));
  const sideSel = pick('compra');
  const typeSel = pick('call');
  const has = (el, value) => !!el && Array.from(el.options).some((o) =>
    o.value === value || (o.text || '').trim().toLowerCase() === value);
  // `_obm_inputs` envia 'compra'/'venda' (rótulos da UI); aceite também
  // 'long'/'short' do contrato interno, mas nunca inverta o lado.
  const sideValue = values.side === 'compra' || values.side === 'long' ? 'compra'
    : values.side === 'venda' || values.side === 'short' ? 'venda' : null;
  if (sideValue === null) return {ok:false, error:'side_invalid', side:values.side};
  if (!sideSel || !typeSel || sideSel === typeSel) return {ok:false, error:'select_missing'};
  if (!has(sideSel, sideValue) || !has(typeSel, values.type))
    return {ok:false, error:'option_missing',
      side:has(sideSel,sideValue), type:has(typeSel,values.type)};
  const numeric = inputs.filter((el) =>
    ['number','text','',null].includes(el.getAttribute('type')));
  if (numeric.length < 3) return {ok:false, error:'inputs_missing', count:numeric.length};
  const wanted = [[sideSel, sideValue], [typeSel, values.type],
    [numeric[0], values.qty], [numeric[1], values.strike],
    [numeric[2], values.premium]];
  for (const [el, value] of wanted) {
    if (!setNative(el, value)) return {ok:false, error:'setter_missing'};
  }
  return {ok:true, applied: wanted.map(([el, value]) => ({tag:el.tagName, value:String(value)})),
    readback: {side:sideSel.value, type:typeSel.value, qty:numeric[0].value,
      strike:numeric[1].value, premium:numeric[2].value}};
}"""


def _guard_obm(page: Any) -> dict[str, Any]:
    """Turnstile/Cloudflare = falha explícita. Sem bypass, sem retry cego."""
    try:
        title = (page.title() or "").strip()
        text = (page.locator("body").inner_text() or "").strip()
    except Exception as exc:  # noqa: BLE001 — diagnóstico, nunca engole a falha
        raise BenchFailure(
            "obm_unreadable",
            f"não foi possível ler o montador do OBM: {exc}",
            "confira a conexão; não insista em página de verificação",
        ) from exc
    if not title and not text:
        raise BenchFailure(
            "obm_unreadable",
            "o montador do OBM devolveu página sem título e sem texto",
            "a inspeção falhou; não publique bruto de uma página vazia",
        )
    combined = f"{title}\n{text}".lower()
    blocked = any(
        marker in combined
        for marker in (
            "executando verificação de segurança",
            "verificação de segurança",
            "just a moment",
            "um momento",
            "cf-browser-verification",
            "cf-challenge",
            "turnstile",
            "attention required!",
            "enable javascript and cookies to continue",
        )
    )
    if blocked:
        raise BenchFailure(
            "obm_blocked",
            f"OBM devolveu Cloudflare Turnstile (title={title!r}); "
            "a captura foi abortada sem publicar diretório",
            "use uma sessão liberada pelo OBM; não há bypass neste mecanismo",
        )
    return {"title": title, "body_text": text[:12000]}


def _json_reference_dates(text: str) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "reference_date" and isinstance(value, str):
                    match = re.match(r"\d{4}-\d{2}-\d{2}", value)
                    if match:
                        found.add(match.group(0))
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    try:
        walk(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        pass
    return found


def _text_reference_dates(text: str) -> set[str]:
    found: set[str] = set()
    pattern = re.compile(
        r"(?i)(?:fechamento|refer[êe]ncia|preg[ãa]o|cotiza[çc][ãa]o)[^\n]{0,120}?"
        r"(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})"
    )
    for match in pattern.finditer(text):
        raw = match.group(1)
        if "/" in raw:
            day, month, year = raw.split("/")
            found.add(f"{year}-{month}-{day}")
        else:
            found.add(raw)
    return found


def _is_option_asset_class(value: Any) -> bool:
    """Apenas a classe de opções (aceita ``Opções`` e a variante sem acento)."""
    if not isinstance(value, str):
        return False
    plain = "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
    return plain.casefold() == "opcoes"


def _freshness_shape(payload: Any) -> bool:
    """Reconhece o payload que indexa classes por ``asset_class`` e fontes por ``source``."""
    if not isinstance(payload, dict):
        return False
    classes = payload.get("classes")
    sources = payload.get("sources")
    return (
        isinstance(classes, list)
        and any(
            isinstance(item, dict) and "asset_class" in item and "last_real_date" in item
            for item in classes
        )
        and isinstance(sources, list)
        and any(
            isinstance(item, dict) and "source" in item and "reference_date" in item
            for item in sources
        )
    )


def _json_objects_in_text(text: str) -> list[dict[str, Any]]:
    """Objetos JSON embutidos em HTML/RSC — só fallback, nunca ``api/v1`` direto."""
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def _obm_option_evidence(
    responses: list[dict[str, Any]], fallback_text: str = ""
) -> dict[str, Any]:
    """Prova nomeada das opções, sem usar data de outra classe ou fonte.

    Primária: os dois endpoints públicos carregados pela página (freshness e
    coverage). Fallback: HTML/RSC da página **somente** se eles não trouxerem
    os objetos. Nenhuma chamada a ``api/v1`` é feita por esta função.
    """
    parsed: list[tuple[str, dict[str, Any]]] = []
    for item in responses or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url") if isinstance(item.get("url"), str) else ""
        try:
            payload = json.loads(item.get("text") or "")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            parsed.append((url, payload))

    public = [
        (url, payload)
        for url, payload in parsed
        if any(path in url for path in OBM_PUBLIC_ENDPOINTS) and _freshness_shape(payload)
    ]
    option, b3_source = _extract_obm_evidence(public)
    origins: list[str] = []
    if option["value"] is not None or b3_source["value"] is not None:
        origins.append("public_endpoints")
    if option["value"] is None or b3_source["value"] is None:
        fallback = [
            (OBM_MONTADOR_URL, payload)
            for payload in _json_objects_in_text(fallback_text)
            if _freshness_shape(payload)
        ]
        fb_option, fb_b3 = _extract_obm_evidence(fallback)
        if option["value"] is None:
            option = fb_option
        if b3_source["value"] is None:
            b3_source = fb_b3
        if fb_option["value"] is not None or fb_b3["value"] is not None:
            origins.append("page_rsc_fallback")
    return {
        "origin": "+".join(origins) if origins else None,
        "option_class": option,
        "b3_option_prices_source": b3_source,
    }


def _extract_obm_evidence(
    candidates: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lê só a classe de opções e a fonte de preços B3 de cada payload."""
    option: dict[str, Any] = {"value": None, "url": None, "observed_at": None, "as_of": None}
    b3_source: dict[str, Any] = {
        "value": None,
        "url": None,
        "observed_at": None,
        "expected_at": None,
        "as_of": None,
    }
    for url, payload in candidates:
        classes = payload.get("classes") or []
        sources = payload.get("sources") or []
        option_class = next(
            (
                item
                for item in classes
                if isinstance(item, dict)
                and _is_option_asset_class(item.get("asset_class"))
            ),
            None,
        )
        source = next(
            (
                item
                for item in sources
                if isinstance(item, dict) and item.get("source") == OBM_OPTION_PRICES_SOURCE
            ),
            None,
        )
        if isinstance(option_class, dict) and option["value"] is None:
            # A classe não tem horário próprio: usa o `observado_em` da fonte B3
            # do mesmo payload quando disponível.
            option = {
                "value": option_class.get("last_real_date"),
                "url": url,
                "observed_at": source.get("observado_em") if isinstance(source, dict) else None,
                "as_of": payload.get("as_of"),
            }
        if isinstance(source, dict) and b3_source["value"] is None:
            b3_source = {
                "value": source.get("reference_date"),
                "url": url,
                "observed_at": source.get("observado_em"),
                "expected_at": source.get("esperado_em"),
                "as_of": payload.get("as_of"),
            }
        if option["value"] is not None and b3_source["value"] is not None:
            break
    return option, b3_source


def _check_obm_session(
    option: dict[str, Any], b3_source: dict[str, Any], session: str
) -> None:
    """Guarda positiva: as duas provas precisam existir e ser a sessão do lago."""
    problems: list[str] = []
    if option.get("value") != session:
        problems.append(f"classes[{OBM_OPTION_ASSET_CLASS}].last_real_date={option.get('value')!r}")
    if b3_source.get("value") != session:
        problems.append(
            f"sources[{OBM_OPTION_PRICES_SOURCE}].reference_date={b3_source.get('value')!r}"
        )
    if problems:
        raise BenchFailure(
            "session_mismatch",
            f"OBM sem prova explícita da sessão {session}: {'; '.join(problems)}",
            "a captura só vale quando opções e preços B3 estão na sessão do lago; "
            "não use data de outra classe ou fonte",
        )


def validate_obm_session(
    responses: list[dict[str, Any]], session: str, fallback_text: str = ""
) -> dict[str, Any]:
    """Guarda sobre brutos já capturados — sem rede e sem Playwright."""
    evidence = _obm_option_evidence(responses, fallback_text)
    _check_obm_session(evidence["option_class"], evidence["b3_option_prices_source"], session)
    return evidence


def _obm_reference(
    page: Any,
    responses: list[dict[str, Any]],
    body_text: str,
    fallback_text: str = "",
) -> dict[str, Any]:
    """Datas do OBM para auditoria + prova nomeada da sessão das opções."""
    from_json: set[str] = set()
    for item in responses:
        from_json |= _json_reference_dates(item.get("text", ""))
    from_text = _text_reference_dates(body_text)
    evidence = _obm_option_evidence(responses, fallback_text)
    return {
        "from_json": sorted(from_json),
        "from_text": sorted(from_text),
        "all": sorted(from_json | from_text),
        "session_evidence_origin": evidence["origin"],
        "option_class": evidence["option_class"],
        "b3_option_prices_source": evidence["b3_option_prices_source"],
    }


def _click_preset(page: Any, label: str) -> dict[str, Any]:
    """Clica o preset público pelo texto; ausência é registrada, nunca chutada."""
    try:
        locator = page.locator("button, [role='button']").filter(has_text=label)
        count = locator.count()
        if count == 0:
            return {"label": label, "clicked": False, "reason": "preset_not_found"}
        locator.first.click(timeout=10000)
        page.wait_for_timeout(600)
        return {"label": label, "clicked": True, "matches": count}
    except Exception as exc:  # noqa: BLE001
        return {"label": label, "clicked": False, "reason": f"preset_click_error: {exc}"}


def _adjust_rows(page: Any, target: int, discover: Any) -> dict[str, Any]:
    """Ajusta o número de pernas com botões visíveis; sem adivinhar seletor."""
    actions: list[str] = []
    for _ in range(6):
        current = discover()
        count = len(current.get("rows") or [])
        if count == target:
            return {"ok": True, "rows": count, "actions": actions, "controls": current}
        add = re.compile(r"adicionar|incluir|nova perna|nova linha|add", re.IGNORECASE)
        remove = re.compile(r"remover|excluir|remover perna|del", re.IGNORECASE)
        pattern = add if count < target else remove
        try:
            button = page.locator("button, [role='button']").filter(has_text=pattern)
            if button.count() == 0:
                return {
                    "ok": False,
                    "rows": count,
                    "actions": actions,
                    "reason": "montador_control_missing",
                    "controls": current,
                }
            button.first.click(timeout=10000)
            page.wait_for_timeout(400)
            actions.append("add" if count < target else "remove")
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "rows": count,
                "actions": actions,
                "reason": f"montador_control_error: {exc}",
                "controls": current,
            }
    current = discover()
    return {
        "ok": False,
        "rows": len(current.get("rows") or []),
        "actions": actions,
        "reason": "leg_count_unstable",
        "controls": current,
    }


def _confirmed_legs(response: Any) -> list[dict[str, Any]]:
    """Os tickers/strikes/prêmios que o POST confirmou — a única fonte do OBM."""
    data = response.get("data") if isinstance(response, dict) else None
    legs = data.get("legs") if isinstance(data, dict) else None
    if not isinstance(legs, list):
        return []
    return [
        {
            "kind": row.get("kind"),
            "ticker": row.get("ticker"),
            "side": row.get("side"),
            "option_side": row.get("option_side"),
            "style": row.get("style"),
            "quantity": row.get("quantity"),
            "strike_brl": row.get("strike_brl"),
            "premium_brl": row.get("price_brl"),
            "premium_brl_signed": row.get("premium_brl"),
            "maturity": row.get("maturity"),
        }
        for row in legs
        if isinstance(row, dict)
    ]


def _obm_inputs(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Só pernas de opção entram: a UI pública não tem perna de ação."""
    inputs: list[dict[str, Any]] = []
    for leg in legs:
        if leg.get("kind") != "option":
            continue
        side = leg.get("option_side")
        if side not in ("C", "P"):
            continue
        inputs.append(
            {
                "acao": "compra" if leg.get("side") == "long" else "venda",
                "tipo": "call" if side == "C" else "put",
                "qtd": QUANTITY,
                "strike": leg.get("strike_brl"),
                "premio": leg.get("premium_brl"),
                "ticker": leg.get("ticker"),
            }
        )
    return inputs


NO_SINGLE_LEG_REASON = (
    "A UI pública do montador não oferece controle para deixar uma única perna: "
    "a grade tem duas linhas, não há botão/aria-label/título/ícone de remover no DOM "
    "nem no JS público, e Delete/Backspace não reduzem o número de linhas. "
    "Nada foi substituído por perna sintética nem por chamada direta à API."
)
BE_TOLERANCE_BRL = 0.30
"""OBM interpola breakevens numa grade de 0,50; meia grade + arredondamento."""


def _expected_external(name: str, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """O que o botão público 'Recalcular' deve devolver, em base por ação."""
    signed = [
        ((1.0 if item["acao"] == "compra" else -1.0), float(item["strike"]), float(item["premio"]))
        for item in inputs
    ]
    net_per_share = sum(sign * premium for sign, _strike, premium in signed)
    if name == BULL_CALL_SPREAD:
        low = min(k for _s, k, _p in signed)
        high = max(k for _s, k, _p in signed)
        breakevens = [low + net_per_share]
        max_gain = (high - low - net_per_share) * QUANTITY
        max_loss = -net_per_share * QUANTITY
    elif name == BEAR_PUT_SPREAD:
        high = max(k for _s, k, _p in signed)
        low = min(k for _s, k, _p in signed)
        breakevens = [high - net_per_share]
        max_gain = (high - low - net_per_share) * QUANTITY
        max_loss = -net_per_share * QUANTITY
    elif name == STRADDLE:
        strike = signed[0][1]
        total = abs(net_per_share)
        breakevens = sorted((strike - total, strike + total))
        max_gain = None
        max_loss = -total * QUANTITY
    elif name == SHORT_PUT:
        strike = signed[0][1]
        breakevens = [strike + net_per_share]  # net_per_share é negativo (crédito)
        max_gain = abs(net_per_share) * QUANTITY
        max_loss = -(strike + net_per_share) * QUANTITY
    else:
        return {
            "net_cost_brl": None,
            "breakevens_brl": None,
            "max_profit_brl": None,
            "max_loss_brl": None,
        }
    return {
        "net_cost_brl": round(net_per_share * QUANTITY, 6),
        "net_cost_per_share_brl": round(net_per_share, 6),
        "breakevens_brl": [round(value, 6) for value in breakevens],
        "max_profit_brl": None if max_gain is None else round(max_gain, 6),
        "max_loss_brl": round(max_loss, 6),
        "note": "net_cost assinado como no request (compra +, venda -); BE analítico no vencimento",
    }


def _validate_external(
    name: str, inputs: list[dict[str, Any]], recalc: dict[str, Any]
) -> dict[str, Any]:
    """Confere o bruto externo com os inputs preenchidos, não com preset antigo."""
    expected = _expected_external(name, inputs)
    raw_response = ((recalc or {}).get("response") or {}).get("text")
    try:
        external = json.loads(raw_response) if isinstance(raw_response, str) else {}
    except (TypeError, json.JSONDecodeError):
        external = {}
    problems: list[str] = []
    got_net = external.get("net_cost")
    exp_net = expected.get("net_cost_brl")
    if (
        not isinstance(got_net, int | float)
        or exp_net is None
        or abs(float(got_net) - exp_net) > 1e-6
    ):
        problems.append(f"net_cost: externo={got_net!r} esperado={exp_net!r}")
    payoff_points: list[tuple[float, float]] = []
    payoff = external.get("payoff")
    if isinstance(payoff, list):
        for row in payoff:
            if isinstance(row, dict):
                spot = _as_positive_float(row.get("spot"))
                pnl = row.get("payoff")
                if spot is not None and isinstance(pnl, int | float):
                    payoff_points.append((spot, float(pnl)))
        payoff_points.sort()

    got_be = external.get("breakevens")
    exp_be = expected.get("breakevens_brl") or []
    be_source = "analitico"
    if not isinstance(got_be, list) or len(got_be) != len(exp_be):
        problems.append(f"breakevens: externo={got_be!r} esperado={exp_be!r}")
    else:
        # O OBM interpola os breakevens na própria grade de payoff (passo 0,50).
        # Reconstrói essa grade e confere o número publicado; só depois compara
        # com o analítico dos inputs, com tolerância de meia grade.
        grid_be: list[float] = []
        if payoff_points:
            for (x0, y0), (x1, y1) in zip(payoff_points, payoff_points[1:], strict=False):
                if y0 == 0.0:
                    grid_be.append(x0)
                elif y0 * y1 < 0:
                    grid_be.append(x0 - y0 * (x1 - x0) / (y1 - y0))
        grid_ok = (
            len(grid_be) == len(got_be)
            and all(
                abs(float(got) - want) <= 0.01
                for got, want in zip(got_be, grid_be, strict=False)
            )
        )
        if grid_ok:
            be_source = "grade_do_obm"
        else:
            for got, want in zip(got_be, exp_be, strict=False):
                if not isinstance(got, int | float) or abs(float(got) - want) > BE_TOLERANCE_BRL:
                    problems.append(
                        f"breakeven: externo={got!r} esperado={want} (tol {BE_TOLERANCE_BRL})"
                    )
    max_source = "analitico"
    for field, key in (("max_profit", "max_profit_brl"), ("max_loss", "max_loss_brl")):
        want = expected.get(key)
        got = external.get(field)
        if want is None:
            continue
        exact = isinstance(got, int | float) and abs(float(got) - want) <= 1e-6
        grid_value = (
            max(y for _x, y in payoff_points)
            if field == "max_profit" and payoff_points
            else min(y for _x, y in payoff_points)
            if payoff_points
            else None
        )
        from_grid = (
            isinstance(got, int | float)
            and grid_value is not None
            and abs(float(got) - grid_value) <= 1e-6
        )
        if exact:
            pass
        elif from_grid:
            max_source = "grade_do_obm"
        else:
            problems.append(
                f"{field}: externo={got!r} esperado={want!r} (grade={grid_value!r})"
            )
    return {
        "ok": not problems,
        "expected": expected,
        "max_source": max_source,
        "got": {
            "net_cost": got_net,
            "breakevens": got_be,
            "max_profit": external.get("max_profit"),
            "max_loss": external.get("max_loss"),
        },
        "be_tolerance_brl": BE_TOLERANCE_BRL,
        "be_source": be_source,
        "breakevens_from_obm_grid": True if be_source == "grade_do_obm" else None,
        "problems": problems,
    }


def _wait_ui_stable(page: Any, tries: int = 3, delay_ms: int = 300) -> None:
    """Estabilização simples da UI: três leituras iguais do corpo da página."""
    previous = None
    for _ in range(tries):
        current = page.locator("body").inner_text()
        if current == previous:
            return
        previous = current
        page.wait_for_timeout(delay_ms)


def _public_control_inspection(page: Any, discover: Any) -> dict[str, Any]:
    """Evidência de que não há controle público para remover uma perna."""
    state = discover()
    controls = state.get("buttons") or []
    keyboard: dict[str, Any] = {}
    for key in ("Delete", "Backspace"):
        try:
            rows = page.evaluate(DISCOVER_JS).get("rows") or []
            if len(rows) > 1:
                page.locator("tbody tr, table tr").nth(1).click(
                    position={"x": 5, "y": 5}, timeout=5000
                )
                page.keyboard.press(key)
                page.wait_for_timeout(350)
            keyboard[key] = len(page.evaluate(DISCOVER_JS).get("rows") or [])
        except Exception as exc:  # noqa: BLE001 — diagnóstico, não engole a falha
            keyboard[key] = f"error:{type(exc).__name__}"
    return {
        "n_rows": len(state.get("rows") or []),
        "button_texts": [str(item.get("text") or "")[:60] for item in controls],
        "button_aria_labels": [
            item.get("aria_label") for item in controls if item.get("aria_label")
        ],
        "keyboard_rows_after": keyboard,
        "js_search": "sem 'remover'/'removeLeg' nos chunks públicos do montador",
    }


def _capture_one(
    page: Any,
    name: str,
    legs: list[dict[str, Any]],
    responses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Preenche uma estrutura no OBM e devolve entradas/saídas brutas."""
    preset = _click_preset(page, PRESET_LABELS[name]) if name in PRESET_LABELS else {
        "label": None,
        "clicked": False,
        "reason": "no_preset_for_structure",
    }
    discover = lambda: page.evaluate(DISCOVER_JS)  # noqa: E731 — callback curto e local
    wanted = _obm_inputs(legs)
    adjustment = _adjust_rows(page, len(wanted), discover)

    # Put caixa: esgotada a inspeção dos controles públicos. Sem uma perna
    # legítima, nunca publicar como `captured`.
    if name == SHORT_PUT and not adjustment["ok"] and adjustment.get("rows", 0) > len(wanted):
        return {
            "status": "no_public_single_leg_control",
            "reason": NO_SINGLE_LEG_REASON,
            "preset": preset,
            "inputs": wanted,
            "outputs": None,
            "raw": {
                "adjustment": adjustment,
                "filled": [],
                "controls": discover(),
                "control_inspection": _public_control_inspection(page, discover),
            },
        }

    filled: list[dict[str, Any]] = []
    if adjustment["ok"]:
        for index, item in enumerate(wanted):
            result = page.evaluate(
                FILL_JS,
                {
                    "rowIndex": index,
                    "values": {
                        "side": item["acao"],
                        "type": item["tipo"],
                        "qty": item["qtd"],
                        "strike": item["strike"],
                        "premium": item["premio"],
                    },
                },
            )
            filled.append({"index": index, "item": item, "result": result})
    preset_required = name in PRESET_LABELS
    preset_ok = not preset_required or bool(preset.get("clicked"))
    filled_ok = (
        adjustment["ok"] and bool(wanted) and all(item["result"].get("ok") for item in filled)
    )
    recalc: dict[str, Any] = {
        "button": None,
        "request": None,
        "response": None,
        "ui": None,
        "validation": None,
    }
    if filled_ok:
        button = page.locator("button, [role='button']").filter(has_text="Recalcular")
        if button.count() == 0:
            recalc["button"] = "recalcular_not_found"
        else:
            recalc["button"] = "Recalcular"
            try:
                with page.expect_response(
                    lambda r: r.url.endswith("/api/v1/opcoes/montador")
                    and r.request.method == "POST",
                    timeout=20000,
                ) as expected_response:
                    button.first.click(timeout=10000)
                response = expected_response.value
                _wait_ui_stable(page)
                request = response.request
                recalc["request"] = {
                    "url": request.url,
                    "method": request.method,
                    "post_data": request.post_data,
                }
                try:
                    text = response.text()
                except Exception as exc:  # noqa: BLE001
                    text = f"ERR:{exc}"
                recalc["response"] = {
                    "url": response.url,
                    "status": response.status,
                    "text": text[:200000],
                }
                state_recalc = discover()
                recalc["ui"] = {
                    "body_text": state_recalc.get("body_text"),
                    "tables": page.evaluate(
                        "() => Array.from(document.querySelectorAll('table'))"
                        ".map((t) => t.innerText)"
                    ),
                }
                recalc["validation"] = _validate_external(name, wanted, recalc)
            except Exception as exc:  # noqa: BLE001 — falha explícita, nunca publicar
                recalc["button"] = f"recalc_error:{type(exc).__name__}: {exc}"

    validation = recalc.get("validation") or {}
    ok = preset_ok and filled_ok and bool(validation.get("ok"))
    if not ok:
        if not preset_ok:
            reason = str(preset.get("reason") or "preset_not_clicked")
        elif not filled_ok:
            reason = str(adjustment.get("reason") or "leg_fill_error")
        else:
            reason = "external_output_mismatch: " + "; ".join(
                validation.get("problems") or ["sem resposta válida"]
            )
    else:
        reason = None
    state = discover()
    outputs = {
        "body_text": state.get("body_text"),
        "tables": page.evaluate(
            "() => Array.from(document.querySelectorAll('table')).map((t) => t.innerText)"
        ),
        "api_responses": responses,
        "recalc": recalc,
    }
    return {
        "status": "captured" if ok else "montador_output_mismatch",
        "reason": reason,
        "preset": preset,
        "inputs": wanted,
        "outputs": outputs,
        "raw": {
            "adjustment": adjustment,
            "filled": filled,
            "controls": state,
        },
    }


def capture_obm(
    session: str,
    structures: dict[str, dict[str, Any]],
    accessed_at: str,
) -> dict[str, Any]:
    """Abre **somente** o montador, uma sessão sequencial, e captura o bruto."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BenchFailure(
            "playwright_missing",
            "o driver Playwright não está disponível neste container",
            "rode no serviço `bench` (`podman compose --profile bench run --rm bench ...`)",
        ) from exc

    results: dict[str, Any] = {}
    responses: list[dict[str, Any]] = []

    def on_response(response: Any) -> None:
        if "/api/v1/" not in response.url:
            return
        try:
            text = response.text()
        except Exception:  # noqa: BLE001 — resposta ilegível não é evidência
            return
        responses.append({"url": response.url, "status": response.status, "text": text[:200000]})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="pt-BR",
            user_agent=USER_AGENT,
        )
        context.on("response", on_response)
        page = context.new_page()
        page.goto(OBM_MONTADOR_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(9000)
        guard = _guard_obm(page)
        try:
            # HTML/RSC da página: apenas fallback se os endpoints públicos
            # carregados não trouxerem as duas provas; nunca api/v1 direto.
            fallback_text = (page.content() or "")[:200000]
        except Exception:  # noqa: BLE001 — ausência de fallback é falha explícita
            fallback_text = ""
        reference = _obm_reference(page, responses, guard["body_text"], fallback_text)
        _check_obm_session(
            reference["option_class"], reference["b3_option_prices_source"], session
        )
        time.sleep(PAUSE_S)

        for name in STRUCTURE_NAMES:
            item = structures[name]
            if name in UNSUPPORTED:
                results[name] = {
                    "status": UNSUPPORTED[name]["status"],
                    "reason": UNSUPPORTED[name]["reason"],
                    "evidence_url": UNSUPPORTED[name]["evidence_url"],
                    "evidence_local": UNSUPPORTED[name]["evidence_local"],
                    "inputs": None,
                    "outputs": None,
                    "raw": None,
                }
                continue
            confirmed = _confirmed_legs(item.get("response"))
            if not confirmed:
                results[name] = {
                    "status": "null",
                    "reason": "missing_leg",
                    "inputs": None,
                    "outputs": None,
                    "raw": None,
                }
                continue
            results[name] = _capture_one(page, name, confirmed, responses)
            time.sleep(PAUSE_S)

        browser.close()

    return {
        "url": OBM_MONTADOR_URL,
        "accessed_at": accessed_at,
        "reference_date": session,
        "reference_found": reference,
        "page": {"title": guard["title"], "body_text": guard["body_text"]},
        "responses": responses,
        "structures": results,
    }


# --------------------------------------------------------------------------- #
# Saída, idempotência e CLI                                                    #
# --------------------------------------------------------------------------- #


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _structure_record(
    name: str, plan: dict[str, Any], obm: dict[str, Any] | None
) -> dict[str, Any]:
    record = {
        "name": name,
        "status": plan["status"],
        "reason": plan.get("reason"),
        "source_sha256": _sha256(
            {"request": plan.get("request"), "response": plan.get("response")}
        ),
        "request": plan.get("request"),
        "response": plan.get("response"),
        "obm": obm,
    }
    if name in UNSUPPORTED and obm is None:
        record["status"] = UNSUPPORTED[name]["status"]
        record["reason"] = UNSUPPORTED[name]["reason"]
        record["evidence_url"] = UNSUPPORTED[name]["evidence_url"]
        record["evidence_local"] = UNSUPPORTED[name]["evidence_local"]
    if obm is not None:
        record["status"] = obm["status"]
        record["reason"] = obm.get("reason")
    return record


def _plan_path(output_dir: Path, session: str, underlying: str) -> Path:
    return output_dir / f"structures-{session}-{underlying.lower()}.plan.json"


def _final_dir(output_dir: Path, session: str, underlying: str) -> Path:
    return output_dir / f"structures-{session}-{underlying.lower()}"


def _already_done(
    final: Path, sha: str, model_version: Any, session: str, underlying: str
) -> bool:
    """Idempotência provada nos arquivos e nos SHA, não só no manifesto."""
    path = final / "manifest.json"
    if not path.is_file():
        return False
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            manifest.get("source_sha256") != sha
            or manifest.get("model_version") != model_version
            or manifest.get("published") is not True
            or manifest.get("session") != session
            or manifest.get("underlying") != underlying
            or manifest.get("reference_date") != session
        ):
            return False
        files = manifest.get("files")
        entries = manifest.get("structures")
        if not isinstance(files, dict) or not isinstance(entries, dict):
            return False
        if set(files) != set(STRUCTURE_NAMES) or set(entries) != set(STRUCTURE_NAMES):
            return False
        if len(set(files.values())) != len(STRUCTURE_NAMES):
            return False
        plan_path = final / "plan.json"
        if not plan_path.is_file():
            return False
        published_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan_entries = published_plan.get("structures")
        if (
            not isinstance(published_plan, dict)
            or published_plan.get("source_sha256") != sha
            or not isinstance(plan_entries, dict)
            or set(plan_entries) != set(STRUCTURE_NAMES)
        ):
            return False
        for name in STRUCTURE_NAMES:
            record_path = final / str(files[name])
            if not record_path.is_file() or record_path.name != f"{name}.json":
                return False
            record = json.loads(record_path.read_text(encoding="utf-8"))
            entry = entries[name]
            # `source_sha256` da estrutura é o hash do par request/response dela,
            # não o sha do plano inteiro (que vive no manifesto e no plan.json).
            own_sha = _sha256(
                {"request": record.get("request"), "response": record.get("response")}
            )
            plan_entry = plan_entries[name]
            if (
                not isinstance(record, dict)
                or not isinstance(entry, dict)
                or not isinstance(plan_entry, dict)
                or record.get("name") != name
                or record.get("source_sha256") != own_sha
                or record.get("sha256") != _record_sha(record)
                or not isinstance(record.get("status"), str)
                or not record["status"]
                or entry.get("sha256") != record["sha256"]
                or entry.get("source_sha256") != own_sha
                or plan_entry.get("source_sha256") != own_sha
            ):
                return False
            obm = record.get("obm")
            if (
                isinstance(obm, dict)
                and obm.get("inputs") is not None
                and entry.get("inputs") != obm["inputs"]
            ):
                return False
            # Idempotência não pode pular captura vermelha: exige output externo
            # validado contra os inputs para as estruturas com preset pública.
            status = record.get("status")
            if name == COVERED_CALL:
                if status != "unsupported_external":
                    return False
            elif name == SHORT_PUT and status == "no_public_single_leg_control":
                if (
                    record.get("reason") != NO_SINGLE_LEG_REASON
                    or not isinstance(obm, dict)
                    or obm.get("outputs") is not None
                    or entry.get("external_output_valid") is not False
                ):
                    return False
            else:
                recalc = ((obm or {}).get("outputs") or {}).get("recalc") or {}
                validation = recalc.get("validation") or {}
                if (
                    status != "captured"
                    or validation.get("ok") is not True
                    or entry.get("external_output_valid") is not True
                ):
                    return False
    except (OSError, TypeError, ValueError, KeyError):
        return False
    return True


def _cleanup(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _publish(temp: Path, final: Path) -> None:
    """Publica copiando arquivos: renomear DIRETÓRIO falha no bind do Windows.

    Todo o conteúdo é lido antes de criar o destino; se qualquer escrita falhar,
    o destino é removido — nunca fica um diretório publicado pela metade.
    """
    names = ["manifest.json", "plan.json", *[f"{name}.json" for name in STRUCTURE_NAMES]]
    payloads = {name: (temp / name).read_text(encoding="utf-8") for name in names}
    created = False
    try:
        if final.exists():
            _cleanup(final)
        final.mkdir(parents=True, exist_ok=False)
        created = True
        for name, text in payloads.items():
            (final / name).write_text(text, encoding="utf-8")
    except Exception:
        if created:
            _cleanup(final)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark das estruturas: monta no OBM o que POST /api/structure confirmou. "
            "Sem --execute, apenas planeja com a API local (nenhum site externo, nem navegador)."
        )
    )
    parser.add_argument(
        "--date",
        default=None,
        help="sessão do lake em AAAA-MM-DD (default: as_of_session de /api/health)",
    )
    parser.add_argument(
        "--underlying", default=DEFAULT_UNDERLYING, help=f"papel (default: {DEFAULT_UNDERLYING})"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"pasta de brutos (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="abre o montador do OBM e salva o bruto (padrão: só planejar)",
    )
    parser.add_argument(
        "--force", action="store_true", help="recaptura mesmo se a sessão já estiver publicada"
    )
    args = parser.parse_args(argv)

    try:
        if args.date is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
            raise BenchFailure(
                "bad_date",
                f"--date inválida ({args.date!r})",
                "use uma sessão do lake em ISO AAAA-MM-DD",
            )
        underlying = args.underlying.strip().upper()
        if not underlying:
            raise BenchFailure(
                "bad_underlying", "--underlying vazio", "informe o ticker do papel (ex.: PETR4)"
            )

        health_before = read_health(HTTP_URL, args.date)
        session = args.date or health_before["session"]

        prices = read_prices(HTTP_URL, underlying, session)
        if prices["anchor"] is None:
            raise BenchFailure(
                str(prices["flag"]),
                f"/api/t/{underlying}/prices não trouxe o fechamento D-1 em {session}",
                "sem âncora D-1 não há seleção legítima; não cair para ATM do termo nem mediana",
            )
        anchor, anchor_source = prices["anchor"], prices["anchor_source"]

        underlying_data = read_underlying(HTTP_URL, underlying, session)
        expiry, expiry_source = choose_expiry(underlying_data, session)
        if expiry is None:
            raise BenchFailure(
                "no_maturity",
                f"{underlying} não tem vencimento em {session}",
                "rode `b3o metrics` nesta sessão ou escolha outro papel",
            )
        chain = read_chain(HTTP_URL, underlying, session, expiry)
        plans, diagnostics = plan_structures(
            underlying,
            session,
            chain,
            expiry,
            anchor,
            anchor_source,
            prices["anchor_date"],
            prices["flag"],
        )
        structures = {
            name: evaluate_structure(HTTP_URL, name, plans[name]) for name in STRUCTURE_NAMES
        }

        evaluated = [name for name, item in structures.items() if item["status"] == "evaluated"]
        if not evaluated:
            raise BenchFailure(
                "sparse_chain",
                f"nenhuma estrutura pôde ser avaliada: {diagnostics['missing']}",
                "aumente a grade líquida do papel ou escolha outra sessão; nada foi chutado",
            )

        base_plan = {
            "schema": "b3o-bench-structures/1",
            "mode": "plan",
            "session": session,
            "underlying": underlying,
            "expiry": expiry,
            "expiry_source": expiry_source,
            "model_version": health_before["model_version"],
            "quantity_shares": QUANTITY,
            "anchor": {
                "source": anchor_source,
                "value_brl": anchor,
                "date": prices["anchor_date"],
                "flag": prices["flag"],
                "previous_close_brl": prices["anchor"],
                "raw": prices["raw"],
            },
            "diagnostics": diagnostics,
            "health": health_before,
            "structures": {
                name: _structure_record(name, structures[name], None) for name in STRUCTURE_NAMES
            },
        }
        source_sha = _sha256(base_plan)
        base_plan["source_sha256"] = source_sha

        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        plan_file = _plan_path(output_dir, session, underlying)

        summary = {
            "session": session,
            "underlying": underlying,
            "expiry": expiry,
            "evaluated": evaluated,
            "null": [name for name in STRUCTURE_NAMES if name not in evaluated],
            "source_sha256": source_sha,
            "plan": str(plan_file),
        }
        if not args.execute:
            _write_json(plan_file, base_plan)
            print(json.dumps({"mode": "plan", **summary}, ensure_ascii=False, indent=2))
            return 0

        final = _final_dir(output_dir, session, underlying)
        if (
            _already_done(final, source_sha, health_before["model_version"], session, underlying)
            and not args.force
        ):
            print(
                json.dumps(
                    {"mode": "skip", "already_published": str(final), **summary},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.force and final.exists():
            stamp = _now_iso().replace(":", "").replace("-", "")
            backup = output_dir / f"{final.name}.backup-{stamp}"
            backup.mkdir(parents=True, exist_ok=True)
            for source in final.iterdir():
                if source.is_file():
                    (backup / source.name).write_text(
                        source.read_text(encoding="utf-8"), encoding="utf-8"
                    )

        temp = output_dir / f".tmp-structures-{session}-{underlying.lower()}-{os.getpid()}"
        _cleanup(temp)
        temp.mkdir(parents=True)
        try:
            accessed_at = _now_iso()
            obm = capture_obm(session, structures, accessed_at)
            health_after = read_health(HTTP_URL, session)
            before_ok = health_matches(health_before, session, health_before["model_version"])
            after_ok = health_matches(health_after, session, health_before["model_version"])
            if not before_ok or not after_ok:
                raise BenchFailure(
                    "session_mismatch",
                    "o lake mudou durante a captura: "
                    f"{health_before['session']}/{health_before['model_version']} → "
                    f"{health_after['session']}/{health_after['model_version']}",
                    "repita a captura quando a sessão estiver estável",
                )

            records: dict[str, Any] = {}
            for name in STRUCTURE_NAMES:
                record = _structure_record(name, structures[name], obm["structures"][name])
                record["sha256"] = _record_sha(record)
                filename = f"{name}.json"
                _write_json(temp / filename, record)
                records[name] = {
                    key: record[key]
                    for key in (
                        "name",
                        "status",
                        "reason",
                        "source_sha256",
                        "sha256",
                        "inputs",
                        "outputs",
                    )
                    if key in record
                }
                # `inputs`/`outputs` do manifest vêm do OBM quando existem.
                captured = obm["structures"][name]
                if captured and captured.get("inputs") is not None:
                    records[name]["inputs"] = captured["inputs"]
                    records[name]["outputs"] = captured["outputs"]
                recalc = ((captured or {}).get("outputs") or {}).get("recalc") or {}
                records[name]["external_output_valid"] = bool(
                    (recalc.get("validation") or {}).get("ok")
                )

            manifest = {
                "schema": "b3o-bench-structures/1",
                "mode": "execute",
                "published": True,
                "generated_at": _now_iso(),
                "underlying": underlying,
                "session": session,
                "reference_date": session,
                "model_version": health_before["model_version"],
                "expiry": expiry,
                "expiry_source": expiry_source,
                "url": obm["url"],
                "accessed_at": accessed_at,
                "reference_found": obm["reference_found"],
                "source_sha256": source_sha,
                "quantity_shares": QUANTITY,
                "quantity_note": (
                    "100 ações em toda perna (inclusive ação), sem multiplicar por 100. "
                    "A unidade de Qtd do OBM não foi normalizada neste mecanismo."
                ),
                "health_before": health_before["raw"],
                "health_after": health_after["raw"],
                "anchor": base_plan["anchor"],
                "diagnostics": diagnostics,
                "obm_page": obm["page"],
                "obm_responses": obm["responses"],
                "structures": records,
                "files": {name: f"{name}.json" for name in STRUCTURE_NAMES},
            }
            _write_json(temp / "manifest.json", manifest)
            _write_json(temp / "plan.json", base_plan)
            _publish(temp, final)
        except BaseException:
            _cleanup(temp)
            raise

        print(
            json.dumps(
                {
                    "mode": "execute",
                    "published": str(final),
                    "manifest": str(final / "manifest.json"),
                    **summary,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except BenchFailure as exc:
        print(json.dumps({"error": exc.payload()}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — borda: erro explícito, sem traceback
        print(
            json.dumps(
                {
                    "error": {
                        "code": "unexpected_error",
                        "message": f"{type(exc).__name__}: {exc}",
                        "hint": "falha não prevista no mecanismo; nada foi publicado",
                    }
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
