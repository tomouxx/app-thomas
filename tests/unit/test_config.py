"""Tests unitaires de la configuration (specs/03 §1 à §3).

Les tests de contrat couvrent les critères du ticket V1-01 ; ce fichier
couvre les cas que `specs/03 §3.2` attend de lui, en particulier
`set_cra_deadline`, qui n'a pas de test de contrat. Chaque test travaille
dans un répertoire de données temporaire (conftest.py).
"""

import socket
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cabinet.config import DEFAULT_SETTINGS_TOML, load_settings, paths, set_cra_deadline
from cabinet.llm.types import ConfigError

RACINE = Path(__file__).resolve().parents[2]


def write_settings(content: str) -> Path:
    """Écrit reglages.toml dans le répertoire de données isolé du test."""
    path = paths().settings_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def settings_content() -> str:
    return paths().settings_file.read_text(encoding="utf-8")


def cabinet(*arguments: str) -> Any:
    """Lance la commande `cabinet` dans le processus, sans serveur de modèle."""
    from typer.testing import CliRunner

    from cabinet.cli import app

    return CliRunner().invoke(app, list(arguments))


# --- load_settings (specs/03 §3.1) -----------------------------------------


def test_sans_fichier_les_valeurs_par_defaut() -> None:
    settings = load_settings()
    assert settings.llm.base_url == "http://127.0.0.1:8080/v1"
    assert settings.llm.model == "qwen-local"
    assert settings.llm.timeout_s == 900
    assert settings.cra.deadline is None
    assert settings.ui.port == 8765


def test_cle_inconnue_nommee_dans_le_message() -> None:
    write_settings("[ui]\nhost = \"127.0.0.1\"\n")
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    assert "ui.host" in str(excinfo.value)


def test_adresse_distante_refusee_au_chargement() -> None:
    write_settings('[llm]\nbase_url = "https://api.exemple.test/v1"\n')
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    assert "api.exemple.test" in str(excinfo.value)


def test_chemin_d_agenda_relatif_resolu_dans_le_repertoire_de_donnees() -> None:
    write_settings('[agenda]\nics_path = "config/autre.ics"\n')
    assert load_settings().agenda.ics_path == paths().home / "config" / "autre.ics"


# --- set_cra_deadline (specs/03 §3.2) ---------------------------------------


def test_echeance_remplacee_puis_fichier_compare_ligne_a_ligne() -> None:
    original = (
        "# réglages de l'utilisateur\n"
        "[llm]\nmodel = \"modele-perso\"\n\n"
        "[cra]\ndeadline = 2026-11-30\n# commentaire de la section\n\n"
        "[ui]\nport = 9000\n"
    )
    write_settings(original)
    set_cra_deadline(date(2026, 12, 15))
    expected = original.replace("deadline = 2026-11-30", "deadline = 2026-12-15")
    assert settings_content() == expected


def test_echeance_ajoutee_quand_la_ligne_est_commentee() -> None:
    original = "[cra]\n# deadline = 2026-11-30\n\n[ui]\nport = 8765\n"
    write_settings(original)
    set_cra_deadline(date(2026, 12, 15))
    expected = "[cra]\ndeadline = 2026-12-15\n# deadline = 2026-11-30\n\n[ui]\nport = 8765\n"
    assert settings_content() == expected


def test_echeance_supprimee_avec_none_sans_toucher_aux_autres_lignes() -> None:
    original = "[cra]\ndeadline = 2026-11-30\n# commentaire conservé\n\n[ui]\nport = 8765\n"
    write_settings(original)
    set_cra_deadline(None)
    expected = "[cra]\n# commentaire conservé\n\n[ui]\nport = 8765\n"
    assert settings_content() == expected


def test_echeance_ajoutee_a_la_fin_quand_la_section_cra_est_absente() -> None:
    original = "[ui]\nport = 8765\n"
    write_settings(original)
    set_cra_deadline(date(2026, 12, 15))
    expected = "[ui]\nport = 8765\n\n[cra]\ndeadline = 2026-12-15\n"
    assert settings_content() == expected


def test_echeance_sans_fichier_part_du_texte_par_defaut() -> None:
    set_cra_deadline(date(2026, 12, 15))
    assert "deadline = 2026-12-15" in settings_content()


def test_echeance_absente_avec_none_ne_cree_pas_de_fichier() -> None:
    set_cra_deadline(None)
    assert not paths().settings_file.exists()


# --- paths (specs/03 §1) ----------------------------------------------------


