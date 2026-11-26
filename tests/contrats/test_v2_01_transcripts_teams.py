"""Tests de contrat du ticket V2-01, « Lecture des transcripts Teams ».

Écrits par le relecteur avant la vague. Un test `test_cN_*` par critère
d'acceptation numéroté, des tests `test_contract_*` pour les points du contrat
et des précisions.

Sources : `specs/02 §3.3` (`Segment`, `TranscriptData`), `specs/01 §3.4`
(protocole `Transcriber`), `specs/06 §1.1` (`read_vtt`), `specs/06 §1.2`
(`read_docx`), `specs/06 §2` (agent `transcripts`), `specs/05 §4`
(`archive_file`), `specs/05 §3.3` (place de l'agent dans `build_pipeline`),
`specs/02 §2.2` et `§2.6` (tables `inbox_item` et `transcript`).

Les WebVTT viennent de `fixtures/v2_01/`. Les `.docx` sont fabriqués dans le
test avec `python-docx`, dépendance déclarée (`specs/00 §4.11`). La base est
écrite et relue en SQL par les noms de `specs/02 §2`, sans passer par les
classes du modèle. La date est fixée au mardi 01.12.2026 par
`cabinet.clock.now` (`specs/09 §3.3`) : les fichiers sont datés du 30.11 dans
leur nom, le rangement doit donc suivre le mois de `clock.today()`, soit
`traite/2026-12/` (`specs/05 §4`, étape 1).
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

FIXTURES = Path(__file__).parent / "fixtures" / "v2_01"
ZURICH = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 12, 1, 9, 0, tzinfo=ZURICH)
ARCHIVE_DIR = "traite/2026-12"

VTT_SPEAKERS = "2026-11-30_point-hebdo.vtt"
VTT_NO_SPEAKER = "2026-11-30_sans-orateur.vtt"
VTT_CORRUPT = "2026-11-30_corrompu.vtt"
VTT_NO_CUE = "2026-11-30_sans-replique.vtt"

EXPECTED_SPEAKER_SEGMENTS = [
    (1.0, 4.5, "Alice Exemple", "Bonjour à tous, on commence le point hebdo."),
    (5.0, 9.25, "Bruno Fictif", "Le lot A est livré & la recette démarre lundi."),
    (62.0, 65.0, "Alice Exemple", "On garde la date du 3 décembre."),
    (3600.0, 3602.0, "Chloé Test", "Merci, à la semaine prochaine."),
]

EXPECTED_SPEAKER_LINES = [
    f"{speaker} : {said}" for _, _, speaker, said in EXPECTED_SPEAKER_SEGMENTS
]

DOCX_TURNS = [
    "Alice Exemple 0:03",
    "Bonjour à tous.",
    "On commence par le budget.",
    "",
    "Bruno Fictif 1:15",
    "Le budget 2027 est validé.",
    "Chloé Test 1:02:05",
    "Merci, à la prochaine.",
]

EXPECTED_DOCX_SEGMENTS = [
    (3.0, None, "Alice Exemple", "Bonjour à tous. On commence par le budget."),
    (75.0, None, "Bruno Fictif", "Le budget 2027 est validé."),
    (3725.0, None, "Chloé Test", "Merci, à la prochaine."),
]

DOCX_FREE_TEXT = [
    "Compte rendu libre du comité.",
    "",
    "Décision : on reporte le lot B.",
    "Prochaine séance en janvier.",
]


# --- Base de données ---------------------------------------------------------


def table(name: str) -> Table:
    from cabinet.db.base import get_engine

    return Table(name, MetaData(), autoload_with=get_engine())


def to_db(column: Any, value: Any) -> Any:
    """Valeur Python vers la forme stockée : date et heure en UTC sans fuseau (`specs/02 §1.1`)."""
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
    rows = query(
        "SELECT id, path, kind, status, meeting_id, error FROM inbox_item WHERE id = :id",
        id=item_id,
    )
    assert len(rows) == 1
    return rows[0]


def load_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def transcript_rows() -> list[dict[str, Any]]:
    rows = query(
        "SELECT id, meeting_id, inbox_item_id, source, text, segments FROM transcript ORDER BY id"
    )
    for row in rows:
        row["segments"] = load_json(row["segments"])
    return rows


def as_dicts(expected: list[tuple]) -> list[dict[str, Any]]:
    keys = ("start_s", "end_s", "speaker", "text")
    return [dict(zip(keys, segment, strict=True)) for segment in expected]


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
    """Base créée, deux séries et deux réunions du 30.11.2026."""
    from cabinet.db.base import init_db

    init_db()
    insert("series", id=1, key="point hebdo", label="Point hebdo", followed=False)
    insert("series", id=2, key="comite de pilotage", label="Comité de pilotage", followed=False)
    insert("meeting", id=1, series_id=1, title="Point hebdo", date=date(2026, 11, 30))
    insert("meeting", id=2, series_id=2, title="Comité de pilotage", date=date(2026, 11, 30))
    return home


def deposit(home: Path, name: str, content: bytes | None = None) -> str:
    """Copie un fichier dans `entree/` et renvoie son chemin relatif (`specs/02 §2.2`)."""
    inbox = home / "entree"
    inbox.mkdir(parents=True, exist_ok=True)
    data = (FIXTURES / name).read_bytes() if content is None else content
    (inbox / name).write_bytes(data)
    return f"entree/{name}"


def add_item(
    path: str,
    kind: str = "teams_transcript",
    status: str = "pret",
    meeting_id: int | None = 2,
) -> int:
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


def make_docx(path: Path, paragraphs: list[str]) -> Path:
    from docx import Document

    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(str(path))
    return path


def docx_bytes(tmp_path: Path, paragraphs: list[str]) -> bytes:
    return make_docx(tmp_path / "source.docx", paragraphs).read_bytes()


def run_agent() -> Any:
    """Un passage de l'agent sur une session neuve, validée à la sortie (`specs/01 §3.3`)."""
    from cabinet.agents.transcripts import TranscriptsAgent
    from cabinet.db.base import get_session

    with get_session() as session:
        outcome = TranscriptsAgent().run(session)
        session.commit()
    return outcome


