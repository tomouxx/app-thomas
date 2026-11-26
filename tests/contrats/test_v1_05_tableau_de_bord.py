"""Tests de contrat du ticket V1-05, « Gabarit web et tableau de bord ».

Écrits par le relecteur avant la vague. Un test `test_cN_*` par critère
d'acceptation numéroté, des tests `test_contrat_*` pour les précisions de
`specs/08 §1` à `§2.4`.

Les jeux de données sont dans `fixtures/v1_05/`. Ils sont écrits dans la base
par les noms de tables et de colonnes de `specs/02 §2`, sur des tables relues
par réflexion : la spécification ne nomme pas toutes les classes du modèle.
La date du jour est fixée par `cabinet.clock.now` (`specs/09 §3.3`).

Les pages sont jugées sur leur texte visible, pas sur leur mise en forme :
code HTTP, textes cités par la spécification, liens et champs de formulaire.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, update
from sqlalchemy.sql import sqltypes

FIXTURES = Path(__file__).parent / "fixtures" / "v1_05"
ZURICH = ZoneInfo("Europe/Zurich")
AUJOURD_HUI = datetime(2026, 11, 24, 8, 0, tzinfo=ZURICH)

NAVIGATION = [
    ("Tableau de bord", "/"),
    ("Entrée", "/entree"),
    ("Réunions", "/reunions"),
    ("Actions", "/actions"),
    ("Imputations", "/imputations"),
    ("Brouillons", "/brouillons"),
    ("Journal", "/journal"),
    ("Réglages", "/reglages"),
]

# --- Base de données ---------------------------------------------------------

ORDRE_DES_TABLES = (
    "series",
    "event",
    "meeting",
    "minutes",
    "action_item",
    "inbox_item",
    "draft",
)

DEFAUTS: dict[str, dict[str, Any]] = {
    "series": {"followed": False, "client": None},
    "event": {
        "all_day": False,
        "location": None,
        "organizer": None,
        "attendees": [],
        "cancelled": False,
    },
    "meeting": {"event_id": None},
    "minutes": {
        "content_md": "",
        "summary": "Résumé synthétique.",
        "open_points": [],
        "next_meeting": None,
        "created_at": "2026-11-20T12:00:00+01:00",
    },
    "action_item": {
        "owner": None,
        "due_date": None,
        "quote": "Extrait synthétique.",
        "verified": True,
    },
    "inbox_item": {
        "name_date": None,
        "meeting_id": None,
        "consent_confirmed": False,
        "consent_at": None,
        "error": None,
        "detected_at": "2026-11-20T09:00:00+01:00",
    },
    "draft": {
        "recipients": ["alice.martin@exemple.test"],
        "subject": "Objet synthétique",
        "body": "Corps synthétique.",
        "meeting_id": None,
        "capture_id": None,
        "created_at": "2026-11-20T12:00:00+01:00",
    },
}


def initialiser() -> None:
    from cabinet.db.base import init_db

    init_db()


def table(nom: str) -> Table:
    from cabinet.db.base import get_engine

    return Table(nom, MetaData(), autoload_with=get_engine())


def convertir(colonne: Any, valeur: Any) -> Any:
    """Valeur du jeu de données vers la forme stockée (`specs/02 §1.1`)."""
    if valeur is None:
        return None
    if isinstance(colonne.type, sqltypes.DateTime):
        if isinstance(valeur, str):
            valeur = datetime.fromisoformat(valeur)
        assert valeur.tzinfo is not None, "une date et heure du jeu doit porter son fuseau"
        return valeur.astimezone(UTC).replace(tzinfo=None)
    if isinstance(colonne.type, sqltypes.Date) and isinstance(valeur, str):
        return date.fromisoformat(valeur)
    return valeur


def inserer(jeu: dict[str, list[dict[str, Any]]]) -> None:
    """Écrit les lignes du jeu, table par table, dans l'ordre des clés étrangères."""
    from cabinet.db.base import get_engine

    initialiser()
    with get_engine().begin() as connexion:
        for nom in ORDRE_DES_TABLES:
            if nom not in jeu:
                continue
            cible = table(nom)
            for brute in jeu[nom]:
                ligne = {**DEFAUTS.get(nom, {}), **brute}
                if nom == "inbox_item":
                    ligne.setdefault("sha256", sha256(ligne["path"].encode()).hexdigest())
                if nom == "event":
                    ligne.setdefault("end", ligne["start"])
                valeurs = {cle: convertir(cible.c[cle], val) for cle, val in ligne.items()}
                connexion.execute(cible.insert().values(**valeurs))


