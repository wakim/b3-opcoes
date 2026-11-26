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
| Planejamento | spec v0.6, arquitetura, mockups (mobile e desktop), glossário, direção visual, log de decisões (ADR-001…047) |
| Código | **F0 completo e aceito** (fetch+validação, lake parquet 2024–2026, legs+eventos, curva por sessão, calendário CVM, OI, liquidez, `prepare`/`health`/`rates`). **F1 aceito com ressalvas** — `model_version=2`, 722 sessões / 7,77 M séries (ADR-018/020/021). **F1b: o portão NÃO PASSA** e o dono ratificou a Opção 1 — parar no portão (ADR-023); parecer independente APROVADO. **F2 descritivo entregue e aceito** (ADR-024): `b3o screen` grava `data/out/screen-<data>.md` com os 289 papéis do universo (163 `ok` + 126 excluídos com motivo em 19/11), sem classe de sinal. **F3 entregue** (ADR-025/026/027): `b3o.api:app` sobe HTTP + MCP em `/mcp` com `health`, `screen`, `underlying`, `chain`, `series`, `events`, envelope `as_of_session`/`model_version`, `max_rows` 20/60 e teto de 200 KB. **F4 entregue** (ADR-028): `structure_eval` descritivo (payoff, breakevens, perda/ganho máximo, capital comprometido, gregas e 45 cenários) puro em `derive/structure.py` + `QueryService.structure_eval`, exposto em `POST /api/structure`, tool `structure_eval` e `b3o struct`; nada é gravado e `model_version` segue 2. **F5 entregue e aceito**: PWA (Hoje, Ticker, Série, Aprender, builder de estrutura, liquidez) servido pelo FastAPI, instalado no celular via `tailscale serve` 8443 (só tailnet) e offline pelo service worker. **F6 implementado e validado** (ADR-041/045): journal PostgreSQL append-only, carteira com marcação EOD, P&L, gregas, alertas e payoff; aceitação no uso real (ADR-047). **F7 implementado e validado** (ADR-042): Diário de decisões com previsão estruturada, avaliação automática no lote EOD e calibração pessoal; aceitação no uso real (ADR-047). **F8 entregue e aceita** (ADR-043/044/046/047): interface em shadcn + Tailwind, CSS global reduzido a tokens, base e as exceções da ADR-046; avaliação manual do dono aprovada em 25/11/2026. |
| Retomar em | **F8 aceita; começar a usar F6/F7 (ADR-047).** (1) **Diário todo pregão**: uma decisão real por sessão na app da 8083/8443 (inclusive "não entrei"), com previsão estruturada quando houver tese — a aceitação F7 conta 30 sessões consecutivas e ≥ 10 previsões avaliadas pelo lote. (2) **Duas posições reais** registradas na Carteira na data da execução, e o P&L conferido com o extrato da corretora (F6.6). A F9 está implementada, com validação final pendente; defeito achado no uso vira correção pontual. |
| Novo pedido | **F9 — níveis de linguagem Expert/Leigo**, pedido do dono em 24/11/2026. Implementação concluída; validação final pendente. É apresentação, não altera dado, cálculo ou a aceitação de uso de F6/F7. |
| Benchmark externo | **F10 — benchmark de métricas**, pedido do dono em 25/11/2026, revisado em 26/11 para cobrir **todas** as métricas de mercado. **F10.1 verificado** (skew do OBM pendente), **serviço `bench` concluído** (`826690e`) e mecanismos P3/P4/P5/P7/P6 em construção, sem captura, comparação ou relatório final. Fontes: OBM, opcoes.net.br e a calculadora do B3Lab; a execução futura exige a mesma sessão do lake. |
| Dados baixados | COTAHIST 2024–2026 (2026: 221 sessões, 3.537.186 linhas dos 6 mercados; 722 sessões no total), OI (10 sessões), curva DI (722 sessões: 1 PRE + 721 Selic plana), CVM ITR/DFP/FCA 2025+2026 |
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
| **M3** | "rodo `b3o screen`/`b3o health` e pergunto ao agente o que o dado diz sobre PETR4 — números auditáveis, sem alegação de edge" |
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
| F0.1 | Esqueleto `uv`: `pyproject.toml` (**com `uv.lock` versionado**), `backend/src/b3o/`, `config.yaml`, `data/.gitkeep`, `pytest` + `tests/` (removidos no ADR-019) | 30 min |
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

**Aceitação F1** — parecer da revisão independente de 23/11/2026 (`docs/relatorios/2026-11-23-revisao-f1.md`); ratificações em `docs/decisoes.md` (ADR-018, 019, 020, 021). Recálculo inteiro em `model_version=2`: 722 sessões, 7.767.407 séries, idempotente (sha estável).

- [x] pares call E × put E, mesmo strike e vencimento, ATM ±10%, com negócio nas duas — **ratificado como evidência descritiva, não gate (ADR-018)**: n/mediana/P90 em 19/11, 13/11, 15/10 = 137/1,137/4,459 · 166/0,966/5,713 · 181/1,789/12,117 vol points. O alvo original (≤0,5/≤1,5) é fisicamente inatingível com tick de 1 ¢; a par da camada líquida (book+spread≤5%) tem n≈0–21 e não serve de gate. O que o critério quer provar é provado pela conferência à mão: 125 IVs PETR4 por bissecção própria, max `1,97e−14` em fração, forward idêntico ao gravado.
- [x] PETR4 vs brapi — **N/A (P-J)**: `brapi.dev/api/v2/options/PETR4` devolve 401 sem token, confirmado em 23/11; nenhuma referência inventada.
- [x] forward OTM-only vs ITM-only ≤ 0,5% do spot — Δ **0,141%** do spot (19/11, vencimento 19/11). Nos outros 3 vencimentos medidos não houve ITM válido.
- [x] `r ± 100 bp` → `max|ΔIV| < 0,25` — **0,199** na banda validada `|ln(K/F)| ≤ 0,10` (n=68; em `|K−F|/F ≤ 5%` é 0,053, n=34). O medidor oficial também reporta o bruto 7,196, dominado por opções a 67–82% de moneyness, fora do domínio usado pelo smile.
- [x] ATM 30d sem salto > 2 vol points em dia de rolagem — **reclassificado como informativo (ADR-021)**: 7 saltos em dias de rolagem em 2026 (max +4,78 em 22/07); 26 saltos se contados todos os dias. Ruído de preço de fechamento, não erro de interpolação.
- [x] RR25 da PETR4 negativo em ≥ 80% dos dias — **reclassificado como informativo (ADR-021)**: 72/143 = **50,3%**, mediana −0,007 em 2026; em 19/11 = −1,60. O sinal existe no dia, não é persistente no fechamento.
- [x] nenhuma série marcada `inconsistent` entra no **skew/butterfly** — reescrito de "entra no smile" para refletir o ADR-020: a flag fica no ATM (que precisa dos dois vizinhos de F) e sai só do skew. 1.408/14.000 séries em 19/11.
- [x] `pytest` verde — revogado em 22/11 (ADR-019); substituído por validação no dado real e `ruff check src` limpo.

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
- [x] sem look-ahead: permutação do `rv_fwd20` muda o resultado (rich 42,3%→66,1%, seed 987654321); z recalculado por fora bate exato em 3 papéis × 3 datas e não vaza futuro — parecer `docs/relatorios/2026-11-23-validacao-f1b.md` (APROVADO)
- [x] relatório gerado para 2024–2026, por classe e por papel — `data/out/backtest.md` (6.773 obs, 37 papéis); reproduzido por fora com diferença 0
- [x] **decisão registrada no `AGENTS.md`**: o sinal **NÃO PASSA** (rich 42,3% / cheap 49,8%, ambos abaixo da base `none`). Dono ratificou **Opção 1 — PARAR no portão** (ADR-023, 23/11/2026). **F2 em diante suspenso**: nenhum funil/ranking construído sobre o sinal falsificado; produto pode seguir como painel descritivo sem alegação preditiva.

**Entregável:** `b3o backtest` e `out/backtest.md`.

> **Portão (23/11/2026):** o sinal de reversão do VRP não passa neste dado (acerto abaixo da base incondicional no período completo; a margem negativa do `premium_rich` vem do choque real de mercado de 2026-10-26 — leitura corrigida no ADR-034). **F2+ não começa** sem o dono repensar a tese (estudo de momentum fora da amostra, pré-registrado às cegas) ou redefinir o produto como descritivo. Ver ADR-023 e a seção "Portão F1b" do `AGENTS.md`.

---

# F2 — Painel descritivo

> **Redefinido em 23/11/2026 (ADR-023 Opção 1 + ADR-024).** O funil preditivo original está **suspenso** e fica registrado abaixo, sem execução. O F2 passa a ser um **painel descritivo**: mostra o que o dado diz (IV, RV, VRP, z, skew, liquidez, próximo resultado) **sem classe de sinal, sem taxa de acerto e sem ranking preditivo**.

**Objetivo:** do universo líquido para uma leitura diária completa, com motivo de inclusão/exclusão — descritiva, auditável, sem alegação de previsão.
**Estimativa:** 1 sessão · **Depende de:** F1 (F1b não é mais pré-requisito do descritivo, mas segue como registro do portão)

