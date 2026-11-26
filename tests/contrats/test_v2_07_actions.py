"""Tests de contrat du ticket V2-07, « Actions et décisions ».

Écrits par le relecteur avant la vague. Un test `test_cN_*` par critère
d'acceptation numéroté, des tests `test_contract_*` pour les points du contrat
et des précisions du ticket, et le scénario S5 de `specs/09 §4`, rattaché à
cette tâche.

Sources : `specs/08 §5` (page Actions, paramètres, tri, routes de suivi),
`specs/08 §2.1` (compteur « Actions ouvertes » à partir de V2-07),
`specs/08 §4.3` (contenu figé, suivi modifiable), `specs/01 §6` (codes HTTP),
`specs/09 §3` (doublure `FakeLLM`, date fixée, client web) et `specs/09 §4`
(scénario S5).

Le jeu `fixtures/v2_07/actions.json` est écrit dans la base par les noms de
tables et de colonnes de `specs/02 §2`, sur des tables relues par réflexion.
Il ne dépend d'aucun code des tickets V2-01 à V2-06. La page est jugée sur son
texte visible : présence et ordre des descriptions d'actions.

Le scénario S5 enchaîne les composants réels des tâches dont V2-07 dépend :
`MinutesAgent` (V2-04, V2-05) dans un `Pipeline` (V1-08), puis les routes de
la page Réunion (V2-06). Le transcript est posé en base, comme l'aurait laissé
l'agent `transcripts`, pour ne pas dépendre de la lecture des fichiers.
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
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, select, update
from sqlalchemy.sql import sqltypes

FIXTURES = Path(__file__).parent / "fixtures" / "v2_07"
ZURICH = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=ZURICH)
LOCAL_ORIGIN = {"Origin": "http://127.0.0.1:8765"}

# Descriptions du jeu, par identifiant d'action.
DESCRIPTIONS = {
    1: "Mettre à jour le budget",
    2: "Réserver la salle du séminaire",
    3: "Relire la convention",
    4: "Préparer la démonstration",
    5: "Envoyer le compte rendu client",
    6: "Archiver les anciens dossiers",
    7: "Action restée en brouillon",
    8: "Commander le matériel",
    9: "Valider le devis",
}
# Actions ouvertes de comptes rendus validés, dans l'ordre attendu (specs/08 §5.1).
OPEN_VALIDATED_ORDER = [5, 2, 4, 1, 9, 3, 8]
COUNTER_LABEL = "Actions ouvertes"


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


@pytest.fixture
def dataset() -> dict[str, Any]:
    data = json.loads((FIXTURES / "actions.json").read_text(encoding="utf-8"))
    insert_rows(data)
    return data


# --- Pages -------------------------------------------------------------------


class PageReader(HTMLParser):
    """Texte visible d'une page, sans scripts ni styles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.chunks.append(data)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.chunks).split()).replace("’", "'")


