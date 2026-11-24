"""Tests de contrat du ticket V1-02, « Modèle de données ».

Écrits par le relecteur avant la vague. Sections de référence : `specs/02 §1`,
`§2` et `§4`, `specs/05 §2.1`. Chaque test travaille dans un répertoire de
données temporaire (`CABINET_HOME`, fixé par `conftest.py`).

La spécification nomme les tables, les colonnes et les états, pas les classes
Python qui les portent. Les tests retrouvent donc chaque classe par le nom de
sa table, dans le registre de `Base`, et chaque attribut par le nom de sa
colonne. Ils relisent aussi la base en SQL, par les noms de `specs/02 §2`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

ZURICH = ZoneInfo("Europe/Zurich")
MOMENT = datetime(2026, 11, 20, 10, 0, tzinfo=ZURICH)
FIN = datetime(2026, 11, 20, 11, 0, tzinfo=ZURICH)

TABLES = (
    "agent_run",
    "inbox_item",
    "event",
    "series",
    "meeting",
    "transcript",
    "prep_note",
    "minutes",
    "decision",
    "action_item",
    "timesheet_capture",
    "timesheet_line",
    "anomaly",
    "draft",
)

# Colonnes de chaque table, et celles qui admettent nul (specs/02 §2).
COLONNES: dict[str, tuple[set[str], set[str]]] = {
    "agent_run": (
        {
            "id", "agent", "target_ref", "status", "started_at", "ended_at", "model",
            "prompt_tokens", "completion_tokens", "error",
        },
        {"ended_at", "model", "prompt_tokens", "completion_tokens", "error"},
    ),
    "inbox_item": (
        {
            "id", "path", "sha256", "kind", "status", "name_date", "meeting_id",
            "consent_confirmed", "consent_at", "error", "detected_at",
        },
        {"name_date", "meeting_id", "consent_at", "error"},
    ),
    "event": (
        {
            "id", "uid", "title", "start", "end", "all_day", "location", "organizer",
            "attendees", "cancelled",
        },
        {"location", "organizer"},
    ),
    "series": ({"id", "key", "label", "followed", "client"}, {"client"}),
    "meeting": ({"id", "event_id", "series_id", "title", "date"}, {"event_id"}),
    "transcript": (
        {"id", "meeting_id", "inbox_item_id", "source", "text", "segments"},
        set(),
    ),
    "prep_note": ({"id", "meeting_id", "content_md", "status", "created_at"}, set()),
    "minutes": (
        {
            "id", "meeting_id", "version", "content_md", "summary", "open_points",
            "next_meeting", "status", "created_at",
        },
        {"next_meeting"},
    ),
    "decision": ({"id", "meeting_id", "minutes_id", "text", "quote", "verified"}, set()),
    "action_item": (
        {
            "id", "meeting_id", "minutes_id", "description", "owner", "due_date", "quote",
            "verified", "status",
        },
        {"owner", "due_date"},
    ),
    "timesheet_capture": (
        {"id", "inbox_item_id", "period", "source", "verified", "verified_at", "checked_at"},
        {"period", "verified_at", "checked_at"},
    ),
    "timesheet_line": ({"id", "capture_id", "person", "project_code", "days"}, set()),
    "anomaly": ({"id", "capture_id", "person", "rule_id", "detail", "severity"}, set()),
    "draft": (
        {
            "id", "kind", "recipients", "subject", "body", "status", "meeting_id",
            "capture_id", "created_at",
        },
        {"meeting_id", "capture_id"},
    ),
}

# Valeurs admises de chaque colonne d'état (specs/02 §2).
ETATS: dict[tuple[str, str], set[str]] = {
    ("agent_run", "status"): {"en_cours", "ok", "erreur"},
    ("inbox_item", "kind"): {
        "teams_transcript", "notes", "audio", "app_capture_text", "app_capture_image", "inconnu",
    },
    ("inbox_item", "status"): {
        "nouveau", "a_rattacher", "consentement_requis", "pret", "traite", "erreur", "ignore",
    },
    ("transcript", "source"): {"teams", "audio", "notes"},
    ("prep_note", "status"): {"brouillon", "valide"},
    ("minutes", "status"): {"brouillon", "valide"},
    ("action_item", "status"): {"ouvert", "fait"},
    ("timesheet_capture", "source"): {"texte", "vision"},
    ("anomaly", "severity"): {"bloquant", "a_verifier"},
    ("draft", "kind"): {"envoi_cr", "relance_cra"},
    ("draft", "status"): {"brouillon", "valide", "rejete", "envoye_a_la_main"},
}


# --- Outils ----------------------------------------------------------------


def preparer() -> None:
    """Crée les tables dans le répertoire de données temporaire."""
    from cabinet.db.base import init_db

    init_db()


def session() -> Session:
    from cabinet.db.base import get_session

    return get_session()


def classes() -> dict[str, type]:
    """Classe de chaque table, retrouvée par son nom dans le registre de `Base`."""
    import cabinet.db.models  # noqa: F401 - les modèles s'enregistrent sur Base
    from cabinet.db.base import Base

    return {mapper.local_table.name: mapper.class_ for mapper in Base.registry.mappers}


def classe(table: str) -> type:
    trouvees = classes()
    assert table in trouvees, f"aucun modèle n'est lié à la table `{table}`"
    return trouvees[table]


def attributs(table: str) -> dict[str, str]:
    """Nom de l'attribut Python de chaque colonne de la table."""
    return {prop.columns[0].name: prop.key for prop in inspect(classe(table)).column_attrs}