| ID | Tarefa | Est. |
|---|---|---|
| F2d.1 | `QueryService.screen(date=None, max_rows=None)` — uma linha por papel do universo líquido, só leitura sobre `metrics_underlying`/`metrics_series` + liquidez + earnings; papel excluído traz o **motivo** (`no_options`, `no_metrics`, `no_rates`, `sparse_chain`, …) | 2 h |
| F2d.2 | Qualidade da grade por papel: nº de séries líquidas no dia, vencimentos com cadeia, flags agregadas (sem transformar em nota) | 1 h |
| F2d.3 | `b3o screen --date AAAA-MM-DD [--max-rows N]`: imprime a tabela (uma tela) e grava `data/out/screen-AAAA-MM-DD.md` (P-T) com a tabela + ficha por papel (spot, ATM, RV, VRP, z, skew, exp. move, liquidez, próximo resultado `actual`/`estimated`, flags) | 2 h |
| F2d.4 | Revisão manual de uma semana de relatórios + 1 número conferido à mão por caminho independente (ex.: `iv_atm_30d` de PETR4 em 19/11) | 1 h |

**Aceitação F2 (descritivo)** — parecer independente em `docs/relatorios/2026-11-23-revisao-f2.md` (APROVADO COM RESSALVAS; ressalvas corrigidas e reconfirmadas).
- [x] todo papel do universo líquido aparece: com métricas, ou excluído com motivo explícito — **19/11: 289 = 163 `ok` + 124 `no_options` + 2 `no_trade`** (HBCR11, VXXV11); 9 papéis com métrica fora do universo listados no relatório
- [x] cada linha traz IV ATM 30d, IV rank/percentil, RV20/30/60, VRP, z, RR25, BFLY25, exp. move, `days_to_liquidate`, nº de séries líquidas e próximo resultado (`actual` = confirmado, `estimated` = previsto) — tabela de 21 colunas; ausência vira `—`
- [x] **nenhuma** coluna, rótulo ou ordenação sugere previsão; `signal_class` e taxa histórica de acerto **não aparecem** na tela nem no relatório — `grep` de `signal_class|premium|acerto|ranking|caro|barato` = **0**; ordem alfabética declarada como neutra
- [x] revisão manual de uma semana de relatórios sem absurdo — 13, 16, 17, 18 e 19/11 (`ok` 156/163/163/160/163) varridos por script independente: **0** IV negativa, rank/pct fora de 0–100, NaN ou linha ilegível
- [x] 1 número conferido à mão por caminho independente e registrado no `AGENTS.md` — PETR4 19/11: `iv_atm_30d` **31,43%**, `spot` **R$ 42,67**, **88/782** séries, **25** vencimentos; medianas IV **31,43** (n=35) e VRP **−30,66** (n=35); BOVA11 96/885/26 e VALE3 68/601/23

**Entregável:** `b3o screen --date X` imprime a tabela descritiva e escreve `data/out/screen-X.md`.

<details>
<summary>F2 original (suspenso pelo ADR-023 — registro, não executar)</summary>

**Objetivo:** do universo líquido para um ranking curto, com motivo.

| ID | Tarefa | Est. |
|---|---|---|
| F2.1 | liquidez de papel e de série + `days_to_liquidate` | 1 h |
| F2.2 | classes de sinal (`premium_rich`, `premium_cheap`, `skew_anomaly`, `event_pressure`) | 1 h |
| F2.3 | ranking com **componentes** (sem score opaco), incluindo papéis sem sinal | 1 h |
| F2.4 | `b3o screen` + briefing Markdown por papel, com **resultado próximo** (`core.earnings`, real ou previsto) e taxa histórica da classe (F1b) | 1.5 h |

**Aceitação F2 (original)**
- [ ] todo papel do universo líquido aparece: com classe, ou `none`, ou excluído com motivo
- [ ] número de papéis com classe ≠ `none` por dia é **consistente com a frequência medida no F1b** (sem meta fixa)
- [ ] cada linha traz IV ATM, rank, VRP, z, RR, exp. move e próximo resultado
- [ ] revisão manual de uma semana de briefings sem absurdo (strike incoerente, IV negativa, papel trocado)

**Entregável:** `b3o screen --date X` imprime o ranking e escreve `out/screen-X.md`.

</details>

---

# F3 — MCP mínimo (painel descritivo)

> **Reescrito em 23/11/2026 (ADR-023/024/026/027).** O F3 original aceitava "vale vender put em PETR4?" com taxa histórica da classe — alegação que o portão falsificou. O F3 entregue expõe **leitura descritiva**: o que o dado diz, sem classe de sinal, nota, taxa de acerto ou recomendação. A aceitação original fica preservada em bloco suspenso.

**Objetivo:** o agente consome o painel descritivo e as leituras do lake **antes** de existir tela, com números calculados em Python e falha explícita.
**Estimativa:** 0,5 sessão · **Depende de:** F2 (F1b não é mais pré-requisito do descritivo, mas segue como registro do portão)

| ID | Tarefa | Est. |
|---|---|---|
| F3.0 | `health` honesto: `model_version` vem das tabelas derivadas (`2`), `ingest_model_version` separado, `model_version_mismatch`/`source`/`note` | 30 min |
| F3.1 | FastAPI (`b3o.api:app`) + MCP Streamable HTTP em `/mcp`, uma `QueryService` no lifespan, envelope (`as_of_session`/`model_version`), `max_rows` 20/60 e teto de 200 KB; rotas `health`, `screen`, `underlying`, `chain`, `series`, `events` | 2 h |
| F3.2 | Leituras novas na `QueryService` (`underlying`, `chain`, `series`, `events`) com dataclasses `frozen, slots`; vínculo por ISIN, unidades na borda | 2 h |
| F3.3 | Cliente MCP de fumaça (`backend/scripts/mcp_smoke.py`) + prova MCP×HTTP e a pergunta de aceitação descritiva | 1 h |

**Aceitação F3** — parecer independente em `docs/relatorios/2026-11-23-revisao-f3.md`.
- [x] `podman compose config` válido; `up -d app` sobe e `/api/health` traz `as_of_session` e **`model_version` 2** (o `ingest_state` em 1 aparece como `ingest_model_version`, com `mismatch: true`) — a prova rodou em `127.0.0.1:8081` (`B3_APP_PORT`) porque 8080 está ocupado por outro projeto local; a app dentro do container é `:8080`
- [x] `tools/list` mostra `health`, `screen`, `underlying`, `chain`, `series`, `events`, cada uma com "use quando / não use para" e **sem** termo preditivo
- [x] `screen` de 19/11 devolve **289/163/126** (`no_trade` 2, `no_options` 124), IV mediana **31,43** (n=35), VRP **−30,66** (n=35); mesmo JSON no HTTP; `meta` declara `total=289`, `returned=20`, `truncated=true`
- [x] `underlying PETR4` em 19/11: spot **42,67**, `iv_atm_30d` **31,43**, **88/782** séries, **25** vencimentos; `chain` respeita `delta_range`/`liquidity` e devolve ≤ 60 linhas; `events` traz o próximo resultado com `actual`/`estimated`
- [x] ticker inválido → `{"error":{"code":"ticker_not_found",...}}`; data sem sessão → `no_session` + dica; **nenhum** traceback no corpo
- [x] nenhuma lista > 60 linhas; o teto de 200 KB trunca com `truncated: true` (3.000 linhas sintéticas → 2.562 devolvidas, 199.957 bytes)
- [x] `grep -i` de `signal_class|premium|acerto|ranking|caro|barato|recomend` nas respostas = **0**
- [x] pergunta de aceitação respondida de forma descritiva, sem edge, citando campos reais (IV ATM 30d, RV30, VRP/z, RR25, liquidez, próximo resultado) e dizendo que o sinal não foi validado (ADR-023)
- [x] `ruff check src` limpo; `b3o health`, `b3o screen --date 2026-11-19` e `b3o metrics --date 2026-11-19` continuam funcionando; nenhuma fórmula mudou e `model_version` segue **2**
- [x] `uv.lock` regerado no container e commitado; build do zero e `podman compose up -d app` funcionam

**Entregável:** `podman compose up -d app` sobe HTTP + `/mcp`; `backend/scripts/mcp_smoke.py` prova o contrato no dado real.

<details>
<summary>F3 original (suspenso pelo ADR-023/024 — registro, não executar)</summary>

**Objetivo:** o agente já consome o funil, antes de existir tela.
**Estimativa:** 0,5 sessão · **Depende de:** F2

| ID | Tarefa | Est. |
|---|---|---|
| F3.1 | FastAPI mínimo com MCP Streamable HTTP montado em `/mcp` (`127.0.0.1:8080`), sobre `QueryService` | 1.5 h |
| F3.2 | tools `health`, `screen`, `underlying`, `chain`, `series`, `events`, `signal_backtest` com descrição "use quando / não use para", `max_rows`, teto de 200 KB | 1.5 h |
| F3.3 | teste de contrato: MCP e HTTP devolvem o mesmo número | 30 min |

**Aceitação F3 (original)**
- [ ] agente conectado responde "vale vender put em PETR4?" citando campos que existem nos retornos e a taxa histórica da classe
- [ ] nenhuma tool devolve > 60 linhas
- [ ] erro sempre no formato `{error:{code,message,hint}}`

</details>


---

# F4 — Estruturas

**Objetivo:** avaliar **a estrutura que o usuário especificou** (payoff, breakevens, prêmio líquido, perda/ganho máximo, capital comprometido, gregas e cenários) — descritivo, sem sugestão, comparação, ordenação, margem ou probabilidade (ADR-023/024/028).
**Estimativa:** 1 sessão · **Depende de:** F3

