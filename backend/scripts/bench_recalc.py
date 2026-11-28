"""Recálculo offline das métricas de mercado do benchmark F10 (pacote P7) — PURO.

Reimplementação em **stdlib** das fórmulas de ``backend/src/b3o/derive`` e
``backend/src/b3o/metrics`` para o benchmark conferir números externos **sem
importar o produto**. Python calcula; nenhum número de relatório nasce de um
LLM (AGENTS.md, regra 1).

Regras deste módulo (PLAN.md F10, retomada de 26/11/2026):
- sem ``b3o``, sem rede, sem leitura/escrita de arquivo nas funções de cálculo;
- toda ausência é ``None`` + ``flags`` com o motivo, nunca zero silencioso
  (AGENTS.md, regra 5);
- unidade e verbete de ``docs/glossario.md`` em toda função pública;
- o CLI só documenta o módulo (``--help``): não captura, não compara, não
  escreve relatório.
"""

from __future__ import annotations

import argparse
import math
from bisect import bisect_right
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

SESSIONS_PER_YEAR = 252
"""Base de anualização (spec §6.0: ``T`` em dias úteis ÷ 252)."""

MIN_WINDOW = 3
"""Menor janela da RV: σ² amostral precisa de N ≥ 2 retornos."""

VARIANCE_EPSILON = 1e-12
"""σ² entre ``−``isto e zero é ruído de ponto flutuante, não erro."""

SHOCK_SHARE_MIN = 0.5
"""A partir desta fração da variância o painel avisa (ADR-035)."""

SHOCK_MIN_RETURNS = 10
"""Abaixo disto a fração do maior dia é ruído de amostra."""

BAND_LOW = 0.20
BAND_HIGH = 0.80
MIN_STRIKES_BAND = 5
TARGET_DELTA_25 = 0.25
EXP_MOVE_FACTOR = 1.25
Z_WINDOW = 252
IV_WINDOW = 252

FLAG_INSUFFICIENT_HISTORY = "insufficient_history"
FLAG_INVALID_PRICE = "invalid_price"
FLAG_NEGATIVE_VARIANCE = "negative_variance"
FLAG_EX_DIV_EXCLUDED = "ex_div_excluded"
FLAG_INSUFFICIENT_RETURNS = "insufficient_returns"
FLAG_ZERO_VARIANCE = "zero_variance"
FLAG_NON_FINITE = "non_finite_value"
FLAG_DEGENERATE_RANGE = "degenerate_range"
FLAG_DEGENERATE_STD = "degenerate_std"
FLAG_SPARSE_CHAIN = "sparse_chain"
FLAG_NO_ATM_IV = "no_atm_iv"
FLAG_MISSING_INPUT = "missing_input"
FLAG_INVALID_INPUT = "invalid_input"
FLAG_INVALID_TENOR = "invalid_tenor"
FLAG_CURVE_EXTRAPOLATED = "curve_extrapolated"
FLAG_NO_CURVE = "no_curve"
FLAG_OI_UNAVAILABLE = "oi_unavailable"
FLAG_VOLUME_UNAVAILABLE = "volume_unavailable"
FLAG_MARK_MISSING = "mark_missing"
FLAG_PARTIAL_GREEKS = "gregas_parciais"


@dataclass(frozen=True, slots=True)
class MetricResult[T]:
    """Resultado explícito: ``value`` (ou ``None``) e os motivos em ``flags``."""

    value: T | None
    flags: tuple[str, ...] = ()


def _ok[T](value: T) -> MetricResult[T]:
    """Sucesso limpo, sem ressalvas."""
    return MetricResult(value, ())


def _none[T](*flags: str) -> MetricResult[T]:
    """Falha explícita: valor ausente e os motivos, sem zero silencioso."""
    return MetricResult(None, tuple(flags))


# ---------------------------------------------------------------------------
# 1. Volatilidade realizada — verbete "Volatilidade realizada (RV)"
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OhlcBar:
    """Uma sessão do subjacente; OHLC em centavos, ``adj_factor`` já resolvido.

    O fator de escala (desdobramento/grupamento) vem resolvido pelo chamador:
    multiply os quatro preços por ele antes de qualquer log (spec §6.5).
    """

    refdate: date
    open: int
    high: int
    low: int
    close: int
    adj_factor: float = 1.0


@dataclass(frozen=True, slots=True)
class RvResult:
    """RV anualizada e as ressalvas da janela.

    ``value`` em **% a.a.** (30.0 = 30% ao ano, base 252); ``flags`` explica
    toda ausência; ``n_bars`` é o intervalo de barras atravessado (pode passar
    da janela quando uma data ex de provento obriga a puxar barras antigas).
    """

    value: float | None
    flags: tuple[str, ...]
    n_bars: int


_Logs = tuple[float, float, float, float]
"""``(ln O, ln H, ln L, ln C)`` de uma barra já ajustada."""


@dataclass(frozen=True, slots=True)
class _Observation:
    """Retornos de uma barra contra o fechamento anterior, em log."""

    overnight: float
    intraday: float
    high_log: float
    low_log: float
    close_log: float


