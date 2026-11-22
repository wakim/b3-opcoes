"""Download condicional do COTAHIST anual (F0.2).

Contrato: `docs/contratos/onda-a.md` §2. Regras que vêm do dado medido:

- `BROWSER_UA` é obrigatório: sem User-Agent de browser a B3 responde 520/404.
- O arquivo anual do ano corrente é republicado **inteiro** todo dia, então o GET
  condicional (`ETag` + `If-Modified-Since`) é o caminho normal: 304 custa zero byte.
- 200 → streaming para `tmp-*.part`, sha256 calculado no fluxo, `os.replace` e meta
  nova. Nada de arquivo pela metade no nome final e nada de meta inconsistente.
- Retry só em `httpx.TransportError` e 5xx, no máximo `1 + len(retry_delays)`
  tentativas; 4xx é permanente (re-tentar não conserta URL errada nem 404).
- Toda falha final vira `FetchResult(status="error")` carregando um `SourceError`
  (P-E): nunca exceção que escape da função, nunca zero silencioso.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from b3o.ingest.errors import SourceError

log = logging.getLogger(__name__)

# Sem UA de browser a B3 devolve 520/404 (medido em 22/11/2026).
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_CHUNK_BYTES = 1 << 20

try:
    _TZ: tzinfo = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:  # imagem sem tzdata; Brasil não tem horário de verão desde 2019
    _TZ = timezone(timedelta(hours=-3))

_HINT_NETWORK = (
    "confira a rede/DNS do container e tente de novo; o COTAHIST exige User-Agent de browser"
)
_HINT_PERMANENT = (
    "erro permanente da fonte: confira a URL viva (bvmf.bmfbovespa.com.br/InstDados/SerHist/) "
    "e se o ano existe; não adianta re-tentar"
)


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Resultado de um download; `status` decide o caminho do prepare (P-E)."""

    status: Literal["downloaded", "not_modified", "error"]
    path: Path | None = None
    etag: str | None = None
    last_modified: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    downloaded_at: str | None = None
    error: SourceError | None = None


def cotahist_path(dest_dir: Path, year: int) -> Path:
    """Caminho do zip final do ano (`COTAHIST_A{year}.ZIP`, extensão maiúscula)."""
    return Path(dest_dir) / f"COTAHIST_A{year}.ZIP"


def meta_path(dest_dir: Path, year: int) -> Path:
    """Caminho da meta local do ano (etag/last_modified/sha256/bytes/downloaded_at)."""
    return Path(dest_dir) / f"COTAHIST_A{year}.ZIP.meta.json"