def segments_of(data: Any) -> list[tuple]:
    return [(s.start_s, s.end_s, s.speaker, s.text) for s in data.segments]


# --- Critère 1 : WebVTT avec orateurs ----------------------------------------


def test_c1_vtt_with_speakers_gives_segments_speakers_and_timestamps() -> None:
    from cabinet.readers.teams_vtt import read_vtt

    data = read_vtt(FIXTURES / VTT_SPEAKERS)

    assert segments_of(data) == EXPECTED_SPEAKER_SEGMENTS


def test_c1_vtt_text_has_one_line_per_segment_prefixed_by_speaker() -> None:
    """`specs/06 §1` : `{orateur} : {texte}`, segments dans l'ordre."""
    from cabinet.readers.teams_vtt import read_vtt

    data = read_vtt(FIXTURES / VTT_SPEAKERS)

    assert data.text.splitlines() == EXPECTED_SPEAKER_LINES


def test_c1_vtt_header_note_and_empty_cue_are_dropped() -> None:
    """En-tête et blocs `NOTE` ignorés, réplique vide écartée (`specs/06 §1.1`, étapes 2 et 6)."""
    from cabinet.readers.teams_vtt import read_vtt

    data = read_vtt(FIXTURES / VTT_SPEAKERS)

    assert len(data.segments) == 4
    assert all("synthétique" not in segment.text for segment in data.segments)
    assert all(segment.text for segment in data.segments)
    assert "WEBVTT" not in data.text


# --- Critère 2 : WebVTT sans balise d'orateur --------------------------------


def test_c2_vtt_without_voice_tag_has_no_speaker() -> None:
    from cabinet.readers.teams_vtt import read_vtt

    data = read_vtt(FIXTURES / VTT_NO_SPEAKER)

    assert segments_of(data) == [
        (1.0, 3.0, None, "Ouverture de la séance par la présidente."),
        (4.0, 7.5, None, "Point unique : budget 2027."),
    ]
    assert data.text.splitlines() == [
        "Ouverture de la séance par la présidente.",
        "Point unique : budget 2027.",
    ]


# --- Contrat des lecteurs WebVTT ----------------------------------------------


