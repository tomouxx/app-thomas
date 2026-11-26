"""Tests de contrat du ticket V2-03, « Transcription audio locale ».

Écrits par le relecteur avant la vague. Un test `test_cN_*` par critère
d'acceptation numéroté, des tests `test_contract_*` pour les points du contrat.

Sources : `specs/00 §5` (principe 3, consentement avant audio), `specs/06 §3.1`
(`MlxWhisperTranscriber`, appel exact de la bibliothèque, message de
`TranscriberUnavailable`), `specs/06 §3.2` (agent `transcription`),
`specs/06 §3.3` (`purge_audio`), `specs/03 §6` (option `audio` de
`pyproject.toml`), `specs/05 §3.3` (place de l'agent dans `build_pipeline`),
`specs/05 §4` (rangement), `specs/09 §3.2` (`FakeTranscriber`), `specs/09 §4`
(scénario S4) et `specs/09 §5` (« Transcription importée à la demande »).

Aucun test n'importe la vraie bibliothèque `mlx_whisper` ni ne télécharge de
modèle. Le transcripteur de l'agent est doublé par `FakeTranscriber`. Pour
l'adaptateur réel, `sys.modules["mlx_whisper"]` est remplacé le temps du test :
par `None` pour simuler une bibliothèque absente (l'import lève alors
`ImportError`), ou par un faux module qui note ses appels. Le contrôle de
`sys.modules` après l'import de `cabinet.web.app` tourne dans un processus
neuf, avec un faux paquet `mlx_whisper` en tête du chemin d'import : un import
au chargement d'un module se verrait même sans la bibliothèque installée.

La base est écrite et relue en SQL par les noms de `specs/02 §2`. La date est
fixée au 01.12.2026 par `cabinet.clock.now` (`specs/09 §3.3`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import tomllib
import types
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, text
from sqlalchemy.sql import sqltypes

if TYPE_CHECKING:
    from cabinet.ports.transcriber import TranscriptData

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "v2_03"
TEAMS_FIXTURES = Path(__file__).parent / "fixtures" / "v2_01"
ZURICH = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 12, 1, 9, 0, tzinfo=ZURICH)
ARCHIVE_DIR = "traite/2026-12"
AUDIO = "2026-11-30_comite-de-pilotage.m4a"
WHISPER_MODEL = "mlx-community/whisper-large-v3-mlx"
INSTALL_HINT = '.venv/bin/pip install -e ".[audio]"'
NOT_INSTALLED = "La transcription locale n'est pas installée."
MISSING = object()

FAKE_SEGMENTS = [
    (0.0, 2.5, None, "Bonjour à tous."),
    (2.5, 6.0, None, "Le comité valide le budget 2027."),
]


# --- Doublures ----------------------------------------------------------------


class FakeTranscriber:
    """Doublure du port `Transcriber`, conforme à `specs/09 §3.2`."""

    def __init__(self, result: TranscriptData | Exception) -> None:
        self.result = result
        self.calls: list[tuple[Path, str]] = []

    def transcribe(self, audio_path: Path, language: str = "fr") -> TranscriptData:
        self.calls.append((audio_path, language))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ExplicitLanguageTranscriber(FakeTranscriber):
    """Variante sans langue par défaut : elle voit si l'agent transmet `"fr"` lui-même.

    `specs/06 §3.2`, étape 3, appelle `transcriber.transcribe(chemin, "fr")`.
    Avec `FakeTranscriber`, un appel sans langue passerait inaperçu.
    """

    def transcribe(self, audio_path: Path, language: Any = MISSING) -> TranscriptData:
        self.calls.append((audio_path, language))
        return self.result  # type: ignore[return-value]


class FollowerAgent:
    """Agent placé après la transcription pour vérifier que le pipeline continue."""

    name = "minutes"

    def __init__(self) -> None:
        self.ran = False

    def run(self, session: Any) -> Any:
        from cabinet.agents.base import AgentOutcome

        self.ran = True
        return AgentOutcome()


def transcript_data(segments: list[tuple] = FAKE_SEGMENTS) -> Any:
    from cabinet.ports.transcriber import Segment, TranscriptData

    parts = [Segment(start_s=a, end_s=b, speaker=s, text=t) for a, b, s, t in segments]
    return TranscriptData(text="\n".join(part.text for part in parts), segments=parts)


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


def library_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`import mlx_whisper` lève `ImportError`, que la bibliothèque soit installée ou non."""
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)


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
    rows = query(
        "SELECT id, path, kind, status, meeting_id, consent_confirmed, consent_at, error"
        " FROM inbox_item WHERE id = :id",
        id=item_id,
    )
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
    """Base créée, deux séries et deux réunions du 30.11.2026."""
    from cabinet.db.base import init_db

    init_db()
    insert("series", id=1, key="point hebdo", label="Point hebdo", followed=False)
    insert("series", id=2, key="comite de pilotage", label="Comité de pilotage", followed=False)
    insert("meeting", id=1, series_id=1, title="Point hebdo", date=date(2026, 11, 30))
    insert("meeting", id=2, series_id=2, title="Comité de pilotage", date=date(2026, 11, 30))
    return home


