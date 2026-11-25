"""Dossier d'entrée : lecture des fichiers déposés (specs/05 §5.2).

`FolderInbox` est l'adaptateur du port `Inbox` pour la version 1 : il lit le
dossier `entree/` du répertoire de données. La nature d'un fichier se déduit
de son nom, sans modèle de langage (specs/05 §5.1).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

from cabinet import clock
from cabinet.db.models import InboxKind
from cabinet.ports.inbox import InboxFile

_BLOCK_SIZE = 1024 * 1024
_COPY_DELAY_S = 5.0
_DATE_PREFIX_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_")


class FolderInbox:
    """Lit les fichiers déposés dans un dossier d'entrée (specs/05 §5.2)."""

    def __init__(self, root: Path):
        self._root = root

    def scan(self) -> list[InboxFile]:
        """Les fichiers du dossier, triés par nom ; liste vide si le dossier manque."""
        if not self._root.is_dir():
            return []
        now_ts = clock.now().timestamp()
        files: list[InboxFile] = []
        for entry in sorted(self._root.iterdir(), key=lambda item: item.name):
            if _should_skip(entry, now_ts):
                continue
            files.append(_to_inbox_file(entry))
        return files


def _should_skip(path: Path, now_ts: float) -> bool:
    """Sous-dossier, nom caché ou `~$`, ou modifié depuis moins de 5 secondes."""
    name = path.name
    if name.startswith(".") or name.startswith("~$"):
        return True
    if not path.is_file():
        return True
    return now_ts - path.stat().st_mtime < _COPY_DELAY_S


def _to_inbox_file(path: Path) -> InboxFile:
    """Un `InboxFile` pour un fichier du dossier, avec le chemin absolu."""
    return InboxFile(
        path=path.resolve(),
        sha256=_sha256_of(path),
        kind=_kind_of(path.name).value,
        name_date=_date_from_name(path.name),
        size=path.stat().st_size,
    )


def _sha256_of(path: Path) -> str:
    """Empreinte sha256, calculée par blocs de 1 Mio (specs/05 §5.2)."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(_BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def _kind_of(name: str) -> InboxKind:
    """Nature d'un fichier, déduite du nom (tableau de specs/05 §5.1).

    L'extension et le préfixe `app_` se comparent sans tenir compte de la
    casse. Tout autre cas est de nature `inconnu`.
    """
    extension = Path(name).suffix.lower()
    is_capture = name.lower().startswith("app_")
    if extension in (".vtt", ".docx"):
        return InboxKind.TEAMS_TRANSCRIPT
    if extension == ".md":
        return InboxKind.NOTES
    if extension in (".m4a", ".mp3", ".wav"):
        return InboxKind.AUDIO
    if extension == ".txt":
        return InboxKind.APP_CAPTURE_TEXT if is_capture else InboxKind.NOTES
    if extension == ".html" and is_capture:
        return InboxKind.APP_CAPTURE_TEXT
    if extension in (".png", ".jpg", ".jpeg") and is_capture:
        return InboxKind.APP_CAPTURE_IMAGE
    return InboxKind.INCONNU


def _date_from_name(name: str) -> date | None:
    """Date lue en tête du nom (`AAAA-MM-JJ_`), ou None (specs/05 §5.1)."""
    match = _DATE_PREFIX_RE.match(name)
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
