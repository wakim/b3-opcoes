"""Derivados puros de uma posição (F6, spec §14): payoff, gregas e alertas."""

from __future__ import annotations

import datetime
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from b3o.derive.structure import LegRow, PayoffPoint, StructureLeg, evaluate

if TYPE_CHECKING:
    from b3o.journal import PositionLegRecord


@dataclass(frozen=True, slots=True)
class PositionGreeks:
    """Gregas líquidas; `complete=False` quando alguma opção não tem marca."""

    delta: float
    delta_exposure_brl: float
    gamma: float
    theta_brl: float
    vega_brl: float
    complete: bool
    flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Leg:
    ticker: str
    side: str
    type: str
    qty: int
    entry_price_brl: float
    strike_brl: float | None
    maturity: date | None


def derive_kind(
    legs: Sequence[StructureLeg | LegRow | PositionLegRecord],
) -> str:
    """Classifica a estrutura pelas pernas; combinação não prevista vira `custom`."""
    resolved = tuple(_normalize(leg) for leg in legs)
    if len(resolved) == 2:
        first, second = resolved
        if (
            first.type == "stock"
            and first.side == "long"
            and second.type == "call"
            and second.side == "short"
            and first.qty == second.qty
        ):
            return "covered"
        if (
            second.type == "stock"
            and second.side == "long"
            and first.type == "call"
            and first.side == "short"
            and first.qty == second.qty
        ):
            return "covered"
        calls = [leg for leg in resolved if leg.type == "call"]
        puts = [leg for leg in resolved if leg.type == "put"]
        same_quantity = len({leg.qty for leg in resolved}) == 1
        if len(calls) == 2 and same_quantity:
            long_call = next((leg for leg in calls if leg.side == "long"), None)
            short_call = next((leg for leg in calls if leg.side == "short"), None)
            if (
                long_call is not None
                and short_call is not None
                and long_call.strike_brl is not None
                and short_call.strike_brl is not None
                and long_call.strike_brl < short_call.strike_brl
            ):
                return "bull_call_spread"
        if len(puts) == 2 and same_quantity:
            long_put = next((leg for leg in puts if leg.side == "long"), None)
            short_put = next((leg for leg in puts if leg.side == "short"), None)
            if (
                long_put is not None
                and short_put is not None
                and long_put.strike_brl is not None
                and short_put.strike_brl is not None
                and long_put.strike_brl > short_put.strike_brl
            ):
                return "bear_put_spread"
        if (
            len(calls) == len(puts) == 1
            and same_quantity
            and calls[0].side == puts[0].side == "long"
        ):
            call_strike = calls[0].strike_brl
            put_strike = puts[0].strike_brl
            if call_strike == put_strike:
                return "straddle"
            return "strangle"
    if len(resolved) == 1 and resolved[0].type == "put" and resolved[0].side == "short":
        return "put_caixa"
    return "custom"


def payoff_pnl(
    legs: Sequence[StructureLeg | LegRow | PositionLegRecord],
    spot: float,
) -> float:
    """P&L no vencimento: `Σ sinal × qty × (terminal − entrada)`."""
    spot_brl = _nonnegative(spot, "spot")
    total = 0.0
    for leg in legs:
        resolved = _normalize(leg)
        terminal = _terminal_value(resolved, spot_brl)
        total += _sign(resolved.side) * resolved.qty * (terminal - resolved.entry_price_brl)
    return total


def realized_pnl(
    legs: Sequence[StructureLeg | LegRow | PositionLegRecord],
    exit_prices: Mapping[str, float],
) -> float:
    """P&L realizado com um preço de saída por ticker; ticker ausente é erro."""
    if not isinstance(exit_prices, Mapping):
        raise ValueError("exit_prices precisa ser um mapeamento ticker → preço")
    total = 0.0
    for leg in legs:
        resolved = _normalize(leg)
        if resolved.ticker not in exit_prices:
            raise ValueError(f"preço de saída ausente para {resolved.ticker}")
        exit_price = _nonnegative(exit_prices[resolved.ticker], f"exit_prices[{resolved.ticker!r}]")
        if exit_price <= 0.0:
            raise ValueError(f"preço de saída zero para {resolved.ticker}")
        total += _sign(resolved.side) * resolved.qty * (exit_price - resolved.entry_price_brl)
    return total


