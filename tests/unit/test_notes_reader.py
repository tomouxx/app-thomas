"""Tests unitaires du lecteur de notes et de l'agent `transcripts` (V2-02).

Complètent les tests de contrat (`tests/contrats/test_v2_02_notes.py`) sur
les cas qu'ils ne couvrent pas : les fins de ligne Windows, un en-tête
fermé sur la ligne suivante, le repli cp1252, et le choix du lecteur par la
nature plutôt que par l'extension (specs/06 §1.3 et §2). Chaque test
travaille dans un répertoire de données temporaire (`CABINET_HOME`, fixé par
`conftest.py`).
"""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from cabinet.agents.base import AgentOutcome
from cabinet.agents.transcripts import TranscriptsAgent
from cabinet.config import paths
from cabinet.db.base import get_session, init_db
from cabinet.db.models import (
    InboxItem,
    InboxKind,
    InboxStatus,
    Meeting,
    Series,
    Transcript,
    TranscriptSource,
)
from cabinet.readers.notes import read_notes

ZURICH = ZoneInfo("Europe/Zurich")
DETECTED_AT = datetime(2026, 11, 30, 18, 0, tzinfo=ZURICH)


def write(path: Path, content: bytes) -> Path:
    """Écrit des octets dans un fichier temporaire et renvoie son chemin."""
    path.write_bytes(content)
    return path


def single_segment_text(path: Path, content: bytes) -> str:
    """Le texte du segment unique d'un fichier de notes."""
    data = read_notes(write(path, content))
    assert len(data.segments) == 1
    return data.segments[0].text


def test_fins_de_ligne_windows_normalisees(tmp_path: Path) -> None:
    """Les fins de ligne Windows deviennent des `\n` (choix noté dans le ticket)."""
    content = "Première ligne.\r\nSeconde ligne.\r\n".encode()
    data = read_notes(write(tmp_path / "windows.txt", content))
    assert data.text == "Première ligne.\nSeconde ligne."
    assert data.segments[0].text == data.text


def test_en_tete_ferme_sur_ligne_suivante(tmp_path: Path) -> None:
    """Un en-tête `---` / `---` laisse le corps seul (specs/06 §1.3)."""
    assert single_segment_text(tmp_path / "court.md", b"---\n---\nCorps des notes.\n") == (
        "Corps des notes."
    )


def test_tirets_avec_espace_ne_sont_pas_un_en_tete(tmp_path: Path) -> None:
    """Seule une ligne `---` exacte ouvre l'en-tête (interprétation notée)."""
    content = b"--- \ntitre: Point hebdo\n---\nCorps.\n"
    assert single_segment_text(tmp_path / "espace.md", content) == (
        "--- \ntitre: Point hebdo\n---\nCorps."
    )


def test_utf8_prefere_au_cp1252(tmp_path: Path) -> None:
    """Un octet valide en UTF-8 n'est pas décodé en cp1252."""
    assert single_segment_text(tmp_path / "utf8.txt", b"caf\xc3\xa9") == "café"


def test_repli_cp1252_decode_les_octets_windows(tmp_path: Path) -> None:
    """Un octet invalide en UTF-8 est décodé en cp1252 (0xE9 = é, 0x80 = €)."""
    assert single_segment_text(tmp_path / "windows.txt", b"caf\xe9 : 1 \x80") == "café : 1 €"


def setup_meeting() -> int:
    """Crée la base, une série et une réunion ; renvoie l'identifiant de celle-ci."""
    init_db()
    with get_session() as session:
        series = Series(key="comite de pilotage", label="Comité de pilotage")
        session.add(series)
        meeting = Meeting(series=series, title="Comité de pilotage", date=date(2026, 11, 30))
        session.add(meeting)
        session.commit()
    return meeting.id


def deposit(name: str, content: bytes) -> None:
    """Écrit un fichier dans `entree/` du répertoire de données du test."""
    inbox = paths().inbox
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_bytes(content)


def add_item(path_value: str, kind: InboxKind, meeting_id: int) -> None:
    """Un élément d'entrée à l'état `pret`, rattaché à la réunion."""
    with get_session() as session:
        session.add(
            InboxItem(
                path=path_value,
                sha256=sha256(path_value.encode()).hexdigest(),
                kind=kind,
                status=InboxStatus.PRET,
                meeting_id=meeting_id,
                consent_confirmed=False,
                detected_at=DETECTED_AT,
            )
        )
        session.commit()


def run_agent() -> AgentOutcome:
    """Un passage de l'agent sur une session neuve, validée à la sortie."""
    with get_session() as session:
        outcome = TranscriptsAgent().run(session)
        session.commit()
    return outcome


def item_rows() -> list[InboxItem]:
    """Relit `inbox_item` dans une session neuve, par identifiant."""
    with get_session() as session:
        return list(session.scalars(select(InboxItem).order_by(InboxItem.id)))


def transcript_rows() -> list[Transcript]:
    """Relit `transcript` dans une session neuve, par identifiant."""
    with get_session() as session:
        return list(session.scalars(select(Transcript).order_by(Transcript.id)))


def test_nature_notes_lue_comme_notes_malgre_extension_vtt() -> None:
    """La nature `notes` choisit le lecteur, pas l'extension (specs/06 §2)."""
    meeting_id = setup_meeting()
    deposit("2026-11-30_notes.vtt", "Notes libres, sans en-tête WEBVTT.".encode())
    add_item("entree/2026-11-30_notes.vtt", InboxKind.NOTES, meeting_id)

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (1, 0)
    transcript = transcript_rows()[0]
    assert transcript.source == TranscriptSource.NOTES
    assert transcript.text == "Notes libres, sans en-tête WEBVTT."


def test_transcript_teams_extension_inconnue_passe_en_erreur() -> None:
    """Un transcript Teams sans extension connue passe à `erreur` (specs/06 §2)."""
    meeting_id = setup_meeting()
    deposit("2026-11-30_transcript.txt", b"Texte libre.")
    add_item("entree/2026-11-30_transcript.txt", InboxKind.TEAMS_TRANSCRIPT, meeting_id)

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (0, 1)
    item = item_rows()[0]
    assert item.status == InboxStatus.ERREUR
    assert item.error == "Format non pris en charge : 2026-11-30_transcript.txt."
    assert transcript_rows() == []
