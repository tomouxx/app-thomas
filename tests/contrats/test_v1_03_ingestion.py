"""Tests de contrat du ticket V1-03, « Dossier d'entrée et agent d'ingestion ».

Écrits par le relecteur avant la vague. Chaque test travaille dans un
répertoire de données temporaire (`CABINET_HOME`, fixé par `conftest.py`).

Le dossier d'entrée se teste avec un dossier temporaire et le vrai
`FolderInbox` (`specs/09 §3.2`). Pour qu'un fichier ne soit pas écarté comme
« modifié à l'instant », les tests reculent sa date de modification par
`os.utime`. L'agent s'exécute dans `record_run`, comme le ferait le pipeline
(`specs/05 §3.1`), et la table `inbox_item` se relit en SQL par les noms de
`specs/02 §2.2`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Date, text
from sqlalchemy.orm import Session

JEUX = Path(__file__).resolve().parent / "fixtures" / "v1_03"
ZURICH = ZoneInfo("Europe/Zurich")
# Date fixée loin de l'heure réelle : un test qui la suit prouve que le code
# lit `clock.now()` et non l'horloge du système (specs/05 §5.2).
INSTANT_FIXE = datetime(2030, 1, 15, 10, 0, tzinfo=ZURICH)


# --- Outils -----------------------------------------------------------------


def maison() -> Path:
    """Répertoire de données du test, en chemin absolu résolu."""
    return Path(os.environ["CABINET_HOME"]).expanduser().resolve()


def dossier_entree() -> Path:
    racine = maison() / "entree"
    racine.mkdir(parents=True, exist_ok=True)
    return racine


def vieillir(chemin: Path, secondes: float = 60.0, depuis: float | None = None) -> None:
    """Recule la date de modification, pour que le fichier ne soit pas « en cours de copie »."""
    reference = time.time() if depuis is None else depuis
    os.utime(chemin, (reference - secondes, reference - secondes))


def deposer(
    nom: str, contenu: bytes | None = None, age: float = 60.0, depuis: float | None = None
) -> Path:
    """Écrit un fichier synthétique dans `entree/` et recule sa date de modification."""
    chemin = dossier_entree() / nom
    chemin.write_bytes(contenu if contenu is not None else f"contenu fictif de {nom}".encode())
    vieillir(chemin, age, depuis)
    return chemin


def deposer_jeu(nom: str) -> Path:
    """Copie un fichier de `tests/contrats/fixtures/v1_03/` dans `entree/`."""
    chemin = dossier_entree() / nom
    shutil.copyfile(JEUX / nom, chemin)
    vieillir(chemin)
    return chemin


def scanner() -> list[Any]:
    from cabinet.adapters.folder_inbox import FolderInbox

    return FolderInbox(dossier_entree()).scan()


def par_nom(fichiers: list[Any]) -> dict[str, Any]:
    return {Path(fichier.path).name: fichier for fichier in fichiers}


def agent_ingestion() -> Any:
    from cabinet.adapters.folder_inbox import FolderInbox
    from cabinet.agents.ingestion import IngestionAgent

    return IngestionAgent(FolderInbox(dossier_entree()))


def executer(agent: Any) -> Any:
    """Un passage de l'agent, encadré par le journal comme dans `Pipeline.run`."""
    from cabinet.agents.journal import record_run
    from cabinet.db.base import get_engine, init_db

    init_db()
    with Session(get_engine()) as session, record_run(session, agent.name):
        resultat = agent.run(session)
    return resultat


def elements() -> list[tuple]:
    """Relit `inbox_item` dans une session neuve : (path, kind, status, name_date)."""
    from cabinet.db.base import get_engine

    requete = text("SELECT path, kind, status, name_date FROM inbox_item ORDER BY path").columns(
        name_date=Date()
    )
    with Session(get_engine()) as session:
        return [tuple(ligne) for ligne in session.execute(requete)]


def fixer_date(monkeypatch: pytest.MonkeyPatch, valeur: datetime) -> None:
    monkeypatch.setattr("cabinet.clock.now", lambda: valeur)