def creer(s: Session, table: str, **colonnes: Any) -> int:
    """Ajoute une ligne par le modèle de la table et rend son identifiant."""
    noms = attributs(table)
    objet = classe(table)(**{noms[colonne]: valeur for colonne, valeur in colonnes.items()})
    s.add(objet)
    s.flush()
    return getattr(objet, noms["id"])


def relire(table: str, ident: int) -> dict[str, Any]:
    """Relit une ligne par le modèle, dans une session neuve."""
    noms = attributs(table)
    with session() as s:
        objet = s.get(classe(table), ident)
        assert objet is not None, f"{table} {ident} introuvable"
        return {colonne: getattr(objet, cle) for colonne, cle in noms.items()}


def compter(table: str, condition: str = "1 = 1", **parametres: Any) -> int:
    with session() as s:
        requete = text(f'SELECT COUNT(*) FROM "{table}" WHERE {condition}')
        return s.execute(requete, parametres).scalar_one()


def sql(requete: str, **parametres: Any) -> list[tuple]:
    with session() as s:
        return [tuple(ligne) for ligne in s.execute(text(requete), parametres)]


def executer(requete: str, **parametres: Any) -> None:
    with session() as s:
        s.execute(text(requete), parametres)
        s.commit()


def brut(valeur: Any) -> Any:
    """Valeur d'un état relu, comparable à une chaîne."""
    return getattr(valeur, "value", valeur)


