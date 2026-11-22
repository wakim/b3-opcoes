"""VRP e estatísticas de IV (F1.7; spec §5.3, §6.7, §6.9) — PURO: número entra,
número sai.

Métricas do papel (`core.metrics_underlying`, spec §5.3) e o insumo das classes
de sinal (§6.9): `premium_rich` é `vrp_z >= +1,5` **e** `vrp >= +4` vol points.

- `vrp = iv_atm_30d − rv30` — **tenor casado**: IV de 30 dias úteis menos
  realizada de 30 dias (spec §6.7). Unidade: **pontos de vol** (ponto
  percentual), porque `iv_pct` e `rv30` já estão em %.
- `vrp_z = (vrp − média) / desvio AMOSTRAL (n−1)` da janela de 252 (contrato
  §5.4). Desvio amostral, não populacional: é uma amostra de 252 sessões da
  série do papel, não a população. Menos de 252 observações → `None` — "sem
  aquecimento → null" (contrato §1), nunca zero.
- `iv_rank_252` = posição min–max das últimas 252: `100·(x − min)/(max − min)`;
  `max == min` → `None` (não há posição numa faixa degenerada).
- `iv_percentile_252` = percentil inclusivo `100·#{v <= x}/n` — mede "quantos
  dias foram mais baratos que hoje", que é diferente do rank min–max. As duas
  estão na spec §5.3 e são **duas** métricas, não uma (ADR-013). O campo do lake
  é `iv_pct_252` (spec §5.3); a função leva o nome do contrato §5.4.

**Nada aqui olha o futuro.** As três funções fatiam `series[-window:]` por
dentro, então o caller passa o histórico **até a data** e a janela é contada
para trás; valor antigo demais não entra na conta. O teste de look-ahead do F1b
depende disso.

As três de janela exigem janela **cheia** (252): uma "rank 252" calculada com 30
dias seria um número com nome mentiroso — o produto mostra `null` até aquecer,
como manda a spec §6.7. Valor não finito na janela também derruba a métrica em
`None`, em vez de devolver `NaN` com cara de número.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

VRP_Z_WINDOW = 252
"""Sessões da janela do `vrp_z` (spec §6.7: média/desvio de 252 sessões)."""

IV_WINDOW = 252
"""Sessões das janelas de `iv_rank_252` e `iv_percentile_252` (nome e spec §5.3)."""

MIN_STD_SAMPLES = 2
"""Abaixo disso o desvio amostral não existe (`n−1 <= 0`)."""


def vrp(iv_atm_30d: float, rv30: float) -> float:
    """`iv_atm_30d − rv30`, em **pontos de vol** (spec §6.7, tenor casado)."""
    return iv_atm_30d - rv30


def vrp_z(vrp_series: Sequence[float], window: int = VRP_Z_WINDOW) -> float | None:
    """Z-score do VRP de hoje contra as últimas `window` sessões (hoje incluso).

    `(x − média) / desvio amostral (n−1)`. `None` quando: a série tem menos de
    `window` observações (sem aquecimento), `window < 2`, o desvio é zero (série
    constante — dividir por zero viraria `inf`, que não é sinal) ou há valor não
    finito na janela (`NaN` contaminaria média e desvio).
    """
    if window < MIN_STD_SAMPLES:
        return None
    sample = _window(vrp_series, window)
    if sample is None:
        return None
    mean = math.fsum(sample) / window
    variance = math.fsum((value - mean) ** 2 for value in sample) / (window - 1)
    if variance <= 0.0:
        return None
    return (sample[-1] - mean) / math.sqrt(variance)


def iv_rank_252(iv_series: Sequence[float]) -> float | None:
    """Posição min–max de hoje nas últimas 252 observações, em 0–100.

    `100·(x − min)/(max − min)`; `None` com janela incompleta (aquecimento),
    com valor não finito, ou `max == min` (faixa degenerada, sem posição a
    informar).
    """
    sample = _window(iv_series, IV_WINDOW)
    if sample is None:
        return None
    low = min(sample)
    high = max(sample)
    if high <= low:
        return None
    return 100.0 * (sample[-1] - low) / (high - low)


def iv_percentile_252(iv_series: Sequence[float]) -> float | None:
    """Percentil inclusivo de hoje nas últimas 252 observações, em 0–100.

    `100·#{v <= hoje}/n` — "em quantos dias a IV esteve igual ou abaixo de
    hoje". `None` com janela incompleta (aquecimento) ou valor não finito.
    """
    sample = _window(iv_series, IV_WINDOW)
    if sample is None:
        return None
    today = sample[-1]
    count = sum(1 for value in sample if value <= today)
    return 100.0 * count / len(sample)


def _window(series: Sequence[float], window: int) -> list[float] | None:
    """Últimas `window` observações (hoje incluso) — `None` se faltam ou há `NaN`.

    O corte é **para trás**: o caller passa o histórico até a data e nada além
    dela entra na conta. Valor não finito derruba a janela inteira em vez de
    contaminar a estatística com `NaN` (falha explícita).
    """
    if len(series) < window:
        return None
    sample = list(series[-window:])
    if not all(math.isfinite(value) for value in sample):
        return None
    return sample