| ID | Tarefa | Est. |
|---|---|---|
| F4.1 | `derive/structure.py` puro: payoff, breakevens, prêmio líquido (débito/crédito), gregas agregadas, faixa `mid ± ½ spread` | 2 h |
| F4.2 | cenários spot × IV × dias dentro do `structure_eval` (45 pontos, Black-76, `T'=0` intrínseco) | 1 h |
| F4.3 | perda máxima e capital comprometido (**sem margem**) no cálculo puro | 30 min |
| F4.4 | `POST /api/structure` + tool `structure_eval` + `b3o struct` sobre o mesmo payload | 30 min |
| F4.5 | Prova no dado real: 5 estruturas conferidas à mão + `structure_smoke.py` (sem `tests/`) | 30 min |

**Aceitação F4 (onda J)** — contrato `docs/contratos/onda-j.md`; parecer independente em `docs/relatorios/2026-11-23-revisao-f4.md`.

- [x] payoff, breakeven e prêmio líquido conferem à mão em 5 estruturas de PETR4 em 19/11 — **coberta** 4109 / [41,09] / −268,70 · 158,00 · 207,00; **put caixa** −72 / [40,19] / −178,70 · 72 · 72; **trava alta** 35 / [42,76] / −35 · −9 · 40; **trava baixa** 99 / [42,42] / 151 · −25 · −99; **straddle** 315 / [40,01; 46,31] / 160,70 · −266 · 62,70; conferência por caminho independente (`data/out/f4-conferencia.py`, DuckDB direto + aritmética própria) e recalculo do revisor
- [x] perda máxima correta em risco limitado; **call vendida nua → `max_loss_brl: null` + `unbounded_loss`**; `capital` abaixo do comprometido → `capital_over: true`; `capital_committed_brl` = 4109 / 4019 / 35 / 99 / 315 nas 4 de risco definido (+ coberta)
- [x] `POST /api/structure`, tool `structure_eval` e `b3o struct --json` devolvem **o mesmo JSON** nas 6 estruturas — `structure_smoke.py` **93/93 PASS** no dado real
- [x] cenários ≤ 60 pontos (45), `(S, 0, 0)` ≈ P&L 0, `T'=0` = intrínseco (27/11 com 7 dias → −86,00 = intrínseco 0 − prêmio 0,86), perna sem IV → `scenarios_unavailable` com payoff presente
- [x] erros `bad_legs`/`bad_request`/`underlying_mismatch`/`maturity_mismatch` (400), `ticker_not_found`/`no_session`/`metrics_missing` (404), sempre `{error:{code,message,hint}}` e sem traceback
- [x] nenhuma lista > 60 linhas (pernas, payoff e cenários; `legs` acima de 60 → `bad_legs`); maior resposta real **6.644 bytes** (teto de 200 KB respeitado); `grep` preditivo = **0** (o nome de campo `net_premium_brl` é a única ocorrência de "premium", declarada no contrato §8)
- [x] `ruff check src` limpo; `b3o health`/`screen`/`metrics` intactos; `model_version` segue **2**; `git diff` de `metrics/`/`ingest/`/`store/`/`derive/` existente = vazio; `uv.lock` intacto
- [x] `POST /api/structure` é cálculo, não gravação: nenhum arquivo do lake é criado/alterado (F6 é que grava)

**Entregável:** `b3o struct --underlying PETR4 --leg "option:PETRL44:short:100" --leg "stock:PETR4:long:100"` (e `POST /api/structure` + tool `structure_eval`).

---

# F5 — API + PWA

**Objetivo:** o painel no celular, instalado, via HTTPS.
**Estimativa:** 4 sessões · **Depende de:** F4

**Design:** a UI é construída **com** o **unslop** (skill em `~/.config/opencode/skills/unslop`, `github.com/heydamir/unslop`, CC BY 4.0), não "unslopada" depois. A direção aprovada (`docs/direcao.md`, A · Bancada fria) é o contrato: componente que não segue a direção está errado, não "com outro gosto". Os 9 passes entram na construção de cada tela, com auditoria e checklist no fim de cada uma.

| ID | Tarefa | Est. |
|---|---|---|
| F5.0 | Scaffold `frontend/`: **Vite + React + TS + TanStack Router (file-based) + TanStack Query + Tailwind v4**; serviços `web` (build one-shot) e `web-dev` (Vite) no compose | 1.5 h |
| F5.1 | FastAPI: `/api/health`, gzip, cache HTTP, `X-App-Token` nas escritas, estático de `frontend/dist` | 1.5 h |
| F5.2 | rotas do backend (screen, tickers, ticker, chain, series, glossary, structure) | 2 h |
| F5.3 | Router: 4 destinos + 2 drill-downs + ficha de métrica (modal) | 2 h |
| F5.4 | cliente tipado da API (`frontend/src/lib/api.ts`) + TanStack Query (cache, erro, offline) | 1.5 h |
| F5.5 | componentes base (cartões, régua, cadeia put·strike·call, gráficos SVG) aplicando os 9 passes do unslop | 4 h |
| F5.6 | builder de estrutura (toque nas pernas → prêmio e breakeven) | 1.5 h |
| F5.7 | PWA com `vite-plugin-pwa`: manifest, precache do shell, `NetworkFirst` em `/api`, offline com carimbo | 2 h |
| F5.8 | Dockerfile multi-stage (Node build → imagem final Python) — já existe desde o F0.1b; aqui entra o estágio `web` | 1.5 h |
| F5.9 | `tailscale serve` + instalação no celular + teste de offline | 1 h |
| F5.10 | Hoje: bloco de liquidez (papéis com série líquida + séries por volume) — ponto de partida, não recomendação | 2.5 h |
| F5.11 | Tipografia no desktop: base 15 px e rótulos ≥ 12 px (mobile intacto) | 1 h |

### F5-A — sessão 1 de 4 (infraestrutura + API + cliente tipado + wireframe)

Contrato em `docs/prompts/f5-a.md`; ADR-029…032. Critérios provados com saída colada no parecer `docs/relatorios/2026-11-23-revisao-f5a.md`:

- [x] `podman compose config` válido; `podman compose --profile build run --rm web` gera `frontend/dist` no `node:26-slim` (sem Node no host); `podman compose build app` (com o estágio web) e `up -d app` em `B3_APP_PORT=8081`.
- [x] `GET /` e `GET /t/PETR4` devolvem o **mesmo** HTML com carimbo; `/api/health` segue JSON com `model_version 2`; `/mcp` intacto (`mcp_smoke.py` **53/53**, `structure_smoke.py` **93/93**).
- [x] Headers: `index.html` `no-store`; `/assets/*` `immutable`; `/api/*` `no-cache` (inclusive erros); gzip em HTML/JSON/JS; asset inexistente → 404; `dist` ausente → 503 `frontend_missing` (com dica).
- [x] `GET /api/tickers?q=PETR` → PETR3/PETR4 com `in_universe`/`has_options`, envelope e ≤ 60; `GET /api/glossary` → 30 verbetes (≥ 20); `GET /api/glossary/iv-atm-30d` → texto do verbete; chave desconhecida → 404 `not_found`.
- [x] `npm run lint` e `tsc --noEmit` limpos no container; duas builds seguidas → hashes idênticos; **só** `frontend/src/lib/api.ts` contém `fetch` (grep).
- [x] `uv.lock` intacto; `git diff` de `metrics/ingest/store/derive` existente = vazio; `model_version` 2; `b3o health`, `b3o screen --date 2026-11-19` e `b3o struct` intactos.
- [x] Zero escrita em `data/` por GET; zero vocabulário preditivo novo; sem biblioteca de gráfico; sem SSR/servidor no frontend.
- [x] ADRs 029–032 (stack/dispatcher/glossário/cliente), armadilhas no `AGENTS.md`, `README.md` com os comandos e push por pacote verde.

**Não entregue aqui (escopo das próximas sessões):** 9 passes do unslop/desenho final, ficha de métrica (modal) e gráficos (F5-B); builder de estrutura, Carteira/Diário reais e PWA/offline (F5-C); Tailscale/instalação no celular (F5-D). Os placeholders de Carteira/Diário dizem "F6" explicitamente. A aba Aprender foi tratada à parte, na sessão F5-PreB (accordion independente, ADR-033).

### F5-PreB — ajuste pontual do Aprender (antes do F5-B)

Contrato em `docs/prompts/f5-pre-b.md`; ADR-033; evidência em `docs/relatorios/2026-11-23-f5-pre-b.md`. Commits `4ddaf63` → `b7a4d24` (push em `main`).

- [x] a ficha abre **no lugar**, inline, logo abaixo do verbete tocado: 30 botões com `aria-expanded` e painel `role="region"` com `hidden={!open}`; **vários abertos ao mesmo tempo** (medido: 2 painéis visíveis, `openCount 2`) e cada um colapsa de forma independente.
- [x] uma `useQuery` por verbete com `staleTime` 1 h; `fetch` continua **só** em `lib/api.ts`; carimbo `sessão 2026-11-19 · D-1` visível e **zero erros de console** no navegador.
- [x] B2 fechada: `plain()` remove blockquote (`>`), regra (`---`) e links Markdown, sem biblioteca nova.
- [x] `lint` + `typecheck` limpos; duas builds com hashes idênticos (`index-B8W31Zfi.css` / `index-smFPNgpz.js`); app reconstruído em 8081; `mcp_smoke` **53/53** e `structure_smoke` **93/93**; `model_version` **2**.
- [x] nenhum arquivo congelado tocado; `data/` inalterado (201 arquivos). O bottom sheet da primeira passagem foi revertido a pedido do dono (ADR-033).

**Não entregue aqui:** B1 (`/api/tickers` sem UI de busca) segue para F5-C; **F5-B continua em aberto** — unslop, régua, gráficos SVG e o `MetricModal` de Hoje/Ticker/Série (componente distinto da ficha do Aprender).

### F5-B — sessão 2 de 4 (visual + telas de leitura) — parcial