# Une ligne par cas : nom du fichier, nature, état de départ (specs/05 §5.1).
# Chaque ligne du tableau y figure, avec une extension en minuscules et une en
# majuscules. Le préfixe `app_` se compare lui aussi sans tenir compte de la casse.
CAS_DU_TABLEAU = [
    # .vtt, .docx, tout préfixe : teams_transcript
    ("reunion.vtt", "teams_transcript", "a_rattacher"),
    ("REUNION.VTT", "teams_transcript", "a_rattacher"),
    ("compte-rendu.docx", "teams_transcript", "a_rattacher"),
    ("Compte-Rendu.DOCX", "teams_transcript", "a_rattacher"),
    ("app_reunion.vtt", "teams_transcript", "a_rattacher"),
    # .md, tout préfixe : notes
    ("notes.md", "notes", "a_rattacher"),
    ("NOTES.MD", "notes", "a_rattacher"),
    ("app_notes.md", "notes", "a_rattacher"),
    # .txt sans le préfixe app_ : notes
    ("memo.txt", "notes", "a_rattacher"),
    ("MEMO.TXT", "notes", "a_rattacher"),
    # .m4a, .mp3, .wav, tout préfixe : audio
    ("enregistrement.m4a", "audio", "consentement_requis"),
    ("ENREGISTREMENT.M4A", "audio", "consentement_requis"),
    ("appel.mp3", "audio", "consentement_requis"),
    ("APPEL.MP3", "audio", "consentement_requis"),
    ("dictee.wav", "audio", "consentement_requis"),
    ("DICTEE.WAV", "audio", "consentement_requis"),
    ("app_son.mp3", "audio", "consentement_requis"),
    # .html, .txt avec le préfixe app_ : app_capture_text
    ("app_nov.html", "app_capture_text", "pret"),
    ("app_nov.HTML", "app_capture_text", "pret"),
    ("APP_nov.html", "app_capture_text", "pret"),
    ("app_nov.txt", "app_capture_text", "pret"),
    ("app_nov.TXT", "app_capture_text", "pret"),
    # .png, .jpg, .jpeg avec le préfixe app_ : app_capture_image
    ("app_ecran.png", "app_capture_image", "pret"),
    ("app_ecran.PNG", "app_capture_image", "pret"),
    ("app_ecran.jpg", "app_capture_image", "pret"),
    ("app_ecran.JPG", "app_capture_image", "pret"),
    ("app_ecran.jpeg", "app_capture_image", "pret"),
    ("App_Ecran.JPEG", "app_capture_image", "pret"),
    # tout autre cas : inconnu, dont .html et images sans le préfixe app_
    ("archive.zip", "inconnu", "ignore"),
    ("ARCHIVE.ZIP", "inconnu", "ignore"),
    ("page.html", "inconnu", "ignore"),
    ("image.png", "inconnu", "ignore"),
    ("photo.jpg", "inconnu", "ignore"),
    ("photo.JPEG", "inconnu", "ignore"),
    ("sans-extension", "inconnu", "ignore"),
]


# --- Critère 1 -------------------------------------------------------------


@pytest.mark.parametrize(("nom", "nature", "etat"), CAS_DU_TABLEAU)
def test_c1_nature_selon_le_tableau(nom: str, nature: str, etat: str) -> None:
    deposer(nom)
    fichiers = scanner()
    assert [Path(fichier.path).name for fichier in fichiers] == [nom]
    assert fichiers[0].kind == nature


@pytest.mark.parametrize(("nom", "nature", "etat"), CAS_DU_TABLEAU)
def test_c1_etat_de_depart_selon_le_tableau(nom: str, nature: str, etat: str) -> None:
    deposer(nom)
    executer(agent_ingestion())
    assert elements() == [(f"entree/{nom}", nature, etat, None)]


# --- Critère 2 -------------------------------------------------------------


def test_c2_date_lue_dans_le_nom() -> None:
    deposer_jeu("2026-11-23_point-hebdo.vtt")
    deposer_jeu("notes.md")
    fichiers = par_nom(scanner())
    assert fichiers["2026-11-23_point-hebdo.vtt"].name_date == date(2026, 11, 23)
    assert fichiers["notes.md"].name_date is None


def test_c2_date_stockee_sur_l_element() -> None:
    deposer_jeu("2026-11-23_point-hebdo.vtt")
    deposer_jeu("notes.md")
    executer(agent_ingestion())
    assert elements() == [
        (
            "entree/2026-11-23_point-hebdo.vtt",
            "teams_transcript",
            "a_rattacher",
            date(2026, 11, 23),
        ),
        ("entree/notes.md", "notes", "a_rattacher", None),
    ]


@pytest.mark.parametrize(
    "nom",
    [
        "2026-13-40_point.vtt",  # date impossible
        "2026-11-23-point.vtt",  # pas de tiret bas après la date
        "point_2026-11-23_hebdo.vtt",  # la date n'est pas en tête
        "26-11-23_point.vtt",  # année sur deux chiffres
    ],
)
def test_contrat_date_absente_ou_impossible_donne_none(nom: str) -> None:
    deposer(nom)
    assert scanner()[0].name_date is None


def test_contrat_la_date_vient_du_nom_et_pas_de_la_modification() -> None:
    chemin = deposer("2026-11-23_notes.md")
    ancienne = datetime(2025, 3, 2, 12, 0, tzinfo=ZURICH).timestamp()
    os.utime(chemin, (ancienne, ancienne))
    assert scanner()[0].name_date == date(2026, 11, 23)


