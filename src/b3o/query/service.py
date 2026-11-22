"""`QueryService` (F0.12): a **única** camada de consulta do lake.

Nasce com `health()` — as outras funções da spec §7 entram em F2/F3 — e com o
mínimo que o `prepare` precisa ler de forma honesta (`lake_calendar`,
`counts_by_date`, `spot_tickers`). DuckDB é permitido **aqui e em `store/`**:
qualquer outra query no projeto é bug de arquitetura (teste verde).

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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb

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
    """Pregões entre `ingest_state.last_session` e a última sessão do lake."""

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

    oi_available_dates: object
    """Datas com posições em aberto gravadas (P-N)."""

    oi_unavailable: object
    """`True` quando a última sessão do lake não tem OI (P-N)."""

    earnings_coverage: object
    """`{universe, mapped, unmapped, dates}` do calendário × universo (§6.14)."""

    universe: object
    """`{session, count, tickers}` do universo medido no último prepare (§6.9)."""

    unexplained_gap: None
    """F0 não tem retorno realizado: o campo existe vazio, de propósito."""

    last_error: dict[str, str] | None
    """Último erro de fonte que não derrubou o prepare (P-E)."""

    rss_peak_mib: float | None
    """Pico de RSS do último prepare, em MiB (3,7 GiB de RAM no motor)."""

    model_version: int
    """Versão do cálculo derivado (`ingest_state`)."""

    as_of: str | None
    """`updated_at` do `ingest_state` (ISO com offset, P-D)."""

    def to_dict(self) -> dict[str, object]:
        """Serialização JSON (ordem estável, nada de `date` solto)."""
        return {
            "last_session": self.last_session,
            "age_sessions": self.age_sessions,
            "lake_sessions": self.lake_sessions,
            "lake_years": self.lake_years,
            "counts_by_date": self.counts_by_date,
            "counts_by_market": self.counts_by_market,
            "discarded_by_market": self.discarded_by_market,
            "skipped_lines": self.skipped_lines,
            "rate_source": self.rate_source,
            "oi_available_dates": self.oi_available_dates,
            "oi_unavailable": self.oi_unavailable,
            "earnings_coverage": self.earnings_coverage,
            "universe": self.universe,
            "unexplained_gap": self.unexplained_gap,
            "last_error": self.last_error,
            "rss_peak_mib": self.rss_peak_mib,
            "model_version": self.model_version,
            "as_of": self.as_of,
        }


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
        counts_by_market, discarded = self._markets(prepare)
        skipped_lines = self._skipped(state, state_error)
        rate_source = self._rate_source()
        oi_dates, oi_unavailable = self._open_interest(last_session)
        earnings_coverage = self._earnings_coverage(prepare, last_session)

        return Health(
            last_session=last_session_value,
            age_sessions=age_sessions,
            lake_sessions=lake_sessions,
            lake_years=lake_years,
            counts_by_date=counts_by_date,
            counts_by_market=counts_by_market,
            discarded_by_market=discarded,
            skipped_lines=skipped_lines,
            rate_source=rate_source,
            oi_available_dates=oi_dates,
            oi_unavailable=oi_unavailable,
            earnings_coverage=earnings_coverage,
            universe=self._universe(prepare),
            unexplained_gap=None,
            last_error=prepare.last_error,
            rss_peak_mib=prepare.rss_peak_mib,
            model_version=state.model_version if state is not None else MODEL_VERSION,
            as_of=(
                state.updated_at.isoformat()
                if state is not None and state.updated_at is not None
                else None
            ),
        )

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

    def _age_sessions(
        self,
        state: IngestState | None,
        sessions: list[date],
        state_error: SourceError | None,
    ) -> object:
        """Idade em pregões do **calendário do lake**: state × dado presente.

        `0` significa que o `ingest_state` cobre a última sessão do lake. Se o
        estado não tem `last_session` (nunca rodou, ou o arquivo se perdeu), a
        idade é o lake inteiro — o state está atrás de tudo o que existe.
        """
        if state_error is not None:
            return _error_item(state_error)
        if not sessions:
            return _missing_lake_item(self.data_dir)
        if state is None or state.last_session is None:
            return len(sessions)
        return sum(1 for day in sessions if day > state.last_session)

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


def _missing_lake_item(data_dir: Path) -> dict[str, object]:
    """Erro explícito de lake ausente (nunca "0 sessões" como se fosse verdade)."""
    return _error_item(
        SourceError(
            code="lake_missing",
            message=f"nenhuma partição do COTAHIST em {Path(data_dir) / 'raw_cotahist'}",
            hint=_HINT_DATA_DIR,
        )
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


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


__all__ = [
    "PREPARE_STATE_FILE",
    "Health",
    "PrepareState",
    "QueryService",
    "load_prepare_state",
    "prepare_state_path",
    "save_prepare_state",
]
