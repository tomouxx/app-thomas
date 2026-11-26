"""Tests de contrat du ticket V1-01, « Configuration et répertoire de données ».

Écrits par le relecteur avant la vague. Sections de référence : `specs/03 §1`
à `§3` et `§5`, `specs/04 §2`. Chaque test travaille dans un répertoire de
données temporaire (`CABINET_HOME`, fixé par `conftest.py`).

La commande `cabinet` est lancée dans le processus par le `CliRunner` de
typer, sur l'application `cabinet.cli:app` déclarée dans `pyproject.toml`.
Aucun test ne parle au serveur du modèle : `cabinet llm-test` n'est lancé
qu'avec une adresse distante, refusée avant toute requête, et toute ouverture
de connexion fait échouer le test.
"""

from __future__ import annotations

import re
import socket
from datetime import date
from pathlib import Path
from typing import Any

import pytest

RACINE = Path(__file__).resolve().parents[2]
SOURCES = RACINE / "src" / "cabinet"
DOSSIERS = ("entree", "traite", "brouillons", "config", "data")


def config() -> Any:
    import cabinet.config

    return cabinet.config


def config_error() -> type[Exception]:
    """`ConfigError` s'importe de `llm/types.py` (`specs/02 §3.1`)."""
    from cabinet.llm.types import ConfigError

    return ConfigError


def cabinet(*arguments: str) -> Any:
    """Lance la commande `cabinet` avec ses arguments et rend le résultat."""
    from cabinet.cli import app
    from typer.testing import CliRunner

    return CliRunner().invoke(app, list(arguments))


def fichier_reglages(maison: Path) -> Path:
    return maison / "config" / "reglages.toml"


def ecrire_reglages(maison: Path, contenu: str) -> Path:
    chemin = fichier_reglages(maison)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


def maison_resolue(maison: Path) -> Path:
    """`paths()` rend `CABINET_HOME` absolu par `expanduser().resolve()` (`specs/03 §1`)."""
    return maison.expanduser().resolve()


# --- Critère 1 -------------------------------------------------------------


def test_c1_init_cree_les_cinq_dossiers_et_le_fichier_de_reglages(
    environnement_isole: Path,
) -> None:
    assert not environnement_isole.exists()
    resultat = cabinet("init")
    assert resultat.exit_code == 0, resultat.output
    for dossier in DOSSIERS:
        assert (environnement_isole / dossier).is_dir(), dossier
    assert fichier_reglages(environnement_isole).is_file()


def test_c1_reglages_ecrits_par_init_sont_le_texte_par_defaut(
    environnement_isole: Path,
) -> None:
    """`cabinet init` écrit `DEFAULT_SETTINGS_TOML`, identique à l'exemple du dépôt."""
    assert cabinet("init").exit_code == 0
    ecrit = fichier_reglages(environnement_isole).read_text(encoding="utf-8")
    exemple = (RACINE / "exemples" / "reglages.exemple.toml").read_text(encoding="utf-8")
    assert ecrit == config().DEFAULT_SETTINGS_TOML
    assert ecrit == exemple


# --- Critère 2 -------------------------------------------------------------


def test_c2_second_init_laisse_intact_un_reglages_modifie(environnement_isole: Path) -> None:
    assert cabinet("init").exit_code == 0
    modifie = '# réglages de l\'utilisateur\n[llm]\nmodel = "modele-perso"\n'
    chemin = ecrire_reglages(environnement_isole, modifie)
    resultat = cabinet("init")
    assert resultat.exit_code == 0, resultat.output
    assert chemin.read_bytes() == modifie.encode("utf-8")


def test_c2_init_ne_remplace_pas_un_reglages_present_des_le_premier_appel(
    environnement_isole: Path,
) -> None:
    contenu = "[ui]\nport = 9000\n"
    chemin = ecrire_reglages(environnement_isole, contenu)
    assert cabinet("init").exit_code == 0
    assert chemin.read_text(encoding="utf-8") == contenu
    for dossier in DOSSIERS:
        assert (environnement_isole / dossier).is_dir(), dossier


def test_c2_second_init_ne_vide_pas_les_dossiers(environnement_isole: Path) -> None:
    assert cabinet("init").exit_code == 0
    depose = environnement_isole / "entree" / "2026-11-20_point-projet.vtt"
    depose.write_text("WEBVTT\n", encoding="utf-8")
    assert cabinet("init").exit_code == 0
    assert depose.read_text(encoding="utf-8") == "WEBVTT\n"


