"""Tests de contrat du ticket V2-06, « Page réunion, rendu et validation du compte rendu ».

Écrits par le relecteur avant la vague. Un test `test_cN_*` par critère
d'acceptation numéroté, des tests `test_contract_*` pour les points du contrat
et des précisions du ticket.

Sources : `specs/06 §7` (gabarit exact du rendu), `specs/06 §8` (corrections,
`validate_minutes`, `ValidationBlocked`), `specs/06 §3.3` (`purge_audio`),
`specs/06 §4.4` (régénération), `specs/08 §4.1` à `§4.3` (pages et routes),
`specs/08 §7.1` (`create_draft`), `specs/01 §6` (messages et codes HTTP),
`specs/03 §3` (réglage `transcription.delete_audio_after_validation`) et
`specs/09 §3` (doublure `FakeLLM`, date fixée, client web).

Le jeu `fixtures/v2_06/reunion.json` est écrit dans la base par les noms de
tables et de colonnes de `specs/02 §2`, sur des tables relues par réflexion.
Il ne dépend d'aucun code des tickets V2-01 à V2-05 : le compte rendu, ses
décisions et ses actions sont posés tels qu'un passage les aurait laissés.
Trois tests passent par `MinutesAgent` (V2-04), dont V2-06 dépend : le ticket
demande que l'agent remplisse `content_md` et que la page régénère.

Les objets donnés à `render_minutes` sont ceux du modèle (`cabinet.db.models`),
relus dans une session. La signature de `specs/06 §7` ne fixe pas leur type,
et ce sont ceux que l'agent manipule.

La route de régénération construit son modèle à partir des réglages. Le seul
adaptateur du port `LLM` est `LocalLLM` (`specs/01 §3.4`) : les tests qui la
font aboutir remplacent `LocalLLM.complete` par la doublure, sans réseau.
"""

from __future__ import annotations

import json
import re
import socket
from collections.abc import Iterator
from datetime import UTC, date, datetime
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, select, update
from sqlalchemy.sql import sqltypes

FIXTURES = Path(__file__).parent / "fixtures" / "v2_06"
ZURICH = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=ZURICH)
LOCAL_ORIGIN = {"Origin": "http://127.0.0.1:8765"}

ATTENDEES = [
    "alice.martin@exemple.test",
    "bruno.keller@exemple.test",
    "chloe.favre@exemple.test",
]
SECTIONS = [
    "## Participants",
    "## Résumé",
    "## Décisions",
    "## Actions",
    "## Points ouverts",
    "## Prochaine séance",
]
BLOCKED_TWO = "2 élément(s) restent à vérifier. Confirme-les ou supprime-les avant de valider."
ALREADY_VALIDATED = "Ce compte rendu est déjà validé."
EXPORT_NAME = "2026-11-20_cr_1.md"
DRAFT_SUBJECT = "Compte rendu : Comité de pilotage, 20.11.2026"

# Rendu attendu du compte rendu 1 du jeu, recopié du gabarit de `specs/06 §7`.
EXPECTED_RENDER = """\
# Compte rendu : Comité de pilotage

Date : 20.11.2026

## Participants

- alice.martin@exemple.test
- bruno.keller@exemple.test
- chloe.favre@exemple.test

## Résumé

Le comité a validé le budget de la phase deux et confié le planning à Bruno.

## Décisions

- Budget de la phase deux validé
- Le prestataire actuel est reconduit (à vérifier)

## Actions

| Action | Responsable | Échéance |
| --- | --- | --- |
| Préparer le planning détaillé | Bruno Keller | 30.11.2026 |
| Envoyer la convention signée (à vérifier) | non précisé | non précisée |

## Points ouverts

- Recrutement d'un second développeur

## Prochaine séance

Jeudi 3 décembre à 14 h
"""

# Rendu attendu d'un compte rendu hors agenda, sans aucun élément (`specs/06 §7`, « Si vide »).
EXPECTED_EMPTY_RENDER = """\
# Compte rendu : Point équipe

Date : 18.11.2026

## Participants

Non renseignés.

## Résumé

Point rapide sans décision.

## Décisions

Aucune décision relevée.

## Actions

Aucune action relevée.

## Points ouverts

Aucun point ouvert.

## Prochaine séance

Non précisée.
"""


# --- Doublure du modèle (specs/09 §3.2) ---------------------------------------


class FakeLLM:
    """Rejoue une liste de réponses. La dernière est répétée si les appels continuent."""

    model = "fake"

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: Any, schema: Any = None, images: Any = None) -> Any:
        from cabinet.llm.types import LLMResult
        from pydantic import BaseModel

        self.calls.append({"messages": messages, "schema": schema, "images": images})
        response = self.responses[min(len(self.calls), len(self.responses)) - 1]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, BaseModel):
            return LLMResult(
                text=response.model_dump_json(),
                parsed=response,
                prompt_tokens=10,
                completion_tokens=5,
            )
        return LLMResult(text=str(response))


def replace_local_llm(monkeypatch: pytest.MonkeyPatch, fake: FakeLLM) -> None:
    """Toute instance de `LocalLLM` répond par la doublure, sans connexion."""
    from cabinet.llm.client import LocalLLM

    def complete(self: Any, messages: Any, schema: Any = None, images: Any = None) -> Any:
        return fake.complete(messages, schema, images)

    monkeypatch.setattr(LocalLLM, "complete", complete)


