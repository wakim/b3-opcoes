# Spec — Opções B3 (dados + MCP + API + PWA)

**Status:** rascunho para revisão · **Versão:** 0.6 · **Fonte de dados:** B3 (COTAHIST + regulatórios), EOD, gratuita
**Consumo primário:** celular (PWA instalável) · **Secundário:** desktop responsivo e agente via MCP

## Changelog

| Versão | Mudança |
|---|---|
| 0.1 | Primeira spec: modelo de dados, derivação, QueryService, MCP, API, PWA, aceitação. |
| 0.2 | Fecha lacunas apontadas no review de 22/11: origem do OI e da curva DI, convenções de tempo, ações corporativas que não são dividendo, classes de sinal, `risk_budget` restaurado, catálogo único de tools (journal fora do MCP), contexto seguro do PWA, previsão estruturada, busca e seletor de vencimento no mobile, formulário do diário, aceitação de legs. |
| 0.3 | HTTPS via `tailscale serve` (D12). Posições antecipadas para o F4b (§14): modelo, marcação, gregas líquidas, alertas, uso pelo agente. IA do PWA reorganizada em 4 destinos + 2 drill-downs; `Aprender` ganha casa. Catálogo sobe para 18 tools de leitura. |
| 0.4 | Review de 22/11 (2): ajuste de strike classificado pela assinatura entre strikes — Δ constante = provento, razão constante = escala (desdobramento ≠ dividendo); estilo da opção (americana/europeia) lido do nome resumido do COTAHIST, medido em 2026; sinal do RR25 corrigido na aceitação; `risk_budget` sem margem (só perda máxima); MCP montado no FastAPI (uma porta); catálogo cai para 10 funções; saem RND/PoP, BAW, `quote_spot`, `counter_evidence`, `historical_analog` (vira `signal_backtest` com classes fixas); backtest do sinal (§6.13) antes da UI; ano corrente reconstruído inteiro a cada `prepare`; calendário de resultados derivado da CVM (histórico real + estimativa marcada); validação de IV sem OpLab (paridade entre europeias + referência externa só para PETR4); frontend em Vite + TanStack Router/Query (sem TanStack Start); testes automatizados entram na v1; critérios de aceitação só no `PLAN.md`. |
| 0.6 | **Alinhamento com as decisões de 22–23/11 (ADR-017…035)** — o produto é **descritivo** (ADR-023/024): classes de sinal continuam gravadas mas **fora do produto**; `signal_backtest`, `/api/backtest` e `/api/briefing` não são expostos (ADR-027); Hoje sem filtro por classe; `inconsistent` é informativa e sai só do skew/butterfly (ADR-020); preço de inversão é o fechamento (ADR-017); estilo medido corrigido (calls ~57/42); ticker com espaço à **direita**; `strike_adj_ind` sempre `0` em 2026; sem suíte de testes (ADR-019); descasamento de prazo do portão registrado (§6.13); aviso `rv_shock_day` (ADR-035). |
| 0.5 | **Stack inteira em container** (§4.1): `compose.yaml` + imagem multi-stage (Python runtime / dev, Node para o build), `podman compose` como runner oficial no host (compose padrão, compatível com `docker compose`), `./data:/data` como único bind mount, `.venv`/`node_modules` em volumes nomeados, `prepare-watch` como serviço. Nenhum comando oficial usa Python ou Node do host. |

---

## 1. Escopo

**Entra:** ingestão diária (COTAHIST + arquivos regulatórios + curva DI), calendário de resultados **derivado dos dados abertos da CVM** (§6.14), cálculo de IV/gregas/métricas, **backtest das classes de sinal**, funil, servidor MCP read-only, API HTTP, PWA com quatro destinos e camada educacional, diário de decisões, **posições**.

**Fora de escopo na v1 (explícito, não esquecido):**
intraday e qualquer preço vivo · OI com mais de 10 dias · fundamentos · notícias · densidade neutra ao risco / PoP · prêmio de exercício antecipado · margem · execução de ordens · recomendação de investimento.

**Princípio central:** o Python calcula, o agente interpreta, o humano decide. Nenhum número exibido ou citado é produzido por modelo de linguagem.

---

## 2. Decisões travadas

| # | Decisão | Origem |
|---|---|---|
| D1 | Universo automático por liquidez | owner |
| D2 | Funil e métricas 100% EOD; live nunca alimenta derivação | review adversarial |
| D3 | MCP read-only; escrita só no diário, e só via HTTP | review adversarial |
| D4 | IV por forward implícito (paridade) + Black-76, **só OTM** | review quant |
| D5 | ~~Gatilho do funil é z-score do VRP~~ — **suspenso**: o portão F1b não passou (ADR-023); o produto é descritivo e não tem gatilho (ADR-024). VRP, z e IV rank são leitura, não sinal | review quant · revisto 23/11 |
| D6 | ATM 30d interpolado em variância total | review quant |
| D7 | Série = `(ticker, vencimento)`; strike por data | medição no arquivo |
| D8 | Sem preço vivo na v1 (o endpoint não tem timestamp nem contrato) | review 0.4 |
| D9 | PWA responsivo, mobile-first | owner |
| D10 | Uma `QueryService`, duas portas (MCP e HTTP) | arquitetura |
| D11 | PWA servido por HTTPS (contexto seguro obrigatório) | review de lacunas |
| D12 | HTTPS via `tailscale serve` na rede privada; deploy público só depois | owner |
| D13 | Frontend: **Vite + React + TypeScript + TanStack Router/Query**, SPA pura, servida estática pelo FastAPI | owner, revisado 0.4 |
| D14 | Nenhuma lógica de servidor no frontend: FastAPI é o único backend (garantido pela stack, que não tem servidor) | arquitetura |
| D15 | Um processo, uma porta: MCP montado em `/mcp` no mesmo FastAPI | review 0.4 |
| D16 | O sinal é provado por backtest (§6.13) antes de existir UI — **feito: não passou** (ADR-023); a UI que existe é descritiva | review 0.4 · revisto 23/11 |
| D17 | Ajuste de strike classificado pela assinatura entre strikes (Δ constante × razão constante), nunca por limiar (§6.6) | medição no arquivo |
| D18 | A stack inteira roda em container (`compose.yaml`): host só precisa de Podman e `git`; nenhum comando oficial usa Python/Node do host (§4.1) | owner, 22/11 |