def marked_pnl(
    legs: Sequence[StructureLeg | LegRow | PositionLegRecord],
    marks: Mapping[str, object],
) -> float:
    """P&L com marcas por `leg_id`; zero/ausência não é preço de saída."""
    if not isinstance(marks, Mapping):
        raise ValueError("marks precisa ser um mapeamento leg_id → marca")
    total = 0.0
    for leg in legs:
        resolved = _normalize(leg)
        mark = marks.get(getattr(leg, "id", resolved.ticker))
        price = _mark_number(mark, "mark_brl")
        if price is None or price <= 0.0:
            raise ValueError(f"marca ausente ou zero para {resolved.ticker}")
        total += _sign(resolved.side) * resolved.qty * (price - resolved.entry_price_brl)
    return total


def payoff_snapshot(
    legs: Sequence[StructureLeg | LegRow | PositionLegRecord],
    marks: Mapping[str, object],
    spot: float,
) -> tuple[list[float], list[PayoffPoint]]:
    """Breakevens/payoff com o strike vigente da marca, preservando o preço de entrada."""
    spot_brl = _nonnegative(spot, "spot")
    if spot_brl <= 0.0:
        raise ValueError("spot precisa ser positivo para o payoff")
    if not isinstance(marks, Mapping):
        raise ValueError("marks precisa ser um mapeamento leg_id → marca")
    resolved: list[StructureLeg] = []
    maturities: list[date] = []
    for leg in legs:
        item = _normalize(leg)
        mark = marks.get(getattr(leg, "id", item.ticker))
        strike = None if item.type == "stock" else _mark_number(mark, "strike_brl")
        if item.type != "stock" and (strike is None or strike <= 0.0):
            raise ValueError(f"strike vigente ausente para {item.ticker}")
        if item.maturity is not None:
            maturities.append(item.maturity)
        resolved.append(
            StructureLeg(
                kind="stock" if item.type == "stock" else "option",
                ticker=item.ticker,
                side=item.side,
                quantity=item.qty,
                price_brl=item.entry_price_brl,
                price_low_brl=None,
                price_high_brl=None,
                is_liquid=False,
                option_side=None if item.type == "stock" else "C" if item.type == "call" else "P",
                style=None,
                strike_brl=strike,
                iv_pct=_mark_number(mark, "iv_pct"),
                delta=_mark_number(mark, "delta"),
                gamma=_mark_number(mark, "gamma"),
                theta_brl=_mark_number(mark, "theta_brl"),
                vega_brl=_mark_number(mark, "vega_brl"),
                maturity=item.maturity,
            )
        )
    result = evaluate(
        resolved,
        spot_brl=spot_brl,
        forward_brl=None,
        df=None,
        t=None,
        maturity=min(maturities) if maturities else None,
        scenarios=False,
    )
    return result.breakeven_brl, result.payoff


def aggregate_greeks(
    legs: Sequence[StructureLeg | LegRow | PositionLegRecord],
    marks: Mapping[str, object],
    spot: float,
) -> PositionGreeks:
    """Soma gregas das marcas; ausência vira `complete=False` + flag explícita."""
    spot_brl = _nonnegative(spot, "spot")
    if not isinstance(marks, Mapping):
        raise ValueError("marks precisa ser um mapeamento ticker → marca")
    delta = gamma = theta = vega = 0.0
    complete = True
    for leg in legs:
        resolved = _normalize(leg)
        sign_qty = _sign(resolved.side) * resolved.qty
        if resolved.type == "stock":
            delta += sign_qty
            continue
        mark = marks.get(getattr(leg, "id", resolved.ticker))
        values = {
            name: _mark_number(mark, name) for name in ("delta", "gamma", "theta_brl", "vega_brl")
        }
        if any(value is None for value in values.values()):
            complete = False
        delta += sign_qty * (values["delta"] or 0.0)
        gamma += sign_qty * (values["gamma"] or 0.0)
        theta += sign_qty * (values["theta_brl"] or 0.0)
        vega += sign_qty * (values["vega_brl"] or 0.0)
    return PositionGreeks(
        delta=delta,
        delta_exposure_brl=delta * spot_brl,
        gamma=gamma,
        theta_brl=theta,
        vega_brl=vega,
        complete=complete,
        flags=() if complete else ("gregas_parciais",),
    )