# --- Critère 3 -------------------------------------------------------------


def test_c3_fichier_cache_ignore() -> None:
    deposer(".DS_Store")
    deposer(".brouillon.md")
    deposer("notes.md")
    assert [Path(fichier.path).name for fichier in scanner()] == ["notes.md"]


def test_c3_fichier_de_verrou_office_ignore() -> None:
    deposer("~$brouillon.docx")
    deposer("brouillon.docx")
    assert [Path(fichier.path).name for fichier in scanner()] == ["brouillon.docx"]


def test_c3_fichier_modifie_a_l_instant_ignore() -> None:
    chemin = dossier_entree() / "en-cours-de-copie.vtt"
    chemin.write_bytes(b"WEBVTT\n")
    deposer("notes.md")
    assert [Path(fichier.path).name for fichier in scanner()] == ["notes.md"]


def test_c3_l_agent_ne_cree_rien_pour_les_fichiers_ignores() -> None:
    deposer(".cache.md")
    deposer("~$brouillon.docx")
    (dossier_entree() / "en-cours.md").write_bytes(b"# en cours de copie\n")
    resultat = executer(agent_ingestion())
    assert elements() == []
    assert resultat.processed == 0


def test_c3_fichier_ignore_repris_une_fois_la_copie_finie() -> None:
    chemin = dossier_entree() / "en-cours.md"
    chemin.write_bytes(b"# notes fictives\n")
    executer(agent_ingestion())
    assert elements() == []
    vieillir(chemin)
    executer(agent_ingestion())
    assert elements() == [("entree/en-cours.md", "notes", "a_rattacher", None)]


def test_contrat_sous_dossier_ignore() -> None:
    sous_dossier = dossier_entree() / "archives"
    sous_dossier.mkdir()
    (sous_dossier / "ancien.vtt").write_bytes(b"WEBVTT\n")
    vieillir(sous_dossier / "ancien.vtt")
    vieillir(sous_dossier)
    deposer("notes.md")
    assert [Path(fichier.path).name for fichier in scanner()] == ["notes.md"]


def test_contrat_le_delai_de_cinq_secondes_suit_clock_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le délai se mesure avec `clock.now()` (specs/05 §5.2), pas avec l'heure du système."""
    fixer_date(monkeypatch, INSTANT_FIXE)
    reference = INSTANT_FIXE.timestamp()
    deposer("recent.md", age=2, depuis=reference)
    deposer("ancien.md", age=10, depuis=reference)
    assert [Path(fichier.path).name for fichier in scanner()] == ["ancien.md"]


def test_contrat_dossier_absent_donne_une_liste_vide(tmp_path: Path) -> None:
    from cabinet.adapters.folder_inbox import FolderInbox

    assert FolderInbox(tmp_path / "absent").scan() == []


# --- Critère 4 -------------------------------------------------------------


def test_c4_deux_passages_sans_doublon() -> None:
    deposer_jeu("2026-11-23_point-hebdo.vtt")
    deposer_jeu("notes.md")
    deposer_jeu("app_nov.html")
    executer(agent_ingestion())
    premier = elements()
    executer(agent_ingestion())
    assert elements() == premier
    assert len(premier) == 3


def test_contrat_deux_fichiers_identiques_donnent_un_element() -> None:
    deposer("notes.md", b"# notes fictives identiques\n")
    deposer("notes-copie.md", b"# notes fictives identiques\n")
    resultat = executer(agent_ingestion())
    assert resultat.processed == 1
    assert len(elements()) == 1


def test_contrat_un_fichier_renomme_n_est_pas_nouveau() -> None:
    chemin = deposer("notes.md", b"# notes fictives\n")
    executer(agent_ingestion())
    chemin.rename(chemin.with_name("notes-renommees.md"))
    resultat = executer(agent_ingestion())
    assert resultat.processed == 0
    assert elements() == [("entree/notes.md", "notes", "a_rattacher", None)]


# --- Critère 5 -------------------------------------------------------------


def test_c5_etats_de_depart_audio_capture_et_archive() -> None:
    deposer("2026-11-24_comite.m4a", b"\x00\x00\x00\x20ftypM4A synthetique")
    deposer_jeu("app_nov.html")
    deposer("pieces.zip", b"PK\x03\x04 archive synthetique")
    executer(agent_ingestion())
    assert elements() == [
        ("entree/2026-11-24_comite.m4a", "audio", "consentement_requis", date(2026, 11, 24)),
        ("entree/app_nov.html", "app_capture_text", "pret", None),
        ("entree/pieces.zip", "inconnu", "ignore", None),
    ]


# --- Critère 6 -------------------------------------------------------------


