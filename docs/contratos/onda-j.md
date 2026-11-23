# Contrato — Onda J (F4: `structure_eval` descritivo)

**Dono do contrato:** orquestrador · **Data:** 2026-11-23 · **Válido para:** F4.0–F4.4.
**Base:** spec §6.0/§6.2/§6.3/§6.10/§7/§8/§9, PLAN F4, ADR-017 (preço = close), ADR-019 (sem
testes), ADR-023/024 (descritivo), ADR-026/027 (envelope/leitura só do lake).

A tool **avalia a estrutura que o usuário especificou**. Ela não sugere, não compara, não ordena,
não diz "melhor/caro/barato" e não recomenda. `signal_class` não é lido. PoP/RND e margem ficam
fora (spec §7/§6.10).

## 0. Regras que não se quebram

1. **O Python calcula.** Payoff, breakeven, perda/ganho máximo, capital, gregas agregadas e
   cenários são `derive/structure.py` (puro: sem I/O, sem DuckDB, sem numpy).
2. **Uma camada de consulta.** HTTP (`POST /api/structure`), MCP (`structure_eval`) e CLI
   (`b3o struct`) chamam **o mesmo** `payloads.structure_payload`, que chama a **única**
   `QueryService.structure_eval`. SQL só em `store/`/`query/`.
3. **Descritivo.** Nenhum campo preditivo; nenhuma estrutura é comparada com outra.
4. **Falha explícita.** Erro = `{"error": {code, message, hint}}` (sem envelope, sem traceback);
   ausência = `null` + flag. Nunca zero silencioso.
5. **Limites.** `max_rows`/listas ≤ **60** linhas; teto de **200 KB** trunca com `truncated: true`;
   toda resposta carrega `as_of_session` e `model_version`.
6. **Fórmula gravada congelada.** `metrics/`, `ingest/`, `store/` e o `derive/` existente não são
   alterados; `model_version` continua **2**. A matemática nova é on-demand e versionada em
   `structure_version = 1` (constante do módulo, nunca no `model_version`).
7. **Sem margem, sem lote inventado.** `quantity` é **número de ações** (inteiro > 0). O prêmio é
   por ação; 1 contrato padrão = `quantity: 100`. Não se multiplica por 100 em lugar nenhum.
8. **Container é o caminho oficial.** Sem dependência nova → `uv.lock` não é tocado.

## 1. Donos de arquivo

| Pacote | Arquivos |
|---|---|
| F4.0 | `docs/contratos/onda-j.md` |
| F4.1 | `backend/src/b3o/derive/structure.py` (**novo**, puro) |
| F4.2 | `backend/src/b3o/query/service.py` (método + dataclasses), `query/__init__.py` (se exportar) |
| F4.3 | `backend/src/b3o/api/payloads.py`, `api/app.py`, `api/mcp_server.py`, `api/serialize.py` (só o mapa de status dos códigos novos), `backend/src/b3o/cli.py` (**só** o comando `struct`) |
| F4.4 | `backend/scripts/structure_smoke.py` (**novo**) e `backend/scripts/mcp_smoke.py` (atualizado: 7ª tool `structure_eval` e a exceção do nome `net_premium_brl` no grep) |
| docs | `docs/decisoes.md` (ADR), `docs/glossario.md` (verbetes novos), `PLAN.md`, `AGENTS.md`, `docs/relatorios/2026-11-23-revisao-f4.md` |
| **congelado** | `metrics/`, `ingest/`, `store/`, `derive/` existente (só chamar), `backend/pyproject.toml`, `backend/uv.lock`, `frontend/`, `config.yaml` |

## 2. Entrada canônica

```json
{
  "underlying": "PETR4",
  "date": "2026-11-19",
  "legs": [
    {"kind": "option", "ticker": "PETRL44", "side": "short", "quantity": 100},
    {"kind": "stock",  "ticker": "PETR4",  "side": "long",  "quantity": 100}
  ],
  "capital": 5000,
  "scenarios": true
}
```

