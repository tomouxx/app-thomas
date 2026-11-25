"""Journal des exécutions d'agent (specs/05 §2.2).

`record_run` s'ouvre toujours au début d'une unité de travail, sur une
session qui n'a rien en attente. La trace d'une erreur survit à l'annulation
que ferait l'appelant après coup : tout est journalisé, y compris en cas
d'erreur (specs/00 §5, principe 5).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from cabinet import clock
from cabinet.db.models import AgentRun, RunStatus


@contextmanager
def record_run(session: Session, agent: str, target_ref: str = "") -> Iterator[AgentRun]:
    """Journalise une exécution d'agent et donne la ligne à l'appelant.

    En sortie normale, la ligne passe à `ok` et le travail en attente de
    l'appelant est validé. Sur toute exception, interruption comprise, le
    travail en attente est annulé, la ligne passe à `erreur` avec le message
    de l'exception (ou le nom de sa classe si le message est vide, 2 000
    caractères au plus), puis l'exception est relancée.
    """
    execution = AgentRun(
        agent=agent,
        target_ref=target_ref,
        status=RunStatus.EN_COURS,
        started_at=clock.now(),
    )
    session.add(execution)
    session.commit()  # la ligne est visible par un autre processus pendant l'exécution
    try:
        yield execution
    except BaseException as error:  # interruption comprise (specs/05 §2.2)
        session.rollback()
        execution = session.get(AgentRun, execution.id)
        message = str(error) or type(error).__name__
        execution.status = RunStatus.ERREUR
        execution.error = message[:2000]
        execution.ended_at = clock.now()
        session.commit()
        raise
    else:
        execution.status = RunStatus.OK
        execution.ended_at = clock.now()
        session.commit()