def construire_graphe() -> dict[str, dict[str, Any]]:
    """Une ligne complète dans chacune des quatorze tables, toutes colonnes remplies.

    Rend, pour chaque table, les valeurs écrites, identifiant compris.
    """
    preparer()
    ecrit: dict[str, dict[str, Any]] = {}

    def ajouter(s: Session, table: str, **colonnes: Any) -> int:
        ident = creer(s, table, **colonnes)
        ecrit[table] = {"id": ident, **colonnes}
        return ident

    with session() as s:
        serie = ajouter(
            s, "series", key="point projet", label="Point projet", followed=True,
            client="Client Fictif",
        )
        evenement = ajouter(
            s, "event", uid="evt-001@exemple.test", title="Point projet", start=MOMENT,
            end=FIN, all_day=False, location="Salle Exemple",
            organizer="camille.exemple@exemple.test",
            attendees=["camille.exemple@exemple.test", "sacha.modele@exemple.test"],
            cancelled=False,
        )
        reunion = ajouter(
            s, "meeting", event_id=evenement, series_id=serie, title="Point projet",
            date=date(2026, 11, 20),
        )
        element = ajouter(
            s, "inbox_item", path="entree/2026-11-20_point-projet.vtt", sha256="a" * 64,
            kind="teams_transcript", status="pret", name_date=date(2026, 11, 20),
            meeting_id=reunion, consent_confirmed=True, consent_at=MOMENT,
            error="ligne 3 illisible", detected_at=MOMENT,
        )
        ajouter(
            s, "transcript", meeting_id=reunion, inbox_item_id=element, source="teams",
            text="Camille Exemple : Bonjour.",
            segments=[
                {"start_s": 0.0, "end_s": 4.5, "speaker": "Camille Exemple", "text": "Bonjour."}
            ],
        )
        ajouter(
            s, "prep_note", meeting_id=reunion, content_md="# Préparation\n",
            status="brouillon", created_at=MOMENT,
        )
        compte_rendu = ajouter(
            s, "minutes", meeting_id=reunion, version=1, content_md="# Compte rendu\n",
            summary="Point d'avancement du projet fictif.", open_points=["Budget à revoir"],
            next_meeting="mardi prochain", status="valide", created_at=MOMENT,
        )
        ajouter(
            s, "decision", meeting_id=reunion, minutes_id=compte_rendu,
            text="Le lot A est livré en décembre.", quote="on livre le lot A en décembre",
            verified=True,
        )
        ajouter(
            s, "action_item", meeting_id=reunion, minutes_id=compte_rendu,
            description="Envoyer le planning.", owner="Sacha Modele",
            due_date=date(2026, 11, 27), quote="Sacha envoie le planning", verified=True,
            status="fait",
        )
        element_capture = creer(
            s, "inbox_item", path="entree/app_2026-11.txt", sha256="b" * 64,
            kind="app_capture_text", status="traite", consent_confirmed=False,
            detected_at=MOMENT,
        )
        capture = ajouter(
            s, "timesheet_capture", inbox_item_id=element_capture, period="2026-11",
            source="texte", verified=True, verified_at=MOMENT, checked_at=FIN,
        )
        ajouter(
            s, "timesheet_line", capture_id=capture, person="Camille Exemple",
            project_code="PROJ-A", days=4.5,
        )
        ajouter(
            s, "anomaly", capture_id=capture, person="Camille Exemple", rule_id="R1",
            detail="Il manque 2 jours sur novembre.", severity="bloquant",
        )
        ajouter(
            s, "draft", kind="envoi_cr", recipients=["camille.exemple@exemple.test"],
            subject="Compte rendu du point projet", body="Bonjour,\n", status="valide",
            meeting_id=reunion, capture_id=capture, created_at=MOMENT,
        )
        ajouter(
            s, "agent_run", agent="ingestion", target_ref="fichier:1", status="ok",
            started_at=MOMENT, ended_at=FIN, model="qwen-local", prompt_tokens=10,
            completion_tokens=5, error="aucune",
        )
        s.commit()
    return ecrit


def ids(graphe: dict[str, dict[str, Any]]) -> dict[str, int]:
    return {table: valeurs["id"] for table, valeurs in graphe.items()}


# --- Critère 1 -------------------------------------------------------------


def test_c1_init_db_cree_les_quatorze_tables_aux_noms_exacts() -> None:
    from cabinet.db.base import get_engine

    preparer()
    assert set(inspect(get_engine()).get_table_names()) == set(TABLES)


def test_c1_chaque_table_a_son_modele() -> None:
    preparer()
    assert set(TABLES) <= set(classes())


# --- Critère 2 -------------------------------------------------------------


def test_c2_transcript_sans_reunion_existante_echoue() -> None:
    graphe = construire_graphe()
    with session() as s, pytest.raises(IntegrityError):
        creer(
            s, "transcript", meeting_id=9999, inbox_item_id=graphe["inbox_item"]["id"],
            source="notes", text="Texte orphelin.", segments=[],
        )


def test_c2_transcript_sans_reunion_refuse_aussi_en_sql() -> None:
    graphe = construire_graphe()
    with pytest.raises(IntegrityError):
        executer(
            "INSERT INTO transcript (meeting_id, inbox_item_id, source, text, segments) "
            "VALUES (9999, :element, 'notes', 'Texte orphelin.', '[]')",
            element=graphe["inbox_item"]["id"],
        )
    assert compter("transcript") == 1


# --- Critère 3 -------------------------------------------------------------


def test_c3_deux_inbox_item_de_meme_sha256_refuses() -> None:
    preparer()
    with session() as s:
        creer(
            s, "inbox_item", path="entree/a.txt", sha256="c" * 64, kind="notes",
            status="a_rattacher", consent_confirmed=False, detected_at=MOMENT,
        )
        s.commit()
        with pytest.raises(IntegrityError):
            creer(
                s, "inbox_item", path="entree/copie-de-a.txt", sha256="c" * 64, kind="notes",
                status="a_rattacher", consent_confirmed=False, detected_at=MOMENT,
            )
    assert compter("inbox_item") == 1


def test_c3_deux_event_de_memes_uid_et_start_refuses() -> None:
    preparer()
    commun = {"uid": "evt-002@exemple.test", "all_day": False, "attendees": [], "cancelled": False}
    with session() as s:
        creer(s, "event", title="Revue", start=MOMENT, end=FIN, **commun)
        s.commit()
        with pytest.raises(IntegrityError):
            creer(s, "event", title="Revue bis", start=MOMENT, end=FIN, **commun)
    assert compter("event") == 1


