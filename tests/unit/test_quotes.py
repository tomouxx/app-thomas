"""Tests unitaires du contrôle des extraits (V2-05, specs/06 §5).

Couvrent les cas que les tests de contrat ne couvrent pas : bornes de
longueur, seuil atteint exactement, source plus courte que l'extrait,
fenêtres glissantes à un décalage non nul, durée sur une source de 200 000
caractères et `verified` fixé par l'agent. Chaque test travaille dans un
répertoire de données temporaire (`CABINET_HOME`, fixé par `conftest.py`).
"""

from __future__ import annotations

import difflib
import random
import time
from datetime import date, datetime
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel
from sqlalchemy import text

from cabinet.agents.minutes import MinutesAgent
from cabinet.db.base import get_engine, get_session, init_db
from cabinet.db.models import (
    InboxItem,
    InboxKind,
    InboxStatus,
    Meeting,
    Series,
    Transcript,
    TranscriptSource,
)
from cabinet.llm.prompts.minutes import MinutesOut
from cabinet.llm.types import LLMResult, Message
from cabinet.services.quotes import QuoteCheck, normalize, verify_quote

ZURICH = ZoneInfo("Europe/Zurich")

# Phrase de vingt mots et sa copie à deux mots changés, de même longueur.
ORIGINAL = (
    "Je préviens l'équipe support et le client avant vendredi soir, "
    "puis je mets à jour le planning du lot deux."
)
CHANGED = (
    "Je préviens l'équipe support et le client avant mercredi soir, "
    "puis je mets à jour le planning du lot neuf."
)


class FakeLLM:
    """Doublure du port `LLM` qui rejoue une réponse unique."""

    model = "fake"

    def __init__(self, response: MinutesOut) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        images: list[Any] | None = None,
    ) -> LLMResult:
        self.calls.append({"messages": messages, "schema": schema, "images": images})
        return LLMResult(text="{}", parsed=self.response)


def seed_meeting(title: str, meeting_date: date, source_text: str) -> int:
    """Crée une série, une réunion et un transcript ; renvoie l'identifiant."""
    init_db()
    with get_session() as session:
        series = Series(key=f"serie-{title}", label=title)
        session.add(series)
        session.flush()
        meeting = Meeting(series_id=series.id, title=title, date=meeting_date)
        session.add(meeting)
        session.flush()
        item = InboxItem(
            path=f"entree/{title}.txt",
            sha256=sha256(f"{title} {meeting_date}".encode()).hexdigest(),
            kind=InboxKind.TEAMS_TRANSCRIPT,
            status=InboxStatus.PRET,
            meeting_id=meeting.id,
            detected_at=datetime(2026, 11, 30, 9, 0, tzinfo=ZURICH),
        )
        session.add(item)
        session.flush()
        session.add(
            Transcript(
                meeting_id=meeting.id,
                inbox_item_id=item.id,
                source=TranscriptSource.TEAMS,
                text=source_text,
            )
        )
        session.commit()
        return meeting.id


def item_rows(meeting_id: int, table_name: str) -> list[dict[str, Any]]:
    """Les lignes `decision` ou `action_item` d'une réunion."""
    statement = text(f"SELECT * FROM {table_name} WHERE meeting_id = :id")
    with get_engine().connect() as connection:
        rows = list(connection.execute(statement, {"id": meeting_id}).mappings())
    return [dict(row) for row in rows]


def long_source(size: int = 200_000) -> str:
    """Texte synthétique déterministe de `size` caractères, en lignes de mots."""
    words = ("projet", "equipe", "budget", "planning", "livrable", "comite", "client")
    generator = random.Random(2025)
    lines: list[str] = []
    length = 0
    while length < size:
        line = " ".join(generator.choice(words) for _ in range(generator.randint(8, 16)))
        lines.append(line.capitalize() + ".")
        length += len(lines[-1]) + 1
    return "\n".join(lines)[:size]


# --- normalize ---------------------------------------------------------------


def test_normalize_empty_and_all_punctuation() -> None:
    assert normalize("") == ""
    assert normalize("!!! ??? « »") == ""


def test_normalize_keeps_letters_and_digits() -> None:
    assert normalize("N°45 : 3,14 €") == "n 45 3 14"


# --- verify_quote ------------------------------------------------------------


