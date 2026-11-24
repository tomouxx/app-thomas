"""Réglages communs des tests de contrat. Écrit par le relecteur.

Ce fichier fait trois choses, décrites dans `docs/REPRISE.md` (section 5).

1. Il écarte de la collecte les tests de contrat des tickets qui ne sont ni
   « fait » ni en cours, pour que `make check` puisse passer ticket par ticket.
2. Il isole chaque test : `CABINET_HOME` pointe sur un répertoire temporaire
   et la garde de bouclage n'est jamais levée par l'environnement.
3. Il écrit, si `CABINET_RAPPORT_CONTRATS` est fixée, le décompte des tests de
   contrat par ticket. La boucle s'en sert pour vérifier que les contrats du
   ticket en cours ont bien tourné.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
_RE_FICHIER = re.compile(r"^test_v(\d+)_(c?\d+)_", re.IGNORECASE)
_RE_LIGNE = re.compile(r"^\|\s*(V\d+-(?:\d+|C\d+))\s*\|.*\|\s*fait\s*\|[^|]*\|[^|]*\|\s*$")
_decompte: dict[str, dict[str, int]] = {}


def ticket_du_fichier(nom: str) -> str | None:
    """`test_v0_02_client_llm.py` appartient au ticket `V0-02`."""
    trouve = _RE_FICHIER.match(nom)
    if trouve is None:
        return None
    return f"V{trouve.group(1)}-{trouve.group(2).upper()}"


def tickets_faits() -> set[str]:
    """Tickets à l'état « fait » dans `tickets/ETAT.md`."""
    etat = RACINE / "tickets" / "ETAT.md"
    if not etat.exists():
        return set()
    faits = set()
    for ligne in etat.read_text(encoding="utf-8").splitlines():
        trouve = _RE_LIGNE.match(ligne.strip())
        if trouve is not None:
            faits.add(trouve.group(1))
    return faits


def est_actif(ticket: str) -> bool:
    if os.environ.get("CABINET_CONTRATS") == "tous":
        return True
    if os.environ.get("CABINET_TICKET") == ticket:
        return True
    return ticket in tickets_faits()


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if collection_path.suffix != ".py" or collection_path.parent != Path(__file__).parent:
        return None
    ticket = ticket_du_fichier(collection_path.name)
    if ticket is None or est_actif(ticket):
        return None
    return True


def _compter(identifiant: str, cle: str) -> None:
    chemin = identifiant.split("::")[0].replace("\\", "/")
    if "tests/contrats/" not in f"/{chemin}":
        return
    ticket = ticket_du_fichier(Path(chemin).name)
    if ticket is None:
        return
    cases = _decompte.setdefault(
        ticket, {"reussis": 0, "echoues": 0, "ignores": 0, "erreurs_collecte": 0}
    )
    cases[cle] += 1


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.failed:
        _compter(report.nodeid, "echoues")
    elif report.skipped:
        _compter(report.nodeid, "ignores")
    elif report.when == "call":
        _compter(report.nodeid, "reussis")


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        _compter(report.nodeid, "erreurs_collecte")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    chemin = os.environ.get("CABINET_RAPPORT_CONTRATS")
    if chemin:
        Path(chemin).write_text(json.dumps(_decompte, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(autouse=True)
def environnement_isole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Aucun test de contrat ne touche un vrai répertoire de données."""
    maison = tmp_path / "cabinet-home"
    monkeypatch.setenv("CABINET_HOME", str(maison))
    monkeypatch.delenv("CABINET_ALLOW_REMOTE_LLM", raising=False)
    return maison
