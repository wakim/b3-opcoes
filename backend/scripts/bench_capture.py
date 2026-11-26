"""Captura futura do benchmark externo F10 (pacote P3) — mecanismo, não execução.

Este módulo é o instrumento de captura da sessão do lake. Nesta retomada (26/11/2026)
ele é validado **só estaticamente** (``py_compile``, ``--help``, ``ruff``); nenhum
site externo é aberto. A execução real fica para uma sessão futura, com ``--execute``.

Contrato (PLAN.md §F10, "Execução")
-----------------------------------
- Amostra fixa: PETR4, VALE3, BOVA11, B3SA3, ITUB4; dois vencimentos mensais por
  papel (o do ``smile_maturity`` e o próximo mensal posterior). O vencimento vem de
  ``/api/t/{ticker}?sections=term,smile`` e do atributo ``data-m`` da grade do
  opcoes.net.br — **nunca** de prefixo/ticker de opção.
- ``/api/health`` é lido antes de qualquer página externa e de novo depois; a
  sessão pedida tem que bater com ``as_of_session`` e ``last_session`` do lake.
- OBM: só páginas públicas; respostas ``/api/v1/`` são **interceptadas**, nunca
  chamadas diretamente (robots.txt bloqueia ``/api/``). HTML/RSC é preservado
  byte a byte; o que é extraído vai em ``extracted`` sem alterar o bruto.
- opcoes.net.br: ``quotesMode=UP``, checkbox ``#vAAAA-MM-DD``, espera a contagem
  do DataTable estabilizar, leitura de ``rows().data().toArray()`` e cabeçalho.
  A IV lida é **fração** e não é normalizada aqui.
- Publicação atômica: tudo vai para uma área temporária; só depois de todas as
  guardas (inclusive o health final) o diretório final é substituído. Em falha a
  temporária é removida e nenhum manifesto de sucesso parcial existe.
- Ausência é ``null`` + motivo. Turnstile/Cloudflare é falha explícita (``turnstile``),
  sem bypass. Idempotência por ``(sessão, amostra, versão do plano)``; ``--force``
  recaptura renomeando a saída anterior para ``.bak-<timestamp>``.

Nenhum número de relatório é produzido aqui: o script grava bruto + metadados.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

PLAN_VERSION = "f10-p3-v1"
SAMPLE: tuple[str, ...] = ("PETR4", "VALE3", "BOVA11", "B3SA3", "ITUB4")
OBM_ORIGIN = "https://obm.com.br"
OPCOES_ORIGIN = "https://opcoes.net.br"
DEFAULT_HTTP_URL = "http://app:8080"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
DATE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_TOKENS = (
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"),
    re.compile(r"(?P<d>\d{2})/(?P<m>\d{2})/(?P<y>\d{4})"),
)
# Só campos de envelope explícitos contam como data de referência. "date"/"data"
# genérico de cada linha (vi-historico, OHLC) NÃO entra: são histórico, não pregão.
REF_KEYS = {
    "referencedate",
    "refdate",
    "refdata",
    "datareferencia",
    "dtrefer",
    "asof",
    "asofsession",
    "lastsession",
    "dataref",
}
REF_CONTAINERS = ("data", "latest", "meta", "result", "payload")
# Fallback textual exige associação explícita à referência/ao pregão — nunca
# uma data solta em série histórica.
REF_TEXT = re.compile(
    r"(?i)(?:refer[êe]ncia|data\s*/?\s*hora|fechamento\s+das\s+op[çc][õo]es|"
    r"fechamento\s+do\s+preg[ãa]o|preg[ãa]o\s+de\s+refer[êe]ncia)"
    r"[^0-9]{0,40}?([0-9]{2,4}[-/][0-9]{2}[-/][0-9]{2,4})"
)
# Evidência local (sem rede) de que a página pública aceita ?venc= e que o
# seletor da UI dispara o fetch interceptado:
# data/bench/2026-11-26/obm-underlying-chunk.js ->
#   `initialVenc:t.get("venc")??void 0`
#   `window.history.replaceState(null,"",`/opcoes/${c}?venc=${e}`)`
#   `fetch(`/api/v1/opcoes/${encodeURIComponent(C.underlying)}${t}`...)`
OBM_VENC_QUERY_PROVEN = "obm-underlying-chunk.js: useSearchParams('venc') + replaceState"
TURNSTILE_MARKERS = (
    "um momento",
    "executando verificação de segurança",
    "just a moment",
    "verifying you are human",
    "cf-challenge",
    "turnstile",
    "attention required! | cloudflare",
)
OBM_PUBLIC_PAGES: tuple[tuple[str, str], ...] = (
    ("opcoes", "underlying"),
    ("vi-historico", "vi_historico"),
    ("volatilidade", "volatilidade"),
    ("acoes", "ohlc"),
)
OBM_GLOBAL_PAGES: tuple[tuple[str, str], ...] = (
    ("/tesouro-direto/curva", "ettj"),
    ("/calendario-dividendos", "dividendos"),
)


class CaptureError(Exception):
    """Falha explícita: ``code``/``message``/``hint`` (nunca zero silencioso)."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def payload(self) -> dict[str, dict[str, str]]:
        return {
            "error": {"code": self.code, "message": self.message, "hint": self.hint}
        }


