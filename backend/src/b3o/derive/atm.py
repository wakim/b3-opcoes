"""ATM 30d (F1.5; spec §6.4; contrato Onda E §1 e §5.1–§5.3) — PURO.

Responde "qual é a IV do dinheiro a 30 dias úteis". **Recebe** o smile já pronto
(IV por série calculada pela Onda D e montada pelo F1 CLI) e faz só aritmética de
smile — não refaz IV, não conhece curva, dias úteis nem desconto (`DF` não entra
na variância total; contrato §5.2).

Regras fechadas (contrato §5.2/§5.3):

- **por vencimento:** ATM é a IV em `K = F`, obtida interpolando a **variância
  total** `w = (σ/100)²·T` **linearmente em `x = ln(K/F)`** (spec §6.4), e a IV é
  `100·sqrt(w₀/T)` com `w₀` avaliado em `x = 0`. Interpolar `w` (e não a IV) é o
  que mantém a curva sem arbitragem de calendário; `K == F` exato na grade usa o
  ponto, sem interpolar;
- **entre vencimentos:** `w` linear em `T`, alvo `T_30 = 30/252` exato.
  `F` fora da faixa de strikes → `smile_outside_range` (nunca extrapolar);
- **grade rala:** menos de `MIN_STRIKES` strikes com IV → `sparse_chain`;
- **sem forward:** todos os `atm_iv = None` + `no_forward`;
- **alvo depois do último vencimento válido:** `extrapolated_blocked` +
  `iv_extrapolated`; **antes do primeiro:** `none` + `no_short_expiry`; **nenhum
  válido:** `none` + `no_expiry`.

Três guardas que o contrato não nomeia, para nunca haver exceção nem zero
silencioso (falha explícita, AGENTS.md regra 5):

- `t` não-finito ou `<= 0` no vencimento (vencendo hoje, ou `t` corrompido) →
  `invalid_tenor`;
- `w₀` não-finito (IV não-finita a montante) ou negativo → `invalid_variance`:
  `sqrt` de negativo/`NaN` não pode virar `NaN` com cara de métrica;
- `forward <= 0` é tratado como "sem forward" (`ln` não existe) → `no_forward`.

Pontos duplicados no mesmo strike (call e put europeias do mesmo strike, que por
paridade têm a mesma IV) são consolidados pela **média** das IVs: é a única
escolha simétrica e é o que evita dois `x` idênticos na interpolação. O número de
strikes conta strikes **distintos**.

Unidade de `strike` e de `forward`: **centavos** — os dois na mesma unidade, que
é a do `forward.py` (paridade em centavos). `iv_pct` em **% a.a.**.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

MIN_STRIKES = 3
"""Strikes distintos com IV para o ATM do vencimento existir (contrato §5.2).

