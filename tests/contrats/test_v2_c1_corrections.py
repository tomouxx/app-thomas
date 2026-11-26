"""Tests de contrat du ticket V2-C1, « Corrections de la vague 1 ».

Écrits par le relecteur avant la vague 2. Sources : `revues/vague-1.md`
(réserves R1, R3, R6, R7, R8, R9), `specs/01 §2`, `§3.5` et `§5`,
`specs/03 §5.3`, `specs/05 §3.2`, `§5.2` et `§6.2` (décisions Q1 et Q2 du
26.11.2026) et `specs/08 §1.4`.

Les critères 1 à 5 lisent le code par son arbre syntaxique. Les autres passent
par les interfaces de `specs/`. Le critère 11 (contrats des vagues 0 et 1
toujours verts) est vérifié par ces contrats eux-mêmes.
"""

from __future__ import annotations

import ast
import os
import re
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "cabinet"
UNIT_TESTS = ROOT / "tests" / "unit"
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=ZoneInfo("Europe/Zurich"))

# Mots français relevés dans le code de la vague 1, et quelques voisins courants.
# Un identifiant est découpé sur `_` et sur les majuscules, et aucun morceau ne
# doit figurer ici. Les états de `specs/02` (`pret`, `erreur`…) n'y sont pas.
FRENCH_WORDS = frozenset(
    {
        "adresse", "adresses", "annule", "annulee", "apres", "aujourdhui", "avant", "avec",
        "bouton", "brouillons", "calendrier", "caractere", "caracteres", "champ", "champs",
        "chemin", "chemins", "cle", "cles", "compteur", "compteurs", "connexion", "connu",
        "connus", "contenu", "corps", "courant", "cree", "crees", "debut", "destinataires",
        "donnees", "dossier", "dossiers", "erreurs", "essai", "evenement", "evenements",
        "executions", "faux", "fenetre", "fichier", "fichiers", "fin", "gabarits", "heure", "jour",
        "jours", "ligne", "lignes", "lire", "liste", "modele", "moteur", "nom",
        "nombre", "noms", "nouveau", "nouvelle", "objet", "octets", "precedent", "prefixe",
        "racine", "reglage", "regle", "reponse", "reponses", "resultat", "reunion", "reunions",
        "routeur", "sans", "semaine", "serie", "serveur", "statut", "suivant", "tentatives",
        "texte", "titre", "titres", "trouve", "valeur", "valeurs", "vide", "vrai", "vus", "zone"
    }
)
# Noms imposés par la spécification, qui contiennent un mot de la liste.
SPEC_NAMES = frozenset({"tolerance_jours"})


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Les délais de copie et la fenêtre d'agenda suivent `cabinet.clock` (specs/09 §3.3)."""
    monkeypatch.setattr("cabinet.clock.now", lambda: NOW)
    return NOW


# --- Aides ------------------------------------------------------------------


def python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def defined_names(tree: ast.Module, skip_tests: bool) -> Iterator[tuple[str, int]]:
    """Noms définis dans un module : fonctions, classes, paramètres, cibles d'affectation."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not (skip_tests and node.name.startswith("test_")):
                yield node.name, node.lineno
        elif isinstance(node, ast.arg):
            yield node.arg, node.lineno
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node.id, node.lineno
        elif isinstance(node, ast.ExceptHandler) and node.name:
            yield node.name, node.lineno


def french_parts(name: str) -> list[str]:
    parts = re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", name)
    return [p for p in (part.lower() for part in parts) if p in FRENCH_WORDS]


def enum_members(tree: ast.Module) -> set[str]:
    """Membres des énumérations de `db/models.py`, qui reprennent les états de specs/02."""
    members: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            isinstance(base, ast.Name) and base.id.endswith("Enum") for base in node.bases
        ):
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    members.update(t.id for t in statement.targets if isinstance(t, ast.Name))
    return members


def french_identifiers(root: Path, skip_tests: bool) -> list[str]:
    found = []
    for path in python_files(root):
        tree = parse(path)
        allowed = SPEC_NAMES | enum_members(tree)
        for name, line in defined_names(tree, skip_tests):
            if name not in allowed and french_parts(name):
                found.append(f"{path.relative_to(ROOT)}:{line} {name}")
    return sorted(set(found))


@contextmanager
def browser() -> Iterator[TestClient]:
    from cabinet.web.app import create_app

    with TestClient(create_app()) as client:
        yield client


class FieldParser(HTMLParser):
    """Relève les champs de saisie et les libellés d'une page."""

    FIELD_TYPES = {"text", "date", "email"}

    def __init__(self) -> None:
        super().__init__()
        self.fields: list[dict[str, Any]] = []
        self.label_targets: set[str] = set()
        self.open_labels = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "label":
            self.open_labels += 1
            if values.get("for"):
                self.label_targets.add(str(values["for"]))
            return
        is_input = tag == "input" and (values.get("type") or "text") in self.FIELD_TYPES
        if is_input or tag in {"textarea", "select"}:
            self.fields.append(
                {"tag": tag, "name": values.get("name"), "id": values.get("id"),
                 "wrapped": self.open_labels > 0}
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self.open_labels:
            self.open_labels -= 1

    def unlabelled(self) -> list[dict[str, Any]]:
        return [
            f for f in self.fields
            if not f["wrapped"] and (f["id"] is None or f["id"] not in self.label_targets)
        ]


def make_old(path: Path) -> None:
    """Recule la date de modification pour que `scan` ne prenne pas le fichier pour une copie."""
    old = (NOW - timedelta(hours=1)).timestamp()
    os.utime(path, (old, old))


def ingest() -> Any:
    from cabinet.adapters.folder_inbox import FolderInbox
    from cabinet.agents.ingestion import IngestionAgent
    from cabinet.config import paths
    from cabinet.db.base import get_session, init_db

    init_db()
    with get_session() as session:
        outcome = IngestionAgent(FolderInbox(paths().inbox)).run(session)
        session.commit()
    return outcome


def inbox_paths() -> list[str]:
    from cabinet.db.base import get_engine
    from sqlalchemy import text

    with get_engine().connect() as connection:
        rows = connection.execute(text("SELECT path FROM inbox_item ORDER BY path"))
        return [row[0] for row in rows]


# --- Critère 1 : routes synchrones ------------------------------------------


def test_c1_no_async_route() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{node.lineno} {node.name}"
        for path in python_files(SRC / "web" / "routes")
        for node in ast.walk(parse(path))
        if isinstance(node, ast.AsyncFunctionDef)
    ]
    assert offenders == [], "routes en async def (specs/01 §3.5)"


