"""Cycle de vie d'un brouillon de message, sans aucune capacité d'envoi.

Le service applique les transitions de `specs/02 §2.14` et écrit le fichier
d'export à la validation (`specs/08 §7`). L'application n'envoie rien :
« marquer envoyé » constate un geste de l'utilisateur dans sa messagerie.
"""

from __future__ import annotations

from urllib.parse import quote

from sqlalchemy.orm import Session

from cabinet import clock
from cabinet.config import paths
from cabinet.db.models import Draft, DraftKind, DraftStatus


class TransitionError(Exception):
    """Transition d'état interdite par le diagramme de `specs/02 §2.14`."""


# Les seules transitions du diagramme de `specs/02 §2.14`.
ALLOWED_TRANSITIONS = {
    DraftStatus.BROUILLON: (DraftStatus.VALIDE, DraftStatus.REJETE),
    DraftStatus.VALIDE: (DraftStatus.ENVOYE_A_LA_MAIN,),
}


def create_draft(
    session: Session,
    kind: str | DraftKind,
    recipients: list[str],
    subject: str,
    body: str,
    meeting_id: int | None = None,
    capture_id: int | None = None,
) -> Draft:
    """Crée un brouillon à l'état `brouillon`, daté par `clock.now()` (specs/08 §7.1)."""
    draft = Draft(
        kind=DraftKind(kind),
        recipients=list(recipients),
        subject=subject,
        body=body,
        status=DraftStatus.BROUILLON,
        meeting_id=meeting_id,
        capture_id=capture_id,
        created_at=clock.now(),
    )
    session.add(draft)
    session.flush()
    return draft


def update_draft(
    session: Session, draft_id: int, subject: str, body: str, recipients: list[str]
) -> Draft:
    """Modifie un brouillon ; n'agit que sur l'état `brouillon` (specs/08 §7.1)."""
    draft = _get_draft(session, draft_id)
    if draft.status is not DraftStatus.BROUILLON:
        raise TransitionError("Un brouillon ne se modifie qu'à l'état brouillon.")
    draft.subject = subject
    draft.body = body
    draft.recipients = list(recipients)
    session.flush()
    return draft


def set_status(session: Session, draft_id: int, new_status: str | DraftStatus) -> Draft:
    """Change l'état d'un brouillon ; seules les transitions permises (specs/08 §7.1).

    Le passage à `valide` écrit le fichier d'export (specs/08 §7.2).
    """
    draft = _get_draft(session, draft_id)
    target = DraftStatus(new_status)
    if target not in ALLOWED_TRANSITIONS.get(draft.status, ()):
        raise TransitionError(f"Transition interdite : {draft.status.value} vers {target.value}.")
    draft.status = target
    if target is DraftStatus.VALIDE:
        _write_export(draft)
    session.flush()
    return draft


def mailto_link(draft: Draft) -> str:
    """Lien `mailto:` du brouillon, formule exacte de `specs/08 §7.3`."""
    return (
        "mailto:"
        + ",".join(quote(address, safe="@") for address in draft.recipients)
        + "?subject="
        + quote(draft.subject, safe="")
        + "&body="
        + quote(draft.body, safe="")
    )


def _get_draft(session: Session, draft_id: int) -> Draft:
    """Le brouillon de la base ; `LookupError` s'il est inconnu."""
    draft = session.get(Draft, draft_id)
    if draft is None:
        raise LookupError("Brouillon inconnu.")
    return draft


def _write_export(draft: Draft) -> None:
    """Écrit `brouillons/AAAA-MM-JJ_{kind}_{id}.md` (specs/08 §7.2)."""
    folder = paths().drafts
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{clock.today():%Y-%m-%d}_{draft.kind.value}_{draft.id}.md"
    (folder / name).write_text(_export_content(draft), encoding="utf-8")


def _export_content(draft: Draft) -> str:
    """Contenu du fichier d'export : titre, destinataires, corps (specs/08 §7.2)."""
    return (
        f"# {draft.subject}\n"
        "\n"
        f"Destinataires : {', '.join(draft.recipients)}\n"
        "\n"
        f"{draft.body}\n"
    )
