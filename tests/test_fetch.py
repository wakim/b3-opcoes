"""Testes do download condicional do COTAHIST (F0.2), sem rede real.

`httpx.MockTransport` cobre 200/304/5xx/4xx/transporte: o que o teste prova é o
comportamento do contrato (meta, `.part` descartado, retry só no transitório e
`SourceError` em vez de exceção).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from b3o.ingest.errors import SourceError
from b3o.ingest.fetch import BROWSER_UA, fetch_cotahist, meta_path

YEAR = 2026
BASE_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/"
PAYLOAD = b"PK\x03\x04" + b"conteudo-do-zip-de-teste" * 128
ETAG = '"5f2c1a7b:0"'  # formato IIS: com aspas, guardar e devolver como veio
LAST_MODIFIED = "Wed, 19 Nov 2026 18:00:00 GMT"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def test_200_baixa_em_streaming_grava_meta_e_nao_deixa_part(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["ua"] = request.headers.get("user-agent")
        seen["if_none_match"] = request.headers.get("if-none-match")
        seen["if_modified_since"] = request.headers.get("if-modified-since")
        return httpx.Response(
            200,
            content=PAYLOAD,
            headers={
                "ETag": ETAG,
                "Last-Modified": LAST_MODIFIED,
                "Content-Length": str(len(PAYLOAD)),
            },
        )

    with _client(handler) as client:
        result = fetch_cotahist(YEAR, tmp_path, BASE_URL, client=client, retry_delays=(0.0, 0.0))

    target = tmp_path / f"COTAHIST_A{YEAR}.ZIP"
    assert result.status == "downloaded"
    assert result.error is None
    assert seen["url"] == f"{BASE_URL}COTAHIST_A{YEAR}.ZIP"
    assert seen["ua"] == BROWSER_UA  # sem UA de browser a B3 responde 520/404
    assert seen["if_none_match"] is None  # sem meta não há GET condicional
    assert seen["if_modified_since"] is None

    assert result.path == target
    assert target.read_bytes() == PAYLOAD
    assert not (tmp_path / f"tmp-COTAHIST_A{YEAR}.ZIP.part").exists()

    assert result.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert result.bytes == len(PAYLOAD)
    assert result.etag == ETAG  # aspas preservadas, como vieram
    assert result.last_modified == LAST_MODIFIED

    assert result.downloaded_at is not None
    moment = datetime.fromisoformat(result.downloaded_at)
    assert moment.tzinfo is not None  # ISO 8601 com offset (P-D)

    meta = json.loads(meta_path(tmp_path, YEAR).read_text(encoding="utf-8"))
    assert meta == {
        "etag": ETAG,
        "last_modified": LAST_MODIFIED,
        "sha256": result.sha256,
        "bytes": len(PAYLOAD),
        "downloaded_at": result.downloaded_at,
    }


def test_meta_com_etag_leva_304_e_nao_toca_no_arquivo(tmp_path: Path) -> None:
    target = tmp_path / f"COTAHIST_A{YEAR}.ZIP"
    target.write_bytes(PAYLOAD)
    sha = hashlib.sha256(PAYLOAD).hexdigest()
    old_meta = {
        "etag": ETAG,
        "last_modified": LAST_MODIFIED,
        "sha256": sha,
        "bytes": len(PAYLOAD),
        "downloaded_at": "2026-11-21T21:30:00-03:00",
    }
    meta_file = meta_path(tmp_path, YEAR)
    meta_file.write_text(json.dumps(old_meta), encoding="utf-8")
    zip_mtime = target.stat().st_mtime_ns
    meta_mtime = meta_file.stat().st_mtime_ns

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["if_none_match"] = request.headers.get("if-none-match")
        seen["if_modified_since"] = request.headers.get("if-modified-since")
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(304)

    with _client(handler) as client:
        result = fetch_cotahist(YEAR, tmp_path, BASE_URL, client=client, retry_delays=(0.0, 0.0))

    assert seen["if_none_match"] == ETAG
    assert seen["if_modified_since"] == LAST_MODIFIED
    assert seen["ua"] == BROWSER_UA

    assert result.status == "not_modified"
    assert result.error is None
    assert result.path == target
    # 304 devolve o que a meta sabia, sem reescrever nada
    assert result.etag == ETAG
    assert result.last_modified == LAST_MODIFIED
    assert result.sha256 == sha
    assert result.bytes == len(PAYLOAD)
    assert result.downloaded_at == "2026-11-21T21:30:00-03:00"

    assert target.read_bytes() == PAYLOAD
    assert json.loads(meta_file.read_text(encoding="utf-8")) == old_meta
    # nem conteúdo nem mtime mudam: 304 não reescreve o zip nem a meta (D5a)
    assert target.stat().st_mtime_ns == zip_mtime
    assert meta_file.stat().st_mtime_ns == meta_mtime


def test_500_tenta_tres_vezes_e_devolve_source_unavailable(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, text="boom")

    with _client(handler) as client:
        result = fetch_cotahist(YEAR, tmp_path, BASE_URL, client=client, retry_delays=(0.0, 0.0))

    assert len(calls) == 3  # 1 tentativa + 2 retries
    assert result.status == "error"
    assert isinstance(result.error, SourceError)
    assert result.error.code == "source_unavailable"
    assert result.error.message
    assert result.error.hint.strip()
    assert result.path is None
    assert not (tmp_path / f"COTAHIST_A{YEAR}.ZIP").exists()


def test_erro_de_transporte_usa_os_mesmos_retries(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("sem rota para bvmf", request=request)

    with _client(handler) as client:
        result = fetch_cotahist(YEAR, tmp_path, BASE_URL, client=client, retry_delays=(0.0,))

    assert len(calls) == 2
    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "source_unavailable"


def test_404_e_permanente_nao_retenta(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, text="nao existe")

    with _client(handler) as client:
        result = fetch_cotahist(YEAR, tmp_path, BASE_URL, client=client, retry_delays=(0.0, 0.0))

    assert len(calls) == 1
    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "source_unavailable"
    assert result.error.hint.strip()


def test_content_length_divergente_vira_erro_e_apaga_part(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=PAYLOAD,
            headers={"Content-Length": str(len(PAYLOAD) + 7)},
        )

    with _client(handler) as client:
        result = fetch_cotahist(YEAR, tmp_path, BASE_URL, client=client, retry_delays=(0.0, 0.0))

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "source_unavailable"
    assert "Content-Length" in result.error.message
    assert not (tmp_path / f"COTAHIST_A{YEAR}.ZIP").exists()
    assert not (tmp_path / f"tmp-COTAHIST_A{YEAR}.ZIP.part").exists()


def test_304_sem_arquivo_local_e_erro_explicito(tmp_path: Path) -> None:
    """Sem zip no disco um 304 seria mentira: o estado local está perdido."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    meta_path(tmp_path, YEAR).write_text(
        json.dumps({"etag": ETAG, "last_modified": None, "sha256": "x", "bytes": 1,
                    "downloaded_at": "2026-11-21T21:30:00-03:00"}),
        encoding="utf-8",
    )

    with _client(handler) as client:
        result = fetch_cotahist(YEAR, tmp_path, BASE_URL, client=client, retry_delays=(0.0, 0.0))

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "source_unavailable"
    assert result.path is None