def yang_zhang_vol(
    ohlc: Sequence[OhlcBar],
    window: int,
    *,
    ex_div_dates: Collection[date] = frozenset(),
) -> RvResult:
    """Yang-Zhang das últimas ``window`` barras, em **% a.a.**, base 252.

    Verbete: ``docs/glossario.md`` → "Volatilidade realizada (RV)". Entrada:
    OHLC em centavos (ou qualquer unidade consistente) com ``adj_factor`` de
    escala **já aplicado por barra**; ``ex_div_dates`` são datas ex de provento.
    Saída: ``RvResult.value`` em % a.a., ``None`` + flag quando faltam barras
    (``insufficient_history``), há preço ≤ 0 (``invalid_price``) ou a variância
    é inconsistente (``negative_variance``).

    Fórmula (Yang & Zhang, 2000, eq. 1–6): σ² = σ²_o + k·σ²_c + (1−k)·σ²_rs,
    com k = 0,34/(1,34 + (N+1)/(N−1)) e N = ``window − 1`` retornos. O termo de
    Rogers-Satchell usa ``(H−C)(H−O) + (L−C)(L−O)`` em log, dividido por N.
    Uma data ex **não encolhe a janela**: a observação sai e uma barra mais
    antiga entra, com a flag ``ex_div_excluded`` (spec §6.5).
    """
    if window < MIN_WINDOW:
        raise ValueError(
            f"janela mínima é {MIN_WINDOW} barras; recebido window={window}"
        )
    bars = _sorted_bars(ohlc)
    if len(bars) < window:
        return RvResult(None, (FLAG_INSUFFICIENT_HISTORY,), len(bars))

    logs = [_bar_logs(bar) for bar in bars]
    ex_dates = frozenset(ex_div_dates)
    needed = window - 1
    samples: list[tuple[date, _Logs, _Logs]] = []
    index = len(bars) - 1
    span_last = index
    span_first = index
    excluded = False
    while index >= 1 and len(samples) < needed:
        if bars[index].refdate in ex_dates:
            excluded = True
            index -= 1
            continue
        previous, current = logs[index - 1], logs[index]
        if previous is None or current is None:
            return RvResult(None, (FLAG_INVALID_PRICE,), len(bars))
        if not samples:
            span_last = index
        samples.append((bars[index].refdate, previous, current))
        span_first = index
        index -= 1
    if len(samples) < needed:
        return RvResult(None, (FLAG_INSUFFICIENT_HISTORY,), len(bars))

    observations = [
        _observation(previous, current) for _, previous, current in reversed(samples)
    ]
    variance = _yang_zhang_variance(observations)
    if variance < -VARIANCE_EPSILON:
        return RvResult(None, (FLAG_NEGATIVE_VARIANCE,), len(bars))
    variance = max(variance, 0.0)
    value = math.sqrt(variance * SESSIONS_PER_YEAR) * 100.0
    flags = (FLAG_EX_DIV_EXCLUDED,) if excluded else ()
    return RvResult(value, flags, span_last - span_first + 2)


def rv20(
    ohlc: Sequence[OhlcBar], *, ex_div_dates: Collection[date] = frozenset()
) -> RvResult:
    """RV Yang-Zhang de 20 barras, em **% a.a.**, base 252.

    Verbete: ``docs/glossario.md`` → "Volatilidade realizada (RV)". Entrada:
    OHLC em centavos com ``adj_factor`` de escala já resolvido por barra;
    saída: ``RvResult.value`` em % a.a., ``None`` + flag nas ausências.
    """
    return yang_zhang_vol(ohlc, 20, ex_div_dates=ex_div_dates)


def rv30(
    ohlc: Sequence[OhlcBar], *, ex_div_dates: Collection[date] = frozenset()
) -> RvResult:
    """RV Yang-Zhang de 30 barras, em **% a.a.**, base 252 (tenor do VRP).

    Verbete: ``docs/glossario.md`` → "Volatilidade realizada (RV)". Entrada:
    OHLC em centavos com ``adj_factor`` de escala já resolvido por barra;
    saída: ``RvResult.value`` em % a.a., ``None`` + flag nas ausências.
    """
    return yang_zhang_vol(ohlc, 30, ex_div_dates=ex_div_dates)


def rv60(
    ohlc: Sequence[OhlcBar], *, ex_div_dates: Collection[date] = frozenset()
) -> RvResult:
    """RV Yang-Zhang de 60 barras, em **% a.a.**, base 252.

    Verbete: ``docs/glossario.md`` → "Volatilidade realizada (RV)". Entrada:
    OHLC em centavos com ``adj_factor`` de escala já resolvido por barra;
    saída: ``RvResult.value`` em % a.a., ``None`` + flag nas ausências.
    """
    return yang_zhang_vol(ohlc, 60, ex_div_dates=ex_div_dates)


def _sorted_bars(ohlc: Sequence[OhlcBar]) -> list[OhlcBar]:
    """Barras em ordem cronológica; sessão repetida fica com a última."""
    ordered = sorted(ohlc, key=lambda bar: bar.refdate)
    if len(ordered) < 2:
        return ordered
    deduped = [ordered[0]]
    for bar in ordered[1:]:
        if bar.refdate == deduped[-1].refdate:
            deduped[-1] = bar
        else:
            deduped.append(bar)
    return deduped


