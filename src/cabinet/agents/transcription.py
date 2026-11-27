"""Agent `transcription` : transcription locale des audios consentis (specs/06 §3.2).

L'agent traite les `inbox_item` de nature `audio`, à l'état `pret`, qui ont
un `meeting_id`, par `id` croissant. Il vérifie de nouveau le consentement
avant d'appeler le transcripteur (second contrôle du principe 3, specs/00 §5).
En cas de succès il crée un `transcript` de source `audio` et range le
fichier ; en cas d'échec il passe l'élément à `erreur` avec un message et
continue (specs/06 §3.2).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet.adapters.mlx_whisper_transcriber import TranscriberUnavailable
from cabinet.agents.base import AgentOutcome, archive_file
from cabinet.config import paths
from cabinet.db.models import InboxItem, InboxKind, InboxStatus, Transcript, TranscriptSource
from cabinet.ports.transcriber import Transcriber, TranscriptData

_ERROR_MESSAGE_LIMIT = 500


class TranscriptionAgent:
    """Transcrit les audios à l'état `pret` dont le consentement est confirmé."""

    name = "transcription"

    def __init__(self, transcriber: Transcriber) -> None:
        self._transcriber = transcriber

    def run(self, session: Session) -> AgentOutcome:
        """Un passage : chaque audio prêt est contrôlé, transcrit et rangé."""
        items = session.scalars(
            select(InboxItem)
            .where(
                InboxItem.status == InboxStatus.PRET,
                InboxItem.kind == InboxKind.AUDIO,
                InboxItem.meeting_id.is_not(None),
            )
            .order_by(InboxItem.id)
        ).all()
        processed = 0
        errors = 0
        for item in items:
            if _process_item(session, item, self._transcriber):
                processed += 1
            else:
                errors += 1
        return AgentOutcome(processed=processed, errors=errors)


def _process_item(session: Session, item: InboxItem, transcriber: Transcriber) -> bool:
    """Transcrit un élément et le range ; vrai en cas de succès, faux sinon."""
    if not item.consent_confirmed:
        _mark_consent_required(session, item)
        return False
    try:
        data = transcriber.transcribe(paths().home / item.path, "fr")
    except TranscriberUnavailable as error:
        _mark_error(session, item, str(error))
        return False
    except Exception as error:
        _mark_error(session, item, f"Transcription impossible : {error}.")
        return False
    try:
        session.add(_transcript_for(item, data))
        archive_file(item)
        session.commit()
    except Exception as error:
        _mark_error(session, item, f"Transcription impossible : {error}.")
        return False
    return True


def _transcript_for(item: InboxItem, data: TranscriptData) -> Transcript:
    """Le `transcript` créé pour un audio transcrit (specs/06 §3.2, étape 4)."""
    return Transcript(
        meeting_id=item.meeting_id,
        inbox_item_id=item.id,
        source=TranscriptSource.AUDIO,
        text=data.text,
        segments=[segment.model_dump() for segment in data.segments],
    )


def _mark_consent_required(session: Session, item: InboxItem) -> None:
    """Consentement non confirmé : retour à `consentement_requis` (étape 2)."""
    item.status = InboxStatus.CONSENTEMENT_REQUIS
    session.commit()


def _mark_error(session: Session, item: InboxItem, message: str) -> None:
    """Élément à `erreur` avec un message tronqué (specs/06 §3.2, étapes 5 et 6)."""
    session.rollback()
    item.status = InboxStatus.ERREUR
    item.error = message[:_ERROR_MESSAGE_LIMIT]
    session.commit()


def purge_audio(session: Session, meeting_id: int) -> int:
    """Supprime du disque les audios traités d'une réunion (specs/06 §3.3).

    Seuls les éléments de nature `audio` à l'état `traite` de la réunion sont
    touchés ; leur ligne reste en base avec son chemin. Renvoie le nombre de
    fichiers effectivement supprimés. L'appelant valide la session.
    """
    items = session.scalars(
        select(InboxItem).where(
            InboxItem.meeting_id == meeting_id,
            InboxItem.kind == InboxKind.AUDIO,
            InboxItem.status == InboxStatus.TRAITE,
        )
    ).all()
    deleted = 0
    for item in items:
        source = paths().home / item.path
        if source.is_file():
            source.unlink()
            deleted += 1
    return deleted
