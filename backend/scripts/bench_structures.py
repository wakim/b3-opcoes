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
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HTTP_URL = os.environ.get("B3_HTTP_URL", "http://host.containers.internal:8080")
DEFAULT_OUTPUT_DIR = Path(os.environ.get("B3_BENCH_DIR", "/data/bench"))
OBM_MONTADOR_URL = "https://obm.com.br/opcoes/montador"
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


def read_health(base: str) -> dict[str, Any]:
    """`GET /api/health`: sessão, versão do modelo e o bruto da resposta."""
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
    return {
        "session": session,
        "model_version": body.get("model_version"),
        "age_sessions": data.get("age_sessions"),
        "raw": body,
    }


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
    return {
        "smile_maturity": data.get("smile_maturity"),
        "maturities": maturities,
        "atm_strike_brl": summary.get("atm_strike_brl"),
        "raw": body,
    }


def read_chain(base: str, underlying: str, session: str, expiry: str) -> dict[str, Any]:
    """Cadeia real do vencimento: sem filtro de liquidez e com delta 0–1."""
    params = {
        "date": session,
        "expiry": expiry,
        "delta_min": 0,
        "delta_max": 1,
        "liquidity": "all",
        "max_rows": 60,
    }
    query = urllib.parse.urlencode(params)
    _, body = _request_json(
        f"{base}/api/t/{urllib.parse.quote(underlying)}/chain?{query}"
    )
    _raise_if_error(body, "/api/t/.../chain")
    data = body.get("data") or {}
    return {
        "rows": list(data.get("rows") or []),
        "total": (body.get("meta") or {}).get("total"),
        "raw": body,
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


def plan_structures(
    underlying: str,
    session: str,
    chain: dict[str, Any],
    expiry: str,
    atm_hint: Any,
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

    specs: dict[str, list[dict[str, Any] | None]] = {
        BULL_CALL_SPREAD: [
            leg("option", "long", (_pick_row(usable, calls[0], "C") or {}).get("ticker"))
            if len(calls) >= 2
            else None,
            leg("option", "short", (_pick_row(usable, calls[-1], "C") or {}).get("ticker"))
            if len(calls) >= 2
            else None,
        ],
        BEAR_PUT_SPREAD: [
            leg("option", "long", (_pick_row(usable, puts[-1], "P") or {}).get("ticker"))
            if len(puts) >= 2
            else None,
            leg("option", "short", (_pick_row(usable, puts[0], "P") or {}).get("ticker"))
            if len(puts) >= 2
            else None,
        ],
    }
    # O straddle e as pernas avulsas usam o strike mais perto do ATM/âncora.
    k = nearest(paired) if paired else None
    specs[STRADDLE] = [
        leg("option", "long", (_pick_row(usable, k, "C") or {}).get("ticker")) if k else None,
        leg("option", "long", (_pick_row(usable, k, "P") or {}).get("ticker")) if k else None,
    ]
    put_k = nearest(puts)
    call_k = nearest(calls)
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
  const sideValue = values.side === 'long' ? 'compra' : 'venda';
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
    title = ""
    text = ""
    try:
        title = page.title()
        text = page.locator("body").inner_text()
    except Exception as exc:  # noqa: BLE001 — diagnóstico, nunca engole a falha
        raise BenchFailure(
            "obm_unreadable",
            f"não foi possível ler o montador do OBM: {exc}",
            "confira a conexão; não insista em página de verificação",
        ) from exc
    blocked = any(
        marker in text or marker in title
        for marker in ("Executando verificação de segurança", "Um momento", "Just a moment")
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


def _obm_reference(page: Any, responses: list[dict[str, Any]], body_text: str) -> dict[str, Any]:
    """Data de referência do OBM: JSON que a página carrega, depois texto."""
    from_json: set[str] = set()
    for item in responses:
        from_json |= _json_reference_dates(item.get("text", ""))
    from_text = _text_reference_dates(body_text)
    return {
        "from_json": sorted(from_json),
        "from_text": sorted(from_text),
        "all": sorted(from_json | from_text),
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
    before = len(responses)
    page.wait_for_timeout(700)
    state = discover()
    outputs = {
        "body_text": state.get("body_text"),
        "tables": page.evaluate(
            "() => Array.from(document.querySelectorAll('table')).map((t) => t.innerText)"
        ),
        "api_responses": responses[before:],
    }
    # Só as três estruturas com preset pública podem ser ditas `captured` sem ele.
    # `put_caixa` não tem preset e continua válido só por adjustment + fill.
    preset_required = name in PRESET_LABELS
    preset_ok = not preset_required or bool(preset.get("clicked"))
    filled_ok = adjustment["ok"] and all(item["result"].get("ok") for item in filled)
    ok = preset_ok and filled_ok
    if not ok:
        reason = (
            str(preset.get("reason") or "preset_not_clicked")
            if not preset_ok
            else str(adjustment.get("reason") or "leg_fill_error")
        )
    else:
        reason = None
    return {
        "status": "captured" if ok else "montador_fill_failed",
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
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR")
        context.on("response", on_response)
        page = context.new_page()
        page.goto(OBM_MONTADOR_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(9000)
        guard = _guard_obm(page)
        reference = _obm_reference(page, responses, guard["body_text"])
        if session not in reference["all"]:
            raise BenchFailure(
                "session_mismatch",
                f"OBM referencia {reference['all'] or 'nenhuma data'}; lago está em {session}",
                "a captura só vale na mesma sessão; não publique bruto de outro pregão",
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


def _already_done(final: Path, sha: str, model_version: Any) -> bool:
    manifest = final / "manifest.json"
    if not manifest.is_file():
        return False
    try:
        current = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        current.get("source_sha256") == sha
        and current.get("model_version") == model_version
        and current.get("published") is True
    )


def _cleanup(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


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

        health_before = read_health(HTTP_URL)
        session = args.date or health_before["session"]
        if session != health_before["session"]:
            raise BenchFailure(
                "session_mismatch",
                f"--date {session} difere da sessão do lake {health_before['session']}",
                "o benchmark usa só a última sessão; ajuste --date ou atualize o lake",
            )

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
            underlying, session, chain, expiry, underlying_data.get("atm_strike_brl")
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
        _write_json(plan_file, base_plan)

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
            print(json.dumps({"mode": "plan", **summary}, ensure_ascii=False, indent=2))
            return 0

        final = _final_dir(output_dir, session, underlying)
        if _already_done(final, source_sha, health_before["model_version"]) and not args.force:
            print(
                json.dumps(
                    {"mode": "skip", "already_published": str(final), **summary},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        temp = output_dir / f".tmp-structures-{session}-{underlying.lower()}-{os.getpid()}"
        _cleanup(temp)
        temp.mkdir(parents=True)
        try:
            accessed_at = _now_iso()
            obm = capture_obm(session, structures, accessed_at)
            health_after = read_health(HTTP_URL)
            if (
                health_after["session"] != health_before["session"]
                or health_after["model_version"] != health_before["model_version"]
            ):
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
                record["sha256"] = _sha256(record)
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
                "diagnostics": diagnostics,
                "obm_page": obm["page"],
                "obm_responses": obm["responses"],
                "structures": records,
                "files": {name: f"{name}.json" for name in STRUCTURE_NAMES},
            }
            _write_json(temp / "manifest.json", manifest)
            _write_json(temp / "plan.json", base_plan)
            if final.exists():
                _cleanup(final)
            os.replace(temp, final)
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


if __name__ == "__main__":
    raise SystemExit(main())
