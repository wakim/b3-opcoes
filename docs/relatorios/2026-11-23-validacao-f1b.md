# Validação independente do F1b (o portão) — 23/11/2026

> **Nota de correção (23/11/2026, ADR-034):** este relatório trata 2026-10-26 como "data-armadilha"/"artefato de dado"/"split sem ajuste". A reavaliação mostrou que é um **choque real de mercado** (IBOV −14,35%, real desvalorizado, sem evento corporativo, forward por paridade acompanhando o spot novo). Os números medidos aqui continuam valendo; a interpretação de "artefato" não. O texto abaixo fica como estava, por registro.

**Revisor:** sessão nova, não escreveu o código do F1b. **Runner:** `podman compose`, da raiz.
**Commit revisado:** `73eeca0` (`feat(f1b/backtest): … ambas as classes NAO PASSAM`), `HEAD == origin/main`.
**Scripts do revisor:** `data/tmp/validacao-f1b/` (fora do git). As conferências manuais **não importam `b3o.derive.*`** como fonte da verdade — Yang-Zhang (forma do paper), z amostral, mediana/percentil e agregação foram reimplementados por fora. `b3o.metrics.backtest.load_observations` e `b3o.query` foram importados **só para comparar** com o valor do pipeline.
**Escopo:** verificar que o veredito **NÃO PASSA** é confiável (cálculo certo, sem look-ahead, critérios intactos, F0/F1 intactos). **Não corrijo, não otimizo, não decido o portão.**

---

## 1. Parecer

**APROVADO** — o veredito **NÃO PASSA** das duas classes se reproduz por fora com **diferença 0** em todas as 6.773 observações, os critérios pré-registrados estão intactos, não há look-ahead e o estado de F0/F1 está intacto. Nenhum defeito de cálculo foi encontrado.

> **O NÃO PASSA é confiável.** A decisão de seguir/parar permanece **pendente do dono** (ADR-023). Este parecer não a toma nem propõe novo limiar.

Ressalvas informativas (não são defeitos, não mudam o veredito): §5.

---

## 2. Pré-condições (todas verdes)

| Checagem | Medido | Esperado | |
|---|---|---|---|
| `git log --oneline -3` topo | `73eeca0 feat(f1b/backtest)…` | idem | ✔ |
| `git rev-parse HEAD` == `origin/main` | `73eeca076d…` == `73eeca076d…` | igual | ✔ |
| `git status` | só `?? docs/prompts/validacao-f1b.md` (prompt do dono, não-rastreado); `git diff --stat` vazio | limpo (código) | ✔ |
| `ruff check src` | `All checks passed!` | limpo | ✔ |
| `metrics_underlying` linhas / sessões / intervalo | **116.832** / **722** / 2024-01-02→2026-11-19 | idem | ✔ |
| `model_version` (todas as linhas) | `[2]` (116.832) | todas 2 | ✔ |
| por ano (linhas/sessões) | 2024: 40.488/251 · 2025: 39.569/250 · 2026: 36.775/221 | idem parecer F1 | ✔ |
| `metrics_series` model_version | `[2]` (7.767.407) | todas 2 | ✔ |
| sha256 `metrics_underlying` 2026 | `4c40a8b03b68c13b0ad38cb04bb41cb2592e7879ed49aea98c66d2fdf5fa5198` | `4c40a8b0…` (F1) | ✔ |
| sha256 `metrics_series` 2026 | `0a4d2c9d0ac81b30652b16a84f499d4a174c9d15230338b0e90a3606d0572031` | `0a4d2c9d…` (F1) | ✔ |
| `backend/config.yaml` | `model_version: 2` | inalterado | ✔ |

**O F1b não recalculou métricas**: os sha das partições de 2026 casam exatamente com o parecer do F1. ✔

---

## 3. Tabela item a item (A–E)

### A. O veredito se reproduz por fora

