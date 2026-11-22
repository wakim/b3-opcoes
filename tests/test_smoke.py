"""Smoke test do bootstrap: a CLI existe, o callback está na raiz, o stub falha explícito."""

from typer.testing import CliRunner

from b3o.cli import app

runner = CliRunner()


def test_cli_help_lista_comandos() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("prepare", "health"):
        assert cmd in result.output


def test_stub_prepara_falha_explicitamente() -> None:
    """Stub falha com código ≠ 0 e mensagem — nunca zero silencioso."""
    result = runner.invoke(app, ["prepare"])
    assert result.exit_code != 0
    assert "ainda não implementado" in result.output


def test_stub_health_falha_explicitamente() -> None:
    result = runner.invoke(app, ["health"])
    assert result.exit_code != 0
    assert "ainda não implementado" in result.output


def test_pacote_importa() -> None:
    import b3o

    assert b3o.__version__
