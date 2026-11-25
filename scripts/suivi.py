#!/usr/bin/env python3
"""Tableau de bord de la chaîne de développement de Cabinet.

Page locale sur http://127.0.0.1:8770/. Elle montre en direct qui parle à qui
(boucle, exécutant, passerelle, modèle, outils, vérification, git, relecteurs),
les sessions de l'exécutant façon « runs », la fiche de chaque ticket de la spec
au constat, la couverture des critères par les tests de contrat, les mesures de
la vague, la carte du code et le journal de la garde.

Elle lit :
- `.chaine/journal.jsonl`, tenu par `scripts/boucle_executant.py`
- `.chaine/sessions/<ticket>-t<n>.jsonl`, sortie complète de chaque session
- `.chaine/revues/`, rapports de `scripts/relecteurs.py`
- `.chaine/verifs/`, sorties de `make check` et rapports JUnit
- `tickets/`, `tests/contrats/` et l'historique git
- l'état du modèle dans LM Studio (`lms ps --json`) et de la batterie (`pmset`)

Elle n'écrit que dans `.chaine/`, hors du dépôt suivi par git :
- `.chaine/suivi/tri.json`, décisions de Thomas sur les constats
- `.chaine/suivi/actions.jsonl`, journal des actions faites depuis la page
- `.chaine/suivi/reglages.json`, notifications macOS
- `.chaine/pause`, demande de pause lue par la boucle

Le pilotage (arrêter, mettre en pause, reprendre la boucle, relancer une
relecture) passe par des requêtes POST limitées à l'hôte local, à l'origine de
la page et à un jeton tiré au démarrage. Il ne lance jamais une nouvelle vague.

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
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
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
        self.verifs = self.chaine / "verifs"
        self.pause = self.chaine / "pause"
        self.suivi = self.chaine / "suivi"
        self.tri = self.suivi / "tri.json"
        self.actions = self.suivi / "actions.jsonl"
        self.reglages = self.suivi / "reglages.json"


DEPOT = Depot(Path(__file__).resolve().parent.parent)


# --------------------------------------------------------------------------
# Lectures


def maintenant() -> dt.datetime:
    return dt.datetime.now().astimezone()


def secondes(horodatage: str | None) -> float | None:
    if not horodatage:
        return None
    try:
        return dt.datetime.fromisoformat(horodatage).timestamp()
    except ValueError:
        return None


def iso(instant: float | None) -> str | None:
    if instant is None:
        return None
    return dt.datetime.fromtimestamp(instant).astimezone().isoformat(timespec="seconds")


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
    """Journaux de la boucle, des relecteurs et de la page, fusionnés dans l'ordre du temps."""
    boucle = lire_jsonl(DEPOT.journal)
    autres = lire_jsonl(DEPOT.revues / "journal.jsonl") + lire_jsonl(DEPOT.actions)
    if not autres:
        return boucle
    return sorted(boucle + autres, key=lambda e: secondes(e.get("t")) or 0)


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


def motif_refus(motif: str) -> str:
    if motif.startswith("no approval surface"):
        return "commande hors de la liste permise à l'exécutant, refusée d'office"
    return motif[:200]


def lire_session(fichier: Path) -> dict:
    """Découpe une session en tours. Mis en cache sur la taille et la date du fichier."""
    vide = {"params": {}, "tours": [], "resultat": None, "phase": None, "tokens_en_cours": 0,
            "refus": [], "compactions": [], "partiel": {"pensee": "", "texte": ""}}
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
    refus: list[dict] = []
    compactions: list[dict] = []
    dernier_ts: float | None = None
    retour_ts: float | None = None
    partiel: dict[str, list[str]] = {"pensee": [], "texte": []}
    with fichier.open(encoding="utf-8", errors="replace") as f:
        for ligne in f:
            try:
                msg = json.loads(ligne)
            except ValueError:
                continue
            genre, sous = msg.get("type"), msg.get("subtype")
            ts = secondes(msg.get("timestamp")) if isinstance(msg.get("timestamp"), str) else None
            if ts:
                dernier_ts = ts
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
            elif genre == "stream_event":
                # Flux mot à mot, présent quand la session tourne avec --include-partial-messages.
                evenement = msg.get("event") or {}
                if evenement.get("type") in ("message_start", "message_stop"):
                    partiel = {"pensee": [], "texte": []}
                elif evenement.get("type") == "content_block_delta":
                    delta = evenement.get("delta") or {}
                    if delta.get("type") == "thinking_delta":
                        partiel["pensee"].append(delta.get("thinking", ""))
                    elif delta.get("type") == "text_delta":
                        partiel["texte"].append(delta.get("text", ""))
            elif genre == "system" and sous == "permission_denied":
                refus.append({
                    "outil": msg.get("tool_name"),
                    "id": str(msg.get("tool_use_id")),
                    "motif": motif_refus(str(msg.get("decision_reason") or "")),
                    "ts": dernier_ts,
                })
            elif genre == "system" and sous == "compact_boundary":
                meta = msg.get("compact_metadata") or {}
                compactions.append({
                    "ts": dernier_ts,
                    "declencheur": meta.get("trigger"),
                    "avant": meta.get("pre_tokens"),
                    "apres": meta.get("post_tokens"),
                    "duree_ms": meta.get("duration_ms"),
                })
            elif genre == "assistant":
                message = msg.get("message") or {}
                ident = str(message.get("id") or "")
                tour = par_message.get(ident) if ident else None
                if tour is None:
                    if nouveau or not tours:
                        tours.append({
                            "n": len(tours) + 1, "pensee": "", "texte": "", "outils": [], "tokens": 0,
                            "ts_debut": retour_ts, "ts_fin": None, "pense_ms": 0,
                        })
                    tour = tours[-1]
                    if ident:
                        par_message[ident] = tour
                    nouveau = False
                tour["tokens"] = max(tour["tokens"], tokens)
                tour["pense_ms"] += int(msg.get("thinking_duration_ms") or 0)
                if ts:
                    tour["ts_fin"] = ts
                for bloc in message.get("content") or []:
                    if bloc.get("type") == "thinking":
                        tour["pensee"] += bloc.get("thinking", "")
                        partiel["pensee"] = []
                    elif bloc.get("type") == "text":
                        tour["texte"] += bloc.get("text", "")
                        partiel["texte"] = []
                    elif bloc.get("type") == "tool_use":
                        outil = {
                            "id": bloc.get("id"),
                            "nom": bloc.get("name"),
                            "entree": resume_entree(bloc.get("name", ""), bloc.get("input") or {}),
                            "resultat": None,
                            "erreur": False,
                            "ts": ts,
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
                            if ts and outil.get("ts"):
                                outil["duree_ms"] = round((ts - outil["ts"]) * 1000)
                if ts:
                    retour_ts = ts
                nouveau, phase = True, "attend"
            elif genre == "result":
                usage = msg.get("usage") or {}
                resultat = {
                    "tours": msg.get("num_turns"),
                    "duree_ms": msg.get("duration_ms"),
                    "erreur": msg.get("is_error"),
                    "texte": str(msg.get("result") or "")[:2000],
                    "tokens_entree": usage.get("input_tokens"),
                    "tokens_sortie": usage.get("output_tokens"),
                }
                phase = "terminee"
    for tour in tours:
        tour["pensee"] = tour["pensee"].strip()[:6000]
        tour["texte"] = tour["texte"].strip()[:3000]
    for r in refus:
        outil = par_id.get(r["id"])
        r["entree"] = outil["entree"] if outil else ""
    valeur = {
        "params": params,
        "tours": tours,
        "resultat": resultat,
        "phase": phase,
        "tokens_en_cours": tokens if phase == "reflechit" else 0,
        "refus": refus,
        "compactions": compactions,
        "partiel": {"pensee": "".join(partiel["pensee"]), "texte": "".join(partiel["texte"]).strip()},
    }
    _SESSIONS[str(fichier)] = {"cle": cle, "v": valeur}
    return valeur


def aligner(tours: list[dict], evenements: list[dict], debut: str) -> None:
    """Date chaque tour. Les horodatages de la session priment, le journal sert de repli."""
    if tours and all(t.get("ts_fin") for t in tours):
        precedent = secondes(debut)
        for tour in tours:
            tour["t"] = iso(tour["ts_fin"])
            tour["debut_s"] = tour.get("ts_debut") or precedent
            ecart = tour["ts_fin"] - tour["debut_s"] if tour["debut_s"] is not None else None
            tour["duree_s"] = round(ecart) if ecart is not None else None
            for outil in tour["outils"]:
                if outil.get("ts"):
                    outil["t"] = iso(outil["ts"])
            precedent = tour["ts_fin"]
        return
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
                courant["contrats"] = e.get("contrats")
            elif evt == "hors_liste":
                courant["hors_liste"] = e.get("fichiers") or []
            elif evt == "garde":
                courant["restaures"] = e.get("restaures") or []
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
        pense = sum(t.get("pense_ms") or 0 for t in session["tours"]) / 1000
        outils = sum(o.get("duree_ms") or 0 for t in session["tours"] for o in t["outils"]) / 1000
        compaction = sum(c.get("duree_ms") or 0 for c in session["compactions"]) / 1000
        run["temps"] = {
            "raisonne_s": round(pense),
            "outils_s": round(outils),
            "compaction_s": round(compaction),
            "reste_s": max(0, round(run["duree_s"] - pense - outils - compaction)),
        }
        run["debit"] = round(run["tokens_raisonnement"] / pense, 1) if pense >= 5 else None
        run["nb_refus"] = len(session["refus"])
        run["nb_compactions"] = len(session["compactions"])
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
            if isinstance(noeud, ast.FunctionDef) and noeud.name.startswith("test_"):
                total += nombre_de_cas(noeud)
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
            "nb_tours": len(tours),
            "tokens_tour": session["tokens_en_cours"],
            "tokens_session": run["tokens_raisonnement"],
            "outils": compte,
            "fichiers_ecrits": sorted(ecrits),
            "depuis_dernier_s": round(maintenant().timestamp() - secondes(derniers[-1])),
            "derniere_pensee": (derniere["pensee"] or derniere["texte"])[-900:] if derniere else "",
            "derniere_pensee_tour": derniere["n"] if derniere else None,
            "pensee_partielle": session["partiel"]["pensee"][-2400:],
            "pensee_total": len(session["partiel"]["pensee"]),
            "texte_partiel": session["partiel"]["texte"][-1200:],
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
        "delai_session": debuts[-1].get("delai_session") if debuts else None,
    }
    chaine = {
        "modele_8080": port_ouvert(8080),
        "passerelle_4000": port_ouvert(4000),
        "boucle": vivante,
        "lmstudio": etat_lmstudio(),
        "batterie": etat_batterie(),
        "pause": DEPOT.pause.exists(),
    }
    revues = etat_revues(evenements)
    alertes = calculer_alertes(evenements, runs, chaine, tickets, mesures, revues)
    for run in runs:
        run.pop("evenements", None)
    tete = DEPOT.racine / ".git" / "HEAD"
    return {
        "genere": maintenant().isoformat(timespec="seconds"),
        "racine": str(DEPOT.racine),
        "branche": tete.read_text().strip().rsplit("/", 1)[-1] if tete.exists() else "",
        "chaine": chaine,
        "tickets": tickets,
        "en_cours": en_cours,
        "mesures": mesures,
        "runs": runs,
        "revues": revues,
        "alertes": alertes,
        "reglages": {"notifications": bool(lire_json(DEPOT.reglages, {}).get("notifications"))},
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
# Git, tickets, tests de contrat


def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=DEPOT.racine, capture_output=True, text=True, timeout=15, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


RE_COMMIT = re.compile(r"[0-9a-f]{7,40}")
RE_BLOC = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)")
MAX_LIGNES_DIFF = 1200
_DIFFS: dict[str, list[dict]] = {}


def lire_diff(commit: str) -> list[dict]:
    """Diff d'un commit, fichier par fichier, avec les numéros de ligne des deux côtés."""
    if not RE_COMMIT.fullmatch(commit or ""):
        return []
    if commit in _DIFFS:
        return _DIFFS[commit]
    fichiers: list[dict] = []
    courant: dict | None = None
    bloc: dict | None = None
    a = b = vues = 0
    sortie = git("show", "--format=", "--unified=3", "--no-color", "--no-ext-diff", commit)
    for ligne in sortie.splitlines():
        if ligne.startswith("diff --git "):
            courant = {"chemin": ligne.rsplit(" b/", 1)[-1], "statut": "modifié", "ajouts": 0,
                       "retraits": 0, "blocs": [], "tronque": False, "binaire": False}
            fichiers.append(courant)
            bloc, vues = None, 0
        elif courant is None:
            continue
        elif ligne.startswith("@@"):
            trouve = RE_BLOC.match(ligne)
            if trouve:
                a, b = int(trouve[1]), int(trouve[2])
                bloc = {"entete": trouve[3].strip(), "lignes": []}
                courant["blocs"].append(bloc)
        elif bloc is None:
            if ligne.startswith("new file"):
                courant["statut"] = "ajouté"
            elif ligne.startswith("deleted file"):
                courant["statut"] = "supprimé"
            elif ligne.startswith("Binary files"):
                courant["binaire"] = True
        elif ligne and ligne[0] in "+- ":
            genre, texte = ligne[0], ligne[1:]
            entree: dict = {"g": genre, "t": texte}
            if genre == "+":
                entree["b"] = b
                b += 1
                courant["ajouts"] += 1
            elif genre == "-":
                entree["a"] = a
                a += 1
                courant["retraits"] += 1
            else:
                entree["a"], entree["b"] = a, b
                a += 1
                b += 1
            vues += 1
            if vues <= MAX_LIGNES_DIFF:
                bloc["lignes"].append(entree)
            else:
                courant["tronque"] = True
    _DIFFS[commit] = fichiers
    return fichiers


def section(sections: dict[str, list[str]], debut: str) -> list[str]:
    return next((v for k, v in sections.items() if k.lower().startswith(debut)), [])


def lire_ticket_md(ident: str) -> dict:
    """Ce que le ticket demande : objectif, sections de spec, fichiers, critères."""
    vide = {"titre": "", "objectif": "", "specs": "", "fichiers": [], "criteres": [],
            "precisions": [], "hors_perimetre": ""}
    chemin = DEPOT.racine / "tickets" / f"{ident}.md"
    if not RE_ID.fullmatch(ident) or not chemin.is_file():
        return vide
    sections: dict[str, list[str]] = {"": []}
    nom = ""
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        if ligne.startswith("## "):
            nom = ligne[3:].strip()
            sections[nom] = []
        else:
            sections[nom].append(ligne)
    tete = sections[""]
    titre = next((ligne[2:].strip() for ligne in tete if ligne.startswith("# ")), "")
    entete = " ".join(ligne for ligne in tete if ligne and not ligne.startswith("# "))
    specs = ""
    if "Sections à lire :" in entete:
        specs = entete.split("Sections à lire :", 1)[1].split(". En cas", 1)[0].strip()
    criteres = []
    for ligne in section(sections, "critères"):
        trouve = re.match(r"(\d+)\.\s+(.*)", ligne)
        if trouve:
            criteres.append({"n": int(trouve[1]), "texte": trouve[2].strip()})
        elif criteres and ligne.startswith("   "):
            criteres[-1]["texte"] += " " + ligne.strip()
    fichiers = [m[1] for ligne in section(sections, "fichiers") if (m := re.match(r"-\s+`([^`]+)`", ligne))]
    return {
        "titre": titre.split("·", 1)[-1].strip(),
        "objectif": " ".join(x for x in section(sections, "objectif") if x).strip(),
        "specs": specs,
        "fichiers": fichiers,
        "criteres": criteres,
        "precisions": [x[2:].strip() for x in section(sections, "précisions") if x.startswith("- ")],
        "hors_perimetre": " ".join(x for x in section(sections, "hors périmètre") if x).strip(),
    }


def nombre_de_cas(noeud: ast.FunctionDef) -> int:
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
    return cas


def tests_contrat(ident: str) -> list[dict]:
    """Tests de contrat d'un ticket. Le nom `test_cN_…` rattache un test au critère N."""
    tests = []
    motif = "test_" + ident.lower().replace("-", "_") + "_*.py"
    for fichier in sorted((DEPOT.racine / "tests" / "contrats").glob(motif)):
        try:
            arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for noeud in arbre.body:
            if not (isinstance(noeud, ast.FunctionDef) and noeud.name.startswith("test_")):
                continue
            trouve = re.match(r"test_c(\d+)_(.*)", noeud.name)
            libelle = trouve[2] if trouve else noeud.name.removeprefix("test_contrat_").removeprefix("test_")
            tests.append({
                "nom": noeud.name,
                "module": fichier.stem,
                "fichier": relatif(str(fichier)),
                "ligne": noeud.lineno,
                "cas": nombre_de_cas(noeud),
                "critere": int(trouve[1]) if trouve else None,
                "libelle": libelle.replace("_", " "),
            })
    return tests