def test_c3_meme_instant_ecrit_dans_un_autre_fuseau_reste_un_doublon() -> None:
    """L'unicité porte sur l'instant : `TZDateTime` écrit tout en UTC (`specs/02 §1.1`)."""
    preparer()
    commun = {"uid": "evt-003@exemple.test", "all_day": False, "attendees": [], "cancelled": False}
    with session() as s:
        creer(s, "event", title="Revue", start=MOMENT, end=FIN, **commun)
        s.commit()
        with pytest.raises(IntegrityError):
            creer(
                s, "event", title="Revue", start=MOMENT.astimezone(ZoneInfo("UTC")),
                end=FIN, **commun,
            )


def test_c3_meme_uid_autre_debut_accepte() -> None:
    """Une série récurrente donne une ligne par occurrence (`specs/02 §2.3`)."""
    preparer()
    commun = {"uid": "evt-004@exemple.test", "all_day": False, "attendees": [], "cancelled": False}
    with session() as s:
        creer(s, "event", title="Hebdo", start=MOMENT, end=FIN, **commun)
        creer(
            s, "event", title="Hebdo", start=datetime(2026, 11, 27, 10, 0, tzinfo=ZURICH),
            end=datetime(2026, 11, 27, 11, 0, tzinfo=ZURICH), **commun,
        )
        s.commit()
    assert compter("event") == 2


# --- Critère 4 -------------------------------------------------------------

ENFANTS_DE_LA_REUNION = ("transcript", "prep_note", "minutes", "decision", "action_item")


def test_c4_supprimer_une_reunion_par_le_modele_supprime_ses_enfants() -> None:
    graphe = construire_graphe()
    with session() as s:
        s.delete(s.get(classe("meeting"), graphe["meeting"]["id"]))
        s.commit()
    assert compter("meeting") == 0
    for table in ENFANTS_DE_LA_REUNION:
        assert compter(table) == 0, table


def test_c4_supprimer_une_reunion_en_sql_supprime_ses_enfants() -> None:
    graphe = construire_graphe()
    executer("DELETE FROM meeting WHERE id = :id", id=graphe["meeting"]["id"])
    for table in ENFANTS_DE_LA_REUNION:
        assert compter(table) == 0, table


def test_c4_supprimer_une_reunion_garde_ses_elements_et_brouillons() -> None:
    """`inbox_item` et `draft` restent, `meeting_id` remis à nul (`specs/02 §4`)."""
    graphe = construire_graphe()
    with session() as s:
        s.delete(s.get(classe("meeting"), graphe["meeting"]["id"]))
        s.commit()
    numeros = ids(graphe)
    assert sql("SELECT meeting_id FROM inbox_item WHERE id = :id", id=numeros["inbox_item"]) == [
        (None,)
    ]
    assert sql("SELECT meeting_id FROM draft WHERE id = :id", id=numeros["draft"]) == [(None,)]
    assert compter("event") == 1
    assert compter("series") == 1


# --- Critère 5 -------------------------------------------------------------


@pytest.mark.parametrize("table", TABLES)
def test_c5_aller_retour_de_chaque_modele(table: str) -> None:
    ecrit = construire_graphe()[table]
    relu = relire(table, ecrit["id"])
    for colonne, valeur in ecrit.items():
        assert brut(relu[colonne]) == valeur, f"{table}.{colonne}"


# --- Critère 6 -------------------------------------------------------------


def test_c6_second_init_db_ne_leve_pas_et_ne_perd_rien() -> None:
    from cabinet.db.base import init_db

    graphe = construire_graphe()
    init_db()
    init_db()
    for table in TABLES:
        assert compter(table) == (2 if table == "inbox_item" else 1), table
    assert relire("minutes", graphe["minutes"]["id"])["summary"] == graphe["minutes"]["summary"]


# --- Contrat, hors critères numérotés : schéma (specs/02 §1 et §2) ----------


@pytest.mark.parametrize("table", TABLES)
def test_contrat_colonnes_aux_noms_de_la_spec(table: str) -> None:
    from cabinet.db.base import get_engine

    preparer()
    colonnes = {colonne["name"]: colonne for colonne in inspect(get_engine()).get_columns(table)}
    attendues, nullables = COLONNES[table]
    assert set(colonnes) == attendues
    assert {nom for nom, colonne in colonnes.items() if colonne["nullable"]} == nullables


