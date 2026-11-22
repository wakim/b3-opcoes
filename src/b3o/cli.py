"""CLI b3o — ponto de entrada único (prepare, health, e o que vier).

Armadilha conhecida (AGENTS.md): typer 0.29 colapsa app com um comando só.
Sempre declarar @app.callback() na raiz.
A data de referência NUNCA vem do relógio: vem do arquivo (P-C).
"""

import typer

app = typer.Typer(
    help="Opções B3 — dados EOD públicos da B3, cálculo em Python, decisão no humano.",
    no_args_is_help=True,
)


@app.callback()
def root() -> None:
    """b3o — Opções B3.

    Toda métrica é calculada em Python sobre dados EOD; nenhum número nasce de LLM.
    A data de referência vem sempre do arquivo baixado, nunca do relógio.
    """


@app.command()
def prepare() -> None:
    """Baixa as fontes, valida, reconstrói o lake e deriva o essencial (1×/dia)."""
    # Stub do bootstrap (Onda 0); a implementação é o F0, pacotes A1..C1.
    typer.secho(
        "prepare: ainda não implementado (fase F0).",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


@app.command()
def health() -> None:
    """Saúde do lake: última sessão, idade, contagens, fontes, último erro."""
    # Stub do bootstrap (Onda 0); a implementação é o F0.12.
    typer.secho(
        "health: ainda não implementado (fase F0.12).",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


def main() -> None:
    """Entry point (console script)."""
    app()
