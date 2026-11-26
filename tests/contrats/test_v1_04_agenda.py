"""Tests de contrat du ticket V1-04, « Agenda par fichier ICS ».

Écrits par le relecteur avant la vague. Chaque test travaille dans un
répertoire de données temporaire (`CABINET_HOME`, fixé par `conftest.py`).

L'agenda se teste avec des fichiers ICS synthétiques et le vrai
`IcsCalendarSource` (`specs/09 §3.2`). Les jeux sont dans
`tests/contrats/fixtures/v1_04/`. Les tests de l'agent fixent la date du jour
au mardi 24.11.2026 par `cabinet.clock.now`, ce qui donne la fenêtre du
25.10.2026 au 08.12.2026 (`specs/05 §6.2`). L'agent s'exécute dans
`record_run`, comme le ferait le pipeline (`specs/05 §3.1`), et les tables se
relisent en SQL par les noms de `specs/02 §2.3` à `§2.5`.
"""

from __future__ import annotations

import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import JSON, Boolean, Date, text
from sqlalchemy.orm import Session

JEUX = Path(__file__).resolve().parent / "fixtures" / "v1_04"
ZURICH = ZoneInfo("Europe/Zurich")
AUJOURD_HUI = datetime(2026, 11, 24, 10, 0, tzinfo=ZURICH)

# Contenu de agenda.ics vu depuis le 24.11.2026 : treize occurrences dans la
# fenêtre, dont deux annulées et une sur la journée entière, donc dix réunions
# réparties en quatre séries.
UID_AGENDA = {
    "copil@exemple.test",
    "point-hebdo-12@exemple.test",
    "point-hebdo-13@exemple.test",
    "revue-equipe@exemple.test",
    "annule-statut@exemple.test",
    "annule-titre@exemple.test",
    "journee@exemple.test",
    "point-asie@exemple.test",
}
CLES_AGENDA = {"comite de pilotage", "point hebdo", "revue d equipe", "point asie"}


def zurich(annee: int, mois: int, jour: int, heure: int = 0, minute: int = 0) -> datetime:
    return datetime(annee, mois, jour, heure, minute, tzinfo=ZURICH)


# --- Outils -----------------------------------------------------------------


def maison() -> Path:
    return Path(os.environ["CABINET_HOME"]).expanduser().resolve()


def lire_agenda(nom: str, debut: date, fin: date) -> list[Any]:
    from cabinet.adapters.ics_calendar import IcsCalendarSource

    return IcsCalendarSource(JEUX / nom).list_events(debut, fin)


def par_uid(evenements: list[Any]) -> dict[str, Any]:
    return {evenement.uid: evenement for evenement in evenements}


def nom_du_fuseau(moment: datetime) -> str | None:
    return getattr(moment.tzinfo, "key", None)


def fixer_date(monkeypatch: pytest.MonkeyPatch, valeur: datetime = AUJOURD_HUI) -> None:
    monkeypatch.setattr("cabinet.clock.now", lambda: valeur)


def placer_agenda(nom: str) -> Path:
    """Copie un jeu ICS à l'emplacement par défaut, `config/agenda.ics`."""
    cible = maison() / "config" / "agenda.ics"
    cible.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(JEUX / nom, cible)
    return cible


def agent_agenda(chemin: Path) -> Any:
    from cabinet.adapters.ics_calendar import IcsCalendarSource
    from cabinet.agents.agenda import AgendaAgent

    return AgendaAgent(IcsCalendarSource(chemin))


def executer(agent: Any) -> Any:
    """Un passage de l'agent, encadré par le journal comme dans `Pipeline.run`."""
    from cabinet.agents.journal import record_run
    from cabinet.db.base import get_engine, init_db

    init_db()
    with Session(get_engine()) as session, record_run(session, agent.name):
        resultat = agent.run(session)
    return resultat


