# Sessão autônoma — Opções B3: F0 → F1b (com portão)

Você é o orquestrador desta sessão. Trabalhe sozinho, sem pedir confirmação, do início ao fim da fase descrita abaixo. Este documento é o seu contrato: ele define o que fazer, em que ordem, como provar, quando parar e como registrar o que decidiu.

**Pasta:** `C:\Users\wakim\Projects\b3-opcoes` · **Runner:** `podman compose` (o host não tem Docker) · **Data de referência da sessão:** novembro/2026 — a data do dado é a do arquivo, nunca `date.today()`.

---

## 1. Objetivo da sessão

Chegar, com o lake provado, até o **portão do F1b**: métricas de volatilidade calculadas sobre dado real e um backtest que diga se as classes de sinal têm valor. Se o portão passar pelos critérios pré-registrados (§8), continue para F2 (funil) e F3 (MCP) no mesmo fôlego. Se não passar, pare, escreva o relatório e entregue a leitura — **sem** otimizar limiar.

O que **não** é objetivo: UI, PWA, estruturas (F4), posições (F6), qualquer preço vivo.

---

## 2. Leitura obrigatória antes de escrever qualquer código

Leia nesta ordem, inteiro, sem pular:

1. `PLAN.md` — tarefas, estimativas, critérios de aceitação (só existem lá) e riscos.
2. `docs/spec.md` — schema (§5), derivação (§6), `QueryService` (§7), runtime (§4).
3. `AGENTS.md` — regras que não se quebram, armadilhas **já medidas** do arquivo da B3, convenções de código e do ambiente container.
4. `docs/decisoes.md` — decisões pré-autorizadas (tabela P-A…P-T), critérios de desempate e a lista do que **para** a sessão.

Se alguma dessas fontes conflitar: spec > PLAN > AGENTS > decisoes. Registre o conflito em `docs/decisoes.md` como ADR e siga.

---

## 3. Regras invioláveis

1. **O Python calcula.** Nenhum número — IV, grega, retorno, taxa de acerto — pode ser produzido por LLM. Você confere e interpreta; não estima.
2. **EOD é o dado.** Nada de preço vivo, intraday ou endpoint `instrumentQuotation` em métrica.
3. **Uma camada de consulta.** Toda leitura passa por `QueryService`. SQL/`duckdb.connect` só em `store/` e `query/service.py`, garantido por teste de arquitetura.
4. **Nada de zero silencioso.** Indisponível → `null` + flag (`oi_unavailable`, `rate_source`, `sparse_chain`, …). Erro → `{code, message, hint}`.
5. **Nada de arredondar no cálculo.** Só na serialização. Preços em centavos inteiros (`int64`).
6. **Armadilha nova do dado → `AGENTS.md`, na mesma tarefa.** Sem isso ela volta.
7. **Container é o caminho oficial.** Se um comando só funciona no host, ele está errado (exceto `git`, que roda no host).
8. **Não reescrever o trabalho do dono:** `docs/direcao.md`, `docs/*.html`, `BACKLOG.md`, e a estrutura do `PLAN.md` (checkbox pode ser marcado; tarefa não pode ser apagada).
9. **`model_version` sobe a cada mudança de fórmula**, e o histórico é recalculado inteiro. Comece em `1`.
10. **Sem push.** Não há remote. Commit local ao fim de cada pacote.

---

## 4. Protocolo de execução

### 4.1 Ordem das ondas

Cada **onda** é um conjunto de pacotes que podem rodar em paralelo sem escrever no mesmo arquivo. Onda só abre quando a anterior tem teste verde **e** revisão aprovada.