Contrato em `docs/prompts/f5-b.md`; parecer com veredito e os 9 passes por tela em
`docs/relatorios/2026-11-23-revisao-f5b.md`. Worktree próprio; app provado em `B3_APP_PORT=8082`
(8081 pertence ao container da sessão paralela).

- [x] **F5.5** — componentes base: `Metric`/`MetricGrid`, `Ruler` (a agulha), `Chain` put·strike·call,
  `LineChart` em SVG inline, `MetricModal` (ficha em modal), `ErrorState`/`Stamp` e `metricKeys.ts`
  (40 campos → 30 slugs conferidos um a um em `/api/glossary`). Nenhum import de `lib/ui.tsx`.
- [x] **F5.3 — parte Hoje/Ticker/Série** — 9 passes aplicados na construção; rótulos expandidos
  ("Volatilidade implícita", "Prêmio de volatilidade"), modal com foco/trap/Escape, tabela dividida
  em Com opções (163) e Sem opções (126) com a primeira coluna sticky, `screen` paginado por
  `offset` e o frontend buscando todas as páginas; zero erros de console, carimbo nas 3 telas,
  412 px com todos os alvos ≥ 44 px; `npm run lint` + `tsc --noEmit` limpos, duas builds com hashes
  idênticos, smokes 53/53 e 93/93, nenhum arquivo da sessão paralela tocado e `data/` inalterada por
  GET.

**Não marcado aqui:** F5.3 na parte do Aprender (F5-PreB), F5-B inteiro, smoke de cobertura
campo↔glossário (F5-C), F5.6–F5.9.

### F5-C — sessão 3 de 4 (builder + PWA offline + cobertura + busca)

Contrato em `docs/prompts/f5-c.md`; parecer em `docs/relatorios/2026-11-23-revisao-f5c.md`.
Worktree próprio; app provado em `B3_APP_PORT=8083`.

- [x] **F5.6** — builder de estrutura inline na Ticker: pernas de opção (Comprar/Vender em cada lado
  da linha expandida, 100 ações por padrão, editável) e de ação; lista com remover/limpar e limite de
  60; `POST /api/structure` calcula e o painel exibe prêmio líquido, breakevens, perda/ganho máximo,
  capital, Δ/Γ/Θ/ν, payoff em SVG e a tabela curta de cenários. Zero número calculado em TS; erros
  `bad_legs`/`maturity_mismatch`/`underlying_mismatch` no envelope. As 5 formas (coberta, put caixa,
  trava de alta, trava de baixa, straddle) foram montadas no navegador e bateram com o POST, campo a
  campo.
- [x] **F5.7** — PWA com `vite-plugin-pwa@2`: manifest "Opções B3" (`standalone`, `#101418`, ícones
  192/512 monocromáticos), `registerType: autoUpdate`, precache do shell e `NetworkFirst` em `/api`
  (cache `b3o-api`); sonda `/api/health?probe=1` fora do cache. Offline real (container parado):
  shell servido pelo SW, banner "sem rede — última sessão 2026-11-23", carimbo "offline" sem `D-n`.
  Carimbo online deriva de `age_sessions` (0 → `D-1`) em vez do literal.
- [x] **Cobertura campo↔glossário** — `frontend/scripts/glossary-coverage.mjs` (`npm run coverage`):
  15 campos tocáveis, 44 campos no mapa, 31 verbetes servidos, verde (todo campo tem slug e todo slug
  existe em `/api/glossary`).
- [x] **B1 — busca de papel** — `TickerSearch` no topo de Hoje com `aria-label`, TanStack Query
  (`staleTime` 1 h, `q` vazio não dispara), prefixo-primeiro do servidor, `meta.total` honesto, linhas
  de 58–59 px, 412 px sem overflow e navegação por teclado (Tab + Enter).

**Não marcado aqui:** F5-D (Tailscale/celular), F5-E (liquidez no Hoje), Carteira/Diário reais (F6).

### F5-D — sessão 4 de 4 (Tailscale + celular)

Contrato em `docs/prompts/f5-d.md`; ADR-037; evidência em
`docs/relatorios/2026-11-24-revisao-f5d.md`. App em `127.0.0.1:8083`; URL
`https://desktop-vlp5tqv.tail5745cb.ts.net:8443`.

- [x] **Exposição somente na tailnet:** `tailscale serve --https=8443` aponta para 8083 e coexiste
  com o Funnel da 443 do `realestate`; a 443 não foi alterada. HTTPS 200 e `/api/health` local =
  HTTPS (`2026-11-23`, `model_version 2`).
- [x] **MCP no tailnet:** allowlist por `B3_MCP_ALLOWED_HOSTS` (hostname com e sem porta), defaults
  locais preservados e Host não listado continuando **421**. `mcp_smoke` **53/53** e
  `structure_smoke` **93/93**, local e HTTPS.
- [x] **iOS:** `apple-touch-icon.png` 180×180, metadados iOS e precache; builds limpas idênticas,
  lint/tipos/cobertura verdes.
- [x] **Regressão no host:** Hoje 289/157/132, Ticker com 7 métricas/3 réguas/2 gráficos/28
  vencimentos/6+22, 412 px sem overflow e 165 alvos ≥ 44 px; SW + `b3o-api`, offline com
  shell/banner/carimbo e retorno online sem erros; `data/` inalterado.
- [x] **Instalação no aparelho físico:** dono confirmou o Android instalado e funcionando em
  24/11/2026, após ligar o Tailscale, abrir a URL, instalar o PWA e testar o ciclo offline/online.
  As capturas não foram anexadas; a confirmação explícita do dono fecha o critério.

**F5-D fechado:** F5.9 e a aceitação “instalado no celular” marcadas com a confirmação física do dono.

### F5-E — Hoje: liquidez como ponto de partida (pedido do dono, 23/11/2026)

**Objetivo:** responder “o que eu consigo negociar hoje?” sem depender de já saber o ticker. A busca
sozinha não ajuda quando o dono não sabe o que procurar. Na Hoje, **antes da tabela dos 289**, um
bloco **Liquidez** lista os papéis com mais séries líquidas e, para eles, as séries com mais volume.
É **ponto de partida descritivo**, não recomendação: a ordem é liquidez, não retorno esperado, IV ou
VRP; nada de “melhor/caro/barato/recomendado”. A lista não monta estrutura nem grava nada; o builder
continua sendo a calculadora. (ADR-024: nada preditivo.)

**v1 — papéis (sem backend novo, 1.5 h)**
- Fonte: `/api/screen` já traz `n_liquid_series`, `n_expiries`, `n_series`, `iv_atm_30d`, `vrp`,
  `spot_brl`, `rv_shock_*`. A ordenação no cliente é apresentação (não é número novo): `n_liquid_series`
  DESC, `n_expiries` DESC, `n_series` DESC, `ticker` ASC. Se virar contrato de MCP, move para
  `QueryService`.
- Top **10** com `n_liquid_series > 0`; colunas: papel, fechamento, séries líquidas, vencimentos,
  IV 30d e VRP (mesmos formatos e `†` do Hoje). Linha inteira leva a `/t/{ticker}` (44 px, teclado,
  sem hover-only).
- “Ver todos” expande os demais do mesmo recorte sem nova chamada; se ninguém tiver série líquida,
  o bloco diz “nenhum papel com série líquida nesta sessão” (nunca zero silencioso).
- Posição: abaixo do aviso de pregão dominante e acima de “Com opções”. O card “Com opções”
  continua existindo; o bloco é o atalho de descoberta.
- Nota quieta no bloco: “ordenado por liquidez; não é recomendação”. Nada de score, estrela ou
  “melhores”.

**v1.1 — séries (backend novo, 1 h)**
- `GET /api/liquid?date=&limit=` na **mesma** `QueryService`, read-only: séries da sessão que passam
  a regra §6.9, ordenadas por **volume financeiro** da sessão (R$) DESC, desempate `trades` DESC e
  `ticker` ASC; top 20. Campos: papel, ticker, lado, estilo, strike, vencimento, volume, trades,
  spread, IV, delta, `is_liquid`; `meta.total`/`truncated` honestos e teto de 60 linhas.
- Não criar métrica nem subir `model_version`: é consulta. O volume é o do lake (não reconstituir).
- UI: segunda lista/aba “Séries mais negociadas” (20 linhas), link para `/s/{ticker}/{maturity}`;
  filtro local por papel se couber sem virar nova tela.
- Prova: conferir 1 série à mão pelo volume (R$) e o `chain` correspondente; zero vocabulário
  preditivo; `data/` sem escrita.

**Aceitação F5-E**
- [x] bloco Liquidez no Hoje, top 10 + “ver todos”, ordem determinística; em 23/11 há **33** papéis com
      `n_liquid_series > 0` (top: BBAS3 17/24; PETR4 e VALE3 12; MGLU3 10; BOVA11 9; BBDC4 6;
      ITUB4/PRIO3/CXSE3 3; CSNA3 2 — desempate por vencimentos e ticker).
- [x] linha abre a Ticker; `†` quando `rv_shock_day`; IV/VRP formatados como no resto; 412 px sem
      overflow e alvos ≥ 44 px.
- [x] v1.1 `/api/liquid` com top 20 por volume, `meta` honesto, 1 número conferido à mão; nenhum
      `model_version` novo.
- [x] `npm run coverage` verde (novos campos tocáveis, se houver); `mcp_smoke` 53/53 e
      `structure_smoke` 93/93; `data/` inalterado por GET; zero vocabulário preditivo.
- [x] nota “ordenado por liquidez; não é recomendação” visível; ADR-024 citado no relatório.

