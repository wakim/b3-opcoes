# Prompt — Validação independente do F1b (o portão) e do estado do projeto

> Cole o bloco abaixo numa sessão nova, com o diretório de trabalho em `C:\Users\wakim\Projects\b3-opcoes`.
> Você é o **revisor independente**: não escreveu o código do F1b e **não corrige nada** — mede, reproduz por fora e emite parecer. A decisão de seguir/parar no portão continua sendo do dono, não sua.

---

Você é o **revisor independente do F1b** do projeto Opções B3 (`C:\Users\wakim\Projects\b3-opcoes`; Python 3.15; tudo por `podman compose`, rodando da raiz). O pacote F1b (commit `73eeca0`, já em `origin/main`) implementou o backtest das classes de sinal e concluiu que **ambas as classes `premium_rich`/`premium_cheap` NÃO PASSAM** nos critérios pré-registrados. Sua tarefa: **verificar que esse veredito é confiável** — que o cálculo está certo, que não há look-ahead, que os critérios não foram mexidos e que o estado anterior (F0/F1) está intacto. Você mede e relata; **não corrige, não otimiza, não decide o portão**.

## Leia antes (nesta ordem)
1. `AGENTS.md` inteiro. Em especial: fator de escala na barra **anterior** ao evento; data ex do B2 = data do evento; `indeterminate` não é ausência; Yang-Zhang; **data-armadilha 2026-10-26** (contagem corrigida 45/97, não 93); `read_parquet` com parâmetros **nomeados** quando há duas tabelas; **não há suíte de testes** (ADR-019) — não crie `tests/`.
2. `docs/prompts/f1b-portao.md` (o que o implementador deveria ter feito) e `docs/contratos/onda-fg.md` **§2** (critérios do portão **pré-registrados**) e §4.2/§4.3 (regra de SQL e schema/units).
3. `docs/spec.md` §6.9 (classes) e §6.13 (backtest).
4. `docs/decisoes.md`: ADR-010 (folga de 1 sessão no earnings), ADR-017/018/019/020/021 e os novos **ADR-022** (convenções do `rv_fwd20`) e **ADR-023** (portão NÃO PASSA, pendente do dono).
5. O código do pacote: `backend/src/b3o/derive/backtest.py`, `backend/src/b3o/metrics/backtest.py`, os métodos novos de `backend/src/b3o/query/service.py` (`metrics_underlying_rows`, `spot_bars_in_market`) e o comando `backtest` em `backend/src/b3o/cli.py`.
6. O relatório gerado em `data/out/backtest.md` e o parecer anterior do F1 em `docs/relatorios/2026-11-23-revisao-f1.md` (para o estado de F0/F1).

## Pré-condições (verifique e cole a saída; se alguma falhar, pare e relate)
- `git log --oneline -3` mostra `73eeca0 feat(f1b/backtest)…` no topo; `git status` limpo; `git rev-parse HEAD` == `origin/main`.
- `podman compose run --rm test ruff check src` limpo.
- `metrics_underlying`: todas as linhas `model_version = 2`, 722 `refdate` distintas (2024-01-02→2026-11-19), 116.832 linhas — SQL direto nos parquets. Confirme que o F1b **não** recalculou métricas (o sha das partições de 2026 deve casar com o do parecer do F1: `metrics_underlying` `4c40a8b0…`, `metrics_series` `0a4d2c9d…`).
- `backend/config.yaml` com `model_version: 2` (inalterado).

## Seus arquivos (NÃO edite nada do código; só escreva o parecer e scripts descartáveis)
- Scripts de conferência em `data/tmp/validacao-f1b/` (fora do git). **Não importe `b3o.derive.*` nas conferências manuais** — reimplemente por fora (Yang-Zhang, z, agregação). Pode importar `b3o.query`/`b3o.metrics.backtest` só para comparar com o valor do pipeline, nunca como fonte da verdade manual.
- Parecer final em `docs/relatorios/2026-11-23-validacao-f1b.md`.
- **Proibido:** editar código, `PLAN.md`, `AGENTS.md`, `config.yaml`, `docs/decisoes.md` (salvo apontar defeito no parecer); mudar limiar/janela/recorte; marcar `[x]` em qualquer coisa; registrar "o sinal passou/ não passou" como decisão. Se achar defeito, **pare e relate** com reprodução mínima, sem corrigir.

