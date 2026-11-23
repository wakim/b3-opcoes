# Prompt — F1b: backtest das classes de sinal (o portão)

> Cole o bloco abaixo numa sessão nova, com o diretório de trabalho em `C:\Users\wakim\Projects\b3-opcoes`.
> **Só rode depois que a revisão do F1 (`docs/prompts/validacao-f1.md`) tiver parecer `APROVADO` ou `APROVADO COM RESSALVA`** e o recálculo inteiro em `model_version=2` estiver feito. Esta versão substitui `docs/prompts/onda-g.md`, que tem caminhos antigos.

---

Você é o **implementador do F1b** do projeto Opções B3 (`C:\Users\wakim\Projects\b3-opcoes`; Python 3.15; tudo por `podman compose`, rodando da raiz). Este pacote responde à pergunta que libera ou trava o resto do produto: **as classes de sinal `premium_rich`/`premium_cheap` têm valor?** Você mede e relata. **A decisão de seguir ou parar não é sua.** O orquestrador aplica os critérios pré-registrados e o dono ratifica.

## Leia antes (nesta ordem)
1. `AGENTS.md` inteiro. Em especial: fator de escala aplicado na barra **anterior** ao evento; data ex do B2 = data do evento; `indeterminate` não é ausência de evento; Yang-Zhang; **data-armadilha 2026-10-26** (93 papéis com overnight de ~−50% sem ajuste de strike → RV inflada e `premium_cheap` em massa por 30–60 sessões; não se ajusta, se relata); merge streaming e spill em `/tmp`; `read_parquet` com parâmetros **nomeados**. **Não há suíte de testes** (ADR-019): não crie `tests/`.
2. `PLAN.md`, seção **F1b** (tarefas e aceitação).
3. `docs/contratos/onda-fg.md` **§2 inteiro**: critérios do portão **pré-registrados**. Leia também §4.3 (schema de `metrics_underlying`; IV e vols em **% a.a.**, VRP em pontos de vol), §4.2 (regra de SQL: `duckdb` proibido em `metrics/`, leitura via `QueryService`, escrita via `store/`) e §4.5 passos 10–12 (como o pipeline já calcula RV, z e classe).
4. `docs/spec.md` §6.9 (classes) e §6.13 (backtest).
5. `docs/decisoes.md`: ADR-010 (folga de 1 sessão no earnings), ADR-014, ADR-017/018, ADR-019, ADR-020.
6. O relatório da revisão do F1 em `docs/relatorios/2026-11-23-revisao-f1.md`, se existir. Leve em conta qualquer ressalva sobre `iv_atm_30d`, `rv30` ou `vrp_z_252`.

## Pré-condições (verifique e cole a saída; se alguma falhar, pare e relate)
- `metrics_underlying`: todas as linhas com `model_version = 2` e 722 `refdate` distintas (2024-01-02 → 2026-11-19), checado por SQL direto nos parquets.
- `git status` limpo e `podman compose run --rm test ruff check src` limpo.
- Quantos `(papel, data)` têm `vrp_z_252` preenchido e a partir de que data. O aquecimento é de 252 sessões, então o período efetivo começa por volta de jan/2025. Escreva isso no topo do relatório; não é defeito.

## Seus arquivos (não edite mais nada)
- `backend/src/b3o/derive/backtest.py`: agregação **pura**. Recebe observações `(papel, data, classe, iv_atm_30d, rv_fwd20, earnings_na_janela)` e devolve o relatório por classe, por papel e por recorte. Expõe também `signal_backtest(signal_class, ticker=None)` sobre essas observações (o F2/F3 consome depois).
- `backend/src/b3o/metrics/backtest.py`: carrega `metrics_underlying`, as barras OHLC do papel, `strike_events` e earnings **via `QueryService`** e calcula `rv_fwd20`. **Reuse o Yang-Zhang e o ajuste de escala de `derive/realized.py`, sem reimplementar.**
- `backend/src/b3o/query/service.py`: **só** métodos novos de leitura, se faltarem.
- `backend/src/b3o/cli.py`: **só** o comando `backtest`.
- Saída `data/out/backtest.md` (fora do git).