**Entregue (24/11/2026):** bloco Liquidez + `/api/liquid` + `liquid_smoke.py` **24/24** local/HTTPS.
ADR-038; evidência em `docs/relatorios/2026-11-24-f5e.md`.

**Estimativa:** 2,5 h · **Depende de:** F5-C (campos do `screen`). Pode vir antes ou depois do F5-D;
não depende do Tailscale.

### F5-F — Tipografia no desktop (pedido do dono, 23/11/2026)

**Problema:** no desktop o texto está pequeno. A base é **13,5 px** e a maior parte dos rótulos é
**11 px** (`.metric__label`, `.note`, `.chain-note`, `.quiet-line`, `.stamp`), a tabela é 11,5 px e
os eixos/legendas de gráfico são 10–10,5 px. No celular isso funciona; no monitor, a 100% de zoom,
a leitura fica desconfortável. **Não é para “resolver” com zoom do navegador** — o app tem que ser
legível em 100%.

**Escopo:** `frontend/src/styles.css`, `frontend/src/styles-view.css` e a escala em
`docs/direcao.md` (contrato visual do dono). Nenhuma lógica de componente, nenhum número novo.
Não usar `zoom` nem `transform: scale` (distorce gráficos, sticky e alvos de toque).

**Abordagem:** criar tokens de tamanho (`--fs-xs/sm/base/lg/...`) ou um bloco
`@media (min-width: 1024px)` que sobrescreva as classes atuais; preferir os tokens para não
multiplicar px soltos. O corte mobile/tablet (< 1024 px) fica **exatamente como está**.

**Escala inicial a validar (desktop ≥ 1024 px):**

| Elemento | Hoje | Proposta |
|---|---:|---:|
| base (`body`) | 13,5 px | **15 px** |
| `.screen-title` | 28 px | 32 px |
| `.block-title` | 13 px | 15 px |
| `.lede` | 12,5 px | 14 px |
| rótulos (`.metric__label`, `.note`, `.chain-note`, `.quiet-line`, `.stamp`) | 11 px | **12 px** |
| `.metric__value` / ênfase | 16 / 22 px | 18 / 24 px |
| tabela (`.screen-table`) / `th` | 11,5 / 11 px | 12,5 / 12 px |
| `.chain-row` / detalhe | 12 / 11 px | 13 / 12 px |
| eixos e legendas de gráfico | 10–10,5 px | 11 px |
| busca (nome / meta) | 11,5 / 10,5 px | 12,5 / 11,5 px |
| builder (rótulos / inputs) | 11 / 12 px | 12 / 13 px |
| modal (texto / título) | 13,5 / 18 px | 15 / 20 px |

Números em mono continuam tabulares e alinhados à direita; não mexer em cor, matiz, espaçamento
vertical ou hierarquia — só tamanho e o que o tamanho exigir (line-height/altura de linha).

**Aceitação F5-F**
- [x] em 1280 e 1440 px: `body` 15 px, rótulos ≥ 12 px, tabela ≥ 12,5 px; sem overflow horizontal
      fora do `.table-wrap`; coluna sticky e tabela de Hoje intactas.
- [x] Hoje, Ticker, Série, Aprender, busca, builder, modal e os três gráficos legíveis; eixos/legendas
      sem colidir; o gráfico em largura total continua sem clipping.
- [x] 412 px **sem mudança** de tamanho (comparar computed styles antes/depois); alvos ≥ 44 px.
- [x] `docs/direcao.md` atualizado com a escala desktop (e o ADR, se a mudança for registrada).
- [x] `lint`/`typecheck`/duas builds idênticas; console limpo; zero mudança de dado.
- [x] capturas em 1280/1440 e 412 no relatório.

**Entregue (24/11/2026):** escala desktop ≥ 1024 px, mobile com diferença zero de computed styles,
capturas 1280/1440/412 e ADR-039; evidência em `docs/relatorios/2026-11-24-f5f.md`.

**Estimativa:** 1 h · **Depende de:** F5-C (UI atual). Independente de F5-D/F5-E; pode entrar em
paralelo.

**Aceitação F5**
- [x] instalado no celular pelo domínio `*.ts.net` (HTTPS) — Android confirmado pelo dono em 24/11/2026
- [x] `npm run build` roda **dentro do container** (`podman compose run --rm web`) e o FastAPI serve o `dist`
- [x] `npm run lint` e `tsc --noEmit` limpos; os tipos da API vêm de um único arquivo de contrato
- [x] offline mostra a última sessão com o carimbo correto
- [x] busca de papel e seletor de vencimento funcionando
- [x] nada depende de hover; alvo de toque ≥ 44 px
- [x] teste de cobertura: campo da API ↔ chave do glossário
- [x] Hoje tem o bloco de liquidez (F5-E): papéis com série líquida + séries por volume, como ponto de partida
- [x] desktop legível em 100%: base ≥ 15 px e rótulos ≥ 12 px (F5-F)

---

# F6 — Posições

**Objetivo:** o painel e o agente saberem o que você já tem.
**Estimativa:** 1,5 sessão · **Depende de:** F5
**Status (ADR-047, 24/11/2026):** **implementação validada, aceitação no uso real** — começa quando o dono abrir a primeira posição real; o critério não muda.
**Status em 24/11/2026:** **implementação validada, aceitação pendente** da reconciliação manual com a corretora. Reverificação no HEAD `239685c` repetiu as regressões e confirmou o journal oficial vazio; o dono ainda não tem as duas posições reais e o extrato necessários. A regressão encontrou e corrigiu um defeito de cache do service worker no commit `9d1f961`.
Retomada/execução em `docs/prompts/f6-retomada.md`; evidências em `docs/relatorios/2026-11-24-f6.md`.

| ID | Tarefa | Est. |
|---|---|---|
| F6.0 | PostgreSQL + SQLAlchemy + Alembic para o journal; uma migration inicial única, sem migrar SQLite vazio | 1 h |
| F6.1 | `position`/`position_leg` + `POST /api/positions` e `/close` gravando no PostgreSQL (previsão opcional) | 2 h |
| F6.2 | marcação EOD, P&L, gregas líquidas, breakevens | 2 h |
| F6.3 | alertas determinísticos (spec §14.3) | 1 h |
| F6.4 | tool read-only `positions` no MCP, com paginação | 30 min |
| F6.5 | tela Carteira + detalhe da posição com payoff | 2 h |
| F6.6 | reconciliação com o extrato da corretora (manual, 2 posições) | 1 h |

**Progresso registrado (não é conclusão)**
- [x] dependências PostgreSQL/SQLAlchemy/Alembic adicionadas e `uv.lock` atualizado;
- [x] migration inicial única `0001_f6_initial` criada com tabelas, índices e triggers append-only;
- [x] `journal.py` reescrito para PostgreSQL/SQLAlchemy, com abertura/fechamento transacional;
- [x] `QueryService` corrigido para consulta histórica, marca zero, vencido, strike vigente,
  paginação, previsão, capital ilimitado e snapshot de fechamento;
- [x] API recebeu paginação, idempotência e autenticação antes do corpo; MCP `positions` aceita `offset`/`max_rows`;
- [x] frontend F6 implementado (TokenGate, formulário, lista, detalhe, payoff, fechamento e tooltip de perna);
- [x] Compose terminado (`db`, volume `b3o-postgres`, remoção das referências SQLite);
- [x] Dockerfile copiando `alembic.ini` e `migrations/`;
- [x] smokes isolados em PostgreSQL/schema temporário, sem apagar banco oficial;
- [x] achados da revisão de frontend/backend corrigidos e documentados;
- [x] regressões da implementação PostgreSQL executadas;
- [x] documentação final, relatório, commits e push.

**Validações PostgreSQL:** ruff limpo; `positions_smoke` **49/49**; `mcp_smoke` **58/58**;
`structure_smoke` **93/93**; frontend lint/typecheck/cobertura (**22 campos tocáveis · 55 no mapa · 36
verbetes**) e duas builds limpas com `dist` idêntico. O navegador provou abertura/fechamento, 401/409,
tooltip de perna e viewport 412 px sem overflow ou alvo < 44 px.

**Estado do ambiente:** PostgreSQL 19.0 no volume `b3o-postgres`; migration inicial aplicada e schema
oficial vazio. A app principal fica na 8083; UI e smokes usam schema descartável próprio. O container
antigo da 8084 e o volume SQLite descartável foram removidos.

**Aceitação F6**
- [ ] P&L confere com a corretora em 2 posições
- [x] Δ/Θ/ν líquidos batem com a soma das pernas
- [x] "tese vs realidade" mostra previsão, observado e status quando houver previsão
- [x] agente, perguntado sobre um papel em que há posição, cita a exposição existente

---

# F7 — Diário e calibração pessoal

**Objetivo:** medir se **as suas decisões** acertam (o sinal já foi medido no F1b).
**Estimativa:** 1 sessão · **Depende de:** F6
**Status (ADR-047, 24/11/2026):** **implementação validada, aceitação no uso real** — a janela de 30 sessões começa na primeira decisão real; o critério não muda.
**Status em 24/11/2026:** **implementação validada, aceitação pendente**. Reverificação no HEAD
`239685c` com o journal oficial e o lago real (sessão 2026-11-23, 723 sessões, `model_version=2`):
**0/30 sessões de diário** e **0/10 previsões avaliadas**; faltam
**30 sessões com decisão real** e **10 previsões distintas avaliadas automaticamente**. O dono definiu
“30 dias” como **30 sessões de pregão consecutivas**, não dias corridos. A regressão encontrou e
corrigiu um defeito de cache do service worker no commit `9d1f961`. Contrato em
`docs/contratos/f7.md`; evidências em `docs/relatorios/2026-11-24-f7.md`.

