"""Page Entrée : liste des fichiers, rattachement et consentement (specs/08 §3).

`GET /entree` liste les éléments par état, avec suggestions pour ceux qui
peuvent être rattachés. `POST /entree/{id}/rattacher` rattache un élément à
une réunion existante ou crée une réunion hors agenda (avec sa série). Pour un
audio, la case de consentement est exigée à chaque rattachement (specs/00 §5,
principe 3). `POST /entree/{id}/ignorer` passe l'élément à `ignore`, et
`POST /entree/{id}/reessayer` rejoue un élément en erreur (specs/08 §3.4).
Chaque action réussie redirige vers `/entree`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet import clock
from cabinet.db.base import get_session
from cabinet.db.models import InboxItem, InboxKind, InboxStatus, Meeting, Series
from cabinet.services.matching import suggest_meetings
from cabinet.services.series import series_key

router = APIRouter()

# États dans lesquels un élément peut être rattaché (specs/08 §3.3).
ATTACHABLE_STATUSES = (InboxStatus.A_RATTACHER, InboxStatus.CONSENTEMENT_REQUIS, InboxStatus.PRET)
# Natures qui ne se rattachent pas à une réunion (specs/08 §3.3).
NON_ATTACHABLE_KINDS = (InboxKind.APP_CAPTURE_TEXT, InboxKind.APP_CAPTURE_IMAGE, InboxKind.INCONNU)
# Ordre d'affichage des groupes sur la page (specs/08 §3.2).
STATUS_ORDER = (
    InboxStatus.A_RATTACHER,
    InboxStatus.CONSENTEMENT_REQUIS,
    InboxStatus.PRET,
    InboxStatus.ERREUR,
    InboxStatus.TRAITE,
    InboxStatus.IGNORE,
)
# Les groupes « traités » et « écartés » n'affichent que les 50 plus récents.
RECENT_LIMIT = 50

STATUS_LABELS = {
    InboxStatus.A_RATTACHER: "À rattacher",
    InboxStatus.CONSENTEMENT_REQUIS: "Consentement requis",
    InboxStatus.PRET: "À traiter",
    InboxStatus.ERREUR: "En erreur",
    InboxStatus.TRAITE: "Traités",
    InboxStatus.IGNORE: "Écartés",
}


@router.get("/entree")
def inbox_page(request: Request) -> Any:
    """Page Entrée : éléments par état, avec suggestions (specs/08 §3.2)."""
    with get_session() as session:
        groups = _groups(session)
    return request.app.state.templates.TemplateResponse(
        request, "inbox.html", {"groups": groups}
    )


@router.post("/entree/{item_id}/rattacher")
async def attach(request: Request, item_id: int) -> Any:
    """Rattache l'élément à une réunion, existante ou hors agenda (specs/08 §3.3)."""
    form = await request.form()
    with get_session() as session:
        item = session.get(InboxItem, item_id)
        if item is None:
            return _error(request, 404, "Élément inconnu.")
        refusal = _attach_refusal(item, form)
        if refusal is not None:
            return _error(request, *refusal)
        meeting, refusal = _target_meeting(session, form)
        if meeting is None:
            assert refusal is not None
            return _error(request, *refusal)
        _apply_attach(item, meeting)
        session.commit()
    return RedirectResponse("/entree", status_code=303)


@router.post("/entree/{item_id}/ignorer")
def ignore_item(request: Request, item_id: int) -> Any:
    """Passe l'élément à `ignore` ; un élément traité ne change plus d'état."""
    with get_session() as session:
        item = session.get(InboxItem, item_id)
        if item is None:
            return _error(request, 404, "Élément inconnu.")
        if item.status == InboxStatus.TRAITE:
            return _error(request, 409, "Un élément traité ne change plus d'état.")
        item.status = InboxStatus.IGNORE
        session.commit()
    return RedirectResponse("/entree", status_code=303)


@router.post("/entree/{item_id}/reessayer")
def retry_item(request: Request, item_id: int) -> Any:
    """Rejoue un élément en erreur (specs/08 §3.4)."""
    with get_session() as session:
        item = session.get(InboxItem, item_id)
        if item is None:
            return _error(request, 404, "Élément inconnu.")
        if item.status != InboxStatus.ERREUR:
            return _error(request, 409, "Seul un élément en erreur peut être rejoué.")
        item.error = None
        item.status = _status_after_retry(item)
        session.commit()
    return RedirectResponse("/entree", status_code=303)


def _groups(session: Session) -> list[dict[str, Any]]:
    """Éléments par état, dans l'ordre de la page (specs/08 §3.2)."""
    return [
        {
            "label": STATUS_LABELS[status],
            "elements": [_item_view(item, session) for item in _items_of_status(session, status)],
        }
        for status in STATUS_ORDER
    ]