---

## 3. Assunções a confirmar

| # | Assunção | Default |
|---|---|---|
| A1 | Vencimentos cobertos | mensais + semanais |
| A2 | Opções de índice e ETF | fora da v1 como **precificação própria**; o painel descritivo mostra BOVA11 e a pseudo-linha `IBOV11` porque entram no corte de volume (excluir índice/BDR do universo é decisão pendente) |
| A3 | Capital e limite de risco | parâmetros por chamada |
| A4 | Diário | SQLite no volume de dados |
| A5 | Backfill | 3 anos (2024–2026) |
| A6 | Idioma | código/spec em inglês; UI e conteúdo em português |
| A7 | Offline | shell + última sessão em cache, com carimbo de sessão |
| A8 | Estilo das opções | **medido (2026, recontado em 22/11):** todas as puts são europeias; calls são ~57% americanas e ~42% europeias por série (62.958 A / 46.109 E; o "66/34" antigo estava errado), com os dois estilos no mesmo papel (`PETR` e `PETRE`). O estilo é atributo da série, lido do COTAHIST (§5.2) |
| A9 | Universo útil | definido pelo relatório de liquidez do F0; a expectativa é de 10–20 papéis, concentrados nos 1–2 primeiros vencimentos |

---

## 4. Runtime

**Tudo roda em container.** Não existe venv, `uv` nem Node *no host* como caminho oficial: o `compose.yaml` na raiz define os serviços e todo comando do projeto é `podman compose …`. O arquivo é compose padrão (sem sintaxe exclusiva) — `docker compose` roda igual em outra máquina; **neste host o motor é Podman**, porque não há Docker instalado. Ver `docs/decisoes.md`.

```
[prepare] one-shot
  1. COTAHIST_A{ano}.ZIP        condicional (ETag / If-Modified-Since → 304)
  2. OI: arquivo regulatório    condicional, diário (histórico de 10 dias na B3)
  3. Curva DI: taxas referenciais (fallback: ajustes de DI1; fallback: Selic flat)
  4. CVM dados abertos           semanal: índice ITR/DFP (datas de entrega) + FCA (ticker ↔ empresa)
  → validate → reconstrói a partição do ano corrente (atômico) → derive → ingest_state

[app] FastAPI long-lived, 127.0.0.1:8080
  /mcp    MCP Streamable HTTP            (agente, read-only)
  /api/*  JSON                            (PWA)
  /       estático de frontend/dist       (build Vite, gerado no estágio Node)
```

### 4.1 Serviços do `compose.yaml`

| Serviço | Profile | Imagem | Para quê |
|---|---|---|---|
| `app` | — | `target: runtime` | FastAPI + `/mcp`, `127.0.0.1:8080`, sem deps de dev |
| `test` | — | `target: dev` | container de ferramentas: `b3o …`, `python`, `ruff` (não há suíte de testes — ADR-019) |
| `dev` | `dev` | `target: dev` | `uvicorn --reload` com `backend/src/` e `backend/config.yaml` montados |
| `prepare` | `jobs` | `target: runtime` | one-shot `b3o prepare` |
| `prepare-watch` | — (padrão) | `target: runtime` | `b3o prepare --watch`, ao iniciar e a cada hora; `restart: unless-stopped` (ADR-036) |
| `web` | `build` | `node:26-slim` | `npm ci && npm run build` → `frontend/dist` (entra no F5.0) |
| `web-dev` | `dev` | `node:26-slim` | Vite em `127.0.0.1:5173`, proxy de `/api` (entra no F5.0) |

Regras do ambiente:

- `./data:/data` é o único bind mount de dado (`B3_DATA_DIR=/data`). `.venv` e `node_modules` vivem em **volumes nomeados** — no bind mount do Windows eles não preservam modo e ficam lentos.
- `TZ=America/Sao_Paulo` no container; a data de referência continua vindo do arquivo, nunca do relógio.
- Porta publicada sempre em `127.0.0.1` no host: quem expõe é o `tailscale serve`.
- `.env` opcional (`env_file` com `required: false`): `APP_TOKEN`, `B3_DATA_DIR`, `LOG_LEVEL`.
- `app`, `prepare` e `prepare-watch` compartilham a **mesma** imagem: um `model_version` só, sem duas cópias do cálculo.
- Limite do motor nesta máquina: 8 vCPU e ~3,7 GiB de RAM. Ingestão é streaming (nunca carregar o TXT inteiro em memória) e o pico de RSS entra no `health`.
- `frontend/dist` é gerado no container e servido pelo FastAPI; `frontend/` não tem servidor.

O COTAHIST anual do ano corrente chega **inteiro** a cada dia. Por isso não há append nem lógica de "sessão republicada": a partição do ano é reparseada e substituída por inteiro (segundos). A idempotência vem de graça.

**Ordem de falha:** cada fonte falha independente. Sem OI → `oi = null` e `days_to_liquidate = null`, com flag `oi_unavailable`. Sem curva DI → usa o fallback e grava `rate_source`, sem bloquear. Sem COTAHIST e sem lake → sai não-zero e o app não sobe.

**Contexto seguro (bloqueante para o PWA):** service worker e instalação exigem HTTPS ou localhost. Em `http://192.168.x.x` **não instala e o cache offline não funciona**.

