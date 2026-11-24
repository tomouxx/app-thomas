"""Tests de contrat du ticket V0-01, « Squelette du dépôt ».

Écrits par le relecteur avant la vague. Un test `test_cN_*` par critère
d'acceptation numéroté du ticket.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

from fastapi.testclient import TestClient

RACINE = Path(__file__).resolve().parents[2]
VERSION = "0.1.0"


def recette(cible: str) -> str:
    """Commandes que `make <cible>` lancerait, sans les exécuter.

    `-n` affiche sans exécuter, `-B` ignore ce qui est déjà construit.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAKE", "MFLAGS"))}
    res = subprocess.run(
        ["make", "-n", "-B", cible],
        cwd=RACINE,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, f"`make -n -B {cible}` échoue : {res.stderr}"
    return res.stdout


def test_c1_cible_install_cree_venv_et_installe_les_dependances_de_dev() -> None:
    """Critère 1, partie vérifiable sans réseau : la forme de la cible `install`."""
    commandes = recette("install")
    assert ".venv" in commandes
    assert re.search(r"pip\s+install\s+-e\s+[\"']?\.\[dev\][\"']?", commandes), commandes


def test_c1_pyproject_declare_le_paquet_et_les_dependances_de_dev() -> None:
    projet = tomllib.loads((RACINE / "pyproject.toml").read_text(encoding="utf-8"))
    assert projet["project"]["name"] == "cabinet"
    assert projet["project"]["version"] == VERSION
    dev = " ".join(projet["project"]["optional-dependencies"]["dev"])
    assert "pytest" in dev
    assert "ruff" in dev
    assert (RACINE / "src" / "cabinet" / "__init__.py").is_file()


def test_c2_cible_check_lance_ruff_puis_pytest() -> None:
    commandes = recette("check")
    assert "ruff check ." in commandes, commandes
    assert "pytest -q" in commandes, commandes
    assert commandes.index("ruff check .") < commandes.index("pytest -q")


def test_c3_sante_renvoie_200_et_le_json_du_contrat() -> None:
    from cabinet.web.app import create_app

    reponse = TestClient(create_app()).get("/sante")
    assert reponse.status_code == 200
    assert reponse.json() == {"statut": "ok", "version": VERSION}


def test_c3_version_de_sante_egale_celle_des_metadonnees_du_paquet() -> None:
    from cabinet.web.app import create_app

    assert metadata.version("cabinet") == VERSION
    reponse = TestClient(create_app()).get("/sante")
    assert reponse.json()["version"] == metadata.version("cabinet")


def test_c4_commande_cabinet_version_affiche_la_version() -> None:
    commande = Path(sys.executable).parent / "cabinet"
    assert commande.exists(), "la commande `cabinet` n'est pas installée dans l'environnement"
    res = subprocess.run(
        [str(commande), "version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert VERSION in res.stdout


def test_c5_cible_run_lie_le_serveur_a_la_seule_adresse_de_bouclage() -> None:
    texte = (RACINE / "Makefile").read_text(encoding="utf-8")
    assert "0.0.0.0" not in texte
    commandes = recette("run")
    assert "uvicorn" in commandes, commandes
    assert re.search(r"--host[ =]127\.0\.0\.1\b", commandes), commandes
    assert re.search(r"--port[ =]8765\b", commandes), commandes
    assert "0.0.0.0" not in commandes


def test_c6_gitignore_exclut_les_fichiers_locaux() -> None:
    lignes = (RACINE / ".gitignore").read_text(encoding="utf-8").splitlines()
    motifs = {ligne.strip().strip("/") for ligne in lignes}
    for attendu in (".venv", "__pycache__", "*.db", ".pytest_cache"):
        assert attendu in motifs, f"`{attendu}` manque dans .gitignore"
