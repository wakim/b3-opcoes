"""Fumaça F7 em schema PostgreSQL descartável, com o lago real.

Não é suíte automatizada (ADR-019). O script cria `f7_smoke_<uuid>`, aplica
Alembic somente nesse schema, sobe um uvicorn temporário e remove o schema no
`finally`. Nunca lê `APP_TOKEN`, nunca aceita `B3_JOURNAL_PATH` e nunca escreve no
schema oficial.
"""

from __future__ import annotations

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
from typing import Any

import duckdb
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError

from b3o.journal_eval import evaluate_predictions
from b3o.query.service import QueryService

OPEN_SESSION = date(2026, 11, 18)
EVALUATE_ON = date(2026, 11, 19)
OBSERVED_SESSION = date(2026, 11, 23)
PORT = int(os.environ.get("B3_F7_SMOKE_PORT", "18085"))
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(32)
SCHEMA = f"f7_smoke_{uuid.uuid4().hex}"
TABLES = (
    "position",
    "position_leg",
    "position_event",
    "prediction",
    "decision",
    "prediction_evaluation",
)
BANNED = re.compile(
    r"signal_class|premium_rich|premium_cheap|acerto|ranking|caro|barato|recomend",
    re.IGNORECASE,
)
RESULTS: list[tuple[bool, str]] = []
PAYLOADS: list[dict[str, Any]]


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


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
    counts: dict[str, int | None] = {}
    with engine.connect() as connection:
        for table in TABLES:
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


def independent_spot() -> float:
    with duckdb.connect() as connection:
        row = connection.execute(
            "SELECT close FROM read_parquet(?) WHERE refdate = CAST(? AS DATE)"
            " AND ticker = 'PETR4' AND tpmerc = '010'",
            ["/data/raw_cotahist/year=2026/part-0.parquet", OBSERVED_SESSION],
        ).fetchone()
    if row is None:
        raise AssertionError("PETR4 sem fechamento na sessão observada")
    return float(row[0]) / 100.0


def independent_void_ticker() -> str:
    with duckdb.connect() as connection:
        row = connection.execute(
            "SELECT ticker FROM read_parquet(?) WHERE refdate = CAST(? AS DATE)"
            " AND tpmerc = '010' AND ticker IN ("
            " SELECT ticker FROM read_parquet(?) WHERE refdate = CAST(? AS DATE)"
            " AND tpmerc = '010') EXCEPT SELECT underlying FROM read_parquet(?)"
            " WHERE refdate = CAST(? AS DATE) ORDER BY 1 LIMIT 1",
            [
                "/data/raw_cotahist/year=2026/part-0.parquet",
                OBSERVED_SESSION,
                "/data/raw_cotahist/year=2026/part-0.parquet",
                OPEN_SESSION,
                "/data/metrics_underlying/year=2026/part-0.parquet",
                OBSERVED_SESSION,
            ],
        ).fetchone()
    if row is None:
        raise AssertionError("não foi encontrado papel com spot e sem métrica")
    return str(row[0])


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
        check(tables == set(TABLES), "schema temporário tem as seis tabelas do journal")
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
        expected_triggers = {
            f"{table}_no_{mutation}"
            for table in TABLES
            for mutation in ("update_delete", "truncate")
        }
        check(triggers == expected_triggers, "12 triggers append-only estão instalados")
        indexes = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = :schema "
                    "AND indexname IN ('uq_decision_client_id', 'uq_decision_position', "
                    "'prediction_one_decision_idx', 'uq_prediction_evaluation_session')"
                ),
                {"schema": SCHEMA},
            )
        }
        check(
            indexes
            == {
                "uq_decision_client_id",
                "uq_decision_position",
                "prediction_one_decision_idx",
                "uq_prediction_evaluation_session",
            },
            "índices únicos de decisão e avaliação estão instalados",
        )


def append_only_assertions(engine: Engine) -> None:
    blocked = 0
    for table in TABLES:
        for sql in (
            f"UPDATE {table} SET created_at = created_at",
            f"DELETE FROM {table}",
            f"TRUNCATE TABLE {table}",
        ):
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(text(sql))
                except DBAPIError:
                    blocked += 1
                    transaction.rollback()
                else:
                    transaction.rollback()
    check(blocked == len(TABLES) * 3, "UPDATE/DELETE/TRUNCATE bloqueados nas seis tabelas")


