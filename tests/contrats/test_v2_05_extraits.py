"""Tests de contrat du ticket V2-05, « Vérification des extraits ».

Écrits par le relecteur avant la vague. Un test `test_cN_*` par critère
d'acceptation numéroté, des tests `test_contract_*` pour le contrat et les
précisions du ticket.

Sources : `specs/06 §5` (`normalize`, `verify_quote`, un seul
`SequenceMatcher`, filtre `quick_ratio()`), `specs/02 §3.5` (`QuoteCheck`),
`specs/06 §4.2` et `§4.3` (source d'une réunion, étape 6 de l'agent),
`specs/09 §3.2` (`FakeLLM`).

Les extraits et la source du jeu `fixtures/v2_05/` ont été calibrés sur
l'algorithme de `specs/06 §5`. L'extrait à deux mots changés est placé au
début de la source : les fenêtres glissantes partent alors de sa position, et
son score ne dépend pas de l'arrondi du pas. La source de 200 000 caractères
du critère 4 est générée dans le test, de façon déterministe.
"""

from __future__ import annotations

import difflib
import json
import random
import time
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel
from sqlalchemy import MetaData, Table, select
from sqlalchemy.sql import sqltypes

FIXTURES = Path(__file__).parent / "fixtures" / "v2_05"
ZURICH = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 11, 26, 10, 0, tzinfo=ZURICH)

# Phrase de vingt mots, au début de la source du jeu, et sa copie à deux mots changés.
ORIGINAL_20 = (
    "Je préviens l'équipe support et le client avant vendredi soir, "
    "puis je mets à jour le planning du lot deux."
)
CHANGED_20 = (
    "Je préviens l'équipe support et le client avant mercredi soir, "
    "puis je mets à jour le planning du lot neuf."
)
EXACT_VARIANT = "D'ACCORD ; on valide le REPORT de la mise en production au Lundi 7 Decembre"
INVENTED = (
    "Le fournisseur livrera les licences gratuitement au service juridique "
    "dès le mois de janvier prochain."
)

TABLE_ORDER = ("series", "meeting", "inbox_item", "transcript")
DEFAULTS: dict[str, dict[str, Any]] = {
    "series": {"followed": False, "client": None},
    "meeting": {"event_id": None},
    "inbox_item": {
        "name_date": None,
        "consent_confirmed": False,
        "consent_at": None,
        "error": None,
        "detected_at": "2026-11-20T09:00:00+01:00",
    },
    "transcript": {"segments": []},
}

FILLER_WORDS = (
    *("projet", "equipe", "budget", "planning", "livrable", "comite", "client", "reunion"),
    *("valider", "envoyer", "semaine", "mois", "rapport", "risque", "charge", "recette"),
    *("version", "maquette", "serveur", "contrat", "facture", "echeance", "priorite"),
    *("atelier", "lot", "tache", "revue", "point", "suivi", "action", "decision"),
)


# --- Doublure du modèle (specs/09 §3.2) --------------------------------------


class FakeLLM:
    """Rejoue une liste de réponses. La dernière est répétée si les appels continuent."""

    model = "fake"

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: Any, schema: Any = None, images: Any = None) -> Any:
        from cabinet.llm.types import LLMResult

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


def minutes_out(name: str) -> Any:
    from cabinet.llm.prompts.minutes import MinutesOut

    raw = (FIXTURES / f"reponse_{name}.json").read_text(encoding="utf-8")
    return MinutesOut.model_validate_json(raw)


# --- Base de données ---------------------------------------------------------


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr("cabinet.clock.now", lambda: NOW)
    return NOW


def table(name: str) -> Table:
    from cabinet.db.base import get_engine

    return Table(name, MetaData(), autoload_with=get_engine())


def to_db(column: Any, value: Any) -> Any:
    """Valeur du jeu vers la forme stockée (specs/02 §1.1)."""
    if value is None:
        return None
    if isinstance(column.type, sqltypes.DateTime):
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.astimezone(UTC).replace(tzinfo=None)
    if isinstance(column.type, sqltypes.Date) and isinstance(value, str):
        return date.fromisoformat(value)
    return value


@pytest.fixture
def seeded() -> dict[str, list[dict[str, Any]]]:
    from cabinet.db.base import get_engine, init_db

    dataset = json.loads((FIXTURES / "reunions.json").read_text(encoding="utf-8"))
    init_db()
    with get_engine().begin() as connection:
        for name in TABLE_ORDER:
            target = table(name)
            for raw in dataset.get(name, []):
                row = {**DEFAULTS.get(name, {}), **raw}
                if name == "inbox_item":
                    row.setdefault("sha256", sha256(row["path"].encode()).hexdigest())
                values = {key: to_db(target.c[key], val) for key, val in row.items()}
                connection.execute(target.insert().values(**values))
    return dataset


