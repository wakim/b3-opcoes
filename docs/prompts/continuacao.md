# Sessão autônoma — Opções B3: continuação (F0 fecho → F1 → F1b portão → F2/F3)

Você é o orquestrador desta sessão. Trabalhe sozinho, sem pedir confirmação.
Este documento é o seu contrato: o que fazer, em que ordem, como provar, quando
parar, como registrar o que decidiu.

**Pasta:** `C:\Users\wakim\Projects\b3-opcoes` · **Runner:** `podman compose`
(o host não tem Docker) · **Data de referência:** a do arquivo, nunca o relógio.

## 1. Estado de partida (medido ao fim da sessão anterior, 22/11/2026)

Provado e revisado (ver `git log` e `out/relatorio-sessao.md` da sessão anterior):

- **Onda 0** — bootstrap completo; build do zero ~32 s (critério < 5 min).
- **Onda A** — fetch condicional (ETag→304 no real), validação coletiva
  (ADR-007), parser streaming (3,57 M linhas, RSS ~21 MiB), estilo pela regra
  medida (puts: zero americanas). Revisão independente: APROVADA.
- **Onda B** — lake parquet determinístico (sha igual em 2 escritas do real),
  legs + eventos de strike (SBSP ×0,2 escala, LOGG Δ=R$ 2,86, ABEVX131
  não-data por contiguidade, 313 eventos no ano), curva DI em cascata
  (PRE→APR→Selic, % a.a.), earnings CVM sem look-ahead, OI do BDI (posição de
  opção = `total_qty` em ações-objeto). Revisão independente: APROVADA.
- **Governança** — ADRs 001–010 em `docs/decisoes.md`; dependências sempre na
  última estável (ADR-008, auditoria com `chore(deps):` separado); golden
  fixtures são DADO (`.gitattributes` `-text`); `b3o.fsutil.replace_file`
  obrigatório para todo escritor em `/data` (EACCES do bind); guarda de
  arquitetura pega `now/today` com argumentos (whitelist ADR-010 com marcador).
- **Contratos de onda já escritos e commitados** (leia ANTES de abrir cada
  onda; divergência de contrato é bug): `docs/contratos/onda-c.md` (prepare/
  health/liquidez — pode já estar implementado), `onda-d.md` (conventions/
  forward/iv/greeks + realized), `onda-e.md` (ATM/VRP/skew/dist + checks),
  `onda-fg.md` (CLI metrics/backfill + **O PORTÃO F1b com critérios
  pré-registrados**).

**Verifique o estado real primeiro:** `git log --oneline`, `podman compose run
--rm test pytest`, `podman compose run --rm test ruff check src tests`,
`podman compose run --rm app b3o health`. O que estiver verde e revisado, não
refaça; o que estiver incompleto, complete pelo contrato correspondente.

## 2. Leitura obrigatória (nesta ordem, inteiro)

1. `PLAN.md` — critérios de aceitação (só existem lá) e o que já está `[x]`.
2. `docs/spec.md` — schema (§5), derivação (§6), runtime (§4).
3. `AGENTS.md` — **atualizado pelo dono em 22/11: Python 3.15 e `node:26-slim`
   são a convenção nova** (ver §"Convenções de código", "Frontend" e
   "Ambiente"). Conflito com a spec §4.1 (`node:22-slim`) e com o estado atual
   do repo (3.12): resolva na Onda 0 abaixo e registre ADR.
4. `docs/decisoes.md` — ADRs 001–010 e a tabela P-A…P-T.
5. `docs/contratos/onda-{c,d,e,fg}.md` — os contratos das próximas ondas.