| # | Item | Medido (meu recálculo) | Esperado / relatório | |
|---|---|---|---|---|
| A1 | Classes **antes** do descarte (com z) | none 4526 · event 1142 · cheap 844 · rich 650 · skew 31 (Σ 7193) | idem | ✔ |
| A1 | Descartes | sem z 109.639 · sem IV **0** · sem 20 futuras **420** · sem barra na ponta **0** · RV falhou **0** | idem | ✔ |
| A1 | Classes **depois** (válidas) | rich 617 · cheap 498 · skew 29 · event 1117 · none 4512 (Σ **6773**) | idem | ✔ |
| A1 | `rv_fwd20` meu vs pipeline (6773 obs) | **máx \|diff\| = 0,000e+00** · 0 obs com diff > 1e-6 · conjuntos de (papel,data) iguais | diff ~0 | ✔ |
| A1 | rich | n 617 · acerto **42,30%** · mediana **−1,4128** · P10 −79,5754 · P90 7,7811 | 42,3 / −1,41 / −79,58 / 7,78 | ✔ |
| A1 | cheap | n 498 · acerto **49,80%** · mediana **+0,0038** · P10 −8,4162 · P90 5,0675 | 49,8 / 0,00 / −8,42 / 5,07 | ✔ |
| A1 | skew / event / none | med −2,3367 / −2,5245 / −0,6615 | −2,34 / −2,52 / −0,66 | ✔ |
| A1 | por papel (15 líquidos) | 0 divergências de n/acerto/mediana | idem | ✔ |
| A2 | `GATE_N_MIN` / `GATE_HIT_MIN_PCT` / `GATE_MEDIAN_MIN` / `GATE_MARGIN_PP` | **50 / 55.0 / 1.0 / 5.0** (`derive/backtest.py:44-54`) | idem contrato §2 | ✔ |
| A2 | margem rich / cheap | rich usa `none.edge_pos_pct` (`:255`) · cheap usa `none.edge_neg_pct` (`:259`) | idem | ✔ |
| A2 | janela / aquecimento / período | `FWD_SESSIONS=20` · z 252 por papel (gravado) · período completo 2024-01-02→2026-11-19 | nada alterado | ✔ |
| A3 | P10/P90/mediana à mão (skew, n=29) | mediana rank 14 = **−2,336664** · P10 pos 2,8 → **−33,951370** · P90 pos 25,2 → **7,298005** | −2,34 / −33,95 / 7,30 | ✔ |

**Portão sobre a minha agregação (critérios pré-registrados):**

| Classe | n | Acerto | Mediana | Base `none` | Margem | Veredito |
|---|---:|---:|---:|---:|---:|---|
| `premium_rich` | 617 | 42,30% | −1,4128 | 43,7278% (edge>0) | **−1,4264 p.p.** | **NÃO PASSA** (n✔ acerto✘ mediana✘ margem✘) |
| `premium_cheap` | 498 | 49,80% | +0,0038 | 56,2722% (edge<0) | **−6,4730 p.p.** | **NÃO PASSA** (n✔ acerto✘ mediana✘ margem✘) |

Idêntico ao relatório e ao ADR-023. Nenhuma classe bate acerto ≥ 55%, mediana no sinal certo nem margem ≥ +5 p.p.

### B. O `rv_fwd20` está certo (sem look-ahead, sem off-by-one)

| # | Item | Medido | |
|---|---|---|---|
| B4 | Janela = 21 barras (base + 20 futuras) → **20 retornos** sobre `data+1…data+20` | VALE3 2025-03-10: retornos **== sessões futuras** (2025-03-11…2025-04-07), primeiro = data+1, último = data+20, n=20 | ✔ |
| B4 | Off-by-one descartado | janela até data+19 (2025-04-04) dá rv **25,978** ≠ pipeline **28,045**; pipeline casa com data+20 (diff 0) | ✔ |
| B4 | Conferência à mão (Yang-Zhang próprio), 3 papéis | PETR4 2025-03-20: **38,999816719488** (diff 0) · EGIE3 2025-10-29: **19,772410131568** (diff 0) · VALE3 2025-03-10: **28,045467488462** (diff 0) | ✔ |
| B4 | Provento na janela (exclusão + pullback) | PETR4: ex-div 2025-04-17 **fora** dos retornos; 20 retornos 2025-03-20…2025-04-16 (pullback traz a barra-base); flag `ex_div_excluded`; sem exclusão rv 39,182 (+0,182 — o gap infla) | ✔ |
| B4 | Fator de escala na barra **anterior** | EGIE3 escala 2025-11-27 ×0,714274: barras 24–26/11 adj 0,714274, barra 27/11 adj 1,0; overnight do dia cru **−0,336562** (=ln 0,714) → ajustado **−0,000074**; sem ajuste rv **121,405** vs 19,772 ajustado | ✔ |
| B5 | Mercado da linha (`underlying_market`) | **0 linhas `020`** em `metrics_underlying` (todas as 116.832 são `010`); mecanismo verificado por chamada direta: `spot_bars_in_market('HYPE3F',…,'020')` → 13 barras todas `020`; `'010'` → 0 (não mistura) | ✔ (ressalva §5.2) |
| B6 | 420 descartes = últimas 20 sessões | conjunto de datas descartadas **== `lake[-20:]`** (2026-10-22→2026-11-19, 20 datas); sem barra na ponta **0**, RV falhou **0** | ✔ (ressalva §5.1) |

### C. Look-ahead

