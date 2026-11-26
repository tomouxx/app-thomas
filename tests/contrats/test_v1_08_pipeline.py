"""Tests de contrat du ticket V1-08, « Orchestrateur du pipeline ».

Écrits par le relecteur avant la vague. Chaque test travaille dans un
répertoire de données temporaire (`CABINET_HOME`, fixé par `conftest.py`).

Sources : `specs/05 §3` (orchestrateur, verrou, `build_pipeline`), `specs/05
§2.3` (modèle et tokens au journal), `specs/01 §3.3` (protocole d'agent),
`specs/03 §5.3` (`cabinet run`), `specs/08 §2.3` (`POST /traiter`) et
`specs/09 §4` et `§5` (scénarios S1 à S3, contrôle « Journal complet »).

Les tests de `Pipeline` emploient des agents factices qui respectent le
protocole de `specs/01 §3.3` : un attribut `name` et une méthode
`run(session) -> AgentOutcome`. Aucun modèle n'est appelé. Les tests de
`build_pipeline`, de `cabinet run` et de `POST /traiter` emploient les vrais
agents d'ingestion et d'agenda sur le jeu synthétique de
`tests/contrats/fixtures/v1_08/`. Le journal est relu en SQL dans la table
`agent_run`, par les noms de `specs/02 §2.1`.
"""

from __future__ import annotations

import contextlib
import html
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

RACINE = Path(__file__).resolve().parents[2]
JEU = Path(__file__).resolve().parent / "fixtures" / "v1_08"
MAINTENANT = datetime(2026, 11, 24, 10, 0, tzinfo=ZoneInfo("Europe/Zurich"))

FICHIERS_ENTREE = {
    "2026-11-25_point-hebdo.vtt": "a_rattacher",
    "2026-11-25_notes-point-hebdo.md": "a_rattacher",
    "2026-11-26_comite-de-pilotage.m4a": "consentement_requis",
}


