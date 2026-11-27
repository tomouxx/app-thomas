"""Tests unitaires de l'agent `minutes` (V2-04).

Couvrent les cas attendus par `specs/06 §4.4`, avec une `FakeLLM` et des jeux
dans `tests/fixtures/minutes/`. Chaque test travaille dans un répertoire de
données temporaire (`CABINET_HOME`, fixé par `conftest.py`).
"""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel
from sqlalchemy import text

from cabinet.agents.base import AgentOutcome
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
from cabinet.llm.prompts import GUARD_SENTENCE
from cabinet.llm.prompts.minutes import MinutesOut, build_minutes_messages
from cabinet.llm.types import LLMError, LLMResult, Message

ZURICH = ZoneInfo("Europe/Zurich")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "minutes"


class FakeLLM:
    """Doublure du port `LLM` qui rejoue les réponses fournies, dans l'ordre.

    La dernière réponse se répète pour les appels suivants ; une exception
    fournie est levée à l'appel qui la reçoit.
    """

    model = "fake"

    def __init__(self, *responses: MinutesOut | Exception) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        images: list[Path] | None = None,
    ) -> LLMResult:
        self.calls.append({"messages": messages, "schema": schema, "images": images})
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return LLMResult(text="{}", parsed=response)


def load_response(name: str) -> MinutesOut:
    """Un jeu de test lu dans `tests/fixtures/minutes` et analysé en `MinutesOut`."""
    return MinutesOut.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))


def seed_meeting(title: str, meeting_date: date, source_text: str) -> int:
    """Crée une série, une réunion et un transcript Teams ; renvoie l'identifiant."""
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


def seed_meeting_without_transcript(title: str, meeting_date: date) -> int:
    """Crée une série et une réunion sans transcript ; renvoie l'identifiant."""
    init_db()
    with get_session() as session:
        series = Series(key=f"serie-{title}", label=title)
        session.add(series)
        session.flush()
        meeting = Meeting(series_id=series.id, title=title, date=meeting_date)
        session.add(meeting)
        session.commit()
        return meeting.id


def run_agent(llm: FakeLLM) -> AgentOutcome:
    """Un passage de l'agent sur une session neuve."""
    with get_session() as session:
        return MinutesAgent(llm).run(session)


def minutes_rows(meeting_id: int) -> list[dict[str, Any]]:
    """Les lignes `minutes` d'une réunion (id, version, résumé)."""
    with get_engine().connect() as connection:
        rows = list(
            connection.execute(
                text("SELECT id, version, summary FROM minutes WHERE meeting_id = :id"),
                {"id": meeting_id},
            ).mappings()
        )
    return [dict(row) for row in rows]


def item_rows(meeting_id: int, table: str) -> list[dict[str, Any]]:
    """Les lignes `decision` ou `action_item` d'une réunion."""
    statement = text(f"SELECT * FROM {table} WHERE meeting_id = :id")
    with get_engine().connect() as connection:
        rows = list(connection.execute(statement, {"id": meeting_id}).mappings())
    return [dict(row) for row in rows]


def test_run_cree_le_compte_rendu_version_1_avec_les_valeurs_du_jeu() -> None:
    """Le compte rendu version 1 porte les valeurs du jeu de test (specs/06 §4.3)."""
    fixture = load_response("reponse.json")
    meeting_id = seed_meeting("Point hebdo", date(2026, 11, 23), "Source de la réunion.")

    outcome = run_agent(FakeLLM(fixture))

    assert (outcome.processed, outcome.errors) == (1, 0)
    rows = minutes_rows(meeting_id)
    assert [row["version"] for row in rows] == [1]
    assert rows[0]["summary"] == fixture.summary
    decisions = item_rows(meeting_id, "decision")
    assert [(row["text"], row["quote"]) for row in decisions] == [
        (item.text, item.quote) for item in fixture.decisions
    ]
    actions = item_rows(meeting_id, "action_item")
    assert [(row["description"], row["owner"], str(row["due_date"])) for row in actions] == [
        (item.description, item.owner, item.due_date.isoformat()) for item in fixture.actions
    ]


