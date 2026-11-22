"""Calendário de resultados da CVM (F0.10, spec §6.14) — sem digitação manual.

Fonte única: dados abertos da CVM (`dados.cvm.gov.br`), gratuitos e sem token.

- **`actual`**: data de entrega (`DT_RECEB`) da **primeira versão** (`VERSAO == 1`)
  do ITR/DFP para cada `DT_REFER`. Revisões (VERSAO 2+) não mudam a data — o
  resultado já era público. Chave: `(CD_CVM, DT_REFER)`, porque o ISIN do título
  não identifica a empresa e o `CNPJ_CIA` é o join com o FCA.
- **`estimated`**: a data `actual` da **mesma referência do ano anterior**,
  deslocada para o **mesmo dia da semana dentro da mesma semana ISO**, limitada
  pelo prazo regulatório (ITR: 45 dias após o fim do trimestre; DFP: 3 meses após
  o exercício). Nunca é data confirmada — o `kind` diz isso.
- **Papel ↔ empresa**: membro `fca_cia_aberta_valor_mobiliario_{AAAA}.csv` do FCA.
  Só vale `Mercado == "Bolsa"` **na versão mais recente** do FCA do papel
  (`Data_Referencia` máxima — o FCA é reenviado durante o ano com outra data de
  referência) e com `Data_Fim_Negociacao` vazio. Papel sem correspondência fica
  **fora** da lista: data inventada é pior que ausência (quem expõe a lacuna é o
  C1, com a flag `earnings_unmapped`).

Sem relógio: o "futuro" é relativo à `last_session` recebida (P-C). Um `actual`
posterior à sessão ainda não era público e não entra — nesse caso o papel recebe
o `estimated` (que é o que se sabia na sessão). Nenhum look-ahead no backtest.

Medido em 22/11/2026 (não redescubra): o `dfp_cia_aberta_{AAAA}.zip` do ano
corrente é quase vazio no começo do ciclo (DFP 2026: 9 linhas, só companhias de
exercício não-calendário); o resultado anual de 2025 vem do DFP **2025** (763
linhas, 672 CD_CVM). O FCA do ano corrente é um retrato parcial (2026: 510
códigos; 2025: 530) — incluir dois anos e deixar a versão mais recente vencer por
papel recupera quem não está no arquivo novo (AZUL4, p.ex.) sem inventar nada.
Todos os códigos ativos da Bolsa no FCA casaram com um `CD_CVM` no ITR/DFP em
98,8% dos casos (477 de 483); os 6 restantes ficam fora, como manda a spec.

Falha de rede/parse vira `SourceError(code="source_unavailable")` carregado no
resultado do download (`SourceFailure` nunca escapa daqui): a CVM fora do ar não
derruba o `prepare`, vira flag no `health`.
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import logging
import os
import time
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from b3o.fsutil import replace_file
from b3o.ingest.errors import SourceError, SourceFailure

log = logging.getLogger(__name__)

# Endpoints medidos em 22/11/2026 (200 OK, `application/zip`).
CVM_DOC_BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"
DATASETS: tuple[str, ...] = ("itr", "dfp", "fca")
_URL_TEMPLATE = {
    "itr": CVM_DOC_BASE + "/ITR/DADOS/itr_cia_aberta_{year}.zip",
    "dfp": CVM_DOC_BASE + "/DFP/DADOS/dfp_cia_aberta_{year}.zip",
    "fca": CVM_DOC_BASE + "/FCA/DADOS/fca_cia_aberta_{year}.zip",
}

# O arquivo do ano corrente é reenviado; GET condicional custa zero byte.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

KINDS: tuple[str, ...] = ("actual", "estimated")
COLUMNS: tuple[str, ...] = ("ticker", "ref_period", "date", "kind")

_CHUNK_BYTES = 1 << 20
_ITR_DEADLINE_DAYS = 45
_HINT_NETWORK = (
    "confira a rede/DNS do container; a CVM (dados.cvm.gov.br) é pública e não exige token"
)
_HINT_PERMANENT = (
    "erro permanente da fonte: confira a URL em dados.cvm.gov.br e se o ano existe; "
    "não adianta re-tentar"
)

try:
    _tz: tzinfo = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:  # imagem sem tzdata; Brasil não tem horário de verão desde 2019
    _tz = timezone(timedelta(hours=-3))


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EarningsDate:
    """Um resultado (real ou previsto) de um papel.

    `ref_period` é o fim do trimestre de referência; `date` é o dia da entrega
    (ITR/DFP) ou a estimativa dela. `kind` só admite "actual"/"estimated".
    """

    ticker: str
    ref_period: date
    date: date
    kind: Literal["actual", "estimated"]


@dataclass(frozen=True, slots=True)
class DatasetFetch:
    """Resultado do download de um dataset×ano (A1-style: nunca levanta por rede)."""

    dataset: str
    year: int
    status: Literal["downloaded", "not_modified", "error"]
    path: Path | None = None
    etag: str | None = None
    last_modified: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    downloaded_at: str | None = None
    error: SourceError | None = None


@dataclass(frozen=True, slots=True)
class EarningsFetchResult:
    """Todos os downloads de um `fetch_earnings` (provedor de `SourceError`)."""

    results: tuple[DatasetFetch, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def failures(self) -> tuple[DatasetFetch, ...]:
        return tuple(r for r in self.results if r.status == "error")

    @property
    def errors(self) -> tuple[SourceError, ...]:
        return tuple(r.error for r in self.failures if r.error is not None)

    def path(self, dataset: str, year: int) -> Path | None:
        for r in self.results:
            if r.dataset == dataset and r.year == year:
                return r.path
        return None

    def paths(self, dataset: str) -> list[Path]:
        return [
            r.path
            for r in self.results
            if r.dataset == dataset and r.path is not None and r.error is None
        ]


# --------------------------------------------------------------------------- #
# Download condicional
# --------------------------------------------------------------------------- #


def dataset_path(dest_dir: Path, dataset: str, year: int) -> Path:
    """Caminho do zip local de um dataset×ano (`dest_dir/<dataset>/…`)."""
    return Path(dest_dir) / dataset / f"{dataset}_cia_aberta_{year}.zip"


def dataset_meta_path(dest_dir: Path, dataset: str, year: int) -> Path:
    """Caminho da meta local (`etag`/`last_modified`/`sha256`/`bytes`/`downloaded_at`)."""
    return dataset_path(dest_dir, dataset, year).with_name(
        f"{dataset}_cia_aberta_{year}.zip.meta.json"
    )


def fetch_earnings(
    years: Iterable[int],
    dest_dir: Path,
    *,
    client: httpx.Client,
    retry_delays: tuple[float, ...] = (1.0, 2.0),
) -> EarningsFetchResult:
    """Baixa ITR/DFP/FCA dos anos pedidos se a fonte mudou; nunca levanta por rede.

    `dest_dir` é o cache da CVM (`data_dir/cvm`). `client` é criado por quem chama.
    Falha de um dataset não impede os outros: cada item carrega o seu `SourceError`.
    """
    wanted = sorted({int(y) for y in years})
    results = [
        _fetch_one(dataset, year, Path(dest_dir), client=client, retry_delays=retry_delays)
        for dataset in DATASETS
        for year in wanted
    ]
    return EarningsFetchResult(tuple(results))


def _fetch_one(
    dataset: str,
    year: int,
    dest_dir: Path,
    *,
    client: httpx.Client,
    retry_delays: tuple[float, ...],
) -> DatasetFetch:
    target = dataset_path(dest_dir, dataset, year)
    meta_file = dataset_meta_path(dest_dir, dataset, year)
    url = _URL_TEMPLATE[dataset].format(year=year)

    problem = _url_problem(url)
    if problem is not None:
        return DatasetFetch(dataset=dataset, year=year, status="error", error=problem)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return DatasetFetch(
            dataset=dataset,
            year=year,
            status="error",
            error=SourceError(
                code="source_unavailable",
                message=f"não foi possível criar {target.parent}: {exc}",
                hint="confira o bind mount de dados do container (B3_DATA_DIR=/data)",
            ),
        )

    meta = _read_meta(meta_file)
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/zip, application/octet-stream, */*",
    }
    if meta is not None:
        etag = _as_str(meta.get("etag"))
        last_modified = _as_str(meta.get("last_modified"))
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

    attempts = 1 + len(retry_delays)
    last_error: SourceError | None = None

    for attempt in range(1, attempts + 1):
        try:
            with client.stream("GET", url, headers=headers, follow_redirects=True) as response:
                status = response.status_code
                if status == 304:
                    if meta is None or not target.is_file():
                        return DatasetFetch(
                            dataset=dataset,
                            year=year,
                            status="error",
                            error=SourceError(
                                code="source_unavailable",
                                message=f"HTTP 304 em {url} sem zip/meta local correspondente",
                                hint=(
                                    "apague data/cvm e rode o prepare de novo "
                                    "(estado local perdido)"
                                ),
                            ),
                        )
                    log.info("%s %s: 304 not modified (%s)", dataset, year, target.name)
                    return _not_modified(dataset, year, target, meta)
                if status == 200:
                    return _download(dataset, year, response, target, meta_file)
                if status >= 500:
                    last_error = SourceError(
                        code="source_unavailable",
                        message=f"HTTP {status} em {url}",
                        hint="a CVM está fora do ar ou em manutenção; re-tentar mais tarde",
                    )
                else:
                    return DatasetFetch(
                        dataset=dataset,
                        year=year,
                        status="error",
                        error=SourceError(
                            code="source_unavailable",
                            message=f"HTTP {status} em {url}",
                            hint=_HINT_PERMANENT,
                        ),
                    )
        except httpx.TransportError as exc:
            last_error = SourceError(
                code="source_unavailable",
                message=f"falha de transporte em {url}: {type(exc).__name__}: {exc}",
                hint=_HINT_NETWORK,
            )
        except httpx.HTTPError as exc:
            return DatasetFetch(
                dataset=dataset,
                year=year,
                status="error",
                error=SourceError(
                    code="source_unavailable",
                    message=f"erro HTTP em {url}: {type(exc).__name__}: {exc}",
                    hint=_HINT_NETWORK,
                ),
            )

        if attempt < attempts:
            delay = retry_delays[attempt - 1]
            log.warning(
                "%s %s: tentativa %d/%d falhou (%s); nova tentativa em %.1fs",
                dataset,
                year,
                attempt,
                attempts,
                last_error.message if last_error else "?",
                delay,
            )
            if delay > 0:
                time.sleep(delay)

    error = last_error or SourceError(
        code="source_unavailable",
        message=f"falha desconhecida ao baixar {url}",
        hint=_HINT_NETWORK,
    )
    log.error("%s %s: desisti após %d tentativa(s): %s", dataset, year, attempts, error.message)
    return DatasetFetch(dataset=dataset, year=year, status="error", error=error)


def _download(
    dataset: str,
    year: int,
    response: httpx.Response,
    target: Path,
    meta_file: Path,
) -> DatasetFetch:
    part = target.with_name(f"tmp-{target.name}.part")
    digest = hashlib.sha256()
    written = 0
    try:
        with part.open("wb") as handle:
            for chunk in response.iter_bytes(_CHUNK_BYTES):
                handle.write(chunk)
                digest.update(chunk)
                written += len(chunk)
    except httpx.HTTPError as exc:
        part.unlink(missing_ok=True)
        return DatasetFetch(
            dataset=dataset,
            year=year,
            status="error",
            error=SourceError(
                code="source_unavailable",
                message=(
                    f"corpo incompleto de {response.request.url}: {type(exc).__name__}: {exc}"
                ),
                hint=_HINT_NETWORK,
            ),
        )
    except OSError as exc:
        part.unlink(missing_ok=True)
        return DatasetFetch(
            dataset=dataset,
            year=year,
            status="error",
            error=SourceError(
                code="source_unavailable",
                message=f"falha de disco ao gravar {part}: {exc}",
                hint="confira espaço/permissão do volume de dados do container",
            ),
        )

    declared = _declared_length(response)
    if declared is not None and declared != written:
        part.unlink(missing_ok=True)
        return DatasetFetch(
            dataset=dataset,
            year=year,
            status="error",
            error=SourceError(
                code="source_unavailable",
                message=(
                    f"Content-Length declarou {declared} bytes mas chegaram {written} "
                    f"de {response.request.url}"
                ),
                hint="download truncado; rode o prepare de novo (o .part foi descartado)",
            ),
        )

    try:
        replace_file(part, target)
    except OSError as exc:
        part.unlink(missing_ok=True)
        return DatasetFetch(
            dataset=dataset,
            year=year,
            status="error",
            error=SourceError(
                code="source_unavailable",
                message=f"falha ao renomear {part} para {target}: {exc}",
                hint="confira espaço/permissão do volume de dados do container",
            ),
        )

    meta: dict[str, object] = {
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
        "sha256": digest.hexdigest(),
        "bytes": written,
        "downloaded_at": _now_iso(),
    }
    try:
        _write_meta(meta_file, meta)
    except OSError as exc:
        # O zip vale; sem meta o próximo prepare só paga um 200 (nunca dado errado).
        log.warning("%s %s: zip salvo, meta não gravada em %s: %s", dataset, year, meta_file, exc)

    log.info("%s %s: baixado (%d bytes, sha256=%s)", dataset, year, written, meta["sha256"])
    return DatasetFetch(
        dataset=dataset,
        year=year,
        status="downloaded",
        path=target,
        etag=_as_str(meta["etag"]),
        last_modified=_as_str(meta["last_modified"]),
        sha256=_as_str(meta["sha256"]),
        bytes=written,
        downloaded_at=_as_str(meta["downloaded_at"]),
    )


def _not_modified(dataset: str, year: int, target: Path, meta: dict[str, object]) -> DatasetFetch:
    size = meta.get("bytes")
    return DatasetFetch(
        dataset=dataset,
        year=year,
        status="not_modified",
        path=target,
        etag=_as_str(meta.get("etag")),
        last_modified=_as_str(meta.get("last_modified")),
        sha256=_as_str(meta.get("sha256")),
        bytes=size if isinstance(size, int) and not isinstance(size, bool) else None,
        downloaded_at=_as_str(meta.get("downloaded_at")),
    )


# --------------------------------------------------------------------------- #
# Leitura dos arquivos da CVM
# --------------------------------------------------------------------------- #


def build_earnings(
    itr_zip: Path | Sequence[Path],
    dfp_zip: Path | Sequence[Path],
    fca_zip: Path | Sequence[Path],
    tickers: Iterable[str],
    last_session: date,
) -> list[EarningsDate]:
    """Monta o calendário a partir dos zips já baixados (função pura, sem rede).

    Aceita um ou vários zips por dataset (um por ano). Devolve só papéis que
    casaram com uma empresa no FCA; a ordem é `(ticker, ref_period, kind)`.
    """
    itr_paths = _as_paths(itr_zip)
    dfp_paths = _as_paths(dfp_zip)
    fca_paths = _as_paths(fca_zip)
    doc_paths = itr_paths + dfp_paths

    actuals = _parse_doc_index(doc_paths)  # (CD_CVM, ref) -> DT_RECEB da VERSAO 1
    cnpj_to_cd = _parse_company_ids(doc_paths)  # CNPJ (só dígitos) -> CD_CVM
    ticker_to_cnpj = _parse_fca(fca_paths)  # ticker ativo -> CNPJ (só dígitos)

    refs_by_cd: dict[str, set[date]] = {}
    for cd, ref in actuals:
        refs_by_cd.setdefault(cd, set()).add(ref)

    rows: dict[tuple[str, date, str], EarningsDate] = {}

    for raw in tickers:
        ticker = raw.strip().upper()
        if not ticker:
            continue
        cnpj = ticker_to_cnpj.get(ticker)
        cd = cnpj_to_cd.get(cnpj) if cnpj else None
        if cd is None:
            # Sem correspondência no FCA/ITR: fora da lista (flag é do C1).
            continue

        known = {ref: day for (owner, ref), day in actuals.items() if owner == cd}

        for ref, day in known.items():
            if day <= last_session:
                rows[(ticker, ref, "actual")] = EarningsDate(ticker, ref, day, "actual")

        years = {ref.year for ref in refs_by_cd.get(cd, set())} | {last_session.year}
        for year in sorted(years):
            for ref in _quarter_refs(year):
                if ref in known and known[ref] <= last_session:
                    continue  # já é público: não se estima o que já saiu
                source = known.get(_previous_year(ref))
                if source is None or source > last_session:
                    continue
                estimated = min(_next_year_same_weekday(source), _regulatory_deadline(ref))
                if estimated > last_session:
                    rows[(ticker, ref, "estimated")] = EarningsDate(
                        ticker, ref, estimated, "estimated"
                    )

    return sorted(rows.values(), key=lambda r: (r.ticker, r.ref_period, r.kind))


def _parse_doc_index(paths: Sequence[Path]) -> dict[tuple[str, date], date]:
    """`(CD_CVM, DT_REFER) -> DT_RECEB` da VERSAO 1 do ITR/DFP (índice do zip).

    Revisão (VERSAO ≥ 2) não muda a data: o resultado já era público quando a
    primeira versão foi entregue (spec §6.14).
    """
    first_version: dict[tuple[str, date], date] = {}
    for path in paths:
        for row in _iter_csv(path):
            if _to_int(row.get("VERSAO")) != 1:
                continue
            cd = _field(row, "CD_CVM")
            ref = _parse_date(row.get("DT_REFER"))
            received = _parse_date(row.get("DT_RECEB"))
            if not cd or ref is None or received is None:
                continue
            key = (cd, ref)
            if key not in first_version or received < first_version[key]:
                first_version[key] = received
    return first_version


def _parse_company_ids(paths: Sequence[Path]) -> dict[str, str]:
    """`CNPJ_CIA -> CD_CVM` a partir das linhas do índice (join com o FCA)."""
    mapping: dict[str, str] = {}
    for path in paths:
        for row in _iter_csv(path):
            cd = _field(row, "CD_CVM")
            cnpj = _digits(_field(row, "CNPJ_CIA"))
            if cd and cnpj:
                mapping.setdefault(cnpj, cd)
    return mapping


def _parse_fca(paths: Sequence[Path]) -> dict[str, str]:
    """`Codigo_Negociacao -> CNPJ` dos papéis negociados na Bolsa e ainda ativos.

    O FCA é reenviado com outra `Data_Referencia`; vale a versão mais recente por
    papel. Se a versão mais recente diz que o papel saiu da Bolsa
    (`Data_Fim_Negociacao` preenchido), o papel não é mapeado.
    """
    latest: dict[str, tuple[date, int, str, bool]] = {}
    for path in paths:
        for row in _iter_csv(path, member=_fca_member(path)):
            ticker = _field(row, "Codigo_Negociacao").upper()
            cnpj = _digits(_field(row, "CNPJ_Companhia"))
            if not ticker or not cnpj:
                continue
            reference = _parse_date(row.get("Data_Referencia")) or date.min
            version = _to_int(row.get("Versao")) or 0
            active = (
                _field(row, "Mercado").lower() == "bolsa"
                and not _field(row, "Data_Fim_Negociacao")
            )
            current = latest.get(ticker)
            if current is None or (reference, version) >= (current[0], current[1]):
                latest[ticker] = (reference, version, cnpj, active)
    return {ticker: value[2] for ticker, value in latest.items() if value[3]}


def _iter_csv(path: Path, *, member: str | None = None) -> Iterator[dict[str, str]]:
    """Linhas do CSV latin-1 com `;` (índice do zip por padrão).

    O campo de texto da CVM vem com espaço à direita e o cabeçalho pode carregar
    BOM: ambos normalizados aqui, uma vez.
    """
    with zipfile.ZipFile(path) as archive:
        name = member or _index_member(archive, path)
        with archive.open(name) as handle:
            text = io.TextIOWrapper(handle, encoding="latin-1", newline="")
            for row in csv.DictReader(text, delimiter=";"):
                yield {
                    (key or "").strip().lstrip("\ufeff"): (value or "").strip()
                    for key, value in row.items()
                }


def _index_member(archive: zipfile.ZipFile, path: Path) -> str:
    """Membro do índice (`itr_cia_aberta_AAAA.csv`); senão o .csv mais curto."""
    expected = f"{path.stem}.csv"
    names = archive.namelist()
    if expected in names:
        return expected
    candidates = sorted((n for n in names if n.lower().endswith(".csv")), key=lambda n: (len(n), n))
    if not candidates:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"{path.name} não tem nenhum CSV dentro",
                hint="o zip da CVM mudou de layout; confira o membro do índice",
            )
        )
    return candidates[0]


def _fca_member(path: Path) -> str:
    """Membro `fca_cia_aberta_valor_mobiliario_AAAA.csv` do zip do FCA."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    exact = f"fca_cia_aberta_valor_mobiliario_{_year_of(path)}.csv"
    if exact in names:
        return exact
    candidates = sorted(
        (n for n in names if "valor_mobiliario" in n and n.lower().endswith(".csv")),
        key=lambda n: (len(n), n),
    )
    if not candidates:
        raise SourceFailure(
            SourceError(
                code="bad_data",
                message=f"{path.name} não tem o membro valor_mobiliario",
                hint="confira o dataset cia_aberta-doc-fca em dados.cvm.gov.br",
            )
        )
    return candidates[0]


