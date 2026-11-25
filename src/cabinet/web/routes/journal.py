"""Page Journal (specs/08 §8).

`GET /journal` affiche les 100 dernières exécutions d'agent, la plus récente
en premier (tri par `started_at` puis `id` décroissants). La page répond 200
sur une base vide.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from cabinet.db.base import get_session
from cabinet.db.models import AgentRun

router = APIRouter()


@router.get("/journal")
def journal(request: Request):
    """Les 100 dernières exécutions, la plus récente en premier."""
    with get_session() as session:
        runs = (
            session.scalars(
                select(AgentRun)
                .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
                .limit(100)
            )
            .all()
        )
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "journal.html",
        {"executions": runs, "message": request.query_params.get("message")},
    )