Abaixo disso o vencimento não entra na interpolação: `None` + `sparse_chain` —
o corte que o F0.11 mediu no dado real (P-K) aplicado à grade, não uma
estimativa.
"""

TARGET_T_30D = 30 / 252
"""Tenor da métrica do papel: 30 dias úteis em anos (spec §6.4: "avaliar em
`T = 30/252`"; contrato §5.3: "`T_30 = 30/252` exato")."""

FLAG_NO_FORWARD = "no_forward"
"""Sem forward utilizável: o smile não tem onde ser avaliado."""

FLAG_SPARSE_CHAIN = "sparse_chain"
"""Menos de `MIN_STRIKES` strikes com IV no vencimento."""

FLAG_SMILE_OUTSIDE_RANGE = "smile_outside_range"
"""`F` caiu fora da faixa de strikes do vencimento — extrapolar seria inventar."""

FLAG_IV_EXTRAPOLATED = "iv_extrapolated"
"""O alvo de 30d está depois do último vencimento válido (bloqueado, não estimado)."""

FLAG_NO_SHORT_EXPIRY = "no_short_expiry"
"""O papel não tem vencimento no prazo de 30d (grade curta)."""

FLAG_NO_EXPIRY = "no_expiry"
"""Nenhum vencimento com ATM válido — não há o que interpolar."""

FLAG_INVALID_TENOR = "invalid_tenor"
"""`t` não é finito nem positivo no vencimento (vencendo hoje ou `t` corrompido)."""

FLAG_INVALID_VARIANCE = "invalid_variance"
"""`w₀` não é finito nem não-negativo — a interpolação não vira IV (nunca `NaN`)."""

METHOD_INTERPOLATED = "interpolated"
"""`w` interpolado entre dois vencimentos válidos."""

METHOD_EXTRAPOLATED_BLOCKED = "extrapolated_blocked"
"""Alvo além do último vencimento: `iv_atm_30d = None`."""

METHOD_NONE = "none"
"""Nenhum caminho legítimo até 30d."""


@dataclass(frozen=True, slots=True)
class SmilePoint:
    """Um ponto do smile de um vencimento, com IV e Δ já calculados pela Onda F.

    É o **único** tipo de entrada do F1.5 (contrato §5.1, ADR-013): `checks.py`
    (E2) importa este tipo, por isso ele mora aqui e não em módulo próprio.
    """

    ticker: str
    """Ticker da série (`PETRK410`), só para rastreio e desempate determinístico."""

    maturity: date
    """Vencimento — é a chave de agrupamento do smile."""

    strike: int
    """Strike do dia, em **centavos** (mesma unidade do forward da paridade)."""

    t: float
    """Anos úteis até o vencimento (`du/252`), calculado por `conventions.py`."""

    iv_pct: float | None
    """IV em **% a.a.**; `None` = fora do smile (inconsistente, não invertida)."""

    delta: float | None
    """Δ por ação com o sinal da ponta (call > 0, put < 0); `None` sem IV."""

    flags: tuple[str, ...] = ()
    """Flags da série vindas do pipeline (`wide_spread`, `american_call_ex_div`, …)."""


@dataclass(frozen=True, slots=True)
class ExpiryAtm:
    """ATM de um vencimento: IV em `K = F` por interpolação de variância total."""

    maturity: date
    """Vencimento."""

    t: float
    """Anos úteis (`du/252`) do vencimento — o mesmo `t` dos pontos."""

    atm_iv: float | None
    """IV em `K = F`, em **% a.a.**; `None` quando não há ATM honesto (ver `flags`)."""

    n_strikes: int
    """Strikes **distintos** com IV usados no smile (0 quando não há nenhum)."""

    flags: tuple[str, ...]
    """`sparse_chain`, `smile_outside_range`, `no_forward`, `invalid_tenor`,
    `negative_variance` — vazio quando o ATM saiu da interpolação."""


@dataclass(frozen=True, slots=True)
class Atm30d:
    """ATM a 30 dias úteis — a métrica do papel (`core.metrics_underlying`)."""

    iv_atm_30d: float | None
    """IV ATM a 30 dias úteis, em **% a.a.**; `None` quando `method != "interpolated"`."""

    method: str
    """`"interpolated"`, `"extrapolated_blocked"` ou `"none"`."""

    front: ExpiryAtm | None
    """Vencimento de `t` imediatamente ≤ 30/252 usado (ou o último, quando bloqueado)."""

    back: ExpiryAtm | None
    """Vencimento de `t` imediatamente ≥ 30/252 usado (ou o primeiro, quando curto)."""

    flags: tuple[str, ...]
    """Flags do resultado; `iv_extrapolated` é a que o pipeline grava na série."""


def atm_by_expiry(chain: Sequence[SmilePoint], forward: float | None) -> list[ExpiryAtm]:
    """ATM de cada vencimento presente em `chain`, em ordem crescente de vencimento.

    Não muta a entrada e não depende de ordem de entrada: o resultado é ordenado
    por `maturity` e o desempate interno é por `(strike, ticker)`.
    """
    groups: dict[date, list[SmilePoint]] = {}
    for point in chain:
        groups.setdefault(point.maturity, []).append(point)

    expiries: list[ExpiryAtm] = []
    for maturity in sorted(groups):
        points = sorted(groups[maturity], key=lambda p: (p.strike, p.ticker))
        t = points[0].t
        strike_iv = _strike_iv(points)
        flags: list[str] = []
        atm_iv: float | None = None
        if forward is None or forward <= 0.0:
            flags.append(FLAG_NO_FORWARD)
        elif len(strike_iv) < MIN_STRIKES:
            flags.append(FLAG_SPARSE_CHAIN)
        elif not math.isfinite(t) or t <= 0.0:
            flags.append(FLAG_INVALID_TENOR)
        else:
            w0 = _variance_at_zero(strike_iv, forward, t)
            if w0 is None:
                flags.append(FLAG_SMILE_OUTSIDE_RANGE)
            elif not math.isfinite(w0) or w0 < 0.0:
                flags.append(FLAG_INVALID_VARIANCE)
            else:
                atm_iv = 100.0 * math.sqrt(w0 / t)
        expiries.append(ExpiryAtm(maturity, t, atm_iv, len(strike_iv), tuple(flags)))
    return expiries


def atm_interpolate_30d(expiries: Sequence[ExpiryAtm]) -> Atm30d:
    """`w` linear em `t` entre os vencimentos válidos, avaliado em `T = 30/252`.

    `front` = maior `t <= T_30`, `back` = menor `t >= T_30`. Um vencimento
    exatamente em `T_30` é as duas pontas e devolve a própria IV (o `span` é
    zero e a interpolação degenera no ponto). Nunca extrapola: alvo depois do
    último vencimento é `extrapolated_blocked` (spec §6.4).
    """
    # Só vencimentos com ATM: `(t, maturity, iv)` — ordenados por `t`, desempate por data.
    valid: list[tuple[float, date, float, ExpiryAtm]] = []
    for expiry in expiries:
        if expiry.atm_iv is not None:
            valid.append((expiry.t, expiry.maturity, expiry.atm_iv, expiry))
    valid.sort(key=lambda item: (item[0], item[1]))
    if not valid:
        return Atm30d(None, METHOD_NONE, None, None, (FLAG_NO_EXPIRY,))

    front_point: tuple[float, date, float, ExpiryAtm] | None = None
    for item in valid:
        if item[0] <= TARGET_T_30D:
            front_point = item
    back_point = next((item for item in valid if item[0] >= TARGET_T_30D), None)

    if back_point is None:
        # Alvo além do último vencimento: bloquear, nunca extrapolar (spec §6.4).
        front = front_point[3] if front_point is not None else None
        return Atm30d(None, METHOD_EXTRAPOLATED_BLOCKED, front, None, (FLAG_IV_EXTRAPOLATED,))
    if front_point is None:
        # Alvo antes do primeiro vencimento: o papel não tem grade até 30d.
        return Atm30d(None, METHOD_NONE, None, back_point[3], (FLAG_NO_SHORT_EXPIRY,))

    # `atm_iv` não-None garante `t > 0` e `w >= 0` nas duas pontas (guardas acima),
    # então `w` interpolado é convexo entre dois não-negativos: sem `sqrt` de negativo.
    w_front = (front_point[2] / 100.0) ** 2 * front_point[0]
    w_back = (back_point[2] / 100.0) ** 2 * back_point[0]
    span = back_point[0] - front_point[0]
    if span <= 0.0:
        w = w_front  # as duas pontas são o próprio vencimento de 30d
    else:
        w = w_front + (w_back - w_front) * ((TARGET_T_30D - front_point[0]) / span)
    iv_atm_30d = 100.0 * math.sqrt(w / TARGET_T_30D)
    flags = _merge_flags(front_point[3].flags, back_point[3].flags)
    return Atm30d(iv_atm_30d, METHOD_INTERPOLATED, front_point[3], back_point[3], flags)


def _strike_iv(points: Sequence[SmilePoint]) -> list[tuple[float, float]]:
    """`(strike, iv)` por strike distinto, ordenado — média das IVs no mesmo strike."""
    totals: dict[int, float] = {}
    counts: dict[int, int] = {}
    for point in points:
        if point.iv_pct is None or point.strike <= 0:
            continue
        totals[point.strike] = totals.get(point.strike, 0.0) + point.iv_pct
        counts[point.strike] = counts.get(point.strike, 0) + 1
    return [(float(strike), totals[strike] / counts[strike]) for strike in sorted(totals)]


def _variance_at_zero(
    strike_iv: Sequence[tuple[float, float]], forward: float, t: float
) -> float | None:
    """`w₀` em `x = ln(K/F) = 0` por interpolação linear de `w` em `x`.

    `None` quando `F` está fora da faixa de strikes — o contrato proíbe
    extrapolar (`smile_outside_range`). `K == F` na grade devolve `w` daquele
    ponto, sem interpolar.
    """
    xs = [math.log(strike / forward) for strike, _ in strike_iv]
    ws = [(iv / 100.0) ** 2 * t for _, iv in strike_iv]
    if xs[0] > 0.0 or xs[-1] < 0.0:
        return None
    for i in range(len(xs) - 1):
        if xs[i] <= 0.0 <= xs[i + 1]:
            span = xs[i + 1] - xs[i]
            if span <= 0.0:  # strikes distintos ⇒ impossível; guarda de borda
                return ws[i]
            return ws[i] + (ws[i + 1] - ws[i]) * ((0.0 - xs[i]) / span)
    return ws[-1]  # x = 0 é exatamente o último ponto (xs[-1] == 0)


def _merge_flags(*groups: Sequence[str]) -> tuple[str, ...]:
    """União preservando a ordem de aparição (determinístico, sem `set`)."""
    merged: list[str] = []
    for group in groups:
        for flag in group:
            if flag not in merged:
                merged.append(flag)
    return tuple(merged)
