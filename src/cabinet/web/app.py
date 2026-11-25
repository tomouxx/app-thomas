"""Application web de Cabinet (specs/08 §1)."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from cabinet.db.base import init_db
from cabinet.web.routes import journal as routes_journal


def create_app() -> FastAPI:
    """Crée l'application web de Cabinet."""
    app = FastAPI(title="Cabinet", docs_url=None, redoc_url=None, openapi_url=None)
    init_db()
    app.state.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    app.include_router(routes_journal.router)

    @app.get("/sante")
    def health() -> dict[str, str]:
        """État de l'application et version du paquet."""
        return {"statut": "ok", "version": version("cabinet")}

    return app