def charger(nom: str) -> dict[str, list[dict[str, Any]]]:
    return json.loads((FIXTURES / nom).read_text(encoding="utf-8"))


def ecrire_reglages(maison: Path, contenu: str) -> None:
    dossier = maison / "config"
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "reglages.toml").write_text(contenu, encoding="utf-8")


# --- Pages -------------------------------------------------------------------


class Lecteur(HTMLParser):
    """Texte visible, liens et balises d'une page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.morceaux: list[str] = []
        self.liens: list[tuple[str, str]] = []
        self.balises: list[tuple[str, dict[str, str | None]]] = []
        self._lien: tuple[str, list[str]] | None = None
        self._masque = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributs = dict(attrs)
        self.balises.append((tag, attributs))
        if tag in ("script", "style"):
            self._masque += 1
        if tag == "a":
            self._lien = (attributs.get("href") or "", [])

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._masque:
            self._masque -= 1
        if tag == "a" and self._lien is not None:
            href, morceaux = self._lien
            self.liens.append((href, " ".join("".join(morceaux).split())))
            self._lien = None

    def handle_data(self, data: str) -> None:
        if self._masque:
            return
        self.morceaux.append(data)
        if self._lien is not None:
            self._lien[1].append(data)

    @property
    def texte(self) -> str:
        brut = " ".join(" ".join(self.morceaux).split())
        return brut.replace("’", "'")


def lire(html: str) -> Lecteur:
    lecteur = Lecteur()
    lecteur.feed(html)
    lecteur.close()
    return lecteur


@pytest.fixture
def navigateur() -> Iterator[TestClient]:
    from cabinet.web.app import create_app

    with TestClient(create_app()) as client:
        yield client


def page(navigateur: TestClient, chemin: str) -> Lecteur:
    reponse = navigateur.get(chemin)
    assert reponse.status_code == 200, f"GET {chemin} : {reponse.status_code}"
    return lire(reponse.text)


def chemin_du_lien(href: str) -> str:
    return urlsplit(href).path or "/"


def valeurs_du_compteur(texte: str, libelle: str) -> set[int]:
    """Nombres placés juste après ou juste avant le libellé d'un compteur."""
    motif = re.escape(libelle)
    trouves: set[int] = set()
    apres = re.search(motif + r"\W{0,12}?(\d+)", texte, re.IGNORECASE)
    avant = re.search(r"(\d+)\W{0,12}?" + motif, texte, re.IGNORECASE)
    for resultat in (apres, avant):
        if resultat is not None:
            trouves.add(int(resultat.group(1)))
    return trouves


