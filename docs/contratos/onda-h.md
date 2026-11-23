# Contrato — Onda H (F2 descritivo)

**Dono do contrato:** orquestrador · **Data:** 2026-11-23 · **Válido para:** F2d.1–F2d.4.
**Base:** ADR-023 (Opção 1, parar no portão) + ADR-024 (F2 descritivo). O funil preditivo está
**suspenso**; nada aqui pode reintroduzir classe de sinal, taxa de acerto ou score.

## 0. Regra que não se quebra

A tela e o relatório são **descritivos**: mostram o que o dado diz na sessão. Proibido:
`signal_class` como coluna, taxa histórica de acerto, "premium caro/barato", nota, posição de
ranking, cor que sugira recomendação. O campo `signal_class` continua no parquet (compatibilidade
do lake) e **não é lido** por esta onda.

## 1. Universo

- **Papel do universo:** volume financeiro médio ≥ **R$ 5 M** no mercado `010`, na **janela das
  20 sessões** que terminam na data de referência (spec §6.9). **Unidade:** `volume` no lake está
  em **centavos** — o limiar é `5_000_000 * 100 = 500_000_000`; a média divide pelo **número de
  sessões da janela** (`len(window)`, até 20), como `derive/liquidity.build_liquidity` — sessão da
  janela em que o papel não negociou conta zero; janela curta (começo do lake) divide pelo que
  existe. Medido em 19/11/2026 (janela cheia de 20): **289 papéis** — idêntico ao
  `prepare_state.universe` (diferença **0**), o que serve de conferência independente.
- **Papel com métrica:** linha em `metrics_underlying` na data. Em 19/11: **163** dos 289.
  Há **9** papéis com métrica **fora** do universo (AMAR3, CMIG3, GFSA3, HBOR3, MTRE3, N1VO34,
  RAIZ55, ROMI3, TRIS3): não entram no `screen` (o recorte é o universo) e isso é dito no relatório.
- **Papel sem métrica:** aparece **excluído com motivo**, nunca sumindo em silêncio.
  Composição medida em 19/11: **163 ok + 124 `no_options` + 2 `no_trade` = 289**.
- Motivos de exclusão (ordem de precedência):
  `no_trade` (não negociou no `010`/`020` na data) → `no_options` (nenhuma linha `070`/`080` no
  dia) → `no_metrics` (sem linha em `metrics_underlying`) → **linha sem nenhum número legível**
  (`iv_atm_30d`, `rv20`, `rv30`, `rv60` e `vrp` todos nulos): aí sai o primeiro motivo de grade
  presente entre `no_rates`, `no_forward`, `sparse_chain`, `no_expiry` (senão `no_metrics`).
- **Flag de grade não exclui papel que tem número.** Medido em 19/11: **97 dos 172** papéis com
  métrica têm `no_forward`, 65 têm `sparse_chain`, 97 têm `no_expiry`; excluir por flag derrubaria
  os `ok` de 163 para ~10 e contradiria a composição medida. A flag explica a célula `—` na ficha
  e **não** tira o papel do recorte (contrato corrigido em 23/11, ver §7).

## 2. Interface

```python
# backend/src/b3o/query/service.py
screen(date: date | None = None, max_rows: int | None = None) -> list[ScreenRow]
```

`ScreenRow` (dataclass, `query/service.py`):

| Campo | Tipo | Unidade / regra |
|---|---|---|
| `underlying` | str | ticker do papel |
| `status` | str | `"ok"` ou `"excluded"` |
| `exclusion` | str \| None | motivo do §1 |
| `as_of_session` | date | última sessão do lake ≤ `date` (nunca o relógio, P-C) |
| `model_version` | int | vem do parquet |
| `spot_brl` | float \| None | fechamento do papel (conversão de centavos na serialização) |
| `iv_atm_30d` | float \| None | % a.a. |
| `iv_rank_252` | float \| None | 0–100 |
| `iv_pct_252` | float \| None | 0–100 |
| `rv20` / `rv30` / `rv60` | float \| None | % a.a. |
| `vrp` | float \| None | pontos de vol |
| `vrp_z_252` | float \| None | desvios |
| `rr25` / `bfly25` | float \| None | pontos de vol |
| `ts_slope_30_90` | float \| None | pontos de vol |
| `exp_move_1s_30d` | float \| None | fração |
| `implied_div_yield_30d` | float \| None | % a.a. |
| `forward_30d` | float \| None | R$ (serialização) |
| `days_to_liquidate` | float \| None | dias úteis |
| `rate_source` | str \| None | `b3_reference` \| `selic_flat` \| … |
| `n_series` | int | séries `070`/`080` do papel na data |
| `n_liquid_series` | int | `is_liquid = true` |
| `n_expiries` | int | vencimentos distintos com série na data |
| `earnings_next_date` | date \| None | próximo resultado **≥ as_of_session** |
| `earnings_next_kind` | str \| None | `actual` (confirmado) ou `estimated` (previsto) |
| `earnings_before_expiry` | bool | flag gravada em `metrics_underlying` |
| `flags` | list[str] | união das flags do papel, ordem alfabética |