# --------------------------------------------------------------------------- #
# Datas: estimativa e prazo regulatório
# --------------------------------------------------------------------------- #


def _quarter_refs(year: int) -> tuple[date, ...]:
    return (
        date(year, 3, 31),
        date(year, 6, 30),
        date(year, 9, 30),
        date(year, 12, 31),
    )


def _previous_year(ref: date) -> date:
    return date(ref.year - 1, ref.month, ref.day)


def _next_year_same_weekday(actual: date) -> date:
    """Mesmo dia da semana, na mesma semana ISO, um ano depois da data `actual`."""
    try:
        base = actual.replace(year=actual.year + 1)
    except ValueError:  # 29/02
        base = date(actual.year + 1, 2, 28)
    monday = base - timedelta(days=base.weekday())
    return monday + timedelta(days=actual.weekday())


def _regulatory_deadline(ref: date) -> date:
    """ITR: 45 dias após o trimestre. DFP: 3 meses após o exercício."""
    if ref.month == 12:
        return _add_months(ref, 3)
    return ref + timedelta(days=_ITR_DEADLINE_DAYS)


def _add_months(day: date, months: int) -> date:
    year = day.year + (day.month - 1 + months) // 12
    month = (day.month - 1 + months) % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# --------------------------------------------------------------------------- #
