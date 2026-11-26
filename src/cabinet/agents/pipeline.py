"""Orchestrateur du pipeline : enchaîne les agents, journalise chacun et
empêche deux exécutions simultanées (specs/05 §3).

Le verrou est le fichier `data/pipeline.lock` : il contient le numéro du
processus qui tient le passage. Un verrou au numéro d'un processus mort est
repris ; un verrou vide ou illisible est tenu (specs/05 §3.2, specs/05 §7).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from cabinet.adapters.folder_inbox import FolderInbox
from cabinet.adapters.ics_calendar import IcsCalendarSource
from cabinet.agents.agenda import AgendaAgent
from cabinet.agents.base import Agent, AgentOutcome
from cabinet.agents.ingestion import IngestionAgent
from cabinet.agents.journal import record_run
from cabinet.agents.transcripts import TranscriptsAgent
from cabinet.config import Settings, paths
from cabinet.db.models import AgentRun


class PipelineBusy(Exception):
    """Un passage du pipeline est déjà en cours (specs/05 §3.2)."""


class Pipeline:
    """Enchaîne les agents dans l'ordre, chacun journalisé (specs/05 §3.1)."""

    def __init__(self, agents: list[Agent], lock_path: Path) -> None:
        self._agents = agents
        self._lock_path = lock_path

    def run(self, session_factory: Callable[[], Session]) -> dict[str, AgentOutcome | str]:
        """Un passage complet : une ligne de journal par agent (specs/05 §3.1).

        Une valeur `AgentOutcome` signale un agent allé au bout ; une chaîne
        signale un agent qui a levé une exception. L'ordre des clés est
        l'ordre d'exécution.
        """
        self._take_lock()
        try:
            result: dict[str, AgentOutcome | str] = {}
            for agent in self._agents:
                result[agent.name] = self._run_agent(agent, session_factory)
            return result
        finally:
            self._release_lock()

    def _run_agent(
        self, agent: Agent, session_factory: Callable[[], Session]
    ) -> AgentOutcome | str:
        """Un agent dans `record_run`, sur sa propre session (specs/05 §3.1)."""
        # Une panne de la fabrique remonte telle quelle ; le verrou est retiré par `run`.
        session = session_factory()
        try:
            with record_run(session, agent.name) as execution:
                outcome = agent.run(session)
                self._copy_outcome_to_journal(execution, outcome)
            return outcome
        except Exception as error:  # l'agent suivant s'exécute quand même (specs/05 §3.1)
            return str(error) or type(error).__name__
        finally:
            session.close()

    def _copy_outcome_to_journal(self, execution: AgentRun, outcome: AgentOutcome) -> None:
        """Modèle et tokens recopiés sur la ligne, quand ils sont > 0 (specs/05 §2.3)."""
        execution.model = outcome.model
        if outcome.prompt_tokens > 0:
            execution.prompt_tokens = outcome.prompt_tokens
        if outcome.completion_tokens > 0:
            execution.completion_tokens = outcome.completion_tokens

    def _take_lock(self) -> None:
        """Crée le verrou atomiquement ; reprend un verrou périmé (specs/05 §3.2)."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = self._create_lock()
        except FileExistsError:
            if not self._lock_is_stale():
                raise PipelineBusy("Un traitement est déjà en cours.") from None
            self._lock_path.unlink(missing_ok=True)
            try:
                descriptor = self._create_lock()  # reprise, une seule fois (specs/05 §3.2)
            except FileExistsError:
                raise PipelineBusy("Un traitement est déjà en cours.") from None
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))

    def _create_lock(self) -> int:
        """Création atomique du fichier de verrou ; `FileExistsError` s'il existe."""
        return os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    def _lock_is_stale(self) -> bool:
        """Vrai si le verrou porte le numéro d'un processus mort (specs/05 §3.2)."""
        try:
            pid = int(self._lock_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False  # contenu vide ou illisible : le verrou est tenu (specs/05 §7)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False  # processus vivant d'un autre utilisateur : le verrou est tenu
        return False

    def _release_lock(self) -> None:
        """Retire le verrou, y compris après une exception (specs/05 §3.1)."""
        self._lock_path.unlink(missing_ok=True)


def build_pipeline(settings: Settings) -> Pipeline:
    """Les agents du lot 2, dans l'ordre (specs/05 §3.3)."""
    agents: list[Agent] = [
        IngestionAgent(FolderInbox(paths().inbox)),
        AgendaAgent(IcsCalendarSource(settings.agenda.ics_path)),
        TranscriptsAgent(),
    ]
    return Pipeline(agents, paths().lock_file)
