"""Ligne de commande de Cabinet."""

from __future__ import annotations

import importlib.metadata

import typer
from pydantic import BaseModel

from cabinet.llm.client import LocalLLM
from cabinet.llm.types import ConfigError, LLMError, Message

app = typer.Typer(no_args_is_help=True)


class Ping(BaseModel):
    """Schéma de la sonde `cabinet llm-test` (specs/03 §5.1)."""

    ok: bool


@app.callback()
def main() -> None:
    """Cabinet, assistant local de réunions et d'imputations."""


@app.command()
def version() -> None:
    """Affiche la version du paquet."""
    typer.echo(importlib.metadata.version("cabinet"))


@app.command()
def llm_test() -> None:
    """Vérifie que le modèle local répond par un JSON valide."""
    # Avant V1-01, les valeurs sont celles de specs/03 §3, écrites ici.
    llm = LocalLLM(base_url="http://127.0.0.1:8080/v1", model="qwen-local", timeout_s=900)
    try:
        resultat = llm.complete(
            [Message(role="user", content='Réponds par l\'objet JSON {"ok": true}.')],
            schema=Ping,
        )
    except (ConfigError, LLMError) as erreur:
        typer.echo(str(erreur), err=True)
        raise typer.Exit(code=1) from erreur
    if resultat.parsed is not None and resultat.parsed.ok:
        typer.echo("OK")
    else:
        typer.echo('Le modèle local n\'a pas répondu par {"ok": true}.', err=True)
        raise typer.Exit(code=1)
