"""Réglages communs des tests unitaires.

Depuis V0-03, `create_app()` appelle `init_db()`, qui crée la base sous
`CABINET_HOME`. Chaque test unitaire travaille donc dans un répertoire de
données temporaire, comme les tests de contrat.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def environnement_isole(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucun test unitaire ne touche un vrai répertoire de données."""
    monkeypatch.setenv("CABINET_HOME", str(tmp_path / "cabinet-home"))
    monkeypatch.delenv("CABINET_ALLOW_REMOTE_LLM", raising=False)
