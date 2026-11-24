"""Fumaça F6 em schema PostgreSQL descartável, com o lago real.

O script nunca aceita caminho de arquivo, nunca apaga banco/volume e nunca lê o
`APP_TOKEN` do dono. Ele cria `f6_smoke_<uuid>` no `DATABASE_URL` recebido, aplica
a migration Alembic só nesse schema, sobe um uvicorn temporário em porta alta e
remove o schema no `finally`. Não é suíte de testes (ADR-019).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import httpx
from mcp import Client
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError

from b3o.derive.position import marked_pnl
from b3o.query.service import QueryService, load_prepare_state

SESSION = date(2026, 11, 19)
LATE_SESSION = date(2026, 11, 23)
OPEN_SESSION = date(2026, 11, 18)
PORT = int(os.environ.get("B3_F6_SMOKE_PORT", "18084"))
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(32)
SCHEMA = f"f6_smoke_{uuid.uuid4().hex}"
BANNED = re.compile(
    r"signal_class|premium_rich|premium_cheap|acerto|ranking|caro|barato|recomend",
    re.IGNORECASE,
)
RESULTS: list[tuple[bool, str]] = []
PAYLOADS: list[dict[str, Any]]


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


def near(got: float | None, expected: float | None, tolerance: float = 0.011) -> bool:
    if got is None or expected is None:
        return got is expected
    return abs(float(got) - float(expected)) <= tolerance


def headers(token: str | None = TOKEN) -> dict[str, str]:
    return {} if token is None else {"X-App-Token": token}


def wait_ready(client: httpx.Client) -> None:
    last = "sem resposta"
    for _ in range(600):
        try:
            response = client.get("/api/health", timeout=5)
            if response.status_code == 200:
                return
            last = f"HTTP {response.status_code}: {response.text[:500]}"
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.1)
    raise AssertionError(f"uvicorn temporário não ficou pronto — {last}")


def independent_covered_pnl() -> tuple[float, float, float, float, float]:
    """Entrada/saída da coberta lidas direto do Parquet, sem QueryService."""
    raw = "/data/raw_cotahist/year=2026/part-0.parquet"
    metrics = "/data/metrics_series/year=2026/part-0.parquet"
    with duckdb.connect() as connection:
        spots = connection.execute(
            "SELECT refdate, close FROM read_parquet(?)"
            " WHERE ticker = 'PETR4' AND tpmerc = '010'"
            " AND refdate IN (CAST(? AS DATE), CAST(? AS DATE)) ORDER BY refdate",
            [raw, OPEN_SESSION, SESSION],
        ).fetchall()
        options = connection.execute(
            "SELECT refdate, mid FROM read_parquet(?)"
            " WHERE ticker = 'PETRL44' AND maturity = CAST('2026-12-18' AS DATE)"
            " AND refdate IN (CAST(? AS DATE), CAST(? AS DATE)) ORDER BY refdate",
            [metrics, OPEN_SESSION, SESSION],
        ).fetchall()
    spot_entry, spot_exit = float(spots[0][1]) / 100, float(spots[1][1]) / 100
    option_entry, option_exit = float(options[0][1]) / 100, float(options[1][1]) / 100
    pnl = 100 * (spot_exit - spot_entry) - 100 * (option_exit - option_entry)
    return pnl, spot_entry, spot_exit, option_entry, option_exit


async def mcp_positions(**params: Any) -> dict[str, Any]:
    async with Client(f"{BASE}/mcp") as client:
        result = await client.call_tool("positions", params)
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(result, "content", []) or []:
        text_value = getattr(block, "text", None)
        if text_value:
            return json.loads(text_value)
    raise AssertionError("tool positions sem JSON")


def schema_database_url(database_url: str) -> str:
    url = make_url(database_url)
    query = dict(url.query)
    query["options"] = f"-csearch_path={SCHEMA}"
    return url.set(query=query).render_as_string(hide_password=False)


@contextmanager
def temporary_server(environment: dict[str, str], log_path: Path) -> Iterator[None]:
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "b3o.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
            ],
            cwd="/app",
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            yield
        except Exception:
            log.flush()
            print(log_path.read_text(encoding="utf-8")[-4000:])
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def official_counts(engine: Engine) -> dict[str, int | None]:
    """Retrato read-only do schema oficial; o smoke só compara antes/depois."""
    counts: dict[str, int | None] = {}
    with engine.connect() as connection:
        for table in (
            "position",
            "position_leg",
            "position_event",
            "prediction",
            "decision",
            "prediction_evaluation",
        ):
            exists = connection.execute(
                text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}
            ).scalar_one()
            counts[table] = (
                None
                if exists is None
                else int(
                    connection.execute(text(f"SELECT count(*) FROM public.{table}")).scalar_one()
                )
            )
    return counts


def schema_assertions(engine: Engine) -> None:
    with engine.connect() as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = :schema "
                    "AND tablename <> 'alembic_version'"
                ),
                {"schema": SCHEMA},
            )
        }
        check(
            tables
            == {
                "position",
                "position_leg",
                "position_event",
                "prediction",
                "decision",
                "prediction_evaluation",
            },
            "schema temporário tem as seis tabelas do journal",
            ", ".join(sorted(tables)),
        )
        triggers = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT t.tgname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE NOT t.tgisinternal AND n.nspname = :schema"
                ),
                {"schema": SCHEMA},
            )
        }
        check(
            triggers
            == {
                "position_no_update_delete",
                "position_leg_no_update_delete",
                "position_event_no_update_delete",
                "prediction_no_update_delete",
                "decision_no_update_delete",
                "prediction_evaluation_no_update_delete",
                "position_no_truncate",
                "position_leg_no_truncate",
                "position_event_no_truncate",
                "prediction_no_truncate",
                "decision_no_truncate",
                "prediction_evaluation_no_truncate",
            },
            "triggers append-only de UPDATE/DELETE/TRUNCATE estão instalados",
        )
        indexes = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = :schema "
                    "AND indexname IN ('position_event_one_open_idx', "
                    "'position_event_one_close_idx')"
                ),
                {"schema": SCHEMA},
            )
        }
        check(
            indexes == {"position_event_one_open_idx", "position_event_one_close_idx"},
            "índices únicos de open/close estão instalados",
        )


def append_only_assertions(engine: Engine, position_id: str) -> None:
    blocked: list[bool] = []
    for table in ("position", "position_leg", "position_event", "prediction"):
        for sql in (
            f"UPDATE {table} SET created_at = created_at WHERE "
            + ("id = :position_id" if table == "position" else "position_id = :position_id"),
            f"DELETE FROM {table} WHERE "
            + ("id = :position_id" if table == "position" else "position_id = :position_id"),
        ):
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(text(sql), {"position_id": position_id})
                except DBAPIError:
                    blocked.append(True)
                    transaction.rollback()
                else:
                    blocked.append(False)
                    transaction.rollback()
    check(
        all(blocked) and len(blocked) == 8,
        "triggers bloqueiam UPDATE/DELETE nas quatro tabelas",
    )
    truncate_blocked: list[bool] = []
    for table in ("position", "position_leg", "position_event", "prediction"):
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(f"TRUNCATE TABLE {table}"))
            except DBAPIError:
                truncate_blocked.append(True)
                transaction.rollback()
            else:
                truncate_blocked.append(False)
                transaction.rollback()
    check(all(truncate_blocked), "triggers bloqueiam TRUNCATE nas quatro tabelas")
    with engine.connect() as connection:
        duplicates = connection.execute(
            text(
                "SELECT count(*) FROM (SELECT position_id FROM position_event "
                "WHERE event IN ('open','close') GROUP BY position_id, event HAVING count(*) > 1)"
            )
        ).scalar_one()
        check(duplicates == 0, "nenhum open/close duplicado no schema temporário")


def without_notes(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: without_notes(item) for key, item in value.items() if key != "notes"}
    if isinstance(value, list):
        return [without_notes(item) for item in value]
    return value


def main() -> int:
    global PAYLOADS
    PAYLOADS = []
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL é obrigatória; o smoke não usa banco default")
        return 2
    if not SCHEMA.startswith("f6_smoke_"):
        raise AssertionError("schema de fumaça inválido")
    isolated_url = schema_database_url(database_url)
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    isolated_engine = create_engine(isolated_url, pool_pre_ping=True)
    before_official = official_counts(admin_engine)
    environment = os.environ.copy()
    environment["DATABASE_URL"] = isolated_url
    environment["APP_TOKEN"] = TOKEN
    environment["B3_DATA_DIR"] = "/data"
    environment["LOG_LEVEL"] = "WARNING"

    with tempfile.TemporaryDirectory(prefix="b3o-f6-smoke-") as temporary:
        environment["B3_FRONTEND_DIR"] = str(Path(temporary) / "frontend")
        first_log = Path(temporary) / "server-before.log"
        second_log = Path(temporary) / "server-after.log"
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))

            # GET sem migration: erro explícito e nenhuma tabela criada.
            with temporary_server(environment, first_log):
                with httpx.Client(base_url=BASE, timeout=30.0) as client:
                    wait_ready(client)
                    no_schema = client.get("/api/positions")
                    body = no_schema.json()
                    PAYLOADS.append(body)
                    check(
                        no_schema.status_code == 503
                        and body.get("error", {}).get("code") == "storage_error",
                        "GET sem schema → 503 storage_error",
                        str(no_schema.status_code),
                    )
            with admin_engine.connect() as connection:
                created = connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = :schema"
                    ),
                    {"schema": SCHEMA},
                ).scalar_one()
            check(created == 0, "GET não cria schema/tabela")

            migration = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd="/app",
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            check(migration.returncode == 0, "Alembic aplica 0001…0003 no schema temporário")
            if migration.returncode != 0:
                print(migration.stdout[-2000:])
                print(migration.stderr[-2000:])
                return 1

            with temporary_server(environment, second_log):
                with httpx.Client(base_url=BASE, timeout=120.0) as client:
                    wait_ready(client)
                    empty = client.get("/api/positions").json()
                    PAYLOADS.append(empty)
                    check(
                        empty["data"]["positions"] == []
                        and empty["data"]["totals"]["open_positions"] == 0,
                        "schema migrado vazio devolve Carteira vazia e total zero",
                    )
                    schema_assertions(isolated_engine)

                    malformed_unauth = client.post(
                        "/api/positions",
                        content="{",
                        headers={"Content-Type": "application/json"},
                    )
                    check(
                        malformed_unauth.status_code == 401
                        and malformed_unauth.json()["error"]["code"] == "unauthorized",
                        "autenticação ocorre antes do parse/validação do corpo",
                    )
                    wrong = client.post("/api/positions", headers=headers("errado"), json={})
                    check(wrong.status_code == 401, "token errado → 401")
                    non_ascii = client.post(
                        "/api/positions",
                        headers=[("x-app-token", b"\xff")],
                        content=b"{}",
                    )
                    check(non_ascii.status_code == 401, "token não ASCII → 401, nunca 500")

                    covered_body: dict[str, Any] = {
                        "client_position_id": str(uuid.uuid4()),
                        "underlying": "PETR4",
                        "date": OPEN_SESSION.isoformat(),
                        "notes": "coberta de fumaça; recomendação é texto livre",
                        "capital_limit_brl": 5000,
                        "legs": [
                            {"kind": "stock", "ticker": "PETR4", "side": "long", "quantity": 100},
                            {
                                "kind": "option",
                                "ticker": "PETRL44",
                                "side": "short",
                                "quantity": 100,
                            },
                        ],
                        "prediction": {
                            "metric": "vrp",
                            "operator": ">=",
                            "value": 0,
                            "horizon_sessions": 5,
                            "evaluate_on": "2026-11-26",
                        },
                    }
                    opened_response = client.post(
                        "/api/positions", headers=headers(), json=covered_body
                    )
                    opened = opened_response.json()
                    PAYLOADS.append(opened)
                    check(opened_response.status_code == 200, "POST /api/positions 200")
                    if opened_response.status_code != 200:
                        print(json.dumps(opened, ensure_ascii=False))
                        return 1
                    check(opened["data"]["kind"] == "covered", "kind derivado = covered")
                    check(
                        opened["data"]["capital_committed_brl"] == 4152.0,
                        "capital comprometido = 4152",
                        str(opened["data"]["capital_committed_brl"]),
                    )
                    check(
                        near(opened["data"]["breakeven_brl"][0], 41.52),
                        "breakeven da coberta = 41,52",
                    )
                    check(
                        opened["data"]["prediction"]["status"] == "pending",
                        "previsão gravada como pending",
                    )
                    position_id = opened["data"]["id"]

                    retry = client.post(
                        "/api/positions", headers=headers(), json=covered_body
                    ).json()
                    PAYLOADS.append(retry)
                    check(
                        retry.get("data", {}).get("id") == position_id,
                        "POST repetido com o mesmo client_position_id é idempotente",
                    )
                    conflict_body = {**covered_body, "notes": "conteúdo diferente"}
                    conflict = client.post("/api/positions", headers=headers(), json=conflict_body)
                    check(
                        conflict.status_code == 409
                        and conflict.json()["error"]["code"] == "position_id_conflict",
                        "mesmo UUID com outro corpo → 409 position_id_conflict",
                    )

                    second_body = {
                        "client_position_id": str(uuid.uuid4()),
                        "underlying": "PETR4",
                        "date": OPEN_SESSION.isoformat(),
                        "notes": "segunda posição de fumaça",
                        "legs": covered_body["legs"],
                    }
                    second = client.post(
                        "/api/positions", headers=headers(), json=second_body
                    ).json()
                    PAYLOADS.append(second)
                    second_id = second["data"]["id"]

                    page0 = client.get(
                        "/api/positions",
                        params={"date": SESSION.isoformat(), "max_rows": 1, "offset": 0},
                    ).json()
                    page1 = client.get(
                        "/api/positions",
                        params={"date": SESSION.isoformat(), "max_rows": 1, "offset": 1},
                    ).json()
                    PAYLOADS.extend([page0, page1])
                    check(
                        page0["meta"]["total"] == 2
                        and page0["meta"]["next_offset"] == 1
                        and page1["meta"]["next_offset"] is None
                        and page0["data"]["positions"][0]["id"]
                        != page1["data"]["positions"][0]["id"],
                        "paginação offset/next_offset cobre duas páginas",
                    )
                    portfolio = client.get(
                        "/api/positions", params={"date": SESSION.isoformat(), "max_rows": 60}
                    ).json()
                    PAYLOADS.append(portfolio)
                    check(
                        portfolio["data"]["totals"]["open_positions"]
                        == portfolio["meta"]["total"]
                        == 2,
                        "totals.open_positions compara com meta.total",
                    )
                    expected_pnl, spot_in, spot_out, option_in, option_out = (
                        independent_covered_pnl()
                    )
                    first_summary = next(
                        row for row in portfolio["data"]["positions"] if row["id"] == position_id
                    )
                    check(
                        near(first_summary["pnl_brl"], expected_pnl),
                        "P&L da coberta confere com leitura direta do Parquet",
                        f"got {first_summary['pnl_brl']} · esperado {expected_pnl:.2f}",
                    )
                    check(
                        near(first_summary["delta"], portfolio["data"]["totals"]["delta"] / 2),
                        "delta da posição = contribuição para o total",
                    )
                    mcp0 = asyncio.run(
                        mcp_positions(date=SESSION.isoformat(), max_rows=1, offset=0)
                    )
                    mcp1 = asyncio.run(
                        mcp_positions(date=SESSION.isoformat(), max_rows=1, offset=1)
                    )
                    PAYLOADS.extend([mcp0, mcp1])
                    check(mcp0 == page0 and mcp1 == page1, "MCP positions = HTTP nas duas páginas")
                    check(
                        mcp0["data"]["totals"]["open_positions"] == mcp0["meta"]["total"],
                        "MCP compara totals com meta.total, não com len(positions)",
                    )

                    historical = client.get(
                        "/api/positions", params={"date": OPEN_SESSION.isoformat()}
                    ).json()
                    check(
                        historical["data"]["totals"]["open_positions"] == 2,
                        "posição é aberta na data pedida mesmo fechando depois",
                    )
                    historical_detail = client.get(
                        f"/api/positions/{position_id}",
                        params={"date": OPEN_SESSION.isoformat()},
                    ).json()
                    check(
                        historical_detail["data"]["status"] == "open"
                        and historical_detail["data"]["closed_session"] is None,
                        "detalhe histórico antes do close continua aberto",
                    )

                    closed_response = client.post(
                        f"/api/positions/{position_id}/close",
                        headers=headers(),
                        json={"date": SESSION.isoformat()},
                    )
                    closed = closed_response.json()
                    PAYLOADS.append(closed)
                    check(closed_response.status_code == 200, "POST close 200")
                    check(closed["data"]["status"] == "closed", "status derivado = closed")
                    check(
                        near(closed["data"]["pnl_brl"], expected_pnl),
                        "P&L realizado = marcação do fechamento",
                    )
                    check(
                        all(mark["leg_id"] for mark in closed["data"]["marks"])
                        and len(closed["data"]["marks"]) == 2,
                        "snapshot do close guarda as marcas por leg_id",
                    )
                    later_detail = client.get(
                        f"/api/positions/{position_id}",
                        params={"date": LATE_SESSION.isoformat()},
                    ).json()
                    check(
                        later_detail["as_of_session"] == SESSION.isoformat()
                        and near(later_detail["data"]["pnl_brl"], expected_pnl),
                        "detalhe fechado usa snapshot do close, não outra sessão",
                    )

                    def close_second() -> httpx.Response:
                        with httpx.Client(base_url=BASE, timeout=120.0) as parallel:
                            return parallel.post(
                                f"/api/positions/{second_id}/close",
                                headers=headers(),
                                json={"date": SESSION.isoformat()},
                            )

                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = [pool.submit(close_second) for _ in range(2)]
                        concurrent = [future.result() for future in futures]
                    statuses = sorted(response.status_code for response in concurrent)
                    check(
                        statuses == [200, 409]
                        and any(
                            response.status_code == 409
                            and response.json()["error"]["code"] == "position_closed"
                            for response in concurrent
                        ),
                        "fechamento concorrente cria um evento; o outro recebe 409",
                        str(statuses),
                    )
                    after = client.get(
                        "/api/positions", params={"date": SESSION.isoformat()}
                    ).json()
                    PAYLOADS.append(after)
                    check(
                        after["data"]["positions"] == []
                        and after["data"]["totals"]["open_positions"] == 0,
                        "posições fechadas saem da Carteira",
                    )

                    expired_body = {
                        "client_position_id": str(uuid.uuid4()),
                        "underlying": "PETR4",
                        "date": OPEN_SESSION.isoformat(),
                        "legs": [
                            {
                                "kind": "option",
                                "ticker": "PETRK121",
                                "side": "long",
                                "quantity": 100,
                            }
                        ],
                    }
                    expired = client.post(
                        "/api/positions", headers=headers(), json=expired_body
                    ).json()
                    PAYLOADS.append(expired)
                    expired_id = expired["data"]["id"]
                    expired_detail = client.get(
                        f"/api/positions/{expired_id}",
                        params={"date": LATE_SESSION.isoformat()},
                    ).json()
                    PAYLOADS.append(expired_detail)
                    check(
                        expired_detail["data"]["status"] == "open"
                        and expired_detail["data"]["days_to_expiry"] == 0
                        and expired_detail["data"]["pnl_brl"] is None
                        and "mark_missing" in expired_detail["data"]["flags"]
                        and "event_in_window" in expired_detail["data"]["flags"],
                        "posição vencida continua na carteira, "
                        "sem dias negativos nem P&L inventado",
                    )
                    blocked_close = client.post(
                        f"/api/positions/{expired_id}/close",
                        headers=headers(),
                        json={"date": LATE_SESSION.isoformat()},
                    )
                    check(
                        blocked_close.status_code == 400,
                        "fechamento sem marca positiva é bloqueado",
                    )

                    naked_body = {
                        "client_position_id": str(uuid.uuid4()),
                        "underlying": "PETR4",
                        "date": OPEN_SESSION.isoformat(),
                        "legs": [
                            {
                                "kind": "option",
                                "ticker": "PETRL44",
                                "side": "short",
                                "quantity": 100,
                            }
                        ],
                    }
                    naked = client.post("/api/positions", headers=headers(), json=naked_body).json()
                    PAYLOADS.append(naked)
                    check(
                        naked["data"]["capital_committed_brl"] is None
                        and naked["data"]["pnl_pct"] is None
                        and "unbounded_loss" in naked["data"]["flags"],
                        "risco ilimitado usa capital/percentual nulos e flag",
                    )

                    mismatch_body = {
                        "client_position_id": str(uuid.uuid4()),
                        "underlying": "PETR4",
                        "date": OPEN_SESSION.isoformat(),
                        "legs": [
                            {"kind": "stock", "ticker": "PETR4", "side": "long", "quantity": 100},
                            {
                                "kind": "option",
                                "ticker": "PETRL44",
                                "side": "short",
                                "quantity": 101,
                            },
                        ],
                    }
                    mismatch = client.post(
                        "/api/positions", headers=headers(), json=mismatch_body
                    ).json()
                    check(
                        mismatch["data"]["kind"] == "custom",
                        "kind clássico exige quantidades compatíveis",
                    )

                    invalid_prediction = {
                        **covered_body,
                        "client_position_id": str(uuid.uuid4()),
                        "prediction": {
                            "metric": "vrp",
                            "operator": ">=",
                            "value": 0,
                            "horizon_sessions": 5,
                            "evaluate_on": "2026-11-25",
                        },
                    }
                    bad_prediction = client.post(
                        "/api/positions", headers=headers(), json=invalid_prediction
                    )
                    check(
                        bad_prediction.status_code == 400,
                        "previsão exige evaluate_on coerente com horizon_sessions",
                    )
                    big_quantity = {
                        **covered_body,
                        "client_position_id": str(uuid.uuid4()),
                        "legs": [
                            {
                                "kind": "stock",
                                "ticker": "PETR4",
                                "side": "long",
                                "quantity": 2**63,
                            }
                        ],
                    }
                    check(
                        client.post(
                            "/api/positions", headers=headers(), json=big_quantity
                        ).status_code
                        == 400,
                        "inteiro acima do int64 é rejeitado",
                    )
                    check(
                        client.post(
                            "/api/positions",
                            headers=headers(),
                            json={**covered_body, "notes": "x" * 2001},
                        ).status_code
                        == 400,
                        "nota acima de 2000 caracteres é rejeitada",
                    )
                    check(
                        client.post(
                            "/api/positions",
                            headers=headers(),
                            json={
                                **covered_body,
                                "legs": [
                                    {
                                        "kind": "option",
                                        "ticker": "X" * 33,
                                        "side": "long",
                                        "quantity": 100,
                                    }
                                ],
                            },
                        ).status_code
                        == 400,
                        "ticker acima de 32 caracteres é rejeitado",
                    )

                    fake_leg = SimpleNamespace(
                        id="leg",
                        ticker="PETR4",
                        side="long",
                        type="stock",
                        qty=1,
                        entry_price_brl=1.0,
                        entry_strike_brl=None,
                        maturity=None,
                    )
                    zero_guard = all(
                        _raises_for_mark(fake_leg, value)
                        for value in (0.0, -1.0, float("nan"), float("inf"))
                    )
                    check(zero_guard, "marca zero/negativa/não finita não vira preço")

                    service = QueryService(Path("/data"), database_url=isolated_url)
                    state = load_prepare_state(Path("/data"))
                    check(
                        "AXIA6" not in state.universe
                        and service._position_iv("AXIA6", date(2026, 6, 1)) is not None,
                        "IV da posição lê metrics_underlying mesmo fora do universo",
                    )

                    append_only_assertions(isolated_engine, position_id)

                    serialized = json.dumps(without_notes(PAYLOADS), ensure_ascii=False)
                    check(not BANNED.findall(serialized), "grep preditivo = 0 fora de notes")
                    check("Traceback" not in serialized, "nenhum traceback nas respostas")
                    biggest = max(
                        len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode())
                        for item in PAYLOADS
                    )
                    check(biggest < 200_000, "maior resposta abaixo de 200 KB", f"{biggest} bytes")
                    print(
                        "\nCONFERÊNCIA "
                        f"spot {spot_in:.2f}→{spot_out:.2f} · "
                        f"opção {option_in:.2f}→{option_out:.2f}"
                        f" · P&L {expected_pnl:.2f}"
                    )
        finally:
            try:
                with admin_engine.begin() as connection:
                    connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            finally:
                isolated_engine.dispose()
                admin_engine.dispose()

    after_official = official_counts(create_engine(database_url, pool_pre_ping=True))
    check(after_official == before_official, "schema oficial não recebeu nenhuma escrita do smoke")
    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} critérios PASS")
    if failed:
        print("FALHAS: " + "; ".join(failed))
        return 1
    print("SMOKE OK")
    return 0


def _raises_for_mark(leg: SimpleNamespace, value: float) -> bool:
    try:
        marked_pnl([leg], {leg.id: value})
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