def lire(requete: str, **types: Any) -> list[tuple]:
    """Relit la base dans une session neuve, colonnes typées au besoin."""
    from cabinet.db.base import get_engine

    instruction = text(requete)
    if types:
        instruction = instruction.columns(**types)
    with Session(get_engine()) as session:
        return [tuple(ligne) for ligne in session.execute(instruction)]


def compter(table: str) -> int:
    return lire(f"SELECT COUNT(*) FROM {table}")[0][0]


def evenements_en_base() -> list[tuple]:
    """(uid, title, start, cancelled, all_day), dans l'ordre des débuts."""
    from cabinet.db.base import TZDateTime

    return lire(
        'SELECT uid, title, start AS "start", cancelled AS "cancelled", all_day AS "all_day"'
        " FROM event ORDER BY start, uid",
        start=TZDateTime(),
        cancelled=Boolean(),
        all_day=Boolean(),
    )


def reunions_en_base() -> list[tuple]:
    """(uid de l'évènement, titre, date, clé de série), dans l'ordre des débuts."""
    return lire(
        'SELECT e.uid, m.title, m."date" AS "date", s."key"'
        " FROM meeting m JOIN event e ON m.event_id = e.id"
        " JOIN series s ON m.series_id = s.id ORDER BY e.start, e.uid",
        date=Date(),
    )


class FauxAgenda:
    """Doublure du port `CalendarSource` : note les fenêtres demandées."""

    def __init__(self, evenements: list[Any]) -> None:
        self.evenements = evenements
        self.appels: list[tuple[date, date]] = []

    def list_events(self, start: date, end: date) -> list[Any]:
        self.appels.append((start, end))
        return list(self.evenements)


# --- Critère 1 -------------------------------------------------------------


def test_c1_evenement_simple_lu_en_entier() -> None:
    (evenement,) = lire_agenda("simple.ics", date(2026, 11, 25), date(2026, 11, 25))
    assert evenement.uid == "copil-simple@exemple.test"
    assert evenement.title == "Comité de pilotage"
    assert evenement.location == "Salle Léman"
    assert evenement.start == zurich(2026, 11, 25, 10, 0)
    assert evenement.end == zurich(2026, 11, 25, 11, 30)
    assert evenement.all_day is False
    assert evenement.cancelled is False


def test_c1_organisateur_et_participants_sans_mailto_en_minuscules() -> None:
    """Adresses sans `mailto:`, en minuscules, dans l'ordre du fichier, sans doublon."""
    (evenement,) = lire_agenda("simple.ics", date(2026, 11, 25), date(2026, 11, 25))
    assert evenement.organizer == "alice.martin@exemple.test"
    assert evenement.attendees == ["bruno.petit@exemple.test", "claire.dubois@exemple.test"]


def test_c1_evenement_simple_enregistre_par_l_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    fixer_date(monkeypatch)
    executer(agent_agenda(placer_agenda("agenda.ics")))
    lignes = lire(
        "SELECT title, location, organizer, attendees FROM event WHERE uid = 'copil@exemple.test'",
        attendees=JSON(),
    )
    assert lignes == [
        (
            "Comité de pilotage",
            "Salle Léman",
            "alice.martin@exemple.test",
            ["bruno.petit@exemple.test", "claire.dubois@exemple.test"],
        )
    ]


# --- Critère 2 -------------------------------------------------------------


def test_c2_une_occurrence_par_semaine_dans_la_fenetre() -> None:
    occurrences = lire_agenda("hebdo.ics", date(2026, 10, 19), date(2026, 11, 15))
    assert [occurrence.start for occurrence in occurrences] == [
        zurich(2026, 10, 19, 9, 0),
        zurich(2026, 10, 26, 9, 0),  # après le passage à l'heure d'hiver du 25.10
        zurich(2026, 11, 2, 9, 0),
        zurich(2026, 11, 9, 9, 0),
    ]
    assert {occurrence.uid for occurrence in occurrences} == {"point-hebdo@exemple.test"}
    assert {occurrence.title for occurrence in occurrences} == {"Point hebdo"}
    assert [occurrence.end - occurrence.start for occurrence in occurrences] == [
        timedelta(minutes=30)
    ] * 4