def now_iso() -> str:
    """ISO com offset — o offset vem do ``TZ=America/Sao_Paulo`` do container."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def parse_iso_day(value: str, label: str) -> date:
    if not isinstance(value, str) or not DATE_ISO.match(value):
        raise CaptureError(
            "bad_date",
            f"{label} deve ser AAAA-MM-DD, recebido {value!r}",
            "Passe --date AAAA-MM-DD com uma sessão existente no lake.",
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CaptureError(
            "bad_date",
            f"{label} não é uma data válida: {value!r}",
            "Confira o calendário; feriado/fim de semana não é sessão.",
        ) from exc


def parse_day_token(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    for pattern in DATE_TOKENS:
        found = pattern.search(value)
        if found is None:
            continue
        try:
            return date(int(found["y"]), int(found["m"]), int(found["d"]))
        except ValueError:
            continue
    return None


def normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def ref_dates_from_json(node: Any) -> set[date]:
    """Envelope explícito apenas: nunca recursa em listas de histórico/séries.

    Varredura limitada ao objeto raiz e a um nível de contêineres nomeados
    (``data``/``latest``/``meta``/...). ``date``/``data`` de linha e datas de
    vencimento ficam fora — uma série histórica falsaria a guarda.
    """
    found: set[date] = set()
    if not isinstance(node, dict):
        return found

    def scan(mapping: dict[str, Any]) -> None:
        for key, value in mapping.items():
            if normalized_key(key) in REF_KEYS:
                day = parse_day_token(value)
                if day is not None:
                    found.add(day)

    scan(node)
    for container in REF_CONTAINERS:
        child = node.get(container)
        if isinstance(child, dict):
            scan(child)
    return found


def ref_dates_from_text(text: str) -> set[date]:
    found: set[date] = set()
    for match in REF_TEXT.finditer(text):
        day = parse_day_token(match.group(1))
        if day is not None:
            found.add(day)
    return found


@dataclass
class Artifact:
    """Um bruto capturado, com proveniência completa para o manifesto."""

    name: str
    url: str
    source: str
    kind: str
    body: bytes
    session: str
    content_type: str | None = None
    http_status: int | None = None
    accessed_at: str = field(default_factory=now_iso)
    ticker: str | None = None
    maturity: str | None = None
    loaded_by: str | None = None
    intercepted: bool = False
    extracted: dict[str, Any] | None = None
    reference_date: str | None = None
    note: str | None = None

    def finalize(self) -> None:
        """Descobre/valida a data de referência contra a sessão do lake."""
        if self.reference_date is not None:
            return
        found = ref_dates_from_json(_decoded_json(self.body))
        text_blob = self.body.decode("utf-8", "replace")
        found |= ref_dates_from_text(text_blob)
        if self.extracted:
            found |= ref_dates_from_json(self.extracted)
            page_text = self.extracted.get("page_text")
            if isinstance(page_text, str):
                found |= ref_dates_from_text(page_text)
        if not found:
            raise CaptureError(
                "reference_date_missing",
                f"{self.name}: nenhuma data de referência explícita encontrada",
                "A captura não inventa data; verifique se a página pública traz "
                "a data do pregão ou se o seletor de referência mudou.",
            )
        bad = sorted(day.isoformat() for day in found if day.isoformat() != self.session)
        if bad:
            raise CaptureError(
                "reference_date_mismatch",
                f"{self.name}: fonte traz {bad}, lake está em {self.session}",
                "Abortando sem publicar: OBM/opcoes.net.br só têm o último pregão; "
                "reexecute quando a sessão do lake casar com a das fontes.",
            )
        self.reference_date = self.session

    def to_manifest(self, temp_dir: Path) -> dict[str, Any]:
        """Escreve o bruto e devolve a entrada do manifesto."""
        looks_json = "json" in (self.content_type or "").lower() or self.body.lstrip()[
            :1
        ] in (b"{", b"[")
        raw_name = f"{self.name}.json" if looks_json else f"{self.name}.html"
        (temp_dir / raw_name).write_bytes(self.body)
        entry: dict[str, Any] = {
            "artifact": raw_name,
            "url": self.url,
            "source": self.source,
            "kind": self.kind,
            "ticker": self.ticker,
            "maturity": self.maturity,
            "accessed_at": self.accessed_at,
            "reference_date": self.reference_date,
            "sha256": sha256_hex(self.body),
            "status": "ok",
            "reason": None,
            "content_type": self.content_type,
            "http_status": self.http_status,
            "bytes": len(self.body),
            "intercepted": self.intercepted,
            "loaded_by": self.loaded_by,
            "note": self.note,
        }
        if not looks_json:
            meta_name = f"{self.name}.json"
            wrapper = {
                "raw_file": raw_name,
                "raw_sha256": sha256_hex(self.body),
                "extracted": self.extracted or {},
                "preserved_verbatim": True,
            }
            (temp_dir / meta_name).write_text(
                json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            entry["meta_file"] = meta_name
        return entry


def _decoded_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except (ValueError, TypeError):
        return None


def http_get_json(url: str, label: str) -> tuple[bytes, dict[str, Any]]:
    """GET interno (health/underlying) com stdlib; nunca toca em site externo."""
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - URL controlada
            raw = response.read()
    except HTTPError as exc:
        raise CaptureError(
            f"{label}_http_error",
            f"{label}: HTTP {exc.code} em {url}",
            "Confira se o serviço app está no ar e se B3_HTTP_URL aponta para ele.",
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CaptureError(
            f"{label}_unavailable",
            f"{label}: falha de rede em {url}: {exc}",
            "Use B3_HTTP_URL (default http://app:8080) e rode a captura pelo compose.",
        ) from exc
    parsed = _decoded_json(raw)
    if not isinstance(parsed, dict):
        raise CaptureError(
            f"{label}_bad_json",
            f"{label}: resposta não é um objeto JSON",
            "O envelope da API deve vir de /api/health e /api/t/{ticker}.",
        )
    return raw, parsed


def health_check(
    http_url: str,
    requested: str,
    label: str,
) -> tuple[bytes, dict[str, Any], Artifact]:
    """Lê /api/health e exige que a sessão pedida seja a última do lake."""
    url = f"{http_url.rstrip('/')}/api/health"
    raw, payload = http_get_json(url, label)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    session = payload.get("as_of_session")
    last = data.get("last_session")
    model_version = payload.get("model_version")
    if session is None or last is None or model_version is None:
        raise CaptureError(
            "health_incomplete",
            f"{label}: faltam as_of_session/last_session/model_version",
            "Atualize o lake (b3o prepare) e confirme /api/health antes de capturar.",
        )
    if session != requested or last != requested:
        raise CaptureError(
            "session_mismatch",
            f"{label}: pedido {requested}, health traz as_of_session={session}, "
            f"last_session={last}",
            "A captura só vale para a última sessão do lake; ajuste --date.",
        )
    if not isinstance(model_version, int):
        raise CaptureError(
            "health_incomplete",
            f"{label}: model_version não é inteiro: {model_version!r}",
            "O envelope da API deve trazer model_version numérico.",
        )
    artifact = Artifact(
        name=f"health_{label}",
        url=url,
        source="local",
        kind="health",
        body=raw,
        session=requested,
        content_type="application/json",
        http_status=200,
        reference_date=str(session),
        note=f"as_of_session={session}; last_session={last}; model_version={model_version}",
    )
    return raw, payload, artifact


def read_expiries(
    http_url: str, ticker: str, requested: str
) -> tuple[Artifact, dict[str, Any]]:
    """Vencimentos vêm da API local; nunca do ticker da opção."""
    query = urlencode({"date": requested, "sections": "term,smile"})
    url = f"{http_url.rstrip('/')}/api/t/{ticker}?{query}"
    raw, payload = http_get_json(url, "underlying")
    if payload.get("as_of_session") != requested:
        raise CaptureError(
            "session_mismatch",
            f"{ticker}: /api/t traz {payload.get('as_of_session')!r}, esperado {requested}",
            "A amostra deve usar a mesma sessão do health.",
        )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    smile = data.get("smile_maturity")
    term = data.get("term") if isinstance(data.get("term"), list) else []
    maturities = sorted(
        {
            row["maturity"]
            for row in term
            if isinstance(row, dict) and isinstance(row.get("maturity"), str)
        }
    )
    if smile is not None and not DATE_ISO.match(str(smile)):
        raise CaptureError(
            "underlying_bad_maturity",
            f"{ticker}: smile_maturity inválido {smile!r}",
            "A API deve devolver o vencimento em ISO AAAA-MM-DD.",
        )
    artifact = Artifact(
        name=f"local_underlying_{ticker}",
        url=url,
        source="local",
        kind="underlying",
        body=raw,
        session=requested,
        content_type="application/json",
        http_status=200,
        reference_date=requested,
        ticker=ticker,
        note=f"smile_maturity={smile}; n_term_maturities={len(maturities)}",
    )
    return artifact, {"smile_maturity": smile, "term_maturities": maturities}


def build_plan(requested: str, output_dir: Path) -> dict[str, Any]:
    """Plano determinístico, impresso sem rede quando não há --execute."""
    steps: list[dict[str, Any]] = []
    steps.append(
        {
            "step": "health_before",
            "kind": "local",
            "url": f"{DEFAULT_HTTP_URL}/api/health",
            "guard": "as_of_session == last_session == --date",
        }
    )
    for ticker in SAMPLE:
        steps.append(
            {
                "step": "underlying",
                "kind": "local",
                "ticker": ticker,
                "url": f"{DEFAULT_HTTP_URL}/api/t/{ticker}",
                "params": {"date": requested, "sections": "term,smile"},
                "selects": ["smile_maturity", "next_monthly_after_smile"],
                "guard": "nunca inferir vencimento por prefixo/ticker de opção",
            }
        )
    # 1) grade primeiro: só data-m="1" confirma o próximo mensal
    for ticker in SAMPLE:
        steps.append(
            {
                "step": "opcoes_net_grid",
                "kind": "grid",
                "ticker": ticker,
                "url": f"{OPCOES_ORIGIN}/opcoes/bovespa/{ticker}",
                "actions": [
                    "select_option select[name=quotesMode] = UP",
                    "ler data-m dos checkboxes e escolher o próximo mensal",
                    "check #v{smile_maturity} e #v{next_monthly}",
                    "esperar rows().count() estabilizar",
                    "rows().data().toArray() + cabeçalho",
                ],
                "guard": "IV bruta é fração; não normalizar",
            }
        )
    # 2) OBM depois: ?venc= na própria página pública, nunca /api/v1 direto
    for ticker in SAMPLE:
        slug = ticker.lower()
        for suffix, kind in OBM_PUBLIC_PAGES:
            steps.append(
                {
                    "step": "obm_public_page",
                    "kind": kind,
                    "ticker": ticker,
                    "url": f"{OBM_ORIGIN}/{'opcoes' if suffix != 'acoes' else 'acoes'}"
                    f"/{slug}"
                    + (f"/{suffix}" if suffix != "opcoes" and suffix != "acoes" else ""),
                    "intercept": "respostas /api/v1/ carregadas pela página",
                    "guard": "sem chamada direta a /api/v1",
                }
            )
        steps.append(
            {
                "step": "obm_chain_for_required_expiries",
                "kind": "underlying",
                "ticker": ticker,
                "url": (
                    f"{OBM_ORIGIN}/opcoes/{slug}?venc={{smile_maturity}} e "
                    f"?venc={{next_monthly}}"
                ),
                "evidence": OBM_VENC_QUERY_PROVEN,
                "intercept": "cadeia carregada pela própria página pública",
                "guard": "required_artifact_missing se alguma cadeia não for interceptada",
            }
        )
    for path, kind in OBM_GLOBAL_PAGES:
        steps.append(
            {
                "step": "obm_public_page",
                "kind": kind,
                "ticker": None,
                "url": f"{OBM_ORIGIN}{path}",
                "intercept": "respostas /api/v1/ carregadas pela página",
            }
        )
    steps.append(
        {
            "step": "health_after",
            "kind": "local",
            "url": f"{DEFAULT_HTTP_URL}/api/health",
            "guard": "sessão e model_version iguais aos do health antes",
        }
    )
    return {
        "plan_version": PLAN_VERSION,
        "session": requested,
        "sample": list(SAMPLE),
        "output_dir": str(output_dir),
        "execute": False,
        "network": False,
        "browser": False,
        "steps": steps,
        "note": "Modo plano: nenhuma rede, nenhum navegador. Use --execute na imagem bench.",
    }


def manifest_is_complete(
    manifest_path: Path, session: str, sample: tuple[str, ...]
) -> bool:
    """Só reaproveita se o manifesto e TODOS os brutos declarados ainda batem."""
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    basic = (
        manifest.get("status") == "complete"
        and manifest.get("session") == session
        and manifest.get("plan_version") == PLAN_VERSION
        and list(manifest.get("sample") or []) == list(sample)
        and isinstance(manifest.get("artifacts"), list)
        and bool(manifest["artifacts"])
    )
    if not basic:
        return False
    for entry in manifest["artifacts"]:
        if not isinstance(entry, dict):
            return False
        raw_name = entry.get("artifact")
        if not isinstance(raw_name, str) or not raw_name:
            return False
        raw_path = manifest_path.parent / raw_name
        if not raw_path.is_file():
            return False
        expected = entry.get("sha256")
        if not isinstance(expected, str) or sha256_hex(raw_path.read_bytes()) != expected:
            return False
        meta_name = entry.get("meta_file")
        if meta_name and not (manifest_path.parent / str(meta_name)).is_file():
            return False
    return True


class ResponseCollector:
    """Guarda respostas disparadas pelas páginas, sem chamar a API direto."""

    def __init__(self) -> None:
        self.current_page = ""
        self.items: list[tuple[str, Any]] = []

    def on_response(self, response: Any) -> None:
        self.items.append((self.current_page, response))

    def take(self) -> list[tuple[str, Any]]:
        taken = self.items
        self.items = []
        return taken


def pause_between_pages() -> None:
    """Pausa aleatória de 1–2 s; uma página por vez, sem paralelismo."""
    time.sleep(random.uniform(1.0, 2.0))


def goto_public(page: Any, url: str, collector: ResponseCollector) -> Any:
    """Navega e devolve a Response da página pública (para ler o HTML bruto)."""
    pause_between_pages()
    collector.current_page = url
    try:
        response = page.goto(url, wait_until="load", timeout=60_000)
    except Exception as exc:
        raise CaptureError(
            "page_navigation_failed",
            f"falha ao abrir {url}: {exc}",
            "Página pública fora do ar ou lenta; reexecute. Sem bypass de desafio.",
        ) from exc
    check_turnstile(page, url)
    page.wait_for_timeout(1500)
    check_turnstile(page, url)
    return response


def navigation_body(response: Any, url: str) -> bytes:
    """Corpo exato da resposta de navegação — nunca DOM serializado."""
    if response is None:
        raise CaptureError(
            "navigation_body_missing",
            f"{url}: a navegação não devolveu Response",
            "Sem corpo bruto não há artefato auditável; reexecute a página.",
        )
    try:
        return response.body()
    except Exception as exc:
        raise CaptureError(
            "navigation_body_missing",
            f"{url}: não foi possível ler o corpo da navegação: {exc}",
            "Use o corpo da Response; page.content() é DOM serializado e não serve.",
        ) from exc


def check_turnstile(page: Any, url: str) -> None:
    try:
        title = (page.title() or "").lower()
        body = (page.locator("body").inner_text()[:3000] or "").lower()
    except Exception as exc:
        raise CaptureError(
            "turnstile_check_failed",
            f"{url}: não foi possível inspecionar a página para Turnstile: {exc}",
            "Sem inspeção não há como afirmar que a navegação não foi "
            "interceptada; falha explícita, sem tratar como sucesso e sem "
            "bypass de desafio.",
        ) from exc
    blob = f"{title}\n{body}"
    if any(marker in blob for marker in TURNSTILE_MARKERS):
        raise CaptureError(
            "turnstile",
            f"{url}: Cloudflare/Turnstile interceptou a navegação",
            "Falha explícita, sem bypass: capture no navegador do dono e reexecute; "
            "nunca trate o desafio como sucesso.",
        )


def unique_artifact_name(base: str, artifacts: list[Artifact]) -> str:
    """Nome determinístico e único entre os artefatos já acumulados.

    Duas respostas com a mesma URL/maturidade não podem sobrescrever o mesmo
    bruto nem gerar entradas de manifesto apontando para arquivo sobrescrito.
    O sufixo é contado a partir da lista ``artifacts`` — sem aleatoriedade.
    """
    taken = {item.name for item in artifacts}
    if base not in taken:
        return base
    counter = 2
    while f"{base}-{counter}" in taken:
        counter += 1
    return f"{base}-{counter}"


def save_intercepted(
    collector: ResponseCollector,
    session: str,
    artifacts: list[Artifact],
    kind_hint: str,
    ticker: str | None,
) -> list[str]:
    """Salva só respostas /api/v1/ que a página pública carregou."""
    captured_maturities: list[str] = []
    for loaded_by, response in collector.take():
        response_url = str(response.url)
        if "/api/v1/" not in urlparse(response_url).path:
            continue
        query = parse_qs(urlparse(response_url).query)
        maturity = (query.get("venc") or query.get("maturity") or [None])[0]
        try:
            body = response.body()
            status = response.status
            headers = response.headers or {}
        except Exception as exc:
            raise CaptureError(
                "intercept_failed",
                f"não foi possível ler {response_url}: {exc}",
                "A resposta pode ter sido descartada pelo navegador; repita a página.",
            ) from exc
        segment = urlparse(response_url).path.rstrip("/").split("/")[-1] or "api"
        suffix = f"_{maturity}" if maturity else ""
        label = f"{ticker.lower()}_" if ticker else ""
        note = "carregada pela página pública; NÃO chamada diretamente"
        if "agregado" in segment:
            note += (
                "; fields[] preservado no bruto, inclusive a descrição de "
                "skew_25d_30d_ratio (definição pendente, F10.1)"
            )
        base = f"obm_{kind_hint}_{label}{segment}{suffix}"
        artifact = Artifact(
            name=unique_artifact_name(base, artifacts),
            url=response_url,
            source="obm",
            kind=kind_hint,
            body=body,
            session=session,
            content_type=headers.get("content-type"),
            http_status=status,
            ticker=ticker,
            maturity=maturity,
            loaded_by=loaded_by,
            intercepted=True,
            note=note,
        )
        artifact.finalize()
        artifacts.append(artifact)
        if maturity:
            captured_maturities.append(maturity)
    return sorted(set(captured_maturities))


def capture_obm(
    page: Any,
    collector: ResponseCollector,
    session: str,
    artifacts: list[Artifact],
    expiries: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Abre só páginas públicas do OBM e intercepta a cadeia de cada vencimento.

    O chunk local ``obm-underlying-chunk.js`` prova que a própria página pública
    lê ``?venc=`` (``useSearchParams``) e reescreve a URL com ``replaceState``.
    Portanto ``/opcoes/{slug}?venc=AAAA-MM-DD`` é página pública, não API direta:
    o fetch ``/api/v1/opcoes/{slug}?venc=…`` é disparado por ela e interceptado.
    """
    captured: dict[str, list[str]] = {}
    for ticker in SAMPLE:
        slug = ticker.lower()
        info = expiries[ticker]
        required = [
            day
            for day in (info.get("smile_maturity"), info.get("next_monthly"))
            if isinstance(day, str) and day
        ]
        pages: list[tuple[str, str, str | None]] = [
            (f"/opcoes/{slug}", "underlying", None)
        ]
        for maturity in required:
            pages.append((f"/opcoes/{slug}?venc={maturity}", "underlying", maturity))
        for suffix, kind in OBM_PUBLIC_PAGES:
            if suffix in ("opcoes", "acoes"):
                continue
            pages.append((f"/opcoes/{slug}/{suffix}", kind, None))
        pages.append((f"/acoes/{slug}", "ohlc", None))

        captured[ticker] = []
        for path, kind, wanted in pages:
            url = f"{OBM_ORIGIN}{path}"
            response = goto_public(page, url, collector)
            body = navigation_body(response, url)
            extracted = {
                "rsc_push_count": body.count(b"self.__next_f.push"),
                "raw_html_preserved": True,
                "venc_query": wanted,
            }
            name = f"obm_{kind}_{slug}_page"
            if wanted:
                name += f"_venc_{wanted}"
            artifact = Artifact(
                name=name,
                url=url,
                source="obm",
                kind=kind,
                body=body,
                session=session,
                content_type=(
                    response.headers.get("content-type")
                    if hasattr(response, "headers")
                    else "text/html; charset=utf-8"
                )
                or "text/html; charset=utf-8",
                http_status=response.status if hasattr(response, "status") else 200,
                ticker=ticker,
                maturity=wanted,
                extracted=extracted,
                note=(
                    "HTML/RSC bruto da Response de navegação; extração não altera "
                    f"o original. {OBM_VENC_QUERY_PROVEN}"
                    if wanted
                    else "HTML/RSC bruto da Response de navegação; extração não "
                    "altera o original"
                ),
            )
            artifact.finalize()
            artifacts.append(artifact)
            for maturity in save_intercepted(
                collector, session, artifacts, kind, ticker
            ):
                if maturity not in captured[ticker]:
                    captured[ticker].append(maturity)
        captured[ticker].sort()
        for wanted in required:
            has_chain = any(
                item.intercepted
                and item.source == "obm"
                and item.kind == "underlying"
                and item.ticker == ticker
                and item.maturity == wanted
                for item in artifacts
            )
            if not has_chain:
                raise CaptureError(
                    "required_artifact_missing",
                    f"{ticker}: cadeia de {wanted} não foi interceptada",
                    "A página pública com ?venc= deve carregar "
                    "/api/v1/opcoes/{slug}?venc=…; sem interceptação não há "
                    "manifesto completo. Nunca chamar /api/v1 direto.",
                )
    for path, kind in OBM_GLOBAL_PAGES:
        url = f"{OBM_ORIGIN}{path}"
        response = goto_public(page, url, collector)
        body = navigation_body(response, url)
        artifact = Artifact(
            name=f"obm_{kind}_page",
            url=url,
            source="obm",
            kind=kind,
            body=body,
            session=session,
            content_type="text/html; charset=utf-8",
            http_status=200,
            extracted={"raw_html_preserved": True},
            note="HTML bruto da Response de navegação; extração não altera o original",
        )
        artifact.finalize()
        artifacts.append(artifact)
        save_intercepted(collector, session, artifacts, kind, None)
    return captured


