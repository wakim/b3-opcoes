# Contrato — Onda C (F0.11 liquidez · F0.12 CLI prepare/health)

**Dono do contrato:** orquestrador · **Data:** 2026-11-22 · **Válido para:** C1.

C1 costura a Onda B inteira num pipeline reproduzível: `b3o prepare` de ponta a
ponta, `b3o health` honesto, e o **relatório de liquidez que define o universo**.

## 0. Donos de arquivo

| Pacote | Dono | Arquivos |
|---|---|---|
| C1 | impl. C1 | `src/b3o/config.py`, `src/b3o/derive/liquidity.py`, `src/b3o/query/__init__.py`, `src/b3o/query/service.py`, `src/b3o/cli.py`, `tests/test_config.py`, `tests/test_liquidity.py`, `tests/test_health.py`, `tests/test_cli_prepare.py` |
| congelado | todos os da Onda A + B + `docs/contratos/*` |

`src/b3o/cli.py` passa a ser do C1 nesta onda (o orquestrador não toca).
`query/service.py` nasce aqui com `health()` só — as outras 9 funções da spec §7
entram em F2/F3; nada de SQL além do que `health` precisar (duckdb permitido
**só aqui e em store/**).

## 1. Defeitos da revisão A que são requisito aqui

- **D2:** o client httpx é criado AQUI, com `httpx.Timeout(connect=10, read=120)`
  e `BROWSER_UA` — e um **teste prova** (assert do timeout no client construído
  pelo factory do prepare). `read=120` (não 90): medido pelo B5, o servidor de OI
  estoura leitura com 60 s e já deu `ReadTimeout` até com 170 s sob carga — o
  retry cobre, mas o timeout não pode ser apertado.
- **D3:** o parse conta linhas por mercado durante a ingestão (um `Counter`
  no streaming — de graça) e grava em `ingest_state.counts_by_market`; o health
  expõe. ADR-007 sem contagem é silêncio disfarçado.
- As contagens de linhas puladas (`skipped_lines` do state, vindas do
  `ValidationReport`) aparecem no health.

## 2. `src/b3o/config.py`

- `load_config(path: Path) -> Config`: YAML plano (P-B), chaves = campos;
  `Config` dataclass com `data_dir: Path`, `log_level: str`, `model_version: int`,
  `cotahist_base_url: str`. Erro de leitura/valor → `SourceError(code="bad_config",
  …, hint)` — nunca KeyError cru.

## 3. `b3o prepare` (pipeline one-shot; `--watch` agenda)

```
1. load_config
2. client = httpx.Client(headers={"User-Agent": BROWSER_UA},
                         timeout=httpx.Timeout(connect=10, read=90),
                         follow_redirects=True)
3. COTAHIST_A{ano}.ZIP → fetch_cotahist (A1) → 304 ok / 200 ok / erro = para TUDO
4. validate_cotahist → fatal? exit ≠ 0 com relatório resumido
5. iter_cotahist (contando por mercado — D3) → write_cotahist_year (B1, streaming)
6. read_cotahist_years → build_legs + classify_strike_events (B2)
   → persiste legs/strike_events em parquet (padrão B1: data_dir/legs/year=…,
   data_dir/strike_events/… — escrita via store/lake.py helper SE o B1 expuser;
   senão escreva direto com pyarrow aqui e REGISTRE)
7. rates (B3): refdate = última sessão do lake; grava rates_di
8. earnings (B4): baixa ITR/DFP/FCA do ano corrente **E DO ANTERIOR**
   (descoberta B4: o DFP corrente é quase vazio; sem o DFP do ano anterior não
   existe actual 31-12 nem estimativa — cache com ETag em data_dir/cvm/),
   build com last_session = última sessão do lake, grava
9. OI (B5): para as últimas N sessões disponíveis (comece com as do lake), grava
10. liquidity (aqui, §4) → out/relatorio-liquidez-AAAA-MM-DD.md + universo no state
11. ingest_state update (etag/sha/trailer/last_session/counts/skipped/RSS pico)
12. resumo de uma tela no stdout (P-G); exit 0
```

- **Ordem de falha (spec §4):** cada fonte falha independente — sem OI/rates/
  earnings o prepare SEGUE e o health acusa (P-E). Sem COTAHIST e sem lake →
  exit ≠ 0.
- RSS pico: `resource.getrusage(RUSAGE_SELF).ru_maxrss` no fim, em MiB, no state.
- `--watch`: loop — roda, dorme até as próximas 21:30 America/Sao_Paulo, repete.
  Brasil não tem DST desde 2019: **offset fixo −03:00** (decisão registrada,
  sem dependência de tzdata). Nunca `date.today()` para DATA DE REFERÊNCIA
  (P-C) — o relógio só serve para agendar e carimbar `updated_at`.
- Idempotência: rodar 2× → mesmo lake (B1 garante por partição; o state atualiza).

## 4. `src/b3o/derive/liquidity.py` (F0.11) — PURA

```python
@dataclass(frozen=True, slots=True)
class LiquidityUnderlying:
    underlying: str
    liquid_days_20d: int       # dias com >=5 strikes liquidos na banda e >=2 vencimentos
    avg_volume_brl_20d: int   # centavos (volume do SUBJACENTE, mercado 010)
    days_to_liquidate: float | None  # oi_total / media volume 20d; None sem OI
    in_universe: bool          # avg_volume_20d >= R$ 5 M (spec §6.9)

def build_liquidity(rows: Iterable[CotahistRow], oi: Mapping[date, Mapping[str, int]]) -> list[LiquidityUnderlying]: ...
```

- **Série líquida (spec §6.9):** `trades >= 5` nas últimas 5 sessões **e**
  `spread_pct <= 10` **e** `bid > 0` **e** `ask > 0`; sem book (bid=ask=0),
  `mid = close` e flag `one_sided` — para liquidez, `one_sided` NÃO é líquida.
- **Banda 20Δ–80Δ proxy:** `K/S ∈ [0,75, 1,25]` (moneyness por spot — Δ real
  só existe no F1; o relatório DIZ isso em uma linha: "banda aproximada por
  moneyness; substituída por Δ em F1").
- **Papel no universo:** volume financeiro médio 20d do subjacente (mercado
  010) `≥ R$ 5 M` (centavos: 500_000_000¢... cuidado: R$ 5.000.000 = 5×10^8
  centavos? não: R$ 1 = 100¢ → R$ 5 M = 5×10^8 ¢ ✓ int64).
- `days_to_liquidate` (§6.9): `oi / média de volume 20d` — por SÉRIE no
  funil (F2); aqui por PAPEL com o somatório do OI das séries do papel (diga
  no relatório que é a versão por papel). **Unidade (descoberta B5, AGENTS):**
  para OPÇÃO o `oi` é `total_qty` (`Total de posições`) em **ações-objeto**, e o
  volume 20d do subjacente é em ações — a divisão é coerente sem multiplicador;
  `OpnIntrst` (contratos) vem vazio para 100% das opções e NÃO é o campo.
- strike==0 (IBOV órfã, AGENTS) não entra em NENHUMA conta de banda/strike.
- Saída: relatório Markdown em `out/relatorio-liquidez-AAAA-MM-DD.md`:
  tabela por papel (liquid_days, avg_volume, days_to_liquidate, in_universe),
  o número do universo no topo e as datas da sessão. P-T: nome com data.

## 5. `b3o health` (F0.12) — via `QueryService.health()`

`src/b3o/query/service.py`: classe `QueryService(data_dir: Path)` com
`health() -> Health` (dataclass → dict no CLI):

```
last_session (date), age_sessions (int — em SESSÕES ÚTEIS, contadas do lake,
  nunca do relógio), counts_by_date (últimas 5), counts_by_market,
  skipped_lines {width, numeric}, rate_source (último), oi_available_dates,
  oi_unavailable (bool), earnings_coverage (n. tickers do universo mapeados),
  unexplained_gap: null (F0: sem RV; campo existe, honesto),
  last_error (do state), rss_peak_mib, model_version, as_of (do state.updated_at)
```

- `age_sessions`: diferença em pregões entre `last_session` do lake e a última
  data COM DADO na fonte?? NÃO — idade = quantos pregões o dado tem de
  atraso RELATIVO A PRÓPRIA fonte... F0: idade em pregões desde a última
  sessão presente no lake vs. a última sessão esperada — use a última data do
  arquivo + a contagem de datas (se o arquivo parou de atualizar, a idade
  cresce contra o relógio só como carimbo `updated_at` e `now` informativo
  marcado como `now_wall_clock_informative`). A regra dura: **a data de
  referência vem do arquivo**; a idade honesta é "days since last_session
  (útil)" computada com o CALENDÁRIO do próprio lake (datas presentes), e o
  health ainda mostra `updated_at` para o humano decidir.
- Erro em qualquer leitura → campo com `{error:{code,message,hint}}` nesse
  item, NUNCA zero silencioso.
- `b3o health` imprime JSON uma-tela (P-G).

## 6. Definição de pronto

1. `podman compose run --rm prepare` de ponta a ponta no real (1ª vez baixa
   105 MB, 2ª vez 304 — cronometre as duas e cole).
2. `podman compose run --rm app b3o health` responde com os campos acima.
3. `out/relatorio-liquidez-*.md` existe e o universo aparece no AGENTS.md
   (o orquestrador registra depois do seu relatório — você NÃO edita o AGENTS).
4. pytest verde + ruff limpo; D2/D3 provados por teste.
5. Commit só dos seus arquivos; `feat(f0/prepare): …`.
