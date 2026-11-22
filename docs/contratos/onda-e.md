# Contrato — Onda E (F1.5 ATM · F1.7 VRP · F1.8 skew · F1.9 dist · F1.10 checks)

**Dono do contrato:** orquestrador · **Data:** 2026-11-22 · **Válido para:** E1, E2.

Entrada: o núcleo da Onda D (`conventions/forward/iv/greeks/realized`) e o lake
da Onda C. Tudo aqui é **puro** em `derive/` (zero I/O). `model_version = 1`.

Fonte de verdade: docs/spec.md §6.4 (ATM), §6.7 (VRP), §6.8 (skew), §6.10
(expected move/dividendo implícito), §6.11 (validadores), §5.3
(`metrics_underlying`). Unidades: IV em **% a.a.** (pontos de vol = ponto
percentual), `rate_annual` % a.a. (divide por 100 no DF — Onda D).

## 0. Donos de arquivo

| Pacote | Dono | Arquivos |
|---|---|---|
| E1 | impl. E1 | `src/b3o/derive/atm.py`, `src/b3o/derive/vrp.py`, `src/b3o/derive/skew.py`, `src/b3o/derive/dist.py`, `tests/test_atm.py`, `tests/test_vrp.py`, `tests/test_skew.py`, `tests/test_dist.py` |
| E2 | impl. E2 | `src/b3o/derive/checks.py`, `tests/test_checks.py` |
| congelado | orquestrador + ondas A–D | todo o resto |

## 1. E1 — superfície e sinal

### `atm.py` (F1.5)

```python
@dataclass(frozen=True, slots=True)
class ExpiryAtm:
    maturity: date
    t: float                 # anos úteis (du/252)
    atm_iv: float | None     # % a.a. — IV interpolada em K=F (variância total)
    n_strikes: int
    flags: tuple[str, ...]   # "sparse_chain", "iv_extrapolated" (quando aplicado no 30d, fica no resultado final)

@dataclass(frozen=True, slots=True)
class Atm30d:
    iv_atm_30d: float | None  # % a.a.
    method: str                # "interpolated" | "extrapolated_blocked" | "none"
    front: ExpiryAtm | None    # vencimento antes de 30d
    back: ExpiryAtm | None     # vencimento depois de 30d
    flags: tuple[str, ...]

def atm_by_expiry(chain: Sequence[OptionQuote], forward: float | None,
                  curve: CurveInput) -> list[ExpiryAtm]: ...
def atm_interpolate_30d(expiries: Sequence[ExpiryAtm]) -> Atm30d: ...
```

- Por vencimento: IV no strike `K = F` por **interpolação de variância total**
  `w = σ²T` em `log(K/F)` (spec §6.4); sem forward → `None` + flag.
- Entre vencimentos: `w` linear em `T`, avaliar em `T = 30/252`
  (du=30). **Sem extrapolar além do último vencimento** →
  `method="extrapolated_blocked"`, `iv_atm_30d=None` + flag `iv_extrapolated`
  (spec: "sem extrapolar… null + iv_extrapolated").
- Mínimo de strikes por vencimento (ex.: 3) — senão `sparse_chain` e o
  vencimento não entra na interpolação.
- O `OptionQuote` de entrada vem do F1 CLI (Onda F): strike ¢, mid, t, style,
  ex-div flags — E1 não refaz IV; RECEBE as IVs (f(só matemática de smile)).

### `vrp.py` (F1.7)

```python
def vrp(iv_atm_30d: float, rv30: float) -> float: ...          # iv − rv (pontos)
def vrp_z(vrp_series: Sequence[float], window: int = 252) -> float | None: ...
    # (vrp − média252) / desvio252 — SEM as 252 sessões de aquecimento → None + flag
def iv_rank_252(iv_series: Sequence[float]) -> float | None: ...  # percentil 0–100
```

- `vrp = iv_atm_30d − rv30` (tenor casado, §6.7). Série histórica por papel
  vem do caller (F CLI); **z usa SÓ dados até a data** (o caller fatia — e o
  teste de look-ahead do F1b caça isso).
