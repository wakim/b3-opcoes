# Prompt — Validação independente do F1 (antes da Onda G / portão)

> Cole o bloco abaixo numa sessão nova, com o diretório de trabalho em `C:\Users\wakim\Projects\b3-opcoes`.

---

Você é o **revisor independente do F1** do projeto Opções B3. Não escreveu o código. Seu trabalho é dizer, com evidência medida no dado real, se o F1 pode ser marcado `[x]` no `PLAN.md` e se a Onda G (F1b, o portão) pode começar. **Não comece a Onda G nem o F2** — o entregável é o parecer.

## Leia antes (nesta ordem)
1. `AGENTS.md` — regras, armadilhas do dado e ambiente (tudo roda por `podman compose`, da raiz; **não há suíte de testes**, ADR-019 — não crie `tests/`).
2. `PLAN.md` — seção **F1** (tarefas F1.1–F1.12 e os 8 critérios de aceitação).
3. `docs/relatorios/2026-11-22.md` — §2–4 (o que foi feito), §7 (pendências) e **§9 (estado exato ao parar)**.
4. `docs/decisoes.md` — **ADR-017, ADR-018 (pendente do dono), ADR-019, ADR-020** (`model_version` 2).
5. `docs/contratos/onda-fg.md` — §1 (Onda F) e §2 (portão pré-registrado; só leia, não rode).
6. `docs/glossario.md` e `docs/spec.md` §6 só para conferir fórmula quando precisar.

## Estado de partida (verificado em 23/11/2026)
- Último commit funcional: `6b62498` (carry assimétrico, `model_version=2`); depois só docs e o refactor `backend/`+`frontend/` (`06d617d`).
- **`metrics_underlying`/`metrics_series` estão misturados v1/v2**: o `--backfill` v2 parou no bloco 2025-02-12..2025-03-13 (`data/tmp/backfill_v2.log`); 2024 e o começo de 2025 estão em v2, o resto de 2025 em v1, e 2026 foi reescrito só por um `metrics --date 2026-11-19`. **Nada medido em cima dessas tabelas vale até o recálculo inteiro.**
- Caminhos: o código está em `backend/src/b3o/` (o relatório ainda cita `src/b3o/`); o medidor de aceitação está em `backend/scripts/f1_acceptance.py`. Confira no `Dockerfile`/`compose.yaml` onde ele aparece dentro do container (provavelmente `/app/scripts/`).

## O que fazer

### 1. Recalcular inteiro em v2 (pré-requisito)
- `podman compose build` e confirme `model_version: 2` no `backend/config.yaml`.
- Rode em background (~40 min): `podman compose run --rm test b3o metrics --backfill --from 2024-01-02 --to 2026-11-19`. Se morrer por OOM/EIO, consulte a armadilha do merge streaming no `AGENTS.md` antes de mexer em qualquer coisa.
- Prove por SQL independente (DuckDB direto nos parquets, não pelo `QueryService`): **todas** as linhas com `model_version = 2`, **722** `refdate` distintas em `metrics_underlying`, e contagem de linhas por ano. Registre também a contagem de `metrics_series` (antes eram 7,77 M em v1 — explique a diferença, se houver).
- Idempotência: rode `b3o metrics --date 2026-11-19` de novo e compare o sha256 (ou um hash ordenado das linhas) da partição de 2026 antes e depois.