```
Onda 0  bootstrap (você mesmo, sem sub-agente)   git, pyproject, imagem, compose, smoke test
Onda A  F0: fetch+validate (A1) ∥ parse+style (A2)
Onda B  F0: store (B1) ∥ legs (B2) ∥ rates (B3) ∥ earnings (B4) ∥ open_interest (B5)
Onda C  F0: liquidez+health+prepare (C1)          → revisão + auditoria de dado
Onda D  F1: convenções+forward+IV+gregas (D1) ∥ realizada+eventos (D2)
Onda E  F1: ATM+VRP+skew+dist (E1) ∥ validadores (E2)
Onda F  F1: CLI metrics+backfill+validação externa (F1*)   → revisão quant
Onda G  F1b: backtest+relatório (G1) ∥ teste de look-ahead (G2)  → PORTÃO
Onda H  (só se o portão passar) F2 funil, depois F3 MCP     → revisão de contrato
```

Ordem de dado: **2026 primeiro**, ponta a ponta, até a aceitação do F0 fechar (ADR-006). Só depois baixe 2024 e 2025.

### 4.2 Sub-agentes — quem faz o quê

Você **não implementa** os módulos: distribui. Você escreve os contratos, integra, roda as verificações e decide.

| Papel | Agente | Recebe | Entrega |
|---|---|---|---|
| **Implementador** | `general` | pacote (IDs do PLAN), contratos de entrada/saída, seções da spec, arquivos que **possui**, comandos de verificação | código + teste + commit; **não** marca checkbox |
| **Revisor** | `general`, sessão nova, **nunca o autor** | diff/commits, IDs, critérios de aceitação, armadilhas relevantes | parecer por critério: passa / não passa + evidência (saída de comando, não opinião) |
| **Auditor de dado** | `general`, sessão nova | o que conferir à mão, **independente do código do projeto** | contagem/valor recalculado por outro caminho + comparação |
| **Explorador** | `explore` | pergunta pontual sobre o repo | resposta curta, sem edição |

Regras de orquestração:

- **Contrato antes do código.** Antes de abrir uma onda você escreve (ou faz o pacote anterior escrever) o arquivo de contrato: assinatura das funções, dataclasses/pydantic, nomes de coluna, tipos. Os implementadores da onda recebem o mesmo contrato. Divergência de nome de campo é o bug mais caro do projeto.
- **Um dono por arquivo.** Nenhum implementador edita arquivo que não é dele. Precisa mudar contrato alheio? Abre um pacote novo, na onda seguinte.
- **Autor não aprova.** Quem implementou não marca `[x]` no `PLAN.md` nem revisa o próprio pacote. Você marca, com base no parecer do revisor.
- **Duas rodadas, no máximo.** Revisor reprovou → devolve ao implementador com a lista objetiva (arquivo:linha, critério, evidência). Segunda reprovação no mesmo critério → **você** decide, registra em `docs/decisoes.md` como conflito e segue. Nunca três rodadas na mesma areia.
- **Paralelize o que é independente; serialize o que compartilha estado.** `Promise.all`/duas chamadas simultâneas de `subagent` quando os pacotes não se tocam; espera quando um depende do artefato do outro.
- **Toda reprovação vira tarefa**, não "pendência".

Template de prompt para implementador (preencha e envie; não resuma demais):

```
Contexto: repo C:\Users\wakim\Projects\b3-opcoes, produto "Opções B3". Leia antes de codar:
PLAN.md (tarefas {IDs}), docs/spec.md (§{seções}), AGENTS.md (inteiro), docs/decisoes.md.
Stack: Python 3.12 em container. Todo comando: `podman compose run --rm test ...`.
Seu pacote: {IDs}. Você é o único dono destes arquivos: {lista}.
Não edite nada fora da lista. Não marque checkbox no PLAN.md. Não mude fórmula sem avisar.
Contrato (não negociável): {assinaturas, campos, unidades}
Critério de pronto: {critérios copiados do PLAN} — provados por {comandos}.
Armadilhas que se aplicam: {3–6 linhas do AGENTS.md, citadas}
Ao terminar: commit `feat({escopo}): {resultado}`, e responda com (a) o que fez, (b) saída dos comandos
de verificação, (c) dúvidas/decisões que precisou tomar, (d) qualquer coisa que descobriu sobre o dado.
```

Template para revisor:

