"""Journal transacional append-only do F6, em PostgreSQL via SQLAlchemy.

O schema é criado exclusivamente pelo Alembic. Abertura insere `position`,
`position_leg`, evento `open` e `prediction` opcional na mesma transação;
fechamento insere somente evento `close`. Status e sessão de fechamento são
derivados do evento. Triggers do banco bloqueiam UPDATE e DELETE.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from b3o.derive.position import derive_kind
from b3o.ingest.errors import SourceError, SourceFailure

DEFAULT_DATABASE_URL = "postgresql+psycopg://b3o:b3o-local@localhost:5432/b3o"
_MAX_INT64 = 2**63 - 1
_HINT_PAYLOAD = "confira os campos da posição, das pernas e da previsão antes de gravar"
_HINT_STORAGE = "confira DATABASE_URL, o serviço PostgreSQL e as migrations Alembic"
_PREDICTION_STATUSES = frozenset({"pending", "hit", "miss", "void"})
_OPERATORS = frozenset({"<", "<=", ">", ">="})

metadata = MetaData()
position_table = Table(
    "position",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("underlying", String(32), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("opened_session", Date, nullable=False),
    Column("opened_at", DateTime(timezone=True), nullable=False),
    Column("capital_committed_brl", Float, nullable=True),
    Column("capital_limit_brl", Float, nullable=True),
    Column("notes", Text, nullable=False),
    Column("entry_iv_atm_30d", Float, nullable=True),
    Column("breakeven_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
position_leg_table = Table(
    "position_leg",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("position_id", String(36), ForeignKey("position.id"), nullable=False),
    Column("ticker", String(32), nullable=False),
    Column("side", String(8), nullable=False),
    Column("type", String(8), nullable=False),
    Column("maturity", Date, nullable=True),
    Column("qty", BigInteger, nullable=False),
    Column("entry_price_brl", Float, nullable=False),
    Column("entry_strike_brl", Float, nullable=True),
    Column("entry_iv_pct", Float, nullable=True),
    Column("entry_delta", Float, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
position_event_table = Table(
    "position_event",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("position_id", String(36), ForeignKey("position.id"), nullable=False),
    Column("event", String(16), nullable=False),
    Column("session", Date, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
prediction_table = Table(
    "prediction",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("position_id", String(36), ForeignKey("position.id"), nullable=False),
    Column("metric", String(64), nullable=False),
    Column("operator", String(8), nullable=False),
    Column("value", Float, nullable=False),
    Column("horizon_sessions", BigInteger, nullable=False),
    Column("evaluate_on", Date, nullable=False),
    Column("status", String(16), nullable=False),
    Column("observed", Float, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("position_opened_idx", position_table.c.opened_session, position_table.c.id)
Index("position_leg_parent_idx", position_leg_table.c.position_id, position_leg_table.c.id)
Index(
    "position_event_parent_idx",
    position_event_table.c.position_id,
    position_event_table.c.id,
)
Index("prediction_parent_idx", prediction_table.c.position_id, prediction_table.c.id)


def _new_id() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PositionLegRecord:
    """Perna registrada na abertura; preços por ação, quantidade em ações."""

    position_id: str
    ticker: str
    side: str
    type: str
    maturity: date | None
    qty: int
    entry_price_brl: float
    entry_strike_brl: float | None
    entry_iv_pct: float | None
    entry_delta: float | None
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class PositionRecord:
    """Cabeçalho imutável da posição; `kind` deve vir de `derive_kind`."""

    underlying: str
    kind: str
    opened_session: date
    opened_at: datetime
    capital_committed_brl: float | None
    capital_limit_brl: float | None = None
    notes: str = ""
    entry_iv_atm_30d: float | None = None
    breakeven_json: str = "[]"
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class PositionEventRecord:
    """Evento append-only; `close` é a única fonte do status de fechamento."""

    id: str
    position_id: str
    event: str
    session: date
    payload_json: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PredictionRecord:
    """Previsão estruturada da spec §6.12, opcional na abertura."""

    position_id: str
    metric: str
    operator: str
    value: float
    horizon_sessions: int
    evaluate_on: date
    status: str
    observed: float | None
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class PositionOpen(PositionRecord):
    """Posição com pernas, previsão e evento de fechamento opcionais."""

    legs: tuple[PositionLegRecord, ...] = ()
    prediction: PredictionRecord | None = None
    close_event: PositionEventRecord | None = None

    @property
    def status(self) -> str:
        """Status derivado do evento `close`, nunca de uma coluna atualizada."""
        return "closed" if self.close_event is not None else "open"


class JournalStore:
    """Repositório SQLAlchemy do diário; toda escrita é uma transação append-only."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
        try:
            self.engine: Engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                future=True,
            )
        except (TypeError, ValueError, SQLAlchemyError) as exc:
            raise _storage_error("configurar PostgreSQL", exc) from exc

    def open_position(
        self,
        position: PositionRecord,
        legs: Sequence[PositionLegRecord],
        prediction: PredictionRecord | None = None,
        *,
        request_fingerprint: str | None = None,
    ) -> PositionOpen:
        """Grava abertura; repetir o mesmo `request_fingerprint` é idempotente.

        O `position.id` vem de `client_position_id` quando informado. O conflito de
        id é resolvido no PostgreSQL com `ON CONFLICT DO NOTHING`, então duas
        requisições simultâneas não criam duas posições nem disparam 500.
        """
        if not isinstance(position, PositionRecord):
            raise _bad_request("position precisa ser PositionRecord")
        prepared = _prepare_position(position)
        prepared_legs = _prepare_legs(prepared, legs)
        derived_kind = derive_kind(prepared_legs)
        if prepared.kind != derived_kind:
            raise _bad_request(
                f"kind {prepared.kind!r} diverge do derivado das pernas: {derived_kind!r}"
            )
        prepared_prediction = _prepare_prediction(prepared, prediction)
        if prepared_prediction is not None and prepared_prediction.status != "pending":
            raise _bad_request("a previsão na abertura precisa ter status pending")
        fingerprint = (
            None
            if request_fingerprint is None
            else _required_text(request_fingerprint, "request_fingerprint", max_length=128)
        )

        with self._write_connection() as connection:
            inserted_id = connection.execute(
                pg_insert(position_table)
                .values(_position_values(prepared))
                .on_conflict_do_nothing(index_elements=[position_table.c.id])
                .returning(position_table.c.id)
            ).scalar_one_or_none()
            if inserted_id is None:
                existing = connection.execute(
                    select(position_table).where(position_table.c.id == prepared.id)
                ).first()
                if existing is None:
                    raise _storage_error("ler posição idempotente", RuntimeError("linha ausente"))
                opened = _load_positions(connection, [_position_from_row(existing)])[0]
                payload = _open_event_payload(connection, prepared.id)
                if fingerprint is None or payload.get("request_fingerprint") != fingerprint:
                    raise _position_id_conflict(prepared.id)
                return opened

            connection.execute(
                position_leg_table.insert(),
                [_leg_values(leg) for leg in prepared_legs],
            )
            if prepared_prediction is not None:
                connection.execute(
                    prediction_table.insert(),
                    _prediction_values(prepared_prediction),
                )
            connection.execute(
                position_event_table.insert().values(
                    id=_new_id(),
                    position_id=prepared.id,
                    event="open",
                    session=prepared.opened_session,
                    payload_json=_json_object(
                        {} if fingerprint is None else {"request_fingerprint": fingerprint},
                        "open_event",
                    ),
                    created_at=_utc_now(),
                )
            )
        return PositionOpen(
            underlying=prepared.underlying,
            kind=prepared.kind,
            opened_session=prepared.opened_session,
            opened_at=prepared.opened_at,
            capital_committed_brl=prepared.capital_committed_brl,
            capital_limit_brl=prepared.capital_limit_brl,
            notes=prepared.notes,
            entry_iv_atm_30d=prepared.entry_iv_atm_30d,
            breakeven_json=prepared.breakeven_json,
            id=prepared.id,
            created_at=prepared.created_at,
            legs=prepared_legs,
            prediction=prepared_prediction,
        )

    def list_positions(self) -> list[PositionOpen]:
        """Lista posições com pernas/previsão; banco vazio devolve lista vazia."""
        with self._read_connection() as connection:
            try:
                records = [
                    _position_from_row(row)
                    for row in connection.execute(
                        select(position_table).order_by(
                            position_table.c.opened_session.desc(),
                            position_table.c.created_at.desc(),
                            position_table.c.id,
                        )
                    )
                ]
                return _load_positions(connection, records)
            except (TypeError, ValueError) as exc:
                raise _bad_journal(exc) from exc

    def get_position(self, position_id: str) -> PositionOpen | None:
        """Lê uma posição; id ausente devolve `None`."""
        identifier = _required_text(position_id, "position_id", max_length=36)
        with self._read_connection() as connection:
            try:
                row = connection.execute(
                    select(position_table).where(position_table.c.id == identifier)
                ).first()
                if row is None:
                    return None
                return _load_positions(connection, [_position_from_row(row)])[0]
            except (TypeError, ValueError) as exc:
                raise _bad_journal(exc) from exc

    def close_position(
        self,
        position_id: str,
        session: date,
        payload: Mapping[str, Any],
    ) -> PositionOpen:
        """Acrescenta o evento `close`; não atualiza a linha da posição."""
        identifier = _required_text(position_id, "position_id", max_length=36)
        close_session = _date_value(session, "session")
        payload_json = _json_object(payload, "payload")
        with self._write_connection() as connection:
            row = connection.execute(
                select(position_table)
                .where(position_table.c.id == identifier)
                .with_for_update()
            ).first()
            if row is None:
                raise _bad_request(f"posição não encontrada: {identifier}")
            record = _position_from_row(row)
            if close_session < record.opened_session:
                raise _bad_request("a sessão de fechamento não pode anteceder a abertura")
            if _close_event(connection, identifier) is not None:
                raise _position_closed(identifier)
            event = PositionEventRecord(
                id=_new_id(),
                position_id=identifier,
                event="close",
                session=close_session,
                payload_json=payload_json,
                created_at=_utc_now(),
            )
            connection.execute(position_event_table.insert(), _event_values(event))
            return _load_positions(connection, [record], close_events={identifier: event})[0]

    def has_close(self, position_id: str) -> bool:
        """True somente quando existe evento `close`."""
        identifier = _required_text(position_id, "position_id", max_length=36)
        with self._read_connection() as connection:
            return (
                connection.execute(
                    select(position_event_table.c.id)
                    .where(
                        position_event_table.c.position_id == identifier,
                        position_event_table.c.event == "close",
                    )
                    .limit(1)
                ).first()
                is not None
            )

    @contextmanager
    def _write_connection(self) -> Iterator[Connection]:
        try:
            with self.engine.begin() as connection:
                yield connection
        except (TypeError, ValueError) as exc:
            raise _bad_journal(exc) from exc
        except SQLAlchemyError as exc:
            raise _storage_error("gravar journal PostgreSQL", exc) from exc

    @contextmanager
    def _read_connection(self) -> Iterator[Connection]:
        try:
            with self.engine.connect() as connection:
                yield connection
        except SQLAlchemyError as exc:
            raise _storage_error("ler journal PostgreSQL", exc) from exc