def _bar_logs(bar: OhlcBar) -> _Logs | None:
    """Logs da barra ajustada; ``None`` quando algum preço não é positivo."""
    factor = bar.adj_factor
    prices = (
        bar.open * factor,
        bar.high * factor,
        bar.low * factor,
        bar.close * factor,
    )
    if any(price <= 0 for price in prices):
        return None
    return tuple(math.log(price) for price in prices)  # type: ignore[return-value]


def _observation(previous: _Logs, current: _Logs) -> _Observation:
    """Retornos da barra corrente contra o fechamento anterior, em log."""
    open_i, high_i, low_i, close_i = current
    close_prev = previous[3]
    return _Observation(
        overnight=open_i - close_prev,
        intraday=close_i - open_i,
        high_log=high_i - close_prev,
        low_log=low_i - close_prev,
        close_log=close_i - close_prev,
    )


def _yang_zhang_variance(observations: Sequence[_Observation]) -> float:
    """σ²_YZ diária (não anualizada) — equação (5) do paper."""
    n = len(observations)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    variance_o = _sample_variance([obs.overnight for obs in observations])
    variance_c = _sample_variance([obs.intraday for obs in observations])
    variance_rs = math.fsum(
        (obs.high_log - obs.close_log) * (obs.high_log - obs.overnight)
        + (obs.low_log - obs.close_log) * (obs.low_log - obs.overnight)
        for obs in observations
    ) / n
    return variance_o + k * variance_c + (1.0 - k) * variance_rs


def _sample_variance(values: Sequence[float]) -> float:
    """Variância amostral (divisor n−1), como no paper."""
    mean = math.fsum(values) / len(values)
    return math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)


# ---------------------------------------------------------------------------
# 2. Pregão dominante — verbete "Pregão dominante na RV"
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShockDay:
    """Pregão que mais pesa na variância da janela e a fração que ele explica."""

    refdate: date | None
    share: float | None
    n_returns: int
    flags: tuple[str, ...]

    @property
    def is_shock(self) -> bool:
        """A fração passa de metade (ADR-035): o aviso do painel liga."""
        return self.share is not None and self.share >= SHOCK_SHARE_MIN


def rv_shock_day(
    ohlc: Sequence[OhlcBar],
    window: int = 30,
    *,
    ex_div_dates: Collection[date] = frozenset(),
) -> ShockDay:
    """Pregão dominante e ``r²_max / Σr²`` da janela, em fração 0–1.

    Verbete: ``docs/glossario.md`` → "Pregão dominante na RV". Entrada: OHLC
    com ``adj_factor`` já resolvido por barra; retornos **fechamento-a-fechamento**
    das últimas ``window`` barras (padrão 30), sem centrar. Saída: ``refdate``
    (data do pregão), ``share`` (fração 0–1) e ``n_returns``; ``None`` + flag
    ``insufficient_returns`` com menos de 10 retornos ou variância nula
    (``zero_variance``). Datas ex de provento saem da janela sem puxar barras.
    """
    bars = _sorted_bars(ohlc)[-window:]
    returns: list[tuple[date, float]] = []
    ex_dates = frozenset(ex_div_dates)
    for previous, current in zip(bars, bars[1:], strict=False):
        if current.refdate in ex_dates:
            continue
        before = previous.close * previous.adj_factor
        after = current.close * current.adj_factor
        if before <= 0 or after <= 0:
            continue
        returns.append((current.refdate, math.log(after / before)))
    if len(returns) < SHOCK_MIN_RETURNS:
        return ShockDay(None, None, len(returns), (FLAG_INSUFFICIENT_RETURNS,))
    total = math.fsum(value * value for _, value in returns)
    if total <= VARIANCE_EPSILON:
        return ShockDay(None, None, len(returns), (FLAG_ZERO_VARIANCE,))
    day, value = max(returns, key=lambda item: item[1] * item[1])
    return ShockDay(day, value * value / total, len(returns), ())


# ---------------------------------------------------------------------------
# 3. VRP, z-score, rank e percentil — verbetes "VRP", "z-score do VRP",
#    "IV rank e percentil"
# ---------------------------------------------------------------------------


def vrp(iv_atm_30d: float | None, rv30: float | None) -> MetricResult[float]:
    """``iv_atm_30d − rv30``, em **pontos de vol** (tenor casado, spec §6.7).

    Verbete: ``docs/glossario.md`` → "VRP". Entrada: IV e RV em % a.a.; saída:
    pontos de vol. ``None`` + ``missing_input``/``non_finite_value`` se faltar
    algum número (nunca zero para "não sei").
    """
    if iv_atm_30d is None or rv30 is None:
        return _none(FLAG_MISSING_INPUT)
    if not (math.isfinite(iv_atm_30d) and math.isfinite(rv30)):
        return _none(FLAG_NON_FINITE)
    return _ok(iv_atm_30d - rv30)


