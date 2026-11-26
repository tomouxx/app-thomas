"""Lecteur des transcripts Teams au format WebVTT (specs/06 §1.1).

Le lecteur transforme un fichier `.vtt` en `TranscriptData`. Il ne touche
ni à la base ni au fichier. Tout fichier illisible lève `ValueError` avec
un message en français (specs/06 §1).
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from cabinet.ports.transcriber import Segment, TranscriptData

_TIMESTAMP_RE = re.compile(r"(\d{2}:)?\d{2}:\d{2}\.\d{3}\s+-->\s+(\d{2}:)?\d{2}:\d{2}\.\d{3}")
_SPEAKER_RE = re.compile(r"<v(?:\.[^ >]*)?\s+([^>]+)>")
_TAG_RE = re.compile(r"<[^>]+>")


def read_vtt(path: Path) -> TranscriptData:
    """Lit un fichier WebVTT et renvoie les répliques (specs/06 §1.1)."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines or not lines[0].startswith("WEBVTT"):
        raise ValueError("Le fichier n'est pas au format WebVTT.")
    segments: list[Segment] = []
    for block in _blocks(lines):
        segment = _segment_from_block(block)
        if segment is not None:
            segments.append(segment)
    if not segments:
        raise ValueError("Aucune réplique lisible dans le fichier WebVTT.")
    return TranscriptData(text=_build_text(segments), segments=segments)


def _blocks(lines: list[str]) -> list[list[str]]:
    """Blocs séparés par une ligne vide, sans l'en-tête ni NOTE/STYLE/REGION."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if not line.strip():
            if current:
                blocks.append(current)
            current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return [block for block in blocks[1:] if not _is_ignored(block)]


def _is_ignored(block: list[str]) -> bool:
    """Vrai pour un bloc qui commence par NOTE, STYLE ou REGION."""
    return block[0].startswith(("NOTE", "STYLE", "REGION"))


def _segment_from_block(block: list[str]) -> Segment | None:
    """Le segment d'un bloc, ou `None` s'il n'a pas de ligne d'horodatage."""
    for index, line in enumerate(block):
        match = _TIMESTAMP_RE.match(line)
        if match is None:
            continue
        start_raw, end_raw = (part.strip() for part in match.group(0).split("-->"))
        raw_text = " ".join(block[index + 1 :])
        speaker_match = _SPEAKER_RE.search(raw_text)
        cleaned = html.unescape(_TAG_RE.sub("", raw_text)).strip()
        if not cleaned:
            return None
        return Segment(
            start_s=_timestamp_to_seconds(start_raw),
            end_s=_timestamp_to_seconds(end_raw),
            speaker=speaker_match.group(1).strip() if speaker_match else None,
            text=cleaned,
        )
    return None


def _timestamp_to_seconds(value: str) -> float:
    """Un horodatage WebVTT en secondes (`MM:SS.mmm` ou `HH:MM:SS.mmm`)."""
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    minutes, seconds = int(parts[0]), float(parts[1])
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