def test_c2_chaque_occurrence_devient_un_evenement_et_une_reunion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixer_date(monkeypatch)
    executer(agent_agenda(placer_agenda("agenda.ics")))
    revues = [ligne for ligne in reunions_en_base() if ligne[0] == "revue-equipe@exemple.test"]
    # Mercredis du 25.10 au 08.12.2026.
    assert [ligne[2] for ligne in revues] == [
        date(2026, 10, 28),
        date(2026, 11, 4),
        date(2026, 11, 11),
        date(2026, 11, 18),
        date(2026, 11, 25),
        date(2026, 12, 2),
    ]
    assert {ligne[3] for ligne in revues} == {"revue d equipe"}


# --- Critère 3 -------------------------------------------------------------


def test_c3_annulation_par_statut_ou_par_titre() -> None:
    evenements = par_uid(lire_agenda("annulations.ics", date(2026, 11, 26), date(2026, 11, 26)))
    assert {uid: evenement.cancelled for uid, evenement in evenements.items()} == {
        "annule-statut@exemple.test": True,
        "annule-titre-fr@exemple.test": True,
        "annule-titre-majuscules@exemple.test": True,
        "annule-titre-en@exemple.test": True,
        "annule-titre-en-minuscules@exemple.test": True,
        "maintenu@exemple.test": False,
        "annulation-dans-le-titre@exemple.test": False,
    }


def test_c3_evenement_annule_enregistre_sans_reunion(monkeypatch: pytest.MonkeyPatch) -> None:
    fixer_date(monkeypatch)
    executer(agent_agenda(placer_agenda("agenda.ics")))
    annules = {ligne[0] for ligne in evenements_en_base() if ligne[3]}
    assert annules == {"annule-statut@exemple.test", "annule-titre@exemple.test"}
    avec_reunion = {ligne[0] for ligne in reunions_en_base()}
    assert not annules & avec_reunion
    cles = {ligne[0] for ligne in lire('SELECT "key" FROM series')}
    assert "revue budget" not in cles
    assert "point fournisseur" not in cles


# --- Critère 4 -------------------------------------------------------------


def test_c4_cle_de_serie_sans_prefixe_ni_numero() -> None:
    from cabinet.services.series import series_key

    assert series_key("RE: Point hebdo 12") == series_key("Point hebdo")


def test_c4_cle_de_serie_sans_accent_ni_casse() -> None:
    from cabinet.services.series import series_key

    assert series_key("Comité été") == series_key("comite ete")


@pytest.mark.parametrize(
    ("titre", "cle"),
    [
        ("RE: Point hebdo 12", "point hebdo"),
        ("Point hebdo", "point hebdo"),
        ("Comité été", "comite ete"),
        ("TR: RE: Comité de pilotage (3/5)", "comite de pilotage"),
        ("2026", "sans titre"),
        ("FW: Annulé: Revue d'équipe", "revue d equipe"),
        ("Canceled:Point client", "point client"),
        ("  Point   hebdo  ", "point hebdo"),
    ],
)
def test_contrat_cle_de_serie_selon_la_spec(titre: str, cle: str) -> None:
    """Tableau de `specs/05 §6.3`, et les autres préfixes de l'étape 3."""
    from cabinet.services.series import series_key

    assert series_key(titre) == cle


# --- Critère 5 -------------------------------------------------------------


def test_c5_deux_passages_sans_doublon(monkeypatch: pytest.MonkeyPatch) -> None:
    fixer_date(monkeypatch)
    chemin = placer_agenda("agenda.ics")
    premier = executer(agent_agenda(chemin))
    avant = (evenements_en_base(), reunions_en_base(), lire('SELECT "key", label FROM series'))
    second = executer(agent_agenda(chemin))
    apres = (evenements_en_base(), reunions_en_base(), lire('SELECT "key", label FROM series'))
    assert apres == avant
    assert (compter("event"), compter("series"), compter("meeting")) == (13, 4, 10)
    assert premier.processed == 13
    assert second.processed == 0