def test_paths_construit_les_cinq_sous_dossiers() -> None:
    chemins = paths()
    assert chemins.inbox == chemins.home / "entree"
    assert chemins.processed == chemins.home / "traite"
    assert chemins.drafts == chemins.home / "brouillons"
    assert chemins.config == chemins.home / "config"
    assert chemins.data == chemins.home / "data"


def test_paths_porte_les_fichiers_connus() -> None:
    chemins = paths()
    assert chemins.settings_file == chemins.config / "reglages.toml"
    assert chemins.roster_file == chemins.config / "equipe.yaml"
    assert chemins.db_file == chemins.data / "cabinet.db"
    assert chemins.lock_file == chemins.data / "pipeline.lock"


def test_paths_ne_cree_rien_sur_le_disque(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    maison = tmp_path / "maison"
    monkeypatch.setenv("CABINET_HOME", str(maison))
    paths()
    assert not maison.exists()


def test_paths_rend_absolu_un_cabinet_home_relatif(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CABINET_HOME", "donnees")
    assert paths().home == (tmp_path / "donnees").resolve()


def test_paths_relit_la_variable_a_chaque_appel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_HOME", str(tmp_path / "premier"))
    premier = paths().home
    monkeypatch.setenv("CABINET_HOME", str(tmp_path / "second"))
    assert paths().home == (tmp_path / "second").resolve()
    assert premier == (tmp_path / "premier").resolve()


def test_paths_repli_sur_tilde_cabinet_sans_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    faux_domicile = tmp_path / "domicile"
    faux_domicile.mkdir()
    monkeypatch.setenv("HOME", str(faux_domicile))
    monkeypatch.delenv("CABINET_HOME")
    assert paths().home == faux_domicile / "Cabinet"
    assert list(faux_domicile.iterdir()) == []


# --- cabinet init (specs/03 §5.2) -------------------------------------------


def test_init_cree_les_cinq_dossiers_et_le_fichier_par_defaut() -> None:
    resultat = cabinet("init")
    assert resultat.exit_code == 0, resultat.output
    for nom in ("entree", "traite", "brouillons", "config", "data"):
        assert (paths().home / nom).is_dir(), nom
    assert paths().settings_file.is_file()


def test_init_ecrit_le_texte_par_defaut_identique_a_l_exemple_du_depot() -> None:
    assert cabinet("init").exit_code == 0
    ecrit = paths().settings_file.read_text(encoding="utf-8")
    exemple = (RACINE / "exemples" / "reglages.exemple.toml").read_text(encoding="utf-8")
    assert ecrit == DEFAULT_SETTINGS_TOML
    assert ecrit == exemple


def test_second_init_laisse_intact_un_reglages_modifie() -> None:
    assert cabinet("init").exit_code == 0
    modifie = "[llm]\nmodel = \"modele-perso\"\n"
    write_settings(modifie)
    assert cabinet("init").exit_code == 0
    assert settings_content() == modifie


def test_init_ne_vide_pas_les_dossiers() -> None:
    assert cabinet("init").exit_code == 0
    depose = paths().inbox / "2026-11-20_point-projet.vtt"
    depose.write_text("WEBVTT\n", encoding="utf-8")
    assert cabinet("init").exit_code == 0
    assert depose.read_text(encoding="utf-8") == "WEBVTT\n"


def test_init_affiche_le_repertoire_et_le_fichier_cree() -> None:
    resultat = cabinet("init")
    assert str(paths().home) in resultat.output
    assert "reglages.toml" in resultat.output


def test_init_ne_cree_ni_equipe_ni_agenda() -> None:
    assert cabinet("init").exit_code == 0
    assert not paths().roster_file.exists()
    assert not (paths().config / "agenda.ics").exists()


# --- cabinet llm-test (specs/03 §5.1) ----------------------------------------


def test_llm_test_refuse_une_adresse_distante_avant_toute_connexion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tentatives: list[object] = []

    def connexion_interdite(*args: object, **kwargs: object) -> None:
        tentatives.append(args)
        raise OSError("connexion interdite pendant les tests")

    monkeypatch.setattr(socket.socket, "connect", connexion_interdite)
    monkeypatch.setattr(socket, "create_connection", connexion_interdite)
    write_settings('[llm]\nbase_url = "https://api.exemple.test/v1"\n')
    resultat = cabinet("llm-test")
    assert tentatives == []
    assert resultat.exit_code == 1
    assert "api.exemple.test" in resultat.stderr
