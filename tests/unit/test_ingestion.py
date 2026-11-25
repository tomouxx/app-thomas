"""Tests unitaires du dossier d'entrée et de l'agent d'ingestion (V1-03).

Complètent les tests de contrat (`tests/contrats/test_v1_03_ingestion.py`)
sur les cas qu'ils ne couvrent pas : empreinte d'un fichier de plus d'1 Mio,
chemin absolu quand la racine est relative, et l'échec d'un élément qui
n'arrête pas les autres (specs/01 §3.3). Chaque test travaille dans un
répertoire de données temporaire (`CABINET_HOME`, fixé par `conftest.py`).
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from cabinet.adapters.folder_inbox import FolderInbox
from cabinet.agents.base import AgentOutcome
from cabinet.agents.ingestion import IngestionAgent
from cabinet.db.base import get_session, init_db
from cabinet.db.models import InboxItem, InboxStatus
from cabinet.ports.inbox import InboxFile


def inbox_dir() -> Path:
    """Le dossier `entree/` du test, créé s'il manque."""
    root = Path(os.environ["CABINET_HOME"]) / "entree"
    root.mkdir(parents=True, exist_ok=True)
    return root


def deposit(name: str, content: bytes | None = None, age_s: float = 60.0) -> Path:
    """Écrit un fichier synthétique dans `entree/` et recule sa date de modification."""
    path = inbox_dir() / name
    path.write_bytes(content if content is not None else b"contenu fictif")
    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))
    return path


def scan() -> list[InboxFile]:
    """Un balayage du dossier d'entrée du test."""
    return FolderInbox(inbox_dir()).scan()


def run_agent(agent: IngestionAgent) -> AgentOutcome:
    """Un passage de l'agent, sur une session neuve."""
    init_db()
    with get_session() as session:
        return agent.run(session)


def items() -> list[InboxItem]:
    """Relit `inbox_item` dans une session neuve, trié par chemin."""
    init_db()
    with get_session() as session:
        return list(session.scalars(select(InboxItem).order_by(InboxItem.path)))


class FixedInbox:
    """Doublure du port `Inbox` : renvoie une liste fixée (specs/01 §3.4)."""

    def __init__(self, files: list[InboxFile]):
        self._files = files

    def scan(self) -> list[InboxFile]:
        return self._files


# --- Nature et date du nom (specs/05 §5.1) ----------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("REUNION.VTT", "teams_transcript"),
        ("COMPTE-RENDU.DOCX", "teams_transcript"),
        ("NOTES.MD", "notes"),
        ("MEMO.TXT", "notes"),
        ("APPEL.MP3", "audio"),
        ("DICTEE.WAV", "audio"),
        ("APP_NOV.HTML", "app_capture_text"),
        ("App_Ecran.JPEG", "app_capture_image"),
        ("ARCHIVE.ZIP", "inconnu"),
    ],
)
def test_kind_with_uppercase_extension(name: str, kind: str) -> None:
    """Chaque ligne du tableau 5.1, avec une extension en majuscules."""
    deposit(name)
    (file,) = scan()
    assert file.kind == kind


def test_name_date_read_from_the_file_name() -> None:
    """`2026-11-23_point-hebdo.vtt` est daté du 23.11.2026, `notes.md` ne l'est pas."""
    deposit("2026-11-23_point-hebdo.vtt")
    deposit("notes.md")
    by_name = {Path(file.path).name: file for file in scan()}
    assert by_name["2026-11-23_point-hebdo.vtt"].name_date == date(2026, 11, 23)
    assert by_name["notes.md"].name_date is None


@pytest.mark.parametrize(
    "name",
    [
        "2026-13-40_point.vtt",  # date impossible
        "2026-11-23-point.vtt",  # pas de tiret bas après la date
        "point_2026-11-23_hebdo.vtt",  # la date n'est pas en tête
    ],
)
def test_name_date_is_none_when_absent_or_impossible(name: str) -> None:
    deposit(name)
    assert scan()[0].name_date is None


# --- Balayage (specs/05 §5.2) -------------------------------------------------


def test_hidden_lock_and_fresh_files_are_ignored() -> None:
    """Fichier caché, fichier `~$` et fichier modifié à l'instant : ignorés."""
    deposit(".cache.md")
    deposit("~$brouillon.docx")
    (inbox_dir() / "en-cours.md").write_bytes(b"copie en cours")
    deposit("notes.md")
    assert [Path(file.path).name for file in scan()] == ["notes.md"]


def test_fingerprint_of_a_file_larger_than_one_mio() -> None:
    """L'empreinte est calculée par blocs de 1 Mio (specs/05 §5.2)."""
    content = b"x" * (2 * 1024 * 1024 + 12345)
    deposit("gros.m4a", content)
    (file,) = scan()
    assert file.sha256 == hashlib.sha256(content).hexdigest()
    assert file.size == len(content)


def test_scan_returns_absolute_paths_with_a_relative_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le chemin renvoyé est absolu, même si la racine l'est pas (specs/05 §5.2)."""
    (tmp_path / "entree").mkdir()
    target = tmp_path / "entree" / "notes.md"
    target.write_bytes(b"contenu fictif")
    stamp = time.time() - 60.0
    os.utime(target, (stamp, stamp))
    monkeypatch.chdir(tmp_path)
    (file,) = FolderInbox(Path("entree")).scan()
    assert file.path.is_absolute()


# --- Agent d'ingestion (specs/05 §5.3) ----------------------------------------


def test_two_runs_create_no_duplicate() -> None:
    """Deux passages de suite ne créent pas de doublon."""
    deposit("notes.md")
    deposit("app_nov.html", b"<html></html>")
    agent = IngestionAgent(FolderInbox(inbox_dir()))
    run_agent(agent)
    first = [(row.path, row.sha256) for row in items()]
    run_agent(agent)
    assert [(row.path, row.sha256) for row in items()] == first
    assert len(first) == 2


def test_initial_states_for_audio_capture_and_archive() -> None:
    """Audio à `consentement_requis`, capture `app_` à `pret`, `.zip` à `ignore`."""
    deposit("comite.m4a", b"\x00\x00ftyp")
    deposit("app_nov.html", b"<html></html>")
    deposit("pieces.zip", b"PK\x03\x04")
    run_agent(IngestionAgent(FolderInbox(inbox_dir())))
    by_path = {item.path: item for item in items()}
    assert by_path["entree/comite.m4a"].status is InboxStatus.CONSENTEMENT_REQUIS
    assert by_path["entree/app_nov.html"].status is InboxStatus.PRET
    assert by_path["entree/pieces.zip"].status is InboxStatus.IGNORE


def test_a_failing_item_does_not_stop_the_others(tmp_path: Path) -> None:
    """L'échec d'un élément est annulé et compté, sans arrêt des autres (specs/01 §3.3)."""
    deposit("notes.md")
    bad = InboxFile(
        path=tmp_path / "hors" / "x.md",  # hors du répertoire de données
        sha256="ab" * 32,
        kind="notes",
        name_date=None,
        size=1,
    )
    agent = IngestionAgent(FixedInbox([bad, scan()[0]]))
    outcome = run_agent(agent)
    assert (outcome.processed, outcome.errors) == (1, 1)
    assert [item.path for item in items()] == ["entree/notes.md"]
