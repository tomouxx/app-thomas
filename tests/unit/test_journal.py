"""Tests unitaires du journal des exécutions d'agent (V0-03).

Complètent les tests de contrat (`tests/contrats/test_v0_03_journal.py`)
sur les cas qu'ils ne couvrent pas. Chaque test travaille dans un
répertoire de données temporaire (`CABINET_HOME`, fixé par `conftest.py`).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import StatementError

from cabinet import clock
from cabinet.agents.journal import record_run
from cabinet.db.base import get_engine, get_session, init_db
from cabinet.db.models import AgentRun, RunStatus
from cabinet.web.app import create_app


@pytest.fixture()
def session():
    """Base initialisée et session ouverte, fermée en fin de test."""
    init_db()
    with get_session() as ouverte:
        yield ouverte


def test_clock_today_suit_le_now_fixe(monkeypatch: pytest.MonkeyPatch) -> None:
    """`today()` suit `now()`, qu'un test remplace (specs/01 §3.6)."""
    fixe = datetime(2026, 11, 24, 9, 30, tzinfo=ZoneInfo("Europe/Zurich"))
    monkeypatch.setattr(clock, "now", lambda: fixe)
    assert clock.today() == date(2026, 11, 24)


def test_tzdatetime_ecrit_en_utc_et_relit_dans_le_fuseau_local(session) -> None:
    """L'instant est conservé ; la valeur relue porte le fuseau LOCAL_TZ."""
    instant = datetime(2026, 11, 24, 9, 30, tzinfo=ZoneInfo("Europe/Zurich"))
    session.add(AgentRun(agent="essai", started_at=instant, status=RunStatus.OK))
    session.commit()

    brute = session.execute(text("SELECT started_at FROM agent_run")).scalar_one()
    assert str(brute).startswith("2026-11-24 08:30:00")  # UTC, Zurich est à +1 en novembre

    relue = session.get(AgentRun, 1)
    assert relue is not None
    assert relue.started_at == instant
    assert str(relue.started_at.tzinfo) == "Europe/Zurich"


def test_tzdatetime_refuse_une_date_sans_fuseau(session) -> None:
    """Une date sans fuseau lève ValueError à l'écriture (specs/02 §1.1)."""
    with pytest.raises(StatementError) as memoire:
        session.add(AgentRun(agent="essai", started_at=datetime(2026, 11, 24, 9, 30)))
        session.commit()
    assert isinstance(memoire.value.__cause__, ValueError)


def test_erreur_sans_message_porte_le_nom_de_la_classe(session) -> None:
    """Un message vide est remplacé par le nom de la classe (specs/05 §2.2)."""
    with pytest.raises(ZeroDivisionError), record_run(session, "essai"):
        raise ZeroDivisionError()
    erreur = session.execute(text("SELECT error FROM agent_run")).scalar_one()
    assert erreur == "ZeroDivisionError"


def test_erreur_longue_est_tronquee_a_deux_mille_caracteres(session) -> None:
    """Le message d'erreur est limité à 2 000 caractères (specs/05 §2.2)."""
    with pytest.raises(RuntimeError, match="^x"), record_run(session, "essai"):
        raise RuntimeError("x" * 2500)
    longueur = session.execute(text("SELECT length(error) FROM agent_run")).scalar_one()
    assert longueur == 2000


def test_interruption_laisse_la_ligne_a_erreur_et_remonte(session) -> None:
    """Une interruption laisse la ligne à `erreur` et remonte (specs/05 §2.2)."""
    with pytest.raises(KeyboardInterrupt), record_run(session, "essai"):
        raise KeyboardInterrupt()
    statut = session.execute(text("SELECT status FROM agent_run")).scalar_one()
    message = session.execute(text("SELECT error FROM agent_run")).scalar_one()
    assert statut == "erreur"
    assert message == "KeyboardInterrupt"


def test_exception_annule_le_travail_en_attente(session) -> None:
    """Un objet ajouté avant une exception n'est pas enregistré (specs/05 §2.2)."""
    with pytest.raises(RuntimeError), record_run(session, "essai"):
        session.add(AgentRun(agent="autre", started_at=clock.now()))
        raise RuntimeError("échec")
    noms = [ligne[0] for ligne in session.execute(text("SELECT agent FROM agent_run ORDER BY id"))]
    assert noms == ["essai"]


def test_get_session_renvoie_des_sessions_neuves() -> None:
    """Chaque appel renvoie une session neuve, sans expiration au commit."""
    init_db()
    premiere = get_session()
    seconde = get_session()
    try:
        assert premiere is not seconde
        ligne = AgentRun(agent="essai", started_at=clock.now())
        premiere.add(ligne)
        premiere.commit()
    finally:
        premiere.close()
        seconde.close()
    # Sans expiration au commit, l'objet reste utilisable après la fermeture.
    assert ligne.agent == "essai"


def test_second_appel_d_init_db_ne_perd_rien(session) -> None:
    """`init_db()` est idempotent (specs/05 §2.1)."""
    with record_run(session, "essai"):
        pass
    init_db()
    nombre = session.execute(text("SELECT count(*) FROM agent_run")).scalar_one()
    assert nombre == 1


def test_get_engine_resout_le_chemin_a_chaque_appel(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Changer `CABINET_HOME` entre deux appels donne un autre moteur."""
    init_db()
    premier = get_engine()
    monkeypatch.setenv("CABINET_HOME", str(tmp_path / "autre"))
    second = get_engine()
    assert premier is not second


def test_sortie_normale_valide_le_travail_de_l_appelant() -> None:
    """Le commit de `record_run` valide aussi le travail en attente (specs/05 §2.2)."""
    init_db()
    with get_session() as session, record_run(session, "essai"):
        session.add(AgentRun(agent="autre", started_at=clock.now()))
    with get_session() as session:
        noms = [ligne[0] for ligne in session.execute(text("SELECT agent FROM agent_run ORDER BY id"))]
    assert noms == ["essai", "autre"]


def test_page_journal_affiche_en_cours_sans_date_de_fin() -> None:
    """Une ligne sans `ended_at` affiche « en cours » (specs/08 §8)."""
    init_db()
    with get_session() as session:
        session.execute(
            text(
                "INSERT INTO agent_run (agent, target_ref, status, started_at) "
                "VALUES ('agenda', '', 'en_cours', '2026-11-24 08:30:00.000000')"
            )
        )
        session.commit()
    with TestClient(create_app()) as navigateur:
        page = navigateur.get("/journal").text
    assert "en cours" in page


def test_page_journal_tri_par_identifiant_si_meme_heure() -> None:
    """À `started_at` égale, le tri est par `id` décroissant (specs/08 §8)."""
    init_db()
    with get_session() as session:
        for nom in ("alpha", "beta"):
            session.execute(
                text(
                    "INSERT INTO agent_run (agent, target_ref, status, started_at) "
                    "VALUES (:nom, '', 'ok', '2026-11-24 08:30:00.000000')"
                ),
                {"nom": nom},
            )
        session.commit()
    with TestClient(create_app()) as navigateur:
        page = navigateur.get("/journal").text
    assert page.index("beta") < page.index("alpha")