# --- Critère 2 : longueur des lignes ----------------------------------------


def test_c2_ruff_selects_e501() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "E501" in config["tool"]["ruff"]["lint"]["select"]
    assert config["tool"]["ruff"]["line-length"] == 100


def test_c2_no_line_over_100_characters() -> None:
    long_lines = [
        f"{path.relative_to(ROOT)}:{number}"
        for root in (SRC, UNIT_TESTS)
        for path in python_files(root)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if len(line) > 100
    ]
    assert long_lines == []


# --- Critère 3 : paquet services ---------------------------------------------


def test_c3_services_is_a_package() -> None:
    assert (SRC / "services" / "__init__.py").is_file()


# --- Critère 4 : identifiants en anglais --------------------------------------


def test_c4_no_french_identifier_in_src() -> None:
    assert french_identifiers(SRC, skip_tests=False) == []


def test_c4_no_french_identifier_in_unit_tests() -> None:
    assert french_identifiers(UNIT_TESTS, skip_tests=True) == []


def test_c4_no_french_identifier_in_app_js() -> None:
    script = (SRC / "web" / "static" / "app.js").read_text(encoding="utf-8")
    declared = re.findall(r"\b(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)", script)
    assert [name for name in declared if french_parts(name)] == []


def test_c4_word_list_catches_known_names() -> None:
    """La liste attrape bien les noms relevés par la revue de la vague 1."""
    for name in ("_lire_calendrier", "debut_fenetre", "_upsert_reunion", "RACINE", "FauxServeur"):
        assert french_parts(name), name
    for name in ("tolerance_days", "window_start", "_read_calendar", "FakeServer", "PRET"):
        assert not french_parts(name), name


# --- Critère 5 : annotations --------------------------------------------------


def test_c5_every_function_in_src_is_annotated() -> None:
    missing = []
    for path in python_files(SRC):
        for node in ast.walk(parse(path)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            params += [p for p in (args.vararg, args.kwarg) if p is not None]
            bare = [p.arg for p in params if p.annotation is None and p.arg not in {"self", "cls"}]
            if node.returns is None or bare:
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name} {bare}")
    assert missing == []


# --- Critère 6 : libellés des champs -------------------------------------------


def test_c6_inbox_fields_have_labels(environnement_isole: Path) -> None:
    from cabinet.db.base import init_db

    inbox = environnement_isole / "entree"
    inbox.mkdir(parents=True)
    for name, content in (("2026-11-23_point-hebdo.vtt", b"WEBVTT\n"), ("revue.m4a", b"\0" * 64)):
        (inbox / name).write_bytes(content)
        make_old(inbox / name)
    init_db()
    ingest()
    with browser() as client:
        page = client.get("/entree")
    assert page.status_code == 200
    parser = FieldParser()
    parser.feed(page.text)
    assert parser.fields, "la page Entrée doit montrer le formulaire de rattachement"
    assert parser.unlabelled() == []


@pytest.mark.parametrize("state", ["brouillon", "valide"])
def test_c6_draft_fields_have_labels(state: str) -> None:
    from cabinet.db.base import get_session, init_db
    from cabinet.services.drafts import create_draft, set_status

    init_db()
    with get_session() as session:
        draft = create_draft(
            session, "envoi_cr", ["alice.martin@exemple.test"], "Compte rendu", "Bonjour,\n"
        )
        session.commit()
        if state == "valide":
            set_status(session, draft.id, "valide")
            session.commit()
    with browser() as client:
        page = client.get("/brouillons")
    assert page.status_code == 200
    parser = FieldParser()
    parser.feed(page.text)
    assert parser.fields, "la page Brouillons doit montrer des champs à cet état"
    assert parser.unlabelled() == []


