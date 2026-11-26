"""Page Brouillons : cycle de vie d'un brouillon, sans aucune capacité d'envoi.

`GET /brouillons` liste les brouillons par état, dans l'ordre de
`specs/08 §7.4`. Les `POST` modifient, valident (avec export), rejettent ou
marquent envoyé à la main. Une `TransitionError` donne 409, un identifiant
inconnu donne 404, et chaque `POST` réussi redirige vers `/brouillons`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet.db.base import get_session
from cabinet.db.models import Draft, DraftStatus
from cabinet.services.drafts import TransitionError, mailto_link, set_status, update_draft

router = APIRouter()

# Ordre d'affichage des groupes sur la page (specs/08 §7.4).
STATUS_ORDER = (
    DraftStatus.BROUILLON,
    DraftStatus.VALIDE,
    DraftStatus.ENVOYE_A_LA_MAIN,
    DraftStatus.REJETE,
)

STATUS_LABELS = {
    DraftStatus.BROUILLON: "Brouillons",
    DraftStatus.VALIDE: "Validés",
    DraftStatus.ENVOYE_A_LA_MAIN: "Envoyés à la main",
    DraftStatus.REJETE: "Rejetés",
}


@router.get("/brouillons")
def drafts_page(request: Request) -> Any:
    """Page Brouillons : brouillons par état (specs/08 §7.4)."""
    with get_session() as session:
        groups = _groups(session)
    return request.app.state.templates.TemplateResponse(
        request, "drafts.html", {"groups": groups}
    )


@router.post("/brouillons/{draft_id}")
def modify_draft(
    request: Request,
    draft_id: int,
    subject: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
    recipients: Annotated[str, Form()] = "",
) -> Any:
    """Modifie un brouillon à l'état `brouillon` (specs/08 §7.4)."""
    with get_session() as session:
        if _unknown(session, draft_id):
            return _error(request, 404, "Brouillon inconnu.")
        try:
            update_draft(session, draft_id, subject, body, _recipients(recipients))
        except TransitionError as error:
            return _error(request, 409, str(error))
        session.commit()
    return RedirectResponse("/brouillons", status_code=303)


@router.post("/brouillons/{draft_id}/valider")
def validate_draft(request: Request, draft_id: int) -> Any:
    """Valide un brouillon et écrit son export (specs/08 §7.4)."""
    return _change_status(request, draft_id, DraftStatus.VALIDE)


@router.post("/brouillons/{draft_id}/rejeter")
def reject_draft(request: Request, draft_id: int) -> Any:
    """Rejette un brouillon (specs/08 §7.4)."""
    return _change_status(request, draft_id, DraftStatus.REJETE)


@router.post("/brouillons/{draft_id}/envoye")
def mark_sent_manually(request: Request, draft_id: int) -> Any:
    """Constate l'envoi manuel de l'utilisateur (specs/08 §7.4)."""
    return _change_status(request, draft_id, DraftStatus.ENVOYE_A_LA_MAIN)


def _change_status(request: Request, draft_id: int, target: DraftStatus) -> Any:
    """Changement d'état commun aux trois routes : 404, 409 ou redirection."""
    with get_session() as session:
        if _unknown(session, draft_id):
            return _error(request, 404, "Brouillon inconnu.")
        try:
            set_status(session, draft_id, target)
        except TransitionError as error:
            return _error(request, 409, str(error))
        session.commit()
    return RedirectResponse("/brouillons", status_code=303)


def _unknown(session: Session, draft_id: int) -> bool:
    return session.get(Draft, draft_id) is None


def _groups(session: Session) -> list[dict[str, Any]]:
    """Brouillons par état, dans l'ordre de la page (specs/08 §7.4)."""
    return [
        {
            "label": STATUS_LABELS[status],
            "drafts": [_draft_view(draft) for draft in _of_status(session, status)],
        }
        for status in STATUS_ORDER
    ]


def _of_status(session: Session, status: DraftStatus) -> list[Draft]:
    query = select(Draft).where(Draft.status == status)
    return list(
        session.scalars(query.order_by(Draft.created_at.desc(), Draft.id.desc())).all()
    )


def _draft_view(draft: Draft) -> dict[str, Any]:
    """Données d'affichage d'un brouillon ; le lien `mailto:` est réservé à l'état valide."""
    view: dict[str, Any] = {
        "id": draft.id,
        "kind": draft.kind.value,
        "recipients": ", ".join(draft.recipients),
        "subject": draft.subject,
        "body": draft.body,
        "created_at": draft.created_at.strftime("%d.%m.%Y"),
        "status": draft.status.value,
    }
    if draft.status is DraftStatus.VALIDE:
        view["mailto"] = mailto_link(draft)
    return view


def _recipients(raw: str) -> list[str]:
    """Adresses séparées par une virgule (specs/08 §7.4)."""
    return [part.strip() for part in raw.split(",") if part.strip()]


def _error(request: Request, code: int, message: str) -> Any:
    """Gabarit d'erreur avec le code et un message en français (specs/08 §1.2)."""
    return request.app.state.templates.TemplateResponse(
        request, "error.html", {"code": code, "message": message}, status_code=code
    )