def vrp_z(vrp_series: Sequence[float | None]) -> MetricResult[float]:
    """Z-score do VRP de hoje na janela de 252 sessões (hoje incluso).

    Verbete: ``docs/glossario.md`` → "z-score do VRP". Entrada: série de VRP em
    pontos de vol, terminando em hoje; saída: adimensional (desvios-padrão).
    Desvio **amostral** (n−1), como manda o contrato §5.4. ``None`` +
    ``insufficient_history`` com janela incompleta, ``non_finite_value`` com
    valor não finito, ``degenerate_std`` com desvio zero.
    """
    sample = _window_252(vrp_series)
    if sample is None:
        if len(vrp_series) < Z_WINDOW:
            return _none(FLAG_INSUFFICIENT_HISTORY)
        return _none(FLAG_NON_FINITE)
    mean = math.fsum(sample) / Z_WINDOW
    variance = math.fsum((value - mean) ** 2 for value in sample) / (Z_WINDOW - 1)
    if variance <= 0.0:
        return _none(FLAG_DEGENERATE_STD)
    return _ok((sample[-1] - mean) / math.sqrt(variance))


def iv_rank_252(iv_series: Sequence[float | None]) -> MetricResult[float]:
    """Posição min–max de hoje nas últimas 252 IVs, em 0–100.

    Verbete: ``docs/glossario.md`` → "IV rank e percentil". Entrada: IVs em
    % a.a. terminando em hoje; saída: 0–100. ``None`` + ``insufficient_history``,
    ``non_finite_value`` ou ``degenerate_range`` (máx == mín).
    """
    sample = _window_252(iv_series)
    if sample is None:
        return _none(
            FLAG_INSUFFICIENT_HISTORY if len(iv_series) < IV_WINDOW else FLAG_NON_FINITE
        )
    low, high = min(sample), max(sample)
    if high <= low:
        return _none(FLAG_DEGENERATE_RANGE)
    return _ok(100.0 * (sample[-1] - low) / (high - low))


def iv_percentile_252(iv_series: Sequence[float | None]) -> MetricResult[float]:
    """Percentil inclusivo de hoje nas últimas 252 IVs, em 0–100.

    Verbete: ``docs/glossario.md`` → "IV rank e percentil". Entrada: IVs em
    % a.a. terminando em hoje; saída: ``100·#{v ≤ hoje}/252``, 0–100. ``None``
    + ``insufficient_history`` ou ``non_finite_value``.
    """
    sample = _window_252(iv_series)
    if sample is None:
        return _none(
            FLAG_INSUFFICIENT_HISTORY if len(iv_series) < IV_WINDOW else FLAG_NON_FINITE
        )
    today = sample[-1]
    count = sum(1 for value in sample if value <= today)
    return _ok(100.0 * count / len(sample))


def _window_252(series: Sequence[float | None]) -> list[float] | None:
    """Últimas 252 observações (hoje incluso); ``None`` se faltam ou há `None`/NaN."""
    if len(series) < Z_WINDOW:
        return None
    sample = series[-Z_WINDOW:]
    if any(value is None or not math.isfinite(value) for value in sample):
        return None
    return [float(value) for value in sample]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. Skew 25Δ — verbetes "Skew e risk reversal" e "Butterfly"
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeltaPoint:
    """Ponto do smile: ``delta`` com sinal da ponta (call > 0, put < 0)."""

    delta: float
    iv_pct: float


@dataclass(frozen=True, slots=True)
class SkewResult:
    """Risk reversal e butterfly a 25Δ, ambos em pontos de vol."""

    rr25: float | None
    bfly25: float | None
    n_band: int
    flags: tuple[str, ...]


def rr25_bfly25(chain: Sequence[DeltaPoint], atm_iv: float | None) -> SkewResult:
    """``RR25 = IV25c − IV25p`` e ``BFLY25 = ½(IV25c+IV25p) − IV_ATM``.

    Verbetes: ``docs/glossario.md`` → "Skew e risk reversal" e "Butterfly".
    Entrada: Δ assinado e IV em % a.a. por ponto da cadeia; ``atm_iv`` em % a.a.
    Saída: pontos de vol. Banda ``|Δ| ∈ [0,20; 0,80]``, mínimo 5 pontos, 25Δ
    por interpolação linear em ``|Δ|`` **por lado**, sem extrapolação. Falta de
    bracket ou de pontos na banda → ``None`` + ``sparse_chain``; sem ATM →
    ``bfly25 = None`` + ``no_atm_iv`` (o RR continua valendo).
    """
    band = [point for point in chain if BAND_LOW <= abs(point.delta) <= BAND_HIGH]
    n_band = len(band)
    if n_band < MIN_STRIKES_BAND:
        return SkewResult(None, None, n_band, (FLAG_SPARSE_CHAIN,))
    calls = sorted(
        (abs(point.delta), point.iv_pct) for point in band if point.delta > 0.0
    )
    puts = sorted(
        (abs(point.delta), point.iv_pct) for point in band if point.delta < 0.0
    )
    iv_call_25 = _iv_at_delta(calls, TARGET_DELTA_25)
    iv_put_25 = _iv_at_delta(puts, TARGET_DELTA_25)
    if iv_call_25 is None or iv_put_25 is None:
        return SkewResult(None, None, n_band, (FLAG_SPARSE_CHAIN,))
    rr25 = iv_call_25 - iv_put_25
    if atm_iv is None:
        return SkewResult(rr25, None, n_band, (FLAG_NO_ATM_IV,))
    bfly25 = 0.5 * (iv_call_25 + iv_put_25) - atm_iv
    return SkewResult(rr25, bfly25, n_band, ())


