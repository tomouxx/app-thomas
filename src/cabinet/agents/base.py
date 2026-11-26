"""Contrat commun de tous les agents (specs/01 §3.3, specs/02 §3.4).

`AgentOutcome` porte le décompte d'un passage et, pour un agent qui appelle
le modèle, son nom et la somme des tokens : l'orchestrateur les recopie sur
la ligne de journal (specs/05 §2.3). `archive_file` range tout fichier lu
avec succès dans `traite/AAAA-MM/` (specs/05 §4).
"""

from __future__ import annotations

import shutil
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session

from cabinet import clock
from cabinet.config import paths
from cabinet.db.models import InboxItem, InboxStatus


class AgentOutcome(BaseModel):
    """Résultat d'un passage d'agent (specs/02 §3.4)."""

    processed: int = 0  # éléments traités avec succès pendant ce passage
    errors: int = 0  # éléments en échec, sans arrêt de l'agent
    model: str | None = None  # nom du modèle appelé, si l'agent en a appelé un
    prompt_tokens: int = 0  # somme sur tous les appels du passage
    completion_tokens: int = 0


class Agent(Protocol):
    """Contrat commun d'un agent (specs/01 §3.3)."""

    name: str  # l'un des sept noms du tableau de specs/00 §1

    def run(self, session: Session) -> AgentOutcome: ...


def archive_file(item: InboxItem) -> None:
    """Range un fichier lu dans `traite/AAAA-MM/` (specs/05 §4).

    Le mois est celui de `clock.today()`. Si un fichier du même nom existe
    déjà dans le dossier, `_{item.id}` est inséré avant l'extension. L'appelant
    valide la session.
    """
    source = paths().home / item.path
    target_dir = paths().processed / clock.today().strftime("%Y-%m")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if target.exists():
        target = target_dir / f"{source.stem}_{item.id}{source.suffix}"
    shutil.move(str(source), str(target))
    item.path = target.relative_to(paths().home).as_posix()
    item.status = InboxStatus.TRAITE
