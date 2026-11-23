"""`QueryService` (F0.12): a **única** camada de consulta do lake.

Nasce com `health()` — as outras funções da spec §7 entram em F2/F3 — e com o
mínimo que o `prepare` precisa ler de forma honesta (`lake_calendar`,
`counts_by_date`, `spot_tickers`). A Onda F acrescenta as **leituras tipadas** que
o pipeline de métricas consome: `options_on`, `spot_bars`, `rates_curve`,
`strike_events`, `earnings_rows`, `open_interest_on` e `series_history`. DuckDB é
permitido **aqui e em `store/`**: qualquer outra query no projeto é bug de
arquitetura (teste verde).

Armadilha do DuckDB medida em 22/11/2026: com **mais de um** `read_parquet(?)`
na mesma consulta, o binding posicional entrega a primeira lista de caminhos a
todas as ocorrências (a CTE de `legs` recebia o Parquet do COTAHIST). Quem lê
duas tabelas usa `_fetch_named` com `$nome` — continua parametrizado e sem
caminho em f-string.

Regras que o `health` respeita:

- **a data de referência vem do arquivo** (P-C): `last_session` é a maior data
  presente no lake e `age_sessions` é contado no **calendário do próprio lake**
  — é a distância, em pregões, entre o que o `ingest_state` diz ter carregado e
  o que o lake de fato tem. Relógio não entra aqui; o carimbo de parede
  (`now_wall_clock_informative`) é acrescentado pela CLI, que é a borda;
- **nada de zero silencioso**: todo item que não pôde ser lido vira
  `{"error": {code, message, hint}}` **naquele item**. Zero, lista vazia e
  `None` só aparecem quando são a verdade;
- `counts_by_market` e `last_error` moram no `prepare_state.json` (sidecar):
  o `ingest_state.json` da Onda B é um schema fechado e recusa chave nova —
  ADR-012 registra o desvio e o dia em que ele volta para dentro do state;
- `skipped_lines` vem do `ingest_state` (ADR-007: linha torta se conta).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

import duckdb

from b3o.derive.calendar import trading_sessions
from b3o.derive.dist import expected_move_1s
from b3o.derive.forward import ParityQuote, forward_by_parity
from b3o.derive.liquidity import DAYS_WINDOW, VOLUME_MIN_CENTS
from b3o.derive.realized import DEFAULT_GAP_THRESHOLD, flag_unexplained
from b3o.fsutil import replace_file
from b3o.ingest.errors import SourceError, SourceFailure
from b3o.ingest.parse import KEPT_MARKETS
from b3o.store.lake import cotahist_partition_path
from b3o.store.state import MODEL_VERSION, IngestState, load_ingest_state

log = logging.getLogger(__name__)

PREPARE_STATE_FILE = "prepare_state.json"
"""Sidecar do `prepare` (diagnóstico que o `ingest_state` congelado não aceita)."""

PREPARE_STATE_VERSION = 1

_HINT_DATA_DIR = "confira B3_DATA_DIR (no container é /data) e se o lake já rodou b3o prepare"
_HINT_PREPARE = "rode `b3o prepare` para popular o lake antes de consultar"


# --------------------------------------------------------------------------- #
# Sidecar do prepare (D3 + último erro + RSS)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PrepareState:
    """Retrato do último `prepare` que não cabe no `ingest_state` (schema fechado).

    Todos os campos são de diagnóstico: qualquer um ausente/ilegível degrada para
    `None` no `health` (com erro explícito quando é o caso), nunca para zero.
    """

    counts_by_market: dict[str, dict[str, int]] = field(default_factory=dict)
    """`{ano: {mercado: linhas}}` de **todas** as linhas `01` do arquivo (D3)."""

    universe: tuple[str, ...] = ()
    """Papéis que fecharam o corte de liquidez no último prepare (§6.9)."""

    universe_session: date | None = None
    """Sessão em que o universo foi medido (a última do lake)."""

    last_error: dict[str, str] | None = None
    """Último `{code, message, hint}` que não derrubou o prepare (P-E)."""

    failures: tuple[dict[str, str], ...] = ()
    """Todas as falhas de fonte do último prepare (estágio + erro)."""

    rss_peak_mib: float | None = None
    """Pico de RSS do processo no fim do prepare (`ru_maxrss` em MiB)."""

    rates: dict[str, object] | None = None
    """`{refdate, source, nodes}` da curva gravada."""

    open_interest: dict[str, object] | None = None
    """`{with_data, without_data, errors}` da janela do OI."""

    oi_date: date | None = None
    """Data de OI usada no `days_to_liquidate`."""

    earnings: dict[str, object] | None = None
    """`{actual, estimated, tickers}` do calendário gravado."""

    liquidity: dict[str, object] | None = None
    """`{report, universe_count, sessions}` do relatório de liquidez."""

    cotahist: dict[str, object] | None = None
    """`{year, status, etag, sha256, trailer_total, lines}` do último download."""

    duration_seconds: float | None = None
    """Tempo do prepare inteiro (parede)."""

    def to_dict(self) -> dict[str, object]:
        """JSON do sidecar (datas ISO, chaves ordenadas na gravação)."""
        return {
            "version": PREPARE_STATE_VERSION,
            "counts_by_market": {
                year: dict(sorted(markets.items()))
                for year, markets in sorted(self.counts_by_market.items())
            },
            "universe": list(self.universe),
            "universe_session": self.universe_session.isoformat()
            if self.universe_session
            else None,
            "last_error": self.last_error,
            "failures": list(self.failures),
            "rss_peak_mib": self.rss_peak_mib,
            "rates": self.rates,
            "open_interest": self.open_interest,
            "oi_date": self.oi_date.isoformat() if self.oi_date else None,
            "earnings": self.earnings,
            "liquidity": self.liquidity,
            "cotahist": self.cotahist,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, raw: object) -> PrepareState:
        """Leitura tolerante: campo ausente/torto vira default, nunca exceção.

        O sidecar é diagnóstico de um `prepare` anterior — um JSON de versão
        antiga não pode impedir o `health` de responder. O que é *dado* (lake,
        state) continua com validação estrita nos módulos da Onda B.
        """
        if not isinstance(raw, Mapping):
            return cls()
        markets: dict[str, dict[str, int]] = {}
        raw_markets = raw.get("counts_by_market")
        if isinstance(raw_markets, Mapping):
            for year, counts in raw_markets.items():
                if not isinstance(counts, Mapping):
                    continue
                parsed = {
                    str(market): int(value)
                    for market, value in counts.items()
                    if isinstance(value, int) and not isinstance(value, bool)
                }
                markets[str(year)] = parsed
        universe_raw = raw.get("universe")
        universe = (
            tuple(str(item) for item in universe_raw)
            if isinstance(universe_raw, list)
            else ()
        )
        return cls(
            counts_by_market=markets,
            universe=universe,
            universe_session=_iso_date(raw.get("universe_session")),
            last_error=_error_mapping(raw.get("last_error")),
            failures=tuple(
                item
                for item in (
                    _error_mapping(entry) for entry in _as_list(raw.get("failures"))
                )
                if item is not None
            ),
            rss_peak_mib=_float_or_none(raw.get("rss_peak_mib")),
            rates=_mapping_or_none(raw.get("rates")),
            open_interest=_mapping_or_none(raw.get("open_interest")),
            oi_date=_iso_date(raw.get("oi_date")),
            earnings=_mapping_or_none(raw.get("earnings")),
            liquidity=_mapping_or_none(raw.get("liquidity")),
            cotahist=_mapping_or_none(raw.get("cotahist")),
            duration_seconds=_float_or_none(raw.get("duration_seconds")),
        )


def prepare_state_path(data_dir: Path) -> Path:
    """Caminho do sidecar: `data_dir/prepare_state.json`."""
    return Path(data_dir) / PREPARE_STATE_FILE


def load_prepare_state(data_dir: Path) -> PrepareState:
    """Lê o sidecar; ausente/ilegível devolve o default (o `health` marca a falta)."""
    path = prepare_state_path(data_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return PrepareState()
    except (OSError, ValueError) as exc:
        log.warning("prepare_state ilegível em %s: %s", path, exc)
        return PrepareState()
    return PrepareState.from_dict(raw)


def save_prepare_state(data_dir: Path, state: PrepareState) -> Path:
    """Grava o sidecar de forma atômica (`tmp/` + `fsutil.replace_file`)."""
    root = Path(data_dir)
    path = prepare_state_path(root)
    tmp_dir = root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"prepare_state-{uuid.uuid4().hex[:12]}.json"
    try:
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        replace_file(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Health:
    """Saúde do lake, pronta para JSON de uma tela (P-G).

    Campos que podem falhar individualmente são `object`: ou o valor, ou
    `{"error": {code, message, hint}}` — nunca zero/list-vazia silencioso.
    """

    last_session: str | None
    """Maior `refdate` presente no lake (a verdade do dado, P-C)."""

    age_sessions: object
    """Pregões de atraso contra a última sessão **esperada** (D-1, calendário B3)."""

    lake_sessions: object
    """Quantas sessões o lake tem (todas as partições)."""

    lake_years: object
    """Anos com partição no lake."""

    counts_by_date: object
    """Linhas por `refdate` nas últimas 5 sessões (do lake, não do state)."""

    counts_by_market: object
    """`{ano: {mercado: linhas}}` de todas as linhas `01` (D3, sidecar)."""

    discarded_by_market: object
    """Mercados fora do universo (`017`, `021`, `030`…) da última safra (D3)."""

    skipped_lines: object
    """`{width, numeric}` do `ingest_state` (ADR-007)."""

    rate_source: object
    """`{refdate, source, nodes}` do último dia da curva DI (P-M)."""

    rates_sessions: object
    """Quantas sessões **do lake** têm curva (PRE da B3 ou `selic_flat`).

    `0` é a verdade de um lake sem nenhuma curva e não é silêncio: a falta da
    tabela aparece em `rate_source` como `rates_missing` (P-M).
    """

    oi_available_dates: object
    """Datas com posições em aberto gravadas (P-N)."""

    oi_unavailable: object
    """`True` quando a última sessão do lake não tem OI (P-N)."""

    earnings_coverage: object
    """`{universe, mapped, unmapped, dates}` do calendário × universo (§6.14)."""

    universe: object
    """`{session, count, tickers}` do universo medido no último prepare (§6.9)."""

    unexplained_gap: object
    """Overnights de `|r| > 40%` no mercado `010` que nenhum evento do B2 explica.

    Definição fechada (§6.5 + AGENTS): fechamento anterior ≥ R$ 1,00 (piso de
    preço), o papel **negociou na sessão imediatamente anterior do lake**
    (lacuna de negociação não é gap de preço) e o gap do dia não está em
    `strike_events` (qualquer `kind` — `indeterminate` também é evento real).

    `{"count", "sample"}` (amostra de até 20) quando há gap; `None` quando não
    há nenhum, que é o estado saudável (nada a explicar). Sem dado para julgar
    (ano com candidato e sem partição de eventos), o valor é `{"error": …}`.
    """

    last_error: dict[str, str] | None
    """Último erro de fonte que não derrubou o prepare (P-E)."""

    rss_peak_mib: float | None
    """Pico de RSS do último prepare, em MiB (3,7 GiB de RAM no motor)."""

    model_version: int
    """Versão do cálculo das **tabelas derivadas** (`metrics_underlying`/`metrics_series`).

    É a versão que produziu os números que o painel mostra (F3.0). Sem tabela
    derivada, cai para a versão do `ingest_state` — e o campo
    `model_version_source` diz de onde veio.
    """

    ingest_model_version: int
    """O que o `ingest_state` diz (`prepare`) — diagnóstico, não a verdade do cálculo."""

    model_version_mismatch: bool
    """`True` quando o `ingest_state` e as tabelas derivadas discordam (F3.0)."""

    model_version_source: str
    """`"metrics"` (tabelas derivadas) ou `"ingest_state"` (fallback explícito)."""

    model_version_note: str | None
    """Explicação quando há divergência, tabela ausente ou leitura falha."""

    as_of: str | None
    """`updated_at` do `ingest_state` (ISO com offset, P-D)."""

    expected_last_session: str | None = None
    """Última sessão exigível hoje (estritamente antes de hoje, calendário B3)."""

    def to_dict(self) -> dict[str, object]:
        """Serialização JSON (ordem estável, nada de `date` solto)."""
        return {
            "last_session": self.last_session,
            "age_sessions": self.age_sessions,
            "expected_last_session": self.expected_last_session,
            "lake_sessions": self.lake_sessions,
            "lake_years": self.lake_years,
            "counts_by_date": self.counts_by_date,
            "counts_by_market": self.counts_by_market,
            "discarded_by_market": self.discarded_by_market,
            "skipped_lines": self.skipped_lines,
            "rate_source": self.rate_source,
            "rates_sessions": self.rates_sessions,
            "oi_available_dates": self.oi_available_dates,
            "oi_unavailable": self.oi_unavailable,
            "earnings_coverage": self.earnings_coverage,
            "universe": self.universe,
            "unexplained_gap": self.unexplained_gap,
            "last_error": self.last_error,
            "rss_peak_mib": self.rss_peak_mib,
            "model_version": self.model_version,
            "ingest_model_version": self.ingest_model_version,
            "model_version_mismatch": self.model_version_mismatch,
            "model_version_source": self.model_version_source,
            "model_version_note": self.model_version_note,
            "as_of": self.as_of,
        }


# --------------------------------------------------------------------------- #
# Leituras tipadas (Onda F) — o pipeline de métricas consome exatamente estas
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class OptionDay:
    """Uma linha de opção do dia, com o papel resolvido pelo ISIN (§4.1/ADR-014).

    Preços e strike em **centavos por ação**, como no lake (`quote_factor` ainda
    **não** aplicado: `mid = (bid+ask)/2 / quote_factor` é conta do F1b).
    """

    refdate: date
    ticker: str
    maturity: date
    """Vencimento do **leg** (a série é `(ticker, maturity)`, nunca o ISIN)."""
    side: str
    """`C`/`P`, do mercado (`070`/`080`) consolidado no leg."""
    style: str | None
    """`A`/`E` consolidado no leg; `None` + flag `style_unknown` quando não resolve."""
    leg_id: str
    leg_index: int
    strike: int
    """Strike do **leg persistido** — autoridade de fronteira (o ajuste é por data)."""
    strike_raw: int
    """Strike do `raw_cotahist` do dia — igual ao do leg, exceto quando divergem (§4.8)."""
    distribution_id: str
    quote_factor: int
    """`FATCOT`: preço por ação = preço ÷ fator (não é sempre 1)."""
    close: int
    avg: int
    best_bid: int
    best_ask: int
    trades: int
    volume: int
    isin_underlying: str
    underlying: str
    underlying_market: str
    """`010` ou `020` — o mercado que forneceu o spot do dia (§6.1)."""
    spot_close: int
    """Fechamento do papel no dia, no mercado de `underlying_market`, em centavos."""


@dataclass(frozen=True, slots=True)
class SpotBar:
    """Uma sessão do subjacente no mercado à vista, em **centavos**.

    `qty` é a **quantidade** negociada em ações (a unidade do `Total de posições`
    do OI) e `volume` é o financeiro em centavos: `volume ≈ qty × avg ÷ FATCOT`
    (o `010`/`020` tem `FATCOT = 1`). `trades` é o número de negócios. Os três
    vêm do mesmo recorte do OHLC e existem para `days_to_liquidate`
    (`Σ OI ÷ média de qty 20d`) e para o volume médio do papel (§6.9).
    """

    refdate: date
    open: int
    high: int
    low: int
    close: int
    market: str
    """`010` (lote padrão) ou `020` (fracionário) — o escolhido do dia."""

    qty: int = 0
    """Ações negociadas no dia (mesma unidade do `total_qty` do OI)."""

    volume: int = 0
    """Volume financeiro em centavos (`volume ≈ qty × avg`; `FATCOT` é 1 no à vista)."""

    trades: int = 0
    """Número de negócios da sessão."""


@dataclass(frozen=True, slots=True)
class RateNode:
    """Um nó da curva da sessão: `rate_annual` em **% a.a.**, base 252."""

    du: int
    rate_annual: float
    source: str


@dataclass(frozen=True, slots=True)
class EventRow:
    """Uma linha de `strike_events` (evento de ajuste de strike do B2, spec §6.6).

    `delta_strike` é **magnitude** (AGENTS: os 296 proventos de 2026 têm valor
    positivo; o sinal mora em `kind`). É `None` em `scale` e em `indeterminate`
    — o lake não inventa zero, e aqui também não: zero significaria "não mudou".
    """

    underlying: str
    date: date
    kind: str
    delta_strike: int | None
    factor: float | None
    n_series: int
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class EarningsRow:
    """Uma data do calendário da CVM (`actual`) ou estimada (`estimated`)."""

    ticker: str
    ref_period: date
    date: date
    kind: str


@dataclass(frozen=True, slots=True)
class SeriesDay:
    """Um dia de negócio de uma série de opção (liquidez §6.9 e volume 20d)."""

    refdate: date
    ticker: str
    trades: int
    close: int
    best_bid: int
    best_ask: int


@dataclass(frozen=True, slots=True)
class UnderlyingMetricRow:
    """Uma linha de `metrics_underlying` para o backtest (F1b, contrato §4.3).

    Só as colunas que o portão consome: a classe **gravada** (não se recalcula),
    a IV ATM 30d e o `vrp_z_252` (aquecimento), mais `vrp`/`rv30` para a prova
    de look-ahead do z. Unidades do contrato: IV/RV em % a.a., VRP em pontos de
    vol. `model_version` viaja junto para o relatório não misturar modelos.
    """

    refdate: date
    underlying: str
    underlying_market: str
    iv_atm_30d: float | None
    rv30: float | None
    vrp: float | None
    vrp_z_252: float | None
    signal_class: str
    model_version: int


@dataclass(frozen=True, slots=True)
class ScreenRow:
    """Uma linha do painel descritivo (F2d = contrato onda-h §2).

    **Descritivo, não preditivo** (ADR-023/ADR-024): a linha só repete o que o
    lake já calculou. `signal_class` não é lido em lugar nenhum desta onda.

    Unidades: `spot_brl`/`forward_30d` em **R$** (o parquet guarda centavos e a
    conversão acontece aqui, na borda de serialização); IV e RV em **% a.a.**;
    `vrp`/`rr25`/`bfly25`/`ts_slope_30_90` em **pontos de vol**; `iv_rank_252`
    em 0–100; `iv_pct_252` em 0–100; `exp_move_1s_30d` em **fração** do spot.
    """

    underlying: str
    """Ticker do papel (o ISIN resolveu opção→papel, nunca o prefixo do ticker)."""

    status: str
    """`ok` (linha em `metrics_underlying`) ou `excluded`."""

    exclusion: str | None
    """Motivo do §1 do contrato quando `status = "excluded"`."""

    as_of_session: date
    """Sessão de referência do lake (nunca o relógio, P-C)."""

    model_version: int
    """Versão do cálculo que produziu a linha (`metrics_underlying`)."""

    spot_brl: float | None
    iv_atm_30d: float | None
    iv_rank_252: float | None
    iv_pct_252: float | None
    rv20: float | None
    rv30: float | None
    rv60: float | None
    vrp: float | None
    vrp_z_252: float | None
    rr25: float | None
    bfly25: float | None
    ts_slope_30_90: float | None
    exp_move_1s_30d: float | None
    implied_div_yield_30d: float | None
    forward_30d: float | None
    days_to_liquidate: float | None
    rate_source: str | None
    n_series: int
    n_liquid_series: int
    n_expiries: int
    earnings_next_date: date | None
    earnings_next_kind: str | None
    """`actual` (confirmado) ou `estimated` (previsto)."""
    earnings_before_expiry: bool
    flags: list[str]
    """União das flags do papel, em ordem alfabética (não viram nota)."""


@dataclass(frozen=True, slots=True)
class ScreenBundle:
    """`screen()` + o contexto que o relatório precisa e as linhas não carregam.

    `outside_universe` são os papéis **com métrica** na sessão mas fora do corte
    de volume (§1 do contrato): ficam de fora do recorte e o relatório diz isso
    em voz alta, em vez de sumirem.

    `earnings_coverage_start` é a **primeira data** do calendário da CVM no lake.
    Sessão anterior a ela não tem "próximo resultado" (o campo sai `None`), e o
    relatório avisa — em vez de mostrar o primeiro documento conhecido como se
    fosse o próximo (P-N).
    """

    session: date
    model_version: int
    rows: list[ScreenRow]
    outside_universe: list[str]
    earnings_coverage_start: date | None = None


@dataclass(frozen=True, slots=True)
class TermRow:
    """Um vencimento na estrutura a termo do papel (F3.2).

    `iv_atm` é a IV da série com `|Δ|` mais próximo de 0,5 **já gravada** no lake
    (seleção, não recálculo); `forward_brl` é a paridade put-call reconstruída dos
    `mid`s gravados com `derive.forward.forward_by_parity` (a mesma função pura do
    pipeline); `exp_move_1s` é `1,25 × straddle ATM / spot` (§6.10) em **fração**.
    """

    maturity: date
    t: float
    """`t` gravado do vencimento, em dias úteis / 252."""
    iv_atm: float | None
    """% a.a.; `None` quando nenhuma série com IV/Δ existe no vencimento."""
    atm_strike_brl: float | None
    forward_brl: float | None
    forward_method: str
    """`crossing` | `ols` | `none` (o que a paridade conseguiu)."""
    exp_move_1s: float | None
    """Fração do spot (0,06 = 6%)."""
    n_strikes: int
    """Strikes distintos (> 0) com série do papel no dia."""
    n_liquid_strikes: int
    """Strikes distintos com ao menos uma série `is_liquid`."""


@dataclass(frozen=True, slots=True)
class SmileRow:
    """Uma série no smile de um vencimento (F3.2), ordenada por `(strike, side)`."""

    strike_brl: float
    iv_pct: float | None
    delta: float | None
    side: str
    is_liquid: bool


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """Uma sessão do histórico do papel (`metrics_underlying`, F3.2)."""

    date: date
    iv_atm_30d: float | None
    rv30: float | None
    vrp: float | None
    vrp_z_252: float | None
    rr25: float | None


@dataclass(frozen=True, slots=True)
class UnderlyingDetail:
    """Leitura de um papel (spec §7: `underlying`), montada só com o que existe."""

    ticker: str
    as_of_session: date
    model_version: int
    in_universe: bool
    """Papel está no recorte de liquidez da sessão (senão `summary` é `None`)."""
    summary: ScreenRow | None
    """A **mesma** linha do `screen_bundle` (sem `signal_class`, que nem existe)."""
    term: list[TermRow]
    smile: list[SmileRow]
    smile_maturity: date | None
    history: list[HistoryRow]
    notes: list[str]
    """Explicações de seção vazia/flag, nunca número inventado."""


@dataclass(frozen=True, slots=True)
class ChainRow:
    """Uma série na cadeia do papel (F3.2), preço em **R$ por ação**.

    `iv_pct` em % a.a., `t` em du/252, `theta_brl` em R$/ação por dia útil e
    `vega_brl` em R$/ação por 1 ponto de vol — como gravados em `metrics_series`.
    `mid_brl` é o preço usado na IV (ADR-017: o fechamento); `close_brl` é o
    fechamento do dia dividido pelo `FATCOT` (o mesmo número, exposto nos dois
    nomes que o contrato pede).
    """

    ticker: str
    side: str
    style: str | None
    maturity: date
    strike_brl: float
    t: float
    bid_brl: float | None
    ask_brl: float | None
    mid_brl: float | None
    close_brl: float | None
    iv_pct: float | None
    delta: float | None
    gamma: float | None
    theta_brl: float | None
    vega_brl: float | None
    spread_brl: float | None
    spread_pct: float | None
    is_liquid: bool
    flags: list[str]


@dataclass(frozen=True, slots=True)
class ChainResult:
    """A cadeia filtrada + o total **antes** do corte de `max_rows`."""

    ticker: str
    as_of_session: date
    model_version: int
    rows: list[ChainRow]
    total: int
    notes: list[str]


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """Um dia de uma série (F3.2): preço em R$/ação, IV em % a.a."""

    date: date
    close_brl: float | None
    mid_brl: float | None
    iv_pct: float | None
    delta: float | None


@dataclass(frozen=True, slots=True)
class SeriesDetail:
    """Identidade + última linha + histórico de uma série (`ticker`, `maturity`)."""

    ticker: str
    maturity: date
    side: str
    style: str | None
    strike_brl: float
    leg_id: str
    t: float
    is_liquid: bool
    flags: list[str]
    gamma: float | None
    theta_brl: float | None
    vega_brl: float | None
    last: SeriesPoint
    history: list[SeriesPoint]
    as_of_session: date
    model_version: int


@dataclass(frozen=True, slots=True)
class StrikeEventRow:
    """Um evento de ajuste de strike do papel (B2, §6.6)."""

    date: date
    kind: str
    delta_strike: int | None
    factor: float | None
    n_series: int
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class EarningsEventRow:
    """Uma data do calendário da CVM (`actual`) ou estimada (`estimated`)."""

    date: date
    ref_period: date
    kind: str


@dataclass(frozen=True, slots=True)
class EventsDetail:
    """Vencimentos, eventos de strike e resultados de um papel (`events`)."""

    ticker: str
    as_of_session: date
    model_version: int
    maturities: list[date]
    strike_events: list[StrikeEventRow]
    earnings: list[EarningsEventRow]
    next_earnings: EarningsEventRow | None
    notes: list[str]


@dataclass(frozen=True, slots=True)
class _ScreenMetrics:
    """Colunas de `metrics_underlying` que o painel lê (uma linha por papel).

    Tipo interno: o painel é descritivo e repassa os valores como estão; nada
    aqui é recalculado (contrato §4). `forward_cents` em centavos, como no lake.
    """

    underlying: str
    spot_cents: float | None
    iv_atm_30d: float | None
    iv_rank_252: float | None
    iv_pct_252: float | None
    rv20: float | None
    rv30: float | None
    rv60: float | None
    vrp: float | None
    vrp_z_252: float | None
    rr25: float | None
    bfly25: float | None
    ts_slope_30_90: float | None
    exp_move_1s_30d: float | None
    implied_div_yield_30d: float | None
    days_to_liquidate: float | None
    forward_cents: float | None
    rate_source: str | None
    flags: tuple[str, ...]
    model_version: int


class QueryService:
    """Leitura do lake: uma classe, um arquivo, nenhuma query fora daqui."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    # ---------------------------- calendário ---------------------------- #

    def lake_calendar(self, year: int | None = None) -> list[date]:
        """Datas com dado no lake, em ordem — o calendário que o `prepare` usa.

        Sem partição para o ano pedido devolve lista vazia (não é erro: o lake
        pode simplesmente não ter aquele ano); Parquet ilegível continua sendo
        `SourceFailure` do `store`.
        """
        paths = self._partitions(year)
        if not paths:
            return []
        rows = self._fetch("SELECT DISTINCT refdate FROM read_parquet(?) ORDER BY 1", paths)
        return [row[0] for row in rows]

    def counts_by_date(self, year: int | None = None) -> dict[date, int]:
        """Linhas por `refdate` (todas as sessões, ordenadas) das partições do ano."""
        paths = self._partitions(year)
        if not paths:
            return {}
        rows = self._fetch(
            "SELECT refdate, count(*) FROM read_parquet(?) GROUP BY 1 ORDER BY 1", paths
        )
        return {row[0]: int(row[1]) for row in rows}

    def spot_tickers(self, year: int | None = None) -> list[str]:
        """Tickers do mercado à vista (`010`) presentes no lake — base do earnings."""
        paths = self._partitions(year)
        if not paths:
            return []
        rows = self._fetch(
            "SELECT DISTINCT ticker FROM read_parquet(?) WHERE tpmerc = '010' ORDER BY 1",
            paths,
        )
        return [str(row[0]) for row in rows]

    def open_interest_dates(self, year: int | None = None) -> list[date]:
        """Datas com posições em aberto gravadas; vazio se o OI nunca foi baixado."""
        paths = self._oi_partitions(year)
        if not paths:
            return []
        rows = self._fetch("SELECT DISTINCT refdate FROM read_parquet(?) ORDER BY 1", paths)
        return [row[0] for row in rows]

    def open_interest_totals(self, dates: Iterable[date]) -> dict[date, dict[str, int]]:
        """`{refdate: {ticker: total_qty}}` das datas pedidas (só `total_qty` não nulo).

        `total_qty` é `Total de posições`, na unidade do ativo-objeto (ações): é a
        coluna certa para opção de ação, onde `Contratos em aberto` vem vazio em
        100% das linhas (AGENTS.md, medido no B5).
        """
        wanted = sorted(set(dates))
        if not wanted:
            return {}
        paths = self._oi_partitions(None)
        if not paths:
            return {}
        rows = self._fetch(
            "SELECT refdate, ticker, total_qty FROM read_parquet(?) "
            "WHERE total_qty IS NOT NULL AND refdate IN (SELECT unnest(?::DATE[])) "
            "ORDER BY refdate, ticker",
            paths,
            wanted,
        )
        totals: dict[date, dict[str, int]] = {}
        for refdate, ticker, quantity in rows:
            totals.setdefault(refdate, {})[str(ticker)] = int(quantity)
        return totals

    # ---------------------- leituras tipadas (Onda F) -------------------- #

    def options_on(self, refdate: date) -> list[OptionDay]:
        """Linhas de opção (`070`/`080`) de `refdate`, juntadas ao leg e ao spot.

        Origens e regras:

        - **leg** (`data/legs`) é a autoridade de fronteira: `strike`,
          `distribution_id`, `side`, `style` e `leg_id` vêm dele, não do raw;
          `strike_raw` traz o strike do `raw_cotahist` do dia para o pipeline
          detectar divergência (§4.8: o leg manda, a linha ganha `leg_mismatch`);
        - **papel**: resolvido pelo `isin_underlying` da opção contra a linha à
          vista (`010`, caindo para `020`) do **mesmo dia** — `ticker → underlying`
          não é persistido em lugar nenhum (§4.1); `010` tem precedência;
        - **descarte documentado**: opção cujo `isin_underlying` não casa com
          nenhuma linha `010`/`020` do dia **sai do retorno**. Não se inventa
          papel (o universo é de ações/units/ETFs com spot no dia) e não se
          devolve campo "não resolvido" — o número fica visível no F1b como
          diferença entre linhas de opção do dia e linhas retornadas;
        - `strike <= 0` **não** é filtrado aqui (IBOV órfã): o pipeline filtra e
          conta (§4.5, passo 2);
        - preço em centavos, `quote_factor` sem aplicar; `None` não aparece
          neste tipo — linha de opção sem `maturity` no leg não existe no lake.

        Falha de leitura (partição ausente/corrompida) é `SourceFailure`
        (`lake_missing`/`storage_error`), nunca lista vazia disfarçada.
        """
        raw_paths = self._partitions(refdate.year)
        if not raw_paths:
            raise _missing_lake_failure(self.data_dir)
        leg_paths = self._legs_partitions(refdate.year)
        if not leg_paths:
            raise SourceFailure(
                SourceError(
                    code="lake_missing",
                    message=(
                        f"partição de legs de {refdate.year} não existe em "
                        f"{self.data_dir / 'legs'}"
                    ),
                    hint="rode `b3o prepare` para derivar os legs do ano",
                )
            )
        sql = (
            "WITH options AS ("
            "  SELECT ticker, refdate, maturity, strike, quote_factor, close, avg,"
            "         best_bid, best_ask, trades, volume, isin_underlying"
            "  FROM read_parquet($raw)"
            "  WHERE refdate = $day AND tpmerc IN ('070', '080') AND isin_underlying <> ''"
            "), legs AS ("
            "  SELECT ticker, refdate, leg_id, leg_index, side, style, strike, distribution_id,"
            "         maturity"
            "  FROM read_parquet($legs)"
            "  WHERE refdate = $day"
            "), spot AS ("
            "  SELECT refdate, ticker, tpmerc, close, isin_underlying,"
            "         CASE WHEN tpmerc = '010' THEN 0 ELSE 1 END AS preference"
            "  FROM read_parquet($raw)"
            "  WHERE refdate = $day AND tpmerc IN ('010', '020') AND isin_underlying <> ''"
            "), spot_best AS ("
            "  SELECT refdate, ticker, tpmerc, close, isin_underlying FROM ("
            "    SELECT *, row_number() OVER ("
            "      PARTITION BY refdate, isin_underlying ORDER BY preference"
            "    ) AS rn FROM spot"
            "  ) WHERE rn = 1"
            ")"
            " SELECT o.refdate, o.ticker, l.maturity, l.side, l.style, l.leg_id, l.leg_index,"
            "        l.strike, o.strike, l.distribution_id, o.quote_factor, o.close, o.avg,"
            "        o.best_bid, o.best_ask, o.trades, o.volume, o.isin_underlying,"
            "        s.ticker, s.tpmerc, s.close"
            " FROM options o"
            " JOIN legs l ON l.ticker = o.ticker AND l.refdate = o.refdate"
            " LEFT JOIN spot_best s ON s.refdate = o.refdate"
            "                      AND s.isin_underlying = o.isin_underlying"
            " ORDER BY o.ticker, l.maturity, l.leg_index"
        )
        rows = self._fetch_named(
            sql,
            {
                "raw": [str(path) for path in raw_paths],
                "legs": [str(path) for path in leg_paths],
                "day": refdate,
            },
        )
        result: list[OptionDay] = []
        for row in rows:
            if row[18] is None:  # LEFT JOIN sem spot: opção sem papel resolvido sai
                continue
            result.append(
                OptionDay(
                    refdate=row[0],
                    ticker=str(row[1]),
                    maturity=row[2],
                    side=str(row[3]),
                    style=None if row[4] is None else str(row[4]),
                    leg_id=str(row[5]),
                    leg_index=int(row[6]),
                    strike=int(row[7]),
                    strike_raw=int(row[8]),
                    distribution_id=str(row[9]),
                    quote_factor=int(row[10]),
                    close=int(row[11]),
                    avg=int(row[12]),
                    best_bid=int(row[13]),
                    best_ask=int(row[14]),
                    trades=int(row[15]),
                    volume=int(row[16]),
                    isin_underlying=str(row[17]),
                    underlying=str(row[18]),
                    underlying_market=str(row[19]),
                    spot_close=int(row[20]),
                )
            )
        return result

    def spot_bars(self, underlying: str, start: date, end: date) -> list[SpotBar]:
        """OHLC do papel entre `start` e `end` (inclusive), uma barra por sessão.

        Mercado `010` tem precedência sobre `020` quando o papel negociou nos dois
        no mesmo dia (§6.1: nunca somar os dois); `market` diz qual entrou. Sem
        linha no intervalo, lista vazia — ausência de negócio não é erro.
        """
        years = range(start.year, end.year + 1)
        paths = [path for year in years for path in self._partitions(year)]
        if not paths:
            return []
        sql = (
            "SELECT refdate, open, high, low, close, tpmerc, qty, volume, trades FROM ("
            "  SELECT refdate, open, high, low, close, tpmerc, qty, volume, trades,"
            "         row_number() OVER ("
            "           PARTITION BY refdate ORDER BY CASE WHEN tpmerc = '010' THEN 0 ELSE 1 END"
            "         ) AS rn"
            "  FROM read_parquet(?)"
            "  WHERE ticker = ? AND tpmerc IN ('010', '020') AND refdate BETWEEN ? AND ?"
            ") WHERE rn = 1 ORDER BY refdate"
        )
        rows = self._fetch(sql, paths, underlying, start, end)
        return [
            SpotBar(
                refdate=row[0],
                open=int(row[1]),
                high=int(row[2]),
                low=int(row[3]),
                close=int(row[4]),
                market=str(row[5]),
                qty=int(row[6]),
                volume=int(row[7]),
                trades=int(row[8]),
            )
            for row in rows
        ]

    def spot_bars_in_market(
        self, underlying: str, start: date, end: date, market: str
    ) -> list[SpotBar]:
        """OHLC do papel **num mercado só** (`010` ou `020`), entre `start` e `end`.

        O backtest (F1b) usa o mesmo mercado da linha de métrica (`underlying_market`):
        misturar lote padrão com fracionário na mesma série de RV criaria retorno
        artificial de um mercado para o outro. Diferente de `spot_bars`, não há
        preferência nem `row_number` — o mercado é fixo. Sem linha no intervalo,
        lista vazia (ausência de negócio não é erro).
        """
        years = range(start.year, end.year + 1)
        paths = [path for year in years for path in self._partitions(year)]
        if not paths:
            return []
        rows = self._fetch(
            "SELECT refdate, open, high, low, close, tpmerc, qty, volume, trades "
            "FROM read_parquet(?) "
            "WHERE ticker = ? AND tpmerc = ? AND refdate BETWEEN ? AND ? "
            "ORDER BY refdate",
            paths,
            underlying,
            market,
            start,
            end,
        )
        return [
            SpotBar(
                refdate=row[0],
                open=int(row[1]),
                high=int(row[2]),
                low=int(row[3]),
                close=int(row[4]),
                market=str(row[5]),
                qty=int(row[6]),
                volume=int(row[7]),
                trades=int(row[8]),
            )
            for row in rows
        ]

    def rates_curve(self, refdate: date) -> list[RateNode]:
        """Nós da curva da sessão, ordenados por `du`; `[]` quando não há curva.

        `rate_annual` em **% a.a.** em todas as fontes (o F1b divide por 100 no
        `DF`). Lista vazia é a resposta honesta de "esta sessão não tem curva":
        quem consome marca `no_rates` e deixa IV/forward como `None` (§4.4) —
        nunca `DF = 1`.
        """
        paths = self._partitions_for("rates_di", refdate.year)
        if not paths:
            return []
        rows = self._fetch(
            "SELECT du, rate_annual, source FROM read_parquet(?) "
            "WHERE refdate = ? ORDER BY du, source",
            paths,
            refdate,
        )
        return [
            RateNode(du=int(row[0]), rate_annual=float(row[1]), source=str(row[2]))
            for row in rows
        ]

    def strike_events(self, years: Sequence[int] | None = None) -> list[EventRow]:
        """Todas as linhas de `strike_events` (eventos de ajuste do B2).

        `years` filtra pelo ano da **data do evento**; `None` devolve o histórico
        inteiro. Ano sem partição é "sem evento classificado" (o prepare grava
        partição vazia quando não há ajuste), então a lista pode vir vazia sem
        erro. `delta_strike` é magnitude e `None` em `scale`/`indeterminate`
        (AGENTS: a direção só existe em `kind`).
        """
        paths = self._event_partitions()
        if not paths:
            return []
        rows = self._fetch(
            "SELECT underlying, date, kind, delta_strike, factor, n_series, ambiguous "
            "FROM read_parquet(?) ORDER BY date, underlying",
            paths,
        )
        wanted = None if years is None else {int(year) for year in years}
        result: list[EventRow] = []
        for row in rows:
            if wanted is not None and row[1].year not in wanted:
                continue
            result.append(
                EventRow(
                    underlying=str(row[0]),
                    date=row[1],
                    kind=str(row[2]),
                    delta_strike=None if row[3] is None else int(row[3]),
                    factor=None if row[4] is None else float(row[4]),
                    n_series=int(row[5]),
                    ambiguous=bool(row[6]),
                )
            )
        return result

    def earnings_rows(self, years: Sequence[int] | None = None) -> list[EarningsRow]:
        """Calendário da CVM (`data/earnings/part-0.parquet`), filtrado por ano.

        A tabela é única (ADR-010 item 2: sem `year=`); `years` filtra pelo ano de
        `date` (a data do resultado, não a de referência). Sem tabela gravada,
        lista vazia — e o `health` já acusa `earnings_missing`.
        """
        path = self.data_dir / "earnings" / "part-0.parquet"
        if not path.is_file():
            return []
        rows = self._fetch(
            "SELECT ticker, ref_period, date, kind FROM read_parquet(?) ORDER BY ticker, date",
            [path],
        )
        wanted = None if years is None else {int(year) for year in years}
        return [
            EarningsRow(
                ticker=str(row[0]),
                ref_period=row[1],
                date=row[2],
                kind=str(row[3]),
            )
            for row in rows
            if wanted is None or row[2].year in wanted
        ]

    def open_interest_on(self, refdate: date) -> dict[str, int]:
        """`ticker -> total_qty` da sessão (posição na unidade do ativo-objeto).

        `total_qty` é `Total de posições` (coberta + bloqueada + descoberta): a
        coluna certa para opção de ação, onde `Contratos em aberto` vem vazio em
        100% das linhas (AGENTS, medido no B5). Data sem OI devolve `{}` — o
        consumidor marca `oi_unavailable`/`None`, nunca zero (§6.9).
        """
        return self.open_interest_totals([refdate]).get(refdate, {})

    def series_history(
        self, tickers: Sequence[str], end: date, sessions: int
    ) -> dict[str, list[SeriesDay]]:
        """Últimas `sessions` sessões **do calendário do lake** até `end`, por série.

        O corte vem do calendário do próprio lake (`lake_calendar`), não de
        aritmética de calendário: feriado e sessão sem pregão não entram na
        contagem. Devolve uma entrada para **cada** ticker pedido, com lista
        possivelmente vazia (série ausente do recorte ≠ erro).

        Uma linha por `(ticker, refdate)` com `trades`, `close`, `best_bid` e
        `best_ask` (tpmerc `070`/`080`). Se o lake tiver a mesma `(ticker,
        refdate)` duas vezes (dado duplicado), fica a de maior `volume` — o
        volume é a fonte da verdade do negócio (AGENTS).
        """
        wanted = [str(ticker) for ticker in tickers]
        result: dict[str, list[SeriesDay]] = {ticker: [] for ticker in wanted}
        if not wanted or sessions <= 0:
            return result

        calendar_paths = [
            path for year in range(self._first_year(end), end.year + 1)
            for path in self._partitions(year)
        ]
        if not calendar_paths:
            return result
        days = [
            row[0]
            for row in self._fetch(
                "SELECT DISTINCT refdate FROM read_parquet(?) WHERE refdate <= ? ORDER BY 1",
                calendar_paths,
                end,
            )
        ]
        window = days[-sessions:]
        if not window:
            return result
        start, stop = window[0], window[-1]
        paths = [
            path for year in range(start.year, stop.year + 1)
            for path in self._partitions(year)
        ]
        if not paths:
            return result
        rows = self._fetch(
            "SELECT ticker, refdate, trades, close, best_bid, best_ask FROM ("
            "  SELECT ticker, refdate, trades, close, best_bid, best_ask, volume,"
            "         row_number() OVER (PARTITION BY ticker, refdate ORDER BY volume DESC) AS rn"
            "  FROM read_parquet(?)"
            "  WHERE tpmerc IN ('070', '080') AND refdate BETWEEN ? AND ?"
            "    AND ticker IN (SELECT unnest(?::VARCHAR[]))"
            ") WHERE rn = 1 ORDER BY ticker, refdate",
            paths,
            start,
            stop,
            wanted,
        )
        for ticker, refdate, trades, close, best_bid, best_ask in rows:
            result[str(ticker)].append(
                SeriesDay(
                    refdate=refdate,
                    ticker=str(ticker),
                    trades=int(trades),
                    close=int(close),
                    best_bid=int(best_bid),
                    best_ask=int(best_ask),
                )
            )
        return result

    def metrics_underlying_rows(
        self, start: date | None = None, end: date | None = None
    ) -> list[UnderlyingMetricRow]:
        """Linhas de `metrics_underlying` (F1b), ordenadas por `(refdate, underlying)`.

        `start`/`end` filtram o `refdate` (inclusive); `None` = sem corte. Sem
        partição devolve `[]` (o `metrics` nunca rodou — ausência, não erro).
        Parquet ilegível levanta `SourceFailure(storage_error)`. Uma ocorrência
        de `read_parquet(?)` por consulta: o binding posicional é seguro aqui
        (a armadilha do AGENTS é com **duas** tabelas na mesma consulta).
        """
        paths = self._partitions_for("metrics_underlying", None)
        if not paths:
            return []
        sql = (
            "SELECT refdate, underlying, underlying_market, iv_atm_30d, rv30, vrp, "
            "vrp_z_252, signal_class, model_version FROM read_parquet(?)"
        )
        params: list[object] = [[str(path) for path in paths]]
        clauses: list[str] = []
        if start is not None:
            clauses.append("refdate >= ?")
            params.append(start)
        if end is not None:
            clauses.append("refdate <= ?")
            params.append(end)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY refdate, underlying"
        rows = self._fetch(sql, paths, *params[1:])
        return [
            UnderlyingMetricRow(
                refdate=row[0],
                underlying=str(row[1]),
                underlying_market=str(row[2]),
                iv_atm_30d=None if row[3] is None else float(row[3]),
                rv30=None if row[4] is None else float(row[4]),
                vrp=None if row[5] is None else float(row[5]),
                vrp_z_252=None if row[6] is None else float(row[6]),
                signal_class=str(row[7]),
                model_version=int(row[8]),
            )
            for row in rows
        ]

    # ----------------------- painel descritivo (F2) ---------------------- #

    def derived_model_version(self) -> int | None:
        """Maior `model_version` gravado em `metrics_underlying` ∪ `metrics_series`.

        É a versão que produziu os números que o painel mostra (F3.0). `None`
        quando nenhuma tabela derivada existe — ausência, não versão 1 inventada.
        As duas tabelas são lidas com parâmetros **nomeados** (a armadilha do
        DuckDB é com duas listas posicionais na mesma consulta).
        """
        underlying = self._partitions_for("metrics_underlying", None)
        series = self._partitions_for("metrics_series", None)
        if not underlying and not series:
            return None
        parts: list[str] = []
        params: dict[str, object] = {}
        if underlying:
            parts.append("SELECT max(model_version) AS version FROM read_parquet($underlying)")
            params["underlying"] = [str(path) for path in underlying]
        if series:
            parts.append("SELECT max(model_version) AS version FROM read_parquet($series)")
            params["series"] = [str(path) for path in series]
        rows = self._fetch_named(" UNION ALL ".join(parts), params)
        versions = [int(row[0]) for row in rows if row[0] is not None]
        return max(versions) if versions else None

    def screen(
        self, date: date | None = None, max_rows: int | None = None
    ) -> list[ScreenRow]:
        """Uma linha por papel do universo líquido da sessão (contrato onda-h §2).

        **Descritivo** (ADR-024): repete o que o lake já calculou; `signal_class`
        não é lido. A ordem é **alfabética por papel**; `max_rows=None` devolve
        todas e `max_rows=N` as N primeiras (nunca "as melhores"). `max_rows=0`
        é lista vazia **na API** — o CLI traduz `--max-rows 0` para "todas".
        Data sem sessão no lake é `SourceFailure(no_session)` — nunca relatório
        vazio.
        """
        rows = self.screen_bundle(date).rows
        if max_rows is None:
            return list(rows)
        return list(rows[: max(0, int(max_rows))])

    def screen_bundle(self, date: date | None = None) -> ScreenBundle:
        """Mesma computação de `screen`, com a sessão e os papéis fora do universo.

        O cabeçalho do relatório precisa de dois fatos que não cabem em
        `ScreenRow`: a sessão (para o nome do arquivo) e a lista de papéis **com
        métrica mas fora do corte de volume** (contrato §1: não entram no recorte
        e isso é dito em voz alta). Calcular isso aqui evita reler a janela de 20
        sessões duas vezes.

        Universo (§6.9, idêntico ao `build_liquidity`): mercado `010` (com `020`
        de fallback no dia em que o papel não tem lote padrão), janela das 20
        sessões do lake que terminam na data, média dividindo pelo número de
        sessões da janela (`len(window)`, até 20 — sessão sem negócio conta zero;
        janela curta no começo do lake divide pelo que existe) e corte de
        `VOLUME_MIN_CENTS` (R$ 5 M em centavos). Medido em 19/11/2026: **289**
        papéis, igual ao `prepare_state.universe`.

        O vínculo opção→papel é **pelo ISIN** (`isin_underlying` da opção contra
        a linha `010`/`020` do mesmo dia), como o pipeline faz: prefixo de
        ticker não identifica papel (PETR3 e PETR4 compartilham prefixo —
        AGENTS, armadilha 1).
        """
        lake = self.lake_calendar()
        if not lake:
            raise _missing_lake_failure(self.data_dir)
        if date is None:
            session = lake[-1]
        else:
            session = date
            if session not in lake:
                raise SourceFailure(
                    SourceError(
                        code="no_session",
                        message=f"sessão {session.isoformat()} não existe no lake",
                        hint=(
                            f"o lake tem {len(lake)} sessão(ões), de "
                            f"{lake[0].isoformat()} a {lake[-1].isoformat()} — use uma "
                            "data desse intervalo ou rode `b3o prepare`"
                        ),
                    )
                )
        window = [day for day in lake if day <= session][-DAYS_WINDOW:]

        volumes = self._screen_volumes(window)
        universe = sorted(
            ticker
            for ticker, total in volumes.items()
            if total // len(window) >= VOLUME_MIN_CENTS
        )
        spot_close, isin_to_ticker = self._screen_spots(session)
        option_map = self._screen_option_map(session, isin_to_ticker)
        options_by_paper = Counter(option_map.values())
        series_counts = self._screen_series_counts(session, option_map)
        metrics = self._screen_metrics(session)
        earnings, earnings_coverage_start = self._screen_next_earnings(session)
        model_version = (
            max(row.model_version for row in metrics.values())
            if metrics
            else load_ingest_state(self.data_dir).model_version
        )

        rows: list[ScreenRow] = []
        for paper in universe:
            metric = metrics.get(paper)
            earnings_date, earnings_kind = earnings.get(paper, (None, None))
            base = _screen_base_row(
                paper,
                session=session,
                model_version=model_version,
                spot_brl=spot_close[paper] / 100.0 if paper in spot_close else None,
                earnings=(earnings_date, earnings_kind),
            )
            if paper not in spot_close:
                rows.append(replace(base, exclusion="no_trade"))
                continue
            if options_by_paper.get(paper, 0) == 0:
                rows.append(replace(base, exclusion="no_options"))
                continue
            if metric is None:
                rows.append(replace(base, exclusion="no_metrics"))
                continue
            reason = _screen_empty_metric_reason(metric)
            if reason is not None:
                rows.append(replace(base, exclusion=reason))
                continue
            n_series, n_liquid, n_expiries = series_counts.get(paper, (0, 0, 0))
            rows.append(
                replace(
                    base,
                    status="ok",
                    exclusion=None,
                    spot_brl=(
                        None if metric.spot_cents is None else metric.spot_cents / 100.0
                    ),
                    iv_atm_30d=metric.iv_atm_30d,
                    iv_rank_252=metric.iv_rank_252,
                    iv_pct_252=metric.iv_pct_252,
                    rv20=metric.rv20,
                    rv30=metric.rv30,
                    rv60=metric.rv60,
                    vrp=metric.vrp,
                    vrp_z_252=metric.vrp_z_252,
                    rr25=metric.rr25,
                    bfly25=metric.bfly25,
                    ts_slope_30_90=metric.ts_slope_30_90,
                    exp_move_1s_30d=metric.exp_move_1s_30d,
                    implied_div_yield_30d=metric.implied_div_yield_30d,
                    forward_30d=(
                        None if metric.forward_cents is None else metric.forward_cents / 100.0
                    ),
                    days_to_liquidate=metric.days_to_liquidate,
                    rate_source=metric.rate_source,
                    n_series=n_series,
                    n_liquid_series=n_liquid,
                    n_expiries=n_expiries,
                    earnings_before_expiry="earnings_before_expiry" in metric.flags,
                    flags=list(metric.flags),
                )
            )
        return ScreenBundle(
            session=session,
            model_version=model_version,
            rows=rows,
            outside_universe=sorted(set(metrics) - set(universe)),
            earnings_coverage_start=earnings_coverage_start,
        )

    # ---------------------- leituras do F3 (MCP/API) --------------------- #

    def underlying(
        self,
        ticker: str,
        date: date | None = None,
        sections: Sequence[str] = ("summary", "term", "smile", "history"),
        expiry: date | None = None,
        days: int = 60,
    ) -> UnderlyingDetail:
        """Leitura de um papel em `sections` (`summary`/`term`/`smile`/`history`).

        Regras do contrato onda-i §4.1:

        - `summary` é **a mesma linha** de `screen_bundle` (o número do `screen`
          não pode divergir do da tool); papel fora do universo sai sem resumo,
          com nota — não desaparece;
        - `term` seleciona a IV ATM por `|Δ| ≈ 0,5` do que está gravado e
          reconstrói o forward por paridade com `derive.forward`; `exp_move` usa
          a constante da §6.10 (`derive.dist`). Nenhuma métrica é recalculada;
        - `smile` é uma linha por série com IV, ordenado por `(strike, side)`;
          `expiry=None` escolhe o vencimento de `t` mais próximo de 30/252;
        - `history` vem de `metrics_underlying`, só `date <= as_of_session`.

        Ticker que não é papel à vista nem tem métrica → `ticker_not_found`.
        """
        paper = _clean_ticker(ticker)
        session = self._session(date)
        self._require_underlying(paper)
        wanted = {str(section) for section in sections}
        notes: list[str] = []
        summary: ScreenRow | None = None
        in_universe = paper in load_prepare_state(self.data_dir).universe
        model_version = self.derived_model_version() or MODEL_VERSION

        if "summary" in wanted:
            bundle = self.screen_bundle(session)
            model_version = bundle.model_version
            summary = next((row for row in bundle.rows if row.underlying == paper), None)
            in_universe = summary is not None
            if summary is None:
                if paper in bundle.outside_universe:
                    notes.append(
                        f"{paper} tem métrica na sessão mas está fora do universo de "
                        "liquidez (volume médio 20d < R$ 5 M): não entra no screen"
                    )
                else:
                    notes.append(
                        f"{paper} não está no universo de liquidez da sessão e não tem "
                        "linha em metrics_underlying"
                    )

        rows = (
            self._chain_rows(paper, session) if {"term", "smile"} & wanted else []
        )
        spot = self._spot_brl(paper, session)
        if {"term", "smile"} & wanted and not rows:
            notes.append(
                "no_options: nenhuma série de opção do papel com métrica nesta sessão "
                "(confira o `chain` e o motivo no `screen`)"
            )

        term = _term_rows(rows, spot) if "term" in wanted else []
        if "term" in wanted and rows and not term:
            notes.append("term: nenhum vencimento com série utilizável nesta sessão")

        smile: list[SmileRow] = []
        smile_maturity: date | None = None
        if "smile" in wanted:
            smile_maturity = expiry or _nearest_expiry(rows)
            if smile_maturity is None:
                if rows:
                    notes.append("smile: nenhum vencimento com IV na sessão")
            else:
                available = {row.maturity for row in rows}
                if smile_maturity not in available:
                    raise SourceFailure(
                        SourceError(
                            code="expiry_not_found",
                            message=(
                                f"{paper} não tem série no vencimento "
                                f"{smile_maturity.isoformat()} em "
                                f"{session.isoformat()}"
                            ),
                            hint=(
                                "vencimentos com série: "
                                + _sample_sorted(available)
                                + " — use um deles ou omita `expiry`"
                            ),
                        )
                    )
                smile = _smile_rows(rows, smile_maturity)
                if not smile:
                    notes.append(
                        f"smile: o vencimento {smile_maturity.isoformat()} não tem "
                        "série com IV gravada"
                    )

        history = self._history_rows(paper, session, days) if "history" in wanted else []
        if "history" in wanted and not history:
            notes.append(
                "history: nenhuma linha em metrics_underlying até a sessão "
                "(rode `b3o metrics` se o lake tem a sessão)"
            )

        if model_version is None:
            model_version = MODEL_VERSION
        return UnderlyingDetail(
            ticker=paper,
            as_of_session=session,
            model_version=model_version,
            in_universe=in_universe,
            summary=summary,
            term=term,
            smile=smile,
            smile_maturity=smile_maturity,
            history=history,
            notes=notes,
        )

    def chain(
        self,
        ticker: str,
        expiry: date | None = None,
        date: date | None = None,
        delta_range: tuple[float, float] | None = (0.25, 0.75),
        liquidity: str = "strict",
        max_rows: int = 60,
    ) -> ChainResult:
        """Cadeia do papel na sessão, uma linha por série (contrato §4.2).

        `liquidity="strict"` usa o `is_liquid` **gravado** (§6.9); `"all"` não
        filtra. `delta_range` aplica a `|Δ|` (puts têm Δ negativo) e `None` não
        filtra; `expiry` desconhecido → `expiry_not_found`. O corte de
        `max_rows` é **depois** do filtro e pega as primeiras linhas da ordem
        `(maturity, strike, side)` — nunca "as melhores".
        """
        paper = _clean_ticker(ticker)
        session = self._session(date)
        self._require_underlying(paper)
        if liquidity not in ("strict", "all"):
            raise SourceFailure(
                SourceError(
                    code="bad_request",
                    message=f"liquidity inválido ({liquidity!r})",
                    hint="use liquidity=strict (regra §6.9 gravada) ou liquidity=all",
                )
            )
        rows = self._chain_rows(paper, session)
        notes: list[str] = []
        filtered = rows
        if not rows:
            notes.append(
                "no_options: nenhuma série do papel com métrica nesta sessão"
            )
        if expiry is not None:
            available = {row.maturity for row in rows}
            if expiry not in available:
                raise SourceFailure(
                    SourceError(
                        code="expiry_not_found",
                        message=(
                            f"{paper} não tem série no vencimento {expiry.isoformat()} "
                            f"em {session.isoformat()}"
                        ),
                        hint="vencimentos com série: " + _sample_sorted(available),
                    )
                )
            filtered = [row for row in filtered if row.maturity == expiry]
        if liquidity == "strict":
            filtered = [row for row in filtered if row.is_liquid]
        if delta_range is not None:
            low, high = delta_range
            filtered = [
                row
                for row in filtered
                if row.delta is not None and low <= abs(row.delta) <= high
            ]
        if rows and not filtered:
            notes.append(
                "filtros (liquidez/delta) não deixaram nenhuma série; use "
                "liquidity=all ou amplie delta_range para ver a grade"
            )
        total = len(filtered)
        limit = max(0, int(max_rows))
        return ChainResult(
            ticker=paper,
            as_of_session=session,
            model_version=self.derived_model_version() or MODEL_VERSION,
            rows=filtered[:limit],
            total=total,
            notes=notes,
        )

    def series(
        self, ticker: str, maturity: date, days: int = 60
    ) -> SeriesDetail:
        """Identidade, última linha e histórico de uma série (contrato §4.3).

        A sessão é a última do lake (a série é `(ticker, maturity)`). Ticker que
        não existe no lake → `ticker_not_found`; vencimento que não é da série →
        `maturity_not_found` com amostra; série sem métrica → `metrics_missing`.
        """
        code = _clean_ticker(ticker)
        session = self._session(None)
        paths = self._partitions_for("metrics_series", None)
        rows = (
            self._fetch(
                "SELECT refdate, mid, iv, delta, gamma, theta_brl, vega_brl, is_liquid,"
                " flags, side, style, strike, leg_id, t, model_version"
                " FROM read_parquet(?) WHERE ticker = ? AND maturity = ? AND refdate <= ?"
                " ORDER BY refdate",
                paths,
                code,
                maturity,
                session,
            )
            if paths
            else []
        )
        if not rows:
            self._series_failure(code, maturity, paths)
        last = rows[-1]

        def point(row: tuple) -> SeriesPoint:
            return SeriesPoint(
                date=row[0],
                close_brl=float(row[1]) / 100.0,
                mid_brl=float(row[1]) / 100.0,
                iv_pct=None if row[2] is None else float(row[2]),
                delta=None if row[3] is None else float(row[3]),
            )

        window = rows[-int(days) :] if days > 0 else []
        points = [point(row) for row in window]
        return SeriesDetail(
            ticker=code,
            maturity=maturity,
            side=str(last[9]),
            style=None if last[10] is None else str(last[10]),
            strike_brl=float(last[11]) / 100.0,
            leg_id=str(last[12]),
            t=float(last[13]),
            is_liquid=bool(last[7]),
            flags=[str(flag) for flag in (last[8] or ())],
            gamma=None if last[4] is None else float(last[4]),
            theta_brl=None if last[5] is None else float(last[5]),
            vega_brl=None if last[6] is None else float(last[6]),
            last=point(last),
            history=points,
            as_of_session=session,
            model_version=int(last[14]),
        )

    def events(self, ticker: str) -> EventsDetail:
        """Vencimentos, eventos de strike e resultados do papel (contrato §4.4)."""
        paper = _clean_ticker(ticker)
        session = self._session(None)
        self._require_underlying(paper)
        rows = self._chain_rows(paper, session)
        maturities = sorted({row.maturity for row in rows})
        strike_events = [
            StrikeEventRow(
                date=event.date,
                kind=event.kind,
                delta_strike=event.delta_strike,
                factor=event.factor,
                n_series=event.n_series,
                ambiguous=event.ambiguous,
            )
            for event in self.strike_events()
            if event.underlying == paper
        ]
        earnings_all = [
            EarningsEventRow(date=row.date, ref_period=row.ref_period, kind=row.kind)
            for row in self.earnings_rows()
            if row.ticker == paper
        ]
        earnings_all.sort(key=lambda row: (row.date, 0 if row.kind == "actual" else 1))
        next_earnings = next(
            (row for row in earnings_all if row.date >= session), None
        )
        notes: list[str] = []
        if not maturities:
            notes.append(
                f"maturities: nenhuma série de {paper} com métrica na sessão "
                f"{session.isoformat()}; o calendário e os eventos continuam válidos"
            )
        coverage_start = min((row.date for row in self.earnings_rows()), default=None)
        if not earnings_all:
            notes.append(
                f"earnings: nenhuma data de resultado para {paper} no calendário da CVM"
            )
        if coverage_start is not None and session < coverage_start:
            notes.append(
                f"calendário da CVM começa em {coverage_start.isoformat()}; a sessão "
                f"{session.isoformat()} é anterior — a ausência não é 'sem resultado'"
            )
        return EventsDetail(
            ticker=paper,
            as_of_session=session,
            model_version=self._model_version_for(),
            maturities=maturities,
            strike_events=strike_events,
            earnings=earnings_all,
            next_earnings=next_earnings,
            notes=notes,
        )

    # --------------------- internos das leituras do F3 -------------------- #

    def _session(self, date: date | None) -> date:
        """Sessão pedida (ou a última do lake); fora do lake → `no_session`."""
        lake = self.lake_calendar()
        if not lake:
            raise _missing_lake_failure(self.data_dir)
        if date is None:
            return lake[-1]
        if date not in lake:
            raise SourceFailure(
                SourceError(
                    code="no_session",
                    message=f"sessão {date.isoformat()} não existe no lake",
                    hint=(
                        f"o lake tem {len(lake)} sessão(ões), de "
                        f"{lake[0].isoformat()} a {lake[-1].isoformat()} — use uma "
                        "data desse intervalo ou rode `b3o prepare`"
                    ),
                )
            )
        return date

    def _require_underlying(self, ticker: str) -> None:
        """Papel existe como `010`/`020` (em qualquer ano) ou em `metrics_underlying`."""
        paths = self._partitions(None)
        if not paths:
            raise _missing_lake_failure(self.data_dir)
        rows = self._fetch(
            "SELECT 1 FROM read_parquet(?) WHERE ticker = ? AND tpmerc IN ('010', '020')"
            " LIMIT 1",
            paths,
            ticker,
        )
        if rows:
            return
        metrics = self._partitions_for("metrics_underlying", None)
        if metrics and self._fetch(
            "SELECT 1 FROM read_parquet(?) WHERE underlying = ? LIMIT 1",
            metrics,
            ticker,
        ):
            return
        raise SourceFailure(
            SourceError(
                code="ticker_not_found",
                message=f"{ticker} não é um papel do mercado à vista (010/020) no lake",
                hint=(
                    "confira o ticker (maiúsculas, sem sufixo); para série de opção use "
                    "`series`/`chain` com o ticker da série"
                ),
            )
        )

    def _chain_rows(self, ticker: str, session: date) -> list[ChainRow]:
        """Séries do papel na sessão: `metrics_series` + book/fechamento do raw.

        O vínculo opção→papel é pelo **ISIN** (armadilha 1 do AGENTS): o mesmo
        prefixo de ticker serve PETR3 e PETR4. Só entram séries com linha em
        `metrics_series` na sessão (a métrica é a fonte de IV/gregas/liquidez).
        """
        raw_paths = self._partitions(session.year)
        if not raw_paths:
            raise _missing_lake_failure(self.data_dir)
        metric_paths = self._partitions_for("metrics_series", session.year)
        if not metric_paths:
            return []
        sql = (
            "WITH spot AS ("
            "  SELECT ticker, isin_underlying,"
            "         row_number() OVER (PARTITION BY isin_underlying"
            "           ORDER BY CASE WHEN tpmerc = '010' THEN 0 ELSE 1 END) AS rn"
            "  FROM read_parquet($raw)"
            "  WHERE refdate = $day AND tpmerc IN ('010', '020')"
            "    AND isin_underlying <> ''"
            "), options AS ("
            "  SELECT DISTINCT ticker, isin_underlying FROM read_parquet($raw)"
            "  WHERE refdate = $day AND tpmerc IN ('070', '080')"
            "    AND isin_underlying <> ''"
            "), mapped AS ("
            "  SELECT o.ticker FROM options o JOIN spot s"
            "    ON s.isin_underlying = o.isin_underlying AND s.rn = 1"
            "  WHERE s.ticker = $ticker"
            "), bars AS ("
            "  SELECT ticker, best_bid, best_ask, close, quote_factor,"
            "         row_number() OVER (PARTITION BY ticker ORDER BY volume DESC) AS rn"
            "  FROM read_parquet($raw)"
            "  WHERE refdate = $day AND tpmerc IN ('070', '080')"
            ")"
            " SELECT m.ticker, m.maturity, m.side, m.style, m.strike, m.t, m.mid, m.iv,"
            "        m.delta, m.gamma, m.theta_brl, m.vega_brl, m.is_liquid, m.flags,"
            "        m.model_version, b.best_bid, b.best_ask, b.close, b.quote_factor"
            " FROM read_parquet($metrics) m"
            " JOIN mapped p ON p.ticker = m.ticker"
            " LEFT JOIN bars b ON b.ticker = m.ticker AND b.rn = 1"
            " WHERE m.refdate = $day"
            " ORDER BY m.maturity, m.strike, m.side"
        )
        rows = self._fetch_named(
            sql,
            {
                "raw": [str(path) for path in raw_paths],
                "metrics": [str(path) for path in metric_paths],
                "day": session,
                "ticker": ticker,
            },
        )
        result: list[ChainRow] = []
        for row in rows:
            factor = int(row[18]) if row[18] else 1
            # `best_bid/ask == 0` no lake significa "sem oferta" (é o que a regra
            # §6.9 usa). Expor 0,0 sugere "oferta a zero": ausência vira `None`.
            bid = (
                None
                if row[15] is None or int(row[15]) <= 0
                else float(row[15]) / factor / 100.0
            )
            ask = (
                None
                if row[16] is None or int(row[16]) <= 0
                else float(row[16]) / factor / 100.0
            )
            close = None if row[17] is None else float(row[17]) / factor / 100.0
            spread_brl = None
            spread_pct = None
            if bid is not None and ask is not None and bid > 0 and ask > 0:
                spread_brl = ask - bid
                mid_book = (bid + ask) / 2.0
                if mid_book > 0:
                    spread_pct = (ask - bid) / mid_book * 100.0
            result.append(
                ChainRow(
                    ticker=str(row[0]),
                    side=str(row[2]),
                    style=None if row[3] is None else str(row[3]),
                    maturity=row[1],
                    strike_brl=float(row[4]) / 100.0,
                    t=float(row[5]),
                    bid_brl=bid,
                    ask_brl=ask,
                    mid_brl=float(row[6]) / 100.0,
                    close_brl=close,
                    iv_pct=None if row[7] is None else float(row[7]),
                    delta=None if row[8] is None else float(row[8]),
                    gamma=None if row[9] is None else float(row[9]),
                    theta_brl=None if row[10] is None else float(row[10]),
                    vega_brl=None if row[11] is None else float(row[11]),
                    spread_brl=spread_brl,
                    spread_pct=spread_pct,
                    is_liquid=bool(row[12]),
                    flags=[str(flag) for flag in (row[13] or ())],
                )
            )
        return result

    def _spot_brl(self, ticker: str, session: date) -> float | None:
        """Fechamento do papel em R$ (mercado `010` antes de `020`), se houver."""
        paths = self._partitions(session.year)
        if not paths:
            return None
        rows = self._fetch(
            "SELECT close FROM read_parquet(?) WHERE refdate = ? AND ticker = ?"
            " AND tpmerc IN ('010', '020')"
            " ORDER BY CASE WHEN tpmerc = '010' THEN 0 ELSE 1 END LIMIT 1",
            paths,
            session,
            ticker,
        )
        return float(rows[0][0]) / 100.0 if rows else None

    def _history_rows(
        self, ticker: str, session: date, days: int
    ) -> list[HistoryRow]:
        """Últimas `days` linhas de `metrics_underlying` até a sessão (crescente)."""
        paths = self._partitions_for("metrics_underlying", None)
        if not paths or days <= 0:
            return []
        rows = self._fetch(
            "SELECT refdate, iv_atm_30d, rv30, vrp, vrp_z_252, rr25"
            " FROM read_parquet(?) WHERE underlying = ? AND refdate <= ?"
            " ORDER BY refdate",
            paths,
            ticker,
            session,
        )
        return [
            HistoryRow(
                date=row[0],
                iv_atm_30d=None if row[1] is None else float(row[1]),
                rv30=None if row[2] is None else float(row[2]),
                vrp=None if row[3] is None else float(row[3]),
                vrp_z_252=None if row[4] is None else float(row[4]),
                rr25=None if row[5] is None else float(row[5]),
            )
            for row in rows[-int(days) :]
        ]

    def _model_version_for(self) -> int:
        """Maior versão das tabelas derivadas; sem tabela, a constante do código."""
        return self.derived_model_version() or MODEL_VERSION

    def _series_failure(self, ticker: str, maturity: date, paths: Sequence[Path]) -> None:
        """Classifica a ausência da série: ticker, vencimento ou métrica faltando."""
        any_rows = (
            self._fetch(
                "SELECT DISTINCT maturity FROM read_parquet(?) WHERE ticker = ?"
                " ORDER BY maturity",
                list(paths),
                ticker,
            )
            if paths
            else []
        )
        if any_rows:
            available = [row[0] for row in any_rows]
            raise SourceFailure(
                SourceError(
                    code="maturity_not_found",
                    message=(
                        f"{ticker} não tem série no vencimento {maturity.isoformat()} "
                        "em metrics_series"
                    ),
                    hint="vencimentos disponíveis: " + _sample_sorted(set(available)),
                )
            )
        raw_paths = self._partitions(None)
        exists = bool(
            raw_paths
            and self._fetch(
                "SELECT 1 FROM read_parquet(?) WHERE ticker = ?"
                " AND tpmerc IN ('070', '080') LIMIT 1",
                raw_paths,
                ticker,
            )
        )
        if exists:
            raise SourceFailure(
                SourceError(
                    code="metrics_missing",
                    message=(
                        f"a série {ticker} existe no lake, mas não tem linha em "
                        "metrics_series para o vencimento pedido"
                    ),
                    hint="rode `b3o metrics --date <sessão>` (sem métrica não há IV/gregas)",
                )
            )
        raise SourceFailure(
            SourceError(
                code="ticker_not_found",
                message=f"{ticker} não existe como série de opção (070/080) no lake",
                hint="confira o ticker (maiúsculas) e o vencimento; para papel use `chain`",
            )
        )

    def _screen_volumes(self, window: Sequence[date]) -> dict[str, int]:
        """`ticker -> volume financeiro somado` na janela (centavos, `010` antes de `020`)."""
        if not window:
            return {}
        paths = [
            path
            for year in range(window[0].year, window[-1].year + 1)
            for path in self._partitions(year)
        ]
        if not paths:
            raise _missing_lake_failure(self.data_dir)
        rows = self._fetch(
            "WITH bars AS ("
            "  SELECT refdate, ticker, volume,"
            "         row_number() OVER (PARTITION BY refdate, isin_underlying"
            "                            ORDER BY CASE WHEN tpmerc = '010' THEN 0 ELSE 1 END)"
            "           AS rn"
            "  FROM read_parquet(?)"
            "  WHERE tpmerc IN ('010', '020') AND refdate BETWEEN ? AND ?"
            "    AND isin_underlying <> ''"
            ") SELECT ticker, sum(volume) FROM bars WHERE rn = 1 GROUP BY 1",
            paths,
            window[0],
            window[-1],
        )
        return {str(ticker): int(total) for ticker, total in rows}

    def _screen_spots(self, session: date) -> tuple[dict[str, int], dict[str, str]]:
        """`ticker -> close` e `isin -> ticker` do dia (`010` antes de `020`)."""
        paths = self._partitions(session.year)
        if not paths:
            raise _missing_lake_failure(self.data_dir)
        rows = self._fetch(
            "WITH spot AS ("
            "  SELECT ticker, close, isin_underlying,"
            "         row_number() OVER (PARTITION BY isin_underlying"
            "                            ORDER BY CASE WHEN tpmerc = '010' THEN 0 ELSE 1 END)"
            "           AS rn"
            "  FROM read_parquet(?)"
            "  WHERE refdate = ? AND tpmerc IN ('010', '020')"
            "    AND isin_underlying <> ''"
            ") SELECT ticker, close, isin_underlying FROM spot WHERE rn = 1",
            paths,
            session,
        )
        close_by_ticker = {str(ticker): int(close) for ticker, close, _isin in rows}
        ticker_by_isin = {str(isin): str(ticker) for ticker, _close, isin in rows}
        return close_by_ticker, ticker_by_isin

    def _screen_option_map(
        self, session: date, ticker_by_isin: Mapping[str, str]
    ) -> dict[str, str]:
        """`ticker da opção -> papel`, resolvido **pelo ISIN** (nunca pelo prefixo)."""
        paths = self._partitions(session.year)
        if not paths:
            raise _missing_lake_failure(self.data_dir)
        rows = self._fetch(
            "SELECT DISTINCT ticker, isin_underlying FROM read_parquet(?)"
            " WHERE refdate = ? AND tpmerc IN ('070', '080')"
            "   AND isin_underlying <> ''",
            paths,
            session,
        )
        mapping: dict[str, str] = {}
        for ticker, isin in rows:
            paper = ticker_by_isin.get(str(isin))
            if paper is not None:
                mapping[str(ticker)] = paper
        return mapping

    def _screen_series_counts(
        self, session: date, option_map: Mapping[str, str]
    ) -> dict[str, tuple[int, int, int]]:
        """`papel -> (séries, séries líquidas, vencimentos)` de `metrics_series`."""
        paths = self._partitions_for("metrics_series", session.year)
        if not paths:
            return {}
        rows = self._fetch(
            "SELECT ticker, maturity, is_liquid FROM read_parquet(?) WHERE refdate = ?",
            paths,
            session,
        )
        counts: dict[str, list[int]] = {}
        expiries: dict[str, set[date]] = defaultdict(set)
        for ticker, maturity, is_liquid in rows:
            paper = option_map.get(str(ticker))
            if paper is None:
                continue
            bucket = counts.setdefault(paper, [0, 0])
            bucket[0] += 1
            if is_liquid:
                bucket[1] += 1
            expiries[paper].add(maturity)
        return {
            paper: (bucket[0], bucket[1], len(expiries[paper]))
            for paper, bucket in counts.items()
        }

    def _screen_metrics(self, session: date) -> dict[str, _ScreenMetrics]:
        """Linhas de `metrics_underlying` da sessão, indexadas por papel."""
        paths = self._partitions_for("metrics_underlying", session.year)
        if not paths:
            return {}
        rows = self._fetch(
            "SELECT underlying, spot, iv_atm_30d, iv_rank_252, iv_pct_252, rv20, rv30,"
            " rv60, vrp, vrp_z_252, rr25, bfly25, ts_slope_30_90, exp_move_1s_30d,"
            " implied_div_yield_30d, days_to_liquidate, forward_30d, rate_source,"
            " flags, model_version FROM read_parquet(?) WHERE refdate = ?",
            paths,
            session,
        )
        metrics: dict[str, _ScreenMetrics] = {}
        for row in rows:
            metrics[str(row[0])] = _ScreenMetrics(
                underlying=str(row[0]),
                spot_cents=_float_or_none(row[1]),
                iv_atm_30d=_float_or_none(row[2]),
                iv_rank_252=_float_or_none(row[3]),
                iv_pct_252=_float_or_none(row[4]),
                rv20=_float_or_none(row[5]),
                rv30=_float_or_none(row[6]),
                rv60=_float_or_none(row[7]),
                vrp=_float_or_none(row[8]),
                vrp_z_252=_float_or_none(row[9]),
                rr25=_float_or_none(row[10]),
                bfly25=_float_or_none(row[11]),
                ts_slope_30_90=_float_or_none(row[12]),
                exp_move_1s_30d=_float_or_none(row[13]),
                implied_div_yield_30d=_float_or_none(row[14]),
                days_to_liquidate=_float_or_none(row[15]),
                forward_cents=_float_or_none(row[16]),
                rate_source=None if row[17] is None else str(row[17]),
                flags=tuple(str(flag) for flag in (row[18] or ())),
                model_version=int(row[19]),
            )
        return metrics

    def _screen_next_earnings(
        self, session: date
    ) -> tuple[dict[str, tuple[date, str]], date | None]:
        """Próximo resultado **≥ sessão** por papel (`actual` ganha empate de data).

        Devolve também a **primeira data** do calendário no lake. Sessão anterior
        a ela não tem "próximo resultado" que o dado sustente: o mapa sai vazio e
        o relatório avisa, em vez de mostrar o primeiro documento da CVM como se
        fosse o resultado seguinte daquele papel (P-N).
        """
        rows = self.earnings_rows()
        coverage_start = min((row.date for row in rows), default=None)
        if coverage_start is None or session < coverage_start:
            return {}, coverage_start
        next_by_ticker: dict[str, tuple[tuple[date, int], date, str]] = {}
        for row in rows:
            if row.date < session:
                continue
            key = (row.date, 0 if row.kind == "actual" else 1)
            current = next_by_ticker.get(row.ticker)
            if current is None or key < current[0]:
                next_by_ticker[row.ticker] = (key, row.date, row.kind)
        return (
            {ticker: (value[1], value[2]) for ticker, value in next_by_ticker.items()},
            coverage_start,
        )

    # ------------------------------ health ------------------------------ #

    def health(self) -> Health:
        """Monta o `Health`: cada leitura falha no próprio item, nunca no todo."""
        prepare = load_prepare_state(self.data_dir)

        try:
            state: IngestState | None = load_ingest_state(self.data_dir)
        except SourceFailure as exc:
            log.warning("health: ingest_state ilegível: %s", exc.message)
            state = None
            state_error = exc.error
        else:
            state_error = None

        try:
            paths = self._partitions(None)
            counts = self.counts_by_date(None) if paths else {}
        except SourceFailure as exc:
            counts = {}
            lake_error: dict[str, object] = _error_item(exc)
        else:
            lake_error = {} if paths else _missing_lake_item(self.data_dir)

        sessions = sorted(counts)
        last_session = sessions[-1] if sessions else None
        counts_by_date: object
        if lake_error:
            counts_by_date = lake_error
            lake_sessions: object = lake_error
            lake_years: object = lake_error
            last_session_value: str | None = None
        else:
            counts_by_date = {
                day.isoformat(): count for day, count in list(counts.items())[-5:]
            }
            lake_sessions = len(sessions)
            lake_years = self._years()
            last_session_value = last_session.isoformat() if last_session else None

        age_sessions = self._age_sessions(state, sessions, state_error)
        expected = _expected_last_session(date.today())
        counts_by_market, discarded = self._markets(prepare)
        skipped_lines = self._skipped(state, state_error)
        rate_source = self._rate_source()
        rates_sessions = self._rates_sessions(sessions)
        oi_dates, oi_unavailable = self._open_interest(last_session)
        earnings_coverage = self._earnings_coverage(prepare, last_session)
        unexplained_gap = self._unexplained_gap(sessions)
        model_version, ingest_version, mismatch, source, note = self._model_versions(state)

        return Health(
            last_session=last_session_value,
            age_sessions=age_sessions,
            expected_last_session=expected.isoformat() if expected else None,
            lake_sessions=lake_sessions,
            lake_years=lake_years,
            counts_by_date=counts_by_date,
            counts_by_market=counts_by_market,
            discarded_by_market=discarded,
            skipped_lines=skipped_lines,
            rate_source=rate_source,
            rates_sessions=rates_sessions,
            oi_available_dates=oi_dates,
            oi_unavailable=oi_unavailable,
            earnings_coverage=earnings_coverage,
            universe=self._universe(prepare),
            unexplained_gap=unexplained_gap,
            last_error=prepare.last_error,
            rss_peak_mib=prepare.rss_peak_mib,
            model_version=model_version,
            ingest_model_version=ingest_version,
            model_version_mismatch=mismatch,
            model_version_source=source,
            model_version_note=note,
            as_of=(
                state.updated_at.isoformat()
                if state is not None and state.updated_at is not None
                else None
            ),
        )

    def _model_versions(
        self, state: IngestState | None
    ) -> tuple[int, int, bool, str, str | None]:
        """`(model_version, ingest_model_version, mismatch, source, note)` (F3.0).

        A verdade do cálculo é a **tabela derivada**: o `ingest_state` é o
        retrato do último `prepare` e pode estar defasado (medido em 23/11/2026:
        `ingest_state` em 1 e `metrics_underlying` em 2). Sem tabela derivada, o
        fallback para o `ingest_state` é explícito em `source`/`note` — nunca um
        `2` que ninguém calculou nem um `1` silencioso.
        """
        ingest = state.model_version if state is not None else MODEL_VERSION
        try:
            derived = self.derived_model_version()
        except SourceFailure as exc:
            return (
                ingest,
                ingest,
                False,
                "ingest_state",
                f"não foi possível ler as tabelas de métrica ({exc.code}): "
                f"{exc.message}; usando o ingest_state",
            )
        if derived is None:
            return (
                ingest,
                ingest,
                False,
                "ingest_state",
                "nenhuma tabela de métrica gravada: a versão é a do ingest_state",
            )
        if derived != ingest:
            return (
                derived,
                ingest,
                True,
                "metrics",
                (
                    f"o ingest_state diz model_version {ingest}, mas as métricas estão "
                    f"em {derived} — o health reporta a versão das métricas"
                ),
            )
        return derived, ingest, False, "metrics", None


    # ----------------------------- internos ----------------------------- #

    def _partitions(self, year: int | None) -> list[Path]:
        """Partições do COTAHIST (um ano ou todos), caminho canônico do `store`."""
        if year is not None:
            path = cotahist_partition_path(self.data_dir, year)
            return [path] if path.is_file() else []
        found: list[Path] = []
        root = self.data_dir / "raw_cotahist"
        if not root.is_dir():
            return []
        for directory in sorted(root.glob("year=*")):
            path = directory / "part-0.parquet"
            if path.is_file():
                found.append(path)
        return found

    def _partitions_for(self, table: str, year: int | None) -> list[Path]:
        """Partições `data/<table>/year=*/part-0.parquet` (um ano ou todos)."""
        root = self.data_dir / table
        if year is not None:
            path = root / f"year={int(year):04d}" / "part-0.parquet"
            return [path] if path.is_file() else []
        if not root.is_dir():
            return []
        return [
            directory / "part-0.parquet"
            for directory in sorted(root.glob("year=*"))
            if (directory / "part-0.parquet").is_file()
        ]

    def _legs_partitions(self, year: int) -> list[Path]:
        """Partições de `legs` do ano (identidade de leg por `(ticker, refdate)`)."""
        return self._partitions_for("legs", year)

    def _event_partitions(self) -> list[Path]:
        """Partições de `strike_events` de todos os anos (tabela pequena)."""
        return self._partitions_for("strike_events", None)

    def _first_year(self, end: date) -> int:
        """Primeiro ano do lake até `end` (ou o próprio `end` se o lake for vazio)."""
        years = [year for year in self._years() if year <= end.year]
        return min(years) if years else end.year

    def _years(self) -> list[int]:
        years: list[int] = []
        root = self.data_dir / "raw_cotahist"
        for directory in sorted(root.glob("year=*")) if root.is_dir() else []:
            try:
                years.append(int(directory.name.split("=", 1)[1]))
            except (IndexError, ValueError):
                log.warning("health: partição com nome inesperado ignorada: %s", directory)
        return sorted(years)

    def _oi_partitions(self, year: int | None) -> list[Path]:
        """Partições de `open_interest` do ano (ou de todos os anos)."""
        root = self.data_dir / "open_interest"
        if not root.is_dir():
            return []
        if year is not None:
            path = root / f"year={year:04d}" / "part-0.parquet"
            return [path] if path.is_file() else []
        found: list[Path] = []
        for directory in sorted(root.glob("year=*")):
            path = directory / "part-0.parquet"
            if path.is_file():
                found.append(path)
        return found

    def _fetch(self, sql: str, paths: list[Path], *extra: object) -> list[tuple]:
        """Executa uma consulta DuckDB sobre os Parquets; erro vira `storage_error`."""
        params: list[object] = [[str(path) for path in paths], *extra]
        try:
            with duckdb.connect() as connection:
                return connection.execute(sql, params).fetchall()
        except duckdb.Error as exc:
            raise SourceFailure(
                SourceError(
                    code="storage_error",
                    message=f"falha ao consultar o lake: {exc}",
                    hint=(
                        "partição corrompida ou schema antigo: rode `b3o prepare` de novo; "
                        "se persistir, apague a partição do ano"
                    ),
                )
            ) from exc

    def _fetch_named(self, sql: str, params: Mapping[str, object]) -> list[tuple]:
        """Executa SQL com parâmetros **nomeados** (`$nome`) — inclusive listas de caminho.

        `_fetch` (posicional) basta para uma lista de caminhos por consulta. Quando
        a consulta lê **duas tabelas diferentes** (`read_parquet($raw)` ×
        `read_parquet($legs)`), o binding posicional do DuckDB entrega a primeira
        lista às duas (medido 22/11/2026: a CTE de `legs` recebia o Parquet do
        COTAHIST e falhava em `leg_id`); nomear o parâmetro resolve e continua
        sendo SQL parametrizado — nada de caminho por f-string.
        """
        try:
            with duckdb.connect() as connection:
                return connection.execute(sql, dict(params)).fetchall()
        except duckdb.Error as exc:
            raise SourceFailure(
                SourceError(
                    code="storage_error",
                    message=f"falha ao consultar o lake: {exc}",
                    hint=(
                        "partição corrompida ou schema antigo: rode `b3o prepare` de novo; "
                        "se persistir, apague a partição do ano"
                    ),
                )
            ) from exc

    def _age_sessions(
        self,
        state: IngestState | None,
        sessions: list[date],
        state_error: SourceError | None,
        *,
        today: date | None = None,
    ) -> object:
        """Idade em pregões: quantas sessões o lake está **atrás do esperado**.

        O `last_session` do `ingest_state` é, por construção, a maior data do lake
        — comparar o estado com o próprio lake devolve **0 sempre** (defeito
        achado na revisão do F0: o `health` não acusaria fonte parada). O
        esperado é a última sessão **estritamente antes de hoje** no calendário
        da B3 (dia útil menos feriado, ADR-015), porque o arquivo é **D-1**: a
        sessão de hoje só é exigível amanhã.

        `today` existe para a borda injetar a data (o relógio aqui mede
        defasagem, não é a data de referência do dado — P-C continua valendo).
        Sem `today`, usa o relógio local do processo.
        """
        if state_error is not None:
            return _error_item(state_error)
        if not sessions:
            return _missing_lake_item(self.data_dir)
        if state is None or state.last_session is None:
            return len(sessions)
        reference = today or date.today()
        expected = _expected_last_session(reference)
        if state.last_session >= expected:
            return 0
        behind = [day for day in sessions if state.last_session < day <= expected]
        # Sessões esperadas que nem existem no lake também contam: a idade é a
        # distância no calendário da B3, não o que por acaso foi baixado.
        expected_days = trading_sessions(
            state.last_session + timedelta(days=1), expected, known=tuple(sessions)
        )
        return max(len(behind), len(expected_days))

    def _markets(self, prepare: PrepareState) -> tuple[object, object]:
        """Contagens por mercado (D3): tudo do sidecar, separando os descartados."""
        if not prepare.counts_by_market:
            missing = _error_item(
                SourceError(
                    code="prepare_state_missing",
                    message="prepare_state.json sem counts_by_market",
                    hint=_HINT_PREPARE,
                )
            )
            return missing, None
        latest = max(prepare.counts_by_market)
        markets = prepare.counts_by_market[latest]
        discarded = {
            market: count for market, count in markets.items() if market not in KEPT_MARKETS
        }
        return prepare.counts_by_market, discarded

    def _skipped(self, state: IngestState | None, state_error: SourceError | None) -> object:
        """Linhas puladas do `ingest_state` (ADR-007); estado ausente é erro no item."""
        if state_error is not None:
            return _error_item(state_error)
        if state is None or state.updated_at is None:
            return _error_item(
                SourceError(
                    code="state_missing",
                    message=f"ingest_state.json ausente em {self.data_dir}",
                    hint=_HINT_PREPARE,
                )
            )
        return state.skipped_lines.to_dict()

    def _rates_sessions(self, sessions: list[date]) -> object:
        """Quantas sessões do lake têm curva (`rates_di`), de qualquer fonte.

        `0` sem partição de taxas é a verdade contável — a ausência da tabela é
        reportada em `rate_source` como `rates_missing`, não aqui.
        """
        paths = self._partitions_for("rates_di", None)
        if not paths or not sessions:
            return 0
        try:
            rows = self._fetch("SELECT DISTINCT refdate FROM read_parquet(?)", paths)
        except SourceFailure as exc:
            return _error_item(exc)
        with_curve = {row[0] for row in rows}
        return sum(1 for day in sessions if day in with_curve)

    def _unexplained_gap(self, sessions: list[date]) -> object:
        """Overnights > 40% sem evento do B2, com piso de preço e continuidade.

        Três filtros, todos medidos no lake de 2026 (AGENTS): `|r| > 40%`
        (log, `ln(open/prev_close)`) sozinho dá 428 casos em 312 tickers e
        apenas 3 têm evento; fechamento anterior ≥ R$ 1,00 corta 67 casos de
        centavos (`2 ¢ → 3 ¢` = +40,5%); e exigir barra na sessão anterior **do
        calendário do lake** corta lacuna de negociação (papel parado 3 meses não
        é gap de preço). O que sobra é comparado com `strike_events` do papel por
        data exata: **qualquer** `kind` explica o gap (`indeterminate` também é
        evento real — AGENTS; o `kind` diz a classe, não se houve evento).
        """
        if not sessions:
            return None  # lake ausente/ilegível já é erro nos outros campos
        paths = self._partitions(None)
        sql = (
            "WITH cal AS ("
            "  SELECT refdate, row_number() OVER (ORDER BY refdate) AS idx"
            "  FROM (SELECT DISTINCT refdate FROM read_parquet($raw))"
            "), bars AS ("
            "  SELECT s.ticker AS ticker, s.refdate AS refdate, s.open AS open,"
            "         s.close AS close, s.trades AS trades, c.idx AS idx"
            "  FROM read_parquet($raw) s JOIN cal c ON c.refdate = s.refdate"
            "  WHERE s.tpmerc = '010'"
            "), lagged AS ("
            "  SELECT ticker, refdate, idx, open, trades,"
            "         lag(close) OVER w AS prev_close,"
            "         lag(trades) OVER w AS prev_trades,"
            "         lag(idx) OVER w AS prev_idx"
            "  FROM bars WINDOW w AS (PARTITION BY ticker ORDER BY refdate)"
            ")"
            " SELECT ticker, refdate, ln(open / prev_close) AS overnight"
            " FROM lagged"
            " WHERE prev_close >= 100 AND prev_trades > 0 AND prev_idx = idx - 1"
            "   AND open > 0 AND abs(ln(open / prev_close)) > $threshold"
            " ORDER BY refdate, ticker"
        )
        try:
            rows = self._fetch_named(
                sql,
                {
                    "raw": [str(path) for path in paths],
                    "threshold": DEFAULT_GAP_THRESHOLD,
                },
            )
        except SourceFailure as exc:
            return _error_item(exc)
        if not rows:
            return None

        candidate_years = {row[1].year for row in rows}
        event_years = self._event_years()
        missing = sorted(candidate_years - event_years)
        if missing:
            return _error_item(
                SourceError(
                    code="events_missing",
                    message=(
                        f"ano(s) {missing} têm overnight > 40% mas nenhuma partição de "
                        "strike_events: não dá para separar gap explicado de inexplicado"
                    ),
                    hint=(
                        "rode `b3o prepare` para o ano; sem os eventos do B2 o health "
                        "não julga o gap (e não inventa 'explicado')"
                    ),
                )
            )
        try:
            events = self.strike_events()
        except SourceFailure as exc:
            return _error_item(exc)

        event_dates: dict[str, set[date]] = defaultdict(set)
        for event in events:
            event_dates[event.underlying].add(event.date)

        returns_by_ticker: dict[str, list[tuple[date, float]]] = defaultdict(list)
        for ticker, refdate, overnight in rows:
            returns_by_ticker[str(ticker)].append((refdate, float(overnight)))

        flagged: list[tuple[date, str, float]] = []
        for ticker, returns in returns_by_ticker.items():
            values = dict(returns)
            for day in flag_unexplained(
                returns, event_dates.get(ticker, set()), threshold=DEFAULT_GAP_THRESHOLD
            ):
                flagged.append((day, ticker, values[day]))
        if not flagged:
            return None
        flagged.sort()
        return {
            "count": len(flagged),
            "sample": [
                {"ticker": ticker, "date": day.isoformat(), "overnight": round(value, 6)}
                for day, ticker, value in flagged[:20]
            ],
        }

    def _event_years(self) -> set[int]:
        """Anos com partição de `strike_events` gravada (o prepare grava mesmo vazia)."""
        years = {
            year
            for path in self._partitions_for("strike_events", None)
            if (year := _partition_year(path)) is not None
        }
        return years

    def _rate_source(self) -> object:
        """Último dia da curva DI: `{refdate, source, nodes}` (P-M)."""
        paths = sorted((self.data_dir / "rates_di").glob("year=*/part-0.parquet"))
        if not paths:
            return _error_item(
                SourceError(
                    code="rates_missing",
                    message=f"nenhuma curva DI em {self.data_dir / 'rates_di'}",
                    hint=_HINT_PREPARE,
                )
            )
        try:
            rows = self._fetch(
                "SELECT refdate, source, count(*) FROM read_parquet(?) "
                "GROUP BY 1, 2 ORDER BY refdate DESC LIMIT 1",
                paths,
            )
        except SourceFailure as exc:
            return _error_item(exc)
        if not rows:
            return _error_item(
                SourceError(
                    code="rates_missing",
                    message="partição de taxas existe mas está vazia",
                    hint=_HINT_PREPARE,
                )
            )
        refdate, source, nodes = rows[0]
        return {
            "refdate": refdate.isoformat() if isinstance(refdate, date) else str(refdate),
            "source": str(source),
            "nodes": int(nodes),
        }

    def _open_interest(self, last_session: date | None) -> tuple[object, object]:
        """Datas com OI gravado e a flag `oi_unavailable` da última sessão (P-N)."""
        paths = sorted((self.data_dir / "open_interest").glob("year=*/part-0.parquet"))
        if not paths:
            return (
                _error_item(
                    SourceError(
                        code="oi_missing",
                        message=f"nenhuma posição em aberto em {self.data_dir / 'open_interest'}",
                        hint=_HINT_PREPARE,
                    )
                ),
                True,
            )
        try:
            rows = self._fetch("SELECT DISTINCT refdate FROM read_parquet(?) ORDER BY 1", paths)
        except SourceFailure as exc:
            return _error_item(exc), True
        dates = [row[0] for row in rows]
        available = [day.isoformat() for day in dates]
        unavailable = last_session is None or last_session not in set(dates)
        return available, unavailable

    def _earnings_coverage(
        self, prepare: PrepareState, last_session: date | None
    ) -> object:
        """Calendário × universo: quantos papéis do universo têm data mapeada (§6.14)."""
        paths = sorted((self.data_dir / "earnings").glob("*.parquet"))
        if not paths:
            return _error_item(
                SourceError(
                    code="earnings_missing",
                    message=f"nenhum calendário em {self.data_dir / 'earnings'}",
                    hint=_HINT_PREPARE,
                )
            )
        if not prepare.universe:
            return _error_item(
                SourceError(
                    code="universe_missing",
                    message="prepare_state.json sem universo de liquidez",
                    hint=_HINT_PREPARE,
                )
            )
        try:
            rows = self._fetch(
                "SELECT DISTINCT ticker FROM read_parquet(?)",
                paths,
            )
        except SourceFailure as exc:
            return _error_item(exc)
        mapped = {str(row[0]) for row in rows}
        universe = set(prepare.universe)
        missing = sorted(universe - mapped)
        result: dict[str, object] = {
            "universe": len(universe),
            "mapped": len(universe & mapped),
            "unmapped_count": len(missing),
            "unmapped_sample": missing[:10],  # amostra: a lista completa está no sidecar
            "rows": self._earnings_rows(paths),
        }
        tradable = [str(item) for item in self._tradable(prepare)]
        if tradable:
            result["tradable"] = {
                "count": len(tradable),
                "mapped": len(set(tradable) & mapped),
            }
        return result

    def _earnings_rows(self, paths: list[Path]) -> object:
        try:
            rows = self._fetch("SELECT count(*) FROM read_parquet(?)", paths)
        except SourceFailure as exc:
            return _error_item(exc)
        return int(rows[0][0]) if rows else 0

    def _universe(self, prepare: PrepareState) -> object:
        if not prepare.universe:
            return _error_item(
                SourceError(
                    code="universe_missing",
                    message="prepare_state.json sem universo de liquidez",
                    hint=_HINT_PREPARE,
                )
            )
        return {
            "session": prepare.universe_session.isoformat()
            if prepare.universe_session
            else None,
            "count": len(prepare.universe),
            "tradable_count": len(self._tradable(prepare)),
        }

    @staticmethod
    def _tradable(prepare: PrepareState) -> tuple[str, ...]:
        """Papéis com ao menos 1 dia de grade líquida (subconjunto de `universe`).

        O corte do universo é só de volume (§6.9), então ele inclui ETF, unit e BDR
        sem opção; este subconjunto é o que tem mercado de opções de verdade e é o
        número que interessa ao funil do F2.
        """
        liquidity = prepare.liquidity or {}
        raw = liquidity.get("tradable")
        if not isinstance(raw, list):
            return ()
        return tuple(str(item) for item in raw)


