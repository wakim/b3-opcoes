# Plano de execução — Opções B3

Documento de trabalho. A **spec** (`docs/spec.md`) é a fonte da verdade para fórmulas, schema e contratos; este plano é a **ordem de execução**, com tarefas, dependências e critérios de aceitação. **Os critérios de aceitação vivem só aqui** — a spec não os repete.

---

## Como usar

- Cada tarefa tem ID (`F1.4`), estimativa e critério. Marque `[x]` quando **o critério passar**, não quando o código rodar.
- **Nenhum comando oficial roda fora do container.** `podman compose …` é o runner; a seção *Ambiente* tem os atalhos.
- Decisão que aparecer no caminho e não couber na spec: **não pare** — escolha o padrão mais simples e reversível, registre em `docs/decisoes.md` e siga. Revisão periódica, não bloqueante.
- Uma fase só fecha quando todos os critérios da fase passarem. Se um critério falhar, ele vira tarefa da fase — não vira "pendência".
- Tarefa que descobre algo novo sobre o dado: **anote no `AGENTS.md`**. As armadilhas já registradas lá custaram uma sessão cada.
- Nada de UI antes de o número estar certo **e de o sinal provar que serve**. A tentação de "ver bonito" é o que produz gráfico de dado errado.
- As estimativas são de trabalho focado. Conte com **2–3×** no calendário.
- **Diretiva do dono (22/11/2026): o projeto não tem suíte de testes.** Onde este plano fala em `pytest`, "teste automatizado", "teste de contrato" ou "pytest verde", vale a **validação no dado real** (rodar o comando, conferir 1 número à mão por caminho independente, comparar com medição já registrada no `AGENTS.md`). `tests/` foi removido e `pytest` saiu das dependências — ver ADR-019.

---

## Estado atual

