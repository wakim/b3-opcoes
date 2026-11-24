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
    func,
    or_,
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
_EVALUATION_STATUSES = frozenset({"hit", "miss", "void"})
_OPERATORS = frozenset({"<", "<=", ">", ">="})
_DECISION_TYPES = frozenset({"no_entry", "entry", "exit", "hold", "watch", "other"})

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
    Column("position_id", String(36), ForeignKey("position.id"), nullable=True),
    Column("decision_id", String(36), ForeignKey("decision.id"), nullable=True),
    Column("metric", String(64), nullable=False),
    Column("operator", String(8), nullable=False),
    Column("value", Float, nullable=False),
    Column("horizon_sessions", BigInteger, nullable=False),
    Column("evaluate_on", Date, nullable=False),
    Column("status", String(16), nullable=False),
    Column("observed", Float, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
decision_table = Table(
    "decision",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("underlying", String(32), nullable=False),
    Column("session", Date, nullable=False),
    Column("decision_type", String(32), nullable=False),
    Column("context", Text, nullable=False),
    Column("notes", Text, nullable=False),
    Column("position_id", String(36), ForeignKey("position.id"), nullable=True),
    Column("client_decision_id", String(36), nullable=True),
    Column("request_fingerprint", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
prediction_evaluation_table = Table(
    "prediction_evaluation",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("prediction_id", String(36), ForeignKey("prediction.id"), nullable=False),
    Column("session", Date, nullable=False),
    Column("observed", Float, nullable=True),
    Column("status", String(16), nullable=False),
    Column("void_reason", String(64), nullable=True),
    Column("source", String(32), nullable=False),
    Column("model_version", String(16), nullable=False),
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
Index("decision_session_idx", decision_table.c.session, decision_table.c.id)
Index(
    "decision_underlying_idx",
    decision_table.c.underlying,
    decision_table.c.session,
    decision_table.c.id,
)
Index(
    "prediction_evaluation_parent_idx",
    prediction_evaluation_table.c.prediction_id,
    prediction_evaluation_table.c.session,
    prediction_evaluation_table.c.id,
)


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
class PredictionEvaluationRecord:
    """Evento imutável que fixa observado/status no primeiro pregão posterior."""

    prediction_id: str
    session: date
    status: str
    source: str
    model_version: str
    observed: float | None = None
    void_reason: str | None = None
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class PredictionRecord:
    """Previsão estruturada da spec §6.12; estado corrente vem do evento.

    `status`/`observed` continuam na linha por compatibilidade com a migration
    F6, mas ficam sempre `pending`/`null`. A leitura os substitui pelo último
    evento de avaliação, sem UPDATE.
    """

    position_id: str | None
    metric: str
    operator: str
    value: float
    horizon_sessions: int
    evaluate_on: date
    status: str
    observed: float | None
    decision_id: str | None = None
    underlying: str | None = None
    evaluation: PredictionEvaluationRecord | None = None
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Evento append-only de decisão; pode existir sem posição."""

    underlying: str
    session: date
    decision_type: str
    context: str
    notes: str
    position_id: str | None = None
    client_decision_id: str | None = None
    request_fingerprint: str | None = None
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class DecisionOpen(DecisionRecord):
    """Decisão com previsão opcional e desfecho derivado."""

    prediction: PredictionRecord | None = None


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
        fingerprint = (
            None
            if request_fingerprint is None
            else _required_text(request_fingerprint, "request_fingerprint", max_length=128)
        )
        prepared_prediction = _prepare_prediction(prepared, prediction)
        if prepared_prediction is not None and prepared_prediction.status != "pending":
            raise _bad_request("a previsão na abertura precisa ter status pending")
        decision = _prepare_decision(
            DecisionRecord(
                underlying=prepared.underlying,
                session=prepared.opened_session,
                decision_type="entry",
                context="position_open",
                notes=prepared.notes,
                position_id=prepared.id,
                request_fingerprint=fingerprint,
            )
        )
        if prepared_prediction is not None:
            prepared_prediction = replace(
                prepared_prediction,
                position_id=prepared.id,
                decision_id=None,
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
            connection.execute(decision_table.insert(), _decision_values(decision))
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

    def record_decision(
        self,
        decision: DecisionRecord,
        prediction: PredictionRecord | None = None,
        *,
        request_fingerprint: str | None = None,
    ) -> DecisionOpen:
        """Grava decisão sem posição e previsão opcional na mesma transação."""
        if not isinstance(decision, DecisionRecord):
            raise _bad_request("decision precisa ser DecisionRecord")
        prepared = _prepare_decision(decision)
        if prepared.position_id is not None:
            raise _bad_request("POST /api/journal não cria posição")
        fingerprint = _optional_fingerprint(request_fingerprint)
        if prepared.client_decision_id is not None and fingerprint is None:
            raise _bad_request("client_decision_id exige request_fingerprint")
        prepared = replace(prepared, request_fingerprint=fingerprint)
        prepared_prediction = _prepare_prediction_for_decision(prepared, prediction)

        with self._write_connection() as connection:
            inserted_id = connection.execute(
                pg_insert(decision_table)
                .values(_decision_values(prepared))
                .on_conflict_do_nothing()
                .returning(decision_table.c.id)
            ).scalar_one_or_none()
            if inserted_id is None:
                existing = _existing_decision(connection, prepared)
                if existing is None:
                    raise _storage_error("ler decisão idempotente", RuntimeError("linha ausente"))
                if (
                    prepared.client_decision_id is not None
                    and existing.request_fingerprint != fingerprint
                ):
                    raise _decision_id_conflict(prepared.client_decision_id)
                loaded = _load_decisions(connection, [existing])[0]
                return loaded

            if prepared_prediction is not None:
                connection.execute(
                    prediction_table.insert(),
                    _prediction_values(prepared_prediction),
                )
        return DecisionOpen(
            underlying=prepared.underlying,
            session=prepared.session,
            decision_type=prepared.decision_type,
            context=prepared.context,
            notes=prepared.notes,
            position_id=prepared.position_id,
            client_decision_id=prepared.client_decision_id,
            request_fingerprint=prepared.request_fingerprint,
            id=prepared.id,
            created_at=prepared.created_at,
            prediction=prepared_prediction,
        )

    def list_decisions(
        self,
        *,
        session: date | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[DecisionOpen], int]:
        """Lista paginada de decisões com desfecho derivado do evento."""
        _date_or_none(session, "session")
        page_offset = _nonnegative_int(offset, "offset")
        page_limit = _positive_int(limit, "limit", maximum=60)
        with self._read_connection() as connection:
            try:
                query = select(decision_table)
                count_query = select(func.count()).select_from(decision_table)
                if session is not None:
                    query = query.where(decision_table.c.session == session)
                    count_query = count_query.where(decision_table.c.session == session)
                total = int(connection.execute(count_query).scalar_one())
                rows = connection.execute(
                    query.order_by(
                        decision_table.c.session.desc(),
                        decision_table.c.created_at.desc(),
                        decision_table.c.id,
                    )
                    .offset(page_offset)
                    .limit(page_limit)
                )
                records = [_decision_from_row(row) for row in rows]
                return _load_decisions(connection, records), total
            except (TypeError, ValueError) as exc:
                raise _bad_journal(exc) from exc

    def get_decision(self, decision_id: str) -> DecisionOpen | None:
        """Lê uma decisão; id ausente devolve `None`."""
        identifier = _uuid4(decision_id)
        with self._read_connection() as connection:
            try:
                row = connection.execute(
                    select(decision_table).where(decision_table.c.id == identifier)
                ).first()
                if row is None:
                    return None
                return _load_decisions(connection, [_decision_from_row(row)])[0]
            except (TypeError, ValueError) as exc:
                raise _bad_journal(exc) from exc

    def list_predictions(self) -> list[PredictionRecord]:
        """Todas as previsões, de posições ou decisões, com estado derivado."""
        with self._read_connection() as connection:
            try:
                records = [
                    _prediction_from_row(row)
                    for row in connection.execute(
                        select(prediction_table).order_by(
                            prediction_table.c.created_at, prediction_table.c.id
                        )
                    )
                ]
                return _attach_evaluations(connection, records)
            except (TypeError, ValueError) as exc:
                raise _bad_journal(exc) from exc

    def pending_predictions(self, as_of: date) -> list[PredictionRecord]:
        """Previsões pendentes cujo `evaluate_on` já passou na sessão informada."""
        cutoff = _date_value(as_of, "as_of")
        with self._read_connection() as connection:
            try:
                records = [
                    _prediction_from_row(row)
                    for row in connection.execute(
                        select(prediction_table)
                        .where(prediction_table.c.evaluate_on < cutoff)
                        .order_by(prediction_table.c.evaluate_on, prediction_table.c.id)
                    )
                ]
                return [
                    prediction
                    for prediction in _attach_evaluations(connection, records)
                    if prediction.evaluation is None
                ]
            except (TypeError, ValueError) as exc:
                raise _bad_journal(exc) from exc

    def record_evaluation(
        self,
        prediction_id: str,
        *,
        session: date,
        observed: float | None,
        status: str,
        void_reason: str | None,
        source: str,
        model_version: str,
    ) -> tuple[PredictionEvaluationRecord, bool]:
        """Insere uma avaliação; lock e índice único serializam concorrência."""
        identifier = _uuid4(prediction_id)
        evaluation_session = _date_value(session, "session")
        evaluation_status = _required_text(status, "status", max_length=16).lower()
        if evaluation_status not in _EVALUATION_STATUSES:
            raise _bad_request(f"status de avaliação inválido: {status!r}")
        evaluation_observed = _optional_number(observed, "observed")
        reason = None
        if void_reason is not None:
            reason = _required_text(void_reason, "void_reason", max_length=64)
        if evaluation_status == "void":
            if evaluation_observed is not None or reason is None:
                raise _bad_request("avaliação void exige observed null e void_reason")
        elif evaluation_observed is None or reason is not None:
            raise _bad_request("avaliação hit/miss exige observed e sem void_reason")
        event = PredictionEvaluationRecord(
            prediction_id=identifier,
            session=evaluation_session,
            observed=evaluation_observed,
            status=evaluation_status,
            void_reason=reason,
            source=_required_text(source, "source", max_length=32),
            model_version=_required_text(model_version, "model_version", max_length=16),
        )
        with self._write_connection() as connection:
            row = connection.execute(
                select(prediction_table.c.id)
                .where(prediction_table.c.id == identifier)
                .with_for_update()
            ).first()
            if row is None:
                raise _bad_request(f"previsão não encontrada: {identifier}")
            latest = _latest_evaluation(connection, identifier)
            if latest is not None:
                return latest, False
            inserted = connection.execute(
                pg_insert(prediction_evaluation_table)
                .values(_evaluation_values(event))
                .on_conflict_do_nothing(
                    index_elements=[
                        prediction_evaluation_table.c.prediction_id,
                        prediction_evaluation_table.c.session,
                    ]
                )
                .returning(prediction_evaluation_table.c.id)
            ).scalar_one_or_none()
            if inserted is None:
                existing = connection.execute(
                    select(prediction_evaluation_table)
                    .where(
                        prediction_evaluation_table.c.prediction_id == identifier,
                        prediction_evaluation_table.c.session == evaluation_session,
                    )
                ).first()
                if existing is None:
                    raise _storage_error("ler avaliação idempotente", RuntimeError("linha ausente"))
                return _evaluation_from_row(existing), False
        return event, True

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
    prediction_records = [
        _prediction_from_row(row)
        for row in connection.execute(
            select(prediction_table)
            .where(prediction_table.c.position_id.in_(identifiers))
            .order_by(prediction_table.c.created_at, prediction_table.c.id)
        )
    ]
    for prediction in _attach_evaluations(connection, prediction_records):
        if prediction.position_id is not None:
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


def _attach_underlyings(
    connection: Connection,
    records: Sequence[PredictionRecord],
) -> list[PredictionRecord]:
    position_ids = tuple(
        record.position_id for record in records if record.position_id is not None
    )
    decision_ids = tuple(
        record.decision_id for record in records if record.decision_id is not None
    )
    underlyings: dict[tuple[str, str], str] = {}
    if position_ids:
        for row in connection.execute(
            select(position_table.c.id, position_table.c.underlying).where(
                position_table.c.id.in_(position_ids)
            )
        ):
            underlyings[("position", str(row[0]))] = str(row[1])
    if decision_ids:
        for row in connection.execute(
            select(decision_table.c.id, decision_table.c.underlying).where(
                decision_table.c.id.in_(decision_ids)
            )
        ):
            underlyings[("decision", str(row[0]))] = str(row[1])
    return [
        replace(
            record,
            underlying=(
                record.underlying
                or (
                    underlyings.get(("position", record.position_id))
                    if record.position_id is not None
                    else underlyings.get(("decision", record.decision_id or ""))
                )
            ),
        )
        for record in records
    ]


def _attach_evaluations(
    connection: Connection,
    records: Sequence[PredictionRecord],
) -> list[PredictionRecord]:
    if not records:
        return []
    records = _attach_underlyings(connection, records)
    identifiers = tuple(record.id for record in records)
    latest: dict[str, PredictionEvaluationRecord] = {}
    for row in connection.execute(
        select(prediction_evaluation_table)
        .where(prediction_evaluation_table.c.prediction_id.in_(identifiers))
        .order_by(
            prediction_evaluation_table.c.prediction_id,
            prediction_evaluation_table.c.session,
            prediction_evaluation_table.c.created_at,
            prediction_evaluation_table.c.id,
        )
    ):
        event = _evaluation_from_row(row)
        latest[event.prediction_id] = event
    return [
        replace(
            record,
            status=latest[record.id].status,
            observed=latest[record.id].observed,
            evaluation=latest[record.id],
        )
        if record.id in latest
        else record
        for record in records
    ]


def _latest_evaluation(
    connection: Connection,
    prediction_id: str,
) -> PredictionEvaluationRecord | None:
    row = connection.execute(
        select(prediction_evaluation_table)
        .where(prediction_evaluation_table.c.prediction_id == prediction_id)
        .order_by(
            prediction_evaluation_table.c.session.desc(),
            prediction_evaluation_table.c.created_at.desc(),
            prediction_evaluation_table.c.id.desc(),
        )
        .limit(1)
    ).first()
    return None if row is None else _evaluation_from_row(row)


def _load_decisions(
    connection: Connection,
    records: Sequence[DecisionRecord],
) -> list[DecisionOpen]:
    if not records:
        return []
    identifiers = tuple(record.id for record in records)
    decision_by_position = {
        record.position_id: record.id
        for record in records
        if record.position_id is not None
    }
    predictions: dict[str, PredictionRecord] = {}
    prediction_filter = prediction_table.c.decision_id.in_(identifiers)
    if decision_by_position:
        prediction_filter = or_(
            prediction_table.c.decision_id.in_(identifiers),
            prediction_table.c.position_id.in_(tuple(decision_by_position)),
        )
    prediction_records = [
        _prediction_from_row(row)
        for row in connection.execute(
            select(prediction_table)
            .where(prediction_filter)
            .order_by(prediction_table.c.created_at, prediction_table.c.id)
        )
    ]
    for prediction in _attach_evaluations(connection, prediction_records):
        decision_id = prediction.decision_id
        if decision_id is None and prediction.position_id is not None:
            decision_id = decision_by_position.get(prediction.position_id)
        if decision_id is not None:
            predictions[decision_id] = prediction
    return [
        DecisionOpen(
            underlying=record.underlying,
            session=record.session,
            decision_type=record.decision_type,
            context=record.context,
            notes=record.notes,
            position_id=record.position_id,
            client_decision_id=record.client_decision_id,
            request_fingerprint=record.request_fingerprint,
            id=record.id,
            created_at=record.created_at,
            prediction=predictions.get(record.id),
        )
        for record in records
    ]


def _existing_decision(
    connection: Connection,
    decision: DecisionRecord,
) -> DecisionRecord | None:
    if decision.client_decision_id is not None:
        query = select(decision_table).where(
            decision_table.c.client_decision_id == decision.client_decision_id
        )
    else:
        query = select(decision_table).where(decision_table.c.id == decision.id)
    row = connection.execute(query).first()
    return None if row is None else _decision_from_row(row)


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
        "decision_id": record.decision_id,
        "metric": record.metric,
        "operator": record.operator,
        "value": record.value,
        "horizon_sessions": record.horizon_sessions,
        "evaluate_on": record.evaluate_on,
        "status": record.status,
        "observed": record.observed,
        "created_at": record.created_at,
    }


def _decision_values(record: DecisionRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "underlying": record.underlying,
        "session": record.session,
        "decision_type": record.decision_type,
        "context": record.context,
        "notes": record.notes,
        "position_id": record.position_id,
        "client_decision_id": record.client_decision_id,
        "request_fingerprint": record.request_fingerprint,
        "created_at": record.created_at,
    }


def _evaluation_values(record: PredictionEvaluationRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "prediction_id": record.prediction_id,
        "session": record.session,
        "observed": record.observed,
        "status": record.status,
        "void_reason": record.void_reason,
        "source": record.source,
        "model_version": record.model_version,
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
        position_id=(
            None
            if _value(row, "position_id") is None
            else str(_value(row, "position_id"))
        ),
        decision_id=(
            None
            if _value(row, "decision_id") is None
            else str(_value(row, "decision_id"))
        ),
        metric=str(_value(row, "metric")),
        operator=str(_value(row, "operator")),
        value=float(_value(row, "value")),
        horizon_sessions=int(_value(row, "horizon_sessions")),
        evaluate_on=_parse_date(_value(row, "evaluate_on"), "evaluate_on"),
        status=str(_value(row, "status")),
        observed=_optional_float(_value(row, "observed")),
        created_at=_parse_timestamp(_value(row, "created_at"), "created_at"),
    )


def _decision_from_row(row: Any) -> DecisionRecord:
    position_id = _value(row, "position_id")
    client_id = _value(row, "client_decision_id")
    fingerprint = _value(row, "request_fingerprint")
    return DecisionRecord(
        id=str(_value(row, "id")),
        underlying=str(_value(row, "underlying")),
        session=_parse_date(_value(row, "session"), "session"),
        decision_type=str(_value(row, "decision_type")),
        context=str(_value(row, "context")),
        notes=str(_value(row, "notes")),
        position_id=None if position_id is None else str(position_id),
        client_decision_id=None if client_id is None else str(client_id),
        request_fingerprint=None if fingerprint is None else str(fingerprint),
        created_at=_parse_timestamp(_value(row, "created_at"), "created_at"),
    )


def _evaluation_from_row(row: Any) -> PredictionEvaluationRecord:
    return PredictionEvaluationRecord(
        id=str(_value(row, "id")),
        prediction_id=str(_value(row, "prediction_id")),
        session=_parse_date(_value(row, "session"), "session"),
        observed=_optional_float(_value(row, "observed")),
        status=str(_value(row, "status")),
        void_reason=(
            None
            if _value(row, "void_reason") is None
            else str(_value(row, "void_reason"))
        ),
        source=str(_value(row, "source")),
        model_version=str(_value(row, "model_version")),
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


def _prepare_decision(decision: DecisionRecord) -> DecisionRecord:
    _date_value(decision.session, "session")
    _timestamp(decision.created_at, "created_at")
    decision_type = _required_text(decision.decision_type, "decision_type", max_length=32).lower()
    if decision_type not in _DECISION_TYPES:
        raise _bad_request(f"decision_type inválido: {decision.decision_type!r}")
    position_id = (
        None if decision.position_id is None else _uuid4(decision.position_id)
    )
    client_id = (
        None
        if decision.client_decision_id is None
        else _uuid4(decision.client_decision_id)
    )
    return replace(
        decision,
        id=client_id or _uuid4(decision.id),
        underlying=_required_text(decision.underlying, "underlying", max_length=32).upper(),
        session=decision.session,
        decision_type=decision_type,
        context=_text(decision.context, "context", max_length=500),
        notes=_text(decision.notes, "notes", max_length=4000),
        position_id=position_id,
        client_decision_id=client_id,
        request_fingerprint=_optional_fingerprint(decision.request_fingerprint),
    )


def _prepare_prediction(
    position: PositionRecord,
    prediction: PredictionRecord | None,
) -> PredictionRecord | None:
    if prediction is None:
        return None
    return _prepare_prediction_record(
        prediction,
        position_id=position.id,
        decision_id=None,
        underlying=position.underlying,
    )


def _prepare_prediction_for_decision(
    decision: DecisionRecord,
    prediction: PredictionRecord | None,
) -> PredictionRecord | None:
    if prediction is None:
        return None
    return _prepare_prediction_record(
        prediction,
        position_id=None,
        decision_id=decision.id,
        underlying=decision.underlying,
    )


def _prepare_prediction_record(
    prediction: PredictionRecord,
    *,
    position_id: str | None,
    decision_id: str | None,
    underlying: str,
) -> PredictionRecord:
    if not isinstance(prediction, PredictionRecord):
        raise _bad_request("prediction precisa ser PredictionRecord")
    _timestamp(prediction.created_at, "created_at")
    _date_value(prediction.evaluate_on, "evaluate_on")
    operator = _required_text(prediction.operator, "operator", max_length=8)
    status = _required_text(prediction.status, "status", max_length=16).lower()
    if operator not in _OPERATORS:
        raise _bad_request(f"operator inválido: {prediction.operator!r}")
    if status != "pending" or prediction.observed is not None:
        raise _bad_request("a previsão nasce pending e sem observed")
    if prediction.evaluation is not None:
        raise _bad_request("a previsão nasce sem evento de avaliação")
    if (
        isinstance(prediction.horizon_sessions, bool)
        or not isinstance(prediction.horizon_sessions, int)
        or not 0 < prediction.horizon_sessions <= _MAX_INT64
    ):
        raise _bad_request("horizon_sessions precisa ser inteiro positivo dentro do int64")
    if position_id is not None:
        if prediction.position_id not in (None, position_id):
            raise _bad_request("position_id da previsão diverge da posição")
        if prediction.decision_id is not None:
            raise _bad_request("previsão de posição não pode trazer decision_id")
    if decision_id is not None:
        if prediction.position_id is not None:
            raise _bad_request("previsão de decisão não pode trazer position_id")
        if prediction.decision_id not in (None, decision_id):
            raise _bad_request("decision_id da previsão diverge da decisão")
    return replace(
        prediction,
        id=_uuid4(prediction.id),
        position_id=position_id,
        decision_id=decision_id,
        underlying=underlying,
        metric=_required_text(prediction.metric, "metric", max_length=64).lower(),
        operator=operator,
        value=_number(prediction.value, "value"),
        status="pending",
        observed=None,
        evaluation=None,
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


def _nonnegative_int(value: object, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_INT64
    ):
        raise _bad_request(f"{name} precisa ser inteiro não negativo dentro do int64")
    return value


def _positive_int(value: object, name: str, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= maximum
    ):
        raise _bad_request(f"{name} precisa ser inteiro entre 1 e {maximum}")
    return value


def _optional_fingerprint(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value, "request_fingerprint", max_length=128)


def _number(value: object, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _bad_request(f"{name} precisa ser número")
    if isinstance(value, int) and not -(_MAX_INT64 + 1) <= value <= _MAX_INT64:
        raise _bad_request(f"{name} inteiro fora do int64")
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


def _decision_id_conflict(decision_id: str) -> SourceFailure:
    return SourceFailure(
        SourceError(
            code="decision_id_conflict",
            message=f"client_decision_id {decision_id} já foi usado com outro conteúdo",
            hint=(
                "mantenha o mesmo UUID apenas para repetir a mesma decisão; "
                "use outro para outra decisão"
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