def place(home: Path, relative: str, content: bytes | None = None) -> str:
    """Écrit un fichier sous le répertoire de données et renvoie son chemin relatif."""
    target = home / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((FIXTURES / AUDIO).read_bytes() if content is None else content)
    return relative


def add_item(
    path: str,
    kind: str = "audio",
    status: str = "pret",
    meeting_id: int | None = 2,
    consent: bool = True,
) -> int:
    return insert(
        "inbox_item",
        path=path,
        sha256=sha256(path.encode()).hexdigest(),
        kind=kind,
        status=status,
        meeting_id=meeting_id,
        consent_confirmed=consent,
        consent_at=NOW if consent else None,
        detected_at=datetime(2026, 11, 30, 18, 0, tzinfo=ZURICH),
    )


def run_agent(transcriber: Any) -> Any:
    """Un passage de l'agent sur une session neuve, validée à la sortie (`specs/01 §3.3`)."""
    from cabinet.agents.transcription import TranscriptionAgent
    from cabinet.db.base import get_session

    with get_session() as session:
        outcome = TranscriptionAgent(transcriber).run(session)
        session.commit()
    return outcome


def same_path(received: Any, expected: Path) -> bool:
    return Path(received).resolve() == expected.resolve()


# --- Critère 1 : audio consenti transcrit -------------------------------------


def test_c1_ready_consented_audio_gives_an_audio_transcript(db: Path) -> None:
    item_id = add_item(place(db, f"entree/{AUDIO}"))
    fake = FakeTranscriber(transcript_data())

    outcome = run_agent(fake)

    assert (outcome.processed, outcome.errors) == (1, 0)
    assert len(fake.calls) == 1
    transcripts = transcript_rows()
    assert len(transcripts) == 1
    transcript = transcripts[0]
    assert transcript["source"] == "audio"
    assert (transcript["meeting_id"], transcript["inbox_item_id"]) == (2, item_id)
    assert transcript["text"].splitlines() == [said for *_, said in FAKE_SEGMENTS]
    assert [segment["speaker"] for segment in transcript["segments"]] == [None, None]
    assert [(s["start_s"], s["end_s"], s["text"]) for s in transcript["segments"]] == [
        (a, b, t) for a, b, _, t in FAKE_SEGMENTS
    ]


def test_c1_transcribed_audio_is_processed_and_archived(db: Path) -> None:
    item_id = add_item(place(db, f"entree/{AUDIO}"))

    run_agent(FakeTranscriber(transcript_data()))

    row = item_row(item_id)
    assert (row["status"], row["path"]) == ("traite", f"{ARCHIVE_DIR}/{AUDIO}")
    assert (db / ARCHIVE_DIR / AUDIO).is_file()
    assert not (db / "entree" / AUDIO).exists()


# --- Critère 2 : pas de consentement, pas de transcription --------------------


def test_c2_audio_without_consent_never_reaches_the_transcriber(db: Path) -> None:
    """Second contrôle du principe 3 (`specs/06 §3.2`, étape 2)."""
    item_id = add_item(place(db, f"entree/{AUDIO}"), consent=False)
    fake = FakeTranscriber(transcript_data())

    outcome = run_agent(fake)

    assert fake.calls == []
    assert (outcome.processed, outcome.errors) == (0, 1)
    row = item_row(item_id)
    assert row["status"] == "consentement_requis"
    assert row["path"] == f"entree/{AUDIO}"
    assert (db / "entree" / AUDIO).is_file()
    assert transcript_rows() == []


def test_c2_unconsented_audio_does_not_stop_the_others(db: Path) -> None:
    refused = add_item(place(db, "entree/2026-11-30_sans-accord.m4a"), consent=False)
    accepted = add_item(place(db, f"entree/{AUDIO}"))
    fake = FakeTranscriber(transcript_data())

    outcome = run_agent(fake)

    assert (outcome.processed, outcome.errors) == (1, 1)
    assert len(fake.calls) == 1
    assert same_path(fake.calls[0][0], db / "entree" / AUDIO)
    assert item_row(refused)["status"] == "consentement_requis"
    assert item_row(accepted)["status"] == "traite"


