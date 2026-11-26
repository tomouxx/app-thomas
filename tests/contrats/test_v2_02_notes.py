"""Tests de contrat du ticket V2-02, « Lecture des notes ».

Écrits par le relecteur avant la vague. Un test `test_cN_*` par critère
d'acceptation numéroté, des tests `test_contract_*` pour les points du contrat.

Sources : `specs/06 §1.3` (`read_notes`), `specs/06 §1` (texte rendu, une
ligne par segment, `ValueError` en français), `specs/06 §2` (agent
`transcripts`, nature `notes`, source `notes`), `specs/05 §4` (rangement),
`specs/02 §2.2`, `§2.6` et `§3.3` (tables et modèles d'échange).

Les notes viennent de `fixtures/v2_02/`. `2026-11-30_notes-windows.txt` est
encodé en cp1252 et n'est pas de l'UTF-8 valide. La base est écrite et relue
en SQL par les noms de `specs/02 §2`. La date est fixée au 01.12.2026 par
`cabinet.clock.now` (`specs/09 §3.3`), d'où le rangement dans `traite/2026-12/`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import MetaData, Table, text
from sqlalchemy.sql import sqltypes

FIXTURES = Path(__file__).parent / "fixtures" / "v2_02"
TEAMS_FIXTURES = Path(__file__).parent / "fixtures" / "v2_01"
ZURICH = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 12, 1, 9, 0, tzinfo=ZURICH)
ARCHIVE_DIR = "traite/2026-12"

NOTES_UTF8 = "2026-11-30_notes-comite.md"
NOTES_HEADER = "2026-11-30_notes-entete.md"
NOTES_CP1252 = "2026-11-30_notes-windows.txt"

EXPECTED_UTF8 = (
    "# Notes du comité de pilotage\n"
    "\n"
    "Élodie Exemple présente l'état d'avancement : « budget » 2027 validé.\n"
    "- Cœur du sujet : la recette du lot B, prévue à l'été.\n"
    "- Coût estimé : 1 200 € (à confirmer)."
)
EXPECTED_CP1252 = (
    "Notes tapées sous Windows.\nCoût estimé : 1 200 € pour l'œuvre.\nÀ revoir après l'été."
)
EXPECTED_HEADER_BODY = "Le lot A est livré.\nProchaine étape : recette."


# --- Base de données ---------------------------------------------------------


def table(name: str) -> Table:
    from cabinet.db.base import get_engine

    return Table(name, MetaData(), autoload_with=get_engine())


def to_db(column: Any, value: Any) -> Any:
    """Date et heure en UTC sans fuseau (`specs/02 §1.1`)."""
    if isinstance(value, datetime) and isinstance(column.type, sqltypes.DateTime):
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def insert(name: str, **values: Any) -> int:
    from cabinet.db.base import get_engine

    target = table(name)
    row = {key: to_db(target.c[key], value) for key, value in values.items()}
    with get_engine().begin() as connection:
        return connection.execute(target.insert().values(**row)).inserted_primary_key[0]


def query(sql: str, **params: Any) -> list[dict[str, Any]]:
    from cabinet.db.base import get_engine

    with get_engine().connect() as connection:
        return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def item_row(item_id: int) -> dict[str, Any]:
    rows = query("SELECT id, path, status, error FROM inbox_item WHERE id = :id", id=item_id)
    assert len(rows) == 1
    return rows[0]


def transcript_rows() -> list[dict[str, Any]]:
    rows = query(
        "SELECT id, meeting_id, inbox_item_id, source, text, segments FROM transcript ORDER BY id"
    )
    for row in rows:
        if isinstance(row["segments"], str):
            row["segments"] = json.loads(row["segments"])
    return rows


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Mardi 01.12.2026, 09:00 à Zurich (`specs/09 §3.3`)."""
    monkeypatch.setattr("cabinet.clock.now", lambda: NOW)
    return NOW


@pytest.fixture
def home(environnement_isole: Path) -> Path:
    """Répertoire de données temporaire fixé par `conftest.py`."""
    return environnement_isole


@pytest.fixture
def db(home: Path) -> Path:
    """Base créée, une série et une réunion du 30.11.2026."""
    from cabinet.db.base import init_db

    init_db()
    insert("series", id=1, key="comite de pilotage", label="Comité de pilotage", followed=False)
    insert("meeting", id=1, series_id=1, title="Comité de pilotage", date=date(2026, 11, 30))
    return home