def fetch_cotahist(
    year: int,
    dest_dir: Path,
    base_url: str,
    *,
    client: httpx.Client,
    retry_delays: tuple[float, ...] = (1.0, 2.0),
) -> FetchResult:
    """Baixa o COTAHIST do ano se a fonte mudou; devolve sempre um `FetchResult`.

    `client` é criado por quem chama (timeout connect 10 s / read 90 s, §2).
    `retry_delays` tem uma entrada por re-tentativa; testes passam `(0.0, 0.0)`.
    """
    dest = Path(dest_dir)
    target = cotahist_path(dest, year)
    meta_file = meta_path(dest, year)
    url = f"{base_url}COTAHIST_A{year}.ZIP"

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _error_result(
            SourceError(
                code="source_unavailable",
                message=f"não foi possível criar {dest}: {exc}",
                hint="confira o bind mount de dados do container (B3_DATA_DIR=/data)",
            )
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
            with client.stream(
                "GET", url, headers=headers, follow_redirects=True
            ) as response:
                status = response.status_code
                if status == 304:
                    if meta is None or not target.is_file():
                        return _error_result(
                            SourceError(
                                code="source_unavailable",
                                message=f"HTTP 304 em {url} sem zip/meta local correspondente",
                                hint=(
                                    "apague data/raw e rode o prepare de novo "
                                    "(estado local perdido)"
                                ),
                            )
                        )
                    log.info("COTAHIST %s: 304 not modified (%s)", year, target.name)
                    return _not_modified_result(target, meta)
                if status == 200:
                    return _download(response, target, meta_file)
                if status >= 500:
                    last_error = SourceError(
                        code="source_unavailable",
                        message=f"HTTP {status} em {url}",
                        hint="a B3 está fora do ar ou em manutenção; re-tentar mais tarde",
                    )
                else:
                    return _error_result(
                        SourceError(
                            code="source_unavailable",
                            message=f"HTTP {status} em {url}",
                            hint=_HINT_PERMANENT,
                        )
                    )
        except httpx.TransportError as exc:
            last_error = SourceError(
                code="source_unavailable",
                message=f"falha de transporte em {url}: {type(exc).__name__}: {exc}",
                hint=_HINT_NETWORK,
            )
        except httpx.HTTPError as exc:
            return _error_result(
                SourceError(
                    code="source_unavailable",
                    message=f"erro HTTP em {url}: {type(exc).__name__}: {exc}",
                    hint=_HINT_NETWORK,
                )
            )

        if attempt < attempts:
            delay = retry_delays[attempt - 1]
            log.warning(
                "COTAHIST %s: tentativa %d/%d falhou (%s); nova tentativa em %.1fs",
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
    log.error("COTAHIST %s: desisti após %d tentativa(s): %s", year, attempts, error.message)
    return _error_result(error)


def _download(
    response: httpx.Response, target: Path, meta_file: Path
) -> FetchResult:
    """Grava o corpo de um 200 em `.part`, confere tamanho, renomeia e grava a meta."""
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
        return _error_result(
            SourceError(
                code="source_unavailable",
                message=f"corpo incompleto de {response.request.url}: {type(exc).__name__}: {exc}",
                hint=_HINT_NETWORK,
            )
        )
    except OSError as exc:
        part.unlink(missing_ok=True)
        return _error_result(
            SourceError(
                code="source_unavailable",
                message=f"falha de disco ao gravar {part}: {exc}",
                hint="confira espaço/permissão do volume de dados do container",
            )
        )

    declared = _declared_length(response)
    if declared is not None and declared != written:
        part.unlink(missing_ok=True)
        return _error_result(
            SourceError(
                code="source_unavailable",
                message=(
                    f"Content-Length declarou {declared} bytes mas chegaram {written} "
                    f"de {response.request.url}"
                ),
                hint="download truncado; rode o prepare de novo (o .part foi descartado)",
            )
        )

    try:
        os.replace(part, target)
    except OSError as exc:
        part.unlink(missing_ok=True)
        return _error_result(
            SourceError(
                code="source_unavailable",
                message=f"falha ao renomear {part} para {target}: {exc}",
                hint="confira espaço/permissão do volume de dados do container",
            )
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
        # O zip vale; sem meta o próximo prepare só paga um 200 de novo (nunca dado errado).
        log.warning(
            "COTAHIST %s: zip salvo, meta não gravada em %s: %s", target.name, meta_file, exc
        )

    log.info("COTAHIST %s: baixado (%d bytes, sha256=%s)", target.name, written, meta["sha256"])
    return FetchResult(
        status="downloaded",
        path=target,
        etag=_as_str(meta["etag"]),
        last_modified=_as_str(meta["last_modified"]),
        sha256=_as_str(meta["sha256"]),
        bytes=written,
        downloaded_at=_as_str(meta["downloaded_at"]),
        error=None,
    )


def _not_modified_result(target: Path, meta: dict[str, object]) -> FetchResult:
    size = meta.get("bytes")
    return FetchResult(
        status="not_modified",
        path=target,
        etag=_as_str(meta.get("etag")),
        last_modified=_as_str(meta.get("last_modified")),
        sha256=_as_str(meta.get("sha256")),
        bytes=size if isinstance(size, int) and not isinstance(size, bool) else None,
        downloaded_at=_as_str(meta.get("downloaded_at")),
        error=None,
    )


def _error_result(error: SourceError) -> FetchResult:
    return FetchResult(status="error", path=None, error=error)


def _declared_length(response: httpx.Response) -> int | None:
    """`Content-Length` do corpo; ignora se houver codificação (o tamanho seria o comprimido)."""
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
    """Lê a meta local; ilegível/ausente devolve None (o GET vira 200, não erro)."""
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
    if not isinstance(data, dict):
        log.warning("meta inválida em %s: não é objeto", path)
        return None
    return data


def _write_meta(path: Path, meta: dict[str, object]) -> None:
    """Grava a meta de forma atômica (tmp + os.replace)."""
    tmp = path.with_name(f"{path.name}.part")
    tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _now_iso() -> str:
    """Instante do download, ISO 8601 com offset de America/Sao_Paulo (P-D)."""
    return datetime.now(_TZ).isoformat(timespec="seconds")
