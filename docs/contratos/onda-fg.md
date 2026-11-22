# Contrato — Onda F (F1.11 CLI/backfill/validação) · Onda G (F1b portão)

**Dono do contrato:** orquestrador · **Data:** 2026-11-22 · **Válido para:** F1*, G1, G2.

**Os limiares do portão (§8 do termo de sessão) são PRÉ-REGISTRADOS aqui.**
Mudá-los depois de ver o resultado é proibido (P-L). Não existe "rodar até
passar": recorte, janela, subconjunto e limiar são estes e nenhum outro.

## 0. Donos de arquivo

| Pacote | Dono | Arquivos |
|---|---|---|
| F1* | impl. F | `src/b3o/metrics/` (pipeline por data), `src/b3o/cli.py` (comandos `metrics`/`--backfill`), `tests/test_metrics_*.py` — detalhado no fim, **após as ondas D/E pousarem** (este contrato fixa a semântica; a lista de arquivos fecha na abertura da onda) |
| G1 | impl. G | `src/b3o/derive/backtest.py`, `src/b3o/cli.py` (comando `backtest`), `out/backtest.md` |
| G2 | impl. G (mesmo dono, mesmo pacote) | `tests/test_backtest_lookahead.py` |

## 1. Onda F — métricas por (papel, data) (`core.metrics_*`)

- `b3o metrics --date AAAA-MM-DD`: calcula para o universo líquido do dia:
  - `metrics_series`: por série — `iv` (Black-76, % a.a.), `delta`, `gamma`,
    `theta_brl`, `theta_pct`, `vega_brl`, `rho`, `iv_pct_252` (percentil),
    `flags[]` (`wide_spread`, `no_trade_5d`, `bfly_negative`, `sparse_chain`,
    `one_sided`, `iv_extrapolated`, `scale_event`, `american_call_ex_div`,
    `inconsistent`, `style_unknown`).
  - `metrics_underlying`: `iv_atm_30d`, `iv_rank_252`, `iv_pct_252`, `rv20/30/60`,
    `vrp`, `vrp_z_252`, `rr25`, `bfly25`, `ts_slope_30_90`, `exp_move_1s_30d`,
    `implied_div_yield_30d`, `days_to_liquidate`, `forward_30d`, `rate_source`,
    `model_version`.
  - Grava em `data/metrics_underlying/`, `data/metrics_series/`
    (partição `year=YYYY`, ADR-009; `model_version=1` sempre gravado).
- `b3o metrics --backfill`: recalcula o histórico INTEIRO disponível
  (2026 primeiro; 2024/2025 depois de baixados — ADR-006); **z/percentis usam
  só dados ≤ data** (o teste de look-ahead do G2 caça o contrário).
- **Ingestão de 2024/2025**: `prepare` aceita `--year` (fetch dos anos
  fechados, imutáveis — sem reparse diário); o `health` mostra as partições.
- **Validação externa (F1.12, critérios do PLAN)**: (a) paridade call E × put E
  mesmo strike — mediana |ΔIV| ≤ 0,5, P90 ≤ 1,5 vol point em PETR4/VALE3/BBAS3/
  ITUB4/B3SA3; (b) PETR4 vs brapi sandbox (sem token; convenção dias corridos/365
  → converter para a variância total em T útil/252; ±2 vol points; diferença
  sistemática registrada no AGENTS). brapi fora → P-J (registra e segue).
- `american_call_ex_div`: call americana ITM com ex-dividendo antes do
  vencimento → IV contaminada → **fora do smile e do forward** (spec §6.2).

## 2. Onda G — o portão (pré-registrado; NÃO OTIMIZAR)

### G1 — backtest (§6.13)

```
para cada (papel, data) do histórico com classe != none:
    rv_fwd20 = Yang-Zhang das 20 sessões SEGUINTES (ajustada por corporate_events)
    edge = iv_atm_30d(data) − rv_fwd20        # pontos de vol
    acerto: premium_rich → edge > 0; premium_cheap → edge < 0
base de comparação: classe none (mesmas datas)
recortes: por classe, por papel, e "janela contém resultado" (earnings)
```

