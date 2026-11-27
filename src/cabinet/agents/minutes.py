"""Agent `minutes` : extraction structurée du compte rendu d'une réunion (specs/06 §4).

L'agent traite les réunions qui ont au moins un `transcript` et aucun
`minutes`, par date puis par `id`. Pour chacune, il envoie la source au
modèle local entre balises `<source>` et crée, dans une seule transaction, le
compte rendu `version` 1 (état `brouillon`) et ses décisions et actions
(`status` `ouvert`). Après l'extraction, chaque extrait est contrôlé contre
la source sans modèle et `verified` porte le résultat (specs/06 §5) ; avant
V2-06 `content_md` reste vide (specs/06 §4.3).

`regenerate` refait l'extraction sur un compte rendu `brouillon`, remplace
les décisions et actions des versions non validées et crée la version
suivante ; un compte rendu `valide` ne se régénère pas (specs/06 §4.4).
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cabinet import clock
from cabinet.agents.base import AgentOutcome
from cabinet.db.models import (
    ActionItem,
    Decision,
    DocStatus,
    Meeting,
    Minutes,
    Transcript,
    TranscriptSource,
)
from cabinet.llm.prompts.minutes import (
    ActionOut,
    DecisionOut,
    MinutesOut,
    build_minutes_messages,
)
from cabinet.llm.types import LLMError
from cabinet.ports.llm import LLM
from cabinet.services.quotes import verify_quote

_SUMMARY_MAX_WORDS = 120
_QUOTE_MAX_CHARS = 200

_SOURCE_LABELS: dict[TranscriptSource, str] = {
    TranscriptSource.TEAMS: "transcript Teams",
    TranscriptSource.NOTES: "notes",
    TranscriptSource.AUDIO: "transcription audio",
}


class ValidationBlocked(Exception):
    """Un compte rendu validé ne se régénère pas (specs/06 §4.4, étape 1)."""


class MinutesAgent:
    """Extrait le compte rendu des réunions qui n'en ont pas encore."""

    name = "minutes"

    def __init__(self, llm: LLM, context_tokens: int = 64000) -> None:
        self._llm = llm
        # Réservé au morcellement des sources longues (V2-09, specs/06 §6).
        self._context_tokens = context_tokens

    def run(self, session: Session) -> AgentOutcome:
        """Un passage : chaque réunion éligible est extraite, sans arrêt sur erreur."""
        outcome = AgentOutcome()
        for meeting in _eligible_meetings(session):
            self._process_meeting(session, meeting, outcome)
        return outcome

    def regenerate(self, session: Session, meeting_id: int) -> Minutes:
        """La version suivante du compte rendu d'une réunion (specs/06 §4.4).

        Les décisions et actions des versions non validées sont remplacées ;
        une `LLMError` remonte à l'appelant sans rien modifier.
        """
        current = _current_minutes(session, meeting_id)
        if current is None:
            raise ValueError("La réunion n'a pas de compte rendu à régénérer.")
        if current.status == DocStatus.VALIDE:
            raise ValidationBlocked("Un compte rendu validé ne se régénère pas.")
        meeting = session.get(Meeting, meeting_id)
        source = meeting_source(session, meeting_id)
        result = self._llm.complete(
            build_minutes_messages(meeting.title, meeting.date, source), schema=MinutesOut
        )
        _apply_limits(result.parsed)
        _delete_unvalidated_items(session, meeting_id)
        return _create_minutes(session, meeting_id, current.version + 1, result.parsed, source)

    def _process_meeting(self, session: Session, meeting: Meeting, outcome: AgentOutcome) -> None:
        """Extrait et range le compte rendu d'une réunion ; une erreur ne stoppe pas."""
        source = meeting_source(session, meeting.id)
        messages = build_minutes_messages(meeting.title, meeting.date, source)
        outcome.model = self._llm.model
        try:
            result = self._llm.complete(messages, schema=MinutesOut)
        except LLMError:
            session.rollback()  # rien n'est écrit pour cette réunion (specs/06 §4.3, étape 3)
            outcome.errors += 1
            return
        _apply_limits(result.parsed)
        _create_minutes(session, meeting.id, 1, result.parsed, source)
        outcome.processed += 1
        outcome.prompt_tokens += result.prompt_tokens or 0
        outcome.completion_tokens += result.completion_tokens or 0


