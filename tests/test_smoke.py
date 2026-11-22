"""Smoke test do bootstrap: a CLI existe, o callback está na raiz, erro é explícito.

Depois do C1 os comandos são reais: `prepare` e `health` falham com código ≠ 0 e
mensagem com dica quando o config não existe — nunca zero silencioso.
"""

from pathlib import Path

from typer.testing import CliRunner

from b3o.cli import app

runner = CliRunner()

MISSING = Path("config-que-nao-existe.yaml")


def test_cli_help_lista_comandos() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("prepare", "health"):
        assert cmd in result.output


def test_prepare_com_config_ausente_falha_explicitamente() -> None:
    """Config ausente → exit ≠ 0 com a dica na saída (nunca default silencioso)."""
    result = runner.invoke(app, ["prepare", "--config", str(MISSING)])
    assert result.exit_code != 0
    assert "bad_config" in result.output
    assert "config.yaml" in result.output


def test_health_com_config_ausente_falha_explicitamente() -> None:
    result = runner.invoke(app, ["health", "--config", str(MISSING)])
    assert result.exit_code != 0
    assert "bad_config" in result.output


def test_pacote_importa() -> None:
    import b3o

    assert b3o.__version__
