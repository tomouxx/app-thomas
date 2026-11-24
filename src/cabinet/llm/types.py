"""Types d'échange du client de modèle local (specs/02 §3.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Message(BaseModel):
    """Un message du dialogue envoyé au modèle."""

    role: Literal["system", "user", "assistant"]
    content: str


class LLMResult(BaseModel):
    """Résultat d'un appel au modèle."""

    text: str  # réponse, sans bloc de raisonnement
    parsed: BaseModel | None = None  # instance du schéma demandé, sinon None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ConfigError(Exception):
    """Réglage invalide, par exemple une adresse de modèle non locale."""


class LLMError(Exception):
    """Échec d'un appel au modèle local."""
