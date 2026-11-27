"""Tests unitaires de la transcription locale (V2-03).

Complètent les tests de contrat (`tests/contrats/test_v2_03_transcription.py`)
sur les cas qu'ils ne couvrent pas : la langue par défaut de l'adaptateur,
les segments vides écartés, le message d'erreur tronqué et un fichier audio
manquant sur disque. Chaque test travaille dans un répertoire de données
temporaire (`CABINET_HOME`, fixé par `conftest.py`).
"""

from __future__ import annotations

import sys
import types
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from cabinet.adapters.mlx_whisper_transcriber import MlxWhisperTranscriber
from cabinet.agents.base import AgentOutcome
from cabinet.agents.transcription import TranscriptionAgent
from cabinet.config import paths
from cabinet.db.base import get_engine, get_session, init_db
from cabinet.db.models import InboxItem, InboxKind, InboxStatus, Meeting, Series
from cabinet.ports.transcriber import Transcriber, TranscriptData

ZURICH = ZoneInfo("Europe/Zurich")
AUDIO = "2026-11-30_comite-de-pilotage.m4a"


class RecordingTranscriber:
    """Doublure du port `Transcriber` qui note ses appels."""

    def __init__(self, result: TranscriptData | Exception) -> None:
        self.result = result
        self.calls: list[tuple[Path, str]] = []

    def transcribe(self, audio_path: Path, language: str = "fr") -> TranscriptData:
        self.calls.append((audio_path, language))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fake_library(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> list[tuple]:
    """Installe un faux module `mlx_whisper` qui note ses appels et rend `result`."""
    calls: list[tuple] = []
    module = types.ModuleType("mlx_whisper")

    def transcribe(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return result

    module.transcribe = transcribe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx_whisper", module)
    return calls


def seed_audio_item() -> int:
    """Crée une réunion et un audio `pret` consenti ; renvoie l'identifiant."""
    init_db()
    audio_file = paths().home / "entree" / AUDIO
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"audio")
    with get_session() as session:
        series = Series(key="comite de pilotage", label="Comité de pilotage")
        session.add(series)
        session.flush()
        meeting = Meeting(
            series_id=series.id, title="Comité de pilotage", date=date(2026, 11, 30)
        )
        session.add(meeting)
        session.flush()
        item = InboxItem(
            path=f"entree/{AUDIO}",
            sha256=sha256(AUDIO.encode()).hexdigest(),
            kind=InboxKind.AUDIO,
            status=InboxStatus.PRET,
            meeting_id=meeting.id,
            consent_confirmed=True,
            detected_at=datetime(2026, 11, 30, 18, 0, tzinfo=ZURICH),
        )
        session.add(item)
        session.commit()
        return item.id


def run_agent(transcriber: Transcriber) -> AgentOutcome:
    """Un passage de l'agent sur une session neuve, validée à la sortie."""
    with get_session() as session:
        outcome = TranscriptionAgent(transcriber).run(session)
        session.commit()
    return outcome


def item_row(item_id: int) -> dict[str, Any]:
    with get_engine().connect() as connection:
        rows = list(
            connection.execute(
                text("SELECT id, path, status, error FROM inbox_item WHERE id = :id"),
                {"id": item_id},
            ).mappings()
        )
    assert len(rows) == 1
    return dict(rows[0])


def test_adapter_default_language_is_fr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans langue fournie, l'adaptateur transmet `"fr"` à la bibliothèque."""
    calls = fake_library(monkeypatch, {"segments": []})
    audio = tmp_path / AUDIO
    audio.write_bytes(b"audio")

    data = MlxWhisperTranscriber("modele-synthetique").transcribe(audio)

    assert calls == [((str(audio),), {"path_or_hf_repo": "modele-synthetique", "language": "fr"})]
    assert data == TranscriptData(text="", segments=[])


def test_adapter_discards_empty_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Les segments dont le texte est vide après nettoyage sont écartés."""
    calls = fake_library(
        monkeypatch,
        {
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "   "},
                {"start": 1.0, "end": 2.5, "text": " Bonjour."},
            ]
        },
    )
    audio = tmp_path / AUDIO
    audio.write_bytes(b"audio")

    data = MlxWhisperTranscriber("modele-synthetique").transcribe(audio)

    assert len(calls) == 1
    assert [segment.text for segment in data.segments] == ["Bonjour."]
    assert [(s.start_s, s.end_s) for s in data.segments] == [(1.0, 2.5)]
    assert data.text == "Bonjour."


def test_agent_truncates_error_message_to_500_characters() -> None:
    """Le message d'erreur écrit dans `item.error` est limité à 500 caractères."""
    item_id = seed_audio_item()
    fake = RecordingTranscriber(RuntimeError("x" * 600))

    outcome = run_agent(fake)

    assert (outcome.processed, outcome.errors) == (0, 1)
    row = item_row(item_id)
    assert row["status"] == "erreur"
    assert len(row["error"]) == 500


def test_agent_missing_audio_file_gives_transcription_impossible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fichier absent du disque : la bibliothèque lève, l'agent compte une erreur."""
    item_id = seed_audio_item()
    (paths().home / "entree" / AUDIO).unlink()

    def failing_transcribe(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise FileNotFoundError(f"[Errno 2] No such file or directory: {args[0]}")

    module = types.ModuleType("mlx_whisper")
    module.transcribe = failing_transcribe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx_whisper", module)

    outcome = run_agent(MlxWhisperTranscriber("modele-synthetique"))

    assert (outcome.processed, outcome.errors) == (0, 1)
    row = item_row(item_id)
    assert row["status"] == "erreur"
    assert row["error"].startswith("Transcription impossible : ")