| # | Item | Medido | |
|---|---|---|---|
| C7 | Permutação do futuro (seed **987654321**, diferente da do implementador 20261123) | rich acerto **42,30→66,13%** (Δ+23,83 p.p.), mediana −1,41→+6,93 · cheap **49,80→58,43%** (Δ+8,64 p.p.), mediana 0,00→−2,46 · none mediana −0,66→−2,81. **PROVA PASSA** (mudou materialmente) | ✔ |
| C8 | z recalculado por fora (3 papéis × 3 datas: VALE3, BBAS3, ITUB4) | **9/9 com \|diff\| = 0,000e+00**; fatiar a série completa (com futuro) até a data dá o **mesmo z** | ✔ |
| C8 | Linhas que fatiam o histórico | `derive/vrp.py:112` (`series[-window:]`) · `metrics/underlying.py:171` (`history[-(window-1):]` + hoje) · `metrics/pipeline.py:595` (`refdate >= first_session: continue`, seed só passado) · `:871` (`window_with_today`) | ✔ |
| C9 | Classe gravada, não recalculada | `signal_class()` só é chamada em `metrics/underlying.py:267` (pipeline F1 que grava). O backtest importa só constantes (`CLASS_ORDER, NONE, PREMIUM_CHEAP, PREMIUM_RICH`); lê `row.signal_class` do parquet (`metrics/backtest.py:270`) | ✔ |

### D. Recortes

| # | Item | Medido | |
|---|---|---|---|
| D10 | Earnings: só `actual`, folga de 1 sessão | calendário: actual 2757 · estimated 398 (**fora**). BBAS3 DT_RECEB 2026-05-13 → pregão **2026-05-14**; PETR4 2026-05-11 → **2026-05-12** (batem com AGENTS). `bisect_right` = primeira sessão estritamente posterior | ✔ |
| D10 | `earnings_na_janela` à mão | BBAS3 393 obs e PETR4 409 obs: **0 flags divergentes** do cálculo à mão (data real + 1 sessão ∈ data+1…data+20) | ✔ |
| D10 | n do recorte com/sem | rich 176/441 · cheap 145/353 · skew 10/19 · event 629/488 · none 1004/3508 | ✔ (idem relatório) |
| D11 | Trap 2026-10-26 (informativo) | cheap **com** a trap: n **33**, acerto **100%**, mediana **−32,8189** · **sem**: n **465**, acerto **46,24%**, mediana **+0,1731** | ✔ |
| D11 | Definição da janela | rv30 passada (30 sessões até data) + rv_fwd20 futura (20) contém 2026-10-26 (`metrics/backtest.py:266`, `lake[idx-29:idx+21]`) | ✔ |
| D11 | Veredito usa período completo | gate sobre as 498 cheap (sem excluir trap): acerto 49,80%, mediana +0,0038 → a trap **não** entra no gate (`gate_verdicts` usa `by_class` completo) | ✔ |
| D11 | Edge extremo rastreado às barras cruas | rich mín **−217,5275** = CYRE3 2026-09-30 (iv 57,9448 − rv_fwd 275,4722). Barras: C23/10 **3557** → O26/10 **1779**, razão **0,5001**, overnight ln **−0,692866**. **0 eventos B2 em 26/10**; **124 séries de opção pareadas (via ISIN BRCYREACNOR7), 0 com strike diferente** → split sem ajuste de strike = **artefato de dado**, não bug | ✔ |

### E. Arquitetura e estado anterior

| # | Item | Medido | |
|---|---|---|---|
| E12 | Regra de SQL | `import duckdb` só em `query/service.py:44` e `store/metrics.py:40`; nenhum em `metrics/` ou `derive/` (só docstrings). Nenhum `round(` em `derive/`. Métodos novos usam **1** `read_parquet(?)` por consulta (posicional seguro com uma tabela; a armadilha é com duas) | ✔ |
| E12 | Leitura via QueryService / escrita via store | `metrics/backtest.py` é só leitura (sem `store`, sem escrita); relatório gravado por `cli._write_text_atomic` → `fsutil.replace_file` (texto em `data/out/`, não no lake) | ✔ |
| E13 | F0/F1 intactos | `git show 73eeca0 --stat`: só `cli.py`, `derive/backtest.py`, `metrics/backtest.py`, `query/service.py`, `docs/decisoes.md` (5 arquivos, +1008). **Não** toca `derive/realized.py`, `derive/signal.py`, `metrics/pipeline.py` nem `config.yaml`/`model_version` | ✔ |
| E14 | Idempotência | `b3o backtest` 2×: run1 vs run2 **0 diff** (fora commit); orig vs run1 **0 diff** (fora commit). Commit no container = "não resolvido" (sem git na imagem); orig = `73eeca0`. `data/out/backtest.md` no gitignore (`git check-ignore` confirma) | ✔ |
| E15 | Governança | `PLAN.md` F1b **não** marcado `[x]` (linhas 151-153 `[ ]`); `AGENTS.md` **não** registra "o sinal passou" (grep); ADR-023 **pendente do dono** com as 3 opções (parar / mudar limiar a priori + fora da amostra / trocar sinal) | ✔ |
| E15 | Por-papel inclui os 15 líquidos | todos os 15 presentes; **AXIA3 com 0 obs** explicado: entrou no universo em 2025-11-10, 255 linhas, só 136 com `vrp`, **0 com z**, maior run de vrp consecutivo = **18 < 252** → sem aquecimento | ✔ |

