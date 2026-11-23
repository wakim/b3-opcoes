"""Configuração do pipeline (`config.yaml` plano — P-B, contrato Onda C §2).

Quatro chaves, nomes iguais aos campos de `Config`: `data_dir`, `log_level`,
`model_version`, `cotahist_base_url`. Segredo nunca mora aqui (`APP_TOKEN` vai no
`.env`); o YAML é versionado e legível por humano.

Regras:

- **falha explícita**: arquivo ausente, YAML torto, chave faltando, chave
  desconhecida, tipo errado ou valor fora do domínio viram
  `SourceFailure(code="bad_config")` com dica acionável. Nunca `KeyError` cru,
  nunca default silencioso (uma `data_dir` default escreveria o lake no lugar
  errado — o pior tipo de bug);
- chave desconhecida é erro de propósito: protege contra typo
  (`data_dirr`) que cairia em default e apontaria o lake para outro lugar;
- `B3_DATA_DIR` (documentada no `PLAN.md` como raiz do lake no container)
  sobrepõe `data_dir` quando definida e não vazia — é o mesmo `/data` do
  compose, e é o único override por ambiente;
- a URL do COTAHIST ganha a barra final se faltar (`base + "COTAHIST_A…"`).

Sem relógio, sem rede, sem I/O além de ler o arquivo (o teste de arquitetura
proíbe leitura de relógio em `src/`, P-C).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from b3o.ingest.errors import SourceError, SourceFailure

CONFIG_KEYS = ("data_dir", "log_level", "model_version", "cotahist_base_url")
"""Chaves aceitas no YAML (a ordem é a do `config.yaml` versionado)."""

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

DATA_DIR_ENV = "B3_DATA_DIR"
"""Override de `data_dir` por ambiente (o `/data` do compose/PLAN.md)."""

_HINT_CONFIG = (
    "confira config.yaml na raiz: data_dir, log_level, model_version e "
    "cotahist_base_url, valores planos (P-B). Nenhum segredo mora aqui"
)


@dataclass(frozen=True, slots=True)
class Config:
    """Configuração validada do pipeline (imutável, sem default silencioso)."""

    data_dir: Path
    """Raiz do lake (`/data` no container, bind de `./data`)."""

    log_level: str
    """Nível do `logging` padrão, maiúsculo (P-F)."""

    model_version: int
    """Versão do cálculo derivado; sobe a cada mudança de fórmula (≥ 1)."""

    cotahist_base_url: str
    """Base do COTAHIST viva (`…/InstDados/SerHist/`), com barra final."""


def load_config(path: Path) -> Config:
    """Lê e valida o YAML plano; qualquer problema vira `bad_config` com dica."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise _failure(
            f"config não encontrado: {source}",
            "crie config.yaml na raiz (ou passe --config com o caminho certo); "
            "no container ele está em /app/config.yaml",
        ) from exc
    except OSError as exc:
        raise _failure(
            f"não foi possível ler {source}: {exc}",
            "confira permissão/caminho do config (no bind do Windows: modo 666 é normal)",
        ) from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _failure(f"{source}: YAML inválido: {exc}", _HINT_CONFIG) from exc

    if raw is None:
        raise _failure(f"{source}: YAML vazio", _HINT_CONFIG)
    if not isinstance(raw, dict):
        raise _failure(
            f"{source}: esperado objeto plano de chaves, veio {type(raw).__name__}",
            _HINT_CONFIG,
        )

    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        raise _failure(
            f"{source}: chave(s) desconhecida(s): {unknown}",
            f"as chaves válidas são {list(CONFIG_KEYS)}; typo em chave vira erro, "
            "nunca default silencioso",
        )
    missing = [key for key in CONFIG_KEYS if key not in raw]
    if missing:
        raise _failure(
            f"{source}: chave(s) faltando: {missing}",
            "declare todas as chaves de config.yaml (nenhuma tem default); veja o "
            "arquivo versionado na raiz",
        )

    data_dir = _data_dir(raw["data_dir"], source)
    log_level = _log_level(raw["log_level"], source)
    model_version = _model_version(raw["model_version"], source)
    base_url = _base_url(raw["cotahist_base_url"], source)

    return Config(
        data_dir=data_dir,
        log_level=log_level,
        model_version=model_version,
        cotahist_base_url=base_url,
    )


def _data_dir(value: object, source: Path) -> Path:
    """`data_dir` → `Path` absoluto ou relativo; ambiente sobrepõe se definido."""
    if not isinstance(value, str) or not value.strip():
        raise _failure(
            f"{source}: data_dir inválido ({value!r}), esperado caminho não vazio",
            "no container é /data (bind de ./data); no host, o caminho que o prepare "
            "deve usar",
        )
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    return Path(override or value.strip())


def _log_level(value: object, source: Path) -> str:
    if not isinstance(value, str) or value.strip().upper() not in LOG_LEVELS:
        raise _failure(
            f"{source}: log_level inválido ({value!r}), esperado um de {list(LOG_LEVELS)}",
            "log_level é o nível do logging padrão (P-F), em maiúsculas",
        )
    return value.strip().upper()


def _model_version(value: object, source: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _failure(
            f"{source}: model_version inválido ({value!r}), esperado inteiro ≥ 1",
            "model_version começa em 1 e sobe a cada mudança de fórmula (AGENTS.md)",
        )
    return value


def _base_url(value: object, source: Path) -> str:
    """URL base do COTAHIST: http(s) absoluta, com barra final normalizada."""
    if not isinstance(value, str) or not value.strip():
        raise _failure(
            f"{source}: cotahist_base_url inválido ({value!r}), esperado URL https",
            "a URL viva é https://bvmf.bmfbovespa.com.br/InstDados/SerHist/ (a antiga "
            "arquivos.b3.com.br/dados/pregao/cotahist está morta — AGENTS.md)",
        )
    base = value.strip()
    if not base.endswith("/"):
        base += "/"
    try:
        parsed = httpx.URL(base)
    except (httpx.InvalidURL, ValueError) as exc:
        raise _failure(
            f"{source}: cotahist_base_url não é URL válida: {value!r}",
            "use https://bvmf.bmfbovespa.com.br/InstDados/SerHist/",
        ) from exc
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise _failure(
            f"{source}: cotahist_base_url sem esquema http(s) ou host: {value!r}",
            "use https://bvmf.bmfbovespa.com.br/InstDados/SerHist/",
        )
    return base


def _failure(message: str, hint: str) -> SourceFailure:
    """Estado/config inválido no formato do projeto (P-E)."""
    return SourceFailure(SourceError(code="bad_config", message=message, hint=hint))


__all__ = ["CONFIG_KEYS", "DATA_DIR_ENV", "LOG_LEVELS", "Config", "load_config"]