- Sem 252 sessões → `None` (spec: "sem aquecimento → null").

### `skew.py` (F1.8)

```python
@dataclass(frozen=True, slots=True)
class SkewMetrics:
    rr25: float | None
    bfly25: float | None
    n_strikes_band: int        # strikes entre 20Δ e 80Δ
    flags: tuple[str, ...]     # "sparse_chain"

def rr25_bfly25(chain: Sequence[DeltaPoint], atm_iv: float) -> SkewMetrics: ...
```

- `RR25 = iv(25Δ call) − iv(25Δ put)`; `BFLY25 = ½(iv25c + iv25p) − iv_atm`
  (§6.8). Os 25Δ vêm da cadeia já com delta (Onda F calcula Δ com `greeks.py`);
  E1 recebe `DeltaPoint{delta, iv}` e faz a aritmética + interpolação até 25Δ.
- **Mínimo 5 strikes entre 20Δ e 80Δ** — senão `None` + `sparse_chain`.
- IVs das duas pontas por interpolação linear em Δ.

### `dist.py` (F1.9)

```python
def expected_move_1s(straddle_mid: float, spot: float) -> float: ...
    # exp_move ≈ 1,25 × straddle/spot (§6.10) — constante CITADA no comentário
def implied_div_yield(r_pct: float, forward: float, spot: float, t: float) -> float | None: ...
    # r − ln(F/S)/T, em % a.a.; sem forward → None
```

## 2. E2 — validadores (F1.10)

```python
@dataclass(frozen=True, slots=True)
class ConsistencyReport:
    series_flags: Mapping[str, tuple[str, ...]]   # ticker -> flags
    ok_smile: bool

def check_chain(chain: Sequence[OptionQuote], forward: float | None,
                *, butterfly_tol: float = 0.05) -> ConsistencyReport: ...
```

- **Butterfly ≥ −tol** (em variância total, por trios de strikes adjacentes):
  violação → flag `bfly_negative` por série.
- **Calendário**: variância total não-decrescente no mesmo strike entre
  vencimentos → `calendar_violation`.
- **Monotonicidade**: call decrescente / put crescente em K (em IV) →
  `monotonicity_violation`.
- Série com qualquer flag `bfly_negative`/`calendar_violation`/
  `monotonicity_violation` → `inconsistent` — **fora do smile e do mispricing**
  (spec §6.11); o F1 CLI grava `flags[]` em `metrics_series`.
- Tolerâncias default documentadas; sem tolerância "generosa" escondida.

## 3. Testes (propriedades, não tabuleiros mágicos)

- ATM: cadeia sintética com smile quadrático conhecido → IV em K=F recupera o
  valor plantado (±1e-6 relativo); interpolação em `w` bate com
  `w = w1 + (w2−w1)·(T−T1)/(T2−T1)`; além do último vencimento → None+flag;
  poucos strikes → sparse_chain.
- VRP: série sintética com média/desvio conhecidos → z confere; janela curta →
  None; **z da data t usa só dados ≤ t** (teste explícito com série em que o
  futuro mudaria o resultado).
- Skew: cadeia plantada com RR negativo conhecido → recupera; <5 strikes →
  None+sparse_chain; BFLY com IVs plantadas.
- dist: straddle 2,00 em spot 40,00 → exp_move = 1,25×0,05 = 6,25% (à mão);
  implied_div com F/S conhecidos → confere com r − ln(F/S)/T.
- checks: trios plantados violando butterfly/calendário/monotonicia → flags
  certas; cadeia limpa → ok_smile True e zero flags.
- 1 número REAL de mão (depois do F CLI; aqui opcional): não inventar.

## 4. Definição de pronto

1. pytest verde + ruff limpos (compose).
2. `derive/` puro (arquitetura verde; zero I/O nos seus módulos).
3. Toda constante com fonte citada no comentário (1,25 do §6.10, tolerâncias).
4. Commit por pacote `feat(f1/<atm|vrp|skew|dist|checks>): …`; retry index.lock.
5. Descoberta sobre o dado → resposta.
