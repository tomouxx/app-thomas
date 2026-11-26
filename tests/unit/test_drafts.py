"""Tests unitaires du cycle de vie des brouillons, sans envoi (V1-07).

Complètent les tests de contrat (`tests/contrats/test_v1_07_brouillons.py`) sur
les cas qu'ils ne couvrent pas : redirections, identifiants inconnus en 404,
modification refusée hors de l'état `brouillon`, absence d'export au rejet,
ordre des groupes sur la page et absence de capacité d'envoi dans `src/`.
Chaque test travaille dans un répertoire de données temporaire (`CABINET_HOME`,
fixé par `conftest.py`).
"""

from __future__ import annotations

from datetime import datetime
from html import unescape
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cabinet.config import paths
from cabinet.db.base import get_session, init_db
from cabinet.db.models import Draft, DraftStatus
from cabinet.services.drafts import create_draft, mailto_link, set_status
from cabinet.web.app import create_app

ZURICH = ZoneInfo("Europe/Zurich")
MAINTENANT = datetime(2026, 11, 20, 9, 30, tzinfo=ZURICH)
JOUR = "2026-11-20"
DESTINATAIRES = ["alice.martin@exemple.test", "bruno.keller@exemple.test"]
OBJET = "Compte rendu : comité d'été, 20.11.2026"
CORPS = "Bonjour à tous,\n\nVoici les décisions prises.\nÀ bientôt,\nAlice"
GROUP_LABELS = ("Brouillons", "Validés", "Envoyés à la main", "Rejetés")


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mardi 20.11.2026 à 9 h 30, comme les tests de contrat du ticket."""
    monkeypatch.setattr("cabinet.clock.now", lambda: MAINTENANT)


def add_draft(
    session: Session,
    kind: str = "envoi_cr",
    recipients: list[str] | None = None,
    subject: str = OBJET,
    body: str = CORPS,
) -> Draft:
    draft = create_draft(
        session, kind, recipients or list(DESTINATAIRES), subject, body
    )
    session.commit()
    return draft


def get_draft(session: Session, draft_id: int) -> Draft:
    return session.get(Draft, draft_id)


def export_files() -> list[Path]:
    folder = paths().drafts
    return sorted(folder.glob("*.md")) if folder.exists() else []


# --- Transitions -------------------------------------------------------------


def test_validate_draft_redirects_and_sets_valide() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
        response = client.post(f"/brouillons/{draft.id}/valider", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/brouillons"
    with get_session() as session:
        assert get_draft(session, draft.id).status is DraftStatus.VALIDE


def test_reject_draft_redirects_and_sets_rejete() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
        response = client.post(f"/brouillons/{draft.id}/rejeter", follow_redirects=False)
    assert response.status_code == 303
    with get_session() as session:
        assert get_draft(session, draft.id).status is DraftStatus.REJETE


def test_mark_sent_manually_redirects_and_sets_envoye() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
            set_status(session, draft.id, DraftStatus.VALIDE)
            session.commit()
        response = client.post(f"/brouillons/{draft.id}/envoye", follow_redirects=False)
    assert response.status_code == 303
    with get_session() as session:
        assert get_draft(session, draft.id).status is DraftStatus.ENVOYE_A_LA_MAIN


def test_rejected_draft_cannot_be_validated() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
            set_status(session, draft.id, DraftStatus.REJETE)
            session.commit()
        response = client.post(f"/brouillons/{draft.id}/valider", follow_redirects=False)
    assert response.status_code == 409
    with get_session() as session:
        assert get_draft(session, draft.id).status is DraftStatus.REJETE


def test_draft_cannot_be_marked_sent_directly() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
        response = client.post(f"/brouillons/{draft.id}/envoye", follow_redirects=False)
    assert response.status_code == 409
    with get_session() as session:
        assert get_draft(session, draft.id).status is DraftStatus.BROUILLON


def test_unknown_draft_is_404_on_every_action() -> None:
    with TestClient(create_app()) as client:
        for action in ("", "/valider", "/rejeter", "/envoye"):
            response = client.post(
                f"/brouillons/999{action}", data={}, follow_redirects=False
            )
            assert response.status_code == 404


# --- Export ------------------------------------------------------------------


def test_validation_writes_export_file_with_subject_and_body() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
        response = client.post(f"/brouillons/{draft.id}/valider", follow_redirects=False)
    assert response.status_code == 303
    expected = paths().drafts / f"{JOUR}_envoi_cr_{draft.id}.md"
    assert expected.exists()
    content = expected.read_text(encoding="utf-8")
    assert OBJET in content
    assert CORPS in content


def test_export_content_follows_the_spec_format() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
        client.post(f"/brouillons/{draft.id}/valider", follow_redirects=False)
    content = (paths().drafts / f"{JOUR}_envoi_cr_{draft.id}.md").read_text(
        encoding="utf-8"
    )
    lines = content.splitlines()
    assert lines[0] == f"# {OBJET}"
    assert lines[1] == ""
    assert lines[2].startswith("Destinataires : ")
    assert lines[3] == ""
    assert "\n".join(lines[4:]) == CORPS


def test_rejection_writes_no_export() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
        response = client.post(f"/brouillons/{draft.id}/rejeter", follow_redirects=False)
    assert response.status_code == 303
    assert export_files() == []


# --- Modification ------------------------------------------------------------


def test_modify_draft_updates_fields_and_parses_recipients() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
        response = client.post(
            f"/brouillons/{draft.id}",
            data={
                "subject": "Nouvel objet",
                "recipients": "carla@exemple.test, david@exemple.test",
                "body": "Nouveau corps.",
            },
            follow_redirects=False,
        )
    assert response.status_code == 303
    with get_session() as session:
        saved = get_draft(session, draft.id)
    assert saved.subject == "Nouvel objet"
    assert saved.recipients == ["carla@exemple.test", "david@exemple.test"]
    assert saved.body == "Nouveau corps."


def test_modify_validated_draft_is_refused() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
            set_status(session, draft.id, DraftStatus.VALIDE)
            session.commit()
        response = client.post(
            f"/brouillons/{draft.id}", data={"subject": "Autre"}, follow_redirects=False
        )
    assert response.status_code == 409
    with get_session() as session:
        saved = get_draft(session, draft.id)
    assert saved.subject == OBJET


def test_modify_rejected_draft_is_refused() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
            set_status(session, draft.id, DraftStatus.REJETE)
            session.commit()
        response = client.post(
            f"/brouillons/{draft.id}", data={"subject": "Autre"}, follow_redirects=False
        )
    assert response.status_code == 409


# --- Page --------------------------------------------------------------------


def test_page_without_validated_draft_has_no_mailto() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            add_draft(session)
        response = client.get("/brouillons")
    assert response.status_code == 200
    page = unescape(response.text)
    assert "mailto:" not in page
    assert "data-copy" not in page


def test_page_validated_draft_has_mailto_and_copy_button() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            draft = add_draft(session)
            set_status(session, draft.id, DraftStatus.VALIDE)
            session.commit()
        response = client.get("/brouillons")
    with get_session() as session:
        link = mailto_link(get_draft(session, draft.id))
    page = unescape(response.text)
    assert link in page
    assert "data-copy" in page


def test_page_lists_drafts_by_status_in_order() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            add_draft(session)
            valide = add_draft(session, subject="Validé")
            set_status(session, valide.id, DraftStatus.VALIDE)
            envoye = add_draft(session, subject="Envoyé")
            set_status(session, envoye.id, DraftStatus.VALIDE)
            set_status(session, envoye.id, DraftStatus.ENVOYE_A_LA_MAIN)
            rejete = add_draft(session, subject="Rejeté")
            set_status(session, rejete.id, DraftStatus.REJETE)
            session.commit()
        response = client.get("/brouillons")
    assert response.status_code == 200
    positions = [response.text.index(label) for label in GROUP_LABELS]
    assert positions == sorted(positions)


def test_page_shows_kind_recipients_subject_body_and_date() -> None:
    with TestClient(create_app()) as client:
        with get_session() as session:
            add_draft(session)
        response = client.get("/brouillons")
    page = unescape(response.text)
    assert "envoi_cr" in page
    assert ", ".join(DESTINATAIRES) in page
    assert OBJET in page
    assert "Voici les décisions prises." in page
    assert "20.11.2026" in page


# --- Lien mailto -------------------------------------------------------------


def test_mailto_link_decodes_back_to_original() -> None:
    init_db()
    with get_session() as session:
        draft = add_draft(session)
    link = mailto_link(draft)
    assert link.startswith("mailto:")
    head, query = link[len("mailto:"):].split("?", 1)
    assert head == ",".join(DESTINATAIRES)
    params = dict(pair.split("=", 1) for pair in query.split("&"))
    assert unquote(params["subject"]) == OBJET
    assert unquote(params["body"]) == CORPS


def test_mailto_link_encodes_accents_and_newlines() -> None:
    init_db()
    with get_session() as session:
        draft = add_draft(session)
    link = mailto_link(draft)
    assert "été" not in link
    assert "\n" not in link
    assert "%0A" in link


# --- Pas d'envoi -------------------------------------------------------------


def test_no_send_capability_in_source() -> None:
    source = Path(__file__).resolve().parents[2] / "src"
    for file in sorted(source.rglob("*")):
        if not file.is_file() or "__pycache__" in file.parts:
            continue
        content = file.read_bytes().decode("utf-8", errors="ignore")
        for marker in ("smtplib", "sendMail", "send_message"):
            assert marker not in content, f"{marker} trouvé dans {file}"
