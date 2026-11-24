"""Contrato puro das previsões estruturadas do Diário (spec §6.12).

Aqui não há I/O nem número novo: apenas a unidade pública de cada métrica, a
escala aplicada ao valor gravado e a comparação determinística. O lote EOD lê o
lago pela ``QueryService``; esta tabela só diz como interpretar o observado.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class MetricSpec:
    field: str
    unit: str
    scale: float
    source: str = "metrics_underlying"


METRIC_SPECS = MappingProxyType(
    {
        "iv_atm_30d": MetricSpec("iv_atm_30d", "pct_annual", 1.0),
        "vrp": MetricSpec("vrp", "vol_points", 1.0),
        "rr25": MetricSpec("rr25", "vol_points", 1.0),
        "spot": MetricSpec("spot", "brl", 0.01),
    }
)

_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def supported_metrics() -> tuple[str, ...]:
    return tuple(METRIC_SPECS)


def compare(observed: float, threshold: float, comparison: str) -> str:
    """Devolve ``hit``/``miss``; entrada não finita é erro explícito."""
    if comparison not in _OPERATORS:
        raise ValueError(f"operador não suportado: {comparison!r}")
    if not math.isfinite(observed) or not math.isfinite(threshold):
        raise ValueError("observado e limite precisam ser finitos")
    return "hit" if _OPERATORS[comparison](observed, threshold) else "miss"
