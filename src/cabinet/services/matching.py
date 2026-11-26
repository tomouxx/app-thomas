"""Suggestions de rattachement d'un élément du dossier d'entrée (specs/08 §3.1).

Pour un fichier, on propose les réunions du même jour que la date lue dans son
nom (à défaut, le jour de sa détection), classées par similarité décroissante
entre le nom du fichier (sans date ni extension) et le titre de la réunion.
La similarité est une règle simple (`difflib`), pas un modèle : minuscules,
sans accents, tirets et soulignements convertis en espaces.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet.db.models import InboxItem, Meeting

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_")


def suggest_meetings(
    session: Session, item: InboxItem, limit: int = 5
) -> list[tuple[Meeting, float]]:
    """Réunions du jour de référence, triées par similarité décroissante."""
    meetings = session.scalars(select(Meeting).where(Meeting.date == _reference_day(item))).all()
    scored = [(meeting, _score(_file_name(item.path), meeting.title)) for meeting in meetings]
    scored.sort(key=lambda pair: (-pair[1], pair[0].id))
    return scored[:limit]


def _reference_day(item: InboxItem) -> date:
    """Le jour de la réunion : celui du nom du fichier, sinon celui de la détection."""
    if item.name_date is not None:
        return item.name_date
    assert item.detected_at is not None, "un élément détecté porte toujours une date de détection"
    return item.detected_at.date()


def _file_name(path: str) -> str:
    """Nom du fichier sans extension ni préfixe de date `AAAA-MM-JJ_`."""
    stem = Path(path).stem
    return _DATE_PREFIX_RE.sub("", stem, count=1)


def normalize(raw: str) -> str:
    """Minuscules, sans accents, tirets et soulignements en espaces (specs/08 §3.1)."""
    decomposed = unicodedata.normalize("NFKD", raw.lower())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_accents.replace("-", " ").replace("_", " ")


def _score(name: str, title: str) -> float:
    return SequenceMatcher(None, normalize(name), normalize(title)).ratio()
