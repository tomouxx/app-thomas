"""Tests unitaires du squelette : route /sante, application et commande version."""

from __future__ import annotations

from importlib.metadata import version

from fastapi.testclient import TestClient
from typer.testing import CliRunner

import cabinet
from cabinet.cli import app as cli_app
from cabinet.web.app import create_app


def test_sante_renvoie_200_et_le_json_du_contrat() -> None:
    """GET /sante renvoie 200 et le JSON du contrat."""
    reponse = TestClient(create_app()).get("/sante")
    assert reponse.status_code == 200
    assert reponse.json() == {"statut": "ok", "version": version("cabinet")}


def test_create_app_desactive_les_pages_de_documentation() -> None:
    """Les pages de documentation de FastAPI sont désactivées."""
    application = create_app()
    assert application.docs_url is None
    assert application.redoc_url is None
    assert application.openapi_url is None


def test_version_du_paquet_vient_des_metadonnees() -> None:
    """__version__ est lue dans les métadonnées du paquet."""
    assert cabinet.__version__ == version("cabinet")


def test_commande_version_affiche_la_version() -> None:
    """`cabinet version` affiche la version du paquet."""
    resultat = CliRunner().invoke(cli_app, ["version"])
    assert resultat.exit_code == 0
    assert version("cabinet") in resultat.output