```
Você revisa, não implementa. Sessão nova, sem confiança no autor.
Repo: C:\Users\wakim\Projects\b3-opcoes. Leia PLAN.md (critérios {IDs}), docs/spec.md (§{seções}),
AGENTS.md, e o diff: `git show {hash}` / `git diff {base}..HEAD`.
Rode você mesmo: {comandos: pytest, prepare, health, contagem}.
Para cada critério: PASSA / NÃO PASSA + a saída que prova. Nada de "parece correto".
Procure especificamente: {lista de riscos da tarefa}.
Também caça: zero silencioso, arredondamento no cálculo, float onde deveria ser centavo inteiro,
query SQL fora de QueryService, teste que testa o mock, mensagem de erro sem `hint`,
nome de campo sem unidade, uso de `date.today()`, `isin` como chave de série.
Resposta final: tabela critério × veredito + até 5 defeitos priorizados (arquivo:linha, por que importa).
```

### 4.3 Definição de pronto (todo pacote)

- roda no dado real de 2026, não só em fixture;
- teste automatizado da fórmula/parser + 1 número conferido à mão;
- falha explícita (erro ou `null` + flag);
- comando de verificação no pacote, executado **pelo revisor**, com saída colada;
- `podman compose run --rm test pytest` verde e `ruff check src tests` limpo;
- armadilha descoberta anotada no `AGENTS.md`;
- commit feito com mensagem `tipo(escopo): resultado`.

---

## 5. Onda 0 — bootstrap (você mesmo, sem sub-agente)

Nada de módulo de negócio aqui. Só o chão.

1. `git init`; `.gitattributes` com `* text=auto eol=lf` (evita diff de CRLF no Windows).
2. `pyproject.toml` (`[project]`, `[project.scripts] b3o = "b3o.cli:main"`, `[dependency-groups] dev`), `uv lock` **no container** — atenção: `uv lock` escrito direto no bind mount falha com `Input/output error (os error 5)`; rode em `/tmp` e copie `uv.lock` de volta (medido, ver `AGENTS.md`). `uv.lock` versionado.
3. Skeleton `src/b3o/{__init__,cli}.py`, `config.yaml`, `tests/test_smoke.py`. No `cli.py`, **sempre** `@app.callback()` na raiz: `typer` 0.29 colapsa app de um comando só e `b3o health` falha como "unexpected extra argument(s)".
4. `Dockerfile` multi-stage (`deps` → `runtime` / `dev`, estágio `web` preparado mas inerte) e `compose.yaml` com `app`, `test`, `dev`, `prepare`, `prepare-watch`, exatamente como a spec §4.1; `.env.example`.
5. Teste de arquitetura provisório: falha se `duckdb.connect`/`.execute(` aparecer fora de `store/` e `query/service.py`; e se `date.today()` aparecer em `src/` (use agora mesmo — é quando é barato).
6. Verificação da onda: `podman compose config` → `podman compose build` (sem cache, cronometrar) → `podman compose run --rm test pytest` → commit.

Se a build falhar por rede/registry, resolva antes de qualquer outra coisa (é o risco `Container sem rede para B3/CVM` do PLAN). Se passar de 30 min no bootstrap, registre a decisão e siga com a imagem mínima.

---

## 6. F0 — o dado

Objetivo: lake reprodutível e o **universo real** de papéis líquidos medido.

### Pacotes

**A1 — download e validação** (`F0.2`, `F0.4`)
Arquivos: `src/b3o/ingest/fetch.py`, `src/b3o/ingest/validate.py`, `tests/test_validate.py`.
Requisitos: `ETag`/`If-Modified-Since` → 304 sem baixar; `httpx` com timeout, retry exponencial limitado (3), falha em `error` estruturado; cache em `data/raw/` com `.meta.json` (etag, last_modified, sha256, bytes, baixado_em); trailer `99` conferido contra linhas lidas; largura 245; campos numéricos parseáveis. Validador devolve relatório, não levanta exceção no primeiro erro: coleta e sai com código.