# --- Environnement -----------------------------------------------------------


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Mardi 24.11.2026, 10:00 à Zurich (specs/09 §3.3)."""
    monkeypatch.setattr("cabinet.clock.now", lambda: NOW)
    return NOW


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucun test n'ouvre de connexion (specs/09 §1)."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("tentative de connexion réseau")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# --- Base de données ---------------------------------------------------------

TABLE_ORDER = (
    "series",
    "event",
    "meeting",
    "inbox_item",
    "transcript",
    "minutes",
    "decision",
    "action_item",
    "prep_note",
    "draft",
)

DEFAULTS: dict[str, dict[str, Any]] = {
    "series": {"followed": False, "client": None},
    "event": {
        "all_day": False,
        "location": None,
        "organizer": None,
        "attendees": [],
        "cancelled": False,
    },
    "meeting": {"event_id": None},
    "inbox_item": {
        "name_date": None,
        "meeting_id": None,
        "consent_confirmed": False,
        "consent_at": None,
        "error": None,
        "detected_at": "2026-11-20T09:00:00+01:00",
    },
    "transcript": {"segments": []},
    "minutes": {
        "content_md": "",
        "open_points": [],
        "next_meeting": None,
        "status": "brouillon",
        "created_at": "2026-11-20T16:00:00+01:00",
    },
    "decision": {"verified": False},
    "action_item": {"owner": None, "due_date": None, "verified": False, "status": "ouvert"},
    "prep_note": {"status": "brouillon", "created_at": "2026-11-21T08:00:00+01:00"},
}


def reflect(name: str) -> Table:
    from cabinet.db.base import get_engine

    return Table(name, MetaData(), autoload_with=get_engine())


def to_stored(column: Any, value: Any) -> Any:
    """Valeur du jeu vers la forme stockée (`specs/02 §1.1`)."""
    if value is None:
        return None
    if isinstance(column.type, sqltypes.DateTime):
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        assert value.tzinfo is not None, "une date et heure du jeu doit porter son fuseau"
        return value.astimezone(UTC).replace(tzinfo=None)
    if isinstance(column.type, sqltypes.Date) and isinstance(value, str):
        return date.fromisoformat(value)
    return value


def insert_rows(dataset: dict[str, Any]) -> None:
    """Écrit les lignes du jeu, table par table, dans l'ordre des clés étrangères."""
    from cabinet.db.base import get_engine, init_db

    init_db()
    with get_engine().begin() as connection:
        for name in TABLE_ORDER:
            target = reflect(name)
            for raw in dataset.get(name, []):
                row = {**DEFAULTS.get(name, {}), **raw}
                if name == "inbox_item":
                    row.setdefault("sha256", sha256(row["path"].encode()).hexdigest())
                if name == "event":
                    row.setdefault("end", row["start"])
                values = {key: to_stored(target.c[key], val) for key, val in row.items()}
                connection.execute(target.insert().values(**values))


def load_dataset() -> dict[str, Any]:
    return json.loads((FIXTURES / "reunion.json").read_text(encoding="utf-8"))


def fetch_rows(name: str, **filters: Any) -> list[dict[str, Any]]:
    """Relit une table dans une connexion neuve, par `id` croissant."""
    from cabinet.db.base import get_engine

    target = reflect(name)
    query = select(target).order_by(target.c.id)
    for key, value in filters.items():
        query = query.where(target.c[key] == value)
    with get_engine().connect() as connection:
        return [dict(row) for row in connection.execute(query).mappings()]


def fetch_row(name: str, ident: int) -> dict[str, Any] | None:
    rows = fetch_rows(name, id=ident)
    return rows[0] if rows else None


def set_columns(name: str, values: dict[str, Any], **filters: Any) -> None:
    from cabinet.db.base import get_engine

    target = reflect(name)
    query = update(target).values(**values)
    for key, value in filters.items():
        query = query.where(target.c[key] == value)
    with get_engine().begin() as connection:
        connection.execute(query)


def as_list(value: Any) -> list[Any]:
    """Une colonne JSON relue par réflexion peut revenir en texte."""
    return json.loads(value) if isinstance(value, str) else list(value)


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def write_settings(home: Path, content: str) -> None:
    folder = home / "config"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "reglages.toml").write_text(content, encoding="utf-8")


def create_files(home: Path, dataset: dict[str, Any]) -> None:
    """Crée sur le disque le fichier de chaque élément d'entrée du jeu."""
    for item in dataset["inbox_item"]:
        target = home / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"contenu synthetique")


def snapshot(home: Path) -> dict[str, Any]:
    """État complet de ce qu'une validation peut modifier."""
    folder = home / "brouillons"
    files = sorted(p.name for p in folder.iterdir()) if folder.exists() else []
    return {
        **{name: fetch_rows(name) for name in ("minutes", "decision", "action_item", "draft")},
        "files": files,
    }


@pytest.fixture
def dataset() -> dict[str, Any]:
    data = load_dataset()
    insert_rows(data)
    return data


@pytest.fixture
def ready(dataset: dict[str, Any]) -> dict[str, Any]:
    """Le jeu, avec tous les éléments vérifiés : le compte rendu est validable."""
    set_columns("decision", {"verified": True})
    set_columns("action_item", {"verified": True})
    return dataset


@pytest.fixture
def validated(ready: dict[str, Any]) -> dict[str, Any]:
    """Le jeu, avec le compte rendu 1 déjà à l'état `valide`."""
    set_columns("minutes", {"status": "valide"}, id=1)
    return ready


# --- Rendu -------------------------------------------------------------------


