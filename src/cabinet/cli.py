"""Ligne de commande de Cabinet."""

from __future__ import annotations

import importlib.metadata

import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Cabinet, assistant local de réunions et d'imputations."""


@app.command()
def version() -> None:
    """Affiche la version du paquet."""
    typer.echo(importlib.metadata.version("cabinet"))