def resultats_junit() -> dict[tuple[str, str], dict]:
    """Résultat par test, lu dans les rapports JUnit de `.chaine/verifs/`. Le plus récent gagne."""
    resultats: dict[tuple[str, str], dict] = {}
    if not DEPOT.verifs.is_dir():
        return resultats
    for fichier in sorted(DEPOT.verifs.glob("*junit*.xml"), key=lambda p: p.stat().st_mtime):
        try:
            racine = ET.parse(fichier).getroot()
        except (ET.ParseError, OSError):
            continue
        vus: dict[tuple[str, str], dict] = {}
        for cas in racine.iter("testcase"):
            cle = (cas.get("classname", "").rsplit(".", 1)[-1], cas.get("name", "").split("[", 1)[0])
            compte = vus.setdefault(cle, {"reussis": 0, "echoues": 0, "ignores": 0, "source": fichier.name})
            if cas.find("failure") is not None or cas.find("error") is not None:
                compte["echoues"] += 1
            elif cas.find("skipped") is not None:
                compte["ignores"] += 1
            else:
                compte["reussis"] += 1
        resultats.update(vus)
    return resultats


def couverture_ticket(ticket: dict, verif: dict | None, junit: dict) -> dict:
    """Pour chaque critère du ticket, les tests de contrat qui le couvrent et leur état."""
    md = lire_ticket_md(ticket["id"])
    tests = tests_contrat(ticket["id"])
    compte = (verif or {}).get("contrats") or {}
    total = sum(t["cas"] for t in tests)
    par_compte = bool(tests and compte.get("reussis") == total and not compte.get("echoues"))
    for t in tests:
        mesure = junit.get((t["module"], t["nom"]))
        if mesure:
            t["etat"] = "echec" if mesure["echoues"] else "reussi" if mesure["reussis"] else "ignore"
            t["source"] = mesure["source"]
        elif par_compte:
            t["etat"], t["source"] = "reussi", "compte du ticket"
        else:
            t["etat"], t["source"] = "non_mesure", ""
    criteres = []
    for c in md["criteres"]:
        lies = [t for t in tests if t["critere"] == c["n"]]
        if not tests:
            etat = "tests_a_ecrire"
        elif not lies:
            etat = "sans_test"
        elif any(t["etat"] == "echec" for t in lies):
            etat = "echec"
        elif all(t["etat"] == "reussi" for t in lies):
            etat = "reussi"
        else:
            etat = "non_mesure"
        criteres.append({**c, "etat": etat, "tests": lies})
    return {
        "id": ticket["id"],
        "titre": ticket["titre"],
        "etat": ticket["etat"],
        "criteres": criteres,
        "autres_tests": [t for t in tests if t["critere"] is None],
        "nb_tests": len(tests),
        "nb_cas": total,
    }


def couverture() -> dict:
    evenements = lire_journal()
    verifs: dict[str, dict] = {}
    for e in evenements:
        if e["evt"] == "verif" and e.get("contrats"):
            verifs[e["ticket"]] = e
    junit = resultats_junit()
    tickets = [couverture_ticket(t, verifs.get(t["id"]), junit) for t in lire_tickets()]
    compte: dict[str, int] = {}
    for t in tickets:
        for c in t["criteres"]:
            compte[c["etat"]] = compte.get(c["etat"], 0) + 1
    return {"tickets": tickets, "compte": compte, "junit": sorted({v["source"] for v in junit.values()})}


# --------------------------------------------------------------------------
# Écritures de la page. Tout va dans `.chaine/suivi/`, jamais dans le dépôt suivi par git.

_ECRITURE = threading.Lock()
DECISIONS = {"corriger", "accepte", "hors_sujet"}
RE_CLE = re.compile(r"V\d+-(?:\d+|C\d+)@[0-9a-f]{7,40}/(?:r\d+-)?[a-z]+-\d+")


def lire_json(chemin: Path, defaut: object) -> object:
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaut


def ecrire_json(chemin: Path, valeur: object) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    provisoire = chemin.with_suffix(".tmp")
    provisoire.write_text(json.dumps(valeur, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(provisoire, chemin)


def noter_action(evt: str, **champs: object) -> None:
    DEPOT.suivi.mkdir(parents=True, exist_ok=True)
    ligne = {"t": maintenant().isoformat(timespec="seconds"), "evt": evt, **champs}
    with _ECRITURE, DEPOT.actions.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ligne, ensure_ascii=False) + "\n")


def rapports_avec_tri() -> list[dict]:
    tri = lire_json(DEPOT.tri, {})
    rapports = lire_rapports()
    for r in rapports:
        for c in r.get("constats", []):
            c["cle"] = f"{r.get('ticket')}@{r.get('commit')}/{c.get('id')}"
            c["tri"] = tri.get(c["cle"])
        # Décisions prises sur une relecture précédente du même commit, archivée depuis.
        prefixe = f"{r.get('ticket')}@{r.get('commit')}/"
        cles = {c["cle"] for c in r.get("constats", [])}
        r["tri_archive"] = sum(1 for cle in tri if cle.startswith(prefixe) and cle not in cles)
    return rapports


def trier(cle: str, decision: str, note: str) -> tuple[bool, str]:
    """Range la décision de Thomas sur un constat. Une décision vide l'efface."""
    if not RE_CLE.fullmatch(cle):
        return False, "constat inconnu"
    if decision and decision not in DECISIONS:
        return False, "décision inconnue"
    with _ECRITURE:
        tri = lire_json(DEPOT.tri, {})
        if decision:
            tri[cle] = {"decision": decision, "note": note[:500],
                        "t": maintenant().isoformat(timespec="seconds")}
        else:
            tri.pop(cle, None)
        ecrire_json(DEPOT.tri, tri)
    noter_action("tri", cle=cle, decision=decision or "effacée")
    return True, ""


# --------------------------------------------------------------------------
# Fiche d'un ticket, de la spec au constat


def fiche_ticket(ident: str) -> dict | None:
    lignes = {t["id"]: t for t in lire_tickets()}
    if ident not in lignes:
        return None
    evenements = lire_journal()
    runs = [r for r in construire_runs(evenements, boucle_vivante()) if r["ticket"] == ident]
    for run in runs:
        run.pop("evenements", None)
    verifs = [e for e in evenements if e["evt"] == "verif" and e.get("ticket") == ident]
    ligne = lignes[ident]
    commit = ligne["commit"] or next((r["commit"] for r in reversed(runs) if r.get("commit")), "")
    sortie = ""
    if runs:
        fichier = DEPOT.verifs / f"{runs[-1]['id']}-check.txt"
        if fichier.is_file():
            sortie = "\n".join(fichier.read_text(encoding="utf-8", errors="replace").splitlines()[-25:])
    info = git("show", "-s", "--format=%h\x1f%aI\x1f%s", commit).strip().split("\x1f") if commit else []
    note = DEPOT.racine / "tickets" / "notes" / f"{ident}.md"
    rapport = next((r for r in rapports_avec_tri() if r.get("ticket") == ident), None)
    return {
        "ligne": ligne,
        "ticket": lire_ticket_md(ident),
        "runs": runs,
        "verif": verifs[-1] if verifs else None,
        "sortie_check": sortie,
        "commit": {"empreinte": info[0], "date": info[1], "sujet": info[2]} if len(info) == 3 else None,
        "diff": lire_diff(commit),
        "revue": rapport,
        "couverture": couverture_ticket(ligne, verifs[-1] if verifs else None, resultats_junit()),
        "note": note.read_text(encoding="utf-8")[:6000] if note.is_file() else "",
    }


def rejeu(identifiant: str) -> dict | None:
    """Évènements d'une session, puis de la relecture de son commit, pour les rejouer."""
    evenements = lire_journal()
    run = next((r for r in construire_runs(evenements, boucle_vivante()) if r["id"] == identifiant), None)
    if run is None:
        return None
    suite = [{"t": run["debut"], "evt": "session_debut", "ticket": run["ticket"],
              "tentative": run["tentative"]}] + run["evenements"]
    if run.get("commit"):
        suite += [e for e in evenements if e.get("commit") == run["commit"]
                  and e["evt"] in ("revue_debut", "agent_debut", "agent_fin", "revue_fin")]
    suite.sort(key=lambda e: secondes(e.get("t")) or 0)
    return {"id": identifiant, "debut": suite[0]["t"], "fin": suite[-1]["t"], "evenements": suite}


def pensees(identifiant: str, depuis: int) -> dict | None:
    """Pensées et textes de Qwen, tour par tour, à partir du tour `depuis`."""
    evenements = lire_journal()
    run = next((r for r in construire_runs(evenements, boucle_vivante()) if r["id"] == identifiant), None)
    if run is None:
        return None
    session = lire_session(Path(run["fichier"]))
    tours = [
        {"n": t["n"], "t": iso(t.get("ts_fin")), "pensee": t["pensee"], "texte": t["texte"],
         "tokens": t["tokens"], "outils": [f"{o['nom']} · {o['entree'][:90]}" for o in t["outils"]]}
        for t in session["tours"] if t["n"] > depuis and (t["pensee"] or t["texte"])
    ]
    return {"id": identifiant, "ticket": run["ticket"], "statut": run["statut"],
            "nb_tours": len(session["tours"]), "tours": tours[-40:]}


# --------------------------------------------------------------------------
# Alertes de santé

_BATTERIE: dict = {"t": 0.0, "v": None}
DELAI_SESSION_MIN = 120


def etat_batterie() -> dict | None:
    with _VERROU:
        if time.monotonic() - _BATTERIE["t"] < 30:
            return _BATTERIE["v"]
        valeur = None
        try:
            sortie = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True,
                                    timeout=4, check=False).stdout
            pourcent = re.search(r"(\d+)%", sortie)
            if sortie:
                valeur = {"secteur": "AC Power" in sortie, "pourcent": int(pourcent[1]) if pourcent else None}
        except (OSError, subprocess.SubprocessError):
            valeur = None
        _BATTERIE.update(t=time.monotonic(), v=valeur)
        return valeur


def calculer_alertes(evenements: list[dict], runs: list[dict], chaine: dict, tickets: list[dict],
                     mesures: dict, revues: dict) -> list[dict]:
    alertes: list[dict] = []

    def alerte(niveau: str, code: str, titre: str, detail: str) -> None:
        alertes.append({"niveau": niveau, "code": code, "titre": titre, "detail": detail})

    vivante = chaine["boucle"]
    run = runs[-1] if runs and runs[-1]["statut"] in ("en_cours", "verification") else None
    if vivante and run and run["statut"] == "en_cours" and run.get("fichier"):
        with contextlib.suppress(OSError):
            silence = time.time() - Path(run["fichier"]).stat().st_mtime
            if silence > 600:
                alerte("critique" if silence > 1200 else "alerte", f"silence-{run['id']}",
                       "La session ne produit plus rien",
                       f"{run['id']} n'a rien écrit depuis {round(silence / 60)} minutes. "
                       "Le modèle est peut-être bloqué.")
        ecoule = run["duree_s"] / 60
        delai = mesures.get("delai_session") or DELAI_SESSION_MIN
        if ecoule > delai - 20:
            alerte("alerte", f"delai-{run['id']}", "La session approche du délai",
                   f"{run['id']} tourne depuis {round(ecoule)} minutes, le délai est de {delai} minutes.")
        de_la_session = [e for e in run.get("evenements", [])]
        erreurs = [e.get("resume", "")[:120] for e in de_la_session if e["evt"] == "outil_erreur"]
        if len(erreurs) >= 3 and len(set(erreurs[-3:])) == 1 and de_la_session[-1]["evt"] == "outil_erreur":
            alerte("alerte", f"erreur-{run['id']}-{len(erreurs)}", "Qwen répète la même erreur",
                   f"Trois erreurs d'outil identiques de suite : {erreurs[-1]}")
        appels = [(e.get("nom"), e.get("resume")) for e in de_la_session if e["evt"] == "outil"]
        if len(appels) >= 4 and len(set(appels[-4:])) == 1:
            alerte("alerte", f"boucle-{run['id']}-{len(appels)}", "Qwen relance la même commande",
                   f"Quatre appels identiques de suite : {appels[-1][0]} · {str(appels[-1][1])[:120]}")
    if vivante:
        lm = chaine.get("lmstudio") or {}
        if not chaine["modele_8080"] or lm.get("statut") == "aucun":
            alerte("critique", "modele", "Le modèle n'est plus chargé",
                   "LM Studio ne répond pas sur le port 8080 ou n'a aucun modèle chargé.")
        if not chaine["passerelle_4000"]:
            alerte("critique", "passerelle", "La passerelle est éteinte",
                   "LiteLLM ne répond pas sur le port 4000. Relancez `scripts/chaine.sh demarrer`.")
        batterie = chaine.get("batterie") or {}
        if batterie and not batterie.get("secteur"):
            faible = (batterie.get("pourcent") or 100) < 25
            alerte("critique" if faible else "alerte", "batterie", "Le Mac est sur batterie",
                   f"Il reste {batterie.get('pourcent')} %. Branchez le secteur avant que la vague s'arrête.")
        if not revues.get("veille"):
            alerte("alerte", "veille", "Les relecteurs automatiques sont à l'arrêt",
                   "La boucle tourne, mais aucun agent ne relira ses commits.")
    else:
        fins = [e for e in evenements if e["evt"].startswith("boucle_")]
        if fins and fins[-1]["evt"] == "boucle_debut":
            alerte("critique", f"arret-{fins[-1]['t']}", "La boucle s'est arrêtée sans le dire",
                   "Aucun évènement de fin après le dernier démarrage. Le Mac a peut-être redémarré.")
        if DEPOT.pause.exists():
            alerte("info", "pause", "Pause demandée",
                   "La boucle s'arrêtera après le ticket en cours à son prochain passage.")
    for t in tickets:
        if t["etat"] == "bloqué":
            alerte("critique", f"bloque-{t['id']}", f"{t['id']} est bloqué",
                   f"Trois tentatives sans succès. Voir tickets/notes/{t['id']}.md.")
    a_trier: dict[str, int] = {}
    for r in rapports_avec_tri():
        en_erreur = [a.get("agent") for a in r.get("angles", []) if a.get("verdict") == "erreur"]
        if en_erreur:
            alerte("alerte", f"incomplet-{r.get('ticket')}-{r.get('commit')}",
                   f"Relecture incomplète de {r.get('ticket')}",
                   f"L'angle {', '.join(str(a) for a in en_erreur)} n'a pas pu relire. "
                   "Relancez la relecture depuis le panneau Pilotage.")
        for c in r.get("constats", []):
            if c.get("statut") == "confirme" and not c.get("tri") and c.get("gravite") in ("bloquant", "majeur"):
                a_trier[c["gravite"]] = a_trier.get(c["gravite"], 0) + 1
    if a_trier.get("bloquant"):
        alerte("critique", f"tri-bloquant-{a_trier['bloquant']}", "Constats bloquants à trier",
               f"{a_trier['bloquant']} constat(s) bloquant(s) confirmé(s) attendent votre décision, onglet Revues.")
    if a_trier.get("majeur"):
        alerte("alerte", f"tri-majeur-{a_trier['majeur']}", "Constats majeurs à trier",
               f"{a_trier['majeur']} constat(s) majeur(s) confirmé(s) attendent votre décision, onglet Revues.")
    if not vivante and mesures["tickets"] and mesures["faits"] + mesures["bloques"] == mesures["tickets"]:
        rapport = DEPOT.racine / "revues" / f"vague-{mesures['vague']}.md"
        if not rapport.exists():
            alerte("info", f"revue-vague-{mesures['vague']}", "Vague terminée, revue à faire",
                   f"Les {mesures['tickets']} tickets sont traités. Le rapport revues/vague-{mesures['vague']}.md "
                   "n'existe pas encore.")
    return alertes


# --------------------------------------------------------------------------
# Mesures : rapport de vague prérempli, débit, coûts, carte du code, garde

GRAVITES = ("bloquant", "majeur", "mineur", "remarque")