def _expected_last_session(today: date) -> date | None:
    """Última sessão exigível **estritamente antes de hoje** (calendário B3, ADR-015).

    O COTAHIST do ano corrente vem como D-1: a sessão de hoje só é exigível
    amanhã. Devolve `None` se o calendário não tiver sessão no intervalo (não
    deve acontecer; falha explícita em vez de data inventada).
    """
    limit = today - timedelta(days=1)
    if limit.year < 2000:
        return None
    sessions = trading_sessions(date(limit.year - 1, 12, 1), limit)
    return sessions[-1] if sessions else None


def _missing_lake_item(data_dir: Path) -> dict[str, object]:
    """Erro explícito de lake ausente (nunca "0 sessões" como se fosse verdade)."""
    return _error_item(_missing_lake_failure(data_dir))


def _missing_lake_failure(data_dir: Path) -> SourceFailure:
    """Mesmo erro de lake ausente, como `SourceFailure` (para `raise`, não para o health)."""
    return SourceFailure(
        SourceError(
            code="lake_missing",
            message=f"nenhuma partição do COTAHIST em {Path(data_dir) / 'raw_cotahist'}",
            hint=_HINT_DATA_DIR,
        )
    )


_GRID_REASONS: tuple[str, ...] = ("no_rates", "no_forward", "sparse_chain", "no_expiry")
"""Motivos de grade do contrato §1, na ordem de precedência.

Só excluem quando a linha de `metrics_underlying` **não diz nada**: nenhum de
`iv_atm_30d`/`rv20`/`rv30`/`rv60`/`vrp` preenchido (`_screen_empty_metric_reason`).
Flag de grade num papel que tem número **não** exclui: `no_forward`/`sparse_chain`
aparecem em 97/65 dos 172 papéis de 19/11/2026 e o contrato mediu **163 `ok`**
nesse dia — excluir por flag reduziria o painel a ~10 papéis e contradiria a
contagem medida. A flag explica a célula `—`, não tira o papel do recorte.
"""