def decision_body(
    client_id: str,
    *,
    underlying: str = "PETR4",
    metric: str = "spot",
    operator: str = ">=",
    value: float = 0,
    horizon: int = 1,
    evaluate_on: str = EVALUATE_ON.isoformat(),
    notes: str = "decisão sem posição",
    date: str = OPEN_SESSION.isoformat(),
) -> dict[str, Any]:
    return {
        "client_decision_id": client_id,
        "underlying": underlying,
        "date": date,
        "decision_type": "no_entry",
        "context": "registro de fumaça",
        "notes": notes,
        "prediction": {
            "metric": metric,
            "operator": operator,
            "value": value,
            "horizon_sessions": horizon,
            "evaluate_on": evaluate_on,
        },
    }


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
    if "B3_JOURNAL_PATH" in os.environ:
        print("B3_JOURNAL_PATH não é aceito no journal PostgreSQL")
        return 2
    if not SCHEMA.startswith("f7_smoke_"):
        raise AssertionError("schema de fumaça inválido")
    isolated_url = schema_database_url(database_url)
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    isolated_engine = create_engine(isolated_url, pool_pre_ping=True)
    before_official = official_counts(admin_engine)
    environment = os.environ.copy()
    environment.pop("B3_JOURNAL_PATH", None)
    environment["DATABASE_URL"] = isolated_url
    environment["APP_TOKEN"] = TOKEN
    environment["B3_DATA_DIR"] = "/data"
    environment["LOG_LEVEL"] = "WARNING"
    observed = independent_spot()
    void_ticker = independent_void_ticker()

    with tempfile.TemporaryDirectory(prefix="b3o-f7-smoke-") as temporary:
        environment["B3_FRONTEND_DIR"] = str(Path(temporary) / "frontend")
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))

            with temporary_server(environment, Path(temporary) / "before.log"):
                with httpx.Client(base_url=BASE, timeout=30.0) as client:
                    wait_ready(client)
                    response = client.get("/api/journal")
                    body = response.json()
                    PAYLOADS.append(body)
                    check(
                        response.status_code == 503
                        and body.get("error", {}).get("code") == "storage_error",
                        "GET sem schema → 503 storage_error",
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

            no_token_environment = dict(environment)
            no_token_environment.pop("APP_TOKEN", None)
            with temporary_server(no_token_environment, Path(temporary) / "no-token.log"):
                with httpx.Client(base_url=BASE, timeout=30.0) as client:
                    wait_ready(client)
                    response = client.post(
                        "/api/journal", headers=headers(), json=decision_body(str(uuid.uuid4()))
                    )
                    check(
                        response.status_code == 503
                        and response.json()["error"]["code"] == "app_token_missing",
                        "servidor sem APP_TOKEN → 503 app_token_missing",
                    )

            with temporary_server(environment, Path(temporary) / "server.log"):
                with httpx.Client(base_url=BASE, timeout=120.0) as client:
                    wait_ready(client)
                    schema_assertions(isolated_engine)
                    empty = client.get("/api/journal").json()
                    PAYLOADS.append(empty)
                    check(
                        empty["data"]["decisions"] == [] and empty["meta"]["total"] == 0,
                        "journal migrado vazio devolve lista vazia",
                    )
                    malformed = client.post(
                        "/api/journal", content="{", headers={"Content-Type": "application/json"}
                    )
                    check(
                        malformed.status_code == 401
                        and malformed.json()["error"]["code"] == "unauthorized",
                        "autenticação ocorre antes do parse JSON",
                    )
                    check(
                        client.post(
                            "/api/journal", headers=headers("errado"), json={}
                        ).status_code
                        == 401,
                        "token errado → 401",
                    )
                    check(
                        client.post(
                            "/api/journal",
                            headers=[("x-app-token", b"\xff")],
                            content=b"{}",
                        ).status_code
                        == 401,
                        "token não ASCII → 401",
                    )
                    check(
                        client.post(
                            "/api/journal", headers=headers(), content=b"x" * 65_537
                        ).status_code
                        == 400,
                        "corpo acima de 64 KiB é rejeitado",
                    )

                    base = decision_body(str(uuid.uuid4()), value=observed)
                    invalid_cases = [
                        {**base, "underlying": "PETRL44"},
                        {**base, "underlying": "NAOEXISTE"},
                        {**base, "decision_type": "buy_now"},
                        {**base, "context": "x" * 501},
                        {**base, "notes": "x" * 4001},
                        {**base, "prediction": {**base["prediction"], "metric": "unknown"}},
                        {**base, "prediction": {**base["prediction"], "horizon_sessions": 0}},
                        {**base, "prediction": {**base["prediction"], "horizon_sessions": 253}},
                        {**base, "prediction": {**base["prediction"], "value": 2**63}},
                        {**base, "prediction": {**base["prediction"], "evaluate_on": "2026-11-25"}},
                    ]
                    invalid_responses = [
                        client.post("/api/journal", headers=headers(), json=body)
                        for body in invalid_cases
                    ]
                    check(
                        [response.status_code for response in invalid_responses[:2]] == [404, 404]
                        and all(
                            response.status_code == 400
                            for response in invalid_responses[2:]
                        ),
                        "ticker desconhecido → 404; demais campos inválidos → 400",
                    )
                    nonfinite = client.post(
                        "/api/journal",
                        headers=headers(),
                        content=json.dumps(base).replace('"value": 41.3', '"value": NaN'),
                    )
                    check(nonfinite.status_code == 400, "valor não finito → 400, nunca 500")

                    hit_body = decision_body(str(uuid.uuid4()), value=observed)
                    hit_response = client.post("/api/journal", headers=headers(), json=hit_body)
                    hit_decision = hit_response.json()
                    PAYLOADS.append(hit_decision)
                    check(
                        hit_response.status_code == 200,
                        "POST /api/journal registra decisão sem posição",
                    )
                    if hit_response.status_code != 200:
                        print(json.dumps(hit_decision, ensure_ascii=False))
                        return 1
                    hit_id = hit_decision["data"]["decision"]["id"]
                    check(
                        hit_decision["data"]["decision"]["prediction"]["status"] == "pending",
                        "previsão nasce pending",
                    )
                    retry = client.post("/api/journal", headers=headers(), json=hit_body).json()
                    PAYLOADS.append(retry)
                    check(
                        retry["data"]["decision"]["id"] == hit_id,
                        "mesmo client_decision_id + corpo é idempotente",
                    )
                    conflict = client.post(
                        "/api/journal",
                        headers=headers(),
                        json={**hit_body, "notes": "outro conteúdo"},
                    )
                    check(
                        conflict.status_code == 409
                        and conflict.json()["error"]["code"] == "decision_id_conflict",
                        "mesmo UUID com outro corpo → 409 decision_id_conflict",
                    )

                    miss_body = decision_body(
                        str(uuid.uuid4()), value=observed + 1.0, notes="segunda decisão"
                    )
                    miss_id = client.post(
                        "/api/journal", headers=headers(), json=miss_body
                    ).json()["data"]["decision"]["id"]
                    void_body = decision_body(
                        str(uuid.uuid4()),
                        underlying=void_ticker,
                        metric="iv_atm_30d",
                        value=0.0,
                        notes="métrica ausente",
                    )
                    void_id = client.post(
                        "/api/journal", headers=headers(), json=void_body
                    ).json()["data"]["decision"]["id"]
                    pending_body = decision_body(
                        str(uuid.uuid4()),
                        date=OBSERVED_SESSION.isoformat(),
                        horizon=1,
                        evaluate_on="2026-11-24",
                        notes="ainda sem pregão posterior",
                    )
                    pending_id = client.post(
                        "/api/journal", headers=headers(), json=pending_body
                    ).json()["data"]["decision"]["id"]
                    no_prediction = client.post(
                        "/api/journal",
                        headers=headers(),
                        json={
                            "client_decision_id": str(uuid.uuid4()),
                            "underlying": "PETR4",
                            "date": OPEN_SESSION.isoformat(),
                            "decision_type": "watch",
                            "context": "sem previsão",
                            "notes": "observação livre",
                        },
                    ).json()
                    PAYLOADS.append(no_prediction)
                    check(
                        no_prediction["data"]["decision"]["prediction"] is None,
                        "decisão sem previsão continua sem previsão",
                    )

                    position_body = {
                        "client_position_id": str(uuid.uuid4()),
                        "underlying": "PETR4",
                        "date": OPEN_SESSION.isoformat(),
                        "notes": "posição fechada antes da avaliação",
                        "legs": [
                            {"kind": "stock", "ticker": "PETR4", "side": "long", "quantity": 100}
                        ],
                        "prediction": {
                            "metric": "spot",
                            "operator": ">=",
                            "value": observed,
                            "horizon_sessions": 1,
                            "evaluate_on": EVALUATE_ON.isoformat(),
                        },
                    }
                    position = client.post(
                        "/api/positions", headers=headers(), json=position_body
                    ).json()
                    PAYLOADS.append(position)
                    if "data" not in position:
                        print(json.dumps(position, ensure_ascii=False))
                        return 1
                    position_id = position["data"]["id"]
                    client.post(
                        f"/api/positions/{position_id}/close",
                        headers=headers(),
                        json={"date": EVALUATE_ON.isoformat()},
                    )

                    page0 = client.get(
                        "/api/journal", params={"max_rows": 2, "offset": 0}
                    ).json()
                    page1 = client.get(
                        "/api/journal", params={"max_rows": 2, "offset": 2}
                    ).json()
                    PAYLOADS.extend([page0, page1])
                    check(
                        page0["meta"]["total"] == 6
                        and page0["meta"]["next_offset"] == 2
                        and page1["meta"]["next_offset"] == 4
                        and len(page0["data"]["decisions"]) == 2,
                        "paginação offset/next_offset é honesta",
                    )
                    check(
                        client.get("/api/journal", params={"max_rows": 0}).status_code == 400,
                        "max_rows=0 não gera cursor parado",
                    )
                    check(
                        client.get(
                            "/api/journal", params={"offset": 2**63}
                        ).status_code
                        == 400,
                        "offset acima do int64 → 400",
                    )
                    detail = client.get(f"/api/journal/{hit_id}").json()
                    PAYLOADS.append(detail)
                    check(
                        detail["data"]["decision"]["id"] == hit_id,
                        "GET de detalhe devolve a decisão",
                    )
                    check(
                        client.get(f"/api/journal/{uuid.uuid4()}").status_code == 404,
                        "decisão inexistente → 404",
                    )

                    service = QueryService(Path("/data"), database_url=isolated_url)
                    batch = evaluate_predictions(service, as_of=OBSERVED_SESSION)
                    check(
                        batch.candidates == 4
                        and batch.evaluated == 4
                        and batch.hit == 2
                        and batch.miss == 1
                        and batch.void == 1
                        and batch.pending == 0,
                        "lote avalia hit/miss/void no primeiro pregão posterior",
                        json.dumps(batch.to_dict(), sort_keys=True),
                    )
                    outcomes = {
                        identifier: client.get(f"/api/journal/{identifier}").json()["data"][
                            "decision"
                        ]["prediction"]
                        for identifier in (hit_id, miss_id, void_id, pending_id)
                    }
                    PAYLOADS.extend(outcomes.values())
                    check(
                        outcomes[hit_id]["status"] == "hit"
                        and abs(outcomes[hit_id]["observed"] - observed) < 1e-9
                        and outcomes[hit_id]["evaluated_on"] == OBSERVED_SESSION.isoformat(),
                        "hit compara com o fechamento EOD da sessão correta",
                        f"observado {outcomes[hit_id]['observed']}",
                    )
                    check(outcomes[miss_id]["status"] == "miss", "miss é determinístico")
                    check(
                        outcomes[void_id]["status"] == "void"
                        and outcomes[void_id]["observed"] is None
                        and outcomes[void_id]["void_reason"] == "metric_unavailable",
                        "métrica ausente vira void, nunca miss/zero",
                    )
                    check(
                        outcomes[pending_id]["status"] == "pending"
                        and outcomes[pending_id]["evaluated_on"] is None,
                        "sem pregão posterior no lake, previsão continua pending",
                    )
                    position_detail = client.get(f"/api/positions/{position_id}").json()
                    PAYLOADS.append(position_detail)
                    check(
                        position_detail["data"]["prediction"]["status"] == "hit"
                        and position_detail["data"]["status"] == "closed",
                        "posição fechada continua com previsão avaliável",
                    )
                    repeated = evaluate_predictions(service, as_of=OBSERVED_SESSION)
                    check(repeated.evaluated == 0, "lote repetido não duplica evento")

                    concurrent_body = decision_body(
                        str(uuid.uuid4()), value=observed, notes="avaliação concorrente"
                    )
                    concurrent_id = client.post(
                        "/api/journal", headers=headers(), json=concurrent_body
                    ).json()["data"]["decision"]["id"]

                    def run_batch() -> Any:
                        worker = QueryService(Path("/data"), database_url=isolated_url)
                        return evaluate_predictions(worker, as_of=OBSERVED_SESSION).evaluated

                    with ThreadPoolExecutor(max_workers=2) as pool:
                        created = list(pool.map(lambda _: run_batch(), range(2)))
                    check(
                        sorted(created) == [0, 1],
                        "lote concorrente cria exatamente um evento",
                        str(created),
                    )
                    with isolated_engine.connect() as connection:
                        event_count = connection.execute(
                            text(
                                "SELECT count(*) FROM prediction_evaluation e JOIN prediction p"
                                " ON p.id = e.prediction_id JOIN decision d ON d.id = p.decision_id"
                                " WHERE d.id = :decision_id"
                            ),
                            {"decision_id": concurrent_id},
                        ).scalar_one()
                        seed = connection.execute(
                            text(
                                "SELECT p.status, p.observed FROM prediction p JOIN decision d"
                                " ON d.id = p.decision_id WHERE d.id = :decision_id"
                            ),
                            {"decision_id": hit_id},
                        ).first()
                    check(event_count == 1, "índice único impede avaliação duplicada")
                    check(
                        seed is not None and seed[0] == "pending" and seed[1] is None,
                        "linha prediction permanece pending/null; estado vem do evento",
                    )

                    calibration = client.get(
                        "/api/journal/calibration", params={"max_rows": 60}
                    ).json()
                    PAYLOADS.append(calibration)
                    groups = {
                        (row["metric"], row["horizon_sessions"]): row
                        for row in calibration["data"]["groups"]
                    }
                    spot_group = groups[("spot", 1)]
                    iv_group = groups[("iv_atm_30d", 1)]
                    check(
                        spot_group["n"] == 4
                        and spot_group["hit"] == 3
                        and spot_group["miss"] == 1
                        and spot_group["void"] == 0
                        and abs(spot_group["hit_rate_pct"] - 75.0) < 1e-9
                        and iv_group["n"] == 1
                        and iv_group["void"] == 1
                        and iv_group["hit_rate_pct"] is None,
                        "calibração agrega por métrica/horizonte com n e void separado",
                    )
                    check(
                        calibration["data"]["totals"]["n"] == 5
                        and calibration["data"]["totals"]["void"] == 1,
                        "totais da calibração incluem previsões de posição e decisão",
                    )
                    calibration_page = client.get(
                        "/api/journal/calibration", params={"max_rows": 1, "offset": 1}
                    ).json()
                    check(
                        calibration_page["meta"]["total"] == 2
                        and calibration_page["meta"]["offset"] == 1
                        and calibration_page["meta"]["next_offset"] is None,
                        "relatório de calibração pagina sem cursor parado",
                    )

                    append_only_assertions(isolated_engine)
                    serialized = json.dumps(without_notes(PAYLOADS), ensure_ascii=False)
                    check(not BANNED.findall(serialized), "grep preditivo = 0 fora de notes")
                    check("Traceback" not in serialized, "nenhum traceback nas respostas")
                    biggest = max(
                        len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode())
                        for item in PAYLOADS
                    )
                    check(biggest < 200_000, "maior resposta abaixo de 200 KB", f"{biggest} bytes")
                    print(
                        f"\nCONFERÊNCIA spot PETR4 {OBSERVED_SESSION} = {observed:.2f}"
                        f" · void {void_ticker} sem metrics_underlying"
                    )
        finally:
            try:
                with admin_engine.begin() as connection:
                    connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            finally:
                isolated_engine.dispose()
                admin_engine.dispose()

    after_official = official_counts(create_engine(database_url, pool_pre_ping=True))
    check(after_official == before_official, "schema oficial não recebeu escrita do smoke")
    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} critérios PASS")
    if failed:
        print("FALHAS: " + "; ".join(failed))
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
