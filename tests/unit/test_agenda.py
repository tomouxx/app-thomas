"""Tests unitaires de l'agenda par fichier ICS (V1-04).

Complètent les tests de contrat (`tests/contrats/test_v1_04_agenda.py`)
sur les cas qu'ils ne couvrent pas : clés de série avec préfixes empilés,
préfixe accentué ou titre vide ; évènement sans `DTEND` et journée entière
sans fin ; occurrence déplacée qui donne une nouvelle ligne (specs/05 §6).
Chaque test travaille dans un répertoire de données temporaire (`CABINET_HOME`,
fixé par `conftest.py`).
"""

from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from cabinet.adapters.ics_calendar import IcsCalendarSource
from cabinet.agents.agenda import AgendaAgent
from cabinet.db.base import get_session, init_db
from cabinet.db.models import Event, Meeting
from cabinet.ports.calendar import CalendarError
from cabinet.services.series import series_key

JEUX = Path(__file__).resolve().parent.parent / "fixtures" / "agenda"
ZURICH = ZoneInfo("Europe/Zurich")


def zurich(annee: int, mois: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(annee, mois, day, hour, minute, tzinfo=ZURICH)


def read_events(name: str, start: date, end: date):
    """Les occurrences d'un jeu ICS du dossier de fixtures."""
    return IcsCalendarSource(JEUX / name).list_events(start, end)


def fixer_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le jour courant au mardi 24.11.2026, comme les tests de contrat."""
    monkeypatch.setattr("cabinet.clock.now", lambda: datetime(2026, 11, 24, 10, 0, tzinfo=ZURICH))


def executer(agent: AgendaAgent):
    """Un passage de l'agent, sur une session neuve."""
    init_db()
    with get_session() as session:
        return agent.run(session)


def compter(table: type) -> int:
    """Le nombre de lignes d'une table, relues dans une session neuve."""
    init_db()
    with get_session() as session:
        return len(list(session.scalars(select(table))))


# --- Clé de série, cas hors du tableau de contrat (specs/05 §6.3) ------------


def test_cle_de_serie_prefixes_empiles() -> None:
    assert series_key("RE: TR: FW: Point hebdo") == "point hebdo"


def test_cle_de_serie_prefixe_annule_accentue() -> None:
    """L'accent part avant le retrait du préfixe : `Annulé:` devient `annule:`."""
    assert series_key("Annulé: Point client") == "point client"


def test_cle_de_serie_titre_vide() -> None:
    assert series_key("") == "sans titre"


def test_cle_de_serie_chiffres_au_milieu() -> None:
    assert series_key("Point 2026 hebdo") == "point hebdo"


# --- Lecture du fichier ICS, cas hors contrat (specs/05 §6.1) ----------------


def test_evenement_sans_dtend() -> None:
    """Sans `DTEND`, la fin vaut le début (specs/05 §6.1)."""
    (event,) = read_events("sans_fin.ics", date(2026, 11, 25), date(2026, 11, 25))
    assert event.start == zurich(2026, 11, 25, 17, 0)
    assert event.end == event.start
    assert event.all_day is False


def test_journee_entiere_sans_dtend() -> None:
    """Journée entière sans `DTEND` : la fin est minuit du lendemain."""
    (event,) = read_events("journee_sans_fin.ics", date(2026, 11, 25), date(2026, 11, 25))
    assert event.all_day is True
    assert event.start == zurich(2026, 11, 25)
    assert event.end == zurich(2026, 11, 26)


def test_fichier_binaire_illisible(tmp_path: Path) -> None:
    """Des octets non textuels lèvent la même erreur qu'un texte invalide."""
    illisible = tmp_path / "agenda.ics"
    illisible.write_bytes(b"\x00\x01\x02pas un agenda")
    with pytest.raises(CalendarError) as erreur:
        IcsCalendarSource(illisible).list_events(date(2026, 11, 1), date(2026, 11, 30))
    assert str(erreur.value).startswith("Fichier d'agenda illisible : ")


# --- Agent, cas hors contrat (specs/05 §6.2) ----------------------------------


def test_occurrence_deplacee_donne_une_nouvelle_ligne(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Même `uid`, heure changée : une ligne `event` et une réunion de plus."""
    fixer_date(monkeypatch)
    path = tmp_path / "agenda.ics"
    shutil.copyfile(JEUX / "deplace_avant.ics", path)
    premier = executer(AgendaAgent(IcsCalendarSource(path)))
    shutil.copyfile(JEUX / "deplace_apres.ics", path)
    second = executer(AgendaAgent(IcsCalendarSource(path)))

    assert (premier.processed, second.processed) == (1, 1)
    assert compter(Event) == 2
    assert compter(Meeting) == 2
    init_db()
    with get_session() as session:
        debuts = list(
            session.scalars(
                select(Event.start)
                .where(Event.uid == "point-deplace@exemple.test")
                .order_by(Event.start)
            )
        )
    assert debuts == [zurich(2026, 11, 25, 9, 0), zurich(2026, 11, 25, 14, 0)]