@pytest.fixture(autouse=True)
def date_fixee(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """La fenêtre de l'agenda et le délai de copie suivent `cabinet.clock` (specs/09 §3.3)."""
    monkeypatch.setattr("cabinet.clock.now", lambda: MAINTENANT)
    return MAINTENANT


@pytest.fixture
def verrou(environnement_isole: Path) -> Path:
    """Base initialisée et chemin du verrou, `data/pipeline.lock` (specs/03 §1)."""
    from cabinet.db.base import init_db

    init_db()
    return environnement_isole / "data" / "pipeline.lock"


# --- Doublures ---------------------------------------------------------------


class AgentFactice:
    """Agent de test conforme au protocole `Agent` de `specs/01 §3.3`.

    Il note son passage dans `passages`, appelle `pendant(session)` s'il est
    fourni, puis lève `erreur` ou renvoie un `AgentOutcome` construit avec
    `resultat`.
    """

    def __init__(
        self,
        name: str,
        passages: list[str],
        *,
        erreur: BaseException | None = None,
        resultat: dict[str, Any] | None = None,
        pendant: Callable[[Session], None] | None = None,
    ) -> None:
        self.name = name
        self.passages = passages
        self.erreur = erreur
        self.resultat = {"processed": 1} if resultat is None else resultat
        self.pendant = pendant
        self.sessions: list[Session] = []

    def run(self, session: Session) -> Any:
        from cabinet.agents.base import AgentOutcome

        self.passages.append(self.name)
        self.sessions.append(session)
        if self.pendant is not None:
            self.pendant(session)
        if self.erreur is not None:
            raise self.erreur
        return AgentOutcome(**self.resultat)


class SessionTracee(Session):
    """Session qui note sa fermeture, pour vérifier `specs/05 §3.1`, étape 2.5."""

    fermee = False

    def close(self) -> None:
        self.fermee = True
        super().close()


def fabrique_tracee(ouvertes: list[SessionTracee]) -> Callable[[], Session]:
    from cabinet.db.base import get_engine

    def fabrique() -> Session:
        session = SessionTracee(get_engine(), expire_on_commit=False)
        ouvertes.append(session)
        return session

    return fabrique


def pipeline(agents: list[Any], chemin_verrou: Path) -> Any:
    from cabinet.agents.pipeline import Pipeline

    return Pipeline(agents, chemin_verrou)


def lancer(agents: list[Any], chemin_verrou: Path) -> dict[str, Any]:
    from cabinet.db.base import get_session

    return pipeline(agents, chemin_verrou).run(get_session)


def journal(colonnes: str = "agent, status") -> list[tuple]:
    """Relit `agent_run` dans une session neuve, dans l'ordre d'écriture."""
    from cabinet.db.base import get_engine

    with Session(get_engine()) as session:
        return [
            tuple(ligne)
            for ligne in session.execute(text(f"SELECT {colonnes} FROM agent_run ORDER BY id"))
        ]


def journal_socle() -> list[tuple]:
    """Lignes des deux agents du lot 1. Les lots suivants ajoutent des agents après eux
    (specs/05 §3.3), et ces contrats doivent rester justes quand ils arrivent."""
    return [ligne for ligne in journal() if ligne[0] in ("ingestion", "agenda")]


def compter(table: str) -> int:
    from cabinet.db.base import get_engine

    with Session(get_engine()) as session:
        return session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def pid_mort() -> int:
    """Numéro d'un processus terminé et récolté."""
    processus = subprocess.Popen([sys.executable, "-c", "pass"])
    processus.wait()
    with pytest.raises(ProcessLookupError):
        os.kill(processus.pid, 0)
    return processus.pid


# --- Répertoire de données réel pour build_pipeline, cabinet run et /traiter --


def deposer_agenda(maison: Path) -> None:
    (maison / "config").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(JEU / "agenda.ics", maison / "config" / "agenda.ics")


def deposer_fichiers(maison: Path) -> None:
    """Copie les trois fichiers et recule leur date de modification (specs/09 §3.2)."""
    entree = maison / "entree"
    entree.mkdir(parents=True, exist_ok=True)
    ancien = (MAINTENANT - timedelta(hours=1)).timestamp()
    for nom in FICHIERS_ENTREE:
        cible = entree / nom
        shutil.copyfile(JEU / "entree" / nom, cible)
        os.utime(cible, (ancien, ancien))


def cabinet(*arguments: str) -> Any:
    from cabinet.cli import app
    from typer.testing import CliRunner

    return CliRunner().invoke(app, list(arguments))


def tenir_le_verrou(maison: Path) -> Path:
    """Pose un verrou au numéro du processus de test, qui est vivant."""
    chemin = maison / "data" / "pipeline.lock"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(str(os.getpid()), encoding="utf-8")
    return chemin


@contextmanager
def navigateur() -> Iterator[TestClient]:
    from cabinet.web.app import create_app

    with TestClient(create_app()) as client:
        yield client


def traiter() -> tuple[str, str]:
    """`POST /traiter`, puis la page d'arrivée. Renvoie le message et la page."""
    with navigateur() as client:
        reponse = client.post("/traiter", follow_redirects=False)
        assert reponse.status_code == 303
        adresse = urlsplit(reponse.headers["location"])
        assert adresse.path == "/"
        message = parse_qs(adresse.query).get("message", [""])[0]
        page = client.get(reponse.headers["location"])
    assert page.status_code == 200
    return message, html.unescape(page.text)


# --- Critère 1 -------------------------------------------------------------


def test_c1_agents_executes_dans_l_ordre_de_la_liste(verrou: Path) -> None:
    passages: list[str] = []
    noms = ["minutes", "ingestion", "agenda"]
    resultat = lancer([AgentFactice(nom, passages) for nom in noms], verrou)

    assert passages == noms
    assert list(resultat) == noms
    assert journal("agent") == [(nom,) for nom in noms]


def test_c1_resultat_porte_l_agent_outcome_de_chaque_agent(verrou: Path) -> None:
    from cabinet.agents.base import AgentOutcome

    passages: list[str] = []
    resultat = lancer(
        [
            AgentFactice("ingestion", passages, resultat={"processed": 3}),
            AgentFactice("agenda", passages, resultat={"processed": 5, "errors": 1}),
        ],
        verrou,
    )
    assert all(isinstance(valeur, AgentOutcome) for valeur in resultat.values())
    assert (resultat["ingestion"].processed, resultat["ingestion"].errors) == (3, 0)
    assert (resultat["agenda"].processed, resultat["agenda"].errors) == (5, 1)


# --- Critère 2 -------------------------------------------------------------


def test_c2_agent_en_exception_journalise_erreur_et_suivant_execute(verrou: Path) -> None:
    passages: list[str] = []
    resultat = lancer(
        [
            AgentFactice("ingestion", passages),
            AgentFactice("agenda", passages, erreur=RuntimeError("agenda illisible")),
            AgentFactice("transcripts", passages),
        ],
        verrou,
    )

    assert passages == ["ingestion", "agenda", "transcripts"]
    assert journal("agent, status") == [
        ("ingestion", "ok"),
        ("agenda", "erreur"),
        ("transcripts", "ok"),
    ]
    erreur = journal("error")[1][0]
    assert "agenda illisible" in erreur
    assert isinstance(resultat["agenda"], str)


def test_c2_resultat_porte_le_message_de_l_echec(verrou: Path) -> None:
    from cabinet.agents.base import AgentOutcome

    passages: list[str] = []
    resultat = lancer(
        [
            AgentFactice("ingestion", passages, erreur=ValueError("dossier inaccessible")),
            AgentFactice("agenda", passages),
        ],
        verrou,
    )
    assert list(resultat) == ["ingestion", "agenda"]
    assert isinstance(resultat["ingestion"], str)
    assert "dossier inaccessible" in resultat["ingestion"]
    assert isinstance(resultat["agenda"], AgentOutcome)


def test_c2_travail_annule_en_echec_et_valide_en_succes(verrou: Path) -> None:
    """Chaque agent tourne dans `record_run`, sur sa session (specs/05 §2.2 et §3.1)."""

    def ecrire(cle: str) -> Callable[[Session], None]:
        def pendant(session: Session) -> None:
            session.execute(
                text("INSERT INTO series (key, label, followed) VALUES (:k, :k, 0)"), {"k": cle}
            )

        return pendant

    passages: list[str] = []
    lancer(
        [
            AgentFactice("ingestion", passages, pendant=ecrire("serie gardee")),
            AgentFactice(
                "agenda", passages, pendant=ecrire("serie annulee"), erreur=RuntimeError("panne")
            ),
        ],
        verrou,
    )
    from cabinet.db.base import get_engine

    with Session(get_engine()) as session:
        cles = session.execute(text("SELECT key FROM series ORDER BY key")).scalars().all()
    assert cles == ["serie gardee"]


# --- Critère 3 -------------------------------------------------------------


def test_c3_verrou_tenu_leve_pipeline_busy(verrou: Path) -> None:
    from cabinet.agents.pipeline import PipelineBusy

    verrou.write_text(str(os.getpid()), encoding="utf-8")
    passages: list[str] = []
    with pytest.raises(PipelineBusy):
        lancer([AgentFactice("ingestion", passages)], verrou)

    assert passages == []
    assert journal() == []
    # Le passage refusé ne retire pas le verrou d'un autre.
    assert verrou.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_c3_passage_lance_pendant_un_autre(verrou: Path) -> None:
    """Scénario S3 de `specs/09 §4`."""
    from cabinet.agents.pipeline import PipelineBusy
    from cabinet.db.base import get_session

    passages_second: list[str] = []
    second = pipeline([AgentFactice("agenda", passages_second)], verrou)
    observees: list[BaseException] = []

    def lancer_le_second(session: Session) -> None:
        try:
            second.run(get_session)
        except Exception as erreur:  # la classe est vérifiée après le passage
            observees.append(erreur)

    passages: list[str] = []
    resultat = lancer([AgentFactice("ingestion", passages, pendant=lancer_le_second)], verrou)

    assert len(observees) == 1
    assert isinstance(observees[0], PipelineBusy)
    assert passages_second == []
    assert list(resultat) == ["ingestion"]
    assert not verrou.exists()


@pytest.mark.parametrize("contenu", ["", "pas-un-numero"])
def test_c3_verrou_vide_ou_illisible_est_tenu(verrou: Path, contenu: str) -> None:
    """Seul un numéro de processus mort rend un verrou périmé (specs/05 §3.2 et §7)."""
    from cabinet.agents.pipeline import PipelineBusy

    verrou.write_text(contenu, encoding="utf-8")
    passages: list[str] = []
    with pytest.raises(PipelineBusy):
        lancer([AgentFactice("ingestion", passages)], verrou)
    assert passages == []
    assert verrou.exists()


# --- Critère 4 -------------------------------------------------------------


def test_c4_verrou_retire_apres_un_passage_normal(verrou: Path) -> None:
    lancer([AgentFactice("ingestion", [])], verrou)
    assert not verrou.exists()
    # Un second passage n'est pas bloqué.
    lancer([AgentFactice("ingestion", [])], verrou)
    assert not verrou.exists()


def test_c4_verrou_retire_apres_un_agent_en_erreur(verrou: Path) -> None:
    lancer([AgentFactice("ingestion", [], erreur=RuntimeError("panne"))], verrou)
    assert not verrou.exists()


def test_c4_verrou_retire_apres_une_exception_inattendue(verrou: Path) -> None:
    """Le verrou se retire dans un bloc `finally` (specs/05 §3.1, étape 3)."""

    def fabrique_en_panne() -> Session:
        raise RuntimeError("base indisponible")

    with contextlib.suppress(RuntimeError):
        pipeline([AgentFactice("ingestion", [])], verrou).run(fabrique_en_panne)
    assert not verrou.exists()


def test_c4_verrou_present_pendant_le_passage_avec_le_numero_du_processus(verrou: Path) -> None:
    lus: list[str] = []

    def lire_le_verrou(session: Session) -> None:
        lus.append(verrou.read_text(encoding="utf-8").strip())

    lancer([AgentFactice("ingestion", [], pendant=lire_le_verrou)], verrou)
    assert lus == [str(os.getpid())]
    assert not verrou.exists()


# --- Critère 5 -------------------------------------------------------------


def test_c5_cabinet_run_sort_avec_0_sans_erreur(environnement_isole: Path) -> None:
    deposer_agenda(environnement_isole)
    deposer_fichiers(environnement_isole)

    resultat = cabinet("run")

    assert resultat.exit_code == 0, resultat.output
    lignes = resultat.output.splitlines()
    assert "ingestion : 3 traité(s), 0 erreur(s)" in lignes
    agenda = [
        ligne for ligne in lignes if re.fullmatch(r"agenda : \d+ traité\(s\), 0 erreur\(s\)", ligne)
    ]
    assert len(agenda) == 1
    assert lignes.index("ingestion : 3 traité(s), 0 erreur(s)") < lignes.index(agenda[0])


def test_c5_cabinet_run_sort_avec_1_si_un_agent_echoue(environnement_isole: Path) -> None:
    """Sans `config/agenda.ics`, l'agent d'agenda lève `CalendarError` (specs/05 §6.1)."""
    resultat = cabinet("run")

    assert resultat.exit_code == 1, resultat.output
    lignes = resultat.output.splitlines()
    ingestion = [ligne for ligne in lignes if ligne.startswith("ingestion : ")]
    echec = [ligne for ligne in lignes if ligne.startswith("agenda : échec : ")]
    assert len(ingestion) == 1
    assert len(echec) == 1
    assert "Fichier d'agenda introuvable" in echec[0]
    assert lignes.index(ingestion[0]) < lignes.index(echec[0])
    assert journal_socle() == [("ingestion", "ok"), ("agenda", "erreur")]


def test_c5_cabinet_run_sort_avec_2_si_occupe(environnement_isole: Path) -> None:
    deposer_agenda(environnement_isole)
    chemin = tenir_le_verrou(environnement_isole)

    resultat = cabinet("run")

    assert resultat.exit_code == 2, resultat.output
    assert "Un traitement est déjà en cours." in resultat.output
    assert chemin.read_text(encoding="utf-8").strip() == str(os.getpid())

    from cabinet.db.base import init_db

    init_db()  # la table existe même si la commande s'est arrêtée avant
    assert journal() == []


# --- Critère 6 -------------------------------------------------------------


def test_c6_post_traiter_redirige_vers_l_accueil_avec_le_resume(environnement_isole: Path) -> None:
    deposer_agenda(environnement_isole)
    deposer_fichiers(environnement_isole)

    message, page = traiter()

    assert message.startswith("Traitement terminé.")
    assert "ingestion : 3" in message
    assert "agenda : " in message
    assert "Traitement terminé." in page
    assert "ingestion : 3" in page
    assert journal_socle() == [("ingestion", "ok"), ("agenda", "ok")]


def test_c6_resume_signale_un_agent_en_echec(environnement_isole: Path) -> None:
    message, page = traiter()

    assert message.startswith("Traitement terminé.")
    assert "agenda" in message
    assert "en échec" in message
    assert "en échec" in page


def test_c6_post_traiter_occupe_redirige_avec_le_message(environnement_isole: Path) -> None:
    deposer_agenda(environnement_isole)
    chemin = tenir_le_verrou(environnement_isole)

    message, page = traiter()

    assert message == "Un traitement est déjà en cours."
    assert "Un traitement est déjà en cours." in page
    assert chemin.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_c6_tableau_de_bord_porte_le_bouton_traiter_maintenant() -> None:
    with navigateur() as client:
        reponse = client.get("/")
    assert reponse.status_code == 200
    page = html.unescape(reponse.text)
    assert "Traiter maintenant" in page
    formulaire = re.compile(
        r"<form(?=[^>]*\baction\s*=\s*[\"']/traiter[\"'])(?=[^>]*\bmethod\s*=\s*[\"']post[\"'])",
        re.IGNORECASE,
    )
    assert formulaire.search(page)


# --- Contrat, hors critères numérotés ---------------------------------------


@pytest.mark.parametrize(
    ("resultat", "attendu"),
    [
        ({"model": "fake", "prompt_tokens": 120, "completion_tokens": 30}, ("fake", 120, 30)),
        ({"processed": 2}, (None, None, None)),
        ({"model": "fake"}, ("fake", None, None)),
    ],
)
def test_contrat_modele_et_tokens_recopies_au_journal(
    verrou: Path, resultat: dict[str, Any], attendu: tuple
) -> None:
    """`specs/05 §2.3` : tokens recopiés seulement s'ils sont supérieurs à zéro."""
    lancer([AgentFactice("minutes", [], resultat=resultat)], verrou)
    assert journal("model, prompt_tokens, completion_tokens") == [attendu]


def test_contrat_chaque_agent_a_sa_propre_session_fermee(verrou: Path) -> None:
    ouvertes: list[SessionTracee] = []
    agents = [
        AgentFactice("ingestion", []),
        AgentFactice("agenda", [], erreur=RuntimeError("panne")),
        AgentFactice("transcripts", []),
    ]
    pipeline(agents, verrou).run(fabrique_tracee(ouvertes))

    recues = [agent.sessions[0] for agent in agents]
    assert len({id(session) for session in recues}) == 3
    assert all(any(session is ouverte for ouverte in ouvertes) for session in recues)
    assert all(session.fermee for session in recues)


def test_contrat_journal_complet_une_ligne_par_agent_et_par_passage(verrou: Path) -> None:
    """Contrôle « Journal complet » de `specs/09 §5` et principe 5 de `specs/00 §5`."""
    noms = ["ingestion", "agenda", "transcripts"]
    for _ in range(2):
        lancer(
            [
                AgentFactice("ingestion", []),
                AgentFactice("agenda", [], erreur=RuntimeError("panne")),
                AgentFactice("transcripts", []),
            ],
            verrou,
        )
    lignes = journal("agent, target_ref, status, ended_at IS NOT NULL")
    assert [ligne[0] for ligne in lignes] == noms * 2
    assert all(ligne[1] == "" for ligne in lignes)
    assert [ligne[2] for ligne in lignes] == ["ok", "erreur", "ok"] * 2
    assert all(ligne[3] == 1 for ligne in lignes)


def test_contrat_verrou_perime_repris(verrou: Path) -> None:
    """Un verrou au numéro d'un processus mort est repris (specs/05 §3.2)."""
    verrou.write_text(str(pid_mort()), encoding="utf-8")
    passages: list[str] = []
    resultat = lancer([AgentFactice("ingestion", passages)], verrou)

    assert passages == ["ingestion"]
    assert list(resultat) == ["ingestion"]
    assert not verrou.exists()


def test_contrat_build_pipeline_assemble_ingestion_puis_agenda(environnement_isole: Path) -> None:
    from cabinet.agents.base import AgentOutcome
    from cabinet.agents.pipeline import build_pipeline
    from cabinet.config import load_settings
    from cabinet.db.base import get_session, init_db

    init_db()
    resultat = build_pipeline(load_settings()).run(get_session)

    assert list(resultat)[:2] == ["ingestion", "agenda"]
    assert isinstance(resultat["ingestion"], AgentOutcome)
    assert isinstance(resultat["agenda"], str)
    assert "Fichier d'agenda introuvable" in resultat["agenda"]


def test_contrat_build_pipeline_prend_le_verrou_du_repertoire_de_donnees(
    environnement_isole: Path,
) -> None:
    from cabinet.agents.pipeline import PipelineBusy, build_pipeline
    from cabinet.config import load_settings
    from cabinet.db.base import get_session, init_db

    init_db()
    tenir_le_verrou(environnement_isole)
    with pytest.raises(PipelineBusy):
        build_pipeline(load_settings()).run(get_session)
    assert journal() == []


def test_contrat_scenario_s1_socle(environnement_isole: Path) -> None:
    """Scénario S1 de `specs/09 §4` : init, agenda, trois fichiers, un passage."""
    assert cabinet("init").exit_code == 0
    deposer_agenda(environnement_isole)
    deposer_fichiers(environnement_isole)

    resultat = cabinet("run")
    assert resultat.exit_code == 0, resultat.output

    from cabinet.db.base import get_engine

    with Session(get_engine()) as session:
        elements = session.execute(text("SELECT path, status FROM inbox_item")).all()
        cles = session.execute(text("SELECT key FROM series ORDER BY key")).scalars().all()
    etats = {Path(chemin).name: etat for chemin, etat in elements}
    assert etats == FICHIERS_ENTREE
    # Fenêtre du 25.10 au 08.12.2026 : cinq « Point hebdo » et un comité (specs/05 §6.2).
    assert compter("event") == 6
    assert cles == ["comite de pilotage", "point hebdo"]
    assert compter("meeting") == 6
    assert journal_socle() == [("ingestion", "ok"), ("agenda", "ok")]


def test_contrat_scenario_s2_passage_repete(environnement_isole: Path) -> None:
    """Scénario S2 de `specs/09 §4` : deux passages, aucun doublon."""
    deposer_agenda(environnement_isole)
    deposer_fichiers(environnement_isole)

    premier = cabinet("run")
    assert premier.exit_code == 0, premier.output
    comptes = {table: compter(table) for table in ("inbox_item", "event", "series", "meeting")}

    second = cabinet("run")
    assert second.exit_code == 0, second.output
    assert "ingestion : 0 traité(s), 0 erreur(s)" in second.output.splitlines()
    assert {table: compter(table) for table in comptes} == comptes
    assert journal_socle() == [("ingestion", "ok"), ("agenda", "ok")] * 2
