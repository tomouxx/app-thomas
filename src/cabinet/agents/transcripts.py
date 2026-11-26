"""Agent `transcripts` : lit les transcripts Teams et rattache leur texte.

L'agent traite les `inbox_item` à l'état `pret`, de nature
`teams_transcript`, qui ont un `meeting_id`, par `id` croissant (specs/06 §2).
En cas de succès il crée un `transcript` de source `teams` et range le
fichier ; sur toute exception il passe l'élément à `erreur` avec son message
et continue (specs/06 §2, étapes 4 et 5).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet.agents.base import AgentOutcome, archive_file
from cabinet.config import paths
from cabinet.db.models import InboxItem, InboxKind, InboxStatus, Transcript, TranscriptSource
from cabinet.ports.transcriber import TranscriptData
from cabinet.readers.teams_docx import read_docx
from cabinet.readers.teams_vtt import read_vtt

_ERROR_MESSAGE_LIMIT = 500


class TranscriptsAgent:
    """Lit les transcripts Teams et crée leur `transcript` (specs/06 §2)."""

    name = "transcripts"

    def run(self, session: Session) -> AgentOutcome:
        """Un passage : chaque élément prêt est lu, rattaché et rangé."""
        items = session.scalars(
            select(InboxItem)
            .where(
                InboxItem.status == InboxStatus.PRET,
                InboxItem.kind == InboxKind.TEAMS_TRANSCRIPT,
                InboxItem.meeting_id.is_not(None),
            )
            .order_by(InboxItem.id)
        ).all()
        processed = 0
        errors = 0
        for item in items:
            if _process_item(session, item):
                processed += 1
            else:
                errors += 1
        return AgentOutcome(processed=processed, errors=errors)


def _process_item(session: Session, item: InboxItem) -> bool:
    """Lit un élément et le range ; vrai en cas de succès, faux en erreur."""
    source = paths().home / item.path
    try:
        if not source.is_file():
            raise ValueError(f"Fichier introuvable : {source.name}.")
        data = _read(source)
        session.add(
            Transcript(
                meeting_id=item.meeting_id,
                inbox_item_id=item.id,
                source=TranscriptSource.TEAMS,
                text=data.text,
                segments=[segment.model_dump() for segment in data.segments],
            )
        )
        archive_file(item)
        session.commit()
        return True
    except Exception as error:
        session.rollback()
        item.status = InboxStatus.ERREUR
        item.error = str(error)[:_ERROR_MESSAGE_LIMIT]
        session.commit()
        return False


def _read(source: Path) -> TranscriptData:
    """Le lecteur choisi par l'extension, insensible à la casse."""
    suffix = source.suffix.lower()
    if suffix == ".vtt":
        return read_vtt(source)
    if suffix == ".docx":
        return read_docx(source)
    raise ValueError(f"Format non pris en charge : {source.name}.")