@pytest.mark.parametrize(("table", "colonne"), sorted(ETATS))
def test_contrat_etats_aux_valeurs_de_la_spec(table: str, colonne: str) -> None:
    from cabinet.db.base import Base

    preparer()
    type_colonne = Base.metadata.tables[table].c[colonne].type
    assert set(getattr(type_colonne, "enums", ())) == ETATS[(table, colonne)]


def test_contrat_etat_stocke_sous_sa_valeur() -> None:
    graphe = construire_graphe()
    requete = "SELECT kind, status FROM inbox_item WHERE id = :id"
    assert sql(requete, id=graphe["inbox_item"]["id"]) == [("teams_transcript", "pret")]
    assert sql("SELECT status FROM draft") == [("valide",)]


def test_contrat_valeurs_par_defaut() -> None:
    preparer()
    with session() as s:
        serie = creer(s, "series", key="defauts", label="Défauts")
        evenement = creer(
            s, "event", uid="evt-005@exemple.test", title="Défauts", start=MOMENT, end=FIN
        )
        reunion = creer(
            s, "meeting", series_id=serie, event_id=evenement, title="Défauts",
            date=date(2026, 11, 20),
        )
        element = creer(
            s, "inbox_item", path="entree/defauts.txt", sha256="d" * 64, kind="notes",
            detected_at=MOMENT,
        )
        transcript = creer(
            s, "transcript", meeting_id=reunion, inbox_item_id=element, source="notes",
            text="Notes.",
        )
        fiche = creer(s, "prep_note", meeting_id=reunion, content_md="", created_at=MOMENT)
        compte_rendu = creer(
            s, "minutes", meeting_id=reunion, version=1, summary="Résumé.", created_at=MOMENT
        )
        decision = creer(
            s, "decision", meeting_id=reunion, minutes_id=compte_rendu, text="D.", quote="d"
        )
        action = creer(
            s, "action_item", meeting_id=reunion, minutes_id=compte_rendu, description="A.",
            quote="a",
        )
        capture = creer(s, "timesheet_capture", inbox_item_id=element, source="texte")
        brouillon = creer(
            s, "draft", kind="relance_cra", subject="Relance", body="Bonjour,\n",
            created_at=MOMENT,
        )
        execution = creer(s, "agent_run", agent="agenda", started_at=MOMENT)
        s.commit()

    attendu = {
        ("series", serie): {"followed": False, "client": None},
        ("event", evenement): {
            "all_day": False, "attendees": [], "cancelled": False, "location": None,
        },
        ("meeting", reunion): {},
        ("inbox_item", element): {
            "status": "nouveau", "consent_confirmed": False, "meeting_id": None,
        },
        ("transcript", transcript): {"segments": []},
        ("prep_note", fiche): {"status": "brouillon"},
        ("minutes", compte_rendu): {
            "content_md": "", "open_points": [], "status": "brouillon", "next_meeting": None,
        },
        ("decision", decision): {"verified": False},
        ("action_item", action): {"verified": False, "status": "ouvert", "owner": None},
        ("timesheet_capture", capture): {"verified": False, "period": None},
        ("draft", brouillon): {"recipients": [], "status": "brouillon", "meeting_id": None},
        ("agent_run", execution): {"target_ref": "", "status": "en_cours"},
    }
    for (table, ident), valeurs in attendu.items():
        relu = relire(table, ident)
        for colonne, valeur in valeurs.items():
            assert brut(relu[colonne]) == valeur, f"{table}.{colonne}"


# --- Contrat : unicités du ticket ------------------------------------------


def test_contrat_series_key_unique() -> None:
    preparer()
    with session() as s:
        creer(s, "series", key="comite", label="Comité")
        s.commit()
        with pytest.raises(IntegrityError):
            creer(s, "series", key="comite", label="Comité bis")


def test_contrat_minutes_meeting_et_version_uniques() -> None:
    graphe = construire_graphe()
    reunion = graphe["meeting"]["id"]
    with session() as s:
        creer(
            s, "minutes", meeting_id=reunion, version=2, summary="Version 2.", created_at=FIN
        )
        s.commit()
        with pytest.raises(IntegrityError):
            creer(
                s, "minutes", meeting_id=reunion, version=2, summary="Doublon.", created_at=FIN
            )
    assert compter("minutes") == 2