| Campo | Regra |
|---|---|
| `underlying` | opcional; se presente tem de ser o papel resolvido (senão `underlying_mismatch`) |
| `date` | opcional; ISO; `None` → última sessão do lake; data sem sessão → `no_session` |
| `legs` | **obrigatório**, lista não vazia, no máximo **60 pernas** (é a lista que volta na resposta; `> 60` → `bad_legs`); cada perna é `{kind, ticker, side, quantity}` |
| `kind` | `option` \| `stock` (outro → `bad_legs`) |
| `side` | `long` \| `short` (outro → `bad_legs`) |
| `quantity` | inteiro **> 0**, em ações (0, negativo ou não inteiro → `bad_legs`) |
| `capital` | opcional, R$ ≥ 0 (negativo → `bad_request`) |
| `scenarios` | opcional, default `true` |

Regras de resolução (todas no `QueryService`, com o vínculo opção→papel **pelo ISIN**, nunca pelo
prefixo do ticker — armadilha 1 do `AGENTS.md`):

- toda perna de opção resolve para o **mesmo papel** (senão `underlying_mismatch`) e para o **mesmo
  vencimento** (senão `maturity_mismatch`);
- a perna de ação só é aceita com `ticker == underlying` resolvido (senão `underlying_mismatch`);
  estrutura só de ação é válida (`underlying` = o próprio ticker);
- ticker inexistente no lake → `ticker_not_found`; série existe mas sem linha em `metrics_series`
  na sessão → `metrics_missing`; papel sem fechamento na sessão → `metrics_missing`;
- preço da perna = **`metrics_series.mid`** da sessão (ADR-017: o close por ação; `mid` já é
  `close / FATCOT`); perna de ação = fechamento do `010` (fallback `020`);
- série ilíquida **entra** com `illiquid_leg` (não bloqueia);
- faixa de preço da perna: `price_low_brl = mid − spread/2`, `price_high_brl = mid + spread/2`
  quando **os dois lados do book existem** na sessão (`spread = ask − bid`); senão `null` (a
  ausência não vira 0). A perna de ação não tem book nesta onda → faixa `null`.

## 3. `derive/structure.py` (puro) — `structure_version = 1`

```python
@dataclass(frozen=True, slots=True)
class StructureLeg:            # perna JÁ resolvida (entrada do cálculo)
    kind: str                  # "option" | "stock"
    ticker: str
    side: str                  # "long" | "short"
    quantity: int              # ações
    price_brl: float           # por ação
    price_low_brl: float | None
    price_high_brl: float | None
    is_liquid: bool
    option_side: str | None    # "C" | "P" (None em ação)
    style: str | None          # "A" | "E" | None
    strike_brl: float | None
    iv_pct: float | None       # % a.a.
    delta: float | None
    gamma: float | None
    theta_brl: float | None    # R$/ação/dia útil
    vega_brl: float | None     # R$/ação/1 vol pt
    maturity: date | None

def evaluate(legs, *, spot_brl, forward_brl, df, t, maturity=None,
             capital_brl=None, scenarios=True) -> StructureResult
```

### 3.1 Payoff no vencimento

Valor terminal por perna (por ação), `s` = preço do papel no vencimento:

| perna | valor terminal |
|---|---|
| stock long/short | `s` |
| call long/short | `max(s − K, 0)` |
| put long/short | `max(K − s, 0)` |

`terminal_brl(s) = Σ sign × quantity × valor_terminal`, `sign = +1` em `long`, `−1` em `short`.

Fluxo de caixa na abertura: `premium_brl(perna) = +quantity × price_brl` (long paga) e
`−quantity × price_brl` (short recebe). `net_premium_brl = Σ premium_brl` —
**positivo = débito pago, negativo = crédito recebido** (inclui a compra/venda da ação ao preço da
sessão). A ação entra no cálculo exatamente como uma perna cujo valor terminal é `s` e cujo
"prêmio" é `sessão × quantity` — é isso que faz a coberta ter breakeven `S₀ − prêmio da call`.

`pnl_brl(s) = terminal_brl(s) − net_premium_brl`.

### 3.2 Breakevens

