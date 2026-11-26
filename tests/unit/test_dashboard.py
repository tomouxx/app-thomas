"""Tests unitaires du tableau de bord et des réglages (V1-05).

Complètent les tests de contrat (`tests/contrats/test_v1_05_tableau_de_bord.py`)
sur les cas qu'ils ne couvrent pas : texte de l'échéance, bornes et tri de
la fenêtre des réunions, compte rendu courant, sections de la page Réglages.
Chaque test travaille dans un répertoire de données temporaire (`CABINET_HOME`,
fixé par `conftest.py`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cabinet.config import load_settings
from cabinet.db.base import get_session, init_db
from cabinet.db.models import (
    DocStatus,
    Event,
    Meeting,
    Minutes,
    Series,
)
from cabinet.web.app import create_app
from cabinet.web.routes.dashboard import (
    _count_draft_minutes,
    dashboard_counters,
    deadline_text,
    settings_sections,
    upcoming_meetings,
)

ZURICH = ZoneInfo("Europe/Zurich")
TODAY = date(2026, 11, 24)


@pytest.fixture
def set_date(monkeypatch: pytest.MonkeyPatch):
    def fixer(value: datetime) -> None:
        monkeypatch.setattr("cabinet.clock.now", lambda: value)

    return fixer


@pytest.fixture(autouse=True)
def fixed_today(set_date):
    """Mardi 24.11.2026, comme les tests de contrat du ticket."""
    set_date(datetime(2026, 11, 24, 8, 0, tzinfo=ZURICH))


def add_series(session: Session, key: str, followed: bool = False) -> Series:
    series = Series(key=key, label=key.title(), followed=followed)
    session.add(series)
    session.flush()
    return series


def add_meeting(
    session: Session, day: date, title: str = "Meeting", start: datetime | None = None
) -> Meeting:
    series = add_series(session, title.lower().replace(" ", "-"))
    event_id = None
    if start is not None:
        event = Event(uid=f"evt-{title}", title=title, start=start, end=start + timedelta(hours=1))
        session.add(event)
        session.flush()
        event_id = event.id
    meeting = Meeting(series_id=series.id, title=title, date=day, event_id=event_id)
    session.add(meeting)
    session.commit()
    return meeting


def add_minutes(session: Session, meeting_id: int, versions: list[tuple[int, DocStatus]]) -> None:
    for version, status in versions:
        session.add(
            Minutes(
                meeting_id=meeting_id,
                version=version,
                summary="Résumé synthétique.",
                status=status,
                created_at=datetime(2026, 11, 20, 12, 0, tzinfo=ZURICH),
            )
        )
    session.commit()


# --- Texte de l'échéance -----------------------------------------------------


def test_deadline_text_none_when_unset() -> None:
    assert deadline_text(None, TODAY) is None


def test_deadline_text_j_minus_n_in_the_future() -> None:
    assert deadline_text(date(2026, 11, 27), TODAY) == "J-3"


def test_deadline_text_today() -> None:
    assert deadline_text(TODAY, TODAY) == "aujourd'hui"


def test_deadline_text_in_the_past() -> None:
    assert deadline_text(date(2026, 11, 20), TODAY) == "échéance dépassée"


# --- Fenêtre des réunions à venir --------------------------------------------


def test_upcoming_meetings_excludes_eight_days_later() -> None:
    init_db()
    with get_session() as session:
        add_meeting(session, TODAY + timedelta(days=7), "Last day of window")
        add_meeting(session, TODAY + timedelta(days=8), "Beyond the window")
        titles = [row["title"] for row in upcoming_meetings(session)]
    assert "Last day of window" in titles
    assert "Beyond the window" not in titles


def test_upcoming_meetings_sorted_by_date_then_time() -> None:
    init_db()
    with get_session() as session:
        afternoon = datetime(2026, 11, 24, 14, 0, tzinfo=ZURICH)
        morning = datetime(2026, 11, 24, 9, 0, tzinfo=ZURICH)
        add_meeting(session, TODAY, "Budget review", afternoon)
        add_meeting(session, TODAY, "Morning point", morning)
        add_meeting(session, TODAY + timedelta(days=2), "Workshop")
        rows = upcoming_meetings(session)
    assert [row["title"] for row in rows] == ["Morning point", "Budget review", "Workshop"]
    assert rows[0]["time"] == "09:00"
    assert rows[1]["time"] == "14:00"


def test_meeting_without_event_has_no_time() -> None:
    init_db()
    with get_session() as session:
        add_meeting(session, TODAY + timedelta(days=2), "Workshop")
        rows = upcoming_meetings(session)
    assert len(rows) == 1
    assert rows[0]["time"] is None


# --- Compte rendu courant ----------------------------------------------------


def test_current_minutes_valide_is_not_counted() -> None:
    """Le compte rendu courant est la version maximale (specs/02 §2.8)."""
    init_db()
    with get_session() as session:
        first = add_meeting(session, date(2026, 11, 10), "Kickoff")
        second = add_meeting(session, date(2026, 11, 12), "Follow up")
        add_minutes(session, first.id, [(1, DocStatus.VALIDE), (2, DocStatus.BROUILLON)])
        add_minutes(session, second.id, [(1, DocStatus.BROUILLON), (2, DocStatus.VALIDE)])
        assert _count_draft_minutes(session) == 1


def test_counters_zero_on_an_empty_base() -> None:
    init_db()
    with get_session() as session:
        assert dashboard_counters(session) == {
            "inbox": 0,
            "drafts": 0,
            "actions": 0,
            "minutes": 0,
        }


# --- Page Réglages -----------------------------------------------------------


def test_settings_sections_cover_every_value() -> None:
    sections = settings_sections(load_settings())
    names = [section["name"] for section in sections]
    assert names == ["llm", "transcription", "agenda", "cra", "cra.regles", "ui"]
    by_name = {section["name"]: dict(section["fields"]) for section in sections}
    assert by_name["llm"]["base_url"] == "http://127.0.0.1:8080/v1"
    assert by_name["llm"]["timeout_s"] == "900.0"
    assert by_name["transcription"]["delete_audio_after_validation"] == "oui"
    assert by_name["cra"]["deadline"] == "(vide)"
    assert by_name["cra.regles"]["R1"] == "oui"
    assert by_name["ui"]["port"] == "8765"


def test_dashboard_page_announces_no_upcoming_meeting() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Aucune réunion dans les sept prochains jours" in response.text


def test_settings_page_shows_empty_deadline() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/reglages")
    assert response.status_code == 200
    assert "(vide)" in response.text
