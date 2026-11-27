"""Prompt du compte rendu : schémas de sortie et construction des messages.

Le message système est le texte exact de `specs/06 §4.1`, suivi de la phrase
de garde (`GUARD_SENTENCE`). Le message utilisateur ne porte que les
métadonnées de l'application ; la source est entre balises `<source>`, et un
`</source>` contenu dans la source est échappé pour ne pas fermer la balise
(`specs/04 §4`, règle 4). Les limites de longueur (120 mots, 200 caractères)
sont demandées au modèle par le prompt ; l'agent tronque la réponse
(`specs/02 §3.5`).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from cabinet.llm.prompts import GUARD_SENTENCE
from cabinet.llm.types import Message

_SYSTEM_TEXT = (
    "Tu rédiges le compte rendu d'une réunion à partir de sa source.\n"
    "Règles :\n"
    "- N'invente rien. Tout ce que tu écris vient de la source.\n"
    "- Le résumé fait 120 mots au plus.\n"
    "- Chaque décision et chaque action porte un extrait recopié mot pour mot de la source, "
    "de 200 caractères au plus.\n"
    "- Le responsable d'une action n'est donné que si un nom est dit dans la source. "
    "Sinon null.\n"
    "- L'échéance d'une action n'est donnée que si une date est dite dans la source, "
    "au format AAAA-MM-JJ. Sinon null.\n"
    "- Les points ouverts sont les questions restées sans décision.\n"
    "- La prochaine séance n'est donnée que si elle est dite dans la source. Sinon null.\n"
    "- S'il n'y a ni décision ni action, renvoie des listes vides."
)


class DecisionOut(BaseModel):
    """Une décision extraite de la source (specs/02 §3.5)."""

    text: str
    quote: str  # extrait mot pour mot de la source, 200 caractères au plus


class ActionOut(BaseModel):
    """Une action extraite de la source (specs/02 §3.5)."""

    description: str
    owner: str | None  # seulement si un nom est dit dans la source
    due_date: date | None  # seulement si une date est dite dans la source
    quote: str


class MinutesOut(BaseModel):
    """Extraction structurée du compte rendu d'une réunion (specs/02 §3.5)."""

    summary: str  # 120 mots au plus
    decisions: list[DecisionOut]
    actions: list[ActionOut]
    open_points: list[str]
    next_meeting: str | None


def build_minutes_messages(
    meeting_title: str, meeting_date: date, source_text: str
) -> list[Message]:
    """Les deux messages de l'appel : système (règles + garde) et utilisateur."""
    system = _SYSTEM_TEXT + "\n" + GUARD_SENTENCE
    user = (
        f"Réunion : {meeting_title}, {_format_date(meeting_date)}.\n"
        f"<source>\n{_escape_source(source_text)}\n</source>"
    )
    return [Message(role="system", content=system), Message(role="user", content=user)]


def _format_date(meeting_date: date) -> str:
    """Date de la réunion au format JJ.MM.AAAA (specs/06 §4.1)."""
    return meeting_date.strftime("%d.%m.%Y")


def _escape_source(source_text: str) -> str:
    """Une balise de clôture dans la source ne ferme pas la balise (specs/04 §4)."""
    return source_text.replace("</source>", "<\\/source>")
