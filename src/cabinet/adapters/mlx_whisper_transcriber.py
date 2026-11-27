"""Transcription locale via `mlx-whisper` (specs/06 §3.1).

La bibliothèque est importée à l'intérieur de `transcribe`, jamais au
chargement du module : importer `cabinet.web.app` ne doit pas la faire
apparaître dans `sys.modules`. À sa première utilisation, elle télécharge le
modèle depuis Hugging Face (specs/06 §3.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cabinet.ports.transcriber import Segment, TranscriptData

INSTALL_HINT = '.venv/bin/pip install -e ".[audio]"'


class TranscriberUnavailable(Exception):
    """La bibliothèque `mlx-whisper` n'est pas installée (specs/06 §3.1)."""


class MlxWhisperTranscriber:
    """Transcription locale d'un audio via `mlx-whisper` (specs/06 §3.1)."""

    def __init__(self, model: str) -> None:
        self.model = model

    def transcribe(self, audio_path: Path, language: str = "fr") -> TranscriptData:
        """Transcrit un fichier audio et renvoie ses segments (specs/06 §3.1)."""
        try:
            import mlx_whisper
        except ImportError as error:
            raise TranscriberUnavailable(
                "La transcription locale n'est pas installée. Lance : " + INSTALL_HINT
            ) from error
        result = mlx_whisper.transcribe(
            str(audio_path), path_or_hf_repo=self.model, language=language
        )
        segments = _build_segments(result["segments"])
        return TranscriptData(
            text="\n".join(segment.text for segment in segments), segments=segments
        )


def _build_segments(raw_segments: list[dict[str, Any]]) -> list[Segment]:
    """Un `Segment` par segment de la bibliothèque ; les vides sont écartés."""
    segments: list[Segment] = []
    for raw in raw_segments:
        text = str(raw["text"]).strip()
        if not text:
            continue
        segments.append(
            Segment(start_s=raw["start"], end_s=raw["end"], speaker=None, text=text)
        )
    return segments
