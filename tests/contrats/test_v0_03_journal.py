"""Tests de contrat du ticket V0-03, « Journal des exécutions d'agent ».

Écrits par le relecteur avant la vague. Chaque test travaille dans un
répertoire de données temporaire (`CABINET_HOME`, fixé par `conftest.py`).

Le ticket ne dit pas ce que renvoie `get_session()`. Les tests ouvrent donc
leurs propres sessions sur `get_engine()` et relisent la table `agent_run`
en SQL, par les noms de la section 6 de `SPEC.md`.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session


def ouvrir_session() -> Session:
    """Initialise la base du répertoire temporaire et ouvre une session."""
    from cabinet.db.base import get_engine, init_db

    init_db()
    return Session(get_engine())


def lignes(colonnes: str = "agent, target_ref, status, error") -> list[tuple]:
    """Relit la table dans une session neuve, comme le ferait un autre processus."""
    from cabinet.db.base import get_engine

    with Session(get_engine()) as session:
        requete = text(f"SELECT {colonnes} FROM agent_run ORDER BY id")
        return [tuple(ligne) for ligne in session.execute(requete)]


def enregistrer(agent: str, cible: str = "") -> None:
    from cabinet.agents.journal import record_run

    with ouvrir_session() as session:
        with record_run(session, agent, cible):
            pass
        session.commit()


def page_journal() -> str:
    from cabinet.web.app import create_app

    with TestClient(create_app()) as navigateur:
        reponse = navigateur.get("/journal")
    assert reponse.status_code == 200
    return reponse.text


# --- Critère 1 -------------------------------------------------------------


def test_c1_bloc_sans_exception_laisse_une_ligne_ok() -> None:
    from cabinet.agents.journal import record_run

    with ouvrir_session() as session:
        with record_run(session, "ingestion", "fichier:1") as execution:
            pass
        session.commit()
        assert execution.ended_at is not None
        assert execution.ended_at >= execution.started_at
    assert lignes() == [("ingestion", "fichier:1", "ok", None)]


def test_c1_ligne_en_cours_pendant_le_bloc() -> None:
    from cabinet.agents.journal import record_run

    with ouvrir_session() as session:
        with record_run(session, "agenda") as execution:
            assert execution.status == "en_cours"
            assert execution.started_at is not None
        session.commit()
        assert execution.status == "ok"


def test_c1_l_appelant_note_le_modele_et_les_compteurs() -> None:
    from cabinet.agents.journal import record_run

    with ouvrir_session() as session:
        with record_run(session, "minutes", "reunion:7") as execution:
            execution.model = "qwen-local"
            execution.prompt_tokens = 1200
            execution.completion_tokens = 340
        session.commit()
    assert lignes("model, prompt_tokens, completion_tokens, status") == [
        ("qwen-local", 1200, 340, "ok")
    ]


def test_c1_cible_vide_par_defaut() -> None:
    enregistrer("agenda")
    assert lignes("agent, target_ref") == [("agenda", "")]


# --- Critère 2 -------------------------------------------------------------


def test_c2_exception_laisse_une_ligne_erreur_et_remonte() -> None:
    from cabinet.agents.journal import record_run

    with ouvrir_session() as session:
        with (
            pytest.raises(RuntimeError, match="lecture impossible du fichier"),
            record_run(session, "transcripts", "fichier:9"),
        ):
            raise RuntimeError("lecture impossible du fichier")
        # Un appelant annule son travail après une erreur. La trace doit rester.
        session.rollback()

    relues = lignes()
    assert len(relues) == 1
    agent, cible, etat, erreur = relues[0]
    assert (agent, cible, etat) == ("transcripts", "fichier:9", "erreur")
    assert "lecture impossible du fichier" in erreur


# --- Critère 3 -------------------------------------------------------------


def test_c3_page_journal_affiche_les_agents_enregistres() -> None:
    enregistrer("ingestion", "fichier:1")
    enregistrer("agenda")
    page = page_journal()
    assert "ingestion" in page
    assert "agenda" in page


def test_c3_page_journal_vide_repond_200() -> None:
    ouvrir_session().close()
    page_journal()


# --- Critère 4 -------------------------------------------------------------


def test_c4_base_creee_dans_le_repertoire_temporaire(environnement_isole: Path) -> None:
    assert not environnement_isole.exists()
    enregistrer("ingestion")
    assert (environnement_isole / "data" / "cabinet.db").is_file()


def test_c4_rien_n_est_ecrit_ailleurs(
    tmp_path: Path, environnement_isole: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    faux_domicile = tmp_path / "domicile"
    dossier_courant = tmp_path / "dossier-courant"
    faux_domicile.mkdir()
    dossier_courant.mkdir()
    monkeypatch.setenv("HOME", str(faux_domicile))
    monkeypatch.chdir(dossier_courant)

    enregistrer("ingestion")

    assert (environnement_isole / "data" / "cabinet.db").is_file()
    assert list(faux_domicile.iterdir()) == []
    assert list(dossier_courant.iterdir()) == []


def test_c4_chaque_repertoire_a_sa_propre_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    premier = tmp_path / "premier"
    second = tmp_path / "second"
    monkeypatch.setenv("CABINET_HOME", str(premier))
    enregistrer("ingestion")
    monkeypatch.setenv("CABINET_HOME", str(second))
    enregistrer("agenda")

    assert (premier / "data" / "cabinet.db").is_file()
    assert (second / "data" / "cabinet.db").is_file()
    assert lignes("agent") == [("agenda",)]


# --- Critère 5 -------------------------------------------------------------


def test_c5_page_limitee_a_cent_lignes() -> None:
    from cabinet.agents.journal import record_run

    with ouvrir_session() as session:
        for rang in range(150):
            with record_run(session, f"agent-{rang:03d}"):
                pass
        session.commit()
    assert len(lignes("agent")) == 150
    affiches = set(re.findall(r"agent-\d{3}", page_journal()))
    assert len(affiches) == 100


# --- Contrat, hors critères numérotés ---------------------------------------


def test_contrat_cles_etrangeres_actives_a_chaque_connexion() -> None:
    from cabinet.db.base import get_engine, init_db

    init_db()
    moteur = get_engine()
    for _ in range(2):
        with moteur.connect() as connexion:
            assert connexion.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_contrat_la_plus_recente_en_premier() -> None:
    enregistrer("agent-ancien")
    time.sleep(1.1)
    enregistrer("agent-recent")
    page = page_journal()
    assert page.index("agent-recent") < page.index("agent-ancien")


def test_contrat_la_route_sante_repond_toujours() -> None:
    from cabinet.web.app import create_app

    with TestClient(create_app()) as navigateur:
        assert navigateur.get("/sante").status_code == 200


def test_contrat_page_affiche_cible_etat_et_erreur() -> None:
    from cabinet.agents.journal import record_run

    with (
        ouvrir_session() as session,
        pytest.raises(RuntimeError),
        record_run(session, "timesheets", "capture:42"),
    ):
        raise RuntimeError("capture illisible")
    page = page_journal()
    for attendu in ("timesheets", "capture:42", "erreur", "capture illisible"):
        assert attendu in page
