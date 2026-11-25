"""Tests unitaires du modèle de données (V1-02).

Complètent les tests de contrat
(`tests/contrats/test_v1_02_modele_de_donnees.py`) sur les cas qu'ils ne
couvrent pas : cascades au niveau du compte rendu, relations portées par le
modèle, et particularités de SQLite. Chaque test travaille dans un
répertoire de données temporaire (`CABINET_HOME`, fixé par `conftest.py`).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from cabinet.db.base import get_session, init_db
from cabinet.db.models import (
    ActionItem,
    Anomaly,
    CaptureSource,
    Decision,
    Event,
    InboxItem,
    InboxKind,
    Meeting,
    Minutes,
    PrepNote,
    Series,
    Severity,
    TimesheetCapture,
    TimesheetLine,
    Transcript,
    TranscriptSource,
)

MOMENT = datetime(2026, 11, 24, 9, 30, tzinfo=ZoneInfo("Europe/Zurich"))


@pytest.fixture()
def session():
    """Base initialisée et session ouverte, fermée en fin de test."""
    init_db()
    with get_session() as ouverte:
        yield ouverte


def _serie(session, cle="hebdos") -> Series:
    """Une série à laquelle rattacher des réunions."""
    serie = Series(key=cle, label="Hebdo")
    session.add(serie)
    session.flush()
    return serie


def _reunion(session, serie: Series | None = None) -> Meeting:
    """Une réunion sans évènement."""
    if serie is None:
        serie = _serie(session)
    reunion = Meeting(series_id=serie.id, title="Hebdo", date=date(2026, 11, 24))
    session.add(reunion)
    session.flush()
    return reunion


def test_date_de_reunion_est_une_pure_date(session) -> None:
    """`meeting.date` est une `datetime.date` pure, sans heure (specs/02 §2.5)."""
    reunion = _reunion(session)
    session.commit()

    relue = session.get(Meeting, reunion.id)
    assert relue is not None
    assert type(relue.date) is date


def test_supprimer_un_compte_rendu_par_le_modele_enleve_decisions_et_actions(session) -> None:
    """Cascade ORM : supprimer un compte rendu enlève ses décisions et actions (specs/02 §4)."""
    reunion = _reunion(session)
    compte_rendu = Minutes(meeting_id=reunion.id, version=1, summary="Résumé", created_at=MOMENT)
    session.add(compte_rendu)
    session.flush()
    session.add(Decision(meeting_id=reunion.id, minutes_id=compte_rendu.id, text="D", quote="Q"))
    session.add(
        ActionItem(meeting_id=reunion.id, minutes_id=compte_rendu.id, description="A", quote="Q")
    )
    session.commit()

    session.delete(session.get(Minutes, compte_rendu.id))
    session.commit()

    for table in ("minutes", "decision", "action_item"):
        nombre = session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        assert nombre == 0, table


def test_supprimer_un_evenement_par_le_modele_detache_la_reunion(session) -> None:
    """Supprimer un évènement par le modèle détache la réunion (specs/02 §4)."""
    serie = _serie(session)
    evenement = Event(uid="u1", title="Hebdo", start=MOMENT, end=MOMENT)
    session.add(evenement)
    session.flush()
    reunion = Meeting(
        event_id=evenement.id, series_id=serie.id, title="Hebdo", date=date(2026, 11, 24)
    )
    session.add(reunion)
    session.commit()

    session.delete(session.get(Event, evenement.id))
    session.commit()

    identifiant = session.execute(text("SELECT event_id FROM meeting")).scalar_one()
    assert identifiant is None


def test_supprimer_une_serie_sans_reunion_est_autorise(session) -> None:
    """Une série sans réunion peut être supprimée (specs/02 §4)."""
    serie = _serie(session)
    session.commit()

    session.delete(serie)
    session.commit()

    nombre = session.execute(text("SELECT count(*) FROM series")).scalar_one()
    assert nombre == 0


def test_deux_reunions_peuvent_se_passer_d_evenement(session) -> None:
    """`event_id` est unique mais les valeurs nulles ne se heurtent pas (specs/02 §2.5)."""
    serie = _serie(session)
    for titre in ("Hebdo", "Mensuel"):
        session.add(Meeting(series_id=serie.id, title=titre, date=date(2026, 11, 24)))
    session.commit()

    nombre = session.execute(text("SELECT count(*) FROM meeting")).scalar_one()
    assert nombre == 2


def test_les_relations_portent_leurs_enfants(session) -> None:
    """Les relations du modèle portent leurs enfants (specs/02 §4)."""
    serie = _serie(session)
    evenement = Event(uid="u1", title="Hebdo", start=MOMENT, end=MOMENT)
    session.add(evenement)
    session.flush()
    reunion = Meeting(
        event_id=evenement.id, series_id=serie.id, title="Hebdo", date=date(2026, 11, 24)
    )
    session.add(reunion)
    session.flush()

    element = InboxItem(
        path="entree/a.txt", sha256="a" * 64, kind=InboxKind.NOTES, detected_at=MOMENT
    )
    session.add(element)
    session.flush()
    session.add(
        Transcript(
            meeting_id=reunion.id,
            inbox_item_id=element.id,
            source=TranscriptSource.NOTES,
            text="T",
        )
    )
    session.add(PrepNote(meeting_id=reunion.id, content_md="Fiche", created_at=MOMENT))
    compte_rendu = Minutes(meeting_id=reunion.id, version=1, summary="Résumé", created_at=MOMENT)
    session.add(compte_rendu)
    session.flush()
    session.add(Decision(meeting_id=reunion.id, minutes_id=compte_rendu.id, text="D", quote="Q"))
    session.add(
        ActionItem(meeting_id=reunion.id, minutes_id=compte_rendu.id, description="A", quote="Q")
    )

    element_capture = InboxItem(
        path="entree/b.txt", sha256="b" * 64, kind=InboxKind.APP_CAPTURE_TEXT, detected_at=MOMENT
    )
    session.add(element_capture)
    session.flush()
    capture = TimesheetCapture(inbox_item_id=element_capture.id, source=CaptureSource.TEXTE)
    session.add(capture)
    session.flush()
    session.add(
        TimesheetLine(capture_id=capture.id, person="Thomas", project_code="P1", days=0.5)
    )
    session.add(
        Anomaly(
            capture_id=capture.id,
            person="Thomas",
            rule_id="R1",
            detail="D",
            severity=Severity.A_VERIFIER,
        )
    )
    session.commit()

    assert reunion.event is not None and reunion.event.uid == "u1"
    assert [transcript.source for transcript in reunion.transcripts] == [TranscriptSource.NOTES]
    assert reunion.prep_note is not None and reunion.prep_note.content_md == "Fiche"
    assert [version.version for version in reunion.minutes] == [1]

    assert [decision.text for decision in compte_rendu.decisions] == ["D"]
    assert [action.description for action in compte_rendu.action_items] == ["A"]

    assert [ligne.person for ligne in capture.lines] == ["Thomas"]
    assert [anomalie.rule_id for anomalie in capture.anomalies] == ["R1"]
