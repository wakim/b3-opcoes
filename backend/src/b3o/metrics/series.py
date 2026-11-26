"""Métricas por série e o smile por strike (F1b, contrato §4.3/§4.5) — PURO.

Entra uma linha de opção já resolvida (papel, spot, `t`, leg) e sai o que vai
para `metrics_series`: preço por ação, IV em % a.a., gregas em unidade
interpretável, liquidez e `flags[]`. Nenhum I/O, nenhum duckdb/pyarrow: o
`pipeline.py` costura leitura e gravação.

Preço (ADR-017)
---------------
O preço que entra na IV é o **fechamento do negócio** (`close`), nunca o mid do
book. O book de fechamento da B3 é largo e velho (PETRX44 em 19/11/2026 tinha
`70/167` com fechamento em 157 ¢; o mid dava IV 24,8 contra 32,8 do fechamento).
`best_bid`/`best_ask` alimentam **só** a liquidez e as flags (`one_sided`,
`wide_spread`); a coluna `mid` da tabela carrega o `close` por ação
(`close / quote_factor`), como o ADR-017 manda.

Unidades
--------
`strike`, `mid` e preços em **centavos por ação**; IV em **% a.a.**; o `iv.py`
devolve fração e a conversão `×100` mora aqui. `theta_brl`/`vega_brl`/`rho` saem
de `greeks.greeks_black76` na moeda de `F` (centavos) e são **divididos por 100**
para virar R$ por ação — o nome do campo no contrato diz R$; o `/quote_factor` já
é feito pela grega. `gamma` é a exceção: como é ∂Δ/∂F, converter `F` de centavos
para R$ exige **multiplicar por 100** (γ por centavo → γ por R$ de variação do
forward, isto é, a variação do delta por ação para cada R$1 de variação de F/forward).

Smile por strike (contrato §4.5 passo 6)
---------------------------------------
Uma linha por strike: a IV é a **média** das IVs válidas de call e put do mesmo
strike (paridade manda que sejam iguais; a média divide o ruído de tick) e o `Δ`
do ponto é o da perna **OTM** (call acima do forward, put abaixo; nunca a média
de Δ de call com Δ de put, que se anula). Perna sem IV (intrínseco, preço fora de
faixa, `leg_mismatch`, `american_call_ex_div`) fica fora da média; strike sem
nenhuma perna válida fica fora do smile.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date

from b3o.derive.atm import SmilePoint
from b3o.derive.greeks import Greeks, greeks_black76
from b3o.derive.iv import implied_vol_detail

MIN_TRADES_5D = 5
"""`trades ≥ 5` em 5 sessões para a série ser líquida (spec §6.9)."""

SPREAD_MAX_PCT = 10.0
"""Spread máximo, em % do mid (`(ask − bid) / mid × 100`), para a série ser líquida."""

AMERICAN_CALL_DELTA = 0.85
"""Δ acima disto marca a call americana ITM (contrato §4.5 passo 7)."""

FLAG_ZERO_STRIKE = "zero_strike"
"""Linha de opção com `strike <= 0` (órfã de ajuste de grade do IBOV, AGENTS)."""

FLAG_LEG_MISMATCH = "leg_mismatch"
"""`strike` do leg persistido difere do `raw_cotahist` do dia (§4.8: o leg manda)."""

FLAG_ONE_SIDED = "one_sided"
"""Sem book dos dois lados (`bid > 0` e `ask > 0`)."""

FLAG_WIDE_SPREAD = "wide_spread"
"""`spread_pct > 10` — book dos dois lados, mas largo."""

FLAG_NO_TRADE_5D = "no_trade_5d"
"""Menos de 5 negócios nas últimas 5 sessões."""

FLAG_STYLE_UNKNOWN = "style_unknown"
"""Estilo `None` no leg: não entra na paridade (style_strict)."""

FLAG_T_ESTIMATED = "t_estimated"
"""Vencimento depois da última sessão do lake: `T` vem do calendário de feriados."""

FLAG_IV_EXPIRED = "iv_expired"
"""`t <= 0`: vencimento no fechamento, só intrínseco."""

FLAG_NO_RATES = "no_rates"
"""Sessão sem curva: IV `None` — nunca `DF = 1` (§4.4)."""

FLAG_NO_FORWARD = "no_forward"
"""Vencimento sem forward utilizável (sem par de paridade)."""

FLAG_FORWARD_OUTLIER = "forward_outlier"
"""Forward rejeitado pela sanidade de carry: `|ln(F/S)/T − r| > 5% a.a.`."""

FLAG_AMERICAN_CALL_EX_DIV = "american_call_ex_div"
"""Call americana ITM com ex-dividendo antes do vencimento: fora do smile."""

FLAG_SCALE_EVENT = "scale_event"
"""Evento de escala (desdobramento/grupamento) do papel na sessão."""

FLAG_INCONSISTENT = "inconsistent"
"""Validadores do smile (`checks.check_chain`): a série sai do smile."""

FLAG_BFLY_NEGATIVE = "bfly_negative"
FLAG_CALENDAR_VIOLATION = "calendar_violation"
FLAG_MONOTONICITY_VIOLATION = "monotonicity_violation"


@dataclass(frozen=True, slots=True)
class OptionQuote:
    """Uma linha de opção do dia já resolvida pelo `QueryService`.

    Preços em centavos **por contrato** (o `quote_factor` ainda não foi
    aplicado); `spot` em centavos por ação.
    """

    refdate: date
    ticker: str
    underlying: str
    underlying_market: str
    maturity: date
    leg_id: str
    leg_index: int
    side: str
    style: str | None
    strike: int
    strike_raw: int
    distribution_id: str
    quote_factor: int
    close: int
    best_bid: int
    best_ask: int
    trades: int
    volume: int
    spot: float
    t: float

    @property
    def price(self) -> float:
        """Preço por ação em centavos: `close / quote_factor` (ADR-017: o close)."""
        return self.close / self.quote_factor


@dataclass(frozen=True, slots=True)
class EvaluatedQuote:
    """Série com IV, gregas, liquidez e flags — o insumo de `metrics_series`."""

    quote: OptionQuote
    price: float
    spread_pct: float | None
    iv_pct: float | None
    greeks: Greeks | None
    trades_5d: int
    is_liquid: bool
    flags: tuple[str, ...]

    @property
    def usable_for_smile(self) -> bool:
        """Tem IV e nenhuma razão de exclusão do smile (§4.5 passos 6–7)."""
        if self.iv_pct is None:
            return False
        excluded = {
            FLAG_LEG_MISMATCH,
            FLAG_AMERICAN_CALL_EX_DIV,
            FLAG_INCONSISTENT,
        }
        return not (excluded & set(self.flags))


@dataclass(frozen=True, slots=True)
class StrikeSmile:
    """Um strike consolidado do vencimento: IV média e Δ da perna OTM."""

    strike: int
    ticker: str
    iv_pct: float | None
    delta: float | None
    flags: tuple[str, ...] = ()

    def point(self, maturity: date, t: float) -> SmilePoint:
        """`SmilePoint` do `atm.py` (o tipo que `atm_by_expiry`/`check_chain` usam)."""
        return SmilePoint(
            ticker=self.ticker,
            maturity=maturity,
            strike=self.strike,
            t=t,
            iv_pct=self.iv_pct,
            delta=self.delta,
            flags=self.flags,
        )


@dataclass(frozen=True, slots=True)
class SeriesMetrics:
    """Uma linha de `data/metrics_series` (schema do contrato §4.3)."""

    refdate: date
    ticker: str
    maturity: date
    leg_id: str
    side: str
    style: str | None
    strike: int
    t: float
    mid: float
    quote_factor: int
    iv: float | None
    delta: float | None
    gamma: float | None
    theta_brl: float | None
    theta_pct: float | None
    vega_brl: float | None
    rho: float | None
    iv_pct_252: float | None
    is_liquid: bool
    flags: list[str] = field(default_factory=list)
    model_version: int = 1

    def to_row(self) -> dict[str, object]:
        """`dict` na ordem/valores que o escritor de `store/metrics.py` valida."""
        return {
            "refdate": self.refdate,
            "ticker": self.ticker,
            "maturity": self.maturity,
            "leg_id": self.leg_id,
            "side": self.side,
            "style": self.style,
            "strike": self.strike,
            "t": self.t,
            "mid": self.mid,
            "quote_factor": self.quote_factor,
            "iv": self.iv,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta_brl": self.theta_brl,
            "theta_pct": self.theta_pct,
            "vega_brl": self.vega_brl,
            "rho": self.rho,
            "iv_pct_252": self.iv_pct_252,
            "is_liquid": self.is_liquid,
            "flags": sorted(set(self.flags)),
            "model_version": self.model_version,
        }


def spread_pct(best_bid: int, best_ask: int) -> float | None:
    """Spread em % do mid; `None` quando falta um dos lados (`one_sided`)."""
    if best_bid <= 0 or best_ask <= 0:
        return None
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0.0:
        return None
    return (best_ask - best_bid) / mid * 100.0


def is_liquid_series(
    quote: OptionQuote,
    spread: float | None,
    trades_5d: int,
) -> bool:
    """Regra da §6.9: 5 negócios em 5 sessões, spread ≤ 10, book dos dois lados, negócio hoje."""
    if quote.strike <= 0:
        return False
    if trades_5d < MIN_TRADES_5D:
        return False
    if quote.best_bid <= 0 or quote.best_ask <= 0 or spread is None:
        return False
    if spread > SPREAD_MAX_PCT:
        return False
    return quote.trades > 0


def evaluate_quote(
    quote: OptionQuote,
    *,
    forward: float | None,
    df: float | None,
    lake_last: date,
    dividend_dates: Collection[date],
    scale_event: bool,
    trades_5d: int,
    extra_flags: Sequence[str] = (),
) -> EvaluatedQuote:
    """Calcula preço, IV, gregas e flags de uma série.

    A IV só existe com `t > 0`, curva (`df`) e forward. Cada ausência deixa a
    flag do motivo; o `implied_vol_detail` carrega o motivo da falha da inversão.
    `extra_flags` são as do vencimento (`forward_outlier`) que não se deduzem da
    linha sozinha.
    """
    flags: list[str] = list(extra_flags)
    if quote.strike != quote.strike_raw:
        flags.append(FLAG_LEG_MISMATCH)
    if quote.style is None:
        flags.append(FLAG_STYLE_UNKNOWN)
    if quote.maturity > lake_last:
        flags.append(FLAG_T_ESTIMATED)
    if quote.t <= 0.0:
        flags.append(FLAG_IV_EXPIRED)
    elif df is None:
        flags.append(FLAG_NO_RATES)
    if quote.t > 0.0 and (forward is None or forward <= 0.0):
        flags.append(FLAG_NO_FORWARD)
    if scale_event:
        flags.append(FLAG_SCALE_EVENT)

    spread = spread_pct(quote.best_bid, quote.best_ask)
    if spread is None:
        flags.append(FLAG_ONE_SIDED)
    elif spread > SPREAD_MAX_PCT:
        flags.append(FLAG_WIDE_SPREAD)
    if trades_5d < MIN_TRADES_5D:
        flags.append(FLAG_NO_TRADE_5D)

    iv_pct: float | None = None
    greeks: Greeks | None = None
    if quote.t > 0.0 and df is not None and forward is not None and forward > 0.0:
        sigma, reason = implied_vol_detail(
            quote.price,
            forward,
            float(quote.strike),
            quote.t,
            df,
            is_call=quote.side == "C",
        )
        if sigma is None:
            if reason is not None:
                flags.append(reason)
        else:
            iv_pct = sigma * 100.0
            greeks = greeks_black76(
                "C" if quote.side == "C" else "P",
                forward,
                float(quote.strike),
                quote.t,
                df,
                sigma,
                quote_factor=quote.quote_factor,
            )
            if _american_call_ex_div(quote, greeks, dividend_dates):
                flags.append(FLAG_AMERICAN_CALL_EX_DIV)

    liquid = is_liquid_series(quote, spread, trades_5d)
    return EvaluatedQuote(
        quote=quote,
        price=quote.price,
        spread_pct=spread,
        iv_pct=iv_pct,
        greeks=greeks,
        trades_5d=trades_5d,
        is_liquid=liquid,
        flags=tuple(sorted(set(flags))),
    )


def _american_call_ex_div(
    quote: OptionQuote,
    greeks: Greeks,
    dividend_dates: Collection[date],
) -> bool:
    """Call `A` com Δ > 0,85 e provento entre hoje e o vencimento (spec §5.4/§6.2)."""
    if quote.side != "C" or quote.style != "A" or greeks.delta <= AMERICAN_CALL_DELTA:
        return False
    return any(quote.refdate <= day <= quote.maturity for day in dividend_dates)


def build_strike_smile(
    evaluated: Sequence[EvaluatedQuote],
    forward: float | None,
) -> list[StrikeSmile]:
    """Consolida as séries de **um vencimento** em uma linha por strike (§4.5 passo 6)."""
    by_strike: dict[int, list[EvaluatedQuote]] = defaultdict(list)
    for item in evaluated:
        by_strike[item.quote.strike].append(item)

    points: list[StrikeSmile] = []
    for strike in sorted(by_strike):
        legs = by_strike[strike]
        valid = [item for item in legs if item.usable_for_smile]
        if not valid:
            continue
        iv_pct = sum(item.iv_pct for item in valid) / len(valid)  # type: ignore[arg-type]
        chosen = _otm_leg(valid, strike, forward)
        flags = tuple(sorted({flag for item in legs for flag in item.flags}))
        points.append(
            StrikeSmile(
                strike=strike,
                ticker=chosen.quote.ticker,
                iv_pct=iv_pct,
                delta=chosen.greeks.delta if chosen.greeks is not None else None,
                flags=flags,
            )
        )
    return points


def _otm_leg(
    valid: Sequence[EvaluatedQuote],
    strike: int,
    forward: float | None,
) -> EvaluatedQuote:
    """Perna OTM do strike: call acima de `F`, put abaixo; sem `F`, a call se houver."""
    if forward is None or forward <= 0.0:
        wanted = "C"
    elif strike > forward:
        wanted = "C"
    elif strike < forward:
        wanted = "P"
    else:
        wanted = "C"
    for item in valid:
        if item.quote.side == wanted:
            return item
    return valid[0]


def inconsistent_flags(
    series_flags: dict[str, tuple[str, ...]],
    ticker_to_strike: dict[str, tuple[date, int]],
) -> dict[tuple[date, int], tuple[str, ...]]:
    """`(maturity, strike)` → flags de violação do `check_chain`.

    O `SmilePoint` consolida call+put num ticker só (o da perna OTM); a violação
    é do **strike**, então ela atinge todas as séries daquele strike/vencimento.
    """
    result: dict[tuple[date, int], list[str]] = defaultdict(list)
    for ticker, flags in series_flags.items():
        key = ticker_to_strike.get(ticker)
        if key is None:
            continue
        bucket = result[key]
        for flag in flags:
            if flag not in bucket:
                bucket.append(flag)
    return {key: tuple(value) for key, value in result.items()}


def series_metrics(
    evaluated: EvaluatedQuote,
    *,
    iv_pct_252: float | None,
    model_version: int,
    extra_flags: Sequence[str] = (),
) -> SeriesMetrics:
    """Fecha a linha de `metrics_series` (flats de flags ordenados no `to_row`)."""
    quote = evaluated.quote
    greeks = evaluated.greeks
    return SeriesMetrics(
        refdate=quote.refdate,
        ticker=quote.ticker,
        maturity=quote.maturity,
        leg_id=quote.leg_id,
        side=quote.side,
        style=quote.style,
        strike=quote.strike,
        t=quote.t,
        mid=evaluated.price,
        quote_factor=quote.quote_factor,
        iv=evaluated.iv_pct,
        delta=None if greeks is None else greeks.delta,
        # γ é ∂Δ/∂F: F entra em centavos, então γ sai "por centavo". Multiplicar
        # por 100 converte para variação do delta por ação para cada R$1 de
        # variação de F/forward.
        gamma=None if greeks is None else greeks.gamma * 100.0,
        theta_brl=None if greeks is None else greeks.theta_brl / 100.0,
        theta_pct=None if greeks is None else greeks.theta_pct,
        vega_brl=None if greeks is None else greeks.vega_brl / 100.0,
        rho=None if greeks is None else greeks.rho / 100.0,
        iv_pct_252=iv_pct_252,
        is_liquid=evaluated.is_liquid,
        flags=sorted(set(evaluated.flags) | set(extra_flags)),
        model_version=model_version,
    )


__all__ = [
    "FLAG_AMERICAN_CALL_EX_DIV",
    "FLAG_BFLY_NEGATIVE",
    "FLAG_CALENDAR_VIOLATION",
    "FLAG_FORWARD_OUTLIER",
    "FLAG_INCONSISTENT",
    "FLAG_IV_EXPIRED",
    "FLAG_LEG_MISMATCH",
    "FLAG_MONOTONICITY_VIOLATION",
    "FLAG_NO_FORWARD",
    "FLAG_NO_RATES",
    "FLAG_NO_TRADE_5D",
    "FLAG_ONE_SIDED",
    "FLAG_SCALE_EVENT",
    "FLAG_STYLE_UNKNOWN",
    "FLAG_T_ESTIMATED",
    "FLAG_WIDE_SPREAD",
    "FLAG_ZERO_STRIKE",
    "EvaluatedQuote",
    "OptionQuote",
    "SeriesMetrics",
    "StrikeSmile",
    "build_strike_smile",
    "evaluate_quote",
    "inconsistent_flags",
    "is_liquid_series",
    "series_metrics",
    "spread_pct",
]