def test_contract_reader_returns_port_models() -> None:
    """`TranscriptData` et `Segment` vivent dans `ports/transcriber.py` (`specs/02 §3.3`)."""
    from cabinet.ports.transcriber import Segment, TranscriptData
    from cabinet.readers.teams_vtt import read_vtt

    data = read_vtt(FIXTURES / VTT_SPEAKERS)

    assert isinstance(data, TranscriptData)
    assert all(isinstance(segment, Segment) for segment in data.segments)


def test_contract_transcriber_protocol_lives_in_port() -> None:
    """Le protocole `Transcriber` de `specs/01 §3.4` est dans le même module."""
    from cabinet.ports import transcriber

    assert hasattr(transcriber, "Transcriber")
    assert hasattr(transcriber.Transcriber, "transcribe")


def test_contract_vtt_with_utf8_bom_is_read(tmp_path: Path) -> None:
    """Lecture en `utf-8-sig` (`specs/06 §1.1`, étape 1)."""
    from cabinet.readers.teams_vtt import read_vtt

    path = tmp_path / "bom.vtt"
    path.write_bytes(b"\xef\xbb\xbf" + (FIXTURES / VTT_NO_SPEAKER).read_bytes())

    assert len(read_vtt(path).segments) == 2


def test_contract_non_webvtt_file_raises_value_error_in_french() -> None:
    from cabinet.readers.teams_vtt import read_vtt

    with pytest.raises(ValueError) as error:
        read_vtt(FIXTURES / VTT_CORRUPT)
    assert str(error.value) == "Le fichier n'est pas au format WebVTT."


def test_contract_vtt_without_cue_raises_value_error_in_french() -> None:
    from cabinet.readers.teams_vtt import read_vtt

    with pytest.raises(ValueError) as error:
        read_vtt(FIXTURES / VTT_NO_CUE)
    assert str(error.value) == "Aucune réplique lisible dans le fichier WebVTT."


# --- Critère 3 : .docx au format supposé --------------------------------------


def test_c3_docx_in_assumed_format_gives_one_segment_per_turn(tmp_path: Path) -> None:
    from cabinet.readers.teams_docx import read_docx

    data = read_docx(make_docx(tmp_path / "point.docx", DOCX_TURNS))

    assert segments_of(data) == EXPECTED_DOCX_SEGMENTS


def test_c3_docx_text_has_one_line_per_turn_prefixed_by_speaker(tmp_path: Path) -> None:
    from cabinet.readers.teams_docx import read_docx

    data = read_docx(make_docx(tmp_path / "point.docx", DOCX_TURNS))

    assert data.text.splitlines() == [
        f"{speaker} : {said}" for _, _, speaker, said in EXPECTED_DOCX_SEGMENTS
    ]


# --- Critère 4 : .docx de texte libre -----------------------------------------


def test_c4_free_text_docx_gives_a_single_segment_with_all_text(tmp_path: Path) -> None:
    from cabinet.readers.teams_docx import read_docx

    data = read_docx(make_docx(tmp_path / "libre.docx", DOCX_FREE_TEXT))

    assert len(data.segments) == 1
    segment = data.segments[0]
    assert (segment.start_s, segment.end_s, segment.speaker) == (None, None, None)
    expected = [paragraph for paragraph in DOCX_FREE_TEXT if paragraph]
    assert segment.text.splitlines() == expected
    assert data.text.splitlines() == expected


# --- Contrat du lecteur .docx -------------------------------------------------


def test_contract_unreadable_docx_raises_value_error_in_french(tmp_path: Path) -> None:
    from cabinet.readers.teams_docx import read_docx

    path = tmp_path / "abime.docx"
    path.write_bytes(b"Ceci n'est pas une archive Word.")

    with pytest.raises(ValueError) as error:
        read_docx(path)
    assert str(error.value) == "Fichier Word illisible."


def test_contract_docx_without_text_raises_value_error_in_french(tmp_path: Path) -> None:
    from cabinet.readers.teams_docx import read_docx

    path = make_docx(tmp_path / "vide.docx", ["", ""])

    with pytest.raises(ValueError) as error:
        read_docx(path)
    assert str(error.value) == "Le document Word ne contient aucun texte."


# --- Critère 5 : agent, rangement et rattachement -----------------------------


