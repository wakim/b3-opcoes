"""Selic plana **em lote** para fechar o histórico de taxa (Onda F, §4.4/ADR-014).

O proxy da B3 publica as últimas ~20 sessões de curva DI; `rates_di` tinha **uma
data** (2026-11-19) quando a Onda F abriu. O backtest (2024–2026) precisa de taxa
em toda sessão, então este módulo preenche o que falta com a Selic diária do
SGS 11 do BCB, **uma chamada para o intervalo inteiro**:

```
GET https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados
    ?formato=json&dataInicial=DD/MM/AAAA&dataFinal=DD/MM/AAAA
```

Regras (todas medidas, não redescubra):

- **a curva PRE da B3 tem precedência**: sessão que já tem qualquer nó em
  `rates_di` não é tocada (o proxy é a curva de mercado; Selic plana é fallback);
- **o SGS só publica em dia útil**: sessão sem ponto (feriado, fim de semana,
  reunião que não abriu) usa o **último valor anterior** — a Selic é constante
  entre reuniões do Copom e o SGS republica o mesmo valor todo dia útil;
- **unidade**: o SGS devolve `% a.d.` (`0.043739`); o lake guarda `rate_annual`
  em **% a.a.** (`((1 + t/100)**252 − 1) * 100`). O ×100 é obrigatório — o F1
  divide por 100 no `DF`, e uma fonte em fração ao lado de outra em porcento é
  bug silencioso de 100×;
- **curva plana** `du = 1…FLAT_HORIZON_DU` (756 ≈ 3 anos): nenhum vencimento de
  opção de ação passa disso, então a interpolação log-DF do F1 nunca extrapola;
- **escrita**: `write_rates_partition` (Onda B) faz o merge por `refdate`, então
  esta função é idempotente e nunca apaga a curva PRE do dia;
- **falha explícita**: intervalo sem nenhum ponto, sessão anterior ao primeiro
  ponto da série e erro de disco são `SourceFailure` com `hint` — nunca zero.
  Falha de rede/HTTP ganha retry (o BCB é lento como o BDI; medido no B5).

Custo medido: uma chamada HTTP e **uma** reescrita da partição do ano por sessão
(o escritor da Onda B é por dia). 221 sessões de 2026 levam dezenas de segundos,
não minutos — o backfill roda uma vez por ano preparado.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from b3o.ingest.errors import SourceError, SourceFailure
from b3o.ingest.rates import (
    FLAT_HORIZON_DU,
    SGS_SELIC_URL,
    SOURCE_SELIC_FLAT,
    RatePoint,
    _get,
    _parse_br_float,
    write_rates_partition,
)

log = logging.getLogger(__name__)

DEFAULT_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)
"""Uma entrada por re-tentativa (mesmo padrão do OI — o BCB também estoura leitura)."""

_HINT_NETWORK = (
    "confira rede/DNS do container e tente de novo; a série 11 do SGS é pública e "
    "sem token, e o endpoint exige User-Agent de browser"
)
_HINT_SERIES = (
    "o SGS 11 é diário e cobre qualquer data histórica; lista vazia significa "
    "endpoint errado ou data fora da série — confira "
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados"
)


def backfill_selic_flat(
    sessions: Sequence[date],
    *,
    client: httpx.Client,
    data_dir: Path,
    retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
) -> dict[str, object]:
    """Preenche `rates_di` das sessões **sem** curva com a Selic plana anualizada.

    Uma chamada ao SGS 11 para `[min(sessions), max(sessions)]`; só as sessões
    sem nenhum nó são gravadas (PRE da B3 vence). Devolve:

    ```
    {"sessions": n, "written": k, "skipped_existing": m, "source": "selic_flat",
     "selic_pct": {"AAAA-MM-DD": 0.043739, ...}, "errors": []}
    ```

    `selic_pct` é o valor **diário em % a.d.** usado em cada sessão gravada (a
    anualização está no lake, não aqui); `errors` fica reservado para falhas por
    sessão — hoje toda falha de fonte ou de disco é `SourceFailure`, porque um
    resumo com erro silencioso é o que a regra 5 proíbe.
    """
    wanted = sorted(set(sessions))
    if not wanted:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message="backfill_selic_flat sem sessão para preencher",
                hint="o calendário vem do lake: rode `b3o prepare` antes do `b3o rates`",
            )
        )

    existing = _existing_curve_dates(data_dir)
    missing = [day for day in wanted if day not in existing]
    skipped = len(wanted) - len(missing)
    if not missing:
        log.info("selic plana: as %d sessões já têm curva; nada a fazer", len(wanted))
        return {
            "sessions": len(wanted),
            "written": 0,
            "skipped_existing": skipped,
            "source": SOURCE_SELIC_FLAT,
            "selic_pct": {},
            "errors": [],
        }

    points = _fetch_series(client, missing, retry_delays=retry_delays)
    selic_pct: dict[str, float] = {}
    written = 0
    for day in missing:
        daily_pct = _value_on(points, day)
        rate_annual = ((1.0 + daily_pct / 100.0) ** 252 - 1.0) * 100.0
        curve = [
            RatePoint(refdate=day, du=du, rate_annual=rate_annual, source=SOURCE_SELIC_FLAT)
            for du in range(1, FLAT_HORIZON_DU + 1)
        ]
        try:
            write_rates_partition(curve, data_dir)
        except OSError as exc:
            raise SourceFailure(
                SourceError(
                    code="storage_error",
                    message=f"curva Selic de {day.isoformat()} não gravada: {exc}",
                    hint=(
                        "confira espaço/permissão do volume de dados do container "
                        "(B3_DATA_DIR, /data); as sessões já gravadas continuam válidas"
                    ),
                )
            ) from exc
        selic_pct[day.isoformat()] = daily_pct
        written += 1

    log.info(
        "selic plana: %d sessão(ões) gravada(s), %d já tinha(m) curva, %.6f%% a.d. em média",
        written,
        skipped,
        sum(selic_pct.values()) / len(selic_pct) if selic_pct else 0.0,
    )
    return {
        "sessions": len(wanted),
        "written": written,
        "skipped_existing": skipped,
        "source": SOURCE_SELIC_FLAT,
        "selic_pct": selic_pct,
        "errors": [],
    }


def _existing_curve_dates(data_dir: Path) -> set[date]:
    """Datas que já têm **qualquer** nó de curva em `rates_di` (PRE ou fallback)."""
    root = Path(data_dir) / "rates_di"
    if not root.is_dir():
        return set()
    dates: set[date] = set()
    for path in sorted(root.glob("year=*/part-0.parquet")):
        try:
            column = pq.read_table(path, columns=["refdate"]).column("refdate")
        except (OSError, pa.ArrowException) as exc:
            raise SourceFailure(
                SourceError(
                    code="bad_data",
                    message=f"partição de taxas ilegível em {path}: {exc}",
                    hint=(
                        "apague data/rates_di e rode `b3o prepare` + `b3o rates --backfill`; "
                        "não dá para saber que dias já têm curva"
                    ),
                )
            ) from exc
        dates.update(day for day in column.to_pylist() if isinstance(day, date))
    return dates


def _fetch_series(
    client: httpx.Client,
    sessions: Sequence[date],
    *,
    retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
) -> list[tuple[date, float]]:
    """Uma chamada ao SGS 11 para o intervalo; devolve `[(data, % a.d.)]` ordenado."""
    start, end = sessions[0], sessions[-1]
    params = {
        "formato": "json",
        "dataInicial": start.strftime("%d/%m/%Y"),
        "dataFinal": end.strftime("%d/%m/%Y"),
    }
    attempts = 1 + len(retry_delays)
    last_error: SourceError | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = _get(client, SGS_SELIC_URL, source=SOURCE_SELIC_FLAT, params=params)
            if response.status_code >= 400:
                raise SourceFailure(
                    SourceError(
                        code="source_unavailable",
                        message=(
                            f"HTTP {response.status_code} no SGS 11 "
                            f"({start.isoformat()}…{end.isoformat()})"
                        ),
                        hint=_HINT_NETWORK,
                    )
                )
            return _parse_series(response, start=start, end=end)
        except SourceFailure as exc:
            last_error = exc.error
            if attempt >= attempts:
                raise
            delay = float(retry_delays[attempt - 1])
            log.warning(
                "SGS 11: tentativa %d/%d falhou (%s); nova tentativa em %.0fs",
                attempt,
                attempts,
                exc.error.message,
                delay,
            )
            time.sleep(delay)
    raise SourceFailure(  # pragma: no cover - o laço sempre levanta ou devolve
        last_error
        or SourceError(
            code="source_unavailable",
            message="SGS 11 sem resposta",
            hint=_HINT_NETWORK,
        )
    )


def _parse_series(
    response: httpx.Response, *, start: date, end: date
) -> list[tuple[date, float]]:
    """JSON `[{"data": "DD/MM/AAAA", "valor": "0.043739"}]` → pontos ordenados."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=f"resposta do SGS 11 não é JSON: {exc}",
                hint=_HINT_SERIES,
            )
        ) from exc
    if not isinstance(payload, list):
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=f"SGS 11 devolveu {type(payload).__name__}, esperado lista",
                hint=_HINT_SERIES,
            )
        )

    points: dict[date, float] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            when = datetime.strptime(str(item.get("data", "")), "%d/%m/%Y").date()
            value = _parse_br_float(str(item.get("valor", "")))
        except ValueError:
            continue
        points[when] = value

    if not points:
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=(
                    f"SGS 11 sem nenhum ponto entre {start.isoformat()} e {end.isoformat()}"
                ),
                hint=_HINT_SERIES,
            )
        )
    return sorted(points.items())


def _value_on(points: Sequence[tuple[date, float]], day: date) -> float:
    """Valor diário em vigor em `day`: o do próprio dia ou o último anterior.

    Sessão anterior ao primeiro ponto é falha explícita (nunca 0): pedir Selic
    para antes do começo da série é erro de calendário, não ausência de dado.
    """
    chosen: float | None = None
    for when, value in points:
        if when > day:
            break
        chosen = value
    if chosen is None:
        raise SourceFailure(
            SourceError(
                code="source_unavailable",
                message=(
                    f"sem Selic até {day.isoformat()}: o primeiro ponto do intervalo é "
                    f"{points[0][0].isoformat()}"
                ),
                hint=(
                    "a série 11 começa em 1986 e cobre qualquer sessão do lake; uma data "
                    "antes do primeiro ponto é erro de calendário — confira `dataInicial`"
                ),
            )
        )
    return chosen


__all__ = ["DEFAULT_RETRY_DELAYS", "backfill_selic_flat"]