# --- Critère 3 -------------------------------------------------------------


def test_c3_sans_fichier_les_valeurs_par_defaut(environnement_isole: Path) -> None:
    reglages = config().load_settings()

    assert reglages.llm.base_url == "http://127.0.0.1:8080/v1"
    assert reglages.llm.model == "qwen-local"
    assert reglages.llm.timeout_s == 900
    assert reglages.llm.context_tokens == 64000

    assert reglages.transcription.model == "mlx-community/whisper-large-v3-mlx"
    assert reglages.transcription.delete_audio_after_validation is True

    assert reglages.cra.deadline is None
    regles = reglages.cra.regles
    assert isinstance(regles, config().RulesSettings)
    assert (regles.R1, regles.R2, regles.R3, regles.R4) == (True, True, True, True)
    assert regles.tolerance_jours == 0.0

    assert reglages.ui.port == 8765


def test_c3_chemin_d_agenda_par_defaut_resolu_dans_le_repertoire_de_donnees(
    environnement_isole: Path,
) -> None:
    reglages = config().load_settings()
    attendu = maison_resolue(environnement_isole) / "config" / "agenda.ics"
    assert Path(reglages.agenda.ics_path) == attendu


# --- Critère 4 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("contenu", "cle"),
    [
        ("[llm]\ntemperature = 0.5\n", "llm.temperature"),
        ("[cra.regles]\nR5 = true\n", "cra.regles.R5"),
        ("[messagerie]\nserveur = \"smtp.exemple.test\"\n", "messagerie"),
    ],
)
def test_c4_cle_inconnue_leve_config_error_qui_la_nomme(
    environnement_isole: Path, contenu: str, cle: str
) -> None:
    ecrire_reglages(environnement_isole, contenu)
    with pytest.raises(config_error()) as erreur:
        config().load_settings()
    assert cle in str(erreur.value)


# --- Critère 5 -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://api.exemple.test/v1",
        "http://127.0.0.1.exemple.test:8080/v1",
        "http://192.168.1.10:8080/v1",
    ],
)
def test_c5_adresse_distante_leve_config_error_au_chargement(
    environnement_isole: Path, url: str
) -> None:
    ecrire_reglages(environnement_isole, f'[llm]\nbase_url = "{url}"\n')
    with pytest.raises(config_error()):
        config().load_settings()


@pytest.mark.parametrize("url", ["http://localhost:4000/v1", "http://[::1]:8080/v1"])
def test_c5_adresse_de_bouclage_acceptee(environnement_isole: Path, url: str) -> None:
    ecrire_reglages(environnement_isole, f'[llm]\nbase_url = "{url}"\n')
    assert config().load_settings().llm.base_url == url


# --- Critère 6 -------------------------------------------------------------


def test_c6_tilde_cabinet_n_apparait_que_dans_config() -> None:
    assert (SOURCES / "config.py").is_file()
    motif = re.compile(r"~/?Cabinet")
    porteurs = [
        chemin.relative_to(SOURCES).as_posix()
        for chemin in sorted(SOURCES.rglob("*.py"))
        if motif.search(chemin.read_text(encoding="utf-8"))
    ]
    assert [nom for nom in porteurs if nom != "config.py"] == []


# --- Contrat, hors critères numérotés : chemins (specs/03 §1) ---------------


def test_contrat_paths_suit_cabinet_home(environnement_isole: Path) -> None:
    chemins = config().paths()
    maison = maison_resolue(environnement_isole)
    assert chemins.home == maison
    assert chemins.inbox == maison / "entree"
    assert chemins.processed == maison / "traite"
    assert chemins.drafts == maison / "brouillons"
    assert chemins.config == maison / "config"
    assert chemins.data == maison / "data"


def test_contrat_paths_porte_les_fichiers_connus(environnement_isole: Path) -> None:
    chemins = config().paths()
    maison = maison_resolue(environnement_isole)
    assert chemins.settings_file == maison / "config" / "reglages.toml"
    assert chemins.roster_file == maison / "config" / "equipe.yaml"
    assert chemins.db_file == maison / "data" / "cabinet.db"
    assert chemins.lock_file == maison / "data" / "pipeline.lock"