def _load_positions(
    connection: Connection,
    records: Sequence[PositionRecord],
    *,
    close_events: Mapping[str, PositionEventRecord] | None = None,
) -> list[PositionOpen]:
    if not records:
        return []
    by_id = {record.id: record for record in records}
    identifiers = tuple(by_id)
    legs: dict[str, list[PositionLegRecord]] = defaultdict(list)
    predictions: dict[str, PredictionRecord] = {}
    closes = dict(close_events or {})
    for row in connection.execute(
        select(position_leg_table)
        .where(position_leg_table.c.position_id.in_(identifiers))
        .order_by(position_leg_table.c.created_at, position_leg_table.c.id)
    ):
        leg = _leg_from_row(row)
        legs[leg.position_id].append(leg)
    for row in connection.execute(
        select(prediction_table)
        .where(prediction_table.c.position_id.in_(identifiers))
        .order_by(prediction_table.c.created_at, prediction_table.c.id)
    ):
        prediction = _prediction_from_row(row)
        predictions[prediction.position_id] = prediction
    for row in connection.execute(
        select(position_event_table)
        .where(
            position_event_table.c.position_id.in_(identifiers),
            position_event_table.c.event == "close",
        )
        .order_by(position_event_table.c.created_at, position_event_table.c.id)
    ):
        event = _event_from_row(row)
        closes[event.position_id] = event
    return [
        PositionOpen(
            underlying=record.underlying,
            kind=record.kind,
            opened_session=record.opened_session,
            opened_at=record.opened_at,
            capital_committed_brl=record.capital_committed_brl,
            capital_limit_brl=record.capital_limit_brl,
            notes=record.notes,
            entry_iv_atm_30d=record.entry_iv_atm_30d,
            breakeven_json=record.breakeven_json,
            id=record.id,
            created_at=record.created_at,
            legs=tuple(legs[record.id]),
            prediction=predictions.get(record.id),
            close_event=closes.get(record.id),
        )
        for record in records
    ]


