"""Expected move e dividendo implícito (F1.9; spec §6.10; contrato §1 e §5.6).

Duas leituras diretas do smile do 30d, sem modelo:

- `expected_move_1s = 1,25 × straddle_mid / spot` — aproximação do desvio-padrão
  de 1σ do preço pelo straddle ATM. A constante **1,25** é a da spec §6.10
  (`exp_move_1σ ≈ 1,25 × straddle/spot`); ela existe porque o valor esperado do
  módulo do retorno sob normal (`√(2/π) ≈ 0,7979`) é menor que σ, então o
  straddle sozinho subestima 1σ — `1/0,7979 ≈ 1,2533`, arredondado para 1,25 na
  spec. Devolve **fração** (0,0625 = 6,25%); quem serializa multiplica por 100 e
  o campo leva a unidade no nome (contrato §5.6).
- `implied_div_yield = r − ln(F/S)/T`, em **% a.a.** — o carry entre spot e
  forward: o que sobra do `ln(F/S)/T` depois do juro é o dividendo (ou o custo de
  empréstimo) implícito no forward da paridade (spec §6.2/§6.10). Mesma unidade do
  `rate_annual` (% a.a.), então a subtração é direta — não se divide `r` por 100.

Guardas (falha explícita, nunca zero silencioso): preço ≤ 0 ou `t <= 0` → `None`.
Spot/forward em qualquer unidade **desde que os dois iguais** (o `ln(F/S)` é
razão; centavos é o que o `forward.py` devolve).
"""

from __future__ import annotations

import math

EXP_MOVE_FACTOR = 1.25
"""Constante da spec §6.10: `exp_move_1σ ≈ 1,25 × straddle/spot`."""


def expected_move_1s(straddle_mid: float, spot: float) -> float | None:
    """Movimento esperado de 1σ, em **fração** do spot (0,0625 = 6,25%).

    `None` quando o spot não é positivo (não há razão) ou o straddle é negativo
    (mid impossível, dado corrompido) — não se devolve 0 para esconder o problema.
    """
    if spot <= 0.0 or straddle_mid < 0.0:
        return None
    return EXP_MOVE_FACTOR * straddle_mid / spot


def implied_div_yield(r_pct: float, forward: float, spot: float, t: float) -> float | None:
    """Dividendo (ou custo de empréstimo) implícito, em **% a.a.**

    `r_pct − 100·ln(F/S)/T` — spec §6.10, com `r_pct` já em % a.a. e `T` em anos
    úteis (`du/252`). Assume dividendo contínuo (a forma da paridade com `F`).

    `None` quando `t <= 0` (sem prazo não há taxa anualizada), `forward <= 0` ou
    `spot <= 0` (`ln` não existe).
    """
    if t <= 0.0 or forward <= 0.0 or spot <= 0.0:
        return None
    return r_pct - 100.0 * math.log(forward / spot) / t