def page_text(client: TestClient, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, f"GET {url} : {response.status_code}"
    reader = PageReader()
    reader.feed(response.text)
    reader.close()
    return reader.text


def actions_page(client: TestClient, **params: str) -> str:
    url = "/actions" + (f"?{urlencode(params)}" if params else "")
    return page_text(client, url)


def listed(text: str) -> list[int]:
    """Identifiants des actions du jeu présentes dans la page, dans l'ordre d'affichage."""
    found = [(text.index(label), ident) for ident, label in DESCRIPTIONS.items() if label in text]
    return [ident for _, ident in sorted(found)]


def counter_values(text: str, label: str) -> set[int]:
    """Nombres placés juste après ou juste avant le libellé d'un compteur."""
    pattern = re.escape(label)
    values: set[int] = set()
    after = re.search(pattern + r"\W{0,12}?(\d+)", text, re.IGNORECASE)
    before = re.search(r"(\d+)\W{0,12}?" + pattern, text, re.IGNORECASE)
    for match in (after, before):
        if match is not None:
            values.add(int(match.group(1)))
    return values


def open_actions_counter(client: TestClient) -> set[int]:
    return counter_values(page_text(client, "/"), COUNTER_LABEL)


@pytest.fixture
def client() -> Iterator[TestClient]:
    from cabinet.web.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def post(client: TestClient, url: str, data: dict[str, str] | None = None) -> Any:
    return client.post(url, data=data or {}, headers=LOCAL_ORIGIN, follow_redirects=False)


def assert_redirects_to(response: Any, path: str) -> None:
    assert response.status_code == 303, f"code {response.status_code} : {response.text[:300]}"
    assert urlsplit(response.headers["location"]).path == path


# --- Critère 1 -------------------------------------------------------------


def test_c1_only_actions_of_validated_minutes_listed(
    dataset: dict[str, Any], client: TestClient
) -> None:
    shown = listed(actions_page(client))
    assert set(shown) == set(OPEN_VALIDATED_ORDER)
    assert 7 not in shown, "une action d'un compte rendu brouillon n'apparaît jamais"
    assert 6 not in shown, "une action faite n'apparaît pas par défaut"


def test_c1_validating_minutes_makes_its_actions_appear(
    dataset: dict[str, Any], client: TestClient
) -> None:
    assert 7 not in listed(actions_page(client))
    set_columns("minutes", {"status": "valide"}, id=5)
    assert 7 in listed(actions_page(client))


# --- Critère 2 -------------------------------------------------------------


def test_c2_sorted_by_due_date_without_due_date_last(
    dataset: dict[str, Any], client: TestClient
) -> None:
    """Échéance croissante, sans échéance en dernier, puis `id` (specs/08 §5.1)."""
    assert listed(actions_page(client)) == OPEN_VALIDATED_ORDER


# --- Critère 3 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("series_id", "expected"),
    [("1", [2, 1, 3]), ("2", [5, 4, 9, 8])],
)
def test_c3_filter_by_series(
    dataset: dict[str, Any], client: TestClient, series_id: str, expected: list[int]
) -> None:
    assert listed(actions_page(client, serie=series_id)) == expected


@pytest.mark.parametrize(
    ("owner", "expected"),
    [("Alice Martin", [4, 1]), ("Chloé Favre", [5, 9]), ("Bruno Keller", [2])],
)
def test_c3_filter_by_owner(
    dataset: dict[str, Any], client: TestClient, owner: str, expected: list[int]
) -> None:
    """Valeur exacte de `owner` ; l'action 7 d'Alice reste cachée (brouillon)."""
    assert listed(actions_page(client, responsable=owner)) == expected


def test_c3_filter_overdue(dataset: dict[str, Any], client: TestClient) -> None:
    """Échéance antérieure au jour courant : l'échéance du jour n'est pas en retard."""
    assert listed(actions_page(client, retard="1")) == [5, 2]