Caminho escolhido: **`tailscale serve`** no host, apontando para a porta local da app:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8080
# PWA em https://<host>.<tailnet>.ts.net  (certificado válido, contexto seguro)
```

Sem Caddy, sem certificado manual, sem container extra. A app fica publicada em `127.0.0.1:8080` (dentro do compose, `ports: ["127.0.0.1:8080:8080"]`) e só a tailnet a alcança. Funciona nos dois aparelhos, em qualquer rede.

Autenticação: leitura liberada na tailnet; **escrita** (`POST /api/*`) exige `X-App-Token` **só quando `APP_TOKEN` está definido** (guardado no `localStorage` após a primeira digitação); com `APP_TOKEN` vazio as escritas ficam abertas e o acesso é controlado pela rede (ADR-045). Deploy público futuro = a mesma app atrás de Caddy/HTTPS, com Basic auth (padrão do projeto Imoveis).

---

## 5. Modelo de dados

Parquet + DuckDB, particionado por `year`. Preços em **centavos inteiros** (`int64`; volume de série quente passa de R$ 21 M/dia e estoura `int32`).

### 5.1 `raw.cotahist`

`refdate`, `tpmerc` (010 à vista, 020 fracionário, 070 call, 080 put, 012/013 exercício), `ticker` (**trim** — o campo vem alinhado à esquerda com espaço à direita; AGENTS, armadilha 5), `corp_name`, `open/high/low/avg/close`, `best_bid`, `best_ask`, `trades`, `qty`, `volume`, `strike`, `strike_adj_ind`, `maturity`, `isin_underlying`, `distribution_id`.

> **Armadilha registrada:** o `isin` é do **ativo-objeto**, não da série. Nunca usar como chave. Medido em 2026: 220.282 tickers de opção, 51.830 (23,5%) com mais de um strike; dos que têm dois valores, 98% descendentes (provento) e 698 ascendentes (não-provento).

### 5.2 `core.series_leg`

Uma linha por `(ticker, refdate)`, com o *leg* (trecho entre ajustes).

| Coluna | Nota |
|---|---|
| `leg_id` | `{ticker}\|{maturity}\|{leg_index}` |
| `ticker`, `maturity`, `side` | side = C/P |
| `style` | `E` se o 1º token do nome resumido (`l[27:39]`) é a raiz do ticker + `E` (`PETRE`), `A` se é a raiz pura (`PETR`); senão `null` + flag `style_unknown` |
| `quote_factor` | `FATCOT` do COTAHIST (`l[210:217]`); preço por ação = preço ÷ fator |
| **`underlying`** | ticker do ativo-objeto, derivado do ISIN do ativo-objeto (`isin_underlying` → linha `010` com o mesmo ISIN); nunca nulo |
| `underlying_market` | `010` ou `020` — qual mercado forneceu o spot do dia (§6.1) |
| `leg_index` | incrementa a cada mudança de strike |
| `strike` | o do dia, em centavos |
| `mid`, `spread_abs`, `spread_pct` | `spread_pct = spread_abs / mid`; se bid ou ask = 0, `mid = close` e `spread_pct = null` + flag `one_sided`. **Em `metrics_series`, `mid` carrega o fechamento** (ADR-017): IV e forward invertem o `close`, nunca o meio do book |
| `trades`, `volume`, `oi` | `oi` vem de `core.open_interest` |
| `is_liquid` | regra §6.9 |

**Fronteira de leg** = mudança de `strike` **ou** mudança de `strike_adj_ind` **ou** mudança de `distribution_id`. Se os três divergirem, flag `leg_ambiguity` e o leg é cortado no primeiro sinal. *Medido em 2026:* `strike_adj_ind` é **sempre `0`** (3.570.794 linhas), então na prática a fronteira é `strike` e `distribution_id`.

### 5.3 `core.metrics_underlying`

`iv_atm_30d`, `iv_rank_252`, `iv_pct_252`, `rv20`, `rv30`, `rv60`, `vrp`, `vrp_z_252`, `rr25`, `bfly25`, `ts_slope_30_90`, `exp_move_1s_30d`, `implied_div_yield_30d`, `days_to_liquidate`, `forward_30d`, `rate_source`, `model_version`.

### 5.4 `core.metrics_series`

`iv`, `delta`, `gamma`, `theta_brl`, `theta_pct`, `vega_brl`, `rho`, `iv_pct_252`, `flags[]` (`wide_spread`, `no_trade_5d`, `bfly_negative`, `sparse_chain`, `one_sided`, `iv_extrapolated`, `scale_event`, `american_call_ex_div`).

`american_call_ex_div`: call americana ITM com ex-dividendo antes do vencimento — a IV por Black-76 está contaminada pelo valor do exercício antecipado e a série fica fora do smile.

### 5.5 Insumos externos e auxiliares

| Tabela | Origem | Frequência |
|---|---|---|
| `core.rates_di(refdate, du, rate_annual, source)` | B3 taxas referenciais (DI x pré) | diária |
| `core.open_interest(refdate, ticker, oi)` | arquivo regulatório *Posições em Aberto de Derivativos (Listado)* | diária, 10 dias na fonte |
| `core.dividends_implied(underlying, ex_date, amount_per_share, source)` | Δstrike (§6.6) | derivada |
| `core.corporate_events(underlying, date, kind, factor)` | evento de escala (desdobramento, grupamento, bonificação), detectado pela razão constante entre strikes (§6.6) | derivada |
| `core.earnings(underlying, ref_period, date, kind)` | CVM: ITR/DFP (`kind = actual`) ou projeção (`kind = estimated`), §6.14 | semanal |
| `core.ingest_state` | etag, last_modified, sha256, trailer, last_session, counts por data, model_version | diária |
| journal PostgreSQL | append-only: `decision`, `prediction` (§6.12), `position`, `position_leg` (§14); schema por Alembic (ADR-041) | por decisão |

---

## 6. Derivação

### 6.0 Convenções (sem elas, nada é reproduzível)

| Grandeza | Convenção |
|---|---|
| `T` em Black-76 | dias **úteis** entre data e vencimento ÷ **252** |
| Vencimento | último dia de negociação; expira no fechamento desse dia |
| `r` | DI, taxa efetiva anual, base 252; interpolar em **log-DF** por dias úteis |
| `DF` | `exp(−r·T)` |
| IV | anualizada em base 252 |
| Θ | por **dia útil**, em R$ e em % do prêmio |
| ν | R$ por ação por **1 ponto** de vol (o multiplicador do contrato vem de `fator de cotação`) |
| Arredondamento | nenhum no cálculo; arredondar só na serialização |

### 6.1 Spot do subjacente
Preferir mercado `010`; usar `020` só quando `010` não tiver negócio no dia. Registrar em `underlying_market`. Nunca somar os dois (é o mesmo papel em lotes diferentes).

### 6.2 Forward implícito
Paridade `C − P = DF·(F − K)`, apenas quotes **OTM** (call `K > F`, put `K < F`). A paridade só é exata entre europeias. As puts são todas europeias; nas calls, **preferir a série europeia** (`PETRE…`) quando houver, e aceitar a americana só OTM e sem ex-dividendo no prazo (prêmio de exercício desprezível). Séries com `american_call_ex_div` não entram.
Forward = strike de cruzamento (`C = P`, interpolado). Sem cruzamento na grade → OLS de `(C−P)` contra `K` nos OTM com `|K−F₀|/F₀ ≤ 15%`, `F = K₀ − (C−P)_{K₀}/slope`.
*Sanidade:* `F` OTM-only vs ITM-only não pode divergir mais de 0,5% do spot com sinal sistemático.

### 6.3 IV e gregas
Black-76 sobre `(F, K, T, DF, σ)`, inversão por Brent em `[1e-4, 5]`.
*Sanidade:* recomputar com `r ± 100 bp`; `max|ΔIV| < 0,25`.

### 6.4 ATM 30d
Por vencimento: IV no strike `K = F` por interpolação de variância total `w = σ²T` em `log(K/F)`.
Entre vencimentos: interpolar `w` linearmente em `T`, avaliar em `T = 30/252`.
*Sanidade:* nenhum salto > 2 vol points em dia de rolagem.

### 6.5 Realizada
Yang-Zhang sobre OHLC, janelas de 20, 30 e 60 sessões, base 252. Detectar gap overnight compatível com provento e excluir da janela.
**Desdobramento/grupamento:** o fator vem de `core.corporate_events` (§6.6), detectado pelo ajuste de strike das opções — não por heurística de retorno. **Ajustar a série do subjacente pelo fator** antes de qualquer RV. Sem isso, um desdobramento 1:3 gera três meses de RV falsa.
*Rede de segurança:* return overnight `|r| > 40%` sem evento correspondente → flag `unexplained_gap` no `health` (não ajusta nada sozinho).

### 6.6 Provento implícito no strike
A classificação **não** pode usar um limiar de razão. Medido no COTAHIST 2026: provento grande sobre strike baixo dá razão de até 0,51 numa série (desconto constante em R$ sobre strike pequeno), e eventos de provento chegam a razão mediana 0,90 — a mesma faixa de uma bonificação de 10% (0,909).

A assinatura que separa os dois está **entre strikes do mesmo subjacente, na mesma data**:

| Assinatura | Classe | Efeito |
|---|---|---|
| `Δstrike` igual em todos os strikes (±1 centavo de arredondamento) | provento | `dividends_implied`: `amount = Δstrike × fator` |
| razão `strike_novo / strike_antigo` igual em todos os strikes (±0,1%) e `Δstrike` variando | evento de escala (desdobramento, bonificação, grupamento) | `corporate_events` com `factor = razão`; flag `scale_event`; **não** emite dividendo |
| as duas cabem (provento pequeno, razão > 0,99) | provento | — |
| nenhuma cabe, ou só 1 strike observado | indeterminado | `leg_ambiguity`; tentar a mesma data em outras séries do subjacente |

Regras de amostra:
- **Só mudanças contíguas** entram na classificação: a série precisa ter negociado no pregão anterior. O COTAHIST só lista série com negócio, e 70% das mudanças de strike em 2026 aparecem depois de uma lacuna — podem acumular mais de um evento, e **a data observada não é a data ex**.
- `ex_date` = primeira data com mudança contígua no subjacente. Mudança não contígua é atribuída aos eventos já classificados; se não fechar com a soma deles → `leg_ambiguity`.
- Validar contra `strike_adj_ind`; divergência → `leg_ambiguity`. *(Em 2026 o campo é sempre `0`: não há o que validar — AGENTS.)*
- Em 2026, das 274 datas-evento contíguas com ≥ 2 strikes: 268 provento, 3 escala, 3 indeterminadas (BBDC, variação de 2–3 centavos entre strikes); mais 13 com um só strike.
  *Nota de 23/11/2026 (contagem fechada na auditoria do C1):* o retrato acima é do arquivo até **22/10/2026**. Com o ano até 19/11 são **313 eventos** = 296 provento + 2 escala + 15 indeterminados (299 com ≥ 2 strikes, 14 com um só). A reconciliação item a item está no `AGENTS.md`, armadilha 2.

### 6.7 VRP
`vrp = iv_atm_30d − rv30` (tenor casado). `vrp_z = (vrp − média252) / desvio252`.

### 6.8 Skew
`RR25 = iv(25Δ call) − iv(25Δ put)` · `BFLY25 = ½·(iv25c + iv25p) − iv_atm`.
Mínimo de 5 strikes entre 20Δ e 80Δ; abaixo disso `null` + `sparse_chain`.

### 6.9 Liquidez e classes de sinal

**Série líquida:** `trades ≥ 5` em 5 sessões **e** `spread_pct ≤ 10` **e** `bid > 0` (e `ask > 0`).
**Papel no funil:** volume financeiro médio 20d `≥ R$ 5 M`.
`days_to_liquidate = oi / média de quantidade (ações) 20d` (10 primeiros dias úteis) — o OI vem em ações do ativo-objeto, não em reais (ADR-020).

Classes de sinal — **gravadas em `metrics_underlying.signal_class`, mas fora do produto** (ADR-023/024: o portão F1b não passou). Nenhuma tela, rota, tool ou relatório as exibe ou filtra por elas; ficam como registro do portão. Reintroduzir exige novo ADR e novo portão pré-registrado fora da amostra:

| Classe | Regra |
|---|---|
| `premium_rich` | `vrp_z ≥ +1,5` **e** `vrp ≥ +4` |
| `premium_cheap` | `vrp_z ≤ −1,5` **e** `vrp ≤ −4` |
| `skew_anomaly` | `|rr25 − mediana252| > 2σ252` e grade com ≥ 5 strikes |
| `event_pressure` | `iv_atm(front) > iv_atm(back) + 2` |
| `none` | nenhuma das acima |

~~Classe com ≠ `none` é rara por construção… o ranking mostra a classe como coluna.~~ **Suspenso (ADR-024):** o `screen` é uma tabela descritiva em ordem alfabética, sem classe, sem nota e sem ranking.

**Resultado próximo:** se há data em `core.earnings` antes do vencimento de referência, a linha ganha `earnings_before_expiry: true`, a data e o `kind` (`actual` ou `estimated`, exibido como "previsto"). Não muda a classe — é o primeiro item de contexto do briefing.

### 6.10 Expected move e carry
`straddle_atm / spot` → `exp_move_1σ ≈ 1,25 × straddle/spot` (por vencimento; 30d para a métrica do papel).
`implied_div_yield = r − ln(F/S)/T`.

Densidade neutra ao risco e PoP ficam **fora da v1**: a segunda derivada numa grade esparsa da B3 é ruído com cara de número (ver `BACKLOG.md`).

### 6.11 Validadores (filtram, não informam)
Butterflies ≥ `−tolerância`; variância total não-decrescente no mesmo strike; call decrescente / put crescente em K. Falha → `inconsistent`. **Revisto pelo ADR-020:** a flag é informativa — a série **continua no ATM** (que precisa dos dois vizinhos de F) e sai só do skew/butterfly; tolerâncias relativas à variância ATM (`butterfly_tol = 0,25`, monotonicidade 1 vol point, só em `|ln(K/F)| ≤ 0,10`).

### 6.12 Previsão estruturada (avaliável)
O diário não aceita texto livre como previsão. Schema:

```yaml
metric: iv_atm_30d | vrp | rr25 | spot | ...
operator: "<" | "<=" | ">" | ">="
value: 26.0
horizon_sessions: 5
evaluate_on: 2026-11-26
status: pending | hit | miss | void
observed: null
```

O batch avalia no dia seguinte a `evaluate_on` e grava `observed`/`status`. Sem isso, a calibração pessoal seria manual para sempre.

### 6.13 Backtest das classes de sinal
> **Status (23/11/2026):** executado — **NÃO PASSA** nas duas classes (ADR-023, parecer independente APROVADO). A margem negativa do `premium_rich` vem do choque real de mercado de 2026-10-26 (ADR-034). O produto parou no portão e segue descritivo (ADR-024). O texto abaixo é o desenho pré-registrado, mantido como registro.
>
> **Descasamento de prazo (registrado depois):** `iv_atm_30d` é IV de **30 dias úteis** (`T = 30/252`, ≈ 6 semanas), e o desfecho é a RV dos **20** pregões seguintes. O próprio glossário diz que tenores diferentes "fabricam VRP". Como foi pré-registrado, o veredito não se refaz nesta amostra; **qualquer estudo futuro casa os prazos** (IV de N pregões contra RV dos N pregões seguintes).

Pergunta: quando a classe disparou, a vol entregue depois confirmou o preço?

- Para cada `(papel, data)` do histórico (2024–2026, após as 252 sessões de aquecimento do z): `rv_fwd20` = Yang-Zhang das **20 sessões seguintes**, ajustada por evento de escala.
- Desfecho: `edge = iv_atm_30d(data) − rv_fwd20`. Para `premium_rich`, acerto = `edge > 0`; para `premium_cheap`, acerto = `edge < 0`.
- Relatório por classe e por papel: `n`, taxa de acerto, mediana e P10 de `edge`, comparados com a classe `none` (base).
- **Sem look-ahead:** o z na data usa só dados até a data; a validação embaralha o futuro e exige que o resultado mude (feito por script no parecer do F1b; não há suíte — ADR-019).
- **Sem otimização:** os limiares da §6.9 são fixados *a priori*. Mudar limiar exige justificativa escrita no `AGENTS.md` e um período fora da amostra.
- ~~Exposto como `signal_backtest(signal_class, ticker=None)`; o briefing e o agente citam a taxa histórica da classe.~~ **Não exposto** (ADR-027): existe só como `b3o backtest` (registro do portão).

### 6.14 Calendário de resultados (sem manutenção manual)
Fonte única: **dados abertos da CVM** (`dados.cvm.gov.br`), gratuitos, sem token.

- **Histórico (`kind = actual`):** índices anuais do ITR e da DFP (`itr_cia_aberta_AAAA`, `dfp_cia_aberta_AAAA`, desde 2010/2011). A data de resultado é a **data de entrega da primeira versão** do documento para cada data de referência. Em companhia listada, o ITR/DFP é entregue no dia da divulgação (em geral após o fechamento) — logo, o pregão afetado é o seguinte.
- **Futuro (`kind = estimated`):** a mesma data de referência do ano anterior, deslocada para o mesmo dia da semana; limitada pelo prazo regulatório (ITR: 45 dias após o trimestre; DFP: 3 meses após o exercício). Exibida como "previsto", nunca como data confirmada.
- **Confirmação opcional:** documentos do IPE (categoria "Calendário de Eventos Corporativos" ou comunicados) trazem a data anunciada, mas em PDF não estruturado — fora da v1; o campo `kind` já comporta `announced` se entrar depois.
- **Papel ↔ empresa:** FCA da CVM (valores mobiliários, com código de negociação) liga `PETR4` ao código CVM / CNPJ. Papel sem correspondência → `earnings = null` + flag `earnings_unmapped`.
- **Backtest:** a data `actual` permite medir se `event_pressure` e `premium_rich` antes de resultado se comportam diferente — entra como recorte no relatório do §6.13.
- *A confirmar no F0:* nomes exatos dos campos (a expectativa é `DT_RECEB`, `DT_REFER`, `VERSAO`, `CD_CVM` no ITR/DFP) e a tabela de códigos de negociação no FCA.

---

## 7. `QueryService`

Uma classe, um arquivo. Nenhuma query SQL fora dela. Todas as leituras devolvem `as_of_session` e `model_version`. **Estado em 23/11:** existem `health`, `screen`, `search_tickers`, `underlying`, `chain`, `series`, `events` e `structure_eval`; `signal_backtest` **não é exposto** (ADR-027); `positions`/`position` são do F6. Menos tools, sem sobreposição: agentes escolhem pior quando duas tools respondem a mesma pergunta.

```python
health()                                                -> Health
screen(date=None, max_rows=20)                          -> list[ScreenRow]
underlying(ticker, date=None,
           sections=("summary","term","smile","history"),
           expiry=None, days=60)                        -> UnderlyingDetail
chain(ticker, expiry=None, date=None,
      delta_range=(0.25, 0.75), liquidity="strict",
      max_rows=60)                                      -> list[ChainRow]
series(ticker, maturity, days=60)                       -> SeriesDetail   # resumo + histórico (ponto com session_index, leg_id, strike_brl)
prices(ticker, days=250)                                -> PricesDetail   # só HTTP; fechamento do papel + marcas (ADR-050)
structure_eval(legs, date=None, capital=None,
               scenarios=True)                          -> StructureResult # payoff, gregas, cenários, perda máxima
events(ticker)                                          -> Events          # vencimentos, ex-dividendo, eventos de escala, resultados
# signal_backtest(signal_class, ticker=None)            -> BacktestResult  # NÃO exposto (ADR-027)
positions(date=None)                                    -> Portfolio       # posições + agregados
position(id, date=None)                                 -> PositionDetail
```

- `underlying` substitui `underlying_summary`, `term_structure`, `smile` e `iv_history`; `sections` controla o tamanho da resposta.
- `structure_eval` absorve cenários (spot × IV × dias) e orçamento de risco: perda máxima e capital comprometido. **Sem margem** — não está no dado, e aproximação errada é pior que ausência. Risco ilimitado → `max_loss_brl = null` + flag `unbounded_loss`.
- Evidência contrária à tese é trabalho do agente, com estas tools; não há função determinística para isso.

---

## 8. MCP

Streamable HTTP montado em `/mcp` no mesmo FastAPI (`127.0.0.1:8080/mcp`). Expõe as leituras **descritivas** da §7 (hoje: `health`, `screen`, `underlying`, `chain`, `series`, `events`, `structure_eval`; `positions` no F6), com os nomes canônicos (nomes antigos da doc de arquitetura — `option_chain`, `data_health`, `underlying_overview` — não existem).

`positions` (F6) existe para o agente **conhecer a exposição que você já tem**. O produto não recomenda (ADR-024); a descrição de `screen` e de `structure_eval` instruirá o agente a chamar `positions` antes de **comentar** um papel; sem isso, ele sugeriria dobrar exposição em PETR4 sem saber que você já está vendido em vol.

Regras:

| Regra | Valor |
|---|---|
| `max_rows` padrão / teto | 20 / 60 |
| Teto de resposta | 200 KB; acima, truncar com `truncated: true` |
| Erros | `{"error": {"code", "message", "hint"}}` — nunca dado parcial |
| `screen` | componentes por coluna, sem score único |
| Unidades | sempre no nome do campo (`iv_pct`, `spread_brl`, `theta_pct`) |

**Não existe tool de escrita.** O diário é HTTP-only (§9).

---

## 9. API HTTP

| Rota | Serve | Cache |
|---|---|---|
| `GET /api/health` | `health()` | 30 s |
| `GET /api/screen?date=` | aba Hoje | 60 s |
| `GET /api/tickers?q=` | busca de papel | 1 h |
| `GET /api/t/{t}?sections=&exp=` | `underlying` (cabeçalho, métricas, gráficos) | 60 s |
| `GET /api/t/{t}/chain?exp=&delta=` | cadeia | 60 s |
| `GET /api/t/{t}/events` | `events` | 1 h |
| `GET /api/t/{t}/prices?days=&offset=` | fechamento do papel por sessão do lake (escala do B2 aplicada, provento não descontado) + marcas; 60 pontos por página (ADR-050) | 60 s |
| `GET /api/s/{t}/{maturity}` | `series` (resumo + histórico) | 60 s |
| ~~`GET /api/backtest?class=&t=`~~ | não exposto (ADR-027) | — |
| `GET /api/glossary` · `GET /api/glossary/{key}` | lista e verbete; o número do dia vem da tela, não do texto (ADR-031) | 1 h |
| ~~`GET /api/briefing?date=`~~ | não existe; o relatório do dia é `b3o screen` → `data/out/` | — |
| `POST /api/structure` | `structure_eval` | — |
| `GET /api/positions` | aba Carteira (posições + agregados) | 60 s |
| `GET /api/positions/{id}` | posição com marcação, gregas e tese | 60 s |
| `POST /api/positions` | abre posição (grava journal + legs) | — |
| `POST /api/positions/{id}/close` | fecha (grava exit) | — |
| `POST /api/journal` | registra decisão (com previsão estruturada) | — |
| `GET /api/journal` | lista + desfecho | 30 s |

Gráficos recebem pontos já reduzidos (≤ 70).

---

## 10. PWA — quatro destinos e dois drill-downs

Mobile-first (412 px), breakpoints `≥ 600` (tablet, 2 colunas) e `≥ 1024` (desktop, menu lateral + tabela completa da cadeia).

**Linguagem:** o PWA tem perfil global **Expert × Leigo**, default **Expert**. A preferência fica em `localStorage` (`b3o:language`, valores `expert`/`leigo`), com leitura/escrita defensivas: ausente, inválido, corrompido ou storage bloqueado cai em Expert. A escolha sincroniza abas pelo evento `storage`, sem regravar a chave, sobrevive a reload/atualização/offline e não invalida nem refaz queries. O seletor acessível fica no header de todas as rotas. Um dicionário único de apresentação resolve rótulo, variante, ajuda curta e fallback explícito de códigos; no Leigo, o termo aparece expandido antes da sigla. `Metric`/`MetricModal` recebem a chave semântica, mas o slug do verbete continua vindo exclusivamente de `metricKeys.ts`. A ficha completa continua vindo só de `docs/glossario.md` via `/api/glossary`; os três níveis do glossário não são perfis de idioma.

Ticker e Série **não são destinos** — são navegação. Ficam fora da barra inferior, com botão de voltar, e a barra passa a ter quatro itens de verdade:

| Destino (bottom nav) | Conteúdo |
|---|---|
| **Hoje** | busca · tabela descritiva do universo (com opções / sem opções), em ordem alfabética, com motivo de exclusão · medianas · aviso de pregão dominante na RV (ADR-035). **Sem** filtro por classe, candidatos ou ranking (ADR-024) |
| **Carteira** | posições abertas · agregados (capital, P&L, Δ/Θ/ν líquidos) · tese vs realidade |
| **Diário** | lista de decisões · **formulário de registro** · previsão estruturada · desfecho |
| **Aprender** | glossário em **três níveis** (o 1 visível de saída), com ficha por métrica; a mesma ficha abre num **modal** ao tocar em qualquer métrica das outras telas |

| Drill-down (push, com voltar) | Conteúdo |
|---|---|
| **Ticker** | fechamento D-1 · 6 métricas · próximo resultado · **seletor de vencimento** · 4 gráficos · cadeia · builder de estrutura |
| **Série** | contrato, gregas, histórico com marcas de ajuste, últimos pregões |

### 10.1 Stack do frontend

| Peça | Escolha | Por quê |
|---|---|---|
| UI | **React** | ecossistema do TanStack |
| Build | **Vite** | dev server rápido, code splitting por rota, build em container Node |
| Linguagem | **TypeScript** | o contrato da API vira tipo; erro de campo aparece no editor, não no celular |
| Roteamento | **TanStack Router** (file-based) | 4 destinos + 2 drill-downs mapeiam em `routes/` |
| Dados | **TanStack Query** | cache por chave, revalidação, estado de offline explícito |
| Estilo | **Tailwind v4** | os tokens do mockup (variáveis CSS) portam direto |
| PWA | **vite-plugin-pwa** (Workbox) | manifest, precache do shell e estratégia `NetworkFirst` para `/api` |
| Lint/check | `eslint` + `tsc --noEmit` | mesma disciplina do projeto Imoveis |

**SPA pura, sem framework full-stack.** O dado é EOD e vem por `/api`; SSR não traria nada a um PWA de um usuário atrás da tailnet. TanStack Start em modo SPA foi descartado (v0.4): só acrescentava a chave de SPA que muda entre versões e a tentação de *server function*. Com Vite + Router puro, não existe servidor no frontend para alguém usar — a regra da `QueryService` única passa a ser garantida pela stack.

**Build sem Node no Windows:** o build oficial roda no container (`node:26-slim`), como no projeto Imoveis; o serviço `web` do compose gera `web/dist`, que o FastAPI serve. Para o ciclo de dev, hot reload de Vite dentro do Podman no Windows sofre com a vigilância de arquivos entre volumes: usar o serviço `web-dev` com `CHOKIDAR_USEPOLLING=1` (ou Node local via `fnm`, opcional e nunca como caminho oficial).

Regras:
- Alvo de toque ≥ 44 px; **nada depende de hover**.
- Cadeia: put · strike · call; toque expande OI/volume/IV/spread em R$; toque longo marca perna para a estrutura.
- **Builder de estrutura**: pernas escolhidas na cadeia aparecem num rodapé fixo com prêmio líquido e breakeven, alimentando `POST /api/structure`.
- Filtro de faixa (`ATM ±3`, líquidas, todas) aplicado **no servidor**.
- Carimbo `sessão AAAA-MM-DD · D-1` no topo de todas as telas.
- Contraste AA; gráficos em SVG inline (≈4 KB), sem biblioteca.
- **Camada educacional**: toque no nome de qualquer métrica abre ficha (o que é · erro comum · o número de hoje). Aba "Aprender" com os três níveis do glossário. Conteúdo em `docs/glossario.md`, servido por `/api/glossary`.
- Regra de cobertura: **nenhuma métrica aparece na tela sem verbete** — script de conferência (`npm run coverage`, F5-C) comparando os campos tocáveis com as chaves do glossário; não é suíte de testes (ADR-019).

Offline: precache do shell; *network-first* para `/api` com fallback ao cache da última sessão; banner permanente com a data; sem cache → erro explícito.

---

## 11. Operação

- `prepare` compara `ETag`/`Last-Modified` em cada fonte baixada; 304 → não baixa.
- Parse **sem assumir ordem** (o arquivo é ordenado por instrumento); ao final, reconciliar `count(distinct refdate)` e linhas por data contra o trailer.
- Ano corrente: reparsear o arquivo anual inteiro e **substituir a partição** do ano. Anos fechados são imutáveis. Escrita atômica: `tmp/` → `os.replace`; nunca substituir antes de validar.
- Se uma data já carregada mudar de conteúdo (sha por data em `ingest_state`), registrar em `health` — a substituição já cuidou do dado.
- `model_version` sobe a cada mudança de fórmula e o histórico é **recalculado inteiro**, sobrescrevendo as tabelas derivadas (não há `model=vN/` nem ponteiro — o rank nunca mistura modelos).
- **Validação (sem suíte de testes — ADR-019):** cada fórmula e o parser são conferidos **no dado real** e por um caminho independente (contagem por fora, valor recalculado à mão); a medição fica registrada no `AGENTS.md`. Antes de qualquer troca de `model_version`, rodar o pipeline no histórico e comparar com a medição registrada.
- **Agendamento (ADR-036):** o `prepare-watch` é serviço padrão do `compose up` (`restart: unless-stopped`): roda ao iniciar e a cada hora (`--interval`, env `B3_WATCH_INTERVAL_MIN`). Ciclo sem arquivo novo (COTAHIST 304 + lake já construído com o mesmo sha256, mesmo `model_version`, sem falha de fonte pendente e `metrics_underlying` na última sessão) só confere e dorme (~0,3 s). Arquivo novo reconstrói o ano e calcula `metrics_*` das sessões novas. O `prepare` one-shot (profile `jobs`) continua para uso manual.
- **Sem Python no host:** nenhum passo do pipeline depende de `uv`/Python/Node instalados fora do container. O host precisa de Podman e `git`.
- `health()` reporta: última sessão, idade, contagem por data, `rate_source`, fontes ausentes, inconsistências, último erro.

---

## 12. Aceitação por fase

Os critérios vivem **só no `PLAN.md`**, por fase. Duplicá-los aqui fez as duas cópias divergirem na v0.3.

---

## 13. Riscos abertos

| Risco | Mitigação |
|---|---|
| PWA exige HTTPS | D11; Tailscale no F5, testado antes de a UI crescer |
| Fonte de OI é frágil (10 dias, layout próprio) | tabela separada e nullable; `days_to_liquidate` cai para `null` |
| Curva DI pode faltar | fallback em cascata + `rate_source` visível |
| Grade esparsa em papel pequeno | `sparse_chain` → `null`, nunca extrapolar |
| Divergência tela × agente | `QueryService` única + `mcp_smoke.py` comparando MCP × HTTP no dado real |
| Desdobramento tratado como dividendo | assinatura entre strikes (§6.6) + conferência com eventos reais de 2026 (SBSP ×0,2, SIMH ×2) + ajuste antes de RV (§6.5) |
| Choque de mercado lido como defeito do dado | ADR-034 (26/10/2026); aviso `rv_shock_day` na tela (ADR-035) |
| Paridade com estilos misturados | estilo por série (A8); puts todas europeias; calls americanas com ex-dividendo fora do forward e do smile |
| Sinal sem valor | **materializado**: o portão não passou (ADR-023); produto descritivo (ADR-024) |
| Universo pequeno demais para um "funil" | relatório de liquidez no F0; o produto aceita ser um painel de poucos papéis |
| Posição registrada fora da app → P&L diverge | abrir/fechar só pela app; reconciliação semanal contra o extrato da corretora |

---

## 14. Posições (F6)

O funil não pode recomendar o que você já tem. Posição é a ponte entre a decisão e o resultado.

### 14.1 Modelo

`position(id, underlying, kind, opened_at, closed_at, status, capital_committed, notes)`
`position_leg(position_id, ticker, side, type, maturity, qty, entry_price, entry_strike)`

A perna é identificada pelo `ticker` + `maturity` (a série); o strike vigente vem do leg do dia, já que a B3 o ajusta por provento. `entry_strike` é só registro.

`kind` (coberta, trava de alta, trava de baixa, straddle, put caixa…) é **derivado** das pernas, não digitado.

Regra de ouro: **posição só nasce e morre pela app** (`POST /api/positions` e `/close`). Cada evento grava uma linha no journal PostgreSQL; na abertura, a previsão estruturada (§6.12) é opcional mas pedida pelo formulário — sem ela, a posição fica fora da calibração. Registro manual fora da app é o que faz o P&L divergir da corretora.

### 14.2 Marcação e derivados (EOD, nunca live)

- Cada perna marcada pelo `mid` do dia; sem book, `close`, com flag `one_sided`.
- `pnl_brl = Σ perna (mark − entry) × qty × sinal`; `pnl_pct = pnl / capital_committed`.
- Gregas líquidas: `Δ` também em R$ de exposição (`Δ × qty × spot`), `Θ` em R$/dia, `ν` em R$/ponto.
- `breakeven[]`: raízes do payoff linear por partes no vencimento.
- `iv_at_entry` vs `iv_atual` por subjacente — é a medida direta de "a tese de vol se realizou".

### 14.3 Alertas determinísticos

| Flag | Regra |
|---|---|
| `near_expiry` | `≤ 5` dias úteis |
| `itm` | `\|Δ\| > 0,85` em alguma perna |
| `delta_drift` | `\|Δ_now − Δ_entry\| > 0,25` |
| `event_in_window` | ex-dividendo (Δstrike) ou vencimento dentro do prazo |
| `thesis_failed` | previsão vinculada com `status = miss` |
| `capital_over` | `capital_committed > limite` |

### 14.4 UI

Cartão por posição: pernas, P&L (R$ e %), Δ/Θ/ν líquidos, breakevens, dias para o vencimento, uma linha de **"tese vs realidade"** (`vrp_z na entrada → iv hoje`, previsão → observado) e as flags.
Detalhe: payoff atual e no vencimento, tabela por perna com marcação do dia.

### 14.5 Uso pelo agente

`positions()` é tool do MCP (posições + agregados), e as descrições de `screen` e `structure_eval` mandam o agente consultá-la antes de recomendar. Sem isso o agente sugere dobrar exposição que você já tem — o erro mais caro e mais fácil de cometer.
