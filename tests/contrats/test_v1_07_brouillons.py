"""Tests de contrat du ticket V1-07, « Brouillons sans envoi ».

Écrits par le relecteur avant la vague. Chaque test travaille dans un
répertoire de données temporaire (`CABINET_HOME`, fixé par `conftest.py`).

Sources : `specs/00 §5` (principe 1, brouillons seulement), `specs/02 §2.14`
(table `draft` et ses transitions), `specs/08 §7` (service, export, lien
`mailto:`, page) et `specs/09 §4` et `§5` (scénario S10, contrôle « Aucune
fonction d'envoi »).

Les brouillons sont créés par le service `create_draft`, comme le fait la
recette du lot 1 (« Créer un brouillon à la main dans la base »). Les états
sont relus en SQL dans la table `draft`, par les noms de `specs/02 §2.14`.
Les tests valident eux-mêmes la session après un appel de service : la
spécification ne dit pas si le service le fait.
"""

from __future__ import annotations

import html
import json
import re
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

RACINE = Path(__file__).resolve().parents[2]
MAINTENANT = datetime(2026, 11, 20, 9, 30, tzinfo=ZoneInfo("Europe/Zurich"))
JOUR = "2026-11-20"

DESTINATAIRES = ["alice.martin@exemple.test", "bruno.keller@exemple.test"]
OBJET = "Compte rendu : comité d'été, 20.11.2026"
CORPS = "Bonjour à tous,\n\nVoici les décisions prises.\nÀ bientôt,\nAlice"

ETATS = ("brouillon", "valide", "rejete", "envoye_a_la_main")
TRANSITIONS_PERMISES = {
    ("brouillon", "valide"),
    ("brouillon", "rejete"),
    ("valide", "envoye_a_la_main"),
}
TRANSITIONS_INTERDITES = [
    (depart, cible)
    for depart in ETATS
    for cible in ETATS
    if (depart, cible) not in TRANSITIONS_PERMISES
]


@pytest.fixture(autouse=True)
def date_fixee(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """La date d'export et `created_at` viennent de `cabinet.clock` (specs/09 §3.3)."""
    monkeypatch.setattr("cabinet.clock.now", lambda: MAINTENANT)
    return MAINTENANT


# --- Aides -------------------------------------------------------------------


def creer_brouillon(
    subject: str = OBJET,
    body: str = CORPS,
    recipients: list[str] | None = None,
    kind: str = "envoi_cr",
) -> int:
    """Crée un brouillon par le service et renvoie son identifiant."""
    from cabinet.db.base import get_session, init_db
    from cabinet.services.drafts import create_draft

    init_db()
    with get_session() as session:
        draft = create_draft(session, kind, list(recipients or DESTINATAIRES), subject, body)
        session.commit()
        return draft.id


def changer_etat(draft_id: int, cible: str) -> Any:
    from cabinet.db.base import get_session
    from cabinet.services.drafts import set_status

    with get_session() as session:
        draft = set_status(session, draft_id, cible)
        session.commit()
        return draft


def amener_a(etat: str, **champs: Any) -> int:
    """Crée un brouillon et le mène à `etat` par les seules transitions permises."""
    chemins = {
        "brouillon": [],
        "valide": ["valide"],
        "rejete": ["rejete"],
        "envoye_a_la_main": ["valide", "envoye_a_la_main"],
    }
    draft_id = creer_brouillon(**champs)
    for cible in chemins[etat]:
        changer_etat(draft_id, cible)
    return draft_id


def lire(colonnes: str, draft_id: int) -> tuple:
    """Relit une ligne de `draft` dans une session neuve."""
    from cabinet.db.base import get_engine

    with Session(get_engine()) as session:
        ligne = session.execute(
            text(f"SELECT {colonnes} FROM draft WHERE id = :id"), {"id": draft_id}
        ).one()
        return tuple(ligne)


def etat(draft_id: int) -> str:
    return lire("status", draft_id)[0]


@contextmanager
def navigateur() -> Iterator[TestClient]:
    from cabinet.web.app import create_app

    with TestClient(create_app()) as client:
        yield client


def page_brouillons() -> str:
    with navigateur() as client:
        reponse = client.get("/brouillons")
    assert reponse.status_code == 200
    return reponse.text


def poster(adresse: str, champs: dict[str, str] | None = None) -> Any:
    with navigateur() as client:
        return client.post(adresse, data=champs or {}, follow_redirects=False)


def fichier_export(maison: Path, draft_id: int, kind: str = "envoi_cr") -> Path:
    return maison / "brouillons" / f"{JOUR}_{kind}_{draft_id}.md"


def lien_attendu(recipients: list[str], subject: str, body: str) -> str:
    """Formule de `specs/08 §7.3`, recopiée telle quelle."""
    return (
        "mailto:"
        + ",".join(quote(a, safe="@") for a in recipients)
        + "?subject="
        + quote(subject, safe="")
        + "&body="
        + quote(body, safe="")
    )


def decoder_mailto(lien: str) -> tuple[list[str], dict[str, str]]:
    """Découpe un lien `mailto:` en destinataires et paramètres décodés."""
    assert lien.startswith("mailto:")
    adresses, _, requete = lien.removeprefix("mailto:").partition("?")
    parametres = {}
    for morceau in requete.split("&"):
        cle, _, valeur = morceau.partition("=")
        parametres[cle] = unquote(valeur)
    return [unquote(a) for a in adresses.split(",")], parametres


# --- Critère 1 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("depart", "route", "attendu"),
    [
        ("brouillon", "valider", "valide"),
        ("brouillon", "rejeter", "rejete"),
        ("valide", "envoye", "envoye_a_la_main"),
    ],
)
def test_c1_transitions_permises_par_la_page(depart: str, route: str, attendu: str) -> None:
    draft_id = amener_a(depart)
    reponse = poster(f"/brouillons/{draft_id}/{route}")
    assert reponse.status_code == 303
    assert urlsplit(reponse.headers["location"]).path == "/brouillons"
    assert etat(draft_id) == attendu