def _iv_at_delta(points: Sequence[tuple[float, float]], target: float) -> float | None:
    """IV em ``|Δ| = target`` por interpolação linear; ``None`` sem bracket."""
    if not points:
        return None
    if target < points[0][0] or target > points[-1][0]:
        return None
    for (low_delta, low_iv), (high_delta, high_iv) in zip(
        points, points[1:], strict=False
    ):
        if low_delta <= target <= high_delta:
            span = high_delta - low_delta
            if span <= 0.0:
                return low_iv
            return low_iv + (high_iv - low_iv) * ((target - low_delta) / span)
    return points[-1][1]


# ---------------------------------------------------------------------------
# 5. Estrutura a termo — verbete "Estrutura a termo"
# ---------------------------------------------------------------------------


def ts_slope_30_90(
    iv_atm_30d: float | None, iv_atm_90d: float | None
) -> MetricResult[float]:
    """``IV_ATM90 − IV_ATM30``, em **pontos de vol** (contango positivo).

    Verbete: ``docs/glossario.md`` → "Estrutura a termo" (ADR-013). Entrada:
    IVs ATM em % a.a. nos tenores de 30/252 e 90/252; saída: pontos de vol.
    ``None`` + ``missing_input`` se algum tenor não tem ATM; ``non_finite_value``
    se algum número não é finito. Sempre casado por vencimento, nunca
    extrapolado.
    """
    if iv_atm_30d is None or iv_atm_90d is None:
        return _none(FLAG_MISSING_INPUT)
    if not (math.isfinite(iv_atm_30d) and math.isfinite(iv_atm_90d)):
        return _none(FLAG_NON_FINITE)
    return _ok(iv_atm_90d - iv_atm_30d)


# ---------------------------------------------------------------------------
# 6. Fator de desconto — verbete "Fator de desconto (DF)"
# ---------------------------------------------------------------------------


def _curve_arrays(curve: Sequence[tuple[int, float]]) -> tuple[list[int], list[float]]:
    """Nós ordenados por DU e o ln(DF) de cada um; valida a grade da curva."""
    if not curve:
        return [], []
    by_du: dict[int, float] = {}
    for du, rate in curve:
        if du <= 0:
            raise ValueError(f"nó de curva com du <= 0: {du}")
        if not math.isfinite(rate):
            raise ValueError(f"taxa não finita no nó du={du}: {rate}")
        previous = by_du.get(du)
        if previous is not None and previous != rate:
            raise ValueError(f"dois nós para du={du} com taxas diferentes")
        by_du[du] = rate
    nodes = sorted(by_du)
    log_dfs = [-(by_du[node] / 100.0) * (node / SESSIONS_PER_YEAR) for node in nodes]
    return nodes, log_dfs


def log_discount_factor(
    du: int, curve: Sequence[tuple[int, float]]
) -> MetricResult[float]:
    """``ln(DF)`` em ``du`` dias úteis, interpolado linearmente em DU.

    Verbete: ``docs/glossario.md`` → "Fator de desconto (DF)". Entrada: curva
    ETTJ em pares ``(du, taxa % a.a.)``; saída: ``ln(DF)`` adimensional.
    Abaixo do primeiro nó usa o primeiro; acima do último → ``None`` +
    ``curve_extrapolated``; curva vazia → ``None`` + ``no_curve``.
    """
    nodes, log_dfs = _curve_arrays(curve)
    if not nodes:
        return _none(FLAG_NO_CURVE)
    if du <= nodes[0]:
        return _ok(log_dfs[0])
    if du > nodes[-1]:
        return _none(FLAG_CURVE_EXTRAPOLATED)
    position = bisect_right(nodes, du) - 1
    if nodes[position] == du:
        return _ok(log_dfs[position])
    span = nodes[position + 1] - nodes[position]
    weight = (du - nodes[position]) / span
    return _ok(log_dfs[position] + weight * (log_dfs[position + 1] - log_dfs[position]))


def discount_factor_from_curve(
    du: int, curve: Sequence[tuple[int, float]]
) -> MetricResult[float]:
    """``DF`` no nó/interpolação de ``du`` dias úteis da curva ETTJ.

    Verbete: ``docs/glossario.md`` → "Fator de desconto (DF)". Entrada: DU
    inteiro e curva em % a.a.; saída: DF em 0–1. ``du <= 0`` (vencimento na
    própria sessão) → ``DF = 1``; acima do último nó → ``None`` +
    ``curve_extrapolated`` (nunca taxa inventada, spec §6.0).
    """
    if du <= 0:
        return _ok(1.0)
    log_df = log_discount_factor(du, curve)
    if log_df.value is None:
        return _none(*log_df.flags)
    return _ok(math.exp(log_df.value))


def discount_factor(rate_pct: float, t: float) -> MetricResult[float]:
    """``DF = exp(−r·t)`` com ``r`` em **% a.a.** e ``t = du/252`` em anos.

    Verbete: ``docs/glossario.md`` → "Fator de desconto (DF)". Saída: DF 0–1.
    ``None`` + ``invalid_input`` se a taxa não é finita ou ``t < 0``/não finito.
    """
    if not math.isfinite(rate_pct) or not math.isfinite(t) or t < 0.0:
        return _none(FLAG_INVALID_INPUT)
    return _ok(math.exp(-(rate_pct / 100.0) * t))