def position_flags(
    days_to_expiry: int | None,
    deltas_now: Sequence[float | None],
    deltas_entry: Sequence[float | None],
    event_in_window: bool,
    thesis_failed: bool,
    capital_over: bool,
) -> tuple[str, ...]:
    """Aplica os seis alertas determinísticos da spec §14.3, na ordem da tabela."""
    if days_to_expiry is not None and (
        isinstance(days_to_expiry, bool) or not isinstance(days_to_expiry, int)
    ):
        raise ValueError("days_to_expiry precisa ser inteiro ou None")
    if len(deltas_now) != len(deltas_entry):
        raise ValueError("deltas_now e deltas_entry precisam ter o mesmo comprimento")
    flags: list[str] = []
    if days_to_expiry is not None and days_to_expiry <= 5:
        flags.append("near_expiry")
    now_values = tuple(_optional_delta(value, "deltas_now") for value in deltas_now)
    entry_values = tuple(_optional_delta(value, "deltas_entry") for value in deltas_entry)
    if any(value is not None and abs(value) > 0.85 for value in now_values):
        flags.append("itm")
    if any(
        now is not None and entry is not None and abs(now - entry) > 0.25
        for now, entry in zip(now_values, entry_values, strict=True)
    ):
        flags.append("delta_drift")
    if event_in_window:
        flags.append("event_in_window")
    if thesis_failed:
        flags.append("thesis_failed")
    if capital_over:
        flags.append("capital_over")
    return tuple(flags)


def _normalize(leg: StructureLeg | LegRow | PositionLegRecord) -> _Leg:
    """Adapta os três contratos de perna ao mesmo conjunto de campos."""
    if isinstance(leg, StructureLeg | LegRow):
        kind = leg.kind
        option_side = leg.option_side
        qty = leg.quantity
        price = leg.price_brl
        strike = leg.strike_brl
        leg_type = (
            "stock" if kind == "stock" else {"C": "call", "P": "put"}.get(str(option_side), "")
        )
        type_name = "kind"
    else:
        kind = leg.type
        option_side = None
        qty = leg.qty
        price = leg.entry_price_brl
        strike = leg.entry_strike_brl
        leg_type = leg.type
        type_name = "type"
    side = _choice(leg.side, {"long", "short"}, "side")
    if leg_type not in {"stock", "call", "put"}:
        raise ValueError(f"{type_name} inválido na perna: {leg_type!r}")
    if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
        raise ValueError(f"qty inválido na perna {leg.ticker!r}: {qty!r}")
    entry_price = _nonnegative(price, f"entry_price_brl de {leg.ticker}")
    if leg_type != "stock":
        strike = _nonnegative(strike, f"strike_brl de {leg.ticker}")
        if strike == 0.0:
            raise ValueError(f"strike_brl não positivo em {leg.ticker}")
    maturity = getattr(leg, "maturity", None)
    if maturity is not None and (
        not isinstance(maturity, date) or isinstance(maturity, datetime.datetime)
    ):
        raise ValueError(f"maturity inválido na perna {leg.ticker!r}: {maturity!r}")
    return _Leg(
        ticker=_ticker(leg.ticker),
        side=side,
        type=leg_type,
        qty=qty,
        entry_price_brl=entry_price,
        strike_brl=strike if leg_type != "stock" else None,
        maturity=maturity,
    )


def _terminal_value(leg: _Leg, spot_brl: float) -> float:
    if leg.type == "stock":
        return spot_brl
    assert leg.strike_brl is not None
    if leg.type == "call":
        return max(spot_brl - leg.strike_brl, 0.0)
    return max(leg.strike_brl - spot_brl, 0.0)


def _sign(side: str) -> float:
    return 1.0 if side == "long" else -1.0


def _mark_number(mark: object, name: str) -> float | None:
    if mark is None:
        return None
    value = mark.get(name) if isinstance(mark, Mapping) else getattr(mark, name, None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _optional_delta(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} precisa conter números ou None")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} precisa conter números finitos")
    return number


def _nonnegative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} precisa ser número")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} inválido: {value!r}")
    return number


def _choice(value: object, choices: set[str], name: str) -> str:
    if not isinstance(value, str) or value.strip().lower() not in choices:
        raise ValueError(f"{name} inválido: {value!r}")
    return value.strip().lower()


def _ticker(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ticker precisa ser texto não vazio")
    return value.strip().upper()