## O que verificar (cada item com número e reprodução)

### A. O veredito do portão se reproduz por fora
1. **Contagens e classes** por SQL direto: com `vrp_z_252` preenchido, `signal_class` deve dar `none` 4526, `event_pressure` 1142, `premium_cheap` 844, `premium_rich` 650, `skew_anomaly` 31 (antes do descarte por RV futura). Depois do descarte (sem 20 sessões futuras = 420), as observações válidas somam **6773** e as classes viram rich 617 / cheap 498 / skew 29 / event 1117 / none 4512. Reproduza a agregação **por fora** (seu próprio group-by + mediana/percentil) e compare `n`, acerto e mediana por classe com o relatório. Tolerância: acerto ±0,1 p.p., mediana ±0,01.
2. **Critérios pré-registrados intactos:** confira no código que `GATE_N_MIN=50`, `GATE_HIT_MIN_PCT=55.0`, `GATE_MEDIAN_MIN=1.0`, `GATE_MARGIN_PP=5.0` e que a margem do rich usa `none.edge_pos_pct` e a do cheap usa `none.edge_neg_pct`. Nenhum limiar/janela/recorte/subconjunto foi alterado em relação ao contrato §2.
3. **Percentil/mediana:** confira à mão P10/P90 (interpolação linear) e mediana de uma classe em uma amostra pequena; bata com o relatório.

### B. O `rv_fwd20` está certo (sem look-ahead, sem off-by-one)
4. **Janela correta:** `rv_fwd20` usa `window=21` barras (base `data` + 20 futuras) → **20 retornos** sobre `data+1 … data+20`. Confirme que **não** é `data … data+19` (off-by-one) nem 19 retornos. Reproduza à mão (Yang-Zhang próprio) em **3 datas de papéis diferentes**, incluindo uma **com provento na janela** (para verificar a exclusão/pullback) e uma perto de um **evento de escala** (ex.: SBSP3 após 29/04/2026, ou SIMH3 após 18/02/2026) para verificar o fator na barra anterior. Compare com o valor do pipeline (tolerância 1e-6).
5. **Mercado:** confirme que usa `underlying_market` da linha (não mistura `010`/`020`). **Nota medida (23/11/2026): não há linha `020` em `metrics_underlying`** — todas as 116.832 são `010` (o universo com opção é todo lote padrão; os fracionários `…F` não têm opção). Verifique o mecanismo por chamada direta a `spot_bars_in_market(ticker, …, '020')` num ticker fracionário (ex.: `HYPE3F`): as barras vêm todas `020` e a consulta `'010'` para o mesmo ticker devolve vazio (não mistura).
6. **Descartes honestos:** os 420 "sem 20 sessões futuras" são exatamente as datas nas últimas 20 sessões do lake (**2026-10-22→2026-11-19**, conforme calendário; a sessão 2026-10-21 é a −21, tem 20 futuras e é mantida). Confirme por SQL; confira que não há descarte silencioso (sem barra na ponta = 0, RV falhou = 0).

### C. Look-ahead (o resultado pode estar errado?)
7. **Permutação do futuro:** rode `data/tmp/f1b/lookahead_perm.py` (ou reimplemente) com **outra seed** e confirme que o resultado muda materialmente (acerto/mediana por classe). Se nada mudar, o teste falha — relate.
8. **z não vaza futuro:** recalcula por fora o `vrp_z_252` de **3 datas de 3 papéis** (não só PETR4) das `vrp` gravadas ≤ data e compare com o gravado (diferença ~0). Mostre que fatiar a série completa (com futuro) até a data dá o mesmo z. Cite a linha do pipeline que fatia o histórico.
9. **Classe gravada, não recalculada:** confirme que o backtest lê `signal_class` do parquet e não reclassifica (nenhuma chamada a `derive/signal.signal_class` no caminho do backtest).