`pnl_brl(s)` é linear por partes, com kinks nos strikes (> 0). Raízes em `s ≥ 0`: troca de sinal em
cada segmento `[0, K₁], [K₁, K₂], …, [K_n, ∞)`, mais o próprio kink quando `pnl = 0` nele.
O último segmento é linear com inclinação `slope_∞ = Σ sign × quantity` (call e ação acima de todos
os strikes; put não inclina acima do seu strike) — a raiz dele é finita mesmo em estrutura com
perda/ganho ilimitado. Devolver **ordenado, único** (tolerância `1e-9`), sem `null`.

### 3.3 Perda/ganho máximo e capital

- `pnl_min`/`pnl_max` = mínimo/máximo de `pnl_brl` nos kinks + `s = 0`; se `slope_∞ < 0`, o limite
  em `s → ∞` é `−∞`; se `slope_∞ > 0`, é `+∞`.
- `max_loss_brl` = `max(0, −pnl_min)` em **magnitude ≥ 0**; `null` quando `pnl_min = −∞`
  (+ flag `unbounded_loss`). `max_gain_brl` = `max(0, pnl_max)`; `null` quando `pnl_max = +∞`
  (+ `unbounded_gain`). `0` é resposta legítima ("não perde" / "não ganha"), não ausência.
- `capital_committed_brl`: `net_premium_brl > 0` (débito) → o próprio débito; `net_premium_brl ≤ 0`
  (crédito/risco definido) → `max_loss_brl`; `null` quando `max_loss_brl` é `null`.
  **Sem margem** (não está no dado).
- `capital_over = capital_brl is not None and capital_committed_brl is not None and
  capital_committed_brl > capital_brl` (+ flag `capital_over`; não é erro).

### 3.4 Gregas agregadas

`Σ sign × quantity × grega_da_perna`. Ação: `Δ = 1`, `Γ = Θ = ν = 0` (não se inventa decaimento).
Valores como gravados: `Θ` em R$/ação/dia útil, `ν` em R$/ação/1 vol pt, `Γ` por ação na unidade
de `F`. Perna sem grega gravada contribui `0` e a flag `no_greeks` **não** é criada nesta onda (o
`delta` nulo aparece no campo da perna); a agregação é sobre o que existe — documentado.

### 3.5 Cenários (`scenarios=True`, default)

Grade fixa: spot `× {0,80; 0,90; 1,00; 1,10; 1,20}` (5) × choque de IV `{−5, 0, +5}` pontos (3) ×
dias à frente `{0, 7, 15}` (3) = **45 pontos** (≤ 60). Cada ponto:

- opção: `F' = F × s'/S`, `σ' = (iv_pct + choque)/100` (IV gravada em % a.a., choque em pontos),
  `T' = max(t − dias/252, 0)`; `T' = 0` → intrínseco (`max(F'−K,0)`/`max(K−F',0)`, `DF = 1`);
  senão `black76_*` de `derive/iv.py` com `DF' = exp(log_df_interp_or_none(du − dias, curva))`,
  `du = round(t × 252)`;
- ação: valor `= s'` (independe de IV e dias);
- `pnl_brl = Σ sign × quantity × valor' − net_premium_brl`.

Falta de insumo → `scenarios = null` + flag `scenarios_unavailable`, com a causa explícita:
`forward_brl is None` → `forward_unavailable`; `df is None` → `no_rates`; perna de opção com
`iv_pct is None` → `no_iv`. O **payoff no vencimento continua** sendo calculado.

Sanidade interna: em `(s' = S, choque = 0, dias = 0)` o P&L é `≈ 0` (o valor Black-76 com a IV
gravada reproduz o `mid`, que é o preço usado na inversão; ADR-017).

### 3.6 Curva de payoff (gráfico)

Grade base de **41 pontos** entre `0,6·S` e `1,4·S`, acrescida de todo strike e todo breakeven
dentro do intervalo; ordenada e única, **capada em 60** (se passar, preserva strikes e breakevens e
reduz a base uniformemente). Cada ponto é `PayoffPoint(spot_brl, pnl_brl)`.

### 3.7 `StructureResult`