# --- Critère 6 -------------------------------------------------------------


def test_c6_fichier_absent_leve_une_erreur_claire(tmp_path: Path) -> None:
    from cabinet.adapters.ics_calendar import IcsCalendarSource
    from cabinet.ports.calendar import CalendarError

    absent = tmp_path / "config" / "agenda.ics"
    with pytest.raises(CalendarError) as erreur:
        IcsCalendarSource(absent).list_events(date(2026, 11, 1), date(2026, 11, 30))
    message = str(erreur.value)
    assert message.startswith("Fichier d'agenda introuvable : ")
    assert str(absent) in message
    assert "Exporte ton agenda au format ICS et place-le à cet endroit." in message


def test_c6_le_journal_enregistre_l_erreur(monkeypatch: pytest.MonkeyPatch) -> None:
    from cabinet.ports.calendar import CalendarError

    fixer_date(monkeypatch)
    absent = maison() / "config" / "agenda.ics"
    with pytest.raises(CalendarError):
        executer(agent_agenda(absent))
    ((agent, etat, erreur),) = lire("SELECT agent, status, error FROM agent_run")
    assert (agent, etat) == ("agenda", "erreur")
    assert "Fichier d'agenda introuvable" in erreur
    assert str(absent) in erreur
    assert (compter("event"), compter("series"), compter("meeting")) == (0, 0, 0)


# --- Critère 7 -------------------------------------------------------------


def test_c7_heures_relues_dans_le_fuseau_de_zurich(monkeypatch: pytest.MonkeyPatch) -> None:
    from cabinet.db.base import TZDateTime

    fixer_date(monkeypatch)
    executer(agent_agenda(placer_agenda("agenda.ics")))
    lignes = lire(
        'SELECT uid, start AS "start", "end" AS "end" FROM event ORDER BY start, uid',
        start=TZDateTime(),
        end=TZDateTime(),
    )
    assert len(lignes) == 13
    for _, debut, fin in lignes:
        assert nom_du_fuseau(debut) == "Europe/Zurich"
        assert nom_du_fuseau(fin) == "Europe/Zurich"
    heures = {uid: (debut, fin) for uid, debut, fin in lignes}
    assert heures["copil@exemple.test"] == (
        zurich(2026, 11, 25, 10, 0),
        zurich(2026, 11, 25, 11, 30),
    )
    # 23:30 UTC le 24.11 est 00:30 à Zurich le 25.11.
    assert heures["point-asie@exemple.test"][0] == zurich(2026, 11, 25, 0, 30)


def test_c7_evenements_lus_dans_le_fuseau_de_zurich() -> None:
    evenements = lire_agenda("fuseaux.ics", date(2026, 11, 24), date(2026, 11, 27))
    assert evenements
    for evenement in evenements:
        assert nom_du_fuseau(evenement.start) == "Europe/Zurich"
        assert nom_du_fuseau(evenement.end) == "Europe/Zurich"


# --- Contrat, hors critères numérotés ---------------------------------------


def test_contrat_conversion_des_fuseaux() -> None:
    """UTC, autre fuseau et heure sans fuseau, prise dans LOCAL_TZ (specs/05 §6.1)."""
    evenements = par_uid(lire_agenda("fuseaux.ics", date(2026, 11, 24), date(2026, 11, 24)))
    assert evenements["utc@exemple.test"].start == zurich(2026, 11, 24, 9, 0)
    assert evenements["new-york@exemple.test"].start == zurich(2026, 11, 24, 15, 0)
    assert evenements["new-york@exemple.test"].end == zurich(2026, 11, 24, 16, 0)
    assert evenements["flottant@exemple.test"].start == zurich(2026, 11, 24, 12, 0)