@pytest.mark.parametrize(
    ("depart", "route"),
    [("rejete", "valider"), ("brouillon", "envoye")],
)
def test_c1_transitions_interdites_renvoient_409(depart: str, route: str) -> None:
    draft_id = amener_a(depart)
    reponse = poster(f"/brouillons/{draft_id}/{route}")
    assert reponse.status_code == 409
    assert etat(draft_id) == depart


@pytest.mark.parametrize(("depart", "cible"), sorted(TRANSITIONS_PERMISES))
def test_c1_service_accepte_les_trois_transitions(depart: str, cible: str) -> None:
    draft_id = amener_a(depart)
    draft = changer_etat(draft_id, cible)
    assert draft.status == cible
    assert etat(draft_id) == cible


@pytest.mark.parametrize(("depart", "cible"), TRANSITIONS_INTERDITES)
def test_c1_service_refuse_toute_autre_transition(depart: str, cible: str) -> None:
    from cabinet.db.base import get_session
    from cabinet.services.drafts import TransitionError, set_status

    draft_id = amener_a(depart)
    with get_session() as session, pytest.raises(TransitionError):
        set_status(session, draft_id, cible)
    assert etat(draft_id) == depart


# --- Critère 2 -------------------------------------------------------------


@pytest.mark.parametrize("kind", ["envoi_cr", "relance_cra"])
def test_c2_validation_cree_le_fichier_d_export(environnement_isole: Path, kind: str) -> None:
    draft_id = creer_brouillon(kind=kind)
    reponse = poster(f"/brouillons/{draft_id}/valider")
    assert reponse.status_code == 303

    export = fichier_export(environnement_isole, draft_id, kind)
    assert export.is_file()
    contenu = export.read_text(encoding="utf-8")
    assert OBJET in contenu
    assert CORPS in contenu


def test_c2_export_suit_le_format_de_la_spec(environnement_isole: Path) -> None:
    """`specs/08 §7.2` : titre, ligne des destinataires, corps, séparés par une ligne vide."""
    draft_id = creer_brouillon()
    changer_etat(draft_id, "valide")

    lignes = fichier_export(environnement_isole, draft_id).read_text(encoding="utf-8").split("\n")
    assert lignes[0] == f"# {OBJET}"
    assert lignes[1] == ""
    assert lignes[2].startswith("Destinataires : ")
    adresses = [a.strip() for a in lignes[2].removeprefix("Destinataires : ").split(",")]
    assert adresses == DESTINATAIRES
    assert lignes[3] == ""
    assert "\n".join(lignes[4:]).rstrip("\n") == CORPS


