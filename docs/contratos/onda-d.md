# Contrato — Onda D (F1.1–F1.4 núcleo de precificação · F1.6 realizada+eventos)

**Dono do contrato:** orquestrador · **Data:** 2026-11-22 · **Válido para:** D1, D2.

`model_version = 1` começa AQUI. Toda fórmula é PURA (recebe número, devolve
número; zero I/O em `derive/`). Nenhum arredondamento no cálculo — só na
serialização (que nem existe nesta onda: só funções e testes).

Fonte de verdade: docs/spec.md §6.0–§6.6. Este contrato traduz — divergência
de unidade/convenção é bug.

## 0. Donos de arquivo

| Pacote | Dono | Arquivos |
|---|---|---|
| D1 | impl. D1 | `src/b3o/derive/conventions.py`, `src/b3o/derive/forward.py`, `src/b3o/derive/iv.py`, `src/b3o/derive/greeks.py`, `tests/test_conventions.py`, `tests/test_forward.py`, `tests/test_iv.py`, `tests/test_greeks.py` |
| D2 | impl. D2 | `src/b3o/derive/realized.py`, `src/b3o/derive/corporate_events.py`, `tests/test_realized.py`, `tests/test_corporate_events.py` |
| congelado | orquestrador + ondas A/B/C | todo o resto, `docs/contratos/*` |

Regra de unidade herdada do B3: **`rate_annual` está em % a.a.** — divida por
100 antes de usar em `DF = exp(−r·T)`.

## 1. D1 — convenções, forward, IV, gregas

### `conventions.py` (F1.1)

```python
def business_days_between(cal: TradingCalendar, start: date, end: date) -> int: ...
def year_fraction(business_days: int) -> float:      # du / 252.0, exato
def discount_factor(rate_annual_pct: float, t: float) -> float:  # exp(-r/100 * t)
def log_df_interp(du: int, curve: Sequence[tuple[int, float]]) -> float: ...
    # interpolação em log-DF por DU entre nós da curva (spec §6.0):
    # fora da grade: NUNCA extrapolar acima do último nó para frente;
    # abaixo do primeiro nó, usar o primeiro (curva DI começa em du=1).
```

- `TradingCalendar`: **derivado do lake** (datas de sessão presentes), NÃO de
  tabela de feriados — o dado é a verdade (P-C). API mínima:
  `TradingCalendar(sessions: sorted Sequence[date])`, `.business_days(a, b)`,
  `.is_session(d)`, `.next_session(d)`, `.sessions_between(a, b)`.
  Quem constrói com as datas do lake é o C1/F1 CLI; aqui só a classe pura.
- `T` em **dias úteis/252**; vencimento expira no **fechamento** do último dia
  de negociação (a sessão do vencimento CONTA como dia de vida? Decisão:
  `T = business_days(refdate, maturity)/252` com a contagem EXCLUINDO o
  maturity day — convenção "expira no fechamento": uma opção que vence HOJE
  tem T=0 (só intrínseco) — registre na docstring; teste trava).
- Sem `scipy`: `norm_cdf`/`norm_pdf` via `math.erf` (float puro).

### `forward.py` (F1.2) — paridade, SÓ OTM

```python
def forward_by_parity(chain: Sequence[ParityQuote], *, style_strict: bool = True) -> ForwardResult: ...
@dataclass(frozen=True, slots=True)
class ParityQuote:
    strike: int          # ¢
    call_mid: float      # ¢ (mid ou close se one_sided — o C1/F1 passa o mid já resolvido)
    put_mid: float
    call_style: str | None   # 'A' | 'E' | None
    put_style: str | None     # puts são sempre 'E' (medido)
    call_ex_div: bool         # american_call_ex_div contaminada — NÃO entra
@dataclass(frozen=True, slots=True)
class ForwardResult:
    forward: float | None     # ¢
    method: str               # "crossing" | "ols" | "none"
    n_pairs: int
    ols_slope: float | None
    flags: tuple[str, ...]     # ex.: "no_otm_crossing", "wide_spread_pairs"
```