def _screen_empty_metric_reason(metric: _ScreenMetrics) -> str | None:
    """Motivo quando a linha existe mas nenhum número de IV/RV/VRP é legível.

    O contrato §1 manda cair em `no_metrics` nesse caso (há linha no parquet, mas
    nada que o painel possa mostrar); se houver flag de grade, ela é o motivo mais
    informativo e tem precedência.
    """
    readable = (metric.iv_atm_30d, metric.rv20, metric.rv30, metric.rv60, metric.vrp)
    if any(value is not None for value in readable):
        return None
    for reason in _GRID_REASONS:
        if reason in metric.flags:
            return reason
    return "no_metrics"


def _screen_base_row(
    underlying: str,
    *,
    session: date,
    model_version: int,
    spot_brl: float | None,
    earnings: tuple[date | None, str | None],
) -> ScreenRow:
    """Linha `excluded` sem motivo: cada ramo do `screen` só troca o que sabe.

    `spot_brl` existe para papel que negociou no dia mesmo sem métrica (a linha à
    vista é lida no mesmo recorte do universo); `earnings` não é métrica e vale
    para o papel inteiro.
    """
    return ScreenRow(
        underlying=underlying,
        status="excluded",
        exclusion=None,
        as_of_session=session,
        model_version=model_version,
        spot_brl=spot_brl,
        iv_atm_30d=None,
        iv_rank_252=None,
        iv_pct_252=None,
        rv20=None,
        rv30=None,
        rv60=None,
        vrp=None,
        vrp_z_252=None,
        rr25=None,
        bfly25=None,
        ts_slope_30_90=None,
        exp_move_1s_30d=None,
        implied_div_yield_30d=None,
        forward_30d=None,
        days_to_liquidate=None,
        rate_source=None,
        n_series=0,
        n_liquid_series=0,
        n_expiries=0,
        earnings_next_date=earnings[0],
        earnings_next_kind=earnings[1],
        earnings_before_expiry=False,
        flags=[],
    )