def test_contrat_meeting_event_id_unique() -> None:
    graphe = construire_graphe()
    with session() as s, pytest.raises(IntegrityError):
        creer(
            s, "meeting", event_id=graphe["event"]["id"], series_id=graphe["series"]["id"],
            title="Seconde réunion du même évènement", date=date(2026, 11, 20),
        )


def test_contrat_meeting_hors_agenda_sans_evenement() -> None:
    """`meeting.event_id` est unique mais peut être nul pour plusieurs réunions."""
    graphe = construire_graphe()
    with session() as s:
        for jour in (21, 22):
            creer(
                s, "meeting", series_id=graphe["series"]["id"], title="Hors agenda",
                date=date(2026, 11, jour),
            )
        s.commit()
    assert compter("meeting", "event_id IS NULL") == 2


def test_contrat_prep_note_une_seule_par_reunion() -> None:
    graphe = construire_graphe()
    with session() as s, pytest.raises(IntegrityError):
        creer(
            s, "prep_note", meeting_id=graphe["meeting"]["id"], content_md="Seconde fiche.",
            created_at=FIN,
        )


# --- Contrat : suppressions (specs/02 §4) -----------------------------------


def test_contrat_supprimer_un_compte_rendu_supprime_decisions_et_actions() -> None:
    graphe = construire_graphe()
    executer("DELETE FROM minutes WHERE id = :id", id=graphe["minutes"]["id"])
    assert compter("decision") == 0
    assert compter("action_item") == 0
    assert compter("meeting") == 1


@pytest.mark.parametrize("par_le_modele", [True, False])
def test_contrat_supprimer_une_capture_supprime_lignes_et_anomalies(par_le_modele: bool) -> None:
    graphe = construire_graphe()
    capture = graphe["timesheet_capture"]["id"]
    if par_le_modele:
        with session() as s:
            s.delete(s.get(classe("timesheet_capture"), capture))
            s.commit()
    else:
        executer("DELETE FROM timesheet_capture WHERE id = :id", id=capture)
    assert compter("timesheet_capture") == 0
    assert compter("timesheet_line") == 0
    assert compter("anomaly") == 0
    assert sql("SELECT capture_id FROM draft") == [(None,)]


def test_contrat_supprimer_un_evenement_detache_la_reunion() -> None:
    graphe = construire_graphe()
    executer("DELETE FROM event WHERE id = :id", id=graphe["event"]["id"])
    assert sql("SELECT event_id FROM meeting") == [(None,)]


def test_contrat_supprimer_une_serie_avec_reunion_refuse_en_sql() -> None:
    graphe = construire_graphe()
    with pytest.raises(IntegrityError):
        executer("DELETE FROM series WHERE id = :id", id=graphe["series"]["id"])
    assert compter("series") == 1
    assert compter("meeting") == 1


def test_contrat_supprimer_une_serie_avec_reunion_refuse_par_le_modele() -> None:
    graphe = construire_graphe()
    with session() as s, pytest.raises(IntegrityError):
        s.delete(s.get(classe("series"), graphe["series"]["id"]))
        s.commit()
    assert compter("series") == 1
    assert compter("meeting") == 1


# --- Contrat : dates et heures (specs/02 §1.1) ------------------------------


def test_contrat_date_relue_dans_le_fuseau_de_zurich() -> None:
    graphe = construire_graphe()
    debut = relire("event", graphe["event"]["id"])["start"]
    assert debut == MOMENT
    assert debut.tzinfo is not None
    assert debut.utcoffset() == MOMENT.utcoffset()
    assert getattr(debut.tzinfo, "key", None) == "Europe/Zurich"


def test_contrat_date_ecrite_en_utc_sans_fuseau() -> None:
    """10 h à Zurich le 20.11.2026 est 9 h UTC."""
    construire_graphe()
    (valeur,) = sql("SELECT start FROM event")[0]
    assert str(valeur).startswith("2026-11-20 09:00:00")


def test_contrat_date_sans_fuseau_refusee() -> None:
    preparer()
    with session() as s, pytest.raises((ValueError, StatementError)):
        creer(
            s, "event", uid="evt-006@exemple.test", title="Sans fuseau",
            start=datetime(2026, 11, 20, 10, 0), end=datetime(2026, 11, 20, 11, 0),
            all_day=False, attendees=[], cancelled=False,
        )


def test_contrat_cles_etrangeres_actives() -> None:
    from cabinet.db.base import get_engine

    preparer()
    with get_engine().connect() as connexion:
        assert connexion.execute(text("PRAGMA foreign_keys")).scalar() == 1
