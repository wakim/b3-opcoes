"""Forward implícito por paridade put-call (F1.2, spec §6.2) — PURO.

    C − P = DF·(F − K)

O forward `F` é o único jeito de a IV de Black-76 ficar limpa no mercado brasileiro:
com DI alto e dividendo no meio, usar o spot como `F` joga o erro de juro/dividendo
direto no smile. Este módulo recebe a cadeia já resolvida pelo C1/F1 (`ParityQuote`:
strike e mids em **centavos**, estilo por série) e devolve `F` em centavos.

Regras do contrato (Onda D §1) e o que cada uma protege
-----------------------------------------------------
- **Paridade só é exata entre europeias.** Puts na B3 são todas europeias (medido:
  1.508.144 linhas `E`, zero `A`); calls existem nos dois estilos. Então:
  - par **E/E** (call E + put E) é exato em qualquer strike;
  - **call americana** só entra **OTM** (`K > F₀`, onde o prêmio de exercício
    antecipado é desprezível) **e** sem ex-dividendo no prazo (`call_ex_div=False`);
  - **put 'A'** (não existe no dado de 2026, mas se aparecer) exclui o par;
  - estilo `None` (desconhecido) exclui o par em `style_strict=True` — `None`
    significa "não sei", e "não sei" não entra em conta de preço.
- **Seleção do lado OTM.** `F₀` = spot do dia (passado pelo chamador). Com as duas
  legs europeias a paridade vale em todo strike; a restrição OTM do contrato protege
  as legs que carregam prêmio: call americana ITM e put não-europeia, as duas fora.
- **Cruzamento primeiro**: o strike onde `C(K) = P(K)` (interpolação linear de `C−P`
  em `K` entre os dois strikes que trocam de sinal). Não precisa de taxa nem de DF.
  O cruzamento é tentado só com pares **E/E** e, se nenhum bracket europeu fechar,
  admite-se call americana OTM ("preferir a série europeia"). Um bracket só vale se o
  DF implícito dele for plausível — print velho a 3 ¢ do strike cria "cruzamento" com
  slope de 27 (visto em PETR4 19/11/2026, strike 4344), e esse forward não existe.
- **OLS como reserva**: ajuste de `(P − C)` contra `K` (o slope é o DF, positivo) na
  banda `|K−F₀|/F₀ ≤ 15%`; `F = −intercepto/slope`, exato da paridade `P−C = DF(K−F)`.
  Slope `≤ 0` ou menos de `MIN_OLS_PAIRS` pares → `none` + flag.
- **Nunca se inventa forward**: sem par utilizável, `ForwardResult(None, "none", 0, …)`
  com a flag do motivo (regra 5 do AGENTS).

Sem I/O, sem numpy: só `math`, `collections.abc` e `dataclasses`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

OTM_BAND = 0.15
"""Banda de moneyness da amostra do OLS: `|K − F₀|/F₀ ≤ 15%` (spec §6.2)."""

MIN_OLS_PAIRS = 3
"""Mínimo de pares para o OLS — dois pontos não têm resíduo para denunciar erro."""

CROSS_DF_MIN = 0.5
CROSS_DF_MAX = 1.5
"""Faixa em que o DF implícito de um bracket pode cair para o cruzamento valer.