def test_prompt_porte_balises_source_et_phrase_de_garde() -> None:
    """Le prompt porte les balises `<source>` et la phrase de garde exacte (specs/06 §4.1)."""
    seed_meeting("Point hebdo", date(2026, 11, 23), "Texte de la source.")
    llm = FakeLLM(load_response("reponse.json"))

    run_agent(llm)

    expected = build_minutes_messages("Point hebdo", date(2026, 11, 23), "Texte de la source.")
    assert llm.calls[0]["messages"] == expected
    system, user = llm.calls[0]["messages"]
    assert system.content.endswith(GUARD_SENTENCE)
    assert user.content == (
        "Réunion : Point hebdo, 23.11.2026.\n<source>\nTexte de la source.\n</source>"
    )


def test_llm_error_ne_cree_rien_et_passe_a_la_reunion_suivante() -> None:
    """Une `LLMError` ne laisse aucune ligne ; la réunion suivante est traitée."""
    first = seed_meeting("Première", date(2026, 11, 23), "Source un.")
    second = seed_meeting("Suivante", date(2026, 11, 30), "Source deux.")
    llm = FakeLLM(LLMError("panne du modèle"), load_response("reponse.json"))

    outcome = run_agent(llm)

    assert (outcome.processed, outcome.errors) == (1, 1)
    assert minutes_rows(first) == []
    assert len(minutes_rows(second)) == 1


def test_reunions_sans_transcript_ou_deja_traitees_sont_ignorees() -> None:
    """Sans transcript, ou déjà dotée d'un compte rendu, la réunion est ignorée."""
    no_source = seed_meeting_without_transcript("Sans source", date(2026, 11, 23))
    seed_meeting("Traitee", date(2026, 11, 30), "Source déjà traitée.")
    run_agent(FakeLLM(load_response("reponse.json")))  # crée le compte rendu de « Traitee »
    llm = FakeLLM(load_response("reponse.json"))

    outcome = run_agent(llm)

    assert (outcome.processed, outcome.errors) == (0, 0)
    assert llm.calls == []
    assert minutes_rows(no_source) == []


def test_regenerer_cree_la_version_2_et_conserve_la_version_1() -> None:
    """`regenerate` crée la version 2 ; la ligne de la version 1 reste en base."""
    fixture = load_response("reponse.json")
    meeting_id = seed_meeting("Point hebdo", date(2026, 11, 23), "Source de la réunion.")
    run_agent(FakeLLM(fixture))

    with get_session() as session:
        created = MinutesAgent(FakeLLM(fixture)).regenerate(session, meeting_id)

    assert [row["version"] for row in minutes_rows(meeting_id)] == [1, 2]
    assert created.version == 2


def test_regenerer_sans_compte_rendu_leve_value_error() -> None:
    """Une réunion sans compte rendu ne se régénère pas ; le modèle n'est pas appelé."""
    meeting_id = seed_meeting("Point hebdo", date(2026, 11, 23), "Source de la réunion.")
    llm = FakeLLM(load_response("reponse.json"))

    with get_session() as session, pytest.raises(ValueError):
        MinutesAgent(llm).regenerate(session, meeting_id)

    assert llm.calls == []


def test_owner_et_due_date_a_none_quand_le_modele_renvoie_null() -> None:
    """`owner` et `due_date` sont stockés à NULL quand le modèle renvoie null."""
    meeting_id = seed_meeting("Point hebdo", date(2026, 11, 23), "Source de la réunion.")

    run_agent(FakeLLM(load_response("reponse_nuls.json")))

    actions = item_rows(meeting_id, "action_item")
    assert [(row["owner"], row["due_date"]) for row in actions] == [(None, None)]


def test_run_traite_par_date_puis_par_identifiant() -> None:
    """Les réunions sont traitées par date, puis par identifiant (specs/06 §4.3)."""
    seed_meeting("Plus tard", date(2026, 11, 30), "Source du trente.")
    seed_meeting("Plus tôt", date(2026, 11, 23), "Source du vingt-trois.")
    llm = FakeLLM(load_response("reponse.json"))

    run_agent(llm)

    sources = [appel["messages"][1].content for appel in llm.calls]
    assert "Source du vingt-trois" in sources[0]
    assert "Source du trente" in sources[1]


def test_tokens_absents_du_resultat_sont_comptes_zero() -> None:
    """Un résultat sans tokens n'ajoute rien aux totaux (specs/05 §2.3)."""
    seed_meeting("Point hebdo", date(2026, 11, 23), "Source de la réunion.")
    llm = FakeLLM(load_response("reponse.json"))  # `LLMResult` sans tokens

    outcome = run_agent(llm)

    assert (outcome.prompt_tokens, outcome.completion_tokens) == (0, 0)