### D. Recortes
10. **Earnings (folga ADR-010):** confirme que só `kind='actual'` entra, deslocado para o **pregão seguinte** do calendário do lake, e que `estimated` fica de fora. Confira à mão em 1 papel (ex.: PETR4 ou BBAS3) que `earnings_na_janela` bate com a data real de divulgação + 1 sessão dentro de `data+1…data+20`. Reproduza os `n` do recorte com/sem resultado.
11. **Data-armadilha 2026-10-26 (informativo):** confirme as **33** observações `premium_cheap` com a trap na janela (acerto 100%, mediana −32,8) e **465** sem (acerto 46,2%, mediana +0,17). Verifique a definição da janela (rv30 passada + rv_fwd20 futura contém 2026-10-26) e que **o veredito usa o período completo** (a trap não entra no gate). Confirme que os edges muito negativos (−217, −215, −204…) vêm de split sem ajuste de strike (dado), não de bug — rastreie 1 até as barras cruas.

### E. Arquitetura e estado anterior
12. **Regra de SQL:** nenhum `duckdb`/`read_parquet` em `metrics/` ou `derive/`; leitura só via `QueryService`; escrita só via `store/`. Nenhum `round(` em `derive/`. Os métodos novos usam parâmetros nomeados quando há duas tabelas.
13. **F0/F1 intactos:** o F1b não tocou `derive/realized.py`, `derive/signal.py`, `metrics/pipeline.py` nem o `model_version`. Confirme por `git show 73eeca0 --stat` que só os 4 arquivos do pacote + `docs/decisoes.md` mudaram.
14. **Idempotência:** `b3o backtest` duas vezes produz o mesmo relatório (fora a linha de commit). O relatório não está no git (`data/` ignorado).
15. **Governança:** `PLAN.md` F1b **não** marcado `[x]`; `AGENTS.md` **não** registra "o sinal passou"; ADR-023 está **pendente do dono** com as 3 opções (parar / mudar limiar a priori + fora da amostra / trocar sinal). O por-papel inclui os 15 líquidos; explique por que AXIA3 aparece com 0 observações (sem aquecimento de z).

## Parecer (`docs/relatorios/2026-11-23-validacao-f1b.md`)
- **Veredito:** `APROVADO` / `APROVADO COM RESSALVA` / `REPROVADO`, com o motivo em 1–3 linhas.
- Tabela item a item (A–E) com o número medido, o esperado e `✔/✘/ressalva`.
- Defeitos por severidade (alta/média/baixa), cada um com **reprodução mínima** (comando + arquivo:linha), sem corrigir.
- Se o veredito do portão se reproduz e não há defeito de cálculo: declare explicitamente que o **NÃO PASSA é confiável** e que a decisão segue pendente do dono (ADR-023). Se achar defeito que mude o veredito, **pare**, relate e não proponha novo limiar.
- Lista dos comandos usados (reprodução).

## Definição de pronto
1. Todos os itens A–E medidos no dado real, com número colado.
2. As conferências à mão (Yang-Zhang, z, agregação) por caminho independente, sem importar `b3o.derive`.
3. `ruff check src` limpo; nenhum arquivo de código editado por você.
4. Parecer gravado em `docs/relatorios/`; **nenhum commit de código** (você não implementa). Se versionar o parecer, commit `docs(validacao): parecer independente do F1b` e push em `origin/main`.
5. Na resposta final: o veredito, a tabela-resumo, os defeitos (se houver) e a confirmação de que a decisão do portão permanece com o dono.