```python
structure_version: int      # 1
spot_brl: float
forward_brl: float | None
df: float | None
t: float | None
maturity: date | None
legs: list[LegRow]          # eco resolvido (ver §4) + premium_brl por perna
net_premium_brl: float
net_premium_low_brl: float | None    # null se alguma perna não tem faixa de book
net_premium_high_brl: float | None
breakeven_brl: list[float]
max_loss_brl: float | None
max_gain_brl: float | None
capital_committed_brl: float | None
capital_input_brl: float | None
capital_over: bool
delta, gamma, theta_brl, vega_brl: float
payoff: list[PayoffPoint(spot_brl, pnl_brl)]
scenarios: list[ScenarioRow(spot_brl, iv_shift_pts, days_forward, pnl_brl)] | None
flags: list[str]
```

Flags (nomes estáveis, ordem alfabética): `capital_over`, `forward_unavailable`, `illiquid_leg`,
`no_iv`, `no_rates`, `scenarios_unavailable`, `unbounded_gain`, `unbounded_loss`.

## 4. `QueryService.structure_eval` (F4.2)

```python
structure_eval(legs, underlying=None, date=None, capital=None, scenarios=True) -> StructureEval
```

`StructureEval(underlying, as_of_session, model_version, result: StructureResult, notes: list[str])`.

- Sessão por `_session(date)` (`no_session` fora do lake); papel por `_require_underlying`.
- Opção: linha de `metrics_series` da sessão, resolvida por `_chain_rows` (que já usa o ISIN);
  o forward do vencimento e o `DF` saem de `_term_rows` + `rates_curve` + `log_df_interp_or_none`
  (reuso do F3; nada é gravado nem recalculado no lake).
- `as_of_session` e `model_version` no resultado; `notes` explica `illiquid_leg`, cenários
  indisponíveis e o motivo do `capital_over`.
- Erros: `no_session`, `ticker_not_found`, `metrics_missing`, `expiry_not_found` (vencimento pedido
  não existe — aqui só ocorre via resolução), `underlying_mismatch`, `maturity_mismatch`, `bad_legs`,
  `bad_request`.

## 5. Superfícies (F4.3)

### 5.1 HTTP

`POST /api/structure` com o JSON do §2 (corpo = objeto). Resposta = envelope do F3
(`as_of_session`, `model_version`, `data`, `meta`) com `data` = resultado achatado:

```json
{
  "underlying": "PETR4",
  "spot_brl": 42.67,
  "forward_brl": 43.17,
  "df": 0.989...,
  "t": 0.079...,
  "maturity": "2026-12-18",
  "structure_version": 1,
  "legs": [...],
  "net_premium_brl": 4109.0,
  "net_premium_low_brl": null,
  "net_premium_high_brl": null,
  "breakeven_brl": [41.09],
  "max_loss_brl": 4109.0,
  "max_gain_brl": 207.0,
  "capital_committed_brl": 4109.0,
  "capital_input_brl": 5000.0,
  "capital_over": false,
  "delta": 48.624814, "gamma": -0.098687, "theta_brl": 3.849762, "vega_brl": -4.793596,
  "payoff": [{"spot_brl": 25.6, "pnl_brl": -1549.0}, ...],
  "scenarios": [{"spot_brl": 34.13, "iv_shift_pts": -5.0, "days_forward": 0, "pnl_brl": ...}, ...],
  "flags": [],
  "notes": []
}
```

`meta` descreve `payoff` (`total` = pontos calculados, `returned` = após teto de 200 KB,
`truncated`); `meta.lists` traz `legs` e `scenarios` (0 quando `scenarios = null`).
Status: `bad_legs`/`bad_request`/`underlying_mismatch`/`maturity_mismatch` → **400**;
`ticker_not_found`/`metrics_missing`/`no_session`/`expiry_not_found` → **404**.

### 5.2 MCP

Tool `structure_eval(underlying=None, date=None, legs=[...], capital=None, scenarios=True)`.
Descrição com "use quando / não use para": avalia **a estrutura dada**; não sugere, não compara e
não ordena estruturas; não cita `positions` (F6). Mesmo JSON do HTTP.

### 5.3 CLI

```
b3o struct --underlying PETR4 --date 2026-11-19 \
  --leg "option:PETRL44:short:100" --leg "stock:PETR4:long:100" \
  [--capital 5000] [--no-scenarios] [--json]
```