**A2 — parser** (`F0.3`, `F0.7`)
Arquivos: `src/b3o/ingest/parse.py`, `tests/fixtures/cotahist_2026_sample.txt` (10 linhas reais, versionado, ≤ 8 KB), `tests/test_parse.py`.
Requisitos: `latin-1`, streaming linha a linha, **nunca** carregar o arquivo inteiro em memória; filtro de mercados 010/020/070/080/012/013; `ticker` com `strip()` (campo alinhado à direita); preços e strike em centavos `int64`; `FATCOT` em `l[210:217]`; estilo pelo 1º token de `l[27:39]` (`PETRE` = E, `PETR` = A, `null` + `style_unknown` quando não bater, com exceção de raiz de 5 letras tipo `BRAVA`); sufixos `/EJ`, `/AT` não mudam o estilo. Golden file conferido campo a campo contra o TXT bruto.
Teste obrigatório: nas 10 linhas, os valores batem com o arquivo; strike right-aligned coberto.

**B1 — store** (`F0.5`) — `src/b3o/store/lake.py`, `store/state.py`, testes.
Parquet particionado `year=YYYY`, escrita atômica (`tmp/` → `os.replace`), ano corrente reconstruído inteiro, `ingest_state` com contagem por `refdate` e sha por data. Idempotência provada: duas execuções → mesmo conteúdo (compare hashes das partições, não só "rodou").

**B2 — legs** (`F0.6`) — `src/b3o/derive/legs.py`, `derive/strike_events.py`, testes.
Uma linha por `(ticker, refdate)` com `leg_index`, `strike` do dia, `strike_adj_ind`, `distribution_id`. Fronteira = qualquer mudança nos três; divergência → `leg_ambiguity`. Classificação **só com mudanças contíguas** (a série negociou no pregão anterior) e **pela assinatura entre strikes do mesmo papel e data**: Δ em R$ igual em todos (±1 centavo) = provento; razão igual (±0,1%) e Δ variando = escala; os dois cabem → provento; nenhum → indeterminado. Ascendente = grupamento. **Proibido limiar de razão.** Testes com SBSP 29/04/2026 (×0,2), SIMH 18/02/2026 (×2), LOGG 06/2026 (R$ 2,86) e ABEVX131 (12,04 → 12,00 → 11,94).

**B3 — curva DI** (`F0.9`) — `src/b3o/ingest/rates.py` + teste. Taxas referenciais → fallback DI1 → fallback Selic flat; `rate_source` sempre gravado; interpolação em log-DF por dias úteis (a interpolação fica em `derive/conventions.py` no F1; aqui só se guarda `du`/`rate_annual`).

**B4 — calendário de resultados** (`F0.10`) — `src/b3o/ingest/earnings.py` + teste. Índices ITR/DFP + FCA da CVM; `actual` = data de entrega da 1ª versão; `estimated` = mesma data do ano anterior deslocada para o mesmo dia da semana, limitada pelo prazo regulatório; `kind` explícito; papel sem correspondência → `null` + `earnings_unmapped`. **Anote no `AGENTS.md` os nomes exatos dos campos** (a spec §6.14 deixa isso "a confirmar no F0").

**B5 — open interest** (`F0.8`) — `src/b3o/ingest/open_interest.py` + teste. Arquivo regulatório, 10 dias, join por `(refdate, ticker)`, nullable.

**C1 — liquidez, health e preparo** (`F0.11`, `F0.12`) — `src/b3o/derive/liquidity.py`, `query/service.py::health`, `cli.py`, testes.
Liquidez: por papel, dias com ≥ 5 strikes líquidos entre 20Δ e 80Δ (aprox. por moneyness — Δ vem no F1, então use moneyness e diga isso no relatório) e ≥ 2 vencimentos líquidos; `days_to_liquidate` quando há OI (sem OI → `null`). `health`: última sessão, idade em dias úteis, contagem por data, `rate_source`, `oi_unavailable`, `unexplained_gap`, último erro, pico de RSS do `prepare`.

### Aceitação F0 (copie do `PLAN.md`; nada aqui é opcional)