# --- Critère 3 : bibliothèque absente -----------------------------------------


def test_c3_missing_library_puts_item_in_error_and_pipeline_goes_on(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cabinet.adapters.mlx_whisper_transcriber import MlxWhisperTranscriber
    from cabinet.agents.base import AgentOutcome
    from cabinet.agents.pipeline import Pipeline
    from cabinet.agents.transcription import TranscriptionAgent
    from cabinet.db.base import get_session

    library_missing(monkeypatch)
    first = add_item(place(db, "entree/2026-11-30_premier.m4a"))
    second = add_item(place(db, f"entree/{AUDIO}"))
    follower = FollowerAgent()
    agents = [TranscriptionAgent(MlxWhisperTranscriber(WHISPER_MODEL)), follower]

    result = Pipeline(agents, db / "data" / "pipeline.lock").run(get_session)

    outcome = result["transcription"]
    assert isinstance(outcome, AgentOutcome)
    assert (outcome.processed, outcome.errors) == (0, 2)
    for item_id in (first, second):
        row = item_row(item_id)
        assert row["status"] == "erreur"
        assert NOT_INSTALLED in row["error"]
        assert INSTALL_HINT in row["error"]
    assert transcript_rows() == []
    assert follower.ran
    assert query("SELECT agent, status FROM agent_run ORDER BY id") == [
        {"agent": "transcription", "status": "ok"},
        {"agent": "minutes", "status": "ok"},
    ]


def test_c3_adapter_raises_transcriber_unavailable_with_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cabinet.adapters.mlx_whisper_transcriber import (
        MlxWhisperTranscriber,
        TranscriberUnavailable,
    )

    library_missing(monkeypatch)
    audio = tmp_path / AUDIO
    audio.write_bytes((FIXTURES / AUDIO).read_bytes())

    with pytest.raises(TranscriberUnavailable) as error:
        MlxWhisperTranscriber(WHISPER_MODEL).transcribe(audio)
    assert NOT_INSTALLED in str(error.value)
    assert INSTALL_HINT in str(error.value)


# --- Critère 4 : import à la demande ------------------------------------------


IMPORT_PROBE = textwrap.dedent(
    """
    import sys

    import cabinet.web.app

    print("web.app", "mlx_whisper" in sys.modules)

    import cabinet.adapters.mlx_whisper_transcriber
    import cabinet.agents.pipeline
    import cabinet.agents.transcription

    print("modules", "mlx_whisper" in sys.modules)

    import mlx_whisper

    print("probe", getattr(mlx_whisper, "REVIEWER_PROBE", False))
    """
)


def test_c4_importing_web_app_does_not_import_mlx_whisper(home: Path, tmp_path: Path) -> None:
    """Contrôle « Transcription importée à la demande » de `specs/09 §5`.

    Processus neuf, faux paquet `mlx_whisper` en tête du chemin : s'il était
    importé au chargement d'un module, il apparaîtrait dans `sys.modules`.
    """
    probe_dir = tmp_path / "probe"
    (probe_dir / "mlx_whisper").mkdir(parents=True)
    (probe_dir / "mlx_whisper" / "__init__.py").write_text(
        '"""Faux paquet du relecteur."""\nREVIEWER_PROBE = True\n', encoding="utf-8"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(probe_dir), env.get("PYTHONPATH", "")) if part
    )
    env["CABINET_HOME"] = str(home)
    env.pop("CABINET_ALLOW_REMOTE_LLM", None)

    completed = subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        "web.app False",
        "modules False",
        "probe True",
    ]


# --- Critère 5 : langue `fr` ----------------------------------------------------


def test_c5_language_fr_is_passed_to_the_transcriber(db: Path) -> None:
    add_item(place(db, f"entree/{AUDIO}"))
    fake = FakeTranscriber(transcript_data())

    run_agent(fake)

    assert len(fake.calls) == 1
    audio_path, language = fake.calls[0]
    assert language == "fr"
    assert same_path(audio_path, db / "entree" / AUDIO)


def test_c5_agent_passes_fr_explicitly(db: Path) -> None:
    """`specs/06 §3.2`, étape 3 : `transcriber.transcribe(chemin, "fr")`."""
    add_item(place(db, f"entree/{AUDIO}"))
    fake = ExplicitLanguageTranscriber(transcript_data())

    run_agent(fake)

    assert [language for _, language in fake.calls] == ["fr"]