def pick_next_monthly(
    smile: str | None,
    monthly_on_grid: set[str],
    term_maturities: list[str],
) -> tuple[str | None, list[str]]:
    """Próximo mensal = menor data > smile com ``data-m="1"`` na grade pública.

    Regra calendarística equivalente documentada em ``f10_1-verificacoes.md:100``:
    o checkbox traz ``data-m="1"`` (mensal) e ``data-w="W4"`` (semanal). O mero
    primeiro vencimento posterior do ``term`` não serve — pode ser semanal.
    """
    if not smile:
        return None, ["no_smile_maturity"]
    candidates = sorted(
        day
        for day in monthly_on_grid
        if day > smile and day in term_maturities
    )
    if not candidates:
        return None, ["no_next_monthly_after_smile"]
    return candidates[0], []


def wait_datatable_count(page: Any, timeout_s: float = 25.0) -> int:
    """Espera rows().count() estabilizar (duas leituras iguais)."""
    script = (
        "() => { const j = window.jQuery || window.$; if (!j) return null;"
        " const t = j('#tblListaOpc').DataTable();"
        " return t ? t.rows().count() : null; }"
    )
    last: int | None = None
    stable = 0
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        value = page.evaluate(script)
        if value is None:
            time.sleep(0.4)
            continue
        count = int(value)
        stable = stable + 1 if count == last else 0
        last = count
        if stable >= 2:
            return count
        time.sleep(0.7)
    raise CaptureError(
        "datatable_unstable",
        f"contagem de #tblListaOpc não estabilizou em {timeout_s:.0f}s",
        "Confira quotesMode=UP e os checkboxes; não gravar linhas parciais.",
    )