| Item | Situação |
|---|---|
| Planejamento | spec v0.5, arquitetura, mockups (mobile e desktop), glossário, direção visual, log de decisões (ADR-001…018) |
| Código | **F0 completo e aceito** (fetch+validação, lake parquet 2024–2026, legs+eventos, curva por sessão, calendário CVM, OI, liquidez, `prepare`/`health`/`rates`). **F1 a ~80%**: convenções/forward/IV/gregas, realizada+eventos, ATM/VRP/skew/dist, validadores e a plumbing do pipeline estão prontos; `b3o metrics` roda no dado real e está em **correção da rodada de revisão** (tolerância do validador, volume do papel, sanidade do forward). Sem UI. |
| Dados baixados | COTAHIST 2026 (221 sessões, 3.537.186 linhas dos 6 mercados), OI (10 sessões), curva DI (1 sessão; Selic plana em lote para o histórico), CVM ITR/DFP/FCA 2025+2026 |
| Ambiente | Podman 6.1.4 com `podman compose` (provider docker-compose v5.8); Python 3.15 e `node:26-slim` no container (ADR-011); bind `C:\` ↔ container verificado; repositório em `github.com/wakim/b3-opcoes` (SSH, ADR-016) |
| Pasta | `C:\Users\wakim\Projects\b3-opcoes` (git local, `origin` = GitHub) |

**Stack decidida:** backend Python (uv) + Parquet/DuckDB · MCP montado no FastAPI · frontend **Vite + React + TypeScript + TanStack Router/Query** (SPA pura), build em container Node, servido pelo FastAPI. **Tudo em container** (`compose.yaml`, spec §4.1); o runner no host é `podman compose`.

---

## Princípios de trabalho

1. **Validar contra o dado, não contra a intuição.** Toda dúvida sobre campo, layout ou ordenação se resolve lendo o arquivo.
2. **Determinístico no Python, julgamento no agente, decisão no humano.** Nenhum número sai de um LLM.
3. **EOD é o dado; live não existe na v1.**
4. **Uma fonte de consulta.** MCP e HTTP chamam o mesmo `QueryService`.
5. **Nenhuma métrica sem verbete.** Se entra na tela, entra no glossário.
6. **Falha explícita, nunca dado parcial.** Erro com código e dica; `null` com flag; nunca zero silencioso.
7. **Provar o sinal antes de construir em cima dele.** Há 3 anos de histórico: o backtest vem antes da UI.
8. **Validação no dado real.** O projeto **não tem suíte de testes** (ADR-019): cada fórmula é conferida à mão por um caminho independente (fonte citada ou propriedade matemática) e a medição fica registrada no `AGENTS.md`.

---

## Marcos (o que você consegue fazer ao final de cada um)

| Marco | Capacidade |
|---|---|
| **M0** | "sei quantos pregões, séries e papéis líquidos existem, e consigo provar que a conta está certa" |
| **M1** | "a IV de call e put europeias no mesmo strike bate dentro de 1 vol point, e a da PETR4 é coerente com uma referência externa" |
| **M2** | "sei, com 3 anos de dado, se `premium_rich` antecipa RV abaixo da IV" — **portão** |
| **M3** | "rodo `b3o screen` e pergunto ao agente 'vale vender put em PETR4?' com números auditáveis" |
| **M4** | "avalio uma estrutura com payoff, breakeven e perda máxima" |
| **M5** | "abro o painel no celular, instalado, via HTTPS" |
| **M6** | "vejo minha posição, o P&L e se a tese se realizou" |
| **M7** | "seis semanas depois, sei se as minhas decisões acertaram" |

---

# F0 — Ingestão e verdade do dado

**Objetivo:** o lake existe, é reprodutível, e sabemos exatamente o que tem dentro — inclusive **quanto dele é líquido**.
**Estimativa:** 2,5 sessões · **Depende de:** nada

| ID | Tarefa | Est. |
|---|---|---|
| F0.0 | `git init` + `.gitignore` (data, .venv, __pycache__, *.db) + primeiro commit dos docs | 15 min |
| F0.1 | Esqueleto `uv`: `pyproject.toml` (**com `uv.lock` versionado**), `src/b3o/`, `config.yaml`, `data/.gitkeep`, `pytest` + `tests/` | 30 min |
| F0.1b | **Stack em container:** `compose.yaml` (spec §4.1) + `Dockerfile` multi-stage (`runtime` sem deps de dev, `dev` com elas, estágio `node` para o futuro `web/dist`), serviços `app`/`test`/`dev`/`prepare`/`prepare-watch`, `./data:/data`, volumes nomeados; `podman compose config` válido e `podman compose run --rm test pytest` rodando | 2 h |
| F0.2 | `fetch.py`: download condicional (`ETag` / `If-Modified-Since` → 304), retry, timeout, cache em `data/raw/` | 1 h |
| F0.3 | `parse.py`: COTAHIST streaming, latin-1, 245 bytes, filtro de mercados (010/020/070/080/012/013), preços em centavos | 2 h |
| F0.4 | `validate.py`: trailer `99`, contagem de linhas, largura, campos numéricos | 1 h |
| F0.5 | `store.py`: Parquet particionado por `year`; **o ano corrente é reconstruído inteiro a cada `prepare`** (escrita atômica `tmp/` → `os.replace`); `ingest_state` | 1 h |
| F0.6 | `legs.py`: leg por `(ticker, maturity)` + `strike`/`strike_adj_ind`/`distribution_id`; **classificação do ajuste pela assinatura entre strikes** (Δ constante = provento, razão constante = escala, só mudanças contíguas; spec §6.6); flag `leg_ambiguity` | 2 h |
| F0.7 | estilo da opção (nome resumido: `PETRE` × `PETR`) e `FATCOT` por série, no próprio parser | 30 min |
| F0.8 | `open_interest.py`: arquivo regulatório (10 dias), join por `(refdate, ticker)`, nullable | 1.5 h |
| F0.9 | `rates.py`: curva DI (taxas referenciais → fallback DI1 → fallback Selic), `rate_source` | 1.5 h |
| F0.10 | `earnings.py`: índices ITR/DFP + FCA da CVM → `core.earnings` (`actual` histórico + `estimated` futuro; spec §6.14) | 2 h |
| F0.11 | **Relatório de liquidez:** por papel, quantos dias têm ≥ 5 strikes líquidos entre 20Δ e 80Δ (aprox. por moneyness) e ≥ 2 vencimentos líquidos | 1 h |
| F0.12 | CLI `b3o prepare` e `b3o health` | 1 h |
| F0.13 | Testes: golden file do parser (10 linhas reais), trailer, strike right-aligned, estilo pelo nome, classificação de ajuste com LOGG (provento grande), SBSP (desdobramento) e SIMH (grupamento) | 1 h |

**Aceitação F0** — parecer da revisão independente de 22/11/2026 (cada item com a evidência no `out/relatorio-sessao.md`)
- [x] `podman compose config` válido e a imagem constrói do zero (sem cache) em < 5 min — **69,4 s**
- [x] `prepare` e `pytest` rodam **só** por `podman compose`; nenhum passo documentado exige Python/Node no host — (o `pytest` foi depois revogado: ADR-019)
- [x] rodar `podman compose run --rm prepare` duas vezes seguidas produz o mesmo lake (idempotência) e a segunda roda mais rápido (304) — **170,0 s, 304, sha do parquet idêntico**
- [x] contagem de linhas por `refdate` bate com um `GROUP BY` independente feito direto no TXT — **221 datas, 3.537.186 linhas, 0 divergência**
- [x] trailer confere; nenhuma linha de largura errada passa — **trailer 3.570.794; a linha de 246 B é pulada e contada** (a convenção do trailer muda em 2024, medido)
- [x] **legs conferidas à mão em 5 tickers com ajuste conhecido** (ex.: `ABEVX131`: 12,04 → 12,00 → 11,94), incluindo **SBSP 29/04/2026 (×0,2)** e **SIMH 18/02/2026 (×2)**, que **não** aparecem em `dividends_implied`, e **LOGG 06/2026 (R$ 2,86)**, que aparece — **SBSP factor 0,2000043 / SIMH 2,0 / LOGG Δ286 ¢; escala não vira dividendo**
- [x] estilo das opções bate com o medido no `AGENTS.md` (puts 100% europeias; calls ~66/34 por série) — **puts 107.990 E / 0 A; calls 57,6% A — o 66/34 do texto era erro, corrigido no AGENTS**
- [x] OI e curva DI presentes **ou** flag explícita (`oi_unavailable`, `rate_source`) — **10 datas de OI; 722 sessões com curva (1 PRE + 721 Selic plana)**
- [x] relatório de liquidez define o **universo real** (número de papéis) e entra no `AGENTS.md` — **289 papéis, 15 com grade líquida**
- [x] `core.earnings` cobre todos os papéis do universo líquido; datas `actual` de 2025 conferidas à mão em 3 papéis (RI da empresa); `estimated` de 2026 conferida contra a data que de fato saiu — **13/15 (os 2 são ETF); VALE3 24/04, ITUB4 08/05, PETR4 12/05 de 2025 conferidos; `estimated` de 2026 é só de 2027 e não é conferível (o actual substitui)**
- [x] `pytest` verde — **critério revogado pelo dono em 22/11 (ADR-019: sem suíte); na data da revisão eram 523 verdes**
- [x] `health` mostra: última sessão, idade, contagem por data, fontes, último erro — **20 campos, idade contra a sessão esperada (D-1), 165 gaps inexplicados**

**Entregável:** `b3o prepare` roda de ponta a ponta, `health` responde e sabemos o tamanho do universo útil.

---

# F1 — Derivadas essenciais

**Objetivo:** só o necessário para o sinal: forward, IV, gregas, ATM 30d, RV, VRP, z, skew.
**Estimativa:** 2,5 sessões · **Depende de:** F0

| ID | Tarefa | Est. |
|---|---|---|
| F1.1 | `conventions.py`: T em dias úteis/252, `DF`, interpolação log-DF, arredondamento só na serialização | 30 min |
| F1.2 | `forward.py`: paridade OTM, **preferindo pares europeus**; crossing strike + fallback OLS | 2 h |
| F1.3 | `iv.py`: Black-76 + Brent vetorizado (numpy), limites e falhas | 2 h |
| F1.4 | `greeks.py`: Δ/Γ/Θ/ν/ρ em unidade interpretável (`theta_brl`, `theta_pct`, `vega_brl`) | 1 h |
| F1.5 | `atm.py`: ATM por vencimento em variância total + interpolação para 30d | 1.5 h |
| F1.6 | `realized.py`: Yang-Zhang 20/30/60; exclusão de gap de provento; ajuste por `corporate_events` | 2 h |
| F1.7 | `vrp.py`: VRP, z-score 252, IV rank/percentil | 1 h |
| F1.8 | `skew.py`: RR25, BFLY25, `sparse_chain` | 1 h |
| F1.9 | `dist.py`: expected move e dividendo implícito (**sem RND/PoP**) | 1 h |
| F1.10 | `checks.py`: butterfly ≥ −tol, calendário, monotonicidade | 1 h |
| F1.11 | Testes: Black-76 contra valores de referência, paridade, inversão IV→preço→IV, Yang-Zhang em série sintética | 1 h |
| F1.12 | **Validação de IV sem fonte paga:** (a) call europeia × put europeia no mesmo strike/vencimento; (b) PETR4 contra o sandbox gratuito da brapi (sem token), convertendo a convenção de tempo | 1.5 h |

**Aceitação F1**
- [ ] pares call E × put E, mesmo strike e vencimento, ATM ±10%, com negócio nas duas: mediana de `|IV_call − IV_put|` ≤ 0,5 e P90 ≤ 1,5 vol point em PETR4, VALE3, BBAS3, ITUB4, B3SA3 (a paridade entre europeias é independente de modelo: divergência = forward, taxa ou preço errado)
- [ ] PETR4: IV das séries europeias líquidas a ±2 vol points da brapi depois de converter a variância total para T em dias úteis/252 (a brapi usa dias corridos/365 e dividendo anunciado; diferença sistemática registrada no `AGENTS.md`)
- [ ] forward OTM-only vs ITM-only ≤ 0,5% do spot
- [ ] `r ± 100 bp` → `max|ΔIV| < 0,25`
- [ ] ATM 30d sem salto > 2 vol points em dia de rolagem
- [ ] RR25 da PETR4 **negativo** (puts mais caras) em ≥ 80% dos dias
- [ ] nenhuma série marcada `inconsistent` entra no smile
- [ ] `pytest` verde

**Entregável:** `b3o metrics --date 2026-11-19` grava `metrics_underlying` e `metrics_series`; `b3o metrics --backfill` recalcula 2024–2026.

---

# F1b — Backtest do sinal (portão)

**Objetivo:** responder, antes de qualquer UI, se as classes de sinal têm valor.
**Estimativa:** 1 sessão · **Depende de:** F1

| ID | Tarefa | Est. |
|---|---|---|
| F1b.1 | `backtest.py`: para cada `(papel, data)` com classe ≠ `none`, medir RV realizada nos 20 dias seguintes vs IV ATM 30d na data (spec §6.13) | 2 h |
| F1b.2 | Mesmo cálculo para `none` (base de comparação) e por papel | 1 h |
| F1b.3 | Relatório: n, taxa de acerto, mediana de `iv − rv_fwd`, pior caso, por classe | 1 h |

**Aceitação F1b**
- [ ] sem look-ahead: teste que embaralha o futuro e mostra que o resultado muda
- [ ] relatório gerado para 2024–2026, por classe e por papel
- [ ] **decisão registrada no `AGENTS.md`**: o sinal segue, muda de limiar (com justificativa *a priori*, não otimizada) ou é trocado. Se não há sinal, F2 em diante é repensado antes de continuar.

**Entregável:** `b3o backtest` e `out/backtest.md`.

---

# F2 — Funil

**Objetivo:** do universo líquido para um ranking curto, com motivo.
**Estimativa:** 1 sessão · **Depende de:** F1b

| ID | Tarefa | Est. |
|---|---|---|
| F2.1 | liquidez de papel e de série + `days_to_liquidate` | 1 h |
| F2.2 | classes de sinal (`premium_rich`, `premium_cheap`, `skew_anomaly`, `event_pressure`) | 1 h |
| F2.3 | ranking com **componentes** (sem score opaco), incluindo papéis sem sinal | 1 h |
| F2.4 | `b3o screen` + briefing Markdown por papel, com **resultado próximo** (`core.earnings`, real ou previsto) e taxa histórica da classe (F1b) | 1.5 h |

**Aceitação F2**
- [ ] todo papel do universo líquido aparece: com classe, ou `none`, ou excluído com motivo
- [ ] número de papéis com classe ≠ `none` por dia é **consistente com a frequência medida no F1b** (sem meta fixa)
- [ ] cada linha traz IV ATM, rank, VRP, z, RR, exp. move e próximo resultado
- [ ] revisão manual de uma semana de briefings sem absurdo (strike incoerente, IV negativa, papel trocado)

**Entregável:** `b3o screen --date X` imprime o ranking e escreve `out/screen-X.md`.

---

# F3 — MCP mínimo

**Objetivo:** o agente já consome o funil, antes de existir tela.
**Estimativa:** 0,5 sessão · **Depende de:** F2

| ID | Tarefa | Est. |
|---|---|---|
| F3.1 | FastAPI mínimo com MCP Streamable HTTP montado em `/mcp` (`127.0.0.1:8080`), sobre `QueryService` | 1.5 h |
| F3.2 | tools `health`, `screen`, `underlying`, `chain`, `series`, `events`, `signal_backtest` com descrição "use quando / não use para", `max_rows`, teto de 200 KB | 1.5 h |
| F3.3 | teste de contrato: MCP e HTTP devolvem o mesmo número | 30 min |

**Aceitação F3**
- [ ] agente conectado responde "vale vender put em PETR4?" citando campos que existem nos retornos e a taxa histórica da classe
- [ ] nenhuma tool devolve > 60 linhas
- [ ] erro sempre no formato `{error:{code,message,hint}}`

---

# F4 — Estruturas

**Objetivo:** transformar "prêmio caro" em estrutura avaliada.
**Estimativa:** 1 sessão · **Depende de:** F3

| ID | Tarefa | Est. |
|---|---|---|
| F4.1 | `structure_eval`: payoff, breakevens, prêmio líquido, gregas agregadas, faixa mid±½ spread | 2 h |
| F4.2 | cenários spot × IV × dias dentro do `structure_eval` | 1 h |
| F4.3 | perda máxima e capital comprometido (**sem margem**) dentro do `structure_eval` | 30 min |
| F4.4 | tool `structure_eval` no MCP + `b3o struct` | 30 min |
| F4.5 | Testes: payoff e breakeven das 5 estruturas abaixo | 30 min |

**Aceitação F4**
- [ ] payoff e breakeven conferem à mão em 5 estruturas (coberta, put caixa, trava alta, trava baixa, straddle)
- [ ] perda máxima correta em estruturas de risco limitado; `null` + flag `unbounded_loss` nas demais

**Entregável:** `b3o struct --underlying PETR4 --legs "..."`

---

# F5 — API + PWA

**Objetivo:** o painel no celular, instalado, via HTTPS.
**Estimativa:** 4 sessões · **Depende de:** F4

**Design:** a UI é construída **com** o **unslop** (skill em `~/.config/opencode/skills/unslop`, `github.com/heydamir/unslop`, CC BY 4.0), não "unslopada" depois. A direção aprovada (`docs/direcao.md`, A · Bancada fria) é o contrato: componente que não segue a direção está errado, não "com outro gosto". Os 9 passes entram na construção de cada tela, com auditoria e checklist no fim de cada uma.

| ID | Tarefa | Est. |
|---|---|---|
| F5.0 | Scaffold `web/`: **Vite + React + TS + TanStack Router (file-based) + TanStack Query + Tailwind v4**; serviços `web` (build one-shot) e `web-dev` (Vite) no compose | 1.5 h |
| F5.1 | FastAPI: `/api/health`, gzip, cache HTTP, `X-App-Token` nas escritas, estático de `web/dist` | 1.5 h |
| F5.2 | rotas do backend (screen, tickers, ticker, chain, series, glossary, structure) | 2 h |
| F5.3 | Router: 4 destinos + 2 drill-downs + sheet de métrica | 2 h |
| F5.4 | cliente tipado da API (`web/src/lib/api.ts`) + TanStack Query (cache, erro, offline) | 1.5 h |
| F5.5 | componentes base (cartões, régua, cadeia put·strike·call, gráficos SVG) aplicando os 9 passes do unslop | 4 h |
| F5.6 | builder de estrutura (toque nas pernas → prêmio e breakeven) | 1.5 h |
| F5.7 | PWA com `vite-plugin-pwa`: manifest, precache do shell, `NetworkFirst` em `/api`, offline com carimbo | 2 h |
| F5.8 | Dockerfile multi-stage (Node build → imagem final Python) — já existe desde o F0.1b; aqui entra o estágio `web` | 1.5 h |
| F5.9 | `tailscale serve` + instalação no celular + teste de offline | 1 h |

**Aceitação F5**
- [ ] instalado no celular pelo domínio `*.ts.net` (HTTPS)
- [ ] `npm run build` roda **dentro do container** (`podman compose run --rm web`) e o FastAPI serve o `dist`
- [ ] `npm run lint` e `tsc --noEmit` limpos; os tipos da API vêm de um único arquivo de contrato
- [ ] offline mostra a última sessão com o carimbo correto
- [ ] busca de papel e seletor de vencimento funcionando
- [ ] nada depende de hover; alvo de toque ≥ 44 px
- [ ] teste de cobertura: campo da API ↔ chave do glossário

---

# F6 — Posições

**Objetivo:** o funil e o agente saberem o que você já tem.
**Estimativa:** 1,5 sessão · **Depende de:** F5

| ID | Tarefa | Est. |
|---|---|---|
| F6.1 | `position`/`position_leg` + `POST /api/positions` e `/close` gravando no `journal.db` (previsão opcional) | 2 h |
| F6.2 | marcação EOD, P&L, gregas líquidas, breakevens | 2 h |
| F6.3 | alertas determinísticos (spec §14.3) | 1 h |
| F6.4 | tools `positions` e `position` no MCP | 30 min |
| F6.5 | tela Carteira + detalhe da posição com payoff | 2 h |
| F6.6 | reconciliação com o extrato da corretora (manual, 2 posições) | 1 h |

**Aceitação F6**
- [ ] P&L confere com a corretora em 2 posições
- [ ] Δ/Θ/ν líquidos batem com a soma das pernas
- [ ] "tese vs realidade" mostra previsão, observado e status quando houver previsão
- [ ] agente, perguntado sobre um papel em que há posição, cita a exposição existente

---

# F7 — Diário e calibração pessoal

**Objetivo:** medir se **as suas decisões** acertam (o sinal já foi medido no F1b).
**Estimativa:** 1 sessão · **Depende de:** F6

| ID | Tarefa | Est. |
|---|---|---|
| F7.1 | decisões sem posição (ex.: "não entrei") no mesmo `journal.db` + avaliação automática da previsão no batch | 1.5 h |
| F7.2 | formulário de registro no mobile | 1.5 h |
| F7.3 | relatório de calibração: acerto das decisões por classe de sinal, comparado com a taxa do F1b | 1.5 h |

**Aceitação F7**
- [ ] 30 dias de diário sem lacuna
- [ ] ≥ 10 previsões avaliadas automaticamente
- [ ] relatório compara acerto pessoal × taxa histórica do sinal

---

## Grafo de dependências

```
F0 ──> F1 ──> F1b ──> F2 ──> F3 ──> F4 ──> F5 ──> F6 ──> F7
              portão
