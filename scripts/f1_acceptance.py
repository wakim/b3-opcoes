"""Aceitação do F1 no dado real — critérios 3, 4, 5 e 6 do PLAN.

Roda dentro do container (lê `/data`):

    podman compose run --rm test python /app/scripts/f1_acceptance.py

Não há suíte de testes (ADR-019): este script **é** a medição, e o resultado vai
para o relatório da sessão. Critérios:
3. forward OTM-only vs ITM-only ≤ 0,5% do spot
4. r ± 100 bp → max|ΔIV| < 0,25
5. ATM 30d sem salto > 2 vol points em dia de rolagem
6. RR25 da PETR4 negativo em ≥ 80% dos dias
"""

import datetime as dt
import math
import statistics
from pathlib import Path

import duckdb

from b3o.derive.forward import ParityQuote, forward_by_parity
from b3o.derive.iv import implied_vol_detail
from b3o.query.service import QueryService

REF = dt.date(2026, 11, 19)
service = QueryService(Path("/data"))
con = duckdb.connect()

# ---------------------------------------------------------------- 6. RR25 PETR4
rows = con.execute(
    """
    SELECT count(*) FILTER (WHERE rr25 IS NOT NULL) com_rr,
           count(*) FILTER (WHERE rr25 < 0) negativos,
           count(*) total
    FROM read_parquet('/data/metrics_underlying/year=2026/part-0.parquet')
    WHERE underlying='PETR4'
    """
).fetchone()
print(f"6) PETR4 RR25: {rows[1]}/{rows[0]} negativos = {100 * rows[1] / max(rows[0], 1):.1f}% "
      f"(dias com rr25 = {rows[0]} de {rows[2]})")

# ------------------------------------------------------- 5. salto do ATM 30d
series = con.execute(
    """
    SELECT refdate, iv_atm_30d FROM read_parquet('/data/metrics_underlying/year=2026/part-0.parquet')
    WHERE underlying='PETR4' AND iv_atm_30d IS NOT NULL ORDER BY refdate
    """
).fetchall()
jumps = []
for (d0, v0), (d1, v1) in zip(series, series[1:]):
    jumps.append((abs(v1 - v0), d1))
jumps.sort(reverse=True)
over = [j for j in jumps if j[0] > 2.0]
print(f"5) PETR4 ATM 30d: {len(series)} dias, {len(over)} saltos > 2 vol points; "
      f"maiores: {[(round(j[0], 2), str(j[1])) for j in jumps[:4]]}")

# ------------------------------------------- 3. forward OTM-only vs ITM-only
options = service.options_on(REF)
curve = [(node.du, node.rate_annual) for node in service.rates_curve(REF)]
spot = next(
    (o.spot_close for o in options if o.underlying == "PETR4"), None
)
pairs: dict[dt.date, dict[int, dict]] = {}
for o in options:
    if o.underlying != "PETR4" or o.strike <= 0:
        continue
    pairs.setdefault(o.maturity, {}).setdefault(o.strike, {})[o.side] = o

print(f"3) forward PETR4 19/11 (spot {spot} ¢), por vencimento:")
for maturity, by_strike in sorted(pairs.items())[:4]:
    chain = [
        ParityQuote(
            strike=s,
            call_mid=v["C"].close / v["C"].quote_factor,
            put_mid=v["P"].close / v["P"].quote_factor,
            call_style=v["C"].style,
            put_style=v["P"].style,
        )
        for s, v in sorted(by_strike.items())
        if "C" in v and "P" in v
    ]
    full = forward_by_parity(chain, spot=float(spot))
    otm = forward_by_parity(
        [q for q in chain if q.strike >= spot], spot=float(spot)
    )
    itm = forward_by_parity(
        [q for q in chain if q.strike <= spot], spot=float(spot)
    )
    if full.forward and otm.forward and itm.forward:
        diff = abs(otm.forward - itm.forward) / spot * 100
        print(
            f"   {maturity}: full {full.forward:.2f} ({full.method}) | "
            f"OTM {otm.forward:.2f} | ITM {itm.forward:.2f} | Δ {diff:.3f}% do spot"
        )
    else:
        print(f"   {maturity}: full {full.forward} OTM {otm.forward} ITM {itm.forward}")

# ------------------------------------------- 4. sensibilidade a r ± 100 bp
def df_at(du: int, shift: float) -> float:
    nodes = [(d, r_ + shift) for d, r_ in curve]
    sorted_nodes = sorted(nodes)
    if du <= sorted_nodes[0][0]:
        log_df = -(sorted_nodes[0][1] / 100.0) * (du / 252.0)
    else:
        position = 0
        for i, (d, _r) in enumerate(sorted_nodes):
            if d <= du:
                position = i
        if position == len(sorted_nodes) - 1:
            log_df = -(sorted_nodes[position][1] / 100.0) * (du / 252.0)
        else:
            d0, r0 = sorted_nodes[position]
            d1, r1 = sorted_nodes[position + 1]
            w = (du - d0) / (d1 - d0)
            log_df = -(((r0 / 100) * (d0 / 252)) * (1 - w) + ((r1 / 100) * (d1 / 252)) * w)
    return math.exp(log_df)


du = 20
ivs = []
for o in options:
    if o.underlying != "PETR4" or o.strike <= 0 or o.maturity != dt.date(2026, 12, 18):
        continue
    price = o.close / o.quote_factor
    if price <= 0:
        continue
    t = du / 252.0
    base, _ = implied_vol_detail(price, 4316.93, o.strike, t, df_at(du, 0.0), is_call=o.side == "C")
    plus, _ = implied_vol_detail(price, 4316.93, o.strike, t, df_at(du, 1.0), is_call=o.side == "C")
    minus, _ = implied_vol_detail(price, 4316.93, o.strike, t, df_at(du, -1.0), is_call=o.side == "C")
    if None not in (base, plus, minus):
        ivs.append(max(abs(plus - base), abs(minus - base)) * 100)
print(f"4) r ±100 bp: n={len(ivs)}, max|ΔIV| = {max(ivs):.3f} vol points" if ivs else "4) sem amostra")