READ_GRID = """() => {
  const j = window.jQuery || window.$;
  const t = j('#tblListaOpc').DataTable();
  const columns = Array.from(
    document.querySelectorAll('#tblListaOpc thead th')
  ).map(th => th.innerText.trim());
  const boxes = Array.from(
    document.querySelectorAll('#listavencimentos input[type="checkbox"]')
  ).map(e => ({id: e.id, value: e.value, du: e.getAttribute('data-du'),
               m: e.getAttribute('data-m'), w: e.getAttribute('data-w'),
               checked: e.checked}));
  return {
    columns: columns,
    rows: t.rows().data().toArray(),
    row_count: t.rows().count(),
    quotes_mode: (document.querySelector('select[name="quotesMode"]') || {}).value || null,
    checked_boxes: boxes.filter(b => b.checked).map(b => b.value),
    boxes: boxes,
    page_text: (document.body.innerText || '').slice(0, 100000)
  };
}"""


def capture_opcoes_net(
    page: Any,
    collector: ResponseCollector,
    session: str,
    ticker: str,
    smile: str | None,
    term_maturities: list[str],
    artifacts: list[Artifact],
) -> tuple[str | None, list[str]]:
    """Grade EOD do opcoes.net.br; agrade descoberta vem ANTES do OBM."""
    url = f"{OPCOES_ORIGIN}/opcoes/bovespa/{ticker}"
    response = goto_public(page, url, collector)
    try:
        page.select_option('select[name="quotesMode"]', "UP")
    except Exception as exc:
        raise CaptureError(
            "quotes_mode_failed",
            f"{ticker}: não foi possível forçar quotesMode=UP",
            "Último pregão é obrigatório; tempo real não entra no benchmark.",
        ) from exc
    boxes = page.locator("#listavencimentos input[type=\"checkbox\"]").evaluate_all(
        "(els) => els.map(e => ({id: e.id, value: e.value, "
        "m: e.getAttribute('data-m'), checked: e.checked}))"
    )
    monthly = {str(b["value"]) for b in boxes if str(b.get("m")) == "1"}
    month_after, flags = pick_next_monthly(smile, monthly, term_maturities)
    selected = [day for day in (smile, month_after) if day]
    available = {str(b["value"]) for b in boxes}
    for day in selected:
        if day not in available:
            flags.append(f"expiry_not_on_grid:{day}")
    real_selected = [day for day in selected if day in available]
    try:
        for box in boxes:
            if box.get("checked"):
                page.uncheck(f"#{box['id']}")
        for day in real_selected:
            page.check(f"#v{day}")
    except Exception as exc:
        raise CaptureError(
            "expiry_checkbox_failed",
            f"{ticker}: falha ao marcar os vencimentos {real_selected}",
            "Use #vAAAA-MM-DD com clique nativo; não dispatchEvent.",
        ) from exc
    count = wait_datatable_count(page)
    grid = page.evaluate(READ_GRID)
    if int(grid.get("row_count", -1)) != count:
        flags.append("row_count_changed_after_read")
    if grid.get("quotes_mode") != "UP":
        raise CaptureError(
            "quotes_mode_changed",
            f"{ticker}: quotesMode voltou para {grid.get('quotes_mode')!r}",
            "Nunca capturar em tempo real; recarregue e reforce UP.",
        )
    extracted = {
        "rows": grid["rows"],
        "columns": grid["columns"],
        "row_count": grid["row_count"],
        "quotes_mode": grid["quotes_mode"],
        "checked_expiries": grid["checked_boxes"],
        "monthly_expiries_on_grid": sorted(monthly),
        "smile_maturity": smile,
        "next_monthly": month_after,
        "next_monthly_reason": (
            None if month_after else "no_next_monthly_after_smile"
        ),
        "page_text": grid["page_text"],
        "flags": flags,
    }
    body = navigation_body(response, url)
    artifact = Artifact(
        name=f"opcoes_net_grid_{ticker.lower()}",
        url=url,
        source="opcoes_net",
        kind="grid",
        body=body,
        session=session,
        content_type="text/html; charset=utf-8",
        http_status=200,
        ticker=ticker,
        maturity=month_after,
        extracted=extracted,
        note=(
            "HTML bruto da Response de navegação; IV bruta em fração, não "
            f"normalizada; linhas e cabeçalho em extracted; rows={count}"
        ),
    )
    artifact.finalize()
    artifacts.append(artifact)
    return month_after, flags