```

O F1b é o portão: se o sinal não se sustenta, nada depois dele é construído sem revisão.

---

## Definition of Done (toda tarefa)

- [ ] roda no dado real de 2026, não só em fixture
- [ ] roda dentro do container (`podman compose run --rm …`), sem depender de nada instalado no host
- [ ] fórmula e parser com **1 número conferido à mão** por caminho independente (não há suíte de testes — ADR-019)
- [ ] falha de forma explícita (erro ou `null` + flag), nunca zero silencioso
- [ ] `health` não mente sobre a idade do dado
- [ ] se mudou fórmula → `model_version` subiu, testes atualizados, recálculo documentado
- [ ] se descobriu algo sobre o dado → anotado no `AGENTS.md`

---

## Validação manual (complementa os testes)

| O quê | Como |
|---|---|
| Contagem de linhas | `GROUP BY refdate` no TXT vs no Parquet |
| Legs | 5 tickers com ajuste conhecido + 1 evento de escala |
| IV | paridade call E × put E; PETR4 vs brapi (sandbox) |
| Forward | OTM-only vs ITM-only, mesma data |
| ATM 30d | série de 60 dias, procurar descontinuidade em rolagem |
| Payoff | desenhar no papel e comparar com `structure_eval` |
| P&L | extrato da corretora vs Carteira |

---

## Ambiente

Tudo pelo compose. `podman` aqui é só o motor; o `compose.yaml` é padrão e roda em `docker compose` também (ver `docs/decisoes.md`, ADR-001).

```powershell
cd C:\Users\wakim\Projects\b3-opcoes