- Paridade `C − P = DF·(F − K)`, **só OTM** (call `K > F₀`, put `K < F₀`) com
  `F₀` = spot do dia (aproximação para selecionar o lado OTM; iterar 1× se
  quiser, documentado).
- **Preferir pares europeus** (call E + put E do mesmo strike). Call americana
  só OTM e `call_ex_div == False`. Puts: sempre europeias (medido) — se
  aparecer put 'A', exclua o par (flag).
- **Crossing first**: interpolar o strike onde `C(K) == P(K)` por interpolação
  linear em K do `C−P` — sem cruzamento na grade → **OLS** de `(C−P)` contra
  `K` nos OTM com `|K−F₀|/F₀ ≤ 0,15`, `F = K₀ − (C−P)_{K₀}/slope`; slope
  não-negativo e mínimo de pares (ex. 3) senão `method="none"`, `forward=None`
  + flag. Nunca invente forward.
- Sem dados → `ForwardResult(None, "none", 0, flags=("no_pairs",))` — null+flag.

### `iv.py` (F1.3) — Black-76 + inversão

```python
def black76_call_price(F: float, K: float, t: float, df: float, sigma: float) -> float: ...
def black76_put_price(F: float, K: float, t: float, df: float, sigma: float) -> float: ...
def implied_vol(price: float, F: float, K: float, t: float, df: float, *,
                is_call: bool, lo: float = 1e-4, hi: float = 5.0) -> float | None: ...
    # Brent vetorial opcional (numpy) para LOTE; scalar para teste.
    # preço abaixo do intrínseco ajustado ou acima do teto → None + flag "iv_inconsistent_input"
```

- `d1 = (ln(F/K) + σ²T/2)/(σ√T)`, `d2 = d1 − σ√T`; call `= DF·(F·N(d1) − K·N(d2))`.
- Inversão por **Brent em [1e-4, 5]** (implementação própria ~20 linhas, ADR-004);
  vetorizada com numpy para lotes (a métrica do F1 processa o dia inteiro).
- `t == 0` → preço é intrínseco; IV **None** + flag `iv_expired` (não extrapolar).
- Sanity embutida: recomputar preço com a IV achada, |Δ| < 1e-6 relativo.

### `greeks.py` (F1.4) — unidades interpretáveis (§5.4)

```python
@dataclass(frozen=True, slots=True)
class Greeks:
    delta: float        # por ação, sinal: call +, put −
    gamma: float        # ∂²V/∂S²
    theta_brl: float    # R$ por DIA ÚTIL (÷252 do anual)
    theta_pct: float    # % do prêmio por dia útil
    vega_brl: float     # R$ por ação por 1 PONTO de vol (σ de 25%→26%)
    rho: float
def greeks_black76(kind: Literal["C", "P"], F: float, K: float, t: float,
                   df: float, sigma: float, *, quote_factor: int) -> Greeks: ...
```

- `vega_brl`: a spec §6.0 diz "R$ por ação por 1 ponto de vol" — derivada em
  σ e multiplicada por **0,01** (um ponto percentual), com o multiplicador do
  contrato vindo do `quote_factor` (FATCOT) — preço por ação = preço ÷ fator.
- `theta_brl`: Θ anual ÷ 252, por ação (÷ quote_factor onde aplicável).
- Gregas de put com o sinal certo (delta negativo).

### Testes de referência (F1.11, aqui)

- **Black-76 contra valores de referência escritos à mão**: use um par
  publicado (ex.: Haug 2007, cap. Black-76: F=100, K=100... ou outro livro
 /fonte que você CITE no comentário do teste). Proibido gerar o esperado com o
  próprio código em teste. Além do par citado: **propriedades** que não dependem
  de tabela — paridade `C−P = DF(F−K)` numérico (1e-12), inversão
  preço→IV→preço (1e-6 relativo), `put == call − DF(F−K)`, vega igual para
  call/put, `theta_pct = theta_brl/preço`.