def test_c3_filter_overdue_follows_injected_date(
    dataset: dict[str, Any], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cabinet.clock.now", lambda: datetime(2026, 12, 1, 9, 0, tzinfo=ZURICH))
    assert listed(actions_page(client, retard="1")) == [5, 2, 4, 1, 9]


def test_contract_filters_combine(dataset: dict[str, Any], client: TestClient) -> None:
    assert listed(actions_page(client, serie="2", retard="1")) == [5]
    assert listed(actions_page(client, serie="1", responsable="Alice Martin")) == [1]


def test_contract_state_parameter_shows_closed_actions(
    dataset: dict[str, Any], client: TestClient
) -> None:
    """`etat=fait` montre les actions closes, `ouvert` est la valeur par défaut."""
    assert listed(actions_page(client, etat="fait")) == [6]
    assert listed(actions_page(client, etat="ouvert")) == OPEN_VALIDATED_ORDER


# --- Critère 4 -------------------------------------------------------------


def test_c4_close_then_reopen_changes_state_and_counter(
    dataset: dict[str, Any], client: TestClient
) -> None:
    assert 7 in open_actions_counter(client)

    response = post(client, "/actions/1/clore")
    assert response.status_code == 303
    assert fetch_row("action_item", 1)["status"] == "fait"
    assert 6 in open_actions_counter(client)
    assert 1 not in listed(actions_page(client))
    assert 1 in listed(actions_page(client, etat="fait"))

    response = post(client, "/actions/1/rouvrir")
    assert response.status_code == 303
    assert fetch_row("action_item", 1)["status"] == "ouvert"
    assert 7 in open_actions_counter(client)
    assert listed(actions_page(client)) == OPEN_VALIDATED_ORDER


def test_c4_dashboard_counter_ignores_draft_minutes(
    dataset: dict[str, Any], client: TestClient
) -> None:
    """Le compteur ne compte que les actions ouvertes de comptes rendus validés (specs/08 §2.1)."""
    values = open_actions_counter(client)
    assert 7 in values, f"compteur « {COUNTER_LABEL} » : attendu 7, lu {values}"
    assert 8 not in values


# --- Critère 5 -------------------------------------------------------------


def test_c5_owner_and_due_date_saved(dataset: dict[str, Any], client: TestClient) -> None:
    response = post(client, "/actions/1", {"owner": "Bruno Keller", "due_date": "2026-12-15"})
    assert response.status_code == 303
    row = fetch_row("action_item", 1)
    assert row["owner"] == "Bruno Keller"
    assert row["due_date"] == date(2026, 12, 15)
    assert row["description"] == DESCRIPTIONS[1]
    assert row["status"] == "ouvert"
    assert listed(actions_page(client, responsable="Bruno Keller")) == [2, 1]


def test_c5_empty_fields_give_none(dataset: dict[str, Any], client: TestClient) -> None:
    """Un champ vide donne `None` (specs/08 §5.2)."""
    response = post(client, "/actions/1", {"owner": "", "due_date": ""})
    assert response.status_code == 303
    row = fetch_row("action_item", 1)
    assert row["owner"] is None
    assert row["due_date"] is None
    assert listed(actions_page(client))[-3:] == [1, 3, 8]


# --- Précisions de la spécification -----------------------------------------


@pytest.mark.parametrize(
    ("route", "data"),
    [
        ("/actions/7", {"owner": "Bruno Keller", "due_date": "2026-12-15"}),
        ("/actions/7/clore", {}),
        ("/actions/7/rouvrir", {}),
    ],
)
def test_contract_tracking_routes_refuse_action_of_draft_minutes(
    dataset: dict[str, Any], client: TestClient, route: str, data: dict[str, str]
) -> None:
    """Les routes de suivi n'agissent que sur une action d'un compte rendu validé (§5.2)."""
    before = fetch_row("action_item", 7)
    assert post(client, route, data).status_code == 409
    assert fetch_row("action_item", 7) == before


def test_contract_unknown_action_returns_404(dataset: dict[str, Any], client: TestClient) -> None:
    assert post(client, "/actions/1/clore").status_code == 303
    assert post(client, "/actions/999/clore").status_code == 404


def test_contract_decisions_of_three_last_validated_meetings_per_series(
    dataset: dict[str, Any], client: TestClient
) -> None:
    text = actions_page(client)
    for shown in (
        "Choix du prestataire confirmé",
        "Budget de la phase deux validé",
        "Calendrier de la phase deux arrêté",
        "Revue de code obligatoire",
        "Démonstration mensuelle maintenue",
    ):
        assert shown in text, f"décision « {shown} » absente de /actions"
    assert "Décision prise en octobre" not in text, "quatrième réunion validée de la série"
    assert "Décision encore en brouillon" not in text, "décision d'un compte rendu brouillon"
    assert "Comité de pilotage" in text
    assert "Point équipe" in text


def test_contract_empty_actions_page_returns_200(client: TestClient) -> None:
    from cabinet.db.base import init_db

    init_db()
    assert client.get("/actions").status_code == 200


# --- Scénario S5, réunion complète (specs/09 §4) -----------------------------

S5_SOURCE = (
    "Alice Martin : Nous retenons le scénario B pour le budget 2027.\n"
    "David Roux : Je chiffre le scénario B pour vendredi prochain."
)
S5_ATTENDEES = ["alice.martin@exemple.test", "david.roux@exemple.test"]


def test_contract_scenario_s5_full_meeting(environnement_isole: Path, client: TestClient) -> None:
    """Transcript rattaché, passage avec un extrait inventé, 409, confirmation, validation."""
    from cabinet.agents.minutes import MinutesAgent
    from cabinet.agents.pipeline import Pipeline
    from cabinet.config import paths
    from cabinet.db.base import get_session
    from cabinet.llm.prompts.minutes import ActionOut, DecisionOut, MinutesOut

    insert_rows(
        {
            "series": [{"id": 10, "key": "atelier budget", "label": "Atelier budget"}],
            "event": [
                {
                    "id": 10,
                    "uid": "atelier-budget-2026-11-23@exemple.test",
                    "title": "Atelier budget",
                    "start": "2026-11-23T09:00:00+01:00",
                    "end": "2026-11-23T10:00:00+01:00",
                    "attendees": S5_ATTENDEES,
                }
            ],
            "meeting": [
                {
                    "id": 10,
                    "event_id": 10,
                    "series_id": 10,
                    "title": "Atelier budget",
                    "date": "2026-11-23",
                }
            ],
            "inbox_item": [
                {
                    "id": 10,
                    "path": "traite/2026-11/2026-11-23_atelier-budget.vtt",
                    "kind": "teams_transcript",
                    "status": "traite",
                    "name_date": "2026-11-23",
                    "meeting_id": 10,
                }
            ],
            "transcript": [
                {
                    "id": 10,
                    "meeting_id": 10,
                    "inbox_item_id": 10,
                    "source": "teams",
                    "text": S5_SOURCE,
                }
            ],
        }
    )
    answer = MinutesOut(
        summary="L'atelier retient le scénario B pour le budget 2027.",
        decisions=[
            DecisionOut(
                text="Scénario B retenu",
                quote="Nous retenons le scénario B pour le budget 2027.",
            )
        ],
        actions=[
            ActionOut(
                description="Chiffrer le scénario B",
                owner="David Roux",
                due_date=date(2026, 11, 27),
                quote="David promet un chiffrage complet du scénario C dès lundi matin.",
            )
        ],
        open_points=[],
        next_meeting=None,
    )

    result = Pipeline([MinutesAgent(FakeLLM(answer))], paths().lock_file).run(get_session)
    assert result["minutes"].processed == 1

    minutes = fetch_rows("minutes", meeting_id=10)
    assert [(row["version"], row["status"]) for row in minutes] == [(1, "brouillon")]
    minutes_id = minutes[0]["id"]
    decisions = fetch_rows("decision", minutes_id=minutes_id)
    actions = fetch_rows("action_item", minutes_id=minutes_id)
    assert [bool(row["verified"]) for row in decisions] == [True]
    assert [bool(row["verified"]) for row in actions] == [False]
    assert "Chiffrer le scénario B" not in actions_page(client)

    assert post(client, "/reunions/10/valider").status_code == 409
    assert fetch_rows("minutes", meeting_id=10)[0]["status"] == "brouillon"

    assert_redirects_to(
        post(client, f"/reunions/10/actions/{actions[0]['id']}/confirmer"), "/reunions/10"
    )
    assert_redirects_to(post(client, "/reunions/10/valider"), "/reunions/10")

    assert fetch_rows("minutes", meeting_id=10)[0]["status"] == "valide"
    export = environnement_isole / "brouillons" / f"2026-11-23_cr_{minutes_id}.md"
    assert export.is_file()
    drafts = fetch_rows("draft", meeting_id=10)
    assert [(d["kind"], as_list(d["recipients"])) for d in drafts] == [("envoi_cr", S5_ATTENDEES)]
    assert "Chiffrer le scénario B" in actions_page(client)