@pytest.fixture
def fixer_date(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    def fixer(valeur: datetime) -> None:
        monkeypatch.setattr("cabinet.clock.now", lambda: valeur)

    return fixer


@pytest.fixture(autouse=True)
def date_du_jour(fixer_date: Callable[[datetime], None]) -> None:
    """Mardi 24.11.2026, 08:00 à Zurich, sauf si le test fixe une autre date."""
    fixer_date(AUJOURD_HUI)


# --- Critère 1 -------------------------------------------------------------


def test_c1_tableau_de_bord_repond_200_sur_une_base_vide(navigateur: TestClient) -> None:
    reponse = navigateur.get("/")
    assert reponse.status_code == 200
    assert "text/html" in reponse.headers["content-type"]


def test_c1_tableau_de_bord_repond_200_sans_fichier_de_reglages(
    environnement_isole: Path, navigateur: TestClient
) -> None:
    assert not (environnement_isole / "config" / "reglages.toml").exists()
    assert navigateur.get("/").status_code == 200


# --- Critère 2 -------------------------------------------------------------


def test_c2_huit_liens_de_navigation_sur_le_tableau_de_bord_et_le_journal(
    navigateur: TestClient,
) -> None:
    for adresse in ("/", "/journal"):
        liens = page(navigateur, adresse).liens
        presents = {(texte, chemin_du_lien(href)) for href, texte in liens}
        for libelle, chemin in NAVIGATION:
            assert (libelle, chemin) in presents, (
                f"lien « {libelle} » ({chemin}) absent de {adresse}"
            )


# --- Critère 3 -------------------------------------------------------------


def test_c3_quatre_compteurs_conformes_a_la_base(navigateur: TestClient) -> None:
    inserer(charger("compteurs.json"))
    texte = page(navigateur, "/").texte
    attendus = {
        "Fichiers à traiter": 3,
        "Brouillons à relire": 2,
        "Actions ouvertes": 4,
        "Comptes rendus à relire": 1,
    }
    for libelle, nombre in attendus.items():
        valeurs = valeurs_du_compteur(texte, libelle)
        assert nombre in valeurs, f"compteur « {libelle} » : attendu {nombre}, lu {valeurs}"


def test_c3_compteurs_a_zero_sur_une_base_vide(navigateur: TestClient) -> None:
    texte = page(navigateur, "/").texte
    for libelle in (
        "Fichiers à traiter",
        "Brouillons à relire",
        "Actions ouvertes",
        "Comptes rendus à relire",
    ):
        assert 0 in valeurs_du_compteur(texte, libelle), f"compteur « {libelle} » absent ou non nul"


def test_c3_compteurs_suivent_la_base(navigateur: TestClient) -> None:
    """Un élément de plus à rattacher et un brouillon de plus changent les compteurs."""
    jeu = charger("compteurs.json")
    jeu["inbox_item"].append(
        {"id": 8, "path": "entree/notes-beta.md", "kind": "notes", "status": "a_rattacher"}
    )
    jeu["draft"].append({"id": 6, "kind": "envoi_cr", "status": "brouillon"})
    inserer(jeu)
    texte = page(navigateur, "/").texte
    assert 4 in valeurs_du_compteur(texte, "Fichiers à traiter")
    assert 3 in valeurs_du_compteur(texte, "Brouillons à relire")


# --- Critère 4 -------------------------------------------------------------


def test_c4_echeance_dans_trois_jours_affiche_j_moins_3(
    environnement_isole: Path, navigateur: TestClient
) -> None:
    ecrire_reglages(environnement_isole, "[cra]\ndeadline = 2026-11-27\n")
    texte = page(navigateur, "/").texte
    assert "J-3" in texte
    assert "échéance dépassée" not in texte


def test_c4_echeance_le_jour_meme_affiche_aujourd_hui(
    environnement_isole: Path, navigateur: TestClient
) -> None:
    ecrire_reglages(environnement_isole, "[cra]\ndeadline = 2026-11-24\n")
    texte = page(navigateur, "/").texte
    assert "aujourd'hui" in texte
    assert re.search(r"J-\d", texte) is None
    assert "échéance dépassée" not in texte


def test_c4_echeance_passee_affiche_echeance_depassee(
    environnement_isole: Path, navigateur: TestClient
) -> None:
    ecrire_reglages(environnement_isole, "[cra]\ndeadline = 2026-11-20\n")
    texte = page(navigateur, "/").texte
    assert "échéance dépassée" in texte
    assert re.search(r"J-\d", texte) is None


def test_c4_le_texte_suit_la_date_injectee(
    environnement_isole: Path,
    navigateur: TestClient,
    fixer_date: Callable[[datetime], None],
) -> None:
    """Même réglage, trois dates du jour : le texte vient de `clock.today()`."""
    ecrire_reglages(environnement_isole, "[cra]\ndeadline = 2026-11-30\n")
    fixer_date(datetime(2026, 11, 20, 9, 0, tzinfo=ZURICH))
    assert "J-10" in page(navigateur, "/").texte
    fixer_date(datetime(2026, 11, 30, 23, 30, tzinfo=ZURICH))
    assert "aujourd'hui" in page(navigateur, "/").texte
    fixer_date(datetime(2026, 12, 1, 0, 30, tzinfo=ZURICH))
    assert "échéance dépassée" in page(navigateur, "/").texte


def test_contrat_reglage_vide_n_affiche_aucune_echeance(navigateur: TestClient) -> None:
    texte = page(navigateur, "/").texte
    assert re.search(r"J-\d", texte) is None
    assert "échéance dépassée" not in texte


# --- Critère 5 -------------------------------------------------------------


def test_c5_aucune_adresse_externe_dans_templates_et_static() -> None:
    import cabinet.web

    web = Path(cabinet.web.__file__).parent
    assert (web / "templates" / "base.html").is_file()
    assert (web / "static" / "app.css").is_file()
    fichiers = [
        chemin
        for dossier in ("templates", "static")
        for chemin in sorted((web / dossier).rglob("*"))
        if chemin.is_file()
    ]
    motif = re.compile(rb"https?://", re.IGNORECASE)
    fautifs = [str(f.relative_to(web)) for f in fichiers if motif.search(f.read_bytes())]
    assert fautifs == []


# --- Critère 6 -------------------------------------------------------------


def test_c6_seules_les_reunions_des_sept_prochains_jours_dans_l_ordre(
    navigateur: TestClient,
) -> None:
    inserer(charger("reunions_a_venir.json"))
    texte = page(navigateur, "/").texte
    attendues = ["Point matinal", "Revue budget", "Atelier prospective", "Comité de pilotage"]
    for titre in attendues:
        assert titre in texte, f"réunion « {titre} » absente"
    positions = [texte.index(titre) for titre in attendues]
    assert positions == sorted(positions), "réunions hors de l'ordre chronologique"
    assert "Réunion passée" not in texte
    assert "Revue lointaine" not in texte


def test_c6_bornes_incluses_aujourd_hui_et_sept_jours_plus_tard(navigateur: TestClient) -> None:
    jeu = {
        "series": [{"id": 1, "key": "borne", "label": "Borne"}],
        "meeting": [
            {"id": 1, "series_id": 1, "title": "Veille de la fenêtre", "date": "2026-11-23"},
            {"id": 2, "series_id": 1, "title": "Premier jour de la fenêtre", "date": "2026-11-24"},
            {"id": 3, "series_id": 1, "title": "Dernier jour de la fenêtre", "date": "2026-12-01"},
            {"id": 4, "series_id": 1, "title": "Lendemain de la fenêtre", "date": "2026-12-02"},
        ],
    }
    inserer(jeu)
    texte = page(navigateur, "/").texte
    assert "Premier jour de la fenêtre" in texte
    assert "Dernier jour de la fenêtre" in texte
    assert "Veille de la fenêtre" not in texte
    assert "Lendemain de la fenêtre" not in texte


def test_contrat_reunions_affichent_date_et_heure(navigateur: TestClient) -> None:
    """Dates `JJ.MM.AAAA`, heures `HH:MM` dans `Europe/Zurich` (`specs/08 §1.2`)."""
    inserer(charger("reunions_a_venir.json"))
    texte = page(navigateur, "/").texte
    for attendu in ("24.11.2026", "26.11.2026", "01.12.2026", "09:00", "14:00", "10:00"):
        assert attendu in texte, f"« {attendu} » absent de la page"


def test_contrat_marque_suivie_pour_une_serie_suivie(navigateur: TestClient) -> None:
    from cabinet.db.base import get_engine

    inserer(charger("reunions_a_venir.json"))
    avec = len(re.findall(r"\bsuivie\b", page(navigateur, "/").texte, re.IGNORECASE))
    series = table("series")
    with get_engine().begin() as connexion:
        connexion.execute(update(series).values(followed=False))
    sans = len(re.findall(r"\bsuivie\b", page(navigateur, "/").texte, re.IGNORECASE))
    assert avec - sans == 1


# --- Précisions de la spécification -----------------------------------------


def test_contrat_navigation_dans_l_ordre_de_la_specification(navigateur: TestClient) -> None:
    liens = [(texte, chemin_du_lien(href)) for href, texte in page(navigateur, "/").liens]
    positions = [liens.index(entree) for entree in NAVIGATION if entree in liens]
    assert len(positions) == len(NAVIGATION)
    assert positions == sorted(positions)


def test_contrat_page_en_francais(navigateur: TestClient) -> None:
    reponse = navigateur.get("/")
    assert reponse.status_code == 200
    assert re.search(r"<html[^>]*\blang=[\"']fr[\"']", reponse.text, re.IGNORECASE)


def test_contrat_message_de_redirection_affiche_et_echappe(navigateur: TestClient) -> None:
    message = "Traitement terminé. <b>gras</b>"
    reponse = navigateur.get("/?message=" + quote(message))
    assert reponse.status_code == 200
    assert "<b>gras</b>" not in reponse.text
    assert message in lire(reponse.text).texte


def test_contrat_page_reglages_en_lecture_seule(
    environnement_isole: Path, navigateur: TestClient
) -> None:
    ecrire_reglages(environnement_isole, '[llm]\nmodel = "modele-essai"\n')
    reponse = navigateur.get("/reglages")
    assert reponse.status_code == 200
    lecteur = lire(reponse.text)
    texte = lecteur.texte
    assert str(environnement_isole.resolve()) in texte
    for valeur in ("modele-essai", "http://127.0.0.1:8080/v1", "900", "64000", "8765"):
        assert valeur in texte, f"« {valeur} » absent de la page Réglages"
    assert "mlx-community/whisper-large-v3-mlx" in texte
    assert "agenda.ics" in texte
    assert "CABINET_ALLOW_REMOTE_LLM" not in reponse.text
    saisies = [
        attributs
        for balise, attributs in lecteur.balises
        if balise in ("textarea", "select")
        or (balise == "input" and (attributs.get("type") or "text") not in ("hidden", "submit"))
    ]
    assert saisies == [], "la page Réglages ne porte aucun champ de saisie"


@pytest.mark.parametrize("adresse", ["/", "/reglages"])
def test_contrat_reglages_invalides_donnent_500_avec_le_message(
    adresse: str, environnement_isole: Path, navigateur: TestClient
) -> None:
    """Une `ConfigError` rend la page d'erreur au code 500 (`specs/08 §1.2`)."""
    ecrire_reglages(environnement_isole, "[llm]\ninconnue = 1\n")
    reponse = navigateur.get(adresse)
    assert reponse.status_code == 500
    texte = lire(reponse.text).texte
    assert "Clé inconnue dans reglages.toml" in texte
    assert "llm.inconnue" in texte


def test_contrat_gabarits_du_ticket_presents() -> None:
    import cabinet.web

    gabarits = Path(cabinet.web.__file__).parent / "templates"
    for nom in ("base.html", "dashboard.html", "settings.html", "error.html"):
        assert (gabarits / nom).is_file(), f"gabarit {nom} absent"


def test_contrat_couleurs_et_police_de_la_charte() -> None:
    import cabinet.web

    css = (Path(cabinet.web.__file__).parent / "static" / "app.css").read_text(encoding="utf-8")
    for couleur in ("#FFFAF0", "#0F0E2B", "#1F24E9"):
        variable = re.compile(r"--[\w-]+\s*:\s*" + couleur + r"\b", re.IGNORECASE)
        assert variable.search(css), f"couleur {couleur} non déclarée en variable CSS"
    assert re.search(r"font-family\s*:\s*system-ui\s*,\s*sans-serif", css)


def test_contrat_feuille_de_style_servie_sous_static(navigateur: TestClient) -> None:
    reponse = navigateur.get("/static/app.css")
    assert reponse.status_code == 200
    assert "#FFFAF0".lower() in reponse.text.lower()


# --- Gardes de l'application (specs/08 §1.3) --------------------------------


def test_contrat_garde_d_hote_refuse_un_nom_etranger() -> None:
    from cabinet.web.app import create_app

    with TestClient(create_app(), base_url="http://cabinet.exemple.test") as client:
        assert client.get("/").status_code == 400
        assert client.get("/sante").status_code == 400


@pytest.mark.parametrize(
    "base", ["http://127.0.0.1:8765", "http://localhost:8765", "http://testserver"]
)
def test_contrat_garde_d_hote_accepte_les_hotes_locaux(base: str) -> None:
    from cabinet.web.app import create_app

    with TestClient(create_app(), base_url=base) as client:
        assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    "origine",
    [
        "http://attaquant.exemple.test",
        "http://127.0.0.1.exemple.test:8765",
        "http://localhost.exemple.test:8765",
        "https://127.0.0.1:8765",
        "null",
    ],
)
def test_contrat_garde_d_origine_refuse_un_post_etranger(
    origine: str, navigateur: TestClient
) -> None:
    reponse = navigateur.post("/sante", headers={"Origin": origine}, follow_redirects=False)
    assert reponse.status_code == 403


@pytest.mark.parametrize("origine", [None, "http://127.0.0.1:8765", "http://localhost:8765"])
def test_contrat_garde_d_origine_laisse_passer_un_post_local(
    origine: str | None, navigateur: TestClient
) -> None:
    entetes = {} if origine is None else {"Origin": origine}
    reponse = navigateur.post("/sante", headers=entetes, follow_redirects=False)
    assert reponse.status_code != 403


def test_contrat_garde_d_origine_ne_touche_pas_les_get(navigateur: TestClient) -> None:
    reponse = navigateur.get("/sante", headers={"Origin": "http://attaquant.exemple.test"})
    assert reponse.status_code == 200