| ID | Tarefa | Est. |
|---|---|---|
| F7.1 | decisões sem posição (ex.: "não entrei") no mesmo PostgreSQL + avaliação automática da previsão no batch | 1.5 h |
| F7.2 | formulário de registro no mobile | 1.5 h |
| F7.3 | relatório de calibração: acerto das **previsões estruturadas** (§6.12) por métrica e horizonte, com `n`; sem classe de sinal nem taxa do F1b (ADR-023/024 — o sinal não passou e não é base de comparação) | 1.5 h |

**Progresso registrado (não é aceitação do dono)**
- [x] migration F7 `0002` + `0003`, decisão/previsão/avaliação append-only, índices únicos e triggers;
- [x] `POST/GET /api/journal`, detalhe, calibração paginada e autenticação antes do corpo;
- [x] lote `b3o evaluate`, integrado ao `prepare`/`prepare-watch`, primeiro pregão posterior e `void` explícito;
- [x] formulário/lista/calibração mobile-first, rascunho, 401/409 e `NetworkOnly`;
- [x] smokes e regressões técnicos, cobertura campo↔glossário, builds idênticas e navegador sem erros.

**Aceitação F7**
- [ ] 30 sessões de pregão consecutivas com decisão real, sem lacuna (**0/30** na janela 08/10–23/11/2026; faltam 30)
- [ ] ≥ 10 previsões distintas avaliadas automaticamente (**0/10**; faltam 10)
- [x] relatório mostra o acerto pessoal por métrica/horizonte com `n` e o que ficou `void`, sem comparar com classe de sinal (ADR-024)

---

# F8 — Interface com shadcn + Tailwind

**Objetivo:** substituir o CSS artesanal por componentes shadcn e utilitários Tailwind,
preservando o contrato visual, os gráficos SVG e a responsividade já validados.
**Estimativa:** 1,5 sessão · **Depende de:** F6 e F7 — **exceção autorizada pelo dono em 24/11/2026 (ADR-044)** para migrar F8.2/F8.3 em paralelo, mantendo F6/F7 pendentes.

| ID | Tarefa | Est. |
|---|---|---|
| F8.1 | Configurar shadcn no Vite/React e inventariar componentes/tokens reutilizáveis | 45 min |
| F8.2 | Migrar Carteira/detalhe, modais, formulários, tabelas e estados para shadcn + Tailwind | 1 h |
| F8.3 | Migrar as demais telas e remover o CSS artesanal redundante | 45 min |
| F8.4 | Regressão visual/funcional em 412, 1280 e 1440 px; lint, typecheck, cobertura e builds | 30 min |

**Progresso em 24/11/2026:**
- [x] F8.1 configurada (`components.json`, alias `@`, primitives Button/Input/Select/Table/Dialog);
- [x] revisão tipográfica desktop aprovada e aplicada por tokens `@theme` + utilitários `lg:` (ADR-043);
- [x] F8.2 migrar Carteira/detalhe, modais, formulários, tabelas e estados — commit `1230b69`;
- [x] F8.3 migrar as demais telas e remover CSS artesanal redundante — commit `54e7489` (146 regras sem referência removidas);
- [x] fecho Tailwind — commits `cd61e4e` e `3e2c3b5`, 18 arquivos migrados, `styles.css` 110 linhas, `styles-view.css` 390 e classes compartilhadas em `frontend/src/lib/classes.ts` (ADR-046);
- [x] F8.4 regressão visual/funcional em 412, 1280 e 1440 px — avaliação manual do dono aprovada em 25/11/2026 na app 8092, sem problemas registrados por rota/estado; referência canônica da migração `57f5f84`. O fecho não rodou nova comparação pixel a pixel (relatório F8).

F6 e F7 continuam pendentes de aceitação real (ADR-047); a autorização paralela está registrada na ADR-044. O fecho da F8 não inicia nem altera essa contagem.

**Aceitação da revisão tipográfica (ADR-043)**
- [x] 1280 e 1440 px: corpo 16, título 36, bloco 16, lede 15, rótulos 13, métrica 20, herói 28, tabela 14/13, cadeia 14/13, gráfico 12, busca 14/12,5, builder 13/14 e modal 16/22; sem overflow/truncamento fora das regiões de tabela.
- [x] 412 px sem mudança de tamanho/line-height; alvos ≥ 44 px.
- [x] shadcn sem `text-sm` herdado; sans de sistema, mono tabular, sem zoom/scale/CSS arbitrário.
- [x] lint/typecheck/cobertura, duas builds limpas idênticas e console limpo.

**Aceitação F8**
- [x] componentes shadcn cobrem os padrões interativos sem perder acessibilidade ou foco (F8.2: diálogos/formulários; F8.3: ordem e contorno de foco idênticos ao legado)
- [x] Tailwind é a camada principal de layout/espaçamento/tipografia; CSS global só para tokens, base e as exceções da ADR-046: `styles.css` 110 linhas e `styles-view.css` 390 linhas (`.chart*`, `.ruler*`, `.table-wrap`/`--scroll`, `.screen-table`, `.liquidity-table`, `.position-flags`); classes compartilhadas em `frontend/src/lib/classes.ts`
- [x] zero regressão de contrato, número, gráfico, offline ou alvo de toque ≥ 44 px — avaliação manual aprovada; offline reexercitado página + service worker (`b3o-f8-offline-final/result.json`: navegação e `/api/screen` do cache; sonda `/api/health?probe=1`, `/api/positions` e `/api/journal` com `network_error`; nenhum deles em `caches`); alvos < 44 px = 0 em 412/1280/1440; console 0; `mcp_smoke` 60/60 após ajuste à ADR-045
- [x] duas builds limpas idênticas e navegador sem erros de console — 10 arquivos idênticos; `npm run check` e cobertura 26/63/45 verdes; `structure_smoke` 93/93; `liquid_smoke` 23/24 idêntico na referência `57f5f84` por fixture fixa em 23/11 contra `/api/s` na última sessão (limite registrado no relatório)

### F8.5 — consolidação e expansão da migração shadcn

Plano executado em `docs/planejamento-migracao-shadcn.md`, sem reabrir F6/F7/F9 nem alterar backend, dados, fórmulas, API, rotas ou `model_version`.

- [x] **F8.5.0 — baseline e guardas:** referência `a796afa`, lago `2026-11-24`/`model_version=2`/724 sessões e 48 estados aceitos.
- [x] **F8.5.1 — fundação Radix:** imports unificados em `radix-ui`, dependências individuais removidas e 48 estados sem regressão.
- [x] **F8.5.2 — Tooltip, ToggleGroup e Alert:** teclado/toque/foco/Escape, aparência e envelope de erro preservados; comparação determinística 48/48 **PASS**, interações **PASS** (Accordion múltiplo fica para F8.5.4), suplemento **PASS**, cobertura 26/63/45 e lago com 191 hashes idênticos. Revisão independente: **PASS**.
- [x] **F8.5.3 — tabelas e variantes dos controles:** 48 estados, 24 checks de tabela e suplemento F6/F7 PASS; zero diferença de geometria/fonte/cor, lago idêntico e revisão independente **PASS**.
- [x] **F8.5.4 — pilotos comportamentais:** Select nativo mantido; Checkbox Radix e Accordion múltiplo adotados; 62 checks × 3 larguras, 61 checks de Checkbox, 36 de tabelas e teclado em navegador real **PASS**; zero pageerror. Revisão independente: **PASS**.
- [x] **F8.5.5 — validação e fechamento:** check/cobertura 26/63/45, duas builds limpas idênticas (10 arquivos), interações 62×3, tabelas 36, alvos 18 e offline 20 **PASS**; lago 2026-11-24/`model_version=2`/724 sessões e 191 hashes idênticos. Revisão independente: **PASS**.

---

# F9 — Níveis de linguagem: Expert × Leigo

**Pedido do dono (24/11/2026):** permitir escolher uma linguagem mais expert ou mais leiga. No modo
leigo, os termos devem ser menos abreviados e ter mais explicações/tooltips.
**Status:** implementação concluída; validação final ainda não executada. Não reabre F6/F7 nem altera a aceitação de uso real.
**Prompt de execução:** `docs/prompts/f9.md` (orquestração com subagentes Hy4 flash Free).

**Objetivo:** mudar apenas a camada de apresentação do PWA, mantendo os mesmos números, unidades,
ordens, rotas e contratos da API.
**Estimativa:** 3 h · **Depende de:** F8. Não depende da aceitação de uso de F6/F7.

| ID | Tarefa | Est. |
|---|---|---|
| F9.1 | Preferência global de linguagem, com seletor acessível e persistência local por aparelho (`localStorage`); default **Expert**, valor inválido cai no default | 30 min |
| F9.2 | Dicionário único de apresentação por campo/semântica: rótulo, sigla, explicação curta e rótulo expandido; reaproveitar `metricKeys.ts`, `HelpTip`, `MetricModal` e `docs/glossario.md` | 45 min |
| F9.3 | Aplicar os dois perfis em Hoje, Ticker, Série, Carteira, Diário, Aprender, builder, tabelas, gráficos, modais, flags e mensagens recorrentes | 1 h |
| F9.4 | Ajuda contextual no modo Leigo: uma frase curta por termo/métrica, com a ficha completa do glossário sob demanda; sem esconder informação essencial atrás de hover | 30 min |
| F9.5 | Regressão dos dois perfis em 412/1280/1440 px, teclado/toque, offline, cobertura e builds; documentar o contrato na spec §10 | 15 min |