def test_c6_processed_compte_les_nouveaux_elements() -> None:
    deposer_jeu("2026-11-23_point-hebdo.vtt")
    deposer_jeu("notes.md")
    deposer_jeu("app_nov.html")
    assert executer(agent_ingestion()).processed == 3
    assert executer(agent_ingestion()).processed == 0
    deposer("2026-11-25_dictee.wav", b"RIFF synthetique")
    assert executer(agent_ingestion()).processed == 1
    assert len(elements()) == 4


def test_c6_fichier_ignore_par_sa_nature_compte_comme_nouvel_element() -> None:
    """Un `.zip` donne un élément `ignore` : c'est un élément créé (specs/05 §5.3)."""
    deposer("pieces.zip", b"PK\x03\x04 archive synthetique")
    assert executer(agent_ingestion()).processed == 1


# --- Contrat, hors critères numérotés ---------------------------------------


def test_contrat_inbox_file_porte_chemin_absolu_empreinte_et_taille() -> None:
    contenu = (JEUX / "notes.md").read_bytes()
    chemin = deposer_jeu("notes.md")
    (fichier,) = scanner()
    assert Path(fichier.path).is_absolute()
    assert Path(fichier.path).resolve() == chemin.resolve()
    assert fichier.sha256 == hashlib.sha256(contenu).hexdigest()
    assert fichier.size == len(contenu)
    assert fichier.kind == "notes"


def test_contrat_scan_trie_par_nom() -> None:
    for nom in ("c-notes.md", "a-notes.md", "b-notes.md"):
        deposer(nom)
    assert [Path(fichier.path).name for fichier in scanner()] == [
        "a-notes.md",
        "b-notes.md",
        "c-notes.md",
    ]


def test_contrat_inbox_file_est_le_modele_du_port() -> None:
    from cabinet.ports.inbox import InboxFile

    deposer("notes.md")
    (fichier,) = scanner()
    assert isinstance(fichier, InboxFile)
    assert set(InboxFile.model_fields) == {"path", "sha256", "kind", "name_date", "size"}


def test_contrat_chemin_relatif_au_repertoire_de_donnees() -> None:
    from cabinet.db.base import get_engine

    deposer_jeu("2026-11-23_point-hebdo.vtt")
    executer(agent_ingestion())
    with Session(get_engine()) as session:
        chemin, empreinte = session.execute(text("SELECT path, sha256 FROM inbox_item")).one()
    assert chemin == "entree/2026-11-23_point-hebdo.vtt"
    attendu = hashlib.sha256((JEUX / "2026-11-23_point-hebdo.vtt").read_bytes()).hexdigest()
    assert empreinte == attendu


def test_contrat_date_de_detection_lue_sur_clock_now(monkeypatch: pytest.MonkeyPatch) -> None:
    from cabinet.db.base import TZDateTime, get_engine

    fixer_date(monkeypatch, INSTANT_FIXE)
    deposer("notes.md", age=60, depuis=INSTANT_FIXE.timestamp())
    executer(agent_ingestion())
    requete = text("SELECT detected_at FROM inbox_item").columns(detected_at=TZDateTime())
    with Session(get_engine()) as session:
        detecte = session.execute(requete).scalar_one()
    assert detecte == INSTANT_FIXE


def test_contrat_l_agent_ne_deplace_ni_ne_modifie_rien() -> None:
    deposer_jeu("2026-11-23_point-hebdo.vtt")
    deposer_jeu("app_nov.html")
    deposer("pieces.zip", b"PK\x03\x04 archive synthetique")
    avant = {chemin.name: chemin.read_bytes() for chemin in sorted(dossier_entree().iterdir())}
    executer(agent_ingestion())
    apres = {chemin.name: chemin.read_bytes() for chemin in sorted(dossier_entree().iterdir())}
    assert apres == avant
    assert not (maison() / "traite").exists()


def test_contrat_agent_nomme_ingestion_sans_modele() -> None:
    deposer("notes.md")
    agent = agent_ingestion()
    assert agent.name == "ingestion"
    resultat = executer(agent)
    assert resultat.errors == 0
    assert resultat.model is None
    assert (resultat.prompt_tokens, resultat.completion_tokens) == (0, 0)


def test_contrat_agent_outcome_et_protocole_agent() -> None:
    """`agents/base.py` définit `Agent` et `AgentOutcome` (specs/01 §3.3, specs/02 §3.4)."""
    from cabinet.agents import base

    assert hasattr(base, "Agent")
    vide = base.AgentOutcome()
    assert (vide.processed, vide.errors) == (0, 0)
    assert vide.model is None
    assert (vide.prompt_tokens, vide.completion_tokens) == (0, 0)
    rempli = base.AgentOutcome(processed=2, errors=1, model="fake", prompt_tokens=10)
    assert (rempli.processed, rempli.errors, rempli.model) == (2, 1, "fake")
    assert (rempli.prompt_tokens, rempli.completion_tokens) == (10, 0)