# ---------------------------------------------------------------------------
# 7. Forward por carry e dividendo implícito — verbetes "Forward implícito"
#    e "Dividendo implícito"
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dividend:
    """Provento por ação na data ``du`` dias úteis após a sessão."""

    du: int
    amount_brl: float


@dataclass(frozen=True, slots=True)
class CarryForward:
    """Forward por carry e o VP dos proventos, com flags de ausência."""

    forward: float | None
    rate_pct: float | None
    pv_dividends_brl: float | None
    flags: tuple[str, ...]


def forward_by_carry(
    spot: float,
    curve: Sequence[tuple[int, float]],
    t: float,
    dividends: Sequence[Dividend] = (),
) -> CarryForward:
    """``F = S·e^{r·t} − VP(proventos)`` com a curva ETTJ, sem extrapolar.

    Verbete: ``docs/glossario.md`` → "Forward implícito". Entrada: ``spot`` em
    R$/ação, curva em pares ``(du, taxa % a.a.)``, ``t = du/252`` em anos e
    proventos ``(du, R$/ação)``. Saída: forward em R$/ação, ``rate_pct`` em
    % a.a. (taxa efetiva do nó de ``du = round(t·252)``) e VP dos proventos
    **composto até o vencimento** (``Σ D_i·DF_i/DF_T``), para casar
    dimensionalmente com ``S·e^{r·t}``. ``spot <= 0`` ou ``t <= 0`` →
    ``invalid_input``/``invalid_tenor``; provento além do último nó da curva →
    ``curve_extrapolated``. Nunca se inventa taxa nem se descarta provento
    silenciosamente.
    """
    if not math.isfinite(spot) or spot <= 0.0:
        return CarryForward(None, None, None, (FLAG_INVALID_INPUT,))
    if not math.isfinite(t) or t <= 0.0:
        return CarryForward(None, None, None, (FLAG_INVALID_TENOR,))
    du_t = round(t * SESSIONS_PER_YEAR)
    log_df_t = log_discount_factor(du_t, curve)
    if log_df_t.value is None:
        return CarryForward(None, None, None, log_df_t.flags)
    df_t = math.exp(log_df_t.value)
    rate_pct = -log_df_t.value * 100.0 / t
    pv_today = 0.0
    for dividend in dividends:
        if not math.isfinite(dividend.amount_brl) or dividend.amount_brl < 0.0:
            return CarryForward(None, None, None, (FLAG_INVALID_INPUT,))
        if dividend.du <= 0:
            df_i = 1.0
        else:
            log_df_i = log_discount_factor(dividend.du, curve)
            if log_df_i.value is None:
                return CarryForward(None, None, None, log_df_i.flags)
            df_i = math.exp(log_df_i.value)
        pv_today += dividend.amount_brl * df_i
    pv_at_expiry = pv_today / df_t
    forward = spot / df_t - pv_at_expiry
    return CarryForward(forward, rate_pct, pv_at_expiry, ())


def implied_div_yield_pct(
    rate_pct: float | None,
    forward: float | None,
    spot: float,
    t: float,
) -> MetricResult[float]:
    """``r − 100·ln(F/S)/T``, em **% a.a.** (dividendo implícito contínuo).

    Verbete: ``docs/glossario.md`` → "Dividendo implícito". Entrada: ``r`` em
    % a.a., forward e spot na **mesma** unidade (a razão é adimensional), ``t``
    em anos (``du/252``); saída: % a.a. ``None`` + ``missing_input`` se falta
    taxa/forward, ``invalid_input`` se ``t <= 0`` ou preços não positivos.
    """
    if rate_pct is None or forward is None:
        return _none(FLAG_MISSING_INPUT)
    if (
        not math.isfinite(rate_pct)
        or not math.isfinite(forward)
        or not math.isfinite(spot)
        or not math.isfinite(t)
        or t <= 0.0
        or forward <= 0.0
        or spot <= 0.0
    ):
        return _none(FLAG_INVALID_INPUT)
    return _ok(rate_pct - 100.0 * math.log(forward / spot) / t)


# ---------------------------------------------------------------------------
# 8. Dias para liquidar — verbete "Dias para liquidar"
# ---------------------------------------------------------------------------


def days_to_liquidate(
    total_open_interest: int | None,
    average_daily_quantity: float | None,
) -> MetricResult[float]:
    """``OI / quantidade média negociada 20d``, ambos em **ações**.

    Verbete: ``docs/glossario.md`` → "Dias para liquidar". Entrada: OI em ações
    do ativo-objeto (``Total de posições`` da B3) e quantidade média em
    ações/dia; saída: número de pregões (dias). ``None`` + ``oi_unavailable``
    ou ``volume_unavailable`` se falta OI ou se o denominador não é positivo —
    usar volume em R$ aqui daria número sem unidade (ADR-020).
    """
    if (
        total_open_interest is None
        or isinstance(total_open_interest, bool)
        or total_open_interest < 0
    ):
        return _none(FLAG_OI_UNAVAILABLE)
    if (
        average_daily_quantity is None
        or not math.isfinite(average_daily_quantity)
        or average_daily_quantity <= 0.0
    ):
        return _none(FLAG_VOLUME_UNAVAILABLE)
    return _ok(float(total_open_interest) / average_daily_quantity)