@pytest.mark.parametrize("base_url", ["", "sem-esquema.br/"])
def test_base_url_invalida_vira_source_error_sem_rede(tmp_path: Path, base_url: str) -> None:
    """D1: URL sem esquema não pode escapar como ValueError/InvalidURL."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise AssertionError(f"não deveria haver chamada de rede: {request.url}")

    with _client(handler) as client:
        result = fetch_cotahist(YEAR, tmp_path, base_url, client=client, retry_delays=(0.0, 0.0))

    assert calls == []  # validação é prévia ao stream
    assert result.status == "error"
    assert result.path is None
    assert isinstance(result.error, SourceError)
    assert result.error.code == "source_unavailable"
    assert "base_url" in result.error.message
    assert result.error.hint.strip()
    assert list(tmp_path.iterdir()) == []  # nenhum zip, .part ou meta criado


def test_republicacao_publica_zip_e_meta_via_replace_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D1: no bind, `os.replace` sobre alvo existente dá EACCES — publicar via fsutil."""
    old_payload = b"PK\x03\x04 versao antiga do zip"
    new_payload = b"PK\x03\x04 versao republicada pela B3 no dia seguinte"

    def first(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=old_payload, headers={"ETag": '"v1:0"'})

    with _client(first) as client:
        first_result = fetch_cotahist(
            YEAR, tmp_path, BASE_URL, client=client, retry_delays=(0.0,)
        )
    assert first_result.status == "downloaded"

    # bind do Windows: rename para nome NOVO passa; replace sobre existente é recusado
    real_replace = os.replace
    refused: list[str] = []

    def replace_eacces(a: object, b: object) -> None:
        target = Path(str(b))
        if target.exists():
            refused.append(target.name)
            raise PermissionError(13, "Permission denied (bind)")
        real_replace(a, b)

    monkeypatch.setattr(os, "replace", replace_eacces)

    def second(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=new_payload, headers={"ETag": '"v2:0"'})

    with _client(second) as client:
        result = fetch_cotahist(YEAR, tmp_path, BASE_URL, client=client, retry_delays=(0.0,))

    assert result.status == "downloaded"
    assert result.error is None
    assert result.etag == '"v2:0"'
    assert (tmp_path / f"COTAHIST_A{YEAR}.ZIP").read_bytes() == new_payload
    meta = json.loads(meta_path(tmp_path, YEAR).read_text(encoding="utf-8"))
    assert meta["sha256"] == hashlib.sha256(new_payload).hexdigest()
    assert meta["etag"] == '"v2:0"'
    # o fallback do replace_file foi exercido nas DUAS publicações: zip e meta
    assert sorted(refused) == [f"COTAHIST_A{YEAR}.ZIP", f"COTAHIST_A{YEAR}.ZIP.meta.json"]
    assert not list(tmp_path.glob("*.part"))
