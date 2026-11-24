#!/usr/bin/env python3
"""Tableau de bord de la chaîne de développement de Cabinet.

Page locale sur http://127.0.0.1:8770/. Elle montre en direct qui parle à qui
(boucle, exécutant, passerelle, modèle, outils, vérification, git), la liste des
sessions de l'exécutant façon « runs », le détail tour par tour de chaque session
et l'état des tickets.

Elle lit sans rien écrire :
- `.chaine/journal.jsonl`, tenu par `scripts/boucle_executant.py`
- `.chaine/sessions/<ticket>-t<n>.jsonl`, sortie complète de chaque session
- `tickets/ETAT.md`
- l'état du modèle dans LM Studio (`lms ps --json`)

Bibliothèque standard seulement. Voir `docs/REPRISE.md`, section 4.

Usage : python3 scripts/suivi.py [--port 8770] [--racine CHEMIN]
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as dt
import json
import os
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ADRESSE = "127.0.0.1"
RE_ID = re.compile(r"V\d+-(?:\d+|C\d+)")
HOTES = {"127.0.0.1", "localhost"}
LMS = Path.home() / ".lmstudio" / "bin" / "lms"


class Depot:
    """Chemins lus par la page, à partir de la racine du dépôt."""

    def __init__(self, racine: Path) -> None:
        self.racine = racine
        self.chaine = racine / ".chaine"
        self.journal = self.chaine / "journal.jsonl"
        self.pid = self.chaine / "boucle.pid"
        self.etat = racine / "tickets" / "ETAT.md"
        self.revues = self.chaine / "revues"


DEPOT = Depot(Path(__file__).resolve().parent.parent)


# --------------------------------------------------------------------------
# Lectures


def maintenant() -> dt.datetime:
    return dt.datetime.now().astimezone()


def secondes(horodatage: str | None) -> float | None:
    if not horodatage:
        return None
    return dt.datetime.fromisoformat(horodatage).timestamp()


def lire_tickets() -> list[dict]:
    tickets = []
    if not DEPOT.etat.exists():
        return tickets
    for ligne in DEPOT.etat.read_text(encoding="utf-8").splitlines():
        cellules = [c.strip() for c in ligne.strip().strip("|").split("|")]
        if len(cellules) == 6 and RE_ID.fullmatch(cellules[0]):
            cles = ("id", "titre", "depend", "etat", "tentatives", "commit")
            tickets.append(dict(zip(cles, cellules, strict=True)))
    return tickets


def lire_jsonl(chemin: Path) -> list[dict]:
    if not chemin.exists():
        return []
    evenements = []
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        try:
            evenements.append(json.loads(ligne))
        except ValueError:
            continue
    return evenements


def lire_journal() -> list[dict]:
    """Journal de la boucle et journal des relecteurs, fusionnés dans l'ordre du temps."""
    boucle = lire_jsonl(DEPOT.journal)
    revues = lire_jsonl(DEPOT.revues / "journal.jsonl")
    if not revues:
        return boucle
    return sorted(boucle + revues, key=lambda e: secondes(e.get("t")) or 0)


AGENTS_REVUE = ("conformite", "principes", "tests", "code", "conduite", "verificateur")


def etat_revues(evenements: list[dict]) -> dict:
    agents = {a: {"etat": "repos"} for a in AGENTS_REVUE}
    for e in evenements:
        if e["evt"] == "agent_debut":
            agents[e["agent"]] = {"etat": "relit", "ticket": e["ticket"], "depuis": e["t"]}
        elif e["evt"] == "agent_fin":
            agents[e["agent"]] = {"etat": "fini", **{k: e.get(k) for k in (
                "ticket", "verdict", "constats", "confirmes", "gravites", "cout", "duree_s")}}
    rapports = lire_rapports()
    try:
        os.kill(int((DEPOT.revues / "veille.pid").read_text()), 0)
        veille = True
    except (OSError, ValueError):
        veille = False
    return {
        "veille": veille,
        "agents": agents,
        "tickets": [{k: r.get(k) for k in ("ticket", "commit", "verdict", "cout_usd", "duree_s")}
                    | {"confirmes": sum(1 for c in r["constats"] if c.get("statut") == "confirme"),
                       "bloquants": sum(1 for c in r["constats"] if c.get("statut") == "confirme"
                                        and c.get("gravite") == "bloquant")}
                    for r in rapports],
        "cout_usd": round(sum(r.get("cout_usd") or 0 for r in rapports), 2),
    }