def rows(name: str, **filters: Any) -> list[dict[str, Any]]:
    from cabinet.db.base import get_engine

    target = table(name)
    query = select(target).order_by(target.c.id)
    for key, value in filters.items():
        query = query.where(target.c[key] == value)
    with get_engine().connect() as connection:
        return [dict(row) for row in connection.execute(query).mappings()]


def run_minutes_agent(llm: FakeLLM) -> Any:
    """Un passage de `MinutesAgent` dans `record_run`, comme le fait `Pipeline`."""
    from cabinet.agents.journal import record_run
    from cabinet.agents.minutes import MinutesAgent
    from cabinet.db.base import get_session

    agent = MinutesAgent(llm)
    with get_session() as session, record_run(session, agent.name):
        outcome = agent.run(session)
    return outcome


def fixture_source() -> str:
    dataset = json.loads((FIXTURES / "reunions.json").read_text(encoding="utf-8"))
    return dataset["transcript"][0]["text"]


def long_source(size: int = 200_000) -> str:
    """Texte synthétique déterministe de `size` caractères, en lignes de 8 à 16 mots."""
    generator = random.Random(2026)
    lines: list[str] = []
    length = 0
    while length < size:
        words = [generator.choice(FILLER_WORDS) for _ in range(generator.randint(8, 16))]
        line = " ".join(words).capitalize() + "."
        lines.append(line)
        length += len(line) + 1
    return "\n".join(lines)[:size]


def verify(quote: str, source: str, **options: Any) -> Any:
    from cabinet.services.quotes import verify_quote

    return verify_quote(quote, source, **options)


# --- Critère 1 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Équipe : l'ÉTÉ, déjà !  ", "equipe l ete deja"),
        ("Ça coûte « 45 000 » francs ; d’accord ?", "ca coute 45 000 francs d accord"),
        ("Lot-2 (révisé)...\n\tFin.", "lot 2 revise fin"),
        ("Déjà  vu", "deja vu"),
    ],
)
def test_c1_normalize_lowers_strips_accents_and_punctuation(raw: str, expected: str) -> None:
    from cabinet.services.quotes import normalize

    assert normalize(raw) == expected


def test_c1_exact_quote_found_with_score_one_despite_case_accents_punctuation() -> None:
    from cabinet.services.quotes import QuoteCheck

    source = fixture_source()
    assert EXACT_VARIANT not in source
    result = verify(EXACT_VARIANT, source)

    assert isinstance(result, QuoteCheck)
    assert result.found is True
    assert result.score == 1.0


def test_c1_quote_spanning_two_lines_is_an_exact_match() -> None:
    """Une fin de ligne est une espace après normalisation (`specs/06 §5`)."""
    source = fixture_source()
    quote = "le planning du lot deux ; Claire Dubois : d'accord"
    result = verify(quote, source)

    assert (result.found, result.score) == (True, 1.0)


# --- Critère 2 -------------------------------------------------------------


def test_c2_two_words_changed_out_of_twenty_is_found() -> None:
    original, changed = ORIGINAL_20.split(), CHANGED_20.split()
    assert len(original) == len(changed) == 20
    assert sum(a != b for a, b in zip(original, changed, strict=True)) == 2

    result = verify(CHANGED_20, fixture_source())

    assert result.found is True
    assert 0.85 <= result.score < 1.0


def test_c2_invented_quote_is_not_found() -> None:
    result = verify(INVENTED, fixture_source())

    assert result.found is False
    assert 0.0 <= result.score < 0.85


def test_contract_threshold_parameter_decides_found() -> None:
    """`specs/06 §5`, étape 6 : `found` vaut vrai si le meilleur score atteint le seuil."""
    source = fixture_source()
    default = verify(CHANGED_20, source)
    strict = verify(CHANGED_20, source, threshold=0.99)
    lenient = verify(CHANGED_20, source, threshold=default.score)

    assert strict.found is False
    assert strict.score == pytest.approx(default.score)
    assert lenient.found is True


# --- Critère 3 -------------------------------------------------------------


def test_c3_five_character_quote_not_found_even_if_present() -> None:
    source = fixture_source()
    assert "lot 2" in source
    result = verify("lot 2", source)

    assert (result.found, result.score) == (False, 0.0)


@pytest.mark.parametrize("quote", ["", "   ", "« ! »", "Lot  2, 45"])
def test_c3_empty_or_short_quote_never_found(quote: str) -> None:
    """Moins de 10 caractères une fois normalisé : jamais trouvé (`specs/06 §5`, étape 2)."""
    result = verify(quote, fixture_source())

    assert (result.found, result.score) == (False, 0.0)


# --- Critère 4 -------------------------------------------------------------


def test_c4_quote_checked_in_under_two_seconds_on_200k_source() -> None:
    source = long_source()
    assert len(source) == 200_000
    middle = len(source) // 2
    source = source[:middle] + ORIGINAL_20 + source[middle + len(ORIGINAL_20) :]
    assert len(source) == 200_000

    start = time.perf_counter()
    result = verify(CHANGED_20, source)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"{elapsed:.2f} s"
    assert 0.0 <= result.score <= 1.0