Resumo de uma tela (prêmio líquido, breakevens, perda/ganho máximo, capital, Δ/Γ/Θ/ν, maior e menor
P&L do payoff, nº de cenários, flags) e `--json` imprime o envelope. **Nenhum cálculo no CLI**: ele
usa `payloads.structure_payload`. Exit 1 em erro, com `code/message/hint`.

## 6. Prova (F4.4)

`backend/scripts/structure_smoke.py`, rodando no container contra o servidor:

1. `tools/list` inclui `structure_eval`, com "Use quando"/"Não use para" e zero termo preditivo;
2. MCP = HTTP **no mesmo JSON** para 5 estruturas + 1 call nua;
3. números conferidos à mão (fora do código, no relatório): coberta, put caixa, trava de alta,
   trava de baixa, straddle — prêmio, breakeven, perda/ganho máximo e P&L em `0,9·S`, `S`, `1,1·S`;
4. call vendida nua → `max_loss_brl: null` + `unbounded_loss`; `capital = 1000` na put caixa →
   `capital_over: true`; `capital_committed_brl` correto nas 4 de risco definido;
5. cenários ≤ 60 pontos, `T' = 0` = intrínseco, `(S, 0, 0)` = P&L 0, perna sem IV →
   `scenarios_unavailable` + payoff presente;
6. erros: ticker inexistente, vencimentos diferentes, papéis diferentes, `quantity ≤ 0`, `kind`
   inválido, data sem sessão → `{error:{code,message,hint}}`, sem traceback;
7. nenhuma lista > 60; `grep -i` de `signal_class|premium|acerto|ranking|caro|barato|recomend`
   nas respostas e descrições = 0;
8. `POST /api/structure` não grava nada (nenhum arquivo novo em `data/`).

## 7. Critério de pronto

Os 10 itens do §7 do prompt da sessão, provados com saída colada: `podman compose config`; app no ar
(`B3_APP_PORT=8081`) com `model_version 2`; MCP = HTTP = CLI; 5 estruturas conferidas à mão;
`unbounded_loss` e `capital_over`; cenários e limites; erros sem traceback; zero vocabulário
preditivo; `ruff check src` limpo e comandos do F1/F2/F3 intactos; `git diff` de
`metrics/ingest/store/derive-existente` vazio; `uv.lock` intacto; PLAN/ADRs/AGENTS/relatório.

## 8. Desvios declarados

1. **Faixa `mid ± ½ spread`** (PLAN F4.1) entra como `price_low_brl`/`price_high_brl` por perna e
   bandas agregadas; a perna de ação fica sem faixa nesta onda (o `_spot_brl` lê só o `close`) —
   não se inventa book.
2. **`df` e `t` no resultado**: são insumo dos cenários e ficam visíveis para auditoria (o `t` é o
   gravado; o `df` sai da curva da sessão no `du = round(t×252)` — a mesma convenção do pipeline).
3. **`scenarios` inexistente vira `null`**, não lista vazia: lista vazia sugeriria "calculado e sem
   pontos", que seria mentira.
4. **`max_loss_brl`/`max_gain_brl` são magnitudes ≥ 0** (0 = "não perde"/"não ganha"); o sinal vive
   no payoff e nos cenários. `null` só quando ilimitado, junto da flag.
5. **`derive/structure.py` não lê `date` do sistema**; recebe só números e a data do vencimento já
   resolvida (mesma disciplina das outras fórmulas puras).
6. **O grep de `premium` nas respostas** casa o **nome de campo** `net_premium_brl`/
   `premium_brl` exigido por este contrato (P-R: inglês com unidade), não um rótulo preditivo.
   O smoke aplica o grep **removendo esses nomes de campo**; o que sobra — `premium_rich`,
   `premium_cheap`, `signal_class`, `acerto`, `ranking`, `caro`, `barato`, `recomend` — é **0**.
   Nenhuma classe de sinal é lida.
7. **`scenarios=False` devolve `scenarios = null` sem flag**: é escolha do chamador, não
   indisponibilidade (o contrato só marca flag quando o insumo falta).
