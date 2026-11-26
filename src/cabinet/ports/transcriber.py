"""Port de transcription : `Segment`, `TranscriptData` et protocole `Transcriber`.

Les modèles sont ceux de `specs/02 §3.3`, le protocole celui de
`specs/01 §3.4`. Les lecteurs (`readers/`) et le transcripteur local
(`adapters/mlx_whisper_transcriber.py`, lot 2) renvoient tous un
`TranscriptData`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class Segment(BaseModel):
    """Une réplique d'un transcript (specs/02 §3.3)."""

    start_s: float | None
    end_s: float | None
    speaker: str | None
    text: str


class TranscriptData(BaseModel):
    """Le résultat de la lecture d'une source (specs/02 §3.3).

    `text` reprend les segments dans l'ordre, une ligne par segment :
    `{orateur} : {texte}` quand l'orateur est connu, `{texte}` sinon.
    """

    text: str
    segments: list[Segment]


class Transcriber(Protocol):
    """Transcription locale d'un audio (specs/01 §3.4)."""

    def transcribe(self, audio_path: Path, language: str = "fr") -> TranscriptData: ...
