"""Fumaça do MCP/HTTP do F3 (não é suíte de testes — ADR-019).

Roda **no container** (`podman compose run --rm test python scripts/mcp_smoke.py`),
contra o servidor `app` no ar. O que ele prova, com saída colada:

1. `tools/list` traz `health`, `screen`, `underlying`, `chain`, `series`, `events`,
   cada uma com "Use quando" e "Não use para" e sem vocabulário preditivo;
2. `screen` de 19/11 devolve 289/163/126, IV mediana 31,43 (n=35) e VRP −30,66
   (n=35), com no máximo 60 linhas e `truncated` coerente;
3. `underlying PETR4` traz spot 42,67, IV 31,43, 88/782 séries, 25 vencimentos;
4. `chain` respeita `delta_range`/`liquidity` e `max_rows`; `events` traz o
   próximo resultado com `actual`/`estimated`;
5. MCP e HTTP devolvem **o mesmo JSON** para `health`, `screen` e `underlying`;
6. erro de ticker/data sai `{"error": {code, message, hint}}`, sem traceback;
7. nenhuma lista passa de 60 linhas; o envelope de 200 KB trunca com
   `truncated: true` (provado no serializador com 3.000 linhas sintéticas);
8. `grep` de termos preditivos nas respostas = 0.

URLs por ambiente: `B3_MCP_URL` (default `http://host.containers.internal:8080/mcp`)
e `B3_HTTP_URL` (default `http://host.containers.internal:8080`).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Iterable
from typing import Any

import httpx
from mcp import Client

from b3o.api.serialize import MAX_RESPONSE_BYTES, envelope

MCP_URL = os.environ.get("B3_MCP_URL", "http://host.containers.internal:8080/mcp")
HTTP_URL = os.environ.get("B3_HTTP_URL", "http://host.containers.internal:8080")
SESSION = "2026-11-19"
TICKER = "PETR4"
SERIES_TICKER = "PETRL44"
SERIES_MATURITY = "2026-12-18"
TOOLS = ("health", "screen", "underlying", "chain", "series", "events")
BANNED = re.compile(
    r"signal_class|premium|acerto|ranking|caro|barato|recomend", re.IGNORECASE
)

RESULTS: list[tuple[bool, str]] = []
PAYLOADS: list[dict[str, Any]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    """Registra um critério; imprime PASS/FAIL com a evidência."""
    RESULTS.append((ok, label))
    suffix = f" — {detail}" if detail else ""
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{suffix}")


def payload(result: Any) -> dict[str, Any]:
    """Conteúdo estruturado da tool (ou o texto JSON, se o SDK só mandar texto)."""
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError("tool sem conteúdo JSON")


def lists_in(value: Any, path: str = "") -> list[tuple[str, list[Any]]]:
    """Todas as listas do payload (para o limite de 60 linhas)."""
    found: list[tuple[str, list[Any]]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(lists_in(item, f"{path}.{key}" if path else key))
    elif isinstance(value, list):
        found.append((path, value))
    return found


def max_response_size() -> tuple[int, str]:
    """Maior lista + tamanho do maior payload real (evidência do teto)."""
    biggest = 0
    where = ""
    for item in PAYLOADS:
        size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode())
        if size > biggest:
            biggest, where = size, str(item.get("data", {}).get("ticker") or "?")
    return biggest, where


async def call(client: Client, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Chama a tool e guarda o payload para as verificações globais."""
    started = time.perf_counter()
    result = await client.call_tool(name, arguments)
    elapsed = time.perf_counter() - started
    body = payload(result)
    PAYLOADS.append(body)
    keys = ", ".join(list(body)[:4])
    print(f"  · {name}({arguments}) {elapsed:.2f}s · {keys}")
    return body