def _items_of_status(session: Session, status: InboxStatus) -> list[InboxItem]:
    query = select(InboxItem).where(InboxItem.status == status)
    if status in (InboxStatus.TRAITE, InboxStatus.IGNORE):
        query = query.limit(RECENT_LIMIT)
    return list(
        session.scalars(query.order_by(InboxItem.detected_at.desc(), InboxItem.id.desc())).all()
    )


def _item_view(item: InboxItem, session: Session) -> dict[str, Any]:
    """Données d'affichage d'un élément de la page."""
    view: dict[str, Any] = {
        "id": item.id,
        "name": Path(item.path).name,
        "kind": item.kind.value,
        "name_date": item.name_date.strftime("%d.%m.%Y") if item.name_date is not None else None,
        "meeting": item.meeting.title if item.meeting is not None else None,
        "error_message": item.error,
        "status": item.status.value,
    }
    if item.status in (InboxStatus.A_RATTACHER, InboxStatus.CONSENTEMENT_REQUIS):
        view["suggestions"] = [
            {"id": meeting.id, "title": meeting.title} for meeting, _ in suggest_meetings(session, item)
        ]
    return view


def _attach_refusal(item: InboxItem, form: Any) -> tuple[int, str] | None:
    """Contrôles avant tout changement (specs/08 §3.3) : état, nature, consentement."""
    if item.status not in ATTACHABLE_STATUSES:
        return 409, "Cet élément ne peut plus être rattaché."
    if item.kind in NON_ATTACHABLE_KINDS:
        return 409, "Ce type de fichier ne se rattache pas à une réunion."
    if item.kind == InboxKind.AUDIO and not _consent_checked(form):
        return 400, "Le consentement des participants est requis pour un enregistrement."
    return None


def _consent_checked(form: Any) -> bool:
    """La case est cochée si le formulaire porte une valeur non vide."""
    raw = form.get("consentement")
    return isinstance(raw, str) and raw != ""


def _target_meeting(session: Session, form: Any) -> tuple[Meeting | None, tuple[int, str] | None]:
    """La réunion de rattachement : une existante, ou une nouvelle hors agenda."""
    raw_id = form.get("meeting_id")
    if isinstance(raw_id, str) and raw_id:
        meeting = _existing_meeting(session, raw_id)
        if meeting is None:
            return None, (404, "Réunion inconnue.")
        return meeting, None
    title = _form_text(form, "titre").strip()
    raw_day = _form_text(form, "date")
    if not title or not raw_day:
        return None, (400, "Une réunion hors agenda exige un titre et une date.")
    try:
        day = date.fromisoformat(raw_day)
    except ValueError:
        return None, (400, "La date de la réunion est illisible.")
    return _off_agenda_meeting(session, title, day), None


def _form_text(form: Any, name: str) -> str:
    raw = form.get(name)
    return raw if isinstance(raw, str) else ""


def _existing_meeting(session: Session, raw_id: str) -> Meeting | None:
    try:
        meeting_id = int(raw_id)
    except ValueError:
        return None
    return session.get(Meeting, meeting_id)


def _off_agenda_meeting(session: Session, title: str, day: date) -> Meeting:
    """Crée la réunion sans évènement et sa série si elle manque (specs/08 §3.3)."""
    key = series_key(title)
    series = session.scalar(select(Series).where(Series.key == key))
    if series is None:
        series = Series(key=key, label=title)
        session.add(series)
        session.flush()
    meeting = Meeting(series_id=series.id, title=title, date=day)
    session.add(meeting)
    session.flush()
    return meeting


def _apply_attach(item: InboxItem, meeting: Meeting) -> None:
    """Étapes 7 à 8 de specs/08 §3.3 : lien, consentement pour un audio, état `pret`."""
    item.meeting_id = meeting.id
    if item.kind == InboxKind.AUDIO:
        item.consent_confirmed = True
        item.consent_at = clock.now()
    item.status = InboxStatus.PRET


def _status_after_retry(item: InboxItem) -> InboxStatus:
    """L'état d'où l'élément peut repartir (specs/08 §3.4)."""
    if item.kind == InboxKind.AUDIO:
        has_all = item.meeting_id is not None and item.consent_confirmed
        return InboxStatus.PRET if has_all else InboxStatus.CONSENTEMENT_REQUIS
    if item.kind in (InboxKind.APP_CAPTURE_TEXT, InboxKind.APP_CAPTURE_IMAGE):
        return InboxStatus.PRET
    return InboxStatus.PRET if item.meeting_id is not None else InboxStatus.A_RATTACHER


def _error(request: Request, code: int, message: str) -> Any:
    """Gabarit d'erreur avec le code et un message en français (specs/08 §1.2)."""
    return request.app.state.templates.TemplateResponse(
        request, "error.html", {"code": code, "message": message}, status_code=code
    )
