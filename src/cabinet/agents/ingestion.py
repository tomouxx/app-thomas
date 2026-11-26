"""Agent d'ingestion : un élément par fichier dont l'empreinte est nouvelle.

L'agent ne lit pas le contenu d'un fichier au-delà du calcul d'empreinte et
ne déplace rien (specs/05 §5.3). L'échec d'un élément n'arrête pas les
autres : il est annulé et compté dans `AgentOutcome.errors` (specs/01 §3.3).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet import clock
from cabinet.agents.base import AgentOutcome
from cabinet.config import paths
from cabinet.db.models import InboxItem, InboxKind, InboxStatus
from cabinet.ports.inbox import Inbox, InboxFile


class IngestionAgent:
    """Crée un `inbox_item` par fichier dont l'empreinte est nouvelle (specs/05 §5.3)."""

    name = "ingestion"

    def __init__(self, inbox: Inbox) -> None:
        self._inbox = inbox

    def run(self, session: Session) -> AgentOutcome:
        """Un passage : un élément par empreinte nouvelle, validé au fur et à mesure."""
        known = _known_fingerprints(session)
        processed = 0
        errors = 0
        for file in self._inbox.scan():
            if file.sha256 in known:
                continue
            try:
                _create_item(session, file)
            except Exception:
                session.rollback()
                errors += 1
            else:
                known.add(file.sha256)
                processed += 1
        return AgentOutcome(processed=processed, errors=errors)


def _known_fingerprints(session: Session) -> set[str]:
    """Les empreintes déjà présentes dans `inbox_item`."""
    return set(session.scalars(select(InboxItem.sha256)))


def _create_item(session: Session, file: InboxFile) -> None:
    """Crée l'élément d'un fichier et le valide (specs/05 §5.3)."""
    kind = InboxKind(file.kind)
    item = InboxItem(
        path=_relative_path(file.path),
        sha256=file.sha256,
        kind=kind,
        status=_initial_status(kind),
        name_date=file.name_date,
        detected_at=clock.now(),
    )
    session.add(item)
    session.commit()


def _relative_path(path: Path) -> str:
    """Chemin relatif au répertoire de données, en notation POSIX (specs/02 §2.2)."""
    return path.relative_to(paths().home).as_posix()


def _initial_status(kind: InboxKind) -> InboxStatus:
    """État de départ d'un élément, selon sa nature (tableau de specs/05 §5.1)."""
    if kind is InboxKind.AUDIO:
        return InboxStatus.CONSENTEMENT_REQUIS
    if kind in (InboxKind.APP_CAPTURE_TEXT, InboxKind.APP_CAPTURE_IMAGE):
        return InboxStatus.PRET
    if kind is InboxKind.INCONNU:
        return InboxStatus.IGNORE
    return InboxStatus.A_RATTACHER
