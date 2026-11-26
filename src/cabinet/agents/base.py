"""Contrat commun de tous les agents (specs/01 §3.3, specs/02 §3.4).

`AgentOutcome` porte le décompte d'un passage et, pour un agent qui appelle
le modèle, son nom et la somme des tokens : l'orchestrateur les recopie sur
la ligne de journal (specs/05 §2.3).
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session


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
