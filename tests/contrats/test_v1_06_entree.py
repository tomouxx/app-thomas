"""Tests de contrat du ticket V1-06, « Page Entrée, rattachement et consentement ».

Écrits par le relecteur avant la vague. Un test `test_cN_*` par critère
d'acceptation numéroté, des tests `test_contrat_*` pour les précisions de
`specs/08 §3` et le principe 3 de `specs/00 §5` (consentement avant audio).

Le jeu de données est dans `fixtures/v1_06/entree.json`. Il est écrit dans la
base par les noms de tables et de colonnes de `specs/02 §2`, sur des tables
relues par réflexion, et relu de la même façon. Seules `InboxItem` et
`Meeting`, nommées par la signature de `suggest_meetings`, sont importées du
modèle. L'heure est fixée par `cabinet.clock.now` (`specs/09 §3.3`).

Un `POST` réussi redirige par 303 vers `/entree`. Un refus (400, 404, 409)
ne modifie jamais la base : les tests relisent la ligne avant et après.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.sql import sqltypes

FIXTURES = Path(__file__).parent / "fixtures" / "v1_06"
ZURICH = ZoneInfo("Europe/Zurich")
MAINTENANT = datetime(2026, 11, 24, 10, 0, tzinfo=ZURICH)
PHRASE_CONSENTEMENT = "J'ai l'accord des participants pour cet enregistrement"
ORIGINE_ETRANGERE = "http://attaquant.exemple.test"

# --- Base de données ---------------------------------------------------------

ORDRE_DES_TABLES = ("series", "event", "meeting", "inbox_item")

DEFAUTS: dict[str, dict[str, Any]] = {
    "series": {"followed": False, "client": None},
    "meeting": {"event_id": None},
    "inbox_item": {
        "name_date": None,
        "meeting_id": None,
        "consent_confirmed": False,
        "consent_at": None,
        "error": None,
        "detected_at": "2026-11-24T09:00:00+01:00",
    },
}


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
    from cabinet.db.base import get_engine, init_db

    init_db()
    with get_engine().begin() as connexion:
        for nom in ORDRE_DES_TABLES:
            if nom not in jeu:
                continue
            cible = table(nom)
            for brute in jeu[nom]:
                ligne = {**DEFAUTS.get(nom, {}), **brute}
                if nom == "inbox_item":
                    ligne.setdefault("sha256", sha256(ligne["path"].encode()).hexdigest())
                valeurs = {cle: convertir(cible.c[cle], val) for cle, val in ligne.items()}
                connexion.execute(cible.insert().values(**valeurs))


def charger() -> dict[str, list[dict[str, Any]]]:
    return json.loads((FIXTURES / "entree.json").read_text(encoding="utf-8"))


def ligne(nom: str, ident: int) -> dict[str, Any]:
    """Relit une ligne dans une connexion neuve. Dates et heures en UTC sans fuseau."""
    from cabinet.db.base import get_engine

    cible = table(nom)
    with get_engine().connect() as connexion:
        resultat = connexion.execute(select(cible).where(cible.c.id == ident)).mappings().one()
    return dict(resultat)


def lignes(nom: str, **filtres: Any) -> list[dict[str, Any]]:
    from cabinet.db.base import get_engine

    cible = table(nom)
    requete = select(cible).order_by(cible.c.id)
    for cle, valeur in filtres.items():
        requete = requete.where(cible.c[cle] == valeur)
    with get_engine().connect() as connexion:
        return [dict(r) for r in connexion.execute(requete).mappings()]


def compter(nom: str) -> int:
    from cabinet.db.base import get_engine

    cible = table(nom)
    with get_engine().connect() as connexion:
        return connexion.execute(select(func.count()).select_from(cible)).scalar_one()


def en_utc(valeur: datetime) -> datetime:
    return valeur.astimezone(UTC).replace(tzinfo=None)


# --- Suggestions -------------------------------------------------------------


def normaliser(texte: str) -> str:
    """Normalisation de `specs/08 §3.1` : minuscules, sans accents, tirets en espaces."""
    decompose = unicodedata.normalize("NFKD", texte.lower())
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return sans_accents.replace("-", " ").replace("_", " ")


def score(nom: str, titre: str) -> float:
    return SequenceMatcher(None, normaliser(nom), normaliser(titre)).ratio()


def suggerer(item_id: int, **options: Any) -> list[tuple[int, date, float]]:
    from cabinet.db.base import get_session
    from cabinet.db.models import InboxItem
    from cabinet.services.matching import suggest_meetings

    with get_session() as session:
        item = session.get(InboxItem, item_id)
        assert item is not None
        return [(m.id, m.date, s) for m, s in suggest_meetings(session, item, **options)]


# --- Pages -------------------------------------------------------------------


class Lecteur(HTMLParser):
    """Texte visible et champs de chaque formulaire, rangés par adresse d'action."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.morceaux: list[str] = []
        self.formulaires: dict[str, dict[str, Any]] = {}
        self.champs_hors_formulaire: list[dict[str, str | None]] = []
        self._formulaire: dict[str, Any] | None = None
        self._masque = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributs = dict(attrs)
        if tag in ("script", "style"):
            self._masque += 1
        if tag == "form":
            action = urlsplit(attributs.get("action") or "").path
            self._formulaire = {"method": (attributs.get("method") or "get").lower(), "champs": []}
            self.formulaires[action] = self._formulaire
        if tag in ("input", "select", "textarea", "button"):
            champ = {"balise": tag, **attributs}
            if self._formulaire is not None:
                self._formulaire["champs"].append(champ)
            else:
                self.champs_hors_formulaire.append(champ)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._masque:
            self._masque -= 1
        if tag == "form":
            self._formulaire = None

    def handle_data(self, data: str) -> None:
        if not self._masque:
            self.morceaux.append(data)

    @property
    def texte(self) -> str:
        brut = " ".join(" ".join(self.morceaux).split())
        return brut.replace("’", "'")


