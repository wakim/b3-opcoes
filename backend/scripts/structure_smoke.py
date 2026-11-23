"""Fumaça do F4 — `structure_eval` (não é suíte de testes — ADR-019).

Roda **no container** (`podman compose run --rm test python scripts/structure_smoke.py`)
contra o servidor `app` no ar. O que ele prova, com saída colada:

1. `tools/list` traz `structure_eval` com "Use quando"/"Não use para" e sem termo preditivo;
2. MCP, HTTP (`POST /api/structure`) e CLI (`b3o struct --json`) devolvem **o mesmo JSON**;
3. as 5 estruturas conferidas à mão (coberta, put caixa, trava de alta, trava de baixa,
   straddle) + call nua batem com os números calculados **por fora** (`data/out/f4-conferencia.py`,
   caminho independente que não importa b3o);
4. call nua → `max_loss_brl: null` + `unbounded_loss`; `capital` abaixo do comprometido →
   `capital_over: true`;
5. cenários ≤ 60 pontos, `(S, 0, 0)` ≈ P&L 0, `T' = 0` = intrínseco, perna sem IV →
   `scenarios_unavailable` com payoff presente;
6. erros de entrada no formato `{error: {code, message, hint}}`, sem traceback;
7. nenhuma lista > 60 linhas; `grep` de termo preditivo nas respostas/descrições = 0.

URLs por ambiente: `B3_MCP_URL` (default `http://host.containers.internal:8080/mcp`) e
`B3_HTTP_URL` (default `http://host.containers.internal:8080`).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from typing import Any

import httpx
from mcp import Client

MCP_URL = os.environ.get("B3_MCP_URL", "http://host.containers.internal:8080/mcp")
HTTP_URL = os.environ.get("B3_HTTP_URL", "http://host.containers.internal:8080")
SESSION = "2026-11-19"
BANNED = re.compile(
    r"signal_class|premium|acerto|ranking|caro|barato|recomend", re.IGNORECASE
)

Body = dict[str, Any]
BODIES: dict[str, Body] = {
    "coberta": {
        "underlying": "PETR4",
        "date": SESSION,
        "legs": [
            {"kind": "stock", "ticker": "PETR4", "side": "long", "quantity": 100},
            {"kind": "option", "ticker": "PETRL44", "side": "short", "quantity": 100},
        ],
        "capital": 5000,
        "scenarios": True,
    },
    "put_caixa": {
        "underlying": "PETR4",
        "date": SESSION,
        "legs": [
            {"kind": "option", "ticker": "PETRX42", "side": "short", "quantity": 100},
        ],
        "capital": 4019,
        "scenarios": True,
    },
    "trava_alta": {
        "underlying": "PETR4",
        "date": SESSION,
        "legs": [
            {"kind": "option", "ticker": "PETRL447", "side": "long", "quantity": 100},
            {"kind": "option", "ticker": "PETRL44", "side": "short", "quantity": 100},
        ],
        "capital": 5000,
        "scenarios": True,
    },
    "trava_baixa": {
        "underlying": "PETR4",
        "date": SESSION,
        "legs": [
            {"kind": "option", "ticker": "PETRX14", "side": "long", "quantity": 100},
            {"kind": "option", "ticker": "PETRX42", "side": "short", "quantity": 100},
        ],
        "capital": 5000,
        "scenarios": True,
    },
    "straddle": {
        "underlying": "PETR4",
        "date": SESSION,
        "legs": [
            {"kind": "option", "ticker": "PETRL44", "side": "long", "quantity": 100},
            {"kind": "option", "ticker": "PETRX44", "side": "long", "quantity": 100},
        ],
        "capital": 5000,
        "scenarios": True,
    },
    "call_nua": {
        "underlying": "PETR4",
        "date": SESSION,
        "legs": [
            {"kind": "option", "ticker": "PETRL44", "side": "short", "quantity": 100},
        ],
        "capital": 5000,
        "scenarios": True,
    },
}

# conferência à mão (data/out/f4-conferencia.py): spot 42,67, forward 43,168929, df 0,989178031
HAND: dict[str, dict[str, Any]] = {
    "coberta": {
        "net": 4109.0,
        "be": [41.09],
        "max_loss": 4109.0,
        "max_gain": 207.0,
        "committed": 4109.0,
        "pnl": [-268.70, 158.00, 207.00],
        "flags": set(),
    },
    "put_caixa": {
        "net": -72.0,
        "be": [40.19],
        "max_loss": 4019.0,
        "max_gain": 72.0,
        "committed": 4019.0,
        "pnl": [-178.70, 72.00, 72.00],
        "flags": {"illiquid_leg"},
    },
    "trava_alta": {
        "net": 35.0,
        "be": [42.76],
        "max_loss": 35.0,
        "max_gain": 40.0,
        "committed": 35.0,
        "pnl": [-35.00, -9.00, 40.00],
        "flags": {"illiquid_leg"},
    },
    "trava_baixa": {
        "net": 99.0,
        "be": [42.42],
        "max_loss": 99.0,
        "max_gain": 151.0,
        "committed": 99.0,
        "pnl": [151.00, -25.00, -99.00],
        "flags": {"illiquid_leg"},
    },
    "straddle": {
        "net": 315.0,
        "be": [40.01, 46.31],
        "max_loss": 315.0,
        "max_gain": None,
        "committed": 315.0,
        "pnl": [160.70, -266.00, 62.70],
        "flags": {"illiquid_leg", "unbounded_gain"},
    },
    "call_nua": {
        "net": -158.0,
        "be": [44.74],
        "max_loss": None,
        "max_gain": 158.0,
        "committed": None,
        "pnl": [158.00, 158.00, -219.70],
        "flags": {"unbounded_loss"},
    },
}
SPOTS = (0.9 * 42.67, 42.67, 1.1 * 42.67)

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


async def call(client: Client, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    body = payload(result)
    PAYLOADS.append(body)
    return body


def mcp_arguments(body: Body) -> dict[str, Any]:
    return {
        "underlying": body.get("underlying"),
        "date": body.get("date"),
        "legs": body["legs"],
        "capital": body.get("capital"),
        "scenarios": body.get("scenarios", True),
    }


def cli_json(body: Body) -> dict[str, Any]:
    """`b3o struct --json` no próprio container (mesmo payload do HTTP/MCP)."""
    command = ["b3o", "struct", "--date", str(body["date"]), "--json"]
    if body.get("underlying"):
        command += ["--underlying", str(body["underlying"])]
    for leg in body["legs"]:
        command += [
            "--leg",
            f"{leg['kind']}:{leg['ticker']}:{leg['side']}:{leg['quantity']}",
        ]
    if body.get("capital") is not None:
        command += ["--capital", str(body["capital"])]
    if not body.get("scenarios", True):
        command += ["--no-scenarios"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if completed.returncode != 0:
        raise AssertionError(
            f"b3o struct falhou ({completed.returncode}): {completed.stderr[-300:]}"
        )
    return json.loads(completed.stdout)


def lists_in(value: Any, path: str = "") -> list[tuple[str, list[Any]]]:
    found: list[tuple[str, list[Any]]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(lists_in(item, f"{path}.{key}" if path else key))
    elif isinstance(value, list):
        found.append((path, value))
    return found


def near(value: float | None, expected: float | None, tol: float = 0.01) -> bool:
    if value is None or expected is None:
        return value is None and expected is None
    return abs(float(value) - expected) <= tol


def payoff_at(body: dict[str, Any], spot: float) -> float | None:
    points = body["data"]["payoff"]
    point = min(points, key=lambda row: abs(row["spot_brl"] - spot))
    return point["pnl_brl"] if abs(point["spot_brl"] - spot) < 0.02 else None


def scenario_at(body: dict[str, Any], spot: float, shift: float, days: int) -> float | None:
    rows = body["data"]["scenarios"] or []
    for row in rows:
        if (
            abs(row["spot_brl"] - spot) < 0.005
            and abs(row["iv_shift_pts"] - shift) < 1e-9
            and row["days_forward"] == days
        ):
            return row["pnl_brl"]
    return None


def verify_structure(name: str, mcp_body: dict[str, Any], http_body: dict[str, Any],
                     cli_body: dict[str, Any]) -> None:
    expected = HAND[name]
    data = mcp_body["data"]
    check(
        mcp_body == http_body == cli_body,
        f"{name}: MCP = HTTP = CLI (mesmo JSON)",
        f"mcp={len(json.dumps(mcp_body))}B http={len(json.dumps(http_body))}B",
    )
    check(
        mcp_body["as_of_session"] == SESSION
        and mcp_body["model_version"] == 2
        and data["structure_version"] == 1,
        f"{name}: as_of_session 19/11, model_version 2, structure_version 1",
        f"{mcp_body['as_of_session']}/{mcp_body['model_version']}/{data['structure_version']}",
    )
    check(
        near(data["net_premium_brl"], expected["net"], 0.005),
        f"{name}: prêmio líquido {expected['net']}",
        str(data["net_premium_brl"]),
    )
    check(
        len(data["breakeven_brl"]) == len(expected["be"])
        and all(
            near(got, want, 0.01)
            for got, want in zip(data["breakeven_brl"], expected["be"], strict=True)
        ),
        f"{name}: breakevens {expected['be']}",
        str([round(value, 2) for value in data["breakeven_brl"]]),
    )
    check(
        near(data["max_loss_brl"], expected["max_loss"]),
        f"{name}: perda máxima {expected['max_loss']}",
        str(data["max_loss_brl"]),
    )
    check(
        near(data["max_gain_brl"], expected["max_gain"]),
        f"{name}: ganho máximo {expected['max_gain']}",
        str(data["max_gain_brl"]),
    )
    check(
        near(data["capital_committed_brl"], expected["committed"], 0.01),
        f"{name}: capital comprometido {expected['committed']}",
        str(data["capital_committed_brl"]),
    )
    got_flags = set(data["flags"])
    check(
        got_flags == expected["flags"],
        f"{name}: flags {sorted(expected['flags'])}",
        json.dumps(sorted(got_flags)),
    )
    got_pnl = [payoff_at(mcp_body, spot) for spot in SPOTS]
    check(
        all(near(got, want, 0.05) for got, want in zip(got_pnl, expected["pnl"], strict=True)),
        f"{name}: P&L em 0,9S/S/1,1S = {expected['pnl']}",
        str([None if value is None else round(value, 2) for value in got_pnl]),
    )
    scenarios = data["scenarios"]
    check(
        scenarios is not None and len(scenarios) == 45,
        f"{name}: 45 cenários (5 spots × 3 IV × 3 dias)",
        "null" if scenarios is None else str(len(scenarios)),
    )
    land = scenario_at(mcp_body, 42.67, 0.0, 0)
    check(
        land is not None and abs(land) <= 0.05,
        f"{name}: cenário (S, 0, 0) ≈ P&L 0",
        "null" if land is None else f"{land:.4f}",
    )


async def main_async() -> int:
    print(f"MCP  {MCP_URL}\nHTTP {HTTP_URL}\n")
    async with Client(MCP_URL) as client, httpx.AsyncClient(
        base_url=HTTP_URL, timeout=180.0
    ) as http:
        tools = await client.list_tools()
        names = [tool.name for tool in tools.tools]
        check(
            "structure_eval" in names,
            "tools/list inclui structure_eval",
            json.dumps(names),
        )
        tool = next(tool for tool in tools.tools if tool.name == "structure_eval")
        text = tool.description or ""
        check(
            "Use quando" in text and "Não use para" in text,
            "descrição de structure_eval: use quando/não use para",
        )
        check(
            not BANNED.findall(text),
            "descrição de structure_eval sem termo preditivo",
            json.dumps(sorted(set(BANNED.findall(text)))),
        )

        for name, body in BODIES.items():
            mcp_body = await call(client, "structure_eval", mcp_arguments(body))
            response = await http.post("/api/structure", json=body)
            check(
                response.status_code == 200,
                f"{name}: POST /api/structure 200",
                f"HTTP {response.status_code}",
            )
            http_body = response.json()
            PAYLOADS.append(http_body)
            try:
                cli_body = cli_json(body)
            except (AssertionError, json.JSONDecodeError) as exc:
                check(False, f"{name}: b3o struct --json", str(exc)[:200])
                continue
            verify_structure(name, mcp_body, http_body, cli_body)
            print()

        print("Provas específicas:")
        naked = await call(client, "structure_eval", mcp_arguments(BODIES["call_nua"]))
        check(
            naked["data"]["max_loss_brl"] is None
            and "unbounded_loss" in naked["data"]["flags"],
            "call vendida nua: max_loss null + unbounded_loss",
            json.dumps(sorted(naked["data"]["flags"])),
        )
        over_body = dict(BODIES["put_caixa"], capital=1000)
        over = await call(client, "structure_eval", mcp_arguments(over_body))
        check(
            over["data"]["capital_over"] is True
            and near(over["data"]["capital_committed_brl"], 4019.0, 0.01),
            "capital 1000 < comprometido 4019: capital_over true",
            json.dumps(over["data"]["capital_over"]),
        )
        over_http = (await http.post("/api/structure", json=over_body)).json()
        check(over == over_http, "capital_over: MCP = HTTP")

        # T' = 0: 27/11 (t = 5/252) com 7 dias à frente → intrínseco
        near_body = {
            "underlying": "PETR4",
            "date": SESSION,
            "legs": [
                {
                    "kind": "option",
                    "ticker": "PETRK429W4",
                    "side": "long",
                    "quantity": 100,
                }
            ],
            "scenarios": True,
        }
        t_zero_body = await call(client, "structure_eval", mcp_arguments(near_body))
        forward = t_zero_body["data"]["forward_brl"]
        t_zero = scenario_at(t_zero_body, 42.67, 0.0, 7)
        intrinsic = 100.0 * max(forward - 42.91, 0.0) - 100.0 * 0.86
        check(
            t_zero is not None and near(t_zero, intrinsic, 0.01),
            "T'=0 (27/11, 7 dias): P&L = intrínseco − prêmio",
            f"got {t_zero} · esperado {intrinsic:.4f} (F={forward:.6f})",
        )

        # vencimento com mid e sem IV: payoff existe, cenário não
        no_iv_body = {
            "underlying": "PETR4",
            "date": SESSION,
            "legs": [
                {
                    "kind": "option",
                    "ticker": "PETRL424W1",
                    "side": "long",
                    "quantity": 100,
                }
            ],
            "scenarios": True,
        }
        no_iv = await call(client, "structure_eval", mcp_arguments(no_iv_body))
        check(
            no_iv["data"]["scenarios"] is None
            and "no_iv" in no_iv["data"]["flags"]
            and "scenarios_unavailable" in no_iv["data"]["flags"]
            and len(no_iv["data"]["payoff"]) > 0,
            "perna sem IV: cenários null + no_iv/scenarios_unavailable, payoff presente",
            json.dumps(sorted(no_iv["data"]["flags"])),
        )

        print("\nErros de entrada:")
        errors: list[dict[str, Any]] = []

        def error_of(response: httpx.Response) -> dict[str, Any]:
            body = response.json()
            errors.append(body)
            return body

        bad_qty = await http.post(
            "/api/structure",
            json={
                "underlying": "PETR4",
                "date": SESSION,
                "legs": [
                    {"kind": "option", "ticker": "PETRL44", "side": "long", "quantity": 0}
                ],
            },
        )
        check(
            bad_qty.status_code == 400
            and error_of(bad_qty).get("error", {}).get("code") == "bad_legs",
            "quantity 0 → 400 bad_legs",
            f"HTTP {bad_qty.status_code}",
        )
        bad_kind = await http.post(
            "/api/structure",
            json={
                "underlying": "PETR4",
                "date": SESSION,
                "legs": [
                    {"kind": "future", "ticker": "PETRL44", "side": "long", "quantity": 100}
                ],
            },
        )
        check(
            bad_kind.status_code == 400
            and error_of(bad_kind).get("error", {}).get("code") == "bad_legs",
            "kind inválido → 400 bad_legs",
            f"HTTP {bad_kind.status_code}",
        )
        mixed_maturity = await http.post(
            "/api/structure",
            json={
                "underlying": "PETR4",
                "date": SESSION,
                "legs": [
                    {"kind": "option", "ticker": "PETRL44", "side": "long", "quantity": 100},
                    {"kind": "option", "ticker": "PETRK429W4", "side": "long", "quantity": 100},
                ],
            },
        )
        check(
            mixed_maturity.status_code == 400
            and error_of(mixed_maturity).get("error", {}).get("code") == "maturity_mismatch",
            "vencimentos diferentes → 400 maturity_mismatch",
            f"HTTP {mixed_maturity.status_code}",
        )
        mixed_underlying = await http.post(
            "/api/structure",
            json={
                "underlying": "PETR4",
                "date": SESSION,
                "legs": [
                    {"kind": "option", "ticker": "PETRL44", "side": "long", "quantity": 100},
                    {"kind": "stock", "ticker": "VALE3", "side": "long", "quantity": 100},
                ],
            },
        )
        check(
            mixed_underlying.status_code == 400
            and error_of(mixed_underlying).get("error", {}).get("code") == "underlying_mismatch",
            "papéis diferentes → 400 underlying_mismatch",
            f"HTTP {mixed_underlying.status_code}",
        )
        unknown = await http.post(
            "/api/structure",
            json={
                "underlying": "XXXX9",
                "date": SESSION,
                "legs": [
                    {"kind": "option", "ticker": "XXXX9", "side": "long", "quantity": 100}
                ],
            },
        )
        check(
            unknown.status_code == 404
            and error_of(unknown).get("error", {}).get("code") == "ticker_not_found",
            "ticker inexistente → 404 ticker_not_found",
            f"HTTP {unknown.status_code}",
        )
        no_session = await http.post(
            "/api/structure",
            json={
                "underlying": "PETR4",
                "date": "2026-11-21",
                "legs": [
                    {"kind": "option", "ticker": "PETRL44", "side": "long", "quantity": 100}
                ],
            },
        )
        check(
            no_session.status_code == 404
            and error_of(no_session).get("error", {}).get("code") == "no_session",
            "data sem sessão → 404 no_session",
            f"HTTP {no_session.status_code}",
        )
        check(
            all(
                set(body.get("error", {})) == {"code", "message", "hint"}
                for body in errors
                if body.get("error")
            ),
            "todos os erros com code/message/hint",
        )
        serialized = json.dumps(errors, ensure_ascii=False)
        check(
            "Traceback" not in serialized and 'File "' not in serialized,
            "nenhum traceback nas respostas de erro",
        )

        print("\nLimites e vocabulário:")
        over_long = [
            (name, len(items))
            for item in PAYLOADS
            for name, items in lists_in(item)
            if len(items) > 60
        ]
        check(not over_long, "nenhuma lista > 60 linhas", json.dumps(over_long))
        text_all = json.dumps(PAYLOADS, ensure_ascii=False)
        # `premium` é o nome de campo do contrato (`net_premium_brl`, `premium_brl`);
        # o critério é sobre vocabulário preditivo (`premium_rich/cheap` etc.), então
        # o termo só é aceito como parte desses nomes de campo.
        for field in (
            "net_premium_low_brl",
            "net_premium_high_brl",
            "net_premium_brl",
            "premium_brl",
        ):
            text_all = text_all.replace(field, "campo")
        check(
            not BANNED.findall(text_all),
            "grep preditivo nas respostas = 0 (fora o nome de campo net_premium_brl)",
            json.dumps(sorted(set(BANNED.findall(text_all)))),
        )
        biggest = max(
            len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode())
            for item in PAYLOADS
        )
        check(biggest < 200_000, "maior resposta < 200 KB", f"{biggest} bytes")

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