**Regras de escopo**
- **Expert:** preserva a linguagem concisa atual (IV, RV, VRP, Δ, Θ, ν, RR25, BFLY25 etc.); a ajuda
  continua disponível pelos componentes existentes.
- **Leigo:** expande o termo antes da sigla (ex.: “Volatilidade implícita (IV)”, “Volatilidade realizada
  (RV)”, “Prêmio de volatilidade (VRP)”) e acrescenta explicação curta em português. A sigla não
  desaparece: ela continua sendo a ponte com o glossário e com o vocabulário do app.
- A preferência é global, vale em todas as rotas e sobrevive a reload, atualização do PWA e uso offline.
  Não há conta, backend, sincronização entre aparelhos nem gravação no journal.
- Textos curtos ficam num mapa de apresentação único; **definições completas continuam somente em
  `docs/glossario.md`**, servidas por `/api/glossary`. Nada de `if` de linguagem espalhado pelos componentes.
- Tooltip existe para **hover, foco e toque**, com fechamento por Escape e sem conteúdo essencial
  exclusivamente hover. O modal de métrica continua sendo a explicação completa.
- Campos da API, chaves do glossário, fórmulas, unidades e `model_version` **não mudam**; segue 2.
  Trocar de perfil não pode mudar nenhum número nem criar escrita em `data/`.

**Aceitação F9**
- [ ] um seletor global alterna Expert/Leigo sem reload; a escolha persiste após reload e offline, e um
      valor inválido/ausente abre em Expert
- [ ] no Leigo, todos os termos recorrentes mapeados usam rótulo expandido e têm ajuda curta; no Expert,
      os rótulos atuais permanecem reconhecíveis e a ficha completa continua acessível
- [ ] a ajuda abre por hover, teclado e toque, fecha por Escape, preserva o foco e não depende de hover;
      alvos de toque ≥ 44 px
- [ ] Hoje, Ticker, Série, Carteira, Diário, Aprender, builder, gráficos, tabelas, flags e modais respeitam
      o perfil escolhido, sem overflow, truncamento ou colisão em 412/1280/1440 px
- [ ] `npm run coverage` continua verde nos dois perfis; todo campo aponta para o mesmo slug do glossário,
      independentemente do rótulo visível
- [ ] os payloads e números exibidos são idênticos nos dois perfis; `model_version` segue 2 e `data/` não
      recebe escrita
- [ ] `lint`, `tsc --noEmit`, duas builds limpas idênticas, smokes existentes e console do navegador passam;
      spec §10 documenta os dois perfis e o default

---

# F10 — Benchmark externo de métricas

**Pedido do dono (25/11/2026):** comparar as métricas do painel com referências externas.
**Revisão (26/11/2026):** o dono exigiu cobertura para **todas** as métricas de mercado, podendo combinar
sites. Fontes investigadas no navegador em 26/11/2026 (sessão 25/11 nos sites e no lake).

**Estado real em 26/11/2026:**
- **F10.1 concluído com uma pendência explícita:** “OpçõesBR” não é `opcoes.net.br`; B3Lab usa `r`
  contínua e `T = du/252`; o montador público do OBM não aceita perna de ação; o seletor do
  opcoes.net.br usa `quotesMode=UP`, `#vAAAA-MM-DD` e espera do DataTable. A definição de
  `skew_25d_30d_ratio` continua pendente e a captura futura deve preservar `fields[]` do agregado.
  Evidência: `data/bench/f10_1-verificacoes.md`.
- **F10.2 concluído somente na infraestrutura:** serviço `bench` isolado, `Dockerfile.bench` e
  Playwright 1.65.2, commit `826690e`. O mecanismo de captura ainda não existe.
- **Mecanismos em construção/pausados:** P3/P4/P5/P7 foram despachados e pausados; a auditoria desta
  retomada não encontrou arquivos parciais. P6 não foi iniciado.
- **Nenhuma captura externa, comparação numérica ou relatório final de benchmark foi executado.**
  Nenhum item da **Aceitação F10** está marcado.

**Escopo da retomada:** construir e validar estaticamente os mecanismos (`py_compile`, `--help`,
`ruff` e `git diff --check` em container). **Não executar captura**, não abrir OBM/opcoes.net.br/B3Lab,
não gerar relatório final e não concluir o F10 nesta sessão. Não reabre F6/F7/F9 nem altera a
aceitação de uso real.

**Objetivo:** medir a aderência de todas as métricas de mercado a referências externas, separando
divergência de metodologia de erro real — sem transformar site de terceiro em dono da verdade.
**Estimativa:** 2 sessões (~7 h) · **Depende de:** F1, F3 e F4. É independente de F9 e da aceitação de uso de F6/F7.

## Fontes (medidas em 26/11/2026)

| Fonte | Acesso | O que entrega | Restrição |
|---|---|---|---|
| **OBM** — `obm.com.br` | Público, sem login. As páginas carregam JSON de `/api/v1/…` | Cadeia por vencimento (`/opcoes/{T}`, JSON `/api/v1/opcoes/{T}?venc=AAAA-MM-DD`: `premio`, `bid`, `ask`, `spread_ratio`, `vi_ratio`, Δ Γ Θ ν ρ, `oi`, `volume`, `liq`, `du`); agregado (`/api/v1/opcoes/{T}/agregado-diario`: `iv_atm_30d/60d/90d_ratio`, `skew_25d_30d_ratio`, OI e volume do papel); `vi-historico` (VI ATM de 252 sessões); `/opcoes/{t}/volatilidade` (smile, VI ATM por vencimento, superfície); `/acoes/{t}` (OHLC diário de 1 ano com `volume_financial` e `trades_count`, embutido no payload RSC); `/tesouro-direto/curva` (ETTJ prefixada ANBIMA em du/252); `/calendario-dividendos` (proventos por data ex); `/opcoes/montador` (payoff no servidor: custo, breakeven, lucro/perda máximos, gregas somadas, choque de tempo e vol, exercício A/E) | **Só o último pregão**: `?date=`/`?data=` são ignorados. `robots.txt` bloqueia `/api/` para robôs: o script abre as **páginas públicas** e guarda o JSON que elas já carregam, sem chamar `/api/v1` direto. A API oficial (`api.obm.com.br`, chave grátis) não tem dataset de opções. |
| **opcoes.net.br** — distinto de **OpçõesBR** (`opcoesbr.com.br`, F10.1) | Público, sem login, para a grade e o cabeçalho | Grade `/opcoes/bovespa/{T}` (DataTable `#tblListaOpc`: tipo, **estilo `Mod.` A/E**, strike, último, negócios, volume, IV, Δ, Γ, Θ R$, **Θ %**, ν, coberto/travado/descoberto); cabeçalho com VH e IV calls/puts com rank, percentil, mín e máx; calculadora B&S `/calculadora-Black-Scholes` | **Só o último pregão**; forçar `quotesMode=UP` ("Último pregão"), nunca `RT`. Selecionar `#vAAAA-MM-DD` e esperar a contagem do DataTable estabilizar. A metodologia "ATM de 4 séries / RV30 em 21 retornos" pertence a `opcoesbr.com.br`, não a este site. Tabela de volatilidades por ação, histórico, skew e simulador pedem login — **fora** do plano. |
| **B3Lab** — `b3lab.com.br/calculadora-black-scholes` | Público, sem login, cálculo no navegador | Preço teórico, IV e as 5 gregas a partir de S, K, vencimento (data, dias corridos ou **dias úteis**), r, σ e q; campos React com id terminando em `-spot`, `-strike`, `-rate`, `-sigma`, `-dividend-yield` | Black-Scholes com `q`: Black-76 equivale a `S = F`, `q = r`. **F10.1 confirmou `r` contínua e `T = du/252`**; o "30 dias corridos" exibido é só tradução do prazo. A **grade pública não serve**: em 25/11 usava spot 40,55 (fechamento 41,36) e só 3 vencimentos. |
| **OpLab** | — | Só referência de produto/convenções | Dado ao vivo e métricas proprietárias não entram na comparação. |

## Matriz de cobertura

**Direto** = a fonte publica o número. **Recalculado** = dado externo + fórmula reimplementada no script de
benchmark, fora de `backend/src/b3o` (confere dado e código, não metodologia de terceiro — o relatório marca
assim). **Descritivo** = regra diferente; só se descreve, sem gate numérico.