def rapport_vague(vague: int | None) -> dict:
    evenements = lire_journal()
    debuts = [e for e in evenements if e["evt"] == "boucle_debut"]
    if vague is None:
        vague = debuts[-1].get("vague") if debuts else 0
    prefixe = f"V{vague}-"
    runs = [r for r in construire_runs(evenements, boucle_vivante()) if r["ticket"].startswith(prefixe)]
    tickets = [t for t in lire_tickets() if t["id"].startswith(prefixe)]
    rapports = {r.get("ticket"): r for r in rapports_avec_tri()}
    interventions = [e for e in evenements if e["evt"] == "intervention"]
    lignes = []
    sans_aide = 0
    for t in tickets:
        les_runs = [r for r in runs if r["ticket"] == t["id"]]
        aides = [
            e for e in interventions if e.get("auteur") == "Thomas" and any(
                (secondes(r["debut"]) or 0) <= (secondes(e["t"]) or 0) <= (secondes(r["fin"]) or 9e12)
                for r in les_runs)
        ]
        seul = t["etat"] == "fait" and len(les_runs) == 1 and not aides
        sans_aide += seul
        revue = rapports.get(t["id"])
        confirmes = [c for c in (revue or {}).get("constats", []) if c.get("statut") == "confirme"]
        dernier = les_runs[-1] if les_runs else {}
        lignes.append({
            "id": t["id"], "titre": t["titre"], "etat": t["etat"], "tentatives": len(les_runs),
            "commit": t["commit"], "sans_aide": seul,
            "duree_s": sum(r["duree_s"] for r in les_runs),
            "tours": sum(r["nb_tours"] for r in les_runs),
            "erreurs_outils": sum(r["nb_erreurs"] for r in les_runs),
            "refus": sum(r["nb_refus"] for r in les_runs),
            "compactions": sum(r["nb_compactions"] for r in les_runs),
            "contrats": dernier.get("contrats"),
            "hors_liste": dernier.get("hors_liste") or [],
            "revue": revue.get("verdict") if revue else None,
            "constats": {g: sum(1 for c in confirmes if c.get("gravite") == g) for g in GRAVITES},
            "tri": {d: sum(1 for c in confirmes if (c.get("tri") or {}).get("decision") == d) for d in DECISIONS},
            "a_trier": sum(1 for c in confirmes if not c.get("tri")),
            "a_corriger": [
                {"titre": c.get("titre"), "fichier": c.get("fichier"), "ligne": c.get("ligne"),
                 "gravite": c.get("gravite"), "attendu": c.get("attendu") or "", "regle": c.get("regle") or ""}
                for c in confirmes
                if (c.get("tri") or {}).get("decision") == "corriger"
                or (not c.get("tri") and c.get("gravite") in ("bloquant", "majeur"))
            ],
        })
    de_la_vague = [e for e in evenements if e["evt"] in ("boucle_debut", "boucle_fin", "boucle_arret")
                   and (e.get("vague") == vague or e["evt"] == "boucle_arret")]
    debut = next((e["t"] for e in de_la_vague if e["evt"] == "boucle_debut"), None)
    fin = next((e["t"] for e in reversed(de_la_vague) if e["evt"] == "boucle_fin"), None)
    archives = sorted(str(p.relative_to(DEPOT.chaine)) for p in (DEPOT.chaine / "archives").glob("*/sessions/*.jsonl"))
    mesures = {
        "vague": vague,
        "tickets": len(tickets),
        "faits": sum(1 for t in tickets if t["etat"] == "fait"),
        "bloques": sum(1 for t in tickets if t["etat"] == "bloqué"),
        "sans_aide": sans_aide,
        "sessions": len(runs),
        "interventions_thomas": sum(1 for e in interventions if e.get("auteur") == "Thomas"),
        "interventions_relecteur": sum(1 for e in interventions if e.get("auteur") != "Thomas"),
        "debut": debut,
        "fin": fin,
        "duree_s": round((secondes(fin) or 0) - (secondes(debut) or 0)) if debut and fin else None,
        "duree_sessions_s": sum(r["duree_s"] for r in runs),
        "restaurations_garde": sum(len(e.get("restaures") or []) for e in evenements if e["evt"] == "garde"),
        "cout_revues_usd": round(sum(r.get("cout_usd") or 0 for r in rapports.values()
                                     if str(r.get("ticket", "")).startswith(prefixe)), 2),
        "sessions_archivees": archives,
        "pilotage": [e for e in evenements if e["evt"] == "pilotage"],
    }
    rapport = DEPOT.racine / "revues" / f"vague-{vague}.md"
    return {"mesures": mesures, "tickets": lignes, "interventions": interventions,
            "rapport_existe": rapport.exists(), "markdown": markdown_rapport(mesures, lignes, interventions)}