# ---------------------------------------------------------------------------
# 9. Expected move — verbete "Expected move"
# ---------------------------------------------------------------------------


def expected_move_1s(straddle_brl: float | None, spot: float) -> MetricResult[float]:
    """``1,25 × straddle / spot``, em **fração** do spot (0,0625 = 6,25%).

    Verbete: ``docs/glossario.md`` → "Expected move". Entrada: straddle ATM e
    spot na mesma unidade (R$/ação); saída: fração (quem serializa ×100).
    ``None`` + ``invalid_input`` se o spot não é positivo ou o straddle é
    negativo/não finito — nunca zero para esconder dado corrompido.
    """
    if (
        straddle_brl is None
        or not math.isfinite(straddle_brl)
        or straddle_brl < 0.0
        or not math.isfinite(spot)
        or spot <= 0.0
    ):
        return _none(FLAG_INVALID_INPUT)
    return _ok(EXP_MOVE_FACTOR * straddle_brl / spot)


def expected_move_from_atm_iv(iv_pct: float | None, t: float) -> MetricResult[float]:
    """``1,25 × straddle_ATM(σ, t) / S`` com o straddle teórico em ``K = F``.

    Verbete: ``docs/glossario.md`` → "Expected move" (ADR-053). O straddle ATM
    por Black-76 em ``K = F`` é ``2·DF·F·(2N(σ√t/2) − 1)``; com ``DF·F/S = 1``
    (carry ignorado, flag no chamador) o expected move é
    ``1,25 · 2 · (2N(σ√t/2) − 1)``. Entrada: IV em % a.a., ``t`` em anos úteis;
    saída: fração do spot. Serve para levar a IV ATM 30d externa ao mesmo
    construto do campo interno, quando a captura não tem vencimentos em volta
    de 30 du para interpolar o straddle.
    """
    if iv_pct is None or not math.isfinite(iv_pct) or iv_pct < 0.0:
        return _none(FLAG_INVALID_INPUT)
    if not math.isfinite(t) or t <= 0.0:
        return _none(FLAG_INVALID_INPUT)
    half = (iv_pct / 100.0) * math.sqrt(t) / 2.0
    call_over_f = math.erf(half / math.sqrt(2.0))  # 2N(x) − 1 = erf(x/√2)
    return _ok(EXP_MOVE_FACTOR * 2.0 * call_over_f)


# ---------------------------------------------------------------------------
# 10. P&L e exposição delta — verbetes "Payoff no vencimento", "P&L da
#     posição", "Exposição delta em reais", "Gregas agregadas da estrutura"
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PositionLeg:
    """Uma perna em **ações**: ``stock``, ``call`` ou ``put``, ``long``/``short``."""

    ticker: str
    kind: str
    side: str
    quantity: int
    price_brl: float
    strike_brl: float | None = None


@dataclass(frozen=True, slots=True)
class LegMark:
    """Marca EOD por ação e gregas da perna; campos ausentes são ``None``."""

    price_brl: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta_brl: float | None = None
    vega_brl: float | None = None


@dataclass(frozen=True, slots=True)
class PositionGreeks:
    """Gregas líquidas; ``complete=False`` quando alguma grega falta."""

    delta: float | None
    delta_exposure_brl: float | None
    gamma: float | None
    theta_brl: float | None
    vega_brl: float | None
    complete: bool
    flags: tuple[str, ...]


def _validate_leg(leg: PositionLeg) -> PositionLeg:
    """Valida a perna; erro de chamada levanta ``ValueError`` (não é dado ausente)."""
    if not isinstance(leg.ticker, str) or not leg.ticker.strip():
        raise ValueError("ticker precisa ser texto não vazio")
    if leg.kind not in {"stock", "call", "put"}:
        raise ValueError(f"kind inválido na perna {leg.ticker!r}: {leg.kind!r}")
    if leg.side not in {"long", "short"}:
        raise ValueError(f"side inválido na perna {leg.ticker!r}: {leg.side!r}")
    if isinstance(leg.quantity, bool) or not isinstance(leg.quantity, int) or leg.quantity <= 0:
        raise ValueError(f"quantity inválido na perna {leg.ticker!r}")
    if not math.isfinite(leg.price_brl) or leg.price_brl < 0.0:
        raise ValueError(f"price_brl inválido na perna {leg.ticker!r}")
    if leg.kind != "stock" and (
        leg.strike_brl is None
        or not math.isfinite(leg.strike_brl)
        or leg.strike_brl <= 0.0
    ):
        raise ValueError(f"strike_brl não positivo em {leg.ticker!r}")
    return leg


def _sign(side: str) -> float:
    """Sinal do lado: long = +1, short = −1."""
    return 1.0 if side == "long" else -1.0


def _terminal_value(leg: PositionLeg, spot: float) -> float:
    """Valor terminal por ação: ação = S; call = max(S−K,0); put = max(K−S,0)."""
    if leg.kind == "stock":
        return spot
    assert leg.strike_brl is not None
    if leg.kind == "call":
        return max(spot - leg.strike_brl, 0.0)
    return max(leg.strike_brl - spot, 0.0)


