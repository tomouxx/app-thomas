"""Application web de Cabinet (specs/08 §1).

`create_app()` crée l'application, initialise la base, expose les gabarits
et le dossier statique, et installe les garde-fous d'hôte et d'origine
(specs/08 §1.3). L'application ne s'expose jamais hors de la machine locale.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from cabinet.db.base import init_db
from cabinet.web.routes import dashboard as routes_dashboard
from cabinet.web.routes import inbox as routes_inbox
from cabinet.web.routes import journal as routes_journal

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
LOCAL_ORIGIN_PREFIXES = ("http://127.0.0.1:", "http://localhost:")


class OriginGuard:
    """Refuse un POST dont l'en-tête Origin n'est pas local (specs/08 §1.3).

    Un POST sans en-tête Origin est accepté ; les autres méthodes ne sont
    pas touchées. Le contrôle porte sur l'en-tête, pas sur le hôte.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        origin = _origin_header(scope["headers"])
        if origin is not None and not origin.startswith(LOCAL_ORIGIN_PREFIXES):
            response = Response(status_code=403, content="Origine refusée.")
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _origin_header(headers: list[tuple[bytes, bytes]]) -> str | None:
    """Valeur de l'en-tête Origin, si elle est présente."""
    for name, value in headers:
        if name == b"origin":
            return value.decode("latin-1")
    return None


def create_app() -> FastAPI:
    """Crée l'application web de Cabinet (specs/08 §1.1)."""
    app = FastAPI(title="Cabinet", docs_url=None, redoc_url=None, openapi_url=None)
    init_db()
    app.state.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )
    app.include_router(routes_journal.router)
    app.include_router(routes_dashboard.router)
    app.include_router(routes_inbox.router)

    @app.get("/sante")
    def health() -> dict[str, str]:
        """État de l'application et version du paquet."""
        return {"statut": "ok", "version": version("cabinet")}

    app.add_middleware(OriginGuard)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    return app