- `podman compose config` válido; build do zero < 5 min.
- `prepare`/`pytest` só por compose; nenhum passo exige Python/Node no host.
- Duas execuções de `prepare` → mesmo lake; segunda mais rápido (304).
- Contagem por `refdate` bate com `GROUP BY` independente feito direto no TXT.
- Trailer confere; nenhuma linha de largura errada passa.
- Legs conferidas à mão em 5 tickers com ajuste conhecido, incluindo SBSP (×0,2) e SIMH (×2) que **não** aparecem em `dividends_implied`, e LOGG (R$ 2,86) que aparece.
- Estilo bate com o medido: puts 100% europeias; calls ~66/34 por série.
- OI e DI presentes **ou** flag explícita.
- Relatório de liquidez define o universo (número de papéis) e entra no `AGENTS.md`.
- `core.earnings` cobre todo o universo líquido; `actual` de 2025 conferido em 3 papéis no RI da empresa; `estimated` de 2026 conferido contra a data que de fato saiu.
- `pytest` verde; `health` mostra sessão, idade, contagem, fontes, último erro.

**Auditoria obrigatória (sub-agente, sessão nova):** recontar as linhas por `refdate` **sem usar o projeto** (script descartável de 20 linhas ou PowerShell direto no TXT), escolher 3 tickers com ajuste e refazer a leitura do strike dia a dia à mão, e conferir 2 linhas do golden file byte a byte. Divergência = pacote reprovado.

### Entregável F0

`podman compose run --rm prepare` de ponta a ponta, `health` respondendo, `out/relatorio-liquidez.md` escrito, `AGENTS.md` atualizado, `PLAN.md` com F0 `[x]` (você marca, com o parecer em mãos).

---

## 7. F1 — derivadas

Objetivo: o mínimo para o sinal — forward, IV, gregas, ATM 30d, RV, VRP, z, skew. `model_version = 1` aqui.

**D1 — núcleo de precificação** (`F1.1`–`F1.4`) — `derive/conventions.py`, `forward.py`, `iv.py`, `greeks.py`.
`T` em dias úteis/252; `DF = exp(−r·T)`; `r` interpolado em log-DF; forward por paridade **só OTM**, preferindo pares europeus, strike de cruzamento + fallback OLS restrito a `|K−F₀|/F₀ ≤ 15%`; Black-76 com Brent em `[1e-4, 5]` vetorizado; gregas em unidade interpretável (`theta_brl`, `theta_pct`, `vega_brl` em R$/ponto por ação — o multiplicador vem do `FATCOT`).
Testes: Black-76 contra valores de referência (escreva os esperados à mão, não gerados pelo próprio código); inversão preço → IV → preço com erro < 1e-6; paridade C−P = DF(F−K) num caso sintético; gregas contra diferenças finitas.

**D2 — realizada e eventos** (`F1.6`) — `derive/realized.py`, `derive/corporate_events.py`.
Yang-Zhang 20/30/60 sobre OHLC, base 252; ajuste da série do subjacente pelo fator de `core.corporate_events` **antes** de qualquer RV; exclusão do gap de provento; `|r| > 40%` sem evento → `unexplained_gap` no health, sem ajustar nada.
Teste: série sintética com vol conhecida (recuperar ±0,5 vol point) e série com desdobramento 1:3 (sem ajuste, RV explode; com ajuste, volta ao valor certo).

**E1 — superfície e sinal** (`F1.5`, `F1.7`–`F1.9`) — `derive/atm.py`, `vrp.py`, `skew.py`, `dist.py`.
ATM por vencimento em variância total `w = σ²T` em `log(K/F)`; entre vencimentos, interpolação linear em `T` até `T = 30/252`; sem extrapolar além do último vencimento — `null` + `iv_extrapolated`. VRP = `iv_atm_30d − rv30`, `vrp_z` em 252 sessões (sem 252 de aquecimento → `null`). RR25/BFLY25 com mínimo de 5 strikes entre 20Δ e 80Δ; senão `null` + `sparse_chain`. Expected move e dividendo implícito conforme §6.10.