| Métrica | Fonte principal | Segunda fonte | Tipo |
|---|---|---|---|
| Spot / fechamento | OBM (OHLC) | opcoes.net.br | direto |
| Strike, vencimento, du | OBM (`du`) | opcoes.net.br (`d.u.`) | direto |
| Estilo A/E | opcoes.net.br (`Mod.`) | — | direto |
| Prêmio, bid/ask, `spread_pct` | OBM (`premio`, `bid`, `ask`, `spread_ratio`) | opcoes.net.br (último) | direto |
| `iv` por série | OBM `vi_ratio` | opcoes.net.br; B3Lab invertendo o prêmio | direto |
| `delta`, `gamma`, `vega_brl`, `theta_brl`, `rho` | OBM | opcoes.net.br (sem ρ); B3Lab com os nossos inputs | direto |
| `theta_pct` | opcoes.net.br | B3Lab | direto |
| `iv_atm_30d` | OBM `iv_atm_30d_ratio` | opcoes.net.br (cabeçalho; metodologia pública não confirmada) | direto |
| Estrutura a termo (IV ATM por vencimento) | OBM `/volatilidade` | — | direto |
| `ts_slope_30_90` | OBM (`iv_atm_30d` e `iv_atm_90d`) | — | recalculado |
| Smile | OBM `/volatilidade` | opcoes.net.br (IV por strike) | direto |
| `rr25` | OBM `skew_25d_30d_ratio` (definição a confirmar) | 25Δ recalculado do smile do OBM com a nossa definição | direto + recalculado |
| `bfly25` | smile do OBM nos pontos 25Δ | smile do opcoes.net.br | recalculado |
| `iv_rank_252`, `iv_pct_252` | OBM (rank + `vi-historico`) | opcoes.net.br (rank/percentil, calls e puts separados) | direto + recalculado |
| `rv20`, `rv30`, `rv60` (Yang-Zhang) | OHLC do OBM + Yang-Zhang reimplementado | opcoes.net.br VH (fechamento a fechamento) | recalculado; VH só descritivo |
| Pregão dominante (`rv_shock_day`) | OHLC do OBM | — | recalculado |
| `vrp`, `vrp_z_252` | IV ATM do OBM − RV recalculada; z pelo `vi-historico` | — | recalculado |
| `exp_move_1s_30d` | `iv_atm_30d` do OBM | — | recalculado |
| DF / curva | ETTJ prefixada ANBIMA (via OBM) | — | direto (fonte independente da DI da B3) |
| `forward_30d` | S·e^{rt} − VP dos proventos (ETTJ + calendário do OBM) | — | recalculado |
| `implied_div_yield_30d` | proventos com data ex na janela (calendário do OBM) | — | recalculado |
| Volume, negócios | OBM (`volume_financial`, `trades_count`) | opcoes.net.br | direto |
| OI coberto/descoberto/total | OBM `oi` | opcoes.net.br (coberto, travado, descoberto) | direto |
| `days_to_liquidate` | OI e volume do OBM | — | recalculado |
| `is_liquid` | OBM `liq` | — | descritivo |
| Preço teórico | B3Lab (`S = F`, `q = r`) | calculadora do opcoes.net.br | direto |
| Estrutura (F4): prêmio líquido, breakeven, perda/ganho máximo, gregas somadas, cenário tempo × vol | montador do OBM — trava de alta, trava de baixa, straddle, put caixa | — | direto |
| Venda coberta (perna de ação) | montador público do OBM **não aceita perna de ação** (F10.1) | — | `unsupported_external` |
| P&L da posição, exposição delta em R$ | `premio` e `delta` do OBM × quantidade | — | recalculado |

**Fora do benchmark:** Diário, previsão, desfecho e calibração pessoal (não são métricas de mercado; nenhuma
fonte publica equivalente — verificação por recontagem interna) e `signal_class` (fora do produto, ADR-023).

## Execução

- **Amostra:** 5 papéis (PETR4, VALE3, BOVA11, B3SA3, ITUB4), 2 vencimentos mensais por papel (o do smile e o
  seguinte), todas as séries com cotação nos dois sites; calls/puts europeias líquidas no gate de IV/Gregas.
- **A captura depende do dia; a comparação não.** OBM e opcoes.net.br só mostram o último pregão. A captura
  lê `/api/health` e **aborta** se a data de qualquer fonte for diferente da sessão do lake; grava os dados
  brutos com URL e horário de acesso. A comparação roda depois, quantas vezes for preciso, sobre o que foi gravado.
- **Scripts** (em `backend/scripts/`, sem suíte de testes — ADR-019):
  - `bench_capture.py` — Playwright; OBM (cadeia, agregado, `vi-historico`, volatilidade, OHLC, ETTJ,
    proventos) e opcoes.net.br (grade por vencimento + cabeçalho) → `data/bench/AAAA-MM-DD/`.
  - `bench_b3lab.py` — Playwright; preenche a calculadora com os inputs das nossas séries (F, DF, du/252, σ)
    e grava as saídas. Não depende da sessão.
  - `bench_structures.py` — Playwright; monta no montador do OBM as estruturas de referência e grava o resultado.
  - `bench_compare.py` — sem Playwright, no serviço `test`; lê o bruto e o nosso lado pela `QueryService`/`/api`,
    casa por `(ticker, vencimento, strike)` e lado, normaliza unidades (`vi_ratio` × 100, Θ por dia corrido/útil,
    ν por 1 ponto/1%, ação × contrato) e escreve **JSON intermediário** para o relatório futuro.
  - `bench_recalc.py` — funções puras, sem importar `b3o`, com docstrings citando o glossário e a unidade;
    recalcula as métricas da matriz para a comparação futura. Nesta retomada não gera relatório final.
- **Container:** os três scripts de Playwright rodam num serviço `bench` do compose (profile próprio, imagem
  oficial `mcr.microsoft.com/playwright/python`). Playwright **não** entra no `pyproject.toml` do produto.
- **Volume:** uma captura por sessão, poucas dezenas de páginas, sem paralelismo.

| ID | Tarefa | Est. |
|---|---|---|
| F10.1 | **Verificações antes do gate:** (a) confirmar que "OpçõesBR" é o opcoes.net.br pela metodologia publicada (ATM de 4 séries, RV30 em 21 pregões); (b) definição e sinal do `skew_25d_30d_ratio` do OBM; (c) convenção de `r` do B3Lab, calibrada num caso conhecido; (d) se o montador do OBM aceita perna de ação; (e) como trocar de vencimento na grade do opcoes.net.br pelo Playwright. Registrar cada resposta com data | 45 min |
| F10.2 | `bench_capture.py` + serviço `bench`; capturar a sessão do lake com guarda de data | 1 h 30 |
| F10.3 | `bench_b3lab.py` e `bench_structures.py` | 1 h |
| F10.4 | `bench_compare.py`: IV por série/ATM/smile/termo, Gregas e preço — `n`, mediana, P90 e máximo das diferenças (pontos de vol para IV) | 1 h 30 |
| F10.5 | Recalculados: RV Yang-Zhang, `rv_shock_day`, VRP/z, rank/percentil, `rr25`/`bfly25`, `ts_slope_30_90`, forward, dividendo implícito, DF, `days_to_liquidate`, expected move, P&L/exposição | 1 h 30 |
| F10.6 | Relatório em `data/out/benchmark-AAAA-MM-DD.md`: matriz preenchida (fonte, URL, data de acesso, `n`, estatística), outliers e classificação (`metodologia`, `liquidez`, `dado externo`, `bug interno`); registrar aprendizado no `AGENTS.md` e abrir correção só para bug reproduzido | 45 min |

### Estado dos mecanismos (retomada de 26/11/2026)

| Pacote | Arquivo | Estado |
|---|---|---|
| P1 / F10.1 | `data/bench/f10_1-verificacoes.md` | Verificações concluídas; `skew_25d_30d_ratio` pendente |
| P2 / F10.2 (infra) | `compose.yaml`, `Dockerfile.bench` | Concluído e commitado (`826690e`) |
| P3 | `backend/scripts/bench_capture.py` | Em construção; execução futura, proibida nesta retomada |
| P4 | `backend/scripts/bench_b3lab.py` | Em construção; execução futura, proibida nesta retomada |
| P5 | `backend/scripts/bench_structures.py` | Em construção; execução futura, proibida nesta retomada |
| P7 | `backend/scripts/bench_recalc.py` | Em construção; somente funções puras e validação estática |
| P6 | `backend/scripts/bench_compare.py` | Não iniciado; depende de P3/P4/P5/P7 e produz só JSON intermediário |
| Captura e relatório | `data/bench/`, `data/out/` | Não executados; fora do escopo desta retomada |

**Aceitação F10**
- [ ] toda métrica de mercado da matriz tem ao menos uma referência externa no relatório (direta ou
      recalculada), e as que ficam fora do benchmark aparecem listadas com o motivo
- [ ] toda comparação usa a mesma sessão e o mesmo contrato; spot/forward, strike, vencimento, estilo,
      taxa, prazo e unidade estão declarados, com fonte, URL e data de acesso externas
- [ ] IV por série/ATM/smile/termo tem duas referências EOD na amostra, com estatística de diferença e
      outliers individuais explicados
- [ ] preço teórico e Gregas são conferidos com inputs idênticos na calculadora; diferenças de unidade
      (por ação/contrato, por dia corrido/dia útil, por 1 ponto/1% de vol) não são confundidas com erro
- [ ] métricas recalculadas aparecem marcadas como tal; IV rank/percentil, RV e VRP só têm gate numérico
      quando a construção é equivalente (VH do opcoes.net.br × Yang-Zhang fica descritivo)
- [ ] as cinco verificações do F10.1 estão respondidas e registradas
- [ ] toda divergência material tem causa classificada; só vira correção após reprodução independente no
      dado interno — nunca apenas para igualar o site externo
- [ ] relatório e brutos salvos fora do git; Playwright, scraping, serviço pago ou cotação ao vivo não viram
      dependência do produto; se um bug mudar fórmula, `model_version` sobe e o histórico é recalculado inteiro

---

## Grafo de dependências

```
F0 ──> F1 ──> F1b ──> F2 ──> F3 ──> F4 ──> F5 ──> F6 ──> F7 ──> F8 ──> F9
              portão

F1 + F3 + F4 ──> F10   (benchmark externo; independente de F9)
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
| IV | paridade call E × put E; benchmark externo F10 (OBM + opcoes.net.br; B3Lab para preço/Gregas) |
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
podman compose up -d prepare-watch                         # serviço padrão: prepare ao iniciar e a cada hora (ADR-036)
podman compose --profile dev up dev                        # backend com --reload
```

| Variável | Uso |
|---|---|
| `B3_DATA_DIR` | raiz do lake no container (`/data`, bind de `./data`) |
| `B3_APP_PORT` | porta publicada no host (default `8080`; a app no container é sempre `:8080`) |
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

