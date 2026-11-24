"""Application web de Cabinet."""

from __future__ import annotations

from importlib.metadata import version

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Crée l'application web de Cabinet."""
    app = FastAPI(title="Cabinet", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/sante")
    def sante() -> dict[str, str]:
        """État de l'application et version du paquet."""
        return {"statut": "ok", "version": version("cabinet")}

    return app