**E2 — validadores** (`F1.10`) — `derive/checks.py`. Butterfly ≥ −tol, calendário e monotonicidade; reprovado → `inconsistent`, fora do smile e do mispricing.

**F1\* — CLI, backfill e validação externa** (`F1` entregável, `F1.11`, `F1.12`)
`b3o metrics --date` e `--backfill` (2026 primeiro, depois 2024/2025); `core.metrics_underlying` e `core.metrics_series` gravados. Validação: (a) paridade entre call E e put E no mesmo strike — mediana de `|ΔIV|` ≤ 0,5 e P90 ≤ 1,5 vol point em PETR4, VALE3, BBAS3, ITUB4, B3SA3; (b) PETR4 contra o sandbox gratuito da brapi (sem token), convertendo dias corridos/365 → dias úteis/252, ±2 vol points; diferença sistemática registrada no `AGENTS.md`. Se a brapi estiver fora: P-J, registre e siga.

### Aceitação F1

As oito primeiras linhas da aceitação F1 do `PLAN.md`, incluindo: `r ± 100 bp` → `max|ΔIV| < 0,25`; ATM 30d sem salto > 2 vol points em rolagem; RR25 de PETR4 negativo em ≥ 80% dos dias; nenhuma série `inconsistent` no smile.

**Revisão obrigatória (sub-agente quant, sessão nova):** reimplemente a inversão de IV por bisect **independente** e compare em 20 séries; refaça o forward de uma data por OTM-only e por ITM-only; verifique que o z-score da data usa só dados até a data (leia o código e cite a linha); confira uma grega contra payoff numérico. Reprovou → volta ao implementador.

---

## 8. F1b — backtest (o portão)

**G1 — backtest** (`F1b.1`–`F1b.3`) — `src/b3o/derive/backtest.py`, `cli.py`, `out/backtest.md`.
Para cada `(papel, data)` com classe ≠ `none`: `rv_fwd20` = Yang-Zhang das 20 sessões seguintes (ajustada por escala); `edge = iv_atm_30d(data) − rv_fwd20`; acerto por classe (`premium_rich`: `edge > 0`; `premium_cheap`: `edge < 0`); relatório com `n`, taxa, mediana e P10 de `edge`, **sempre** comparado com a classe `none` (base) e por papel. Recorte extra: janelas que contêm resultado (`core.earnings`).

**G2 — teste de look-ahead** (`F1b` critério) — teste que embaralha o futuro (permuta `rv_fwd20` entre datas) e **exige** mudança material do resultado; se o resultado não mudar, o teste falha — ele está provando que a métrica não é constante, não que o código está certo.

### Regra do portão (pré-registrada — não otimize depois de ver o resultado)

O sinal **passa** se, no período completo (2024–2026, após as 252 sessões de aquecimento):

- `premium_rich`: `n ≥ 50`, taxa de acerto ≥ 55%, mediana de `edge ≥ +1,0` vol point, e taxa ≥ taxa da classe `none` + 5 p.p.;
- simétrico para `premium_cheap` (`edge < 0`, mediana ≤ −1,0, mesma diferença).

- **Passou** → registre em `docs/decisoes.md` e no `AGENTS.md`, marque F1b `[x]` e siga para F2 e F3.
- **Não passou** → **pare** o desenvolvimento de produto. Escreva `out/backtest.md` com o resultado, a leitura em 10 linhas e a decisão proposta (segue / muda limiar com justificativa *a priori* / troca de sinal) em `docs/decisoes.md` como pendente do dono. Marque F1b `[x]` somente nos critérios que passaram; o de decisão fica aberto.

Não é permitido: mudar limiar, mudar janela (`rv_fwd20` → outra), escolher subconjunto de papéis, ou rodar até achar um recorte que passe. Se o resultado for fraco, é resultado.

---

## 9. Ondas H (só se o portão passar)

