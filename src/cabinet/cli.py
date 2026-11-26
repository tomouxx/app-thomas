"""Ligne de commande de Cabinet."""

from __future__ import annotations

import importlib.metadata

import typer
from pydantic import BaseModel

from cabinet.agents.pipeline import PipelineBusy, build_pipeline
from cabinet.config import DEFAULT_SETTINGS_TOML, load_settings, paths
from cabinet.db.base import get_session, init_db
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
def init() -> None:
    """Crée le répertoire de données et un reglages.toml par défaut (specs/03 §5.2)."""
    data_paths = paths()
    created: list[str] = []
    for item in (data_paths.home, data_paths.inbox, data_paths.processed, data_paths.drafts,
                 data_paths.config, data_paths.data):
        if not item.exists():
            created.append(str(item))
    for item in (data_paths.home, data_paths.inbox, data_paths.processed, data_paths.drafts,
                 data_paths.config, data_paths.data):
        item.mkdir(parents=True, exist_ok=True)
    typer.echo(str(data_paths.home))
    for item in created:
        typer.echo(f"créé : {item}")
    if not data_paths.settings_file.exists():
        data_paths.config.mkdir(parents=True, exist_ok=True)
        data_paths.settings_file.write_text(DEFAULT_SETTINGS_TOML, encoding="utf-8")
        typer.echo(f"créé : {data_paths.settings_file}")


@app.command()
def run() -> None:
    """Exécute le pipeline et affiche une ligne par agent (specs/03 §5.3)."""
    settings = load_settings()
    init_db()
    pipeline = build_pipeline(settings)
    try:
        result = pipeline.run(get_session)
    except PipelineBusy:
        typer.echo("Un traitement est déjà en cours.")
        raise typer.Exit(code=2) from None
    code = 0
    for name, value in result.items():
        if isinstance(value, str):
            typer.echo(f"{name} : échec : {value}")
            code = 1
        else:
            typer.echo(f"{name} : {value.processed} traité(s), {value.errors} erreur(s)")
            if value.errors > 0:
                code = 1
    if code != 0:
        raise typer.Exit(code=code)


@app.command()
def llm_test() -> None:
    """Vérifie que le modèle local répond par un JSON valide."""
    try:
        settings = load_settings()
        llm = LocalLLM(
            base_url=settings.llm.base_url,
            model=settings.llm.model,
            timeout_s=settings.llm.timeout_s,
        )
        result = llm.complete(
            [Message(role="user", content='Réponds par l\'objet JSON {"ok": true}.')],
            schema=Ping,
        )
    except (ConfigError, LLMError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    if result.parsed is not None and result.parsed.ok:
        typer.echo("OK")
    else:
        typer.echo('Le modèle local n\'a pas répondu par {"ok": true}.', err=True)
        raise typer.Exit(code=1)