def lire(html: str) -> Lecteur:
    lecteur = Lecteur()
    lecteur.feed(html)
    lecteur.close()
    return lecteur


def champs(lecteur: Lecteur, item_id: int) -> list[dict[str, Any]]:
    action = f"/entree/{item_id}/rattacher"
    assert action in lecteur.formulaires, f"aucun formulaire vers {action}"
    formulaire = lecteur.formulaires[action]
    assert formulaire["method"] == "post"
    return formulaire["champs"]


def radios(lecteur: Lecteur, item_id: int) -> set[str]:
    return {
        c.get("value") or ""
        for c in champs(lecteur, item_id)
        if c["balise"] == "input" and c.get("type") == "radio" and c.get("name") == "meeting_id"
    }


def case_consentement(lecteur: Lecteur, item_id: int) -> dict[str, Any] | None:
    for champ in champs(lecteur, item_id):
        if champ["balise"] == "input" and champ.get("name") == "consentement":
            return champ
    return None


@pytest.fixture
def fixer_date(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    def fixer(valeur: datetime) -> None:
        monkeypatch.setattr("cabinet.clock.now", lambda: valeur)

    return fixer


@pytest.fixture(autouse=True)
def heure_fixee(fixer_date: Callable[[datetime], None]) -> None:
    """Mardi 24.11.2026, 10:00 à Zurich."""
    fixer_date(MAINTENANT)


@pytest.fixture
def jeu() -> None:
    inserer(charger())


@pytest.fixture
def navigateur(jeu: None) -> Iterator[TestClient]:
    from cabinet.web.app import create_app

    with TestClient(create_app()) as client:
        yield client


def poster(
    navigateur: TestClient,
    adresse: str,
    donnees: dict[str, str] | None = None,
    origine: str | None = None,
) -> Any:
    entetes = {} if origine is None else {"Origin": origine}
    return navigateur.post(adresse, data=donnees or {}, headers=entetes, follow_redirects=False)


def assert_redirige_vers_entree(reponse: Any) -> None:
    assert reponse.status_code == 303, f"code {reponse.status_code} : {reponse.text[:300]}"
    assert urlsplit(reponse.headers["location"]).path == "/entree"


def valeur_de_la_case(navigateur: TestClient, item_id: int) -> str:
    """Ce que le navigateur envoie quand l'utilisateur coche la case de la page."""
    lecteur = lire(navigateur.get("/entree").text)
    case = case_consentement(lecteur, item_id)
    assert case is not None, f"aucune case `consentement` pour l'élément {item_id}"
    assert case.get("type") == "checkbox"
    return case.get("value") or "on"


# --- Critère 1 -------------------------------------------------------------


def test_c1_suggestions_du_jour_lu_dans_le_nom_seulement(jeu: None) -> None:
    suggestions = suggerer(1)
    assert {ident for ident, _, _ in suggestions} == {1, 2, 3}
    assert all(jour == date(2026, 11, 23) for _, jour, _ in suggestions)


def test_c1_suggestions_triees_par_similarite_decroissante(jeu: None) -> None:
    nom = "point-hebdo-equipe"
    titres = {1: "Point hebdo équipe", 2: "Comité de pilotage", 3: "Revue budget"}
    attendu = sorted(titres, key=lambda ident: (-score(nom, titres[ident]), ident))

    suggestions = suggerer(1)

    assert [ident for ident, _, _ in suggestions] == attendu
    assert attendu[0] == 1
    for ident, _, valeur in suggestions:
        assert valeur == pytest.approx(score(nom, titres[ident]))
    valeurs = [valeur for _, _, valeur in suggestions]
    assert valeurs == sorted(valeurs, reverse=True)
    assert valeurs[0] == pytest.approx(1.0)


def test_c1_aucune_reunion_ce_jour_aucune_suggestion(jeu: None) -> None:
    inserer(
        {
            "inbox_item": [
                {
                    "id": 30,
                    "path": "entree/2026-10-01_point-hebdo-equipe.vtt",
                    "kind": "teams_transcript",
                    "status": "a_rattacher",
                    "name_date": "2026-10-01",
                }
            ]
        }
    )
    assert suggerer(30) == []


def test_contrat_repli_sur_la_date_de_detection(jeu: None) -> None:
    """Sans date dans le nom, le jour de référence est celui de `detected_at`."""
    assert [ident for ident, _, _ in suggerer(3)] == [4]


def test_contrat_date_de_detection_lue_dans_le_fuseau_local(jeu: None) -> None:
    """00:30 à Zurich le 24 est encore le 23 en UTC : le jour retenu est le 24."""
    inserer(
        {
            "inbox_item": [
                {
                    "id": 31,
                    "path": "entree/point-hebdo-equipe.vtt",
                    "kind": "teams_transcript",
                    "status": "a_rattacher",
                    "detected_at": "2026-11-24T00:30:00+01:00",
                }
            ]
        }
    )
    assert [ident for ident, _, _ in suggerer(31)] == [4]


def test_contrat_egalite_de_score_departagee_par_id(jeu: None) -> None:
    inserer(
        {
            "meeting": [
                {"id": 41, "series_id": 3, "title": "Revue budget", "date": "2026-11-27"},
                {"id": 40, "series_id": 3, "title": "Revue budget", "date": "2026-11-27"},
            ],
            "inbox_item": [
                {
                    "id": 32,
                    "path": "entree/2026-11-27_revue-budget.vtt",
                    "kind": "teams_transcript",
                    "status": "a_rattacher",
                    "name_date": "2026-11-27",
                }
            ],
        }
    )
    assert [ident for ident, _, _ in suggerer(32)] == [40, 41]


def test_contrat_au_plus_limit_suggestions(jeu: None) -> None:
    titres = {
        50: "Point hebdo équipe",
        51: "Point hebdo",
        52: "Point équipe",
        53: "Hebdo",
        54: "Comité de pilotage",
        55: "Revue budget",
        56: "Atelier prospective",
    }
    inserer(
        {
            "meeting": [
                {"id": ident, "series_id": 1, "title": titre, "date": "2026-11-26"}
                for ident, titre in titres.items()
            ],
            "inbox_item": [
                {
                    "id": 33,
                    "path": "entree/2026-11-26_point-hebdo-equipe.vtt",
                    "kind": "teams_transcript",
                    "status": "a_rattacher",
                    "name_date": "2026-11-26",
                }
            ],
        }
    )
    nom = "point-hebdo-equipe"
    classement = sorted(titres, key=lambda ident: (-score(nom, titres[ident]), ident))
    assert [ident for ident, _, _ in suggerer(33)] == classement[:5]
    assert [ident for ident, _, _ in suggerer(33, limit=2)] == classement[:2]


# --- Critère 2 -------------------------------------------------------------


def test_c2_transcript_rattache_a_une_reunion_existante(navigateur: TestClient) -> None:
    reponse = poster(navigateur, "/entree/1/rattacher", {"meeting_id": "1"})
    assert_redirige_vers_entree(reponse)
    element = ligne("inbox_item", 1)
    assert element["meeting_id"] == 1
    assert element["status"] == "pret"
    assert not element["consent_confirmed"]
    assert element["consent_at"] is None


def test_c2_notes_rattachees_passent_a_pret(navigateur: TestClient) -> None:
    reponse = poster(navigateur, "/entree/3/rattacher", {"meeting_id": "4"})
    assert_redirige_vers_entree(reponse)
    element = ligne("inbox_item", 3)
    assert (element["meeting_id"], element["status"]) == (4, "pret")


def test_contrat_element_pret_peut_etre_rattache_a_une_autre_reunion(
    navigateur: TestClient,
) -> None:
    assert_redirige_vers_entree(poster(navigateur, "/entree/1/rattacher", {"meeting_id": "1"}))
    assert_redirige_vers_entree(poster(navigateur, "/entree/1/rattacher", {"meeting_id": "2"}))
    element = ligne("inbox_item", 1)
    assert (element["meeting_id"], element["status"]) == (2, "pret")


def test_contrat_reunion_inconnue_donne_404_sans_rien_changer(navigateur: TestClient) -> None:
    avant = ligne("inbox_item", 1)
    reponse = poster(navigateur, "/entree/1/rattacher", {"meeting_id": "999"})
    assert reponse.status_code == 404
    assert ligne("inbox_item", 1) == avant


def test_contrat_element_inconnu_donne_404(navigateur: TestClient) -> None:
    assert poster(navigateur, "/entree/999/rattacher", {"meeting_id": "1"}).status_code == 404
    assert poster(navigateur, "/entree/999/ignorer").status_code == 404


# --- Critère 3 : consentement, premier contrôle du principe 3 ---------------


def test_c3_audio_sans_la_case_refuse_par_400_et_inchange(navigateur: TestClient) -> None:
    avant = ligne("inbox_item", 2)
    reponse = poster(navigateur, "/entree/2/rattacher", {"meeting_id": "2"})
    assert reponse.status_code == 400
    assert "text/html" in reponse.headers["content-type"]
    apres = ligne("inbox_item", 2)
    assert apres == avant
    assert apres["status"] == "consentement_requis"
    assert apres["meeting_id"] is None
    assert not apres["consent_confirmed"]
    assert apres["consent_at"] is None


def test_c3_audio_hors_agenda_sans_la_case_ne_cree_ni_reunion_ni_serie(
    navigateur: TestClient,
) -> None:
    avant = ligne("inbox_item", 2)
    reunions, series = compter("meeting"), compter("series")
    reponse = poster(
        navigateur,
        "/entree/2/rattacher",
        {"titre": "Atelier enregistré", "date": "2026-11-23"},
    )
    assert reponse.status_code == 400
    assert ligne("inbox_item", 2) == avant
    assert (compter("meeting"), compter("series")) == (reunions, series)


def test_c3_audio_deja_pret_rerattache_sans_la_case_refuse(navigateur: TestClient) -> None:
    """Même un audio déjà consenti exige la case à chaque rattachement."""
    case = valeur_de_la_case(navigateur, 2)
    poster(navigateur, "/entree/2/rattacher", {"meeting_id": "2", "consentement": case})
    avant = ligne("inbox_item", 2)
    assert avant["status"] == "pret"
    reponse = poster(navigateur, "/entree/2/rattacher", {"meeting_id": "1"})
    assert reponse.status_code == 400
    assert ligne("inbox_item", 2) == avant


def test_contrat_formulaire_d_un_audio_porte_la_case_de_consentement(
    navigateur: TestClient,
) -> None:
    lecteur = lire(navigateur.get("/entree").text)
    assert PHRASE_CONSENTEMENT in lecteur.texte
    case = case_consentement(lecteur, 2)
    assert case is not None
    assert case.get("type") == "checkbox"
    assert "checked" not in case, "la case n'est jamais cochée d'avance"
    assert case_consentement(lecteur, 1) is None, "un transcript ne porte pas la case"


def test_contrat_post_d_origine_etrangere_refuse_sans_rien_changer(
    navigateur: TestClient,
) -> None:
    case = valeur_de_la_case(navigateur, 2)
    avant = ligne("inbox_item", 2)
    reponse = poster(
        navigateur,
        "/entree/2/rattacher",
        {"meeting_id": "2", "consentement": case},
        origine=ORIGINE_ETRANGERE,
    )
    assert reponse.status_code == 403
    assert ligne("inbox_item", 2) == avant


# --- Critère 4 -------------------------------------------------------------


def test_c4_audio_avec_la_case_passe_a_pret_avec_consent_at(navigateur: TestClient) -> None:
    case = valeur_de_la_case(navigateur, 2)
    autre_audio = ligne("inbox_item", 8)
    reponse = poster(navigateur, "/entree/2/rattacher", {"meeting_id": "2", "consentement": case})
    assert_redirige_vers_entree(reponse)
    element = ligne("inbox_item", 2)
    assert element["status"] == "pret"
    assert element["meeting_id"] == 2
    assert element["consent_confirmed"]
    assert element["consent_at"] == en_utc(MAINTENANT)
    assert ligne("inbox_item", 8) == autre_audio, "le consentement vaut pour ce fichier seul"


def test_c4_audio_hors_agenda_avec_la_case(navigateur: TestClient) -> None:
    case = valeur_de_la_case(navigateur, 2)
    reponse = poster(
        navigateur,
        "/entree/2/rattacher",
        {"titre": "Atelier enregistré", "date": "2026-11-23", "consentement": case},
    )
    assert_redirige_vers_entree(reponse)
    element = ligne("inbox_item", 2)
    reunions = lignes("meeting", title="Atelier enregistré")
    assert len(reunions) == 1
    assert element["meeting_id"] == reunions[0]["id"]
    assert element["status"] == "pret"
    assert element["consent_confirmed"]
    assert element["consent_at"] == en_utc(MAINTENANT)


# --- Critère 5 -------------------------------------------------------------


def test_c5_reunion_hors_agenda_creee_avec_sa_serie(navigateur: TestClient) -> None:
    series_avant = compter("series")
    reponse = poster(
        navigateur,
        "/entree/1/rattacher",
        {"titre": "Atelier découverte client", "date": "2026-11-25"},
    )
    assert_redirige_vers_entree(reponse)

    reunions = lignes("meeting", title="Atelier découverte client")
    assert len(reunions) == 1
    reunion = reunions[0]
    assert reunion["date"] == date(2026, 11, 25)
    assert reunion["event_id"] is None

    assert compter("series") == series_avant + 1
    serie = ligne("series", reunion["series_id"])
    assert serie["key"] == "atelier decouverte client"

    element = ligne("inbox_item", 1)
    assert (element["meeting_id"], element["status"]) == (reunion["id"], "pret")


def test_c5_serie_existante_reprise_par_sa_cle(navigateur: TestClient) -> None:
    """`series_key("RE: Point hebdo 12")` vaut `point hebdo`, série 4 du jeu."""
    series_avant = compter("series")
    reponse = poster(
        navigateur,
        "/entree/3/rattacher",
        {"titre": "RE: Point hebdo 12", "date": "2026-11-24"},
    )
    assert_redirige_vers_entree(reponse)
    assert compter("series") == series_avant
    reunions = lignes("meeting", title="RE: Point hebdo 12")
    assert len(reunions) == 1
    assert reunions[0]["series_id"] == 4
    assert reunions[0]["event_id"] is None
    assert ligne("inbox_item", 3)["meeting_id"] == reunions[0]["id"]


@pytest.mark.parametrize(
    "donnees",
    [{"titre": "Atelier sans date"}, {"date": "2026-11-25"}, {}],
)
def test_contrat_hors_agenda_incomplet_donne_400_sans_rien_creer(
    donnees: dict[str, str], navigateur: TestClient
) -> None:
    avant = ligne("inbox_item", 1)
    reunions, series = compter("meeting"), compter("series")
    reponse = poster(navigateur, "/entree/1/rattacher", donnees)
    assert reponse.status_code == 400
    assert ligne("inbox_item", 1) == avant
    assert (compter("meeting"), compter("series")) == (reunions, series)


# --- Critère 6 -------------------------------------------------------------


def test_c6_ignorer_passe_l_element_a_ignore(navigateur: TestClient) -> None:
    assert_redirige_vers_entree(poster(navigateur, "/entree/1/ignorer"))
    assert ligne("inbox_item", 1)["status"] == "ignore"


def test_c6_ignorer_un_audio_ne_donne_aucun_consentement(navigateur: TestClient) -> None:
    assert_redirige_vers_entree(poster(navigateur, "/entree/2/ignorer"))
    element = ligne("inbox_item", 2)
    assert element["status"] == "ignore"
    assert not element["consent_confirmed"]
    assert element["consent_at"] is None


def test_contrat_ignorer_un_element_en_erreur(navigateur: TestClient) -> None:
    assert_redirige_vers_entree(poster(navigateur, "/entree/6/ignorer"))
    assert ligne("inbox_item", 6)["status"] == "ignore"


def test_contrat_ignorer_un_element_traite_donne_409(navigateur: TestClient) -> None:
    avant = ligne("inbox_item", 4)
    assert poster(navigateur, "/entree/4/ignorer").status_code == 409
    assert ligne("inbox_item", 4) == avant


# --- Critère 7 -------------------------------------------------------------


def test_c7_rattacher_un_element_traite_donne_409(navigateur: TestClient) -> None:
    avant = ligne("inbox_item", 4)
    reponse = poster(navigateur, "/entree/4/rattacher", {"meeting_id": "1"})
    assert reponse.status_code == 409
    assert ligne("inbox_item", 4) == avant


def test_c7_rattacher_un_element_traite_hors_agenda_ne_cree_rien(navigateur: TestClient) -> None:
    reunions, series = compter("meeting"), compter("series")
    reponse = poster(
        navigateur, "/entree/4/rattacher", {"titre": "Atelier tardif", "date": "2026-11-25"}
    )
    assert reponse.status_code == 409
    assert (compter("meeting"), compter("series")) == (reunions, series)


@pytest.mark.parametrize("item_id", [6, 9])
def test_contrat_rattacher_hors_des_etats_permis_donne_409(
    item_id: int, navigateur: TestClient
) -> None:
    """États `erreur` et `ignore` : seuls `a_rattacher`, `consentement_requis`, `pret` passent."""
    avant = ligne("inbox_item", item_id)
    assert (
        poster(navigateur, f"/entree/{item_id}/rattacher", {"meeting_id": "1"}).status_code == 409
    )
    assert ligne("inbox_item", item_id) == avant


def test_contrat_une_capture_ne_se_rattache_pas(navigateur: TestClient) -> None:
    avant = ligne("inbox_item", 5)
    reponse = poster(navigateur, "/entree/5/rattacher", {"meeting_id": "1"})
    assert reponse.status_code == 409
    assert ligne("inbox_item", 5) == avant


# --- Réessayer (specs/08 §3.4) ----------------------------------------------


@pytest.mark.parametrize(
    ("item_id", "etat_attendu"),
    [
        (6, "pret"),  # transcript rattaché
        (7, "pret"),  # audio rattaché et consenti
        (8, "consentement_requis"),  # audio sans consentement
        (10, "pret"),  # capture
        (11, "a_rattacher"),  # transcript sans réunion
    ],
)
def test_contrat_reessayer_remet_l_element_dans_le_bon_etat(
    item_id: int, etat_attendu: str, navigateur: TestClient
) -> None:
    avant = ligne("inbox_item", item_id)
    assert_redirige_vers_entree(poster(navigateur, f"/entree/{item_id}/reessayer"))
    apres = ligne("inbox_item", item_id)
    assert apres["status"] == etat_attendu
    assert apres["error"] is None
    assert apres["meeting_id"] == avant["meeting_id"]
    assert apres["consent_confirmed"] == avant["consent_confirmed"]


@pytest.mark.parametrize("item_id", [1, 2, 4, 5, 9])
def test_contrat_reessayer_hors_erreur_donne_409(item_id: int, navigateur: TestClient) -> None:
    avant = ligne("inbox_item", item_id)
    assert poster(navigateur, f"/entree/{item_id}/reessayer").status_code == 409
    assert ligne("inbox_item", item_id) == avant


# --- Page Entrée (specs/08 §3.2) --------------------------------------------


def test_contrat_page_entree_repond_200_sur_une_base_vide() -> None:
    from cabinet.web.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/entree").status_code == 200


def test_contrat_page_entree_liste_les_elements_par_etat(navigateur: TestClient) -> None:
    reponse = navigateur.get("/entree")
    assert reponse.status_code == 200
    texte = lire(reponse.text).texte
    groupes = [
        ["2026-11-23_point-hebdo-equipe.vtt", "notes-revue.md"],  # a_rattacher
        ["2026-11-23_comite-de-pilotage.m4a"],  # consentement_requis
        ["app_nov.html"],  # pret
        [
            "2026-11-23_revue-budget.vtt",
            "2026-11-23_revue-budget.m4a",
            "enregistrement-atelier.wav",
            "app_oct.txt",
            "2026-11-23_sans-reunion.vtt",
        ],  # erreur
        ["2026-11-20_point-hebdo.vtt"],  # traite
        ["brouillon-ecarte.md", "archive.zip"],  # ignore
    ]
    for groupe in groupes:
        for nom in groupe:
            assert nom in texte, f"élément {nom} absent de la page"
    for precedent, suivant in zip(groupes, groupes[1:], strict=False):
        dernier = max(texte.index(nom) for nom in precedent)
        premier = min(texte.index(nom) for nom in suivant)
        assert dernier < premier, f"{precedent} doit précéder {suivant}"
    assert "Le fichier n'est pas au format WebVTT." in texte


def test_contrat_formulaire_porte_les_suggestions_en_boutons_radio(
    navigateur: TestClient,
) -> None:
    lecteur = lire(navigateur.get("/entree").text)
    assert radios(lecteur, 1) == {"1", "2", "3"}
    assert radios(lecteur, 2) == {"1", "2", "3"}
    assert radios(lecteur, 3) == {"4"}


def test_contrat_formulaire_porte_titre_et_date_hors_agenda(navigateur: TestClient) -> None:
    lecteur = lire(navigateur.get("/entree").text)
    for item_id in (1, 2, 3):
        noms = {c.get("name"): c for c in champs(lecteur, item_id)}
        assert "titre" in noms
        assert "date" in noms
        assert noms["date"].get("type") == "date"


def test_contrat_seuls_les_elements_a_rattacher_portent_un_formulaire(
    navigateur: TestClient,
) -> None:
    lecteur = lire(navigateur.get("/entree").text)
    for item_id in (4, 5, 6, 9):
        assert f"/entree/{item_id}/rattacher" not in lecteur.formulaires


def test_contrat_groupes_traite_et_ignore_limites_aux_50_plus_recents() -> None:
    from cabinet.web.app import create_app

    inserer(
        {
            "inbox_item": [
                {
                    "id": 100 + rang,
                    "path": f"entree/ecarte-{rang:03d}.md",
                    "kind": "notes",
                    "status": "ignore",
                    "detected_at": datetime(2026, 11, 1, 8, 0, tzinfo=ZURICH).replace(minute=rang),
                }
                for rang in range(1, 56)
            ]
        }
    )
    with TestClient(create_app()) as client:
        texte = lire(client.get("/entree").text).texte
    affiches = {rang for rang in range(1, 56) if f"ecarte-{rang:03d}.md" in texte}
    assert affiches == set(range(6, 56))
