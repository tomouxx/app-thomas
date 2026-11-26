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


class FakeAgent:
    """Agent de test conforme au protocole `Agent` (specs/01 §3.3)."""

    def __init__(
        self,
        name: str,
        *,
        error: BaseException | None = None,
        counts: dict[str, int] | None = None,
        order: list[str] | None = None,
    ) -> None:
        self.name = name
        self.error = error
        self.counts = {"processed": 1} if counts is None else counts
        self.order = order

    def run(self, session: Session) -> AgentOutcome:
        if self.order is not None:
            self.order.append(self.name)
        if self.error is not None:
            raise self.error
        return AgentOutcome(**self.counts)


@pytest.fixture()
def lock() -> Path:
    """Base initialisée et chemin du verrou du répertoire de données."""
    init_db()
    return paths().lock_file


def _run(agents: list[FakeAgent], lock_path: Path) -> dict[str, AgentOutcome | str]:
    """Un passage du pipeline sur la base de test."""
    return Pipeline(agents, lock_path).run(get_session)


def _journal() -> list[tuple[str, str]]:
    """Lignes du journal (agent, statut), dans l'ordre d'exécution."""
    with Session(get_engine()) as session:
        return list(session.execute(text("SELECT agent, status FROM agent_run ORDER BY id")))


def _dead_pid() -> int:
    """Numéro d'un processus terminé et récolté."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_agents_executes_dans_l_ordre_de_la_liste(lock: Path) -> None:
    """L'ordre de la liste est l'ordre d'exécution et des clés du résultat."""
    order: list[str] = []
    outcomes = _run([FakeAgent(name, order=order) for name in ("a", "b", "c")], lock)
    assert order == ["a", "b", "c"]
    assert list(outcomes) == ["a", "b", "c"]


def test_agent_en_exception_journalise_erreur_et_suivant_execute(lock: Path) -> None:
    """L'agent en exception est journalisé `erreur` ; le suivant s'exécute."""
    outcomes = _run(
        [FakeAgent("a"), FakeAgent("b", error=RuntimeError("panne")), FakeAgent("c")],
        lock,
    )
    assert _journal() == [("a", "ok"), ("b", "erreur"), ("c", "ok")]
    assert isinstance(outcomes["a"], AgentOutcome)
    assert isinstance(outcomes["c"], AgentOutcome)
    assert isinstance(outcomes["b"], str) and "panne" in outcomes["b"]


def test_verrou_tenu_par_le_processus_de_test_leve_pipeline_busy(lock: Path) -> None:
    """Un verrou au numéro du processus de test est tenu (specs/05 §3.2)."""
    lock.write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(PipelineBusy):
        _run([FakeAgent("a")], lock)
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_verrou_retire_apres_un_passage_normal_et_apres_une_panne(lock: Path) -> None:
    """Le verrou disparaît après un passage réussi comme après une panne."""
    _run([FakeAgent("a")], lock)
    assert not lock.exists()
    _run([FakeAgent("a", error=RuntimeError("panne"))], lock)
    assert not lock.exists()


def test_verrou_perime_repris(lock: Path) -> None:
    """Un verrou au numéro d'un processus mort est repris (specs/05 §3.2)."""
    lock.write_text(str(_dead_pid()), encoding="utf-8")
    outcomes = _run([FakeAgent("a")], lock)
    assert isinstance(outcomes["a"], AgentOutcome)
    assert not lock.exists()


def test_stale_lock_recreated_by_other_process_raises_busy(
    lock: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seconde course (specs/05 §3.2) : après la reprise d'un verrou périmé, un
    autre processus recrée le verrou avant notre création atomique ; nous levons
    `PipelineBusy` et laissons le verrou en place."""
    lock.write_text(str(_dead_pid()), encoding="utf-8")
    pipeline = Pipeline([FakeAgent("a")], lock)
    original_create = pipeline._create_lock
    attempts = 0

    def create_with_intruder() -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            lock.write_text(str(os.getpid()), encoding="utf-8")
        return original_create()

    monkeypatch.setattr(pipeline, "_create_lock", create_with_intruder)
    with pytest.raises(PipelineBusy):
        pipeline.run(get_session)
    assert lock.exists()


def test_build_pipeline_assemble_ingestion_puis_agenda(lock: Path) -> None:
    """Lot 2 : ingestion, agenda puis transcripts (specs/05 §3.3)."""
    pipeline = build_pipeline(load_settings())
    assert [agent.name for agent in pipeline._agents] == ["ingestion", "agenda", "transcripts"]
    assert pipeline._lock_path == paths().lock_file


def test_cabinet_run_sort_avec_1_si_un_agent_compte_des_erreurs(
    lock: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un agent qui compte des erreurs sans lever sort avec 1 (specs/03 §5.3)."""
    from typer.testing import CliRunner

    from cabinet.cli import app

    def fake_pipeline(settings):
        return Pipeline([FakeAgent("ingestion", counts={"processed": 2, "errors": 1})], lock)

    monkeypatch.setattr("cabinet.cli.build_pipeline", fake_pipeline)
    result = CliRunner().invoke(app, ["run"])
    assert result.exit_code == 1, result.output
    assert "ingestion : 2 traité(s), 1 erreur(s)" in result.output.splitlines()


def test_traiter_resume_signale_les_erreurs_d_un_agent(
    lock: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le résumé web porte `({n} erreur(s))` pour un agent qui en compte (specs/08 §2.3)."""
    from cabinet.web.app import create_app

    def fake_pipeline(settings):
        return Pipeline([FakeAgent("ingestion", counts={"processed": 2, "errors": 1})], lock)

    monkeypatch.setattr("cabinet.web.routes.dashboard.build_pipeline", fake_pipeline)
    with TestClient(create_app()) as client:
        response = client.post("/traiter", follow_redirects=False)
    assert response.status_code == 303
    message = parse_qs(urlsplit(response.headers["location"]).query).get("message", [""])[0]
    assert message == "Traitement terminé. ingestion : 2 (1 erreur(s))"