# --- Critère 3 -------------------------------------------------------------


def test_c3_modifier_un_brouillon_valide_renvoie_409() -> None:
    draft_id = amener_a("valide")
    avant = lire("subject, body, recipients", draft_id)
    reponse = poster(
        f"/brouillons/{draft_id}",
        {"subject": "Objet changé", "body": "Corps changé", "recipients": "x@exemple.test"},
    )
    assert reponse.status_code == 409
    assert lire("subject, body, recipients", draft_id) == avant
    assert etat(draft_id) == "valide"


@pytest.mark.parametrize("depart", ["valide", "rejete", "envoye_a_la_main"])
def test_c3_service_refuse_de_modifier_hors_brouillon(depart: str) -> None:
    from cabinet.db.base import get_session
    from cabinet.services.drafts import TransitionError, update_draft

    draft_id = amener_a(depart)
    with get_session() as session, pytest.raises(TransitionError):
        update_draft(session, draft_id, "Objet changé", "Corps changé", ["x@exemple.test"])
    assert lire("subject, body", draft_id) == (OBJET, CORPS)


# --- Critère 4 -------------------------------------------------------------


def test_c4_lien_mailto_absent_avant_validation() -> None:
    creer_brouillon()
    page = page_brouillons()
    assert OBJET in html.unescape(page)  # le brouillon est bien affiché
    assert "mailto:" not in page
    assert "data-copy" not in page


def test_c4_lien_mailto_et_bouton_copier_presents_apres_validation() -> None:
    draft_id = creer_brouillon()
    changer_etat(draft_id, "valide")
    page = page_brouillons()
    assert lien_attendu(DESTINATAIRES, OBJET, CORPS) in html.unescape(page)
    assert "data-copy" in page


@pytest.mark.parametrize("depart", ["rejete", "envoye_a_la_main"])
def test_c4_lien_mailto_absent_hors_etat_valide(depart: str) -> None:
    """« n'apparaissent qu'à l'état valide » (specs/08 §7.4)."""
    amener_a(depart)
    page = page_brouillons()
    assert OBJET in html.unescape(page)
    assert "mailto:" not in page
    assert "data-copy" not in page


# --- Critère 5 -------------------------------------------------------------


def test_c5_lien_decode_redonne_objet_et_corps() -> None:
    from cabinet.db.base import get_session, init_db
    from cabinet.services.drafts import create_draft, mailto_link

    init_db()
    with get_session() as session:
        draft = create_draft(session, "envoi_cr", list(DESTINATAIRES), OBJET, CORPS)
        session.commit()
        lien = mailto_link(draft)

    assert "\n" not in lien
    assert " " not in lien
    assert "%0A" in lien
    adresses, parametres = decoder_mailto(lien)
    assert adresses == DESTINATAIRES
    assert parametres == {"subject": OBJET, "body": CORPS}


def test_c5_caracteres_reserves_ne_cassent_pas_le_lien() -> None:
    from cabinet.db.base import get_session, init_db
    from cabinet.services.drafts import create_draft, mailto_link

    objet = "Q&R ? budget = 50 % + marge #2"
    corps = "Ligne 1 & suite\r\nLigne 2 ? oui = non\n+ 100 % / fin"
    init_db()
    with get_session() as session:
        draft = create_draft(session, "relance_cra", ["chloe.favre@exemple.test"], objet, corps)
        session.commit()
        lien = mailto_link(draft)

    adresses, parametres = decoder_mailto(lien)
    assert adresses == ["chloe.favre@exemple.test"]
    assert parametres == {"subject": objet, "body": corps}


# --- Critère 6 -------------------------------------------------------------


def test_c6_aucune_fonction_d_envoi_dans_src() -> None:
    """Équivalent de `grep -rn "smtplib\\|sendMail\\|send_message" src/` (specs/09 §5, S10)."""
    source = RACINE / "src"
    assert (source / "cabinet").is_dir()
    motif = re.compile(r"smtplib|sendMail|send_message")
    fautifs = []
    for chemin in sorted(source.rglob("*")):
        if not chemin.is_file() or "__pycache__" in chemin.parts:
            continue
        contenu = chemin.read_bytes().decode("utf-8", errors="ignore")
        for numero, ligne in enumerate(contenu.splitlines(), start=1):
            if motif.search(ligne):
                fautifs.append(f"{chemin.relative_to(RACINE)}:{numero}: {ligne.strip()}")
    assert fautifs == []