# --- Critère 7 : cabinet run et une configuration invalide ---------------------


def test_c7_run_with_invalid_settings_exits_1_without_traceback(environnement_isole: Path) -> None:
    from cabinet.cli import app
    from typer.testing import CliRunner

    config = environnement_isole / "config"
    config.mkdir(parents=True)
    (config / "reglages.toml").write_text("[llm]\ncle_inconnue = 1\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["run"])
    output = result.output + (result.stderr if result.stderr_bytes is not None else "")
    assert result.exit_code == 1
    assert "cle_inconnue" in output
    assert "Traceback" not in output
    assert result.exception is None or isinstance(result.exception, SystemExit)


# --- Garde : répertoire de données derrière un lien symbolique ------------------


def test_contract_ingestion_through_symlinked_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_home = tmp_path / "real-home"
    (real_home / "entree").mkdir(parents=True)
    link = tmp_path / "link-home"
    link.symlink_to(real_home, target_is_directory=True)
    """Déjà tenu à la fin de la vague 1. Le test garde le comportement (`specs/02 §2.2`)."""
    monkeypatch.setenv("CABINET_HOME", str(link))
    note = real_home / "entree" / "notes.md"
    note.write_text("# Notes\n", encoding="utf-8")
    make_old(note)
    outcome = ingest()
    assert outcome.errors == 0
    assert outcome.processed == 1
    assert inbox_paths() == ["entree/notes.md"]


# --- Critère 8 : fichier illisible (décision Q2) -------------------------------


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root lit tout")
def test_c8_unreadable_file_is_skipped_then_taken(environnement_isole: Path) -> None:
    from cabinet.adapters.folder_inbox import FolderInbox
    from cabinet.config import paths

    inbox = environnement_isole / "entree"
    inbox.mkdir(parents=True)
    locked = inbox / "2026-11-23_point-hebdo.vtt"
    locked.write_text("WEBVTT\n", encoding="utf-8")
    make_old(locked)
    locked.chmod(0)
    try:
        assert FolderInbox(paths().inbox).scan() == []
        first = ingest()
        assert first.processed == 0
        assert inbox_paths() == []
    finally:
        locked.chmod(0o644)
    make_old(locked)
    second = ingest()
    assert second.processed == 1
    assert inbox_paths() == ["entree/2026-11-23_point-hebdo.vtt"]


# --- Critère 9 : une réunion garde sa série (décision Q1) ---------------------


ICS_TEMPLATE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Contrat V2-C1//FR
BEGIN:VEVENT
UID:serie-q1@exemple.test
DTSTAMP:20261120T080000Z
DTSTART;TZID=Europe/Zurich:20261125T100000
DTEND;TZID=Europe/Zurich:20261125T110000
SUMMARY:{title}
END:VEVENT
END:VCALENDAR
"""


def run_agenda(home: Path, title: str) -> None:
    from cabinet.adapters.ics_calendar import IcsCalendarSource
    from cabinet.agents.agenda import AgendaAgent
    from cabinet.db.base import get_session, init_db

    ics = home / "config" / "agenda.ics"
    ics.parent.mkdir(parents=True, exist_ok=True)
    ics.write_text(ICS_TEMPLATE.format(title=title), encoding="utf-8")
    init_db()
    with get_session() as session:
        AgendaAgent(IcsCalendarSource(ics)).run(session)
        session.commit()


def meeting_and_series() -> list[tuple[str, str]]:
    from cabinet.db.base import get_engine
    from sqlalchemy import text

    query = text(
        "SELECT meeting.title, series.key FROM meeting "
        "JOIN series ON series.id = meeting.series_id ORDER BY meeting.id"
    )
    with get_engine().connect() as connection:
        return [(row[0], row[1]) for row in connection.execute(query)]


def test_c9_renamed_meeting_keeps_its_series(environnement_isole: Path) -> None:
    run_agenda(environnement_isole, "Point hebdo")
    assert meeting_and_series() == [("Point hebdo", "point hebdo")]
    run_agenda(environnement_isole, "Revue de projet")
    assert meeting_and_series() == [("Revue de projet", "point hebdo")]


# --- Critère 10 : verrou illisible ---------------------------------------------


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root lit tout")
def test_c10_unreadable_lock_is_held(environnement_isole: Path) -> None:
    from cabinet.agents.pipeline import Pipeline, PipelineBusy
    from cabinet.db.base import get_session, init_db

    lock = environnement_isole / "data" / "pipeline.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("1", encoding="utf-8")
    lock.chmod(0)
    init_db()
    try:
        with pytest.raises(PipelineBusy):
            Pipeline([], lock).run(get_session)
        assert lock.exists()
    finally:
        lock.chmod(0o644)