def test_c5_adapter_passes_model_and_language_to_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Appel exact de `specs/06 §3.1`, étape 2, puis construction des segments (étapes 3 et 4)."""
    from cabinet.adapters.mlx_whisper_transcriber import MlxWhisperTranscriber
    from cabinet.ports.transcriber import TranscriptData

    calls = fake_library(
        monkeypatch,
        {
            "text": " Bonjour à tous. Le comité valide le budget 2027.",
            "segments": [
                {"id": 0, "seek": 0, "start": 0.0, "end": 2.5, "text": " Bonjour à tous. "},
                {"id": 1, "seek": 0, "start": 2.5, "end": 3.0, "text": "   "},
                {
                    "id": 2,
                    "seek": 0,
                    "start": 3.0,
                    "end": 6.0,
                    "text": " Le comité valide le budget 2027.",
                },
            ],
            "language": "fr",
        },
    )
    audio = tmp_path / AUDIO
    audio.write_bytes((FIXTURES / AUDIO).read_bytes())

    data = MlxWhisperTranscriber("modele-synthetique").transcribe(audio, language="fr")

    assert calls == [((str(audio),), {"path_or_hf_repo": "modele-synthetique", "language": "fr"})]
    assert isinstance(data, TranscriptData)
    assert [(s.start_s, s.end_s, s.speaker, s.text) for s in data.segments] == [
        (0.0, 2.5, None, "Bonjour à tous."),
        (3.0, 6.0, None, "Le comité valide le budget 2027."),
    ]
    assert data.text.splitlines() == ["Bonjour à tous.", "Le comité valide le budget 2027."]


# --- Critère 6 : purge des audios de la réunion -------------------------------


def test_c6_purge_audio_deletes_only_the_processed_audio_of_the_meeting(db: Path) -> None:
    from cabinet.agents.transcription import purge_audio
    from cabinet.db.base import get_session

    vtt = (TEAMS_FIXTURES / "2026-11-30_sans-orateur.vtt").read_bytes()
    deleted = {
        add_item(place(db, f"{ARCHIVE_DIR}/a.m4a"), status="traite"),
        add_item(place(db, "traite/2026-11/b.wav"), status="traite"),
    }
    kept = {
        add_item(place(db, f"{ARCHIVE_DIR}/notes.md", b"notes"), kind="notes", status="traite"),
        add_item(
            place(db, f"{ARCHIVE_DIR}/point.vtt", vtt), kind="teams_transcript", status="traite"
        ),
        add_item(place(db, "entree/d.mp3")),  # audio de la réunion, pas encore traité
        add_item(place(db, "entree/g.m4a"), status="erreur"),
        add_item(place(db, f"{ARCHIVE_DIR}/e.m4a"), status="traite", meeting_id=1),
    }
    gone = add_item(f"{ARCHIVE_DIR}/deja-supprime.m4a", status="traite")
    paths_before = {item_id: item_row(item_id)["path"] for item_id in deleted | kept | {gone}}

    with get_session() as session:
        count = purge_audio(session, 2)
        session.commit()

    assert count == 2
    for item_id in deleted:
        assert not (db / paths_before[item_id]).exists()
    for item_id in kept:
        assert (db / paths_before[item_id]).is_file()
    for item_id, path in paths_before.items():
        row = item_row(item_id)
        assert row["path"] == path, "la ligne inbox_item reste en base avec son chemin"


def test_c6_purge_audio_of_a_meeting_without_audio_returns_zero(db: Path) -> None:
    from cabinet.agents.transcription import purge_audio
    from cabinet.db.base import get_session

    other = add_item(place(db, f"{ARCHIVE_DIR}/e.m4a"), status="traite", meeting_id=1)

    with get_session() as session:
        assert purge_audio(session, 2) == 0
    assert (db / item_row(other)["path"]).is_file()


# --- Contrat de l'agent -------------------------------------------------------


def test_contract_transcriber_unavailable_from_the_port_goes_to_error(db: Path) -> None:
    """`specs/06 §3.2`, étape 5 : message de l'exception, agent qui continue."""
    from cabinet.adapters.mlx_whisper_transcriber import TranscriberUnavailable

    message = f"{NOT_INSTALLED} Lance : {INSTALL_HINT}"
    item_id = add_item(place(db, f"entree/{AUDIO}"))
    fake = FakeTranscriber(TranscriberUnavailable(message))

    outcome = run_agent(fake)

    assert (outcome.processed, outcome.errors) == (0, 1)
    row = item_row(item_id)
    assert (row["status"], row["error"]) == ("erreur", message)
    assert (db / "entree" / AUDIO).is_file()