def deposit(home: Path, name: str, content: bytes | None = None, source: Path = FIXTURES) -> str:
    inbox = home / "entree"
    inbox.mkdir(parents=True, exist_ok=True)
    data = (source / name).read_bytes() if content is None else content
    (inbox / name).write_bytes(data)
    return f"entree/{name}"


def add_item(path: str, kind: str = "notes", status: str = "pret", meeting_id: int = 1) -> int:
    return insert(
        "inbox_item",
        path=path,
        sha256=sha256(path.encode()).hexdigest(),
        kind=kind,
        status=status,
        meeting_id=meeting_id,
        consent_confirmed=False,
        detected_at=datetime(2026, 11, 30, 18, 0, tzinfo=ZURICH),
    )


def run_agent() -> Any:
    """Un passage de l'agent sur une session neuve, validée à la sortie (`specs/01 §3.3`)."""
    from cabinet.agents.transcripts import TranscriptsAgent
    from cabinet.db.base import get_session

    with get_session() as session:
        outcome = TranscriptsAgent().run(session)
        session.commit()
    return outcome


def write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def assert_single_plain_segment(data: Any, expected: str) -> None:
    """Un seul segment sans orateur ni horodatage, texte rendu identique (`specs/06 §1.3`)."""
    assert len(data.segments) == 1
    segment = data.segments[0]
    assert (segment.start_s, segment.end_s, segment.speaker) == (None, None, None)
    assert segment.text == expected
    assert data.text.rstrip("\n") == expected


# --- Critère 1 : UTF-8 avec accents -------------------------------------------


def test_c1_utf8_markdown_with_accents_read_without_alteration() -> None:
    from cabinet.readers.notes import read_notes

    data = read_notes(FIXTURES / NOTES_UTF8)

    assert_single_plain_segment(data, EXPECTED_UTF8)


def test_c1_reader_returns_port_models() -> None:
    from cabinet.ports.transcriber import Segment, TranscriptData
    from cabinet.readers.notes import read_notes

    data = read_notes(FIXTURES / NOTES_UTF8)

    assert isinstance(data, TranscriptData)
    assert isinstance(data.segments[0], Segment)


def test_contract_utf8_bom_is_dropped(tmp_path: Path) -> None:
    """Décodage en `utf-8-sig` (`specs/06 §1.3`, étape 1)."""
    from cabinet.readers.notes import read_notes

    content = b"\xef\xbb\xbf" + (FIXTURES / NOTES_UTF8).read_bytes()

    assert_single_plain_segment(read_notes(write(tmp_path, "bom.md", content)), EXPECTED_UTF8)


# --- Critère 2 : cp1252 -------------------------------------------------------


def test_c2_cp1252_text_file_read_without_error() -> None:
    from cabinet.readers.notes import read_notes

    raw = (FIXTURES / NOTES_CP1252).read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")  # le jeu n'est pas de l'UTF-8 valide

    assert_single_plain_segment(read_notes(FIXTURES / NOTES_CP1252), EXPECTED_CP1252)


# --- Critère 3 : en-tête entre `---` ------------------------------------------


def test_c3_front_matter_between_dashes_is_removed() -> None:
    from cabinet.readers.notes import read_notes

    data = read_notes(FIXTURES / NOTES_HEADER)

    assert_single_plain_segment(data, EXPECTED_HEADER_BODY)
    assert "titre:" not in data.text
    assert "---" not in data.text


def test_contract_front_matter_without_closing_line_is_kept(tmp_path: Path) -> None:
    """Sans seconde ligne `---`, rien n'est retiré (`specs/06 §1.3`, étape 2)."""
    from cabinet.readers.notes import read_notes

    content = "---\ntitre: Point hebdo\nLe lot A est livré.\n"
    data = read_notes(write(tmp_path, "ouvert.md", content.encode("utf-8")))

    assert_single_plain_segment(data, content.strip())


def test_contract_dashes_after_the_first_line_are_kept(tmp_path: Path) -> None:
    """Seul un bloc qui commence à la première ligne est un en-tête."""
    from cabinet.readers.notes import read_notes

    content = "Début des notes.\n---\nSuite après un séparateur.\n---\nFin.\n"
    data = read_notes(write(tmp_path, "separateurs.md", content.encode("utf-8")))

    assert_single_plain_segment(data, content.strip())