# --- Contrat, hors critères numérotés ---------------------------------------


def test_contrat_creation_a_l_etat_brouillon() -> None:
    """`create_draft` crée un brouillon à l'état `brouillon`, daté par `clock.now()`."""
    from cabinet.db.base import get_session, init_db
    from cabinet.services.drafts import create_draft

    init_db()
    with get_session() as session:
        draft = create_draft(session, "envoi_cr", list(DESTINATAIRES), OBJET, CORPS)
        session.commit()
        assert draft.status == "brouillon"
        assert draft.created_at == MAINTENANT
        draft_id = draft.id

    kind, recipients, subject, body, status, meeting_id, capture_id = lire(
        "kind, recipients, subject, body, status, meeting_id, capture_id", draft_id
    )
    assert (kind, subject, body, status) == ("envoi_cr", OBJET, CORPS, "brouillon")
    assert json.loads(recipients) == DESTINATAIRES
    assert (meeting_id, capture_id) == (None, None)


def test_contrat_modification_d_un_brouillon_par_la_page() -> None:
    draft_id = creer_brouillon()
    reponse = poster(
        f"/brouillons/{draft_id}",
        {
            "subject": "Objet corrigé",
            "body": "Corps corrigé\nsur deux lignes",
            "recipients": "david.roux@exemple.test,emma.blanc@exemple.test",
        },
    )
    assert reponse.status_code == 303
    assert urlsplit(reponse.headers["location"]).path == "/brouillons"
    subject, body, recipients, status = lire("subject, body, recipients, status", draft_id)
    assert (subject, body, status) == (
        "Objet corrigé",
        "Corps corrigé\nsur deux lignes",
        "brouillon",
    )
    assert json.loads(recipients) == ["david.roux@exemple.test", "emma.blanc@exemple.test"]


def test_contrat_modification_par_le_service() -> None:
    from cabinet.db.base import get_session
    from cabinet.services.drafts import update_draft

    draft_id = creer_brouillon()
    with get_session() as session:
        draft = update_draft(
            session, draft_id, "Objet corrigé", "Corps corrigé", ["x@exemple.test"]
        )
        session.commit()
        assert (draft.subject, draft.body, draft.status) == (
            "Objet corrigé",
            "Corps corrigé",
            "brouillon",
        )
    subject, recipients = lire("subject, recipients", draft_id)
    assert subject == "Objet corrigé"
    assert json.loads(recipients) == ["x@exemple.test"]


@pytest.mark.parametrize("route", ["", "/valider", "/rejeter", "/envoye"])
def test_contrat_identifiant_inconnu_renvoie_404(route: str) -> None:
    draft_id = creer_brouillon()
    champs = {"subject": "s", "body": "b", "recipients": "x@exemple.test"} if route == "" else {}
    # La route existe : l'identifiant connu ne donne pas 404.
    assert poster(f"/brouillons/{draft_id}{route}", champs).status_code != 404
    assert poster(f"/brouillons/{draft_id + 999}{route}", champs).status_code == 404


def test_contrat_page_vide_repond_200() -> None:
    from cabinet.db.base import init_db

    init_db()
    page_brouillons()


def test_contrat_page_liste_les_etats_dans_l_ordre() -> None:
    """Ordre de `specs/08 §7.4` : brouillon, valide, envoye_a_la_main, rejete."""
    amener_a("rejete", subject="Objet-rejete")
    amener_a("envoye_a_la_main", subject="Objet-envoye")
    amener_a("valide", subject="Objet-valide")
    amener_a("brouillon", subject="Objet-brouillon")
    page = page_brouillons()
    positions = [
        page.index(nom)
        for nom in ("Objet-brouillon", "Objet-valide", "Objet-envoye", "Objet-rejete")
    ]
    assert positions == sorted(positions)


def test_contrat_page_affiche_nature_destinataires_objet_corps_et_date() -> None:
    creer_brouillon(kind="relance_cra")
    page = html.unescape(page_brouillons())
    assert "relance_cra" in page or "relance" in page.lower()
    for attendu in (*DESTINATAIRES, OBJET, "Voici les décisions prises.", "20.11.2026"):
        assert attendu in page