Se fontes conflitarem: spec > PLAN > AGENTS > decisões — EXCETO no caso do
runtime (3.15/node:26), em que o AGENTS editado pelo dono é a vontade mais
recente: registre o conflito como ADR curto ("AGENTS 22/11 supere a spec §4.1
em versões de runtime; spec a atualizar na próxima revisão") e siga o AGENTS.

## 3. Regras que não se quebram (herdadas — valem sem repetir o resto do doc)

1. **O Python calcula.** Nenhum número nasce de LLM. Valor esperado de teste
   vem de fonte citada ou propriedade matemática — nunca do código sob teste.
2. **EOD é o dado.** Nada de preço vivo em métrica.
3. **Uma camada de consulta.** SQL/duckdb só em `store/` e `query/service.py`
   (guarda de arquitetura verde, SEMPRE).
4. **Falha explícita.** `{code,message,hint}` ou `null` + flag; nunca zero
   silencioso; contagens de linhas puladas nunca capadas (ADR-007).
5. **Sem arredondamento no cálculo**; centavos int64; `rate_annual` em % a.a.
6. **Container é o caminho oficial** (`podman compose run --rm test pytest`).
   `uv lock` só no container com o workaround do AGENTS.
7. **Não reescrever trabalho do dono:** docs de direção, mockups, BACKLOG,
   estrutura do PLAN (checkbox marca; nada apaga).
8. **`model_version = 1`** no primeiro cálculo de métrica (Onda F); sobe a
   cada mudança de fórmula com recálculo inteiro.
9. **Sem push.** Commit local por pacote, `tipo(escopo): resultado`.
10. **Armadilha nova → AGENTS.md na mesma tarefa.**

## 4. Onda 0 — migração de runtime (se ainda não feita)

O dono editou o AGENTS: **Python 3.15** e **`node:26-slim`**. Hoje o repo está
em 3.12/pyproject `>=3.12`/Dockerfile `python:3.12-slim`. Migre ANTES de abrir
as ondas de cálculo (tudo o que vier roda em cima desta imagem):

1. `pyproject.toml`: `requires-python = ">=3.15"`; Dockerfile: `python:3.15-slim`;
   estágio `web`: `node:26-slim`; compose: serviço `web` → `node:26-slim`
   (e a linha da spec §4.1 fica registrada como ADR de conflito, item §2).
2. `uv lock` NO CONTAINER (workaround do bind do AGENTS); commit do lock.
3. `podman compose build` + `pytest` + `ruff` verdes; se algo quebrar na
   migração (deprecação 3.12→3.15), conserte nos donos certos e registre.
4. Commit: `chore(runtime): Python 3.15 + node 26 (diretiva do dono, AGENTS 22/11)`.
5. Criterio de pronto: build < 5 min do zero (cronometre) e suite verde.

## 5. Protocolo de execução (o mesmo da sessão anterior)

- **Ondas** com revisor obrigatório (sessão NOVA, nunca o autor) antes de
  abrir a próxima; duas rodadas máx, na terceira o orquestrador decide e
  registra ADR.
- **Contrato antes do código** — já está todo escrito (`docs/contratos/`);
  abrir a onda = ler o contrato + distribuir pacotes.
- **Um dono por arquivo**; commit por pacote; retry em `index.lock`.
- **Implementador:** `general`; **Revisor:** `general` nova sessão (template
  do `docs/prompts/f0-f1b.md` continua válido); **Auditor de dado:** `general`
  nova sessão, contagem/valor RECALCULADO por fora do código do projeto.
- Modelo dos sub-agentes: o da sessão anterior (DeepSeek v4.1 flash high).
- **Definição de pronto por pacote:** roda no real de 2026; teste automatizado
  + 1 número conferido à mão por caminho independente; falha explícita;
  `pytest`/`ruff` verdes por compose; armadilha anotada no AGENTS; commit.

## 6. Ondas

```
Onda A'  fecho do F0 (se o C1 da sessão anterior não tiver pousado):
         C1 prepare/health/liquidez (contrato onda-c.md)
         → revisão + AUDITORIA DE DADO OBRIGATÓRIA (abaixo)
         → aceitação F0 item a item (PLAN) → [x] com parecer → Estado atual
Onda D   F1.1–F1.4 (conventions/forward/iv/greeks) ∥ F1.6 (realized/events)   [onda-d.md]
         → revisão quant obrigatória: IV reimplementada por bisect independente
           em 20 séries; forward OTM-only vs ITM-only; z usa só o passado (cite
           a linha); uma grega contra payoff numérico.
Onda E   F1.5/F1.7/F1.8/F1.9 (atm/vrp/skew/dist) ∥ F1.10 (checks)             [onda-e.md]
Onda F   F1 CLI: b3o metrics --date/--backfill; core.metrics_*; validação
         externa (paridade call E × put E em 5 papéis; PETR4 vs brapi com
         conversão de convenção; brapi fora → P-J e segue)                    [onda-fg.md §1]
         → revisão + aceitação F1 (as 8 primeiras linhas do PLAN)
Onda G   F1b: backtest + relatório ∥ teste de look-ahead                       [onda-fg.md §2]
         → O PORTÃO (critérios pré-registrados, §8 abaixo)
Onda H   (só se o portão passar) F2 funil → F3 MCP                             [§9]
```

Ordem do dado continua ADR-006: 2026 primeiro; 2024/2025 entram no backfill
do F1/F1b (`prepare --year`, anos fechados imutáveis).

## 7. Auditoria de dado obrigatória (fecho do F0)

Sub-agente novo, sem usar o código do projeto:
1. Recontar linhas por `refdate` direto no TXT (script descartável de ~20
   linhas ou PowerShell) e comparar com o `ingest_state.counts_by_date`.
2. Escolher 3 tickers com ajuste e refazer a leitura do strike dia a dia à mão
   contra o `strike_events` gravado (SBSP ×0,2 e SIMH ×2 têm que estar como
   escala; LOGG R$ 2,86 como provento).
3. Conferir 2 linhas do golden file byte a byte contra o TXT bruto.
Divergência = F0 reprovado; corrige e re-audita.

## 8. O PORTÃO (pré-registrado — não otimize depois de ver o resultado)

No período completo 2024–2026, após 252 sessões de aquecimento, janela
`rv_fwd20` fixa, recortes só classe/papel/resultado (com folga de 1 sessão do
ADR-010 na janela de earnings):

- **`premium_rich` passa se:** `n ≥ 50`, taxa de acerto ≥ 55%, mediana de
  `edge ≥ +1,0` vol point, e taxa ≥ taxa do `none` + 5 p.p.
- **`premium_cheap` simétrico:** acerto = `edge < 0`, mediana ≤ −1,0, mesma
  margem sobre `none`.

**Passou** → ADR + AGENTS, F1b `[x]`, siga para F2/F3.
**Não passou** → PARE o produto. `out/backtest.md` com resultado + leitura em
10 linhas + decisão proposta em `docs/decisoes.md` (pendente do dono). Marque
só os critérios que passaram. **Proibido:** mudar limiar, mudar janela,
escolher subconjunto, recalcular modelo para "consertar".

O teste G2 (permutação do futuro) **exige** mudança material do resultado —
métrica constante = teste falho.

## 9. Ondas H (só com o portão aberto)

- **F2 funil** (spec §6.9 classes, ranking por componentes, `b3o screen`,
  `out/screen-*.md` com próximo resultado e taxa histórica da classe; todo
  papel do universo aparece com classe ou motivo de exclusão).
- **F3 MCP** (spec §7/§8: FastAPI + MCP Streamable HTTP em `/mcp` na 8080,
  tools sobre `QueryService`, `max_rows` 20/60, teto 200 KB com `truncated`,
  erro `{error:{code,message,hint}}`, teste de contrato MCP × HTTP).
- Revisão de contrato; ADRs das escolhas (SDK `mcp`, camadas).

## 10. Ao terminar a sessão

1. `PLAN.md`: marque `[x]` **só com parecer de revisor em mãos**; atualize a
   tabela "Estado atual"; nada apagado.
2. **Cronograma em `out/relatorio-sessao.md`:** por fase — fechado/aberto,
   critérios com veredito e evidência, tempos reais contra estimativa do PLAN,
   ADRs criados, a tabela do portão com números reais e a projeção honesta do
   que falta (com o fator 2–3× do PLAN).
3. AGENTS.md com toda armadilha nova (regra 10) e `docs/decisoes.md` com ADRs.
4. Mensagem final curta ao dono: (1) o que está provado, (2) o que não está e
   por quê, (3) ADRs provisórios para ratificar, (4) próximo passo óbvio.

## 11. Só quatro motivos para parar e perguntar

Dinheiro/conta/credencial/contrato · critério que falha e cuja correção muda a
spec · dado que não existe na fonte depois de todos os fallbacks · decisão que
apaga/reescreve trabalho do dono. Qualquer outro caso: default do
`docs/decisoes.md`, ADR curto, siga.