## O que calcular (período 2024-01-02 a 2026-11-19)
1. `classe` = `signal_class` gravado em `metrics_underlying`, sem recalcular.
2. `rv_fwd20` = Yang-Zhang das **20 sessões seguintes** (`data+1 … data+20` no calendário do lake), em % a.a. base 252. Usa o mesmo mercado (`010`/`020`) da linha de métrica, aplica o fator de escala acumulado nas barras anteriores a cada evento `scale` e exclui o gap de provento (`ex_div_dates`). Sem 20 sessões futuras no lake, o valor é `None` e a observação sai (conte quantas).
3. `edge = iv_atm_30d(data) − rv_fwd20`, em pontos de vol. Só entra quem tem os dois valores e `vrp_z_252` preenchido.
4. Acerto:
   - `premium_rich` → `edge > 0`;
   - `premium_cheap` → `edge < 0`;
   - `none` → base: relate a taxa de `edge > 0` e a de `edge < 0`, nas mesmas datas.
   - `skew_anomaly` e `event_pressure`: relate `n` e a distribuição de `edge`, sem veredito.
5. `earnings_na_janela`: resultado (`actual`, deslocado 1 sessão pela folga do ADR-010) dentro de `data+1 … data+20`. Vira recorte separado.

## Relatório (`data/out/backtest.md`)
- Topo: período, nº de sessões, nº de `(papel, data)` com z, nº de observações descartadas por motivo (sem IV, sem RV futura, sem z), `model_version`, commit.
- Por classe: `n`, taxa de acerto, mediana, P10 e P90 de `edge`, com a classe `none` ao lado.
- Por papel (pelo menos os 15 com grade líquida do `AGENTS.md`): `n`, acerto e mediana.
- Recorte com vs. sem resultado na janela.
- **Recorte informativo da data-armadilha:** o mesmo número de `premium_cheap` com e sem as observações cuja janela de RV (passada ou futura) contém 2026-10-26. Ele é **só informativo**: o veredito usa o período completo, como está pré-registrado.
- Tabela do portão com os critérios e o veredito linha a linha (`passa`/`não passa`). Você preenche, mas não interpreta como decisão.

## Critérios pré-registrados (NÃO mexer)
- `premium_rich`: `n ≥ 50`, acerto ≥ 55%, mediana de `edge ≥ +1,0` vol point, acerto ≥ acerto(`none`) + 5 p.p.
- `premium_cheap`: simétrico (`edge < 0`, mediana ≤ −1,0, mesma margem).
- Classe com `n < 50` **não passa**; relate o `n`.

**PROIBIDO:** mudar limiar, janela (`rv_fwd20`), recorte ou subconjunto de papéis; rodar até achar um recorte que passe; mexer em `model_version`, no pipeline de métricas ou nos validadores para "consertar" o resultado. Se achar defeito no F1 durante o backtest, **pare e relate** com reprodução mínima, sem corrigir.

## Prova de que o resultado pode estar errado (scripts descartáveis em `data/tmp/`, não testes)
1. `data/tmp/lookahead_perm.py`: embaralhe `rv_fwd20` entre datas (seed fixa), recalcule e **cole antes/depois** (acerto e mediana por classe). Se nada mudar, a prova falha. Relate.
2. `data/tmp/lookahead_z.py`: mostre que `vrp_z_252` de uma data não muda quando se acrescenta histórico posterior. Recalcule o z de 3 datas por fora (das `vrp` gravadas ≤ data) e compare com o gravado. Cite a linha do pipeline que fatia o histórico.
3. Confira à mão um `rv_fwd20` da PETR4 (uma data qualquer de 2025) por um caminho independente, com o seu próprio Yang-Zhang sobre as barras, e compare com o valor usado no backtest.

## CLI
`podman compose run --rm test b3o backtest [--from 2024-01-02] [--to 2026-11-19] [--config …]`. Imprime numa tela: `n` por classe, acerto, mediana e veredito de cada critério. Grava `data/out/backtest.md`.

## Definição de pronto
1. `b3o backtest` roda no histórico real; saída colada na resposta.
2. As três provas acima coladas (antes/depois, z recalculado, RV à mão).
3. `ruff check src` limpo; `model_version` inalterado.
4. Commit `feat(f1b/backtest): …` e push para `origin master`, conforme a regra de commit e push por pacote do `AGENTS.md`. O relatório **não** entra no commit (`data/` é ignorado).
5. Na resposta final:
   - a tabela do portão;
   - uma leitura do resultado em até 10 linhas;
   - se algum critério **não passou**, uma proposta de ADR **pendente do dono** em `docs/decisoes.md` (seguir / mudar limiar com justificativa *a priori* e período fora da amostra / trocar o sinal). Não marque `[x]` no `PLAN.md` nem registre "o sinal passou" no `AGENTS.md`; isso fica para depois da ratificação do dono.