- Classes (§6.9, limiares FIXOS): `premium_rich` = `vrp_z ≥ +1,5` E `vrp ≥ +4`;
  `premium_cheap` = `vrp_z ≤ −1,5` E `vrp ≤ −4`; `skew_anomaly` =
  `|rr25 − mediana252| > 2σ252` com grade ≥5 strikes; `event_pressure` =
  `iv_atm(front) > iv_atm(back) + 2`; senão `none`.
- **Aquecimento**: só (papel, data) com ≥252 sessões de histórico de
  `vrp`/`rr25` antes da data.
- **Período**: 2024–2026 completos (após baixar os anos; ADR-006) — se um
  ano não existir na fonte, o relatório diz qual e por quê.
- **Janela com resultado**: earnings DENTRO das 20 sessões seguintes —
  com a **folga de 1 sessão do ADR-010** (DT_RECEB vale como evento do
  pregão SEGUINTE — nunca usar o resultado no dia em que "saiu" como se
  o pregão já soubesse).
- Relatório `out/backtest.md` (P-T): `n`, taxa de acerto, mediana e P10 de
  `edge`, **sempre** com a classe `none` ao lado (base), por classe e por
  papel. Nenhuma seleção de subconjunto além disso.
- `signal_backtest(signal_class, ticker)` (F2/MCP consome depois; aqui só a
  função pura sobre as métricas gravadas).

### Critérios do portão (pré-registrados — §8 do termo)

O sinal PASSA se, no período completo (2024–2026, após aquecimento):
- `premium_rich`: `n ≥ 50`, taxa ≥ 55%, mediana de `edge ≥ +1,0` vol point,
  e taxa ≥ taxa do `none` + 5 p.p.;
- `premium_cheap` simétrico: `edge < 0` (acerto), mediana ≤ −1,0, mesma
  diferença contra `none`.

**Passou** → registrar em decisoes + AGENTS, marcar F1b, seguir para F2/F3.
**Não passou** → PARAR o desenvolvimento de produto; `out/backtest.md` com
o resultado + leitura em 10 linhas + decisão proposta (segue / muda limiar com
justificativa a priori / troca de sinal) em decisoes como pendente do dono;
marcar só os critérios que passaram.

PROIBIDO na G: mudar limiar; mudar janela (`rv_fwd20` → outra); escolher
subconjunto de papéis; rodar até achar recorte que passe; recalcular
`model_version` para "consertar" o resultado.

### G2 — teste de look-ahead (o teste que PROVA que o resultado pode estar errado)

- Teste que **permuta `rv_fwd20` entre datas** (embaralha o futuro) e **exige
  mudança material** no relatório (taxa de acerto, mediana de edge): se o
  resultado permute == original, o teste FALHA — ele está provando que a
  métrica não é constante e que o pipeline consome o futuro de verdade.
- E o complementar: **z da data usa só dados até a data** — construa uma
  série sintética em que vazar 1 dia muda o z e prove que o código do F não
  vaza (cite a linha que fatia o histórico).
- O teste roda no `pytest` normal (determinístico, seed fixa).

## 3. Definição de pronto

1. pytest verde + ruff limpos.
2. G1 roda no histórico real e gera `out/backtest.md` com os recortes
   registrados — saídas coladas na resposta (n por classe, taxas, medianas).
3. G2 verde com a permutação MUDANDO o resultado (cole antes/depois).
4. Commit `feat(f1b/backtest): …` + o relatório NÃO entra no commit (out/ é
   ignorado; o orquestrador arquiva o texto na mensagem final).
5. Nenhuma decisão de "seguir/parar" é sua: o orquestrador aplica os critérios
   pré-registrados e o dono ratifica.