def lire_rapports() -> list[dict]:
    rapports = []
    for fichier in sorted(DEPOT.revues.glob("V*.json")):
        try:
            rapports.append(json.loads(fichier.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return rapports


def port_ouvert(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((ADRESSE, port)) == 0


def boucle_vivante() -> bool:
    try:
        os.kill(int(DEPOT.pid.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


_LMS_CACHE: dict = {"t": 0.0, "v": None}
_VERROU = threading.Lock()


def etat_lmstudio() -> dict | None:
    """Statut du modèle chargé dans LM Studio, relu au plus toutes les 3 secondes."""
    with _VERROU:
        if time.monotonic() - _LMS_CACHE["t"] < 3:
            return _LMS_CACHE["v"]
        valeur = None
        try:
            sortie = subprocess.run(
                [str(LMS), "ps", "--json"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            ).stdout
            modeles = json.loads(sortie or "[]")
            if modeles:
                m = modeles[0]
                valeur = {
                    "statut": m.get("status"),
                    "nom": m.get("displayName") or m.get("identifier"),
                    "identifiant": m.get("identifier"),
                    "contexte": m.get("contextLength"),
                    "file": m.get("queued"),
                }
            else:
                valeur = {"statut": "aucun"}
        except (OSError, ValueError, subprocess.SubprocessError):
            valeur = None
        _LMS_CACHE.update(t=time.monotonic(), v=valeur)
        return valeur


# --------------------------------------------------------------------------
# Session de l'exécutant (sortie stream-json de Claude Code)


def relatif(chemin: str) -> str:
    return chemin.replace(str(DEPOT.racine) + "/", "")


def resume_entree(nom: str, entree: dict) -> str:
    if nom in ("Read", "Edit"):
        return relatif(str(entree.get("file_path", "")))
    if nom == "Write":
        lignes = len(str(entree.get("content", "")).splitlines())
        return f"{relatif(str(entree.get('file_path', '')))} ({lignes} lignes)"
    if nom == "Bash":
        return relatif(str(entree.get("command", "")))
    if nom in ("Glob", "Grep"):
        morceaux = [str(entree.get("pattern", "")), relatif(str(entree.get("path", "")))]
        return " dans ".join(m for m in morceaux if m)
    return json.dumps(entree, ensure_ascii=False)[:300]


def texte_resultat(contenu: object) -> str:
    if isinstance(contenu, list):
        return "\n".join(b.get("text", "") for b in contenu if isinstance(b, dict))
    return str(contenu or "")


_SESSIONS: dict = {}


def lire_session(fichier: Path) -> dict:
    """Découpe une session en tours. Mis en cache sur la taille et la date du fichier."""
    vide = {"params": {}, "tours": [], "resultat": None, "phase": None, "tokens_en_cours": 0}
    if not fichier.is_file():
        return vide
    try:
        info = fichier.stat()
    except OSError:
        return vide
    cle = (str(fichier), info.st_size, info.st_mtime_ns)
    if _SESSIONS.get(str(fichier), {}).get("cle") == cle:
        return _SESSIONS[str(fichier)]["v"]
    params: dict = {}
    tours: list[dict] = []
    par_id: dict[str, dict] = {}
    par_message: dict[str, dict] = {}
    resultat = None
    phase = None
    tokens = 0
    nouveau = True
    with fichier.open(encoding="utf-8", errors="replace") as f:
        for ligne in f:
            try:
                msg = json.loads(ligne)
            except ValueError:
                continue
            genre, sous = msg.get("type"), msg.get("subtype")
            if genre == "system" and sous == "init":
                params = {
                    "modele": msg.get("model"),
                    "outils": msg.get("tools"),
                    "permissions": msg.get("permissionMode"),
                    "session": msg.get("session_id"),
                    "version_claude_code": msg.get("claude_code_version"),
                }
            elif genre == "system" and sous == "thinking_tokens":
                tokens = int(msg.get("estimated_tokens") or 0)
                nouveau, phase = True, "reflechit"
            elif genre == "assistant":
                message = msg.get("message") or {}
                ident = str(message.get("id") or "")
                tour = par_message.get(ident) if ident else None
                if tour is None:
                    if nouveau or not tours:
                        tours.append(
                            {"n": len(tours) + 1, "pensee": "", "texte": "", "outils": [], "tokens": 0}
                        )
                    tour = tours[-1]
                    if ident:
                        par_message[ident] = tour
                    nouveau = False
                tour["tokens"] = max(tour["tokens"], tokens)
                for bloc in message.get("content") or []:
                    if bloc.get("type") == "thinking":
                        tour["pensee"] += bloc.get("thinking", "")
                    elif bloc.get("type") == "text":
                        tour["texte"] += bloc.get("text", "")
                    elif bloc.get("type") == "tool_use":
                        outil = {
                            "id": bloc.get("id"),
                            "nom": bloc.get("name"),
                            "entree": resume_entree(bloc.get("name", ""), bloc.get("input") or {}),
                            "resultat": None,
                            "erreur": False,
                        }
                        if bloc.get("name") in ("Write", "Edit"):
                            outil["fichier"] = relatif(str((bloc.get("input") or {}).get("file_path", "")))
                        tour["outils"].append(outil)
                        par_id[str(bloc.get("id"))] = outil
                phase = "outil" if tour["outils"] else "ecrit"
                tokens = 0
            elif genre == "user":
                contenu = (msg.get("message") or {}).get("content")
                for bloc in contenu if isinstance(contenu, list) else []:
                    if bloc.get("type") == "tool_result":
                        outil = par_id.get(str(bloc.get("tool_use_id")))
                        if outil is not None:
                            outil["resultat"] = texte_resultat(bloc.get("content"))[:1500]
                            outil["erreur"] = bool(bloc.get("is_error"))
                nouveau, phase = True, "attend"
            elif genre == "result":
                resultat = {
                    "tours": msg.get("num_turns"),
                    "duree_ms": msg.get("duration_ms"),
                    "erreur": msg.get("is_error"),
                    "texte": str(msg.get("result") or "")[:2000],
                }
                phase = "terminee"
    for tour in tours:
        tour["pensee"] = tour["pensee"].strip()[:6000]
        tour["texte"] = tour["texte"].strip()[:3000]
    valeur = {
        "params": params,
        "tours": tours,
        "resultat": resultat,
        "phase": phase,
        "tokens_en_cours": tokens if phase == "reflechit" else 0,
    }
    _SESSIONS[str(fichier)] = {"cle": cle, "v": valeur}
    return valeur


def aligner(tours: list[dict], evenements: list[dict], debut: str) -> None:
    """Date chaque tour avec les évènements « outil » et « texte » du journal, dans l'ordre."""
    outils = [e["t"] for e in evenements if e["evt"] == "outil"]
    textes = [e["t"] for e in evenements if e["evt"] == "texte"]
    k = j = 0
    precedent = secondes(debut)
    for tour in tours:
        instant = None
        for outil in tour["outils"]:
            if k < len(outils):
                outil["t"] = outils[k]
                instant = instant or outils[k]
                k += 1
        if tour["texte"]:
            if j < len(textes):
                instant = instant or textes[j]
            j += 1
        tour["t"] = instant
        s = secondes(instant)
        tour["debut_s"] = precedent
        tour["duree_s"] = round(s - precedent) if s is not None and precedent is not None else None
        if s is not None:
            precedent = s


# --------------------------------------------------------------------------
# Runs et état d'ensemble


def construire_runs(evenements: list[dict], vivante: bool) -> list[dict]:
    runs: list[dict] = []
    courant: dict | None = None
    for e in evenements:
        evt = e["evt"]
        if evt == "session_debut":
            courant = {
                "id": f"{e['ticket']}-t{e['tentative']}",
                "ticket": e["ticket"],
                "tentative": e["tentative"],
                "debut": e["t"],
                "fin": None,
                "fichier": e.get("fichier"),
                "statut": "en_cours",
                "motif": "",
                "commit": None,
                "evenements": [],
            }
            runs.append(courant)
            continue
        if courant is None:
            continue
        if e.get("ticket") == courant["ticket"] and e.get("tentative") == courant["tentative"]:
            courant["evenements"].append(e)
            if evt == "session_fin":
                courant["fin"] = e["t"]
                courant["statut"] = "verification"
                for cle in ("duree_s", "tours", "appels_outils", "erreurs_outils",
                            "refus_permission", "tokens_entree", "tokens_sortie",
                            "delai_depasse", "resume"):
                    courant[cle] = e.get(cle)
            elif evt == "verif":
                courant["statut"] = "reussi" if e.get("ok") else "echec"
                courant["motif"] = e.get("motif") or ""
            elif evt == "commit":
                courant["statut"] = "reussi"
                courant["commit"] = e.get("commit")
        elif evt in ("boucle_arret", "boucle_erreur") and courant["statut"] in ("en_cours", "verification"):
            courant["statut"] = "interrompu"
            courant["fin"] = courant["fin"] or e["t"]
    if runs and runs[-1]["statut"] in ("en_cours", "verification") and not vivante:
        runs[-1]["statut"] = "interrompu"
    for run in runs:
        session = lire_session(Path(run["fichier"])) if run.get("fichier") else lire_session(Path())
        run["tokens_raisonnement"] = sum(t["tokens"] for t in session["tours"]) + session["tokens_en_cours"]
        run["nb_tours"] = len(session["tours"])
        run["nb_outils"] = sum(len(t["outils"]) for t in session["tours"])
        run["nb_erreurs"] = sum(1 for t in session["tours"] for o in t["outils"] if o["erreur"])
        fin = secondes(run["fin"]) or maintenant().timestamp()
        run["duree_s"] = run.get("duree_s") or round(fin - secondes(run["debut"]))
    return runs


def compter_contrats() -> int:
    """Nombre de tests de contrat, chaque cas paramétré compté à part."""
    total = 0
    for fichier in sorted((DEPOT.racine / "tests" / "contrats").glob("test_*.py")):
        try:
            arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for noeud in arbre.body:
            if not (isinstance(noeud, ast.FunctionDef) and noeud.name.startswith("test_")):
                continue
            cas = 1
            for deco in noeud.decorator_list:
                if (
                    isinstance(deco, ast.Call)
                    and isinstance(deco.func, ast.Attribute)
                    and deco.func.attr == "parametrize"
                    and len(deco.args) > 1
                    and isinstance(deco.args[1], (ast.List, ast.Tuple))
                ):
                    cas *= len(deco.args[1].elts)
            total += cas
    return total


def construire_etat() -> dict:
    evenements = lire_journal()
    tickets = lire_tickets()
    vivante = boucle_vivante()
    runs = construire_runs(evenements, vivante)
    debuts = [e for e in evenements if e["evt"] == "boucle_debut"]
    vague = debuts[-1].get("vague") if debuts else None
    en_cours = None
    if runs and runs[-1]["statut"] in ("en_cours", "verification"):
        run = runs[-1]
        session = lire_session(Path(run["fichier"]))
        tours = session["tours"]
        aligner(tours, run["evenements"], run["debut"])
        derniers = [e["t"] for e in run["evenements"]] or [run["debut"]]
        compte: dict[str, int] = {}
        ecrits: set[str] = set()
        for t in tours:
            for o in t["outils"]:
                compte[o["nom"]] = compte.get(o["nom"], 0) + 1
                if o.get("fichier"):
                    ecrits.add(o["fichier"])
        derniere = next((t for t in reversed(tours) if t["pensee"] or t["texte"]), None)
        en_cours = {
            "run": run["id"],
            "ticket": run["ticket"],
            "tentative": run["tentative"],
            "debut": run["debut"],
            "phase": "verification" if run["statut"] == "verification" else session["phase"],
            "tour": len(tours) + (1 if session["phase"] in ("reflechit", "attend") else 0),
            "tokens_tour": session["tokens_en_cours"],
            "tokens_session": run["tokens_raisonnement"],
            "outils": compte,
            "fichiers_ecrits": sorted(ecrits),
            "depuis_dernier_s": round(maintenant().timestamp() - secondes(derniers[-1])),
            "derniere_pensee": (derniere["pensee"] or derniere["texte"])[-900:] if derniere else "",
            "derniere_pensee_tour": derniere["n"] if derniere else None,
        }
    de_la_vague = [t for t in tickets if vague is not None and t["id"].startswith(f"V{vague}-")]
    interventions = [e for e in evenements if e["evt"] == "intervention"]
    verifs = [e for e in evenements if e["evt"] == "verif"]
    commits = [e for e in evenements if e["evt"] == "commit"]
    mesures = {
        "vague": vague,
        "tickets": len(de_la_vague),
        "faits": sum(1 for t in de_la_vague if t["etat"] == "fait"),
        "bloques": sum(1 for t in de_la_vague if t["etat"] == "bloqué"),
        "sessions": len(runs),
        "interventions_thomas": sum(1 for e in interventions if e.get("auteur") == "Thomas"),
        "interventions_relecteur": sum(1 for e in interventions if e.get("auteur") != "Thomas"),
        "debut": debuts[0]["t"] if debuts else None,
        "derniere_verif": verifs[-1] if verifs else None,
        "dernier_reglage": next((e for e in reversed(interventions) if e.get("auteur") != "Thomas"), None),
        "nb_contrats": compter_contrats(),
        "dernier_commit": commits[-1] if commits else None,
    }
    for run in runs:
        run.pop("evenements", None)
    tete = DEPOT.racine / ".git" / "HEAD"
    return {
        "genere": maintenant().isoformat(timespec="seconds"),
        "racine": str(DEPOT.racine),
        "branche": tete.read_text().strip().rsplit("/", 1)[-1] if tete.exists() else "",
        "chaine": {
            "modele_8080": port_ouvert(8080),
            "passerelle_4000": port_ouvert(4000),
            "boucle": vivante,
            "lmstudio": etat_lmstudio(),
        },
        "tickets": tickets,
        "en_cours": en_cours,
        "mesures": mesures,
        "runs": runs,
        "revues": etat_revues(evenements),
        "seq": len(evenements),
        "activite": evenements[-80:],
    }


def detail_run(identifiant: str) -> dict | None:
    evenements = lire_journal()
    runs = construire_runs(evenements, boucle_vivante())
    run = next((r for r in runs if r["id"] == identifiant), None)
    if run is None:
        return None
    session = lire_session(Path(run["fichier"]))
    tours = json.loads(json.dumps(session["tours"]))
    aligner(tours, run["evenements"], run["debut"])
    ecrits = sorted({o["fichier"] for t in tours for o in t["outils"] if o.get("fichier")})
    verifs = sorted(p.name for p in (DEPOT.chaine / "verifs").glob(f"{identifiant}*"))
    return {
        "run": {k: v for k, v in run.items() if k != "evenements"},
        "params": {
            "ticket": run["ticket"],
            "tentative": run["tentative"],
            **session["params"],
            "fichier": relatif(str(run["fichier"])),
        },
        "resultat": session["resultat"],
        "phase": session["phase"],
        "tokens_en_cours": session["tokens_en_cours"],
        "tours": tours,
        "artefacts": {"ecrits": ecrits, "verifs": verifs},
        "evenements": [e for e in run["evenements"] if e["evt"] not in ("outil", "texte")],
    }


def evenements_depuis(rang: int) -> dict:
    evenements = lire_journal()
    if rang > len(evenements):
        return {"seq": len(evenements), "remis_a_zero": True, "evenements": evenements[-80:]}
    return {"seq": len(evenements), "remis_a_zero": False, "evenements": evenements[rang:]}


# --------------------------------------------------------------------------
# Page

PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chaîne Cabinet</title>
<style>
:root{
  color-scheme:light;
  --fond:#f6f4ee; --surface:#fffdf8; --surface-2:#f1ede3; --encre:#0f0e2b; --doux:#5e5c72;
  --trait:#e1dacb; --accent:#1f24e9; --accent-lave:#e8e9fd;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
  --bon:#0ca30c; --alerte:#c98500; --critique:#d03b3b; --gris:#9a98a8; --zc:#a8561a;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace; --corps:system-ui,-apple-system,"Segoe UI",sans-serif;
}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --fond:#121219; --surface:#1a1a22; --surface-2:#23232d; --encre:#eceaf4; --doux:#a3a1b6;
    --trait:#33333f; --accent:#8b8ff7; --accent-lave:#26274a;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
    --bon:#2fbf55; --alerte:#e0a42a; --critique:#e66767; --gris:#6f6d80; --zc:#e39a5c;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --fond:#121219; --surface:#1a1a22; --surface-2:#23232d; --encre:#eceaf4; --doux:#a3a1b6;
  --trait:#33333f; --accent:#8b8ff7; --accent-lave:#26274a;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
  --bon:#2fbf55; --alerte:#e0a42a; --critique:#e66767; --gris:#6f6d80; --zc:#e39a5c;
}
*{box-sizing:border-box}
body{margin:0;background:var(--fond);color:var(--encre);font:14px/1.5 var(--corps)}
code,.mono{font-family:var(--mono);font-size:12.5px}
button{font:inherit;color:inherit}
.haut{display:flex;flex-wrap:wrap;gap:12px 24px;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid var(--trait);background:var(--surface)}
.marque{display:flex;gap:12px;align-items:center}
.logo{width:34px;height:34px;border-radius:9px;background:var(--accent);color:#fff;display:grid;place-items:center;font:700 17px var(--corps)}
.sur{font:11.5px var(--mono);color:var(--doux);letter-spacing:.04em;text-transform:uppercase}
h1{font-size:19px;margin:0;line-height:1.2}
.pastilles{display:flex;flex-wrap:wrap;gap:8px}
.pastille{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;border:1px solid var(--trait);background:var(--surface);font-size:12.5px;white-space:nowrap}
.pastille i{width:8px;height:8px;border-radius:50%;background:var(--gris)}
.pastille.ok i{background:var(--bon)} .pastille.ko i{background:var(--critique)} .pastille.vif i{background:var(--accent);animation:battre 1.2s infinite}
@keyframes battre{50%{transform:scale(1.6);opacity:.55}}
.onglets{display:flex;gap:4px;padding:0 20px;border-bottom:1px solid var(--trait);background:var(--surface);overflow-x:auto}
.onglets button{background:none;border:0;border-bottom:2px solid transparent;padding:10px 12px;cursor:pointer;color:var(--doux);white-space:nowrap}
.onglets button[aria-selected="true"]{color:var(--encre);border-bottom-color:var(--accent);font-weight:600}
main{padding:18px 20px 40px;max-width:1500px;margin:0 auto}
.carte{background:var(--surface);border:1px solid var(--trait);border-radius:10px;padding:14px 16px}
.carte-t{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;font-weight:600;margin-bottom:10px}
.aide{font-weight:400;color:var(--doux);font-size:12.5px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.kpi{background:var(--surface);border:1px solid var(--trait);border-radius:10px;padding:10px 14px}
.kpi b{display:block;font-size:21px;line-height:1.25;font-variant-numeric:tabular-nums}
.kpi span{color:var(--doux);font-size:12.5px}
.kpi.seuil b small{font-size:13px;color:var(--doux);font-weight:400}
.direct{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:14px;align-items:start}
@media (max-width:1100px){.direct{grid-template-columns:minmax(0,1fr)}}
.defile{overflow-x:auto}
#graphe{display:block;width:100%;min-width:900px;height:auto}
.zone{stroke-width:1.3}
.zone.zq{fill:color-mix(in srgb,var(--accent) 5%,var(--surface));stroke:color-mix(in srgb,var(--accent) 40%,var(--surface))}
.zone.zc{fill:color-mix(in srgb,var(--zc) 9%,var(--surface));stroke:color-mix(in srgb,var(--zc) 50%,var(--surface))}
.zone.zb{fill:var(--surface-2);stroke:var(--trait)}
#graphe .zt{font:600 11.5px var(--mono);letter-spacing:.06em;fill:var(--doux)}
#graphe .zq-t{fill:var(--accent)} #graphe .zc-t{fill:var(--zc)}
#graphe .zs{font-size:11px;fill:var(--doux)}
.noeud.claude rect.fond{stroke:color-mix(in srgb,var(--zc) 55%,var(--surface))}
.noeud.claude .b{fill:var(--zc) !important}
.legende i.z{border:1.3px solid}
.legende i.zq{background:color-mix(in srgb,var(--accent) 10%,var(--surface));border-color:var(--accent)}
.legende i.zc{background:color-mix(in srgb,var(--zc) 14%,var(--surface));border-color:var(--zc)}
.legende i.zb{background:var(--surface-2);border-color:var(--gris)}
#graphe text{fill:var(--encre);font-family:var(--corps)}
.noeud rect.fond{fill:var(--surface);stroke:var(--trait);stroke-width:1.4;transition:stroke .3s,fill .3s}
.noeud .t{font-size:13.5px;font-weight:600}
.noeud .s{font-size:11px;fill:var(--doux) !important}
.noeud .b{font:11.5px var(--mono);fill:var(--accent) !important}
.noeud.humain rect.fond{stroke-dasharray:5 4}
.noeud.actif rect.fond{stroke:var(--accent);stroke-width:2.4;fill:var(--accent-lave)}
.noeud.eclat-ok rect.fond{stroke:var(--bon);stroke-width:3}
.noeud.eclat-ko rect.fond{stroke:var(--critique);stroke-width:3}
.noeud.eclat-alerte rect.fond{stroke:var(--alerte);stroke-width:3}
.noeud.eclat rect.fond{stroke:var(--accent);stroke-width:3}
.noeud.lecture .marque-f{fill:var(--s1)} .noeud.ecriture .marque-f{fill:var(--s2)} .noeud.shell .marque-f{fill:var(--s3)} .noeud.recherche .marque-f{fill:var(--s4)}
.arete{fill:none;stroke:var(--trait);stroke-width:1.8}
.arete.vif{stroke:var(--accent);stroke-dasharray:6 7;animation:defiler 0.9s linear infinite;stroke-width:2.2}
@keyframes defiler{to{stroke-dashoffset:-26}}
.fleche{fill:var(--gris)}
.etiq{font-size:10.5px;fill:var(--doux) !important;paint-order:stroke;stroke:var(--surface);stroke-width:4px;stroke-linejoin:round}
.jauge-fond{fill:var(--surface-2)} .jauge{fill:var(--accent);transition:width .8s}
.particule{filter:drop-shadow(0 0 4px currentColor)}
.pensee{margin-top:12px;border-top:1px solid var(--trait);padding-top:12px}
.pensee .ligne{color:var(--doux);font-size:13px}
.pensee .ligne b{color:var(--encre)}
.pensee blockquote{margin:8px 0 0;padding:10px 12px;border-left:3px solid var(--accent);background:var(--surface-2);border-radius:0 8px 8px 0;white-space:pre-wrap;font-size:13px;max-height:220px;overflow:auto}
.legende{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:var(--doux);margin-top:8px}
.legende i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.flux{max-height:760px;display:flex;flex-direction:column}
#flux{list-style:none;margin:0;padding:0;overflow:auto;flex:1}
#flux li{display:grid;grid-template-columns:52px 14px 1fr;gap:8px;padding:7px 2px;border-bottom:1px solid var(--trait);font-size:12.5px;animation:entrer .5s ease-out}
@keyframes entrer{from{background:var(--accent-lave)}}
#flux .h{font:11.5px var(--mono);color:var(--doux)}
#flux .p{width:10px;height:10px;border-radius:50%;margin-top:4px;background:var(--gris)}
#flux .l b{font-weight:600} #flux .l span{color:var(--doux);word-break:break-word}
.p.lecture{background:var(--s1)!important}.p.ecriture{background:var(--s2)!important}.p.shell{background:var(--s3)!important}.p.recherche{background:var(--s4)!important}
.p.claude{background:var(--zc)!important}.p.ok{background:var(--bon)!important}.p.ko{background:var(--critique)!important}.p.alerte{background:var(--alerte)!important}.p.boucle{background:var(--accent)!important}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--trait);white-space:nowrap}
th{font-size:12px;color:var(--doux);font-weight:600}
tbody tr{cursor:pointer} tbody tr:hover{background:var(--surface-2)} tbody tr.sel{background:var(--accent-lave)}
td.n{text-align:right} th.n{text-align:right}
.statut{display:inline-flex;gap:5px;align-items:center;font-size:12.5px;font-weight:600}
.statut::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--gris)}
.statut.reussi{color:var(--bon)} .statut.reussi::before{background:var(--bon)}
.statut.echec,.statut.bloque{color:var(--critique)} .statut.echec::before,.statut.bloque::before{background:var(--critique)}
.statut.en_cours,.statut.verification{color:var(--accent)} .statut.en_cours::before,.statut.verification::before{background:var(--accent);animation:battre 1.2s infinite}
.statut.interrompu{color:var(--alerte)} .statut.interrompu::before{background:var(--alerte)}
#detail{margin-top:14px;display:grid;gap:14px}
.grille2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}
@media (max-width:900px){.grille2{grid-template-columns:1fr}}
.params{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:13px}
.params dt{color:var(--doux)} .params dd{margin:0;word-break:break-word}
.graphique svg{display:block;width:100%;height:auto}
.graphique text{fill:var(--doux);font-size:11px;font-family:var(--corps)}
.axe{stroke:var(--trait)} .grille{stroke:var(--trait);stroke-dasharray:2 4}
.barre{fill:var(--s1)} .barre:hover,.barre.sur{fill:var(--accent)}
.barre.encours{fill:var(--accent);opacity:.45;animation:battre-o 1.6s infinite}
@keyframes battre-o{50%{opacity:.25}}
.pens.encours{fill:var(--accent);opacity:.4;animation:battre-o 1.6s infinite}
.lane{fill:var(--surface-2)}
.pens{fill:var(--gris);opacity:.45}
.mk.lecture{fill:var(--s1)}.mk.ecriture{fill:var(--s2)}.mk.shell{fill:var(--s3)}.mk.recherche{fill:var(--s4)}
.mk{stroke:var(--surface);stroke-width:2}
.mk.err{stroke:var(--critique);stroke-width:2.5}
.tours details{border-bottom:1px solid var(--trait);padding:8px 0}
.tours summary{cursor:pointer;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}
.tours summary .num{font:600 12px var(--mono);color:var(--accent);min-width:56px}
.tours summary .meta{color:var(--doux);font-size:12.5px}
.tours .corps{padding:8px 0 4px 68px;display:grid;gap:8px}
@media (max-width:700px){.tours .corps{padding-left:0}}
.tours pre{margin:0;white-space:pre-wrap;word-break:break-word;font:12px/1.5 var(--mono);background:var(--surface-2);padding:8px 10px;border-radius:8px;max-height:260px;overflow:auto}
.tours .pens-t{white-space:pre-wrap;font-size:13px;color:var(--doux);font-style:italic}
.outil-l{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:5px;font:600 11.5px var(--mono);padding:1px 8px;border-radius:999px;border:1px solid var(--trait)}
.chip i{width:8px;height:8px;border-radius:2px}
.chip.lecture i{background:var(--s1)}.chip.ecriture i{background:var(--s2)}.chip.shell i{background:var(--s3)}.chip.recherche i{background:var(--s4)}
.chip.err{border-color:var(--critique);color:var(--critique)}
.filtres{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.filtres button{border:1px solid var(--trait);background:var(--surface);padding:4px 12px;border-radius:999px;cursor:pointer}
.filtres button[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.kanban{display:grid;grid-template-columns:repeat(4,minmax(220px,1fr));gap:12px;overflow-x:auto}
@media (max-width:700px){.kanban{grid-template-columns:minmax(0,1fr)} .haut,main{padding-left:16px;padding-right:16px}}
.colonne{background:var(--surface-2);border-radius:10px;padding:10px;min-height:120px}
.colonne h3{margin:0 0 8px;font-size:13px;display:flex;justify-content:space-between}
.ticket{background:var(--surface);border:1px solid var(--trait);border-radius:8px;padding:8px 10px;margin-bottom:8px}
.ticket .id{font:600 12px var(--mono);color:var(--accent)}
.ticket .ti{font-size:13px;margin:2px 0}
.ticket .me{font-size:11.5px;color:var(--doux)}
.ticket.courant{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-lave);animation:respirer 2s infinite}
@keyframes respirer{50%{box-shadow:0 0 0 6px var(--accent-lave)}}
.bulle{position:fixed;z-index:9;pointer-events:none;background:var(--encre);color:var(--surface);padding:6px 9px;border-radius:7px;font-size:12px;max-width:340px;white-space:pre-line}
.vide{color:var(--doux);font-size:13px;padding:6px 0}
#revues{display:grid;gap:14px}
.synthese{margin:0 0 10px;max-width:90ch}
.angles{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;margin-bottom:12px}
.angle{background:var(--surface-2);border-radius:8px;padding:8px 10px;font-size:13px}
.angle div{color:var(--doux);margin-top:4px}
table.constats td{white-space:normal;vertical-align:top} table.constats td.txt{min-width:320px}
table.constats tbody tr{cursor:default}
table.constats tr.st-rejete td{opacity:.55} table.constats tr.st-rejete summary{text-decoration:line-through}
.chip.st.confirme{border-color:var(--bon);color:var(--bon)} .chip.st.rejete{color:var(--doux)} .chip.st.incertain{border-color:var(--alerte);color:var(--alerte)}
.grav{font:600 12px var(--mono)} .grav.bloquant{color:var(--critique)} .grav.majeur{color:var(--alerte)} .grav.mineur,.grav.remarque{color:var(--doux)}
.forts{margin-top:10px}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<header class="haut">
  <div class="marque"><div class="logo" aria-hidden="true">C</div>
    <div><div class="sur">Cabinet · chaîne de développement</div><h1 id="titre">Vague</h1></div></div>
  <div class="pastilles" id="pastilles" aria-live="polite"></div>
</header>
<nav class="onglets" role="tablist">
  <button role="tab" data-onglet="direct" aria-selected="true">Direct</button>
  <button role="tab" data-onglet="runs" aria-selected="false">Runs</button>
  <button role="tab" data-onglet="tickets" aria-selected="false">Tickets</button>
  <button role="tab" data-onglet="revues" aria-selected="false">Revues</button>
</nav>
<main>
<section id="v-direct">
  <div class="kpis" id="kpis"></div>
  <div class="direct">
    <div class="carte">
      <div class="carte-t">Qui fait quoi <span class="aide">Qwen développe, la boucle vérifie à chaque session, six agents Claude relisent chaque commit, Claude juge la vague. Un point circule à chaque échange.</span></div>
      <div class="defile">
      <svg id="graphe" viewBox="0 0 1250 800" role="img" aria-label="Qui fait quoi. En bas, six relecteurs automatiques Claude relisent chaque commit puis un vérificateur confirme leurs constats. À gauche, la boucle, un script sans IA, lance les sessions, lance make check et fait les commits. Au centre, Qwen, en local, développe : il écrit le code par Claude Code, avec quatre familles d'outils, dans le dépôt. À droite, Claude, distant, contrôle : il a écrit les tests de contrat que make check exécute, il règle la chaîne et il relit tout le code à la fin de la vague.">
        <defs><marker id="fl" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path class="fleche" d="M0,0L10,5L0,10z"/></marker></defs>
        <rect class="zone zb" x="20" y="112" width="232" height="420" rx="14"/>
        <text class="zt" x="36" y="134">BOUCLE · VÉRIFIE</text><text class="zs" x="36" y="150">script, sans IA</text>
        <rect class="zone zq" x="268" y="112" width="728" height="420" rx="14"/>
        <text class="zt zq-t" x="284" y="134">QWEN · DÉVELOPPE</text><text class="zs" x="284" y="150">modèle local, sur ce Mac. Il écrit tout le code.</text>
        <rect class="zone zc" x="1012" y="112" width="224" height="420" rx="14"/>
        <text class="zt zc-t" x="1028" y="134">CLAUDE · CONTRÔLE</text><text class="zs" x="1028" y="150">distant, n'écrit aucun code</text>
        <g id="aretes">
          <path class="arete" id="e-thomas-boucle" d="M200,80 L200,176" marker-end="url(#fl)"/>
          <path class="arete" id="e-relecteur-boucle" d="M1124,172 V104 Q1124,92 1112,92 H238 Q226,92 226,104 V176" marker-end="url(#fl)"/>
          <path class="arete" id="e-boucle-exec" d="M232,206 L346,206" marker-end="url(#fl)"/>
          <path class="arete" id="e-exec-boucle" d="M346,240 L234,240" marker-end="url(#fl)"/>
          <path class="arete" id="e-exec-pass" d="M562,220 L596,220" marker-end="url(#fl)"/>
          <path class="arete" id="e-pass-modele" d="M742,220 L776,220" marker-end="url(#fl)"/>
          <path class="arete" id="e-exec-lecture" d="M395,262 C395,300 355,296 355,326" marker-end="url(#fl)"/>
          <path class="arete" id="e-exec-recherche" d="M435,262 C435,300 500,296 500,326" marker-end="url(#fl)"/>
          <path class="arete" id="e-exec-ecriture" d="M480,262 C480,300 645,296 645,326" marker-end="url(#fl)"/>
          <path class="arete" id="e-exec-shell" d="M520,262 C520,300 790,296 790,326" marker-end="url(#fl)"/>
          <path class="arete" id="e-lecture-depot" d="M355,386 C355,420 470,416 470,446" marker-end="url(#fl)"/>
          <path class="arete" id="e-recherche-depot" d="M500,386 C500,420 530,416 530,446" marker-end="url(#fl)"/>
          <path class="arete" id="e-ecriture-depot" d="M645,386 C645,420 610,416 610,446" marker-end="url(#fl)"/>
          <path class="arete" id="e-shell-depot" d="M790,386 C790,420 680,416 680,446" marker-end="url(#fl)"/>
          <path class="arete" id="e-boucle-verif" d="M136,262 L136,326" marker-end="url(#fl)"/>
          <path class="arete" id="e-verif-depot" d="M232,384 C290,440 340,494 416,494" marker-end="url(#fl)"/>
          <path class="arete" id="e-verif-git" d="M136,392 L136,446" marker-end="url(#fl)"/>
          <path class="arete" id="e-relecteur-contrats" d="M1124,242 L1124,300" marker-end="url(#fl)"/>
          <path class="arete" id="e-contrats-verif" d="M1030,352 C1000,352 1004,552 960,552 L262,552 C240,552 238,420 226,394" marker-end="url(#fl)"/>
          <path class="arete" id="e-depot-revue" d="M718,486 C860,486 900,473 1028,473" marker-end="url(#fl)"/>
        </g>
        <text class="etiq" x="290" y="198" text-anchor="middle">lance une session</text>
        <text class="etiq" x="290" y="256" text-anchor="middle">bilan</text>
        <text class="etiq" x="579" y="208" text-anchor="middle">API</text>
        <text class="etiq" x="759" y="208" text-anchor="middle">API</text>
        <text class="etiq" x="148" y="300">à chaque fin de session</text>
        <text class="etiq" x="148" y="425">si tout passe</text>
        <text class="etiq" x="660" y="88" text-anchor="middle">règle la chaîne si elle coince</text>
        <text class="etiq" x="1124" y="276" text-anchor="middle">écrits avant la vague</text>
        <text class="etiq" x="610" y="547" text-anchor="middle">exécutés par make check à chaque session</text>
        <text class="etiq" x="880" y="468" text-anchor="middle">relit tout le code à la fin</text>
        <g id="noeuds">
          <g class="noeud humain" id="n-thomas"><rect class="fond" x="20" y="24" width="232" height="56" rx="10"/><text class="t" x="36" y="48">Thomas</text><text class="s" x="36" y="66">commanditaire, décide de la suite</text></g>
          <g class="noeud" id="n-boucle"><rect class="fond" x="40" y="178" width="192" height="84" rx="10"/><text class="t" x="56" y="202">Boucle</text><text class="s" x="56" y="220">boucle_executant.py</text><text class="b" id="b-boucle" x="56" y="246"></text></g>
          <g class="noeud" id="n-verif"><rect class="fond" x="40" y="328" width="192" height="64" rx="10"/><text class="t" x="56" y="352">make check</text><text class="s" x="56" y="370">ruff, tests de Qwen, contrats</text><text class="b" id="b-verif" x="56" y="386"></text></g>
          <g class="noeud" id="n-git"><rect class="fond" x="40" y="448" width="192" height="62" rx="10"/><text class="t" x="56" y="472">Git</text><text class="s" x="56" y="490">un commit par ticket réussi</text><text class="b" id="b-git" x="216" y="472" text-anchor="end"></text></g>
          <g class="noeud" id="n-exec"><rect class="fond" x="348" y="178" width="214" height="84" rx="10"/><text class="t" x="364" y="202">Exécutant · Qwen</text><text class="s" x="364" y="220">écrit le code, via Claude Code</text><text class="b" id="b-exec" x="364" y="246"></text></g>
          <g class="noeud" id="n-pass"><rect class="fond" x="598" y="192" width="144" height="56" rx="10"/><text class="t" x="612" y="216">Passerelle</text><text class="s" x="612" y="234">LiteLLM · 4000</text></g>
          <g class="noeud" id="n-modele"><rect class="fond" x="778" y="168" width="200" height="104" rx="10"/><text class="t" x="794" y="192" id="t-modele">Qwen3.8 27B</text><text class="s" x="794" y="210">le modèle · LM Studio · 8080</text><text class="b" id="b-modele" x="794" y="236"></text><rect class="jauge-fond" x="794" y="250" width="168" height="6" rx="3"/><rect class="jauge" id="jauge" x="794" y="250" width="0" height="6" rx="3"/></g>
          <g class="noeud lecture" id="n-lecture"><rect class="fond" x="290" y="328" width="130" height="58" rx="10"/><rect class="marque-f" x="290" y="328" width="5" height="58" rx="2"/><text class="t" x="304" y="350">Lecture</text><text class="s" x="304" y="368">Read</text><text class="b" id="b-lecture" x="406" y="350" text-anchor="end"></text></g>
          <g class="noeud recherche" id="n-recherche"><rect class="fond" x="435" y="328" width="130" height="58" rx="10"/><rect class="marque-f" x="435" y="328" width="5" height="58" rx="2"/><text class="t" x="449" y="350">Recherche</text><text class="s" x="449" y="368">Glob, Grep</text><text class="b" id="b-recherche" x="551" y="350" text-anchor="end"></text></g>
          <g class="noeud ecriture" id="n-ecriture"><rect class="fond" x="580" y="328" width="130" height="58" rx="10"/><rect class="marque-f" x="580" y="328" width="5" height="58" rx="2"/><text class="t" x="594" y="350">Écriture</text><text class="s" x="594" y="368">Write, Edit</text><text class="b" id="b-ecriture" x="696" y="350" text-anchor="end"></text></g>
          <g class="noeud shell" id="n-shell"><rect class="fond" x="725" y="328" width="130" height="58" rx="10"/><rect class="marque-f" x="725" y="328" width="5" height="58" rx="2"/><text class="t" x="739" y="350">Shell</text><text class="s" x="739" y="368">Bash</text><text class="b" id="b-shell" x="841" y="350" text-anchor="end"></text></g>
          <g class="noeud" id="n-depot"><rect class="fond" x="418" y="448" width="300" height="62" rx="10"/><text class="t" x="434" y="472">Dépôt</text><text class="s" x="434" y="490">code écrit par Qwen : src/, tests/unit/</text><text class="b" id="b-depot" x="702" y="472" text-anchor="end"></text></g>
          <g class="noeud claude" id="n-relecteur"><rect class="fond" x="1030" y="172" width="188" height="70" rx="10"/><text class="t" x="1046" y="196">Relecteur · Claude</text><text class="s" x="1046" y="214">surveille, règle, juge</text><text class="b" id="b-relecteur" x="1046" y="232"></text></g>
          <g class="noeud claude" id="n-contrats"><rect class="fond" x="1030" y="302" width="188" height="66" rx="10"/><text class="t" x="1046" y="326">Tests de contrat</text><text class="s" x="1046" y="344">tests/contrats, par Claude</text><text class="b" id="b-contrats" x="1046" y="360"></text></g>
          <g class="noeud claude" id="n-revue"><rect class="fond" x="1030" y="440" width="188" height="66" rx="10"/><text class="t" x="1046" y="464">Revue de vague</text><text class="s" x="1046" y="482">phase B, rapport, verdict</text><text class="b" id="b-revue" x="1046" y="498"></text></g>
        </g>
        <rect class="zone zc" x="20" y="588" width="1216" height="196" rx="14"/>
        <text class="zt zc-t" x="36" y="610">CLAUDE · RELECTEURS AUTOMATIQUES</text><text class="zs" x="36" y="626">six agents relisent chaque ticket commité, en lecture seule, sans rien corriger</text>
        <g id="aretes-revue">
          <path class="arete" id="e-git-a-conformite" d="M136,510 V632 H130 V648" marker-end="url(#fl)"/>
          <path class="arete" id="e-git-a-principes" d="M136,510 V632 H284 V648" marker-end="url(#fl)"/>
          <path class="arete" id="e-git-a-tests" d="M136,510 V632 H438 V648" marker-end="url(#fl)"/>
          <path class="arete" id="e-git-a-code" d="M136,510 V632 H592 V648" marker-end="url(#fl)"/>
          <path class="arete" id="e-git-a-conduite" d="M136,510 V632 H746 V648" marker-end="url(#fl)"/>
          <path class="arete" id="e-a-conformite-verificateur" d="M130,712 V748 H935 V714" marker-end="url(#fl)"/>
          <path class="arete" id="e-a-principes-verificateur" d="M284,712 V748 H935 V714" marker-end="url(#fl)"/>
          <path class="arete" id="e-a-tests-verificateur" d="M438,712 V748 H935 V714" marker-end="url(#fl)"/>
          <path class="arete" id="e-a-code-verificateur" d="M592,712 V748 H935 V714" marker-end="url(#fl)"/>
          <path class="arete" id="e-a-conduite-verificateur" d="M746,712 V748 H935 V714" marker-end="url(#fl)"/>
          <path class="arete" id="e-a-verificateur-rapport" d="M1010,681 H1046" marker-end="url(#fl)"/>
          <path class="arete" id="e-a-rapport-revue" d="M1133,648 V510" marker-end="url(#fl)"/>
        </g>
        <text class="etiq" x="148" y="578">à chaque commit</text>
        <text class="etiq" x="560" y="766" text-anchor="middle">constats à vérifier</text>
        <text class="etiq" x="1142" y="566">nourrit la phase B</text>
        <g id="noeuds-revue">
          <g class="noeud claude" id="n-a-conformite"><rect class="fond" x="60" y="650" width="140" height="62" rx="10"/><text class="t" x="74" y="672">Conformité</text><text class="s" x="74" y="688">ticket et specs/</text><text class="b" id="b-a-conformite" x="74" y="704"></text></g>
          <g class="noeud claude" id="n-a-principes"><rect class="fond" x="214" y="650" width="140" height="62" rx="10"/><text class="t" x="228" y="672">Principes</text><text class="s" x="228" y="688">les sept principes</text><text class="b" id="b-a-principes" x="228" y="704"></text></g>
          <g class="noeud claude" id="n-a-tests"><rect class="fond" x="368" y="650" width="140" height="62" rx="10"/><text class="t" x="382" y="672">Tests de Qwen</text><text class="s" x="382" y="688">pas de test complaisant</text><text class="b" id="b-a-tests" x="382" y="704"></text></g>
          <g class="noeud claude" id="n-a-code"><rect class="fond" x="522" y="650" width="140" height="62" rx="10"/><text class="t" x="536" y="672">Code</text><text class="s" x="536" y="688">conventions, lisibilité</text><text class="b" id="b-a-code" x="536" y="704"></text></g>
          <g class="noeud claude" id="n-a-conduite"><rect class="fond" x="676" y="650" width="140" height="62" rx="10"/><text class="t" x="690" y="672">Conduite</text><text class="s" x="690" y="688">la session de Qwen</text><text class="b" id="b-a-conduite" x="690" y="704"></text></g>
          <g class="noeud claude" id="n-a-verificateur"><rect class="fond" x="860" y="650" width="150" height="62" rx="10"/><text class="t" x="874" y="672">Vérificateur</text><text class="s" x="874" y="688">confirme ou rejette</text><text class="b" id="b-a-verificateur" x="874" y="704"></text></g>
          <g class="noeud claude" id="n-a-rapport"><rect class="fond" x="1048" y="650" width="170" height="62" rx="10"/><text class="t" x="1062" y="672">Rapport du ticket</text><text class="s" x="1062" y="688">.chaine/revues/</text><text class="b" id="b-a-rapport" x="1062" y="704"></text></g>
        </g>
        <g id="particules"></g>
      </svg>
      </div>
      <div class="legende" aria-label="Zones et familles d'outils">
        <span><i class="z zq"></i>Qwen, développe</span><span><i class="z zb"></i>boucle, vérifie</span><span><i class="z zc"></i>Claude, contrôle</span>
        <span><i style="background:var(--s1)"></i>Lecture</span><span><i style="background:var(--s4)"></i>Recherche</span>
        <span><i style="background:var(--s2)"></i>Écriture</span><span><i style="background:var(--s3)"></i>Shell</span>
        <span><i style="background:var(--bon)"></i>vérification réussie</span><span><i style="background:var(--critique)"></i>échec ou refus</span>
      </div>
      <div class="pensee" id="pensee"></div>
    </div>
    <aside class="carte flux"><div class="carte-t">Fil des évènements <span class="aide" id="flux-aide"></span></div><ol id="flux"></ol></aside>
  </div>
</section>
<section id="v-runs" hidden>
  <div class="carte"><div class="carte-t">Runs <span class="aide">une session de l'exécutant par ligne. Cliquez pour le détail.</span></div>
    <div class="defile"><table id="runs"><thead><tr><th>Run</th><th>Statut</th><th>Début</th><th class="n">Durée</th><th class="n">Tours</th><th class="n">Outils</th><th class="n">Erreurs</th><th class="n">Tokens de raisonnement</th><th>Verdict</th></tr></thead><tbody></tbody></table></div>
  </div>
  <div id="detail"></div>
</section>
<section id="v-revues" hidden>
  <div class="kpis" id="kpis-r"></div>
  <div id="revues"></div>
</section>
<section id="v-tickets" hidden>
  <div class="kpis" id="kpis-t"></div>
  <div class="filtres" id="filtres"></div>
  <div class="kanban" id="kanban"></div>
</section>
</main>
<div class="bulle" id="bulle" hidden></div>
<script>
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const FAM = {Read:"lecture", Glob:"recherche", Grep:"recherche", Write:"ecriture", Edit:"ecriture", Bash:"shell"};
const FAM_NOM = {lecture:"Lecture", recherche:"Recherche", ecriture:"Écriture", shell:"Shell"};
const STATUTS = {reussi:"réussi", echec:"échec", en_cours:"en cours", verification:"vérification", interrompu:"interrompu"};
const LIBELLES = {boucle_debut:"Début de la boucle", boucle_fin:"Fin de la boucle", boucle_arret:"Boucle interrompue",
  boucle_erreur:"Erreur de la boucle", session_debut:"Début de session", session_init:"Session prête", session_fin:"Fin de session",
  outil:"Outil", outil_erreur:"Refus ou erreur d'outil", texte:"Qwen écrit", garde:"Fichier protégé restauré",
  hors_liste:"Fichier hors du ticket", install:"make install", verif:"Vérification", commit:"Commit", bloque:"Ticket bloqué",
  saute:"Ticket sauté", intervention:"Intervention", remise:"Ticket remis à faire", nettoyage:"Arbre mis de côté", alerte:"Alerte",
  veille_debut:"Relecteurs en veille", revue_debut:"Relecture lancée", agent_debut:"Relecteur au travail", agent_fin:"Relecteur terminé",
  revue_fin:"Relecture terminée", revue_erreur:"Relecture en échec"};
const AGENTS = ["conformite","principes","tests","code","conduite"];
const NOMS_A = {conformite:"Conformité", principes:"Principes", tests:"Tests de Qwen", code:"Code", conduite:"Conduite", verificateur:"Vérificateur"};
const VERDICT = {ok:"✓ ok", reserves:"⚠ réserves", ko:"✕ ko", erreur:"erreur"};
const COUL_V = {ok:"bon", reserves:"alerte", ko:"critique", erreur:"critique"};
const gravTxt = g => g ? Object.entries(g).map(([k,v]) => `${v} ${k}${v > 1 ? "s" : ""}`).join(", ") : "";
const nf = new Intl.NumberFormat("fr-FR");
let etat = null, seq = null, onglet = "direct", runSel = null, lotFiltre = null, file = [], enLecture = false;

function duree(s){ if (s == null) return ""; s = Math.max(0, Math.round(s)); const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60;
  return h ? `${h} h ${String(m).padStart(2,"0")}` : m ? `${m} min ${String(x).padStart(2,"0")} s` : `${x} s`; }
const heure = t => t ? new Date(t).toLocaleTimeString("fr-FR",{hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "";
const depuis = t => (Date.now() - new Date(t).getTime())/1000;
async function api(u){ const r = await fetch(u, {cache:"no-store"}); if (!r.ok) throw new Error(r.status); return r.json(); }

/* ---------- onglets ---------- */
document.querySelectorAll(".onglets button").forEach(b => b.addEventListener("click", () => {
  onglet = b.dataset.onglet;
  document.querySelectorAll(".onglets button").forEach(x => x.setAttribute("aria-selected", x === b));
  for (const v of ["direct","runs","tickets","revues"]) $("#v-"+v).hidden = v !== onglet;
  history.replaceState(null, "", onglet === "direct" ? location.pathname : "#" + onglet);
  rendre();
}));
{ const h = location.hash.slice(1); if (["runs","tickets","revues"].includes(h)) document.querySelector(`[data-onglet="${h}"]`).click(); }

/* ---------- graphe animé ---------- */
const NS = "http://www.w3.org/2000/svg";
function particule(id, classe, inverse=false, ms=850){
  return new Promise(ok => {
    const p = document.getElementById(id); if (!p) return ok();
    const L = p.getTotalLength(), c = document.createElementNS(NS, "circle");
    c.setAttribute("r", 5.5); c.setAttribute("class", "particule");
    c.style.fill = `var(--${classe})`; c.style.color = `var(--${classe})`;
    $("#particules").appendChild(c);
    const t0 = performance.now();
    (function pas(t){ const k = Math.min(1, (t - t0)/ms), e = k<.5 ? 2*k*k : 1-Math.pow(-2*k+2,2)/2;
      const pt = p.getPointAtLength((inverse ? 1-e : e) * L); c.setAttribute("cx", pt.x); c.setAttribute("cy", pt.y);
      if (k < 1) requestAnimationFrame(pas); else { c.remove(); ok(); } })(t0);
  });
}
function eclat(noeud, classe="eclat"){ const n = document.getElementById("n-"+noeud); if (!n) return;
  n.classList.remove("eclat","eclat-ok","eclat-ko","eclat-alerte"); void n.getBBox(); n.classList.add(classe);
  setTimeout(() => n.classList.remove(classe), 1400); }
const COULEUR = {lecture:"s1", recherche:"s4", ecriture:"s2", shell:"s3"};
async function reponseModele(){ await particule("e-pass-modele","accent",true,600); await particule("e-exec-pass","accent",true,500); eclat("exec"); }
async function scene(ev){
  const f = FAM[ev.nom] || "shell";
  switch (ev.evt){
    case "boucle_debut": eclat("thomas"); await particule("e-thomas-boucle","accent"); eclat("boucle"); break;
    case "session_debut": eclat("boucle"); await particule("e-boucle-exec","accent"); eclat("exec"); break;
    case "session_init": await particule("e-exec-pass","accent",false,500); await particule("e-pass-modele","accent",false,600); eclat("modele"); break;
    case "outil": await reponseModele();
      await particule("e-exec-"+f, COULEUR[f]); eclat(f);
      if (f === "lecture" || f === "recherche") await particule(`e-${f}-depot`, COULEUR[f], true, 650);
      else { await particule(`e-${f}-depot`, COULEUR[f], false, 650); eclat("depot"); }
      break;
    case "outil_erreur": eclat("exec","eclat-ko"); break;
    case "texte": await reponseModele(); break;
    case "session_fin": await particule("e-exec-boucle","accent"); eclat("boucle"); break;
    case "garde": eclat("boucle","eclat-alerte"); await particule("e-verif-depot","alerte",false,700); eclat("depot","eclat-alerte"); break;
    case "hors_liste": eclat("depot","eclat-alerte"); break;
    case "install": await particule("e-boucle-verif","accent"); eclat("verif"); break;
    case "verif": eclat("contrats"); await Promise.all([particule("e-boucle-verif","accent"), particule("e-contrats-verif","zc",false,1500)]);
      await particule("e-verif-depot", ev.ok ? "bon" : "critique", false, 700);
      eclat("verif", ev.ok ? "eclat-ok" : "eclat-ko"); eclat("contrats", ev.ok ? "eclat-ok" : "eclat-ko"); break;
    case "commit": await particule("e-verif-git","bon"); eclat("git","eclat-ok"); break;
    case "bloque": eclat("boucle","eclat-ko"); break;
    case "intervention": if (ev.auteur === "Thomas") { eclat("thomas"); await particule("e-thomas-boucle","accent"); }
      else { eclat("relecteur"); await particule("e-relecteur-boucle","accent",false,1100); } eclat("boucle"); break;
    case "boucle_fin": eclat("boucle","eclat-ok"); await particule("e-depot-revue","zc",false,1200); eclat("revue"); break;
    case "boucle_arret": case "boucle_erreur": eclat("boucle","eclat-alerte"); break;
    case "revue_debut": eclat("git"); break;
    case "agent_debut": if (ev.agent === "verificateur") { for (const a of AGENTS) particule(`e-a-${a}-verificateur`,"zc",false,1100); eclat("a-verificateur"); }
      else { await particule(`e-git-a-${ev.agent}`,"zc",false,1100); eclat("a-" + ev.agent); } break;
    case "agent_fin": if (ev.agent === "verificateur") { eclat("a-verificateur", ev.verdict === "ok" ? "eclat-ok" : ev.verdict === "ko" ? "eclat-ko" : "eclat-alerte");
        await particule("e-a-verificateur-rapport", COUL_V[ev.verdict] || "zc"); eclat("a-rapport"); }
      else eclat("a-" + ev.agent, ev.verdict === "ok" ? "eclat-ok" : ev.verdict === "ko" ? "eclat-ko" : "eclat-alerte"); break;
    case "revue_fin": await particule("e-a-rapport-revue","zc",false,900); eclat("revue"); break;
  }
}
async function jouer(){ if (enLecture) return; enLecture = true;
  while (file.length){ const ev = file.shift(); ajouterFlux(ev, true); if (onglet === "direct" && !document.hidden) await scene(ev); }
  enLecture = false; }

function graphe(){
  const c = etat.chaine, ec = etat.en_cours, lm = c.lmstudio;
  const genere = lm && lm.statut === "generating";
  for (const id of ["e-exec-pass","e-pass-modele"]) document.getElementById(id).classList.toggle("vif", genere);
  document.getElementById("n-modele").classList.toggle("actif", genere);
  document.getElementById("n-exec").classList.toggle("actif", !!ec);
  document.getElementById("n-boucle").classList.toggle("actif", c.boucle);
  if (lm && lm.nom) $("#t-modele").textContent = lm.nom;
  let bm = !c.modele_8080 ? "éteint" : !lm ? "état inconnu" : genere ? "génère" : lm.statut === "idle" ? "au repos" : (lm.statut || "");
  if (ec && ec.phase === "reflechit" && genere) bm = `réfléchit · ${nf.format(ec.tokens_tour)} tokens`;
  $("#b-modele").textContent = bm;
  $("#jauge").setAttribute("width", ec && ec.phase === "reflechit" ? Math.min(162, 162 * ec.tokens_tour / 4000) : 0);
  $("#b-boucle").textContent = c.boucle ? (ec ? `${ec.ticket} · tentative ${ec.tentative}/3` : "en marche") : "à l'arrêt";
  $("#b-exec").textContent = ec ? `tour ${ec.tour} · ${phaseTexte(ec.phase)}` : "en attente";
  const n = {lecture:0, recherche:0, ecriture:0, shell:0};
  if (ec) for (const [k,v] of Object.entries(ec.outils)) n[FAM[k] || "shell"] += v;
  for (const k in n) $("#b-"+k).textContent = n[k] ? "×" + n[k] : "";
  $("#b-depot").textContent = ec && ec.fichiers_ecrits.length ? `${ec.fichiers_ecrits.length} fichier(s) écrit(s)` : "";
  const v = etat.mesures.derniere_verif;
  $("#b-verif").textContent = v ? (v.ok ? `✓ ${v.ticket} réussi` : `✕ ${v.ticket} : ${v.motif}`).slice(0,28) : "";
  const g = etat.mesures.dernier_commit; $("#b-git").textContent = g ? g.commit : "";
  const m = etat.mesures, rg = m.dernier_reglage;
  $("#b-relecteur").textContent = rg ? `dernier réglage ${heure(rg.t).slice(0,5)}` : "surveille la boucle";
  const ct = v && v.contrats;
  $("#b-contrats").textContent = ct && (ct.reussis || ct.echoues) ? `${v.ticket} : ${ct.reussis} ✓ ${ct.echoues ? ct.echoues + " ✕" : ""}` : `${m.nb_contrats} tests, en attente`;
  const finie = !c.boucle && m.tickets > 0 && m.faits + m.bloques === m.tickets;
  $("#b-revue").textContent = finie ? "à faire maintenant" : "après la vague";
  document.getElementById("n-revue").classList.toggle("actif", finie);
  const rv = etat.revues || {agents:{}, tickets:[]};
  for (const a of [...AGENTS, "verificateur"]){ const x = rv.agents[a] || {}, b = $("#b-a-" + a);
    document.getElementById("n-a-" + a).classList.toggle("actif", x.etat === "relit");
    b.textContent = x.etat === "relit" ? `relit ${x.ticket}…` : x.etat === "fini" ? `${x.ticket} ${VERDICT[x.verdict] || x.verdict}` : (rv.veille ? "en veille" : "à l'arrêt"); }
  const der = rv.tickets[rv.tickets.length - 1];
  $("#b-a-rapport").textContent = der ? `${der.ticket} ${VERDICT[der.verdict] || ""} · ${der.confirmes} constat(s)` : "aucun pour l'instant";
}
function phaseTexte(p){ return {reflechit:"réfléchit", outil:"utilise un outil", attend:"attend le modèle", ecrit:"écrit", terminee:"terminée", verification:"vérification"}[p] || "démarre"; }

function pensee(){
  const ec = etat.en_cours, el = $("#pensee");
  if (!ec){ el.innerHTML = `<div class="ligne">Aucune session en cours. ${etat.chaine.boucle ? "La boucle prépare la suite." : "La boucle est à l'arrêt."}</div>`; return; }
  const ph = ec.phase === "reflechit" ? `Qwen <b>réfléchit</b> depuis ${duree(ec.depuis_dernier_s)}, ${nf.format(ec.tokens_tour)} tokens de raisonnement pour ce tour`
    : ec.phase === "verification" ? "La boucle <b>vérifie</b> le travail : make install, make check, tests de contrat"
    : `Qwen <b>${phaseTexte(ec.phase)}</b>, dernier échange il y a ${duree(ec.depuis_dernier_s)}`;
  el.innerHTML = `<div class="ligne">En ce moment : ${ph}.</div>` + (ec.derniere_pensee ?
    `<div class="ligne">Dernière pensée de Qwen, tour ${ec.derniere_pensee_tour}, telle qu'il l'a écrite :</div><blockquote>${esc(ec.derniere_pensee)}</blockquote>` : "");
}

/* ---------- indicateurs ---------- */
function kpis(){
  const m = etat.mesures, ec = etat.en_cours;
  const tuiles = [
    [ec ? ec.ticket : "aucun", "ticket en cours"],
    [ec ? duree(depuis(ec.debut)) : "", ec ? `session, tentative ${ec.tentative}/3` : "session"],
    [ec ? ec.tour : "", "tours de Qwen"],
    [ec ? nf.format(Object.values(ec.outils).reduce((a,b)=>a+b,0)) : "", "appels d'outils"],
    [ec ? nf.format(ec.tokens_session) : "", "tokens de raisonnement"],
    [`${m.faits}<small> / ${m.tickets}</small>`, "tickets faits (seuil : tous)", "seuil"],
    [`${m.interventions_thomas}<small> / 2 max</small>`, "interventions de Thomas", "seuil"],
    [m.debut ? duree(depuis(m.debut)) : "", "depuis le début de la vague"],
  ];
  $("#kpis").innerHTML = tuiles.map(([v,l,c]) => `<div class="kpi ${c||""}"><b>${v === "" ? "–" : v}</b><span>${esc(l)}</span></div>`).join("");
}
function pastilles(){
  const c = etat.chaine, lm = c.lmstudio, gen = lm && lm.statut === "generating";
  const p = (cls, t) => `<span class="pastille ${cls}"><i></i>${esc(t)}</span>`;
  $("#pastilles").innerHTML = p(c.modele_8080 ? (gen ? "vif" : "ok") : "ko", "modèle " + (c.modele_8080 ? (gen ? "génère" : "au repos") : "éteint"))
    + p(c.passerelle_4000 ? "ok" : "ko", "passerelle " + (c.passerelle_4000 ? "prête" : "éteinte"))
    + p(c.boucle ? "vif" : "", "boucle " + (c.boucle ? "en marche" : "à l'arrêt"))
    + p("", "branche " + etat.branche);
  $("#titre").textContent = etat.mesures.vague != null ? `Vague ${etat.mesures.vague}` : "Chaîne";
}

/* ---------- fil ---------- */
function classeEv(ev){ if (ev.evt === "outil") return FAM[ev.nom] || "shell";
  if (ev.evt === "verif") return ev.ok ? "ok" : "ko"; if (ev.evt === "commit") return "ok";
  if (["outil_erreur","bloque","boucle_erreur"].includes(ev.evt)) return "ko";
  if (["garde","hors_liste","alerte","boucle_arret","intervention"].includes(ev.evt)) return "alerte";
  if (ev.evt === "agent_fin" || ev.evt === "revue_fin") return {ok:"ok", reserves:"alerte", ko:"ko"}[ev.verdict] || "ko";
  if (ev.evt === "revue_erreur") return "ko";
  if (["veille_debut","revue_debut","agent_debut"].includes(ev.evt)) return "claude";
  return "boucle"; }
function detailEv(ev){
  switch (ev.evt){
    case "outil": return `${ev.nom} · ${ev.resume || ""}`;
    case "verif": return ev.ok ? "make check et contrats passent" : ev.motif;
    case "commit": return `${ev.ticket} validé, commit ${ev.commit}`;
    case "session_debut": case "session_init": return `${ev.ticket}, tentative ${ev.tentative}`;
    case "session_fin": return `${ev.ticket} · ${duree(ev.duree_s)} · ${ev.appels_outils} outils`;
    case "intervention": return `${ev.auteur} : ${ev.texte}`;
    case "garde": return (ev.restaures || []).join(", ");
    case "hors_liste": return (ev.fichiers || []).join(", ");
    case "bloque": return `${ev.ticket} après ${ev.tentatives} tentatives`;
    case "boucle_debut": return `vague ${ev.vague}, modèle ${ev.modele}`;
    case "revue_debut": return `${ev.ticket}, commit ${ev.commit}, six agents`;
    case "agent_debut": return `${NOMS_A[ev.agent]} relit ${ev.ticket}`;
    case "agent_fin": return `${NOMS_A[ev.agent]} : ${VERDICT[ev.verdict] || ev.verdict}${ev.agent === "verificateur" ? `, ${ev.confirmes ?? 0} confirmé(s), ${ev.rejetes ?? 0} rejeté(s)` : `, ${ev.constats} constat(s)`}`;
    case "revue_fin": return `${ev.ticket} : ${VERDICT[ev.verdict] || ev.verdict}, ${ev.confirmes} constat(s) confirmé(s), ${ev.cout} $`;
    case "revue_erreur": return `${ev.ticket} : ${ev.erreur}`;
    case "veille_debut": return `modèle ${ev.modele}, vérificateur ${ev.verificateur}`;
    default: return ev.resume || ev.detail || ev.motif || "";
  }
}
const court = t => etat && etat.racine ? String(t).split(etat.racine + "/").join("") : String(t);
function ajouterFlux(ev, nouveau){
  const li = document.createElement("li");
  li.innerHTML = `<span class="h">${heure(ev.t).slice(0,5)}</span><span class="p ${classeEv(ev)}"></span><span class="l"><b>${esc(LIBELLES[ev.evt] || ev.evt)}</b> <span>${esc(court(detailEv(ev)).slice(0,220))}</span></span>`;
  if (!nouveau) li.style.animation = "none";
  const ol = $("#flux"); ol.prepend(li); while (ol.children.length > 200) ol.lastChild.remove();
}

/* ---------- runs ---------- */
function runs(){
  const tb = $("#runs tbody");
  if (!etat.runs.length){ tb.innerHTML = `<tr><td colspan="9" class="vide">Aucune session pour l'instant.</td></tr>`; return; }
  tb.innerHTML = etat.runs.slice().reverse().map(r => `<tr data-run="${esc(r.id)}" class="${r.id === runSel ? "sel" : ""}" tabindex="0">
    <td><b class="mono">${esc(r.ticket)}</b> · t${r.tentative}</td><td><span class="statut ${r.statut}">${STATUTS[r.statut] || r.statut}</span></td>
    <td>${heure(r.debut)}</td><td class="n">${duree(r.duree_s)}</td><td class="n">${r.nb_tours}</td><td class="n">${r.nb_outils}</td>
    <td class="n">${r.nb_erreurs}</td><td class="n">${nf.format(r.tokens_raisonnement)}</td><td>${esc(r.commit ? "commit " + r.commit : r.motif || "")}</td></tr>`).join("");
  tb.querySelectorAll("tr[data-run]").forEach(tr => { const ouvrir = () => { runSel = tr.dataset.run; runs(); detail(); };
    tr.addEventListener("click", ouvrir); tr.addEventListener("keydown", e => { if (e.key === "Enter") ouvrir(); }); });
  if (!runSel && etat.runs.length){ runSel = etat.runs[etat.runs.length-1].id; detail(); }
}
let detailCharge = 0;
async function detail(){
  if (!runSel) return; const d = await api("/api/run?id=" + encodeURIComponent(runSel)).catch(() => null);
  if (!d){ $("#detail").innerHTML = ""; return; }
  detailCharge = Date.now();
  const r = d.run, p = d.params;
  const fam = {lecture:0, recherche:0, ecriture:0, shell:0};
  for (const t of d.tours) for (const o of t.outils) fam[FAM[o.nom] || "shell"]++;
  $("#detail").innerHTML = `
  <div class="grille2">
    <div class="carte"><div class="carte-t">${esc(r.ticket)} · tentative ${r.tentative} <span class="statut ${r.statut}">${STATUTS[r.statut] || r.statut}</span></div>
      <dl class="params">
        <dt>Ticket</dt><dd>${esc(p.ticket)}</dd><dt>Modèle</dt><dd>${esc(p.modele || "")}</dd>
        <dt>Outils permis</dt><dd class="mono">${esc((p.outils || []).join(", "))}</dd><dt>Permissions</dt><dd>${esc(p.permissions || "")}</dd>
        <dt>Début</dt><dd>${heure(r.debut)}</dd><dt>Durée</dt><dd>${duree(r.duree_s)}</dd>
        <dt>Tours</dt><dd>${d.tours.length}</dd><dt>Tokens de raisonnement</dt><dd>${nf.format(r.tokens_raisonnement)}</dd>
        <dt>Verdict</dt><dd>${esc(r.commit ? "commit " + r.commit : r.motif || (r.statut === "en_cours" ? "session en cours" : ""))}</dd>
        <dt>Enregistrement</dt><dd class="mono">${esc(p.fichier)}</dd>
      </dl></div>
    <div class="carte"><div class="carte-t">Artefacts <span class="aide">fichiers écrits par Qwen et sorties de la vérification</span></div>
      ${d.artefacts.ecrits.length ? d.artefacts.ecrits.map(f => `<div class="mono">${esc(f)}</div>`).join("") : `<div class="vide">Aucun fichier écrit pour l'instant.</div>`}
      ${d.artefacts.verifs.length ? `<div class="aide" style="margin-top:10px">Vérification, dans .chaine/verifs/</div>` + d.artefacts.verifs.map(f => `<div class="mono">${esc(f)}</div>`).join("") : ""}
      <div class="legende">${Object.entries(fam).map(([k,v]) => `<span class="chip ${k}"><i></i>${FAM_NOM[k]} ${v}</span>`).join("")}</div>
    </div>
  </div>
  <div class="grille2">
    <div class="carte graphique"><div class="carte-t">Tokens de raisonnement par tour <span class="aide">survolez une barre</span></div><div id="g-tokens"></div></div>
    <div class="carte graphique"><div class="carte-t">Déroulé <span class="aide">gris : Qwen réfléchit, points : appels d'outils</span></div><div id="g-deroule"></div>
      <div class="legende">${Object.keys(FAM_NOM).map(k => `<span><i style="background:var(--${COULEUR[k]})"></i>${FAM_NOM[k]}</span>`).join("")}<span><i style="background:var(--critique)"></i>erreur</span></div></div>
  </div>
  <div class="carte tours"><div class="carte-t">Tours de Qwen <span class="aide">pensée, outils, résultats</span></div>${toursHtml(d)}</div>`;
  barres(d); deroule(d);
}
function toursHtml(d){
  if (!d.tours.length) return `<div class="vide">Qwen n'a pas encore terminé de tour.</div>`;
  return d.tours.slice().reverse().map(t => `<details ${t.n === d.tours.length ? "open" : ""}><summary><span class="num">Tour ${t.n}</span>
    <span class="meta">${t.duree_s != null ? duree(t.duree_s) + " · " : ""}${nf.format(t.tokens)} tokens</span>
    ${t.outils.map(o => `<span class="chip ${FAM[o.nom] || "shell"} ${o.erreur ? "err" : ""}"><i></i>${esc(o.nom)}</span>`).join("")}</summary>
    <div class="corps">${t.pensee ? `<div class="pens-t">${esc(t.pensee)}</div>` : ""}${t.texte ? `<div>${esc(t.texte)}</div>` : ""}
    ${t.outils.map(o => `<div><div class="outil-l"><span class="chip ${FAM[o.nom] || "shell"} ${o.erreur ? "err" : ""}"><i></i>${esc(o.nom)}</span><code>${esc(o.entree)}</code></div>
      ${o.resultat != null ? `<pre>${esc(o.resultat)}</pre>` : `<div class="vide">en attente du résultat</div>`}</div>`).join("")}</div></details>`).join("");
}
const bulle = $("#bulle");
function montrer(e, texte){ bulle.textContent = texte; bulle.hidden = false;
  const x = Math.min(e.clientX + 14, innerWidth - 350); bulle.style.left = x + "px"; bulle.style.top = (e.clientY + 14) + "px"; }
function cacher(){ bulle.hidden = true; }
function barres(d){
  const el = $("#g-tokens"), T = d.tours.slice();
  if (d.phase === "reflechit" && d.tokens_en_cours > 0 && d.run.statut === "en_cours") T.push({n: T.length + 1, tokens: d.tokens_en_cours, outils: [], enCours: true});
  if (!T.length){ el.innerHTML = `<div class="vide">Pas encore de tour.</div>`; return; }
  const W = 560, H = 220, g = 54, b = 22, ih = H - b - 10, iw = W - g - 8;
  const max = Math.max(100, ...T.map(t => t.tokens)), pas = Math.pow(10, Math.floor(Math.log10(max))), haut = Math.ceil(max/pas)*pas;
  const bw = iw / T.length, larg = Math.max(2, Math.min(28, bw - 2));
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Tokens de raisonnement de chacun des ${T.length} tours de la session">`;
  for (let k = 0; k <= 4; k++){ const v = haut*k/4, y = 10 + ih - ih*k/4;
    s += `<line class="grille" x1="${g}" x2="${W-8}" y1="${y}" y2="${y}"/><text x="${g-6}" y="${y+4}" text-anchor="end">${nf.format(v)}</text>`; }
  T.forEach((t, i) => { const h = ih * t.tokens / haut, x = g + i*bw + (bw - larg)/2, y = 10 + ih - h;
    const r = Math.min(4, larg/2, h);
    s += `<path class="barre ${t.enCours ? "encours" : ""}" data-i="${i}" d="M${x},${10+ih} V${y+r} Q${x},${y} ${x+r},${y} H${x+larg-r} Q${x+larg},${y} ${x+larg},${y+r} V${10+ih} Z"/>`;
    s += `<rect data-i="${i}" x="${g + i*bw}" y="10" width="${bw}" height="${ih}" fill="transparent"/>`;
    if (T.length <= 30 || i % Math.ceil(T.length/30) === 0) s += `<text x="${x + larg/2}" y="${H-6}" text-anchor="middle">${t.n}</text>`; });
  s += `<line class="axe" x1="${g}" x2="${W-8}" y1="${10+ih}" y2="${10+ih}"/></svg>`;
  el.innerHTML = s;
  el.querySelectorAll("[data-i]").forEach(n => { const t = T[+n.dataset.i];
    n.addEventListener("mousemove", e => { el.querySelectorAll(".barre").forEach(x => x.classList.toggle("sur", x.dataset.i === n.dataset.i));
      montrer(e, `Tour ${t.n}${t.enCours ? " (en cours)" : ""}\n${nf.format(t.tokens)} tokens de raisonnement${t.duree_s != null ? "\n" + duree(t.duree_s) : ""}\n${t.outils.map(o => o.nom).join(", ") || "aucun outil"}`); });
    n.addEventListener("mouseleave", () => { cacher(); el.querySelectorAll(".barre").forEach(x => x.classList.remove("sur")); }); });
}
function deroule(d){
  const el = $("#g-deroule"), T = d.tours.filter(t => t.t); if (!T.length){ el.innerHTML = `<div class="vide">Pas encore d'appel daté.</div>`; return; }
  const t0 = new Date(d.run.debut).getTime() / 1000;
  const fin = Math.max(...T.map(t => new Date(t.t).getTime()/1000), d.run.statut === "en_cours" ? Date.now()/1000 : 0);
  const W = 560, H = 150, g = 74, iw = W - g - 12, X = s => g + iw * (s - t0) / Math.max(60, fin - t0);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Chronologie de la session : périodes de réflexion de Qwen et appels d'outils">`;
  s += `<rect class="lane" x="${g}" y="18" width="${iw}" height="34" rx="6"/><rect class="lane" x="${g}" y="66" width="${iw}" height="34" rx="6"/>`;
  s += `<text x="${g-8}" y="39" text-anchor="end">Réflexion</text><text x="${g-8}" y="87" text-anchor="end">Outils</text>`;
  const mn = Math.max(60, fin - t0) / 60, pas = mn > 90 ? 30 : mn > 30 ? 10 : mn > 10 ? 5 : 1;
  for (let m = 0; m <= mn; m += pas){ const x = X(t0 + m*60); s += `<line class="grille" x1="${x}" x2="${x}" y1="14" y2="104"/><text x="${x}" y="122" text-anchor="middle">${m} min</text>`; }
  T.forEach((t, i) => { if (t.debut_s != null){ const a = X(t.debut_s), b = X(new Date(t.t).getTime()/1000);
    s += `<rect class="pens" data-k="p${i}" x="${a}" y="22" width="${Math.max(2, b - a - 2)}" height="26" rx="4"/>`; } });
  if (d.run.statut === "en_cours" && d.phase === "reflechit"){ const a = X(new Date(T[T.length-1].t).getTime()/1000), b = X(Date.now()/1000);
    s += `<rect class="pens encours" data-k="c0" x="${a}" y="22" width="${Math.max(3, b - a)}" height="26" rx="4"/>`; }
  const mks = []; T.forEach(t => t.outils.forEach(o => { if (o.t) mks.push({o, t}); }));
  mks.forEach((m, i) => { const x = X(new Date(m.o.t).getTime()/1000);
    s += `<circle class="mk ${FAM[m.o.nom] || "shell"} ${m.o.erreur ? "err" : ""}" data-k="o${i}" cx="${x}" cy="83" r="6"/>`; });
  s += `</svg>`; el.innerHTML = s;
  el.querySelectorAll("[data-k]").forEach(n => { const k = n.dataset.k, i = +k.slice(1);
    n.addEventListener("mousemove", e => { if (k[0] === "c"){ montrer(e, `Tour en cours\nQwen réfléchit depuis ${duree(Date.now()/1000 - new Date(T[T.length-1].t).getTime()/1000)}\n${nf.format(d.tokens_en_cours)} tokens pour l'instant`); return; }
      if (k[0] === "p"){ const t = T[i]; montrer(e, `Tour ${t.n}\nQwen réfléchit ${duree(t.duree_s)}\n${nf.format(t.tokens)} tokens`); }
      else { const m = mks[i]; montrer(e, `${m.o.nom}${m.o.erreur ? " (erreur)" : ""}\n${m.o.entree}\n${heure(m.o.t)}`); } });
    n.addEventListener("mouseleave", cacher); });
}

/* ---------- tickets ---------- */
function tickets(){
  const lots = [...new Set(etat.tickets.map(t => t.id.split("-")[0]))];
  $("#filtres").innerHTML = [`<button aria-pressed="${lotFiltre === null}" data-lot="">Tous</button>`].concat(lots.map(l =>
    `<button aria-pressed="${lotFiltre === l}" data-lot="${l}">Lot ${l.slice(1)}</button>`)).join("");
  $("#filtres").querySelectorAll("button").forEach(b => b.addEventListener("click", () => { lotFiltre = b.dataset.lot || null; tickets(); }));
  const courant = etat.en_cours && etat.en_cours.ticket;
  const col = {"à faire":[], "en cours":[], "fait":[], "bloqué":[]};
  etat.tickets.filter(t => !lotFiltre || t.id.startsWith(lotFiltre + "-")).forEach(t => {
    (t.id === courant ? col["en cours"] : (col[t.etat] || col["à faire"])).push(t); });
  $("#kanban").innerHTML = Object.entries(col).map(([k, l]) => `<div class="colonne"><h3><span>${k[0].toUpperCase() + k.slice(1)}</span><span>${l.length}</span></h3>
    ${l.map(t => `<div class="ticket ${t.id === courant ? "courant" : ""}"><div class="id">${esc(t.id)}</div><div class="ti">${esc(t.titre)}</div>
    <div class="me">dépend de : ${esc(t.depend)}${+t.tentatives ? ` · ${t.tentatives} tentative(s)` : ""}${t.commit ? ` · ${esc(t.commit)}` : ""}</div></div>`).join("") || `<div class="vide">vide</div>`}</div>`).join("");
  const m = etat.mesures;
  $("#kpis-t").innerHTML = [[`${m.faits} / ${m.tickets}`, `tickets de la vague ${m.vague ?? ""} faits`], [m.bloques, "bloqués"],
    [m.sessions, "sessions lancées"], [`${m.interventions_thomas} / 2 max`, "interventions de Thomas"], [m.interventions_relecteur, "réglages du relecteur"]]
    .map(([v,l]) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");
}

/* ---------- boucle de rafraîchissement ---------- */
let revuesCharge = 0, revuesSig = "";
async function revues(force){
  const rv = etat.revues, sig = JSON.stringify(rv.tickets);
  $("#kpis-r").innerHTML = [[rv.tickets.length, "tickets relus"], [rv.tickets.reduce((a,t) => a + t.confirmes, 0), "constats confirmés"],
    [rv.tickets.reduce((a,t) => a + t.bloquants, 0), "bloquants confirmés"], [`${nf.format(rv.cout_usd)} $`, "coût des relectures"],
    [rv.veille ? "en veille" : "à l'arrêt", "relecteurs automatiques"]].map(([v,l]) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");
  if (!force && sig === revuesSig && Date.now() - revuesCharge < 30000) return;
  revuesSig = sig; revuesCharge = Date.now();
  const R = await api("/api/revues").catch(() => []);
  if (!R.length){ $("#revues").innerHTML = `<div class="carte vide">Aucune relecture pour l'instant. Les six agents relisent chaque ticket dès que la boucle le commite.</div>`; return; }
  const ordreG = {bloquant:0, majeur:1, mineur:2, remarque:3}, ordreS = {confirme:0, incertain:1, rejete:2};
  $("#revues").innerHTML = R.slice().reverse().map(r => `<div class="carte revue">
    <div class="carte-t">${esc(r.ticket)} · commit <code>${esc(r.commit)}</code> <span class="statut ${r.verdict === "ok" ? "reussi" : r.verdict === "ko" ? "echec" : "interrompu"}">${esc(VERDICT[r.verdict] || r.verdict)}</span>
      <span class="aide">relu ${heure(r.date)} · ${duree(r.duree_s)} · ${nf.format(r.cout_usd)} $ · ${esc(r.modeles.angles)}, vérificateur ${esc(r.modeles.verificateur)}</span></div>
    <p class="synthese">${esc(r.synthese)}</p>
    <div class="angles">${r.angles.map(a => `<div class="angle"><b>${esc(NOMS_A[a.agent])}</b> <span class="statut ${a.verdict === "ok" ? "reussi" : a.verdict === "ko" || a.verdict === "erreur" ? "echec" : "interrompu"}">${esc(VERDICT[a.verdict] || a.verdict)}</span><div>${esc(a.resume || a.erreur || "")}</div></div>`).join("")}</div>
    <div class="defile"><table class="constats"><thead><tr><th>Statut</th><th>Gravité</th><th>Agent</th><th>Où</th><th>Constat</th></tr></thead><tbody>
    ${r.constats.slice().sort((a,b) => (ordreS[a.statut] ?? 1) - (ordreS[b.statut] ?? 1) || ordreG[a.gravite] - ordreG[b.gravite]).map(c => `<tr class="st-${esc(c.statut)}">
      <td><span class="chip st ${esc(c.statut)}">${esc({confirme:"confirmé", rejete:"rejeté", incertain:"incertain"}[c.statut] || "")}</span></td>
      <td><span class="grav ${esc(c.gravite)}">${esc(c.gravite)}</span></td><td>${esc(NOMS_A[c.agent])}</td>
      <td class="mono">${esc(c.fichier)}${c.ligne ? ":" + c.ligne : ""}</td>
      <td class="txt"><details><summary>${esc(c.titre)}</summary><div class="aide">${esc(c.detail)}</div>
        ${c.attendu ? `<div><b>Attendu</b> : ${esc(c.attendu)}</div>` : ""}<div><b>Règle</b> : ${esc(c.regle)}</div>
        <div><b>Vérificateur</b> : ${esc(c.raison || "")}</div></details></td></tr>`).join("") || `<tr><td colspan="5" class="vide">Aucun constat.</td></tr>`}
    </tbody></table></div>
    ${r.angles.some(a => (a.points_forts || []).length) ? `<div class="aide forts">Points forts : ${esc(r.angles.flatMap(a => a.points_forts || []).slice(0,6).join(" · "))}</div>` : ""}
  </div>`).join("");
}
function rendre(){ if (!etat) return; pastilles(); kpis(); graphe(); pensee();
  if (onglet === "runs") runs(); if (onglet === "tickets") tickets(); if (onglet === "revues") revues(); }
async function rafraichir(){
  try {
    const e = await api("/api/etat"); const premier = etat === null; etat = e;
    if (premier){ seq = e.seq; e.activite.forEach(ev => ajouterFlux(ev, false)); }
    else if (e.seq !== seq){ const n = await api("/api/evenements?depuis=" + seq); seq = n.seq;
      if (n.remis_a_zero){ $("#flux").innerHTML = ""; n.evenements.forEach(ev => ajouterFlux(ev, false)); }
      else { file.push(...n.evenements); jouer(); } }
    $("#flux-aide").textContent = `mis à jour ${heure(e.genere)}`;
    rendre();
    if (onglet === "runs" && runSel && Date.now() - detailCharge > 6000 && !document.querySelector("#detail details[open]:hover")) detail();
  } catch (err) { $("#flux-aide").textContent = "page de suivi injoignable, nouvel essai…"; }
}
rafraichir(); setInterval(rafraichir, 2000);
</script>
</body>
</html>
"""


class Gestionnaire(BaseHTTPRequestHandler):
    def envoyer(self, code: int, corps: bytes, type_: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", type_)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def json(self, valeur: object, code: int = 200) -> None:
        corps = json.dumps(valeur, ensure_ascii=False, default=str).encode("utf-8")
        self.envoyer(code, corps, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        hote = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if hote not in HOTES:
            self.envoyer(400, b"hote refuse", "text/plain; charset=utf-8")
            return
        url = urlsplit(self.path)
        params = parse_qs(url.query)
        if url.path == "/":
            self.envoyer(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path in ("/api/etat", "/etat.json"):
            self.json(construire_etat())
        elif url.path == "/api/evenements":
            self.json(evenements_depuis(int((params.get("depuis") or ["0"])[0] or 0)))
        elif url.path == "/api/revues":
            self.json(lire_rapports())
        elif url.path == "/api/run":
            detail = detail_run((params.get("id") or [""])[0])
            if detail is None:
                self.json({"erreur": "run inconnu"}, 404)
            else:
                self.json(detail)
        else:
            self.envoyer(404, b"introuvable", "text/plain; charset=utf-8")

    def log_message(self, *_args: object) -> None:
        return


def main() -> int:
    global DEPOT
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("--port", type=int, default=8770)
    analyseur.add_argument("--racine", type=Path, help="racine du dépôt, par défaut celle du script")
    args = analyseur.parse_args()
    if args.racine:
        DEPOT = Depot(args.racine.resolve())
    serveur = ThreadingHTTPServer((ADRESSE, args.port), Gestionnaire)
    print(f"Suivi de la chaîne sur http://{ADRESSE}:{args.port}/ (dépôt {DEPOT.racine})")
    with contextlib.suppress(KeyboardInterrupt):
        serveur.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