---

## 4. Defeitos por severidade

**Nenhum defeito de cálculo (alta/média/baixa) foi encontrado.** O veredito NÃO PASSA se reproduz com diferença 0 por caminho independente; não há look-ahead; os critérios pré-registrados estão intactos; F0/F1 intactos.

As únicas divergências são entre o **texto do prompt de validação** e o dado medido (não são defeitos do código do F1b) — registradas como ressalvas informativas no §5.

---

## 5. Ressalvas informativas (não mudam o veredito)

1. **Fronteira dos 420 descartes (B6).** O prompt dizia "2026-10-21→2026-11-19". Medido: as últimas 20 sessões do lake são **2026-10-22→2026-11-19**; a sessão 2026-10-21 é a −21 (tem 20 sessões futuras e é **mantida**, 32 obs). A contagem **420** bate exatamente; o conjunto de datas descartadas é exatamente `lake[-20:]`. É um off-by-one no texto do prompt, não no código.

2. **Mercado 020 (B5).** Não existe **nenhuma** linha `underlying_market='020'` em `metrics_underlying` (todas as 116.832 são `010`): o universo com opção negociada é todo lote padrão; os tickers fracionários (`…F`) não têm opção e não entram no funil. O prompt pedia "ache uma linha 020" — não há. O **mecanismo** foi verificado por chamada direta a `spot_bars_in_market` (filtra `tpmerc` corretamente, não mistura mercados). O código usa `row.underlying_market` e está correto; o ramo 020 simplesmente não é exercitado no dado real.

3. **Convenção do pullback (B4, documentada no ADR-022).** Quando um dia ex de provento cai na janela futura, a exclusão **puxa uma barra mais para trás** para manter N=20 — o `rv_fwd20` pode incluir até o retorno do próprio dia-base (passado). Ex.: PETR4 2025-03-20 usa retornos 2025-03-20…2025-04-16. É a convenção pré-registrada (ADR-022 item 3, mesma do `rv20/30/60`), não look-ahead: dado passado é permitido; o que não pode vazar é o futuro no **sinal/z**, e isso foi verificado (C8/C9). Não é defeito.

4. **Contagem da trap (D11).** O AGENTS registra a contagem corrigida de papéis em 26/10 como 45/97 (não 93). O relatório do F1b usa "~45–97 papéis" no texto do recorte — coerente. O recorte do portão é por **observação** (33 cheap com a trap na janela), não por papel; os dois números são consistentes.

---

## 6. Declaração explícita

O veredito **NÃO PASSA** de `premium_rich` e `premium_cheap` **é confiável**: reproduzido por fora com diferença 0 em 6.773 observações, sobre os critérios pré-registrados intactos, sem look-ahead e com o estado de F0/F1 intacto. **A decisão de seguir/parar permanece pendente do dono (ADR-023).** Este parecer não decide o portão nem propõe novo limiar.

---

## 7. Comandos usados (reprodução)

```
git log --oneline -3 ; git status --short ; git rev-parse HEAD ; git rev-parse origin/main
git show 73eeca0 --stat
git check-ignore data/out/backtest.md
podman compose run --rm test ruff check src
podman compose run --rm test python /data/tmp/validacao-f1b/precond.py
podman compose run --rm test python /data/tmp/validacao-f1b/recompute.py
podman compose run --rm test python /data/tmp/validacao-f1b/find_cases.py
podman compose run --rm test python /data/tmp/validacao-f1b/find_scale.py
podman compose run --rm test python /data/tmp/validacao-f1b/rv_detail.py
podman compose run --rm test python /data/tmp/validacao-f1b/z_check.py
podman compose run --rm test python /data/tmp/validacao-f1b/perm.py
podman compose run --rm test python /data/tmp/validacao-f1b/trap_trace.py
podman compose run --rm test python /data/tmp/validacao-f1b/earnings_check.py
podman compose run --rm test python /data/tmp/validacao-f1b/axia3.py
podman compose run --rm test python /data/tmp/validacao-f1b/percentile_hand.py
podman compose run --rm test python /data/tmp/validacao-f1b/final_nums.py
podman compose run --rm test b3o backtest   (2×, idempotência)
```

Nenhum arquivo de código foi editado pelo revisor (`git diff --stat` vazio). O artefato `data/out/backtest.md` foi restaurado ao original do implementador após o teste de idempotência. Scripts descartáveis em `data/tmp/validacao-f1b/` (fora do git).