def payoff_pnl(legs: Sequence[PositionLeg], spot: float) -> MetricResult[float]:
    """P&L no vencimento: ``Σ sinal × quantidade × (terminal − entrada)``.

    Verbete: ``docs/glossario.md`` → "Payoff no vencimento". Entrada: pernas com
    quantidade em **ações** e preços em R$/ação, spot em R$/ação; saída: P&L em
    R$. ``None`` + ``invalid_input`` se o spot não é finito ou negativo; é uma
    função determinística do preço terminal, não previsão (ADR-024).
    """
    if not math.isfinite(spot) or spot < 0.0:
        return _none(FLAG_INVALID_INPUT)
    total = 0.0
    for raw in legs:
        leg = _validate_leg(raw)
        total += (
            _sign(leg.side)
            * leg.quantity
            * (_terminal_value(leg, spot) - leg.price_brl)
        )
    return _ok(total)


def marked_pnl(
    legs: Sequence[PositionLeg], marks: Mapping[str, LegMark | None]
) -> MetricResult[float]:
    """P&L marcado: ``Σ sinal × quantidade × (marca − entrada)``.

    Verbete: ``docs/glossario.md`` → "P&L da posição". Entrada: pernas em ações
    e R$/ação, marcas por ``ticker`` em R$/ação; saída: P&L em R$. Marca
    ausente, zero ou não finita → ``None`` + ``mark_missing`` para a posição
    inteira (nunca contribuir zero silencioso, P-N).
    """
    if not isinstance(marks, Mapping):
        raise ValueError("marks precisa ser um mapeamento ticker → LegMark")
    total = 0.0
    for raw in legs:
        leg = _validate_leg(raw)
        mark = marks.get(leg.ticker)
        price = None if mark is None else mark.price_brl
        if price is None or not math.isfinite(price) or price <= 0.0:
            return _none(FLAG_MARK_MISSING)
        total += _sign(leg.side) * leg.quantity * (price - leg.price_brl)
    return _ok(total)


def position_greeks(
    legs: Sequence[PositionLeg],
    marks: Mapping[str, LegMark | None],
    spot: float,
) -> PositionGreeks:
    """Gregas líquidas e exposição ``delta × quantidade × spot`` em R$.

    Verbetes: ``docs/glossario.md`` → "Gregas agregadas da estrutura" e
    "Exposição delta em reais". Entrada: pernas em ações, marcas por ``ticker``
    (Δ/Γ/Θ R$/ação/ν por ponto de vol), spot em R$/ação; saída: gregas somadas
    com o sinal da perna (ação: Δ = qty; Γ = Θ = ν = 0 por definição).

    Se **qualquer** perna de opção não tem marca (ausente, zero ou não finita —
    zero não é preço) ou alguma grega, os agregados saem ``None`` com
    ``gregas_parciais`` (e ``mark_missing`` se a marca faltou): nunca se soma
    zero no lugar do dado ausente (regra 5 do AGENTS).
    """
    if not isinstance(marks, Mapping):
        raise ValueError("marks precisa ser um mapeamento ticker → LegMark")
    if not math.isfinite(spot) or spot <= 0.0:
        return PositionGreeks(None, None, None, None, None, False, (FLAG_INVALID_INPUT,))
    delta = gamma = theta = vega = 0.0
    flags: set[str] = set()
    for raw in legs:
        leg = _validate_leg(raw)
        sign_qty = _sign(leg.side) * leg.quantity
        if leg.kind == "stock":
            delta += sign_qty
            continue
        mark = marks.get(leg.ticker)
        price = None if mark is None else mark.price_brl
        if price is None or not math.isfinite(price) or price <= 0.0:
            flags.add(FLAG_MARK_MISSING)
            flags.add(FLAG_PARTIAL_GREEKS)
            continue
        values = {
            "delta": mark.delta,
            "gamma": mark.gamma,
            "theta_brl": mark.theta_brl,
            "vega_brl": mark.vega_brl,
        }
        if any(
            value is None or not math.isfinite(value) for value in values.values()
        ):
            flags.add(FLAG_PARTIAL_GREEKS)
            continue
        delta += sign_qty * float(values["delta"])
        gamma += sign_qty * float(values["gamma"])
        theta += sign_qty * float(values["theta_brl"])
        vega += sign_qty * float(values["vega_brl"])
    if flags:
        ordered = tuple(sorted(flags))
        return PositionGreeks(None, None, None, None, None, False, ordered)
    return PositionGreeks(
        delta, delta * spot, gamma, theta, vega, True, ()
    )


# ---------------------------------------------------------------------------
# CLI — só documentação; nenhum I/O de dado nem cálculo de relatório (F10)
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Mostra a descrição do módulo; o benchmark chamará as funções diretamente."""
    parser = argparse.ArgumentParser(
        prog="bench_recalc",
        description=(
            "Funções puras (stdlib) que recalculam as métricas de mercado do "
            "benchmark F10: RV Yang-Zhang 20/30/60, pregão dominante, VRP/z, "
            "rank/percentil, RR25/BFLY25, ts_slope_30_90, DF/ETTJ, forward por "
            "carry, dividendo implícito, dias para liquidar, expected move e "
            "P&L/exposição delta. Não importa b3o, não acessa rede, não lê "
            "arquivo e não gera relatório — Python calcula, agente interpreta, "
            "humano decide (AGENTS.md, regra 1)."
        ),
    )
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