def test_c5_vtt_item_processed_archived_and_attached_to_its_meeting(db: Path) -> None:
    item_id = add_item(deposit(db, VTT_SPEAKERS), meeting_id=2)

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (1, 0)
    row = item_row(item_id)
    assert row["status"] == "traite"
    assert row["path"] == f"{ARCHIVE_DIR}/{VTT_SPEAKERS}"
    assert (db / ARCHIVE_DIR / VTT_SPEAKERS).is_file()
    assert not (db / "entree" / VTT_SPEAKERS).exists()

    transcripts = transcript_rows()
    assert len(transcripts) == 1
    transcript = transcripts[0]
    assert (transcript["meeting_id"], transcript["inbox_item_id"]) == (2, item_id)
    assert transcript["source"] == "teams"
    assert transcript["text"].splitlines() == EXPECTED_SPEAKER_LINES
    assert transcript["segments"] == as_dicts(EXPECTED_SPEAKER_SEGMENTS)


def test_c5_docx_item_processed_and_attached_to_its_meeting(db: Path, tmp_path: Path) -> None:
    name = "2026-11-30_point-hebdo.docx"
    item_id = add_item(deposit(db, name, docx_bytes(tmp_path, DOCX_TURNS)), meeting_id=1)

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (1, 0)
    row = item_row(item_id)
    assert (row["status"], row["path"]) == ("traite", f"{ARCHIVE_DIR}/{name}")
    assert (db / ARCHIVE_DIR / name).is_file()
    transcript = transcript_rows()[0]
    assert (transcript["meeting_id"], transcript["inbox_item_id"]) == (1, item_id)
    assert transcript["source"] == "teams"
    assert transcript["segments"] == as_dicts(EXPECTED_DOCX_SEGMENTS)


def test_c5_each_transcript_goes_to_the_meeting_of_its_item(db: Path, tmp_path: Path) -> None:
    vtt_id = add_item(deposit(db, VTT_SPEAKERS), meeting_id=2)
    docx_name = "2026-11-30_comite.docx"
    docx_id = add_item(deposit(db, docx_name, docx_bytes(tmp_path, DOCX_TURNS)), meeting_id=1)

    run_agent()

    links = {row["inbox_item_id"]: row["meeting_id"] for row in transcript_rows()}
    assert links == {vtt_id: 2, docx_id: 1}


# --- Critère 6 : fichier corrompu ---------------------------------------------


def test_c6_corrupt_file_goes_to_error_and_other_files_are_processed(
    db: Path, tmp_path: Path
) -> None:
    first = add_item(deposit(db, VTT_SPEAKERS))
    corrupt = add_item(deposit(db, VTT_CORRUPT))
    docx_name = "2026-11-30_comite.docx"
    last = add_item(deposit(db, docx_name, docx_bytes(tmp_path, DOCX_TURNS)))

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (2, 1)
    broken = item_row(corrupt)
    assert broken["status"] == "erreur"
    assert broken["error"] == "Le fichier n'est pas au format WebVTT."
    assert broken["path"] == f"entree/{VTT_CORRUPT}"
    assert (db / "entree" / VTT_CORRUPT).is_file(), "un fichier en erreur reste dans entree/"
    assert {item_row(first)["status"], item_row(last)["status"]} == {"traite"}
    assert sorted(row["inbox_item_id"] for row in transcript_rows()) == [first, last]


def test_c6_unreadable_docx_goes_to_error_with_its_message(db: Path) -> None:
    name = "2026-11-30_abime.docx"
    broken = add_item(deposit(db, name, b"Ceci n'est pas une archive Word."))
    good = add_item(deposit(db, VTT_NO_SPEAKER))

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (1, 1)
    row = item_row(broken)
    assert (row["status"], row["error"]) == ("erreur", "Fichier Word illisible.")
    assert item_row(good)["status"] == "traite"
    assert [row["inbox_item_id"] for row in transcript_rows()] == [good]


# --- Contrat de l'agent -------------------------------------------------------


def test_contract_missing_file_goes_to_error(db: Path) -> None:
    """`specs/06 §2`, étape 2 : « Fichier introuvable : {nom}. »"""
    missing = add_item(f"entree/{VTT_SPEAKERS}")
    good = add_item(deposit(db, VTT_NO_SPEAKER))

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (1, 1)
    row = item_row(missing)
    assert row["status"] == "erreur"
    assert row["error"].startswith("Fichier introuvable : ")
    assert VTT_SPEAKERS in row["error"]
    assert item_row(good)["status"] == "traite"


