"""Fumaça do F5-E — `GET /api/liquid` (não é suíte de testes, ADR-019).

Prova no lago real: top por volume financeiro, desempate, meta honesto, teto de
60, erros no envelope, correspondência com `chain`/`series` e uma conferência
independente do volume no Parquet. Nada é gravado.

URLs por ambiente: `B3_HTTP_URL` (default `http://host.containers.internal:8080`)
e `B3_DATA_DIR` (default `/data`).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import duckdb
import httpx

HTTP_URL = os.environ.get("B3_HTTP_URL", "http://host.containers.internal:8080")
DATA_DIR = Path(os.environ.get("B3_DATA_DIR", "/data"))
SESSION = "2026-11-23"
BANNED = re.compile(
    r"signal_class|premium_rich|premium_cheap|acerto|ranking|caro|barato|recomend",
    re.IGNORECASE,
)
REQUIRED = {
    "underlying",
    "ticker",
    "side",
    "style",
    "strike_brl",
    "maturity",
    "volume_brl",
    "trades",
    "spread_brl",
    "spread_pct",
    "iv_pct",
    "delta",
    "is_liquid",
}

RESULTS: list[tuple[bool, str]] = []
PAYLOADS: list[dict[str, Any]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((ok, label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


def near(got: float | None, expected: float | None, tolerance: float = 1e-6) -> bool:
    if got is None or expected is None:
        return got is expected
    return abs(float(got) - float(expected)) <= tolerance


def main() -> int:
    print(f"HTTP {HTTP_URL}\n")
    with httpx.Client(base_url=HTTP_URL, timeout=120.0) as http:
        response = http.get("/api/liquid", params={"date": SESSION, "limit": 20})
        body = response.json()
        PAYLOADS.append(body)
        check(response.status_code == 200, "GET /api/liquid 200", str(response.status_code))
        check(
            body.get("as_of_session") == SESSION,
            "as_of_session = 2026-11-23",
            str(body.get("as_of_session")),
        )
        check(body.get("model_version") == 2, "model_version = 2", str(body.get("model_version")))
        rows = body.get("data", {}).get("rows", [])
        meta = body.get("meta", {})
        check(bool(rows), "top 20 não vazio", f"n={len(rows)}")
        check(len(rows) <= 20, "respeita limit=20", str(len(rows)))
        check(meta.get("total", -1) >= len(rows), "meta.total antes do corte", json.dumps(meta))
        check(
            meta.get("truncated") == (len(rows) < meta.get("total", 0)),
            "meta.truncated coerente",
            json.dumps(meta),
        )
        check(
            all(REQUIRED <= set(row) for row in rows),
            "todo registro tem os campos do contrato",
            ",".join(sorted(REQUIRED)),
        )
        check(
            all(row["is_liquid"] is True for row in rows),
            "todas as linhas são líquidas",
            f"n={len(rows)}",
        )
        check(
            all(row["volume_brl"] > 0 for row in rows),
            "volume financeiro positivo",
            f"n={len(rows)}",
        )
        order = [(-row["volume_brl"], -row["trades"], row["ticker"]) for row in rows]
        check(order == sorted(order), "ordem volume DESC, trades DESC, ticker ASC")

        capped = http.get("/api/liquid", params={"date": SESSION, "limit": 999}).json()
        PAYLOADS.append(capped)
        check(
            len(capped["data"]["rows"]) <= 60,
            "limit acima do teto é cortado em 60",
            str(len(capped["data"]["rows"])),
        )
        empty = http.get("/api/liquid", params={"date": SESSION, "limit": 0}).json()
        PAYLOADS.append(empty)
        check(
            empty["data"]["rows"] == []
            and empty["meta"]["total"] == meta["total"]
            and empty["meta"]["truncated"],
            "limit=0 devolve lista vazia e total honesto",
            json.dumps(empty["meta"]),
        )

        matched: dict[str, Any] | None = None
        chain_row: dict[str, Any] | None = None
        for candidate in rows[:10]:
            chain = http.get(
                f"/api/t/{candidate['underlying']}/chain",
                params={
                    "date": SESSION,
                    "expiry": candidate["maturity"],
                    "delta_min": 0,
                    "delta_max": 1,
                    "liquidity": "strict",
                    "max_rows": 60,
                },
            )
            if chain.status_code != 200:
                continue
            chain_body = chain.json()
            PAYLOADS.append(chain_body)
            found = next(
                (row for row in chain_body["data"]["rows"] if row["ticker"] == candidate["ticker"]),
                None,
            )
            if found is not None:
                matched, chain_row = candidate, found
                break
        check(
            matched is not None and chain_row is not None,
            "uma série do top aparece no chain correspondente",
        )
        if matched is not None and chain_row is not None:
            check(
                near(matched["iv_pct"], chain_row["iv_pct"])
                and near(matched["delta"], chain_row["delta"])
                and near(matched["spread_brl"], chain_row["spread_brl"])
                and near(matched["spread_pct"], chain_row["spread_pct"])
                and matched["is_liquid"] == chain_row["is_liquid"],
                "IV/delta/spread/liquidez batem com o chain",
                matched["ticker"],
            )
            series = http.get(f"/api/s/{matched['ticker']}/{matched['maturity']}").json()
            PAYLOADS.append(series)
            check(
                near(matched["strike_brl"], series["data"]["strike_brl"])
                and matched["side"] == series["data"]["side"]
                and matched["style"] == series["data"]["style"]
                and near(matched["iv_pct"], series["data"]["last"]["iv_pct"])
                and near(matched["delta"], series["data"]["last"]["delta"]),
                "identidade/IV/delta batem com a série",
                matched["ticker"],
            )

            raw_path = DATA_DIR / "raw_cotahist" / "year=2026" / "part-0.parquet"
            with duckdb.connect() as connection:
                raw = connection.execute(
                    "SELECT volume, trades, best_bid, best_ask, quote_factor"
                    " FROM read_parquet(?) WHERE refdate = ? AND ticker = ?"
                    " AND tpmerc IN ('070', '080')"
                    " ORDER BY volume DESC, trades DESC LIMIT 1",
                    [str(raw_path), SESSION, matched["ticker"]],
                ).fetchone()
            check(raw is not None, "barra bruta encontrada no Parquet", matched["ticker"])
            if raw is not None:
                volume, trades, bid, ask, factor = raw
                factor = int(factor) if factor else 1
                bid_brl = None if bid is None or int(bid) <= 0 else float(bid) / factor / 100.0
                ask_brl = None if ask is None or int(ask) <= 0 else float(ask) / factor / 100.0
                spread_brl = None if bid_brl is None or ask_brl is None else ask_brl - bid_brl
                spread_pct = (
                    None
                    if spread_brl is None or (bid_brl + ask_brl) <= 0
                    else spread_brl / ((bid_brl + ask_brl) / 2.0) * 100.0
                )
                check(
                    near(matched["volume_brl"], int(volume) / 100.0)
                    and matched["trades"] == int(trades)
                    and near(matched["spread_brl"], spread_brl)
                    and near(matched["spread_pct"], spread_pct),
                    "volume/trades/spread conferidos por fora no COTAHIST",
                    f"{matched['ticker']} R$ {int(volume) / 100:.2f}",
                )

        bad_date = http.get("/api/liquid", params={"date": "2026-11-21"})
        check(
            bad_date.status_code == 404
            and bad_date.json().get("error", {}).get("code") == "no_session",
            "sessão inexistente → 404 no_session",
            str(bad_date.status_code),
        )
        malformed = http.get("/api/liquid", params={"date": "ontem"})
        check(
            malformed.status_code == 400
            and malformed.json().get("error", {}).get("code") == "bad_request",
            "data inválida → 400 bad_request",
            str(malformed.status_code),
        )
        bad_limit = http.get("/api/liquid", params={"limit": "abc"})
        check(
            bad_limit.status_code == 400
            and bad_limit.json().get("error", {}).get("code") == "bad_request",
            "limit inválido → 400 bad_request",
            str(bad_limit.status_code),
        )
        serialized = json.dumps(PAYLOADS, ensure_ascii=False)
        check(
            not BANNED.findall(serialized),
            "grep preditivo = 0",
            json.dumps(sorted(set(BANNED.findall(serialized)))),
        )
        check("Traceback" not in serialized, "nenhum traceback nas respostas")
        biggest = max(
            len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode())
            for item in PAYLOADS
        )
        check(biggest < 200_000, "maior resposta abaixo de 200 KB", f"{biggest} bytes")

    if rows:
        top = rows[0]
        print(
            "\nTOP "
            f"{top['ticker']} ({top['underlying']}) · "
            f"volume R$ {top['volume_brl']:.2f} · {top['trades']} negócios · "
            f"IV {top['iv_pct']} · delta {top['delta']}"
        )
    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} critérios PASS")
    if failed:
        print("FALHAS: " + "; ".join(failed))
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
