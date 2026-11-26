"""Lecteur des transcripts Teams au format `.docx` (specs/06 §1.2).

La forme supposée est une ligne « orateur puis horodatage » suivie du texte,
à vérifier sur un exemple anonymisé (specs/06 §1.2). Le lecteur transforme
le fichier en `TranscriptData` sans toucher ni à la base ni au fichier. Tout
fichier illisible lève `ValueError` avec un message en français (specs/06 §1).
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document

from cabinet.ports.transcriber import Segment, TranscriptData

_HEADER_RE = re.compile(r"^(?P<speaker>.+?)\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?)$")


def read_docx(path: Path) -> TranscriptData:
    """Lit un fichier `.docx` et renvoie les prises de parole (specs/06 §1.2)."""
    try:
        document = Document(str(path))
    except Exception as error:
        raise ValueError("Fichier Word illisible.") from error
    paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    if not paragraphs:
        raise ValueError("Le document Word ne contient aucun texte.")
    segments = _segments_from_paragraphs(paragraphs)
    return TranscriptData(text=_build_text(segments), segments=segments)


def _segments_from_paragraphs(paragraphs: list[str]) -> list[Segment]:
    """Un segment par ligne d'en-tête, ou un seul segment de texte libre."""
    if not any(_HEADER_RE.match(paragraph) for paragraph in paragraphs):
        return [_free_text_segment(paragraphs)]
    segments: list[Segment] = []
    current: Segment | None = None
    for paragraph in paragraphs:
        match = _HEADER_RE.match(paragraph)
        if match is not None:
            current = Segment(
                start_s=_time_to_seconds(match["time"]),
                end_s=None,
                speaker=match["speaker"],
                text="",
            )
            segments.append(current)
        elif current is not None:
            current.text = f"{current.text} {paragraph}".strip()
    return segments


def _free_text_segment(paragraphs: list[str]) -> Segment:
    """Un seul segment sans orateur ni horodatage, paragraphes joints par un retour à la ligne."""
    return Segment(start_s=None, end_s=None, speaker=None, text="\n".join(paragraphs))


def _time_to_seconds(value: str) -> float:
    """Un horodatage d'en-tête en secondes (`m:ss` ou `h:mm:ss`)."""
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = (int(part) for part in parts)
        return hours * 3600 + minutes * 60 + seconds
    minutes, seconds = (int(part) for part in parts)
    return minutes * 60 + seconds


def _build_text(segments: list[Segment]) -> str:
    """Une ligne par segment, préfixée par l'orateur quand il est connu."""
    lines: list[str] = []
    for segment in segments:
        if segment.speaker is not None:
            lines.append(f"{segment.speaker} : {segment.text}")
        else:
            lines.append(segment.text)
    return "\n".join(lines)
