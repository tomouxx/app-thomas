"""Tests unitaires de la page Entrée, du rattachement et du consentement (V1-06).

Complètent les tests de contrat (`tests/contrats/test_v1_06_entree.py`) sur les
cas qu'ils ne couvrent pas : date illisible, `meeting_id` non entier, éléments
inconnus en 404 sur les trois actions, rejoués selon la nature, boutons de la
page. Chaque test travaille dans un répertoire de données temporaire
(`CABINET_HOME`, fixé par `conftest.py`).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from html import unescape
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet.db.base import get_session, init_db
from cabinet.db.models import InboxItem, InboxKind, InboxStatus, Meeting, Series
from cabinet.services.matching import suggest_meetings
from cabinet.services.series import series_key
from cabinet.web.app import create_app

ZURICH = ZoneInfo("Europe/Zurich")
MAINTENANT = datetime(2026, 11, 24, 10, 0, tzinfo=ZURICH)
PHRASE_CONSENTEMENT = "J'ai l'accord des participants pour cet enregistrement"
GROUP_LABELS = (
    "À rattacher",
    "Consentement requis",
    "À traiter",
    "En erreur",
    "Traités",
    "Écartés",
)


@pytest.fixture
def set_date(monkeypatch: pytest.MonkeyPatch):
    def fixer(value: datetime) -> None:
        monkeypatch.setattr("cabinet.clock.now", lambda: value)

    return fixer


@pytest.fixture(autouse=True)
def fixed_now(set_date):
    """Mardi 24.11.2026 à 10 h, comme les tests de contrat du ticket."""
    set_date(MAINTENANT)


def add_meeting(session: Session, day: date, title: str) -> Meeting:
    series = session.scalar(select(Series).where(Series.key == series_key(title)))
    if series is None:
        series = Series(key=series_key(title), label=title)
        session.add(series)
        session.flush()
    meeting = Meeting(series_id=series.id, title=title, date=day)
    session.add(meeting)
    session.commit()
    return meeting


def add_item(
    session: Session,
    path: str,
    kind: InboxKind,
    status: InboxStatus,
    name_date: date | None = None,
    meeting_id: int | None = None,
    consent_confirmed: bool = False,
    error: str | None = None,
    detected_at: datetime = MAINTENANT,
) -> InboxItem:
    item = InboxItem(
        path=path,
        sha256=hashlib.sha256(path.encode("utf-8")).hexdigest(),
        kind=kind,
        status=status,
        name_date=name_date,
        meeting_id=meeting_id,
        consent_confirmed=consent_confirmed,
        error=error,
        detected_at=detected_at,
    )
    session.add(item)
    session.commit()
    return item


def get_item(session: Session, item_id: int) -> InboxItem:
    return session.get(InboxItem, item_id)


def row_count(session: Session, model: type) -> int:
    return len(session.scalars(select(model)).all())


# --- Suggestions -------------------------------------------------------------


def test_suggestions_only_meetings_of_the_reference_day_sorted_by_similarity() -> None:
    init_db()
    with get_session() as session:
        point = add_meeting(session, date(2026, 11, 23), "Point hebdo équipe")
        comite = add_meeting(session, date(2026, 11, 23), "Comité de pilotage")
        add_meeting(session, date(2026, 11, 24), "Point hebdo équipe")
        item = add_item(
            session,
            "entree/2026-11-23_point-hebdo-equipe.vtt",
            InboxKind.TEAMS_TRANSCRIPT,
            InboxStatus.A_RATTACHER,
            name_date=date(2026, 11, 23),
        )
        suggestions = suggest_meetings(session, item)
    assert [meeting.id for meeting, _ in suggestions] == [point.id, comite.id]
    assert suggestions[0][1] > suggestions[1][1]


def test_suggestions_fall_back_to_detection_day_in_local_tz() -> None:
    init_db()
    with get_session() as session:
        meeting = add_meeting(session, date(2026, 11, 24), "Point hebdo")
        add_meeting(session, date(2026, 11, 23), "Point hebdo")
        item = add_item(
            session,
            "entree/notes-revue.md",
            InboxKind.NOTES,
            InboxStatus.A_RATTACHER,
            detected_at=datetime(2026, 11, 24, 9, 15, tzinfo=ZURICH),
        )
        suggestions = suggest_meetings(session, item)
    assert [row.id for row, _ in suggestions] == [meeting.id]


def test_suggestions_respect_the_limit() -> None:
    init_db()
    with get_session() as session:
        for number in range(1, 7):
            add_meeting(session, date(2026, 11, 23), f"Réunion numéro {number}")
        item = add_item(
            session,
            "entree/2026-11-23_sans-reunion.vtt",
            InboxKind.TEAMS_TRANSCRIPT,
            InboxStatus.A_RATTACHER,
            name_date=date(2026, 11, 23),
        )
        default = suggest_meetings(session, item)
        limited = suggest_meetings(session, item, limit=2)
    assert len(default) == 5
    assert len(limited) == 2


# --- Rattachement ------------------------------------------------------------


def test_attach_transcript_to_existing_meeting() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            meeting = add_meeting(session, date(2026, 11, 23), "Point hebdo équipe")
            item = add_item(
                session,
                "entree/2026-11-23_point-hebdo-equipe.vtt",
                InboxKind.TEAMS_TRANSCRIPT,
                InboxStatus.A_RATTACHER,
            )
        response = client.post(
            f"/entree/{item.id}/rattacher",
            data={"meeting_id": str(meeting.id)},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/entree"
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.meeting_id == meeting.id
    assert saved.status == InboxStatus.PRET


def test_attach_audio_without_consent_is_refused() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            meeting = add_meeting(session, date(2026, 11, 23), "Comité de pilotage")
            item = add_item(
                session,
                "entree/2026-11-23_comite-de-pilotage.m4a",
                InboxKind.AUDIO,
                InboxStatus.CONSENTEMENT_REQUIS,
            )
        response = client.post(
            f"/entree/{item.id}/rattacher",
            data={"meeting_id": str(meeting.id)},
            follow_redirects=False,
        )
    assert response.status_code == 400
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.CONSENTEMENT_REQUIS
    assert saved.meeting_id is None
    assert saved.consent_confirmed is False


def test_attach_audio_with_consent_sets_consent_at() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            meeting = add_meeting(session, date(2026, 11, 23), "Comité de pilotage")
            item = add_item(
                session,
                "entree/2026-11-23_comite-de-pilotage.m4a",
                InboxKind.AUDIO,
                InboxStatus.CONSENTEMENT_REQUIS,
            )
        response = client.post(
            f"/entree/{item.id}/rattacher",
            data={"meeting_id": str(meeting.id), "consentement": "on"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.PRET
    assert saved.consent_confirmed is True
    assert saved.consent_at == MAINTENANT


def test_attach_off_agenda_creates_meeting_and_series() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/notes-atelier.md", InboxKind.NOTES, InboxStatus.A_RATTACHER
            )
        response = client.post(
            f"/entree/{item.id}/rattacher",
            data={"titre": "Atelier découverte client", "date": "2026-11-24"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    with get_session() as session:
        saved = get_item(session, item.id)
        meeting = session.get(Meeting, saved.meeting_id)
        series = session.get(Series, meeting.series_id)
    assert saved.status == InboxStatus.PRET
    assert meeting.event_id is None
    assert meeting.title == "Atelier découverte client"
    assert meeting.date == date(2026, 11, 24)
    assert series.key == "atelier decouverte client"


def test_attach_off_agenda_reuses_existing_series() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            add_meeting(session, date(2026, 11, 24), "Point hebdo")
            item = add_item(
                session, "entree/notes-reprise.md", InboxKind.NOTES, InboxStatus.A_RATTACHER
            )
        response = client.post(
            f"/entree/{item.id}/rattacher",
            data={"titre": "RE: Point hebdo 12", "date": "2026-11-24"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    with get_session() as session:
        series = list(session.scalars(select(Series)).all())
    assert len(series) == 1
    assert series[0].key == "point hebdo"
    assert row_count(session, Meeting) == 2


def test_attach_off_agenda_requires_title_and_date() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/notes.md", InboxKind.NOTES, InboxStatus.A_RATTACHER
            )
        for payload in ({"titre": "Atelier"}, {"date": "2026-11-24"}, {}):
            response = client.post(
                f"/entree/{item.id}/rattacher", data=payload, follow_redirects=False
            )
            assert response.status_code == 400
    with get_session() as session:
        assert row_count(session, Meeting) == 0
        assert row_count(session, Series) == 0


def test_attach_off_agenda_rejects_unreadable_date() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/notes.md", InboxKind.NOTES, InboxStatus.A_RATTACHER
            )
        response = client.post(
            f"/entree/{item.id}/rattacher",
            data={"titre": "Atelier", "date": "24/11/2026"},
            follow_redirects=False,
        )
    assert response.status_code == 400
    with get_session() as session:
        assert row_count(session, Meeting) == 0


def test_attach_unknown_meeting_is_refused() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/notes.md", InboxKind.NOTES, InboxStatus.A_RATTACHER
            )
        response = client.post(
            f"/entree/{item.id}/rattacher",
            data={"meeting_id": "999"},
            follow_redirects=False,
        )
    assert response.status_code == 404
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.A_RATTACHER
    assert saved.meeting_id is None


def test_attach_non_integer_meeting_id_is_refused() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/notes.md", InboxKind.NOTES, InboxStatus.A_RATTACHER
            )
        response = client.post(
            f"/entree/{item.id}/rattacher",
            data={"meeting_id": "abc"},
            follow_redirects=False,
        )
    assert response.status_code == 404
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.A_RATTACHER


def test_attach_traite_item_is_refused() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            meeting = add_meeting(session, date(2026, 11, 20), "Point hebdo")
            item = add_item(
                session,
                "entree/traite.vtt",
                InboxKind.TEAMS_TRANSCRIPT,
                InboxStatus.TRAITE,
                meeting_id=meeting.id,
            )
        first = client.post(
            f"/entree/{item.id}/rattacher",
            data={"meeting_id": str(meeting.id)},
            follow_redirects=False,
        )
        second = client.post(
            f"/entree/{item.id}/rattacher",
            data={"titre": "Atelier", "date": "2026-11-24"},
            follow_redirects=False,
        )
    assert first.status_code == 409
    assert second.status_code == 409
    with get_session() as session:
        saved = get_item(session, item.id)
        assert row_count(session, Meeting) == 1
    assert saved.status == InboxStatus.TRAITE


def test_attach_capture_is_refused() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/app_nov.html", InboxKind.APP_CAPTURE_TEXT, InboxStatus.PRET
            )
        response = client.post(
            f"/entree/{item.id}/rattacher",
            data={"titre": "Atelier", "date": "2026-11-24"},
            follow_redirects=False,
        )
    assert response.status_code == 409
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.PRET
    assert saved.meeting_id is None


def test_attach_unknown_item_is_404() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/entree/999/rattacher", data={"meeting_id": "1"}, follow_redirects=False
        )
    assert response.status_code == 404


# --- Ignorer -----------------------------------------------------------------


def test_ignore_moves_item_to_ignore() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/brouillon.md", InboxKind.NOTES, InboxStatus.A_RATTACHER
            )
        response = client.post(f"/entree/{item.id}/ignorer", follow_redirects=False)
    assert response.status_code == 303
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.IGNORE


def test_ignore_traite_is_refused() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/traite.vtt", InboxKind.TEAMS_TRANSCRIPT, InboxStatus.TRAITE
            )
        response = client.post(f"/entree/{item.id}/ignorer", follow_redirects=False)
    assert response.status_code == 409
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.TRAITE


def test_ignore_unknown_item_is_404() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/entree/999/ignorer", follow_redirects=False)
    assert response.status_code == 404


# --- Réessayer ---------------------------------------------------------------


def test_retry_transcript_with_meeting_goes_pret() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            meeting = add_meeting(session, date(2026, 11, 23), "Revue budget")
            item = add_item(
                session,
                "entree/2026-11-23_revue-budget.vtt",
                InboxKind.TEAMS_TRANSCRIPT,
                InboxStatus.ERREUR,
                meeting_id=meeting.id,
                error="Lecture impossible.",
            )
        response = client.post(f"/entree/{item.id}/reessayer", follow_redirects=False)
    assert response.status_code == 303
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.PRET
    assert saved.error is None


def test_retry_transcript_without_meeting_goes_a_rattacher() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session,
                "entree/2026-11-23_sans-reunion.vtt",
                InboxKind.TEAMS_TRANSCRIPT,
                InboxStatus.ERREUR,
                error="Lecture impossible.",
            )
        response = client.post(f"/entree/{item.id}/reessayer", follow_redirects=False)
    assert response.status_code == 303
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.A_RATTACHER


def test_retry_audio_without_consent_goes_consentement_requis() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session,
                "entree/enregistrement-atelier.wav",
                InboxKind.AUDIO,
                InboxStatus.ERREUR,
                error="Transcription impossible.",
            )
        response = client.post(f"/entree/{item.id}/reessayer", follow_redirects=False)
    assert response.status_code == 303
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.CONSENTEMENT_REQUIS


def test_retry_audio_with_consent_and_meeting_goes_pret() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            meeting = add_meeting(session, date(2026, 11, 23), "Revue budget")
            item = add_item(
                session,
                "entree/2026-11-23_revue-budget.m4a",
                InboxKind.AUDIO,
                InboxStatus.ERREUR,
                meeting_id=meeting.id,
                consent_confirmed=True,
                error="Transcription impossible.",
            )
        response = client.post(f"/entree/{item.id}/reessayer", follow_redirects=False)
    assert response.status_code == 303
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.PRET


def test_retry_capture_goes_pret() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/app_oct.txt", InboxKind.APP_CAPTURE_TEXT, InboxStatus.ERREUR
            )
        response = client.post(f"/entree/{item.id}/reessayer", follow_redirects=False)
    assert response.status_code == 303
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.PRET


def test_retry_non_error_is_refused() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/notes.md", InboxKind.NOTES, InboxStatus.A_RATTACHER
            )
        response = client.post(f"/entree/{item.id}/reessayer", follow_redirects=False)
    assert response.status_code == 409
    with get_session() as session:
        saved = get_item(session, item.id)
    assert saved.status == InboxStatus.A_RATTACHER


def test_retry_unknown_item_is_404() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/entree/999/reessayer", follow_redirects=False)
    assert response.status_code == 404


# --- Page --------------------------------------------------------------------


def test_page_empty_base() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/entree")
    assert response.status_code == 200
    for label in GROUP_LABELS:
        assert label not in response.text


def test_page_groups_in_order() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            meeting = add_meeting(session, date(2026, 11, 23), "Point hebdo")
            add_item(session, "entree/a.vtt", InboxKind.TEAMS_TRANSCRIPT, InboxStatus.A_RATTACHER)
            add_item(session, "entree/b.m4a", InboxKind.AUDIO, InboxStatus.CONSENTEMENT_REQUIS)
            add_item(session, "entree/c.md", InboxKind.NOTES, InboxStatus.PRET)
            add_item(
                session,
                "entree/d.vtt",
                InboxKind.TEAMS_TRANSCRIPT,
                InboxStatus.ERREUR,
                error="Lecture impossible.",
            )
            add_item(
                session,
                "entree/e.vtt",
                InboxKind.TEAMS_TRANSCRIPT,
                InboxStatus.TRAITE,
                meeting_id=meeting.id,
            )
            add_item(session, "entree/f.md", InboxKind.NOTES, InboxStatus.IGNORE)
        response = client.get("/entree")
    assert response.status_code == 200
    positions = [response.text.index(label) for label in GROUP_LABELS]
    assert positions == sorted(positions)
    for name in ("a.vtt", "b.m4a", "c.md", "d.vtt", "e.vtt", "f.md"):
        assert name in response.text


def test_page_attach_form_lists_suggestions_as_radios() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            meeting = add_meeting(session, date(2026, 11, 23), "Point hebdo équipe")
            item = add_item(
                session,
                "entree/2026-11-23_point-hebdo-equipe.vtt",
                InboxKind.TEAMS_TRANSCRIPT,
                InboxStatus.A_RATTACHER,
                name_date=date(2026, 11, 23),
            )
        response = client.get("/entree")
    assert f'action="/entree/{item.id}/rattacher"' in response.text
    assert 'type="radio" name="meeting_id"' in response.text
    assert f'value="{meeting.id}"' in response.text


def test_page_no_attach_form_for_processed_item() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/traite.vtt", InboxKind.TEAMS_TRANSCRIPT, InboxStatus.TRAITE
            )
        response = client.get("/entree")
    assert f'action="/entree/{item.id}/rattacher"' not in response.text
    assert f'action="/entree/{item.id}/ignorer"' not in response.text


def test_page_audio_form_has_consent_checkbox() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            add_item(
                session, "entree/enregistrement.m4a", InboxKind.AUDIO, InboxStatus.CONSENTEMENT_REQUIS
            )
        response = client.get("/entree")
    assert '<input type="checkbox" name="consentement">' in response.text
    assert PHRASE_CONSENTEMENT in response.text


def test_page_transcript_form_has_no_consent_checkbox() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            add_item(
                session, "entree/notes.md", InboxKind.NOTES, InboxStatus.A_RATTACHER
            )
        response = client.get("/entree")
    assert 'name="consentement"' not in response.text


def test_page_error_item_shows_message_and_buttons() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session,
                "entree/casse.vtt",
                InboxKind.TEAMS_TRANSCRIPT,
                InboxStatus.ERREUR,
                error="Le fichier n'est pas au format WebVTT.",
            )
        response = client.get("/entree")
    page = unescape(response.text)
    assert "Le fichier n'est pas au format WebVTT." in page
    assert f'action="/entree/{item.id}/ignorer"' in page
    assert f'action="/entree/{item.id}/reessayer"' in page


def test_page_non_error_item_has_no_retry_button() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            item = add_item(
                session, "entree/notes.md", InboxKind.NOTES, InboxStatus.A_RATTACHER
            )
        response = client.get("/entree")
    assert f'action="/entree/{item.id}/ignorer"' in response.text
    assert f'action="/entree/{item.id}/reessayer"' not in response.text


def test_page_limits_recent_groups_to_fifty() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            for number in range(1, 56):
                add_item(
                    session,
                    f"entree/ecarte-{number:03d}.md",
                    InboxKind.NOTES,
                    InboxStatus.IGNORE,
                    detected_at=datetime(2026, 11, 1, 8, number, tzinfo=ZURICH),
                )
        response = client.get("/entree")
    assert "ecarte-055.md" in response.text
    assert "ecarte-006.md" in response.text
    assert "ecarte-005.md" not in response.text
    assert "ecarte-001.md" not in response.text
