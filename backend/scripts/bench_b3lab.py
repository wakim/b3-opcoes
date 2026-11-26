"""F10 / P4 — mecanismo futuro de benchmark da calculadora do B3Lab.

Este script **não** é executado nesta retomada: só é construído e validado
estaticamente. Com ``--execute`` (numa rodada futura, no serviço ``bench`` do
compose) ele preenche ``https://b3lab.com.br/calculadora-black-scholes`` com os
inputs das nossas séries europeias líquidas e grava os outputs brutos. Nesta
sessão não há captura, não há acesso a site externo e ``--help`` nunca abre
navegador. Sem ``--execute`` o script consulta **apenas a API local**
(``B3_HTTP_URL``): não é "offline" — existe rede local —, mas nenhum site
externo é aberto e nenhum navegador é iniciado.

Contrato (PLAN.md F10, F10.1 e pacote P4)
----------------------------------------
* Uma camada de consulta: todos os números vêm de ``/api/health``,
  ``/api/t/{ticker}/chain`` e ``POST /api/structure`` da nossa API (a mesma
  ``QueryService`` do produto). Nenhum número é inventado aqui; ``r`` é
  calculado em Python por ``r = -ln(DF)/t`` (glossário: "Fator de desconto
  (DF)", "Forward implícito").
* Só séries ``style == "E"`` (glossário: "Estilo: americana × europeia"),
  ``is_liquid``, com strike/preço/IV/delta/vencimento válidos; no máximo 40,
  ordenação determinística, sem repetir ``(ticker, maturity)``.
* Para cada série, ``POST /api/structure`` com **uma** perna ``option``
  ``long`` de 100 ações devolve ``forward_brl`` (F, R$/ação), ``df``, ``t``
  (du/252) e ``maturity``. ``du = round(t * 252)`` — ``t`` **nunca** é usado
  como DU (armadilha do F4).
* B3Lab: S = F, K = strike, vencimento em **Dias úteis = du**, sigma = IV em
  %, CALL/PUT pelo lado da série. F10.1 confirmou ``r`` contínua e ``T =
  du/252``; Black-76 com ``q = r`` equivale a Black-Scholes com ``S = F``.
  ``r`` e ``q`` são digitados em % a.a. (``r_cont * 100``).
* Ausência é ``null`` + motivo; nunca zero silencioso. Theta interno é
  R$/ação/dia útil (glossário: "Theta"); o theta do B3Lab fica **bruto** até o
  P6 normalizar. Nenhum output ausente é preenchido.

Saída
-----
* ``<output-dir>/<ticker>-<maturity>-<side>.json``: bruto por série (linha da
  cadeia, request/response do ``structure``, inputs calculados, inputs e
  outputs do B3Lab sem normalização, flags).
* ``<output-dir>/manifest.json``: URL, ``accessed_at`` com offset ISO,
  ``reference_date``, SHA, inputs, outputs, flags, health antes/depois e a
  guarda de sessão. Idempotência por sessão + lista de tickers + limite;
  ``--force`` recaptura.
* Escrita passa por arquivos ``.pending`` e só é promovida depois do ``health``
  final com a **mesma** sessão; divergência aborta e limpa os temporários.

Sem dependências novas: stdlib + Playwright (somente no serviço ``bench``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from playwright.sync_api import Page, sync_playwright

B3LAB_URL = "https://b3lab.com.br/calculadora-black-scholes"
HTTP_URL = os.environ.get("B3_HTTP_URL", "http://app:8080").rstrip("/")
TZ_BR = timezone(timedelta(hours=-3))
"""Brasil sem DST desde 2019; offset explícito, nunca relógio local do host."""

MAX_LIMIT = 40
DEFAULT_TICKERS = ("PETR4", "VALE3", "BOVA11", "B3SA3", "ITUB4")
DEFAULT_OUTPUT_ROOT = Path("/data/bench")
PAUSE_SECONDS = 1.5

SELECTORS = {
    "spot": 'input[id$="-spot"]',
    "strike": 'input[id$="-strike"]',
    "business_days": 'input[id$="-business-days"]',
    "rate": 'input[id$="-rate"]',
    "sigma": 'input[id$="-sigma"]',
    "dividend_yield": 'input[id$="-dividend-yield"]',
}

SIDE_LABEL = {"C": "CALL", "CALL": "CALL", "P": "PUT", "PUT": "PUT"}
CHALLENGE_MARKERS = (
    "executando verificação de segurança",
    "um momento",
    "just a moment",
    "cf-turnstile",
    "attention required!",
)

NUMBER_LINE = re.compile(r"[+\-\u2212\u2013]?\d[\d.,]*")
PRECO_LINE = re.compile(r"R\$\s*([+\-\u2212\u2013]?\d[\d.,]*)")
"""Valor = linha que é **só** número (vírgula decimal e sinal), ou `R$ número`
no prêmio. Descrições como "por R$ 1 no ativo" ou "por 1%" não casam."""

OUTPUT_LABELS = {
    "preco": "Prêmio teórico Black-Scholes",
    "delta": "Delta (Δ)",
    "gamma": "Gamma (Γ)",
    "theta": "Theta (Θ)",
    "vega": "Vega (ν)",
    "rho": "Rho (ρ)",
}
"""Rótulos exatos do bloco de resultados medido em F10.1."""

BLOCK_BOUNDARIES = frozenset(
    {
        "Prazo",
        "Dias corridos até o vencimento",
        "GREGAS",
        "Gráfico de sensibilidade",
        *OUTPUT_LABELS.values(),
    }
)
"""Próximos rótulos conhecidos: encerram o bloco antes da descrição."""

DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
"""UA de desktop comum; o default do Chromium headless traz `HeadlessChrome`."""

MANIFEST_FLAGS = (
    "black76_equivalent_S_eq_F_q_eq_r",
    "r_continuous_from_df",
    "T_is_du_over_252_not_calendar_days",
    "theta_internal_unit_brl_per_share_per_business_day",
    "b3lab_outputs_raw_until_p6",
)


class BenchError(Exception):
    """Falha explícita no formato ``{code, message, hint}`` do projeto."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def payload(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def canonical(value: Any) -> str:
    """JSON canônico para SHA e comparação; sem arredondar número nenhum."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def http_json(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET/POST JSON pela stdlib; erro HTTP vira ``BenchError`` com código/hint."""
    data = canonical(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=60.0) as response:  # noqa: S310 - URL fixa do compose
            raw = response.read()
    except HTTPError as exc:
        raw = exc.read()
        try:
            error = json.loads(raw).get("error", {})
        except (ValueError, AttributeError):
            error = {}
        raise BenchError(
            str(error.get("code") or "http_error"),
            str(error.get("message") or f"HTTP {exc.code} em {url}"),
            str(error.get("hint") or "confira B3_HTTP_URL e se a app está no ar"),
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise BenchError(
            "http_unavailable",
            f"não foi possível acessar {url}: {exc}",
            "confira B3_HTTP_URL e `podman compose up -d app`; nada é lido do relógio",
        ) from exc
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise BenchError(
            "bad_payload",
            f"resposta não JSON de {url}",
            "confira se a URL aponta para a API do b3o (B3_HTTP_URL)",
        ) from exc
    if not isinstance(parsed, dict):
        raise BenchError("bad_payload", f"resposta inesperada de {url}", "esperado objeto JSON")
    return parsed


def fetch_health() -> dict[str, Any]:
    """Sessão de referência vem de ``/api/health`` (P-C), nunca do relógio."""
    payload = http_json("GET", f"{HTTP_URL}/api/health")
    session = payload.get("as_of_session") or (payload.get("data") or {}).get("last_session")
    if not isinstance(session, str) or not session:
        raise BenchError(
            "no_session",
            "/api/health não declarou as_of_session",
            "rode `b3o prepare` e confira `/api/health` antes de capturar",
        )
    return payload


def parse_session(value: str | None, health: dict[str, Any]) -> str:
    if value is None:
        return str(health["as_of_session"])
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise BenchError(
            "bad_arguments",
            f"--date inválido ({value!r})",
            "use uma sessão do lake em ISO AAAA-MM-DD (a de /api/health é o default)",
        ) from exc


def health_sessions(health: dict[str, Any]) -> tuple[str | None, str | None]:
    """Par (as_of_session, last_session) declarado por /api/health."""
    return (
        health.get("as_of_session"),
        (health.get("data") or {}).get("last_session"),
    )


def guard_session(health: dict[str, Any], requested: str | None) -> None:
    """--date tem que bater com as_of_session E last_session; falha explícita."""
    as_of, last = health_sessions(health)
    if requested is not None and (requested != as_of or requested != last):
        raise BenchError(
            "session_mismatch",
            f"--date {requested} não bate com /api/health "
            f"(as_of_session={as_of}, last_session={last})",
            "use a sessão congelada do lake; nunca capture outra data",
        )
    if not as_of or as_of != last:
        raise BenchError(
            "health_inconsistent",
            f"/api/health divergiu internamente (as_of_session={as_of}, last_session={last})",
            "confira o serviço app e o prepare antes de capturar",
        )
    model_version = health.get("model_version")
    if not isinstance(model_version, int) or isinstance(model_version, bool):
        raise BenchError(
            "model_version_invalid",
            f"/api/health declarou model_version inválido: {model_version!r}",
            "model_version deve ser inteiro (como no P3); sem versão não há comparabilidade",
        )


def parse_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise BenchError(
            "bad_arguments", f"--limit inválido ({value!r})", "use um inteiro entre 1 e 40"
        ) from exc
    if not 1 <= limit <= MAX_LIMIT:
        raise BenchError(
            "bad_arguments",
            f"--limit fora da faixa ({limit})",
            f"use um inteiro entre 1 e {MAX_LIMIT}",
        )
    return limit


def normalize_tickers(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        ticker = value.strip().upper()
        if ticker and ticker not in result:
            result.append(ticker)
    if not result:
        raise BenchError(
            "bad_arguments", "--tickers vazio", "informe ao menos um papel (ex.: PETR4)"
        )
    return result


def capture_key(session: str, tickers: list[str], limit: int) -> str:
    return hashlib.sha256(
        canonical(
            {"reference_date": session, "tickers": tickers, "limit": limit}
        ).encode("utf-8")
    ).hexdigest()


def candidate_reason(row: dict[str, Any], session: str) -> str | None:
    """Motivo de uma linha da cadeia não ser elegível; ``None`` = elegível."""
    if str(row.get("style") or "").upper() != "E":
        return "style_not_e"
    if row.get("is_liquid") is not True:
        return "not_liquid"
    if not is_number(row.get("strike_brl")) or float(row["strike_brl"]) <= 0:
        return "invalid_strike"
    price = row.get("close_brl")
    if not is_number(price) or float(price) <= 0:
        price = row.get("mid_brl")
    if not is_number(price) or float(price) <= 0:
        return "invalid_price"
    if not is_number(row.get("iv_pct")) or float(row["iv_pct"]) <= 0:
        return "invalid_iv"
    if not is_number(row.get("delta")):
        return "invalid_delta"
    try:
        if not date.fromisoformat(str(row.get("maturity"))) > date.fromisoformat(session):
            return "invalid_maturity"
    except (TypeError, ValueError):
        return "invalid_maturity"
    if str(row.get("side") or "").upper() not in SIDE_LABEL:
        return "invalid_side"
    return None


def select_series(
    session: str, tickers: list[str], limit: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Lê as cadeias (strict, Δ 0–1, 60 linhas) e monta o plano determinístico."""
    planned: list[dict[str, Any]] = []
    paper_reasons: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for paper in tickers:
        query = urlencode(
            {
                "date": session,
                "liquidity": "strict",
                "delta_min": 0,
                "delta_max": 1,
                "max_rows": 60,
            }
        )
        url = f"{HTTP_URL}/api/t/{paper}/chain?{query}"
        try:
            payload = http_json("GET", url)
        except BenchError as exc:
            paper_reasons.append(
                {
                    "underlying": paper,
                    "reason": "chain_unavailable",
                    "error": exc.payload(),
                }
            )
            continue
        rows = (payload.get("data") or {}).get("rows") or []
        meta = payload.get("meta") or {}
        counters: dict[str, int] = {}
        eligible: list[dict[str, Any]] = []
        for row in rows:
            reason = candidate_reason(row, session)
            if reason is not None:
                counters[reason] = counters.get(reason, 0) + 1
                continue
            eligible.append(row)
        eligible.sort(
            key=lambda row: (
                str(row["maturity"]),
                float(row["strike_brl"]),
                str(row["ticker"]),
                str(row["side"]),
            )
        )
        selected = 0
        for row in eligible:
            key = (str(row["ticker"]), str(row["maturity"]))
            if key in seen:
                counters["duplicate_ticker_maturity"] = (
                    counters.get("duplicate_ticker_maturity", 0) + 1
                )
                continue
            if len(planned) >= limit:
                break
            seen.add(key)
            planned.append(
                {
                    "underlying": paper,
                    "ticker": str(row["ticker"]),
                    "side": str(row["side"]),
                    "side_label": SIDE_LABEL[str(row["side"]).upper()],
                    "maturity": str(row["maturity"]),
                    "strike_brl": float(row["strike_brl"]),
                    "iv_pct": float(row["iv_pct"]),
                    "delta": float(row["delta"]),
                    "chain_row": row,
                    "chain_url": url,
                    "chain_meta": meta,
                }
            )
            selected += 1
        reason: dict[str, Any] = {
            "underlying": paper,
            "selected": selected,
            "chain_rows": len(rows),
            "chain_total": meta.get("total"),
            "chain_truncated": meta.get("truncated"),
            "excluded": dict(sorted(counters.items())),
        }
        if selected == 0:
            reason["reason"] = "no_eligible_series" if rows else "no_chain_rows"
        paper_reasons.append(reason)
    if not planned:
        raise BenchError(
            "no_series",
            "nenhuma série europeia líquida elegível nos papéis pedidos",
            "confira --tickers/--date e os motivos em paper_reasons; nunca use zero silencioso",
        )
    return planned, paper_reasons


def structure_inputs(
    item: dict[str, Any], response: dict[str, Any]
) -> dict[str, Any]:
    """F/DF/t do POST /api/structure; ``du = round(t * 252)`` e ``r = -ln(DF)/t``."""
    data = response.get("data") or {}
    forward = data.get("forward_brl")
    df = data.get("df")
    t = data.get("t")
    if not is_number(forward) or float(forward) <= 0:
        raise BenchError("invalid_inputs", "forward_brl inválido", "sem forward não há S = F")
    if not is_number(df) or float(df) <= 0:
        raise BenchError("invalid_inputs", "df inválido ou ausente", "sem curva DI, null + motivo")
    if not is_number(t) or float(t) <= 0:
        raise BenchError("invalid_inputs", "t inválido ou não positivo", "sem prazo não há r")
    du = round(float(t) * 252)
    if du <= 0:
        raise BenchError("invalid_inputs", f"du inválido ({du})", "t em du/252 deve dar du > 0")
    r_cont = -math.log(float(df)) / float(t)
    return {
        "underlying": item["underlying"],
        "ticker": item["ticker"],
        "side": item["side_label"],
        "maturity": item["maturity"],
        "S": float(forward),
        "K": item["strike_brl"],
        "sigma": item["iv_pct"],
        "t": float(t),
        "df": float(df),
        "du": du,
        "r_cont": r_cont,
        "r_pct": r_cont * 100.0,
        "q_pct": r_cont * 100.0,
        "internal_iv_pct": item["iv_pct"],
        "internal_delta": item["delta"],
    }


def structure_request(item: dict[str, Any], session: str) -> dict[str, Any]:
    """Uma perna long de 100 ações; nada é multiplicado por 100 (onda-j §0.7)."""
    return {
        "underlying": item["underlying"],
        "date": session,
        "legs": [
            {
                "kind": "option",
                "ticker": item["ticker"],
                "side": "long",
                "quantity": 100,
            }
        ],
        "scenarios": False,
    }


def challenge_text(page: Page) -> str | None:
    """Turnstile/Cloudflare é falha explícita — sem bypass, sem retry cego."""
    try:
        title = page.title() or ""
        text = page.locator("body").inner_text(timeout=10_000)[:3000]
    except Exception:  # noqa: BLE001 - página em challenge pode não ter DOM útil
        return "página sem DOM legível"
    joined = f"{title}\n{text}".lower()
    for marker in CHALLENGE_MARKERS:
        if marker in joined:
            return marker
    return None


def fill_calculator(page: Page, inputs: dict[str, Any]) -> None:
    """Seleciona Dias úteis e o lado, e preenche S/K/du/r/σ/q na calculadora."""
    page.get_by_role("button", name="Dias úteis").first.click(timeout=30_000)
    page.get_by_role("button", name=inputs["side"], exact=True).first.click(timeout=30_000)
    values = {
        "spot": inputs["S"],
        "strike": inputs["K"],
        "business_days": inputs["du"],
        "rate": inputs["r_pct"],
        "sigma": inputs["sigma"],
        "dividend_yield": inputs["q_pct"],
    }
    for field, value in values.items():
        locator = page.locator(SELECTORS[field])
        locator.wait_for(state="visible", timeout=30_000)
        locator.fill(str(value))


def stable_outputs(page: Page) -> str:
    """Espera o React estabilizar: texto idêntico em 4 leituras, com prêmio presente."""
    previous = ""
    repeats = 0
    for _ in range(60):
        text = page.locator("body").inner_text(timeout=15_000)
        if text == previous and "Prêmio" in text and "teórico" in text:
            repeats += 1
            if repeats >= 4:
                return text
        else:
            repeats = 0
        previous = text
        page.wait_for_timeout(250)
    raise BenchError(
        "outputs_unstable",
        "outputs da calculadora não estabilizaram em ~15 s",
        "não grave outputs parciais; confira a página e o seletor de Dias úteis",
    )


def extract_outputs(text: str) -> tuple[dict[str, str | None], list[str]]:
    """Extrai por bloco: rótulo → próximo rótulo → linha somente número.

    O texto real do F10.1 tem uma linha de descrição entre o rótulo e o valor
    (ex.: `Delta (Δ)` / `Variação do prêmio por R$ 1 no ativo` / `0,5706`); uma
    regex de proxidade capturaria o `1` da descrição. Aqui o bloco é delimitado
    pelos rótulos conhecidos e só uma linha **inteiramente** numérica é aceita.
    Valores ficam brutos (vírgula decimal e sinal originais); ausência é
    `None` + `missing_output:<chave>`, nunca zero.
    """
    lines = [line.strip() for line in text.splitlines()]
    boundaries = [index for index, line in enumerate(lines) if line in BLOCK_BOUNDARIES]
    outputs: dict[str, str | None] = {}
    flags: list[str] = []
    for key, label in OUTPUT_LABELS.items():
        start = next((index for index, line in enumerate(lines) if line == label), None)
        if start is None:
            outputs[key] = None
            flags.append(f"missing_output:{key}")
            continue
        end = next((index for index in boundaries if index > start), len(lines))
        value: str | None = None
        for line in lines[start + 1 : end]:
            match = (
                PRECO_LINE.fullmatch(line) if key == "preco" else NUMBER_LINE.fullmatch(line)
            )
            if match is None:
                continue
            value = match.group(1) if key == "preco" else match.group(0)
            break
        outputs[key] = value
        if value is None:
            flags.append(f"missing_output:{key}")
    return outputs, flags


def pending_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.pending-{os.getpid()}")


def write_pending(
    path: Path, value: Any, created: list[tuple[Path, Path]]
) -> Path:
    target = pending_path(path)
    try:
        target.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise BenchError(
            "write_failed",
            f"não foi possível escrever {target}: {exc}",
            "confira --output-dir e o bind ./data:/data",
        ) from exc
    created.append((target, path))
    return target


def promote(pending: Path, final: Path) -> None:
    """Unlink + rename: o bind do Windows recusa substituir alvo existente."""
    try:
        if final.exists():
            final.unlink()
        pending.rename(final)
    except OSError as exc:
        raise BenchError(
            "write_failed",
            f"não foi possível promover {pending} → {final}: {exc}",
            "confira --output-dir e se outro processo está escrevendo no mesmo diretório",
        ) from exc


def cleanup(created: list[tuple[Path, Path]]) -> None:
    for pending, _ in created:
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            pass


def load_reusable(
    manifest: Path, key: str, session: str, model_version: int
) -> dict[str, Any] | None:
    """Reutiliza só se manifesto, sessão, modelo, arquivos e SHA conferem."""
    if not manifest.exists():
        return None
    try:
        existing = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(existing, dict):
        return None
    if (
        existing.get("capture_key") != key
        or existing.get("reference_date") != session
        or existing.get("model_version") != model_version
        or existing.get("complete") is not True
    ):
        return None
    guard = existing.get("session_guard") or {}
    if (
        guard.get("same") is not True
        or guard.get("before") != session
        or guard.get("after") != session
    ):
        return None
    series = existing.get("series") or []
    counts = existing.get("counts") or {}
    if not series or len(series) != counts.get("captured"):
        return None
    records: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in series:
        name = item.get("file") if isinstance(item, dict) else None
        if (
            not isinstance(name, str)
            or not name
            or name in names
            or "/" in name
            or "\\" in name
        ):
            return None
        names.add(name)
        try:
            record = json.loads((manifest.parent / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (
            not isinstance(record, dict)
            or record.get("reference_date") != session
            or record.get("capture_key") != key
        ):
            return None
        records.append(record)
    if hashlib.sha256(canonical(records).encode("utf-8")).hexdigest() != existing.get("sha256"):
        return None
    return existing


def capture(
    planned: list[dict[str, Any]],
    session: str,
    key: str,
    output_dir: Path,
    created: list[tuple[Path, Path]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Captura sequencial, um navegador, uma página, pausa 1–2 s entre séries."""
    series: list[dict[str, Any]] = []
    raw_records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    try:
        playwright = sync_playwright().__enter__()
    except Exception as exc:  # noqa: BLE001 - driver ausente é falha explícita
        raise BenchError(
            "browser_launch_failed",
            f"Playwright não iniciou: {exc}",
            "rode no serviço bench do compose (`podman compose --profile bench ...`)",
        ) from exc
    try:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(user_agent=DESKTOP_USER_AGENT)
        for index, item in enumerate(planned):
            accessed_at = datetime.now(TZ_BR).isoformat()
            structure_body = structure_request(item, session)
            try:
                structure_response = http_json(
                    "POST", f"{HTTP_URL}/api/structure", structure_body
                )
            except BenchError as exc:
                error = exc.payload()
                skipped.append(
                    {"ticker": item["ticker"], "maturity": item["maturity"], "error": error}
                )
                record = {
                    "reference_date": session,
                    "capture_key": key,
                    "accessed_at": accessed_at,
                    "url": B3LAB_URL,
                    "underlying": item["underlying"],
                    "chain_row": item["chain_row"],
                    "structure_request": structure_body,
                    "structure_response": error,
                    "inputs": None,
                    "b3lab": None,
                    "flags": ["structure_unavailable"],
                    "reason": error["message"],
                }
                raw_records.append(record)
                write_pending(
                    output_dir / f"{item['ticker']}-{item['maturity']}-{item['side']}.json",
                    record,
                    created,
                )
                continue
            try:
                inputs = structure_inputs(item, structure_response)
            except BenchError as exc:
                error = exc.payload()
                skipped.append(
                    {"ticker": item["ticker"], "maturity": item["maturity"], "error": error}
                )
                record = {
                    "reference_date": session,
                    "capture_key": key,
                    "accessed_at": accessed_at,
                    "url": B3LAB_URL,
                    "underlying": item["underlying"],
                    "chain_row": item["chain_row"],
                    "structure_request": structure_body,
                    "structure_response": structure_response,
                    "inputs": None,
                    "b3lab": None,
                    "flags": ["inputs_unavailable"],
                    "reason": error["message"],
                }
                raw_records.append(record)
                write_pending(
                    output_dir / f"{item['ticker']}-{item['maturity']}-{item['side']}.json",
                    record,
                    created,
                )
                continue

            if index:
                time.sleep(PAUSE_SECONDS)
            page.goto(B3LAB_URL, wait_until="domcontentloaded", timeout=60_000)
            marker = challenge_text(page)
            if marker is not None:
                raise BenchError(
                    "cloudflare_challenge",
                    f"B3Lab apresentou challenge ({marker})",
                    "sem bypass: use o navegador do dono/outro canal e não grave outputs",
                )
            fill_calculator(page, inputs)
            marker = challenge_text(page)
            if marker is not None:
                raise BenchError(
                    "cloudflare_challenge",
                    f"B3Lab apresentou challenge após o preenchimento ({marker})",
                    "sem bypass: abortar e limpar temporários",
                )
            text = stable_outputs(page)
            outputs, output_flags = extract_outputs(text)
            b3lab_inputs = {
                "side": inputs["side"],
                "spot": inputs["S"],
                "strike": inputs["K"],
                "business_days": inputs["du"],
                "rate_pct": inputs["r_pct"],
                "sigma_pct": inputs["sigma"],
                "dividend_yield_pct": inputs["q_pct"],
            }
            flags = list(output_flags)
            record = {
                "reference_date": session,
                "capture_key": key,
                "accessed_at": accessed_at,
                "url": B3LAB_URL,
                "underlying": item["underlying"],
                "chain_row": item["chain_row"],
                "structure_request": structure_body,
                "structure_response": structure_response,
                "inputs": inputs,
                "b3lab": {
                    "inputs": b3lab_inputs,
                    "outputs": outputs,
                    "page_text": text,
                    "flags": flags,
                },
                "flags": flags,
                "reason": None,
            }
            raw_records.append(record)
            series.append(
                {
                    "ticker": item["ticker"],
                    "maturity": item["maturity"],
                    "side": inputs["side"],
                    "file": f"{item['ticker']}-{item['maturity']}-{item['side']}.json",
                    "inputs": b3lab_inputs,
                    "outputs": outputs,
                    "flags": flags,
                }
            )
            write_pending(
                output_dir / series[-1]["file"],
                record,
                created,
            )
        browser.close()
    except BenchError:
        try:
            playwright.stop()
        except Exception:  # noqa: BLE001 - cleanup não pode esconder a falha original
            pass
        raise
    except Exception as exc:  # noqa: BLE001 - falha de página é explícita, sem traceback
        try:
            playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        raise BenchError(
            "browser_failed",
            f"falha no navegador: {exc}",
            "nenhum output é inventado; confira o serviço bench e a página do B3Lab",
        ) from exc
    else:
        playwright.stop()
    return series, skipped, raw_records


def build_manifest(
    session: str,
    key: str,
    tickers: list[str],
    limit: int,
    health_before: dict[str, Any],
    health_after: dict[str, Any],
    planned: list[dict[str, Any]],
    paper_reasons: list[dict[str, Any]],
    series: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    raw_records: list[dict[str, Any]],
    started_at: str,
    ended_at: str,
) -> dict[str, Any]:
    sha = hashlib.sha256(canonical(raw_records).encode("utf-8")).hexdigest()
    flags = list(MANIFEST_FLAGS)
    if skipped:
        flags.append("partial_capture")
    return {
        "tool": "bench_b3lab",
        "schema_version": 1,
        "capture_key": key,
        "reference_date": session,
        "url": B3LAB_URL,
        "api_urls": {
            "health": f"{HTTP_URL}/api/health",
            "chain": f"{HTTP_URL}/api/t/{{ticker}}/chain",
            "structure": f"{HTTP_URL}/api/structure",
        },
        "accessed_at": started_at,
        "accessed_at_end": ended_at,
        "model_version": health_before.get("model_version"),
        "sha256": sha,
        "tickers": tickers,
        "limit": limit,
        "paper_reasons": paper_reasons,
        "planned": [
            {
                "underlying": item["underlying"],
                "ticker": item["ticker"],
                "maturity": item["maturity"],
                "side": item["side_label"],
                "strike_brl": item["strike_brl"],
                "iv_pct": item["iv_pct"],
                "delta": item["delta"],
            }
            for item in planned
        ],
        "inputs": [{"series": item["file"], **item["inputs"]} for item in series],
        "outputs": [
            {"series": item["file"], **item["outputs"], "flags": item["flags"]}
            for item in series
        ],
        "series": series,
        "skipped": skipped,
        "flags": flags,
        "session_guard": {
            "before": health_before.get("as_of_session"),
            "after": health_after.get("as_of_session"),
            "same": health_before.get("as_of_session") == health_after.get("as_of_session"),
        },
        "health_before": health_before,
        "health_after": health_after,
        "counts": {
            "planned": len(planned),
            "captured": len(series),
            "skipped": len(skipped),
        },
        "complete": not skipped and len(series) == len(planned),
        "notes": [
            "F/DF/t vêm de POST /api/structure; du = round(t * 252), nunca t como DU",
            "r = -ln(DF)/t em contínuo; digitado em % a.a.; q = r equivale a Black-76 com S = F",
            "outputs do B3Lab ficam brutos (vírgula decimal e sinal originais) até o P6",
            "theta interno é R$/ação/dia útil; o theta capturado aqui não foi convertido",
        ],
    }


def run(args: argparse.Namespace) -> int:
    health_before = fetch_health()
    session = parse_session(args.date, health_before)
    guard_session(health_before, args.date)
    tickers = normalize_tickers(args.tickers)
    limit = parse_limit(args.limit)
    key = capture_key(session, tickers, limit)
    output_dir = (
        Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / session / "b3lab"
    )
    manifest = output_dir / "manifest.json"
    existing = (
        None
        if args.force
        else load_reusable(manifest, key, session, health_before["model_version"])
    )
    if existing is not None:
        print(
            json.dumps(
                {
                    "mode": "reused",
                    "capture_key": key,
                    "reference_date": session,
                    "manifest": str(manifest),
                    "sha256": existing.get("sha256"),
                    "files_verified": len(existing.get("series") or []),
                    "complete": existing.get("complete"),
                    "captured": len(existing.get("series") or []),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    planned, paper_reasons = select_series(session, tickers, limit)
    plan = {
        "mode": "plan" if not args.execute else "execute",
        "capture_key": key,
        "reference_date": session,
        "url": B3LAB_URL,
        "tickers": tickers,
        "limit": limit,
        "selected": len(planned),
        "series": [
            {
                "underlying": item["underlying"],
                "ticker": item["ticker"],
                "maturity": item["maturity"],
                "side": item["side_label"],
                "strike_brl": item["strike_brl"],
                "iv_pct": item["iv_pct"],
            }
            for item in planned
        ],
        "paper_reasons": paper_reasons,
        "output_dir": str(output_dir),
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BenchError(
            "write_failed", f"não foi possível criar {output_dir}: {exc}", "confira --output-dir"
        ) from exc

    created: list[tuple[Path, Path]] = []
    started_at = datetime.now(TZ_BR).isoformat()
    try:
        series, skipped, raw_records = capture(planned, session, key, output_dir, created)
        ended_at = datetime.now(TZ_BR).isoformat()
        health_after = fetch_health()
        guard_session(health_after, session)
        before_pair = health_sessions(health_before)
        after_pair = health_sessions(health_after)
        if before_pair != after_pair:
            raise BenchError(
                "session_changed",
                f"sessão mudou durante a captura: {before_pair} → {after_pair}",
                "abortar e limpar temporários; a comparação exige a mesma sessão do lake",
            )
        if health_after.get("model_version") != health_before.get("model_version"):
            raise BenchError(
                "model_version_changed",
                (
                    "model_version mudou durante a captura: "
                    f"{health_before.get('model_version')} → {health_after.get('model_version')}"
                ),
                "abortar e limpar temporários; o histórico não pode misturar modelos",
            )
        manifest_value = build_manifest(
            session,
            key,
            tickers,
            limit,
            health_before,
            health_after,
            planned,
            paper_reasons,
            series,
            skipped,
            raw_records,
            started_at,
            ended_at,
        )
        write_pending(manifest, manifest_value, created)
        for pending, final in created:
            promote(pending, final)
    except BaseException:
        cleanup(created)
        raise

    print(
        json.dumps(
            {
                "mode": "captured",
                "capture_key": key,
                "reference_date": session,
                "manifest": str(manifest),
                "sha256": manifest_value["sha256"],
                "counts": manifest_value["counts"],
                "complete": manifest_value["complete"],
                "skipped": skipped,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest_value["complete"] else 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "F10/P4: plano sem navegador (default), consultando só a API local, ou "
            "captura futura do B3Lab com --execute. --help nunca abre navegador."
        )
    )
    parser.add_argument(
        "--date", default=None, help="sessão do lake em AAAA-MM-DD (default: /api/health)"
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=list(DEFAULT_TICKERS),
        metavar="TICKER",
        help=f"papéis a consultar (default: {' '.join(DEFAULT_TICKERS)})",
    )
    parser.add_argument(
        "--limit",
        default=str(MAX_LIMIT),
        help=f"máximo de séries, 1..{MAX_LIMIT} (default: {MAX_LIMIT})",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="diretório de brutos (default: /data/bench/<sessão>/b3lab)",
    )
    parser.add_argument(
        "--execute", action="store_true", help="executa a captura (sem isto, só plano)"
    )
    parser.add_argument(
        "--force", action="store_true", help="recaptura mesmo com manifesto completo"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except BenchError as exc:
        print(json.dumps({"error": exc.payload()}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - sem traceback na borda do script
        print(
            json.dumps(
                {
                    "error": {
                        "code": "internal_error",
                        "message": str(exc),
                        "hint": "falha inesperada; não grave outputs parciais",
                    }
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
