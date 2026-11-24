"""Backtest das classes de sinal (F1b, spec §6.13, contrato onda-fg §2) — PURO.

Recebe observações ``(papel, data, classe, iv_atm_30d, rv_fwd20, earnings_na_janela)``
já resolvidas pelo carregador (``metrics/backtest.py``) e devolve o relatório por
classe, por papel e por recorte. Nenhum número nasce aqui de I/O: a RV futura, o
ajuste de escala e a folga de earnings são resolvidos **antes**, no carregador.

Desfecho (pré-registrado, NÃO otimizar — P-L):

    edge = iv_atm_30d(data) − rv_fwd20        # pontos de vol
    acerto: premium_rich → edge > 0; premium_cheap → edge < 0
    base:   classe none nas mesmas datas (taxa de edge > 0 e de edge < 0)

Critérios do portão (contrato §2, ``docs/prompts/f1b-portao.md``):

- ``premium_rich``: ``n ≥ 50``, acerto ≥ 55%, mediana de ``edge ≥ +1,0`` vol
  point, acerto ≥ acerto(``none``) + 5 p.p.;
- ``premium_cheap``: simétrico (``edge < 0``, mediana ≤ −1,0, mesma margem);
- classe com ``n < 50`` **não passa**.

``skew_anomaly`` e ``event_pressure`` não têm veredito: só ``n`` e distribuição.

Convenções de estatística:

- mediana: ``statistics.median`` (média dos dois centrais em n par);
- P10/P90: interpolação linear entre ranks vizinhos (a convenção ``linear`` do
  numpy — determinística, documentada aqui para o relatório não mentir);
- taxas em **%** (0–100); ``edge`` em pontos de vol, sem arredondar (só a
  serialização arredonda — AGENTS).

Este módulo é puro: math/statistics/dataclasses/typing apenas. Zero I/O.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from b3o.derive.signal import CLASS_ORDER, NONE, PREMIUM_CHEAP, PREMIUM_RICH

GATE_N_MIN = 50
"""`n ≥ 50` por classe (contrato onda-fg §2, pré-registrado)."""

GATE_HIT_MIN_PCT = 55.0
"""Taxa de acerto mínima, em % (contrato onda-fg §2)."""

GATE_MEDIAN_MIN = 1.0
"""Mediana de `edge` mínima em módulo, em vol points (+1,0 rich / −1,0 cheap)."""

GATE_MARGIN_PP = 5.0
"""Margem sobre a taxa da classe `none`, em pontos percentuais."""

GATE_CLASSES: tuple[str, ...] = (PREMIUM_RICH, PREMIUM_CHEAP)
"""Classes com veredito no portão; as outras só relatam distribuição."""


@dataclass(frozen=True, slots=True)
class Observation:
    """Uma observação do backtest: o que o sinal disse e o que o futuro entregou.

    ``rv_fwd20`` é a Yang-Zhang das 20 sessões **seguintes** à data (% a.a., base
    252), já ajustada por escala e sem gap de provento — resolvida no carregador.
    ``None`` aqui significa "sem RV futura": a observação não entra em nenhuma
    estatística de ``edge`` (o carregador já a descartou e contou o motivo).
    """

    underlying: str
    refdate: date
    signal_class: str
    iv_atm_30d: float | None
    rv_fwd20: float | None
    earnings_na_janela: bool
    """Resultado (`actual`, folga de 1 sessão do ADR-010) em `data+1 … data+20`."""

    trap_in_rv_window: bool = False
    """Informativo: 2026-10-26 cai na janela de RV passada (rv30) ou futura.

    2026-10-26 é um choque **real** de mercado (ADR-034: IBOV −14,35%, sem evento
    corporativo), não artefato de dado: a RV de 30–60 sessões depois é alta de
    verdade. O recorte com/sem estas observações é **só informativo**: o veredito
    do portão usa o período completo, como está pré-registrado.
    """

    @property
    def edge(self) -> float | None:
        """`iv_atm_30d − rv_fwd20` em pontos de vol; `None` se falta um lado."""
        if self.iv_atm_30d is None or self.rv_fwd20 is None:
            return None
        return self.iv_atm_30d - self.rv_fwd20


@dataclass(frozen=True, slots=True)
class ClassStats:
    """Estatística de `edge` de uma classe (ou de um recorte dela).

    ``hit_rate_pct`` só existe para ``premium_rich``/``premium_cheap`` (a direção
    do acerto é da classe). Para ``none`` o que importa são ``edge_pos_pct`` e
    ``edge_neg_pct`` — a base de comparação do portão. Para ``skew_anomaly`` e
    ``event_pressure`` só a distribuição, sem veredito.
    """

    signal_class: str
    n: int
    hit_rate_pct: float | None
    edge_pos_pct: float | None
    edge_neg_pct: float | None
    median: float | None
    p10: float | None
    p90: float | None
    edge_min: float | None
    edge_max: float | None


@dataclass(frozen=True, slots=True)
class CriterionResult:
    """Um critério pré-registrado do portão, com o valor medido e o veredito."""

    name: str
    measured: float | None
    required: str
    passed: bool


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """Veredito linha a linha de uma classe do portão (todos os critérios)."""

    signal_class: str
    criteria: tuple[CriterionResult, ...]

    @property
    def passed(self) -> bool:
        """A classe passa só se **todos** os critérios passarem."""
        return all(item.passed for item in self.criteria)


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """Relatório completo: por classe, por papel, por recorte de earnings e trap."""

    by_class: dict[str, ClassStats]
    by_ticker: dict[str, dict[str, ClassStats]]
    earnings_split: dict[str, dict[bool, ClassStats]]
    """`{classe: {True: com resultado na janela, False: sem}}`."""
    cheap_trap_split: dict[bool, ClassStats]
    """`premium_cheap` com/sem observações cuja janela de RV contém 2026-10-26."""


def class_stats(observations: Sequence[Observation], signal_class: str) -> ClassStats:
    """Estatística de `edge` das observações (já filtradas por classe/recorte).

    Observação sem `edge` (IV ou RV futura ausente) não entra no `n` — o
    carregador garante que só chegam observações completas, mas a função não
    confia e reconta.
    """
    edges = [obs.edge for obs in observations if obs.edge is not None]
    values = [value for value in edges if value is not None]
    n = len(values)
    if n == 0:
        return ClassStats(signal_class, 0, None, None, None, None, None, None, None, None)
    ordered = sorted(values)
    positive = sum(1 for value in values if value > 0.0)
    negative = sum(1 for value in values if value < 0.0)
    hit: float | None = None
    if signal_class == PREMIUM_RICH:
        hit = 100.0 * positive / n
    elif signal_class == PREMIUM_CHEAP:
        hit = 100.0 * negative / n
    return ClassStats(
        signal_class=signal_class,
        n=n,
        hit_rate_pct=hit,
        edge_pos_pct=100.0 * positive / n,
        edge_neg_pct=100.0 * negative / n,
        median=statistics.median(values),
        p10=_percentile(ordered, 0.10),
        p90=_percentile(ordered, 0.90),
        edge_min=ordered[0],
        edge_max=ordered[-1],
    )


def signal_backtest(
    observations: Sequence[Observation],
    signal_class: str,
    ticker: str | None = None,
) -> ClassStats:
    """Taxa histórica da classe (F2/F3 consome depois) — pura sobre as observações.

    `ticker=None` agrega o universo inteiro; com ticker, só o papel. A classe é
    a gravada em `metrics_underlying` (não se reclassifica aqui).
    """
    selected = [
        obs
        for obs in observations
        if obs.signal_class == signal_class
        and (ticker is None or obs.underlying == ticker)
    ]
    return class_stats(selected, signal_class)


def aggregate(observations: Sequence[Observation]) -> BacktestReport:
    """Relatório por classe, por papel, por earnings e pelo recorte informativo."""
    by_class = {
        label: class_stats(
            [obs for obs in observations if obs.signal_class == label], label
        )
        for label in CLASS_ORDER
    }
    by_ticker: dict[str, dict[str, ClassStats]] = {}
    for ticker in sorted({obs.underlying for obs in observations}):
        paper_obs = [obs for obs in observations if obs.underlying == ticker]
        by_ticker[ticker] = {
            label: class_stats(
                [obs for obs in paper_obs if obs.signal_class == label], label
            )
            for label in CLASS_ORDER
        }
    earnings_split: dict[str, dict[bool, ClassStats]] = {}
    for label in CLASS_ORDER:
        label_obs = [obs for obs in observations if obs.signal_class == label]
        earnings_split[label] = {
            present: class_stats(
                [obs for obs in label_obs if obs.earnings_na_janela == present], label
            )
            for present in (True, False)
        }
    cheap_obs = [obs for obs in observations if obs.signal_class == PREMIUM_CHEAP]
    cheap_trap_split = {
        present: class_stats(
            [obs for obs in cheap_obs if obs.trap_in_rv_window == present],
            PREMIUM_CHEAP,
        )
        for present in (True, False)
    }
    return BacktestReport(by_class, by_ticker, earnings_split, cheap_trap_split)


def gate_verdicts(report: BacktestReport) -> dict[str, GateVerdict]:
    """Aplica os critérios pré-registrados (contrato §2) — sem interpretar.

    O veredito é mecânico: cada critério compara o medido com o limiar fixo.
    A decisão de seguir/parar é do dono, não desta função (P-L).
    """
    none_stats = report.by_class.get(NONE)
    verdicts: dict[str, GateVerdict] = {}
    for label in GATE_CLASSES:
        stats = report.by_class.get(label)
        if stats is None:
            stats = class_stats([], label)
        if label == PREMIUM_RICH:
            base_rate = none_stats.edge_pos_pct if none_stats else None
            median_ok = stats.median is not None and stats.median >= GATE_MEDIAN_MIN
            median_required = f"mediana ≥ +{GATE_MEDIAN_MIN:.1f}"
        else:
            base_rate = none_stats.edge_neg_pct if none_stats else None
            median_ok = stats.median is not None and stats.median <= -GATE_MEDIAN_MIN
            median_required = f"mediana ≤ −{GATE_MEDIAN_MIN:.1f}"
        margin_ok = (
            stats.hit_rate_pct is not None
            and base_rate is not None
            and stats.hit_rate_pct >= base_rate + GATE_MARGIN_PP
        )
        criteria = (
            CriterionResult(
                name="n",
                measured=float(stats.n),
                required=f"≥ {GATE_N_MIN}",
                passed=stats.n >= GATE_N_MIN,
            ),
            CriterionResult(
                name="acerto",
                measured=stats.hit_rate_pct,
                required=f"≥ {GATE_HIT_MIN_PCT:.0f}%",
                passed=stats.hit_rate_pct is not None
                and stats.hit_rate_pct >= GATE_HIT_MIN_PCT,
            ),
            CriterionResult(
                name="mediana",
                measured=stats.median,
                required=median_required,
                passed=median_ok,
            ),
            CriterionResult(
                name="margem vs none",
                measured=(
                    stats.hit_rate_pct - base_rate
                    if stats.hit_rate_pct is not None and base_rate is not None
                    else None
                ),
                required=f"≥ +{GATE_MARGIN_PP:.0f} p.p.",
                passed=margin_ok,
            ),
        )
        verdicts[label] = GateVerdict(signal_class=label, criteria=criteria)
    return verdicts


def _percentile(ordered: Sequence[float], q: float) -> float:
    """Percentil por interpolação linear entre ranks vizinhos (convenção `linear`).

    `ordered` já está ordenado e não vazio; `q` em [0, 1]. Posição `q·(n−1)`:
    a parte inteira escolhe o rank, a fracionária interpola até o próximo.
    """
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


__all__ = [
    "GATE_CLASSES",
    "GATE_HIT_MIN_PCT",
    "GATE_MARGIN_PP",
    "GATE_MEDIAN_MIN",
    "GATE_N_MIN",
    "BacktestReport",
    "ClassStats",
    "CriterionResult",
    "GateVerdict",
    "Observation",
    "aggregate",
    "class_stats",
    "gate_verdicts",
    "signal_backtest",
]