Regras:
- `max_rows=None` → todas as linhas; `max_rows=N` → as **N primeiras em ordem alfabética**
  (a truncagem é declarada no retorno/relatório; nunca "os melhores").
- Ordenação sempre **alfabética por `underlying`**.
- `n_series`/`n_liquid_series`/`n_expiries` vêm de `metrics_series`; o vínculo
  `ticker → underlying` é resolvido **pelo ISIN** (nunca por prefixo de ticker — armadilha 1 do
  `AGENTS.md`), como o pipeline já faz.
- Nada de SQL fora de `store/` e `query/` (guarda de arquitetura).
- `date=None` → última sessão do lake; data sem sessão → erro `{code,message,hint}`-style no CLI
  (`EXIT 1`), nunca relatório vazio.

## 3. CLI

```
b3o screen --date 2026-11-19 [--max-rows N] [--config ...]
```

- Terminal (P-G, uma tela): cabeçalho com sessão, `model_version`, contagens do universo
  (`ok`, `excluded` por motivo), mediana de `iv_atm_30d` e de `vrp` **entre os `ok`**, e as
  primeiras `--max-rows` (default 20) linhas da tabela.
- Relatório `data/out/screen-AAAA-MM-DD.md` (P-T): **todas** as linhas, sempre:
  1. cabeçalho: sessão, `as_of`, `model_version`, fontes de taxa;
  2. tabela descritiva;
  3. ficha por papel com `ok` (bullets: spot, IV ATM, rank/percentil, RV20/30/60, VRP/z,
     RR25/BFLY25, slope 30–90, exp. move, dividendo implícito, forward, liquidez, próximo
     resultado com `actual`/`estimated`, flags);
  4. seção "Excluídos": `ticker — motivo` agrupada por motivo, com a contagem.
- Sem arredondar no cálculo; arredondar só na serialização (2 casas para R$, 2 para vol points).
- Célula sem dado = `—` (nunca `0`).

## 4. Fonte dos números (sem recomputar fórmula)

Esta onda **não calcula métrica**: lê `metrics_underlying`, `metrics_series`, `earnings`,
`legs`/`raw_cotahist` (para o vínculo por ISIN e para o universo por volume). Se um número não
está no lake, ele sai `null` + flag/motivo — não é derivado aqui.

## 5. Donos de arquivo

| Pacote | Arquivos |
|---|---|
| F2d (implementador) | `backend/src/b3o/query/service.py` (método `screen` + `ScreenRow`), `backend/src/b3o/derive/screen.py` (novo: agregação pura de linhas e formatação do Markdown), `backend/src/b3o/cli.py` (só o comando `screen`) |
| congelado | todo o resto (`metrics/`, `ingest/`, `store/`, `derive/` existentes) |

- `derive/screen.py` é **puro**: recebe listas/dicts, devolve linhas e o Markdown; não abre
  arquivo, não consulta DuckDB.
- A escrita do relatório usa `b3o.fsutil.replace_file` (EACCES do bind).

## 6. Critério de pronto

1. `podman compose run --rm test ruff check src` limpo.
2. No dado real: `b3o screen --date 2026-11-19` roda e gera o `.md`; as contagens do relatório
   batem com consulta independente (**289** no universo, **163** `ok`, **126** excluídos =
   124 `no_options` + 2 `no_trade`).
3. 1 número conferido à mão por caminho independente: `iv_atm_30d` de PETR4 em 19/11 = **31,43**
   (ADR-020; interpolação 32,84 em 18/12 e 30,96 em 15/01). Repetir também `spot` = **R$ 42,67**
   (fechamento `4267` ¢ no `010`).
4. Nenhuma ocorrência de `signal_class`, "premium", "acerto" ou "ranking" na saída.
5. `--max-rows 5` corta a tabela do terminal em 5 linhas alfabéticas e o `.md` continua completo;
   rodar duas vezes produz o mesmo arquivo (idempotente).
6. Data sem sessão (ex.: `2026-11-21`, sábado) sai com erro explícito e exit ≠ 0.
7. Armadilha nova (se houver) anotada no `AGENTS.md`; commit `feat(f2/screen): …`.

## 7. Correções feitas na aceitação (23/11/2026, orquestrador)

Três pontos do contrato original estavam errados e foram corrigidos **antes** de aceitar
(nenhum número do lake foi alterado):

1. **Flag de grade não é motivo de exclusão** (§1). O texto original mandava excluir por
   `no_rates/no_forward/sparse_chain/no_expiry`; no dado real 153 dos 172 papéis com métrica têm
   ao menos uma dessas flags. A regra passou a ser: a linha é `ok` se tem pelo menos um número
   legível; a flag só explica a célula `—`. Exclusão por grade só quando **todos** os campos de
   IV/RV/VRP estão nulos.
2. **Divisor da média de volume é `len(window)`** (até 20), não 20 fixo. É o que
   `build_liquidity` faz; com 20 fixo, uma data no começo do lake (janela de 1 sessão) encolhia
   o universo para ~50 papéis. Em 19/11 a janela é cheia e o resultado continua **289**.
3. **`iv_rank_252` está em 0–100** (`derive/vrp.iv_rank_252` devolve `100 × …`), não 0–1 como o
   rascunho dizia. O `ScreenRow` passou a documentar 0–100.
