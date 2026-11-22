"""Classes de sinal da spec §6.9 — PURO: número entra, classe sai.

As classes são **raras por construção** (z ≥ 1,5 é cauda; 0 a 3 candidatos por
dia num universo de 10–20 papéis) e os limiares são **pré-registrados**
(`docs/contratos/onda-fg.md` §2, portão G1). Não se otimiza limiar depois de
ver o resultado (P-L): o mesmo arquivo vale para o backtest.

| Classe | Regra |
|---|---|
| `premium_rich` | `vrp_z ≥ +1,5` **e** `vrp ≥ +4` (pontos de vol) |
| `premium_cheap` | `vrp_z ≤ −1,5` **e** `vrp ≤ −4` |
| `skew_anomaly` | `|rr25 − mediana252| > 2·σ252` **e** grade com ≥ 5 strikes na banda |
| `event_pressure` | `iv_atm(front) > iv_atm(back) + 2` (pontos de vol) |
| `none` | nenhuma das acima |

**Precedência: rich > cheap > skew > event.** Ela existe para o resultado ser
determinístico quando duas condições disparam no mesmo dia (raro, mas possível:
um papel com VRP caro e skew anômalo é uma coisa só, e a que carrega mais
informação de prêmio é o VRP). A classe é um rótulo único, não um conjunto.

**Nada é inventado.** Cada regra depende de entradas que podem faltar
(`None` = sem aquecimento de 252 sessões, sem forward, grade rala): se uma
entrada necessária é `None`, aquela classe **não pode** ser atribuída. As
outras ainda são avaliadas, na ordem de precedência. `n_strikes_band` é `int`
(0 = grade sem pontos na banda) e a condição exige ≥ 5.

`vrp`, `rr25` e as IVs chegam em **pontos de vol** (% a.a.), como no lake
(`metrics_underlying`); a mediana e o desvio do `rr25` são da janela de 252
sessões **incluindo a de hoje** (mesma convenção do `vrp_z` da `derive/vrp.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

PREMIUM_RICH = "premium_rich"
PREMIUM_CHEAP = "premium_cheap"
SKEW_ANOMALY = "skew_anomaly"
EVENT_PRESSURE = "event_pressure"
NONE = "none"

RICH_Z_MIN = 1.5
"""`vrp_z ≥ +1,5` — cauda superior da própria série (spec §6.9)."""

RICH_VRP_MIN = 4.0
"""`vrp ≥ +4` pontos de vol (spec §6.9)."""

CHEAP_Z_MAX = -1.5
"""`vrp_z ≤ −1,5` — cauda inferior (spec §6.9)."""

CHEAP_VRP_MAX = -4.0
"""`vrp ≤ −4` pontos de vol (spec §6.9)."""

SKEW_SIGMA_MIN = 2.0
"""`|rr25 − mediana252| > 2·σ252` (spec §6.9)."""

SKEW_MIN_STRIKES = 5
"""Grade com ≥ 5 strikes na banda 20Δ–80Δ (spec §6.8/§6.9)."""

EVENT_SPREAD_MIN = 2.0
"""`iv_atm(front) − iv_atm(back) > 2` pontos de vol (spec §6.9)."""

CLASS_ORDER: tuple[str, ...] = (
    PREMIUM_RICH,
    PREMIUM_CHEAP,
    SKEW_ANOMALY,
    EVENT_PRESSURE,
    NONE,
)
"""Ordem de precedência (também a ordem do resumo da CLI)."""


@dataclass(frozen=True, slots=True)
class ClassInput:
    """Entradas da classificação — todas em vol points / % a.a., como no lake."""

    vrp: float | None
    """`iv_atm_30d − rv30`, em pontos de vol."""

    vrp_z: float | None
    """Z-score do VRP contra 252 sessões (inclui hoje); `None` sem aquecimento."""

    rr25: float | None
    """Risk reversal 25Δ do vencimento de referência, em pontos de vol."""

    rr25_median_252: float | None
    """Mediana do `rr25` nas 252 sessões até hoje (inclusive); `None` sem janela cheia."""

    rr25_std_252: float | None
    """Desvio amostral (n−1) do `rr25` nas 252 sessões; `None` sem janela cheia."""

    iv_atm_front: float | None
    """IV ATM do vencimento da frente (≤ 30d), em % a.a."""

    iv_atm_back: float | None
    """IV ATM do vencimento de trás (≥ 30d), em % a.a."""

    n_strikes_band: int
    """Pontos com |Δ| em [20Δ; 80Δ] no vencimento do skew."""


def signal_class(data: ClassInput) -> str:
    """Classe da §6.9 com precedência `rich > cheap > skew > event` (determinística)."""
    if _is_rich(data):
        return PREMIUM_RICH
    if _is_cheap(data):
        return PREMIUM_CHEAP
    if _is_skew_anomaly(data):
        return SKEW_ANOMALY
    if _is_event_pressure(data):
        return EVENT_PRESSURE
    return NONE


def _is_rich(data: ClassInput) -> bool:
    """`vrp_z ≥ +1,5` **e** `vrp ≥ +4`; ambos obrigatórios."""
    if data.vrp_z is None or data.vrp is None:
        return False
    return data.vrp_z >= RICH_Z_MIN and data.vrp >= RICH_VRP_MIN


def _is_cheap(data: ClassInput) -> bool:
    """`vrp_z ≤ −1,5` **e** `vrp ≤ −4`; ambos obrigatórios."""
    if data.vrp_z is None or data.vrp is None:
        return False
    return data.vrp_z <= CHEAP_Z_MAX and data.vrp <= CHEAP_VRP_MAX


def _is_skew_anomaly(data: ClassInput) -> bool:
    """`|rr25 − mediana| > 2σ` **e** ≥ 5 strikes na banda.

    `σ == 0` não expressa anomalia (série constante: qualquer diferença de
    float dispararia) — é tratado como "sem condição", documentado no relatório.
    """
    if (
        data.rr25 is None
        or data.rr25_median_252 is None
        or data.rr25_std_252 is None
        or data.rr25_std_252 <= 0.0
        or data.n_strikes_band < SKEW_MIN_STRIKES
    ):
        return False
    return abs(data.rr25 - data.rr25_median_252) > SKEW_SIGMA_MIN * data.rr25_std_252


def _is_event_pressure(data: ClassInput) -> bool:
    """`iv_atm(front) > iv_atm(back) + 2`; as duas IVs obrigatórias."""
    if data.iv_atm_front is None or data.iv_atm_back is None:
        return False
    return data.iv_atm_front > data.iv_atm_back + EVENT_SPREAD_MIN


__all__ = [
    "CHEAP_VRP_MAX",
    "CHEAP_Z_MAX",
    "CLASS_ORDER",
    "EVENT_SPREAD_MIN",
    "NONE",
    "PREMIUM_CHEAP",
    "PREMIUM_RICH",
    "RICH_VRP_MIN",
    "RICH_Z_MIN",
    "SKEW_ANOMALY",
    "SKEW_MIN_STRIKES",
    "SKEW_SIGMA_MIN",
    "ClassInput",
    "signal_class",
]