def test_exact_quote_found_despite_case_accents_punctuation() -> None:
    source = "Claire Dubois : D'accord, on valide le report de la mise en production."
    quote = "D'ACCORD ; ON VALIDE LE REPORT DE LA MISE EN PRODUCTION"

    result = verify_quote(quote, source)

    assert isinstance(result, QuoteCheck)
    assert (result.found, result.score) == (True, 1.0)


def test_similar_quote_at_offset_is_found_by_windows() -> None:
    """L'extrait n'est pas en tête de source : ce sont les fenêtres qui le trouvent."""
    prefix = " ".join(["un"] * 10) + " "  # 29 caractères, un décalage non nul
    source = prefix + ORIGINAL

    result = verify_quote(CHANGED, source)

    assert result.found is True
    assert 0.85 <= result.score < 1.0


def test_invented_quote_is_not_found() -> None:
    source = "Le budget du lot deux reste à quarante-cinq mille francs."
    quote = "Le fournisseur livrera les licences gratuitement au service juridique."

    result = verify_quote(quote, source)

    assert result.found is False
    assert 0.0 <= result.score < 0.85


@pytest.mark.parametrize("quote", ["", "abc", "budget va", "lot 2"])
def test_quote_under_ten_chars_never_found(quote: str) -> None:
    """Moins de 10 caractères une fois normalisé : jamais trouvé, même présent."""
    source = "Le budget va au lot 2 et à rien d'autre."

    result = verify_quote(quote, source)

    assert (result.found, result.score) == (False, 0.0)


def test_quote_of_exactly_ten_chars_is_checked() -> None:
    """Dix caractères normalisés : la borne est atteinte, l'extrait est contrôlé."""
    source = "Le budget val ide reste inchangé."

    result = verify_quote("budget val", source)

    assert (result.found, result.score) == (True, 1.0)


def test_threshold_equal_to_best_score_is_found() -> None:
    """`found` est vrai dès que le meilleur score atteint le seuil (specs/06 §5, étape 6)."""
    prefix = " ".join(["un"] * 10) + " "
    source = prefix + ORIGINAL
    default = verify_quote(CHANGED, source)

    assert 0.0 < default.score < 1.0
    assert verify_quote(CHANGED, source, threshold=default.score).found is True
    assert verify_quote(CHANGED, source, threshold=min(default.score + 0.05, 1.0)).found is False


def test_short_source_is_compared_whole() -> None:
    """Source plus courte que l'extrait : comparaison en entier (specs/06 §5, étape 4)."""
    source = "Budget validé."
    quote = "Le budget du lot deux est validé par le comité de pilotage."
    expected = difflib.SequenceMatcher(
        None, normalize(source), normalize(quote), autojunk=False
    ).ratio()

    result = verify_quote(quote, source)

    assert result.found is False
    assert result.score == pytest.approx(expected)


def test_quote_checked_in_under_two_seconds_on_200k_source() -> None:
    source = long_source()
    assert len(source) == 200_000
    middle = len(source) // 2
    source = source[:middle] + ORIGINAL + source[middle + len(ORIGINAL) :]

    start = time.perf_counter()
    result = verify_quote(CHANGED, source)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"{elapsed:.2f} s"
    assert 0.0 <= result.score <= 1.0


# --- agent -------------------------------------------------------------------


def test_agent_sets_verified_on_decisions_and_actions() -> None:
    """Après l'agent, `verified` est vrai si l'extrait existe dans la source."""
    source = (
        "Claire Dubois : D'accord, on valide le report de la mise en production.\n"
        "Marc Petit : Le budget du lot deux reste à quarante-cinq mille francs."
    )
    meeting_id = seed_meeting("Point hebdo", date(2026, 11, 23), source)
    response = MinutesOut(
        summary="Le report est validé.",
        decisions=[
            {
                "text": "Report de la mise en production validé.",
                "quote": "D'ACCORD, ON VALIDE LE REPORT DE LA MISE EN PRODUCTION",
            }
        ],
        actions=[
            {
                "description": "Suivre le budget du lot deux.",
                "owner": None,
                "due_date": None,
                "quote": "Le fournisseur livrera les licences gratuitement en janvier.",
            }
        ],
        open_points=[],
        next_meeting=None,
    )

    with get_session() as session:
        MinutesAgent(FakeLLM(response)).run(session)

    decisions = {row["text"]: row["verified"] for row in item_rows(meeting_id, "decision")}
    actions = {row["description"]: row["verified"] for row in item_rows(meeting_id, "action_item")}
    assert decisions == {"Report de la mise en production validé.": True}
    assert actions == {"Suivre le budget du lot deux.": False}