# Persistência (tabela pequena: um arquivo, sem partição por ano)
# --------------------------------------------------------------------------- #


def earnings_path(data_dir: Path) -> Path:
    return Path(data_dir) / "earnings" / "part-0.parquet"


def write_earnings(rows: Iterable[EarningsDate], data_dir: Path) -> Path:
    """Grava `earnings/part-0.parquet` de forma atômica (tmp + `fsutil.replace_file`)."""
    data = sorted(rows, key=lambda r: (r.ticker, r.ref_period, r.kind))
    for row in data:
        if row.kind not in KINDS:
            raise ValueError(f"kind inválido: {row.kind!r} (só {KINDS})")

    table = pa.table(
        {
            "ticker": pa.array([r.ticker for r in data], type=pa.string()),
            "ref_period": pa.array([r.ref_period for r in data], type=pa.date32()),
            "date": pa.array([r.date for r in data], type=pa.date32()),
            "kind": pa.array([r.kind for r in data], type=pa.string()),
        }
    )

    target = earnings_path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(data_dir) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"earnings-part-0-{os.getpid()}.parquet"
    try:
        pq.write_table(table, tmp, compression="zstd")
        replace_file(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def read_earnings(data_dir: Path) -> list[EarningsDate]:
    """Lê `earnings/part-0.parquet`; arquivo ausente é erro explícito, não lista vazia."""
    path = earnings_path(data_dir)
    table = pq.read_table(path, columns=list(COLUMNS))
    return [
        EarningsDate(
            ticker=ticker,
            ref_period=ref,
            date=day,
            kind=kind,
        )
        for ticker, ref, day, kind in zip(
            table["ticker"].to_pylist(),
            table["ref_period"].to_pylist(),
            table["date"].to_pylist(),
            table["kind"].to_pylist(),
            strict=True,
        )
    ]


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #


def _as_paths(value: Path | Sequence[Path]) -> list[Path]:
    if isinstance(value, Path):
        return [value]
    return [Path(item) for item in value]


def _field(row: dict[str, str], key: str) -> str:
    return row.get(key, "").strip()


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    """Data ISO (`2026-03-31`); aceita `dd/mm/aaaa` por robustez, nunca inventa."""
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _year_of(path: Path) -> int | None:
    for part in path.stem.split("_"):
        if part.isdigit() and len(part) == 4:
            return int(part)
    return None


def _url_problem(url: str) -> SourceError | None:
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError):
        parsed = None
    if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.host:
        return SourceError(
            code="source_unavailable",
            message=f"URL inválida para a CVM: {url!r}",
            hint="confira o template em earnings._URL_TEMPLATE (dados.cvm.gov.br)",
        )
    return None


def _declared_length(response: httpx.Response) -> int | None:
    if "content-encoding" in response.headers or "transfer-encoding" in response.headers:
        return None
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_meta(path: Path) -> dict[str, object] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("meta ilegível em %s: %s", path, exc)
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("meta inválida em %s: não é JSON", path)
        return None
    return data if isinstance(data, dict) else None


def _write_meta(path: Path, meta: dict[str, object]) -> None:
    tmp = path.with_name(f"{path.name}.part")
    tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    replace_file(tmp, path)


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _now_iso() -> str:
    return datetime.now(_tz).isoformat(timespec="seconds")  # carimbo de download (excecao documentada, ADR-010) — data de referencia NUNCA sai daqui  # noqa: E501
