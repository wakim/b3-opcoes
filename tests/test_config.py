"""Configuração (contrato Onda C §2): YAML plano, chave ausente e valor torto explícitos.

O ponto do teste: uma `data_dir` default silenciosa escreveria o lake no lugar
errado — todo problema de config precisa virar `bad_config` com dica, nunca
`KeyError` cru e nunca default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from b3o.config import Config, load_config
from b3o.ingest.errors import SourceFailure

VALID = """\
data_dir: /data
log_level: info
model_version: 1
cotahist_base_url: "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/"
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _sem_override_de_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    """O container exporta B3_DATA_DIR=/data: os testes não podem herdar isso."""
    monkeypatch.delenv("B3_DATA_DIR", raising=False)


def test_config_valido(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, VALID))
    assert isinstance(config, Config)
    assert config.data_dir == Path("/data")
    assert config.log_level == "INFO"  # normalizado para maiúsculas
    assert config.model_version == 1
    assert config.cotahist_base_url.endswith("/")


def test_config_normaliza_barra_final_da_url(tmp_path: Path) -> None:
    text = VALID.replace(
        "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/",
        "https://bvmf.bmfbovespa.com.br/InstDados/SerHist",
    )
    config = load_config(_write(tmp_path, text))
    assert config.cotahist_base_url == "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/"


def test_config_aceita_data_dir_relativa(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, VALID.replace("data_dir: /data", "data_dir: lake")))
    assert config.data_dir == Path("lake")


def test_chave_faltando_falha_com_bad_config(tmp_path: Path) -> None:
    broken = "\n".join(
        line for line in VALID.splitlines() if not line.startswith("model_version")
    )
    with pytest.raises(SourceFailure) as excinfo:
        load_config(_write(tmp_path, broken))
    assert excinfo.value.code == "bad_config"
    assert "model_version" in excinfo.value.message
    assert excinfo.value.hint  # sempre com dica


def test_chave_desconhecida_e_erro(tmp_path: Path) -> None:
    """Typo em chave não pode cair em default: apontaria o lake para outro lugar."""
    with pytest.raises(SourceFailure) as excinfo:
        load_config(_write(tmp_path, VALID + "data_dirr: /tmp\n"))
    assert excinfo.value.code == "bad_config"
    assert "data_dirr" in excinfo.value.message


def test_arquivo_ausente_falha_com_bad_config(tmp_path: Path) -> None:
    with pytest.raises(SourceFailure) as excinfo:
        load_config(tmp_path / "nao-existe.yaml")
    assert excinfo.value.code == "bad_config"
    assert "não encontrado" in excinfo.value.message


def test_yaml_torto_falha_com_bad_config(tmp_path: Path) -> None:
    with pytest.raises(SourceFailure) as excinfo:
        load_config(_write(tmp_path, "data_dir: [\n"))
    assert excinfo.value.code == "bad_config"


def test_yaml_que_nao_e_objeto_falha(tmp_path: Path) -> None:
    with pytest.raises(SourceFailure) as excinfo:
        load_config(_write(tmp_path, "- data_dir\n- log_level\n"))
    assert excinfo.value.code == "bad_config"


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("data_dir: /data", 'data_dir: ""', "data_dir"),
        ("log_level: info", "log_level: BARULHENTO", "log_level"),
        ("model_version: 1", "model_version: 0", "model_version"),
        ("model_version: 1", "model_version: true", "model_version"),
        ("model_version: 1", "model_version: 'um'", "model_version"),
        (
            'cotahist_base_url: "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/"',
            "cotahist_base_url: arquivos.b3.com.br",
            "cotahist_base_url",
        ),
        (
            'cotahist_base_url: "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/"',
            "cotahist_base_url: 42",
            "cotahist_base_url",
        ),
    ],
)
def test_valor_invalido_falha_com_bad_config(
    tmp_path: Path, old: str, new: str, expected: str
) -> None:
    with pytest.raises(SourceFailure) as excinfo:
        load_config(_write(tmp_path, VALID.replace(old, new)))
    assert excinfo.value.code == "bad_config"
    assert expected in excinfo.value.message
    assert excinfo.value.hint


def test_env_b3_data_dir_sobrepoe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`B3_DATA_DIR` é o override documentado do PLAN.md (o `/data` do compose)."""
    monkeypatch.setenv("B3_DATA_DIR", "/mnt/lake")
    config = load_config(_write(tmp_path, VALID))
    assert config.data_dir == Path("/mnt/lake")