def test_contrat_paths_est_un_modele_pydantic() -> None:
    from pydantic import BaseModel

    assert isinstance(config().paths(), BaseModel)


def test_contrat_paths_ne_cree_rien(environnement_isole: Path) -> None:
    config().paths()
    assert not environnement_isole.exists()


def test_contrat_paths_relit_la_variable_a_chaque_appel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_HOME", str(tmp_path / "premier"))
    premier = config().paths().home
    monkeypatch.setenv("CABINET_HOME", str(tmp_path / "second"))
    second = config().paths().home
    assert premier == maison_resolue(tmp_path / "premier")
    assert second == maison_resolue(tmp_path / "second")


def test_contrat_cabinet_home_relatif_rendu_absolu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CABINET_HOME", "donnees-essai")
    maison = config().paths().home
    assert maison.is_absolute()
    assert maison == (tmp_path / "donnees-essai").resolve()


@pytest.mark.parametrize("valeur", [None, ""])
def test_contrat_repli_sur_tilde_cabinet_sans_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valeur: str | None
) -> None:
    """Le répli vaut `~/Cabinet`. `HOME` pointe sur un dossier temporaire : rien n'est lu."""
    faux_domicile = tmp_path / "domicile"
    faux_domicile.mkdir()
    monkeypatch.setenv("HOME", str(faux_domicile))
    if valeur is None:
        monkeypatch.delenv("CABINET_HOME", raising=False)
    else:
        monkeypatch.setenv("CABINET_HOME", valeur)
    assert config().paths().home == faux_domicile / "Cabinet"
    assert list(faux_domicile.iterdir()) == []


def test_contrat_la_base_suit_paths(environnement_isole: Path) -> None:
    """`db/base.py` passe par `paths()` (`specs/05 §2.1`)."""
    from cabinet.db.base import get_engine, init_db

    init_db()
    assert Path(get_engine().url.database).resolve() == config().paths().db_file.resolve()
    assert config().paths().db_file.is_file()


# --- Contrat : chargement des réglages (specs/03 §3.1) ---------------------


def test_contrat_fichier_par_defaut_relu_donne_les_valeurs_par_defaut(
    environnement_isole: Path,
) -> None:
    sans_fichier = config().load_settings()
    ecrire_reglages(environnement_isole, config().DEFAULT_SETTINGS_TOML)
    assert config().load_settings() == sans_fichier


def test_contrat_load_settings_n_ecrit_pas_le_fichier(environnement_isole: Path) -> None:
    """Le fichier appartient à l'utilisateur, il est facultatif (`specs/03 §3` et `§7`)."""
    config().load_settings()
    assert not fichier_reglages(environnement_isole).exists()


def test_contrat_fichier_partiel_complete_par_les_valeurs_par_defaut(
    environnement_isole: Path,
) -> None:
    ecrire_reglages(environnement_isole, '[llm]\nmodel = "modele-essai"\n')
    reglages = config().load_settings()
    assert reglages.llm.model == "modele-essai"
    assert reglages.llm.base_url == "http://127.0.0.1:8080/v1"
    assert reglages.llm.timeout_s == 900
    assert reglages.ui.port == 8765


def test_contrat_echeance_lue_comme_une_date(environnement_isole: Path) -> None:
    ecrire_reglages(environnement_isole, "[cra]\ndeadline = 2026-11-30\n")
    assert config().load_settings().cra.deadline == date(2026, 11, 30)


def test_contrat_regles_lues_du_fichier(environnement_isole: Path) -> None:
    ecrire_reglages(environnement_isole, "[cra.regles]\nR2 = false\ntolerance_jours = 0.5\n")
    regles = config().load_settings().cra.regles
    assert regles.R2 is False
    assert regles.R1 is True
    assert regles.tolerance_jours == 0.5


def test_contrat_chemin_d_agenda_relatif_resolu(environnement_isole: Path) -> None:
    ecrire_reglages(environnement_isole, '[agenda]\nics_path = "config/autre.ics"\n')
    attendu = maison_resolue(environnement_isole) / "config" / "autre.ics"
    assert Path(config().load_settings().agenda.ics_path) == attendu


