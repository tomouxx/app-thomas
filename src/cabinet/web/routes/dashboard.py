"""Tableau de bord et page Réglages (specs/08 §2).

`GET /` affiche les réunions des sept prochains jours, les quatre compteurs
et l'échéance des imputations. `GET /reglages` affiche les réglages en
lecture seule. Les deux relisent les réglages à chaque requête ; une erreur
de configuration répond 500 avec le gabarit d'erreur (specs/08 §1.2).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from cabinet import clock
from cabinet.config import Settings, load_settings, paths
from cabinet.db.base import get_session
from cabinet.db.models import (
    ActionItem,
    ActionStatus,
    DocStatus,
    Draft,
    DraftStatus,
    Event,
    InboxItem,
    InboxStatus,
    Meeting,
    Minutes,
    Series,
)
from cabinet.llm.types import ConfigError

router = APIRouter()


@router.get("/")
def dashboard(request: Request) -> Any:
    """Tableau de bord : réunions à venir, compteurs et échéance (specs/08 §2.1)."""
    try:
        settings = load_settings()
    except ConfigError as error:
        return error_page(request, 500, str(error))
    with get_session() as session:
        meetings = upcoming_meetings(session)
        counters = dashboard_counters(session)
    deadline = deadline_text(settings.cra.deadline, clock.today())
    return request.app.state.templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "meetings": meetings,
            "counters": counters,
            "deadline": deadline,
            "message": request.query_params.get("message"),
        },
    )


@router.get("/reglages")
def settings_page(request: Request) -> Any:
    """Réglages en lecture seule (specs/08 §2.4)."""
    try:
        settings = load_settings()
    except ConfigError as error:
        return error_page(request, 500, str(error))
    return request.app.state.templates.TemplateResponse(
        request,
        "settings.html",
        {"home": str(paths().home), "sections": settings_sections(settings)},
    )


def upcoming_meetings(session: Session) -> list[dict[str, Any]]:
    """Réunions de aujourd'hui à sept jours plus tard inclus (specs/08 §2.1)."""
    today = clock.today()
    rows = session.execute(
        select(Meeting, Event.start, Series.followed)
        .join(Series, Meeting.series_id == Series.id)
        .outerjoin(Event, Meeting.event_id == Event.id)
        .where(Meeting.date >= today, Meeting.date <= today + timedelta(days=7))
        .order_by(Meeting.date.asc(), Event.start.asc())
    ).all()
    return [
        {
            "date": meeting.date.strftime("%d.%m.%Y"),
            "time": start.strftime("%H:%M") if start is not None else None,
            "title": meeting.title,
            "followed": bool(followed),
        }
        for meeting, start, followed in rows
    ]


def dashboard_counters(session: Session) -> dict[str, int]:
    """Les quatre compteurs du tableau de bord (specs/08 §2.1)."""
    return {
        "inbox": _count_inbox(session),
        "drafts": _count_drafts(session),
        "actions": _count_actions(session),
        "minutes": _count_draft_minutes(session),
    }


def _count_inbox(session: Session) -> int:
    """Fichiers à traiter : à rattacher ou consentement requis."""
    return (
        session.scalar(
            select(func.count(InboxItem.id)).where(
                InboxItem.status.in_([InboxStatus.A_RATTACHER, InboxStatus.CONSENTEMENT_REQUIS])
            )
        )
        or 0
    )


def _count_drafts(session: Session) -> int:
    """Brouillons de message à relire."""
    return (
        session.scalar(select(func.count(Draft.id)).where(Draft.status == DraftStatus.BROUILLON))
        or 0
    )


def _count_actions(session: Session) -> int:
    """Actions ouvertes."""
    return (
        session.scalar(
            select(func.count(ActionItem.id)).where(ActionItem.status == ActionStatus.OUVERT)
        )
        or 0
    )


def _count_draft_minutes(session: Session) -> int:
    """Réunions dont le compte rendu courant est en brouillon (specs/02 §2.8)."""
    latest = (
        select(Minutes.meeting_id, func.max(Minutes.version).label("version"))
        .group_by(Minutes.meeting_id)
        .subquery()
    )
    return (
        session.scalar(
            select(func.count())
            .select_from(latest)
            .join(
                Minutes,
                and_(
                    Minutes.meeting_id == latest.c.meeting_id,
                    Minutes.version == latest.c.version,
                ),
            )
            .where(Minutes.status == DocStatus.BROUILLON)
        )
        or 0
    )


def deadline_text(deadline: date | None, today: date) -> str | None:
    """Texte de l'échéance des imputations (specs/08 §2.2)."""
    if deadline is None:
        return None
    days = (deadline - today).days
    if days > 0:
        return f"J-{days}"
    if days == 0:
        return "aujourd'hui"
    return "échéance dépassée"


def error_page(request: Request, code: int, message: str) -> Any:
    """Gabarit d'erreur avec le code et un message en français (specs/08 §1.2)."""
    return request.app.state.templates.TemplateResponse(
        request, "error.html", {"code": code, "message": message}, status_code=code
    )


def settings_sections(settings: Settings) -> list[dict[str, Any]]:
    """Toutes les valeurs de Settings, section par section (specs/08 §2.4).

    Une sous-section imbriquée (par exemple `cra.regles`) devient une
    section à part, nommée par son chemin complet.
    """
    raw = settings.model_dump(mode="json")
    sections: list[dict[str, Any]] = []
    for name, content in raw.items():
        if not isinstance(content, dict):
            continue
        flat = {key: value for key, value in content.items() if not isinstance(value, dict)}
        sections.append({"name": name, "fields": _display_pairs(flat)})
        for sub_name, sub in content.items():
            if isinstance(sub, dict):
                sections.append({"name": f"{name}.{sub_name}", "fields": _display_pairs(sub)})
    return sections


def _display_pairs(values: dict[str, Any]) -> list[tuple[str, str]]:
    """Paires (clé, valeur) avec les valeurs formatées pour l'affichage."""
    return [(key, _display_value(value)) for key, value in values.items()]


def _display_value(value: Any) -> str:
    """Valeur affichable : « oui »/« non » pour un booléen, « (vide) » pour None."""
    if value is None:
        return "(vide)"
    if isinstance(value, bool):
        return "oui" if value else "non"
    return str(value)