def _error_item(exc: BaseException) -> dict[str, object]:
    """`{error: {code, message, hint}}` no formato do projeto, sem traceback."""
    if isinstance(exc, SourceFailure):
        error = exc.error
    elif isinstance(exc, SourceError):
        error = exc
    else:  # pragma: no cover - rede de segurança: nada de traceback vazando
        error = SourceError(
            code="internal_error",
            message=f"{type(exc).__name__}: {exc}",
            hint="erro inesperado no health; confira o log do container",
        )
    return {"error": error.to_dict()}


def _iso_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _mapping_or_none(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _error_mapping(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    code = value.get("code")
    message = value.get("message")
    hint = value.get("hint")
    if not all(isinstance(part, str) for part in (code, message, hint)):
        return None
    return {"code": str(code), "message": str(message), "hint": str(hint)}


def _as_list(value: object) -> Iterable[object]:
    return value if isinstance(value, list) else ()


def _partition_year(path: Path) -> int | None:
    """Ano do diretório `year=YYYY` de uma partição (`None` se o nome não casar)."""
    try:
        return int(Path(path).parent.name.split("=", 1)[1])
    except (IndexError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


# --------------------------------------------------------------------------- #
# Helpers das leituras do F3 (puros, sem I/O)
# --------------------------------------------------------------------------- #

TENOR_30D = 30.0 / 252.0
"""Alvo do `smile`/ATM quando o chamador não escolhe vencimento (spec §6.4)."""


def _clean_ticker(ticker: str) -> str:
    """Ticker normalizado (maiúsculas, sem espaço); vazio é `bad_request`."""
    code = str(ticker).strip().upper()
    if not code:
        raise SourceFailure(
            SourceError(
                code="bad_request",
                message="ticker vazio",
                hint="informe o ticker do papel (ex.: PETR4) ou da série (ex.: PETRL5)",
            )
        )
    return code


def _sample_sorted(values: Iterable[date | str]) -> str:
    """Amostra legível de datas/tickers para a dica de erro (não despeja tudo)."""
    ordered = sorted(values)
    sample = ordered[:8]
    text = ", ".join(
        item.isoformat() if isinstance(item, date) else str(item) for item in sample
    )
    remaining = len(ordered) - len(sample)
    return text + (f" (+{remaining})" if remaining else "") if ordered else "nenhum"


def _strike_cents(row: ChainRow) -> int:
    """Strike em centavos inteiros (a chave que reconcilia call e put)."""
    return int(round(row.strike_brl * 100))


def _group_by_strike(rows: Sequence[ChainRow]) -> dict[int, dict[str, ChainRow]]:
    """`strike (¢) -> {lado: linha}` — call e put do mesmo strike no mesmo grupo."""
    grouped: dict[int, dict[str, ChainRow]] = defaultdict(dict)
    for row in rows:
        grouped[_strike_cents(row)][row.side] = row
    return grouped


def _maturity_t(rows: Sequence[ChainRow], maturity: date) -> float:
    """`t` do vencimento (todas as séries do dia carregam o mesmo `t`)."""
    return max((row.t for row in rows if row.maturity == maturity), default=0.0)


def _nearest_expiry(rows: Sequence[ChainRow]) -> date | None:
    """Vencimento com IV de `t` mais próximo de 30/252 (None se não houver IV)."""
    maturities = {row.maturity for row in rows if row.iv_pct is not None}
    if not maturities:
        return None
    return min(maturities, key=lambda maturity: abs(_maturity_t(rows, maturity) - TENOR_30D))


def _atm_row(rows: Sequence[ChainRow], reference: float | None) -> ChainRow | None:
    """Linha com `|Δ|` mais próximo de 0,5 (desempate: strike perto do forward/spot).

    É uma **seleção** sobre a IV/Δ gravados, não uma nova interpolação: o ATM
    oficial do papel (30d, variância total) vive em `metrics_underlying`.
    """
    candidates = [
        row for row in rows if row.iv_pct is not None and row.delta is not None
    ]
    if not candidates:
        return None
    anchor = reference if reference is not None and reference > 0 else None

    def key(row: ChainRow) -> tuple[float, float]:
        distance = abs(abs(row.delta or 0.0) - 0.5)  # type: ignore[arg-type]
        tie = abs(row.strike_brl - anchor) if anchor is not None else 0.0
        return (distance, tie)

    return min(candidates, key=key)


def _term_rows(rows: Sequence[ChainRow], spot: float | None) -> list[TermRow]:
    """Estrutura a termo: um `TermRow` por vencimento (contrato onda-i §4.1).

    O forward é reconstruído por paridade dos `mid`s gravados, com a **mesma**
    função pura do pipeline (`forward_by_parity`); sem par honesto ele é `None`
    e `forward_method` diz `none`. `exp_move_1s` usa a constante da spec §6.10.
    """
    by_maturity: dict[date, list[ChainRow]] = defaultdict(list)
    for row in rows:
        by_maturity[row.maturity].append(row)
    result: list[TermRow] = []
    for maturity in sorted(by_maturity):
        bucket = by_maturity[maturity]
        by_strike = _group_by_strike(bucket)
        quotes: list[ParityQuote] = []
        for cents in sorted(by_strike):
            pair = by_strike[cents]
            call = pair.get("C")
            put = pair.get("P")
            if call is None or put is None:
                continue
            if not call.mid_brl or not put.mid_brl or call.mid_brl <= 0 or put.mid_brl <= 0:
                continue
            quotes.append(
                ParityQuote(
                    strike=float(cents) / 100.0,
                    call_mid=call.mid_brl,
                    put_mid=put.mid_brl,
                    call_style=call.style,
                    put_style=put.style,
                    call_ex_div="american_call_ex_div" in call.flags,
                )
            )
        parity = forward_by_parity(quotes, spot=spot)
        atm = _atm_row(bucket, parity.forward if parity.forward is not None else spot)
        straddle: float | None = None
        if atm is not None:
            pair = by_strike[_strike_cents(atm)]
            call = pair.get("C")
            put = pair.get("P")
            if call is not None and put is not None and call.mid_brl and put.mid_brl:
                straddle = call.mid_brl + put.mid_brl
        exp_move = (
            None
            if straddle is None or spot is None
            else expected_move_1s(straddle, spot)
        )
        strikes = {cents for cents in by_strike if cents > 0}
        liquid_strikes = {
            cents
            for cents in strikes
            if any(row.is_liquid for row in by_strike[cents].values())
        }
        result.append(
            TermRow(
                maturity=maturity,
                t=_maturity_t(bucket, maturity),
                iv_atm=None if atm is None else atm.iv_pct,
                atm_strike_brl=None if atm is None else atm.strike_brl,
                forward_brl=parity.forward,
                forward_method=parity.method,
                exp_move_1s=exp_move,
                n_strikes=len(strikes),
                n_liquid_strikes=len(liquid_strikes),
            )
        )
    return result


def _smile_rows(rows: Sequence[ChainRow], maturity: date) -> list[SmileRow]:
    """Uma linha por série com IV no vencimento, na ordem de `(strike, side)`."""
    return [
        SmileRow(
            strike_brl=row.strike_brl,
            iv_pct=row.iv_pct,
            delta=row.delta,
            side=row.side,
            is_liquid=row.is_liquid,
        )
        for row in rows
        if row.maturity == maturity and row.iv_pct is not None and row.strike_brl > 0
    ]


__all__ = [
    "PREPARE_STATE_FILE",
    "ChainResult",
    "ChainRow",
    "EarningsEventRow",
    "EarningsRow",
    "EventRow",
    "EventsDetail",
    "Health",
    "HistoryRow",
    "OptionDay",
    "PrepareState",
    "QueryService",
    "RateNode",
    "ScreenBundle",
    "ScreenRow",
    "SeriesDay",
    "SeriesDetail",
    "SeriesPoint",
    "SmileRow",
    "SpotBar",
    "StrikeEventRow",
    "TermRow",
    "UnderlyingDetail",
    "UnderlyingMetricRow",
    "load_prepare_state",
    "prepare_state_path",
    "save_prepare_state",
]