async def http_get(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    response = await client.get(path)
    return response.json()


def verify_screen(body: dict[str, Any]) -> None:
    """Critério 3: números do painel idênticos à medição do F2."""
    summary = body["data"]["summary"]
    check(summary["total"] == 289, "screen.total = 289", str(summary["total"]))
    check(summary["ok"] == 163, "screen.ok = 163", str(summary["ok"]))
    check(summary["excluded"] == 126, "screen.excluded = 126", str(summary["excluded"]))
    check(
        summary["by_reason"] == {"no_trade": 2, "no_options": 124},
        "screen.by_reason = {no_trade: 2, no_options: 124}",
        json.dumps(summary["by_reason"]),
    )
    check(
        abs(summary["iv_median"] - 31.43) < 0.01 and summary["iv_count"] == 35,
        "screen IV mediana 31,43 (n=35)",
        f"{summary['iv_median']} n={summary['iv_count']}",
    )
    check(
        abs(summary["vrp_median"] + 30.66) < 0.01 and summary["vrp_count"] == 35,
        "screen VRP mediana −30,66 (n=35)",
        f"{summary['vrp_median']} n={summary['vrp_count']}",
    )
    check(
        body["meta"]["total"] == 289 and body["meta"]["returned"] == 20,
        "screen meta total/returned = 289/20",
        json.dumps(body["meta"]),
    )
    check(
        len(body["data"]["rows"]) == 20,
        "screen rows = 20",
        str(len(body["data"]["rows"])),
    )


def verify_underlying(body: dict[str, Any]) -> None:
    """Critério 4: números de PETR4 em 19/11."""
    data = body["data"]
    summary = data["summary"]
    check(
        abs(summary["spot_brl"] - 42.67) < 0.005,
        "underlying PETR4 spot = 42,67",
        str(summary["spot_brl"]),
    )
    check(
        abs(summary["iv_atm_30d"] - 31.43) < 0.01,
        "underlying PETR4 iv_atm_30d = 31,43",
        str(summary["iv_atm_30d"]),
    )
    check(
        summary["n_series"] == 782
        and summary["n_liquid_series"] == 88
        and summary["n_expiries"] == 25,
        "underlying PETR4 séries 88/782 e 25 vencimentos",
        f"{summary['n_liquid_series']}/{summary['n_series']}/{summary['n_expiries']}",
    )
    check(
        len(data["term"]) == 25 and data["counts"]["term"] == 25,
        "underlying term = 25 vencimentos",
        str(len(data["term"])),
    )
    check(
        body["meta"]["total"] == 112
        and body["meta"]["returned"] == 60
        and body["meta"]["truncated"] is True,
        "underlying smile truncado em 60 de 112",
        json.dumps(body["meta"]),
    )
    check(
        len(data["history"]) == 60,
        "underlying history = 60 sessões",
        str(len(data["history"])),
    )


def verify_chain(body: dict[str, Any]) -> None:
    """Critério 4: chain respeita filtros e o teto de 60."""
    rows = body["data"]["rows"]
    check(
        all(row["is_liquid"] for row in rows),
        "chain strict: todas as linhas com is_liquid",
        f"n={len(rows)}",
    )
    check(
        all(0.25 <= abs(row["delta"]) <= 0.75 for row in rows if row["delta"] is not None),
        "chain delta_range 0,25–0,75 respeitado",
        f"n={len(rows)}",
    )
    check(
        body["meta"]["returned"] <= 60,
        "chain ≤ 60 linhas",
        json.dumps(body["meta"]),
    )


def verify_events(body: dict[str, Any]) -> None:
    """Critério 4: próximo resultado com actual/estimated."""
    data = body["data"]
    next_earnings = data["next_earnings"]
    check(
        next_earnings is not None and next_earnings["kind"] in ("actual", "estimated"),
        "events next_earnings com actual/estimated",
        json.dumps(next_earnings),
    )
    check(
        len(data["maturities"]) == 25,
        "events maturities = 25",
        str(len(data["maturities"])),
    )
    check(
        any(row["kind"] == "dividend" for row in data["strike_events"]),
        "events strike_events inclui dividend",
        f"n={len(data['strike_events'])}",
    )


def verify_limits_and_banned() -> None:
    """Critérios 6 e 7: listas ≤ 60 e zero vocabulário preditivo."""
    over = [
        (name, len(items))
        for item in PAYLOADS
        for name, items in lists_in(item)
        if len(items) > 60
    ]
    check(not over, "nenhuma lista > 60 linhas", json.dumps(over))
    text = json.dumps(PAYLOADS, ensure_ascii=False)
    hits = sorted(set(BANNED.findall(text)))
    check(not hits, "grep preditivo nas respostas = 0", json.dumps(hits))
    biggest, where = max_response_size()
    check(
        biggest < MAX_RESPONSE_BYTES,
        "maior resposta real abaixo de 200 KB",
        f"{biggest} bytes ({where})",
    )
    synthetic = {
        "rows": [
            {"ticker": "PETRL44", "strike_brl": 43.16, "iv_pct": 32.8, "flags": ["t_estimated"]}
            for _ in range(3000)
        ]
    }
    truncated = envelope(
        synthetic,
        as_of_session=SESSION,
        model_version=2,
        list_key="rows",
    )
    size = len(json.dumps(truncated, ensure_ascii=False, separators=(",", ":")).encode())
    check(
        truncated["meta"]["truncated"] is True
        and truncated["meta"]["returned"] < 3000
        and size <= MAX_RESPONSE_BYTES,
        "envelope de 200 KB trunca com truncated: true",
        f"{json.dumps(truncated['meta'])} · {size} bytes",
    )


def verify_errors(bodies: Iterable[dict[str, Any]]) -> None:
    """Critério 5: erro estruturado e sem traceback."""
    expected = {"ticker_not_found", "no_session"}
    seen = {
        body.get("error", {}).get("code")
        for body in bodies
        if isinstance(body.get("error"), dict)
    }
    check(expected <= seen, "erros ticker_not_found e no_session", json.dumps(sorted(seen)))
    text = json.dumps(list(bodies), ensure_ascii=False)
    check("Traceback" not in text and "File \"" not in text, "nenhum traceback na resposta")


async def main_async() -> int:
    print(f"MCP  {MCP_URL}\nHTTP {HTTP_URL}\n")
    errors: list[dict[str, Any]] = []
    async with Client(MCP_URL) as client, httpx.AsyncClient(
        base_url=HTTP_URL, timeout=120.0
    ) as http:
        tools = await client.list_tools()
        names = [tool.name for tool in tools.tools]
        check(names == list(TOOLS), "tools/list = as 6 tools do F3", json.dumps(names))
        for tool in tools.tools:
            text = tool.description or ""
            ok = "Use quando" in text and "Não use para" in text
            check(ok, f"descrição de {tool.name}: use quando/não use para")
            hits = sorted(set(BANNED.findall(text)))
            check(not hits, f"descrição de {tool.name} sem termo preditivo", json.dumps(hits))

        health = await call(client, "health", {})
        screen = await call(client, "screen", {"date": SESSION, "max_rows": 20})
        underlying = await call(client, "underlying", {"ticker": TICKER, "date": SESSION})
        chain = await call(client, "chain", {"ticker": TICKER, "date": SESSION})
        chain_all = await call(
            client,
            "chain",
            {
                "ticker": TICKER,
                "date": SESSION,
                "delta_min": 0.0,
                "delta_max": 1.0,
                "liquidity": "all",
                "max_rows": 60,
            },
        )
        series = await call(
            client,
            "series",
            {"ticker": SERIES_TICKER, "maturity": SERIES_MATURITY, "days": 60},
        )
        series_zero = await call(
            client,
            "series",
            {"ticker": SERIES_TICKER, "maturity": SERIES_MATURITY, "days": 0},
        )
        events = await call(client, "events", {"ticker": TICKER})

        print("\nHTTP nos mesmos parâmetros:")
        http_health = await http_get(http, "/api/health")
        http_screen = await http_get(
            http, f"/api/screen?date={SESSION}&max_rows=20"
        )
        http_underlying = await http_get(http, f"/api/t/{TICKER}?date={SESSION}")

        check(health == http_health, "MCP = HTTP em health")
        check(screen == http_screen, "MCP = HTTP em screen")
        check(underlying == http_underlying, "MCP = HTTP em underlying")

        verify_screen(screen)
        verify_underlying(underlying)
        verify_chain(chain)
        verify_events(events)
        check(
            chain_all["meta"]["total"] >= 60
            and chain_all["meta"]["returned"] == 60
            and chain_all["meta"]["truncated"] is True,
            "chain all trunca a grade em 60 com truncated",
            json.dumps(chain_all["meta"]),
        )
        check(
            series["data"]["ticker"] == SERIES_TICKER
            and series["data"]["maturity"] == SERIES_MATURITY
            and len(series["data"]["history"]) > 0,
            "series devolve identidade + histórico",
            f"n={len(series['data']['history'])}",
        )
        check(
            len(series_zero["data"]["history"]) == 0
            and series_zero["data"]["last"] is not None,
            "series days=0 devolve histórico vazio (não tudo)",
            f"n={len(series_zero['data']['history'])}",
        )
        zero_book = [
            row
            for row in chain_all["data"]["rows"]
            if row["bid_brl"] == 0 or row["ask_brl"] == 0
        ]
        check(
            not zero_book,
            "chain: book ausente é null, nunca 0,0",
            f"linhas com zero={len(zero_book)}",
        )

        print("\nErros de parâmetro/rota (HTTP):")
        bad_parameter = await http.get("/api/screen?max_rows=abc")
        bad_body = bad_parameter.json()
        check(
            bad_parameter.status_code == 400
            and bad_body.get("error", {}).get("code") == "bad_request",
            "max_rows malformado → 400 {error:{code:bad_request}}",
            f"HTTP {bad_parameter.status_code} {json.dumps(bad_body)[:80]}",
        )
        missing_route = await http.get("/api/nope")
        missing_body = missing_route.json()
        check(
            missing_route.status_code == 404
            and missing_body.get("error", {}).get("code") == "not_found",
            "rota /api desconhecida → 404 {error:{code:not_found}}",
            f"HTTP {missing_route.status_code} {json.dumps(missing_body)[:80]}",
        )

        print("\nErros (MCP):")
        errors.append(await call(client, "underlying", {"ticker": "XXXX9"}))
        errors.append(await call(client, "screen", {"date": "2026-11-21"}))
        verify_errors(errors)

        verify_limits_and_banned()

    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} critérios PASS")
    if failed:
        print("FALHAS: " + "; ".join(failed))
        return 1
    print("SMOKE OK")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