def _close_event(connection: Connection, position_id: str) -> PositionEventRecord | None:
    row = connection.execute(
        select(position_event_table)
        .where(
            position_event_table.c.position_id == position_id,
            position_event_table.c.event == "close",
        )
        .limit(1)
    ).first()
    return None if row is None else _event_from_row(row)


def _open_event_payload(connection: Connection, position_id: str) -> dict[str, Any]:
    row = connection.execute(
        select(position_event_table.c.payload_json).where(
            position_event_table.c.position_id == position_id,
            position_event_table.c.event == "open",
        )
    ).first()
    if row is None:
        return {}
    try:
        parsed = json.loads(str(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _position_values(record: PositionRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "underlying": record.underlying,
        "kind": record.kind,
        "opened_session": record.opened_session,
        "opened_at": record.opened_at,
        "capital_committed_brl": record.capital_committed_brl,
        "capital_limit_brl": record.capital_limit_brl,
        "notes": record.notes,
        "entry_iv_atm_30d": record.entry_iv_atm_30d,
        "breakeven_json": record.breakeven_json,
        "created_at": record.created_at,
    }


def _leg_values(record: PositionLegRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "position_id": record.position_id,
        "ticker": record.ticker,
        "side": record.side,
        "type": record.type,
        "maturity": record.maturity,
        "qty": record.qty,
        "entry_price_brl": record.entry_price_brl,
        "entry_strike_brl": record.entry_strike_brl,
        "entry_iv_pct": record.entry_iv_pct,
        "entry_delta": record.entry_delta,
        "created_at": record.created_at,
    }


def _prediction_values(record: PredictionRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "position_id": record.position_id,
        "metric": record.metric,
        "operator": record.operator,
        "value": record.value,
        "horizon_sessions": record.horizon_sessions,
        "evaluate_on": record.evaluate_on,
        "status": record.status,
        "observed": record.observed,
        "created_at": record.created_at,
    }


def _event_values(record: PositionEventRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "position_id": record.position_id,
        "event": record.event,
        "session": record.session,
        "payload_json": record.payload_json,
        "created_at": record.created_at,
    }


def _value(row: Any, name: str) -> object:
    return row._mapping[name] if hasattr(row, "_mapping") else row[name]


def _position_from_row(row: Any) -> PositionRecord:
    return PositionRecord(
        id=str(_value(row, "id")),
        underlying=str(_value(row, "underlying")),
        kind=str(_value(row, "kind")),
        opened_session=_parse_date(_value(row, "opened_session"), "opened_session"),
        opened_at=_parse_timestamp(_value(row, "opened_at"), "opened_at"),
        capital_committed_brl=_optional_float(_value(row, "capital_committed_brl")),
        capital_limit_brl=_optional_float(_value(row, "capital_limit_brl")),
        notes=str(_value(row, "notes")),
        entry_iv_atm_30d=_optional_float(_value(row, "entry_iv_atm_30d")),
        breakeven_json=str(_value(row, "breakeven_json")),
        created_at=_parse_timestamp(_value(row, "created_at"), "created_at"),
    )


def _leg_from_row(row: Any) -> PositionLegRecord:
    maturity = _value(row, "maturity")
    return PositionLegRecord(
        id=str(_value(row, "id")),
        position_id=str(_value(row, "position_id")),
        ticker=str(_value(row, "ticker")),
        side=str(_value(row, "side")),
        type=str(_value(row, "type")),
        maturity=_parse_date(maturity, "maturity") if maturity is not None else None,
        qty=int(_value(row, "qty")),
        entry_price_brl=float(_value(row, "entry_price_brl")),
        entry_strike_brl=_optional_float(_value(row, "entry_strike_brl")),
        entry_iv_pct=_optional_float(_value(row, "entry_iv_pct")),
        entry_delta=_optional_float(_value(row, "entry_delta")),
        created_at=_parse_timestamp(_value(row, "created_at"), "created_at"),
    )


def _prediction_from_row(row: Any) -> PredictionRecord:
    return PredictionRecord(
        id=str(_value(row, "id")),
        position_id=str(_value(row, "position_id")),
        metric=str(_value(row, "metric")),
        operator=str(_value(row, "operator")),
        value=float(_value(row, "value")),
        horizon_sessions=int(_value(row, "horizon_sessions")),
        evaluate_on=_parse_date(_value(row, "evaluate_on"), "evaluate_on"),
        status=str(_value(row, "status")),
        observed=_optional_float(_value(row, "observed")),
        created_at=_parse_timestamp(_value(row, "created_at"), "created_at"),
    )


def _event_from_row(row: Any) -> PositionEventRecord:
    return PositionEventRecord(
        id=str(_value(row, "id")),
        position_id=str(_value(row, "position_id")),
        event=str(_value(row, "event")),
        session=_parse_date(_value(row, "session"), "session"),
        payload_json=str(_value(row, "payload_json")),
        created_at=_parse_timestamp(_value(row, "created_at"), "created_at"),
    )


def _prepare_position(position: PositionRecord) -> PositionRecord:
    _date_value(position.opened_session, "opened_session")
    _timestamp(position.opened_at, "opened_at")
    _timestamp(position.created_at, "created_at")
    committed = _optional_number(
        position.capital_committed_brl,
        "capital_committed_brl",
        minimum=0.0,
    )
    limit = _optional_number(position.capital_limit_brl, "capital_limit_brl", minimum=0.0)
    entry_iv = _optional_number(position.entry_iv_atm_30d, "entry_iv_atm_30d", minimum=0.0)
    breakeven_json = _breakeven_json(position.breakeven_json)
    return replace(
        position,
        id=_uuid4(position.id),
        underlying=_required_text(position.underlying, "underlying", max_length=32).upper(),
        kind=_required_text(position.kind, "kind", max_length=32).lower(),
        capital_committed_brl=committed,
        capital_limit_brl=limit,
        notes=_text(position.notes, "notes", max_length=2000),
        entry_iv_atm_30d=entry_iv,
        breakeven_json=breakeven_json,
    )


def _prepare_legs(
    position: PositionRecord,
    legs: Sequence[PositionLegRecord],
) -> tuple[PositionLegRecord, ...]:
    if not isinstance(legs, Sequence) or isinstance(legs, str | bytes):
        raise _bad_request("legs precisa ser uma sequência de PositionLegRecord")
    prepared: list[PositionLegRecord] = []
    identifiers: set[str] = set()
    for raw in legs:
        if not isinstance(raw, PositionLegRecord):
            raise _bad_request("cada perna precisa ser PositionLegRecord")
        _date_or_none(raw.maturity, "maturity")
        _timestamp(raw.created_at, "created_at")
        side = _required_text(raw.side, "side", max_length=8).lower()
        leg_type = _required_text(raw.type, "type", max_length=8).lower()
        if side not in {"long", "short"}:
            raise _bad_request(f"side inválido na perna {raw.ticker!r}: {raw.side!r}")
        if leg_type not in {"stock", "call", "put"}:
            raise _bad_request(f"type inválido na perna {raw.ticker!r}: {raw.type!r}")
        if (
            isinstance(raw.qty, bool)
            or not isinstance(raw.qty, int)
            or not 0 < raw.qty <= _MAX_INT64
        ):
            raise _bad_request("qty precisa ser inteiro positivo dentro do int64")
        if leg_type != "stock" and raw.maturity is None:
            raise _bad_request(f"perna {leg_type} sem maturity: {raw.ticker}")
        strike = _optional_number(raw.entry_strike_brl, "entry_strike_brl", minimum=0.0)
        if leg_type != "stock" and (strike is None or strike <= 0.0):
            raise _bad_request(f"perna {leg_type} sem entry_strike_brl positivo: {raw.ticker}")
        identifier = _uuid4(raw.id)
        if identifier in identifiers:
            raise _bad_request(f"id de perna repetido: {identifier}")
        identifiers.add(identifier)
        if raw.position_id and _required_text(raw.position_id, "position_id") != position.id:
            raise _bad_request("position_id da perna diverge da posição")
        prepared.append(
            replace(
                raw,
                id=identifier,
                position_id=position.id,
                ticker=_required_text(raw.ticker, "ticker", max_length=32).upper(),
                side=side,
                type=leg_type,
                qty=raw.qty,
                entry_price_brl=_number(raw.entry_price_brl, "entry_price_brl", minimum=0.0),
                entry_strike_brl=strike,
                entry_iv_pct=_optional_number(raw.entry_iv_pct, "entry_iv_pct", minimum=0.0),
                entry_delta=_optional_number(raw.entry_delta, "entry_delta"),
            )
        )
    if not prepared:
        raise _bad_request("posição precisa de ao menos uma perna")
    return tuple(prepared)


def _prepare_prediction(
    position: PositionRecord,
    prediction: PredictionRecord | None,
) -> PredictionRecord | None:
    if prediction is None:
        return None
    if not isinstance(prediction, PredictionRecord):
        raise _bad_request("prediction precisa ser PredictionRecord")
    _timestamp(prediction.created_at, "created_at")
    _date_value(prediction.evaluate_on, "evaluate_on")
    operator = _required_text(prediction.operator, "operator", max_length=8)
    status = _required_text(prediction.status, "status", max_length=16).lower()
    if operator not in _OPERATORS:
        raise _bad_request(f"operator inválido: {prediction.operator!r}")
    if status not in _PREDICTION_STATUSES:
        raise _bad_request(f"status de previsão inválido: {prediction.status!r}")
    if (
        isinstance(prediction.horizon_sessions, bool)
        or not isinstance(prediction.horizon_sessions, int)
        or not 0 < prediction.horizon_sessions <= _MAX_INT64
    ):
        raise _bad_request("horizon_sessions precisa ser inteiro positivo dentro do int64")
    if (
        prediction.position_id
        and _required_text(prediction.position_id, "position_id") != position.id
    ):
        raise _bad_request("position_id da previsão diverge da posição")
    return replace(
        prediction,
        id=_uuid4(prediction.id),
        position_id=position.id,
        metric=_required_text(prediction.metric, "metric", max_length=64),
        operator=operator,
        value=_number(prediction.value, "value"),
        status=status,
        observed=_optional_number(prediction.observed, "observed"),
    )


def _parse_date(value: object, name: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{name} inválido no journal: {value!r}")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{name} inválido no journal: {value!r}") from exc


def _parse_timestamp(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f"{name} inválido no journal: {value!r}") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError(f"{name} sem offset no journal: {value!r}")
    return stamp


def _iso(value: datetime) -> str:
    return value.isoformat()


def _uuid4(value: object) -> str:
    try:
        identifier = uuid.UUID(str(value))
    except (ValueError, AttributeError) as exc:
        raise _bad_request(f"id precisa ser uuid4: {value!r}") from exc
    if identifier.version != 4:
        raise _bad_request(f"id precisa ser uuid4: {value!r}")
    return str(identifier)


def _required_text(value: object, name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise _bad_request(f"{name} precisa ser texto não vazio, sem NUL")
    text = value.strip()
    if max_length is not None and len(text) > max_length:
        raise _bad_request(f"{name} excede {max_length} caracteres")
    return text


def _text(value: object, name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise _bad_request(f"{name} precisa ser texto sem NUL")
    if max_length is not None and len(value) > max_length:
        raise _bad_request(f"{name} excede {max_length} caracteres")
    return value


def _date_value(value: object, name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise _bad_request(f"{name} precisa ser date (AAAA-MM-DD)")
    return value


def _date_or_none(value: object, name: str) -> date | None:
    return None if value is None else _date_value(value, name)


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _bad_request(f"{name} precisa ser datetime com offset")
    return value


def _number(value: object, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _bad_request(f"{name} precisa ser número")
    try:
        number = float(value)
    except OverflowError as exc:
        raise _bad_request(f"{name} inválido: {value!r}") from exc
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise _bad_request(f"{name} inválido: {value!r}")
    return number


def _optional_number(
    value: object,
    name: str,
    *,
    minimum: float | None = None,
) -> float | None:
    return None if value is None else _number(value, name, minimum=minimum)


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _breakeven_json(value: object) -> str:
    if not isinstance(value, str):
        raise _bad_request("breakeven_json precisa ser uma string JSON")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise _bad_request("breakeven_json não é JSON válido") from exc
    if not isinstance(parsed, list):
        raise _bad_request("breakeven_json precisa conter uma lista de valores")
    for item in parsed:
        _number(item, "breakeven_json", minimum=0.0)
    return json.dumps(parsed, separators=(",", ":"), allow_nan=False)


def _json_object(value: object, name: str) -> str:
    if not isinstance(value, Mapping):
        raise _bad_request(f"{name} precisa ser um objeto JSON")
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise _bad_request(f"{name} contém valor não serializável: {exc}") from exc


def _json_default(value: object) -> str:
    if isinstance(value, date | datetime):
        return value.isoformat()
    raise TypeError(f"tipo não serializável: {type(value).__name__}")


def _bad_request(message: str) -> SourceFailure:
    return SourceFailure(SourceError(code="bad_request", message=message, hint=_HINT_PAYLOAD))


def _position_closed(position_id: str) -> SourceFailure:
    return SourceFailure(
        SourceError(
            code="position_closed",
            message=f"posição {position_id} já está fechada",
            hint="não há segunda saída: o journal é append-only",
        )
    )


def _position_id_conflict(position_id: str) -> SourceFailure:
    return SourceFailure(
        SourceError(
            code="position_id_conflict",
            message=f"client_position_id {position_id} já foi usado com outro conteúdo",
            hint=(
                "mantenha o mesmo UUID apenas para repetir a mesma abertura; "
                "use outro para outra posição"
            ),
        )
    )


def _storage_error(action: str, exc: BaseException) -> SourceFailure:
    return SourceFailure(
        SourceError(
            code="storage_error",
            message=f"não foi possível {action}: {exc}",
            hint=_HINT_STORAGE,
        )
    )


def _bad_journal(exc: BaseException) -> SourceFailure:
    return SourceFailure(
        SourceError(
            code="storage_error",
            message=f"journal PostgreSQL com dados inválidos: {exc}",
            hint="não edite o diário à mão; restaure um backup íntegro do PostgreSQL",
        )
    )