podman compose config                                    # valida o compose
podman compose build                                     # imagem runtime + dev
podman compose run --rm test b3o health                     # saúde do lake (o serviço `test` é o container de ferramentas; não há testes — ADR-019)
podman compose run --rm test ruff check src                  # lint
podman compose run --rm prepare                          # baixa e deriva
podman compose run --rm app b3o health                    # saúde do lake
podman compose up -d app                                  # 127.0.0.1:8080 (HTTP + /mcp)
podman compose --profile watch up -d prepare-watch         # prepare diário 21:30 BRT
podman compose --profile dev up dev                        # backend com --reload
```

| Variável | Uso |
|---|---|
| `B3_DATA_DIR` | raiz do lake no container (`/data`, bind de `./data`) |
| `APP_TOKEN` | token das escritas |
| `TZ` | `America/Sao_Paulo` no container (só log; a data vem do arquivo) |

Porta: `8080` (app + `/mcp`), publicada em `127.0.0.1`, exposta só pela tailnet.

**Diagnóstico rápido** (quando o container "não vê" um arquivo): `podman machine ssh` e conferir o mount; no Windows os arquivos do bind aparecem como `nobody:root`, o que é esperado.

---

## Riscos do plano

| Risco | Sinal | Ação |
|---|---|---|
| F0 virar uma semana | contagem não fecha | parar tudo e resolver; sem isso nada acima vale |
| Universo pequeno demais | relatório F0.11 com < 8 papéis | aceitar: o produto é um painel de poucos papéis, não um funil |
| Sinal sem valor | F1b sem diferença entre classe e `none` | rever o sinal antes de F2; não otimizar limiar no mesmo dado |
| Curva DI indisponível | `rate_source` = fallback | seguir com Selic e marcar; revisar em F1 |
| Grade esparsa demais | maioria `sparse_chain` | reduzir universo a papéis com ≥ 8 strikes |
| PWA não instalar | sem service worker | checar HTTPS e `manifest` antes de seguir |
| Escopo crescer | tarefa fora do plano | vai para `BACKLOG.md` |
| Bind mount do Windows degradar a ingestão | `prepare` acima de ~15 min só para 2026 | ler em streaming, testar `podman machine set --cpus/--memory`; última instância: volume nomeado para `raw/` com cópia |
| Container sem rede para B3/CVM | `fetch` com timeout e nenhum download | testar `podman run --rm alpine wget` antes de escrever parser |

---

## Anti-objetivos (não fazer agora)

- Intraday, book em tempo real, WebSocket, **qualquer preço vivo**
- Fundamentos, notícias (o calendário de resultados entra só como data, derivada da CVM)
- Densidade neutra ao risco, PoP, prêmio de exercício antecipado
- Múltiplos usuários, permissões, billing
- App nativo, biblioteca de gráfico (os gráficos continuam em SVG)
- SSR ou framework full-stack no frontend
- Otimização de parâmetros do funil no mesmo dado do backtest