def render_from_db(minutes_id: int) -> str:
    """`render_minutes` sur les objets du modèle relus dans une session."""
    from cabinet.db.base import get_session
    from cabinet.db.models import ActionItem, Decision, Meeting, Minutes
    from cabinet.services.minutes_render import render_minutes

    with get_session() as session:
        minutes = session.get(Minutes, minutes_id)
        assert minutes is not None
        meeting = session.get(Meeting, minutes.meeting_id)
        decisions = session.scalars(
            select(Decision).where(Decision.minutes_id == minutes_id).order_by(Decision.id)
        ).all()
        actions = session.scalars(
            select(ActionItem).where(ActionItem.minutes_id == minutes_id).order_by(ActionItem.id)
        ).all()
        return render_minutes(
            meeting,
            minutes,
            list(decisions),
            list(actions),
            list(minutes.open_points),
            minutes.next_meeting,
        )


def lines_of(markdown: str) -> list[str]:
    """Lignes sans espaces de fin, sans lignes vides au début ni à la fin."""
    lines = [line.rstrip() for line in markdown.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


# --- Pages -------------------------------------------------------------------


class PageReader(HTMLParser):
    """Texte visible et adresses d'action des formulaires d'une page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.forms: list[tuple[str, str]] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in ("script", "style"):
            self._hidden += 1
        if tag == "form":
            action = urlsplit(attributes.get("action") or "").path
            self.forms.append((action, (attributes.get("method") or "get").lower()))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.chunks.append(data)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.chunks).split()).replace("’", "'")

    @property
    def post_actions(self) -> set[str]:
        return {action for action, method in self.forms if method == "post"}


def read_page(html: str) -> PageReader:
    reader = PageReader()
    reader.feed(html)
    reader.close()
    return reader


@pytest.fixture
def client() -> Iterator[TestClient]:
    from cabinet.web.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def get_page(client: TestClient, url: str) -> PageReader:
    response = client.get(url)
    assert response.status_code == 200, f"GET {url} : {response.status_code}"
    return read_page(response.text)


def post(client: TestClient, url: str, data: dict[str, str] | None = None) -> Any:
    return client.post(url, data=data or {}, headers=LOCAL_ORIGIN, follow_redirects=False)


def assert_redirects_to(response: Any, path: str) -> None:
    assert response.status_code == 303, f"code {response.status_code} : {response.text[:300]}"
    assert urlsplit(response.headers["location"]).path == path


def redirect_message(response: Any) -> str | None:
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return query.get("message", [None])[0]


# --- Critère 1 -------------------------------------------------------------


def test_c1_render_has_six_sections_in_order(dataset: dict[str, Any]) -> None:
    lines = lines_of(render_from_db(1))
    assert lines[0] == "# Compte rendu : Comité de pilotage"
    positions = [lines.index(section) for section in SECTIONS]
    assert positions == sorted(positions)
    assert [line for line in lines if line.startswith("## ")] == SECTIONS


def test_c1_render_marks_unverified_items_only(dataset: dict[str, Any]) -> None:
    lines = lines_of(render_from_db(1))
    assert "- Le prestataire actuel est reconduit (à vérifier)" in lines
    assert "- Budget de la phase deux validé" in lines
    assert "| Envoyer la convention signée (à vérifier) | non précisé | non précisée |" in lines
    assert "| Préparer le planning détaillé | Bruno Keller | 30.11.2026 |" in lines
    assert sum(line.count("(à vérifier)") for line in lines) == 2


def test_c1_render_without_unverified_item_has_no_mark(ready: dict[str, Any]) -> None:
    rendered = render_from_db(1)
    assert "(à vérifier)" not in rendered
    assert [line for line in lines_of(rendered) if line.startswith("## ")] == SECTIONS


def test_contract_render_matches_template(dataset: dict[str, Any]) -> None:
    """Gabarit exact de `specs/06 §7`, ligne à ligne."""
    assert lines_of(render_from_db(1)) == lines_of(EXPECTED_RENDER)


def test_contract_render_empty_sections(dataset: dict[str, Any]) -> None:
    """Réunion hors agenda, sans décision, action, point ouvert ni prochaine séance."""
    insert_rows(
        {
            "minutes": [
                {
                    "id": 2,
                    "meeting_id": 2,
                    "version": 1,
                    "summary": "Point rapide sans décision.",
                }
            ]
        }
    )
    lines = lines_of(render_from_db(2))
    assert lines == lines_of(EXPECTED_EMPTY_RENDER)
    assert "| Action | Responsable | Échéance |" not in lines


def test_contract_render_escapes_table_cells(ready: dict[str, Any]) -> None:
    """Dans une cellule, `|` devient `\\|` et un retour à la ligne une espace."""
    set_columns(
        "action_item",
        {"description": "Comparer A | B\nen détail", "owner": "Équipe A|B"},
        id=2,
    )
    lines = lines_of(render_from_db(1))
    assert "| Comparer A \\| B en détail | Équipe A\\|B | non précisée |" in lines


def test_contract_minutes_agent_fills_content_md() -> None:
    """`MinutesAgent` remplit désormais `content_md` avec `render_minutes`."""
    from cabinet.agents.minutes import MinutesAgent
    from cabinet.db.base import get_session
    from cabinet.llm.prompts.minutes import ActionOut, DecisionOut, MinutesOut

    data = load_dataset()
    data.pop("minutes")
    data.pop("decision")
    data.pop("action_item")
    insert_rows(data)
    answer = MinutesOut(
        summary="Le comité a validé le budget de la phase deux.",
        decisions=[
            DecisionOut(
                text="Budget de la phase deux validé",
                quote="Nous validons le budget de la phase deux pour le premier trimestre.",
            )
        ],
        actions=[
            ActionOut(
                description="Signer le contrat cadre",
                owner=None,
                due_date=None,
                quote="Le directeur signe le contrat cadre lundi prochain.",
            )
        ],
        open_points=["Recrutement d'un second développeur"],
        next_meeting=None,
    )
    with get_session() as session:
        MinutesAgent(FakeLLM(answer)).run(session)
        session.commit()

    rows = fetch_rows("minutes", meeting_id=1)
    assert len(rows) == 1
    content = rows[0]["content_md"]
    assert content
    assert content == render_from_db(rows[0]["id"])
    assert "- Budget de la phase deux validé" in lines_of(content)
    assert "| Signer le contrat cadre (à vérifier) | non précisé | non précisée |" in content


# --- Critère 2 -------------------------------------------------------------


def test_c2_validate_with_unverified_item_returns_409_and_changes_nothing(
    environnement_isole: Path, dataset: dict[str, Any], client: TestClient
) -> None:
    create_files(environnement_isole, dataset)
    before = snapshot(environnement_isole)
    response = post(client, "/reunions/1/valider")
    assert response.status_code == 409
    assert BLOCKED_TWO in read_page(response.text).text
    assert snapshot(environnement_isole) == before
    assert (environnement_isole / dataset["inbox_item"][1]["path"]).is_file()


def test_c2_validate_minutes_raises_validation_blocked(dataset: dict[str, Any]) -> None:
    from cabinet.agents.minutes import ValidationBlocked, validate_minutes
    from cabinet.db.base import get_session

    with get_session() as session, pytest.raises(ValidationBlocked) as raised:
        validate_minutes(session, 1)
    assert str(raised.value) == BLOCKED_TWO
    assert fetch_row("minutes", 1)["status"] == "brouillon"
    assert fetch_rows("draft") == []


# --- Critère 3 -------------------------------------------------------------


def test_c3_validate_succeeds_after_confirming(
    dataset: dict[str, Any], client: TestClient
) -> None:
    assert_redirects_to(post(client, "/reunions/1/decisions/2/confirmer"), "/reunions/1")
    assert_redirects_to(post(client, "/reunions/1/actions/2/confirmer"), "/reunions/1")
    assert fetch_row("decision", 2)["verified"]
    assert fetch_row("action_item", 2)["verified"]

    assert_redirects_to(post(client, "/reunions/1/valider"), "/reunions/1")
    assert fetch_row("minutes", 1)["status"] == "valide"


def test_c3_validate_succeeds_after_deleting(dataset: dict[str, Any], client: TestClient) -> None:
    assert_redirects_to(post(client, "/reunions/1/decisions/2/supprimer"), "/reunions/1")
    assert_redirects_to(post(client, "/reunions/1/actions/2/supprimer"), "/reunions/1")
    assert fetch_row("decision", 2) is None
    assert fetch_row("action_item", 2) is None

    assert_redirects_to(post(client, "/reunions/1/valider"), "/reunions/1")
    assert fetch_row("minutes", 1)["status"] == "valide"


def test_c3_confirming_one_of_two_still_blocks(dataset: dict[str, Any], client: TestClient) -> None:
    assert_redirects_to(post(client, "/reunions/1/decisions/2/confirmer"), "/reunions/1")
    response = post(client, "/reunions/1/valider")
    assert response.status_code == 409
    assert "1 élément(s) restent à vérifier." in read_page(response.text).text
    assert fetch_row("minutes", 1)["status"] == "brouillon"


def test_contract_confirm_rerenders_content(dataset: dict[str, Any], client: TestClient) -> None:
    """Chaque correction recalcule `content_md` (specs/06 §8.1)."""
    assert_redirects_to(post(client, "/reunions/1/decisions/2/confirmer"), "/reunions/1")
    content = fetch_row("minutes", 1)["content_md"]
    assert "- Le prestataire actuel est reconduit" in lines_of(content)
    assert "- Le prestataire actuel est reconduit (à vérifier)" not in content
    assert content == render_from_db(1)


# --- Critère 4 -------------------------------------------------------------


def test_c4_validation_writes_export_file(
    environnement_isole: Path, ready: dict[str, Any], client: TestClient
) -> None:
    assert_redirects_to(post(client, "/reunions/1/valider"), "/reunions/1")
    export = environnement_isole / "brouillons" / EXPORT_NAME
    assert export.is_file(), "fichier brouillons/AAAA-MM-JJ_cr_<id>.md absent"
    content_md = fetch_row("minutes", 1)["content_md"]
    assert content_md
    assert export.read_text(encoding="utf-8").rstrip("\n") == content_md.rstrip("\n")


def test_c4_validation_creates_envoi_cr_draft_for_attendees(
    ready: dict[str, Any], client: TestClient
) -> None:
    assert_redirects_to(post(client, "/reunions/1/valider"), "/reunions/1")
    drafts = fetch_rows("draft")
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["kind"] == "envoi_cr"
    assert draft["status"] == "brouillon"
    assert as_list(draft["recipients"]) == ATTENDEES
    assert draft["subject"] == DRAFT_SUBJECT
    assert draft["body"] == fetch_row("minutes", 1)["content_md"]
    assert draft["meeting_id"] == 1
    assert draft["created_at"] == utc(NOW)


def test_contract_validate_minutes_service(
    environnement_isole: Path, ready: dict[str, Any]
) -> None:
    """`validate_minutes(session, minutes_id, delete_audio=True)` renvoie le compte rendu."""
    from cabinet.agents.minutes import validate_minutes
    from cabinet.db.base import get_session

    with get_session() as session:
        minutes = validate_minutes(session, 1, delete_audio=False)
        assert minutes.id == 1
        assert minutes.status == "valide"
        session.commit()

    row = fetch_row("minutes", 1)
    assert row["status"] == "valide"
    assert row["content_md"] == render_from_db(1)
    assert "(à vérifier)" not in row["content_md"]
    assert (environnement_isole / "brouillons" / EXPORT_NAME).is_file()
    assert [d["kind"] for d in fetch_rows("draft")] == ["envoi_cr"]


def test_contract_validation_rerenders_before_validating(
    environnement_isole: Path, ready: dict[str, Any], client: TestClient
) -> None:
    """Un `content_md` périmé est recalculé juste avant la validation (specs/06 §8.2)."""
    set_columns("minutes", {"content_md": "Ancien rendu périmé."}, id=1)
    assert_redirects_to(post(client, "/reunions/1/valider"), "/reunions/1")
    content_md = fetch_row("minutes", 1)["content_md"]
    assert "Ancien rendu périmé." not in content_md
    assert content_md == render_from_db(1)
    export = (environnement_isole / "brouillons" / EXPORT_NAME).read_text(encoding="utf-8")
    assert "Ancien rendu périmé." not in export


def test_contract_validation_without_event_has_no_recipients(dataset: dict[str, Any]) -> None:
    """Réunion hors agenda : liste de destinataires vide (specs/06 §8.2, étape 6)."""
    from cabinet.agents.minutes import validate_minutes
    from cabinet.db.base import get_session

    insert_rows(
        {
            "minutes": [
                {"id": 2, "meeting_id": 2, "version": 1, "summary": "Point rapide."}
            ]
        }
    )
    with get_session() as session:
        validate_minutes(session, 2, delete_audio=False)
        session.commit()
    drafts = fetch_rows("draft", meeting_id=2)
    assert len(drafts) == 1
    assert as_list(drafts[0]["recipients"]) == []
    assert drafts[0]["subject"] == "Compte rendu : Point équipe, 18.11.2026"


def test_contract_validate_twice_is_refused(validated: dict[str, Any], client: TestClient) -> None:
    from cabinet.agents.minutes import ValidationBlocked, validate_minutes
    from cabinet.db.base import get_session

    with get_session() as session, pytest.raises(ValidationBlocked) as raised:
        validate_minutes(session, 1)
    assert str(raised.value) == ALREADY_VALIDATED

    response = post(client, "/reunions/1/valider")
    assert response.status_code == 409
    assert ALREADY_VALIDATED in read_page(response.text).text
    assert fetch_rows("draft") == []


# --- Critère 5 -------------------------------------------------------------


def test_c5_validation_deletes_meeting_audio_when_setting_on(
    environnement_isole: Path, ready: dict[str, Any], client: TestClient
) -> None:
    """Réglage par défaut : `delete_audio_after_validation = true` (specs/03 §3)."""
    create_files(environnement_isole, ready)
    items = {item["id"]: environnement_isole / item["path"] for item in ready["inbox_item"]}

    assert_redirects_to(post(client, "/reunions/1/valider"), "/reunions/1")

    assert not items[2].exists(), "l'audio traité de la réunion validée doit être supprimé"
    assert items[1].is_file(), "un fichier non audio n'est jamais touché"
    assert items[3].is_file(), "l'audio d'une autre réunion n'est jamais touché"
    assert items[4].is_file(), "des notes ne sont jamais touchées"
    assert items[5].is_file(), "un audio non traité n'est jamais touché"
    assert [row["id"] for row in fetch_rows("inbox_item")] == [1, 2, 3, 4, 5]


def test_c5_validation_keeps_audio_when_setting_off(
    environnement_isole: Path, ready: dict[str, Any], client: TestClient
) -> None:
    write_settings(environnement_isole, "[transcription]\ndelete_audio_after_validation = false\n")
    create_files(environnement_isole, ready)

    assert_redirects_to(post(client, "/reunions/1/valider"), "/reunions/1")

    assert fetch_row("minutes", 1)["status"] == "valide"
    for item in ready["inbox_item"]:
        assert (environnement_isole / item["path"]).is_file(), item["path"]


def test_c5_validate_minutes_delete_audio_flag(
    environnement_isole: Path, ready: dict[str, Any]
) -> None:
    """La valeur par défaut de `delete_audio` est vraie (specs/06 §8.2)."""
    from cabinet.agents.minutes import validate_minutes
    from cabinet.db.base import get_session

    create_files(environnement_isole, ready)
    audio = environnement_isole / ready["inbox_item"][1]["path"]
    with get_session() as session:
        validate_minutes(session, 1)
        session.commit()
    assert not audio.exists()


# --- Critère 6 -------------------------------------------------------------


def test_c6_editing_action_of_validated_minutes_returns_409(
    validated: dict[str, Any], client: TestClient
) -> None:
    before = fetch_row("action_item", 1)
    response = post(
        client,
        "/reunions/1/actions/1",
        {
            "description": "Description changée",
            "owner": "Chloé Favre",
            "due_date": "2026-12-15",
            "quote": "Je prépare le planning détaillé avant le 30 novembre.",
        },
    )
    assert response.status_code == 409
    assert fetch_row("action_item", 1) == before


@pytest.mark.parametrize(
    ("url", "data"),
    [
        ("/reunions/1/resume", {"summary": "Résumé changé."}),
        ("/reunions/1/decisions/1", {"text": "Texte changé", "quote": "Extrait changé."}),
        ("/reunions/1/decisions/1/confirmer", {}),
        ("/reunions/1/decisions/1/supprimer", {}),
        (
            "/reunions/1/actions/1",
            {"description": "Changée", "owner": "", "due_date": "", "quote": "Extrait."},
        ),
        ("/reunions/1/actions/1/confirmer", {}),
        ("/reunions/1/actions/1/supprimer", {}),
        ("/reunions/1/regenerer", {}),
        ("/reunions/1/valider", {}),
    ],
)
def test_c6_every_correction_route_refuses_validated_minutes(
    environnement_isole: Path,
    validated: dict[str, Any],
    client: TestClient,
    url: str,
    data: dict[str, str],
) -> None:
    before = snapshot(environnement_isole)
    response = post(client, url, data)
    assert response.status_code == 409
    assert snapshot(environnement_isole) == before


# --- Critère 7 -------------------------------------------------------------


def test_c7_meetings_page_and_meeting_without_source_return_200(
    dataset: dict[str, Any], client: TestClient
) -> None:
    listing = get_page(client, "/reunions")
    assert "Comité de pilotage" in listing.text
    assert "Point équipe" in listing.text
    page = get_page(client, "/reunions/2")
    assert "Point équipe" in page.text
    assert "18.11.2026" in page.text


def test_c7_meetings_page_on_empty_database_returns_200(client: TestClient) -> None:
    from cabinet.db.base import init_db

    init_db()
    assert client.get("/reunions").status_code == 200


def test_c7_meeting_without_event_or_minutes_returns_200(client: TestClient) -> None:
    insert_rows(
        {
            "series": [{"id": 1, "key": "atelier", "label": "Atelier"}],
            "meeting": [{"id": 1, "series_id": 1, "title": "Atelier", "date": "2026-11-25"}],
        }
    )
    assert "Atelier" in get_page(client, "/reunions/1").text


def test_contract_unknown_meeting_returns_404(dataset: dict[str, Any], client: TestClient) -> None:
    assert client.get("/reunions/1").status_code == 200
    assert client.get("/reunions/999").status_code == 404


# --- Page Réunions (specs/08 §4.1) -------------------------------------------


def test_contract_meetings_list_window_and_order(client: TestClient) -> None:
    """De 30 jours avant à 14 jours après le jour courant, la plus récente d'abord."""
    insert_rows(
        {
            "series": [{"id": 1, "key": "fenetre", "label": "Série fenêtre"}],
            "meeting": [
                {"id": 1, "series_id": 1, "title": "Revue trop ancienne", "date": "2026-10-24"},
                {"id": 2, "series_id": 1, "title": "Revue de fin octobre", "date": "2026-10-26"},
                {"id": 3, "series_id": 1, "title": "Revue du jour", "date": "2026-11-24"},
                {"id": 4, "series_id": 1, "title": "Revue de décembre", "date": "2026-12-07"},
                {"id": 5, "series_id": 1, "title": "Revue trop lointaine", "date": "2026-12-09"},
            ],
        }
    )
    text = get_page(client, "/reunions").text
    expected = ["Revue de décembre", "Revue du jour", "Revue de fin octobre"]
    for title in expected:
        assert title in text, f"« {title} » absente de /reunions"
    positions = [text.index(title) for title in expected]
    assert positions == sorted(positions), "réunions hors de l'ordre décroissant"
    assert "Revue trop ancienne" not in text
    assert "Revue trop lointaine" not in text
    for shown in ("07.12.2026", "24.11.2026", "26.10.2026"):
        assert shown in text


def test_contract_meetings_list_shows_minutes_and_prep_state(client: TestClient) -> None:
    """État du compte rendu (« aucun », « brouillon » et son compte, « validé »), et de la fiche."""
    insert_rows(
        {
            "series": [{"id": 1, "key": "etats", "label": "Série des états"}],
            "meeting": [
                {"id": 1, "series_id": 1, "title": "Réunion brouillon", "date": "2026-11-23"},
                {"id": 2, "series_id": 1, "title": "Réunion validée", "date": "2026-11-20"},
                {
                    "id": 3,
                    "series_id": 1,
                    "title": "Réunion sans compte rendu",
                    "date": "2026-11-18",
                },
            ],
            "minutes": [
                {"id": 1, "meeting_id": 1, "version": 1, "summary": "Résumé un."},
                {
                    "id": 2,
                    "meeting_id": 2,
                    "version": 1,
                    "summary": "Résumé deux.",
                    "status": "valide",
                },
            ],
            "decision": [
                {"id": 1, "meeting_id": 1, "minutes_id": 1, "text": "D1", "quote": "Q1"},
                {"id": 2, "meeting_id": 1, "minutes_id": 1, "text": "D2", "quote": "Q2"},
                {"id": 3, "meeting_id": 1, "minutes_id": 1, "text": "D3", "quote": "Q3"},
            ],
            "action_item": [
                {"id": 1, "meeting_id": 1, "minutes_id": 1, "description": "A1", "quote": "Q4"},
                {
                    "id": 2,
                    "meeting_id": 1,
                    "minutes_id": 1,
                    "description": "A2",
                    "quote": "Q5",
                    "verified": True,
                },
            ],
            "prep_note": [{"id": 1, "meeting_id": 2, "content_md": "# Fiche"}],
        }
    )
    text = get_page(client, "/reunions").text
    titles = ["Réunion brouillon", "Réunion validée", "Réunion sans compte rendu"]
    starts = [text.index(title) for title in titles]
    assert starts == sorted(starts)
    rows = [text[start:end] for start, end in zip(starts, [*starts[1:], len(text)], strict=True)]

    assert "brouillon" in rows[0].lower().replace("réunion brouillon", "")
    assert re.search(r"\b4\b", rows[0]), "nombre d'éléments à vérifier absent (4 attendus)"
    assert "validé" in rows[1].lower().replace("réunion validée", "")
    assert re.search(r"\baucun\b", rows[2], re.IGNORECASE)
    assert "prête" in rows[1].lower()
    assert "prête" not in rows[0].lower()
    assert re.search(r"\baucune\b", rows[0], re.IGNORECASE)


# --- Page d'une réunion (specs/08 §4.2) --------------------------------------


def test_contract_meeting_page_shows_header_sources_and_minutes(
    dataset: dict[str, Any], client: TestClient
) -> None:
    insert_rows(
        {
            "prep_note": [
                {
                    "id": 1,
                    "meeting_id": 1,
                    "content_md": "# Fiche de préparation : Comité de pilotage\n\n"
                    "Aucun historique pour cette série.",
                }
            ]
        }
    )
    text = get_page(client, "/reunions/1").text
    expected = [
        "Comité de pilotage",
        "20.11.2026",
        "14:00",
        "Salle Jura",
        *ATTENDEES,
        "Comité de pilotage mensuel",
        "2026-11-20_comite-de-pilotage.vtt",
        "2026-11-20_complement-copil.m4a",
        "Aucun historique pour cette série.",
        "Le comité a validé le budget de la phase deux et confié le planning à Bruno.",
        "Budget de la phase deux validé",
        "Nous validons le budget de la phase deux pour le premier trimestre.",
        "Le prestataire actuel est reconduit",
        "Nous reconduisons le prestataire pour deux ans.",
        "Préparer le planning détaillé",
        "Je prépare le planning détaillé avant le 30 novembre.",
        "Bruno Keller",
        "Envoyer la convention signée",
        "Chloé envoie la convention signée demain.",
        "Recrutement d'un second développeur",
        "Jeudi 3 décembre à 14 h",
    ]
    for value in expected:
        assert value in text, f"« {value} » absent de la page de la réunion"
    assert "à vérifier" in text
    assert re.search(r"version\W{0,3}1\b|\bv1\b", text, re.IGNORECASE), "numéro de version absent"


def test_contract_meeting_page_draft_buttons(dataset: dict[str, Any], client: TestClient) -> None:
    """Brouillon : modifier, supprimer, confirmer (non vérifiés seulement), régénérer, valider."""
    page = get_page(client, "/reunions/1")
    expected = {
        "/reunions/1/resume",
        "/reunions/1/decisions/1",
        "/reunions/1/decisions/1/supprimer",
        "/reunions/1/decisions/2",
        "/reunions/1/decisions/2/supprimer",
        "/reunions/1/decisions/2/confirmer",
        "/reunions/1/actions/1",
        "/reunions/1/actions/1/supprimer",
        "/reunions/1/actions/2",
        "/reunions/1/actions/2/supprimer",
        "/reunions/1/actions/2/confirmer",
        "/reunions/1/regenerer",
        "/reunions/1/valider",
    }
    missing = expected - page.post_actions
    assert not missing, f"formulaires absents : {sorted(missing)}"
    assert "/reunions/1/decisions/1/confirmer" not in page.post_actions
    assert "/reunions/1/actions/1/confirmer" not in page.post_actions
    for label in ("Valider le compte rendu", "Régénérer", "Confirmer"):
        assert label in page.text


def test_contract_meeting_page_validated_has_no_edit_buttons(
    validated: dict[str, Any], client: TestClient
) -> None:
    page = get_page(client, "/reunions/1")
    correction = re.compile(r"^/reunions/1/(resume|decisions|actions|regenerer$|valider$)")
    assert [action for action in page.post_actions if correction.search(action)] == []
    assert "Valider le compte rendu" not in page.text
    assert "Confirmer" not in page.text
    assert "Budget de la phase deux validé" in page.text


def test_contract_meeting_page_escapes_text(dataset: dict[str, Any], client: TestClient) -> None:
    """Un texte venu du modèle passe par l'échappement de Jinja2 (specs/08 §10)."""
    set_columns("minutes", {"summary": "<b>gras</b> et <script>alert(1)</script>"}, id=1)
    response = client.get("/reunions/1")
    assert response.status_code == 200
    assert "<b>gras</b>" not in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "<b>gras</b>" in read_page(response.text).text


# --- Corrections (specs/08 §4.3, specs/06 §8.1) ------------------------------


def test_contract_edit_summary_rerenders(dataset: dict[str, Any], client: TestClient) -> None:
    response = post(client, "/reunions/1/resume", {"summary": "Nouveau résumé du comité."})
    assert_redirects_to(response, "/reunions/1")
    row = fetch_row("minutes", 1)
    assert row["summary"] == "Nouveau résumé du comité."
    assert "Nouveau résumé du comité." in row["content_md"]
    assert row["content_md"] == render_from_db(1)


def test_contract_edit_decision_text_keeps_verified(
    dataset: dict[str, Any], client: TestClient
) -> None:
    """Modifier le texte seul ne change pas `verified`, dans un sens comme dans l'autre."""
    for ident, verified in ((1, True), (2, False)):
        quote = fetch_row("decision", ident)["quote"]
        response = post(
            client,
            f"/reunions/1/decisions/{ident}",
            {"text": f"Texte corrigé {ident}", "quote": quote},
        )
        assert_redirects_to(response, "/reunions/1")
        row = fetch_row("decision", ident)
        assert row["text"] == f"Texte corrigé {ident}"
        assert bool(row["verified"]) is verified
    assert "- Texte corrigé 2 (à vérifier)" in lines_of(fetch_row("minutes", 1)["content_md"])


def test_contract_edit_decision_quote_reruns_check(
    dataset: dict[str, Any], client: TestClient
) -> None:
    """Un extrait retouché est recontrôlé contre la source de la réunion (specs/06 §8.1)."""
    invented = "Le comité adopte un budget triplé sans aucune discussion."
    found = "La question du recrutement reste ouverte pour l'instant."
    response = post(
        client,
        "/reunions/1/decisions/1",
        {"text": "Budget de la phase deux validé", "quote": invented},
    )
    assert_redirects_to(response, "/reunions/1")
    response = post(
        client,
        "/reunions/1/decisions/2",
        {"text": "Le prestataire actuel est reconduit", "quote": found},
    )
    assert_redirects_to(response, "/reunions/1")

    first, second = fetch_row("decision", 1), fetch_row("decision", 2)
    assert (first["quote"], bool(first["verified"])) == (invented, False)
    assert (second["quote"], bool(second["verified"])) == (found, True)


def test_contract_edit_action_fields(dataset: dict[str, Any], client: TestClient) -> None:
    response = post(
        client,
        "/reunions/1/actions/1",
        {
            "description": "Préparer le planning détaillé de la phase deux",
            "owner": "Chloé Favre",
            "due_date": "2026-12-04",
            "quote": "Je prépare le planning détaillé avant le 30 novembre.",
        },
    )
    assert_redirects_to(response, "/reunions/1")
    row = fetch_row("action_item", 1)
    assert row["description"] == "Préparer le planning détaillé de la phase deux"
    assert row["owner"] == "Chloé Favre"
    assert row["due_date"] == date(2026, 12, 4)
    assert row["verified"]
    expected_line = "| Préparer le planning détaillé de la phase deux | Chloé Favre | 04.12.2026 |"
    assert expected_line in lines_of(fetch_row("minutes", 1)["content_md"])


def test_contract_edit_action_quote_reruns_check(
    dataset: dict[str, Any], client: TestClient
) -> None:
    response = post(
        client,
        "/reunions/1/actions/2",
        {
            "description": "Envoyer la convention signée",
            "owner": "",
            "due_date": "",
            "quote": "Je prépare le planning détaillé avant le 30 novembre.",
        },
    )
    assert_redirects_to(response, "/reunions/1")
    assert fetch_row("action_item", 2)["verified"]


def test_contract_unknown_decision_returns_404(dataset: dict[str, Any], client: TestClient) -> None:
    assert_redirects_to(post(client, "/reunions/1/decisions/2/confirmer"), "/reunions/1")
    assert post(client, "/reunions/1/decisions/99/confirmer").status_code == 404


def test_contract_regenerate_failure_keeps_minutes(
    monkeypatch: pytest.MonkeyPatch,
    environnement_isole: Path,
    dataset: dict[str, Any],
    client: TestClient,
) -> None:
    """`LLMError` : message « La régénération a échoué : … » et ancien compte rendu en place."""
    from cabinet.llm.types import LLMError

    replace_local_llm(monkeypatch, FakeLLM(LLMError("modèle indisponible")))
    before = snapshot(environnement_isole)

    response = post(client, "/reunions/1/regenerer")

    assert_redirects_to(response, "/reunions/1")
    assert redirect_message(response) == "La régénération a échoué : modèle indisponible."
    assert snapshot(environnement_isole) == before
    runs = fetch_rows("agent_run", agent="minutes")
    assert [(run["target_ref"], run["status"]) for run in runs] == [("reunion:1", "erreur")]


def test_contract_regenerate_creates_next_version(
    monkeypatch: pytest.MonkeyPatch, dataset: dict[str, Any], client: TestClient
) -> None:
    """`MinutesAgent.regenerate` dans `record_run(session, "minutes", "reunion:{id}")`."""
    from cabinet.llm.prompts.minutes import DecisionOut, MinutesOut

    answer = MinutesOut(
        summary="Résumé régénéré du comité.",
        decisions=[
            DecisionOut(
                text="Budget confirmé",
                quote="Nous validons le budget de la phase deux pour le premier trimestre.",
            )
        ],
        actions=[],
        open_points=[],
        next_meeting=None,
    )
    fake = FakeLLM(answer)
    replace_local_llm(monkeypatch, fake)

    assert_redirects_to(post(client, "/reunions/1/regenerer"), "/reunions/1")

    assert len(fake.calls) == 1
    versions = {row["version"]: row for row in fetch_rows("minutes", meeting_id=1)}
    assert set(versions) == {1, 2}
    assert versions[2]["status"] == "brouillon"
    assert versions[2]["summary"] == "Résumé régénéré du comité."
    assert versions[2]["content_md"] == render_from_db(versions[2]["id"])
    assert {row["minutes_id"] for row in fetch_rows("decision")} == {versions[2]["id"]}
    runs = fetch_rows("agent_run", agent="minutes")
    assert [(run["target_ref"], run["status"]) for run in runs] == [("reunion:1", "ok")]