def meeting_source(session: Session, meeting_id: int) -> str:
    """Le texte source d'une réunion (specs/06 §4.2).

    Avec un seul transcript, c'est son texte ; avec plusieurs, chaque texte
    est précédé d'un intertitre `## Source {n} : {libellé}`, par `id`
    croissant, les blocs séparés par une ligne vide.
    """
    transcripts = session.scalars(
        select(Transcript).where(Transcript.meeting_id == meeting_id).order_by(Transcript.id)
    ).all()
    if len(transcripts) == 1:
        return transcripts[0].text
    blocks = [
        f"## Source {index} : {_SOURCE_LABELS[transcript.source]}\n{transcript.text}"
        for index, transcript in enumerate(transcripts, start=1)
    ]
    return "\n\n".join(blocks)


def _eligible_meetings(session: Session) -> list[Meeting]:
    """Réunions avec au moins un transcript et aucun compte rendu (specs/06 §4.3)."""
    return list(
        session.scalars(
            select(Meeting)
            .where(
                Meeting.id.in_(select(Transcript.meeting_id)),
                ~Meeting.id.in_(select(Minutes.meeting_id)),
            )
            .order_by(Meeting.date, Meeting.id)
        ).all()
    )


def _current_minutes(session: Session, meeting_id: int) -> Minutes | None:
    """Le compte rendu de plus grande version d'une réunion (specs/06 §4.4)."""
    return session.scalars(
        select(Minutes)
        .where(Minutes.meeting_id == meeting_id)
        .order_by(Minutes.version.desc())
    ).first()


def _apply_limits(response: MinutesOut) -> None:
    """Résumé coupé à 120 mots, extraits coupés à 200 caractères (specs/06 §4.3)."""
    response.summary = " ".join(response.summary.split()[:_SUMMARY_MAX_WORDS])
    for item in response.decisions:
        item.quote = _trim_quote(item.quote)
    for item in response.actions:
        item.quote = _trim_quote(item.quote)


def _trim_quote(quote: str) -> str:
    """Un extrait coupé reste une sous-chaîne de la source (specs/06 §4.3, étape 4)."""
    return quote[:_QUOTE_MAX_CHARS]


def _create_minutes(
    session: Session, meeting_id: int, version: int, response: MinutesOut, source: str
) -> Minutes:
    """Le compte rendu et ses éléments, dans une seule transaction (specs/06 §4.3).

    Chaque extrait est contrôlé contre la source et `verified` porte le
    résultat (specs/06 §5).
    """
    minutes = Minutes(
        meeting_id=meeting_id,
        version=version,
        content_md="",  # rempli par le rendu Markdown (V2-06)
        summary=response.summary,
        open_points=list(response.open_points),
        next_meeting=response.next_meeting,
        status=DocStatus.BROUILLON,
        created_at=clock.now(),
    )
    session.add(minutes)
    session.flush()  # l'identifiant du compte rendu est nécessaire aux éléments
    for item in response.decisions:
        _add_decision(session, meeting_id, minutes.id, item, source)
    for item in response.actions:
        _add_action_item(session, meeting_id, minutes.id, item, source)
    session.commit()
    return minutes


def _add_decision(
    session: Session, meeting_id: int, minutes_id: int, item: DecisionOut, source: str
) -> None:
    """Une décision et le contrôle de son extrait (specs/06 §5)."""
    session.add(
        Decision(
            meeting_id=meeting_id,
            minutes_id=minutes_id,
            text=item.text,
            quote=item.quote,
            verified=_is_verified(item.quote, source),
        )
    )


def _add_action_item(
    session: Session, meeting_id: int, minutes_id: int, item: ActionOut, source: str
) -> None:
    """Une action et le contrôle de son extrait (specs/06 §5)."""
    session.add(
        ActionItem(
            meeting_id=meeting_id,
            minutes_id=minutes_id,
            description=item.description,
            owner=item.owner,
            due_date=item.due_date,
            quote=item.quote,
            verified=_is_verified(item.quote, source),
        )
    )


def _is_verified(quote: str, source: str) -> bool:
    """Le contrôle déterministe de l'extrait contre la source (specs/06 §5)."""
    return verify_quote(quote, source).found


def _delete_unvalidated_items(session: Session, meeting_id: int) -> None:
    """Décisions et actions des comptes rendus non validés (specs/06 §4.4, étape 3)."""
    unvalidated = select(Minutes.id).where(
        Minutes.meeting_id == meeting_id, Minutes.status != DocStatus.VALIDE
    )
    session.execute(
        delete(Decision).where(
            Decision.meeting_id == meeting_id, Decision.minutes_id.in_(unvalidated)
        )
    )
    session.execute(
        delete(ActionItem).where(
            ActionItem.meeting_id == meeting_id, ActionItem.minutes_id.in_(unvalidated)
        )
    )