def duree_texte(s: float | None) -> str:
    if s is None:
        return "inconnue"
    s = round(s)
    heures, minutes = divmod(s // 60, 60)
    return f"{heures} h {minutes:02d}" if heures else f"{minutes} min"


def markdown_rapport(mesures: dict, lignes: list[dict], interventions: list[dict]) -> str:
    """Sections du rapport de vague que la chaîne sait remplir seule. Le verdict reste au relecteur."""
    m = mesures
    sortie = [
        "## Tickets",
        "",
        "Prérempli par `scripts/suivi.py` à partir des relecteurs automatiques. À juger par le relecteur.",
        "",
        "| Ticket | Verdict | Critère en défaut | Fichier et ligne | Attendu |",
        "| --- | --- | --- | --- | --- |",
    ]
    for t in lignes:
        if not t["a_corriger"]:
            sortie.append(f"| {t['id']} | à juger | aucun constat retenu | | |")
        for c in t["a_corriger"]:
            ou = f"`{c['fichier']}`" + (f":{c['ligne']}" if c["ligne"] else "")
            sortie.append(f"| {t['id']} | à juger | {c['titre']} ({c['gravite']}) | {ou} | {c['attendu']} |")
    sortie += [
        "",
        "## Mesures",
        "",
        "| Mesure | Valeur |",
        "| --- | --- |",
        f"| Tickets terminés sans aide | {m['sans_aide']} sur {m['tickets']} |",
        f"| Tickets faits | {m['faits']} sur {m['tickets']} |",
        f"| Tickets bloqués | {m['bloques']} |",
        f"| Interventions de Thomas | {m['interventions_thomas']} |",
        f"| Réglages de la chaîne par le relecteur | {m['interventions_relecteur']} |",
        f"| Sessions lancées | {m['sessions']} |",
        f"| Durée de la vague | {duree_texte(m['duree_s'])} |",
        f"| Durée cumulée des sessions | {duree_texte(m['duree_sessions_s'])} |",
        f"| Fichiers protégés restaurés par la garde | {m['restaurations_garde']} |",
        f"| Coût des relecteurs automatiques | {m['cout_revues_usd']} $ |",
        f"| Sessions interrompues avant la vague, archivées | {len(m['sessions_archivees'])} |",
        "",
        "| Ticket | Tentatives | Durée | Tours | Erreurs d'outil | Refus | Contrats réussis | Hors liste | Relecture |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t in lignes:
        contrats = (t["contrats"] or {}).get("reussis", "")
        constats = ", ".join(f"{n} {g}" for g, n in t["constats"].items() if n) or "aucun constat"
        sortie.append(
            f"| {t['id']} | {t['tentatives']} | {duree_texte(t['duree_s'])} | {t['tours']} | "
            f"{t['erreurs_outils']} | {t['refus']} | {contrats} | {len(t['hors_liste'])} | "
            f"{t['revue'] or 'non relu'} ({constats}) |"
        )
    if interventions:
        sortie += ["", "Interventions notées dans le journal :", ""]
        sortie += [f"- {e['t'][11:16]}, {e.get('auteur')}. {e.get('texte')}" for e in interventions]
    return "\n".join(sortie) + "\n"


def mesures_debit() -> dict:
    """Débit du modèle, partage du temps, coût des relectures et projection."""
    evenements = lire_journal()
    runs = construire_runs(evenements, boucle_vivante())
    series = {}
    for run in runs:
        session = lire_session(Path(run["fichier"])) if run.get("fichier") else {"tours": [], "compactions": []}
        series[run["id"]] = {
            "tours": [
                {"n": t["n"], "tokens": t["tokens"], "pense_s": round((t.get("pense_ms") or 0) / 1000, 1),
                 "debit": round(t["tokens"] / (t["pense_ms"] / 1000), 1)
                 if (t.get("pense_ms") or 0) >= 3000 and t["tokens"] else None}
                for t in session["tours"]
            ],
            "compactions": session["compactions"],
        }
        run.pop("evenements", None)
    rapports = lire_rapports()
    couts = []
    cumul = 0.0
    for r in sorted(rapports, key=lambda r: r.get("date") or ""):
        angles = {a.get("agent"): a.get("cout") or 0 for a in r.get("angles", [])}
        total = r.get("cout_usd") or 0
        cumul += total
        couts.append({"ticket": r.get("ticket"), "date": r.get("date"), "total": total,
                      "angles": angles, "verificateur": round(max(0, total - sum(angles.values())), 4),
                      "cumul": round(cumul, 2), "duree_s": r.get("duree_s")})
    tickets = lire_tickets()
    faits = [t for t in tickets if t["etat"] == "fait"]
    restants = [t for t in tickets if t["etat"] == "à faire"]
    reussis = [r for r in runs if r["statut"] == "reussi"]
    par_ticket = sum(r["duree_s"] for r in runs if r["ticket"] in {t["id"] for t in faits}) / max(1, len(faits))
    cout_moyen = cumul / max(1, len(couts))
    projection = {
        "base": len(faits),
        "restants": len(restants),
        "duree_par_ticket_s": round(par_ticket) if faits else None,
        "duree_restante_s": round(par_ticket * len(restants)) if faits else None,
        "cout_par_ticket_usd": round(cout_moyen, 2) if couts else None,
        "cout_restant_usd": round(cout_moyen * len(restants), 1) if couts else None,
        "debit_moyen": round(sum(r["debit"] or 0 for r in reussis) / max(1, sum(1 for r in reussis if r["debit"])), 1)
        if reussis else None,
    }
    return {"runs": runs, "series": series, "couts": couts, "projection": projection}


_CARTE: dict = {"tete": None, "v": None}


def carte_code() -> dict:
    """Fichiers de `src/` et `tests/unit/`, avec le ticket qui les a créés et les constats posés dessus."""
    tete = git("rev-parse", "HEAD").strip()
    if _CARTE["tete"] != tete or _CARTE["v"] is None:
        origine: dict[str, dict] = {}
        courant = ""
        journal = git("log", "--reverse", "--name-status", "--format=@%s", "--", "src", "tests/unit")
        for ligne in journal.splitlines():
            if ligne.startswith("@"):
                trouve = re.match(r"@\[(V\d+-(?:\d+|C\d+))\]", ligne)
                courant = trouve[1] if trouve else ""
            elif "\t" in ligne:
                chemin = ligne.split("\t")[-1]
                info = origine.setdefault(chemin, {"ticket": courant, "tickets": []})
                if courant and courant not in info["tickets"]:
                    info["tickets"].append(courant)
        fichiers = []
        for chemin in git("ls-files", "src", "tests/unit").splitlines():
            try:
                lignes = len((DEPOT.racine / chemin).read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                continue
            info = origine.get(chemin, {"ticket": "", "tickets": []})
            fichiers.append({"chemin": chemin, "lignes": lignes, "ticket": info["ticket"],
                             "tickets": info["tickets"]})
        _CARTE.update(tete=tete, v=fichiers)
    fichiers = [dict(f) for f in _CARTE["v"]]
    par_fichier: dict[str, list[dict]] = {}
    for r in rapports_avec_tri():
        for c in r.get("constats", []):
            if c.get("statut") == "confirme" and c.get("fichier"):
                par_fichier.setdefault(c["fichier"], []).append(c)
    for f in fichiers:
        constats = par_fichier.get(f["chemin"], [])
        f["constats"] = len(constats)
        f["pire"] = next((g for g in GRAVITES if any(c.get("gravite") == g for c in constats)), None)
        f["titres"] = [f"{c.get('gravite')} · {c.get('titre')}" for c in constats][:6]
    return {"tete": tete[:7], "fichiers": fichiers}


def journal_garde() -> dict:
    """Tout ce que la garde a refusé, restauré ou signalé, session par session."""
    evenements = lire_journal()
    runs = construire_runs(evenements, boucle_vivante())
    lignes: list[dict] = []
    for e in evenements:
        if e["evt"] == "garde":
            lignes.append({"t": e["t"], "ticket": e.get("ticket"), "genre": "restaure",
                           "detail": ", ".join(e.get("restaures") or [])})
        elif e["evt"] == "hors_liste":
            lignes.append({"t": e["t"], "ticket": e.get("ticket"), "genre": "hors_liste",
                           "detail": ", ".join(e.get("fichiers") or [])})
        elif e["evt"] == "alerte":
            lignes.append({"t": e["t"], "ticket": e.get("ticket"), "genre": "alerte",
                           "detail": e.get("detail") or ""})
    for run in runs:
        if not run.get("fichier"):
            continue
        session = lire_session(Path(run["fichier"]))
        for r in session["refus"]:
            lignes.append({"t": iso(r.get("ts")) or run["debut"], "ticket": run["ticket"], "genre": "refus",
                           "detail": f"{r.get('outil')} · {r.get('entree') or 'commande inconnue'}",
                           "motif": r.get("motif")})
        for tour in session["tours"]:
            for o in tour["outils"]:
                if str(o.get("fichier") or "").startswith("/") and not o["erreur"]:
                    lignes.append({"t": iso(o.get("ts")) or run["debut"], "ticket": run["ticket"],
                                   "genre": "hors_depot", "detail": f"{o['nom']} · {o['fichier']}",
                                   "motif": "fichier écrit hors du dépôt"})
                if o["erreur"] and "denied" not in str(o.get("resultat") or "")[:200]:
                    lignes.append({"t": iso(o.get("ts")) or run["debut"], "ticket": run["ticket"],
                                   "genre": "erreur", "detail": f"{o['nom']} · {o['entree'][:200]}",
                                   "motif": str(o.get("resultat") or "")[:300]})
        for c in session["compactions"]:
            lignes.append({"t": iso(c.get("ts")) or run["debut"], "ticket": run["ticket"], "genre": "compaction",
                           "detail": f"contexte résumé, de {c.get('avant')} à {c.get('apres')} tokens "
                                     f"en {round((c.get('duree_ms') or 0) / 60000, 1)} minutes"})
    lignes.sort(key=lambda x: secondes(x["t"]) or 0, reverse=True)
    compte: dict[str, int] = {}
    for ligne in lignes:
        compte[ligne["genre"]] = compte.get(ligne["genre"], 0) + 1
    return {"lignes": lignes, "compte": compte}


# --------------------------------------------------------------------------
# Pilotage : les seules actions de la page qui touchent à la chaîne


def pid_vivant(fichier: Path, attendu: str) -> int | None:
    """Pid lu dans `fichier`, s'il désigne bien un processus dont la commande contient `attendu`."""
    try:
        pid = int(fichier.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    commande = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                              text=True, check=False).stdout
    return pid if attendu in commande else None


def lancer_detache(commande: list[str], journal: Path) -> int:
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("ab") as sortie:
        proc = subprocess.Popen(commande, cwd=DEPOT.racine, stdin=subprocess.DEVNULL, stdout=sortie,
                                stderr=subprocess.STDOUT, start_new_session=True)
    return proc.pid


def piloter(action: str, corps: dict) -> tuple[bool, str]:
    """Exécute une action de pilotage. Renvoie (succès, message pour Thomas)."""
    scripts = DEPOT.racine / "scripts"
    boucle = pid_vivant(DEPOT.pid, "boucle_executant")
    veille = pid_vivant(DEPOT.revues / "veille.pid", "relecteurs")
    if action == "boucle_arreter":
        if not boucle:
            return False, "La boucle ne tourne pas."
        os.kill(boucle, signal.SIGTERM)
        return True, "Arrêt demandé. La session en cours est interrompue, son travail reste dans l'arbre."
    if action == "boucle_pause":
        if not boucle:
            return False, "La boucle ne tourne pas."
        DEPOT.pause.write_text(maintenant().isoformat(timespec="seconds") + "\n")
        return True, "La boucle s'arrêtera après le ticket en cours."
    if action == "boucle_pause_annuler":
        DEPOT.pause.unlink(missing_ok=True)
        return True, "Pause annulée."
    if action == "boucle_reprendre":
        if boucle:
            return False, "La boucle tourne déjà."
        debuts = [e for e in lire_jsonl(DEPOT.journal) if e["evt"] == "boucle_debut"]
        if not debuts:
            return False, "Aucune vague n'a encore été lancée. Le premier lancement se fait au terminal."
        vague = debuts[-1].get("vague")
        if not any(t["id"].startswith(f"V{vague}-") and t["etat"] == "à faire" for t in lire_tickets()):
            return False, (f"La vague {vague} n'a plus de ticket à faire. Une nouvelle vague se lance au "
                           "terminal, après un rapport au verdict GO et votre accord.")
        commande = ["caffeinate", "-i", sys.executable, str(scripts / "boucle_executant.py"), str(vague)]
        if git("status", "--porcelain").strip():
            if not corps.get("garder"):
                return False, "ARBRE_NON_PROPRE"
            commande.append("--garder")
        DEPOT.pause.unlink(missing_ok=True)
        lancer_detache(commande, DEPOT.chaine / "boucle.log")
        return True, f"Boucle relancée sur la vague {vague}."
    if action == "veille_demarrer":
        if veille:
            return False, "Les relecteurs sont déjà en veille."
        lancer_detache([sys.executable, str(scripts / "relecteurs.py")], DEPOT.revues / "veille.log")
        return True, "Relecteurs en veille. Ils reliront chaque nouveau commit de ticket."
    if action == "veille_arreter":
        if not veille:
            return False, "Les relecteurs ne sont pas en veille."
        os.kill(veille, signal.SIGTERM)
        return True, "Veille des relecteurs arrêtée."
    if action == "revue_relancer":
        ticket = str(corps.get("ticket") or "")
        fait = next((t for t in lire_tickets() if t["id"] == ticket and t["etat"] == "fait"), None)
        if not fait:
            return False, "Seul un ticket fait peut être relu."
        lancer_detache([sys.executable, str(scripts / "relecteurs.py"), "--ticket", ticket],
                       DEPOT.revues / "veille.log")
        return True, f"Relecture de {ticket} lancée. Comptez une à deux minutes et environ un dollar."
    if action == "notifications":
        reglages = lire_json(DEPOT.reglages, {})
        reglages["notifications"] = bool(corps.get("actif"))
        with _ECRITURE:
            ecrire_json(DEPOT.reglages, reglages)
        return True, "Notifications macOS " + ("activées." if reglages["notifications"] else "coupées.")
    return False, "Action inconnue."


def notifier(titre: str, texte: str) -> None:
    script = ["osascript", "-e", "on run argv", "-e",
              "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run"]
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run([*script, titre, texte[:240]], capture_output=True, timeout=5, check=False)


def veilleur() -> None:
    """Fil de fond : envoie une notification macOS pour chaque alerte et chaque verdict nouveaux."""
    vus: set[str] | None = None
    rang = len(lire_journal())
    while True:
        time.sleep(20)
        try:
            actif = bool(lire_json(DEPOT.reglages, {}).get("notifications"))
            etat = construire_etat()
            codes = {a["code"]: a for a in etat["alertes"] if a["niveau"] != "info"}
            evenements = lire_journal()
            nouveaux = evenements[rang:] if rang <= len(evenements) else []
            rang = len(evenements)
            if actif and vus is not None:
                for code, a in codes.items():
                    if code not in vus:
                        notifier("Cabinet · " + a["titre"], a["detail"])
                for e in nouveaux:
                    if e["evt"] == "commit":
                        notifier("Cabinet · ticket fait", f"{e.get('ticket')} est validé, commit {e.get('commit')}.")
                    elif e["evt"] == "bloque":
                        notifier("Cabinet · ticket bloqué", f"{e.get('ticket')} après {e.get('tentatives')} tentatives.")
                    elif e["evt"] == "boucle_fin":
                        notifier("Cabinet · vague terminée", f"La boucle de la vague {e.get('vague')} est finie.")
                    elif e["evt"] == "revue_fin" and e.get("verdict") != "ok":
                        notifier("Cabinet · relecture", f"{e.get('ticket')} : {e.get('verdict')}, "
                                                        f"{e.get('confirmes')} constat(s) confirmé(s).")
            vus = set(codes)
        except Exception:  # noqa: BLE001, le veilleur ne doit jamais faire tomber la page
            continue


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
@media (max-width:900px){.grille2{grid-template-columns:minmax(0,1fr)}}
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
[hidden]{display:none!important}
.bouton{border:1px solid var(--trait);background:var(--surface);padding:5px 12px;border-radius:8px;cursor:pointer;white-space:nowrap}
.bouton:hover{border-color:var(--accent)} .bouton:disabled{opacity:.5;cursor:default}
.bouton.fort{background:var(--accent);border-color:var(--accent);color:#fff}
.bouton.danger{border-color:var(--critique);color:var(--critique)}
.haut-d{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;justify-content:flex-end}
select,input[type=range]{font:inherit;color:inherit;accent-color:var(--accent)}
select{background:var(--surface);border:1px solid var(--trait);border-radius:8px;padding:4px 8px}
#alertes{max-width:1500px;margin:0 auto;padding:0 20px;display:grid;gap:6px}
#alertes:not(:empty){padding-top:12px}
.alerte-b{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;padding:8px 12px;border-radius:8px;border:1px solid var(--trait);border-left-width:4px;background:var(--surface);font-size:13px}
.alerte-b span{color:var(--doux)}
.alerte-b.critique{border-left-color:var(--critique)} .alerte-b.critique b{color:var(--critique)}
.alerte-b.alerte{border-left-color:var(--alerte)} .alerte-b.alerte b{color:var(--alerte)}
.alerte-b.info{border-left-color:var(--accent)}
.rejeu{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:center;margin-top:12px;padding:10px 12px;background:var(--surface-2);border-radius:8px}
.rejeu input[type=range]{flex:1 1 240px;min-width:160px}
.rejeu .mono{min-width:210px;color:var(--doux)}
.rejeu.actif{outline:2px solid var(--accent)}
#rj-evenement{flex-basis:100%;font-size:12.5px;color:var(--doux);min-height:18px}
#pensees{display:grid;gap:8px;margin-top:10px;max-height:420px;overflow:auto}
#pz-direct{margin-top:10px} #pz-direct-p{max-height:200px;overflow:auto}
.pz{border-left:3px solid var(--trait);padding:6px 10px;background:var(--surface-2);border-radius:0 8px 8px 0}
.pz.neuf{border-left-color:var(--accent)}
.pz header{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:12px;color:var(--doux)}
.pz header b{font:600 12px var(--mono);color:var(--accent)}
.pz p{margin:4px 0 0;white-space:pre-wrap;font-size:13px;word-break:break-word}
.pz p.dit{color:var(--encre)} .pz p.pense{color:var(--doux);font-style:italic}
.pz .curseur::after{content:"▍";color:var(--accent);animation:battre-o 1s infinite}
.multiples{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
.multiple h4{margin:0 0 2px;font-size:12.5px;font-weight:600}
.multiple svg{display:block;width:100%;height:auto}
.multiple text{fill:var(--doux);font-size:11px;font-family:var(--corps)}
.col{fill:var(--s1)} .col.choisi{fill:var(--accent)} .col.terne{fill:var(--gris);opacity:.55}
table.cote td,table.cote th{white-space:nowrap} table.cote tbody tr{cursor:default}
table.simple tbody tr{cursor:default} table.simple td{white-space:normal;vertical-align:top}
a.mono,td.nw{white-space:nowrap!important}
.legende .case{display:inline-grid;vertical-align:middle;margin-right:5px}
.ticket{cursor:pointer} .ticket:hover{border-color:var(--accent)}
.ticket .badges{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}
.mini{white-space:nowrap;font:600 11px var(--mono);padding:0 6px;border-radius:999px;border:1px solid var(--trait);color:var(--doux)}
.mini.bon{border-color:var(--bon);color:var(--bon)} .mini.alerte{border-color:var(--alerte);color:var(--alerte)} .mini.critique{border-color:var(--critique);color:var(--critique)}
#fiche{display:grid;gap:14px}
.fiche-tete{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
.fiche-tete h2{margin:0;font-size:18px}
.chemin{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:22px;overflow-x:auto;padding-bottom:2px}
.etape{position:relative;background:var(--surface);border:1px solid var(--trait);border-radius:10px;padding:10px 12px;border-top:4px solid var(--gris)}
.etape:not(:last-child)::after{content:"→";position:absolute;right:-18px;top:50%;transform:translateY(-50%);color:var(--gris)}
.etape.bon{border-top-color:var(--bon)} .etape.alerte{border-top-color:var(--alerte)} .etape.critique{border-top-color:var(--critique)} .etape.vif{border-top-color:var(--accent)}
.etape .qui{font:600 11px var(--mono);letter-spacing:.05em;text-transform:uppercase;color:var(--doux)}
.etape b{display:block;font-size:14px} .etape span{font-size:12.5px;color:var(--doux)}
.crit{display:grid;grid-template-columns:28px 1fr;gap:4px 8px;padding:6px 0;border-bottom:1px solid var(--trait);font-size:13px}
.crit:last-child{border-bottom:0}
.puce{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:6px;font:700 12px var(--mono);border:1.5px solid var(--gris);color:var(--doux)}
.puce.reussi{border-color:var(--bon);color:var(--bon)} .puce.echec{background:var(--critique);border-color:var(--critique);color:#fff}
.puce.sans_test{border-color:var(--critique);color:var(--critique);border-style:dashed} .puce.non_mesure{border-color:var(--alerte);color:var(--alerte)}
.crit>div{min-width:0;overflow-wrap:anywhere}
.crit .tests{color:var(--doux);font-size:12px} .crit .tests code{font-size:11.5px}
.fichiers-l{display:flex;flex-wrap:wrap;gap:6px}
.fichier-d>summary{cursor:pointer;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;padding:6px 0}
.fichier-d{border-bottom:1px solid var(--trait)}
.plus{color:var(--bon);font:600 12px var(--mono)} .moins{color:var(--critique);font:600 12px var(--mono)}
.diff{font:12px/1.45 var(--mono);border:1px solid var(--trait);border-radius:8px;overflow:auto;max-height:560px;margin:6px 0 10px}
.diff table{width:max-content;min-width:100%} .diff td{padding:0 8px;border:0;white-space:pre}
.diff tbody tr{cursor:default} .diff tbody tr:hover{background:none}
.diff td.no{color:var(--doux);text-align:right;user-select:none;width:1%;opacity:.75}
.diff tr.plus td{background:color-mix(in srgb,var(--bon) 14%,var(--surface))}
.diff tr.moins td{background:color-mix(in srgb,var(--critique) 13%,var(--surface))}
.diff tr.entete td{background:var(--surface-2);color:var(--doux)}
.diff tr.note td{white-space:normal;padding:6px 8px;font-family:var(--corps);font-size:13px;background:var(--surface)}
.constat{border:1px solid var(--trait);border-left:4px solid var(--gris);border-radius:8px;padding:8px 10px;margin:6px 0;background:var(--surface);display:grid;gap:4px;font:13px/1.5 var(--corps);max-width:110ch}
.constat.g-bloquant{border-left-color:var(--critique)} .constat.g-majeur{border-left-color:var(--alerte)}
.constat.st-rejete{opacity:.6}
.constat-t{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center}
.tri{display:inline-flex;border:1px solid var(--trait);border-radius:999px;overflow:hidden;margin-left:auto}
.tri button{border:0;background:none;padding:2px 10px;cursor:pointer;font-size:12px;border-right:1px solid var(--trait)}
.tri button:last-child{border-right:0} .tri button:hover{background:var(--surface-2)}
.tri button[aria-pressed="true"]{background:var(--accent);color:#fff;font-weight:600}
td .tri{margin-left:0}
@media (max-width:1200px){td .tri{flex-direction:column;border-radius:8px} td .tri button{border-right:0;border-bottom:1px solid var(--trait);text-align:left} td .tri button:last-child{border-bottom:0}}
.matrice{display:grid;gap:3px;overflow-x:auto}
.matrice .rang{display:grid;grid-template-columns:64px 1fr;gap:8px;align-items:center}
.matrice .rang>a{font:600 12px var(--mono);color:var(--accent);text-decoration:none}
.matrice .cases{display:flex;gap:3px}
.case{width:24px;height:24px;border-radius:5px;display:grid;place-items:center;font:700 11px var(--mono);border:1.5px solid var(--trait);color:var(--doux);flex:none}
.case.reussi{background:color-mix(in srgb,var(--bon) 22%,var(--surface));border-color:var(--bon);color:var(--encre)}
.case.echec{background:var(--critique);border-color:var(--critique);color:#fff}
.case.sans_test{border-color:var(--critique);border-style:dashed;color:var(--critique)}
.case.non_mesure{border-color:var(--alerte);color:var(--alerte)}
.partage .rg{display:grid;grid-template-columns:86px 1fr 70px;gap:10px;align-items:center;padding:5px 0;font-size:12.5px}
.partage .piste{display:flex;gap:2px;height:22px}
.partage .seg{height:100%;border-radius:4px;min-width:2px;display:grid;place-items:center;font:600 11px var(--mono);color:#fff;overflow:hidden}
.seg.raisonne{background:var(--accent)} .seg.outils{background:var(--s3)} .seg.compaction{background:var(--s4)} .seg.reste{background:var(--gris)}
.ligne-d{fill:none;stroke:var(--s1);stroke-width:2} .pt{fill:var(--s1);stroke:var(--surface);stroke-width:2}
.moy{stroke:var(--doux);stroke-dasharray:5 4}
.barre-h{display:inline-block;height:8px;border-radius:4px;background:var(--s1);vertical-align:middle;margin-right:6px}
#carte svg{display:block;width:100%;height:auto}
#carte text{font-family:var(--corps);fill:var(--encre);pointer-events:none}
#carte .dossier{fill:none;stroke:var(--trait)} #carte .dossier-t{font:600 11px var(--mono);fill:var(--doux)}
#carte .pave{stroke-width:1.3;cursor:pointer} #carte .pave:hover{stroke-width:2.6}
#carte .nb{font:700 10.5px var(--mono);fill:#fff}
.md{white-space:pre;overflow:auto;max-height:420px;font:12px/1.5 var(--mono);background:var(--surface-2);padding:10px 12px;border-radius:8px}
dialog{border:1px solid var(--trait);border-radius:12px;background:var(--surface);color:var(--encre);padding:18px 20px;width:min(560px,calc(100vw - 32px))}
dialog::backdrop{background:rgba(10,10,25,.45)}
.pil-tete{display:flex;justify-content:space-between;align-items:center;margin:0 0 8px;font-size:16px}
.pil-g{border-top:1px solid var(--trait);padding:10px 0}
.pil-g h3{margin:0 0 6px;font-size:13px} .pil-g .l{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pil-g p{margin:0 0 8px;font-size:13px;color:var(--doux)}
#avis{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);max-width:calc(100vw - 32px);padding:8px 14px;border-radius:8px;background:var(--surface-2);border:1px solid var(--trait);font-size:13px;z-index:50;box-shadow:0 4px 16px rgba(0,0,0,.15)} #avis.ko{border-color:var(--critique);color:var(--critique)}
#pil-message{font-size:13px;min-height:20px;padding-top:8px} #pil-message.ok{color:var(--bon)} #pil-message.ko{color:var(--critique)}
.cartes,#fiche,#revues,#detail{display:grid;gap:14px;grid-template-columns:minmax(0,1fr)}
.carte{min-width:0}
table.simple td.n,table.simple th{white-space:nowrap}
@media (max-width:700px){#alertes{padding-left:16px;padding-right:16px} .chemin{grid-template-columns:repeat(5,150px)} .partage .rg{grid-template-columns:70px 1fr}.partage .rg>span:last-child{display:none}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<header class="haut">
  <div class="marque"><div class="logo" aria-hidden="true">C</div>
    <div><div class="sur">Cabinet · chaîne de développement</div><h1 id="titre">Vague</h1></div></div>
  <div class="haut-d"><div class="pastilles" id="pastilles" aria-live="polite"></div>
    <button type="button" class="bouton" id="b-pilotage">Pilotage</button></div>
</header>
<nav class="onglets" role="tablist">
  <button role="tab" data-onglet="direct" aria-selected="true">Direct</button>
  <button role="tab" data-onglet="runs" aria-selected="false">Runs</button>
  <button role="tab" data-onglet="tickets" aria-selected="false">Tickets</button>
  <button role="tab" data-onglet="revues" aria-selected="false">Revues</button>
  <button role="tab" data-onglet="couverture" aria-selected="false">Couverture</button>
  <button role="tab" data-onglet="mesures" aria-selected="false">Mesures</button>
  <button role="tab" data-onglet="carte" aria-selected="false">Carte du code</button>
  <button role="tab" data-onglet="garde" aria-selected="false">Garde</button>
</nav>
<div id="alertes"></div>
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
      <div class="rejeu" id="rejeu">
        <button type="button" class="bouton" id="rj-jouer">▶ Rejouer</button>
        <select id="rj-run" aria-label="Session à rejouer"></select>
        <select id="rj-vitesse" aria-label="Vitesse du rejeu"><option value="30">×30</option><option value="60" selected>×60</option><option value="120">×120</option><option value="300">×300</option></select>
        <input type="range" id="rj-curseur" min="0" max="1000" value="0" aria-label="Position dans la session">
        <span class="mono" id="rj-horloge"></span>
        <button type="button" class="bouton" id="rj-direct" hidden>Revenir au direct</button>
        <div id="rj-evenement"></div>
      </div>
      <div class="pensee"><div id="pensee"></div>
        <div class="ligne">Pensée de Qwen, telle qu'il l'écrit : <b id="pensees-t"></b></div>
        <article class="pz neuf" id="pz-direct" hidden><header><b>En ce moment</b><span id="pz-direct-t"></span></header><p class="pense curseur" id="pz-direct-p"></p><p class="dit" id="pz-direct-dit"></p></article>
        <div id="pensees" aria-live="off"></div></div>
    </div>
    <aside class="carte flux"><div class="carte-t">Fil des évènements <span class="aide" id="flux-aide"></span></div><ol id="flux"></ol></aside>
  </div>
</section>
<section id="v-runs" hidden>
  <div class="carte"><div class="carte-t">Runs <span class="aide">une session de l'exécutant par ligne. Cliquez pour le détail.</span></div>
    <div class="defile"><table id="runs"><thead><tr><th><span title="Cochez pour comparer">⇄</span></th><th>Run</th><th>Statut</th><th>Début</th><th class="n">Durée</th><th class="n">Tours</th><th class="n">Outils</th><th class="n">Erreurs</th><th class="n">Tokens de raisonnement</th><th>Verdict</th></tr></thead><tbody></tbody></table></div>
  </div>
  <div id="comparaison"></div>
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
  <div id="fiche" hidden></div>
</section>
<section id="v-couverture" hidden>
  <div class="kpis" id="kpis-c"></div>
  <div class="filtres" id="filtres-c"></div>
  <div class="cartes" id="couverture"></div>
</section>
<section id="v-mesures" hidden>
  <div class="kpis" id="kpis-m"></div>
  <div class="cartes" id="mesures"></div>
</section>
<section id="v-carte" hidden><div id="carte"></div></section>
<section id="v-garde" hidden>
  <div class="kpis" id="kpis-g"></div>
  <div class="filtres" id="filtres-g"></div>
  <div id="garde"></div>
</section>
</main>
<dialog id="pilotage" aria-labelledby="pil-titre">
  <form method="dialog" class="pil-tete"><b id="pil-titre">Pilotage de la chaîne</b><button class="bouton">Fermer</button></form>
  <p class="aide">Ces boutons agissent sur la chaîne. Chaque action est notée dans le fil des évènements. Une nouvelle vague ne se lance pas d'ici : elle demande un rapport au verdict GO et votre accord.</p>
  <div class="pil-g"><h3>Boucle de développement</h3><div id="pil-boucle"></div></div>
  <div class="pil-g"><h3>Relecteurs automatiques</h3><div id="pil-revues"></div></div>
  <div class="pil-g"><h3>Notifications macOS</h3><div id="pil-notif"></div></div>
  <div id="pil-message" role="status"></div>
</dialog>
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
  revue_fin:"Relecture terminée", revue_erreur:"Relecture en échec", pilotage:"Pilotage depuis la page", tri:"Constat trié", boucle_pause:"Boucle en pause"};
const ONGLETS = ["direct","runs","tickets","revues","couverture","mesures","carte","garde"];
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
  for (const v of ONGLETS) $("#v-"+v).hidden = v !== onglet;
  history.replaceState(null, "", onglet === "direct" ? location.pathname : "#" + onglet);
  rendre();
}));

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
    case "pilotage": eclat("thomas"); await particule("e-thomas-boucle","accent"); eclat("boucle"); break;
  }
}
async function jouer(){ if (enLecture) return; enLecture = true;
  while (file.length){ const ev = file.shift(); ajouterFlux(ev, true); if (onglet === "direct" && !document.hidden && !rj.actif) await scene(ev); }
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
  el.innerHTML = `<div class="ligne">En ce moment : ${ph}.</div>`;
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
  if (["garde","hors_liste","alerte","boucle_arret","intervention","pilotage"].includes(ev.evt)) return "alerte";
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
    case "pilotage": return `${ev.action}${ev.ticket ? " " + ev.ticket : ""} : ${ev.detail}`;
    case "tri": return `${ev.cle} : ${ev.decision}`;
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
  if (!etat.runs.length){ tb.innerHTML = `<tr><td colspan="10" class="vide">Aucune session pour l'instant.</td></tr>`; return; }
  tb.innerHTML = etat.runs.slice().reverse().map(r => `<tr data-run="${esc(r.id)}" class="${r.id === runSel ? "sel" : ""}" tabindex="0">
    <td><input type="checkbox" data-cmp="${esc(r.id)}" ${cmp.has(r.id) ? "checked" : ""} aria-label="Comparer ${esc(r.id)}"></td>
    <td><b class="mono">${esc(r.ticket)}</b> · t${r.tentative}</td><td><span class="statut ${r.statut}">${STATUTS[r.statut] || r.statut}</span></td>
    <td>${heure(r.debut)}</td><td class="n">${duree(r.duree_s)}</td><td class="n">${r.nb_tours}</td><td class="n">${r.nb_outils}</td>
    <td class="n">${r.nb_erreurs}</td><td class="n">${nf.format(r.tokens_raisonnement)}</td><td>${esc(r.commit ? "commit " + r.commit : r.motif || "")}</td></tr>`).join("");
  tb.querySelectorAll("tr[data-run]").forEach(tr => { const ouvrir = () => { runSel = tr.dataset.run; runs(); detail(); };
    tr.addEventListener("click", e => { if (!e.target.matches("input")) ouvrir(); }); tr.addEventListener("keydown", e => { if (e.key === "Enter") ouvrir(); }); });
  tb.querySelectorAll("[data-cmp]").forEach(c => c.addEventListener("change", () => { c.checked ? cmp.add(c.dataset.cmp) : cmp.delete(c.dataset.cmp); comparer(); }));
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
  for (const id of ["kanban","filtres","kpis-t"]) $("#" + id).hidden = !!ficheSel;
  $("#fiche").hidden = !ficheSel;
  if (ficheSel){ fiche(); return; }
  const relus = Object.fromEntries((etat.revues.tickets || []).map(r => [r.ticket, r]));
  const lots = [...new Set(etat.tickets.map(t => t.id.split("-")[0]))];
  $("#filtres").innerHTML = [`<button aria-pressed="${lotFiltre === null}" data-lot="">Tous</button>`].concat(lots.map(l =>
    `<button aria-pressed="${lotFiltre === l}" data-lot="${l}">Lot ${l.slice(1)}</button>`)).join("");
  $("#filtres").querySelectorAll("button").forEach(b => b.addEventListener("click", () => { lotFiltre = b.dataset.lot || null; tickets(); }));
  const courant = etat.en_cours && etat.en_cours.ticket;
  const col = {"à faire":[], "en cours":[], "fait":[], "bloqué":[]};
  etat.tickets.filter(t => !lotFiltre || t.id.startsWith(lotFiltre + "-")).forEach(t => {
    (t.id === courant ? col["en cours"] : (col[t.etat] || col["à faire"])).push(t); });
  poser("#kanban", Object.entries(col).map(([k, l]) => `<div class="colonne"><h3><span>${k[0].toUpperCase() + k.slice(1)}</span><span>${l.length}</span></h3>
    ${l.map(t => `<div class="ticket ${t.id === courant ? "courant" : ""}" data-fiche="${esc(t.id)}" tabindex="0" role="link" aria-label="Ouvrir la fiche de ${esc(t.id)}"><div class="id">${esc(t.id)}</div><div class="ti">${esc(t.titre)}</div>
    <div class="me">dépend de : ${esc(t.depend)}${+t.tentatives ? ` · ${t.tentatives} tentative(s)` : ""}${t.commit ? ` · ${esc(t.commit)}` : ""}</div>
    ${relus[t.id] ? `<div class="badges"><span class="mini ${COUL_V[relus[t.id].verdict] || ""}">relu : ${esc(VERDICT[relus[t.id].verdict] || relus[t.id].verdict)}</span>${relus[t.id].confirmes ? `<span class="mini">${relus[t.id].confirmes} constat(s)</span>` : ""}</div>` : ""}</div>`).join("") || `<div class="vide">vide</div>`}</div>`).join(""));
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
  poser("#revues", R.slice().reverse().map(r => `<div class="carte revue">
    <div class="carte-t"><a href="#ticket=${esc(r.ticket)}" data-fiche="${esc(r.ticket)}" class="mono">${esc(r.ticket)}</a> · commit <code>${esc(r.commit)}</code> <span class="statut ${r.verdict === "ok" ? "reussi" : r.verdict === "ko" ? "echec" : "interrompu"}">${esc(VERDICT[r.verdict] || r.verdict)}</span>
      <span class="aide">${(r.relecture || 1) > 1 ? `relecture n° ${r.relecture} · ` : ""}relu ${heure(r.date)} · ${duree(r.duree_s)} · ${nf.format(r.cout_usd)} $ · ${esc(r.modeles.angles)}, vérificateur ${esc(r.modeles.verificateur)}</span></div>
    <p class="synthese">${esc(r.synthese)}</p>
    ${r.tri_archive ? `<div class="alerte-b alerte"><b>⚠ Constats à trier de nouveau</b><span>Ce ticket a été relu une nouvelle fois. Vos ${r.tri_archive} décision(s) portaient sur la relecture précédente, gardée dans .chaine/revues/archives/. Les constats ci-dessous sont nouveaux.</span></div>` : ""}
    <div class="angles">${r.angles.map(a => `<div class="angle"><b>${esc(NOMS_A[a.agent])}</b> <span class="statut ${a.verdict === "ok" ? "reussi" : a.verdict === "ko" || a.verdict === "erreur" ? "echec" : "interrompu"}">${esc(VERDICT[a.verdict] || a.verdict)}</span><div>${esc(a.resume || a.erreur || "")}</div></div>`).join("")}</div>
    <div class="defile"><table class="constats"><thead><tr><th>Statut</th><th>Gravité</th><th>Agent</th><th>Où</th><th>Constat</th><th>Votre décision</th></tr></thead><tbody>
    ${r.constats.slice().sort((a,b) => (ordreS[a.statut] ?? 1) - (ordreS[b.statut] ?? 1) || ordreG[a.gravite] - ordreG[b.gravite]).map(c => `<tr class="st-${esc(c.statut)}">
      <td><span class="chip st ${esc(c.statut)}">${esc({confirme:"confirmé", rejete:"rejeté", incertain:"incertain"}[c.statut] || "")}</span></td>
      <td><span class="grav ${esc(c.gravite)}">${esc(c.gravite)}</span></td><td>${esc(NOMS_A[c.agent])}</td>
      <td class="mono">${esc(c.fichier)}${c.ligne ? ":" + c.ligne : ""}</td>
      <td class="txt"><details><summary>${esc(c.titre)}</summary><div class="aide">${esc(c.detail)}</div>
        ${c.attendu ? `<div><b>Attendu</b> : ${esc(c.attendu)}</div>` : ""}<div><b>Règle</b> : ${esc(c.regle)}</div>
        <div><b>Vérificateur</b> : ${esc(c.raison || "")}</div></details></td><td>${triHtml(c)}</td></tr>`).join("") || `<tr><td colspan="6" class="vide">Aucun constat.</td></tr>`}
    </tbody></table></div>
    ${r.angles.some(a => (a.points_forts || []).length) ? `<div class="aide forts">Points forts : ${esc(r.angles.flatMap(a => a.points_forts || []).slice(0,6).join(" · "))}</div>` : ""}
  </div>`).join(""));
}
function rendre(){ if (!etat) return; pastilles(); kpis(); alertes(); if (!rj.actif) graphe(); pensee(); directMaj(); penseesMaj(); rjListe(); pilotageEtat();
  if (onglet === "runs"){ runs(); comparer(); } if (onglet === "tickets") tickets(); if (onglet === "revues") revues();
  if (VUES[onglet]) VUES[onglet](); }
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
/* ====================================================================
   Suite : alertes, rejeu, pensée en direct, comparaison, fiche de ticket,
   tri des constats, couverture, mesures, carte du code, garde, pilotage
   ==================================================================== */
let JETON = "__JETON__";
const tampon = {};
async function charger(cle, url, ms = 15000, force = false){
  const c = tampon[cle]; if (!force && c && Date.now() - c.t < ms) return c.v;
  const v = await api(url).catch(() => null); if (v) tampon[cle] = {t: Date.now(), v}; return v || (c && c.v);
}
function poser(sel, html){ const el = typeof sel === "string" ? $(sel) : sel; if (!el || el._h === html) return false; el._h = html; el.innerHTML = html; return true; }
/* Un onglet ouvert avant un redémarrage du suivi garde l'ancien jeton : sur un refus,
   la page reprend le jeton du serveur et renvoie la requête une fois. */
async function poster(url, corps, reessai = true){
  let r;
  try { r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json", "X-Suivi-Jeton":JETON}, body:JSON.stringify(corps)}); }
  catch { return {ok:false, message:"serveur de suivi injoignable"}; }
  if (r.status === 403 && reessai){
    const j = await api("/api/jeton").catch(() => null);
    if (j && j.jeton && j.jeton !== JETON){ JETON = j.jeton; return poster(url, corps, false); }
  }
  return r.json().catch(() => ({ok:false, message:"réponse illisible"}));
}
function avis(texte, ko = true){
  let n = $("#avis"); if (!n){ n = document.createElement("div"); n.id = "avis"; n.setAttribute("role", "status"); document.body.append(n); }
  n.textContent = texte; n.className = ko ? "ko" : ""; n.hidden = false;
  clearTimeout(avis.t); avis.t = setTimeout(() => { n.hidden = true; }, 6000);
}
const tuiles = L => L.map(([v,l]) => `<div class="kpi"><b>${v === "" || v == null ? "–" : v}</b><span>${esc(l)}</span></div>`).join("");
const STATUT_C = {confirme:"confirmé", rejete:"rejeté", incertain:"incertain"};
const lotDe = id => +String(id || "").slice(1).split("-")[0];

/* ---------- alertes ---------- */
const SIGNE = {critique:"✕", alerte:"⚠", info:"ℹ"};
function alertes(){
  poser("#alertes", (etat.alertes || []).map(a => `<div class="alerte-b ${a.niveau}" role="${a.niveau === "info" ? "status" : "alert"}"><b>${SIGNE[a.niveau]} ${esc(a.titre)}</b><span>${esc(a.detail)}</span></div>`).join(""));
}

/* ---------- pensée de Qwen au fil de l'eau ---------- */
let pz = {run:null, n:0, charge:false, vu:0};
function ajouterPensee(t, anime){
  const a = document.createElement("article"); a.className = "pz" + (anime ? " neuf" : "");
  a.innerHTML = `<header><b>Tour ${t.n}</b><span>${heure(t.t)}</span><span>${nf.format(t.tokens)} tokens de raisonnement</span>${t.outils.length ? `<span>puis ${esc(t.outils.join(", ").slice(0,140))}</span>` : ""}</header>`;
  const blocs = [[t.pensee, "pense"], [t.texte, "dit"]].filter(b => b[0]);
  const ps = blocs.map(([txt, cls]) => { const p = document.createElement("p"); p.className = cls; a.appendChild(p); return [p, txt]; });
  const boite = $("#pensees"); boite.prepend(a); while (boite.children.length > 30) boite.lastChild.remove();
  if (!anime || matchMedia("(prefers-reduced-motion:reduce)").matches){ ps.forEach(([p, txt]) => p.textContent = txt); return; }
  (async () => { for (const [p, txt] of ps){ p.classList.add("curseur"); const pas = Math.max(3, Math.ceil(txt.length / 240));
      for (let i = 0; i <= txt.length; i += pas){ p.textContent = txt.slice(0, i); await new Promise(r => setTimeout(r, 16)); }
      p.textContent = txt; p.classList.remove("curseur"); } })();
}
/* Flux mot à mot : le serveur donne la fin de la pensée en cours, la page la déroule entre deux relevés. */
const pzd = {total:0, montre:0, queue:"", texte:"", flux:false};
function directMaj(){
  const ec = etat.en_cours, el = $("#pz-direct"), q = ec ? ec.pensee_partielle || "" : "", dit = ec ? ec.texte_partiel || "" : "";
  if (!q && !dit){ el.hidden = true; pzd.total = pzd.montre = 0; pzd.queue = ""; return; }
  el.hidden = false; pzd.flux = true; if (ec.pensee_total < pzd.montre) pzd.montre = 0;
  pzd.total = ec.pensee_total; pzd.queue = q; pzd.texte = dit;
  $("#pz-direct-t").textContent = `tour ${ec.tour}, ${nf.format(ec.tokens_tour)} tokens de raisonnement`;
}
setInterval(() => { const el = $("#pz-direct"); if (el.hidden) return;
  const reste = pzd.total - pzd.montre; if (reste > 0) pzd.montre += Math.max(1, Math.ceil(reste / 30));
  const p = $("#pz-direct-p"), txt = pzd.queue.slice(0, Math.max(0, pzd.queue.length - (pzd.total - pzd.montre)));
  if (p.textContent !== txt){ p.textContent = txt; p.scrollTop = p.scrollHeight; }
  if ($("#pz-direct-dit").textContent !== pzd.texte) $("#pz-direct-dit").textContent = pzd.texte; }, 60);
async function penseesMaj(){
  const ec = etat.en_cours, id = ec ? ec.run : (etat.runs.length ? etat.runs[etat.runs.length - 1].id : null);
  if (!id || pz.occupe) return;
  if (pz.run !== id){ pz = {run:id, n:0, charge:false, vu:0}; $("#pensees").innerHTML = ""; pzd.flux = false; }
  if (pz.charge && (!ec || ec.nb_tours <= pz.vu)) return;
  pz.occupe = true;
  const d = await api(`/api/pensees?id=${encodeURIComponent(id)}&depuis=${pz.n}`).catch(() => null);
  pz.occupe = false; if (!d || pz.run !== id) return;
  const premier = !pz.charge; pz.charge = true; pz.vu = d.nb_tours;
  $("#pensees-t").textContent = ec ? `session ${id}, ${pzd.flux ? "mot à mot" : "dès qu'un tour se termine"}` : `dernière session, ${id}`;
  for (const t of (premier ? d.tours.slice(-12) : d.tours)){ pz.n = Math.max(pz.n, t.n); ajouterPensee(t, !premier && !pzd.flux); }
  if (premier && d.tours.length) pz.n = d.tours[d.tours.length - 1].n;
}

/* ---------- rejeu d'une session ---------- */
const rj = {actif:false, joue:false, ev:[], t0:0, t1:1, pos:0, i:0, compte:{}, dernier:0, scenes:0, id:null, der:null};
function rjListe(){
  const opts = etat.runs.slice().reverse().map(r => `<option value="${esc(r.id)}">${esc(r.id)} · ${duree(r.duree_s)}</option>`).join("");
  if (poser("#rj-run", opts) && rj.id) $("#rj-run").value = rj.id;
  $("#rj-jouer").disabled = !etat.runs.length;
}
function rjCompter(){ rj.compte = {lecture:0, recherche:0, ecriture:0, shell:0}; rj.der = null;
  for (const e of rj.ev.slice(0, rj.i)){ if (e.evt === "outil") rj.compte[FAM[e.nom] || "shell"]++; rj.der = e; } }
function rjAfficher(){
  $("#rj-curseur").value = Math.round(1000 * (rj.pos - rj.t0) / (rj.t1 - rj.t0));
  $("#rj-horloge").textContent = `${heure(rj.pos * 1000)} · ${duree(rj.pos - rj.t0)} sur ${duree(rj.t1 - rj.t0)}`;
  $("#rj-jouer").textContent = rj.joue ? "⏸ Pause" : "▶ Rejouer";
  $("#rejeu").classList.toggle("actif", rj.actif); $("#rj-direct").hidden = !rj.actif;
  if (!rj.actif) { $("#rj-evenement").textContent = ""; return; }
  for (const k in rj.compte) $("#b-" + k).textContent = rj.compte[k] ? "×" + rj.compte[k] : "";
  $("#b-exec").textContent = "rejeu de " + rj.id; $("#b-boucle").textContent = "rejeu";
  $("#rj-evenement").textContent = rj.der ? `${heure(rj.der.t)} · ${LIBELLES[rj.der.evt] || rj.der.evt} · ${court(detailEv(rj.der)).slice(0, 200)}` : "Début de la session.";
}
function rjAller(frac){ rj.pos = rj.t0 + frac * (rj.t1 - rj.t0); rj.i = rj.ev.findIndex(e => e.s > rj.pos); if (rj.i < 0) rj.i = rj.ev.length; rjCompter(); rjAfficher(); }
async function rjCharger(id){
  const d = await api("/api/rejeu?id=" + encodeURIComponent(id)).catch(() => null); if (!d) return false;
  rj.id = id; rj.ev = d.evenements.map(e => ({...e, s: new Date(e.t).getTime() / 1000}));
  rj.t0 = rj.ev[0].s; rj.t1 = rj.ev[rj.ev.length - 1].s + 4; rj.actif = true; rjAller(0); return true;
}
function rjPas(t){
  if (!rj.joue) return;
  rj.pos += Math.min(0.25, (t - rj.dernier) / 1000) * +$("#rj-vitesse").value; rj.dernier = t;
  while (rj.i < rj.ev.length && rj.ev[rj.i].s <= rj.pos){ const e = rj.ev[rj.i++]; rj.der = e;
    if (e.evt === "outil") rj.compte[FAM[e.nom] || "shell"]++;
    if (rj.scenes < 5){ rj.scenes++; scene(e).finally(() => rj.scenes--); } }
  if (rj.pos >= rj.t1){ rj.pos = rj.t1; rj.joue = false; }
  rjAfficher(); if (rj.joue) requestAnimationFrame(rjPas);
}
$("#rj-jouer").addEventListener("click", async () => {
  if (rj.joue){ rj.joue = false; rjAfficher(); return; }
  const id = $("#rj-run").value; if (!id) return;
  if (!rj.actif || rj.id !== id || rj.pos >= rj.t1){ if (!await rjCharger(id)) return; }
  rj.joue = true; rj.dernier = performance.now(); rjAfficher(); requestAnimationFrame(rjPas);
});
$("#rj-curseur").addEventListener("input", async e => { if (!rj.actif && !await rjCharger($("#rj-run").value)) return; rjAller(e.target.value / 1000); });
$("#rj-run").addEventListener("change", () => { rj.joue = false; rj.actif = false; rj.id = $("#rj-run").value; rjAfficher(); rendre(); });
$("#rj-direct").addEventListener("click", () => { rj.joue = false; rj.actif = false; rjAfficher(); rendre(); });

/* ---------- comparaison de runs ---------- */
const cmp = new Set();
const METRIQUES = [["duree_s","Durée", duree], ["nb_tours","Tours de Qwen"], ["nb_outils","Appels d'outils"], ["nb_erreurs","Erreurs d'outil"],
  ["nb_refus","Refus de permission"], ["tokens_raisonnement","Tokens de raisonnement"], ["debit","Tokens par seconde, en raisonnement"], ["tokens_entree","Tokens lus par le modèle"]];
const fmtM = (m, v) => v == null ? "–" : m[2] ? m[2](v) : nf.format(v);
function colonnes(m, R){
  const W = 260, H = 118, g = 8, bas = 20, ih = H - bas - 14, max = Math.max(1, ...R.map(r => r[m[0]] || 0)), bw = (W - 2*g) / R.length, larg = Math.max(3, Math.min(34, bw - 4));
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(m[1])} de chaque session, dans l'ordre du temps">`;
  R.forEach((r, i) => { const v = r[m[0]] || 0, h = Math.max(v ? 2 : 0, ih * v / max), x = g + i*bw + (bw - larg)/2, y = 14 + ih - h, rr = Math.min(4, larg/2, h);
    const cls = cmp.size ? (cmp.has(r.id) ? "choisi" : "terne") : "";
    s += `<path class="col ${cls}" d="M${x},${14+ih} V${y+rr} Q${x},${y} ${x+rr},${y} H${x+larg-rr} Q${x+larg},${y} ${x+larg},${y+rr} V${14+ih} Z"/>`;
    if (R.length <= 8) s += `<text x="${x + larg/2}" y="${y - 3}" text-anchor="middle">${esc(fmtM(m, r[m[0]]))}</text>`;
    if (R.length <= 14) s += `<text x="${x + larg/2}" y="${H - 6}" text-anchor="middle">${esc(r.ticket.slice(1))}${r.tentative > 1 ? "·" + r.tentative : ""}</text>`;
    s += `<rect data-m="${esc(m[0])}" data-r="${i}" x="${g + i*bw}" y="0" width="${bw}" height="${H}" fill="transparent"/>`; });
  return s + `<line class="axe" x1="${g}" x2="${W-g}" y1="${14+ih}" y2="${14+ih}"/></svg>`;
}
function comparer(){
  const R = etat.runs; if (!R.length){ poser("#comparaison", ""); return; }
  const C = R.filter(r => cmp.has(r.id));
  let h = `<div class="carte"><div class="carte-t">Comparer les sessions <span class="aide">une colonne par session, dans l'ordre du temps. Cochez des lignes du tableau pour les mettre en avant et les voir côte à côte.</span></div>
    <div class="multiples">${METRIQUES.map(m => `<div class="multiple"><h4>${esc(m[1])}</h4>${colonnes(m, R)}</div>`).join("")}</div>`;
  if (C.length >= 2) h += `<div class="defile"><table class="cote"><thead><tr><th>Mesure</th>${C.map(r => `<th class="n">${esc(r.id)}</th>`).join("")}</tr></thead><tbody>
    ${METRIQUES.map(m => `<tr><td>${esc(m[1])}</td>${C.map(r => `<td class="n">${esc(fmtM(m, r[m[0]]))}</td>`).join("")}</tr>`).join("")}
    <tr><td>Tests de contrat réussis</td>${C.map(r => `<td class="n">${r.contrats ? r.contrats.reussis : "–"}</td>`).join("")}</tr>
    <tr><td>Verdict</td>${C.map(r => `<td class="n">${esc(STATUTS[r.statut] || r.statut)}</td>`).join("")}</tr></tbody></table></div>`;
  h += `</div>`;
  if (!poser("#comparaison", h)) return;
  $("#comparaison").querySelectorAll("rect[data-m]").forEach(n => { const r = R[+n.dataset.r], m = METRIQUES.find(x => x[0] === n.dataset.m);
    n.addEventListener("mousemove", e => montrer(e, `${r.id}\n${m[1]} : ${fmtM(m, r[m[0]])}\n${STATUTS[r.statut] || r.statut}`)); n.addEventListener("mouseleave", cacher); });
}

/* ---------- tri des constats ---------- */
const TRI = [["corriger","À corriger"], ["accepte","Accepté"], ["hors_sujet","Hors sujet"]];
function triHtml(c){ if (c.statut !== "confirme" || !c.cle) return ""; const d = c.tri && c.tri.decision;
  return `<span class="tri" data-cle="${esc(c.cle)}" role="group" aria-label="Votre décision sur ce constat">${TRI.map(([k,l]) => `<button type="button" aria-pressed="${d === k}" data-d="${k}">${l}</button>`).join("")}</span>`; }
document.addEventListener("click", async e => {
  const b = e.target.closest(".tri button"); if (b){ e.preventDefault(); e.stopPropagation();
    const dejala = b.getAttribute("aria-pressed") === "true";
    const r = await poster("/api/tri", {cle: b.parentElement.dataset.cle, decision: dejala ? "" : b.dataset.d});
    if (r.ok){ b.parentElement.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", !dejala && x === b)); delete tampon["fiche:" + ficheSel]; revuesSig = ""; rafraichir(); }
    else avis("Décision non enregistrée : " + (r.message || "erreur inconnue") + ". Rechargez la page si cela se répète.");
    return; }
  const t = e.target.closest("[data-fiche]"); if (t){ e.preventDefault(); ouvrirFiche(t.dataset.fiche); return; }
  const r = e.target.closest("[data-aller-run]"); if (r){ e.preventDefault(); runSel = r.dataset.allerRun; document.querySelector('[data-onglet="runs"]').click(); detail(); }
});
function constatHtml(c){
  return `<div class="constat g-${esc(c.gravite)} st-${esc(c.statut)}"><div class="constat-t"><span class="grav ${esc(c.gravite)}">${esc(c.gravite)}</span>
    <span class="chip st ${esc(c.statut)}">${STATUT_C[c.statut] || ""}</span><b>${esc(c.titre)}</b><span class="aide">${esc(NOMS_A[c.agent] || "")}${c.ligne ? " · ligne " + c.ligne : ""}</span>${triHtml(c)}</div>
    <div>${esc(c.detail)}</div>${c.attendu ? `<div><b>Attendu</b> : ${esc(c.attendu)}</div>` : ""}
    <div class="aide">Règle : ${esc(c.regle)}${c.raison ? " · Vérificateur : " + esc(c.raison) : ""}</div></div>`;
}

/* ---------- fiche d'un ticket ---------- */
let ficheSel = null, ficheRejetes = false;
const PUCE = {reussi:["✓","réussi"], echec:["✕","en échec"], sans_test:["!","sans test de contrat"], non_mesure:["?","non mesuré"], tests_a_ecrire:["·","tests à écrire avant la vague"]};
function ouvrirFiche(id){ ficheSel = id; if (onglet !== "tickets") document.querySelector('[data-onglet="tickets"]').click(); else { history.replaceState(null, "", "#ticket=" + id); tickets(); } scrollTo({top:0}); }
function criteresHtml(cv){
  return cv.criteres.map(c => `<div class="crit"><span class="puce ${c.etat}" title="${PUCE[c.etat][1]}">${PUCE[c.etat][0]}</span><div><b>${c.n}.</b> ${esc(c.texte)}
    <div class="tests">${c.tests.length ? c.tests.map(t => `<code>${esc(t.nom)}</code>${t.cas > 1 ? " ×" + t.cas : ""}`).join(" · ") : PUCE[c.etat][1]}</div></div></div>`).join("")
    + (cv.autres_tests.length ? `<div class="crit"><span class="puce ${cv.autres_tests.some(t => t.etat === "echec") ? "echec" : cv.autres_tests.every(t => t.etat === "reussi") ? "reussi" : "non_mesure"}">+</span><div><b>Précisions de la spécification</b>
      <div class="tests">${cv.autres_tests.map(t => `<code>${esc(t.nom)}</code>`).join(" · ")}</div></div></div>` : "");
}
function diffHtml(f, constats){
  const lignes = new Set(); f.blocs.forEach(b => b.lignes.forEach(l => { if (l.b) lignes.add(l.b); }));
  const par = {}, hors = [];
  constats.forEach(c => { if (c.ligne && lignes.has(+c.ligne)) (par[c.ligne] = par[c.ligne] || []).push(c); else hors.push(c); });
  let h = hors.map(constatHtml).join("");
  if (f.binaire) return h + `<div class="vide">Fichier binaire.</div>`;
  if (!f.blocs.length) return h + `<div class="vide">Fichier vide.</div>`;
  h += `<div class="diff"><table><tbody>` + f.blocs.map(b => `<tr class="entete"><td class="no"></td><td class="no"></td><td>@@ ${esc(b.entete)}</td></tr>` + b.lignes.map(l =>
    `<tr class="${l.g === "+" ? "plus" : l.g === "-" ? "moins" : ""}"><td class="no">${l.a ?? ""}</td><td class="no">${l.b ?? ""}</td><td>${l.g}${esc(l.t)}</td></tr>`
    + (l.b && par[l.b] ? `<tr class="note"><td colspan="3">${par[l.b].map(constatHtml).join("")}</td></tr>` : "")).join("")).join("") + `</tbody></table></div>`;
  return h + (f.tronque ? `<div class="vide">Diff coupé après 1 200 lignes.</div>` : "");
}
async function fiche(force){
  const d = await charger("fiche:" + ficheSel, "/api/ticket?id=" + encodeURIComponent(ficheSel), 8000, force); if (!d){ poser("#fiche", `<div class="carte vide">Ticket introuvable.</div>`); return; }
  const t = d.ticket, l = d.ligne, rv = d.revue, cv = d.couverture, v = d.verif;
  const tous = rv ? rv.constats : [], C = tous.filter(c => ficheRejetes || c.statut !== "rejete");
  const conf = tous.filter(c => c.statut === "confirme"), aTrier = conf.filter(c => !c.tri).length;
  const pire = ["bloquant","majeur","mineur","remarque"].find(g => conf.some(c => c.gravite === g));
  const duDiff = new Set(d.diff.map(f => f.chemin)), ailleurs = C.filter(c => !duDiff.has(c.fichier));
  const nonCouverts = cv.criteres.filter(c => c.etat === "sans_test").length, echecs = cv.criteres.filter(c => c.etat === "echec").length;
  const run = d.runs[d.runs.length - 1], enCours = etat.en_cours && etat.en_cours.ticket === ficheSel;
  const ecrits = new Set(d.diff.map(f => f.chemin));
  const etapes = [
    ["Claude, avant la vague", "Spécification", `${cv.criteres.length} critères, ${cv.nb_cas} tests de contrat`, !cv.nb_tests ? "" : echecs ? "critique" : nonCouverts ? "alerte" : "bon"],
    ["Qwen", "Développement", d.runs.length ? `${d.runs.length} session(s), ${duree(d.runs.reduce((a, r) => a + r.duree_s, 0))}` : "pas encore commencé", enCours ? "vif" : l.etat === "fait" ? "bon" : l.etat === "bloqué" ? "critique" : ""],
    ["Boucle", "Vérification", v ? (v.ok ? `make check passe, ${v.contrats ? v.contrats.reussis : 0} contrats réussis` : v.motif) : "en attente", v ? (v.ok ? "bon" : "critique") : ""],
    ["Boucle", "Commit", d.commit ? `${d.commit.empreinte}, ${heure(d.commit.date).slice(0,5)}` : "aucun", d.commit ? "bon" : ""],
    ["Claude, six agents", "Relecture", rv ? `${VERDICT[rv.verdict] || rv.verdict}, ${conf.length} constat(s) confirmé(s)${aTrier ? `, ${aTrier} à trier` : ""}` : "pas encore relu", rv ? (COUL_V[rv.verdict] || "") : ""],
  ];
  const h = `
  <div class="fiche-tete"><button class="bouton" id="fiche-retour">← Tous les tickets</button><h2><span class="mono">${esc(l.id)}</span> · ${esc(l.titre)}</h2>
    <span class="statut ${enCours ? "en_cours" : l.etat === "fait" ? "reussi" : l.etat === "bloqué" ? "bloque" : ""}">${esc(enCours ? "en cours" : l.etat)}</span>
    <span class="aide">dépend de : ${esc(l.depend)}</span></div>
  <div class="chemin">${etapes.map(([qui, nom, txt, cls]) => `<div class="etape ${cls}"><div class="qui">${esc(qui)}</div><b>${esc(nom)}</b><span>${esc(txt)}</span></div>`).join("")}</div>
  <div class="grille2">
    <div class="carte"><div class="carte-t">Ce que le ticket demande <span class="aide">tickets/${esc(l.id)}.md</span></div>
      <p class="synthese">${esc(t.objectif)}</p>
      <dl class="params"><dt>Sections à lire</dt><dd>${esc(t.specs.replace(/`/g, ""))}</dd>
        <dt>Fichiers attendus</dt><dd class="fichiers-l">${t.fichiers.map(f => `<span class="mini ${ecrits.has(f) ? "bon" : ""}" title="${ecrits.has(f) ? "présent dans le commit" : "absent du commit"}">${esc(f)}</span>`).join("") || "aucun"}</dd>
        ${run && (run.hors_liste || []).length ? `<dt>Hors de la liste</dt><dd class="fichiers-l">${run.hors_liste.map(f => `<span class="mini alerte">${esc(f)}</span>`).join("")}</dd>` : ""}
        ${t.hors_perimetre ? `<dt>Hors périmètre</dt><dd>${esc(t.hors_perimetre)}</dd>` : ""}</dl></div>
    <div class="carte"><div class="carte-t">Critères d'acceptation et tests de contrat <span class="aide">${cv.nb_tests ? `${cv.nb_tests} tests, ${cv.nb_cas} cas` : "tests écrits avant la vague du ticket"}</span></div>${criteresHtml(cv) || `<div class="vide">Aucun critère lu dans le ticket.</div>`}</div>
  </div>
  <div class="grille2">
    <div class="carte"><div class="carte-t">Sessions de Qwen <span class="aide">cliquez pour le détail tour par tour</span></div>
      ${d.runs.length ? `<div class="defile"><table><thead><tr><th>Run</th><th>Statut</th><th class="n">Durée</th><th class="n">Tours</th><th class="n">Erreurs</th><th class="n">Refus</th></tr></thead><tbody>${d.runs.map(r =>
        `<tr data-aller-run="${esc(r.id)}" tabindex="0"><td class="mono">${esc(r.id)}</td><td><span class="statut ${r.statut}">${STATUTS[r.statut] || r.statut}</span></td><td class="n">${duree(r.duree_s)}</td><td class="n">${r.nb_tours}</td><td class="n">${r.nb_erreurs}</td><td class="n">${r.nb_refus}</td></tr>`).join("")}</tbody></table></div>` : `<div class="vide">Aucune session pour ce ticket.</div>`}
      ${d.note ? `<details><summary>Note de l'exécutant, tickets/notes/${esc(l.id)}.md</summary><pre class="md">${esc(d.note)}</pre></details>` : ""}</div>
    <div class="carte"><div class="carte-t">Vérification par la boucle <span class="aide">fin de la sortie de make check</span></div>
      ${d.sortie_check ? `<pre class="md">${esc(d.sortie_check)}</pre>` : `<div class="vide">Pas encore de vérification.</div>`}</div>
  </div>
  ${rv ? `<div class="carte"><div class="carte-t">Relecture par les six agents <span class="statut ${rv.verdict === "ok" ? "reussi" : rv.verdict === "ko" ? "echec" : "interrompu"}">${esc(VERDICT[rv.verdict] || rv.verdict)}</span>
      <span class="aide">${heure(rv.date)} · ${duree(rv.duree_s)} · ${nf.format(rv.cout_usd)} $${pire ? " · constat le plus grave : " + pire : ""}</span></div>
      <p class="synthese">${esc(rv.synthese)}</p>
      <div class="angles">${rv.angles.map(a => `<div class="angle"><b>${esc(NOMS_A[a.agent])}</b> <span class="statut ${a.verdict === "ok" ? "reussi" : a.verdict === "ko" || a.verdict === "erreur" ? "echec" : "interrompu"}">${esc(VERDICT[a.verdict] || a.verdict)}</span><div>${esc(a.resume || a.erreur || "")}</div></div>`).join("")}</div>
      ${ailleurs.length ? `<div class="aide">Constats qui ne visent pas un fichier du commit :</div>${ailleurs.map(constatHtml).join("")}` : ""}</div>` : ""}
  <div class="carte"><div class="carte-t">Code écrit par Qwen${d.commit ? `, commit <code>${esc(d.commit.empreinte)}</code>` : ""} <span class="aide">${d.diff.length} fichier(s). Les constats des relecteurs sont posés sur la ligne visée.</span>
      <label class="aide"><input type="checkbox" id="fiche-rejetes" ${ficheRejetes ? "checked" : ""}> montrer aussi les constats rejetés</label></div>
    ${d.diff.map(f => { const cs = C.filter(c => c.fichier === f.chemin), nb = cs.filter(c => c.statut === "confirme").length;
      return `<details class="fichier-d" ${cs.length ? "open" : ""}><summary><code>${esc(f.chemin)}</code><span class="aide">${esc(f.statut)}</span><span class="plus">+${f.ajouts}</span><span class="moins">−${f.retraits}</span>
        ${nb ? `<span class="mini alerte">${nb} constat(s) confirmé(s)</span>` : ""}${(run && run.hors_liste || []).includes(f.chemin) ? `<span class="mini alerte">hors de la liste du ticket</span>` : ""}</summary>${diffHtml(f, cs)}</details>`; }).join("") || `<div class="vide">Pas encore de commit pour ce ticket.</div>`}
  </div>`;
  if (!poser("#fiche", h)) return;
  $("#fiche-retour").addEventListener("click", () => { ficheSel = null; history.replaceState(null, "", "#tickets"); tickets(); });
  const cr = $("#fiche-rejetes"); if (cr) cr.addEventListener("change", () => { ficheRejetes = cr.checked; fiche(); });
}

/* ---------- couverture ---------- */
let couvLot = null;
async function vueCouverture(){
  const d = await charger("couverture", "/api/couverture"); if (!d) return;
  const c = d.compte, total = Object.values(c).reduce((a, b) => a + b, 0), ecrits = total - (c.tests_a_ecrire || 0);
  poser("#kpis-c", tuiles([[`${c.reussi || 0}<small> / ${ecrits}</small>`, "critères couverts et réussis"], [c.sans_test || 0, "critères sans test de contrat"], [c.echec || 0, "critères en échec"],
    [c.non_mesure || 0, "critères non mesurés"], [c.tests_a_ecrire || 0, "critères des vagues à venir"]]));
  const lots = [...new Set(d.tickets.map(t => t.id.split("-")[0]))];
  if (poser("#filtres-c", [`<button aria-pressed="${couvLot === null}" data-lot="">Tous</button>`].concat(lots.map(l => `<button aria-pressed="${couvLot === l}" data-lot="${l}">Lot ${l.slice(1)}</button>`)).join("")))
    $("#filtres-c").querySelectorAll("button").forEach(b => b.addEventListener("click", () => { couvLot = b.dataset.lot || null; vueCouverture(); }));
  const T = d.tickets.filter(t => !couvLot || t.id.startsWith(couvLot + "-"));
  const h = `<div class="carte"><div class="carte-t">Matrice des critères <span class="aide">une ligne par ticket, une case par critère « Terminé quand ». Cliquez un ticket pour sa fiche.</span></div>
    <div class="matrice">${T.map(t => `<div class="rang"><a href="#ticket=${esc(t.id)}" data-fiche="${esc(t.id)}">${esc(t.id)}</a><div class="cases">${t.criteres.map(k =>
      `<span class="case ${k.etat}" title="${esc(`${t.id}, critère ${k.n} : ${PUCE[k.etat][1]}\n${k.texte}`)}">${k.etat === "reussi" ? k.n : PUCE[k.etat][0]}</span>`).join("")}</div></div>`).join("")}</div>
    <div class="legende"><span><span class="case reussi">1</span> couvert et réussi</span><span><span class="case sans_test">!</span> sans test de contrat, à vérifier à la main</span><span><span class="case echec">✕</span> en échec</span>
      <span><span class="case non_mesure">?</span> non mesuré</span><span><span class="case">·</span> tests à écrire avant la vague du ticket</span></div>
    <div class="aide forts">${d.junit.length ? "Résultat par test lu dans " + esc(d.junit.join(", ")) + "." : "Aucun rapport par test : l'état vient du compte de tests réussis du ticket, noté par la boucle."}</div></div>
  ${T.filter(t => t.nb_tests).map(t => `<div class="carte"><div class="carte-t"><a href="#ticket=${esc(t.id)}" data-fiche="${esc(t.id)}" class="mono">${esc(t.id)}</a> ${esc(t.titre)} <span class="aide">${t.nb_tests} tests, ${t.nb_cas} cas · ticket ${esc(t.etat)}</span></div>${criteresHtml(t)}</div>`).join("")}`;
  poser("#couverture", h);
}

/* ---------- mesures ---------- */
let debitRun = null;
const NATURES = [["raisonne","Qwen raisonne"], ["outils","les outils s'exécutent"], ["compaction","le contexte est résumé"], ["reste","le modèle relit le contexte et écrit sa réponse"]];
function partageHtml(R){
  const max = Math.max(1, ...R.map(r => r.duree_s));
  return `<div class="partage">${R.map(r => { const t = r.temps || {}, tot = Math.max(1, r.duree_s);
    return `<div class="rg"><span class="mono">${esc(r.id)}</span><div class="piste" style="width:${(100 * r.duree_s / max).toFixed(1)}%">${NATURES.map(([k, nom]) => { const v = t[k + "_s"] || 0, p = 100 * v / tot;
      return v ? `<div class="seg ${k}" style="width:${p.toFixed(2)}%" title="${esc(`${r.id} : ${nom}, ${duree(v)}, ${p.toFixed(0)} % de la session`)}">${p >= 9 ? p.toFixed(0) + " %" : ""}</div>` : ""; }).join("")}</div><span>${duree(r.duree_s)}</span></div>`; }).join("")}</div>
    <div class="legende">${NATURES.map(([k, nom]) => `<span><i class="seg ${k}"></i>${nom}</span>`).join("")}</div>`;
}
function debitHtml(S){
  const P = S.tours.filter(t => t.debit != null); if (P.length < 2) return `<div class="vide">Pas assez de tours mesurés.</div>`;
  const W = 640, H = 220, g = 40, b = 26, iw = W - g - 12, ih = H - b - 12, n = S.tours.length, max = Math.ceil(Math.max(...P.map(t => t.debit)) / 5) * 5;
  const X = i => g + iw * (i - 1) / Math.max(1, n - 1), Y = v => 12 + ih - ih * v / max, moy = P.reduce((a, t) => a + t.debit, 0) / P.length;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Tokens de raisonnement par seconde, tour par tour">`;
  for (let k = 0; k <= 4; k++){ const v = max * k / 4; s += `<line class="grille" x1="${g}" x2="${W-12}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${g-6}" y="${Y(v)+4}" text-anchor="end">${nf.format(v)}</text>`; }
  s += `<line class="moy" x1="${g}" x2="${W-12}" y1="${Y(moy)}" y2="${Y(moy)}"/>`;
  s += `<path class="ligne-d" d="${P.map((t, i) => `${i ? "L" : "M"}${X(t.n).toFixed(1)},${Y(t.debit).toFixed(1)}`).join(" ")}"/>`;
  P.forEach((t, i) => { s += `<circle class="pt" cx="${X(t.n)}" cy="${Y(t.debit)}" r="${P.length > 50 ? 3 : 4}"/><rect data-p="${i}" x="${X(t.n) - 6}" y="12" width="12" height="${ih}" fill="transparent"/>`; });
  const pas = Math.max(1, Math.ceil(n / 12)); for (let i = 1; i <= n; i += pas) s += `<text x="${X(i)}" y="${H-6}" text-anchor="middle">${i}</text>`;
  return s + `<line class="axe" x1="${g}" x2="${W-12}" y1="${12+ih}" y2="${12+ih}"/></svg>
    <div class="legende"><span>Ligne pointillée : moyenne de la session, ${moy.toFixed(1).replace(".", ",")} tokens par seconde sur ${P.length} tours mesurés. Les tours de moins de trois secondes ne sont pas tracés.</span></div>`;
}
async function vueMesures(){
  const [R, D] = await Promise.all([charger("rapport", "/api/rapport"), charger("debit", "/api/debit")]); if (!R || !D) return;
  const m = R.mesures, pj = D.projection;
  poser("#kpis-m", tuiles([[`${m.sans_aide}<small> / ${m.tickets}</small>`, "tickets terminés sans aide"], [m.bloques, "tickets bloqués"], [`${m.interventions_thomas}<small> / 2 max</small>`, "interventions de Thomas"],
    [m.interventions_relecteur, "réglages du relecteur"], [m.duree_s != null ? duree(m.duree_s) : duree(m.duree_sessions_s), m.duree_s != null ? "durée de la vague" : "durée des sessions, vague en cours"],
    [`${nf.format(m.cout_revues_usd)} $`, "coût des relecteurs"]]));
  if (!debitRun || !D.series[debitRun]) debitRun = D.runs.length ? D.runs[D.runs.length - 1].id : null;
  const ag = ["conformite","principes","tests","code","conduite"];
  const h = `
  <div class="carte"><div class="carte-t">Rapport de la vague ${m.vague}, prérempli <span class="aide">${R.rapport_existe ? `revues/vague-${m.vague}.md existe déjà.` : "le verdict et le jugement de chaque ticket restent au relecteur."}</span>
      <button class="bouton" id="copier-md">Copier le Markdown</button></div>
    <div class="defile"><table class="simple"><thead><tr><th>Ticket</th><th>État</th><th class="n">Tentatives</th><th class="n">Durée</th><th class="n">Tours</th><th class="n">Erreurs</th><th class="n">Refus</th><th class="n">Contrats</th><th>Relecture</th><th>Votre tri</th></tr></thead><tbody>
    ${R.tickets.map(t => `<tr><td><a href="#ticket=${esc(t.id)}" data-fiche="${esc(t.id)}" class="mono">${esc(t.id)}</a> ${esc(t.titre)}</td><td><span class="statut ${t.etat === "fait" ? "reussi" : t.etat === "bloqué" ? "bloque" : ""}">${esc(t.etat)}${t.sans_aide ? ", sans aide" : ""}</span></td>
      <td class="n">${t.tentatives}</td><td class="n">${duree(t.duree_s)}</td><td class="n">${t.tours}</td><td class="n">${t.erreurs_outils}</td><td class="n">${t.refus}</td><td class="n">${t.contrats ? t.contrats.reussis : "–"}</td>
      <td>${t.revue ? esc(VERDICT[t.revue] || t.revue) + " · " + (esc(gravTxt(Object.fromEntries(Object.entries(t.constats).filter(x => x[1])))) || "aucun constat") : "pas encore relu"}</td>
      <td>${t.a_trier ? `<span class="mini alerte">${t.a_trier} à trier</span> ` : ""}${t.tri.corriger ? `<span class="mini critique">${t.tri.corriger} à corriger</span> ` : ""}${t.tri.accepte ? `<span class="mini bon">${t.tri.accepte} accepté(s)</span> ` : ""}${t.tri.hors_sujet ? `<span class="mini">${t.tri.hors_sujet} hors sujet</span>` : ""}</td></tr>`).join("")}
    </tbody></table></div>
    <details><summary>Voir le Markdown à coller dans revues/vague-${m.vague}.md</summary><pre class="md" id="md">${esc(R.markdown)}</pre></details>
    ${m.sessions_archivees.length ? `<div class="aide forts">${m.sessions_archivees.length} session(s) interrompue(s) avant cette vague sont archivées dans .chaine/archives/ et ne comptent pas dans ces mesures.</div>` : ""}</div>
  <div class="grille2">
    <div class="carte"><div class="carte-t">Où passe le temps d'une session <span class="aide">la longueur de la barre suit la durée. La part grise est déduite par différence.</span></div>${D.runs.length ? partageHtml(D.runs) : `<div class="vide">Aucune session.</div>`}</div>
    <div class="carte graphique"><div class="carte-t">Débit de Qwen pendant le raisonnement <span class="aide">tokens par seconde, tour par tour</span>
        <select id="debit-run" aria-label="Session">${D.runs.slice().reverse().map(r => `<option value="${esc(r.id)}" ${r.id === debitRun ? "selected" : ""}>${esc(r.id)}</option>`).join("")}</select></div>
      <div id="g-debit">${debitRun ? debitHtml(D.series[debitRun]) : `<div class="vide">Aucune session.</div>`}</div></div>
  </div>
  <div class="grille2">
    <div class="carte"><div class="carte-t">Coût des relecteurs automatiques <span class="aide">en dollars, par ticket et par agent</span></div>
      ${D.couts.length ? `<div class="defile"><table class="simple"><thead><tr><th>Ticket</th>${ag.map(a => `<th class="n">${esc(NOMS_A[a])}</th>`).join("")}<th class="n">Vérificateur</th><th class="n">Total</th><th class="n">Cumul</th></tr></thead><tbody>
        ${D.couts.map(c => `<tr><td class="mono nw">${esc(c.ticket)}</td>${ag.map(a => `<td class="n">${(c.angles[a] || 0).toFixed(2)}</td>`).join("")}<td class="n">${c.verificateur.toFixed(2)}</td>
          <td class="n"><b>${c.total.toFixed(2)}</b></td><td class="n">${c.cumul.toFixed(2)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="vide">Aucune relecture pour l'instant.</div>`}</div>
    <div class="carte"><div class="carte-t">Projection sur le reste du projet <span class="aide">estimation, extrapolée de ${pj.base} ticket(s) fait(s). Les tickets à venir sont plus gros que ceux du lot 0.</span></div>
      <div class="kpis">${tuiles([[pj.restants, "tickets restants"], [pj.duree_par_ticket_s ? duree(pj.duree_par_ticket_s) : "", "par ticket, en moyenne"], [pj.duree_restante_s ? duree(pj.duree_restante_s) : "", "de calcul restant, estimé"],
        [pj.cout_restant_usd != null ? nf.format(pj.cout_restant_usd) + " $" : "", "de relecture restant, estimé"], [pj.debit_moyen ?? "", "tokens par seconde, en moyenne"]])}</div></div>
  </div>`;
  if (!poser("#mesures", h)) return;
  $("#copier-md").addEventListener("click", async () => { const b = $("#copier-md"); try { await navigator.clipboard.writeText(R.markdown); b.textContent = "Copié"; } catch (e) { b.textContent = "Copie impossible, ouvrez le détail"; } setTimeout(() => b.textContent = "Copier le Markdown", 2500); });
  const sel = $("#debit-run"); if (sel) sel.addEventListener("change", () => { debitRun = sel.value; $("#mesures")._h = ""; vueMesures(); });
  const S = debitRun && D.series[debitRun], P = S ? S.tours.filter(t => t.debit != null) : [];
  $("#g-debit").querySelectorAll("rect[data-p]").forEach(n => { const t = P[+n.dataset.p];
    n.addEventListener("mousemove", e => montrer(e, `Tour ${t.n}\n${t.debit} tokens par seconde\n${nf.format(t.tokens)} tokens en ${duree(t.pense_s)}`)); n.addEventListener("mouseleave", cacher); });
}

/* ---------- carte du code ---------- */
let carteMode = "lot";
function paver(items, x, y, w, h){
  const out = []; let reste = items.slice().sort((a, b) => b.v - a.v), S = reste.reduce((a, i) => a + i.v, 0);
  while (reste.length && w > 0.5 && h > 0.5){
    const court = Math.min(w, h), aire = v => v / S * w * h; let rang = [], pire = Infinity;
    while (reste.length){ const essai = rang.concat(reste[0]), s = essai.reduce((a, i) => a + aire(i.v), 0), ep = s / court;
      const p = Math.max(...essai.map(i => { const l = aire(i.v) / ep; return Math.max(ep / l, l / ep); }));
      if (rang.length && p > pire) break; pire = p; rang = essai; reste.shift(); }
    const s = rang.reduce((a, i) => a + aire(i.v), 0), ep = s / court; let pos = 0;
    for (const i of rang){ const l = aire(i.v) / ep; out.push(w >= h ? {i, x, y: y + pos, w: ep, h: l} : {i, x: x + pos, y, w: l, h: ep}); pos += l; }
    S -= rang.reduce((a, i) => a + i.v, 0); if (w >= h){ x += ep; w -= ep; } else { y += ep; h -= ep; }
  }
  return out;
}
async function vueCarte(){
  const d = await charger("carte", "/api/carte"); if (!d) return;
  const F = d.fichiers, W = 1200, H = 600;
  const dossiers = {}; F.forEach(f => { const k = f.chemin.split("/").slice(0, -1).join("/"); (dossiers[k] = dossiers[k] || []).push({...f, v: Math.max(4, f.lignes)}); });
  const G = Object.entries(dossiers).map(([nom, fs]) => ({nom, fs, v: fs.reduce((a, f) => a + f.v, 0) + 6}));
  const lots = [...new Set(F.map(f => lotDe(f.ticket)).filter(n => !isNaN(n)))].sort();
  const teinte = f => { if (carteMode === "constats"){ const p = [0, 28, 50, 72][Math.min(3, f.constats)]; return p ? `fill:color-mix(in srgb,var(--accent) ${p}%,var(--surface));stroke:var(--accent)` : "fill:var(--surface-2);stroke:var(--trait)"; }
    const n = lotDe(f.ticket); return isNaN(n) ? "fill:var(--surface-2);stroke:var(--gris)" : `fill:color-mix(in srgb,var(--s${n % 4 + 1}) 30%,var(--surface));stroke:var(--s${n % 4 + 1})`; };
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Carte des ${F.length} fichiers de src et tests/unit. La surface d'un pavé suit le nombre de lignes.">`;
  const P = [];
  for (const g of paver(G, 0, 0, W, H)){ s += `<rect class="dossier" x="${g.x + 1}" y="${g.y + 1}" width="${Math.max(0, g.w - 2)}" height="${Math.max(0, g.h - 2)}" rx="6"/>`;
    if (g.w > 60 && g.h > 26) s += `<text class="dossier-t" x="${g.x + 7}" y="${g.y + 14}">${esc(g.i.nom.length * 6.6 > g.w - 12 ? "…" + g.i.nom.slice(-Math.floor((g.w - 20) / 6.6)) : g.i.nom)}</text>`;
    for (const r of paver(g.i.fs, g.x + 4, g.y + 19, Math.max(1, g.w - 8), Math.max(1, g.h - 23))){ const f = r.i, n = P.push(f) - 1, nom = f.chemin.split("/").pop();
      s += `<rect class="pave" data-f="${n}" x="${r.x + 1}" y="${r.y + 1}" width="${Math.max(0, r.w - 2)}" height="${Math.max(0, r.h - 2)}" rx="4" style="${teinte(f)}"/>`;
      const place = r.w - (f.constats ? 30 : 10);
      if (place > 44 && r.h > 17) s += `<text x="${r.x + 6}" y="${r.y + 14}" font-size="11.5">${esc(nom.length * 6.3 > place ? nom.slice(0, Math.max(3, Math.floor((place - 8) / 6.3))) + "…" : nom)}</text>`;
      if (r.w > 96 && r.h > 32) s += `<text x="${r.x + 6}" y="${r.y + 28}" font-size="10.5" opacity=".7">${esc(f.ticket || "kit")} · ${f.lignes} l.</text>`;
      if (f.constats && r.w > 20 && r.h > 20){ const c = f.pire === "bloquant" || f.pire === "majeur" ? "critique" : f.pire === "mineur" ? "alerte" : "gris";
        s += `<circle cx="${r.x + r.w - 12}" cy="${r.y + 12}" r="8" fill="var(--${c})"/><text class="nb" x="${r.x + r.w - 12}" y="${r.y + 15.5}" text-anchor="middle">${f.constats}</text>`; } } }
  s += `</svg>`;
  const h = `<div class="carte"><div class="carte-t">Carte du code <span class="aide">${F.length} fichiers de src/ et tests/unit/, commit ${esc(d.tete)}. La surface suit le nombre de lignes. La pastille compte les constats confirmés. Cliquez un pavé pour la fiche de son ticket.</span></div>
    <div class="filtres"><button aria-pressed="${carteMode === "lot"}" data-mode="lot">Colorer par lot</button><button aria-pressed="${carteMode === "constats"}" data-mode="constats">Colorer par nombre de constats</button></div>
    <div class="defile">${s}</div>
    <div class="legende">${carteMode === "lot" ? lots.map(n => `<span><i style="background:color-mix(in srgb,var(--s${n % 4 + 1}) 30%,var(--surface));border:1.3px solid var(--s${n % 4 + 1})"></i>lot ${n}</span>`).join("")
      : [0, 1, 2, 3].map(n => `<span><i style="background:${n ? `color-mix(in srgb,var(--accent) ${[0, 28, 50, 72][n]}%,var(--surface))` : "var(--surface-2)"};border:1.3px solid var(--trait)"></i>${n === 3 ? "3 constats ou plus" : n + " constat" + (n > 1 ? "s" : "")}</span>`).join("")}
      <span><i style="background:var(--critique);border-radius:50%"></i>constat majeur ou bloquant</span><span><i style="background:var(--alerte);border-radius:50%"></i>mineur</span><span><i style="background:var(--gris);border-radius:50%"></i>remarque</span></div></div>`;
  if (!poser("#carte", h)) return;
  $("#carte").querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click", () => { carteMode = b.dataset.mode; vueCarte(); }));
  $("#carte").querySelectorAll(".pave").forEach(n => { const f = P[+n.dataset.f];
    n.addEventListener("mousemove", e => montrer(e, `${f.chemin}\n${f.lignes} lignes · créé par ${f.ticket || "le kit"}${f.tickets.length > 1 ? "\nmodifié par " + f.tickets.slice(1).join(", ") : ""}${f.titres.length ? "\n" + f.titres.join("\n") : ""}`));
    n.addEventListener("mouseleave", cacher); n.addEventListener("click", () => { if (f.ticket) ouvrirFiche(f.ticket); }); });
}

/* ---------- journal de la garde ---------- */
let gardeGenre = null;
const GENRES = {refus:["Refus de permission","critique","refus de permission"], erreur:["Erreur d'outil","alerte","erreurs d'outil"], hors_liste:["Fichier hors du ticket","alerte","fichiers hors de la liste du ticket"], hors_depot:["Écriture hors du dépôt","critique","écritures hors du dépôt"],
  restaure:["Fichier protégé restauré","critique","fichiers protégés restaurés"], alerte:["Alerte","critique","alertes de la boucle"], compaction:["Contexte résumé","","résumés du contexte"]};
async function vueGarde(){
  const d = await charger("garde", "/api/garde"); if (!d) return;
  poser("#kpis-g", tuiles(Object.entries(GENRES).map(([k, g]) => [d.compte[k] || 0, g[2]])));
  if (poser("#filtres-g", [`<button aria-pressed="${gardeGenre === null}" data-g="">Tout</button>`].concat(Object.entries(GENRES).map(([k, [nom]]) => `<button aria-pressed="${gardeGenre === k}" data-g="${k}">${nom} · ${d.compte[k] || 0}</button>`)).join("")))
    $("#filtres-g").querySelectorAll("button").forEach(b => b.addEventListener("click", () => { gardeGenre = b.dataset.g || null; vueGarde(); }));
  const L = d.lignes.filter(l => !gardeGenre || l.genre === gardeGenre);
  poser("#garde", `<div class="carte"><div class="carte-t">Journal de la garde <span class="aide">ce que la chaîne a refusé, restauré ou signalé à Qwen, du plus récent au plus ancien</span></div>
    <div class="defile"><table class="simple"><thead><tr><th>Heure</th><th>Ticket</th><th>Nature</th><th>Détail</th><th>Motif ou message</th></tr></thead><tbody>
    ${L.map(l => `<tr><td class="mono nw">${heure(l.t)}</td><td><a href="#ticket=${esc(l.ticket)}" data-fiche="${esc(l.ticket)}" class="mono">${esc(l.ticket || "")}</a></td>
      <td><span class="mini ${GENRES[l.genre][1]}">${esc(GENRES[l.genre][0])}</span></td><td class="mono">${esc(court(l.detail))}</td><td class="aide">${esc(court(l.motif || ""))}</td></tr>`).join("") || `<tr><td colspan="5" class="vide">Rien à signaler.</td></tr>`}
    </tbody></table></div></div>`);
}

/* ---------- pilotage ---------- */
const dlg = $("#pilotage");
$("#b-pilotage").addEventListener("click", () => { $("#pil-message").textContent = ""; pilotageEtat(); dlg.showModal(); });
function pilotageEtat(){
  if (!dlg.open && dlg._vu) return; dlg._vu = true;
  const c = etat.chaine, m = etat.mesures, rv = etat.revues, reste = etat.tickets.some(t => m.vague != null && t.id.startsWith(`V${m.vague}-`) && t.etat === "à faire");
  const b = (action, texte, cls = "", extra = "") => `<button type="button" class="bouton ${cls}" data-action="${action}" ${extra}>${texte}</button>`;
  poser("#pil-boucle", `<p>${c.boucle ? `La boucle tourne${etat.en_cours ? `, sur ${esc(etat.en_cours.ticket)}` : ""}.${c.pause ? " Une pause est demandée après le ticket en cours." : ""}` : `La boucle est à l'arrêt.${reste ? ` Il reste des tickets à faire dans la vague ${m.vague}.` : m.vague != null ? ` La vague ${m.vague} n'a plus de ticket à faire.` : ""}`}</p>
    <div class="l">${c.boucle ? (c.pause ? b("boucle_pause_annuler", "Annuler la pause") : b("boucle_pause", "Pause après le ticket en cours")) + b("boucle_arreter", "Arrêter maintenant", "danger") : b("boucle_reprendre", `Reprendre la vague ${m.vague ?? ""}`, "fort", reste ? "" : "disabled")}</div>`);
  const faits = etat.tickets.filter(t => t.etat === "fait");
  poser("#pil-revues", `<p>${rv.veille ? "Les six agents sont en veille : chaque commit de ticket est relu." : "La veille est arrêtée : les commits ne sont plus relus."}</p>
    <div class="l">${rv.veille ? b("veille_arreter", "Arrêter la veille") : b("veille_demarrer", "Démarrer la veille", "fort")}
    <select id="pil-ticket" aria-label="Ticket à relire">${faits.map(t => `<option>${esc(t.id)}</option>`).join("")}</select>${b("revue_relancer", "Relire ce ticket", "", faits.length ? "" : "disabled")}</div>`);
  poser("#pil-notif", `<p>Une notification à chaque alerte, ticket fait ou bloqué, fin de vague et relecture avec réserves, même page fermée.</p>
    <div class="l">${etat.reglages.notifications ? b("notifications", "Couper les notifications", "", 'data-actif="0"') : b("notifications", "Activer les notifications", "fort", 'data-actif="1"')}</div>`);
}
async function agir(action, extra = {}){
  const d = await poster("/api/action", {action, ...extra});
  if (d.message === "ARBRE_NON_PROPRE"){ if (confirm("L'arbre de travail contient des modifications non commitées. Reprendre le ticket en les gardant ?")) return agir(action, {...extra, garder:true}); return; }
  const el = $("#pil-message"); el.textContent = d.message; el.className = d.ok ? "ok" : "ko"; dlg._vu = false; await rafraichir(); setTimeout(() => { dlg._vu = false; rafraichir(); }, 2500);
}
dlg.addEventListener("click", e => {
  const b = e.target.closest("[data-action]"); if (!b || b.disabled) return; const a = b.dataset.action;
  if (a === "boucle_arreter" && !confirm("Arrêter la boucle maintenant ? La session en cours est interrompue. Son travail reste dans l'arbre de travail.")) return;
  if (a === "revue_relancer"){ const t = $("#pil-ticket").value; if (!confirm(`Relire ${t} avec les six agents ? Comptez une à deux minutes et environ un dollar.`)) return; agir(a, {ticket: t}); return; }
  agir(a, a === "notifications" ? {actif: b.dataset.actif === "1"} : {});
});
const VUES = {couverture: vueCouverture, mesures: vueMesures, carte: vueCarte, garde: vueGarde};
{ const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith("ticket=")) ouvrirFiche(h.slice(7)); else if (ONGLETS.includes(h) && h !== "direct") document.querySelector(`[data-onglet="${h}"]`).click(); }
rafraichir(); setInterval(rafraichir, 2000);
</script>
</body>
</html>
"""


JETON = secrets.token_urlsafe(24)
PORT = 8770


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

    def hote_admis(self) -> bool:
        hote = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if hote in HOTES:
            return True
        self.envoyer(400, b"hote refuse", "text/plain; charset=utf-8")
        return False

    def do_GET(self) -> None:
        if not self.hote_admis():
            return
        url = urlsplit(self.path)
        params = parse_qs(url.query)

        def param(nom: str) -> str:
            return (params.get(nom) or [""])[0]

        routes = {
            "/api/etat": construire_etat,
            "/api/jeton": lambda: {"jeton": JETON},
            "/etat.json": construire_etat,
            "/api/revues": rapports_avec_tri,
            "/api/couverture": couverture,
            "/api/debit": mesures_debit,
            "/api/carte": carte_code,
            "/api/garde": journal_garde,
            "/api/evenements": lambda: evenements_depuis(int(param("depuis") or 0)),
            "/api/run": lambda: detail_run(param("id")),
            "/api/ticket": lambda: fiche_ticket(param("id")),
            "/api/rejeu": lambda: rejeu(param("id")),
            "/api/pensees": lambda: pensees(param("id"), int(param("depuis") or 0)),
            "/api/rapport": lambda: rapport_vague(int(param("vague")) if param("vague") else None),
        }
        if url.path == "/":
            self.envoyer(200, PAGE.replace("__JETON__", JETON).encode("utf-8"), "text/html; charset=utf-8")
        elif url.path in routes:
            try:
                valeur = routes[url.path]()
            except ValueError:
                self.json({"erreur": "paramètre illisible"}, 400)
                return
            if valeur is None:
                self.json({"erreur": "introuvable"}, 404)
            else:
                self.json(valeur)
        else:
            self.envoyer(404, b"introuvable", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        """Écritures de la page. Hôte local, origine de la page, jeton du processus et JSON exigés."""
        if not self.hote_admis():
            return
        origines = {f"http://{h}:{PORT}" for h in HOTES}
        if (
            self.headers.get("Origin") not in origines
            or not secrets.compare_digest(self.headers.get("X-Suivi-Jeton") or "", JETON)
            or not (self.headers.get("Content-Type") or "").startswith("application/json")
        ):
            self.json({"ok": False, "message": "requête refusée"}, 403)
            return
        try:
            taille = int(self.headers.get("Content-Length") or 0)
            corps = json.loads(self.rfile.read(taille)) if 0 < taille <= 65536 else {}
        except ValueError:
            corps = None
        if not isinstance(corps, dict):
            self.json({"ok": False, "message": "corps illisible"}, 400)
            return
        chemin = urlsplit(self.path).path
        if chemin == "/api/tri":
            ok, message = trier(str(corps.get("cle") or ""), str(corps.get("decision") or ""),
                                str(corps.get("note") or ""))
        elif chemin == "/api/action":
            action = str(corps.get("action") or "")
            ok, message = piloter(action, corps)
            if message != "ARBRE_NON_PROPRE":
                noter_action("pilotage", action=action, ok=ok, detail=message,
                             ticket=corps.get("ticket"))
        else:
            self.envoyer(404, b"introuvable", "text/plain; charset=utf-8")
            return
        self.json({"ok": ok, "message": message}, 200 if ok else 409)

    def log_message(self, *_args: object) -> None:
        return


def main() -> int:
    global DEPOT, PORT
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("--port", type=int, default=8770)
    analyseur.add_argument("--racine", type=Path, help="racine du dépôt, par défaut celle du script")
    args = analyseur.parse_args()
    if args.racine:
        DEPOT = Depot(args.racine.resolve())
    PORT = args.port
    serveur = ThreadingHTTPServer((ADRESSE, args.port), Gestionnaire)
    threading.Thread(target=veilleur, daemon=True).start()
    print(f"Suivi de la chaîne sur http://{ADRESSE}:{args.port}/ (dépôt {DEPOT.racine})")
    with contextlib.suppress(KeyboardInterrupt):
        serveur.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