def test_contract_other_failure_gives_transcription_impossible(db: Path) -> None:
    """`specs/06 §3.2`, étape 6 : « Transcription impossible : {détail}. »"""
    failed = add_item(place(db, "entree/2026-11-30_abime.m4a"))
    fake = FakeTranscriber(RuntimeError("flux audio illisible"))

    outcome = run_agent(fake)

    assert (outcome.processed, outcome.errors) == (0, 1)
    row = item_row(failed)
    assert row["status"] == "erreur"
    assert row["error"].startswith("Transcription impossible : ")
    assert "flux audio illisible" in row["error"]
    assert transcript_rows() == []


def test_contract_only_ready_audio_with_a_meeting_is_transcribed(db: Path) -> None:
    """Sélection de `specs/06 §3.2`, étape 1."""
    waiting = add_item(place(db, "entree/attente.m4a"), status="consentement_requis", consent=False)
    unattached = add_item(place(db, "entree/sans-reunion.m4a"), meeting_id=None)
    notes = add_item(place(db, "entree/notes.md", b"Notes du comite."), kind="notes", consent=False)
    fake = FakeTranscriber(transcript_data())

    outcome = run_agent(fake)

    assert fake.calls == []
    assert (outcome.processed, outcome.errors) == (0, 0)
    assert item_row(waiting)["status"] == "consentement_requis"
    assert item_row(unattached)["status"] == "pret"
    assert item_row(notes)["status"] == "pret"
    assert transcript_rows() == []


def test_contract_second_run_transcribes_nothing_again(db: Path) -> None:
    add_item(place(db, f"entree/{AUDIO}"))
    fake = FakeTranscriber(transcript_data())

    run_agent(fake)
    second = run_agent(fake)

    assert len(fake.calls) == 1
    assert (second.processed, second.errors) == (0, 0)
    assert len(transcript_rows()) == 1


def test_contract_agent_name_is_transcription() -> None:
    from cabinet.agents.transcription import TranscriptionAgent

    assert TranscriptionAgent(FakeTranscriber(RuntimeError("inutile"))).name == "transcription"


def test_contract_build_pipeline_runs_transcription_right_after_transcripts(home: Path) -> None:
    """`specs/05 §3.3` (répertoire vide : aucun audio, la bibliothèque n'est pas appelée)."""
    from cabinet.agents.base import AgentOutcome
    from cabinet.agents.pipeline import build_pipeline
    from cabinet.config import load_settings
    from cabinet.db.base import get_session, init_db

    init_db()
    result = build_pipeline(load_settings()).run(get_session)

    names = list(result)
    assert "transcription" in names
    assert names.index("transcription") == names.index("transcripts") + 1
    assert isinstance(result["transcription"], AgentOutcome)


def test_contract_pyproject_declares_the_audio_extra() -> None:
    """`specs/03 §6` : `audio = ["mlx-whisper>=0.4.3"]`, hors des dépendances principales."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    audio = project.get("optional-dependencies", {}).get("audio", [])
    assert [dep.replace(" ", "") for dep in audio] == ["mlx-whisper>=0.4.3"]
    main = [dep.replace(" ", "") for dep in project["dependencies"]]
    assert not any(dep.startswith("mlx-whisper") for dep in main)


def test_contract_scenario_s4_consent(db: Path) -> None:
    """Scénario S4 de `specs/09 §4` : case refusée par 400, puis acceptée, puis transcription."""
    from cabinet.web.app import create_app

    item_id = add_item(place(db, f"entree/{AUDIO}"), status="consentement_requis", consent=False)
    forced = add_item(place(db, "entree/2026-11-30_force.m4a"), consent=False)
    before = item_row(item_id)

    with TestClient(create_app()) as client:
        refused = client.post(
            f"/entree/{item_id}/rattacher", data={"meeting_id": "2"}, follow_redirects=False
        )
        assert refused.status_code == 400
        assert item_row(item_id) == before
        accepted = client.post(
            f"/entree/{item_id}/rattacher",
            data={"meeting_id": "2", "consentement": "on"},
            follow_redirects=False,
        )
        assert accepted.status_code == 303

    row = item_row(item_id)
    assert (row["status"], row["meeting_id"], bool(row["consent_confirmed"])) == ("pret", 2, True)
    assert row["consent_at"] is not None

    fake = FakeTranscriber(transcript_data())
    outcome = run_agent(fake)

    assert (outcome.processed, outcome.errors) == (1, 1)
    assert len(fake.calls) == 1
    assert same_path(fake.calls[0][0], db / "entree" / AUDIO)
    assert item_row(item_id)["status"] == "traite"
    assert item_row(forced)["status"] == "consentement_requis"
    assert [row["inbox_item_id"] for row in transcript_rows()] == [item_id]