def test_contrat_champs_absents() -> None:
    """Sans titre, sans fin, lieu vide, ni organisateur ni participant (specs/05 §6.1)."""
    evenements = par_uid(lire_agenda("fuseaux.ics", date(2026, 11, 24), date(2026, 11, 24)))
    evenement = evenements["sans-fin-sans-titre@exemple.test"]
    assert evenement.title == "(sans titre)"
    assert evenement.start == zurich(2026, 11, 24, 17, 0)
    assert evenement.end == evenement.start
    assert evenement.location is None
    assert evenement.organizer is None
    assert evenement.attendees == []


def test_contrat_journee_entiere() -> None:
    evenements = par_uid(lire_agenda("fuseaux.ics", date(2026, 11, 27), date(2026, 11, 27)))
    journee = evenements["journee@exemple.test"]
    assert journee.all_day is True
    assert journee.start == zurich(2026, 11, 27)
    assert journee.end == zurich(2026, 11, 28)
    assert nom_du_fuseau(journee.start) == "Europe/Zurich"


def test_contrat_exceptions_de_recurrence() -> None:
    """Occurrence exclue (EXDATE), déplacée et annulée (RECURRENCE-ID)."""
    occurrences = lire_agenda("exceptions.ics", date(2026, 10, 26), date(2026, 11, 22))
    assert [(o.start, o.title, o.cancelled) for o in occurrences] == [
        (zurich(2026, 10, 26, 9, 0), "Point hebdo", False),
        (zurich(2026, 11, 10, 14, 0), "Point hebdo (déplacé)", False),
        (zurich(2026, 11, 16, 9, 0), "Point hebdo", True),
    ]


def test_contrat_bornes_de_la_fenetre_et_ordre() -> None:
    """Le jour `end` est rendu, le lendemain non. Tri par début puis par `uid`."""
    evenements = lire_agenda("bornes.ics", date(2026, 11, 24), date(2026, 11, 25))
    assert [evenement.uid for evenement in evenements] == [
        "debut@exemple.test",
        "a-meme-heure@exemple.test",
        "b-meme-heure@exemple.test",
        "fin@exemple.test",
    ]


def test_contrat_fichier_illisible(tmp_path: Path) -> None:
    from cabinet.adapters.ics_calendar import IcsCalendarSource
    from cabinet.ports.calendar import CalendarError

    illisible = tmp_path / "agenda.ics"
    illisible.write_text("pas un agenda\n", encoding="utf-8")
    with pytest.raises(CalendarError) as erreur:
        IcsCalendarSource(illisible).list_events(date(2026, 11, 1), date(2026, 11, 30))
    assert str(erreur.value).startswith("Fichier d'agenda illisible : ")


def test_contrat_evenement_data_est_le_modele_du_port() -> None:
    from cabinet.ports.calendar import EventData

    (evenement,) = lire_agenda("simple.ics", date(2026, 11, 25), date(2026, 11, 25))
    assert isinstance(evenement, EventData)
    assert set(EventData.model_fields) == {
        "uid",
        "title",
        "start",
        "end",
        "all_day",
        "location",
        "organizer",
        "attendees",
        "cancelled",
    }


def test_contrat_fenetre_de_trente_jours_avant_a_quatorze_apres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cabinet.agents.agenda import AgendaAgent
    from cabinet.ports.calendar import EventData

    fixer_date(monkeypatch)
    faux = FauxAgenda(
        [
            EventData(
                uid="faux@exemple.test",
                title="Point fictif",
                start=zurich(2026, 11, 26, 9, 0),
                end=zurich(2026, 11, 26, 10, 0),
                all_day=False,
                location=None,
                organizer=None,
                attendees=[],
                cancelled=False,
            )
        ]
    )
    agent = AgendaAgent(faux)
    assert agent.name == "agenda"
    resultat = executer(agent)
    assert faux.appels == [(date(2026, 10, 25), date(2026, 12, 8))]
    assert resultat.processed == 1
    assert resultat.model is None
    assert reunions_en_base() == [
        ("faux@exemple.test", "Point fictif", date(2026, 11, 26), "point fictif")
    ]


