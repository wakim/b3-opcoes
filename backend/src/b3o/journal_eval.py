"""Lote EOD de avaliação das previsões do Diário (F7).

A avaliação acontece no **primeiro pregão real posterior a `evaluate_on`**, mesmo
que o lote rode vários dias depois. Métrica ausente vira evento `void`; fechar a
posição não cancela a previsão. Toda leitura do lago passa pela `QueryService`.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from b3o.ingest.errors import SourceError, SourceFailure
from b3o.journal import PredictionRecord
from b3o.prediction import METRIC_SPECS, compare
from b3o.query.service import QueryService
from b3o.store.state import MODEL_VERSION


@dataclass(frozen=True, slots=True)
class EvaluationBatchResult:
    session: date
    candidates: int
    evaluated: int
    hit: int
    miss: int
    void: int
    pending: int

    def to_dict(self) -> dict[str, object]:
        return {
            "session": self.session.isoformat(),
            "candidates": self.candidates,
            "evaluated": self.evaluated,
            "hit": self.hit,
            "miss": self.miss,
            "void": self.void,
            "pending": self.pending,
        }


def evaluate_predictions(
    service: QueryService,
    *,
    as_of: date | None = None,
) -> EvaluationBatchResult:
    """Avalia previsões pendentes; repetição/concorrência não duplica evento."""
    known = service.lake_calendar()
    if not known:
        raise SourceFailure(
            SourceError(
                code="lake_missing",
                message="não há sessões no lake para avaliar previsões",
                hint="rode `b3o prepare` antes de `b3o evaluate`",
            )
        )
    session = known[-1] if as_of is None else as_of
    if session not in known:
        raise SourceFailure(
            SourceError(
                code="no_session",
                message=f"sessão {session.isoformat()} não existe no lake",
                hint="informe uma sessão presente em `b3o health`",
            )
        )
    pending = service.journal.pending_predictions(session)
    due: list[tuple[PredictionRecord, date]] = []
    for prediction in pending:
        target = _next_session(known, prediction.evaluate_on)
        if target is not None and target <= session:
            due.append((prediction, target))
    by_session: dict[date, list[PredictionRecord]] = defaultdict(list)
    for prediction, target in due:
        if prediction.underlying is not None:
            by_session[target].append(prediction)
    observations = {
        target: service.prediction_observations(
            target,
            [prediction.underlying for prediction in predictions if prediction.underlying],
        )
        for target, predictions in sorted(by_session.items())
    }
    model_version = str(service.derived_model_version() or MODEL_VERSION)
    hit = miss = void = evaluated = 0
    for prediction, target in due:
        spec = METRIC_SPECS.get(prediction.metric)
        observation = (
            observations[target].get((prediction.underlying, prediction.metric))
            if prediction.underlying is not None
            else None
        )
        if prediction.underlying is None:
            status, observed, reason = "void", None, "prediction_parent_missing"
            source, event_model = "journal", model_version
        elif spec is None:
            status, observed, reason = "void", None, "unsupported_metric"
            source = "journal"
            event_model = model_version
        elif observation is None:
            status, observed, reason = "void", None, "metric_unavailable"
            source = spec.source
            event_model = model_version
        else:
            try:
                status = compare(observation.value, prediction.value, prediction.operator)
            except ValueError:
                status, observed, reason = "void", None, "invalid_observation"
            else:
                observed, reason = observation.value, None
            source = observation.source
            event_model = str(observation.model_version)
        _, created = service.journal.record_evaluation(
            prediction.id,
            session=target,
            observed=observed,
            status=status,
            void_reason=reason,
            source=source,
            model_version=event_model,
        )
        if not created:
            continue
        evaluated += 1
        if status == "hit":
            hit += 1
        elif status == "miss":
            miss += 1
        else:
            void += 1
    return EvaluationBatchResult(
        session=session,
        candidates=len(pending),
        evaluated=evaluated,
        hit=hit,
        miss=miss,
        void=void,
        pending=len(pending) - len(due),
    )


def _next_session(known: Sequence[date], after: date) -> date | None:
    index = bisect_right(known, after)
    return known[index] if index < len(known) else None