Paridade é linear em `K` com slope `−DF`. Fechamento largo e print velho criam
"cruzamentos" com slope absurdo (num par real de PETR4 em 19/11/2026, o strike 4344
fechou 45¢ fora da curva e o bracket dava DF ≈ 27). O guarda é largo de propósito:
só mata bracket que não é paridade, não discute a taxa do dia.
"""

METHOD_CROSSING = "crossing"
METHOD_OLS = "ols"
METHOD_NONE = "none"

FLAG_NO_PAIRS = "no_pairs"
FLAG_NO_OTM_CROSSING = "no_otm_crossing"
FLAG_FEW_PAIRS = "ols_few_pairs"
FLAG_NONPOSITIVE_SLOPE = "ols_nonpositive_slope"
FLAG_PUT_NOT_EUROPEAN = "put_not_european"
FLAG_STYLE_UNKNOWN = "style_unknown"
FLAG_AMERICAN_CALL_EX_DIV = "american_call_ex_div"
FLAG_AMERICAN_CALL_ITM = "american_call_itm"
FLAG_INVALID_QUOTE = "invalid_quote"
FLAG_DUPLICATE_STRIKE = "duplicate_strike"
FLAG_CROSSING_INCONSISTENT_SLOPE = "crossing_inconsistent_slope"


@dataclass(frozen=True, slots=True)
class ParityQuote:
    """Um strike com as duas legs da paridade, já em **centavos**.

    O C1/F1 resolve o mid (ou usa o `close` quando o book é `one_sided`) antes de
    chamar: aqui só entra número pronto. `call_style`/`put_style` são `'A'`/`'E'`/`None`
    por série (o estilo NÃO vem do ticker — AGENTS, armadilha 6).
    """

    strike: int
    call_mid: float
    put_mid: float
    call_style: str | None = None
    put_style: str | None = None
    call_ex_div: bool = False


@dataclass(frozen=True, slots=True)
class ForwardResult:
    """Forward de um vencimento, com o método que o produziu e o que ficou de fora."""

    forward: float | None
    """Forward em **centavos**; `None` quando não há estimativa honesta."""

    method: str
    """`"crossing"` | `"ols"` | `"none"`."""

    n_pairs: int
    """Pares que entraram na estimativa escolhida (cruzamento: 2; OLS: a amostra)."""

    ols_slope: float | None
    """Slope do OLS de `(P−C)` em `K` — o DF estimado; `None` fora do OLS."""

    flags: tuple[str, ...]
    """Motivos de exclusão/degradação, ordenados (reprodutíveis); vazio só em sucesso limpo."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    """Par que passou no filtro de estilo/preço, antes do filtro OTM."""

    strike: int
    diff: float
    """`C − P` em centavos."""

    american_call: bool
    """Se a call é americana (só pode entrar OTM)."""


def forward_by_parity(
    chain: Sequence[ParityQuote],
    *,
    spot: float | None = None,
    style_strict: bool = True,
) -> ForwardResult:
    """Estima o forward do vencimento pela paridade, **só com quotes confiáveis**.

    `spot` é o `F₀` que separa OTM de ITM (spec §6.2). Sem `spot`, o `F₀` inicial é o
    strike de `|C−P|` mínimo — o proxy de cruzamento, que existe justamente onde
    `C−P = 0`; é só para escolher o lado, não é a resposta.
    """
    flags: set[str] = set()
    candidates = _candidates(chain, style_strict=style_strict, flags=flags)
    if not candidates:
        return _failure(0, flags | {FLAG_NO_PAIRS})

    f0 = _initial_reference(candidates, spot)
    if f0 is None or f0 <= 0.0:
        return _failure(0, flags | {FLAG_NO_PAIRS})

    european: list[tuple[int, float]] = []
    admitted: list[tuple[int, float]] = []
    for candidate in candidates:
        pair = (candidate.strike, candidate.diff)
        if not candidate.american_call:
            european.append(pair)
            admitted.append(pair)
            continue
        if candidate.strike <= f0:
            flags.add(FLAG_AMERICAN_CALL_ITM)  # prêmio de exercício: ITM americana não entra
            continue
        admitted.append(pair)
    if not admitted:
        return _failure(0, flags | {FLAG_NO_PAIRS})

    # "Preferir a série europeia": o cruzamento tenta E/E primeiro; só se não houver
    # cruzamento entre europeias é que as calls americanas OTM entram (spec §6.2).
    crossing = _crossing_forward(european, f0, flags)
    if crossing is None and len(admitted) > len(european):
        crossing = _crossing_forward(admitted, f0, flags)
    if crossing is not None:
        return ForwardResult(
            forward=crossing,
            method=METHOD_CROSSING,
            n_pairs=2,
            ols_slope=None,
            flags=tuple(sorted(flags)),
        )
    flags.add(FLAG_NO_OTM_CROSSING)

    return _ols_forward(admitted, f0, flags)


def _candidates(
    chain: Sequence[ParityQuote],
    *,
    style_strict: bool,
    flags: set[str],
) -> list[_Candidate]:
    """Pares que passam no filtro de estilo e de preço, ordenados por strike.

    Strike `0` é órfão de ajuste de grade (AGENTS) e preço não positivo é ausência de
    quote: os dois ficam de fora e deixam a flag. Strike repetido fica com o primeiro.
    """
    by_strike: dict[int, _Candidate] = {}
    for quote in chain:
        if quote.strike <= 0:
            flags.add(FLAG_INVALID_QUOTE)
            continue
        if not (_is_price(quote.call_mid) and _is_price(quote.put_mid)):
            flags.add(FLAG_INVALID_QUOTE)
            continue
        if quote.put_style == "A":
            flags.add(FLAG_PUT_NOT_EUROPEAN)
            continue
        if quote.put_style is None and style_strict:
            flags.add(FLAG_STYLE_UNKNOWN)
            continue
        if quote.call_style == "A" and quote.call_ex_div:
            flags.add(FLAG_AMERICAN_CALL_EX_DIV)
            continue
        if quote.call_style is None and style_strict:
            flags.add(FLAG_STYLE_UNKNOWN)
            continue
        if quote.strike in by_strike:
            flags.add(FLAG_DUPLICATE_STRIKE)
            continue
        by_strike[quote.strike] = _Candidate(
            strike=quote.strike,
            diff=quote.call_mid - quote.put_mid,
            american_call=quote.call_style == "A",
        )
    return [by_strike[strike] for strike in sorted(by_strike)]


def _initial_reference(candidates: Sequence[_Candidate], spot: float | None) -> float | None:
    """`F₀` para separar OTM de ITM: o spot do dia, ou o strike de `|C−P|` mínimo."""
    if spot is not None and math.isfinite(spot) and spot > 0.0:
        return float(spot)
    if not candidates:
        return None
    return float(min(candidates, key=lambda item: abs(item.diff)).strike)


def _crossing_forward(
    pairs: Sequence[tuple[int, float]], f0: float, flags: set[str]
) -> float | None:
    """Strike onde `C−P` cruza zero, interpolado linearmente entre os strikes vizinhos.

    A paridade é linear em `K` (`C−P = DF·(F−K)`), então a raiz da reta entre dois
    strikes consecutivos que trocam de sinal é o próprio forward (`F = K*`). Se há
    mais de um cruzamento (cotações ruins), fica o mais próximo de `F₀`.

    Um bracket só entra se o DF implícito dele (`−Δ(C−P)/ΔK`) for plausível
    (`[CROSS_DF_MIN, CROSS_DF_MAX]`): sem esse guarda, um print velho a 3 ¢ do strike
    produz "cruzamento" com slope de 27 e um forward sem sentido.
    """
    best: float | None = None
    best_distance = math.inf
    for (strike_low, diff_low), (strike_high, diff_high) in zip(pairs, pairs[1:], strict=False):
        if diff_low == 0.0:
            crossing = float(strike_low)
        elif diff_high == 0.0:
            crossing = float(strike_high)
        elif diff_low * diff_high > 0.0:
            continue
        else:
            width = strike_high - strike_low
            implied_df = -(diff_high - diff_low) / width
            if not CROSS_DF_MIN <= implied_df <= CROSS_DF_MAX:
                flags.add(FLAG_CROSSING_INCONSISTENT_SLOPE)
                continue
            crossing = strike_low + width * (diff_low / (diff_low - diff_high))
        distance = abs(crossing - f0)
        if distance < best_distance:
            best, best_distance = crossing, distance
    return best


def _ols_forward(
    pairs: Sequence[tuple[int, float]],
    f0: float,
    flags: set[str],
) -> ForwardResult:
    """OLS de `(P−C)` contra `K` na banda OTM; `F = −intercepto/slope`.

    Com `P−C = DF·(K − F)` (paridade reescrita), o ajuste `y = a + b·K` tem `b = DF`
    (positivo, o guarda do contrato) e `a = −DF·F`; logo `F = −a/b` — exato, sem
    depender de escolher `K₀`. É a mesma conta do contrato (`F = K₀ − (C−P)₀/slope`):
    lá o slope é o de `C−P` (negativo); aqui o de `P−C` (positivo), daí o sinal.
    """
    band = [(strike, -diff) for strike, diff in pairs if abs(strike - f0) / f0 <= OTM_BAND]
    if len(band) < MIN_OLS_PAIRS:
        return _failure(len(band), flags | {FLAG_FEW_PAIRS})

    slope, intercept = _least_squares(band)
    if slope <= 0.0:
        return ForwardResult(
            forward=None,
            method=METHOD_NONE,
            n_pairs=len(band),
            ols_slope=slope,
            flags=tuple(sorted(flags | {FLAG_NONPOSITIVE_SLOPE})),
        )

    forward = -intercept / slope
    if not math.isfinite(forward) or forward <= 0.0:
        return ForwardResult(
            forward=None,
            method=METHOD_NONE,
            n_pairs=len(band),
            ols_slope=slope,
            flags=tuple(sorted(flags | {FLAG_NONPOSITIVE_SLOPE})),
        )
    return ForwardResult(
        forward=forward,
        method=METHOD_OLS,
        n_pairs=len(band),
        ols_slope=slope,
        flags=tuple(sorted(flags)),
    )


def _least_squares(sample: Sequence[tuple[int, float]]) -> tuple[float, float]:
    """OLS simples de `y = (P−C)` em `x = K`: devolve `(slope, intercept)`."""
    n = len(sample)
    mean_x = sum(strike for strike, _ in sample) / n
    mean_y = sum(diff for _, diff in sample) / n
    sxx = sum((strike - mean_x) ** 2 for strike, _ in sample)
    sxy = sum((strike - mean_x) * (diff - mean_y) for strike, diff in sample)
    slope = sxy / sxx
    return slope, mean_y - slope * mean_x


def _failure(n_pairs: int, flags: set[str]) -> ForwardResult:
    """Resultado de falha explícita: `None` + método `"none"` + as flags."""
    return ForwardResult(
        forward=None,
        method=METHOD_NONE,
        n_pairs=n_pairs,
        ols_slope=None,
        flags=tuple(sorted(flags)),
    )


def _is_price(value: float | None) -> bool:
    """Preço utilizável: finito e positivo (zero é ausência de quote, não preço)."""
    return value is not None and math.isfinite(value) and value > 0.0
