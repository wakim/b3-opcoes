"""Métricas por papel (`metrics_underlying`, contrato §4.3/§4.5) — PURO.

O pipeline entrega o que já foi calculado — `ExpiryAtm` por vencimento, a série
de RV, o forward do vencimento da frente, OI, calendário de resultados — e este
módulo faz a aritmética do papel:

- **ATM 30d** vem de `derive/atm.py` (`atm_interpolate_30d`); aqui só se
  interpola o mesmo `w` para **90/252**, para o `ts_slope_30_90` (ADR-013:
  `iv_atm(90d) − iv_atm(30d)`, contango positivo, `None` sem bracket);
- **VRP/z/rank/percentil** vêm de `derive/vrp.py`; a janela é a de 252 sessões
  **incluindo hoje** e o histórico chega pronto do pipeline (nunca do futuro);
- **skew** vem de `derive/skew.py`, com a mediana/desvio do próprio `rr25`;
- **expected move** e **dividendo implícito** vêm de `derive/dist.py`;
- **dias para liquidar** é `Σ OI ÷ quantidade média negociada 20d` (§6.9), com a
  mesma unidade dos dois lados (ações do ativo-objeto).

Regra 5 do AGENTS: toda indisponibilidade é `None` + flag — nunca zero, nunca
`DF = 1`, nunca `ln` de preço não positivo. Este módulo não levanta por dado
faltando: devolve `None` e quem chama grava a flag.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from b3o.derive.atm import ExpiryAtm
from b3o.derive.dist import expected_move_1s, implied_div_yield
from b3o.derive.signal import ClassInput, signal_class

TARGET_T_30D = 30 / 252
"""Tenor de 30 dias úteis — o mesmo `TARGET_T_30D` do `atm.py`."""

TARGET_T_90D = 90 / 252
"""Tenor de 90 dias úteis do `ts_slope_30_90` (ADR-013)."""

FLAG_NO_90D = "no_90d"
"""Sem bracket de vencimento em 90/252: `ts_slope_30_90` é `None` (não se extrapola)."""

FLAG_NO_30D_BRACKET = "no_30d_bracket"
"""Sem vencimentos em volta de 30/252 para `forward_30d`/`exp_move_1s_30d`/
`implied_div_yield_30d`: o campo é `None` (não se extrapola — ADR-053)."""

FLAG_NO_ATM_IV = "no_atm_iv"
"""Sem ATM do vencimento de referência (o `bfly25` depende dele)."""

FLAG_OI_UNAVAILABLE = "oi_unavailable"
"""Sem posições em aberto para o papel na sessão (P-N)."""

FLAG_VOLUME_UNAVAILABLE = "volume_unavailable"
"""Sem quantidade negociada 20d exposta pela leitura: dias para liquidar é `None`."""

FLAG_NO_FORWARD = "no_forward"
"""Nenhum vencimento do papel tem forward por paridade."""

FLAG_T_ESTIMATED = "t_estimated"
"""Algum vencimento além da última sessão do lake usa o calendário estendido."""


@dataclass(frozen=True, slots=True)
class UnderlyingMetrics:
    """Uma linha de `data/metrics_underlying` (schema do contrato §4.3)."""

    refdate: date
    underlying: str
    spot: float
    underlying_market: str
    iv_atm_30d: float | None
    iv_rank_252: float | None
    iv_pct_252: float | None
    rv20: float | None
    rv30: float | None
    rv60: float | None
    vrp: float | None
    vrp_z_252: float | None
    rr25: float | None
    bfly25: float | None
    ts_slope_30_90: float | None
    exp_move_1s_30d: float | None
    implied_div_yield_30d: float | None
    days_to_liquidate: float | None
    forward_30d: float | None
    rate_source: str
    signal_class: str
    flags: list[str] = field(default_factory=list)
    model_version: int = 1

    def to_row(self) -> dict[str, object]:
        """`dict` na ordem/valores que o escritor de `store/metrics.py` valida."""
        return {
            "refdate": self.refdate,
            "underlying": self.underlying,
            "spot": self.spot,
            "underlying_market": self.underlying_market,
            "iv_atm_30d": self.iv_atm_30d,
            "iv_rank_252": self.iv_rank_252,
            "iv_pct_252": self.iv_pct_252,
            "rv20": self.rv20,
            "rv30": self.rv30,
            "rv60": self.rv60,
            "vrp": self.vrp,
            "vrp_z_252": self.vrp_z_252,
            "rr25": self.rr25,
            "bfly25": self.bfly25,
            "ts_slope_30_90": self.ts_slope_30_90,
            "exp_move_1s_30d": self.exp_move_1s_30d,
            "implied_div_yield_30d": self.implied_div_yield_30d,
            "days_to_liquidate": self.days_to_liquidate,
            "forward_30d": self.forward_30d,
            "rate_source": self.rate_source,
            "signal_class": self.signal_class,
            "flags": sorted(set(self.flags)),
            "model_version": self.model_version,
        }


def interpolate_variance_iv(
    expiries: Sequence[ExpiryAtm],
    target_t: float,
) -> float | None:
    """IV em `target_t` por `w = σ²·t` linear em `t` entre vencimentos válidos.

    Mesma aritmética de `atm.atm_interpolate_30d`, para o tenor de 90 dias
    (ADR-013). `None` quando o alvo cai fora da faixa coberta — nunca extrapola.
    """
    valid = sorted(
        (expiry.t, expiry.atm_iv)
        for expiry in expiries
        if expiry.atm_iv is not None and expiry.t > 0.0
    )
    if not valid or target_t <= 0.0:
        return None
    if target_t < valid[0][0] or target_t > valid[-1][0]:
        return None
    front = max((item for item in valid if item[0] <= target_t), default=None)
    back = min((item for item in valid if item[0] >= target_t), default=None)
    if front is None or back is None:  # pragma: no cover - coberto pela faixa acima
        return None
    if back[0] == front[0]:
        return front[1]
    w_front = (front[1] / 100.0) ** 2 * front[0]
    w_back = (back[1] / 100.0) ** 2 * back[0]
    w = w_front + (w_back - w_front) * ((target_t - front[0]) / (back[0] - front[0]))
    if not math.isfinite(w) or w < 0.0:
        return None
    return 100.0 * math.sqrt(w / target_t)


def nearest_expiry(
    expiries: Sequence[ExpiryAtm],
    target_t: float,
) -> ExpiryAtm | None:
    """Vencimento de `|t − target|` mínimo (desempate pelo `t` menor)."""
    candidates = [expiry for expiry in expiries if math.isfinite(expiry.t)]
    if not candidates:
        return None
    return min(candidates, key=lambda expiry: (abs(expiry.t - target_t), expiry.t))


def window_with_today(
    history: Sequence[float],
    today: float | None,
    window: int = 252,
) -> list[float] | None:
    """Janela de `window` observações **terminando em hoje** (hoje incluso).

    `history` são as observações anteriores em ordem cronológica. Sem histórico
    suficiente (ou `today` ausente/não finito) devolve `None`: a métrica de 252
    fica `null` até aquecer — nunca um número com nome mentiroso.
    """
    if today is None or not math.isfinite(today):
        return None
    values = [value for value in list(history)[-(window - 1):]]
    values.append(float(today))
    if len(values) < window:
        return None
    return values


def median_and_std(values: Sequence[float] | None) -> tuple[float | None, float | None]:
    """Mediana e desvio amostral (n−1); `(None, None)` sem janela ou com valor não finito."""
    if values is None or len(values) < 2:
        return None, None
    ordered = sorted(values)
    if not all(math.isfinite(value) for value in ordered):
        return None, None
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        median = ordered[middle]
    else:
        median = 0.5 * (ordered[middle - 1] + ordered[middle])
    mean = math.fsum(ordered) / len(ordered)
    variance = math.fsum((value - mean) ** 2 for value in ordered) / (len(ordered) - 1)
    if variance < 0.0:  # pragma: no cover - só com NaN, já barrado acima
        return median, None
    return median, math.sqrt(variance)


def days_to_liquidate(
    total_open_interest: int | None,
    average_daily_quantity: float | None,
) -> float | None:
    """`Σ OI ÷ quantidade média negociada 20d` (spec §6.9) — ambos na mesma unidade.

    `OI` é `Total de posições` na unidade do ativo-objeto (ações), e o
    denominador é a **quantidade** média negociada 20d (ações/dia): dividir ações
    por R$ não tem unidade (liquidity.py já fixou essa convenção no F0.11).
    `None` quando falta OI ou denominador positivo — nunca zero (P-N).
    """
    if total_open_interest is None or total_open_interest < 0:
        return None
    if average_daily_quantity is None or average_daily_quantity <= 0.0:
        return None
    return total_open_interest / average_daily_quantity


def earnings_before_expiry(
    rows: Iterable[tuple[date, str]],
    refdate: date,
    maturity: date | None,
) -> bool:
    """Há resultado (`actual`/`estimated`) entre a sessão e o vencimento?

    ADR-010: `DT_RECEB` vale como evento do pregão **seguinte** (o resultado sai
    depois do fechamento). Por isso a data igual a `refdate` **conta** (o pregão
    afetado é o próximo) e data anterior não conta. `maturity` `None` (sem
    frente) devolve `False`.
    """
    if maturity is None:
        return False
    return any(refdate <= day <= maturity for day, _kind in rows)


def expected_move_cents(straddle_price: float | None, spot: float) -> float | None:
    """`exp_move_1σ` em fração do spot, a partir do straddle ATM por ação."""
    if straddle_price is None:
        return None
    return expected_move_1s(straddle_price, spot)


def bracket_30d(
    points: Iterable[tuple[float, float]],
    target_t: float = TARGET_T_30D,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """`(front, back)` em volta de `target_t` entre pontos `(t, valor)` válidos.

    `front` = maior `t <= alvo`, `back` = menor `t >= alvo`; um ponto exatamente
    no alvo é as duas pontas. `None` quando o alvo cai fora da faixa — nunca
    extrapola (mesma regra do `iv_atm_30d`, spec §6.4). Desempate pelo menor `t`.
    """
    valid = sorted(
        (t, value)
        for t, value in points
        if math.isfinite(t) and t > 0.0 and math.isfinite(value)
    )
    front = None
    for item in valid:
        if item[0] <= target_t:
            front = item
    back = next((item for item in valid if item[0] >= target_t), None)
    if front is None or back is None:
        return None
    return front, back


def _linear_at(
    front: tuple[float, float],
    back: tuple[float, float],
    target_t: float,
) -> float:
    span = back[0] - front[0]
    if span <= 0.0:
        return front[1]
    return front[1] + (back[1] - front[1]) * ((target_t - front[0]) / span)


def forward_at_30d(
    forwards: Iterable[tuple[float, float]],
    spot: float,
    target_t: float = TARGET_T_30D,
) -> float | None:
    """Forward a 30 dias úteis: `ln(F/S)` linear em `t` entre os vencimentos em volta.

    `ln(F/S) = (r − q)·t` com carry constante é linear em `t`; interpolar o log
    (e não `F`) preserva essa forma. Entradas `(t, F)` em anos úteis e na mesma
    unidade do `spot`; saída na unidade do `spot`. `None` sem bracket (ADR-053).
    """
    if not math.isfinite(spot) or spot <= 0.0:
        return None
    logs = [(t, math.log(f / spot)) for t, f in forwards if f is not None and f > 0.0]
    pair = bracket_30d(logs, target_t)
    if pair is None:
        return None
    return spot * math.exp(_linear_at(pair[0], pair[1], target_t))


def expected_move_at_30d(
    moves: Iterable[tuple[float, float]],
    target_t: float = TARGET_T_30D,
) -> float | None:
    """Expected move a 30 dias úteis: `em²` linear em `t` entre os vencimentos em volta.

    `em ≈ σ·√t`, então `em²` é variância total — a mesma interpolação do
    `iv_atm_30d`. Entradas `(t, em)` com `em` em fração do spot; saída em fração.
    `None` sem bracket (ADR-053).
    """
    squares = [(t, em * em) for t, em in moves if em is not None and em >= 0.0]
    pair = bracket_30d(squares, target_t)
    if pair is None:
        return None
    w = _linear_at(pair[0], pair[1], target_t)
    if not math.isfinite(w) or w < 0.0:
        return None
    return math.sqrt(w)


def implied_div_yield_pct(
    log_df: float | None,
    forward: float | None,
    spot: float,
    t: float,
) -> float | None:
    """Dividendo implícito em % a.a. a partir do `ln(DF)` da curva e do forward.

    `r_pct = −ln(DF)·252/du·100` (taxa efetiva anual implícita no DF da curva);
    `implied_div_yield` do `dist.py` recebe `r_pct`, `F`, `S` e `T` já em anos.
    """
    if log_df is None or forward is None or forward <= 0.0 or spot <= 0.0 or t <= 0.0:
        return None
    rate_pct = -log_df * 100.0 / t
    return implied_div_yield(rate_pct, forward, spot, t)


def classify(
    vrp_value: float | None,
    vrp_z_value: float | None,
    rr25_value: float | None,
    rr_history: Sequence[float] | None,
    iv_front: float | None,
    iv_back: float | None,
    n_strikes_band: int,
) -> tuple[str, float | None, float | None]:
    """`signal_class` + `(mediana, desvio)` do `rr25` da janela (252, com hoje)."""
    median, std = median_and_std(rr_history)
    label = signal_class(
        ClassInput(
            vrp=vrp_value,
            vrp_z=vrp_z_value,
            rr25=rr25_value,
            rr25_median_252=median,
            rr25_std_252=std,
            iv_atm_front=iv_front,
            iv_atm_back=iv_back,
            n_strikes_band=n_strikes_band,
        )
    )
    return label, median, std


__all__ = [
    "FLAG_NO_30D_BRACKET",
    "FLAG_NO_90D",
    "FLAG_NO_ATM_IV",
    "FLAG_NO_FORWARD",
    "FLAG_OI_UNAVAILABLE",
    "FLAG_T_ESTIMATED",
    "FLAG_VOLUME_UNAVAILABLE",
    "TARGET_T_30D",
    "TARGET_T_90D",
    "UnderlyingMetrics",
    "bracket_30d",
    "classify",
    "expected_move_at_30d",
    "forward_at_30d",
    "days_to_liquidate",
    "earnings_before_expiry",
    "expected_move_cents",
    "implied_div_yield_pct",
    "interpolate_variance_iv",
    "median_and_std",
    "nearest_expiry",
    "window_with_today",
]
