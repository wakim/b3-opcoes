# Prompt — Onda G (backtest das classes e o portão)

Você é o IMPLEMENTADOR do pacote **G** do projeto `C:\Users\wakim\Projects\b3-opcoes` (opções da B3; Python 3.15; container por `podman compose`). Este pacote responde à única pergunta que autoriza (ou mata) o resto do produto: **as classes de sinal têm valor?**

**Diretiva do dono (22/11/2026): o projeto NÃO tem testes** (ADR-019). Não crie `tests/`. A prova do look-ahead é um **script descartável** em `data/tmp/` cujo antes/depois você cola na resposta.

LEIA:
1. `docs/contratos/onda-fg.md` **§2 inteiro** — critérios do portão **pré-registrados**. Não mude limiar, janela, recorte nem subconjunto. Não rode até passar.
2. `docs/spec.md` §6.13 (backtest) e §6.9 (classes).
3. `AGENTS.md`: escala antes da RV, data ex do B2 = data do evento, `close` para IV, folga de 1 sessão do ADR-010 na janela de earnings.
4. `docs/decisoes.md`: ADR-010 (folga), ADR-014 (tabelas), ADR-017/018 (IV e paridade), ADR-019 (sem testes).

## Seus arquivos
`src/b3o/derive/backtest.py` (agregação pura: dada uma lista de observações `(papel, data, classe, iv_atm_30d, rv_fwd20, earnings_na_janela)`, devolve o relatório por classe/papel), `src/b3o/metrics/backtest.py` (carrega o lake + `metrics_underlying`, calcula `rv_fwd20`), `src/b3o/cli.py` (**só** o comando `backtest`), `out/backtest.md` (saída). Não edite mais nada.

## O que calcular

Para cada `(papel, data)` do período **2024-01-02 a 2026-11-19**:
1. `classe` = `signal_class` gravado em `metrics_underlying` (já calculado pelo pipeline, limiares fixos §6.9).
2. `rv_fwd20` = Yang-Zhang das **20 sessões seguintes** ao dia `data` (barras `data+1 … data+20` no calendário do lake), com `adj_factor` de `corporate_events` (escala) e `ex_div_dates`; sem 20 sessões futuras → `None` (fora).
3. `edge = iv_atm_30d(data) − rv_fwd20` (**pontos de vol**). Só entra quem tem `iv_atm_30d` e `rv_fwd20`.
4. Acerto: `premium_rich` → `edge > 0`; `premium_cheap` → `edge < 0`; `none` → base (relate a taxa de acerto de `edge > 0` **e** `< 0`).
5. Aquecimento: só `(papel, data)` com `vrp_z_252` preenchido (o z exige 252 sessões).
6. `earnings_na_janela`: existe `earnings_rows` do papel com `date` (deslocada em 1 sessão, ADR-010) entre `data` e `data+20` sessões; recorte separado.

## Relatório (`out/backtest.md`)
- Por classe: `n`, taxa de acerto, mediana, P10 e P90 de `edge`, e a **classe `none` ao lado** (mesmas datas).
- Por papel: `n`, acerto, mediana.
- Recorte "janela com resultado" vs sem.
- Tabela do portão com os quatro critérios e veredito por linha (você **não** decide; só preenche).
- Deixe escrito no topo: período, nº de sessões, quantos `(papel, data)` com z, e o `model_version`.

## Critérios pré-registrados (não mexa)
`premium_rich`: `n ≥ 50`, acerto ≥ 55%, mediana de `edge` ≥ +1,0 vol point, acerto ≥ acerto(`none`) + 5 p.p.
`premium_cheap`: simétrico (`edge < 0`, mediana ≤ −1,0, mesma margem).
Se `n` for menor que 50 numa classe, o critério **não passa** — relate o `n`.

## Prova de look-ahead (script, não teste)
Em `data/tmp/lookahead.py`: embaralhe `rv_fwd20` entre as datas (seed fixa), recalcule o relatório e **cole antes/depois** (taxa de acerto e mediana por classe). Se o resultado **não mudar**, o pipeline não está consumindo o futuro e o teste falha — relate. Prove também, com uma série sintética de dois dias, que `vrp_z` da data t usa só dados ≤ t (cite a linha do código do pipeline que fatia).

## CLI
`b3o backtest [--from 2024-01-02] [--to 2026-11-19] [--config …]` → roda e imprime o resumo de uma tela (n por classe, acerto, mediana, veredito de cada critério) e grava `out/backtest.md` (P-T).

## Definição de pronto
1. `b3o backtest` roda no histórico real e gera o relatório; saídas coladas na resposta.
2. O script de permutação muda o resultado (antes/depois colados).
3. `ruff check src` limpo.
4. Nenhuma decisão "seguir/parar" é sua: o orquestrador aplica os critérios e o dono ratifica.