- Gregas contra **diferenças finitas** (h de 1e-4 em F e σ, 1e-6 em t)
  com tolerância 1e-3 relativo — diferenças finitas SÃO independentes da fórmula fechada.
- Interpolação log-DF: nós conhecidos → valor entre nós bate com
  `exp(interp linear em log-DF)`; fora do último nó → None/flag.
- T=0: call = max(F−K,0)·DF… com DF=1 — intrínseco, IV None.
- **Conferência de mão obrigatória no REAL**: depois que o C1 existir. Aqui:
  1 par PETR4 real do golden (PETRM410 de 19/11: strike 3569¢, close 3¢ —
  IV vai sair alta; use PETRK410 call 13,00¢ ITM) calculado com print e colado
  na resposta, marcado como "cálculo de mão com as fórmulas do contrato".

## 2. D2 — realizada + eventos corporativos

### `realized.py` (F1.6) — Yang-Zhang

```python
def yang_zhang_vol(ohlc: Sequence[OhlcBar], window: int) -> float | None: ...
@dataclass(frozen=True, slots=True)
class OhlcBar:
    refdate: date
    open: int; high: int; low: int; close: int   # ¢ — do SUBJACENTE, mercado 010
    adj_factor: float = 1.0   # fator de corporate_events aplicado ANTES (§6.5)
```

- Yang-Zhang (OHLC, base 252): `σ² = σ²_overnight + k·σ²_open−close` com
  `k = (n−1)/(n+1)`… implemente a fórmula do paper original (Yang & Zhang 2000,
  σ²_YZ = σ²_o + k σ²_c + (1−k)σ²_rs) — CITE a fonte no comentário; janelas
  20/30/60; `None` + flag `insufficient_history` se janela incompleta.
- **Ajuste por `corporate_events` ANTES de qualquer RV** (§6.5): a série do
  subjacente multiplicada por `1/fator` nas datas de escala (desdobramento
  1:3 sem ajuste explode a RV — teste com série sintética recuperando ±0,5
  vol point).
- **Gap de provento excluído**: return overnight compatível com o Δ do
  dividendo implícito (do `strike_events` do B2) NÃO entra na janela
  (detectado pelo C1/F1 pipeline e passado como `ex_div_dates: frozenset[date]`).
- Série sintética de teste com vol CONHECIDA (gere retornos gaussianos com
  seed fixa, vol alvo 30%; recupere ±0,5 vol point) + série com desdobramento
  1:3 no meio (sem ajuste explode; com ajuste volta ao valor certo).
- `|r| > 40%` overnight sem evento correspondente → quem acusa é o health do
  C1 (`unexplained_gap`); aqui só a função `flag_unexplained(returns,
  events) -> list[date]` pura.

### `corporate_events.py` (F1.6b) — ponte do B2

- Funções puras que transformam `StrikeEvent` (B2) em série de fatores por
  papel: `scale_factors(events) -> Mapping[str, Mapping[date, float]]` e
  `dividend_dates(events) -> Mapping[str, frozenset[date]]` — o insumo do
  realized e do F1b. Reusa o B2; não re-classifica nada.

## 3. Definição de pronto

1. pytest verde + ruff limpos (rodando os DOIS comandos por compose).
2. Toda fórmula com teste de referência INDEPENDENTE (citado ou propriedade
   matemática) — nunca esperado gerado pelo código sob teste.
3. `derive/` sem I/O: teste de arquitetura continua verde (e seu módulo não
   pode ter open()/Path/pyarrow — só math/numpy/typing).
4. `model_version` não é desta onda (é do C1/F1 CLI) — mas nenhuma fórmula
   pode existir fora daqui (regra 9 do AGENTS).
5. Commit por pacote: `feat(f1/conventions|forward|iv|greeks|realized): …`.
   Retry index.lock (outro pacote D em paralelo).
6. Descoberta sobre o dado → resposta (orquestrador registra no AGENTS.md).
