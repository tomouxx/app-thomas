"""Modèles de données (specs/02 §2).

Vague 0 : seule la table `agent_run` existe. Les treize autres tables
arrivent avec les tickets qui les écrivent (specs/10).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cabinet.db.base import Base, TZDateTime


class RunStatus(enum.StrEnum):
    """État d'une exécution d'agent (specs/02 §2.1)."""

    EN_COURS = "en_cours"
    OK = "ok"
    ERREUR = "erreur"


class AgentRun(Base):
    """Une ligne du journal des exécutions d'agent (specs/02 §2.1).

    Le journal ne contient jamais le texte d'un prompt ni celui d'une
    réponse du modèle.
    """

    __tablename__ = "agent_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent: Mapped[str] = mapped_column(String(50), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[RunStatus] = mapped_column(
        Enum(
            RunStatus,
            native_enum=False,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
        default=RunStatus.EN_COURS,
    )
    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