def test_contract_only_ready_items_with_a_meeting_are_read(db: Path) -> None:
    """Sélection de `specs/06 §2`, étape 1 : `pret`, transcript Teams, réunion rattachée."""
    waiting = add_item(deposit(db, VTT_SPEAKERS), status="a_rattacher", meeting_id=None)
    unattached = add_item(deposit(db, VTT_NO_SPEAKER), meeting_id=None)
    audio_path = deposit(db, "2026-11-30_comite.m4a", b"faux audio")
    audio = insert(
        "inbox_item",
        path=audio_path,
        sha256=sha256(audio_path.encode()).hexdigest(),
        kind="audio",
        status="pret",
        meeting_id=2,
        consent_confirmed=True,
        consent_at=NOW,
        detected_at=NOW,
    )
    failed = add_item(deposit(db, "2026-11-30_echec.vtt", b"WEBVTT\n"), status="erreur")

    outcome = run_agent()

    assert (outcome.processed, outcome.errors) == (0, 0)
    assert transcript_rows() == []
    assert item_row(waiting)["status"] == "a_rattacher"
    assert item_row(unattached)["status"] == "pret"
    assert item_row(audio)["status"] == "pret"
    assert item_row(failed)["status"] == "erreur"
    assert sorted(p.name for p in (db / "entree").iterdir()) == sorted(
        [VTT_SPEAKERS, VTT_NO_SPEAKER, "2026-11-30_comite.m4a", "2026-11-30_echec.vtt"]
    )


def test_contract_second_run_reads_nothing_again(db: Path) -> None:
    add_item(deposit(db, VTT_SPEAKERS))

    first = run_agent()
    second = run_agent()

    assert first.processed == 1
    assert (second.processed, second.errors) == (0, 0)
    assert len(transcript_rows()) == 1


def test_contract_archive_name_collision_inserts_item_id(db: Path) -> None:
    """`specs/05 §4`, étape 2 : `_{item.id}` avant l'extension si le nom est pris."""
    archive = db / ARCHIVE_DIR
    archive.mkdir(parents=True)
    (archive / VTT_SPEAKERS).write_text("fichier déjà rangé", encoding="utf-8")
    item_id = add_item(deposit(db, VTT_SPEAKERS))

    run_agent()

    stem = VTT_SPEAKERS.removesuffix(".vtt")
    expected = f"{ARCHIVE_DIR}/{stem}_{item_id}.vtt"
    assert item_row(item_id)["path"] == expected
    assert (db / expected).is_file()
    assert (archive / VTT_SPEAKERS).read_text(encoding="utf-8") == "fichier déjà rangé"


def test_contract_agent_runs_in_pipeline_and_leaves_a_journal_line(db: Path) -> None:
    """Nom `transcripts` (`specs/05 §1`), une ligne `agent_run` (principe 5)."""
    from cabinet.agents.base import AgentOutcome
    from cabinet.agents.pipeline import Pipeline
    from cabinet.agents.transcripts import TranscriptsAgent
    from cabinet.db.base import get_session

    add_item(deposit(db, VTT_SPEAKERS))
    agent = TranscriptsAgent()
    assert agent.name == "transcripts"

    result = Pipeline([agent], db / "data" / "pipeline.lock").run(get_session)

    assert isinstance(result["transcripts"], AgentOutcome)
    assert result["transcripts"].processed == 1
    assert query("SELECT agent, status FROM agent_run") == [
        {"agent": "transcripts", "status": "ok"}
    ]


def test_contract_build_pipeline_runs_transcripts_right_after_agenda(home: Path) -> None:
    """`specs/05 §3.3` : `TranscriptsAgent()` suit `AgendaAgent` (répertoire vide, rien à lire)."""
    from cabinet.agents.base import AgentOutcome
    from cabinet.agents.pipeline import build_pipeline
    from cabinet.config import load_settings
    from cabinet.db.base import get_session, init_db

    init_db()
    result = build_pipeline(load_settings()).run(get_session)

    names = list(result)
    assert names[:2] == ["ingestion", "agenda"]
    assert "transcripts" in names
    assert names.index("transcripts") == names.index("agenda") + 1
    assert isinstance(result["transcripts"], AgentOutcome)
