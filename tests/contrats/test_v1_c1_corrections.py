"""Tests de contrat du ticket V1-C1, « Corrections de la vague 0 ».

Écrits par le relecteur après la revue de la vague 0 (`revues/vague-0.md`,
réserves R1 à R4). Les critères 1 et 2 portent sur tout `src/` : ils restent
actifs pour les tickets suivants et gardent les deux règles de `specs/01 §5`.

Le critère 5 n'a pas de test ici : les tests de contrat de la vague 0 tournent
à chaque `make check` puisque leurs tickets sont faits.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
SRC = RACINE / "src" / "cabinet"
LONGUEUR_MAX = 40

NOMS_FRANCAIS = {
    "_HOTES_LOCAUX", "_NB_ESSAIS", "_appeler", "_avec_images", "_consigne_schema",
    "_defauts", "_sans_bloc_code", "_somme", "_uri_de_donnees", "champ", "compteurs_compl",
    "compteurs_prompt", "contenu", "corps", "corps_messages", "defauts", "disponibles",
    "donnees", "essai", "faute", "hote", "lignes", "recu", "reponse", "texte", "valeur",
    "valeurs", "erreur", "_activer_cles_etrangeres", "_chemin_base", "_identification",
    "_moteur", "chemin", "connexion", "maison", "moteur", "membre", "sante", "routeur",
    "executions", "gabarits", "resultat",
}


def modules_python() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def arbre(fichier: Path) -> ast.Module:
    return ast.parse(fichier.read_text(encoding="utf-8"), filename=str(fichier))


def noms_definis(module: ast.Module) -> set[str]:
    """Noms que le module définit : fonctions, classes, paramètres, variables."""
    noms: set[str] = set()
    for noeud in ast.walk(module):
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            noms.add(noeud.name)
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = noeud.args
            for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
                noms.add(argument.arg)
            for argument in (arguments.vararg, arguments.kwarg):
                if argument is not None:
                    noms.add(argument.arg)
        elif isinstance(noeud, ast.Name) and isinstance(noeud.ctx, ast.Store):
            noms.add(noeud.id)
        elif isinstance(noeud, ast.ExceptHandler) and noeud.name:
            noms.add(noeud.name)
        elif isinstance(noeud, ast.alias):
            noms.add(noeud.asname or "")
    return noms


# --- Critère 1 -------------------------------------------------------------


def test_c1_aucune_fonction_de_src_ne_depasse_40_lignes() -> None:
    trop_longues = []
    for fichier in modules_python():
        for noeud in ast.walk(arbre(fichier)):
            if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
                longueur = noeud.end_lineno - noeud.lineno + 1
                if longueur > LONGUEUR_MAX:
                    relatif = fichier.relative_to(RACINE)
                    trop_longues.append(f"{relatif}:{noeud.lineno} {noeud.name} ({longueur} lignes)")
    assert trop_longues == [], (
        "specs/01 §5 : une fonction de plus de 40 lignes est découpée. " + ", ".join(trop_longues)
    )


def test_c1_la_regle_de_longueur_compte_la_ligne_def_et_la_docstring() -> None:
    """Garde-fou du test lui-même : une fonction de 41 lignes est bien repérée."""
    corps = "\n".join(f"    x{i} = {i}" for i in range(39))
    source = f'def f():\n    """Docstring."""\n{corps}\n'
    fonction = ast.parse(source).body[0]
    assert fonction.end_lineno - fonction.lineno + 1 == 41


# --- Critère 2 -------------------------------------------------------------


def test_c2_les_identifiants_en_francais_sont_remplaces() -> None:
    restes = []
    for fichier in modules_python():
        for nom in sorted(noms_definis(arbre(fichier)) & NOMS_FRANCAIS):
            restes.append(f"{fichier.relative_to(RACINE)} : {nom}")
    assert restes == [], "specs/01 §5 : identifiants en anglais. " + ", ".join(restes)


def test_c2_les_noms_imposes_par_la_specification_restent() -> None:
    from cabinet import clock
    from cabinet.agents.journal import record_run
    from cabinet.db.base import TZDateTime, get_engine, get_session, init_db
    from cabinet.db.models import AgentRun, RunStatus
    from cabinet.llm.client import LocalLLM, assert_loopback
    from cabinet.llm.types import ConfigError, LLMError, LLMResult, Message
    from cabinet.web.app import create_app

    assert callable(record_run) and callable(assert_loopback) and callable(create_app)
    assert callable(get_engine) and callable(get_session) and callable(init_db)
    assert callable(clock.now) and callable(clock.today)
    assert {s.value for s in RunStatus} == {"en_cours", "ok", "erreur"}
    for classe in (TZDateTime, AgentRun, LocalLLM, ConfigError, LLMError, LLMResult, Message):
        assert inspect.isclass(classe)


# --- Critère 3 -------------------------------------------------------------


def test_c3_la_route_du_journal_n_annonce_pas_une_chaine() -> None:
    """La route vit dans `web/routes/journal.py` (`specs/08 §9`). Lecture par l'arbre syntaxique :
    FastAPI 0.165 n'expose plus les routes d'un routeur inclus dans `app.routes`."""
    module = arbre(SRC / "web" / "routes" / "journal.py")
    servantes = [
        n for n in ast.walk(module)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any("/journal" in ast.unparse(d) and ".get(" in ast.unparse(d) for d in n.decorator_list)
    ]
    assert servantes, "aucune fonction de web/routes/journal.py ne sert GET /journal"
    for fonction in servantes:
        retour = ast.unparse(fonction.returns) if fonction.returns is not None else None
        assert retour not in ("str", "'str'"), f"{fonction.name} annonce -> {retour}"


# --- Critère 4 -------------------------------------------------------------


def _record_run_tests() -> list[ast.FunctionDef]:
    fichier = RACINE / "tests" / "unit" / "test_journal.py"
    assert fichier.is_file(), "tests/unit/test_journal.py a disparu"
    return [
        n for n in arbre(fichier).body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
        and "record_run" in ast.unparse(n)
    ]


def test_c4_un_test_unitaire_couvre_l_interruption() -> None:
    assert any("KeyboardInterrupt" in ast.unparse(t) for t in _record_run_tests())


def test_c4_un_test_unitaire_couvre_l_annulation_du_travail_en_attente() -> None:
    candidats = [
        t for t in _record_run_tests()
        if ".add(" in ast.unparse(t) and "raises" in ast.unparse(t)
    ]
    assert candidats, (
        "il manque un test qui ajoute un objet à la session dans record_run puis lève une exception"
    )