**F2 — funil.** `derive/funnel.py`: liquidez de papel/série, `days_to_liquidate`, classes de sinal (§6.9), ranking por **componentes** (sem score opaco), incluindo papéis sem sinal e excluídos com motivo; `b3o screen --date` + `out/screen-AAAA-MM-DD.md` com próximo resultado e taxa histórica da classe. Aceitação: todo papel do universo aparece; frequência de classe ≠ `none` consistente com o F1b; cada linha com IV ATM, rank, VRP, z, RR, exp. move e próximo resultado; uma semana de briefings revisada sem absurdo.

**F3 — MCP.** FastAPI com MCP Streamable HTTP em `/mcp` (`127.0.0.1:8080`), tools `health`, `screen`, `underlying`, `chain`, `series`, `events`, `signal_backtest` sobre `QueryService`; descrição "use quando / não use para"; `max_rows` 20 padrão/60 teto; teto de 200 KB com `truncated`; erro `{error:{code,message,hint}}`; teste de contrato MCP × HTTP com o mesmo número. Aceitação: nenhuma tool devolve > 60 linhas; o agente responde "vale vender put em PETR4?" citando campos que existem.

---

## 10. Decisões no caminho

- Dúvida coberta por `docs/decisoes.md` (tabela P-A…P-T ou ADR) → **use o default**, cite o código na mensagem de commit.
- Dúvida não coberta → aplique o desempate (spec > PLAN > menor escopo > mais reversível), decida, registre um ADR curto e siga. **Não pergunte.**
- Só há quatro motivos para parar e perguntar (§"Quando parar e perguntar" de `docs/decisoes.md`). Se nenhum deles ocorrer, a sessão vai até o fim.
- Se você parar por um desses motivos: escreva a pergunta com a decisão default que adotaria e o custo de reverter, e siga com o default se ele for reversível. Só trave o que for irreversível.

---

## 11. Commits e relatório

- Um commit por pacote: `feat(f0/parse): ...`, `feat(f1/iv): ...`, `fix(...)`, `docs(...)`, `test(...)`.
- Ao fim de cada onda: atualize `PLAN.md` (checkbox marcado só com parecer), `AGENTS.md` (armadilhas) e `docs/decisoes.md` (ADRs) **no mesmo commit** da onda.
- No fim da sessão, escreva `out/relatorio-sessao.md`: o que fechou por fase, critérios com veredito e evidência, o que ficou aberto e por quê, ADRs criados, e a tabela do portão F1b com números reais.
- Mensagem final ao dono, curta: (1) o que está provado; (2) o que não está e por quê; (3) as decisões que ele precisa ratificar (lista de ADRs provisórias); (4) o próximo passo óbvio.

---

## 12. Checklist de armadilhas (não redescubra — está tudo medido no `AGENTS.md`)

- `isin` é do ativo-objeto → chave da série é `(ticker, maturity)`.
- Strike muda no meio da vida (23,5% dos tickers em 2026); strike é **por data**; nenhuma métrica atravessa o ajuste.
- 70% das mudanças aparecem depois de lacuna → só mudança **contígua** data e classifica.
- Arquivo anual é ordenado **por instrumento**, não por data → filtrar por data lendo tudo.
- Ticker alinhado à direita → `strip()` sempre.
- Preços com 2 decimais implícitos; centavos `int64` (volume estoura `int32`).
- `FATCOT` não é sempre 1 (100, 1.000, 1.000.000).
- Estilo no nome resumido (`l[27:39]`), não em sufixo; puts todas europeias; calls 66/34.
- MV do Podman: 8 vCPU / 3,7 GiB → streaming obrigatório, medir RSS.
- `uv.lock` versionado e `--frozen`; dev deps só no estágio `dev` (teste no serviço `test`, não no `app`).

---

## 13. Comece agora

1. Leia os quatro documentos do §2.
2. Rode `git status` e confirme que não há código (só docs).
3. Execute a Onda 0 sozinho, até `podman compose run --rm test pytest` passar.
4. Só então abra a Onda A com dois implementadores em paralelo.

Não peça permissão para nada do que está neste documento. Ele já foi autorizado.