### 2. Medir os critérios do F1 (um por um, com número)
Monte uma tabela `critério | medido | limiar | passa? | como medi`:
1. Paridade E/E (PETR4, VALE3, BBAS3, ITUB4, B3SA3; ATM ±10%; negócio nas duas): mediana e P90 de `|IV_call − IV_put|` em **pelo menos 3 datas** (19/11, 13/11, 15/10/2026). Compare com o ADR-018 (1,22/5,14 · 1,09/6,33 · 1,84/16,96). O critério original é inatingível segundo o ADR-018 — **não decida por ele**: reporte o número e se ele se mantém em v2.
2. PETR4 vs brapi: confirme que `brapi.dev/api/v2/options/PETR4` continua 401 sem token. Se sim, critério registrado como inviável (P-J), sem inventar referência.
3–6. Rode `backend/scripts/f1_acceptance.py` (forward OTM×ITM ≤ 0,5% do spot; `r ± 100 bp` → `max|ΔIV| < 0,25`; ATM 30d sem salto > 2 vol points em rolagem; RR25 PETR4 < 0 em ≥ 80% dos dias). **Leia o script antes**: confira que ele mede o que o PLAN pede (universo, datas, definição de "dia de rolagem", unidade da IV — fração vs %) e diga se algum critério está sendo medido de forma frouxa.
7. Nenhuma série `inconsistent` entra no smile: prove por SQL que nenhuma série com a flag alimenta ATM/RR25/BFLY25 (veja como o ADR-020 tornou `inconsistent` "informativo" e verifique se isso contradiz o critério do PLAN).
8. `pytest` verde — revogado pelo ADR-019; marque como N/A.

### 3. Conferência à mão por caminho independente (DoD do PLAN)
Escreva scripts **seus** em `data/tmp/revisao-f1/` (não importe `b3o.derive.*`) e confira, para PETR4 em 2026-11-19:
- uma IV europeia ATM via Black-76 + bissecção própria, a partir do `close`, do forward e do DF gravados → deve bater com `metrics_series` (tolerância 1e-4 em fração);
- o forward por paridade recalculado de um par E/E → bate com o gravado; e a regra do carry assimétrico do ADR-020 (F acima da curva rejeitado; dividendo implícito ≤ 25% a.a.) se cumpre em todas as datas da PETR4;
- uma grega (Δ ou vega) por diferença finita da sua própria função de preço;
- RV Yang-Zhang 20d da PETR4 recalculada das barras do `010`, aplicando o fator de escala na barra **anterior** ao evento e excluindo o gap de provento (`AGENTS.md`);
- VRP e IV rank/percentil de 1 dia, pela definição do `docs/glossario.md`.
Cheque também a data-armadilha **2026-10-26** (93 papéis com overnight ~−50%): confirme que o pipeline não "inventou" fator e quantifique quanto `premium_cheap` ela produz — isso é insumo do portão, não erro a corrigir.

### 4. Revisão de código focada
Leia o diff de `e00f23a` e `6b62498` (`git show`) procurando: zero silencioso no lugar de `null`+flag, arredondamento no cálculo, SQL fora do `QueryService`, `read_parquet(?)` posicional repetido, mudança de fórmula sem subir `model_version`. `podman compose run --rm test ruff check src` limpo.

### 5. Pendências do F0 (§7.1 do relatório)
Para cada uma (OI engolido em `except SourceFailure`, `prepare` sem `--year` usando o relógio, evento com `underlying` = ISIN), diga se ainda está aberta e se bloqueia o portão. Não corrija — só classifique.

## Regras
- Todo número vem de comando que você rodou; cite o comando. Nenhum número estimado.
- Não mude fórmula, limiar ou validador para "passar". Achou defeito: descreva com reprodução mínima e pare naquele item.
- Não otimize nada contra o dado do backtest; não leia resultados da Onda G (não existem).
- Achou fato novo sobre o dado: proponha o texto para o `AGENTS.md`, mas **não commite** sem o dono pedir.

## Entregável
`docs/relatorios/2026-11-23-revisao-f1.md` com:
1. **Parecer:** `APROVADO` / `APROVADO COM RESSALVA` / `REPROVADO`, em uma linha.
2. Tabela dos 8 critérios (seção 2).
3. Tabela das conferências à mão (valor do lake × seu valor × diferença).
4. Defeitos encontrados, por severidade, com arquivo:linha.
5. O que o dono precisa decidir antes da Onda G (no mínimo: ADR-018, e se o critério 7 é compatível com o ADR-020).
6. Diff proposto para o `PLAN.md` (quais `[ ]` viram `[x]`, com a evidência ao lado) — sem aplicar.