def test_contrat_chemin_d_agenda_absolu_garde(
    environnement_isole: Path, tmp_path: Path
) -> None:
    absolu = tmp_path / "ailleurs" / "agenda.ics"
    ecrire_reglages(environnement_isole, f'[agenda]\nics_path = "{absolu.as_posix()}"\n')
    assert Path(config().load_settings().agenda.ics_path) == absolu


def test_contrat_aucun_cache_chaque_appel_relit_le_fichier(environnement_isole: Path) -> None:
    ecrire_reglages(environnement_isole, '[llm]\nmodel = "premier"\n')
    assert config().load_settings().llm.model == "premier"
    ecrire_reglages(environnement_isole, '[llm]\nmodel = "second"\n')
    assert config().load_settings().llm.model == "second"


def test_contrat_message_exact_de_la_cle_inconnue(environnement_isole: Path) -> None:
    ecrire_reglages(environnement_isole, "[cra.regles]\nR5 = true\n")
    with pytest.raises(config_error()) as erreur:
        config().load_settings()
    assert str(erreur.value) == "Clé inconnue dans reglages.toml : cra.regles.R5."


def test_contrat_fichier_illisible_leve_config_error(environnement_isole: Path) -> None:
    ecrire_reglages(environnement_isole, "[llm\nmodel = \n")
    with pytest.raises(config_error()) as erreur:
        config().load_settings()
    assert str(erreur.value).startswith("reglages.toml est illisible : ")


def test_contrat_valeur_invalide_leve_config_error_qui_nomme_la_cle(
    environnement_isole: Path,
) -> None:
    ecrire_reglages(environnement_isole, '[llm]\ntimeout_s = "longtemps"\n')
    with pytest.raises(config_error()) as erreur:
        config().load_settings()
    assert str(erreur.value).startswith("Valeur invalide pour llm.timeout_s : ")


def test_contrat_adresse_distante_nommee_dans_le_message(environnement_isole: Path) -> None:
    ecrire_reglages(environnement_isole, '[llm]\nbase_url = "https://api.exemple.test/v1"\n')
    with pytest.raises(config_error()) as erreur:
        config().load_settings()
    assert "api.exemple.test" in str(erreur.value)


def test_contrat_variable_d_environnement_leve_la_garde_au_chargement(
    environnement_isole: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`load_settings` passe par `assert_loopback`, qui lit `CABINET_ALLOW_REMOTE_LLM`."""
    url = "https://api.exemple.test/v1"
    ecrire_reglages(environnement_isole, f'[llm]\nbase_url = "{url}"\n')
    monkeypatch.setenv("CABINET_ALLOW_REMOTE_LLM", "1")
    assert config().load_settings().llm.base_url == url


# --- Contrat : ligne de commande (specs/03 §5) ------------------------------


def test_contrat_init_affiche_le_repertoire_et_ce_qu_il_cree(environnement_isole: Path) -> None:
    premier = cabinet("init")
    assert premier.exit_code == 0
    assert str(maison_resolue(environnement_isole)) in premier.output
    assert "reglages.toml" in premier.output

    second = cabinet("init")
    assert second.exit_code == 0
    assert str(maison_resolue(environnement_isole)) in second.output


def test_contrat_init_ne_cree_ni_equipe_ni_agenda(environnement_isole: Path) -> None:
    assert cabinet("init").exit_code == 0
    assert not (environnement_isole / "config" / "equipe.yaml").exists()
    assert not (environnement_isole / "config" / "agenda.ics").exists()


def test_contrat_llm_test_lit_l_adresse_dans_les_reglages(
    environnement_isole: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cabinet llm-test` prend ses valeurs dans `load_settings()` (`specs/03 §5.1`).

    L'adresse distante est refusée avant toute requête : code 1, message sur
    la sortie d'erreur. Une tentative de connexion fait échouer le test.
    """
    tentatives: list[object] = []

    def connexion_interdite(*args: object, **kwargs: object) -> None:
        tentatives.append(args)
        raise OSError("connexion interdite pendant les tests de contrat")

    monkeypatch.setattr(socket.socket, "connect", connexion_interdite)
    monkeypatch.setattr(socket, "create_connection", connexion_interdite)
    ecrire_reglages(environnement_isole, '[llm]\nbase_url = "https://api.exemple.test/v1"\n')

    resultat = cabinet("llm-test")
    assert tentatives == []
    assert resultat.exit_code == 1
    assert "api.exemple.test" in resultat.stderr