def test_contrat_evenements_hors_fenetre_ecartes(monkeypatch: pytest.MonkeyPatch) -> None:
    fixer_date(monkeypatch)
    executer(agent_agenda(placer_agenda("agenda.ics")))
    assert {ligne[0] for ligne in evenements_en_base()} == UID_AGENDA


def test_contrat_series_et_reunions_creees(monkeypatch: pytest.MonkeyPatch) -> None:
    """Série nouvelle : `followed` à faux, libellé du premier évènement (specs/05 §6.2)."""
    fixer_date(monkeypatch)
    executer(agent_agenda(placer_agenda("agenda.ics")))
    series = lire(
        'SELECT "key", label, followed AS "followed" FROM series ORDER BY "key"',
        followed=Boolean(),
    )
    assert {ligne[0] for ligne in series} == CLES_AGENDA
    par_cle = {cle: (libelle, suivie) for cle, libelle, suivie in series}
    assert par_cle["point hebdo"] == ("Point hebdo 12", False)
    assert not any(ligne[2] for ligne in series)

    reunions = {ligne[0]: ligne[1:] for ligne in reunions_en_base()}
    assert reunions["point-hebdo-12@exemple.test"] == (
        "Point hebdo 12",
        date(2026, 11, 17),
        "point hebdo",
    )
    assert reunions["point-hebdo-13@exemple.test"] == (
        "RE: Point hebdo 13",
        date(2026, 11, 24),
        "point hebdo",
    )
    assert reunions["copil@exemple.test"] == (
        "Comité de pilotage",
        date(2026, 11, 25),
        "comite de pilotage",
    )
    # Le jour de la réunion est celui de Zurich, pas celui d'UTC.
    assert reunions["point-asie@exemple.test"] == ("Point Asie", date(2026, 11, 25), "point asie")


def test_contrat_journee_entiere_sans_reunion(monkeypatch: pytest.MonkeyPatch) -> None:
    fixer_date(monkeypatch)
    executer(agent_agenda(placer_agenda("agenda.ics")))
    journees = [ligne for ligne in evenements_en_base() if ligne[4]]
    assert [ligne[0] for ligne in journees] == ["journee@exemple.test"]
    assert "journee@exemple.test" not in {ligne[0] for ligne in reunions_en_base()}


def test_contrat_l_agent_ne_supprime_rien_et_met_a_jour(monkeypatch: pytest.MonkeyPatch) -> None:
    """Évènement retiré, puis annulé, puis renommé : rien ne disparaît (specs/05 §6.2)."""
    fixer_date(monkeypatch)
    chemin = placer_agenda("agenda.ics")
    executer(agent_agenda(chemin))
    shutil.copyfile(JEUX / "agenda_apres.ics", chemin)
    resultat = executer(agent_agenda(chemin))

    assert resultat.processed == 1  # seul point-hebdo-14 est nouveau
    assert (compter("event"), compter("series"), compter("meeting")) == (14, 4, 11)

    evenements = {ligne[0]: ligne for ligne in evenements_en_base()}
    assert "point-hebdo-13@exemple.test" in evenements  # retiré du fichier, gardé en base
    assert evenements["copil@exemple.test"][3] is True  # devenu annulé
    assert evenements["point-hebdo-12@exemple.test"][1] == "TR: Point hebdo 12"
    ((lieu,),) = lire("SELECT location FROM event WHERE uid = 'point-hebdo-12@exemple.test'")
    assert lieu == "Salle B"

    reunions = {ligne[0]: ligne[1:] for ligne in reunions_en_base()}
    assert "point-hebdo-13@exemple.test" in reunions
    assert "copil@exemple.test" in reunions  # la réunion reste, son évènement est annulé
    assert reunions["point-hebdo-12@exemple.test"][0] == "TR: Point hebdo 12"
    assert reunions["point-hebdo-14@exemple.test"][2] == "point hebdo"

    libelles = dict(lire('SELECT "key", label FROM series'))
    assert libelles["point hebdo"] == "Point hebdo 12"