# --- Critère 5 -------------------------------------------------------------


EXPECTED_VERIFIED = {
    "decision": {
        "Report de la mise en production au lundi 7 décembre.": True,
        "Licences gratuites dès janvier.": False,
        "Budget du lot 2 maintenu.": True,
    },
    "action_item": {
        "Prévenir le support et le client, puis mettre à jour le planning.": True,
        "Suivre le lot 2.": False,
    },
}


def test_c5_agent_sets_verified_on_decisions_and_actions(seeded: dict) -> None:
    llm = FakeLLM(minutes_out("atlas"), minutes_out("comite"))
    outcome = run_minutes_agent(llm)

    assert outcome.processed == 2
    decisions = {row["text"]: row["verified"] for row in rows("decision", meeting_id=1)}
    actions = {row["description"]: row["verified"] for row in rows("action_item", meeting_id=1)}
    assert decisions == EXPECTED_VERIFIED["decision"]
    assert actions == EXPECTED_VERIFIED["action_item"]


def test_c5_check_calls_no_model(seeded: dict) -> None:
    """Le contrôle est déterministe : un seul appel au modèle par réunion (`specs/06 §5`)."""
    llm = FakeLLM(minutes_out("atlas"), minutes_out("comite"))
    run_minutes_agent(llm)

    assert len(llm.calls) == 2


def test_c5_quote_cut_to_200_characters_is_checked_after_the_cut(seeded: dict) -> None:
    """`specs/06 §4.3`, étape 4 : un extrait coupé reste une sous-chaîne de la source."""
    response = minutes_out("atlas")
    long_quote = response.decisions[2].quote
    assert len(long_quote) > 200
    assert verify(long_quote, fixture_source()).found is False
    assert verify(long_quote[:200], fixture_source()).found is True

    run_minutes_agent(FakeLLM(response, minutes_out("comite")))

    stored = rows("decision", meeting_id=1, text=response.decisions[2].text)
    assert len(stored) == 1
    assert len(stored[0]["quote"]) <= 200
    assert stored[0]["verified"] is True


def test_contract_quotes_checked_against_the_whole_meeting_source(seeded: dict) -> None:
    """`specs/06 §4.2` : la source d'une réunion à deux transcripts réunit les deux."""
    run_minutes_agent(FakeLLM(minutes_out("atlas"), minutes_out("comite")))

    assert [row["verified"] for row in rows("decision", meeting_id=2)] == [True]
    assert [row["verified"] for row in rows("action_item", meeting_id=2)] == [True]


# --- Contrat, hors critères numérotés ---------------------------------------


def test_contract_source_shorter_than_quote_is_compared_whole() -> None:
    """`specs/06 §5`, étape 4 : la source entière, comparée à l'extrait."""
    from cabinet.services.quotes import normalize

    source = "Budget validé."
    quote = "Le budget du lot deux est validé par le comité."
    expected = difflib.SequenceMatcher(
        None, normalize(source), normalize(quote), autojunk=False
    ).ratio()

    result = verify(quote, source)

    assert result.found is False
    assert result.score == pytest.approx(expected)


def test_contract_score_is_a_float_between_zero_and_one() -> None:
    for quote in (EXACT_VARIANT, CHANGED_20, INVENTED, "lot 2"):
        result = verify(quote, fixture_source())
        assert isinstance(result.found, bool)
        assert isinstance(result.score, float)
        assert 0.0 <= result.score <= 1.0


def test_contract_single_sequence_matcher_and_quick_ratio_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Précision du ticket et `specs/06 §5`, étape 5.

    Un seul `SequenceMatcher`, créé avec `autojunk=False`, sert à toutes les
    fenêtres. `ratio()` n'est calculé que si `quick_ratio()` dépasse le
    meilleur score : avec l'extrait proche en tête de source, il l'est donc
    bien moins souvent que `quick_ratio()`.
    """
    from cabinet.services import quotes

    created: list[Any] = []
    counters = {"quick_ratio": 0, "ratio": 0}
    real = difflib.SequenceMatcher

    class SpyMatcher(real):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

        def quick_ratio(self) -> float:
            counters["quick_ratio"] += 1
            return super().quick_ratio()

        def ratio(self) -> float:
            counters["ratio"] += 1
            return super().ratio()

    monkeypatch.setattr(difflib, "SequenceMatcher", SpyMatcher)
    if hasattr(quotes, "SequenceMatcher"):
        monkeypatch.setattr(quotes, "SequenceMatcher", SpyMatcher)

    source = ORIGINAL_20 + "\n" + long_source(20_000)
    result = quotes.verify_quote(CHANGED_20, source)

    assert result.found is True
    assert len(created) == 1
    assert created[0].autojunk is False
    assert counters["quick_ratio"] > 10
    assert counters["ratio"] < counters["quick_ratio"] / 2
