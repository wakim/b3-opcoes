"""Skew a 25Δ (F1.8; spec §6.8; contrato §1 e §5.5) — PURO: Δ entra, vol sai.

Duas métricas do smile, na mesma data e vencimento:

- `RR25 = iv(25Δ call) − iv(25Δ put)` — o "risk reversal": preço relativo da
  cauda de baixa. Call tem Δ > 0 e put Δ < 0, então RR negativo = puts mais
  caros que calls (skew de proteção, o normal em ações).
- `BFLY25 = ½·(iv25c + iv25p) − iv_atm` — a "butterfly": convexidade/custo das
  asas contra o dinheiro.

Regras (contrato §5.5):

- banda `|Δ| ∈ [0,20; 0,80]` — fora dela o Δ da cadeia da B3 é ruído de IV
  invertida em série ilíquida, não informação;
- **mínimo 5 pontos na banda**, senão `rr25` e `bfly25` são `None` +
  `sparse_chain` (spec §6.8);
- 25Δ sai de **interpolação linear em Δ** entre vizinhos daquele lado; sem
  bracket naquele lado (nenhum ponto em volta de 0,25) → `rr25 = None` +
  `sparse_chain` — não se inventa 25Δ extrapolando a asa;
- `bfly25` precisa do ATM do mesmo vencimento; sem ATM → `bfly25 = None` +
  `no_atm_iv` (o `rr25` continua valendo, ele não usa o ATM).

**Lado pela ponta, magnitude pela banda.** O tipo `DeltaPoint` carrega o Δ
**assinado** (call > 0, put < 0): é o sinal que diz de que lado da cadeia o ponto
está, e é o que permite juntar calls e puts na mesma lista. A banda e a
interpolação usam `|Δ|`. Uma entrada só com `|Δ|` (todos positivos) não permite
separar as pontas por construção — cai em "sem bracket no lado put", que é a
falha explícita certa, não uma média silenciosa de calls com puts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

BAND_LOW = 0.20
"""Limite inferior da banda de Δ em valor absoluto (20Δ) — contrato §5.5."""

BAND_HIGH = 0.80
"""Limite superior da banda de Δ em valor absoluto (80Δ) — contrato §5.5."""

MIN_STRIKES_BAND = 5
"""Pontos mínimos na banda para o skew existir (spec §6.8), senão `sparse_chain`."""

TARGET_DELTA_25 = 0.25
"""O Δ interpolado: 25Δ em valor absoluto (0,25), contrato §5.5."""

FLAG_SPARSE_CHAIN = "sparse_chain"
"""Grade insuficiente na banda ou sem bracket de 25Δ naquele lado."""

FLAG_NO_ATM_IV = "no_atm_iv"
"""Sem ATM do vencimento: `bfly25` fica `None` (o `rr25` não depende dele)."""


@dataclass(frozen=True, slots=True)
class DeltaPoint:
    """Um ponto da cadeia já com Δ calculado pela Onda F (`greeks.py`)."""

    delta: float
    """Δ por ação **com o sinal da ponta**: call > 0, put < 0. A banda usa `|delta|`."""

    iv_pct: float
    """IV do ponto em **% a.a.** (só pontos com IV entram: inconsistente já saiu)."""


@dataclass(frozen=True, slots=True)
class SkewMetrics:
    """Skew do vencimento: risk reversal e butterfly a 25Δ."""

    rr25: float | None
    """`iv(25Δ call) − iv(25Δ put)` em **pontos de vol**; `None` sem bracket/banda."""

    bfly25: float | None
    """`½·(iv25c + iv25p) − iv_atm` em **pontos de vol**; `None` sem as pontas ou ATM."""

    n_strikes_band: int
    """Pontos com `|Δ|` em `[0,20; 0,80]` (calls e puts contam cada um o seu)."""

    flags: tuple[str, ...]
    """`sparse_chain` e/ou `no_atm_iv`; vazio quando as duas métricas saíram."""


def rr25_bfly25(chain: Sequence[DeltaPoint], atm_iv: float | None) -> SkewMetrics:
    """`RR25` e `BFLY25` da cadeia de um vencimento, com o ATM da mesma data.

    `atm_iv` é aceito `None` (o pipeline não teve ATM): `bfly25` vira `None` com
    `no_atm_iv`, `rr25` sai normalmente. Não muta `chain`.
    """
    band = [point for point in chain if BAND_LOW <= abs(point.delta) <= BAND_HIGH]
    n_band = len(band)
    if n_band < MIN_STRIKES_BAND:
        return SkewMetrics(None, None, n_band, (FLAG_SPARSE_CHAIN,))

    # Sinal separa a ponta; |Δ| é a coordenada da interpolação (cresce com K na call).
    calls = sorted((abs(point.delta), point.iv_pct) for point in band if point.delta > 0.0)
    puts = sorted((abs(point.delta), point.iv_pct) for point in band if point.delta < 0.0)
    iv_call_25 = _iv_at_delta(calls, TARGET_DELTA_25)
    iv_put_25 = _iv_at_delta(puts, TARGET_DELTA_25)
    if iv_call_25 is None or iv_put_25 is None:
        # Sem 25Δ de um dos lados, RR e BFLY não existem: `None`, nunca extrapolar.
        return SkewMetrics(None, None, n_band, (FLAG_SPARSE_CHAIN,))

    rr25 = iv_call_25 - iv_put_25
    if atm_iv is None:
        return SkewMetrics(rr25, None, n_band, (FLAG_NO_ATM_IV,))
    bfly25 = 0.5 * (iv_call_25 + iv_put_25) - atm_iv
    return SkewMetrics(rr25, bfly25, n_band, ())


def _iv_at_delta(points: Sequence[tuple[float, float]], target: float) -> float | None:
    """IV em `|Δ| = target` por interpolação linear em Δ entre vizinhos.

    `points` são `(|Δ|, iv)` de **um lado**, ordenados por `|Δ|`. `None` quando
    `target` cai fora da faixa coberta por aquele lado (sem bracket).
    """
    if not points:
        return None
    if target < points[0][0] or target > points[-1][0]:
        return None
    for i in range(len(points) - 1):
        low_delta, low_iv = points[i]
        high_delta, high_iv = points[i + 1]
        if low_delta <= target <= high_delta:
            span = high_delta - low_delta
            if span <= 0.0:  # Δ idêntico nos dois pontos: usa o primeiro
                return low_iv
            return low_iv + (high_iv - low_iv) * ((target - low_delta) / span)
    return points[-1][1]  # target == |Δ| do último ponto