def publish(temp_dir: Path, final_dir: Path, force: bool) -> str | None:
    """Publica só após todas as guardas; --force renomeia a saída anterior."""
    backup: str | None = None
    if final_dir.exists():
        if not force:
            raise CaptureError(
                "output_exists",
                f"diretório final já existe: {final_dir}",
                "Use --force para recapturar (a saída antiga vira .bak-<timestamp>).",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = final_dir.with_name(f"{final_dir.name}.bak-{stamp}")
        final_dir.rename(backup_dir)
        backup = str(backup_dir)
    temp_dir.rename(final_dir)
    return backup


def execute(args: argparse.Namespace, requested: str, final_dir: Path) -> int:
    """Roda a captura de fato — chamada só com --execute, dentro da imagem bench."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CaptureError(
            "playwright_missing",
            "Playwright não está disponível neste processo",
            "Rode pelo serviço bench: podman compose --profile bench run --rm bench ...",
        ) from exc

    http_url = os.environ.get("B3_HTTP_URL", DEFAULT_HTTP_URL)
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(prefix="bench-capture-", dir=str(final_dir.parent))
    )
    artifacts: list[Artifact] = []
    flags: list[str] = []
    backup: str | None = None
    manifest: dict[str, Any] | None = None
    try:
        _, health_before, art_before = health_check(http_url, requested, "before")
        artifacts.append(art_before)
        expiries: dict[str, dict[str, Any]] = {}
        for ticker in SAMPLE:
            local_art, info = read_expiries(http_url, ticker, requested)
            artifacts.append(local_art)
            expiries[ticker] = {
                "smile_maturity": info["smile_maturity"],
                # preenchido pela grade (data-m="1") ANTES do OBM
                "next_monthly": None,
                "term_maturities": info["term_maturities"],
                "source": "/api/t/{ticker}?sections=term,smile + data-m da grade",
                "reason": (
                    "no_smile_maturity" if not info["smile_maturity"] else None
                ),
            }
            if not info["smile_maturity"]:
                flags.append(f"{ticker}:no_smile_maturity")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT, locale="pt-BR")
            page = context.new_page()
            collector = ResponseCollector()
            page.on("response", collector.on_response)
            try:
                # 1) grade primeiro: só data-m="1" confirma o próximo mensal
                for ticker in SAMPLE:
                    info = expiries[ticker]
                    month_after, grid_flags = capture_opcoes_net(
                        page,
                        collector,
                        requested,
                        ticker,
                        info["smile_maturity"],
                        info["term_maturities"],
                        artifacts,
                    )
                    info["next_monthly"] = month_after
                    if not month_after:
                        prefix = f"{info['reason']};" if info["reason"] else ""
                        info["reason"] = prefix + "no_next_monthly_after_smile"
                    flags.extend(f"{ticker}:{flag}" for flag in grid_flags)
                # 2) OBM depois, já com os dois vencimentos exigidos
                captured_chain = capture_obm(
                    page, collector, requested, artifacts, expiries
                )
            finally:
                context.close()
                browser.close()

        _, health_after, art_after = health_check(http_url, requested, "after")
        artifacts.append(art_after)
        if health_after.get("model_version") != health_before.get("model_version"):
            raise CaptureError(
                "model_version_changed",
                "model_version mudou entre o health antes e o depois",
                "O lake não pode mudar no meio da captura; repita quando estabilizar.",
            )

        entries = [artifact.to_manifest(temp_dir) for artifact in artifacts]
        manifest = {
            "status": "complete",
            "plan_version": PLAN_VERSION,
            "session": requested,
            "requested_date": requested,
            "model_version": health_before.get("model_version"),
            "sample": list(SAMPLE),
            "expiries": expiries,
            "captured_chains": captured_chain,
            "flags": sorted(set(flags)),
            "captured_at": now_iso(),
            "counts": {
                "artifacts": len(entries),
                "by_source": {
                    source: sum(1 for e in entries if e["source"] == source)
                    for source in sorted({e["source"] for e in entries})
                },
            },
            "health_before": {
                "as_of_session": health_before.get("as_of_session"),
                "last_session": (health_before.get("data") or {}).get("last_session"),
                "model_version": health_before.get("model_version"),
                "artifact": art_before.name + ".json",
            },
            "health_after": {
                "as_of_session": health_after.get("as_of_session"),
                "last_session": (health_after.get("data") or {}).get("last_session"),
                "model_version": health_after.get("model_version"),
                "artifact": art_after.name + ".json",
            },
            "artifacts": entries,
            "note": "Só bruto e metadados; nenhum número de relatório é calculado aqui.",
        }
        (temp_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        backup = publish(temp_dir, final_dir, args.force)
        temp_dir = Path("")  # publicado; o finally não deve apagar o destino
        print(
            json.dumps(
                {
                    "status": "captured",
                    "session": requested,
                    "output_dir": str(final_dir),
                    "artifacts": manifest["counts"]["artifacts"],
                    "flags": manifest["flags"],
                    "superseded": backup,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except CaptureError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:  # falha imprevista também é explícita
        error = CaptureError(
            "capture_failed",
            f"falha inesperada: {type(exc).__name__}: {exc}",
            "Nenhum diretório final foi publicado; reexecute e investigue o bruto.",
        )
        print(json.dumps(error.payload(), ensure_ascii=False), file=sys.stderr)
        return 3
    finally:
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def default_output_dir(requested: str) -> Path:
    """``data/bench/AAAA-MM-DD`` no container, onde ``data/`` é montado em /data."""
    data_dir = Path(os.environ.get("B3_DATA_DIR", "/data"))
    return data_dir / "bench" / requested


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench_capture",
        description=(
            "Captura EOD do benchmark F10 (P3). Sem --execute apenas imprime o plano "
            "e não faz rede nem abre navegador."
        ),
    )
    parser.add_argument("--date", required=True, help="sessão do lake (AAAA-MM-DD)")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="destino (default: $B3_DATA_DIR/bench/AAAA-MM-DD)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="executa a captura (somente na imagem bench)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="recaptura renomeando a saída anterior para .bak-<timestamp>",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    requested = parse_iso_day(args.date, "--date").isoformat()
    output_dir = (
        Path(args.output_dir) if args.output_dir else default_output_dir(requested)
    )
    if not args.execute:
        print(json.dumps(build_plan(requested, output_dir), ensure_ascii=False, indent=2))
        return 0

    manifest_path = output_dir / "manifest.json"
    if manifest_is_complete(manifest_path, requested, SAMPLE) and not args.force:
        print(
            json.dumps(
                {
                    "status": "already_captured",
                    "session": requested,
                    "output_dir": str(output_dir),
                    "plan_version": PLAN_VERSION,
                    "hint": "Use --force para recapturar (a saída antiga vira .bak).",
                },
                ensure_ascii=False,
            )
        )
        return 0
    return execute(args, requested, output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
