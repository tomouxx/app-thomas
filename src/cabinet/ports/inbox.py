"""Port du dossier d'entrée (specs/01 §3.4, specs/02 §3.2).

Le port décrit ce que les agents attendent d'un dossier d'entrée : une liste
de fichiers repérés, sans rien savoir de leur emplacement. L'adaptateur de la
version 1 est `FolderInbox` (`src/cabinet/adapters/folder_inbox.py`).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class InboxFile(BaseModel):
    """Un fichier repéré dans le dossier d'entrée (specs/02 §3.2)."""

    path: Path  # chemin absolu du fichier dans entree/
    sha256: str
    kind: str  # valeur de InboxKind
    name_date: date | None
    size: int  # octets


class Inbox(Protocol):
    """Port du dossier d'entrée (specs/01 §3.4)."""

    def scan(self) -> list[InboxFile]: ...