def test_contrat_texte_du_brouillon_echappe() -> None:
    """Un corps peut venir du modèle : il passe par l'échappement de Jinja2 (specs/08 §10)."""
    creer_brouillon(subject="<script>alert(1)</script>", body="<b>gras</b>")
    page = page_brouillons()
    assert "<script>alert(1)</script>" not in page
    assert "<b>gras</b>" not in page


def test_contrat_rejet_n_ecrit_aucun_export(environnement_isole: Path) -> None:
    draft_id = creer_brouillon()
    changer_etat(draft_id, "rejete")
    dossier = environnement_isole / "brouillons"
    assert not dossier.exists() or list(dossier.glob("*.md")) == []


def test_contrat_mailto_suit_la_formule_de_la_spec() -> None:
    """`specs/08 §7.3` fixe la formule exacte du lien."""
    from cabinet.db.base import get_session, init_db
    from cabinet.services.drafts import create_draft, mailto_link

    init_db()
    with get_session() as session:
        draft = create_draft(session, "envoi_cr", list(DESTINATAIRES), OBJET, CORPS)
        session.commit()
        assert mailto_link(draft) == lien_attendu(DESTINATAIRES, OBJET, CORPS)


def test_contrat_bouton_copier_vise_une_zone_de_la_page() -> None:
    """`data-copy` porte l'identifiant de la zone de texte à copier (specs/08 §7.4)."""
    draft_id = creer_brouillon()
    changer_etat(draft_id, "valide")
    page = page_brouillons()
    cibles = re.findall(r'data-copy\s*=\s*["\']([^"\']+)["\']', page)
    assert cibles
    for cible in cibles:
        assert re.search(rf'\bid\s*=\s*["\']{re.escape(cible)}["\']', page)


def test_contrat_app_js_ne_fait_que_copier() -> None:
    """`app.js` appelle `navigator.clipboard.writeText` et ne fait rien d'autre."""
    script = (RACINE / "src" / "cabinet" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "navigator.clipboard.writeText" in script
    for interdit in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "http://", "https://"):
        assert interdit not in script


def test_contrat_aucune_route_d_envoi() -> None:
    """Les routes des brouillons sont celles de `specs/08 §7.4`, aucune autre."""
    from cabinet.web.app import create_app

    trouvees = set()
    for route in create_app().routes:
        chemin = getattr(route, "path", "")
        methodes = getattr(route, "methods", None) or set()
        if not chemin.startswith("/brouillons"):
            continue
        normalise = re.sub(r"\{[^}]+\}", "{id}", chemin).rstrip("/") or "/"
        trouvees |= {(m, normalise) for m in methodes if m != "HEAD"}
    assert trouvees == {
        ("GET", "/brouillons"),
        ("POST", "/brouillons/{id}"),
        ("POST", "/brouillons/{id}/valider"),
        ("POST", "/brouillons/{id}/rejeter"),
        ("POST", "/brouillons/{id}/envoye"),
    }


def test_contrat_cycle_complet_sans_aucune_connexion(
    monkeypatch: pytest.MonkeyPatch, environnement_isole: Path
) -> None:
    """« Marquer envoyé » constate un geste de l'utilisateur. L'application n'envoie rien."""

    def refuser(*args: object, **kwargs: object) -> None:
        raise AssertionError("tentative de connexion réseau")

    monkeypatch.setattr(socket.socket, "connect", refuser)
    monkeypatch.setattr(socket.socket, "connect_ex", refuser)
    monkeypatch.setattr(socket, "create_connection", refuser)

    draft_id = creer_brouillon()
    with navigateur() as client:
        etapes = [
            client.post(
                f"/brouillons/{draft_id}",
                data={
                    "subject": OBJET,
                    "body": CORPS,
                    "recipients": ",".join(DESTINATAIRES),
                },
                follow_redirects=False,
            ),
            client.post(f"/brouillons/{draft_id}/valider", follow_redirects=False),
            client.post(f"/brouillons/{draft_id}/envoye", follow_redirects=False),
        ]
        assert client.get("/brouillons").status_code == 200
    assert [reponse.status_code for reponse in etapes] == [303, 303, 303]
    assert etat(draft_id) == "envoye_a_la_main"
    assert fichier_export(environnement_isole, draft_id).is_file()
