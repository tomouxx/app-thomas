"""Tests unitaires de l'orchestrateur du pipeline (V1-08).

Complètent les tests de contrat (`tests/contrats/test_v1_08_pipeline.py`)
sur les cas qu'ils ne couvrent pas : le code de sortie 1 d'un agent qui
compte des erreurs sans lever, et le résumé du traitement web avec erreurs.
Chaque test travaille dans un répertoire de données temporaire (`CABINET_HOME`,
fixé par `conftest.py`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from cabinet.agents.base import AgentOutcome
from cabinet.agents.pipeline import Pipeline, PipelineBusy, build_pipeline
from cabinet.config import load_settings, paths
from cabinet.db.base import get_engine, get_session, init_db


class AgentFactice:
    """Agent de test conforme au protocole `Agent` (specs/01 §3.3)."""

    def __init__(
        self,
        name: str,
        *,
        erreur: BaseException | None = None,
        resultat: dict[str, int] | None = None,
        passages: list[str] | None = None,
    ) -> None:
        self.name = name
        self.erreur = erreur
        self.resultat = {"processed": 1} if resultat is None else resultat
        self.passages = passages

    def run(self, session: Session) -> AgentOutcome:
        if self.passages is not None:
            self.passages.append(self.name)
        if self.erreur is not None:
            raise self.erreur
        return AgentOutcome(**self.resultat)


@pytest.fixture()
def verrou() -> Path:
    """Base initialisée et chemin du verrou du répertoire de données."""
    init_db()
    return paths().lock_file


def _lancer(agents: list[AgentFactice], chemin: Path) -> dict[str, AgentOutcome | str]:
    """Un passage du pipeline sur la base de test."""
    return Pipeline(agents, chemin).run(get_session)


def _journal() -> list[tuple[str, str]]:
    """Lignes du journal (agent, statut), dans l'ordre d'exécution."""
    with Session(get_engine()) as session:
        return list(session.execute(text("SELECT agent, status FROM agent_run ORDER BY id")))


def _pid_mort() -> int:
    """Numéro d'un processus terminé et récolté."""
    processus = subprocess.Popen([sys.executable, "-c", "pass"])
    processus.wait()
    return processus.pid


def test_agents_executes_dans_l_ordre_de_la_liste(verrou: Path) -> None:
    """L'ordre de la liste est l'ordre d'exécution et des clés du résultat."""
    passages: list[str] = []
    resultat = _lancer([AgentFactice(nom, passages=passages) for nom in ("a", "b", "c")], verrou)
    assert passages == ["a", "b", "c"]
    assert list(resultat) == ["a", "b", "c"]


def test_agent_en_exception_journalise_erreur_et_suivant_execute(verrou: Path) -> None:
    """L'agent en exception est journalisé `erreur` ; le suivant s'exécute."""
    resultat = _lancer(
        [AgentFactice("a"), AgentFactice("b", erreur=RuntimeError("panne")), AgentFactice("c")],
        verrou,
    )
    assert _journal() == [("a", "ok"), ("b", "erreur"), ("c", "ok")]
    assert isinstance(resultat["a"], AgentOutcome)
    assert isinstance(resultat["c"], AgentOutcome)
    assert isinstance(resultat["b"], str) and "panne" in resultat["b"]


def test_verrou_tenu_par_le_processus_de_test_leve_pipeline_busy(verrou: Path) -> None:
    """Un verrou au numéro du processus de test est tenu (specs/05 §3.2)."""
    verrou.write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(PipelineBusy):
        _lancer([AgentFactice("a")], verrou)
    assert verrou.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_verrou_retire_apres_un_passage_normal_et_apres_une_panne(verrou: Path) -> None:
    """Le verrou disparaît après un passage réussi comme après une panne."""
    _lancer([AgentFactice("a")], verrou)
    assert not verrou.exists()
    _lancer([AgentFactice("a", erreur=RuntimeError("panne"))], verrou)
    assert not verrou.exists()


def test_verrou_perime_repris(verrou: Path) -> None:
    """Un verrou au numéro d'un processus mort est repris (specs/05 §3.2)."""
    verrou.write_text(str(_pid_mort()), encoding="utf-8")
    resultat = _lancer([AgentFactice("a")], verrou)
    assert isinstance(resultat["a"], AgentOutcome)
    assert not verrou.exists()


def test_build_pipeline_assemble_ingestion_puis_agenda(verrou: Path) -> None:
    """Lot 1 : ingestion puis agenda, verrou du répertoire de données (specs/05 §3.3)."""
    pipeline = build_pipeline(load_settings())
    assert [agent.name for agent in pipeline._agents] == ["ingestion", "agenda"]
    assert pipeline._lock_path == paths().lock_file


def test_cabinet_run_sort_avec_1_si_un_agent_compte_des_erreurs(
    verrou: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un agent qui compte des erreurs sans lever sort avec 1 (specs/03 §5.3)."""
    from typer.testing import CliRunner

    from cabinet.cli import app

    def faux_pipeline(settings):
        return Pipeline([AgentFactice("ingestion", resultat={"processed": 2, "errors": 1})], verrou)

    monkeypatch.setattr("cabinet.cli.build_pipeline", faux_pipeline)
    resultat = CliRunner().invoke(app, ["run"])
    assert resultat.exit_code == 1, resultat.output
    assert "ingestion : 2 traité(s), 1 erreur(s)" in resultat.output.splitlines()


def test_traiter_resume_signale_les_erreurs_d_un_agent(
    verrou: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le résumé web porte `({n} erreur(s))` pour un agent qui en compte (specs/08 §2.3)."""
    from cabinet.web.app import create_app

    def faux_pipeline(settings):
        return Pipeline([AgentFactice("ingestion", resultat={"processed": 2, "errors": 1})], verrou)

    monkeypatch.setattr("cabinet.web.routes.dashboard.build_pipeline", faux_pipeline)
    with TestClient(create_app()) as client:
        reponse = client.post("/traiter", follow_redirects=False)
    assert reponse.status_code == 303
    message = parse_qs(urlsplit(reponse.headers["location"]).query).get("message", [""])[0]
    assert message == "Traitement terminé. ingestion : 2 (1 erreur(s))"
