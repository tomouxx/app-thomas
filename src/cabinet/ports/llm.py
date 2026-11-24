"""Port du modèle de langage : les agents ne connaissent que ce protocole (specs/04 §1)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from cabinet.llm.types import LLMResult, Message


class LLM(Protocol):
    model: str

    def complete(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        images: list[Path] | None = None,
    ) -> LLMResult: ...