# --- Contrat : fichier vide ---------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [b"", b"   \n\t\n  ", b"---\ntitre: Point hebdo\n---\n   \n"],
    ids=["empty", "blank", "front-matter-only"],
)
def test_contract_empty_notes_raise_value_error_in_french(tmp_path: Path, content: bytes) -> None:
    from cabinet.readers.notes import read_notes

    with pytest.raises(ValueError) as error:
        read_notes(write(tmp_path, "vide.md", content))
    assert str(error.value) == "Le fichier de notes est vide."


# --- Critère 4 : fichier vide et agent ----------------------------------------


def test_c4_empty_notes_file_puts_item_in_error(db: Path) -> None:
    empty_name, blank_name = "2026-11-30_notes-vides.md", "2026-11-30_notes-blanches.txt"
    empty = add_item(deposit(db, empty_name, b""))
    blank = add_item(deposit(db, blank_name, b"  \r\n\t\r\n"))
    good = add_item(deposit(db, NOTES_UTF8))

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (1, 2)
    for item_id, name in ((empty, empty_name), (blank, blank_name)):
        row = item_row(item_id)
        assert row["status"] == "erreur"
        assert row["error"] == "Le fichier de notes est vide."
        assert row["path"] == f"entree/{name}"
        assert (db / "entree" / name).is_file(), "un fichier en erreur reste dans entree/"
    assert item_row(good)["status"] == "traite"
    assert [row["inbox_item_id"] for row in transcript_rows()] == [good]


# --- Critère 5 : source `notes` -----------------------------------------------


def test_c5_markdown_notes_give_a_transcript_with_source_notes(db: Path) -> None:
    item_id = add_item(deposit(db, NOTES_UTF8))

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (1, 0)
    transcripts = transcript_rows()
    assert len(transcripts) == 1
    transcript = transcripts[0]
    assert transcript["source"] == "notes"
    assert (transcript["meeting_id"], transcript["inbox_item_id"]) == (1, item_id)
    assert transcript["text"].rstrip("\n") == EXPECTED_UTF8
    assert transcript["segments"] == [
        {"start_s": None, "end_s": None, "speaker": None, "text": EXPECTED_UTF8}
    ]


def test_c5_cp1252_txt_notes_give_a_transcript_with_source_notes(db: Path) -> None:
    item_id = add_item(deposit(db, NOTES_CP1252))

    run_agent()

    transcript = transcript_rows()[0]
    assert (transcript["source"], transcript["inbox_item_id"]) == ("notes", item_id)
    assert transcript["text"].rstrip("\n") == EXPECTED_CP1252


# --- Contrat de l'agent -------------------------------------------------------


def test_contract_notes_item_is_processed_and_archived(db: Path) -> None:
    """`specs/06 §2`, étape 4, et `specs/05 §4`."""
    item_id = add_item(deposit(db, NOTES_HEADER))

    run_agent()

    row = item_row(item_id)
    assert (row["status"], row["path"]) == ("traite", f"{ARCHIVE_DIR}/{NOTES_HEADER}")
    assert (db / ARCHIVE_DIR / NOTES_HEADER).is_file()
    assert not (db / "entree" / NOTES_HEADER).exists()
    assert transcript_rows()[0]["text"].rstrip("\n") == EXPECTED_HEADER_BODY


def test_contract_notes_and_teams_transcript_in_the_same_run(db: Path) -> None:
    """La nature choisit le lecteur et la source (`specs/06 §2`, étapes 3 et 4)."""
    notes = add_item(deposit(db, NOTES_UTF8))
    teams = add_item(
        deposit(db, "2026-11-30_sans-orateur.vtt", source=TEAMS_FIXTURES),
        kind="teams_transcript",
    )

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (2, 0)
    sources = {row["inbox_item_id"]: row["source"] for row in transcript_rows()}
    assert sources == {notes: "notes", teams: "teams"}


def test_contract_notes_waiting_for_a_meeting_are_not_read(db: Path) -> None:
    waiting = add_item(deposit(db, NOTES_UTF8), status="a_rattacher")

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (0, 0)
    assert item_row(waiting)["status"] == "a_rattacher"
    assert transcript_rows() == []
